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
use core_ai\provider;
use GuzzleHttp\Psr7\Response;

defined('MOODLE_INTERNAL') || die();
require_once(__DIR__ . '/testcase_helper_trait.php');

/**
 * Request construction, response parsing, error mapping and timeout handling.
 *
 * @package    aiprovider_edgellm
 * @copyright  2026 Tai Le Tan
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 * @covers     \aiprovider_edgellm\abstract_processor
 * @covers     \aiprovider_edgellm\process_summarise_text
 */
final class abstract_processor_test extends \advanced_testcase {

    use testcase_helper_trait;

    /** @var provider The provider under test. */
    protected provider $provider;

    /** @var summarise_text The action being processed. */
    protected summarise_text $action;

    protected function setUp(): void {
        parent::setUp();
        $this->resetAfterTest();
        $this->provider = $this->create_provider(summarise_text::class);
        $this->action = new summarise_text(
            contextid: 1,
            userid: 1,
            prompttext: 'This is a test prompt',
        );
    }

    /**
     * Invoke a protected method on a processor.
     *
     * @param string $name Method name.
     * @param array $args Arguments.
     * @param provider|null $provider Provider to use, defaults to the fixture.
     * @return mixed
     */
    private function invoke(string $name, array $args = [], ?provider $provider = null) {
        $processor = new process_summarise_text($provider ?? $this->provider, $this->action);
        $method = new \ReflectionMethod($processor, $name);
        return $method->invoke($processor, ...$args);
    }

    /**
     * The request body must be a chat completion carrying both messages.
     */
    public function test_create_request_object(): void {
        $request = $this->invoke('create_request_object', ['user-abc']);
        $body = json_decode($request->getBody()->getContents());

        $this->assertEquals('POST', $request->getMethod());
        $this->assertEquals('mock-deterministic', $body->model);
        $this->assertEquals('user-abc', $body->user);

        // Non-streaming: Moodle actions return complete text, and the benchmark
        // depends on this path never becoming a stream.
        $this->assertFalse($body->stream);

        $this->assertCount(2, $body->messages);
        $this->assertEquals('system', $body->messages[0]->role);
        $this->assertEquals('user', $body->messages[1]->role);
        $this->assertEquals('This is a test prompt', $body->messages[1]->content);
    }

    /**
     * A configured system instruction must win over the action's default.
     */
    public function test_system_instruction_override(): void {
        $provider = $this->create_provider(
            summarise_text::class,
            ['systeminstruction' => 'Be extremely brief.'],
        );
        $request = $this->invoke('create_request_object', ['user-abc'], $provider);
        $body = json_decode($request->getBody()->getContents());

        $this->assertEquals('Be extremely brief.', $body->messages[0]->content);
    }

    /**
     * With no override, the action's own instruction is used, so the request is
     * identical to what core would send.
     */
    public function test_system_instruction_defaults_to_action(): void {
        $request = $this->invoke('create_request_object', ['user-abc']);
        $body = json_decode($request->getBody()->getContents());

        $this->assertEquals(
            summarise_text::get_system_instruction(),
            $body->messages[0]->content,
        );
    }

    /**
     * Extra parameters are merged into the body. This is how the methodology's
     * controlled variables reach the backend.
     */
    public function test_create_request_object_with_extra_params(): void {
        $provider = $this->create_provider(
            summarise_text::class,
            ['modelextraparams' => json_encode(['temperature' => 0, 'max_tokens' => 300])],
        );
        $request = $this->invoke('create_request_object', ['user-abc'], $provider);
        $body = json_decode($request->getBody()->getContents());

        $this->assertSame(0, $body->temperature);
        $this->assertSame(300, $body->max_tokens);
    }

    /**
     * Malformed extra parameters must be ignored rather than thrown.
     *
     * A configuration mistake that turned into an exception would be recorded as
     * a backend failure, which would corrupt the error rate the study reports.
     */
    public function test_invalid_extra_params_are_ignored(): void {
        $provider = $this->create_provider(
            summarise_text::class,
            ['modelextraparams' => 'this is not json'],
        );
        $request = $this->invoke('create_request_object', ['user-abc'], $provider);
        $body = json_decode($request->getBody()->getContents());

        $this->assertEquals('mock-deterministic', $body->model);
        $this->assertObjectNotHasProperty('temperature', $body);
    }

    /**
     * A well-formed response is mapped onto core's expected keys.
     */
    public function test_handle_api_success(): void {
        $response = new Response(200, ['Content-Type' => 'application/json'], $this->success_body());
        $result = $this->invoke('handle_api_success', [$response]);

        $this->assertTrue($result['success']);
        $this->assertEquals('chatcmpl-mock-1234', $result['id']);
        $this->assertEquals('fp_mock_deterministic', $result['fingerprint']);
        $this->assertEquals('A summary.', $result['generatedcontent']);
        $this->assertEquals('stop', $result['finishreason']);
        $this->assertEquals(22, $result['prompttokens']);
        $this->assertEquals(32, $result['completiontokens']);
        $this->assertEquals('mock-deterministic', $result['model']);
    }

