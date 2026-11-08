"""Logging setup: console + rotating file in DATA_DIR/logs, with secret redaction.

Every Arr API key and the gateway token are registered as secrets and masked in
every log line, so log files and debug bundles are safe to share.
"""
from __future__ import annotations

import contextvars
import functools
import inspect
import logging
import logging.handlers
import re
import time
from pathlib import Path

from .config import cfg, msg

_SECRETS: set[str] = set()

# The request being served, so every log line it causes can be matched to the X-Request-Id the
# frontends log and show. "-" outside a request (poller, startup).
REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = REQUEST_ID.get()
        return True
_PATTERNS = [
    re.compile(r"(?i)(x-api-key|apikey|api_key|authorization|bearer|token)([\"']?\s*[:=]\s*[\"']?|\s+)([A-Za-z0-9._\-]{8,})"),
]


def register_secret(value: str | None) -> None:
    if value and len(value) >= 6:
        _SECRETS.add(value)


def redact(text: str) -> str:
    for secret in sorted(_SECRETS, key=len, reverse=True):
        text = text.replace(secret, "***")
    for pat in _PATTERNS:
        text = pat.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def log_dir(data_dir: Path) -> Path:
    d = data_dir / cfg("logging.dir_name")
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_logger(name: str) -> logging.Logger:
    """The one way modules get a logger. Records carry function name and line (see logging.format)."""
    return logging.getLogger(name)


def _describe_args(fn, args, kwargs) -> str:
    """Compact "a=1, b='x'" of the call, skipping self/cls and truncating long values."""
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
    except TypeError:
        return ""
    parts = []
    for k, v in bound.arguments.items():
        if k in ("self", "cls"):
            continue
        r = repr(v)
        parts.append(f"{k}={r if len(r) <= 80 else r[:77] + '...'}")
    return ", ".join(parts)


def traced(fn=None, *, level: str | None = None):
    """Log entry and a single exit (or the exception) of a function, with duration.

    Use on functions with many return paths instead of logging at each one. Works on sync and
    async functions. The record's funcName/lineno point at the wrapped function.
    Arguments are logged repr'd and redacted, so secrets never reach the file.
    """
    def wrap(f):
        log = logging.getLogger(f.__module__.rsplit(".", 1)[-1])
        lvl = getattr(logging, (level or cfg("logging.trace_level")).upper())
        name = f.__qualname__   # the wrapper is what logging sees as funcName, so name the function in the text

        def enter(args, kwargs):
            if log.isEnabledFor(lvl):
                log.log(lvl, msg("log.trace_enter", func=name, args=_describe_args(f, args, kwargs)))
            return time.perf_counter()

        def done(t0, key, **values):
            if log.isEnabledFor(lvl):
                log.log(lvl, msg(key, func=name, ms=round((time.perf_counter() - t0) * 1000, 1), **values))

        if inspect.iscoroutinefunction(f):
            @functools.wraps(f)
            async def awrapper(*args, **kwargs):
                t0 = enter(args, kwargs)
                try:
                    result = await f(*args, **kwargs)
                except BaseException as exc:
                    done(t0, "log.trace_error", error_type=type(exc).__name__, error=exc)
                    raise
                done(t0, "log.trace_exit")
                return result
            return awrapper

        @functools.wraps(f)
        def swrapper(*args, **kwargs):
            t0 = enter(args, kwargs)
            try:
                result = f(*args, **kwargs)
            except BaseException as exc:
                done(t0, "log.trace_error", error_type=type(exc).__name__, error=exc)
                raise
            done(t0, "log.trace_exit")
            return result
        return swrapper

    return wrap(fn) if fn is not None else wrap


def _daily_handler(path: Path, fmt: logging.Formatter) -> logging.Handler:
    """Writes to `path` (e.g. gateway.log) and rolls at midnight to gateway-YYYYMMDD.log, keeping retention_days."""
    handler = logging.handlers.TimedRotatingFileHandler(
        path, when="midnight", backupCount=cfg("logging.retention_days"), encoding="utf-8")
    handler.suffix = cfg("logging.date_format")
    handler.extMatch = re.compile(r"^\d{8}$")
    stem, ext = path.stem, path.suffix
    handler.namer = lambda name: str(path.with_name(f"{stem}-{name.rsplit('.', 1)[-1]}{ext}"))
    handler.setFormatter(fmt)
    handler.addFilter(RequestIdFilter())
    return handler


def setup_logging(data_dir: Path, level: str | None = None) -> None:
    fmt = RedactingFormatter(cfg("logging.format"))
    root = logging.getLogger()
    root.setLevel(level or cfg("logging.level"))
    for noisy in cfg("logging.quiet_loggers"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    for h in list(root.handlers):
        root.removeHandler(h)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(RequestIdFilter())
    root.addHandler(console)
    # Main file to tail: every record from every module. Area files hold one module's logic only.
    root.addHandler(_daily_handler(log_dir(data_dir) / cfg("logging.main_file"), fmt))
    for area, file_name in cfg("logging.area_files").items():
        lg = logging.getLogger(area)
        for h in [h for h in lg.handlers if isinstance(h, logging.handlers.TimedRotatingFileHandler)]:
            lg.removeHandler(h)
            h.close()
        lg.addHandler(_daily_handler(log_dir(data_dir) / file_name, fmt))   # still propagates to the main file
    # uvicorn's own loggers go through the root handlers too
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
