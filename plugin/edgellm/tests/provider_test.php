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

use core_ai\aiactions\summarise_text;
use GuzzleHttp\Psr7\Request;

defined('MOODLE_INTERNAL') || die();
require_once(__DIR__ . '/testcase_helper_trait.php');

/**
 * Provider configuration and authentication.
 *
 * @package    aiprovider_edgellm
 * @copyright  2026 Tai Le Tan
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 * @covers     \aiprovider_edgellm\provider
 */
final class provider_test extends \advanced_testcase {

    use testcase_helper_trait;

    protected function setUp(): void {
        parent::setUp();
        $this->resetAfterTest();
    }

    /**
     * The three actions the study's workload model maps onto, and no more.
     * Image generation is out of scope.
     */
    public function test_action_list(): void {
        $actions = provider::get_action_list();

        $this->assertContains(\core_ai\aiactions\generate_text::class, $actions);
        $this->assertContains(\core_ai\aiactions\summarise_text::class, $actions);
        $this->assertContains(\core_ai\aiactions\explain_text::class, $actions);
        $this->assertNotContains(\core_ai\aiactions\generate_image::class, $actions);
    }

    /**
     * The endpoint is the only genuinely required setting, because the point is
     * talking to a local runtime that needs no key.
     */
    public function test_is_provider_configured_requires_only_an_endpoint(): void {
        $provider = $this->create_provider(summarise_text::class);
        $this->assertTrue($provider->is_provider_configured());

        $unconfigured = $this->create_provider(
            summarise_text::class,
            [],
            ['endpoint' => ''],
        );
        $this->assertFalse($unconfigured->is_provider_configured());
    }

    /**
     * No key configured means no Authorization header at all, rather than an
     * empty bearer token that a strict runtime would reject.
     */
    public function test_no_auth_header_without_an_api_key(): void {
        $provider = $this->create_provider(summarise_text::class);
        $request = new Request('POST', '');

        $result = $provider->add_authentication_headers($request);

        $this->assertFalse($result->hasHeader('Authorization'));
    }

    /**
     * A key is sent as a bearer token, which is what makes the cloud baseline
     * reachable through this same code path.
     */
    public function test_auth_header_with_an_api_key(): void {
        $provider = $this->create_provider(
            summarise_text::class,
            [],
            ['apikey' => 'sk-test-key'],
        );
        $request = new Request('POST', '');

        $result = $provider->add_authentication_headers($request);

        $this->assertEquals('Bearer sk-test-key', $result->getHeaderLine('Authorization'));
    }

    /**
     * Every supported action must return a real settings form. Core calls
     * is_cancelled() on the return value without checking it, so returning false
     * for a supported action would be a fatal error in the admin UI.
     */
    public function test_supported_actions_return_a_settings_form(): void {
        foreach (provider::get_action_list() as $actionclass) {
            $form = provider::get_action_settings($actionclass, ['providerid' => 0]);
            $this->assertInstanceOf(
                \core_ai\form\action_settings_form::class,
                $form,
                "No settings form returned for {$actionclass}",
            );
        }
    }

    /**
     * An unsupported action returns false, as core's own providers do.
     */
    public function test_unsupported_action_returns_false(): void {
        $form = provider::get_action_settings(
            \core_ai\aiactions\generate_image::class,
            ['providerid' => 0],
        );
        $this->assertFalse($form);
    }
}
