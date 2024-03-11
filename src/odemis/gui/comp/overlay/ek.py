# -*- coding: utf-8 -*-


"""
:created: 2014-01-25
:author: Rinze de Laat
:copyright: © 2014-2021 Rinze de Laat, Éric Piel, Philip Winkler, Delmic

This file is part of Odemis.

.. license::
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

import cairo
import odemis.gui as gui
import odemis.util.conversion as conversion
import wx
from odemis import model
from odemis.gui.comp.overlay.base import Label, Vec, WorldOverlay
from odemis.gui.comp.overlay.gadget import EKLine
from odemis.util.comp import compute_camera_fov


class EKOverlay(WorldOverlay):
    """ Overlay showing the EK mask the user can position over a mirror camera feed. The EK mask
     consists of 3 lines. The user can edit the top and bottom lines by either moving them or dragging
     their endpoints. The middle line corresponds to the hole position and is updated based on the
     position of the top and bottom lines. This line is not draggable."""

    def __init__(self, cnvs):
        WorldOverlay.__init__(self, cnvs)

        self.cnvs = cnvs
        self._left_dragging = False  # Indicate whether a mouse drag is in progress
        self._selected_line = None  # EKLine currently dragged (if any)
        self._lines = []  # type: List[EKline]
        self._ccd = None  # type: DigitalCamera
        self.wl_list = None  # list of floats: pixel number -> wavelength

        # To show an orange warning in the middle if grating is in 0th order (ie, no wavelength)
        self._warning_label = Label(
            "Select a wavelength to calibrate the chromatic aberration",
            pos=(0, 0),  # Will be updated just before drawing
            font_size=30,
            flip=False,
            align=wx.ALIGN_CENTRE_HORIZONTAL | wx.ALIGN_CENTRE_VERTICAL,
            colour=conversion.hex_to_frgba(gui.FG_COLOUR_WARNING),
            opacity=1.0,
            deg=None,
            background=None
        )

        # Default values using the standard mirror size, in m
        self.parabola_f = 2.5e-3
        self.focus_dist = 0.5e-3
        self.x_max = 13.25e-3

    def set_mirror_dimensions(self, parabola_f, x_max, focus_dist):
        """
        Updates the dimensions of the mirror
        parabola_f (float): focal length of the parabola in m.
         If < 0, the drawing will be flipped (ie, with the circle towards the top)
        x_max (float): cut off of the parabola from the origin (in m)
        focus_dist (float): the vertical mirror cutoff, iow the min distance
          between the mirror and the sample (in m)
        """
        # TODO: support x_max negative (when the mirror is inverted)
        self.parabola_f = parabola_f
        self.focus_dist = focus_dist
        self.x_max = x_max

    def create_ek_mask(self, ccd, tab_data):
        """
        To be called once, to set up the overlay
        ccd (DigitalCamera): the CCD component used to show the raw image
        tab_data (Sparc2AlignGUIData): it should have mirrorPositionTopPhys and mirrorPositionBottomPhys VAs
          containing tuples of 2 floats
        """
        self._ccd = ccd
        # Calculate the image width which is used for positioning top and bottom lines.
        fov = compute_camera_fov(ccd)
        # The center is always at 0,0, so the sides are easy to compute
        im_width = (-fov[0] / 2, fov[0] / 2)

        # Will be updated before every redraw
        self.wl_list = ccd.getMetadata().get(model.MD_WL_LIST)

        # TODO: listen to the spectrograph position to force a redraw?
        # Or have a separate function to update the wl_list? (which would be called whenever the MD_WL_LIST changes)?
        # The tricky part is that the MD_WL_LIST is updated after the spectrograph position changes.
        # So it'd be hard to know if we are reading the old MD_WL_LIST or the brand new one.

        # Create the lines. Beware: the bottom/top names refer to the bottom/top
        # of the mirror (when installed above the sample). However, the convention
        # is that the CCD image is upside-down, with the bottom of the mirror at
        # the top. So the "top" line is always below the bottom line, and this is
        # ensured by _ensure_top_bottom_order().
        self._top_line = EKLine(self.cnvs, self, tab_data.mirrorPositionTopPhys, im_width)
        self._bottom_line = EKLine(self.cnvs, self, tab_data.mirrorPositionBottomPhys, im_width)
        self._lines = [self._top_line, self._bottom_line]

    def on_left_down(self, evt):
        if not self.active.value:
            return super(EKOverlay, self).on_left_down(evt)

        vpos = evt.Position
        self._selected_line, drag = self._get_tool_below(vpos)

        if drag != gui.HOVER_NONE:
            self._left_dragging = True
            self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)

            self._selected_line.start_dragging(drag, vpos)
            self.cnvs.request_drawing_update()

            # capture the mouse
            self.cnvs.SetFocus()

        else:
            # Nothing to do with tools
            evt.Skip()

    def _get_tool_below(self, vpos):
        """
        Find a tool corresponding to the given mouse position.
        Args:
            vpos (int, int): position of the mouse in view coordinate
        Returns: (tool or None, HOVER_*): the most appropriate tool and the hover mode.
            If no tool is found, it returns None.
        """
        for line in self._lines:
            hover_mode = line.get_hover(vpos)
            if hover_mode != gui.HOVER_NONE:
                return line, hover_mode

        return None, gui.HOVER_NONE

    def on_motion(self, evt):
        """ Process drag motion if enabled, otherwise call super method so event will propagate """
        if not self.active.value:
            return super(EKOverlay, self).on_motion(evt)

        if hasattr(self.cnvs, "left_dragging") and self.cnvs.left_dragging:
            # Already being handled by the canvas itself
            evt.Skip()
            return

        vpos = evt.Position
        if self._left_dragging:
            if not self._selected_line:
                logging.error("Dragging without selected tool")
                evt.Skip()
                return

            if not self.wl_list:
                # This can happen if the wavelength goes to 0 while dragging
                logging.info("No WL_LIST any more so cannot drag the EK line anymore")
                self._left_dragging = False
                return

            self._selected_line.on_motion(vpos)
            self.cnvs.request_drawing_update()
        else:
            # Hover-only => only update the cursor based on what could happen
            _, drag = self._get_tool_below(vpos)
            if drag != gui.HOVER_NONE:
                self.cnvs.set_dynamic_cursor(wx.CURSOR_HAND)
            else:
                self.cnvs.reset_dynamic_cursor()

            evt.Skip()

    def on_left_up(self, evt):
        """ Stop drawing a selected tool if the overlay is active """
        if not self.active.value:
            return super(EKOverlay, self).on_left_up(evt)

        if self._left_dragging:
            if self._selected_line:
                self._selected_line.stop_updating_tool()
                self.cnvs.request_drawing_update()
                self._selected_line = None

                # Make sure the lines didn't get over each other
                self._ensure_top_bottom_order()

            self._left_dragging = False
            self.cnvs.set_dynamic_cursor(wx.CURSOR_HAND)
        else:
            evt.Skip()

    def _ensure_top_bottom_order(self):
        """
        Check that the bottom line is *above* the top line, and invert them if that
        is not the case (because the user dragged a line over the other one).
        Note: the convention is that the mirror image is received upside-down,
        hence the bottom of the mirror is shown at the top.
        """
        if self.wl_list:
            wl_mid = self.wl_list[len(self.wl_list) // 2]
            at, bt = self._top_line._line_va.value
            p_top_mid = at + bt * wl_mid
            ab, bb = self._bottom_line._line_va.value
            p_bottom_mid = ab + bb * wl_mid

            if p_bottom_mid < p_top_mid:
                logging.info("Switching top (%s m) and bottom (%s) to keep them ordered",
                             p_bottom_mid, p_top_mid)
                self._top_line._line_va.value = ab, bb
                self._bottom_line._line_va.value = at, bt

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        self.wl_list = self._ccd.getMetadata().get(model.MD_WL_LIST)

        if self.wl_list:
            for line in self._lines:
                line.draw(ctx)
                line.sync_with_canvas(shift=shift, scale=scale)
            self.draw_pole_line(ctx)
        else:
            logging.debug("No MD_WL_LIST available")
            self._warning_label.pos = self.cnvs.get_half_buffer_size()
            self._warning_label.draw(ctx)

    def draw_pole_line(self, ctx):
        """
        Draws the line corresponding to the pole position, between the top and bottom lines,
        based on the mirror coordinates
        """
        p_start_pos_top = Vec(self._top_line.p_start_pos)
        p_end_pos_top = Vec(self._top_line.p_end_pos)
        p_start_pos_bot = Vec(self._bottom_line.p_start_pos)
        p_end_pos_bot = Vec(self._bottom_line.p_end_pos)

        # Computes the position of the mirror hole compared to the top and
        # bottom of the mirror (theoretically), as a ratio. This allows to place
        # the pole line at the right position relative to the top and bottom
        # lines. Be careful with the names! The top of the mirror is shown at the
        # *bottom*, and conversely, the bottom of the  mirror is shown a the *top*.
        a = 1 / (4 * self.parabola_f)
        hole_height = math.sqrt(self.parabola_f / a)
        bottom_mirror = self.focus_dist  # focus_dist
        top_mirror = math.sqrt(self.x_max / a)  # x_max
        pos_ratio = (top_mirror - hole_height) / (top_mirror - bottom_mirror)

        p_start_pos = p_start_pos_top + (p_start_pos_bot - p_start_pos_top) * pos_ratio
        p_end_pos = p_end_pos_top + (p_end_pos_bot - p_end_pos_top) * pos_ratio

        offset = self.cnvs.get_half_buffer_size()
        b_start = self.cnvs.phys_to_buffer(p_start_pos, offset)
        b_end = self.cnvs.phys_to_buffer(p_end_pos, offset)

        # Draws a black background for the line
        ctx.set_line_width(3)
        ctx.set_source_rgba(0, 0, 0, 0.5)
        ctx.move_to(*b_start)
        ctx.line_to(*b_end)
        ctx.stroke()

        colour = conversion.hex_to_frgb(gui.FG_COLOUR_EDIT)
        ctx.set_source_rgba(*colour)
        ctx.set_line_width(2)
        ctx.set_dash([50, 10, 10, 10])
        ctx.set_line_join(cairo.LINE_JOIN_MITER)
        ctx.move_to(*b_start)
        ctx.line_to(*b_end)
        ctx.stroke()
