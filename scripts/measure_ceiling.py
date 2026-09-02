#!/usr/bin/env python3
"""Measure the concurrency ceiling with real Moodle in the request path.

Spike 2 measured PHP's built-in server against a script that only slept. That
established the shape of the problem but not the number that matters: real
Moodle does session handling, database queries, policy checks and logging on
every request, so its ceiling is lower, possibly much lower.

This drives Moodle's own benchmark endpoint through the same open-loop harness
the study uses, walks the Arm A concurrency ladder, and reports where the
system stops keeping up.

Reading the output. Two quantities move for different reasons:

* **t1**, measured inside Moodle around the core AI manager call, does not
  include web-server queueing. A request that waited in the web server's accept
  queue still shows a small t1.
* **the harness's wall clock**, measured out here, does include that queueing.

So the ceiling shows up as the gap between them opening. If wall time climbs
while t1 stays flat, requests are queueing in front of Moodle, and any Arm A run
at that concurrency would be measuring the web server rather than the AI
subsystem.

Prerequisites, all of which this script checks:

    cp .env.example .env
    make sync-plugin
    make serve
    make bench-setup       # prints the token; put it in .env as BENCH_TOKEN
    .venv/bin/python bench/mock_server.py --profile mid --port 8090 &

Run::

    .venv/bin/python scripts/measure_ceiling.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "bench"))

from harness import RunConfig, percentile, run_load, summarise  # noqa: E402

LADDER = [1, 2, 5, 10, 20, 50]

# How far the harness's wall clock may exceed t1 before the web server is
# judged to be queueing rather than serving. Fixed before the run.
QUEUEING_FACTOR = 1.5


def load_env():
    """Read .env into the environment, without overriding what is already set."""
    path = os.path.join(REPO_ROOT, ".env")
    if not os.path.isfile(path):
        raise SystemExit(
            "no .env found. Run 'make env' and edit it, or copy .env.example.")
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            # Values may be quoted so that a shell sourcing the same file keeps
            # them intact. Strip the quotes here for the same reason.
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key.strip(), value)


def preflight(bench_url, token, mock_url):
    """Fail early and specifically rather than producing a run of errors."""
    problems = []

    try:
        with urllib.request.urlopen(mock_url + "/health", timeout=5) as response:
            json.loads(response.read().decode())
    except Exception as exc:
        problems.append("mock server not answering at %s (%s). Start it with: "
                        ".venv/bin/python bench/mock_server.py --profile mid "
                        "--port %s" % (mock_url, exc, mock_url.rsplit(":", 1)[-1]))

    request = urllib.request.Request(
        bench_url,
        data=json.dumps({"action": "summarise_text",
                         "prompttext": "preflight"}).encode(),
        headers={"Content-Type": "application/json", "X-Bench-Token": token},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode())
        if not body.get("success"):
            problems.append("bench endpoint reachable but the action failed: %s"
                            % body.get("errormessage") or body.get("error"))
        elif body.get("t2_model_ms") is None:
            problems.append(
                "bench endpoint returned no t2. Instrumentation is off, so the "
                "provider in use is probably core's rather than edgellm's, or "
                "'make bench-setup' has not been run.")
        return body
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:200]
        problems.append("bench endpoint returned HTTP %d: %s. Run "
                        "'make bench-setup' and put the printed token in .env "
                        "as BENCH_TOKEN." % (exc.code, detail))
    except Exception as exc:
        problems.append("bench endpoint not reachable at %s (%s). Run "
                        "'make serve'." % (bench_url, exc))
    finally:
        if problems:
            for problem in problems:
                print("PREFLIGHT FAILED: %s" % problem, file=sys.stderr)
            raise SystemExit(1)
    return None


def run_level(bench_url, token, action, concurrency, expected_ms, duration,
              timeout_s):
    cfg = RunConfig(
        url=bench_url,
        arm="ceiling",
        config_id="moodle-ceiling-c%d" % concurrency,
        run_id="moodle-ceiling-c%d" % concurrency,
        model="mock-deterministic",
        runtime="moodle+mock",
        workload="synthetic",
        concurrency_target=concurrency,
        rate=concurrency / (expected_ms / 1000.0),
        duration_s=duration,
        warmup_requests=3,
        seed=1,
        target="moodle",
        bench_token=token,
        action=action,
        timeout_s=timeout_s,
    )
    return asyncio.run(run_load(cfg))


def level_summary(result):
    ok = result.ok_rows
    t1 = [r["t1_total_ms"] for r in ok if r["t1_total_ms"] != ""]
    t2 = [r["t2_model_ms"] for r in ok if r["t2_model_ms"] != ""]
    overhead = [r["t1_total_ms"] - r["t2_model_ms"] for r in ok
                if r["t1_total_ms"] != "" and r["t2_model_ms"] != ""]
    return {
        "concurrency": result.config.concurrency_target,
        "target_rate": result.config.rate,
        "requests": len(result.rows),
        "ok": len(ok),
        "error_rate": result.error_rate,
        "t1_ms": summarise(t1),
        "t2_ms": summarise(t2),
        "overhead_ms": summarise(overhead),
        "harness_wall_ms": summarise(result.harness_wall_ms),
        "dispatch_lag_ms": summarise(result.dispatch_lag_ms),
        "harness_saturated": result.saturated,
        "saturation_reasons": result.saturation_reasons,
    }


def fmt(block, key, width=9, places=1):
    if not block or block.get(key) is None:
        return "n/a".rjust(width)
    return ("%.*f" % (places, block[key])).rjust(width)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ladder", default=",".join(str(c) for c in LADDER))
    parser.add_argument("--duration", type=float, default=20.0)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--action", default="summarise_text")
    parser.add_argument("--out", default="results/raw/moodle_ceiling.json")
    args = parser.parse_args(argv)

    load_env()
    wwwroot = os.environ.get("MOODLE_WWWROOT", "http://localhost:8080")
    token = os.environ.get("BENCH_TOKEN", "")
    mock_port = os.environ.get("MOCK_PORT", "8090")
    mock_url = "http://localhost:%s" % mock_port
    bench_url = wwwroot.rstrip("/") + "/ai/provider/edgellm/bench.php"

    if not token:
        raise SystemExit(
            "BENCH_TOKEN is empty in .env. Run 'make bench-setup' and copy the "
            "token it prints.")

    print("bench endpoint: %s" % bench_url)
    print("mock:           %s" % mock_url)
    print()

    probe = preflight(bench_url, token, mock_url)
    print("preflight ok: t1=%.1f ms, t2=%.1f ms on a single request"
          % (probe["t1_total_ms"], probe["t2_model_ms"]))

    # Calibrate the expected latency rather than assuming it, so Little's law
    # produces the arrival rate that actually holds the target concurrency.
    print("calibrating with 5 sequential requests ... ", end="", flush=True)
    samples = []
    for _ in range(5):
        body = preflight(bench_url, token, mock_url)
        samples.append(body["t1_total_ms"])
    expected_ms = percentile(samples, 50)
    print("median t1 %.1f ms" % expected_ms)
    print()

    ladder = [int(c) for c in args.ladder.split(",") if c.strip()]
    levels = []
    for concurrency in ladder:
        rate = concurrency / (expected_ms / 1000.0)
        print("  c=%-3d rate %7.2f/s ... " % (concurrency, rate), end="", flush=True)
        started = time.time()
        result = run_level(bench_url, token, args.action, concurrency,
                           expected_ms, args.duration, args.timeout)
        level = level_summary(result)
        levels.append(level)
        print("t1 p95 %8.1f ms, wall p95 %8.1f ms, errors %5.1f%%  (%.0fs)"
              % (level["t1_ms"]["p95"] if level["t1_ms"] else float("nan"),
                 level["harness_wall_ms"]["p95"] or 0.0,
                 level["error_rate"] * 100.0,
                 time.time() - started))

    print()
    header = ("  conc   rate/s   ok   err%    t1 p50    t1 p95    t2 p50   "
              "ovh p50   ovh p95  wall p50  wall p95   queueing")
    print(header)
    print("  " + "-" * (len(header) - 2))
    ceiling = None
    for level in levels:
        wall50 = (level["harness_wall_ms"] or {}).get("p50")
        t150 = (level["t1_ms"] or {}).get("p50")
        queueing = ""
        if wall50 and t150 and wall50 > t150 * QUEUEING_FACTOR:
            queueing = "QUEUEING"
            if ceiling is None:
                ceiling = level["concurrency"]
        print("  %4d %8.2f %4d %6.1f %9s %9s %9s %9s %9s %9s %9s   %s" % (
            level["concurrency"],
            level["target_rate"],
            level["ok"],
            level["error_rate"] * 100.0,
            fmt(level["t1_ms"], "p50"),
            fmt(level["t1_ms"], "p95"),
            fmt(level["t2_ms"], "p50"),
            fmt(level["overhead_ms"], "p50", 9, 2),
            fmt(level["overhead_ms"], "p95", 9, 2),
            fmt(level["harness_wall_ms"], "p50"),
            fmt(level["harness_wall_ms"], "p95"),
            queueing,
        ))

    print()
    print("t1 and t2 come from inside Moodle. 'wall' is the harness's own clock "
          "and includes")
    print("web-server queueing, which t1 does not. A level is marked QUEUEING "
          "when wall p50")
    print("exceeds t1 p50 by more than %.0f%%." % ((QUEUEING_FACTOR - 1) * 100))
    print()
    if ceiling is None:
        print("No queueing detected up to concurrency %d. The web server kept "
              "up across the whole ladder." % ladder[-1])
    else:
        print("QUEUEING FIRST APPEARS AT CONCURRENCY %d." % ceiling)
        print("Arm A results at or above that level would attribute web-server")
        print("saturation to Moodle's AI subsystem. Raise "
              "PHP_CLI_SERVER_WORKERS, move to php-fpm, or cap the ladder and")
        print("revise the methodology openly.")

    report = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bench_url": bench_url,
        "php_cli_server_workers": os.environ.get("PHP_CLI_SERVER_WORKERS"),
        "calibrated_expected_ms": expected_ms,
        "queueing_factor": QUEUEING_FACTOR,
        "queueing_first_at_concurrency": ceiling,
        "duration_s_per_level": args.duration,
        "levels": levels,
    }
    out = args.out if os.path.isabs(args.out) else os.path.join(REPO_ROOT, args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)
    print()
    print("report written to %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
