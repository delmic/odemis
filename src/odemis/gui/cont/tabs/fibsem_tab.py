# -*- coding: utf-8 -*-

"""
@author: Patrick Cleeve
Copyright © 2024, Delmic

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
import logging
import math
import numpy

import wx

import odemis.gui.cont.acquisition as acqcont
import odemis.gui.cont.views as viewcont
import odemis.gui.model as guimod
import odemis.gui.util as guiutil
from odemis import model
from odemis.acq.move import FIB_IMAGING, MILLING, POSITION_NAMES, SEM_IMAGING
from odemis.acq.stream import (
    FIBStream,
    LiveStream,
    SEMStream,
    StaticFIBStream,
    StaticSEMStream,
)
from odemis.gui import conf
from odemis.gui.comp.buttons import BTN_TOGGLE_COMPLETE, BTN_TOGGLE_OFF
from odemis.gui.conf.licences import LICENCE_FIBSEM_ENABLED, LICENCE_MILLING_ENABLED
from odemis.gui.cont import milling, settings
from odemis.gui.cont.correlation import update_image_in_views
from odemis.gui.cont.features import CryoFeatureController
from odemis.gui.cont.stream_bar import (
    CryoFIBAcquiredStreamsController,
    CryoStreamsController,
)
from odemis.gui.cont.tabs.tab import Tab
from odemis.gui.model import TOOL_ACT_ZOOM_FIT
from odemis.gui.util import call_in_wx_main
from odemis.util import units
from odemis.util.dataio import data_to_static_streams


class FibsemTab(Tab):

    def __init__(self, name, button, panel, main_frame, main_data):
        """
        :type name: str
        :type button: odemis.gui.comp.buttons.TabButton
        :type panel: wx._windows.Panel
        :type main_frame: odemis.gui.main_xrc.xrcfr_main
        :type main_data: odemis.gui.model.MainGUIData
        """
        tab_data = guimod.CryoFIBSEMGUIData(main_data)
        super(FibsemTab, self).__init__(
            name, button, panel, main_frame, tab_data)

        self.main_data = main_data

        # First we create the views, then the streams
        vpv = self._create_views(main_data, panel.pnl_secom_grid.viewports)

        # Order matters!
        self.view_controller = viewcont.ViewPortController(tab_data, panel, vpv)

        # Connect the view selection buttons
        buttons = collections.OrderedDict([
            (panel.btn_secom_view_all,
                (None, panel.lbl_secom_view_all)),
            (panel.btn_secom_view_tl,
                (panel.vp_secom_tl, panel.lbl_secom_view_tl)),
            (panel.btn_secom_view_tr,
                (panel.vp_secom_tr, panel.lbl_secom_view_tr)),
            (panel.btn_secom_view_bl,
                (panel.vp_secom_bl, panel.lbl_secom_view_bl)),
            (panel.btn_secom_view_br,
                (panel.vp_secom_br, panel.lbl_secom_view_br)),
        ])

        # remove the play overlay from the top view with static streams
        panel.vp_secom_bl.canvas.remove_view_overlay(panel.vp_secom_bl.canvas.play_overlay)
        panel.vp_secom_br.canvas.remove_view_overlay(panel.vp_secom_br.canvas.play_overlay)

        tab_data.focussedView.subscribe(self._on_view, init=True)

        self._view_selector = viewcont.ViewButtonController(
            tab_data,
            panel,
            buttons,
            panel.pnl_secom_grid.viewports
        )

        self._settingbar_controller = settings.LocalizationSettingsController(
            panel,
            tab_data
        )
        panel.fp_settings_secom_optical.Show(False) # TODO: remove dependency

        self._streambar_controller = CryoStreamsController(
            tab_data,
            panel.pnl_secom_streams,
            view_ctrl=self.view_controller
        )
        self._streambar_controller._stream_bar.btn_add_stream.Hide()

        self._acquisition_controller = acqcont.CryoAcquiController(
            tab_data, panel, self, mode=guimod.AcquiMode.FIBSEM)
        self.conf = conf.get_acqui_conf()

        # Toolbar
        self.tb = panel.secom_toolbar
        for t in guimod.TOOL_ORDER:
            if t in tab_data.tool.choices:
                self.tb.add_tool(t, tab_data.tool)
        # Add fit view to content to toolbar
        self.tb.add_tool(TOOL_ACT_ZOOM_FIT, self.view_controller.fitViewToContent)


        # TODO: get components from main_data?
        # setup electron beam, det
        electron_beam = model.getComponent(role="e-beam")
        electron_det = model.getComponent(role="se-detector")

        hwemtvas = set()
        hwdetvas = set()

        hwemt_vanames = ("probeCurrent", "accelVoltage", "resolution", "dwellTime", "horizontalFoV")
        hwdet_vanames = ("brightness", "contrast", "detector_mode", "detector_type")
        for vaname in model.getVAs(electron_beam):
            if vaname in hwemt_vanames:
                hwemtvas.add(vaname)
        for vaname in model.getVAs(electron_det):
            if vaname in hwdet_vanames:
                hwdetvas.add(vaname)

        self.sem_stream = SEMStream(
            name="SEM",
            detector=electron_det,
            dataflow=electron_det.data,
            emitter=electron_beam,
            focuser=main_data.ebeam_focus, #electron_focus,
            hwemtvas=hwemtvas,
            hwdetvas=hwdetvas,
            blanker=None)

        # setup ion beam, det
        ion_beam = model.getComponent(role="ion-beam")
        ion_det = model.getComponent(role="se-detector-ion")

        hwemtvas = set()
        hwdetvas = set()
        for vaname in model.getVAs(ion_beam):
            if vaname in hwemt_vanames:
                hwemtvas.add(vaname)
        for vaname in model.getVAs(ion_det):
            if vaname in hwdet_vanames:
                hwdetvas.add(vaname)

        self.fib_stream = FIBStream(
            name="FIB",
            detector=ion_det,
            dataflow=ion_det.data,
            emitter=ion_beam,
            focuser=main_data.ion_focus,
            hwemtvas=hwemtvas,
            hwdetvas=hwdetvas,
        )
        sem_stream_cont = self._streambar_controller.addStream(self.sem_stream, add_to_view=True)
        sem_stream_cont.stream_panel.show_remove_btn(False)

        fib_stream_cont = self._streambar_controller.addStream(self.fib_stream, add_to_view=True)
        fib_stream_cont.stream_panel.show_remove_btn(False)

        self._feature_panel_controller = CryoFeatureController(tab_data, panel, self,
                                                               mode=guimod.AcquiMode.FIBSEM)

        self._acquired_stream_controller = CryoFIBAcquiredStreamsController(
            tab_data=tab_data,
            feature_view=tab_data.views.value[3],
            ov_view = tab_data.views.value[2],
            stream_bar=panel.pnl_cryosecom_acquired,
            view_ctrl=self.view_controller,
            static=True,
        )
        # self.chamber_tab = None
        self.tab_data_model.streams.subscribe(self._on_acquired_streams)
        # Link which overview streams is shown with the ones shown in the Chamber
        self.tab_data_model.views.value[2].stream_tree.flat.subscribe(self._on_overview_visible)

        # milling pattern controls
        self.milling_task_controller = milling.MillingTaskController(tab_data, panel, self)
        self.automation_controller = milling.AutomatedMillingController(tab_data, panel, self)
        panel.Layout()

        # fib viewport double click event for vertical movements
        self.pm = self.tab_data_model.main.posture_manager
        panel.pnl_secom_grid.viewports[1].canvas.Bind(wx.EVT_LEFT_DCLICK, self.on_dbl_click) # bind the double click event

        # TODO: replace with current_posture?
        self.pm.stage.position.subscribe(self._on_stage_pos, init=True)
        self.panel = panel

        rx = self.pm.stage.getMetadata()[model.MD_FAV_MILL_POS_ACTIVE]["rx"]
        self.panel.ctrl_milling_angle.SetValue(math.degrees(rx))
        self.panel.ctrl_milling_angle.Bind(wx.EVT_COMMAND_ENTER, self._update_milling_angle)
        self._update_milling_angle(None)
        self.panel.btn_switch_milling.Bind(wx.EVT_BUTTON, self._move_to_milling_position)
        self.panel.btn_switch_sem_imaging.Bind(wx.EVT_BUTTON, self._move_to_sem)

    def _on_view(self, view):
        """Hide/Disable milling controls when fib view is not selected"""
        # is_fib_view = issubclass(view.stream_classes, FIBStream)
        is_fib_view = view == self.panel.vp_secom_br.view
        self.panel.fp_milling.Show(is_fib_view and LICENCE_MILLING_ENABLED)
        # TODO: activate the corresponding channel on xtui

        self.tab_data_model.is_sem_active_view = view == self.panel.vp_secom_tl.view
        self.tab_data_model.is_fib_active_view = view == self.panel.vp_secom_tr.view
        if hasattr(self, "_acquisition_controller"):
            txt = "ACQUIRE"
            if self.tab_data_model.is_sem_active_view:
                txt = "ACQUIRE SEM"
            if self.tab_data_model.is_fib_active_view:
                txt = "ACQUIRE FIB"
            self._acquisition_controller._panel.btn_cryosecom_acquire.SetLabel(txt)

        live = issubclass(view.stream_classes, LiveStream)
        self.panel.fp_secom_streams.Show(live)
        self.panel.fp_acquisitions.Show(live)
        self.panel.fp_automation.Show(not live and LICENCE_MILLING_ENABLED)
        self.panel.fp_acquired.Show(not live)

    @property
    def settingsbar_controller(self):
        return self._settingbar_controller

    @property
    def streambar_controller(self):
        return self._streambar_controller

    def _create_views(self, main_data, viewports):
        """
        Create views depending on the actual hardware present
        return OrderedDict: as needed for the ViewPortController
        """
        # Acquired data at the top, live data at the bottom
        vpv = collections.OrderedDict([
            (viewports[0],  # focused view
             {
                "cls": guimod.MicroscopeView,
                "stage": main_data.stage,
                "name": "SEM",
                "stream_classes": SEMStream,
              }),
            (viewports[1],
             {
                "cls": guimod.MicroscopeView,
                "name": "FIB",
                "stage": main_data.stage,
                "stream_classes": FIBStream,
              }),
            (viewports[2],
             {"name": "Overview",
              "cls": guimod.FeatureOverviewView,
              "stage": main_data.stage,
              "stream_classes": StaticSEMStream,
              }),
            (viewports[3],
             {"name": "FeatureView",
              "stream_classes": StaticFIBStream,
              }),
        ])

        return vpv

    @call_in_wx_main
    def load_overview_data(self, data):
        # Create streams from data
        streams = data_to_static_streams(data)
        bbox = (None, None, None, None)  # ltrb in m
        for s in streams:

            if not isinstance(s, StaticSEMStream):
                logging.debug("Only StaticSEMStream supported for overview data in this tab")
                continue
            s.name.value = "Overview " + s.name.value
            # Add the static stream to the streams list of the model and also to the overviewStreams to easily
            # distinguish between it and other acquired streams
            self.tab_data_model.overviewStreams.value.append(s)
            self.tab_data_model.streams.value.insert(0, s)
            self._acquired_stream_controller.showOverviewStream(s)

            # ov_view = self.panel.vp_secom_bl.view
            # ov_view.addStream(s)
            # ov_sc = self.streambar_controller._add_stream_cont(s, show_panel=True, static=True,
            #                        view=ov_view)
            # ov_sc.stream_panel.show_remove_btn(True)

            # Compute the total bounding box
            try:
                s_bbox = s.getBoundingBox()
            except ValueError:
                continue  # Stream has no data (yet)
            if bbox[0] is None:
                bbox = s_bbox
            else:
                bbox = (min(bbox[0], s_bbox[0]), min(bbox[1], s_bbox[1]),
                        max(bbox[2], s_bbox[2]), max(bbox[3], s_bbox[3]))

        # Recenter to the new content only
        if bbox[0] is not None:
            self.panel.vp_secom_bl.canvas.fit_to_bbox(bbox)

        # sync overview streams with correlation tab
        if len(streams) > 0 and self.main_data.role == "meteor":
            correlation_tab = self.main_data.getTabByName("meteor-correlation")
            correlation_tab.correlation_controller.add_streams(streams)

    def clear_acquired_streams(self):
        """
        Remove overview map streams and feature streams, both from view and panel.
        """
        self._acquired_stream_controller.clear()

    @call_in_wx_main
    def _on_acquired_streams(self, streams):
        """
        Filter out deleted acquired streams (features and overview) from their respective origin
        :param streams: list(Stream) updated list of tab streams
        """
        from odemis.gui.cont.tabs import LocalizationTab, CryoChamberTab

        # Get all acquired streams from features list and overview streams
        acquired_streams = set()
        # for feature in self.tab_data_model.main.features.value:
        #     acquired_streams.update(feature.streams.value)
        acquired_streams.update(self.tab_data_model.overviewStreams.value)

        unused_streams = acquired_streams.difference(set(streams))
        # localization_tab: LocalizationTab = self.main_data.getTabByName("cryosecom-localization")


        for st in unused_streams:
            if st in self.tab_data_model.overviewStreams.value:
                # remove from fibsem tab model
                self.tab_data_model.overviewStreams.value.remove(st)
                # remove from chamber tab
                chamber_tab: CryoChamberTab = self.main_data.getTabByName("cryosecom_chamber")
                chamber_tab.remove_overview_streams([st])
                logging.debug(f"Stream removed from chamber tab: {st.name.value}")
                # remove from localization tab
                # if st in localization_tab.tab_data_model.overviewStreams.value:
                #     logging.debug(f"Removing stream from other tabs: {st.name.value}")
                #     localization_tab.tab_data_model.overviewStreams.value.remove(st)
                #     localization_tab.tab_data_model.streams.value.remove(st)
                #
                #     # remove from overview view
                #     localization_tab._acquired_stream_controller._ov_view.removeStream(st)
                #     update_image_in_views(st, localization_tab.tab_data_model.views.value)
                #     logging.debug(f"Stream removed from localization tab: {st.name.value}")

        # Update and save the used stream settings on acquisition
        if acquired_streams:
            self._streambar_controller.update_stream_settings()

    def _stop_streams_subscriber(self):
        self.tab_data_model.streams.unsubscribe(self._on_acquired_streams)

    def _start_streams_subscriber(self):
        self.tab_data_model.streams.subscribe(self._on_acquired_streams)

    def _on_overview_visible(self, val):
        """
        Apply the visibility status of overview streams in the FIBSEM tab to
        the overview streams in the Chamber tab.
        """
        # prevent circular import
        from odemis.gui.cont.tabs.cryo_chamber_tab import CryoChamberTab

        # All the overview streams
        ov_streams = set(self.tab_data_model.overviewStreams.value)
        # Visible overview streams
        visible_ov_streams = set(self.tab_data_model.views.value[2].getStreams())
        # Invisible overview streams
        invisible_ov_streams = ov_streams.difference(visible_ov_streams)
        # Hide the invisible overview streams
        chamber_tab: CryoChamberTab = self.main_data.getTabByName("cryosecom_chamber")
        chamber_tab.remove_overview_streams(invisible_ov_streams)
        # Show the visible overview streams
        chamber_tab.load_overview_streams(visible_ov_streams)

    def on_dbl_click(self, evt):

        active_canvas = evt.GetEventObject()
        logging.debug(f"mouse down event, canvas: {active_canvas}")

        if evt.AltDown():
            # get the position of the mouse, convert to physical position
            pos = evt.GetPosition()
            p_pos = active_canvas.view_to_phys(pos, active_canvas.get_half_buffer_size())
            init_pos = self.fib_stream.raw[0].metadata[model.MD_POS]

            # get difference between p_pos and init_pos
            dx = p_pos[0] - init_pos[0]
            dy = p_pos[1] - init_pos[1]

            # invert dy if scan rotated.
            if numpy.isclose(self.main_data.ion_beam.rotation.value,
                                math.radians(180),
                                atol=1e-2):
                dy *= -1
                logging.debug("Scan rotation detected, inverting dy")
            logging.info(f"Moving stage vertically by: {dx}, {dy}")
            f = self.pm.sample_stage.moveRelChamberReferential({"x": dx, "z": dy})
            f.result()
            return

        # super event passthrough
        active_canvas.on_left_down(evt)

    @call_in_wx_main
    def _on_stage_pos(self, pos):
        """
        Called when the stage is moved, enable the tab if position is imaging mode, disable otherwise
        :param pos: (dict str->float or None) updated position of the stage
        """
        guiutil.enable_tab_on_stage_position(
            button=self.button,
            posture_manager=self.pm,
            target=[SEM_IMAGING, MILLING, FIB_IMAGING],
            tooltip="FIBSEM tab is only available at SEM position"
        )

        # update stage pos label
        rx = math.degrees(pos["rx"])
        rz = math.degrees(pos["rz"])
        posture = self.pm.current_posture.value
        pos_name = POSITION_NAMES[posture]

        # TODO: move this to legend.py
        r = units.readable_str(units.round_significant(rz, 3))
        t = units.readable_str(units.round_significant(rx, 3))
        txt = f"Stage R: {r}° T: {t}° [{pos_name}]"

        # update the stage position label
        try:
            self.view_controller.viewports[0].bottom_legend.set_stage_pos_label(txt)
            self.view_controller.viewports[1].bottom_legend.set_stage_pos_label(txt)
            self.view_controller.viewports[2].bottom_legend.set_stage_pos_label(txt)
            ltab  = self.main_data.getTabByName("cryosecom-localization")
            if ltab:
                ltab.view_controller.viewports[0].bottom_legend.set_stage_pos_label(txt)
                ltab.view_controller.viewports[2].bottom_legend.set_stage_pos_label(txt)
                ltab.view_controller.viewports[3].bottom_legend.set_stage_pos_label(txt)
        except Exception as e:
            pass

        # update the stage position buttons
        self.panel.btn_switch_sem_imaging.SetValue(BTN_TOGGLE_OFF) # BTN_TOGGLE_OFF
        self.panel.btn_switch_milling.SetValue(BTN_TOGGLE_OFF)
        if posture == SEM_IMAGING:
            self.panel.btn_switch_sem_imaging.SetValue(BTN_TOGGLE_COMPLETE) # BTN_TOGGLE_COMPLETE
        if posture == MILLING:
            self.panel.btn_switch_milling.SetValue(BTN_TOGGLE_COMPLETE)

        self.panel.Layout()

    def _update_milling_angle(self, evt: wx.Event):

        # update the metadata of the stage
        milling_angle = math.radians(self.panel.ctrl_milling_angle.GetValue())
        current_md = self.pm.stage.getMetadata()
        self.pm.stage.updateMetadata({model.MD_FAV_MILL_POS_ACTIVE: {'rx': milling_angle,
                                                                     "rz": current_md[model.MD_FAV_MILL_POS_ACTIVE]["rz"]}})

        md = self.pm.get_posture_orientation(MILLING)
        stage_tilt = md["rx"]
        self.panel.ctrl_milling_angle.SetToolTip(f"A milling angle of {math.degrees(milling_angle):.2f}° "
                                                 f"corresponds to a stage tilt of {math.degrees(stage_tilt):.2f}°")

        self._on_stage_pos(self.pm.stage.position.value)

        # if the tab isn't shown, we don't want to ask the user
        if evt is None: # if the event is None, it means this is the initial update, dont ask the user
            return

        # changing milling angle, causes previously defined features at milling angle to be "seen" as SEM_IMAGING
        # QUERY: should we update the features to the new milling angle?
        box = wx.MessageDialog(self.main_frame,
                            message=f"Do you want to update existing feature positions with the updated milling angle ({math.degrees(milling_angle):.2f}°)?",
                            caption="Update existing feature positions?", style=wx.YES_NO | wx.ICON_QUESTION | wx.CENTER)

        ans = box.ShowModal()  # Waits for the window to be closed
        if ans == wx.ID_YES:
            logging.debug(f"Updating existing feature positions with the updated milling angle ({math.degrees(milling_angle):.2f}°)")
            # NOTE: use stage_tilt, not milling_angle
            for feature in self.main_data.features.value:
                milling_position = feature.get_posture_position(MILLING)
                if milling_position is not None:
                    milling_position["rx"] = stage_tilt
                    feature.set_posture_position(MILLING, milling_position)

    def _move_to_milling_position(self, evt: wx.Event):
        logging.info(f"MILLING ORIENTATION: {self.pm.get_posture_orientation(MILLING)}")

        if self.pm.current_posture.value != SEM_IMAGING:
            wx.MessageBox("Switch to SEM position first", "Error", wx.OK | wx.ICON_ERROR)
            return

        f = self.pm.cryoSwitchSamplePosition(MILLING)
        f.result()

        self._on_stage_pos(self.pm.stage.position.value)

    def _move_to_sem(self, evt: wx.Event):

        if self.pm.current_posture.value != MILLING:
            wx.MessageBox("Switch to milling position first", "Error", wx.OK | wx.ICON_ERROR)
            return

        f = self.pm.cryoSwitchSamplePosition(SEM_IMAGING)
        f.result()

        self._on_stage_pos(self.pm.stage.position.value)

    def terminate(self):
        self.main_data.stage.position.unsubscribe(self._on_stage_pos)
        # make sure the streams are stopped
        for s in self.tab_data_model.streams.value:
            s.is_active.value = False

    @classmethod
    def get_display_priority(cls, main_data):
        if main_data.role == "meteor" and main_data.fibsem and LICENCE_FIBSEM_ENABLED:
            return 2
        else:
            return None

    # def Show(self, show=True):
    #     assert (show != self.IsShown())  # we assume it's only called when changed

    #     if not show:  # if fibsem tab is not chosen
    #         # pause streams when not displayed
    #         self.streambar_controller.pauseStreams()
