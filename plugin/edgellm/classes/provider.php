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

namespace aiprovider_edgellm;

use core_ai\form\action_settings_form;
use Psr\Http\Message\RequestInterface;

/**
 * AI provider for an arbitrary OpenAI-compatible endpoint.
 *
 * Written for the moodle-inference-bench study rather than for production. Two
 * things distinguish it from the providers core ships, and both exist to serve
 * the measurement:
 *
 * 1. The endpoint is a single provider-level URL, so one instance can be pointed
 *    at the benchmark mock, a llama.cpp server, Ollama's OpenAI-compatible
 *    endpoint, or a commercial API, by changing one setting and nothing else.
 * 2. It carries T2 timing instrumentation. See the instrumentation class.
 *
 * Core's own aiprovider_openai is the primary measurement path for Arm A,
 * precisely because it contains none of our code. This provider supplies T2 and
 * acts as the comparison against that path. See revision R1 in the study's
 * methodology.
 *
 * @package    aiprovider_edgellm
 * @copyright  2026 Tai Le Tan
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class provider extends \core_ai\provider {

    #[\Override]
    public static function get_action_list(): array {
        // The three actions the study's workload model maps onto. Image
        // generation is out of scope.
        return [
            \core_ai\aiactions\generate_text::class,
            \core_ai\aiactions\summarise_text::class,
            \core_ai\aiactions\explain_text::class,
        ];
    }

    #[\Override]
    public function add_authentication_headers(RequestInterface $request): RequestInterface {
        // A local runtime usually needs no key. One is sent only when
        // configured, which is what makes the cloud baseline in section 10 of
        // the methodology reachable through this same code path.
        if (empty($this->config['apikey'])) {
            return $request;
        }
        return $request->withAddedHeader('Authorization', "Bearer {$this->config['apikey']}");
    }

    #[\Override]
    public static function get_action_settings(
        string $action,
        array $customdata = [],
    ): action_settings_form|bool {
        $actionname = substr($action, (strrpos($action, '\\') + 1));
        $customdata['actionname'] = $actionname;
        $customdata['action'] = $action;
        $customdata['providername'] = 'aiprovider_edgellm';

        if (in_array($actionname, ['generate_text', 'summarise_text', 'explain_text'], true)) {
            return new form\action_form(customdata: $customdata);
        }

        return false;
    }

    #[\Override]
    public static function get_action_setting_defaults(string $action): array {
        $mform = self::get_action_settings($action, ['providerid' => 0]);
        if ($mform === false) {
            return [];
        }
        return $mform->get_defaults();
    }

    #[\Override]
    public function is_provider_configured(): bool {
        // The endpoint is the only genuinely required setting. An API key is
        // not, because the whole point is talking to a local runtime.
        return !empty($this->config['endpoint']);
    }
}
