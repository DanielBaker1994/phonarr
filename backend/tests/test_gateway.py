import asyncio
import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.service import classify_queue_item

from app.devfake import FakeArr

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

# Optional titles list: IDs only.
TITLES = {"movies": [10331, {"tmdb_id": 653}, 999, "junk"], "series": [{"tvdb_id": 70001}]}


@pytest.fixture
def fake():
    return FakeArr()


@pytest.fixture
def client(tmp_path, fake):
    (tmp_path / "titles.json").write_text(json.dumps(TITLES))
    app = create_app(data_dir=tmp_path, transport=fake.transport(), run_poller=False, token=TOKEN, output_dir=tmp_path)
    with TestClient(app) as c:
        yield c


def configure(c):
    r = c.put("/v1/config/radarr", json={"base_url": "http://radarr.test:7878/", "api_key": "radarr0123456789abcdef"}, headers=AUTH)
    assert r.status_code == 200, r.text
    r = c.put("/v1/config/sonarr", json={"base_url": "https://sonarr.test/sonarr", "api_key": "sonarr0123456789abcdef"}, headers=AUTH)
    assert r.status_code == 200, r.text
    c.put("/v1/defaults", json={"radarr_profile_id": 1}, headers=AUTH)


# ------------------------------------------------------------------- auth/config
def test_requires_token(client):
    assert client.get("/v1/config").status_code == 401
    assert client.get("/v1/config", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/v1/health").status_code == 200


def test_connection_errors_are_actionable(client):
    r = client.post("/v1/config/radarr/test", json={"base_url": "http://radarr.test:7878", "api_key": "bad"}, headers=AUTH)
    assert r.status_code == 422 and "API key" in r.json()["error"]["message"]
    r = client.post("/v1/config/radarr/test", json={"base_url": "http://box.tail1234.ts.net", "api_key": "x"}, headers=AUTH)
    assert "Tailscale" in r.json()["error"]["message"]
    r = client.post("/v1/config/radarr/test", json={"base_url": "http://sonarr.test", "api_key": "sonarr0123456789abcdef"}, headers=AUTH)
    assert "not Radarr" in r.json()["error"]["message"]
    r = client.post("/v1/config/radarr/test", json={"base_url": "ftp://x", "api_key": "x"}, headers=AUTH)
    assert r.status_code == 422


def test_config_never_returns_api_keys(client):
    configure(client)
    body = client.get("/v1/config", headers=AUTH).text
    assert "radarr0123456789abcdef" not in body and "sonarr0123456789abcdef" not in body
    cfg = client.get("/v1/config", headers=AUTH).json()
    assert cfg["radarr"]["api_key_set"] and cfg["sonarr"]["base_url"] == "https://sonarr.test/sonarr"
    # Change URL without re-entering the key: the saved key is reused.
    r = client.put("/v1/config/radarr", json={"base_url": "http://radarr.test/radarr"}, headers=AUTH)
    assert r.status_code == 200


def test_options_lists_profiles(client):
    configure(client)
    opts = client.get("/v1/options", headers=AUTH).json()
    assert [p["name"] for p in opts["radarr"]["quality_profiles"]] == ["HD-1080p", "Any"]
    assert opts["defaults"]["radarr_profile_id"] == 1


# ------------------------------------------------------------------- discovery
def test_discovery_shows_titles_list(client):
    configure(client)
    items = client.get("/v1/discover?type=movie", headers=AUTH).json()["items"]
    assert {i["id"] for i in items} == {10331, 653, 999}       # bad entries in the list are skipped
    assert all(i["source"] == "list" for i in items)


def test_discovery_includes_radarr_discover_and_hides_excluded(client, fake):
    configure(client)
    fake.discover_ids = [(1010, True), (1011, False)]
    orig = fake._radarr

    def patched(method, path, params, body):
        r = orig(method, path, params, body)
        if path == "/api/v3/importlist/movie":
            import httpx
            data = r.json() + [{**fake.movie_lookup[1012], "isExcluded": True}]
            return httpx.Response(200, json=data)
        return r
    fake._radarr = patched
    by_id = {i["id"]: i for i in client.get("/v1/discover?type=movie", headers=AUTH).json()["items"]}
    assert by_id[1010]["source"] == "recommended" and by_id[1011]["source"] == "import_list"
    assert 1012 not in by_id
    # metadata from the feed is reused: opening the title needs no extra lookup
    fake.calls.clear()
    assert client.get("/v1/titles/movie/1010", headers=AUTH).status_code == 200
    assert not any("lookup" in c for c in fake.calls)


def test_search_finds_titles_outside_the_list(client, fake):
    configure(client)
    r = client.get("/v1/discover?type=movie&q=matrix", headers=AUTH).json()
    assert r["searched"] is True and [(i["id"], i["source"]) for i in r["items"]] == [(603, "search")]
    r = client.get("/v1/discover?type=movie&q=night", headers=AUTH).json()
    assert [i["id"] for i in r["items"]] == [10331]               # list title not duplicated by search
    tv = client.get("/v1/discover?type=series&q=haunted", headers=AUTH).json()
    assert [i["id"] for i in tv["items"]] == [70002]
    assert client.post("/v1/requests", json={"type": "movie", "id": 603}, headers=AUTH).status_code == 200


def test_unknown_title_is_404_and_adds_nothing(client, fake):
    configure(client)
    r = client.post("/v1/requests", json={"type": "movie", "id": 12345}, headers=AUTH)
    assert r.status_code == 404 and "couldn't find" in r.json()["error"]["message"]
    assert client.get("/v1/titles/movie/12345", headers=AUTH).status_code == 404
    assert fake.posts == []


def test_request_plan_names_profile_and_folder(client, fake):
    configure(client)
    plan = client.get("/v1/titles/movie/10331", headers=AUTH).json()["request_plan"]
    assert plan["quality_profile"] == "HD-1080p" and plan["root_folder"] == "/movies" and plan["problem"] is None
    tv = client.get("/v1/request-plan?type=series", headers=AUTH).json()
    assert tv["quality_profile"] == "HD-720p" and tv["season_monitor"] == "all"   # the only profile
    client.put("/v1/defaults", json={"radarr_profile_id": None}, headers=AUTH)
    plan = client.get("/v1/discover?type=movie", headers=AUTH).json()["request_plan"]
    assert plan["problem"]["kind"] == "config" and "default" in plan["problem"]["message"]


# -------------------------------------------------------------- batch requests
def test_batch_request_adds_each_once_and_reports_per_item(client, fake):
    configure(client)
    client.post("/v1/requests", json={"type": "movie", "id": 10331}, headers=AUTH)
    r = client.post("/v1/requests/batch", headers=AUTH, json={
        "items": [{"type": "movie", "id": 10331}, {"type": "movie", "id": 653}, {"type": "movie", "id": 653},
                  {"type": "movie", "id": 12345}, {"type": "series", "id": 70001}],
        "movie": {"quality_profile_id": 4}, "series": {"season_monitor": "first"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["added"], body["already"], body["failed"]) == (2, 1, 1)
    res = {(x["type"], x["id"]): x for x in body["results"]}
    assert res[("movie", 12345)]["error"]["kind"] == "not_found"
    assert len(fake.movies) == 2 and len(fake.series) == 1
    assert [b["qualityProfileId"] for k, b in fake.posts if k == "movie"] == [1, 4]
    series_body = [b for k, b in fake.posts if k == "series"][0]
    assert [x["monitored"] for x in series_body["seasons"]] == [False, True, False]
    # batch overrides are one-off
    d = client.get("/v1/options", headers=AUTH).json()["defaults"]
    assert d["radarr_profile_id"] == 1 and d["season_monitor"] == "all"


def test_batch_validation(client):
    configure(client)
    assert client.post("/v1/requests/batch", json={"items": []}, headers=AUTH).status_code == 422
    assert client.post("/v1/requests/batch", json={"items": [{"type": "book", "id": 1}]}, headers=AUTH).status_code == 404
    many = [{"type": "movie", "id": i} for i in range(101)]
    assert client.post("/v1/requests/batch", json={"items": many}, headers=AUTH).status_code == 422


def test_season_override_is_one_off(client, fake):
    configure(client)
    r = client.post("/v1/requests", json={"type": "series", "id": 70001, "season_monitor": "future"}, headers=AUTH)
    assert r.status_code == 200
    assert fake.posts[0][1]["addOptions"]["monitor"] == "future"
    assert client.get("/v1/options", headers=AUTH).json()["defaults"]["season_monitor"] == "all"
    assert client.post("/v1/requests", json={"type": "series", "id": 70002, "season_monitor": "bogus"},
                       headers=AUTH).status_code == 422


# -------------------------------------------------------------------- requests
def test_one_tap_request_uses_defaults_and_hides_title(client, fake):
    configure(client)
    r = client.post("/v1/requests", json={"type": "movie", "id": 10331}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["status"]["state"] == "searching"
    kind, body = fake.posts[0]
    assert body["qualityProfileId"] == 1 and body["rootFolderPath"] == "/movies"
    assert body["addOptions"] == {"searchForMovie": True}
    assert 10331 not in ids(client.get("/v1/discover?type=movie", headers=AUTH))
    tracked = client.get("/v1/discover?type=movie&include_tracked=true", headers=AUTH).json()["items"]
    assert tracked[0]["status"]["state"] == "searching"


def test_override_does_not_change_default(client, fake):
    configure(client)
    r = client.post("/v1/requests", json={"type": "movie", "id": 10331, "quality_profile_id": 4}, headers=AUTH)
    assert r.status_code == 200
    assert fake.posts[0][1]["qualityProfileId"] == 4
    assert client.get("/v1/options", headers=AUTH).json()["defaults"]["radarr_profile_id"] == 1


def test_missing_default_is_config_failure_with_retry(client, fake):
    client.put("/v1/config/radarr", json={"base_url": "http://radarr.test", "api_key": "radarr0123456789abcdef"}, headers=AUTH)
    r = client.post("/v1/requests", json={"type": "movie", "id": 10331}, headers=AUTH)
    assert r.status_code == 422 and r.json()["error"]["kind"] == "config"
    st = client.get("/v1/status", headers=AUTH).json()["items"]["movie:10331"]
    assert st["state"] == "failed" and st["error_kind"] == "config"
    client.put("/v1/defaults", json={"radarr_profile_id": 4}, headers=AUTH)
    r = client.post("/v1/requests/movie/10331/retry", headers=AUTH)
    assert r.status_code == 200, r.text
    assert len(fake.movies) == 1 and fake.movies[0]["qualityProfileId"] == 4


def test_single_profile_used_without_default(client, fake):
    configure(client)
    r = client.post("/v1/requests", json={"type": "series", "id": 70001}, headers=AUTH)
    assert r.status_code == 200, r.text
    body = fake.posts[0][1]
    assert body["qualityProfileId"] == 7 and body["rootFolderPath"] == "/tv"
    assert [s["monitored"] for s in body["seasons"]] == [False, True, True]  # specials off
    assert "languageProfileId" not in body  # Sonarr v4


def test_sonarr_v3_language_profile_and_latest_season(tmp_path):
    fake = FakeArr(sonarr_version="3.0.10.1567")
    (tmp_path / "titles.json").write_text(json.dumps(TITLES))
    with TestClient(create_app(tmp_path, fake.transport(), False, TOKEN)) as c:
        configure(c)
        c.put("/v1/defaults", json={"season_monitor": "latest"}, headers=AUTH)
        assert c.post("/v1/requests", json={"type": "series", "id": 70001}, headers=AUTH).status_code == 200
        body = fake.posts[0][1]
        assert body["languageProfileId"] == 1
        assert [s["monitored"] for s in body["seasons"]] == [False, False, True]
        assert body["addOptions"]["monitor"] == "latestSeason"


def test_repeated_taps_do_not_duplicate(client, fake):
    configure(client)
    for _ in range(3):
        r = client.post("/v1/requests", json={"type": "movie", "id": 10331}, headers={**AUTH, "Idempotency-Key": "a"})
        assert r.status_code == 200
    assert len(fake.posts) == 1 and len(fake.movies) == 1


def test_concurrent_requests_do_not_duplicate(client, fake):
    configure(client)
    gw = client.app.state.gateway

    async def burst():
        return await asyncio.gather(*(gw.request("movie", 10331) for _ in range(8)))

    results = client.portal.call(burst)
    assert len(fake.posts) == 1
    assert sum(not r["deduplicated"] for r in results) == 1


def test_existing_library_item_is_linked_not_added(client, fake):
    configure(client)
    fake.movies.append({"id": 5, "tmdbId": 10331, "title": "Night of the Living Dead", "hasFile": True, "monitored": True})
    client.app.state.gateway._library.clear()
    r = client.post("/v1/requests", json={"type": "movie", "id": 10331}, headers=AUTH)
    assert r.json()["deduplicated"] and r.json()["status"]["state"] == "complete"
    assert fake.posts == []
    assert 10331 not in ids(client.get("/v1/discover?type=movie", headers=AUTH))


# ---------------------------------------------------------------------- status
def test_poster_progress_and_import_states(client, fake):
    configure(client)
    client.post("/v1/requests", json={"type": "movie", "id": 10331}, headers=AUTH)
    mid = fake.movies[0]["id"]
    fake.radarr_queue = [{"id": 1, "movieId": mid, "size": 1000, "sizeleft": 250, "status": "downloading",
                          "trackedDownloadStatus": "ok", "trackedDownloadState": "downloading"}]
    gw = client.app.state.gateway
    client.portal.call(gw.refresh_status)
    st = client.get("/v1/status", headers=AUTH).json()["items"]["movie:10331"]
    assert st["state"] == "downloading" and st["progress"] == 0.75
    # Download finished but not imported yet: importing, not complete.
    fake.radarr_queue[0].update(sizeleft=0, status="completed", trackedDownloadState="importPending")
    client.portal.call(gw.refresh_status)
    assert gw.status_for("movie", 10331)["state"] == "importing"
    fake.radarr_queue = []
    fake.movies[0]["hasFile"] = True
    client.portal.call(gw.refresh_status, True)
    assert gw.status_for("movie", 10331)["state"] == "complete"


def test_status_etag_304(client, fake):
    configure(client)
    r = client.get("/v1/status", headers=AUTH)
    etag = r.headers["etag"]
    assert "X-Poll-After" in r.headers
    r2 = client.get("/v1/status", headers={**AUTH, "If-None-Match": etag})
    assert r2.status_code == 304


def test_status_is_batched_not_per_title(client, fake):
    configure(client)
    gw = client.app.state.gateway
    client.portal.call(gw.refresh_status)
    fake.calls.clear()
    client.portal.call(gw.refresh_status)
    # one queue call per service; library served from cache
    assert sorted(c.split(" ", 1)[1] for c in fake.calls) == ["GET /api/v3/queue/details", "GET /sonarr/api/v3/queue/details"]


def test_series_aggregates_episode_progress(client, fake):
    configure(client)
    client.post("/v1/requests", json={"type": "series", "id": 70001}, headers=AUTH)
    sid = fake.series[0]["id"]
    fake.sonarr_queue = [
        {"id": 11, "seriesId": sid, "episodeId": 1, "size": 100, "sizeleft": 0, "status": "downloading", "trackedDownloadState": "downloading"},
        {"id": 12, "seriesId": sid, "episodeId": 2, "size": 100, "sizeleft": 100, "status": "queued"},
    ]
    fake.episodes = [
        {"id": 1, "seriesId": sid, "seasonNumber": 1, "episodeNumber": 1, "title": "Pilot", "hasFile": False, "monitored": True},
        {"id": 2, "seriesId": sid, "seasonNumber": 1, "episodeNumber": 2, "title": "Two", "hasFile": False, "monitored": True},
        {"id": 3, "seriesId": sid, "seasonNumber": 1, "episodeNumber": 3, "title": "Three", "hasFile": True, "monitored": True},
    ]
    gw = client.app.state.gateway
    client.portal.call(gw.refresh_status)
    st = gw.status_for("series", 70001)
    assert st["state"] == "downloading" and st["progress"] == 0.5
    detail = client.get("/v1/titles/series/70001", headers=AUTH).json()
    assert [e["state"] for e in detail["episodes"]] == ["downloading", "queued", "complete"]


# ---------------------------------------------------------------------- retry
def test_failed_download_retry_reuses_existing_record(client, fake):
    configure(client)
    client.post("/v1/requests", json={"type": "movie", "id": 10331}, headers=AUTH)
    mid = fake.movies[0]["id"]
    fake.radarr_queue = [{"id": 9, "movieId": mid, "size": 1000, "sizeleft": 900, "status": "failed",
                          "trackedDownloadStatus": "error", "trackedDownloadState": "failed",
                          "errorMessage": "Download client reported failure"}]
    gw = client.app.state.gateway
    client.portal.call(gw.refresh_status)
    st = client.get("/v1/status", headers=AUTH).json()["items"]["movie:10331"]
    assert st["state"] == "failed" and st["error_kind"] == "download" and "failure" in st["message"]
    activity = client.get("/v1/activity", headers=AUTH).json()["items"]
    assert activity[0]["status"]["state"] == "failed"
    r = client.post("/v1/requests/movie/10331/retry", headers=AUTH)
    assert r.status_code == 200, r.text
    assert fake.deleted_queue == [(9, {"removeFromClient": "true", "blocklist": "true"})]
    assert fake.commands[-1] == {"name": "MoviesSearch", "movieIds": [mid]}
    assert len(fake.movies) == 1 and len(fake.posts) == 1  # no second Radarr record


def test_unavailable_service_keeps_stale_status(client, fake):
    configure(client)
    client.post("/v1/requests", json={"type": "movie", "id": 10331}, headers=AUTH)
    gw = client.app.state.gateway
    fake.down.add("radarr.test")
    client.portal.call(gw.refresh_status)
    snap = client.get("/v1/status", headers=AUTH).json()
    assert snap["services"]["radarr"]["reachable"] is False
    assert "Could not connect" in snap["services"]["radarr"]["error"]["message"]
    assert snap["items"]["movie:10331"]["stale"] is True
    r = client.post("/v1/requests/movie/10331/retry", headers=AUTH)
    assert r.status_code == 503


# -------------------------------------------------------------------- polling
def test_adaptive_poll_interval(client, fake):
    gw = client.app.state.gateway
    now = 1_000_000.0
    gw.snapshot["items"] = {}
    gw.last_seen = gw.last_watching = None   # never seen (monotonic clock: 0 is not "long ago" on a fresh host)
    assert gw.poll_interval(now) is None          # nobody here, nothing active -> stop
    gw.last_seen = now
    assert gw.poll_interval(now) == 60
    gw.snapshot["items"] = {"movie:1": {"state": "downloading"}}
    assert gw.poll_interval(now) == 20
    gw.last_watching = now
    assert gw.poll_interval(now) == 5
    assert gw.poll_interval(now + 600) == 60      # app backgrounded, download continues


def test_classify_queue_states():
    assert classify_queue_item({"status": "paused", "size": 10, "sizeleft": 5})["state"] == "paused"
    assert classify_queue_item({"status": "warning", "trackedDownloadStatus": "warning"})["state"] == "stalled"
    c = classify_queue_item({"status": "completed", "trackedDownloadState": "importPending",
                             "trackedDownloadStatus": "warning",
                             "statusMessages": [{"title": "x", "messages": ["No files found are eligible"]}]})
    assert c["state"] == "failed" and "No files" in c["message"]
    assert classify_queue_item({"status": "downloading", "size": 0})["progress"] is None


# ------------------------------------------------------------- optional servers
def test_only_radarr_configured_works(client, fake):
    client.put("/v1/config/radarr", json={"base_url": "http://radarr.test", "api_key": "radarr0123456789abcdef"}, headers=AUTH)
    client.put("/v1/defaults", json={"radarr_profile_id": 1}, headers=AUTH)
    tv = client.get("/v1/discover?type=series", headers=AUTH).json()
    assert tv["configured"] is False and tv["items"] == [] and tv["error"] is None
    assert client.get("/v1/discover?type=movie", headers=AUTH).json()["configured"] is True
    snap = client.get("/v1/status", headers=AUTH).json()
    assert snap["services"]["sonarr"]["configured"] is False
    assert client.post("/v1/requests", json={"type": "movie", "id": 10331}, headers=AUTH).status_code == 200
    r = client.post("/v1/requests", json={"type": "series", "id": 70001}, headers=AUTH)
    assert r.status_code == 422 and "not configured" in r.json()["error"]["message"]
    opts = client.get("/v1/options", headers=AUTH).json()
    assert "quality_profiles" in opts["radarr"] and opts["sonarr"]["error"]["kind"] == "config"


def test_remove_connection(client, fake):
    configure(client)
    cfg = client.delete("/v1/config/sonarr", headers=AUTH).json()
    assert cfg["sonarr"]["configured"] is False and cfg["radarr"]["configured"] is True
    assert client.get("/v1/discover?type=series", headers=AUTH).json()["configured"] is False


# ------------------------------------------------------------------ edge cases
def test_retry_cooldown_prevents_search_storm(client, fake):
    configure(client)
    client.post("/v1/requests", json={"type": "movie", "id": 10331}, headers=AUTH)
    for _ in range(3):
        assert client.post("/v1/requests/movie/10331/retry", headers=AUTH).status_code == 200
    assert len([c for c in fake.commands if c["name"] == "MoviesSearch"]) == 1


def test_inaccessible_root_folder_is_config_error(client, fake):
    configure(client)
    orig = fake._radarr

    def patched(method, path, params, body):
        if path == "/api/v3/rootfolder":
            import httpx
            return httpx.Response(200, json=[{"path": "/movies", "accessible": False}])
        return orig(method, path, params, body)

    fake._radarr = patched
    client.app.state.gateway._options.clear()
    r = client.post("/v1/requests", json={"type": "movie", "id": 10331}, headers=AUTH)
    assert r.status_code == 422 and "not accessible" in r.json()["error"]["message"]
    assert fake.posts == []


def test_library_items_without_ids_are_ignored(client, fake):
    configure(client)
    fake.movies.append({"id": 77, "title": "Home video", "hasFile": True, "monitored": True})
    gw = client.app.state.gateway
    client.portal.call(gw.refresh_status, True)
    assert not any(k.endswith(":None") for k in gw.snapshot["items"])


# ------------------------------------------------------------------ debug logs
def test_debug_bundle_is_redacted(client, fake, tmp_path):
    import io
    import logging
    import zipfile

    from app.logs import RedactingFormatter, log_dir

    configure(client)
    handler = logging.FileHandler(log_dir(tmp_path) / "gateway.log")
    handler.setFormatter(RedactingFormatter("%(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger("x").warning("calling with X-Api-Key: radarr0123456789abcdef and token %s", TOKEN)
    handler.close()
    logging.getLogger().removeHandler(handler)
    r = client.get("/v1/debug/bundle", headers=AUTH)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert "gateway/info.json" in names and "gateway/logs/gateway.log" in names
    blob = b"".join(z.read(n) for n in names).decode()
    assert "radarr0123456789abcdef" not in blob and "sonarr0123456789abcdef" not in blob and TOKEN not in blob
    assert "calling with" in blob
    assert client.get("/v1/debug/bundle").status_code == 401


def test_redact_patterns():
    from app.logs import redact
    assert redact("GET /api?apikey=abcdef1234567890") == "GET /api?apikey=***"
    assert "s3cr3tvalue" not in redact("Authorization: Bearer s3cr3tvalue")


# -------------------------------------------------------------------- dev mode
def test_dev_simulation_progresses_to_complete(tmp_path):
    fake = FakeArr(simulate=True)
    (tmp_path / "titles.json").write_text(json.dumps(TITLES))
    with TestClient(create_app(tmp_path, fake.transport(), False, TOKEN)) as c:
        configure(c)
        c.post("/v1/requests", json={"type": "movie", "id": 10331}, headers=AUTH)
        gw = c.app.state.gateway
        seen = []
        for _ in range(20):
            c.portal.call(gw.refresh_status, True)
            seen.append(gw.status_for("movie", 10331)["state"])
        assert "downloading" in seen and "importing" in seen and seen[-1] == "complete"


# ------------------------------------------------------- filters & more like this
RICH = {"movies": [10331, 1010, 1011, 1012, 1013, 961, 653], "series": [70002, 70001]}


@pytest.fixture
def rich(tmp_path, fake):
    (tmp_path / "titles.json").write_text(json.dumps(RICH))
    with TestClient(create_app(tmp_path, fake.transport(), False, TOKEN)) as c:
        configure(c)
        yield c


def ids(resp):
    return [i["id"] for i in resp.json()["items"]]


def test_filters_and_sorting(rich):
    d = lambda qs: rich.get("/v1/discover?type=movie&" + qs, headers=AUTH)  # noqa: E731
    assert set(ids(d("genres=Horror"))) == {10331, 1010, 1012, 1013, 653}
    assert set(ids(d("genres=Crime,Comedy"))) == {1011, 1012, 961}                    # any-of
    assert set(ids(d("decade=1960"))) == {10331, 1010, 1012}
    assert set(ids(d("year_min=1950&year_max=1959"))) == {1013}
    assert set(ids(d("runtime=brisk"))) == {1010, 1011, 1012, 1013, 961}
    assert set(ids(d("rating_min=7.5"))) == {10331, 653}
    assert ids(d("decade=1920&sort=year_asc")) == [653, 961]
    assert ids(d("sort=year_desc"))[0] == 10331
    assert set(ids(d("sort=rating"))[:2]) == {10331, 653} and ids(d("sort=rating"))[-1] == 961  # unrated last
    assert ids(d("q=souls")) == [1010]
    assert set(ids(d("language=German"))) == {653}
    assert d("sort=bogus").status_code == 422


def test_new_filters_english_decades_runtime_range_neighbours(rich):
    d = lambda qs: rich.get("/v1/discover?type=movie&" + qs, headers=AUTH)  # noqa: E731
    assert set(ids(d("english_only=true"))) == {10331, 1010, 1011, 1012, 1013, 961}   # 961 has no language: kept
    assert set(ids(d("decades=1920,1960"))) == {653, 961, 10331, 1010, 1012}
    assert set(ids(d("runtime_min=75&runtime_max=95"))) == {1010, 1013, 961, 653}
    assert set(ids(d("exclude_genres=Horror"))) == {1011, 961}
    assert ids(d("genres=Crime")) == [1011]
    assert set(ids(d("genres=Crime&neighbours=true"))) == {1011, 10331}                # Thriller is Crime's neighbour
    assert ids(d("genres=Crime&neighbours=true&neighbours_off=Thriller")) == [1011]
    assert ids(d("genres=Crime&neighbours=true&exclude_genres=Horror")) == [1011]    # an excluded genre always wins


def test_discover_sorts_by_trusted_rating_and_pages_without_cap(rich):
    d = lambda qs: rich.get("/v1/discover?type=movie&" + qs, headers=AUTH).json()  # noqa: E731
    order = [i["id"] for i in d("")["items"]]
    assert set(order[:2]) == {10331, 653} and order[-1] == 961       # highest rated first by default, unrated last
    assert "page" not in d("") and d("")["matched"] == 7
    pages = [[i["id"] for i in d(f"page_size=3&page={n}")["items"]] for n in (1, 2, 3)]
    assert [len(p) for p in pages] == [3, 3, 1] and sum(pages, []) == order
    assert d("page_size=3&page=3")["matched"] == 7 and d("page_size=3&page=3")["page"] == 3
    card = next(i for i in d("")["items"] if i["id"] == 10331)
    assert card["rating"] == 7.6 and card["ratings"] == {"imdb": 7.8, "tmdb": 7.5}
    assert card["votes"] == {"imdb": 140000, "tmdb": 2000}
    f = d("")["facets"]
    assert f["genre_neighbours"]["Crime"] == [{"value": "Thriller", "overlap": 0.5}]
    assert f["years"] == {"min": 1922, "max": 1968} and f["non_english"] == 1
    tv = {i["id"]: i for i in rich.get("/v1/discover?type=series", headers=AUTH).json()["items"]}
    assert tv[70002]["rating"] == 7.0 and tv[70002]["ratings"] == {"tvdb": 7.2}       # 50 votes vs 20 prior votes
    assert rich.get("/v1/discover?type=movie&page_size=0", headers=AUTH).status_code == 422


def test_facets_describe_unfiltered_set(rich):
    r = rich.get("/v1/discover?type=movie&genres=Crime", headers=AUTH).json()
    assert r["total"] == 7 and len(r["items"]) == 1
    f = r["facets"]
    assert {"value": "Horror", "count": 5} in f["genres"]
    assert [d["value"] for d in f["decades"]] == ["1920", "1940", "1950", "1960"]
    assert f["rated"] == 6


def test_more_like_this_ranks_by_metadata(rich):
    r = rich.get("/v1/titles/movie/10331/similar", headers=AUTH).json()
    order = [i["id"] for i in r["items"]]
    assert 10331 not in order
    assert order[0] == 1010                      # horror + same era + rated similarly
    assert 1012 in order and 961 not in order     # horror comedy qualifies; an unrelated 1920s comedy doesn't
    top = r["items"][0]
    assert top["reasons"] and "Horror" in top["reasons"][0]
    # mixed results include the horror TV anthology, but not the comedy show
    types = {(i["type"], i["id"]) for i in r["items"]}
    assert ("series", 70002) in types and ("series", 70001) not in types
    no_mix = rich.get("/v1/titles/movie/10331/similar?mix=false", headers=AUTH).json()
    assert all(i["type"] == "movie" for i in no_mix["items"])


def test_more_like_this_respects_pool_and_tracking(rich, fake):
    rich.post("/v1/requests", json={"type": "movie", "id": 1010}, headers=AUTH)
    r = rich.get("/v1/titles/movie/10331/similar?mix=false", headers=AUTH).json()
    assert 1010 not in [i["id"] for i in r["items"]]               # requested -> hidden by default
    r = rich.get("/v1/titles/movie/10331/similar?mix=false&include_tracked=true", headers=AUTH).json()
    assert 1010 in [i["id"] for i in r["items"]]
    # 999 is in the fake metadata but in no source -> not a candidate
    assert 999 not in [i["id"] for i in r["items"]]


def test_detail_exposes_rich_metadata(rich):
    d = rich.get("/v1/titles/movie/10331", headers=AUTH).json()
    # rating is the trusted rating: IMDb and TMDB by votes, pulled towards 6.5 by 25k prior votes
    assert d["ratings"] == {"imdb": 7.8, "tmdb": 7.5} and d["rating"] == 7.6
    assert d["language"] == "English" and d["studio"] == "Image Ten" and "zombie" in d["keywords"]
    assert d["decade"] == 1960


# ------------------------------------------------------------ API compatibility
def _statuses(report):
    return {f"{f['feature']} › {c['name']}": c["status"] for f in report["features"] for c in f["checks"]}


def test_api_check_all_ok_against_current_api(client, fake):
    configure(client)
    r = client.post("/v1/compat/check", headers=AUTH).json()
    radarr, sonarr = r["radarr"], r["sonarr"]
    assert radarr["version"] == "5.14.0.9383" and radarr["reachable"]
    st = _statuses(radarr)
    assert st["Adding films › Add movie"] == "ok"           # validation probe got a 400
    assert st["Download progress › Queue"] == "ok"          # empty queue is fine
    assert st["Title details and filters › Movie lookup"] in ("ok", "info")
    assert st["Retry › Search command"] == "info"           # no spec reachable in tests
    assert radarr["status"] in ("ok", "info") and sonarr["status"] in ("ok", "info")
    assert not any("Language profiles" in k for k in _statuses(sonarr))   # v4: not needed
    assert fake.posts == [] and fake.movies == []           # probes never create anything
    assert client.get("/v1/config", headers=AUTH).json()["radarr"]["api_check"]["status"] in ("ok", "info")


def test_api_check_detects_changes_after_update(client, fake):
    configure(client)
    client.post("/v1/requests", json={"type": "movie", "id": 10331}, headers=AUTH)
    fake.radarr_queue = [{"id": 1, "movieId": fake.movies[0]["id"], "size": 10, "sizeleft": 5, "status": "downloading"}]
    first = client.post("/v1/compat/check?service=radarr", headers=AUTH).json()["radarr"]
    assert _statuses(first)["Download progress › Queue"] in ("ok", "info") and first["changes"] == []
    # Simulate a Radarr update that renames a field and drops an endpoint.
    fake.radarr_version = "6.0.0.1"
    fake.radarr_queue = [{"id": 1, "movieId": fake.movies[0]["id"], "size": 10, "remaining": 5, "status": "downloading"}]
    orig = fake._radarr

    def patched(method, path, params, body):
        import httpx
        if path == "/api/v3/movie" and method == "POST" and not body:
            return httpx.Response(404)
        return orig(method, path, params, body)

    fake._radarr = patched
    second = client.post("/v1/compat/check?service=radarr", headers=AUTH).json()["radarr"]
    st = _statuses(second)
    assert st["Download progress › Queue"] == "error" and st["Adding films › Add movie"] == "error"
    assert second["status"] == "error" and second["previous_version"] == "5.14.0.9383"
    changes = "\n".join(second["changes"])
    assert "Version 5.14.0.9383 → 6.0.0.1" in changes and "Download progress › Queue" in changes
    assert "sizeleft" in changes
    assert client.get("/v1/compat", headers=AUTH).json()["radarr"]["version"] == "6.0.0.1"


def test_api_check_uses_server_spec_for_write_endpoints(client, fake):
    configure(client)
    fake.radarr_spec = {
        "paths": {
            "/api/v3/command": {"post": {"requestBody": {"content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/CommandResource"}}}}}},
            "/api/v3/queue/{id}": {"delete": {"parameters": [{"name": "removeFromClient"}]}},
            "/api/v3/movie": {"post": {"requestBody": {"content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/MovieResource"}}}}}},
        },
        "components": {"schemas": {
            "CommandResource": {"properties": {"name": {}}},
            "MovieResource": {"properties": {k: {} for k in ["tmdbId", "title", "qualityProfileId", "monitored",
                                                             "minimumAvailability", "addOptions"]}},  # no rootFolderPath
        }},
    }
    r = client.post("/v1/compat/check?service=radarr", headers=AUTH).json()["radarr"]
    assert r["spec_source"].startswith("radarr server")
    st = {c["name"]: c for f in r["features"] for c in f["checks"]}
    assert st["Search command"]["status"] == "ok"
    assert st["Remove failed download"]["status"] == "warning" and "blocklist" in st["Remove failed download"]["details"][0]
    assert st["Add movie"]["status"] == "error" and "rootFolderPath" in st["Add movie"]["details"][0]
    assert st["Queue"]["status"] == "warning"   # GET endpoints missing from this partial spec are flagged


