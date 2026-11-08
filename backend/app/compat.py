"""API compatibility check: does *this* Radarr/Sonarr still expose everything the app relies on?

Three layers, cheapest first:
1. Live probes of every read endpoint the gateway uses, checking the response fields it reads.
2. Safe "validation probes" for write endpoints: POST an empty body and expect a 400 validation
   error. Nothing is created; a 404/405 means the route is gone.
3. The OpenAPI spec: first from the server itself, otherwise the upstream spec published in the
   Radarr/Sonarr GitHub repos (fetched without any credentials). Used to confirm write endpoints,
   request fields and query parameters that can't be probed safely.

The result is stored, and each run is diffed against the previous one (including the version),
so the app can say exactly what changed after an Arr update.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .arr import ArrClient
from .config import msg
from .logs import get_logger

log = get_logger("compat")

SPEC_TTL = 7 * 86400
LOCAL_SPEC_PATHS = ("/docs/v3/openapi.json", "/api/v3/openapi.json", "/openapi.json", "/swagger/v3/swagger.json")
UPSTREAM_SPECS = {
    "radarr": ["https://raw.githubusercontent.com/Radarr/Radarr/develop/src/Radarr.Api.V3/openapi.json"],
    "sonarr": ["https://raw.githubusercontent.com/Sonarr/Sonarr/v5-develop/src/Sonarr.Api.V3/openapi.json",
               "https://raw.githubusercontent.com/Sonarr/Sonarr/develop/src/Sonarr.Api.V3/openapi.json"],
}
# Well-known titles used only to read lookup responses (nothing is added).
PROBE_TMDB_ID = 603      # The Matrix
PROBE_TVDB_ID = 81189    # Breaking Bad

RANK = {"ok": 0, "info": 1, "warning": 2, "error": 3}


@dataclass
class Check:
    name: str
    method: str
    path: str
    params: dict | None = None
    fields: list[str] = field(default_factory=list)          # required response fields ("[]." = list item)
    optional: list[str] = field(default_factory=list)        # nice-to-have fields (missing = info)
    write: bool = False                                       # validation probe instead of a real call
    request_fields: list[str] = field(default_factory=list)  # checked against the spec's request schema
    query_params: list[str] = field(default_factory=list)    # checked against the spec's parameters
    spec_only: bool = False                                    # can't be probed safely; spec only
    when: str | None = None                                    # "sonarr_v3" etc.


@dataclass
class Feature:
    name: str
    checks: list[Check]


RADARR = [
    Feature("Connection", [Check("System status", "GET", "/api/v3/system/status", fields=["appName", "version"])]),
    Feature("Quality profiles and folders", [
        Check("Quality profiles", "GET", "/api/v3/qualityprofile", fields=["[].id", "[].name"]),
        Check("Root folders", "GET", "/api/v3/rootfolder", fields=["[].path"], optional=["[].accessible", "[].freeSpace"]),
    ]),
    Feature("Library (hide what you own)", [
        Check("Movie list", "GET", "/api/v3/movie", fields=["[].id", "[].tmdbId", "[].hasFile", "[].monitored", "[].title"],
              optional=["[].images", "[].year"]),
    ]),
    Feature("Title details and filters", [
        Check("Movie lookup", "GET", "/api/v3/movie/lookup/tmdb", params={"tmdbId": PROBE_TMDB_ID},
              fields=["title", "tmdbId", "year", "images", "genres"],
              optional=["overview", "runtime", "studio", "certification", "ratings", "originalLanguage",
                        "popularity", "collection", "keywords", "originalTitle"]),
    ]),
    Feature("Discover and search", [
        Check("Radarr Discover (import lists + recommendations)", "GET", "/api/v3/importlist/movie",
              params={"includeRecommendations": "true"}, optional=["[].tmdbId", "[].title"]),
        Check("Title search", "GET", "/api/v3/movie/lookup", params={"term": "nosferatu"},
              optional=["[].tmdbId", "[].title"]),
    ]),
    Feature("Download progress", [
        Check("Queue", "GET", "/api/v3/queue/details", params={"includeMovie": "false"},
              fields=["[].id", "[].movieId", "[].size", "[].sizeleft", "[].status"],
              optional=["[].trackedDownloadState", "[].trackedDownloadStatus", "[].errorMessage", "[].statusMessages"]),
    ]),
    Feature("Adding films", [
        Check("Add movie", "POST", "/api/v3/movie", write=True,
              request_fields=["tmdbId", "title", "qualityProfileId", "rootFolderPath", "monitored",
                              "minimumAvailability", "addOptions"]),
    ]),
    Feature("Retry", [
        Check("Search command", "POST", "/api/v3/command", spec_only=True, request_fields=["name"]),
        Check("Remove failed download", "DELETE", "/api/v3/queue/{id}", spec_only=True,
              query_params=["removeFromClient", "blocklist"]),
    ]),
]

SONARR = [
    Feature("Connection", [Check("System status", "GET", "/api/v3/system/status", fields=["appName", "version"])]),
    Feature("Quality profiles and folders", [
        Check("Quality profiles", "GET", "/api/v3/qualityprofile", fields=["[].id", "[].name"]),
        Check("Root folders", "GET", "/api/v3/rootfolder", fields=["[].path"], optional=["[].accessible"]),
        Check("Language profiles (Sonarr v3)", "GET", "/api/v3/languageprofile", fields=["[].id"], when="sonarr_v3"),
    ]),
    Feature("Library (hide what you own)", [
        Check("Series list", "GET", "/api/v3/series", fields=["[].id", "[].tvdbId", "[].monitored", "[].title"],
              optional=["[].statistics", "[].images"]),
    ]),
    Feature("Title details and filters", [
        Check("Series lookup", "GET", "/api/v3/series/lookup", params={"term": f"tvdb:{PROBE_TVDB_ID}"},
              fields=["[].title", "[].tvdbId", "[].year", "[].seasons"],
              optional=["[].genres", "[].network", "[].runtime", "[].ratings", "[].images", "[].status",
                        "[].originalLanguage", "[].certification"]),
    ]),
    Feature("Discover and search", [
        Check("Title search", "GET", "/api/v3/series/lookup", params={"term": "twilight"},
              optional=["[].tvdbId", "[].title"]),
    ]),
    Feature("Download progress", [
        Check("Queue", "GET", "/api/v3/queue/details", params={"includeEpisode": "false", "includeSeries": "false"},
              fields=["[].id", "[].seriesId", "[].size", "[].sizeleft", "[].status"],
              optional=["[].episodeId", "[].trackedDownloadState", "[].trackedDownloadStatus", "[].errorMessage"]),
        Check("Episodes", "GET", "/api/v3/episode", spec_only=True, query_params=["seriesId"]),
    ]),
    Feature("Adding shows", [
        Check("Add series", "POST", "/api/v3/series", write=True,
              request_fields=["tvdbId", "title", "qualityProfileId", "rootFolderPath", "monitored", "seasons",
                              "seasonFolder", "addOptions"]),
    ]),
    Feature("Retry", [
        Check("Search command", "POST", "/api/v3/command", spec_only=True, request_fields=["name"]),
        Check("Remove failed download", "DELETE", "/api/v3/queue/{id}", spec_only=True,
              query_params=["removeFromClient", "blocklist"]),
    ]),
]


# ---------------------------------------------------------------- field helpers
def _has(data: Any, path: str) -> bool | None:
    """True/False if the dotted path exists; None when it can't be judged (empty list)."""
    node = data
    for part in path.split("."):
        if part.endswith("[]") or part == "[]":
            key = part[:-2]
            if key:
                if not isinstance(node, dict) or key not in node:
                    return False
                node = node[key]
            if not isinstance(node, list):
                return False
            if not node:
                return None
            node = node[0]
        else:
            if not isinstance(node, dict) or part not in node:
                return False
            node = node[part]
    return True


