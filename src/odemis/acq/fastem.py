#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 19 Apr 2021

@author: Philip Winkler, Éric Piel, Thera Pals, Sabrina Rossberger

Copyright © 2021 Philip Winkler, Delmic

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

from __future__ import division

import logging
import math
import os
import threading
import time
from concurrent.futures import CancelledError

import numpy

from odemis import model, util
from odemis.acq import stitching
from odemis.acq.stitching import REGISTER_IDENTITY
from odemis.acq.stream import SEMStream
from odemis.util import TimeoutError


SINGLE_BEAM_ROTATION_DEFAULT = 0  # 0 is typically a good guess (better than multibeam rotation of ~0.0157 rad)
MULTI_BEAM_ROTATION_DEFAULT = 0

# The executor is a single object, independent of how many times the module (fastem.py) is loaded.
_executor = model.CancellableThreadPoolExecutor(max_workers=1)


class FastEMROA(object):
    """
    Representation of a FastEM ROA (region of acquisition).
    The region of acquisition is a megafield image, which consists of a sequence of single field images. Each single
    field image itself consists of cell images. The number of cell images is defined by the shape of the multiprobe
    and detector.
    """

    def __init__(self, name, coordinates, roc, asm, multibeam, descanner, detector):
        """
        :param name: (str) Name of the region of acquisition (ROA). It is the name of the megafield (id) as stored on
                     the external storage.
        :param coordinates: (float, float, float, float) left, top, right, bottom, Bounding box
                            coordinates of the ROA in [m]. The coordinates are in the sample carrier coordinate
                            system, which corresponds to the component with role='stage'.
        :param roc: (FastEMROC) Corresponding region of calibration (ROC).
        :param asm: (technolution.AcquisitionServer) The acquisition server module component.
        :param multibeam: (technolution.EBeamScanner) The multibeam scanner component of the acquisition server module.
        :param descanner: (technolution.MirrorDescanner) The mirror descanner component of the acquisition server module.
        :param detector: (technolution.MPPC) The detector object to be used for collecting the image data.
        """
        self.name = model.StringVA(name)
        self.coordinates = model.TupleContinuous(coordinates,
                                                 range=((-1, -1, -1, -1), (1, 1, 1, 1)),
                                                 cls=(int, float),
                                                 unit='m')
        self.roc = model.VigilantAttribute(roc)
        self._asm = asm
        self._multibeam = multibeam
        self._descanner = descanner
        self._detector = detector

        # Calculate how many field images are needed to cover the full ROA.
        self.field_indices = self.calculate_field_indices()
        self.coordinates.subscribe(self.on_coordinates)

        # TODO need to check if megafield already exists, otherwise overwritten, subscribe whenever name is changed,
        #  it should be checked

    def on_coordinates(self, coordinates):
        """Recalculate the field indices when the coordinates of the region of acquisition (ROA) have changed
        (e.g. resize, moving).
        :param coordinates: (float, float, float, float) left, top, right, bottom, Bounding box coordinates of the
                            ROA in [m]. The coordinates are in the sample carrier coordinate system, which
                            corresponds to the component with role='stage'.
        """
        self.field_indices = self.calculate_field_indices()

    def estimate_roa_time(self):
        """
        Computes the approximate time it will take to run the ROA (megafield) acquisition.
        :return (0 <= float): The estimated time for the ROA (megafield) acquisition in s.
        """
        field_time = self._detector.frameDuration.value
        tot_time = len(self.field_indices) * field_time

        return tot_time

    def calculate_field_indices(self):
        """
        Calculates the number of single field images needed to cover the ROA (region of acquisition). Determines the
        corresponding indices of the field images in a matrix covering the ROA. If the ROA cannot be resembled
        by an integer number of single field images, the number of single field images is increased to cover the
        full region. An ROA is a rectangle.

        :return: (list of nested tuples (col, row)) The column and row field indices of the field images in the order
                 they should be acquired. The tuples are re-ordered so that the single field images resembling the
                 ROA are acquired first rows then columns.
        """
        l, t, r, b = self.coordinates.value  # tuple of floats: l, t, r, b coordinates in m
        px_size = self._multibeam.pixelSize.value
        field_res = self._multibeam.resolution.value

        # The size of a field consists of the effective cell images excluding overscanned pixels.
        field_size = (field_res[0] * px_size[0], field_res[1] * px_size[1])
        # Note: floating point errors here, can result in an additional row or column of fields (that's fine)
        n_hor_fields = math.ceil(abs(r - l) / field_size[0])
        n_vert_fields = math.ceil(abs(b - t) / field_size[1])

        # Create the field indices based on the number of horizontal and vertical fields.
        field_indices = numpy.ndindex(n_vert_fields, n_hor_fields)

        # ndindex returns an iterator, the values need to be returned as a list.
        # The fields should be acquired per row, therefore when looping over the field indices the vertical fields
        # should initially stay constant. The indices are swapped, because the dataflow expects first the column
        # index, then the row index. ((0,0), (1,0), (2,0), ...., (0,2), (1,2), ...)
        field_indices = [f[::-1] for f in field_indices]

        return field_indices


