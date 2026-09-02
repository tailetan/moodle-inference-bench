# AI inference on commodity hardware for Moodle: benchmark methodology

Version 2. Supersedes the GPU-scoped draft.

Scope: vanilla Moodle core, no institutional customisation, no GPU. Every input is public and every measurement is reproducible on an ordinary developer laptop.

## 0. Why this scope is the right one

Version 1 of this document assumed a GPU server and asked how many concurrent users it could serve. That study needs hardware, and it answers a question only well-resourced institutions can act on.

This version asks two questions that are cheaper to answer and, arguably, more useful:

- Most Moodle sites have no GPU and never will. Whether they can use the AI subsystem at all is unanswered.
- Moodle's own contribution to AI request latency has never been published, and isolating it turns out to need no model at all.

There is also a scope honesty point. The KPI topic is edge AI, meaning inference near the user rather than in a data centre. A model running on commodity CPU hardware sits genuinely at the edge of that spectrum. A rented data-centre GPU does not.

## 1. What this study answers

**Q1 - What latency does Moodle's AI subsystem add, and how does it behave under load?**

Measured as `T1 - T2` against a mock endpoint of known, fixed latency. No model involved. Unpublished by anyone as far as we can establish.

**Q2 - Which Moodle AI actions are viable on commodity hardware with no GPU?**

Measured per action, against fixed budgets stated in section 3. The expected answer is "some of them", and knowing which is directly actionable for a site administrator.

**Q3 - How much output quality is given up against a cloud baseline?** *(secondary)*

Small indicative sample, scoped in section 9.

Q1 is the contribution. Q2 is the practical finding. Q3 exists so Q2 means something, since the fastest configuration is always the least capable one.

## 2. Two arms

The study splits into two independent arms with different constraints. Keeping them separate is what makes the whole thing feasible without a GPU.

| | Arm A: Subsystem overhead | Arm B: CPU inference viability |
|---|---|---|
| Answers | Q1 | Q2, Q3 |
| Backend | Mock endpoint, deterministic latency | Real model on CPU |
| Concurrency ladder | 1, 2, 5, 10, 20, 50 | 1, 2, 4 |
| Run cost | Seconds of CPU | Minutes per request |
| Limiting factor | Nothing meaningful | Wall-clock time |

**Arm A carries the full concurrency ladder** precisely because there is no model. The mock returns on a timer, so fifty simultaneous requests cost almost nothing. This means the load-related part of Q1, whether Moodle's overhead grows under concurrency, is fully answerable on a laptop. That was not obvious and it is the reason this study survives the loss of a GPU.

**Arm B is deliberately shallow on concurrency.** CPU inference does not batch the way GPU serving does, so concurrency beyond a handful measures thread contention rather than anything useful. Arm B answers a viability question, not a scaling one.

## 3. Success criteria

Fixed before any run. Adjusting them after seeing results would make the study worthless.

| Metric | Budget | Applies to |
|---|---|---|
| Time to first token, p95 | <= 1.0 s | Arm B, all workloads |
| Sustained output rate | >= 10 tokens/s | Arm B, all workloads |
| Summarise end-to-end, p95 | <= 8 s | Arm B, workload A |
| Subsystem overhead (`T1 - T2`), p95 | <= 100 ms | Arm A |
| Error rate | < 1% | Both |

Report a configuration as **passing** only if it meets every applicable budget. Judge on p95, not the mean.

The 100 ms overhead budget is a judgement, not a standard. It is set at the point where core's contribution would start to matter against a 1 s TTFT budget. If measured overhead lands far below it, say so plainly and move on; that is a useful null result.

## 4. Predictions

Written before running anything, so the study can be wrong about something.

1. **Subsystem overhead will be small at low concurrency**, in the tens of milliseconds, and will be dominated by logging and capability checks rather than by request construction.
2. **Overhead will grow with concurrency**, because PHP request handling and synchronous logging writes contend. If it does not, that is a genuine finding about core's efficiency.
3. **`generate_text` will pass on CPU with a 3B model** and be marginal with 8B.
4. **`summarise_text` will fail on CPU for long inputs**, badly. Prefill on CPU is the weak point: an 8,000-token page must be processed in full before the first token appears, and CPU compute is the wrong shape for that work.
5. **Thread count will show a peak rather than a monotone improvement**, with performance degrading past the physical core count.

