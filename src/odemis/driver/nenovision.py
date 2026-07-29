#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Jul 21, 2026

@author: Nandish Patel

Copyright © 2026 Nandish Patel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

# Odemis backend driver for the NenoVision LiteScope AFM-in-SEM.
# Controls closed-loop piezo stages via an NI mioDAQ.
# CRITICAL WARNING:
# Before initializing or switching to external scan control, the AFM probe MUST
# be retracted from the sample to avoid collisions!

# It's possible to simulate a specific card by obtaining a NCE file, adding
# "DevIsSimulated = 1" to the file content and running:
# nidaqmxconfig --import ni-pci6361-sim.nce --replace
# nidaqmxconfig --import ni-usb6421-sim.nce --replace


import logging
import math
import subprocess
import sys
import threading
import time
from concurrent.futures._base import CancelledError
from typing import Any, Dict, List, Optional, Set, Tuple

import nidaqmx
import numpy
from nidaqmx.constants import AcquisitionType
from nidaqmx.stream_writers import AnalogMultiChannelWriter

from odemis import model
from odemis.model import CancellableFuture, CancellableThreadPoolExecutor, isasync
from odemis.util import driver


class LiteScope(model.Actuator):
    """
    Actuator controlling the LiteScope X and Y closed-loop scanners.

    This component converts spatial positions (e.g., -80µm to 80µm) into DAQ voltages
    (e.g., -10V to 10V) dynamically based on the provided configuration.
    It strictly enforces speed limits using hardware-timed S-curve voltage ramps
    to prevent overshoot and mechanical stress on the piezo stages.
    """

    TARGET_SAMPLE_RATE = 10000.0  # 10 kHz target sample rate for smooth analog output
    MAX_SAMPLES = 2**16  # Maximum number of samples to generate for a single move (16-bit buffer limit)
    # Physical speed limits in m/s (Assuming +/-10V maps to +/-80um)
    MAX_SPEED_ENGAGED_M_S = 20e-6     # 2.5 V/s
    MAX_SPEED_RETRACTED_M_S = 320e-6  # 40.0 V/s
    MIN_SPEED_M_S = 1e-7

    def __init__(self, name: str, role: str, axes: Dict[str, Dict[str, Any]], device: str = "Dev1",
                 settle_time: float = 0.0, inverted: Optional[set] = None, **kwargs: Any) -> None:
        """
        Initializes the LiteScope closed-loop piezo actuator.

        :param name: The name of the component.
        :param role: The role of the component in Odemis.
        :param axes: Odemis configuration dictionary for axes properties
            (must include keys: 'channel', 'speed', 'rng_m', 'rng_v', 'unit').
            The rng_m and rng_v values define the mapping from physical meters to DAQ voltages.
        :param device: NI DAQ device identifier (e.g., "Dev1", "NIUSB-6421").
            Check available devices using the command 'nilsdev'. One can create a simulated device
            using NI Hardware Manager or by importing a simulated NCE file with
            'nidaqmxconfig --import <file> --replace'.
        :param settle_time: Time to wait in seconds after a move finishes before returning.
        :param **kwargs: Additional keyword arguments passed to the parent 'Actuator'.

        :raises: ValueError If axes configurations are missing or the NI-DAQ device is not found.
        """
        if not axes:
            raise ValueError("LiteScope requires axes definition.")
        if inverted:
            raise ValueError("LiteScope does not support inverted axes. Swap rng_v[0] and rng_v[1] instead.")

        self._device: str = device
        self._settle_time: float = settle_time

        self._check_nidaqmx()

        # Connect to the local system and verify the device
        system = nidaqmx.system.System.local()
        try:
            self._nidev = system.devices[self._device]
            hw_version = f"NI {self._nidev.product_type} s/n: {self._nidev.serial_num}"
        except nidaqmx.DaqError:
            raise ValueError(f"Failed to find NI DAQ device '{self._device}'. Please check the connection.")

        self._ao_max_rate: float = self._nidev.ao_max_rate
        self._ao_min_rate: float = self._nidev.ao_min_rate
        logging.debug(f"[{name}] DAQ AO limits: min {self._ao_min_rate} Hz, max {self._ao_max_rate} Hz")

        axes_def: Dict[str, model.Axis] = {}
        self._axes_map: Dict[str, int] = {}
        self._range_v: Dict[str, List[float]] = {}

        self._speed: Dict[str, float] = {}
        self._voltage: Dict[str, float] = {}

        ao_chans_available = [chan.name for chan in self._nidev.ao_physical_chans]

        for ax_name, cfg in axes.items():
            missing = {"channel", "speed", "rng_m", "rng_v", "unit"} - set(cfg.keys())
            if missing:
                raise ValueError(f"Axis '{ax_name}' missing key(s): {', '.join(missing)}")

            if math.isclose(cfg["rng_m"][0], cfg["rng_m"][1]):
                raise ValueError(f"Axis '{ax_name}' rng_m has zero span: {cfg['rng_m']}")
            if math.isclose(cfg["rng_v"][0], cfg["rng_v"][1]):
                raise ValueError(f"Axis '{ax_name}' rng_v has zero span: {cfg['rng_v']}")

            expected_chan = f"{self._device}/ao{cfg['channel']}"
            if expected_chan not in ao_chans_available:
                raise ValueError(f"AO channel '{expected_chan}' not found. Available: {ao_chans_available}")

            self._axes_map[ax_name] = cfg["channel"]
            self._range_v[ax_name] = cfg["rng_v"]
            self._speed[ax_name] = cfg["speed"]

            axes_def[ax_name] = model.Axis(unit=cfg["unit"], range=tuple(cfg["rng_m"]))

            # Initialize safely to the center of the configured voltage range
            self._voltage[ax_name] = (cfg["rng_v"][0] + cfg["rng_v"][1]) / 2

        super().__init__(name, role, axes=axes_def, **kwargs)

        self._metadata: Dict[str, str] = {
            model.MD_HW_NAME: f"NI {self._nidev.product_type}",
            model.MD_SW_VERSION: f"driver {'.'.join(str(v) for v in system.driver_version)}, "
                                    f"linux {'.'.join(str(v) for v in driver.get_linux_version())}",
            model.MD_HW_VERSION: hw_version,
        }

        self._executor = CancellableThreadPoolExecutor(max_workers=1)

        # Initialize tracking VAs
        init_positions = {ax: self._v_to_m(ax, self._voltage[ax]) for ax in axes}
        self.position = model.VigilantAttribute(init_positions, unit="m", readonly=True)

        # Enforce speed limits
        self.speed = model.MultiSpeedVA(
            self._speed,
            [self.MIN_SPEED_M_S, self.MAX_SPEED_RETRACTED_M_S],
            unit="m/s",
            setter=self._set_speed
        )

        # Apply initial physical voltages to hardware
        self._set_voltage(self._voltage)

    @staticmethod
    def _check_nidaqmx() -> None:
        """
        Runs a safe canary check via a subprocess to ensure the NI-DAQmx C-library
        will not segfault the main Odemis Python process if the driver is misconfigured.

        :raises:
            model.HwError: If the NI-DAQmx subprocess returns a fatal exit code.
        """
        canary_cmd = [sys.executable, "-c", "import nidaqmx; all(nidaqmx.system.System.local().devices)"]
        try:
            proc = subprocess.run(canary_cmd, timeout=30)
        except subprocess.TimeoutExpired as ex:
            raise model.HwError("NI-DAQmx canary check timed out.") from ex
        if proc.returncode < 0:
            raise model.HwError("NI-DAQmx failed to load. The C-library crashed. Reboot the computer and try again.")
        elif proc.returncode != 0:
            logging.warning(f"nidaqmx canary exited with code {proc.returncode}")

    def _m_to_v(self, axis: str, val_m: float) -> float:
        """
        Converts physical meters to DAQ voltage for a given axis.

        :param axis: The axis name (e.g., 'x').
        :param val_m: The physical coordinate in meters.

        :returns: The equivalent DAQ voltage.
        """
        rng_m = self.axes[axis].range
        rng_v = self._range_v[axis]
        return rng_v[0] + (val_m - rng_m[0]) / (rng_m[1] - rng_m[0]) * (rng_v[1] - rng_v[0])

    def _v_to_m(self, axis: str, val_v: float) -> float:
        """
        Converts DAQ voltage to physical meters for a given axis.

        :param axis: The axis name (e.g., 'x').
        :param val_v: The DAQ voltage.

        :returns: The equivalent physical coordinate in meters.
        """
        rng_m = self.axes[axis].range
        rng_v = self._range_v[axis]
        return rng_m[0] + (val_v - rng_v[0]) / (rng_v[1] - rng_v[0]) * (rng_m[1] - rng_m[0])

    @staticmethod
    def smooth_step(l: int, start: float, end: float, vstart: float = 0.0, vend: float = 1.0) -> numpy.ndarray:
        """
        Generates a smoothstep (S-curve) interpolation between two values.

        The interpolation follows the cubic smoothstep polynomial
        -2x³ + 3x², producing a trajectory with smooth acceleration and
        deceleration compared to a linear ramp.

        :param l: Number of values (samples) to generate.
        :param start: Normalized interval start (usually near 0.0).
        :param end: Normalized interval end (usually 1.0).
        :param vstart: Physical starting value (voltage).
        :param vend: Physical ending value (voltage).

        :returns: A 1D numpy array of shape (l,) containing the smoothstep values.
        """
        x = numpy.linspace(start, end, l, dtype=numpy.float64)
        b = float(vend) - float(vstart)
        return (-2 * x ** 3 + 3 * x ** 2) * b + vstart

    def _set_speed(self, value: Dict[str, float]) -> Dict[str, float]:
        """
        Setter for the 'MultiSpeedVA' attribute.
        Persists the new per-axis speeds to the internal tracking dict.

        :param value: A dictionary mapping axis names to speeds in m/s.
                    Values are already range-validated by the VA before this is called.
        :returns: The full updated speed dict for all axes.
        """
        self._speed.update({ax: s for ax, s in value.items() if ax in self.axes})
        return self._speed.copy()

    def _configure_task_channels(self, task: nidaqmx.Task, axes: List[str]) -> None:
        """
        Helper to add configured Analog Output (AO) channels to an NI-DAQmx task.

        :param task: The active NI-DAQmx task.
        :param axes: Ordered list of axis names to attach to the task.
        """
        for axis in axes:
            task.ao_channels.add_ao_voltage_chan(
                f"{self._device}/ao{self._axes_map[axis]}",
                min_val=min(self._range_v[axis]),
                max_val=max(self._range_v[axis])
            )

    def _set_voltage(self, volts: Dict[str, float]) -> None:
        """
        Instantly sets the AO channels to a specific voltage using on-demand writing.
        Used for initial positioning only.

        :param volts: A mapping of axis names to target voltages.
        """
        with nidaqmx.Task() as task:
            self._configure_task_channels(task, list(volts.keys()))

            # Extract values in the same order keys were added to the task
            v_array = numpy.array([volts[ax] for ax in volts], dtype=numpy.float64)

            writer = AnalogMultiChannelWriter(task.out_stream)
            writer.auto_start = True
            writer.write_one_sample(v_array)

        self._voltage.update(volts)
        self.position._set_value(
            {ax: self._v_to_m(ax, self._voltage[ax]) for ax in self.axes},
            force_write=True
        )

    def _cancel_current_move(self, future: CancellableFuture) -> bool:
        """Called by CancellableFuture cancel() to interrupt an in-progress move."""
        logging.debug(f"[{self.name}] Cancelling current move...")
        with future._moving_lock:
            future._must_stop.set()
            if future._running_task is not None:
                future._running_task.stop()
        # Wait briefly for the background thread to update position
        future._position_updated.wait(5.0)
        return True

    def _create_future(self) -> CancellableFuture:
        """
        Creates a custom cancellable future for safely interrupting DAQ ramping mid-move.

        :returns: An Odemis future equipped with a threading event and lock.
        """
        f = CancellableFuture()
        f._moving_lock = threading.Lock()
        f._must_stop = threading.Event()
        f._position_updated = threading.Event()
        f._running_task = None
        f.task_canceller = self._cancel_current_move
        return f

    @isasync
    def moveAbs(self, pos: Dict[str, float]) -> CancellableFuture:
        """
        Moves the stage to an absolute spatial position.

        :param pos: A mapping of axis names to target positions in meters.

        :returns: A future representing the ongoing hardware movement.
        """
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        f = self._create_future()
        return self._executor.submitf(f, self._do_move_abs, f, pos)

    @isasync
    def moveRel(self, shift: Dict[str, float]) -> CancellableFuture:
        """
        Moves the stage relative to its current spatial position.

        :param shift: A mapping of axis names to positional shifts in meters.

        :returns: A future representing the ongoing hardware movement.
        """
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        f = self._create_future()
        return self._executor.submitf(f, self._do_move_rel, f, shift)

    def _generate_waveform(self, axes: List[str], start_v: Dict[str, float], target_v: Dict[str, float]) -> Tuple[numpy.ndarray, float, int, float, float]:
        """
        Calculates a smoothstep waveform for the given axes ensuring physical speed
        limits are respected.

        :param axes: Ordered list of axis names involved in the move.
        :param start_v: Starting voltages per axis.
        :param target_v: Target voltages per axis.

        :returns: A tuple containing
            - waveform (2D numpy.ndarray): Shape (num_axes, n_samples).
            - sample_rate (float): The configured hardware clock rate in Hz.
            - n_samples (int): Total number of samples in the waveform.
            - duration (float): Total execution time of the move in seconds.
            - max_dv (float): The largest absolute voltage shift requested across all axes.
        """
        durations = []
        for axis in axes:
            dv = target_v[axis] - start_v[axis]
            rng_m = self.axes[axis].range
            rng_v = self._range_v[axis]
            voltage_per_meter = abs(rng_v[1] - rng_v[0]) / (rng_m[1] - rng_m[0])
            speed_v_s = self.speed.value[axis] * voltage_per_meter
            durations.append(abs(dv) / speed_v_s)

        # Smoothstep peak derivative is 1.5x the average. Multiply duration to ensure safety.
        duration = max(durations) * 1.5
        max_dv = max(abs(target_v[ax] - start_v[ax]) for ax in axes)

        sample_rate = max(self._ao_min_rate, min(self.TARGET_SAMPLE_RATE, self._ao_max_rate))
        n_samples = max(2, math.ceil(duration * sample_rate))
        if n_samples > self.MAX_SAMPLES:
            n_samples = self.MAX_SAMPLES
            sample_rate = n_samples / duration

        # 1.0 / n_samples prevents 1-sample flat spots since DAC is already holding start_v
        waves = [
            self.smooth_step(n_samples, 1.0 / n_samples, 1.0, start_v[ax], target_v[ax])
            for ax in axes
        ]

        return numpy.vstack(waves), sample_rate, n_samples, duration, max_dv

    def _write_ao_finite(self, axes: List[str], waveform: numpy.ndarray, sample_rate: float,
                         n_samples: int, duration: float, future: CancellableFuture) -> int:
        """
        Handles NI-DAQmx hardware execution, polling, and future-cancellation tracking.

        :param axes: Ordered list of axis names matching the row order of waveform.
        :param waveform: The 2D float64 numpy array to stream to the DAC.
        :param sample_rate: The timing rate configured on the hardware clock.
        :param n_samples: Number of samples per channel.
        :param duration: Time required to execute the full waveform.
        :param future: Future storing the cancellation event flag.

        :returns: The number of samples physically generated. If the move was cancelled
                  mid-way, this will be less than 'n_samples'.
        """
        with nidaqmx.Task() as task:
            self._configure_task_channels(task, axes)

            task.timing.cfg_samp_clk_timing(
                rate=sample_rate,
                sample_mode=AcquisitionType.FINITE,
                samps_per_chan=n_samples
            )

            writer = AnalogMultiChannelWriter(task.out_stream)
            writer.auto_start = False
            writer.write_many_sample(waveform, timeout=duration + 1.0)

            try:
                with future._moving_lock:
                    if future._must_stop.is_set():
                        return 0
                    future._running_task = task

                end_time = time.monotonic() + duration
                task.start()

                while not task.is_task_done():
                    remaining = end_time - time.monotonic()
                    sleept = remaining / 2.0 if remaining > 0 else 0.001
                    if future._must_stop.wait(sleept):
                        logging.debug(f"[{self.name}] Move cancelled mid-execution.")
                        break
            finally:
                with future._moving_lock:
                    future._running_task = None
                task.stop()

            return task.out_stream.total_samp_per_chan_generated

    def _do_move_rel(self, future: CancellableFuture, shift: Dict[str, float]) -> None:
        """
        Background thread orchestrator for executing relative stage movements.
        The relative move is executed by recalculating the target absolute position and delegating to _do_move_abs.

        :param future: Future state indicating cancellation events.
        :param shift: A mapping of axis names to positional shifts in meters.
        """
        target = {ax: self._v_to_m(ax, self._voltage[ax]) + sh for ax, sh in shift.items()}
        self._do_move_abs(future, target)

    def _do_move_abs(self, future: CancellableFuture, pos: Dict[str, float]) -> None:
        """
        Background thread orchestrator for executing absolute stage movements.

        :param future: Future state indicating cancellation events.
        :param pos: A mapping of target positions per axis in meters.
        """
        axes = list(pos.keys())

        start_v = {ax: self._voltage[ax] for ax in axes}
        target_v = {ax: self._m_to_v(ax, pos[ax]) for ax in axes}

        waveform, sample_rate, n_samples, duration, max_dv = self._generate_waveform(
            axes, start_v, target_v
        )

        logging.debug(f"[{self.name}] Ramping max {max_dv:.2f}V over {duration:.3f}s ({n_samples} samples).")
        samples_generated = self._write_ao_finite(axes, waveform, sample_rate, n_samples, duration, future)

        # Software state update
        if samples_generated == 0:
            final_v = start_v.copy()
        else:
            if samples_generated >= n_samples:
                final_v = target_v.copy()
            else:
                final_v = {ax: waveform[i, samples_generated - 1] for i, ax in enumerate(axes)}
                logging.info(f"[{self.name}] Move stopped at sample {samples_generated}/{n_samples}. "
                            f"Volts: {', '.join(f'{ax}: {final_v[ax]:.5f}V' for ax in axes)}")

            if self._settle_time > 0:
                logging.debug(f"[{self.name}] Settling for {self._settle_time}s...")
                future._must_stop.wait(self._settle_time)

        self._voltage.update(final_v)
        self.position._set_value(
            {ax: self._v_to_m(ax, self._voltage[ax]) for ax in self.axes},
            force_write=True
        )

        # Signal that the position has been updated, allowing any waiting threads to proceed
        # and check for cancellation after the position update.
        future._position_updated.set()
        if future._must_stop.is_set():
            raise CancelledError()

    def stop(self, axes: Optional[Set[str]] = None) -> None:
        """
        Stops the physical motion of the stage immediately.

        :param axes: Specific axes to halt (stops all by default).
        """
        if self._executor:
            self._executor.cancel()

    def terminate(self) -> None:
        """
        Cleans up the component, halts physical motion, and safely shuts down
        the background executor thread pool.
        """
        if self._executor:
            self.stop()
            self._executor.shutdown(wait=True)
            self._executor = None
        super().terminate()
