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
import wx

import odemis.gui.cont.acquisition as acqcont
import odemis.gui.cont.views as viewcont
import odemis.gui.model as guimod
from odemis.gui import conf

from odemis import model
from odemis.gui.cont.stream_bar import CryoStreamsController, CryoFIBAcquiredStreamsController
from odemis.gui.cont import milling, settings
from odemis.gui.cont.features import CryoFeatureController
from odemis.gui.cont.tabs.tab import Tab
from odemis.acq.stream import LiveStream, SEMStream, FIBStream, StaticSEMStream, StaticFIBStream
from odemis.gui.model import TOOL_ACT_ZOOM_FIT
from odemis.gui.util import call_in_wx_main
from odemis.util.dataio import data_to_static_streams

# milling feature flag
MILLING_ENABLED = True

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
            tab_data, panel, self)
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

        hwemt_vanames = ("beamCurrent", "accelVoltage", "resolution", "dwellTime", "horizontalFoV")
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
        # hwemt_vanames = ("beamCurrent", "accelVoltage", "resolution", "dwellTime", "horizontalFoV")
        # hwdet_vanames = ("brightness", "contrast", "detector_mode", "detector_type")
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

        self._feature_panel_controller = CryoFeatureController(tab_data, panel, self)

        self._acquired_stream_controller = CryoFIBAcquiredStreamsController(
            tab_data=tab_data,
            feature_view=tab_data.views.value[3],
            stream_bar=panel.pnl_cryosecom_acquired,
            view_ctrl=self.view_controller,
            static=True,
        )

        # milling pattern controls
        self.milling_task_controller = milling.MillingTaskController(tab_data, panel, self)
        self.automation_controller = milling.AutomatedMillingController(tab_data, panel, self)
        panel.Layout()

        # fib viewport double click event for vertical movements
        self.pm = self.tab_data_model.main.posture_manager
        panel.pnl_secom_grid.viewports[1].canvas.Bind(wx.EVT_LEFT_DCLICK, self.on_dbl_click) # bind the double click event


    def _on_view(self, view):
        """Hide/Disable milling controls when fib view is not selected"""
        # is_fib_view = issubclass(view.stream_classes, FIBStream)
        is_fib_view = view == self.panel.vp_secom_br.view
        self.panel.fp_milling.Show(is_fib_view and MILLING_ENABLED)
        # TODO: activate the corresponding channel on xtui

        live = issubclass(view.stream_classes, LiveStream)
        self.panel.fp_secom_streams.Show(live)
        self.panel.fp_acquisitions.Show(live)
        self.panel.fp_automation.Show(not live)
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

            ov_view = self.panel.vp_secom_bl.view
            ov_view.addStream(s)
            ov_sc = self.streambar_controller._add_stream_cont(s, show_panel=True, static=True,
                                   view=ov_view)
            ov_sc.stream_panel.show_remove_btn(True)

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

    def on_dbl_click(self, evt):

        active_canvas = evt.GetEventObject()
        logging.debug(f"mouse down event, canvas: {active_canvas}")

        if evt.AltDown():
            # get the position of the mouse, convert to physical position
            pos = evt.GetPosition()
            p_pos = active_canvas.view_to_phys(pos, active_canvas.get_half_buffer_size())
            init_pos = self.fib_stream.raw[0].metadata[model.MD_POS]

            # get difference between p_pos and init_pos
            dist_x = p_pos[0] - init_pos[0]
            dist_y = p_pos[1] - init_pos[1]

            logging.info(f"Moving stage vertically by: {dist_x}, {dist_y}")
            f = self.pm.stage.move_vertical({"x": dist_x, "y": dist_y})
            f.result()
            return

        # super event passthrough
        active_canvas.on_left_down(evt)

    # def _on_stage_pos(self, pos):
    #     """
    #     Called when the stage is moved, enable the tab if position is imaging mode, disable otherwise
    #     :param pos: (dict str->float or None) updated position of the stage
    #     """
    #     guiutil.enable_tab_on_stage_position(
    #         self.button,
    #         self.main_data.posture_manager,
    #         self._allowed_targets,
    #         tooltip=self.DISABLED_TAB_TOOLTIP.get(self.main_data.role)
    #     )

    def terminate(self):
        # self._stage.position.unsubscribe(self._on_stage_pos)
        # make sure the streams are stopped
        for s in self.tab_data_model.streams.value:
            s.is_active.value = False

    @classmethod
    def get_display_priority(cls, main_data):
        has_fibsem = any([c.role == "fibsem" for c in model.getComponents()])
        if main_data.role == "meteor" and has_fibsem:
            return 2
        else:
            return None

    # def Show(self, show=True):
    #     assert (show != self.IsShown())  # we assume it's only called when changed

    #     if not show:  # if fibsem tab is not chosen
    #         # pause streams when not displayed
    #         self.streambar_controller.pauseStreams()