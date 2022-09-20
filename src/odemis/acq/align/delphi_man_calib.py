#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 5 Jan 2015

@author: Kimon Tsitsikas

Copyright © 2014-2017 Kimon Tsitsikas, Éric Piel, Delmic

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


import argparse
from builtins import input
import collections
import logging
import math
from odemis import model, util
from odemis.acq import align
from odemis.acq.align import spot
from odemis.gui.conf import get_calib_conf
import os
import shutil
import sys
import termios
import threading
import time
import tty

import odemis.acq.align.delphi as aligndelphi

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


ANSI_BLACK = "30"
ANSI_RED = "31"
ANSI_GREEN = "32"
ANSI_YELLOW = "33"
ANSI_BLUE = "34"
ANSI_MAGENTA = "35"
ANSI_CYAN = "36"
ANSI_WHITE = "37"

def print_col(colour, s, *args, **kwargs):
    """
    Print with a given colour
    colour (string): A ANSI terminal compatible colour code
    s (string): message to print
    *args, **kwargs: same as print()
    """
    coloured_s = "\033[1;%sm%s\033[1;m" % (colour, s)
    print(coloured_s, *args, **kwargs)


def input_col(colour, s):
    """
    Print with a given colour, and read an input string from the terminal.
    IOW, raw_input() with colours.
    colour (string): A ANSI terminal compatible colour code
    s (string): message to print
    return (string): the input from the user
    """
    coloured_s = "\033[1;%sm%s\033[1;m" % (colour, s)
    return input(coloured_s)


def _discard_data(df, data):
    """
    Does nothing, just discard the SEM data received (for spot mode)
    """
    pass


class ArrowFocus(object):
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
                logging.info(u"Moving ebeam focus by %g µm", mov * 1e6)
                f.result()
            if not self._moves_opt:
                pass
            else:
                mov = self._moves_opt.popleft()
                f = self.opt_focus.moveRel({'z': mov})
                logging.info(u"Moving optical focus by %g µm", mov * 1e6)
                f.result()

    def focusByArrow(self, rollback_pos=None):
        """
        rollback_pos (float, float): absolute sem_stage position
        """
        self._focus_thread = threading.Thread(target=self._move_focus,
                                              name="Arrow focus thread")
        self._focus_thread.start()
        init_pos = self.sem_stage.position.value
        try:
            while True:
                c = getch()
                if c in ('R', 'r') and rollback_pos:
                    f = self.sem_stage.moveAbs({'x': rollback_pos[0],
                                                'y': rollback_pos[1]})
                    f.result()
                elif c in ('I', 'i'):
                    f = self.sem_stage.moveAbs(init_pos)
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


