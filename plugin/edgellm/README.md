# aiprovider_edgellm

A Moodle AI provider for an arbitrary OpenAI-compatible endpoint, carrying
measurement instrumentation for the moodle-inference-bench study.

**Do not install this on a production site.** It ships a benchmark endpoint that
executes AI actions over HTTP. That endpoint is closed by default and refuses
every request unless an administrator switches it on and sets a token, but the
plugin exists to be measured with, not to be run with.

## What it is for

The study measures Moodle's own AI subsystem overhead as `T1 - T2`:

- **`T2`** is the latency at the HTTP boundary between the provider and the
  backend. Only code inside a provider can see that boundary, which is why this
  plugin exists.
- **`T1`** is the latency of the whole core path, from the manager down. A
  provider cannot measure it, because a provider sits *below* the manager. So
  `bench.php` times a call to `\core_ai\manager::process_action()` from outside.

Both numbers come from separate clock reads at separate points. Neither is
derived from the other, because `T1 - T2` is the study's headline finding and a
derived value would make it circular.

Core's own `aiprovider_openai` is the **primary** measurement path for Arm A,
precisely because it contains none of our code. This plugin supplies `T2` and
acts as the comparison against that path. See revision R1 in
[`../../docs/methodology.md`](../../docs/methodology.md).

## Layout

```
classes/
  provider.php            configuration, auth, supported actions
  abstract_processor.php  request construction, response parsing, error mapping
  process_*.php           one empty class per action; all behaviour is shared
  instrumentation.php     the T2 measurement. Writes nothing, off by default
  form/action_form.php    action settings: model, system instruction, extra params
  hook_listener.php       provider instance settings: endpoint, model, key, timeout
bench.php                 the T1 driver endpoint. Closed by default
cli/benchmode.php         turn instrumentation and the endpoint on and off
```

## Instrumentation is kept out of the production path

Everything to do with measurement lives in `instrumentation.php`. The request
path contains exactly two calls to it, bracketing the HTTP send and nothing
else, which is what makes the recorded value `T2` rather than something broader.

Three properties are deliberate:

- **It writes nothing.** No database, no log, no file. A write inside the request
  would land inside `T1`, inflating the very number the study exists to measure.
  The value is held in a static for the life of the request and read afterwards
  by `bench.php`.
- **It is off unless switched on**, via `cli/benchmode.php`. When off, a request
  costs one static property read.
- **It cannot change the response**, including on failure.

## Usage

From the Moodle root, after syncing the plugin with
[`../../scripts/sync-plugin.sh`](../../scripts/sync-plugin.sh):

```bash
php admin/cli/upgrade.php --non-interactive
php public/ai/provider/edgellm/cli/benchmode.php --on
```

That prints the endpoint URL and a token. Drive it with the token in the
`X-Bench-Token` header:

```bash
curl -X POST http://localhost:8080/ai/provider/edgellm/bench.php \
  -H 'Content-Type: application/json' \
  -H 'X-Bench-Token: THE_TOKEN' \
  -d '{"action":"summarise_text","prompttext":"..."}'
```

The response carries `t1_total_ms` and `t2_model_ms` along with token counts and
the backend status. The generated text itself is **not** returned: the benchmark
measures latency, and shipping the body back would add transfer time to every
measurement.

Run `cli/benchmode.php --off` when the run finishes.

### Moodle blocks local endpoints by default

`core\http_client` enforces `curlsecurityblockedhosts` and
`curlsecurityallowedport`. Their shipped defaults block `localhost`, `127.0.0.0/8`
and every port except 80 and 443, so a local runtime is unreachable until both
are widened. Those values are part of the measured configuration and must be
recorded with any results.

## A note on core's error handling

Guzzle's `ConnectException` extends `TransferException` directly and is **not** a
`RequestException`. Core's `aiprovider_openai` catches only `RequestException`,
so a refused connection to a configured endpoint escapes as an uncaught
exception rather than becoming a handled AI error. That is the most likely
failure when pointing Moodle at a local runtime that is not running.

This plugin catches `GuzzleException` instead, and maps a transport failure with
no HTTP status onto 500.

## Tests

PHPUnit tests covering request construction, response parsing, error mapping,
transport failures and timeout handling are in `tests/`.

**26 tests, 77 assertions, all passing** against Moodle 5.2.2+ on PHP 8.3.30 and
PostgreSQL 16.13:

```
vendor/bin/phpunit --testsuite aiprovider_edgellm_testsuite
```

The run reports three PHPUnit deprecations, all of the form "Metadata found in
doc-comment ... use attributes instead". They come from the `@covers`
annotations. Core's own `aiprovider_ollama` tests emit the same warning, so this
matches current Moodle convention and is left alone rather than diverging from
the surrounding codebase.

Also verified end to end against a running Moodle, with the deterministic mock
as the backend:

- the plugin installs through `admin/cli/upgrade.php`
- every file passes `php -l`
- `bench.php` refuses requests when disabled, when no token is presented, and
  when the wrong token is presented
- `summarise_text` and `generate_text` both return `t1_total_ms` and
  `t2_model_ms`, recorded independently
- a refused connection returns JSON with `success: false`, a
  `backend_error_type` of `ConnectException`, and both timings still recorded
- unsupported actions and a missing prompt are rejected with 400
