# -*- coding: utf-8 -*-
"""
Created on 6 Feb 2012

@author: Éric Piel

Copyright © 2012-2021 Éric Piel, Philip Winkler, Delmic

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

import cairo
from decorator import decorator
import logging
import numpy
from odemis import util, model
from odemis.acq import stream
from odemis.acq.stream import DataProjection
from odemis.gui import BLEND_SCREEN, BLEND_DEFAULT
from odemis.gui.comp.canvas import CAN_ZOOM, CAN_DRAG, CAN_FOCUS, CAN_MOVE_STAGE, BitmapCanvas
from odemis.gui.comp.overlay.view import HistoryOverlay, PointSelectOverlay, MarkingLineOverlay
from odemis.gui.util import wxlimit_invocation, ignore_dead, img, \
    call_in_wx_main
from odemis.gui.util.img import format_rgba_darray, apply_flip
from odemis.util import units, limit_invocation
from odemis.util.img import getBoundingBox
import scipy.ndimage
import time
import weakref
import wx
from wx.lib.imageutils import stepColour
import wx.lib.newevent

import odemis.gui as gui
import odemis.gui.comp.canvas as canvas
import odemis.gui.comp.overlay.view as view_overlay
import odemis.gui.comp.overlay.world as world_overlay
import odemis.gui.model as guimodel
import wx.lib.wxcairo as wxcairo


@decorator
def view_check(f, self, *args, **kwargs):
    """ This method decorator check if the view attribute is set """
    if self.view:
        return f(self, *args, **kwargs)


"""
Define a wx event that is triggered when the scale to fit view to content is triggered.
"""
evtFitViewToContent, EVT_FIT_VIEW_TO_CONTENT = wx.lib.newevent.NewEvent()

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

        self.view = None
        self._tab_data_model = None

        self.abilities |= {CAN_ZOOM, CAN_FOCUS, CAN_MOVE_STAGE}
        self.fit_view_to_next_image = True

        # Current (tool) mode. TODO: Make platform (secom/sparc) independent
        # and use listen to .tool (cf SparcCanvas)
        self.current_mode = None
        # None (all allowed) or a set of guimodel.TOOL_* allowed (rest is treated like NONE)
        self.allowed_modes = None

        # Overlays

        # Passive overlays that only display information, but offer no interaction
        self._crosshair_ol = None
        self._spotmode_ol = None
        self.mirror_ol = None
        self.ek_ol = None
        self._fps_ol = None
        self._last_frame_update = None
        self._focus_overlay = None
        self._pixelvalue_ol = None

        self.pixel_overlay = None
        self.points_overlay = None
        self.line_overlay = None
        self.dicho_overlay = None
        self.gadget_overlay = None
        self.cryofeature_overlay = None

        # play/pause icon
        self.play_overlay = view_overlay.PlayIconOverlay(self)
        self.add_view_overlay(self.play_overlay)

        # Unused at the moment
        self.zoom_overlay = None
        self.update_overlay = None

        self.background_brush = wx.BRUSHSTYLE_SOLID

        self.focus_timer = None

        # Image caching "dictionary": list of (weakref to DataArray, rgba image)
        # Cannot use a normal dict because the DataArrays (numpy.arrays) are not
        # hashable, so cannot they be used as keys of a dict.
        self._images_cache = []

        self._roa = None  # The ROI VA of SEM concurrent stream, initialized on setView()
        self.roa_overlay = None

        self._dc_region = None  # The ROI VA of the drift correction
        self.driftcor_overlay = None

        self.Bind(wx.EVT_WINDOW_DESTROY, self._on_destroy, source=self)

    def _on_destroy(self, evt):
        # FIXME: it seems like this object stays in memory even after being destroyed.
        # => need to make sure that the object is garbage collected
        # (= no more references) once it's not used.
        if self.view:
            # Drop references
            self.view = None

            # As the tab data model may stay longer, we need to make sure it
            # doesn't update the canvas anymore
            tab_data = self._tab_data_model
            tab_data.tool.unsubscribe(self._on_tool)
            tab_data.main.debug.unsubscribe(self._on_debug)
            self._tab_data_model = None

    def clear(self):
        super(DblMicroscopeCanvas, self).clear()
        # Reclaim some memory
        self._images_cache = []

    def setView(self, view, tab_data):
        """
        Set the view that this canvas is displaying/representing
        Can be called only once, at initialisation.

        :param view:(model.MicroscopeView)
        :param tab_data: (model.MicroscopyGUIData)
        """

        # This is a kind of kludge, see mscviewport.MicroscopeViewport for details
        assert(self.view is None)

        self.view = view
        self._tab_data_model = tab_data

        self.view.mpp.subscribe(self._on_view_mpp, init=True)
        self.view.view_pos.subscribe(self._onViewPos)
        # Update new position immediately, so that fit_to_content() directly
        # gets the correct center
        phys_pos = self.view.view_pos.value
        self._calc_bg_offset(phys_pos)
        self.requested_phys_pos = tuple(phys_pos)

        # Disable the linking of the view <> stage position if there is no stage
        if not self.view.has_stage():
            self.abilities.discard(CAN_MOVE_STAGE)

        # any image changes
        self.view.lastUpdate.subscribe(self._on_view_image_update, init=True)

        # handle cross hair
        self.view.show_crosshair.subscribe(self._on_cross_hair_show, init=True)

        self.view.interpolate_content.subscribe(self._on_interpolate_content, init=True)

        self.view.show_pixelvalue.subscribe(self._on_pixel_value_show, init=True)

        tab_data.main.debug.subscribe(self._on_debug, init=True)

        # Only create the overlays which could possibly be used
        tools_possible = set(tab_data.tool.choices)
        if self.allowed_modes:
            tools_possible &= self.allowed_modes

        if guimodel.TOOL_RULER in tools_possible or guimodel.TOOL_LABEL in tools_possible:
            self.gadget_overlay = world_overlay.GadgetOverlay(self, tab_data.tool)
            # Ruler selection overlay: always shown & active
            self.add_world_overlay(self.gadget_overlay)
            self.gadget_overlay.active.value = True

        if guimodel.TOOL_ROA in tools_possible:
            # Get the region of interest and link it to the ROA overlay
            self._roa = tab_data.roa
            self.roa_overlay = world_overlay.RepetitionSelectOverlay(self, self._roa,
                                                                     tab_data.fovComp)
            self.add_world_overlay(self.roa_overlay)

        if guimodel.TOOL_RO_ANCHOR in tools_possible:
            # Link drift correction region
            self._dc_region = tab_data.driftCorrector.roi
            self.driftcor_overlay = world_overlay.RepetitionSelectOverlay(self,
                self._dc_region, tab_data.fovComp, colour=gui.SELECTION_COLOUR_2ND)
            self.add_world_overlay(self.driftcor_overlay)

        if self.roa_overlay or self.driftcor_overlay:
            # Regions depend on the field of view (=pixelSize/magnification)
            if model.hasVA(tab_data.fovComp, "pixelSize"):
                tab_data.fovComp.pixelSize.subscribe(self._on_hw_fov)

        if guimodel.TOOL_POINT in tools_possible:
            self.points_overlay = world_overlay.PointsOverlay(self)
            self.pixel_overlay = world_overlay.PixelSelectOverlay(self)

        if guimodel.TOOL_LINE in tools_possible:
            self.line_overlay = world_overlay.SpectrumLineSelectOverlay(self)

        if guimodel.TOOL_FEATURE in tools_possible:
            self.cryofeature_overlay = world_overlay.CryoFeatureOverlay(self, tab_data)
            self.add_world_overlay(self.cryofeature_overlay)
            self.cryofeature_overlay.active.value = True

        tab_data.tool.subscribe(self._on_tool, init=True)

    @call_in_wx_main
    def _on_tool(self, tool_mode):
        """ Set the right mode and active overlays when a tool is selected """

        # A weird situation which should not happen
        if self.dragging:
            logging.error("Changing to mode (%s) while dragging is not supported!", tool_mode)
            return

        # Check if the desired tool mode is allowed
        if self.allowed_modes and tool_mode not in self.allowed_modes:
            # This can happen if only some views in a tab accepts a mode
            logging.info("Toolmode %s is not allowed and will be ignored", tool_mode)
            tool_mode = guimodel.TOOL_NONE

        self.set_default_cursor(wx.STANDARD_CURSOR)

        self.current_mode = tool_mode
        self._set_tool_mode(tool_mode)
        self.request_drawing_update()

    def _set_tool_mode(self, tool_mode):
        self._set_spot_mode(tool_mode)
        self._set_dichotomy_mode(tool_mode)
        self._set_point_select_mode(tool_mode)
        self._set_line_select_mode(tool_mode)

        self._set_roa_mode(tool_mode)
        self._set_dc_mode(tool_mode)

        # TODO: return the cursor? return whether a redraw/refresh is needed?

    # Overlay creation and activation

    def _on_cross_hair_show(self, activated):
        """ Activate the cross hair view overlay """
        if activated:
            if self._crosshair_ol is None:
                self._crosshair_ol = view_overlay.CenteredLineOverlay(self)
            self.add_view_overlay(self._crosshair_ol)
        elif self._crosshair_ol:
            self.remove_view_overlay(self._crosshair_ol)

        self.Refresh(eraseBackground=False)

    def _on_interpolate_content(self, activated):
        """ Activate or deactivate interpolation"""
        self.request_drawing_update()

    def _on_pixel_value_show(self, activated):
        """ Activate the pixelvalue view overlay"""
        if activated:
            if self._pixelvalue_ol is None:
                view = self.view
                self._pixelvalue_ol = view_overlay.PixelValueOverlay(self, view)
            self.add_view_overlay(self._pixelvalue_ol)
            self._pixelvalue_ol.active.value = True
        elif self._pixelvalue_ol:
            self._pixelvalue_ol.active.value = False
            self.remove_view_overlay(self._pixelvalue_ol)

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
        use_world = hasattr(self._tab_data_model, 'spotPosition')

        if tool_mode == guimodel.TOOL_SPOT:
            if self._spotmode_ol is None:
                if use_world:
                    spot_va = self._tab_data_model.spotPosition
                    self._spotmode_ol = world_overlay.SpotModeOverlay(self, spot_va,
                                                                      self._tab_data_model.fovComp)
                else:
                    spot_va = None
                    self._spotmode_ol = view_overlay.SpotModeOverlay(self, spot_va)

            if use_world:
                self.add_world_overlay(self._spotmode_ol)
                self._spotmode_ol.active.value = True
            else:
                self.add_view_overlay(self._spotmode_ol)
                self._spotmode_ol.active.value = True

        elif self._spotmode_ol:
            if use_world:
                self.remove_world_overlay(self._spotmode_ol)
            else:
                self.remove_view_overlay(self._spotmode_ol)
            self._spotmode_ol.active.value = False

        if self._spotmode_ol:
            self.view.show_crosshair.value = (not tool_mode == guimodel.TOOL_SPOT)

    def _set_dichotomy_mode(self, tool_mode):
        """ Activate the dichotomy overlay if needed """

        if tool_mode == guimodel.TOOL_DICHO:
            if not self.dicho_overlay:
                self.dicho_overlay = view_overlay.DichotomyOverlay(self,
                                                                   self._tab_data_model.dicho_seq)
                self.add_view_overlay(self.dicho_overlay)
            self.dicho_overlay.active.value = True
        elif self.dicho_overlay:
            self.dicho_overlay.active.value = False

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

            stream_tree = self.view.stream_tree
            tab_streams = self._tab_data_model.streams.value
            if not len(stream_tree):
                return
            elif stream_tree.get_projections_by_type(stream.SpectrumStream):
                self.pixel_overlay.active.value = True
                self.add_world_overlay(self.pixel_overlay)
            elif any(isinstance(s, stream.ARStream) for s in tab_streams):
                self.add_world_overlay(self.points_overlay)
                self.points_overlay.active.value = True
            elif any(isinstance(s, stream.SpectrumStream) for s in tab_streams):
                self.pixel_overlay.active.value = True
                self.add_world_overlay(self.pixel_overlay)
        else:
            if self.pixel_overlay:
                self.pixel_overlay.active.value = False
                self.remove_world_overlay(self.pixel_overlay)
            if self.points_overlay:
                self.points_overlay.active.value = False
                self.remove_world_overlay(self.points_overlay)

    def _set_line_select_mode(self, tool_mode):
        """ Activate the required line selection overlay """

        if tool_mode == guimodel.TOOL_LINE:
            # Enable the Spectrum point select overlay when a spectrum stream
            # is attached to the view
            stream_tree = self.view.stream_tree
            if stream_tree.get_projections_by_type(stream.SpectrumStream):
                self.line_overlay.active.value = True
                self.add_world_overlay(self.line_overlay)
        else:
            if self.line_overlay:
                self.line_overlay.active.value = False
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

        # Use the stream tree, to get the DataProjection if there is one
        streams = self.view.stream_tree.getProjections()
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

            image = s.image.value

            # FluoStreams are merged using the "Screen" method that handles colour
            # merging without decreasing the intensity.
            ostream = s.stream if isinstance(s, DataProjection) else s
            if isinstance(ostream, (stream.FluoStream, stream.StaticFluoStream, stream.CLStream, stream.FastEMOverviewStream)):
                images_opt.append((image, BLEND_SCREEN, s.name.value, s))
            elif isinstance(ostream, stream.SpectrumStream):
                images_spc.append((image, BLEND_DEFAULT, s.name.value, s))
            else:
                images_std.append((image, BLEND_DEFAULT, s.name.value, s))

        # Sort by size, so that the biggest picture is first drawn (no opacity)
        def get_area(d):
            try:
                # We use the stream, as for pyramidal image it'll give the whole
                # area (correctly) instead of just the field of view.
                s = d[3]
                bbox = s.getBoundingBox()
            except ValueError as ex:
                # This can happen in some rare cases if the stream was reset in-between
                logging.debug("image from %s s", time.time() - d[0].metadata[model.MD_ACQ_DATE])
                bbox = getBoundingBox(d[0])
            width = bbox[2] - bbox[0]
            height = bbox[3] - bbox[1]
            return width * height

        images_opt.sort(key=get_area, reverse=True)
        images_spc.sort(key=get_area, reverse=True)
        images_std.sort(key=get_area, reverse=True)

        return images_opt + images_std + images_spc

    def _format_rgba_darray_cached(self, da):
        """
        Return the RGBA version of a RGB(A) DataArray, optimized by re-using a
        the previous computed output (stored in ._images_cache)
        """
        for wda, rgba_da in self._images_cache:
            if wda() is da:
                return rgba_da
        return format_rgba_darray(da)

    def _convert_streams_to_images(self):
        """ Temporary function to convert the StreamTree to a list of images as the canvas
        currently expects.

        """
        images = self._get_ordered_images()

        # add the images in order
        ims = []
        im_cache = []
        for rgbim, blend_mode, name, _ in images:
            if isinstance(rgbim, tuple): # tuple of tuple of tiles
                if len(rgbim) == 0 or len(rgbim[0]) == 0:
                    continue
                first_tile = rgbim[0][0]
                md = first_tile.metadata
                new_array = []
                for tile_column in rgbim:
                    new_array_col = []
                    for tile in tile_column:
                        rgba_tile = self._format_rgba_darray_cached(tile)
                        im_cache.append((weakref.ref(tile), rgba_tile))
                        new_array_col.append(rgba_tile)
                        rgba_tile.metadata = md
                    new_array.append(tuple(new_array_col))
                # creates a 2D tuple with the converted tiles
                rgba_im = tuple(new_array)

                # Calculate the shape of the image composed of the tiles
                tiles_merged_shape = util.img.getTilesSize(rgba_im)
                # the center of the image composed of the tiles
                pos = util.img.getCenterOfTiles(rgba_im, tiles_merged_shape)
            else:
                # Get converted RGBA image from cache, or create it and cache it
                # On large images it costs 100 ms (per image and per canvas)
                rgba_im = self._format_rgba_darray_cached(rgbim)
                im_cache.append((weakref.ref(rgbim), rgba_im))

                md = rgbim.metadata
                pos = md[model.MD_POS]

            scale = md[model.MD_PIXEL_SIZE]
            rot = md.get(model.MD_ROTATION, 0)
            shear = md.get(model.MD_SHEAR, 0)
            flip = md.get(model.MD_FLIP, 0)

            # Replace the old cache, so the obsolete RGBA images can be garbage collected
            self._images_cache = im_cache

            keepalpha = False
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

        self.merge_ratio = self.view.stream_tree.kwargs.get("merge", 0.5)

    # FIXME: it shouldn't need to ignore deads, as the subscription should go
    # away as soon as it's destroyed. However, after SECOM acquisition, something
    # seems to keep reference to the Canvas, which prevents it from being
    # fully destroyed.
    @ignore_dead
    def _on_view_image_update(self, t):
        # TODO: use the real streamtree functions,for now we call a conversion layer
        self._convert_streams_to_images()
        if (self.fit_view_to_next_image and
            any(i is not None for i in self.images) and  # at least an image
            all(s > 1 for s in self.ClientSize)):  # at least visible
            self.fit_view_to_content()
            self.fit_view_to_next_image = False
        # logging.debug("Will update drawing for new image")
        wx.CallAfter(self.request_drawing_update)

    def update_drawing(self):
        """ Update the drawing and thumbnail """
        # TODO: detect that the canvas is not visible, and so should no/less frequently be updated?
        # The difficulty is that it must be redrawn as soon as it's shown again.

        super(DblMicroscopeCanvas, self).update_drawing()

        if self.view:
            self.update_thumbnail()

    @wxlimit_invocation(2)  # max 1/2 Hz
    def update_thumbnail(self):
        if self and self.IsEnabled():
            img = self._get_img_from_buffer()
            if img is not None:
                self.view.thumbnail.value = img

    def _onViewPos(self, phys_pos):
        """
        When the view position is updated: recenter the view
        phys_pos (tuple of 2 float): X/Y in physical coordinates (m)
        """
        # skip ourselves, to avoid asking the stage to move to (almost) the same position
        super(DblMicroscopeCanvas, self).recenter_buffer(phys_pos)

    def recenter_buffer(self, phys_pos):
        """
        Update the position of the buffer on the world
        phys_pos (float, float): the coordinates of the center of the buffer in
                                 physical units (m, with Y going up)
        """
        move_dict = self.view.clipToStageLimits({"x": phys_pos[0], "y": phys_pos[1]})
        phys_pos = (move_dict["x"], move_dict["y"])

        # in case we are not attached to a view yet (shouldn't happen)
        super(DblMicroscopeCanvas, self).recenter_buffer(phys_pos)
        if self.view:
            # This will call _onViewPos() -> recenter_buffer(), but as
            # recenter_buffer() has already been called with this position,
            # nothing will happen
            self.view.view_pos.value = phys_pos

    def on_center_position_changed(self, shift):
        """
        Called whenever the view position changes.

        shift (float, float): offset moved in physical coordinates
        """
        if self.view and CAN_MOVE_STAGE in self.abilities:
            self.view.moveStageBy(shift)

    def fit_view_to_content(self, recenter=None):
        """ Adapts the MPP and center to fit to the current content

        recenter (None or boolean): If True, also recenter the view. If None, it
            will try to be clever, and only recenter if no stage is connected,
            as otherwise, it could cause an unexpected move.
        """
        # TODO: this method should be on the View, and it'd update the view_pos
        # and mpp according to the streams (and stage)
        if recenter is None:
            # Do not recenter if the  view position is linked to the stage position
            recenter = CAN_MOVE_STAGE not in self.abilities

        self.fit_to_content(recenter=recenter)

        # this will indirectly call _on_view_mpp(), but not have any additional effect
        if self.view:
            new_mpp = 1 / self.scale
            self.view.mpp.value = self.view.mpp.clip(new_mpp)
            if recenter:
                self.view.view_pos.value = self.requested_phys_pos

    def fit_to_bbox(self, bbox):
        """
        Zoom in to the bounding box and recenter. Same as fit_view_to_content,
          but with explicit bounding box, so we can zoom to a specific position.
        bbox (4 floats): bounding box to be shown, defined as minx, miny, maxx,
          maxy positions in m.

        Note: Should be called from main GUI thread. Make sure caller of this method
        is running in the main GUI thread.
        """
        # compute mpp so that the bbox fits exactly the visible part
        w, h = abs(bbox[2] - bbox[0]), abs(bbox[3] - bbox[1])  # m

        # In case they are 0, don't go crazy, and just pretend it's tiny
        w = max(1e-12, w)
        h = max(1e-12, h)

        cs = self.ClientSize
        cw = max(1, cs[0])  # px
        ch = max(1, cs[1])  # px
        self.scale = min(ch / h, cw / w)  # pick the dimension which is shortest

        c = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        self.requested_phys_pos = c  # As recenter_buffer but without request_drawing_update

        if self.view:
            self.view.mpp.value = self.view.mpp.clip(1 / self.scale)
            self.view.view_pos.value = c

        self.request_drawing_update()

    def _on_view_mpp(self, mpp):
        """ Called when the view.mpp is updated """
        self.scale = 1 / mpp
        wx.CallAfter(self.request_drawing_update)

    @view_check
    def Zoom(self, inc, block_on_zero=False):
        """ Zoom by the given factor

        :param inc (float): scale the current view by 2^inc
        :param block_on_zero (boolean): if True, and the zoom goes from software
            downscaling to software upscaling, it will stop at no software scaling
            ex:  # 1 => *2 ; -1 => /2; 2 => *4...

        """

        scale = 2.0 ** inc
        prev_mpp = self.view.mpp.value
        # Clip within the range
        mpp = prev_mpp / scale

        if block_on_zero:
            # Check for every image
            for im, _ in self.view.stream_tree.getImages():
                try:
                    if isinstance(im, tuple):
                        # gets the metadata of the first tile
                        md = im[0][0].metadata
                    else:
                        md = im.metadata
                    im_mpp = md[model.MD_PIXEL_SIZE][0]
                    # did we just passed the image mpp (=zoom zero)?
                    if ((prev_mpp < im_mpp < mpp or prev_mpp > im_mpp > mpp) and
                            abs(prev_mpp - im_mpp) > 1e-15):  # for float error
                        mpp = im_mpp
                except KeyError:
                    pass

        self.view.mpp.value = self.view.mpp.clip(mpp)  # this will call _on_view_mpp()

    def on_knob_rotate(self, evt):
        """ Powermate knob rotation processor """

        if CAN_FOCUS in self.abilities:
            self._show_focus_overlay_timed()

            change = evt.step_value * 2  # magic constant that feels fast enough
            if evt.ShiftDown():
                change *= 0.1  # softer

            self.on_extra_axis_move(1, change)

        super(DblMicroscopeCanvas, self).on_knob_rotate(evt)

    def _show_focus_overlay_timed(self):
        """
        Show the focus overlay for a brief time (5s).
        If it's already shown, extends the duration it is shown.
        """
        # Stop the clear timer if one is running
        if self.focus_timer is not None:
            self.focus_timer.Stop()

        if not self._focus_overlay:
            self._focus_overlay = self.add_view_overlay(view_overlay.FocusOverlay(self))

        # Set a timer to clear the overlay in x seconds
        self.focus_timer = wx.CallLater(5000, self._hide_focus_overlay)

    def _hide_focus_overlay(self):
        """ Clear the focus overlay after the focus timer has ran out """
        if self._focus_overlay:
            self._focus_overlay.clear_shift()
        self.focus_timer = None

    # Zoom/merge management
    def on_wheel(self, evt):
        """ Process user mouse wheel events

        If able and without modifiers, the Canvas will zoom in/out
        If the Ctrl key is down, the merge ratio of the visible layers will be adjusted.

        """

        change = evt.GetWheelRotation() / evt.GetWheelDelta()
        if evt.ShiftDown():
            change *= 0.1  # softer

        if evt.CmdDown():  # = Ctrl on Linux/Win or Cmd on Mac
            ratio = self.view.merge_ratio.value + (change * 0.1)
            # clamp
            ratio = sorted(self.view.merge_ratio.range + (ratio,))[1]
            self.view.merge_ratio.value = ratio
        else:
            if CAN_ZOOM in self.abilities:
                self.Zoom(change, block_on_zero=evt.ShiftDown())

        super(DblMicroscopeCanvas, self).on_wheel(evt)

    _key_to_zoom = {
        ord("+"): 1,  # scale, => FoV x 2
        ord("="): 1,  # On US keyboards, = is the same key as +, but without shift
        ord("-"):-1,  # scale, => FoV / 2
        ord("_"):-1,  # On US keyboards, _ is the same key as -, but with shift
    }

    _key_to_focus = {
        wx.WXK_PAGEUP: 10,  # px
        wx.WXK_PAGEDOWN:-10,  # px
    }

    def on_char(self, evt):
        """ Process a key stroke """

        ukey = evt.GetUnicodeKey()
        if CAN_ZOOM in self.abilities and ukey in self._key_to_zoom:
            change = self._key_to_zoom[ukey]
            if evt.ShiftDown():
                block_on_zero = True
                change *= 0.1  # softer
            else:
                block_on_zero = False

            self.Zoom(change, block_on_zero)
            return

        key = evt.GetKeyCode()
        if CAN_FOCUS in self.abilities and key in self._key_to_focus:
            self._show_focus_overlay_timed()

            change = self._key_to_focus[key]
            if evt.ShiftDown():
                change *= 0.1  # softer

            self.on_extra_axis_move(1, change)
            return

        super(DblMicroscopeCanvas, self).on_char(evt)

    @view_check
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
            phy_shift = self.view.moveFocusRel(shift)
            self._focus_overlay.add_shift(phy_shift, axis)

    @view_check
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
            if evt.ShiftDown():
                softener = 0.1  # softer
            else:
                softener = 1

            # We only care of the vertical position for the focus
            pos = evt.Position
            # Flip the sign for vertical movement, as indicated in the
            # on_extra_axis_move docstring: up/right is positive
            shift = -(pos[1] - self._rdrag_init_pos[1])
            change = shift - self._rdrag_prev_value[1]

            # Changing the extra axis start the focus timer
            if change:
                self.on_extra_axis_move(1, change * softener)
                self._rdrag_prev_value[1] = shift

        super(DblMicroscopeCanvas, self).on_motion(evt)

    def draw(self):
        """ Redraw the buffer while calculating the number of frames we *could* display

        The fps value is an indication of how many times we can draw per second and not the actual
        number of frames displayed on screen!

        """

        interpolate_data = False if self.view is None else self.view.interpolate_content.value
        if self._fps_ol:
            if self._last_frame_update is None:
                self._last_frame_update = time.time()
            super(DblMicroscopeCanvas, self).draw(interpolate_data=interpolate_data)
            now = time.time()

            try:
                dur = now - self._last_frame_update
                fps = 1 / dur
                self._fps_ol.labels[0].text = u"%s fps" % units.readable_str(fps, sig=3)
            except ZeroDivisionError:
                self._fps_ol.labels[0].text = u"∞ fps"
            self._last_frame_update = now
        else:
            super(DblMicroscopeCanvas, self).draw(interpolate_data=interpolate_data)

    def _getContentBoundingBox(self):
        """
        return (4 floats or Nones): ltrb in m. The physical position of the content
        or 4 Nones if no content is present.
        """
        # Find bounding box of all the content
        bbox = (None, None, None, None)  # ltrb in m
        if self.view is not None:
            streams = self.view.stream_tree.getProjections()
            for s in streams:
                try:
                    s_bbox = s.getBoundingBox()
                except ValueError:
                    continue  # Stream has no data (yet)
                if bbox[0] is None:
                    bbox = s_bbox
                else:
                    bbox = (min(bbox[0], s_bbox[0]), min(bbox[1], s_bbox[1]),
                            max(bbox[2], s_bbox[2]), max(bbox[3], s_bbox[3]))

        return bbox

    # TODO: just return best scale and center? And let the caller do what it wants?
    # It would allow to decide how to redraw depending if it's on size event or more high level.
    def fit_to_content(self, recenter=False):
        """ Adapt the scale and (optionally) center to fit to the current content
        recenter: (boolean) If True, also recenter the view.
        """
        # TODO: take into account the dragging. For now we skip it (is unlikely to happen anyway)

        bbox = self._getContentBoundingBox()
        if bbox[0] is None:
            return  # no image => nothing to do

        # if no recenter, increase bbox so that its center is the current center
        if not recenter:
            c = self.requested_phys_pos  # think ahead, use the next center pos
            hw = max(abs(c[0] - bbox[0]), abs(c[0] - bbox[2]))
            hh = max(abs(c[1] - bbox[1]), abs(c[1] - bbox[3]))
            bbox = [c[0] - hw, c[1] - hh, c[0] + hw, c[1] + hh]

        # compute mpp so that the bbox fits exactly the visible part
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]  # m
        if w == 0 or h == 0:
            logging.warning("Weird image size of %fx%f m", w, h)
            return  # no image
        cs = self.ClientSize
        cw = max(1, cs[0])  # px
        ch = max(1, cs[1])  # px
        self.scale = min(ch / h, cw / w)  # pick the dimension which is shortest

        # TODO: avoid aliasing when possible by picking a round number for the
        # zoom level (for the "main" image) if it's ±10% of the target size

        if recenter:
            c = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
            self.requested_phys_pos = c  # As recenter_buffer but without request_drawing_update

        wx.CallAfter(self.request_drawing_update)

    def _set_roa_mode(self, tool_mode):
        if tool_mode == guimodel.TOOL_ROA:
            self.roa_overlay.active.value = True
        elif self.roa_overlay:
            self.roa_overlay.active.value = False

    def _set_dc_mode(self, tool_mode):
        if tool_mode == guimodel.TOOL_RO_ANCHOR:
            self.driftcor_overlay.active.value = True
        elif self.driftcor_overlay:
            self.driftcor_overlay.active.value = False

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

    def _on_hw_fov(self, pxs):
        """
        Called when the FoV of the component linked to the ROIs changes.
          The notification happens when the pixelSize is updated.
        pxs (float, float): the new pixelSize
        """
        # The FoV changes, so the (relative) ROA is different.
        # Either we update the ROA so that physically it stays the same, or
        # we update the selection so that the ROA stays the same. It's probably
        # that the user has forgotten to set the magnification before, so let's
        # pick solution 2.

        # TODO: acq.stream.RepetitionStream has something similar, as it has an
        # extra attribute pixelSize which is linked to the hw pixelSize.
        # Probably both codes are at the wrong place. It should go into a
        # controller in the GUI. => drop RepetitionStream.pixelSize and provide
        # something equivalent in the GUI layer, and in that layer, update
        # pixelSize whenever the fovComp.pixelSize changes.
        if self.roa_overlay:
            self.roa_overlay.on_roa(self._roa.value)
        if self.driftcor_overlay:
            self.driftcor_overlay.on_roa(self._dc_region.value)


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

        # Disable certain tools in overview. Don't list ROA, ROI, or spot mode
        self.allowed_modes = guimodel.ALL_TOOL_MODES - {guimodel.TOOL_SPOT,
                                                        guimodel.TOOL_ROA,
                                                        guimodel.TOOL_ROI,
                                                        guimodel.TOOL_RO_ANCHOR}

        self.SetMinSize((400, 400))

    def _on_view_mpp(self, mpp):
        DblMicroscopeCanvas._on_view_mpp(self, mpp)
        self.fit_view_to_content(True)

    def setView(self, view, tab_data):
        super(OverviewCanvas, self).setView(view, tab_data)
        self.history_overlay = HistoryOverlay(self, tab_data.stage_history)
        self.add_view_overlay(self.history_overlay)

    @wxlimit_invocation(2)  # max 1/2 Hz
    def update_thumbnail(self):

        if not self or 0 in self.ClientSize:
            return  # nothing to update

        # We need to scale the thumbnail ourselves, instead of letting the
        # button handle it, because we need to be able to draw the history
        # overlay without it being rescaled afterwards

        image = self._get_img_from_buffer()
        scaled_img = img.wxImageScaleKeepRatio(image, gui.VIEW_BTN_SIZE, wx.IMAGE_QUALITY_HIGH)
        ratio = min(gui.VIEW_BTN_SIZE[0] / image.Width,
                    gui.VIEW_BTN_SIZE[1] / image.Height)
        shift = ((gui.VIEW_BTN_SIZE[0] - self.ClientSize.x * ratio) / 2,
                 (gui.VIEW_BTN_SIZE[1] - self.ClientSize.y * ratio) / 2)

        dc = wx.MemoryDC()
        bitmap = wx.Bitmap(scaled_img)
        dc.SelectObject(bitmap)

        ctx = wxcairo.ContextFromDC(dc)
        self.history_overlay.draw(ctx, ratio, shift)

        # close the DC, to be sure the bitmap can be used safely
        del dc

        scaled_img = bitmap.ConvertToImage()
        self.view.thumbnail.value = scaled_img


class SparcARCanvas(DblMicroscopeCanvas):
    """
    Special restricted version that displays the first stream always fitting
    the entire canvas (and without taking into account rotation/shear).
    It also has a .flip attribute to flip horizontally and/or vertically the
    whole image if needed.
    """
    # TODO: could probably be done with a simple BitmapCanvas + fit_to_content?

    def __init__(self, *args, **kwargs):
        super(SparcARCanvas, self).__init__(*args, **kwargs)
        self.abilities -= {CAN_ZOOM, CAN_DRAG}
        self.allowed_modes = {guimodel.TOOL_NONE}

        # same as flip argument of set_images(): int with wx.VERTICAL and/or wx.HORIZONTAL or just use MD_FLIP
        self.flip = 0

    def _convert_streams_to_images(self):
        """
        Same as the overridden method, but ensures the goal image keeps the alpha
        and is displayed second. Also force the mpp to be the one of the sensor.
        """
        streams = self.view.stream_tree.getProjections()
        ims = []

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
            wim = format_rgba_darray(rgbim)
            keepalpha = (rgbim.shape[2] == 4)
            scale = rgbim.metadata[model.MD_PIXEL_SIZE]
            pos = (0, 0)  # the sensor image should be centered on the sensor center

            # TODO: make the blending mode an option
            ims.append((wim, pos, scale, keepalpha, None,
                        None, self.flip, BLEND_SCREEN, s.name.value))

        # normal images at the beginning, goal image at the end
        self.set_images(ims)

        # set merge_ratio
        self.merge_ratio = self.view.stream_tree.kwargs.get("merge", 1)

        # always refit to image (for the rare case it has changed size)
        self.fit_view_to_content(recenter=False)

    def on_size(self, event):
        # refit image
        self.fit_view_to_content(recenter=False)
        # Skip DblMicroscopeCanvas.on_size which plays with mpp
        canvas.DraggableCanvas.on_size(self, event)

    def fit_to_content(self, recenter=False):
        # Override the default function to _not_ move the center, as we always
        # display everything at 0,0.

        bbox = self._getContentBoundingBox()
        if bbox[0] is None:
            return  # no image => nothing to do

        # compute mpp so that the bbox fits exactly the visible part
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]  # m
        if w == 0 or h == 0:
            logging.warning("Weird image size of %fx%f m", w, h)
            return  # no image
        cs = self.ClientSize
        cw = max(1, cs[0])  # px
        ch = max(1, cs[1])  # px
        self.scale = min(ch / h, cw / w)  # pick the dimension which is shortest

        # Force it back to the center (in case recenter_buffer was called)
        self.requested_phys_pos = (0, 0)
        wx.CallAfter(self.request_drawing_update)


class BarPlotCanvas(canvas.PlotCanvas):
    """
    A canvas to represent 1D data (not necessarily equally distributed), and
    provides an overlay to show the value corresponding to a given x position.
    It takes a set of coordinates (ordered along X).
    """

    def __init__(self, *args, **kwargs):
        self.view = None
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

        self.markline_overlay = view_overlay.MarkingLineOverlay(self,
            orientation=MarkingLineOverlay.HORIZONTAL | MarkingLineOverlay.VERTICAL,
            map_y_from_x=True)
        self.add_view_overlay(self.markline_overlay)

    def set_data(self, data, unit_x=None, unit_y=None, range_x=None, range_y=None):
        """ Subscribe to the x position of the overlay when data is loaded """

        super(BarPlotCanvas, self).set_data(data, unit_x, unit_y, range_x, range_y)

        if data is not None:
            self.markline_overlay.active.value = True
        else:
            self.markline_overlay.active.value = False

    def clear(self):
        super(BarPlotCanvas, self).clear()
        self.markline_overlay.clear_labels()
        self.markline_overlay.active.value = False
        wx.CallAfter(self.update_drawing)

    def setView(self, view, tab_data):
        """ Set the view that this canvas is displaying/representing
        Can be called only once, at initialisation.

        :param view:(model.MicroscopeView)
        :param tab_data: (model.MicroscopyGUIData)
        """
        # This is a kind of kludge, see mscviewport.MicroscopeViewport for details
        assert(self.view is None)

        self.view = view
        self._tab_data_model = tab_data

    @wxlimit_invocation(2)  # max 1/2 Hz
    def update_thumbnail(self):
        if self and self.IsEnabled():
            if self._data is None:
                self.view.thumbnail.value = None
            else:
                img = self._get_img_from_buffer()
                if img is not None:
                    self.view.thumbnail.value = img

    def update_drawing(self):
        super(BarPlotCanvas, self).update_drawing()

        if self.view:
            self.update_thumbnail()
            
class NavigableBarPlotCanvas(BarPlotCanvas):
    
    """
    A plot canvas that can be navigated by the user.

    The x and y scales can be dragged with the left mouse button, and can be zoomed
    with the mouse wheel.The plot can be panned with a middle mouse button drag.
    Therefore, the data range and display range are different.

    The plot also re-samples large data sets to speed up their display.

    API:
    Read only values
        .data_xrange: The x range of the data
        .data_yrange: The y range of the data
        .display_xrange: The currently displayed x range
        .display_yrange: The currently displayed y range
    Methods
        .set_data(...): Functions similar to the parent
        .set_1d_data(...): Functions similar to the parent
        .set_ranges(x_range, y_range): Set the display range of the plot. x_range and y_range
            are range tuples.
        .reset_ranges(): Resets the display ranges to the data ranges
        .refresh_plot(): Redraws the plot with the newest parameters.
            This function takes a window of the data set based on the current display range
            and resamples large data sets so that they can be displayed quickly without lag.

    Note: fit_view_to_content does not function in the same way as the parent. Because the plot ranges
        are usually controlled by VA's in the viewport, this method now posts
        a evtFitViewToContent event, which is intercepted by the parent viewport.

    """

    def __init__(self, *args, **kwargs):
        super(NavigableBarPlotCanvas, self).__init__(*args, **kwargs)
        self.abilities |= {CAN_DRAG}
        self._data_buffer = None  # numpy arrays with the plot X and Y data
        self.display_xrange = None  # range tuple of floats
        self.display_yrange = None  # range tuple of floats
        self.data_xrange = None  # range tuple of floats
        self.data_yrange = None  # range tuple of floats

    # TODO: only provide set_data(), and store the data as a single 2xN numpy array.
    def set_data(self, data, unit_x=None, unit_y=None, range_x=None, range_y=None,
                 display_xrange=None, display_yrange=None):
        xs, ys = list(zip(*data))
        self.set_1d_data(xs, ys, unit_x, unit_y, range_x, range_y, display_xrange, display_yrange)

    def set_1d_data(self, xs, ys, unit_x=None, unit_y=None, range_x=None, range_y=None,
                    display_xrange=None, display_yrange=None):
        if len(xs) != len(ys):
            msg = "X and Y list are of unequal length. X: %s, Y: %s, Xs: %s..."
            raise ValueError(msg % (len(xs), len(ys), str(xs)[:30]))
        self._data_buffer = (numpy.array(xs), numpy.array(ys))
        self.unit_x = unit_x
        self.unit_y = unit_y

        if range_x is None:
            # It's easy, as the data is ordered
            range_x = (xs[0], xs[-1])

        # If a range is not given, we calculate it from the data
        if range_y is None:
            min_y = float(min(self._data_buffer[1]))
            max_y = float(max(self._data_buffer[1]))
            range_y = (min_y, max_y)

        self.data_xrange = range_x
        self.data_yrange = range_y
        self.display_xrange = range_x if display_xrange is None else display_xrange
        self.display_yrange = range_y if display_yrange is None else display_yrange
        
        self.refresh_plot()

    def clear(self):
        self._data_buffer = None
        super(NavigableBarPlotCanvas, self).clear()

    # TODO: refactor so that the viewports has fit_view_to_content(), and they
    # are in charge of calling the right function in the canvas
    def fit_view_to_content(self, recenter=None):
        # note - need to do this via an event to the viewport. Otherwise it doesn't
        # set the axes properly
        evt = evtFitViewToContent()
        wx.PostEvent(self, evt)

    def reset_ranges(self):
        if self._data_buffer is None:
            return

        self.set_ranges(self.data_xrange, self.data_yrange)

    def _resample_plot(self, xs, ys, threshold):
        """
        Re-sample the data if there are more points than the threshold
        xs, ys: two 1-D arrays with data
        threshold: the minimum number of points for a re-sampling to occur
        """
        # Re-sample data based on window size
        if len(xs) > threshold:
            rs_factor = len(xs) // (threshold // 2)
            logging.debug("Re-sampling data with factor %d", rs_factor)

            """
            # TODO: One we support Scipy 1.2.1, we can use this function
            xr = xs[::rs_factor]
            # Add the peaks back into the data set so that features are displayed
            # peaks, _ = scipy.signal.find_peaks(ys, height=0)
            xr = numpy.append(xr, peaks)
            xr = numpy.unique(xr)
            xr.sort()
            """
            n = len(xs)
            rem = n % rs_factor
            new_shape = (n // rs_factor, rs_factor)

            # Reshape the array into bins and take the max of each bin
            xr = numpy.reshape(xs[:(n - rem)], new_shape)
            xr = numpy.amax(xr, axis=1)

            # in case there are remaining items left at the end (could not fit into
            # an equally sized bin) add these to the end of the re-sampled dataset
            rem_items = xs[(n - rem):n]
            if len(rem_items) > 0:
                xr = numpy.append(xr, max(rem_items))

            yr = numpy.interp(xr, xs, ys)

            return xr, yr
        else:
            return xs, ys

    @limit_invocation(0.05)  # Max 20 Hz
    def refresh_plot(self):
        """
        Refresh the displayed data in the plot.
        """
        if self._data_buffer is None:
            return

        (xst , yst) = self._data_buffer

        # Using the selected horizontal range, define a window
        # for display of the data.
        lo, hi = self.display_xrange

        # Find the index closest to the range extremes
        lox = numpy.searchsorted(xst, lo, side="left")
        hix = numpy.searchsorted(xst, hi, side="right")

        # normal Case
        if lox != hix:
            xs = xst[lox:hix]
            ys = yst[lox:hix]
        # otherwise we are zoomed in so much that only a single point is visible.
        # Therefore just display a single bar that fills the the panel
        else:
            xs = [(hi + lo) / 2]
            ys = [yst[lox]]

        # Add a few points onto the beginning and end of the array
        # to prevent gaps in the data from appearing
        if lox > 0 and xs[0] != lo:
            xs = numpy.insert(xs, 0, xst[lox - 1])
            ys = numpy.insert(ys, 0, yst[lox - 1])

        if hix < len(xst) and xs[-1] != hi:
            xs = numpy.append(xs, xst[hix])
            ys = numpy.append(ys, yst[hix])

        # Resample the plot (if necessary) for the view, and restack the data
        xs, ys = self._resample_plot(xs, ys, self.ClientSize.x * 10)
        temp_data = numpy.column_stack((xs, ys))

        if temp_data.size == 0:
            temp_data = numpy.empty((1, 2))

        super(NavigableBarPlotCanvas, self).set_data(temp_data, self.unit_x,
                      self.unit_y, self.display_xrange, self.display_yrange)

        self.Refresh()

    def set_ranges(self, x_range, y_range):
        """
        Set the ranges of the plot.
        """
        self.display_xrange = x_range
        self.display_yrange = y_range

        self.refresh_plot()

    def on_size(self, evt):
        # Reset the data display to ensure the data resampling is done
        self.refresh_plot()
        super(NavigableBarPlotCanvas, self).on_size(evt)


class TwoDPlotCanvas(BitmapCanvas):
    """
    Canvas that shows 2D data and plots the value as intensity. IOW, it takes
    an image and scale it to fit the whole area.
    """

    def __init__(self, *args, **kwargs):

        super(TwoDPlotCanvas, self).__init__(*args, **kwargs)

        self.SetBackgroundColour(stepColour(self.Parent.BackgroundColour, 50))
        self.SetForegroundColour(self.Parent.ForegroundColour)

        self.view = None
        self._tab_data_model = None

        self.unit_x = None
        self.unit_y = None
        self.range_x = None  # list of floats, from left to right (at least two)
        self.range_y = None  # list of floats, from top to bottom (at least two)

        self._crosshair_ol = None
        self._pixelvalue_ol = None

        self.markline_overlay = view_overlay.MarkingLineOverlay(self,
            orientation=MarkingLineOverlay.HORIZONTAL | MarkingLineOverlay.VERTICAL)
        self.add_view_overlay(self.markline_overlay)

        # play/pause icon
        self.play_overlay = view_overlay.PlayIconOverlay(self)
        self.add_view_overlay(self.play_overlay)

        self.background_brush = wx.BRUSHSTYLE_SOLID

    def draw(self):
        """ Map the image data to the canvas and draw it """

        if self.IsEnabled():
            im_data = self.images[0]
            ctx = wxcairo.ContextFromDC(self._dc_buffer)

            if im_data is not None:
                im_format = cairo.FORMAT_RGB24
                height, width, depth = im_data.shape
                csize = self.ClientSize

                # Resize images too big for Cairo
                if height > 4096 or width > 4096:
                    small_shape = min(height, csize[1]), min(width, csize[0]), depth
                    scale = tuple(n / o for o, n in zip(im_data.shape, small_shape))
                    im_data = scipy.ndimage.interpolation.zoom(im_data, zoom=scale,
                                       output=im_data.dtype, order=1, prefilter=False)
                    height, width, depth = im_data.shape

                # In Cairo a surface is a target that it can render to. Here we're going
                # to use it as the source for a pattern
                imgsurface = cairo.ImageSurface.create_for_data(im_data, im_format, width, height)

                # In Cairo a pattern is the 'paint' that it uses to draw
                surfpat = cairo.SurfacePattern(imgsurface)

                # Set the filter, so we get low quality but fast scaling
                surfpat.set_filter(cairo.FILTER_FAST)

                # Save and restore the transformation matrix, to prevent scale accumulation
                ctx.save()

                apply_flip(ctx, im_data.metadata["dc_flip"], (0, 0, csize[0], csize[1]))
                # Scale the width and height separately in such a way that the image data fill the
                # entire canvas
                ctx.scale(csize[0] / width, csize[1] / height)
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
        if self.view:
            self.update_thumbnail()

    def clear(self):
        super(TwoDPlotCanvas, self).clear()
        self.range_x = None
        self.range_y = None
        self.markline_overlay.clear_labels()
        self.markline_overlay.active.value = False
        wx.CallAfter(self.update_drawing)

    def setView(self, view, tab_data):
        """ Set the view that this canvas is displaying/representing
        Can be called only once, at initialisation.

        :param view:(model.MicroscopeView)
        :param tab_data: (model.MicroscopyGUIData)
        """
        # This is a kind of kludge, see viewport.MicroscopeViewport for details
        assert(self.view is None)

        self.view = view
        self._tab_data_model = tab_data

        # handle cross hair
        self.view.show_crosshair.subscribe(self._on_cross_hair_show, init=True)
        self.view.show_pixelvalue.subscribe(self._on_pixel_value_show, init=True)

    def _on_cross_hair_show(self, activated):
        """ Activate the cross hair view overlay """
        if activated:
            if self._crosshair_ol is None:
                self._crosshair_ol = view_overlay.CenteredLineOverlay(self)
            self.add_view_overlay(self._crosshair_ol)
        elif self._crosshair_ol:
            self.remove_view_overlay(self._crosshair_ol)

        self.Refresh(eraseBackground=False)

    def _on_pixel_value_show(self, activated):
        """ Activate the pixelvalue view overlay"""
        if activated:
            if self._pixelvalue_ol is None:
                view = self.view
                self._pixelvalue_ol = view_overlay.PixelValueOverlay(self, view)
            self.add_view_overlay(self._pixelvalue_ol)
        elif self._pixelvalue_ol:
            self.remove_view_overlay(self._pixelvalue_ol)

        self.Refresh(eraseBackground=False)

    def set_2d_data(self, im_data, unit_x=None, unit_y=None, range_x=None, range_y=None, flip=0):
        """ Set the data to be displayed
        flip (int): 0 for no flip, wx.HORZ and wx.VERT otherwise
        """
        im_bgra = format_rgba_darray(im_data)
        self.set_images([(im_bgra, (0.0, 0.0), 1.0, True, None, None, flip, None, "")])
        self.unit_x = unit_x
        self.unit_y = unit_y
        self.range_x = range_x
        self.range_y = range_y

        self.markline_overlay.active.value = True

    @wxlimit_invocation(2)  # max 1/2 Hz
    def update_thumbnail(self):
        if self and self.IsEnabled():
            if all(i is None for i in self.images):
                self.view.thumbnail.value = None
            else:
                image = self._get_img_from_buffer()
                if image is not None:
                    self.view.thumbnail.value = image

    def val_to_pos(self, val):
        """ Translate a value tuple to a pixel position tuple
        If a value (x or y) is out of range, it will be clipped.
        :param val: (float, float) The value coordinates to translate
        :return: (int, int)
        """
        size = self.ClientSize
        x = min(max(min(self.range_x), val[0]), max(self.range_x))  # clip withing range
        pos_x_new = img.value_to_pixel(x, size[0], self.range_x, wx.HORIZONTAL)

        y = min(max(min(self.range_y), val[1]), max(self.range_y))  # clip
        pos_y_new = img.value_to_pixel(y, size[1], self.range_y, wx.VERTICAL)

        return pos_x_new, pos_y_new

    def pos_to_val(self, pos):
        """ Map the given pixel position to a value in the data range
        """
        size = self.ClientSize
        pos_x = min(max(0, pos[0]), size[0] - 1)  # clip
        val_x_new = img.pixel_to_value(pos_x, size[0], self.range_x, wx.HORIZONTAL)

        pos_y = min(max(0, pos[1]), size[1] - 1)  # clip
        val_y_new = img.pixel_to_value(pos_y, size[1], self.range_y, wx.VERTICAL)

        return val_x_new, val_y_new


class AngularResolvedCanvas(canvas.DraggableCanvas):
    """ Angle-resolved canvas """

    # TODO: it actually could be just a BitmapCanvas, but it needs
    # a (simple) fit_to_content()

    def __init__(self, *args, **kwargs):

        super(AngularResolvedCanvas, self).__init__(*args, **kwargs)

        self.default_margin = 0
        self.margins = (self.default_margin, self.default_margin)

        self.view = None
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
        self.fit_to_content()
        super(AngularResolvedCanvas, self).on_size(evt)

    def setView(self, view, tab_data):
        """Set the view that this canvas is displaying/representing
        Can be called only once, at initialisation.

        :param view:(model.MicroscopeView)
        :param tab_data: (model.MicroscopyGUIData)
        """
        # This is a kind of kludge, see viewport.MicroscopeViewport for details
        assert(self.view is None)

        self.view = view
        self._tab_data_model = tab_data

        # any image changes
        self.view.lastUpdate.subscribe(self._onViewImageUpdate, init=True)

        self.polar_overlay.active.value = True

    def _convert_streams_to_images(self):
        """ Temporary function to convert the StreamTree to a list of images as
        the canvas currently expects.
        """

        # Normally the view.streamtree should have only one image anyway
        streams = self.view.stream_tree.getProjections()

        # add the images in order
        ims = []
        for s in streams:
            # image is always centered, fitting the whole canvas
            wim = format_rgba_darray(s.image.value)
            ims.append((wim, (0, 0), (1, 1), False, None, None, None, None, s.name.value))

        self.set_images(ims)

    def _onViewImageUpdate(self, t):
        self._convert_streams_to_images()
        self.fit_to_content()
        wx.CallAfter(self.request_drawing_update)

    def update_drawing(self):
        super(AngularResolvedCanvas, self).update_drawing()
        if self.view:
            self.update_thumbnail()

    # TODO: just return best scale and center? And let the caller do what it wants?
    # It would allow to decide how to redraw depending if it's on size event or more high level.
    def fit_to_content(self):
        """ Adapt the scale and (optionally) center to fit to the current content

        """
        # TODO check if it's possible to remove duplicate code from the other fit_to_content

        # Find bounding box of all the content
        bbox = [None, None, None, None]  # ltrb in m
        for im in self.images:
            if im is None:
                continue
            md = im.metadata
            shape = im.shape
            im_scale = md['dc_scale']
            w, h = shape[1] * im_scale[0], shape[0] * im_scale[1]
            c = md['dc_center']
            bbox_im = [c[0] - w / 2, c[1] - h / 2, c[0] + w / 2, c[1] + h / 2]
            if bbox[0] is None:
                bbox = bbox_im
            else:
                bbox = (min(bbox[0], bbox_im[0]), min(bbox[1], bbox_im[1]),
                        max(bbox[2], bbox_im[2]), max(bbox[3], bbox_im[3]))

        if bbox[0] is None:
            return  # no image => nothing to do

        # compute mpp so that the bbox fits exactly the visible part
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]  # m
        if w == 0 or h == 0:
            logging.warning("Weird image size of %fx%f m", w, h)
            return  # no image
        cs = self.ClientSize
        cw = max(1, cs[0])  # px
        ch = max(1, cs[1])  # px
        self.scale = min(ch / h, cw / w)  # pick the dimension which is shortest

        # TODO: avoid aliasing when possible by picking a round number for the
        # zoom level (for the "main" image) if it's ±10% of the target size
        c = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        self.requested_phys_pos = c  # As recenter_buffer but without request_drawing_update

        wx.CallAfter(self.request_drawing_update)

    @wxlimit_invocation(2)  # max 1/2 Hz
    def update_thumbnail(self):
        if self and self.IsEnabled():
            img = self._get_img_from_buffer()
            if img is not None:
                self.view.thumbnail.value = img


class FastEMAcquisitionCanvas(DblMicroscopeCanvas):
    """
    Canvas for FastEM acquisition tab, inherits from DblMicroscopeCanvas. Unlike DblMicroscopeCanvas,
    it can contain multiple ROA overlays and calibration overlays specific to FastEM. It also allows to
    add a background overlay (e.g. to simulate a sample holder).
    """

    def __init__(self, *args, **kwargs):
        super(FastEMAcquisitionCanvas, self).__init__(*args, **kwargs)

    def add_background_overlay(self, rectangles):
        """
        rectangles (list of (float, float, float, float)): background rectangles in physical ltrb coordinates
        """
        overlay = world_overlay.FastEMBackgroundOverlay(self, rectangles)
        self.add_world_overlay(overlay)
        return overlay

    def add_roa_overlay(self, coordinates, colour=gui.SELECTION_COLOUR):
        """
        coordinates (TupleContinuousVA): VA of 4 floats representing region of acquisition coordinates
        colour (str): border colour of ROA overlay, given as string of hex code
        """
        overlay = world_overlay.FastEMROAOverlay(self, coordinates, colour=colour)
        self.add_world_overlay(overlay)
        # Always activate after creating, otherwise the code to select the region in
        # FastEMROAOverlay.on_left_down will never be called.
        overlay.active.value = True
        return overlay

    def remove_overlay(self, overlay):
        """
        overlay (FastEMROAOverlay or FastEMROAOverlay): overlay to be deleted
        """
        overlay.active.value = False  # deactivating the overlay avoids weird behaviour
        self.remove_world_overlay(overlay)
        wx.CallAfter(self.request_drawing_update)

    def add_calibration_overlay(self, coordinates, label, colour=gui.FG_COLOUR_WARNING):
        """
        coordinates (TupleContinuousVA): VA of 4 floats representing region of calibration coordinates
        label (str): label for the overlay (typically a number 1-9)
        colour (str): border colour of ROA overlay, given as string of hex code
        """
        overlay = world_overlay.FastEMROCOverlay(self, coordinates, label, colour=colour)
        self.add_world_overlay(overlay)
        overlay.active.value = False  # no need to activate/select by default
        return overlay

    def zoom_out(self):
        """
        Zoom out to show all scintillators.
        raises ValueError, IndexError: in case it's called too early during GUI startup

        Note: Should be called from main GUI thread. Make sure caller of this method
        is running in the main GUI thread.
        """
        logging.debug("Zooming out to show all scintillators.")
        if self._tab_data_model:
            sz = self._tab_data_model.main.scintillator_size
            l = min(list(self._tab_data_model.main.scintillator_positions.values()), key=lambda item: item[0])[0] - sz[0]
            t = max(list(self._tab_data_model.main.scintillator_positions.values()), key=lambda item: item[1])[1] + sz[1]
            r = max(list(self._tab_data_model.main.scintillator_positions.values()), key=lambda item: item[0])[0] + sz[0]
            b = min(list(self._tab_data_model.main.scintillator_positions.values()), key=lambda item: item[1])[1] - sz[1]
            self.fit_to_bbox((l, t, r, b))
        else:
            raise ValueError("Tab data model not initialized yet.")

    def on_dbl_click(self, evt):
        # don't recenter on double click, it's confusing, especially because selecting + moving a ROA
        # involves two clicks
        evt.Skip()

    def setView(self, view, tab_data):
        super(FastEMAcquisitionCanvas, self).setView(view, tab_data)
        view.show_crosshair.value = False
