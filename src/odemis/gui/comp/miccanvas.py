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

import cairo
from decorator import decorator
import logging
import numpy
from odemis import util, model
from odemis.acq import stream
from odemis.acq.stream import UNDEFINED_ROI, EMStream
from odemis.gui import BLEND_SCREEN, BLEND_DEFAULT
from odemis.gui.comp.canvas import CAN_ZOOM, CAN_DRAG, CAN_FOCUS, BitmapCanvas
from odemis.gui.comp.overlay.view import HistoryOverlay, PointSelectOverlay, MarkingLineOverlay
from odemis.gui.util import wxlimit_invocation, ignore_dead, img
from odemis.gui.util.img import format_rgba_darray
from odemis.model import VigilantAttributeBase
from odemis.util import units
import time
import weakref
import wx
from wx.lib.imageutils import stepColour

import odemis.gui as gui
import odemis.gui.comp.canvas as canvas
import odemis.gui.comp.overlay.view as view_overlay
import odemis.gui.comp.overlay.world as world_overlay
import odemis.gui.model as guimodel
import wx.lib.wxcairo as wxcairo


@decorator
def microscope_view_check(f, self, *args, **kwargs):
    """ This method decorator check if the microscope_view attribute is set """
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

        # Current (tool) mode. TODO: Make platform (secom/sparc) independent
        # and use listen to .tool (cf SparcCanvas)
        self.current_mode = None
        # None (all allowed) or a set of guimodel.TOOL_* allowed (rest is treated like NONE)
        self.allowed_modes = None

        self._previous_size = self.ClientSize

        # Overlays

        # Passive overlays that only display information, but offer no interaction
        self._crosshair_ol = None
        self._spotmode_ol = None
        self._fps_ol = None
        self._focus_overlay = None

        self.pixel_overlay = None
        self.points_overlay = None
        self.line_overlay = None
        self.dicho_overlay = None

        # play/pause icon
        self.play_overlay = view_overlay.PlayIconOverlay(self)
        self.add_view_overlay(self.play_overlay)

        # Unused at the moment
        self.zoom_overlay = None
        self.update_overlay = None

        self.background_brush = wx.BRUSHSTYLE_SOLID

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

        self.microscope_view.mpp.subscribe(self._on_view_mpp, init=True)
        self.microscope_view.view_pos.subscribe(self._onViewPos)
        # Update new position immediately, so that fit_to_content() directly
        # gets the correct center
        world_pos = self.physical_to_world_pos(self.microscope_view.view_pos.value)
        self._calc_bg_offset(world_pos)
        self.requested_world_pos = world_pos

        # any image changes
        self.microscope_view.lastUpdate.subscribe(self._on_view_image_update, init=True)

        # handle cross hair
        self.microscope_view.show_crosshair.subscribe(self._on_cross_hair_show, init=True)

        tab_data.main.debug.subscribe(self._on_debug, init=True)

        if tab_data.tool:
            # only create these overlays if they could be possibly used
            if guimodel.TOOL_POINT in tab_data.tool.choices:
                self.points_overlay = world_overlay.PointsOverlay(self)
                self.pixel_overlay = world_overlay.PixelSelectOverlay(self)
            if guimodel.TOOL_LINE in tab_data.tool.choices:
                self.line_overlay = world_overlay.SpectrumLineSelectOverlay(self)
            tab_data.tool.subscribe(self._on_tool, init=True)

    def _on_tool(self, tool_mode):
        """ Set the right mode and active overlays when a tool is selected """

        # A weird situation which should not happen
        if self.dragging:
            logging.error("Changing to mode (%s) while dragging is not supported!", tool_mode)
            return

        # Check if the desired tool mode is allowed
        if self.allowed_modes and tool_mode not in self.allowed_modes:
            logging.warn("Toolmode %s is not allowed and will be ignored!", tool_mode)
            tool_mode = guimodel.TOOL_NONE

        self.current_mode = tool_mode
        cursor = wx.STANDARD_CURSOR

        self._set_spot_mode(tool_mode)
        self._set_dichotomy_mode(tool_mode)
        self._set_point_select_mode(tool_mode)
        self._set_line_select_mode(tool_mode)

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
                self._crosshair_ol = view_overlay.CrossHairOverlay(self)
            self.add_view_overlay(self._crosshair_ol)
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
                self._fps_ol = view_overlay.TextViewOverlay(self)
                self._fps_ol.add_label("")
            self.add_view_overlay(self._fps_ol)
        elif self._fps_ol:
            self.remove_view_overlay(self._fps_ol)

        self.Refresh(eraseBackground=False)

    def _set_spot_mode(self, tool_mode):

        if not any([isinstance(s, EMStream) for s in self.microscope_view.stream_tree]):
            return

        use_world = hasattr(self._tab_data_model, 'spotPosition')
        if self._spotmode_ol is None:
            if use_world:
                spot_va = self._tab_data_model.spotPosition
                self._spotmode_ol = world_overlay.SpotModeOverlay(self, spot_va)
            else:
                spot_va = None
                self._spotmode_ol = view_overlay.SpotModeOverlay(self, spot_va)

        if tool_mode == guimodel.TOOL_SPOT:
            if use_world:
                self.add_world_overlay(self._spotmode_ol)
                # Activate the spot mode overlay when the canvas can be dragged, so the user can
                # position the spot. (By default, the spot is static in the center)
                if CAN_DRAG in self.abilities:
                    self._spotmode_ol.activate()
            else:
                self.add_view_overlay(self._spotmode_ol)
                self._spotmode_ol.activate()

        else:
            if use_world:
                self.remove_world_overlay(self._spotmode_ol)
            else:
                self.remove_view_overlay(self._spotmode_ol)
            self._spotmode_ol.deactivate()

        if self._spotmode_ol:
            self.microscope_view.show_crosshair.value = not tool_mode == guimodel.TOOL_SPOT

        self.Refresh(eraseBackground=False)

    def _set_dichotomy_mode(self, tool_mode):
        """ Activate the dichotomy overlay if needed """

        if tool_mode == guimodel.TOOL_DICHO:
            if not self.dicho_overlay:
                self.dicho_overlay = view_overlay.DichotomyOverlay(self,
                                                                   self._tab_data_model.dicho_seq)
                self.add_view_overlay(self.dicho_overlay)
            self.dicho_overlay.activate()
        elif self.dicho_overlay:
            self.dicho_overlay.deactivate()

    # TODO: move the logic of 'tool -> overlay' to the (tab?) controller
    # => different mode for "pixel" or "point"
    def _set_point_select_mode(self, tool_mode):
        """ Activate the required point selection overlay
        """

        if tool_mode == guimodel.TOOL_POINT:
            # if no stream => don't show anything
            # elif a spectrum stream is visible => pixel (spec)
            # elif a AR stream is present => points (AR)
            # elif a spectrum stream is present => pixel (spec)
            # else => don't show anything
            # TODO: shall we always display an overlay if stream is present?
            # Otherwise, when going from no stream to one stream, nothing
            # happens until tool is changed and changed back.

            stream_tree = self.microscope_view.stream_tree
            tab_streams = self._tab_data_model.streams.value
            if not len(stream_tree):
                return
            elif stream_tree.get_streams_by_type(stream.SpectrumStream):
                self.pixel_overlay.activate()
                self.add_world_overlay(self.pixel_overlay)
            elif any(isinstance(s, stream.ARStream) for s in tab_streams):
                self.add_world_overlay(self.points_overlay)
                self.points_overlay.activate()
            elif any(isinstance(s, stream.SpectrumStream) for s in tab_streams):
                self.pixel_overlay.activate()
                self.add_world_overlay(self.pixel_overlay)
        else:
            if self.pixel_overlay:
                self.pixel_overlay.deactivate()
                self.remove_world_overlay(self.pixel_overlay)
            if self.points_overlay:
                self.points_overlay.deactivate()
                self.remove_world_overlay(self.points_overlay)

    def _set_line_select_mode(self, tool_mode):
        """ Activate the required line selection overlay """

        if tool_mode == guimodel.TOOL_LINE:
            # Enable the Spectrum point select overlay when a spectrum stream
            # is attached to the view
            stream_tree = self.microscope_view.stream_tree
            if stream_tree.get_streams_by_type(stream.SpectrumStream):
                self.line_overlay.activate()
                self.add_world_overlay(self.line_overlay)
        else:
            if self.line_overlay:
                self.line_overlay.deactivate()
                self.remove_world_overlay(self.line_overlay)

    # END Overlay creation and activation

    def _get_ordered_images(self):
        """ Return the list of images to display, ordered bottom to top (=last to draw)

        The last image of the list will have the merge ratio applied (as opacity)

        """

        # The ordering is as follow:
        # * Optical images all together first, to be blended with screen operator
        #   The biggest one is set as first and drawn full opacity in order to
        #   even if the background is not black.
        # * Spectrum images all together (normally there is just one), and put
        #   as the end, so that the merge ratio applies to it.
        # * Other images (ie, SEM) going from the biggest to the smallest, so
        #   that the biggest one is at the bottom and displayed at full opacity.
        #   In that case it's normally fine to reorder the images wrt to the
        #   merge ratio because they are (typically) all the same type, the GUI
        #   widget is unspecifying anyway.
        # The merge ratio actually corresponds to the opacity of the last image drawn

        streams = self.microscope_view.getStreams()
        images_opt = []
        images_spc = []
        images_std = []

        for s in streams:
            if not s:
                # should not happen, but let's not completely fail on this
                logging.error("StreamTree has a None stream")
                continue

            if not hasattr(s, "image") or s.image.value is None:
                continue

            # FluoStreams are merged using the "Screen" method that handles colour
            # merging without decreasing the intensity.
            if isinstance(s, stream.OpticalStream):
                images_opt.append((s.image.value, BLEND_SCREEN, s.name.value))
            elif isinstance(s, (stream.SpectrumStream, stream.CLStream)):
                images_spc.append((s.image.value, BLEND_DEFAULT, s.name.value))
            else:
                images_std.append((s.image.value, BLEND_DEFAULT, s.name.value))

        # Sort by size, so that the biggest picture is first drawn (no opacity)
        def get_area(d):
            return numpy.prod(d[0].shape[0:2]) * d[0].metadata[model.MD_PIXEL_SIZE][0]

        images_opt.sort(key=get_area, reverse=True)
        images_spc.sort(key=get_area, reverse=True)
        images_std.sort(key=get_area, reverse=True)

        # Reset the first image to be drawn to the default blend operator to be
        # drawn full opacity (only useful if the background is not full black)
        if images_opt:
            images_opt[0] = (images_opt[0][0], BLEND_DEFAULT, images_opt[0][2])

        return images_opt + images_std + images_spc

    def _convert_streams_to_images(self):
        """ Temporary function to convert the StreamTree to a list of images as the canvas
        currently expects.

        """
        images = self._get_ordered_images()

        # add the images in order
        ims = []
        for rgbim, blend_mode, name in images:
            # TODO: convert to RGBA later, in canvas and/or cache the conversion
            # On large images it costs 100 ms (per image and per canvas)

            rgba_im = format_rgba_darray(rgbim)

            keepalpha = False
            scale = rgbim.metadata[model.MD_PIXEL_SIZE]
            pos = self.physical_to_world_pos(rgbim.metadata[model.MD_POS])
            rot = rgbim.metadata.get(model.MD_ROTATION, 0)
            shear = rgbim.metadata.get(model.MD_SHEAR, 0)
            flip = rgbim.metadata.get(model.MD_FLIP, 0)

            ims.append((rgba_im, pos, scale, keepalpha, rot, shear, flip, blend_mode, name))

        # TODO: Canvas needs to accept the NDArray (+ specific attributes recorded separately).
        self.set_images(ims)

        # For debug only:
        # if images:
        #     self._lastest_datetime = max(im[0].metadata.get(model.MD_ACQ_DATE, 0) for im in images)
        # else:
        #     self._lastest_datetime = 0

        # if self._lastest_datetime > 0:
        #     logging.debug("Updated canvas list %g s after acquisition",
        #                   time.time() - self._lastest_datetime)

        self.merge_ratio = self.microscope_view.stream_tree.kwargs.get("merge", 0.5)

    # FIXME: it shouldn't need to ignore deads, as the subscription should go
    # away as soon as it's destroyed. However, after SECOM acquisition, something
    # seems to keep reference to the SecomCanvas, which prevents it from being
    # fully destroyed.
    @ignore_dead
    def _on_view_image_update(self, t):
        # TODO: use the real streamtree functions,for now we call a conversion layer
        self._convert_streams_to_images()
        if self.fit_view_to_next_image and any([i is not None for i in self.images]):
            self.fit_view_to_content()
            self.fit_view_to_next_image = False
        # logging.debug("Will update drawing for new image")
        wx.CallAfter(self.request_drawing_update)

    def update_drawing(self):
        """ Update the drawing and thumbnail """
        # TODO: detect that the canvas is not visible, and so should no/less frequently be updated?
        # The difficulty is that it must be redrawn as soon as it's shown again.

        super(DblMicroscopeCanvas, self).update_drawing()

        if self.microscope_view:
            self.update_thumbnail()

    @wxlimit_invocation(2)  # max 1/2 Hz
    def update_thumbnail(self):
        if self.IsEnabled():
            img = self._get_img_from_buffer()
            if img is not None:
                self.microscope_view.thumbnail.value = img

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
        world_pos (float, float): the coordinates of the center of the buffer in
                                  world units
        """
        # in case we are not attached to a view yet (shouldn't happen)
        super(DblMicroscopeCanvas, self).recenter_buffer(world_pos)
        if self.microscope_view:
            physical_pos = self.world_to_physical_pos(world_pos)
            # This will call _onViewPos() -> recenter_buffer(), but as
            # recenter_buffer() has already been called with this position,
            # nothing will happen
            self.microscope_view.view_pos.value = physical_pos

    def on_center_position_changed(self, shift):
        """
        Called whenever the view position changes.

        shift (float, float): offset moved in world coordinates
        """
        if self.microscope_view:
            phys_shift = self.world_to_physical_pos(shift)
            self.microscope_view.moveStageBy(phys_shift)

    def fit_view_to_content(self, recenter=None):
        """ Adapts the MPP and center to fit to the current content

        recenter (None or boolean): If True, also recenter the view. If None, it
            will try to be clever, and only recenter if no stage is connected,
            as otherwise, it could cause an unexpected move.
        """
        if recenter is None:
            # recenter only if there is no stage attached
            recenter = not self.microscope_view.has_stage()

        super(DblMicroscopeCanvas, self).fit_to_content(recenter=recenter)

        # this will indirectly call _on_view_mpp(), but not have any additional effect
        if self.microscope_view:
            new_mpp = 1 / self.scale
            self.microscope_view.mpp.value = self.microscope_view.mpp.clip(new_mpp)

    def _on_view_mpp(self, mpp):
        """ Called when the view.mpp is updated """
        self.scale = 1 / mpp
        wx.CallAfter(self.request_drawing_update)

    # TODO: move to viewport?
    @property
    def horizontal_field_width(self):
        """ Return the field width of the canvas in meters

        :return: (None or float) Field width in meters
        """

        width = self.ClientSize.x
        height = self.ClientSize.y
        # trick: we actually return the smallest of the FoV dimensions, so
        # that we are sure the microscope image will fit fully (if it's square)
        if self.microscope_view and width:
            return self.microscope_view.mpp.value * min(width, height)

        return None

    @horizontal_field_width.setter
    def horizontal_field_width(self, hfw):
        """ Set the mpp of the microscope view according to the given HFW """

        # Trick: we use the smallest of the canvas dimensions to be sure the image
        # will fit.
        size = min(self.ClientSize)
        # TODO: return both FoV dimensions, and move this cleverness to the
        # controller, so that it can do the right thing even if the image is not
        # square.
        if self.microscope_view and size > 0:
            mpp = self.microscope_view.mpp.clip(hfw / size)
            self.microscope_view.mpp.value = mpp

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
        shift (float): relative amount of "virtual pixels" moved
            >0: toward up/right
            Note: "virtual pixel" is expected to already be converted based on the
            mouse movement and key context. So it can be different from the
            actual number of pixels that were moved by the mouse.
        """
        if axis == 1:
            phy_shift = self.microscope_view.moveFocusRel(shift)
            self._focus_overlay.add_shift(phy_shift, axis)

    @microscope_view_check
    def on_right_down(self, event):
        """ Process right mouse button down event

        In this class, we only manage the mouse cursor and the overlay that displays the right
        dragging behaviour. The actual dragging logic is handled in the super class.

        """
        if CAN_FOCUS in self.abilities and not self.dragging:
            # Create the overlay, on the first time it is needed
            if not self._focus_overlay:
                self._focus_overlay = self.add_view_overlay(view_overlay.FocusOverlay(self))
            # Note: Set the cursor before the super method is called.
            # There is a Ubuntu/wxPython related bug that SetCursor does not work once CaptureMouse
            # is called (which happens in the super method).
            self.set_dynamic_cursor(wx.CURSOR_SIZENS)
            self._focus_overlay.clear_shift()

        super(DblMicroscopeCanvas, self).on_right_down(event)

    def on_right_up(self, event):
        """ Process right mouse button release event

        Stop the focus timers and clear any visual indicators. The actual mouse dragging is cleared
        in the super class's method.

        """
        if CAN_FOCUS in self.abilities and self.right_dragging:
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
                softener = 0.1  # softer
            else:
                softener = 1

            linear_zone = 32  # px
            # We only care of the vertical position for the focus
            pos = evt.GetPositionTuple()
            # Flip the sign for vertical movement, as indicated in the
            # on_extra_axis_move docstring: up/right is positive
            shift = -(pos[1] - self._rdrag_init_pos[1])

            if abs(shift) <= linear_zone:
                value = shift
            else:
                ssquare = cmp(shift, 0) * (abs(shift) - linear_zone) ** 2
                value = shift + ssquare / linear_zone

            change = value - self._rdrag_prev_value[1]

            # Changing the extra axis start the focus timer
            if change:
                self.on_extra_axis_move(1, change * softener)
                self._rdrag_prev_value[1] = value

        super(DblMicroscopeCanvas, self).on_motion(evt)

    def world_to_physical_pos(self, pos):
        """ Translate world coordinates into physical coordinates.
        Works both for absolute and relative values.

        :param pos: (float, float) "world" coordinates
        :return: (float, float)
        """
        # The y value needs to be flipped between physical and world coordinates.
        return (pos[0], -pos[1])

    def physical_to_world_pos(self, phy_pos):
        """ Translate physical coordinates into world coordinates.
        Works both for absolute and relative values.

        :param phy_pos: (float, float) "physical" coordinates in m
        :return: (float, float)
        """
        # The y value needs to be flipped between physical and world coordinates.
        return phy_pos[0], -phy_pos[1]

    def _get_sem_rect(self):
        """
        Returns the (theoretical) scanning area of the SEM. Works even if the
        SEM has not send any image yet.
        returns (tuple of 4 floats): position in physical coordinates m (l, t, r,br)
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
        sem_rect = [sem_center[0] - sem_width[0] / 2,  # left
                    sem_center[1] - sem_width[1] / 2,  # top
                    sem_center[0] + sem_width[0] / 2,  # right
                    sem_center[1] + sem_width[1] / 2]  # bottom

        return sem_rect

    def convert_spot_ratio_to_phys(self, r_spot):
        if r_spot in (None, (None, None)):
            return None
        else:
            # convert relative position to physical position
            try:
                sem_rect = self._get_sem_rect()
            except AttributeError:
                logging.warning("Trying to convert a SEM ROI, but no SEM available")
                return None

        # In physical coordinates Y goes up, but in ROI, Y goes down => "1-"
        phys_pos = (
            sem_rect[0] + r_spot[0] * (sem_rect[2] - sem_rect[0]),
            sem_rect[1] + (1 - r_spot[1]) * (sem_rect[3] - sem_rect[1])
        )

        return phys_pos

    def convert_spot_phys_to_ratio(self, p_spot):
        """ Clip the physical spot to the sem data and convert it into a ratio

        returns:
            The clipped physical spot and the ratio

        """

        # Get the position of the overlay in physical coordinates
        if p_spot is None:
            return 0.5, 0.5

        # Position of the complete SEM scan in physical coordinates
        l, t, r, b = self._get_sem_rect()
        px, py = p_spot

        # Take only the intersection so that that ROA is always inside the SEM scan
        p_spot = (max(min(px, r), l), max(min(py, b), t))

        # Convert the ROI into relative value compared to the SEM scan
        # In physical coordinates Y goes up, but in ROI, Y goes down => "1-"
        r_spot = (
            (p_spot[0] - l) / (r - l),
            (p_spot[1] - t) / (b - t)
        )

        return p_spot, r_spot

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
        if rel_rect[2] > 1:  # if went too far
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

    def selection_to_real_size(self, start_w_pos, end_w_pos):
        w = abs(start_w_pos[0] - end_w_pos[0])
        h = abs(start_w_pos[1] - end_w_pos[1])
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

        self.default_margin = 0
        self.margins = (self.default_margin, self.default_margin)

        self.abilities = set()  # Cannot move, zoom...

        self.background_brush = wx.BRUSHSTYLE_SOLID

        # Point select overlay for stage navigation. Does not need to be assigned to any overlay
        # list, because it does not draw anything.
        self.point_select_overlay = PointSelectOverlay(self)

        # This canvas can have a special overlay for tracking position history
        self.history_overlay = None

        self.SetMinSize((400, 400))

    def _on_view_mpp(self, mpp):
        DblMicroscopeCanvas._on_view_mpp(self, mpp)
        self.fit_view_to_content(True)

    def setView(self, microscope_view, tab_data):
        super(OverviewCanvas, self).setView(microscope_view, tab_data)
        self.history_overlay = HistoryOverlay(self, tab_data.stage_history)
        self.add_view_overlay(self.history_overlay)

    @wxlimit_invocation(2)  # max 1/2 Hz
    def update_thumbnail(self):

        if not self.IsEnabled() or self.ClientSize.x * self.ClientSize.y <= 0:
            return  # nothing to update

        # We need to scale the thumbnail ourselves, instead of letting the
        # button handle it, because we need to be able to draw the history
        # overlay without it being rescaled afterwards

        # Create an image from the bitmap buffer
        image = wx.ImageFromBitmap(self._bmp_buffer)
        scaled_img = img.wxImageScaleKeepRatio(image, gui.VIEW_BTN_SIZE, wx.IMAGE_QUALITY_HIGH)
        ratio = min(gui.VIEW_BTN_SIZE[0] / image.Width,
                    gui.VIEW_BTN_SIZE[1] / image.Height)
        shift = ((gui.VIEW_BTN_SIZE[0] - self.ClientSize.x * ratio) / 2,
                 (gui.VIEW_BTN_SIZE[1] - self.ClientSize.y * ratio) / 2)

        dc = wx.MemoryDC()
        bitmap = wx.BitmapFromImage(scaled_img)
        dc.SelectObject(bitmap)

        ctx = wxcairo.ContextFromDC(dc)
        self.history_overlay.draw(ctx, ratio, shift)

        # close the DC, to be sure the bitmap can be used safely
        del dc

        scaled_img = wx.ImageFromBitmap(bitmap)
        self.microscope_view.thumbnail.value = scaled_img


class SecomCanvas(DblMicroscopeCanvas):
    pass


class SparcAcquiCanvas(DblMicroscopeCanvas):
    def __init__(self, *args, **kwargs):
        super(SparcAcquiCanvas, self).__init__(*args, **kwargs)

        self._roa = None  # The ROI VA of SEM concurrent stream, initialized on setView()
        self.roa_overlay = None

        self._dc_region = None  # The dcRegion VA of the SEM concurrent
        self.driftcor_overlay = None

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
            self.driftcor_overlay.activate()
        elif self.driftcor_overlay:
            self.driftcor_overlay.deactivate()
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

        # Associate the ROI of the SEM concurrent stream to the region of acquisition
        sem_stream = tab_data.semStream
        if sem_stream is None:
            raise KeyError("SEM concurrent stream not set, required for the SPARC acquisition")

        super(SparcAcquiCanvas, self).setView(microscope_view, tab_data)

        # Get the region of interest and link it to the ROA overlay

        self._roa = sem_stream.roi
        self.roa_overlay = world_overlay.RepetitionSelectOverlay(self, self._roa)
        self.add_world_overlay(self.roa_overlay)

        # Link drift correction region

        self._dc_region = sem_stream.dcRegion
        self.driftcor_overlay = world_overlay.RepetitionSelectOverlay(
            self, self._dc_region, colour=gui.SELECTION_COLOUR_2ND)
        self.add_world_overlay(self.driftcor_overlay)

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
        self.driftcor_overlay.on_roa(self._dc_region.value)


class SparcARCanvas(DblMicroscopeCanvas):
    """
    Special restricted version that displays the first stream always fitting
    the entire canvas.
    It also has a .flip attribute to flip horizontally and/or vertically the
    whole image if needed.
    """
    # TODO: could probably be done with a simple BitmapCanvas + fit_to_content?

    def __init__(self, *args, **kwargs):
        super(SparcARCanvas, self).__init__(*args, **kwargs)
        self.abilities -= {CAN_ZOOM, CAN_DRAG}
        # same as flip argument of set_images(): int with wx.VERTICAL or wx.HORIZONTAL
        self.flip = wx.VERTICAL  # TODO: default to 0 (and change in Viewport)

        self._goal_im_ref = None
        self._goal_wim = None

    def _reset_goal_im(self):
        """ Called when the goal_im is dereferenced """
        self._goal_wim = None

    # noinspection PyTypeChecker
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
            # Special trick to avoid regenerating the BGRA image for Goal all the time
            # TODO: make it generic
            if s.name.value == "Goal":
                prev_im = None if self._goal_im_ref is None else self._goal_im_ref()
                if self._goal_wim is None or prev_im is None or prev_im is not rgbim:
                    logging.debug("Converting goal image")
                    wim = format_rgba_darray(rgbim)
                    self._goal_im_ref = weakref.ref(rgbim, self._reset_goal_im)
                    self._goal_wim = wim
                else:
                    wim = self._goal_wim
            else:
                wim = format_rgba_darray(rgbim)

            keepalpha = (rgbim.shape[2] == 4)

            scale = rgbim.metadata[model.MD_PIXEL_SIZE]
            pos = (0, 0)  # the sensor image should be centered on the sensor center

            if s.name.value == "Goal":
                # goal image => add at the end
                ims.append((wim, pos, scale, keepalpha, None, None, self.flip, None, s.name.value))
            else:
                # add at the beginning
                ims[0] = (wim, pos, scale, keepalpha, None, None, self.flip, None, s.name.value)

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


class BarPlotCanvas(canvas.PlotCanvas):
    """
    A canvas to represent 1D data (not necessarily equally distributed), and
    provides an overlay to show the value corresponding to a given x position.
    It takes a set of coordinates (ordered along X).
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

        super(BarPlotCanvas, self).__init__(*args, **kwargs)

        # play/pause icon
        self.play_overlay = view_overlay.PlayIconOverlay(self)
        self.add_view_overlay(self.play_overlay)

        self.drag_init_pos = None

        self.SetBackgroundColour(stepColour(self.Parent.BackgroundColour, 50))
        self.SetForegroundColour(self.Parent.ForegroundColour)

        self.closed = canvas.PLOT_CLOSE_BOTTOM
        self.plot_mode = canvas.PLOT_MODE_BAR

        self.markline_overlay = view_overlay.MarkingLineOverlay(
            self,
            orientation=MarkingLineOverlay.HORIZONTAL | MarkingLineOverlay.VERTICAL)
        self.add_view_overlay(self.markline_overlay)
        self.markline_overlay.activate()

    def set_data(self, data, unit_x=None, unit_y=None, range_x=None, range_y=None):
        """ Subscribe to the x position of the overlay when data is loaded """

        super(BarPlotCanvas, self).set_data(data, unit_x, unit_y, range_x, range_y)

        if data:
            self.markline_overlay.v_pos.subscribe(self._map_to_plot_values, init=True)
            self.markline_overlay.activate()
        else:
            self.markline_overlay.v_pos.unsubscribe(self._map_to_plot_values)
            self.markline_overlay.deactivate()

    def clear(self):
        super(BarPlotCanvas, self).clear()
        self.val_x.value = None
        self.val_y.value = None
        self.markline_overlay.clear_labels()
        self.markline_overlay.deactivate()
        wx.CallAfter(self.update_drawing)

    # Event handlers

    def on_size(self, evt):
        """ Update the position of the focus line """
        super(BarPlotCanvas, self).on_size(evt)
        if None not in (self.val_x.value, self.val_y.value):
            pos = (self._val_x_to_pos_x(self.val_x.value), self._val_y_to_pos_y(self.val_y.value))
            self.markline_overlay.set_position(pos)

    def _map_to_plot_values(self, v_pos):
        """ Calculate the x and y *values* belonging to the x pixel position """

        if not self._data or v_pos is None:
            return

        v_posx, v_posy = v_pos

        self.val_x.value = self._pos_x_to_val_x(v_posx, snap=True)
        self.val_y.value = self._val_x_to_val_y(self.val_x.value, snap=True)

        pos = (v_posx, self._val_y_to_pos_y(self.val_y.value, self.data_prop[2], self.data_prop[3]))
        self.markline_overlay.set_position(pos)

        self.markline_overlay.x_label = units.readable_str(self.val_x.value, self.unit_x, 3)
        self.markline_overlay.y_label = units.readable_str(self.val_y.value, self.unit_y, 3)

        # self.Parent.Refresh()  # TODO: Does it need to be parent? is it needed at all?

    def setView(self, microscope_view, tab_data):
        """ Set the microscope_view that this canvas is displaying/representing
        Can be called only once, at initialisation.

        :param microscope_view:(model.MicroscopeView)
        :param tab_data: (model.MicroscopyGUIData)
        """
        # This is a kind of kludge, see mscviewport.MicroscopeViewport for details
        assert(self.microscope_view is None)

        self.microscope_view = microscope_view
        self._tab_data_model = tab_data

    @wxlimit_invocation(2)  # max 1/2 Hz
    def update_thumbnail(self):
        if self.IsEnabled():
            if self._data is None:
                self.microscope_view.thumbnail.value = None
            else:
                img = self._get_img_from_buffer()
                if img is not None:
                    self.microscope_view.thumbnail.value = img

    def update_drawing(self):
        super(BarPlotCanvas, self).update_drawing()

        if self.microscope_view:
            self.update_thumbnail()

    def get_y_value(self):
        """ Return the current y value """
        return self.val_y.value


