#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Jul 21, 2026

@author: Nandish Patel

Copyright © 2026 Nandish Patel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

import logging
import time
import unittest
from concurrent.futures import CancelledError

import numpy

from odemis import model

try:
    from odemis.driver import nenovision
    from odemis.driver.nenovision import MIN_SPEED_M_S, MAX_SPEED_RETRACTED_M_S, LiteScope
except ImportError as ex:
    # Skip tests if nenovision driver is not available (e.g., python3-nidaqmx not installed)
    nenovision = None

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

CONFIG_AXES = {
    "x": {
        "channel": 0,
        "speed": 160e-6,  # m/s
        "rng_m": [-80e-6, 80e-6],
        "rng_v": [-10.0, 10.0],
        "unit": "m",
    },
    "y": {
        "channel": 1,
        "speed": 160e-6,   # m/s
        "rng_m": [-80e-6, 80e-6],
        "rng_v": [-10.0, 10.0],
        "unit": "m",
    },
}

CONFIG_LITESCOPE = {
    "name": "LiteScope",
    "role": "scan-stage",
    "device": "Dev1",
    "settle_time": 0.0,
    "axes": CONFIG_AXES,
}


# ---------------------------------------------------------------------------
# Pure-math tests — no hardware required
# ---------------------------------------------------------------------------

class TestLiteScopeMath(unittest.TestCase):
    """Tests for static / pure-math methods that need no NI-DAQmx hardware."""

    @classmethod
    def setUpClass(cls) -> None:
        if not nenovision:
            raise unittest.SkipTest("nenovision driver is not available. Check if python3-nidaqmx is installed.")

    def test_smooth_step_reaches_vend(self):
        """Last sample must equal vend exactly."""
        for vstart, vend in [(-10.0, 10.0), (5.0, -3.0), (0.0, 0.0)]:
            with self.subTest(vstart=vstart, vend=vend):
                wave = LiteScope.smooth_step(100, 1 / 100, 1.0, vstart, vend)
                self.assertEqual(len(wave), 100)
                self.assertAlmostEqual(wave[-1], vend, places=10)

    def test_smooth_step_monotonic_positive(self):
        """Waveform is non-decreasing when vend > vstart."""
        wave = LiteScope.smooth_step(500, 1 / 500, 1.0, -10.0, 10.0)
        self.assertTrue(numpy.all(numpy.diff(wave) >= 0))

    def test_smooth_step_monotonic_negative(self):
        """Waveform is non-increasing when vend < vstart."""
        wave = LiteScope.smooth_step(500, 1 / 500, 1.0, 10.0, -10.0)
        self.assertTrue(numpy.all(numpy.diff(wave) <= 0))

    def test_smooth_step_constant_when_equal(self):
        """vstart == vend must produce a flat array."""
        wave = LiteScope.smooth_step(50, 1 / 50, 1.0, 7.0, 7.0)
        numpy.testing.assert_array_almost_equal(wave, numpy.full(50, 7.0))

    def test_smooth_step_s_curve_peak_near_center(self):
        """Peak derivative (max step) should occur near the midpoint."""
        wave = LiteScope.smooth_step(1000, 1 / 1000, 1.0, 0.0, 1.0)
        peak_idx = int(numpy.argmax(numpy.diff(wave)))
        self.assertGreater(peak_idx, 300, "Peak too early — not an S-curve")
        self.assertLess(peak_idx, 700, "Peak too late — not an S-curve")

    def test_smooth_step_minimum_samples(self):
        """l=2 (NI FINITE minimum) must work without error."""
        wave = LiteScope.smooth_step(2, 0.5, 1.0, -10.0, 10.0)
        self.assertEqual(len(wave), 2)
        self.assertAlmostEqual(wave[-1], 10.0, places=10)

    def test_smooth_step_peak_velocity_factor(self):
        """
        The smoothstep derivative peaks at 1.5x the average velocity.
        Verify the max step / mean step ratio is close to 1.5.
        """
        n = 10000
        wave = LiteScope.smooth_step(n, 1 / n, 1.0, 0.0, 1.0)
        diffs = numpy.diff(wave)
        ratio = diffs.max() / diffs.mean()
        self.assertAlmostEqual(ratio, 1.5, delta=0.05)


# ---------------------------------------------------------------------------
# Integration tests — require a NI DAQ device (real or simulated)
# ---------------------------------------------------------------------------

