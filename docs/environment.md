# Environment: no Docker

Phase 4 in the original build plan called for `docker-compose.yml` bringing up
Moodle and PostgreSQL. That is not how this study runs. **Docker Desktop is not
installed on this machine and is not to be installed.** Everything runs natively
on Windows and WSL2.

This is not a workaround. The environment the study needs already exists on the
host, and using it removes a container layer from between the harness and the
thing being measured, which for a latency study is a benefit rather than a cost.

## What is already here

| Piece | Where | Detail |
|---|---|---|
| Moodle | WSL2 `Ubuntu-22.04`, `~/workspace/moodle` | 5.2.2+, git `v5.2.0-551-gb07dd04b40e`, docroot `public/` |
| PHP | same distro | 8.3.30 CLI, with `pgsql curl intl mbstring soap sodium xml zip gd` |
| Composer | same distro | 2.9.7 |
| Database | reachable on `localhost:5432` | PostgreSQL, database `moodle`, user `moodleuser` |
| Web server | `~/workspace/moodle-serve.sh` | PHP built-in server, `public/` docroot, port 8080 |
| Benchmark harness | WSL2 `Ubuntu-22.04-loopos` | Python 3.10 venv, see the repository README |

A separate PostgreSQL 16 is installed on Windows as service
`postgresql-x64-16`, but that service is stopped and Moodle does not use it.

## The two distributions share one network

WSL2 runs every distribution in a single utility VM with one network namespace.
Both distributions report the same addresses, so the harness in
`Ubuntu-22.04-loopos` reaches the Moodle server in `Ubuntu-22.04` at
`http://localhost:8080` with no bridging, no port forwarding and no containers.

That is what makes phase 6 possible without Docker. It also has a consequence
worth remembering: **ports collide across distributions.** Moodle holds 8080, so
the mock server defaults to 8090.

## Open problem: the dev server caps concurrency at 8

`moodle-serve.sh` starts PHP's built-in server with `PHP_CLI_SERVER_WORKERS=8`.
That is eight concurrent PHP requests, and the Arm A concurrency ladder goes to
fifty.

This is not a performance inconvenience, it is a correctness threat to the
study's headline finding. Beyond eight concurrent requests, arrivals queue in
the web server *before* Moodle's AI subsystem sees them. That queueing lands
inside `t1` and not inside `t2`, so it would be reported as Moodle subsystem
overhead when it is really dev-server saturation. The result would look like
prediction 2 in methodology section 4 coming true, and it would be an artefact.

It has to be resolved before Arm A executes. Options, not yet chosen:

1. **Raise the worker count** well above the top of the ladder. Cheapest, but
   PHP's built-in server is explicitly not built for concurrency and may not
   scale cleanly even so.
2. **Run php-fpm behind nginx or Apache** in the Moodle distribution. Closest to
   how a real site runs, which also makes the result more transferable. Needs
   root in that distribution.
3. **Cap the Arm A ladder** at what the server genuinely sustains and revise the
   methodology openly to say why.

Whichever is chosen, the fix must be verified rather than assumed: drive the
chosen stack with the harness against a trivial endpoint and confirm that
latency does not inflect at the worker count. The harness already reports the
saturation signals needed to see it.

## Core already ships AI providers

`public/ai/provider/` in this checkout contains `awsbedrock`, `azureai`,
`deepseek`, `gemini`, `ollama` and `openai`. Core shipping an Ollama provider
changes the framing of phase 3, though not its purpose: `aiprovider_edgellm`
exists to carry T1/T2 measurement instrumentation and to point at an arbitrary
OpenAI-compatible endpoint, which is what Arm A needs and what a shipping
provider deliberately does not do. Worth deciding, before phase 3 starts,
whether to also spot-check against core's own provider so the overhead figure is
not specific to our plugin.

## Verified: core's own provider can drive the mock

Run on 2 September 2026 by `scripts/spike_core_provider.php`, which creates an
unmodified `aiprovider_openai` instance pointed at the mock, runs one real
`summarise_text` through `\core_ai\manager::process_action()`, and removes
everything afterwards. It passed: core reached the mock and parsed the response.

This is what makes path A1 in methodology revision R1 real rather than proposed.

Three practical points came out of it.

**cURL security blocks the request by default.** `core\http_client` enforces
`curlsecurityblockedhosts` (which lists `127.0.0.0/8` and `localhost`) and
`curlsecurityallowedport` (which permits only 80 and 443). Both must be widened
before any local endpoint is reachable, and both are now recorded as controlled
variables in the methodology. The spike widens them at the start and restores
them at the end, so it leaves no lasting change.

**The mock had to return `system_fingerprint`.** Core reads that field
unconditionally. It now returns a fixed value.

**The two WSL2 distributions share a loopback interface.** A mock bound to
`127.0.0.1` in the bench distribution is reachable at `http://localhost:<port>`
from the Moodle distribution. Binding to `0.0.0.0` is not required. This was
tested directly rather than inferred, because an earlier failure that looked
like a binding problem turned out to be a mock process that had exited with the
shell that launched it.

That last point is worth remembering when running anything long: a process
started inside a one-shot `wsl.exe` invocation dies when that invocation ends.

### An indicative number, which is not a result

The single spike request completed in about 473 ms against a mock configured for
410 ms.

**Do not quote this.** It is one request, not three repeats of a five-minute
run. It went through PHP CLI rather than the web server, with a cold opcache, no
warmup, and none of the section 6 laptop controls in force. It is recorded here
only because it shows the study is unlikely to produce a trivial null result:
the gap is the same order of magnitude as the 100 ms budget in section 3, so
there is something there worth measuring properly. The real figure comes from
Arm A in phase 6.
