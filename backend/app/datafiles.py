"""Export and import the whole data folder as one .zip, for moving an install to another machine.

    python -m app export-data [FILE|-]            (`phonarr export-data` in the Docker image)
    python -m app import-data FILE|- [--replace]

The zip holds manifest.json (format, version, created time, and each file's path, size and SHA-256)
and the data folder's files under data/. SQLite files are copied with SQLite's backup API, so an
export is consistent while the gateway is running; -wal/-shm/-journal files are skipped.
Secrets never go in: secret files (storage.secret_files) are skipped and kept on import, and the
saved Radarr/Sonarr connections (which hold API keys) are removed from the copy (export.scrub).

Import checks everything before it touches the data folder: the manifest, every checksum and size,
and every path (no absolute paths, no ".."). It refuses a data folder that already has data unless
replace=True, unpacks into a staging folder inside the data folder, then empties the data folder
(never deleting the folder itself: it may be a mount point) and moves the staged files in.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator

from .config import cfg, msg
from .logs import get_logger, traced

log = get_logger("datafiles")


class DataFileError(Exception):
    """An export or import that can't go ahead. The message says why; nothing was changed."""


def _skipped(rel: PurePosixPath) -> bool:
    secrets = {cfg(f"storage.data_files.{k}") for k in cfg("storage.secret_files")}
    return (str(rel) in secrets or rel.name in cfg("storage.ignored_files")
            or rel.parts[0].startswith(cfg("export.staging_prefix"))
            or any(rel.name.endswith(s) for s in cfg("export.skip_suffixes")))


def _data_files(data: Path) -> Iterator[tuple[PurePosixPath, Path]]:
    """(path relative to the data folder, absolute path) for every file an export should hold."""
    for path in sorted(p for p in data.rglob("*") if p.is_file() and not p.is_symlink()):
        rel = PurePosixPath(path.relative_to(data).as_posix())
        if not _skipped(rel):
            yield rel, path


def _is_sqlite(path: Path) -> bool:
    header = cfg("export.sqlite_header").encode()
    with path.open("rb") as f:
        return f.read(len(header)) == header


def _sqlite_copy(src: Path, dest: Path, scrub: list[str]) -> None:
    """A consistent snapshot of a live SQLite file (backup API), minus anything in scrub."""
    source = sqlite3.connect(str(src))
    target = sqlite3.connect(str(dest))
    try:
        source.backup(target)
        for sql in scrub:
            target.execute(sql)
        target.commit()
        target.execute("PRAGMA journal_mode=DELETE")   # one self-contained file, no -wal needed
        if scrub:
            target.execute("VACUUM")                   # deleted rows linger in free pages until a VACUUM
    finally:
        target.close()
        source.close()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(cfg("export.chunk_bytes")):
            h.update(chunk)
    return h.hexdigest()


