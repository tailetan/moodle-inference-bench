# Machine profile

The single machine every number in this repository comes from. Section 13 of
[methodology.md](methodology.md) names "single machine, and a laptop at that" as
the first threat to validity, so this file is part of the results, not
housekeeping.

Recorded 2 September 2026. Re-record and commit this file if anything below
changes.

## Host

| | |
|---|---|
| Machine | Lenovo, machine type `21AJS18000` |
| CPU | 12th Gen Intel Core i5-1235U |
| Cores | 10 physical (hybrid: performance + efficient), 12 logical threads |
| Base clock reported by the OS | 1300 MHz |
| RAM | 31.71 GB, 2 modules at 3200 MT/s |
| OS | Windows 11 Pro, build 10.0.26200.9106 |

The i5-1235U is a hybrid part. Its physical cores are not interchangeable: a
thread on a performance core and a thread on an efficient core do materially
different amounts of work per cycle. Arm B's thread-count sweep has to be read
with that in mind, and "physical core count" is not a single meaningful number
on this CPU.

## WSL2

Everything in `bench/` runs inside WSL2. Both distributions present are Ubuntu
22.04.5 LTS with Python 3.10.12.

| | |
|---|---|
| WSL version | 2.6.3.0 |
| Kernel | 6.6.87.2-1 |
| Distribution used | Ubuntu 22.04.5 LTS |
| Python | 3.10.12 |

`%USERPROFILE%\.wslconfig`, pinned:

```ini
[wsl2]
memory=16GB
swap=8GB
processors=4
```

Two consequences worth stating before they surprise anyone:

- **The guest sees 4 processors, not 12.** Arm B's thread-count sweep in section
  6 of the methodology lists 4, 8, physical core count and all logical cores.
  Three of those four values are unreachable from inside WSL2 under this
  `.wslconfig`. Either the cap is raised for Arm B and re-recorded here, or the
  sweep is cut down and the methodology is revised openly to say so. This is an
  Arm B decision and is not resolved yet.
- **The guest sees 16 GB of the host's 32 GB.** That is ample for Arm A and for
  a quantised 3B or 8B model, so it does not constrain the study as planned.

The repository is checked out on the Windows filesystem and reached from WSL2
through `/mnt/c`, which is a DrvFS mount. This is slow for process startup and
for interpreter imports -- starting the mock server takes seconds rather than
milliseconds -- but it is not inside any measured interval. Results CSVs are
buffered in memory and written once at the end of a run for the same reason.

## Environment state during phase 1

Recorded honestly rather than aspirationally. Phase 1 validates the instrument;
it is not a measurement run, so the section 6 laptop controls were not all in
force.

| Control from methodology section 6 | State during phase 1 validation |
|---|---|
| Mains power, not battery | Met. On AC, battery at 100%. |
| Power profile at maximum performance | **Not met.** Windows power scheme was Balanced (`381b4222-f694-41f0-9685-ff5bb260df2e`). |
| No browser, IDE, containers or sync clients running | **Not met.** An IDE was open. |
| WSL2 memory cap set explicitly and recorded | Met, see above. |
| Machine idle and at room temperature for five minutes beforehand | **Not met.** |

None of this invalidates phase 1: the validation asks whether the harness
reproduces a latency the mock was configured to produce, and a busier machine
makes that a harder test, not an easier one. It does mean the phase 1 timings
must not be quoted as performance figures.

**Before Arm A execution (phase 6), all five controls must be met and this
table re-recorded.** Set the power scheme to High performance or Ultimate
performance and note the GUID actually in force.

## Not yet recorded

Deliberately empty rather than guessed at:

- CPU package temperature and clock under load. The telemetry sampler that
  section 12 requires is a phase 6 deliverable and does not exist yet.
- Docker Desktop version. Docker is not installed on this host, which phase 4
  will need to resolve.
- Moodle version, image tags, model files and checksums. Phases 3 to 5.

## Sensitivity to background activity, observed

Phase 1 validation was run four times. The first and last passed every level;
the two in between failed levels, and in both cases the cause was other work on
the host rather than load from the benchmark.

| Run | Host activity during the run | Result |
|---|---|---|
| 1 | Occasional log reads | 12 of 12 passed |
| 2 | `find` and `rm` against the `/mnt/c` DrvFS mount | 3 levels failed |
| 3 | Two watchers each launching `wsl.exe` every 25-30 s | 2 levels failed |
| 4 | Nothing else running | 12 of 12 passed |

The failures were not a concurrency ceiling: run 2 failed at concurrency 10 and
20 while passing at 50. In run 3 the worst level recorded a dispatch lag of
1,031 ms and the mock's own server-side timing drifted 245 ms, so the stall hit
both processes at once, which only a host-wide event can do.

Two consequences:

- The saturation detection in `bench/harness.py` earns its place. It failed the
  affected runs loudly and named the cause, instead of returning numbers that
  would have looked plausible.
- On a 4-processor WSL2 VM, launching `wsl.exe` repeatedly is itself enough
  interference to invalidate a run. Whatever watches an Arm A run in phase 6
  must not do that.
