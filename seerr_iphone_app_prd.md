# Phonarr: Product Requirements

Version 1.2 · 2026-11-07 · Status: approved. 1.1 removes the review step, adds discovery sources, the visible request plan, quick add and multi-select. 1.2 adds the web frontend, health status and the API console.

> **Addendum 1.2: two frontends.** This document was written for the iPhone app. The product now has a **web frontend** (a PWA in `web/`), served by the gateway's own container next to Radarr and Sonarr and opened in any browser, including as an iPhone home screen app. It is the main frontend; the iPhone app remains a supported second one. Wherever this document says "the app", "the phone" or "the iPhone", the requirement applies to **both frontends** unless it is about something only native iOS can do (Keychain, haptics, the share sheet, TestFlight). Web equivalents: the token lives in browser storage instead of the Keychain; "background" means the tab or home screen app is hidden; tap = click; long-press = long-press or right-click.
>
> New in 1.2:
> - **FR-WEB-1** The gateway container serves the web frontend at `/` from the same port as the API; no separate web server and no build step.
> - **FR-WEB-2** The web frontend covers every screen the iPhone app has (Discover, Movies, TV Shows, details, filters, More like this, multi-select, Customize Request, Activity, Settings, API compatibility, System status, debug report) and works over plain HTTP on Tailscale. HTTPS adds offline caching only.
> - **FR-OPS-1** `GET /v1/health` (no auth) reports whether the gateway is up and whether Radarr and Sonarr are reachable (`ok` / `degraded` / `setup`), without URLs, keys or error text. The Docker image's healthcheck uses it. Both frontends show it under Settings → System status.
> - **FR-OPS-2** A prebuilt image is published to GitHub Container Registry by GitHub Actions, and a ready-to-paste service entry exists for an existing Arr compose file.
> - **FR-OPS-3** An API console page (`/console/`) lists every endpoint with a copy-and-paste curl command and a Test button that shows the response.
> - **FR-OPS-4** One build entry script per target at the repo root (`build-web.sh`, `build-ios.sh`, plus `dev.sh`, `test.sh`); helpers live in `scripts/`.

## 1. Summary

Phonarr is a native iPhone app, inspired by Seerr (formerly Overseerr/Jellyseerr), for discovering and requesting films and TV series into an existing Radarr and Sonarr installation. A small Python service (the "gateway") runs next to Radarr and Sonarr in Docker. It holds every Arr credential and is the only thing the iPhone talks to.

The app is not a general Arr client. It never exposes indexer search, manual release selection, or raw downloads: it adds titles to Radarr and Sonarr, which then search the owner's sources as usual. Every title Radarr or Sonarr can look up can be requested, with no review or approval step.

## 2. Users and context

- One household owner who runs Radarr and Sonarr with their own indexers and sources.
- Gateway, Radarr and Sonarr share a Docker network. The iPhone reaches the gateway over the LAN or Tailscale (MagicDNS `*.ts.net` included).
- Radarr and Sonarr may each have their own hostname, port and URL base, for example `http://radarr:7878`, `https://sonarr.tail1234.ts.net/sonarr`.

## 3. What we borrow from Seerr, and what we change

| Seerr behaviour | Phonarr |
|---|---|
| Discovery from TMDB trending/popular lists | Discovery comes from **Radarr Discover** (import lists + recommendations), an optional **titles list** of IDs, and **search** through Radarr/Sonarr lookup. Metadata and artwork come from the Arr lookup endpoints (Servarr metadata proxies), so no TMDB key is needed |
| Request, then optional admin approval | No approval step. The owner is the only user, so a request is sent straight to the Arr app |
| Request modal asks for server, profile and root folder | One tap uses the saved defaults, and the screen says which ones before you tap. **Customize Request** is a secondary action for one-off overrides. **Multi-select** adds many titles at once |
| Status badges: Requested / Processing / Available | Poster-level state and progress bar driven by the Arr download queue |
| Hides nothing by default | Hides titles already in the library, requested or downloading |
| Users and permissions | One shared bearer token between app and gateway |

## 4. Arr API surface used (v3 API; Radarr 4/5, Sonarr 3/4)

Both apps authenticate with the `X-Api-Key` header. Every path sits under the configured URL base.

