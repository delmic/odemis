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
from odemis import dataio
import odemis
import sys


class HRAcquirer(object):
    
    def __init__(self, fn, number, exposure, power, wavelength):

        self.exporter = dataio.find_fittest_exporter(fn)
        
        # TODO insert ~/Pictures + fn + fn-XXXX.ext
        self.fntmpl =  

    def set_hardware_settings(self):
        """
        Setup the hardware to the defined settings
        """
        pass
    
    def _on_continuous_image(self, df, data):
        
        # TODO: export in a separate thread to save time? (put to a queue)
        self._n += 1
        self.save_data(data, self._n)
        
        if self._n == self.number:
            self.ccd.data.unsubscribe(self._on_continuous_image)
            self._acq_done.set()
        
        
    def run_acquisition_continuous(self):
        """
        Run the acquisition with the maximum frame rate
        """
        
#       Switch on [lasersource] at [laserpower]
        self._n = 0
        self.ccd.data.subscribe(self._on_continuous_image)
        
        # Wait for event done
        self._acq_done.wait() # TODO: put large timeout
        
#       switch off laser
    
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
        
        """
        # TODO: disable compression to save time

        filename = self.fntmpl
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

    # anything to do?
    if options.setattr:
        for l in options.setattr:
            if len(l) < 3 or (len(l) - 1) % 2 == 1:
                logging.error("--set-attr expects component name and then a even number of arguments")
    if options.upmd:
        for l in options.upmd:
            if len(l) < 3 or (len(l) - 1) % 2 == 1:
                logging.error("--update-metadata expects component name and then a even number of arguments")

    logging.debug("Trying to find the backend")
    status = get_backend_status()
    if options.check:
        logging.info("Status of back-end is %s", status)
        return status_to_xtcode[status]

    try:
        # check if there is already a backend running
        if status == BACKEND_STOPPED:
            raise IOError("No running back-end")
        elif status == BACKEND_DEAD:
            raise IOError("Back-end appears to be non-responsive.")

        if options.setattr is not None:
            for l in options.setattr:
                # C A B E F => C, {A: B, E: F}
                c = l[0]
                avs = dict(zip(l[1::2], l[2::2]))
                set_attr(c, avs)
        elif options.upmd is not None:
            for l in options.upmd:
                c = l[0]
                kvs = dict(zip(l[1::2], l[2::2]))
                update_metadata(c, kvs)
        # TODO: catch keyboard interrupt and stop the moves
        elif options.reference is not None:
            for c, a in options.reference:
                reference(c, a)
        elif options.position is not None:
            for c, a, d in options.position:
                move_abs(c, a, d)
#             time.sleep(0.5)
        elif options.move is not None:
            # TODO warn if same axis multiple times
            # TODO move commands to the same actuator should be agglomerated
            for c, a, d in options.move:
                move(c, a, d)
#             time.sleep(0.5) # wait a bit for the futures to close nicely
        elif options.stop:
            stop_move()
        elif options.acquire is not None:
            component = options.acquire[0]
            if len(options.acquire) == 1:
                dataflows = ["data"]
            else:
                dataflows = options.acquire[1:]
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
