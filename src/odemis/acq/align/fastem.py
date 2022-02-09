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

from odemis import model

try:
    import fastem_calibrations
    from fastem_calibrations import (
        autofocus_multiprobe,
        scan_rotation_pre_align,
        scan_amplitude_pre_align,
        image_translation_pre_align,
        descan_gain,
        image_rotation_pre_align,
        image_rotation,
        image_translation
    )
except ImportError:
    fastem_calibrations = None

# TODO does it make sense to make this a list?
OPTICAL_AUTOFOCUS = "autofocus_multiprobe"
SCAN_ROTATION_PREALIGN = "scan_rotation_pre_align"
SCAN_AMPLITUDE_PREALIGN = "scan_amplitude_pre_align"
DESCAN_GAIN_STATIC = "descan_gain_static"
IMAGE_ROTATION_PREALIGN = "image_rotation_pre_align"
IMAGE_TRANSLATION_PREALIGN = "image_translation_pre_align"
IMAGE_ROTATION_FINAL = "image_rotation"
IMAGE_TRANSLATION_FINAL = "image_translation"

# The executor is a single object, independent of how many times the module (fastem.py) is loaded.
_executor = model.CancellableThreadPoolExecutor(max_workers=1)


def align(scanner, multibeam, descanner, detector, stage, ccd, beamshift, det_rotator, calibrations):
    """
    Start a calibration task for a given list of calibrations.

    :param scanner: (xt_client.Scanner) Scanner component connecting to the XT adapter.
    :param multibeam: (technolution.EBeamScanner) The multibeam scanner component of the acquisition server module.
    :param descanner: (technolution.MirrorDescanner) The mirror descanner component of the acquisition server module.
    :param detector: (technolution.MPPC) The detector object to be used for collecting the image data.
    :param stage: (actuator.ConvertStage) The stage in the corrected scan coordinate system, the x and y axes are
        aligned with the x and y axes of the multiprobe and the multibeam scanner.
    :param ccd: (model.DigitalCamera) A camera object of the diagnostic camera.
    :param beamshift: (tfsbc.BeamShiftController) Component that controls the beamshift deflection.
    :param det_rotator: (tmcm.CANController) K-mirror controller.
    :param calibrations: (list of str) List of calibrations that should be run.

    :returns: (ProgressiveFuture) Alignment future object, which can be cancelled.
            The result of the future is:
                (Exception or None): Exception raised during the acquisition or None.
    """

    if fastem_calibrations is None:
        raise ModuleNotFoundError("fastem_calibration module missing. Cannot run calibrations.")

    f = model.ProgressiveFuture()

    # Create a task that runs the calibration and alignments.
    task = CalibrationTask(scanner, multibeam, descanner, detector, stage, ccd, beamshift, det_rotator, f, calibrations)

    f.task_canceller = task.cancel  # lets the future know how to cancel the task.

    # Connect the future to the task and run it in a thread.
    # task.run is executed by the executor and runs as soon as no other task is executed
    _executor.submitf(f, task.run)

    return f


def estimate_calibration_time(calibrations):
    """
    Computes the approximate time it will take to run all calibrations.
    :param calibrations: (list of str) List of calibrations that should be run.
    :return (0 <= float): The estimated time for the requested calibrations in s.
    """
    tot_time = 0
    for calib in calibrations:
        # TODO how to use a list of function calls in this loop to avoid writing the same line
        #  for each calibration?
        if calib == OPTICAL_AUTOFOCUS:
            tot_time += autofocus_multiprobe.estimate_calibration_time()
        if calib == IMAGE_TRANSLATION_PREALIGN:
            tot_time += image_translation_pre_align.estimate_calibration_time()
        if calib == SCAN_AMPLITUDE_PREALIGN:
            tot_time += scan_amplitude_pre_align.estimate_calibration_time()
        if calib == DESCAN_GAIN_STATIC:
            tot_time += descan_gain.estimate_calibration_time()
        if calib == IMAGE_ROTATION_PREALIGN:
            tot_time += image_rotation_pre_align.estimate_calibration_time()
        if calib == IMAGE_TRANSLATION_PREALIGN:
            tot_time += image_translation_pre_align.estimate_calibration_time()
        if calib == IMAGE_ROTATION_FINAL:
            tot_time += image_rotation.estimate_calibration_time()
        if calib == IMAGE_TRANSLATION_FINAL:
            tot_time += image_translation.estimate_calibration_time()

    return tot_time


