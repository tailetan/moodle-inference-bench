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
 * Strings for aiprovider_edgellm.
 *
 * @package    aiprovider_edgellm
 * @copyright  2026 Tai Le Tan
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

defined('MOODLE_INTERNAL') || die();

$string['apikey'] = 'API key';
$string['apikey_help'] = 'Sent as a bearer token when set. Leave empty for a local runtime that needs no authentication.';
$string['benchtoken'] = 'Benchmark endpoint token';
$string['benchtoken_desc'] = 'Shared secret required by bench.php. The benchmark endpoint refuses every request unless this is set and matches.';
$string['enablebenchendpoint'] = 'Enable the benchmark endpoint';
$string['enablebenchendpoint_desc'] = 'Allows bench.php to run AI actions over HTTP for measurement. Leave off except while a benchmark run is in progress. This is measurement scaffolding, not a feature.';
$string['enableinstrumentation'] = 'Record backend latency (T2)';
$string['enableinstrumentation_desc'] = 'Times the HTTP call to the endpoint and holds the result in memory for the benchmark endpoint to read. Nothing is written to the database or to logs. Off by default.';
$string['endpoint'] = 'API endpoint';
$string['endpoint_help'] = 'Full URL of an OpenAI-compatible chat completions endpoint, path included. For example http://localhost:8090/v1/chat/completions for the benchmark mock, or http://localhost:11434/v1/chat/completions for Ollama.';
$string['extraparams'] = 'Extra model parameters';
$string['extraparams_help'] = 'A JSON object merged into the request body, for example {"temperature": 0, "max_tokens": 300}. The benchmark methodology fixes temperature at 0 and holds max_tokens constant across arms.';
$string['invalidjson'] = 'Invalid JSON. Extra model parameters must be a JSON object.';
$string['model'] = 'Model';
$string['model_help'] = 'Model name sent to the endpoint. Free text, because the endpoint decides which names are valid.';
$string['pluginname'] = 'EdgeLLM benchmark provider';
$string['privacy:metadata:aiprovider_edgellm:externalpurpose'] = 'This information is sent to the configured endpoint so a response can be generated. The endpoint may be on the same machine or remote, depending on configuration.';
$string['privacy:metadata:aiprovider_edgellm:model'] = 'The model name used to generate the response.';
$string['privacy:metadata:aiprovider_edgellm:prompttext'] = 'The user-entered text prompt used to generate the response.';
$string['providermodel'] = 'Default model';
$string['providermodel_help'] = 'Used by any action that does not set a model of its own.';
$string['systeminstruction'] = 'System instruction';
$string['systeminstruction_help'] = 'Sent as the system message. Defaults to the instruction the action itself defines, so leaving it alone reproduces core behaviour.';
$string['timeout'] = 'Timeout (seconds)';
$string['timeout_help'] = 'HTTP request timeout. CPU inference can take far longer than a cloud API, particularly when a long input has to be processed before the first token appears.';
