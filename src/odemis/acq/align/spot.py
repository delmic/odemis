# -*- coding: utf-8 -*-
"""
Created on 14 Apr 2014

@author: Kimon Tsitsikas

Copyright © 2013-2014 Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms  of the GNU General Public License version 2 as published by the Free
Software  Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY;  without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR  PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

from __future__ import division

from Pyro4.core import isasync
from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, \
    RUNNING
import logging
import math
from odemis import model
from odemis.acq._futures import executeTask
from odemis.dataio import hdf5
from scipy import ndimage
import threading
import numpy 
import time

import coordinates

from . import autofocus

ROUGH_MOVE = 1  # Number of max steps to reach the center in rough move
FINE_MOVE = 10  # Number of max steps to reach the center in fine move
FOV_MARGIN = 250  # pixels

_alignment_lock = threading.Lock()
_center_lock = threading.Lock()


def MeasureSNR(image):
    # Estimate noise
    bl = image.metadata[model.MD_BASELINE]
    m = ndimage.mean(image)-bl
    sdn = ndimage.standard_deviation(image[image < (bl* 1.1)])
    ms = ndimage.mean(image[image >= (bl * 1.1)])-bl
    snr = ms / sdn
    #logging.debug("Mean %f and standard deviation %f and sum %f, sdn %f, ms=%f", #ndimage.mean(image),
#    ndimage.standard_deviation(image), numpy.sum(image), sdn, ms)
    
    return snr

def _DoAlignSpot(future, ccd, stage, escan, focus):
    """
    Adjusts settings until we have a clear and well focused optical spot image, 
    detects the spot and manipulates the stage so as to move the spot center to 
    the optical image center. If no spot alignment is achieved an exception is
    raised.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    ccd (model.DigitalCamera): The CCD
    stage (model.CombinedActuator): The stage
    escan (model.Emitter): The e-beam scanner
    focus (model.CombinedActuator): The optical focus
    returns (float):    Final distance to the center #m 
    raises:    
            CancelledError() if cancelled
            ValueError
    """
    init_binning = ccd.binning.value
    init_et = ccd.exposureTime.value
    init_cres = ccd.resolution.value
    init_scale = escan.scale.value
    init_eres = escan.resolution.value

    logging.debug("Starting Spot alignment...")
    try:
        if future._spot_alignment_state == CANCELLED:
            raise CancelledError()

        # Configure CCD and set ebeam to spot mode
        logging.debug("Configure CCD and set ebeam to spot mode...")
        ccd.binning.value = (1, 1)
        ccd.resolution.value = ccd.resolution.range[1]
        ccd.exposureTime.value = 600e-03
        escan.scale.value = (1, 1)
        escan.resolution.value = (1, 1)

        # Estimate noise and adjust exposure time based on "Rose criterion"
        logging.debug("Adjust exposure time...")
        image = ccd.data.get(False)
        snr = MeasureSNR(image)
        while (snr < 5 and ccd.exposureTime.value < 800e-03):
            ccd.exposureTime.value = ccd.exposureTime.value + 100e-03
            # time.sleep(1)
            image = ccd.data.get(False)
            snr = MeasureSNR(image)
            print snr

        # Try to find spot
        logging.debug("Trying to find spot...")
        future._centerspotf = CenterSpot(ccd, stage, ROUGH_MOVE)
        dist = future._centerspotf.result()
        # If spot not found, autofocus and then retry
        if dist is None:
            logging.debug("Spot not found, try to autofocus...")
            try:
                ccd.binning.value=(8, 8)
                future._autofocusf = autofocus.AutoFocus(ccd, None, focus, autofocus.ROUGH_SPOTMODE_ACCURACY)
                lens_pos, fm_level = future._autofocusf.result()
                ccd.binning.value=(1, 1)
            except IOError:
                raise IOError('Spot alignment failure. AutoFocus failed.')
            future._centerspotf = CenterSpot(ccd, stage, ROUGH_MOVE)
            dist = future._centerspotf.result()
            if dist is None:
                raise IOError('Spot alignment failure. Spot not found')

        # Limitate FoV to save time
        logging.debug("Crop FoV...")
        CropFoV(ccd)

        # Autofocus
        logging.debug("Autofocusing...")
        try:
            ccd.binning.value=(8, 8)
            future._autofocusf = autofocus.AutoFocus(ccd, None, focus, autofocus.FINE_SPOTMODE_ACCURACY)
            lens_pos, fm_level = future._autofocusf.result()
            ccd.binning.value=(1, 1)
        except IOError:
            raise IOError('Spot alignment failure. AutoFocus failed.')
        if future._spot_alignment_state == CANCELLED:
            raise CancelledError()

        ccd.binning.value = (1, 1)
        image = ccd.data.get(False)
        # Make sure you have the correct resolution
        while image.shape != (ccd.resolution.value[1], ccd.resolution.value[0]):
            image = ccd.data.get(False)

        # Center spot
        logging.debug("Aligning spot...")
        future._centerspotf = CenterSpot(ccd, stage, FINE_MOVE)
        dist = future._centerspotf.result()
        if dist is None:
            raise IOError('Spot alignment failure. Cannot reach the center.')
        return dist
    finally:
        ccd.binning.value = init_binning
        ccd.exposureTime.value = init_et
        ccd.resolution.value = init_cres
        escan.scale.value = init_scale
        escan.resolution.value = init_eres


def _CancelAlignSpot(future):
    """
    Canceller of _DoAlignSpot task.
    """
    logging.debug("Cancelling spot alignment...")

    with _alignment_lock:
        if future._spot_alignment_state == FINISHED:
            return False
        future._spot_alignment_state = CANCELLED
        future._autofocusf.cancel()
        future._centerspotf.cancel()
        logging.debug("Spot alignment cancelled.")

    return True


def estimateAlignmentTime():
    """
    Estimates spot alignment procedure duration
    """
    # TODO
    return 60  # s


def FindSpot(image):
    """
    This function detects the spot and calculates and returns the coordinates of
    its center. The algorithms for spot detection and center calculation are 
    similar to the ones that are used in Fine alignment.
    image (model.DataArray): Optical image
    returns (tuple of floats):    The spot center coordinates
    """
    subimages, subimage_coordinates = coordinates.DivideInNeighborhoods(image, (1, 1), 20)
    if subimages == []:
        return None

    spot_coordinates = coordinates.FindCenterCoordinates(subimages)
    optical_coordinates = coordinates.ReconstructCoordinates(subimage_coordinates, spot_coordinates)
    print optical_coordinates
    if len(optical_coordinates) > 1:
        return None
    return optical_coordinates[0]

def CropFoV(ccd):
    """
    Limitate the ccd FoV to just contain the spot, in order to save some time
    on AutoFocus process.
    ccd (model.DigitalCamera): The CCD
    """
    # time.sleep(3)
    image = ccd.data.get(False)
    center_pxs = ((image.shape[1] / 2),
                 (image.shape[0] / 2))
    print center_pxs
    spot_pxs = FindSpot(image)
    tab_pxs = [a - b for a, b in zip(spot_pxs, center_pxs)]
    print tab_pxs
    max_dim = int(max(abs(tab_pxs[0]), abs(tab_pxs[1])))
    print max_dim
    range_x = (ccd.resolution.range[0][0], ccd.resolution.range[1][0])
    range_y = (ccd.resolution.range[0][1], ccd.resolution.range[1][1])
    print range_x, range_y
    qu = 2 * max_dim + FOV_MARGIN
    ccd.resolution.value = (sorted((range_x[0], 2 * max_dim + FOV_MARGIN, range_x[1]))[1],
                            sorted((range_y[0], 2 * max_dim + FOV_MARGIN, range_y[1]))[1])
    print ccd.resolution.value
    ccd.binning.value = (1, 1)
    #print ccd.resolution.value
    # Make sure acquired images have the correct resolution
    image = ccd.data.get(False)
    while image.shape != (ccd.resolution.value[1], ccd.resolution.value[0]):
        image = ccd.data.get(False)


def CenterSpot(ccd, stage, mx_steps):
    """
    Wrapper for _DoCenterSpot.
    ccd (model.DigitalCamera): The CCD
    stage (model.CombinedActuator): The stage
    mx_steps (int): Maximum number of steps to reach the center
    returns (model.ProgressiveFuture):    Progress of _DoCenterSpot,
                                         whose result() will return:
            returns (float):    Final distance to the center #m 
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateCenterTime())
    f._spot_center_state = RUNNING

    # Task to run
    doCenterSpot = _DoCenterSpot
    f.task_canceller = _CancelCenterSpot

    # Run in separate thread
    center_thread = threading.Thread(target=executeTask,
                  name="Spot center",
                  args=(f, doCenterSpot, f, ccd, stage, mx_steps))

    center_thread.start()
    return f

