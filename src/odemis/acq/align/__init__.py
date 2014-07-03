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
from .autofocus import AutoFocus


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
    f.task_canceller = _CancelFindOverlay

    # Create scanner for scan grid
    f._scanner = GridScanner(repetitions, dwell_time, escan, ccd, detector)

    # Run in separate thread
    overlay_thread = threading.Thread(target=executeTask,
                  name="SEM/CCD overlay",
                  args=(f, _DoFindOverlay, f, repetitions, dwell_time, max_allowed_diff, escan, ccd, detector))

    overlay_thread.start()
    return f


def AlignSpot(ccd, stage, escan, focus):
    """
    Wrapper for DoAlignSpot. It provides the ability to check the progress of
    spot mode procedure or even cancel it.
    ccd (model.DigitalCamera): The CCD
    stage (model.Actuator): The stage
    escan (model.Emitter): The e-beam scanner
    focus (model.Actuator): The optical focus
    returns (model.ProgressiveFuture):    Progress of DoAlignSpot,
                                         whose result() will return:
            returns (float):    Final distance to the center #m 
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateAlignmentTime(ccd.exposureTime.value))
    f._spot_alignment_state = RUNNING

    # Task to run
    f.task_canceller = _CancelAlignSpot
    f._alignment_lock = threading.Lock()

    # Create autofocus and centerspot module
    f._autofocusf = model.InstantaneousFuture()
    f._centerspotf = model.InstantaneousFuture()

    # Run in separate thread
    alignment_thread = threading.Thread(target=executeTask,
                  name="Spot alignment",
                  args=(f, _DoAlignSpot, f, ccd, stage, escan, focus))

    alignment_thread.start()
    return f

