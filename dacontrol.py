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

from osgeo import gdal, gdal_array
import PIL.Image as Image
import andorcam3
import andorcam2
import argparse
import math
import os
import subprocess
import sys
import time

def get_version():
    if not os.path.isdir(".git"):
        # TODO should fallback to a VERSION file
        return "version unknown"
    
    try:
        p = subprocess.Popen(["git", "describe",
                              "--tags", "--dirty", "--always"],
                             stdout=subprocess.PIPE)
        return p.stdout.read().strip()
    except EnvironmentError:
        print "unable to run git"
        return "version unknown"

def run_self_test(camera):
    """
    Run self test for the given camera
    camera (andorcam2 or andorcam3): device
    return (boolean) True if all the tests passed, False otherwise
    """
    cam_metadata = camera.getCameraMetadata()
    print "Testing device: %s" % cam_metadata["Camera name"]
    return camera.selfTest()

def scan():
    # Warning: apparently it's pretty impossible to have both sdk v2 and v3 open
    # in the same process.
    cameras = andorcam3.AndorCam3.scan()
    for i, name, res in sorted(cameras):
        print "%d: %s (%dx%d)" % (i + 30, name, res[0], res[1])
    
    # It tends to crash more if called before andorcam3
    cameras = andorcam2.AndorCam2.scan()
    for i, name, res in sorted(cameras):
        print "%d: %s (%dx%d)" % (i + 20, name, res[0], res[1])
            
def saveAsTiff(filename, array, metadata={}):
    """
    Saves an array as a TIFF file.
    filename (string): name of the file to save
    array (ndarray): 2D array of int or float
    metadata (dict: string->values): metadata to save (only some values
       are supported) 
    """
    saveAsTiffGDAL(filename, array, metadata)
    # TODO： use tifffile.py instead of gdal?
    # http://www.lfd.uci.edu/~gohlke/code/tifffile.py.html
    
    
# Conversion from our internal tagname convention to (gdal) TIFF tagname
# string -> (string, callable)
DATagToTiffTag = {"Software name": ("TIFFTAG_SOFTWARE", str),
                  "Camera name": ("TIFFTAG_HOSTCOMPUTER", str),
                  "Acquisition date": ("TIFFTAG_DATETIME", lambda x: time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime(x)))
                  }
#TIFFTAG_DOCUMENTNAME
#TIFFTAG_IMAGEDESCRIPTION
#TIFFTAG_ARTIST
#TIFFTAG_COPYRIGHT
#TIFFTAG_XRESOLUTION
#TIFFTAG_YRESOLUTION
#TIFFTAG_RESOLUTIONUNIT
# TODO how to put our own tags?
def saveAsTiffGDAL(filename, array, metadata={}):
    """
    Saves an array as a TIFF file.
    filename (string): name of the file to save
    array (ndarray): 2D array of int or float
    metadata (dict: string->values): metadata to save (only some values
       are supported) 
    """

    driver = gdal.GetDriverByName("GTiff")
    
    # gdal expects the array to be in 'F' order, but it's in 'C'
    array.shape = (array.shape[1], array.shape[0])
    ds = gdal_array.OpenArray(array)
    array.shape = (array.shape[1], array.shape[0])
    for key, val in metadata.items():
        if key in DATagToTiffTag:
            ds.SetMetadataItem(DATagToTiffTag[key][0], DATagToTiffTag[key][1](val))
    driver.CreateCopy(filename, ds) # options=["COMPRESS=LZW"]

def saveAsTiffPIL(filename, array, metadata={}):
    """
    Saves an array as a TIFF file.
    filename (string): name of the file to save
    array (ndarray): 2D array of int or float
    metadata (dict: string->values): metadata to save (only some values
       are supported) 
    """
    # Two memory copies for one conversion! because of the stride, fromarray() does as bad
    pil_im = Image.fromarray(array)
    #pil_im = Image.fromstring('I', size, array.tostring(), 'raw', 'I;16', 0, -1)
    # 16bits files are converted to 32 bit float TIFF with PIL
    pil_im.save(filename, "TIFF") 



def main(args):
    """
    Contains the console handling code for the AndorCam3 class
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """
    #print args
    # arguments handling 
    parser = argparse.ArgumentParser(description="Delmic Acquisition Software for Andor Cameras")

    parser.add_argument('--version', action='version', version="%(prog)s " + get_version())
    parser.add_argument('--list', '-l', dest="list", action="store_true", default=False,
                        help="list all the available cameras.")
    parser.add_argument("--device", dest="device", type=int,
                        help="number of the device. (see --list for possible values)")
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
    cmd_grp.add_argument("--multi", "-m", dest="multi", type=int,
                        help="Records several images in a row, number of images or 0 to record forever. Images files are named by time. To be used instead of --output.")

    options = parser.parse_args(args[1:])
    
    # List mode
    if options.list:
        scan()
        return 0
    
    if options.device is None:
        parser.error("Device number must be specified")
        
    try:
        if (20 <= options.device and options.device <= 29):
            camera = andorcam2.AndorCam2(options.device - 20)
        elif (30 <= options.device and options.device <= 39):
            camera = andorcam3.AndorCam3(options.device - 30)
        else:
            parser.error("Device %d unknown." % options.device)
    except Exception, err:
        print "Error while connecting to the camera: " + str(err)
        return 128
    
    # Test mode
    if options.test:
        if run_self_test(camera):
            print "Test passed."
            return 0
        else:
            print "Test failed."
            return 127

    if options.width is None or options.height is None or options.exposure is None:
        parser.error("you need to specify the width, height and exposure time.")

    if options.multi is None:
        if not options.output_filename:
            parser.error("name of the output file must be specified")
            
        # acquire an image
        size = (options.width, options.height)
        im, metadata = camera.acquire(size, options.exposure, options.binning)
        metadata["Software name"] = "Delmic Acquisition Software %s" % get_version()
        
        saveAsTiff(options.output_filename, im, metadata)
    else:
        if options.output_filename:
            parser.error("--output conflicts with --multi: name of the output files is automatic.")
            
        # record serveral images
        size = (options.width, options.height)
        
        if options.multi == 0:
            camera.acquireFlow(record_image, size, options.exposure, 
                               options.binning)
            raw_input("Press Enter to stop recording...")
            camera.stopAcquireFlow(sync=True)
        else:
            camera.acquireFlow(record_image, size, options.exposure, 
                               options.binning, options.multi)
            camera.waitAcquireFlow()
            
    return 0

def record_image(camera, im, metadata):
    """
    Records an image to the current directory with the filename from its date.
    """
    time_subsec, time_sec = math.modf(metadata["Acquisition date"])
    # there is no way to put subseconds in strftime directly
    str_msec = "%0.3d" % int(round(time_subsec * 1e3))
    filename = time.strftime("%Y%m%d-%H:%M:%S." + str_msec + ".tiff",
                             time.localtime(time_sec))
    print "Saving %s" % filename 
    saveAsTiff(filename, im, metadata)
    

if __name__ == '__main__':
    exit(main(sys.argv))

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: