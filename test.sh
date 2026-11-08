#!/usr/bin/env bash
# RUN ALL TESTS: the gateway (pytest, also checks the web app is served and the API console lists every
# endpoint) and the web app's filter logic (node --test web/tests). The iOS app has no test target yet.
set -euo pipefail
cd "$(dirname "$0")"
source scripts/lib.sh

need_python
python=$(bash scripts/python-env.sh)
say "Gateway tests"
(cd backend && "$python" -m pytest -q)

need node "Install Node.js 18 or later (only used to run the web tests, not to build anything)."
say "Web tests"
node --test web/tests/*.test.mjs
