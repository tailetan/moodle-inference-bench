#!/usr/bin/env python3
"""Deterministic OpenAI-compatible mock endpoint for Arm A.

Arm A measures Moodle's own AI-subsystem overhead as ``t1 - t2``. That
subtraction is only clean if ``t2`` is a number we *chose* rather than a number
we measured off a noisy model. This server is that chosen number: it answers
``POST /v1/chat/completions`` on a timer and nothing else.

Three knobs, all deterministic:

* ``ttft_ms``          time from "request fully received" to the first token
* ``inter_token_ms``   gap between consecutive tokens
* ``tokens``           how many tokens to emit

so the configured end-to-end latency is::

    total_ms = ttft_ms + (tokens - 1) * inter_token_ms

Two properties matter for the instrument-validation phase:

1. **Absolute deadlines, not relative sleeps.** Every token is scheduled at
   ``t0 + ttft + i * inter_token`` and we sleep *until* that instant. Sleeping
   ``inter_token`` seconds 200 times in a row would accumulate the event loop's
   per-sleep overshoot into a large error; sleeping to an absolute deadline
   discards that error on every token instead of compounding it.
2. **The server reports what it actually did.** Configured values go out as
   ``X-Mock-*`` response headers before the body starts; the timings the server
   really achieved go out in the final chunk under a ``mock`` key. So when a
   measurement disagrees with the configuration we can tell whether the mock
   drifted or the harness mis-measured, rather than guessing.

Both streaming (SSE) and non-streaming responses are supported. The harness
uses streaming; the Moodle provider in phase 3 will use non-streaming, and both
paths must be equally deterministic.

Run::

    .venv/bin/python bench/mock_server.py --profile mid --port 8080
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from aiohttp import web

# --------------------------------------------------------------------------
# Latency profiles
# --------------------------------------------------------------------------
# These are *configuration points*, not measurements of any model. "fast" and
# "slow" are the two Arm A settings the methodology calls for, set far enough
# apart to separate a fixed per-request overhead in Moodle from one that scales
# with backend latency. "mid" exists only for instrument validation, where a
# short total latency keeps the validation run quick.
PROFILES = {
    "fast": {"ttft_ms": 50.0, "inter_token_ms": 5.0, "tokens": 32},
    "mid": {"ttft_ms": 100.0, "inter_token_ms": 10.0, "tokens": 32},
    "slow": {"ttft_ms": 800.0, "inter_token_ms": 25.0, "tokens": 200},
}

MODEL_NAME = "mock-deterministic"

# One "token" of output text. Deliberately boring: the mock says nothing about
# quality, only about time.
TOKEN_TEXT = "lorem "


@dataclass(frozen=True)
class Timing:
    """The latency contract for a single request."""

    ttft_ms: float
    inter_token_ms: float
    tokens: int
    force_status: int = 200

    @property
    def total_ms(self) -> float:
        return self.ttft_ms + max(0, self.tokens - 1) * self.inter_token_ms


def _pick(headers, body_mock, key, header, default, cast):
    """Resolve one knob: request header wins, then the body ``mock`` object,
    then the server default. Headers are offered so any client can drive the
    mock without putting non-standard fields into an OpenAI request body."""
    raw = headers.get(header)
    if raw is None:
        raw = body_mock.get(key)
    if raw is None:
        return default
    return cast(raw)


def resolve_timing(headers, body, defaults):
    body_mock = body.get("mock") or {}
    if not isinstance(body_mock, dict):
        body_mock = {}
    return Timing(
        ttft_ms=_pick(headers, body_mock, "ttft_ms", "X-Mock-Ttft-Ms",
                      defaults.ttft_ms, float),
        inter_token_ms=_pick(headers, body_mock, "inter_token_ms",
                             "X-Mock-Inter-Token-Ms",
                             defaults.inter_token_ms, float),
        tokens=_pick(headers, body_mock, "tokens", "X-Mock-Tokens",
                     defaults.tokens, int),
        force_status=_pick(headers, body_mock, "force_status",
                           "X-Mock-Force-Status", 200, int),
    )


async def sleep_until(loop, deadline):
    """Sleep until an absolute event-loop timestamp.

    Returns immediately if the deadline has already passed, which is how
    per-token scheduling error stops accumulating.
    """
    remaining = deadline - loop.time()
    if remaining > 0:
        await asyncio.sleep(remaining)


def estimate_prompt_tokens(body):
    """Rough prompt-token count for the ``usage`` block.

    A chars/4 approximation, not a real tokenizer. Nothing in Arm A depends on
    it -- the mock's latency comes from configuration, not from input length --
    so an approximation is honest and sufficient here. Arm B takes token counts
    from the runtime itself.
    """
    chars = 0
    for message in body.get("messages") or []:
        content = message.get("content")
        if isinstance(content, str):
            chars += len(content)
    return max(1, math.ceil(chars / 4))


def _chunk(request_id, created, delta, finish, extra=None):
    payload = {
        "id": request_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": MODEL_NAME,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if extra:
        payload.update(extra)
    return b"data: " + json.dumps(payload, separators=(",", ":")).encode() + b"\n\n"


def _config_headers(timing):
    """Configured values, sent before the body so a client can read them even on
    a streaming response."""
    return {
        "X-Mock-Ttft-Ms": "%.3f" % timing.ttft_ms,
        "X-Mock-Inter-Token-Ms": "%.3f" % timing.inter_token_ms,
        "X-Mock-Tokens": str(timing.tokens),
        "X-Mock-Total-Ms": "%.3f" % timing.total_ms,
    }


def _mock_report(timing, actual_ttft_ms, actual_total_ms):
    """Server-side ground truth, so a measurement disagreement can be
    attributed to the mock or to the harness rather than argued about."""
    return {
        "configured_ttft_ms": timing.ttft_ms,
        "configured_inter_token_ms": timing.inter_token_ms,
        "configured_total_ms": timing.total_ms,
        "actual_ttft_ms": actual_ttft_ms,
        "actual_total_ms": actual_total_ms,
    }


async def handle_chat_completions(request):
    loop = asyncio.get_running_loop()
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"error": {"message": "invalid JSON body",
                       "type": "invalid_request_error"}},
            status=400,
        )

    # The clock starts once the whole request is in hand, which is where a real
    # backend would begin work.
    t0 = loop.time()
    timing = resolve_timing(request.headers, body, request.app["defaults"])

    if timing.force_status != 200:
        # Deliberate failure injection, so the harness's error columns can be
        # exercised without waiting for a real fault.
        return web.json_response(
            {"error": {"message": "injected mock failure",
                       "type": "mock_injected_error"}},
            status=timing.force_status,
            headers=_config_headers(timing),
        )

    request_id = "chatcmpl-mock-" + uuid.uuid4().hex[:16]
    created = int(time.time())
    prompt_tokens = estimate_prompt_tokens(body)
    stream = bool(body.get("stream", False))

    if not stream:
        await sleep_until(loop, t0 + timing.total_ms / 1000.0)
        actual_total_ms = (loop.time() - t0) * 1000.0
        return web.json_response(
            {
                "id": request_id,
                "object": "chat.completion",
                "created": created,
                "model": MODEL_NAME,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant",
                                "content": TOKEN_TEXT * timing.tokens},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": timing.tokens,
                    "total_tokens": prompt_tokens + timing.tokens,
                },
                # A non-streaming response has no observable first token, so
                # actual TTFT is reported as null rather than as a copy of the
                # total.
                "mock": _mock_report(timing, None, actual_total_ms),
            },
            headers=_config_headers(timing),
        )

    headers = _config_headers(timing)
    headers.update({
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })
    response = web.StreamResponse(status=200, headers=headers)
    await response.prepare(request)

    # Every token after the first serialises to identical bytes, so build them
    # once. At concurrency 50 the mock emits thousands of chunks per second and
    # a json.dumps per chunk would put the mock's own CPU cost inside the
    # latency it is supposed to be defining.
    first_chunk = _chunk(request_id, created,
                         # The first chunk carries the role *and* the first
                         # token, so that time-to-first-token as the harness
                         # sees it is time to real content, not time to an
                         # empty role-only preamble.
                         {"role": "assistant", "content": TOKEN_TEXT}, None)
    repeat_chunk = _chunk(request_id, created, {"content": TOKEN_TEXT}, None)

    actual_ttft_ms = 0.0
    for index in range(timing.tokens):
        deadline = t0 + (timing.ttft_ms + index * timing.inter_token_ms) / 1000.0
        await sleep_until(loop, deadline)
        if index == 0:
            actual_ttft_ms = (loop.time() - t0) * 1000.0
            await response.write(first_chunk)
        else:
            await response.write(repeat_chunk)

    actual_total_ms = (loop.time() - t0) * 1000.0
    await response.write(_chunk(
        request_id, created, {}, "stop",
        extra={
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": timing.tokens,
                "total_tokens": prompt_tokens + timing.tokens,
            },
            "mock": _mock_report(timing, actual_ttft_ms, actual_total_ms),
        },
    ))
    await response.write(b"data: [DONE]\n\n")
    await response.write_eof()
    return response


async def handle_health(request):
    defaults = request.app["defaults"]
    return web.json_response({
        "status": "ok",
        "model": MODEL_NAME,
        "defaults": {
            "ttft_ms": defaults.ttft_ms,
            "inter_token_ms": defaults.inter_token_ms,
            "tokens": defaults.tokens,
            "total_ms": defaults.total_ms,
        },
    })


async def handle_models(request):
    return web.json_response({
        "object": "list",
        "data": [{"id": MODEL_NAME, "object": "model",
                  "owned_by": "moodle-inference-bench"}],
    })


def build_app(defaults):
    app = web.Application()
    app["defaults"] = defaults
    app.router.add_post("/v1/chat/completions", handle_chat_completions)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/v1/models", handle_models)
    return app


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Deterministic OpenAI-compatible mock endpoint for Arm A.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="mid",
                        help="named latency profile used as the server default")
    parser.add_argument("--ttft-ms", type=float, default=None,
                        help="override the profile time-to-first-token")
    parser.add_argument("--inter-token-ms", type=float, default=None,
                        help="override the profile inter-token delay")
    parser.add_argument("--tokens", type=int, default=None,
                        help="override the profile output token count")
    return parser.parse_args(argv)


def defaults_from_args(args):
    profile = PROFILES[args.profile]
    return Timing(
        ttft_ms=profile["ttft_ms"] if args.ttft_ms is None else args.ttft_ms,
        inter_token_ms=(profile["inter_token_ms"] if args.inter_token_ms is None
                        else args.inter_token_ms),
        tokens=int(profile["tokens"] if args.tokens is None else args.tokens),
    )


def main(argv=None):
    args = parse_args(argv)
    defaults = defaults_from_args(args)
    print(
        "mock_server: profile=%s ttft=%sms inter_token=%sms tokens=%d "
        "total=%sms  ->  http://%s:%d"
        % (args.profile, defaults.ttft_ms, defaults.inter_token_ms,
           defaults.tokens, defaults.total_ms, args.host, args.port),
        flush=True,
    )
    web.run_app(build_app(defaults), host=args.host, port=args.port,
                print=None, access_log=None)


if __name__ == "__main__":
    main()
