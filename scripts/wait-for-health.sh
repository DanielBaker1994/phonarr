#!/usr/bin/env bash
# Waits up to 60 s for <url>/v1/health to answer, then prints it. Used by ../build-web.sh.
# If it never answers, prints the container's last log lines so you see why without a second command.
set -euo pipefail
url="${1:-http://localhost:8787}"
pretty() { if command -v python3 >/dev/null 2>&1; then python3 -m json.tool --no-ensure-ascii; else cat; echo; fi; }
for _ in $(seq 1 30); do
  if body=$(curl -fsS "$url/v1/health" 2>/dev/null); then
    printf '%s' "$body" | pretty | head -20
    exit 0
  fi
  sleep 2
done
echo "No answer from $url/v1/health after 60 s. Last container log lines:" >&2
docker compose -f backend/docker-compose.yml logs --tail 30 phonarr >&2 || true
echo "More: docker compose -f backend/docker-compose.yml logs phonarr, or the files in /app/cache/logs inside the container" >&2
exit 1
