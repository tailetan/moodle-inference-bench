#!/usr/bin/env python3
"""Open-loop load generator for the Moodle inference benchmark.

Why open loop
-------------
A closed-loop harness (each worker waits for its response before sending again)
lets the offered load adapt to how fast the system happens to be. Queueing never
accumulates and the tail looks wonderful. That failure has a name --
*coordinated omission* -- and it removes exactly the slow measurements this
study exists to find.

So: arrivals are a Poisson process at a fixed target rate, the schedule is
computed up front, and every latency is measured from the request's *scheduled*
arrival time rather than from whenever the harness got round to dispatching it.
If the harness runs late, that lateness lands inside the reported latency where
it belongs, and it is also reported separately as ``arrival_offset_ms``.

Two measurement points, measured independently
----------------------------------------------
* ``t2_model_ms`` -- latency at the HTTP boundary: from just before the request
  is sent to when the response is fully consumed. This is the same boundary the
  Moodle provider plugin will instrument in phase 3.
* ``t1_total_ms`` -- total latency from the scheduled arrival instant to the same
  completion. In the full Arm A this comes from Moodle, upstream of the
  provider, and covers the core AI manager, policy checks and logging.

Neither is derived from the other. They are taken from separate clock reads at
separate points in the code, deliberately, because ``t1 - t2`` is the headline
finding and a derived value would make it circular.

In phase 1 there is no Moodle in the path, so ``t1 - t2`` is the harness's own
scheduling error rather than Moodle's overhead. That is precisely what makes it
a validation signal: it should be close to zero, and if it is not, the
instrument is not yet trustworthy.

Concurrency ladder vs arrival rate
----------------------------------
The methodology fixes a concurrency ladder (1, 2, 5, 10, 20, 50) but open-loop
generation is parameterised by arrival rate, not by concurrency. The two are
joined by Little's law::

    rate = concurrency_target / expected_latency_seconds

So ``--concurrency 10`` against a backend configured for 410 ms means a target
arrival rate of 24.39 req/s, which will hold ~10 requests in flight *if the
system keeps up*. If it does not, in-flight count rises above the target -- and
that rise is a result, not a harness setting. This is the intended behaviour of
an open-loop design and is why the column is named ``concurrency_target``.

Usage::

    .venv/bin/python bench/harness.py --url http://127.0.0.1:8090 \\
        --concurrency 10 --expected-latency-ms 410 --duration 60 \\
        --out results/raw/example.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import aiohttp

# The results schema from the methodology, section 12. One row per request.
# Fixed: do not add columns here without changing the methodology document.
CSV_FIELDS = [
    "run_id", "timestamp", "arm", "config_id", "model", "quant", "runtime",
    "threads", "workload", "input_bucket", "prompt_id", "concurrency_target",
    "arrival_offset_ms", "t1_total_ms", "t2_model_ms", "ttft_ms",
    "output_tokens", "input_tokens", "tokens_per_sec", "status", "error_type",
]

# Phase 1 has no prompt corpus yet (that is phase 5). A single fixed synthetic
# prompt is used so the mock has something to receive; the mock's latency does
# not depend on it.
SYNTHETIC_PROMPT = (
    "Summarise the following course announcement in two sentences. "
    "The announcement text is deliberately short because in this phase the "
    "backend latency is configured rather than computed."
)
SYNTHETIC_PROMPT_ID = "synthetic-fixed-v1"


# --------------------------------------------------------------------------
# Small statistics helpers (stdlib only, so the instrument has no numeric deps)
# --------------------------------------------------------------------------

def percentile(values, q):
    """Linear-interpolation percentile. ``q`` in 0..100. Returns None if empty."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * (q / 100.0)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarise(values):
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": min(values),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": max(values),
        "mean": sum(values) / len(values),
    }


# --------------------------------------------------------------------------
# Harness self-monitoring
# --------------------------------------------------------------------------

