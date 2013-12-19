# -*- coding: utf-8 -*-
"""
Created on 17 Dec 2013

@author: Kimon Tsitsikas

Copyright © 2012-2013 Kimon Tsitsikas, Delmic

This is a script to test the functionalities included to “FineOverlay” i.e. 
ScanGrid, DivideInNeighborhoods, FindCenterCoordinates, ReconstructImage, 
MatchCoordinates, CalculateTransform. The user gives arguments regarding the 
grid scanned for the overlay and receives output regarding the precision of 
the achieved overlay (in case an overlay cannot be found for the given parameters,
a warning message is shown). The script first finds the components needed for 
scanning and capturing the grid i.e. e-beam scanner, se-detector and CCD. Then, 
similarly to “FineOverlay”, it links the different functions feeding the output 
of the one to the input of the other.

run as:
python overlay.py --repetitions_x 9 --repetitions_y 9 --dwell_time 1e-06 --max_allowed_diff 1e-07

--repetitions defines the number of CL spots in the grid.
--dwell_time indicates the time to scan each spot. #s
--max_allowed_diff indicates the maximum allowed difference in electron coordinates. #m

You first need to run the odemis backend with the SECOM config:
odemisd --log-level 2 install/linux/usr/share/odemis/secom-tud.odm.yaml
"""

from __future__ import division

import logging
import numpy
from odemis import model
from odemis.dataio import hdf5
from odemis.model._dataflow import MD_PIXEL_SIZE, MD_POS
from odemis.acq.align import images, coordinates, transform
import sys
import threading
import time
import operator
import argparse
import math

logging.getLogger().setLevel(logging.DEBUG)

def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser(description=
                     "Automated AR acquisition at multiple spot locations")

    parser.add_argument("--repetitions_x", "-rx", dest="repetitions_x", required=True,
                        help="repetitions defines the number of CL spots in the grid (x dimension)")
    parser.add_argument("--repetitions_y", "-ry", dest="repetitions_y", required=True,
                        help="repetitions defines the number of CL spots in the grid (y dimension)")
    parser.add_argument("--dwell_time", "-dt", dest="dwell_time", required=True,
                        help="dwell_time indicates the time to scan each spot (unit: s)")
    parser.add_argument("--max_allowed_diff", "-md", dest="max_allowed_diff", required=True,
                        help="max_allowed_diff indicates the maximum allowed difference in electron coordinates (unit: m)")

    options = parser.parse_args(args[1:])
    repetitions = (int(options.repetitions_x), int(options.repetitions_y))
    dwell_time = float(options.dwell_time)
    max_allowed_diff = float(options.max_allowed_diff)

    try:
        escan = None
        detector = None
        ccd = None
        # find components by their role
        for c in model.getComponents():
            if c.role == "e-beam":
                escan = c
            elif c.role == "se-detector":
                detector = c
            elif c.role == "ccd":
                ccd = c
        if not all([escan, detector, ccd]):
            logging.error("Failed to find all the components")
            raise KeyError("Not all components found")
    
        optical_image, electron_coordinates, electron_scale = images.ScanGrid(repetitions, dwell_time, escan, ccd, detector)

        ############## TO BE REMOVED ON TESTING##############
        grid_data = hdf5.read_data("real_optical.h5")
        C, T, Z, Y, X = grid_data[0].shape
        grid_data[0].shape = Y, X
        optical_image = grid_data[0]
        #####################################################
    
        subimages, subimage_coordinates, subimage_size = coordinates.DivideInNeighborhoods(optical_image, repetitions)
        spot_coordinates = coordinates.FindCenterCoordinates(subimages)
        optical_coordinates = coordinates.ReconstructImage(subimage_coordinates, spot_coordinates, subimage_size)
    
        # TODO: Make function for scale calculation
        sorted_coordinates = sorted(optical_coordinates, key=lambda tup: tup[1])
        optical_scale = sorted_coordinates[0][0] - sorted_coordinates[1][0]
        scale = electron_scale[0] / optical_scale
    
        # max_allowed_diff in pixels
        max_allowed_diff_px = max_allowed_diff / escan.pixelSize.value[0]

        known_estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(optical_coordinates, electron_coordinates, scale, max_allowed_diff_px)
    
        (calc_translation_x, calc_translation_y), calc_scaling, calc_rotation = transform.CalculateTransform(known_estimated_coordinates, known_optical_coordinates)
        final_electron = coordinates._TransformCoordinates(known_optical_coordinates, (calc_translation_x, calc_translation_y), calc_rotation, calc_scaling)

        # Calculate distance between the expected and found electron coordinates
        coord_diff = []
        for ta, tb in zip(final_electron, known_estimated_coordinates):
            tab = tuple(map(operator.sub, ta, tb))
            coord_diff.append(math.hypot(tab[0], tab[1]))

        mean_difference = numpy.mean(coord_diff) * escan.pixelSize.value[0]

        variance_sum = 0
        for i in range(0, len(coord_diff)):
            variance_sum += (mean_difference - coord_diff[i]) ** 2
        variance = (variance_sum / len(coord_diff)) * escan.pixelSize.value[0]
        
        not_found_spots = len(electron_coordinates) - len(final_electron)

    except:
        logging.exception("Unexpected error while performing action.")
        return 127

    logging.info("\n**Overlay precision stats (Resulted to expected electron coordinates comparison)**\n Mean distance: %f (unit: m)\n Variance: %f (unit: m)\n Not found spots: %d", mean_difference, variance, not_found_spots)
    return 0

if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)
