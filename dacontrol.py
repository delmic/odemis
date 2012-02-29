#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 26 jan 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Delmic Acquisition Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''

import sys
import argparse
import pi

def run_self_test(port):
    """
    Run self test on each detect controller of the network connected to the given
    serial port.
    port (string): name of the serial port
    return (boolean) True if all the tests passed, False otherwise
    """
    ser = pi.PIRedStone.openSerialPort(port)
    bus = pi.PIRedStone(ser)
    adds = bus.scanNetwork(2)
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
    Contains the console handling code for the Quanta class
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """
    #print args
    # arguments handling 
    parser = argparse.ArgumentParser(description="Delmic Acquisition Software for Quanta SEM")
    #parser = OptionParser(version="%prog 0.1")

    parser.add_argument('--version', action='version', version='%(prog)s 0.1')   
    parser.add_argument("--port", dest="port", required=True,
                        help="name of the serial port. (ex: '/dev/ttyUSB0' on Linux, 'COM3' on Windows)")
    cmd_grp = parser.add_argument_group('Stage commands')
    parser.add_argument("--test", "-t", dest="test", action="store_true", default=False,
                        help="test the connection to the motor controllers.")
    cmd_grp.add_argument("--stage-x", "-x", dest="stage_x", type=float, metavar="X",
                        help=u"move the X axis of the stage by X µm. Default is to not move the stage.") # unicode for µ
    cmd_grp.add_argument("--stage-y", "-y", dest="stage_y", type=float, metavar="Y",
                        help=u"move the Y axis of the stage by Y µm. Default is to not move the stage.")
    cmd_grp.add_argument("--sync", dest="sync", action="store_true", default=False,
                        help="waits until all the moves are complete before exiting.")
    cmd_grp.add_argument("--stop", "-s", dest="stop", action="store_true", default=False,
                        help="Immediately stop the stage in all directions. The other commands are not executed.")

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
        stage = pi.StageSECOM(options.port)
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