def test_api_check_service_down(client, fake):
    configure(client)
    fake.down.add("sonarr.test")
    r = client.post("/v1/compat/check?service=sonarr", headers=AUTH).json()["sonarr"]
    assert r["reachable"] is False and r["status"] == "error"


# ------------------------------------------------------------- web frontend, health, API console
def test_web_frontend_is_served_next_to_the_api(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert '<div id="app">' in r.text and r.headers["cache-control"] == "no-cache"
    assert client.get("/js/main.js").headers["content-type"].startswith("text/javascript")
    assert client.get("/manifest.webmanifest").headers["content-type"].startswith("application/manifest+json")
    assert client.get("/console/").status_code == 200
    # The API still wins over static files, and still needs the token.
    assert client.get("/v1/config").status_code == 401
    assert client.get("/docs").status_code == 200


def test_web_files_and_api_are_gzipped(client):
    """Phones load the app over mobile data: JS, CSS and JSON go out compressed when the client asks."""
    r = client.get("/js/store.js", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers.get("content-encoding") == "gzip" and "export" in r.text
    assert client.get("/css/app.css", headers={"Accept-Encoding": "gzip"}).headers.get("content-encoding") == "gzip"
    assert client.get("/js/store.js", headers={"Accept-Encoding": "identity"}).headers.get("content-encoding") is None


def test_api_only_when_web_dir_is_missing(tmp_path, fake):
    app = create_app(data_dir=tmp_path, transport=fake.transport(), run_poller=False, token=TOKEN,
                     web_dir=tmp_path / "no-web-here")
    with TestClient(app) as c:
        assert c.get("/").status_code == 404
        h = c.get("/v1/health").json()
        assert h["ok"] and h["web_frontend"] is False


def test_health_reports_services_without_secrets(client, fake):
    h = client.get("/v1/health").json()
    assert h["ok"] is True and h["status"] == "setup" and h["web_frontend"] is True
    assert h["services"]["radarr"] == {"configured": False, "reachable": None, "version": None,
                                       "checked_s_ago": None, "down_for_s": None}
    assert h["poller"] == "off"
    configure(client)
    client.get("/v1/status", headers=AUTH)
    h = client.get("/v1/health").json()
    assert h["status"] == "ok" and h["services"]["radarr"]["configured"] and h["version"]
    assert "radarr.test" not in json.dumps(h) and "radarr0123456789abcdef" not in json.dumps(h)
    fake.down.add("radarr.test")
    client.portal.call(client.app.state.gateway.refresh_status)
    h = client.get("/v1/health").json()
    assert h["ok"] is True and h["status"] == "degraded" and h["services"]["radarr"]["reachable"] is False


def test_debug_bundle_includes_frontend_log_redacted(client):
    import io
    import zipfile

    configure(client)
    r = client.post("/v1/debug/bundle", headers=AUTH,
                    json={"name": "web", "log": f"INFO hello Bearer {TOKEN} apikey=radarr0123456789abcdef",
                          "info": {"user_agent": "test"}})
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert {"web/app.log", "web/device.json", "gateway/info.json"} <= set(z.namelist())
    log_text = z.read("web/app.log").decode()
    assert "hello" in log_text and TOKEN not in log_text and "radarr0123456789abcdef" not in log_text


def test_api_console_lists_every_route(client):
    """web/console/index.html must offer every /v1 route, so humans can test all of them."""
    import re

    html = client.get("/console/").text
    shape = lambda path: re.sub(r"\{[^}]+\}", "{}", path)   # noqa: E731  parameter names may differ
    listed = {(m, shape(p)) for m, p in re.findall(r'method: "(\w+)", path: "([^"]+)"', html)}
    routes = {(m, shape(r.path)) for r in client.app.routes if getattr(r, "path", "").startswith("/v1/")
              for m in getattr(r, "methods", ()) if m not in ("HEAD", "OPTIONS")}
    assert routes - listed == set(), f"add these to ENDPOINTS in web/console/index.html: {sorted(routes - listed)}"
    assert listed - routes == set(), f"these console entries have no route: {sorted(listed - routes)}"


# ------------------------------------------------------------------ config manager + logging
def test_config_manager_reads_json_and_env(monkeypatch):
    from app import config
    assert config.cfg("gateway.batch_max") == 100
    assert config.msg("errors.batch_max", max=5) == "At most 5 titles per batch."
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    assert config.cfg("logging.level") == "DEBUG"
    monkeypatch.setenv("ARR_TIMEOUT", "3")
    assert config.cfg("arr.timeout") == 3.0
    with pytest.raises(KeyError):
        config.cfg("nope.nothing")


def test_every_message_template_is_used_in_code():
    import json
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    msgs = json.loads((root / "config" / "messages.json").read_text())
    code = "".join(p.read_text() for p in root.glob("*.py"))
    for group, items in msgs.items():
        for key in items:
            assert f"{group}.{key}" in code, f"unused message {group}.{key}"


def test_traced_logs_entry_and_exit_once(caplog):
    import logging

    from app.logs import traced

    @traced
    def work(x, token="s"):
        if x:
            return 1
        return 2

    with caplog.at_level(logging.DEBUG):
        assert work(0) == 2
    lines = [r.getMessage() for r in caplog.records]
    assert any("-> " in l and "work(x=0" in l for l in lines)
    assert sum("done in" in l for l in lines) == 1


def test_logging_setup_main_and_area_files_roll_daily(tmp_path):
    import logging

    from app.logs import log_dir, setup_logging
    setup_logging(tmp_path, "INFO")
    logging.getLogger("arr").info("from arr logic")
    logging.getLogger("gateway").info("from gateway logic")
    for h in logging.getLogger().handlers + logging.getLogger("arr").handlers:
        h.flush()
    d = log_dir(tmp_path)
    main, arr = (d / "gateway.log").read_text(), (d / "arr.log").read_text()
    assert "from arr logic" in main and "from gateway logic" in main
    assert "from arr logic" in arr and "from gateway logic" not in arr
    assert "test_logging_setup_main_and_area_files_roll_daily" in main   # function name in every line
    handler = next(h for h in logging.getLogger().handlers if hasattr(h, "namer") and h.namer)
    assert handler.namer(str(d / "gateway.log.20261107")).endswith("gateway-20261107.log")


# ------------------------------------------------------------------ debuggability
def test_outage_reason_and_transitions_are_logged(client, fake, caplog):
    import logging

    configure(client)
    gw = client.app.state.gateway
    with caplog.at_level(logging.INFO):
        fake.down.add("radarr.test")
        client.portal.call(gw.refresh_status)
        client.portal.call(gw.refresh_status)          # still down: no second DOWN line
        fake.down.discard("radarr.test")
        client.portal.call(gw.refresh_status)
    lines = [r.getMessage() for r in caplog.records]
    assert any("failed after" in l and "Could not connect" in l for l in lines)   # the reason, not "None"
    assert sum("Radarr is DOWN" in l for l in lines) == 1
    assert any("Radarr is back UP" in l for l in lines)


def test_health_has_fresh_reachability_when_idle(client, fake):
    """Nobody watching, nothing downloading: the idle check still notices an outage."""
    configure(client)
    gw = client.app.state.gateway
    client.get("/v1/status", headers=AUTH)
    gw.last_seen = gw.last_watching = None   # never seen (monotonic clock: 0 is not "long ago" on a fresh host)
    assert gw.poll_interval() is None
    fake.down.add("sonarr.test")
    client.portal.call(gw.check_reachability)
    h = client.get("/v1/health").json()
    assert h["status"] == "degraded" and h["services"]["sonarr"]["reachable"] is False
    assert h["services"]["sonarr"]["checked_s_ago"] is not None and h["services"]["sonarr"]["down_for_s"] is not None
    # the snapshot the apps see agrees
    assert client.get("/v1/status", headers=AUTH).json()["services"]["sonarr"]["reachable"] is False


def test_unexpected_error_is_json_with_request_id_and_logged(client, caplog, monkeypatch):
    import logging

    gw = client.app.state.gateway

    def boom():
        raise RuntimeError("kaboom")
    monkeypatch.setattr(gw, "public_config", boom)
    with caplog.at_level(logging.INFO):
        r = client.get("/v1/config", headers={**AUTH, "X-Request-Id": "web-abc123"})
    assert r.status_code == 500
    err = r.json()["error"]
    assert err["kind"] == "internal" and err["request_id"] == "web-abc123" and "web-abc123" in err["message"]
    assert r.headers["X-Request-Id"] == "web-abc123"
    unhandled = [rec for rec in caplog.records if "Unhandled error in GET /v1/config" in rec.getMessage()]
    assert unhandled and unhandled[0].exc_info and "kaboom" in str(unhandled[0].exc_info[1])


def test_every_error_uses_the_same_envelope(client):
    r = client.get("/v1/config")
    assert r.status_code == 401 and r.json()["error"]["kind"] == "auth" and r.json()["error"]["request_id"]
    r = client.post("/v1/requests", json={"type": "movie", "id": "abc"}, headers=AUTH)
    assert r.status_code == 422 and "id" in r.json()["error"]["message"]
    r = client.get("/v1/nope", headers=AUTH)
    assert r.status_code == 404 and r.json()["error"]["kind"] == "not_found"


def test_failed_request_is_logged(client, fake, caplog):
    import logging

    configure(client)
    fake.down.add("radarr.test")
    with caplog.at_level(logging.INFO):
        client.post("/v1/requests", json={"type": "movie", "id": 10331}, headers=AUTH)
    assert any("Request movie 10331 failed" in r.getMessage() for r in caplog.records)


def test_poller_survives_errors(client, monkeypatch):
    gw = client.app.state.gateway
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("db locked")
        await asyncio.sleep(10)

    async def run_briefly():
        try:
            await asyncio.wait_for(gw.run_poller(), 0.3)
        except asyncio.TimeoutError:
            pass

    monkeypatch.setattr(gw, "_poll_once", flaky)
    monkeypatch.setattr("app.service.POLLER_ERROR_BACKOFF", 0)
    client.portal.call(run_briefly)
    assert len(calls) == 2 and gw.last_poll_error_at


def test_data_dir_check_explains_permissions(tmp_path):
    import os
    import stat

    from app.main import check_data_dir
    assert check_data_dir(tmp_path / "ok") is None
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        problem = check_data_dir(locked)
        if os.geteuid() == 0:
            assert problem is None          # root can write anyway
        else:
            assert "not writable" in problem and "chown" in problem and "PUID" in problem
    finally:
        locked.chmod(stat.S_IRWXU)


def test_log_lines_carry_request_id(tmp_path):
    import logging

    from app.logs import REQUEST_ID, log_dir, setup_logging
    setup_logging(tmp_path, "INFO")
    token = REQUEST_ID.set("rid42")
    logging.getLogger("gateway").info("inside a request")
    REQUEST_ID.reset(token)
    logging.getLogger("gateway").info("outside")
    for h in logging.getLogger().handlers:
        h.flush()
    text = (log_dir(tmp_path) / "gateway.log").read_text()
    assert "[rid42] gateway" in text and "[-] gateway" in text


def test_log_lines_are_utc_iso(tmp_path):
    """KB-11: gateway lines start with UTC ISO 8601 + ms + Z, like the web log's toISOString()."""
    import logging
    import re

    from app.logs import log_dir, setup_logging
    setup_logging(tmp_path, "INFO")
    before = dt.datetime.now(dt.timezone.utc)
    logging.getLogger("gateway").info("what time is it")
    for h in logging.getLogger().handlers:
        h.flush()
    line = next(l for l in (log_dir(tmp_path) / "gateway.log").read_text().splitlines() if "what time is it" in l)
    m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3})Z ", line)
    assert m, f"not a UTC ISO timestamp: {line!r}"
    stamp = dt.datetime.fromisoformat(m.group(1)).replace(tzinfo=dt.timezone.utc)
    assert abs((stamp - before).total_seconds()) < 5   # UTC, not local time with a Z stuck on


