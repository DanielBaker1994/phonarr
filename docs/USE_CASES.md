# Use-case map: every screen, path and state

This is the reference for how the app should behave in each place you can end up. Each row has an ID (used by the clickable prototype's scenario list), what you do, what should happen, where it lives in code, and its status.

Status: ✅ built and matches · 🔧 gap found while writing this map and fixed in this pass · 📱 built, needs a check on a real iPhone (layout or gesture behaviour that can't be compiled here) · ⏳ open gap.

Every row applies to **both frontends** (the web app and the iPhone app) unless it says otherwise. Code paths name the iPhone file under `ios/Phonarr/`; the web app has the same component in the matching file under `web/js/`:

| iPhone (`ios/Phonarr/`) | Web (`web/js/`) |
|---|---|
| `Core/APIClient.swift` | `api.js` |
| `Core/AppModel.swift` | `store.js` |
| `Core/Filters.swift` | `filters.js` |
| `Core/Models.swift` | `models.js` |
| `Core/AppLog.swift` | `log.js` |
| `PhonarrApp.swift` (RootView, MainTabs, Toast) | `main.js`, `router.js` |
| `Views/BrowseViews.swift` | `views/browse.js` (grids, Discover), `views/components.js` (PosterCell, PosterRow, banners) |
| `Views/PosterCard.swift` | `views/components.js` (PosterCard, badges, progress) |
| `Views/TitleDetailView.swift` (+ CustomizeRequestSheet) | `views/detail.js` (+ CustomizeSheet) |
| `Views/FilterViews.swift` | `views/filters.js` |
| `Views/SimilarViews.swift` | `views/similar.js` |
| `Views/ActivityView.swift` | `views/activity.js` |
| `Views/SettingsViews.swift` | `views/settings.js` |
| `Views/CompatView.swift` | `views/compat.js` |
| `Views/SystemStatusView.swift` | `views/status.js` |

Paths starting with `backend/` are the gateway. On the web, "tap" also means click and "long-press" also means right-click.

## 1. How requesting works (the short version)

| You do | What happens | Popup? |
|---|---|---|
| Tap a poster | Opens its details | No |
| Tap the round **+** on a poster | Adds it now with your defaults. Badge turns to a spinner, then Searching, then a progress bar | No |
| Tap **Add to Library** on details | Same as + | No |
| Read the line under Add to Library | "Uses HD-1080p · /movies" (TV also shows the season choice). This is exactly what a tap will send | — |
| Tap **Change** next to it | Customize Request sheet: quality profile, root folder, seasons (TV). Applies to this one request only | Sheet, only when you ask |
| Long-press a poster | Add to Library (shows the profile it will use), Customize Request…, Select | Menu |
| **Select** in Movies or TV Shows, tick posters, tap **Add N** | All of them are added in one go with your defaults, no popup per title | No |
| Tap **Options…** in the selection bar | One Customize sheet for the whole batch | Sheet, only when you ask |
| No default set yet and several profiles exist | + / Add / Add N open Customize once, so nothing fails. The plan line says "Choose a default…" with **Choose for this one** and **Set defaults** | Sheet, instead of a failure |
| Change your defaults | Settings → Request defaults, or "Set defaults" / "Change my defaults instead" wherever a plan is shown | — |

## 2. Launch and pairing

On the web, pairing is **sign-in with the token only**: the page is served by the gateway, so its address is already known (`SignInView` in `views/settings.js`). L2 does not apply.

| ID | Situation | Expected | Code | |
|---|---|---|---|---|
| L1 | First launch | Welcome + gateway address + token. Connect tests the token against `/v1/config` before saving | `Views/SettingsViews.swift` `OnboardingView`, `GatewayPairingView`; `AppModel.pair` | ✅ |
| L2 | Address without `http://` | `http://` added | `AppModel.pair` | ✅ |
| L3 | Wrong token | "The gateway rejected the access token…" and nothing saved | `APIClient.send` 401 | ✅ |
| L4 | Gateway unreachable | "Can't reach the gateway. Is the container running…" / Tailscale hint for DNS | `APIClient.send` | ✅ |
| L5 | Token rotated on the server later | Back to pairing with the address kept and an explanation | `AppModel.handleAuthFailure` | ✅ |
| L6 | Reinstall, both servers already connected on the gateway | Setup steps skipped | `AppModel.pair` | ✅ |
| L7 | Cold start | Cached posters and statuses show at once, then refresh | `AppModel.loadCachedCatalog` | ✅ |

## 3. First-run setup

| ID | Situation | Expected | Code | |
|---|---|---|---|---|
| S1 | Step 1 Radarr | URL + key, Test Connection, Save and continue, or Skip | `SetupFlowView`, `ServiceConnectionView` | ✅ |
| S2 | Test fails | Specific message (wrong key, URL base, Sonarr in the Radarr field, `.ts.net` from Docker…) | `backend/app/arr.py` | ✅ |
| S3 | Step 2 Sonarr | Same, Skip allowed | `SetupFlowView` | ✅ |
| S4 | Summary | Which servers are connected, **request defaults** (profile, folder, seasons) so the first + works, Start browsing | `SetupFlowView.summary` + `DefaultsSection` | 🔧 defaults were not shown here, so the first + on a multi-profile Radarr failed |
| S5 | Nothing connected | Allowed. Tabs show Connect cards | `ConnectServerCard` | ✅ |

## 4. Discover tab

| ID | Situation | Expected | Code | |
|---|---|---|---|---|
| D1 | Normal | Rows: In progress, Recommended by Radarr, Films to add, TV to add, then generated shelves (Highest rated, decades, genres, Under 90 min) | `Views/BrowseViews.swift` `DiscoverView` | ✅ |
| D2 | In progress row after a reload | Comes from Activity, so requested titles stay visible even though the catalogue now hides them | `DiscoverView.inProgress` | 🔧 was built from the catalogue only and emptied after a refresh |
| D3 | Tap poster | Details (§6) | `PosterCell` | ✅ |
| D4 | Tap + | Adds with defaults; spinner badge → Searching → progress; toast "Added to library" | `PosterCell.quickAction`, `AppModel.add` | ✅ 📱 check the + doesn't also open details |
| D5 | + while that server is unreachable | No + shown; the orange banner explains | `PosterCell.quickAction` | 🔧 + used to stay and every tap failed |
| D6 | + with no usable default | Opens Customize for that title | `PosterCell.quickAction` | ✅ |
| D7 | Long-press | Add (subtitle: "Uses HD-1080p · /movies"), Customize Request…, Retry on failed | `PosterCell.menu` | ✅ |
| D8 | Nothing to show | "Nothing to discover yet" with how to get titles: search, Radarr import lists/recommendations, `titles.json` | `EmptyCatalogView` | ✅ |
| D9 | Only one server connected | Other section replaced by its Connect card | `ConnectServerCard` | ✅ |
| D10 | Gateway offline | Banner "Showing saved results"; posters stay | `ServiceBanner` | ✅ |
| D11 | Multi-select on Discover | Not offered on rows; use See all → Select | — | ✅ by design |

## 5. Movies and TV Shows grids (and See all)

| ID | Situation | Expected | Code | |
|---|---|---|---|---|
| G1 | Browse | Poster grid with + on each, sort menu, filter sheet, removable chips with "N of M" | `MediaGridView`, `Views/FilterViews.swift` | ✅ |
| G2 | Filters | Genre (any-of), decade, runtime, minimum rating, language, studio/network, collection, certification, series status, **Found in** (titles list, import lists, Radarr recommendations, search) | `FilterSheet`, `Core/Filters.swift` | ✅ |
| G3 | Search 1 character | Local title filter only | `MediaGridView.runSearch` | ✅ |
| G4 | Search 2+ characters | Local matches, then "From Radarr search" with anything else Radarr/Sonarr can find (debounced 450 ms). Each can be added | `MediaGridView`, `AppModel.search`, `backend/app/service.py::search` | ✅ |
| G5 | Search finds nothing more | "No other matches for “…”" | `MediaGridView` | ✅ |
| G6 | Search while Radarr is down | Orange message under the search heading | `MediaGridView` | ✅ |
| G7 | Tap **Select** (or long-press → Select) | Tab bar hides; checkmark circles appear; title shows "N selected"; Select All / Deselect All; Done | `MediaGridView.toolbar` | ✅ 📱 |
| G8 | Tap a poster in select mode | Ticks it (haptic). Requested titles are dimmed and can't be ticked; failed ones can (they'll be retried) | `PosterCell` selection branch | ✅ |
| G9 | Selection bar | "Uses HD-1080p · /movies", **Options…**, **Add N** (or "Retry N" if all are failed) | `MediaGridView.selectionBar` | ✅ |
| G10 | Add N | One batch call. Toast "Added 4 · 1 already in library · 1 failed". Done if all succeeded; failed ones stay ticked | `AppModel.addBatch`, `POST /v1/requests/batch` | ✅ |
| G11 | Options… | Customize sheet for the batch ("Add 6 titles"); Add 6 sends the batch with one-off choices | `CustomizeRequestSheet` | ✅ |
| G12 | Add N with no usable default | Opens Options… instead | `selectionBar` | ✅ |
| G13 | A ticked title gets requested elsewhere | Unticked on the next status update | `MediaGridView .onChange(statuses)` | ✅ |
| G14 | Select All | Ticks every requestable title currently shown (after filters and search) | `toolbar` | ✅ |
| G15 | Server not connected | Connect card instead of the grid | `MediaGridView` | ✅ |

## 6. Title details

| ID | State | Expected | Code | |
|---|---|---|---|---|
| T1 | Not requested | Add to Library, then the plan line "Uses … · Change" and "Change applies to this request only" | `TitleDetailView.actionArea`, `RequestPlanSummary` | ✅ |
| T2 | Not requested, no usable default | Orange "Choose a default Radarr quality profile in Settings." + Choose for this one / Set defaults; Add opens Customize | `RequestPlanSummary` | ✅ |
| T3 | Defaults changed while details is open | Plan line updates | `TitleDetailView.plan` prefers `AppModel.requestPlans` | 🔧 kept showing the plan loaded with the page |
| T4 | Submitting | Disabled button with spinner | `actionArea` | ✅ |
| T5 | Queued / Searching / Downloading / Importing / Paused / Stalled | Status line with icon, % and bar where known, Arr's message ("Waiting for a release") | `StatusLine` | ✅ |
| T6 | Complete | "In library" in green, no action | `StatusLine` | ✅ |
| T7 | Failed (download) | Red box with Arr's message, Retry (clears the failed grab, searches again on the same record) | `FailureBox` | ✅ |
| T8 | Failed (config) | "Needs attention in Settings", Retry, and **Open request defaults** | `FailureBox` | 🔧 had no way to reach the fix |
| T9 | Series | Episodes by season with per-episode state and progress | `EpisodeList` | ✅ |
| T10 | More like this | Row of similar titles with a reason under each; See all → grid with "Include TV/films" toggle | `Views/SimilarViews.swift` | ✅ |
| T11 | Radarr can't find the ID | "Radarr couldn't find this title." | `backend` 404 | ✅ |
| T12 | Server down | Add disabled; banner on the tab | `actionArea` | ✅ |

## 7. Customize Request sheet

| ID | Situation | Expected | Code | |
|---|---|---|---|---|
| C1 | One film | Quality profile, Root folder, footnote "this request only", "Change my defaults instead", Request | `CustomizeRequestSheet` | ✅ |
| C2 | One show | Same plus **Seasons** (All, First, Latest, Future only) | `CustomizeRequestSheet` | 🔧 seasons couldn't be overridden per request before (gateway now accepts `season_monitor`) |
| C3 | Batch | Title "Add N titles", counts per type, Add N | `CustomizeRequestSheet` | ✅ |
| C4 | Only one profile / folder | Shown as a fixed value, not a picker | `picker`, `folderPicker` | ✅ |
| C5 | No default set | Pickers start on "Choose…"; Request disabled until both are picked | `ready` | ✅ |
| C6 | Options not loaded yet | Loads, then pre-fills from the plan | `.task` | 🔧 pre-fill ran before options arrived and left the pickers empty |
| C7 | Cancel | Nothing sent | — | ✅ |

## 8. Activity tab

| ID | Situation | Expected | Code | |
|---|---|---|---|---|
| A1 | Something downloading | Sections Needs attention / In progress / Recently added; bars move every 5 s while open | `Views/ActivityView.swift`, gateway `POLL_WATCHING` | ✅ |
| A2 | Failed row | Message + Retry right in the row | `ActivityRow` | ✅ 📱 check Retry doesn't also open details |
| A3 | Empty | "No activity" | `ActivityView` | ✅ |
| A4 | Arr down | Banner, statuses marked "(last known)" | `ServiceBanner`, `StatusLine` | ✅ |

## 9. Settings

| ID | Situation | Expected | Code | |
|---|---|---|---|---|
| X1 | Connections | Radarr and Sonarr rows with a dot (green / orange / grey), edit or remove each | `ServiceRows`, `ServiceConnectionView` | ✅ |
| X2 | Request defaults | Profile and folder per server, TV seasons. Footer explains + / Add / Add selected use these | `DefaultsSection` | ✅ |
| X3 | Discovery | "Show titles already in library" toggle, footer listing the sources | `SettingsView` | ✅ |
| X4 | API compatibility | Badge, Test API, per-feature results and what changed since last run | `Views/CompatView.swift` | ✅ |
| X5 | Debug report | Share a zip of app + gateway logs (redacted) | `DebugSection` | ✅ |
| X6 | Disconnect / Sign out | Back to pairing (iPhone) or sign-in (web, asks first) | `AppModel.unpair` | ✅ |
| X7 | System status | "Everything is up" / "a server is unreachable" / "no server connected"; gateway version, uptime, last refresh, poll interval (or the idle check interval); background poller if not running, last poll error; per server reachable, down for, last checked, version, address, error. Refreshes every 15 s. Web also links to the API console and raw `/v1/health` and lists this browser's recent warnings and errors; iPhone shows the app's version, build and commit | `Views/SystemStatusView.swift`, `web/js/views/status.js`, `backend/app/main.py::health` | ✅ web 📱 iPhone (uncompiled) |
| X8 | A screen crashes while rendering (web) | "This screen hit a bug" panel with the error, Reload and Download debug report; the tab bar stays; going to another tab clears it | `web/js/main.js` `ScreenBoundary` | ✅ web (SwiftUI has no equivalent; crashes are reported by MetricKit) |
| X8 | Home screen app (web only) | Shows how to Add to Home Screen, or "Running as a home screen app"; notes what HTTPS adds | `web/js/views/settings.js::InstallSection` | ✅ |

## 10. Background, polling and other devices

| ID | Situation | Expected | Code | |
|---|---|---|---|---|
| P1 | App backgrounded (web: tab or home screen app hidden) | Frontend stops polling; gateway slows to 60 s while downloads run, stops when idle | `AppModel.scenePhaseChanged`, web `store.js` `visibilitychange`, `Gateway.poll_interval` | ✅ |
| P2 | Back to foreground | Immediate refresh | `AppModel.refreshNow` | ✅ |
| P3 | Title added in Radarr directly | Poster badge updates on the next poll; it leaves "to add" on the next Discover refresh | status merge | ✅ |
| P4 | Title deleted in Radarr after a request | Failed "No longer in Radarr. Retry will add it again." | `backend` `refresh_status` | ✅ |

## 11. Accessibility

| ID | Situation | Expected | Code | |
|---|---|---|---|---|
| V1 | VoiceOver on a poster | "Title, year, state, percent"; actions rotor offers Add to library / Retry | `PosterCard` | ✅ 📱 |
| V2 | Select mode | Selected trait; hint "Double-tap to select" or "Already requested" | `PosterCell` | ✅ |
| V3 | Dynamic Type | Text scales; poster grid adapts | system | 📱 |

## 12. Open gaps

- ⏳ Multi-select isn't on the Discover rows (use See all). Rows are horizontal and ticking there is awkward; revisit if you miss it.
- ⏳ No per-request record of which profile was used is shown after the request. Radarr shows it; the app could add "Requested as HD-1080p" on details.
- ⏳ Editing `titles.json` from the app.
- ⏳ The iOS code has not been compiled yet in this environment (Linux). The first Xcode build may surface small type-check errors.

## 13. Web frontend: differences from the iPhone app

| Area | iPhone app | Web app | Why |
|---|---|---|---|
| Pairing | Address + token | Token only | The page comes from the gateway |
| Token storage | Keychain | Browser `localStorage` (per origin; the home screen app has its own) | No Keychain in browsers |
| Long-press on a poster | Context menu with preview | Action sheet (long-press on touch, right-click with a mouse) | No native context menus |
| Haptics | Success / warning / selection | None | Not available in iOS Safari |
| Debug report | Share sheet with a zip built on the phone | `POST /v1/debug/bundle` with the browser log; share sheet over HTTPS, download over HTTP | Browsers can't build zips without a library |
| Offline start | Cached catalogue in Caches | Cached catalogue in `localStorage`; the app shell itself only over HTTPS (service worker) | Service workers need a secure context |
| Pull to refresh | System pull-to-refresh | Refresh button in the header | Pull-to-refresh fights the browser's own scrolling |
| Wide screens | iPhone only | Side bar (with the app name) instead of tab bar from 900 px, content centred beside it, bigger grids from 1600 px; hover lift on posters and hover rows for a mouse; Escape closes sheets; keyboard focus ring | Laptops and iPads |
| iPhone Safari fit | Native | Safe-area padding top, bottom and both sides (landscape notch); `dvh` heights so sheets fit with Safari's toolbars; every field 16 px so Safari never zooms in; 44 px hit areas on small controls; no double-tap zoom on controls; press feedback on touch; sheets don't scroll the page behind; pinch zoom stays on; reduced motion respected (`web/css/app.css`, `web/index.html`, checked by `web/tests/shell.test.mjs`) | A browser has none of this by default |
| First load | App bundle | All modules and both config files preload in parallel (`<link rel=modulepreload>` in `index.html`); the gateway gzips JS, CSS and JSON (about 167 KB to 58 KB) | Plain HTTP over Tailscale has no service worker cache |
| API console | — | `/console/` (§14) | Served by the gateway |

## 14. API console (`/console/`)

| ID | Situation | Expected | Code | |
|---|---|---|---|---|
| K1 | Open it | Gateway URL (this page's origin) and token (shared with the web app's sign-in) at the top; every endpoint grouped below | `web/console/index.html` | ✅ |
| K2 | Edit a parameter or body | The curl command updates as you type | `curlFor` | ✅ |
| K3 | Copy curl | Copied, also over plain HTTP; button says "Copied" | `copyText` | ✅ |
| K4 | Test | Status, time, ETag / X-Poll-After, pretty JSON; zip endpoints give a download link; invalid JSON body explained | `run`, `renderResult` | ✅ |
| K5 | Endpoint that changes things | Tagged, asks before running | `test()` | ✅ |
| K6 | Test every read-only endpoint | Runs all GETs, table of status and time with links to each card | `#runAll` | ✅ |
| K7 | Untick "real URL and token" | Commands use `$ARR` / `$TOKEN`; the two lines that set them appear with a Copy button | `renderVars` | ✅ |
| K8 | New `/v1` route added to the gateway | `test_api_console_lists_every_route` fails until it's in `ENDPOINTS` | `backend/tests` | ✅ |
