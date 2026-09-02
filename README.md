# moodle-inference-bench

An R&D study measuring two things about AI inference in Moodle, on a laptop with
no GPU:

1. **How much latency Moodle's own AI subsystem adds** to a request, isolated
   from model latency. As far as we can establish, nobody has published this.
2. **Which Moodle AI actions are viable on commodity hardware with no GPU.**

There is no GPU anywhere in this study. That is a deliberate scope choice, not a
limitation being worked around: most Moodle sites have no GPU and never will, and
a model running on commodity CPU hardware sits genuinely at the edge of the edge
AI spectrum in a way a rented data-centre GPU does not.

The experimental design is [`docs/methodology.md`](docs/methodology.md) and it is
the specification. Code that would contradict it does not get written.

## Status

**Phase 1 of 6 complete: the measuring instrument is built and validated.**

No study result exists yet. Read that sentence literally -- there is currently no
Moodle in the measurement path, no model, and no figure for Moodle's subsystem
overhead. What exists is a load harness and a mock backend, and evidence that the
harness measures accurately.

| Phase | | Status |
|---|---|---|
| 1 | Harness and instrument validation | Done |
| 2 | Analysis and plotting | Not started |
| 3 | Moodle provider plugin (`aiprovider_edgellm`) | Not started |
| 4 | Docker environment | Not started |
| 5 | Prompt corpus | Not started |
| 6 | Arm A execution | Not started |
| -- | Arm B (CPU inference viability) | Deliberately last |

[`docs/results.md`](docs/results.md) states what has and has not been measured in
more detail.

## The two arms

The study splits in two, and the halves are kept separate throughout because
they have different constraints.

**Arm A, subsystem overhead.** Drives Moodle against a mock endpoint with
configurable fixed latency. No model runs. Because the mock is cheap, this arm
carries the full concurrency ladder: 1, 2, 5, 10, 20, 50. This is the novel
contribution and it runs first.

**Arm B, CPU inference viability.** Real models on CPU via llama.cpp and Ollama.
Concurrency only 1, 2 and 4, because CPU inference does not batch and anything
beyond that measures thread contention rather than capacity.

## Two measurement points

Every request records both, independently:

- **`t2_model_ms`** -- backend latency at the HTTP boundary between the provider
  plugin and the endpoint.
- **`t1_total_ms`** -- total latency as Moodle sees it, including the core AI
  manager, policy checks and the provider plugin.

`t1 - t2` is Moodle's own overhead and is the headline finding. Neither value is
ever inferred from the other or estimated; both come from separate clock reads at
separate points, on every request.

## Open-loop load generation

Arrivals follow a Poisson process at a target rate, and latency is measured from
each request's **scheduled** arrival time rather than from when the harness
managed to dispatch it.

A closed-loop harness -- each worker waiting for its response before sending
again -- lets offered load adapt to system speed, so queueing never accumulates
and the tail looks excellent. That failure has a name, **coordinated omission**,
and it would hide exactly the queueing this study exists to detect.

## Quick start

Four commands, from a Linux shell (WSL2 on this machine -- see
[`docs/machine-profile.md`](docs/machine-profile.md)):

```bash
git clone <this repo> && cd moodle-inference-bench
./scripts/bootstrap-venv.sh      # creates .venv, installs pinned deps
./scripts/smoke.sh               # ~30s end-to-end check of mock + harness
.venv/bin/python bench/validate_harness.py --duration 30
```

The last command is the phase 1 deliverable: it starts the mock, drives the
harness at every concurrency level in both streaming and non-streaming mode, and
reports pass or fail per level.

Requirements: Python 3.10+ and `curl`. `make` is optional -- the `Makefile`
wraps the same scripts, but every phase 1 workflow has a `scripts/` entry point
that does not need it.

The bootstrap script handles Debian and Ubuntu images that ship `python3`
without the `python3-venv` package by building the venv without pip and
bootstrapping pip from `get-pip.py`. That path needs no root.

## What phase 1 contains

```
bench/
  mock_server.py       OpenAI-compatible endpoint answering on a timer, with
                       configurable TTFT, inter-token delay and token count.
                       Streaming and non-streaming, both deterministic.
  harness.py           Async open-loop load generator. Poisson arrivals,
                       dual t1/t2 timing, one CSV row per request.
  validate_harness.py  Proves the harness against the mock at every
                       concurrency level before anything else is trusted.
  requirements.txt     Pinned.
```

Everything is `asyncio` on `aiohttp`. Not a thread per request: that model does
not hold up at concurrency 50.

### The harness reports its own saturation, loudly

If the harness ran out of capacity before the system under test did, it would
report its own ceiling as the system's result, and the number would look
perfectly plausible. So every run is checked against thresholds that are about
the instrument rather than about the study:

- **dispatch lag** -- how late each request went out relative to its scheduled
  arrival, recorded per request in `arrival_offset_ms`
- **event-loop lag** -- a background task times its own 5 ms sleeps, so a
  saturated event loop is visible directly rather than inferred
- **dropped requests** -- rows recorded must equal arrivals scheduled
- **schedule sanity** -- the arrival count must be plausible under Poisson noise

Crossing any of these prints a banner that is impossible to read past and, with
`--fail-on-saturation`, exits non-zero.

Note what is deliberately *not* treated as saturation: the realised arrival rate
differing from the target. Poisson counts have variance equal to their mean, so
over a short window that difference is the process working correctly. The
harness reports `schedule_sigma` instead, which is the number worth reading.

### Mapping the concurrency ladder onto an arrival rate

The methodology fixes a concurrency ladder, but open-loop generation is
parameterised by arrival rate. Little's law joins them:

```
target arrival rate = concurrency_target / expected end-to-end latency
```

