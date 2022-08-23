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

from concurrent.futures.thread import ThreadPoolExecutor
import copy
import logging
import math
from odemis import model, util
from odemis.acq import stream
from odemis.model import BAND_PASS_THROUGH, MD_POL_NONE
from odemis.util import TimeoutError
import queue
import re
import time

GRATING_NOT_MIRROR = "CONST:NOTMIRROR"  # A special string so that no grating position can be like this

ACQ_QUALITY_FAST = 0
ACQ_QUALITY_BEST = 1

TEMP_EPSILON = 3  # °C

# Dict includes all the modes available and the corresponding component axis or
# VA values
# {Mode: (detector_needed, {role: {axis/VA: value}})}
SPARC_MODES = {'ar': ("ccd",
                {'lens-switch': {'rx': math.radians(90)},
                 'ar-spec-selector': {'rx': 0},
                 'ar-det-selector': {'rx': 0},
                }),
         'cli': ("cl-detector",  # CL-intencity: PMT just after the parabolic mirror
                {'lens-switch': {'rx': math.radians(90)},
                 'ar-spec-selector': {'rx': 0},
                 'ar-det-selector': {'rx': math.radians(90)},
                }),
         'spectral': ("spectrometer",
                {'lens-switch': {'rx': math.radians(90)},
                 'ar-spec-selector': {'rx': math.radians(90)},
                 'spec-det-selector': {'rx': 0},
                }),
         'monochromator': ("monochromator",
                {'lens-switch': {'rx': math.radians(90)},
                 'ar-spec-selector': {'rx': math.radians(90)},
                 'spec-det-selector': {'rx': math.radians(90)},
                }),
         'mirror-align': ("ccd",
                {'lens-switch': {'rx': 0},
                 'filter': {'band': BAND_PASS_THROUGH},
                 'ar-spec-selector': {'rx': 0},
                 'ar-det-selector': {'rx': 0},
                }),
         'chamber-view': ("ccd",
                {'lens-switch': {'rx': math.radians(90)},
                 'filter': {'band': BAND_PASS_THROUGH},
                 'ar-spec-selector': {'rx': 0},
                 'ar-det-selector': {'rx': 0},
                }),
         'fiber-align': ("spectrometer",
                {'lens-switch': {'rx': math.radians(90)},
                 'filter': {'band': BAND_PASS_THROUGH},
                 'ar-spec-selector': {'rx': math.radians(90)},
                 'spec-det-selector': {'rx': 0},
                 'spectrograph': {'slit-in': 500e-6},
                }),
         }

