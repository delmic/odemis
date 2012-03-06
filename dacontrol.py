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

import sys
import argparse

def run_self_test(port):
    """
    Run self test on each detect controller of the network connected to the given
    serial port.
    port (string): name of the serial port
    return (boolean) True if all the tests passed, False otherwise
    """
    ser = pi.PIRedStone.openSerialPort(port)
    bus = pi.PIRedStone(ser)
    adds = bus.scanNetwork()
    if not adds:
        print "No controller found."
        return False
    
    passed = True
    for add in adds:
        cont = pi.PIRedStone(ser, add)
        if cont.selfTest():
            print "Controller %d: test passed." % add
            passed = passed and True
        else:
            print "Controller %d: test failed." % add
            passed = False
    
    return passed

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
    cmd_grp.add_argument("--binning", "-b", dest="binning", type=int,
                        help="Number of pixels to bin together when acquiring the picture. (Defaut is 1)")
    cmd_grp.add_argument("--output", "-o", dest="output_filename",
                        help="name of the file where the image should be saved. It is saved in TIFF format.")

    options = parser.parse_args(args[1:])
  

    # Test mode
    if options.test:
        if run_self_test(options.port):
            print "Test passed."
            return 0
        else:
            print "Test failed."
            return 127

    try:
        stage = pi.StageRedStone(options.port, CONFIG_RS_SECOM_2)
    except Exception, err:
        print "Error while connecting to the motor controllers: " + str(err)
        return 128
    
    if options.stop:
        stage.stopMotion()
        return 0
    
    # move
    positions = {}
    if options.stage_x:
        positions['x'] = options.stage_x * 1e-6 # µm -> m
    if options.stage_y:
        positions['y'] = options.stage_y * 1e-6 # µm -> m
    stage.moveRel(positions, options.sync)

    return 0
        
if __name__ == '__main__':
    exit(main(sys.argv))

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: