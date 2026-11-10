# Edge cases

✅ = handled and covered by a test (`backend/tests/test_gateway.py`, `web/tests/`). 🟢 = handled in code without an automated test (iOS has no test target yet; the web UI was checked by hand in a phone-sized browser). ⏳ = known and not handled yet.

Unless a row says otherwise, frontend behaviour applies to both the web app and the iPhone app.

## Setup and connections
| Case | Behaviour | |
|---|---|---|
| Only Radarr, or only Sonarr, is configured | The other section shows a "Connect Sonarr/Radarr" card. Discovery, status and requests work for the configured one. Requests for the missing one return a clear config error | ✅ `test_only_radarr_configured_works` |
| Setup: either server can be skipped | Two separate setup screens (Radarr, then Sonarr), each with Skip. You can finish with neither connected | 🟢 |
| Remove a connection later | `DELETE /v1/config/{service}`. The other server keeps working | ✅ `test_remove_connection` |
| Wrong key, wrong host, wrong port, wrong URL base, wrong app (a Sonarr URL in the Radarr field), TLS failure, a `.ts.net` name that won't resolve, a non-JSON proxy login page | A specific message that says what to fix | ✅ `test_connection_errors_are_actionable` |
| Change the URL without retyping the key | The saved key is reused | ✅ |
| Self-signed certificate on an Arr app | `ARR_VERIFY_TLS=0` | 🟢 |
| Gateway token rotated | The app signs out to pairing, keeps the address, and explains why | 🟢 |
| Phone offline or gateway down | Cached catalogue and statuses stay visible, an offline banner shows, and polling retries every 15 s | 🟢 |
| Move the install to another machine | Copy the data folder plus `.env`, or `phonarr export-data` / `import-data`. Export is a consistent SQLite backup while running, without API keys or the token | ✅ `test_export_imports_into_another_project…`, `test_export_is_consistent…` |
| Import over existing data, or a damaged, foreign or unsafe zip | Refused without `--replace`; manifest, checksums and paths are checked before anything is written | ✅ `test_import_refuses…`, `test_import_rejects…` |
| A data path in settings.json outside the data folder | Startup and the CLI stop with the file and key | ✅ `test_config_path_outside_data_folder…` |
| Old compose file still mounts `/data` | The container stops and says to change the mount to `/app/data` | 🟢 |

## Requests
| Case | Behaviour | |
|---|---|---|
| Repeated taps, network retries, two phones at once | A per-title lock, a DB unique constraint and an idempotency key. One Arr record | ✅ `test_repeated_taps…`, `test_concurrent…` |
| Title already in Radarr or Sonarr (added outside the app) | Linked, not added again | ✅ |
| Arr says "already been added" (race with another client) | Re-reads the library and links | 🟢 |
| No default profile or root folder, and several exist | Config failure on the poster with a pointer to Settings. Retry works once it's fixed | ✅ |
| Default profile deleted in Arr | Falls back to the only profile if there is just one, otherwise asks for a new default | ✅ (logic shared with the above) |
| Root folder reported as inaccessible by Arr | Config error before anything is added | ✅ `test_inaccessible_root_folder…` |
| Sonarr v3 (needs a language profile) and v4 | Detected from the version | ✅ |
| One-off override | Used once and never saved | ✅ |
| One-off season choice for a show | Used once and never saved | ✅ `test_season_override_is_one_off` |
| Radarr/Sonarr can't find the ID | 404 "couldn't find this title" on details and on request. Nothing is added | ✅ `test_unknown_title_is_404…` |
| No usable default yet (several profiles, none chosen) | Details and the selection bar show the problem. + and Add open Customize Request instead of failing | ✅ gateway `test_request_plan…`, 🟢 app |
| Multi-select: some titles already in the library, some unknown, duplicates in the list | Each item succeeds or fails on its own; duplicates are sent once; failed ones stay selected so you can retry | ✅ `test_batch_request…` |
| Multi-select: a selected title gets requested elsewhere meanwhile | Dropped from the selection on the next status update | 🟢 |

