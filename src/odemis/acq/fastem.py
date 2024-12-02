#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 19 Apr 2021

@author: Philip Winkler, Éric Piel, Thera Pals, Sabrina Rossberger

Copyright © 2021-2022 Philip Winkler, Delmic

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
import json
import logging
import math
import os
import threading
import time
from typing import Optional
from concurrent.futures import CancelledError

import numpy

try:
    from fastem_calibrations import util as fastem_util

    fastem_calibrations = True
except ImportError as err:
    logging.info("fastem_calibrations package not found with error: {}".format(err))
    fastem_calibrations = False

import odemis.acq.stream as acqstream
from odemis import model, util
from odemis.acq import fastem_conf, stitching
from odemis.acq.align.fastem import align, estimate_calibration_time
from odemis.acq.stitching import REGISTER_IDENTITY, FocusingMethod
from odemis.acq.stream import SEMStream
from odemis.util import TimeoutError, transform
from odemis.util.driver import guessActuatorMoveDuration
from odemis.util.registration import estimate_grid_orientation_from_img
from odemis.util.transform import SimilarityTransform, to_physical_space

# The executor is a single object, independent of how many times the module (fastem.py) is loaded.
_executor = model.CancellableThreadPoolExecutor(max_workers=1)

# TODO: Normally we do not use component names in code, only roles. Store in the roles in the SETTINGS_SELECTION,
#  and at init lookup the role -> name conversion (using model.getComponent(role=role)).
# Selection of components, VAs and values to save with the ROA acquisition, structured: {component: {VA: value}}
SETTINGS_SELECTION = {
    'Beam Shift Controller': ['shift', ],
    'Detector Rotator': ['position',
                         'referenced',
                         'speed', ],
    'Mirror Descanner': ['clockPeriod',
                         'physicalFlybackTime',
                         'rotation',
                         'scanAmplitude',
                         'scanOffset', ],
    'MultiBeam Scanner': ['clockPeriod',
                          'dwellTime',
                          'rotation',
                          'scanAmplitude',
                          'scanDelay',
                          'scanOffset', ],
    'MultiBeam Scanner XT': ['accelVoltage',
                             'beamShiftTransformationMatrix',
                             'multiprobeRotation',
                             'patternStigmator',
                             'power',
                             'rotation', ],
    'Sample Stage': ['position',
                     'referenced',
                     'speed']
}


class FastEMCalibration(object):
    """
    A class containing FastEM calibration related attributes.
    """

    def __init__(self, name: str) -> None:
        """
        :param name: (str) Name of the calibration.
        """
        self.name = model.StringVA(name)
        self.region = None  # FastEMROC
        self.is_done = model.BooleanVA(False)  # states if the calibration was done successfully or not
        self.sequence = model.ListVA([])  # list of calibrations that need to be run sequencially
        self.button = None
        self.shape = None  # FastEMROCOverlay
        self.is_done.subscribe(self._on_done)

    def _on_done(self, done):
        if not done and self.region is not None:
            self.region.parameters.clear()  # Clear the calibrated parameters if calibration is not done


class FastEMROC(object):
    """
    Representation of a FastEM ROC (region of calibration).
    The region of calibration is a single field image acquired with the acquisition server component and typically
    acquired at a region with no sample section on the scintillator. The calibration image serves for the dark
    offset and digital gain calibration for the megafield acquisition. Typically, one calibration region per
    scintillator is acquired and assigned with all ROAs on the respective scintillator.
    """

    def __init__(self, name, coordinates=acqstream.UNDEFINED_ROI, colour="#FFA300"):
        """
        :param name: (str) Name of the region of calibration (ROC).
        :param coordinates: (float, float, float, float) left, top, right, bottom, Bounding box coordinates of the
                            ROC in [m]. The coordinates are in the sample carrier coordinate system, which
                            corresponds to the component with role='stage'.
        :param colour: The colour of the region of calibration (ROC).
        """
        self.name = model.StringVA(name)
        self.coordinates = model.TupleContinuous(coordinates,
                                                 range=((-1, -1, -1, -1), (1, 1, 1, 1)),
                                                 cls=(int, float),
                                                 unit='m')
        self.parameters = {}  # dictionary used for storing the values of the calibrated parameters
        self.colour = colour


def estimate_acquisition_time(roa, pre_calibrations=None, acq_dwell_time: Optional[float] = None):
    """
    Computes the approximate time it will take to run the ROA (megafield) acquisition including pre-calibrations
    if specified.

    :param roa: (FastEMROA) The acquisition region object to be acquired (megafield).
    :param pre_calibrations: (list[Calibrations]) List of calibrations that should be run before the ROA acquisition.
                             Default is None.
    :param acq_dwell_time: (float or None) The acquisition dwell time.

    :return (0 <= float): The estimated time for the ROA (megafield) acquisition in s including pre-calibrations.
    """
    tot_time = roa.estimate_acquisition_time(acq_dwell_time)
    if pre_calibrations:
        tot_time += estimate_calibration_time(pre_calibrations)

    return tot_time