**Radarr**
- `GET /api/v3/system/status`: connection test (`appName` must be `Radarr`, plus `version`)
- `GET /api/v3/qualityprofile`, `GET /api/v3/rootfolder`
- `GET /api/v3/movie`: whole library in one call, cached and matched by `tmdbId`
- `GET /api/v3/movie/lookup/tmdb?tmdbId=`: metadata and images for one title
- `GET /api/v3/movie/lookup?term=`: search
- `GET /api/v3/importlist/movie?includeRecommendations=true`: Radarr Discover (import lists + recommendations)
- `POST /api/v3/movie` with `addOptions.searchForMovie=true`: the request
- `GET /api/v3/queue/details?includeMovie=false`: every active download in one call
- `POST /api/v3/command {name: MoviesSearch, movieIds}`: retry
- `DELETE /api/v3/queue/{id}?removeFromClient=true&blocklist=true`: clears a failed download before a retry

**Sonarr**
- `GET /api/v3/system/status` (`appName` must be `Sonarr`). The major version decides whether a language profile is needed (v3 only, `GET /api/v3/languageprofile`)
- `GET /api/v3/qualityprofile`, `GET /api/v3/rootfolder`
- `GET /api/v3/series`: whole library in one call, matched by `tvdbId`. It includes `statistics` (episode counts)
- `GET /api/v3/series/lookup?term=tvdb:{id}` (one title) and `?term=` (search)
- `POST /api/v3/series` with per-season `monitored` flags plus `addOptions.monitor` (v4) and `searchForMissingEpisodes=true`
- `GET /api/v3/queue/details?includeEpisode=true`
- `GET /api/v3/episode?seriesId=`: episode detail screen
- `POST /api/v3/command {name: SeriesSearch, seriesId}`: retry

## 5. Functional requirements

### 5.1 Pairing and configuration
- **FR-1** On first launch the app asks for the gateway URL and access token, then shows the Radarr and Sonarr setup.
- **FR-2** Radarr and Sonarr are set up separately. Each takes a base URL (scheme, host, port, optional path) and an API key, and has a **Test Connection** button.
- **FR-3** The gateway checks reachability, the API key and the app type before saving. Errors must say what to do next: DNS failure, connection refused, timeout, TLS error, HTTP 401 (wrong key), 404 (wrong URL base), or the wrong app (for example a Sonarr URL in the Radarr field).
- **FR-2a** Radarr and Sonarr are **each optional**. First-run setup has one screen per server, each with Skip. With only one connected, the other section shows a "Connect" card and nothing is blocked. A connection can be removed later.
- **FR-4** API keys travel iPhone → gateway once, when they are saved. The gateway never returns them; responses only show `api_key_set: true`.

### 5.2 Defaults
- **FR-5** Quality profiles and root folders are fetched live from each Arr app.
- **FR-6** Saved on the gateway: default Radarr profile, default Sonarr profile, Radarr and Sonarr root folders, and the default TV season choice (`all`, `first`, `latest`, `future`).
- **FR-7** When an app has exactly one profile or one root folder, it is used without asking and the picker is hidden.
- **FR-8** Customize Request overrides the profile, root folder and (for TV) season choice for one request, or one multi-select batch, only. It never changes the saved defaults.
- **FR-8a** The last first-run setup step shows the defaults, so the first one-tap Add doesn't stop to ask.

### 5.3 Discovery sources
- **FR-9** Discovery merges, de-duplicated by TMDB/TVDB ID: the Radarr library, Radarr Discover (import lists and recommendations; excluded titles hidden), the optional titles list, and, when the user searches, Radarr/Sonarr lookup results.
- **FR-10** The titles list (`titles.json`) holds IDs only. It is optional, reloaded on change, and bad entries are skipped and logged.
- **FR-11** Search (2+ characters) shows local matches first, then "From Radarr/Sonarr search" results that aren't already shown. Any result can be added.
- **FR-12** Each card carries its `source`, which drives a "Found in" filter and a "Recommended by Radarr" shelf. Sonarr has no Discover feed, so TV comes from the list and search.