## Status and progress
| Case | Behaviour | |
|---|---|---|
| Download finished but not imported | "Importing", never "complete" | ✅ |
| Import blocked (Arr `importPending` with a warning) | Shown as Failed with Arr's own message | ✅ |
| Several queue items for one film (an old grab and a retry) | Shows the most important state | 🟢 |
| Size unknown (`size` = 0) | No percentage, an indeterminate bar | ✅ |
| Library items with no TMDB or TVDB ID (manual imports) | Ignored | ✅ |
| Series monitoring "future only" with nothing aired | "Waiting for new episodes" | 🟢 |
| Title deleted from Arr after the request | Failed: "No longer in Radarr. Retry will add it again." Retry re-adds only after confirming it's gone | 🟢 |
| Arr unreachable | Last known status kept and marked `stale`. Banner shown | ✅ |

## Retry
| Case | Behaviour | |
|---|---|---|
| Failed download | Clears the failed queue item (blocklisting the release) and searches again on the **existing** record | ✅ |
| Repeated Retry taps | 30-second cooldown, so it doesn't hammer the indexers | ✅ |
| Retry while Arr is down | 503 with a message. No cooldown is set, so you can try again | ✅ |

## Discovery sources
| Case | Behaviour | |
|---|---|---|
| Malformed `titles.json` | Keeps the last good list and logs an error | 🟢 |
| Junk entries in `titles.json` | Skipped and logged | ✅ |
| An ID in `titles.json` Radarr/Sonarr can't find | Hidden and logged | 🟢 |
| Excluded titles in Radarr's Discover feed | Hidden | ✅ |
| Search term matches only an alternate title | Lookup results are kept even when the shown title doesn't contain the term | ✅ |
| Radarr has no import lists and an empty library | Discover shows search hints instead of an empty screen | 🟢 |

## Web frontend, health and deployment
| Case | Behaviour | |
|---|---|---|
| Web app served next to the API | `/` is the web app, `/v1/*` and `/docs` still win, API still needs the token | ✅ `test_web_frontend_is_served_next_to_the_api` |
| Image or checkout without `web/` | API only; `/v1/health` says `web_frontend: false` | ✅ `test_api_only_when_web_dir_is_missing` |
| Plain HTTP over Tailscale (not a secure context) | No service worker, no `crypto.randomUUID` (fallback UUID for idempotency keys), no clipboard API (console falls back to `execCommand`), debug report downloads instead of sharing | 🟢 |
| Old web files cached after an update | Files are `Cache-Control: no-cache` with ETag; the service worker is network-first | 🟢 |
| Web tab or home screen app hidden | Polling stops (`visibilitychange`), restarts on return | 🟢 |
| Gateway token rotated (web) | Back to sign-in with an explanation | 🟢 |
| localStorage full or blocked (private mode) | Token lasts for the tab; catalogue cache skipped | 🟢 |
| Radarr or Sonarr down | `/v1/health` stays `ok: true` (Docker stays healthy) with `status: degraded` and that server `reachable: false` | ✅ `test_health_reports_services_without_secrets` |
| `/v1/health` is unauthenticated | No URLs, keys or error text in it | ✅ same test |
| Web debug report | Browser log sent to `POST /v1/debug/bundle`, redacted again on the gateway | ✅ `test_debug_bundle_includes_frontend_log_redacted` |
| New `/v1` route not in the API console | Test fails and names it | ✅ `test_api_console_lists_every_route` |
| Client-side filters drift from the gateway's | Same cases checked in node | ✅ `web/tests/filters.test.mjs` |
| Bind-mounted data folder created by Docker as root | `boot.py` chowns it to `PUID:PGID` and drops root before the gateway starts; checked with the real image | 🟢 tested by hand in Docker |
| Data folder the gateway user can't write (`user:` set) | One line naming the `chown` to run, exit 1, no traceback loop | ✅ `test_data_dir_check_explains_permissions`, `test_boot_checks_data_dir_and_leaves_owned_folders_alone` |
| Radarr or Sonarr goes down while no frontend is open | Idle reachability check every 5 min; `/v1/health` turns `degraded` with `down_for_s` | ✅ `test_health_has_fresh_reachability_when_idle` |
| Outage cause and timeline | Each failed Arr call logged with its reason; one DOWN line, hourly reminders, an UP line with the downtime | ✅ `test_outage_reason_and_transitions_are_logged` |
| Unexpected exception in a route | Logged with traceback and request id; JSON 500 with the request id | ✅ `test_unexpected_error_is_json_with_request_id_and_logged` |
| 401, 404 and validation errors | Same `{"error": …}` envelope with the field in the message | ✅ `test_every_error_uses_the_same_envelope` |
| A poll cycle raises | Logged, retried after `gateway.poller_error_backoff`; health shows `last_poll_error_s_ago` | ✅ `test_poller_survives_errors` |
| `localhost` or a stopped container entered as the Radarr URL | Docker-specific explanation | ✅ `test_docker_specific_connection_hints` |
| Radarr answers 5xx with its own explanation | Message kept ("Radarr returned HTTP 503: …"); API check reports it as a warning, not an API change | ✅ `test_arr_server_error_keeps_its_explanation` |
| Token or API keys as Docker secrets | `*_FILE` variables | ✅ `test_secrets_can_come_from_files` |
| JavaScript error in the web app | Logged (and in the debug report); a crash while rendering shows a panel with Reload and Download debug report | ✅ `web/tests/config-log.test.mjs`, 🟢 checked in a browser |
| iPhone app crashes | MetricKit report saved into the log folder on the next launch, included in the debug report | 🟢 not compiled yet |
| GHCR package private | `docker compose pull` is denied until the server logs in (docs/DEPLOYMENT.md §2.3) | 🟢 documented |