So `--concurrency 10 --expected-latency-ms 410` gives a target rate of
24.39 req/s. The consequence worth keeping in mind is that `concurrency_target`
is an *offered* load, not an enforced one: if the system cannot keep up, requests
in flight rise above the target, and that rise is a result rather than a harness
setting.

## Validation results

Mock configured latency: TTFT 100 ms, inter-token 10 ms, 32 tokens, total
410 ms. 30 seconds per level, 12,814 requests in total. Tolerances were fixed
before the run: p50 within 5 ms, p95 within 15 ms, clock agreement within 2 ms.

Every delta column is milliseconds, measured minus configured.

```
conc mode    n    err%   rate tgt  disp p99  loop p99  mock p95  ttft p50  ttft p95  ttot p50  ttot p95  clk p95  verdict
-------------------------------------------------------------------------------------------------------------------------
   1 stream   79   0.00      2.44      1.88      1.52      2.37      3.30      4.44      3.82      5.25     0.02  PASS
   2 stream  160   0.00      4.88      1.67      1.58      2.22      3.41      4.61      3.73      5.17     0.02  PASS
   5 stream  364   0.00     12.20      1.98      1.65      1.56      3.10      4.45      3.34      4.59     0.02  PASS
  10 stream  731   0.00     24.39      1.98      1.75      1.45      2.73      3.95      2.93      4.25     0.01  PASS
  20 stream 1432   0.00     48.78      2.53      1.73      1.45      2.45      3.80      2.63      4.10     0.01  PASS
  50 stream 3641   0.00    121.95      2.39      1.99      1.37      2.19      3.89      2.32      3.97     0.01  PASS
   1 json     79   0.00      2.44      3.50      1.10      2.02       n/a       n/a      4.16      6.19     0.02  PASS
   2 json    160   0.00      4.88      2.22      1.34      1.85       n/a       n/a      4.59      6.77     0.03  PASS
   5 json    364   0.00     12.20      2.27      1.31      1.95       n/a       n/a      3.88      5.75     0.02  PASS
  10 json    731   0.00     24.39      2.24      1.44      1.97       n/a       n/a      4.08      5.65     0.02  PASS
  20 json   1432   0.00     48.78      2.21      1.55      1.82       n/a       n/a      3.82      5.35     0.02  PASS
  50 json   3641   0.00    121.95      2.66      1.82      1.58       n/a       n/a      3.36      5.22     0.02  PASS
```

Reading it:

- **`ttot p50` / `ttot p95`** are the headline: end-to-end latency measured at
  the HTTP boundary never sits more than 4.6 ms above the configured 410 ms at
  the median, or 6.8 ms at the 95th percentile, at any concurrency level. That
  residual is real HTTP and parsing cost, not drift: it does not grow with
  concurrency.
- **`mock p95`** is how far the mock's own server-side timing strayed from its
  configuration, at most 2.4 ms. That share of any `ttot` delta belongs to the
  mock rather than to the harness, which is why the mock reports it.
- **`clk p95`** is the p95 disagreement between `t1 - t2` and the separately
  recorded dispatch lag, at most 0.03 ms. `t1` and `t2` come from independent
  clock reads, so this says the two measurement points agree to well under a
  tenth of a millisecond. Since `t1 - t2` is the study's headline finding, this
  is the check that matters most.
- **`disp p99` and `loop p99`** stay under 3.5 ms and 2.0 ms. The harness was
  nowhere near its own ceiling, including at concurrency 50 and 121.95 req/s.
- **`ttft` is `n/a` in json mode** by design. A non-streaming response has no
  first-token event, so the column is left empty rather than filled with a copy
  of end-to-end latency.
- **Error rate was zero everywhere.**

Raw CSVs, per-level instrument metadata and the full report with every check,
measured value, expected value, delta and tolerance are committed under
[`results/raw/validation/`](results/raw/validation/).

### These numbers need a quiet machine, and that is a finding

Three earlier validation runs failed levels, and in every case the cause was
other work on the host rather than load from the benchmark. Filesystem commands
against the `/mnt/c` DrvFS mount, and repeated `wsl.exe` process launches
against a 4-processor VM, stalled the whole host: at the worst point the mock's
own timing drifted 245 ms and dispatch lag reached 1,031 ms.

Two things are worth taking from that. The saturation detection works, and it
named the cause rather than quietly returning plausible-looking numbers. And
methodology section 6's laptop controls -- no browser, no IDE, nothing else
running -- are not boilerplate on this machine. Arm A execution in phase 6 has
to honour them or the run is wasted.

## Standards this repo holds itself to

- **Every version pinned.** Python dependencies, and in later phases the Moodle
  version, image tags and model checksums. Reproducibility is the point.
- **Raw CSVs are committed.** Charts are derived artefacts, regenerated from
  those CSVs by a script in the repo so a reader can rerun or dispute them.
- **Percentiles, never bare averages.**
- **No fabricated sample results anywhere**, including in example output and
  documentation. Placeholder numbers survive into publication.
- **This README states plainly what has and has not been measured.**

## Repository layout

```
docs/           methodology (the specification), machine profile, results
plugin/edgellm/ the Moodle AI provider plugin (phase 3); mounts to
                <moodle>/ai/provider/edgellm
bench/          mock server, harness, validation, analysis, run configs
corpus/         frozen prompt corpus (phase 5)
results/raw/    committed raw CSVs
results/charts/ derived charts (phase 2)
scripts/        entry points that do not require make
```

The plugin lives in a subdirectory rather than at the repository root: this is a
study repo, not a plugin repo. The plugin carries measurement instrumentation and
is not intended to be installed as an ordinary provider plugin.

## Licence

MIT. See [LICENSE](LICENSE).
