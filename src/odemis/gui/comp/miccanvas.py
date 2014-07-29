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
import numpy
from odemis import util, model
from odemis.acq import stream
from odemis.gui.model import LiveViewGUIData
from odemis.gui.comp.canvas import CAN_ZOOM, CAN_DRAG, CAN_FOCUS
from odemis.gui.comp.overlay.view import HistoryOverlay, PointSelectOverlay, MarkingLineOverlay
from odemis.gui.util import wxlimit_invocation, call_after, ignore_dead, img
from odemis.model import VigilantAttributeBase
from odemis.util import units
import threading
import time
import weakref
import wx

from odemis.acq.stream import UNDEFINED_ROI, SEMStream
import odemis.gui as gui
import odemis.gui.comp.canvas as canvas
import odemis.gui.comp.overlay.view as view_overlay
import odemis.gui.comp.overlay.world as world_overlay
import odemis.gui.model as guimodel

SECOM_MODES = (guimodel.TOOL_ZOOM, guimodel.TOOL_ROI)
SPARC_MODES = (guimodel.TOOL_ROA, guimodel.TOOL_POINT, guimodel.TOOL_RO_ANCHOR)


@decorator
def microscope_view_check(f, self, *args, **kwargs):
    """ This method decorator check if the microscope_view attribute is set
    """
    if self.microscope_view:
        return f(self, *args, **kwargs)


# Note: a Canvas with a fit_view_to_content method indicates that the view
# can be adapted. (Some other components of the GUI will use this information)


