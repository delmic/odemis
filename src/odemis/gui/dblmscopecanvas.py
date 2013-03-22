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
import cairo
from wx.lib.pubsub import pub
from decorator import decorator

from odemis.gui import FOREGROUND_COLOUR_EDIT
from odemis.gui.comp.canvas import DraggableCanvas, WorldToBufferPoint
from odemis.gui.model import EM_STREAMS
from odemis.gui.util.conversion import hex_to_rgba


CROSSHAIR_COLOR = wx.GREEN
CROSSHAIR_SIZE = 16

SELECTION_COLOR = FOREGROUND_COLOUR_EDIT

# Various modes canvas elements can go into.

MODE_SECOM_ZOOM = 1
MODE_SECOM_UPDATE = 2

SECOM_MODES = (MODE_SECOM_ZOOM, MODE_SECOM_UPDATE)

MODE_SPARC_SELECT = 3
MODE_SPARC_PICK = 4

SPARC_MODES = (MODE_SPARC_SELECT, MODE_SPARC_PICK)

HOVER_TOP_EDGE = 1
HOVER_RIGHT_EDGE = 2
HOVER_BOTTOM_EDGE = 3
HOVER_LEFT_EDGE = 4
HOVER_SELECTION = 5

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
        # This is a kind of kludge, see mscviewport.MicroscopeViewport for details
        assert(self.microscope_view is None)

        self.microscope_view = microscope_view

        # meter per "world unit"
        # for conversion between "world pos" in the canvas and a real unit
        # mpp == mpwu => 1 world coord == 1 px => scale == 1
        self.mpwu = self.microscope_view.mpp.value  #m/wu
        # Should not be changed!
        # FIXME!! => have a PhyscicalCanvas which directly use physical units

        self.microscope_view.mpp.subscribe(self._onMPP)

        # TODO subscribe to view_pos to synchronize with the other views
        # TODO subscribe to stage_pos as well/instead.
        if hasattr(self.microscope_view, "stage_pos"):
            self.microscope_view.stage_pos.subscribe(self._onStagePos, init=True)

        # any image changes
        microscope_view.lastUpdate.subscribe(self._onViewImageUpdate, init=True)


    def _convertStreamsToImages(self):
        """
        Temporary function to convert the StreamTree to a list of images as the canvas
          currently expects.
        """
        streams = self.microscope_view.streams.streams

        # create a list of of each stream's image, but re-ordered so that SEM is first
        images = []
        has_sem_image = False
        for i, s in enumerate(streams):
            if not s:
                # should not happen, but let's not completely fail on this
                logging.error("StreamTree has a None stream")
                continue

            iim = s.image.value
            if iim is None or iim.image is None:
                continue

            if isinstance(s, EM_STREAMS):
                # as first
                images.insert(0, iim)
                if has_sem_image:
                    logging.warning("Multiple SEM images are not handled correctly for now")
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
        self.merge_ratio = self.microscope_view.streams.kwargs.get("merge", 0.5)

    def _onViewImageUpdate(self, t):
        # TODO use the real streamtree functions
        # for now we call a conversion layer
        self._convertStreamsToImages()
        logging.debug("Will update drawing for new image")
        wx.CallAfter(self.ShouldUpdateDrawing)

    def UpdateDrawing(self):
        # override just in order to detect when it's just finished redrawn

        # TODO: detect that the canvas is not visible, and so should no/less frequently
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
        # this can be caused by any viewport which has requested to recenter the buffer
        pos = (value["x"] / self.mpwu, value["y"] / self.mpwu)
        # self.ReCenterBuffer(pos)
        # skip ourself, to avoid asking the stage to move to (almost) the same position
        wx.CallAfter(super(DblMicroscopeCanvas, self).ReCenterBuffer, pos)

    def ReCenterBuffer(self, pos):
        """
        Update the position of the buffer on the world
        pos (2-tuple float): the coordinates of the center of the buffer in fake units
        """
        super(DblMicroscopeCanvas, self).ReCenterBuffer(pos)

        # TODO check it works fine
        if not self.microscope_view:
            return
        new_pos = self.world_pos_requested
        physical_pos = (new_pos[0] * self.mpwu, new_pos[1] * self.mpwu)
        self.microscope_view.view_pos.value = physical_pos # this should be done even when dragging

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

        # FIXME: seems to crash when the mpp is very low (1 px = the whole screen)
        # maybe in the zooming function?

        self.microscope_view.mpp.value = mpp # this will call _onMPP()

    # Zoom/merge management
    def OnWheel(self, event):
        change = event.GetWheelRotation() / event.GetWheelDelta()
        if event.ShiftDown():
            change *= 0.2 # softer

        if event.CmdDown(): # = Ctrl on Linux/Win or Cmd on Mac
            ratio = self.microscope_view.merge_ratio.value + (change * 0.1)
            ratio = sorted(self.microscope_view.merge_ratio.range + (ratio,))[1] # clamp
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

        #focus = [self.microscope_view.focus0, self.microscope_view.focus1][axis]
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

        self.zoom_overlay = SelectionOverlay(self, "Zoom")
        self.ViewOverlays.append(self.zoom_overlay)

        self.update_overlay = SelectionOverlay(self, "Update")
        self.WorldOverlays.append(self.update_overlay)

        self.active_overlay = None

        pub.subscribe(self.toggle_zoom_mode, 'secom.tool.zoom.click')
        pub.subscribe(self.toggle_update_mode, 'secom.tool.update.click')
        pub.subscribe(self.on_zoom_start, 'secom.canvas.zoom.select')

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
                self.active_overlay.start_selection(pos)
                pub.sendMessage('secom.canvas.zoom.select', canvas=self)
            # Clicked on edge
            elif hover != HOVER_SELECTION:
                self.dragging = True
                self.active_overlay.start_edit(pos, hover)
            # Clicked inside selection
            elif self.current_mode == MODE_SECOM_ZOOM:
                self.dragging = False

            self.ShouldUpdateDrawing()
        else:
            DraggableCanvas.OnLeftDown(self, event)

    def OnLeftUp(self, event):
        if self.current_mode in SECOM_MODES:
            if self.dragging:
                self.dragging = False
                # Stop both selection and edit
                self.active_overlay.stop_selection()
            else:
                print "ZOOM! ZOOM!"
                self.active_overlay.clear_selection()
                pub.sendMessage('secom.canvas.zoom.done')

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
                if hover == HOVER_SELECTION:
                    self.SetCursor(wx.StockCursor(wx.CURSOR_MAGNIFIER))
                elif hover in (HOVER_LEFT_EDGE, HOVER_RIGHT_EDGE):
                    self.SetCursor(wx.StockCursor(wx.CURSOR_SIZEWE))
                elif hover in (HOVER_TOP_EDGE, HOVER_BOTTOM_EDGE):
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
                ch = CrossHairOverlay(self, CROSSHAIR_COLOR, CROSSHAIR_SIZE)
                self.ViewOverlays.append(ch)
                self.Refresh(eraseBackground=False)
        else:
            if ch:
                self.ViewOverlays.remove(ch)
                self.Refresh(eraseBackground=False)

