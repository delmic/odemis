# -*- coding: utf-8 -*-
"""
:created: 16 Feb 2012
:author: Éric Piel
:copyright: © 2012 - 2022 Éric Piel, Rinze de Laat, Philip Winkler, Delmic

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
import logging
import math
from abc import ABCMeta
from typing import Tuple

import odemis.acq.stream as acqstream
from odemis import model
from odemis.acq.feature import CryoFeature
from odemis.gui import conf
from odemis.gui.conf import get_general_conf
from odemis.gui.cont.fastem_project_tree import FastEMTreeNode, NodeType
from odemis.gui.model._constants import (
    FLM_ALIGN,
    SEM_ALIGN,
    STATE_DISABLED,
    STATE_OFF,
    STATE_ON,
    TOOL_DICHO,
    TOOL_FEATURE,
    TOOL_LABEL,
    TOOL_LINE,
    TOOL_NONE,
    TOOL_POINT,
    TOOL_RO_ANCHOR,
    TOOL_ROA,
    TOOL_RULER,
    TOOL_SPOT,
    VIEW_LAYOUT_22,
    VIEW_LAYOUT_DYNAMIC,
    VIEW_LAYOUT_FULLSCREEN,
    VIEW_LAYOUT_ONE,
    VIEW_LAYOUT_VERTICAL,
    Z_ALIGN,
)
from odemis.model import (
    MD_CALIB,
    BooleanVA,
    FloatContinuous,
    IntEnumerated,
    StringEnumerated,
    StringVA,
    VigilantAttribute,
)
from odemis.util.filename import create_filename, make_unique_name


class MicroscopyGUIData(metaclass=ABCMeta):
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

        # Available viewports
        self.viewports = model.ListVA()

        # Current tool selected (from the toolbar, cf cont.tools)
        # Child can update the .choices with extra TOOL_*
        self.tool = IntEnumerated(TOOL_NONE, choices={TOOL_NONE})

        # The MicroscopeView currently focused, it is one of the `views` or `None`.
        # See class docstring for more info.
        self.focussedView = VigilantAttribute(None)

        layouts = {VIEW_LAYOUT_ONE, VIEW_LAYOUT_22, VIEW_LAYOUT_FULLSCREEN, VIEW_LAYOUT_DYNAMIC}
        self.viewLayout = model.IntEnumerated(VIEW_LAYOUT_22, choices=layouts)

        # The subset of views taken from `views` that *can* actually displayed,
        # but they might be hidden as well.
        # This attribute is also handled and manipulated by the ViewController.
        self.visible_views = model.ListVA()


class AcquisitionWindowData(MicroscopyGUIData):
    """ Represent an interface used to only show the streams ready to acquire.
    """

    def __init__(self, main):
        assert main.microscope is not None
        MicroscopyGUIData.__init__(self, main)
        self.viewLayout = model.IntEnumerated(VIEW_LAYOUT_ONE, choices={VIEW_LAYOUT_ONE})


class LiveViewGUIData(MicroscopyGUIData):
    """ Represent an interface used to only show the current data from the microscope.

    It should be able to handle SEM-only, optical-only, SECOM and DELPHI systems.

    """

    def __init__(self, main):
        assert main.microscope is not None
        MicroscopyGUIData.__init__(self, main)

        # Current tool selected (from the toolbar)
        tools = {TOOL_NONE, TOOL_RULER}  # TOOL_ZOOM, TOOL_ROI}
        if main.time_correlator: # FLIM
            tools.add(TOOL_ROA)

            self.roa = model.TupleContinuous(acqstream.UNDEFINED_ROI,
                                             range=((0, 0, 0, 0), (1, 1, 1, 1)),
                                             cls=(int, float))

            # Component to which the (relative) ROIs and spot position refer to for
            # the field-of-view.
            self.fovComp = None

            # This requires tc_detector. For now we assume that if a
            # time_correlator is present, the tc_detector is also present.
            # (although it's technically not entirely true, as we could move
            # the laser-mirror and do an acquisition without it, just no feedback)
            tools.add(TOOL_SPOT)

            # The SpotConfocalstream, used to control spot mode.
            # It is set at start-up by the tab controller.
            self.spotStream = None

        # Update the tool selection with the new tool list
        self.tool.choices = tools

        # The position of the spot. Two floats 0->1. (None, None) if undefined.
        self.spotPosition = model.TupleVA((None, None))

        # Represent the global state of the microscopes. Mostly indicating
        # whether optical/sem streams are active.
        hw_states = {STATE_OFF, STATE_ON, STATE_DISABLED}

        if main.ccd or main.photo_ds:
            self.opticalState = model.IntEnumerated(STATE_OFF, choices=hw_states)
            if main.laser_mirror:
                # For storing shared settings to all confocal streams
                self.confocal_set_stream = None

        if main.ebeam:
            self.emState = model.IntEnumerated(STATE_OFF, choices=hw_states)

        # history list of visited stage positions, ordered with latest visited
        # as last entry.
        self.stage_history = model.ListVA()

        # VA for autofocus procedure mode
        self.autofocus_active = BooleanVA(False)


class CryoGUIData(MicroscopyGUIData):
    """
    Represents an interface for handling cryo microscopes.
    """
    def __init__(self, main):
        if not main.is_viewer and main.role not in ("enzel", "meteor", "mimas"):
            raise ValueError(
                "Expected a microscope role of 'enzel', 'meteor', or 'mimas' but found it to be %s." % main.role)
        super().__init__(main)

    def add_new_feature(self, pos_x, pos_y, pos_z=None, f_name=None):
        """
        Create a new feature and add it to the features list
        """
        if not f_name:
            existing_names = [f.name.value for f in self.main.features.value]
            f_name = make_unique_name("Feature-1", existing_names)
        if pos_z is None:
            pos_z = self.main.focus.position.value['z']
        feature = CryoFeature(f_name, pos_x, pos_y, pos_z)
        self.main.features.value.append(feature)
        self.main.currentFeature.value = feature
        return feature

    # Todo: find the right margin
    ATOL_FEATURE_POS = 0.1e-3  # m

    def select_current_position_feature(self):
        """
        Given current stage position, either select one of the features closest to
          the position or create a new one with the position.
        """
        current_position = self.main.stage.position.value
        current_feature = self.main.currentFeature.value

        def dist_to_pos(feature):
            return math.hypot(feature.pos.value[0] - current_position["x"],
                              feature.pos.value[1] - current_position["y"])


        if current_feature and dist_to_pos(current_feature) <= self.ATOL_FEATURE_POS:
            return  # We are already good, nothing else to do

        # Find the closest feature... and check it's actually close by
        try:
            closest = min(self.main.features.value, key=dist_to_pos)
            if dist_to_pos(closest) <= self.ATOL_FEATURE_POS:
                self.main.currentFeature.value = closest
                return
        except ValueError:  # raised by min() if no features at all
            pass

        # No feature nearby => create a new one
        feature = self.add_new_feature(current_position["x"], current_position["y"],
                                       self.main.focus.position.value["z"])
        logging.debug("New feature created at %s because none are close by.",
                      (current_position["x"], current_position["y"]))
        self.main.currentFeature.value = feature


class CryoLocalizationGUIData(CryoGUIData):
    """ Represent an interface used to only show the current data from the microscope.

    It it used for handling CryoSECOM systems.

    """

    def __init__(self, main):
        super().__init__(main)

        # Current tool selected (from the toolbar)
        tools = {TOOL_NONE, TOOL_RULER, TOOL_FEATURE}
        # Update the tool selection with the new tool list
        self.tool.choices = tools
        # VA for autofocus procedure mode
        self.autofocus_active = BooleanVA(False)
        # the zstack minimum range below current focus position
        self.zMin = model.FloatContinuous(
            value=-10e-6, range=(-1000e-6, 0), unit="m")
        # the zstack maximum range above current focus position
        self.zMax = model.FloatContinuous(
            value=10e-6, range=(0, 1000e-6), unit="m")
        # the distance between two z-levels
        self.zStep = model.FloatContinuous(
            value=1e-6, range=(-100e-6, 100e-6), unit="m")
        # for enabling/disabling z-stack acquisition
        self.zStackActive = model.BooleanVA(value=False)
        # the streams to acquire among all streams in .streams
        self.acquisitionStreams = model.ListVA()
        # the static overview map streams, among all streams in .streams
        self.overviewStreams = model.ListVA()
        # for the filename
        config = conf.get_acqui_conf()
        self.filename = model.StringVA(create_filename(
            config.pj_last_path, config.fn_ptn,
            config.last_extension,
            config.fn_count))
        self.main.project_path.subscribe(self._on_project_path_change)
        # Add zPos VA to control focus on acquired view
        self.zPos = model.FloatContinuous(0, range=(0, 0), unit="m")
        self.zPos.clip_on_range = True
        self.streams.subscribe(self._on_stream_change, init=True)

        if main.stigmator:
            # stigmator should have a "MD_CALIB" containing a dict[float, dict],
            # where the key is the stigmator angle (rad), and the value contains
            # the calibration to pass to z_localization.determine_z_position().
            calib = main.stigmator.getMetadata().get(MD_CALIB)
            if calib:
                angles = frozenset(calib.keys())
                rng = main.stigmator.axes["rz"].range
                for a in angles:
                    if not rng[0] <= a <= rng[1]:
                        raise ValueError(f"stigmator MD_CALIB has angle {a} outside of range {rng}.")

                self.stigmatorAngle = model.FloatEnumerated(min(angles), choices=angles)
            else:
                logging.warning("stigmator component present, but no MD_CALIB, Z localization will be disabled")

    def _updateZParams(self):
        # Calculate the new range of z pos
        # NB: this is a copy of AnalysisGUIData._updateZParams
        limits = []

        for s in self.streams.value:
            if model.hasVA(s, "zIndex"):
                metadata = s.getRawMetadata()[0]  # take only the first
                zcentre = metadata[model.MD_POS][2]
                zstep = metadata[model.MD_PIXEL_SIZE][2]
                limits.append(zcentre - s.zIndex.range[1] * zstep / 2)
                limits.append(zcentre + s.zIndex.range[1] * zstep / 2)

        if len(limits) > 1:
            self.zPos.range = (min(limits), max(limits))
            logging.debug("Z stack display range updated to %f - %f, ZPos: %f",
                          self.zPos.range[0], self.zPos.range[1], self.zPos.value)
        else:
            self.zPos.range = (0, 0)

    def _on_stream_change(self, _):
        self._updateZParams()

    def _on_project_path_change(self, _):
        config = conf.get_acqui_conf()
        self.filename.value = create_filename(
                    config.pj_last_path, config.fn_ptn,
                    config.last_extension,
                    config.fn_count)


class CryoCorrelationGUIData(CryoGUIData):
    """ Represent an interface used to correlate multiple streams together.

    Used for METEOR systems.

    """

    def __init__(self, main):
        super().__init__(main)

        # Current tool selected (from the toolbar)
        tools = {TOOL_NONE, TOOL_RULER}
        # Update the tool selection with the new tool list
        self.tool.choices = tools

        # the streams to correlate among all streams in .streams
        self.selected_stream = model.VigilantAttribute(None)

        # for export tool
        self.acq_fileinfo = VigilantAttribute(None) # a FileInfo


class SparcAcquisitionGUIData(MicroscopyGUIData):
    """ Represent an interface used to select a precise area to scan and
    acquire signal. It allows fine control of the shape and density of the scan.
    It is specifically made for the SPARC system.
    """
    def __init__(self, main):
        assert main.microscope is not None
        MicroscopyGUIData.__init__(self, main)

        # more tools: for selecting the sub-region of acquisition

        self.tool.choices = {
            TOOL_NONE,
            #TOOL_ZOOM,
            #TOOL_ROI,
            TOOL_ROA,
            TOOL_RO_ANCHOR,
            TOOL_RULER,
            TOOL_SPOT,
        }

        # List of streams to be acquired (as the ones used to display the live
        # view are different)
        self.acquisitionStreams = set()

        # Component to which the (relative) ROIs and spot position refer to for
        # the field-of-view.
        self.fovComp = None

        # The SEM concurrent stream that is used to select the acquisition settings
        # eg, ROI (aka ROA). It also gets Leeches to run during the entire
        # series of acquisition (ie, the drift corrector and/or PCD acquirer).
        # It is set at start-up by the tab controller, and will never be active.
        self.semStream = None

        # Should be a TupleContinuous VA.
        # It is set at start-up by the tab controller.
        self.roa = None

        # The Spot SEM stream, used to control spot mode.
        # It is set at start-up by the tab controller.
        self.spotStream = None

        # The position of the spot. Two floats 0->1. (None, None) if undefined.
        self.spotPosition = model.TupleVA((None, None))

        # The leech to be used for drift correction (AnchorDriftCorrector)
        # It is set at start-up by the tab controller.
        self.driftCorrector = None

        # Whether to use a scan stage (if there is one)
        self.useScanStage = model.BooleanVA(False, readonly=(main.scan_stage is None))

        # Whether to acquire the probe current (via a Leech)
        self.pcdActive = model.BooleanVA(False, readonly=(main.pcd is None))

        # TODO: VA for autofocus procedure mode needs to be connected in the tab
#         self.autofocus_active = BooleanVA(False)


class ChamberGUIData(MicroscopyGUIData):

    def __init__(self, main):
        MicroscopyGUIData.__init__(self, main)
        self.viewLayout = model.IntEnumerated(VIEW_LAYOUT_ONE, choices={VIEW_LAYOUT_ONE})

        # TODO: VA for autofocus procedure mode needs to be connected in the tab.
        # It's not really recommended (and there is no toolbar button), but it's
        # possible to change the focus, and the menu is there, so why not.
#         self.autofocus_active = BooleanVA(False)


class CryoChamberGUIData(CryoGUIData):

    def __init__(self, main):
        CryoGUIData.__init__(self, main)
        self.viewLayout = model.IntEnumerated(VIEW_LAYOUT_ONE, choices={VIEW_LAYOUT_ONE})

        self.stage_align_slider_va = model.FloatVA(1e-6)
        self.show_advaned = model.BooleanVA(False)


class AnalysisGUIData(MicroscopyGUIData):
    """
    Represent an interface used to show the recorded microscope data. Typically
    it represents all the data present in a specific file.
    All the streams should be StaticStreams
    """
    def __init__(self, main):
        MicroscopyGUIData.__init__(self, main)
        self._conf = get_general_conf()

        # only tool to zoom and pick point/line/ruler
        self.tool.choices = {TOOL_NONE, TOOL_RULER, TOOL_POINT, TOOL_LABEL, TOOL_LINE}  # TOOL_ZOOM

        # The current file it displays. If None, it means there is no file
        # associated to the data displayed
        self.acq_fileinfo = VigilantAttribute(None) # a FileInfo

        # The current file being used for calibration. It is set to u""
        # when no calibration is used. They are directly synchronised with the
        # configuration file.
        ar_file = self._conf.get("calibration", "ar_file")
        spec_bck_file = self._conf.get("calibration", "spec_bck_file")
        temporalspec_bck_file = self._conf.get("calibration", "temporalspec_bck_file")
        angularspec_bck_file = self._conf.get("calibration", "angularspec_bck_file")
        spec_file = self._conf.get("calibration", "spec_file")
        self.ar_cal = StringVA(ar_file) # a unicode
        self.spec_bck_cal = StringVA(spec_bck_file) # a unicode
        self.temporalspec_bck_cal = StringVA(temporalspec_bck_file)  # a unicode
        self.angularspec_bck_cal = StringVA(angularspec_bck_file)
        self.spec_cal = StringVA(spec_file)  # a unicode

        self.ar_cal.subscribe(self._on_ar_cal)
        self.spec_bck_cal.subscribe(self._on_spec_bck_cal)
        self.temporalspec_bck_cal.subscribe(self._on_temporalspec_bck_cal)
        self.angularspec_bck_cal.subscribe(self._on_angularspec_bck_cal)
        self.spec_cal.subscribe(self._on_spec_cal)

        self.zPos = model.FloatContinuous(0, range=(0, 0), unit="m")
        self.zPos.clip_on_range = True
        self.streams.subscribe(self._on_stream_change, init=True)

    def _updateZParams(self):
        # Calculate the new range of z pos
        limits = []

        for s in self.streams.value:
            if model.hasVA(s, "zIndex"):
                metadata = s.getRawMetadata()[0]  # take only the first
                zcentre = metadata[model.MD_POS][2]
                zstep = metadata[model.MD_PIXEL_SIZE][2]
                limits.append(zcentre - s.zIndex.range[1] * zstep / 2)
                limits.append(zcentre + s.zIndex.range[1] * zstep / 2)

        if len(limits) > 1:
            self.zPos.range = (min(limits), max(limits))
            logging.debug("Z stack display range updated to %f - %f, ZPos: %f",
                          self.zPos.range[0], self.zPos.range[1], self.zPos.value)
        else:
            self.zPos.range = (0, 0)

    def _on_ar_cal(self, fn):
        self._conf.set("calibration", "ar_file", fn)

    def _on_spec_bck_cal(self, fn):
        self._conf.set("calibration", "spec_bck_file", fn)

    def _on_temporalspec_bck_cal(self, fn):
        self._conf.set("calibration", "temporalspec_bck_file", fn)

    def _on_angularspec_bck_cal(self, fn):
        self._conf.set("calibration", "angularspec_bck_file", fn)

    def _on_spec_cal(self, fn):
        self._conf.set("calibration", "spec_file", fn)

    def _on_stream_change(self, streams):
        self._updateZParams()


class ActuatorGUIData(MicroscopyGUIData):
    """
    Represent an interface used to move the actuators of a microscope. It might
    also display one or more views, but it's not required.
    => Used for the SECOM and SPARC(v2) alignment tabs
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
                  "lens_mover": (50e-6, [5e-6, 500e-6], "lens_mover", None),
                  "lens_switch": (50e-6, [5e-6, 500e-6], "lens_switch", None),
                  # There is not way to change the spec_focus stepsize in the GUI.
                  # On the typical SPARCv2, the smallest step is ~10µm, anything below will not move.
                  "spec_focus": (100e-6, [1e-6, 1000e-6], "spectrograph", {"focus"}),
                  "mirror_r": (10e-6, [100e-9, 1e-3], "mirror", {"ry", "rz"}),
                  # SPARCv2 light aligner dichroic mirror and spec switch foldable mirror
                  "light_aligner": (50e-6, [5e-6, 500e-6], "light_aligner", None),
                  "spec_switch": (50e-6, [5e-6, 500e-6], "spec_switch", None),
                  }
        if main.spec_ded_aligner:
            ss_def.update({
                "spec_ded_aligner_xy": (5e-6, [100e-9, 1e-3], "spec_ded_aligner", {"x", "y"}),
                "spec_ded_aligner_z": (25e-6, [5e-6, 500e-6], "spec_ded_aligner", {"z"}),
            })

        # Use mirror_xy preferably, and fallback to mirror
        if main.mirror_xy:
            # Typically for the SPARCv2
            ss_def.update({
                "mirror": (10e-6, [100e-9, 1e-3], "mirror_xy", None),
            })
        elif main.mirror:
            # SPARC mirror Y usually needs to be 10x bigger than X
            ss_def.update({
                "mirror_x": (1e-6, [100e-9, 1e-3], "mirror", {"x"}),
                "mirror_y": (10e-6, [100e-9, 1e-3], "mirror", {"y"}),
            })

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

                all_axn = set(getattr(main, an).axes.keys())
                if axn is None: # take all of them
                    axn = all_axn
                else: # take only the one listed
                    axn &= all_axn

                for a in axn:
                    self._axis_to_act_ss[(an, a)] = (an, ss)
                    logging.debug("Add axis %s/%s to stepsize %s", an, a, ss)

        # set of (str, str): actuator name, axis name
        self.axes = frozenset(self._axis_to_act_ss.keys())

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

        logging.debug("Requesting %s.%s to move by %s m", a.name, axis, ss)
        move = {axis: ss}
        f = a.moveRel(move)

        if sync:
            f.result()  # wait until the future is complete
        else:
            f.add_done_callback(self._on_axis_move_done)

    def _on_axis_move_done(self, f):
        """
        Called whenever a move is completed, just to log error
        """
        ex = f.exception()
        if ex:
            logging.warning("Move failed: %s", ex)