# Not much is needed, as most of the optical path is guessed from the affects
# It's still important to have every possible detector roles listed, for the
# guessing methods to know which detector is optical.
SPARC2_MODES = {
            'ar': (r"ccd.*",
                {'lens-switch': {'x': ("MD:" + model.MD_FAV_POS_ACTIVE, 'on')},
                 'lens-mover': {'x': "MD:" + model.MD_FAV_POS_ACTIVE},
                 'slit-in-big': {'x': 'on'},  # fully opened
                 'spectrograph': {'grating': 'mirror'},
                 # 'cl-det-selector': {'x': 'off'},
                 # 'spec-selector': {'x': "MD:" + model.MD_FAV_POS_DEACTIVE},
                 # 'spec-det-selector': {'rx': 0},
                 'chamber-light': {'power': 'off'},
                }),
            'ek': (r"ccd.*",
                {'lens-switch': {'x': "MD:" + model.MD_FAV_POS_ACTIVE},  # ek mode available only if MD_FAV_POS_ACTIVE
                 'lens-mover': {'x': "MD:" + model.MD_FAV_POS_ACTIVE},
                 'slit-in-big': {'x': 'off'},  # closed
                 'spectrograph': {'grating': GRATING_NOT_MIRROR},
                 # Typically the grating should be non-mirror but we leave it up to the user/GUI.
                 # 'cl-det-selector': {'x': 'off'},
                 # 'spec-selector': {'x': "MD:" + model.MD_FAV_POS_DEACTIVE},
                 # 'spec-det-selector': {'rx': 0},
                 'chamber-light': {'power': 'off'},
                }),
            'cli': ("cl-detector",
                {'lens-switch': {'x': ("MD:" + model.MD_FAV_POS_ACTIVE, 'on')},
                 'lens-mover': {'x': "MD:" + model.MD_FAV_POS_ACTIVE},
                 # 'cl-det-selector': {'x': 'on'},
                 # 'spec-selector': {'x': "MD:" + model.MD_FAV_POS_DEACTIVE},
                 # there is also the cl-filter, but that's just up to the user
                 'chamber-light': {'power': 'off'},
                 'pol-analyzer': {'pol': MD_POL_NONE},
                }),
            'spectral': (r"spectrometer.*",
                {'lens-switch': {'x': ("MD:" + model.MD_FAV_POS_DEACTIVE, 'off')},
                 'lens-mover': {'x': "MD:" + model.MD_FAV_POS_ACTIVE},
                 'slit-in-big': {'x': 'off'},  # closed
                 # The grating is left as-is, but in practice, the GUI takes care
                 # of picking a grating (non mirror) by default, using a "local axis"
                 # on the stream.
                 'chamber-light': {'power': 'off'},
                 'pol-analyzer': {'pol': MD_POL_NONE},
                }),
            'streak-align': ("streak-ccd",  # alignment tab
                {'lens-switch': {'x': ("MD:" + model.MD_FAV_POS_DEACTIVE, 'off')},
                 'lens-mover': {'x': "MD:" + model.MD_FAV_POS_ACTIVE},
                 'slit-in-big': {'x': 'on'},  # fully opened (independent of spg.slit-in)
                 'chamber-light': {'power': 'off'},
                 'pol-analyzer': {'pol': MD_POL_NONE},
                }),
            'streak-focus': ("streak-ccd",  # manual focus in alignment tab
                {'lens-switch': {'x': ("MD:" + model.MD_FAV_POS_DEACTIVE, 'off')},
                 'lens-mover': {'x': "MD:" + model.MD_FAV_POS_ACTIVE},
                 'slit-in-big': {'x': 'off'},  # closed
                 'filter': {'band': BAND_PASS_THROUGH},
                 'spectrograph': {'slit-in': 10e-6},  # slit to the minimum
                 'chamber-light': {'power': 'off'},
                 'pol-analyzer': {'pol': MD_POL_NONE},
                }),
            'temporal-spectrum': ("streak-ccd",  # acquisition tab
                {'lens-switch': {'x': ("MD:" + model.MD_FAV_POS_DEACTIVE, 'off')},
                 'lens-mover': {'x': "MD:" + model.MD_FAV_POS_ACTIVE},
                 'slit-in-big': {'x': 'off'},  # closed
                 'filter': {'band': BAND_PASS_THROUGH},
                 'chamber-light': {'power': 'off'},
                 'pol-analyzer': {'pol': MD_POL_NONE},
                 }),
            'monochromator': ("monochromator",
                {'lens-switch': {'x': ("MD:" + model.MD_FAV_POS_DEACTIVE, 'off')},
                 'lens-mover': {'x': "MD:" + model.MD_FAV_POS_ACTIVE},
                 'slit-in-big': {'x': 'off'},  # closed
                 # 'cl-det-selector': {'x': 'off'},
                 # TODO
                 # 'spec-det-selector': {'rx': math.radians(90)},
                 # 'spec-selector': {'x': "MD:" + model.MD_FAV_POS_ACTIVE},
                 'spectrograph': {'grating': GRATING_NOT_MIRROR},
                 'chamber-light': {'power': 'off'},
                 'pol-analyzer': {'pol': MD_POL_NONE},
                }),
            'time-correlator': ("time-correlator",
                {'lens-switch': {'x': ("MD:" + model.MD_FAV_POS_DEACTIVE, 'off')},
                 'lens-mover': {'x': "MD:" + model.MD_FAV_POS_ACTIVE},
                 'chamber-light': {'power': 'off'},
                 'pol-analyzer': {'pol': MD_POL_NONE},
                }),
            'mirror-align': (r"ccd.*",  # Also used for lens alignment
                {'lens-switch': {'x': ("MD:" + model.MD_FAV_POS_DEACTIVE, 'off')},
                 'lens-mover': {'x': "MD:" + model.MD_FAV_POS_ACTIVE},
                 'slit-in-big': {'x': 'on'},
                 'filter': {'band': BAND_PASS_THROUGH},
                 'spectrograph': {'grating': 'mirror'},
                 # 'spec-selector': {'x': "MD:" + model.MD_FAV_POS_DEACTIVE},
                 # 'cl-det-selector': {'x': 'off'},
                 # 'spec-det-selector': {'rx': 0},
                 'chamber-light': {'power': 'off'},
                 'pol-analyzer': {'pol': MD_POL_NONE},
                }),
            'lens2-align': (r"ccd.*",  # Same as the mirror-align, but with the lens-switch active
                {'lens-switch': {'x': "MD:" + model.MD_FAV_POS_ACTIVE},
                 # lens2-align changes the metadata of lens switch
                 'lens-mover': {'x': "MD:" + model.MD_FAV_POS_ACTIVE},
                 'slit-in-big': {'x': 'on'},
                 'filter': {'band': BAND_PASS_THROUGH},
                 'spectrograph': {'grating': 'mirror'},
                 'chamber-light': {'power': 'off'},
                 'pol-analyzer': {'pol': MD_POL_NONE},
                 }),
            'chamber-view': (r"ccd.*",  # Same as AR but SEM is disabled and a light may be used
                {'lens-switch': {'x': ("MD:" + model.MD_FAV_POS_ACTIVE, 'on')},
                 'lens-mover': {'x': "MD:" + model.MD_FAV_POS_ACTIVE},
                 'slit-in-big': {'x': 'on'},
                 'filter': {'band': BAND_PASS_THROUGH},
                 'spectrograph': {'grating': 'mirror'},
                 # Note: focus is store/restore when going to/from this mode
                 # 'spec-selector': {'x': "MD:" + model.MD_FAV_POS_DEACTIVE},
                 # 'cl-det-selector': {'x': 'off'},
                 # 'spec-det-selector': {'rx': 0},
                 'chamber-light': {'power': 'on'},
                 'pol-analyzer': {'pol': MD_POL_NONE},
                }),
            'spec-focus': (r"ccd.*",  # TODO: only use "focus" as target?
                {'lens-switch': {'x': ("MD:" + model.MD_FAV_POS_DEACTIVE, 'off')},
                 'lens-mover': {'x': "MD:" + model.MD_FAV_POS_ACTIVE},
                 'slit-in-big': {'x': 'off'},  # closed
                 'filter': {'band': BAND_PASS_THROUGH},
                 'spectrograph': {'slit-in': 10e-6},  # slit to the minimum
                 # 'spec-selector': {'x': "MD:" + model.MD_FAV_POS_DEACTIVE},
                 # 'cl-det-selector': {'x': 'off'},
                 # 'spec-det-selector': {'rx': 0},
                 'chamber-light': {'power': 'off'},
                 'pol-analyzer': {'pol': MD_POL_NONE},
                }),
            'fiber-align': ("fiber-aligner",
                {'lens-switch': {'x': ("MD:" + model.MD_FAV_POS_DEACTIVE, 'off')},
                 'lens-mover': {'x': "MD:" + model.MD_FAV_POS_ACTIVE},
                 'filter': {'band': BAND_PASS_THROUGH},
                 # 'spec-selector': {'x': "MD:" + model.MD_FAV_POS_ACTIVE},
                 # Grating "mirror" forces wavelength to zero order and saves the
                 # current values so we can restore them
                 'spectrograph-dedicated': {'slit-in': 500e-6, 'grating': 'mirror'},
                 'chamber-light': {'power': 'off'},
                 'pol-analyzer': {'pol': MD_POL_NONE},
                }),
            'spec-fiber-focus': ("focus",  # if multiple focusers, the detector should be passed, to pick the right path
                {'lens-switch': {'x': ("MD:" + model.MD_FAV_POS_DEACTIVE, 'off')},
                 'lens-mover': {'x': "MD:" + model.MD_FAV_POS_ACTIVE},
                 'filter': {'band': BAND_PASS_THROUGH},
                 # In the current convention, only the spectrograph-dedicated
                 # can be after the fiber, so no need to check for spectrograph
                 'spectrograph-dedicated': {'slit-in': 50e-6},  # small, to get a sharp line, but enought to get some light
                 'chamber-light': {'power': 'off'},
                 'pol-analyzer': {'pol': MD_POL_NONE},
                }),
         }

