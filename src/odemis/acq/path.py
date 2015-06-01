# -*- coding: utf-8 -*-
"""
Created on 7 May 2015

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

from __future__ import division

import logging
import math
from odemis import model
from odemis.acq import stream


# Dict includes all the modes available and the corresponding component axis or
# VA values
# {Mode: (detector_needed, {role: {axis/VA: value}})}
# TODO: how about the filter? 'mirror-align' should force it to "pass-through" (and then put it back to what it was?)
# TODO: another mode fo specfib-align?
MODES = {'ar': ("ccd",
               {'lens-switch': {'rx': math.radians(90)}, 'ar-det-selector': {'rx': 0},
               'ar-spec-selector': {'rx': 0}}),
         'spectral': ("spectrometer",
                {'lens-switch': {'rx': math.radians(90)}, 'ar-det-selector': {'rx': 0},
               'ar-spec-selector': {'rx': math.radians(90)}, 'spectrograph': {'spec-det-selector': 0}}),
         'mirror-align': ("ccd",
                {'lens-switch': {'rx': 0}, 'ar-det-selector': {'rx': 0},
               'ar-spec-selector': {'rx': 0}}),
         'cli': ("cl-detector",  # cli
                {'lens-switch': {'rx': math.radians(90)}, 'ar-det-selector': {'rx': math.radians(90)}}),
         'monochromator': ("monochromator",
                {'lens-switch': {'rx': math.radians(90)}, 'ar-det-selector': {'rx': 0},
               'ar-spec-selector': {'rx': math.radians(90)}, 'spectrograph': {'spec-det-selector': math.radians(90)}})}

# Use subset for modes guessed
GUESS_MODES = MODES.copy()
del GUESS_MODES['mirror-align']  # No stream should ever imply alignment mode



class OpticalPathManager(object):
    """
    The purpose of this module is setting the physical components contained in
    the optical path of a SPARC system to the right position/configuration with
    respect to the mode given.
    """
    def __init__(self, microscope):
        """
        microscope (Microscope): the whole microscope component, thus it can
            handle all the components needed
        """
        self.microscope = microscope
        self.known_comps = dict()  # keep list of already accessed components
        # Removes modes which are not supported by the current microscope
        self._modes = MODES.copy()
        for m, (det, conf) in self._modes.items():
            try:
                comp = model.getComponent(role=det)
            except LookupError:
                logging.debug("Removing mode %s, which is not supported", m)
                del self._modes[m]

    def setPath(self, mode):
        """
        Given a particular mode it sets all the necessary components of the
        optical path (found through the microscope component) to the
        corresponding positions.
        mode (str): The optical path mode
        raises:
                ValueError if the given mode does not exist
                IOError if a detector is missing
        """
        if mode not in self._modes:
            raise ValueError("Mode given does not exist")

        modeconf = self._modes[mode][1]
        fmoves = []  # moves in progress
        for comp_role, conf in modeconf.items():
            # Try to access the component needed
            try:
                if comp_role in self.known_comps:
                    # Reuse component to avoid extensive thread usage
                    comp = self.known_comps[comp_role]
                else:
                    comp = model.getComponent(role=comp_role)
                    self.known_comps[comp_role] = comp
            except LookupError:
                logging.debug("Failed to find component %s, skipping it", comp_role)
                continue

            mv = {}
            for axis, pos in conf.items():
                if axis in comp.axes:
                    mv[axis] = pos
                else:
                    logging.debug("Not moving axis %s.%s as it is not present", comp_role, axis)

            fmoves.append(comp.moveAbs(mv))
        # wait for all the moves to be completed
        for f in fmoves:
            f.result()

    def guessMode(self, guess_stream):
        """
        Given a stream and by checking its components (e.g. role of detectors)
        guesses and returns the corresponding optical path mode.
        guess_stream (object): The given optical stream
        returns (str): Mode estimated
        raises:
                ValueError if no mode can be inferred for the given stream
                IOError if given object is not a stream
        """
        # Handle multiple detector streams
        if isinstance(guess_stream, stream.Stream):
            if isinstance(guess_stream, stream.MultipleDetectorStream):
                for st in guess_stream.streams:
                    for mode, conf in GUESS_MODES.items():
                        if conf[0] == st.detector.role:
                            return mode
            else:
                for mode, conf in GUESS_MODES.items():
                    if conf[0] == guess_stream.detector.role:
                        return mode
            # In case no mode was found yet
            raise ValueError("No mode can be inferred for the given stream")
        else:
            raise IOError("Given object is not a stream")
