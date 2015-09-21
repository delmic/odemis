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

import Queue
from abc import ABCMeta
import collections
import logging
import math
from odemis import model
from odemis.acq import path
from odemis.acq.stream import Stream, SEMStream, CLSettingsStream, StreamTree
from odemis.gui.conf import get_general_conf
from odemis.model import (FloatContinuous, VigilantAttribute, IntEnumerated, StringVA, BooleanVA,
                          MD_POS, InstantaneousFuture)
from odemis.model._vattributes import StringEnumerated
import os
import threading
import time


# The different states of a microscope
STATE_OFF = 0
STATE_ON = 1
STATE_DISABLED = 2  # TODO: use this state when cannot be used

# Chamber states
CHAMBER_UNKNOWN = 0  # Chamber in an unknown state
CHAMBER_VENTED = 1   # Chamber can be opened
CHAMBER_VACUUM = 2   # Chamber ready for imaging
CHAMBER_PUMPING = 3  # Decreasing chamber pressure (set it to request pumping)
CHAMBER_VENTING = 4  # Pressurizing chamber (set it to request venting)

# The different types of view layouts
VIEW_LAYOUT_ONE = 0  # one big view
VIEW_LAYOUT_22 = 1  # 2x2 layout
VIEW_LAYOUT_FULLSCREEN = 2  # Fullscreen view (not yet supported)

# The different tools (selectable in the tool bar). Actually, only the ones which
# have a mode, the ones which have a direct action don't need to be known
# explicitly.
TOOL_NONE = 0  # No tool (normal)
TOOL_ZOOM = 1  # Select the region to zoom in
TOOL_ROI = 2  # Select the region of interest (sub-area to be updated)
TOOL_ROA = 3  # Select the region of acquisition (area to be acquired, SPARC-only)
TOOL_POINT = 4  # Select a point (to acquire/display)
TOOL_LINE = 5  # Select a line (to acquire/display)
TOOL_DICHO = 6  # Dichotomy mode to select a sub-quadrant (for SECOM lens alignment)
TOOL_SPOT = 7  # Activate spot mode on the SEM
TOOL_RO_ANCHOR = 8
# Autofocus state
TOOL_AUTO_FOCUS_ON = True
TOOL_AUTO_FOCUS_OFF = False


class MainGUIData(object):
    """
    Contains all the data corresponding to the entire GUI.

    In the MVC terminology, it's a model. It contains attributes to directly
    access the microscope components, and data to be used or represented in the
    entire GUI.

    Normally, there is only one instance of this object per running GUI, so only one microscope
    can be manipulated at a time by the interface. An instance of this class will normally be
    created in the `main.py` module during start-up of the GUI.

    The two main attributes are:

        .microscope:
            The HwComponent root of all the other components (can be None
            if there is no microscope available, like an interface to display
            recorded acquisition).
        .role (string): copy of .microscope.role (string) should be used to find out
            the generic type of microscope connected.

    There are also many .ccd, .stage, etc. attributes which can be used to access
    the sub-components directly.

    """
    # Mapping between the component role and the attribute name on the MainGUIData
    _ROLE_TO_ATTR = {
        "ccd": "ccd",
        "se-detector": "sed",
        "bs-detector": "bsd",
        "cl-detector": "cld",
        "spectrometer": "spectrometer",
        # spectrograph is special: it's a child of spectrometer
        "monochromator": "monochromator",
        "chamber-ccd": "chamber_ccd",
        "overview-ccd": "overview_ccd",
        "stage": "stage",
        "focus": "focus",
        "ebeam-focus": "ebeam_focus",
        "overview-focus": "overview_focus",
        "mirror": "mirror",
        "align": "aligner",
        "fiber-aligner": "fibaligner",
        "lens-switch": "lens_switch",
        "ar-spec-selector": "ar_spec_sel",
        "chamber": "chamber",
        "light": "light",
        "brightlight": "brightlight",
        "filter": "light_filter",
        "lens": "lens",
        "e-beam": "ebeam",
        "chamber-light": "chamber_light",
        "overview-light": "overview_light",
    }

    def __init__(self, microscope):
        """
        :param microscope: (model.Microscope or None): the root of the HwComponent tree
            provided by the back-end. If None, it means the interface is not
            connected to a microscope (and displays a recorded acquisition).

        """

        self.microscope = microscope
        self.role = None

        # The following attributes are either HwComponents or None (if not available)
        self.ccd = None
        self.stage = None
        self.focus = None  # actuator to change the camera focus
        self.aligner = None  # actuator to align ebeam/ccd (SECOM)
        self.mirror = None  # actuator to change the mirror position (SPARC)
        self.fibaligner = None  # actuator to move/calibrate the fiber (SPARC)
        self.light = None  # epi-fluorescence light (SECOM/DELPHI)
        self.brightlight = None  # brightlight (no hardware has this yet)
        self.light_filter = None  # emission light filter for SECOM/output filter for SPARC
        self.lens = None  # Optical lens for SECOM/focus lens for the SPARC
        self.ebeam = None
        self.ebeam_focus = None  # change the e-beam focus
        self.sed = None  # secondary electron detector
        self.bsd = None  # backscattered electron detector
        self.cld = None  # cathodoluminescnence detector (aka analog PMT)
        self.spectrometer = None  # 1D detector that returns a spectrum
        self.spectrograph = None  # actuator to change the wavelength
        self.monochromator = None  # 0D detector behind the spectrograph
        # TODO: the next two should not be needed anymore
        self.ar_spec_sel = None  # actuator to select AR/Spectrometer (SPARC)
        self.lens_switch = None  # actuator to (de)activate the lens (SPARC)
        self.chamber = None  # actuator to control the chamber (has vacuum, pumping etc.)
        self.chamber_ccd = None  # view of inside the chamber
        self.chamber_light = None   # Light illuminating the chamber
        self.overview_ccd = None  # global view from above the sample
        self.overview_focus = None  # focus of the overview CCD
        self.overview_light = None  # light of the overview CCD

        # Indicates whether the microscope is acquiring a high quality image
        self.is_acquiring = model.BooleanVA(False)

        # The microscope object will be probed for common detectors, actuators, emitters etc.
        if microscope:
            self.role = microscope.role

            for c in microscope.children.value:
                try:
                    attrname = self._ROLE_TO_ATTR[c.role]
                    setattr(self, attrname, c)
                except KeyError:
                    pass  # not interested by this component

            # Spectrograph is not directly a child, but a sub-comp of spectrometer
            if self.spectrometer:
                for child in self.spectrometer.children.value:
                    if child.role == "spectrograph":
                        self.spectrograph = child

            # Check that the components that can be expected to be present on an actual microscope
            # have been correctly detected.

            if not any((self.ccd, self.sed, self.bsd, self.spectrometer)):
                raise KeyError("No detector found in the microscope")

            if not self.light and not self.ebeam:
                raise KeyError("No emitter found in the microscope")

            # Optical path manager (for now, only used on the SPARC)
            # Used to control the actuators so that the ligth goes to the right
            # detector (in the right way).
            if microscope.role in ("sparc", "sparc2"):
                self.opm = path.OpticalPathManager(microscope)

        # Chamber is complex so we provide a "simplified state"
        # It's managed by the ChamberController. Setting to PUMPING or VENTING
        # state will request a pressure change.
        chamber_states = {CHAMBER_UNKNOWN, CHAMBER_VENTED, CHAMBER_PUMPING,
                          CHAMBER_VACUUM, CHAMBER_VENTING}
        self.chamberState = model.IntEnumerated(CHAMBER_UNKNOWN, chamber_states)

        # Used when doing fine alignment, based on the value used by the user
        # when doing manual alignment. 0.1s is not too bad value if the user
        # hasn't specified anything (yet).
        self.fineAlignDwellTime = FloatContinuous(0.1, range=[1e-9, 100],
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

    def stopMotion(self):
        """
        Stops immediately every axis
        """
        if self.microscope is None:
            return

        for c in self.microscope.children.value:
            # Actuators have an .axes roattribute
            if not isinstance(c.axes, collections.Mapping):
                continue
            try:
                # TODO: run each of them in a separate thread, to call the stop
                # ASAP? (or all but the last one?)
                c.stop()
            except Exception:
                logging.exception("Failed to stop %s actuator", c.name)

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
            raise LookupError("Failed to find tab %s among %d defined tabs" %
                              (name, len(self.tab.choices)))


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

    focussedView
    ~~~~~~~~~~~~

    Usage (02-12-2014):

    The focused view is set in the following places:

    * Tab: As a result of user generated events (i.e. mouse clicks) in overlays
    * ViewPort: When a child object of ViewPort gains focus
    * ViewPortController: - Default focus in the constructor
                          - When visible views change (i.e. make sure that the focus remains
                            with a ViewPort that is visible)
                          - Focus the ViewPort that displays a given stream
    * ViewButtonController: Focus is set on view button click

    Focused view listeners:

    * StreamController: Show the streams associated with the focused view in the stream panel
    * Tab: To track the canvas cross hair
    * ViewPortController: To set the focus to the right ViewPort
    * ViewButtonController: Set which view button is selected (This method is also called by the
                            viewLayout VA)

    viewLayout
    ~~~~~~~~~~

    Usage (02-12-2014):

    The layout of the grid is set in the following places:

    * ViewButtonController: Change the layout if needed (depending on which button was clicked)
    * Tab:  - Connection to the 2x2 vs 1x1 menu item
            - Reset to 2x2 when a new file is loaded

    View layout Listeners:

    * Tab: Connection to 2x2 menu item checkmark
    * ViewPortController: Adjust the grid layout
    * ViewButtonController: Set which view button is selected (Same method is called by the
                            focussedView VA)

    """
    __metaclass__ = ABCMeta

    def __init__(self, main):
        self.main = main

        # Streams available (handled by StreamController)
        # It should be LRU sorted, so that the latest stream is first in the list.
        # Note: we need to make sure ourselves that each stream in this
        # attribute is unique (i.e. only occurs once in the list).
        self.streams = model.ListVA()

        # Available Views. The are handled by the ViewController.
        # The `views` list basically keeps track of the relevant references.
        self.views = model.ListVA()

        # Current tool selected (from the toolbar, cf cont.tools)
        self.tool = None  # Needs to be overridden by a IntEnumerated

        # The MicroscopeView currently focused, it is one of the `views` or `None`.
        # See class docstring for more info.
        self.focussedView = VigilantAttribute(None)

        layouts = set([VIEW_LAYOUT_ONE, VIEW_LAYOUT_22, VIEW_LAYOUT_FULLSCREEN])
        self.viewLayout = model.IntEnumerated(VIEW_LAYOUT_22, choices=layouts)

        # The subset of views taken from `views` that *can* actually displayed,
        # but they might be hidden as well.
        # This attribute is also handled and manipulated by the ViewController.
        self.visible_views = model.ListVA()


class LiveViewGUIData(MicroscopyGUIData):
    """ Represent an interface used to only show the current data from the microscope.

    It should be able to handle SEM-only, optical-only, SECOM and DELPHI systems.

    """

    def __init__(self, main):
        assert main.microscope is not None
        MicroscopyGUIData.__init__(self, main)

        # Current tool selected (from the toolbar)
        tools = set([TOOL_NONE, TOOL_ZOOM, TOOL_ROI])
        self.tool = IntEnumerated(TOOL_NONE, choices=tools)

        # Represent the global state of the microscopes. Mostly indicating
        # whether optical/sem streams are active.
        hw_states = {STATE_OFF, STATE_ON, STATE_DISABLED}

        if self.main.ccd:
            self.opticalState = model.IntEnumerated(STATE_OFF, choices=hw_states)

        if self.main.ebeam:
            self.emState = model.IntEnumerated(STATE_OFF, choices=hw_states)

        # history list of visited stage positions, ordered with latest visited
        # as last entry.
        self.stage_history = model.ListVA()

        # VA for autofocus procedure mode
        self.autofocus_active = BooleanVA(False)


class ScannedAcquisitionGUIData(MicroscopyGUIData):
    """ Represent an interface used to select a precise area to scan and
    acquire signal. It allows fine control of the shape and density of the scan.
    It is specifically made for the SPARC system.
    """
    def __init__(self, main):
        assert main.microscope is not None
        MicroscopyGUIData.__init__(self, main)

        # more tools: for selecting the sub-region of acquisition

        tools = {
            TOOL_NONE,
            TOOL_ZOOM,
            TOOL_ROI,
            TOOL_ROA,
            TOOL_RO_ANCHOR,
            TOOL_POINT,
            TOOL_LINE,
            TOOL_SPOT,
        }

        self.tool = IntEnumerated(TOOL_NONE, choices=tools)

        # Very special view which is used only as a container to save which
        # stream will be acquired (for the Sparc acquisition interface only).
        # The tab controller will take care of filling it
        self.acquisitionView = MicroscopeView("Acquisition")

        # The SEM concurrent stream that is used to select the acquisition settings
        # eg, ROI (aka ROA), dcPeriod, dcRegion.
        # It is set at start-up by the tab controller, and will never be active.
        self.semStream = None

        # The Spot SEM stream, used to control spot mode.
        # It is set at start-up by the tab controller.
        self.spotStream = None

        # The position of the spot. Two floats 0->1. (None, None) if undefined.
        self.spotPosition = model.TupleVA((None, None))


class ChamberGUIData(MicroscopyGUIData):
    pass


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
        spec_bck_file = self._conf.get("calibration", "spec_bck_file")
        spec_file = self._conf.get("calibration", "spec_file")
        self.ar_cal = StringVA(ar_file) # a unicode
        self.spec_bck_cal = StringVA(spec_bck_file) # a unicode
        self.spec_cal = StringVA(spec_file) # a unicode

        self.ar_cal.subscribe(self._on_ar_cal)
        self.spec_bck_cal.subscribe(self._on_spec_bck_cal)
        self.spec_cal.subscribe(self._on_spec_cal)

    def _on_ar_cal(self, fn):
        self._conf.set("calibration", "ar_file", fn)

    def _on_spec_bck_cal(self, fn):
        self._conf.set("calibration", "spec_bck_file", fn)

    def _on_spec_cal(self, fn):
        self._conf.set("calibration", "spec_file", fn)


class ActuatorGUIData(MicroscopyGUIData):
    """
    Represent an interface used to move the actuators of a microscope. It might
    also display one or more views, but it's not required.
    => Used for the lens (SECOM) and mirror (SPARC) alignment tabs
    """
    def __init__(self, main):
        assert main.microscope is not None
        MicroscopyGUIData.__init__(self, main)

        # Step size name -> val, range, actuator, axes (None if all)
        # str -> float, [float, float], str, (str, ...)
        ss_def = {"stage": (1e-6, [100e-9, 1e-3], "stage", None),
                  # "focus": (100e-9, [10e-9, 1e-4], "focus", None),
                  "aligner": (1e-6, [100e-9, 1e-4], "aligner", None),
                  "fibaligner": (50e-6, [5e-6, 500e-6], "fibaligner", None),
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
                self.stepsizes[ss] = FloatContinuous(v, r)
                if axn is None:
                    axn = getattr(main, an).axes
                for a in axn:
                    self._axis_to_act_ss[(an, a)] = (an, ss)
                    logging.debug("Add axis %s/%s to stepsize %s", an, a, ss)

        # set of (str, str): actuator name, axis name
        self.axes = frozenset(self._axis_to_act_ss.keys())

        # Tools are for lens alignment (mirror alignment actually needs none)
        tools = {TOOL_NONE, TOOL_DICHO, TOOL_SPOT}
        self.tool = IntEnumerated(TOOL_NONE, choices=tools)

        # For dichotomic mode (SECOM)
        self.dicho_seq = model.ListVA()  # list of 4 enumerated for each corner

        # For the SPARC, the current alignment mode. Same values than the modes
        # of the OpticalPathManager
        # TODO: move to subclass
        self.align_mode = StringEnumerated("chamber-view", choices={"chamber-view", "mirror-align", "fiber-align"})

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
            f.result()  # wait until the future is complete


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
        self.thumbnail = VigilantAttribute(None)  # contains a wx.Image

        # Last time the image of the view was changed. It's actually mostly
        # a trick to allow other parts of the GUI to know when the (theoretical)
        # composited image has changed.
        self.lastUpdate = model.FloatVA(time.time(), unit="s")

    def __unicode__(self):
        return u"{}".format(self.name.value)

    def __str__(self):
        return "{}".format(self.name.value)


MAX_SAFE_MOVE_DISTANCE = 10e-3  # 1 cm


class StreamView(View):
    """
    An abstract class that is common for every view which display layers of
    streams and might have also actuators such as a stage and a focus.

    Basically, its "input" is a StreamTree and it can request stage and focus
    move. It never computes the composited image from all the streams itself.
    It's up to other objects (e.g., the canvas) to ask the StreamTree for its
    latest image (the main goal of this scheme is to avoid computation when not
    needed). Similarly, the thumbnail is never automatically recomputed, but
    other objects can update it.
    """

    def __init__(self, name, stage=None, stream_classes=None):
        """
        :param name (string): user-friendly name of the view
        :param stage (Actuator): actuator with two axes: x and y
        :param stream_classes (None, or tuple of classes): all subclasses that the
          streams in this view is allowed to show.
        """

        super(StreamView, self).__init__(name)

        if stream_classes is None:
            self.stream_classes = (Stream,)
        else:
            self.stream_classes = stream_classes
        self._stage = stage

        # Will be created on the first time it's needed
        self._focus_thread = None
        self._focus_queue = Queue.Queue()  # contains tuples of (focuser, relative distance)

        # The real stage position, to be modified via moveStageToView()
        # it's a direct access from the stage, so looks like a dict of axes
        if stage:
            self.stage_pos = stage.position

            # the current center of the view, which might be different from
            # the stage
            pos = self.stage_pos.value
            view_pos_init = (pos["x"], pos["y"])
        else:
            view_pos_init = (0, 0)

        self.view_pos = model.ListVA(view_pos_init, unit="m")

        self._fstage_move = InstantaneousFuture() # latest future representing a move request

        # current density (meter per pixel, ~ scale/zoom level)
        # 1µm/px => ~large view of the sample (view width ~= 1000 px)
        self.mpp = FloatContinuous(1e-6, range=(10e-12, 50e-6), unit="m/px")

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

    def has_stage(self):
        return self._stage is not None

    def _moveFocus(self):
        time_last_move = 0
        try:
            while True:
                # wait until there is something to do
                focuser, shift = self._focus_queue.get()
                pos = focuser.position.value["z"]
                axis = focuser.axes["z"]
                try:
                    rng = axis.range
                except AttributeError:
                    rng = None

                # rate limit to 20 Hz
                sleept = time_last_move + 0.05 - time.time()
                # We always wait a bit, so that we don't start with a tiny move
                time.sleep(max(0.05, sleept))

                # Add more moves if there are already more
                try:
                    nf = focuser
                    while nf is focuser:
                        nf, ns = self._focus_queue.get(block=False)
                        shift += ns
                        if nf is not focuser:
                            # Oops, got a bit too fast, and read message for another focuser => put it back
                            self._focus_queue.put((nf, ns))
                except Queue.Empty:
                    pass
                logging.debug("Moving focus '%s' by %f μm", focuser.name, shift * 1e6)

                # clip to the range
                if rng:
                    new_pos = pos + shift
                    new_pos = max(rng[0], min(new_pos, rng[1]))
                    req_shift = shift
                    shift = new_pos - pos
                    if abs(shift - req_shift) > 1e-9:
                        logging.info("Restricting focus move to %f µm as it reached the end",
                                     shift * 1e6)

                f = focuser.moveRel({"z": shift})
                time_last_move = time.time()
                # wait until it's finished so that we don't accumulate requests,
                # but instead only do requests of size "big enough"
                try:
                    f.result()
                except Exception:
                    logging.info("Failed to apply focus move", exc_info=1)
        except Exception:
            logging.exception("Focus mover thread failed")

    def moveFocusRel(self, shift):
        """
        shift (float): position change in "virtual pixels".
            >0: toward up/right
            Note: "virtual pixel" represents the number of pixels, taking into
            account mouse movement and key context. So it can be different from
            the actual number of pixels that were moved by the mouse.
        return (float): actual distance moved by the focus in meter
        """
        # FIXME: "stop all axes" should also clear the queue

        # TODO: optimise by only updating focuser when the stream tree changes
        for s in self.getStreams():
            if s.should_update.value:
                focuser = s.focuser
                curr_s = s
                break
        else:
            logging.info("Trying to change focus while no stream is playing")
            return 0

        # Create the focus thread if it's not yet existing
        if self._focus_thread is None:
            self._focus_thread = threading.Thread(target=self._moveFocus,
                                      name="Focus mover view %s" % self.name.value)
            # TODO: way to detect the view is not used and so we need to stop the thread?
            # (cf __del__?)
            self._focus_thread.daemon = True
            self._focus_thread.start()

        # TODO: optimise with the focuser
        # Find the depth of field (~ the size of one "focus step")
        for c in (curr_s.detector, curr_s.emitter):
            if hasattr(c, "depthOfField") and isinstance(c.depthOfField, model.VigilantAttributeBase):
                dof = c.depthOfField.value
                break
        else:
            logging.debug("No depth of field info found")
            dof = 1e-6  # m, not too bad value

        # positive == opt lens goes up == closer from the sample
        # k is a magical constant that allows to ensure a small move has a small
        # effect, and a big move has a significant effect.
        k = 50e-3  # 1/px
        val = dof * k * shift  # m
        assert(abs(val) < 0.01)  # a move of 1 cm is a clear sign of bug
        self._focus_queue.put((focuser, val))
        return val

    def moveStageBy(self, shift):
        """
        Request a relative move of the stage
        pos (tuple of 2 float): X, Y offset in m
        :return (None or Future): a future (that allows to know when the move is finished)
        """
        if not self._stage:
            return None

        # TODO: Use the max FoV of the streams to determine what's a big
        # distance (because on the overview cam a  move can be much bigger than
        # on a SEM image at high mag).

        # Check it makes sense (=> not too big)
        distance = math.hypot(*shift)
        if distance > MAX_SAFE_MOVE_DISTANCE:
            logging.error("Cancelling request to move by %f m (because > %f m)",
                          distance, MAX_SAFE_MOVE_DISTANCE)
            return
        elif distance < 0.1e-9:
            logging.debug("skipping move request of almost 0")
            return

        move = {"x": shift[0], "y": shift[1]}
        logging.debug("Sending move request of %s", move)
        f = self._stage.moveRel(move)
        self._fstage_move = f
        f.add_done_callback(self._on_stage_move_done)
        return f

    def moveStageToView(self):
        """ Move the stage to the current view_pos

        :return (None or Future): a future (that allows to know when the move is finished)

        Note: once the move is finished stage_pos will be updated (by the
        back-end)
        """
        if not self._stage:
            return

        view_pos = self.view_pos.value
        prev_pos = self.stage_pos.value
        shift = (view_pos[0] - prev_pos["x"], view_pos[1] - prev_pos["y"])
        return self.moveStageBy(shift)

    def moveStageTo(self, pos):
        """
        Request an absolute move of the stage to a given position
        pos (tuple of 2 float): X, Y absolute coordinates
        :return (None or Future): a future (that allows to know when the move is finished)
        """
        if not self._stage:
            return None

        move = {"x": pos[0], "y": pos[1]}
        # TODO: clip to the range of the axes
        f = self._stage.moveAbs(move)
        self._fstage_move = f
        f.add_done_callback(self._on_stage_move_done)
        return f

    def _on_stage_move_done(self, f):
        """
        Called whenever a stage move is completed
        """
        ex = f.exception()
        if ex:
            # TODO: make it warning once it doesn't automatically pops up the panel
            logging.info("Stage move failed: %s", ex)

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
            logging.warning("Aborting the addition of a duplicate stream")
            return

        if not isinstance(stream, self.stream_classes):
            msg = "Adding incompatible stream '%s' to view '%s'. %s needed"
            logging.warning(msg, stream.name.value, self.name.value, self.stream_classes)

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
            if stream not in self.stream_tree.getStreams():
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


class MicroscopeView(StreamView):
    """
    Represents a view from a microscope and ways to alter it.
    It will stay centered on the stage position.
    """
    def __init__(self, name, stage=None, **kwargs):
        StreamView.__init__(self, name, stage=stage, **kwargs)
        if stage:
            self.stage_pos.subscribe(self._on_stage_pos)

    def _on_stage_pos(self, pos):
        # we want to recenter the viewports whenever the stage moves

        # Don't recenter if a stage move has been requested and on going
        # as view_pos is already at the (expected) final position
        if not self._fstage_move.done():
            return

        self.view_pos.value = [pos["x"], pos["y"]]

    def _on_stage_move_done(self, f):
        """
        Called whenever a stage move is completed
        """
        super(MicroscopeView, self)._on_stage_move_done(f)
        self._on_stage_pos(self.stage_pos.value)

class ContentView(StreamView):
    """
    Represents a view from a microscope but (almost) always centered on the
    content
    """
    def __init__(self, name, **kwargs):
        StreamView.__init__(self, name, **kwargs)

    def _onNewImage(self, im):
        # Don't recenter if a stage move has been requested and on going
        # as view_pos is already at the (expected) final position
        if self._fstage_move.done() and im is not None:
            # Move the center's view to the center of this new image
            try:
                pos = im.metadata[MD_POS]
            except IndexError:
                pass
            else:
                self.view_pos.value = pos

        super(ContentView, self)._onNewImage(im)

    # Note: we don't reset the view position at the end of the move. It will
    # only be reset on the next image after the end of the move (if it ever
    # comes). This is done on purpose to clearly show that the image displayed
    # is not yet at the place where the move finished.

class OverviewView(StreamView):
    """
    A large FoV view which is used to display the previous positions reached
    (if possible) on top of an overview image of the sample.
    The main difference with the standard MicroscopeView is that it is not
    centered on the current stage position.
    """
    def __init__(self, name, **kwargs):
        StreamView.__init__(self, name, **kwargs)

        self.show_crosshair.value = False
