# -*- coding: utf-8 -*-
"""
Created on 6 Feb 2012

@author: Éric Piel

Copyright © 2012-2013 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

from __future__ import division
from .comp.overlay import CrossHairOverlay, ViewSelectOverlay, \
    WorldSelectOverlay, TextViewOverlay
from decorator import decorator
from odemis import util, model
from odemis.gui import instrmodel
from odemis.gui.model import EM_STREAMS, stream
from odemis.gui.model.stream import UNDEFINED_ROI
from odemis.gui.util import limit_invocation, call_after, units, ignore_dead
from odemis.model._vattributes import VigilantAttributeBase
import odemis.gui.comp.overlay as overlay
from wx.lib.pubsub import pub
import logging
import odemis.gui as gui
import odemis.gui.comp.canvas as canvas
import threading
import wx

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

class DblMicroscopeCanvas(canvas.DraggableCanvas):
    """ A draggable, flicker-free window class adapted to show pictures of two
    microscope simultaneously.

    It knows size and position of what is represented in a picture and display
    the pictures accordingly.

    It also provides various typical overlays (ie, drawings) for microscope views.
    """
    def __init__(self, *args, **kwargs):
        canvas.DraggableCanvas.__init__(self, *args, **kwargs)
        self.microscope_view = None
        self._microscope_model = None

        self.Bind(wx.EVT_MOUSEWHEEL, self.OnWheel)

        # TODO: If it's too resource consuming, which might want to create just
        # our own thread
        # FIXME: "stop all axes" should also cancel the next timer
        self._moveFocusLock = threading.Lock()
        self._moveFocusDistance = [0, 0]
        # TODO: deduplicate!
        self._moveFocus0Timer = wx.PyTimer(self._moveFocus0)
        self._moveFocus1Timer = wx.PyTimer(self._moveFocus1)

        # Current (tool) mode. TODO: Make platform (secom/sparc) independant
        self.current_mode = None
        # meter per "world unit"
        self.mpwu = None

        # for the FPS
        self.fps_overlay = TextViewOverlay(self)
        self.ViewOverlays.append(self.fps_overlay)

    def setView(self, microscope_view, microscope_model):
        """
        Set the microscope_view that this canvas is displaying/representing
        Can be called only once, at initialisation.

        :param microscope_view:(instrmodel.MicroscopeView)
        :param microscope_model: (instrmodel.MicroscopeModel)
        """
        # This is a kind of kludge, see mscviewport.MicroscopeViewport for
        # details
        assert(self.microscope_view is None)

        self.microscope_view = microscope_view
        self._microscope_model = microscope_model

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
        self.microscope_view.lastUpdate.subscribe(self._onViewImageUpdate, init=True)

        # handle crosshair
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

    def _orderStreamsToImages(self, streams):
        """
        Create a list of each stream's image, ordered from the first one to
        be draw to the last one (topest).
        streams (list of Streams) the streams to order
        return (list of InstrumentalImage)
        """
        images = []
        for s in streams:
            if not s:
                # should not happen, but let's not completely fail on this
                logging.error("StreamTree has a None stream")
                continue

            if hasattr(s, "image"):
                iim = s.image.value
                if iim is None or iim.image is None:
                    continue

                images.append(iim)

        # Sort by size, so that the biggest picture is first drawn (no opacity)
        images.sort(
            lambda a, b: cmp(
                b.image.Height * b.image.Width * b.mpp if b else 0,
                a.image.Height * a.image.Width * a.mpp if a else 0
            )
        )

        return images

    def _convertStreamsToImages(self):
        """ Temporary function to convert the StreamTree to a list of images as
        the canvas currently expects.
        """
        streams = self.microscope_view.getStreams()
        # get the images, in order
        images = self._orderStreamsToImages(streams)

        # remove all the images (so they can be garbage collected)
        self.Images = [None]

        # add the images in order
        for i, iim in enumerate(images):
            if iim is None:
                continue
            scale = iim.mpp / self.mpwu
            pos = self.real_to_world_pos(iim.center)
            self.SetImage(i, iim.image, pos, scale)

        # set merge_ratio
        self.merge_ratio = self.microscope_view.stream_tree.kwargs.get("merge", 0.5)

    def _onViewImageUpdate(self, t):
        # TODO use the real streamtree functions
        # for now we call a conversion layer
        self._convertStreamsToImages()
        #logging.debug("Will update drawing for new image")
        wx.CallAfter(self.ShouldUpdateDrawing)

    def UpdateDrawing(self):
        # override just in order to detect when it's just finished redrawn

        # TODO: detect that the canvas is not visible, and so should no/less
        # frequently be updated?
        super(DblMicroscopeCanvas, self).UpdateDrawing()

        if not self.microscope_view:
            return

        self._updateThumbnail()

    @limit_invocation(2) # max 1/2s
    @call_after  # needed as it accesses the DC
    @ignore_dead  # This method might get called after the canvas is destroyed
    def _updateThumbnail(self):
        # TODO: avoid doing 2 copies, by using directly the wxImage from the
        # result of the StreamTree
        # logging.debug("Updating thumbnail with size = %s", self.ClientSize)

        csize = self.ClientSize
        if (csize[0] * csize[1]) <= 0:
            return # nothing to update

        # new bitmap to copy the DC
        bitmap = wx.EmptyBitmap(*self.ClientSize)
        dc = wx.MemoryDC()
        dc.SelectObject(bitmap)

        # simplified version of OnPaint()
        margin = ((self._bmp_buffer_size[0] - self.ClientSize[0]) // 2,
                  (self._bmp_buffer_size[1] - self.ClientSize[1]) // 2)

        dc.BlitPointSize((0, 0), self.ClientSize, self._dc_buffer, margin)

        # close the DC, to be sure the bitmap can be used safely
        del dc

        img = wx.ImageFromBitmap(bitmap)
        self.microscope_view.thumbnail.value = img

    def _onStagePos(self, value):
        """
        When the stage is moved: recenter the view
        value: dict with "x" and "y" entries containing meters
        """
        # this can be caused by any viewport which has requested to recenter
        # the buffer
        pos = self.real_to_world_pos((value["x"], value["y"]))
        # skip ourself, to avoid asking the stage to move to (almost) the same
        # position
        wx.CallAfter(super(DblMicroscopeCanvas, self).ReCenterBuffer, pos)

    def ReCenterBuffer(self, pos):
        """
        Update the position of the buffer on the world
        pos (2-tuple float): the coordinates of the center of the buffer in
                             fake units
        """
        # it will update self.requested_world_pos
        super(DblMicroscopeCanvas, self).ReCenterBuffer(pos)

        # TODO: check it works fine
        if not self.microscope_view:
            return
        physical_pos = self.world_to_real_pos(self.requested_world_pos)
        # this should be done even when dragging
        self.microscope_view.view_pos.value = physical_pos

        self.microscope_view.moveStageToView()
        # stage_pos will be updated once the move is completed

    def fitViewToContent(self, recenter=None):
        """
        Adapts the MPP and center to fit to the current content
        recenter (None or boolean): If True, also recenter the view. If None, it
         will try to be clever, and only recenter if no stage is connected, as
         otherwise, it could cause an unexpected move.
        """
        if recenter is None:
            # recenter only if there is no stage attached
            recenter = not hasattr(self.microscope_view, "stage_pos")
        canvas.DraggableCanvas.fitViewToContent(self, recenter=recenter)

        # this will indirectly call _onMPP(), but not have any additional effect
        if self.microscope_view and self.mpwu:
            self.microscope_view.mpp.value = self.mpwu / self.scale

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

    # @microscope_view_check
    def onExtraAxisMove(self, axis, shift):
        """
        called when the extra dimensions are modified (right drag)
        axis (int>0): the axis modified
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
            # logging.error("%s, %s", axis, shift)
            self.queueMoveFocus(axis, val)

    def queueMoveFocus(self, axis, shift, period=0.1):
        """ Move the focus, but at most every period, to avoid accumulating
        many slow small moves.

        axis (0|1): axis/focus number
            0 => X
            1 => Y
        shift (float): distance of the focus move
        period (second): maximum time to wait before it will be moved
        """
        # update the complete move to do
        with self._moveFocusLock:
            self._moveFocusDistance[axis] += shift
            # logging.debug(
            #         "Increasing focus mod with %s for axis %s set to %s",
            #         shift,
            #         axis,
            #         self._moveFocusDistance[axis])

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

    def world_to_real_pos(self, pos):
        phy_pos = tuple([v * self.mpwu for v in pos])
        return phy_pos

    def real_to_world_pos(self, phy_pos):
        """
        phy_pos (tuple of float): "physical" coordinates in m
        return (tuple of float)
        """
        world_pos = tuple([v / self.mpwu for v in phy_pos])
        return world_pos

    def selection_to_real_size(self, start_w_pos, end_w_pos):
        w = abs(start_w_pos[0] - end_w_pos[0]) * self.mpwu
        h = abs(start_w_pos[1] - end_w_pos[1]) * self.mpwu
        return w, h


    # Hook to update the FPS value
    def _DrawMergedImages(self, dc_buffer, images, mergeratio=0.5):
        fps = super(DblMicroscopeCanvas, self)._DrawMergedImages(dc_buffer,
                                                         images,
                                                         mergeratio)
        tlp = wx.GetTopLevelParent(self)

        # TODO: have an complete GUI model, which contains one VA .debug
        if hasattr(tlp, "menu_item_debug"):
            debug_mode = tlp.menu_item_debug.IsChecked()
            if debug_mode:
                self.fps_overlay.set_label("%d fps" % fps)
            else:
                self.fps_overlay.set_label("")

