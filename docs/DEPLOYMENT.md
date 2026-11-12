# Deploying, running and debugging Phonarr

This is the operational guide. It is written so a person or a future coding agent can pick the project up cold. For orientation in the code, see [`../CLAUDE.md`](../CLAUDE.md). Product requirements are in [`../seerr_iphone_app_prd.md`](../seerr_iphone_app_prd.md).

**Current assumptions (update these when they change)**
- The **backend** (gateway) runs in Docker on the same host and Docker network as Radarr and Sonarr, as one more service in the owner's existing big compose file, pulling `ghcr.io/danielbaker1994/phonarr` (built by GitHub Actions).
- The **web app** is the main frontend. It is served by that same container and opened in a browser, usually Safari on the iPhone as a home screen app.
- The **iPhone app** is a supported second frontend. Apple account: **paid Apple Developer Program**, personal use only, never on the public App Store.
- Frontends reach the gateway over **Tailscale** (or the home LAN) using plain HTTP. Radarr and Sonarr run plain HTTP inside the homelab.
- Radarr and Sonarr are each optional. Either one alone is a supported setup.

---

## 1. The pieces

| Piece | Folder | Runs on | Talks to |
|---|---|---|---|
| Backend: gateway API | `backend/` (FastAPI, Python 3.12, SQLite) | Docker on the media server | Radarr and Sonarr (`X-Api-Key`); frontends (bearer token) |
| Web frontend (main) | `web/` (HTML, CSS, JS modules; no build step) | Served by the gateway container at `/`; runs in the browser | Only the gateway (same origin, `/v1`) |
| API console | `web/console/index.html` | Served at `/console/` | Only the gateway |
| iPhone app (second frontend) | `ios/` (SwiftUI, iOS 17+, no dependencies) | iPhone, built with Xcode on a Mac | Only the gateway |

