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
import logging
import numpy
import wx

from typing import List
from odemis.gui import conf
from odemis.gui.cont.features import CryoFeatureController

from odemis import model
import odemis.acq.stream as acqstream
import odemis.gui
import odemis.gui.cont.acquisition as acqcont
from odemis.gui.cont.stream_bar import CryoAcquiredStreamsController, CryoStreamsController
import odemis.gui.cont.views as viewcont
from odemis.gui.cont import milling
import odemis.gui.model as guimod
import odemis.gui.util as guiutil
from odemis.acq.align import AutoFocus
from odemis.acq.move import MILLING, ALIGNMENT, \
    FM_IMAGING, SEM_IMAGING, THREE_BEAMS
from odemis.acq.stream import LiveStream, StaticStream
from odemis.gui.conf.data import get_local_vas
from odemis.gui.cont import settings
from odemis.gui.cont.acquisition import CryoZLocalizationController
from odemis.gui.cont.tabs.tab import Tab
from odemis.gui.model import TOOL_ACT_ZOOM_FIT, TOOL_AUTO_FOCUS
from odemis.gui.util import call_in_wx_main
from odemis.util.dataio import data_to_static_streams


class LocalizationTab(Tab):

    def __init__(self, name, button, panel, main_frame, main_data):
        """
        :type name: str
        :type button: odemis.gui.comp.buttons.TabButton
        :type panel: wx._windows.Panel
        :type main_frame: odemis.gui.main_xrc.xrcfr_main
        :type main_data: odemis.gui.model.MainGUIData
        """

        tab_data = guimod.CryoLocalizationGUIData(main_data)
        super(LocalizationTab, self).__init__(
            name, button, panel, main_frame, tab_data)
        # self.set_label("LOCALIZATION")

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
        panel.vp_secom_tl.canvas.remove_view_overlay(panel.vp_secom_tl.canvas.play_overlay)
        panel.vp_secom_tr.canvas.remove_view_overlay(panel.vp_secom_tr.canvas.play_overlay)

        # Add a sample overlay to each view (if we have information)
        if main_data.sample_centers:
            for vp in (panel.vp_secom_tl, panel.vp_secom_tr, panel.vp_secom_bl, panel.vp_secom_br):
                vp.show_sample_overlay(main_data.sample_centers, main_data.sample_radius)

        # Default view is the Live 1
        tab_data.focussedView.value = panel.vp_secom_bl.view
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

        self._streambar_controller = CryoStreamsController(
            tab_data,
            panel.pnl_secom_streams,
            view_ctrl=self.view_controller
        )

        self._acquisition_controller = acqcont.CryoAcquiController(
            tab_data, panel, self)

        self._acquired_stream_controller = CryoAcquiredStreamsController(
            tab_data,
            feature_view=tab_data.views.value[1],
            ov_view=tab_data.views.value[0],
            stream_bar=panel.pnl_cryosecom_acquired,
            view_ctrl=self.view_controller,
            static=True,
        )

        self._feature_panel_controller = CryoFeatureController(tab_data, panel, self)
        self._zloc_controller = CryoZLocalizationController(tab_data, panel, self)
        self.tab_data_model.streams.subscribe(self._on_acquired_streams)
        self.conf = conf.get_acqui_conf()

        # Toolbar
        self.tb = panel.secom_toolbar
        for t in guimod.TOOL_ORDER:
            if t in tab_data.tool.choices:
                self.tb.add_tool(t, tab_data.tool)
        # Add fit view to content to toolbar
        self.tb.add_tool(TOOL_ACT_ZOOM_FIT, self.view_controller.fitViewToContent)
        # auto focus
        self._autofocus_f = model.InstantaneousFuture()
        self.tb.add_tool(TOOL_AUTO_FOCUS, self.tab_data_model.autofocus_active)
        self.tb.enable_button(TOOL_AUTO_FOCUS, False)
        self.tab_data_model.autofocus_active.subscribe(self._onAutofocus)
        tab_data.streams.subscribe(self._on_current_stream)
        self._streambar_controller.addFluo(add_to_view=True, play=False)
        # Link which overview streams is shown with the ones shown in the Chamber
        self.tab_data_model.views.value[0].stream_tree.flat.subscribe(self._on_overview_visible)

        # Will create SEM stream with all settings local
        emtvas = set()
        hwemtvas = set()

        # The sem stream is visible for enzel, but not for meteor
        if self.main_data.role == 'enzel':
            for vaname in get_local_vas(main_data.ebeam, main_data.hw_settings_config):
                if vaname in ("resolution", "dwellTime", "scale"):
                    emtvas.add(vaname)
                else:
                    hwemtvas.add(vaname)

            # This stream is used both for rendering and acquisition
            sem_stream = acqstream.SEMStream(
                "Secondary electrons",
                main_data.sed,
                main_data.sed.data,
                main_data.ebeam,
                focuser=main_data.ebeam_focus,
                hwemtvas=hwemtvas,
                hwdetvas=None,
                emtvas=emtvas,
                detvas=get_local_vas(main_data.sed, main_data.hw_settings_config)
            )

            sem_stream_cont = self._streambar_controller.addStream(sem_stream, add_to_view=True)
            sem_stream_cont.stream_panel.show_remove_btn(False)

        # Only enable the tab when the stage is at the right position
        if self.main_data.role == "enzel":
            self._stage = self.tab_data_model.main.stage
            self._allowed_targets = [THREE_BEAMS, ALIGNMENT, SEM_IMAGING]
        elif self.main_data.role == "meteor":
            # The stage is in the FM referential, but we care about the stage-bare
            # in the SEM referential to move between positions
            self._allowed_targets = [FM_IMAGING]
            self._stage = self.tab_data_model.main.stage_bare
        elif self.main_data.role == "mimas":
            # Only useful near the active positions: milling (FIB) or FLM
            self._allowed_targets = [FM_IMAGING, MILLING]
            self._stage = self.tab_data_model.main.stage

        self._aligner = self.tab_data_model.main.aligner

        main_data.is_acquiring.subscribe(self._on_acquisition, init=True)

        # For now, only possible to mill on the MIMAS. Eventually, this could be
        # dependent on the availability of the ion-beam component.
        if self.main_data.role == "mimas":
            self._serial_milling_controller = milling.MillingButtonController(
                tab_data,
                panel,
                self
            )

    def _on_overview_visible(self, val):
        """
        Apply the visibility status of overview streams in the Localisation tab to
        the overview streams in the Chamber tab.
        """
        # prevent circular import
        from odemis.gui.cont.tabs.cryo_chamber_tab import CryoChamberTab

        # All the overview streams
        ov_streams = set(self.tab_data_model.overviewStreams.value)
        # Visible overview streams
        visible_ov_streams = set(self.tab_data_model.views.value[0].getStreams())
        # Invisible overview streams
        invisible_ov_streams = ov_streams.difference(visible_ov_streams)
        # Hide the invisible overview streams
        chamber_tab: CryoChamberTab = self.main_data.getTabByName("cryosecom_chamber")
        chamber_tab.remove_overview_streams(invisible_ov_streams)
        # Show the visible overview streams
        chamber_tab.load_overview_streams(visible_ov_streams)

    def _on_view(self, view):
        """Hide/Disable hardware related sub-panels and controls on the right when not in live stream"""
        live = issubclass(view.stream_classes, odemis.acq.stream._live.LiveStream)
        self.panel.fp_settings_secom_optical.Show(live)
        self.panel.fp_secom_streams.Show(live)
        self.panel.fp_acquisitions.Show(live)
        self.panel.btn_use_current_z.Enable(live)

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
                 "cls": guimod.FeatureOverviewView,
                 "stage": main_data.stage,
                 "name": "Overview",
                 "stream_classes": StaticStream,
              }),
            (viewports[1],
             {"name": "Acquired",
              "cls": guimod.FeatureView,
              "zPos": self.tab_data_model.zPos,
              "stream_classes": StaticStream,
              }),
            (viewports[2],
             {"name": "Live 1",
              "cls": guimod.FeatureView,
              "stage": main_data.stage,
              "stream_classes": LiveStream,
              }),
            (viewports[3],
             {"name": "Live 2",
              "cls": guimod.FeatureView,
              "stage": main_data.stage,
              "stream_classes": LiveStream,
              }),
        ])

        return vpv

    @call_in_wx_main
    def load_overview_data(self, data: List[model.DataArray]):
        # Create streams from data
        streams = data_to_static_streams(data)
        bbox = (None, None, None, None)  # ltrb in m
        for s in streams:
            s.name.value = "Overview " + s.name.value
            # Add the static stream to the streams list of the model and also to the overviewStreams to easily
            # distinguish between it and other acquired streams
            self.tab_data_model.overviewStreams.value.append(s)
            self.tab_data_model.streams.value.insert(0, s)
            self._acquired_stream_controller.showOverviewStream(s)

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
            self.panel.vp_secom_tl.canvas.fit_to_bbox(bbox)

        # Mimas does not have the overview image in the chamber tab, so don't display it there.
        if self.main_data.role in ["meteor", "enzel"]:
            # Display the same acquired data in the chamber tab view
            chamber_tab = self.main_data.getTabByName("cryosecom_chamber")
            chamber_tab.load_overview_streams(streams)

        # sync overview streams with correlation tab
        if len(streams) > 0 and self.main_data.role == "meteor":
            correlation_tab = self.main_data.getTabByName("meteor-correlation")
            correlation_tab.correlation_controller.add_streams(streams)

    def reset_live_streams(self):
        """
        Clear the content of the live streams. So the streams and their settings
        are still available, but without any image.
        """
        live_streams = [stream for stream in self.tab_data_model.streams.value if isinstance(stream, LiveStream)]
        for stream in live_streams:
            if stream.raw:
                stream.raw = []
                stream.image.value = None
                stream.histogram._set_value(numpy.empty(0), force_write=True)

    def clear_acquired_streams(self):
        """
        Remove overview map streams and feature streams, both from view and panel.
        """
        self._acquired_stream_controller.clear()

    def _onAutofocus(self, active):
        if active:
            # Determine which stream is active
            try:
                self.curr_s = self.tab_data_model.streams.value[0]
            except IndexError:
                # Should not happen as the menu/icon should be disabled
                logging.info("No stream to run the autofocus")
                self.tab_data_model.autofocus_active.value = False
                return

            # run only if focuser is available
            if self.curr_s.focuser:
                emt = self.curr_s.emitter
                det = self.curr_s.detector
                self._autofocus_f = AutoFocus(det, emt, self.curr_s.focuser)
                self._autofocus_f.add_done_callback(self._on_autofocus_done)
            else:
                # Should never happen as normally the menu/icon are disabled
                logging.info("Autofocus cannot run as no hardware is available")
                self.tab_data_model.autofocus_active.value = False
        else:
            self._autofocus_f.cancel()

    def _on_autofocus_done(self, future):
        self.tab_data_model.autofocus_active.value = False

    def _on_current_stream(self, streams):
        """
        Called when the current stream changes
        """
        # Try to get the current stream
        try:
            self.curr_s = streams[0]
        except IndexError:
            self.curr_s = None

        if self.curr_s:
            self.curr_s.should_update.subscribe(self._on_stream_update, init=True)
        else:
            wx.CallAfter(self.tb.enable_button, TOOL_AUTO_FOCUS, False)

    def _on_stream_update(self, updated):
        """
        Called when the current stream changes play/pause
        Used to update the autofocus button
        """
        # TODO: just let the menu controller also update toolbar (as it also
        # does the same check for the menu entry)
        try:
            self.curr_s = self.tab_data_model.streams.value[0]
        except IndexError:
            f = None
        else:
            f = self.curr_s.focuser

        f_enable = all((updated, f))
        if not f_enable:
            self.tab_data_model.autofocus_active.value = False
        wx.CallAfter(self.tb.enable_button, TOOL_AUTO_FOCUS, f_enable)

    @call_in_wx_main
    def _on_acquired_streams(self, streams):
        """
        Filter out deleted acquired streams (features and overview) from their respective origin
        :param streams: list(Stream) updated list of tab streams
        """
        # Get all acquired streams from features list and overview streams
        acquired_streams = set()
        for feature in self.tab_data_model.main.features.value:
            acquired_streams.update(feature.streams.value)
        acquired_streams.update(self.tab_data_model.overviewStreams.value)

        unused_streams = acquired_streams.difference(set(streams))

        for st in unused_streams:
            if st in self.tab_data_model.overviewStreams.value:
                self.tab_data_model.overviewStreams.value.remove(st)
                # Remove from chamber tab too
                chamber_tab = self.main_data.getTabByName("cryosecom_chamber")
                chamber_tab.remove_overview_streams([st])

        # Update and save the used stream settings on acquisition
        if acquired_streams:
            self._streambar_controller.update_stream_settings()

    @call_in_wx_main
    def display_acquired_data(self, data):
        """
        Display the acquired streams on the top right view
        data (DataArray): the images/data acquired
        """
        # get the top right view port
        view = self.tab_data_model.views.value[1]
        self.tab_data_model.focussedView.value = view
        for s in data_to_static_streams(data):
            if self.tab_data_model.main.currentFeature.value:
                s.name.value = self.tab_data_model.main.currentFeature.value.name.value + " - " + s.name.value
                self.tab_data_model.main.currentFeature.value.streams.value.append(s)
            self.tab_data_model.streams.value.insert(0, s)  # TODO: let addFeatureStream do that
            self._acquired_stream_controller.showFeatureStream(s)
        # refit the latest acquired feature so that the new data is fully visible in the
        # acquired view even when the user had moved around/zoomed in
        self.panel.vp_secom_tr.canvas.fit_view_to_content()

    def _stop_streams_subscriber(self):
        self.tab_data_model.streams.unsubscribe(self._on_acquired_streams)

    def _start_streams_subscriber(self):
        self.tab_data_model.streams.subscribe(self._on_acquired_streams)

    def _on_acquisition(self, is_acquiring):
        # When acquiring, the tab is automatically disabled and should be left as-is
        # In particular, that's the state when moving between positions in the
        # Chamber tab, and the tab should wait for the move to be complete before
        # actually be enabled.
        if is_acquiring:
            self._stage.position.unsubscribe(self._on_stage_pos)
        else:
            self._stage.position.subscribe(self._on_stage_pos, init=True)

    # role -> tooltip message
    DISABLED_TAB_TOOLTIP = {
        "enzel": "Localization can only be performed in the three beams or SEM imaging modes",
        "meteor": "Localization can only be performed in FM mode",
        "mimas": "Localization and milling can only be performed in optical or FIB mode",
    }

    def _on_stage_pos(self, pos):
        """
        Called when the stage is moved, enable the tab if position is imaging mode, disable otherwise
        :param pos: (dict str->float or None) updated position of the stage
        """
        guiutil.enable_tab_on_stage_position(
            self.button,
            self.main_data.posture_manager,
            self._allowed_targets,
            tooltip=self.DISABLED_TAB_TOOLTIP.get(self.main_data.role)
        )

    def terminate(self):
        super(LocalizationTab, self).terminate()
        self._stage.position.unsubscribe(self._on_stage_pos)
        # make sure the streams are stopped
        for s in self.tab_data_model.streams.value:
            s.is_active.value = False

    @classmethod
    def get_display_priority(cls, main_data):
        if main_data.role in ("enzel", "meteor", "mimas"):
            return 2
        else:
            return None

    def Show(self, show=True):
        assert (show != self.IsShown())  # we assume it's only called when changed
        super(LocalizationTab, self).Show(show)

        if not show:  # if localization tab is not chosen
            # pause streams when not displayed
            self._streambar_controller.pauseStreams()
