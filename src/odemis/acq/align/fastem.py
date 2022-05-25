#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 20 Jul 2021

@author: Philip Winkler, Sabrina Rossberger

Copyright © 2021 - 2022 Philip Winkler, Delmic

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
import logging
import time
from concurrent.futures import CancelledError
from enum import Enum

from odemis import model

try:
    from fastem_calibrations import (
        autofocus_multiprobe,
        scan_rotation_pre_align,
        descan_gain,
        scan_amplitude_pre_align,
        image_translation_pre_align,
        descan_gain,
        image_rotation_pre_align,
        image_rotation,
        image_translation,
        dark_offset_correction,
        digital_gain_correction,
        scan_rotation,
        scan_amplitude,
        cell_translation,
    )
    from fastem_calibrations.configure_hw import (
        get_config_asm,
        configure_asm
    )
    fastem_calibrations = True
except ImportError as err:
    logging.info("fastem_calibrations package not found with error: {}".format(err))
    autofocus_multiprobe = None
    scan_rotation_pre_align = None
    descan_gain = None
    scan_amplitude_pre_align = None
    image_rotation_pre_align = None
    image_translation_pre_align = None
    image_rotation = None
    image_translation = None
    dark_offset_correction = None
    digital_gain_correction = None
    scan_rotation = None
    scan_amplitude = None
    cell_translation = None

    fastem_calibrations = False

# The executor is a single object, independent of how many times the module (fastem.py) is loaded.
_executor = model.CancellableThreadPoolExecutor(max_workers=1)


class Calibrations(Enum):
    """
    Connect each calibration to a unique constant name.
    """
    OPTICAL_AUTOFOCUS = autofocus_multiprobe
    SCAN_ROTATION_PREALIGN = scan_rotation_pre_align
    DESCAN_GAIN_STATIC = descan_gain
    SCAN_AMPLITUDE_PREALIGN = scan_amplitude_pre_align
    IMAGE_ROTATION_PREALIGN = image_rotation_pre_align
    IMAGE_TRANSLATION_PREALIGN = image_translation_pre_align
    IMAGE_ROTATION_FINAL = image_rotation
    IMAGE_TRANSLATION_FINAL = image_translation
    DARK_OFFSET = dark_offset_correction
    DIGITAL_GAIN = digital_gain_correction
    SCAN_ROTATION_FINAL = scan_rotation
    SCAN_AMPLITUDE_FINAL = scan_amplitude
    CELL_TRANSLATION = cell_translation


def align(scanner, multibeam, descanner, detector, stage, ccd, beamshift, det_rotator, calibrations, stage_pos=None):
    """
    Start a calibration task for a given list of calibrations.

    :param scanner: (xt_client.Scanner) Scanner component connecting to the XT adapter.
    :param multibeam: (technolution.EBeamScanner) The multibeam scanner component of the acquisition server module.
    :param descanner: (technolution.MirrorDescanner) The mirror descanner component of the acquisition server module.
    :param detector: (technolution.MPPC) The detector object to be used for collecting the image data.
    :param stage: (actuator) The stage in the corrected scan coordinate system, the x and y axes are
            aligned with the x and y axes of the multiprobe and the multibeam scanner. Must have x, y and z axes.
    :param ccd: (model.DigitalCamera) A camera object of the diagnostic camera.
    :param beamshift: (tfsbc.BeamShiftController) Component that controls the beamshift deflection.
    :param det_rotator: (actuator) K-mirror controller. Must have a rotational (rz) axis.
    :param calibrations: (list[Calibrations]) List of calibrations that should be run.
    :param stage_pos: (float, float) Stage position where the calibration should be run. If None,
                      the calibration is run at the current stage position.

    :returns: (ProgressiveFuture) Alignment future object, which can be cancelled.
    """

    if not fastem_calibrations:
        raise ModuleNotFoundError("fastem_calibration module missing. Cannot run calibrations.")

    est_dur = estimate_calibration_time(calibrations)
    f = model.ProgressiveFuture(start=time.time(), end=time.time() + est_dur)

    # Create a task that runs the calibration and alignments.
    task = CalibrationTask(f, scanner, multibeam, descanner, detector, stage, ccd, beamshift, det_rotator,
                           calibrations, stage_pos)

    f.task_canceller = task.cancel  # lets the future know how to cancel the task.

    # Connect the future to the task and run it in a thread.
    # task.run is executed by the executor and runs as soon as no other task is executed
    _executor.submitf(f, task.run)

    return f


