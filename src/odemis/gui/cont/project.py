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

from __future__ import division

import logging

import wx

import odemis.acq.fastem
import odemis.acq.stream as acqstream
import odemis.gui.model as guimodel
from odemis.gui import conf
from odemis.gui.comp.stream import FastEMProjectPanel, FastEMROAPanel, FastEMCalibrationPanel
from odemis.gui.util import call_in_wx_main
from odemis.util.filename import make_unique_name

# Blue, green, cyan, yellow, purple, magenta, red
FASTEM_PROJECT_COLOURS = ["#0000ff", "#00ff00", "#00ffff", "#ffff00", "#ff00ff",
                          "#ff00bf", "#ff0000"]


class FastEMProjectBarController(object):
    """
    Creates/removes new the FastEM projects.
    """

    def __init__(self, tab_data, project_bar, view_ctrl):
        """
        project_bar (FastEMProjectBar): top-level panel containing all project panels
        tab_data (FastEMAcquisitionGUIData): tab data model
        view_ctrl (FastEMAcquisitionViewport): viewport controller
        """
        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._project_bar = project_bar
        self._view_ctrl = view_ctrl

        self.project_ctrls = {}  # dict int --> FastEMProjectController
        self._project_bar.btn_add_project.Bind(wx.EVT_BUTTON, self._add_project)

        # Always show one project by default
        self._add_project(None)

    def _add_project(self, _):
        # Get the smallest number that is not already in use. It's a bit challenging because projects can be
        # deleted, so we might have project 2 in colour red, but project 1 in blue has been deleted, so the
        # next project (which is now again the second project) should not use red again.
        num = next(idx for idx, num in enumerate(sorted(self.project_ctrls.keys()) + [0], 1) if idx != num)
        name = "Project-%s" % num
        logging.debug("Creating new project %s.", name)
        colour = FASTEM_PROJECT_COLOURS[(num - 1) % len(FASTEM_PROJECT_COLOURS)]
        project_ctrl = FastEMProjectController(name, colour, self._tab_data_model, self._project_bar, self._view_ctrl)

        # Add the project model to tab_data
        self.project_ctrls[num] = project_ctrl
        self._tab_data_model.projects.value.append(project_ctrl.model)

        # Remove callback for every new remove button
        project_ctrl.panel.btn_remove.Bind(wx.EVT_BUTTON, lambda evt: self._remove_project(evt, project_ctrl))

    def _remove_project(self, _, project_ctrl):
        # TODO: open dialog "Are you sure?"
        logging.debug("Removing project %s." % project_ctrl.model.name.value)
        # Delete all ROIs of the project
        # .remove_roa_ctrl automatically removes itself from .roa_ctrls, so a for-loop doesn't work
        while project_ctrl.roa_ctrls:
            project_ctrl.remove_roa_ctrl(next(iter(project_ctrl.roa_ctrls.values())))

        # Remove panel
        self._project_bar.remove_project_panel(project_ctrl.panel)

        # Remove controller from .project_ctrls list
        self.project_ctrls = {key: val for key, val in self.project_ctrls.items() if val != project_ctrl}

        # Remove model
        self._tab_data_model.projects.value.remove(project_ctrl.model)

        # Destroy ROAController object
        del project_ctrl


