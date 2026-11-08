"""FastAPI app: the gateway API (/v1/*) that both frontends talk to, plus the web frontend's
static files (web/) served at / from the same container."""
from __future__ import annotations

import asyncio
import datetime as dt
import hmac
import logging
import io
import json
import platform
import time
import zipfile
import mimetypes
import os
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .arr import TIMINGS, ArrError
from .config import cfg, msg
from .logs import REQUEST_ID, get_logger, log_dir, redact, register_secret, setup_logging
from .db import Database
from .discovery import SORTS
from .sources import TitleList
from .service import SEASON_MODES, Gateway, GatewayError

log = get_logger("gateway")

MediaType = str  # "movie" | "series"
Service = str    # "radarr" | "sonarr"


class ConnectionIn(BaseModel):
    base_url: str
    api_key: str | None = None


class DefaultsIn(BaseModel):
    radarr_profile_id: int | None = None
    sonarr_profile_id: int | None = None
    radarr_root: str | None = None
    sonarr_root: str | None = None
    season_monitor: str | None = Field(None, description=" | ".join(SEASON_MODES))


class RequestIn(BaseModel):
    type: str
    id: int
    # One-off overrides; never saved as defaults.
    quality_profile_id: int | None = None
    root_folder: str | None = None
    season_monitor: str | None = Field(None, description=" | ".join(SEASON_MODES))


class Override(BaseModel):
    quality_profile_id: int | None = None
    root_folder: str | None = None
    season_monitor: str | None = None


class BatchItem(BaseModel):
    type: str
    id: int


class ClientReport(BaseModel):
    """What a frontend adds to the debug bundle: its own log and a few facts about the device."""
    log: str = ""
    info: dict = Field(default_factory=dict)
    name: str = "client"   # folder name inside the zip, e.g. "web"


class BatchIn(BaseModel):
    items: list[BatchItem]
    movie: Override | None = None      # applies to every film in this batch only
    series: Override | None = None     # applies to every show in this batch only


def env_or_file(name: str) -> str:
    """NAME, or the contents of the file named by NAME_FILE (Docker/compose secrets: /run/secrets/...)."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    path = os.environ.get(f"{name}_FILE", "").strip()
    if path:
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            log.error(msg("log.secret_file_unreadable", name=f"{name}_FILE", path=path, error=exc))
    return ""


def _load_token(data_dir: Path) -> tuple[str, str]:
    """(token, where it came from) - the source goes in the startup line, never the token."""
    token = env_or_file("ARR_GATEWAY_TOKEN")
    if token:
        return token, "env" if os.environ.get("ARR_GATEWAY_TOKEN", "").strip() else "file"
    path = data_dir / "access_token"
    if path.exists():
        return path.read_text().strip(), str(path)
    token = secrets.token_urlsafe(32)
    path.write_text(token)
    path.chmod(0o600)
    log.warning(msg("log.no_token", path=path))
    return token, f"generated:{path}"


def _new_request_id(incoming: str | None) -> str:
    """Reuse a frontend's X-Request-Id if it looks sane, so both logs carry the same id."""
    if incoming and 4 <= len(incoming) <= 64 and all(ch.isalnum() or ch in "-_" for ch in incoming):
        return incoming
    return secrets.token_hex(6)


# Set at image build time (GitHub Actions passes the tag or commit); "dev" when run from a checkout.
APP_VERSION = os.environ.get("APP_VERSION", "dev")

# The web frontend lives next to backend/ in the repo (../web) and at /srv/web in the Docker image (WEB_DIR).
REPO_WEB_DIR = Path(__file__).resolve().parents[2] / "web"
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("application/manifest+json", ".webmanifest")


