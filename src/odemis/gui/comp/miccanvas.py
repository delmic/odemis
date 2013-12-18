# -*- coding: utf-8 -*-
"""
Created on 6 Feb 2012

@author: Éric Piel

Copyright © 2012-2013 Éric Piel, Delmic

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

from __future__ import division

from decorator import decorator
import logging
from odemis import util, model
from odemis.gui.comp.canvas import CAN_ZOOM, CAN_MOVE, CAN_FOCUS
from odemis.gui.model import stream
from odemis.gui.model.stream import UNDEFINED_ROI, EM_STREAMS
from odemis.gui.util import limit_invocation, call_after, units, ignore_dead
from odemis.model._vattributes import VigilantAttributeBase
import threading
import wx
from wx.lib.pubsub import pub

import odemis.gui as gui
import odemis.gui.comp.canvas as canvas
import odemis.gui.comp.overlay as comp_overlay
import odemis.gui.model as guimodel


# Various modes canvas elements can go into.
# TODO: directly use the TOOL_* values
MODE_SECOM_ZOOM = guimodel.TOOL_ZOOM
MODE_SECOM_UPDATE = guimodel.TOOL_ROI
MODE_SECOM_DICHO = guimodel.TOOL_DICHO

SECOM_MODES = (MODE_SECOM_ZOOM, MODE_SECOM_UPDATE)

MODE_SPARC_SELECT = guimodel.TOOL_ROA
MODE_SPARC_PICK = guimodel.TOOL_POINT

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

    Public attributes:
    .abilities (set of CAN_*): features/restrictions allowed to be performed
    .fitViewToNextImage (Boolean): False by default. If True, next time an image
      is received, it will ensure the whole content fits the view (and reset
      this flag).
    """
    def __init__(self, *args, **kwargs):
        canvas.DraggableCanvas.__init__(self, *args, **kwargs)
        self.microscope_view = None
        self._tab_data_model = None

        self.abilities |= set([CAN_ZOOM, CAN_FOCUS])
        self.fitViewToNextImage = True

        # TODO: If it's too resource consuming, which might want to create just
        # our own thread. cf model.stream.histogram
        # FIXME: "stop all axes" should also cancel the next timer
        self._moveFocusLock = threading.Lock()
        self._moveFocusDistance = [0, 0]
        # TODO: deduplicate!
        self._moveFocus0Timer = wx.PyTimer(self._moveFocus0)
        self._moveFocus1Timer = wx.PyTimer(self._moveFocus1)

        # Current (tool) mode. TODO: Make platform (secom/sparc) independent
        # and use listen to .tool (cf SparcCanvas)
        self.current_mode = None
        # None (all allowed) or a set of guimodel.TOOL_* allowed (rest is treated like NONE)
        self.allowedModes = None

        # meter per "world unit"
        # for conversion between "world pos" in the canvas and a real unit
        # mpp == mpwu => 1 world coord == 1 px => scale == 1
        self.mpwu = 1 # m/wu
        # This is a const, don't change at runtime!
        # FIXME: turns out to be useless. => Need to directly use physical
        # coordinates. Currently, the only difference is that Y in going up in
        # physical coordinates and down in world coordinates.

        self._previous_size = None

        self.cursor = wx.STANDARD_CURSOR

        # Some more overlays
        self._crosshair_ol = None
        self._spotmode_ol = None
        self._fps_ol = comp_overlay.TextViewOverlay(self)
        self.focus_overlay = None
        self.roi_overlay = None
        self.point_overlay = None
        self.points_overlay = None

        # play/pause icon
        self.icon_overlay = comp_overlay.StreamIconOverlay(self)
        self.view_overlays.append(self.icon_overlay)

        self.zoom_overlay = comp_overlay.ViewSelectOverlay(self, "Zoom")
        self.view_overlays.append(self.zoom_overlay)

        self.update_overlay = comp_overlay.WorldSelectOverlay(self, "Update")
        self.world_overlays.append(self.update_overlay)

    def setView(self, microscope_view, tab_data):
        """
        Set the microscope_view that this canvas is displaying/representing
        Can be called only once, at initialisation.

        :param microscope_view:(model.MicroscopeView)
        :param tab_data: (model.MicroscopyGUIData)
        """
        # This is a kind of kludge, see mscviewport.MicroscopeViewport for
        # details
        assert(self.microscope_view is None)

        self.microscope_view = microscope_view
        self._tab_data_model = tab_data

        self.focus_overlay = None

        if self.microscope_view.get_focus_count():
            self.focus_overlay = comp_overlay.FocusOverlay(self)
            self.view_overlays.append(self.focus_overlay)

        self.microscope_view.mpp.subscribe(self._onMPP, init=True)

        if tab_data.tool:
            # If required, create a DichotomyOverlay
            if guimodel.TOOL_DICHO in tab_data.tool.choices:
                self.dicho_overlay = comp_overlay.DichotomyOverlay(self,
                                                     tab_data.dicho_seq)
                self.view_overlays.append(self.dicho_overlay)

            # If required, create a PointSelectOverlay
            if guimodel.TOOL_POINT in tab_data.tool.choices:
                self.point_overlay = comp_overlay.PointSelectOverlay(self)
                self.world_overlays.append(self.point_overlay)
                self.points_overlay = comp_overlay.PointsOverlay(self)
                self.world_overlays.append(self.points_overlay)

        if hasattr(self.microscope_view, "stage_pos"):
            # TODO: should this be moved to MicroscopeView, to update view_pos
            # when needed?
            # Listen to stage pos, so that all views move together when the
            # stage moves.
            self.microscope_view.stage_pos.subscribe(self._onStagePos)
        self.microscope_view.view_pos.subscribe(self._onViewPos, init=True)

        # any image changes
        self.microscope_view.lastUpdate.subscribe(self._onViewImageUpdate, init=True)

        # handle crosshair
        self.microscope_view.show_crosshair.subscribe(self._onCrossHair, init=True)

        tab_data.main.debug.subscribe(self._onDebug, init=True)

        if tab_data.tool:
            tab_data.tool.subscribe(self._onTool, init=True)

    def _onTool(self, tool):
        """
        Called when the tool (mode) of the view changes
        """
        if self._ldragging:
            logging.error("Changing to mode (%s) while dragging not implemented", tool)
            # TODO: queue it until dragging is finished?
            # Really? Why? I can't think of a scenario.

        # filter the tool mode if needed:
        if self.allowedModes is not None:
            if tool not in self.allowedModes:
                tool = guimodel.TOOL_NONE

        # TODO: send a .enable/.disable to overlay when becoming the active one
        if self.current_mode == MODE_SECOM_DICHO:
            self.dicho_overlay.enable(False)
        elif self.current_mode == guimodel.TOOL_SPOT:
            self._showSpotMode(False)

        # TODO: fix with the rest of the todos
        if self.point_overlay:
            self.point_overlay.enable(False)
            self.points_overlay.enable(False)

        # TODO: one mode <-> one overlay (type)
        # TODO: create the overlay on the fly, the first time it's requested
        if tool == guimodel.TOOL_ROA:
            self.current_mode = MODE_SPARC_SELECT
            self.active_overlay = self.roi_overlay
            self.cursor = wx.StockCursor(wx.CURSOR_CROSS)
        elif tool == guimodel.TOOL_POINT:
            # Enable the Spectrum point select overlay when a spectrum stream
            # is attached to the view
            if (self.point_overlay and
                    self.microscope_view.stream_tree.spectrum_streams):
                self.current_mode = MODE_SPARC_PICK
                self.active_overlay = self.point_overlay
                self.point_overlay.enable(True)
            # Enable the Angular Resolve point select overlay when there's a
            # AR stream known anywhere in the data model (and the view has
            # streams).
            elif (self.points_overlay and
                  len(self.microscope_view.stream_tree) and
                  any([isinstance(s, stream.AR_STREAMS) for s
                       in self._tab_data_model.streams.value])):
                self.current_mode = MODE_SPARC_PICK
                self.active_overlay = self.points_overlay
                self.points_overlay.enable(True)
        elif tool == guimodel.TOOL_ROI:
            self.current_mode = MODE_SECOM_UPDATE
            self.active_overlay = self.update_overlay
            self.cursor = wx.StockCursor(wx.CURSOR_CROSS)
        elif tool == guimodel.TOOL_ZOOM:
            self.current_mode = MODE_SECOM_ZOOM
            self.active_overlay = self.zoom_overlay
            self.cursor = wx.StockCursor(wx.CURSOR_CROSS)
        elif tool == guimodel.TOOL_DICHO:
            self.current_mode = MODE_SECOM_DICHO
            self.active_overlay = self.dicho_overlay
            #FIXME: cursor handled by .enable()
            # self.cursor = wx.StockCursor(wx.CURSOR_HAND)
            self.dicho_overlay.enable(True)
        elif tool == guimodel.TOOL_SPOT:
            self.current_mode = tool
            # the only thing the view does is to indicate the mode
            self._showSpotMode(True)
        elif tool == guimodel.TOOL_NONE:
            self.current_mode = None
            self.active_overlay = None
            self.cursor = wx.STANDARD_CURSOR
            self.request_drawing_update()
        else:
            logging.warning("Unhandled tool type %s", tool)

        self.SetCursor(self.cursor)

    def _onCrossHair(self, activated):
        """ Activate or disable the display of a cross in the middle of the view
        activated = true if the cross should be displayed
        """
        if activated:
            if self._crosshair_ol is None:
                self._crosshair_ol = comp_overlay.CrossHairOverlay(self)

            if self._crosshair_ol not in self.view_overlays:
                self.view_overlays.append(self._crosshair_ol)
                self.Refresh(eraseBackground=False)
        else:
            try:
                self.view_overlays.remove(self._crosshair_ol)
                self.Refresh(eraseBackground=False)
            except ValueError:
                pass # it was already not displayed

    def _showSpotMode(self, activated=True):
        if activated:
            if self._spotmode_ol is None:
                self._spotmode_ol = comp_overlay.SpotModeOverlay(self)

            if self._spotmode_ol not in self.view_overlays:
                self.view_overlays.append(self._spotmode_ol)
                self.Refresh(eraseBackground=False)
        else:
            try:
                self.view_overlays.remove(self._spotmode_ol)
                self.Refresh(eraseBackground=False)
            except ValueError:
                pass # it was already not displayed


    # FIXME: seems like it might still be called while the Canvas has been
    # destroyed
    # => need to make sure that the object is garbage collected (= no more
    # references) once it's not used. (Or explicitly unsubscribe??)
    @ignore_dead
    def _onDebug(self, activated):
        """
        Called when GUI debug mode changes => display FPS overlay
        """
        if activated:
            if self._fps_ol not in self.view_overlays:
                self.view_overlays.append(self._fps_ol)
                self.Refresh(eraseBackground=False)
        else:
            try:
                self.view_overlays.remove(self._fps_ol)
                self.Refresh(eraseBackground=False)
            except ValueError:
                pass # it was already not displayed

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
        self.images = [None]

        # add the images in order
        for i, iim in enumerate(images):
            if iim is None:
                continue
            scale = iim.mpp / self.mpwu
            pos = self.physical_to_world_pos(iim.center)
            self.set_image(i, iim.image, pos, scale)

        # set merge_ratio
        self.merge_ratio = self.microscope_view.stream_tree.kwargs.get("merge", 0.5)

    def _onViewImageUpdate(self, t):
        # TODO use the real streamtree functions
        # for now we call a conversion layer
        self._convertStreamsToImages()
        if self.fitViewToNextImage and filter(bool, self.images):
            self.fit_view_to_content()
            self.fitViewToNextImage = False
        #logging.debug("Will update drawing for new image")
        wx.CallAfter(self.request_drawing_update)

    def update_drawing(self):
        # override just in order to detect when it's just finished redrawn

        # TODO: detect that the canvas is not visible, and so should no/less
        # frequently be updated? The difficulty is that it must be redrawn as
        # soon as it's shown again.
        super(DblMicroscopeCanvas, self).update_drawing()

        if self.microscope_view:
            self._updateThumbnail()

    @limit_invocation(2) # max 1/2 Hz
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

        # simplified version of on_paint()
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
        pos = self.physical_to_world_pos((value["x"], value["y"]))
        # skip ourself, to avoid asking the stage to move to (almost) the same
        # position
        wx.CallAfter(super(DblMicroscopeCanvas, self).recenter_buffer, pos)

    def _onViewPos(self, phy_pos):
        """
        When the view position is updated: recenter the view
        phy_pos (tuple of 2 float): X/Y in physical coordinates (m)
        """
        pos = self.physical_to_world_pos(phy_pos)
        # skip ourself, to avoid asking the stage to move to (almost) the same
        # position
        wx.CallAfter(super(DblMicroscopeCanvas, self).recenter_buffer, pos)

    def recenter_buffer(self, world_pos):
        """
        Update the position of the buffer on the world
        pos (2-tuple float): the coordinates of the center of the buffer in
                             fake units
        """
        # in case we are not attached to a view yet (shouldn't happen)
        if not self.microscope_view:
            logging.debug("recenter_buffer called without microscope view")
            super(DblMicroscopeCanvas, self).recenter_buffer(world_pos)
        else:
            self._calc_bg_offset(world_pos)
            self.requested_world_pos = world_pos
            physical_pos = self.world_to_physical_pos(world_pos)
            # This will call _onViewPos() -> recenter_buffer()
            self.microscope_view.view_pos.value = physical_pos

            self.microscope_view.moveStageToView() # will do nothing if no stage
            # stage_pos will be updated once the move is completed

    def fit_view_to_content(self, recenter=None):
        """ Adapts the MPP and center to fit to the current content

        recenter (None or boolean): If True, also recenter the view. If None, it
            will try to be clever, and only recenter if no stage is connected,
            as otherwise, it could cause an unexpected move.
        """
        if recenter is None:
            # recenter only if there is no stage attached
            recenter = not hasattr(self.microscope_view, "stage_pos")

        super(DblMicroscopeCanvas, self).fit_view_to_content(recenter=recenter)

        # this will indirectly call _onMPP(), but not have any additional effect
        if self.microscope_view:
            new_mpp = self.mpwu / self.scale
            rng_mpp = self.microscope_view.mpp.range
            new_mpp = max(rng_mpp[0], min(new_mpp, rng_mpp[1]))
            self.microscope_view.mpp.value = new_mpp

    def _onMPP(self, mpp):
        """ Called when the view.mpp is updated
        """
        self.scale = self.mpwu / mpp
        wx.CallAfter(self.request_drawing_update)

    def on_size(self, event):
        new_size = event.Size

        # Update the mpp, so that the same width is displayed
        if self._previous_size and self.microscope_view:
            hfw = self._previous_size[0] * self.microscope_view.mpp.value
            self.microscope_view.mpp.value = hfw / new_size[0]

        super(DblMicroscopeCanvas, self).on_size(event)
        self._previous_size = new_size

    @microscope_view_check
    def Zoom(self, inc, block_on_zero=False):
        """
        Zoom by the given factor
        inc (float): scale the current view by 2^inc
        block_on_zero (boolean): if True, and the zoom goes from software
          downscaling to software upscaling, it will stop at no software scaling
        ex:  # 1 => *2 ; -1 => /2; 2 => *4...
        """
        scale = 2.0 ** inc
        prev_mpp = self.microscope_view.mpp.value
        # Clip within the range
        mpp = prev_mpp / scale

        if block_on_zero:
            # Check for every image
            for s in self.microscope_view.stream_tree.getStreams():
                try:
                    im_mpp = s.image.value.mpp
                    # did we just passed the image mpp (=zoom zero)?
                    if ((prev_mpp < im_mpp < mpp or prev_mpp > im_mpp > mpp) and
                        abs(prev_mpp - im_mpp) > 1e-15): # for float error
                        mpp = im_mpp
                except AttributeError:
                    pass

        mpp = sorted(self.microscope_view.mpp.range + (mpp,))[1]
        self.microscope_view.mpp.value = mpp # this will call _onMPP()

    # Zoom/merge management
    def on_wheel(self, evt):
        change = evt.GetWheelRotation() / evt.GetWheelDelta()
        if evt.ShiftDown():
            change *= 0.2 # softer

        if evt.CmdDown(): # = Ctrl on Linux/Win or Cmd on Mac
            ratio = self.microscope_view.merge_ratio.value + (change * 0.1)
            # clamp
            ratio = sorted(self.microscope_view.merge_ratio.range + (ratio,))[1]
            self.microscope_view.merge_ratio.value = ratio
        else:
            if CAN_ZOOM in self.abilities:
                self.Zoom(change, block_on_zero=evt.ShiftDown())

        super(DblMicroscopeCanvas, self).on_wheel(evt)

    def on_char(self, evt):
        key = evt.GetKeyCode()

        if CAN_ZOOM in self.abilities:
            change = 1
            if evt.ShiftDown():
                block_on_zero = True
                change *= 0.2 # softer
            else:
                block_on_zero = False

            if key == ord("+"):
                self.Zoom(change, block_on_zero)
            elif key == ord("-"):
                self.Zoom(-change, block_on_zero)

        super(DblMicroscopeCanvas, self).on_char(evt)

    @microscope_view_check
    def on_extra_axis_move(self, axis, shift):
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

        if self.focus_overlay:
            self.focus_overlay.add_shift(shift, 0)
        logging.debug("Moving focus0 by %f μm", shift * 1e6)
        self.microscope_view.get_focus(0).moveRel({"z": shift})

    def _moveFocus1(self):
        with self._moveFocusLock:
            shift = self._moveFocusDistance[1]
            self._moveFocusDistance[1] = 0

        if self.focus_overlay:
            self.focus_overlay.add_shift(shift, 1)

        logging.debug("Moving focus1 by %f μm", shift * 1e6)
        self.microscope_view.get_focus(1).moveRel({"z": shift})

    def on_right_down(self, event):
        if CAN_FOCUS in self.abilities:
            # Note: Set the cursor before the super method is called.
            # There probably is a Ubuntu/wxPython related bug that
            # SetCursor does not work one CaptureMouse is called (which)
            # happens in the super method.
            if self.microscope_view:
                num_focus = self.microscope_view.get_focus_count()
                if num_focus == 1:
                    logging.debug("One focus actuator found")
                    self.SetCursor(wx.StockCursor(wx.CURSOR_SIZENS))
                elif num_focus == 2:
                    logging.debug("Two focus actuators found")
                    self.SetCursor(wx.StockCursor(wx.CURSOR_CROSS))
            if self.focus_overlay:
                self.focus_overlay.clear_shift()

        super(DblMicroscopeCanvas, self).on_right_down(event)

    def on_right_up(self, event):
        if self._rdragging:
            # Stop the timers, so there won't be any more focussing once the
            # button is released.
            for timer in [self._moveFocus0Timer, self._moveFocus1Timer]:
                if timer.IsRunning():
                    timer.Stop()
            if self.focus_overlay:
                self.focus_overlay.clear_shift()
        super(DblMicroscopeCanvas, self).on_right_up(event)

    # Y is opposite of our Y in computer (going up)
    def world_to_physical_pos(self, pos):
        """ Translate world coordinates into physical coordinates.

        Note: The y value needs to be flipped between world and physical
            coordinates.

        Note: If 'meters per world unit' (mpwu) is one, world and physical
            coordinates are the same.

        :param phy_pos: (float, float) "world" coordinates
        :return: (float, float)

        """

        phy_pos = (pos[0] * self.mpwu, -pos[1] * self.mpwu)
        return phy_pos

    def physical_to_world_pos(self, phy_pos):
        """ Translate physical coordinates into world coordinates.

        Note: The y value needs to be flipped between physical and world
            coordinates.

        Note: If 'meters per world unit' (mpwu) is one, world and physical
            coordinates are the same.

        :param phy_pos: (float, float) "physical" coordinates in m
        :return: (float, float)

        """
        world_pos = (phy_pos[0] / self.mpwu, -phy_pos[1] / self.mpwu)
        return world_pos

    def selection_to_real_size(self, start_w_pos, end_w_pos):
        w = abs(start_w_pos[0] - end_w_pos[0]) * self.mpwu
        h = abs(start_w_pos[1] - end_w_pos[1]) * self.mpwu
        return w, h

    # Hook to update the FPS value
    def _draw_merged_images(self, dc_buffer, images, mergeratio=0.5):
        fps = super(DblMicroscopeCanvas, self)._draw_merged_images(dc_buffer,
                                                         images,
                                                         mergeratio)
        self._fps_ol.set_label("%d fps" % fps)