@traced
def export_data(out: BinaryIO, data: Path) -> dict:
    """Write the data folder to `out` as one zip. Returns the manifest."""
    scrubs = {cfg(f"storage.data_files.{k}"): sql for k, sql in cfg("export.scrub").items()}
    folder = cfg("export.folder")
    files = []
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, path in _data_files(data):
            src = path
            if _is_sqlite(path):
                src = Path(tmp) / f"{len(files)}.sqlite3"
                _sqlite_copy(path, src, scrubs.get(str(rel), []))
            files.append({"path": str(rel), "size": src.stat().st_size, "sha256": _sha256(src)})
            z.write(src, f"{folder}/{rel}")
        manifest = {"format": cfg("export.format"), "version": cfg("export.version"),
                    "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "files": files}
        z.writestr(cfg("export.manifest"), json.dumps(manifest, indent=2))
    return manifest


def _safe(name: str) -> PurePosixPath | None:
    """The path relative to the data folder if `name` is a safe zip member under it, else None."""
    if "\\" in name or ":" in name:
        return None
    p = PurePosixPath(name)
    folder = cfg("export.folder")
    if p.is_absolute() or ".." in p.parts or len(p.parts) < 2 or p.parts[0] != folder:
        return None
    return PurePosixPath(*p.parts[1:])


def _read_manifest(z: zipfile.ZipFile, src: str) -> dict:
    name = cfg("export.manifest")
    if name not in z.namelist():
        raise DataFileError(msg("datafiles.no_manifest", src=src, manifest=name))
    try:
        manifest = json.loads(z.read(name))
        files = {f["path"]: f for f in manifest["files"]}
        fmt, version = manifest["format"], int(manifest["version"])
    except (ValueError, KeyError, TypeError) as exc:
        raise DataFileError(msg("datafiles.bad_manifest", src=src, error=exc))
    if fmt != cfg("export.format"):
        raise DataFileError(msg("datafiles.wrong_format", src=src, found=fmt, format=cfg("export.format")))
    if version > cfg("export.version"):
        raise DataFileError(msg("datafiles.too_new", src=src, found=version, version=cfg("export.version")))
    for path in files:
        rel = _safe(f"{cfg('export.folder')}/{path}") if isinstance(path, str) else None
        if rel is None or str(rel) != path:
            raise DataFileError(msg("datafiles.unsafe_path", src=src, path=path))
    return files


def _check_members(z: zipfile.ZipFile, files: dict, src: str) -> list[tuple[zipfile.ZipInfo, str]]:
    members, seen = [], set()
    for info in z.infolist():
        if info.filename == cfg("export.manifest") or info.is_dir():
            continue
        rel = _safe(info.filename)
        if rel is None or str(rel) in seen:
            raise DataFileError(msg("datafiles.unsafe_path", src=src, path=info.filename))
        if str(rel) not in files:
            raise DataFileError(msg("datafiles.unlisted", src=src, path=info.filename))
        seen.add(str(rel))
        members.append((info, str(rel)))
    for path in files:
        if path not in seen:
            raise DataFileError(msg("datafiles.missing", src=src, path=path))
    return members


def _existing(data: Path, staging: Path | None = None) -> list[Path]:
    """Top-level entries of the data folder, apart from secrets (kept: an export never has them),
    leftovers like .write-test, and the staging folder of the import in progress."""
    keep = {cfg(f"storage.data_files.{k}") for k in cfg("storage.secret_files")} | set(cfg("storage.ignored_files"))
    return sorted(p for p in data.iterdir() if p.name not in keep and p != staging)


@traced
def import_data(src: Path, data: Path, replace: bool = False, label: str | None = None) -> dict:
    """Replace the data folder's contents with the export at `src`. Returns the manifest's files."""
    label = label or str(src)
    if not zipfile.is_zipfile(src):
        raise DataFileError(msg("datafiles.not_zip", src=label))
    data.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src) as z:
        files = _read_manifest(z, label)
        members = _check_members(z, files, label)
        current = [p for p in _existing(data) if not p.name.startswith(cfg("export.staging_prefix"))]
        if current and not replace:
            raise DataFileError(msg("datafiles.not_empty", data=data, example=current[0].name))
        staging = Path(tempfile.mkdtemp(prefix=cfg("export.staging_prefix"), dir=data))
        try:
            for info, rel in members:
                target = staging / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                h, size = hashlib.sha256(), 0
                with z.open(info) as fin, target.open("wb") as fout:
                    while chunk := fin.read(cfg("export.chunk_bytes")):
                        h.update(chunk)
                        size += len(chunk)
                        fout.write(chunk)
                if h.hexdigest() != files[rel]["sha256"] or size != files[rel]["size"]:
                    raise DataFileError(msg("datafiles.damaged", src=label, path=rel))
            # Everything checked out: empty the folder (not the folder itself) and move the new files in.
            for p in _existing(data, staging):
                shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
            for p in staging.iterdir():
                os.replace(p, data / p.name)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return files

