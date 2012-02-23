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
    parser.add_argument("--port", dest="port",
                        help="name of the serial port. (ex: '/dev/ttyUSB0' on Linux, 'COM3' on Windows)")
    cmd_grp = parser.add_argument_group('Microscope commands')
    cmd_grp.add_argument("--stage-x", "-x", dest="stage_x", type=float, metavar="X",
                        help=u"move the X axis of the stage to position X (in µm). Default is to not move the stage.") # unicode for µ
    cmd_grp.add_argument("--stage-y", "-y", dest="stage_y", type=float, metavar="Y",
                        help=u"move the Y axis of the stage to position Y (in µm). Default is to not move the stage.")
    cmd_grp.add_argument("--scan-size", "-s", dest="scan_size", type=float,
                        help=u"Set the scanning field size (in µm). Default is to use the current size.")
    cmd_grp.add_argument("--output", "-o", dest="output_filename",
                        help="name of the file where the image should be saved. It is saved in TIFF format.")
    mode_grp = parser.add_mutually_exclusive_group()
    mode_grp.add_argument("--test", "-t", dest="test", action="store_true", default=False,
                        help="test the connection to the motor controllers.")
    mode_grp.add_argument("--sync", dest="is_sync", action="store_true", default=False,
                        help="waits until all the moves are complete before exiting.")
    options = parser.parse_args(args[1:])

    # we need a port
    

    # Test mode
    if options.test:
        ser = PIRedStone.openSerialPort(port)
        if PIC180.self_test():
            print "test passed."
            return 0
        else:
            print "test failed."
            return 127
    

    stage = pi.StageSECOM(options.port)
        except Exception, err:
            print "Error while connecting to the motor controllers: " + str(err)
            return 128
    
    # Aquisition mode (remote or local is the same)
    positions = {}
    if options.stage_x:
        positions['x'] = options.stage_x
    if options.stage_y:
        positions['y'] = options.stage_y
    stage.move_stage(positions, confirm=True) # put confirm = False only if you are really sure
    

    return 0
        
if __name__ == '__main__':
    exit(main(sys.argv))

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: