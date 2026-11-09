"""Command line: `python -m app <command>` (`phonarr <command>` in the Docker image).

    export-data [FILE|-]           whole data folder to one .zip (default: stdout)
    import-data FILE|- [--replace] replace the data folder with an export (stop the app first)

Messages go to stderr, so `export-data -` can stream the zip to stdout.
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

from .config import ConfigError, check_paths, data_dir, msg
from .datafiles import DataFileError, export_data, import_data
from .logs import get_logger

log = get_logger("datafiles")


def _drop_root() -> None:
    """`docker compose exec` runs as root: act as PUID:PGID like the gateway, so files keep their owner."""
    if hasattr(os, "getuid") and os.getuid() == 0 and os.environ.get("PUID"):
        from .boot import _ids, drop_privileges
        uid, gid = _ids()
        if uid != 0:
            drop_privileges(uid, gid)


def _export(target: str) -> None:
    data = data_dir()
    if target == "-":
        manifest = export_data(sys.stdout.buffer, data)
        sys.stdout.buffer.flush()
    else:
        with open(target, "wb") as f:
            manifest = export_data(f, data)
    log.info(msg("datafiles.exported", files=len(manifest["files"]), bytes=sum(f["size"] for f in manifest["files"]),
                 data=data, dest="stdout" if target == "-" else target))


def _import(source: str, replace: bool) -> None:
    data = data_dir()
    if source == "-":
        with tempfile.NamedTemporaryFile(suffix=".zip") as f:   # zip needs to seek; stdin can't
            shutil.copyfileobj(sys.stdin.buffer, f)
            f.flush()
            files = import_data(Path(f.name), data, replace, label="stdin")
    else:
        files = import_data(Path(source), data, replace)
    log.info(msg("datafiles.imported", files=len(files), bytes=sum(f["size"] for f in files.values()), data=data))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="phonarr")
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export-data", help=msg("datafiles.usage_export"))
    exp.add_argument("file", nargs="?", default="-")
    imp = sub.add_parser("import-data", help=msg("datafiles.usage_import"))
    imp.add_argument("file")
    imp.add_argument("--replace", action="store_true", help=msg("datafiles.usage_replace"))
    args = parser.parse_args(argv)

    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    try:
        check_paths()
        _drop_root()
        if args.command == "export-data":
            _export(args.file)
        else:
            _import(args.file, args.replace)
    except (ConfigError, DataFileError, OSError) as exc:
        log.error(msg("datafiles.failed", action=args.command, error=exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