class WebFiles(StaticFiles):
    """The web frontend has no build step and no hashed file names, so the browser must revalidate
    every file (cheap: StaticFiles answers 304 from ETag / Last-Modified)."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


def create_app(data_dir: str | Path | None = None, transport: httpx.AsyncBaseTransport | None = None,
               run_poller: bool | None = None, token: str | None = None,
               web_dir: str | Path | None = None) -> FastAPI:
    data_dir = Path(data_dir or os.environ.get("DATA_DIR", "/data"))
    web_dir = Path(web_dir or os.environ.get("WEB_DIR") or REPO_WEB_DIR)
    data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(data_dir / "gateway.sqlite3")
    titles = TitleList(os.environ.get("TITLES_FILE", data_dir / "titles.json"))
    gateway = Gateway(db, titles, transport)
    access_token, token_source = (token, "argument") if token else _load_token(data_dir)
    register_secret(access_token)
    dev_fake = os.environ.get("DEV_FAKE_ARR") == "1" and transport is None
    if dev_fake:
        from .devfake import FakeArr
        transport = FakeArr(simulate=True).transport()
        gateway.transport = transport
        log.warning(msg("log.dev_fake"))
        example = Path(__file__).resolve().parent.parent / "data" / "titles.example.json"
        if not titles.path.exists() and example.exists():
            titles.path = example
    if run_poller is None:
        run_poller = os.environ.get("POLLER", "1") != "0"
    gateway.auto_compat = run_poller   # background API checks only when the poller runs

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if dev_fake:
            for svc, url, key in (("radarr", "http://radarr.test", "radarr0123456789abcdef"), ("sonarr", "http://sonarr.test", "sonarr0123456789abcdef")):
                if not db.get_service(svc):
                    await gateway.save_config(svc, url, key)
        await _bootstrap_env_config(gateway)
        await gateway.load_clients()
        log.info(msg("log.startup", version=APP_VERSION, data_dir=data_dir, web=web_dir if web_served else "none",
                     token_source=token_source, radarr="connected" if "radarr" in gateway.clients else "not connected",
                     sonarr="connected" if "sonarr" in gateway.clients else "not connected",
                     poller="on" if run_poller else "off", level=logging.getLevelName(logging.getLogger().level),
                     tz=time.strftime("%Z")))
        task = asyncio.create_task(gateway.run_poller()) if run_poller else None
        gateway.poller_task = task
        yield
        if task:
            task.cancel()
        await gateway.aclose()

    web_served = (web_dir / "index.html").is_file()
    started_at = time.time()
    app = FastAPI(title="Phonarr gateway", version=APP_VERSION, lifespan=lifespan)
    app.state.gateway = gateway

    def auth(authorization: str = Header(default="")) -> None:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(value.strip(), access_token):
            raise HTTPException(401, detail={"kind": "auth", "message": msg("errors.auth")})

    # Every error answers with the same envelope both frontends parse:
    # {"error": {"kind", "message", "request_id"}}. The request id is also in every gateway log line.
    def error_response(status: int, error: dict) -> JSONResponse:
        return JSONResponse({"error": {**error, "request_id": REQUEST_ID.get()}}, status_code=status)

    @app.exception_handler(GatewayError)
    async def _gw_error(_: Request, exc: GatewayError):
        return error_response(exc.status, {"kind": exc.kind, "message": exc.message})

    @app.exception_handler(ArrError)
    async def _arr_error(_: Request, exc: ArrError):
        status = 503 if exc.kind == "unavailable" else 422 if exc.kind == "config" else 502
        return error_response(status, exc.as_dict())

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        if isinstance(exc.detail, dict) and "message" in exc.detail:
            error = exc.detail
        else:
            error = {"kind": "not_found" if exc.status_code == 404 else "request",
                     "message": msg("errors.not_found_path") if exc.status_code == 404 else str(exc.detail)}
        return error_response(exc.status_code, error)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        details = "; ".join(f"{'.'.join(str(p) for p in e.get('loc', ()) if p != 'body')}: {e.get('msg')}"
                            for e in exc.errors())
        return error_response(422, {"kind": "request", "message": msg("errors.validation", details=details)})

    quiet_paths = set(cfg("logging.quiet_paths"))
    slow_ms = cfg("logging.slow_request_ms")

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        """Request id, one log line per API call, and a JSON 500 (logged with its traceback) for any
        unexpected error, instead of Starlette's plain-text "Internal Server Error"."""
        rid = _new_request_id(request.headers.get("x-request-id"))
        reset = REQUEST_ID.set(rid)
        started = time.perf_counter()
        path = request.url.path
        try:
            try:
                response = await call_next(request)
            except Exception:
                log.exception(msg("log.unhandled", method=request.method, path=path, request_id=rid))
                response = error_response(500, {"kind": "internal", "message": msg("errors.internal", request_id=rid)})
            ms = round((time.perf_counter() - started) * 1000, 1)
            if path.startswith("/v1/") and response.status_code != 304:
                level = (logging.WARNING if response.status_code >= 400 or ms >= slow_ms
                         else logging.DEBUG if path in quiet_paths else logging.INFO)
                log.log(level, msg("log.http", method=request.method, path=path, status=response.status_code, ms=ms))
            response.headers["X-Request-Id"] = rid
            return response
        finally:
            REQUEST_ID.reset(reset)

    def check_service(service: str) -> str:
        if service not in ("radarr", "sonarr"):
            raise GatewayError(404, "not_found", msg("errors.unknown_service"))
        return service

    def check_type(media_type: str) -> str:
        if media_type not in ("movie", "series"):
            raise GatewayError(404, "not_found", msg("errors.bad_type"))
        return media_type

    # ---------------------------------------------------------------- routes
    @app.get("/v1/health")
    async def health():
        """Is it up? No auth, so Docker's healthcheck, Uptime Kuma or a browser can call it.
        `ok` means the gateway process is serving (Docker uses this). `status` adds Radarr/Sonarr:
        "ok" (every connected server answered), "degraded" (one is unreachable) or "setup" (none connected).
        No URLs, keys or error text here; those need the token (GET /v1/config)."""
        now = time.time()
        services = {}
        for svc in ("radarr", "sonarr"):
            saved = db.get_service(svc)
            known = gateway.service_health(svc)
            services[svc] = {"configured": saved is not None,
                             "reachable": known.get("reachable") if saved is not None else None,
                             "version": saved["version"] if saved else None,
                             "checked_s_ago": round(now - known["checked_at"], 1) if saved and known.get("checked_at") else None,
                             "down_for_s": round(now - known["down_since"]) if saved and known.get("down_since") else None}
        connected = [v for v in services.values() if v["configured"]]
        poller = gateway.poller_state()
        status = ("degraded" if poller == "stopped" or any(v["reachable"] is False for v in connected)
                  else "setup" if not connected else "ok")
        snap_at = gateway.snapshot["generated_at"]
        return {"ok": True, "status": status, "version": APP_VERSION,
                "uptime_s": round(now - started_at), "web_frontend": web_served,
                "services": services, "poller": poller,
                "last_poll_error_s_ago": round(now - gateway.last_poll_error_at) if gateway.last_poll_error_at else None,
                "last_status_refresh_s_ago": round(now - snap_at, 1) if snap_at else None,
                "polls": gateway.polls, "poll_interval": gateway.poll_interval(),
                "idle_health_check_s": cfg("gateway.poll.idle_health_check") or None,
                "recent_arr_calls": list(TIMINGS)[-30:]}

    @app.get("/v1/config", dependencies=[Depends(auth)])
    async def get_config():
        return gateway.public_config()

    @app.post("/v1/config/{service}/test", dependencies=[Depends(auth)])
    async def test_config(service: str, body: ConnectionIn):
        return await gateway.test_connection(check_service(service), body.base_url, body.api_key)

    @app.put("/v1/config/{service}", dependencies=[Depends(auth)])
    async def put_config(service: str, body: ConnectionIn):
        result = await gateway.save_config(check_service(service), body.base_url, body.api_key)
        return {**result, "config": gateway.public_config()[service]}

    @app.delete("/v1/config/{service}", dependencies=[Depends(auth)])
    async def delete_config(service: str):
        await gateway.remove_config(check_service(service))
        return gateway.public_config()

    @app.get("/v1/compat", dependencies=[Depends(auth)])
    async def compat():
        """Last API compatibility report per service (None if never run / not configured)."""
        return gateway.compat_reports()

    @app.post("/v1/compat/check", dependencies=[Depends(auth)])
    async def compat_check(service: str | None = None):
        if service:
            check_service(service)
        return await gateway.run_compat(service)

    def build_bundle(client: ClientReport | None = None) -> bytes:
        """Zip of redacted gateway logs and state, plus the calling frontend's own log if it sent one."""
        buf = io.BytesIO()
        info = {
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "gateway_version": app.version,
            "python": platform.python_version(),
            "dev_fake_arr": dev_fake,
            "web_frontend": str(web_dir) if web_served else None,
            "config": gateway.public_config(),
            "defaults": gateway.defaults(),
            "polling": {"interval": gateway.poll_interval(), "polls": gateway.polls,
                        "last_client_seen_s_ago": round(time.time() - gateway.last_seen, 1) if gateway.last_seen else None,
                        "snapshot_age_s": round(time.time() - gateway.snapshot["generated_at"], 1)
                        if gateway.snapshot["generated_at"] else None},
            "services": gateway.snapshot["services"],
            "titles_list": gateway.titles.counts(),
        }
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("gateway/info.json", redact(json.dumps(info, indent=2, default=str)))
            z.writestr("gateway/status.json", redact(json.dumps(gateway.snapshot, indent=2, default=str)))
            z.writestr("gateway/requests.json", redact(json.dumps(db.list_requests(), indent=2, default=str)))
            z.writestr("gateway/arr_call_timings.json", json.dumps(list(TIMINGS), indent=2))
            z.writestr("gateway/api_compat.json", redact(json.dumps(gateway.compat_reports(), indent=2, default=str)))
            for f in sorted(log_dir(data_dir).glob("*.log*")):
                z.writestr(f"gateway/logs/{f.name}", redact(f.read_text(encoding="utf-8", errors="replace")))
            if client:
                folder = "".join(ch for ch in client.name if ch.isalnum() or ch in "-_")[:32] or "client"
                z.writestr(f"{folder}/app.log", redact(client.log[-2_000_000:]))
                z.writestr(f"{folder}/device.json", redact(json.dumps(client.info, indent=2, default=str)))
        return buf.getvalue()

    def zip_response(data: bytes) -> Response:
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="gateway-debug.zip"'})

    @app.get("/v1/debug/bundle", dependencies=[Depends(auth)])
    async def debug_bundle():
        """Gateway logs and state only (the iPhone app zips this together with its own log)."""
        return zip_response(build_bundle())

    @app.post("/v1/debug/bundle", dependencies=[Depends(auth)])
    async def debug_bundle_with_client(body: ClientReport):
        """Same zip, with the caller's log added (the web frontend can't build zips itself)."""
        return zip_response(build_bundle(body))

    @app.get("/v1/options", dependencies=[Depends(auth)])
    async def options():
        return await gateway.all_options()

    @app.put("/v1/defaults", dependencies=[Depends(auth)])
    async def put_defaults(body: DefaultsIn):
        return gateway.save_defaults(body.model_dump(exclude_unset=True))

    @app.get("/v1/discover", dependencies=[Depends(auth)])
    async def discover(type: str = Query("movie"), q: str | None = None, genre: str | None = None,
                       genres: str | None = None, decade: int | None = None, year_min: int | None = None,
                       year_max: int | None = None, runtime: str | None = None, rating_min: float | None = None,
                       language: str | None = None, studio: str | None = None, collection: str | None = None,
                       certification: str | None = None, status: str | None = None, source: str | None = None,
                       sort: str | None = None, include_tracked: bool = False):
        check_type(type)
        if sort and sort not in SORTS:
            raise GatewayError(422, "request", msg("errors.bad_sort", sorts=", ".join(SORTS)))
        await gateway.ensure_fresh()
        filters = {"q": q, "genres": genres or genre, "decade": decade, "year_min": year_min, "year_max": year_max,
                   "runtime": runtime, "rating_min": rating_min, "language": language, "studio": studio,
                   "collection": collection, "certification": certification, "status": status,
                   "source": source}
        return await gateway.discover(type, filters, include_tracked, sort)

    @app.get("/v1/titles/{media_type}/{external_id}/similar", dependencies=[Depends(auth)])
    async def similar(media_type: str, external_id: int, mix: bool = True, limit: int = Query(20, ge=1, le=60),
                      include_tracked: bool = False):
        return await gateway.similar(check_type(media_type), external_id, mix, limit, include_tracked)

    @app.get("/v1/titles/{media_type}/{external_id}", dependencies=[Depends(auth)])
    async def title(media_type: str, external_id: int):
        return await gateway.title_detail(check_type(media_type), external_id)

    @app.post("/v1/requests", dependencies=[Depends(auth)])
    async def create_request(body: RequestIn, idempotency_key: str | None = Header(default=None)):
        check_type(body.type)
        return await gateway.request(body.type, body.id, body.quality_profile_id, body.root_folder, idempotency_key,
                                     body.season_monitor)

    @app.post("/v1/requests/batch", dependencies=[Depends(auth)])
    async def create_batch(body: BatchIn):
        """Add several titles in one go (multi-select). Per-item results; never a duplicate Arr record."""
        for it in body.items:
            check_type(it.type)
        overrides = {k: v.model_dump(exclude_none=True) for k, v in (("movie", body.movie), ("series", body.series)) if v}
        return await gateway.request_batch([it.model_dump() for it in body.items], overrides)

    @app.get("/v1/request-plan", dependencies=[Depends(auth)])
    async def request_plan(type: str = Query("movie")):
        """What a one-tap request uses right now (profile name, root folder, season mode)."""
        return await gateway.request_plan(check_type(type))

    @app.post("/v1/requests/{media_type}/{external_id}/retry", dependencies=[Depends(auth)])
    async def retry(media_type: str, external_id: int):
        return await gateway.retry(check_type(media_type), external_id)

    @app.get("/v1/status", dependencies=[Depends(auth)])
    async def status(response: Response, viewing: str | None = None,
                     if_none_match: str | None = Header(default=None)):
        gateway.note_client(viewing)
        await gateway.ensure_fresh()
        interval = gateway.poll_interval() or 60
        headers = {"ETag": gateway.etag, "X-Poll-After": str(int(interval))}
        if if_none_match and if_none_match == gateway.etag:
            return Response(status_code=304, headers=headers)
        response.headers.update(headers)
        return {**gateway.snapshot, "next_poll_seconds": interval}

    @app.get("/v1/activity", dependencies=[Depends(auth)])
    async def activity(viewing: str | None = "activity"):
        gateway.note_client(viewing)
        await gateway.ensure_fresh()
        return {"items": gateway.activity(), "services": gateway.snapshot["services"],
                "next_poll_seconds": gateway.poll_interval() or 60}

    # Web frontend. Mounted last so every /v1 route and /docs wins over a static file.
    if web_served:
        app.mount("/", WebFiles(directory=web_dir, html=True), name="web")
    else:
        log.info(msg("log.no_web", path=web_dir))

    return app


