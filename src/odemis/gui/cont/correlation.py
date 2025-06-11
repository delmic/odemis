
# -*- coding: utf-8 -*-

"""
@author Patrick Cleeve

Copyright © 2023, Delmic

Handles the controls for correlating two (or more) streams together.

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
import copy
import logging
import math
from typing import List

import wx

# IMPORTANT: wx.html needs to be imported for the HTMLWindow defined in the XRC
# file to be correctly identified. See: http://trac.wxwidgets.org/ticket/3626
# This is not related to any particular wxPython version and is most likely permanent.
import wx.html

import odemis.acq.stream as acqstream
import odemis.gui.model as guimod
from odemis import model
from odemis.acq.stream import RGBStream, StaticFluoStream, StaticSEMStream, StaticStream
from odemis.gui.cont.tabs.localization_tab import LocalizationTab
from odemis.gui.util import call_in_wx_main
from odemis.acq.move import FM_IMAGING

# TODO: move to more approprate location
def update_image_in_views(s: StaticStream, views: List[guimod.StreamView]) -> None:
    """Force update the static stream in the selected views (forces image update)
    :param s: (StaticStream) the static stream to update
    :param views: (list[StreamView]) the list of views to update"""
    v: guimod.StreamView
    for v in views:
        for sp in v.stream_tree.getProjections():  # stream or projection
            st = sp.stream if isinstance(sp, acqstream.DataProjection) else sp

            # only update the selected stream
            if st is s:
                sp.force_image_update()

def convert_rgb_to_sem(rgb_stream: RGBStream) -> StaticSEMStream:
    """Convert an RGB stream to a SEM stream
    :param rgb_stream: (RGBStream) the RGB stream to convert
    :return: (StaticSEMStream) the converted SEM stream
    """
    d = rgb_stream.raw[0]
    if isinstance(d, model.DataArrayShadow):
        d = d.getData()

    # get dim order, and select the first channel (arbitrary choice)
    dims = d.metadata[model.MD_DIMS]
    if dims == "YXC":
        d = d[:, :, 0]
    elif dims == "CYX":
        d = d[0, :, :]

    # convert to sem stream
    sem_stream = StaticSEMStream(rgb_stream.name.value, raw=d)

    # update md
    sem_stream.raw[0].metadata = copy.deepcopy(d.metadata)
    sem_stream.raw[0].metadata[model.MD_ACQ_TYPE] = model.MD_AT_EM
    sem_stream.raw[0].metadata[model.MD_DIMS] = "YX"

    return sem_stream

class CorrelationController(object):

    def __init__(self, tab_data, panel, tab, viewports):
        """
        :param tab_data: (MicroscopyGUIData) the representation of the microscope GUI
        :param panel: (wx._windows.Panel) the panel containing the UI controls
        :param tab: (Tab) the tab which should show the data
        :param viewports: (ViewPorts) the tab view ports
        """

        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._panel = panel
        self._tab = tab
        self._viewports = viewports

        # disable if no streams are present
        self._tab_data_model.streams.subscribe(self._on_correlation_streams_change, init=True)
        self._tab_data_model.streams.subscribe(self._update_correlation_cmb, init=True)
        # if not self._main_data_model.is_viewer:
        #     # localisation tab doesn't exist in viewer
        #     self._tab_data_model.streams.subscribe(self.update_localization_tab_streams, init=False)

        # connect the correlation streams to the tab data
        self._panel.cmb_correlation_stream.Bind(wx.EVT_COMBOBOX, self._on_selected_stream_change)

        # reference frames
        self.ref_frame_names = ["METEOR", "FIBSEM", "No Reference"] # for display purposes
        self.ref_stream_map = {0: StaticFluoStream,  # METEOR
                               1: StaticSEMStream,  # FIBSEM
                               2: type(None)} # match everything except None (No reference type)
        self.ref_stream = self.ref_stream_map[0]

        # connect the reference stream to the tab data
        self._panel.cmb_correlation_reference.Append(self.ref_frame_names)
        self._panel.cmb_correlation_reference.SetSelection(0)
        self._panel.cmb_correlation_reference.Bind(wx.EVT_COMBOBOX, self._on_reference_stream_change)

        # reset correlation data
        self._panel.btn_reset_correlation.Bind(wx.EVT_BUTTON, self.reset_correlation_pressed)

        # enable correlation controls
        self._panel.ctrl_enable_correlation.SetValue(True) # enable by default
        self._panel.ctrl_auto_resize_view.SetValue(False) # disable auto resize by default
        self._panel.ctrl_enable_correlation.SetToolTip("Enable/Disable correlation controls")
        self._panel.ctrl_auto_resize_view.SetToolTip("Automatically resize correlation overlay viewports after moving the streams.")

        # bind mouse and keyboard events for correlation controls
        for vp in self._viewports:
            vp.canvas.Bind(wx.EVT_CHAR, self.on_char)
            vp.canvas.Bind(wx.EVT_LEFT_DOWN, self.on_mouse_down)

        # localization tab
        self.localization_tab: LocalizationTab = None

        # auto correlation SEM<>FM, based on the  stage-bare position found in the image metadata
        self.pm = self._main_data_model.posture_manager
        if self.pm:
            self._panel.btn_correlate.Bind(wx.EVT_BUTTON, self.on_auto_correlate)
        else:
            # On the viewer, there is no info on the stage transformation, so cannot do auto correlation
            self._panel.btn_correlate.Disable()

    @call_in_wx_main
    def _update_correlation_cmb(self, streams: list) -> None:
        """update the correlation combo box with the available streams
        :param streams: (list[StaticStream]) the streams to add to the combo box"""
        # keep the combobox in sync with streams
        self._panel.cmb_correlation_stream.Clear()
        for s in streams:
            if isinstance(s, self.ref_stream):
                continue # cant move fluo streams
            self._panel.cmb_correlation_stream.Append(s.name.value, s)

        # select the first stream, if available and nothing is selected
        if (self._panel.cmb_correlation_stream.GetCount() > 0
            and self._panel.cmb_correlation_stream.GetSelection() == wx.NOT_FOUND):
            self._panel.cmb_correlation_stream.SetSelection(0) # this doesn't trigger selection event

            # trigger the event manually, to automatically select the first stream
            if self._tab_data_model.selected_stream.value is None:
                logging.debug(f"Forcing selected stream change to first stream")
                self._on_selected_stream_change(None)

    @call_in_wx_main
    def _on_correlation_streams_change(self, streams: list) -> None:
        """hide/show the correlation panel if there are no streams
        :param streams: (list[StaticStream]) the streams in the correlation tab"""
        visible = len(streams) != 0
        self._panel.fp_meteor_correlation.Show(visible)
        # reset selected stream
        if not visible:
            self._panel.cmb_correlation_stream.Clear()
            self._tab_data_model.selected_stream.value = None

    @call_in_wx_main
    def _on_selected_stream_change(self, evt: wx.Event) -> None:
        """change the selected stream to the one selected in the combo box
        :param evt: (wx.Event) the event"""
        idx = self._panel.cmb_correlation_stream.GetSelection()
        self._tab_data_model.selected_stream.value = self._panel.cmb_correlation_stream.GetClientData(idx)
        logging.debug(f"Selected Stream Changed to {idx}: {self._tab_data_model.selected_stream.value.name.value}")

    @call_in_wx_main
    def _on_reference_stream_change(self, evt: wx.Event) -> None:
        """change the reference stream to the one selected in the combo box
        :param evt: (wx.Event) the event"""
        idx = self._panel.cmb_correlation_reference.GetSelection()
        self.ref_stream = self.ref_stream_map[idx]
        logging.debug(f"Reference Frame: {self.ref_frame_names[idx]} - Fixed stream: {idx}, {self.ref_stream}")

        # refresh combobox
        self._update_correlation_cmb(self._tab_data_model.streams.value)

    def reset_correlation_pressed(self, evt: wx.Event) -> None:
        """"Reset the correlation data for the selected stream, and re-draw
        :param evt: (wx.Event) the event"""
        s = self._tab_data_model.selected_stream.value
        self._reset_stream_correlation_data(s)
        update_image_in_views(s, self._tab_data_model.views.value)
        self.fit_correlation_views_to_content(force=True)

    def _reset_stream_correlation_data(self, s: StaticStream) -> None:
        """reset the stream position to the original position / rotation / scale
        :param s: (StaticStream) the stream to reset"""
        if s is not None and s.raw:
            s.raw[0].metadata[model.MD_POS_COR] = (0, 0)
            s.raw[0].metadata[model.MD_ROTATION_COR] = 0
            s.raw[0].metadata[model.MD_PIXEL_SIZE_COR] = (1, 1)

            self.update_localization_tab_streams_metadata(s)

    def add_streams(self, streams: list) -> None:
        """add streams to the correlation tab
        :param streams: (list[StaticStream]) the streams to add"""

        # NOTE: we only add streams if they are not already in the correlation tab
        # we will not remove streams from the correlation tab from outside it,
        # as the user may still want to correlate them,
        # even if they have been removed from another tab, e.g. 'localization'

        # add streams to correlation tab
        logging.debug(f"Adding {len(streams)} streams to correlation tab {streams}")
        for s in streams:

            # skip existing streams, live streams
            if s in self._tab_data_model.streams.value or not isinstance(s, StaticStream):
                continue

            # if the user has loaded a rgb stream, assume it is meant to be a SEM stream
            # (convert to 2D SEM stream, fix metadata, etc.)
            if isinstance(s, RGBStream):
                logging.debug(f"Converting RGB stream to SEM: {s.name.value}")
                s = convert_rgb_to_sem(s)

            # reset the stream correlation data, and add to correlation streams
            self._reset_stream_correlation_data(s)
            self._tab_data_model.streams.value.append(s)

            # add stream to streambar
            sc = self._tab.streambar_controller.addStream(s, add_to_view=True, play=False)
            sc.stream_panel.show_remove_btn(True)

    def _stop_streams_subscriber(self):
        self._tab_data_model.streams.unsubscribe(self.update_localization_tab_streams)

    def _start_streams_subscriber(self):
        self._tab_data_model.streams.subscribe(self.update_localization_tab_streams, init=False)

    @call_in_wx_main
    def update_localization_tab_streams(self, streams: list) -> None:
        """add streams to the localization tab
        :param streams: (list[StaticStream]) the streams to add"""
        if self.localization_tab is None:
            try:
                self.localization_tab: LocalizationTab = self._main_data_model.getTabByName("cryosecom-localization")
            except LookupError:
                return  # Localization tab doesn't exist (eg, on the Viewer) => nothing to do

        # TODO: extend this to support non-overviews
        # remove streams from localization tab when they are deleted from correlation tab
        current_streams = len(streams)
        localization_streams = len(self.localization_tab.tab_data_model.overviewStreams.value)
        if current_streams < localization_streams:
            # remove streams from localization tab
            logging.debug("Attempting to remove streams from localization tab")
            for s in self.localization_tab.tab_data_model.overviewStreams.value:
                if s not in streams:
                    logging.debug(f"Removing stream from other tabs: {s.name.value}")
                    # remove from model
                    self.localization_tab.tab_data_model.overviewStreams.value.remove(s)
                    self.localization_tab.tab_data_model.streams.value.remove(s)

                    # remove from overview view
                    self.localization_tab._acquired_stream_controller._ov_view.removeStream(s)
                    update_image_in_views(s, self.localization_tab.tab_data_model.views.value)
                    logging.debug(f"Stream removed from localization tab: {s.name.value}")

                    # remove from chamber tab
                    chamber_tab = self._main_data_model.getTabByName("cryosecom_chamber")
                    chamber_tab.remove_overview_streams([s])
                    logging.debug(f"Stream removed from chamber tab: {s.name.value}")

                    return

        logging.debug(f"Adding {len(streams)} streams to localization tab {streams}")
        for s in streams:
            # add stream to localizations tab
            if s not in self.localization_tab.tab_data_model.streams.value:
                self.localization_tab.tab_data_model.overviewStreams.value.append(s)
                self.localization_tab.tab_data_model.streams.value.insert(0, s)
                self.localization_tab._acquired_stream_controller.showOverviewStream(s)


    def clear_streams(self) -> None:
        """clears streams from the correlation tab"""
        self._tab.streambar_controller.clear()

    def correlation_enabled(self) -> bool:
        """return if correlation controls are enabled and a stream is selected"""
        logging.debug(f"correlation enabled: {self._panel.ctrl_enable_correlation.IsChecked()}")
        logging.debug(f"selected stream: {self._tab_data_model.selected_stream.value}")
        return (self._panel.ctrl_enable_correlation.IsChecked() and
                self._tab_data_model.selected_stream.value is not None)

    def on_mouse_down(self, evt: wx.Event) -> None:
        """handle mouse down events
        :param evt: (wx.Event) the event"""
        active_canvas = evt.GetEventObject()
        logging.debug(f"mouse down event, canvas: {active_canvas}")

        # check if shift is pressed, and if a stream is selected
        if evt.ShiftDown() and self.correlation_enabled():

            # get the position of the mouse, convert to physical position
            pos = evt.GetPosition()
            p_pos = active_canvas.view_to_phys(pos, active_canvas.get_half_buffer_size())
            logging.debug(f"shift pressed, mouse_pos: {pos}, phys_pos: {p_pos}")

            # move selected stream to position
            self._move_stream_to_pos(p_pos)

        elif self._tab_data_model.tool.value == guimod.TOOL_RULER:
            logging.debug(f"Ruler is active, passing event to gadget overlay")
            active_canvas.gadget_overlay.on_left_down(evt)  # ruler is active, pass event to ruler
        else:
            logging.debug("invalid correlation event, passing event to canvas")
            active_canvas.on_left_down(evt)       # super event passthrough

    def on_char(self, evt: wx.Event) -> None:
        """handle key presses
        :param evt: (wx.Event) the event"""

        # event data
        key = evt.GetKeyCode()
        shift_mod = evt.ShiftDown()

        # pass through event, if not a valid correlation key or enabled
        valid_keys = [wx.WXK_LEFT, wx.WXK_RIGHT, wx.WXK_UP, wx.WXK_DOWN]
        if key not in valid_keys or not self.correlation_enabled():
            evt.Skip()
            return

        ### CONTROLS ##############################
        # SHIFT + LEFT CLICK -> MOVE_TO_POSITION
        # LEFT, RIGHT -> TRANSLATION X
        # UP, DOWN -> TRANSLATION Y
        # SHIFT + LEFT, RIGHT -> ROTATION
        # SHIFT + UP, DOWN -> SCALE
        ###########################################
        dx, dy, dr, dpx  = 0, 0, 0, 0

        # correlation control modifiers
        if shift_mod:
            dr = math.radians(self._panel.dr_step_cntrl.GetValue())
            dpx = self._panel.dpx_step_cntrl.GetValue() / 100
        else:
            dx = dy = self._panel.dxy_step_cntrl.GetValue()

        logging.debug(f"key: {key}, shift: {shift_mod}")
        logging.debug(f"dx: {dx}, dy: {dy}, dr: {dr}, dpx: {dpx}")

        if key == wx.WXK_LEFT:
            self._move_stream(-dx, 0, -dr, 0)
        elif key == wx.WXK_RIGHT:
            self._move_stream(dx, 0, dr, 0)
        elif key == wx.WXK_UP:
            self._move_stream(0, dy, 0, dpx)
        elif key == wx.WXK_DOWN:
            self._move_stream(0, -dy, 0, -dpx)

    def _move_stream(self, dx: float, dy: float , dr: float = 0, dpx: float = 0) -> None:
        """move the selected stream by the specified amount. the stream is forced
        to update in the views.
        :param dx: (float) the change in x translation (metres)
        :param dy: (float) the change y translation (metres)
        :param dr: (float) the change in rotation (radians)
        :param dpx: (float) the change in scale of the pixelsize (percentage)
        """
        if self._tab_data_model.selected_stream.value is None:
            return

        s = self._tab_data_model.selected_stream.value

        logging.debug(f"move stream {s.name.value}: {dx}, {dy}, {dr}, {dpx}")

        # translation
        p = s.raw[0].metadata[model.MD_POS_COR]
        if len(p) == 2:
            x, y = p
            s.raw[0].metadata[model.MD_POS_COR] = (x - dx, y - dy) # correlation direction is reversed
        else:
            x, y, z = p
            s.raw[0].metadata[model.MD_POS_COR] = (x - dx, y - dy, z)

        # rotation
        rotation = s.raw[0].metadata.get(model.MD_ROTATION_COR, 0)
        s.raw[0].metadata[model.MD_ROTATION_COR] = (rotation + dr)

        # scale (pixel size)
        scalecor = s.raw[0].metadata.get(model.MD_PIXEL_SIZE_COR, (1, 1))
        s.raw[0].metadata[model.MD_PIXEL_SIZE_COR] = (scalecor[0] + dpx, scalecor[1] + dpx)

        # TODO: split x, y scale, add shear?

        # update the image in the views
        update_image_in_views(s, self._tab_data_model.views.value)

        # fit viewports to content, as they have moved
        self.fit_correlation_views_to_content()

        # update the localization tab
        if not self._main_data_model.is_viewer:
            self.update_localization_tab_streams_metadata(s)

    def fit_correlation_views_to_content(self, force: bool = False) -> None:
        # TODO: be more selective about which viewports to fit
        # fit the source viewports to the content, as the image may have moved
        self._panel.vp_correlation_tl.canvas.fit_view_to_content()
        self._panel.vp_correlation_tr.canvas.fit_view_to_content()

        # fit the overlay viewports to the content (optional)
        auto_resize = self._panel.ctrl_auto_resize_view.IsChecked()
        if auto_resize or force:
            self._panel.vp_correlation_bl.canvas.fit_view_to_content()
            self._panel.vp_correlation_br.canvas.fit_view_to_content()

    def update_localization_tab_streams_metadata(self, s: StaticStream) -> None:
        """update the metadata of the stream in the localization tab"""

        if self.localization_tab is None:
            try:
                self.localization_tab: LocalizationTab = self._main_data_model.getTabByName("cryosecom-localization")
            except LookupError:
                return  # Localization tab doesn't exist (eg, on the Viewer) => nothing to do

        # also update the localization tab
        if s in self.localization_tab.tab_data_model.streams.value:

            logging.debug(f"Updating localisation stream: {s.name.value}")

            # get stream in localization tab
            idx = self.localization_tab.tab_data_model.streams.value.index(s)
            sl = self.localization_tab.tab_data_model.streams.value[idx]

            # match cor metadata
            sl.raw[0].metadata[model.MD_POS_COR] = s.raw[0].metadata[model.MD_POS_COR]
            sl.raw[0].metadata[model.MD_ROTATION_COR] = s.raw[0].metadata[model.MD_ROTATION_COR]
            sl.raw[0].metadata[model.MD_PIXEL_SIZE_COR] = s.raw[0].metadata[model.MD_PIXEL_SIZE_COR]

            update_image_in_views(s, self.localization_tab.tab_data_model.views.value)

    def _move_stream_to_pos(self, pos: tuple) -> None:
        """move the selected stream to the position pos
        :param pos: (tuple) the realspace position to move the stream to (metres)"""
        # the difference between the clicked position, and the position in metadata
        # is the offset (be careful because correlation is sign flipped)
        s = self._tab_data_model.selected_stream.value

        # the cur_pos is the realspace position of the image
        p = s.raw[0].metadata.get(model.MD_POS, (0,0))
        x, y = p[:2]  # x, y positions only (ignore z)

        # the correlation pos is the change in position in the viewer
        cx, cy = s.raw[0].metadata[model.MD_POS_COR]

        # the new position is the difference between the clicked position and the realspace position
        # i.e. the correlation position. However, already have an existing correlation position, so we need to
        # find how much we need to modify this by.
        nx = -(pos[0] - x)
        ny = -(pos[1] - y)

        # the offset is the difference between the current cor position and the new position
        # that is how much we need to move the current correlation position by.
        dx = cx - nx
        dy = cy - ny

        logging.debug(f"cur_pos: {x}, {y}, cor_pos: {cx}, {cy}, new_pos: {nx}, {ny}, offset_pos: {dx}, {dy}")

        # move the stream using the correlation position offset
        self._move_stream(dx=dx, dy=dy, dr=0, dpx=0)

    def on_auto_correlate(self, evt: wx.Event) -> None:
        """"Automatically correlate the SEM/FM overviews based on the SEM stage position"""

        # Use the posture transformation to convert the SEM image position (in stage-bare coordinates)
        # to equivalent ones in the FM sample stage coordinates
        # Note: as of 2025-05, this is also needed for the SEM images acquired in Odemis, as the
        # sample stage origin is different per posture. Once this is fixed, and all postures have the
        # sample origin, this feature is only useful when importing SEM images from the SEM software.
        for s in self._tab_data_model.streams.value:
            if not isinstance(s, StaticSEMStream):
                continue

            md = s.raw[0].metadata
            try:
                emd = md[model.MD_EXTRA_SETTINGS]
                stage_md = emd["Stage"]
                pos = stage_md["position"][0]
            except KeyError:
                logging.debug(f"Stream {s.name.value} does not have SEM stage position metadata")
                continue

            # convert sem stage-bare position to fm sample-stage position
            fm_pos = self.pm.to_posture(pos, FM_IMAGING)
            ssp = self.pm.to_sample_stage_from_stage_position(fm_pos)
            md_pos = (ssp["x"], ssp["y"])
            # update metadata
            self._tab_data_model.selected_stream.value = s
            self._move_stream_to_pos(md_pos)
