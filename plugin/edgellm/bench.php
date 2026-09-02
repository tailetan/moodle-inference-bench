<?php
// This file is part of Moodle - http://moodle.org/
//
// Moodle is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Moodle is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with Moodle.  If not, see <http://www.gnu.org/licenses/>.

/**
 * Benchmark driver endpoint. Measurement scaffolding, not a feature.
 *
 * The study needs T1: the latency of the whole core AI path, from the manager
 * down through policy checks, the provider, the HTTP client and response
 * parsing. A provider plugin cannot measure that, because a provider sits below
 * the manager and only ever sees its own slice. So something has to call the
 * manager and time it, and that something has to be reachable over HTTP because
 * the load harness drives the system from outside.
 *
 * That is all this file is. It times one call to
 * \core_ai\manager::process_action() and reports it, alongside the T2 the
 * provider recorded at its HTTP boundary. Both numbers come from separate clock
 * reads at separate points, which is what the methodology requires: T1 minus T2
 * is the headline finding, and deriving either from the other would make it
 * circular.
 *
 * SECURITY. This endpoint runs AI actions, which cost time and, against a
 * commercial API, money. It is therefore closed by default and refuses every
 * request unless an administrator has explicitly:
 *
 *   1. set `enablebenchendpoint` for this plugin, and
 *   2. set a `benchtoken`, which the caller must present.
 *
 * Both are set from the CLI script in this plugin's cli directory. Turn the
 * endpoint off when a run finishes. Do not deploy this plugin to a production
 * site.
 *
 * @package    aiprovider_edgellm
 * @copyright  2026 Tai Le Tan
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

// No session. The harness is not a browser and holds no cookie, and starting a
// session per request would add lock contention that lands inside T1.
define('NO_MOODLE_COOKIES', true);

require_once(__DIR__ . '/../../../config.php');

/**
 * Emit a JSON response and stop.
 *
 * @param array $payload The response body.
 * @param int $status HTTP status code.
 */
function aiprovider_edgellm_bench_respond(array $payload, int $status = 200): void {
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    http_response_code($status);
    echo json_encode($payload, JSON_UNESCAPED_SLASHES);
    die();
}

// -----------------------------------------------------------------------
// Gate 1: the endpoint must be explicitly switched on.
// -----------------------------------------------------------------------
if (empty(get_config('aiprovider_edgellm', 'enablebenchendpoint'))) {
    aiprovider_edgellm_bench_respond(
        ['error' => 'benchmark endpoint is disabled'],
        403,
    );
}

// -----------------------------------------------------------------------
// Gate 2: a shared secret must be configured and presented.
// -----------------------------------------------------------------------
$expectedtoken = (string)get_config('aiprovider_edgellm', 'benchtoken');
$presentedtoken = (string)($_SERVER['HTTP_X_BENCH_TOKEN'] ?? '');

// An unset token must never mean "no token required".
if ($expectedtoken === '' || !hash_equals($expectedtoken, $presentedtoken)) {
    aiprovider_edgellm_bench_respond(['error' => 'invalid benchmark token'], 403);
}

if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    aiprovider_edgellm_bench_respond(['error' => 'POST required'], 405);
}

$raw = file_get_contents('php://input');
$input = json_decode($raw, true);
if (!is_array($input)) {
    aiprovider_edgellm_bench_respond(['error' => 'body must be a JSON object'], 400);
}

$actionname = (string)($input['action'] ?? 'summarise_text');
$prompttext = (string)($input['prompttext'] ?? '');

if ($prompttext === '') {
    aiprovider_edgellm_bench_respond(['error' => 'prompttext is required'], 400);
}

$allowedactions = ['generate_text', 'summarise_text', 'explain_text'];
if (!in_array($actionname, $allowedactions, true)) {
    aiprovider_edgellm_bench_respond(
        ['error' => 'unsupported action', 'allowed' => $allowedactions],
        400,
    );
}
$actionclass = '\\core_ai\\aiactions\\' . $actionname;

// Run as a real user so that the policy and capability checks the methodology
// wants inside T1 actually execute. Defaults to the site admin; a specific user
// can be named to measure a non-admin path.
$user = !empty($input['userid'])
    ? \core_user::get_user((int)$input['userid'], '*', MUST_EXIST)
    : get_admin();
\core\session\manager::set_user($user);

$context = \context_system::instance();

\aiprovider_edgellm\instrumentation::reset();

$manager = \core\di::get(\core_ai\manager::class);
$action = new $actionclass(
    contextid: $context->id,
    userid: (int)$user->id,
    prompttext: $prompttext,
);

// ---------------------------------------------------------------------------
// T1. Everything core does, measured from outside the manager. Nothing is
// written between these two clock reads.
// ---------------------------------------------------------------------------
// An uncaught exception here would be rendered by Moodle as an HTML error page.
// The harness parses JSON, so that would be recorded as a malformed response
// rather than as the failure it is, and the run would be quietly wrong. Every
// outcome must leave this file as JSON.
$t1start = microtime(true);
try {
    $response = $manager->process_action($action);
    $t1ms = (microtime(true) - $t1start) * 1000;
} catch (\Throwable $e) {
    $t1ms = (microtime(true) - $t1start) * 1000;
    $measurement = \aiprovider_edgellm\instrumentation::get();
    aiprovider_edgellm_bench_respond([
        'success' => false,
        'action' => $actionname,
        't1_total_ms' => round($t1ms, 3),
        't2_model_ms' => $measurement['backend_ms'] !== null
            ? round($measurement['backend_ms'], 3)
            : null,
        'backend_status' => $measurement['status'],
        'backend_error_type' => $measurement['error_type'],
        'errorcode' => 'exception',
        'error' => get_class($e),
        'errormessage' => $e->getMessage(),
    ], 200);
}

// T2, as recorded by the provider at its own HTTP boundary.
$measurement = \aiprovider_edgellm\instrumentation::get();

$payload = [
    'success' => $response->get_success(),
    'action' => $actionname,
    't1_total_ms' => round($t1ms, 3),
    't2_model_ms' => $measurement['backend_ms'] !== null
        ? round($measurement['backend_ms'], 3)
        : null,
    'backend_status' => $measurement['status'],
    'backend_error_type' => $measurement['error_type'],
];

if ($response->get_success()) {
    $data = $response->get_response_data();
    $payload['input_tokens'] = $data['prompttokens'] ?? null;
    $payload['output_tokens'] = $data['completiontokens'] ?? null;
    $payload['model'] = $data['model'] ?? null;
    $payload['finishreason'] = $data['finishreason'] ?? null;
    // The generated text itself is not returned. The benchmark measures
    // latency, and shipping the body back would add transfer time to every
    // measurement for no benefit. Quality evaluation collects outputs
    // separately.
    $payload['output_chars'] = strlen((string)($data['generatedcontent'] ?? ''));
} else {
    $payload['errorcode'] = $response->get_errorcode();
    $payload['error'] = $response->get_error();
    $payload['errormessage'] = $response->get_errormessage();
}

aiprovider_edgellm_bench_respond($payload);
