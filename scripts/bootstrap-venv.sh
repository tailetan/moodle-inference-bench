#!/usr/bin/env bash
# Create the benchmark virtualenv and install pinned dependencies.
#
# Normally this is just `python3 -m venv .venv && .venv/bin/pip install -r
# bench/requirements.txt`. On Debian/Ubuntu images that ship python3 without the
# python3-venv package there is no ensurepip, so we build the venv without pip
# and bootstrap pip from get-pip.py. That path needs no root.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${VENV_DIR:-$REPO_ROOT/.venv}"

if [ ! -x "$VENV/bin/python" ]; then
  if python3 -m venv "$VENV" 2>/dev/null; then
    echo "venv created with ensurepip"
  else
    echo "ensurepip unavailable; bootstrapping pip from get-pip.py"
    rm -rf "$VENV"
    python3 -m venv --without-pip "$VENV"
    curl -fsSL -o /tmp/get-pip.py https://bootstrap.pypa.io/pip/get-pip.py
    "$VENV/bin/python" /tmp/get-pip.py --quiet
  fi
fi

"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$REPO_ROOT/bench/requirements.txt"
"$VENV/bin/python" -m pip --version
"$VENV/bin/python" -c "import aiohttp; print('aiohttp', aiohttp.__version__)"
