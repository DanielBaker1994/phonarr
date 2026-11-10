# Known bugs from similar apps: PRD

What bugs have apps like Phonarr already hit, does Phonarr have the same risk, and what test proves it either way? Each item below is written **test first**: the test is added before any fix, its first result is recorded (fails or passes), and only failing items get a fix.

Status column: **FAIL → fixed** means the test failed on the first run and the fix made it pass. **PASS** means Phonarr already handled it and the test now guards it. **New** means the item is a requirement for a new feature (the Calendar), written before the feature existed. 🟢 means the fix is in iOS code, which can't be compiled or tested on Linux, so it was reviewed by hand.

## Sources

| App | What it is | What was read |
|---|---|---|
| [Ruddarr](https://github.com/ruddarr/app) (MIT) | Native iOS client for Radarr and Sonarr, with a calendar and notifications. The closest app to Phonarr | `CHANGELOG.md` (every "Fixed" entry), 1,885 commits of git log, `Models/Calendar.swift` |
| [Seerr](https://github.com/seerr-team/seerr) (Overseerr/Jellyseerr) | The request app Phonarr is inspired by; talks to Radarr and Sonarr | 3,238 commits, every `fix(...)` commit touching requests, dates, retries, caching, timeouts |
| [ntfy Android](https://github.com/binwiederhier/ntfy-android) | Push notification client (long-lived connections, offline) | Fix commits on reconnects, timestamps, duplicates |
| [Gotify Android](https://github.com/gotify/android) | Push notification client | Fix commits on reconnects, duplicates after reconnect, stale times on resume |

Ideas only: no code or assets were copied from any of them.

## The bug classes that keep coming back

1. **The network drops in the middle of a write.** The client can't tell if the server acted. Apps either show a false failure that never clears, or retry and create a duplicate.
2. **Dates that are really days get treated as instants.** Radarr's release dates are calendar days sent as `…T00:00:00Z`. Shown in a timezone west of UTC they land on the previous day (Seerr #3273, Ruddarr "Fixed issue displaying the wrong date in the calendar").
3. **"Today" goes stale.** Apps left open overnight or resumed from the background keep yesterday's highlight and relative times (Ruddarr fixed this three times; Gotify "update date-times on app resume").
4. **Background loops multiply.** A refresh started while another is in flight leaves two loops running, and only one can be stopped (Ruddarr "redundant network requests at launch").
5. **Caching a bad answer.** A transient empty or invalid response is cached or acted on as if it were real (Seerr #3406 "do not cache invalid responses", #2302 "incorrect deletion when the media server is unreachable").
6. **Decoding is too strict.** One unknown enum value or a missing field breaks a whole screen (Ruddarr #838 "Harden decoding against deprecated and unknown API values", "Fixed dozens of decoding errors").
7. **Wall-clock time used for durations.** A clock that jumps backwards freezes caches and cooldowns.
8. **Logs that can't be lined up.** Server and app logs in different timezones, or too hard to get at when something goes wrong (Ruddarr added a bug report sheet, network diagnostics and "capture API response when decoding error occurs").

## Items

### A. Connectivity and sending

| ID | Seen in | Risk in Phonarr | Test | First run | Fix |
|---|---|---|---|---|---|
| KB-01 | Ruddarr "monitor toggles not reverting on failure"; ntfy connection-lost dialog fixes | The phone loses the connection while `POST /v1/requests` is in flight. Both frontends mark the title **Failed** locally. If the gateway never got the request, its snapshot doesn't change, so polls return 304 and the false Failed never clears. Tapping + again goes to Retry, which answers 404 "nothing to retry". | `web/tests/store.test.mjs`: add() with fetch rejecting leaves the title requestable, and the next tap sends a request, not a retry; `test_known_bugs.py::test_retry_of_a_title_the_gateway_never_saw_adds_it` | FAIL → fixed | Transport failures restore the previous status and say "couldn't reach the gateway"; the next tap reuses the same idempotency key. Same change in `AppModel.swift` 🟢. Gateway: Retry on a title it has never seen and that isn't in Arr adds it with the defaults instead of a 404 |
| KB-02 | Seerr #2375/#2442 (Arr timeouts) | Radarr creates the movie but the gateway's `POST /api/v3/movie` times out. The gateway records `unavailable`, and because the library cache isn't dropped on failure the title shows Failed for up to 10 minutes although Radarr has it. | `test_known_bugs.py::test_add_that_timed_out_but_landed_is_linked` | FAIL → fixed | On a transport failure during the add, re-read the library once; if the title is there, link it |
| KB-03 | Seerr #3380/#3377 (serialize requests for the same title), Radarr's "already been added" validation | Another client adds the title between our library check and our POST. Radarr answers 400 with a validation array (`[{"propertyName":"TmdbId","errorMessage":"This movie has already been added"}]`). Handled in code, never tested. | `test_known_bugs.py::test_already_added_by_someone_else_is_linked` (Radarr and Sonarr) | PASS | None needed (the existing "already" handling links it) |
| KB-04 | Ruddarr "Fixed redundant network requests when opening views and at launch" | Web: `refreshNow()` while a poll is in flight starts a second loop; the old one reschedules itself when its fetch returns. Two loops poll, and `stopPolling()` only stops one, so polling carries on while the tab is hidden. | `web/tests/store.test.mjs`: refreshNow twice during a slow poll, then hide the tab: no fetch after hiding | FAIL → fixed | A loop generation number: a loop whose generation is old exits instead of rescheduling |
| KB-05 | Ruddarr "ignore cancellation errors", "Fixed release search briefly showing an empty state" | iOS: a search cancelled by typing (`.task(id:)`) returns `CancellationError` text as the search error, and the spinner flag is left on when the task is cancelled. | Code review (no iOS test target) | 🟢 (code review: both problems confirmed) | Ignore cancellation in `search`, reset the spinner on every exit 🟢 |
| KB-06 | Seerr #3406 "do not cache invalid responses" | Radarr/Sonarr lookup answering something that isn't a list (an error object, or `null` during startup). `search()` slices it and caches the result. | `test_known_bugs.py::test_search_does_not_cache_or_crash_on_invalid_lookup` | FAIL → fixed | Treat a non-list as an error, never cache it |
| KB-07 | Seerr #2302 "prevent incorrect season deletion when media server is unreachable", #3399 "confirm orphan candidates before declining" | Radarr answers the library call with `[]` once (restarting, database locked). Every requested title flips to "No longer in Radarr. Retry will add it again." | `test_known_bugs.py::test_one_empty_library_answer_does_not_orphan_requests` | FAIL → fixed | A title is only "No longer in Radarr" after two refreshes in a row agree it's gone |

### B. Time and timezones

| ID | Seen in | Risk in Phonarr | Test | First run | Fix |
|---|---|---|---|---|---|
| KB-10 | General (VM resume, NTP corrections) | TTLs, the Retry cooldown and the poll interval use `time.time()`. If the clock moves back, caches look fresh for hours and Retry is refused. | `test_known_bugs.py::test_clock_going_backwards_does_not_freeze_retry_or_caches` | FAIL → fixed | `time.monotonic()` for every duration; `time.time()` only for timestamps people see |
| KB-11 | Gotify/ntfy timestamp fixes; correlating logs | Gateway log lines use local time with no offset (`2026-11-10 08:00:00,123`); the web and iPhone logs use UTC (`…Z`). In a debug report the two sides look hours apart. | `test_gateway.py::test_log_lines_are_utc_iso` | | Gateway lines in UTC ISO 8601 with milliseconds and `Z`, like the frontends |
| KB-12 | Seerr #3273 "anchor day diff to UTC midnight", Ruddarr "wrong date in the calendar" | Calendar: Radarr's `inCinemas`, `digitalRelease`, `physicalRelease` are days sent as midnight UTC. Converting them to local time puts them on the previous day west of UTC. | `test_calendar_movie_dates_are_days_not_instants` (gateway), `web/tests/calendar.test.mjs` | New | Movie dates are taken as the date part, never converted |
| KB-13 | Ruddarr calendar (`airDateUtc`) | Calendar: an episode at 21:00 in New York is 02:00 UTC the next day. Asking Sonarr for whole UTC days drops it at the edges, and bucketing by UTC puts it on the wrong day. | `test_calendar_episode_times_follow_viewer_timezone`, `test_calendar_range_edges_keep_late_episodes` | New | Ask Sonarr for the range padded by a day; bucket `airDateUtc` in the viewer's IANA timezone |
| KB-14 | Common Python-in-Docker failure (`ZoneInfoNotFoundError` on slim images) | The calendar needs IANA timezones; slim images may have no tz database. A bad `tz` from a browser must not 500. | `test_calendar_unknown_timezone_falls_back_to_utc` | New | `tzdata` in requirements.txt; an unknown zone falls back to UTC and says so in the response |
| KB-15 | Ruddarr "calendar day highlight not updating at midnight", "current date not updating", "check calendar date on becoming active", "refresh calendar when resuming"; Gotify "update date-times on app resume" | Calendar left open overnight or resumed the next day keeps yesterday as Today. | `web/tests/calendar.test.mjs` (today is recomputed from the clock, not stored) | New | Recompute today on `visibilitychange` / scene phase active and on a timer at the next local midnight; reload when the day changed |
| KB-16 | Common in JavaScript date code | Building a list of days by adding 86,400 s skips or repeats a day across a daylight saving change. | `web/tests/calendar.test.mjs` and gateway test over the October/March DST weekends | New | Step by calendar day, not by seconds |

### C. Data and decoding

| ID | Seen in | Risk in Phonarr | Test | First run | Fix |
|---|---|---|---|---|---|
| KB-20 | Ruddarr #838, "Fixed dozens of decoding errors" | Arr payloads with `null` where a list or object is expected (`images`, `genres`, `ratings`, `statistics`, `seasons`) or a new queue `status` / `trackedDownloadState`. One bad item must not break Discover or status. iOS `MediaState` already decodes unknown values to `.unknown`. | `test_known_bugs.py::test_nulls_and_unknown_values_do_not_break_normalising` | FAIL → fixed | `null` episode counts in `statistics` crashed the status refresh; now read as 0. Nulls in lists/objects and unknown queue states already passed |
| KB-21 | Seerr #3351 "phantom specials block season request", Ruddarr "hide specials in calendar" | Season 0 (specials) counted as the first or latest season for the season choice. | `test_known_bugs.py::test_specials_never_count_as_first_or_latest_season` | PASS | None needed |
| KB-22 | Ruddarr "Capture API response when decoding error occurs" | When Radarr/Sonarr answer with something that isn't JSON, the log should name the call, status and the start of the body (redacted), so a report is enough to diagnose it. | `test_known_bugs.py::test_non_json_answer_is_logged_with_a_snippet` | FAIL → fixed | `arr.py` logs a WARNING with the call, status, content type and the first `arr.log_body_chars` characters of the body, redacted |
| KB-23 | Seerr #3699 "stop background cache refresh from logging credentials" | An Arr error message that contains a URL with `apikey=` reaching a log line. | `test_known_bugs.py::test_background_errors_never_log_keys` | PASS | None needed (`RedactingFormatter` masks `apikey=` values and registered keys) |

### D. Calendar (new feature, from Ruddarr's changelog)

Every Ruddarr calendar fix becomes a requirement here, tested before the Calendar exists.

| ID | Seen in Ruddarr | Requirement | Test |
|---|---|---|---|
| KB-30 | "Reset calendar events after modifying instances", "calendar/queue not updating instances when changed" | Removing or changing a Radarr/Sonarr connection drops that service's calendar entries straight away (no stale cache) | `test_calendar_follows_connection_changes` |
| KB-31 | "Improved calendar error handling", "hide calendar error when list isn't empty" | One service down: the other's entries still show, with a small notice naming the one that failed. Never a full-screen error when there is something to show | `test_calendar_partial_failure_keeps_other_service` |
| KB-32 | "Collapse episodes bundles in calendar" | A season dropped all at once shows as one entry ("8 episodes") per show per day | `test_calendar_collapses_episode_bundles` |
| KB-33 | "Support hiding specials in calendar" | Specials are marked so the frontend can hide them (a filter, remembered per device) | gateway test + `calendar.test.mjs` |
| KB-34 | "Fade unmonitored items", "Sort monitored calendar items before unmonitored", "Persist Monitored filter", "Fixed checking series monitoring status" | Unmonitored entries are faded and listed after monitored ones; series monitoring counts as well as episode monitoring | gateway test + `calendar.test.mjs` |
| KB-35 | "Fixed calendar loading on cold starts", "Smoother initial calendar tab load", "Avoid initial calendar scroll" | The calendar opens on today without a jump, and a slow first load shows a placeholder, not an error | Checked by hand in a phone-sized browser |
| KB-36 | "Update events after refreshing calendar data", "Fixed search results not updating after adding media" | Requesting a title from the calendar updates its entry (status comes from the same snapshot as everywhere else) | `test_calendar_entries_carry_status` |

## Debug features (from Ruddarr's bug report sheet, network diagnostics and decode-failure capture)

Settings → Debug gets a **Logs** screen, the same in both frontends:

- The last 1,000 lines (configurable) of the gateway log and of this device's own log, merged by time into one list. Both sides write UTC (KB-11), so they line up.
- Filters: source (Gateway, This device, Both), level (Everything, Warnings and errors), and text search.
- Tap a request id to see every line for that call on both sides.
- Copy or download what's shown. Everything is redacted on the gateway and again on the device.
- A header with what usually matters first: the gateway's version and timezone, the device's timezone, and the clock difference between device and gateway (from the HTTP `Date` header), so a wrong phone clock is obvious.

API: `GET /v1/debug/logs?lines=&level=&q=&rid=` returns parsed, redacted lines from the current and previous daily `gateway.log`.

## Results

Filled in as each test is written and run (first run before any fix).

- **KB-01 FAIL → fixed.** `store.test.mjs`: after `add()` with `fetch` rejecting, the title was `'failed'`, expected `'not_requested'` ("title shown as failed after a lost connection"). Gateway side, `test_retry_of_a_title_the_gateway_never_saw_adds_it`: `POST /v1/requests/movie/961/retry` answered 404 "Nothing to retry for this title.". Fixed: a transport error during add restores the previous status, toasts `errors.add_unreached` and keeps the idempotency key for the next tap (web `store.js`, iPhone `AppModel.swift` 🟢); Retry on a title with no request record that isn't in Arr now adds it with the defaults (404 only when Arr can't find the title). No existing test asserted the old 404; the unused `errors.nothing_to_retry` message was removed.
- **KB-02 FAIL → fixed.** The add answered 503 "Radarr did not respond in time" although the fake Radarr had saved the movie. Fixed: on an `unavailable` error from the add POST, `_submit` re-reads the library once and links the record if it's there (logged as `log.add_landed`).
- **KB-03 PASS.** Radarr and Sonarr both answering 400 `[{"propertyName": …, "errorMessage": "This … has already been added"}]` after another client's add: linked to that record, one Arr record, not Failed. The fake Sonarr now rejects duplicates like the real one.
- **KB-04 FAIL → fixed.** After `refreshNow()` twice during a slow poll, one poll interval later two `/v1/status` fetches went out ("two loops are polling", 2 !== 1). Fixed: a poll generation number bumped by `refreshNow`/`stopPolling`; a loop from an older generation exits instead of rescheduling, so hiding the tab stops all polling.
- **KB-05 🟢.** Code review confirmed both problems: `AppModel.search` returned the `CancellationError` text as the search error, and `runSearch` returned early after setting `searchingRemote = true`, leaving the spinner on. Fixed: `search` ignores `CancellationError`; `runSearch` tracks the newest run and clears the spinner on every exit of it.
- **KB-06 FAIL → fixed.** A lookup answering `{"message": "Database is locked"}` gave a 500 (`KeyError: slice(None, 40, None)` in `search`); `null` was cached as an empty result. Fixed: `_lookup_list` treats any non-list as an `unavailable` error (shown under the search heading, never cached), and `lookup_raw` never caches an error object.
- **KB-07 FAIL → fixed.** One `[]` library answer turned the requested film into `failed` / "No longer in Radarr. Retry will add it again." (expected `searching`). Fixed: a per-title count of successful refreshes in a row without it; only `gateway.missing_confirmations` (2) in a row show "No longer in …", until then the previous status stays and the library cache is dropped so the next cycle reads it fresh.
- **KB-10 FAIL → fixed.** With the wall clock an hour back and 31 s really passed, Retry was refused ("the cooldown is measured on the wall clock", 1 search instead of 2). Fixed: `time.monotonic()` for every TTL, cooldown, poll timing and health duration in `service.py` and `main.py` (and the compat duration); `time.time()` stays for shown/stored timestamps, and stored ones (metadata cache, compat spec, last compat run) treat a time "in the future" as expired.
- **KB-20 FAIL → fixed.** `null` lists/objects and unknown queue states were fine, but `statistics: {"episodeFileCount": null, "episodeCount": null}` crashed the status refresh (`TypeError: '>' not supported between instances of 'NoneType' and 'int'`). Fixed: counts read as `or 0`.
- **KB-21 PASS.** Specials are never monitored for "first" or "latest" (also when listed last), and `season_count` leaves them out.
- **KB-22 FAIL → fixed.** For an HTML login page answering `/api/v3/queue/details` with 200, the only WARNING was "Radarr is DOWN: … answered, but not with the Radarr API (got non-JSON)", without the call, status or body. Fixed: `log.arr_non_json` names service, method, path, status, content type and the start of the body, redacted before it's logged.
- **KB-23 PASS.** A 500 whose message carries `apikey=…` (an unregistered indexer key and Radarr's own key) never reached a formatted log line in refresh or the idle check. (The test's own handler first lacked the request-id filter; that was a test bug, fixed before recording the result.)
