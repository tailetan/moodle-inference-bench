#!/usr/bin/env bash
# Copy plugin/edgellm into the Moodle checkout.
#
# The original build plan bind-mounted this with Docker. There is no Docker
# here, and a symlink does not work: PHP resolves __DIR__ through symlinks, so
# the plugin's entry points would look for config.php relative to this
# repository rather than to Moodle, and every one of them would fail.
#
# Copying keeps the plugin behaving exactly like a normally installed plugin,
# with no path handling of its own. This repository stays the source of truth,
# so re-run this after editing the plugin.
set -euo pipefail

MOODLE_ROOT="${MOODLE_ROOT:-$HOME/workspace/moodle}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$REPO_ROOT/plugin/edgellm"

if [ ! -d "$MOODLE_ROOT" ]; then
  echo "MOODLE_ROOT does not exist: $MOODLE_ROOT" >&2
  exit 1
fi

# Moodle 5.x serves from public/.
if [ -d "$MOODLE_ROOT/public" ]; then
  TARGET="$MOODLE_ROOT/public/ai/provider/edgellm"
else
  TARGET="$MOODLE_ROOT/ai/provider/edgellm"
fi

case "${1:-sync}" in
  sync)
    # A stale symlink from an earlier approach would otherwise be copied into.
    if [ -L "$TARGET" ]; then
      rm -f "$TARGET"
    fi
    mkdir -p "$TARGET"
    if command -v rsync > /dev/null 2>&1; then
      rsync -a --delete "$SOURCE/" "$TARGET/"
    else
      rm -rf "${TARGET:?}"/*
      cp -r "$SOURCE/." "$TARGET/"
    fi
    echo "synced $SOURCE -> $TARGET"
    ;;
  remove)
    if [ -L "$TARGET" ]; then
      rm -f "$TARGET"
      echo "removed symlink $TARGET"
    elif [ -d "$TARGET" ]; then
      rm -rf "$TARGET"
      echo "removed $TARGET"
    else
      echo "nothing at $TARGET"
    fi
    ;;
  *)
    echo "usage: $0 [sync|remove]" >&2
    exit 1
    ;;
esac
