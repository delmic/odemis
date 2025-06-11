# -*- coding: utf-8 -*-

"""
@author: Rinze de Laat, Éric Piel, Philip Winkler, Victoria Mavrikopoulou,
         Anders Muskens, Bassim Lazem, Patrick Cleeve

Copyright © 2012-2022 Rinze de Laat, Éric Piel, Delmic

Handles the switch of the content of the main GUI tabs.

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

import collections
import itertools
import logging
import math
import os.path
from concurrent.futures import CancelledError

import wx

import odemis.gui.cont.views as viewcont
import odemis.gui.model as guimod
from odemis import model
from odemis.acq.feature import load_project_data
from odemis.acq.move import (
    ALIGNMENT,
    COATING,
    FM_IMAGING,
    GRID_1,
    GRID_2,
    LOADING,
    LOADING_PATH,
    MILLING,
    FIB_IMAGING,
    POSITION_NAMES,
    SEM_IMAGING,
    THREE_BEAMS,
    UNKNOWN,
)
from odemis.acq.stream import StaticStream
from odemis.gui import conf
from odemis.gui.comp.buttons import (
    BTN_TOGGLE_COMPLETE,
    BTN_TOGGLE_OFF,
    BTN_TOGGLE_PROGRESS,
)
from odemis.gui.comp.edit_position_metadata import EditMeteorCalibrationDialog
from odemis.gui.cont.tabs.fibsem_tab import FibsemTab
from odemis.gui.cont.tabs.correlation_tab import CorrelationTab
from odemis.gui.cont.tabs.localization_tab import LocalizationTab
from odemis.gui.cont.tabs.tab import Tab
from odemis.gui.util import call_in_wx_main
from odemis.gui.util.widgets import AxisConnector, VigilantAttributeConnector
from odemis.gui.util.wx_adapter import fix_static_text_clipping
from odemis.gui.win.acquisition import (
    LoadProjectFileDialog,
    SelectFileDialog,
    ShowChamberFileDialog,
)
from odemis.model import InstantaneousFuture
from odemis.util import almost_equal
from odemis.util.filename import create_projectname, guess_pattern
from odemis.util.units import readable_str
try:
    from odemis.acq.align.tdct import parse_3dct_yaml_file
except ImportError as e:
    logging.warning(f"Unable to import 3DCT module: {e}")

class CryoChamberTab(Tab):
    def __init__(self, name, button, panel, main_frame, main_data):
        """ Chamber view tab for ENZEL, METEOR, and MIMAS"""

        tab_data = guimod.CryoChamberGUIData(main_data)
        super(CryoChamberTab, self).__init__(name, button, panel, main_frame, tab_data)
        self.set_label("CHAMBER")

        # Controls the stage movement based on the imaging mode
        self.posture_manager = main_data.posture_manager

        # future to handle the move
        self._move_future = InstantaneousFuture()
        # create the tiled area view and its controller to show on the chamber tab
        vpv = collections.OrderedDict([
            (panel.vp_overview_map,
             {
                 "cls": guimod.FeatureOverviewView,
                 "stage": main_data.stage,
                 "name": "Overview",
                 "stream_classes": StaticStream,
             }), ])

        self._view_controller = viewcont.ViewPortController(tab_data, panel, vpv)
        self._tab_panel = panel
        self._role = main_data.role
        self._aligner = main_data.aligner

        # For project selection
        self.conf = conf.get_acqui_conf()
        self.btn_change_folder = self.panel.btn_change_folder
        self.btn_load_project = self.panel.btn_load_project
        self.btn_change_folder.Bind(wx.EVT_BUTTON, self._on_change_project_folder)
        self.btn_load_project.Bind(wx.EVT_BUTTON, self._load_project_data)
        self.txt_projectpath = self.panel.txt_projectpath
        self.btn_load_project.Hide()

        # flag to indicate whether the user has successfully created or
        # loaded a project when first opening the gui. other actions are blocked,
        # via a dialog, until a project is created or loaded.
        self._is_initial_project_ready: bool = False

        self._move_cancelled = False

        # enable import from 3dct for correlation
        main_frame.Bind(wx.EVT_MENU, self._import_features_from_3dct, id=main_frame.menu_item_import_from_3dct.GetId())
        if self._role == 'meteor':
            main_frame.menu_item_import_from_3dct.Enable(True)

        # enable meteor calibration
        main_frame.Bind(wx.EVT_MENU, self._edit_meteor_calibration, id=main_frame.menu_item_edit_meteor_calibration.GetId())
        if self._role == 'meteor':
            main_frame.menu_item_edit_meteor_calibration.Enable(True)

        self._current_posture = UNKNOWN  # position of the sample (regularly updated)
        self._target_posture = None  # when moving, move POSITION to be reached, otherwise None

        if self._role == 'enzel':
            # get the stage and its meta data
            self._stage = self.tab_data_model.main.stage
            stage_metadata = self._stage.getMetadata()

            # TODO: this is not anymore the milling angle (rx - ION_BEAM_TO_SAMPLE_ANGLE),
            # but directly the rx value, so the name of the control should be updated
            # Define axis connector to link milling angle to UI float ctrl
            self.milling_connector = AxisConnector('rx', self._stage, panel.ctrl_rx, pos_2_ctrl=self._milling_angle_changed,
                                                ctrl_2_pos=self._milling_ctrl_changed, events=wx.EVT_COMMAND_ENTER)
            # Set the milling angle range according to rx axis range
            try:
                rx_range = self._stage.axes['rx'].range
                panel.ctrl_rx.SetValueRange(*(math.degrees(r) for r in rx_range))
                # Default value for milling angle, will be used to store the angle value out of milling position
                rx_value = self._stage.position.value['rx']
                self.panel.ctrl_rx.Value = readable_str(math.degrees(rx_value), unit="°", sig=3)
            except KeyError:
                raise ValueError('The stage is missing an rx axis.')
            panel.ctrl_rx.Bind(wx.EVT_CHAR, panel.ctrl_rx.on_char)

            self.position_btns = {LOADING: self.panel.btn_switch_loading, THREE_BEAMS: self.panel.btn_switch_imaging,
                                  ALIGNMENT: self.panel.btn_switch_align, COATING: self.panel.btn_switch_coating,
                                  SEM_IMAGING: self.panel.btn_switch_zero_tilt_imaging}
            self._grid_btns = {}
            self.btn_aligner_axes = {self.panel.stage_align_btn_p_aligner_x: ("x", 1),
                                self.panel.stage_align_btn_m_aligner_x: ("x", -1),
                                self.panel.stage_align_btn_p_aligner_y: ("y", 1),
                                self.panel.stage_align_btn_m_aligner_y: ("y", -1),
                                self.panel.stage_align_btn_p_aligner_z: ("z", 1),
                                self.panel.stage_align_btn_m_aligner_z: ("z", -1)}

            panel.btn_switch_advanced.Show()
            panel.pnl_advanced_align.Show()

            # Vigilant attribute connectors for the align slider and show advanced button
            self._slider_aligner_va_connector = VigilantAttributeConnector(tab_data.stage_align_slider_va,
                                                                        self.panel.stage_align_slider_aligner,
                                                                        events=wx.EVT_SCROLL_CHANGED)
            self._show_advanced_va_connector = VigilantAttributeConnector(tab_data.show_advaned,
                                                                        self.panel.btn_switch_advanced,
                                                                        events=wx.EVT_BUTTON,
                                                                        ctrl_2_va=self._btn_show_advanced_toggled,
                                                                        va_2_ctrl=self._on_show_advanced)

            # Event binding for move control
            panel.stage_align_btn_p_aligner_x.Bind(wx.EVT_BUTTON, self._on_aligner_btn)
            panel.stage_align_btn_m_aligner_x.Bind(wx.EVT_BUTTON, self._on_aligner_btn)
            panel.stage_align_btn_p_aligner_y.Bind(wx.EVT_BUTTON, self._on_aligner_btn)
            panel.stage_align_btn_m_aligner_y.Bind(wx.EVT_BUTTON, self._on_aligner_btn)
            panel.stage_align_btn_p_aligner_z.Bind(wx.EVT_BUTTON, self._on_aligner_btn)
            panel.stage_align_btn_m_aligner_z.Bind(wx.EVT_BUTTON, self._on_aligner_btn)

        elif self._role == 'meteor':
            # We use stage-bare, which is in the referential of the SEM chamber.
            # (While "stage" is in the FLM referential)
            self._stage = self.tab_data_model.main.stage_bare

            # Fail early when required axes are not found on the focuser positions metadata
            focuser = self.tab_data_model.main.focus
            focus_md = focuser.getMetadata()
            required_axis = {'z'}
            for fmd_key, fmd_value in focus_md.items():
                if fmd_key in [model.MD_FAV_POS_DEACTIVE, model.MD_FAV_POS_ACTIVE] and not required_axis.issubset(fmd_value.keys()):
                    raise ValueError(f"Focuser {fmd_key} metadata ({fmd_value}) does not have the required axes {required_axis}.")

            # All the meteor buttons
            self.position_btns = {
                SEM_IMAGING: self.panel.btn_switch_sem_imaging,
                FM_IMAGING: self.panel.btn_switch_fm_imaging,
                MILLING: self.panel.btn_switch_milling,
                FIB_IMAGING: self.panel.btn_switch_fib_imaging,
           }
            # Remove the ones which are not supported on this system
            self.position_btns = {posture: btn for posture, btn in self.position_btns.items()
                                  if posture in main_data.posture_manager.postures}

            # Grid buttons (for switching between grids)
            # For now, hard-coded to 2 grids. Could be extended to be more flexible, once some METEOR
            # support a different number of grids (see MIMAS or FASTEM).
            self._grid_btns = {
                  GRID_1: self.panel.btn_switch_grid1,
                  GRID_2: self.panel.btn_switch_grid2,
            }
            # show load project button
            self.btn_load_project.Show()

        elif self._role == 'mimas':
            self._stage = self.tab_data_model.main.stage

            self.position_btns = {
                LOADING: self.panel.btn_switch_loading_chamber_tab,
                COATING: self.panel.btn_switch_coating_chamber_tab,
                FM_IMAGING: self.panel.btn_switch_optical_chamber_tab,
                MILLING: self.panel.btn_switch_milling_chamber_tab
            }

            self._grid_btns = {}

            # TODO: add Tilt angle control, like on the ENZEL, but outside the advanced panel

        # start and end position are used for the gauge progress bar
        self._start_pos = self._stage.position.value
        self._end_pos = self._start_pos

        # Event binding for position control
        for btn in itertools.chain(self.position_btns.values(), self._grid_btns.values()):
            btn.Show()
            btn.Bind(wx.EVT_BUTTON, self._on_switch_btn)

        panel.btn_cancel.Bind(wx.EVT_BUTTON, self._on_cancel)

        # Show current position of the stage via the progress bar
        self._stage.position.subscribe(self._update_progress_bar, init=False)
        self.posture_manager.current_posture.subscribe(self._on_posture, init=True)
        self._show_warning_msg(None)

        # Show temperature control, if available
        if main_data.sample_thermostat:
            panel.pnl_temperature.Show()

            # Connect the heating VA to a checkbox. It's a little tricky, because
            # it's actually a VAEnumerated, but we only want to assign two values
            # off/on.
            self._vac_sample_target_tmp = VigilantAttributeConnector(
                main_data.sample_thermostat.heating, self.panel.ctrl_sample_heater,
                va_2_ctrl=self._on_sample_heater,
                ctrl_2_va=self._sample_heater_to_va,
                events=wx.EVT_CHECKBOX
                )

            # Connect the .targetTemperature
            self._vac_sample_target_tmp = VigilantAttributeConnector(main_data.sample_thermostat.targetTemperature,
                                                                     self.panel.ctrl_sample_target_tmp,
                                                                     events=wx.EVT_COMMAND_ENTER)

    def _get_overview_view(self):
        overview_view = next(
            (view for view in self.tab_data_model.views.value if isinstance(view, guimod.FeatureOverviewView)), None)
        if not overview_view:
            logging.warning("Could not find view of type FeatureOverviewView.")

        return overview_view

    def remove_overview_streams(self, streams):
        """
        Remove the overview static stream from the view with the given list of acquired static streams
        :param streams: (list of StaticStream) the newly acquired static streams from the localization tab
        """
        try:
            overview_view = self._get_overview_view()
            for stream in streams:
                overview_view.removeStream(stream)
        except AttributeError:  # No overview view
            pass

    def load_overview_streams(self, streams):
        """
        Load the overview view with the given list of acquired static streams
        :param streams: (list of StaticStream) the newly acquired static streams from the localization tab
        """
        try:
            overview_view = self._get_overview_view()
            for stream in streams:
                overview_view.addStream(stream)

            # Make sure the whole content is shown
            self.panel.vp_overview_map.canvas.fit_view_to_content()
        except AttributeError:  # No overview view
            pass

    def _on_change_project_folder(self, evt) -> bool:
        """
        Shows a dialog to change the path and name of the project directory.
        Updates .conf and project path text control when a new project is created.
        :return: True if the project was successfully created, False otherwise
        """
        # do not warn if there is no data (eg, at init)
        if self._is_initial_project_ready:
            box = wx.MessageDialog(self.main_frame,
                                "This will clear the current project data from Odemis",
                                caption="Reset Project", style=wx.YES_NO | wx.ICON_QUESTION | wx.CENTER)

            box.SetYesNoLabels("&Reset Project", "&Cancel")
            ans = box.ShowModal()  # Waits for the window to be closed
            if ans == wx.ID_NO:
                return False

        prev_dir = self.conf.pj_last_path

        # Generate suggestion for the new project name to show it on the file dialog
        root_dir = os.path.dirname(self.conf.pj_last_path)
        np = create_projectname(root_dir, self.conf.pj_ptn, count=self.conf.pj_count)
        new_dir = ShowChamberFileDialog(self.panel, np)
        if new_dir is None: # Cancelled
            return False

        logging.debug("Selected project folder %s", new_dir)

        # Three possibilities:
        # * The folder doesn't exists yet => create it
        # * The folder already exists and is empty => nothing else to do
        # * The folder already exists and has files in it => Error (because we might override files)
        # TODO: in the last case, ask the user if we should re-open this project,
        # to add new acquisitions to it.
        if not os.path.isdir(new_dir):
            os.mkdir(new_dir)
        elif os.listdir(new_dir):
            dlg = wx.MessageDialog(self.main_frame,
                                   "Selected directory {} already contains files.".format(new_dir),
                                   style=wx.OK | wx.ICON_WARNING)
            dlg.ShowModal()
            dlg.Destroy()
            return False

        # only reset and delete data if not initial setup
        if self._is_initial_project_ready:
            # Reset project, clear the data
            self._reset_project_data()

            # If the previous project is empty, it means the user never used it.
            # (for instance, this happens just after starting the GUI and the user
            # doesn't like the automatically chosen name)
            # => Just automatically delete previous folder if empty.
            try:
                if os.path.isdir(prev_dir) and not os.listdir(prev_dir):
                    logging.debug("Deleting empty project folder %s", prev_dir)
                    os.rmdir(prev_dir)
            except Exception:
                # It might be just due to some access rights, let's not worry too much
                logging.exception("Failed to delete previous project folder %s", prev_dir)

            # Handle weird cases where the previous directory would point to the same
            # folder as the new folder, either with completely the same path, or with
            # different paths (eg, due to symbolic links).
            if not os.path.isdir(new_dir):
                logging.warning("Recreating folder %s which was gone", new_dir)
                os.mkdir(new_dir)

        self._change_project_conf(new_dir)

        return True

    def _change_project_conf(self, new_dir):
        """
        Update new project info in config file and show it on the text control
        """
        self.conf.pj_last_path = new_dir
        self.conf.pj_ptn, self.conf.pj_count = guess_pattern(new_dir)
        self.txt_projectpath.Value = os.path.basename(self.conf.pj_last_path)
        self.tab_data_model.main.project_path.value = new_dir
        logging.debug("Generated project folder name pattern '%s'", self.conf.pj_ptn)

    def _create_new_dir(self):
        """
        Create a new project directory from config pattern and update project config with new name
        """
        root_dir = os.path.dirname(self.conf.pj_last_path)
        np = create_projectname(root_dir, self.conf.pj_ptn, count=self.conf.pj_count)
        try:
            os.mkdir(np)
        except OSError:
            # If for some reason it's not possible to create the new folder, for
            # example because it's on a remote folder which is not connected anymore,
            # don't completely fail, but just try something safe.
            logging.exception("Failed to create expected project folder %s, will fallback to default folder", np)
            pj_last_path = self.conf.default.get("project", "pj_last_path")
            root_dir = os.path.dirname(pj_last_path)
            pj_ptn = self.conf.default.get("project", "pj_ptn")
            pj_count = self.conf.default.get("project", "pj_count")
            np = create_projectname(root_dir, pj_ptn, count=pj_count)
            os.mkdir(np)

        self._change_project_conf(np)

    def _create_project(self) -> bool:
        """
        Create a project based on the user selected directory and name
        :return: True if the project was successfully created, False otherwise
        """
        return self._on_change_project_folder(None)

    def _reset_project_data(self):
        try:
            streams = self._get_overview_view().getStreams()
            self.remove_overview_streams(streams)
            localization_tab: LocalizationTab = self.tab_data_model.main.getTabByName("cryosecom-localization")
            localization_tab.clear_acquired_streams()
            localization_tab.reset_live_streams()
            fibsem_tab: FibsemTab = self.tab_data_model.main.getTabByName("meteor-fibsem")
            if fibsem_tab:
                fibsem_tab.clear_acquired_streams()
            self.tab_data_model.main.features.value = []
            self.tab_data_model.main.currentFeature.value = None

            correlation_tab: CorrelationTab = self.tab_data_model.main.getTabByName("meteor-correlation")
            correlation_tab.clear_streams()
        except LookupError:
            logging.warning("Unable to find localization tab.")

    def _load_project_data(self, evt: wx.Event) -> bool:
        """ Load a project based on the user selected directory.
        :return: True if the project was successfully loaded, False otherwise
        """

        # do not warn if there is no data (eg, at init)
        if self._is_initial_project_ready:
            box = wx.MessageDialog(self.main_frame,
                                "This will clear the current project data from Odemis",
                                caption="Reset Project", style=wx.YES_NO | wx.ICON_QUESTION | wx.CENTER)

            box.SetYesNoLabels("&Reset Project", "&Cancel")
            ans = box.ShowModal()  # Waits for the window to be closed
            if ans == wx.ID_NO:
                return False

        # select a project directory to load
        proj_path = LoadProjectFileDialog(self.panel, self.conf.pj_last_path)
        if proj_path is None: # Cancelled
            return False

        logging.debug("Selected project folder %s", proj_path)

        # Reset project, clear the data
        if self._is_initial_project_ready:
            self._reset_project_data()

        # dont delete empty directories for now
        # If the previous project is empty, it means the user never used it.
        # (for instance, this happens just after starting the GUI and the user
        # doesn't like the automatically chosen name)
        # => Just automatically delete previous folder if empty.
        # try:
        #     if os.path.isdir(prev_dir) and not os.listdir(prev_dir):
        #         logging.debug("Deleting empty project folder %s", prev_dir)
        #         os.rmdir(prev_dir)
        # except Exception:
        #     # It might be just due to some access rights, let's not worry too much
        #     logging.exception("Failed to delete previous project folder %s", prev_dir)

        # change the project path in the config file
        self._change_project_conf(proj_path)

        # load project data
        proj_data = load_project_data(proj_path)

        # no data found in the project directory, not necessarily an error as the
        # user may create a project, but not have any data yet. so we just inform the user
        if len(proj_data["features"]) == 0 and len(proj_data["overviews"]) == 0:
            logging.warning("No data found in the project directory.")
            box = wx.MessageDialog(self.main_frame,
                               f"No data found in the selected directory: {proj_path}.",
                               caption="No Project Data", style=wx.OK | wx.CENTER)

            box.SetOKLabel("&OK")
            ans = box.ShowModal()  # Waits for the window to be closed
            return True

        # stop the stream subscribers to prevent circular updates and timing issues
        localization_tab: LocalizationTab = self.tab_data_model.main.getTabByName("cryosecom-localization")
        correlation_tab: CorrelationTab = self.tab_data_model.main.getTabByName("meteor-correlation")
        fibesem_tab: FibsemTab = self.tab_data_model.main.getTabByName("meteor-fibsem")
        # correlation_tab.correlation_controller._stop_streams_subscriber()
        localization_tab._stop_streams_subscriber()
        if fibesem_tab:
            fibesem_tab._stop_streams_subscriber()

        # follow the order of the data loading in the localization tab
        # load overview streams
        localization_tab.load_overview_data(data=proj_data["overviews"])

        # load sem overview streams
        try:
            fibsem_tab: Tab = self.tab_data_model.main.getTabByName("meteor-fibsem")
            fibsem_tab.load_overview_data(data=proj_data["overviews"]) # filters to only semstatic streams
        except Exception:
            logging.warning("Unable to find FIBSEM tab. Likely disabled by licence.")

        # load features
        self.tab_data_model.main.features.value = proj_data["features"]

        # load acquired streams
        f_streams = []
        for f in self.tab_data_model.main.features.value:
            f_streams.extend(f.streams.value)
        localization_tab.tab_data_model.streams.value.extend(f_streams)

        # log project data
        logging.debug(f"Loaded project data from {proj_path}")
        logging.debug(f"{len(self.tab_data_model.main.features.value)} features loaded.")
        logging.debug(f"{len(proj_data['overviews'])} overviews loaded.")
        logging.debug(f"{len(localization_tab.tab_data_model.streams.value)} streams loaded.")

        # re-subscribe to stream updates
        # NOTE: this must be called with wx.CallAfter force the call to happen after existing stream addition events
        wx.CallAfter(localization_tab._start_streams_subscriber)
        # wx.CallAfter(correlation_tab.correlation_controller._start_streams_subscriber)
        if fibsem_tab:
            wx.CallAfter(fibsem_tab._start_streams_subscriber)

        return True

    def _import_features_from_3dct(self, _):

        # load 3dct position
        path = SelectFileDialog(parent=self.panel,
                                message="Select 3DCT Position File to load",
                                default_path=self.conf.pj_last_path)

        if path is None: # Cancelled
            logging.warning("No 3DCT position file selected, exiting.")
            return

        # load yaml file
        try:
            pt = parse_3dct_yaml_file(path)
        except Exception as e:
            logging.error(f"Failed to load 3DCT position file: {e}")
            return

        # redraw milling position
        fibsem_tab = self.tab_data_model.main.getTabByName("meteor-fibsem")
        fibsem_tab.milling_task_controller.draw_milling_tasks(pos=pt, convert_pos=False)

    def _edit_meteor_calibration(self, evt):
        """Open a dialog for editing meteor stage calibration metadata"""

        md_calib = self._stage.getMetadata()[model.MD_CALIB]
        dialog = EditMeteorCalibrationDialog(self.main_frame, md_calib)
        dialog.Center()

        ret = dialog.ShowModal()
        if ret == wx.ID_OK:
            self._stage.updateMetadata({model.MD_CALIB: dialog.md_calib})
            logging.info(f"Updated calibration stage metadata: {self._stage.getMetadata()[model.MD_CALIB]}")

        dialog.Destroy()

    @call_in_wx_main
    def _update_progress_bar(self, pos):
        """
        Update the progress bar, based on the current position of the stage.
        Called when the position of the stage changes.
        pos (dict str->float): current position of the sample stage
        """
        if not self.IsShown():
            return
        # start and end position should be set for the progress bar to update
        # otherwise, the movement is not coming from the tab switching buttons
        if not self._start_pos or not self._end_pos:
            return
        # Get the ratio of the current position in respect to the start/end position
        val = self.posture_manager.getMovementProgress(pos, self._start_pos, self._end_pos)
        if val is None:
            return
        val = min(max(0, int(round(val * 100))), 100)
        logging.debug("Updating move progress to %s%%", val)
        # Set the move gauge with the movement progress percentage
        self.panel.gauge_move.Value = val
        self.panel.gauge_move.Refresh()

    @call_in_wx_main
    def _control_warning_msg(self, posture: int):
        # show/hide the warning msg
        if self._move_cancelled:
            txt_msg = self._get_cancel_warning_msg()
            self._show_warning_msg(txt_msg)
        elif posture == UNKNOWN and self._move_future.done(): # unknown position and not moving
            txt_warning = "To enable buttons, please move away from unknown position."
            self._show_warning_msg(txt_warning)
        else:
            self._show_warning_msg(None)
        self._tab_panel.Layout()

    def _get_cancel_warning_msg(self):
        """
        Create and return a text message to show under the progress bar.
        return (str): the cancel message. It returns None if the target position is None.
        """
        # Show warning message if target position is indicated
        if self._target_posture is not None:
            current_label = POSITION_NAMES[self._current_posture]
            target_label = POSITION_NAMES[self._target_posture]
            return "Stage stopped between {} and {} positions".format(
                current_label, target_label
            )

        return None

    def _toggle_switch_buttons(self, currently_pressed=None):
        """
        Toggle currently pressed button (if any) and untoggle rest of switch buttons
        """
        moving = not self._move_future.done()
        for button in self.position_btns.values():
            if button == currently_pressed:
                if moving:
                    button.SetValue(BTN_TOGGLE_PROGRESS)
                else:
                    button.SetValue(BTN_TOGGLE_COMPLETE)
            else:
                button.SetValue(BTN_TOGGLE_OFF)  # Unpressed

    def _toggle_grid_buttons(self, currently_pressed=None):
        """
        Toggle currently pressed button (if any) and untoggle rest of grid buttons
        """
        # METEOR-only code for now
        for button in self._grid_btns.values():
            if button == currently_pressed:
                button.SetValue(BTN_TOGGLE_COMPLETE)
            else:
                button.SetValue(BTN_TOGGLE_OFF)  # Unpressed

    def _update_movement_controls(self):
        """
        Enable/disable chamber move controls (position and stage) based on current move
        """
        # Get current movement (including unknown and on the path)
        self._current_posture = self.posture_manager.current_posture.value
        self._enable_position_controls(self._current_posture)
        if self._role == 'enzel':
            # Enable stage advanced controls on sem imaging
            self._enable_advanced_controls(self._current_posture == SEM_IMAGING)
        elif self._role == "meteor":
            self._control_warning_msg(self._current_posture)
        elif self._role == 'mimas':
            pass

    def _enable_position_controls(self, current_posture: int):
        """
        Enable/disable switching position button based on current move
        current_posture (acq.move constant): as reported by getCurrentPositionLabel()
        """
        if self._role == 'enzel':
            # Define which button to disable in respect to the current move
            disable_buttons = {LOADING: (), THREE_BEAMS: (), ALIGNMENT: (), COATING: (),
                               SEM_IMAGING: (), LOADING_PATH: (ALIGNMENT, COATING, SEM_IMAGING)}
            for movement, button in self.position_btns.items():
                if current_posture == UNKNOWN:
                    # If at unknown position, only allow going to LOADING position
                    button.Enable(movement == LOADING)
                elif movement in disable_buttons[current_posture]:
                    button.Disable()
                else:
                    button.Enable()

            # TODO: is it useful to leave the current button in "progress" status when cancelled?
            # How can this help the user?

            # The move button should turn green only if current move is known and not cancelled
            if current_posture in self.position_btns and not self._move_cancelled:
                btn = self.position_btns[current_posture]
                # btn.icon_on = img.getBitmap(self.btn_toggle_icons[btn][1])
                btn.SetValue(2)  # Complete
                self._toggle_switch_buttons(btn)
            else:
                self._toggle_switch_buttons(currently_pressed=None)

        elif self._role == 'meteor':
            # enabling/disabling meteor buttons
            for button in itertools.chain(self.position_btns.values(), self._grid_btns.values()):
                button.Enable(current_posture != UNKNOWN)

            # turn on (green) the current position button green
            btn = self.position_btns.get(current_posture)
            self._toggle_switch_buttons(btn)

            # It's a common mistake that the stage.POS_ACTIVE_RANGE is incorrect.
            # If so, the sample moving will be very odd, as the move is clipped to
            # the range. So as soon as we reach FM_IMAGING, we check that the
            # current position is within range, if not, most likely that range is wrong.
            if current_posture == FM_IMAGING:
                imaging_stage = self.tab_data_model.main.stage
                stage_pos = imaging_stage.position.value
                imaging_rng = imaging_stage.getMetadata().get(model.MD_POS_ACTIVE_RANGE, {})
                for a, pos in stage_pos.items():
                    if a in imaging_rng:
                        rng = imaging_rng[a]
                        if not rng[0] <= pos <= rng[1]:
                            logging.warning("After moving to FM IMAGING, stage position is %s, outside of POS_ACTIVE_RANGE %s",
                                            stage_pos, imaging_rng)
                            break

            # Turn on (green) the Grid button
            current_grid_label = self.posture_manager.getCurrentGridLabel()
            btn = self._grid_btns.get(current_grid_label)
            self._toggle_grid_buttons(btn)

        elif self._role == 'mimas':
            # enabling/disabling mimas buttons
            for movement, button in self.position_btns.items():
                if current_posture == UNKNOWN:
                    # If at unknown position, only allow going to LOADING position
                    button.Enable(movement == LOADING)
                else:
                    button.Enable(True)

            # turn on (green) the current position button green
            btn = self.position_btns.get(current_posture)
            self._toggle_switch_buttons(btn)

    def _enable_advanced_controls(self, enable=True):
        """
        Enable/disable stage advanced controls
        """
        self.panel.ctrl_rx.Enable(enable)
        self.panel.stage_align_slider_aligner.Enable(enable)
        for button in self.btn_aligner_axes.keys():
            button.Enable(enable)

    def _btn_show_advanced_toggled(self):
        """
        Get the value of advanced button for _show_advanced_va_connector ctrl_2_va
        """
        return self.panel.btn_switch_advanced.GetValue()

    def _on_show_advanced(self, evt):
        """
        Event handler for the Advanced button to show/hide stage advanced panel
        """
        self.panel.pnl_advanced_align.Show(self.tab_data_model.show_advaned.value)
        # Adjust the panel's static text controls
        fix_static_text_clipping(self.panel)

    def _show_warning_msg(self, txt_warning):
        """
        Show warning message under progress bar, hide if no message is indicated
        """
        self.panel.pnl_ref_msg.Show(txt_warning is not None)
        if txt_warning:
            self.panel.txt_warning.SetLabel(txt_warning)

    def _milling_angle_changed(self, pos):
        """
        Called from the milling axis connector when the stage rx change.
        Updates the milling control with the changed angle position.
        :param pos: (float) value of rx
       """
        current_angle = math.degrees(pos)
        # When the user is typing (and HasFocus() == True) dont updated the value to prevent overwriting the user input
        if not self.panel.ctrl_rx.HasFocus() \
                and not almost_equal(self.panel.ctrl_rx.GetValue(), current_angle, atol=1e-3):
            self.panel.ctrl_rx.SetValue(current_angle)

    def _milling_ctrl_changed(self):
        """
        Called when the milling control value is changed.
        Used to return the correct rx angle value.
        :return: (float) The calculated rx angle from the milling ctrl
        """
        return math.radians(self.panel.ctrl_rx.GetValue())

    def _on_aligner_btn(self, evt):
        """
        Event handling for the stage advanced panel axes buttons
        """
        target_button = evt.theButton
        move_future = self._perform_axis_relative_movement(target_button)
        if move_future is None:
            return
        # Set the tab's move_future and attach its callback
        self._move_future = move_future
        self._move_future.add_done_callback(self._on_move_done)
        self._show_warning_msg(None)
        self.panel.btn_cancel.Enable()

    def _on_switch_btn(self, evt):
        """
        Event handling for the position panel buttons
        """
        self._move_cancelled = False
        target_button = evt.theButton
        move_future = self._perform_switch_position_movement(target_button)
        if move_future is None:
            target_button.SetValue(0)
            return

        # Set the tab's move_future and attach its callback
        self._move_future = move_future
        self._move_future.add_done_callback(self._on_move_done)

        self.panel.gauge_move.Value = 0

        # Indicate we are "busy" (disallow changing tabs)
        self.tab_data_model.main.is_acquiring.value = True

        # Toggle the current button (orange) and enable cancel
        # target_button.icon_on = img.getBitmap(self.btn_toggle_icons[target_button][0])  # orange
        if target_button in self._grid_btns.values():
            self._toggle_grid_buttons(target_button)
        else:
            self._toggle_switch_buttons(target_button)

        self._show_warning_msg(None)
        if self._role == 'enzel':
            self._enable_advanced_controls(False)
        self.panel.btn_cancel.Enable()

    @call_in_wx_main
    def _on_move_done(self, future):
        """
        Done callback of any of the tab movements
        :param future: cancellable future of the move
        """
        try:
            future.result()
        except Exception as ex:
            # Something went wrong, don't go any further
            if not isinstance(ex, CancelledError):
                logging.warning("Failed to move stage: %s", ex)

        self.tab_data_model.main.is_acquiring.value = False

        self.panel.btn_cancel.Disable()
        # Get currently pressed button (if any)
        self._update_movement_controls()

        # After the movement is done, set start, end and target position to None
        # That way any stage moves from outside the chamber tab are not considered
        self._target_posture = None
        self._start_pos = None
        self._end_pos = None

    def _on_cancel(self, evt):
        """
        Called when the cancel button is pressed
        """
        # Cancel the running move
        self._move_future.cancel()
        self.panel.btn_cancel.Disable()
        self._move_cancelled = True
        self._update_movement_controls()
        logging.info("Stage move cancelled.")

    def _perform_switch_position_movement(self, target_button):
        """
        Perform the target switch position target_position procedure based on the requested move and return back the target_position future
        :param target_button: currently pressed button to move the stage to
        :return (CancellableFuture or None): cancellable future of the move
        """
        # Only proceed if there is no currently running target_position
        if not self._move_future.done():
            return

        # Get the required target_position from the pressed button
        all_btns = self.position_btns.copy()
        all_btns.update(self._grid_btns)
        self._target_posture = next((m for m in all_btns.keys() if target_button == all_btns[m]),
                                     None)
        if self._target_posture is None:
            logging.error("Unknown target button: %s", target_button)
            return None

        # define the start position
        self._start_pos = self._stage.position.value
        current_posture = self.posture_manager.current_posture.value
        # determine the end position for the gauge
        end_pos = self.posture_manager.getTargetPosition(self._target_posture)

        if self._role == 'enzel':
            if (
                current_posture is LOADING
                and not self._display_insertion_stick_warning_msg()
            ):
                return None

        elif self._role == 'meteor':
            if (
                self._target_posture in [FM_IMAGING, SEM_IMAGING, MILLING, FIB_IMAGING]
                and current_posture in [LOADING, SEM_IMAGING, FM_IMAGING, MILLING, FIB_IMAGING]
                and not self._display_meteor_pos_warning_msg(end_pos)
            ):
                return None

        self._end_pos = end_pos
        return self.posture_manager.cryoSwitchSamplePosition(self._target_posture)

    def _on_posture(self, posture: int) -> None:
        logging.info(f"Stage posture changed to {POSITION_NAMES[posture]}")
        self._update_movement_controls()

    def _display_insertion_stick_warning_msg(self) -> bool:
        box = wx.MessageDialog(self.main_frame, "The sample will be loaded. Please make sure that the sample is properly set and the insertion stick is removed.",
                            caption="Loading sample", style=wx.YES_NO | wx.ICON_QUESTION| wx.CENTER)
        box.SetYesNoLabels("&Load", "&Cancel")
        ans = box.ShowModal()  # Waits for the window to be closed
        return ans == wx.ID_YES

    def _display_meteor_pos_warning_msg(self, end_pos) -> bool:
        """
        Ask confirmation to the user before moving to a different position on the METEOR
        end_pos: target position of the stage, if the user accepts the move
        return: True if the user accepts, False if the move should be cancelled.
        """
        pos_str = []
        for axis in ("x", "y", "z", "m", "rx", "ry", "rz", "rm"):
            if axis in end_pos:
                if axis.startswith("r"):
                    pos_str.append(f"{axis} = " + readable_str(math.degrees(end_pos[axis]), "°", 4))
                else:
                    pos_str.append(f"{axis} = " + readable_str(end_pos[axis], "m", 4))
        pos_str = "\n". join(pos_str)

        # Check the deviation in rotation angle when switching from SEM to FM,
        # give a warning message if switching is done from a different rotation angle
        target_label = self.tab_data_model.main.posture_manager.getCurrentPostureLabel(end_pos)
        warn_msg = "The stage will move to this position:\n%s\n\nIs it safe?"
        if target_label == FM_IMAGING:
            stage_md = self._stage.getMetadata()
            fav_angles = stage_md[model.MD_FAV_SEM_POS_ACTIVE]
            axis_name = "rz" if "rz" in fav_angles else "rm"
            current_angle = self._stage.position.value[axis_name]
            fav_angle = fav_angles[axis_name]

            if not math.isclose(current_angle, fav_angle):
                warn_msg = ("The current rotation value is different from the desired value. The switching behavior"
                            " may not be proper.\n\n") + warn_msg

        box = wx.MessageDialog(self.main_frame,
                               warn_msg % (pos_str,),
                               caption="Large move of the stage",
                               style=wx.YES_NO | wx.ICON_QUESTION | wx.CENTER)
        ans = box.ShowModal()  # Waits for the window to be closed
        return ans == wx.ID_YES

    def _perform_axis_relative_movement(self, target_button):
        """
        Call the stage relative movement procedure based on the currently requested axis move and return back its future
        :param target_button: currently pressed axis button to relatively move the stage to
        :return (CancellableFuture or None): cancellable future of the move
        """
        # Only proceed if there is no currently running movement
        if not self._move_future.done():
            target_button.SetValue(0)
            return
        # Get the movement text symbol like +X, -X, +Y..etc from the currently pressed button
        axis, sign = self.btn_aligner_axes[target_button]
        stage = self.tab_data_model.main.stage
        md = stage.getMetadata()
        active_range = md[model.MD_POS_ACTIVE_RANGE]
        # The amount of relative move shift is taken from the panel slider
        shift = self.tab_data_model.stage_align_slider_va.value
        shift *= sign
        target_position = stage.position.value[axis] + shift
        if not self._is_in_range(target_position, active_range[axis]):
            warning_text = "Requested movement would go out of stage imaging range."
            self._show_warning_msg(warning_text)
            return
        return stage.moveRel(shift={axis: shift})

    def _is_in_range(self, pos, range):
        """
        A helper function to check if current position is in its axis range
        :param pos: (float) position axis value
        :param range: (tuple) position axis range
        :return: True if position in range, False otherwise
        """
        # Add 1% margin for hardware slight errors
        margin = (range[1] - range[0]) * 0.01
        return (range[0] - margin) <= pos <= (range[1] + margin)

    def _on_sample_heater(self, heating: int):
        """
        Called when sample_thermostat.heating changes, to update the "Sample heater"
          checkbox.
        Converts from several values to boolean.
        Must be called in the main GUI thread.
        heating: new heating value
        """
        # Use MD_FAV_POS_DEACTIVE with the "heating" key to get the off value,
        # and fallback to using the min of the choices.
        md = self.tab_data_model.main.sample_thermostat.getMetadata()
        heating_choices = self.tab_data_model.main.sample_thermostat.heating.choices.keys()
        val_off = md.get(model.MD_FAV_POS_DEACTIVE, {}).get("heating", min(heating_choices))

        # Any value above the "off" value is considered on (and vice-versa)
        self.panel.ctrl_sample_heater.SetValue(heating > val_off)

    def _sample_heater_to_va(self):
        """
        Called to read the "Sample heater" checkbox and return the corresponding
        value to set sample_thermostat.heating
        return (int): value to set in .heating
        """
        # Use the MD_FAV_POS_ACTIVE/MD_FAV_POS_DEACTIVE with the "heating" key.
        # If it's not there, fallback to using the min or max value of the choices
        md = self.tab_data_model.main.sample_thermostat.getMetadata()
        heating_choices = self.tab_data_model.main.sample_thermostat.heating.choices.keys()

        if self.panel.ctrl_sample_heater.GetValue():
            val_on = md.get(model.MD_FAV_POS_ACTIVE, {}).get("heating", max(heating_choices))
            return val_on
        else:
            val_off = md.get(model.MD_FAV_POS_DEACTIVE, {}).get("heating", min(heating_choices))
            return val_off

    def Show(self, show=True):
        Tab.Show(self, show=show)

        # the first time the tab is shown, ask the user to create or load a project
        if self.IsShown() and not self._is_initial_project_ready:

            # stay in the loop until the user successfully creates or loads a project
            while True:

                # Ask the user to create or load a project
                box = wx.MessageDialog(
                    self.main_frame,
                    message="Create or Load a project?",
                    caption="Odemis Project",
                    style=wx.YES_NO | wx.ICON_QUESTION | wx.CENTER,
                )

                box.SetYesNoLabels("&Load Project", "&Create Project",)
                ans = box.ShowModal()  # Waits for the window to be closed

                if ans == wx.ID_NO:
                    logging.debug("creating project")
                    ret = self._create_project()
                elif ans == wx.ID_YES:
                    logging.debug("loading project")
                    ret = self._load_project_data(None)

                # break if the user successfully created or loaded a project
                if ret:
                    break

            # Set the project as ready
            self._is_initial_project_ready = True

    def query_terminate(self):
        """
        Called to perform action prior to terminating the tab
        :return: (bool) True to proceed with termination, False for canceling
        """
        if self._current_posture is LOADING:
            return True
        if self._move_future.running() and self._target_posture is LOADING:
            return self._confirm_terminate_dialog(
                "The sample is still moving to the loading position, are you sure you want to close Odemis?"
            )

        return self._confirm_terminate_dialog(
            "The sample is still loaded, are you sure you want to close Odemis?"
        )

    def _confirm_terminate_dialog(self, message):
        box = wx.MessageDialog(
            self.main_frame,
            message,
            caption="Closing Odemis",
            style=wx.YES_NO | wx.ICON_QUESTION | wx.CENTER,
        )
        box.SetYesNoLabels("&Close Window", "&Cancel")
        ans = box.ShowModal()  # Waits for the window to be closed
        return ans == wx.ID_YES

    @classmethod
    def get_display_priority(cls, main_data):
        if main_data.role in ("enzel", "meteor", "mimas"):
            return 10
        return None
