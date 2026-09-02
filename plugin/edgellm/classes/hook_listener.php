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

use core_ai\hook\after_ai_provider_form_hook;

/**
 * Adds this provider's instance settings to the provider setup form.
 *
 * @package    aiprovider_edgellm
 * @copyright  2026 Tai Le Tan
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class hook_listener {

    /**
     * Define the provider instance form.
     *
     * @param after_ai_provider_form_hook $hook The hook to add to the AI instance setup.
     */
    public static function set_form_definition_for_aiprovider_edgellm(
        after_ai_provider_form_hook $hook,
    ): void {
        if ($hook->plugin !== 'aiprovider_edgellm') {
            return;
        }

        $mform = $hook->mform;

        // The full endpoint URL, path included. Changing this one field is what
        // repoints the whole study between the benchmark mock, a local runtime
        // and a commercial API.
        $mform->addElement(
            'text',
            'endpoint',
            get_string('endpoint', 'aiprovider_edgellm'),
            ['size' => 50],
        );
        $mform->setType('endpoint', PARAM_URL);
        $mform->addHelpButton('endpoint', 'endpoint', 'aiprovider_edgellm');
        $mform->addRule('endpoint', get_string('required'), 'required', null, 'client');
        $mform->setDefault('endpoint', 'http://localhost:8090/v1/chat/completions');

        // Default model, used when an action does not set its own.
        $mform->addElement(
            'text',
            'model',
            get_string('providermodel', 'aiprovider_edgellm'),
            ['size' => 40],
        );
        $mform->setType('model', PARAM_TEXT);
        $mform->addHelpButton('model', 'providermodel', 'aiprovider_edgellm');

        // Optional. A local runtime usually needs no key; a commercial API for
        // the cloud baseline does.
        $mform->addElement(
            'passwordunmask',
            'apikey',
            get_string('apikey', 'aiprovider_edgellm'),
        );
        $mform->setType('apikey', PARAM_TEXT);
        $mform->addHelpButton('apikey', 'apikey', 'aiprovider_edgellm');

        // CPU inference is slow enough that the default HTTP timeout is not
        // generous enough for a long prefill.
        $mform->addElement(
            'text',
            'timeout',
            get_string('timeout', 'aiprovider_edgellm'),
            ['size' => 6],
        );
        $mform->setType('timeout', PARAM_INT);
        $mform->addHelpButton('timeout', 'timeout', 'aiprovider_edgellm');
        $mform->setDefault('timeout', 60);
    }
}
