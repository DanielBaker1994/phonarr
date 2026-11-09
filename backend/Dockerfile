# Phonarr: one image with the gateway API (/v1/*) and the web frontend (/), plus the API console (/console/).
# Build context is the REPO ROOT (it needs both backend/ and web/):
#   docker build -f backend/Dockerfile -t phonarr .
# ./build-web.sh and .github/workflows/docker-image.yml both do exactly this.
FROM python:3.12-slim

ARG VERSION=dev
LABEL org.opencontainers.image.title="Phonarr" \
      org.opencontainers.image.description="Discover and request films and TV into Radarr and Sonarr: gateway API + web app" \
      org.opencontainers.image.source="https://github.com/DanielBaker1994/phonarr" \
      org.opencontainers.image.version="${VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app WEB_DIR=/app/web APP_VERSION=${VERSION} \
    PUID=1000 PGID=1000 TZ=Etc/UTC
# /app is the project root: app/ (code), web/, data/ (bind-mount from the host: everything to keep)
# and cache/ (logs and other rebuildable output). Paths come from app/config/settings.json "storage".
WORKDIR /app
# tzdata so TZ=Europe/London (etc.) works: log timestamps and the midnight log roll follow your timezone.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY web ./web
RUN useradd --system --uid 1000 gateway && mkdir -p /app/data /app/cache && chown gateway /app/data /app/cache \
 && printf '#!/bin/sh\nexec python -m app "$@"\n' > /usr/local/bin/phonarr && chmod +x /usr/local/bin/phonarr
# `phonarr export-data -` / `phonarr import-data --replace -` move the data folder (README "Moving to another machine").
# No USER line on purpose: app/boot.py (the ENTRYPOINT) starts as root, gives PUID:PGID /app/data and /app/cache
# (Docker creates a missing bind-mount folder as root), then drops to PUID:PGID before the gateway starts.
# The gateway itself never runs as root. Prefer `user: "1000:1000"` in compose? That works too; boot.py
# then only checks /app/data is writable and says how to fix it if not.
EXPOSE 8787
VOLUME ["/app/data"]
# "healthy" = the gateway answers. Radarr/Sonarr being down shows as status "degraded" in /v1/health, not as unhealthy,
# so Docker never restarts the gateway because an Arr app is offline.
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8787/v1/health', timeout=4); sys.exit(0)"
# Optional: put a titles list at data/titles.json (see backend/examples/titles.example.json).
# --no-access-log: the gateway logs each /v1 call itself, with a request id (app/main.py request_log).
ENTRYPOINT ["python", "-m", "app.boot"]
CMD ["uvicorn", "app.main:_app_factory", "--factory", "--host", "0.0.0.0", "--port", "8787", "--proxy-headers", "--no-access-log"]