class SecomCanvas(DblMicroscopeCanvas):

    def __init__(self, *args, **kwargs):
        super(SecomCanvas, self).__init__(*args, **kwargs)

        self.zoom_overlay = ViewSelectOverlay(self, "Zoom")
        self.ViewOverlays.append(self.zoom_overlay)

        self.update_overlay = WorldSelectOverlay(self, "Update")
        self.WorldOverlays.append(self.update_overlay)

        self.active_overlay = None

        # TODO: use .tool as for SparcCanvas
        pub.subscribe(self.toggle_zoom_mode, 'secom.tool.zoom.click')
        pub.subscribe(self.toggle_update_mode, 'secom.tool.update.click')
        pub.subscribe(self.on_zoom_start, 'secom.canvas.zoom.start')

        self.cursor = wx.STANDARD_CURSOR

        # TODO: once the StreamTrees can render fully, reactivate the background
        # pattern
        self.backgroundBrush = wx.SOLID

    # Special version which put the SEM images first, as with the current
    # display mechanism in the canvas, the fluorescent images must be displayed
    # together last
    def _orderStreamsToImages(self, streams):
        """
        Create a list of each stream's image, ordered from the first one to
        be draw to the last one (topest).
        streams (list of Streams) the streams to order
        return (list of InstrumentalImage)
        """
        images = []
        has_sem_image = False
        for s in streams:
            if not s:
                # should not happen, but let's not completely fail on this
                logging.error("StreamTree has a None stream")
                continue

            if not hasattr(s, "image"):
                continue

            iim = s.image.value
            if iim is None or iim.image is None:
                continue

            if isinstance(s, EM_STREAMS):
                # as last
                images.append(iim)
                logging.debug("inserting SEM image")
                # FIXME: See the log warning
                if has_sem_image:
                    logging.warning(("Multiple SEM images are not handled "
                                     "correctly for now"))
                has_sem_image = True
            else:
                images.insert(0, iim) # as first
                logging.debug("inserting normal image")

        return images

    def add_world_overlay(self, wol):
        self.WorldOverlays.append(wol)

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
            vpos = event.GetPosition()
            hover = self.active_overlay.is_hovering(vpos)

            # Clicked outside selection
            if not hover:
                self.dragging = True
                self.active_overlay.start_selection(vpos, self.scale)
                pub.sendMessage('secom.canvas.zoom.start', canvas=self)
                if not self.HasCapture():
                    self.CaptureMouse()
            # Clicked on edge
            elif hover != gui.HOVER_SELECTION:
                self.dragging = True
                self.active_overlay.start_edit(vpos, hover)
                if not self.HasCapture():
                    self.CaptureMouse()
            # Clicked inside selection
            elif self.current_mode == MODE_SECOM_ZOOM:
                self.dragging = True
                self.active_overlay.start_drag(vpos)
                if not self.HasCapture():
                    self.CaptureMouse()

            self.ShouldUpdateDrawing()

        else:
            canvas.DraggableCanvas.OnLeftDown(self, event)

    def OnLeftUp(self, event):
        if self.current_mode in SECOM_MODES:
            if self.dragging:
                self.dragging = False
                # Stop selection, edit, or drag
                self.active_overlay.stop_selection()
                if self.HasCapture():
                    self.ReleaseMouse()
            else:
                # TODO: Put actual zoom function here
                self.active_overlay.clear_selection()
                pub.sendMessage('secom.canvas.zoom.end')

            self.ShouldUpdateDrawing()
        else:
            canvas.DraggableCanvas.OnLeftUp(self, event)

    def OnMouseMotion(self, event):
        if self.current_mode in SECOM_MODES and self.active_overlay:
            vpos = event.GetPosition()

            # TODO: Make a better, more natural between the different kinds
            # of dragging (edge vs whole selection)
            if self.dragging:
                if self.active_overlay.dragging:
                    self.active_overlay.update_selection(vpos)
                else:
                    if self.active_overlay.edit_edge:
                        self.active_overlay.update_edit(vpos)
                    else:
                        self.active_overlay.update_drag(vpos)
                self.ShouldUpdateDrawing()
                #self.Draw(wx.PaintDC(self))
            else:
                hover = self.active_overlay.is_hovering(vpos)
                if hover == gui.HOVER_SELECTION:
                    self.SetCursor(wx.StockCursor(wx.CURSOR_HAND))
                elif hover in (gui.HOVER_LEFT_EDGE, gui.HOVER_RIGHT_EDGE):
                    self.SetCursor(wx.StockCursor(wx.CURSOR_SIZEWE))
                elif hover in (gui.HOVER_TOP_EDGE, gui.HOVER_BOTTOM_EDGE):
                    self.SetCursor(wx.StockCursor(wx.CURSOR_SIZENS))
                else:
                    self.SetCursor(self.cursor)

        else:
            canvas.DraggableCanvas.OnMouseMotion(self, event)

    # Capture unwanted events when a tool is active.

    def OnWheel(self, event):
        if self.current_mode not in SECOM_MODES:
            super(SecomCanvas, self).OnWheel(event)

    def OnRightDown(self, event):
        # If we're currently not performing an action...
        if self.current_mode not in SECOM_MODES:
            # Note: Set the cursor before the super method is called.
            # There probably is a Ubuntu/wxPython related bug that
            # SetCursor does not work one CaptureMouse is called (which)
            # happens in the super method.
            num_focus = self.microscope_view.get_focus_count()
            if num_focus == 1:
                logging.debug("One focus actuator found")
                self.SetCursor(wx.StockCursor(wx.CURSOR_SIZENS))
            elif num_focus == 2:
                logging.debug("Two focus actuators found")
                self.SetCursor(wx.StockCursor(wx.CURSOR_CROSS))

            super(SecomCanvas, self).OnRightDown(event)


    def OnRightUp(self, event):
        if self.current_mode not in SECOM_MODES:
            super(SecomCanvas, self).OnRightUp(event)


