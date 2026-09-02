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
 * Turn benchmark mode on and off.
 *
 * Instrumentation and the benchmark endpoint are deliberately not exposed in the
 * admin UI. They are not features, and putting them behind a CLI script that has
 * to be run on purpose keeps that distinction obvious.
 *
 * Usage:
 *   php cli/benchmode.php --status
 *   php cli/benchmode.php --on
 *   php cli/benchmode.php --on --token=SECRET
 *   php cli/benchmode.php --off
 *
 * @package    aiprovider_edgellm
 * @copyright  2026 Tai Le Tan
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

define('CLI_SCRIPT', true);

require_once(__DIR__ . '/../../../../config.php');
require_once($CFG->libdir . '/clilib.php');

[$options, $unrecognised] = cli_get_params(
    [
        'on' => false,
        'off' => false,
        'status' => false,
        'token' => '',
        'help' => false,
    ],
    ['h' => 'help'],
);

if ($options['help'] || (!$options['on'] && !$options['off'] && !$options['status'])) {
    cli_writeln('Turn aiprovider_edgellm benchmark mode on or off.');
    cli_writeln('');
    cli_writeln('  --status        show the current state');
    cli_writeln('  --on            enable instrumentation and the benchmark endpoint');
    cli_writeln('  --off           disable both and clear the token');
    cli_writeln('  --token=SECRET  use this token instead of generating one');
    cli_writeln('');
    cli_writeln('The benchmark endpoint executes AI actions over HTTP. Turn it');
    cli_writeln('off when a run finishes, and never enable it on a production site.');
    exit(0);
}

if ($options['on'] && $options['off']) {
    cli_error('--on and --off are mutually exclusive.');
}

if ($options['status']) {
    $enabled = get_config('aiprovider_edgellm', 'enablebenchendpoint');
    $instrumented = get_config('aiprovider_edgellm', 'enableinstrumentation');
    $token = (string)get_config('aiprovider_edgellm', 'benchtoken');
    cli_writeln('benchmark endpoint:  ' . (!empty($enabled) ? 'ENABLED' : 'disabled'));
    cli_writeln('T2 instrumentation:  ' . (!empty($instrumented) ? 'ENABLED' : 'disabled'));
    cli_writeln('token:               ' . ($token !== '' ? $token : '(not set)'));
    exit(0);
}

if ($options['off']) {
    set_config('enablebenchendpoint', 0, 'aiprovider_edgellm');
    set_config('enableinstrumentation', 0, 'aiprovider_edgellm');
    set_config('benchtoken', '', 'aiprovider_edgellm');
    cli_writeln('Benchmark mode OFF. Endpoint disabled, instrumentation disabled, token cleared.');
    exit(0);
}

$token = $options['token'] !== '' ? $options['token'] : bin2hex(random_bytes(24));

set_config('enablebenchendpoint', 1, 'aiprovider_edgellm');
set_config('enableinstrumentation', 1, 'aiprovider_edgellm');
set_config('benchtoken', $token, 'aiprovider_edgellm');

cli_writeln('Benchmark mode ON.');
cli_writeln('  endpoint: ' . $CFG->wwwroot . '/ai/provider/edgellm/bench.php');
cli_writeln('  token:    ' . $token);
cli_writeln('');
cli_writeln('Send the token in the X-Bench-Token header. Run --off when finished.');
exit(0);