class TwoDPlotCanvas(BitmapCanvas):
    """
    Canvas that shows 2D data and plots the value as intensity. IOW, it takes
    an image and scale it to fit the whole area.
    """

    def __init__(self, *args, **kwargs):

        super(TwoDPlotCanvas, self).__init__(*args, **kwargs)

        self.SetBackgroundColour(stepColour(self.Parent.BackgroundColour, 50))
        self.SetForegroundColour(self.Parent.ForegroundColour)

        self.microscope_view = None
        self._tab_data_model = None

        self.markline_overlay = view_overlay.MarkingLineOverlay(
            self,
            orientation=MarkingLineOverlay.HORIZONTAL | MarkingLineOverlay.VERTICAL)
        self.add_view_overlay(self.markline_overlay)

        self.background_brush = wx.BRUSHSTYLE_SOLID

    def draw(self):
        """ Map the image data to the canvas and draw it """

        if self.IsEnabled():
            im_data = self.images[0]
            ctx = wxcairo.ContextFromDC(self._dc_buffer)

            if im_data is not None:
                im_format = cairo.FORMAT_RGB24
                height, width, _ = im_data.shape

                # stride = cairo.ImageSurface.format_stride_for_width(im_format, width)

                # In Cairo a surface is a target that it can render to. Here we're going
                # to use it as the source for a pattern
                imgsurface = cairo.ImageSurface.create_for_data(im_data, im_format, width, height)

                # In Cairo a pattern is the 'paint' that it uses to draw
                surfpat = cairo.SurfacePattern(imgsurface)

                # Set the filter, so we get low quality but fast scaling
                surfpat.set_filter(cairo.FILTER_FAST)

                # Save and restore the transformation matrix, to prevent scale accumulation
                ctx.save()

                # Scale the width and height separately in such a way that the image data fill the
                # entire canvas
                ctx.scale(self.ClientSize.x / width, self.ClientSize.y / height)
                ctx.set_source(surfpat)
                ctx.paint()

                ctx.restore()
            else:
                # The background only needs to be drawn when there is no image data, since the image
                # data will always fill the entire view.
                self._draw_background(ctx)

    def update_drawing(self):
        """ Update the drawing and thumbnail """
        super(TwoDPlotCanvas, self).update_drawing()
        if self.microscope_view:
            self.update_thumbnail()

    def clear(self):
        super(TwoDPlotCanvas, self).clear()
        self.markline_overlay.clear_labels()
        self.markline_overlay.deactivate()
        wx.CallAfter(self.update_drawing)

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

    def set_2d_data(self, im_data):
        """ Set the data to be displayed

        TODO: Process the units for both the horizontal and vertical legends/axis
        TODO: Allow for both a horizontal and vertical domain

        """

        self.set_images([(im_data, (0.0, 0.0), 1.0, True, None, None, None, None, "Spatial Spectrum")])
        self.markline_overlay.clear_labels()
        self.markline_overlay.activate()

    @wxlimit_invocation(2)  # max 1/2 Hz
    def update_thumbnail(self):
        if self.IsEnabled():
            if all(i is None for i in self.images):
                self.microscope_view.thumbnail.value = None
            else:
                image = self._get_img_from_buffer()
                if image is not None:
                    self.microscope_view.thumbnail.value = image


