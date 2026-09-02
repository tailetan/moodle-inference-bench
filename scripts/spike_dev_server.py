#!/usr/bin/env python3
"""Spike 2: what concurrency can the Moodle dev server actually sustain?

``moodle-serve.sh`` starts PHP's built-in server with ``PHP_CLI_SERVER_WORKERS=8``
while the Arm A concurrency ladder goes to 50. If requests queue in the web
server before Moodle's AI subsystem sees them, that queueing lands inside T1 but
not inside T2, and would be reported as Moodle's overhead when it is really
dev-server saturation. That would corrupt the study's headline finding while
looking exactly like the result the methodology predicts.

So the ceiling has to be measured, not read off a configuration value.

Method: serve a PHP script that does nothing but sleep for a known duration, and
drive it with the same open-loop harness the benchmark uses. The sleep is the
only work, so any latency above it is queueing. Walk the ladder, and repeat at a
raised worker count to see whether raising it is a sufficient fix.

The harness's own saturation checks stay meaningful here and are worth reading:
dispatch lag measures the *harness*, which in an open-loop design is independent
of how slow the server is. Dispatch lag staying low while latency explodes is
what tells us the ceiling is the server's, not ours.

Run::

    .venv/bin/python scripts/spike_dev_server.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "bench"))

from harness import RunConfig, percentile, run_load, summarise  # noqa: E402

LADDER = [1, 2, 5, 10, 20, 50]
SLEEP_MS = 410.0


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_server(url, timeout_s=25.0):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        try:
            request = urllib.request.Request(
                url + "/v1/chat/completions",
                data=b'{"messages":[]}',
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                response.read()
                return True
        except Exception as exc:
            last = exc
            time.sleep(0.3)
    raise SystemExit("PHP dev server never became ready: %s" % last)


def run_level(url, concurrency, duration, timeout_s):
    cfg = RunConfig(
        url=url,
        arm="spike",
        config_id="devserver-c%d" % concurrency,
        run_id="devserver-c%d" % concurrency,
        model="php-fixed-latency",
        runtime="php-cli-server",
        workload="synthetic",
        concurrency_target=concurrency,
        rate=concurrency / (SLEEP_MS / 1000.0),
        duration_s=duration,
        warmup_requests=3,
        seed=1,
        # Non-streaming: the PHP script returns a whole body, as Moodle's
        # providers do.
        stream=False,
        timeout_s=timeout_s,
    )
    return asyncio.run(run_load(cfg))


def summarise_level(result):
    ok = result.ok_rows
    latencies = [r["t2_model_ms"] for r in ok]
    return {
        "concurrency": result.config.concurrency_target,
        "target_rate": result.config.rate,
        "requests": len(result.rows),
        "ok": len(ok),
        "error_rate": result.error_rate,
        "timeouts": sum(1 for r in result.rows if r["status"] == "timeout"),
        "latency": summarise(latencies),
        "excess_p50": (percentile(latencies, 50) - SLEEP_MS) if latencies else None,
        "excess_p95": (percentile(latencies, 95) - SLEEP_MS) if latencies else None,
        "dispatch_lag_p99": percentile(result.dispatch_lag_ms, 99),
        "harness_saturated": result.saturated,
    }


def fmt(value, width=10, places=1):
    if value is None:
        return "n/a".rjust(width)
    return ("%.*f" % (places, value)).rjust(width)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workers", default="8,64",
                        help="comma-separated PHP_CLI_SERVER_WORKERS values")
    parser.add_argument("--ladder", default=",".join(str(c) for c in LADDER))
    parser.add_argument("--duration", type=float, default=10.0,
                        help="arrival window per level; kept short because a "
                             "saturated open-loop run builds a backlog")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--php", default="")
    args = parser.parse_args(argv)

    php = args.php or shutil.which("php")
    if not php:
        raise SystemExit(
            "php not found on PATH. This spike measures the Moodle dev server, "
            "so run it in the distribution where Moodle lives, or pass --php.")

    router = os.path.join(REPO_ROOT, "scripts", "spike_dev_server", "router.php")
    docroot = os.path.dirname(router)
    ladder = [int(c) for c in args.ladder.split(",") if c.strip()]
    worker_counts = [int(w) for w in args.workers.split(",") if w.strip()]

    print("PHP:            %s" % php)
    print("backend sleep:  %.0f ms (the only work the endpoint does)" % SLEEP_MS)
    print("ladder:         %s" % ladder)
    print("worker counts:  %s" % worker_counts)
    print()

    report = {"sleep_ms": SLEEP_MS, "runs": []}

    for workers in worker_counts:
        port = free_port()
        url = "http://127.0.0.1:%d" % port
        env = dict(os.environ)
        env["PHP_CLI_SERVER_WORKERS"] = str(workers)
        env["SPIKE_SLEEP_MS"] = str(int(SLEEP_MS))

        server = subprocess.Popen(
            [php, "-S", "127.0.0.1:%d" % port, "-t", docroot, router],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        levels = []
        try:
            wait_for_server(url)
            # Theoretical ceiling: each worker is blocked for the whole sleep,
            # so sustainable throughput is workers / sleep_seconds, and the
            # sustainable concurrency is just the worker count.
            capacity = workers / (SLEEP_MS / 1000.0)
            print("PHP_CLI_SERVER_WORKERS=%d  ->  theoretical ceiling %.1f req/s "
                  "(concurrency %d)" % (workers, capacity, workers))
            for concurrency in ladder:
                print("  c=%-3d rate %7.2f/s ... " % (
                    concurrency, concurrency / (SLEEP_MS / 1000.0)),
                    end="", flush=True)
                started = time.time()
                result = run_level(url, concurrency, args.duration, args.timeout)
                level = summarise_level(result)
                levels.append(level)
                print("p95 %8.1f ms, errors %5.1f%%  (%.0fs)" % (
                    level["latency"].get("p95") or 0.0,
                    level["error_rate"] * 100.0,
                    time.time() - started))
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()

        report["runs"].append({"workers": workers, "levels": levels})
        print()

    print_report(report)

    out = os.path.join(REPO_ROOT, "results", "raw", "spike_dev_server.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)
    print("\nreport written to %s" % out)
    return 0


def print_report(report):
    print("=" * 100)
    print("Latency above the %.0f ms backend sleep is queueing in the web "
          "server." % report["sleep_ms"])
    print("=" * 100)
    for run in report["runs"]:
        print()
        print("PHP_CLI_SERVER_WORKERS=%d" % run["workers"])
        header = ("  conc   target/s   ok   err%   timeout   p50 excess   "
                  "p95 excess   disp p99   harness sat")
        print(header)
        print("  " + "-" * (len(header) - 2))
        for level in run["levels"]:
            print("  %4d %10.2f %4d %6.1f %9d %12s %12s %10s %13s" % (
                level["concurrency"],
                level["target_rate"],
                level["ok"],
                level["error_rate"] * 100.0,
                level["timeouts"],
                fmt(level["excess_p50"], 12),
                fmt(level["excess_p95"], 12),
                fmt(level["dispatch_lag_p99"], 10, 2),
                "YES" if level["harness_saturated"] else "no",
            ))
    print()
    print("'disp p99' is the harness's own dispatch lag. It stays low even when "
          "latency explodes,")
    print("because open-loop dispatch does not wait for responses. That is what "
          "shows the ceiling")
    print("belongs to the server rather than to the instrument.")


if __name__ == "__main__":
    sys.exit(main())
