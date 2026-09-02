#!/usr/bin/env python3
"""Prove the instrument before trusting it.

Phase 1 of the study builds a load harness and a mock backend. Nothing measured
later means anything unless the harness reports the mock's *configured* latency
back accurately, at every concurrency level the study will use.

This script starts the mock server as a separate process, drives the harness at
each rung of the Arm A concurrency ladder (1, 2, 5, 10, 20, 50) in both
streaming and non-streaming mode, and checks four things:

1. **Accuracy.** Backend-boundary TTFT and end-to-end latency match the mock's
   configured values, at p50 and at p95.
2. **Clock agreement.** ``t1 - t2`` matches the independently recorded dispatch
   lag. t1 and t2 come from separate clock reads at separate points; in phase 1
   the only thing between them is the harness's own scheduling delay, so these
   two numbers must agree. If they do not, one of the two measurement points is
   wrong, and ``t1 - t2`` is the study's headline finding.
3. **No harness saturation.** Dispatch lag and event-loop lag stay small, and
   the achieved arrival rate matches the target. A harness that saturates before
   the system does reports its own ceiling as the system's.
4. **No errors.**

Failures are printed as failures. Tolerances are fixed here, before the run, and
are not to be relaxed to make a run pass -- if a level fails, the instrument is
not ready for that level and the write-up says so.

Run::

    .venv/bin/python bench/validate_harness.py --duration 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness import (  # noqa: E402
    RunConfig, build_meta, percentile, run_load, summarise, write_csv,
)

LADDER = [1, 2, 5, 10, 20, 50]

# Tolerances, fixed before the run.
#
# "Within a few milliseconds" at the median: 5 ms. The tail is allowed more
# because a userspace timer on a 4-core laptop under load will occasionally be
# late, and pretending otherwise would mean tuning the tolerance to the result.
TOL_P50_MS = 5.0
TOL_P95_MS = 15.0
# t1 - t2 versus the separately recorded dispatch lag. These measure the same
# physical interval by two routes, so they should agree very tightly.
TOL_CLOCK_AGREEMENT_MS = 2.0


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_health(url, timeout_s=20.0):
    deadline = time.time() + timeout_s
    last_error = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/health", timeout=2) as response:
                return json.loads(response.read().decode())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last_error = exc
            time.sleep(0.2)
    raise SystemExit("mock server did not become healthy: %s" % last_error)


def check(label, measured, expected, tolerance):
    """One tolerance check. Returns a dict rather than raising, so a failing
    level still produces a full row in the report."""
    if measured is None:
        return {"label": label, "measured": None, "expected": expected,
                "delta": None, "tolerance": tolerance, "pass": False}
    delta = measured - expected
    return {
        "label": label,
        "measured": measured,
        "expected": expected,
        "delta": delta,
        "tolerance": tolerance,
        "pass": abs(delta) <= tolerance,
    }


def evaluate(result, configured, stream):
    """Turn one harness run into a pass/fail verdict with its evidence."""
    ok_rows = result.ok_rows
    checks = []

    # Backend-boundary TTFT: measured TTFT is referenced to the scheduled
    # arrival, so removing the separately recorded dispatch lag leaves the
    # interval the mock actually controls. This is a cross-check between two
    # independently recorded columns, not a derivation of one from the other.
    backend_ttft = [r["ttft_ms"] - r["arrival_offset_ms"]
                    for r in ok_rows if r["ttft_ms"] != ""]
    t2_values = [r["t2_model_ms"] for r in ok_rows]
    clock_gap = [(r["t1_total_ms"] - r["t2_model_ms"]) - r["arrival_offset_ms"]
                 for r in ok_rows]

    # TTFT is only observable on a streaming response, so it is only checked
    # there. A non-streaming run leaves the column empty rather than restating
    # end-to-end latency, and checking a column we deliberately did not fill
    # would test nothing.
    if stream:
        checks.append(check("ttft p50", percentile(backend_ttft, 50),
                            configured["ttft_ms"], TOL_P50_MS))
        checks.append(check("ttft p95", percentile(backend_ttft, 95),
                            configured["ttft_ms"], TOL_P95_MS))
    checks.append(check("total p50", percentile(t2_values, 50),
                        configured["total_ms"], TOL_P50_MS))
    checks.append(check("total p95", percentile(t2_values, 95),
                        configured["total_ms"], TOL_P95_MS))
    checks.append(check("t1-t2 vs dispatch lag p95",
                        percentile([abs(v) for v in clock_gap], 95),
                        0.0, TOL_CLOCK_AGREEMENT_MS))

    errors_ok = result.error_rate == 0.0
    passed = all(c["pass"] for c in checks) and errors_ok and not result.saturated

    return {
        "concurrency_target": result.config.concurrency_target,
        "mode": "stream" if stream else "json",
        "requests": len(result.rows),
        "requests_ok": len(ok_rows),
        "error_rate": result.error_rate,
        "target_rate_per_s": result.config.rate,
        "achieved_rate_per_s": result.achieved_rate,
        "dispatch_lag_ms": summarise(result.dispatch_lag_ms),
        "event_loop_lag_ms": summarise(result.loop_lag_ms),
        "backend_ttft_ms": summarise(backend_ttft),
        "t2_model_ms": summarise(t2_values),
        "t1_total_ms": summarise([r["t1_total_ms"] for r in ok_rows]),
        "mock_actual_ttft_ms": summarise(result.mock_actual_ttft_ms),
        "mock_actual_total_ms": summarise(result.mock_actual_total_ms),
        "checks": checks,
        "saturated": result.saturated,
        "saturation_reasons": result.saturation_reasons,
        "pass": passed,
    }


def fmt(value, width=8, places=2):
    if value is None:
        return "n/a".rjust(width)
    return ("%.*f" % (places, value)).rjust(width)


def print_report(configured, levels):
    print()
    print("Mock configured latency: ttft=%.1f ms, inter-token=%.1f ms, "
          "tokens=%d, total=%.1f ms"
          % (configured["ttft_ms"], configured["inter_token_ms"],
             configured["tokens"], configured["total_ms"]))
    print("Tolerances: p50 +/- %.1f ms, p95 +/- %.1f ms, clock agreement "
          "+/- %.1f ms" % (TOL_P50_MS, TOL_P95_MS, TOL_CLOCK_AGREEMENT_MS))
    print()

    header = ("conc mode    n    err%   rate tgt  disp p99  loop p99  mock p95  "
              "ttft p50  ttft p95  ttot p50  ttot p95  clk p95  verdict")
    print(header)
    print("-" * len(header))
    for level in levels:
        by_label = {c["label"]: c for c in level["checks"]}
        mock_p95 = level["mock_actual_total_ms"].get("p95")
        mock_drift = (None if mock_p95 is None
                      else mock_p95 - configured["total_ms"])
        print("%4d %-6s %4d %6.2f %9.2f %9.2f %9.2f %9s %9s %9s %9s %9s %8s  %s"
              % (
                  level["concurrency_target"],
                  level["mode"],
                  level["requests"],
                  level["error_rate"] * 100.0,
                  level["target_rate_per_s"],
                  level["dispatch_lag_ms"].get("p99") or 0.0,
                  level["event_loop_lag_ms"].get("p99") or 0.0,
                  fmt(mock_drift, 9),
                  fmt(by_label["ttft p50"]["delta"] if "ttft p50" in by_label
                      else None, 9),
                  fmt(by_label["ttft p95"]["delta"] if "ttft p95" in by_label
                      else None, 9),
                  fmt(by_label["total p50"]["delta"], 9),
                  fmt(by_label["total p95"]["delta"], 9),
                  fmt(by_label["t1-t2 vs dispatch lag p95"]["measured"], 8),
                  "PASS" if level["pass"] else "FAIL",
              ))
    print()
    print("All delta columns are milliseconds, measured minus configured.")
    print("  mock p95  how far the mock's own server-side timing drifted from "
          "its configuration; this part of any ttot delta is the mock's, not "
          "the harness's.")
    print("  clk p95   p95 absolute disagreement between (t1 - t2) and the "
          "separately recorded dispatch lag. t1 and t2 come from independent "
          "clock reads, so this is how well the two measurement points agree.")
    print("  rate got is omitted: arrivals are Poisson, so the realised rate "
          "varies around the target by design. See schedule_sigma in the "
          "per-run metadata.")

    for level in levels:
        if level["pass"]:
            continue
        print()
        print("FAIL detail: concurrency=%d mode=%s"
              % (level["concurrency_target"], level["mode"]))
        for c in level["checks"]:
            if not c["pass"]:
                print("  check %-28s measured=%s expected=%.3f delta=%s "
                      "tolerance=%.3f"
                      % (c["label"],
                         "n/a" if c["measured"] is None else "%.3f" % c["measured"],
                         c["expected"],
                         "n/a" if c["delta"] is None else "%.3f" % c["delta"],
                         c["tolerance"]))
        if level["error_rate"]:
            print("  error rate %.4f" % level["error_rate"])
        for reason in level["saturation_reasons"]:
            print("  saturation: " + reason)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate the harness against the mock at every "
                    "concurrency level.")
    parser.add_argument("--duration", type=float, default=30.0,
                        help="arrival window per level, in seconds")
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--profile", default="mid",
                        help="mock latency profile to validate against")
    parser.add_argument("--ladder", default=",".join(str(c) for c in LADDER),
                        help="comma-separated concurrency levels")
    parser.add_argument("--modes", default="stream,json",
                        help="comma-separated: stream, json")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out-dir", default="results/raw/validation",
                        help="where per-level CSVs and the report are written")
    parser.add_argument("--port", type=int, default=0,
                        help="mock server port; 0 picks a free one")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    out_dir = args.out_dir if os.path.isabs(args.out_dir) else os.path.join(
        repo_root, args.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    port = args.port or free_port()
    url = "http://127.0.0.1:%d" % port

    server = subprocess.Popen(
        [sys.executable, os.path.join(here, "mock_server.py"),
         "--host", "127.0.0.1", "--port", str(port), "--profile", args.profile],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    levels = []
    try:
        health = wait_for_health(url)
        configured = health["defaults"]
        print("mock server up on %s: %s" % (url, json.dumps(configured)))

        ladder = [int(c) for c in args.ladder.split(",") if c.strip()]
        modes = [m.strip() for m in args.modes.split(",") if m.strip()]

        for mode in modes:
            stream = mode == "stream"
            for concurrency in ladder:
                rate = concurrency / (configured["total_ms"] / 1000.0)
                config_id = "validate-%s-c%d" % (mode, concurrency)
                cfg = RunConfig(
                    url=url,
                    arm="A",
                    config_id=config_id,
                    run_id=config_id,
                    workload="synthetic",
                    concurrency_target=concurrency,
                    rate=rate,
                    duration_s=args.duration,
                    warmup_requests=args.warmup,
                    seed=args.seed,
                    stream=stream,
                    max_tokens=int(configured["tokens"]),
                    timeout_s=60.0,
                )
                print("running %-22s target rate %8.3f req/s ... "
                      % (config_id, rate), end="", flush=True)
                started = time.time()
                result = asyncio.run(run_load(cfg))
                level = evaluate(result, configured, stream)
                levels.append(level)
                print("%s  (%d requests in %.1fs)"
                      % ("PASS" if level["pass"] else "FAIL",
                         len(result.rows), time.time() - started))

                csv_path = os.path.join(out_dir, config_id + ".csv")
                write_csv(csv_path, result.rows)
                with open(csv_path + ".meta.json", "w", encoding="utf-8") as fh:
                    json.dump(build_meta(result), fh, indent=2, default=str)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    print_report(configured, levels)

    report = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": sys.version,
        "mock_profile": args.profile,
        "mock_configured": configured,
        "duration_s_per_level": args.duration,
        "tolerances_ms": {
            "p50": TOL_P50_MS,
            "p95": TOL_P95_MS,
            "clock_agreement": TOL_CLOCK_AGREEMENT_MS,
        },
        "levels": levels,
        "all_passed": all(level["pass"] for level in levels),
    }
    report_path = os.path.join(out_dir, "validation_report.json")
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    print("\nreport written to %s" % report_path)

    if not report["all_passed"]:
        print("\nVALIDATION FAILED -- the harness is not yet trustworthy at "
              "every level above.")
        return 1
    print("\nVALIDATION PASSED at every concurrency level.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