def acquire(roa, path, username, scanner, multibeam, descanner, detector, stage, scan_stage, ccd, beamshift, lens,
            se_detector, ebeam_focus, pre_calibrations=None, save_full_cells=False, settings_obs=None,
            spot_grid_thresh=0.5, blank_beam=True, acq_dwell_time: Optional[float] = None):
    """
    Start a megafield acquisition task for a given region of acquisition (ROA).

    :param roa: (FastEMROA) The acquisition region object to be acquired (megafield).
    :param path: (str) Path on the external storage where the image data is stored. Here, it is possible
                to specify sub-directories (such as acquisition date and project name) additional to the main
                path as specified in the component.
                The ASM will create the directory on the external storage, including the parent directories,
                if they do not exist.
    :param username: (str) The current user's name.
    :param scanner: (xt_client.Scanner) Scanner component connecting to the XT adapter.
    :param multibeam: (technolution.EBeamScanner) The multibeam scanner component of the acquisition server module.
    :param descanner: (technolution.MirrorDescanner) The mirror descanner component of the acquisition server module.
    :param detector: (technolution.MPPC) The detector object to be used for collecting the image data.
    :param stage: (actuator.ConvertStage) The stage in the sample carrier coordinate system. The x and y axes are
        aligned with the x and y axes of the ebeam scanner.
    :param scan_stage: (actuator.ConvertStage) The stage in the corrected scan coordinate system, the x and y axes are
            aligned with the x and y axes of the multiprobe and the multibeam scanner.
    :param ccd: (model.DigitalCamera) A camera object of the diagnostic camera.
    :param beamshift: (tfsbc.BeamShiftController) Component that controls the beamshift deflection.
    :param lens: (static.OpticalLens) Optical lens component.
    :param se_detector: (model.Detector) single beam secondary electron detector.
    :param ebeam_focus: (model.Actuator) SEM focus control.
    :param pre_calibrations: (list[Calibrations]) List of calibrations that should be run before the ROA acquisition.
                             Default is None.
    :param save_full_cells: (bool) If True save the full cell images instead of cropping them
                       to the effective cell size.
    :param settings_obs: (SettingsObserver) VAs of all components of which some will be
                         integrated in the acquired ROA as metadata. Default is None,
                         if None the metadata will not be updated.
    :param spot_grid_thresh: (0<float<=1) Relative threshold on the minimum intensity of spots in the
        diagnostic camera image, calculated as `max(image) * spot_grid_thresh`.
    :param blank_beam: (bool) If true the beam will be blanked during stage moves, if false the beam remains
        un-blanked during stage moves.
    :param acq_dwell_time: (float or None) The acquisition dwell time.
    :return: (ProgressiveFuture) Acquisition future object, which can be cancelled. The result of the future is
             a tuple that contains:
                (model.DataArray): The acquisition data, which depends on the value of the detector.dataContent VA.
                (Exception or None): Exception raised during the acquisition or None.

    """

    est_dur = estimate_acquisition_time(roa, pre_calibrations, acq_dwell_time)
    f = model.ProgressiveFuture(start=time.time(), end=time.time() + est_dur)

    # TODO: pass path through attribute on ROA instead of argument?
    # Create a task that acquires the megafield image.
    task = AcquisitionTask(scanner, multibeam, descanner, detector, stage, scan_stage, ccd, beamshift, lens,
                           se_detector, ebeam_focus, roa, path, username, pre_calibrations, save_full_cells,
                           settings_obs, spot_grid_thresh, blank_beam, f)

    f.task_canceller = task.cancel  # lets the future know how to cancel the task.

    # Connect the future to the task and run it in a thread.
    # task.run is executed by the executor and runs as soon as no other task is executed
    _executor.submitf(f, task.run)

    return f


