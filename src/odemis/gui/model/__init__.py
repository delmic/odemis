# -*- coding: utf-8 -*-
"""
:created: 16 Feb 2012
:author: Éric Piel
:copyright: © 2012-2013 Éric Piel, Rinze de Laat, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License version 2 as published by the Free
    Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
    FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
    details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""

from abc import ABCMeta
import collections
import logging
import math
from odemis import model
from odemis.acq.stream import Stream, StreamTree
from odemis.gui.conf import get_general_conf
from odemis.model import (FloatContinuous, VigilantAttribute, IntEnumerated,
                          NotSettableError, StringVA)
import os
import threading
import time

import odemis.dataio as dataio


# The different states of a microscope
STATE_OFF = 0
STATE_ON = 1
STATE_PAUSE = 2

# Chamber states
CHAMBER_UNKNOWN = 0  # Chamber in an unknown state
CHAMBER_VENTED = 1   # Chamber can be opened
CHAMBER_VACUUM = 2   # Chamber ready for imaging
CHAMBER_PUMPING = 3  # Decreasing chamber pressure
CHAMBER_VENTING = 4  # Pressurizing chamber

# The different types of view layouts
VIEW_LAYOUT_ONE = 0 # one big view
VIEW_LAYOUT_22 = 1 # 2x2 layout
VIEW_LAYOUT_FULLSCREEN = 2 # Fullscreen view (not yet supported)

# The different tools (selectable in the tool bar). Actually, only the ones which
# have a mode, the ones which have a direct action don't need to be known
# explicitly.
TOOL_NONE = 0 # No tool (normal)
TOOL_ZOOM = 1 # Select the region to zoom in
TOOL_ROI = 2 # Select the region of interest (sub-area to be updated)
TOOL_ROA = 3 # Select the region of acquisition (area to be acquired, SPARC-only)
TOOL_POINT = 4 # Select a point (to acquire/display)
TOOL_LINE = 5 # Select a line (to acquire/display)
TOOL_DICHO = 6 # Dichotomy mode to select a sub-quadrant (for SECOM lens alignment)
TOOL_SPOT = 7 # Activate spot mode on the SEM
TOOL_RO_ANCHOR = 8

class MainGUIData(object):
    """
    Contains all the data corresponding to the entire GUI.

    In the MVC terminology, it's a model. It contains attributes to directly
    access the microscope components, and data to be used or represented in the
    entire GUI. Normally, there is only one instance of this object per
    execution (only one microscope manipulated at a time by the interface).

    It contains mainly:
    .microscope:
        The HwComponent root of all the other components (can be None
        if there is no microscope available, like an interface to display
        recorded acquisition).
    .role (string): copy of .microscope.role (string) should be used to find out
        the generic type of microscope connected.

    There are also many .ccd, .stage, etc, which can be used to directly access
    the sub-components.
    """

    def __init__(self, microscope):
        """
        microscope (model.Microscope or None): the root of the HwComponent tree
         provided by the back-end. If None, it means the interface is not
         connected to a microscope (and displays a recorded acquisition).
        """

        self.microscope = microscope
        self.role = None

        # These are either HwComponents or None (if not available)
        self.ccd = None
        self.stage = None
        self.focus = None # actuator to change the camera focus
        self.aligner = None # actuator to align ebeam/ccd
        self.mirror = None # actuator to change the mirror position (on SPARC)
        self.light = None
        self.light_filter = None # emission light filter for SECOM/output filter for SPARC
        self.lens = None
        self.ebeam = None
        self.ebeam_focus = None # change the e-beam focus
        self.sed = None # secondary electron detector
        self.bsd = None # back-scatter electron detector
        self.spectrometer = None # spectrometer
        self.spectrograph = None # actuator to change the wavelength
        self.ar_spec_sel = None # actuator to select AR/Spectrometer (SPARC)
        self.lens_switch = None # actuator to (de)activate the lens (SPARC)
        self.chamber = None # actuator to control the chamber (has vacuum, pumping etc.)
        self.ccd_chamber = None # view of inside the chamber
        self.ccd_overview = None # global view from above the sample

        # Indicates whether the microscope is acquiring a high quality image
        self.is_acquiring = model.BooleanVA(False)

        if microscope:
            self.role = microscope.role

            for d in microscope.detectors:
                if d.role == "ccd":
                    self.ccd = d
                elif d.role == "se-detector":
                    self.sed = d
                elif d.role == "bs-detector":
                    self.bsd = d
                elif d.role == "spectrometer":
                    self.spectrometer = d
                elif d.role == "ccd-chamber":
                    self.ccd_chamber = d
                elif d.role == "ccd-overview":
                    self.ccd_overview = d

            for a in microscope.actuators:
                if a.role == "stage":
                    self.stage = a # most views move this actuator when moving
                elif a.role == "focus":
                    self.focus = a
                elif a.role == "ebeam-focus":
                    self.ebeam_focus = a
                elif a.role == "mirror":
                    self.mirror = a
                elif a.role == "align":
                    self.aligner = a
                elif a.role == "lens-switch":
                    self.lens_switch = a
                elif a.role == "ar-spec-selector":
                    self.ar_spec_sel = a
                elif a.role == "chamber":
                    self.chamber = a

            # Spectrograph is not directly an actuator, but a sub-comp of spectrometer
            if self.spectrometer:
                for child in self.spectrometer.children:
                    if child.role == "spectrograph":
                        self.spectrograph = child

            for e in microscope.emitters:
                if e.role == "light":
                    self.light = e
                    self._light_power_on = None # None = unknown
                elif e.role == "filter":
                    self.light_filter = e
                elif e.role == "lens":
                    self.lens = e
                elif e.role == "e-beam":
                    self.ebeam = e

            # Do some typical checks on expectations from an actual microscope
            if not any((self.ccd, self.sed, self.bsd, self.spectrometer)):
                raise KeyError("No detector found in the microscope")

            if not self.light and not self.ebeam:
                raise KeyError("No emitter found in the microscope")

        # TODO: all that on/off thing is crazy:
        # * we cannot do it (for now)
        # * we'd better turn on/off the hardware when streams need it
        # * pause and off are the same things but for SEM (blank/off)
        # * optical on in live view means light on, while in lens align it means light off
        # => we'd be better with just one global pause button (and pressure)

        # Handle turning on/off the instruments
        hw_states = {STATE_OFF, STATE_ON, STATE_PAUSE}
        if self.ccd:
            # not so nice to hard code it here, but that should do it for now...
            if self.role == "sparc":
                self.arState = model.IntEnumerated(STATE_OFF, choices=hw_states)
                self.arState.subscribe(self.onARState)
            else:
                self.opticalState = model.IntEnumerated(STATE_OFF, choices=hw_states)
                self.opticalState.subscribe(self.onOpticalState)

        if self.ebeam:
            self.emState = model.IntEnumerated(STATE_OFF, choices=hw_states)
            self.emState.subscribe(self.onEMState)

        if self.spectrometer:
            self.specState = model.IntEnumerated(STATE_OFF, choices=hw_states)
            self.specState.subscribe(self.onSpecState)

        # Chamber vacuum states
        if self.chamber:
            chamber_states = {CHAMBER_UNKNOWN, CHAMBER_VENTED, CHAMBER_PUMPING, CHAMBER_VACUUM,
                              CHAMBER_VENTING}
            self.chamber_state = model.IntEnumerated(CHAMBER_UNKNOWN, chamber_states)
            self.chamber_state.subscribe(self.on_chamber_state)

            if hasattr(self.chamber, 'pressure'):
                self.chamber.pressure.subscribe(self.on_chamber_pressure)

        # Used when doing fine alignment, based on the value used by the user
        # when doing manual alignment. 0.1s is not too bad value if the user
        # hasn't specified anything (yet).
        self.fineAlignDwellTime = model.FloatContinuous(0.1, range=[1e-9, 100],
                                                        unit="s")

        # TODO: should we put also the configuration related stuff?
        # Like path/file format
        # Set to True to request debug info to be displayed
        self.debug = model.BooleanVA(False)

        # Current tab (+ all available tabs in choices as a dict tab -> name)
        # Fully set and managed later by the TabBarController.
        # Not very beautiful because Tab is not part of the model.
        # MicroscopyGUIData would be better in theory, but is less convenient
        # do directly access additional GUI information.
        self.tab = model.VAEnumerated(None, choices={None: ""})

    def onOpticalState(self, state):
        """ Event handler for when the state of the optical microscope changes
        """
        # only called when it changes

        if state in (STATE_OFF, STATE_PAUSE):
            # Turn off the optical path. All the streams using it should be
            # already deactivated.
            if self.light:
                if self.light.power.value > 0:
                    # save the value only if it makes sense
                    self._light_power_on = self.light.power.value
                self.light.power.value = 0
        elif state == STATE_ON:
            # the image acquisition from the camera is handled solely by the streams
            if self.light:
                # if power is above 0 already, it's probably the user who wants
                # to force to a specific value, respect that.
                if self.light.power.value == 0:
                    if self._light_power_on: # re-use previous value
                        self.light.power.value = self._light_power_on
                    else:
                        # pick a nice value (= slighty more than 0), if not already on
                        try:
                            # if continuous: 10 %
                            self.light.power.value = self.light.power.range[1] * 0.1
                        except (AttributeError, model.NotApplicableError):
                            try:
                                # if enumerated: the second lowest
                                self.light.power.value = sorted(self.light.power.choices)[1]
                            except (AttributeError, model.NotApplicableError):
                                logging.error("Unknown light power range, setting to 1 W")
                                self.light.power.value = 1

    def onEMState(self, state):
        """ Event handler for when the state of the electron microscope changes
        """
        if state == STATE_OFF:
            # TODO: actually turn off the ebeam and detector
            try:
                # TODO save the previous value
                # blank the ebeam
                self.ebeam.energy.value = 0
            except NotSettableError:
                # Too bad. let's just do nothing then.
                logging.debug("Ebeam doesn't support setting energy to 0")
        elif state == STATE_PAUSE:
            try:
                # TODO save the previous value
                # blank the ebeam
                self.ebeam.energy.value = 0
            except NotSettableError:
                # Too bad. let's just do nothing then.
                logging.debug("Ebeam doesn't support setting energy to 0")

        elif state == STATE_ON:
            try:
                # TODO use the previous value
                if hasattr(self.ebeam.energy, "choice"):
                    if isinstance(self.ebeam.energy.choices,
                                  collections.Iterable):
                        self.ebeam.energy.value = max(self.ebeam.energy.choices)
            except NotSettableError:
                # Too bad. let's just do nothing then (and hope it's on)
                logging.debug("Ebeam doesn't support setting energy")

    def onARState(self, state):
        # nothing to do here, the settings controller will just hide the stream/settings
        pass

    def onSpecState(self, state):
        # nothing to do here, the settings controller will just hide the stream/settings
        pass

    def on_chamber_state(self, chamber_state):
        """ Set the desired pressure on the chamber when the chamber's state changes

        Only 'active' states (i.e. either CHAMBER_PUMPING or CHAMBER_VENTING) will allow for a
        change in pressure.

        """

        vented_pressure, vacuum_pressure = self.chamber.axes["pressure"].choices.keys()

        if chamber_state == CHAMBER_PUMPING:
            self.chamber.moveAbs({"pressure": vacuum_pressure})
        elif chamber_state == CHAMBER_VENTING:
            self.chamber.moveAbs({"pressure": vented_pressure})

    def on_chamber_pressure(self, current_pressure):
        """ Determine the state of the chamber when the pressure changes

        This method can change the state from CHAMBER_PUMPING to CHAMBER_VACUUM or from
        CHAMBER_VENTING to CHAMBER_VENTED.

        """
        if self.chamber_state.value in (CHAMBER_PUMPING, CHAMBER_UNKNOWN):

            vacuum_pressure = min(self.chamber.axes["pressure"].choices.keys())

            if current_pressure <= vacuum_pressure:
                self.chamber_state.value = CHAMBER_VACUUM

        elif self.chamber_state.value in (CHAMBER_VENTING, CHAMBER_UNKNOWN):

            vented_pressure = max(self.chamber.axes["pressure"].choices.keys())

            if current_pressure >= vented_pressure:
                self.chamber_state.value = CHAMBER_VENTED

    def stopMotion(self):
        """
        Stops immediately every axis
        """
        if self.microscope is None:
            return

        for act in self.microscope.actuators:
            try:
                # TODO: run each of them in a separate thread, to call the stop
                # ASAP? (or all but the last one?)
                act.stop()
            except Exception:
                logging.exception("Failed to stop %s actuator", act.name)

        logging.info("Stopped motion on every axes")

    def getTabByName(self, name):
        """
        Look in .tab.choices for a tab with the given name
        name (str): name to look for
        returns (Tab): tab whose name fits the provided name
        raise:
            LookupError: if no tab exists with such a name
        """
        for t, n in self.tab.choices.items():
            if n == name:
                return t
        else:
            raise LookupError("Failed to find tab %s in the %d tabs" %
                              name, len(self.tab.choices))


class MicroscopyGUIData(object):
    """Contains all the data corresponding to a GUI tab.

    In the Odemis GUI, there's basically one MicroscopyGUIData per tab (or just
    one for each window without tab). In the MVC terminology, it's a model.

    This is a meta-class. You actually want to use one
    of the sub-classes to represent a specific type of interface. Not all
    interfaces have the same attributes. However, there are always:
    .main:
        The MainGUIData object for the current GUI.
    .views and .focussedView:
        Represent the available/currently selected views (graphical image/data
        display).
    .viewLayout:
        The current way on how the views are organized (the choices
        give all the possibilities of this GUI)
    .streams:
        All the stream/data available to the user to manipulate.
    .tool:
        the current "mode" in which the user is (the choices give all the
        available tools for this GUI).
    """
    __metaclass__ = ABCMeta

    def __init__(self, main):
        self.main = main

        # Streams available (handled by StreamController)
        # Note: we need to make sure ourselves that each stream in this
        # attribute is unique (i.e. only occurs once in the list).
        self.streams = model.ListVA()

        # TODO: the latest stream used.
        # IOW, either the active one, or the last stream which was paused.
        self.currentStream = VigilantAttribute(None)

        # Available Views. The are handled by the ViewController.
        # The `views` list basically keeps track of the relevant references.
        self.views = model.ListVA()

        # Current tool selected (from the toolbar, cf cont.tools)
        self.tool = None # Needs to be overridden by a IntEnumerated

        # The MicroscopeView currently focused, it is one of the `views`
        # or `None`.
        self.focussedView = VigilantAttribute(None)

        layouts = set([VIEW_LAYOUT_ONE, VIEW_LAYOUT_22, VIEW_LAYOUT_FULLSCREEN])
        self.viewLayout = model.IntEnumerated(VIEW_LAYOUT_22, choices=layouts)

        # The subset of views taken from `views` that *can* actually displayed,
        # but they might be hidden as well.
        # This attribute is also handled and manipulated by the ViewController.
        self.visible_views = model.ListVA()


class LiveViewGUIData(MicroscopyGUIData):
    """ Represent an interface used to only show the current data from the
    microscope. It should be able to handle SEM-only, optical-only, and SECOM
    systems.
    """
    def __init__(self, main):
        assert main.microscope is not None
        MicroscopyGUIData.__init__(self, main)

        # Current tool selected (from the toolbar)
        tools = set([TOOL_NONE, TOOL_ZOOM, TOOL_ROI])
        self.tool = IntEnumerated(TOOL_NONE, choices=tools)


class ScannedAcquisitionGUIData(MicroscopyGUIData):
    """ Represent an interface used to select a precise area to scan and
    acquire signal. It allows fine control of the shape and density of the scan.
    It is specifically made for the SPARC system.
    """
    def __init__(self, main):
        assert main.microscope is not None
        MicroscopyGUIData.__init__(self, main)

        # more tools: for selecting the sub-region of acquisition
        tools = set([TOOL_NONE,
                     TOOL_ZOOM,
                     TOOL_ROI,
                     TOOL_ROA,
                     TOOL_RO_ANCHOR,
                     TOOL_POINT,
                     TOOL_LINE])

        self.tool = IntEnumerated(TOOL_NONE, choices=tools)

        # Very special view which is used only as a container to save which
        # stream will be acquired (for the Sparc acquisition interface only).
        # The tab controller will take care of filling it
        self.acquisitionView = MicroscopeView("Acquisition")

        # The SEM CL stream that is used to select the acquisition settings
        # It will be set at start up by the tab controller
        self.semStream = None

class AnalysisGUIData(MicroscopyGUIData):
    """
    Represent an interface used to show the recorded microscope data. Typically
    it represents all the data present in a specific file.
    All the streams should be StaticStreams
    """
    def __init__(self, main):
        MicroscopyGUIData.__init__(self, main)
        self._conf = get_general_conf()

        # only tool to zoom and pick point/line
        tools = set([TOOL_NONE, TOOL_ZOOM, TOOL_POINT, TOOL_LINE])
        self.tool = IntEnumerated(TOOL_NONE, choices=tools)

        # The current file it displays. If None, it means there is no file
        # associated to the data displayed
        self.acq_fileinfo = VigilantAttribute(None) # a FileInfo

        # The current file being used for calibration. It is set to u""
        # when no calibration is used. They are directly synchronised with the
        # configuration file.
        ar_file = self._conf.get("calibration", "ar_file")
        spec_file = self._conf.get("calibration", "spec_file")
        self.ar_cal = StringVA(ar_file) # a unicode
        self.spec_cal = StringVA(spec_file) # a unicode

        self.ar_cal.subscribe(self._on_ar_cal)
        self.spec_cal.subscribe(self._on_spec_cal)

    def _on_ar_cal(self, fn):
        self._conf.set("calibration", "ar_file", fn)
        self._conf.write()

    def _on_spec_cal(self, fn):
        self._conf.set("calibration", "spec_file", fn)
        self._conf.write()


class ActuatorGUIData(MicroscopyGUIData):
    """
    Represent an interface used to move the actuators of a microscope. It might
    also display one or more views, but it's not required.
    """
    def __init__(self, main):
        assert main.microscope is not None
        MicroscopyGUIData.__init__(self, main)

        # check there is something to move
        if not main.microscope.actuators:
            raise KeyError("No actuators found in the microscope")

        # Step size name -> val, range, actuator, axes (None if all)
        # str -> float, [float, float], str, (str, ...)
        ss_def = {"stage": (1e-6, [100e-9, 1e-3], "stage", None),
                  "focus": (100e-9, [10e-9, 1e-4], "focus", None),
                  "aligner": (1e-6, [100e-9, 1e-4], "aligner", None),
                  # Mirror is a bit more complicated as it has 4 axes and Y
                  # usually needs to be 10x bigger than X
                  "mirror_x": (1e-6, [100e-9, 1e-3], "mirror", ("x",)),
                  "mirror_y": (10e-6, [100e-9, 1e-3], "mirror", ("y",)),
                  "mirror_r": (10e-6, [100e-9, 1e-3], "mirror", ("ry", "rz"))
                  }
        # str -> VA: name (as the name of the attribute) -> step size (m)
        self.stepsizes = {}

        # This allow the UI code to mention axes only as role/axis name.
        # str -> (str, str):
        # role/axis ("mirror/x") -> (actuator ("mirror"), stepsize ("mirror_r"))
        self._axis_to_act_ss = {}

        # remove the ones that don't have an actuator
        for ss, (v, r, an, axn) in ss_def.items():
            if getattr(main, an) is not None:
                self.stepsizes[ss] = model.FloatContinuous(v, r)
                if axn is None:
                    axn = getattr(main, an).axes
                for a in axn:
                    self._axis_to_act_ss[(an, a)] = (an, ss)
                    logging.debug("Add axis %s/%s to stepsize %s", an, a, ss)

        # set of (str, str): actuator name, axis name
        self.axes = frozenset(self._axis_to_act_ss.keys())

        # Tools are for lens alignment (mirror alignment actually needs none)
        tools = set([TOOL_NONE, TOOL_DICHO, TOOL_SPOT])
        self.tool = IntEnumerated(TOOL_NONE, choices=tools)

        # For dichotomic mode
        self.dicho_seq = model.ListVA() # list of 4 enumerated for each corner

    def step(self, actuator, axis, factor, sync=False):
        """
        Moves a given axis by a one step (of stepsizes).

        :param actuator: (str) name of the actuator to move (from .axes[0])
        :param axis: (str) name of the axis to move (from .axes[1])
        :param factor: (float) amount to which multiply the stepsizes. -1 makes
            it goes one step backward.
        :param sync: (bool) wait until the move is over before returning

        :raises: KeyError if the axis doesn't exist
        """
        an, ssn = self._axis_to_act_ss[(actuator, axis)]
        a = getattr(self.main, an)
        ss = factor * self.stepsizes[ssn].value

        if abs(ss) > 10e-3:
            # more than 1 cm is too dangerous
            logging.warning("Not moving axis %s because a distance of %g m is too big.",
                            axis, ss)

        move = {axis: ss}
        f = a.moveRel(move)

        if sync:
            f.result() # wait until the future is complete


class FileInfo(object):
    """
    Represent all the information about a microscope acquisition recorded
    inside a file. It's mostly aimed at containing information, and its
    attributes should be considered readonly after initialisation.
    """

    def __init__(self, a_file=None, metadata=None):
        """
        :param a_file: (unicode or File or None): the full name of the file or
            a File that contains the acquisition. If provided (and the file
            exists), some fields will be automatically filled in.
        :param metadata: (dict String -> value): The meta-data as model.MD_*.
        """

        self.file_name = None
        self.file_obj = None

        if isinstance(a_file, basestring):
            # The given parameter is a file name
            self.file_name = a_file
        elif a_file is not None:
            # Assume the given parameter is a File Object
            self.file_name = a_file.name
            self.file_obj = a_file # file object

        # TODO: settings of the instruments for the acquisition?
        # Might be per stream
        self.metadata = metadata or {}

        if not model.MD_ACQ_DATE in self.metadata and self.file_name:
            # try to auto fill acquisition time (seconds from epoch)
            try:
                acq_date = os.stat(self.file_name).st_ctime
                self.metadata[model.MD_ACQ_DATE] = acq_date
            except OSError:
                # can't open the file => just cannot guess the time
                pass

    @property
    def file_path(self):
        """ Return the directory that contains the file """
        return os.path.dirname(self.file_name) if self.file_name else None

    @property
    def file_basename(self):
        """ Return the file name """
        return os.path.basename(self.file_name) if self.file_name else None

    @property
    def is_empty(self):
        return self.file_name is None

    def __repr__(self):
        return "%s (%s)" % (self.__class__, self.file_name)

class View(object):

    def __init__(self, name):
        self.name = model.StringVA(name)

        # a thumbnail version of what is displayed
        self.thumbnail = VigilantAttribute(None) # contains a wx.Image

        # Last time the image of the view was changed. It's actually mostly
        # a trick to allow other parts of the GUI to know when the (theoretical)
        # composited image has changed.
        self.lastUpdate = model.FloatVA(time.time(), unit="s")

    def __unicode__(self):
        return u"{}".format(self.name.value)

    def __str__(self):
        return "{}".format(self.name.value)

MAX_SAFE_MOVE_DISTANCE = 10e-3 # 1 cm
class MicroscopeView(View):
    """ Represents a view from a microscope and ways to alter it.

    Basically, its "input" is a StreamTree and it can request stage and focus
    move. It never computes the composited image from all the streams itself.
    It's up to other objects (e.g., the canvas) to ask the StreamTree for its
    latest image (the main goal of this scheme is to avoid computation when not
    needed). Similarly, the thumbnail is never automatically recomputed, but
    other objects can update it.
    """

    def __init__(self, name, stage=None,
                 focus0=None, focus1=None, stream_classes=None):
        """
        :param name (string): user-friendly name of the view
        :param stage (Actuator): actuator with two axes: x and y
        :param focus0 (Actuator): actuator with one axis: z. Can be None
        :param focus1 (Actuator): actuator with one axis: z. Can be None
          Focuses 0 and 1 are modified when changing focus respectively along
          the X and Y axis.
        :param stream_classes (None, or tuple of classes): all subclasses that the
          streams in this view is allowed to show.
        """

        super(MicroscopeView, self).__init__(name)

        if stream_classes is None:
            self.stream_classes = (Stream,)
        else:
            self.stream_classes = stream_classes
        self._stage = stage
        self._focus = [focus0, focus1]

        # The real stage position, to be modified via moveStageToView()
        # it's a direct access from the stage, so looks like a dict of axes
        if stage:
            self.stage_pos = stage.position
            # stage.position.subscribe(self.onStagePos)

            # the current center of the view, which might be different from
            # the stage
            # TODO: we might need to have it on the MicroscopeModel, if all the
            # viewports must display the same location
            pos = self.stage_pos.value
            view_pos_init = (pos["x"], pos["y"])
        else:
            view_pos_init = (0, 0)

        self.view_pos = model.ListVA(view_pos_init, unit="m")

        # current density (meter per pixel, ~ scale/zoom level)
        # 10µm/px => ~large view of the sample
        self.mpp = FloatContinuous(10e-6, range=(10e-12, 1e-3), unit="m/px")

        # How much one image is displayed on the other one. Value used by
        # StreamTree
        self.merge_ratio = FloatContinuous(0.3, range=[0, 1], unit="")
        self.merge_ratio.subscribe(self._onMergeRatio)

        # Streams to display (can be considered an implementation detail in most
        # cases)
        # Note: use addStream/removeStream for simple modifications
        self.stream_tree = StreamTree(merge=self.merge_ratio.value)
        # Only modify with this lock acquired:
        # TODO: Is this the source of the intermittent locking of the GUI when
        # Streams are active? If so, is there another/better way?
        self._streams_lock = threading.Lock()

        # TODO: list of annotations to display
        self.show_crosshair = model.BooleanVA(True)

    def get_focus(self, i):
        return self._focus[i]

    def get_focus_count(self):
        """ Get the number of available focus actuators """
        return len([a for a in self._focus if a])

    def has_stage(self):
        return self._stage is not None

    def moveStageToView(self):
        """ Move the stage to the current view_pos

        :return (None or Future): a future (that allows to know when the move is finished)

        Note: once the move is finished stage_pos will be updated (by the
        back-end)
        """

        if not self._stage:
            return

        # TODO: a way to know if it can do absolute move? => .capabilities!
        # if hasattr(self.stage, "moveAbs"):
        #     # absolute
        #     move = {"x": pos[0], "y": pos[1]}
        #     self._stage.moveAbs(move)
        # else:

        view_pos = self.view_pos.value
        # relative
        prev_pos = self.stage_pos.value
        move = {
            "x": view_pos[0] - prev_pos["x"],
            "y": view_pos[1] - prev_pos["y"]
        }
        if abs(move["x"]) < 1e-12 and abs(move["y"]) < 1e-12:
            logging.debug("skipping move request of 0")
            return

        # Check it makes sense (=> not too big)
        distance = math.sqrt(sum([v ** 2 for v in move.values()]))
        if distance > MAX_SAFE_MOVE_DISTANCE:
            logging.error("Cancelling request to move by %f m (because > %f m)",
                          distance, MAX_SAFE_MOVE_DISTANCE)
            return

        logging.debug("Sending move request of %s", move)
        return self._stage.moveRel(move)

    # def onStagePos(self, pos):
    #     # we want to recenter the viewports whenever the stage moves
    #     # Not sure whether that's really the right way to do it though...
    #     # TODO: avoid it to move the view when the user is dragging the view
    #     #  => might require cleverness
    #     # self.view_pos = model.ListVA((pos["x"], pos["y"]), unit="m")

    def getStreams(self):
        """
        :return: [Stream] list of streams that are displayed in the view

        Do not modify directly, use addStream(), and removeStream().
        Note: use .stream_tree for getting the raw StreamTree
        """
        return self.stream_tree.getStreams()

    def addStream(self, stream):
        """
        Add a stream to the view. It takes care of updating the StreamTree
        according to the type of stream.
        stream (acq.stream.Stream): stream to add
        If the stream is already present, nothing happens
        """

        # check if the stream is already present
        if stream in self.stream_tree.getStreams():
            return

        if not isinstance(stream, self.stream_classes):
            msg = "Adding incompatible stream '%s' to view '%s'. %s needed"
            logging.warning(msg,
                          stream.name.value,
                          self.name.value,
                          self.stream_classes)

        # Find out where the stream should go in the streamTree
        # FIXME: manage sub-trees, with different merge operations
        # For now we just add it to the list of streams, with the only merge
        # operation possible
        with self._streams_lock:
            self.stream_tree.add_stream(stream)

        # subscribe to the stream's image
        if hasattr(stream, "image"):
            stream.image.subscribe(self._onNewImage)

            # if the stream already has an image, update now
            if stream.image.value is not None:
                self._onNewImage(stream.image.value)
        else:
            logging.debug("No image found for stream %s", type(stream))

    def removeStream(self, stream):
        """
        Remove a stream from the view. It takes care of updating the StreamTree.
        stream (Stream): stream to remove
        If the stream is not present, nothing happens
        """
        # Stop listening to the stream changes
        if hasattr(stream, "image"):
            stream.image.unsubscribe(self._onNewImage)

        with self._streams_lock:
            # check if the stream is already removed
            if not stream in self.stream_tree.getStreams():
                return

            # remove stream from the StreamTree()
            # TODO handle more complex trees
            self.stream_tree.remove_stream(stream)

        # let everyone know that the view has changed
        self.lastUpdate.value = time.time()

    def _onNewImage(self, im):
        """
        Called when one stream has its image updated
        im (DataArray)
        """
        # just let everyone know that the composited image has changed
        self.lastUpdate.value = time.time()

    def _onMergeRatio(self, ratio):
        """
        Called when the merge ratio is modified
        """
        # This actually modifies the root operator of the stream tree
        # It has effect only if the operator can do something with the "merge"
        # argument
        with self._streams_lock:
            self.stream_tree.kwargs["merge"] = ratio

        # just let everyone that the composited image has changed
        self.lastUpdate.value = time.time()

    def is_compatible(self, stream_cls):
        """ Check if the given stream class is compatible with this view.
        """
        return issubclass(stream_cls, self.stream_classes)