class SparcAcquiCanvas(DblMicroscopeCanvas):
    def __init__(self, *args, **kwargs):
        super(SparcAcquiCanvas, self).__init__(*args, **kwargs)

        self._roa = None # The ROI VA of SEM CL stream, initialized on setView()
        self.roi_overlay = WorldSelectOverlay(self, "Region of acquisition")
        self.WorldOverlays.append(self.roi_overlay)
        self.active_overlay = None
        self.cursor = wx.STANDARD_CURSOR

    def setView(self, microscope_view, microscope_model):
        """
        Set the microscope_view that this canvas is displaying/representing
        Can be called only once, at initialisation.

        :param microscope_view:(instrmodel.MicroscopeView)
        :param microscope_model: (instrmodel.MicroscopeModel)
        """
        super(SparcAcquiCanvas, self).setView(microscope_view, microscope_model)

        # Associate the ROI of the SEM CL stream to the region of acquisition
        for s in microscope_model.acquisitionView.getStreams():
            if s.name.value == "SEM CL":
                self._roa = s.roi
                break
        else:
            raise KeyError("Failed to find SEM CL stream, required for the Sparc acquisition")

        self._roa.subscribe(self._onROA, init=True)

        sem = microscope_model.ebeam
        if not sem:
            raise AttributeError("No SEM on the microscope")

        if isinstance(sem.magnification, VigilantAttributeBase):
            sem.magnification.subscribe(self._onSEMMag)

        microscope_model.tool.subscribe(self.onTool, init=True)

    def onTool(self, tool):
        """
        Called when the tool (mode) of the view changes
        """
        if self.dragging:
            logging.error("Changing to mode (%s) while dragging not implemented", tool)
            # TODO: queue it until dragging is finished?
            # Really? Why? I can't think of a scenario.

        if tool == instrmodel.TOOL_ROA:
            self.current_mode = MODE_SPARC_SELECT
            self.active_overlay = self.roi_overlay
            self.cursor = wx.StockCursor(wx.CURSOR_CROSS)
        elif tool == instrmodel.TOOL_NONE:
            self.current_mode = None
            self.active_overlay = None
            self.cursor = wx.STANDARD_CURSOR
            self.ShouldUpdateDrawing()
        else:
            logging.warning("Unhandled tool type %s", tool)

        self.SetCursor(self.cursor)

    def OnLeftDown(self, event):

        # If one of the Sparc tools is activated...
        # current_mode is set through 'toggle_select_mode', which in
        # turn if activated by a pubsub event
        if self.current_mode in SPARC_MODES:
            vpos = event.GetPosition()
            hover = self.active_overlay.is_hovering(vpos)

            # Clicked outside selection
            if not hover or hover == gui.HOVER_SELECTION:
                self.dragging = True
                self.active_overlay.start_selection(vpos, self.scale)
                if not self.HasCapture():
                    self.CaptureMouse()
            # Clicked on edge
            elif hover != gui.HOVER_SELECTION:
                self.dragging = True
                self.active_overlay.start_edit(vpos, hover)
                if not self.HasCapture():
                    self.CaptureMouse()
            self.ShouldUpdateDrawing()

        else:
            canvas.DraggableCanvas.OnLeftDown(self, event)

    def OnLeftUp(self, event):
        if self.current_mode in SPARC_MODES:
            if self.dragging:
                self.dragging = False
                # Stop both selection and edit
                self.active_overlay.stop_selection()
                if self.HasCapture():
                    self.ReleaseMouse()
                logging.debug("ROA = %s", self.roi_overlay.get_physical_sel())
                self._updateROA()
                # force it to redraw the selection, even if the ROA hasn't changed
                # because the selection is clipped identically
                self._onROA(self._roa.value)
            else:
                if self._roa:
                    self._roa.value = UNDEFINED_ROI

        else:
            canvas.DraggableCanvas.OnLeftUp(self, event)

    def OnMouseMotion(self, event):
        if self.current_mode in SPARC_MODES and self.active_overlay:
            vpos = event.GetPosition()

            if self.dragging:
                if self.active_overlay.dragging:
                    self.active_overlay.update_selection(vpos)
                else:
                    self.active_overlay.update_edit(vpos)
                self.ShouldUpdateDrawing()
                #self.Draw(wx.PaintDC(self))
            else:
                hover = self.active_overlay.is_hovering(vpos)
                if hover == gui.HOVER_SELECTION:
                    # No special cusor needed
                    self.SetCursor(self.cursor)
                elif hover in (gui.HOVER_LEFT_EDGE, gui.HOVER_RIGHT_EDGE):
                    self.SetCursor(wx.StockCursor(wx.CURSOR_SIZEWE))
                elif hover in (gui.HOVER_TOP_EDGE, gui.HOVER_BOTTOM_EDGE):
                    self.SetCursor(wx.StockCursor(wx.CURSOR_SIZENS))
                else:
                    self.SetCursor(self.cursor)

        else:
            canvas.DraggableCanvas.OnMouseMotion(self, event)

    # Capture unwanted events when a tool is active.

    def OnWheel(self, event):
        #if self.current_mode not in SPARC_MODES:
        super(SparcAcquiCanvas, self).OnWheel(event)

    def OnRightDown(self, event):
        if self.current_mode not in SPARC_MODES:
            super(SparcAcquiCanvas, self).OnRightDown(event)

    def OnRightUp(self, event):
        if self.current_mode not in SPARC_MODES:
            super(SparcAcquiCanvas, self).OnRightUp(event)

    def _onSEMMag(self, mag):
        """
        Called when the magnification of the SEM changes
        """
        # That means the pixelSize changes, so the (relative) ROA is different
        # Either we update the ROA so that physically it stays the same, or
        # we update the selection so that the ROA stays the same. It's probably
        # that the user has forgotten to set the magnification before, so let's
        # pick solution 2.
        self._onROA(self._roa.value)

    def _getSEMRect(self):
        """
        Returns the (theoretical) scanning area of the SEM. Works even if the
        SEM has not send any image yet.
        returns (tuple of 4 floats): position in m (t, l, b, r)
        raises AttributeError in case no SEM is found
        """
        sem = self._microscope_model.ebeam
        if not sem:
            raise AttributeError("No SEM on the microscope")

        try:
            sem_center = self.microscope_view.stage_pos.value
        except AttributeError:
            # no stage => pos is always 0,0
            sem_center = (0, 0)
        # TODO: pixelSize will be updated when the SEM magnification changes,
        # so we might want to recompute this ROA whenever pixelSize changes so
        # that it's always correct (but maybe not here in the view)
        sem_width = (sem.shape[0] * sem.pixelSize.value[0],
                     sem.shape[1] * sem.pixelSize.value[1])
        sem_rect = [sem_center[0] - sem_width[0] / 2, # top
                    sem_center[1] - sem_width[1] / 2, # left
                    sem_center[0] + sem_width[0] / 2, # bottom
                    sem_center[1] + sem_width[1] / 2] # right

        return sem_rect

    def _updateROA(self):
        """
        Update the value of the ROA in the GUI according to the roi_overlay
        """
        sem = self._microscope_model.ebeam
        if not self._roa or not sem:
            logging.warning("ROA is supposed to be updated, but no ROA/SEM attribute")
            return

        # Get the position of the overlay in physical coordinates
        phys_rect = self.roi_overlay.get_physical_sel()
        if phys_rect is None:
            self._roa.value = UNDEFINED_ROI
            return

        # Position of the complete SEM scan in physical coordinates
        sem_rect = self._getSEMRect()

        # Take only the intersection so that that ROA is always inside the SEM scan
        phys_rect = util.rect_intersect(phys_rect, sem_rect)
        if phys_rect is None:
            self._roa.value = UNDEFINED_ROI
            return

        # Convert the ROI into relative value compared to the SEM scan
        rel_rect = [(phys_rect[0] - sem_rect[0]) / (sem_rect[2] - sem_rect[0]),
                    (phys_rect[1] - sem_rect[1]) / (sem_rect[3] - sem_rect[1]),
                    (phys_rect[2] - sem_rect[0]) / (sem_rect[2] - sem_rect[0]),
                    (phys_rect[3] - sem_rect[1]) / (sem_rect[3] - sem_rect[1])]

        # and is at least one pixel big
        rel_pixel_size = (1 / sem.shape[0], 1 / sem.shape[1])
        rel_rect[2] = max(rel_rect[2], rel_rect[0] + rel_pixel_size[0])
        if rel_rect[2] > 1: # if went too far
            rel_rect[0] -= rel_rect[2] - 1
            rel_rect[2] = 1
        rel_rect[3] = max(rel_rect[3], rel_rect[1] + rel_pixel_size[1])
        if rel_rect[3] > 1:
            rel_rect[1] -= rel_rect[3] - 1
            rel_rect[3] = 1

        # update roa
        self._roa.value = rel_rect

    def _onROA(self, roi):
        """
        Called when the ROI of the SEM CL is updated (that's our region of
         acquisition).
        roi (tuple of 4 floats): top, left, bottom, right position relative to
          the SEM image
        """
        if roi == UNDEFINED_ROI:
            phys_rect = None
        else:
            # convert relative position to physical position
            try:
                sem_rect = self._getSEMRect()
            except AttributeError:
                return # no SEM => ROA is not meaningful

            phys_rect = (sem_rect[0] + roi[0] * (sem_rect[2] - sem_rect[0]),
                         sem_rect[1] + roi[1] * (sem_rect[3] - sem_rect[1]),
                         sem_rect[0] + roi[2] * (sem_rect[2] - sem_rect[0]),
                         sem_rect[1] + roi[3] * (sem_rect[3] - sem_rect[1]))

        logging.debug("Selection now set to %s", phys_rect)
        self.roi_overlay.set_physical_sel(phys_rect)
        wx.CallAfter(self.ShouldUpdateDrawing)

