"""Where discovery titles come from.

1. Your titles list (optional): a JSON file of TMDB / TVDB IDs you want to
   browse, e.g. everything your sources carry. IDs only; titles, posters and
   metadata are looked up through Radarr/Sonarr.
2. Radarr Discover: your Radarr import lists plus Radarr's recommendations
   (GET /api/v3/importlist/movie?includeRecommendations=true).
3. Your library: titles already in Radarr/Sonarr (shown when "show requested"
   is on, and used for status).
4. Search: typing in the search field also runs a Radarr/Sonarr lookup, so any
   title Radarr/Sonarr can find can be requested.
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import msg
from .logs import get_logger

log = get_logger("sources")

ID_FIELD = {"movie": "tmdb_id", "series": "tvdb_id"}
LIST_KEY = {"movie": "movies", "series": "series"}


class TitleList:
    """Reads titles.json: {"movies": [10331, {"tmdb_id": 653}], "series": [{"tvdb_id": 70001}]}.

    Plain numbers and objects with tmdb_id / tvdb_id are both accepted; any
    other keys are ignored. A missing file just means an empty list. The file
    is re-read when it changes, so edits apply without a restart.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._mtime: float | None = None
        self._ids: dict[str, list[int]] = {"movie": [], "series": []}

    def _load(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            self._ids, self._mtime = {"movie": [], "series": []}, None
            return
        if mtime == self._mtime:
            return
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError) as exc:
            log.error(msg("log.titles_unreadable", path=self.path, error=exc))
            return
        ids: dict[str, list[int]] = {"movie": [], "series": []}
        for media_type in ("movie", "series"):
            seen = set()
            for item in raw.get(LIST_KEY[media_type], []) if isinstance(raw, dict) else []:
                value = item.get(ID_FIELD[media_type]) if isinstance(item, dict) else item
                try:
                    ext = int(value)
                except (TypeError, ValueError):
                    log.warning(msg("log.titles_skipped", media_type=media_type, field=ID_FIELD[media_type], item=item))
                    continue
                if ext not in seen:
                    seen.add(ext)
                    ids[media_type].append(ext)
        self._ids, self._mtime = ids, mtime
        log.info(msg("log.titles_loaded", path=self.path, movies=len(ids["movie"]), series=len(ids["series"])))

    def ids(self, media_type: str) -> list[int]:
        self._load()
        return list(self._ids[media_type])

    def counts(self) -> dict:
        self._load()
        return {"file": str(self.path), "exists": self.path.exists(),
                "movies": len(self._ids["movie"]), "series": len(self._ids["series"])}