def estimate_calibration_time(calibrations):
    """
    Computes the approximate time it will take to run all calibrations.
    :param calibrations: (list[Calibrations]) List of calibrations that should be run.
    :return (0 <= float): The estimated time for the requested calibrations in s.
    """
    # Note: the check for None is only in case fastem_calibrations is missing
    return sum(c.value.estimate_calibration_time() for c in calibrations if c.value is not None)


class CalibrationTask(object):
    """
    The calibration task, which runs the calibrations according to the order in the list of calibrations passed.
    """

    def __init__(self, future, scanner, multibeam, descanner, detector, stage, ccd, beamshift, det_rotator,
                 calibrations, stage_pos):
        """
        :param future: (ProgressiveFuture) Acquisition future object, which can be cancelled.
                       (Exception or None): Exception raised during the calibration or None.
        :param scanner: (xt_client.Scanner) Scanner component connecting to the XT adapter.
        :param multibeam: (technolution.EBeamScanner) The multibeam scanner component of the acquisition server module.
        :param descanner: (technolution.MirrorDescanner) The mirror descanner component of the acquisition server
            module.
        :param detector: (technolution.MPPC) The detector object to be used for collecting the image data.
        :param stage: (actuator) The stage in the corrected scan coordinate system, the x and y axes are
            aligned with the x and y axes of the multiprobe and the multibeam scanner. Must have x, y and z axes.
        :param ccd: (model.DigitalCamera) A camera object of the diagnostic camera.
        :param beamshift: (tfsbc.BeamShiftController) Component that controls the beamshift deflection.
        :param det_rotator: (actuator) K-mirror controller. Must have a rotational (rz) axis.
        :param calibrations: (list[Calibrations]) List of calibrations that should be run.
        :param stage_pos: (float, float) Stage position where the calibration should be run in meter. If None,
                      the calibration is run at the current stage position.
        """
        self.asm_config = None
        self._scanner = scanner
        self._multibeam = multibeam
        self._descanner = descanner
        self._detector = detector
        self._dataflow = detector.data
        self._stage = stage
        self._ccd = ccd
        self._beamshift = beamshift
        self._det_rotator = det_rotator
        self._future = future

        self.calibrations = calibrations
        self.stage_pos = stage_pos

        # List of calibrations to be executed. Used for progress update.
        self._calibrations_remaining = set(calibrations)

        # keep track if future was cancelled or not
        self._cancelled = False

    def run(self):
        """
        Runs a set of calibration procedures and return the calibrated settings.
        :returns:
            self.asm_config: (nested dict) A dictionary containing factory and/or calibrated settings. Settings
            that are calibrated or are overwriting factory settings. The content of the dict is:
            multibeam:
                scanOffset: (tuple) The x and y start of the scanning movement (start of scan ramp) of the multibeam
                            scanner in arbitrary units.
                scanAmplitude: (tuple) The x and y heights of the scan ramp of the multibeam scanner in arbitrary units.
                dwellTime: (float) The acquisition time for one pixel within a cell image in seconds.
                resolution: (tuple) The effective resolution of a single field image excluding overscanned pixels
                                    in pixels.
            descanner:
                scanOffset: (tuple) The x and y start of the scanning movement (start of scan ramp) of the descanner
                            in arbitrary units.
                scanAmplitude: (tuple) The x and y heights of the scan ramp of the descanner in arbitrary units.
            mppc:
                cellCompleteResolution: (tuple) The resolution of a cell image including overscanned pixels in pixels.
                cellTranslation: (tuple of tuples of shape mppc.shape) The origin for each cell image within the
                                 overscanned cell image in pixels.
                cellDarkOffset: (tuple of tuples of shape mppc.shape) The dark offset correction for each cell image.
                cellDigitalGain: (tuple of tuples of shape mppc.shape) The digital gain correction for each cell image.
        :raise:
            Exception: If a calibration failed.
            CancelledError: If the calibration was cancelled.
        """

        # Get the estimated time for all requested calibrations.
        total_calibration_time = self.estimate_calibration_time()

        # No need to set the start time of the future: it's automatically done when setting its state to running.
        self._future.set_progress(end=time.time() + total_calibration_time)  # provide end time to future
        logging.info("Starting calibrations, with expected duration of %f s", total_calibration_time)

        try:
            logging.debug("Starting calibration, reading initial hardware settings.")
            self.asm_config = get_config_asm(self._multibeam, self._descanner, self._detector)

            # reset beamshift
            self._beamshift.shift.value = (0, 0)

            if self.stage_pos:
                # move to region of calibration (ROC) position
                sf = self._stage.moveAbs({'x': self.stage_pos[0], 'y': self.stage_pos[1]})
                sf.result()  # wait until stage is at correct position

            # loop over calibrations in list (order in list is important!)
            for calib in self.calibrations:
                logging.debug("Starting calibration %s", calib)
                # TODO return a sub-future when implemented for calibrations
                self.run_calibration(calib)

                # def _pass_future_progress(sub_f, start, end):
                #     f.set_progress(start, end)

                # TODO Connect the progress of the sub-future to the main future when sub-futures are implemented
                # sf.add_update_callback(_pass_future_progress)
                # sf.result()

                # remove from set of calibrations when finished
                self._calibrations_remaining.discard(calib)

                # In case the calibrations were cancelled by a client, before the future returned,
                # raise cancellation error.
                if self._cancelled:
                    raise CancelledError()

                # Update the time left for the calibrations remaining
                self._future.set_progress(end=time.time() + self.estimate_calibration_time())
                logging.debug("Finished calibration %s successfully", calib)

        except CancelledError:
            logging.debug("Calibration was cancelled.")
            raise
        except Exception as ex:
            logging.error("Calibration failed: %s", ex, exc_info=True)
            raise
        finally:
            # Remove references to the calibrations once all calibrations are finished/cancelled.
            self._calibrations_remaining.clear()
            self._scanner.blanker.value = True  # always blank the beam to reduce beam damage on sample
            # put system back into state ready for next task and set calibrated settings
            if self.asm_config is None:
                logging.warning("Failed to retrieve asm configuration, configure_asm cannot be executed.")
            else:
                configure_asm(self._multibeam, self._descanner, self._detector, self._dataflow, self.asm_config)
            logging.debug("Calibrations finished.")

        return self.asm_config

    def run_calibration(self, calibration):
        """
        Run a calibration.
        """
        if calibration == Calibrations.OPTICAL_AUTOFOCUS:
            calibration.value.run(self._scanner, self._multibeam, self._descanner, self._detector,
                                  self._dataflow, self._ccd, self._stage)

        elif calibration == Calibrations.SCAN_ROTATION_PREALIGN:
            calibration.value.run(self._scanner, self._multibeam, self._descanner,
                                  self._detector, self._dataflow, self._ccd)

        elif calibration == Calibrations.DESCAN_GAIN_STATIC:
            calibration.value.run(self._scanner, self._multibeam, self._descanner,
                                  self._detector, self._dataflow, self._ccd)

        elif calibration == Calibrations.SCAN_AMPLITUDE_PREALIGN:
            s_offset, s_amplitude = calibration.value.run(self._scanner, self._multibeam, self._descanner,
                                                          self._detector, self._dataflow, self._ccd)
            self.asm_config["multibeam"]["scanOffset"] = s_offset
            self.asm_config["multibeam"]["scanAmplitude"] = s_amplitude

        elif calibration == Calibrations.IMAGE_ROTATION_PREALIGN:
            calibration.value.run(self._scanner, self._multibeam, self._descanner, self._detector,
                                  self._dataflow, self._ccd, self._det_rotator)

        elif calibration == Calibrations.IMAGE_TRANSLATION_PREALIGN:
            d_offset = calibration.value.run(self._scanner, self._multibeam, self._descanner,
                                             self._detector, self._dataflow, self._ccd)
            self.asm_config["descanner"]["scanOffset"] = d_offset

        elif calibration == Calibrations.IMAGE_ROTATION_FINAL:
            calibration.value.run(self._scanner, self._multibeam, self._descanner,
                                  self._detector, self._dataflow, self._det_rotator)

        elif calibration == Calibrations.IMAGE_TRANSLATION_FINAL:
            d_offset = calibration.value.run(self._scanner, self._multibeam, self._descanner,
                                             self._detector, self._dataflow, self._ccd)
            self.asm_config["descanner"]["scanOffset"] = d_offset

        elif calibration == Calibrations.DARK_OFFSET:
            dark_offset = calibration.value.run(self._scanner, self._multibeam, self._descanner,
                                                self._detector, self._dataflow)
            self.asm_config["mppc"]["cellDarkOffset"] = dark_offset

        elif calibration == Calibrations.DIGITAL_GAIN:
            digital_gain = calibration.value.run(self._scanner, self._multibeam, self._descanner,
                                                 self._detector, self._dataflow)
            self.asm_config["mppc"]["cellDigitalGain"] = digital_gain

        elif calibration == Calibrations.SCAN_ROTATION_FINAL:
            calibration.value.run(self._scanner, self._multibeam, self._descanner, self._detector, self._dataflow)

        elif calibration == Calibrations.SCAN_AMPLITUDE_FINAL:
            s_offset, s_amplitude = calibration.value.run(self._scanner, self._multibeam, self._descanner,
                                                          self._detector, self._dataflow)
            self.asm_config["multibeam"]["scanOffset"] = s_offset
            self.asm_config["multibeam"]["scanAmplitude"] = s_amplitude

        elif calibration == Calibrations.CELL_TRANSLATION:
            translation, resolution, cell_complete_resolution = calibration.value.run(self._scanner, self._multibeam,
                                                                                      self._descanner, self._detector,
                                                                                      self._dataflow,
                                                                                      plot_cell_translation=True)
            self.asm_config["mppc"]["cellTranslation"] = translation
            self.asm_config["multibeam"]["resolution"] = resolution
            self.asm_config["mppc"]["cellCompleteResolution"] = cell_complete_resolution

        else:
            raise ValueError(f"Unknown calibration {calibration.name}.")

        # TODO only needed for an acquisition -> move to acquisition code by reading the calibrated values
        #  from the respective MD (implement similar method to get_config_asm)
        configure_asm(self._multibeam, self._descanner, self._detector, self._dataflow, self.asm_config, upload=False)

    def cancel(self, future):
        """
        Cancels the calibrations.
        :param future: (future) The calibration future.
        :return: (bool) True if cancelled.
        """
        self._cancelled = True

        # FIXME Currently there is no subfuture implemented for each calibration.
        #  So, when cancelling while a calibration has already started it will run until it is completely finished.

        return True

    def estimate_calibration_time(self):
        """
        Computes the approximate time it will take to run the remaining calibrations.
        :return (0 <= float): The estimated time for the remaining calibrations in s.
        """
        return estimate_calibration_time(self._calibrations_remaining)