async def _bootstrap_env_config(gateway: Gateway) -> None:
    """Optionally seed connections from env vars (RADARR_URL / RADARR_API_KEY ...) on first start."""
    for service in ("radarr", "sonarr"):
        url = os.environ.get(f"{service.upper()}_URL")
        key = env_or_file(f"{service.upper()}_API_KEY")
        saved = gateway.db.get_service(service)
        if url and saved and saved["base_url"].rstrip("/") != url.strip().rstrip("/"):
            log.warning(msg("log.env_ignored", service=service.capitalize(), env=f"{service.upper()}_URL"))
        if url and key and not saved:
            try:
                await gateway.save_config(service, url, key)
                log.info(msg("log.env_configured", service=service))
            except (ArrError, GatewayError) as e:
                log.error(msg("log.env_failed", service=service, error=getattr(e, "message", e)))


def check_data_dir(data_dir: Path) -> str | None:
    """None if the data folder can be written, else one clear sentence saying how to fix it.
    The usual cause: Docker created the bind-mounted folder as root and the gateway runs as uid 1000."""
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        return None
    except OSError as exc:
        uid = os.getuid() if hasattr(os, "getuid") else "?"
        gid = os.getgid() if hasattr(os, "getgid") else "?"
        return msg("errors.data_not_writable", path=data_dir, uid=uid, gid=gid, error=exc)


def _app_factory() -> FastAPI:
    data_dir = Path(os.environ.get("DATA_DIR", "/data"))
    problem = check_data_dir(data_dir)
    if problem:
        # Logging can't write its files yet, so say it plainly on stderr (docker logs) and stop.
        print(problem, file=sys.stderr, flush=True)   # noqa: T201  the one place a logger can't be used
        raise SystemExit(1)
    setup_logging(data_dir)
    return create_app()
