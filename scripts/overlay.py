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

import argparse
import logging
import math
import numpy
from odemis import model
from odemis.acq.align import coordinates, transform, spot
from odemis.acq.align.find_overlay import GridScanner
from odemis.dataio import hdf5
from odemis.util import img
import operator
from scipy import misc
import sys


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

    parser.add_argument("--repetitions_x", "-x", dest="repetitions_x", required=True,
                        help="repetitions defines the number of CL spots in the grid (x dimension)")
    parser.add_argument("--repetitions_y", "-y", dest="repetitions_y", required=True,
                        help="repetitions defines the number of CL spots in the grid (y dimension)")
    parser.add_argument("--dwell_time", "-t", dest="dwell_time", required=True,
                        help="dwell_time indicates the time to scan each spot (unit: s)")
    parser.add_argument("--max_allowed_diff", "-d", dest="max_allowed_diff", required=True,
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
    
        # ccd.data.get()
        gscanner = GridScanner(repetitions, dwell_time, escan, ccd, detector)

        # Wait for ScanGrid to finish
        optical_image, electron_coordinates, electron_scale = gscanner.DoAcquisition()
        hdf5.export("scanned_image.h5", optical_image)
        logging.debug("electron coord = %s", electron_coordinates)

        ############## TO BE REMOVED ON TESTING##############
#        grid_data = hdf5.read_data("scanned_image.h5")
#        C, T, Z, Y, X = grid_data[0].shape
#        grid_data[0].shape = Y, X
#        optical_image = grid_data[0]
        #####################################################
    
        logging.debug("Isolating spots...")
        opxs = optical_image.metadata[model.MD_PIXEL_SIZE]
        optical_dist = escan.pixelSize.value[0] * electron_scale[0] / opxs[0]
        subimages, subimage_coordinates = coordinates.DivideInNeighborhoods(optical_image, repetitions, optical_dist)
        logging.debug("Number of spots found: %d", len(subimages))

        hdf5.export("spot_found.h5", subimages,thumbnail=None)
        logging.debug("Finding spot centers...")
        spot_coordinates = spot.FindCenterCoordinates(subimages)
        logging.debug("center coord = %s", spot_coordinates)
        optical_coordinates = coordinates.ReconstructCoordinates(subimage_coordinates, spot_coordinates)
        logging.debug(optical_coordinates)
        rgb_optical = img.DataArray2RGB(optical_image)
        
        for ta in optical_coordinates:
            rgb_optical[ta[1] - 1:ta[1] + 1, ta[0] - 1:ta[0] + 1, 0] = 255
            rgb_optical[ta[1] - 1:ta[1] + 1, ta[0] - 1:ta[0] + 1, 1] *= 0.5
            rgb_optical[ta[1] - 1:ta[1] + 1, ta[0] - 1:ta[0] + 1, 2] *= 0.5
        
        misc.imsave('spots_image.png', rgb_optical)

        # TODO: Make function for scale calculation
        sorted_coordinates = sorted(optical_coordinates, key=lambda tup: tup[1])
        tab = tuple(map(operator.sub, sorted_coordinates[0], sorted_coordinates[1]))
        optical_scale = math.hypot(tab[0], tab[1])
        scale = electron_scale[0] / optical_scale
        print(scale)

        # max_allowed_diff in pixels
        max_allowed_diff_px = max_allowed_diff / escan.pixelSize.value[0]

        logging.debug("Matching coordinates...")
        known_electron_coordinates, known_optical_coordinates, max_diff = coordinates.MatchCoordinates(optical_coordinates, electron_coordinates, scale, max_allowed_diff_px)
    
        logging.debug("Calculating transformation...")
        (calc_translation_x, calc_translation_y), (calc_scaling_x, calc_scaling_y), calc_rotation = transform.CalculateTransform(known_electron_coordinates, known_optical_coordinates)
        logging.debug("Electron->Optical: ")
        print(calc_translation_x, calc_translation_y, calc_scaling_x, calc_scaling_y, calc_rotation)
        final_electron = coordinates._TransformCoordinates(known_optical_coordinates, (calc_translation_x, calc_translation_y), calc_rotation, (calc_scaling_x, calc_scaling_y))

        logging.debug("Overlay done.")
        
        # Calculate distance between the expected and found electron coordinates
        coord_diff = []
        for ta, tb in zip(final_electron, known_electron_coordinates):
            tab = tuple(map(operator.sub, ta, tb))
            coord_diff.append(math.hypot(tab[0], tab[1]))

        mean_difference = numpy.mean(coord_diff) * escan.pixelSize.value[0]

        variance_sum = 0
        for i in range(0, len(coord_diff)):
            variance_sum += (mean_difference - coord_diff[i]) ** 2
        variance = (variance_sum / len(coord_diff)) * escan.pixelSize.value[0]
        
        not_found_spots = len(electron_coordinates) - len(final_electron)

        # Generate overlay image
        logging.debug("Generating images...")
        (calc_translation_x, calc_translation_y), (calc_scaling_x, calc_scaling_y), calc_rotation = transform.CalculateTransform(known_optical_coordinates, known_electron_coordinates)
        logging.debug("Optical->Electron: ")
        print(calc_translation_x, calc_translation_y, calc_scaling_x, calc_scaling_y, calc_rotation)
        overlay_coordinates = coordinates._TransformCoordinates(known_electron_coordinates, (calc_translation_y, calc_translation_x), -calc_rotation, (calc_scaling_x, calc_scaling_y))

        for ta in overlay_coordinates:
            rgb_optical[ta[0] - 1:ta[0] + 1, ta[1] - 1:ta[1] + 1, 1] = 255
            
        misc.imsave('overlay_image.png', rgb_optical)
        misc.imsave('optical_image.png', optical_image)
        logging.debug("Done. Check electron_image.png, optical_image.png and overlay_image.png.")

    except:
        logging.exception("Unexpected error while performing action.")
        return 127

    logging.info("\n**Overlay precision stats (Resulted to expected electron coordinates comparison)**\n Mean distance: %f (unit: m)\n Variance: %f (unit: m)\n Not found spots: %d", mean_difference, variance, not_found_spots)
    return 0

if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)