Prediction 4 is the one worth the article. If it holds, the finding is that a GPU-less Moodle site can generate but cannot summarise, which maps directly onto two of core's actions and is immediately actionable.

## 5. Workload model

Moodle's AI actions have opposite performance profiles. Averaging them produces a number describing neither.

| Workload | Input | Output | Bound by | Maps to |
|---|---|---|---|---|
| A. Summarise | 2,000-8,000 tokens | 150-300 tokens | Prefill / compute | `summarise_text` |
| B. Explain | 100-400 tokens | 200-600 tokens | Mixed | `explain_text` |
| C. Generate | 20-100 tokens | 400-1,200 tokens | Decode / bandwidth | `generate_text` |

On CPU this split matters more than it does on GPU, not less. Decode is bounded by memory bandwidth, which commodity hardware has some of. Prefill is bounded by compute, which is exactly what a CPU lacks relative to a GPU. Expect the gap between workload A and workload C to be far wider than any GPU study would show.

Workload A additionally needs an input-length sweep, since the whole question is where prefill becomes intolerable. Run it at 1k, 2k, 4k and 8k input tokens rather than as a single bucket.

**Prompt corpus.** 50 fixed prompts per workload, built from public Moodle content: Mount Orange demo courses, Moodle Docs pages, and core's own test data generators. Nothing institution-specific, nothing learner-authored. Freeze it, commit it, checksum it.

Using public content costs a little realism and buys reproducibility: any reader can run the same corpus and compare directly.

**Prompts must not share a long common prefix.** Runtimes cache KV state for shared prefixes, which collapses prefill cost and turns the benchmark into a measurement of cache hits. Vary the openings deliberately and document that you did.

## 6. Variables

**Independent**

- *Arm A*: concurrency (1, 2, 5, 10, 20, 50); mock latency setting (fast and slow, to separate fixed from proportional overhead)
- *Arm B*: model size (3B, 8B); quantisation (4-bit, 8-bit); runtime (llama.cpp direct, Ollama); CPU thread count (4, 8, physical core count, all logical cores); input length for workload A

Thread count is a genuine variable on CPU, not a tuning detail. It is cheap to sweep and likely to produce a non-obvious result.

**Dependent**

- `t1_total_ms`, `t2_model_ms`, and their difference
- Time to first token
- Inter-token latency, tokens per second
- Host RAM peak, CPU utilisation, CPU package temperature and clock
- Error and timeout counts

**Controlled**

- Temperature 0, fixed seed where the runtime supports it
- Identical `max_tokens` and system prompt across arms
- Model warm: loaded, ten throwaway requests, then record
- Moodle version pinned; vanilla core with no third-party plugins beyond the provider under test
- **Laptop-specific controls**, which matter more here than any of the above:
  - Mains power, not battery. Power profile set to maximum performance and recorded.
  - No browser, IDE, containers or sync clients running beyond those under test.
  - WSL2 memory cap set explicitly in `.wslconfig` and recorded.
  - Machine at room temperature and idle for five minutes before each run.

That last block is the difference between a laptop study people trust and one they dismiss. Document the machine state as carefully as the software versions.

## 7. Where you measure

Two boundaries, logged independently on every request:

```
[Placement] -> [core_ai manager] -> [aiprovider_edgellm] -> HTTP -> [runtime or mock]
               |                                                 |
               T1                                                T2
```

- **T2** is backend latency. In Arm A this is a value you configured, which is what makes the arm work.
- **T1** is what a user experiences: manager policy checks, logging, provider request construction, PHP HTTP client, response parsing.

`T1 - T2` is Moodle's own overhead.

Never infer one from the other. Never estimate either. Both are recorded per request or the arm is invalid.

> **Amended 2 September 2026.** This section originally assumed a single
> measurement path through a purpose-built provider plugin. It now uses three
> paths, one of which contains no code of ours at all. See revision R1.



