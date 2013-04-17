#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Created on 6 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 2 of the License, or (at your option) any later
version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

import logging
import threading
import time

import wx
from wx.lib.pubsub import pub
from decorator import decorator

import odemis.gui as gui
from .comp.canvas import DraggableCanvas
from .comp.overlay import CrossHairOverlay, ViewSelectOverlay, WorldSelectOverlay
from odemis.gui.model import EM_STREAMS



# Various modes canvas elements can go into.

MODE_SECOM_ZOOM = 1
MODE_SECOM_UPDATE = 2

SECOM_MODES = (MODE_SECOM_ZOOM, MODE_SECOM_UPDATE)

MODE_SPARC_SELECT = 3
MODE_SPARC_PICK = 4

SPARC_MODES = (MODE_SPARC_SELECT, MODE_SPARC_PICK)



@decorator
def microscope_view_check(f, self, *args, **kwargs):
    """ This method decorator check if the microscope_view attribute is set
    """
    if self.microscope_view:
        return f(self, *args, **kwargs)

class DblMicroscopeCanvas(DraggableCanvas):
    """ A draggable, flicker-free window class adapted to show pictures of two
    microscope simultaneously.

    It knows size and position of what is represented in a picture and display
    the pictures accordingly.

    It also provides various typical overlays (ie, drawings) for microscope views.
    """
    def __init__(self, *args, **kwargs):
        DraggableCanvas.__init__(self, *args, **kwargs)
        self.microscope_view = None

        self.Bind(wx.EVT_MOUSEWHEEL, self.OnWheel)

        # TODO: If it's too resource consuming, which might want to create just
        # our own thread
        # FIXME: "stop all axes" should also cancel the next timer
        self._moveFocusLock = threading.Lock()
        self._moveFocusDistance = [0, 0]
        # TODO deduplicate!
        self._moveFocus0Timer = wx.PyTimer(self._moveFocus0)
        self._moveFocus1Timer = wx.PyTimer(self._moveFocus1)

        # for thumbnail update (might need a timer, instead of a minimum period
        self._lastThumbnailUpdate = 0
        self._thumbnailUpdatePeriod = 2 # s, minimal period before updating again

        # self.WorldOverlays.append(CrossHairOverlay("Blue",
        #                                            CROSSHAIR_SIZE,
        #                                            (-10, -10)))
        # self.WorldOverlays.append(CrossHairOverlay("Red",
        #                                            CROSSHAIR_SIZE,
        #                                            (-10, -10)))

        self.current_mode = None

    def setView(self, microscope_view):
        """
        Set the microscope_view that this canvas is displaying/representing
        Can be called only once, at initialisation.

        :param microscope_view:(instrmodel.MicroscopeView)
        """
        # This is a kind of kludge, see mscviewport.MicroscopeViewport for
        # details
        assert(self.microscope_view is None)

        self.microscope_view = microscope_view

        # meter per "world unit"
        # for conversion between "world pos" in the canvas and a real unit
        # mpp == mpwu => 1 world coord == 1 px => scale == 1
        self.mpwu = self.microscope_view.mpp.value  #m/wu
        # Should not be changed!
        # FIXME: have a PhyscicalCanvas which directly use physical units

        self.microscope_view.mpp.subscribe(self._onMPP)

        # TODO: subscribe to view_pos to synchronize with the other views
        # TODO: subscribe to stage_pos as well/instead.
        if hasattr(self.microscope_view, "stage_pos"):
            self.microscope_view.stage_pos.subscribe(self._onStagePos, init=True)

        # any image changes
        microscope_view.lastUpdate.subscribe(self._onViewImageUpdate, init=True)


    def _convertStreamsToImages(self):
        """
        Temporary function to convert the StreamTree to a list of images as the
            canvas currently expects.
        """
        streams = self.microscope_view.stream_tree.streams
        # create a list of of each stream's image, but re-ordered so that SEM is
        #first
        images = []
        has_sem_image = False
        for i, s in enumerate(streams):
            if not s:
                # should not happen, but let's not completely fail on this
                logging.error("StreamTree has a None stream")
                continue

            if hasattr(s, "image"):
                iim = s.image.value

                if iim is None or iim.image is None:
                    continue

                if isinstance(s, EM_STREAMS):
                    # as first
                    images.insert(0, iim)
                    if has_sem_image:
                        logging.warning(("Multiple SEM images are not handled "
                                         "correctly for now"))
                    has_sem_image = True
                else:
                    images.append(iim)

        if not has_sem_image: # make sure there is always a SEM image
            images.insert(0, None)

        # remove all the images (so that the images deleted go away)
        self.Images = [None]

        # add the images in order
        for i, iim in enumerate(images):
            if iim is None:
                continue
            scale = float(iim.mpp) / self.mpwu
            pos = (iim.center[0] / self.mpwu, iim.center[1] / self.mpwu)
            self.SetImage(i, iim.image, pos, scale)

        # set merge_ratio
        self.merge_ratio = self.microscope_view.stream_tree.kwargs.get("merge", 0.5)

    def _onViewImageUpdate(self, t):
        # TODO use the real streamtree functions
        # for now we call a conversion layer
        self._convertStreamsToImages()
        logging.debug("Will update drawing for new image")
        wx.CallAfter(self.ShouldUpdateDrawing)

    def UpdateDrawing(self):
        # override just in order to detect when it's just finished redrawn

        # TODO: detect that the canvas is not visible, and so should no/less
        # frequently
        # be updated?
        super(DblMicroscopeCanvas, self).UpdateDrawing()

        if not self.microscope_view:
            return
        now = time.time()
        if (self._lastThumbnailUpdate + self._thumbnailUpdatePeriod) < now:
            self._updateThumbnail()
            self._lastThumbnailUpdate = now

    # TODO use rate limiting decorator
    def _updateThumbnail(self):
        # TODO avoid doing 2 copies, by using directly the wxImage from the
        # result of the StreamTree

        # new bitmap to copy the DC
        bitmap = wx.EmptyBitmap(*self.ClientSize)
        dc = wx.MemoryDC()
        dc.SelectObject(bitmap)

        # simplified version of OnPaint()
        margin = ((self._bmp_buffer_size[0] - self.ClientSize[0])/2,
                  (self._bmp_buffer_size[1] - self.ClientSize[1])/2)

        dc.BlitPointSize((0, 0), self.ClientSize, self._dc_buffer, margin)

        # close the DC, to be sure the bitmap can be used safely
        del dc

        self.microscope_view.thumbnail.value = wx.ImageFromBitmap(bitmap)

    def _onStagePos(self, value):
        """
        When the stage is moved: recenter the view
        value: dict with "x" and "y" entries containing meters
        """
        # this can be caused by any viewport which has requested to recenter
        # the buffer
        pos = (value["x"] / self.mpwu, value["y"] / self.mpwu)
        # self.ReCenterBuffer(pos)
        # skip ourself, to avoid asking the stage to move to (almost) the same
        #position
        wx.CallAfter(super(DblMicroscopeCanvas, self).ReCenterBuffer, pos)

    def ReCenterBuffer(self, pos):
        """
        Update the position of the buffer on the world
        pos (2-tuple float): the coordinates of the center of the buffer in
                             fake units
        """
        super(DblMicroscopeCanvas, self).ReCenterBuffer(pos)

        # TODO: check it works fine
        if not self.microscope_view:
            return
        new_pos = self.requested_world_pos
        physical_pos = (new_pos[0] * self.mpwu, new_pos[1] * self.mpwu)
        # this should be done even when dragging
        self.microscope_view.view_pos.value = physical_pos

        self.microscope_view.moveStageToView()
        # stage_pos will be updated once the move is completed

    def _onMPP(self, mpp):
        """
        Called when the view.mpp is updated
        """
        self.scale = self.mpwu / mpp
        wx.CallAfter(self.ShouldUpdateDrawing)

    @microscope_view_check
    def Zoom(self, inc):
        """
        Zoom by the given factor
        inc (float): scale the current view by 2^inc
        ex:  # 1 => *2 ; -1 => /2; 2 => *4...
        """
        scale = 2.0 ** inc
        # Clip within the range
        mpp = self.microscope_view.mpp.value / scale
        mpp = sorted(self.microscope_view.mpp.range + (mpp,))[1]

        # FIXME: seems to crash when the mpp is very low (1 px = the whole
        # screen)
        # maybe in the zooming function?

        self.microscope_view.mpp.value = mpp # this will call _onMPP()

    # Zoom/merge management
    def OnWheel(self, event):
        change = event.GetWheelRotation() / event.GetWheelDelta()
        if event.ShiftDown():
            change *= 0.2 # softer

        if event.CmdDown(): # = Ctrl on Linux/Win or Cmd on Mac
            ratio = self.microscope_view.merge_ratio.value + (change * 0.1)
            # clamp
            ratio = sorted(self.microscope_view.merge_ratio.range + (ratio,))[1]
            self.microscope_view.merge_ratio.value = ratio
        else:
            self.Zoom(change)

    @microscope_view_check
    def onExtraAxisMove(self, axis, shift):
        """
        called when the extra dimensions are modified (right drag)
        axis (0<int): the axis modified
            0 => X
            1 => Y
        shift (int): relative amount of pixel moved
            >0: toward up/right
        """

        if self.microscope_view.get_focus(axis) is not None:
            # conversion: 1 unit => 0.1 μm (so a whole screen, ~44000u, is a
            # couple of mm)
            # TODO this should be adjusted by the lens magnification:
            # the higher the magnification, the smaller is the change
            # (=> proportional ?)
            # negative == go up == closer from the sample
            val = 0.1e-6 * shift # m
            assert(abs(val) < 0.01) # a move of 1 cm is a clear sign of bug

            self.queueMoveFocus(axis, val)

    def queueMoveFocus(self, axis, shift, period = 0.1):
        """
        Move the focus, but at most every period, to avoid accumulating
        many slow small moves.
        axis (0,1): axis/focus number
        shift (float): distance of the focus move
        period (second): maximum time to wait before it will be moved
        """
        # update the complete move to do
        with self._moveFocusLock:
            self._moveFocusDistance[axis] += shift

        # start the timer if not yet started
        timer = [self._moveFocus0Timer, self._moveFocus1Timer][axis]
        if not timer.IsRunning():
            timer.Start(period * 1000.0, oneShot=True)

    def _moveFocus0(self):
        with self._moveFocusLock:
            shift = self._moveFocusDistance[0]
            self._moveFocusDistance[0] = 0
        logging.debug("Moving focus0 by %f μm", shift * 1e6)
        self.microscope_view.get_focus(0).moveRel({"z": shift})

    def _moveFocus1(self):
        with self._moveFocusLock:
            shift = self._moveFocusDistance[1]
            self._moveFocusDistance[1] = 0
        logging.debug("Moving focus1 by %f μm", shift * 1e6)
        self.microscope_view.get_focus(1).moveRel({"z": shift})

