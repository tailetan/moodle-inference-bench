#!/usr/bin/env bash
# Start or stop Moodle's dev server with the worker count the benchmark needs.
#
# This exists rather than reusing the checkout's own moodle-serve.sh because the
# worker count is a measured variable here, not a convenience: PHP's built-in
# server blocks a whole worker for the duration of each request, so sustainable
# concurrency equals PHP_CLI_SERVER_WORKERS. At the usual default of 8, the Arm A
# ladder saturates the web server long before it reaches 50, and that queueing
# would be misreported as Moodle's own overhead. See docs/environment.md.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$REPO_ROOT/.env" ] && set -a && . "$REPO_ROOT/.env" && set +a

MOODLE_ROOT="${MOODLE_ROOT:-$HOME/workspace/moodle}"
MOODLE_PORT="${MOODLE_PORT:-8080}"
WORKERS="${PHP_CLI_SERVER_WORKERS:-64}"
PIDFILE="$REPO_ROOT/.moodle-bench-server.pid"
LOGFILE="$REPO_ROOT/.moodle-bench-server.log"

DOCROOT="$MOODLE_ROOT"
[ -d "$MOODLE_ROOT/public" ] && DOCROOT="$MOODLE_ROOT/public"

case "${1:-start}" in
  start)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "already running (pid $(cat "$PIDFILE")) on port $MOODLE_PORT"
      exit 0
    fi
    cd "$MOODLE_ROOT"
    : > "$LOGFILE"
    PHP_CLI_SERVER_WORKERS="$WORKERS" \
      nohup php -S "0.0.0.0:$MOODLE_PORT" -t "$DOCROOT" > "$LOGFILE" 2>&1 &
    PID=$!
    echo "$PID" > "$PIDFILE"

    # Checking that something answers the port is not enough: another server
    # may already own it, in which case ours died on bind and the benchmark
    # would silently run against the wrong worker count. Verify our own
    # process is the one that came up.
    for _ in $(seq 1 60); do
      if ! kill -0 "$PID" 2>/dev/null; then break; fi
      if curl -s -o /dev/null -m 2 "http://localhost:$MOODLE_PORT/"; then break; fi
      sleep 0.5
    done

    if ! kill -0 "$PID" 2>/dev/null || grep -q "Failed to listen" "$LOGFILE"; then
      rm -f "$PIDFILE"
      echo "FAILED to start on port $MOODLE_PORT." >&2
      grep -m1 "Failed to listen" "$LOGFILE" >&2 || true
      echo "Something else is already listening:" >&2
      ss -ltnp 2>/dev/null | grep ":$MOODLE_PORT" >&2 || true
      echo "" >&2
      echo "Stop it first. If it is the checkout's own dev server:" >&2
      echo "  ~/workspace/moodle-serve.sh stop" >&2
      exit 1
    fi

    echo "Moodle dev server started (pid $PID), workers=$WORKERS"
    echo "  http://localhost:$MOODLE_PORT"
    echo "  log: $LOGFILE"
    ;;
  stop)
    if [ -f "$PIDFILE" ]; then
      kill "$(cat "$PIDFILE")" 2>/dev/null || true
      rm -f "$PIDFILE"
      echo "stopped"
    else
      echo "not running"
    fi
    ;;
  status)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "running (pid $(cat "$PIDFILE")) on port $MOODLE_PORT, workers=$WORKERS"
    else
      echo "not running"
    fi
    ;;
  *)
    echo "usage: $0 [start|stop|status]" >&2
    exit 1
    ;;
esac
