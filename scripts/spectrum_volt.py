#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 27 Mar 2017

@author: Éric Piel

Acquires a CL spectrum at different e-beam voltages.
If the e-beam is not in spot mode, it will be set to spot mode at the center
of the SEM field-of-view.
The spectrometer settings are used untouched.

Warning: the optical path should be properly configured already (ie, the spectrum
stream should be the last one playing in the GUI).

run as:
./spectrum_volt.py --volt 5 7.5 10 15 --spectrometer spectrometer-integrated --output spectra.h5

"""

import argparse
import logging
from odemis import model, dataio, util
import os
import sys


def save_hw_settings(ebeam):

    res = ebeam.resolution.value
    scale = ebeam.scale.value
    trans = ebeam.translation.value
    dt = ebeam.dwellTime.value
    volt = ebeam.accelVoltage.value

    hw_settings = (res, scale, trans, dt, volt)

    return hw_settings


def resume_hw_settings(ebeam, hw_settings):

    res, scale, trans, dt, volt = hw_settings

    # order matters!
    ebeam.scale.value = scale
    ebeam.resolution.value = res
    ebeam.translation.value = trans
    ebeam.dwellTime.value = dt

    ebeam.accelVoltage.value = volt


def discard_data(df, da):
    """
    Receives the SE detector data, which is unused
    """
    logging.debug("Received one ebeam data")


def acquire_volts(volts, detector):
    """
    vots (list of floats > 0): voltage in kV
    detector (str): role of the spectrometer to use
    returns (list of DataArray): all the spectra, in order
    """
    ebeam = model.getComponent(role="e-beam")
    sed = model.getComponent(role="se-detector")
    spmt = model.getComponent(role=detector)
    hw_settings = save_hw_settings(ebeam)

    # Go to spot mode (ie, res = 1x1)
    if ebeam.resolution.value != (1, 1):
        ebeam.resolution.value = (1, 1)
        ebeam.translation.value = (0, 0) # at the center of the FoV
    else:
        logging.info("Leaving the e-beam in spot mode at %s", ebeam.translation.value)

    ebeam.dwellTime.value = 0.1

    try:
        # Activate the e-beam
        sed.data.subscribe(discard_data)

        das = []
        for vstr in volts:
            v = float(vstr) * 1000
            ebeam.accelVoltage.value = v
            if not util.almost_equal(ebeam.accelVoltage.value, v):
                logging.warning("Voltage requested at %g kV, but e-beam set at %g kV",
                                v / 1000, ebeam.accelVoltage.value / 1000)
            else:
                logging.info("Acquiring at %g kV", v / 1000)

            # Acquire one spectrum
            spec = spmt.data.get()
            # Add dimensions to make it a spectrum (X, first dim -> C, 5th dim)
            spec.shape = (spec.shape[-1], 1, 1, 1, 1)

            # Add some useful metadata
            spec.metadata[model.MD_DESCRIPTION] = "Spectrum at %g kV" % (v / 1000)
            spec.metadata[model.MD_EBEAM_VOLTAGE] = v
            # TODO: store the spot position in MD_POS
            das.append(spec)

    finally:
        sed.data.unsubscribe(discard_data)  # Just to be sure
        resume_hw_settings(ebeam, hw_settings)

    return das


def save_data(das, filename):
    """
    Saves a series of spectra
    das (list of DataArray): data to save
    filename (str)
    """
    exporter = dataio.find_fittest_converter(filename)

    if os.path.exists(filename):
        # mostly to warn if multiple ypos/xpos are rounded to the same value
        logging.warning("Overwriting file '%s'.", filename)
    else:
        logging.info("Saving file '%s", filename)

    exporter.export(filename, das)


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser(description="Acquires a CL spectrum at different e-beam voltages")

    parser.add_argument("--volt", "-v", dest="volts", nargs="+",
                        help="Voltages (in kV) for which a spectrum should be acquired"
                        )
    parser.add_argument("--spectrometer", "-s", dest="spectrometer", default="spectrometer",
                        help="Role of the detector to use to acquire a spectrum (default: spectrometer)"
                        )
    parser.add_argument("--output", "-o", dest="output", required=True,
                        help="Name where to save the spectra. "
                        "The file format is derived from the extension "
                        "(TIFF and HDF5 are supported).")
    parser.add_argument("--log-level", dest="loglev", metavar="<level>", type=int,
                        default=1, help="set verbosity level (0-2, default = 1)")

    options = parser.parse_args(args[1:])

    # Set up logging before everything else
    if options.loglev < 0:
        logging.error("Log-level must be positive.")
        return 127
    loglev_names = [logging.WARNING, logging.INFO, logging.DEBUG]
    loglev = loglev_names[min(len(loglev_names) - 1, options.loglev)]
    logging.getLogger().setLevel(loglev)

    try:
        das = acquire_volts(options.volts, options.spectrometer)
        save_data(das, options.output)

    except KeyboardInterrupt:
        logging.info("Interrupted before the end of the execution")
        return 1
    except ValueError as exp:
        logging.error("%s", exp)
        return 127
    except Exception:
        logging.exception("Unexpected error while performing action.")
        return 127

    return 0

if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)
