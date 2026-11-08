#!/usr/bin/env bash
# BUILD FOR THE BROWSER.
# Builds and (re)starts the Phonarr container on the server that runs Radarr and Sonarr.
# One container serves the gateway API (/v1), the web app (/) and the API console (/console/).
# Then open http://<server>:8787/ on any device, or its Tailscale address on your iPhone.
#
# Uses backend/docker-compose.yml (builds from this checkout). If your Arr stack has one big compose
# file, paste backend/compose-entry.yml into it instead and skip this script (see docs/DEPLOYMENT.md §2).
set -euo pipefail
cd "$(dirname "$0")"
source scripts/lib.sh

need docker "Install Docker with the compose plugin."
need curl "Install curl (used to check the container answers)."
if [ ! -f backend/.env ]; then
  cp backend/.env.example backend/.env
  die "Created backend/.env. Set ARR_GATEWAY_TOKEN (openssl rand -base64 32), ARR_NETWORK, PUID/PGID and TZ in it, then run ./build-web.sh again."
fi
grep -q '^ARR_GATEWAY_TOKEN=change-me' backend/.env && die "Set ARR_GATEWAY_TOKEN in backend/.env first (openssl rand -base64 32)."
network=$(sed -n 's/^ARR_NETWORK=//p' backend/.env); network=${network:-arr}
docker network inspect "$network" >/dev/null 2>&1 \
  || die "Docker network '$network' (ARR_NETWORK in backend/.env) doesn't exist. Run: docker network ls, and set the one your radarr/sonarr containers use (docker inspect radarr shows it)."

say "Building and starting the container (gateway + web app)"
mkdir -p backend/data   # created as you, not root; the container also fixes ownership (PUID/PGID) on start
VERSION=$(git describe --always --dirty 2>/dev/null || echo dev) docker compose -f backend/docker-compose.yml up -d --build

say "Waiting for it to answer"
bash scripts/wait-for-health.sh http://localhost:8787
say "Open http://<this-server>:8787/ (web app) or http://<this-server>:8787/console/ (API console)"