# ------------------------------------------------------------------ GET /v1/debug/logs (Settings → Logs)
def _write_log(tmp_path, name, lines):
    from app.logs import log_dir
    (log_dir(tmp_path) / name).write_text("".join(l + "\n" for l in lines), encoding="utf-8")


def _log_line(i, level="INFO", rid="-", text=None):
    return f"2026-11-10T08:{i // 60:02d}:{i % 60:02d}.{i % 1000:03d}Z {level} [{rid}] gateway.work:12: {text or f'line {i}'}"


def test_debug_logs_tail_count_and_shape(client, tmp_path):
    _write_log(tmp_path, "gateway.log", [_log_line(i) for i in range(50)])
    r = client.get("/v1/debug/logs?lines=10", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [l["message"] for l in body["lines"]] == [f"line {i}" for i in range(40, 50)]   # oldest first, newest last
    assert body["truncated"] is True
    first = body["lines"][0]
    assert first == {"ts": "2026-11-10T08:00:40.040Z", "level": "INFO", "rid": "-", "source": "gateway",
                     "where": "gateway.work:12", "message": "line 40"}
    assert set(body["gateway"]) == {"version", "timezone", "now"} and body["gateway"]["now"].endswith("Z")
    assert client.get("/v1/debug/logs?lines=100", headers=AUTH).json()["truncated"] is False
    assert client.get("/v1/debug/logs").status_code == 401
    r = client.get("/v1/debug/logs?lines=999999", headers=AUTH)
    assert r.status_code == 422 and "request_id" in r.json()["error"]


def test_debug_logs_reads_previous_day_and_legacy_lines(client, tmp_path):
    _write_log(tmp_path, "gateway-20261109.log", ["2026-11-09 23:59:59,500 INFO [-] gateway.work:3: old format"]
               + [_log_line(i, text=f"yesterday {i}") for i in range(3)])
    _write_log(tmp_path, "gateway.log", [_log_line(i) for i in range(2)])
    body = client.get("/v1/debug/logs?lines=6", headers=AUTH).json()
    lines = body["lines"]
    assert [l["message"] for l in lines] == ["old format", "yesterday 0", "yesterday 1", "yesterday 2", "line 0", "line 1"]
    assert lines[0]["ts"].endswith(".500Z") and lines[0]["level"] == "INFO" and body["truncated"] is False
    assert client.get("/v1/debug/logs?lines=5", headers=AUTH).json()["truncated"] is True


def test_debug_logs_level_rid_and_search_filters(client, tmp_path):
    _write_log(tmp_path, "gateway.log", [
        _log_line(1, "INFO", "web-aaaa1111", "GET /v1/status -> 200"),
        _log_line(2, "WARNING", "web-bbbb2222", "POST /v1/requests -> 503"),
        _log_line(3, "ERROR", "-", "Radarr is down"),
        _log_line(4, "DEBUG", "web-aaaa1111", "detail"),
    ])
    get = lambda query: [l["message"] for l in client.get(f"/v1/debug/logs?{query}", headers=AUTH).json()["lines"]]  # noqa: E731
    assert get("level=WARNING") == ["POST /v1/requests -> 503", "Radarr is down"]
    assert get("level=warn") == ["POST /v1/requests -> 503", "Radarr is down"]
    assert get("rid=web-aaaa1111") == ["GET /v1/status -> 200", "detail"]
    assert get("q=radarr") == ["Radarr is down"]
    r = client.get("/v1/debug/logs?level=loud", headers=AUTH)
    assert r.status_code == 422 and r.json()["error"]["kind"] == "request"


def test_debug_logs_redacts_registered_secrets(client, tmp_path):
    from app.logs import register_secret
    register_secret("supersecret-abcdef123")
    _write_log(tmp_path, "gateway.log", [_log_line(1, text="key supersecret-abcdef123 and token " + TOKEN)])
    body = client.get("/v1/debug/logs", headers=AUTH).text
    assert "supersecret-abcdef123" not in body and TOKEN not in body and "***" in body


def test_debug_logs_keeps_traceback_with_its_line(client, tmp_path):
    import logging

    from app.logs import setup_logging
    setup_logging(tmp_path, "INFO")
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("gateway").exception("it broke")
    logging.getLogger("gateway").info("after")
    for h in logging.getLogger().handlers:
        h.flush()
    lines = client.get("/v1/debug/logs?lines=2", headers=AUTH).json()["lines"]
    assert [l["level"] for l in lines] == ["ERROR", "INFO"]
    assert lines[0]["message"].startswith("it broke\nTraceback") and "ValueError: boom" in lines[0]["message"]
    assert lines[0]["where"].startswith("gateway.test_debug_logs_keeps_traceback_with_its_line:")
    assert lines[1]["message"] == "after"


def test_debug_logs_without_a_log_file(client, tmp_path):
    from app.logs import log_dir
    for f in log_dir(tmp_path).glob("*.log*"):
        f.unlink()
    body = client.get("/v1/debug/logs", headers=AUTH).json()
    assert body["lines"] == [] and body["truncated"] is False


def test_debug_logs_reads_only_the_tail_of_a_big_file(client, tmp_path, monkeypatch):
    from app import logs
    _write_log(tmp_path, "gateway.log", [_log_line(i % 3000) for i in range(30000)])
    reads = []
    real_open = logs.Path.open

    def counting_open(self, *a, **kw):
        f = real_open(self, *a, **kw)
        real_read = f.read
        f.read = lambda n=-1: reads.append(n) or real_read(n)   # noqa: E731
        return f
    monkeypatch.setattr(logs.Path, "open", counting_open)
    body = client.get("/v1/debug/logs?lines=5", headers=AUTH).json()
    assert len(body["lines"]) == 5 and body["truncated"]
    assert all(n > 0 for n in reads) and sum(reads) <= 65536   # one chunk from the end, never the whole file


def test_boot_checks_data_dir_and_leaves_owned_folders_alone(tmp_path, monkeypatch):
    import os

    from app import boot
    d = tmp_path / "data"
    d.mkdir()
    assert boot.take_ownership(d, os.getuid(), os.getgid()) is False   # already ours: nothing to do
    monkeypatch.setenv("DATA_DIR", str(d))
    if os.getuid() != 0:
        boot.main([])                               # writable: returns (would exec the command)
        d.chmod(0o500)
        try:
            with pytest.raises(SystemExit):
                boot.main([])
        finally:
            d.chmod(0o700)


def test_docker_specific_connection_hints():
    import httpx

    from app.arr import _describe_connect_error
    dns = httpx.ConnectError("[Errno -2] Name or service not known")
    assert "same Docker network" in _describe_connect_error("radarr", "http://radarr:7878", dns).message
    assert "Tailscale" in _describe_connect_error("radarr", "http://box.tail1.ts.net", dns).message
    refused = httpx.ConnectError("[Errno 111] Connection refused")
    e = _describe_connect_error("sonarr", "http://localhost:8989", refused)
    assert e.kind == "config" and "Phonarr container itself" in e.message and "http://sonarr:8989" in e.message


def test_arr_server_error_keeps_its_explanation():
    import httpx

    from app.arr import ArrClient, ArrError

    def handler(request):
        return httpx.Response(503, json={"message": "Search for 'matrix' failed. Invalid response received from RadarrAPI."})

    async def go():
        c = ArrClient("radarr", "http://radarr:7878", "radarr0123456789abcdef", httpx.MockTransport(handler))
        try:
            await c.get("/api/v3/movie/lookup", term="matrix")
        finally:
            await c.aclose()

    with pytest.raises(ArrError) as e:
        asyncio.run(go())
    assert e.value.kind == "unavailable" and "HTTP 503" in e.value.message and "RadarrAPI" in e.value.message


def test_secrets_can_come_from_files(tmp_path, monkeypatch):
    from app.main import _load_token, env_or_file
    secret = tmp_path / "token"
    secret.write_text("from-a-docker-secret\n")
    monkeypatch.delenv("ARR_GATEWAY_TOKEN", raising=False)
    monkeypatch.setenv("ARR_GATEWAY_TOKEN_FILE", str(secret))
    assert _load_token(tmp_path) == ("from-a-docker-secret", "file")
    monkeypatch.setenv("ARR_GATEWAY_TOKEN", "plain")
    assert env_or_file("ARR_GATEWAY_TOKEN") == "plain"          # the plain variable wins
    monkeypatch.setenv("RADARR_API_KEY_FILE", str(tmp_path / "missing"))
    assert env_or_file("RADARR_API_KEY") == ""


# ------------------------------------------------------------------ moving the data folder
def _export_zip(data, tmp_path, name="data.zip"):
    from app.datafiles import export_data
    out = tmp_path / name
    with out.open("wb") as f:
        export_data(f, data)
    return out


def test_export_imports_into_another_project_and_reads_back_the_same(tmp_path, fake):
    import zipfile

    from app.datafiles import import_data
    from app.db import Database
    a, b = tmp_path / "a" / "data", tmp_path / "b" / "data"
    a.mkdir(parents=True)
    (a / "titles.json").write_text(json.dumps(TITLES))
    (a / "photos").mkdir()
    (a / "photos" / "p.jpg").write_bytes(bytes(range(256)) * 10)
    (a / "access_token").write_text("secret-token-a")
    with TestClient(create_app(a, fake.transport(), False, TOKEN, output_dir=tmp_path / "a" / "cache")) as c:
        configure(c)
        assert c.post("/v1/requests", json={"type": "movie", "id": 10331}, headers=AUTH).status_code == 200
    src = Database(a / "gateway.sqlite3")
    zip_path = _export_zip(a, tmp_path)
    names = zipfile.ZipFile(zip_path).namelist()
    assert "manifest.json" in names and "data/gateway.sqlite3" in names and "data/photos/p.jpg" in names
    assert not any(n.endswith(("-wal", "-shm", "access_token")) for n in names)          # secrets and WAL stay out
    blob = b"".join(zipfile.ZipFile(zip_path).read(n) for n in names)
    assert b"radarr0123456789abcdef" not in blob and b"secret-token-a" not in blob        # no API keys either

    b.mkdir(parents=True)
    (b / "access_token").write_text("secret-token-b")       # the new machine's own secret is kept
    import_data(zip_path, b)
    dst = Database(b / "gateway.sqlite3")
    assert dst.list_requests() == src.list_requests() and dst.list_requests()
    assert dst.get_setting("defaults") == src.get_setting("defaults")
    assert dst.get_service("radarr") is None                 # reconnect with the key from .env or Settings
    assert (b / "titles.json").read_text() == (a / "titles.json").read_text()
    assert (b / "photos" / "p.jpg").read_bytes() == (a / "photos" / "p.jpg").read_bytes()
    assert (b / "access_token").read_text() == "secret-token-b"
    assert not [p for p in b.iterdir() if p.name.startswith(".import-staging-")]


def test_export_is_consistent_while_a_connection_writes(tmp_path):
    import io
    import sqlite3
    import threading
    import zipfile

    from app.datafiles import export_data
    data = tmp_path / "data"
    data.mkdir()
    conn = sqlite3.connect(str(data / "notes.db"), check_same_thread=False, isolation_level=None)   # any SQLite file
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (n INTEGER)")
    stop = threading.Event()

    def writer():
        while not stop.is_set():                              # two rows per transaction: a torn copy shows an odd count
            conn.execute("BEGIN")
            conn.execute("INSERT INTO t VALUES (1)")
            conn.execute("INSERT INTO t VALUES (2)")
            conn.execute("COMMIT")

    t = threading.Thread(target=writer)
    t.start()
    try:
        for _ in range(5):
            buf = io.BytesIO()
            export_data(buf, data)
            copy = tmp_path / "copy.sqlite3"
            copy.write_bytes(zipfile.ZipFile(buf).read("data/notes.db"))
            check = sqlite3.connect(str(copy))
            assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert check.execute("SELECT COUNT(*) FROM t").fetchone()[0] % 2 == 0
            check.close()
    finally:
        stop.set()
        t.join()
        conn.close()


def test_import_refuses_a_folder_with_data_unless_replace(tmp_path):
    from app.datafiles import DataFileError, import_data
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "titles.json").write_text("new")
    zip_path = _export_zip(a, tmp_path)
    (b / "titles.json").write_text("old")
    (b / "stale.txt").write_text("gone after replace")
    with pytest.raises(DataFileError, match="--replace"):
        import_data(zip_path, b)
    assert (b / "titles.json").read_text() == "old"
    import_data(zip_path, b, replace=True)
    assert (b / "titles.json").read_text() == "new" and not (b / "stale.txt").exists()