class SecomCanvas(DblMicroscopeCanvas):

    def __init__(self, *args, **kwargs):
        super(SecomCanvas, self).__init__(*args, **kwargs)

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
                # logging.debug("inserting SEM image")
                # FIXME: See the log warning
                if has_sem_image:
                    logging.warning(("Multiple SEM images are not handled "
                                     "correctly for now"))
                has_sem_image = True
            else:
                images.insert(0, iim) # as first
                # logging.debug("inserting normal image")

        return images

    def on_left_down(self, event):
        # TODO: move this to the overlay
        # If one of the Secom tools is activated...
        if self.current_mode in SECOM_MODES:
            vpos = event.GetPositionTuple()
            hover = self.active_overlay.is_hovering(vpos)

            # Clicked outside selection
            if not hover:
                self._ldragging = True
                self.active_overlay.start_selection(vpos)
                pub.sendMessage('secom.canvas.zoom.start', canvas=self)
                if not self.HasCapture():
                    self.CaptureMouse()
            # Clicked on edge
            elif hover != gui.HOVER_SELECTION:
                self._ldragging = True
                self.active_overlay.start_edit(vpos, hover)
                if not self.HasCapture():
                    self.CaptureMouse()
            # Clicked inside selection
            elif self.current_mode == MODE_SECOM_ZOOM:
                self._ldragging = True
                self.active_overlay.start_drag(vpos)
                if not self.HasCapture():
                    self.CaptureMouse()

            self.request_drawing_update()

        else:
            super(SecomCanvas, self).on_left_down(event)


    def on_left_up(self, event):
        if self.current_mode in SECOM_MODES:
            if self._ldragging:
                self._ldragging = False
                # Stop selection, edit, or drag
                self.active_overlay.stop_selection()
                if self.HasCapture():
                    self.ReleaseMouse()
            else:
                # TODO: Put actual zoom function here
                self.active_overlay.clear_selection()
                pub.sendMessage('secom.canvas.zoom.end')

            self.request_drawing_update()
        else:
            super(SecomCanvas, self).on_left_up(event)

    def on_motion(self, event):
        if self.current_mode in SECOM_MODES and self.active_overlay:
            vpos = event.GetPositionTuple()

            # TODO: Make a better, more natural between the different kinds
            # of dragging (edge vs whole selection)
            if self._ldragging:
                if self.active_overlay.dragging:
                    self.active_overlay.update_selection(vpos)
                else:
                    if self.active_overlay.edit_edge:
                        self.active_overlay.update_edit(vpos)
                    else:
                        self.active_overlay.update_drag(vpos)
                self.request_drawing_update()
                #self.draw(wx.PaintDC(self))
            else:
                hover = self.active_overlay.is_hovering(vpos)
                if hover == gui.HOVER_SELECTION:
                    self.SetCursor(wx.StockCursor(wx.CURSOR_SIZENESW)) # A closed hand!
                elif hover in (gui.HOVER_LEFT_EDGE, gui.HOVER_RIGHT_EDGE):
                    self.SetCursor(wx.StockCursor(wx.CURSOR_SIZEWE))
                elif hover in (gui.HOVER_TOP_EDGE, gui.HOVER_BOTTOM_EDGE):
                    self.SetCursor(wx.StockCursor(wx.CURSOR_SIZENS))
                else:
                    self.SetCursor(self.cursor)
        else:
            super(SecomCanvas, self).on_motion(event)

    # Capture unwanted events when a tool is active.

    def on_wheel(self, event):
        if self.current_mode not in SECOM_MODES:
            super(SecomCanvas, self).on_wheel(event)

    def on_right_down(self, event):
        # If we're currently not performing an action...
        if self.current_mode not in SECOM_MODES:
            super(SecomCanvas, self).on_right_down(event)

    def on_right_up(self, event):
        if self.current_mode not in SECOM_MODES:
            super(SecomCanvas, self).on_right_up(event)

