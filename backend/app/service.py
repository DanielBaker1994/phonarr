"""Core gateway logic: config, discovery, requests, status aggregation."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any

import httpx

from . import calendar as calendar_feed
from . import discovery
from .compat import CompatChecker
from .arr import ArrClient, ArrError, normalize_base_url
from .config import cfg, msg
from .db import Database
from .logs import get_logger, traced
from .sources import TitleList

log = get_logger("gateway")

SERVICE_FOR = {"movie": "radarr", "series": "sonarr"}
# Tunables live in config/settings.json ("gateway"); names kept so tests and other modules import them.
LIBRARY_TTL = cfg("gateway.ttl.library")      # seconds; library lists are refreshed after requests anyway
OPTIONS_TTL = cfg("gateway.ttl.options")
METADATA_TTL = cfg("gateway.ttl.metadata")
POOL_TTL = cfg("gateway.ttl.pool")            # Radarr Discover (import lists + recommendations)
SEARCH_TTL = cfg("gateway.ttl.search")
BATCH_MAX = cfg("gateway.batch_max")
SOURCE_ORDER = tuple(cfg("gateway.source_order"))
RETRY_COOLDOWN = cfg("gateway.retry_cooldown")  # seconds; repeated Retry taps don't trigger repeated searches
COMPAT_EVERY = cfg("gateway.compat_every")      # re-run the API compatibility check daily
SEASON_MODES = tuple(cfg("gateway.season_modes"))

# Adaptive polling intervals (seconds). None = stop polling.
POLL_WATCHING = cfg("gateway.poll.watching")          # phone is on the Activity / details screen and something is moving
POLL_FOREGROUND = cfg("gateway.poll.foreground")       # phone is open, something is moving
POLL_IDLE_ACTIVE = cfg("gateway.poll.idle_active")      # phone backgrounded/closed, something is moving
POLL_FOREGROUND_QUIET = cfg("gateway.poll.foreground_quiet") # phone open, nothing moving (picks up changes made elsewhere)
CLIENT_GONE_AFTER = cfg("gateway.poll.client_gone_after")    # no phone check-in for this long = backgrounded
WATCHING_WINDOW = cfg("gateway.poll.watching_window")
# With nobody watching and nothing downloading, the poller doesn't stop completely: it makes one cheap
# system/status call per server this often, so /v1/health (and Uptime Kuma) never reports a stale "ok".
# 0 turns it off (the old behaviour).
IDLE_HEALTH_CHECK = cfg("gateway.poll.idle_health_check")
DOWN_REMINDER_EVERY = cfg("gateway.down_reminder_every")  # repeat the "still down" log line this often
POLLER_ERROR_BACKOFF = cfg("gateway.poller_error_backoff")
# A requested title missing from this many successful library reads in a row is "No longer in Radarr";
# fewer is treated as a bad answer (Radarr restarting, database locked) and the last status is kept.
MISSING_CONFIRMATIONS = cfg("gateway.missing_confirmations")

ACTIVE_STATES = {"queued", "downloading", "importing", "paused", "stalled"}


class GatewayError(Exception):
    def __init__(self, status: int, kind: str, message: str):
        super().__init__(message)
        self.status, self.kind, self.message = status, kind, message


def _image(images: list[dict] | None, cover: str) -> str | None:
    for img in images or []:
        if img.get("coverType") == cover:
            url = img.get("remoteUrl") or img.get("url")
            if url and url.startswith("http"):
                return url
    return None


def _movie_ratings(m: dict) -> dict[str, dict]:
    """Radarr v4+/v5: {imdb:{value,votes}, tmdb:{...}, metacritic:{...}, rottenTomatoes:{...}, trakt:{...}};
    Radarr v3: a single {value, votes}, treated as TMDB. Returns {source: {value, votes}} for sources with a value."""
    r = m.get("ratings") or {}
    if "value" in r:
        return {"tmdb": {"value": r["value"], "votes": r.get("votes") or 0}} if r.get("value") else {}
    out = {}
    for src in ("imdb", "tmdb", "trakt", "rottenTomatoes", "metacritic"):
        v = (r.get(src) or {}).get("value")
        if v:
            out[src] = {"value": v, "votes": (r.get(src) or {}).get("votes") or 0}
    return out


def _rating_fields(sources: dict[str, dict], media_type: str) -> dict:
    """rating: the trusted rating (discovery.trusted_rating); ratings: {source: value}; votes: {source: votes}."""
    return {"rating": discovery.trusted_rating(sources, media_type),
            "ratings": {k: v["value"] for k, v in sources.items()},
            "votes": {k: v["votes"] for k, v in sources.items() if v.get("votes")}}


def _lang(x: dict) -> str | None:
    lang = x.get("originalLanguage")
    return lang.get("name") if isinstance(lang, dict) else None


def _keywords(x: dict) -> list[str]:
    kw = x.get("keywords") or []
    return [k if isinstance(k, str) else str(k.get("name", "")) for k in kw if k][:30]


def normalize_movie(m: dict) -> dict:
    collection = m.get("collection") or {}
    return {
        "decade": (m["year"] // 10) * 10 if m.get("year") else None,
        **_rating_fields(_movie_ratings(m), "movie"),
        "popularity": m.get("popularity"),
        "language": _lang(m),
        "collection": collection.get("title") or collection.get("name"),
        "keywords": _keywords(m),
        "series_status": None,
        "release_status": m.get("status"),
        "youtube_trailer_id": m.get("youTubeTrailerId"),
        "type": "movie", "id": m.get("tmdbId"), "title": m.get("title"), "original_title": m.get("originalTitle"),
        "year": m.get("year"), "overview": m.get("overview"), "genres": m.get("genres") or [],
        "runtime": m.get("runtime"), "certification": m.get("certification"), "studio": m.get("studio"),
        "imdb_id": m.get("imdbId"), "poster": _image(m.get("images"), "poster"),
        "fanart": _image(m.get("images"), "fanart"),
    }


def normalize_series(s: dict) -> dict:
    r = s.get("ratings") or {}
    return {
        "decade": (s["year"] // 10) * 10 if s.get("year") else None,
        **_rating_fields({"tvdb": {"value": r["value"], "votes": r.get("votes") or 0}} if r.get("value") else {},
                         "series"),
        "popularity": None,
        "language": _lang(s),
        "collection": None,
        "keywords": [],
        "series_type": s.get("seriesType"),
        "type": "series", "id": s.get("tvdbId"), "title": s.get("title"), "original_title": None,
        "year": s.get("year"), "overview": s.get("overview"), "genres": s.get("genres") or [],
        "runtime": s.get("runtime"), "certification": s.get("certification"), "studio": s.get("network"),
        "imdb_id": s.get("imdbId"), "poster": _image(s.get("images"), "poster"),
        "fanart": _image(s.get("images"), "fanart"),
        "season_count": len([x for x in s.get("seasons") or [] if x.get("seasonNumber", 0) > 0]),
        "series_status": s.get("status"),
    }


def _queue_message(item: dict) -> str | None:
    if item.get("errorMessage"):
        return item["errorMessage"]
    msgs = []
    for sm in item.get("statusMessages") or []:
        msgs.extend(sm.get("messages") or [])
        if not sm.get("messages") and sm.get("title"):
            msgs.append(sm["title"])
    return "; ".join(msgs)[:300] or None


def classify_queue_item(item: dict) -> dict:
    """Map an Arr queue record to (state, progress, message)."""
    status = (item.get("status") or "").lower()
    tds = (item.get("trackedDownloadStatus") or "").lower()
    tstate = (item.get("trackedDownloadState") or "").lower()
    size = float(item.get("size") or 0)
    left = float(item.get("sizeleft") or 0)
    progress = round(max(0.0, min(1.0, (size - left) / size)), 4) if size > 0 else None
    msg = _queue_message(item)

    if tstate in ("failed", "failedpending") or status == "failed" or tds == "error":
        state = "failed"
    elif tstate == "importpending" and tds == "warning":
        state, msg = "failed", "Import needs attention: " + (msg or "Arr could not import the download.")
    elif tstate in ("importpending", "importing", "imported") or status == "completed":
        state, progress = "importing", 1.0
    elif status == "paused":
        state = "paused"
    elif status in ("queued", "delay"):
        state = "queued"
    elif status in ("warning", "downloadclientunavailable") or tds == "warning":
        state = "stalled"
    elif status == "downloading":
        state = "downloading"
    else:
        state = "queued"
    return {"state": state, "progress": progress, "message": msg, "size": size, "sizeleft": left}


STATE_PRIORITY = ["failed", "downloading", "importing", "stalled", "paused", "queued"]


class Gateway:
    # Durations, TTLs and cooldowns use time.monotonic(), which never jumps when the wall clock is corrected
    # (VM resume, NTP). time.time() is only for timestamps that are shown or stored (KB-10).
    def __init__(self, db: Database, titles: TitleList, transport: httpx.AsyncBaseTransport | None = None):
        self.db = db
        self.titles = titles
        self._discover_cache: tuple[float, list[dict]] | None = None
        self._search_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}
        self.transport = transport
        self.clients: dict[str, ArrClient] = {}
        self._library: dict[str, tuple[float, list[dict]]] = {}
        self._options: dict[str, tuple[float, dict]] = {}
        self._queue: dict[str, list[dict]] = {"radarr": [], "sonarr": []}
        self._service_health: dict[str, dict] = {}
        self.snapshot: dict = {"items": {}, "services": {}, "generated_at": 0}
        self.etag = ""
        self._title_locks: dict[tuple[str, int], asyncio.Lock] = {}
        self._refresh_lock = asyncio.Lock()
        self.wake = asyncio.Event()
        self.last_seen: float | None = None       # monotonic
        self.last_watching: float | None = None   # monotonic
        self.snapshot_at: float | None = None     # monotonic time of the last snapshot
        self._missing: dict[str, int] = {}        # "movie:603" -> library reads in a row without it
        self.polls = 0
        self._last_retry: dict[tuple[str, int], float] = {}
        self._compat_task: asyncio.Task | None = None
        self.auto_compat = True
        self.poller_task: asyncio.Task | None = None
        self.last_poll_error_at: float | None = None
        self.calendar_cache = calendar_feed.CalendarCache()   # GET /v1/calendar only; never polled

    # ------------------------------------------------------------------ config
    async def load_clients(self) -> None:
        for service in ("radarr", "sonarr"):
            cfg = self.db.get_service(service)
            if cfg:
                await self._set_client(service, ArrClient(service, cfg["base_url"], cfg["api_key"], self.transport))
                self.clients[service].version = cfg.get("version")

    async def _set_client(self, service: str, client: ArrClient) -> None:
        old = self.clients.get(service)
        self.clients[service] = client
        self._library.pop(service, None)
        self._options.pop(service, None)
        self._search_cache.clear()
        self.calendar_cache.clear(service)
        if service == "radarr":
            self._discover_cache = None
        if old:
            await old.aclose()

    def client(self, service: str) -> ArrClient:
        c = self.clients.get(service)
        if c is None:
            name = service.capitalize()
            raise ArrError(service, "config", msg("arr.not_configured", name=name))
        return c

    @traced
    async def test_connection(self, service: str, base_url: str, api_key: str | None) -> dict:
        return (await self._test(service, base_url, api_key))[0]

    async def _test(self, service: str, base_url: str, api_key: str | None) -> tuple[dict, str]:
        try:
            url = normalize_base_url(base_url)
        except ValueError as exc:
            raise GatewayError(422, "config", str(exc))
        if not api_key:
            saved = self.db.get_service(service)
            if not saved:
                raise GatewayError(422, "config", msg("errors.enter_api_key"))
            api_key = saved["api_key"]
        client = ArrClient(service, url, api_key, self.transport)
        try:
            status = await client.system_status()
            profiles = await client.get("/api/v3/qualityprofile")
        finally:
            await client.aclose()
        return {"ok": True, "base_url": url, "version": status.get("version"),
                "app_name": status.get("appName"), "quality_profiles": len(profiles or [])}, api_key

    @traced
    async def save_config(self, service: str, base_url: str, api_key: str | None) -> dict:
        result, key = await self._test(service, base_url, api_key)
        self.db.save_service(service, result["base_url"], key, result["version"])
        client = ArrClient(service, result["base_url"], key, self.transport)
        client.version = result["version"]
        await self._set_client(service, client)
        self._note_health(service, True, None)   # it just answered the test, so health says so straight away
        self.wake.set()
        self.db.set_setting(f"compat:{service}", None)   # new server: start a fresh history
        self.schedule_compat(service)
        return result

    def _compat_summary(self, service: str) -> dict | None:
        r = self.db.get_setting(f"compat:{service}")
        if not r or service not in self.clients:
            return None
        return {"status": r["status"], "checked_at": r["checked_at"], "version": r["version"],
                "changes": len(r.get("changes") or [])}

    @traced
    async def remove_config(self, service: str) -> None:
        self.db.delete_service(service)
        self._service_health.pop(service, None)
        client = self.clients.pop(service, None)
        self._library.pop(service, None)
        self.calendar_cache.clear(service)
        self._options.pop(service, None)
        self._queue[service] = []
        if client:
            await client.aclose()
        await self.refresh_status()

    # ------------------------------------------------------- API compatibility
    @traced
    async def run_compat(self, service: str | None = None) -> dict:
        checker = CompatChecker(self.db, self.transport)
        out = {}
        for svc in ([service] if service else ["radarr", "sonarr"]):
            if svc in self.clients:
                out[svc] = await checker.run(svc, self.clients[svc])
                if self.clients[svc].version:
                    cfg = self.db.get_service(svc)
                    if cfg and cfg.get("version") != self.clients[svc].version:
                        self.db.save_service(svc, cfg["base_url"], cfg["api_key"], self.clients[svc].version)
            else:
                out[svc] = None
        self.db.set_setting("compat_last_run", time.time())
        return out

    def compat_reports(self) -> dict:
        return {svc: (self.db.get_setting(f"compat:{svc}") if svc in self.clients else None)
                for svc in ("radarr", "sonarr")}

    def schedule_compat(self, service: str | None = None) -> None:
        """Run the check in the background (startup, daily, after a connection is saved)."""
        if not self.auto_compat:
            return
        if self._compat_task and not self._compat_task.done():
            return

        async def _run():
            try:
                await self.run_compat(service)
            except Exception:
                log.exception(msg("log.compat_failed"))

        self._compat_task = asyncio.create_task(_run())

    def public_config(self) -> dict:
        out = {}
        for service in ("radarr", "sonarr"):
            cfg = self.db.get_service(service)
            health = self._service_health.get(service, {})
            out[service] = {
                "configured": cfg is not None,
                "base_url": cfg["base_url"] if cfg else None,
                "api_key_set": bool(cfg and cfg["api_key"]),
                "version": cfg["version"] if cfg else None,
                "reachable": health.get("reachable"),
                "error": health.get("error"),
                "api_check": self._compat_summary(service),
            }
        return out

    # ----------------------------------------------------------------- options
    async def service_options(self, service: str, force: bool = False) -> dict:
        cached = self._options.get(service)
        if cached and not force and time.monotonic() - cached[0] < OPTIONS_TTL:
            return cached[1]
        c = self.client(service)
        profiles, roots = await asyncio.gather(c.get("/api/v3/qualityprofile"), c.get("/api/v3/rootfolder"))
        opts = {
            "quality_profiles": [{"id": p["id"], "name": p["name"]} for p in profiles or []],
            "root_folders": [{"path": r["path"], "free_space": r.get("freeSpace"), "accessible": r.get("accessible", True)}
                             for r in roots or []],
        }
        if service == "sonarr" and c.major_version and c.major_version < 4:
            langs = await c.get("/api/v3/languageprofile")
            opts["language_profiles"] = [{"id": p["id"], "name": p["name"]} for p in langs or []]
        self._options[service] = (time.monotonic(), opts)
        return opts

    def defaults(self) -> dict:
        d = self.db.get_setting("defaults", {}) or {}
        d.setdefault("season_monitor", "all")
        for k in ("radarr_profile_id", "sonarr_profile_id", "radarr_root", "sonarr_root"):
            d.setdefault(k, None)
        return d

    def save_defaults(self, values: dict) -> dict:
        d = self.defaults()
        if "season_monitor" in values and values["season_monitor"] not in SEASON_MODES:
            raise GatewayError(422, "config", msg("errors.season_monitor", modes=", ".join(SEASON_MODES)))
        d.update({k: v for k, v in values.items() if k in d})
        self.db.set_setting("defaults", d)
        return d

    async def all_options(self) -> dict:
        out: dict[str, Any] = {"defaults": self.defaults()}
        for service in ("radarr", "sonarr"):
            try:
                out[service] = await self.service_options(service, force=True)
            except ArrError as e:
                out[service] = {"error": e.as_dict()}
        return out

    @traced
    async def _resolve(self, media_type: str, profile_override: int | None, root_override: str | None) -> tuple[int, str]:
        service = SERVICE_FOR[media_type]
        name = service.capitalize()
        opts = await self.service_options(service)
        d = self.defaults()
        profiles = {p["id"] for p in opts["quality_profiles"]}
        if profile_override is not None:
            if profile_override not in profiles:
                raise ArrError(service, "config", msg("arr.profile_gone", profile=profile_override, name=name))
            profile = profile_override
        elif d[f"{service}_profile_id"] in profiles:
            profile = d[f"{service}_profile_id"]
        elif len(profiles) == 1:
            profile = next(iter(profiles))
        elif not profiles:
            raise ArrError(service, "config", msg("arr.no_profiles", name=name))
        else:
            raise ArrError(service, "config", msg("arr.choose_profile", name=name))
        accessible = {r["path"]: r.get("accessible", True) for r in opts["root_folders"]}
        roots = list(accessible)
        if root_override:
            if root_override not in roots:
                raise ArrError(service, "config", msg("arr.root_not_configured", root=root_override, name=name))
            root = root_override
        elif d[f"{service}_root"] in roots:
            root = d[f"{service}_root"]
        elif len(roots) == 1:
            root = roots[0]
        elif not roots:
            raise ArrError(service, "config", msg("arr.no_roots", name=name))
        else:
            raise ArrError(service, "config", msg("arr.choose_root", name=name))
        if accessible.get(root) is False:
            raise ArrError(service, "config", msg("arr.root_inaccessible", name=name, root=root))
        return profile, root

    # ----------------------------------------------------------------- library
    async def library(self, service: str, force: bool = False) -> list[dict]:
        cached = self._library.get(service)
        if cached and not force and time.monotonic() - cached[0] < LIBRARY_TTL:
            return cached[1]
        path = "/api/v3/movie" if service == "radarr" else "/api/v3/series"
        items = await self.client(service).get(path) or []
        self._library[service] = (time.monotonic(), items)
        return items

    async def find_in_library(self, media_type: str, external_id: int, force: bool = False) -> dict | None:
        service = SERVICE_FOR[media_type]
        key = "tmdbId" if media_type == "movie" else "tvdbId"
        for item in await self.library(service, force=force):
            if item.get(key) == external_id:
                return item
        return None

    # ---------------------------------------------------------------- metadata
    async def lookup_raw(self, media_type: str, external_id: int) -> dict | None:
        cached = self.db.get_metadata(media_type, external_id, METADATA_TTL)
        if cached is not None:
            return cached
        if media_type == "movie":
            try:
                data = await self.client("radarr").get("/api/v3/movie/lookup/tmdb", tmdbId=external_id)
            except ArrError as e:
                if e.status == 404:
                    return None
                raise
            if isinstance(data, list):
                data = next((d for d in data if isinstance(d, dict) and d.get("tmdbId") == external_id), None)
            elif not isinstance(data, dict) or data.get("tmdbId") != external_id:
                data = None     # an error object or null is never cached as metadata
        else:
            results = self._lookup_list("sonarr", await self.client("sonarr").get(
                "/api/v3/series/lookup", term=f"tvdb:{external_id}"))
            data = next((d for d in results if d.get("tvdbId") == external_id), None)
        if data:
            self.db.save_metadata(media_type, external_id, data)
        return data

    async def metadata(self, media_type: str, external_id: int) -> dict | None:
        raw = await self.lookup_raw(media_type, external_id)
        if raw is None:
            return None
        return normalize_movie(raw) if media_type == "movie" else normalize_series(raw)

    # --------------------------------------------------------------- discovery
    CARD_FIELDS = ("type", "id", "title", "original_title", "year", "decade", "genres", "poster", "overview",
                   "runtime", "rating", "ratings", "votes", "popularity", "language", "studio", "collection",
                   "certification", "keywords", "series_status")

    def _card(self, meta: dict, source: str, added: str | None = None) -> dict:
        return {**{k: meta.get(k) for k in self.CARD_FIELDS}, "source": source, "added": added,
                "status": self.status_for(meta["type"], meta["id"])}

    @staticmethod
    def _normalize(media_type: str, raw: dict) -> dict:
        return normalize_movie(raw) if media_type == "movie" else normalize_series(raw)

    async def radarr_discover(self, force: bool = False) -> list[dict]:
        """Radarr's own Discover feed: movies from your import lists plus its recommendations."""
        if self._discover_cache and not force and time.monotonic() - self._discover_cache[0] < POOL_TTL:
            return self._discover_cache[1]
        items = await self.client("radarr").get("/api/v3/importlist/movie", includeRecommendations="true") or []
        items = [m for m in items if m.get("tmdbId") and not m.get("isExcluded")]
        for m in items:   # same shape as a lookup: saves a lookup when the title is opened or requested
            if not m.get("isExisting"):
                self.db.save_metadata("movie", m["tmdbId"], m)
        self._discover_cache = (time.monotonic(), items)
        return items

    @traced
    async def search(self, media_type: str, term: str) -> list[dict]:
        """Free-text Radarr/Sonarr lookup, so anything they can find can be requested."""
        term = term.strip()
        key = (media_type, term.casefold())
        cached = self._search_cache.get(key)
        if cached and time.monotonic() - cached[0] < SEARCH_TTL:
            return cached[1]
        service = SERVICE_FOR[media_type]
        path = "/api/v3/movie/lookup" if media_type == "movie" else "/api/v3/series/lookup"
        id_key = "tmdbId" if media_type == "movie" else "tvdbId"
        raw = self._lookup_list(service, await self.client(service).get(path, term=term))
        cards = []
        for r in raw[:40]:
            if not r.get(id_key):
                continue
            self.db.save_metadata(media_type, r[id_key], r)
            cards.append(self._card(self._normalize(media_type, r), "search"))
        self._search_cache[key] = (time.monotonic(), cards)
        if len(self._search_cache) > 200:
            self._search_cache.pop(next(iter(self._search_cache)))
        return cards

    @staticmethod
    def _lookup_list(service: str, raw: Any) -> list[dict]:
        """A lookup answer must be a list; an error object or null (Arr starting up) is an error, never an
        empty result that gets cached."""
        if not isinstance(raw, list):
            raise ArrError(service, "unavailable", msg("arr.bad_lookup", name=service.capitalize()))
        return [r for r in raw if isinstance(r, dict)]

    @traced
    async def pool_cards(self, media_type: str) -> tuple[list[dict], dict | None]:
        """Every title discovery can show for one type: library, your titles list, Radarr Discover."""
        service = SERVICE_FOR[media_type]
        if service not in self.clients:
            return [], None
        cards: dict[int, dict] = {}
        error = None
        try:
            for item in await self.library(service):
                meta = self._normalize(media_type, item)
                if meta["id"]:
                    cards[meta["id"]] = self._card(meta, "library", item.get("added"))
        except ArrError as e:
            error = e.as_dict()
        if media_type == "movie":
            try:
                for raw in await self.radarr_discover():
                    if raw["tmdbId"] not in cards:
                        cards[raw["tmdbId"]] = self._card(normalize_movie(raw),
                                                          "recommended" if raw.get("isRecommendation") else "import_list")
            except ArrError as e:
                error = error or e.as_dict()
        missing = [i for i in self.titles.ids(media_type) if i not in cards]
        sem = asyncio.Semaphore(4)

        async def from_list(ext: int):
            nonlocal error
            async with sem:
                try:
                    meta = await self.metadata(media_type, ext)
                except ArrError as e:
                    if e.kind != "request":
                        error = error or e.as_dict()
                    return None
            if meta is None:
                log.warning(msg("log.titles_missing", media_type=media_type, ext=ext, service=service.capitalize()))
                return None
            return self._card(meta, "list")

        for c in await asyncio.gather(*(from_list(i) for i in missing)):
            if c:
                cards[c["id"]] = c
        return list(cards.values()), error

    @traced
    async def discover(self, media_type: str, filters: dict | None = None, include_tracked: bool = False,
                       sort: str | None = None, page: int | None = None, page_size: int | None = None) -> dict:
        if SERVICE_FOR[media_type] not in self.clients:
            return {"items": [], "genres": [], "facets": {}, "total": 0, "matched": 0, "error": None, "configured": False,
                    "searched": False, "request_plan": None}
        filters = dict(filters or {})
        cards, service_error = await self.pool_cards(media_type)
        searched = False
        found: list[dict] = []
        q = (filters.get("q") or "").strip()
        if len(q) >= 2:
            searched = True
            try:
                known = {c["id"] for c in cards}
                found = [c for c in await self.search(media_type, q) if c["id"] not in known]
                for c in found:
                    c["status"] = self.status_for(media_type, c["id"])
            except ArrError as e:
                service_error = service_error or e.as_dict()
        if not include_tracked:
            cards = [c for c in cards if c["status"]["state"] == "not_requested"]
            found = [c for c in found if c["status"]["state"] == "not_requested"]
        all_facets = discovery.facets(cards)       # facets describe the unfiltered set
        total = len(cards)
        neighbours = all_facets["genre_neighbours"]
        matched = discovery.apply_filters(cards, filters, neighbours)
        # Lookup results already match the search term (often by an alternate title): filter on everything else.
        matched += discovery.apply_filters(found, {**filters, "q": None}, neighbours)
        items = discovery.sort_cards(matched, sort or cfg("discovery.default_sort"))
        paging = {}
        if page_size:   # no cap: every match is reachable, one page at a time
            page = max(1, page or 1)
            paging = {"page": page, "page_size": page_size}
            items = items[(page - 1) * page_size:page * page_size]
        return {"items": items, "genres": [f["value"] for f in all_facets.get("genres", [])],
                "facets": all_facets, "total": total, "matched": len(matched), **paging,
                "error": service_error, "configured": True,
                "searched": searched, "request_plan": await self.request_plan(media_type)}

    @traced
    async def similar(self, media_type: str, external_id: int, mix: bool = True, limit: int = 20,
                      include_tracked: bool = False) -> dict:
        meta = await self.metadata(media_type, external_id)
        if meta is None:
            raise GatewayError(404, "not_found", msg("errors.unknown_title"))
        candidates, error = await self.pool_cards(media_type)
        other = "series" if media_type == "movie" else "movie"
        if mix and SERVICE_FOR[other] in self.clients:
            more, err2 = await self.pool_cards(other)
            candidates += more
            error = error or err2
        if not include_tracked:
            candidates = [c for c in candidates if c["status"]["state"] == "not_requested"]
        items = discovery.more_like_this(meta, candidates, limit=limit)
        return {"seed": {"type": media_type, "id": external_id, "title": meta.get("title")},
                "items": items, "candidates": len(candidates), "error": error}

    async def request_plan(self, media_type: str, profile_override: int | None = None,
                           root_override: str | None = None, season_override: str | None = None) -> dict:
        """What a one-tap request would use right now, by name, so the app can show it before you tap."""
        service = SERVICE_FOR[media_type]
        plan: dict[str, Any] = {"service": service, "quality_profile_id": None, "quality_profile": None,
                                "root_folder": None, "season_monitor": None, "problem": None}
        if media_type == "series":
            plan["season_monitor"] = season_override or self.defaults()["season_monitor"]
        try:
            profile, root = await self._resolve(media_type, profile_override, root_override)
            opts = await self.service_options(service)
            plan.update(quality_profile_id=profile, root_folder=root,
                        quality_profile=next((p["name"] for p in opts["quality_profiles"] if p["id"] == profile), None))
        except ArrError as e:
            plan["problem"] = {"kind": e.kind, "message": e.message}
        return plan

    @traced
    async def title_detail(self, media_type: str, external_id: int) -> dict:
        meta = await self.metadata(media_type, external_id)
        if meta is None:
            name = SERVICE_FOR[media_type].capitalize()
            raise GatewayError(404, "not_found", msg("errors.title_not_found", name=name))
        detail = {**meta, "status": self.status_for(media_type, external_id),
                  "request_plan": await self.request_plan(media_type)}
        if media_type == "series":
            detail["episodes"] = await self._episode_detail(external_id)
        return detail

    async def _episode_detail(self, tvdb_id: int) -> list[dict] | None:
        series = await self.find_in_library("series", tvdb_id)
        if not series:
            return None
        episodes = await self.client("sonarr").get("/api/v3/episode", seriesId=series["id"]) or []
        by_episode = {q.get("episodeId"): q for q in self._queue.get("sonarr", []) if q.get("seriesId") == series["id"]}
        out = []
        for ep in episodes:
            if ep.get("seasonNumber", 0) == 0 and not ep.get("hasFile"):
                continue
            q = by_episode.get(ep["id"])
            if ep.get("hasFile"):
                st = {"state": "complete", "progress": 1.0, "message": None}
            elif q:
                c = classify_queue_item(q)
                st = {"state": c["state"], "progress": c["progress"], "message": c["message"]}
            elif ep.get("monitored"):
                st = {"state": "searching", "progress": None, "message": None}
            else:
                st = {"state": "not_requested", "progress": None, "message": None}
            out.append({"season": ep.get("seasonNumber"), "episode": ep.get("episodeNumber"), "title": ep.get("title"),
                        "air_date": ep.get("airDate"), **st})
        out.sort(key=lambda e: (e["season"], e["episode"]))
        return out

    # ---------------------------------------------------------------- requests
    def _lock(self, media_type: str, external_id: int) -> asyncio.Lock:
        return self._title_locks.setdefault((media_type, external_id), asyncio.Lock())

    @traced
    async def request(self, media_type: str, external_id: int, profile_override: int | None = None,
                      root_override: str | None = None, idempotency_key: str | None = None,
                      season_override: str | None = None) -> dict:
        if media_type not in SERVICE_FOR:
            raise GatewayError(422, "request", msg("errors.bad_type"))
        if season_override is not None and season_override not in SEASON_MODES:
            raise GatewayError(422, "request", msg("errors.season_monitor", modes=", ".join(SEASON_MODES)))
        async with self._lock(media_type, external_id):
            existing = self.db.get_request(media_type, external_id)
            if existing and existing["arr_id"] and not existing["error_kind"]:
                return self._request_result(media_type, external_id, deduplicated=True)
            return await self._submit(media_type, external_id, profile_override, root_override, idempotency_key,
                                      season_override)

    @traced
    async def request_batch(self, items: list[dict], overrides: dict | None = None) -> dict:
        """Request several titles at once. Each item succeeds or fails on its own; one failure never
        stops the rest. overrides = {"movie": {...}, "series": {...}} applies to this batch only."""
        if not items:
            raise GatewayError(422, "request", msg("errors.choose_title"))
        if len(items) > BATCH_MAX:
            raise GatewayError(422, "request", msg("errors.batch_max", max=BATCH_MAX))
        overrides = overrides or {}
        seen, unique = set(), []
        for it in items:
            key = (it.get("type"), it.get("id"))
            if key not in seen:
                seen.add(key)
                unique.append(it)
        sem = asyncio.Semaphore(3)

        async def one(it: dict) -> dict:
            media_type, ext = it.get("type"), it.get("id")
            o = overrides.get(media_type) or {}
            async with sem:
                try:
                    r = await self.request(media_type, int(ext), o.get("quality_profile_id"), o.get("root_folder"),
                                           None, o.get("season_monitor"))
                    return {**r, "ok": True, "error": None}
                except GatewayError as e:
                    if e.kind not in ("download", "config", "unavailable"):   # those are logged by _submit
                        log.warning(msg("log.batch_item_failed", media_type=media_type, id=ext, kind=e.kind,
                                        error=e.message))
                    return {"type": media_type, "id": ext, "ok": False, "deduplicated": False,
                            "error": {"kind": e.kind, "message": e.message},
                            "status": self.status_for(media_type, ext) if media_type in SERVICE_FOR else None}

        results = await asyncio.gather(*(one(it) for it in unique))
        log.info(msg("log.batch", total=len(results),
                     added=sum(r["ok"] and not r["deduplicated"] for r in results),
                     existing=sum(r["ok"] and r["deduplicated"] for r in results), failed=sum(not r["ok"] for r in results)))
        return {"results": results,
                "added": sum(r["ok"] and not r["deduplicated"] for r in results),
                "already": sum(r["ok"] and r["deduplicated"] for r in results),
                "failed": sum(not r["ok"] for r in results)}

    @traced
    async def _submit(self, media_type, external_id, profile_override, root_override, idempotency_key,
                      season_override=None) -> dict:
        service = SERVICE_FOR[media_type]
        name = service.capitalize()
        try:
            # 1. Already in the library? Link to it rather than adding again.
            found = await self.find_in_library(media_type, external_id, force=True)
            if found:
                self.db.upsert_request(media_type, external_id, arr_id=found["id"], title=found.get("title"),
                                       error_kind=None, error_message=None)
                await self.refresh_status(force_library=False)
                return self._request_result(media_type, external_id, deduplicated=True)
            # 2. Metadata for the add body.
            try:
                raw = await self.lookup_raw(media_type, external_id)
            except ArrError as e:
                if e.kind != "request":
                    raise
                raw = None
            if raw is None:
                raise GatewayError(404, "not_found", msg("errors.title_not_found_add", name=name))
            meta = self._normalize(media_type, raw)
            # 3. Defaults (or one-off override; never persisted).
            profile, root = await self._resolve(media_type, profile_override, root_override)
            self.db.upsert_request(media_type, external_id, title=meta["title"], quality_profile_id=profile,
                                   root_folder=root, idempotency_key=idempotency_key)
            # 4. Add.
            body = await (self._movie_body(raw, profile, root) if media_type == "movie"
                          else self._series_body(raw, profile, root, season_override))
            path = "/api/v3/movie" if media_type == "movie" else "/api/v3/series"
            try:
                created = await self.client(service).post(path, body)
            except ArrError as e:
                already = e.kind == "request" and "already" in e.message.lower()
                if not already and e.kind != "unavailable":
                    raise
                # Someone else added it first, or the add timed out after Radarr saved it: look once more.
                try:
                    created = await self.find_in_library(media_type, external_id, force=True)
                except ArrError:
                    created = None
                if not created:
                    raise e
                if not already:
                    log.info(msg("log.add_landed", service=name, media_type=media_type, id=external_id, error=e.message))
            self.db.upsert_request(media_type, external_id, arr_id=created["id"], error_kind=None, error_message=None)
            log.info(msg("log.requested", media_type=media_type, id=external_id, title=meta["title"], profile=profile, root=root))
        except ArrError as e:
            kind = "download" if e.kind == "request" else "config" if e.kind == "config" else "unavailable"
            log.warning(msg("log.request_failed", media_type=media_type, id=external_id, kind=kind, error=e.message))
            self.db.upsert_request(media_type, external_id, error_kind=kind, error_message=e.message)
            self._patch_snapshot(media_type, external_id, {"state": "failed", "progress": None,
                                                            "message": e.message, "error_kind": kind})
            raise GatewayError(503 if kind == "unavailable" else 422 if kind == "config" else 502, kind, e.message)
        self._library.pop(service, None)
        self.calendar_cache.clear(service)
        await self.refresh_status()
        self.wake.set()
        return self._request_result(media_type, external_id, deduplicated=False)

    async def _movie_body(self, raw: dict, profile: int, root: str) -> dict:
        body = {k: v for k, v in raw.items() if k not in ("id", "path", "folderName", "movieFile")}
        body.update({"qualityProfileId": profile, "rootFolderPath": root, "monitored": True,
                     "minimumAvailability": "released", "addOptions": {"searchForMovie": True}})
        return body

    async def _series_body(self, raw: dict, profile: int, root: str, season_override: str | None = None) -> dict:
        mode = season_override or self.defaults()["season_monitor"]
        seasons = [dict(s) for s in raw.get("seasons") or []]
        numbers = sorted(s["seasonNumber"] for s in seasons if s.get("seasonNumber", 0) > 0)
        pick = {"all": set(numbers), "first": set(numbers[:1]), "latest": set(numbers[-1:]), "future": set()}[mode]
        for s in seasons:
            s["monitored"] = s.get("seasonNumber") in pick
        body = {k: v for k, v in raw.items() if k not in ("id", "path", "folder")}
        body.update({
            "qualityProfileId": profile, "rootFolderPath": root, "monitored": True, "seasonFolder": True,
            "seasons": seasons, "seriesType": raw.get("seriesType") or "standard",
            "addOptions": {"monitor": {"all": "all", "first": "firstSeason", "latest": "latestSeason",
                                       "future": "future"}[mode],
                           "searchForMissingEpisodes": mode != "future",
                           "ignoreEpisodesWithFiles": False, "ignoreEpisodesWithoutFiles": False},
        })
        client = self.client("sonarr")
        if client.major_version and client.major_version < 4:
            langs = (await self.service_options("sonarr")).get("language_profiles") or []
            if not langs:
                raise ArrError("sonarr", "config", msg("arr.language_profile"))
            body["languageProfileId"] = langs[0]["id"]
        return body

    @traced
    async def retry(self, media_type: str, external_id: int) -> dict:
        service = SERVICE_FOR.get(media_type)
        if not service:
            raise GatewayError(422, "request", msg("errors.bad_type"))
        async with self._lock(media_type, external_id):
            last = self._last_retry.get((media_type, external_id))
            if last is not None and time.monotonic() - last < RETRY_COOLDOWN:
                log.info(msg("log.retry_cooldown", media_type=media_type, id=external_id))
                return self._request_result(media_type, external_id, deduplicated=True)
            record = self.db.get_request(media_type, external_id)
            try:
                found = await self.find_in_library(media_type, external_id, force=True)
            except ArrError as e:
                raise GatewayError(503 if e.kind == "unavailable" else 422, e.kind, e.message)
            if not found:
                # Confirmed absent from Arr (never added, removed there, or the add never reached the gateway
                # because the phone lost its connection): add it now, with the defaults if we never saw it.
                record = record or {}
                return await self._submit(media_type, external_id, record.get("quality_profile_id"),
                                          record.get("root_folder"), None)
            client = self.client(service)
            await self.refresh_status()
            id_key = "movieId" if media_type == "movie" else "seriesId"
            failed = [q for q in self._queue.get(service, [])
                      if q.get(id_key) == found["id"] and classify_queue_item(q)["state"] == "failed"]
            try:
                for q in failed:
                    await client.delete(f"/api/v3/queue/{q['id']}", removeFromClient="true", blocklist="true")
                if media_type == "movie":
                    await client.post("/api/v3/command", {"name": "MoviesSearch", "movieIds": [found["id"]]})
                else:
                    await client.post("/api/v3/command", {"name": "SeriesSearch", "seriesId": found["id"]})
            except ArrError as e:
                raise GatewayError(503 if e.kind == "unavailable" else 422, e.kind, e.message)
            self.db.upsert_request(media_type, external_id, arr_id=found["id"], title=found.get("title"),
                                   error_kind=None, error_message=None)
            self._last_retry[(media_type, external_id)] = time.monotonic()
            log.info(msg("log.retried", media_type=media_type, id=external_id, cleared=len(failed)))
            self._patch_snapshot(media_type, external_id, {"state": "searching", "progress": None,
                                                            "message": "Searching again", "error_kind": None})
            self.wake.set()
            return self._request_result(media_type, external_id, deduplicated=False)

    def _request_result(self, media_type: str, external_id: int, deduplicated: bool) -> dict:
        rec = self.db.get_request(media_type, external_id) or {}
        return {"type": media_type, "id": external_id, "arr_id": rec.get("arr_id"),
                "deduplicated": deduplicated, "status": self.status_for(media_type, external_id)}

    # ---------------------------------------------------------------- calendar
    async def calendar(self, start: str | None = None, end: str | None = None, tz: str | None = None) -> dict:
        """Upcoming and recent releases in the viewer's local days (app/calendar.py). On demand only."""
        try:
            return await calendar_feed.build(self, start, end, tz, image=_image)
        except calendar_feed.CalendarError as e:
            raise GatewayError(422, "request", str(e))

    # ------------------------------------------------------------------ status
    def status_for(self, media_type: str, external_id: int) -> dict:
        return self.snapshot["items"].get(f"{media_type}:{external_id}") or \
            {"state": "not_requested", "progress": None, "message": None, "error_kind": None}

    def _patch_snapshot(self, media_type: str, external_id: int, status: dict) -> None:
        self.snapshot["items"][f"{media_type}:{external_id}"] = status
        self._recompute_etag()

    def _recompute_etag(self) -> None:
        body = json.dumps({"items": self.snapshot["items"], "services": self.snapshot["services"]}, sort_keys=True)
        self.etag = '"' + hashlib.sha1(body.encode()).hexdigest()[:16] + '"'

    @traced
    async def refresh_status(self, force_library: bool = False) -> dict:
        async with self._refresh_lock:
            items: dict[str, dict] = {}
            services: dict[str, dict] = {}
            activity: list[dict] = []
            for service, media_type in (("radarr", "movie"), ("sonarr", "series")):
                if service not in self.clients:
                    services[service] = {"reachable": None, "error": None, "configured": False}
                    continue
                try:
                    client = self.client(service)
                    if service == "radarr":
                        queue = await client.get("/api/v3/queue/details", includeMovie="false")
                    else:
                        queue = await client.get("/api/v3/queue/details", includeEpisode="false", includeSeries="false")
                    library = await self.library(service, force=force_library)
                    self._queue[service] = queue or []
                    services[service] = {"reachable": True, "error": None, "configured": True}
                except ArrError as e:
                    services[service] = {"reachable": False, "error": e.as_dict(), "configured": True}
                    # keep last known statuses for this service, flagged stale
                    for k, v in self.snapshot["items"].items():
                        if k.startswith(media_type + ":"):
                            items[k] = {**v, "stale": True}
                    continue
                agg = self._aggregate_movies if service == "radarr" else self._aggregate_series
                for key, st, lib_item in agg(library, self._queue[service]):
                    items[key] = st
                    activity.append({"key": key, "lib": lib_item})
            # Request records whose Arr add failed (no Arr record yet) or whose Arr record vanished.
            missing: dict[str, int] = {k: n for k, n in self._missing.items()
                                       if services.get(SERVICE_FOR[k.split(":")[0]], {}).get("reachable") is False}
            for rec in self.db.list_requests():
                key = f"{rec['media_type']}:{rec['external_id']}"
                svc = SERVICE_FOR[rec["media_type"]]
                if key in items or services.get(svc, {}).get("reachable") is False:
                    continue
                if rec["error_kind"]:
                    items[key] = {"state": "failed", "progress": None, "message": rec["error_message"],
                                  "error_kind": rec["error_kind"]}
                elif rec["arr_id"]:
                    # One empty or partial library answer (restarting, database locked) must not orphan every
                    # request: only reads in a row that agree it's gone count (KB-07).
                    missing[key] = self._missing.get(key, 0) + 1
                    if missing[key] >= MISSING_CONFIRMATIONS:
                        items[key] = {"state": "failed", "progress": None, "error_kind": "download",
                                      "message": f"No longer in {svc.capitalize()}. Retry will add it again."}
                    else:
                        log.info(msg("log.missing_unconfirmed", title=key, service=svc.capitalize(),
                                     count=missing[key], needed=MISSING_CONFIRMATIONS))
                        if key in self.snapshot["items"]:
                            items[key] = self.snapshot["items"][key]
                        self._library.pop(svc, None)    # read it fresh next cycle, not from the cache
            self._missing = missing
            for svc, v in services.items():
                self._note_health(svc, v["reachable"], v["error"])
            self._activity_lib = {a["key"]: a["lib"] for a in activity}
            self.snapshot = {"items": items, "services": services, "generated_at": time.time()}
            self.snapshot_at = time.monotonic()
            self._recompute_etag()
            self.polls += 1
            return self.snapshot

    def _aggregate_movies(self, movies: list[dict], queue: list[dict]):
        by_movie: dict[int, list[dict]] = {}
        for q in queue:
            if q.get("movieId"):
                by_movie.setdefault(q["movieId"], []).append(q)
        for m in movies:
            if not m.get("tmdbId"):
                continue  # added manually without a TMDB match; can't be matched to a title
            qs = [classify_queue_item(q) for q in by_movie.get(m["id"], [])]
            if m.get("hasFile") and not any(c["state"] == "failed" for c in qs):
                st = {"state": "complete", "progress": 1.0, "message": None, "error_kind": None}
            elif qs:
                # Several grabs for one film (e.g. a retry while the old one lingers): show the most important.
                c = min(qs, key=lambda c: STATE_PRIORITY.index(c["state"]) if c["state"] in STATE_PRIORITY else 99)
                st = {"state": c["state"], "progress": c["progress"], "message": c["message"],
                      "error_kind": "download" if c["state"] == "failed" else None}
            elif m.get("monitored"):
                st = {"state": "searching", "progress": None, "message": "Waiting for a release", "error_kind": None}
            else:
                st = {"state": "paused", "progress": None, "message": "Not monitored in Radarr", "error_kind": None}
            yield f"movie:{m.get('tmdbId')}", st, m

    def _aggregate_series(self, series: list[dict], queue: list[dict]):
        by_series: dict[int, list[dict]] = {}
        for q in queue:
            if q.get("seriesId"):
                by_series.setdefault(q["seriesId"], []).append(q)
        for s in series:
            if not s.get("tvdbId"):
                continue
            stats = s.get("statistics") or {}
            have, want = stats.get("episodeFileCount") or 0, stats.get("episodeCount") or 0
            qs = [classify_queue_item(q) for q in by_series.get(s["id"], [])]
            base = {"episodes_have": have, "episodes_wanted": want}
            if qs:
                states = {c["state"] for c in qs}
                state = next(p for p in STATE_PRIORITY if p in states) if states & set(STATE_PRIORITY) else "queued"
                size = sum(c["size"] for c in qs)
                left = sum(c["sizeleft"] for c in qs)
                progress = round((size - left) / size, 4) if size > 0 else None
                failed = [c for c in qs if c["state"] == "failed"]
                if failed:
                    msg = f"{len(failed)} episode download(s) failed" + (f": {failed[0]['message']}" if failed[0]["message"] else "")
                else:
                    msg = f"{len(qs)} episode(s) in queue"
                st = {"state": state, "progress": progress, "message": msg,
                      "error_kind": "download" if state == "failed" else None, **base}
            elif want > 0 and have >= want:
                st = {"state": "complete", "progress": 1.0, "message": None, "error_kind": None, **base}
            elif s.get("monitored"):
                st = {"state": "searching", "progress": (have / want) if want else None,
                      "message": "Waiting for releases" if want else "Waiting for new episodes",
                      "error_kind": None, **base}
            else:
                st = {"state": "paused", "progress": None, "message": "Not monitored in Sonarr", "error_kind": None, **base}
            yield f"series:{s.get('tvdbId')}", st, s

    def activity(self) -> list[dict]:
        lib = getattr(self, "_activity_lib", {})
        recent = {f"{r['media_type']}:{r['external_id']}": r for r in self.db.list_requests()}
        out = []
        for key, st in self.snapshot["items"].items():
            req = recent.get(key)
            # Show everything in flight or failed, plus completed items requested via the app.
            if st["state"] == "complete" and not req:
                continue
            media_type, ext = key.split(":")
            item = lib.get(key)
            norm = (normalize_movie(item) if media_type == "movie" else normalize_series(item)) if item else None
            out.append({"type": media_type, "id": int(ext),
                        "title": (norm or {}).get("title") or (req or {}).get("title"),
                        "year": (norm or {}).get("year"), "poster": (norm or {}).get("poster"),
                        "status": st, "requested_at": (req or {}).get("created_at"),
                        "updated_at": (req or {}).get("updated_at")})
        order = {"failed": 0, "downloading": 1, "importing": 2, "stalled": 3, "paused": 4, "queued": 5,
                 "searching": 6, "complete": 7}
        out.sort(key=lambda a: (order.get(a["status"]["state"], 9), -(a["updated_at"] or 0)))
        return out

    # ----------------------------------------------------------------- polling
    def note_client(self, viewing: str | None) -> None:
        now = time.monotonic()
        was_gone = self.last_seen is None or now - self.last_seen > CLIENT_GONE_AFTER
        self.last_seen = now
        if viewing == "activity":
            self.last_watching = now
        if was_gone:
            self.wake.set()  # app came back to the foreground: refresh now

    def has_active(self) -> bool:
        return any(v["state"] in ACTIVE_STATES for v in self.snapshot["items"].values())

    def poll_interval(self, now: float | None = None) -> float | None:
        now = now or time.monotonic()
        active = self.has_active()
        client_here = self.last_seen is not None and now - self.last_seen < CLIENT_GONE_AFTER
        watching = self.last_watching is not None and now - self.last_watching < WATCHING_WINDOW
        if active and watching:
            return POLL_WATCHING
        if active and client_here:
            return POLL_FOREGROUND
        if active:
            return POLL_IDLE_ACTIVE
        if client_here:
            return POLL_FOREGROUND_QUIET
        return None

    def _note_health(self, service: str, reachable: bool | None, error: dict | None) -> None:
        """Record a reachability result and log the transitions, so the log answers
        "when did Sonarr go down, why, and when did it come back?". Times are monotonic (durations only)."""
        now = time.monotonic()
        prev = self._service_health.get(service, {})
        entry = {"reachable": reachable, "error": error, "checked_at": now,
                 "down_since": prev.get("down_since"), "reminded_at": prev.get("reminded_at")}
        name = service.capitalize()
        reason = (error or {}).get("message")
        if reachable is False:
            if prev.get("reachable") is not False:
                entry["down_since"] = entry["reminded_at"] = now
                log.warning(msg("log.service_down", service=name, error=reason))
            elif now - (prev.get("reminded_at") or now) >= DOWN_REMINDER_EVERY:
                entry["reminded_at"] = now
                log.warning(msg("log.service_still_down", service=name, error=reason,
                                minutes=round((now - entry["down_since"]) / 60)))
        else:
            if reachable and prev.get("reachable") is False and prev.get("down_since"):
                log.info(msg("log.service_up", service=name, minutes=round((now - prev["down_since"]) / 60)))
            entry["down_since"] = entry["reminded_at"] = None
        self._service_health[service] = entry

    def service_health(self, service: str) -> dict:
        return self._service_health.get(service, {})

    async def check_reachability(self) -> None:
        """Cheap idle check: one system/status call per connected server. A change triggers a full refresh
        so the snapshot the frontends see agrees with /v1/health."""
        changed = False
        for service, client in list(self.clients.items()):
            before = self._service_health.get(service, {}).get("reachable")
            try:
                await client.system_status()
                self._note_health(service, True, None)
            except ArrError as e:
                self._note_health(service, False, e.as_dict())
            changed |= before != self._service_health[service]["reachable"]
        if changed:
            await self.refresh_status()

    def poller_state(self) -> str:
        """"running", "off" (POLLER=0) or "stopped" (it should run but the task ended: a bug)."""
        if self.poller_task is None:
            return "off"
        return "stopped" if self.poller_task.done() else "running"

    async def ensure_fresh(self) -> None:
        interval = self.poll_interval() or POLL_FOREGROUND_QUIET
        if self.snapshot_at is None or time.monotonic() - self.snapshot_at > interval:
            await self.refresh_status()

    async def run_poller(self) -> None:
        while True:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # never let the loop die
                self.last_poll_error_at = time.monotonic()
                log.exception(msg("log.poller_failed"))
                await asyncio.sleep(POLLER_ERROR_BACKOFF)

    async def _poll_once(self) -> None:
        # Stored, so wall clock; a last run "in the future" (clock went back) counts as due.
        since = time.time() - (self.db.get_setting("compat_last_run") or 0)
        if since > COMPAT_EVERY or since < 0:
            self.schedule_compat()
        interval = self.poll_interval()
        idle = interval is None
        try:
            await asyncio.wait_for(self.wake.wait(), timeout=interval or IDLE_HEALTH_CHECK or None)
        except asyncio.TimeoutError:
            pass
        woken = self.wake.is_set()
        self.wake.clear()
        if idle and not woken:
            await self.check_reachability()
        else:
            await self.refresh_status()

    async def aclose(self) -> None:
        for c in self.clients.values():
            await c.aclose()
