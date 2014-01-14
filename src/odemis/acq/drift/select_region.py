# -*- coding: utf-8 -*-
"""
Created on 8 Jan 2014

@author: kimon

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

import logging
import numpy
import math
import cv2
import time
import threading
import sys

from odemis import model
from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, \
    RUNNING
from odemis.util import TimeoutError

_selection_lock = threading.Lock()
_sem_done = threading.Event()


def SelectRegion(detector, emitter, dwell_time, sample_region):
    """
    Wrapper for _SelectRegion. It provides the ability to cancel the process.
    detector (model.Detector): The sec. electron detector
    emitter (model.Emitter): The e-beam scanner
    dwell_time (float): Time to scan each pixel
    sample_region (tuple of 3 floats): roi of the sample in order to avoid overlap
    returns (model.ProgressiveFuture): Progress of _SelectRegion 
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateSelectionTime(dwell_time, emitter.resolution.range[1]))
    f._selection_state = RUNNING

    # Task to run
    doSelectRegion = _SelectRegion
    f.task_canceller = _CancelSelectRegion

    # Run in separate thread
    selection_thread = threading.Thread(target=_executeTask,
                  name="Select_region",
                  args=(f, doSelectRegion, f, detector, emitter, dwell_time, sample_region))

    selection_thread.start()
    return f

def _SelectRegion(future, detector, emitter, dwell_time, sample_region):
    """
    It performs a scan of the whole image in order to detect a region with clean
    edges, proper for drift measurements. This region must not overlap with the
    sample that is to be scanned due to the danger of contamination.
    detector (model.Detector): The sec. electron detector
    emitter (model.Emitter): The e-beam scanner
    dwell_time (float): Time to scan each pixel
	sample_region (tuple of 3 floats): roi of the sample in order to avoid overlap
    returns (tuple of 3 floats): roi of the selected region
    """
    _sem_done.clear()

    # Scanner setup (order matters)
    emitter.scale.value = 1
    emitter.resolution.value = emitter.resolution.range[1]
    emitter.translation.value = (0, 0)
    emitter.dwellTime.value = dwell_time

    try:
        if future._selection_state == CANCELLED:
            raise CancelledError()
        detector.data.subscribe(_ssOnSEMImage)

        logging.debug("Scanning whole image...")

        if not _sem_done.wait(2 * dwell_time * numpy.prod(emitter.resolution.value) + 1):
            raise TimeoutError("Acquisition of SEM timed out")
        detector.data.unsubscribe(_ssOnSEMImage)

        with _selection_lock:
            if future._selection_state == CANCELLED:
                raise CancelledError()
            logging.debug("Selection done.")
            future._selection_state = FINISHED

    finally:
        detector.data.unsubscribe(_discard_data)
        ccd.data.unsubscribe(_ssOnCCDImage)
    # like in stream_drift_test for roi 0,0,1,1
    # cv2.Canny(img, 100, 200)
    return

def _ssOnSEMImage(self, df, data):
    logging.debug("SEM data received")
    df._electron_image = data
    df.unsubscribe(_ssOnSEMImage)
    # Do not stop the acquisition, as it ensures the e-beam is at the right place
    if not self._sem_done.is_set():
        self._sem_done.set()

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
    if not future.set_running_or_notify_cancel():
        return

    try:
        result = fn(*args, **kwargs)
    except BaseException:
        e = sys.exc_info()[1]
        future.set_exception(e)
    else:
        future.set_result(result)

def _CancelSelectRegion(future):
    """
    Canceller of _SelectRegion task.
    """
    logging.debug("Cancelling region selection...")

    with _selection_lock:
        if future._selection_state == FINISHED:
            logging.debug("Selection already finished.")
            return False
        future._selection_state = CANCELLED
        _sem_done.set()
        logging.debug("Selection cancelled.")

    return True

def estimateSelectionTime(dwell_time, repetitions):
    """
    Estimates selection procedure duration
    """
    return dwell_time * numpy.prod(repetitions) + 0.1  # s
