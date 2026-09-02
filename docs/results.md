# Results

Nothing in this file is a study result yet.

Phase 1 builds and validates the measuring instrument. It produces timings, but
they are timings of a mock endpoint answering on a timer, driven by a harness on
the same laptop. They say something about the harness and nothing about Moodle,
about any model, or about CPU inference.

## What has been measured

**The instrument.** `bench/validate_harness.py` drives the harness against
`bench/mock_server.py` at every rung of the Arm A concurrency ladder
(1, 2, 5, 10, 20, 50) in both streaming and non-streaming mode, and checks that
the latencies the harness reports match the latencies the mock was configured to
produce.

Raw output is committed under [`../results/raw/validation/`](../results/raw/validation/):
one CSV per level in the schema from methodology section 12, a `.meta.json`
sidecar per level describing the instrument's own behaviour during that run, and
`validation_report.json` holding every check with its measured value, expected
value, delta and tolerance.

The validation summary table produced by that script is reproduced in the
[README](../README.md).

**The analysis pipeline.** `bench/analyse.py` reads those raw CSVs and produces
[`../results/summary.csv`](../results/summary.csv),
[`../results/summary.json`](../results/summary.json) and the charts in
[`../results/charts/`](../results/charts/). Everything is regenerated from the
raw rows on every run, never from the summary, so any chart can be recomputed
from committed data.

The numbers it currently reports are the instrument's, not the study's: every
row came from the mock backend, so `t1 - t2` there is the harness's own
scheduling delay rather than Moodle's overhead. `analyse.py` says so itself when
every row has `runtime=mock`, rather than relying on the reader to notice.

`bench/test_analyse.py` checks the analysis code against constructed inputs: 28
checks covering percentiles, blank cells not being treated as zero, `t1 - t2`
being computed per request rather than by subtracting two percentiles, errors
being excluded from latency but counted in the error rate, the 10% repeat
variance flag firing in both directions, and configurations that differ in more
than concurrency not being drawn as one line.

## What has not been measured

- **Q1, Moodle's AI subsystem overhead.** The provider plugin and the benchmark
  driver endpoint exist (phase 3), and Moodle has been driven end to end against
  the mock by hand, so `T1` and `T2` are both recorded per request. What does not
  exist is a measurement run: the environment wiring (phase 4), the prompt corpus
  (phase 5) and the Arm A execution matrix (phase 6) are all still to come. **No
  figure for `T1 - T2` in the methodology's sense exists**, because no run has
  been made with repeats, a concurrency ladder or the section 6 machine controls
  in force.
- **Q2, CPU inference viability.** Arm B. Not started, and deliberately
  sequenced after Arm A.
- **Q3, output quality against a cloud baseline.** Not started.
- **Host telemetry.** The 1 Hz CPU utilisation, RAM, temperature and clock
  sampler required by methodology section 12 is a phase 6 deliverable and does
  not exist.

## Notes for when results do arrive

- Percentiles, never bare averages. p50, p95 and p99. `analyse.py` reports the
  mean only for sustained output rate, where the methodology asks for it, and
  always alongside the median.
- Three repeats per configuration, median reported, and any configuration whose
  repeats vary by more than 10% flagged rather than averaged away. Every
  configuration recorded so far has a single repeat, so `analyse.py` says the
  check could not be applied rather than reporting agreement it did not test.
- Charts are derived artefacts. They are regenerated from the committed raw CSVs
  by a script in the repository, so a reader can rerun or dispute them.
- The success criteria in methodology section 3 were fixed before any run and
  are not to be adjusted after seeing results.