def _resolve(spec: dict, schema: dict | None) -> dict:
    seen = 0
    while schema and "$ref" in schema and seen < 10:
        name = schema["$ref"].rsplit("/", 1)[-1]
        schema = spec.get("components", {}).get("schemas", {}).get(name, {})
        seen += 1
    if schema and schema.get("type") == "array":
        return _resolve(spec, schema.get("items"))
    return schema or {}


def _spec_op(spec: dict, path: str, method: str) -> dict | None:
    return (spec.get("paths", {}).get(path) or {}).get(method.lower())


def _spec_props(spec: dict, op: dict, kind: str) -> set[str]:
    if kind == "request":
        content = (op.get("requestBody") or {}).get("content") or {}
    else:
        content = ((op.get("responses") or {}).get("200") or {}).get("content") or {}
    for media in ("application/json", "text/json", "text/plain"):
        if media in content:
            return set(_resolve(spec, content[media].get("schema")).get("properties", {}).keys())
    return set()


def _worst(statuses: list[str]) -> str:
    return max(statuses, key=lambda s: RANK.get(s, 0)) if statuses else "ok"


# ----------------------------------------------------------------- the checker
class CompatChecker:
    def __init__(self, db, transport: httpx.AsyncBaseTransport | None = None):
        self.db = db
        self.transport = transport

    async def fetch_spec(self, service: str, client: ArrClient) -> tuple[dict | None, str | None]:
        cache = self.db.get_setting(f"compat_spec:{service}") or {}
        if cache.get("version") == client.version and time.time() - cache.get("at", 0) < SPEC_TTL:
            return cache.get("spec"), cache.get("source")
        spec, source = None, None
        for path in LOCAL_SPEC_PATHS:   # the server's own spec matches its exact version
            status, data, _ = await client.probe("GET", path)
            if status == 200 and isinstance(data, dict) and "paths" in data:
                spec, source = data, f"{service} server ({path})"
                break
        if spec is None:
            env = os.environ.get(f"ARR_SPEC_URL_{service.upper()}")
            urls = [env] if env else UPSTREAM_SPECS[service]
            async with httpx.AsyncClient(timeout=8, transport=self.transport, follow_redirects=True) as http:
                for url in urls:     # no API key is ever sent here
                    try:
                        r = await http.get(url)
                        if r.status_code == 200 and "paths" in (data := r.json()):
                            spec, source = data, f"upstream ({url.split('/')[3]} {url.split('/')[5]} branch)"
                            break
                    except (httpx.HTTPError, ValueError):
                        continue
        if spec:
            self.db.set_setting(f"compat_spec:{service}",
                                {"version": client.version, "at": time.time(), "spec": spec, "source": source})
        return spec, source

    async def run(self, service: str, client: ArrClient) -> dict:
        started = time.time()
        status, data, err = await client.probe("GET", "/api/v3/system/status")
        version = (data or {}).get("version") if status == 200 else None
        if version:
            client.version = version
        major = int(version.split(".")[0]) if version else 0
        spec, spec_source = await self.fetch_spec(service, client) if status == 200 else (None, None)
        if spec and spec_source and spec_source.startswith("upstream"):
            spec_note = "Compared with the latest upstream spec, which may be newer than your server."
        elif spec:
            spec_note = "Compared with the spec served by your server."
        else:
            spec_note = "No OpenAPI spec reachable; write endpoints were checked with safe validation probes only."

        features = []
        for feat in (RADARR if service == "radarr" else SONARR):
            results = []
            for chk in feat.checks:
                if chk.when == "sonarr_v3" and major >= 4:
                    continue
                results.append(await self._check(client, chk, spec))
            features.append({"feature": feat.name, "status": _worst([r["status"] for r in results]), "checks": results})

        report = {
            "service": service, "checked_at": started, "duration_ms": round((time.time() - started) * 1000),
            "version": version, "reachable": status is not None and err is None,
            "status": _worst([f["status"] for f in features]),
            "spec_source": spec_source, "spec_note": spec_note, "features": features,
        }
        previous = self.db.get_setting(f"compat:{service}")
        report["previous_version"] = (previous or {}).get("version")
        report["previous_checked_at"] = (previous or {}).get("checked_at")
        report["changes"] = self._diff(previous, report)
        self.db.set_setting(f"compat:{service}", report)
        log.info(msg("log.compat_done", service=service, version=version, status=report["status"], changes=len(report["changes"])))
        return report

    async def _check(self, client: ArrClient, chk: Check, spec: dict | None) -> dict:
        out = {"name": chk.name, "method": chk.method, "path": chk.path, "status": "ok", "details": []}
        op = _spec_op(spec, chk.path, chk.method) if spec else None

        def note(level: str, text: str):
            out["details"].append(text)
            if RANK[level] > RANK[out["status"]]:
                out["status"] = level

        # Spec checks (when we have one)
        if spec is not None:
            if op is None:
                note("error" if chk.spec_only else "warning", f"{chk.method} {chk.path} is not in the API spec.")
            else:
                if chk.request_fields:
                    props = _spec_props(spec, op, "request")
                    missing = [f for f in chk.request_fields if props and f not in props]
                    if missing:
                        note("error", "Request no longer accepts: " + ", ".join(missing))
                if chk.query_params:
                    names = {p.get("name") for p in op.get("parameters") or []}
                    missing = [q for q in chk.query_params if q not in names]
                    if missing:
                        note("warning", "Query parameters not in spec: " + ", ".join(missing))
        if chk.spec_only:
            if spec is None:
                note("info", "Can't be checked without changing data, and no API spec was reachable.")
            elif op is not None and out["status"] == "ok":
                out["details"].append("Confirmed in API spec.")
            return out

        # Live probe
        if chk.write:
            status, data, err = await client.probe(chk.method, chk.path, body={})
            if err:
                note("error", err)
            elif status in (404, 405):
                note("error", f"Endpoint missing (HTTP {status}).")
            elif status == 401:
                note("error", "API key rejected.")
            elif status in (400, 422):
                out["details"].append("Endpoint present (validation probe, nothing was created).")
            else:
                note("warning", f"Unexpected HTTP {status} to a validation probe.")
            return out

        status, data, err = await client.probe(chk.method, chk.path, params=chk.params)
        if err:
            note("error", err)
            return out
        if status == 401:
            note("error", "API key rejected.")
            return out
        if status is not None and status >= 500:
            # Radarr/Sonarr answered with an error of its own (often "can't reach its metadata server"):
            # not an API change, so a warning that carries its message rather than a bare "HTTP 503".
            text = (data or {}).get("message") if isinstance(data, dict) else None
            note("warning", msg("compat.server_error", status=status, message=text or "-"))
            return out
        if status != 200:
            note("error", msg("compat.http_status", status=status,
                              message=(data or {}).get("message", "") if isinstance(data, dict) else ""))
            return out
        missing, unknown = [], []
        for f in chk.fields:
            has = _has(data, f)
            if has is False:
                missing.append(f)
            elif has is None:
                unknown.append(f)
        if missing:
            note("error", "Missing fields: " + ", ".join(m.replace("[].", "") for m in missing))
        if unknown:
            # Empty list: fall back to the spec's response schema if we have it.
            props = _spec_props(spec, op, "response") if spec and op else set()
            gone = [u for u in unknown if props and u.replace("[].", "").split(".")[0] not in props]
            if gone:
                note("error", "Not in response schema: " + ", ".join(g.replace("[].", "") for g in gone))
            elif props:
                out["details"].append("Empty right now; fields confirmed in API spec.")
            else:
                out["details"].append("Empty right now, so fields couldn't be checked.")
        opt_missing = [o for o in chk.optional if _has(data, o) is False]
        if opt_missing:
            note("info", "Optional fields not present: " + ", ".join(o.replace("[].", "") for o in opt_missing))
        return out

    @staticmethod
    def _diff(prev: dict | None, cur: dict) -> list[str]:
        if not prev:
            return []
        changes = []
        if prev.get("version") != cur.get("version"):
            changes.append(f"Version {prev.get('version') or '?'} → {cur.get('version') or '?'}")

        def flat(r):
            return {f"{f['feature']} › {c['name']}": c for f in r.get("features", []) for c in f["checks"]}

        a, b = flat(prev), flat(cur)
        for key in sorted(set(a) | set(b)):
            if key not in a:
                changes.append(f"{key}: newly checked ({b[key]['status']})")
            elif key not in b:
                changes.append(f"{key}: no longer checked")
            elif a[key]["status"] != b[key]["status"]:
                detail = "; ".join(b[key]["details"][:1])
                changes.append(f"{key}: {a[key]['status']} → {b[key]['status']}" + (f" ({detail})" if detail else ""))
        return changes