class FastEMProjectController(object):
    """
    Controller for a FastEM project. This class is responsible for the creation and maintenance
    of ROIs belonging to the project.
    During initialization, a panel is created and added to the project bar.
    """

    def __init__(self, name, colour, tab_data, project_bar, view_ctrl):
        """
        name (str): default name for the project
        colour (str): hexadecimal colour code for the bounding box of the roas in the viewport
        tab_data (FastEMAcquisitionGUIData): tab data model
        project_bar (FastEMProjectBar): top-level panel containing all project panels
        view_ctrl (FastEMAcquisitionViewport): viewport controller
        """
        self._tab_data = tab_data
        self._project_bar = project_bar
        self._view_ctrl = view_ctrl

        self.roa_ctrls = {}  # dict int --> FastEMROAController
        self.colour = colour
        self.model = guimodel.FastEMProject(name)

        # Create the panel and add it to the project bar. Subscribe to controls.
        self.panel = FastEMProjectPanel(project_bar, name=name)
        project_bar.add_project_panel(self.panel)
        self.panel.btn_add_roa.Bind(wx.EVT_BUTTON, self._on_btn_roa)
        # Listen to both enter and kill focus event to make sure the text is really updated
        self.panel.txt_ctrl.Bind(wx.EVT_KILL_FOCUS, self._on_text)
        self.panel.txt_ctrl.Bind(wx.EVT_TEXT_ENTER, self._on_text)

        # For ROA creation process
        self._current_roa_ctrl = None

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

    def _on_btn_roa(self, _):
        # Two-step process: Instantiate FastEM object here, but wait until first ROI is selected until
        # further processing. The process can still be aborted by clicking in the viewport without dragging.
        # In the callback to the ROI, the ROI creation will be completed or aborted.
        self._project_bar.enable_buttons(False)

        # Deactivate all ROAs
        for roa_ctrl in self.roa_ctrls.values():
            roa_ctrl.overlay.active.value = False

        # Minimum index that has not yet been deleted, find the first index which is not in the existing indices
        num = next(idx for idx, n in enumerate(sorted(self.roa_ctrls.keys()) + [0], 1) if idx != n)
        name = "ROA-%s" % num
        name = make_unique_name(name, [roa.name.value for roa in self.model.roas.value])
        # better guess for parameters after region is selected in _add_roa_ctrl
        roa_ctrl = FastEMROAController(name, None, self.colour, self._tab_data, self.panel, self._view_ctrl)
        self.roa_ctrls[num] = roa_ctrl
        self._current_roa_ctrl = roa_ctrl

        roa_ctrl.model.coordinates.subscribe(self._add_roa_ctrl)

    def _on_btn_remove(self, _, roa_ctrl):
        self.remove_roa_ctrl(roa_ctrl)

    def _add_roa_ctrl(self, coords):
        roa_ctrl = self._current_roa_ctrl
        self._current_roa_ctrl = None
        roa_ctrl.model.coordinates.unsubscribe(self._add_roa_ctrl)

        # Abort ROI creation if nothing was selected
        if coords == acqstream.UNDEFINED_ROI:
            logging.debug("Aborting ROA creation.")
            self._view_ctrl.viewports[0].canvas.remove_roa_overlay(roa_ctrl.overlay)
            self.roa_ctrls = {key: val for key, val in self.roa_ctrls.items() if val != roa_ctrl}
        else:
            # Create the panel
            roa_ctrl.create_panel()

            # Improve parameters guess
            num = self._find_closest_scintillator(coords)
            roa_ctrl.model.roc.value = self._tab_data.calibration_regions.value[num]

            # Add the ROA model to project model
            self.model.roas.value.append(roa_ctrl.model)

            # Callback to ROI remove button
            roa_ctrl.panel.btn_remove.Bind(wx.EVT_BUTTON, lambda evt: self._on_btn_remove(evt, roa_ctrl))

        # Enable buttons of project bar
        self._project_bar.enable_buttons(True)

    # Should be called from GUI main thread
    def remove_roa_ctrl(self, roa_ctrl):
        # Public function, so it can officially be called from the projectbar controller
        logging.debug("Removing ROA '%s' of project '%s'.", roa_ctrl.model.name.value, self.model.name.value)
        # Remove panel
        roa_ctrl.panel.Destroy()
        self.panel.fit_panels()

        # Remove controller from .roa_ctrl list
        self.roa_ctrls = {key: val for key, val in self.roa_ctrls.items() if val != roa_ctrl}

        # Remove overlay
        self._view_ctrl.viewports[0].canvas.remove_roa_overlay(roa_ctrl.overlay)

        # Remove model
        self.model.roas.value.remove(roa_ctrl.model)

        # Destroy ROAController object
        del roa_ctrl

    def _find_closest_scintillator(self, coordinates):
        """
        Given coordinates coords, find the closest scintillator.
        coordinates (float, float, float, float): l, t, r, b coordinates in m
        return (int): name (key) of closest scintillator in ._tab_data.scintillator_positions dict
        """
        roi_x, roi_y = (coordinates[2] + coordinates[0]) / 2, (coordinates[1] + coordinates[3]) / 2
        mindist = 1  # distances always lower 1
        closest = None
        for num, (sc_x, sc_y) in self._tab_data.main.scintillator_positions.items():
            # scintillators are rectangular, use maximum instead of euclidean distance
            dist = max(abs(roi_x - sc_x), abs(roi_y - sc_y))
            if dist < mindist:
                mindist = dist
                closest = num
        return closest


