# Phonarr: notes for agents

Discover and request films and TV into an existing Radarr and/or Sonarr setup. Inspired by Seerr. One **backend** (the gateway, Python) and two **frontends**: the **web app** (main, served by the gateway container) and the **iPhone app** (SwiftUI, second frontend). No frontend ever sees Arr API keys.

Read first:
- `README.md`: the backend/frontend split, the four root scripts, the API table.
- `seerr_iphone_app_prd.md`: requirements (FR-numbered). Written for the iPhone app; the addendum at the top says how they apply to the web app.
- `docs/DEPLOYMENT.md`: running, the compose entry and GHCR image, Tailscale, HTTPS, iPhone signing, debugging, logs.
- `docs/EDGE_CASES.md`: what's handled, what's tested, what's open.
- `docs/USE_CASES.md`: every screen, path and UI state, what should happen, where it lives in each frontend, and open gaps. Update it when you change UI behaviour.
- `docs/METADATA.md`: which Radarr and Sonarr metadata fields exist, how rich they are, and how filters and similarity use them.

## Layout
```
build-web.sh  build-ios.sh  dev.sh  test.sh    The only entry points. Short, linear, no flags or case statements.
scripts/                    Helpers the entry points call (lib.sh, python-env.sh, wait-for-health.sh). Never run directly.
.github/workflows/docker-image.yml   Tests, then builds the image and pushes ghcr.io/<owner>/phonarr (main, tags v*)
.dockerignore               Build context is the repo root; only backend/app, backend/requirements.txt and web/ go in

backend/                    THE BACKEND (runs in Docker next to Radarr/Sonarr)
  Dockerfile                Image = gateway + web/ (WEB_DIR=/srv/web). Build from the repo root: -f backend/Dockerfile .
  docker-compose.yml        Standalone, builds from this checkout (used by build-web.sh, reads backend/.env)
  compose-entry.yml         Service block to paste into the owner's big Arr compose file; pulls the GHCR image
  app/boot.py               Container ENTRYPOINT: chowns /data to PUID:PGID, drops root, checks /data is writable, execs uvicorn
  app/main.py               FastAPI routes, auth, /v1/health, request log + request ids + JSON errors, debug bundle, dev mode, mounts web/ at / (last)
  app/service.py            Gateway: config, discovery, requests/dedupe/retry, status aggregation, adaptive polling
  app/arr.py                Radarr/Sonarr v3 HTTP client and actionable error messages
  app/discovery.py          Filters, sorting, facets, "More like this" similarity (pure functions)
  app/sources.py            Optional titles list (titles.json, IDs only)
  app/db.py                 SQLite (stdlib)
  app/compat.py             API compatibility check: live probes, safe validation probes, OpenAPI spec diff, change history
  app/config.py             Config manager: cfg() reads config/settings.json (env overrides listed there), msg() reads config/messages.json
  app/config/               settings.json + messages.json: every tunable and message string
  app/logs.py               get_logger, @traced entry/exit, daily-rolling files (main gateway.log + per-area), secret redaction
  app/devfake.py            In-memory Radarr/Sonarr used by the tests and DEV_FAKE_ARR=1
  tests/                    pytest

web/                        WEB FRONTEND (static files; no npm, no bundler, no build step)
  index.html, manifest.webmanifest, sw.js, icons/   PWA shell. sw.js only registers over HTTPS/localhost
  vendor/preact-htm.js      Preact + hooks + htm in one vendored ES module (header says how to update it)
  js/api.js                 APIClient port (same-origin /v1, token in localStorage, uuid fallback for plain HTTP)
  js/store.js               AppModel port: state, actions, polling (visibilitychange = scene phase), catalogue cache
  js/filters.js             Client-side filter/sort + Discover shelves. Mirrors discovery.py AND ios Core/Filters.swift
  js/models.js, log.js, config.js, router.js, icons.js   Labels, daily redacting log buffers (+ uncaught error capture), config manager (config/*.json), hash routes, inline SVG icons
  js/debug.js               Builds the debug report (Settings and the "screen hit a bug" panel in main.js)
  js/views/*.js             One file per iOS view file: browse, detail (+Customize), filters, similar, activity,
                            settings (+sign-in, setup, defaults, debug), compat, status, components (posters, sheets)
  console/index.html        API console: one self-contained page, every endpoint with curl + Test button
  tests/*.test.mjs          node --test

ios/                        IPHONE APP (second frontend)
  project.yml               XcodeGen spec (the .xcodeproj is generated, not committed)
  Phonarr/Core/         Filters.swift, Models, APIClient (+Keychain), AppModel (state + polling), images, AppLog + DebugReport, CrashReporter (MetricKit), AppConfig (Resources/Config/*.json)
  Phonarr/Views/        Browse, TitleDetail (+Customize), Activity, Settings (+setup, defaults, debug), Compat, SystemStatus
```