class SecomAlignGUIData(ActuatorGUIData):
    def __init__(self, main):
        ActuatorGUIData.__init__(self, main)
        # Tools are for lens alignment (mirror alignment actually needs none)
        self.tool.choices = {TOOL_NONE, TOOL_DICHO, TOOL_SPOT}

        self.viewLayout = model.IntEnumerated(VIEW_LAYOUT_ONE, choices={VIEW_LAYOUT_ONE})

        # For dichotomic mode
        self.dicho_seq = model.ListVA()  # list of 4 enumerated for each corner


class EnzelAlignGUIData(ActuatorGUIData):
    def __init__(self, main):
        ActuatorGUIData.__init__(self, main)
        self.viewLayout = model.IntEnumerated(VIEW_LAYOUT_VERTICAL, choices={VIEW_LAYOUT_VERTICAL})
        self.step_size = model.FloatContinuous(1e-6, range=(50e-9,50e-6), unit="m")
        self.align_mode = StringEnumerated(Z_ALIGN, choices=set((Z_ALIGN, SEM_ALIGN, FLM_ALIGN)))


class SparcAlignGUIData(ActuatorGUIData):
    def __init__(self, main):
        ActuatorGUIData.__init__(self, main)
        self.viewLayout = model.IntEnumerated(VIEW_LAYOUT_ONE, choices={VIEW_LAYOUT_ONE})

        # Same values than the modes of the OpticalPathManager
        amodes = ["chamber-view", "mirror-align", "fiber-align"]
        if main.spectrometer is None:
            amodes.remove("fiber-align")
            # Note: if no fiber alignment actuators, but a spectrometer, it's
            # still good to provide the mode, as the user can do it manually.

        if main.ccd is None:
            amodes.remove("chamber-view")
            amodes.remove("mirror-align")
            # Note: even if no lens-switch present, leave chamber-view as the user
            # might still switch the lens manually

        if not amodes:
            raise ValueError("Trying to build alignment tab for SPARC without spectrometer nor CCD")
        self.align_mode = StringEnumerated(amodes[0], choices=set(amodes))