# TODO add ROC acquisition to acquisition task

class FastEMROC(object):
    """
    Representation of a FastEM ROC (region of calibration).
    The region of calibration is a single field image acquired with the acquisition server component and typically
    acquired at a region with no sample section on the scintillator. The calibration image serves for the dark
    offset and digital gain calibration for the megafield acquisition. Typically, one calibration region per
    scintillator is acquired and assigned with all ROAs on the respective scintillator.
    """

    def __init__(self, name, coordinates):
        """
        :param name: (str) Name of the region of calibration (ROC). It is the name of the megafield (id) as stored on
                     the external storage.
        :param coordinates: (float, float, float, float) left, top, right, bottom, Bounding box coordinates of the
                            ROC in [m]. The coordinates are in the sample carrier coordinate system, which
                            corresponds to the component with role='stage'.
        """
        self.name = model.StringVA(name)
        self.coordinates = model.TupleContinuous(coordinates,
                                                 range=((-1, -1, -1, -1), (1, 1, 1, 1)),
                                                 cls=(int, float),
                                                 unit='m')
        self.parameters = None  # calibration object with all relevant parameters
        # TODO parameters are the darkOffset and digitalGain values for mppc VAs; set before starting acquisition
        # if None -> acquire, if not None, don't acquire again


def acquire(roa, path, multibeam, descanner, detector, stage):
    """
    Start a megafield acquisition task for a given region of acquisition (ROA).

    :param roa: (FastEMROA) The acquisition region object to be acquired (megafield).
    :param path: (str) Path on the external storage where the image data is stored. Here, it is possible
                to specify sub-directories (such as acquisition date and project name) additional to the main
                path as specified in the component.
                The ASM will create the directory on the external storage, including the parent directories,
                if they do not exist.
    :param multibeam: (technolution.EBeamScanner) The multibeam scanner component of the acquisition server module.
    :param descanner: (technolution.MirrorDescanner) The mirror descanner component of the acquisition server module.
    :param detector: (technolution.MPPC) The detector object to be used for collecting the image data.
    :param stage: (actuator.ConvertStage) The stage in the corrected scan coordinate system, the x and y axes are
        aligned with the x and y axes of the multiprobe and the multibeam scanner.

    :return: (ProgressiveFuture) Acquisition future object, which can be cancelled. The result of the future is
             a tuple that contains:
                (model.DataArray): The acquisition data, which depends on the value of the detector.dataContent VA.
                (Exception or None): Exception raised during the acquisition or None.
    """
    f = model.ProgressiveFuture()

    # TODO: pass path through attribute on ROA instead of argument?
    # Create a task that acquires the megafield image.
    task = AcquisitionTask(multibeam, descanner, detector, stage, roa, path, f)

    f.task_canceller = task.cancel  # lets the future know how to cancel the task.

    # Connect the future to the task and run it in a thread.
    # task.run is executed by the executor and runs as soon as no other task is executed
    _executor.submitf(f, task.run)

    return f


def _configure_multibeam_hw(scanner):
    md = scanner.getMetadata()
    if model.MD_MULTI_BEAM_ROTATION in md:
        scanner.rotation.value = md[model.MD_MULTI_BEAM_ROTATION]
    else:
        scanner.rotation.value = MULTI_BEAM_ROTATION_DEFAULT
        logging.warning("Scanner doesn't have MULTI_BEAM_ROTATION metadata, using %s rad.",
                        scanner.rotation.value)


class AcquisitionTask(object):
    """
    The acquisition task for a single region of acquisition (ROA, megafield).
    An ROA consists of multiple single field images.
    """

    def __init__(self, multibeam, descanner, detector, stage, roa, path, future):
        """
        :param multibeam: (technolution.EBeamScanner) The multibeam scanner component of the acquisition server module.
        :param descanner: (technolution.MirrorDescanner) The mirror descanner component of the acquisition server module.
        :param detector: (technolution.MPPC) The detector object to be used for collecting the image data.
        :param roa: (FastEMROA) The acquisition region object to be acquired (megafield).
        :param path: (str) Path on the external storage where the image data is stored. Here, it is possible
                    to specify sub-directories (such as acquisition date and project name) additional to the main
                    path as specified in the component.
                    The ASM will create the directory on the external storage, including the parent directories,
                    if they do not exist.
        :param future: (ProgressiveFuture) Acquisition future object, which can be cancelled. The result of the future
                        is a tuple that contains:
                            (model.DataArray): The acquisition data, which depends on the value of the
                                               detector.dataContent VA.
                            (Exception or None): Exception raised during the acquisition or None.
        """
        self._multibeam = multibeam
        self._descanner = descanner
        self._detector = detector
        self._stage = stage
        self._roa = roa  # region of acquisition object
        self._roc = roa.roc  # region of calibration object
        self._path = path  # sub-directories on external storage
        self._future = future

        # Dictionary containing the single field images with index as key: e.g. {(0,1): DataArray}.
        self.megafield = {}
        self.field_idx = (0, 0)

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

        # set the sub-directories (<acquisition date>/<project name>) and megafield id
        self._detector.filename.value = os.path.join(self._path, self._roa.name.value)

        exceptions = None

        # Get the estimated time for the roa.
        total_roa_time = self._roa.estimate_roa_time()
        # No need to set the start time of the future: it's automatically done when setting its state to running.
        self._future.set_progress(end=time.time() + total_roa_time)  # provide end time to future
        logging.info("Starting acquisition of mega field, with expected duration of %f s", total_roa_time)

        dataflow = self._detector.data

        try:
            logging.debug("Starting megafield acquisition.")
            dataflow.subscribe(self.image_received)

            # Acquire the single field images.
            self.acquire_roa(dataflow)

            # In case the acquisition was cancelled, before the future returned, raise cancellation error
            # after unsubscribing from the dataflow
            if self._cancelled:
                self._future.cancel()
                raise CancelledError()

        except CancelledError:
            logging.debug("Acquisition was cancelled.")
            raise

        except Exception as ex:
            # Check if any field images have already been acquired; if not => just raise the exception.
            if len(self._fields_remaining) == len(self._roa.field_indices):
                raise
            # If image data was already acquired, just log a warning.
            logging.warning("Exception during roa acquisition (after some data has already been acquired).",
                            exc_info=True)
            exceptions = ex  # let the caller handle the exception

        finally:
            # Remove references to the megafield once the acquisition is finished/cancelled.
            self._fields_remaining.clear()

            # Finish the megafield also if an exception was raised, in order to enable a new acquisition.
            logging.debug("Finish megafield acquisition.")
            dataflow.unsubscribe(self.image_received)

        return self.megafield, exceptions

    def acquire_roa(self, dataflow):
        """
        Acquire the single field images that resemble the region of acquisition (ROA, megafield image).
        :param dataflow: (model.DataFlow) The dataflow on the detector.
        :return: (list of DataArrays): A list of the raw image data. Each data array (entire field, thumbnail,
                                       or zero array) represents one single field image within the ROA (megafield).
        """

        total_field_time = self._detector.frameDuration.value
        timeout = total_field_time + 5  # TODO what margin should be used?

        # Acquire all single field images, which are automatically offloaded to the external storage.
        for field_idx in self._roa.field_indices:
            # Reset the event that waits for the image being received (puts flag to false).
            self._data_received.clear()
            self.field_idx = field_idx
            logging.debug("Acquiring field with index: %s", field_idx)

            self.move_stage_to_next_tile()  # move stage to next field image position

            dataflow.next(field_idx)  # acquire the next field image.

            # Wait until single field image data has been received (image_received sets flag to True).
            if not self._data_received.wait(timeout):
                # TODO here we often timeout when actually just the offload queue is full
                #  need to handle offload queue error differently to just wait a bit instead of timing out
                #   -> check if finish megafield is called in finally when hitting here
                raise TimeoutError("Timeout while waiting for field image.")

            self._fields_remaining.discard(field_idx)

            # Cancel the acquisition and return to the run function and handle the cancellation there.
            # Note: The acquisition of the current single field image (tile) is still finished though.
            if self._cancelled:
                return

            # Update the time left for the acquisition.
            expected_time = len(self._fields_remaining) * total_field_time
            self._future.set_progress(end=time.time() + expected_time)

        return self.megafield

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

        # Report if it's too late for cancellation (and the f.result() will return)
        if not self._fields_remaining:
            return False

        return True

    def get_abs_stage_movement(self):
        """
        Based on the field index calculate the stage position where the next tile (field image) should be acquired.
        The position is always calculated with respect to the top/left tile (field image). The stage position returned
        is the center of the respective tile.
        :return: (float, float) The new absolute stage x and y position in meter.
        """
        px_size = self._multibeam.pixelSize.value
        field_res = self._multibeam.resolution.value
        pos_orig = self._roa.coordinates.value[:2]  # position of top/left corner of the ROA

        # The position of the stage when acquiring the top/left tile needs to be matching the center of the tile.
        pos_first_tile = (pos_orig[0] - field_res[0]/2. * px_size[0], pos_orig[1] + field_res[1]/2. * px_size[1])

        rel_move_hor = self.field_idx[0] * px_size[0] * field_res[0]  # in meter
        rel_move_vert = self.field_idx[1] * px_size[1] * field_res[1]  # in meter

        # Move in the negative x direction, because the second field should be right of the first.
        # Move in positive y direction, because the second field should be bottom of the first.
        # TODO verify that this results in the images being taken in the correct order (left to right, top to bottom).
        pos_hor = pos_first_tile[0] - rel_move_hor
        pos_vert = pos_first_tile[1] + rel_move_vert

        return pos_hor, pos_vert

    def move_stage_to_next_tile(self):
        """Move the stage to the next tile (field image) position."""

        pos_hor, pos_vert = self.get_abs_stage_movement()  # get the absolute position for the new tile
        f = self._stage.moveAbs({'x': pos_hor, 'y': pos_vert})  # move the stage
        f.result()
        logging.debug("Moved to stage position %s" % (self._stage.position.value,))