class AcquisitionTask(object):
    """
    The acquisition task for a single region of acquisition (ROA, megafield).
    An ROA consists of multiple single field images.
    """

    def __init__(self, scanner, multibeam, descanner, detector, stage, scan_stage, ccd, beamshift, lens, se_detector,
                 ebeam_focus, roa, path, username, pre_calibrations, save_full_cells, settings_obs, spot_grid_thresh,
                 blank_beam, future):
        """
        :param scanner: (xt_client.Scanner) Scanner component connecting to the XT adapter.
        :param multibeam: (technolution.EBeamScanner) The multibeam scanner component of the acquisition server module.
        :param descanner: (technolution.MirrorDescanner) The mirror descanner component of the acquisition server
        module.
        :param detector: (technolution.MPPC) The detector object to be used for collecting the image data.
        :param stage: (actuator.ConvertStage) The stage in the sample carrier coordinate system. The x and y axes are
            aligned with the x and y axes of the ebeam scanner.
        :param scan_stage: (actuator.ConvertStage) The stage in the corrected scan coordinate system, the x and y axes
            are aligned with the x and y axes of the multiprobe and the multibeam scanner.
        :param ccd: (model.DigitalCamera) A camera object of the diagnostic camera.
        :param beamshift: (tfsbc.BeamShiftController) Component that controls the beamshift deflection.
        :param lens: (static.OpticalLens) Optical lens component.
        :param se_detector: (model.Detector) single beam secondary electron detector.
        :param ebeam_focus: (model.Actuator) SEM focus control.
        :param roa: (FastEMROA) The acquisition region object to be acquired (megafield).
        :param path: (str) Path on the external storage where the image data is stored. Here, it is possible
                    to specify sub-directories (such as acquisition date and project name) additional to the main
                    path as specified in the component.
                    The ASM will create the directory on the external storage, including the parent directories,
                    if they do not exist.
        :param username: (str) The current user's name.
        :param pre_calibrations: (list[Calibrations]) List of calibrations that should be run before the ROA
                                 acquisition.
        :param save_full_cells: (bool) If True save the full cell images instead of cropping them
                               to the effective cell size.
        :param settings_obs: (SettingsObserver) VAs of all components of which some will be
                             integrated in the acquired ROA as metadata. If None the metadata will not be updated.
        :param spot_grid_thresh: (0<float<=1) Relative threshold on the minimum intensity of spots in the
            diagnostic camera image, calculated as `max(image) * spot_grid_thresh`.
        :param blank_beam: (bool) If true the beam will be blanked during stage moves, if false the beam remains
            un-blanked during stage moves.
        :param future: (ProgressiveFuture) Acquisition future object, which can be cancelled. The result of the future
                        is a tuple that contains:
                            (model.DataArray): The acquisition data, which depends on the value of the
                                               detector.dataContent VA.
                            (Exception or None): Exception raised during the acquisition or None.
        """
        self._scanner = scanner
        self._multibeam = multibeam
        self._descanner = descanner
        self._detector = detector
        self._stage = stage
        self._stage_scan = scan_stage
        self._ccd = ccd
        self._beamshift = beamshift
        self._lens = lens
        self._se_detector = se_detector
        self._ebeam_focus = ebeam_focus
        self._roa = roa  # region of acquisition object
        self._roc2 = roa.roc_2.value  # object for region of calibration 2
        self._roc3 = roa.roc_3.value  # object for region of calibration 3
        self._path = path  # sub-directories on external storage
        self._username = username
        self._future = future
        self._pre_calibrations = pre_calibrations
        self._save_full_cells = save_full_cells
        self._pre_calibrations_future = None
        self._settings_obs = settings_obs
        self._spot_grid_thresh = spot_grid_thresh
        self._blank_beam = blank_beam
        self._total_roa_time = 0
        if isinstance(future, model.ProgressiveFuture):
            self._total_roa_time = future.end_time - future.start_time

        # save the initial multibeam resolution, because the resolution will get updated if save_full_cells is True
        self._old_res = self._multibeam.resolution.value

        beam_shift_path = fastem_util.create_image_dir("beam-shift-correction")
        # If there is a project name the path will be
        # [image-dir]/beam-shift-correction/[timestamp]/[project-name]/[roa-name]_[slice-idx]
        # if there is no project name it will be
        # [image-dir]/beam-shift-correction/[timestamp]/[roa-name]_[slice-idx]
        self.beam_shift_path = os.path.join(beam_shift_path,
                                            path if path else "",  # project-name or empty
                                            f"{self._roa.name.value}_{self._roa.slice_index.value}")
        os.makedirs(self.beam_shift_path, exist_ok=True)

        # Dictionary containing the single field images with index as key: e.g. {(0,1): DataArray}.
        self.megafield = {}
        self.field_idx = (0, 0)
        self._pos_first_tile = None  # only calculate the position of the first tile when `run` is called.

        # TODO the .dataContent might need to be set somewhere else in future when using a live stream for
        #  display of thumbnail images -> .dataContent = "thumbnail"
        # set size of returned data array
        # The full image data is directly stored via the asm on the external storage.
        self._detector.dataContent.value = "empty"  # dataArray of shape (0,0) is returned with some MD

        # list of field image indices that still need to be acquired {(0,0), (1,0), (0,1), ...}
        self._fields_remaining = set(self._roa.field_indices)  # Used for progress update.

        # keep track if future was cancelled or not
        self._cancelled = False

        # Threading event, which keeps track of when image data has been received from the detector.
        self._data_received = threading.Event()

    def run(self):
        """
        Runs the acquisition of one ROA (megafield).
        :returns:
            megafield: (list of DataArrays) A list of the raw image data. Each data array (entire field, thumbnail,
                or zero array) represents one single field image within the roa (megafield).
            exception: (Exception or None) Exception raised during the acquisition. If some single field image data has
                already been acquired, exceptions are not raised, but returned.
        :raise:
            Exception: If it failed before any single field images were acquired or if acquisition was cancelled.
        """
        eff_field_size = (int((1 - self._roa.overlap) * self._multibeam.resolution.value[0]),
                          int((1 - self._roa.overlap) * self._multibeam.resolution.value[1]))
        self._detector.updateMetadata({model.MD_FIELD_SIZE: eff_field_size})

        self._detector.updateMetadata({model.MD_SLICE_IDX: self._roa.slice_index.value})
        self._detector.updateMetadata({model.MD_USER: self._username})

        # No need to set the start time of the future: it's automatically done when setting its state to running.
        logging.info(
            "Starting acquisition of ROA %s, with expected duration of %f s, %s by %s fields and overlap %s.",
            self._roa.shape.name.value, self._total_roa_time, self._roa.field_indices[-1][0] + 1, self._roa.field_indices[-1][1] + 1,
            self._roa.overlap,
        )

        # Update the position of the first tile.
        self._pos_first_tile = self.get_pos_first_tile()

        if self._pre_calibrations:
            self.pre_calibrate(self._pre_calibrations)

        # set the sub-directories (<user>/<project-name>/<roa-name>)
        self._detector.filename.value = os.path.join(self._username, self._path, self._roa.name.value)

        # Move the stage to the first tile, to ensure the correct position is
        # stored in the megafield metadata yaml file.
        self.field_idx = (0, 0)
        self._scanner.blanker.value = True  # blank the beam during the move
        self.move_stage_to_next_tile()

        if self._settings_obs:
            self._create_acquisition_metadata()

        exception = None
        dataflow = self._detector.data

        try:
            logging.debug("Configure hardware for acquisition.")
            # configure the HW settings
            fastem_conf.configure_scanner(self._scanner, fastem_conf.MEGAFIELD_MODE)
            fastem_conf.configure_detector(self._detector, self._roc2, self._roc3)
            fastem_conf.configure_multibeam(self._multibeam)

            if self._save_full_cells:
                old_cell_translation = self._detector.cellTranslation.value
                old_cell_translation_md = self._detector.getMetadata().get(model.MD_CELL_TRANSLATION, None)

                # set the resolution to the complete resolution, typically 7200px
                self._multibeam.resolution.value = (
                    self._detector.shape[0] * self._detector.cellCompleteResolution.value[0],
                    self._detector.shape[1] * self._detector.cellCompleteResolution.value[1]
                )

                # set the cell translation to 0, because we do not want to do any cropping
                cell_translation = tuple(tuple((0, 0) for i in range(0, self._detector.shape[0]))
                                         for j in range(0, self._detector.shape[1]))
                self._detector.updateMetadata({model.MD_CELL_TRANSLATION: cell_translation})
                self._detector.cellTranslation.value = cell_translation

            dataflow.subscribe(self.image_received)

            # Acquire the single field images.
            self.acquire_roa(dataflow)

        except CancelledError:  # raised in acquire_roa()
            logging.debug("Acquisition was cancelled.")
            raise

        except Exception as ex:
            # Check if any field images have already been acquired; if not => just raise the exception.
            if len(self._fields_remaining) == len(self._roa.field_indices):
                raise
            # If image data was already acquired, just log a warning.
            logging.warning("Exception during roa acquisition (after some data has already been acquired).",
                            exc_info=True)
            exception = ex  # let the caller handle the exception

        finally:
            # Remove references to the megafield once the acquisition is finished/cancelled.
            self._fields_remaining.clear()

            # Blank the beam after the acquisition is done.
            self._scanner.blanker.value = True

            # Finish the megafield also if an exception was raised, in order to enable a new acquisition.
            logging.debug("Finish ROA acquisition.")
            dataflow.unsubscribe(self.image_received)
            if self._save_full_cells:
                # Restore all parameter values, to have the correct values for the next acquisition without
                # save_full_cells
                self._multibeam.resolution.value = self._old_res
                self._detector.cellTranslation.value = old_cell_translation
                self._detector.getMetadata()[model.MD_CELL_TRANSLATION] = old_cell_translation_md

        return self.megafield, exception

    def acquire_roa(self, dataflow):
        """
        Acquire the single field images that resemble the region of acquisition (ROA, megafield image).
        :param dataflow: (model.DataFlow) The dataflow on the detector.
        """
        beam_shift_indices = self._calculate_beam_shift_cor_indices()

        total_field_time = self._detector.frameDuration.value + 1.5  # there is about 1.5 seconds overhead per field
        # The first field is acquired twice, so the timeout must be at least twice the total field time.
        # Use 5 times the total field time to have a wide margin.
        timeout = 5 * total_field_time + 2
        beam_shift_failed = False
        # Acquire all single field images, which are automatically offloaded to the external storage.
        for field_idx in self._roa.field_indices:
            # Reset the event that waits for the image being received (puts flag to false).
            self._data_received.clear()
            self.field_idx = field_idx
            logging.debug("Acquiring field with index: %s", field_idx)

            self.move_stage_to_next_tile()  # move stage to next field image position
            if self._blank_beam or field_idx == self._roa.field_indices[0]:
                logging.debug("unblank the beam")
                self._scanner.blanker.value = False  # unblank the beam

            prev_beam_shift = self._beamshift.shift.value
            if field_idx in beam_shift_indices or beam_shift_failed:
                logging.debug(f"Will run beam shift correction for field index {field_idx}")
                try:
                    new_beam_shift = self.correct_beam_shift()
                    # The difference in x or y should not be larger than 2 micrometers
                    if any(map(lambda n, p: abs(n - p) > 2e-6, new_beam_shift, prev_beam_shift)):
                        raise ValueError(
                            f"Difference in beam shift is larger than 2 µm, therefore it most likely failed. "
                            f"Previous beam shift: {prev_beam_shift}, new beam shift: {new_beam_shift}"
                        )
                    beam_shift_failed = False
                except Exception:
                    logging.exception("Correcting the beam shift failed, check if the image quality is still good.")
                    # In case of failure save the ccd image
                    ccd_image = self._ccd.data.get(asap=False)
                    fastem_util.save_image(self.beam_shift_path, f"{self.field_idx}_after.tiff", ccd_image)
                    beam_shift_failed = True

            dataflow.next(field_idx)  # acquire the next field image.

            # Wait until single field image data has been received (image_received sets flag to True).
            if not self._data_received.wait(timeout):
                # TODO here we often timeout when actually just the offload queue is full
                #  need to handle offload queue error differently to just wait a bit instead of timing out
                #   -> check if finish megafield is called in finally when hitting here
                raise TimeoutError("Timeout while waiting for field image.")

            if self._blank_beam:
                logging.debug("blank the beam")
                self._scanner.blanker.value = True  # blank the beam after the acquisition
            self._fields_remaining.discard(field_idx)

            # In case the acquisition was cancelled by a client, before the future returned, raise cancellation error.
            # Note: The acquisition of the current single field image (tile) is still finished though.
            if self._cancelled:
                raise CancelledError()

        logging.debug("Successfully acquired all fields of ROA.")

    def pre_calibrate(self, pre_calibrations):
        """
        Run optical multiprobe autofocus and image translation pre-alignment before the ROA acquisition.
        The image translation pre-alignment adjusts the descanner.scanOffset VA such that the image of the
        multiprobe is roughly centered on the mppc detector. This function reads in the ASM configuration
        and makes sure all values, except the descanner offset, are set back after the calibrations are run.
        The calibration is run at the stage position as indicated on the .field_indices attribute.

        NOTE: Canceling is currently not supported.

        :param pre_calibrations: (list[Calibrations]) List of calibrations that should be run before the ROA
                                 acquisition.
        """
        if not fastem_calibrations:
            raise ModuleNotFoundError("Need fastem_calibrations repository to run pre-calibrations.")

        # The pre-calibrations should run on a position that lies a full field
        # outside the ROA, therefore temporarily set the overlap to zero.
        overlap_init = self._roa.overlap
        self._roa.overlap = 0
        # Move the stage such that the pre-calibrations are done to the left of the top left field,
        # outside the region of acquisition to limit beam damage.
        fi = numpy.array(self._roa.field_indices)
        # col, row => row 0 is the top of the ROA and the lowest column value is the most left field
        min_col = numpy.min(fi[fi[:, 1] == 0], axis=0)[0]

        logging.debug("Start pre-calibration.")
        try:
            for i in range(3):  # try running the pre-calibrations 3 times
                # Move 1/10th of a field to the top right
                self.field_idx = (min_col - 1 + 0.1 * i, - i * 0.1)
                pos_hor, pos_vert = self.get_abs_stage_movement()  # get the absolute position for the new tile
                logging.debug(f"Moving to stage position x: {pos_hor}, y: {pos_vert}")
                self._stage_scan.moveAbsSync({'x': pos_hor, 'y': pos_vert})
                logging.debug(f"Will run pre-calibrations at field index {self.field_idx}")
                try:
                    self._pre_calibrations_future = align(self._scanner, self._multibeam,
                                                          self._descanner, self._detector,
                                                          self._stage, self._ccd,
                                                          self._beamshift, None,  # no need for the detector rotator
                                                          self._se_detector, self._ebeam_focus,
                                                          calibrations=pre_calibrations)
                    self._pre_calibrations_future.result()  # wait for the calibrations to be finished
                    break  # if it successfully ran, do not try again
                except CancelledError:
                    logging.debug("Cancelled acquisition pre-calibrations.")
                    raise
                except Exception as err:
                    if i == 2:
                        raise ValueError(f"Pre-calibrations failed 3 times, with error {err}")
                    else:
                        logging.warning(f"Pre-calibration failed for ROA {self._roa.shape.name.value} with error {err}, "
                                        f"will try again.")
        finally:
            self._roa.overlap = overlap_init  # set back the overlap to the initial value

        logging.debug("Finish pre-calibration.")

    def image_received(self, dataflow, data):
        """
        Function called by dataflow when data has been received from the detector.
        :param dataflow: (model.DataFlow) The dataflow on the detector.
        :param data: (model.DataArray) The data array containing the image data.
        """
        self.megafield[self.field_idx] = data
        # When data is received notify the threading event, which keeps track of whether data was received.
        self._data_received.set()

    def cancel(self, future):
        """
        Cancels the ROA acquisition.
        :param future: (future) The ROA (megafield) future.
        :return: (bool) True if cancelled, False if too late to cancel as future is already finished.
        """
        self._cancelled = True
        # Also cancel the pre-calibrations if they have not finished executing
        if self._pre_calibrations_future and not self._pre_calibrations_future.done():
            self._pre_calibrations_future.cancel()
            self._pre_calibrations_future = None

        # Report if it's too late for cancellation (and the f.result() will return)
        if not self._fields_remaining:
            return False

        return True

    def get_pos_first_tile(self):
        """
        Get the stage position of the first tile
        """
        px_size = self._multibeam.pixelSize.value
        field_res = self._multibeam.resolution.value

        # Get the coordinate of the top left corner of the ROA, this corresponds to the (xmin, ymax) coordinate in the
        # role='stage' coordinate system.
        points = self._roa.shape.points.value.copy()
        xmin_roa, _, _, ymax_roa = util.get_polygon_bbox(points)

        # Transform from stage to scan-stage coordinate system
        rot_cor = self._stage_scan.getMetadata()[model.MD_ROTATION_COR]
        t = transform.RigidTransform(rotation=-rot_cor)
        coords = t.apply([xmin_roa, ymax_roa])

        # The position of the stage when acquiring the top/left tile needs to be matching the center of that tile.
        # The stage coordinate system is pointing to the right in the x direction, and upwards in the y direction,
        # therefore add half a field in the x-direction and subtract half a field in the y-direction.
        pos_first_tile = (coords[0] + field_res[0] / 2 * px_size[0],
                          coords[1] - field_res[1] / 2 * px_size[1])

        return pos_first_tile

    def get_abs_stage_movement(self):
        """
        Based on the field index calculate the stage position where the next tile (field image) should be acquired.
        The position is always calculated with respect to the first (top/left) tile (field image). The stage position
        returned is the center of the respective tile.
        :return: (float, float) The new absolute stage x and y position in meter.
        """
        px_size = self._multibeam.pixelSize.value
        # When saving the full cells, the stage should still move based on the cropped cells.
        field_res = self._multibeam.resolution.value if not self._save_full_cells else self._old_res

        rel_move_hor = self.field_idx[0] * px_size[0] * field_res[0] * (1 - self._roa.overlap)  # in meter
        rel_move_vert = self.field_idx[1] * px_size[1] * field_res[1] * (1 - self._roa.overlap)  # in meter

        # Acceleration unknown, guessActuatorMoveDuration uses a default acceleration
        estimated_time_x = guessActuatorMoveDuration(self._stage_scan, "x", abs(rel_move_hor))  # s
        estimated_time_y = guessActuatorMoveDuration(self._stage_scan, "y", abs(rel_move_hor))  # s
        logging.debug(f"Estimated time for stage movement: {estimated_time_x + estimated_time_y} s")

        # With role="stage", move positive in x direction, because the second field should be right of the first,
        # and move negative in y direction, because the second field should be bottom of the first.
        pos_hor = self._pos_first_tile[0] + rel_move_hor
        pos_vert = self._pos_first_tile[1] - rel_move_vert

        return pos_hor, pos_vert

    def move_stage_to_next_tile(self):
        """Move the stage to the next tile (field image) position."""
        pos_hor, pos_vert = self.get_abs_stage_movement()  # get the absolute position for the new tile

        logging.debug(f"Moving to scan-stage position x: {pos_hor}, y: {pos_vert}")
        t = time.time()
        self._stage_scan.moveAbsSync({'x': pos_hor, 'y': pos_vert})  # move the stage
        logging.debug(f"Actual time for stage movement: {time.time() - t} s")
        stage_pos = self._stage_scan.position.value
        diff_x = stage_pos["x"] - pos_hor
        diff_y = stage_pos["y"] - pos_vert
        logging.debug(f"Moved to scan-stage position {stage_pos}, "
                      f"difference in xy between actual and target stage position: {diff_x}, {diff_y} m")

    def correct_beam_shift(self):
        """
        The stage creates a parasitic magnetic field. This causes the beams to shift slightly when the stage is moved,
        and thus the beams shift in between single field acquisitions. Therefore, the single fields cannot be
        seamlessly concatenated.

        To correct for this we measure the average (center) position of the spots before acquiring the single field.
        We compare this with the good multiprobe position, this is the factory calibrated position where we know the
        beams are roughly centered on the mppc detector. Using the difference between the current beam positions and the
        good beam positions we calculate in what direction and how much to shift beams, such that they are always
        centered on the mppc detector.

        :return: (float, float) the value of the beam shift after correction
        """
        pixel_size = self._ccd.pixelSize.value
        magnification = self._lens.magnification.value
        sigma = self._ccd.pointSpreadFunctionSize.value
        # asap=False: wait until new image is acquired (don't read from buffer)
        ccd_image = self._ccd.data.get(asap=False)
        tform, error = estimate_grid_orientation_from_img(ccd_image, (8, 8), SimilarityTransform, sigma,
                                                          threshold_rel=self._spot_grid_thresh)
        logging.debug(f"Found center of grid at {tform.translation}, error: {error}.")

        # Determine the shift of the spots, by subtracting the good multiprobe position from the average (center)
        # spot position.
        fav_pos_active = self._ccd.getMetadata()[model.MD_FAV_POS_ACTIVE]
        # FIXME: If i and j are not in the metadata, use x and y instead.
        #  Old yaml files used x and y, support for x and y can be removed when all yaml files are updated.
        try:
            i = fav_pos_active["i"]
        except KeyError:
            i = fav_pos_active["x"]
        try:
            j = fav_pos_active["j"]
        except KeyError:
            j = ccd_image.shape[1] - fav_pos_active["y"]

        good_mp_position = numpy.array([j, i])
        shift = good_mp_position - tform.translation  # [px]
        shift_m = to_physical_space(shift, pixel_size=pixel_size)  # [m]

        # FIXME A positive xy-shift of the beamshift component moves the pattern to the left bottom
        #  on the diagnostic camera. Therefore we need to invert the shift.
        #  When the beamshift component is fixed, this should be removed.
        shift_m *= -1
        beam_shift_cor = shift_m / magnification  # [m]
        # Convert the shift from pixels to meters
        logging.debug("Beam shift adjustment required: {} [m]".format(beam_shift_cor))

        cur_beam_shift_pos = numpy.array(self._beamshift.shift.value)
        logging.debug("Current beam shift: {} [m]".format(self._beamshift.shift.value))
        self._beamshift.shift.value = (cur_beam_shift_pos + beam_shift_cor)

        logging.debug("New beam shift m: {}".format(self._beamshift.shift.value))
        return self._beamshift.shift.value

    def _create_acquisition_metadata(self):
        """
        Get the acquisition metadata based on the SETTINGS_SELECTION.

        :return: (dict)
            Nested dictionary containing the current components, VAs and values: {component: {VA: value}}
        """
        settings = self._settings_obs.get_all_settings()

        selected_settings = {}
        for comp, vas in SETTINGS_SELECTION.items():
            if comp in settings:
                selected_settings[comp] = {va: settings[comp][va] for va in vas if va in settings[comp]}
            else:
                logging.info(f"Cannot find component {comp} in the settings observer, "
                             f"VAs from this component will not be stored")

        self._detector.updateMetadata({model.MD_EXTRA_SETTINGS: json.dumps(selected_settings)})
        return selected_settings

    def _calculate_beam_shift_cor_indices(self, n_beam_shifts=10):
        """
        Calculate for which indices to run the beam shift correction. The beam shift correction should run every
        n sections, starting at the first field in a row. For polygonal sections there can be gaps in the indices,
        the beam shift correction should then be run on the next possible section.
        Example: If the beam shift correction should run every 3 sections for the indices [(1, 0), (2, 0), (8, 0)],
        it should run for indices (1, 0) and (8, 0).

        :param n_beam_shifts: (int) Number of sections after which the beam shift correction should run.
        """
        # Sort indices by row first and then by column to enable row-wise processing
        field_indices = sorted(self._roa.field_indices, key=lambda x: (x[1], x[0]))

        beam_shift_indices = []
        current_row = -1  # Initialize with -1 to handle the first row (row 0) properly
        for idx in field_indices:
            col, row = idx  # field indices are saved (col, row)
            if row > current_row:
                # Always apply beam shift correction at the start of a new row
                beam_shift_indices.append(idx)
                current_row = row
            elif col >= beam_shift_indices[-1][0] + n_beam_shifts:
                # Apply beam shift correction after every n_beam_shifts for the rest of the row
                beam_shift_indices.append(idx)
        return beam_shift_indices