In Arm A, because T2 is known exactly rather than measured noisily, the subtraction is clean. This is the one respect in which having no GPU produces a *better* experiment than having one: there is no thermal drift, no scheduler variance and no batching behaviour to confound the result.

## 8. Load generation

**Open-loop, in both arms.** Arrivals follow a Poisson process at a target rate, and latency is measured from the *scheduled* arrival time, not from when the harness managed to dispatch.

A closed-loop harness, where each worker waits for its response before sending again, lets offered load adapt to system speed. Queueing never accumulates and tail latencies look excellent. The failure has a name, **coordinated omission**: the harness omits exactly the slow measurements you needed.

Run duration:

- *Arm A*: 5 minutes per configuration. Cheap, so no reason to skimp.
- *Arm B*: 10 minutes minimum, because CPU thermal throttling on a laptop typically appears after three to five minutes and a shorter run will miss it entirely.

Three repeats per configuration. Randomise order across configurations so machine warm-up does not correlate with variable order. Report the median; flag any configuration whose repeats vary by more than 10% rather than averaging it away. On a laptop, expect to hit that flag, and treat variance as a finding rather than noise to be smoothed.

## 9. Quality evaluation

Kept deliberately small. Speed numbers without a quality axis get dismissed, because the fastest configuration is always the smallest model.

- 30 outputs per workload per model configuration, scored against the cloud baseline output.
- Rubric with three dimensions: factual accuracy against the source text, instruction adherence, fluency. Scored 1 to 5.
- LLM judge for the first pass, human spot-check of 20%, with the judge-human agreement rate reported.

State the sample size everywhere it appears. This is an indicative signal, not an eval suite, and saying so protects the credibility of the latency numbers, which *are* measured properly.

## 10. Cloud baseline

Run the identical corpus against a commercial API through the same provider code path, changing only the endpoint. This controls for the harness's own overhead.

Record network round-trip time separately. The test host is in Vietnam; if the API terminates in the US or EU, geography contributes to the baseline and must be broken out rather than left to inflate the cloud arm.

## 11. Cost model

Simpler than the GPU version, and more favourable, which is worth stating explicitly rather than quietly.

```
Self-hosted cost per 1,000 requests
  = (electricity: package power x hours x rate / requests)
  + (ops: hours per month x loaded rate / requests per month)
```

Hardware capital cost is zero, because the premise is hardware the institution already owns. That makes the crossover volume far lower than in a GPU deployment, and the honest counterweight is that capacity is far lower too. Report both together or the comparison is misleading.

Cloud cost comes from measured token counts times published pricing. Pull prices on the day of writing and cite the date.

Include operations labour. Omitting it is how self-hosting studies produce numbers that do not survive deployment.

## 12. Results schema

One row per request:

```
run_id, timestamp, arm, config_id, model, quant, runtime, threads,
workload, input_bucket, prompt_id, concurrency_target, arrival_offset_ms,
t1_total_ms, t2_model_ms, ttft_ms, output_tokens, input_tokens,
tokens_per_sec, status, error_type
```

Host telemetry sampled at 1 s to a separate file, joined on `run_id` and timestamp:

```
run_id, timestamp, cpu_util_pct, host_ram_mb, cpu_temp_c, cpu_clock_mhz, power_profile
```

Commit raw CSVs. Charts are derived artefacts regenerated by a script in the repo, so a reader can rerun or dispute the analysis.

## 13. Threats to validity

State these in the write-up rather than waiting for a reviewer.

- **Single machine, and a laptop at that.** Results describe one CPU under one thermal design. Publish the exact model, core count, RAM speed and power profile, and give the bandwidth-over-model-size rule of thumb so readers can extrapolate.
- **Thermal throttling is a first-class effect here, not an edge case.** Sustained CPU inference will throttle. Report the shape of the degradation rather than only steady-state figures.
- **Arm A's overhead figures depend on a mock, not a real backend.** A real runtime might interact with core differently, for instance through streaming behaviour. State this and, if any GPU access ever becomes available, spot-check one configuration against it.
- **No concurrency scaling result.** This study cannot say how many users a GPU deployment would serve. Do not let readers infer it.
- **Demo content is cleaner than production content.** Real courses carry messy HTML, mixed languages and pasted PDF text, all of which change token counts and prefill cost.
- **Quality sample of 30 detects large differences and misses small ones.**
- **Background system activity on a shared workstation** is controlled by procedure, not by isolation. A managed corporate laptop may run agents you do not control. Record what was running.

