# -*- coding: utf-8 -*-
"""
Created on 18 Dec 2013

@author: Kimon Tsitsikas

Copyright © 2012-2013 Kimon Tsitsikas, Delmic

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

import logging
import time
import sys
import threading
import numpy

from odemis import model
from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, \
    RUNNING

_scan_lock = threading.Lock()
_ccd_done = threading.Event()
global _optical_image
        
def _discard_data(df, data):
    """
    Does nothing, just discard the SEM data received (for spot mode)
    """
    pass

def _ssOnCCDImage(df, data):
    """
    Receives the CCD data
    """
    global _optical_image
    _optical_image = data
    _ccd_done.set()
    logging.debug("Got CCD image!")

def ScanGrid(repetitions, dwell_time, escan, ccd, detector):
    """
    Wrapper for DoScanGrid. It provides the ability to check the progress of scan procedure 
    or even cancel it.
    repetitions (tuple of ints): The number of CL spots are used
    dwell_time (float): Time to scan each spot #s
    escan (model.Emitter): The e-beam scanner
    ccd (model.DigitalCamera): The CCD
    detector (model.Detector): The electron detector
    returns (model.ProgressiveFuture):    Progress of DoScanGrid
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateScanTime(dwell_time, repetitions))
    f._state = RUNNING

    # Task to run
    doScanGrid = _DoScanGrid
    f.task_canceller = _CancelScanGrid

    # Run in separate thread
    scan_thread = threading.Thread(target=_executeTask,
                  name="Scan grid",
                  args=(f, doScanGrid, f, repetitions, dwell_time, escan, ccd, detector))

    scan_thread.start()
    return f

def _DoScanGrid(future, repetitions, dwell_time, escan, ccd, detector):
    """
    Uses the e-beam to scan the rectangular grid consisted of the given number 
    of spots and acquires the corresponding CCD image
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    repetitions (tuple of ints): The number of CL spots are used
    dwell_time (float): Time to scan each spot #s
    escan (model.Emitter): The e-beam scanner
    ccd (model.DigitalCamera): The CCD
    detector (model.Detector): The electron detector
    returns (model.DataArray): 2D array containing the intensity of each pixel in 
                                the spotted optical image
            (List of tuples):  Coordinates of spots in electron image
            (Tuple of floats):    Scaling of electron image
    """
    _ccd_done.clear()

    # Scanner setup (order matters)
    scale = [(escan.resolution.range[1][0] - 1) / repetitions[0],
             (escan.resolution.range[1][1] - 1) / repetitions[1]]
    escan.scale.value = scale
    escan.resolution.value = repetitions
    escan.translation.value = (0, 0)

    # TODO: use the smallest dwell time _always_
    dt_rng = escan.dwellTime.range
    escan.dwellTime.value = dt_rng[0]
    # escan.dwellTime.value = max(dt_rng[0], min(dt, dt_rng[1]))

    # CCD setup
    binning = (1, 1)
    ccd.binning.value = binning
    ccd.resolution.value = (ccd.shape[0] // binning[0],
                            ccd.shape[1] // binning[1])
    et = numpy.prod(repetitions) * dwell_time
    # For safety, ensure the exposure time is at least twice the time for a whole scan
    ccd.exposureTime.value = et  # s
    readout = numpy.prod(ccd.resolution.value) / ccd.readoutRate.value
    tot_time = et + readout + 0.05

    try:
        if future._state == CANCELLED:
            raise CancelledError()
        detector.data.subscribe(_discard_data)
        ccd.data.subscribe(_ssOnCCDImage)

        logging.debug("Scanning spot grid...")

        # Wait for CCD to capture the image
        _ccd_done.wait(2 * tot_time + 1)  # TODO timeout

        with _scan_lock:
            if future._state == CANCELLED:
                detector.data.unsubscribe(_discard_data)
                ccd.data.unsubscribe(_ssOnCCDImage)
                raise CancelledError()
            logging.debug("Scan done.")
            future._state = FINISHED

    finally:
        detector.data.unsubscribe(_discard_data)
        ccd.data.unsubscribe(_ssOnCCDImage)

    electron_coordinates = []
    # TODO: convert to numpy call?
    for i in xrange(repetitions[0]):
        for j in xrange(repetitions[1]):
            # Compute electron coordinates based on scale and repetitions
            electron_coordinates.append((i * scale[0], j * scale[1]))

    return _optical_image, electron_coordinates, scale

# Copy from acqmng
# @staticmethod
def _executeTask(future, fn, *args, **kwargs):
    """
    Executes a task represented by a future.
    Usually, called as main task of a (separate thread).
    Based on the standard futures code _WorkItem.run()
    future (Future): future that is used to represent the task
    fn (callable): function to call for running the future
    *args, **kwargs: passed to the fn
    returns None: when the task is over (or cancelled)
    """
    try:
        result = fn(*args, **kwargs)
    except BaseException:
        e = sys.exc_info()[1]
        future.set_exception(e)
    else:
        future.set_result(result)

def _CancelScanGrid(future):
    """
    Canceller of _DoScanGrid task.
    """    
    logging.debug("Cancelling scan...")
    
    with _scan_lock:
        if future._state == FINISHED:
            logging.debug("Scan already finished.")
            return False
        future._state = CANCELLED
        _ccd_done.set()
        logging.debug("Scan cancelled.")

    return True

def estimateScanTime(dwell_time, repetitions):
    """
    Estimates scan procedure duration
    """
    return dwell_time * repetitions[0] * repetitions[1] + 0.1  # s

