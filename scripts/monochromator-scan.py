#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 19 Nov 2015

@author: Éric Piel

This is a script to acquire a full spectrum based on a monochromator, by scanning
along the center wavelength of the spectrograph

run as:
./scripts/monochromator-scan.py

You first need to run Odemis (with a SPARC). Then, in the acquisition tab,
select spot mode, and pick the point you're interested.

'''

from __future__ import division

import logging
from odemis import dataio, model
from odemis.util import units
import odemis
import readline  # for nice editing in raw_input()
import sys
import threading
import numpy
import math


logging.getLogger().setLevel(logging.INFO) # put "DEBUG" level for more messages

_pt_acq = threading.Event()
_data = []
_md = None

def _on_mchr_data(df, data):
    global _md, _data, _pt_acq
    if not _md:
        _md = data.metadata.copy()
    _data.append(data[0, 0])
    _pt_acq.set()

def acquire_spec(wls, wle, res, dt, filename):
    """
    wls (float): start wavelength in m
    wle (float): end wavelength in m
    res (int): number of points to acquire
    dt (float): dwell time in seconds
    filename (str): filename to save to
    """

    ebeam = model.getComponent(role="e-beam")
    sed = model.getComponent(role="se-detector")
    mchr = model.getComponent(role="monochromator")
    sgrh = model.getComponent(role="spectrograph")

    prev_dt = ebeam.dwellTime.value
    prev_res = ebeam.resolution.value
    prev_scale = ebeam.scale.value
    prev_trans = ebeam.translation.value
    prev_wl = sgrh.position.value["wavelength"]

    ebeam.resolution.value = (1, 1)  # Force one pixel only
    ebeam.dwellTime.value = dt
    trig = mchr.softwareTrigger
    df = mchr.data
    df.synchronizedOn(trig)
    df.subscribe(_on_mchr_data)

    wllist = []
    if res <= 1:
        res = 1
        wli = 0
    else:
        wli = (wle - wls) / (res - 1)
 
    das = []
    try:
        for i in range(res):
            cwl = wls + i * wli  # requested value
            sgrh.moveAbs({"wavelength": cwl}).result()
            cwl = sgrh.position.value["wavelength"]  # actual value
            logging.info("Acquiring point %d/%d @ %s", i + 1, res,
                         units.readable_str(cwl, unit="m", sig=3))

            _pt_acq.clear()
            trig.notify()
            if not _pt_acq.wait(dt * 5 + 1):
                raise IOError("Timeout waiting for the data")
            wllist.append(cwl)

    except KeyboardInterrupt:
        logging.info("Stopping after only %d images acquired", i + 1)
    finally:
        df.unsubscribe(_on_mchr_data)
        df.synchronizedOn(None)
        logging.debug("Restoring hardware settings")
        if prev_res != (1, 1):
            ebeam.resolution.value = prev_res
        ebeam.dwellTime.value = prev_dt
        sgrh.moveAbs({"wavelength": prev_wl})

    if _data:  # Still save whatever got acquired, even if interrupted
        # Convert the sequence of data into one spectrum
        na = numpy.array(_data)  # keeps the dtype
        na.shape += (1, 1, 1, 1)  # make it 5th dim to indicate a channel
        md = _md
        md[model.MD_WL_LIST] = wllist

        # MD_POS should already be at the correct position (from the e-beam metadata)

        # MD_PIXEL_SIZE is not meaningful but handy for the display in Odemis
        # (it's the size of the square on top of the SEM survey => BIG!)
        sempxs = ebeam.pixelSize.value
        md[model.MD_PIXEL_SIZE] = (sempxs[0] * 50, sempxs[1] * 50)

        md[model.MD_DESCRIPTION] = "Spectrum"
        spec = model.DataArray(na, md)
        das.append(spec)

    # Acquire survey image
    try:
        logging.info("Acquiring SEM survey image")
        ebeam.translation.value = (0, 0)
        ebeam.scale.value = (1, 1)  # Allow full FoV
        ebeam.resolution.value = ebeam.resolution.range[1] # max FoV
        ebeam.scale.value = (4, 4)  # not too many pixels
        ebeam.dwellTime.value = 10e-6 # 10µs is hopefully enough
        semsur = sed.data.get()
        semsur.metadata[model.MD_DESCRIPTION] = "SEM survey"
        das.insert(0, semsur)
    finally:
        logging.debug("Restoring hardware settings")
        ebeam.scale.value = prev_scale
        ebeam.translation.value = prev_trans
        if prev_res != (1, 1):
            ebeam.resolution.value = prev_res
        ebeam.dwellTime.value = prev_dt

    if das:
        # Save the file
        exporter = dataio.find_fittest_converter(filename)
        exporter.export(filename, [semsur, spec])
        logging.info("Spectrum successfully saved to %s", filename)
        raw_input("Press Enter to close.")

def getNumber(prompt):
    """
    return (float)
    """
    while True:
        s = raw_input(prompt)
        try:
            return float(s)
        except ValueError:
            print("Please type in a valid number")

def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """
    ebeam = model.getComponent(role="e-beam")
    if ebeam.resolution.value != (1, 1):
        raw_input("Please select spot mode and pick a point and press Enter...")


    wls = getNumber("Starting wavelength (in nm): ") * 1e-9
    wle = getNumber("Ending wavelength (in nm): ") * 1e-9
    nbp = getNumber("Number of wavelengths to acquire: ")
    dt = getNumber("Dwell time (in ms): ") * 1e-3
    exp_time = nbp * (dt + 0.05)  # 50 ms to change wavelength
    print("Expected duration: %s" % (units.readable_time(math.ceil(exp_time)),))

    filename = raw_input("Filename to store the spectrum: ")
    if "." not in filename:
        # No extension -> force hdf5
        filename += ".h5"
    
    print("Press Ctrl+C to cancel the acquisition")

    try:
        n = acquire_spec(wls, wle, int(nbp), dt, filename)
    except Exception:
        logging.exception("Unexpected error while performing action.")
        return 127

    return 0

if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)