class SparcAcquiCanvas(DblMicroscopeCanvas):
    def __init__(self, *args, **kwargs):
        super(SparcAcquiCanvas, self).__init__(*args, **kwargs)

        self._roa = None # The ROI VA of SEM CL stream, initialized on setView()
        self.roi_overlay = comp_overlay.RepetitionSelectOverlay(
                                                self, "Region of acquisition")
        self.world_overlays.append(self.roi_overlay)

    def setView(self, microscope_view, tab_data):
        """
        Set the microscope_view that this canvas is displaying/representing
        Can be called only once, at initialisation.

        :param microscope_view:(model.MicroscopeView)
        :param tab_data: (model.MicroscopyGUIData)
        """
        super(SparcAcquiCanvas, self).setView(microscope_view, tab_data)

        # Associate the ROI of the SEM CL stream to the region of acquisition
        for s in tab_data.acquisitionView.getStreams():
            if s.name.value == "SEM CL":
                self._roa = s.roi
                break
        else:
            raise KeyError("Failed to find SEM CL stream, required for the Sparc acquisition")

        # TODO: move this to the RepetitionSelectOverlay?
        self._roa.subscribe(self._onROA, init=True)

        sem = tab_data.main.ebeam
        if not sem:
            raise AttributeError("No SEM on the microscope")

        if isinstance(sem.magnification, VigilantAttributeBase):
            sem.magnification.subscribe(self._onSEMMag)

    # TODO: maybe should not be called directly, but should be a VA on the view
    # or the tab?
    def showRepetition(self, rep, style=None):
        """
        Change/display repetition on the ROA, if the ROA is displayed
        rep (None or tuple of 2 ints): if None, repetition is hidden
        style (overlay.FILL_*): type of repetition display
        """
        if rep is None:
            self.roi_overlay.fill = comp_overlay.FILL_NONE
        else:
            self.roi_overlay.fill = style
            self.roi_overlay.repetition = rep

        wx.CallAfter(self.request_drawing_update)

    def on_left_down(self, event):
        # If one of the Sparc tools is activated...
        # current_mode is set through 'toggle_select_mode', which in
        # turn if activated by a pubsub event
        if self.current_mode in SPARC_MODES:
            vpos = event.GetPositionTuple()
            hover = self.active_overlay.is_hovering(vpos)

            # Clicked outside selection
            if not hover:
                self._ldragging = True
                self.active_overlay.start_selection(vpos)
                if not self.HasCapture():
                    self.CaptureMouse()
            # Clicked on edge
            elif hover != gui.HOVER_SELECTION:
                self._ldragging = True
                self.active_overlay.start_edit(vpos, hover)
                if not self.HasCapture():
                    self.CaptureMouse()
            # Clicked inside selection
            elif self.current_mode == MODE_SPARC_SELECT:
                self._ldragging = True
                self.active_overlay.start_drag(vpos)
                if not self.HasCapture():
                    self.CaptureMouse()
            self.request_drawing_update()

        else:
            super(SparcAcquiCanvas, self).on_left_down(event)

    def on_left_up(self, event):
        if self.current_mode in SPARC_MODES:
            if self._ldragging:
                self._ldragging = False
                # Stop both selection and edit
                self.active_overlay.stop_selection()
                if self.HasCapture():
                    self.ReleaseMouse()
                self._updateROA()
                # force it to redraw the selection, even if the ROA hasn't changed
                # because the selection is clipped identically
                if self._roa:
                    self._onROA(self._roa.value)
            else:
                if self._roa:
                    self._roa.value = UNDEFINED_ROI

        else:
            super(SparcAcquiCanvas, self).on_left_up(event)

    def on_motion(self, event):
        if self.current_mode in SPARC_MODES and self.active_overlay:
            vpos = event.GetPositionTuple()

            if self._ldragging:
                if self.active_overlay.dragging:
                    self.active_overlay.update_selection(vpos)
                else:
                    if self.active_overlay.edit_edge:
                        self.active_overlay.update_edit(vpos)
                    else:
                        self.active_overlay.update_drag(vpos)
                self.request_drawing_update()
                #self.draw(wx.PaintDC(self))

            else:
                hover = self.active_overlay.is_hovering(vpos)
                if hover == gui.HOVER_SELECTION:
                    self.SetCursor(wx.StockCursor(wx.CURSOR_SIZENESW)) # A closed hand!
                elif hover in (gui.HOVER_LEFT_EDGE, gui.HOVER_RIGHT_EDGE):
                    self.SetCursor(wx.StockCursor(wx.CURSOR_SIZEWE))
                elif hover in (gui.HOVER_TOP_EDGE, gui.HOVER_BOTTOM_EDGE):
                    self.SetCursor(wx.StockCursor(wx.CURSOR_SIZENS))
                else:
                    self.SetCursor(self.cursor)

        else:
            super(SparcAcquiCanvas, self).on_motion(event)

    # Capture unwanted events when a tool is active.

    def on_wheel(self, event):
        #if self.current_mode not in SPARC_MODES:
        super(SparcAcquiCanvas, self).on_wheel(event)

    def on_right_down(self, event):
        if self.current_mode not in SPARC_MODES:
            super(SparcAcquiCanvas, self).on_right_down(event)

    def on_right_up(self, event):
        if self.current_mode not in SPARC_MODES:
            super(SparcAcquiCanvas, self).on_right_up(event)

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
        returns (tuple of 4 floats): position in physical coordinates m (l, t, b, r)
        raises AttributeError in case no SEM is found
        """
        sem = self._tab_data_model.main.ebeam
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
        sem_rect = [sem_center[0] - sem_width[0] / 2, # left
                    sem_center[1] - sem_width[1] / 2, # top
                    sem_center[0] + sem_width[0] / 2, # right
                    sem_center[1] + sem_width[1] / 2] # bottom

        return sem_rect

    def _updateROA(self):
        """
        Update the value of the ROA in the GUI according to the roi_overlay
        """
        try:
            sem = self._tab_data_model.main.ebeam
            if not self._roa or not sem:
                raise AttributeError()
        except AttributeError:
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
        # In physical coordinates Y goes up, but in ROI, Y goes down => "1-"
        rel_rect = [(phys_rect[0] - sem_rect[0]) / (sem_rect[2] - sem_rect[0]),
                    1 - (phys_rect[3] - sem_rect[1]) / (sem_rect[3] - sem_rect[1]),
                    (phys_rect[2] - sem_rect[0]) / (sem_rect[2] - sem_rect[0]),
                    1 - (phys_rect[1] - sem_rect[1]) / (sem_rect[3] - sem_rect[1])]

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

        # Update ROA. We need to unsubscribe to be sure we don't received
        # intermediary values as ROA is modified by the stream further on, and
        # VA don't ensure the notifications are in ordered.
        self._roa.unsubscribe(self._onROA)
        self._roa.value = rel_rect
        self._roa.subscribe(self._onROA, init=True)
        # FIXME: we receive both this value and the value updated by the stream

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

            # In physical coordinates Y goes up, but in ROI, Y goes down => "1-"
            phys_rect = (sem_rect[0] + roi[0] * (sem_rect[2] - sem_rect[0]),
                         sem_rect[1] + (1 - roi[3]) * (sem_rect[3] - sem_rect[1]),
                         sem_rect[0] + roi[2] * (sem_rect[2] - sem_rect[0]),
                         sem_rect[1] + (1 - roi[1]) * (sem_rect[3] - sem_rect[1]))

        self.roi_overlay.set_physical_sel(phys_rect)
        wx.CallAfter(self.request_drawing_update)

class SparcAlignCanvas(DblMicroscopeCanvas):
    """
    Special restricted version that displays the first stream always fitting
    the entire canvas.
    """

    def __init__(self, *args, **kwargs):
        super(SparcAlignCanvas, self).__init__(*args, **kwargs)
        self.abilities -= set([CAN_ZOOM, CAN_MOVE])
        self._ccd_mpp = None # tuple of 2 floats of m/px

    def setView(self, microscope_view, tab_data):
        DblMicroscopeCanvas.setView(self, microscope_view, tab_data)
        # find the MPP of the sensor and use it on all images
        try:
            self._ccd_mpp = self.tab_data.main.ccd.pixelSize.value #pylint: disable=E1101
        except AttributeError:
            logging.info("Failed to find CCD for Sparc mirror alignment")

    def _convertStreamsToImages(self):
        """
        Same as the overridden method, but ensures the goal image keeps the alpha
        and is displayed second. Also force the mpp to be the one of the sensor.
        """
        # remove all the images (so they can be garbage collected)
        self.images = [None]

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
                self.set_image(len(self.images), iim.image, pos, scale, keepalpha=True)
            else:
                # add at the beginning
                self.set_image(0, iim.image, pos, scale)

        # set merge_ratio
        self.merge_ratio = self.microscope_view.stream_tree.kwargs.get("merge", 1)

        # always refit to image (for the rare case it has changed size)
        self.fit_view_to_content(recenter=True)

    def on_size(self, event):
        DblMicroscopeCanvas.on_size(self, event)
        # refit image
        self.fit_view_to_content(recenter=True)


# TODO: change name?
class ZeroDimensionalPlotCanvas(canvas.PlotCanvas):
    """ A plotable canvas with a vertical 'focus line', that shows the x and y
    values of the selected position.
    """

    def __init__(self, *args, **kwargs):

        # These attributes need to be assigned before the super constructor
        # is called, because they are used in the on_size event handler.
        self.current_y_value = None
        self.current_x_value = None
        # FIXME: This attribute should be renamed to simply `view`, but that
        # would also  require renaming the `microscope_view` attributes of the
        # other Canvas classes.
        self.microscope_view = None
        self._tab_data_model = None

        super(ZeroDimensionalPlotCanvas, self).__init__(*args, **kwargs)

        self.unit_x = None
        self.unit_y = None

        self._ldragging = False

        ## Overlays

        # List of all overlays used by this canvas
        self.overlays = []

        self.SetBackgroundColour(self.Parent.BackgroundColour)
        self.SetForegroundColour(self.Parent.ForegroundColour)

        self.closed = canvas.PLOT_CLOSE_BOTTOM
        self.plot_mode = canvas.PLOT_MODE_BAR

        self.markline_overlay = comp_overlay.MarkingLineOverlay(self)
        self.add_overlay(self.markline_overlay)

    # Event handlers

    def on_left_down(self, evt):
        self._ldragging = True
        self.drag_init_pos = evt.GetPositionTuple()

        # logging.debug("Drag started at %s", self.drag_init_pos)

        if not self.HasCapture():
            self._position_focus_line(evt)
            self.CaptureMouse()

        self.SetFocus()

        super(ZeroDimensionalPlotCanvas, self).on_left_down(evt)

    def on_left_up(self, evt):
        self._ldragging = False
        self.SetCursor(wx.STANDARD_CURSOR)
        if self.HasCapture():
            self.ReleaseMouse()

        super(ZeroDimensionalPlotCanvas, self).on_left_up(evt)

    def on_motion(self, evt):
        if self._ldragging and self.markline_overlay:
            self._position_focus_line(evt)

        super(ZeroDimensionalPlotCanvas, self).on_motion(evt)

    def on_size(self, evt):  #pylint: disable=W0222
        """ Update the position of the focus line """

        super(ZeroDimensionalPlotCanvas, self).on_size(evt)

        if None not in (self.current_x_value, self.current_y_value):
            pos = (self._val_x_to_pos_x(self.current_x_value),
                   self._val_y_to_pos_y(self.current_y_value))
            self.markline_overlay.set_position(pos)

    def _position_focus_line(self, evt):
        """ Position the focus line at the position of the given mouse event """

        if not self._data:
            return

        x, _ = evt.GetPositionTuple()
        self.current_x_value = self._pos_x_to_val_x(x)
        self.current_y_value = self._val_x_to_val_y(self.current_x_value)
        pos = (x, self._val_y_to_pos_y(self.current_y_value))

        label = "%s"  % units.readable_str(
                                    self.current_x_value,
                                    self.unit_x,
                                    3)

        # TODO: find a more elegant way to link the legend.
        if hasattr(self.Parent, 'legend_panel'):
            self.Parent.legend_panel.set_label(label, x)
            self.Parent.legend_panel.Refresh()

        #self.markline_overlay.set_label(label)
        self.markline_overlay.set_position(pos)
        self.Refresh()

    def setView(self, microscope_view, tab_data):
        """Set the microscope_view that this canvas is displaying/representing
        Can be called only once, at initialisation.

        :param microscope_view:(model.MicroscopeView)
        :param tab_data: (model.MicroscopyGUIData)
        """
        # This is a kind of kludge, see mscviewport.MicroscopeViewport for
        # details
        assert(self.microscope_view is None)

        self.microscope_view = microscope_view
        self._tab_data_model = tab_data

    @limit_invocation(0.5) # max 1/2 Hz
    @call_after  # needed as it accesses the DC
    @ignore_dead  # This method might get called after the canvas is destroyed
    def _updateThumbnail(self):
        csize = self.ClientSize
        if (csize[0] * csize[1]) <= 0:
            return # nothing to update

        # new bitmap to copy the DC
        bitmap = wx.EmptyBitmap(*self.ClientSize)
        context = wx.ClientDC(self)

        dc = wx.MemoryDC()
        dc.SelectObject(bitmap)

        dc.BlitPointSize((0, 0), self.ClientSize, context, (0, 0))

        # close the DC, to be sure the bitmap can be used safely
        del dc

        img = wx.ImageFromBitmap(bitmap)
        self.microscope_view.thumbnail.value = img


    def on_paint(self, event=None):
        wx.BufferedPaintDC(self, self._bmp_buffer)
        dc = wx.PaintDC(self)

        for o in self.overlays:
            o.Draw(dc)

        if self.microscope_view:
            self._updateThumbnail()

    def add_overlay(self, ol):
        self.overlays.append(ol)

    def get_y_value(self):
        """ Return the current y value """
        return self.current_y_value

    def set_x_unit(self, unit):
        self.unit_x = unit

    def set_y_unit(self, unit):
        self.unit_y = unit

class AngularResolvedCanvas(canvas.DraggableCanvas):
    """ Angular resolved canvas
    """

    def __init__(self, *args, **kwargs):

        super(AngularResolvedCanvas, self).__init__(*args, **kwargs)

        self.microscope_view = None
        self._tab_data_model = None
        self.abilities -= set([CAN_MOVE])

        # self.backgroundBrush = wx.SOLID # background is always black

        ## Overlays

        self.polar_overlay = comp_overlay.PolarOverlay(self)
        self.view_overlays.append(self.polar_overlay)
        self.active_overlay = self.polar_overlay

    # Event handlers

    def on_size(self, evt):  #pylint: disable=W0222
        """ Called when the canvas is resized """
        self.fit_view_to_content()
        super(AngularResolvedCanvas, self).on_size(evt)

    def setView(self, microscope_view, tab_data):
        """Set the microscope_view that this canvas is displaying/representing
        Can be called only once, at initialisation.

        :param microscope_view:(model.MicroscopeView)
        :param tab_data: (model.MicroscopyGUIData)
        """
        # This is a kind of kludge, see mscviewport.MicroscopeViewport for
        # details
        assert(self.microscope_view is None)

        self.microscope_view = microscope_view
        self._tab_data_model = tab_data

        # any image changes
        self.microscope_view.lastUpdate.subscribe(self._onViewImageUpdate, init=True)

    # TODO: should be simplified
    def fit_view_to_content(self, recenter=None):
        """ Adapts the MPP to fit to the current content
        recenter: never used (it's always centered)
        """
        super(AngularResolvedCanvas, self).fit_view_to_content(recenter=True)

    def _getStreamsImages(self, streams):
        """
        Create a list of each stream's image
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

        return images

    def _convertStreamsToImages(self):
        """ Temporary function to convert the StreamTree to a list of images as
        the canvas currently expects.
        """
        # Normally the view.streamtree should have only one image anyway
        streams = self.microscope_view.getStreams()
        # get the images, in order
        images = self._getStreamsImages(streams)

        # remove all the images (so they can be garbage collected)
        self.images = [None]

        # add the images in order
        for i, iim in enumerate(images):
            if iim is None:
                continue
            # image is always centered, fitting the whole canvas
            self.set_image(i, iim.image, (0, 0), 1)

    def _onViewImageUpdate(self, t):
        self._convertStreamsToImages()
        self.fit_view_to_content()
        wx.CallAfter(self.request_drawing_update)

    def update_drawing(self):
        super(AngularResolvedCanvas, self).update_drawing()

        if self.microscope_view:
            self._updateThumbnail()

    @limit_invocation(0.5) # max 1/2 Hz
    @call_after  # needed as it accesses the DC
    @ignore_dead  # This method might get called after the canvas is destroyed
    def _updateThumbnail(self):
        csize = self.ClientSize
        if (csize[0] * csize[1]) <= 0:
            return # nothing to update

        # new bitmap to copy the DC
        bitmap = wx.EmptyBitmap(*self.ClientSize)
        context = wx.ClientDC(self)

        dc = wx.MemoryDC()
        dc.SelectObject(bitmap)

        dc.BlitPointSize((0, 0), self.ClientSize, context, (0, 0))

        # close the DC, to be sure the bitmap can be used safely
        del dc

        img = wx.ImageFromBitmap(bitmap)
        self.microscope_view.thumbnail.value = img