class SecomCanvas(DblMicroscopeCanvas):

    def __init__(self, *args, **kwargs):
        super(SecomCanvas, self).__init__(*args, **kwargs)

        self.zoom_overlay = ViewSelectOverlay(self, "Zoom")
        self.ViewOverlays.append(self.zoom_overlay)

        self.update_overlay = WorldSelectOverlay(self, "Update")
        self.WorldOverlays.append(self.update_overlay)

        self.active_overlay = None

        pub.subscribe(self.toggle_zoom_mode, 'secom.tool.zoom.click')
        pub.subscribe(self.toggle_update_mode, 'secom.tool.update.click')
        pub.subscribe(self.on_zoom_start, 'secom.canvas.zoom.start')

        self.cursor = wx.STANDARD_CURSOR

    def _toggle_mode(self, enabled, overlay, mode):
        if self.current_mode == mode and not enabled:
            self.current_mode = None
            self.active_overlay = None
            self.cursor = wx.STANDARD_CURSOR
            self.zoom_overlay.clear_selection()
            self.ShouldUpdateDrawing()
        elif not self.dragging and enabled:
            self.current_mode = mode
            self.active_overlay = overlay
            self.cursor = wx.StockCursor(wx.CURSOR_CROSS)

        self.SetCursor(self.cursor)

    def toggle_zoom_mode(self, enabled):
        logging.debug("Zoom mode %s", self)
        self._toggle_mode(enabled, self.zoom_overlay, MODE_SECOM_ZOOM)

    def toggle_update_mode(self, enabled):
        logging.debug("Update mode %s", self)
        self._toggle_mode(enabled, self.update_overlay, MODE_SECOM_UPDATE)

    def on_zoom_start(self, canvas):
        """ If a zoom selection starts, all previous selections should be
        cleared.
        """
        if canvas != self:
            self.zoom_overlay.clear_selection()
            self.ShouldUpdateDrawing()

    def OnLeftDown(self, event):
        # If one of the Secom tools is activated...
        if self.current_mode in SECOM_MODES:
            pos = event.GetPosition()
            hover = self.active_overlay.is_hovering(pos)

            # Clicked outside selection
            if not hover:
                self.dragging = True
                self.active_overlay.start_selection(pos, self.scale)
                pub.sendMessage('secom.canvas.zoom.start', canvas=self)
                if not self.HasCapture():
                    self.CaptureMouse()
            # Clicked on edge
            elif hover != gui.HOVER_SELECTION:
                self.dragging = True
                self.active_overlay.start_edit(pos, hover)
                if not self.HasCapture():
                    self.CaptureMouse()
            # Clicked inside selection
            elif self.current_mode == MODE_SECOM_ZOOM:
                self.dragging = False
                if self.HasCapture():
                    self.ReleaseMouse()

            self.ShouldUpdateDrawing()

        else:
            DraggableCanvas.OnLeftDown(self, event)

    def OnLeftUp(self, event):
        if self.current_mode in SECOM_MODES:
            if self.dragging:
                self.dragging = False
                # Stop both selection and edit
                self.active_overlay.stop_selection()
                if self.HasCapture():
                    self.ReleaseMouse()
            else:
                print "ZOOM! ZOOM!"
                self.active_overlay.clear_selection()
                pub.sendMessage('secom.canvas.zoom.end')

            self.ShouldUpdateDrawing()
        else:
            DraggableCanvas.OnLeftUp(self, event)

    def OnMouseMotion(self, event):
        if self.current_mode in SECOM_MODES and self.active_overlay:
            pos = event.GetPosition()

            if self.dragging:
                if self.active_overlay.dragging:
                    self.active_overlay.update_selection(pos)
                else:
                    self.active_overlay.update_edit(pos)
                self.ShouldUpdateDrawing()
                #self.Draw(wx.PaintDC(self))
            else:
                hover = self.active_overlay.is_hovering(pos)
                if hover == gui.HOVER_SELECTION:
                    self.SetCursor(wx.StockCursor(wx.CURSOR_MAGNIFIER))
                elif hover in (gui.HOVER_LEFT_EDGE, gui.HOVER_RIGHT_EDGE):
                    self.SetCursor(wx.StockCursor(wx.CURSOR_SIZEWE))
                elif hover in (gui.HOVER_TOP_EDGE, gui.HOVER_BOTTOM_EDGE):
                    self.SetCursor(wx.StockCursor(wx.CURSOR_SIZENS))
                else:
                    self.SetCursor(self.cursor)

        else:
            DraggableCanvas.OnMouseMotion(self, event)

    # Capture onwanted events when a tool is active.

    def OnWheel(self, event):
        if self.current_mode not in SECOM_MODES:
            super(SecomCanvas, self).OnWheel(event)

    def OnRightDown(self, event):
        if self.current_mode not in SECOM_MODES:
            super(SecomCanvas, self).OnRightDown(event)

    def OnRightUp(self, event):
        if self.current_mode not in SECOM_MODES:
            super(SecomCanvas, self).OnRightUp(event)

    def setView(self, microscope_view):
        """
        Set the microscope_view that this canvas is displaying/representing
        Can be called only once, at initialisation.

        :param microscope_view:(instrmodel.MicroscopeView)
        """
        super(SecomCanvas, self).setView(microscope_view)
        self.microscope_view.show_crosshair.subscribe(
                                            self._onCrossHair,
                                            init=True
        )

    def _onCrossHair(self, activated):
        """ Activate or disable the display of a cross in the middle of the view
        activated = true if the cross should be displayed
        """
        # We don't specifically know about the crosshair, so look for it in the
        # static overlays
        ch = None
        for o in self.ViewOverlays:
            if isinstance(o, CrossHairOverlay):
                ch = o
                break

        if activated:
            if not ch:
                ch = CrossHairOverlay(self)
                self.ViewOverlays.append(ch)
                self.Refresh(eraseBackground=False)
        else:
            if ch:
                self.ViewOverlays.remove(ch)
                self.Refresh(eraseBackground=False)

class SparcAcquiCanvas(DblMicroscopeCanvas):
    def __init__(self, *args, **kwargs):
        super(SparcAcquiCanvas, self).__init__(*args, **kwargs)

        self.roi_overlay = WorldSelectOverlay(self, "Region of Interst")
        self.WorldOverlays.append(self.roi_overlay)

        self.active_overlay = None

        pub.subscribe(self.toggle_select_mode, 'sparc.acq.tool.select.click')

        self.cursor = wx.STANDARD_CURSOR

    def _toggle_mode(self, enabled, overlay, mode):
        if self.current_mode == mode and not enabled:
            self.current_mode = None
            self.active_overlay = None
            self.cursor = wx.STANDARD_CURSOR
            self.ShouldUpdateDrawing()
        elif not self.dragging and enabled:
            if mode == MODE_SPARC_SELECT:
                self.roi_overlay.clear_selection()
                pub.sendMessage(
                    'sparc.acq.selection.changed',
                    region_of_interest=self.roi_overlay.get_world_selection_pos()
                )
                self.ShouldUpdateDrawing()
            self.current_mode = mode
            self.active_overlay = overlay
            self.cursor = wx.StockCursor(wx.CURSOR_CROSS)

        self.SetCursor(self.cursor)

    def toggle_select_mode(self, enabled):
        """ This method is called using pubsub, usually when a menu button is
        toggled. """
        logging.debug("Update mode %s", self)
        self._toggle_mode(enabled, self.roi_overlay, MODE_SPARC_SELECT)

    def OnLeftDown(self, event):

        # If one of the Sparc tools is activated...
        # current_mode is set through 'toggle_select_mode', which in
        # turn if activated by a pubsub event
        if self.current_mode in SPARC_MODES:
            pos = event.GetPosition()
            hover = self.active_overlay.is_hovering(pos)

            # Clicked outside selection
            if not hover:
                self.dragging = True
                self.active_overlay.start_selection(pos, self.scale)
                pub.sendMessage('sparc.acq.select.start', canvas=self)
                if not self.HasCapture():
                    self.CaptureMouse()
            # Clicked on edge
            elif hover != gui.HOVER_SELECTION:
                self.dragging = True
                self.active_overlay.start_edit(pos, hover)
                if not self.HasCapture():
                    self.CaptureMouse()

            self.ShouldUpdateDrawing()

        else:
            DraggableCanvas.OnLeftDown(self, event)

    def OnLeftUp(self, event):
        if self.current_mode in SPARC_MODES:
            if self.dragging:
                self.dragging = False
                # Stop both selection and edit
                self.active_overlay.stop_selection()
                if self.HasCapture():
                    self.ReleaseMouse()
                pub.sendMessage('sparc.acq.select.end')
                pub.sendMessage(
                    'sparc.acq.selection.changed',
                    region_of_interest=self.roi_overlay.get_world_selection_pos()
                )
            else:
                self.active_overlay.clear_selection()

            self.ShouldUpdateDrawing()
        else:
            DraggableCanvas.OnLeftUp(self, event)

    def OnMouseMotion(self, event):
        if self.current_mode in SPARC_MODES and self.active_overlay:
            pos = event.GetPosition()

            if self.dragging:
                if self.active_overlay.dragging:
                    self.active_overlay.update_selection(pos)
                else:
                    self.active_overlay.update_edit(pos)
                self.ShouldUpdateDrawing()
                #self.Draw(wx.PaintDC(self))
            else:
                hover = self.active_overlay.is_hovering(pos)
                if hover == gui.HOVER_SELECTION:
                    self.SetCursor(wx.StockCursor(wx.CURSOR_MAGNIFIER))
                elif hover in (gui.HOVER_LEFT_EDGE, gui.HOVER_RIGHT_EDGE):
                    self.SetCursor(wx.StockCursor(wx.CURSOR_SIZEWE))
                elif hover in (gui.HOVER_TOP_EDGE, gui.HOVER_BOTTOM_EDGE):
                    self.SetCursor(wx.StockCursor(wx.CURSOR_SIZENS))
                else:
                    self.SetCursor(self.cursor)

        else:
            DraggableCanvas.OnMouseMotion(self, event)

    # Capture onwanted events when a tool is active.

    def OnWheel(self, event):
        if self.current_mode not in SPARC_MODES:
            super(SparcAcquiCanvas, self).OnWheel(event)

    def OnRightDown(self, event):
        if self.current_mode not in SPARC_MODES:
            super(SparcAcquiCanvas, self).OnRightDown(event)

    def OnRightUp(self, event):
        if self.current_mode not in SPARC_MODES:
            super(SparcAcquiCanvas, self).OnRightUp(event)

    def setView(self, microscope_view):
        """
        Set the microscope_view that this canvas is displaying/representing
        Can be called only once, at initialisation.

        :param microscope_view:(instrmodel.MicroscopeView)
        """
        super(SparcAcquiCanvas, self).setView(microscope_view)
        self.microscope_view.show_crosshair.subscribe(self._onCrossHair, init=True)

    def _onCrossHair(self, activated):
        """ Activate or disable the display of a cross in the middle of the view
        activated = true if the cross should be displayed
        """
        # We don't specifically know about the crosshair, so look for it in the
        # static overlays
        ch = None
        for o in self.ViewOverlays:
            if isinstance(o, CrossHairOverlay):
                ch = o
                break

        if activated:
            if not ch:
                ch = CrossHairOverlay(self)
                self.ViewOverlays.append(ch)
                self.Refresh(eraseBackground=False)
        else:
            if ch:
                self.ViewOverlays.remove(ch)
                self.Refresh(eraseBackground=False)