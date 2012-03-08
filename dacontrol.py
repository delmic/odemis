#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 6 mar 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Delmic Acquisition Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''

import andorcam
import argparse
import sys
from PIL import Image

def run_self_test(device):
    """
    Run self test on each detect controller of the network connected to the given
    serial port.
    port (string): name of the serial port
    return (boolean) True if all the tests passed, False otherwise
    """
    
    passed = True
    
    return passed

def scan():
    cameras = andorcam.AndorCam.scan()
    for i, name, res in sorted(cameras):
        print "%d: %s (%dx%d)" % (i, name, res[0], res[1]) 

def main(args):
    """
    Contains the console handling code for the AndorCam3 class
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """
    #print args
    # arguments handling 
    parser = argparse.ArgumentParser(description="Delmic Acquisition Software for Andor Cameras")

    parser.add_argument('--version', action='version', version='%(prog)s 0.1')
    parser.add_argument('--list', '-l', dest="list", action="store_true", default=False,
                        help="list all the available cameras.")
    parser.add_argument("--device", dest="device",
                        help="name of the device. (see --list for possible values)")
    cmd_grp = parser.add_argument_group('Camera commands')
    parser.add_argument("--test", "-t", dest="test", action="store_true", default=False,
                        help="test the connection to the camera.")
    cmd_grp.add_argument("--width", dest="width", type=int,
                        help="Width of the picture to acquire (in pixel).")
    cmd_grp.add_argument("--height", dest="height", type=int,
                        help="Height of the picture to acquire (in pixel).")
    cmd_grp.add_argument("--exp", "-e",  dest="exposure", type=float,
                        help="Exposure time (in second).")
    cmd_grp.add_argument("--binning", "-b", dest="binning", type=int, default=1, # TODO 1 2 3 4 or 8 only
                        help="Number of pixels to bin together when acquiring the picture. (Default is 1)")
    cmd_grp.add_argument("--output", "-o", dest="output_filename",
                        help="name of the file where the image should be saved. It is saved in TIFF format.")

    options = parser.parse_args(args[1:])
  
    # Test mode
    if options.test:
        if run_self_test(options.device):
            print "Test passed."
            return 0
        else:
            print "Test failed."
            return 127

    # List mode
    if options.list:
        scan()
        return 0
    
    if options.width is None or options.height is None or options.exposure is None:
        parser.error("you need to specify the width, height and exposure time.")
    if not options.output_filename:
        parser.error("name of the output file must be specified")
    
#    try:
    camera = andorcam.AndorCam(options.device)
#    except Exception, err:
#        print "Error while connecting to the camera: " + str(err)
#        return 128
#    
    # acquire an image
    size = (options.width, options.height)
    im = camera.acquire(size, options.exposure, options.binning)
    
    # Two memory copies for one conversion! because of the stride, fromarray() does as bad
#    im = Image.fromstring('I', size, array.tostring(), 'raw', 'I;16', stride, -1)
    #im = Image.frombuffer('I', size, cbuffers[curbuf], 'raw', 'I;16', stride, -1)
    #pil_im = Image.fromarray(im)
    pil_im = Image.fromstring('I', size, im.tostring(), 'raw', 'I;16', 0, -1)
    pil_im = pil_im.convert("L") # 16bits TIFF are not well supported!
    pil_im.save(options.output_filename, "TIFF") 
    
    return 0
        
if __name__ == '__main__':
    exit(main(sys.argv))

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: