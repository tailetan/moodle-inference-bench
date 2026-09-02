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

**Phases 1 to 4 complete. Phases 5 and 6 not started.**

No study result exists yet. Read that sentence literally -- there is no model
anywhere, no prompt corpus, and no figure for Moodle's subsystem overhead. What
exists is a validated load harness, a deterministic mock backend, and a provider
plugin that has been driven end to end against that mock by hand.

Moodle is now in the measurement path, and both `T1` and `T2` are recorded
independently on every request. What has not happened is a measurement run:
no repeats, no concurrency ladder, no machine controls, no percentiles.

| Phase | | Status |
|---|---|---|
| 1 | Harness and instrument validation | Done |
| 2 | Analysis and plotting | Done |
| 3 | Provider paths: core `aiprovider_openai` plus `aiprovider_edgellm` | Done |
| 4 | Environment wiring (native, no Docker) | Done |
| 5 | Prompt corpus | Not started |
| 6 | Arm A execution | Not started |
| -- | Arm B (CPU inference viability) | Deliberately last |

[`docs/results.md`](docs/results.md) states what has and has not been measured in
more detail.

There is no Docker in this study. Moodle 5.2, PHP 8.3 and PostgreSQL run
natively on the host, which removes a container layer from between the harness
and the thing being measured. [`docs/environment.md`](docs/environment.md)
records that setup, along with two checks run against it before any phase work
started: core's own `aiprovider_openai` does drive our mock endpoint, and the
Moodle dev server's concurrency ceiling has been measured rather than guessed at.
The ceiling is real, it sits at the configured worker count, and raising that
count clears it for a trivial endpoint. Both results, and what remains unverified
about them, are in [`docs/environment.md`](docs/environment.md).

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
[`docs/machine-profile.md`](docs/machine-profile.md)). This needs no Moodle and
no model:

```bash
git clone <this repo> && cd moodle-inference-bench
./scripts/bootstrap-venv.sh      # creates .venv, installs pinned deps
./scripts/smoke.sh               # ~30s end-to-end check of mock + harness
.venv/bin/python bench/validate_harness.py --duration 30
```

The last command is the phase 1 deliverable: it starts the mock, drives the
harness at every concurrency level in both streaming and non-streaming mode, and
reports pass or fail per level.

### Driving Moodle

Four more, once a Moodle checkout exists. `make env` writes `.env`, which is the
only file to edit -- one line in it repoints the whole study between the mock, a
llama.cpp server, Ollama and a commercial API.

```bash
make env                # then edit .env: MOODLE_ROOT and BACKEND_ENDPOINT
make sync-plugin        # copy the plugin in and run Moodle's upgrade
make serve              # Moodle dev server with the benchmark worker count
make bench-setup        # point Moodle at the backend; prints a token for .env
```

`make bench-setup` widens Moodle's cURL security so a local endpoint is
reachable at all, creates the provider instances and opens the benchmark
endpoint. It records the **exact prior value** of everything it touches, so
`make bench-teardown` restores what was there rather than what the defaults
happen to be -- including unsetting a setting that had never been set.

**Run `make bench-teardown` when a run finishes.** Leaving it up leaves a way to
execute AI actions over HTTP and leaves the site's SSRF protection widened.

The mock server defaults to port 8090, not 8080: WSL2 runs every distribution in
one network namespace, and a Moodle dev server commonly holds 8080.

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

## Phase 3: the provider plugin

[`plugin/edgellm/`](plugin/edgellm/) holds `aiprovider_edgellm`, which mounts to
`<moodle>/ai/provider/edgellm` via
[`scripts/sync-plugin.sh`](scripts/sync-plugin.sh). Its own
[README](plugin/edgellm/README.md) covers the design.

Two things are worth pulling out here.

**`T1` and `T2` come from different places, by necessity.** `T2` is the HTTP
boundary, visible only to code inside a provider. `T1` is the whole core path,
and a provider sits below the manager so it cannot see it. So `bench.php` times
`\core_ai\manager::process_action()` from outside, and the provider records `T2`
from inside. Neither is derived from the other.

**Instrumentation writes nothing.** No database, no log, no file. A write inside
the request would land inside `T1` and inflate the number the study exists to
measure. The value is held in memory for the life of the request and read
afterwards.

