<?php
// Fixed-latency stand-in for a model backend, served by PHP's built-in server.
//
// This exists to measure the web server, not Moodle and not a model. It sleeps
// for a configured number of milliseconds and returns an OpenAI-shaped body.
// Because the work is a sleep of known duration, any latency above that sleep
// is queueing rather than computation, which is exactly the quantity spike 2 is
// looking for.
//
// Served as the router script for `php -S`, so it answers every path.

$sleepms = (int)(getenv('SPIKE_SLEEP_MS') ?: 410);
$tokens = (int)(getenv('SPIKE_TOKENS') ?: 32);

usleep($sleepms * 1000);

header('Content-Type: application/json');

echo json_encode([
    'id' => 'chatcmpl-spike',
    'object' => 'chat.completion',
    'created' => time(),
    'model' => 'php-fixed-latency',
    'system_fingerprint' => 'fp_spike',
    'choices' => [[
        'index' => 0,
        'message' => ['role' => 'assistant', 'content' => str_repeat('lorem ', $tokens)],
        'finish_reason' => 'stop',
    ]],
    'usage' => [
        'prompt_tokens' => 22,
        'completion_tokens' => $tokens,
        'total_tokens' => 22 + $tokens,
    ],
], JSON_UNESCAPED_SLASHES);