class Sparc2AlignGUIData(ActuatorGUIData):
    def __init__(self, main):
        ActuatorGUIData.__init__(self, main)
        self.viewLayout = model.IntEnumerated(VIEW_LAYOUT_ONE, choices={VIEW_LAYOUT_ONE})

        # Mode values are different from the modes of the OpticalPathManager
        amodes = [
                  "lens-align", "mirror-align", "lens2-align", "center-align",
                  "ek-align", "streak-align", "fiber-align", "light-in-align",
                  "tunnel-lens-align",
                 ]

        # VA for autofocus procedure mode
        self.autofocus_active = BooleanVA(False)

        # If no direct spectrograph (eg, SPARC-compact), the lens 1 doesn't
        # need to be aligned. Same thing if no lens-mover (happens in some
        # hybrid/custom SPARC)
        if not main.spectrograph or not main.lens_mover:
            amodes.remove("lens-align")

        if not main.mirror:
            amodes.remove("mirror-align")
            if "lens-align" in amodes:
                amodes.remove("lens-align")

        # There is a special combination of components that indicates the potential presence of an FPLM module.
        # Eventhough it's not a watertight detection method, it is convenient to store its result
        # in a variable, so it can be reused later without the need of having to repeat the checks.
        self.fplm_module_present = (
            not main.spec_switch
            and main.mirror
            and main.light_aligner
            and main.mirror.name in main.light_aligner.affects.value
        )

        if self.fplm_module_present:
            amodes.remove("mirror-align")
            amodes.append("light-in-align-ar")

        if main.lens and model.hasVA(main.lens, "polePosition"):
            # Position of the hole from the center of the AR image (in m)
            # This is different from the polePosition of the lens, which is in
            # pixels from the top-left corner of the AR image.
            self.polePositionPhysical = model.TupleContinuous((0, 0),
                                           ((-1, -1), (1, 1)), unit="m",
                                           cls=(int, float),
                                           setter=self._setPolePosPhysical)

            main.lens.polePosition.subscribe(self._onPolePosCCD, init=True)

            if main.isAngularSpectrumSupported():
                self.mirrorPositionTopPhys = model.TupleContinuous((100e-6, 0),
                                           ((-1e18, -1e18), (1e18, 1e18)), unit="m",
                                           cls=(int, float),
                                           setter=self._setMirrorPosTopPhysical
                                           )
                self.mirrorPositionBottomPhys = model.TupleContinuous((-100e-6, 0),
                                           ((-1e18, -1e18), (1e18, 1e18)), unit="m",
                                           cls=(int, float),
                                           setter=self._setMirrorPosBottomPhysical
                                           )

                main.lens.mirrorPositionTop.subscribe(self._onMirrorPosTopCCD, init=True)
                main.lens.mirrorPositionBottom.subscribe(self._onMirrorPosBottomCCD, init=True)

                # Check that the lens-switch has the right metadata
                md = main.lens_switch.getMetadata()
                if not {model.MD_FAV_POS_ACTIVE, model.MD_FAV_POS_DEACTIVE}.issubset(md.keys()):
                    raise ValueError("lens-switch should have FAV_POS_ACTIVE and FAV_POS_DEACTIVE")
            else:
                amodes.remove("lens2-align")
                amodes.remove("ek-align")
        else:
            amodes.remove("center-align")
            amodes.remove("lens2-align")
            amodes.remove("ek-align")

        if main.fibaligner is None:
            amodes.remove("fiber-align")

        if main.streak_ccd is None:
            amodes.remove("streak-align")

        if main.light_aligner is None:
            amodes.remove("light-in-align")

        else:
            if main.spec_switch:
                # Check that the spec-selector has the right metadata
                md = main.spec_switch.getMetadata()
                if not {model.MD_FAV_POS_ACTIVE, model.MD_FAV_POS_DEACTIVE}.issubset(md.keys()):
                    raise ValueError("spec-switch should have FAV_POS_ACTIVE and FAV_POS_DEACTIVE")

        if main.spec_ded_aligner is None:
            amodes.remove("tunnel-lens-align")

        self.align_mode = StringEnumerated(amodes[0], choices=set(amodes))

    def _getImagePixelSizeNoBinning(self) -> Tuple[float, float]:
        """
        Finds out the pixel size of an image from the CCD if the binning was
        at 1x1.
        return: the pixel size (X,Y)
        """
        # The .pixelSize of the CCD contains the sensor pixel size.
        # The image pixel size depend on the lens magnification and binning.
        try:
            md = self.main.ccd.getMetadata()
            pxs = md[model.MD_PIXEL_SIZE]
        except KeyError:
            # Fallback to the sensor pixel size, which is what is used when no
            # lens magnification is known.
            pxs = self.main.ccd.pixelSize.value

        if model.hasVA(self.main.ccd, "binning"):
            b = self.main.ccd.binning.value
        else:
            b = (1, 1)

        return pxs[0] / b[0], pxs[1] / b[1]

    def _posToCCD(self, posphy, absolute: bool=True, clip: bool=True):
        """
        Convert position from physical coordinates to CCD coordinates (top-left
         pixel is 0, 0).
        Note: it will clip the coordinates to fit within the CCD
        posphy (float, float)
        absolute: if True, will adjust from origin being at the center to the origin
          being at the top-left. Otherwise, only the scale is adjusted.
        clip: if True, limit the value to within the CCD boundaries, and round to
          an int. Otherwise the value returned will be two floats.
        return (0<=int or float, 0<=int or float)
        """
        # Pole position is always expressed considering there is no binning
        pxs = self._getImagePixelSizeNoBinning()
        res = self.main.ccd.shape[0:2]

        # Convert into px referential (Y is inverted)
        posccd = (posphy[0] / pxs[0], -posphy[1] / pxs[1])

        if absolute:
            # Convert into the referential with the top-left corner as origin
            posccd = (posccd[0] + (res[0] - 1) / 2, posccd[1] + (res[1] - 1) / 2)

        if clip:
            if not 0 <= posccd[0] < res[0] or not 0 <= posccd[1] < res[1]:
                logging.warning("Pos %s out of the CCD", posccd)

            # Round to int, and clip to within CCD
            posccd = (max(0, min(int(round(posccd[0])), res[0] - 1)),
                      max(0, min(int(round(posccd[1])), res[1] - 1)))

        return posccd

    def _posToPhysical(self, posccd, absolute: bool=True):
        """
        Convert position from CCD coordinates to physical coordinates.
        Note: it conciders the physical origin to be at the center of the CCD.
        posccd (int, int)
        absolute: if True, will adjust from origin being at the center to the origin
          being at the top-left. Otherwise, only the scale is adjusted.
        return (float, float)
        """
        # position is always expressed considering there is no binning
        pxs = self._getImagePixelSizeNoBinning()
        res = self.main.ccd.shape[0:2]

        # Convert into the referential with the center as origin
        if absolute:
            posccd = (posccd[0] - (res[0] - 1) / 2, posccd[1] - (res[1] - 1) / 2)

        # Convert into world referential (Y is inverted)
        posc = (posccd[0] * pxs[0], -posccd[1] * pxs[1])
        return posc

    def _lineToCCD(self, linephy):
        a, b = linephy
        # Both values are in Y: as px = a + b * wl
        _, a_px = self._posToCCD((0, a), absolute=True, clip=False)  # To px from the top
        _, b_px = self._posToCCD((0, b), absolute=False, clip=False)  # To px/wl
        return a_px, b_px

    def _lineToPhysical(self, lineccd):
        a_px, b_px = lineccd
        # Both values are in Y: as px = a + b * wl
        _, a = self._posToPhysical((0, a_px), absolute=True)  # To m from the center
        _, b = self._posToPhysical((0, b_px), absolute=False)  # To px/wl
        return a, b

    def _setPolePosPhysical(self, posphy):
        posccd = self._posToCCD(posphy)

        logging.debug("Updated CCD polepos to %s px (= %s m)", posccd, posphy)

        self.main.lens.polePosition.unsubscribe(self._onPolePosCCD)
        self.main.lens.polePosition.value = posccd
        self.main.lens.polePosition.subscribe(self._onPolePosCCD)

        return self._posToPhysical(posccd)

    def _onPolePosCCD(self, posccd):
        posphy = self._posToPhysical(posccd)
        logging.debug("Updated world polepos to %s m (= %s px)", posphy, posccd)

        # Update without calling the setter
        self.polePositionPhysical._value = posphy
        self.polePositionPhysical.notify(posphy)

    def _setMirrorPosTopPhysical(self, linephy):
        lineccd = self._lineToCCD(linephy)
        logging.debug("Updated CCD mirror top pos to %s px (= %s m)", lineccd, linephy)

        self.main.lens.mirrorPositionTop.unsubscribe(self._onMirrorPosTopCCD)
        self.main.lens.mirrorPositionTop.value = lineccd
        self.main.lens.mirrorPositionTop.subscribe(self._onMirrorPosTopCCD)

        return linephy

    def _onMirrorPosTopCCD(self, lineccd):
        linephy = self._lineToPhysical(lineccd)
        logging.debug("Updated world mirror top pos to %s m (= %s px)", linephy, lineccd)

        # Update without calling the setter
        self.mirrorPositionTopPhys._value = linephy
        self.mirrorPositionTopPhys.notify(linephy)

    def _setMirrorPosBottomPhysical(self, linephy):
        lineccd = self._lineToCCD(linephy)
        logging.debug("Updated CCD mirror bottom pos to %s px (= %s m)", lineccd, linephy)

        self.main.lens.mirrorPositionBottom.unsubscribe(self._onMirrorPosBottomCCD)
        self.main.lens.mirrorPositionBottom.value = lineccd
        self.main.lens.mirrorPositionBottom.subscribe(self._onMirrorPosBottomCCD)

        return linephy

    def _onMirrorPosBottomCCD(self, lineccd):
        linephy = self._lineToPhysical(lineccd)
        logging.debug("Updated world mirror bottom pos to %s m (= %s px)", linephy, lineccd)

        # Update without calling the setter
        self.mirrorPositionBottomPhys._value = linephy
        self.mirrorPositionBottomPhys.notify(linephy)