### 5.4 Discovery
- **FR-13** Tabs: Discover, Movies, TV Shows, Activity, Settings.
- **FR-14** The Discover tab mixes films and series in poster rows. The Movies and TV tabs show poster grids with search (title) and a genre filter.
- **FR-15** By default discovery shows only titles that are not in the library, not requested and not downloading. Matching uses `tmdbId` and `tvdbId`, never the title.
- **FR-16** The detail screen shows the poster and fanart, year, runtime, genres, certification, overview, network or studio, ratings, and themes.

### 5.4a Filters and "More like this"
- **FR-16a** Movies and TV support sorting (title, newest, oldest, rating, popularity, runtime, recently added) and filters (genre multi-select, decade, runtime band, minimum rating, language, studio or network, collection, certification, series status, found in). A filter appears only when the set has 2+ values for it. Active filters show as removable chips with a result count.
- **FR-16b** Discover adds shelves generated from metadata: highest rated, before 1930, under 90 minutes, the top genres, and one per decade.
- **FR-16c** Title details show **More like this**, ranked over genre, keywords, collection, era, studio or network, language, rating and runtime, with plain reasons. It can mix films and TV. Candidates are the discovery pool, and owned or requested titles are hidden by default. See docs/METADATA.md.

### 5.5 Requesting
- **FR-17** The primary action, **Add to Library**, is one tap and opens no confirmation dialog.
- **FR-17a** Tapping a poster opens details; it never pops up a dialog. A round **+** on each not-requested poster adds it straight away with the defaults. On a failed poster the same spot is **Retry**. Long-press offers Add (with the profile it will use), Customize Request… and Select.
- **FR-17b** Under Add to Library, details shows the **request plan**: "Uses HD-1080p · /movies · All seasons" and **Change** (opens Customize Request for this title). The gateway resolves the plan the same way a request does (`request_plan` on discover and details, `GET /v1/request-plan`).
- **FR-17c** When no default can be resolved (several profiles, none chosen), the plan shows the problem with "Choose for this one" and "Set defaults", and + / Add open Customize Request instead of failing.
- **FR-17d** **Multi-select** in Movies, TV Shows and See all: Select (toolbar, or long-press → Select), tap posters to tick them, Select All. Already-requested titles can't be ticked; failed ones can (they're retried). A bottom bar shows the plan, **Options…** (one-off overrides for the whole batch) and **Add N**. One `POST /v1/requests/batch` call; each title succeeds or fails on its own, the toast summarises ("Added 4 · 1 already in library · 1 failed"), and failed titles stay selected.
- **FR-18** The gateway runs these steps in order: dedupe, check whether it already exists in Arr (by external ID), look up metadata, add with defaults, start the search, record the request, refresh the status cache.
- **FR-19** Dedupe works at three levels. The app disables the button while a request is in flight and sends an `Idempotency-Key`. The gateway holds a lock per title. The database has a unique constraint on `(media_type, external_id)`. Sending the same request again returns the existing record.
- **FR-20** The response returns at once with the new state, and the poster updates without a full reload.

### 5.6 Status and progress
- **FR-21** States: `not_requested`, `submitting` (on the phone only), `queued`, `searching` (monitored with nothing in the queue), `downloading`, `paused`, `stalled`, `importing`, `complete`, `failed`.
- **FR-22** `complete` requires the file to be imported (Radarr `hasFile`, Sonarr `episodeFileCount ≥ episodeCount` for monitored episodes). A finished download that has not been imported counts as `importing`.
- **FR-23** Progress = `(size − sizeleft) / size`, shown only when `size > 0`.
- **FR-24** A series shows one combined state (the worst failure, otherwise the most active state, plus combined progress). The detail screen lists progress per episode.
- **FR-25** Posters show a thin progress bar along the bottom and a small status badge. Failure uses a distinct red badge.

### 5.7 Failures and retry
- **FR-26** Failures fall into two kinds. `download` covers a failed or errored queue item, a rejected import or a stalled download; Retry clears the failed queue item (blocklisting that release) and starts a new search on the **existing** Arr record. `config` covers a bad key, a missing default profile or root folder, or an Arr app that can't be reached; it points the user to Settings.
- **FR-27** Retry never creates a second Arr record. It only re-adds a title when the gateway has confirmed the Arr record is gone.
- **FR-28** When Radarr or Sonarr is unreachable, the app shows an "unavailable" banner and keeps the last known statuses, marked as stale.

