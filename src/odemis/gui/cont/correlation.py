
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
import logging
import math
import wx
# IMPORTANT: wx.html needs to be imported for the HTMLWindow defined in the XRC
# file to be correctly identified. See: http://trac.wxwidgets.org/ticket/3626
# This is not related to any particular wxPython version and is most likely permanent.
import wx.html
import odemis.acq.stream as acqstream
import odemis.gui.model as guimod
from odemis import model
from odemis.acq.stream import StaticStream
from odemis.gui.util import call_in_wx_main

# TODO: move to more approprate location
def update_image_in_views(s: StaticStream, views: list) -> None:
    """Force update the static stream in the selected views
    :param s: (StaticStream) the static stream to update
    :param views: (list[View]) the list of views to update"""
    v: guimod.View 
    for v in views:
        for sp in v.stream_tree.getProjections():  # stream or projection
            if isinstance(sp, acqstream.DataProjection):
                st = sp.stream
            else:
                st = sp
            if st is s:
                sp._shouldUpdateImage()


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

        # connect the correlation streams to the tab data
        self._panel.cmb_correlation_stream.Bind(wx.EVT_COMBOBOX, self._on_selected_stream_change)

        # reset correlation data
        self._panel.btn_reset_correlation.Bind(wx.EVT_BUTTON, self.reset_correlation_pressed)

        # enable correlation controls
        self._panel.ctrl_enable_correlation.SetValue(True) # enable by default

        # bind mouse and keyboard events for correlation controls
        for vp in self._viewports:
            vp.canvas.Bind(wx.EVT_CHAR, self.on_char)
            vp.canvas.Bind(wx.EVT_LEFT_DOWN, self.on_mouse_down)
    
    @call_in_wx_main
    def _update_correlation_cmb(self, streams: list) -> None:
        """update the correlation combo box with the available streams
        :param streams: (list[StaticStream]) the streams to add to the combo box"""
        # keep the combobox in sync with streams
        self._panel.cmb_correlation_stream.Clear()
        for s in streams:
            self._panel.cmb_correlation_stream.Append(s.name.value, s)
        
        # select the first stream, if available
        if len(streams) > 0:
            self._panel.cmb_correlation_stream.SetSelection(0)
    
    @call_in_wx_main
    def _on_correlation_streams_change(self, streams: list) -> None:
        """hide/show the correlation panel if there are no streams
        :param streams: (list[StaticStream]) the streams in the correlation tab"""
        visible = len(streams) != 0
        self._panel.fp_meteor_correlation.Show(visible)

    @call_in_wx_main
    def _on_selected_stream_change(self, evt: wx.Event) -> None:
        """change the selected stream to the one selected in the combo box
        :param evt: (wx.Event) the event"""
        idx = self._panel.cmb_correlation_stream.GetSelection()
        self._tab_data_model.selected_stream.value = self._panel.cmb_correlation_stream.GetClientData(idx)
        logging.debug(f"Selected Stream Changed to {idx}: {self._tab_data_model.selected_stream.value.name.value}")
    
    def reset_correlation_pressed(self, evt: wx.Event) -> None:
        """"Reset the correlation data for the selected stream, and re-draw
        :param evt: (wx.Event) the event"""
        s = self._tab_data_model.selected_stream.value
        self._reset_stream_correlation_data(s)
        update_image_in_views(s, self._tab_data_model.views.value)

    def _reset_stream_correlation_data(self, s: StaticStream) -> None:
        """reset the stream position to the original position / rotation / scale
        :param s: (StaticStream) the stream to reset"""
        s.raw[0].metadata[model.MD_POS_COR] = (0, 0)
        s.raw[0].metadata[model.MD_ROTATION_COR] = 0
        s.raw[0].metadata[model.MD_PIXEL_SIZE_COR] = (1, 1)       

    def add_streams(self, streams: list) -> None:
        """add streams to the correlation tab
        :param streams: (list[StaticStream]) the streams to add"""

        # NOTE: we only add streams if they are not already in the correlation tab
        # we will not remove streams from the correlation tab from outside it, 
        # as the user may still want to correlate them, 
        # even if they have been removed from another tab, e.g. 'localization'

        # add streams to correlation tab
        logging.debug(f"Adding {len(streams)} streams to correlation tab")
        for s in streams:
            
            # skip existing streams
            if s in self._tab_data_model.streams.value:
                continue 

            # reset the stream correlation data, and add to correlation streams
            self._reset_stream_correlation_data(s)
            self._tab_data_model.streams.value.append(s)          
            
            # add stream to streambar
            sc = self._tab.streambar_controller.addStream(s, add_to_view=True, play=False)
            sc.stream_panel.show_remove_btn(True)  

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
    
        else:
            logging.debug("invalid correlation event, passing event to canvas")
            active_canvas.on_left_down(evt)       # super event passthrough      

    def on_char(self, evt: wx.Event) -> None:
        """handle key presses
        :param evt: (wx.Event) the event"""

        if not self.correlation_enabled():
            logging.debug("correlation not enabled, passing event to canvas")
            active_canvas = evt.GetEventObject()
            active_canvas.on_char(evt)            # super event passthrough

        key = evt.GetKeyCode()
        shift_mod = evt.ShiftDown()

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
            dx = self._panel.dx_step_cntrl.GetValue()
            dy = self._panel.dy_step_cntrl.GetValue()

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
        
        # fit the source viewports to the content, as the image may have moved
        self._panel.vp_correlation_tl.canvas.fit_view_to_content()
        self._panel.vp_correlation_tr.canvas.fit_view_to_content()

    
    def _move_stream_to_pos(self, pos: tuple) -> None:
        """move the selected stream to the position pos
        :param pos: (tuple) the realspace position to move the stream to (metres)"""
        # the difference between the clicked position, and the position in metadata
        # is the offset (be careful because correlation is sign flipped)
        s = self._tab_data_model.selected_stream.value
        
        # the cur_pos is the realspace position of the image
        p = s.raw[0].metadata[model.MD_POS]
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
