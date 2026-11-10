"""Known bugs from similar apps (docs/KNOWN_BUGS_PRD.md, sections A to C).

Each test was written and run before any fix; the PRD records whether it failed or passed first time.
"""
import io
import logging
import time as real_time
from types import SimpleNamespace

import httpx

from app import service
from app.logs import RedactingFormatter, RequestIdFilter, redact

from .test_gateway import AUTH, client, configure, fake  # noqa: F401  (pytest fixtures)

RADARR_KEY = "radarr0123456789abcdef"


def _searches(fake):
    return [c for c in fake.commands if c["name"] == "MoviesSearch"]


# ------------------------------------------------------------------ A. connectivity and sending
def test_retry_of_a_title_the_gateway_never_saw_adds_it(client, fake):
    """KB-01 (gateway side): the phone lost the connection before POST /v1/requests reached the gateway,
    showed Failed, and the next tap went to Retry. Retry must add the title with the defaults, not 404."""
    configure(client)
    r = client.post("/v1/requests/movie/961/retry", headers=AUTH)
    assert r.status_code == 200, r.text
    assert [b["tmdbId"] for kind, b in fake.posts if kind == "movie"] == [961]
    assert r.json()["arr_id"] == fake.movies[0]["id"]
    assert fake.movies[0]["qualityProfileId"] == 1 and fake.movies[0]["rootFolderPath"] == "/movies"
    # Still nothing to retry when the title doesn't exist at all.
    assert client.post("/v1/requests/movie/12345/retry", headers=AUTH).status_code == 404


def test_add_that_timed_out_but_landed_is_linked(client, fake):
    """KB-02: Radarr/Sonarr created the record but the gateway's POST timed out. The gateway re-reads the
    library once and links the record, instead of reporting a failure for a title that is there."""
    configure(client)
    for kind, ext, records in (("movie", 10331, fake.movies), ("series", 70001, fake.series)):
        fake.timeout_after_post.add(kind)
        r = client.post("/v1/requests", json={"type": kind, "id": ext}, headers=AUTH)
        assert r.status_code == 200, r.text
        assert len(records) == 1 and r.json()["arr_id"] == records[0]["id"]
        st = client.get("/v1/status", headers=AUTH).json()["items"][f"{kind}:{ext}"]
        assert st["state"] != "failed", st


def test_already_added_by_someone_else_is_linked(client, fake):
    """KB-03: another client adds the title between our library check and our POST. Radarr/Sonarr answer 400
    with a validation array ("This movie has already been added"); the gateway links the existing record."""
    configure(client)
    for kind, ext, records in (("movie", 10331, fake.movies), ("series", 70001, fake.series)):
        fake.added_elsewhere.add(kind)
        r = client.post("/v1/requests", json={"type": kind, "id": ext}, headers=AUTH)
        assert r.status_code == 200, r.text
        assert len(records) == 1 and r.json()["arr_id"] == records[0]["id"]
        st = client.get("/v1/status", headers=AUTH).json()["items"][f"{kind}:{ext}"]
        assert st["state"] != "failed", st


def test_search_does_not_cache_or_crash_on_invalid_lookup(client, fake):
    """KB-06: a lookup that answers an error object or null (Radarr starting up) is an error, never a cached
    empty result, and never a 500."""
    configure(client)
    for app, kind, term, ext in (("radarr", "movie", "matrix", 603), ("sonarr", "series", "haunted", 70002)):
        for bad in ({"message": "Database is locked"}, None):
            fake.lookup_answer[app] = bad
            r = client.get(f"/v1/discover?type={kind}&q={term}", headers=AUTH)
            assert r.status_code == 200, r.text
            assert r.json()["error"] is not None and ext not in [i["id"] for i in r.json()["items"]]
        del fake.lookup_answer[app]
        r = client.get(f"/v1/discover?type={kind}&q={term}", headers=AUTH).json()
        assert ext in [i["id"] for i in r["items"]], "the bad answer was cached"


