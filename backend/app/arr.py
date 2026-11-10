"""Thin async client for the Radarr / Sonarr v3 API with actionable errors."""
from __future__ import annotations

import logging
import ssl
import time
from collections import deque
from typing import Any
from urllib.parse import urlsplit

import os

import httpx

from .config import cfg, msg
from .logs import get_logger, redact, register_secret

log = get_logger("arr")

EXPECTED_APP = cfg("arr.names")

# Recent call timings, exposed on /v1/health so polling intervals can be tuned
# against what the real servers do.
TIMINGS: deque[dict] = deque(maxlen=cfg("arr.timings_kept"))


class ArrError(Exception):
    """kind: 'config' needs the user to fix settings; 'unavailable' is transient."""

    def __init__(self, service: str, kind: str, message: str, status: int | None = None):
        super().__init__(message)
        self.service = service
        self.kind = kind
        self.message = message
        self.status = status

    def as_dict(self) -> dict:
        return {"service": self.service, "kind": self.kind, "message": self.message}


def normalize_base_url(raw: str) -> str:
    url = (raw or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "http://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(msg("arr.bad_url"))
    if parts.query or parts.fragment:
        raise ValueError(msg("arr.url_query"))
    return url


def _describe_connect_error(service: str, url: str, exc: Exception) -> ArrError:
    parts = urlsplit(url)
    host = parts.hostname or url
    if parts.port:
        host = f"{host}:{parts.port}"
    text = str(exc).lower()
    cause = exc.__cause__ or exc.__context__
    if isinstance(cause, ssl.SSLError) or "ssl" in text or "certificate" in text:
        return ArrError(service, "config",
                        msg("arr.tls", host=host, exc=exc))
    if "name or service not known" in text or "nodename nor servname" in text or "getaddrinfo" in text \
            or "temporary failure in name resolution" in text or "no address associated" in text:
        hostname = parts.hostname or ""
        # A bare name like "radarr" is a Docker service name: it only resolves on a shared Docker network.
        key = ("arr.dns_hint_tailscale" if hostname.endswith(".ts.net")
               else "arr.dns_hint_docker" if hostname and "." not in hostname and hostname != "localhost"
               else "arr.dns_hint_default")
        hint = msg(key, host_only=hostname) if key == "arr.dns_hint_docker" else msg(key)
        return ArrError(service, "config", msg("arr.dns", host=host, hint=hint))
    if "refused" in text or "all connection attempts failed" in text or "unreachable" in text:
        if parts.hostname in ("localhost", "127.0.0.1", "::1"):
            # Inside a container, localhost is the gateway's own container, never Radarr's.
            return ArrError(service, "config", msg("arr.refused_localhost", host=host, name=EXPECTED_APP[service], name_lower=service, port=cfg("arr.default_ports")[service]))
        return ArrError(service, "unavailable",
                        msg("arr.refused", host=host, name=EXPECTED_APP[service]))
    return ArrError(service, "unavailable", msg("arr.connect", host=host, exc=exc))


class ArrClient:
    def __init__(self, service: str, base_url: str, api_key: str,
                 transport: httpx.AsyncBaseTransport | None = None, timeout: float | None = None):
        self.service = service
        self.base_url = normalize_base_url(base_url)
        register_secret(api_key)
        # ARR_VERIFY_TLS=0 allows self-signed certificates on private networks.
        verify = os.environ.get("ARR_VERIFY_TLS", "1") != "0"
        self._client = httpx.AsyncClient(
            verify=verify,
            base_url=self.base_url,
            headers={"X-Api-Key": api_key, "Accept": "application/json"},
            timeout=httpx.Timeout(timeout or cfg("arr.timeout"), connect=cfg("arr.connect_timeout")),
            transport=transport,
            follow_redirects=False,
        )
        self.version: str | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, path: str, **kw: Any) -> Any:
        started = time.perf_counter()
        status = None
        failure: ArrError | None = None
        try:
            resp = await self._client.request(method, path, **kw)
            status = resp.status_code
        except httpx.TimeoutException:
            failure = ArrError(self.service, "unavailable",
                               msg("arr.timeout", name=EXPECTED_APP[self.service], url=self.base_url))
        except httpx.HTTPError as exc:
            failure = _describe_connect_error(self.service, self.base_url, exc)
        ms = round((time.perf_counter() - started) * 1000, 1)
        TIMINGS.append({"service": self.service, "method": method, "path": path, "status": status,
                        "ms": ms, "at": time.time()})
        if failure is not None:
            # The reason (DNS, refused, timeout, TLS) goes in the log, not just "status None".
            log.warning(msg("log.arr_call_failed", service=self.service, method=method, path=path, ms=ms,
                            error=failure.message))
            raise failure
        # Routine calls at DEBUG so polling doesn't bury problems; errors and slow calls stand out.
        level = (logging.WARNING if status >= 400 or ms >= cfg("arr.slow_ms") else logging.DEBUG)
        log.log(level, msg("log.arr_call", service=self.service, method=method, path=path, status=status, ms=ms))

        name = EXPECTED_APP[self.service]
        if resp.status_code == 401:
            raise ArrError(self.service, "config", msg("arr.rejected_key", name=name), 401)
        if resp.status_code in (301, 302, 307, 308):
            raise ArrError(self.service, "config",
                           msg("arr.redirect", name=name, location=resp.headers.get("location")), resp.status_code)
        if resp.status_code == 404 and path.endswith("system/status"):
            raise ArrError(self.service, "config",
                           msg("arr.no_api", name=name, url=self.base_url, service=self.service), 404)
        if resp.status_code >= 500:
            # Keep the app's own explanation (e.g. "Invalid response received from RadarrAPI"), not just the code.
            detail = _error_text(resp)
            raise ArrError(self.service, "unavailable",
                           msg("arr.http_error_detail", name=name, status=resp.status_code, detail=detail) if detail
                           else msg("arr.http_error", name=name, status=resp.status_code), resp.status_code)
        if resp.status_code >= 400:
            raise ArrError(self.service, "request", _error_text(resp) or msg("arr.http_error", name=name, status=resp.status_code),
                           resp.status_code)
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            # Name the call, the status and the start of the body (redacted), so a debug report is enough.
            log.warning(msg("log.arr_non_json", service=self.service, method=method, path=path, status=status,
                            type=resp.headers.get("content-type") or "-",
                            snippet=redact(resp.text[:cfg("arr.log_body_chars")]).replace("\n", " ")))
            raise ArrError(self.service, "config",
                           msg("arr.non_json", url=self.base_url, name=name)) from None

    async def probe(self, method: str, path: str, params: dict | None = None, body: Any = None) -> tuple[int | None, Any, str | None]:
        """Low-level call for the compatibility check: never raises for HTTP errors.
        Returns (status, parsed_json_or_None, transport_error)."""
        started = time.perf_counter()
        try:
            resp = await self._client.request(method, path, params=params, json=body)
        except httpx.HTTPError as exc:
            return None, None, _describe_connect_error(self.service, self.base_url, exc).message
        finally:
            TIMINGS.append({"service": self.service, "method": method, "path": path, "status": None,
                            "ms": round((time.perf_counter() - started) * 1000, 1), "at": time.time(), "probe": True})
        try:
            data = resp.json() if resp.content else None
        except ValueError:
            data = None
        return resp.status_code, data, None

    async def get(self, path: str, **params: Any) -> Any:
        return await self.request("GET", path, params=params or None)

    async def post(self, path: str, body: Any) -> Any:
        return await self.request("POST", path, json=body)

    async def delete(self, path: str, **params: Any) -> Any:
        return await self.request("DELETE", path, params=params or None)

    async def system_status(self) -> dict:
        data = await self.get("/api/v3/system/status")
        expected = EXPECTED_APP[self.service]
        app_name = (data or {}).get("appName") or (data or {}).get("instanceName")
        if not isinstance(data, dict) or app_name != expected:
            raise ArrError(self.service, "config",
                           msg("arr.wrong_app", found=app_name or "not an Arr app", expected=expected))
        self.version = data.get("version")
        return data

    @property
    def major_version(self) -> int:
        try:
            return int((self.version or "0").split(".")[0])
        except ValueError:
            return 0


def _error_text(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:300]
    if isinstance(data, list):
        return "; ".join(str(e.get("errorMessage") or e) for e in data)[:300]
    if isinstance(data, dict):
        return str(data.get("message") or data.get("errorMessage") or data)[:300]
    return str(data)[:300]