# Currently not used as-is, mostly here to make guessMode() happy.
# The only thing it does is to turns on the fan iff SEM acquisition in best
# acquisition quality. This is done via dedicated code. The precise rule is the
# following:
# * In acquisition FAST, the fan is always let on
# * In acquisition BEST, the fan is turned off for every stream not using the CCD
# One difficulty is to detect that the fan is not in used even for optical
# streams (eg, because the CCD is water-cooled).

# TODO: for confocal, detect fluo mode for _any_ photo-detector
# TODO: handle time-correlator and det-selector (using axis choice pos -> detectors)
SECOM_MODES = {
            'fluo': ("ccd",
                {'ccd': {'fanSpeed': 1},
                }),
            'confocal': (r"photo-detector(\d*)$",
                {
                }),
            'overlay': ("ccd",
                {'ccd': {'fanSpeed': 1},
                }),
            'sed': ("se-detector",
                {'ccd': {'fanSpeed': 0}, # To avoid vibrations
                }),
            'bsd': ("bs-detector",
                {'ccd': {'fanSpeed': 0}, # To avoid vibrations
                }),
            'flim': ("time-correlator",
                {
                }),
            'flim-setup': ("tc-detector",
                {
                }),
            }

ALIGN_MODES = {'mirror-align', 'lens2-align', 'ek-align', 'chamber-view',
               'fiber-align', 'streak-align', 'spec-focus', 'spec-fiber-focus', 'streak-focus'}


