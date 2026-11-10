"""Calendar: upcoming and recent releases from Radarr and Sonarr, grouped into the viewer's local days.

Fetched on demand only (GET /v1/calendar): the gateway's poller never asks for it.

Time rules (docs/KNOWN_BUGS_PRD.md section B):
- Radarr's inCinemas / digitalRelease / physicalRelease are calendar DAYS sent as midnight UTC. The date part
  is the day; converting it to the viewer's timezone would move it a day back west of UTC (KB-12).
- Sonarr's airDateUtc is a real instant: converted to the viewer's IANA timezone for the day and HH:MM. Both
  servers are asked for the UTC range padded by a day on each side so an evening episode that is "tomorrow"
  in UTC is not lost at the edges (KB-13). airDate (a date only) is the fallback when there is no instant.
- An unknown timezone is UTC, and the response says so (KB-14). Days are stepped as dates, never as
  86,400-second jumps, so daylight saving changes neither skip nor repeat a day (KB-16).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .arr import ArrError
from .config import cfg, msg
from .logs import get_logger, traced

log = get_logger("calendar")

SERVICES = (("radarr", "movie"), ("sonarr", "series"))
MOVIE_KINDS = (("inCinemas", "cinema"), ("digitalRelease", "digital"), ("physicalRelease", "physical"))
UTC_NAME = "UTC"


class CalendarError(ValueError):
    """A bad start/end; the route answers 422 with this message."""


def resolve_tz(name: str | None) -> tuple[dt.tzinfo, str, bool]:
    """(zone, its name, fell_back). Empty means UTC without a fallback note; unknown means UTC with one."""
    if not name:
        return dt.timezone.utc, UTC_NAME, False
    try:
        return ZoneInfo(name), name, False
    except (ZoneInfoNotFoundError, ValueError, OSError):
        log.warning(msg("calendar.tz_fallback", tz=name[:64]))
        return dt.timezone.utc, UTC_NAME, True


def parse_day(value: str, field: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise CalendarError(msg("calendar.bad_date", field=field)) from None


def local_today(tz: dt.tzinfo) -> dt.date:
    return dt.datetime.now(tz).date()


def date_range(start: str | None, end: str | None, tz: dt.tzinfo) -> tuple[dt.date, dt.date]:
    today = local_today(tz)
    first = parse_day(start, "start") if start else today - dt.timedelta(days=cfg("calendar.default_past_days"))
    last = parse_day(end, "end") if end else today + dt.timedelta(days=cfg("calendar.default_future_days"))
    if last < first:
        raise CalendarError(msg("calendar.end_before_start"))
    max_days = cfg("calendar.max_days")
    if (last - first).days + 1 > max_days:
        raise CalendarError(msg("calendar.too_long", max=max_days))
    return first, last


def query_window(first: dt.date, last: dt.date) -> tuple[str, str]:
    """The UTC window asked of Radarr/Sonarr: the local range plus a pad of whole days each side.
    Local days are at most 14 hours away from UTC, so one day of pad covers every timezone."""
    pad = dt.timedelta(days=cfg("calendar.pad_days"))
    return (first - pad).isoformat() + "T00:00:00Z", (last + pad + dt.timedelta(days=1)).isoformat() + "T00:00:00Z"


def days_between(first: dt.date, last: dt.date) -> list[dt.date]:
    return [first + dt.timedelta(days=i) for i in range((last - first).days + 1)]


def _parse_instant(value: str) -> dt.datetime | None:
    try:
        when = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)


def _day_part(value: Any) -> dt.date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def episode_when(ep: dict, tz: dt.tzinfo) -> tuple[dt.date | None, str | None]:
    instant = _parse_instant(ep.get("airDateUtc")) if ep.get("airDateUtc") else None
    if instant:
        local = instant.astimezone(tz)
        return local.date(), local.strftime("%H:%M")
    return _day_part(ep.get("airDate")), None


# ---------------------------------------------------------------- shaping
def movie_entries(movies: list, first: dt.date, last: dt.date, image: Callable) -> list[dict]:
    out = []
    for m in movies:
        if not isinstance(m, dict) or not m.get("tmdbId"):
            continue
        for field, kind in MOVIE_KINDS:
            day = _day_part(m.get(field))     # a day, never converted (KB-12)
            if day is None or not first <= day <= last:
                continue
            out.append({"type": "movie", "id": m["tmdbId"], "title": m.get("title"), "year": m.get("year"),
                        "poster": image(m.get("images"), "poster"), "date": day.isoformat(), "time": None,
                        "kind": kind, "monitored": bool(m.get("monitored", True)),
                        "has_file": bool(m.get("hasFile")), "special": False})
    return out


def _premiere(ep: dict) -> str | None:
    if ep.get("episodeNumber") != 1 or not ep.get("seasonNumber"):
        return None
    return "series" if ep.get("seasonNumber") == 1 else "season"


def episode_entries(episodes: list, first: dt.date, last: dt.date, tz: dt.tzinfo, image: Callable) -> list[dict]:
    """One entry per show, season and local day: a season dropped at once is one entry (KB-32)."""
    groups: dict[tuple, list[tuple[str | None, dict]]] = {}
    shows: dict[int, dict] = {}
    for ep in episodes:
        if not isinstance(ep, dict):
            continue
        series = ep.get("series") if isinstance(ep.get("series"), dict) else {}
        tvdb = series.get("tvdbId")
        if not tvdb:
            continue
        day, clock = episode_when(ep, tz)
        if day is None or not first <= day <= last:
            continue
        shows[tvdb] = series
        groups.setdefault((tvdb, day, ep.get("seasonNumber") or 0), []).append((clock, ep))
    out = []
    for (tvdb, day, season), items in groups.items():
        items.sort(key=lambda x: (x[1].get("episodeNumber") or 0))
        series = shows[tvdb]
        eps = [e for _, e in items]
        numbers = [e.get("episodeNumber") or 0 for e in eps]
        times = [c for c, _ in items if c]
        series_monitored = bool(series.get("monitored", True))
        finales = [e.get("finaleType") for e in eps if e.get("finaleType")]
        premieres = [p for p in (_premiere(e) for e in eps) if p]
        out.append({
            "type": "series", "id": tvdb, "title": series.get("title"), "year": series.get("year"),
            "poster": image(series.get("images"), "poster"), "date": day.isoformat(),
            "time": min(times) if times else None, "kind": "episode",
            "season": season, "episode": numbers[0], "episodes": [numbers[0], numbers[-1]], "count": len(eps),
            "episode_title": eps[0].get("title") if len(eps) == 1 else None,
            # Series monitoring counts as well as the episode's own flag (KB-34).
            "monitored": series_monitored and any(bool(e.get("monitored", True)) for e in eps),
            "has_file": all(bool(e.get("hasFile")) for e in eps),
            "special": season == 0,
            "premiere": premieres[0] if premieres else None,
            "finale": finales[-1] if finales else None,
        })
    return out


def sort_key(item: dict) -> tuple:
    """Monitored first, then all-day releases before timed ones, then by time, then title (KB-34)."""
    return (not item["monitored"], item["time"] is not None, item["time"] or "", (item["title"] or "").casefold())


# ---------------------------------------------------------------- fetching
class CalendarCache:
    """Raw Radarr/Sonarr answers per (service, window), kept briefly so scrolling and tab switches don't
    refetch. Statuses are never cached: they are applied from the live snapshot on every response (KB-36).
    Cleared when a connection changes (KB-30) and after a request. Errors are never cached."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str, str], tuple[float, list]] = {}

    def get(self, key: tuple[str, str, str]) -> list | None:
        hit = self._data.get(key)
        if hit and time.monotonic() - hit[0] < cfg("calendar.cache_ttl"):
            return hit[1]
        return None

    def put(self, key: tuple[str, str, str], value: list) -> None:
        self._data[key] = (time.monotonic(), value)

    def clear(self, service: str | None = None) -> None:
        if service is None:
            self._data.clear()
        else:
            for key in [k for k in self._data if k[0] == service]:
                del self._data[key]