class LoopLagMonitor:
    """Measures how late the event loop is running.

    A background task asks for a 5 ms sleep over and over and records the
    overshoot. If the loop is saturated -- too many sockets, too much JSON
    parsing, not enough CPU -- the overshoot grows, and every latency the
    harness reports is inflated by roughly that amount. Without this, harness
    saturation would show up as a plausible-looking system result.
    """

    INTERVAL_S = 0.005

    def __init__(self):
        self.samples_ms = []
        self._task = None
        self._running = False

    def start(self):
        self._running = True
        self._task = asyncio.ensure_future(self._run())

    async def _run(self):
        loop = asyncio.get_running_loop()
        while self._running:
            before = loop.time()
            await asyncio.sleep(self.INTERVAL_S)
            overshoot = (loop.time() - before - self.INTERVAL_S) * 1000.0
            self.samples_ms.append(overshoot)

    async def stop(self):
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


@dataclass
class RunConfig:
    url: str
    arm: str = "A"
    config_id: str = "adhoc"
    run_id: str = ""
    model: str = "mock-deterministic"
    quant: str = ""
    runtime: str = "mock"
    threads: str = ""
    workload: str = "synthetic"
    input_bucket: str = ""
    prompt_id: str = SYNTHETIC_PROMPT_ID
    concurrency_target: int = 1
    rate: float = 1.0
    duration_s: float = 30.0
    warmup_requests: int = 10
    lead_in_s: float = 0.5
    seed: int = 1
    timeout_s: float = 60.0
    stream: bool = True
    max_tokens: int = 32
    # Optional per-request overrides sent to the mock as X-Mock-* headers.
    mock_ttft_ms: float = None
    mock_inter_token_ms: float = None
    mock_tokens: int = None
    # Forces the mock to answer with this HTTP status, so the status and
    # error_type columns can be exercised without waiting for a real fault.
    mock_force_status: int = None
    # "backend" drives an OpenAI-compatible endpoint directly. "moodle" drives
    # Moodle's benchmark endpoint, which is the only way to obtain a T1 that
    # means what the methodology says it means. See issue_request.
    target: str = "backend"
    bench_token: str = ""
    action: str = "summarise_text"
    # Saturation thresholds. Crossing one of these fails the run loudly rather
    # than quietly reporting the harness's own ceiling as the system's.
    max_dispatch_lag_p99_ms: float = 25.0
    max_dispatch_lag_max_ms: float = 100.0
    max_loop_lag_p99_ms: float = 25.0
    # Sanity bound on the arrival schedule itself, in standard deviations of the
    # Poisson count. Not a saturation check -- see _assess_saturation.
    max_schedule_sigma: float = 5.0
    out_csv: str = ""


@dataclass
class RunResult:
    config: RunConfig
    rows: list = field(default_factory=list)
    dispatch_lag_ms: list = field(default_factory=list)
    # Wall time the harness saw per request. In moodle mode this is a different
    # quantity from t1 and is kept out of the results schema on purpose.
    harness_wall_ms: list = field(default_factory=list)
    loop_lag_ms: list = field(default_factory=list)
    mock_actual_ttft_ms: list = field(default_factory=list)
    mock_actual_total_ms: list = field(default_factory=list)
    mock_configured_ttft_ms: list = field(default_factory=list)
    mock_configured_total_ms: list = field(default_factory=list)
    wall_duration_s: float = 0.0
    scheduled_count: int = 0
    schedule_sigma: float = 0.0
    saturation_reasons: list = field(default_factory=list)

    @property
    def ok_rows(self):
        return [r for r in self.rows if r["status"] == "ok"]

    @property
    def error_rate(self):
        if not self.rows:
            return 0.0
        return 1.0 - (len(self.ok_rows) / len(self.rows))

    @property
    def achieved_rate(self):
        if self.wall_duration_s <= 0:
            return 0.0
        return len(self.rows) / self.wall_duration_s

    @property
    def saturated(self):
        return bool(self.saturation_reasons)


def build_arrival_offsets(rate, duration_s, seed):
    """Poisson arrival process: exponentially distributed gaps at ``rate``/s.

    The whole schedule is generated before the run starts so that dispatch never
    has to compute anything, and so a run is exactly reproducible from its seed.
    """
    rng = random.Random(seed)
    offsets = []
    clock = 0.0
    while True:
        clock += rng.expovariate(rate)
        if clock > duration_s:
            break
        offsets.append(clock)
    return offsets