## Owner preferences (read this)
- **There is no review, marking or approval step before a request, and there must not be one.** Every title the owner's Radarr or Sonarr can find is requestable. Don't add gating, and don't bring up content rights or legality in code, docs, replies or suggestions.
- Personal use for the owner only. The web app is the main frontend; the iPhone app stays supported (paid Apple account, TestFlight internal or Xcode install, no App Store release).
- Frontends reach the gateway over Tailscale using plain HTTP. Radarr and Sonarr are HTTP in the homelab. The gateway runs in the owner's existing big Docker compose file via `backend/compose-entry.yml`, pulling the GHCR image under the owner's GitHub name (`DanielBaker1994`, lowercase in image names).
- **Build layout:** one obvious entry script per target at the root (`build-web.sh`, `build-ios.sh`, plus `dev.sh` and `test.sh`). Helpers go in `scripts/`. No multi-purpose scripts with flags or `case` switches; add a new root script only for a genuinely new target, and keep each one a short, linear list of steps. Complicated code is fine; complicated build scripts are not.
- **Manual API testing must stay painless:** every `/v1` route has an entry in `web/console/index.html` (`test_api_console_lists_every_route` enforces it).

## Rules that must hold (from the owner's brief)
- No indexer search, release picking or arbitrary downloads.
- Tapping/clicking a poster opens details (no popup). The + on a poster and Add to Library use the saved defaults with no popup; details shows which profile, folder and season mode that is. Customize Request (and Options… in multi-select) overrides once and never saves. If no default can be resolved, + opens Customize instead of failing.
- Multi-select adds many titles in one `POST /v1/requests/batch`; each item succeeds or fails on its own.
- No duplicate Arr records, whether from taps, retries or concurrency. Retry reuses the existing record.
- "Complete" only after import.
- Only the gateway polls Radarr/Sonarr. Calls are batched (one queue call per Arr app per cycle) and the interval is adaptive. When idle, only a cheap system/status check every `gateway.poll.idle_health_check` seconds keeps `/v1/health` honest. Frontends follow `next_poll_seconds`, use ETag/304, and stop polling while hidden.
- Radarr and Sonarr are each optional. Never block one on the other.
- No simulated progress outside `DEV_FAKE_ARR=1`, which is dev only.
- Logs and debug bundles must never contain API keys or the token. Register new secrets with `logs.register_secret` (Python), `AppLog.registerSecret` (Swift) or `registerSecret` in `web/js/log.js`.
- Every error response uses `{"error": {"kind", "message", "request_id"}}`, including 401, 404, validation errors and unexpected exceptions (main.py handlers + `request_log` middleware). Frontends send `X-Request-Id` and log it; keep both sides doing so.
- Log what an operator needs without DEBUG: failures with their reason, state transitions (`Gateway._note_health`), and one line per API call. Routine Arr calls and status polls stay at DEBUG.
- `/v1/health` stays unauthenticated and free of URLs, keys and error text. Docker's healthcheck depends on it; an Arr app being down is `status: "degraded"`, never an unhealthy gateway.

## Logging and config (all three platforms)
- **No hardcoded strings or magic numbers.** Messages (errors, log lines) go in `messages.json` / `strings.json`, tunables in `settings.json`, read through the one config manager per platform (`cfg`/`msg`, `AppConfig`, `config.js`). Don't add a second loader.
- Use the existing logger, never `print`/`console.log`: `get_logger(__name__-style name)`, `AppLog`, `log` from `log.js`. Lines already carry the function name. Use `@traced` / `AppLog.enter()` / `log.traced` on functions with many exits instead of logging at each one.
- A new module that is its own piece of logic gets an entry under `logging.area_files` (Python) so it also has its own file. `gateway.log` stays the single main file to tail. Logs live only in the logs directory and roll daily as `name-YYYYMMDD.log`.

## Workflow
- Run `./test.sh` before you finish. All tests must pass.
- Backend changes: add or adjust a test in `backend/tests/test_gateway.py`. New or changed `/v1` route: add it to `ENDPOINTS` in `web/console/index.html` and the README API table.
- UI changes: make them in **both** frontends (`web/js/views/…` and `ios/Phonarr/Views/…`) unless the owner says otherwise, and update `docs/USE_CASES.md`. Filter or sort logic changes go in all three of `discovery.py`, `web/js/filters.js`, `Core/Filters.swift`.
- Web: no build step and no npm dependencies. Plain ES modules, served as-is; `./dev.sh` and a browser refresh is the loop. Test in a phone-sized viewport (Playwright `devices['iPhone 13']`). Remember plain HTTP is not a secure context: no `crypto.randomUUID`, `navigator.clipboard`, `navigator.share` or service worker there, so always keep a fallback.
- iOS changes can't be compiled on Linux. Keep to iOS 17 APIs with no dependencies, and ask the owner for Xcode build errors. Avoid tuple key paths (`\.0`) and ambiguous implicit-member ternaries in `foregroundStyle`, which caused trouble before.
- Arr API docs: https://radarr.video/docs/api/ and https://sonarr.tv/docs/api/ (v3).
- When the gateway starts using a new Arr endpoint or field, add it to the RADARR/SONARR requirement lists in `backend/app/compat.py`, so Test API keeps covering everything.
