"""Logging setup: console + daily files in the output folder (storage.output_paths.logs), with secret redaction.

Every Arr API key and the gateway token are registered as secrets and masked in
every log line, so log files and debug bundles are safe to share.
"""
from __future__ import annotations

import contextvars
import datetime
import functools
import inspect
import logging
import logging.handlers
import os
import re
import time
from pathlib import Path

from .config import cfg, msg, output_path

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
    """Redacts secrets, and stamps every line in UTC ISO 8601 with milliseconds and Z (KB-11), the same
    form as the web log's toISOString() and the iPhone log, so gateway and app lines line up."""
    converter = time.gmtime

    def __init__(self, fmt: str | None = None, datefmt: str | None = None) -> None:
        super().__init__(fmt, datefmt or cfg("logging.timestamp_format"))

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def log_dir(base: Path | None = None) -> Path:
    """The logs folder: rebuildable output, never in the data folder or an export."""
    d = output_path("logs", base)
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
        path, when="midnight", utc=cfg("logging.roll_utc"), backupCount=cfg("logging.retention_days"), encoding="utf-8")
    handler.suffix = cfg("logging.date_format")
    handler.extMatch = re.compile(r"^\d{8}$")
    stem, ext = path.stem, path.suffix
    handler.namer = lambda name: str(path.with_name(f"{stem}-{name.rsplit('.', 1)[-1]}{ext}"))
    handler.setFormatter(fmt)
    handler.addFilter(RequestIdFilter())
    return handler


def setup_logging(base: Path | None = None, level: str | None = None) -> None:
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
    root.addHandler(_daily_handler(log_dir(base) / cfg("logging.main_file"), fmt))
    for area, file_name in cfg("logging.area_files").items():
        lg = logging.getLogger(area)
        for h in [h for h in lg.handlers if isinstance(h, logging.handlers.TimedRotatingFileHandler)]:
            lg.removeHandler(h)
            h.close()
        lg.addHandler(_daily_handler(log_dir(base) / file_name, fmt))   # still propagates to the main file
    # uvicorn's own loggers go through the root handlers too
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True


# ------------------------------------------------------------------ reading the log back (Settings → Logs)
def utc_iso(when: datetime.datetime | None = None) -> str:
    """UTC ISO 8601 with milliseconds and Z, the one timestamp form every log line and frontend uses."""
    when = (when or datetime.datetime.now(datetime.timezone.utc)).astimezone(datetime.timezone.utc)
    return when.strftime(cfg("logging.timestamp_format")) + f".{when.microsecond // 1000:03d}Z"


def local_timezone() -> str:
    """The gateway's timezone as an operator would name it: the TZ env (Docker) or the local zone's name."""
    return os.environ.get("TZ") or datetime.datetime.now().astimezone().tzname() or "UTC"


_STRFTIME_RE = {"%Y": r"\d{4}", "%m": r"\d{2}", "%d": r"\d{2}", "%H": r"\d{2}", "%M": r"\d{2}", "%S": r"\d{2}"}
_FIELD_RE = {"msecs": r"\d{3}", "levelname": r"[A-Z]+", "request_id": r"[^\]\s]*", "name": r"\S+?",
             "funcName": r"[^\s.:]+", "lineno": r"\d+", "message": r".*"}


def _line_parser(fmt: str, timestamp_format: str) -> re.Pattern:
    """A regex with named groups for one logging format string (logging.format, or a legacy one)."""
    ts = "".join(_STRFTIME_RE.get(part, re.escape(part)) for part in re.split(r"(%[A-Za-z])", timestamp_format) if part)
    out, seen = [], set()
    for part in re.split(r"(%\(\w+\)[-#0 +]*\d*[sdf])", fmt):
        field = re.match(r"%\((\w+)\)", part)
        if not field:
            out.append(re.escape(part))
            continue
        name = field.group(1)
        pattern = ts if name == "asctime" else _FIELD_RE.get(name, r".*?")
        out.append(f"(?:{pattern})" if name in seen else f"(?P<{name}>{pattern})")
        seen.add(name)
    return re.compile("^" + "".join(out) + "$", re.DOTALL)


def _parsers() -> list[tuple[re.Pattern, str, bool]]:
    """(regex, timestamp format, written in UTC): the current format first, then older ones still on disk
    after an upgrade (logging.view.legacy_formats)."""
    found = [(_line_parser(cfg("logging.format"), cfg("logging.timestamp_format")), cfg("logging.timestamp_format"), True)]
    for old in cfg("logging.view.legacy_formats"):
        found.append((_line_parser(old["format"], old["timestamp_format"]), old["timestamp_format"], old["utc"]))
    return found


