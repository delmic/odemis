# -*- coding: utf-8 -*-
"""
Created on 10 Mar 2022

@author: Philip Winkler, Sabrina Rossberger

Copyright © 2022, Philip Winkler, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

### Purpose ###

This module contains classes to control the graphical user interface actions such
as the region of acquisition (ROA), region of calibration (ROC) and projects in the
FASTEM system.
"""

import logging
from functools import partial

import wx

import odemis.acq.stream as acqstream
import odemis.gui.model as guimodel
from odemis.acq.fastem import CALIBRATION_2, CALIBRATION_3, FastEMROA
from odemis.gui import FG_COLOUR_RADIO_INACTIVE, FG_COLOUR_BUTTON
from odemis.gui.comp.fastem import FastEMProjectPanel, FastEMROAPanel, FastEMCalibrationPanel
from odemis.gui.conf.file import AcquisitionConfig
from odemis.gui.util import call_in_wx_main
import odemis.util.conversion as conversion
from odemis.util.filename import make_unique_name

# Blue, cyan, yellow, purple, magenta, red
FASTEM_PROJECT_COLOURS = ["#0000ff", "#00ffff", "#ffff00", "#ff00ff", "#ff00bf", "#ff0000"]


class FastEMProjectListController(object):
    """
    Creates/removes new FastEM projects.
    """

    def __init__(self, tab_data, project_list, viewport):
        """
        :param tab_data: (FastEMAcquisitionGUIData) The tab data model.
        :param project_list: (FastEMProjectList) The top-level panel containing all project panels.
        :param viewport: (FastEMMainViewport) The acquisition view.
        """
        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._project_list = project_list
        self._viewport = viewport
        # New shape overlay creation
        for overlay in self._viewport.canvas.shapes_overlay:
            overlay.new_shape.subscribe(self._on_new_shape)

        self.project_ctrls = {}  # dict FastEMProjectController --> int
        self._project_list.btn_add_project.Bind(wx.EVT_BUTTON, self._add_project)

        # Always show one project by default
        self._add_project(None, collapse=False)

        tab_data.main.is_acquiring.subscribe(self._on_is_acquiring)  # enable/disable project panel

    # already running in main GUI thread as it receives event from GUI
    def _add_project(self, _, name=None, collapse=True):
        # Get the smallest number that is not already in use. It's a bit challenging because projects can be
        # deleted, so we might have project 2 in colour red, but project 1 in blue has been deleted, so the
        # next project (which is now again the second project) should not use red again.
        num = next(idx for idx, num in enumerate(sorted(self.project_ctrls.values()) + [0], 1) if idx != num)
        if name is None:
            name = "Project-%s" % num
        name = make_unique_name(name, [project_ctrl.model.name.value for project_ctrl in self.project_ctrls.keys()])
        logging.debug("Creating new project %s.", name)
        colour = FASTEM_PROJECT_COLOURS[(num - 1) % len(FASTEM_PROJECT_COLOURS)]
        project_ctrl = FastEMProjectController(name, colour, self._tab_data_model, self._project_list, self._viewport)
        project_ctrl.panel._header.Bind(wx.EVT_LEFT_UP, self._on_project_panel_header)
        project_ctrl.panel.collapse(collapse)

        # Add the project model to tab_data
        self.project_ctrls[project_ctrl] = num
        self._tab_data_model.projects.value.append(project_ctrl.model)

        # Remove callback for every new remove button
        project_ctrl.panel.btn_remove.Bind(wx.EVT_BUTTON, lambda evt: self._remove_project(evt, project_ctrl))

        return project_ctrl

    def _on_project_panel_header(self, event):
        for project_ctrl in self.project_ctrls.keys():
            if event.GetEventObject() == project_ctrl.panel._header:
                project_ctrl.panel.collapse(False)
            else:
                project_ctrl.panel.collapse(True)

    # already running in main GUI thread as it receives event from GUI
    def _remove_project(self, _, project_ctrl):
        # TODO: open dialog "Are you sure?"
        logging.debug("Removing project %s." % project_ctrl.model.name.value)
        # Delete all ROIs of the project
        # .remove_roa_ctrl automatically removes itself from .roa_ctrls, so a for-loop doesn't work
        while project_ctrl.roa_ctrls:
            project_ctrl.remove_roa_ctrl(next(iter(project_ctrl.roa_ctrls.keys())))

        # Remove panel
        self._project_list.remove_project_panel(project_ctrl.panel)

        # Remove model
        self._tab_data_model.projects.value.remove(project_ctrl.model)

        # Destroy ROAController object
        del self.project_ctrls[project_ctrl]

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def _on_is_acquiring(self, mode):
        """
        Enable or disable the project list with all the ROAs depending on whether
        a calibration or acquisition is already ongoing or not.
        :param mode: (bool) Whether the system is currently acquiring or not acquiring.
        """
        self._project_list.Enable(not mode)

    def _on_new_shape(self, shape):
        """Callback on a new shape creation."""
        for project_ctrl in self.project_ctrls.keys():
            # Add ROA to the project whose panel is not collapsed
            if not project_ctrl.panel.collapsed:
                project_ctrl.add_roa(shape)
                return