## Calendar
| Case | Behaviour | |
|---|---|---|
| Film release date west of UTC (Radarr sends `2026-11-12T00:00:00Z`) | Shown on the 12th everywhere: release dates are days, never converted | ✅ `test_calendar_movie_dates_are_days_not_instants`, `calendar.test.mjs` |
| Episode at 02:00 UTC seen from New York or Tokyo | 21:00 the day before in New York, 11:00 the same day in Tokyo | ✅ `test_calendar_episode_times_follow_viewer_timezone` |
| Late episode at the edge of the range | Radarr/Sonarr are asked for the range padded by a day each side, then entries are filtered to the local range | ✅ `test_calendar_range_edges_keep_late_episodes` |
| Episode without `airDateUtc` | Its `airDate`, with no time | ✅ `test_calendar_episode_without_utc_time_uses_air_date` |
| Unknown or junk `tz` | UTC, `tz_fallback: true`, a notice in the frontends; never a 500. `tzdata` is installed so slim images know every zone | ✅ `test_calendar_unknown_timezone_falls_back_to_utc` |
| Daylight saving weekends (Europe/London, America/New_York, both directions) | Every day once, in order; times follow the offset change | ✅ `test_calendar_days_step_by_calendar_day_over_dst_*`, `calendar.test.mjs` |
| Bad `start`/`end`, end before start, more than 92 days | 422 in the usual error envelope | ✅ `test_calendar_default_range_and_validation` |
| Connection changed or removed | That server's cached calendar is dropped straight away | ✅ `test_calendar_follows_connection_changes` |
| One server down, or both | The other's entries plus that server's error in `services`; both down is still a 200. Errors are never cached | ✅ `test_calendar_partial_failure_keeps_other_service` |
| A season released at once | One entry per show, season and day ("8 episodes") | ✅ `test_calendar_collapses_episode_bundles` |
| Episode monitored but series unmonitored | Unmonitored (faded, listed last) | ✅ `test_calendar_orders_monitored_first_and_counts_series_monitoring` |
| Requested or downloading while the Calendar is open | Status comes from the live snapshot, not the cached calendar | ✅ `test_calendar_entries_carry_status` |
| The poller | Never calls `/api/v3/calendar` | ✅ `test_calendar_is_fetched_on_demand_never_polled` |
| Calendar left open overnight, or resumed days later | Today moves and the list reloads | ✅ web (`calendar.test.mjs`), 🟢 iPhone |
| Opening on Today without a jump | Placed before the first paint (checked frame by frame in Chromium, iPhone 13 viewport) | 🟢 web checked by hand, iPhone not compiled |

## Not handled yet
- ⏳ Push notifications when a download finishes. This needs the paid account plus APNs, or a self-hosted ntfy.
- ⏳ Several Radarr or Sonarr instances (for example a 4K instance).
- ⏳ Several users or households with separate permissions.
- ⏳ Episode-level requests. Only whole-series requests with a season policy are supported.
- ⏳ An iOS unit test target (`AppModelTests`). The SwiftUI code compiles only on macOS, so it hasn't been run yet. `SystemStatusView.swift` is new and also uncompiled.
- ⏳ Automated browser tests for the web UI committed to the repo (it was walked through with Playwright by hand: sign-in, Discover, grids, filters, search, + / Customize, multi-select, details, Activity, Settings, System status, API console, debug report).
- ⏳ Web push notifications (would need HTTPS and a push service).