class FastEMROAController(object):
    """
    Controller for a single region of acquisition.
    """

    def __init__(self, name, roc, colour, tab_data, project_panel, view_ctrl):
        """
        name (str): default name for the ROA
        roc (FastEMROC): region of calibration
        colour (str): hexadecimal colour code for the bounding box of the roas in the viewport
        tab_data (FastEMAcquisitionGUIData): tab data model
        project_panel (FastEMProjectPanel): project panel
        view_ctrl (FastEMAcquisitionViewport): viewport controller
        """
        self._tab_data = tab_data
        self._project_panel = project_panel
        self._view_ctrl = view_ctrl
        # Read the overlap from the acquisition configuration
        acqui_conf = conf.get_acqui_conf()

        self.model = odemis.acq.fastem.FastEMROA(name, acqstream.UNDEFINED_ROI, roc,
                                                 self._tab_data.main.asm, self._tab_data.main.multibeam,
                                                 self._tab_data.main.descanner, self._tab_data.main.mppc,
                                                 acqui_conf.overlap)
        self.model.coordinates.subscribe(self._on_coordinates)
        self.model.roc.subscribe(self._on_roc)

        # The panel is not created on initialization to allow for cancellation of the ROA creation
        # (cf discussion in FastEMProjectController), create panel with .create_panel().
        self.panel = None

        logging.debug("Creating overlay for ROA '%s'.", name)
        canvas = self._view_ctrl.viewports[0].canvas
        self.overlay = canvas.add_roa_overlay(self.model.coordinates, colour)
        self.overlay.active.subscribe(self._on_overlay_active)

    def create_panel(self):
        """
        Create a panel, add it to the project panel and subscribe to controls. Should only be called once.
        """
        logging.debug("Creating panel for ROA %s.", self.model.name.value)
        self.panel = FastEMROAPanel(self._project_panel, self.model.name.value,
                                    ["Calibration %s" % c for c in sorted(self._tab_data.main.scintillator_positions)])
        self._project_panel.add_roa_panel(self.panel)

        self.panel.calibration_ctrl.Bind(wx.EVT_COMBOBOX, self._on_combobox)
        self.panel.txt_ctrl.Bind(wx.EVT_KILL_FOCUS, self._on_text)
        self.panel.txt_ctrl.Bind(wx.EVT_TEXT_ENTER, self._on_text)

    def _on_overlay_active(self, active):
        if self.panel:
            if active:
                logging.debug("Activating ROA '%s'.", self.model.name.value)
                self.panel.activate()
            else:
                logging.debug("Deactivating ROA '%s'.", self.model.name.value)
                self.panel.deactivate()

    def _on_coordinates(self, coords):
        # Purely for logging
        logging.debug("ROA '%s' coordinates changed to %s.", self.model.name.value, coords)

    def _on_roc(self, roc):
        # Update calibration control
        logging.debug("ROA calibration changed to %s.", roc.name.value)
        if self.panel:
            self.panel.calibration_ctrl.SetSelection(int(roc.name.value) - 1)  # counting starts at 0

    def _on_combobox(self, _):
        num = self.panel.calibration_ctrl.GetSelection() + 1
        self.model.roc.value = self._tab_data.calibration_regions.value[num]
        logging.debug("ROA calibration changed to %s.", self.model.roc.value.name.value)

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


