<?php
// Spike: can Moodle's own aiprovider_openai talk to our mock endpoint?
//
// Revision R1 in docs/methodology.md proposes measuring Arm A through core's
// unmodified OpenAI provider, so that the overhead figure is attributable to
// Moodle rather than to a plugin written for this study. That proposal was read
// off the source. This script executes it.
//
// It does four things and then puts everything back:
//
//   1. Widens the cURL security settings so core is allowed to reach the mock.
//      Core blocks 127.0.0.0/8 and localhost, and permits only ports 80 and 443,
//      so out of the box the request never leaves Moodle.
//   2. Creates an aiprovider_openai instance pointed at the mock.
//   3. Runs one real summarise_text through \core_ai\manager::process_action().
//   4. Deletes the instance and restores the original settings.
//
// Nothing in core is modified. Run it from the Moodle root:
//
//   php /path/to/scripts/spike_core_provider.php --endpoint=http://localhost:8090/v1/chat/completions
//
// This is a spike, not part of the benchmark. It answers yes or no.

define('CLI_SCRIPT', true);

$moodleroot = getenv('MOODLE_ROOT') ?: (getenv('HOME') . '/workspace/moodle');
require_once($moodleroot . '/config.php');
require_once($CFG->libdir . '/clilib.php');

[$options, $unrecognised] = cli_get_params(
    [
        'endpoint' => 'http://localhost:8090/v1/chat/completions',
        'model' => 'mock-deterministic',
        'keep' => false,
        'help' => false,
    ],
    ['h' => 'help'],
);

if ($options['help']) {
    cli_writeln("Spike: drive core's aiprovider_openai against the mock endpoint.");
    cli_writeln("  --endpoint=URL   mock chat completions URL");
    cli_writeln("  --model=NAME     model name to send");
    cli_writeln("  --keep           leave the provider instance and settings in place");
    exit(0);
}

$endpoint = $options['endpoint'];
$model = $options['model'];

cli_heading('Spike: core aiprovider_openai against the mock');
cli_writeln("endpoint: {$endpoint}");
cli_writeln("model:    {$model}");
cli_writeln('');

// ---------------------------------------------------------------------------
// Step 1: cURL security.
// ---------------------------------------------------------------------------
// These are the settings that decide whether core is even allowed to make the
// request. They are recorded here because widening them changes Moodle's
// security posture, and that makes them part of the measured configuration
// rather than incidental setup.
$originalblocked = get_config('core', 'curlsecurityblockedhosts');
$originalports = get_config('core', 'curlsecurityallowedport');

cli_writeln('cURL security before:');
cli_writeln('  blocked hosts: ' . str_replace("\n", ' | ', (string)$originalblocked));
cli_writeln('  allowed ports: ' . str_replace("\n", ' | ', (string)$originalports));

$host = parse_url($endpoint, PHP_URL_HOST);
$port = parse_url($endpoint, PHP_URL_PORT) ?: 80;

// Drop the entries that would block our host, and allow our port.
$blockedlines = preg_split('/\R/', (string)$originalblocked, -1, PREG_SPLIT_NO_EMPTY);
$keep = array_values(array_filter($blockedlines, static function (string $line) {
    $line = trim($line);
    return $line !== 'localhost' && $line !== '127.0.0.0/8' && $line !== '0.0.0.0';
}));
$portlines = preg_split('/\R/', (string)$originalports, -1, PREG_SPLIT_NO_EMPTY);
if (!in_array((string)$port, $portlines, true)) {
    $portlines[] = (string)$port;
}

set_config('curlsecurityblockedhosts', implode("\n", $keep));
set_config('curlsecurityallowedport', implode("\n", $portlines));
cli_writeln('cURL security widened for the spike.');
cli_writeln('');

// ---------------------------------------------------------------------------
// Step 2: create the provider instance.
// ---------------------------------------------------------------------------
$manager = \core\di::get(\core_ai\manager::class);
$actionclass = \core_ai\aiactions\summarise_text::class;

$provider = $manager->create_provider_instance(
    classname: \aiprovider_openai\provider::class,
    name: 'spike-mock',
    enabled: true,
    // is_provider_configured() only requires a non-empty apikey. The mock
    // ignores the Authorization header entirely.
    config: ['apikey' => 'spike-not-a-real-key'],
    actionconfig: [
        $actionclass => [
            'enabled' => true,
            'settings' => [
                'endpoint' => $endpoint,
                'model' => $model,
                'systeminstruction' => 'You are a helpful assistant.',
            ],
        ],
    ],
);
cli_writeln("provider instance created: id={$provider->id}");

$cleanup = function () use ($manager, $provider, $originalblocked, $originalports, $options) {
    if ($options['keep']) {
        cli_writeln('--keep given: leaving provider instance and settings in place.');
        return;
    }
    $manager->delete_provider_instance($provider);
    set_config('curlsecurityblockedhosts', $originalblocked);
    set_config('curlsecurityallowedport', $originalports);
    cli_writeln('cleaned up: provider deleted, cURL security restored.');
};

// ---------------------------------------------------------------------------
// Step 3: run one real action through the core manager.
// ---------------------------------------------------------------------------
$exitcode = 0;
try {
    $action = new $actionclass(
        contextid: (\context_system::instance())->id,
        userid: get_admin()->id,
        prompttext: 'Summarise this short course announcement for the spike run.',
    );

    // Wall time across the whole core path: manager, policy checks, provider,
    // HTTP client, response parsing. This is T1 in the methodology's terms.
    $start = microtime(true);
    $response = $manager->process_action($action);
    $t1ms = (microtime(true) - $start) * 1000;

    cli_writeln('');
    cli_heading('Result');
    $success = $response->get_success();
    cli_writeln('success:      ' . ($success ? 'yes' : 'no'));
    cli_writeln(sprintf('t1 (wall):    %.1f ms', $t1ms));

    if ($success) {
        $data = $response->get_response_data();
        $content = $data['generatedcontent'] ?? '';
        cli_writeln('model:        ' . ($data['model'] ?? '(none)'));
        cli_writeln('fingerprint:  ' . ($data['fingerprint'] ?? '(none)'));
        cli_writeln('finishreason: ' . ($data['finishreason'] ?? '(none)'));
        cli_writeln('prompttokens: ' . ($data['prompttokens'] ?? '(none)'));
        cli_writeln('compltokens:  ' . ($data['completiontokens'] ?? '(none)'));
        cli_writeln('content:      ' . substr((string)$content, 0, 60)
            . (strlen((string)$content) > 60 ? '...' : ''));
        cli_writeln('');
        cli_writeln('SPIKE PASSED: core aiprovider_openai reached the mock and '
            . 'parsed its response.');
    } else {
        cli_writeln('errorcode:    ' . $response->get_errorcode());
        cli_writeln('error:        ' . $response->get_error());
        cli_writeln('errormessage: ' . $response->get_errormessage());
        cli_writeln('');
        cli_writeln('SPIKE FAILED: see the error above. Revision R1 in '
            . 'docs/methodology.md needs amending.');
        $exitcode = 1;
    }
} catch (\Throwable $e) {
    cli_writeln('');
    cli_writeln('SPIKE FAILED with an exception:');
    cli_writeln('  ' . get_class($e) . ': ' . $e->getMessage());
    $exitcode = 1;
} finally {
    cli_writeln('');
    $cleanup();
}

exit($exitcode);