class FastEMAcquisitionGUIData(MicroscopyGUIData):
    """
    GUI model for the FastEM acquisition tab. It contains the user-selected acquisition and
    calibration regions.
    """

    def __init__(self, main, panel):
        assert main.microscope is not None
        super(FastEMAcquisitionGUIData, self).__init__(main)


class FastEMSetupGUIData(MicroscopyGUIData):
    """
    GUI model for the FastEM overview tab.
    """

    def __init__(self, main):
        assert main.microscope is not None
        super(FastEMSetupGUIData, self).__init__(main)

        # Indicates the calibration state; True: is calibrated successfully; False: not yet calibrated
        self.is_optical_autofocus_done = model.BooleanVA(False)
        # Indicates the microscope state; True: is currently calibrating; False: not in calibration mode
        self.is_calibrating = model.BooleanVA(False)


class FastEMMainTabGUIData(MicroscopyGUIData):
    """
    GUI model for the FastEM main tab.
    """

    def __init__(self, main):
        assert main.microscope is not None
        super().__init__(main)

        # FastEM specific view layout
        self.viewLayout._choices = {VIEW_LAYOUT_DYNAMIC, VIEW_LAYOUT_ONE}
        self.viewLayout._value = VIEW_LAYOUT_DYNAMIC
        # Toggle between FastEMSetupTab and FastEMAcquisitionTab
        self.active_tab = model.VAEnumerated(None, choices={None: ""})
        # Toggle between FastEMProjectSettingsTab, FastEMProjectRibbonsTab, FastEMProjectSectionsTab, FastEMProjectROAsTab
        self.active_project_tab = model.VAEnumerated(None, choices={None: ""})
        # Shared VA which stores all EditableShape in any canvas
        self.shapes = model.ListVA([])
        # Shared VA which the shape to copy object of a canvas
        self.shape_to_copy = model.VigilantAttribute(None, readonly=True)
        # The project tree which connects the grids and FastEMProjectTreeCtrl, also it stores data necessary for import / export
        self.projects_tree = FastEMTreeNode("All Projects", NodeType.ALL_PROJECTS)
        # The current project in use
        self.current_project = model.StringVA("Project-1")
        # The project settings data, needed during acquisition
        self.project_settings_data = model.VigilantAttribute({})