def _DoCenterSpot(future, ccd, stage, mx_steps):
    """
    Iteratively acquires an optical image, finds the coordinates of the spot 
    (center) and moves the stage to this position. Repeats until the found 
    coordinates are at the center of the optical image or a maximum number of 
    steps is reached.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    ccd (model.DigitalCamera): The CCD
    stage (model.CombinedActuator): The stage
    mx_steps (int): Maximum number of steps to reach the center
    returns (float):    Final distance to the center #m 
    raises:    
            CancelledError() if cancelled
            ValueError
    """
    stage_ab = InclinedStage("converter-ab", "stage",
                        children={"aligner": stage},
                        axes=["b", "a"],
                        angle=135)
    # time.sleep(4)
    image = ccd.data.get(False)

    # Center of optical image
    pixelSize = image.metadata[model.MD_PIXEL_SIZE]
    center_pxs = ((image.shape[1] / 2),
                 (image.shape[0] / 2))

    # Coordinates of found spot
    hdf5.export("FindSpot.h5", model.DataArray(image))
    spot_pxs = FindSpot(image)
    #print image.metadata
    #print spot_pxs
    if spot_pxs is None:
        return None
    tab_pxs = [a - b for a, b in zip(spot_pxs, center_pxs)]
    tab = (tab_pxs[0] * pixelSize[0], tab_pxs[1] * pixelSize[1])
    #print tab_pxs
    #print pixelSize
    dist = math.hypot(*tab)

    # Epsilon distance below which the lens is considered centered. The worse of:
    # * 1 pixels (because the CCD resolution cannot give us better)
    # * 1 µm (because that's the best resolution of our actuators)
    err_mrg = max(1.5 * pixelSize[0], 1e-06)  # m
    steps = 0

    # Stop once spot is found on the center of the optical image
    while dist > err_mrg:
        if future._spot_center_state == CANCELLED:
            raise CancelledError()
        # Or once max number of steps is reached
        if steps >= mx_steps:
            break

        # Move to the found spot
        f = stage_ab.moveRel({"x":tab[0], "y":-tab[1]})
        f.result()

        #Wait to make sure no previous spot is detected
        # time.sleep(4)
        image = ccd.data.get(False)
        spot_pxs = FindSpot(image)
        if spot_pxs is None:
            return None
        tab_pxs = [a - b for a, b in zip(spot_pxs, center_pxs)]
        tab = (tab_pxs[0] * pixelSize[0], tab_pxs[1] * pixelSize[1])
        dist = math.hypot(*tab)
        steps += 1

    #print dist
    return dist