class FastEMProjectController(object):
    """
    Controller for a FastEM project. This class is responsible for the creation and maintenance
    of ROAs belonging to the same project.
    During initialization, a panel is created and added to the project list.
    """

    def __init__(self, name, colour, tab_data, project_list, viewport):
        """
        :param name: (str) The default name for the project.
        :param colour: (str) Hexadecimal colour code for the bounding box of the roas in the viewport
        :param tab_data: (FastEMAcquisitionGUIData) The tab data model.
        :param project_list: (FastEMProjectList) The top-level panel containing all project panels.
        :param viewport: (FastEMMainViewport) The acquisition view.
        """
        self._tab_data = tab_data
        self._project_bar = project_list
        self._viewport = viewport
        self.regions_calib_2 = tab_data.calibrations[CALIBRATION_2].regions
        self.regions_calib_3 = tab_data.calibrations[CALIBRATION_3].regions

        self.roa_ctrls = {}  # dict FastEMROAController --> int
        self.colour = colour
        self.model = guimodel.FastEMProject(name)

        # Create the panel and add it to the project list. Subscribe to the controls.
        self.panel = FastEMProjectPanel(project_list, name=name)
        project_list.add_project_panel(self.panel)
        # Listen to both enter and kill focus event to make sure the text is really updated
        self.panel.txt_ctrl.Bind(wx.EVT_KILL_FOCUS, self._on_text)
        self.panel.txt_ctrl.Bind(wx.EVT_TEXT_ENTER, self._on_text)

        # For ROA creation process
        self._roa_coord_sub_callback = {}

    # already running in main GUI thread as it receives event from GUI
    def _on_text(self, evt):
        txt = self.panel.txt_ctrl.GetValue()
        current_name = self.model.name.value
        if txt == "":
            txt = current_name
            self.panel.txt_ctrl.SetValue(txt)
        if txt != current_name:
            txt = make_unique_name(txt, [project.name.value for project in self._tab_data.projects.value])
            logging.debug("Renaming project from %s to %s.", self.model.name.value, txt)
            self.model.name.value = txt
            self.panel.txt_ctrl.SetValue(txt)
        evt.Skip()

    def add_roa(self, shape, name=None):
        # Two-step process: Instantiate FastEM object here, but wait until first ROA is selected until
        # further processing. The process can still be aborted by clicking in the viewport without dragging.
        # In the callback to the ROI, the ROI creation will be completed or aborted.
        self._project_bar.enable_buttons(False)

        # Deselect all ROAs
        for roa_ctrl in self.roa_ctrls.keys():
            roa_ctrl.shape.selected.value = False

        # Minimum index that has not yet been deleted, find the first index which is not in the existing indices
        num = next(idx for idx, n in enumerate(sorted(self.roa_ctrls.values()) + [0], 1) if idx != n)
        if name is None:
            name = "ROA-%s" % num
        name = make_unique_name(name, [roa.name.value for roa in self.model.roas.value])
        # better guess for parameters after region is selected in _add_roa_ctrl
        roa_ctrl = FastEMROAController(name, None, None, self.colour, self._tab_data, self.panel, self._viewport, shape)
        self.roa_ctrls[roa_ctrl] = num
        sub_callback = partial(self._add_roa_ctrl, roa_ctrl=roa_ctrl)
        self._roa_coord_sub_callback[roa_ctrl] = sub_callback
        roa_ctrl.model.points.subscribe(sub_callback)

    # already running in main GUI thread as it receives event from GUI
    def _on_btn_remove(self, _, roa_ctrl):
        self.remove_roa_ctrl(roa_ctrl)

    def _add_roa_ctrl(self, points, roa_ctrl):
        roa_ctrl.model.points.unsubscribe(self._roa_coord_sub_callback[roa_ctrl])

        # Abort ROA creation if nothing was selected
        if len(points) == 0:
            logging.debug("Aborting ROA creation.")
            self._viewport.canvas.remove_shape(roa_ctrl.shape)
            del self.roa_ctrls[roa_ctrl]
            del self._roa_coord_sub_callback[roa_ctrl]
        else:
            # Create the panel
            roa_ctrl.create_panel()

            # Add the ROA model to project model
            self.model.roas.value.append(roa_ctrl.model)

            # Callback to ROA remove button
            roa_ctrl.panel.btn_remove.Bind(wx.EVT_BUTTON, lambda evt: self._on_btn_remove(evt, roa_ctrl))

        # Enable buttons of project bar
        self._project_bar.enable_buttons(True)

    def remove_roa_ctrl(self, roa_ctrl):
        """
        Note: Must be called from within the main GUI thread. Make sure caller is running in main GUI thread.
        """
        # Public function, so it can officially be called from the projectbar controller
        logging.debug("Removing ROA '%s' of project '%s'.", roa_ctrl.model.name.value, self.model.name.value)
        # Remove panel
        roa_ctrl.panel.Destroy()
        self.panel.fit_panels()

        # Remove shape
        self._viewport.canvas.remove_shape(roa_ctrl.shape)

        # Remove model
        self.model.roas.value.remove(roa_ctrl.model)

        # Destroy roa controller and its subscriber callback
        del self.roa_ctrls[roa_ctrl]
        del self._roa_coord_sub_callback[roa_ctrl]


