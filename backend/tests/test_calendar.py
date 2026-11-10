"""Calendar (GET /v1/calendar): docs/KNOWN_BUGS_PRD.md items KB-12..KB-16 and KB-30..KB-36.

Written before the feature existed. The fake Radarr/Sonarr answer /api/v3/calendar the way the real ones do:
only titles in the library, filtered by the start/end instants they are given, unmonitored ones only with
unmonitored=true, and the series record only with includeSeries=true.
"""
import datetime as dt

from fastapi.testclient import TestClient

from app.devfake import FakeArr
from app.main import create_app

from .test_gateway import AUTH, TOKEN, client, configure, fake  # noqa: F401  (fixtures)

POSTER = "https://image.tmdb.org/p/upcoming.jpg"


def add_movie(fake, tmdb, title, monitored=True, has_file=False, **dates):
    m = {"id": fake._id(), "tmdbId": tmdb, "title": title, "year": 2026, "monitored": monitored, "hasFile": has_file,
         "images": [{"coverType": "poster", "remoteUrl": POSTER}], **dates}
    fake.movies.append(m)
    return m


def add_series(fake, tvdb, title, monitored=True):
    s = {"id": fake._id(), "tvdbId": tvdb, "title": title, "year": 2026, "monitored": monitored,
         "statistics": {"episodeFileCount": 0, "episodeCount": 10},
         "images": [{"coverType": "poster", "remoteUrl": "https://artworks.thetvdb.com/show.jpg"}]}
    fake.series.append(s)
    return s


def add_episode(fake, series, season, number, air_utc, title=None, monitored=True, has_file=False, **extra):
    e = {"id": fake._id(), "seriesId": series["id"], "seasonNumber": season, "episodeNumber": number,
         "title": title or f"Episode {number}", "monitored": monitored, "hasFile": has_file, **extra}
    if air_utc:
        e["airDateUtc"] = air_utc
        e["airDate"] = air_utc[:10]
    fake.episodes.append(e)
    return e


def cal(c, start, end, tz="UTC", status=200):
    r = c.get("/v1/calendar", params={"start": start, "end": end, "tz": tz}, headers=AUTH)
    assert r.status_code == status, r.text
    return r.json()


def entries(body, date=None):
    return [i for d in body["days"] if date is None or d["date"] == date for i in d["items"]]


def on(body, date):
    return entries(body, date)


# ------------------------------------------------------------------ B. time and timezones
def test_calendar_movie_dates_are_days_not_instants(client, fake):
    """KB-12: Radarr's release dates are days sent as midnight UTC. West of UTC they must not move a day back."""
    configure(client)
    add_movie(fake, 5001, "Upcoming Film", inCinemas="2026-11-12T00:00:00Z",
              digitalRelease="2026-11-20T00:00:00Z", physicalRelease="2026-12-01T00:00:00Z")
    for tz in ("America/Los_Angeles", "Pacific/Honolulu", "UTC", "Pacific/Kiritimati"):
        body = cal(client, "2026-11-10", "2026-12-05", tz)
        found = [(d["date"], i["kind"], i["time"]) for d in body["days"] for i in d["items"]]
        assert found == [("2026-11-12", "cinema", None), ("2026-11-20", "digital", None),
                         ("2026-12-01", "physical", None)], tz
    one = on(body, "2026-11-12")[0]
    assert one["type"] == "movie" and one["id"] == 5001 and one["title"] == "Upcoming Film"
    assert one["poster"] == POSTER and one["monitored"] is True and one["has_file"] is False


def test_calendar_movie_kinds_outside_the_range_are_left_out(client, fake):
    configure(client)
    add_movie(fake, 5002, "Half In", inCinemas="2026-10-01T00:00:00Z", digitalRelease="2026-11-15T00:00:00Z")
    body = cal(client, "2026-11-10", "2026-11-20")
    assert [(d["date"], i["kind"]) for d in body["days"] for i in d["items"]] == [("2026-11-15", "digital")]