def _request_headers(cfg):
    headers = {"Content-Type": "application/json"}
    if cfg.mock_ttft_ms is not None:
        headers["X-Mock-Ttft-Ms"] = repr(float(cfg.mock_ttft_ms))
    if cfg.mock_inter_token_ms is not None:
        headers["X-Mock-Inter-Token-Ms"] = repr(float(cfg.mock_inter_token_ms))
    if cfg.mock_tokens is not None:
        headers["X-Mock-Tokens"] = str(int(cfg.mock_tokens))
    if cfg.mock_force_status is not None:
        headers["X-Mock-Force-Status"] = str(int(cfg.mock_force_status))
    return headers


def _moodle_headers(cfg):
    return {
        "Content-Type": "application/json",
        "X-Bench-Token": cfg.bench_token,
    }


def _moodle_payload(cfg):
    return {
        "action": cfg.action,
        "prompttext": SYNTHETIC_PROMPT,
    }


def _payload(cfg):
    return {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": SYNTHETIC_PROMPT},
        ],
        "stream": cfg.stream,
        "temperature": 0,
        "max_tokens": cfg.max_tokens,
    }


def _blank_row(cfg, scheduled_wall):
    """A CSV row with every schema column present, so a failed request produces
    the same shape as a successful one."""
    return {
        "run_id": cfg.run_id,
        "timestamp": datetime.fromtimestamp(scheduled_wall, timezone.utc)
                             .isoformat(timespec="milliseconds")
                             .replace("+00:00", "Z"),
        "arm": cfg.arm,
        "config_id": cfg.config_id,
        "model": cfg.model,
        "quant": cfg.quant,
        "runtime": cfg.runtime,
        "threads": cfg.threads,
        "workload": cfg.workload,
        "input_bucket": cfg.input_bucket,
        "prompt_id": cfg.prompt_id,
        "concurrency_target": cfg.concurrency_target,
        "arrival_offset_ms": "",
        "t1_total_ms": "",
        "t2_model_ms": "",
        "ttft_ms": "",
        "output_tokens": "",
        "input_tokens": "",
        "tokens_per_sec": "",
        "status": "error",
        "error_type": "",
    }


async def _consume_stream(response, loop):
    """Read an SSE chat-completions stream.

    Returns ``(first_token_loop_time, output_tokens, usage, mock_report)``.
    ``first_token_loop_time`` is captured on the first chunk that carries actual
    content, not on the role-only preamble some backends send.
    """
    first_token_at = None
    delta_tokens = 0
    usage = None
    mock_report = None

    async for raw_line in response.content:
        line = raw_line.strip()
        if not line or not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if data == b"[DONE]":
            break
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        if event.get("usage"):
            usage = event["usage"]
        if event.get("mock"):
            mock_report = event["mock"]
        for choice in event.get("choices") or []:
            content = (choice.get("delta") or {}).get("content")
            if content:
                if first_token_at is None:
                    first_token_at = loop.time()
                delta_tokens += 1

    return first_token_at, delta_tokens, usage, mock_report


def _record_mock_report(result, mock_report):
    """Collect the backend's own view of what it did.

    Run-level only: these never reach the results schema. They exist so that a
    disagreement between measured and configured latency can be attributed to
    the mock or to the harness instead of argued about. Fields the backend
    reports as null -- TTFT on a non-streaming response, which has no first
    token -- are skipped rather than recorded as a number.
    """
    if not mock_report:
        return
    pairs = (
        (result.mock_actual_ttft_ms, "actual_ttft_ms"),
        (result.mock_actual_total_ms, "actual_total_ms"),
        (result.mock_configured_ttft_ms, "configured_ttft_ms"),
        (result.mock_configured_total_ms, "configured_total_ms"),
    )
    for bucket, key in pairs:
        value = mock_report.get(key)
        if value is not None:
            bucket.append(value)


