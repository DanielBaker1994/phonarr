"""Container entrypoint: `python -m app.boot <command...>` (the Dockerfile's ENTRYPOINT).

Makes the image behave like the rest of an Arr stack (linuxserver.io style):
- Started as root (the default): takes PUID/PGID (default 1000:1000, the same variables Radarr and
  Sonarr images use), gives that user the data and output folders, drops root, then runs the command.
  This fixes the classic first-run crash where Docker creates the bind-mounted folder as root.
- Started as a non-root user (`user:` in compose): leaves ownership alone.
Either way it checks the data folder is writable and, if not, prints one sentence saying how to fix it
and exits, instead of a traceback loop. Python only, so no gosu/su-exec is needed in the image.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .config import cfg, data_dir as config_data_dir, data_file, msg, output_dir as config_output_dir


def _ids() -> tuple[int, int]:
    return int(os.environ.get("PUID", "1000")), int(os.environ.get("PGID", "1000"))


def take_ownership(data_dir: Path, uid: int, gid: int) -> bool:
    """chown the data folder tree to uid:gid if its top isn't already theirs. True if it changed anything."""
    data_dir.mkdir(parents=True, exist_ok=True)
    st = data_dir.stat()
    if (st.st_uid, st.st_gid) == (uid, gid):
        return False
    for root, dirs, files in os.walk(data_dir):
        for name in [root, *(os.path.join(root, d) for d in dirs), *(os.path.join(root, f) for f in files)]:
            try:
                os.chown(name, uid, gid, follow_symlinks=False)
            except OSError:
                pass   # e.g. a read-only titles.json mounted on its own; not needed for writing
    return True


def drop_privileges(uid: int, gid: int) -> None:
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)
    os.environ["HOME"] = os.environ.get("HOME") if os.environ.get("HOME") not in (None, "/root") else "/tmp"


def main(argv: list[str]) -> None:
    from .main import check_data_dir   # imported late: keeps this module light

    data_dir = config_data_dir()
    for old in map(Path, cfg("storage.legacy_data_dirs")):
        # The image used to keep data in /data. Stop rather than start empty next to the old data.
        if old != data_dir and data_file("database", old).exists():
            print(msg("errors.legacy_data", old=old, new=data_dir), file=sys.stderr, flush=True)   # noqa: T201
            raise SystemExit(1)
    if hasattr(os, "getuid") and os.getuid() == 0:
        uid, gid = _ids()
        for folder in (data_dir, config_output_dir()):   # output (logs) too: a named volume starts as root
            if take_ownership(folder, uid, gid):
                print(msg("log.chowned", uid=uid, path=folder), file=sys.stderr, flush=True)   # noqa: T201  before logging exists
        if uid != 0:
            drop_privileges(uid, gid)
        print(msg("log.privileges_dropped", uid=os.getuid(), gid=os.getgid()), file=sys.stderr, flush=True)   # noqa: T201
    problem = check_data_dir(data_dir)
    if problem:
        print(problem, file=sys.stderr, flush=True)   # noqa: T201
        raise SystemExit(1)
    if argv:
        os.execvp(argv[0], argv)


if __name__ == "__main__":
    main(sys.argv[1:])