def test_calendar_episode_times_follow_viewer_timezone(client, fake):
    """KB-13: 02:00 UTC is 21:00 the evening before in New York and 11:00 the same day in Tokyo."""
    configure(client)
    show = add_series(fake, 80001, "Late Show")
    add_episode(fake, show, 2, 5, "2026-11-11T02:00:00Z", title="The Fifth")
    ny = cal(client, "2026-11-09", "2026-11-12", "America/New_York")
    assert [(d["date"], i["time"]) for d in ny["days"] for i in d["items"]] == [("2026-11-10", "21:00")]
    item = entries(ny)[0]
    assert item["type"] == "series" and item["id"] == 80001 and item["title"] == "Late Show"
    assert item["season"] == 2 and item["episode"] == 5 and item["episode_title"] == "The Fifth"
    assert item["episodes"] == [5, 5] and item["count"] == 1 and item["kind"] == "episode"
    tokyo = cal(client, "2026-11-09", "2026-11-12", "Asia/Tokyo")
    assert [(d["date"], i["time"]) for d in tokyo["days"] for i in d["items"]] == [("2026-11-11", "11:00")]
    assert ny["tz"] == "America/New_York" and ny["tz_fallback"] is False


def test_calendar_range_edges_keep_late_episodes(client, fake):
    """KB-13: asking Sonarr for whole UTC days would drop the 21:00 New York episode (02:00 UTC next day)."""
    configure(client)
    show = add_series(fake, 80002, "Edge Show")
    add_episode(fake, show, 1, 1, "2026-11-11T02:00:00Z")    # 21:00 on the 10th in New York: in range
    add_episode(fake, show, 1, 2, "2026-11-10T04:00:00Z")    # 23:00 on the 9th in New York: not in range
    add_episode(fake, show, 1, 3, "2026-11-11T05:30:00Z")    # 00:30 on the 11th in New York: not in range
    body = cal(client, "2026-11-10", "2026-11-10", "America/New_York")
    assert [d["date"] for d in body["days"]] == ["2026-11-10"]
    assert [i["episode"] for i in entries(body)] == [1]
    tokyo = cal(client, "2026-11-10", "2026-11-10", "Asia/Tokyo")   # +9: 04:00 UTC on the 10th is 13:00 there
    assert [i["episode"] for i in entries(tokyo)] == [2]


def test_calendar_episode_without_utc_time_uses_air_date(client, fake):
    configure(client)
    show = add_series(fake, 80003, "Dateless")
    add_episode(fake, show, 1, 1, None, airDate="2026-11-12")
    body = cal(client, "2026-11-10", "2026-11-14", "America/New_York")
    assert [(d["date"], i["time"]) for d in body["days"] for i in d["items"]] == [("2026-11-12", None)]


def test_calendar_unknown_timezone_falls_back_to_utc(client, fake):
    """KB-14: a bad tz from a browser is never a 500; it is UTC, and the response says so."""
    configure(client)
    show = add_series(fake, 80004, "Anywhere")
    add_episode(fake, show, 1, 1, "2026-11-11T02:00:00Z")
    for bad in ("Mars/Olympus_Mons", "../../etc/passwd", "", "Not A Zone"):
        r = client.get("/v1/calendar", params={"start": "2026-11-10", "end": "2026-11-12", "tz": bad}, headers=AUTH)
        assert r.status_code == 200, (bad, r.text)
        body = r.json()
        assert body["tz"] == "UTC" and body["tz_fallback"] is (bad != "")
        assert [(d["date"], i["time"]) for d in body["days"] for i in d["items"]] == [("2026-11-11", "02:00")]


def test_calendar_days_step_by_calendar_day_over_dst_london(client, fake):
    """KB-16: Europe/London leaves summer time on 25 October 2026. Every day appears once, in order."""
    configure(client)
    show = add_series(fake, 80005, "Clock Change")
    add_episode(fake, show, 1, 1, "2026-10-25T00:30:00Z")   # still BST (UTC+1): 01:30 on the 25th
    add_episode(fake, show, 1, 2, "2026-10-26T00:30:00Z")   # GMT: 00:30 on the 26th
    body = cal(client, "2026-10-23", "2026-10-28", "Europe/London")
    assert [d["date"] for d in body["days"]] == [f"2026-10-{n}" for n in range(23, 29)]
    assert [(d["date"], i["time"]) for d in body["days"] for i in d["items"]] == [("2026-10-25", "01:30"),
                                                                                  ("2026-10-26", "00:30")]
    spring = cal(client, "2026-03-27", "2026-03-31", "Europe/London")   # summer time starts 29 March
    assert [d["date"] for d in spring["days"]] == [f"2026-03-{n}" for n in range(27, 32)]