########################################################################################################################
# Overview image acquisition

# Fixed settings
# FIXME: in theory the precision of the stage should be better, update this value when the stage precision has improved
STAGE_PRECISION = 29e-6  # m, acceptable precision of the stage position, is used to determine the overview overlap


def acquireTiledArea(stream, stage, area, live_stream=None):
    """
    Start an overview acquisition task for a given area.

    :param stream: (SEMStream) The stream used for the acquisition.
        It must have the detector and emitter connected to the TFS XT client detector and scanner.
        It should be in focus.
        It must NOT have the following local VAs: horizontalFoV, resolution, scale
        (because the VAs of the hardware will be changed directly, and so they should not be changed by the stream).
    :param stage: (actuator.MultiplexActuator) The stage in the sample carrier coordinate system.
        The x and y axes are aligned with the x and y axes of the ebeam scanner. Axes should already be referenced.
    :param area: (float, float, float, float) xmin, ymin, xmax, ymax coordinates of the overview region
        in the sample carrier coordinate system.
    :param live_stream: (StaticStream or None): StaticStream to be updated with each tile acquired,
        to build up live the whole acquisition. NOT SUPPORTED YET.

    :return: (ProgressiveFuture) Acquisition future object, NOT SUPPORTED YET which can be cancelled.
             It returns the complete DataArray.
    """
    # Check the parameters
    if len(area) != 4:
        raise ValueError("area should be 4 float, but got %r" % (area,))

    for vaname in ("horizontalFoV", "resolution", "scale"):
        if vaname in stream.emt_vas:
            raise ValueError("Stream shouldn't have its own VA %s" % (vaname,))

    if set(stage.axes) < {"x", "y"}:
        raise ValueError("Stage needs axes x and y, but has %s" % (stage.axes.keys(),))
    if model.hasVA(stage, "referenced"):
        refd = stage.referenced.value
        for a in ("x", "y"):
            if a in refd:
                if not refd[a]:
                    raise ValueError("Stage axis '%s' is not referenced. Reference it first" % (a,))
            else:
                logging.warning("Going to use the stage in absolute mode, but it doesn't report %s in .referenced VA",
                                a)

    else:
        logging.warning("Going to use the stage in absolute mode, but it doesn't have .referenced VA")

    if live_stream:
        raise NotImplementedError("live_stream not supported")

    # Make a SEMStream copy of the stream, because it is a FastEMSEMStream object, which in its prepare method
    # overwrites the scanner configuration from overview mode to liveview mode.
    sem_stream = SEMStream(stream.name.value + " copy", stream.detector, stream.detector.data, stream.emitter)

    est_dur = estimateTiledAcquisitionTime(sem_stream, stage, area)
    f = model.ProgressiveFuture(start=time.time(), end=time.time() + est_dur)

    # Connect the future to the task and run it in a thread.
    # OverviewAcquisition.run is executed by the executor and runs as soon as no other task is executed
    overview_acq = OverviewAcquisition(future=f)
    _executor.submitf(f, overview_acq.run, sem_stream, stage, area, live_stream)

    return f