## 14. Out of scope

- GPU serving, continuous batching, and concurrency scaling beyond four
- Fine-tuning, distillation, pruning
- Image generation
- Multi-node deployment
- Browser or WebGPU inference
- Security review of the runtime

## 15. Sequence

1. Agree this document.
2. Build the mock server and load harness. Validate the harness against the mock at every concurrency level in section 2. **No model involved, no hardware needed.**
3. Build the prompt corpus and freeze it.
4. Write `aiprovider_edgellm` with T1/T2 instrumentation, and configure core's own `aiprovider_openai` against the mock as the primary measurement path. See revision R1.
5. **Run Arm A in full.** It is fast, it needs no model, and it answers the novel question. If everything else stalls, this alone is a publishable result.
6. Pilot Arm B at concurrency 1 with the 3B model only. Revise this document based on what it shows.
7. Run Arm B in full.
8. Quality scoring, cost model, analysis, write-up.

Step 5 before step 6 is deliberate. It front-loads the contribution and de-risks the KPI: the study produces something publishable before the slow, thermally awkward part begins.

Step 6 will change your budgets and your input-length buckets. Revise this document openly when it does, rather than quietly.

---

## Implementation notes

These record decisions taken while building the instrument that section 15 step 2 calls for. They resolve gaps in the text above rather than changing it. Anything that would change the design goes back into the numbered sections, not here.

### Mapping the concurrency ladder onto an arrival rate

Section 2 fixes a concurrency ladder; section 8 requires open-loop generation, which is parameterised by arrival rate rather than by concurrency. The two are joined by Little's law:

```
target arrival rate = concurrency_target / expected end-to-end latency
```

So concurrency 10 against a backend configured for 410 ms means a target arrival rate of 24.39 req/s. The harness takes `--concurrency` and `--expected-latency-ms` and derives the rate, recording the concurrency figure in the `concurrency_target` column.

The important consequence: `concurrency_target` is an *offered* load, not an enforced one. If the system under test cannot keep up, the number of requests in flight rises above the target. That rise is the queueing this study exists to detect, and suppressing it would be the closed-loop mistake section 8 rejects.

### Poisson arrival counts are not a harness fault

The number of arrivals a run generates is a random variable with variance equal to its mean. Over a short window the realised rate visibly differs from the target, and that is the process behaving correctly. The harness therefore reports `schedule_sigma`, the deviation in standard deviations, and only flags a schedule that is implausible under Poisson noise. Whether the harness kept up is a separate question answered by dispatch lag.

### What t1 and t2 mean before Moodle exists

Section 7 defines T1 at the core AI manager and T2 at the provider's HTTP boundary. In phase 1 there is no Moodle in the path, so the harness records:

- `t2_model_ms` at the HTTP boundary, which is the same boundary the provider plugin will instrument in phase 3
- `t1_total_ms` from the request's scheduled arrival instant

`t1 - t2` in phase 1 is therefore the harness's own scheduling delay rather than Moodle's overhead, and it should be close to zero. This makes it a validation signal: the two measurement points are taken from independent clock reads, and if their difference does not match the separately recorded dispatch lag, one of them is wrong.

### Columns that are left empty rather than estimated

Section 7's rule -- never infer one measurement from another, never estimate
either -- is applied to the whole schema, not only to T1 and T2.

On a non-streaming response there is no first-token event and no observable
decode window, so `ttft_ms` and `tokens_per_sec` are written empty. They are not
filled with a copy of `t1_total_ms`, and `tokens_per_sec` is not computed over a
zero-width interval. End-to-end latency is already recorded in `t1_total_ms`, so
nothing is lost.