class TestLiteScope(unittest.TestCase):
    """Integration tests for the LiteScope actuator driver."""

    @classmethod
    def setUpClass(cls):
        if not nenovision:
            raise unittest.SkipTest("nenovision driver is not available. Check if python3-nidaqmx is installed.")
        try:
            cls.scan_stage = LiteScope(**CONFIG_LITESCOPE)
        except (model.HwError, ValueError) as ex:
            raise unittest.SkipTest(f"Cannot connect to NI DAQ device: {ex}")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "scan_stage"):
            cls.scan_stage.terminate()

    def setUp(self):
        """Return to centre before every test."""
        self.scan_stage.speed.value = {ax_name: ax_conf["speed"] for ax_name, ax_conf in CONFIG_AXES.items()}
        f = self.scan_stage.moveAbs({"x": 0.0, "y": 0.0})
        f.result(timeout=30)

    def test_meters_to_voltage_at_limits(self):
        """Physical range limits must map exactly to voltage range limits."""
        for ax in ("x", "y"):
            rng_m = CONFIG_AXES[ax]["rng_m"]
            rng_v = CONFIG_AXES[ax]["rng_v"]
            self.assertAlmostEqual(self.scan_stage._meters_to_voltage(ax, rng_m[0]), rng_v[0], places=10)
            self.assertAlmostEqual(self.scan_stage._meters_to_voltage(ax, rng_m[1]), rng_v[1], places=10)

    def test_voltage_to_meters_at_limits(self):
        """Voltage range limits must map exactly to physical range limits."""
        for ax in ("x", "y"):
            rng_m = CONFIG_AXES[ax]["rng_m"]
            rng_v = CONFIG_AXES[ax]["rng_v"]
            self.assertAlmostEqual(self.scan_stage._voltage_to_meters(ax, rng_v[0]), rng_m[0], places=10)
            self.assertAlmostEqual(self.scan_stage._voltage_to_meters(ax, rng_v[1]), rng_m[1], places=10)

    def test_meters_to_voltage_roundtrip(self):
        """m -> V -> m must recover the original value for arbitrary positions."""
        for ax in ("x", "y"):
            for val_m in [-80e-6, -40e-6, 0.0, 40e-6, 80e-6]:
                with self.subTest(axis=ax, val_m=val_m):
                    self.assertAlmostEqual(
                        self.scan_stage._voltage_to_meters(ax, self.scan_stage._meters_to_voltage(ax, val_m)),
                        val_m,
                        places=15,
                    )

    def test_position_va_within_range(self):
        """position VA must be present for all axes and within configured range."""
        pos = self.scan_stage.position.value
        for ax_name, ax_conf in CONFIG_AXES.items():
            self.assertIn(ax_name, pos)
            rng = ax_conf["rng_m"]
            self.assertGreaterEqual(pos[ax_name], rng[0])
            self.assertLessEqual(pos[ax_name], rng[1])

    def test_speed_va_exists_for_all_axes(self):
        """speed VA must contain an entry for every configured axis."""
        for ax in CONFIG_AXES:
            self.assertIn(ax, self.scan_stage.speed.value)

    def test_move_abs_both_axes(self):
        """moveAbs with both axes should update both positions."""
        target = {"x": -30e-6, "y": 40e-6}
        f = self.scan_stage.moveAbs(target)
        f.result(timeout=30)
        for ax, val in target.items():
            self.assertAlmostEqual(self.scan_stage.position.value[ax], val, places=8)

    def test_move_abs_single_axis_does_not_disturb_other(self):
        """moveAbs with one axis must leave the other axis unchanged."""
        y_before = self.scan_stage.position.value["y"]
        f = self.scan_stage.moveAbs({"x": 20e-6})
        f.result(timeout=30)
        self.assertAlmostEqual(self.scan_stage.position.value["x"], 20e-6, places=8)
        self.assertAlmostEqual(self.scan_stage.position.value["y"], y_before, places=8)

    def test_move_abs_to_range_limits(self):
        """Stage must reach the minimum and maximum of the configured range."""
        for ax in ("x", "y"):
            rng = CONFIG_AXES[ax]["rng_m"]
            for limit in rng:
                with self.subTest(axis=ax, limit=limit):
                    f = self.scan_stage.moveAbs({ax: limit})
                    f.result(timeout=60)
                    self.assertAlmostEqual(self.scan_stage.position.value[ax], limit, places=8)
                    f = self.scan_stage.moveAbs({ax: 0.0})
                    f.result(timeout=60)

    def test_move_abs_empty_returns_instant_future(self):
        """moveAbs({}) must return an InstantaneousFuture without moving."""
        pos_before = self.scan_stage.position.value.copy()
        f = self.scan_stage.moveAbs({})
        self.assertIsInstance(f, model.InstantaneousFuture)
        for ax in pos_before:
            self.assertAlmostEqual(self.scan_stage.position.value[ax], pos_before[ax])

    def test_move_rel_positive_shift(self):
        """Positive relative shift must increase position by exactly that amount."""
        x_before = self.scan_stage.position.value["x"]
        shift = 15e-6
        f = self.scan_stage.moveRel({"x": shift})
        f.result(timeout=30)
        self.assertAlmostEqual(self.scan_stage.position.value["x"], x_before + shift, places=8)

    def test_move_rel_negative_shift(self):
        """Negative relative shift must decrease position."""
        x_before = self.scan_stage.position.value["x"]
        shift = -15e-6
        f = self.scan_stage.moveRel({"x": shift})
        f.result(timeout=30)
        self.assertAlmostEqual(self.scan_stage.position.value["x"], x_before + shift, places=8)

    def test_move_rel_empty_returns_immediately(self):
        """moveRel({}) must return immediately."""
        f = self.scan_stage.moveRel({})
        self.assertTrue(f.done())

    def test_move_rel_accumulates_correctly(self):
        """Two sequential relative moves should accumulate."""
        f1 = self.scan_stage.moveRel({"x": 10e-6})
        f2 = self.scan_stage.moveRel({"x": 10e-6})
        f2.result(timeout=30)
        self.assertTrue(f1.done())
        self.assertTrue(f2.done())
        self.assertAlmostEqual(self.scan_stage.position.value["x"], 20e-6, places=8)

    def test_cancel_future_interrupts_move(self):
        """Cancelling the future directly must halt the move and leave the scan_stage operable."""
        self.scan_stage.speed.value = {"x": MIN_SPEED_M_S * 100, "y": MIN_SPEED_M_S * 100}
        target = {"x": 80e-6, "y": 80e-6}
        f = self.scan_stage.moveAbs(target)
        time.sleep(0.5)
        f.cancel()

        try:
            f.result(timeout=30)
        except CancelledError:
            logging.debug("Move was cancelled.")
            pass

        self.assertTrue(f.cancelled())

        # Should have moved a little before cancellation
        pos = self.scan_stage.position.value
        self.assertGreater(pos["x"], 0.0)
        self.assertGreater(pos["y"], 0.0)

        # Stage must still accept new moves after cancellation
        f2 = self.scan_stage.moveAbs({"x": 0.0, "y": 0.0})
        f2.result(timeout=30)
        self.assertAlmostEqual(self.scan_stage.position.value["x"], 0.0, places=8)
        self.assertAlmostEqual(self.scan_stage.position.value["y"], 0.0, places=8)

    def test_stop_interrupts_move(self):
        """stop() should interrupt a long move before it reaches the target.
        """
        # Use a slow speed so the move takes several seconds
        self.scan_stage.speed.value = {"x": MIN_SPEED_M_S * 100, "y": MIN_SPEED_M_S * 100}
        target = {"x": 80e-6, "y": 80e-6}
        f = self.scan_stage.moveAbs(target)
        time.sleep(0.05)
        self.scan_stage.stop()

        try:
            f.result(timeout=30)
        except CancelledError:
            logging.debug("Move was cancelled by stop().")
            pass

        # Should have moved a little before stop() was called
        pos = self.scan_stage.position.value
        self.assertGreater(pos["x"], 0.0)
        self.assertGreater(pos["y"], 0.0)

    def test_speed_va_rejects_above_max(self):
        """MultiSpeedVA must reject values above MAX_SPEED_RETRACTED_M_S."""
        over = MAX_SPEED_RETRACTED_M_S * 10
        with self.assertRaises(IndexError):
            self.scan_stage.speed.value = {"x": over, "y": over}

    def test_speed_va_rejects_below_min(self):
        """MultiSpeedVA must reject values below MIN_SPEED_M_S."""
        under = MIN_SPEED_M_S / 10
        with self.assertRaises(IndexError):
            self.scan_stage.speed.value = {"x": under, "y": under}

    def test_position_va_notifies_on_move(self):
        """position VA must call subscribers at least once after a completed move."""
        updates = []

        def on_pos(pos):
            updates.append(pos.copy())

        self.scan_stage.position.subscribe(on_pos)
        try:
            f = self.scan_stage.moveAbs({"x": 25e-6})
            f.result(timeout=30)
        finally:
            self.scan_stage.position.unsubscribe(on_pos)

        self.assertGreater(len(updates), 0)
        # Last update must reflect the final position
        self.assertAlmostEqual(updates[-1]["x"], 25e-6, places=8)

    def test_init_raises_on_missing_axis_key(self):
        """Missing required axis key must raise ValueError before touching hardware."""
        bad_axes = {
            "x": {
                "channel": 0,
                "speed": 50e-6,
                "rng_m": [-80e-6, 80e-6],
                # "rng_v" intentionally omitted
                "unit": "m",
            }
        }
        with self.assertRaises(ValueError):
            LiteScope(name="bad", role="test", axes=bad_axes, device="Dev1")

    def test_init_raises_on_empty_axes(self):
        """Empty axes dict must raise ValueError."""
        with self.assertRaises(ValueError):
            LiteScope(name="bad", role="test", axes={}, device="Dev1")

    def test_init_raises_on_nonexistent_device(self):
        """Non-existent NI device name must raise ValueError."""
        with self.assertRaises(ValueError):
            LiteScope(
                name="bad",
                role="test",
                axes=CONFIG_AXES,
                device="DevNonExistent_XYZ_999",
            )

    def test_scan_full_range_sequential_small_moves(self):
        """Scan X from -80µm to +80µm in 1µm steps with 1ms between each move."""
        self.scan_stage.speed.value = {
            "x": MAX_SPEED_RETRACTED_M_S,
            "y": MAX_SPEED_RETRACTED_M_S,
        }
        f = self.scan_stage.moveAbs({"x": -80e-6})
        f.result(timeout=60)

        step = 1e-6
        positions = numpy.arange(-80e-6, 80e-6, step)

        for target_x in positions:
            f = self.scan_stage.moveAbs({"x": float(target_x)})
            f.result(timeout=10)
            time.sleep(0.001)

        self.assertAlmostEqual(self.scan_stage.position.value["x"], 80e-6, places=7)

    def test_move_start_stop_overhead_full_range(self):
        """Per-move infrastructure overhead (NI task setup/teardown) should average under 15ms."""
        self.scan_stage.speed.value = {
            "x": MAX_SPEED_RETRACTED_M_S,
            "y": MAX_SPEED_RETRACTED_M_S,
        }
        f = self.scan_stage.moveAbs({"x": -80e-6})
        f.result(timeout=60)

        step = 1e-6
        positions = numpy.arange(-80e-6, 80e-6, step)

        overheads = []

        for i, x_target_m in enumerate(positions):
            x_start_m = self.scan_stage.position.value["x"]
            x_start_v = {"x": self.scan_stage._meters_to_voltage("x", x_start_m)}
            x_target_v = {"x": self.scan_stage._meters_to_voltage("x", x_target_m)}

            _, _, _, duration, _ = self.scan_stage._generate_waveform(["x"], x_start_v, x_target_v)

            t0 = time.monotonic()
            f = self.scan_stage.moveAbs({"x": float(x_target_m)})
            f.result(timeout=5)
            elapsed = time.monotonic() - t0
            overhead = elapsed - duration
            logging.info(f"Move {i+1}/{len(positions)}: elapsed={elapsed:.4f}s, theoretical={duration:.4f}s, overhead={overhead*1000:.3f}ms")
            overheads.append(overhead)

        mean_overhead_ms = numpy.mean(overheads) * 1000
        logging.info(f"Mean move overhead: {mean_overhead_ms:.3f} ms (target < 15 ms)")
        self.assertLess(mean_overhead_ms, 15.0,
                        f"Mean overhead {mean_overhead_ms:.2f}ms exceeds 15ms — consider optimising NI task reuse.")

    @unittest.skip("debug visualization — run manually only")
    def test_debug_plot_voltage_curve_0_to_80um(self):
        """Plots the smoothstep voltage waveform for an x-axis move from 0 to 80 µm."""
        import matplotlib.pyplot as plt

        x_start_m, x_target_m = 0.0, 80e-6
        x_start_v = {"x": self.scan_stage._meters_to_voltage("x", x_start_m)}
        x_target_v = {"x": self.scan_stage._meters_to_voltage("x", x_target_m)}

        waveform, sample_rate, n_samples, duration, _ = self.scan_stage._generate_waveform(
            ["x"], x_start_v, x_target_v
        )

        t = numpy.linspace(0, duration, n_samples)
        x_voltage_wave = waveform[0]  # row 0 = x-axis channel

        fig, (ax_voltage, ax_dvdt) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)

        ax_voltage.plot(t * 1e3, x_voltage_wave, color="steelblue")
        ax_voltage.set_ylabel("Voltage (V)")
        ax_voltage.set_title(f"X-axis voltage: {x_start_m*1e6:.0f} µm → {x_target_m*1e6:.0f} µm  "
                             f"({x_start_v['x']:.2f} V → {x_target_v['x']:.2f} V)")
        ax_voltage.grid(True)

        ax_dvdt.plot(t[:-1] * 1e3, numpy.diff(x_voltage_wave) * sample_rate, color="darkorange")
        ax_dvdt.set_ylabel("dV/dt  (V/s)")
        ax_dvdt.set_xlabel("Time (ms)")
        ax_dvdt.set_title("X-axis voltage rate of change (derivative)")
        ax_dvdt.grid(True)

        plt.tight_layout()
        plt.show()


if __name__ == "__main__":
    unittest.main()