### Here come all the classes for drawing overlays

class Overlay(object):

    def __init__(self, base):
        """
        :param base: (DblMicroscopeCanvas) Canvas to which the overlay belongs
        """

        self.base = base

    def _clip_pos(self, pos):
        """ Return the given pos, clipped by the base's size if necessary """

        pos.x = max(1, min(pos.x, self.base.ClientSize.x - 1))
        pos.y = max(1, min(pos.y, self.base.ClientSize.y - 1))

        return pos


class CrossHairOverlay(Overlay):
    def __init__(self, base,
                 color=CROSSHAIR_COLOR, size=CROSSHAIR_SIZE, center=(0, 0)):
        super(CrossHairOverlay, self).__init__(base)

        self.pen = wx.Pen(color)
        self.size = size
        self.center = center

    def Draw(self, dc, shift=(0, 0), scale=1.0):
        """
        Draws the crosshair
        dc (wx.DC)
        shift (2-tuple float): shift for the coordinate conversion
        scale (float): scale for the coordinate conversion
        """
        dc.SetPen(self.pen)

        tl = (self.center[0] - self.size,
              self.center[1] - self.size)
        br = (self.center[0] + self.size,
              self.center[1] + self.size)
        tl_s = WorldToBufferPoint(tl, shift, scale)
        br_s = WorldToBufferPoint(br, shift, scale)
        center = WorldToBufferPoint(self.center, shift, scale)

        dc.DrawLine(tl_s[0], center[1], br_s[0], center[1])
        dc.DrawLine(center[0], tl_s[1], center[0], br_s[1])


