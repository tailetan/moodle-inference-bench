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

namespace aiprovider_edgellm\form;

use core_ai\form\action_settings_form;

/**
 * Action settings form, shared by all three supported actions.
 *
 * Deliberately small. Core's providers offer a model chooser driven by a list of
 * known models, which makes sense when the set of models is known in advance. It
 * is not known here: the endpoint may be any OpenAI-compatible server, so the
 * model is a free text field.
 *
 * @package    aiprovider_edgellm
 * @copyright  2026 Tai Le Tan
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
class action_form extends action_settings_form {

    #[\Override]
    protected function definition(): void {
        $mform = $this->_form;

        $actionconfig = $this->_customdata['actionconfig']['settings'] ?? [];
        $actionname = $this->_customdata['actionname'];
        $action = $this->_customdata['action'];
        $providername = $this->_customdata['providername'] ?? 'aiprovider_edgellm';
        $providerid = $this->_customdata['providerid'] ?? 0;
        $returnurl = $this->_customdata['returnurl'] ?? null;

        $mform->addElement('header', 'generalsettingsheader', get_string('general', 'core'));

        // Model name. Free text, because the endpoint decides what is valid.
        $mform->addElement('text', 'model', get_string('model', 'aiprovider_edgellm'), 'size="40"');
        $mform->setType('model', PARAM_TEXT);
        $mform->addHelpButton('model', 'model', 'aiprovider_edgellm');
        $mform->setDefault('model', $actionconfig['model'] ?? '');

        // System instruction. Defaults to whatever the action itself defines, so
        // that leaving it alone reproduces core's behaviour exactly.
        $mform->addElement(
            'textarea',
            'systeminstruction',
            get_string('systeminstruction', 'aiprovider_edgellm'),
            'wrap="virtual" rows="5" cols="60"',
        );
        $mform->setType('systeminstruction', PARAM_TEXT);
        $mform->setDefault(
            'systeminstruction',
            $actionconfig['systeminstruction'] ?? $action::get_system_instruction(),
        );
        $mform->addHelpButton('systeminstruction', 'systeminstruction', 'aiprovider_edgellm');

        // Extra parameters, as a JSON object. This is where the methodology's
        // controlled variables live: temperature 0, a fixed max_tokens, and a
        // seed where the runtime supports one.
        $mform->addElement(
            'textarea',
            'modelextraparams',
            get_string('extraparams', 'aiprovider_edgellm'),
            'wrap="virtual" rows="5" cols="60"',
        );
        $mform->setType('modelextraparams', PARAM_TEXT);
        $mform->setDefault('modelextraparams', $actionconfig['modelextraparams'] ?? '');
        $mform->addHelpButton('modelextraparams', 'extraparams', 'aiprovider_edgellm');

        if ($returnurl) {
            $mform->addElement('hidden', 'returnurl', $returnurl);
            $mform->setType('returnurl', PARAM_LOCALURL);
        }

        $mform->addElement('hidden', 'action', $action);
        $mform->setType('action', PARAM_TEXT);

        $mform->addElement('hidden', 'provider', $providername);
        $mform->setType('provider', PARAM_TEXT);

        $mform->addElement('hidden', 'providerid', $providerid);
        $mform->setType('providerid', PARAM_INT);

        $this->set_data($actionconfig);
    }

    #[\Override]
    public function validation($data, $files): array {
        $errors = parent::validation($data, $files);

        // Malformed JSON here would otherwise be silently dropped at request
        // time, and the run would appear to use settings it never applied.
        if (!empty($data['modelextraparams'])) {
            json_decode($data['modelextraparams']);
            if (json_last_error() !== JSON_ERROR_NONE) {
                $errors['modelextraparams'] = get_string('invalidjson', 'aiprovider_edgellm');
            }
        }

        return $errors;
    }
}