def test_one_empty_library_answer_does_not_orphan_requests(client, fake):
    """KB-07: Radarr answers the library call with [] once (restarting, database locked). Requested titles keep
    their status; only two refreshes in a row agreeing it's gone make it "No longer in Radarr"."""
    configure(client)
    client.post("/v1/requests", json={"type": "movie", "id": 10331}, headers=AUTH)
    gw = client.app.state.gateway
    status = lambda: client.get("/v1/status", headers=AUTH).json()["items"]["movie:10331"]  # noqa: E731
    before = status()
    assert before["state"] == "searching"

    fake.empty_library["radarr"] = 1
    client.portal.call(gw.refresh_status, True)
    assert status()["state"] == before["state"], status()
    # Something went missing: the next ordinary cycle reads the library again instead of using the cache.
    fake.calls.clear()
    client.portal.call(gw.refresh_status)
    assert "radarr.test GET /api/v3/movie" in fake.calls
    assert status()["state"] == before["state"]

    fake.empty_library["radarr"] = 2
    client.portal.call(gw.refresh_status, True)
    assert status()["state"] == before["state"]
    client.portal.call(gw.refresh_status)
    st = status()
    assert st["state"] == "failed" and "No longer in Radarr" in st["message"]


# ------------------------------------------------------------------ B. time
def test_clock_going_backwards_does_not_freeze_retry_or_caches(client, fake, monkeypatch):
    """KB-10: the wall clock jumps back an hour (VM resume, NTP). Durations use a monotonic clock, so the Retry
    cooldown still ends and caches still expire."""
    configure(client)
    gw = client.app.state.gateway
    client.post("/v1/requests", json={"type": "movie", "id": 10331}, headers=AUTH)
    assert client.post("/v1/requests/movie/10331/retry", headers=AUTH).status_code == 200
    client.get("/v1/discover?type=movie&q=matrix", headers=AUTH)
    assert len(_searches(fake)) == 1

    passed = {"s": 0.0}
    clock = SimpleNamespace(time=lambda: real_time.time() - 3600 + passed["s"],
                            monotonic=lambda: real_time.monotonic() + passed["s"],
                            perf_counter=real_time.perf_counter, strftime=real_time.strftime)
    monkeypatch.setattr(service, "time", clock)

    passed["s"] = service.RETRY_COOLDOWN + 1          # the cooldown really is over
    assert client.post("/v1/requests/movie/10331/retry", headers=AUTH).status_code == 200
    assert len(_searches(fake)) == 2, "Retry refused: the cooldown is measured on the wall clock"

    passed["s"] += max(service.LIBRARY_TTL, service.SEARCH_TTL) + 1
    fake.calls.clear()
    client.portal.call(gw.library, "radarr")
    client.get("/v1/discover?type=movie&q=matrix", headers=AUTH)
    assert "radarr.test GET /api/v3/movie" in fake.calls, "library cache never expired"
    assert "radarr.test GET /api/v3/movie/lookup" in fake.calls, "search cache never expired"


# ------------------------------------------------------------------ C. data and decoding
def test_nulls_and_unknown_values_do_not_break_normalising(client, fake):
    """KB-20: null where a list or object is expected, missing counts, and queue states Phonarr has never seen.
    Discover, status and activity keep working and show every title."""
    configure(client)
    fake.movies = [
        {"id": 1, "tmdbId": 5001, "title": "All nulls", "year": None, "images": None, "genres": None, "ratings": None,
         "collection": None, "keywords": None, "originalLanguage": None, "monitored": True, "hasFile": False},
        {"id": 2, "tmdbId": 5002, "title": "Odd ratings", "ratings": {"imdb": None, "tmdb": {"value": None}},
         "images": [{"coverType": "poster", "remoteUrl": None}], "monitored": True, "hasFile": False},
    ]
    fake.series = [
        {"id": 3, "tvdbId": 6001, "title": "Null show", "seasons": None, "statistics": None, "ratings": None,
         "images": None, "genres": None, "monitored": True},
        {"id": 4, "tvdbId": 6002, "title": "Null counts", "seasons": [{"seasonNumber": 1}],
         "statistics": {"episodeFileCount": None, "episodeCount": None}, "monitored": True},
    ]
    fake.radarr_queue = [{"id": 9, "movieId": 1, "status": "someFutureStatus", "trackedDownloadState": "brandNewState",
                          "trackedDownloadStatus": None, "statusMessages": None, "size": None, "sizeleft": None}]
    fake.sonarr_queue = [{"id": 10, "seriesId": 3, "status": None, "trackedDownloadState": None,
                          "statusMessages": [{"title": "x", "messages": None}]}]
    gw = client.app.state.gateway
    client.portal.call(gw.refresh_status, True)
    items = client.get("/v1/status", headers=AUTH).json()["items"]
    assert {"movie:5001", "movie:5002", "series:6001", "series:6002"} <= set(items)
    assert items["movie:5001"]["state"] == "queued"
    movies = client.get("/v1/discover?type=movie&include_tracked=true", headers=AUTH)
    series = client.get("/v1/discover?type=series&include_tracked=true", headers=AUTH)
    assert movies.status_code == 200 and series.status_code == 200
    assert {5001, 5002} <= {i["id"] for i in movies.json()["items"]}
    assert {6001, 6002} <= {i["id"] for i in series.json()["items"]}
    assert client.get("/v1/activity", headers=AUTH).status_code == 200


