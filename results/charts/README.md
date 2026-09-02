# Charts

Derived artefacts. Every file here is regenerated from the raw CSVs in
`../raw/` by `bench/analyse.py`, never from the summary, so a reader who
disputes a chart can recompute it from committed data:

```bash
make analyse
# or
.venv/bin/python bench/analyse.py results/raw/validation --out-dir results
```

Each chart carries a footer naming the data it was drawn from.

## What is currently here is not a study result

Every chart in this directory was produced from the **phase 1 instrument
validation** runs, where the backend was the deterministic mock. So `t1 - t2`
here is the harness's own scheduling delay, not Moodle's subsystem overhead, and
the latency shown is a number the mock was configured to produce.

They exist to show the analysis pipeline works end to end. Replace them with
Arm A output in phase 6, and do not quote them before then.

## A note on the distribution chart

`overhead_distribution.png` clips its x-axis so the shape of the distribution is
legible, because a handful of slow requests would otherwise compress every curve
into a single vertical stroke. The chart states the clip point, how many
requests lie beyond it, and how far out they go. Clipping the view is not the
same as discarding data, and the difference is kept visible.
