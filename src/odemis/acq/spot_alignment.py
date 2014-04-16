# -*- coding: utf-8 -*-
"""
Created on 15 April 2014

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

from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, \
    RUNNING
import logging
import math
import numpy
from odemis import model
from odemis.dataio import hdf5
import operator
import os
import threading
import time

from .align import spot, focus
from odemis.acq._futures import executeTask


_alignment_lock = threading.Lock()


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
    returns result (boolean) : True if spot is at the center
    raises:    
            CancelledError() if cancelled
            ValueError
    """
    init_binning = ccd.binning.value
    init_et = ccd.exposureTime.value

    logging.debug("Starting Spot alignment...")

    if future._spot_alignment_state == CANCELLED:
        raise CancelledError()
    logging.debug("Autofocusing...")
    lens_pos = focus.AutoFocus(ccd, focus)

    if future._spot_alignment_state == CANCELLED:
        raise CancelledError()
    logging.debug("Aligning spot...")
    result = spot.CenterSpot(ccd, stage)

    if result == False:
        ccd.binning.value = init_binning
        ccd.exposureTime.value = init_et
        raise IOError('Spot alignment failure')

    return result


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
            result (boolean) : True if spot is at the center
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateAlignmentTime())
    f._spot_alignment_state = RUNNING

    # Task to run
    doAlignSpot = _DoAlignSpot
    f.task_canceller = _CancelAlignSpot

    # Run in separate thread
    alignment_thread = threading.Thread(target=executeTask,
                  name="Spot alignment",
                  args=(f, doAlignSpot, f, ccd, stage, escan, focus))

    alignment_thread.start()
    return f

def _CancelAlignSpot(future):
    """
    Canceller of _DoAlignSpot task.
    """
    logging.debug("Cancelling spot alignment...")

    with _alignment_lock:
        if future._spot_alignment_state == FINISHED:
            return False
        future._spot_alignment_state = CANCELLED
        future._future_scan.cancel()
        logging.debug("Spot alignment cancelled.")

    return True

def estimateAlignmentTime():
    """
    Estimates spot alignment procedure duration
    """
    # TODO
    return 0  # s

