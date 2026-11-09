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


# --- where files live ------------------------------------------------------------------------------
# Three kinds of files, kept apart (see README "Where things live"):
#   data    storage.data_dir:   everything the user made and can't rebuild. Exported/imported whole.
#   output  storage.output_dir: logs and other generated files. Safe to delete.
#   secrets backend/.env (ARR_GATEWAY_TOKEN, RADARR_API_KEY ...): never in the image or an export.
# Both folders are relative to the project root: the folder holding this `app` package
# (backend/ in a checkout, /app in the Docker image). DATA_DIR / OUTPUT_DIR move a whole folder.

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ConfigError(Exception):
    """A setting that can't work. The message names the file and the key."""


def _folder(key: str) -> Path:
    override = os.environ.get(cfg(f"{key}_env"), "").strip()
    return Path(override).resolve() if override else (PROJECT_ROOT / cfg(key)).resolve()


def data_dir() -> Path:
    return _folder("storage.data_dir")


def output_dir() -> Path:
    return _folder("storage.output_dir")


def data_file(key: str, base: Path | None = None) -> Path:
    """A file in the data folder by its storage.data_files key, e.g. data_file("database")."""
    return (base or data_dir()) / cfg(f"storage.data_files.{key}")


def output_path(key: str, base: Path | None = None) -> Path:
    """A path in the output folder by its storage.output_paths key, e.g. output_path("logs")."""
    return (base or output_dir()) / cfg(f"storage.output_paths.{key}")


def _source(dotted: str) -> Path:
    """The settings file a key's value comes from: the CONFIG_DIR override if it sets it, else the default."""
    override = os.environ.get("CONFIG_DIR")
    path = Path(override) / "settings.json" if override else None
    if path and path.exists():
        try:
            _dig(json.loads(path.read_text(encoding="utf-8")), dotted)
            return path
        except (KeyError, TypeError):
            pass
    return _DEFAULT_DIR / "settings.json"


def _inside(value: str, dotted: str, folder_key: str) -> None:
    p = Path(value)
    if p.is_absolute() or not p.parts or ".." in p.parts:
        raise ConfigError(msg("errors.config_path_outside", file=_source(dotted), setting=dotted, value=value,
                              folder=cfg(folder_key)))


def check_paths() -> None:
    """Raise ConfigError unless every data path sits inside the data folder, every output path inside
    the output folder, and neither folder contains the other (an export must not pick up logs)."""
    for key, value in cfg("storage.data_files").items():
        _inside(value, f"storage.data_files.{key}", "storage.data_dir")
    for key, value in cfg("storage.output_paths").items():
        _inside(value, f"storage.output_paths.{key}", "storage.output_dir")
    data, out = data_dir(), output_dir()
    if data == out or data in out.parents or out in data.parents:
        raise ConfigError(msg("errors.config_folders_overlap", file=_source("storage.output_dir"),
                              data=data, output=out))