class SelectionOverlay(Overlay):
    """ This overlay is for the selection of a rectangular arrea.
    """

    min_dim = 10 # Minimum selectino size (width and weight)
    hover_margin = 10 #px


    def __init__(self, base, label,
                 sel_cur=None, color=SELECTION_COLOR, center=(0, 0)):
        super(SelectionOverlay, self).__init__(base)

        self.label = label

        self.color = hex_to_rgba(color)
        self.center = center

        self.start_pos = None
        self.end_pos = None

        self.edit_start_pos = None
        self.edit_edge = None

        self.dragging = False
        self.edit = False

        # Dictionary containing values for the inner and outer edges
        self.edges = {}

    # Creating a new selection

    def start_selection(self, start_pos):
        """ Start a new selection.

        :param start_pos: (wx.Point) Pixel coordinates where the selection
            starts
        """

        logging.debug("Starting selection at %s", start_pos)
        self.start_pos = self.end_pos = start_pos
        self.dragging = True

    def update_selection(self, current_pos):
        """ Update the selection to reflect the given mouse position.

        :param current_pos: (wx.Point) Pixel coordinates of the current end
            point
        """

        current_pos = self._clip_pos(current_pos)

        logging.debug("Updating selection to %s", current_pos)
        self.end_pos = current_pos

    def stop_selection(self):
        """ End the creation of the current selection """

        logging.debug("Stopping selection")

        if max(self.get_height(), self.get_width()) < self.min_dim:
            logging.debug("Selection too small")
            self.clear_selection()
        else:
            self._calc_edges()
            self.dragging = False
            self.edit = False

    def clear_selection(self):
        """ Clear the selection """
        logging.debug("Clearing selections")
        self.dragging = False
        self.edit = False

        self.start_pos = None
        self.end_pos = None

        self.edges = {}


    # Edit existing selection

    def start_edit(self, start_pos, edge):
        """ Start an edit to the current selection """
        logging.debug("Starting edit of edge %s at %s", edge, start_pos)
        self.edit_start_pos = start_pos
        self.edit_edge = edge
        self.edit = True

    def update_edit(self, current_pos):
        """ Adjust the selection according to the given position and the current
        edit action
        """
        current_pos = self._clip_pos(current_pos)

        logging.debug("Moving selection to %s", current_pos)

        if self.edit_edge in (HOVER_TOP_EDGE, HOVER_BOTTOM_EDGE):
            if self.edit_edge == HOVER_TOP_EDGE:
                self.start_pos.y = current_pos.y
            else:
                self.end_pos.y = current_pos.y
        else:
            if self.edit_edge == HOVER_LEFT_EDGE:
                self.start_pos.x = current_pos.x
            else:
                self.end_pos.x = current_pos.x

    def stop_edit(self):
        """ End the selection edit """
        self.stop_selection()

    def _calc_edges(self):
        """ Calculate the inner and outer edges of the selection according to
        the hover margin
        """

        l, r = sorted([self.start_pos.x, self.end_pos.x])
        t, b = sorted([self.start_pos.y, self.end_pos.y])

        i_l, o_r, i_t, o_b = [v + self.hover_margin for v in [l, r, t, b]]
        o_l, i_r, o_t, i_b = [v - self.hover_margin for v in [l, r, t, b]]

        self.edges = {
            "i_l": i_l,
            "o_r": o_r,
            "i_t": i_t,
            "o_b": o_b,
            "o_l": o_l,
            "i_r": i_r,
            "o_t": o_t,
            "i_b": i_b
        }

    def is_hovering(self, pos):  #pylint: disable=R0911
        """ Check if the given position is on/near a selection edge or inside
        the selection.

        :return: (bool) Return False if not hovering, or the type of hover
        """

        if self.edges:
            if not self.edges["o_l"] < pos.x < self.edges["o_r"] or \
                not self.edges["o_t"] < pos.y < self.edges["o_b"]:
                return False
            elif self.edges["i_l"] < pos.x < self.edges["i_r"] and \
                self.edges["i_t"] < pos.y < self.edges["i_b"]:
                logging.debug("Selection hover")
                return HOVER_SELECTION
            elif pos.x < self.edges["i_l"]:
                logging.debug("Left edge hover")
                return HOVER_LEFT_EDGE
            elif pos.x > self.edges["i_r"]:
                logging.debug("Right edge hover")
                return HOVER_RIGHT_EDGE
            elif pos.y < self.edges["i_t"]:
                logging.debug("Top edge hover")
                return HOVER_TOP_EDGE
            elif pos.y > self.edges["i_b"]:
                logging.debug("Bottom edge hover")
                return HOVER_BOTTOM_EDGE

        return False

    def get_width(self):
        return abs(self.start_pos.x - self.end_pos.x)

    def get_height(self):
        return abs(self.start_pos.y - self.end_pos.y)

    def get_size(self):
        return (self.get_width(), self.get_height())

    def Draw(self, dc, shift=(0, 0), scale=1.0):
        if self.start_pos and self.end_pos:
            logging.debug("Drawing from %s, %s to %s. %s", self.start_pos.x,
                                                           self.start_pos.y,
                                                           self.end_pos.x,
                                                           self.end_pos.y )
            ctx = wx.lib.wxcairo.ContextFromDC(dc)
            ctx.select_font_face(
                "Courier",
                cairo.FONT_SLANT_NORMAL,
                cairo.FONT_WEIGHT_NORMAL
            )
            ctx.set_font_size(12)

            ctx.set_line_width(1.5)
            ctx.set_source_rgba(0, 0, 0, 1)
            ctx.rectangle(
                self.start_pos.x + 0.5,
                self.start_pos.y + 0.5,
                self.end_pos.x - self.start_pos.x + 0,
                self.end_pos.y - self.start_pos.y + 0
            )

            ctx.stroke()

            ctx.set_line_width(1)
            ctx.set_dash([1.5,])
            ctx.set_line_join(cairo.LINE_JOIN_MITER)

            ctx.set_source_rgba(*self.color)
            ctx.rectangle(self.start_pos.x + 0.5,
                               self.start_pos.y + 0.5,
                               self.end_pos.x - self.start_pos.x,
                               self.end_pos.y - self.start_pos.y)
                               #self.end_pos.x,
                               #self.end_pos.y) # Rectangle(x0, y0, x1, y1)
            ctx.stroke()

            if self.dragging:
                ctx.set_source_rgb(0.0, 0.0, 0.0)
                ctx.move_to(9, 19)
                ctx.show_text("{} {}".format(self.label, self.end_pos))
                ctx.set_source_rgb(1.0, 1.0, 1.0)
                ctx.move_to(10, 20)
                ctx.show_text("{} {}".format(self.label, self.end_pos))

