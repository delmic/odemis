# -*- coding: utf-8 -*-
"""
Created on 18 April 2014

@author: Kimon Tsitsikas

Copyright © 2013-2014 Éric Piel & Kimon Tsitsikas, Delmic

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

from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, \
    RUNNING
import logging
import threading
import numpy
import time
from odemis.acq._futures import executeTask
from odemis import model
#
from .images import GridScanner
from .find_overlay import estimateOverlayTime, _DoFindOverlay, _CancelFindOverlay
from .spot import estimateAlignmentTime, _DoAlignSpot, _CancelAlignSpot
from .autofocus import estimateAutoFocusTime, _DoAutoFocus, _CancelAutoFocus, MeasureFocus

SPOTMODE_ACCURACY = 0.0001  # focus accuracy in spot mode #m


def FindOverlay(repetitions, dwell_time, max_allowed_diff, escan, ccd, detector):
    """
    Wrapper for DoFindOverlay. It provides the ability to check the progress of overlay procedure 
    or even cancel it.
    repetitions (tuple of ints): The number of CL spots are used
    dwell_time (float): Time to scan each spot #s
    max_allowed_diff (float): Maximum allowed difference in electron coordinates #m
    escan (model.Emitter): The e-beam scanner
    ccd (model.DigitalCamera): The CCD
    detector (model.Detector): The electron detector
    returns (model.ProgressiveFuture):    Progress of DoFindOverlay, whose result() will return:
            translation (Tuple of 2 floats), 
            scaling (Float), 
            rotation (Float): Transformation parameters
            transform_data : Transform metadata
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateOverlayTime(dwell_time, repetitions))
    f._find_overlay_state = RUNNING
    f._overlay_lock = threading.Lock()

    # Task to run
    doFindOverlay = _DoFindOverlay
    f.task_canceller = _CancelFindOverlay

    # Create scanner for scan grid
    f._scanner = GridScanner(repetitions, dwell_time, escan, ccd, detector)

    # Run in separate thread
    overlay_thread = threading.Thread(target=executeTask,
                  name="SEM/CCD overlay",
                  args=(f, doFindOverlay, f, repetitions, dwell_time, max_allowed_diff, escan, ccd, detector))

    overlay_thread.start()
    return f


def AlignSpot(ccd, stage, escan, focus):
    """
    Wrapper for DoAlignSpot. It provides the ability to check the progress of
    spot mode procedure or even cancel it.
    ccd (model.DigitalCamera): The CCD
    stage (model.CombinedActuator): The stage
    escan (model.Emitter): The e-beam scanner
    focus (model.CombinedActuator): The optical focus
    returns (model.ProgressiveFuture):    Progress of DoAlignSpot,
                                         whose result() will return:
            returns (float):    Final distance to the center #m 
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateAlignmentTime())
    f._spot_alignment_state = RUNNING

    # Task to run
    doAlignSpot = _DoAlignSpot
    f.task_canceller = _CancelAlignSpot

    # Create autofocusing module
    f._autofocus = AutoFocusing(ccd, escan, focus, SPOTMODE_ACCURACY)

    # Run in separate thread
    alignment_thread = threading.Thread(target=executeTask,
                  name="Spot alignment",
                  args=(f, doAlignSpot, f, ccd, stage, escan, focus))

    alignment_thread.start()
    return f

def AutoFocus(device, escan, focus, accuracy):
    """
    Wrapper for DoAutoFocus. It provides the ability to check the progress of autofocus 
    procedure or even cancel it.
    device: model.DigitalCamera or model.Detector
    focus (model.CombinedActuator): The optical focus
    accuracy (float): Focus precision #m
    returns (model.ProgressiveFuture):    Progress of DoAutoFocus, whose result() will return:
            Focus position #m
            Focus level
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    # Check if you focus the SEM or a digital camera
    if device.role == "se-detector":
        et = escan.dwellTime.value * numpy.prod(escan.resolution.value)
    else:
        et = device.exposureTime.value
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateAutoFocusTime(et))
    f._autofocus_state = RUNNING
    f._autofocus_lock = threading.Lock()

    # Task to run
    doAutoFocus = _DoAutoFocus
    f.task_canceller = _CancelAutoFocus

    # Run in separate thread
    autofocus_thread = threading.Thread(target=executeTask,
                  name="Autofocus",
                  args=(f, doAutoFocus, f, device, et, focus, accuracy))

    autofocus_thread.start()
    return f

class AutoFocusing(object):
    """
    Class providing the ability to AlignSpot to create an AutoFocusing module.
    """
    def __init__(self, device, escan, focus, accuracy):
        self.device = device
        self.escan = escan
        self.focus = focus
        self.accuracy = accuracy
        self.future_focus = None

        self._autofocus_state = FINISHED
        self._autofocus_lock = threading.Lock()

    def _doMeasureFocus(self, image):
        return MeasureFocus(image)

    def DoAutoFocus(self):
        self.future_focus = AutoFocus(self.device, self.escan, self.focus, self.accuracy)
        foc_pos, fm_level = self.future_focus.result()
        return foc_pos, fm_level

    def CancelAutoFocus(self):
        """
        Canceller of DoAutoFocus task.
        """
        return self.future_focus.cancel()