async def issue_moodle_request(session, cfg, scheduled_at, scheduled_wall, result):
    """Drive Moodle's benchmark endpoint and record what Moodle reports.

    The important difference from the direct path: t1 and t2 are **not**
    measured by the harness here. They are measured inside Moodle, around the
    core AI manager call and around the provider's HTTP send respectively, and
    reported back in the response body.

    That is the whole point. The methodology puts T1 at the core AI manager, and
    a wall-clock time taken out here would also contain web-server queueing,
    which grows with load. Attributing that to Moodle's AI subsystem would
    manufacture the study's own prediction 2 out of nothing. The harness's wall
    clock is still recorded, but as run-level metadata answering a different
    question, not as t1.
    """
    loop = asyncio.get_running_loop()
    row = _blank_row(cfg, scheduled_wall)

    dispatch_at = loop.time()
    arrival_offset_ms = (dispatch_at - scheduled_at) * 1000.0
    result.dispatch_lag_ms.append(arrival_offset_ms)
    row["arrival_offset_ms"] = round(arrival_offset_ms, 3)

    status = "ok"
    error_type = ""
    body = None

    wall_start = time.perf_counter()
    try:
        async with session.post(
            cfg.url,
            json=_moodle_payload(cfg),
            headers=_moodle_headers(cfg),
        ) as response:
            text = await response.text()
            if response.status != 200:
                status = "error"
                error_type = "http_%d" % response.status
            else:
                try:
                    body = json.loads(text)
                except json.JSONDecodeError:
                    # bench.php always answers with JSON, including on failure.
                    # HTML here means Moodle rendered an error page, which the
                    # harness must not silently record as a bad measurement.
                    status = "error"
                    error_type = "non_json_response"
    except asyncio.TimeoutError:
        status = "timeout"
        error_type = "timeout"
    except aiohttp.ClientError as exc:
        status = "error"
        error_type = type(exc).__name__
    except Exception as exc:  # pragma: no cover - defensive
        status = "error"
        error_type = type(exc).__name__

    wall_ms = (time.perf_counter() - wall_start) * 1000.0
    result.harness_wall_ms.append(wall_ms)

    if body is not None:
        if not body.get("success"):
            status = "error"
            error_type = str(body.get("errorcode") or body.get("error") or "moodle_error")
            if body.get("backend_error_type"):
                error_type = body["backend_error_type"]
        row["t1_total_ms"] = body.get("t1_total_ms", "")
        row["t2_model_ms"] = body.get("t2_model_ms")
        if row["t2_model_ms"] is None:
            row["t2_model_ms"] = ""
        row["input_tokens"] = body.get("input_tokens") or ""
        if status == "ok":
            row["output_tokens"] = body.get("output_tokens") or 0

    row["status"] = status
    row["error_type"] = error_type
    if status != "ok":
        row["output_tokens"] = 0

    # ttft_ms and tokens_per_sec stay empty. Moodle's providers are
    # non-streaming, so there is no first-token event and no decode window to
    # observe on this path.
    result.rows.append(row)


