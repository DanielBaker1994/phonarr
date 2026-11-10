"""Stateful in-memory Radarr/Sonarr (httpx.MockTransport).

Used by the tests, and by the opt-in local dev mode (DEV_FAKE_ARR=1) so the
iPhone UI can be exercised without real servers. With simulate=True every
add starts a download that advances on each queue poll, then imports;
"His Girl Friday" (TMDB 3085) always fails so the failure/retry UI can be seen.
Never enabled in production: the gateway logs a loud warning when it is on.

Both servers answer /api/v3/calendar like the real ones: library titles only, filtered by the start/end
instants, unmonitored ones only with unmonitored=true, and the series only with includeSeries=true.
With simulate=True the library is seeded with releases around today so the Calendar has something to show.
"""
from __future__ import annotations

import datetime as dt
import json

import httpx

API_KEYS = {"radarr.test": "radarr0123456789abcdef", "sonarr.test": "sonarr0123456789abcdef"}


class FakeArr:
    def __init__(self, sonarr_version: str = "4.0.10.2544", simulate: bool = False):
        self.sonarr_version = sonarr_version
        self.simulate = simulate
        self.fail_tmdb = {3085}
        self.movies: list[dict] = []
        self.series: list[dict] = []
        self.radarr_queue: list[dict] = []
        self.sonarr_queue: list[dict] = []
        self.episodes: list[dict] = []
        self.commands: list[dict] = []
        self.deleted_queue: list[tuple[int, dict]] = []
        self.posts: list[tuple[str, dict]] = []
        self.calls: list[str] = []
        self.down: set[str] = set()
        self.radarr_profiles = [{"id": 1, "name": "HD-1080p"}, {"id": 4, "name": "Any"}]
        self.sonarr_profiles = [{"id": 7, "name": "HD-720p"}]
        self.movie_lookup = {
            10331: {"tmdbId": 10331, "title": "Night of the Living Dead", "originalTitle": "Night of the Living Dead",
                    "year": 1968, "overview": "Zombies.", "genres": ["Horror", "Thriller"], "runtime": 96, "imdbId": "tt0063350",
                    "studio": "Image Ten", "originalLanguage": {"id": 1, "name": "English"}, "popularity": 15.2,
                    "ratings": {"imdb": {"votes": 140000, "value": 7.8}, "tmdb": {"votes": 2000, "value": 7.5}},
                    "keywords": ["zombie", "farmhouse", "siege"],
                    "images": [{"coverType": "poster", "remoteUrl": "https://image.tmdb.org/p/notld.jpg"}]},
            653: {"tmdbId": 653, "title": "Nosferatu", "originalTitle": "Nosferatu, eine Symphonie des Grauens",
                  "year": 1922, "overview": "Count Orlok.", "genres": ["Horror", "Fantasy"], "runtime": 94,
                  "studio": "Prana Film", "originalLanguage": {"id": 4, "name": "German"},
                  "ratings": {"imdb": {"votes": 110000, "value": 7.8}}, "collection": {"title": "Nosferatu Collection"},
                  "images": [{"coverType": "poster", "remoteUrl": "https://image.tmdb.org/p/nos.jpg"}]},
            961: {"tmdbId": 961, "title": "The General", "year": 1926, "genres": ["Comedy", "Action"], "runtime": 79,
                  "overview": "A train engineer pursues the locomotive stolen by Union spies.", "images": []},
            3085: {"tmdbId": 3085, "title": "His Girl Friday", "year": 1940, "genres": ["Comedy", "Romance"],
                   "runtime": 92, "overview": "A newspaper editor tries to win back his ex-wife and star reporter.", "images": []},
            4808: {"tmdbId": 4808, "title": "Charade", "year": 1963, "genres": ["Mystery", "Romance"], "runtime": 113,
                   "certification": "PG", "overview": "Romance and suspense in Paris.", "images": []},
            1010: {"tmdbId": 1010, "title": "Carnival of Souls", "year": 1962, "genres": ["Horror", "Mystery"],
                   "runtime": 78, "studio": "Harcourt Productions", "originalLanguage": {"id": 1, "name": "English"},
                   "ratings": {"imdb": {"votes": 30000, "value": 7.0}, "tmdb": {"votes": 600, "value": 6.9}},
                   "keywords": ["ghost", "organist", "small town"], "popularity": 9.1, "images": []},
            1011: {"tmdbId": 1011, "title": "Detour", "year": 1945, "genres": ["Crime", "Thriller"], "runtime": 68,
                   "studio": "PRC", "originalLanguage": {"id": 1, "name": "English"},
                   "ratings": {"tmdb": {"votes": 300, "value": 7.1}}, "keywords": ["film noir", "hitchhiker"],
                   "popularity": 6.0, "images": []},
            1012: {"tmdbId": 1012, "title": "The Little Shop of Horrors", "year": 1960,
                   "genres": ["Comedy", "Horror"], "runtime": 72, "studio": "The Filmgroup",
                   "originalLanguage": {"id": 1, "name": "English"}, "ratings": {"tmdb": {"votes": 400, "value": 6.2}},
                   "keywords": ["carnivorous plant"], "popularity": 7.5, "images": []},
            1013: {"tmdbId": 1013, "title": "Plan 9 from Outer Space", "year": 1957,
                   "genres": ["Horror", "Science Fiction"], "runtime": 79, "studio": "Reynolds Pictures",
                   "originalLanguage": {"id": 1, "name": "English"}, "ratings": {"tmdb": {"votes": 900, "value": 4.2}},
                   "keywords": ["ghoul", "alien"], "popularity": 8.0, "images": []},
            603: {"tmdbId": 603, "title": "The Matrix", "year": 1999, "genres": ["Action", "Science Fiction"],
                  "runtime": 136, "studio": "Warner Bros.", "images": [], "overview": "…"},
            999: {"tmdbId": 999, "title": "Some Other Film", "year": 2020, "genres": ["Drama"], "images": []},
        }
        self.series_lookup = {
            81189: {"tvdbId": 81189, "title": "Breaking Bad", "year": 2008, "genres": ["Drama"], "network": "AMC",
                    "seasons": [{"seasonNumber": 1, "monitored": False}], "images": []},
            70002: {"tvdbId": 70002, "title": "Haunted Anthology", "year": 1961, "genres": ["Horror", "Mystery"],
                    "network": "DuMont", "ratings": {"votes": 50, "value": 7.2}, "status": "ended", "runtime": 25,
                    "seasons": [{"seasonNumber": 1, "monitored": False}], "images": []},
            70001: {"tvdbId": 70001, "title": "Old Variety Show", "year": 1950, "genres": ["Comedy"],
                    "network": "DuMont", "ratings": {"votes": 20, "value": 6.5}, "status": "ended", "runtime": 30,
                    "seasons": [{"seasonNumber": 0, "monitored": False}, {"seasonNumber": 1, "monitored": False},
                                {"seasonNumber": 2, "monitored": False}],
                    "images": [{"coverType": "poster", "remoteUrl": "https://artworks.thetvdb.com/ops.jpg"}]},
        }
        # Radarr Discover feed (import lists + recommendations): TMDB IDs, recommendation flag
        self.discover_ids: list[tuple[int, bool]] = [(1010, True), (1011, True), (1012, False), (1013, True)] if simulate else []
        self._next_id = 100
        self.radarr_spec: dict | None = None   # tests can serve an OpenAPI spec at /docs/v3/openapi.json
        if simulate:
            self._seed_calendar()

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        self.calls.append(f"{host} {request.method} {request.url.path}")
        if host in self.down:
            raise httpx.ConnectError("[Errno 111] Connection refused", request=request)
        if host not in API_KEYS:
            raise httpx.ConnectError("[Errno -2] Name or service not known", request=request)
        if request.headers.get("x-api-key") != API_KEYS[host]:
            return httpx.Response(401, json={"error": "Unauthorized"})
        path = request.url.path
        # support a URL base such as /radarr
        for base in ("/radarr", "/sonarr"):
            if path.startswith(base + "/"):
                path = path[len(base):]
        body = json.loads(request.content) if request.content else None
        return (self._radarr if host.startswith("radarr") else self._sonarr)(request.method, path, request.url.params, body)

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _radarr(self, method, path, params, body):
        if path == "/api/v3/system/status":
            return httpx.Response(200, json={"appName": "Radarr", "version": getattr(self, "radarr_version", "5.14.0.9383")})
        if path == "/docs/v3/openapi.json" and self.radarr_spec:
            return httpx.Response(200, json=self.radarr_spec)
        if path == "/api/v3/qualityprofile":
            return httpx.Response(200, json=self.radarr_profiles)
        if path == "/api/v3/rootfolder":
            return httpx.Response(200, json=[{"path": "/movies", "freeSpace": 10**12, "accessible": True}])
        if path == "/api/v3/importlist/movie":
            owned = {m["tmdbId"] for m in self.movies}
            return httpx.Response(200, json=[{**self.movie_lookup[t], "isRecommendation": rec, "isExcluded": False,
                                              "isExisting": t in owned}
                                             for t, rec in self.discover_ids if t in self.movie_lookup])
        if path == "/api/v3/movie/lookup":
            term = params.get("term", "").casefold()
            if term.startswith("tmdb:"):
                m = self.movie_lookup.get(int(term[5:]))
                return httpx.Response(200, json=[m] if m else [])
            return httpx.Response(200, json=[m for m in self.movie_lookup.values() if term in m["title"].casefold()])
        if path == "/api/v3/movie/lookup/tmdb":
            m = self.movie_lookup.get(int(params["tmdbId"]))
            return httpx.Response(200, json=m) if m else httpx.Response(404, json={"message": "not found"})
        if path == "/api/v3/movie" and method == "GET":
            return httpx.Response(200, json=self.movies)
        if path == "/api/v3/movie" and method == "POST":
            if not body or "tmdbId" not in body:
                return httpx.Response(400, json=[{"propertyName": "TmdbId", "errorMessage": "'Tmdb Id' must not be empty."}])
            self.posts.append(("movie", body))
            if any(m["tmdbId"] == body["tmdbId"] for m in self.movies):
                return httpx.Response(400, json=[{"errorMessage": "This movie has already been added"}])
            movie = {**body, "id": self._id(), "hasFile": False}
            self.movies.append(movie)
            if self.simulate:
                self.radarr_queue.append(self._dl({"movieId": movie["id"]}, fail=body["tmdbId"] in self.fail_tmdb))
            return httpx.Response(201, json=movie)
        if path == "/api/v3/calendar":
            start, end, unmonitored = _window(params)
            return httpx.Response(200, json=[
                m for m in self.movies if (unmonitored or m.get("monitored"))
                and any(start <= t <= end for t in (_instant(m.get(f)) for f in ("inCinemas", "digitalRelease", "physicalRelease")) if t)])
        if path == "/api/v3/queue/details":
            if self.simulate:
                self._tick(self.radarr_queue, "radarr")
            return httpx.Response(200, json=self.radarr_queue)
        if path.startswith("/api/v3/queue/") and method == "DELETE":
            qid = int(path.rsplit("/", 1)[1])
            self.deleted_queue.append((qid, dict(params)))
            self.radarr_queue = [q for q in self.radarr_queue if q["id"] != qid]
            return httpx.Response(200)
        if path == "/api/v3/command":
            self.commands.append(body)
            if self.simulate and body.get("name") == "MoviesSearch":
                for mid in body.get("movieIds", []):
                    self.radarr_queue.append(self._dl({"movieId": mid}))  # a retry succeeds
            return httpx.Response(201, json={"id": 1, **body})
        return httpx.Response(404)

    def _sonarr(self, method, path, params, body):
        if path == "/api/v3/system/status":
            return httpx.Response(200, json={"appName": "Sonarr", "version": self.sonarr_version})
        if path == "/api/v3/qualityprofile":
            return httpx.Response(200, json=self.sonarr_profiles)
        if path == "/api/v3/languageprofile":
            return httpx.Response(200, json=[{"id": 1, "name": "English"}])
        if path == "/api/v3/rootfolder":
            return httpx.Response(200, json=[{"path": "/tv"}])
        if path == "/api/v3/series/lookup":
            term = params["term"]
            if term.startswith("tvdb:"):
                s = self.series_lookup.get(int(term.split(":")[1]))
                return httpx.Response(200, json=[s] if s else [])
            return httpx.Response(200, json=[s for s in self.series_lookup.values() if term.casefold() in s["title"].casefold()])
        if path == "/api/v3/series" and method == "GET":
            return httpx.Response(200, json=self.series)
        if path == "/api/v3/series" and method == "POST":
            if not body or "tvdbId" not in body:
                return httpx.Response(400, json=[{"propertyName": "TvdbId", "errorMessage": "'Tvdb Id' must not be empty."}])
            self.posts.append(("series", body))
            s = {**body, "id": self._id(), "statistics": {"episodeFileCount": 0, "episodeCount": 4}}
            self.series.append(s)
            if self.simulate:
                for n in range(1, 5):
                    eid = self._id()
                    self.episodes.append({"id": eid, "seriesId": s["id"], "seasonNumber": 1, "episodeNumber": n,
                                          "title": f"Episode {n}", "hasFile": False, "monitored": True})
                    self.sonarr_queue.append(self._dl({"seriesId": s["id"], "episodeId": eid}, size=4e8, speed=0.12 + n * 0.03))
            return httpx.Response(201, json=s)
        if path == "/api/v3/episode":
            return httpx.Response(200, json=[e for e in self.episodes if e["seriesId"] == int(params["seriesId"])])
        if path == "/api/v3/calendar":
            start, end, unmonitored = _window(params)
            shows = {s["id"]: s for s in self.series}
            out = []
            for e in self.episodes:
                when = _instant(e.get("airDateUtc") or e.get("airDate"))
                show = shows.get(e["seriesId"], {})
                if not when or not start <= when <= end:
                    continue
                if not unmonitored and not (e.get("monitored") and show.get("monitored")):
                    continue
                out.append({**e, "series": show} if params.get("includeSeries") == "true" else dict(e))
            return httpx.Response(200, json=out)
        if path == "/api/v3/queue/details":
            if self.simulate:
                self._tick(self.sonarr_queue, "sonarr")
            return httpx.Response(200, json=self.sonarr_queue)
        if path.startswith("/api/v3/queue/") and method == "DELETE":
            qid = int(path.rsplit("/", 1)[1])
            self.deleted_queue.append((qid, dict(params)))
            self.sonarr_queue = [q for q in self.sonarr_queue if q["id"] != qid]
            return httpx.Response(200)
        if path == "/api/v3/command":
            self.commands.append(body)
            return httpx.Response(201, json={"id": 2, **body})
        return httpx.Response(404)

    # ---- calendar seed (dev mode only) --------------------------------------
    def _seed_calendar(self) -> None:
        """Films and shows already in the library with releases around today: every release kind, a weekly
        show at 01:30 UTC (the evening before in the Americas), a season dropped at once, a special,
        an unmonitored show and a season finale. Names are made up."""
        today = dt.datetime.now(dt.timezone.utc).date()
        day = lambda n: (today + dt.timedelta(days=n)).isoformat() + "T00:00:00Z"            # noqa: E731
        at = lambda n, hm: (today + dt.timedelta(days=n)).isoformat() + f"T{hm}:00Z"         # noqa: E731
        films = [
            (2101, "The Lighthouse Keeper", True, False, {"inCinemas": day(3), "digitalRelease": day(24), "physicalRelease": day(40)}),
            (2102, "Midnight Ferry", True, True, {"inCinemas": day(-60), "digitalRelease": day(-2)}),
            (2103, "Paper Moons", False, False, {"physicalRelease": day(9)}),
            (2104, "Northern Lights", True, False, {"inCinemas": day(0), "digitalRelease": day(30)}),
        ]
        for tmdb, title, monitored, has_file, dates in films:
            self.movie_lookup[tmdb] = {"tmdbId": tmdb, "title": title, "year": today.year, "genres": ["Drama"],
                                       "runtime": 104, "overview": "A made-up film for the dev calendar.", "images": []}
            self.movies.append({**self.movie_lookup[tmdb], "id": self._id(), "monitored": monitored,
                                "hasFile": has_file, "qualityProfileId": 1, **dates})
        shows = [
            (72001, "Harbour Lights", True, [(1, n, at(-14 + 7 * n, "01:30"), None) for n in range(1, 7)]),
            (72002, "Glass Houses", True, [(2, n, at(5, "08:00"), None) for n in range(1, 9)]
             + [(0, 1, at(2, "20:00"), None)]),
            (72003, "Quiet Fields", False, [(3, 4, at(1, "21:00"), None)]),
            (72004, "Late Edition", True, [(5, 9, at(5, "02:00"), None), (5, 10, at(12, "02:00"), "season")]),
        ]
        for tvdb, title, monitored, episodes in shows:
            self.series_lookup[tvdb] = {"tvdbId": tvdb, "title": title, "year": today.year, "genres": ["Drama"],
                                        "network": "Dev TV", "status": "continuing", "runtime": 45,
                                        "seasons": [{"seasonNumber": 1, "monitored": False}], "images": []}
            show = {**self.series_lookup[tvdb], "id": self._id(), "monitored": monitored, "qualityProfileId": 7,
                    "statistics": {"episodeFileCount": 0, "episodeCount": len(episodes)}}
            self.series.append(show)
            for season, number, air, finale in episodes:
                aired = air < at(0, "00:00")
                ep = {"id": self._id(), "seriesId": show["id"], "seasonNumber": season, "episodeNumber": number,
                      "title": "Behind the Glass" if season == 0 else f"Chapter {number}", "airDateUtc": air,
                      "airDate": air[:10], "hasFile": aired, "monitored": monitored}
                if finale:
                    ep["finaleType"] = finale
                self.episodes.append(ep)
                show["statistics"]["episodeFileCount"] += int(aired)

    # ---- download simulation (dev mode only) -------------------------------
    def _dl(self, ids: dict, fail: bool = False, size: float = 1.5e9, speed: float = 0.08) -> dict:
        return {"id": self._id(), **ids, "size": size, "sizeleft": size, "status": "downloading",
                "trackedDownloadStatus": "ok", "trackedDownloadState": "downloading", "_speed": speed,
                "_fail": fail, "title": "simulated.release"}

    def _tick(self, queue: list[dict], app: str) -> None:
        for q in list(queue):
            if q["trackedDownloadState"] == "importPending":
                queue.remove(q)
                if app == "radarr":
                    for m in self.movies:
                        if m["id"] == q["movieId"]:
                            m["hasFile"] = True
                else:
                    for e in self.episodes:
                        if e["id"] == q["episodeId"]:
                            e["hasFile"] = True
                    for s in self.series:
                        if s["id"] == q["seriesId"]:
                            s["statistics"]["episodeFileCount"] += 1
                continue
            if q["status"] == "failed":
                continue
            q["sizeleft"] = max(0.0, q["sizeleft"] - q["size"] * q["_speed"])
            if q["_fail"] and q["sizeleft"] < q["size"] * 0.6:
                q.update(status="failed", trackedDownloadStatus="error", trackedDownloadState="failed",
                         errorMessage="Simulated: download client reported the release as corrupt")
            elif q["sizeleft"] == 0:
                q.update(status="completed", trackedDownloadState="importPending")


def _instant(value) -> dt.datetime | None:
    """A Radarr/Sonarr date or date-time as an aware UTC instant (a bare date is midnight UTC)."""
    if not value:
        return None
    try:
        when = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)


def _window(params) -> tuple[dt.datetime, dt.datetime, bool]:
    today = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = _instant(params.get("start")) or today
    end = _instant(params.get("end")) or today + dt.timedelta(days=2)
    return start, end, params.get("unmonitored") == "true"