### 5.8 Polling and efficiency
- **FR-29** Polling happens only on the gateway. It makes one `queue/details` call per Arr app per cycle, never one call per poster.
- **FR-30** The interval adapts. **5 s** while the phone reports someone is watching activity and something is downloading. **20 s** while the app is open and something is downloading. **60 s** while the app is in the background or idle and something is downloading. Polling **stops** when nothing is downloading and no phone has checked in for 2 minutes. Changing the configuration, making a request or the app returning to the foreground triggers an immediate refresh.
- **FR-31** The library lists (`/movie`, `/series`) are cached for 10 minutes and refreshed after each request. A full library sync never runs on every poll.
- **FR-32** `GET /v1/status` returns an `ETag` and honours `If-None-Match` (304), so a phone that polls often costs almost nothing when nothing changed.
- **FR-33** The gateway logs how long each Arr call takes and exposes `GET /v1/health` with recent timings, so the intervals can be tuned against real behaviour.

### 5.9 Diagnostics
- **FR-34** The gateway writes rotating log files to `/data/logs`. The app keeps its own rotating log file.
- **FR-35** Settings → Troubleshooting → **Share debug report** makes one zip (app log, device info, and the gateway's logs and state from `GET /v1/debug/bundle`) and opens the iOS share sheet.
- **FR-36** API keys and the gateway token are redacted from every log and bundle.

## 6. Gateway API (v1)

All routes except `/v1/health` need `Authorization: Bearer <token>`.

```
GET  /v1/health
GET  /v1/config                          -> per service: url, api_key_set, version, reachable
POST /v1/config/{service}/test           {base_url, api_key?}
PUT  /v1/config/{service}                {base_url, api_key}      (tests before saving)
DELETE /v1/config/{service}                                       (remove a connection)
GET  /v1/debug/bundle                    -> zip of redacted logs + state
GET  /v1/options                         -> profiles + root folders for both, current defaults
PUT  /v1/defaults                        {radarr_profile_id, sonarr_profile_id, radarr_root, sonarr_root, season_monitor}
GET  /v1/discover?type=movie|series&q=&genres=&decade=&year_min=&year_max=&runtime=&rating_min=
                 &language=&studio=&collection=&certification=&status=&source=&sort=&include_tracked=false
                                         -> items (with source), facets (value counts), total, request_plan; q also searches Arr
GET  /v1/titles/{type}/{id}/similar?mix=true&limit=20  -> ranked items with score + reasons
GET  /v1/titles/{type}/{id}              -> detail + state + request_plan (+ episodes for series)
GET  /v1/request-plan?type=              -> profile/folder/season a one-tap Add uses, or the problem
POST /v1/requests                        {type, id, quality_profile_id?, root_folder?, season_monitor?}   Idempotency-Key header
POST /v1/requests/batch                  {items:[{type,id}], movie?:{…overrides}, series?:{…overrides}} -> per-item results
POST /v1/requests/{type}/{id}/retry
GET  /v1/status?viewing=activity|browse  -> map of tracked titles to state; ETag
GET  /v1/activity                        -> tracked titles with detail
```

## 7. Non-functional requirements

- Cold start on the iPhone in under 1 s to the first screen, with cached discovery results shown immediately.
- Poster images load straight from the metadata CDN URLs that Arr returns, use the native URL cache, and scroll at 60/120 fps on recent iPhones.
- Dark mode, Dynamic Type and VoiceOver labels on poster states.
- The gateway runs in one container: Python 3.12, FastAPI, httpx and SQLite. No Redis and no queue service.
- Secrets live in a SQLite file inside a Docker volume. The access token comes from the `ARR_GATEWAY_TOKEN` environment variable.
- iOS 17+, built with SwiftUI and the Observation framework, with no third-party dependencies.

## 8. Out of scope (v1)

Multiple users and approvals, Plex/Jellyfin integration, push notifications, manual release selection, indexer search, requesting individual episodes, and editing the titles list from the app.

## 9. Definition of done

Mirrors the owner's brief, section 12: independent configuration and testing of Radarr and Sonarr, profiles fetched automatically, persistent defaults, discovery of films and TV with owned titles hidden, one-tap add, one-off override, poster progress, visible retry, dedupe, adaptive polling, and no Arr keys on the phone. 1.1 adds: no review step, the visible request plan, quick add, and multi-select.