class FastEMROAController(object):
    """
    Controller for a single region of acquisition (ROA).
    """

    def __init__(self, name, roc_2, roc_3, colour, tab_data, project_panel, viewport, shape):
        """
        :param name: (str) The default name for the ROA.
        :param roc_2: (FastEMROC): The region of calibration corresponding to the ROA.
        :param roc_3: (FastEMROC): The region of calibration corresponding to the ROA.
        :param colour: (str) Hexadecimal colour code for the bounding box of the roas in the viewport.
        :param tab_data: (FastEMAcquisitionGUIData) The tab data model.
        :param project_panel: (FastEMProjectPanel) The corresponding project panel.
        :param viewport: (FastEMMainViewport) The acquisition view.
        :param shape: (EditableShape) An editable shape.
        """
        self._tab_data = tab_data
        self._project_panel = project_panel
        self._viewport = viewport

        # Read the overlap from the acquisition configuration
        acqui_conf = AcquisitionConfig()

        self.model = FastEMROA(name, roc_2, roc_3,
                               self._tab_data.main.asm, self._tab_data.main.multibeam,
                               self._tab_data.main.descanner, self._tab_data.main.mppc,
                               acqui_conf.overlap)
        self.model.roc_2.subscribe(self._on_roc)
        self.model.roc_3.subscribe(self._on_roc)

        # The panel is not created on initialization to allow for cancellation of the ROA creation
        # (cf discussion in FastEMProjectController), create panel with .create_panel().
        self.panel = None

        self.shape = shape
        self.shape.colour = conversion.hex_to_frgba(colour)
        self.shape.points.subscribe(self._on_shape_points)
        self.shape.selected.subscribe(self._on_shape_selected)

    def create_panel(self):
        """
        Create a panel, add it to the project panel and subscribe to the controls. Should only be called once.

        Note: Should be called from the main GUI thread, but cannot add decorator here as otherwise creation
        of panel is delayed. Instead, make sure callers are running in main GUI thread!
        """
        logging.debug("Creating panel for ROA %s.", self.model.name.value)
        self.panel = FastEMROAPanel(self._project_panel, self.model.name.value,
                                    ["Calibration %s" % c for c in sorted(self._tab_data.main.scintillator_positions)])
        self._project_panel.add_roa_panel(self.panel)

        self.panel.calibration_ctrl.Bind(wx.EVT_COMBOBOX, self._on_combobox)
        self.panel.txt_ctrl.Bind(wx.EVT_KILL_FOCUS, self._on_text)
        self.panel.txt_ctrl.Bind(wx.EVT_TEXT_ENTER, self._on_text)

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def _on_shape_selected(self, selected):
        if self.panel:
            if selected:
                logging.debug("Selected ROA '%s'.", self.model.name.value)
                self.panel.activate()
            else:
                logging.debug("Deselected ROA '%s'.", self.model.name.value)
                self.panel.deactivate()

    def _on_shape_points(self, points):
        """Assign the shape points value to ROA model's points"""
        self.model.points.value = points
        self._find_closest_scintillator(self.shape.get_position())

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def _on_roc(self, roc):
        # Update calibration control
        logging.debug("ROA calibration changed to %s.", roc.name.value)
        if self.panel:
            self.panel.calibration_ctrl.SetSelection(int(roc.name.value) - 1)  # counting starts at 0

    # already running in main GUI thread as it receives event from GUI
    def _on_combobox(self, _):
        num = self.panel.calibration_ctrl.GetSelection() + 1
        logging.debug("ROA calibration selection changed to %s.", num)
        self.model.roc_2.value = self._tab_data.calibrations[CALIBRATION_2].regions.value[num]
        self.model.roc_3.value = self._tab_data.calibrations[CALIBRATION_3].regions.value[num]

    # already running in main GUI thread as it receives event from GUI
    def _on_text(self, evt):
        txt = self.panel.txt_ctrl.GetValue()
        current_name = self.model.name.value

        # Process input, make sure name is unique in project and complies with whitelisted characters of
        # technolution driver
        roa_project = [p for p in self._tab_data.projects.value if self.model in p.roas.value][0]
        all_project_roas = [roa.name.value for roa in roa_project.roas.value]
        if txt == "":
            txt = current_name
            self.panel.txt_ctrl.SetValue(txt)
        if txt != current_name:
            txt = make_unique_name(txt, all_project_roas)
            logging.debug("Renaming ROA from %s to %s.", self.model.name.value, txt)
            self.model.name.value = txt
            self.panel.txt_ctrl.SetValue(txt)

        evt.Skip()

    def _find_closest_scintillator(self, position):
        """
        Find the closest scintillator for the provided region of acquisition (ROA) position.
        :param position: (float, float) the position of the ROA.
        """
        roi_x, roi_y = position
        mindist = 1  # distances always lower 1
        closest = None
        for num, (sc_x, sc_y) in self._tab_data.main.scintillator_positions.items():
            # scintillators are rectangular, use maximum instead of euclidean distance
            dist = max(abs(roi_x - sc_x), abs(roi_y - sc_y))
            if dist < mindist:
                mindist = dist
                closest = num
        self.model.roc_2.value = self._tab_data.calibrations[CALIBRATION_2].regions.value[closest]
        self.model.roc_3.value = self._tab_data.calibrations[CALIBRATION_3].regions.value[closest]


