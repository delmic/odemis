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
import logging
import math
from odemis import model
from odemis.acq.stream import Stream, StreamTree
from odemis.gui.conf import get_general_conf
from odemis.model import (FloatContinuous, VigilantAttribute, IntEnumerated,
                          NotSettableError, StringVA, getVAs)
import os
import threading
import time


# The different states of a microscope
STATE_OFF = 0
STATE_ON = 1
STATE_DISABLED = 2 # TODO: use this state when cannot be used

# Chamber states
CHAMBER_UNKNOWN = 0  # Chamber in an unknown state
CHAMBER_VENTED = 1   # Chamber can be opened
CHAMBER_VACUUM = 2   # Chamber ready for imaging
CHAMBER_PUMPING = 3  # Decreasing chamber pressure (set it to request pumping)
CHAMBER_VENTING = 4  # Pressurizing chamber (set it to request venting)

# The different types of view layouts
VIEW_LAYOUT_ONE = 0 # one big view
VIEW_LAYOUT_22 = 1 # 2x2 layout
VIEW_LAYOUT_FULLSCREEN = 2 # Fullscreen view (not yet supported)

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
        self.aligner = None  # actuator to align ebeam/ccd
        self.mirror = None  # actuator to change the mirror position (on SPARC)
        self.light = None
        self.light_filter = None  # emission light filter for SECOM/output filter for SPARC
        self.lens = None
        self.ebeam = None
        self.ebeam_focus = None  # change the e-beam focus
        self.sed = None  # secondary electron detector
        self.bsd = None  # back-scatter electron detector
        self.spectrometer = None  # spectrometer
        self.spectrograph = None  # actuator to change the wavelength
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

            for d in microscope.detectors:
                if d.role == "ccd":
                    self.ccd = d
                elif d.role == "se-detector":
                    self.sed = d
                elif d.role == "bs-detector":
                    self.bsd = d
                elif d.role == "spectrometer":
                    self.spectrometer = d
                elif d.role == "chamber-ccd":
                    self.chamber_ccd = d
                elif d.role == "overview-ccd":
                    self.overview_ccd = d

            for a in microscope.actuators:
                if a.role == "stage":
                    self.stage = a # most views move this actuator when moving
                elif a.role == "focus":
                    self.focus = a
                elif a.role == "ebeam-focus":
                    self.ebeam_focus = a
                elif a.role == "overview-focus":
                    self.overview_focus = a
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
                elif e.role == "filter":
                    self.light_filter = e
                elif e.role == "lens":
                    self.lens = e
                elif e.role == "e-beam":
                    self.ebeam = e
                elif e.role == "chamber-light":
                    self.chamber_light = e
                elif e.role == "overview-light":
                    self.overview_light = e

            # Check that the components that can be expected to be present on an actual microscope
            # have been correctly detected.

            if not any((self.ccd, self.sed, self.bsd, self.spectrometer)):
                raise KeyError("No detector found in the microscope")

            if not self.light and not self.ebeam:
                raise KeyError("No emitter found in the microscope")

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

        # Represent the global state of the microscopes. Mostly indicating
        # whether optical/sem streams are active.
        hw_states = {STATE_OFF, STATE_ON, STATE_DISABLED}

        if self.main.ccd:
            self.opticalState = model.IntEnumerated(STATE_OFF, choices=hw_states)

        if self.main.ebeam:
            self.emState = model.IntEnumerated(STATE_OFF, choices=hw_states)


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

        # TODO: unused for now => get rid of them? cf tab controller
        # It'd better to actually use them, and move some of the code to its own
        # controller
        # # Handle turning on/off the instruments
        # hw_states = {STATE_OFF, STATE_ON}
        #
        # if self.main.ccd:
        #     self.arState = model.IntEnumerated(STATE_OFF, choices=hw_states)
        #
        # if self.main.spectrometer:
        #     self.specState = model.IntEnumerated(STATE_OFF, choices=hw_states)


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

    def _on_spec_cal(self, fn):
        self._conf.set("calibration", "spec_file", fn)


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
                  # "focus": (100e-9, [10e-9, 1e-4], "focus", None),
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
                self.stepsizes[ss] = FloatContinuous(v, r)
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


MAX_SAFE_MOVE_DISTANCE = 1000e-3  # 100 cm


class MicroscopeView(View):
    """ Represents a view from a microscope and ways to alter it.

    Basically, its "input" is a StreamTree and it can request stage and focus
    move. It never computes the composited image from all the streams itself.
    It's up to other objects (e.g., the canvas) to ask the StreamTree for its
    latest image (the main goal of this scheme is to avoid computation when not
    needed). Similarly, the thumbnail is never automatically recomputed, but
    other objects can update it.
    """

    def __init__(self, name, stage=None, focus=None, stream_classes=None):
        """
        :param name (string): user-friendly name of the view
        :param stage (Actuator): actuator with two axes: x and y
        :param focus (Actuator): actuator with one axis: z. Can be None
        :param stream_classes (None, or tuple of classes): all subclasses that the
          streams in this view is allowed to show.
        """

        super(MicroscopeView, self).__init__(name)

        if stream_classes is None:
            self.stream_classes = (Stream,)
        else:
            self.stream_classes = stream_classes
        self._stage = stage

        # TODO: allow to have multiple focus, one per stream class
        self.focus = focus
        if focus is not None:
            self._focus_queue = Queue.Queue()
            self._focus_thread = threading.Thread(target=self._moveFocus,
                                                  name="Focus mover view %s" % name)
            # TODO: way to detect the view is not used and so we need to stop the thread? (cf __del__?)
            self._focus_thread.daemon = True
            self._focus_thread.start()

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

    def has_stage(self):
        return self._stage is not None

    def _moveFocus(self):
        time_last_move = 0
        axis = self.focus.axes["z"]
        try:
            rng = axis.range
        except AttributeError:
            rng = None
        try:
            while True:
                # wait until there is something to do
                shift = self._focus_queue.get()
                pos = self.focus.position.value["z"]

                # rate limit to 20 Hz
                sleept = time_last_move + 0.05 - time.time()

                if sleept > 0:
                    time.sleep(sleept)

                # Add more moves if there are already more
                try:
                    while True:
                        shift += self._focus_queue.get(block=False)
                except Queue.Empty:
                    pass
                logging.debug("Moving focus by %f μm", shift * 1e6)

                # clip to the range
                if rng:
                    new_pos = pos + shift
                    new_pos = max(rng[0], min(new_pos, rng[1]))
                    req_shift = shift
                    shift = new_pos - pos
                    if abs(shift - req_shift) > 1e-9:
                        logging.info("Restricting focus move to %f µm as it reached the end",
                                     shift * 1e6)

                f = self.focus.moveRel({"z": shift})
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
        # FIXME: "stop all axes" should also clear the queue
        self._focus_queue.put(shift)

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

        # Absolute move would work too, but by using relative move we can do a
        # couple more checks, to be sure we will not do crazy things.
        #     move = {"x": pos[0], "y": pos[1]}
        #     self._stage.moveAbs(move)

        # TODO: Use the max FoV of the streams to determine what's a big
        # distance (because on the overview cam a  move can be much bigger than
        # on a SEM image at high mag).

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


