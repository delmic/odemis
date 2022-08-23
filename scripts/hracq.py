#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 9 Jan 2015

@author: Éric Piel

Copyright © 2015-2017 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

# This script collects multiple fluorescence images at high frame rate in order
# to provide input for high-resolution reconstruction algorithm.

import numbers
import queue
import argparse
import logging
from odemis import dataio, model
from odemis.gui.util import get_picture_folder
from odemis.util import fluo

import os
import sys
import threading
import time


logging.getLogger().setLevel(logging.INFO)


class HRAcquirer(object):

    def __init__(self, fn, number):
        self.number = number

        # get the components
        self.light = model.getComponent(role="light")
        self.ccd = model.getComponent(role="ccd")

        # TODO: only support TIFF
        # prepare the data export
        self.exporter = dataio.find_fittest_converter(fn)

        # Make the name "fn" -> "~/Pictures/fn-XXXXXX.ext"
        path, base = os.path.split(fn)
        bn, ext = os.path.splitext(base)
        tmpl = os.path.join(path, bn + "-%06d" + ext)
        if path.startswith("/"):
            # if fn starts with / => don't add ~/Pictures
            self.fntmpl = tmpl
        else:
            self.fntmpl = os.path.join(get_picture_folder(), tmpl)

        self._acq_done = threading.Event()
        self._n = 0
        self._startt = 0  # starting time of acquisition

        self._q = queue.Queue()  # queue of tuples (str, DataArray) for saving data
        # TODO: find the right number of threads, based on CPU numbers (but with
        # python threading that might be a bit overkill)
        for i in range(4):
            t = threading.Thread(target=self._saving_thread, args=(i,))
            t.daemon = True
            t.start()

    def _saving_thread(self, i):
        try:
            while True:
                n, da = self._q.get()
                logging.info("Saving data %d in thread %d", n, i)
                self.save_data(da, n)
                self._q.task_done()
        except Exception:
            logging.exception("Failure in the saving thread")

    def set_hardware_settings(self, wavelength, power=None):
        """
        Setup the hardware to the defined settings
        """
        if power is None:
            power = self.light.power.range[-1]
        elif isinstance(power, numbers.Number):
            # Convert the power commandline argument float value into a list (to match light power type)
            power = [power]
        self.power = power

        # find the fitting wavelength for the light
        spectra = self.light.spectra.value
        band = fluo.find_best_band_for_dye(wavelength, spectra)
        wli = spectra.index(band)
        self._full_intensity = [0] * len(spectra)
        self._full_intensity[wli] = 1

        # Special CCD settings
        self.ccd.countConvert.value = 2  # photons
        self.ccd.countConvertWavelength.value = wavelength

    def _on_continuous_image(self, df, data):

        try:
            self._n += 1
            self._q.put((self._n, data))
            fps = self._n / (time.time() - self._startt)
            logging.info("Saved data %d (%g fps), queue size = %d", self._n, fps, self._q.qsize())

            # TODO: if queue size too long => pause until it's all processed

            if self._n == self.number:
                self.ccd.data.unsubscribe(self._on_continuous_image)
                self._acq_done.set()  # indicate it's over
        except Exception:
            logging.exception("Failure to save acquisition %d", self._n)
            # Stop the rest of the acquisition
            self.ccd.data.unsubscribe(self._on_continuous_image)
            self._acq_done.set()  # indicate it's over

    def acquire(self):
        """
        Run the acquisition with the maximum frame rate
        """

        # TODO: support cancellation

        # Switch on laser (at the right wavelength and power)
        self.light.power.value = [ints * pw for ints,pw in zip(self._full_intensity, self.power)]
        self._n = 0
        self._startt = time.time()
        self.ccd.data.subscribe(self._on_continuous_image)

        # Wait for the complete acquisition to be done
        self._acq_done.wait() # TODO: put large timeout
        logging.info("Waiting for all data to be saved")
        self._q.join()
        fps = self.number / (time.time() - self._startt)
        logging.info("Finished with average %g fps", fps)

        # Switch off laser
        self.light.power.value = self.light.power.range[0]

    def save_data(self, data, n):
        """
        Save the data under the right file name and format
        data (DataArray): data to save
        n (int): iteration number
        """
        filename = self.fntmpl % (n,)
        try:
            # Note: compressed seems to written faster
            self.exporter.export(filename, data, compressed=True)
        except IOError as exc:
            raise IOError(u"Failed to save to '%s': %s" % (filename, exc))


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """
    # arguments handling
    parser = argparse.ArgumentParser()

    parser.add_argument("--wavelength", dest="wavelength", type=float,
                        required=True,
                        help="Centre wavelength (in nm) of the excitation light to use.")
    parser.add_argument("--power", dest="power", type=float,
                        help="Excitation light power (in W), default is maximum.")
    parser.add_argument("--number", dest="number", type=int, required=True,
                        help="Number of frames to grab.")
    # TODO: specify the folder name where to save the file
    parser.add_argument("--output", "-o", dest="output",
                        help="template of the filename under which to save the images. "
                        "The file format is derived from the extension "
                        "(TIFF and HDF5 are supported).")

    options = parser.parse_args(args[1:])

    try:
        acquirer = HRAcquirer(options.output, options.number)
        acquirer.set_hardware_settings(options.wavelength * 1e-9, options.power)
        acquirer.acquire()
    except ValueError as exp:
        logging.error("%s", exp)
        return 127
    except IOError as exp:
        logging.error("%s", exp)
        return 129
    except Exception:
        logging.exception("Unexpected error while performing action.")
        return 130

    return 0

if __name__ == '__main__':
    ret = main(sys.argv)
    exit(ret)
