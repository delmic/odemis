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
# python delphi_man_calib.py
#
# You first need to run the odemis backend with the DELPHI config:
# odemisd --log-level 2 install/linux/usr/share/odemis/delphi.odm.yaml


from __future__ import division

import argparse
import collections
import logging
import math
from odemis import model
from odemis.acq import align
from odemis.acq.align import spot
from odemis.gui.conf import get_calib_conf
import os
import shutil
import sys
import threading
import time
import tty

import odemis.acq.align.delphi as aligndelphi
import termios


YES_CHARS = {"Y", "y", ''}
YES_NO_CHARS = {"Y", "y", "N", "n", ''}
TIMEOUT = 1


def getch():
    """
    Get character from keyboard, handling (a bit) ANSI escape codes
    """
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(sys.stdin.fileno())
        ch = sys.stdin.read(1)
        if ch == "\x1b":  # ANSI escape sequence => get another 2 chars
            ch += sys.stdin.read(2)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return ch


def _discard_data(df, data):
    """
    Does nothing, just discard the SEM data received (for spot mode)
    """
    pass


class ArrowFocus():
    """
    Use keyboard arrows to move focus actuators by stepsize.
    """
    def __init__(self, sem_stage, opt_focus, ebeam_focus, opt_stepsize, ebeam_stepsize):
        self.sem_stage = sem_stage
        self.opt_focus = opt_focus
        self.ebeam_focus = ebeam_focus
        self.opt_stepsize = opt_stepsize
        self.ebeam_stepsize = ebeam_stepsize
        # Queue maintaining moves to be done
        self._moves_ebeam = collections.deque()
        self._moves_opt = collections.deque()
        self._acc_move_opt = 0
        self._move_must_stop = threading.Event()

    def _move_focus(self):
        while not self._move_must_stop.is_set():
            if not self._moves_ebeam:
                pass
            else:
                mov = self._moves_ebeam.popleft()
                f = self.ebeam_focus.moveRel({'z': mov})
                f.result()
            if not self._moves_opt:
                pass
            else:
                mov = self._moves_opt.popleft()
                f = self.opt_focus.moveRel({'z': mov})
                f.result()

    def focusByArrow(self, rollback_position=None):
        self._focus_thread = threading.Thread(target=self._move_focus,
                                              name="Arrow focus thread")
        self._focus_thread.start()
        try:
            while True:
                c = getch()
                if c in ('R', 'r') and rollback_position is not None:
                    f = self.sem_stage.moveAbs({'x': rollback_position[0],
                                                'y': rollback_position[1]})
                    f.result()
                elif c == '\x1b[A':
                    self._moves_opt.append(self.opt_stepsize)
                elif c == '\x1b[B':
                    self._moves_opt.append(-self.opt_stepsize)
                elif c == '\x1b[C':
                    self._moves_ebeam.append(self.ebeam_stepsize)
                elif c == '\x1b[D':
                    self._moves_ebeam.append(-self.ebeam_stepsize)
                # break when Enter is pressed
                elif c in ("\n", "\r"):
                    break
                elif c == "\x03":  # Ctrl+C
                    raise KeyboardInterrupt()
                else:
                    logging.debug("Unhandled key: %s", c.encode('string_escape'))
        finally:
            self._move_must_stop.set()
            self._focus_thread.join(10)


