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
 * Measurement scaffolding. Not shipping behaviour.
 *
 * This class exists solely so the benchmark can record T2, the latency at the
 * HTTP boundary between this provider and the backend endpoint. Everything to do
 * with measurement lives here, so that the provider's request path contains two
 * obvious calls and nothing else.
 *
 * Three properties are deliberate:
 *
 * **It writes nothing.** No database, no log, no file. A write inside the
 * request would land inside T1, and T1 minus T2 is the study's headline finding,
 * so instrumentation that inflated T1 would corrupt the very number it exists to
 * produce. The measurement is held in a static for the life of the request and
 * read afterwards by the benchmark endpoint.
 *
 * **It is off unless switched on.** The plugin setting `enableinstrumentation`
 * defaults to off. When off, `start()` returns null and `stop()` returns
 * immediately, so a request costs one static property read.
 *
 * **It never affects the response.** Nothing here can change what the provider
 * returns to Moodle, including when it fails.
 *
 * @package    aiprovider_edgellm
 * @copyright  2026 Tai Le Tan
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
final class instrumentation {

    /** @var float|null Backend latency in milliseconds, for the current request. */
    private static ?float $backendms = null;

    /** @var int|null HTTP status returned by the backend, or null if it never answered. */
    private static ?int $status = null;

    /** @var string|null Error class name if the HTTP call threw. */
    private static ?string $errortype = null;

    /** @var bool|null Resolved once per request so the hot path does no config lookup. */
    private static ?bool $enabled = null;

    /**
     * Whether measurement is switched on.
     *
     * @return bool
     */
    public static function enabled(): bool {
        if (self::$enabled === null) {
            self::$enabled = !empty(get_config('aiprovider_edgellm', 'enableinstrumentation'));
        }
        return self::$enabled;
    }

    /**
     * Open the measurement window, immediately before the HTTP call.
     *
     * @return float|null A start timestamp, or null when measurement is off.
     */
    public static function start(): ?float {
        if (!self::enabled()) {
            return null;
        }
        self::$backendms = null;
        self::$status = null;
        self::$errortype = null;
        return microtime(true);
    }

    /**
     * Close the measurement window, immediately after the HTTP call.
     *
     * @param float|null $start The value returned by start().
     * @param int|null $status HTTP status, or null if the call threw.
     * @param string|null $errortype Exception class name, if any.
     */
    public static function stop(?float $start, ?int $status = null, ?string $errortype = null): void {
        if ($start === null) {
            return;
        }
        self::$backendms = (microtime(true) - $start) * 1000;
        self::$status = $status;
        self::$errortype = $errortype;
    }

    /**
     * The measurement taken during this request.
     *
     * @return array{backend_ms: float|null, status: int|null, error_type: string|null}
     */
    public static function get(): array {
        return [
            'backend_ms' => self::$backendms,
            'status' => self::$status,
            'error_type' => self::$errortype,
        ];
    }

    /**
     * Discard any measurement. Used between runs and by tests.
     */
    public static function reset(): void {
        self::$backendms = null;
        self::$status = null;
        self::$errortype = null;
        self::$enabled = null;
    }
}