class CalibrationTask(object):
    """
    The calibration task.
    """

    def __init__(self, scanner, multibeam, descanner, detector, stage, ccd, beamshift, det_rotator, future, calibrations):
        """
        :param scanner: (xt_client.Scanner) Scanner component connecting to the XT adapter.
        :param multibeam: (technolution.EBeamScanner) The multibeam scanner component of the acquisition server module.
        :param descanner: (technolution.MirrorDescanner) The mirror descanner component of the acquisition server module.
        :param detector: (technolution.MPPC) The detector object to be used for collecting the image data.
        :param stage: (actuator.ConvertStage) The stage in the corrected scan coordinate system, the x and y axes are
            aligned with the x and y axes of the multiprobe and the multibeam scanner.
        :param ccd: (model.DigitalCamera) A camera object of the diagnostic camera.
        :param beamshift: (tfsbc.BeamShiftController) Component that controls the beamshift deflection.
        :param det_rotator: (tmcm.CANController) K-mirror controller.
        :param calibrations: (list of str) List of calibrations that should be run.

        :param future: (ProgressiveFuture) Acquisition future object, which can be cancelled.
                        (Exception or None): Exception raised during the calibration or None.
        """
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

        # List of calibrations to be executed. Used for progress update.
        self._calibrations_remaining = set(calibrations)

        # keep track if future was cancelled or not
        self._cancelled = False

        if model.MD_CALIB not in self._beamshift.getMetadata().keys():
            self._beamshift.updateMetadata({model.MD_CALIB: self._scanner.beamShiftTransformationMatrix.value})

    def run(self):
        """
        Runs a set of calibration procedures.
        :returns:
            (None) If calibrations successful return None.
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
            logging.debug("Starting calibration.")

            # loop over calibrations in list (order in list is important!)
            for calib in self.calibrations:
                # TODO return a sub-future when implemented for calibrations
                self.run_calibrations(calib)

                # def _pass_future_progress(sub_f, start, end):
                #     f.set_progress(start, end)

                # TODO Connect the progress of the sub-future to the main future
                # sf.add_update_callback(_pass_future_progress)
                # sf.result()

                # remove from list of calibrations when finished
                self._calibrations_remaining.discard(calib)

                # In case the calibrations was cancelled by a client, before the future returned,
                # raise cancellation error.
                if self._cancelled:
                    raise CancelledError()

                # Update the time left for the calibrations remaining
                self._future.set_progress(end=time.time() + self.estimate_calibration_time())

        except CancelledError:  # raised in try statement
            logging.debug("Calibration was cancelled.")
            raise
        except Exception as ex:
            raise ex
        finally:
            # Remove references to the calibrations once all calibrations are finished/cancelled.
            self._calibrations_remaining.clear()
            self._scanner.blanker.value = True  # always blank the beam to reduce beam damage on sample
            logging.debug("Finish calibrations.")

    def run_calibrations(self, calibration):
        """
        Run a calibration.
        # Note: All calibrations can be run on bare scintillator.
        """
        if calibration == OPTICAL_AUTOFOCUS:
            autofocus_multiprobe.run_autofocus(self._scanner, self._multibeam, self._descanner, self._detector,
                                               self._dataflow, self._ccd, self._stage)

        if calibration == SCAN_ROTATION_PREALIGN:
            scan_rotation_pre_align.run_scan_rotation_pre_align(self._scanner, self._multibeam, self._descanner,
                                                                self._detector, self._dataflow, self._ccd)

        if calibration == SCAN_AMPLITUDE_PREALIGN:
            scan_amplitude_pre_align.run_scan_amplitude_pre_align(self._scanner, self._multibeam, self._descanner,
                                                                  self._detector, self._dataflow, self._ccd)

        if calibration == DESCAN_GAIN_STATIC:
            descan_gain.run_descan_gain_static(self._scanner, self._multibeam, self._descanner,
                                               self._detector, self._dataflow, self._ccd)

        if calibration == IMAGE_ROTATION_PREALIGN:
            image_rotation_pre_align.run_image_rotation_pre_align(self._scanner, self._multibeam, self._descanner,
                                                                  self._detector, self._dataflow, self._ccd,
                                                                  self._det_rotator)

        if calibration == IMAGE_TRANSLATION_PREALIGN:
            image_translation_pre_align.run_image_translation_pre_align(self._scanner, self._multibeam,
                                                                        self._descanner, self._detector,
                                                                        self._dataflow, self._ccd)

        if calibration == IMAGE_ROTATION_FINAL:
            image_rotation.run_image_rotation(self._scanner, self._multibeam, self._descanner,
                                              self._detector, self._dataflow, self._det_rotator)

        if calibration == IMAGE_TRANSLATION_FINAL:
            image_translation.run_image_translation(self._scanner, self._multibeam, self._descanner,
                                                    self._detector, self._dataflow, self._ccd)

    def cancel(self, future):
        """
        Cancels the calibrations.
        :param future: (future) The calibration future.
        :return: (bool) True if cancelled, TODO False?
        """
        self._cancelled = True

        # FIXME Currently there is no subfuture implemented for each calibration. So currently,
        #  when cancelling the calibration is still finished.
        # TODO When to set this to False in which event?

        return True

    def estimate_calibration_time(self):
        """
        Computes the approximate time it will take to run the remaining calibrations.
        :return (0 <= float): The estimated time for the requested calibrations in s.
        """
        tot_time = 0
        for calib in self._calibrations_remaining:
            # TODO how to use a list of function calls in this loop to avoid writing the same line
            #  for each calibration?
            if calib == OPTICAL_AUTOFOCUS:
                tot_time += autofocus_multiprobe.estimate_calibration_time()
            if calib == IMAGE_TRANSLATION_PREALIGN:
                tot_time += image_translation_pre_align.estimate_calibration_time()
            if calib == SCAN_AMPLITUDE_PREALIGN:
                tot_time += scan_amplitude_pre_align.estimate_calibration_time()
            if calib == DESCAN_GAIN_STATIC:
                tot_time += descan_gain.estimate_calibration_time()
            if calib == IMAGE_ROTATION_PREALIGN:
                tot_time += image_rotation_pre_align.estimate_calibration_time()
            if calib == IMAGE_TRANSLATION_PREALIGN:
                tot_time += image_translation_pre_align.estimate_calibration_time()
            if calib == IMAGE_ROTATION_FINAL:
                tot_time += image_rotation.estimate_calibration_time()
            if calib == IMAGE_TRANSLATION_FINAL:
                tot_time += image_translation.estimate_calibration_time()

        return tot_time
