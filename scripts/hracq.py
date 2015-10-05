#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 9 Jan 2015

@author: Éric Piel

Copyright © 2015 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

# This script collects multiple fluorescence images at high frame rate in order
# to provide input for high-resolution reconstruction algorithm.


import argparse
import logging
from odemis import dataio, model
import odemis
from odemis.gui.util import get_picture_folder
import os
import sys


class HRAcquirer(object):
    
    def __init__(self, fn, number):
        self.number = number

        # get the components
        self.light = model.getComponent(role="light")
        self.ccd = model.getComponent(role="ccd")

        # prepare the data export
        self.exporter = dataio.find_fittest_converter(fn)
        
        # Make the name "fn" -> "~/Pictures + fn + fn-XXXX.ext"
        path, base = os.path.split(fn)
        bn, ext = os.path.splitext(base)
        tmpl = os.path.join(path, bn, bn + "-%05d." + ext)
        if path.startswith("/"):
            # if fn starts with / => don't add ~/Pictures
            self.fntmpl = tmpl
        else:
            self.fntmpl = os.path.join(get_picture_folder(), tmpl)


        self._n = 0

    def set_hardware_settings(self, exposure, binning, power, wavelength):
        """
        Setup the hardware to the defined settings
        """
        self.exposure = exposure
        self.power = power
        self.binning = binning

        # find the fitting wavelength for the light
        spectra = self.light.spectra.value
        for i, s in enumerate(spectra):
            # each spectrum contains 5 values, with center wl as 3rd value
            if abs(s[2] - wavelength * 1e-9) < 2e-9:
                wli = i

        self._off_intensity = [0] * len(spectra)
        self._full_intensity = list(self._off_intensity) # copy
        self._full_intensity[wli] = 1
    
    def _on_continuous_image(self, df, data):
        
        # TODO: export in a separate thread to save time? (put to a queue)
        self._n += 1
        self.save_data(data, self._n)
        
        if self._n == self.number:
            self.ccd.data.unsubscribe(self._on_continuous_image)
            self._acq_done.set() # indicate it's over

    def run_acquisition_continuous(self):
        """
        Run the acquisition with the maximum frame rate
        """
        
#       Switch on [lasersource] at [laserpower]
        self.light.intensity.value = self._full_intensity
        self.light.power.value = self.power
        self._n = 0
        self.ccd.data.subscribe(self._on_continuous_image)
        
        # Wait for event done
        self._acq_done.wait() # TODO: put large timeout
        
#       switch off laser
        self.light.power.value = 0
    
    def run_acquisition_discreet(self):
        """
        Run acquisition with one frame at the time synchronised with excitation
        light.
        """
        
        # Prepare acquisition
        
        
        for n in self.number:
#           Switch on [lasersource] at [laserpower]
#           expose with exposure time
#           switch off laser
#           wait idle time for grab frame
            pass
        

    def save_data(self, data, n):
        """
        Save the data under the right file name and format
        data (DataArray): data to save
        n (int): iteration number
        """
        # TODO: disable compression to save time

        filename = self.fntmpl % (n,)
        try:
            self.exporter.export(filename, data)
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

    parser.add_argument('--version', dest="version", action='store_true',
                        help="show program's version number and exit")
    parser.add_argument("--wavelength", dest="wavelength", type=float,
                        help="Centre wavelength (in nm) of the excitation light to use.")
    parser.add_argument("--power", dest="power", type=float,
                        help="Excitation light power (in W), default is maximum.")
    parser.add_argument("--exposure", dest="exposure", type=float,
                        help="Exposure time for each frame.")
    parser.add_argument("--binning", dest="binning", type=int, default=1,
                        help="Binning of the CCD (default is 1).")
    parser.add_argument("--number", dest="number",
                        help="Number of frames to grab.")
    parser.add_argument('--continuous', dest="continuous", action='store_true',
                         help="Acquire in continuous mode instead of discreet mode. "
                         "It is is faster but might introduce frame-drops.")
    # TODO: specify the folder name where to save the file
    parser.add_argument("--output", "-o", dest="output",
                        help="template of the filename under which to save the images. "
                        "The file format is derived from the extension "
                        "(TIFF and HDF5 are supported).")

    options = parser.parse_args(args[1:])

    # Cannot use the internal feature, because it doesn't support multiline
    if options.version:
        print (odemis.__fullname__ + " " + odemis.__version__ + "\n" +
               odemis.__copyright__ + "\n" +
               "Licensed under the " + odemis.__license__)
        return 0

    # TODO: if arguments not present, ask for them on the console

    try:
        acquirer = HRAcquirer(options.output, options.number)
        acquirer.set_hardware_settings(options.exposure, options.binning,
                                       options.power, options.wavelength)
        filename = options.output.decode(sys.getfilesystemencoding())
        acquire(component, dataflows, filename)
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
