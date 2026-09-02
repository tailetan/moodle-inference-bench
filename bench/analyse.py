#!/usr/bin/env python3
"""Summarise raw benchmark CSVs and regenerate every chart from them.

Two rules from the methodology shape this whole file.

**Percentiles, never bare averages.** A mean latency describes no request that
actually happened. Everything here reports p50, p95 and p99. The one exception is
sustained output rate, where the methodology asks for the mean alongside the
median, so both are given and both are labelled.

**Charts are derived artefacts.** They are regenerated from the raw CSVs on every
run, never from the summary, so a reader who disputes a chart can recompute it
from the committed rows and see exactly where it came from.

A third rule shapes the repeat handling. The methodology requires three repeats
per configuration and says to flag any configuration whose repeats vary by more
than 10% rather than averaging it away. So repeats are kept separate, compared,
and the disagreement is reported as a result in its own right.

Usage::

    .venv/bin/python bench/analyse.py results/raw/validation
    .venv/bin/python bench/analyse.py results/raw/validation --out-dir results
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness import percentile  # noqa: E402

# The methodology's threshold for calling repeats inconsistent, in percent.
REPEAT_VARIANCE_THRESHOLD_PCT = 10.0

# Metrics compared across repeats. If any of these disagrees by more than the
# threshold, the configuration is flagged rather than averaged.
REPEAT_CHECK_METRICS = [
    ("e2e_p50_ms", "end-to-end p50"),
    ("e2e_p95_ms", "end-to-end p95"),
    ("overhead_p95_ms", "t1 - t2 p95"),
    ("tokens_per_sec_median", "tokens/sec median"),
]


def series_key(config_id):
    """The configuration family a config_id belongs to, ignoring concurrency.

    A chart plotted against concurrency must contain one line per family. If
    two configurations that differ in something else, say a streaming and a
    non-streaming variant, were drawn as one line, the line would zigzag
    between two unrelated systems and mean nothing.

    The convention is that a config_id ends with the concurrency level, as
    ``-c10`` or ``_c10``, and everything before that names the family. A
    config_id that does not follow it becomes a family of its own, which is
    safe: worst case a chart has more lines than necessary, never fewer.
    """
    match = re.match(r"^(.*)[-_]c\d+$", config_id)
    return match.group(1) if match else config_id


def to_float(value):
    """Parse a CSV cell as a float, treating an empty cell as absent.

    Empty is not zero. A blank ttft_ms means the value was not observable on
    that request, and averaging it in as zero would silently invent data.
    """
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def load_rows(paths):
    """Read every CSV into one list of rows, remembering the source file."""
    rows = []
    for path in paths:
        with open(path, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                row["_source"] = os.path.basename(path)
                rows.append(row)
    return rows


def stats(values):
    """Percentile summary of a list of numbers."""
    values = [v for v in values if v is not None]
    if not values:
        return None
    return {
        "n": len(values),
        "min": min(values),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": max(values),
    }


def summarise_run(rows):
    """Reduce one repeat (one run_id) to the numbers the study reports."""
    ok = [r for r in rows if r["status"] == "ok"]

    e2e = [to_float(r["t1_total_ms"]) for r in ok]
    backend = [to_float(r["t2_model_ms"]) for r in ok]
    ttft = [to_float(r["ttft_ms"]) for r in ok]
    tps = [to_float(r["tokens_per_sec"]) for r in ok]

    # The headline quantity. Computed per request from two independently
    # recorded columns, never from the difference of two summary statistics:
    # p95(t1) - p95(t2) is not the p95 of the difference.
    overhead = []
    for r in ok:
        t1 = to_float(r["t1_total_ms"])
        t2 = to_float(r["t2_model_ms"])
        if t1 is not None and t2 is not None:
            overhead.append(t1 - t2)

    errors = [r for r in rows if r["status"] != "ok"]
    error_types = defaultdict(int)
    for r in errors:
        error_types[r["error_type"] or "unknown"] += 1

    tps_clean = [v for v in tps if v is not None]

    summary = {
        "requests": len(rows),
        "requests_ok": len(ok),
        "error_rate": (len(errors) / len(rows)) if rows else 0.0,
        "error_types": dict(error_types),
        "e2e_ms": stats(e2e),
        "backend_ms": stats(backend),
        "ttft_ms": stats(ttft),
        "overhead_ms": stats(overhead),
        "tokens_per_sec_median": percentile(tps_clean, 50) if tps_clean else None,
        # The methodology asks for the mean here as well as the median. It is
        # reported alongside, never instead.
        "tokens_per_sec_mean": (sum(tps_clean) / len(tps_clean)) if tps_clean else None,
    }

    # Flatten the few values the repeat comparison needs.
    summary["e2e_p50_ms"] = summary["e2e_ms"]["p50"] if summary["e2e_ms"] else None
    summary["e2e_p95_ms"] = summary["e2e_ms"]["p95"] if summary["e2e_ms"] else None
    summary["overhead_p95_ms"] = (
        summary["overhead_ms"]["p95"] if summary["overhead_ms"] else None)
    return summary


def compare_repeats(runs):
    """Measure how far the repeats of one configuration disagree.

    Reported as the spread across repeats relative to their median. The
    methodology treats variance as a finding rather than noise to be smoothed,
    so this returns the numbers even when they are within tolerance.
    """
    comparisons = {}
    flagged = []

    for key, label in REPEAT_CHECK_METRICS:
        values = [r["summary"].get(key) for r in runs]
        values = [v for v in values if v is not None]
        if len(values) < 2:
            continue
        median = percentile(values, 50)
        if not median:
            continue
        spread_pct = (max(values) - min(values)) / median * 100.0
        comparisons[key] = {
            "label": label,
            "repeats": len(values),
            "min": min(values),
            "median": median,
            "max": max(values),
            "spread_pct": spread_pct,
        }
        if spread_pct > REPEAT_VARIANCE_THRESHOLD_PCT:
            flagged.append("%s varies by %.1f%% across %d repeats"
                           % (label, spread_pct, len(values)))

    return comparisons, flagged


def analyse(rows):
    """Group rows into configurations and repeats, and summarise each."""
    by_config = defaultdict(lambda: defaultdict(list))
    for row in rows:
        by_config[row["config_id"]][row["run_id"]].append(row)

    configs = []
    for config_id in sorted(by_config):
        runs = []
        for run_id in sorted(by_config[config_id]):
            runrows = by_config[config_id][run_id]
            runs.append({
                "run_id": run_id,
                "source": runrows[0]["_source"],
                "summary": summarise_run(runrows),
            })

        allrows = [r for runrows in by_config[config_id].values() for r in runrows]
        first = allrows[0]
        comparisons, flagged = compare_repeats(runs)

        configs.append({
            "config_id": config_id,
            "series": series_key(config_id),
            "arm": first["arm"],
            "model": first["model"],
            "runtime": first["runtime"],
            "workload": first["workload"],
            "concurrency_target": int(first["concurrency_target"] or 0),
            "repeats": len(runs),
            "runs": runs,
            # Pooling every repeat is the right basis for a distribution, but
            # never for deciding whether the repeats agreed. That is what the
            # comparison above is for.
            "pooled": summarise_run(allrows),
            "repeat_comparison": comparisons,
            "repeat_flags": flagged,
        })

    # Sorted so that every consumer -- table, CSV, charts -- presents families
    # together and concurrency in ladder order, rather than alphabetically,
    # where c10 would sort before c2.
    configs.sort(key=lambda c: (c["series"], c["concurrency_target"]))
    return configs


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

SUMMARY_FIELDS = [
    "config_id", "series", "arm", "runtime", "workload", "concurrency_target",
    "repeats",
    "requests", "error_rate_pct",
    "ttft_p50_ms", "ttft_p95_ms", "ttft_p99_ms",
    "e2e_p50_ms", "e2e_p95_ms", "e2e_p99_ms",
    "backend_p50_ms", "backend_p95_ms", "backend_p99_ms",
    "overhead_p50_ms", "overhead_p95_ms", "overhead_p99_ms",
    "tokens_per_sec_median", "tokens_per_sec_mean",
    "repeat_variance_flag",
]


def _pick(block, key):
    return None if block is None else block.get(key)


def summary_row(config):
    pooled = config["pooled"]
    return {
        "config_id": config["config_id"],
        "series": config["series"],
        "arm": config["arm"],
        "runtime": config["runtime"],
        "workload": config["workload"],
        "concurrency_target": config["concurrency_target"],
        "repeats": config["repeats"],
        "requests": pooled["requests"],
        "error_rate_pct": round(pooled["error_rate"] * 100.0, 4),
        "ttft_p50_ms": _pick(pooled["ttft_ms"], "p50"),
        "ttft_p95_ms": _pick(pooled["ttft_ms"], "p95"),
        "ttft_p99_ms": _pick(pooled["ttft_ms"], "p99"),
        "e2e_p50_ms": _pick(pooled["e2e_ms"], "p50"),
        "e2e_p95_ms": _pick(pooled["e2e_ms"], "p95"),
        "e2e_p99_ms": _pick(pooled["e2e_ms"], "p99"),
        "backend_p50_ms": _pick(pooled["backend_ms"], "p50"),
        "backend_p95_ms": _pick(pooled["backend_ms"], "p95"),
        "backend_p99_ms": _pick(pooled["backend_ms"], "p99"),
        "overhead_p50_ms": _pick(pooled["overhead_ms"], "p50"),
        "overhead_p95_ms": _pick(pooled["overhead_ms"], "p95"),
        "overhead_p99_ms": _pick(pooled["overhead_ms"], "p99"),
        "tokens_per_sec_median": pooled["tokens_per_sec_median"],
        "tokens_per_sec_mean": pooled["tokens_per_sec_mean"],
        "repeat_variance_flag": "; ".join(config["repeat_flags"]),
    }


def round_row(row):
    out = {}
    for key, value in row.items():
        out[key] = round(value, 3) if isinstance(value, float) else value
    return out


def write_summary_csv(path, configs):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        for config in configs:
            writer.writerow(round_row(summary_row(config)))


def fmt(value, width=9, places=2):
    if value is None:
        return "n/a".rjust(width)
    return ("%.*f" % (places, value)).rjust(width)


def print_table(configs):
    header = ("config_id                 conc  reqs   err%   ttft p95   e2e p50   "
              "e2e p95   e2e p99   ovh p50   ovh p95   tok/s med")
    print(header)
    print("-" * len(header))
    for config in configs:
        row = summary_row(config)
        print("%-24s %5d %5d %6.2f %10s %9s %9s %9s %9s %9s %10s" % (
            row["config_id"][:24],
            row["concurrency_target"],
            row["requests"],
            row["error_rate_pct"],
            fmt(row["ttft_p95_ms"], 10),
            fmt(row["e2e_p50_ms"]),
            fmt(row["e2e_p95_ms"]),
            fmt(row["e2e_p99_ms"]),
            fmt(row["overhead_p50_ms"]),
            fmt(row["overhead_p95_ms"]),
            fmt(row["tokens_per_sec_median"], 10),
        ))
    print()
    print("ovh is t1 - t2, computed per request and then summarised. All times "
          "in milliseconds.")


def print_repeat_report(configs):
    single = [c for c in configs if c["repeats"] < 2]
    flagged = [c for c in configs if c["repeat_flags"]]

    print()
    if single:
        print("%d of %d configurations have a single repeat, so the "
              "methodology's 10%% consistency check could not be applied to "
              "them." % (len(single), len(configs)))
    if not flagged:
        multi = len(configs) - len(single)
        if multi:
            print("All %d configurations with repeats agree within %.0f%%."
                  % (multi, REPEAT_VARIANCE_THRESHOLD_PCT))
        return

    print()
    print("REPEAT VARIANCE FLAGGED (threshold %.0f%%). These are reported, not "
          "averaged away:" % REPEAT_VARIANCE_THRESHOLD_PCT)
    for config in flagged:
        print("  %s" % config["config_id"])
        for reason in config["repeat_flags"]:
            print("    * %s" % reason)
        for key, block in config["repeat_comparison"].items():
            if block["spread_pct"] > REPEAT_VARIANCE_THRESHOLD_PCT:
                print("      %s: min %.2f, median %.2f, max %.2f"
                      % (block["label"], block["min"], block["median"],
                         block["max"]))


# --------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------

def render_charts(configs, rows, chart_dir, source_label):
    """Regenerate every chart from the raw rows.

    One line per configuration family per percentile. Families are separated by
    colour and percentiles by line style, so a chart never joins two different
    configurations into one misleading line.

    Returns the files written, and the charts deliberately skipped because the
    data needed to draw them does not exist. A skipped chart is reported rather
    than drawn empty.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return [], ["matplotlib is not installed; no charts were drawn"]

    os.makedirs(chart_dir, exist_ok=True)
    written = []
    skipped = []

    families = sorted({c["series"] for c in configs})
    ladder = sorted({c["concurrency_target"] for c in configs})
    by_concurrency = len(ladder) > 1
    colours = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    styles = {"p50": "-o", "p95": "--s", "p99": ":^"}

    def finish(fig, ax, filename, title, ylabel, xlabel):
        ax.set_title(title)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.3)
        if len(ax.get_lines()) > 8:
            ax.legend(fontsize=7, loc="center left", bbox_to_anchor=(1.01, 0.5))
        else:
            ax.legend(fontsize=8)
        # Every chart carries its provenance, so a reader can tell at a glance
        # which raw data produced it.
        fig.text(0.01, 0.01, "regenerated from raw CSV: %s" % source_label,
                 fontsize=7, alpha=0.6)
        fig.tight_layout(rect=(0, 0.04, 1, 0.94))
        path = os.path.join(chart_dir, filename)
        fig.savefig(path, dpi=140)
        plt.close(fig)
        written.append(path)

    def ladder_axis(ax):
        ax.set_xscale("log")
        ax.set_xticks(ladder)
        ax.set_xticklabels([str(c) for c in ladder])

    def series_points(family, metric, key):
        xs, ys = [], []
        for config in configs:
            if config["series"] != family:
                continue
            block = config["pooled"][metric]
            if block is None or block.get(key) is None:
                continue
            xs.append(config["concurrency_target"])
            ys.append(block[key])
        return xs, ys

    def ladder_chart(metric, keys, filename, title, ylabel):
        if not by_concurrency:
            skipped.append("%s: only one concurrency level present" % filename)
            return
        fig, ax = plt.subplots(figsize=(7.5, 4.5))
        drew = False
        for index, family in enumerate(families):
            colour = colours[index % len(colours)]
            for key in keys:
                xs, ys = series_points(family, metric, key)
                if not xs:
                    continue
                label = ("%s %s" % (family, key)) if len(families) > 1 else key
                ax.plot(xs, ys, styles[key], color=colour, label=label)
                drew = True
        if not drew:
            plt.close(fig)
            skipped.append("%s: no data for %s" % (filename, metric))
            return
        ladder_axis(ax)
        finish(fig, ax, filename, title, ylabel, "concurrency target")

    # End-to-end latency against concurrency.
    ladder_chart("e2e_ms", ["p50", "p95", "p99"],
                 "latency_vs_concurrency.png",
                 "End-to-end latency against concurrency", "milliseconds")

    # The headline chart: Moodle's own overhead against concurrency.
    ladder_chart("overhead_ms", ["p50", "p95", "p99"],
                 "overhead_vs_concurrency.png",
                 "Subsystem overhead (t1 - t2) against concurrency",
                 "milliseconds")

    # Time to first token. Only observable on a streaming path, so this is
    # skipped rather than drawn empty when the column is blank throughout.
    ladder_chart("ttft_ms", ["p50", "p95"],
                 "ttft_vs_concurrency.png",
                 "Time to first token against concurrency", "milliseconds")

    # The overhead distribution itself. A percentile table hides the shape; if
    # the distribution is bimodal, this is where it shows.
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    drew = False
    all_values = []
    for index, family in enumerate(families):
        colour = colours[index % len(colours)]
        members = [c for c in configs if c["series"] == family]
        for depth, config in enumerate(members):
            # Recomputed from the raw rows rather than from any stored summary,
            # which is what makes the chart reproducible from committed data.
            values = []
            for row in rows:
                if row["config_id"] != config["config_id"] or row["status"] != "ok":
                    continue
                t1 = to_float(row["t1_total_ms"])
                t2 = to_float(row["t2_model_ms"])
                if t1 is not None and t2 is not None:
                    values.append(t1 - t2)
            if not values:
                continue
            values.sort()
            all_values.extend(values)
            ys = [(i + 1) / len(values) for i in range(len(values))]
            alpha = 0.35 + 0.65 * (depth / max(1, len(members) - 1))
            label = ("%s c=%d" % (family, config["concurrency_target"])
                     if len(families) > 1 else "c=%d" % config["concurrency_target"])
            ax.plot(values, ys, color=colour, alpha=alpha, label=label)
            drew = True
    if drew:
        # A single slow request can stretch the axis until every line collapses
        # into one vertical stroke. Clip to the bulk of the distribution so the
        # shape is legible, and say on the chart that the tail was clipped and
        # how much of it lies beyond. Clipping the view is not the same as
        # discarding data, and the difference has to be visible.
        cutoff = percentile(all_values, 99.5)
        widest = max(all_values)
        if cutoff and widest > cutoff * 1.5:
            limit = cutoff * 1.2
            beyond = sum(1 for v in all_values if v > limit)
            ax.set_xlim(min(all_values), limit)
            fig.text(0.01, 0.955,
                     "x-axis clipped at %.1f ms: %d of %d requests (%.2f%%) "
                     "lie beyond it, out to %.1f ms"
                     % (limit, beyond, len(all_values),
                        beyond / len(all_values) * 100.0, widest),
                     fontsize=7.5, alpha=0.75)
        finish(fig, ax, "overhead_distribution.png",
               "Distribution of subsystem overhead", "cumulative fraction",
               "t1 - t2 (milliseconds)")
    else:
        plt.close(fig)
        skipped.append("overhead_distribution.png: no overhead data")

    return written, skipped


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Summarise raw benchmark CSVs and regenerate charts.")
    parser.add_argument("inputs", nargs="+",
                        help="CSV files, or directories to search for them")
    parser.add_argument("--out-dir", default="results",
                        help="where the summary and charts are written")
    parser.add_argument("--no-charts", action="store_true")
    return parser.parse_args(argv)