async def issue_request(session, cfg, scheduled_at, scheduled_wall, result):
    """Perform one request and append exactly one CSV row."""
    loop = asyncio.get_running_loop()
    row = _blank_row(cfg, scheduled_wall)

    # How late the harness was in getting this request out of the door. This is
    # instrument error, and it is recorded per request rather than averaged.
    dispatch_at = loop.time()
    arrival_offset_ms = (dispatch_at - scheduled_at) * 1000.0
    result.dispatch_lag_ms.append(arrival_offset_ms)
    row["arrival_offset_ms"] = round(arrival_offset_ms, 3)

    first_token_at = None
    output_tokens = 0
    input_tokens = ""
    status = "ok"
    error_type = ""

    # t2 is measured on its own clock, opened immediately before the HTTP call
    # and closed immediately after, so it shares nothing with the t1 measurement
    # above beyond both clocks being monotonic.
    t2_start = time.perf_counter()
    try:
        async with session.post(
            cfg.url.rstrip("/") + "/v1/chat/completions",
            json=_payload(cfg),
            headers=_request_headers(cfg),
        ) as response:
            if response.status != 200:
                await response.read()
                status = "error"
                error_type = "http_%d" % response.status
            elif cfg.stream:
                (first_token_at, output_tokens, usage,
                 mock_report) = await _consume_stream(response, loop)
                if usage:
                    # Prefer the backend's own count when it gives one; fall
                    # back to counting content deltas.
                    output_tokens = usage.get("completion_tokens", output_tokens)
                    input_tokens = usage.get("prompt_tokens", "")
                _record_mock_report(result, mock_report)
                if first_token_at is None:
                    status = "error"
                    error_type = "empty_response"
            else:
                body = await response.json()
                # No first-token timestamp is taken here: a non-streaming
                # response has no first-token event to observe, and the TTFT
                # column stays empty for this mode.
                usage = body.get("usage") or {}
                output_tokens = usage.get("completion_tokens", 0)
                input_tokens = usage.get("prompt_tokens", "")
                mock_report = body.get("mock")
                _record_mock_report(result, mock_report)
    except asyncio.TimeoutError:
        status = "timeout"
        error_type = "timeout"
    except aiohttp.ClientError as exc:
        status = "error"
        error_type = type(exc).__name__
    except Exception as exc:  # pragma: no cover - defensive
        status = "error"
        error_type = type(exc).__name__

    t2_ms = (time.perf_counter() - t2_start) * 1000.0
    t1_ms = (loop.time() - scheduled_at) * 1000.0

    row["t1_total_ms"] = round(t1_ms, 3)
    row["t2_model_ms"] = round(t2_ms, 3)
    row["status"] = status
    row["error_type"] = error_type
    row["input_tokens"] = input_tokens
    row["output_tokens"] = output_tokens if status == "ok" else 0

    # Time to first token and sustained output rate are only observable on a
    # streaming response. A non-streaming backend delivers the whole body at
    # once: there is no first-token event and no decode window, so both columns
    # are left empty rather than filled with a value copied from t1 or computed
    # over a zero-width interval. End-to-end latency is already in t1_total_ms
    # and nothing is lost by not restating it here.
    if status == "ok" and cfg.stream and first_token_at is not None:
        ttft_ms = (first_token_at - scheduled_at) * 1000.0
        row["ttft_ms"] = round(ttft_ms, 3)
        # Sustained output rate: completion tokens over the decode window,
        # excluding time to first token. Both terms are referenced to the same
        # scheduled-arrival origin, so the scheduling offset cancels out.
        decode_s = (t1_ms - ttft_ms) / 1000.0
        if output_tokens and decode_s > 0:
            row["tokens_per_sec"] = round(output_tokens / decode_s, 3)

    result.rows.append(row)


async def _warmup(session, cfg):
    """Sequential throwaway requests, not recorded.

    For the mock this changes nothing, but the same code path serves Arm B where
    the methodology requires a warm model, and exercising it here means it is
    already proven when it matters.
    """
    for _ in range(cfg.warmup_requests):
        try:
            if cfg.target == "moodle":
                url, payload, headers = (
                    cfg.url, _moodle_payload(cfg), _moodle_headers(cfg))
            else:
                url, payload, headers = (
                    cfg.url.rstrip("/") + "/v1/chat/completions",
                    _payload(cfg), _request_headers(cfg))
            async with session.post(url, json=payload, headers=headers) as response:
                await response.read()
        except Exception:
            pass


async def run_load(cfg):
    result = RunResult(config=cfg)
    offsets = build_arrival_offsets(cfg.rate, cfg.duration_s, cfg.seed)
    result.scheduled_count = len(offsets)
    if not offsets:
        raise SystemExit(
            "no arrivals scheduled: rate %.4f/s over %.1fs produced nothing"
            % (cfg.rate, cfg.duration_s))

    timeout = aiohttp.ClientTimeout(total=cfg.timeout_s)
    # limit=0 means no connection-pool cap. This is not a tuning choice: a
    # bounded pool would make requests queue inside the client and silently turn
    # this open-loop harness into a closed-loop one.
    connector = aiohttp.TCPConnector(limit=0, ttl_dns_cache=300)

    monitor = LoopLagMonitor()
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        await _warmup(session, cfg)

        loop = asyncio.get_running_loop()
        issue = issue_moodle_request if cfg.target == "moodle" else issue_request
        monitor.start()

        start_loop = loop.time() + cfg.lead_in_s
        start_wall = time.time() + cfg.lead_in_s
        tasks = []
        for offset in offsets:
            scheduled_at = start_loop + offset
            now = loop.time()
            if scheduled_at > now:
                await asyncio.sleep(scheduled_at - now)
            tasks.append(asyncio.ensure_future(issue(
                session, cfg, scheduled_at, start_wall + offset, result)))

        dispatch_done = loop.time()
        if tasks:
            await asyncio.gather(*tasks)
        result.wall_duration_s = dispatch_done - start_loop

        await monitor.stop()

    result.loop_lag_ms = monitor.samples_ms
    _assess_saturation(result)
    return result