def _parse_line(line: str, parsers) -> dict | None:
    for regex, ts_format, is_utc in parsers:
        m = regex.match(line)
        if not m:
            continue
        g = m.groupdict()
        try:
            when = datetime.datetime.strptime(g["asctime"], ts_format).replace(microsecond=int(g.get("msecs") or 0) * 1000)
        except (KeyError, ValueError):
            continue
        when = when.replace(tzinfo=datetime.timezone.utc) if is_utc else when.astimezone()   # naive local -> aware
        where = g.get("name") or ""
        if g.get("funcName"):
            where += f".{g['funcName']}"
        if g.get("lineno"):
            where += f":{g['lineno']}"
        return {"ts": utc_iso(when), "level": g.get("levelname") or "", "rid": g.get("request_id") or "-",
                "source": cfg("logging.view.source"), "where": where, "message": g.get("message") or ""}
    return None


def _reverse_lines(path: Path, budget: list[int]):
    """The lines of `path`, last first, read in chunks from the end, so a huge file is never read whole.
    Spends `budget[0]` bytes (shared across files) and yields None if it ran out before the start."""
    chunk = cfg("logging.view.chunk_bytes")
    try:
        f = path.open("rb")
    except OSError:
        return
    with f:
        pos = f.seek(0, 2)
        carry = b""
        while pos > 0:
            if budget[0] <= 0:
                yield None
                return
            step = min(chunk, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + carry
            budget[0] -= step
            parts = data.split(b"\n")
            carry = parts[0]
            for raw in reversed(parts[1:]):
                yield raw.decode("utf-8", errors="replace").rstrip("\r")
        yield carry.decode("utf-8", errors="replace").rstrip("\r")


def level_number(name: str) -> int | None:
    """WARNING / warn / Error ... -> the logging level number; None if it isn't a level."""
    name = cfg("logging.view.level_aliases").get(name.upper(), name.upper())
    value = logging.getLevelName(name)
    return value if isinstance(value, int) else None


@traced
def read_log_tail(directory: Path, lines: int, level: str | None = None, q: str | None = None,
                  rid: str | None = None) -> tuple[list[dict], bool]:
    """The last `lines` matching entries of the main log (gateway.log, then the newest rolled days if
    needed), oldest first, parsed with the configured format and redacted again. A line that doesn't parse
    (a traceback) belongs to the entry above it. The flag says older lines exist that weren't returned."""
    main = directory / cfg("logging.main_file")
    stem, ext = main.stem, main.suffix
    rolled = sorted(p for p in directory.glob(f"{stem}-*{ext}")
                    if re.fullmatch(rf"{re.escape(stem)}-\d{{8}}{re.escape(ext)}", p.name))
    files = [main] + rolled[::-1][:cfg("logging.view.previous_files")]
    minimum = level_number(level) if level else None
    needle = q.lower() if q else None
    parsers = _parsers()
    budget = [cfg("logging.view.max_scan_bytes")]
    found: list[dict] = []
    pending: list[str] = []   # continuation lines (read bottom-up) waiting for the header line above them

    def keep(entry: dict) -> bool:
        if minimum is not None and (level_number(entry["level"]) or 0) < minimum:
            return False
        if rid and entry["rid"] != rid and rid not in entry["message"]:
            return False
        return not needle or needle in f"{entry['where']} {entry['rid']} {entry['message']}".lower()

    def add(entry: dict) -> bool:
        if pending:
            entry["message"] = "\n".join([entry["message"], *reversed(pending)])
            pending.clear()
        entry["message"], entry["where"] = redact(entry["message"]), redact(entry["where"])
        if keep(entry):
            found.append(entry)
        return len(found) >= lines

    def more_left(rest, later: list[Path]) -> bool:
        """Anything older than what was returned: the rest of this file, or a later file with content."""
        return any(line is None or line for line in rest) or any(p.exists() and p.stat().st_size for p in later)

    for i, path in enumerate(files):
        lines_up = _reverse_lines(path, budget)
        for line in lines_up:
            if line is None:
                return found[::-1], True
            entry = _parse_line(line, parsers)
            if entry is None:
                if line:
                    pending.append(line)
            elif add(entry):
                return found[::-1], more_left(lines_up, files[i + 1:])
    if pending:   # the oldest file starts with lines that have no header: show them as they are
        add({"ts": None, "level": "", "rid": "-", "source": cfg("logging.view.source"), "where": "",
             "message": pending.pop()})
    return found[::-1], False
