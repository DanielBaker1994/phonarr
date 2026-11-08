"""SQLite persistence. One small file, stdlib only.

Holds Arr credentials, defaults, request records and a metadata
cache. Access is serialised with a lock; volumes are tiny.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS service_config (
    service     TEXT PRIMARY KEY,
    base_url    TEXT NOT NULL,
    api_key     TEXT NOT NULL,
    version     TEXT,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    media_type         TEXT NOT NULL,
    external_id        INTEGER NOT NULL,
    arr_id             INTEGER,
    title              TEXT,
    error_kind         TEXT,
    error_message      TEXT,
    quality_profile_id INTEGER,
    root_folder        TEXT,
    idempotency_key    TEXT,
    created_at         REAL NOT NULL,
    updated_at         REAL NOT NULL,
    UNIQUE (media_type, external_id)
);
CREATE TABLE IF NOT EXISTS metadata_cache (
    media_type  TEXT NOT NULL,
    external_id INTEGER NOT NULL,
    payload     TEXT NOT NULL,
    fetched_at  REAL NOT NULL,
    PRIMARY KEY (media_type, external_id)
);
"""


class Database:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    def _q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    # --- service config -------------------------------------------------
    def get_service(self, service: str) -> dict | None:
        rows = self._q("SELECT * FROM service_config WHERE service=?", (service,))
        return dict(rows[0]) if rows else None

    def save_service(self, service: str, base_url: str, api_key: str, version: str | None) -> None:
        self._q(
            "INSERT INTO service_config(service, base_url, api_key, version, updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(service) DO UPDATE SET base_url=excluded.base_url, api_key=excluded.api_key, "
            "version=excluded.version, updated_at=excluded.updated_at",
            (service, base_url, api_key, version, time.time()),
        )

    def delete_service(self, service: str) -> None:
        self._q("DELETE FROM service_config WHERE service=?", (service,))

    # --- settings ---------------------------------------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        rows = self._q("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_setting(self, key: str, value: Any) -> None:
        self._q(
            "INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    # --- requests ---------------------------------------------------------
    def get_request(self, media_type: str, external_id: int) -> dict | None:
        rows = self._q("SELECT * FROM requests WHERE media_type=? AND external_id=?", (media_type, external_id))
        return dict(rows[0]) if rows else None

    def list_requests(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM requests ORDER BY updated_at DESC")]

    def upsert_request(self, media_type: str, external_id: int, **fields: Any) -> dict:
        now = time.time()
        existing = self.get_request(media_type, external_id)
        if existing is None:
            # INSERT OR IGNORE + unique constraint: a racing duplicate cannot create a 2nd row.
            self._q(
                "INSERT OR IGNORE INTO requests(media_type, external_id, created_at, updated_at) VALUES (?,?,?,?)",
                (media_type, external_id, now, now),
            )
        if fields:
            cols = ", ".join(f"{k}=?" for k in fields)
            self._q(
                f"UPDATE requests SET {cols}, updated_at=? WHERE media_type=? AND external_id=?",
                (*fields.values(), now, media_type, external_id),
            )
        return self.get_request(media_type, external_id)  # type: ignore[return-value]

    # --- metadata cache ---------------------------------------------------
    def get_metadata(self, media_type: str, external_id: int, max_age: float) -> dict | None:
        rows = self._q(
            "SELECT payload, fetched_at FROM metadata_cache WHERE media_type=? AND external_id=?",
            (media_type, external_id),
        )
        if rows and time.time() - rows[0]["fetched_at"] < max_age:
            return json.loads(rows[0]["payload"])
        return None

    def save_metadata(self, media_type: str, external_id: int, payload: dict) -> None:
        self._q(
            "INSERT INTO metadata_cache VALUES (?,?,?,?) ON CONFLICT(media_type, external_id) "
            "DO UPDATE SET payload=excluded.payload, fetched_at=excluded.fetched_at",
            (media_type, external_id, json.dumps(payload), time.time()),
        )
