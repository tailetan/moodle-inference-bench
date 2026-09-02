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

## Measured: the dev server ceiling is real, and raising it works

`moodle-serve.sh` starts PHP's built-in server with `PHP_CLI_SERVER_WORKERS=8`.
The Arm A concurrency ladder goes to 50. That was a suspicion read off a
configuration value, so it was measured rather than assumed.

`scripts/spike_dev_server.py` serves a PHP script that does nothing but sleep for
410 ms, and drives it with the same open-loop harness the benchmark uses. Because
the sleep is the only work, every millisecond above 410 is queueing. Raw output:
[`../results/raw/spike_dev_server.json`](../results/raw/spike_dev_server.json).

Latency above the 410 ms sleep, in milliseconds:

| Concurrency | Target rate | 8 workers, p50 excess | 8 workers, p95 excess | 64 workers, p50 excess | 64 workers, p95 excess |
|---|---|---|---|---|---|
| 1 | 2.44/s | 110 | 568 | 22 | 162 |
| 2 | 4.88/s | 201 | 997 | 26 | 40 |
| 5 | 12.20/s | 99 | 672 | 30 | 42 |
| 10 | 24.39/s | 1,361 | 2,568 | 23 | 39 |
| 20 | 48.78/s | 8,228 | 15,770 | 18 | 37 |
| 50 | 121.95/s | 15,398 | 28,723 | 13 | 427 |

At 8 workers, concurrency 50 also lost **39.4% of requests to timeouts**. At 64
workers nothing timed out at any level.

**The ceiling sits exactly where the worker count puts it.** Each worker blocks
for the whole sleep, so sustainable concurrency is the worker count. The
inflection falls between 5 and 10, and by concurrency 20 the queueing delay is
twenty times the backend latency it is supposed to be measuring.

**It is the server, not the instrument.** The harness's own dispatch lag stayed
between 4 and 43 ms throughout, while response latency reached 29 seconds. In an
open-loop design dispatch does not wait for responses, so that gap is what
distinguishes a saturated server from a saturated harness. The harness flagged
itself as saturated only at the two worst levels, where the backlog was large
enough to disturb it too.

### Decision

Raise the worker count. It is demonstrably sufficient for a trivial endpoint:
at 64 workers the excess is 13 to 30 ms at the median across the whole ladder,
against a 100 ms budget for Moodle's own overhead.

Two conditions on that decision, neither yet satisfied:

1. **Re-measure against real Moodle before Arm A runs.** This spike served a
   sleep. Real Moodle does session handling, database queries and policy checks
   per request, so its ceiling will be lower, and possibly much lower. The
   number that matters is Moodle's, not PHP's.
2. **Decide whether php-fpm behind nginx is needed for the published run.** PHP's
   built-in server is explicitly not built for concurrency, and no real site uses
   it. Results gathered on it are open to the objection that they describe a dev
   server rather than a deployment. That objection is fair, and the answer
   depends on whether the residual overhead at 64 workers stays small once real
   Moodle is in the path.

### A measurement-attribution point this exposes

Even at 64 workers with a trivial script, the web server adds 13 to 30 ms at the
median. That is the same order as the overhead Arm A is trying to detect.

So `T1` must be instrumented **inside PHP**, around the `core_ai` manager call,
exactly as section 7 of the methodology specifies. It must not be taken from the
harness's wall clock, because that number includes web-server queueing and
would inflate Moodle's apparent overhead by an amount that grows with load. The
harness's end-to-end figure is still worth recording, but as a separate quantity
answering a different question.

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

## PHPUnit

Set up on 2 September 2026 so the provider plugin's tests could run. The Moodle
checkout had no PHPUnit environment before this: no `vendor/` directory and no
`phpunit_prefix`.

What was done, in the Moodle root:

```bash
cp config.php config.php.bak-prephpunit
composer install
# two lines added to config.php, before require_once(lib/setup.php)
php public/admin/tool/phpunit/cli/init.php
```

The two lines:

```php
$CFG->phpunit_prefix = 'phpu_';
$CFG->phpunit_dataroot = '/home/tailetan/workspace/phpunitdata';
```

The test environment uses a separate table prefix and a separate dataroot, so
the development site's own data and database tables are untouched. The original
`config.php` is kept at `config.php.bak-prephpunit`.

Running the plugin's suite:

```bash
cd ~/workspace/moodle
vendor/bin/phpunit --testsuite aiprovider_edgellm_testsuite
```

26 tests, 77 assertions, all passing on Moodle 5.2.2+, PHP 8.3.30 and PostgreSQL
16.13.