def test_calendar_days_step_by_calendar_day_over_dst_new_york(client, fake):
    """KB-16: America/New_York starts summer time on 8 March and ends it on 1 November 2026."""
    configure(client)
    show = add_series(fake, 80006, "Eastern")
    add_episode(fake, show, 1, 1, "2026-03-08T01:00:00Z")   # EST (UTC-5): 20:00 on the 7th
    add_episode(fake, show, 1, 2, "2026-03-09T01:00:00Z")   # EDT (UTC-4): 21:00 on the 8th
    body = cal(client, "2026-03-06", "2026-03-10", "America/New_York")
    assert [d["date"] for d in body["days"]] == [f"2026-03-{n:02d}" for n in range(6, 11)]
    assert [(d["date"], i["time"]) for d in body["days"] for i in d["items"]] == [("2026-03-07", "20:00"),
                                                                                  ("2026-03-08", "21:00")]
    autumn = cal(client, "2026-10-30", "2026-11-03", "America/New_York")
    assert [d["date"] for d in autumn["days"]] == ["2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02", "2026-11-03"]


def test_calendar_default_range_and_validation(client, fake):
    configure(client)
    r = client.get("/v1/calendar", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    today = dt.datetime.now(dt.timezone.utc).date()
    assert body["start"] == (today - dt.timedelta(days=7)).isoformat()
    assert body["end"] == (today + dt.timedelta(days=30)).isoformat()
    assert len(body["days"]) == 38 and body["tz"] == "UTC" and body["tz_fallback"] is False
    assert client.get("/v1/calendar").status_code == 401
    for params in ({"start": "2026-02-30"}, {"start": "tomorrow"}, {"end": "2026-13-01"},
                   {"start": "2026-11-10", "end": "2026-11-01"}, {"start": "2026-01-01", "end": "2026-12-31"}):
        r = client.get("/v1/calendar", params=params, headers=AUTH)
        assert r.status_code == 422, (params, r.text)
        err = r.json()["error"]
        assert err["kind"] == "request" and err["message"] and err["request_id"]


# ------------------------------------------------------------------ D. calendar requirements
def test_calendar_follows_connection_changes(client, fake):
    """KB-30: a cached calendar never outlives a connection change; removing a server drops its entries."""
    configure(client)
    add_movie(fake, 5003, "First", inCinemas="2026-11-12T00:00:00Z")
    calls = lambda: sum("radarr.test GET /api/v3/calendar" == c for c in fake.calls)   # noqa: E731
    assert [i["id"] for i in entries(cal(client, "2026-11-10", "2026-11-20"))] == [5003]
    add_movie(fake, 5004, "Second", inCinemas="2026-11-13T00:00:00Z")
    before = calls()
    assert [i["id"] for i in entries(cal(client, "2026-11-10", "2026-11-20"))] == [5003]   # short cache
    assert calls() == before
    r = client.put("/v1/config/radarr", json={"base_url": "http://radarr.test:7878/"}, headers=AUTH)
    assert r.status_code == 200
    assert [i["id"] for i in entries(cal(client, "2026-11-10", "2026-11-20"))] == [5003, 5004]
    client.delete("/v1/config/radarr", headers=AUTH)
    body = cal(client, "2026-11-10", "2026-11-20")
    assert entries(body) == []
    assert body["services"]["radarr"] == {"configured": False, "ok": None, "error": None}
    assert body["services"]["sonarr"]["configured"] is True and body["services"]["sonarr"]["ok"] is True


def test_calendar_partial_failure_keeps_other_service(client, fake):
    """KB-31: Sonarr down still shows Radarr's entries, with Sonarr's error named. The error is not cached."""
    configure(client)
    add_movie(fake, 5005, "Still Here", digitalRelease="2026-11-12T00:00:00Z")
    show = add_series(fake, 80007, "Gone For Now")
    add_episode(fake, show, 1, 1, "2026-11-12T20:00:00Z")
    fake.down.add("sonarr.test")
    body = cal(client, "2026-11-10", "2026-11-14")
    assert [i["id"] for i in entries(body)] == [5005]
    assert body["services"]["radarr"] == {"configured": True, "ok": True, "error": None}
    sonarr = body["services"]["sonarr"]
    assert sonarr["configured"] is True and sonarr["ok"] is False and "Could not connect" in sonarr["error"]["message"]
    fake.down.discard("sonarr.test")
    body = cal(client, "2026-11-10", "2026-11-14")
    assert sorted(i["type"] for i in entries(body)) == ["movie", "series"] and body["services"]["sonarr"]["ok"]
    fake.down.update({"radarr.test", "sonarr.test"})
    client.app.state.gateway.calendar_cache.clear()
    body = cal(client, "2026-11-10", "2026-11-14")      # both down: still a 200 with both errors, never a 5xx
    assert entries(body) == [] and not body["services"]["radarr"]["ok"] and not body["services"]["sonarr"]["ok"]


def test_calendar_collapses_episode_bundles(client, fake):
    """KB-32: a season dropped at once is one entry per show per day ("8 episodes")."""
    configure(client)
    binge = add_series(fake, 80008, "Binge Drop")
    for n in range(1, 9):
        add_episode(fake, binge, 2, n, "2026-11-12T08:00:00Z", has_file=n <= 3)
    weekly = add_series(fake, 80009, "Weekly")
    add_episode(fake, weekly, 1, 4, "2026-11-12T20:00:00Z")
    add_episode(fake, weekly, 1, 5, "2026-11-19T20:00:00Z")
    body = cal(client, "2026-11-10", "2026-11-20")
    day = on(body, "2026-11-12")
    bundle = next(i for i in day if i["id"] == 80008)
    assert bundle["episodes"] == [1, 8] and bundle["count"] == 8 and bundle["season"] == 2
    assert bundle["episode"] == 1 and bundle["episode_title"] is None and bundle["has_file"] is False
    assert len([i for i in day if i["id"] == 80008]) == 1
    assert [(d["date"], i["episode"]) for d in body["days"] for i in d["items"] if i["id"] == 80009] == \
        [("2026-11-12", 4), ("2026-11-19", 5)]


def test_calendar_orders_monitored_first_and_counts_series_monitoring(client, fake):
    """KB-34: monitored before unmonitored, then by time (all-day releases first), then title; an unmonitored
    series makes its episodes unmonitored even when the episode itself is monitored."""
    configure(client)
    add_movie(fake, 5006, "Aardvark", monitored=False, inCinemas="2026-11-12T00:00:00Z")
    add_movie(fake, 5007, "Zebra", inCinemas="2026-11-12T00:00:00Z")
    evening = add_series(fake, 80010, "Evening")
    add_episode(fake, evening, 1, 1, "2026-11-12T20:00:00Z")
    afternoon = add_series(fake, 80011, "Afternoon")
    add_episode(fake, afternoon, 1, 1, "2026-11-12T18:00:00Z")
    ignored = add_series(fake, 80012, "Abandoned", monitored=False)
    add_episode(fake, ignored, 1, 1, "2026-11-12T09:00:00Z", monitored=True)
    day = on(cal(client, "2026-11-10", "2026-11-14"), "2026-11-12")
    assert [i["title"] for i in day] == ["Zebra", "Afternoon", "Evening", "Aardvark", "Abandoned"]
    assert [i["monitored"] for i in day] == [True, True, True, False, False]


def test_calendar_marks_specials_premieres_and_finales(client, fake):
    """KB-33: specials (season 0) are marked so the frontends can hide them."""
    configure(client)
    show = add_series(fake, 80013, "Marked")
    add_episode(fake, show, 0, 3, "2026-11-11T20:00:00Z", title="Behind the Scenes")
    add_episode(fake, show, 3, 1, "2026-11-12T20:00:00Z")
    add_episode(fake, show, 3, 10, "2026-11-13T20:00:00Z", finaleType="season")
    body = cal(client, "2026-11-10", "2026-11-14")
    special, premiere, finale = entries(body)
    assert special["special"] is True and special["season"] == 0 and special["episode_title"] == "Behind the Scenes"
    assert premiere["special"] is False and premiere["premiere"] == "season" and premiere["finale"] is None
    assert finale["finale"] == "season" and finale["premiere"] is None
    pilot = add_series(fake, 80014, "Brand New")
    add_episode(fake, pilot, 1, 1, "2026-11-12T21:00:00Z")
    client.app.state.gateway.calendar_cache.clear()
    assert next(i for i in entries(cal(client, "2026-11-10", "2026-11-14")) if i["id"] == 80014)["premiere"] == "series"


def test_calendar_entries_carry_status(client, fake):
    """KB-36: an entry's status is the gateway's live snapshot, so a request or download shows straight away."""
    configure(client)
    m = add_movie(fake, 5008, "Tracked", inCinemas="2026-11-12T00:00:00Z")
    gw = client.app.state.gateway
    client.portal.call(gw.refresh_status, True)
    item = entries(cal(client, "2026-11-10", "2026-11-14"))[0]
    snap = client.get("/v1/status", headers=AUTH).json()
    assert item["status"] == snap["items"]["movie:5008"] and item["status"]["state"] == "searching"
    fake.radarr_queue.append({"id": 9001, "movieId": m["id"], "size": 100, "sizeleft": 40, "status": "downloading",
                              "trackedDownloadStatus": "ok", "trackedDownloadState": "downloading"})
    client.portal.call(gw.refresh_status)
    item = entries(cal(client, "2026-11-10", "2026-11-14"))[0]    # calendar data cached, status is not
    assert item["status"]["state"] == "downloading" and item["status"]["progress"] == 0.6


def test_calendar_is_fetched_on_demand_never_polled(client, fake):
    """Only an open Calendar screen asks for it: the poller's refresh and idle health check never do."""
    configure(client)
    gw = client.app.state.gateway
    client.portal.call(gw.refresh_status, True)
    client.portal.call(gw.check_reachability)
    assert not any("/calendar" in c for c in fake.calls)


def test_calendar_asks_both_servers_for_unmonitored_titles_and_series(client, fake):
    configure(client)
    show = add_series(fake, 80015, "Quiet", monitored=False)
    add_episode(fake, show, 1, 1, "2026-11-12T20:00:00Z", monitored=False)
    add_movie(fake, 5009, "Unwatched", monitored=False, inCinemas="2026-11-12T00:00:00Z")
    body = cal(client, "2026-11-10", "2026-11-14")
    assert sorted(i["title"] for i in entries(body)) == ["Quiet", "Unwatched"]   # needs unmonitored=true
    series = next(i for i in entries(body) if i["type"] == "series")
    assert series["poster"] == "https://artworks.thetvdb.com/show.jpg"            # needs includeSeries=true


def test_dev_fake_calendar_has_something_to_show(tmp_path):
    """./dev.sh: the simulated servers give a calendar with every kind of entry, around today."""
    fake = FakeArr(simulate=True)
    with TestClient(create_app(tmp_path, fake.transport(), False, TOKEN)) as c:
        configure(c)
        today = dt.datetime.now(dt.timezone.utc).date()
        body = c.get("/v1/calendar", params={"start": (today - dt.timedelta(days=7)).isoformat(),
                                             "end": (today + dt.timedelta(days=30)).isoformat()}, headers=AUTH).json()
        items = entries(body)
        assert {"cinema", "digital", "physical"} <= {i["kind"] for i in items}
        assert any(i["type"] == "series" and i["count"] > 1 for i in items)          # a season bundle
        assert any(i["special"] for i in items) and any(not i["monitored"] for i in items)
        assert any(i["time"] == "01:30" for i in items)                              # 01:30 UTC episode