def test_import_rejects_damaged_foreign_and_unsafe_files(tmp_path):
    import zipfile

    from app.datafiles import DataFileError, import_data
    a = tmp_path / "a"
    a.mkdir()
    (a / "titles.json").write_text("[1, 2, 3]")
    good = zipfile.ZipFile(_export_zip(a, tmp_path))
    manifest = json.loads(good.read("manifest.json"))

    def build(name, entries):
        p = tmp_path / name
        with zipfile.ZipFile(p, "w") as z:
            for n, body in entries.items():
                z.writestr(n, body)
        return p

    def unsafe(path):
        m = dict(manifest, files=[{"path": path, "size": 3, "sha256": "0" * 64}])
        return build(f"unsafe{abs(hash(path))}.zip", {"manifest.json": json.dumps(m), f"data/{path}": "bad"})

    cases = {
        "not a Phonarr data export": tmp_path / "plain.txt",
        "no manifest": build("nomanifest.zip", {"data/titles.json": "[1, 2, 3]"}),
        "not a \"phonarr-data\"": build("foreign.zip", {"manifest.json": json.dumps(dict(manifest, format="other")),
                                                       "data/titles.json": "[1, 2, 3]"}),
        "damaged": build("damaged.zip", {"manifest.json": json.dumps(manifest), "data/titles.json": "[9, 9, 9]"}),
        "doesn't list": build("extra.zip", {"manifest.json": json.dumps(manifest), "data/titles.json": "[1, 2, 3]",
                                            "data/extra.txt": "x"}),
        "is missing": build("missing.zip", {"manifest.json": json.dumps(manifest)}),
    }
    (tmp_path / "plain.txt").write_text("hello")
    for i, path in enumerate(["../escape.txt", "/etc/passwd", "a/../../b"]):
        cases[f"unsafe path{' ' * i}"] = unsafe(path)
    target = tmp_path / "target"
    target.mkdir()
    for expected, src in cases.items():
        with pytest.raises(DataFileError, match=expected.strip()):
            import_data(src, target)
        assert list(target.iterdir()) == []                  # nothing written, no staging left behind


