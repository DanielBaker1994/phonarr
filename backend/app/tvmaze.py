"""TV discovery from TVmaze (https://www.tvmaze.com/api): free, no account and no API key.

Sonarr has no discovery feed (Radarr's import lists can be browsed; Sonarr's can only auto-add), so on its own
the TV tab only ever has your library, which discovery hides. This scans TVmaze's show index in the background,
keeps the most popular shows that carry a TVDB ID (TVmaze's `weight`, 0-100), and the gateway looks each one up
through Sonarr like any other title. Only TVDB IDs are kept from TVmaze; posters, ratings and genres come from
Sonarr so the cards match everything else. Nothing about the user or their library is sent to TVmaze.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .config import cfg, msg
from .logs import get_logger, traced

log = get_logger("tvmaze")


def select(shows: list[dict], count: int, min_weight: int) -> list[dict]:
    """The `count` most popular shows that have a TVDB ID and at least `min_weight`, most popular first.
    Pure, so it can be tested without the network."""
    picked: dict[int, dict] = {}
    for s in shows:
        tvdb = (s.get("externals") or {}).get("thetvdb")
        weight = s.get("weight") or 0
        if not isinstance(tvdb, int) or weight < min_weight:
            continue
        entry = {"tvdb_id": tvdb, "weight": weight, "name": s.get("name"), "premiered": s.get("premiered")}
        if tvdb not in picked or picked[tvdb]["weight"] < weight:
            picked[tvdb] = entry
    ranked = sorted(picked.values(), key=lambda e: (-e["weight"], -(int((e["premiered"] or "0")[:4] or 0))))
    return ranked[:count]


class TVmaze:
    """Walks /shows?page=N (250 shows a page, ordered by TVmaze id) until the end, at a gentle pace."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport

    @traced
    async def popular(self) -> list[dict]:
        base = cfg("tvmaze.base_url").rstrip("/")
        gap = cfg("tvmaze.request_gap")
        shows: list[dict] = []
        page = 0
        async with httpx.AsyncClient(timeout=cfg("tvmaze.timeout"), transport=self.transport,
                                     headers={"User-Agent": cfg("tvmaze.user_agent")}) as client:
            while page < cfg("tvmaze.max_pages"):
                r = await self._get(client, f"{base}/shows", page)
                if r is None:          # 404 = past the last page
                    break
                shows.extend(x for x in r if isinstance(x, dict))
                page += 1
                await asyncio.sleep(gap)
        picked = select(shows, cfg("tvmaze.count"), cfg("tvmaze.min_weight"))
        log.info(msg("log.tvmaze_scanned", pages=page, shows=len(shows), kept=len(picked)))
        return picked

    async def _get(self, client: httpx.AsyncClient, url: str, page: int) -> list[Any] | None:
        for attempt in range(cfg("tvmaze.retries") + 1):
            r = await client.get(url, params={"page": page})
            if r.status_code == 404:
                return None
            if r.status_code == 429:   # rate limited: TVmaze asks clients to back off and retry
                await asyncio.sleep(cfg("tvmaze.rate_limit_wait") * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else []
        raise httpx.HTTPError(msg("log.tvmaze_rate_limited", page=page))
