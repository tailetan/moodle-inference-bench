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
 * The T2 measurement scaffolding.
 *
 * @package    aiprovider_edgellm
 * @copyright  2026 Tai Le Tan
 * @license    http://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 * @covers     \aiprovider_edgellm\instrumentation
 */
final class instrumentation_test extends \advanced_testcase {

    protected function setUp(): void {
        parent::setUp();
        $this->resetAfterTest();
        instrumentation::reset();
    }

    protected function tearDown(): void {
        instrumentation::reset();
        parent::tearDown();
    }

    /**
     * Measurement must be off unless switched on. It is scaffolding, and a site
     * that installed this plugin by accident should pay nothing for it.
     */
    public function test_disabled_by_default(): void {
        $this->assertFalse(instrumentation::enabled());
        $this->assertNull(instrumentation::start());

        // stop() on a null start must be a no-op rather than recording zero.
        instrumentation::stop(null, 200);
        $this->assertNull(instrumentation::get()['backend_ms']);
    }

    /**
     * When enabled, a measurement is taken and the backend status recorded.
     */
    public function test_records_when_enabled(): void {
        set_config('enableinstrumentation', 1, 'aiprovider_edgellm');
        instrumentation::reset();

        $this->assertTrue(instrumentation::enabled());

        $start = instrumentation::start();
        $this->assertNotNull($start);
        usleep(20000);
        instrumentation::stop($start, 200);

        $measurement = instrumentation::get();
        $this->assertNotNull($measurement['backend_ms']);
        // Measured, not asserted precisely: this checks the clock is running,
        // not how fast the machine is.
        $this->assertGreaterThan(15, $measurement['backend_ms']);
        $this->assertEquals(200, $measurement['status']);
        $this->assertNull($measurement['error_type']);
    }

    /**
     * A failed call records the exception type and leaves the status null, so a
     * transport failure is distinguishable from an HTTP error response.
     */
    public function test_records_transport_failure(): void {
        set_config('enableinstrumentation', 1, 'aiprovider_edgellm');
        instrumentation::reset();

        $start = instrumentation::start();
        instrumentation::stop($start, null, 'GuzzleHttp\Exception\ConnectException');

        $measurement = instrumentation::get();
        $this->assertNotNull($measurement['backend_ms']);
        $this->assertNull($measurement['status']);
        $this->assertEquals('GuzzleHttp\Exception\ConnectException', $measurement['error_type']);
    }

    /**
     * Starting a new measurement clears the previous one, so a request can never
     * report the timing of the one before it.
     */
    public function test_start_clears_previous_measurement(): void {
        set_config('enableinstrumentation', 1, 'aiprovider_edgellm');
        instrumentation::reset();

        instrumentation::stop(instrumentation::start(), 500, 'first');
        $this->assertEquals(500, instrumentation::get()['status']);

        instrumentation::start();
        $this->assertNull(instrumentation::get()['backend_ms']);
        $this->assertNull(instrumentation::get()['status']);
        $this->assertNull(instrumentation::get()['error_type']);
    }
}
