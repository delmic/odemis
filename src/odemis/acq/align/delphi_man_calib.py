#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Created on 5 Jan 2015

@author: Kimon Tsitsikas

Copyright © 2014-2015 Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms  of the GNU General Public License version 2 as published by the Free
Software  Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY;  without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR  PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

# This script allows the user to perform the whole delphi calibration procedure
# step by step in a semi-manual way. It attempts to apply each step automatically
# and in case of failure it waits for the user to perform the step failed manually.
#
# run as:
# python delphi_manual_calibration.py
#
# You first need to run the odemis backend with the DELPHI config:
# odemisd --log-level 2 install/linux/usr/share/odemis/delphi.odm.yaml



from __future__ import division

import logging
from odemis import model
import odemis.acq.align.delphi as aligndelphi
import sys
from odemis.acq import align
from odemis.gui.conf import get_calib_conf
import math
from odemis.acq.align import autofocus

logging.getLogger().setLevel(logging.DEBUG)


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """
    try:
        escan = None
        detector = None
        ccd = None
        # find components by their role
        for c in model.getComponents():
            if c.role == "e-beam":
                escan = c
            elif c.role == "bs-detector":
                detector = c
            elif c.role == "ccd":
                ccd = c
            elif c.role == "sem-stage":
                sem_stage = c
            elif c.role == "align":
                opt_stage = c
            elif c.role == "ebeam-focus":
                ebeam_focus = c
            elif c.role == "overview-focus":
                navcam_focus = c
            elif c.role == "focus":
                focus = c
            elif c.role == "overview-ccd":
                overview_ccd = c
            elif c.role == "chamber":
                chamber = c
        if not all([escan, detector, ccd]):
            logging.error("Failed to find all the components")
            raise KeyError("Not all components found")

        # Get pressure values
        pressures = chamber.axes["pressure"].choices
        vacuum_pressure = min(pressures.keys())
        vented_pressure = max(pressures.keys())
        if overview_ccd:
            for p, pn in pressures.items():
                if pn == "overview":
                    overview_pressure = p
                    break
            else:
                overview_pressure = None

        # Move to the overview position first
        f = chamber.moveAbs({"pressure": overview_pressure})
        f.result()

        # Reference the (optical) stage
        logging.debug("Referencing the (optical) stage...")
        f = opt_stage.reference({"x", "y"})
        f.result()

        logging.debug("Referencing the focus...")
        f = focus.reference({"z"})
        f.result()

        # SEM stage to (0,0)
        logging.debug("Moving to the center of SEM stage...")
        f = sem_stage.moveAbs({"x": 0, "y": 0})
        f.result()

        # Calculate offset approximation
        try:
            f = aligndelphi.LensAlignment(overview_ccd, sem_stage)
            position = f.result()
            logging.debug("SEM position after lens alignment: %s", position)
        except Exception:
            raise IOError("Lens alignment failed.")

        # Just to check if move makes sense
        f = sem_stage.moveAbs({"x": position[0], "y": position[1]})
        f.result()

        # Move to SEM
        f = chamber.moveAbs({"pressure": vacuum_pressure})
        f.result()

        # Compute stage calibration values
        # Detect the holes/markers of the sample holder
        # Move Phenom sample stage to expected hole position
        f = sem_stage.moveAbs(aligndelphi.EXPECTED_HOLES[0])
        f.result()
        # Set the FoV to almost 2mm
        escan.horizontalFoV.value = escan.horizontalFoV.range[1]
        msg = "Please turn on the SEM stream and focus the SEM image. Then turn off the stream and press Enter ..."
        raw_input(msg)
        logging.debug("Trying to detect the holes/markers of the sample holder...")
        hole_detectionf = aligndelphi.HoleDetection(detector, escan, sem_stage,
                                                    ebeam_focus, known_focus=None, manual=True)
        first_hole, second_hole, hole_focus = hole_detectionf.result()
        logging.debug("First hole: %s (m,m) Second hole: %s (m,m)", first_hole, second_hole)
        hole_focus = ebeam_focus.position.value.get('z')

        logging.debug("Moving SEM stage to expected offset...")
        f = sem_stage.moveAbs({"x":position[0], "y":position[1]})
        f.result()

        logging.debug("Moving objective stage to (0,0)...")
        f = opt_stage.moveAbs({"x": 0, "y": 0})
        f.result()
        # Set min fov
        # We want to be as close as possible to the center when we are zoomed in
        escan.horizontalFoV.value = escan.horizontalFoV.range[0]

        # Configure CCD and e-beam to write CL spots
        ccd.binning.value = (1, 1)
        ccd.resolution.value = ccd.resolution.range[1]
        ccd.exposureTime.value = 900e-03
        escan.scale.value = (1, 1)
        escan.resolution.value = (1, 1)
        escan.translation.value = (0, 0)
        escan.shift.value = (0, 0)
        escan.dwellTime.value = 5e-06
        detector.data.subscribe(_discard_data)
        msg = "Please turn on the Optical stream, set Power to 0 Watt and focus the image so you have a clearly visible spot. Then turn off the stream and press Enter..."
        raw_input(msg)
        detector.data.unsubscribe(_discard_data)
        align_offsetf = aligndelphi.AlignAndOffset(ccd, detector, escan, sem_stage,
                                               opt_stage, focus)
        offset = align_offsetf.result()
        center_focus = focus.position.value.get('z')

        logging.debug("Calculating rotation and scaling...")
        try:
            rotation_scalingf = aligndelphi.RotationAndScaling(ccd, detector, escan, sem_stage,
                                                           opt_stage, focus, offset, manual=True)
            acc_offset, rotation, scaling = rotation_scalingf.result()
        except Exception:
            # Configure CCD and e-beam to write CL spots
            ccd.binning.value = (1, 1)
            ccd.resolution.value = ccd.resolution.range[1]
            ccd.exposureTime.value = 900e-03
            escan.scale.value = (1, 1)
            escan.resolution.value = (1, 1)
            escan.translation.value = (0, 0)
            escan.shift.value = (0, 0)
            escan.dwellTime.value = 5e-06
            detector.data.subscribe(_discard_data)
            msg = "Rotation calculation failed. Please turn on the Optical stream, set Power to 0 Watt and focus the image so you have a clearly visible spot. Then turn off the stream and press Enter to retry..."
            raw_input(msg)
            detector.data.unsubscribe(_discard_data)
            rotation_scalingf = aligndelphi.RotationAndScaling(ccd, detector, escan, sem_stage,
                                                           opt_stage, focus, offset, manual=True)
            acc_offset, rotation, scaling = rotation_scalingf.result()

        # Offset is divided by scaling, since Convert Stage applies scaling
        # also in the given offset
        pure_offset = acc_offset
        offset = ((acc_offset[0] / scaling[0]), (acc_offset[1] / scaling[1]))

        logging.info("\n**Computed calibration values**\n first hole: %s (unit: m,m)\n second hole: %s (unit: m,m)\n hole focus: %f (unit: m)\n offset: %s (unit: m,m)\n rotation: %f (unit: radians)\n scaling: %s \n", first_hole, second_hole, hole_focus, offset, rotation, scaling)

        logging.debug("Calculating shift parameters...")
        try:
            # Compute spot shift percentage
            spot_shiftf = aligndelphi.SpotShiftFactor(ccd, detector, escan, focus)
            spotshift = spot_shiftf.result()
        except Exception:
            # Configure CCD and e-beam to write CL spots
            ccd.binning.value = (1, 1)
            ccd.resolution.value = ccd.resolution.range[1]
            ccd.exposureTime.value = 900e-03
            escan.scale.value = (1, 1)
            escan.resolution.value = (1, 1)
            escan.translation.value = (0, 0)
            escan.shift.value = (0, 0)
            escan.dwellTime.value = 5e-06
            detector.data.subscribe(_discard_data)
            msg = "Spot shift calculation failed. Please turn on the Optical stream, set Power to 0 Watt and focus the image so you have a clearly visible spot. Then turn off the stream and press Enter to retry..."
            raw_input(msg)
            detector.data.unsubscribe(_discard_data)
            spot_shiftf = aligndelphi.SpotShiftFactor(ccd, detector, escan, focus)
            spotshift = spot_shiftf.result()

        # Compute resolution-related values
        resolution_shiftf = aligndelphi.ResolutionShiftFactor(detector, escan, sem_stage, ebeam_focus, hole_focus)
        resa, resb = resolution_shiftf.result()

        # Compute HFW-related values
        hfw_shiftf = aligndelphi.HFWShiftFactor(detector, escan, sem_stage, ebeam_focus, hole_focus)
        hfwa = hfw_shiftf.result()

        logging.info("\n**Computed SEM shift parameters**\n resa: %s \n resb: %s \n hfwa: %s \n spotshift: %s \n", resa, resb, hfwa, spotshift)

        # Return to the center so fine alignment can be executed just after calibration
        f = sem_stage.moveAbs({"x":-pure_offset[0], "y":-pure_offset[1]})
        f.result()
        f = opt_stage.moveAbs({"x": 0, "y": 0})
        f.result()
        f = focus.moveAbs({"z": center_focus})
        f.result()

        # Focus the CL spot using SEM focus
        # Configure CCD and e-beam to write CL spots
        ccd.binning.value = (1, 1)
        ccd.resolution.value = ccd.resolution.range[1]
        ccd.exposureTime.value = 900e-03
        escan.horizontalFoV.value = escan.horizontalFoV.range[0]
        escan.scale.value = (1, 1)
        escan.resolution.value = (1, 1)
        escan.translation.value = (0, 0)
        escan.shift.value = (0, 0)
        escan.dwellTime.value = 5e-06
        det_dataflow = detector.data
        f = autofocus.AutoFocus(ccd, escan, ebeam_focus, dfbkg=det_dataflow)
        f.result()

        # Refocus the SEM
        escan.resolution.value = (512, 512)
        msg = "Please turn on the SEM stream and focus the SEM image if needed. Then turn off the stream and press Enter ..."
        raw_input(msg)

        # Run the optical fine alignment
        # TODO: reuse the exposure time
        # Configure CCD and e-beam to write CL spots
        ccd.binning.value = (1, 1)
        ccd.resolution.value = ccd.resolution.range[1]
        ccd.exposureTime.value = 900e-03
        escan.scale.value = (1, 1)
        escan.resolution.value = (1, 1)
        escan.translation.value = (0, 0)
        escan.shift.value = (0, 0)
        escan.dwellTime.value = 5e-06
        detector.data.subscribe(_discard_data)
        msg = "Please turn on the Optical stream, set Power to 0 Watt and focus the image so you have a clearly visible spot. Then turn off the stream and press Enter..."
        raw_input(msg)
        detector.data.unsubscribe(_discard_data)
        try:
            escan.horizontalFoV.value = 80e-06
            f = align.FindOverlay((4, 4),
                                  0.5,  # s, dwell time
                                  10e-06,  # m, maximum difference allowed
                                  escan,
                                  ccd,
                                  detector,
                                  skew=True)
            trans_val, cor_md = f.result()
        except Exception:
            # Configure CCD and e-beam to write CL spots
            ccd.binning.value = (1, 1)
            ccd.resolution.value = ccd.resolution.range[1]
            ccd.exposureTime.value = 900e-03
            escan.scale.value = (1, 1)
            escan.resolution.value = (1, 1)
            escan.translation.value = (0, 0)
            escan.shift.value = (0, 0)
            escan.dwellTime.value = 5e-06
            detector.data.subscribe(_discard_data)
            msg = "Fine alignment failed. Please turn on the Optical stream, set Power to 0 Watt and focus the image so you have a clearly visible spot. Then turn off the stream and press Enter to retry..."
            raw_input(msg)
            detector.data.unsubscribe(_discard_data)
            escan.horizontalFoV.value = 80e-06
            f = align.FindOverlay((4, 4),
                      0.5,  # s, dwell time
                      10e-06,  # m, maximum difference allowed
                      escan,
                      ccd,
                      detector,
                      skew=True)
            trans_val, cor_md = f.result()

        trans_md, skew_md = cor_md
        iscale = trans_md[model.MD_PIXEL_SIZE_COR]
        irot = -trans_md[model.MD_ROTATION_COR] % (2 * math.pi)
        ishear = skew_md[model.MD_SHEAR_COR]
        iscale_xy = skew_md[model.MD_PIXEL_SIZE_COR]
        logging.info("\n**Computed fine alignment parameters**\n scaling: %s \n rotation: %f \n", iscale, irot)
        # Update calibration file
        calibconf = get_calib_conf()
        shid, sht = chamber.sampleHolder.value
        calibconf.set_sh_calib(shid, first_hole, second_hole, hole_focus, offset,
                             scaling, rotation, iscale, irot, iscale_xy, ishear,
                             resa, resb, hfwa, spotshift)
    except:
        logging.exception("Unexpected error while performing action.")
        return 127
    finally:
        # Eject the sample holder
        f = chamber.moveAbs({"pressure": vented_pressure})
        f.result()

    return 0

def _discard_data(df, data):
    """
    Does nothing, just discard the SEM data received (for spot mode)
    """
    pass

if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)