What runs where, in one sentence: everything server-side (the API, polling Radarr/Sonarr, the database, *and hosting the web app's files*) is one container next to your Arr stack; the web app's code then runs in whatever browser opens it.

**Build entry points** (repo root, one per target, helpers in `scripts/`):

| Script | Target | Does |
|---|---|---|
| `./build-web.sh` | Browser | `docker compose -f backend/docker-compose.yml up -d --build`, then waits for `/v1/health` |
| `./build-ios.sh` | iPhone | `xcodegen` in `ios/`, opens Xcode |
| `./dev.sh` | Local development | Gateway with simulated Radarr/Sonarr and auto-reload on `:8787`, serving the web app |
| `./test.sh` | Tests | pytest for the gateway, `node --test` for the web filters |

The web app has **no build step**: the files in `web/` are what the browser loads. "Building" the web target means building the Docker image, which copies `web/` in next to the gateway (`backend/Dockerfile`, build context = repo root).

Gateway state lives in the data folder (`/app/data` in the image, bind-mounted from the host; `backend/data` in a checkout). Paths come from `storage` in `backend/app/config/settings.json`, and the gateway refuses to start if one points outside the folder. Moving it to another machine: README "Moving to another machine".
- `gateway.sqlite3`: Arr URLs and API keys, defaults, request records, metadata cache.
- `titles.json` (optional): TMDB/TVDB IDs to show in discovery, IDs only (see `backend/examples/titles.example.json`). Override the path with `TITLES_FILE`.
- `access_token`: only written if neither `ARR_GATEWAY_TOKEN` nor `ARR_GATEWAY_TOKEN_FILE` is set. A secret, so exports skip it and imports keep the one already there.

Logs are rebuildable output and live apart from the data, in `cache/logs` (`/app/cache/logs` in the image, `backend/cache/logs` in a checkout):
- redacted logs, all in this one folder, rolled daily and kept 14 days (`logging.retention_days`).
  - `gateway.log` is the **main file to tail** (`tail -f backend/cache/logs/gateway.log`, or `docker compose exec phonarr tail -f /app/cache/logs/gateway.log`): every line from every module. Each line starts with the time in UTC (ISO 8601 with milliseconds and `Z`) and shows the request id, module, function and line number, e.g. `2026-11-10T08:00:00.123Z WARNING [web-3f2a9c1e] gateway.request_log:235: POST /v1/requests -> 502 in 812ms`. `[-]` means the line didn't come from an API call (poller, startup).
  - `arr.log`, `compat.log`, `sources.log` hold only that logic's lines, for when the main file is too busy.
  - At midnight a file is renamed with the date: `gateway-20261107.log`. Which areas get their own file is the `logging.area_files` setting.
  - Functions with many exit points (requests, retries, refresh) are wrapped in `@traced`, which logs one entry line and one exit line with the duration at `DEBUG` (set `LOG_LEVEL=DEBUG`).

What the log tells you without turning on DEBUG: one `Phonarr <version> starting: …` line per start (data folder, token source, which servers are connected, timezone); one line per API call from a frontend (`[req id] METHOD /v1/... -> status in ms`, polls only at DEBUG); every failed Radarr/Sonarr call **with its reason** (DNS, refused, timeout, TLS, the app's own error text); `Radarr is DOWN: <reason>` when a server stops answering, a reminder every hour while it stays down, and `Radarr is back UP (was down N min)`; every failed request with the title and reason; and any unexpected error with its full traceback and request id. Successful Radarr/Sonarr calls are logged at DEBUG, slow ones (over `arr.slow_ms`, 3 s) at WARNING.

---

## 2. Backend (gateway + web app container)

### 2.1 In your existing Arr compose file (recommended)

Your Arr stack already runs from one big `docker-compose.yml`. Add Phonarr as one more service:

1. Copy the `phonarr:` block from [`backend/compose-entry.yml`](../backend/compose-entry.yml) under `services:` in your file.
2. Set `networks:` to the network your `radarr` and `sonarr` services are on (or remove it if they all use the compose default network). Inside that network the gateway reaches them as `http://radarr:7878` and `http://sonarr:8989`. Not `http://localhost:7878`: inside a container, localhost is the container itself (the setup screen says so if you try).
3. Add `ARR_GATEWAY_TOKEN=<openssl rand -base64 32>` to the `.env` next to your compose file. `PUID`, `PGID` and `TZ` are read from the same `.env`; most Arr stacks already define them for the linuxserver images, and the block reuses them.
4. `docker compose pull phonarr && docker compose up -d phonarr`
5. `docker compose ps phonarr` should say `healthy` within a minute. Open `http://<server>:8787/`. If it isn't, `docker compose logs phonarr` shows why in one line (see §5.2).

**Data folder and permissions.** The container starts as root only long enough to create `./phonarr` (if Docker hasn't), give it to `PUID:PGID` (default 1000:1000, like Radarr and Sonarr), and drop root; the gateway itself always runs as `PUID:PGID` (`backend/app/boot.py`). You don't need to `chown` anything. If you run it with `user: "1000:1000"` instead, nothing is chowned, and a folder that user can't write stops the container with one line saying which `chown` to run.

Updating later: `docker compose pull phonarr && docker compose up -d phonarr`. To stay on a known version, use a tag like `:1.2.0` or `:sha-abc1234` instead of `:latest`.

### 2.2 Standalone, built from this checkout (`./build-web.sh`)

For trying it out, or if you don't use one big compose file:

```bash
./build-web.sh                # first run creates backend/.env and stops: set ARR_GATEWAY_TOKEN and ARR_NETWORK, run again
```

It checks the Docker network in `ARR_NETWORK` exists, builds the image from your checkout with `backend/docker-compose.yml` (stamping `git describe` as the version shown in `/v1/health`), starts it, waits for `/v1/health` and prints it. If the container doesn't answer within 60 s it prints the container's last 30 log lines. State goes to `backend/data/`. Updating: pull the new code, run `./build-web.sh` again. The SQLite schema uses `CREATE TABLE IF NOT EXISTS`; if you change the schema, add a migration step in `app/db.py`.

### 2.3 GitHub: where the image comes from

The repository isn't on GitHub yet. When it is:

1. Create the repo `DanielBaker1994/phonarr` on github.com and push this folder to its `master` branch.
2. That's all for the build: [`.github/workflows/docker-image.yml`](../.github/workflows/docker-image.yml) runs `./test.sh`, then builds `backend/Dockerfile` for `linux/amd64` and `linux/arm64` and pushes
   - `ghcr.io/danielbaker1994/phonarr:latest` and `:sha-<commit>` on every push to `master`,
   - `:1.2.0` when you push a tag `v1.2.0`.
   It logs in with the workflow's own `GITHUB_TOKEN`; no secrets to add. Pull requests are built and tested but not pushed.
3. The first push creates the package **private**. Either make it public (github.com → your profile → Packages → phonarr → Package settings → Change visibility), or log the server in once with a personal access token that has `read:packages`:
   `echo <token> | docker login ghcr.io -u DanielBaker1994 --password-stdin`
4. The image's `org.opencontainers.image.source` label points at the repo, so the package shows up on the repo page. If you rename the repo or account, change that label in `backend/Dockerfile` and the image name in `backend/compose-entry.yml`. GHCR image names are always lowercase.

The image version (`APP_VERSION`, shown in `/v1/health` and System status) comes from the tag or commit; a local build says `dev`.

### 2.4 Environment variables

| Var | Default | Meaning |
|---|---|---|
| `ARR_GATEWAY_TOKEN` | (required) | Bearer token every frontend must send. Rotating it signs every frontend out; each asks for the new one. |
| `ARR_GATEWAY_TOKEN_FILE`, `RADARR_API_KEY_FILE`, `SONARR_API_KEY_FILE` | unset | Read the value from a file instead (compose/Docker secrets, e.g. `/run/secrets/arr_token`). The plain variable wins if both are set. With a secret, drop the `${ARR_GATEWAY_TOKEN:?…}` line from the compose block. |
| `PUID` / `PGID` | `1000` / `1000` | The user the gateway runs as and who owns the data folder. Use the same values as your Arr containers (`id -u`, `id -g` on the host). |
| `TZ` | `Etc/UTC` | Timezone for log timestamps and the midnight log roll, e.g. `Europe/London`. |
| `ARR_NETWORK` | `arr` | Standalone compose only: the external Docker network shared with Radarr and Sonarr. |
| `LOG_LEVEL` | `INFO` | Use `DEBUG` for more detail: every Radarr/Sonarr call, status polls and entry/exit lines. |
| `IDLE_HEALTH_CHECK` | `300` | Seconds between the cheap reachability checks made when no frontend is open and nothing is downloading, so `/v1/health` stays current. `0` turns them off. |
| `BATCH_MAX` | `100` | Most titles in one multi-select request. |
| `ARR_TIMEOUT` | `15` | Seconds to wait for Radarr or Sonarr. |
| `DATA_DIR` | `/app/data` | Moves the whole data folder (database, `titles.json`). Normally leave it and change the bind mount instead. |
| `OUTPUT_DIR` | `/app/cache` | Moves the rebuildable output folder (logs). |
| `TITLES_FILE` | `$DATA_DIR/titles.json` | The optional titles list. |
| `AUTH_REQUIRED` | `false` (settings.json `auth.required`) | Off: no access token needed, and the web app opens straight in. For a private network only. `true` asks for `ARR_GATEWAY_TOKEN` again. |
| `ARR_SPEC_URL_RADARR` / `ARR_SPEC_URL_SONARR` | upstream GitHub | OpenAPI spec used by the API check (§5.4). |
| `CONFIG_DIR` | none | Folder with `settings.json` and/or `messages.json` that override the built-in ones key by key (see Configuration below). |
| `RADARR_URL` / `RADARR_API_KEY`, `SONARR_URL` / `SONARR_API_KEY` | unset | Optional preconfiguration on first start. Otherwise use the setup screens. |
| `ARR_VERIFY_TLS` | `1` | Set `0` to accept self-signed certificates on Radarr or Sonarr. |
| `WEB_DIR` | `/srv/web` in the image, `../web` in a checkout | Where the web app's files are. If there's no `index.html` there, only the API is served. |
| `APP_VERSION` | set at image build | Shown in `/v1/health`. |
| `DEV_FAKE_ARR` | unset | **Dev only.** See 2.5. |
| `POLLER` | `1` | `0` disables the background poller (tests use this). `/v1/health` then says `"poller": "off"`. |
| `PORT` | `8787` | `./dev.sh` only: the port to serve on, if the container already uses 8787 on that machine. |

### 2.5 Local development (`./dev.sh`)

```bash
./dev.sh                      # http://localhost:8787 (token: dev), API console at /console/
./test.sh                     # gateway + web tests, ~5 s, no network needed
```

`dev.sh` creates `backend/.venv` the first time, then runs the gateway with **simulated Radarr and Sonarr** (`DEV_FAKE_ARR=1`) and auto-reload for Python changes. Web changes need only a browser refresh. The iOS Simulator can pair with `http://localhost:8787` and token `dev`.

About the simulated servers (`app/devfake.py`):
- Auto-connects to the in-memory `http://radarr.test` and `http://sonarr.test`.
- Each request starts a simulated download that advances about 8% per poll, goes through "importing", then completes. **His Girl Friday (TMDB 3085)** always fails partway, so you can try the failure and Retry flow. A retry then succeeds.
- Discovery shows `backend/examples/titles.example.json` plus a fake Radarr Discover feed (four recommendations). Search for "matrix" to see a search-only result. Movies need a default quality profile first (two exist), so the first + opens Customize Request, which is also how that path is tested.
- Posters point at made-up URLs, so you see placeholders.
- It logs a loud warning and is off unless `DEV_FAKE_ARR=1`. **Never set it on the real server.** The brief forbids simulated progress in the product, and this exists only for UI work.

Interactive API docs: `http://localhost:8787/docs` (Swagger, click Authorize), or the friendlier API console (§5.6).

### 2.6 Reaching the gateway over Tailscale (HTTP is fine)

With Tailscale connected on the phone or laptop, and the home server on the same tailnet:
- Use the server's Tailscale address and the gateway port: `http://100.x.y.z:8787/`, or with MagicDNS `http://<server-name>.<tailnet>.ts.net:8787/`. In the browser that's the web app; in the iPhone app's pairing screen it's the gateway address.
- **Plain HTTP works** for both frontends. Tailscale already encrypts the traffic (WireGuard). The iPhone app's Info.plist allows HTTP (`NSAllowsArbitraryLoads`). The web app works over HTTP too, minus the offline cache (§3.2).
- Radarr and Sonarr being HTTP doesn't matter to the frontends. Only the gateway talks to them, inside Docker or your LAN.

Things that can still trip you up:
1. **Use Docker or LAN addresses for Radarr and Sonarr, not `.ts.net` names**, in the server setup screens. These URLs are used *by the gateway container*, which usually isn't on the tailnet itself. `http://radarr:7878` (Docker network) or `http://192.168.x.x:7878` always work. A `.ts.net` URL only works if the gateway container is on the tailnet, for example through a `tailscale/tailscale` sidecar with `network_mode: service:tailscale`.
2. **MagicDNS names** only resolve if MagicDNS is on in the Tailscale admin console and "Use Tailscale DNS" is on in the client. The `100.x` IP always works.
3. **Tailscale must be connected.** If it's off, the iPhone app shows "Can't reach the gateway" and keeps your last results; the web app page won't load (or, if cached over HTTPS, shows the same banner).
4. **Host firewall:** port 8787 must be reachable on the server's Tailscale interface. Docker's published ports normally are, but `ufw`/`firewalld` rules or Tailscale ACLs can block it. Test from the phone's Safari: `http://100.x.y.z:8787/v1/health` should show `{"ok":true,…}`.
5. **Local Network permission** (iOS prompt, iPhone app only) only matters for `192.168.x.x` LAN addresses, not Tailscale `100.x` addresses.

### 2.7 Running next to your Arr stack in Docker: what's covered and what isn't

Supported and tested (the image was run next to a real Radarr 6.4 container in one compose file: connect, health, outage and recovery):

| Need | How |
|---|---|
| One more service in the Arr compose file | `backend/compose-entry.yml`; reaches Radarr/Sonarr by service name on a shared network |
| Same user and timezone as the linuxserver images | `PUID`, `PGID`, `TZ` (defaults 1000, 1000, UTC) |
| Bind-mounted data folder that Docker created as root | Fixed automatically on start (`app/boot.py`); a folder it still can't write gives one clear line, not a crash loop |
| Healthcheck | Built into the image and repeated in the compose block; an Arr app being down is `degraded`, never `unhealthy`, so Docker never restarts the gateway for it |
| Monitoring (Uptime Kuma etc.) | `/v1/health` stays current even with no frontend open (§5.5) |
| Secrets as files | `ARR_GATEWAY_TOKEN_FILE`, `RADARR_API_KEY_FILE`, `SONARR_API_KEY_FILE` |
| Bounded `docker logs` | `logging:` options in both compose files (10 MB × 3); the app's own files roll daily |
| `amd64` and `arm64` hosts | The GitHub Actions image is built for both |
| Graceful stop | uvicorn is PID 1 after `boot.py` hands over, so `docker stop` shuts it down cleanly |

Not supported yet, with the workaround where there is one:

| Not supported | Why / workaround |
|---|---|
| Serving under a sub-path (`https://host/phonarr/`) behind a reverse proxy | The web app, console and service worker use absolute `/v1` and `/` paths. Give it its own port, its own subdomain (`phonarr.example.com`), or `tailscale serve` (§3.2) |
| More than one Radarr or Sonarr (e.g. a 4K instance) | One of each (EDGE_CASES.md) |
| More than one gateway container, or uvicorn `--workers` > 1 | Polling state is in memory and SQLite is single-writer. Run exactly one container with the default single worker |
| The data folder on NFS or SMB | SQLite in WAL mode isn't safe on network filesystems. Keep it on a local disk; back it up by copying the folder while the container is stopped, or with `phonarr export-data` while it runs |
| `armv7` (32-bit Raspberry Pi OS) | Only `amd64` and `arm64` images are built. A 64-bit OS on the Pi works |
| Radarr/Sonarr behind a VPN container (`network_mode: service:gluetun`) | Works, but their address is the VPN container's name: `http://gluetun:7878` |
| Rootless Docker or Podman | Untested. Use `user: "<uid>:<gid>"` and make sure that user owns the folder |
| Read-only root filesystem (`read_only: true`) | Untested. The gateway writes only to `/app/data` and `/app/cache` (mount both), so it should work |
| Changing `RADARR_URL` / `SONARR_URL` after the first start | The saved connection wins; the log says so. Change it in Settings, or remove the connection to re-seed from the environment |
| Separate users or permissions | One shared token (`ARR_GATEWAY_TOKEN`) |

---

## 3. Web frontend (main)

### 3.1 Using it
1. Open `http://<server>:8787/` (Tailscale address on the go). Sign in with `ARR_GATEWAY_TOKEN`. There's no gateway address to type: the page comes from the gateway.
2. First time only: connect Radarr, then Sonarr (each can be skipped), and pick request defaults. If the iPhone app or another browser already did this, it's skipped.
3. **iPhone home screen app:** in Safari tap Share → **Add to Home Screen**. It opens full screen without browser bars, with its own icon. The home screen app has its own storage, so sign in once inside it.
4. On a laptop or iPad the tab bar becomes a side bar.

The token stays in that browser's `localStorage`. Settings → Sign out removes it. Radarr/Sonarr API keys typed into the setup screens go straight to the gateway and are never stored in the browser.

### 3.2 HTTPS (optional): offline start and faster loads
Over plain HTTP the browser doesn't treat the page as a secure context, so it won't run the service worker (`web/sw.js`). Everything works; the app just needs the gateway to be reachable to open, and the share sheet for the debug report becomes a download.

For the full PWA (app shell and posters cached, opens instantly, shows saved results when the gateway is briefly unreachable), serve it over HTTPS with Tailscale's built-in certificates, on the server:

```bash
tailscale serve --bg 8787        # https://<server-name>.<tailnet>.ts.net/  →  http://127.0.0.1:8787
```

Then open `https://<server-name>.<tailnet>.ts.net/` (MagicDNS and HTTPS certificates must be enabled in the Tailscale admin console) and add *that* to the home screen. It's a different origin from the `http://100.x…:8787` address, so sign in once there. The iPhone app can use the same HTTPS address.

### 3.3 Updating the web app
It updates with the container (`docker compose pull` / `./build-web.sh`). Files are served with `Cache-Control: no-cache` and revalidated by ETag, and the service worker is network-first, so the next page load picks up the new version. There's nothing to rebuild on the phone.

### 3.4 How it maps to the iPhone app
`web/js/` is a port of `ios/Phonarr/`: `api.js` ↔ `APIClient.swift`, `store.js` ↔ `AppModel.swift`, `filters.js` ↔ `Filters.swift`, and one file in `js/views/` per Swift view file. Behaviour is the same (docs/USE_CASES.md); the differences are listed there in §13.

---

## 4. iPhone app (second frontend)

### 4.1 Requirements
- A Mac with **Xcode 15 or later** (Swift 5.9+, the iOS 17 SDK). Xcode 16 is fine.
- `brew install xcodegen`. The `.xcodeproj` is generated from `ios/project.yml` and is not committed.

```bash
./build-ios.sh        # runs xcodegen in ios/ and opens the project
```

### 4.2 Simulator (no Apple account needed)
1. Select an iPhone simulator and press Run.
2. Pair with `http://localhost:8787`, using a gateway running on the same Mac (`./dev.sh`, §2.5).
3. Logs show in Xcode's console. In Console.app, filter on subsystem `Phonarr`.

The simulator is the fastest loop. With dev mode (§2.5) the whole app works with no real servers.

### 4.3 Your own iPhone with a free Apple ID (before the paid account)
1. Xcode → Settings → Accounts → add your Apple ID. A "Personal Team" is created.
2. Target → Signing & Capabilities: Team = *(Personal Team)*. Change `PRODUCT_BUNDLE_IDENTIFIER` in `project.yml` to something unique to you (for example `com.<yourname>.phonarr`), then re-run `./build-ios.sh`.
3. Connect the iPhone by cable the first time. On the phone, enable **Settings → Privacy & Security → Developer Mode** (iOS 16+) and restart when asked.
4. Run from Xcode. On first launch, trust the developer in **Settings → General → VPN & Device Management**.
5. When the app asks for **Local Network** access, allow it. It is needed to reach a LAN gateway.

Free-account limits you will hit:

| Limit | Effect | Workaround |
|---|---|---|
| Provisioning profile expires after **7 days** | The app stops launching after a week | Re-run from Xcode (cable or Wi-Fi debugging), which re-signs it |
| At most **3 sideloaded apps** per device, and only a few new App IDs per week | Installs may fail with an App ID limit error | Reuse the same bundle ID |
| No TestFlight, no App Store, no ad-hoc sharing | Only devices you can plug into your Mac | Join the paid program (4.4) |
| Some capabilities are unavailable (push notifications, App Groups, iCloud…) | Not used by this app today | — |

Wireless debugging: once paired by cable, tick Window → Devices and Simulators → *Connect via network*.

### 4.4 Paid account, personal use only (the plan)

You don't need App Store review for this. Pick one of these. Both remove the 7-day rebuild.

**Option A: TestFlight, internal testing (recommended).** Install and update over the air from the TestFlight app. No cable, no Mac nearby.
1. Enrol as an individual at developer.apple.com/programs ($99/year). Approval usually takes a day or two.
2. Xcode → Settings → Accounts: sign in. Your paid team now appears next to the Personal Team.
3. In `ios/project.yml`, set `DEVELOPMENT_TEAM` to your Team ID (Membership page) and pick a permanent bundle ID, for example `com.<you>.phonarr`. Then run `./build-ios.sh` again. Leave "Automatically manage signing" on.
4. App Store Connect → Apps → **+** → New App. Use the same bundle ID; the name only has to be unique on the store. Nothing gets published.
5. Xcode: choose *Any iOS Device*, then Product → **Archive** → Distribute App → **TestFlight & App Store** (or "TestFlight Internal Only"). Upload.
6. App Store Connect → TestFlight: add yourself under **Internal Testing**. Internal builds need **no Beta App Review**. Install the TestFlight app on the iPhone and accept the invite.
7. Answer the export-compliance question once ("uses only standard encryption: exempt"). To skip it next time, add `ITSAppUsesNonExemptEncryption = NO` to Info.plist.
- **Each build lasts 90 days.** Upload a new build before then. TestFlight warns you, and re-archiving takes about 5 minutes and needs no code changes. Bump `CFBundleVersion` every upload.

**Option B: install straight from Xcode with the paid team.**
- Same as 3.3, but with the paid team selected. Development provisioning profiles from a paid team last **1 year**, not 7 days. You re-run from Xcode about once a year, or whenever you change the code.
- Simplest if you're happy to plug in, or use wireless debugging, once a year.

Either way you're not limited to 3 sideloaded apps, and you can register up to 100 devices per type (iPad, a partner's phone) if you ever want to.

**What still expires:** the membership itself, yearly. If it lapses, existing installs stop launching once their profile or build expires. Certificates are renewed automatically by Xcode while signing is automatic.

**Optional later:** a small CI job (`xcodebuild archive` plus `xcrun altool`/fastlane `pilot` with an App Store Connect API key) could upload a TestFlight build every ~80 days without you doing anything.

## 5. Debugging

### 5.1 Getting logs from a user (or yourself)
**In the web app:** Settings → Troubleshooting → **Download debug report**. The browser sends its own log (a redacted ring buffer kept in localStorage) to `POST /v1/debug/bundle`, and gets back one zip: the gateway files listed below plus `web/app.log` and `web/device.json` (browser, home screen or not, HTTPS or not, viewport, gateway reachable, servers connected). Over HTTPS on iPhone it opens the share sheet; otherwise it downloads.

**In the iPhone app:** Settings → Troubleshooting → **Share debug report**. This makes one zip, `Phonarr-debug-<date>.zip`, containing:
- `app.log` plus the dated `app-YYYYMMDD.log` files (14 days) and the `api.log` area file: the app's own log (API calls with status, timing and request id, requests, retries, errors, scene changes). Past 1 MB in a day the file is moved aside as `app-YYYYMMDD-HHmmss.log`, never deleted.
- `diagnostics-*.log`: crash and hang reports from MetricKit, if the app crashed in an earlier run (they usually arrive on the next launch).
- `device.json`: app version, build number and git commit (stamped by `./build-ios.sh`), iOS version, device model, gateway host, which servers are connected and reachable, and the number of tracked titles.
- `gateway-debug.zip`: the gateway's `info.json` (versions, config without keys, defaults, polling state), `status.json`, `requests.json`, `arr_call_timings.json` and `logs/*.log*` (main, per-area and dated files). If the gateway is unreachable, `gateway-unavailable.txt` explains why.

It goes out through the iOS share sheet (AirDrop, Mail, Messages, Save to Files). If pairing itself fails, the pairing screen offers **Share app log**.

**Redaction:** Arr API keys and the gateway token are registered as secrets and replaced with `***` in every log line, in the gateway and on the phone. `apikey=`, `X-Api-Key:` and `Bearer …` patterns are also masked. Tests check this (`test_debug_bundle_is_redacted`).

**Server side directly:** `docker logs phonarr`, `cache/logs/gateway.log`, or `curl -H "$H" …/v1/debug/bundle -o x.zip`.

**Reading the logs on the phone:** Settings → Troubleshooting → **Logs** (both frontends) shows the last 1,000 lines of `gateway.log` (and the previous day's file if needed) next to the device's own log, merged by time. Every line on every side is stamped in UTC ISO 8601 with milliseconds (`2026-11-10T08:00:00.123Z`), so they line up whatever timezone the server and phone are in; the header shows both timezones and warns when the phone's clock is more than 2 minutes off the gateway's. Tap a request id to see that call on both sides; Copy or Download/Share what is shown. The same data is at `GET /v1/debug/logs?lines=&level=WARNING&q=&rid=` (redacted, needs the token).

**Following one call through all the logs:** every API call carries a request id. The frontends make one (`web-…` / `ios-…`), send it as `X-Request-Id`, and log it as `[req web-3f2a9c1e]`; the gateway puts the same id on every log line that call causes and returns it in every error (`"request_id"`). An unexpected gateway error tells the user its id, so `grep web-3f2a9c1e gateway.log` finds the traceback.

**Crashes:** the gateway logs any unhandled error with its traceback and returns a JSON error with the request id instead of a bare "Internal Server Error". The web app logs uncaught errors and rejected promises, and a screen that crashes while rendering shows a "This screen hit a bug" panel with Reload and Download debug report instead of a blank page. Settings → System status shows this browser's recent warnings and errors. The iPhone app saves MetricKit crash reports into its log folder (above).

### 5.2 Common problems

| Symptom | Likely cause | Where to look |
|---|---|---|
| Container restarts in a loop; `docker logs` says "The data folder /app/data is not writable by uid …" | You run it with `user:` and that user doesn't own the folder | `sudo chown -R <uid>:<gid> ./phonarr`, or drop `user:` and set `PUID`/`PGID` instead (§2.1) |
| `./build-web.sh` says "Docker network 'arr' … doesn't exist" | `ARR_NETWORK` in `backend/.env` doesn't match your Arr network | `docker network ls`; `docker inspect radarr` shows its network |
| "Could not resolve radarr:7878 … looks like a Docker service name" | Radarr is stopped (a stopped container's name stops resolving), named differently, or on another network | `docker ps`; check `networks:` for both services |
| "Inside Docker, localhost means the Phonarr container itself" | `http://localhost:7878` was entered for Radarr | Use `http://radarr:7878` or the server's LAN address |
| "Radarr returned HTTP 503: … RadarrAPI …" or an API check warning "answered with its own error" | Radarr itself can't reach its metadata server (often DNS or a VPN in front of Radarr) | Radarr → System → Status; not an Phonarr problem |
| "Could not resolve x.ts.net" | The gateway container isn't on the tailnet | §2.6 Tailscale notes |
| "No Radarr API at … include the URL base" | Radarr runs behind `/radarr` | Add the path to the base URL |
| "That URL is Sonarr, not Radarr" | The fields are swapped | Setup screen |
| "answered, but not with the Radarr API (non-JSON)" | A reverse-proxy login page (Authelia and the like) is in front | Use the internal Docker URL instead |
| Every request fails with "Choose a default … in Settings" | Several profiles or root folders and no default chosen | Settings → Request defaults |
| "root folder … is not accessible" | A volume mount or permission problem in Radarr or Sonarr | Arr → Settings → Media Management |
| TV Shows is empty | Sonarr has no discovery feed, so TV comes from TVmaze. The first scan starts 20 s after the gateway starts and takes a few minutes, then Sonarr lookups fill it in. Progress is in `tvmaze.log`, and the debug bundle's `info.json` has a `tvmaze` count. The gateway needs outbound HTTPS to `api.tvmaze.com` | Wait for the first scan, check `tvmaze.log` |
| Discover is empty | Empty Radarr (no import lists or recommendations yet), no `titles.json`, or everything is already owned | Search in Movies/TV, add IDs to `titles.json`, or turn on "Show titles already in library" |
| The app returns to the pairing / sign-in screen | `ARR_GATEWAY_TOKEN` was rotated | Enter the new token |
| Web app page doesn't load at all | The container is down (the gateway serves the page), or Tailscale is off | `docker compose ps phonarr`; `/v1/health` from the server itself |
| Web app signed out after enabling HTTPS | `https://…ts.net` is a different origin from `http://100.x…:8787`, so it has its own storage | Sign in once on the new address |
| `docker compose pull` says `denied` | The GHCR package is private and the server isn't logged in | §2.3 |
| Container shows `unhealthy` | The gateway process isn't answering `/v1/health` | `docker logs phonarr` |
| `/v1/health` says `"poller": "stopped"` (status `degraded`) | The background poller ended, which is a bug | `gateway.log` has the traceback; restart the container and send a debug report |
| "This screen hit a bug" in the web app | A JavaScript error while drawing that screen | Download debug report from that panel; the error is in `web/app.log` |
| "The gateway answered in a format this app doesn't understand" (iPhone) | The app and gateway are different versions | Update both; `app.log` has the field that failed |
| "Can't reach the gateway" banner | The phone is off the LAN or Tailscale is disconnected | Cached results stay visible |
| Posters stuck on "Searching" | Radarr or Sonarr hasn't found a release, which is normal for obscure titles | Check Arr → Activity and the indexers |

### 5.3 Tuning polling
`GET /v1/health` shows the current interval and the last 30 Arr calls with their timings. The values live in `backend/app/config/settings.json` under `gateway.poll` (intervals, including `idle_health_check`), `gateway.ttl` (cache lifetimes) and `gateway.retry_cooldown`; override them with a `settings.json` in `CONFIG_DIR` (see Configuration below). Change them only after looking at real timings from your servers.

### 5.4 API compatibility (after a Radarr or Sonarr update)
Settings → **API compatibility** → **Test API** (both frontends), or `POST /v1/compat/check` in the API console. The same check runs automatically once a day and whenever a server connection is saved. Results also go into the debug report (`gateway/api_compat.json`).

What it does (`backend/app/compat.py`):
1. **Live read checks.** Calls every read endpoint the app uses (system status, quality profiles, root folders, the library, lookups, the queue) and confirms every response field the code reads is still there. Missing optional fields (ratings, keywords…) are reported as info only.
2. **Safe write checks.** Sends an empty POST to "add movie" and "add series" and expects Radarr or Sonarr to reject it with a 400 validation error. That proves the endpoint exists, and **nothing is created**. A 404 or 405 means it's gone.
3. **OpenAPI spec comparison.** For things that can't be probed without side effects (the search command, removing a failed download), and for request fields and query parameters, it compares against the server's own OpenAPI spec if it serves one. Otherwise it uses the upstream spec from the official GitHub repos (Radarr `develop`, Sonarr `v5-develop`, fetched with no credentials, cached per version for a week). Override the URLs with `ARR_SPEC_URL_RADARR` / `ARR_SPEC_URL_SONARR`.
4. **Diff with the previous run.** It shows the version change (for example `5.14 → 6.0`) and every check whose result changed, such as "Download progress › Queue: ok → error (Missing fields: sizeleft)".

Result levels: ✅ compatible · ℹ️ optional data missing · ⚠️ check recommended (for example, missing from the upstream spec) · ⛔ something the app needs is gone. If you get ⛔ after an update, share the debug report with the next agent. The failing endpoint and field are named, and the matching code is in `backend/app/service.py`.

### 5.5 Is it up?
`GET /v1/health` needs no token and never includes URLs, keys or error text:

```json
{"ok": true, "status": "degraded", "version": "1.2.0", "uptime_s": 86400, "web_frontend": true,
 "services": {"radarr": {"configured": true, "reachable": false, "version": "5.14.0", "checked_s_ago": 41.0, "down_for_s": 1260},
              "sonarr": {"configured": true, "reachable": true, "version": "4.0.9", "checked_s_ago": 41.2, "down_for_s": null}},
 "poller": "running", "last_poll_error_s_ago": null, "idle_health_check_s": 300,
 "last_status_refresh_s_ago": 12.3, "poll_interval": 60, "polls": 1440, "recent_arr_calls": […]}
```

- `ok` is true whenever the gateway answers. Docker's `HEALTHCHECK` (in the image and repeated in `compose-entry.yml`) uses only this, so `docker compose ps` shows `healthy` / `unhealthy` and an Arr app being offline never restarts the gateway.
- `status`: `ok` (every connected server answered on the last check), `degraded` (one is unreachable, or the background poller stopped), `setup` (nothing connected yet).
- It stays current with no frontend open: when nothing is being watched or downloaded, the gateway still makes one cheap `system/status` call per server every 5 minutes (`IDLE_HEALTH_CHECK`). Each service shows `checked_s_ago` (when it was last checked) and `down_for_s` (how long it has been unreachable).
- `poller` is `running`, `off` (`POLLER=0`) or `stopped` (a bug; see the log). `last_poll_error_s_ago` is set if a poll cycle failed recently.
- Both frontends show it under **Settings → System status**, refreshed every 15 s, with the error text from `/v1/config` added (that part needs the token).
- For alerts, point Uptime Kuma, Healthchecks or similar at `http://phonarr:8787/v1/health` (inside the Docker network) with a keyword check for `"status":"ok"`.
- The web app is served by the same container, so if the gateway is down the page won't load at all (unless it was cached by the service worker over HTTPS). Check from the server: `curl -s localhost:8787/v1/health`.

### 5.6 Testing the API by hand: the API console
Open **`http://<server>:8787/console/`** (also linked from Settings → System status). It's one self-contained page, `web/console/index.html`:
- Every `/v1` endpoint, grouped, with a short description and editable parameters and JSON body.
- A ready-to-paste `curl` command per endpoint that updates as you edit. By default it contains the real URL and token; untick the box to get `$ARR` / `$TOKEN` variables plus the two lines that set them.
- **Copy curl** (works on plain HTTP too) and **Test**, which runs the request in the page and shows the status, time, key headers (ETag, X-Poll-After) and the pretty-printed response. Zip endpoints give a download link.
- **Test every read-only endpoint** runs all GETs and lists the results: a one-click smoke test after an update.
- Endpoints that add titles or change settings are tagged *changes things* and ask before running.
- The token box is shared with the web app (same browser storage), so if you're signed in there it's already filled in.
- `test_api_console_lists_every_route` fails if a `/v1` route is missing from the page, so it can't fall behind.

Prefer the terminal? The same commands, typed by hand:

```bash
ARR=http://100.x.y.z:8787 TOKEN=…
curl -s "$ARR/v1/health"
curl -s -H "Authorization: Bearer $TOKEN" "$ARR/v1/config"
curl -s -H "Authorization: Bearer $TOKEN" "$ARR/v1/discover?type=movie&q=matrix"
curl -si -H "Authorization: Bearer $TOKEN" "$ARR/v1/status?viewing=activity" | head -20   # ETag / X-Poll-After
curl -s -H "Authorization: Bearer $TOKEN" "$ARR/v1/debug/bundle" -o gateway-debug.zip
```

FastAPI's Swagger UI is at `/docs` (click Authorize and paste the token).


---

## Configuration and logging reference

Nothing tunable or user-facing is hardcoded. Each platform has one config manager reading JSON, so adding a setting or a message means editing JSON, not code:

| Platform | Manager | Settings | Strings |
|---|---|---|---|
| Gateway (Python) | `backend/app/config.py`: `cfg("logging.retention_days")`, `msg("errors.batch_max", max=5)` | `backend/app/config/settings.json` | `backend/app/config/messages.json` |
| iPhone (Swift) | `Core/AppConfig.swift`: `AppConfig.int(...)`, `AppConfig.text(...)` | `Resources/Config/settings.json` | `Resources/Config/strings.json` |
| Web (JS) | `web/js/config.js`: `cfg(...)`, `text(...)` | `web/config/settings.json` | `web/config/strings.json` |

Settings hold timings, TTLs, polling intervals, batch size, log retention and so on. The gateway lets an environment variable override a setting if it is listed under `"env"` in `settings.json` (today `LOG_LEVEL`, `BATCH_MAX`, `ARR_TIMEOUT`, `IDLE_HEALTH_CHECK`). Strings are error messages, log lines and other text, with `{name}` placeholders. Missing keys fail loudly in Python (and a test checks every message is used); Swift and JS fall back to the key text.

Logging works the same way on all three:
- Every line carries the calling function. Gateway: `%(funcName)s:%(lineno)d`. Swift: `Module.function`. Web: `module.function`.
- `@traced` (Python), `AppLog.enter()` (Swift) and `log.traced` / `log.enter()` (web) log one entry and one exit with the duration for functions with many return paths.
- Logs roll daily. Gateway and iPhone use files (`gateway.log` / `app.log` as the main file, dated as `…-YYYYMMDD.log`, plus per-area files `arr.log`… / `api.log`). The web app keeps one localStorage buffer per day (`ayr.log.YYYYMMDD`, 7 days of up to 128 KB, so it never crowds out the catalogue cache) and sends all retained days with the debug report. Web DEBUG lines go to the browser console only unless `logging.store_debug` is on.
- Request ids tie the three together (§5.1).
- Secrets are redacted everywhere before anything is written.