class SparcAlignCanvas(DblMicroscopeCanvas):
    """
    Special restricted version that displays the first stream always fitting
    the entire canvas.
    """

    def __init__(self, *args, **kwargs):
        super(SparcAlignCanvas, self).__init__(*args, **kwargs)
        self._ccd_mpp = None # tuple of 2 floats of m/px

    def setView(self, microscope_view, microscope_model):
        DblMicroscopeCanvas.setView(self, microscope_view, microscope_model)
        # find the MPP of the sensor and use it on all images
        try:
            self._ccd_mpp = self.microscope_view.ccd.pixelSize.value
        except AttributeError:
            logging.info("Failed to find CCD for Sparc mirror alignment")

    def _convertStreamsToImages(self):
        """
        Same as the overridden method, but ensures the goal image keeps the alpha
        and is displayed second. Also force the mpp to be the one of the sensor.
        """
        # remove all the images (so they can be garbage collected)
        self.Images = [None]

        streams = self.microscope_view.getStreams()

        # All the images must be displayed with the same mpp (modulo the binning)
        if self._ccd_mpp:
            mpp = self._ccd_mpp[0]
        else:
            # use the most relevant mpp from an image
            for s in streams:
                if s and not isinstance(s, stream.StaticStream):
                    try:
                        mpp = s.image.mpp
                        break
                    except AttributeError:
                        pass
            else:
                mpp = 13e-6 # sensible fallback


        # order and display the images
        for s in streams:
            if not s:
                # should not happen, but let's not completely fail on this
                logging.error("StreamTree has a None stream")
                continue

            if not hasattr(s, "image"):
                continue
            iim = s.image.value
            if iim is None or iim.image is None:
                continue

            # see if image was obtained with some binning
            try:
                binning = s.raw[0].metadata[model.MD_BINNING][0]
            except (AttributeError, IndexError):
                binning = 1

            scale = mpp * binning / self.mpwu
            pos = (0, 0) # the sensor image should be centered on the sensor center

            if isinstance(s, stream.StaticStream):
                # StaticStream == goal image => add at the end
                self.SetImage(len(self.Images), iim.image, pos, scale, keepalpha=True)
            else:
                # add at the beginning
                self.SetImage(0, iim.image, pos, scale)

        # set merge_ratio
        self.merge_ratio = self.microscope_view.stream_tree.kwargs.get("merge", 1)

        # TODO: check if image size has changed and if yes, refit to AR image (first)
        self.fitViewToContent(recenter=True)


    def OnSize(self, event):
        DblMicroscopeCanvas.OnSize(self, event)
        # TODO: refit image
        self.fitViewToContent(recenter=True)

    # Disable zoom/move by doing nothing for mouse/keys
    # Don't directly override OnWheel to still allow merge ratio change
    def Zoom(self, inc):
        pass

    def OnLeftDown(self, event):
        event.Skip()

    def OnLeftUp(self, event):
        event.Skip()

    def OnRightDown(self, event):
        event.Skip()

    def OnRightUp(self, event):
        event.Skip()

    def OnDblClick(self, event):
        event.Skip()

    def OnChar(self, event):
        event.Skip()

