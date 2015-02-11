# -*- coding: utf-8 -*-
"""
Created on 11 Feb 2014

@author: Kimon Tsitsikas

Copyright © 2012-2013 Kimon Tsitsikas, Delmic

This script acquires a CCD and SEM image with the current settings and uses
FindOverlay module to generate the overlay image of them.

run as:
python demo_overlay.py --repetitions_x 4 --repetitions_y 4 --dwell_time 0.1 --max_allowed_diff 1e-06

--repetitions defines the number of CL spots in the grid.
--dwell_time indicates the time to scan each spot. #s
--max_allowed_diff indicates the maximum allowed difference in electron coordinates. #m

You first need to run the odemis backend with the SECOM config:
odemisd --log-level 2 install/linux/usr/share/odemis/secom-tud.odm.yaml
"""

from __future__ import division

import logging
import numpy
from odemis import model
from odemis.dataio import hdf5
from odemis.acq.align import images, coordinates, transform
import sys
import threading
import time
import operator
import argparse
import math
import Image
from scipy import ndimage
from scipy import misc
from odemis.util import img
from odemis.acq import stream
from odemis.acq.align import find_overlay
from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, \
    RUNNING

logging.getLogger().setLevel(logging.DEBUG)

_acq_lock = threading.Lock()
_sem_done = threading.Event()

def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser(description=
                     "Automated AR acquisition at multiple spot locations")

    parser.add_argument("--repetitions_x", "-x", dest="repetitions_x", required=True,
                        help="repetitions defines the number of CL spots in the grid (x dimension)")
    parser.add_argument("--repetitions_y", "-y", dest="repetitions_y", required=True,
                        help="repetitions defines the number of CL spots in the grid (y dimension)")
    parser.add_argument("--dwell_time", "-t", dest="dwell_time", required=True,
                        help="dwell_time indicates the time to scan each spot (unit: s)")
    parser.add_argument("--max_allowed_diff", "-d", dest="max_allowed_diff", required=True,
                        help="max_allowed_diff indicates the maximum allowed difference in electron coordinates (unit: m)")

    options = parser.parse_args(args[1:])
    repetitions = (int(options.repetitions_x), int(options.repetitions_y))
    dwell_time = float(options.dwell_time)
    max_allowed_diff = float(options.max_allowed_diff)

    try:
        escan = None
        detector = None
        ccd = None
        # find components by their role
        for c in model.getComponents():
            if c.role == "e-beam":
                escan = c
            elif c.role == "bs-detector":
                detector = c
            elif c.role == "ccd":
                ccd = c
            # elif c.role == "light":
            #    light = c
        if not all([escan, detector, ccd]):
            logging.error("Failed to find all the components")
            raise KeyError("Not all components found")
        
        # f_acq = SEMCCDAcquisition(escan, ccd, detector, light)

        # optical_image_1, optical_image_2, optical_image_3, electron_image = f_acq.result()
        
        f = find_overlay.FindOverlay(repetitions, dwell_time, max_allowed_diff, escan, ccd, detector,
                                      skew=True)
        trans_val, cor_md = f.result()
        trans_md, skew_md = cor_md
        iscale = trans_md[model.MD_PIXEL_SIZE_COR]
        irot = -trans_md[model.MD_ROTATION_COR] % (2 * math.pi)
        ishear = -skew_md[model.MD_SHEAR_COR]
        iscale_xy = skew_md[model.MD_PIXEL_SIZE_COR]
        logging.debug("iscale: %s irot: %s ishear: %s iscale_xy: %s", iscale, irot, ishear, iscale_xy)

        # md_1 = img.mergeMetadata(optical_image_1.metadata, correction_md)
        # md_2 = img.mergeMetadata(optical_image_2.metadata, correction_md)
        # md_3 = img.mergeMetadata(optical_image_3.metadata, correction_md)
        # optical_image_1.metadata.update(md_1)
        # optical_image_2.metadata.update(md_2)
        # optical_image_3.metadata.update(md_3)

    except:
        logging.exception("Unexpected error while performing action.")
        return 127

    return 0

def SEMCCDAcquisition(escan, ccd, detector, light):
    f = model.ProgressiveFuture()
    f._acq_state = RUNNING

    # Task to run
    doAcquisition = _DoAcquisition
    f.task_canceller = _CancelAcquisition

    # Run in separate thread
    acq_thread = threading.Thread(target=_executeTask,
                  name="SEMCCDAcquisition",
                  args=(f, doAcquisition, f, escan, ccd, detector, light))

    acq_thread.start()
    return f

def _ssOnSEMImage(df, data):
    """
    Receives the SEM data
    """
    df._electron_image = data
    df.unsubscribe(_ssOnSEMImage)
    _sem_done.set()
    logging.debug("Got SEM image!")

def _DoAcquisition(future, escan, ccd, detector, light):
    _sem_done.clear()

    try:
        if future._acq_state == CANCELLED:
            raise CancelledError()

        logging.debug("Acquiring CCD images...")

        # Turn on light for CCD acquisition
        emissions = [1, 0, 0, 0, 0, 0, 0]
        light.emissions.value = emissions

        optical_image_1 = ccd.data.get()

        emissions = [0, 1, 0, 0, 0, 0, 0]
        light.emissions.value = emissions

        optical_image_2 = ccd.data.get()

        emissions = [0, 0, 1, 0, 0, 0, 0]
        light.emissions.value = emissions

        optical_image_3 = ccd.data.get()

        with _acq_lock:
            if future._acq_state == CANCELLED:
                raise CancelledError()
            logging.debug("Acquisition done.")
            future._acq_state = FINISHED

        # Turn off light for CCD acquisition
        emissions = [0] * len(light.emissions.value)
        light.emissions.value = emissions

        logging.debug("Acquiring SEM image...")

        detector.data.subscribe(_ssOnSEMImage)
        # Wait for SEM to capture the image
        if not _sem_done.wait(2 * numpy.prod(escan.resolution.value) * escan.dwellTime.value + 4):
            raise TimeoutError("Acquisition of SEM timed out")

        detector.data.unsubscribe(_ssOnSEMImage)

    finally:
        detector.data.unsubscribe(_ssOnSEMImage)

    return optical_image_1, optical_image_2, optical_image_3,
    detector.data._electron_image

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

def _CancelAcquisition(future):
    """
    Canceller of _DoAcquisition task.
    """
    logging.debug("Cancelling acquisition...")

    with _acq_lock:
        if future._acq_state == FINISHED:
            logging.debug("Acquisition already finished.")
            return False
        future._acq_state = CANCELLED
        _sem_done.set()
        logging.debug("Acquisition cancelled.")

    return True


if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)