class FastEMROCController(object):
    """
    Controller for a single region of calibration (ROC).
    """

    def __init__(self, main_data, viewport, calib_model):
        """
        :param number: (int) The number of the calibration region.
        :param tab_data: (FastEMAcquisitionGUIData) The tab data model.
        :param viewport (FastEMMainViewport) The acquisition view.
        param calib_prefix: (str) A prefix, which can indicate the order/type of the calibration (e.g. "calib_1").
        """
        self._viewport = viewport
        self._main_data_model = main_data
        scintillator_position = self._main_data_model.scintillator_positions[int(calib_model.name.value)]
        scintillator_size = self._main_data_model.scintillator_sizes[int(calib_model.name.value)]
        self._sample_bbox = (
            scintillator_position[0] - scintillator_size[0] / 2,
            scintillator_position[1] - scintillator_size[1] / 2,
            scintillator_position[0] + scintillator_size[0] / 2,
            scintillator_position[1] + scintillator_size[1] / 2,
        )  # (minx, miny, maxx, maxy) [m]

        # Get ROC model (exists already in tab data) and change coordinates
        self.calib_model = calib_model

        self.shape = None

    def fit_view_to_bbox(self):
        """
        Zoom in on calibration region (ROC). Calibration region has the size of a single field
        image, which is approximately 1/100 of the viewport area (= size of a single scintillator)
        and is centered in the scintillator with the corresponding number.
        """
        logging.debug("Zooming in on calibration region %s.", self.calib_model.name.value)
        cnvs = self._viewport.canvas
        xmin, ymin, xmax, ymax = self.calib_model.coordinates.value
        size = (self._main_data_model.multibeam.resolution.value[0] * self._main_data_model.multibeam.pixelSize.value[0],
                self._main_data_model.multibeam.resolution.value[1] * self._main_data_model.multibeam.pixelSize.value[1])
        # zoom in on ROC; add some space around ROC (factor 5 defines zoom level)
        # wx.callAfter: then don't need decorator to run it in main GUI thread
        wx.CallAfter(cnvs.fit_to_bbox, [xmin - 5 * size[0], ymin - 5 * size[1], xmax + 5 * size[0], ymax + 5 * size[1]])

    def create_calibration_shape(self):
        """
        Create the calibration region (ROC) shape.
        """
        if self.shape is None:
            self.shape = self._viewport.canvas.\
                add_calibration_shape(self.calib_model.coordinates,
                                      self.calib_model.name.value,
                                      self._sample_bbox,
                                      colour=self.calib_model.colour)

    def remove_calibration_shape(self):
        """
        Remove the calibration region (ROC) shape.
        """
        if self.shape:
            self._viewport.canvas.remove_shape(self.shape)
            self.shape = None