def man_calib(logpath):
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

    hw_settings = aligndelphi.list_hw_settings(escan, ccd)

    try:
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
                raise IOError("Failed to find the overview pressure in %s" % (pressures,))

        calibconf = get_calib_conf()
        shid, sht = chamber.sampleHolder.value
        calib_values = calibconf.get_sh_calib(shid)
        if calib_values is None:
            first_hole = second_hole = offset = resa = resb = hfwa = spotshift = (0, 0)
            scaling = iscale = iscale_xy = (1, 1)
            hole_focus = rotation = irot = ishear = 0
            print "\033[1;31mCalibration values missing! All the steps will be performed anyway...\033[1;m"
            force_calib = True
        else:
            first_hole, second_hole, hole_focus, opt_focus, offset, scaling, rotation, iscale, irot, iscale_xy, ishear, resa, resb, hfwa, spotshift = calib_values
            force_calib = False
        print "\033[1;36m"
        print "**Delphi Manual Calibration steps**"
        print "1.Sample holder hole detection"
        print "    Current values: 1st hole: " + str(first_hole)
        print "                    2st hole: " + str(second_hole)
        print "                    hole focus: " + str(hole_focus)
        print "2.Twin stage calibration"
        print "    Current values: offset: " + str(offset)
        print "                    scaling: " + str(scaling)
        print "                    rotation: " + str(rotation)
        print "                    optical focus: " + str(opt_focus)
        print "3.SEM image calibration"
        print "    Current values: resolution-a: " + str(resa)
        print "                    resolution-b: " + str(resb)
        print "                    hfw-a: " + str(hfwa)
        print "                    spot shift: " + str(spotshift)
        print "4.Fine alignment"
        print "    Current values: scale: " + str(iscale)
        print "                    rotation: " + str(irot)
        print "                    scale-xy: " + str(iscale_xy)
        print "                    shear: " + str(ishear)
        print '\033[1;m'
        print "\033[1;33mNote that you should not perform any stage move during the process. \nInstead, you may zoom in/out while focusing.\033[1;m"
        print "\033[1;30mNow initializing, please wait...\033[1;m"

        # Move to the overview position first
        f = chamber.moveAbs({"pressure": overview_pressure})
        f.result()

        # Reference the (optical) stage
        f = opt_stage.reference({"x", "y"})
        f.result()

        f = focus.reference({"z"})
        f.result()

        # SEM stage to (0,0)
        f = sem_stage.moveAbs({"x": 0, "y": 0})
        f.result()

        # Calculate offset approximation
        try:
            f = aligndelphi.LensAlignment(overview_ccd, sem_stage)
            position = f.result()
        except IOError:
            if not force_calib:
                position = (offset[0] * scaling[0], offset[1] * scaling[1])
                logging.warning("Failed to locate the optical lens, will used previous value %s", position)
            else:
                raise IOError("Failed to locate the optical lens in the NavCam view.")

        # Just to check if move makes sense
        f = sem_stage.moveAbs({"x": position[0], "y": position[1]})
        f.result()

        # Move to SEM
        f = chamber.moveAbs({"pressure": vacuum_pressure})
        f.result()

        # Set basic e-beam settings
        escan.spotSize.value = 2.7
        escan.accelVoltage.value = 5300  # V

        # Detect the holes/markers of the sample holder
        while True:
            ans = "Y" if force_calib else None
            while ans not in YES_NO_CHARS:
                msg = "\033[1;35mDo you want to execute the sample holder hole detection? [Y/n]\033[1;m"
                ans = raw_input(msg)
            if ans in YES_CHARS:
                # Move Phenom sample stage next to expected hole position
                sem_stage.moveAbsSync(aligndelphi.SHIFT_DETECTION)
                ebeam_focus.moveAbsSync(hole_focus)
                # Set the FoV to almost 2mm
                escan.horizontalFoV.value = escan.horizontalFoV.range[1]
                msg = "\033[1;34mPlease turn on the SEM stream and focus the SEM image. Then turn off the stream and press Enter ...\033[1;m"
                raw_input(msg)
                print "\033[1;30mTrying to detect the holes/markers, please wait...\033[1;m"
                try:
                    hole_detectionf = aligndelphi.HoleDetection(detector, escan, sem_stage,
                                                                ebeam_focus, manual=True)
                    new_first_hole, new_second_hole, new_hole_focus = hole_detectionf.result()
                    print '\033[1;36m'
                    print "Values computed: 1st hole: " + str(new_first_hole)
                    print "                 2st hole: " + str(new_second_hole)
                    print "                 hole focus: " + str(new_hole_focus)
                    print '\033[1;m'
                    ans = "Y" if force_calib else None
                    while ans not in YES_NO_CHARS:
                        msg = "\033[1;35mDo you want to update the calibration file with these values? [Y/n]\033[1;m"
                        ans = raw_input(msg)
                    if ans in YES_CHARS:
                        first_hole, second_hole, hole_focus = new_first_hole, new_second_hole, new_hole_focus
                        calibconf.set_sh_calib(shid, first_hole, second_hole, hole_focus, opt_focus, offset,
                               scaling, rotation, iscale, irot, iscale_xy, ishear,
                               resa, resb, hfwa, spotshift)
                    break
                except IOError:
                    print "\033[1;31mSample holder hole detection failed.\033[1;m"
            else:
                break

        f = sem_stage.moveAbs({"x": position[0], "y": position[1]})
        f.result()

        f = opt_stage.moveAbs({"x": 0, "y": 0})
        f.result()

        if hole_focus is not None:
            good_focus = hole_focus - aligndelphi.GOOD_FOCUS_OFFSET
        else:
            good_focus = aligndelphi.SEM_KNOWN_FOCUS - aligndelphi.GOOD_FOCUS_OFFSET
        f = ebeam_focus.moveAbs({"z": good_focus})
        f.result()

        # Set min fov
        # We want to be as close as possible to the center when we are zoomed in
        escan.horizontalFoV.value = escan.horizontalFoV.range[0]
        pure_offset = None

        # Start with the best optical focus known so far
        f = focus.moveAbs({"z": opt_focus})
        f.result()

        while True:
            ans = "Y" if force_calib else None
            while ans not in YES_NO_CHARS:
                msg = "\033[1;35mDo you want to execute the twin stage calibration? [Y/n]\033[1;m"
                ans = raw_input(msg)
            if ans in YES_CHARS:
                # Configure CCD and e-beam to write CL spots
                ccd.binning.value = (1, 1)
                ccd.resolution.value = ccd.resolution.range[1]
                ccd.exposureTime.value = 900e-03
                escan.scale.value = (1, 1)
                escan.resolution.value = (1, 1)
                escan.translation.value = (0, 0)
                if not escan.rotation.readonly:
                    escan.rotation.value = 0
                escan.shift.value = (0, 0)
                escan.dwellTime.value = 5e-06
                detector.data.subscribe(_discard_data)
                print "\033[1;34mPlease turn on the Optical stream, set Power to 0 Watt and focus the image so you have a clearly visible spot.\033[1;m"
                print "\033[1;34mUse the up and down arrows or the mouse to move the optical focus and right and left arrows to move the SEM focus. Then turn off the stream and press Enter ...\033[1;m"
                if not force_calib:
                    print "\033[1;33mIf you cannot see the whole source background (bright circle) you may try to move to the already known offset position. \nTo do this press the R key at any moment.\033[1;m"
                    rollback_pos = (offset[0] * scaling[0], offset[1] * scaling[1])
                else:
                    rollback_pos = None
                ar = ArrowFocus(sem_stage, focus, ebeam_focus, ccd.depthOfField.value, escan.depthOfField.value)
                ar.focusByArrow(rollback_pos)
                detector.data.unsubscribe(_discard_data)
                print "\033[1;30mFine alignment in progress, please wait...\033[1;m"
                try:
                    # TODO: the first point (at 0,0) isn't different from the next 4 points,
                    # excepted it might be a little harder to focus.
                    # => use the same code for all of them
                    align_offsetf = aligndelphi.AlignAndOffset(ccd, detector, escan, sem_stage,
                                                               opt_stage, focus)
                    align_offset = align_offsetf.result()
                    new_opt_focus = focus.position.value.get('z')

                    def ask_user_to_focus(n):
                        detector.data.subscribe(_discard_data)
                        msg = ("\033[1;34mAbout to calculate rotation and scaling (%d/4). "
                               "Please turn on the Optical stream, "
                               "set Power to 0 Watt and focus the image using the mouse "
                               "so you have a clearly visible spot. \n"
                               "If you do not see a spot nor the source background, "
                               "move the sem-stage from the command line by steps of 200um "
                               "in x and y until you can see the source background at the center. \n"
                               "Then turn off the stream and press Enter ...\033[1;m" %
                               (n + 1,))
                        raw_input(msg)  # TODO: use ArrowFocus() too?
                        print "\033[1;30mCalculating rotation and scaling (%d/4), please wait...\033[1;m" % (n + 1,)
                        detector.data.unsubscribe(_discard_data)

                    f = aligndelphi.RotationAndScaling(ccd, detector, escan, sem_stage,
                                                       opt_stage, focus, align_offset,
                                                       manual=ask_user_to_focus)
                    acc_offset, new_rotation, new_scaling = f.result()

                    # Offset is divided by scaling, since Convert Stage applies scaling
                    # also in the given offset
                    pure_offset = acc_offset
                    new_offset = ((acc_offset[0] / new_scaling[0]), (acc_offset[1] / new_scaling[1]))

                    print '\033[1;36m'
                    print "Values computed: offset: " + str(new_offset)
                    print "                 scaling: " + str(new_scaling)
                    print "                 rotation: " + str(new_rotation)
                    print "                 optical focus: " + str(new_opt_focus)
                    print '\033[1;m'
                    ans = "Y" if force_calib else None
                    while ans not in YES_NO_CHARS:
                        msg = "\033[1;35mDo you want to update the calibration file with these values? [Y/n]\033[1;m"
                        ans = raw_input(msg)
                    if ans in YES_CHARS:
                        offset, scaling, rotation, opt_focus = new_offset, new_scaling, new_rotation, new_opt_focus
                        calibconf.set_sh_calib(shid, first_hole, second_hole, hole_focus, opt_focus, offset,
                               scaling, rotation, iscale, irot, iscale_xy, ishear,
                               resa, resb, hfwa, spotshift)
                    break
                except IOError:
                    print "\033[1;31mTwin stage calibration failed.\033[1;m"
            else:
                break

        while True:
            ans = "Y" if force_calib else None
            while ans not in YES_NO_CHARS:
                msg = "\033[1;35mDo you want to execute the SEM image calibration? [Y/n]\033[1;m"
                ans = raw_input(msg)
            if ans in YES_CHARS:
                # Resetting shift parameters, to not take them into account during calib
                blank_md = dict.fromkeys(aligndelphi.MD_CALIB_SEM, (0, 0))
                escan.updateMetadata(blank_md)

                # We measure the shift in the area just behind the hole where there
                # are always some features plus the edge of the sample carrier. For
                # that reason we use the focus measured in the hole detection step
                f = sem_stage.moveAbs(aligndelphi.SHIFT_DETECTION)
                f.result()

                f = ebeam_focus.moveAbs({"z": hole_focus})
                f.result()
                try:
                    # Compute spot shift percentage
                    print "\033[1;30mSpot shift measurement in progress, please wait...\033[1;m"
                    f = aligndelphi.ScaleShiftFactor(detector, escan, logpath)
                    new_spotshift = f.result()

                    # Compute resolution-related values.
                    print "\033[1;30mCalculating resolution shift, please wait...\033[1;m"
                    resolution_shiftf = aligndelphi.ResolutionShiftFactor(detector, escan, logpath)
                    new_resa, new_resb = resolution_shiftf.result()

                    # Compute HFW-related values
                    print "\033[1;30mCalculating HFW shift, please wait...\033[1;m"
                    hfw_shiftf = aligndelphi.HFWShiftFactor(detector, escan, logpath)
                    new_hfwa = hfw_shiftf.result()

                    print '\033[1;36m'
                    print "Values computed: resolution-a: " + str(new_resa)
                    print "                 resolution-b: " + str(new_resb)
                    print "                 hfw-a: " + str(new_hfwa)
                    print "                 spot shift: " + str(new_spotshift)
                    print '\033[1;m'
                    ans = "Y" if force_calib else None
                    while ans not in YES_NO_CHARS:
                        msg = "\033[1;35mDo you want to update the calibration file with these values? [Y/n]\033[1;m"
                        ans = raw_input(msg)
                    if ans in YES_CHARS:
                        resa, resb, hfwa, spotshift = new_resa, new_resb, new_hfwa, new_spotshift
                        calibconf.set_sh_calib(shid, first_hole, second_hole, hole_focus, opt_focus, offset,
                               scaling, rotation, iscale, irot, iscale_xy, ishear,
                               resa, resb, hfwa, spotshift)
                    break
                except IOError:
                    print "\033[1;31mSEM image calibration failed.\033[1;m"
            else:
                break

        while True:
            ans = "Y" if force_calib else None
            while ans not in YES_NO_CHARS:
                msg = "\033[1;35mDo you want to execute the fine alignment? [Y/n]\033[1;m"
                ans = raw_input(msg)
            if ans in YES_CHARS:
                # Return to the center so fine alignment can be executed just after calibration
                f = opt_stage.moveAbs({"x": 0, "y": 0})
                f.result()
                if pure_offset is not None:
                    f = sem_stage.moveAbs({"x":pure_offset[0], "y":pure_offset[1]})
                    f.result()
                elif offset is not None:
                    f = sem_stage.moveAbs({"x":offset[0] * scaling[0], "y":offset[1] * scaling[1]})
                    f.result()
                else:
                    f = sem_stage.moveAbs({"x":position[0], "y":position[1]})
                    f.result()

                f = focus.moveAbs({"z": opt_focus})
                f.result()
                f = ebeam_focus.moveAbs({"z": good_focus})
                f.result()

                # Run the optical fine alignment
                # TODO: reuse the exposure time
                # Configure e-beam to write CL spots
                escan.horizontalFoV.value = escan.horizontalFoV.range[0]
                escan.scale.value = (1, 1)
                escan.resolution.value = (1, 1)
                escan.translation.value = (0, 0)
                if not escan.rotation.readonly:
                    escan.rotation.value = 0
                escan.shift.value = (0, 0)
                escan.dwellTime.value = 5e-06
                detector.data.subscribe(_discard_data)
                print "\033[1;34mPlease turn on the Optical stream, set Power to 0 Watt and focus the image so you have a clearly visible spot.\033[1;m"
                print "\033[1;34mUse the up and down arrows or the mouse to move the optical focus and right and left arrows to move the SEM focus. Then turn off the stream and press Enter ...\033[1;m"
                ar = ArrowFocus(sem_stage, focus, ebeam_focus, ccd.depthOfField.value, escan.depthOfField.value)
                ar.focusByArrow()
                detector.data.unsubscribe(_discard_data)

                # restore CCD settings (as the GUI/user might have changed them)
                ccd.binning.value = (1, 1)
                ccd.resolution.value = ccd.resolution.range[1]
                ccd.exposureTime.value = 900e-03
                # Center (roughly) the spot on the CCD
                f = spot.CenterSpot(ccd, sem_stage, escan, spot.ROUGH_MOVE, spot.STAGE_MOVE, detector.data)
                dist, vect = f.result()
                if dist is None:
                    logging.warning("Failed to find a spot, twin stage calibration might have failed")

                print "\033[1;30mFine alignment in progress, please wait...\033[1;m"
                try:
                    escan.horizontalFoV.value = 80e-06
                    f = align.FindOverlay((4, 4),
                                          0.5,  # s, dwell time
                                          10e-06,  # m, maximum difference allowed
                                          escan,
                                          ccd,
                                          detector,
                                          skew=True,
                                          bgsub=True)
                    trans_val, cor_md = f.result()
                    trans_md, skew_md = cor_md
                    new_iscale = trans_md[model.MD_PIXEL_SIZE_COR]
                    new_irot = -trans_md[model.MD_ROTATION_COR] % (2 * math.pi)
                    new_ishear = skew_md[model.MD_SHEAR_COR]
                    new_iscale_xy = skew_md[model.MD_PIXEL_SIZE_COR]
                    print '\033[1;36m'
                    print "Values computed: scale: " + str(new_iscale)
                    print "                 rotation: " + str(new_irot)
                    print "                 scale-xy: " + str(new_iscale_xy)
                    print "                 shear: " + str(new_ishear)
                    print '\033[1;m'
                    ans = "Y" if force_calib else None
                    while ans not in YES_NO_CHARS:
                        msg = "\033[1;35mDo you want to update the calibration file with these values? [Y/n]\033[1;m"
                        ans = raw_input(msg)
                    if ans in YES_CHARS:
                        iscale, irot, iscale_xy, ishear = new_iscale, new_irot, new_iscale_xy, new_ishear
                        calibconf.set_sh_calib(shid, first_hole, second_hole, hole_focus, opt_focus, offset,
                               scaling, rotation, iscale, irot, iscale_xy, ishear,
                               resa, resb, hfwa, spotshift)
                    break
                except ValueError:
                    print "\033[1;31mFine alignment failed.\033[1;m"
            else:
                break

        # Update calibration file
        print "\033[1;30mUpdating calibration file is done\033[1;m"

    finally:
        print "\033[1;30mCalibration ended, now ejecting sample, please wait...\033[1;m"
        # Eject the sample holder
        f = chamber.moveAbs({"pressure": vented_pressure})

        aligndelphi.restore_hw_settings(escan, ccd, hw_settings)

        # Store the final version of the calibration file
        try:
            shutil.copy(calibconf.file_path, logpath)
        except Exception:
            logging.info("Failed to log calibration file", exc_info=True)

        f.result()


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """
    # arguments handling
    parser = argparse.ArgumentParser()

    parser.add_argument("--log-level", dest="loglev", metavar="<level>", type=int,
                        default=0, help="set verbosity level (0-2, default = 0)")

    options = parser.parse_args(args[1:])

    # Set up logging before everything else
    if options.loglev < 0:
        logging.error("Log-level must be positive.")
        return 127

    loglev_names = [logging.WARNING, logging.INFO, logging.DEBUG]
    loglev = loglev_names[min(len(loglev_names) - 1, options.loglev)]
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)  # To always pass all the messages to the handlers

    # Show the log messages both on the console...
    logging.basicConfig(format="%(levelname)s: %(message)s")
    logger.handlers[0].setLevel(loglev)

    # ...and also store them in the special file
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    logpath = os.path.join(os.path.expanduser(u"~"), aligndelphi.CALIB_DIRECTORY,
                           time.strftime(u"%Y%m%d-%H%M%S"))
    os.makedirs(logpath)
    hdlr_calib = logging.FileHandler(os.path.join(logpath, aligndelphi.CALIB_LOG))
    hdlr_calib.setFormatter(formatter)
    hdlr_calib.setLevel(logging.DEBUG)  # Store always all the messages
    logging.getLogger().addHandler(hdlr_calib)

    try:
        man_calib(logpath)
    except KeyboardInterrupt:
        logging.warning("Manual calibration procedure was cancelled.")
    except:
        logging.exception("Unexpected error while performing action.")
        return 127

    return 0


if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)
