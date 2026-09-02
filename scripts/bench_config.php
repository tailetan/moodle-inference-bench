<?php
// This file is part of the moodle-inference-bench study.
//
// Configure a Moodle site for a benchmark run, and put it back afterwards.
//
// Three things have to change before Moodle can be measured against a local
// backend, and all three are changes an administrator would not want left in
// place:
//
//   1. cURL security. core\http_client blocks localhost and every port except
//      80 and 443, so a local runtime is unreachable until both settings are
//      widened. This weakens the site's protection against server-side request
//      forgery and must not be left widened.
//   2. Provider instances pointed at the backend under test.
//   3. The benchmark endpoint and its T2 instrumentation, which execute AI
//      actions over HTTP.
//
// So setup records the exact prior value of everything it touches, and teardown
// restores what was recorded rather than what the defaults happen to be. A site
// that had already customised its cURL settings gets its own values back.
//
// Usage:
//   php scripts/bench_config.php --setup
//   php scripts/bench_config.php --status
//   php scripts/bench_config.php --teardown
//
// Configuration comes from the environment, so .env drives it. See .env.example.

define('CLI_SCRIPT', true);

$moodleroot = getenv('MOODLE_ROOT') ?: (getenv('HOME') . '/workspace/moodle');
if (!is_file($moodleroot . '/config.php')) {
    fwrite(STDERR, "No Moodle at MOODLE_ROOT={$moodleroot}\n");
    exit(1);
}
require_once($moodleroot . '/config.php');
require_once($CFG->libdir . '/clilib.php');

const STATE_KEY = 'benchsavedstate';
const PLUGIN = 'aiprovider_edgellm';
const PROVIDER_NAME_PREFIX = 'bench-';

[$options, $unrecognised] = cli_get_params(
    ['setup' => false, 'teardown' => false, 'status' => false, 'help' => false],
    ['h' => 'help'],
);

if ($options['help'] || (!$options['setup'] && !$options['teardown'] && !$options['status'])) {
    cli_writeln('Configure Moodle for a benchmark run, or restore it afterwards.');
    cli_writeln('');
    cli_writeln('  --setup     point Moodle at the backend and open the bench endpoint');
    cli_writeln('  --status    show what is currently configured');
    cli_writeln('  --teardown  restore everything setup changed');
    cli_writeln('');
    cli_writeln('Driven by environment variables; see .env.example.');
    exit(0);
}

/**
 * Read an environment variable with a fallback.
 *
 * @param string $name Variable name.
 * @param string $default Value to use when unset or empty.
 * @return string
 */
function bench_env(string $name, string $default = ''): string {
    $value = getenv($name);
    return ($value === false || $value === '') ? $default : $value;
}

/**
 * Every provider instance this script created, identified by name prefix so it
 * can never delete one an administrator made.
 *
 * @return array
 */
function bench_owned_providers(): array {
    $manager = \core\di::get(\core_ai\manager::class);
    $owned = [];
    foreach ($manager->get_provider_instances() as $provider) {
        if (strpos($provider->name, PROVIDER_NAME_PREFIX) === 0) {
            $owned[] = $provider;
        }
    }
    return $owned;
}

$manager = \core\di::get(\core_ai\manager::class);

// ---------------------------------------------------------------------------
// Status
// ---------------------------------------------------------------------------
if ($options['status']) {
    $state = get_config(PLUGIN, STATE_KEY);
    cli_heading('Benchmark configuration');
    cli_writeln('saved state:        ' . ($state ? 'present (setup has run)' : 'none'));
    cli_writeln('bench endpoint:     '
        . (get_config(PLUGIN, 'enablebenchendpoint') ? 'ENABLED' : 'disabled'));
    cli_writeln('T2 instrumentation: '
        . (get_config(PLUGIN, 'enableinstrumentation') ? 'ENABLED' : 'disabled'));
    cli_writeln('curl blocked hosts: '
        . str_replace("\n", ' | ', (string)get_config('core', 'curlsecurityblockedhosts')));
    cli_writeln('curl allowed ports: '
        . str_replace("\n", ' | ', (string)get_config('core', 'curlsecurityallowedport')));
    cli_writeln('');
    $owned = bench_owned_providers();
    if (!$owned) {
        cli_writeln('provider instances: none created by this script');
    }
    foreach ($owned as $provider) {
        cli_writeln(sprintf(
            'provider instance:  id=%d name=%s enabled=%d endpoint=%s',
            $provider->id,
            $provider->name,
            $provider->enabled,
            $provider->config['endpoint'] ?? ($provider->actionconfig ? '(per action)' : '(none)'),
        ));
    }
    exit(0);
}