class AngularResolvedCanvas(canvas.DraggableCanvas):
    """ Angle-resolved canvas """

    # TODO: it actually could be just a BitmapCanvas, but it needs
    # a (simple) fit_to_content()

    def __init__(self, *args, **kwargs):

        super(AngularResolvedCanvas, self).__init__(*args, **kwargs)

        self.default_margin = 0
        self.margins = (self.default_margin, self.default_margin)

        self.microscope_view = None
        self._tab_data_model = None
        self.abilities -= {CAN_DRAG, CAN_FOCUS}

        self.background_brush = wx.BRUSHSTYLE_SOLID  # background is always black

        # Overlays

        self.polar_overlay = view_overlay.PolarOverlay(self)
        self.polar_overlay.canvas_padding = 10
        self.add_view_overlay(self.polar_overlay)

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

        self.polar_overlay.activate()

    def _convert_streams_to_images(self):
        """ Temporary function to convert the StreamTree to a list of images as
        the canvas currently expects.
        """

        # Normally the view.streamtree should have only one image anyway
        streams = self.microscope_view.getStreams()

        # add the images in order
        ims = []
        for s in streams:
            # image is always centered, fitting the whole canvas
            wim = format_rgba_darray(s.image.value)
            ims.append((wim, (0, 0), (1, 1), False, None, None, None, None, s.name.value))

        self.set_images(ims)

    def _onViewImageUpdate(self, t):
        self._convert_streams_to_images()
        self.fit_to_content(recenter=True)
        wx.CallAfter(self.request_drawing_update)

    def update_drawing(self):
        super(AngularResolvedCanvas, self).update_drawing()
        if self.microscope_view:
            self.update_thumbnail()

    @wxlimit_invocation(2)  # max 1/2 Hz
    def update_thumbnail(self):
        if self.IsEnabled():
            img = self._get_img_from_buffer()
            if img is not None:
                self.microscope_view.thumbnail.value = img
