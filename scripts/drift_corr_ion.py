"""
Created on 25th October 2022

@author: Karishma Kumar

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License
version 2 as published by the Free Software Foundation. Odemis is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See the GNU General Public License for more details. You should have received a copy of the GNU General Public License
along with Odemis. If not, see http://www.gnu.org/licenses/.

This is a script to test the FIB drift correction for MIMAS. The anchor region is chosen for drift estimation after
certain time delay for e.g. 1 minute and the beam is shifted to correct for the drift after estimation.

run as:
python3 scripts/drift_corr_ion.py --roi [roi]  [Image folder name] --dwell [dwell_time] --nb
 [nb_images] --delay [delay_time]
e.g. python3 scripts/drift_corr_ion.py --roi 0,0.3,0.4,0.7 /home/dev/Pictures/drift_corrector/image__.tiff
 --dwell 10e-06 --nb 10 --delay 5

[Image folder name] provides the path to store the image and also the filename to be used while saving.
 (numbers will be appended to the filename for subsequent images)
[dwell_time] scanning duration per point in seconds
[nb_images] total number of images acquired for drift correction
[delay_time]

e.g. save the output for debugging using hardware
python3 scripts/drift_corr_ion.py --roi 0,0.3,0.4,0.7 /home/dev/Pictures/drift_corrector/image__.tiff
 --dwell 10e-06 --nb 10 --delay 5 |& tee -a  drift_test.log
grep "INFO" drift_test.log>drift_info.log

You first need to run the odemis backend with the FIB config:
For simulation:-
odemis-start /home/[USERNAME]/development/odemis/install/linux/usr/share/odemis/sim/fib-sim.odm.yaml

"""

import argparse
import logging
import math
import os
import sys
import time
from pathlib import Path

from odemis import model, dataio
from odemis.acq import drift
from odemis.acq.drift import MAX_PIXELS
from odemis.util import conversion

logging.getLogger().setLevel(logging.DEBUG)


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """
    parser = argparse.ArgumentParser(description="drift correction for MIMAS")
    parser.add_argument("--roi", dest="roi", required=True,
                        help="e-beam ROI positions (ltrb, relative to the SEM "
                             "field of view)")
    parser.add_argument("--dwell", dest="dwell_time", type=float,
                        default=10e-06, help="(s) FIB stays for this much duration while scanning")
    parser.add_argument("--nb", dest="nb_images", type=int,
                        default=10, help="total images acquired for drift correction")
    parser.add_argument("--delay", dest="delay_time", type=int,
                        default=5, help="(s) time duration between the acquisition of "
                                        "consecutive images")
    parser.add_argument(dest="base",
                        help="filename of the image to be stored")

    options = parser.parse_args(args[1:])
    escan = None
    detector = None

    try:
        # find components by their role
        for c in model.getComponents():
            if c.role == "e-beam":
                escan = c
            elif c.role == "se-detector":
                detector = c

        if not all([escan, detector]):
            logging.error("Failed to find all the components")
            raise KeyError("Not all components found")

        # Make the blanker automatic (ie, disabled when acquiring)
        escan.blanker.value = None

        # Calculate row and column drift after drift correction
        row_drift, col_drift = calculate_drift(escan, detector, options)

        escan.blanker.value = True
        logging.info("col drift is : %s", col_drift)
        logging.info("row drift is : %s", row_drift)

    except:
        # Make sure the blanker is activated if there is an exception
        if escan is not None:
            escan.blanker.value = True
        logging.exception("Unexpected error while performing action.")
        return 127


def calculate_drift(escan, detector, options):
    """
    Calculates drift on the acquired images at the given roi. The acquisition is corrected from drift
     by shifting the FIB by the estimated measure of drift/

    :param escan: scanner driver
    :param detector: detector driver
    :param options: class containing user inputs
    :return: returns the measured drift in vertical and horizontal directions
    """
    roi = conversion.reproduce_typed_value([1.0], options.roi)
    if not all(0 <= r <= 1 for r in roi):
        raise ValueError("roi values must be between 0 and 1")

    p = Path(options.base)
    nb_images = options.nb_images
    delay_time = options.delay_time
    dwell_time = options.dwell_time  # s

    logging.info('Dwell time(s): %s', dwell_time)
    logging.info('roi: %s', roi)
    logging.info('HFW(m): %s', escan.horizontalFoV.value)
    logging.info('current(A): %s', escan.current.value)
    logging.info('MAX res(px): %s', math.sqrt(MAX_PIXELS))
    logging.info('Acquire time(s): %s', delay_time)
    logging.info('file name by: %s', time.strftime("%m%d-%H%M"))
    logging.info('Max images: %s', nb_images)
    logging.info('Pixel size(m): %s', escan.pixelSize.value)

    drift_est = drift.AnchoredEstimator(escan, detector,
                                        roi,
                                        dwell_time, max_pixels=512 ** 2, follow_drift=False)
    i = 0
    row_drift = []
    col_drift = []
    base_time = time.strftime("%m%d-%H%M")
    for i in range(nb_images):
        logging.debug("IMAGE %s", i)

        # Acquire an image at the given location (RoI)
        drift_est.acquire()
        da = drift_est.raw[-1]

        # Save the Acquired Image
        # (For debugging purposes, have an option to store the image, with current date and time in the filename)
        timestr = base_time + "_" + str(i)
        filename = "{0}{1}{2}".format(p.stem, timestr, p.suffix)
        filename_path = os.path.dirname(options.base) + '/' + filename
        dataio.tiff.export(filename_path, da)

        # Estimate the drift
        drift_est.estimate()
        col_drift.append(drift_est.drift[0])
        row_drift.append(drift_est.drift[1])

        # Move FIB to compensate drift
        # The beam shift behaves differently in simulation when compared to hardware. The below compensation
        # code is according to the sign conventions of FIB hardware
        previous_shift = escan.shift.value
        logging.debug("Ion-beam shift in m : %s", previous_shift)
        pixel_size = da.metadata[model.MD_PIXEL_SIZE]
        escan.shift.value = (pixel_size[0] * drift_est.drift[0] + previous_shift[0],
                             -(pixel_size[1] * drift_est.drift[1]) + previous_shift[
                                 1])  # shift in m - absolute position
        logging.debug("New Ion-beam shift in m : %s", escan.shift.value)
        logging.debug("pixel size in m : %s", pixel_size)

        # Wait until next acquisition
        time.sleep(delay_time)
        logging.debug("---------------------------------------------------------------")

    return row_drift, col_drift


if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)
