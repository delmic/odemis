#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 7 Mar 2014

@author: Éric Piel

This is a script to acquire a set of spectrums via a AR pinhole to recreate
an entire AR spectral data cube. It doesn't handle the pinhole actuator.

run as:
./scripts/ar_spectral_ph.py -x 12 -y 15 --spot=0.5,0.6 --drift 10 --anchor=0.1,0.2,0.1,0.2 --output filename.h5

-x -y defines the number of images to acquire in X and Y (resolution of the AR)
--spot defines the position of the ebeam spot (in coordinates between 0->1 of
  the SEM field of view)
--drift defines the time between each drift corrections
--anchor defines the top-left and bottom-right points of the anchor region
--output indicates the name of the file which will contain all the output. It 
         should finish by .h5 (for HDF5) or .tiff (for TIFF).

You first need to run the odemis backend with the SPARC config. For instance,
start Odemis, and close the graphical interface. Alternatively you can start
just the back-end with a command such as:
odemisd --log-level 2 install/linux/usr/share/odemis/sparc.odm.yaml

To change some configuration settings, you can use the cli:
# ensure the magnification is correct
odemis-cli --set-attr "EBeam ExtXY" magnification 5000
# to select the spectrometer exposure time:
odemis-cli --set-attr "Spec10" exposureTime 0.1 # in s
# to select the center wavelength of the spectrometer
odemis-cli --position "SP2300i" wavelength 500e-3 # in µm
'''

import argparse
import logging
import numpy
from odemis import dataio, model
from odemis.acq import drift
from odemis.util import driver
import sys
import threading
from builtins import input

logging.getLogger().setLevel(logging.INFO) # put "DEBUG" level for more messages

class Acquirer(object):
    def __init__(self):
        # find components by their role
        # the ebeam scanner
        self.escan = model.getComponent(role="e-beam")
        # the secondary electron detector
        self.sed = model.getComponent(role="se-detector")
        # the spectrometer
        self.spect = model.getComponent(role="spectrometer")
        
        # For acquisition
        self.spec_data = None
        self.acq_done = threading.Event()

        # For drift correction
        self.drift = (0, 0)

    def acquire_spec(self, spot):
        """
        Acquires a spectrum for the ebeam at one spot position
        spot (2 0<=float<=1): ebeam position
        return (DataArray of one dimension): spectrum
        """
        self.escan.translation.value = (spot[0] - self.drift[0],
                                        spot[1] - self.drift[1])

        # trigger the CCD acquisition
        self.acq_done.clear()
        self.spect.softwareTrigger.notify()

        # wait for it
        logging.debug("Waiting for acquisition")
        self.acq_done.wait()

        # reshape to just one dimension
        self.spec_data.shape = self.spec_data.shape[1:]
        return self.spec_data

    def on_spectrum(self, df, d):
        self.spec_data = d
        self.acq_done.set()

    def discard_sem(self, df, d):
        # receives SEM image... and discards it
        pass

    def acquire_arcube(self, shape, spot, filename, dperiod=None, anchor=None):
        """
        shape (int, int)
        spot (float, float)
        filename (str)
        dperiod (0<float): drift correction period
        anchor (4* 0<=float<=1): anchor region for drift correction
        """
        # Set up the drift correction (using a 10µs dwell time for the anchor)
        if anchor:
            de = drift.AnchoredEstimator(self.escan, self.sed, anchor, 10e-6)
            de.acquire() # original anchor region
            # Estimate the number of pixels the drift period corresponds to
            px_time = (self.spect.exposureTime.value + # exposure time
                       numpy.prod(self.spect.resolution.value) / self.spect.readoutRate.value + # readout time
                       0.1) # overhead (eg, pinhole movement)
            px_iter = de.estimateCorrectionPeriod(dperiod, px_time, shape)
            next_dc = next(px_iter)

        # Set the E-beam in spot mode (order matters)
        self.escan.scale.value = (1, 1)
        self.escan.resolution.value = (1, 1)
        self.escan.dwellTime.value = 0.1 # s, anything not too short/long is fine
        # start the e-beam "scanning"
        self.sed.data.subscribe(self.discard_sem)

        # start the CCD acquisition, blocked on softwareTrigger
        self.spect.data.synchronizedOn(self.spect.softwareTrigger)
        self.spect.data.subscribe(self.on_spectrum)

        spec_data = []
        n = 0
        for i in numpy.ndindex(shape[::-1]): # scan along X fast, then Y
            logging.info("Going to acquire AR point %s", i)

            # TODO: replace next line by code waiting for the pinhole actuator
            # to be finished moving.
            input("Press enter to start next spectrum acquisition...")
            spec = self.acquire_spec(spot)
            # TODO: replace next line by code letting know the pinhole actuator
            # that it should go to next point.
            print("Spectrum for point %s just acquired" % (i,))

            spec_data.append(spec)

            if anchor:
                # Time to do drift-correction?
                n += 1
                if n >= next_dc:
                    de.acquire() # take a new
                    d = de.estimate()
                    self.drift = (self.drift[0] + d[0], self.drift[1] + d[1])
                    logging.info("Drift estimated to %s", self.drift)
                    n = 0
                    next_dc = next(px_iter)

        # Stop all acquisition
        self.spect.data.unsubscribe(self.on_spectrum)
        self.spect.data.synchronizedOn(None)
        self.sed.data.unsubscribe(self.discard_sem)

        data = self.assemble_cube(shape, spec_data)
        # save the file
        exporter = dataio.find_fittest_converter(filename)
        exporter.export(filename, data)

    def assemble_cube(self, shape, specs):
        """
        Assemble all the spectrum data together
        shape (int,int)
        specs (list of DataArray of one dimension): must be in order X/Y
        return DataArray (3 dimensions): spectral cube
        """

        # create a cube out of the spectral data acquired
        # dimensions must be wavelength, 1, 1, Y, X
        assert len(specs) == numpy.prod(shape)
        # each element of specs has a shape of (N)
        # reshape to (N, 1)
        for s in specs:
            s.shape += (1,)
        # concatenate into one big array of (N, Y*X)
        spect_data = numpy.concatenate(specs, axis=1)
        # reshape to (N, 1, 1, Y, X)
        spect_data.shape = (spect_data.shape[0], 1, 1, shape[1], shape[0])

        # copy the metadata from the first point
        spect_data = model.DataArray(spect_data, metadata=specs[0].metadata)

        return spect_data


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser(description=
                     "AR spectral acquisition")

    parser.add_argument("-x", dest="X", type=int, required=True,
                        help="shape of AR image on the X axis")
    parser.add_argument("-y", dest="Y", type=int, required=True,
                        help="shape of AR image on the Y axis")
    parser.add_argument("--spot", dest="spot", required=True,
                        help="e-beam spot position")
    parser.add_argument("--drift", "-d", dest="drift", type=float, default=None,
                        help="time between 2 drift corrections")
    parser.add_argument("--anchor", dest="anchor", default=None,
                        help="e-beam spot position")
    parser.add_argument("--output", "-o", dest="filename", required=True,
                        help="name of the file output")

    options = parser.parse_args(args[1:])

    shape = (options.X, options.Y)
    if shape[0] <= 0 or shape[1] <= 0:
        raise ValueError("X/Y must be > 0")

    spot = driver.reproduceTypedValue([1.0], options.spot)
    if not (0 <= spot[0] <= 1 and 0 <= spot[1] <= 1):
        raise ValueError("spot must be between 0 and 1")

    if options.anchor is None or options.drift is None:
        anchor = None
    else:
        anchor = driver.reproduceTypedValue([1.0], options.anchor)

    a = Acquirer()
    a.acquire_arcube(shape, spot, dperiod=options.drift, anchor=anchor,
                     filename=options.filename)

if __name__ == '__main__':
    try:
        main(sys.argv)
    except ValueError:
        logging.exception("Wrong value")
        ret = 127
    except Exception:
        logging.exception("Error while running the action")
        ret = 128
    else:
        ret = 0
    exit(ret)