def test_config_path_outside_data_folder_is_a_config_error(tmp_path, monkeypatch):
    from app import config
    for bad in ("../elsewhere.sqlite3", "/var/lib/gateway.sqlite3", ""):
        (tmp_path / "settings.json").write_text(json.dumps({"storage": {"data_files": {"database": bad}}}))
        monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
        config.reload()
        try:
            with pytest.raises(config.ConfigError) as e:
                config.check_paths()
            assert "settings.json" in str(e.value) and str(tmp_path) in str(e.value)
            assert "storage.data_files.database" in str(e.value)
        finally:
            monkeypatch.delenv("CONFIG_DIR")
            config.reload()
    config.check_paths()                                      # the shipped settings are fine
    monkeypatch.setenv("OUTPUT_DIR", str(config.data_dir() / "cache"))
    with pytest.raises(config.ConfigError, match="separate"):
        config.check_paths()


def test_cli_streams_export_and_import(tmp_path, monkeypatch, capsysbinary):
    import io
    import sys

    from app.__main__ import main
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    (a / "titles.json").write_text("[1]")
    monkeypatch.setenv("DATA_DIR", str(a))
    assert main(["export-data", "-"]) == 0
    zipped = capsysbinary.readouterr().out
    assert zipped[:2] == b"PK"
    monkeypatch.setenv("DATA_DIR", str(b))
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(zipped)))
    assert main(["import-data", "-"]) == 0
    assert (b / "titles.json").read_text() == "[1]"
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(zipped)))
    assert main(["import-data", "-"]) == 1                   # now has data: needs --replace


# ------------------------------------------------------------------ calendar (more in test_calendar.py)
def test_calendar_route_needs_token_and_uses_the_error_envelope(client):
    assert client.get("/v1/calendar").json()["error"]["kind"] == "auth"
    r = client.get("/v1/calendar?start=2026-11-10&end=2026-11-01", headers=AUTH)
    assert r.status_code == 422 and r.json()["error"]["kind"] == "request" and r.json()["error"]["request_id"]
    body = client.get("/v1/calendar?start=2026-11-10&end=2026-11-11&tz=Europe/London", headers=AUTH).json()
    assert [d["date"] for d in body["days"]] == ["2026-11-10", "2026-11-11"]
    assert body["services"]["radarr"]["configured"] is False and body["services"]["sonarr"]["configured"] is False