def _assess_saturation(result):
    """Decide whether the harness, rather than the system, was the limit.

    Every check here is about the instrument, not about the study's success
    criteria, and each one fails the run rather than annotating it.

    Note what is deliberately *not* checked: the number of requests actually
    generated against ``rate * duration``. Arrivals are Poisson, so that count
    is a random variable; over a short window it deviates from its mean by a lot
    and that is the process working correctly, not the harness failing. Only a
    deviation far outside Poisson noise indicates a broken schedule generator,
    and that is reported separately below rather than as saturation.

    Whether the harness kept up is answered by dispatch lag: if requests went
    out at their scheduled instants, the offered load was the scheduled load.
    """
    cfg = result.config
    reasons = result.saturation_reasons

    if result.scheduled_count != len(result.rows):
        reasons.append(
            "%d arrivals were scheduled but %d rows were recorded: requests "
            "were dropped, so the results are incomplete"
            % (result.scheduled_count, len(result.rows)))

    dispatch_p99 = percentile(result.dispatch_lag_ms, 99)
    if dispatch_p99 is not None and dispatch_p99 > cfg.max_dispatch_lag_p99_ms:
        reasons.append(
            "dispatch lag p99 %.2f ms exceeds %.2f ms: the harness could not "
            "put requests on the wire at their scheduled arrival times"
            % (dispatch_p99, cfg.max_dispatch_lag_p99_ms))

    if result.dispatch_lag_ms:
        dispatch_max = max(result.dispatch_lag_ms)
        if dispatch_max > cfg.max_dispatch_lag_max_ms:
            reasons.append(
                "worst dispatch lag %.2f ms exceeds %.2f ms: the dispatch loop "
                "fell behind the arrival schedule at least once"
                % (dispatch_max, cfg.max_dispatch_lag_max_ms))

    loop_p99 = percentile(result.loop_lag_ms, 99)
    if loop_p99 is not None and loop_p99 > cfg.max_loop_lag_p99_ms:
        reasons.append(
            "event-loop lag p99 %.2f ms exceeds %.2f ms: the harness event loop "
            "was itself the queue, so every latency reported is inflated"
            % (loop_p99, cfg.max_loop_lag_p99_ms))

    # Schedule generator sanity, not saturation: a Poisson count has variance
    # equal to its mean, so flag only a deviation that Poisson noise cannot
    # explain.
    expected = cfg.rate * cfg.duration_s
    if expected > 0:
        sigma = math.sqrt(expected)
        deviation = abs(result.scheduled_count - expected) / sigma
        result.schedule_sigma = deviation
        if deviation > cfg.max_schedule_sigma:
            reasons.append(
                "arrival schedule produced %d requests where %.1f +/- %.1f were "
                "expected (%.1f sigma): the arrival generator is not producing "
                "the configured rate"
                % (result.scheduled_count, expected, sigma, deviation))