# TODO: change name?

class ZeroDimensionalPlotCanvas(canvas.PlotCanvas):
    """ A plotable canvas with a vertical 'focus line', that shows the x and y
    values of the selected position.
    """

    def __init__(self, *args, **kwargs):

        # These attributes need to be assigned before the super constructor
        # is called, because they are used in the OnSize event handler.
        self.current_y_value = None
        self.current_x_value = None

        super(ZeroDimensionalPlotCanvas, self).__init__(*args, **kwargs)

        self.unit_x = None
        self.unit_y = None

        self.dragging = False

        ## Overlays

        self.focusline_overlay = None
        # List of all overlays used by this canvas
        self.overlays = []

        self.SetBackgroundColour(self.Parent.BackgroundColour)
        self.SetForegroundColour(self.Parent.ForegroundColour)

        self.closed = canvas.PLOT_CLOSE_BOTTOM
        self.plot_mode = canvas.PLOT_MODE_BAR
        self.ticks = canvas.PLOT_TICKS_HORZ

        self.set_focusline_ovelay(overlay.FocusLineOverlay(self))

        ## Event binding

        self.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)
        self.Bind(wx.EVT_LEFT_UP, self.OnLeftUp)
        self.Bind(wx.EVT_MOTION, self.OnMouseMotion)
        # self.Bind(wx.EVT_SIZE, self.OnSize)

    # Event handlers

    def OnLeftDown(self, event):
        self.dragging = True
        self.drag_init_pos = event.GetPositionTuple()

        logging.debug("Drag started at %s", self.drag_init_pos)

        if not self.HasCapture():
            self._position_focus_line(event)
            self.CaptureMouse()

        self.SetFocus()
        event.Skip()

    def OnLeftUp(self, event):
        self.dragging = False
        self.SetCursor(wx.STANDARD_CURSOR)
        if self.HasCapture():
            self.ReleaseMouse()
        event.Skip()

    def OnMouseMotion(self, event):
        if self.dragging and self.focusline_overlay:
            self._position_focus_line(event)
        event.Skip()

    def OnSize(self, event):  #pylint: disable=W0222
        """ Update the position of the focus line """
        super(ZeroDimensionalPlotCanvas, self).OnSize(event)
        if None not in (self.current_x_value, self.current_y_value):
            pos = (self._val_x_to_pos_x(self.current_x_value),
                   self._val_y_to_pos_y(self.current_y_value))
            self.focusline_overlay.set_position(pos)

    def _position_focus_line(self, event):
        """ Position the focus line at the position of the given mouse event """

        if not self._data:
            return

        x, _ = event.GetPositionTuple()
        self.current_x_value = self._pos_x_to_val_x(x)
        self.current_y_value = self._val_x_to_val_y(self.current_x_value)
        pos = (x, self._val_y_to_pos_y(self.current_y_value))

        label = "%s" % units.readable_str(self.current_y_value, self.unit_y, 3)

        # TODO: find a more elegant way to link the legend.
        if hasattr(self.Parent, 'legend'):
            self.Parent.legend.set_label(label, x)
            self.Parent.legend.Refresh()

        #self.focusline_overlay.set_label(label)
        self.focusline_overlay.set_position(pos)
        self.Refresh()

    def OnPaint(self, event=None):
        wx.BufferedPaintDC(self, self._bmp_buffer)
        dc = wx.PaintDC(self)

        for o in self.overlays:
            o.Draw(dc)

    def set_focusline_ovelay(self, fol):
        """ Assign a focusline overlay to the canvas """
        # TODO: Add type check to make sure the ovelay is a ViewOverlay.
        # (But importing Viewoverlay causes cyclic imports)
        self.focusline_overlay = fol
        self.add_overlay(fol)
        self.Refresh()

    def add_overlay(self, ol):
        self.overlays.append(ol)

    def get_y_value(self):
        """ Return the current y value """
        return self.current_y_value

    def set_x_unit(self, unit):
        self.unit_x = unit

    def set_y_unit(self, unit):
        self.unit_y = unit