Verified by hand against a running Moodle 5.2 with the mock as the backend: the
plugin installs, all three security gates on the benchmark endpoint hold, both
`summarise_text` and `generate_text` return `t1_total_ms` and `t2_model_ms`, and
a refused connection returns JSON with both timings still recorded. The PHPUnit
suite in `plugin/edgellm/tests/` runs **26 tests and 77 assertions, all
passing**, on Moodle 5.2.2+ with PHP 8.3.30 and PostgreSQL 16.13.

### A bug found in core along the way

Guzzle's `ConnectException` extends `TransferException` and is **not** a
`RequestException`. Core's `aiprovider_openai` catches only `RequestException`,
so a refused connection to a configured endpoint escapes as an uncaught
exception instead of becoming a handled AI error. That is the most likely
failure mode when pointing Moodle at a local runtime that is not running. This
plugin catches `GuzzleException` instead.

## Phase 2: analysis and plotting

`bench/analyse.py` reads raw CSVs and writes `results/summary.csv`,
`results/summary.json` and the charts in `results/charts/`. Charts are
regenerated from the raw rows on every run, never from the summary, so a reader
who disputes one can recompute it from committed data. Each chart carries a
footer naming the data it came from.

Three details that decide whether the output is trustworthy:

- **`t1 - t2` is computed per request, then summarised.** The p95 of a
  difference is not the difference of two p95s, and taking the second would
  quietly misreport the study's headline number.
- **A blank cell is not zero.** An empty `ttft_ms` means the value was not
  observable on that request, so it is excluded rather than averaged in as zero.
- **Repeats are compared, not merged.** A configuration whose repeats disagree
  by more than 10% is flagged with the actual spread. A configuration with one
  repeat is reported as unchecked rather than as consistent.

`bench/test_analyse.py` exercises all of that against constructed inputs, 28
checks in total, including the repeat-variance alarm in both directions -- it
has never fired on real data, and an alarm nobody has tested is not an alarm.

Run it with `make analyse` and `make test-analyse`.

## Phase 4: the environment, and what it revealed

There is no Docker. Moodle 5.2, PHP 8.3 and PostgreSQL run natively, which
removes a container layer from between the harness and the thing being measured.
[`docs/environment.md`](docs/environment.md) records the setup.

The substantive result of this phase was a measurement, not a script.
`scripts/measure_ceiling.py` drives Moodle's benchmark endpoint through the
study's own harness and walks the Arm A ladder:

| Concurrency | t1 p50 | t1 - t2 p50 | t1 - t2 p95 | harness wall p50 |
|---|---|---|---|---|
| 1 | 422.9 | 9.15 | 14.99 | 451.6 |
| 2 | 421.6 | 8.31 | 13.02 | 447.8 |
| 5 | 419.9 | 6.71 | 14.18 | 440.1 |
| 10 | 460.6 | 35.57 | 168.66 | 583.4 |
| 20 | 506.3 | 81.24 | 335.54 | 1142.1 |
| 50 | 510.5 | 85.53 | 217.26 | 6544.5 |

Milliseconds, zero errors throughout.

`t1 - t2` climbing from 7 ms to 85 ms looks exactly like the study's prediction 2
confirmed. **It is not.** Sampling `/proc/stat` during a concurrency-20 run gives
96.9% CPU busy at the median across the guest's 4 cores: above concurrency 5 the
machine has no idle capacity, so that rise is PHP and PostgreSQL competing for
cores rather than Moodle's AI subsystem doing more work.

This also corrects the earlier conclusion from the trivial-endpoint spike, which
suggested raising the worker count would clear the ceiling. That endpoint only
slept, so it never used the CPU. With real Moodle, more workers cannot help and
neither would php-fpm.

Methodology revision R2 splits the ladder in response: 1, 2 and 5 yield an
overhead figure; 10, 20 and 50 are still run and published, but as a capacity
result rather than an overhead one. **Prediction 2 cannot be honestly tested on
this machine as configured**, and saying so is more useful than reporting an
artefact that happens to agree with it.

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
docs/           methodology (the specification), machine profile,
                environment, results
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
