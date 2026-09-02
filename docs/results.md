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

## What has not been measured

- **Q1, Moodle's AI subsystem overhead.** Needs the provider plugin (phase 3),
  the Docker environment (phase 4) and the Arm A execution matrix (phase 6).
  There is currently no Moodle in the measurement path at all, so no figure for
  `T1 - T2` in the methodology's sense exists.
- **Q2, CPU inference viability.** Arm B. Not started, and deliberately
  sequenced after Arm A.
- **Q3, output quality against a cloud baseline.** Not started.
- **Host telemetry.** The 1 Hz CPU utilisation, RAM, temperature and clock
  sampler required by methodology section 12 is a phase 6 deliverable and does
  not exist.

## Notes for when results do arrive

- Percentiles, never bare averages. p50, p95 and p99.
- Three repeats per configuration, median reported, and any configuration whose
  repeats vary by more than 10% flagged rather than averaged away.
- Charts are derived artefacts. They are regenerated from the committed raw CSVs
  by a script in the repository, so a reader can rerun or dispute them.
- The success criteria in methodology section 3 were fixed before any run and
  are not to be adjusted after seeing results.