// ---------------------------------------------------------------------------
// Teardown
// ---------------------------------------------------------------------------
if ($options['teardown']) {
    cli_heading('Restoring Moodle');

    foreach (bench_owned_providers() as $provider) {
        $manager->delete_provider_instance($provider);
        cli_writeln("deleted provider instance id={$provider->id} ({$provider->name})");
    }

    $state = get_config(PLUGIN, STATE_KEY);
    if ($state) {
        $saved = json_decode($state, true);
        if (is_array($saved)) {
            // Restore exactly what was there, including the case where the
            // setting had never been set at all.
            foreach (['curlsecurityblockedhosts', 'curlsecurityallowedport'] as $name) {
                if (array_key_exists($name, $saved)) {
                    if ($saved[$name] === null) {
                        unset_config($name);
                        cli_writeln("unset {$name} (it was never set before)");
                    } else {
                        set_config($name, $saved[$name]);
                        cli_writeln("restored {$name}");
                    }
                }
            }
        }
        unset_config(STATE_KEY, PLUGIN);
    } else {
        cli_writeln('no saved state found: cURL settings left as they are');
    }

    set_config('enablebenchendpoint', 0, PLUGIN);
    set_config('enableinstrumentation', 0, PLUGIN);
    set_config('benchtoken', '', PLUGIN);
    cli_writeln('benchmark endpoint disabled, instrumentation disabled, token cleared');
    exit(0);
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------
cli_heading('Configuring Moodle for a benchmark run');

$endpoint = bench_env('BACKEND_ENDPOINT', 'http://localhost:8090/v1/chat/completions');
$model = bench_env('BACKEND_MODEL', 'mock-deterministic');
$apikey = bench_env('BACKEND_API_KEY');
$timeout = (int)bench_env('BACKEND_TIMEOUT', '120');
$extraparams = bench_env('BACKEND_EXTRA_PARAMS', '{"temperature": 0, "max_tokens": 300}');
$paths = array_filter(array_map('trim', explode(',', bench_env('BENCH_PATHS', 'edgellm,openai'))));
$token = bench_env('BENCH_TOKEN');

if ($token === '') {
    $token = bin2hex(random_bytes(24));
}

json_decode($extraparams);
if (json_last_error() !== JSON_ERROR_NONE) {
    cli_error('BACKEND_EXTRA_PARAMS is not valid JSON: ' . $extraparams);
}

cli_writeln("endpoint: {$endpoint}");
cli_writeln("model:    {$model}");
cli_writeln("paths:    " . implode(', ', $paths));
cli_writeln('');

// Save the prior state before touching anything. Only save once, so running
// setup twice does not overwrite the real original with our own widened value.
if (!get_config(PLUGIN, STATE_KEY)) {
    $saved = [];
    foreach (['curlsecurityblockedhosts', 'curlsecurityallowedport'] as $name) {
        $value = get_config('core', $name);
        // get_config returns false for a setting that was never set. Record
        // that as null so teardown can unset it rather than writing a default.
        $saved[$name] = ($value === false) ? null : $value;
    }
    set_config(STATE_KEY, json_encode($saved), PLUGIN);
    cli_writeln('saved prior cURL security settings');
} else {
    cli_writeln('prior cURL security settings already saved, not overwriting');
}

// Widen cURL security just enough to reach this endpoint.
$host = parse_url($endpoint, PHP_URL_HOST);
$scheme = parse_url($endpoint, PHP_URL_SCHEME);
$port = parse_url($endpoint, PHP_URL_PORT) ?: ($scheme === 'https' ? 443 : 80);

$blocked = preg_split('/\R/', (string)get_config('core', 'curlsecurityblockedhosts'), -1,
    PREG_SPLIT_NO_EMPTY);
$islocal = in_array($host, ['localhost', '127.0.0.1', '::1'], true);
if ($islocal) {
    $blocked = array_values(array_filter($blocked, static function (string $line) {
        $line = trim($line);
        return !in_array($line, ['localhost', '127.0.0.0/8', '0.0.0.0', '0000::1'], true);
    }));
}
set_config('curlsecurityblockedhosts', implode("\n", $blocked));

$ports = preg_split('/\R/', (string)get_config('core', 'curlsecurityallowedport'), -1,
    PREG_SPLIT_NO_EMPTY);
if (!in_array((string)$port, $ports, true)) {
    $ports[] = (string)$port;
}
set_config('curlsecurityallowedport', implode("\n", $ports));
cli_writeln("cURL security widened for {$host}:{$port}");

// Remove any instance from a previous run before creating new ones, so repeated
// setups do not accumulate providers that would all try to serve the action.
foreach (bench_owned_providers() as $provider) {
    $manager->delete_provider_instance($provider);
    cli_writeln("removed stale provider instance id={$provider->id}");
}

$actions = [
    \core_ai\aiactions\generate_text::class,
    \core_ai\aiactions\summarise_text::class,
    \core_ai\aiactions\explain_text::class,
];

if (in_array('edgellm', $paths, true)) {
    $actionconfig = [];
    foreach ($actions as $action) {
        $actionconfig[$action] = [
            'enabled' => true,
            'settings' => [
                'model' => $model,
                'modelextraparams' => $extraparams,
            ],
        ];
    }
    $provider = $manager->create_provider_instance(
        classname: \aiprovider_edgellm\provider::class,
        name: PROVIDER_NAME_PREFIX . 'edgellm',
        enabled: true,
        config: [
            'endpoint' => $endpoint,
            'model' => $model,
            'apikey' => $apikey,
            'timeout' => $timeout,
        ],
        actionconfig: $actionconfig,
    );
    cli_writeln("created provider instance id={$provider->id} (bench-edgellm)");
}

if (in_array('openai', $paths, true)) {
    // Core's own provider, unmodified. This is the primary T1 path: it contains
    // none of this study's code, so the overhead it shows is attributable to
    // Moodle rather than to our plugin. It cannot supply T2.
    $actionconfig = [];
    foreach ($actions as $action) {
        $actionconfig[$action] = [
            'enabled' => true,
            'settings' => [
                'endpoint' => $endpoint,
                'model' => $model,
                'modelextraparams' => $extraparams,
            ],
        ];
    }
    $provider = $manager->create_provider_instance(
        classname: \aiprovider_openai\provider::class,
        name: PROVIDER_NAME_PREFIX . 'openai',
        // Created but NOT enabled. Two enabled providers would both serve the
        // same action and the manager would use whichever sorts first, so which
        // path was measured would be ambiguous. Enable one at a time.
        enabled: false,
        config: ['apikey' => $apikey !== '' ? $apikey : 'bench-not-a-real-key'],
        actionconfig: $actionconfig,
    );
    cli_writeln("created provider instance id={$provider->id} (bench-openai, disabled)");
    cli_writeln('  enable it, and disable bench-edgellm, to measure the core path');
}

set_config('enablebenchendpoint', 1, PLUGIN);
set_config('enableinstrumentation', 1, PLUGIN);
set_config('benchtoken', $token, PLUGIN);

cli_writeln('');
cli_writeln('benchmark endpoint: ' . $CFG->wwwroot . '/ai/provider/edgellm/bench.php');
cli_writeln('bench token:        ' . $token);
cli_writeln('');
cli_writeln('Run --teardown when the run finishes. Leaving the endpoint open');
cli_writeln('leaves a way to execute AI actions over HTTP.');
exit(0);