def man_calib(logpath, keep_loaded=False):
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
        pressures = chamber.axes["vacuum"].choices
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
            first_hole = second_hole = offset = resa = resb = hfwa = scaleshift = (0, 0)
            scaling = iscale = iscale_xy = (1, 1)
            rotation = irot = ishear = 0
            hole_focus = aligndelphi.SEM_KNOWN_FOCUS
            opt_focus = aligndelphi.OPTICAL_KNOWN_FOCUS
            print_col(ANSI_RED, "Calibration values missing! All the steps will be performed anyway...")
            force_calib = True
        else:
            first_hole, second_hole, hole_focus, opt_focus, offset, scaling, rotation, iscale, irot, iscale_xy, ishear, resa, resb, hfwa, scaleshift = calib_values

            force_calib = False
        print_col(ANSI_CYAN,
                  "**Delphi Manual Calibration steps**\n"
                  "1.Sample holder hole detection\n"
                  "    Current values: 1st hole: " + str(first_hole) + "\n"
                  "                    2st hole: " + str(second_hole) + "\n"
                  "                    hole focus: " + str(hole_focus) + "\n"
                  "2.SEM image calibration\n"
                  "    Current values: resolution-a: " + str(resa) + "\n"
                  "                    resolution-b: " + str(resb) + "\n"
                  "                    hfw-a: " + str(hfwa) + "\n"
                  "                    spot shift: " + str(scaleshift) + "\n"
                  "3.Twin stage calibration\n"
                  "    Current values: offset: " + str(offset) + "\n"
                  "                    scaling: " + str(scaling) + "\n"
                  "                    rotation: " + str(rotation) + "\n"
                  "                    optical focus: " + str(opt_focus) + "\n"
                  "4.Fine alignment\n"
                  "    Current values: scale: " + str(iscale) + "\n"
                  "                    rotation: " + str(irot) + "\n"
                  "                    scale-xy: " + str(iscale_xy) + "\n"
                  "                    shear: " + str(ishear))
        print_col(ANSI_YELLOW,
                  "Note that you should not perform any stage move during the process.\n"
                  "Instead, you may zoom in/out while focusing.")
        print_col(ANSI_BLACK, "Now initializing, please wait...")

        # Default value for the stage offset
        position = (offset[0] * scaling[0], offset[1] * scaling[1])

        if keep_loaded and chamber.position.value["vacuum"] == vacuum_pressure:
            logging.info("Skipped optical lens detection, will use previous value %s", position)
        else:
            # Move to the overview position first
            f = chamber.moveAbs({"vacuum": overview_pressure})
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
                f = aligndelphi.LensAlignment(overview_ccd, sem_stage, logpath)
                position = f.result()
            except IOError as ex:
                logging.warning("Failed to locate the optical lens (%s), will use previous value %s",
                                ex, position)

            # Just to check if move makes sense
            f = sem_stage.moveAbs({"x": position[0], "y": position[1]})
            f.result()

            # Move to SEM
            f = chamber.moveAbs({"vacuum": vacuum_pressure})
            f.result()

        # Set basic e-beam settings
        escan.spotSize.value = 2.7
        escan.accelVoltage.value = 5300  # V
        # Without automatic blanker, the background subtraction doesn't work
        if (model.hasVA(escan, "blanker") and  # For simulator
            None in escan.blanker.choices and
            escan.blanker.value is not None):
            logging.warning("Blanker set back to automatic")
            escan.blanker.value = None

        # Detect the holes/markers of the sample holder
        while True:
            ans = "Y" if force_calib else None
            while ans not in YES_NO_CHARS:
                ans = input_col(ANSI_MAGENTA,
                                "Do you want to execute the sample holder hole detection? [Y/n]")
            if ans in YES_CHARS:
                # Move Phenom sample stage next to expected hole position
                sem_stage.moveAbsSync(aligndelphi.SHIFT_DETECTION)
                ebeam_focus.moveAbsSync({"z": hole_focus})
                # Set the FoV to almost 2mm
                escan.horizontalFoV.value = escan.horizontalFoV.range[1]
                input_col(ANSI_BLUE,
                          "Please turn on the SEM stream and focus the SEM image. Then turn off the stream and press Enter...")
                print_col(ANSI_BLACK,"Trying to detect the holes/markers, please wait...")
                try:
                    hole_detectionf = aligndelphi.HoleDetection(detector, escan, sem_stage,
                                                                ebeam_focus, manual=True, logpath=logpath)
                    new_first_hole, new_second_hole, new_hole_focus = hole_detectionf.result()
                    print_col(ANSI_CYAN,
                              "Values computed: 1st hole: " + str(new_first_hole) + "\n"
                              "                 2st hole: " + str(new_second_hole) + "\n"
                              "                 hole focus: " + str(new_hole_focus))
                    ans = "Y" if force_calib else None
                    while ans not in YES_NO_CHARS:
                        ans = input_col(ANSI_MAGENTA,
                                        "Do you want to update the calibration file with these values? [Y/n]")
                    if ans in YES_CHARS:
                        first_hole, second_hole, hole_focus = new_first_hole, new_second_hole, new_hole_focus
                        calibconf.set_sh_calib(shid, first_hole, second_hole, hole_focus, opt_focus, offset,
                               scaling, rotation, iscale, irot, iscale_xy, ishear,
                               resa, resb, hfwa, scaleshift)
                        print_col(ANSI_BLACK, "Calibration file is updated.")
                    break
                except IOError:
                    print_col(ANSI_RED, "Sample holder hole detection failed.")
            else:
                break

        while True:
            ans = "Y" if force_calib else None
            while ans not in YES_NO_CHARS:
                ans = input_col(ANSI_MAGENTA,
                                "Do you want to execute the SEM image calibration? [Y/n]")
            if ans in YES_CHARS:
                # Resetting shift parameters, to not take them into account during calib
                blank_md = dict.fromkeys(aligndelphi.MD_CALIB_SEM, (0, 0))
                escan.updateMetadata(blank_md)

                # We measure the shift in the area just behind the hole where there
                # are always some features plus the edge of the sample carrier. For
                # that reason we use the focus measured in the hole detection step
                sem_stage.moveAbsSync(aligndelphi.SHIFT_DETECTION)

                ebeam_focus.moveAbsSync({"z": hole_focus})
                try:
                    # Compute spot shift percentage
                    print_col(ANSI_BLACK, "Spot shift measurement in progress, please wait...")
                    f = aligndelphi.ScaleShiftFactor(detector, escan, logpath)
                    new_scaleshift = f.result()

                    # Compute resolution-related values.
                    print_col(ANSI_BLACK, "Calculating resolution shift, please wait...")
                    resolution_shiftf = aligndelphi.ResolutionShiftFactor(detector, escan, logpath)
                    new_resa, new_resb = resolution_shiftf.result()

                    # Compute HFW-related values
                    print_col(ANSI_BLACK, "Calculating HFW shift, please wait...")
                    hfw_shiftf = aligndelphi.HFWShiftFactor(detector, escan, logpath)
                    new_hfwa = hfw_shiftf.result()

                    print_col(ANSI_CYAN,
                              "Values computed: resolution-a: " + str(new_resa) + "\n"
                              "                 resolution-b: " + str(new_resb) + "\n"
                              "                 hfw-a: " + str(new_hfwa) + "\n"
                              "                 spot shift: " + str(new_scaleshift))
                    ans = "Y" if force_calib else None
                    while ans not in YES_NO_CHARS:
                        ans = input_col(ANSI_MAGENTA,
                                        "Do you want to update the calibration file with these values? [Y/n]")
                    if ans in YES_CHARS:
                        resa, resb, hfwa, scaleshift = new_resa, new_resb, new_hfwa, new_scaleshift
                        calibconf.set_sh_calib(shid, first_hole, second_hole, hole_focus, opt_focus, offset,
                               scaling, rotation, iscale, irot, iscale_xy, ishear,
                               resa, resb, hfwa, scaleshift)
                        print_col(ANSI_BLACK, "Calibration file is updated.")
                    break
                except IOError:
                    print_col(ANSI_RED, "SEM image calibration failed.")
            else:
                break

        # Update the SEM metadata to have the spots already at corrected place
        escan.updateMetadata({
            model.MD_RESOLUTION_SLOPE: resa,
            model.MD_RESOLUTION_INTERCEPT: resb,
            model.MD_HFW_SLOPE: hfwa,
            model.MD_SPOT_SHIFT: scaleshift
        })

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
                ans = input_col(ANSI_MAGENTA,
                                "Do you want to execute the twin stage calibration? [Y/n]")
            if ans in YES_CHARS:
                # Configure CCD and e-beam to write CL spots
                ccd.binning.value = ccd.binning.clip((4, 4))
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
                print_col(ANSI_BLUE,
                          "Please turn on the Optical stream, set Power to 0 Watt "
                          "and focus the image so you have a clearly visible spot.\n"
                          "Use the up and down arrows or the mouse to move the "
                          "optical focus and right and left arrows to move the SEM focus. "
                          "Then turn off the stream and press Enter...")
                if not force_calib:
                    print_col(ANSI_YELLOW,
                              "If you cannot see the whole source background (bright circle) "
                              "you may try to move to the already known offset position. \n"
                              "To do this press the R key at any moment and use I to go back "
                              "to the initial position.")
                    rollback_pos = (offset[0] * scaling[0], offset[1] * scaling[1])
                else:
                    rollback_pos = None
                ar = ArrowFocus(sem_stage, focus, ebeam_focus, ccd.depthOfField.value, 10e-6)
                ar.focusByArrow(rollback_pos)

                # Did the user adjust the ebeam-focus? If so, let's use this,
                # as it's probably better than the focus for the hole.
                new_ebeam_focus = ebeam_focus.position.value.get('z')
                new_hole_focus = new_ebeam_focus + aligndelphi.GOOD_FOCUS_OFFSET
                if not util.almost_equal(new_hole_focus, hole_focus, atol=10e-6):
                    print_col(ANSI_CYAN, "Updating e-beam focus: %s (ie, hole focus: %s)" %
                                         (new_ebeam_focus, new_hole_focus))
                    good_focus = new_ebeam_focus
                    hole_focus = new_hole_focus

                detector.data.unsubscribe(_discard_data)
                print_col(ANSI_BLACK, "Twin stage calibration starting, please wait...")
                try:
                    # TODO: the first point (at 0,0) isn't different from the next 4 points,
                    # excepted it might be a little harder to focus.
                    # => use the same code for all of them
                    align_offsetf = aligndelphi.AlignAndOffset(ccd, detector, escan, sem_stage,
                                                               opt_stage, focus, logpath)
                    align_offset = align_offsetf.result()
                    new_opt_focus = focus.position.value.get('z')

                    # If the offset is large, it can prevent the SEM stage to follow
                    # the optical stage. If it's really large (eg > 1mm) it will
                    # even prevent from going to the calibration locations.
                    # So warn about this, as soon as we detect it. It could be
                    # caused either due to a mistake in the offset detection, or
                    # because the reference switch of the optical axis is not
                    # centered (enough) on the axis. In such case, a technician
                    # should open the sample holder and move the reference switch.
                    # Alternatively, we could try to be more clever and support
                    # the range of the tmcm axes to be defined per sample
                    # holder, and set some asymmetric range (to reflect the fact
                    # that 0 is not at the center).
                    for a, trans in zip(("x", "y"), align_offset):
                        # SEM pos = Opt pos + offset
                        rng_sem = sem_stage.axes[a].range
                        rng_opt = opt_stage.axes[a].range
                        if (rng_opt[0] + trans < rng_sem[0] or
                            rng_opt[1] + trans > rng_sem[1]):
                            logging.info("Stage align offset = %s, which could cause "
                                         "moves on the SEM stage out of range (on axis %s)",
                                         align_offset, a)
                            input_col(ANSI_RED,
                                      "Twin stage offset on axis %s is %g mm, which could cause moves out of range.\n"
                                      "Check that the reference switch in the sample holder is properly at the center." %
                                      (a, trans * 1e3))

                    def ask_user_to_focus(n):
                        detector.data.subscribe(_discard_data)
                        input_col(ANSI_BLUE,
                                  "About to calculate rotation and scaling (%d/4). " % (n + 1,) +
                                  "Please turn on the Optical stream, "
                                  "set Power to 0 Watt and focus the image using the mouse "
                                  "so you have a clearly visible spot. \n"
                                  "If you do not see a spot nor the source background, "
                                  "move the sem-stage from the command line by steps of 200um "
                                  "in x and y until you can see the source background at the center. \n"
                                  "Then turn off the stream and press Enter...")
                        # TODO: use ArrowFocus() too?
                        print_col(ANSI_BLACK, "Calculating rotation and scaling (%d/4), please wait..." % (n + 1,))
                        detector.data.unsubscribe(_discard_data)

                    f = aligndelphi.RotationAndScaling(ccd, detector, escan, sem_stage,
                                                       opt_stage, focus, align_offset,
                                                       manual=ask_user_to_focus, logpath=logpath)
                    acc_offset, new_rotation, new_scaling = f.result()

                    # Offset is divided by scaling, since Convert Stage applies scaling
                    # also in the given offset
                    pure_offset = acc_offset
                    new_offset = ((acc_offset[0] / new_scaling[0]), (acc_offset[1] / new_scaling[1]))

                    print_col(ANSI_CYAN,
                              "Values computed: offset: " + str(new_offset) + "\n"
                              "                 scaling: " + str(new_scaling) + "\n"
                              "                 rotation: " + str(new_rotation) + "\n"
                              "                 optical focus: " + str(new_opt_focus))
                    ans = "Y" if force_calib else None
                    while ans not in YES_NO_CHARS:
                        ans = input_col(ANSI_MAGENTA,
                                        "Do you want to update the calibration file with these values? [Y/n]")
                    if ans in YES_CHARS:
                        offset, scaling, rotation, opt_focus = new_offset, new_scaling, new_rotation, new_opt_focus
                        calibconf.set_sh_calib(shid, first_hole, second_hole, hole_focus, opt_focus, offset,
                               scaling, rotation, iscale, irot, iscale_xy, ishear,
                               resa, resb, hfwa, scaleshift)
                        print_col(ANSI_BLACK, "Calibration file is updated.")
                    break
                except IOError:
                    print_col(ANSI_RED, "Twin stage calibration failed.")
            else:
                break

        while True:
            ans = "Y" if force_calib else None
            while ans not in YES_NO_CHARS:
                ans = input_col(ANSI_MAGENTA,
                                "Do you want to execute the fine alignment? [Y/n]")
            if ans in YES_CHARS:
                # Return to the center so fine alignment can be executed just after calibration
                f = opt_stage.moveAbs({"x": 0, "y": 0})
                f.result()
                if pure_offset is not None:
                    f = sem_stage.moveAbs({"x":pure_offset[0], "y":pure_offset[1]})
                elif offset is not None:
                    f = sem_stage.moveAbs({"x":offset[0] * scaling[0], "y":offset[1] * scaling[1]})
                else:
                    f = sem_stage.moveAbs({"x":position[0], "y":position[1]})

                fof = focus.moveAbs({"z": opt_focus})
                fef = ebeam_focus.moveAbs({"z": good_focus})
                f.result()
                fof.result()
                fef.result()

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
                print_col(ANSI_BLUE,
                          "Please turn on the Optical stream, set Power to 0 Watt "
                          "and focus the image so you have a clearly visible spot.\n"
                          "Use the up and down arrows or the mouse to move the "
                          "optical focus and right and left arrows to move the SEM focus. "
                          "Then turn off the stream and press Enter...")
                ar = ArrowFocus(sem_stage, focus, ebeam_focus, ccd.depthOfField.value, 10e-6)
                ar.focusByArrow()
                detector.data.unsubscribe(_discard_data)

                print_col(ANSI_BLACK, "Fine alignment in progress, please wait...")

                # restore CCD settings (as the GUI/user might have changed them)
                ccd.binning.value = (1, 1)
                ccd.resolution.value = ccd.resolution.range[1]
                ccd.exposureTime.value = 900e-03
                # Center (roughly) the spot on the CCD
                f = spot.CenterSpot(ccd, sem_stage, escan, spot.ROUGH_MOVE, spot.STAGE_MOVE, detector.data)
                dist, vect = f.result()
                if dist is None:
                    logging.warning("Failed to find a spot, twin stage calibration might have failed")

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
                    print_col(ANSI_CYAN,
                              "Values computed: scale: " + str(new_iscale) + "\n"
                              "                 rotation: " + str(new_irot) + "\n"
                              "                 scale-xy: " + str(new_iscale_xy) + "\n"
                              "                 shear: " + str(new_ishear))
                    ans = "Y" if force_calib else None
                    while ans not in YES_NO_CHARS:
                        ans = input_col(ANSI_MAGENTA,
                                        "Do you want to update the calibration file with these values? [Y/n]")
                    if ans in YES_CHARS:
                        iscale, irot, iscale_xy, ishear = new_iscale, new_irot, new_iscale_xy, new_ishear
                        calibconf.set_sh_calib(shid, first_hole, second_hole, hole_focus, opt_focus, offset,
                               scaling, rotation, iscale, irot, iscale_xy, ishear,
                               resa, resb, hfwa, scaleshift)
                        print_col(ANSI_BLACK, "Calibration file is updated.")
                    break
                except ValueError:
                    print_col(ANSI_RED, "Fine alignment failed.")
            else:
                break
    except Exception:
        logging.exception("Unexpected failure during calibration")
    finally:
        aligndelphi.restore_hw_settings(escan, ccd, hw_settings)

        # Store the final version of the calibration file in the log folder
        try:
            shutil.copy(calibconf.file_path, logpath)
        except Exception:
            logging.info("Failed to log calibration file", exc_info=True)

        if not keep_loaded:
            # Eject the sample holder
            print_col(ANSI_BLACK, "Calibration ended, now ejecting sample, please wait...")
            f = chamber.moveAbs({"vacuum": vented_pressure})
            f.result()

        ans = input_col(ANSI_MAGENTA, "Press Enter to close")


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
    parser.add_argument("--keep-loaded", dest="keep_loaded", action="store_true", default=False,
                        help="Do not force unloading/loading the sample to acquire a overview image")

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
        man_calib(logpath, options.keep_loaded)
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