def estimateTiledAcquisitionTime(stream, stage, area, dwell_time=None):
    """
    Estimate the time needed to acquire a full overview image. Calculate the
    number of tiles needed for the requested area based on set dwell time and
    resolution (number of pixels).

    Note: "estimateTiledAcquisitionTime()" of the "_tiledacq.py" module cannot be used for a couple of reasons:
    Firstly, the e-beam settings are not the current ones, but the one from the fastem_conf.
    Secondly, the xt_client acquisition time is quite a bit longer than the settings suggests.
    Therefore, we have an ad-hoc method here.

    :param stream: (SEMstream) The stream used for the acquisition.
    :param stage: (actuator.MultiplexActuator) The stage in the sample carrier coordinate system.
        The x and y axes are aligned with the x and y axes of the ebeam scanner. UNUSED: Can be used to take
        speed of axes into account for time estimation.
    :param area: (float, float, float, float) xmin, ymin, xmax, ymax coordinates of the overview region
        in the sample carrier coordinate system.
    :param dwell_time: (float) A user input dwell time to be used instead of the stream emitter's dwell time
        for acquisition time calculation.

    :return: The estimated total acquisition time for the overview image in seconds.
    """
    # get the resolution per tile used during overview imaging
    res = fastem_conf.SCANNER_CONFIG[fastem_conf.OVERVIEW_MODE]['resolution']
    fov = fastem_conf.SCANNER_CONFIG[fastem_conf.OVERVIEW_MODE]['horizontalFoV']

    # calculate area size
    fov = (fov, fov * res[1] / res[0])
    acquisition_area = util.normalize_rect(area)  # make sure order is l, b, r, t
    area_size = (acquisition_area[2] - acquisition_area[0],
                 acquisition_area[3] - acquisition_area[1])
    # number of tiles
    nx = math.ceil(abs(area_size[0] / fov[0]))
    ny = math.ceil(abs(area_size[1] / fov[1]))

    # add some overhead per tile for e.g. stage movement
    overhead = 1  # [s]
    # time per tile
    dwell_time_value = dwell_time if dwell_time is not None else stream.emitter.dwellTime.value
    acq_time_tile = res[0] * res[1] * dwell_time_value + overhead  # [s]
    # time for total overview image
    acq_time_overview = nx * ny * acq_time_tile  # [s]

    return acq_time_overview


