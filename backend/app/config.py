"""Config manager: one place to read settings and user-facing text.

Everything tunable lives in JSON next to this file (config/settings.json, config/messages.json)
so code holds no magic numbers or message strings:

    from .config import cfg, msg
    cfg("logging.retention_days")            # dotted lookup; env override if listed in settings.json "env"
    msg("errors.not_configured", name="Radarr")   # str.format template from messages.json

Missing keys fail loudly (KeyError) so typos surface in tests, not in production.
Set CONFIG_DIR to a folder holding either file to override (files are merged key by key).
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

_DEFAULT_DIR = Path(__file__).resolve().parent / "config"


def _merge(base: dict, extra: dict) -> dict:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


@lru_cache(maxsize=None)
def _load(name: str) -> dict:
    data = json.loads((_DEFAULT_DIR / name).read_text(encoding="utf-8"))
    override = os.environ.get("CONFIG_DIR")
    if override and (Path(override) / name).exists():
        _merge(data, json.loads((Path(override) / name).read_text(encoding="utf-8")))
    return data


def _dig(data: dict, dotted: str) -> Any:
    node: Any = data
    for part in dotted.split("."):
        node = node[part]
    return node


def cfg(key: str) -> Any:
    """Setting by dotted key. An env var named in settings.json "env" wins, cast to the default's type."""
    settings = _load("settings.json")
    value = _dig(settings, key)
    env_name = settings.get("env", {}).get(key)
    raw = os.environ.get(env_name) if env_name else None
    if raw not in (None, ""):
        return type(value)(raw) if isinstance(value, (int, float)) and not isinstance(value, bool) else raw
    return value


def msg(key: str, **values: Any) -> str:
    """Text by dotted key from messages.json, formatted with values."""
    template = _dig(_load("messages.json"), key)
    return template.format(**values) if values else template


def reload() -> None:
    """Forget cached files (tests)."""
    _load.cache_clear()