def collect_paths(inputs):
    paths = []
    for item in inputs:
        if os.path.isdir(item):
            paths.extend(sorted(glob.glob(os.path.join(item, "*.csv"))))
        else:
            paths.extend(sorted(glob.glob(item)))
    # The sidecar metadata files describe the instrument, not the results.
    return [p for p in paths if p.endswith(".csv")]


def main(argv=None):
    args = parse_args(argv)
    paths = collect_paths(args.inputs)
    if not paths:
        raise SystemExit("no CSV files found in: %s" % ", ".join(args.inputs))

    rows = load_rows(paths)
    if not rows:
        raise SystemExit("CSV files contained no rows")

    configs = analyse(rows)
    source_label = "%d file(s), %d requests" % (len(paths), len(rows))

    print("read %d rows from %d file(s)" % (len(rows), len(paths)))
    runtimes = sorted({c["runtime"] for c in configs})
    if runtimes == ["mock"]:
        print()
        print("NOTE: every row came from the mock backend. These numbers "
              "describe the instrument, not Moodle and not any model.")
    print()
    print_table(configs)
    print_repeat_report(configs)

    out_dir = args.out_dir
    summary_path = os.path.join(out_dir, "summary.csv")
    write_summary_csv(summary_path, configs)

    detail_path = os.path.join(out_dir, "summary.json")
    with open(detail_path, "w", encoding="utf-8") as handle:
        json.dump({
            "sources": paths,
            "requests": len(rows),
            "repeat_variance_threshold_pct": REPEAT_VARIANCE_THRESHOLD_PCT,
            "configurations": configs,
        }, handle, indent=2, default=str)

    print()
    print("summary written to %s" % summary_path)
    print("detail written to  %s" % detail_path)

    if not args.no_charts:
        written, skipped = render_charts(
            configs, rows, os.path.join(out_dir, "charts"), source_label)
        for path in written:
            print("chart written to   %s" % path)
        for reason in skipped:
            print("chart skipped:     %s" % reason)

    return 0


if __name__ == "__main__":
    sys.exit(main())
