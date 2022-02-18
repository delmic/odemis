#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 17 Feb 2022

@author: Éric Piel

Copyright © 2022 Éric Piel, Delmic

This script reads a overlay report and simulates acquiring it again, so that
it's possible testing different versions of FindOverlay().

run as:
python reproduce_overlay.py report_path

"""

from __future__ import division

import argparse
import logging
import math
from odemis import model
from odemis.acq.align import find_overlay
from odemis.dataio import tiff
from odemis.driver import simcam, simsem
from odemis.util import mock
import os
import sys


logging.getLogger().setLevel(logging.DEBUG)

CONFIG_SED = {"name": "sed", "role": "sed"}
CONFIG_SCANNER = {"name": "scanner", "role": "ebeam"}
CONFIG_SEM = {"name": "sem", "role": "sem", "image": "simsem-fake-output.h5",
              "children": {"detector0": CONFIG_SED, "scanner": CONFIG_SCANNER}
              }


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser()

    parser.add_argument(dest="path",
                        help="path of the report")

    options = parser.parse_args(args[1:])

    # TODO: read infor from report.txt
    repetitions = (4, 4)
    # Note: the dwell time reported is the first dwell time tried, but the data stored
    # corresponds to the last iteration, with longer dwell time. The final dwell time
    # can be found out from the exposure time of the CCD.
    dwell_time = 1.0  # Note: the time doesn't matter for reproducing the error
    sem_fov = (0.00019999999999999998, 0.00019999999999999998)
    max_allowed_diff = 1e-5

    # FIXME: the SEM shape should be the same (but it doesn't matter much, as the spots
    # will be placed at the same position proportionally)

    try:
        im_path = os.path.join(options.path, "OpticalGrid.tiff")
        im = tiff.read_data(im_path)[0]
        # TODO: handle when it's a spot acquisition (ie, 1 image per spot => return each image one after another)

        ccd = mock.FakeCCD(im)

        # TODO: check that the pixel size and FoV match
        # The exposure time should also match
        # Simulate the "lens" by setting the magnification + pixel size
        # (it's already on the image, but not directly, on the component, which confuses FindOverlay)
        im_md = im.metadata
        ccd.updateMetadata({model.MD_LENS_MAG: im_md[model.MD_LENS_MAG],
                            model.MD_PIXEL_SIZE: im_md[model.MD_PIXEL_SIZE]})

        sem = simsem.SimSEM(**CONFIG_SEM)

        for child in sem.children.value:
            if child.name == CONFIG_SED["name"]:
                sed = child
            elif child.name == CONFIG_SCANNER["name"]:
                escan = child

        # TODO: check the SEM shape is the same? How?
        escan.horizontalFoV.value = sem_fov[0]

        f = find_overlay.FindOverlay(repetitions, dwell_time, max_allowed_diff, escan, ccd, sed,
                                     skew=True)
        trans_val, cor_md = f.result()
        trans_md, skew_md = cor_md
        iscale = trans_md[model.MD_PIXEL_SIZE_COR]
        irot = -trans_md[model.MD_ROTATION_COR] % (2 * math.pi)
        ishear = -skew_md[model.MD_SHEAR_COR]
        iscale_xy = skew_md[model.MD_PIXEL_SIZE_COR]
        logging.debug("iscale: %s irot: %s ishear: %s iscale_xy: %s", iscale, irot, ishear, iscale_xy)

    except:
        logging.exception("Unexpected error while performing action.")
        return 127

    return 0


if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)