class FastEMROCController(object):
    """
    Controller for a single region of calibration.
    """

    def __init__(self, number, tab_data, view_ctrl):
        """
        number (int): number of the calibration region
        tab_data (FastEMAcquisitionGUIData): tab data model
        view_ctrl (FastEMAcquisitionViewport): viewport controller
        """
        self._view_ctrl = view_ctrl
        self._tab_data = tab_data

        # By default, the calibration region is in the center of the scintillator. The size is given by
        # the ebeam resolution.
        pos = tab_data.main.scintillator_positions[number]
        sz = (tab_data.main.ebeam.resolution.value[0] * tab_data.main.ebeam.pixelSize.value[0],
              tab_data.main.ebeam.resolution.value[1] * tab_data.main.ebeam.pixelSize.value[1])

        l = pos[0] - 0.5 * sz[0]
        t = pos[1] + 0.5 * sz[1]
        r = pos[0] + 0.5 * sz[0]
        b = pos[1] - 0.5 * sz[1]

        # Get ROC model (exists already in tab data) and change coordinates
        self.model = tab_data.calibration_regions.value[number]
        self.model.coordinates.value = (l, t, r, b)
        self.model.coordinates.subscribe(self._on_coordinates)

        # Add overlay
        self.overlay = view_ctrl.viewports[0].canvas.add_calibration_overlay(self.model.coordinates, number)

    def fit_view_to_bbox(self):
        """
        Zoom in to calibration region. Calibration region is in the center and takes up 1/100 of viewport area.
        """
        logging.debug("Zooming in to calibration region %s.", self.model.name.value)
        cnvs = self._view_ctrl.viewports[0].canvas
        l, t, r, b = self.model.coordinates.value
        sz = (self._tab_data.main.ebeam.resolution.value[0] * self._tab_data.main.ebeam.pixelSize.value[0],
              self._tab_data.main.ebeam.resolution.value[1] * self._tab_data.main.ebeam.pixelSize.value[1])
        cnvs.fit_to_bbox([l - 5 * sz[0], t - 5 * sz[1], r + 5 * sz[0], b + 5 * sz[1]])

    def _on_coordinates(self, coords):
        # Purely for logging
        logging.debug("ROC '%s' coordinates changed to %s.", self.model.name.value, coords)


class FastEMCalibrationRegionsController(object):
    """
    Listens to the calibration buttons and creates the FastEMROCControllers accordingly.
    """

    def __init__(self, tab_data, calibration_bar, view_ctrl):
        """
        tab_data (FastEMAcquisitionGUIData): tab data model
        calibration_bar (FastEMCalibrationBar): main calibration panel
        view_ctrl (FastEMAcquisitionViewport): viewport controller
        """
        self._view_ctrl = view_ctrl
        self._calibration_bar = calibration_bar
        self._tab_data = tab_data

        self.panel = FastEMCalibrationPanel(calibration_bar, tab_data.main.scintillator_layout)
        calibration_bar.add_calibration_panel(self.panel)

        self.roc_ctrls = {}  # int --> FastEMROCController

        for btn in self.panel.buttons.values():
            btn.Bind(wx.EVT_BUTTON, self._on_button)
            btn.Enable(False)  # disabled by default, need to select scintillator in chamber tab first

        # Only enable buttons for scintillators which have been selected in the chamber tab
        tab_data.main.active_scintillators.subscribe(self._on_active_scintillators)

    def _on_button(self, evt):
        btn = evt.GetEventObject()
        btn.SetLabel("OK")
        btn.SetForegroundColour(wx.GREEN)

        num = [num for num, b in self.panel.buttons.items() if b == btn][0]
        if num not in self.roc_ctrls:
            # Create new calibration controller
            logging.debug("Adding ROC controller %s.", num)
            roc_ctrl = FastEMROCController(num, self._tab_data, self._view_ctrl)
            self.roc_ctrls[num] = roc_ctrl
        else:
            # Zoom to existing calibration region
            roc_ctrl = self.roc_ctrls[num]
        roc_ctrl.fit_view_to_bbox()

    @call_in_wx_main
    def _on_active_scintillators(self, scintillators):
        for num, b in self.panel.buttons.items():
            if num in scintillators:
                b.Enable(True)
            else:
                b.Enable(False)
