#!/usr/bin/env python3
"""Self-checks for the analysis code.

The analysis is what turns raw rows into claims, so the places where it could
quietly produce a wrong number are worth pinning down. In particular the
repeat-variance flag has never fired on real data -- every configuration
recorded so far has a single repeat -- and an alarm that has never gone off is
an alarm nobody has tested.

The rows here are constructed, not measured. They are inputs to a test, and no
number in this file is a benchmark result or belongs in any write-up.

Plain asserts and no test framework, so this runs with nothing installed::

    .venv/bin/python bench/test_analyse.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from analyse import (  # noqa: E402
    REPEAT_VARIANCE_THRESHOLD_PCT, analyse, series_key, summarise_run, to_float,
)
from harness import percentile  # noqa: E402


def make_row(config_id, run_id, t1, t2, ttft="", tps="", status="ok",
             concurrency=1, error_type=""):
    return {
        "run_id": run_id,
        "timestamp": "2026-09-02T00:00:00.000Z",
        "arm": "A",
        "config_id": config_id,
        "model": "mock-deterministic",
        "quant": "",
        "runtime": "mock",
        "threads": "",
        "workload": "synthetic",
        "input_bucket": "",
        "prompt_id": "test",
        "concurrency_target": str(concurrency),
        "arrival_offset_ms": "1.0",
        "t1_total_ms": str(t1),
        "t2_model_ms": str(t2),
        "ttft_ms": str(ttft),
        "output_tokens": "32",
        "input_tokens": "22",
        "tokens_per_sec": str(tps),
        "status": status,
        "error_type": error_type,
        "_source": "constructed",
    }


def check(name, condition, detail=""):
    if condition:
        print("  ok   %s" % name)
        return 0
    print("  FAIL %s %s" % (name, detail))
    return 1


def test_percentiles():
    failures = 0
    values = list(range(1, 101))
    failures += check("p50 of 1..100", abs(percentile(values, 50) - 50.5) < 1e-9)
    failures += check("p99 of 1..100", abs(percentile(values, 99) - 99.01) < 1e-9)
    failures += check("p100 is the max", percentile(values, 100) == 100)
    failures += check("single value", percentile([7], 95) == 7)
    failures += check("empty is None", percentile([], 50) is None)
    return failures


def test_blank_is_not_zero():
    """A blank cell means not observable. Treating it as zero would invent data
    and drag every percentile downwards."""
    failures = 0
    failures += check("blank parses as None", to_float("") is None)
    failures += check("None parses as None", to_float(None) is None)
    failures += check("garbage parses as None", to_float("n/a") is None)

    rows = [
        make_row("x", "r1", 100, 90, ttft="", tps=""),
        make_row("x", "r1", 200, 190, ttft="", tps=""),
    ]
    summary = summarise_run(rows)
    failures += check("all-blank ttft yields no stats", summary["ttft_ms"] is None)
    failures += check("all-blank tokens/sec yields None",
                      summary["tokens_per_sec_median"] is None)
    return failures


def test_overhead_is_per_request():
    """t1 - t2 must be computed per request and then summarised.

    Summarising first and subtracting would be wrong: the p95 of a difference is
    not the difference of two p95s. These rows are built so the two disagree.
    """
    failures = 0
    rows = [
        make_row("x", "r1", 100, 90),   # overhead 10
        make_row("x", "r1", 500, 100),  # overhead 400
        make_row("x", "r1", 110, 100),  # overhead 10
        make_row("x", "r1", 120, 20),   # overhead 100
    ]
    summary = summarise_run(rows)

    correct = percentile([10, 400, 10, 100], 95)
    wrong = summary["e2e_ms"]["p95"] - summary["backend_ms"]["p95"]

    failures += check("overhead p95 is the p95 of the differences",
                      abs(summary["overhead_ms"]["p95"] - correct) < 1e-9,
                      "got %s expected %s" % (summary["overhead_ms"]["p95"], correct))
    failures += check("and that differs from subtracting two p95s",
                      abs(correct - wrong) > 1e-9,
                      "the test data no longer distinguishes the two methods")
    return failures


def test_errors_excluded_from_latency():
    """Failed requests must not contribute latency, but must count towards the
    error rate."""
    failures = 0
    rows = [
        make_row("x", "r1", 100, 90),
        make_row("x", "r1", 100, 90),
        make_row("x", "r1", 5, 5, status="error", error_type="http_500"),
    ]
    summary = summarise_run(rows)
    failures += check("error rate counts every row",
                      abs(summary["error_rate"] - 1 / 3) < 1e-9)
    failures += check("latency stats exclude failures",
                      summary["e2e_ms"]["n"] == 2)
    failures += check("error types are counted",
                      summary["error_types"] == {"http_500": 1})
    return failures


def test_repeat_variance_flag():
    """The 10% consistency check. This is the alarm that has never fired on real
    data, so it is tested in both directions."""
    failures = 0

    # Three repeats agreeing closely: no flag.
    consistent = []
    for index, run in enumerate(["r1", "r2", "r3"]):
        base = 100 + index  # 100, 101, 102 -> about 2% spread
        for _ in range(20):
            consistent.append(make_row("cfg-c1", run, base, base - 10))
    configs = analyse(consistent)
    failures += check("consistent repeats are not flagged",
                      configs[0]["repeat_flags"] == [],
                      str(configs[0]["repeat_flags"]))
    failures += check("three repeats are recognised", configs[0]["repeats"] == 3)

    # Three repeats disagreeing badly: flagged, not averaged.
    noisy = []
    for base, run in ((100, "r1"), (150, "r2"), (200, "r3")):
        for _ in range(20):
            noisy.append(make_row("cfg-c1", run, base, base - 10))
    configs = analyse(noisy)
    failures += check("divergent repeats are flagged",
                      len(configs[0]["repeat_flags"]) > 0)
    comparison = configs[0]["repeat_comparison"]["e2e_p50_ms"]
    failures += check("spread is reported as a percentage",
                      abs(comparison["spread_pct"] - 100.0 / 150.0 * 100.0) < 1e-6,
                      str(comparison["spread_pct"]))
    failures += check("threshold is the methodology 10 percent",
                      REPEAT_VARIANCE_THRESHOLD_PCT == 10.0)

    # A single repeat cannot be checked, and must not be silently passed.
    single = [make_row("cfg-c1", "r1", 100, 90) for _ in range(10)]
    configs = analyse(single)
    failures += check("a single repeat produces no comparison",
                      configs[0]["repeat_comparison"] == {})
    failures += check("and is not flagged as consistent either",
                      configs[0]["repeat_flags"] == [])
    return failures


def test_series_grouping():
    """Configurations that differ in more than concurrency must not be drawn as
    one line."""
    failures = 0
    failures += check("trailing -cN is stripped",
                      series_key("validate-stream-c50") == "validate-stream")
    failures += check("trailing _cN is stripped",
                      series_key("arma_slow_c20") == "arma_slow")
    failures += check("stream and json are different families",
                      series_key("validate-stream-c1") != series_key("validate-json-c1"))
    failures += check("an unconventional id is its own family",
                      series_key("something-else") == "something-else")

    rows = []
    for config_id, concurrency in (("a-c1", 1), ("a-c10", 10), ("b-c1", 1)):
        rows.append(make_row(config_id, config_id, 100, 90, concurrency=concurrency))
    configs = analyse(rows)
    families = sorted({c["series"] for c in configs})
    failures += check("two families detected", families == ["a", "b"], str(families))

    # Ladder order, not alphabetical: c10 must not sort before c2.
    rows = []
    for concurrency in (1, 2, 10, 20):
        cid = "x-c%d" % concurrency
        rows.append(make_row(cid, cid, 100, 90, concurrency=concurrency))
    configs = analyse(rows)
    order = [c["concurrency_target"] for c in configs]
    failures += check("configurations sort in ladder order",
                      order == [1, 2, 10, 20], str(order))
    return failures


def main():
    tests = [
        ("percentiles", test_percentiles),
        ("blank cells are not zero", test_blank_is_not_zero),
        ("overhead is per request", test_overhead_is_per_request),
        ("errors excluded from latency", test_errors_excluded_from_latency),
        ("repeat variance flag", test_repeat_variance_flag),
        ("series grouping", test_series_grouping),
    ]

    failures = 0
    for name, fn in tests:
        print(name)
        failures += fn()
        print()

    if failures:
        print("FAILED: %d check(s)" % failures)
        return 1
    print("All analysis self-checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