def _CancelCenterSpot(future):
    """
    Canceller of _DoCenterSpot task.
    """
    logging.debug("Cancelling spot center...")

    with _center_lock:
        if future._spot_center_state == FINISHED:
            return False
        future._spot_center_state = CANCELLED
        logging.debug("Spot center cancelled.")

    return True


def estimateCenterTime():
    """
    Estimates duration of reaching the center
    """
    # TODO
    return 10  # s

class InclinedStage(model.Actuator):
    """
    Fake stage component (with X/Y axis) that converts two axes and shift them
     by a given angle.
    """
    def __init__(self, name, role, children, axes, angle=0):
        """
        children (dict str -> actuator): name to actuator with 2+ axes
        axes (list of string): names of the axes for x and y
        angle (float in degrees): angle of inclination (counter-clockwise) from
          virtual to physical
        """
        assert len(axes) == 2
        if len(children) != 1:
            raise ValueError("StageIncliner needs 1 child")

        self._child = children.values()[0]
        self._axes_child = {"x": axes[0], "y": axes[1]}
        self._angle = angle

        axes_def = {"x": self._child.axes[axes[0]],
                    "y": self._child.axes[axes[1]]}
        model.Actuator.__init__(self, name, role, axes=axes_def)

        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute(
                                    {"x": 0, "y": 0},
                                    unit="m", readonly=True)
        # it's just a conversion from the child's position
        self._child.position.subscribe(self._updatePosition, init=True)

        # No speed, not needed
        # self.speed = model.MultiSpeedVA(init_speed, [0., 10.], "m/s")

    def _convertPosFromChild(self, pos_child):
        a = math.radians(self._angle)
        xc, yc = pos_child
        pos = [xc * math.cos(a) - yc * math.sin(a),
               xc * math.sin(a) + yc * math.cos(a)]
        return pos

    def _convertPosToChild(self, pos):
        a = math.radians(-self._angle)
        x, y = pos
        posc = [x * math.cos(a) - y * math.sin(a),
                x * math.sin(a) + y * math.cos(a)]
        return posc

    def _updatePosition(self, pos_child):
        """
        update the position VA when the child's position is updated
        """
        # it's read-only, so we change it via _value
        vpos_child = [pos_child[self._axes_child["x"]],
                      pos_child[self._axes_child["y"]]]
        vpos = self._convertPosFromChild(vpos_child)
        self.position._value = {"x": vpos[0],
                                "y": vpos[1]}
        self.position.notify(self.position.value)

    @isasync
    def moveRel(self, shift):

        # shift is a vector, conversion is identical to a point
        vshift = [shift.get("x", 0), shift.get("y", 0)]
        vshift_child = self._convertPosToChild(vshift)

        shift_child = {self._axes_child["x"]: vshift_child[0],
                       self._axes_child["y"]: vshift_child[1]}
        f = self._child.moveRel(shift_child)
        return f

    # For now we don't support moveAbs(), not needed
    def moveAbs(self, pos):
        raise NotImplementedError("Do you really need that??")

    def stop(self, axes=None):
        # This is normally never used (child is directly stopped)
        self._child.stop()