class DblMicroscopeCanvas(canvas.DraggableCanvas):
    """ A draggable, flicker-free window class adapted to show pictures of two
    microscope simultaneously.

    It knows size and position of what is represented in a picture and display
    the pictures accordingly.

    It also provides various typical overlays (ie, drawings) for microscope views.

    Public attributes:
    .abilities (set of CAN_*): features/restrictions allowed to be performed
    .fit_view_to_next_image (Boolean): False by default. If True, next time an image
      is received, it will ensure the whole content fits the view (and reset
      this flag).
    """

    def __init__(self, *args, **kwargs):
        canvas.DraggableCanvas.__init__(self, *args, **kwargs)
        self.microscope_view = None
        self._tab_data_model = None

        self.abilities |= {CAN_ZOOM, CAN_FOCUS}
        self.fit_view_to_next_image = True

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
        self.allowed_modes = None

        # meter per "world unit"
        # for conversion between "world pos" in the canvas and a real unit
        # mpp == mpwu => 1 world coord == 1 px => scale == 1
        self.mpwu = 1.0  # m/wu
        # This is a const, don't change at runtime!
        # FIXME: turns out to be useless. => Need to directly use physical
        # coordinates. Currently, the only difference is that Y in going up in
        # physical coordinates and down in world coordinates.

        self._previous_size = self.ClientSize

        self.cursor = wx.STANDARD_CURSOR

        # Overlays

        # Passive overlays that only display information, but offer no interaction
        self._crosshair_ol = None
        self._spotmode_ol = None
        self._fps_ol = None
        self._focus_overlay = None

        self.pixel_overlay = None
        self.points_overlay = None
        self.dicho_overlay = None

        # play/pause icon
        self.icon_overlay = view_overlay.StreamIconOverlay(self)
        self.add_view_overlay(self.icon_overlay)

        # Unused at the moment
        self.zoom_overlay = None
        self.update_overlay = None

    # Ability manipulation

    def disable_zoom(self):
        self.abilities.remove(CAN_ZOOM)

    def enable_zoom(self):
        self.abilities.add(CAN_ZOOM)

    # END Ability manipulation

    def setView(self, microscope_view, tab_data):
        """
        Set the microscope_view that this canvas is displaying/representing
        Can be called only once, at initialisation.

        :param microscope_view:(model.MicroscopeView)
        :param tab_data: (model.MicroscopyGUIData)
        """
        # This is a kind of kludge, see mscviewport.MicroscopeViewport for details
        assert(self.microscope_view is None)

        self.microscope_view = microscope_view
        self._tab_data_model = tab_data

        self._focus_overlay = None

        if self.microscope_view.get_focus_count():
            self._focus_overlay = self.add_view_overlay(view_overlay.FocusOverlay(self))

        self.microscope_view.mpp.subscribe(self._on_view_mpp, init=True)

        if hasattr(self.microscope_view, "stage_pos"):
            # TODO: should this be moved to MicroscopeView, to update view_pos
            # when needed?
            # Listen to stage pos, so that all views move together when the
            # stage moves.
            self.microscope_view.stage_pos.subscribe(self._onStagePos)
        self.microscope_view.view_pos.subscribe(self._onViewPos)
        # Update new position immediately, so that fit_to_content() directly
        # gets the correct center
        world_pos = self.physical_to_world_pos(self.microscope_view.view_pos.value)
        self._calc_bg_offset(world_pos)
        self.requested_world_pos = world_pos

        self.points_overlay = world_overlay.PointsOverlay(self)
        self.pixel_overlay = world_overlay.PixelSelectOverlay(self)

        # any image changes
        self.microscope_view.lastUpdate.subscribe(self._onViewImageUpdate, init=True)

        # handle cross hair
        self.microscope_view.show_crosshair.subscribe(self._on_cross_hair_show, init=True)

        tab_data.main.debug.subscribe(self._on_debug, init=True)

        if tab_data.tool:
            tab_data.tool.subscribe(self._on_tool, init=True)

    def _on_tool(self, tool_mode):
        """ Set the right mode and active overlays when a tool is selected """

        # A weird situation which should not happen
        if self.dragging:
            logging.error("Changing to mode (%s) while dragging is not supported!", tool_mode)

        # Check if the desired tool mode is allowed
        if self.allowed_modes and tool_mode not in self.allowed_modes:
            logging.warn("Toolmode %s is not allowed and will be ignored!", tool_mode)
            tool_mode = guimodel.TOOL_NONE

        self.current_mode = tool_mode
        cursor = wx.STANDARD_CURSOR

        self._set_spot_mode(tool_mode)
        self._set_dichotomy_mode(tool_mode)
        self._set_point_select_mode(tool_mode)

        self.update_drawing()

        if tool_mode == guimodel.TOOL_ROI:
            # self.current_mode = guimodel.TOOL_ROI
            # self.add_active_overlay(self.update_overlay)
            # cursor = wx.CURSOR_CROSS
            raise NotImplementedError()
        elif tool_mode == guimodel.TOOL_ZOOM:
            # self.current_mode = guimodel.TOOL_ZOOM
            # self.add_active_overlay(self.zoom_overlay)
            # cursor = wx.CURSOR_CROSS
            raise NotImplementedError()

        self.set_default_cursor(cursor)

    # Overlay creation and activation

    def _on_cross_hair_show(self, activated):
        """ Activate the cross hair view overlay """
        if activated:
            if self._crosshair_ol is None:
                self._crosshair_ol = self.add_view_overlay(view_overlay.CrossHairOverlay(self))
        elif self._crosshair_ol:
            self.remove_view_overlay(self._crosshair_ol)

        self.Refresh(eraseBackground=False)

    # FIXME: seems like it might still be called while the Canvas has been destroyed
    # => need to make sure that the object is garbage collected (= no more references) once it's
    # not used. (Or explicitly unsubscribe??)
    @ignore_dead
    def _on_debug(self, activated):
        """ Called when GUI debug mode changes => display FPS overlay """
        if activated:
            if self._fps_ol is None:
                self._fps_ol = self.add_view_overlay(view_overlay.TextViewOverlay(self))
                self._fps_ol.add_label("")
        elif self._fps_ol:
            self.remove_view_overlay(self._fps_ol)

        self.Refresh(eraseBackground=False)

    def _set_spot_mode(self, tool_mode):
        is_activated = tool_mode == guimodel.TOOL_SPOT

        if is_activated:
            if self._spotmode_ol is None:
                self._spotmode_ol = self.add_view_overlay(view_overlay.SpotModeOverlay(self))
        elif self._spotmode_ol:
            self.remove_view_overlay(self._spotmode_ol)

        self.microscope_view.show_crosshair.value = not is_activated

        self.Refresh(eraseBackground=False)

    def _set_dichotomy_mode(self, tool_mode):
        """ Activate the dichotomy overlay if needed """
        if tool_mode == guimodel.TOOL_DICHO:
            if not self.dicho_overlay:
                self.dicho_overlay = view_overlay.DichotomyOverlay(
                    self,
                    self._tab_data_model.dicho_seq
                )
                self.add_view_overlay(self.dicho_overlay)
            self.dicho_overlay.activate()
        elif self.dicho_overlay:
            self.dicho_overlay.deactivate()

    def _set_point_select_mode(self, tool_mode):
        """ Activate the required point selection overlay """

        if tool_mode == guimodel.TOOL_POINT:
            # Enable the Spectrum point select overlay when a spectrum stream
            # is attached to the view
            stream_tree = self.microscope_view.stream_tree
            # Enable the Angular Resolve point select overlay when there's a
            # AR stream known anywhere in the data model (and the view has
            # streams).
            tab_streams = self._tab_data_model.streams.value

            if (len(self.microscope_view.stream_tree) and
                    any([isinstance(s, stream.AR_STREAMS) for s in tab_streams])):
                self.points_overlay.activate(True)
            # TODO: Filtering by the name SEM CL is not desired. There should be
            # a more intelligent way to query the StreamTree about what's
            # present, like how it's done for Spectrum and AR streams
            elif stream_tree.spectrum_streams or stream_tree.get_streams_by_name("SEM CL"):
                self.pixel_overlay.activate()
        else:
            if self.pixel_overlay:
                self.pixel_overlay.deactivate()
            if self.points_overlay:
                self.points_overlay.deactivate()

    # END Overlay creation and activation

    def _get_ordered_images(self):
        """ Return the list of images to display, ordered bottom to top (=last to draw) """
        st = self.microscope_view.stream_tree
        images = st.getImages()

        # Sort by size, so that the biggest picture is first drawn (no opacity)
        images.sort(
            lambda a, b: cmp(
                numpy.prod(b.shape[0:2]) * b.metadata[model.MD_PIXEL_SIZE][0],
                numpy.prod(a.shape[0:2]) * a.metadata[model.MD_PIXEL_SIZE][0]
            )
        )

        return images

    def _convert_streams_to_images(self):
        """ Temporary function to convert the StreamTree to a list of images as
        the canvas currently expects.
        """

        images = self._get_ordered_images()

        # add the images in order
        ims = []
        for rgbim in images:
            # TODO: convert to RGBA later, in canvas and/or cache the conversion
            # Canvas needs to accept the NDArray (+ specific attributes
            # recorded separately).

            rgba_im = img.format_rgba_darray(rgbim)
            keepalpha = False
            scale = rgbim.metadata[model.MD_PIXEL_SIZE][0] / self.mpwu
            pos = self.physical_to_world_pos(rgbim.metadata[model.MD_POS])
            rot = -rgbim.metadata.get(model.MD_ROTATION, 0) # ccw -> cw

            ims.append((rgba_im, pos, scale, keepalpha, rot))
        self.set_images(ims)

        # For debug only:
        if images:
            self._latest = max(i.metadata.get(model.MD_ACQ_DATE, 0) for i in images)
        else:
            self._latest = 0
            if self._latest > 0:
                logging.debug("Updated canvas list %g s after acquisition",
                              time.time() - self._latest)

        # set merge_ratio
        self.merge_ratio = self.microscope_view.stream_tree.kwargs.get("merge", 0.5)

    # FIXME: it shouldn't need to ignore deads, as the subscription should go
    # away as soon as it's destroyed. However, after SECOM acquisition, something
    # seems to keep reference to the SecomCanvas, which prevents it from being
    # fully destroyed.
    @ignore_dead
    def _onViewImageUpdate(self, t):
        # TODO: use the real streamtree functions
        # for now we call a conversion layer
        self._convert_streams_to_images()
        if self.fit_view_to_next_image and any([i is not None for i in self.images]):
            self.fit_view_to_content()
            self.fit_view_to_next_image = False
        #logging.debug("Will update drawing for new image")
        wx.CallAfter(self.request_drawing_update)

    def update_drawing(self):
        """ Update the drawing and thumbnail """
        # TODO: detect that the canvas is not visible, and so should no/less
        # frequently be updated? The difficulty is that it must be redrawn as
        # soon as it's shown again.
        super(DblMicroscopeCanvas, self).update_drawing()

        if self.microscope_view:
            self._updateThumbnail()

    @wxlimit_invocation(2)  # max 1/2 Hz
    @call_after  # needed as it accesses the DC
    @ignore_dead
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

        self.microscope_view.thumbnail.value = wx.ImageFromBitmap(bitmap)

    def _onStagePos(self, value):
        """
        When the stage is moved: recenter the view
        value: dict with "x" and "y" entries containing meters
        """
        # this can be caused by any viewport which has requested to recenter
        # the buffer
        pos = self.physical_to_world_pos((value["x"], value["y"]))
        # skip ourselves, to avoid asking the stage to move to (almost) the same position
        wx.CallAfter(super(DblMicroscopeCanvas, self).recenter_buffer, pos)

    def _onViewPos(self, phy_pos):
        """
        When the view position is updated: recenter the view
        phy_pos (tuple of 2 float): X/Y in physical coordinates (m)
        """
        pos = self.physical_to_world_pos(phy_pos)
        # skip ourselves, to avoid asking the stage to move to (almost) the same position
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

            self.microscope_view.moveStageToView()  # will do nothing if no stage
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

        super(DblMicroscopeCanvas, self).fit_to_content(recenter=recenter)

        # this will indirectly call _on_view_mpp(), but not have any additional effect
        if self.microscope_view:
            new_mpp = self.mpwu / self.scale
            self.microscope_view.mpp.value = self.microscope_view.mpp.clip(new_mpp)

    def _on_view_mpp(self, mpp):
        """ Called when the view.mpp is updated """
        self.scale = self.mpwu / mpp
        self.microscope_view.horizontal_field_width.value = self.horizontal_field_width
        wx.CallAfter(self.request_drawing_update)

    @property
    def horizontal_field_width(self):
        """ Return the field width of the canvas in meters

        :return: Field width in meters
        """
        if self.microscope_view:
            return self.microscope_view.mpp.value * self.ClientSize.x

        return None

    def on_size(self, event):
        new_size = event.Size

        # Update the mpp, so that the same data will be displayed.
        if self.microscope_view:
            hfw = self._previous_size[0] * self.microscope_view.mpp.value
            new_mpp = hfw / new_size[0]
            self.microscope_view.mpp.value = self.microscope_view.mpp.clip(new_mpp)

        super(DblMicroscopeCanvas, self).on_size(event)
        self._previous_size = new_size

    @microscope_view_check
    def Zoom(self, inc, block_on_zero=False):
        """ Zoom by the given factor

        :param inc (float): scale the current view by 2^inc
        :param block_on_zero (boolean): if True, and the zoom goes from software
            downscaling to software upscaling, it will stop at no software scaling
            ex:  # 1 => *2 ; -1 => /2; 2 => *4...

        """

        scale = 2.0 ** inc
        prev_mpp = self.microscope_view.mpp.value
        # Clip within the range
        mpp = prev_mpp / scale

        if block_on_zero:
            # Check for every image
            for im in self.microscope_view.stream_tree.getImages():
                try:
                    im_mpp = im.metadata[model.MD_PIXEL_SIZE][0]
                    # did we just passed the image mpp (=zoom zero)?
                    if ((prev_mpp < im_mpp < mpp or prev_mpp > im_mpp > mpp) and
                            abs(prev_mpp - im_mpp) > 1e-15):  # for float error
                        mpp = im_mpp
                except KeyError:
                    pass

        mpp = sorted(self.microscope_view.mpp.range + (mpp,))[1]
        self.microscope_view.mpp.value = mpp # this will call _on_view_mpp()

    # Zoom/merge management
    def on_wheel(self, evt):
        """ Process user mouse wheel events

        If able and without modifiers, the Canvas will zooom in/out
        If the Ctrl key is down, the merge ratio of the visible layers will be adjusted.

        """

        change = evt.GetWheelRotation() / evt.GetWheelDelta()
        if evt.ShiftDown():
            change *= 0.2  # softer

        if evt.CmdDown():  # = Ctrl on Linux/Win or Cmd on Mac
            ratio = self.microscope_view.merge_ratio.value + (change * 0.1)
            # clamp
            ratio = sorted(self.microscope_view.merge_ratio.range + (ratio,))[1]
            self.microscope_view.merge_ratio.value = ratio
        else:
            if CAN_ZOOM in self.abilities:
                self.Zoom(change, block_on_zero=evt.ShiftDown())

        super(DblMicroscopeCanvas, self).on_wheel(evt)

    def on_char(self, evt):
        """ Process a key stroke """

        if CAN_ZOOM in self.abilities:
            key = evt.GetKeyCode()
            change = 1

            if evt.ShiftDown():
                block_on_zero = True
                change *= 0.2  # softer
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

        if self._focus_overlay:
            self._focus_overlay.add_shift(shift, 0)
        logging.debug("Moving focus0 by %f μm", shift * 1e6)
        self.microscope_view.get_focus(0).moveRel({"z": shift})

    def _moveFocus1(self):
        with self._moveFocusLock:
            shift = self._moveFocusDistance[1]
            self._moveFocusDistance[1] = 0

        if self._focus_overlay:
            self._focus_overlay.add_shift(shift, 1)

        logging.debug("Moving focus1 by %f μm", shift * 1e6)
        self.microscope_view.get_focus(1).moveRel({"z": shift})

    def on_right_down(self, event):
        """ Process right mouse button down event

        In this class, we only manage the mouse cursor and the overlay that displays the right
        dragging behaviour. The actual dragging logic is handled in the super class.

        """

        cursor = None

        if CAN_FOCUS in self.abilities and not self.dragging:
            # Note: Set the cursor before the super method is called.
            # There is a Ubuntu/wxPython related bug that SetCursor does not work once CaptureMouse
            # is called (which happens in the super method).
            if self.microscope_view:
                num_focus = self.microscope_view.get_focus_count()
                if num_focus == 1:
                    logging.debug("One focus actuator found")
                    cursor = wx.StockCursor(wx.CURSOR_SIZENS)
                elif num_focus == 2:
                    logging.debug("Two focus actuators found")
                    cursor = wx.StockCursor(wx.CURSOR_CROSS)
            if self._focus_overlay:
                self._focus_overlay.clear_shift()

        super(DblMicroscopeCanvas, self).on_right_down(event, cursor)

    def on_right_up(self, event):
        """ Process right mouse button release event

        Stop the focus timers and clear any visual indicators. The actual mouse dragging is cleared
        in the super class's method.

        """

        if CAN_FOCUS in self.abilities and self.right_dragging:
            # Stop the timers, so there won't be any more focussing once the button is released.
            for timer in [self._moveFocus0Timer, self._moveFocus1Timer]:
                if timer.IsRunning():
                    timer.Stop()
            # The mouse cursor is automatically reset in the super class method
            if self._focus_overlay:
                self._focus_overlay.clear_shift()

        super(DblMicroscopeCanvas, self).on_right_up(event)

    def on_motion(self, evt):
        """ Process mouse motion

        Adjust the focus if it's enabled and the right mouse button is being pressed.
        Left dragging of the canvas is handled in the super class.

        """

        if CAN_FOCUS in self.abilities and self.right_dragging:
            # Linear when small, non-linear when big.
            # use 3 points: starting point, previous point, current point
            #  * if dis < 32 px => min : dis (small linear zone)
            #  * else: dis + 1/32 * sign* (dis-32)**2 => (square zone)
            # send diff between value and previous value sent => it should
            # always be at the same position for the cursor at the same place
            #
            # NOTE: The focus overlay is loosely dependant on the values
            # generated here, because it uses them to guesstimate the maximum
            # value produced while focussing.

            if evt.ShiftDown():
                # TODO: Pressing and releasing the shift key while adjusting focus messes up the
                # origin of the focus, so it's hard/impossible to go back to the original focus
                # by dragging the mouse back to the center. Find a way to 'reset' the focus after
                # the shift key has been released.
                softener = 0.1  # softer
            else:
                softener = 1

            linear_zone = 32.0
            pos = evt.GetPositionTuple()
            for i in [0, 1]:  # x, y
                shift = pos[i] - self._rdrag_init_pos[i]

                if i:
                    # Flip the sign for vertical movement, as indicated in the
                    # on_extra_axis_move docstring: up/right is positive
                    shift = -shift
                    # logging.debug("pos %s, shift %s", pos[i], shift)

                if abs(shift) <= linear_zone:
                    value = shift
                else:
                    ssquare = cmp(shift, 0) * (abs(shift) - linear_zone) ** 2
                    value = shift + ssquare / linear_zone

                change = value - self._rdrag_prev_value[i]

                # Changing the extra axis start the focus timer
                if change:
                    self.on_extra_axis_move(i, change * softener)
                    self._rdrag_prev_value[i] = value

        super(DblMicroscopeCanvas, self).on_motion(evt)

    def world_to_physical_pos(self, pos):
        """ Translate world coordinates into physical coordinates.

        Note: The y value needs to be flipped between world and physical
            coordinates.

        Note: If 'meters per world unit' (mpwu) is one, world and physical
            coordinates are the same.

        :param pos: (float, float) "world" coordinates
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

    def draw(self):
        """ Redraw the buffer while calculating the number of frames we *could* display

        The fps value is an indication of how many times we can draw per second and not the actual
        number of frames displayed on screen!

        """

        if self._fps_ol:
            t_start = time.time()
            super(DblMicroscopeCanvas, self).draw()
            dur = time.time() - t_start

            try:
                fps = 1 / dur
                self._fps_ol.labels[0].text = u"%s fps" % units.readable_str(fps, sig=4)
            except ZeroDivisionError:
                self._fps_ol.labels[0].text = u"∞ fps"
        else:
            super(DblMicroscopeCanvas, self).draw()


class OverviewCanvas(DblMicroscopeCanvas):
    """ Canvas for displaying the overview stream """

    def __init__(self, *args, **kwargs):
        super(OverviewCanvas, self).__init__(*args, **kwargs)

        self.abilities = set()
        self.background_brush = wx.SOLID

        # Point select overlay for stage navigation
        self.point_select_overlay = PointSelectOverlay(self)
        self.add_view_overlay(self.point_select_overlay)

        # This canvas has a special overlay for tracking position history
        self.history_overlay = HistoryOverlay(self)
        self.add_view_overlay(self.history_overlay)

        self.add_active_overlay(self.history_overlay)
        self.add_active_overlay(self.point_select_overlay)

    def setView(self, microscope_view, tab_data):
        super(OverviewCanvas, self).setView(microscope_view, tab_data)
        # Keep track of old stage positions
        if tab_data.main.stage:
            tab_data.main.stage.position.subscribe(self.on_stage_pos_change, init=True)

    def on_stage_pos_change(self, p_pos):
        self.history_overlay.add_location(
            (p_pos['x'], p_pos['y']),
            (self.microscope_view.mpp.value * self.ClientSize.x,
             self.microscope_view.mpp.value * self.ClientSize.y))
        wx.CallAfter(self.Refresh)


class SecomCanvas(DblMicroscopeCanvas):

    def __init__(self, *args, **kwargs):
        super(SecomCanvas, self).__init__(*args, **kwargs)

        # TODO: once the StreamTrees can render fully, reactivate the background
        # pattern
        self.background_brush = wx.SOLID

    # Special version which put the SEM images first, as with the current
    # display mechanism in the canvas, the fluorescent images must be displayed
    # together last
    def _get_ordered_images(self):
        """
        return the list of images to display, in order from lowest to topest
         (last to draw)
        """
        images = []

        streams = self.microscope_view.getStreams()
        has_sem_image = False
        for s in streams:
            if not s:
                # should not happen, but let's not completely fail on this
                logging.error("StreamTree has a None stream")
                continue

            if not hasattr(s, "image"):
                continue

            rgbim = s.image.value
            if rgbim is None:
                continue

            if isinstance(s, stream.EM_STREAMS):
                # as last
                images.append(rgbim)
                # FIXME: See the log warning
                if has_sem_image:
                    logging.warning(("Multiple SEM images are not handled "
                                     "correctly for now"))
                has_sem_image = True
            else:
                images.insert(0, rgbim) # as first

        return images

    # Prevent certain events from being processed by the canvas

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

    def setView(self, microscope_view, tab_data):
        super(SecomCanvas, self).setView(microscope_view, tab_data)

        # TODO: move this to the view controller
        if (isinstance(tab_data, LiveViewGUIData)
            and microscope_view.name.value == "SEM" # TODO do in a cleaner way
            and isinstance(tab_data.main.ebeam.horizontalFoV, VigilantAttributeBase)):
            microscope_view.horizontal_field_width.subscribe(self.on_view_em_hfw_change, init=True)

    def on_view_em_hfw_change(self, hfw):
        """ Adjust the SEM horizontal field width when the view HFW changes """
        if self._tab_data_model.emState.value == guimodel.STATE_ON:
            # Collect all the SEM streams
            em_streams = self.microscope_view.stream_tree.get_streams_by_type(SEMStream)

            for em_stream in em_streams:
                if em_stream.is_active.value:
                    mi, ma = self._tab_data_model.main.ebeam.horizontalFoV.range
                    # Clip the HFW if needed
                    if not mi <= hfw <= ma:
                        hfw = self._tab_data_model.main.ebeam.horizontalFoV.clip(hfw)

                    # Set the hardware HFW
                    self._tab_data_model.main.ebeam.horizontalFoV.value = hfw

                    # Set the HFW for all the views (in case of clipping)
                    for view in self._tab_data_model.visible_views.value:
                        if view.stream_tree.get_streams_by_type(SEMStream):
                            view.horizontal_field_width.value = hfw
                    break


class SparcAcquiCanvas(DblMicroscopeCanvas):
    def __init__(self, *args, **kwargs):
        super(SparcAcquiCanvas, self).__init__(*args, **kwargs)

        self._roa = None  # The ROI VA of SEM CL stream, initialized on setView()
        self.roa_overlay = None

        self._dc_region = None  # The dcRegion VA of the SEM CL
        self.dirftcor_overlay = None

    def _on_tool(self, tool_mode):
        super(SparcAcquiCanvas, self)._on_tool(tool_mode)

        self._set_roa_mode(tool_mode)
        self._set_dc_mode(tool_mode)

    def _set_roa_mode(self, tool_mode):
        if tool_mode == guimodel.TOOL_ROA:
            self.roa_overlay.activate()
        elif self.roa_overlay:
            self.roa_overlay.deactivate()
        self.Refresh(eraseBackground=False)

    def _set_dc_mode(self, tool_mode):
        if tool_mode == guimodel.TOOL_RO_ANCHOR:
            self.dirftcor_overlay.activate()
        elif self.dirftcor_overlay:
            self.dirftcor_overlay.deactivate()
        self.Refresh(eraseBackground=False)

    def setView(self, microscope_view, tab_data):
        """ Set the microscope_view that this canvas is displaying/representing

        Should be called only once, at initialisation.

        :param microscope_view:(model.MicroscopeView)
        :param tab_data: (model.MicroscopyGUIData)

        """

        sem = tab_data.main.ebeam
        if not sem:
            raise AttributeError("No SEM on the microscope")

        # Associate the ROI of the SEM CL stream to the region of acquisition
        sem_stream = tab_data.semStream
        if sem_stream is None:
            raise KeyError("SEM CL stream not set, required for the SPARC acquisition")

        super(SparcAcquiCanvas, self).setView(microscope_view, tab_data)

        # Get the region of interest and link it to the ROA overlay

        self._roa = sem_stream.roi
        self.roa_overlay = world_overlay.RepetitionSelectOverlay(self, self._roa)
        self.add_world_overlay(self.roa_overlay)

        # Link drift correction region

        self._dc_region = sem_stream.dcRegion
        self.dirftcor_overlay = world_overlay.RepetitionSelectOverlay(
            self, self._dc_region, colour=gui.SELECTION_COLOUR_2ND)
        self.add_world_overlay(self.dirftcor_overlay)

        # Regions depend on the magnification (=field of view)

        if isinstance(sem.magnification, VigilantAttributeBase):
            sem.magnification.subscribe(self._on_sem_mag)

    # TODO: maybe should not be called directly, but should be a VA on the view or the tab?
    def show_repetition(self, rep, style=None):
        """ Change/display repetition on the ROA if the ROA is visible

        rep (None or tuple of 2 ints): if None, repetition is hidden
        style (overlay.FILL_*): type of repetition display

        """

        if rep is None:
            self.roa_overlay.fill = world_overlay.RepetitionSelectOverlay.FILL_NONE
        else:
            self.roa_overlay.fill = style
            self.roa_overlay.repetition = rep

        wx.CallAfter(self.request_drawing_update)

    def _on_sem_mag(self, mag):
        """
        Called when the magnification of the SEM changes
        """
        # That means the pixelSize changes, so the (relative) ROA is different
        # Either we update the ROA so that physically it stays the same, or
        # we update the selection so that the ROA stays the same. It's probably
        # that the user has forgotten to set the magnification before, so let's
        # pick solution 2.
        self.roa_overlay.on_roa(self._roa.value)
        self.dirftcor_overlay.on_roa(self._dc_region.value)

    def _get_sem_rect(self):
        """
        Returns the (theoretical) scanning area of the SEM. Works even if the
        SEM has not send any image yet.
        returns (tuple of 4 floats): position in physical coordinates m (l, t, b, r)
        raises AttributeError in case no SEM is found
        """
        sem = self._tab_data_model.main.ebeam

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

    def convert_roi_phys_to_ratio(self, phys_rect):
        """
        Convert and truncate the ROI in physical coordinates to the coordinates
         relative to the SEM FoV
        phys_rect (None or 4 floats): physical position of the tl and br points
        return (4 floats): tlbr positions relative to the FoV
        """
        sem = self._tab_data_model.main.ebeam

        # Get the position of the overlay in physical coordinates
        if phys_rect is None:
            return UNDEFINED_ROI

        # Position of the complete SEM scan in physical coordinates
        sem_rect = self._get_sem_rect()

        # Take only the intersection so that that ROA is always inside the SEM scan
        phys_rect = util.rect_intersect(phys_rect, sem_rect)
        if phys_rect is None:
            return UNDEFINED_ROI

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

        return rel_rect

    def convert_roi_ratio_to_phys(self, roi):
        """
        Convert the ROI in relative coordinates (to the SEM FoV) into physical
         coordinates
        roi (4 floats): tlbr positions relative to the FoV
        return (None or 4 floats): physical position of the tl and br points, or
          None if no ROI is defined
        """
        if roi == UNDEFINED_ROI:
            return None
        else:
            # convert relative position to physical position
            try:
                sem_rect = self._get_sem_rect()
            except AttributeError:
                logging.warning("Trying to convert a SEM ROI, but no SEM available")
                return None

        # In physical coordinates Y goes up, but in ROI, Y goes down => "1-"
        phys_rect = (sem_rect[0] + roi[0] * (sem_rect[2] - sem_rect[0]),
                     sem_rect[1] + (1 - roi[3]) * (sem_rect[3] - sem_rect[1]),
                     sem_rect[0] + roi[2] * (sem_rect[2] - sem_rect[0]),
                     sem_rect[1] + (1 - roi[1]) * (sem_rect[3] - sem_rect[1]))

        return phys_rect


class SparcAlignCanvas(DblMicroscopeCanvas):
    """
    Special restricted version that displays the first stream always fitting
    the entire canvas.
    """
    # TODO: could probably be done with a simple BitmapCanvas + fit_to_content?

    def __init__(self, *args, **kwargs):
        super(SparcAlignCanvas, self).__init__(*args, **kwargs)
        self.abilities -= set([CAN_ZOOM, CAN_DRAG])

        self._goal_im_ref = None
        self._goal_wim = None

    def _reset_goal_im(self):
        """ Called when the goal_im is dereferenced """
        self._goal_wim = None

    def _convert_streams_to_images(self):
        """
        Same as the overridden method, but ensures the goal image keeps the alpha
        and is displayed second. Also force the mpp to be the one of the sensor.
        """
        ims = [None]
        streams = self.microscope_view.getStreams()
        # order and display the images
        for s in streams:
            if not s:
                # should not happen, but let's not completely fail on this
                logging.error("StreamTree has a None stream")
                continue

            if not hasattr(s, "image"):
                continue
            rgbim = s.image.value
            if rgbim is None:
                continue

            # convert to wxImage
            # Special trick to avoid regenerating the wxImage for Goal all the time
            # TODO: make it generic
            if s.name.value == "Goal":
                prev_im = None if self._goal_im_ref is None else self._goal_im_ref()
                if (self._goal_wim is None or prev_im is None or
                    prev_im is not rgbim):
                    logging.debug("Converting goal image")
                    wim = img.format_rgba_darray(rgbim)
                    self._goal_im_ref = weakref.ref(rgbim, self._reset_goal_im)
                    self._goal_wim = wim
                else:
                    wim = self._goal_wim
            else:
                wim = img.format_rgba_darray(rgbim)

            keepalpha = (rgbim.shape[2] == 4)

            scale = rgbim.metadata[model.MD_PIXEL_SIZE][0] / self.mpwu
            pos = (0, 0) # the sensor image should be centered on the sensor center

            if s.name.value == "Goal":
                # goal image => add at the end
                ims.append((wim, pos, scale, keepalpha, None))
            else:
                # add at the beginning
                ims[0] = (wim, pos, scale, keepalpha, None)

        self.set_images(ims)

        # set merge_ratio
        self.merge_ratio = self.microscope_view.stream_tree.kwargs.get("merge", 1)

        # always refit to image (for the rare case it has changed size)
        self.fit_view_to_content(recenter=True)

    def on_size(self, event):
        # refit image
        self.fit_view_to_content(recenter=True)
        # Skip DblMicroscopeCanvas.on_size which plays with mpp
        canvas.DraggableCanvas.on_size(self, event)


class ZeroDimensionalPlotCanvas(canvas.PlotCanvas):
    """ A plotable canvas with a vertical 'focus line', that shows the x and y
    values of the selected position.

    TODO: change name?
    """

    def __init__(self, *args, **kwargs):

        # These attributes need to be assigned before the super constructor
        # is called, because they are used in the on_size event handler.
        self.val_y = model.VigilantAttribute(None)
        self.val_x = model.VigilantAttribute(None)
        # FIXME: This attribute should be renamed to simply `view`, or `view_model`, but that
        # would also require renaming the `microscope_view` attributes of the
        # other Canvas classes.
        self.microscope_view = None
        self._tab_data_model = None

        super(ZeroDimensionalPlotCanvas, self).__init__(*args, **kwargs)

        self.drag_init_pos = None

        ## Overlays
        self.SetBackgroundColour(self.Parent.BackgroundColour)
        self.SetForegroundColour(self.Parent.ForegroundColour)

        self.closed = canvas.PLOT_CLOSE_BOTTOM
        self.plot_mode = canvas.PLOT_MODE_BAR

        self.markline_overlay = view_overlay.MarkingLineOverlay(
            self,
            orientation=MarkingLineOverlay.HORIZONTAL | MarkingLineOverlay.VERTICAL)
        self.add_view_overlay(self.markline_overlay)
        self.markline_overlay.activate()

    def set_data(self, data,
                 unit_x=None, unit_y=None, range_x=None, range_y=None):
        """ Subscribe to the x position of the overlay when data is loaded """
        super(ZeroDimensionalPlotCanvas, self).set_data(data, unit_x, unit_y, range_x, range_y)

        if data is None:
            self.markline_overlay.v_pos.unsubscribe(self._map_to_plot_values)
        else:
            self.markline_overlay.v_pos.subscribe(self._map_to_plot_values, init=True)

    def clear(self):
        super(ZeroDimensionalPlotCanvas, self).clear()
        self.val_x.value = None
        self.val_y.value = None
        self.markline_overlay.clear_labels()

    # Event handlers

    def on_size(self, evt):
        """ Update the position of the focus line """
        super(ZeroDimensionalPlotCanvas, self).on_size(evt)
        if None not in (self.val_x.value, self.val_y.value):
            pos = (self._val_x_to_pos_x(self.val_x.value),
                   self._val_y_to_pos_y(self.val_y.value))
            self.markline_overlay.set_position(pos)

    def _map_to_plot_values(self, v_pos):
        """ Calculate the x and y *values* belonging to the x pixel position """

        if not self._data or v_pos is None:
            return

        v_posx, v_posy = v_pos

        self.val_x.value = self._pos_x_to_val_x(v_posx, snap=True)
        self.val_y.value = self._val_x_to_val_y(self.val_x.value, snap=True)

        pos = (v_posx, self._val_y_to_pos_y(self.val_y.value))
        self.markline_overlay.set_position(pos)

        self.markline_overlay.x_label = units.readable_str(self.val_x.value, self.unit_x, 3)
        self.markline_overlay.y_label = units.readable_str(self.val_y.value, self.unit_y, 3)

        self.Parent.Refresh()  # TODO: Does it need to be parent? is it needed at all?

    def setView(self, microscope_view, tab_data):
        """ Set the microscope_view that this canvas is displaying/representing
        Can be called only once, at initialisation.

        :param microscope_view:(model.MicroscopeView)
        :param tab_data: (model.MicroscopyGUIData)
        """
        # This is a kind of kludge, see mscviewport.MicroscopeViewport for
        # details
        assert(self.microscope_view is None)

        self.microscope_view = microscope_view
        self._tab_data_model = tab_data

    @wxlimit_invocation(2) # max 1/2 Hz
    @call_after  # needed as it accesses the DC
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

        self.microscope_view.thumbnail.value = wx.ImageFromBitmap(bitmap)

    def update_drawing(self):
        super(ZeroDimensionalPlotCanvas, self).update_drawing()

        if self.microscope_view:
            self._updateThumbnail()

    def get_y_value(self):
        """ Return the current y value """
        return self.val_y.value


class AngularResolvedCanvas(canvas.DraggableCanvas):
    """ Angular resolved canvas
    """
    # TODO: it actually could be just a BitmapCanvas, but it needs
    # a (simple) fit_to_content()

    def __init__(self, *args, **kwargs):

        super(AngularResolvedCanvas, self).__init__(*args, **kwargs)

        self.microscope_view = None
        self._tab_data_model = None
        self.abilities -= set([CAN_DRAG, CAN_FOCUS])

        self.background_brush = wx.SOLID # background is always black

        ## Overlays

        self.polar_overlay = view_overlay.PolarOverlay(self)
        self.polar_overlay.canvas_padding = 10
        self.add_view_overlay(self.polar_overlay)
        self.add_active_overlay(self.polar_overlay)

    # Event handlers

    def on_size(self, evt):
        """ Called when the canvas is resized """
        self.fit_to_content(recenter=True)
        super(AngularResolvedCanvas, self).on_size(evt)

    def setView(self, microscope_view, tab_data):
        """Set the microscope_view that this canvas is displaying/representing
        Can be called only once, at initialisation.

        :param microscope_view:(model.MicroscopeView)
        :param tab_data: (model.MicroscopyGUIData)
        """
        # This is a kind of kludge, see viewport.MicroscopeViewport for details
        assert(self.microscope_view is None)

        self.microscope_view = microscope_view
        self._tab_data_model = tab_data

        # any image changes
        self.microscope_view.lastUpdate.subscribe(self._onViewImageUpdate, init=True)

    def _convertStreamsToImages(self):
        """ Temporary function to convert the StreamTree to a list of images as
        the canvas currently expects.
        """
        # Normally the view.streamtree should have only one image anyway
        st = self.microscope_view.stream_tree
        images = st.getImages()

        # add the images in order
        ims = []
        for rgbim in images:
            # image is always centered, fitting the whole canvas
            wim = img.format_rgba_darray(rgbim)
            ims.append((wim, (0, 0), 0.1, False, None))
        self.set_images(ims)

    def _onViewImageUpdate(self, t):
        self._convertStreamsToImages()
        self.fit_to_content(recenter=True)
        wx.CallAfter(self.request_drawing_update)

    def update_drawing(self):
        super(AngularResolvedCanvas, self).update_drawing()

        if self.microscope_view:
            self._updateThumbnail()

    @wxlimit_invocation(2) # max 1/2 Hz
    @call_after  # needed as it accesses the DC
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

        self.microscope_view.thumbnail.value = wx.ImageFromBitmap(bitmap)