class OneTaskExecutor(ThreadPoolExecutor):
    """
    Only one task at a time is executed. If several tasks are queued, only the
    last one is executed.
    """

    def __init__(self):
        super(OneTaskExecutor, self).__init__(max_workers=1)

    # Override ThreadPoolExecutor.submit()
    def submit(self, fn, *args, **kwargs):
        # Cancels all the previous tasks which didn't start yet
        # Note: in practice, there should be only one task at most queued.
        while True:
            try:
                self._work_queue.get(block=False)
                logging.debug("Canceling 1 task before starting the next one")
            except queue.Empty:
                break

        # Add this new function call to the _work_queue
        return super(OneTaskExecutor, self).submit(fn, *args, **kwargs)


# TODO: Could be moved to util
def affectsGraph(microscope):
    """
    Creates a graph based on the affects lists of the microscope components.
    returns (dict str->(set of str))
    """
    graph = {}
    for comp in model.getComponents():
        graph[comp.name] = set(comp.affects.value)
    return graph


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
        self._graph = affectsGraph(self.microscope)
        self._chamber_view_own_focus = False

        # Use subset for modes guessed
        if microscope.role == "sparc2":
            self._modes = copy.deepcopy(SPARC2_MODES)
        elif microscope.role in ("sparc-simplex", "sparc"):
            self._modes = copy.deepcopy(SPARC_MODES)
        elif microscope.role in ("secom", "delphi"):
            self._modes = copy.deepcopy(SECOM_MODES)
        else:
            raise NotImplementedError("Microscope role '%s' unsupported" % (microscope.role,))

        # Currently only used with the SECOM/DELPHI
        self.quality = ACQ_QUALITY_FAST

        # keep list of all components, to avoid creating new proxies
        # every time the mode changes
        self._cached_components = model.getComponents()

        # All the actuators in the microscope, to cache proxy's to them
        self._actuators = []
        for comp in self._cached_components:
            if hasattr(comp, 'axes') and isinstance(comp.axes, dict):
                self._actuators.append(comp)

        # last known axes position (before going to an alignment mode)
        self._stored = {}  # (str, str) -> pos: (comp role, axis name) -> position
        self._last_mode = None  # previous mode that was set

        # Removes modes which are not supported by the current microscope
        for m, (det, conf) in list(self._modes.items()):
            try:
                comp = self._getComponent(det)
            except LookupError:
                logging.debug("Removing mode %s, which is not supported", m)
                del self._modes[m]

        # Create the guess information out of the mode
        # TODO: just make it a dict comprole -> mode
        self.guessed = self._modes.copy()
        # No stream should ever imply alignment mode
        for m in ALIGN_MODES:
            try:
                del self.guessed[m]
            except KeyError:
                pass  # Mode to delete is just not there

        if self.microscope.role in ("secom", "delphi"):
            # To record the fan settings when in "fast" acq quality
            try:
                ccd = self._getComponent("ccd")
            except LookupError:
                ccd = None
                # Check that at least it's a confocal microscope
                try:
                    lm = self._getComponent("laser-mirror")
                except LookupError:
                    logging.warning("Couldn't find a CCD on a SECOM/DELPHI")

            self._has_fan_speed = model.hasVA(ccd, "fanSpeed")
            self._has_fan_temp = (model.hasVA(ccd, "targetTemperature") and
                                  not ccd.targetTemperature.readonly)
            # Consider that by default we are in "fast" acquisition, with the fan
            # active (if it ought to be active)
            self._fan_enabled = True
            # Settings of the fan when the fan is in "active cooling" mode
            self._enabled_fan_speed = None
            self._enabled_fan_temp = None

        # Handle different focus for chamber-view (in SPARCv2)
        if "chamber-view" in self._modes:
            self._focus_in_chamber_view = None
            self._focus_out_chamber_view = None
            # Check whether the focus affects the chamber view
            try:
                chamb_det = self._getComponent(self._modes["chamber-view"][0])
                focus = self._getComponent("focus")
                if self.affects(focus.name, chamb_det.name):
                    self._chamber_view_own_focus = True
            except LookupError:
                pass
            if not self._chamber_view_own_focus:
                logging.debug("No focus component affecting chamber")

        # will take care of executing setPath asynchronously
        self._executor = OneTaskExecutor()

    def __del__(self):
        logging.debug("Ending path manager")

        # Restore the spectrometer focus, so that on next start, this value will
        # be used again as "out of chamber view".
        if self._chamber_view_own_focus and self._last_mode == "chamber-view":
            focus_comp = self._getComponent("focus")
            if self._focus_out_chamber_view is not None:
                logging.debug("Restoring focus from before coming to chamber view to %s",
                              self._focus_out_chamber_view)
                try:
                    focus_comp.moveAbsSync(self._focus_out_chamber_view)
                except IOError as e:
                    logging.info("Actuator move failed giving the error %s", e)

        try:
            self._executor.shutdown(wait=False)
        except AttributeError:
            pass  # Not created

    def _getComponent(self, role):
        """
        same as model.getComponent, but optimised by caching the result.
        Uses regex to match the name to a list of cached components

        return Component
        raise LookupError: if matching component not found
        """
        # if we have not returned raise an exception
        for comp in self._cached_components:
            if comp.role is not None and re.match(role + "$", comp.role):
                return comp
        # if not found...
        raise LookupError("No component with the role %s" % (role,))

    def setAcqQuality(self, quality):
        """
        Update the acquisition quality expected. Depending on the quality,
        some hardware settings will be adjusted.
        quality (ACQ_QUALITY): the acquisition quality
        """
        assert quality in (ACQ_QUALITY_FAST, ACQ_QUALITY_BEST)

        if quality == self.quality:
            return
        self.quality = quality

        if self.microscope.role in ("secom", "delphi"):
            if quality == ACQ_QUALITY_FAST:
                # Restore the fan (if it was active before)
                try:
                    self._setCCDFan(True)
                except Exception:
                    # This can happen mainly if the hardware is in a bad state
                    # let's not make a big fuss: only report the error.
                    logging.exception("Failed to turn on CCD fan")

            # Don't turn off the fan if BEST: first wait for setPath()

    def setPath(self, mode, detector=None):
        """
        Given a particular mode it sets all the necessary components of the
        optical path (found through the microscope component) to the
        corresponding positions.
        path (stream.Stream or str): The stream or the optical path mode
        detector (Component or None): The detector which will be targeted on this
          path. This can only be set if the path is a str (optical mode). That
          is useful in case the mode can be used with multiple detectors (eg,
          fiber-align on a SPARC with multiple spectrometers). When path is a
          Stream, the Stream.detector is always used.
        return (Future): a Future allowing to follow the status of the path
          update.
        raises (via the future):
            ValueError if the given mode does not exist
            IOError if a detector is missing
        """
        f = self._executor.submit(self._doSetPath, mode, detector)

        return f

    def _doSetPath(self, path, detector):
        """
        Actual implementation of setPath()
        """
        if isinstance(path, stream.Stream):
            if detector is not None:
                raise ValueError("Not possible to specify both a stream, and a detector")
            try:
                mode = self.guessMode(path)
            except LookupError:
                logging.debug("%s doesn't require optical path change", path)
                return
            target = self.getStreamDetector(path)  # target detector
        else:
            mode = path
            if mode not in self._modes:
                raise ValueError("Mode '%s' does not exist" % (mode,))
            comp_role = self._modes[mode][0]
            if detector is None:
                target = self._getComponent(comp_role)
            else:
                target = detector

        logging.debug("Going to optical path '%s', with target detector %s.", mode, target.name)

        # Special SECOM mode: just look at the fan and be done
        if self.microscope.role in ("secom", "delphi"):
            try:
                if self.quality == ACQ_QUALITY_FAST:
                    self._setCCDFan(True)
                elif self.quality == ACQ_QUALITY_BEST:
                    self._setCCDFan(target.role == "ccd")
            except Exception:
                # This can happen mainly if the hardware is in a bad state
                # let's not make a big fuss: only report the error. The optical
                # path is correct anyway, just potentially more vibrations.
                logging.exception("Failed to change CCD fan")

        fmoves = []  # moves in progress, list of (future, Component, dict(axis->pos) tuples

        # Restore the spectrometer focus before any other move, as (on the SR193),
        # the value is grating/output dependent
        if self._chamber_view_own_focus and self._last_mode == "chamber-view":
            focus_comp = self._getComponent("focus")
            self._focus_in_chamber_view = focus_comp.position.value.copy()
            if self._focus_out_chamber_view is not None:
                logging.debug("Restoring focus from before coming to chamber view to %s",
                              self._focus_out_chamber_view)
                fmoves.append((focus_comp.moveAbs(self._focus_out_chamber_view), focus_comp, self._focus_out_chamber_view))

        modeconf = self._modes[mode][1]
        for comp_role, conf in modeconf.items():
            # Try to access the component needed
            try:
                comp = self._getComponent(comp_role)
            except LookupError:
                logging.debug("Failed to find component %s, skipping it", comp_role)
                continue

            # Check whether that actuator affects the target
            targets = {target.name} | set(target.affects.value)
            if not any(self.affects(comp.name, n) for n in targets):
                logging.debug("Actuator %s doesn't affect %s, so not moving it",
                              comp.name, target.name)
                continue

            mv = {}
            for axis, pos in conf.items():
                if axis == "power":
                    if model.hasVA(comp, "power"):
                        try:
                            if pos == 'on':
                                comp.power.value = comp.power.range[1]
                            else:
                                comp.power.value = comp.power.range[0]
                            logging.debug("Updating power of comp %s to %f", comp.name, comp.power.value)
                        except AttributeError:
                            logging.debug("Could not retrieve power range of %s component", comp_role)
                    continue
                if not hasattr(comp, "axes") or not isinstance(comp.axes, dict):
                    continue
                if isinstance(pos, tuple):  # several ways to find the position => pick the first one that works
                    for position in pos:
                        if isinstance(position, str) and position.startswith("MD:"):
                            try:
                                pos = self.mdToValue(comp, position[3:])[axis]
                                break
                            except KeyError:
                                pass
                        else:
                            pos = position
                            break
                    else:
                        logging.warning("Failed to find any correct position for component %s in %s", comp.name, pos)
                if isinstance(pos, str) and pos.startswith("MD:"):
                    pos = self.mdToValue(comp, pos[3:])[axis]
                if axis in comp.axes:
                    if axis == "band":
                        # Handle the filter wheel in a special way. Search
                        # for the position (key) that corresponds to the requested
                        # position name (value), typically 'pass-through'.
                        choices = comp.axes[axis].choices
                        for key, value in choices.items():
                            if value == pos:
                                pos = key
                                # Just to store current band in order to restore
                                # it once we leave this mode
                                if self._last_mode not in ALIGN_MODES:
                                    self._stored[comp_role, axis] = comp.position.value[axis]
                                break
                        else:
                            if mode == "mirror-align" and pos == BAND_PASS_THROUGH:
                                # On the SPARC, if there is a filter-wheel in front of the CCD,
                                # there should be a pass-through position. So if it's missing
                                # that's typically a sign that the microscope file is incorrect
                                # eg, a typo in the filter name.
                                logging.warning("No 'pass-through' provided by %s.%s, "
                                                "alignment might be harder due to limited signal. "
                                                "That might be a sign of issue in the microscope file.",
                                                comp.name, axis)
                            else:
                                logging.debug("Choice %s is not present in %s.%s axis, leaving at %s",
                                              pos, comp.name, axis, comp.position.value[axis])
                            continue
                    elif axis == "grating":
                        # If mirror is to be used but not found in grating
                        # choices, then we use zero order. In case of
                        # GRATING_NOT_MIRROR we either use the last known
                        # grating or the first grating that is not mirror.
                        choices = comp.axes[axis].choices
                        if pos == "mirror":
                            # Store current grating (if we use one at the moment)
                            # to restore it once we use a normal grating again
                            if choices[comp.position.value[axis]] != "mirror":
                                self._stored[comp_role, axis] = comp.position.value[axis]
                                self._stored[comp_role, 'wavelength'] = comp.position.value['wavelength']
                            # Use the special "mirror" grating, if it exists
                            for key, value in choices.items():
                                if value == "mirror":
                                    pos = key
                                    break
                            else:
                                # Fallback to zero order (aka "low-quality mirror")
                                axis = 'wavelength'
                                pos = 0
                        elif pos == GRATING_NOT_MIRROR:
                            if choices[comp.position.value[axis]] == "mirror":
                                # if there is a grating stored use this one
                                # otherwise find the non-mirror grating
                                if (comp_role, axis) in self._stored:
                                    pos = self._stored[comp_role, axis]
                                else:
                                    pos = self.findNonMirror(choices)
                                if (comp_role, 'wavelength') in self._stored:
                                    mv['wavelength'] = self._stored[comp_role, 'wavelength']
                            else:
                                pos = comp.position.value[axis]  # no change
                            try:
                                del self._stored[comp_role, axis]
                            except KeyError:
                                pass
                            try:
                                del self._stored[comp_role, 'wavelength']
                            except KeyError:
                                pass
                        else:
                            logging.debug("Using grating position as-is: '%s'", pos)
                            pass  # use pos as-is
                    elif axis == "slit-in":
                        if mode in ALIGN_MODES and (comp_role, axis) not in self._stored:
                            self._stored[comp_role, axis] = comp.position.value[axis]
                    elif hasattr(comp.axes[axis], "choices") and isinstance(comp.axes[axis].choices, dict):
                        choices = comp.axes[axis].choices
                        for key, value in choices.items():
                            if value == pos:
                                pos = key
                                break
                    # write actuator axis and position in dict
                    mv[axis] = pos
                else:
                    logging.debug("Not moving axis %s.%s as it is not present", comp_role, axis)

            if mv:
                try:
                    # move actuator
                    fmoves.append((comp.moveAbs(mv), comp, mv))
                except AttributeError:
                    logging.warning("%s not an actuator, but tried to move to %s", comp_role, mv)

        # Now take care of the selectors based on the target detector
        fmoves.extend(self.selectorsToPath(target.name))

        # If we are about to leave alignment modes, restore values
        if self._last_mode in ALIGN_MODES and mode not in ALIGN_MODES:
            logging.debug("Leaving align mode %s for %s, will restore positions: %s",
                          self._last_mode, mode, self._stored)
            for (cr, an), pos in self._stored.copy().items(): # copy for deleting entries
                if an == "grating":
                    continue  # handled separately via GRATING_NOT_MIRROR
                comp = self._getComponent(cr)
                fmoves.append((comp.moveAbs({an: pos}), comp, {an: pos}))
                del self._stored[cr, an]

        # Save last mode
        self._last_mode = mode

        # wait for all the moves to be completed
        for f, comp, mv in fmoves:
            try:
                # Can be large, eg within 5 min one (any) move should finish.
                f.result(timeout=180)

                # To do an absolute move, an axis should be referenced (if it
                # supports referencing). If not, that's an error (but for now we
                # still try, just in case it might work anyway).
                for a in mv:
                    try:
                        if (model.hasVA(comp, "referenced") and
                            not comp.referenced.value.get(a, True)):
                            logging.error("%s.%s is not referenced, it might be a sign of a hardware issue",
                                          comp.name, a)
                    except Exception:
                        logging.exception("Failed to check %s.%s is referenced", comp.name, a)

            except IOError as e:
                logging.warning("Actuator move failed giving the error %s", e)
            except:
                logging.exception("Actuator %s move %s failed!", comp.name, mv)
                raise

        # When going to chamber view, store the current focus position, and
        # restore the special focus position for chamber, after _really_ all
        # the other moves have finished, because the grating/output selector
        # moves affects the current position of the focus.
        if self._chamber_view_own_focus and mode == "chamber-view":
            focus_comp = self._getComponent("focus")
            self._focus_out_chamber_view = focus_comp.position.value.copy()
            if self._focus_in_chamber_view is not None:
                logging.debug("Restoring focus from previous chamber view to %s",
                              self._focus_in_chamber_view)
                try:
                    focus_comp.moveAbsSync(self._focus_in_chamber_view)
                except IOError as e:
                    logging.warning("Actuator move failed giving the error %s", e)

    def selectorsToPath(self, target):
        """
        Sets the selectors so the optical path leads to the target component
        (usually a detector).
        target (str): component name
        return (list of tuple (futures, Component, dict)): for each move: the
          future, the component, and the new position requested
        """
        fmoves = []
        for comp in self._actuators:
            # TODO: pre-cache this as comp/target -> axis/pos
            # TODO: don't do moves already done

            # TODO: extend the path computation to "for every actuator which _affects_
            # the target, move if position known, and update path to that actuator"?
            # Eg, this would improve path computation on SPARCv2 with fiber aligner
            mv = {}
            for an, ad in comp.axes.items():
                if hasattr(ad, "choices") and isinstance(ad.choices, dict):
                    for pos, value in ad.choices.items():
                        if target in value:
                            # set the position so it points to the target
                            mv[an] = pos

            comp_md = comp.getMetadata()
            if target in comp_md.get(model.MD_FAV_POS_ACTIVE_DEST, {}):
                mv.update(comp_md[model.MD_FAV_POS_ACTIVE])
            elif target in comp_md.get(model.MD_FAV_POS_DEACTIVE_DEST, {}):
                mv.update(comp_md[model.MD_FAV_POS_DEACTIVE])

            if mv:
                logging.debug("Move %s added so %s targets to %s", mv, comp.name, target)
                fmoves.append((comp.moveAbs(mv), comp, mv))
                # make sure this component is also on the optical path
                fmoves.extend(self.selectorsToPath(comp.name))

        return fmoves

    def guessMode(self, guess_stream):
        """
        Given a stream and by checking its components (e.g. role of detector)
        guesses and returns the corresponding optical path mode.
        guess_stream (object): The given optical stream
        returns (str): Mode estimated
        raises:
                LookupError if no mode can be inferred for the given stream
                IOError if given object is not a stream
        """
        if not isinstance(guess_stream, stream.Stream):
            raise IOError("Given object is not a stream")

        # Handle multiple detector streams
        if isinstance(guess_stream, stream.MultipleDetectorStream):
            for st in guess_stream.streams:
                try:
                    return self.guessMode(st)
                except LookupError:
                    pass
        elif isinstance(guess_stream, stream.AngularSpectrumStream):
            return "ek"
        elif isinstance(guess_stream, stream.ARStream):
            return "ar"
        elif isinstance(guess_stream, stream.OverlayStream):
            return "overlay"
        else:
            for mode, conf in self.guessed.items():
                # match the name using regex
                if re.match(conf[0] + '$', guess_stream.detector.role):
                    return mode
        # In case no mode was found yet
        raise LookupError("No mode can be inferred for the given stream")

    def getStreamDetector(self, path_stream):
        """
        Given a stream find the optical detector.
        path_stream (Stream): The given stream
        returns (HwComponent): detector
        raises:
                ValueError if given object is not a stream
                LookupError: if stream has no detector
        """
        if not isinstance(path_stream, stream.Stream):
            raise ValueError("Given object is not a stream")

        # Handle multiple detector streams
        if isinstance(path_stream, stream.MultipleDetectorStream):
            dets = []
            for st in path_stream.streams:
                try:
                    # Prefer the detectors which have a role in the mode, as it's much
                    # more likely to be the optical detector
                    # TODO: handle setting multiple optical paths? => return all the detectors
                    role = st.detector.role
                    for conf in self.guessed.values():
                        if re.match(conf[0] + '$', role):
                            return st.detector
                    dets.append(st.detector)
                except AttributeError:
                    pass
            if dets:
                logging.warning("No detector on stream %s has a known optical role", path_stream.name.value)
                return dets[0]
        elif isinstance(path_stream, stream.OverlayStream):
            return path_stream._ccd
        else:
            try:
                return path_stream.detector
            except AttributeError:
                pass  # will raise error just after

        raise LookupError("Failed to find a detector on stream %s" % (path_stream.name.value,))

    def findNonMirror(self, choices):
        """
        Given a dict of choices finds the one with value different than "mirror"
        """
        for key, value in choices.items():
            if value != "mirror":
                return key
        else:
            raise ValueError("Cannot find grating value in given choices")

    def mdToValue(self, comp, md_name):
        """
        Just retrieves the "md_name" metadata from component "comp"
        """
        md = comp.getMetadata()
        try:
            return md[md_name]
        except KeyError:
            raise KeyError("Metadata %s does not exist in component %s" % (md_name, comp.name))

    def affects(self, affecting, affected):
        """
        Returns True if "affecting" component affects -directly of indirectly-
        the "affected" component
        affecting (str): component name
        affected (str): component name
        return bool
        """
        path = self.findPath(affecting, affected)
        if path is None:
            return False
        else:
            return True

    def findPath(self, node1, node2, path=None):
        """
        Find any path between node1 and node2 (may not be shortest)
        """
        if path is None:
            path = []
        path = path + [node1]
        if node1 == node2:
            return path
        if node1 not in self._graph:
            return None
        for node in self._graph[node1]:
            if node not in path:
                new_path = self.findPath(node, node2, path)
                if new_path:
                    return new_path
        return None

    def _setCCDFan(self, enable):
        """
        Turn on/off the fan of the CCD
        enable (boolean): True to turn on/restore the fan, and False to turn if off
        """
        if not self._has_fan_speed:
            return

        if self._fan_enabled == enable:
            return
        self._fan_enabled = enable

        comp = self._getComponent("ccd")

        if enable:
            if self._enabled_fan_speed is not None:
                logging.debug("Turning fan on of %s", comp.name)
                comp.fanSpeed.value = max(comp.fanSpeed.value, self._enabled_fan_speed)
        else:
            if comp.fanSpeed.value == 0:
                # Already off => don't touch it
                self._enabled_fan_speed = None
                self._enabled_fan_temp = None
            else:
                logging.debug("Turning fan off of %s", comp.name)
                self._enabled_fan_speed = comp.fanSpeed.value
                comp.fanSpeed.value = 0

        # Raise targetTemperature to max/ambient to avoid the fan from
        # automatically starting again. (Some hardware have this built-in when
        # the current temperature is too high compared to the target)
        if self._has_fan_temp:
            temp = comp.targetTemperature
            if enable:
                if self._enabled_fan_temp is not None:
                    temp.value = min(comp.targetTemperature.value, self._enabled_fan_temp)
                    try:
                        self._waitTemperatureReached(comp, timeout=60)
                    except Exception as ex:
                        logging.warning("Failed to reach target temperature of CCD: %s",
                                        ex)
            else:
                # Set ~25°C == ambient temperature
                self._enabled_fan_temp = temp.value
                try:
                    try:
                        temp.value = min(comp.targetTemperature.range[1], 25)
                    except AttributeError:
                        temp.value = util.find_closest(25, comp.targetTemperature.choices)
                except Exception:
                    logging.warning("Failed to change targetTemperature when disabling fan",
                                    exc_info=True)

    def _waitTemperatureReached(self, comp, timeout=None):
        """
        Wait until the current temperature of the component has reached the
          target temperature (within some margin).
        comp (Component)
        timeout (0<float or None): maximum time to wait (in s)
        raises:
            TimeoutError: if time-out reached
        """
        tstart = time.time()
        while timeout is None or time.time() < tstart + timeout:
            # TODO: adjust the timeout depending on whether the temperature
            # gets closer to the target over time or not.
            ttemp = comp.targetTemperature.value
            atemp = comp.temperature.value
            if atemp < ttemp + TEMP_EPSILON:
                return
            else:
                logging.debug(u"Waiting for temperature to reach %g °C (currently at %g °C)",
                              ttemp, atemp)
                time.sleep(1)

        raise TimeoutError("Target temperature (%g C) not reached after %g s" %
                           (comp.targetTemperature.value, timeout))