class FastEMCalibrationRegionsController(object):
    """
    Listens to the calibration buttons and creates the FastEMROCControllers accordingly.
    """

    def __init__(self, tab_data, viewport, calibration):
        """
        :param tab_data (FastEMAcquisitionGUIData): The tab data model.
        :param viewport (FastEMMainViewport): The acquisition view.
        :param calibration: (FastEMCalibration) The object containing FastEM calibration related attributes.
        """
        self._main_data_model = tab_data.main
        self.calibration = calibration

        self.panel = FastEMCalibrationPanel(self.calibration.panel, self._main_data_model.scintillator_layout)
        self.calibration.panel.add_calibration_panel(self.panel)

        # Bind toggle button and create calibration controller for each scintillator
        for btn in self.panel.buttons.keys():
            btn.Bind(wx.EVT_TOGGLEBUTTON, self._on_button)
            btn.Enable(False)  # disabled by default, need to select scintillator in chamber tab first

        # create calibration controller for each scintillator
        self.roc_ctrls = {}
        for roc_num, roc in self.calibration.regions.value.items():
            self.roc_ctrls[roc_num] = FastEMROCController(self._main_data_model, viewport, roc)

        # Only enable buttons for scintillators which have been selected in the chamber tab
        self._main_data_model.active_scintillators.subscribe(self._on_active_scintillators)


    # already running in main GUI thread as it receives event from GUI
    def _on_button(self, evt):
        """
        Called when one of the 9 single region of calibration (ROC) buttons is triggered.
        An ROC can be added or removed by clicking again. If a ROC is added, an overlay will be
        created in the center of the corresponding viewport. Size of the ROC is one single field.
        If a ROC is removed, the coordinates are reset to undefined.
        :param evt: (CommandEvent) Button triggered.
        """

        btn = evt.GetEventObject()
        num = self.panel.buttons.get(btn)
        roc_ctrl = self.roc_ctrls[num]

        if btn.GetValue():
            # update the coordinates
            # By default, the calibration region is in the center of the scintillator.
            # It is a single field image and thus its size is defined by the
            # multibeam resolution and pixel size.
            pos = self._main_data_model.scintillator_positions[num]
            sz = (self._main_data_model.multibeam.resolution.value[0] * self._main_data_model.multibeam.pixelSize.value[0],
                  self._main_data_model.multibeam.resolution.value[1] * self._main_data_model.multibeam.pixelSize.value[1])

            xmin = pos[0] - 0.5 * sz[0]
            ymin = pos[1] + 0.5 * sz[1]
            xmax = pos[0] + 0.5 * sz[0]
            ymax = pos[1] - 0.5 * sz[1]

            roc_ctrl.calib_model.coordinates.value = (xmin, ymin, xmax, ymax)
            roc_ctrl.fit_view_to_bbox()  # Zoom to calibration region
            roc_ctrl.create_calibration_shape()
        else:
            # reset coordinates for ROC to undefined and remove overlay
            roc_ctrl.calib_model.coordinates.value = acqstream.UNDEFINED_ROI
            roc_ctrl.calib_model.parameters.clear()
            roc_ctrl.remove_calibration_shape()
        # update ROC buttons
        self._update_buttons()

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def _update_buttons(self, _=None):
        """
        Checks that the region of calibration (ROC) buttons are up-to-date (synchronize model with GUI).
        Whenever the list of active scintillators changes and or a ROC is selected/deselected, the
        buttons are updated/enabled/disabled accordingly.
        :param active_scintillators: (list of int) A list of active (loaded) scintillators as indicated in the chamber tab.
        """
        active_scintillators = self._main_data_model.active_scintillators.value

        for b, num in self.panel.buttons.items():
            if num in active_scintillators:
                b.Enable(True)  # always enable the button when the scintillator is active
                roc_ctrl = self.roc_ctrls[num]
                if roc_ctrl.shape:
                    b.SetLabel("OK")
                    b.SetForegroundColour(roc_ctrl.calib_model.colour)
                else:
                    b.SetLabel("?")
                    b.SetForegroundColour(FG_COLOUR_RADIO_INACTIVE)
            else:  # scintillator unselected
                b.Enable(False)
                b.SetLabel("?")
                b.SetForegroundColour(FG_COLOUR_BUTTON)

    def _on_active_scintillators(self, scintillators):
        """
        Called when the list of active scintillators has changed. If a scintillator becomes inactive,
        the coordinates of the corresponding region of calibration (ROC) are reset. The calibration
        panel containing the ROC buttons are updated.
        :param scintillators: (list of int) A list of active (loaded) scintillators as indicated in the chamber tab.
        """
        for num in self.panel.buttons.values():
            if num not in scintillators:
                # reset coordinates for ROC to undefined and remove overlay
                roc_ctrl = self.roc_ctrls[num]
                roc_ctrl.calib_model.coordinates.value = acqstream.UNDEFINED_ROI
                roc_ctrl.remove_calibration_shape()

        # update ROC buttons
        self._update_buttons()