def write_csv(path, rows):
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    # Rows are buffered in memory and written once, at the end. Writing during
    # the run would put filesystem latency inside the measurement.
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_meta(result):
    """Run-level metadata, written beside the CSV.

    This is deliberately *not* in the results schema: the schema is one row per
    request and describes the system under test. This file describes the
    instrument, and exists so a reader can check the instrument was sound
    without re-deriving it from the rows.
    """
    cfg = result.config
    ok = result.ok_rows
    return {
        "run_id": cfg.run_id,
        "config_id": cfg.config_id,
        "arm": cfg.arm,
        "url": cfg.url,
        "target": cfg.target,
        "action": cfg.action if cfg.target == "moodle" else None,
        "stream": cfg.stream if cfg.target == "backend" else False,
        "seed": cfg.seed,
        "concurrency_target": cfg.concurrency_target,
        "target_rate_per_s": cfg.rate,
        "achieved_rate_per_s": result.achieved_rate,
        # Poisson arrivals: over a short window the realised rate differs from
        # the target by ordinary sampling variance. schedule_sigma says by how
        # many standard deviations, which is the number worth reading.
        "schedule_sigma": result.schedule_sigma,
        "duration_s_configured": cfg.duration_s,
        "duration_s_dispatch_window": result.wall_duration_s,
        "requests_scheduled": result.scheduled_count,
        "requests_recorded": len(result.rows),
        "requests_ok": len(ok),
        "error_rate": result.error_rate,
        "warmup_requests": cfg.warmup_requests,
        "mock_request_overrides": {
            "ttft_ms": cfg.mock_ttft_ms,
            "inter_token_ms": cfg.mock_inter_token_ms,
            "tokens": cfg.mock_tokens,
            "force_status": cfg.mock_force_status,
        },
        "instrument": {
            "dispatch_lag_ms": summarise(result.dispatch_lag_ms),
            # In moodle mode this is deliberately not t1. It is the harness's
            # own view, which includes web-server queueing, and it is kept here
            # rather than in the results schema so the two cannot be confused.
            "harness_wall_ms": summarise(result.harness_wall_ms),
            "event_loop_lag_ms": summarise(result.loop_lag_ms),
            "saturated": result.saturated,
            "saturation_reasons": result.saturation_reasons,
        },
        "backend_reported": {
            "configured_ttft_ms": summarise(result.mock_configured_ttft_ms),
            "configured_total_ms": summarise(result.mock_configured_total_ms),
            "actual_ttft_ms": summarise(result.mock_actual_ttft_ms),
            "actual_total_ms": summarise(result.mock_actual_total_ms),
        },
        "measured": {
            "t1_total_ms": summarise([r["t1_total_ms"] for r in ok]),
            "t2_model_ms": summarise([r["t2_model_ms"] for r in ok]),
            "ttft_ms": summarise([r["ttft_ms"] for r in ok if r["ttft_ms"] != ""]),
            "t1_minus_t2_ms": summarise(
                [r["t1_total_ms"] - r["t2_model_ms"] for r in ok]),
            "tokens_per_sec": summarise(
                [r["tokens_per_sec"] for r in ok if r["tokens_per_sec"] != ""]),
        },
    }


def print_saturation_banner(result):
    bar = "!" * 78
    print("\n" + bar, file=sys.stderr)
    print("HARNESS SATURATION -- THESE NUMBERS DESCRIBE THE HARNESS, NOT THE SYSTEM",
          file=sys.stderr)
    print(bar, file=sys.stderr)
    for reason in result.saturation_reasons:
        print("  * " + reason, file=sys.stderr)
    print(bar + "\n", file=sys.stderr)


