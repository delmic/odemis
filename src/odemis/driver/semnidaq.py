"""
Created on Jun 12, 2023

@author: Éric Piel

Copyright © 2023 Éric Piel, Delmic

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

# This is a module to drive a scanning electron microscope via the analog
# inputs. It uses a National Instrument (NI) Digital-Analog conversion and
# acquisition (DAQ) card on the computer side to control the X/Y position of the
# electron beam (e-beam), while receiving the intensity sent by the secondary
# electron and/or backscatter detector (or any other detector reporting a
# voltage). The DAQ card is controlled via the NI DAQmx driver and framework.
#
# Although it should in theory be quite generic, this driver is only tested on
# Ubuntu 20.04, with nidaqmx 0.7 and a NI PCIe 6361 DAQ card.
#
# From the point of view of Odemis, this driver provides several HwComponents.
# The e-beam position control is represented by an Scanner (Emitter) component,
# while each detector is represented by a separate Detector device.
#
# The pin connection is configurable, but it typically resembles this, on a NI PCIe 6361:
# Scanner X : AO0/AO GND = pins 22/55
# Scanner Y : AO1/AO GND = pins 21/54
# SED : AI1/AI GND = pins 33/32
# BSD : AI2/AI GND = pins 65/64
#
# To install the NI driver, here is a summarry of the commands needed:
# Download from https://www.ni.com/nl-nl/support/documentation/supplemental/18/downloading-and-installing-ni-driver-software-on-linux-desktop.html
# sudo apt install ./ni-ubuntu2004-drivers-stream.deb
# sudo apt update
# sudo apt install ni-daqmx ni-hwcfg-utility
# sudo dkms autoinstall
# pip3 install nidaqmx
#
# It's possible to simulate a specific card by obtaining a NCE file, adding
# "DevIsSimulated = 1" to the file content and running:
# nidaqmxconfig --import ni-pci6361-sim.nce --replace
#
# The documentation is found mostly in four places:
# * DAQ board documentation: X series user manual.pdf
#   https://www.ni.com/docs/en-US/bundle/pcie-pxie-usb-63xx-features/resource/370784k.pdf
# * NI-DAQmx manual
#   https://www.ni.com/docs/en-US/bundle/ni-daqmx/page/daqhelp/daqhelp.html
# * NI-DAQmx C reference
#   https://www.ni.com/docs/en-US/bundle/ni-daqmx-c-api-ref/page/cdaqmx/help_file_title.html
# * NI-DAQmx Python wrapper
#   https://nidaqmx-python.readthedocs.io/en/latest/index.html

import enum
import functools
import gc
import logging
import math
import queue
import subprocess
import sys
import threading
import time
import warnings
import weakref
from collections.abc import Iterable
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Set

import nidaqmx  # Tested with 0.9.0-dev (August 2023), modified to accept
import numpy
from nidaqmx.constants import (AcquisitionType, DigitalWidthUnits,
                               LineGrouping, TerminalConfiguration,
                               VoltageUnits, RegenerationMode, Edge, CountDirection,
                               TriggerType)
from nidaqmx.stream_readers import CounterReader
from numpy.polynomial import polynomial

from odemis import model, util
from odemis.model import roattribute, oneway
from odemis.util import driver, get_best_dtype_for_acc

logging.captureWarnings(True)  # Log the DAQmx warnings


MAX_GC_PERIOD = 10  # s, maximum time elapsed before running the garbage collector

# How long to wait before indicating that the scan is complete (on the "slow TTL" signals)
# It's not immediate, because if immediately after a new acquisition is requested,
# first we have to wait for "scan_delay_time", and second some hardware get confused/unhappy
# by frequent active/inactive status.
AFTER_SCAN_DELAY = 0.1  # s

# Rough duration of the AI/AO buffer, during which the command loop is not checked.
# The exact duration will depend on the data. It's good to not be too short, otherwise too much CPU
# is used, and it shouldn't be too long otherwise the NI DAQ might not have an internal buffer
# long enough, and the request to stop acquisition might suffer latency.
BUFFER_DURATION = 0.1  # s

# Any settings with frame shorter than below will not be handled by the "standard" continuous
# acquisition, and instead be handled by the "synchronized" acquisition (ie, 1 frame at a time).
# It doesn't have to be very accurate as both methods work. The continuous method is faster and
# avoid some artifacts. It is better give a slightly too short time, and if a frame rate still cannot
# be acquired by continuous acquisition, the method will be switched automatically when detecting the
# issue (but the beginning of the scan will be discarded).
MIN_FRAME_DURATION_CONT_ACQ = 1e-3  # s



class AnalogSEM(model.HwComponent):
    """
    A generic HwComponent which provides children for controlling the scanning
    area and receiving the data from the detector of a SEM via NI-DAQmx.
    """

    def __init__(self, name: str, role: str, children: Dict[str, Dict],
                 device: str,
                 multi_detector_min_period: Optional[float] = None,
                 daemon=None, **kwargs):
        """
        :param children: (internal role -> kwargs) parameters setting for the children.
        Known children are "scanner" (see Scanner), "detector0", "detector1"..., for analog detectors
        (see AnalogDetector), and "counter0", "counter1"... for counting detectors (see CountingDetector).
        They will be provided back in the .children roattribute.
        :param device: name of the NI DAQ device (ex: "Dev1"). (Can be looked up via the `nilsdev` command).
        :param multi_detector_min_period: minimum sampling period (in s) for acquisition when multiple
        detectors are acquiring. Increasing it can reduce the cross-talk. Default is the minimum period
        of the DAQ board. It's typically in the order of 1µs.
        :raise:
            Exception if the device cannot be opened.
        """
        if not isinstance(device, str):
            raise ValueError(f"The device argument must be a string but is a {type(device)}")
        self._device_name = device

        self._check_nidaqmx()

        # Check the device is present, and read its basic properties
        # Checks for the presence of AI, AO, DO will be done by the respective children

        # we will fill the set of children with Components later in ._children
        model.HwComponent.__init__(self, name, role, daemon=daemon, **kwargs)

        system = nidaqmx.system.System.local()

        try:
            self._nidev = system.devices[device]  # This accepts anything
            # If device doesn't exist, it will fail on first usage of _nidev... hence we do it now
            self._hwVersion = f"NI {self._nidev.product_type} s/n: {self._nidev.serial_num}"
        except nidaqmx.DaqError:
            raise ValueError(f"Failed to find device {device}")

        lnx_ver = ".".join(str(v) for v in driver.get_linux_version())
        driver_ver = ".".join(str(v) for v in system.driver_version)
        self._swVersion = f"driver {driver_ver}, linux {lnx_ver}"
        self._metadata = {
            model.MD_HW_NAME: f"NI {self._nidev.product_type}",
            model.MD_SW_VERSION: self._swVersion,
            model.MD_HW_VERSION: self._hwVersion,
        }

        if multi_detector_min_period is None:
            # Use the very minimum period of the board if not specified
            # Note that it may still give warnings
            self._multi_detector_min_period = 1 / self._nidev.ai_max_multi_chan_rate
        elif not isinstance(multi_detector_min_period, (float, int)):
            raise ValueError(f"multi_detector_min_period must be a number, but is {type(multi_detector_min_period)}")
        elif not 0 <= multi_detector_min_period <= 1e-3:
            raise ValueError(f"multi_detector_min_period must be between 0 and 1e-3, but is {multi_detector_min_period}")
        else:
            self._multi_detector_min_period = multi_detector_min_period

        # create the scanner child "scanner" (must be before the detectors)
        try:
            ckwargs = children["scanner"]
        except (KeyError, TypeError):
            raise ValueError(f"AnalogSEM device '{device}' was not given a 'scanner' child")
        self._scanner = Scanner(parent=self, daemon=daemon, **ckwargs)
        self.children.value.add(self._scanner)

        self._analog_dets = []
        for name, ckwargs in children.items():
            if name.startswith("detector"):
                d = AnalogDetector(parent=self, daemon=daemon, **ckwargs)
                self.children.value.add(d)
                self._analog_dets.append(d)

        # We need at least one analog detector. Even if there is a counter, we always need
        # an analog detector, which may be physically connected to nothing.
        if len(self._analog_dets) < 1:
            raise ValueError(f"AnalogSEM device '{device}' was not given any detector")

        self._counting_dets = []
        for name, ckwargs in children.items():
            if name.startswith("counter"):
                d = CountingDetector(parent=self, daemon=daemon, **ckwargs)
                self.children.value.add(d)
                self._counting_dets.append(d)

        self._acquirer = Acquirer(self, self._scanner)

        # Only run the garbage collector when we decide. It can block all the threads
        # for ~15ms, which is quite long if we are about to acquire for a short time.
        gc.disable()
        self._last_gc = time.time()

    def terminate(self):
        if self._acquirer:
            self._acquirer.terminate()
            self._acquirer = None
        for c in self.children.value:
            c.terminate()
        super().terminate()

    @staticmethod
    def _check_nidaqmx() -> None:
        """
        Check that the nidaqmx installation is working.
        In particular, it can detect if the NIDAQmx library is not compatible with the kernel drivers.
        :raises:
            HwError: if the installation has some issue
        """
        # Normally does nothing, but will fail if NI-DAQmx is not ready
        canary_cmd = [sys.executable, "-c", "import nidaqmx; nidaqmx.system.System.local().devices"]
        process = subprocess.run(canary_cmd)
        return_code = process.returncode

        if return_code == 0:
            logging.debug("nidaqmx canary went fine")
        else:
            # Check if the process exited due to a signal
            if return_code < 0:
                # Typically that's because the nidadmx C libary failed to load and kill the whole
                # process with SIGABRT (6) to indicate something is wrong.
                # That happens in particular if the NI-DAQmx has been updated, and the old drivers
                # are still loaded in the kernel. In such case, either the old drivers must be
                # unloaded (rmmod) and then the new version reloaded (modprobe), or just rebooting
                # the computer.
                logging.debug(f"nidaqmx canary exited due to signal {- return_code}")
                raise model.HwError("NI-DAQmx failed to load, reboot the computer and try again.")
            else:
                logging.warning(f"nidaqmx canary exited with code {return_code}")
                # For now, just let it pass... but we probably want to try more to find out what is wrong
                # Example: report a nice error if the libnidaqmx.so is not installed (ie only nidaqmx python)

    def _gc_while_waiting(self, max_time=None):
        """
        May or may not run the garbage collector.
        Note: running the garbage collector blocks all the threads
        max_time (float or None): maximum time it's allow to take.
            If None, consider we can always run it.
        """
        gen = 2  # That's all generations

        if max_time is not None:
            # No need if we already run
            if time.time() < self._last_gc + MAX_GC_PERIOD:
                return

            # The garbage collector with generation 2 takes ~15ms, but gen 0 & 1
            # it's a lot faster (<0.5 ms). So play safe, and only GC on gen 0
            # if less than 100 ms of budget.
            if max_time < 0.1:
                gen = 0

        start_gc = time.time()
        gc.collect(gen)
        self._last_gc = time.time()
        logging.debug("GC at gen %d took %g ms", gen, (self._last_gc - start_gc) * 1000)

    def get_min_dwell_time(self, n_ai: int) -> float:
        """
        find the minimum possible dwell time when acquiring with the given number
        of AI channels. Typically, with more AI channels, the minimum dwell time
        increases.
        :param n_ai: (1 <= int) the number of simultaneous AI acquisitions
        :return: minimum dwell time in s
        """
        min_ao_period = 1 / self._nidev.ao_max_rate
        if n_ai == 0:
            return min_ao_period
        elif n_ai == 1:
            min_ai_period = 1 / self._nidev.ai_max_single_chan_rate
        else:
            # TODO: Automatically find good values. Maybe we could automatically compute it
            # based on the voltages? Or just give up, and expect the installation engineer to find the
            # good values for the system?
            # See specification for the "settling time for multichannels"
            # It depends on the voltage range, and how much precision is required.
            # For the 6361 (and 6251):
            # For >= 1V, 4 least significant bits (LSB), 1µs is enough
            # For <= 0.2V, 4 least significant bits (LSB), 2µs is enough
            # For >= 1V, 1 least significant bits (LSB), 1.5µs is enough
            # For <= 0.2V, 1 least significant bits (LSB), 8µs is enough
            min_ai_period = self._multi_detector_min_period

        # TODO: also modify the AI tasks so that they all use the same voltage range? This minimizes
        # the settle time between samples. Or let the user decide in the configuration file (but
        # that should be well documented)
        return max(min_ao_period, min_ai_period)

    def find_best_dwell_time(self, period: float, nr_ai: int) -> Tuple[float, int, int]:
        """
        Returns the closest dwell time above the given time compatible with the output
        device and the highest over-sampling rate compatible with the input device.
        It tries to find the highest AI over-sampling rate possible, sometimes at the
        expense of the precision. For instance, for 2570, it prefers 2600 / 5 over 2570 / 1.
        That's because the main advantage of a long dwell is to have a large sampling
        number, which improves the SNR.
        It tries to find the smallest AO over-sampling, while keeping the actual
        rate bigger than the minimum accepted by the device.
        :param period (0<float): dwell time requested (in s)
        :param nr_ai (0<=int): number of input channels (aka detectors)
        :return:
         period: a value slightly smaller, or larger than the period (in s)
         ao_osr: a ratio indicating how many times faster runs the AO clock
         ai_osr: a ratio indicating how many times faster runs the AI clock
        """
        # We don't want a too big OSR, as that would mean that hardware-wise, for
        # each pixel the AI read would need to fit in multiple buffers.
        # Anyway, that's not a big deal, as if we have this many samples for a
        # single position, reading it more often would only very marginally
        # improve the SNR. Note: it's quite arbitrary, as the acquirer can deal
        # with any size. Just need to (easily) fit in memory. It might even be
        # a little exceeded due to rounding of the AI period.
        max_ai_osr = 2 ** 24
        # For PCIe 6163:
        # ao_max_rate: 2_857_142.8571428573
        # ao_min_rate: 0.023283064370807974
        # ai_max_single_chan_rate: 2_000_000.0
        # ai_max_multi_chan_rate: 1_000_000.0
        # ai_min_rate: 0.023283064370807974
        # do_max_rate: 10_000_000.0
        # In practice, the AI rate is based on a period rounded to 10ns

        # No do_min_rate, but the minimum accepted is the same as
        # ai/ao_min_rate: 0.023283064370807974

        # Takes care of increasing the period for multiple detectors, afterwards
        # one or multiple detectors is the same (at least, on the NI PCIe 6361)
        min_period = self.get_min_dwell_time(nr_ai)
        period = max(period, min_period)

        max_period = 1 / self._nidev.ao_min_rate  # TODO: store at init
        if period > max_period:
            # If impossible to do a write so long, do multiple acquisitions
            ao_osr = int(math.ceil(period / max_period))
            sub_period = period / ao_osr
        else:
            ao_osr = 1
            sub_period = period

        if nr_ai == 0:
            # For now the rest of this driver makes sure to have at least one AI channel, so it
            # should never be needed, but it's also pretty easy to compute.
            ai_osr = 1
            with nidaqmx.Task() as ao_task:
                ao_period = self._get_closest_period_above(ao_task, sub_period)
                period_actual = ao_osr * ao_period
        else:
            # Try 5 times max:
            # * Try a AI period, and get the closest acceptable *above* it.
            # * Deduce the AI OSR.
            # * Check the corresponding AO period is accepted (typically that always works)
            #   -> if not, try again with an AI period + 10 ns
            # * Check the corresponding DO period is accepted (harder, because it's 2x shorter)
            #   -> if not, try again with an AI period + 10 ns
            with nidaqmx.Task() as ao_task, nidaqmx.Task() as do_task, nidaqmx.Task() as ai_task:
                self._scanner.configure_ao_task(ao_task)
                self._scanner.configure_do_task(do_task)
                # Add N AI channels. The exact channel doesn't matter so we just use the first N detectors
                for det in self._analog_dets[:nr_ai]:
                    det.configure_ai_task(ai_task)

                period_step_size = 10e-9  # s, known step size of the AI period on the NI 625x and 616x.
                sub_period_tried = util.round_up_to_multiple(sub_period, period_step_size)
                ai_sub_osr = max(1, min(int(sub_period_tried / min_period), max_ai_osr))
                ai_period_min = util.round_up_to_multiple(sub_period_tried / ai_sub_osr, period_step_size)

                for i in range(5):  # max 5 trials, should typically work within 2
                    ai_period = ai_period_min + period_step_size * i
                    logging.debug("Trying sub_period %s <= %s * %s s", sub_period_tried, ai_sub_osr, ai_period)
                    ai_period = self._get_closest_period_above(ai_task, ai_period)

                    # Derive back the OSR: must be an int!
                    # Don't clip to max_ai_osr, as it's not a hard limit, and it's important allowing a little bit
                    # more if it started just at max_ai_osr and the period was reduced.
                    ai_sub_osr_accepted = max(1, int(math.ceil(sub_period_tried / ai_period - 1e-12)))
                    ao_period = ai_period * ai_sub_osr_accepted
                    logging.debug("For sub dwell time %s, found AI period %s * %s = %s",
                                  sub_period, ai_period, ai_sub_osr_accepted, ao_period)

                    # Check that the period also works for the AO... normally it always does
                    if not self._is_period_valid(ao_task, ao_period):
                        continue
                    # Check DO period is acceptable. As it's half the AO period, and all periods must be
                    # multiple of 10ns, it can happen that it's not accepted at once.
                    do_period = ao_period / 2  # Hard-coded
                    if do_task.do_channels and not self._is_period_valid(do_task, do_period):
                        continue

                    # Try with ai_sub_osr_accepted + 1, because sometimes the rounding up of ai_period
                    # ended up with a total period which is just longer enough to fit one more osr
                    # (with a much shorter ai_period). eg, for dt=1.87e-6 s.
                    ai_sub_osr_post = int(ao_period / min_period)
                    if ai_sub_osr_post > ai_sub_osr_accepted:
                        ai_period_post = sub_period_tried / ai_sub_osr_post
                        ai_period_post = self._get_closest_period_above(ai_task, ai_period_post)
                        ai_sub_osr_accepted_post = max(1, int(math.ceil(sub_period_tried / ai_period_post - 1e-12)))
                        ao_period_post = ai_period_post * ai_sub_osr_accepted_post
                        logging.debug("Checking period osr + 1: %s * %s = %s",
                                      ai_period_post, ai_sub_osr_accepted_post, ao_period_post)
                        if (ao_period_post <= ao_period
                            and self._is_period_valid(ao_task, ao_period)
                            and (not do_task.do_channels or self._is_period_valid(do_task, ao_period / 2))
                        ):
                            ai_sub_osr_accepted = ai_sub_osr_accepted_post
                            ao_period = ao_period_post
                            logging.debug("Optimized AI period %s * %s = %s",
                                          ai_period_post, ai_sub_osr_accepted, ao_period)

                    # Find the best ao_osr now that we have the definitive sub_period.
                    # It should be very close from the original sub_period, so it's very
                    # unlikely that ao_osr changes, but let's check
                    new_ao_osr = max(1, int(math.ceil(period / ao_period - 1e-12)))
                    if new_ao_osr != ao_osr:
                        logging.warning("Adjusted ao_osr from %d to %d", ao_osr, new_ao_osr)
                        ao_osr = new_ao_osr

                    ai_osr = ai_sub_osr_accepted * ao_osr
                    period_actual = ao_period * ao_osr
                    break  # Found a good period
                else:
                    raise ValueError(f"Failed to find a period for dwell time {period} s with {nr_ai} det")

        logging.info("For requested dwell time %s s with %s detectors, found period %s s with ai_osr=%s, ao_osr=%s",
                     period, nr_ai, period_actual, ai_osr, ao_osr)
        return period_actual, ao_osr, ai_osr

    def _get_closest_period(self, task: nidaqmx.Task, period: float) -> float:
        """
        Find a period that can be accepted for the given task, close to the one requested.
        :param task: A task, already configured with channels (AI, AO, DO... anything)
        :param period: the sample period in s
        :return: an acceptable sample period. Typically it's <= to the requested period
        """
        rate = 1 / period
        # Catch warning 200011 because if there are multiple AI channels, the NI-DAQ driver always
        # generates a warning if the period is less than 2µs (for a PCIe 6361)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", nidaqmx.DaqWarning)
            task.timing.cfg_samp_clk_timing(
                rate=rate,
                source="OnboardClock",
                sample_mode=AcquisitionType.CONTINUOUS,
            )
            # Typically, returns a slightly *higher* rate (so, shorter period)
            accepted_rate = task.timing.samp_clk_rate
        return 1 / accepted_rate

    def _get_closest_period_above(self, task: nidaqmx.Task, period: float) -> float:
        step_size = 10e-9  # s, as we know the hardware has a 10 ns resolution
        for i in range(5):  # max 5 trials
            accepted_period = self._get_closest_period(task, period + i * step_size - 1e-12)
            if accepted_period + 1e-18 >= period:  # almost equal or above
                return accepted_period

        raise ValueError(f"Failed to find a period larger than {period} s")

    def _is_period_valid(self, task: nidaqmx.Task, period: float) -> bool:
        period_accepted = self._get_closest_period(task, period)
        if util.almost_equal(period, period_accepted):
            return True
        logging.info(f"Period {period} not accepted, got {period_accepted}")
        return False


class AcquirerMessage(Enum):
    """
    Messages for the control queue of the Acquirer
    """
    ACQUIRE = enum.auto()  # Start acquisition
    STOP = enum.auto()  # Stop acquisition
    UPDATE_SETTINGS = enum.auto()  # Re-read the scan setting after next frame
    TERMINATE = enum.auto()  # End the Acquirer class


class ImmediateStop(Exception):
    """
    Exception just for internal use to end immediately the acquisition
    """
    pass


class AcquisitionSettings:
    """
    All the settings used to configure an SEM acquisition
    """
    def __init__(self,
                 analog_detectors: List,
                 counting_detectors: List,
                 dwell_time: float,
                 ao_osr: int,
                 ai_osr: int,
                 res: Tuple[int, int],
                 margin: int,
                 positions_n: int,
                 has_do: bool,
                 continuous: bool = True):
        """
        :param analog_detectors: list of analog detectors to acquire the data
        :param counting_detectors: list of counting detectors to acquire the data
        :param dwell_time: duration the beam stays at the same position
        :param ao_osr: number of AO samples per dwell time
        :param ai_osr: number of AI samples per dwell time
        :param res: number of useful pixels in x, y
        :param margin: number of extra pixels for the settle time (aka fly-back)
        :param positions_n: total numbers of e-beam positions to acquire
        :param has_do: True if a digital output task will run
        :param continuous: If False, only acquire a single frame. Otherwise, acquires until a
        UPDATE_SETTINGS (or a STOP) message is received on the message queue.
        """

        self.analog_detectors = analog_detectors
        self.counting_detectors = counting_detectors
        self.dwell_time = dwell_time  # s
        self.ao_osr = ao_osr
        self.ai_osr = ai_osr
        self.res = res  # px, px
        self.margin = margin  # px
        self.positions_n = positions_n
        self.continuous = continuous

        # Derive some useful info
        self.frame_duration = positions_n * dwell_time  # s
        self.ao_sample_rate = ao_osr / dwell_time  # Hz
        self.ao_samples_n = positions_n * ao_osr
        if has_do:
            self.do_sample_rate = self.ao_sample_rate * 2  # Hz, fixed
            self.do_samples_n = self.ao_samples_n * 2
        else:
            self.do_samples_rate = None
            self.do_samples_n = 0
        self.ai_sample_rate = ai_osr / dwell_time  # Hz
        self.ai_samples_n = positions_n * ai_osr


class Acquirer:

    def __init__(self, sem: AnalogSEM, scanner: "Scanner"):
        """
        :param sem: the main object representing the DAQ board
        :param scanner: the Scanner with information on all the scanning parameters
        """
        self._sem = sem
        self._scanner = scanner
        self._mq = queue.Queue()
        self._lock = threading.Lock()  # Hold when changing active_detectors
        self._active_detectors = set()  # Detectors which are to acquire (on next start)
        self._settings_too_fast = False  # True if the latest settigs were detected to be too fast for continuous acquisition

        self._do_data_end = self._scanner._generate_signal_array_end()
        self._do_task_end = nidaqmx.Task()
        self._scanner.configure_do_task(self._do_task_end)

        self._ao_task = None
        self._ao_data_next_sample = 0
        self._ao_data = None
        self._min_ao_buffer_n = self._scanner.get_hw_buf_size()

        self._do_task = None
        self._do_data_next_sample = 0
        self._do_data = None

        self._ai_dtype = None

        self._thread = threading.Thread(target=self._main)
        self._thread.start()

    # Public interface to control the acquirer
    def terminate(self):
        """
        Ends the use of the acquirer.
        Can be called several times. Only the first time has a real effect.
        returns: when the acquirer is completely stopped
        """
        if self._thread is None:
            return

        # First make sure the acquisition is stopped, and then can terminate
        self._mq.put(AcquirerMessage.STOP)
        self._mq.put(AcquirerMessage.TERMINATE)
        self._thread.join(5)
        if self._thread.is_alive():
            logging.warning("Acquisition thread not closing while requested termination")
            self._thread.daemon = True  # To make sure it doesn't prevent from ending the Python process
        self._thread = None
        del self._mq

        self._do_task_end.close()

    @property
    def active_detectors(self):
        return frozenset(self._active_detectors)

    def add_detector(self, detector):
        with self._lock:
            prev_len = len(self._active_detectors)
            self._active_detectors.add(detector)
            if prev_len == 0:
                self._mq.put(AcquirerMessage.ACQUIRE)
            elif prev_len != len(self._active_detectors):
                # Different detectors => same as updating the settings
                self._mq.put(AcquirerMessage.UPDATE_SETTINGS)

            # Check the acquisition thread is running (could be ended due to an error)
            if not self._thread.is_alive():
                logging.debug("Restarting acquirer thread")
                self._thread = threading.Thread(target=self._main)
                self._thread.start()

    def remove_detector(self, detector):
        """
        If detector is not active, no error is raised
        """
        with self._lock:
            prev_len = len(self._active_detectors)
            self._active_detectors.discard(detector)
            if len(self._active_detectors) == 0:
                self._mq.put(AcquirerMessage.STOP)
            elif prev_len != len(self._active_detectors):
                # Different detectors => same as updating the settings
                self._mq.put(AcquirerMessage.UPDATE_SETTINGS)

    def update_settings_on_next_frame(self):
        """
        Force to restart the acquisition after the end of the frame.
        Typically, because the settings have changed.
        """
        self._settings_too_fast = False  # reset to start by trying continuous acquisition
        self._mq.put(AcquirerMessage.UPDATE_SETTINGS)

    def _wait_for_message(self, timeout: Optional[float] = None) -> Optional[AcquirerMessage]:
        """
        Read a message from the message queue.
        :param timeout (None or float >= 0): if None, waits until a message comes in
        Otherwise, stops waiting after the given time. If it's 0, it only check
        whether the queue has already messages, and doesn't wait at all.
        :return: The message, or if the timeout is over, None.
        :raises: ImmediateStop if the message is STOP_NOW
        """
        try:
            if timeout == 0:
                m = self._mq.get(block=False)
            else:
                m = self._mq.get(timeout=timeout)
        except queue.Empty:
            return None

        logging.debug("Acquirer received message %s", m)

        if m is AcquirerMessage.STOP:
            raise ImmediateStop()
        return m

    def _main(self):
        """
        main thread loop.
        returns when the TERMINATE message is received, or an exception happened.
        Then the thread is closed, and the Acquirer cannot be used anymore
        """
        try:
            while True:
                # We begin in "STOPPED" state, and wait for either a ACQUIRE message or TERMINATE
                try:
                    m = self._wait_for_message()
                    if m is AcquirerMessage.ACQUIRE:
                        self._acquire()  # return when back to STOPPED state
                    elif m is AcquirerMessage.TERMINATE:
                        return
                    else:  # We don't care about stop and trigger messages
                        logging.debug("Discarding message while stopped: %s", m)
                except ImmediateStop:
                    logging.debug("Skipping stop message as already stopped")
                    pass
        except Exception:
            logging.exception("Error in acquisition thread")
            logging.info("res = %s, dt = %s", self._scanner.resolution.value, self._scanner.dwellTime.value)
            # Restarting the detector acquisition will restart the thread
        finally:
            try:
                self._scanner.indicate_scan_state(False)
            except Exception:
                logging.warning("Failed to indicate the end of the scan state", exc_info=True)
            logging.debug("acquisition thread closed")

    def _acquire(self):
        """
        Run the acquisition code continuously until STOP or TERMINATE is requested
        """
        try:
            self._scanner.indicate_scan_state(True)  # Blocks until the scan state is set

            while True:
                # Any more messages to process? Some could have arrived in the meantime
                # Look for STOP message only, as settings updates will be automatically applied
                while True:
                    m = self._wait_for_message(timeout=0)
                    if m is None:
                        break

                detectors = list(self._active_detectors)  # Fix in time the detectors that will be used
                if len(detectors) == 0:
                    # It might have just happened
                    logging.debug("No more detectors, assuming it's the end of the acquisition")
                    raise ImmediateStop()  # the main thread will probably receive the STOP message

                # Based on the settings & detectors, use continuous, or frame-by-frame
                need_sync = any(d.data._is_synchronized() for d in detectors)

                if need_sync or self._settings_too_fast:
                    # Acquire a single frame
                    self._acquire_sync(detectors)
                else:
                    # Acquire frame continuously until a setting is changed, or the acquisition is stopped
                    # (return False if the frame rate was too fast)
                    self._settings_too_fast = not self._acquire_series(detectors, continuous=True)
        except ImmediateStop:
            logging.debug("Acquisition stopped immediately")
        except Exception:
            logging.exception("Failure during acquisition")
            raise

        # Stopped.
        # TODO: move to indicate_scan_state()
        # Indicate it on the fast TTLs
        if self._do_data_end is not None:
            try:
                self._do_task_end.write(self._do_data_end, auto_start=True)
                logging.debug("Fast TTLs reset")
            except Exception:
                logging.exception("Failed to indicate the end of the fast TTLs")
        # Delay the state change to avoid too fast switch in case
        # a new acquisition starts soon after.
        self._scanner.indicate_scan_state(False, delay=AFTER_SCAN_DELAY)
        logging.debug("End of the acquisition")

    def _get_ai_dtype(self, task: nidaqmx.Task) -> numpy.dtype:
        """
        Determine the proper dtype that fits for the data that will be read by the given task
        :param task: a task which has already been configured with AI channel(s)
        :return: the dtype, as can be used when creating a numpy array.
        """
        # Code inspired by nidaqmx.InStream.read()

        samp_size_in_bits = task.ai_channels[0].ai_raw_samp_size
        has_negative_range = task.ai_channels[0].ai_rng_low < 0

        if samp_size_in_bits == 32:
            if has_negative_range:
                dtype = numpy.int32
            else:
                dtype = numpy.uint32
        elif samp_size_in_bits == 16:
            if has_negative_range:
                dtype = numpy.int16
            else:
                dtype = numpy.uint16
        elif samp_size_in_bits == 8:
            if has_negative_range:
                dtype = numpy.int8
            else:
                dtype = numpy.uint8
        else:
            raise IOError("Unexpected sample size of {samp_size_in_bits} bits")

        return dtype

    # TODO: also provide for dtype ao
    # see task.channels.ao_resolution + task.channels.ao_resolution_units = ResolutionType.BITS
    # (or task.ao_channels[0].ao_resolution) => 16 bits
    # Or task.out_stream.raw_data_width (in bytes) => 2 bytes

    def _get_images_metadata(self, acq_settings: AcquisitionSettings) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Generate the metadata for the images based on the hardware settings
        :param acq_settings: The settings for the given acquisition
        :return:
            list of metadata dict, in the same order as the analog detectors
            list of metadata dict, in the same order as the counting detectors
        """
        base_md = self._scanner.getMetadata().copy()
        base_md[model.MD_ACQ_DATE] = time.time()
        base_md[model.MD_DWELL_TIME] = acq_settings.dwell_time
        # add scanner translation to the center
        center = base_md.get(model.MD_POS, (0, 0))
        trans = self._scanner.pixelToPhy(self._scanner.translation.value)
        base_md[model.MD_POS] = (center[0] + trans[0],
                                 center[1] + trans[1])

        # metadata is the merge of the base MD + detector MD + scanner MD
        base_md[model.MD_INTEGRATION_COUNT] = acq_settings.ai_osr
        analog_mds = []
        for det in acq_settings.analog_detectors:
            md = base_md.copy()
            md.update(det.getMetadata())
            analog_mds.append(md)

        # CI: sampling is at the same rate as AO
        base_md[model.MD_INTEGRATION_COUNT] = acq_settings.ao_osr
        counting_mds = []
        for det in acq_settings.counting_detectors:
            md = base_md.copy()
            md.update(det.getMetadata())
            counting_mds.append(md)

        return analog_mds, counting_mds

    def _write_int16_interleaved(self, data: numpy.ndarray) -> int:
        """
        Write data to the AO task in a "safe" way by making sure it's contiguous in memory
        :param data: 2D numpy aray of the samples to write of shape (2, N), dtype int16
        :return: number of data written
        """
        # TODO: ideally, we wouldn't have to make the data contiguous (aka copy) because it's always
        #  contiguous. To do so, we would need to write with interleaved samples (FillMode.GROUP_BY_SCAN_NUMBER)
        #  however the python wrapper doesn't allow to do that. Trying to do it "manually" sort of work...
        #  but in this case the write events callback is never called. => Need to investigate what's
        #  wrong. The tests showed that it goes from 1ms to 0.1ms per write (for 50000 samples)
        return self._ao_task.out_stream.write(numpy.ascontiguousarray(data))
        # Example how to write in interleaved mode
        # return writer._interpreter.write_binary_i16(
        #        writer._handle, data.shape[1], False, timeout, FillMode.GROUP_BY_SCAN_NUMBER.value, data)

    def _write_ao_data_finite(self, n: int) -> int:
        """
        Write the next n elements from the ao_data to the board.
        It relies on ._ao_data and ._ao_data_next_sample
        It doesn't wrap around the data, but will just clip the number of samples to write if at the
        end of the array. If there is nothing left to write, this function will do nothing.
        :param n: number of samples per channel to write
        :return: number of samples per channel actually written
        """
        ao_data_len = self._ao_data.shape[1]
        if ao_data_len == self._ao_data_next_sample:
            return 0  # Already wrote all, nothing left to do

        write_end = min(self._ao_data_next_sample + n, ao_data_len)
        written_n = self._write_int16_interleaved(self._ao_data[:, self._ao_data_next_sample:write_end])
        self._ao_data_next_sample = (self._ao_data_next_sample + written_n)

        return written_n

    def _write_ao_data(self, n: int) -> int:
        """
        Write the next n elements from the ao_data to the board.
        It relies on ._ao_data and ._ao_data_next_sample
        It wraps around the data array, but at most once. So if the array has a length of 2, and
        next sample is set to 0, and 16 samples are requested, only 4 will be written.
        :param n: number of samples per channel to write
        :return: number of samples per channel actually written
        """
        ao_data_len = self._ao_data.shape[1]
        write_end = min(self._ao_data_next_sample + n, ao_data_len)
        expected_n = write_end - self._ao_data_next_sample
        written_n = self._write_int16_interleaved(self._ao_data[:, self._ao_data_next_sample:write_end])
        self._ao_data_next_sample = (self._ao_data_next_sample + written_n) % ao_data_len
        # If everything was written => we are done
        # If the board doesn't accept as many as we asked => no need to try more
        if written_n < expected_n or expected_n == n:
            return written_n

        # Else: we need to loop to write one more time from the beginning of the ao_data
        n -= written_n
        if self._ao_data_next_sample != 0:
            logging.error("start should be 0, but got %s", self._ao_data_next_sample)
        written2_n = self._write_int16_interleaved(self._ao_data[:, :n])
        self._ao_data_next_sample = (self._ao_data_next_sample + written2_n) % ao_data_len
        return written_n + written2_n

    def _write_do_data_finite(self, n: int) -> int:
        """
        Write the next n elements from the do_data to the board.
        It relies on ._do_data and ._do_data_next_sample
        It doesn't wrap around the data, but will just clip the number of samples to write if at the
        end of the array. If there is nothing left to write, this function will do nothing.
        :param n: number of samples per channel to write
        :return: number of samples per channel actually written
        """
        do_data_len = self._do_data.shape[-1]
        if do_data_len == self._do_data_next_sample:
            return 0  # Already wrote all, nothing left to do

        write_end = min(self._do_data_next_sample + n, do_data_len)

        written_n = self._do_task.write(self._do_data[self._do_data_next_sample:write_end])
        self._do_data_next_sample = (self._do_data_next_sample + written_n)

        return written_n

    def _write_do_data(self, n: int) -> int:
        """
        Write the next n elements from the do_data to the board.
        It relies on ._do_data and ._do_data_next_sample
        It wraps around the data array, but at most once. So if the array has a length of 2, and
        next sample is set to 0, and 16 samples are requested, only 4 will be written.
        :param n: number of samples per channel to write
        :return: number of samples per channel actually written
        """
        do_data_len = self._do_data.shape[-1]
        write_end = min(self._do_data_next_sample + n, do_data_len)
        expected_n = write_end - self._do_data_next_sample
        # Note: the array has to be contiguous, but we know it's true, because it is just a single
        # dimension array, with uint32's containing the values for all the ports at once.
        written_n = self._do_task.write(self._do_data[self._do_data_next_sample:write_end])
        self._do_data_next_sample = (self._do_data_next_sample + written_n) % do_data_len
        # If everything was written => we are done
        # If the board doesn't accept as many as we asked => no need to try more
        if written_n < expected_n or expected_n == n:
            return written_n

        # Else: we need to loop to write one more time from the beginning of the ao_data
        n -= written_n
        if self._do_data_next_sample != 0:
            logging.error("start should be 0, but got %s", self._do_data_next_sample)
        #written2_n = self._do_task.write(numpy.ascontiguousarray(self._do_data[:, :n]))
        written2_n = self._do_task.write(self._do_data[:n])
        self._do_data_next_sample = (self._do_data_next_sample + written2_n) % do_data_len
        return written_n + written2_n

    def _on_ao_data_consumed(self, task_handle: int, every_n_samples_event_type, num_of_samples: int,
                             callback_data=None,
                             continuous: bool = True) -> int:
        """
        Callback for when AO buffer is low. Used to push more data to the AO channel.
        :param task_handle: identifier of the NI task
        :param every_n_samples_event_type: always TRANSFERRED_FROM_BUFFER
        :param num_of_samples: number of samples that were written since last call
        :param callback_data: it's always None
        :param continuous: if True, it wraps the data so that the next frame immediately starts
        :return: always 0
        """
        try:
            # Refill as much as was consumed (so should be certain to not write too much, so not block)
            # TODO: use space_avail to pass more than just num_of_samples if there is already more that we can write?
            # Typically, 100000 samples ~ 1ms (that's the max it would send at a time, every 50 ms)
            try:
                if continuous:
                    sent_n = self._write_ao_data(num_of_samples)
                else:
                    sent_n = self._write_ao_data_finite(num_of_samples)
            except nidaqmx.DaqError as ex:
                # It might have failed just because the task was stopped/closed
                try:
                    if not self._ao_task.is_task_done():
                        raise ex
                except nidaqmx.DaqError:  # That's a sign the task was closed
                    pass
                logging.debug("Skipping error as AO task is done")
                return 0
        except Exception:
            logging.exception("Failure to send more data")
        return 0

    def _on_do_data_consumed(self, task_handle: int, every_n_samples_event_type, num_of_samples: int,
                             callback_data=None,
                             continuous: bool = True) -> int:
        """
        Callback for when AO buffer is low. Used to push more data to the AO channel.
        :param task_handle: identifier of the NI task
        :param every_n_samples_event_type: always TRANSFERRED_FROM_BUFFER
        :param num_of_samples: number of samples that were written since last call
        :param callback_data: it's always None (fixed in nidaqmx python wrapper)
        :param continuous: if True, it wraps the data so that the next frame immediately starts
        :return: always 0
        """
        try:
            # logging.debug("DO task consumed %s samples", num_of_samples)
            # Refill as much as was consumed (so should be certain to not write too much, so not block)
            # TODO: use space_avail to send more than just num_of_samples?
            # Typically, with separated channels, 100000 samples ~ 8 ms (that's the max it would send at a time, every 50 ms)
            # with merged channels, 100000 samples ~ 1 ms
            try:
                if continuous:
                    sent_n = self._write_do_data(num_of_samples)
                else:
                    sent_n = self._write_do_data_finite(num_of_samples)
            except nidaqmx.DaqError as ex:
                # It might have failed just because the task was stopped/closed
                try:
                    if not self._do_task.is_task_done():
                        raise ex
                except nidaqmx.DaqError:  # That's a sign the task was closed
                    pass
                logging.debug("Skipping error as DO task is done")
                return 0
        except Exception:
            logging.exception("Failure to send more data")
        return 0

    def _find_good_ai_buffer_size(self, acq_settings: AcquisitionSettings, period: float) -> int:
        """
        Compute a buffer size for the AI that fits well with the data shape, so that _downsample_data()
        runs efficiently.
        :param acq_settings: acquisition settings
        :param period: maximum duration of a buffer. This function tries to find a buffer duration
          that is as close as possible, but less or equal to that period.
        :return: number of samples in the AI acquisition per channel
        """
        # Does it fit a whole frame?
        if acq_settings.ai_samples_n / acq_settings.ai_sample_rate < period:
            logging.debug("AI buffer set to the size of a whole frame")
            return max(2, acq_settings.ai_samples_n)  # The buffer must be at least of length 2 for the hardware

        # Can we fit a whole line? If so, how many lines per period?
        line_length = acq_settings.res[0] * acq_settings.ai_osr
        line_dur = line_length / acq_settings.ai_sample_rate  # s
        if line_dur < period:
            logging.debug("AI buffer set to the size of a number of lines")
            return max(2, int(period / line_dur) * line_length)  # samples

        # Can we fit a whole pixel? If so, how many pixels per period?
        pixel_dur = acq_settings.ai_osr / acq_settings.ai_sample_rate  # s
        if pixel_dur < period:
            # Find a round number of pixels per period and convert back to samples
            logging.debug("AI buffer set to the size of a number of pixels")
            return max(2, int(period / pixel_dur) * acq_settings.ai_osr)  # samples

        # OK... let's give up, even a pixel doesn't fit in a period, so just fit exactly the number of samples
        logging.debug("AI buffer set to less than a pixel")
        return min(max(2, int(acq_settings.ai_sample_rate * period)), acq_settings.ai_samples_n)

    def _acquire_sync(self, detectors: list):
        """
        Run a software synchronized acquisition for *one* frame or stop request was received (vie
        the command queue). The drawback compared to _acquire_cont() is that it will wait a
        little bit between each frame, with the e-beam staying at the position of the last pixel.
        However, this is the only way to handle the cases where a detector is synchronized on a
        software event. This is also useful in case the frame rate is too high for the continuous
        acquisition to keep up with the hardware (though it's most likely due to an error in the
        scan settings by the user, such as a scan of 1x1 with a short dwell time)
        :param detectors: list of the detectors (ie, the AI channels) to use
        """
        # Note: for now, in Odemis, we use synchronization only for two aspects: we can start
        # directly with multiple detectors, and we absolutely scan only once. The Odemis API is
        # quite badly fitted to express that. So they end up using the complex "synchronized" API.

        # Typically it's not important to be able to start the acquisition very quickly
        # after receiving the event. Most of the use cases involve changing the scan setting
        # immediately followed by starting the acquisition. So it's hopeless to prepare the hardware
        # early. The only case where it would help in theory is for the cases when the framerate is
        # too high for the continuous acquisition, however these are normally out-of-spec settings
        # so we don't have any need to optimize for them.

        # Wait for all the DataFlows to have received a sync event, while checking the message queue
        # from time to time in case the acquisition stops.
        det_not_ready = set(detectors)
        det_ready = set()
        while det_not_ready:
            for d in det_not_ready:
                try:
                    d.data._wait_sync(timeout=0.1)
                    det_ready.add(d)
                except TimeoutError:
                    pass  # It's all fine, we'll wait more later
                m = self._wait_for_message(timeout=0)
                # only STOP messages are interesting, and they automatically raise an exception, so
                # all other messages are ignored.
                if m is not None:
                    logging.debug("Ignoring message %s", m)

            # Check if new detectors were added (or removed) in the meantime
            detectors = set(self._active_detectors)
            det_not_ready = detectors - det_ready

        if not detectors:
            raise ImmediateStop()

        # Run the tasks
        self._acquire_series(list(detectors), continuous=False)

    def _acquire_series(self, detectors: list, continuous: bool) -> bool:
        """
        Run a continuous acquisition until a setting has changed or stop request was received (vie the command queue)
        :param detectors: list of the detectors (ie, the AI & CI channels) to use
        :param continuous: If False, only acquire a single frame. Otherwise, acquires until a
        UPDATE_SETTINGS (or a STOP) message is received on the message queue.
        :return: False if hardware cannot handle continuous acquisition with the current settings
        (typically, because it causes a too high frame rate). True if the acquisition was successful.
        """
        analog_dets = [d for d in detectors if isinstance(d, AnalogDetector)]
        counting_dets = [d for d in detectors if isinstance(d, CountingDetector)]

        # The acquisition expects to have a AI task, so if no AI detector, just add a arbitrary one,
        # and no one will receive the data. (It uses a tiny bit more CPU but keeps the code simpler)
        if counting_dets and not analog_dets:
            adet = self._sem._analog_dets[0]
            logging.debug(f"Adding dummy AI detector {adet.name} as only CI detector was provided")
            detectors.append(adet)
            analog_dets.append(adet)

        # Get the waveforms
        (scan_array, ttl_array,
         dt, ao_osr, ai_osr, res, margin) = self._scanner._get_scan_waveforms(len(analog_dets))
        acq_settings = AcquisitionSettings(analog_dets, counting_dets,
                                           dt, ao_osr, ai_osr, res, margin,
                                           scan_array.shape[1] // ao_osr,
                                           has_do=(ttl_array is not None), continuous=continuous)
        logging.debug(f"Will scan {acq_settings.positions_n} positions @ {acq_settings.dwell_time * 1e6:.3g} µs "
                      f"{'continuously' if continuous else 'once'} "
                      f"with {len(analog_dets)} AI and {len(counting_dets)} CI, for a total of "
                      f"{acq_settings.frame_duration:.6g} s per frame")

        if continuous and acq_settings.frame_duration < MIN_FRAME_DURATION_CONT_ACQ:
            logging.debug(f"Frame duration {acq_settings.frame_duration:.6g} s is too short for "
                          "continuous acquisition, will use synchronized acquisition")
            return False

        # TODO: now that the AO/DO data is sent in small pieces, we could duplicate it "on the fly"
        # when ao_osr >= 1. In these cases, the sampling rate is always very small (by definition)
        # so it's OK to do memory copy. This would avoid the (unlikely) case of
        # using a huge amount of memory for the AO data if the user selects by mistake a large resolution
        # + long dwell time. (which would probably be stopped before the end, but could fail to even
        # start due to not having enough memory)
        self._ao_data = scan_array
        self._ao_data_next_sample = 0  # position of the next sample to write to the board (updated by _write_ao_data())
        self._do_data = ttl_array
        self._do_data_next_sample = 0  # position of the next sample to write to the board (updated by _write_do_data())

        # we want a buffer which is not too long (~ BUFFER_DURATION)
        ai_buffer_n = self._find_good_ai_buffer_size(acq_settings, BUFFER_DURATION)
        logging.debug("Using a AI buffer of %s samples (%s s) => %g buffers per frame",
                      ai_buffer_n, ai_buffer_n / acq_settings.ai_sample_rate,
                      acq_settings.ai_samples_n / ai_buffer_n)

        # The AO buffer size must be at least of len 2. So if there is just one
        # point, we duplicate it, to make the NI DAQ happy. (It's the same behaviour
        # as anyway the task is continuously repeating)
        ao_samples_n = acq_settings.ao_samples_n
        if ao_samples_n == 1:
            logging.debug("Duplicating AO buffer as it has size 1")
            ao_samples_n = 2
            self._ao_data = numpy.append(self._ao_data, self._ao_data, 1)

        # Also pass AO data in chunks, so that it doesn't need to write the whole AO data before
        # starting, and also can handle really long scan. In tests, it seems it can sustain even 100µs
        # updates, but there is enough buffer to do quite a lot bigger (and really make sure that
        # no interruption can disturb the write).
        # As CI uses the same buffer size, it's best to just use the same period as AI.
        ao_buffer_n = min(max(self._min_ao_buffer_n, int((ai_buffer_n * ao_osr) // ai_osr)), ao_samples_n)
        logging.debug("Using a AO buffer of %s samples (%s s) => %g buffers per frame",
                      ao_buffer_n, ao_buffer_n / acq_settings.ao_sample_rate,
                      ao_samples_n / ao_buffer_n)

        do_buffer_n = min(2 * ao_buffer_n, acq_settings.do_samples_n)  # It's always 2x faster, so always twice bigger

        # Note: creating and configuring the tasks can take up to 30ms!
        ci_tasks = []
        with nidaqmx.Task() as ao_task, nidaqmx.Task() as do_task, nidaqmx.Task() as ai_task:
            self._scanner.configure_ao_task(ao_task)
            self._ao_task = ao_task

            if acq_settings.do_samples_n:
                self._scanner.configure_do_task(do_task)
                self._do_task = do_task
            else:
                self._do_task = None

            if ao_buffer_n < ao_samples_n:
                # TODO: instead of relying on the callback, we could push the new data at the same
                # time as the AI is read. It's mostly just a matter of using a buffer of the right size.
                logging.debug("Will push new AO data every %s s",
                              (ao_buffer_n // 2) / acq_settings.ao_sample_rate)
                on_ao_data_consumed = functools.partial(self._on_ao_data_consumed, continuous=continuous)
                ao_task.register_every_n_samples_transferred_from_buffer_event(ao_buffer_n // 2,
                                                                               on_ao_data_consumed)
                ao_task.out_stream.regen_mode = RegenerationMode.DONT_ALLOW_REGENERATION  # Don't loop back
                if acq_settings.do_samples_n:
                    on_do_data_consumed = functools.partial(self._on_do_data_consumed, continuous=continuous)
                    do_task.register_every_n_samples_transferred_from_buffer_event(do_buffer_n // 2,
                                                                                   on_do_data_consumed)
                    do_task.out_stream.regen_mode = RegenerationMode.DONT_ALLOW_REGENERATION  # Don't loop back
            elif continuous:
                # Everything fits in a single buffer => easy, let the hardware loop
                ao_task.out_stream.regen_mode = RegenerationMode.ALLOW_REGENERATION  # loop back (default)
                if self._do_data is not None:
                    do_task.out_stream.regen_mode = RegenerationMode.ALLOW_REGENERATION  # loop back (default)
            else:  # Only once => don't regen
                ao_task.out_stream.regen_mode = RegenerationMode.DONT_ALLOW_REGENERATION
                if self._do_data is not None:
                    do_task.out_stream.regen_mode = RegenerationMode.DONT_ALLOW_REGENERATION

            # AI tasks
            for d in analog_dets:
                d.configure_ai_task(ai_task)

            # CI tasks
            for d in counting_dets:
                # needs one task per counter => create a new task for each counter
                ci_task = nidaqmx.Task()
                d.configure_ci_task(ci_task)
                ci_tasks.append(ci_task)

            self._ai_dtype = self._get_ai_dtype(ai_task)

            try:
                self._acquire_frames(acq_settings,
                                     ao_task, do_task, ai_task, ci_tasks,
                                     ao_buffer_n, do_buffer_n, ai_buffer_n)
            except IOError as ex:
                if continuous:
                    logging.info(f"Continuous acquisition failed ({ex}), will use synchronized acquisition")
                    return False
                else:  # Too fast for single frame? There is not much hope.
                    raise
            finally:
                ao_task.stop()
                ao_task.register_every_n_samples_transferred_from_buffer_event(0, None)
                do_task.stop()
                do_task.register_every_n_samples_transferred_from_buffer_event(0, None)
                ai_task.stop()
                for ci_task in ci_tasks:
                    ci_task.stop()
                    ci_task.close()

                self._ao_data = None
                self._do_data = None
                logging.debug("End of acquisition")

            return True

    def _acquire_frames(self,
                        acq_settings: AcquisitionSettings,
                        ao_task: nidaqmx.Task, do_task: nidaqmx.Task, ai_task: nidaqmx.Task,
                        ci_tasks: List[nidaqmx.Task],
                        ao_buffer_n: int, do_buffer_n: int, ai_buffer_n: int,
                        ):
        """
        Acquires a series of frames, with the given settings
        :param acq_settings: settings for the acquisition.
        :param ao_task: The AO task object.
        :param do_task: The DO task object.
        :param ai_task: The AI task object.
        :param ci_tasks: All the CI task (counter input)
        :param ao_buffer_n: The number of samples for AO.
        :param do_buffer_n: The number of samples for DO.
        :param ai_buffer_n: The number of samples for AI.
        :raise:
          IOError: if fails to write to buffer or fails to read the data fast enough for the hardware
        """
        assert self._ai_dtype is not None  # It should be now specified

        self._configure_sync_tasks(acq_settings,
                                   ao_task, do_task, ai_task, ci_tasks,
                                   ao_buffer_n, do_buffer_n, ai_buffer_n)

        # Initiate the AO and DO buffers & start the tasks (so they wait for the start trigger)
        self._write_ao_data(ao_buffer_n)
        ao_task.start()  # still waits for the start trigger
        if acq_settings.do_samples_n:
            self._write_do_data(do_buffer_n)
            do_task.start()  # still waits for the start trigger
        for ci_task in ci_tasks:
            ci_task.start()  # still waits for the start trigger

        # Now start!
        ai_task.start()
        logging.debug("AI task started (with AO + DO too)")

        n_analog_det = len(acq_settings.analog_detectors)
        n_counting_det = len(acq_settings.counting_detectors)
        assert n_counting_det == len(ci_tasks)

        # Acquire data until a STOP message is received (or only once if it's a single frame)
        should_stop = not acq_settings.continuous
        # Place to store the raw AI data, with over-sampling
        ai_buffer_full = numpy.empty((n_analog_det, ai_buffer_n), dtype=self._ai_dtype)
        acc_dtype = get_best_dtype_for_acc(ai_buffer_full.dtype, acq_settings.ai_osr)

        # create a counter reader to read from the counter InStream
        ci_readers = []
        for ci_task in ci_tasks:
            ci_reader = CounterReader(ci_task.in_stream)
            ci_reader.verify_array_shape = False  # To go a tiny bit faster when reading
            ci_readers.append(ci_reader)

        # TODO: acquire in multiple times, of sizes ao_buffer_n
        # Note: also works if n_counting_det == 0 => makes a numpy array of dim 0
        # We only need one buffer for all the counters, as they are read one at a time
        ci_buffer_n = ao_buffer_n  # For now ao_buffer is sized for the same duration as AI buffer, so it's what we want
        ci_buffer = numpy.empty((ci_buffer_n,),
                                dtype=numpy.uint32)  # TODO get dtype from the detector

        n_ci_samples_per_ai_sample = acq_settings.ao_sample_rate / acq_settings.ai_sample_rate
        if ci_tasks:
            logging.debug("Will acquire %s CI sample/AI sample", n_ci_samples_per_ai_sample)

        while True:
            # Prepare the metadata, with the settings at the beginning of acquisition
            # Update at every frame as metadata can change at any time (ex: MD_PIXEL_SIZE, MD_POS)
            analog_mds, counting_mds = self._get_images_metadata(acq_settings)

            # The actual frame data, after downsampling.
            ai_data = numpy.empty((n_analog_det, acq_settings.res[1], acq_settings.res[0]),
                                  dtype=self._ai_dtype)
            acquired_n = 0
            prev_samples_n = [0] * n_analog_det
            prev_samples_sum = [0] * n_analog_det

            ci_data = numpy.empty((n_counting_det, acq_settings.res[1], acq_settings.res[0]),
                                  dtype=numpy.uint32)
            ci_acquired_n = 0
            ci_prev_samples_n = [0] * n_counting_det
            ci_prev_samples_sum = [0] * n_counting_det

            while acquired_n < acq_settings.ai_samples_n:
                new_samples_n, prev_samples_n, prev_samples_sum = self._read_ai_buffer(
                    acq_settings,
                    ai_task, ai_data, ai_buffer_full, acquired_n,
                    acc_dtype, prev_samples_n, prev_samples_sum)

                acquired_n += new_samples_n

                # Is it time to acquire CI?
                ci_acquired_n, ci_prev_samples_n, ci_prev_samples_sum = self._read_ci_buffer(
                    acq_settings,
                    ci_readers, ci_data, ci_buffer, acquired_n,
                    ci_acquired_n, n_ci_samples_per_ai_sample,
                    ci_prev_samples_n, ci_prev_samples_sum)

                m = self._wait_for_message(timeout=0)
                if m is None:  # No message
                    pass
                elif m is AcquirerMessage.UPDATE_SETTINGS:
                    should_stop = True
                    logging.debug("Will stop after the frame")
                else:
                    logging.debug("Discarding message during acquisition: %s", m)

                # End of buffer read

            logging.debug(f"Acquired one frame of {ai_data.shape} px, with {acquired_n} samples")

            # TODO: just put the data on a queue, and let the listener take care of this?
            # This would avoid blocking (of course, it's not a big issue, as the hardware is running in background)

            for i, d in enumerate(acq_settings.analog_detectors):
                im = model.DataArray(ai_data[i], analog_mds[i])
                d.data.notify(im)

            for i, d in enumerate(acq_settings.counting_detectors):
                im = model.DataArray(ci_data[i], counting_mds[i])
                d.data.notify(im)

            if should_stop:
                return

    # TODO: make a whole class for this?
    def _read_ai_buffer(self, acq_settings: AcquisitionSettings,
                        ai_task: nidaqmx.Task, ai_data: numpy.ndarray,
                        ai_buffer_full: numpy.ndarray,
                        acquired_n: int, acc_dtype: numpy.dtype,
                        prev_samples_n: List[int], prev_samples_sum: List[int],
                        ) -> Tuple[int, List[int], List[int]]:
        """
        Reads data from the Analog Input (AI) buffer and processes it to fill the corresponding part
        of the final frame data.

        :param acq_settings: AcquisitionSettings object containing the settings for the current acquisition.
        :param ai_task: nidaqmx.Task object representing the AI task.
        :param ai_data: image array to store the data, shape (channels, height, width)
        :param ai_buffer_full: temporary array to store the raw AI data from the device, shape (channels, N)
        :param acquired_n: number of samples already acquired.
        :param acc_dtype: numpy.dtype object representing the data type used for accumulation during downsampling.
        :param prev_samples_n: number of samples lastly processed but not yet completing
        a whole pixel, for each channel. It should be the samples_n returned by the last call.
        :param prev_samples_sum: sum of samples lastly processed but not yet completing
        a whole pixel, for each channel. It should be the samples_sum returned by the last call.
        :param acc_dtype: the numpy data type to use for the accumulator.
        :returns:
            * acquired_n: updated number of samples acquired
            * samples_n: the number of the (last) samples which could not be fully
            fitted in a pixel yet.
            * samples_sum: the sum of the (last) samples which could not be fully
            fitted in a pixel yet.
        """
        n_detectors, ai_buffer_n = ai_buffer_full.shape
        # Compute the number of data left to acquire to fill the array
        samples_left_n = acq_settings.ai_samples_n - acquired_n
        # Note: the hardware buffer must be at least 2, but it's fine to read just 1 at a
        # time, so no minimum value.
        samples_to_acquire = min(samples_left_n, ai_buffer_n)
        if samples_to_acquire < ai_buffer_n:
            # Need to reshape the buffer so that it's of shape C, N *and* contiguous
            # The simple "ai_buffer[:, :samples_to_acquire]" is not contiguous for C > 1.
            # As it's just a buffer we don't really care of the final shape, it's fine
            # to reorganise it.
            ai_buffer = ai_buffer_full.ravel()[:n_detectors * samples_to_acquire]  # long blob
            ai_buffer = ai_buffer.reshape(n_detectors, samples_to_acquire)  # put in the expected shape
            logging.debug("Reduced ai_buffer to %s", ai_buffer.shape)
        else:
            ai_buffer = ai_buffer_full

        # Now we have a bit of time, let's run the garbage collector
        estimated_ai_time = samples_to_acquire / acq_settings.ai_sample_rate
        self._sem._gc_while_waiting(estimated_ai_time)

        # Note: the nidaqmx API is annoying because arrays must be of shape
        # C,N (channel, sample numbers), *except* if C == 1, in which case
        # it must only be of shape N. However, readinto works fine with 1,N.
        try:
            new_samples_n = ai_task.in_stream.readinto(ai_buffer)
        except nidaqmx.DaqReadError as ex:
            raise IOError("Failed to read AI acquisition data") from ex
        if new_samples_n != samples_to_acquire:
            if new_samples_n > samples_to_acquire:
                logging.error("Received %d samples, while expecting %d, there is data loss."
                              "ai_buffer shape %s, nbytes %s",
                              new_samples_n, samples_to_acquire, ai_buffer.shape, ai_buffer.nbytes)
            else:
                logging.warning("Only received %d samples, while expecting %d, will try more",
                                new_samples_n, samples_to_acquire)

        logging.debug("Got another %s AI samples, over %s still to acquire", new_samples_n, samples_left_n)

        # Downsample each channel independently
        for c in range(ai_data.shape[0]):
            prev_samples_n[c], prev_samples_sum[c] = self._downsample_data(ai_data[c],
                                                                           acq_settings.res,
                                                                           acq_settings.margin,
                                                                           acquired_n, acq_settings.ai_osr,
                                                                           ai_buffer[c, :new_samples_n],
                                                                           prev_samples_n[c],
                                                                           prev_samples_sum[c],
                                                                           acc_dtype,
                                                                           average=True)
        return new_samples_n, prev_samples_n, prev_samples_sum

    def _read_ci_buffer(self, acq_settings: AcquisitionSettings,
                        ci_readers: List[CounterReader],
                        ci_data: numpy.ndarray, ci_buffer: numpy.ndarray,
                        ai_acquired_n: int, ci_acquired_n: int, n_ci_samples_per_ai_sample: float,
                        ci_prev_samples_n: List[int], ci_prev_samples_sum: List[int],
                        ) -> Tuple[int, List[int], List[int]]:
        """
        Reads data from the Counter Input (CI) buffers and processes it to fill the corresponding part
        of the final frame data.

        :param acq_settings: AcquisitionSettings object containing the settings for the current acquisition.
        :param ci_readers: a reader for each channel (one per CI task)
        :param ci_data: image array to store the data, shape (channels, height, width)
        :param ci_buffer_full: temporary array to store the raw CI data from the device, for a single channel. shape (N)
        :param ai_acquired_n: number of AI samples already acquired.
        :param ci_acquired_n: number of CI samples already processed. Should be ci_acquired_n as return in the previous call.
        :param n_ci_samples_per_ai_sample: number of samples a CI samples acquired for each AI sample, on average
        :param acc_dtype: numpy.dtype object representing the data type used for accumulation during downsampling.
        :param prev_samples_n: number of samples lastly processed but not yet completing
        a whole pixel, for each channel. It should be the samples_n returned by the last call.
        :param prev_samples_sum: sum of samples lastly processed but not yet completing
        a whole pixel, for each channel. It should be the samples_sum returned by the last call.
        :param acc_dtype: the numpy data type to use for the accumulator.
        :returns:
            * ci_acquired_n: updated number of samples acquired
            * samples_n: the number of the (last) samples which could not be fully
            fitted in a pixel yet.
            * samples_sum: the sum of the (last) samples which could not be fully
            fitted in a pixel yet.
        """
        if not ci_readers:  # Short-cut
            return 0, ci_prev_samples_n, ci_prev_samples_sum

        ci_buffer_n = ci_buffer.shape[0]  # not duplicated per detector, so just 1 dim

        # Estimate how many samples we can expect to be available, and read by chunks of buffer size
        if ai_acquired_n < acq_settings.ai_samples_n:
            ci_samples_done_n = int(ai_acquired_n * n_ci_samples_per_ai_sample)  # theoretical & pessimistic number
            ci_to_read_n = ci_samples_done_n - ci_acquired_n
            ci_to_read_n = (ci_to_read_n // ci_buffer_n) * ci_buffer_n  # round down to buffer size
            ci_samples_goal_n = ci_acquired_n + ci_to_read_n
        else:  # It's the end => read the rest
            ci_samples_goal_n = acq_settings.ao_samples_n

        while ci_acquired_n < ci_samples_goal_n:
            samples_left_n = acq_settings.ao_samples_n - ci_acquired_n
            samples_to_acquire = min(samples_left_n, ci_buffer_n)
            logging.debug("Going to read CI buffer of %s samples", samples_to_acquire)
            for c, ci_reader in enumerate(ci_readers):
                try:
                    new_samples_n = ci_reader.read_many_sample_uint32(ci_buffer[:samples_to_acquire],
                                                                      number_of_samples_per_channel=samples_to_acquire,
                                                                      timeout=0.1)  # Should be immediate as we just checked the period
                except nidaqmx.DaqReadError as ex:
                    raise IOError("Failed to read CI acquisition data") from ex

                if new_samples_n != samples_to_acquire:
                    logging.warning("Only received %d samples, while expecting %d, will try more",
                                    new_samples_n, samples_to_acquire)

                logging.debug("Got another %s CI samples, over %s still to acquire", new_samples_n, samples_left_n)
                logging.debug(
                    f"ci_data[c] shape = {ci_data[c].shape}, ci_buffer shape: {ci_buffer[:new_samples_n].shape}")
                ci_prev_samples_n[c], ci_prev_samples_sum[c] = self._downsample_data(ci_data[c],
                                                                                     acq_settings.res,
                                                                                     acq_settings.margin,
                                                                                     ci_acquired_n,
                                                                                     acq_settings.ao_osr,
                                                                                     ci_buffer[:new_samples_n],
                                                                                     ci_prev_samples_n[c],
                                                                                     ci_prev_samples_sum[c],
                                                                                     acc_dtype=ci_data.dtype,
                                                                                     # for sum, this is the same as data.dtype
                                                                                     average=False)
            ci_acquired_n += new_samples_n

        return ci_acquired_n, ci_prev_samples_n, ci_prev_samples_sum

    def _configure_sync_tasks(self,
                              acq_settings: AcquisitionSettings,
                              ao_task: nidaqmx.Task, do_task: nidaqmx.Task, ai_task: nidaqmx.Task,
                              ci_tasks: List[nidaqmx.Task],
                              ao_buffer_n: int, do_buffer_n: int, ai_buffer_n: int,
                              ):
        """
        Configures the synchronization tasks for AI, AO, and DO.
        :param ao_task: The AO task object.
        :param do_task: The DO task object.
        :param ai_task: The AI task object.
        :param ci_tasks: The list of counter input tasks.
        :param ao_buffer_n: The number of samples for AO.
        :param do_buffer_n: The number of samples for DO.
        :param ai_buffer_n: The number of samples for AI.
        :raises:
            IOError: If the actual AI sample rate is not equal to the expected AI sample rate.
            IOError: If the actual AO sample rate is not equal to the expected AO sample rate.
            IOError: If the actual DO sample rate is not equal to the expected DO sample rate.
        """
        # CI use the same buffer size as AO, but it seems that to read it reliably, we need to
        # ask for extra room (which is automatically done on AI)
        ci_buffer_n = ao_buffer_n * 2

        if acq_settings.continuous:
            sample_mode = AcquisitionType.CONTINUOUS
            # Buffer size is a hint to "guide the internal buffer size" indicating how much data
            # will be read each time. It seems that internally the buffer is 8x bigger.
            ai_samples_n = ai_buffer_n
            ao_samples_n = ao_buffer_n
            ci_samples_n = ci_buffer_n
            do_samples_n = do_buffer_n
        else:
            sample_mode = AcquisitionType.FINITE
            # Hardware wants at least 2 samples. If it's less than that, ask 2, and we'll read just 1
            ai_samples_n = max(2, acq_settings.ai_samples_n)  # To indicate when it will finish
            ao_samples_n = max(2, acq_settings.ao_samples_n)
            ci_samples_n = ao_samples_n
            do_samples_n = max(2, acq_settings.do_samples_n)

        # See list of terminals for the options of source
        # https://www.ni.com/docs/en-US/bundle/ni-daqmx/page/mxcncpts/termnames.html
        # default source is "OnboardClock", which is equivalent to ai/SampleClockTimebase (or ao/SampleClockTimebase)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", nidaqmx.DaqWarning)
            ai_task.timing.cfg_samp_clk_timing(
                rate=acq_settings.ai_sample_rate,
                source="OnboardClock",
                sample_mode=sample_mode,
                samps_per_chan=ai_samples_n
            )
            ai_task.in_stream.input_buf_size = ai_buffer_n
            ai_sample_rate_actual = ai_task.timing.samp_clk_rate

        if not util.almost_equal(ai_sample_rate_actual, acq_settings.ai_sample_rate):
            raise IOError(f"AI sample rate accepted to {ai_sample_rate_actual}, "
                          f"while expected {acq_settings.ai_sample_rate}")

        # logging.debug(f"AI buffer size = {ai_task.in_stream.input_buf_size}, board buffer size: {ai_task.in_stream.input_onbrd_buf_size}")

        # Use the adjusted AI sample rate to select the AO (and DO) sample rates
        ao_task.timing.cfg_samp_clk_timing(
            rate=acq_settings.ao_sample_rate,
            # source=ai_task.timing.samp_clk_src, # Using the same clock as AI is accepted, but doesn't seem to help
            source="OnboardClock",
            sample_mode=sample_mode,
            samps_per_chan=ao_samples_n
        )
        ao_task.out_stream.output_buf_size = ao_buffer_n
        ao_sample_rate_actual = ao_task.timing.samp_clk_rate
        if not util.almost_equal(ao_sample_rate_actual, acq_settings.ao_sample_rate):
            raise IOError(f"AO sample rate accepted to {ao_sample_rate_actual}, "
                          f"while expected {acq_settings.ao_sample_rate}")

        if acq_settings.do_samples_n:
            # In continuous mode, samps_per_chan indicates the size of the buffer.
            # For DO, the buffer needs to be at least as big as the whole data written.
            do_task.timing.cfg_samp_clk_timing(acq_settings.do_sample_rate,
                                               sample_mode=sample_mode,
                                               samps_per_chan=do_samples_n,
                                               )
            do_task.out_stream.output_buf_size = do_buffer_n

            # TODO: instead of using isclose(), have a special function that converts between rate and period in ns
            # The value in ns is actually what the hardware uses, and we know it's a int, so that
            # would avoid all the floating point issues.
            do_sample_rate_actual = do_task.timing.samp_clk_rate
            if not util.almost_equal(do_sample_rate_actual, acq_settings.do_sample_rate):
                raise IOError(f"DO sample rate accepted to {do_sample_rate_actual}, while expected {acq_settings.do_sample_rate}")

        # To ensure all the tasks start simultaneously, we use one task to trigger
        # the other ones. We use the AI task to start the AO and DO tasks (because
        # the AI is usually the one with the fastest sample rate).
        # However, there is a little bit of delay between starting the AI task,
        # emitting the trigger, receiving the trigger and starting the AO/DO tasks.
        # so the AI task tends to start too early. To compensate, we delay the AI
        # task by one sample (and set AO & DO to the minimum, which is 2 board ticks).
        # Note: from trials, it seems we could reduce the AI task delay to just
        # 2 ticks of the AI base-clock (= 1 / ai_max_single_chan_rate SECONDS).
        # However, 1 sample clock also works, and in practice it's usually
        # near the AI base-clock, so not an issue, and easier to write.
        ai_task.triggers.start_trigger.delay = 1  # 1 sample clock is the minimum
        ai_task.triggers.start_trigger.delay_units = DigitalWidthUnits.SAMPLE_CLOCK_PERIODS
        # TODO: does it delay the beginning of the AI sampling (but AO is triggered just after the AI is triggered),
        # or the start trigger is delayed (and AO is triggered only after that delay). Probably the
        # first case (from experiments), but might be worth double-checking.
        # Note: on the simulator, the delay doesn't seem to have any effect. Even delaying by 10s doesn't
        # extend the acquisition duration at all. However, it works on the actual hardware. Could check
        # also using loopback ao -> ai, with special waveforms which have very big value differences
        # between each sample (eg + 1v), and only ~10 values (repeating) in X.
        # ai_task.triggers.start_trigger.delay = 20
        # ai_task.triggers.start_trigger.delay_units = DigitalWidthUnits.SECONDS
        ao_task.triggers.start_trigger.delay = 2  # 2 ticks is minimum
        ao_task.triggers.start_trigger.delay_units = DigitalWidthUnits.TICKS  # 20ns on the pcie6361

        # Connect the triggers
        ao_task.triggers.start_trigger.cfg_dig_edge_start_trig(ai_task.triggers.start_trigger.term)
        ao_task.out_stream.auto_start = False

        # Configure counter task(s)
        for ci_task in ci_tasks:
            ci_task.timing.cfg_samp_clk_timing(acq_settings.ao_sample_rate,
                                               source=ao_task.timing.samp_clk_term,
                                               active_edge=Edge.RISING,
                                               sample_mode=sample_mode,
                                               samps_per_chan=ci_samples_n,
                                               )

            ci_task.in_stream.input_buf_size = ci_buffer_n
            # For counter *input*, it's not the "start trigger" which is used, but the "arm start trigger".
            ci_task.triggers.arm_start_trigger.trig_type = TriggerType.DIGITAL_EDGE
            ci_task.triggers.arm_start_trigger.dig_edge_edge = Edge.RISING
            ci_task.triggers.arm_start_trigger.dig_edge_src = ao_task.timing.samp_clk_term

        if acq_settings.do_samples_n:
            do_task.triggers.start_trigger.delay = 2  # 2 ticks is minimum
            do_task.triggers.start_trigger.delay_units = DigitalWidthUnits.TICKS
            do_task.triggers.start_trigger.cfg_dig_edge_start_trig(ai_task.triggers.start_trigger.term)
            do_task.out_stream.auto_start = False

    @classmethod
    def _downsample_data(cls,
                         data: numpy.ndarray,  # Y x X (no margin)
                         res: Tuple[int, int],  # X, Y
                         margin: int,
                         acquired_n: int,
                         osr: int,
                         buffer: numpy.ndarray,  # N
                         prev_samples_n: int,
                         prev_samples_sum: int,
                         acc_dtype: numpy.dtype=numpy.float64,  # dtype to store the sum
                         average: bool = True,
                         ) -> Tuple[int, int]:  # samples_n, samples_sum
        """
        Downsample the provided acquisition data, and store it at the final place into the
        image array. It accepts any size of acquisition data, with one exception:
        it assumes that buffer never has too much data to fit inside the final data.
        The downsample is done by averaging multiple samples together, in relatively
        optimized way.
        :param data: (2D array of shape YX) final image data. It does NOT contain the margin.
        :param res: X, Y dimensions of the image (pixels)
        :param margin: size of the X margin (pixels)
        :param acquired_n: number of samples acquired and processed so far. So
        *not* including the samples contained in the buffer.
        :param osr: over-sampling ratio (number of samples to average/sum together)
        :param buffer: (1D array of shape N) any number of samples lastly acquired
        :param prev_samples_n: number of samples lastly processed but not yet completing
        a whole pixel. It should be the samples_n returned by the last call.
        :param prev_samples_sum: sum of samples lastly processed but not yet completing
        a whole pixel. It should be the samples_sum returned by the last call.
        :param acc_dtype: the numpy data type to use for the accumulator. Typically, it
        should be the smallest (for optimization) type that fits the sum of osr
        samples.
        :param average: if True, computes the average value (ie, sum/osr), otherwise store the sum
        :returns:
            * samples_n: the number of the (last) samples which could not be fully
            fitted in a pixel yet.
            * samples_sum: the sum of the (last) samples which could not be fully
            fitted in a pixel yet.
        """
        if buffer.shape[0] == 0:
            logging.warning("Empty buffer received at %d pixels, nothing to downsample", acquired_n)
            return prev_samples_n, prev_samples_sum

        line_width = res[0] + margin
        # 1- Finish the previous pixel
        if prev_samples_n > 0:
            # 1.1 Compute the x,y (margin as negative)
            pixel_n = acquired_n // osr
            x, y = (pixel_n % line_width) - margin, pixel_n // line_width
            # take the left over data
            pixel_buffer = buffer[:osr - prev_samples_n]
            new_samples_n = pixel_buffer.shape[0]
            pixel_samples_n = prev_samples_n + new_samples_n
            if x >= 0:  # no need if inside the margin
                pixel_sum = prev_samples_sum + pixel_buffer.sum(dtype=acc_dtype)
            else:
                pixel_sum = 0  # Anything is fine, as it's in the margin
            # if not enough pixels, update the sum (unless it's in the margin), return
            if pixel_samples_n < osr:
                return pixel_samples_n, pixel_sum
            # else get the final pixels, sum, compute the average, store (unless it's in the margin)
            if x >= 0:
                data[y, x] = pixel_sum / osr  # automatically converted to the dtype
            acquired_n += new_samples_n
            buffer = buffer[new_samples_n:]

        # 2 -compute the average value of all the full contained pixels
        # 2.1 Compute the average value of the partial initial line
        # How many pixel still fit in the line
        new_samples_n = buffer.shape[0]
        available_pixels = new_samples_n // osr
        # Compute the x,y (margin as negative)
        pixel_n = acquired_n // osr
        x, y = (pixel_n % line_width) - margin, pixel_n // line_width
        if available_pixels > 0 and x > -margin:
            max_length = line_width - (margin + x)  # including the margin
            line_length = min(available_pixels, max_length)
            new_samples_n = line_length * osr
            # Skip margin pixels
            margin_pixels_n = -x if x < 0 else 0
            buffer_pixels = buffer[margin_pixels_n * osr:new_samples_n]
            # Downsample the whole set of data to the given location
            if buffer_pixels.size > 0:
                cls._downsample_pixels(data, (max(0, x), y), buffer_pixels, osr, acc_dtype)

            # Update the pointers
            acquired_n += new_samples_n
            buffer = buffer[new_samples_n:]

        # 2.2 Compute the average value of the middle lines
        new_samples_n = buffer.shape[0]
        available_pixels = new_samples_n // osr
        available_lines = available_pixels // line_width
        if available_lines > 0:  # Is there at least one full line to copy?
            # Compute the x,y (margin as negative)
            pixel_n = acquired_n // osr
            x, y = (pixel_n % line_width) - margin, pixel_n // line_width
            assert x == -margin  # Now, we know that we start at a full line
            assert available_lines <= (res[1] - y)  # We assume to never receive too much data
            # Compute number of lines
            block_length = line_width * available_lines
            new_samples_n = block_length * osr
            # Reshape & clip buffer to "hide" the margin?
            buffer_2d = buffer[:new_samples_n]
            buffer_2d.shape = (available_lines, line_width * osr)
            buffer_2d = buffer_2d[:, margin * osr:]
            # Downsample the whole set of data to the given location
            cls._downsample_pixels(data, (0, y), buffer_2d, osr, acc_dtype, average)

            # Update the pointers
            acquired_n += new_samples_n
            buffer = buffer[new_samples_n:]

        # 2.3 Compute the average value of the partial last line
        new_samples_n = buffer.shape[0]
        available_pixels = new_samples_n // osr
        assert available_pixels < line_width
        if available_pixels > 0:
            pixel_n = acquired_n // osr
            x, y = (pixel_n % line_width) - margin, pixel_n // line_width
            assert x == -margin  # We know that we start at a full line
            new_samples_n = available_pixels * osr
            # Skip margin pixels
            buffer_pixels = buffer[margin * osr:new_samples_n]
            # Downsample the whole set of data to the given location (which always starts at the beginning of the line)
            if buffer_pixels.size > 0:
                cls._downsample_pixels(data, (0, y), buffer_pixels, osr, acc_dtype, average)

            # Update the pointers
            buffer = buffer[new_samples_n:]

        # 3 - Compute the partial sum of the next pixel
        new_samples_n = buffer.shape[0]
        pixel_sum = buffer.sum(dtype=acc_dtype) if new_samples_n else 0

        return new_samples_n, pixel_sum

    @classmethod
    def _downsample_pixels(cls, data: numpy.array,
                           pos: Tuple[int, int],
                           buffer: numpy.array,
                           osr: int,
                           acc_dtype,
                           average: bool = True,
                           ) -> None:
        """
        Average the given data, and store it at the specific area
        :param data: complete numpy array (shape YX) where to store the result
        :param pos: x, y position of the first sample (= left-top)
        :param buffer: should be of shape N, when storing less than one line, and shape MN when storing a 2D block.
        N must be a multiple of osr.
        :param osr: number of samples to average per point
        :param acc_dtype: data type for the temporary array that will sum all the data. It has to
        be large enough to fit osr * buffer.dtype
        :param average: if True, computes the average value (ie, sum/osr), otherwise store the sum
        :raise:
          ValueError: if the shape of buffer is not a multiple of osr
        """
        # Add a dimension to buffer by breaking last dim into X * osr
        shape = buffer.shape
        if len(shape) == 1:
            shape = (1,) + shape
        buffer_osr = numpy.reshape(buffer, (-1, shape[-1] // osr, osr))

        # Access data as just the part needed
        x, y = pos
        assert 0 <= x
        assert 0 <= y
        subdata = data[y: y + buffer_osr.shape[-3], x: x + buffer_osr.shape[-2]]

        # Compute average and store immediately in the final array
        if osr == 1:  # Fast path
            subdata[:] = buffer_osr[:, :, 0]
        elif average:
            # TODO: this is not optimal, because a temporary "acc" array is created. It should be
            # possible to compute the mean using a single temporary scalar.
            # At least, we could instantiate a temporary array at the beginning of an acquisition,
            # and always reuse it. It should be easy to compute the maximum size based on the size of
            # the AI buffer.

            # Inspired by _mean() from numpy, but save the accumulated value in
            # a separate array of a big enough dtype.
            acc = numpy.add.reduce(buffer_osr, axis=2, dtype=acc_dtype)
            numpy.true_divide(acc, osr, out=subdata, casting='unsafe', subok=False)
        else:  # Just the sum
            numpy.add.reduce(buffer_osr, axis=2, out=subdata)


class Scanner(model.Emitter):
    """
    Represents the e-beam scanner

    Note that the .resolution, .translation, .scale and .rotation VAs are
      linked, so that the region of interest stays approximately the same (in
      terms of physical space). So to change them to specific values, it is
      recommended to set them in the following order: Rotation > Scale >
      Resolution > Translation.
    """

    def __init__(self, name: str, role: str, parent: AnalogSEM,
                 channels: List[int],
                 limits: List[List[float]],
                 park: Optional[List[float]] = None,
                 scanning_ttl: Dict[int, List] = None,
                 pixel_ttl: Optional[List[int]] = None,
                 line_ttl: Optional[List[int]] = None,
                 frame_ttl: Optional[List[int]] = None,
                 settle_time: float = 0,
                 scan_active_delay: float = 0,
                 hfw_nomag: float = 0.1,
                 max_res: List[int] = (4096, 4096),
                 **kwargs):
        """
        :param channels (2-tuple of (0<=int)): output channels for X/Y to drive.
        X is the fast scanned axis, Y is the slow scanned axis.
        :param limits (2x2 array of float): lower/upper bounds of the scan area in V.
        first dim is the X/Y, second dim is beginning/end value. Ex: limits[0][1] is the
        voltage for the end value of X. Note that beginning can be larger than end
        value. In this case, the scanning direction will be reversed.
        :param park (None or 2-tuple of (0<=float)): voltage (in V) of resting position,
        if None, it will default to top-left corner. If the beam cannot be blanked
        this will be the position of the beam when not scanning.
        :param pixel_ttl: digital output channels (on port0) to indicate the beginning
        of a scan of a pixel. It goes high the first half of the duration of a pixel.
        :param line_ttl: digital output channels (on port0) to indicate the beginning
        of a line, not including the settling time. It goes high on the first
        pixel of the line, and goes down at the end of the last pixel.
        :param frame_ttl: digital output channels (on port0) to indicate the beginning
        of a frame, not including the settling time. It goes high on the first
        pixel of the frame, and goes down at the end of the last pixel.
        :param scanning_ttl (None or dict of int -> (bool, Optional[str], bool))):
        List of digital output ports to indicate the ebeam is scanning or not.
        * First argument is "high_auto": if True, it is set to high when scanning,
        with False, the output is inverted.
        * Second argument is "high_enabled": if True, it will be set
        to high when VA is True (see third argument). Otherwise, this is inverted.
        * Third argument is "va_name": if not None, a VigilantAttribute (VA) with that name will be
        created, and will allow to force the TTL to enabled (True) or disabled (False), in
        addition to the automatic behaviour (None) which is the default. Note that it's allowed
        to have multiple channels linked to the same VA.
        :param settle_time (0<=float<=1e-3): time in s for the signal to settle after
        each scan line, when scanning the whole field-of-view.
        :param scan_active_delay (None or 0<=float): minimum time (s) to wait before starting
        to scan, to "warm-up" when going from non-scanning state to scanning.
        :param hfw_nomag (0<float<=1): (theoretical) distance between horizontal borders
        (lower/upper limit in X) if magnification is 1 (in m)
        :param max_res (None or 2-tuple of (0<int)): maximum scan resolution allowed.
        """
        # It will set up ._shape and .parent
        model.Emitter.__init__(self, name, role, parent=parent, **kwargs)

        if len(channels) != 2:
            raise ValueError("E-beam scanner '%s' needs 2 channels" % (name,))

        if len(channels) != len(set(channels)):
            raise ValueError("Duplicated channels %r on device '%s'"
                             % (channels, parent._device_name))

        self._channels = channels
        self._channel_names = []
        for c in channels:
            # Any channel with such number?
            nichan = f"{self.parent._device_name}/ao{c}"
            if nichan not in self.parent._nidev.ao_physical_chans:
                raise ValueError(f"AO channel {c} not available")
            self._channel_names.append(nichan)

        if settle_time < 0:
            raise ValueError("Settle time of %g s for e-beam scanner '%s' is negative"
                             % (settle_time, name))
        elif settle_time > 1:
            # a larger value is a sign that the user mistook in units
            raise ValueError("Settle time of %g s for e-beam scanner '%s' is too long"
                             % (settle_time, name))
        self._settle_time = settle_time

        if scan_active_delay is not None and not 0 <= scan_active_delay < 1000:
            raise ValueError("scan_active_delay %g s is not between 0 and 1000 s" % (scan_active_delay,))
        self._scan_active_delay = scan_active_delay

        if len(parent._nidev.ao_physical_chans) < 2:
            raise ValueError("Device '%s' has only %d output channels, needs at least 2"
                             % (parent._device_name, len(parent._nidev.ao_physical_chans)))

        self._limits = limits

        # This is not the task that will be used to do the acquisition, but a
        # similar one, which is used to gather information about the AO settings.
        self._prepare_ao_task = nidaqmx.Task()

        # check the channel exist and limits are reachable
        self.configure_ao_task(self._prepare_ao_task)

        if park is None:
            park = limits[0][0], limits[1][0]
        elif len(park) != 2:
            raise ValueError(f"park must be a 2-tuple of float but got {park}")
        # Prepare the task, and also check that the voltage is acceptable for the hardware.
        # For the voltage limits, we just need to have limits that fits the (single) value that will be output,
        # but the configuration requires to have a range, which must not be 2 different values. So we have to find
        # a second value which is also valid, but *different* from park. => use any of the scan limits.
        park_limits = [[park[0], limits[0][0] if limits[0][0] != park[0] else limits[0][1]],
                       [park[1], limits[1][0] if limits[1][0] != park[1] else limits[1][1]]]
        self._park_task = nidaqmx.Task()
        self.configure_ao_task(self._park_task, park_limits)
        self._park_data = numpy.array(park, dtype=float)  # We write directly the voltage, so "float"

        # Manage the "slow" TTL signals
        self._scanning_ttl = {}  # Task -> high_auto (bool), high_enabled (bool), name (str)
        self._ttl_setters = []  # To hold the partial VA setters
        self._ttl_tasks = {}  # NI-DAQmx task to control the given D
        self._ttl_lock = threading.Lock()  # Acquire to change the hw TTL state

        # configure each channel for output
        available_do_ports = self._get_available_do_channels(port_number=0)
        for c, v in scanning_ttl.items():
            if not isinstance(v, Iterable) or len(v) != 3:
                raise ValueError("scanning_ttl expects for each channel a "
                                 "[boolean, boolean, name], but got %s" % (v,))
            high_auto, high_enabled, vaname = v
            if (not isinstance(high_auto, bool) or
                not isinstance(high_enabled, bool) or
                not (vaname is None or isinstance(vaname, str))
               ):
                raise ValueError("scanning_ttl expects for each channel a "
                                 "[boolean, boolean, name], but got %s" % (v,))
            if c not in available_do_ports:
                raise ValueError("DAQ device '%s' does not have digital output %s, available ones: %s" %
                                 (parent._device_name, c, sorted(available_do_ports)))

            task = nidaqmx.Task()
            task.do_channels.add_do_chan(
                f"{parent._device_name}/port0/line{c}", line_grouping=LineGrouping.CHAN_PER_LINE
            )
            self._scanning_ttl[task] = v
            # Note: it's fine to have multiple channels assigned to the same VA
            if vaname and not hasattr(self, vaname):
                setter = functools.partial(self._setTTLVA, vaname)
                self._ttl_setters.append(setter)
                # Create a VA with False (off), True (on) and None (auto, the default)
                va = model.VAEnumerated(None, choices={False, True, None},
                                        setter=setter)
                setattr(self, vaname, va)

        # Validate fast TTLs
        fast_do_channels = set()  # set of ints, to check all channels are unique
        if not all (v is None or isinstance(v, list) for v in (pixel_ttl, line_ttl, frame_ttl)):
            raise ValueError("pixel_ttl, line_ttl and frame_ttl should be lists of int, but got %s, %s, %s" %
                             (pixel_ttl, line_ttl, frame_ttl))
        pixel_ttl = pixel_ttl or []
        line_ttl = line_ttl or []
        frame_ttl = frame_ttl or []
        for do_channel in pixel_ttl + line_ttl + frame_ttl:
            # Check it's a valid channel
            if do_channel not in available_do_ports:
                raise ValueError("DAQ device '%s' does not have digital output %s, available ones: %s" %
                                 (parent._device_name, do_channel, sorted(available_do_ports)))

            if do_channel in scanning_ttl:
                raise ValueError(f"scanning_ttl and pixel_ttl/line_ttl/frame_ttl cannot have the same channel ({do_channel})")

            if do_channel in fast_do_channels:
                raise ValueError(f"pixel_ttl/line_ttl/frame_ttl cannot have the same channel ({do_channel})")
            fast_do_channels.add(do_channel)

        self._pixel_ttl = pixel_ttl
        self._line_ttl = line_ttl
        self._frame_ttl = frame_ttl

        # TODO: have a better way to indicate the channel number as it's limited to port0
        # while there is also port 1 & 2. Explicitly ask the full NI name? as "port1/line3"? Or as written on hardware "P1.3"?
        self._fast_do_names = ",".join(f"{self.parent._device_name}/port0/line{n}" for n in fast_do_channels)

        # Manage the scanning state
        self._scan_state_req = queue.Queue()
        self._scan_state = True  # To force changing the digital output when the state go to False
        self._scanning_ready = threading.Event()  # The scan_state is True & waited long enough
        self._scanning_mng = threading.Thread(target=self._scan_state_mng_run, name="Scanning state manager")
        self._scanning_mng.daemon = True
        self._scanning_mng.start()
        self.indicate_scan_state(False)

        # In theory the maximum resolution depends on the precision of the e-beam
        # coils in the scanner, and on the signal to drive it (voltage range,
        # precision of the D/A converter...).
        # For simplicity we just fix it to 4096 by default, which is probably
        # sufficient for most usages and almost always achievable, and allow to
        # override it.
        if max_res is None:
            self._shape = (4096, 4096)
        else:
            max_res = tuple(max_res)
            if len(max_res) != 2:
                raise ValueError(f"max_res should be 2 integers >= 1 but got {max_res}.")
            if any(r > 2 ** 14 for r in max_res):
                raise ValueError(f"max_res {max_res} too big: maximum 16384 px allowed.")
            self._shape = max_res

        # next two values are just to determine the pixel size
        # Distance between borders if magnification = 1. It should be found out
        # via calibration. We assume that pixels are square, i.e., max_res ratio
        # = physical ratio
        if not 0 <= hfw_nomag < 1:
            raise ValueError(f"hfw_nomag is {hfw_nomag} m, while it should be between 0 and 1 m.")
        self._hfw_nomag = hfw_nomag  # m
        # TODO: make it a VA?

        # Allow the user to modify the value, to copy it from the SEM software
        mag = 1e3  # pretty random value which could be real
        self.magnification = model.FloatContinuous(mag, range=(1, 1e9), unit="")
        self.magnification.subscribe(self._onMagnification)

        # pixelSize is the same as MD_PIXEL_SIZE, with scale == 1
        # == smallest size/ between two different ebeam positions
        pxs = (self._hfw_nomag / (self._shape[0] * mag),) * 2
        self.pixelSize = model.VigilantAttribute(pxs, unit="m", readonly=True)

        # (.resolution), .translation, .rotation, and .scaling are used to
        # define the conversion from coordinates to a region of interest.

        # (float, float) in px => moves center of acquisition by this amount
        # independent of scale and rotation.
        tran_rng = ((-self._shape[0] / 2, -self._shape[1] / 2),
                    (self._shape[0] / 2, self._shape[1] / 2))
        self.translation = model.TupleContinuous((0, 0), tran_rng,
                                                  cls=(int, float), unit="px",
                                                  setter=self._setTranslation)
        self.translation.subscribe(self._on_setting_changed)

        # .resolution is the number of pixels actually scanned. If it's less than
        # the whole possible area, it's centered.
        # Start with 256 x 256, for a quick scan.
        resolution = (256, int(256 * self._shape[1] / self._shape[0]))
        self.resolution = model.ResolutionVA(resolution, ((1, 1), self._shape),
                                             setter=self._setResolution)
        self._resolution = resolution
        self.resolution.subscribe(self._on_setting_changed)

        # (float, float) as a ratio => how big is a pixel, compared to pixelSize
        # it basically works the same as binning, but can be float
        # (Default to scan the whole area, with the scale identical on both directions)
        self._scale = (self._shape[0] / resolution[0],) * 2
        self.scale = model.TupleContinuous(self._scale, ((1, 1), self._shape),
                                           cls=(int, float),
                                           unit="", setter=self._setScale)
        self.scale.subscribe(self._onScale, init=True)  # to update metadata
        self.scale.subscribe(self._on_setting_changed)

        # # (float) in rad => rotation of the image compared to the original axes
        # # TODO: for now it's readonly because no rotation is supported
        # self.rotation = model.FloatContinuous(0, (0, 2 * math.pi), unit="rad",
        #                                       readonly=True)

        min_dt = self.parent.get_min_dwell_time(1)
        # max dwell time is purely arbitrary
        range_dwell = (min_dt, 1000)  # s
        self.dwellTime = model.FloatContinuous(min_dt, range_dwell,
                                               unit="s", setter=self._setDwellTime)
        self.dwellTime.subscribe(self._on_setting_changed)

        # Cached data for the waveforms
        self._prev_settings = [None, None, None, None, None]  # resolution, scale, translation, margin, ao_osr
        self._scan_array = None  # last scan array computed
        self._ao_osr = 1
        self._ai_osr = 1
        self._nrchans = 0

    def __del__(self):
        if hasattr(self, "_prepare_ao_task"):
            self._prepare_ao_task.close()
        if hasattr(self, "_park_task"):
            self._park_task.close()
        if hasattr(self, "_scanning_ttl"):  # can happen to be False if the __init__ fails
            for t in self._scanning_ttl:
                t.close()

    def terminate(self):
        if self._scanning_mng:
            self.indicate_scan_state(False)
            self._scan_state_req.put(None)
            self._scanning_mng.join(10)
            self._scanning_mng = None

    def get_hw_buf_size(self) -> int:
        """
        Return the size of the hardware buffer for AO samples in bytes, per channel
        :return: > 0
        """
        return self._park_task.out_stream.output_onbrd_buf_size

    def configure_ao_task(self, ao_task: nidaqmx.Task, limits: Optional[List[List[float]]] = None):
        """
        Adjust the task settings to the channels and limit defined by the user
        :param ao_task: the Task to configure
        :param limits: the min/max voltage for each (2) channels. By default, it uses the _limits as
        needed for a standard full FoV scan.
        raise ValueError: if the user settings don't match the hardware
        """
        if limits is None:
            limits = self._limits

        for i, cname in enumerate(self._channel_names):
            data_lim = limits[i]
            if len(data_lim) != 2:
                raise ValueError(f"limits {i} should be of length 2, but got {data_lim}")

            try:
                aoc = ao_task.ao_channels.add_ao_voltage_chan(
                    cname,
                    min_val=min(data_lim),
                    max_val=max(data_lim),
                    units=VoltageUnits.VOLTS
                )

                # TODO: investigate
                # aoc.ao_data_xfer_mech = nidaqmx.constants.DataTransferActiveTransferMode.DMA

                # It will only check when actually *reading* back the accepted value
                # Note: aoc.ao_min & aoc.ao_max contain the requested range
                rng = aoc.ao_dac_rng_low, aoc.ao_dac_rng_high  # Actual range used by the device
                logging.debug(f"AO channel {cname} for limits {data_lim} set to range {rng} V")
            except nidaqmx.DaqError:
                raise ValueError(f"Data range between {data_lim[0]} and {data_lim[1]} V is too high for hardware.")

    def configure_do_task(self, do_task):
        """
        Adjust the task settings to the DO channels for fast TTLs as specified by the user
        raise:
            ValueError: if the settings defined by the user are invalid
        """
        if not self._fast_do_names:
            # It's OK, the task is just going to do nothing... should just not write to it.
            return

        do_task.do_channels.add_do_chan(
            self._fast_do_names,
            line_grouping=LineGrouping.CHAN_FOR_ALL_LINES,  # data array should be of shape (samples,)
        )
        logging.debug(f"Added DO channels: {self._fast_do_names}")

    def _get_available_do_channels(self, port_number: int) -> Set[int]:
        """
        Find the digital output channel (aka line) available on a given port, based on the
        NI-DAQmx information about the hardware.
        :param port_number: (0 <= int<= 8) the port number.
        :return: the available channels
        """
        available_ports = set()
        port_root = f"{self.parent._device_name}/port{port_number}/line"
        for dopc in self.parent._nidev.do_lines:
            if dopc.name.startswith(port_root):
                available_ports.add(int(dopc.name[len(port_root):]))

        return available_ports

    @roattribute
    def channels(self):
        return self._channels

    @roattribute
    def settleTime(self):
        return self._settle_time

    @roattribute
    def HFWNoMag(self):
        # TODO: make it a VA, to make it easier to calibrate
        return self._hfw_nomag

    def pixelToPhy(self, px_pos):
        """
        Converts a position in pixels to physical (at the current magnification)
        Note: the convention is that in internal coordinates Y goes down, while
        in physical coordinates, Y goes up.
        px_pos (tuple of 2 floats): position in internal coordinates (pixels)
        returns (tuple of 2 floats): physical position in meters
        """
        pxs = self.pixelSize.value  # m/px
        phy_pos = (px_pos[0] * pxs[0], -px_pos[1] * pxs[1])  # - to invert Y
        return phy_pos

    def _on_setting_changed(self, _):
        """
        Called when a VA affecting the image scanning changes
        """
        self.parent._acquirer.update_settings_on_next_frame()

    def _onMagnification(self, mag):
        self._metadata[model.MD_LENS_MAG] = mag

        # Pixel size is the same in both dimensions
        pxs = (self._hfw_nomag / (self._shape[0] * mag),) * 2
        # The VA contains the pixelSize for a scale == 1
        self.pixelSize._set_value(pxs, force_write=True)

        self._updatePixelSizeMD()

    def _onScale(self, s):
        self._updatePixelSizeMD()

    def _updatePixelSizeMD(self):
        # If scaled up, the pixels are bigger => update metadata
        pxs = self.pixelSize.value
        scale = self.scale.value
        pxs_scaled = (pxs[0] * scale[0], pxs[1] * scale[1])
        self._metadata[model.MD_PIXEL_SIZE] = pxs_scaled

    def _setDwellTime(self, value):
        # If multiple acquisitions are started, a different dwell time might be
        # selected during the acquisition, compared to what is defined here.
        nrchans = max(1, len(self.parent._acquirer.active_detectors))
        return self._updateDwellTime(value, nrchans)

    def _updateDwellTime(self, dt, nrchans):
        dt, self._ao_osr, self._ai_osr = self.parent.find_best_dwell_time(dt, nrchans)
        self._nrchans = nrchans
        return dt

    def _setScale(self, value):
        """
        value (1 < float, 1 < float): increase of size between pixels compared to
         the original pixel size. It will adapt the translation and resolution to
         have the same ROI (just different amount of pixels scanned)
        return the actual value used
        """
        prev_scale = self._scale
        self._scale = value

        # adapt resolution so that the ROI stays the same
        change = (prev_scale[0] / self._scale[0],
                  prev_scale[1] / self._scale[1])
        old_resolution = self.resolution.value
        new_resolution = (max(int(round(old_resolution[0] * change[0])), 1),
                          max(int(round(old_resolution[1] * change[1])), 1))
        # no need to update translation, as it's independent of scale and will
        # be checked by setting the resolution.
        self.resolution.value = new_resolution  # will call _setResolution()
        return value

    def _setResolution(self, value):
        """
        value (0<int, 0<int): defines the size of the resolution. If the
         resolution is not possible, it will pick the most fitting one. It will
         recenter the translation if otherwise it would be out of the whole
         scanned area.
        returns the actual value used
        """
        max_size = (int(self._shape[0] / self._scale[0]),
                    int(self._shape[1] / self._scale[1]))

        # at least one pixel, and at most the whole area
        size = (max(min(value[0], max_size[0]), 1),
                max(min(value[1], max_size[1]), 1))
        self._resolution = size

        # setting the same value means it will recheck the boundaries with the
        # new resolution, and reduce the distance to the center if necessary.
        self.translation.value = self.translation.value
        return size

    def _setTranslation(self, value):
        """
        value (float, float): shift from the center. It will always ensure that
          the whole ROI fits the screen.
        returns actual shift accepted
        """
        # compute the min/max of the shift. It's the same as the margin between
        # the centered ROI and the border, taking into account the scaling.
        max_tran = ((self._shape[0] - self._resolution[0] * self._scale[0]) / 2,
                    (self._shape[1] - self._resolution[1] * self._scale[1]) / 2)

        # between -margin and +margin
        tran = (max(min(value[0], max_tran[0]), -max_tran[0]),
                max(min(value[1], max_tran[1]), -max_tran[1]))
        return tran

    def _setTTLVA(self, vaname, value):
        """
        Changes the TTL value of a TTL signal
        vaname (str)
        value (True, False or None)
        return value
        """
        with self._ttl_lock:
            for t, (high_auto, high_enabled, name) in self._scanning_ttl.items():
                if name != vaname:
                    continue
                try:
                    if value is None:
                        # Put it as the _set_scan_state would
                        v = (high_auto == self._scan_state)
                    else:  # Use the value as is (and invert it if not high_enabled)
                        v = (high_enabled == value)
                    logging.debug("Setting digital output %s to %s", t.channel_names[0], v)
                    t.write(v)
                except nidaqmx.DaqError:
                    logging.warning("Failed to change digital output %s to %s", t.channel_names[0], v, exc_info=True)

        return value

    def _write_park_position(self):
        """
        Set the beam to the park position. That's to ensure the beam is always at the same position
        when not scanning. Especially important when blanker TTL is not available.
        """
        logging.debug("Setting to park position at %s", self._park_data)
        try:
            self._park_task.write(self._park_data, auto_start=True)
            logging.debug("Park position set")
        except nidaqmx.DaqError:
            logging.warning("Failed to set to park position", exc_info=True)

    def indicate_scan_state(self, scanning: bool, delay: float = 0) -> None:
        """
        Indicate the ebeam scanning state (via the digital output ports).
        When changing to True, blocks until the scanning is ready.
        :param scanning: if True, indicate it's scanning, otherwise, indicate it's
          parked.
        :param delay: (0 <= float) time to the state to be set to False (if state
        hasn't been requested to change to True in-between).
        If state is set to True, it has to be 0.
        """
        if scanning:
            if delay > 0:
                raise ValueError("Cannot delay starting the scan")

            self._scan_state_req.put(True)
            self._scanning_ready.wait()

        else:
            self._scan_state_req.put(time.time() + delay)

    def _scan_state_mng_run(self):
        """
        Main loop for scan state manager thread:
        Switch on/off the scan state based on the requests received
        """
        try:
            q = self._scan_state_req
            stopt = None  # None if must be on, otherwise time to stop
            while True:
                # wait for a new message or for the time to stop the encoder
                now = time.time()
                if stopt is None or not q.empty():
                    msg = q.get()
                elif now < stopt:  # soon time to turn off the encoder
                    timeout = stopt - now
                    try:
                        msg = q.get(timeout=timeout)
                    except queue.Empty:
                        # time to stop the encoder => just do the loop again
                        continue
                else:  # time to stop
                    # the queue should be empty (with some high likelyhood)
                    # Normally parking the beam has already been done (when the message was received)
                    # but just to be certain, do it again.
                    self._write_park_position()
                    self._set_scan_state(False)
                    self._scanning_ready.clear()
                    stopt = None
                    # We now should have quite some time free, let's run the garbage collector.
                    # Note that when starting the next acquisition, if a long time
                    # has elapsed, the GC will immediately run. That would
                    # probably not be necessary and we could avoid this by setting
                    # the last GC time to the starting time. However, this is
                    # also not a big deal as it runs while waiting for the hardware.
                    # So we don't do it to avoid complexifying further the code.
                    self.parent._gc_while_waiting(None)
                    continue

                # parse the new message
                logging.debug("Decoding scanning state message %s", msg)
                if msg is None:  # The end?
                    if stopt is not None:
                        # Should set the scan state to off soon, instead, immediately do it
                        self._write_park_position()
                        self._set_scan_state(False)
                    return
                elif msg is True:  # turn on the scan state, and wait until ready
                    self._set_scan_state(True)
                    self._scanning_ready.set()
                    stopt = None
                else:  # time at which to turn off the scan state
                    if stopt is not None:
                        stopt = min(msg, stopt)
                    else:
                        stopt = msg
                    # Already move the e-beam back to the park position
                    self._write_park_position()

        except Exception:
            logging.exception("Scanning state manager failed:")
        finally:
            logging.info("Scanning state manager thread over")

    def _set_scan_state(self, scanning):
        """
        Indicate the ebeam scanning state (via the digital output ports)
        scanning (bool): if True, indicate it's scanning, otherwise, indicate it's
          parked.
        """
        with self._ttl_lock:
            logging.debug("Requested scanning state to %s, while it is at %s", scanning, self._scan_state)
            if self._scan_state == scanning:
                return  # No need to update, if it's already correct

            logging.debug("Updating scanning state to %s", scanning)
            self._scan_state = scanning
            for t, (high_auto, high_enabled, name) in self._scanning_ttl.items():
                if name and getattr(self, name).value is not None:
                    logging.debug("Skipping digital output %s set to manual", t.channel_names[0])
                    continue
                try:
                    v = (high_auto == scanning)
                    logging.debug("Automatic setting digital output %s to %s", t.channel_names[0], v)
                    t.write(v)
                except nidaqmx.DaqError:
                    logging.warning("Failed to change digital output %s to %s", t.channel_names[0], v, exc_info=True)

            if scanning and self._scan_active_delay:
                logging.debug("Waiting for %g s for the beam to be ready", self._scan_active_delay)
                time.sleep(self._scan_active_delay)

    # Waveform generation:
    # The goal is to generate the position of the e-beam in X and Y for the given dwell time and
    # other settings. Essentially, it should have a fast dimension scan (typically X) and a slow
    # dimension (Y). We use the standard scan left->right and top->bottom. At the end of each
    # line the beam has to "flyback" quickly back to beginning of the next line. It needs a bit
    # of time to reach other side and "settle" there. That exact time depends on the SEM
    # characteristics and is defined by the "settle time". (That could be avoided by using the
    # so-called "zigzag" pattern, but such pattern has the tendency to have slight misalignment
    # between the odd (left->right) and even (right-> left) lines which gives a sort of
    # "interlacing effect" so it's usually not used for imaging). So the X and Y waveforms look
    # like a sawtooth (amplitude vs time):
    # A
    # ▲
    # │       /|      /|      /|      /|      /|
    # │      / |     / |     / |     / |     / |
    # │     /  |    /  |    /  |    /  |    /  |
    # ├────/   └───/   └───/   └───/   └───/   └....
    # └────────────────────────────────────────────► t
    #                     X (over a few lines)
    # A
    # ▲
    # :                                 ....        :
    # │                        ────────             |
    # │                ────────                     |
    # │        ────────                             |
    # ├────────                                     └
    # └─────────────────────────────────────────...──► t
    #                   Y (over a few lines)
    # Over the whole frame the Y graph looks like a single sawtooth. Note that there are various
    # ways to handle the flyback signal. The most basic way is to just repeat the first position
    # for the entire settle time. This is what we currently do. On the opposite size, a fancy way
    # can be to decompose into two parts. One part before the linear increase, which is already a
    # linear increase, but below the starting point. The second part is a smoothstep function
    # (which has no strong derivative) going from the right to the left point. The advantage of
    # the first part is to allow the beam to acquire the right "speed". It also avoids the first
    # pixel to be exposed more than the rest of the pixels (which can be noticeable in the
    # image). The main drawback with this approach is that it can expose the area just left to
    # the image area.
    #
    # Simultaneously there are signals to indicate the different states of the scanning. Firstly,
    # there is the "scanning" signal which is active every time the scan is active, even a little
    # before and after (to give time to the SEM to react to it, and avoid switching if a new
    # acquisition follows). There are three more status signals: pixel, line, and frame. The
    # pixel signal goes active when the beam moves to a new pixel. The line and frame signal go
    # active for respectively the whole line and frame. So they look like:
    # A
    # ▲
    # :
    # │  ┌─────────────────────────────────────...
    # │──┘                                        └──  frame
    # │  ┌────────────┐  ┌────────────┐ ┌──────...
    # │──┘            └──┘            └─┘         └──  line
    # │  ┌┐┌┐┌┐┌┐┌┐┌┐┌┐  ┌┐┌┐┌┐┌┐┌┐┌┐┌┐  ┌┐┌┐┌┐
    # │──┘└┘└┘└┘└┘└┘└┘└──┘└┘└┘└┘└┘└┘└┘└──┘└┘└┘└...└──  pixel
    # └────────────────────────────────────────...───► t

    def _get_scan_waveforms(self, nrchans: int) -> Tuple[
            numpy.ndarray, Optional[numpy.ndarray],
            float, int, int, Tuple[int, int], int
            ]:
        """
        For the given scan settings, return the analog and digital waveforms
        nrchans (0 <= int): number of simultaneous channels to read (as this number
        affects the dwell time and ai_osr)
        returns:
            analog_wf: a 2D numpy array of shape (2, N), dtype int16: N is the number
                of samples (ie, (X + margin) * Y * ao_osr). The first row is for the
                fast dimension (eg, X) and the second row for the slow dimension
                (typically Y).
            tll_signal: numpy array of shape (N*2, C), dtype bool: the digital signal
              indicating up to three events: pixels are started, when a line is being scanned,
              and when the frame is being scanned. Only the signals that should be
              output are passed (and ordered in the same way as configure_do_task()
              expects them). If no signal are to be output, it's None.
            dwell_time: dwell time used for the waveform
            ao_osr: over-sampling rate, how many output samples should be generated by pixel
              Note that the numpy arrays do not contain the samples duplicated.
              The duplication has to be performed by the caller.
            ai_osr: over-sampling rate, how many input samples should be acquired by pixel
            resolution: resolution used for the waveform
            margin: number of extra pixels added in the X dimension for flyback
        """
        # with self._dt_lock: # TODO: Read/write everything in a (recursive) lock
        if nrchans != self._nrchans:
            # force updating the dwell time for this new number of read channels
            prev_dt = self.dwellTime.value
            dt = self._updateDwellTime(prev_dt, nrchans)
            if dt != prev_dt:
                self.dwellTime._value = dt
                self.dwellTime.notify(dt)
            if nrchans != self._nrchans:
                raise ValueError(f"Cannot run {nrchans} channels currently")
        ao_osr = self._ao_osr
        ai_osr = self._ai_osr
        dwell_time = self.dwellTime.value
        resolution = self.resolution.value
        scale = self.scale.value
        translation = self.translation.value

        # settle_time is proportional to the size of the ROI (and =0 if only 1 px)
        st = self._settle_time * scale[0] * (resolution[0] - 1) / (self._shape[0] - 1)
        # Round-up if settle time represents more than 1% of the dwell time.
        # Below 1% the improvement would be marginal, and that allows to have
        # tiny areas (eg, 4x4) scanned without the first pixel of each line
        # being exposed twice more than the others.
        margin = int(math.ceil(st / dwell_time - 0.01))

        new_settings = [resolution, scale, translation, margin, ao_osr]
        if self._prev_settings != new_settings:
            self._update_raw_scan_array(resolution, scale, translation, margin, ao_osr)
            self._prev_settings = new_settings

        return (self._scan_array,
                self._ttl_signal,
                dwell_time,
                ao_osr,
                ai_osr,
                resolution,
                margin)

    def _update_raw_scan_array(self, shape, scale, translation, margin, dup):
        """
        Update the raw array of values to send to scan the 2D area.
        :param shape: (list of 2 int): X/Y of the scanning area (slow, fast axis)
        :param scale: (tuple of 2 float): scaling of the pixels
        :param translation: (tuple of 2 float): shift from the center
        :param margin: (0<=int): number of additional pixels to add at the beginning of
            each scanned line
        :param dup: (1<=int): how many times each pixel should be duplicated
        returns nothing, but update ._scan_array.
        """
        full_res = self._shape[:2]
        # adapt limits according to the scale and translation so that if scale
        # == 1,1 and translation == 0,0 , the area is centered and a pixel is
        # the size of pixelSize
        roi_limits = []  # min/max for X/Y in V
        roi_limits_raw = []  # min/max for X/Y in raw value (int16)
        for i, lim in enumerate(self._limits):
            center = (lim[0] + lim[1]) / 2
            volt_diff = lim[1] - lim[0]  # can be negative
            ratio = (shape[i] * scale[i]) / full_res[i]
            if ratio > 1.000001:  # cannot be bigger than the whole area
                raise ValueError("Scan area too big: %s * %s > %s" %
                                 (shape, scale, full_res))
            elif ratio > 1:
                # Note: in theory, it'd be impossible for the ratio to be > 1,
                # however, due to floating error, it might happen that a scale
                # a tiny bit too big is allowed. For example:
                # shape = 5760, res = 1025, scale = 5.619512195121952
                # int(shape/scale) * scale == 5760.000000000001
                logging.warning("Scan area appears too big due to floating error (ratio=%g), limiting to maximum",
                                ratio)
                ratio = 1

            # center_comp is to ensure the point scanned of each pixel is at the
            # *center* of the area of each pixel (which corresponds to a square
            # in a grid over the whole area)
            center_comp = (shape[i] - 1) / shape[i]
            roi_hwidth = ((volt_diff * ratio) / 2) * center_comp
            pxv = volt_diff / full_res[i]  # V/px
            shift = translation[i] * pxv
            roi_lim = (center + shift - roi_hwidth,
                       center + shift + roi_hwidth)
            if lim[0] < lim[1]:
                if not lim[0] <= roi_lim[0] <= roi_lim[1] <= lim[1]:
                    raise ValueError("ROI limit %s > limit %s, with area %s * %s" %
                                     (roi_lim, lim, shape, scale))
            else:  # inverted scan direction
                if not lim[0] >= roi_lim[0] >= roi_lim[1] >= lim[1]:
                    raise ValueError("ROI limit %s > limit %s, with area %s * %s" %
                                     (roi_lim, lim, shape, scale))
            roi_limits.append(roi_lim)

            # computes the limits in raw values
            ao_channel = self._prepare_ao_task.ao_channels[i]
            roi_limits_raw.append((self.volt_to_raw(ao_channel, roi_lim[0]),
                                   self.volt_to_raw(ao_channel, roi_lim[1])))

        logging.debug("ranges X = %sV, Y = %sV, for shape %s + margin %d",
                      roi_limits[0], roi_limits[1], shape, margin)

        scan_array = self._generate_scan_array(shape, roi_limits_raw, margin, dup)
        self._scan_array = scan_array.reshape(2, -1)  # flatten the YX+dup dimensions

        # ttl_signal = self._generate_signal_array(shape, margin, dup)
        ttl_signal = self._generate_signal_array_bits(shape, margin, dup)
        if ttl_signal is not None:
            ttl_signal = ttl_signal.ravel()  # flatten the YX+dup dimensions
        self._ttl_signal = ttl_signal

    @staticmethod
    def volt_to_raw(ao_channel: "AOChannel", volt: float) -> int:
        """
        Convert voltage to raw value, for a AO channel. The AO channel should be
        already configured for a specific range.
        :param volt: the voltage to output, assuming it fits the range.
        returns: a value fitting in an int16. It will be clipped to the min/max
        values of an int16
        """
        # Typically, ao_dev_scaling_coeff looks like [0.0, 3244.4] => very simple polynomial
        coeff = ao_channel.ao_dev_scaling_coeff
        poly = polynomial.Polynomial(coeff)
        # convert to the closest int
        raw = int(round(poly(volt)))
        # Make sure it fits within a int16, as the polynomial and the rounding could make it too big
        return min(max(-32768, raw), 32767)

    @staticmethod
    def _generate_scan_array(res: Tuple[int, int],
                             limits: Tuple[Tuple[int, int]],
                             margin: int,
                             dup: int,
                             ) -> numpy.ndarray:
        """
        Generate an array of the values to send to scan a 2D area, using linear
        interpolation between the limits. It's basically a saw-tooth curve on
        the fast dimension and a linear increase on the H dimension.
        :param res: size of the scanning area (X=fast, Y=slow axis)
        :param limits: the min/max limits of fast, slow axes. Must NOT be numpy.uint
        :param margin (0<=int): number of additional pixels to add at the beginning of
            each scanned line
        :param dup: (1<=int): how many times each pixel should be duplicated
        :returns (4D ndarray of 2,  Y, (X + margin), dup of int16): the
            values for each points of the array, with X scanned fast, and Y
            slowly.
        """
        # prepare an array of the right type
        full_shape = (2, res[1], res[0] + margin, dup)
        scan = numpy.empty(full_shape, dtype=numpy.int16, order='C')  # TODO: is this alway this dtype? Use a get_ao_dtype()?
        scan_dup = numpy.moveaxis(scan, 3, 0)  # Move dup to the back, to tell numpy everything needs to be copied

        # fill the Y dimension, by copying the X over every Y value
        # swap because we the broadcast rule is going to duplicate on the first dimension(s)
        scany = scan_dup[:, 1, :, :].swapaxes(1, 2)
        # Note: it's important that limits contain Python int's, and not numpy.uint's,
        # because with uint's, linspace() goes crazy when limits go high->low.
        scany[:, :, :] = numpy.linspace(limits[1][0], limits[1][1], res[1])
        # fill the X dimension
        scan_dup[:, 0, :, margin:] = numpy.linspace(limits[0][0], limits[0][1], res[0])

        # fill the margin with the first pixel (X dimension is already filled)
        if margin:
            scan_dup[:, 0, :, :margin] = limits[0][0]

        return scan

    def _generate_signal_array_bits(self,
                                    res: Tuple[int, int],
                                    margin: int,
                                    dup: int,
                                    ) -> Optional[numpy.ndarray]:
        """
        :param res: size of the scanning area (X=fast, Y=slow axis)
        :param margin (0<=int): number of additional pixels to add at the beginning of
            each scanned line
        :param dup: (1<=int): how many times each pixel should be duplicated
        :return:
            ttl_signal: numpy array of shape Y,X*2, dup, dtype uint32: the digital signal indicating
            when the pixel, line, frame start, as bits.
            The bits are set to match the *_ttl options.
            If not TTLs are to be changed, None is returned
        """
        if not self._fast_do_names:
            # It's OK, the task is just going to do nothing... should just not write to it.
            return None

        # Y dim is as expected, X dim is twice longer, to have the rate twice higher
        # than the dwell time, with half of the dwell time the pixel signal high
        # and half of the pixel signal the dwell time low. That's the slowest rate
        # that allows to distinguish each pixel.
        full_shape = (res[1], 2 * (res[0] + margin), dup)
        ttl_signal = numpy.zeros(full_shape, dtype=numpy.uint32, order='C')

        # Pixel: everything after the margin, is filled with alternating high/low
        for c in self._pixel_ttl:
            pixel_bit = numpy.uint32(1 << c)
            ttl_signal[:, margin * 2::2, 0] |= pixel_bit

        # Line: everything after the margin is the line
        # Special array view, with dup to as first dim, to tell numpy everything needs to be copied
        ttl_signal_dup = numpy.moveaxis(ttl_signal, 2, 0)
        for c in self._line_ttl:
            line_bit = numpy.uint32(1 << c)
            ttl_signal_dup[:, :, margin * 2:] |= line_bit
            # Special case when there is no margin: make it low as the end of the line, to get a transition
            # TODO: if there is really some hardware that rely on the precise timing for line and frame
            # signals, even on such special cases (eg, spot mode), that might not be good enough. We
            # would need to increase the TTL rate to AI rate, so that the last value corresponds to a very
            # short time.
            if not margin:
                ttl_signal_dup[-1, :, -1] &= ~line_bit

        # Frame: almost everywhere high, except for the margin of the first line
        for c in self._frame_ttl:
            frame_bit = numpy.uint32(1 << c)
            frame_signal = ttl_signal_dup.reshape(dup, res[1] * 2 * (res[0] + margin))
            frame_signal[:, margin * 2:] |= frame_bit
            # Special case when there is no margin: make it low as the end of the frame, to get a transition
            if not margin:
                frame_signal[-1, -1] &= ~frame_bit

        return ttl_signal

    def _generate_signal_array_end(self) -> Optional[numpy.ndarray]:
        """
        Return the data to set the fast TTLs when the scan stops
        :return: ttl_signal: numpy array of shape 1, dtype uint32
        If not TTLs are to be changed, None is returned
        """
        if not self._fast_do_names:
            # It's OK, the task is just going to do nothing... should just not write to it.
            return None

        # Everything is off => all bits are 0 => 0.
        return numpy.zeros(1, dtype=numpy.uint32)


class AnalogDetector(model.Detector):
    """
    Represents an analog detector activated by energy caused by the e-beam.
    E.g., secondary electron detector, backscatter detector, analog PMT.
    """

    def __init__(self, name: str, role: str, parent: AnalogSEM,
                 channel: int,
                 limits: List[float],
                 **kwargs):
        """
        channel (0<= int): input channel from which to read
        limits (2-tuple of number): min/max voltage to acquire (in V). If the
          first value > second value, the data is inverted (=reversed contrast)
        """
        # It will set up ._shape and .parent
        super().__init__(name, role, parent=parent, **kwargs)

        self._channel = channel
        if isinstance(channel, int):
            self._channel_name = f"{self.parent._device_name}/ai{channel}"
            # Check that the channel exist
            if self._channel_name not in self.parent._nidev.ai_physical_chans:
                raise ValueError(f"AI channel {channel} not available")
        elif channel.startswith("ao"):
            self._channel_name = self._get_analog_loopback_channel(channel)
            # Whether this is actually an acceptable channel will be checked when creating a task
        else:
            raise ValueError(f"channel must be an int (or a string of format 'aoN'), but got {channel}")

        if limits[0] > limits[1]:
            logging.info("Will invert the data as limit is inverted")
            self.inverted = True
            limits = (limits[1], limits[0])
        else:
            self.inverted = False

        self._limits = limits

        self._shape = (2 ** 16,)  # only one point, 16 bit (signed, but this info is not conveyed)
        self.data = SEMDataFlow(self, parent, self.inverted)

        # Special event to request software unblocking on the scan
        self.softwareTrigger = model.Event()

        self._metadata[model.MD_DET_TYPE] = model.MD_DT_NORMAL

    def _get_analog_loopback_channel(self, ao_channel_name: str) -> str:
        """
        ao_channel_name: as in "ao0"
        """
        return f"{self.parent._device_name}/_{ao_channel_name}_vs_aognd"

    def configure_ai_task(self, ai_task):
        # Note: we always configure the channels for differential reading
        # (so it uses 2 physical connections: channel and channel + 8)
        # We currently have no usage of single-ended connection. If necessary,
        # that could be configured via another argument to the class.
        try:
            aic = ai_task.ai_channels.add_ai_voltage_chan(
                self._channel_name,
                terminal_config=TerminalConfiguration.DIFF,  # NRSE=Single-ended
                min_val=self._limits[0],
                max_val=self._limits[1],
                units=VoltageUnits.VOLTS)
            rng = aic.ai_rng_low, aic.ai_rng_high
            logging.debug(f"AI channel {self._channel} for limits {self._limits} set to range {rng[0]}->{rng[1]} V")
        except nidaqmx.DaqError as ex:
            raise ValueError(f"Data range between {self._limits[0]} and {self._limits[1]} V is too high for hardware.")


class CountingDetector(model.Detector):
    """
    Represents a detector which observe a pulsed signal (rising edges). Typically, the higher
    the pulse frequency, the stronger the original signal is. E.g., a counting
    PMT.
    """
    def __init__(self, name, role, parent, source, **kwargs):
        """
        source (0 <= int): PFI number, the input pin on which the signal is received
        """
        # It will set up ._shape and .parent
        super().__init__(name, role, parent=parent, **kwargs)

        # Try to find an available counter
        for chan in self.parent._nidev.ci_physical_chans:
            cnt_name = chan.name
            if cnt_name not in [d._counter_name for d in parent._counting_dets]:
                # Found a counter not yet used
                self._counter_name = cnt_name
                logging.debug("Using counter %s for counting detector '%s'", cnt_name, name)
                break
        else:
            raise ValueError(f"No counter available anymore, only {len(self.parent._nidev.ci_physical_chans)} available")

        self._source_name = f"/{self.parent._device_name}/PFI{source}"
        # Check that the channel exist
        if self._source_name not in self.parent._nidev.terminals:
            raise ValueError(f"Source PFI{source} not available")
        self.source = source

        maxdata = 2 ** self.parent._nidev.ci_max_size
        self._shape = (maxdata + 1,)  # only one point
        self.data = SEMDataFlow(self, parent)

        # Special event to request software unblocking on the scan
        self.softwareTrigger = model.Event()

        self._metadata[model.MD_DET_TYPE] = model.MD_DT_INTEGRATING

    def configure_ci_task(self, task):
        # create a counter input channel using 'ctr0' to count
        # rising digital edges, counting up from initial_count
        task.ci_channels.add_ci_count_edges_chan(
            self._counter_name,
            edge=Edge.RISING,
            initial_count=0,
            count_direction=CountDirection.COUNT_UP)

        # set the input terminal of the counter input channel on which
        # the counter receives the signal on which it counts edges
        task.ci_channels[0].ci_count_edges_term = self._source_name

        # By default, the counter is "cummulative": it never resets. But we want to reset it
        # after every sample read
        # TODO: clock name should come from argument? In some way it's always correct, right?
        task.ci_channels[0].ci_count_edges_count_reset_term = f'/{self.parent._device_name}/ao/SampleClock'
        task.ci_channels[0].ci_count_edges_count_reset_reset_cnt = 0
        task.ci_channels[0].ci_count_edges_count_reset_enable = True


# Copied from semcomedi
class SEMDataFlow(model.DataFlow):

    def __init__(self, detector: Optional[AnalogDetector | CountingDetector],
                 sem: AnalogSEM, inverted=False):
        """
        detector: the detector that the dataflow corresponds to
        sem: the SEM
        inverted: whether the data values must be inverted
        """
        model.DataFlow.__init__(self)
        self.component = weakref.ref(detector)
        self._sem = weakref.proxy(sem)

        self._sync_event = None  # event to be synchronised on, or None
        self._evtq = None  # a Queue to store received events (= float, time of the event)
        self._prev_max_discard = self._max_discard

        self.inverted = inverted

    def notify(self, data):
        if self.inverted:
            # ~ is better than - as it handles the range asymmetry of int16 (-32768 -> 32767)
            # The only "odd" part is that 0 <-> -1. That doesn't matter as it's all
            # arbitrary values.
            data = ~data

        super().notify(data)

    # start/stop_generate are _never_ called simultaneously (thread-safe)
    def start_generate(self):
        comp = self.component()
        if comp is None:
            # sem/component has been deleted, it's all fine, we'll be GC'd soon
            return

        try:
            self._sem._acquirer.add_detector(comp)
        except ReferenceError:
            # sem/component has been deleted, it's all fine, we'll be GC'd soon
            pass

    def stop_generate(self):
        comp = self.component()
        if comp is None:
            # sem/component has been deleted, it's all fine, we'll be GC'd soon
            return

        try:
            self._sem._acquirer.remove_detector(comp)
            if self._sync_event:
                self._evtq.put(None)  # in case it was waiting for an event
        except ReferenceError:
            # sem/component has been deleted, it's all fine, we'll be GC'd soon
            pass

    def synchronizedOn(self, event):
        """
        Synchronize the acquisition on the given event. Every time the event is
          triggered, the scanner will start a new acquisition/scan.
          The DataFlow can be synchronized only with one Event at a time.
          However each DataFlow can be synchronized, separately. The scan will
          only start once each active DataFlow has received an event.
        event (model.Event or None): event to synchronize with. Use None to
          disable synchronization.
        """
        if self._sync_event == event:
            return

        if self._sync_event:
            self._sync_event.unsubscribe(self)
            self.max_discard = self._prev_max_discard
            if not event:
                self._evtq.put(None)  # in case it was waiting for this event

        self._sync_event = event
        if self._sync_event:
            # if the df is synchronized, the subscribers probably don't want to
            # skip some data
            self._evtq = queue.Queue()  # to be sure it's empty
            self._prev_max_discard = self._max_discard
            self.max_discard = 0
            self._sync_event.subscribe(self)

    @oneway
    def onEvent(self):
        """
        Called by the Event when it is triggered
        """
        if not self._evtq.empty():
            logging.warning("Received synchronization event but already %d queued",
                            self._evtq.qsize())

        self._evtq.put(time.time())

    # Functions for use by the Acquirer
    def _wait_sync(self, timeout: Optional[float] = None) -> None:
        """
        Block until the Event on which the dataflow is synchronised has been
          received. If the DataFlow is not synchronised on any event, this
          method immediately returns
        :raise: TimeoutError if the timeout was reached
        """
        if self._sync_event:
            try:
                self._evtq.get(timeout=timeout)
            except queue.Empty:
                raise TimeoutError(f"No sync event within {timeout} s")

    def _is_synchronized(self) -> bool:
        """
        :return: True if the DataFlow is synchronised on an event
        """
        return self._sync_event is not None