This matters for the plugin in phase 3: Moodle's AI providers return complete
text rather than a stream, so any run driven through Moodle will have both
columns empty by construction. Sustained output rate, which methodology section
3 sets a budget against, is therefore measurable only by driving the runtime
directly. Arm B has to do both, and the write-up must not mix the two.

### Mock latency profiles

Three named profiles are defined in `bench/mock_server.py`. `fast` and `slow` are the two Arm A settings section 6 requires; `mid` exists only to keep instrument validation quick and is not an Arm A configuration.

| Profile | TTFT | Inter-token | Tokens | Configured total |
|---|---|---|---|---|
| `fast` | 50 ms | 5 ms | 32 | 205 ms |
| `mid` | 100 ms | 10 ms | 32 | 410 ms |
| `slow` | 800 ms | 25 ms | 200 | 5,775 ms |

These are configuration points chosen to separate a fixed per-request overhead in Moodle from one that scales with backend latency. They are not measurements of any model and must not be presented as such.


---

## Revisions

Changes to the design after version 2 was agreed. Section 15 requires these to be
made openly rather than quietly, so each one states what changed, what prompted
it, and what it costs.

### R1. Measure through core's own provider, not only through ours

*2 September 2026. Amends sections 7 and 15 step 4. Prompted by an inspection of
the Moodle 5.2 checkout on the test host.*

**What prompted it.** Moodle 5.2 ships six AI providers in `public/ai/provider/`:
`awsbedrock`, `azureai`, `deepseek`, `gemini`, `ollama` and `openai`. Two of them
take an arbitrary endpoint URL from configuration, with no core modification
required:

| Provider | How the endpoint is set |
|---|---|
| `aiprovider_ollama` | `config['endpoint']`, an admin form field of type `PARAM_URL`, default `http://localhost:11434` |
| `aiprovider_openai` | `actionconfig[<action>]['settings']['endpoint']`, configurable per action |

**The problem this exposes.** The original plan measured `T1` only through
`aiprovider_edgellm`, a plugin written for this study. Any figure produced that
way is open to an obvious objection: that the overhead measured is the study
plugin's, not Moodle's. The study's headline claim would rest on code nobody else
runs.

**The change.** Arm A is measured over three paths rather than one.

| Path | Provider | Backend | Supplies |
|---|---|---|---|
| A1 | Core `aiprovider_openai`, unmodified | Mock | `T1`. No code of ours in the path, so the overhead is attributable to core. |
| A2 | `aiprovider_edgellm` | Mock | `T2` at the plugin's HTTP boundary, and a second `T1` for comparison. |
| B1 | Core `aiprovider_ollama`, unmodified | Real runtime | Arm B, with no custom plugin at all. |

A1 is the primary result. A2 exists because `T2` at the provider's HTTP boundary
cannot be recorded without code inside a provider, and because comparing its `T1`
against A1's tests whether our plugin distorts the measurement. **If A1 and A2
disagree, that disagreement is a reported finding, not a nuisance to be averaged
away.**

**What this costs.** `aiprovider_edgellm` stays, but shrinks: it exists for
instrumentation and for pointing at an arbitrary OpenAI-compatible endpoint, not
as a general-purpose provider. Less code than the original plan, not more.

**What is not yet verified.** Two assumptions behind this revision are read off
the source and have not been executed:

1. That `aiprovider_openai` functions against a non-OpenAI endpoint. It may send
   authentication headers, or validate response fields the mock does not yet
   return.
2. That the mock can stand in for `aiprovider_ollama`. That provider speaks the
   Ollama API shape rather than `/v1/chat/completions`, so the mock needs an
   Ollama-shaped route added before path B1 can be exercised against it.

Both are phase 3 checks. If either fails, this revision is amended again rather
than quietly worked around.

**What did not change.** The reasoning for keeping Moodle in the measurement path
at all, which core's built-in AI support strengthens rather than weakens: Q1 is
definitionally a question about Moodle, and core shipping a configurable Ollama
provider means "Moodle against a local CPU model" is now a supported
configuration a site administrator can enable today. Whether it is usable without
a GPU is exactly Q2, and it remains unpublished.