    /**
     * Self-hosted runtimes are not perfectly OpenAI-compatible. Missing optional
     * fields must degrade rather than fail, because a PHP warning here would be
     * counted as a backend error and would distort the error rate.
     */
    public function test_handle_api_success_with_missing_optional_fields(): void {
        $body = json_encode([
            'choices' => [[
                'message' => ['role' => 'assistant', 'content' => 'Terse.'],
            ]],
        ]);
        $response = new Response(200, ['Content-Type' => 'application/json'], $body);
        $result = $this->invoke('handle_api_success', [$response]);

        $this->assertTrue($result['success']);
        $this->assertEquals('Terse.', $result['generatedcontent']);
        $this->assertEquals('stop', $result['finishreason']);
        $this->assertEquals(0, $result['prompttokens']);
        $this->assertEquals(0, $result['completiontokens']);
        // Falls back to the configured model rather than reporting nothing.
        $this->assertEquals('mock-deterministic', $result['model']);
    }

    /**
     * A 200 with no usable content is a failure, not a success with empty text.
     */
    public function test_handle_api_success_without_content_is_an_error(): void {
        $response = new Response(
            200,
            ['Content-Type' => 'application/json'],
            json_encode(['choices' => []]),
        );
        $result = $this->invoke('handle_api_success', [$response]);

        $this->assertFalse($result['success']);
    }

    /**
     * Server errors use the reason phrase; client errors use the message body.
     */
    public function test_handle_api_error(): void {
        $cases = [
            [new Response(500, [], ''), 500],
            [new Response(503, [], ''), 503],
            [
                new Response(401, [], json_encode(['error' => ['message' => 'Invalid key']])),
                401,
            ],
            [
                new Response(429, [], json_encode(['error' => ['message' => 'Slow down']])),
                429,
            ],
        ];

        foreach ($cases as [$response, $expectedcode]) {
            $result = $this->invoke('handle_api_error', [$response]);
            $this->assertFalse($result['success']);
            $this->assertEquals($expectedcode, $result['errorcode']);
        }
    }

    /**
     * A local runtime may return a bare string error rather than OpenAI's
     * envelope. That must not become a PHP error.
     */
    public function test_handle_api_error_with_bare_string_error(): void {
        $response = new Response(400, [], json_encode(['error' => 'model not found']));
        $result = $this->invoke('handle_api_error', [$response]);

        $this->assertFalse($result['success']);
        $this->assertEquals(400, $result['errorcode']);
    }

    /**
     * An empty error body must still map to an error rather than throwing.
     */
    public function test_handle_api_error_with_empty_body(): void {
        $response = new Response(400, [], '');
        $result = $this->invoke('handle_api_error', [$response]);

        $this->assertFalse($result['success']);
        $this->assertEquals(400, $result['errorcode']);
    }

    /**
     * A refused connection has no HTTP status. It must still map to an error
     * rather than escaping as an uncaught exception.
     *
     * This is the failure that matters most in practice: pointing Moodle at a
     * local runtime that is not running. Guzzle ConnectException extends
     * TransferException and is not a RequestException, so a narrower catch
     * misses it entirely.
     */
    public function test_transport_error_without_a_status_maps_to_500(): void {
         = new \GuzzleHttp\Exception\ConnectException(
            'Connection refused',
            new \GuzzleHttp\Psr7\Request('POST', ''),
        );
         = ->invoke('handle_transport_error', []);

        ->assertFalse(['success']);
        ->assertEquals(500, ['errorcode']);
    }

    /**
     * A transport exception that does carry a usable HTTP status keeps it.
     */
    public function test_transport_error_keeps_a_valid_status(): void {
         = new \GuzzleHttp\Exception\TransferException('Gone', 410);
         = ->invoke('handle_transport_error', []);

        ->assertFalse(['success']);
        ->assertEquals(410, ['errorcode']);
    }

    /**
     * The timeout is configurable, because CPU prefill on a long input can take
     * far longer than any cloud API would.
     */
    public function test_timeout_is_configurable(): void {
        $this->assertEquals(30, $this->invoke('get_timeout'));

        $provider = $this->create_provider(summarise_text::class, [], ['timeout' => 900]);
        $this->assertEquals(900, $this->invoke('get_timeout', [], $provider));
    }

    /**
     * With no timeout configured, a sane default applies rather than 0.
     */
    public function test_timeout_default(): void {
        $provider = $this->create_provider(summarise_text::class, [], ['timeout' => null]);
        $this->assertEquals(60, $this->invoke('get_timeout', [], $provider));
    }

    /**
     * Action settings win over provider settings, so one instance can point a
     * single action at a different model.
     */
    public function test_action_model_overrides_provider_model(): void {
        $provider = $this->create_provider(
            summarise_text::class,
            ['model' => 'action-model'],
            ['model' => 'provider-model'],
        );
        $this->assertEquals('action-model', $this->invoke('get_model', [], $provider));
    }
}