class OverviewAcquisition(object):
    """Class to run the acquisition of one overview image (typically one scintillator)."""

    def __init__(self, future: model.ProgressiveFuture) -> None:
        self._sub_future = model.ProgressiveFuture()
        self._future = future
        self._future.task_canceller = self._cancel_acquisition

    def _cancel_acquisition(self, future) -> bool:
        self._sub_future.cancel()
        return True

    def run(self, stream, stage, area, live_stream):
        """
        Runs the acquisition of one overview image (typically one scintillator).

        :param stream: (SEMstream) The stream used for the acquisition.
        :param stage: (actuator.MultiplexActuator) The stage in the sample carrier coordinate system.
            The x and y axes are aligned with the x and y axes of the ebeam scanner.
        :param area: (float, float, float, float) xmin, ymin, xmax, ymax coordinates of the overview region.
        :param live_stream: (StaticStream or None): StaticStream to be updated with each tile acquired,
               to build up live the whole acquisition. NOT SUPPORTED YET.

        :returns: (DataArray) The complete overview image.
        """
        logging.debug("Referencing stage axes x and y.")
        f = stage.reference({"x", "y"})
        f.result(timeout=180)

        # Get the current immersion mode value before configuring the scanner.
        # This value is set back after acquireTiledArea future's result.
        current_immersion_mode = stream.emitter.immersion.value

        fastem_conf.configure_scanner(stream.emitter, fastem_conf.OVERVIEW_MODE)

        # The stage movement precision is quite good (just a few pixels). The stage's
        # position reading is much better, and we can assume it's below a pixel.
        # So as long as we are sure there is some overlap, the tiles will be positioned
        # correctly and without gap.
        overlap = STAGE_PRECISION / stream.emitter.horizontalFoV.value
        logging.debug("Overlap is %s%%", overlap * 100)  # normally < 1%

        def _pass_future_progress(sub_f, start, end):
            self._future.set_progress(start, end)

        # Note, for debugging, it's possible to keep the intermediary tiles with log_path="./tile.ome.tiff"
        self._sub_future = stitching.acquireTiledArea([stream], stage, area, overlap, registrar=REGISTER_IDENTITY,
                                                      focusing_method=FocusingMethod.NONE)

        # Connect the progress of the underlying future to the main future
        # FIXME removed to provide better progress update in GUI
        # When _tiledacq.py has proper time update implemented, add this line here again.
        # self._sub_future.add_update_callback(_pass_future_progress)

        das = []
        try:
            das = self._sub_future.result()
        finally:
            # Set the immersion mode back to the current value which was stored before configuring the scanner.
            stream.emitter.immersion.value = current_immersion_mode

            # FIXME auto blanking not working properly, so force beam blanking after image acquisition for now.
            stream.emitter.blanker.value = True

        if len(das) == 1:
            return das[0]
        else:
            logging.warning("Expected 1 DataArray, but got %d: %r", len(das), das)
            return das[:1]