async def _fetch(gateway: Any, service: str, window: tuple[str, str]) -> tuple[list, dict]:
    client = gateway.clients.get(service)
    if client is None:
        return [], {"configured": False, "ok": None, "error": None}
    key = (service, *window)
    cached = gateway.calendar_cache.get(key)
    if cached is not None:
        return cached, {"configured": True, "ok": True, "error": None}
    params = {"start": window[0], "end": window[1], "unmonitored": "true"}
    if service == "sonarr":
        params["includeSeries"] = "true"
    try:
        data = await client.get("/api/v3/calendar", **params)
        if not isinstance(data, list):     # an error object or null while the server starts: never cache it
            raise ArrError(service, "unavailable", msg("calendar.not_a_list", name=cfg("arr.names")[service]))
    except ArrError as e:
        log.warning(msg("calendar.fetch_failed", service=service, error=e.message))
        return [], {"configured": True, "ok": False, "error": e.as_dict()}
    gateway.calendar_cache.put(key, data)
    return data, {"configured": True, "ok": True, "error": None}


@traced
async def build(gateway: Any, start: str | None, end: str | None, tz_name: str | None,
                image: Callable) -> dict:
    tz, tz_label, fell_back = resolve_tz(tz_name)
    first, last = date_range(start, end, tz)
    window = query_window(first, last)
    (movies, radarr), (episodes, sonarr) = await asyncio.gather(
        *(_fetch(gateway, service, window) for service, _ in SERVICES))
    items = movie_entries(movies, first, last, image) + episode_entries(episodes, first, last, tz, image)
    by_day: dict[str, list[dict]] = {}
    for item in items:
        item["status"] = gateway.status_for(item["type"], item["id"])
        by_day.setdefault(item["date"], []).append(item)
    days = [{"date": d.isoformat(), "items": sorted(by_day.get(d.isoformat(), []), key=sort_key)}
            for d in days_between(first, last)]
    log.debug(msg("calendar.built", start=first, end=last, tz=tz_label, items=len(items)))
    return {"start": first.isoformat(), "end": last.isoformat(), "tz": tz_label, "tz_fallback": fell_back,
            "days": days, "services": {"radarr": radarr, "sonarr": sonarr}}