def print_summary(result):
    meta = build_meta(result)
    print(json.dumps(meta, indent=2, default=str))
    if result.saturated:
        print_saturation_banner(result)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Open-loop Poisson load generator with dual t1/t2 timing.")
    parser.add_argument("--url", default="http://127.0.0.1:8090",
                        help="base URL of the OpenAI-compatible endpoint, or "
                             "the full bench.php URL when --target moodle")
    parser.add_argument("--target", choices=["backend", "moodle"],
                        default="backend",
                        help="backend drives an OpenAI-compatible endpoint "
                             "directly; moodle drives Moodle's benchmark "
                             "endpoint and takes t1 and t2 from its response")
    parser.add_argument("--bench-token", default="",
                        help="X-Bench-Token value, required with --target moodle")
    parser.add_argument("--action", default="summarise_text",
                        choices=["summarise_text", "generate_text", "explain_text"],
                        help="Moodle AI action to invoke with --target moodle")
    parser.add_argument("--arm", default="A")
    parser.add_argument("--config-id", default="adhoc")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--model", default="mock-deterministic")
    parser.add_argument("--quant", default="")
    parser.add_argument("--runtime", default="mock")
    parser.add_argument("--threads", default="")
    parser.add_argument("--workload", default="synthetic")
    parser.add_argument("--input-bucket", default="")

    rate_group = parser.add_mutually_exclusive_group(required=True)
    rate_group.add_argument("--rate", type=float,
                            help="target arrival rate in requests per second")
    rate_group.add_argument("--concurrency", type=int,
                            help="target concurrency; converted to an arrival "
                                 "rate by Little's law using "
                                 "--expected-latency-ms")

    parser.add_argument("--expected-latency-ms", type=float,
                        help="expected end-to-end latency, required with "
                             "--concurrency")
    parser.add_argument("--duration", type=float, default=30.0,
                        help="length of the arrival window in seconds")
    parser.add_argument("--warmup", type=int, default=10,
                        help="throwaway requests before the measured window")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--no-stream", action="store_true",
                        help="use a non-streaming request, as the Moodle "
                             "provider will")
    parser.add_argument("--max-tokens", type=int, default=32)
    parser.add_argument("--mock-ttft-ms", type=float, default=None)
    parser.add_argument("--mock-inter-token-ms", type=float, default=None)
    parser.add_argument("--mock-tokens", type=int, default=None)
    parser.add_argument("--mock-force-status", type=int, default=None,
                        help="make the mock fail with this HTTP status, to "
                             "exercise the status and error_type columns")
    parser.add_argument("--max-dispatch-lag-p99-ms", type=float, default=25.0)
    parser.add_argument("--max-loop-lag-p99-ms", type=float, default=25.0)
    parser.add_argument("--max-dispatch-lag-max-ms", type=float, default=100.0)
    parser.add_argument("--max-schedule-sigma", type=float, default=5.0)
    parser.add_argument("--out", default="",
                        help="CSV output path; a .meta.json sidecar is written "
                             "alongside it")
    parser.add_argument("--fail-on-saturation", action="store_true",
                        help="exit non-zero if the harness saturated")
    return parser.parse_args(argv)


def config_from_args(args):
    if args.target == "moodle" and not args.bench_token:
        raise SystemExit(
            "--target moodle requires --bench-token. The benchmark endpoint "
            "refuses every request without it.")
    if args.concurrency is not None:
        if not args.expected_latency_ms:
            raise SystemExit("--concurrency requires --expected-latency-ms")
        rate = args.concurrency / (args.expected_latency_ms / 1000.0)
        concurrency_target = args.concurrency
    else:
        rate = args.rate
        # Without an expected latency there is no defensible concurrency figure,
        # so record 0 rather than invent one.
        concurrency_target = (
            int(round(rate * args.expected_latency_ms / 1000.0))
            if args.expected_latency_ms else 0)

    run_id = args.run_id or "%s-%s" % (
        args.config_id,
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
    )

    return RunConfig(
        url=args.url, arm=args.arm, config_id=args.config_id, run_id=run_id,
        model=args.model, quant=args.quant, runtime=args.runtime,
        threads=args.threads, workload=args.workload,
        input_bucket=args.input_bucket,
        concurrency_target=concurrency_target, rate=rate,
        duration_s=args.duration, warmup_requests=args.warmup, seed=args.seed,
        timeout_s=args.timeout, stream=not args.no_stream,
        max_tokens=args.max_tokens, mock_ttft_ms=args.mock_ttft_ms,
        mock_inter_token_ms=args.mock_inter_token_ms,
        mock_tokens=args.mock_tokens,
        mock_force_status=args.mock_force_status,
        max_dispatch_lag_p99_ms=args.max_dispatch_lag_p99_ms,
        max_loop_lag_p99_ms=args.max_loop_lag_p99_ms,
        target=args.target,
        bench_token=args.bench_token,
        action=args.action,
        max_dispatch_lag_max_ms=args.max_dispatch_lag_max_ms,
        max_schedule_sigma=args.max_schedule_sigma,
        out_csv=args.out,
    )


def main(argv=None):
    args = parse_args(argv)
    cfg = config_from_args(args)
    result = asyncio.run(run_load(cfg))

    if cfg.out_csv:
        write_csv(cfg.out_csv, result.rows)
        meta_path = cfg.out_csv + ".meta.json"
        with open(meta_path, "w", encoding="utf-8") as handle:
            json.dump(build_meta(result), handle, indent=2, default=str)

    print_summary(result)

    if args.fail_on_saturation and result.saturated:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
