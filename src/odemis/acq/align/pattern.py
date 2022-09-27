# -*- coding: utf-8 -*-
"""
Created on 9 Sep 2014

@author: Kimon Tsitsikas

Copyright © 2013-2014 Kimon Tsitsikas, Delmic

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

from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, \
    RUNNING
import logging
import numpy
import threading

# The goal is to align roughly the SEM and optical lenses. By drawing CL spots
# with the e-beam at a very large FoV, we can observe some of them in the CCD.
# By checking which sub-pattern is visible in the CCD, it's possible to determine
# where is the optical lens compared to the e-beam.

# Note: this is currently UNUSED. On the DELPHI, we rely on placing roughly
# the lens using the NavCam view. On the SECOM, we provide "dichotomy search".


# SEM FoV used for the pattern scanning
SCANNING_FOV = 1020e-06
# CCD FoV that guarantees at least one complete subpattern is observed (m)
DETECTION_FOV = (220e-06, 220e-06)
SUBPATTERNS = (7, 7)  # Dimensions of pattern in terms of subpatterns
SPOT_DIST = 20e-06  # Distance between spots in subpattern #m
SUBPATTERN_DIMS = (3, 3)  # Dimensions of subpattern in terms of spots
# Distance between 2 neighboor subpatterns (from center to center, in m)
SUBPATTERN_DIST = 160e-06

class PatternScanner(object):
    def __init__(self, ccd, detector, escan, opt_stage, focus, pattern):
        self.ccd = ccd
        self.detector = detector
        self.escan = escan
        self.opt_stage = opt_stage
        self.focus = focus
        self.pattern = pattern

        self._pattern_state = FINISHED
        self._pattern_lock = threading.Lock()

        self._hw_settings = ()

    def _save_hw_settings(self):
        scale = self.escan.scale.value
        sem_res = self.escan.resolution.value
        trans = self.escan.translation.value
        dt = self.escan.dwellTime.value

        binning = self.ccd.binning.value
        ccd_res = self.ccd.resolution.value
        et = self.ccd.exposureTime.value

        self._hw_settings = (sem_res, scale, trans, dt, binning, ccd_res, et)

    def _restore_hw_settings(self):
        sem_res, scale, trans, dt, binning, ccd_res, et = self._hw_settings

        # order matters!
        self.escan.scale.value = scale
        self.escan.resolution.value = sem_res
        self.escan.translation.value = trans
        self.escan.dwellTime.value = dt

        self.ccd.binning.value = binning
        self.ccd.resolution.value = ccd_res
        self.ccd.exposureTime.value = et

    def _discard_data(self, df, data):
        """
        Does nothing, just discard the SEM data received (for spot mode)
        """
        pass

    def DoPattern(self):
        """
        Given an SH Calibration Pattern, it first scans the required spots, acquires
        the CCD image, detects the combination of the spots observed and moves
        accordingly. It repeats this process until the CCD image contains the center
        pattern.
        ccd (model.DigitalCamera): The ccd
        detector (model.Detector): The se-detector
        escan (model.Emitter): The e-beam scanner
        opt_stage (model.Actuator): The objective stage
        focus (model.Actuator): Focus of objective lens
        pattern (model.DataArray): 21x21 array containing the spot
                                    pattern in binary data. Needs to
                                    be divided in 3x3 sub-arrays to
                                    get the actual information
        raises:
            CancelledError() if cancelled
            IOError if pattern not found
        """
        self._save_hw_settings()
        self._pattern_state = RUNNING

        escan = self.escan
        ccd = self.ccd
        detector = self.detector
        pattern = self.pattern

        try:
            # Set proper SEM FoV
            escan.horizontalFoV.value = SCANNING_FOV
            # Spot mode
            escan.scale.value = (1, 1)
            escan.resolution.value = (1, 1)
            escan.translation.value = (0, 0)
            escan.dwellTime.value = escan.dwellTime.range[1]
            ccd.binning.value = (1, 1)
            # FIXME: the CCD FoV depends on the lens magnification (cf metadata)
            ccd.resolution.value = (int(DETECTION_FOV[0] / ccd.pixelSize.value[0]),
                                    int(DETECTION_FOV[1] / ccd.pixelSize.value[1]))
            ccd.exposureTime.value = 900e-03  # s

            # Distance between spots in subpattern in pixels
            spot_dist_pxs = SPOT_DIST / escan.pixelSize.value[0]
            subpattern_dist_pxs = SUBPATTERN_DIST / escan.pixelSize.value[0]
            # floor rounding to take care of odd number of pixels
            center_subpattern = (SUBPATTERNS[0] // 2, SUBPATTERNS[1] // 2)
            center_spot = (SUBPATTERN_DIMS[0] // 2, SUBPATTERN_DIMS[1] // 2)

            # Iterate until you reach the center pattern
            while True:
                if self._pattern_state == CANCELLED:
                    raise CancelledError()
                detector.data.subscribe(self._discard_data)
                # Go through the 3x3 subpatterns
                for i, j in numpy.ndindex(SUBPATTERNS):
                    if self._pattern_state == CANCELLED:
                        raise CancelledError()
                    subpattern = pattern[(i * SUBPATTERN_DIMS[0]):(i * SUBPATTERN_DIMS[0]) + SUBPATTERN_DIMS[0],
                                         (j * SUBPATTERN_DIMS[1]):(j * SUBPATTERN_DIMS[1]) + SUBPATTERN_DIMS[1]]
                    # Translation to subpattern center
                    center_translation = ((i - center_subpattern[0]) * subpattern_dist_pxs,
                                          (j - center_subpattern[1]) * subpattern_dist_pxs)
                    for k, l in numpy.ndindex(subpattern.shape):
                        # If spot has to be scanned
                        if subpattern[k, l] == 1:
                            # Translation from subpattern center to particular spot
                            spot_translation = ((k - center_spot[0]) * spot_dist_pxs,
                                                (l - center_spot[1]) * spot_dist_pxs)
                            # Translation from SEM center to particular spot
                            total_translation = (center_translation[0] + spot_translation[0],
                                                 center_translation[1] + spot_translation[1])
                            escan.translation.value = total_translation
                # TODO, pattern detection and move
                # Maybe autofocus in the first iteration?
                # image = ccd.data.get()
                # move = DetectSubpattern(image)
                # If center subpattern is detected the move will be 0 and we break
                # opt_stage.moveRel(move)
        finally:
            detector.data.unsubscribe(self._discard_data)
            self._restore_hw_settings()

    def CancelPattern(self):
        """
        Canceller of DoPattern task.
        """
        logging.debug("Cancelling pattern scan...")

        with self._pattern_lock:
            if self._pattern_state == FINISHED:
                logging.debug("Pattern scan already finished.")
                return False
            self._pattern_state = CANCELLED
            logging.debug("Pattern scan cancelled.")

        return True
