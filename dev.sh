#!/usr/bin/env bash
# DEVELOP LOCALLY (no Docker, no real Radarr/Sonarr needed).
# Runs the gateway with simulated Radarr and Sonarr (DEV_FAKE_ARR=1) and reloads on code changes.
#   Web app:      http://localhost:8787/        (sign in with the token: dev)
#   API console:  http://localhost:8787/console/
#   iOS Simulator: pair with http://localhost:8787 and token dev
# Web changes need only a browser refresh: there is no web build step.
# Port 8787 is the same as the container's: stop the container first if both run on this machine, or PORT=8788 ./dev.sh.
set -euo pipefail
cd "$(dirname "$0")"
source scripts/lib.sh

need_python
python=$(bash scripts/python-env.sh)
port=${PORT:-8787}
say "Gateway with simulated Radarr/Sonarr on http://localhost:$port (token: dev). Ctrl-C to stop."
cd backend
DATA_DIR=./devdata ARR_GATEWAY_TOKEN=dev DEV_FAKE_ARR=1 LOG_LEVEL=DEBUG \
  exec "$python" -m uvicorn app.main:_app_factory --factory --reload --reload-dir app --port "$port"
