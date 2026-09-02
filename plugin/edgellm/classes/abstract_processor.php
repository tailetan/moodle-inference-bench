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

use core\http_client;
use core_ai\process_base;
use GuzzleHttp\Exception\GuzzleException;
use GuzzleHttp\Psr7\Request;
use GuzzleHttp\Psr7\Uri;
use GuzzleHttp\RequestOptions;
use Psr\Http\Message\RequestInterface;
use Psr\Http\Message\ResponseInterface;
use Psr\Http\Message\UriInterface;

/**
 * Shared request and response handling for every action this provider supports.
 *
 * All three supported actions send the same OpenAI-shaped chat completion and
 * parse the same response, differing only in the system instruction the action
 * supplies. So the work lives here once and the concrete processors are empty.
 *
 * @package    aiprovider_edgellm
 * @copyright  2026 Tai Le Tan
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
abstract class abstract_processor extends process_base {

    /** @var int Default request timeout in seconds. */
    private const DEFAULT_TIMEOUT = 60;

    /**
     * Settings for the action being processed.
     *
     * Action settings win over provider settings, so a single provider instance
     * can point one action at a different model without a second instance.
     *
     * @return array
     */
    protected function get_action_settings(): array {
        return $this->provider->actionconfig[$this->action::class]['settings'] ?? [];
    }

    /**
     * The endpoint to call.
     *
     * A full URL including the path, so the provider can address anything that
     * speaks the OpenAI chat completions shape: the benchmark mock, a llama.cpp
     * server, Ollama's compatibility endpoint, or a commercial API for the cloud
     * baseline.
     *
     * @return UriInterface
     */
    protected function get_endpoint(): UriInterface {
        return new Uri($this->provider->config['endpoint']);
    }

    /**
     * The model name to send.
     *
     * @return string
     */
    protected function get_model(): string {
        $settings = $this->get_action_settings();
        return $settings['model'] ?? $this->provider->config['model'] ?? '';
    }

    /**
     * Request timeout in seconds.
     *
     * @return int
     */
    protected function get_timeout(): int {
        return (int)($this->provider->config['timeout'] ?? self::DEFAULT_TIMEOUT);
    }

    /**
     * The system instruction for this action.
     *
     * @return string
     */
    protected function get_system_instruction(): string {
        $settings = $this->get_action_settings();
        if (!empty($settings['systeminstruction'])) {
            return $settings['systeminstruction'];
        }
        return $this->action::get_system_instruction();
    }

    /**
     * Extra model parameters, supplied as a JSON object in the action settings.
     *
     * The methodology fixes temperature at 0 and a constant max_tokens across
     * arms, and this is where those are set. Invalid JSON is ignored rather than
     * thrown, because a malformed setting must not turn into a failed request
     * that would be counted as a backend error.
     *
     * @return array
     */
    protected function get_model_settings(): array {
        $settings = $this->get_action_settings();
        if (empty($settings['modelextraparams'])) {
            return [];
        }
        $params = json_decode($settings['modelextraparams'], true);
        return is_array($params) ? $params : [];
    }

    /**
     * Build the chat completion request.
     *
     * @param string $userid The obfuscated user id.
     * @return RequestInterface
     */
    protected function create_request_object(string $userid): RequestInterface {
        $messages = [];

        $systeminstruction = $this->get_system_instruction();
        if (!empty($systeminstruction)) {
            $messages[] = ['role' => 'system', 'content' => $systeminstruction];
        }
        $messages[] = [
            'role' => 'user',
            'content' => $this->action->get_configuration('prompttext'),
        ];

        $requestobj = [
            'model' => $this->get_model(),
            'messages' => $messages,
            'user' => $userid,
            // Non-streaming. Moodle's AI actions return complete text, so there
            // is no first-token event to observe on this path. The benchmark
            // records TTFT only when driving a runtime directly.
            'stream' => false,
        ];

        foreach ($this->get_model_settings() as $setting => $value) {
            $requestobj[$setting] = $value;
        }

        return new Request(
            method: 'POST',
            uri: '',
            headers: ['Content-Type' => 'application/json'],
            body: json_encode($requestobj, JSON_UNESCAPED_SLASHES),
        );
    }

    #[\Override]
    protected function query_ai_api(): array {
        $request = $this->create_request_object(
            userid: $this->provider->generate_userid($this->action->get_configuration('userid')),
        );
        $request = $this->provider->add_authentication_headers($request);

        $client = \core\di::get(http_client::class);

        // ------------------------------------------------------------------
        // Measurement scaffolding, not shipping behaviour. See instrumentation.
        // These two calls bracket the HTTP boundary and nothing else, which is
        // what makes the recorded value T2 rather than something broader.
        // ------------------------------------------------------------------
        $measure = instrumentation::start();

        try {
            $response = $client->send($request, [
                'base_uri' => $this->get_endpoint(),
                RequestOptions::TIMEOUT => $this->get_timeout(),
                RequestOptions::HTTP_ERRORS => false,
            ]);
        } catch (GuzzleException $e) {
            // GuzzleException, not RequestException. ConnectException extends
            // TransferException directly and is *not* a RequestException, so
            // catching the narrower type lets a refused connection or a DNS
            // failure escape as an uncaught exception. Those are the most likely
            // failures when pointing Moodle at a local runtime that is not
            // running, which makes them the ones that must be handled.
            instrumentation::stop($measure, null, get_class($e));
            return $this->handle_transport_error($e);
        }

        $status = $response->getStatusCode();
        instrumentation::stop($measure, $status);
        // ------------------------------ end ------------------------------

        if ($status === 200) {
            return $this->handle_api_success($response);
        }
        return $this->handle_api_error($response);
    }

    /**
     * Parse a successful chat completion.
     *
     * Fields are read defensively. The provider talks to self-hosted runtimes
     * whose OpenAI compatibility is close but not exact, and a missing optional
     * field must not become a PHP error that gets recorded as a backend failure.
     *
     * @param ResponseInterface $response The response object.
     * @return array
     */
    protected function handle_api_success(ResponseInterface $response): array {
        $bodyobj = json_decode($response->getBody()->getContents());

        if (!isset($bodyobj->choices[0]->message->content)) {
            return \core_ai\error\factory::create(
                500,
                'Response did not contain choices[0].message.content',
            )->get_error_details();
        }

        return [
            'success' => true,
            'id' => $bodyobj->id ?? '',
            'fingerprint' => $bodyobj->system_fingerprint ?? '',
            'generatedcontent' => $bodyobj->choices[0]->message->content,
            'finishreason' => $bodyobj->choices[0]->finish_reason ?? 'stop',
            'prompttokens' => $bodyobj->usage->prompt_tokens ?? 0,
            'completiontokens' => $bodyobj->usage->completion_tokens ?? 0,
            'model' => $bodyobj->model ?? $this->get_model(),
        ];
    }

    /**
     * Map a transport failure onto a core AI error.
     *
     * A connection that was refused, timed out or could not be resolved has no
     * HTTP status: Guzzle reports code 0. Passing that to the error factory
     * would ask it to describe a status that does not exist, so it is mapped to
     * 500, which is what a caller can act on: the backend did not answer.
     *
     * @param \Throwable $e The transport exception.
     * @return array
     */
    protected function handle_transport_error(\Throwable $e): array {
        $code = (int)$e->getCode();
        if ($code < 400 || $code > 599) {
            $code = 500;
        }
        return \core_ai\error\factory::create($code, $e->getMessage())->get_error_details();
    }

    /**
     * Map a non-200 response onto a core AI error.
     *
     * @param ResponseInterface $response The response object.
     * @return array
     */
    protected function handle_api_error(ResponseInterface $response): array {
        $status = $response->getStatusCode();

        if ($status >= 500 && $status < 600) {
            $errormessage = $response->getReasonPhrase();
        } else {
            $bodyobj = json_decode($response->getBody()->getContents());
            // A local runtime may return a bare string or an empty body rather
            // than OpenAI's error envelope.
            $errormessage = $bodyobj->error->message
                ?? $bodyobj->error
                ?? $response->getReasonPhrase();
            if (!is_string($errormessage)) {
                $errormessage = $response->getReasonPhrase();
            }
        }

        return \core_ai\error\factory::create($status, $errormessage)->get_error_details();
    }
}