def test_specials_never_count_as_first_or_latest_season(client, fake):
    """KB-21: season 0 (specials) is never the "first" or "latest" season, wherever it is in the list."""
    configure(client)
    fake.series_lookup[70003] = {"tvdbId": 70003, "title": "Specials Last", "year": 1970, "images": [],
                                 "seasons": [{"seasonNumber": 1}, {"seasonNumber": 2}, {"seasonNumber": 0}]}
    for ext, mode, want in ((70001, "first", {1}), (70003, "latest", {2})):
        r = client.post("/v1/requests", json={"type": "series", "id": ext, "season_monitor": mode}, headers=AUTH)
        assert r.status_code == 200, r.text
        body = next(b for kind, b in fake.posts if kind == "series" and b["tvdbId"] == ext)
        assert {s["seasonNumber"] for s in body["seasons"] if s["monitored"]} == want
    assert client.get("/v1/titles/series/70003", headers=AUTH).json()["season_count"] == 2


def test_non_json_answer_is_logged_with_a_snippet(client, fake, caplog):
    """KB-22: when Radarr answers with something that isn't JSON (a login page from a proxy), the log names the
    call, the status and the start of the body, redacted, so a debug report is enough to diagnose it."""
    configure(client)
    gw = client.app.state.gateway
    fake.raw_answers["radarr /api/v3/queue/details"] = (
        200, f"<html><title>Sign in</title><a href='/login?apikey={RADARR_KEY}'>proxy</a></html>")
    with caplog.at_level(logging.INFO):
        client.portal.call(gw.refresh_status)
    fmt = RedactingFormatter("%(message)s")
    lines = [fmt.format(r) for r in caplog.records if r.levelno >= logging.WARNING]
    hits = [line for line in lines if "/api/v3/queue/details" in line and "200" in line and "<title>Sign in" in line]
    assert hits, lines
    assert all(RADARR_KEY not in line for line in hits)
    assert all(RADARR_KEY not in r.getMessage() for r in caplog.records), "snippet must be redacted at source"


def test_background_errors_never_log_keys(client, fake):
    """KB-23: an Arr error message that contains a URL with apikey= (an indexer's key, or Radarr's own) must
    never reach a log line from the background refresh."""
    configure(client)
    gw = client.app.state.gateway
    orig = fake._radarr

    def failing(method, path, params, body):
        if path == "/api/v3/queue/details":
            return httpx.Response(500, json={"message": "Invalid response from http://indexer.example/api?t=search"
                                                        f"&apikey=unregistered12345678 (own key {RADARR_KEY})"})
        return orig(method, path, params, body)
    fake._radarr = failing

    out = io.StringIO()
    handler = logging.StreamHandler(out)
    handler.setFormatter(RedactingFormatter(service.cfg("logging.format")))
    handler.addFilter(RequestIdFilter())
    root = logging.getLogger()
    root.addHandler(handler)
    old = root.level
    root.setLevel(logging.INFO)
    try:
        client.portal.call(gw.refresh_status)
        client.portal.call(gw.check_reachability)
        client.portal.call(gw.refresh_status)
    finally:
        root.removeHandler(handler)
        root.setLevel(old)
    text = out.getvalue()
    assert "Invalid response from" in text
    assert "unregistered12345678" not in text and RADARR_KEY not in text
    assert "unregistered12345678" not in redact(str(gw.snapshot))
