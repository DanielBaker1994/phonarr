# Shared helpers for the root build scripts. Sourced, not run.
say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "$1 is not installed. $2"; }
# Python 3.12 or later (the gateway's version; the Docker image uses 3.12).
need_python() {
  need python3 "Install Python 3.12 or later."
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' \
    || die "Python 3.12 or later is needed, found $(python3 --version 2>&1). Install a newer one (e.g. brew install python@3.12, or pyenv)."
}
