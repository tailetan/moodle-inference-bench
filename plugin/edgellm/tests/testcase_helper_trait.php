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

/**
 * Shared setup for this plugin's tests.
 *
 * @package    aiprovider_edgellm
 * @copyright  2026 Tai Le Tan
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
trait testcase_helper_trait {

    /**
     * Create a provider instance pointed at a nominal endpoint.
     *
     * @param string $actionclass The action class the settings belong to.
     * @param array $actionsettings Action settings to merge over the defaults.
     * @param array $config Provider-level config to merge over the defaults.
     * @return \core_ai\provider
     */
    public function create_provider(
        string $actionclass,
        array $actionsettings = [],
        array $config = [],
    ): \core_ai\provider {
        $manager = \core\di::get(\core_ai\manager::class);

        $providerconfig = array_merge([
            'endpoint' => 'http://localhost:8090/v1/chat/completions',
            'model' => 'mock-deterministic',
            'timeout' => 30,
        ], $config);

        $actionconfig = [
            $actionclass => [
                'settings' => array_merge([
                    'model' => 'mock-deterministic',
                ], $actionsettings),
            ],
        ];

        return $manager->create_provider_instance(
            classname: '\aiprovider_edgellm\provider',
            name: 'edgellm-test',
            config: $providerconfig,
            actionconfig: $actionconfig,
        );
    }

    /**
     * A well-formed chat completion body, as the mock returns.
     *
     * @param array $overrides Fields to replace in the decoded structure.
     * @return string JSON body.
     */
    public function success_body(array $overrides = []): string {
        $body = [
            'id' => 'chatcmpl-mock-1234',
            'object' => 'chat.completion',
            'created' => 1788000000,
            'model' => 'mock-deterministic',
            'system_fingerprint' => 'fp_mock_deterministic',
            'choices' => [[
                'index' => 0,
                'message' => ['role' => 'assistant', 'content' => 'A summary.'],
                'finish_reason' => 'stop',
            ]],
            'usage' => [
                'prompt_tokens' => 22,
                'completion_tokens' => 32,
                'total_tokens' => 54,
            ],
        ];
        return json_encode(array_merge($body, $overrides));
    }
}