########################################################################################################################
# Overview acquisition

# Fixed settings
# We use a "legacy" resolution in XT, because has the advantage of having a more
# square aspect ratio, compared to new resolutions like  1536 x 1024.
TILE_RES = (1024, 884)  # px
# Maximum FoV without seeing the pole-piece (with T1, immersion off).
# Possibly, using the ETD could allow a slightly wider FoV.
TILE_FOV_X = 1.5e-3  # m

STAGE_PRECISION = 1e-6  # m, how far the stage may go from the requested position

# Observed time it takes to acquire a tile with 1µs dwell time, for time estimation
TIME_PER_TILE_1US = 4.7  # s/tile


def acquireTiledArea(stream, stage, area, live_stream=None):
    """
    :param stream: (SEMStream) Stream used for the acquisition.
     It must have the detector and emitter connected to the TFS XT client detector and scanner.
     It should be in focus.
     It must NOT have the following local VAs: horizontalFoV. resolution, scale
      (because the VAs of the hardware will be changed directly, and so they shouldn’t be changed by the stream).
    :param stage: (Actuator). It should have axes "x" and "y", which should already be referenced.
    :param area: (float, float, float, float) minx, miny, maxx, maxy:  coordinates of the overview region
    :param live_stream: (StaticStream or None): StaticStream to be updated with
       each tile acquired, to build up live the whole acquisition. NOT SUPPORTED YET.
    : return: (ProgressiveFuture), acquisition future. It returns the complete DataArray.
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
                logging.warning("Going to use the stage in absolute mode, but it doesn't report %s in .referenced VA", a)

    else:
        logging.warning("Going to use the stage in absolute mode, but it doesn't have .referenced VA")

    if live_stream:
        raise NotImplementedError("live_stream not supported")

    # FIXME: if stream has .focuser, the acquireTiledArea will try to refocus from time to time
    # => temporarily remove it? Or duplicate the stream? Or pass an argument to
    # acquireTiledArea to disable the autofocusing.
    # FIXME: if the stream already has an image, it will be used to compute the FoV,
    # which might be completely wrong. => provide a .guessFoV() on the LiveStream
    # to guess the FoV on the next image.
    # FIXME: if the stream has .horizontalFoV, it's automatically set to the biggest
    # value, which do not work.

    # To avoid the focuser, and horizontalFoV, and the already present data, we
    # just create our own SEMStream
    sem_stream = SEMStream(stream.name.value + " copy", stream.detector, stream.detector.data, stream.emitter)

    est_dur = estimateTiledAcquisitionTime(sem_stream, stage, area)
    f = model.ProgressiveFuture(start=time.time(), end=time.time() + est_dur)
    _executor.submitf(f, _run_overview_acquisition, f, sem_stream, stage, area, live_stream)

    return f


def estimateTiledAcquisitionTime(stream, stage, area):
    # TODO: fix function to limit the acquisition area so that the FoV is taken into account.
    # t_estim = estimateTiledAcquisitionTime(stream, stage, area, overlap=0)

    # For now, it's just simpler to hard-code the time spent per tile, and derive the total time based on it.
    fov = (TILE_FOV_X, TILE_FOV_X * TILE_RES[1] / TILE_RES[0])
    normalized_area = util.normalize_rect(area)
    area_size = (normalized_area[2] - normalized_area[0],
                 normalized_area[3] - normalized_area[1])
    nx = math.ceil(abs(area_size[0] / fov[0]))
    ny = math.ceil(abs(area_size[1] / fov[1]))

    # TODO: compensate for longer dwell times => should be a A+Bx formula?
    return nx * ny * TIME_PER_TILE_1US  # s


def _configure_overview_hw(scanner):
    """
    Set good parameters an overview acquisition
    """
    # Typically, all these settings should have already been set, but better be safe.
    scanner.multiBeamMode.value = False
    scanner.external.value = False
    scanner.blanker.value = None  # Automatic
    # Disable immersion, to get a larger field of view.
    # It needs to be done before changing the horizontalFoV, as the range is updated
    scanner.immersion.value = False

    scanner.horizontalFoV.value = TILE_FOV_X  # m

    # => compute the scale needed in X, use the same one in Y, and then compute the Y resolution.
    # => set resolution to full FoV, and then adjust
    scale = scanner.shape[0] / TILE_RES[0]
    scanner.scale.value = (scale, scale)
    if scanner.resolution.value != TILE_RES:
        logging.warning("Unexpected resolution %s on e-beam scanner, expected %s",
                        scanner.resolution.value, TILE_RES)

    md = scanner.getMetadata()
    if model.MD_SINGLE_BEAM_ROTATION in md:
        scanner.rotation.value = md[model.MD_SINGLE_BEAM_ROTATION]
    else:
        scanner.rotation.value = SINGLE_BEAM_ROTATION_DEFAULT
        logging.warning("Scanner doesn't have SINGLE_BEAM_ROTATION metadata, using %s rad.",
                        scanner.rotation.value)
    md[model.MD_ROTATION_COR] = -scanner.rotation.value
    scanner.updateMetadata(md)


def _run_overview_acquisition(f, stream, stage, area, live_stream):
    """
    :returns: (DataArray)
    """
    _configure_overview_hw(stream.emitter)

    # The stage movement precision is quite good (just a few pixels). The stage's
    # position reading is much better, and we can assume it's below a pixel.
    # So as long as we are sure there is some overlap, the tiles will be positioned
    # correctly and without gap.
    overlap = STAGE_PRECISION / stream.emitter.horizontalFoV.value
    logging.debug("Overlap is %s%%", overlap * 100)  # normally < 1%

    def _pass_future_progress(sub_f, start, end):
        f.set_progress(start, end)

    # Note, for debugging, it's possible to keep the intermediary tiles with log_path="./tile.ome.tiff"
    sf = stitching.acquireTiledArea([stream], stage, area, overlap, registrar=REGISTER_IDENTITY)
    # Connect the progress of the underlying future to the main future
    sf.add_update_callback(_pass_future_progress)
    das = sf.result()

    if len(das) != 1:
        logging.warning("Expected 1 DataArray, but got %d: %r", len(das), das)
    return das[0]
