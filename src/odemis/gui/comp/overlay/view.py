# -*- coding: utf-8 -*-

"""
:created: 2014-01-25
:author: Rinze de Laat
:copyright: © 2014 Rinze de Laat, Delmic

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

import cairo
import logging
import math
import numbers
import numpy
from odemis import util
from odemis.gui.comp.overlay.base import Label, Vec, ViewOverlay
from odemis.gui.util import img
from odemis.gui.util.conversion import change_brightness
from odemis.util import peak
import wx

import odemis.gui as gui
import odemis.gui.comp.overlay.base as base
import odemis.model as model
import odemis.util.conversion as conversion
import odemis.util.units as units


class TextViewOverlay(base.ViewOverlay):
    """ Render the present labels to the screen """

    def __init__(self, cnvs):
        base.ViewOverlay.__init__(self, cnvs)

    def draw(self, ctx):
        if self.labels:
            self._write_labels(ctx)

# Shape type of CenteredLineOverlay
CROSSHAIR, HORIZONTAL_LINE, VERTICAL_LINE = 0, 1, 2


class CenteredLineOverlay(base.ViewOverlay):
    """ Render a static line (horizontal, vertical, crosshair) around the center of the view """

    def __init__(self, cnvs, colour=gui.CROSSHAIR_COLOR, size=gui.CROSSHAIR_SIZE, shape=CROSSHAIR,
                 thickness=gui.CENTERED_LINE_THICKNESS):
        base.ViewOverlay.__init__(self, cnvs)

        self.colour = conversion.hex_to_frgba(colour)
        self.size = size
        self.thickness = thickness
        self.shape = shape
        if self.shape not in (CROSSHAIR, HORIZONTAL_LINE, VERTICAL_LINE):
            raise ValueError("Unknown shape input {}.".format(self.shape))

    def _draw_vertical_line(self, ctx, center, size, colour, thickness):
        """
        Draw vertical line around the center point
        """
        top = center[1] - size
        bottom = center[1] + size

        ctx.set_line_width(thickness)

        # Draw shadow
        ctx.set_source_rgba(0, 0, 0, 0.9)
        ctx.move_to(center[0] + 1.5, top + 1.5)
        ctx.line_to(center[0] + 1.5, bottom + 1.5)
        ctx.stroke()

        # Draw cross hair
        ctx.set_source_rgba(*colour)
        ctx.move_to(center[0] + 0.5, top + 0.5)
        ctx.line_to(center[0] + 0.5, bottom + 0.5)
        ctx.stroke()

    def _draw_horizontal_line(self, ctx, center, size, colour, thickness):
        """
        Draw horizontal line around the center point
        """
        left = center[0] - size
        right = center[0] + size

        ctx.set_line_width(thickness)

        # Draw shadow
        ctx.set_source_rgba(0, 0, 0, 0.9)
        ctx.move_to(left + 1.5, center[1] + 1.5)
        ctx.line_to(right + 1.5, center[1] + 1.5)
        ctx.stroke()

        # Draw cross hair
        ctx.set_source_rgba(*colour)
        ctx.move_to(left + 0.5, center[1] + 0.5)
        ctx.line_to(right + 0.5, center[1] + 0.5)
        ctx.stroke()

    def draw_crosshair(self, ctx, center, size, colour, thickness):
        """
        Draw cross hair given Cairo context and center position
        """
        self._draw_horizontal_line(ctx, center, size, colour, thickness)
        self._draw_vertical_line(ctx, center, size, colour, thickness)

    def draw(self, ctx):
        """ Draw a cross hair to the Cairo context """
        center = self.cnvs.get_half_view_size()
        if self.shape is CROSSHAIR:
            self.draw_crosshair(ctx, center, size=self.size, colour=self.colour, thickness=self.thickness)
        elif self.shape is HORIZONTAL_LINE:
            self._draw_horizontal_line(ctx, center, size=center[0], colour=self.colour, thickness=self.thickness)
        elif self.shape is VERTICAL_LINE:
            self._draw_vertical_line(ctx, center, size=center[1], colour=self.colour, thickness=self.thickness)


class PlayIconOverlay(base.ViewOverlay):
    """ Render Stream (play/pause) icons to the view """

    opacity = 0.8

    def __init__(self, cnvs):
        base.ViewOverlay.__init__(self, cnvs)
        self.pause = False  # if True: displayed
        self.play = 0  # opacity of the play icon
        self.colour = conversion.hex_to_frgba(gui.FG_COLOUR_HIGHLIGHT, self.opacity)

    def hide_pause(self, hidden=True):
        """ Hide or show the pause icon """

        self.pause = not hidden
        if not self.pause:
            self.play = 1.0
        wx.CallAfter(self.cnvs.Refresh)

    def draw(self, ctx):
        if self.show:
            if self.pause:
                self._draw_pause(ctx)
            elif self.play:
                self._draw_play(ctx)
                if self.play > 0:
                    self.play -= 0.1  # a tenth less
                    # Force a refresh (without erase background), to cause a new draw
                    wx.CallLater(50, self.cnvs.Refresh, False)  # in 0.05 s
                else:
                    self.play = 0

    def _get_dimensions(self):

        width = max(16, self.view_width / 10)
        height = width
        right = self.view_width
        bottom = self.view_height
        margin = self.view_width / 25

        return width, height, right, bottom, margin

    def _draw_play(self, ctx):

        width, height, right, _, margin = self._get_dimensions()

        half_height = height / 2

        x = right - margin - width + 0.5
        y = margin + 0.5

        ctx.set_line_width(1)
        ctx.set_source_rgba(
            *conversion.hex_to_frgba(
                gui.FG_COLOUR_HIGHLIGHT, self.play))

        ctx.move_to(x, y)

        x = right - margin - 0.5
        y += half_height

        ctx.line_to(x, y)

        x = right - margin - width + 0.5
        y += half_height

        ctx.line_to(x, y)
        ctx.close_path()

        ctx.fill_preserve()

        ctx.set_source_rgba(0, 0, 0, self.play)
        ctx.stroke()

    def _draw_pause(self, ctx):

        width, height, right, _, margin = self._get_dimensions()

        bar_width = max(width / 3, 1)
        gap_width = max(width - (2 * bar_width), 1) - 0.5

        x = right - margin - bar_width + 0.5
        y = margin + 0.5

        ctx.set_line_width(1)

        ctx.set_source_rgba(*self.colour)
        ctx.rectangle(x, y, bar_width, height)

        x -= bar_width + gap_width
        ctx.rectangle(x, y, bar_width, height)

        ctx.set_source_rgba(*self.colour)
        ctx.fill_preserve()

        ctx.set_source_rgb(0, 0, 0)
        ctx.stroke()


class PixelValueOverlay(ViewOverlay):
    """ Render the raw value of a selected pixel in the spatial view """

    def __init__(self, cnvs, view):
        ViewOverlay.__init__(self, cnvs, view)

        self._v_pos = None
        self._p_pos = None  
        self.view = view
        self._raw_value = None

        self.colour = conversion.hex_to_frgba(gui.FG_COLOUR_LEGEND)
        self.background_colour = conversion.hex_to_frgba(gui.BG_COLOUR_MAIN)
        self._label = Label(
            "",
            pos=(0, 0),
            font_size=14,
            flip=True,
            align=wx.ALIGN_CENTRE_HORIZONTAL,
            colour=self.colour,
            opacity=1.0,
            deg=None,
            background=self.background_colour
        )

        self._label.text = ""

    def _activate(self):
        # Read the mouse position, so that the value under the cursor can be
        # immediately shown (instead of waiting for the user to move the mouse).
        # If the mouse is outside of the view, it's fine, it'll just show the
        # corresponding value under the mouse, even if it's not displayed.
        self._v_pos = self.cnvs.ScreenToClient(wx.GetMousePosition())
        super()._activate()

    def on_leave(self, evt):
        """ Event handler called when the mouse cursor leaves the canvas """
        if not self.active.value:
            return super(ViewOverlay, self).on_leave(evt)
        else:
            self._v_pos = None
            self._p_pos = None
            self.cnvs.Refresh()

    def on_motion(self, evt):
        """ Update the display of the raw pixel value based on the current mouse position """
        if not self.active.value:
            return super(ViewOverlay, self).on_motion(evt)

        # Whatever happens, we don't keep the event, but pass it to any other interested listener.
        evt.Skip()

        # If the canvas is being dragged, the image position cannot be directly queried,
        # and anyway the cursor is above the same pixel all the time, so no update.
        if hasattr(self.cnvs, "left_dragging") and self.cnvs.left_dragging:
            return

        self._v_pos = Vec(evt.Position)
        self.cnvs.Refresh()

    def _draw_legend(self, stream):
        """ Get the pixel coordinates and the raw pixel value given a projection """
        try:
            pixel_pos = stream.getPixelCoordinates(self._p_pos)
        except LookupError:  # No data
            return None

        if pixel_pos:
            name = stream.name.value
            raw_value = stream.getRawValue(pixel_pos)
            # In case of integers the significant number is None, no need to round the raw value
            sig = None if isinstance(raw_value, (int, numpy.integer)) else 6
            raw = units.readable_str(raw_value, sig=sig)
            # The unicode for the arrow is not available in the current Cairo version
            return u"%s (%d, %d) -> %s" % (name, pixel_pos[0], pixel_pos[1], raw)
        else:
            return None

    def draw(self, ctx):
        """ Display the stream name, pixel coordinates and pixel raw value for each projection of the spatial view """
        # get the projections of the current view
        streams = self.view.stream_tree.getProjections()

        # a rough estimation of the width and height margins in order to get the position of the pixel raw value display
        margin_w, margin_h = 5, 20
        for stream in streams:
            if self._v_pos:
                self._p_pos = self.cnvs.view_to_phys(self._v_pos, self.cnvs.get_half_buffer_size())

                # For SparcARCanvas which supports .flip. Note that this is a
                # kind-of crude version of flipping. The image is actually flipped
                # on its center position, which is not the same as mirroring the
                # whole view. However, on this canvas, the canvas is always at 0,
                # with the image at the center, so this works.
                # TODO: support flip generically.
                flip = self.cnvs.flip if hasattr(self.cnvs, "flip") else 0
                if flip & wx.VERTICAL:
                    self._p_pos = self._p_pos[0], -self._p_pos[1]
                if flip & wx.HORIZONTAL:
                    self._p_pos = -self._p_pos[0], self._p_pos[1]

                view_pos = self.view_width - margin_w, self.view_height - margin_h
                self._label.colour = self.colour
                self._label.pos = Vec(view_pos[0], view_pos[1])

                text = self._draw_legend(stream)
                if text is not None:
                    self._label.text = text
                    self._label.align = wx.ALIGN_RIGHT
                    self._label.draw(ctx)

                    # the legend for the next projection is displayed above the last displayed legend
                    # and a margin_r is roughly estimated and added for a better representation
                    margin_r = 5
                    margin_h += (self._label.text_size[1] + margin_r)


class FocusOverlay(base.ViewOverlay):
    """ Display the focus modification indicator """

    def __init__(self, cnvs):
        base.ViewOverlay.__init__(self, cnvs)

        self.margin = 10
        self.line_width = 16
        self.shifts = [None, None]  # None or float (m)
        self.ppm = (5e6, 5e6)  # px/m, conversion ratio m -> px

        self.focus_label = self.add_label("", align=wx.ALIGN_RIGHT | wx.ALIGN_CENTER_VERTICAL)

    def draw(self, ctx):
        # TODO: Both focuses at the same time, or 'snap' to horizontal/vertical on first motion?

        ctx.set_line_width(10)
        ctx.set_line_join(cairo.LINE_JOIN_MITER)
        ctx.set_source_rgba(1.0, 1.0, 1.0, 0.8)

        x, y = self.cnvs.ClientSize

        # Horizontal
        if self.shifts[0] is not None:
            y -= self.margin + (self.line_width // 2)
            middle = x / 2

            # don't display extremely small values, which are due to accumulation
            # of floating point error
            shiftm = self.shifts[0]
            if abs(shiftm) < 1e-12:
                shiftm = 0
            shift = shiftm * self.ppm[0]
            end_x = middle + (middle * (shift / (x / 2)))
            end_x = min(max(self.margin, end_x), x - self.margin)

            ctx.move_to(middle, y)
            ctx.line_to(end_x, y)
            ctx.stroke()

            lbl = "focus %s" % units.readable_str(shiftm, 'm', 2)
            self.focus_label.text = lbl
            self.focus_label.pos = (end_x, y - 15)

        # Vertical
        if self.shifts[1] is not None:
            x -= self.margin + (self.line_width // 2)
            middle = y / 2

            # don't display extremely small values, which are due to accumulation
            # of floating point error
            shiftm = self.shifts[1]
            if abs(shiftm) < 1e-12:
                shiftm = 0
            shift = shiftm * self.ppm[1]
            end_y = middle - (middle * (shift / (y / 2)))
            end_y = min(max(self.margin, end_y), y - self.margin)

            ctx.move_to(x, middle)
            ctx.line_to(x, end_y)
            ctx.stroke()

            lbl = "focus %s" % units.readable_str(shiftm, 'm', 2)
            self.focus_label.text = lbl
            self.focus_label.pos = (x - 15, end_y)
            self.focus_label.draw(ctx, self.canvas_padding, self.view_width, self.view_height)

    def add_shift(self, shift, axis):
        """ Adds a value on the given axis and updates the overlay

        shift (float): amount added to the current value (can be negative)
        axis (int): axis for which this happens

        """
        if self.shifts[axis] is None:
            self.shifts[axis] = shift
        else:
            self.shifts[axis] += shift
        self.cnvs.Refresh()

    def clear_shift(self):
        logging.debug("Clearing focus shift")
        self.shifts = [None, None]
        self.cnvs.Refresh()


class ViewSelectOverlay(base.ViewOverlay, base.SelectionMixin):

    def __init__(self, cnvs, colour=gui.SELECTION_COLOUR, center=(0, 0)):
        base.ViewOverlay.__init__(self, cnvs)
        base.SelectionMixin.__init__(self, colour, center, base.EDIT_MODE_BOX)

        self.position_label = self.add_label("")

    def draw(self, ctx, shift=(0, 0), scale=1.0):

        if self.select_v_start_pos and self.select_v_end_pos:
            start_pos = self.select_v_start_pos
            end_pos = self.select_v_end_pos

            # logging.debug("Drawing from %s, %s to %s. %s", start_pos[0],
            #                                                start_pos[1],
            #                                                end_pos[0],
            #                                                end_pos[1] )

            rect = (start_pos[0] + 0.5,
                    start_pos[1] + 0.5,
                    end_pos[0] - start_pos[0],
                    end_pos[1] - start_pos[1])

            # draws a light black background for the rectangle
            ctx.set_line_width(2)
            ctx.set_source_rgba(0, 0, 0, 0.5)
            ctx.rectangle(*rect)
            ctx.stroke()

            # draws the dotted line
            ctx.set_line_width(1.5)
            ctx.set_dash([2])
            ctx.set_line_join(cairo.LINE_JOIN_MITER)
            ctx.set_source_rgba(*self.colour)
            ctx.rectangle(*rect)
            ctx.stroke()

            self._debug_draw_edges(ctx)

            self.position_label.pos = start_pos

    def on_left_down(self, evt):
        """ Start drag action if enabled, otherwise call super method so event will propagate """
        if self.active.value:
            base.SelectionMixin._on_left_down(self, evt)

        base.ViewOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        """ End drag action if enabled, otherwise call super method so event will propagate """
        if self.active.value:
            base.SelectionMixin._on_left_up(self, evt)

        base.ViewOverlay.on_left_up(self, evt)

    def on_motion(self, evt):
        """ Process drag motion if enabled, otherwise call super method so event will propagate """
        if self.active.value:
            base.SelectionMixin._on_motion(self, evt)

        base.ViewOverlay.on_motion(self, evt)


class MarkingLineOverlay(base.ViewOverlay, base.DragMixin):
    """ Draw a vertical line at the given view position

    Provides a .val VA indicating the selected position by the user (using mouse).
    """

    HORIZONTAL = 1
    VERTICAL = 2

    def __init__(self, cnvs, colour=gui.SELECTION_COLOUR, orientation=None, map_y_from_x=False):
        """
        map_y_from_x (bool): If True, the Y coordinate of the value will be
          based on the data, obtained via cnvs.val_x_to_val(), and .val will
          contain None as Y => 1D movement.
          If False, both X and Y will be based on the mouse position (2D movement).
        """

        base.ViewOverlay.__init__(self, cnvs)
        base.DragMixin.__init__(self)

        self.label = None
        self.colour = conversion.hex_to_frgba(colour)
        self.map_y_from_x = map_y_from_x

        # highlighted position (in the data format, but not necessarily part of the data)
        self.val = model.VigilantAttribute(None)  # tuple (X, Y) or None

        self._x_label = self.add_label("", colour=self.colour)
        self._y_label = self.add_label("", colour=self.colour, align=wx.ALIGN_BOTTOM)

        self.orientation = orientation or self.HORIZONTAL
        self.label_orientation = self.orientation

        self.line_width = 2

    @property
    def x_label(self):
        return self._x_label

    @x_label.setter
    def x_label(self, lbl):
        if self.label_orientation & self.VERTICAL:
            self._x_label.text = lbl

    @property
    def y_label(self):
        return self._y_label

    @y_label.setter
    def y_label(self, lbl):
        self._y_label.text = lbl

    def clear_labels(self):
        self.val.value = None

    def hide_x_label(self):
        self.label_orientation = self.HORIZONTAL

    # Event Handlers

    def on_left_down(self, evt):
        if self.active.value:
            base.DragMixin._on_left_down(self, evt)
            self.colour = self.colour[:3] + (0.5,)
            self._store_event_pos(evt)
            self.cnvs.Refresh()

        base.ViewOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        if self.active.value:
            base.DragMixin._on_left_up(self, evt)
            self.colour = self.colour[:3] + (1.0,)
            self._store_event_pos(evt)
            self.cnvs.Refresh()

        base.ViewOverlay.on_left_up(self, evt)

    def on_motion(self, evt):
        if self.active.value and self.left_dragging:
            self._store_event_pos(evt)
            self.cnvs.Refresh()

        base.ViewOverlay.on_motion(self, evt)

    # END Event Handlers

    def _store_event_pos(self, evt):
        """ Position the focus line at the position of the given mouse event """
        x, y = evt.Position
        x = max(1, min(self.view_width, x))
        if self.map_y_from_x:
            # Y will be automatically mapped at drawing
            val = self.cnvs.pos_x_to_val_x(x, snap=False), None
        else:
            y = max(1, min(self.view_height, y))
            val = self.cnvs.pos_to_val((x, y))

        self.val.value = val

    def draw(self, ctx):
        val = self.val.value
        if val is not None and self.cnvs.range_x is not None and self.cnvs.range_y is not None:
            if self.map_y_from_x:
                # Maps Y and also snap X to the closest X value in the data
                val = self.cnvs.val_x_to_val(val[0])
            v_pos = self.cnvs.val_to_pos(val)

            if hasattr(self.cnvs, "display_xrange"):
                h_draw = self.cnvs.display_xrange[0] <= val[0] <= self.cnvs.display_xrange[1]
                v_draw = self.cnvs.display_yrange[0] <= val[1] <= self.cnvs.display_yrange[1]
            else:
                h_draw = True
                v_draw = True

            self.x_label = units.readable_str(val[0], self.cnvs.unit_x,
                                              img.guess_sig_num_rng(self.cnvs.range_x, val[0]))
            self.y_label = units.readable_str(val[1], self.cnvs.unit_y,
                                              img.guess_sig_num_rng(self.cnvs.range_y, val[1]))

            ctx.set_line_width(self.line_width)
            ctx.set_dash([3])
            ctx.set_line_join(cairo.LINE_JOIN_MITER)
            ctx.set_source_rgba(*self.colour)

            # v_posx, v_posy = self.v_pos.value
            if self.orientation & self.VERTICAL and h_draw:
                ctx.move_to(v_pos[0], 0)
                ctx.line_to(v_pos[0], self.cnvs.ClientSize.y)
                ctx.stroke()

            if self.orientation & self.HORIZONTAL and v_draw:
                ctx.move_to(0, v_pos[1])
                ctx.line_to(self.cnvs.ClientSize.x, v_pos[1])
                ctx.stroke()

            if self.x_label.text:
                self.x_label.pos = (v_pos[0] + 5, self.cnvs.ClientSize.y)
                self.x_label.draw(ctx, self.canvas_padding, self.view_width, self.view_height)

            if self.y_label.text:
                yp = max(2, v_pos[1] - 5)  # Padding from line
                # Increase bottom margin if x label is close
                label_padding = 30 if v_pos[0] < 50 else 0
                yn = min(self.view_height - label_padding, yp)
                self.y_label.pos = (3, yn)
                self.y_label.draw(ctx, self.canvas_padding, self.view_width, self.view_height)

            r, g, b, a = change_brightness(self.colour, -0.2)
            ctx.set_source_rgba(r, g, b, 0.5)
            ctx.arc(v_pos[0], v_pos[1], 5.5, 0, 2 * math.pi)
            ctx.fill()


class CurveOverlay(base.ViewOverlay, base.DragMixin):
    """ Draw a curve at the given view position
    """
    def __init__(self, cnvs, colour=gui.FG_COLOUR_CURVE, colour_peaks=gui.FG_COLOUR_PEAK, length=256):

        base.ViewOverlay.__init__(self, cnvs)
        base.DragMixin.__init__(self)

        self.length = length  # curve length
        self.label = None
        self.colour = conversion.hex_to_frgba(colour, 0.5)
        self.colour_peaks = conversion.hex_to_frgba(colour_peaks)

        # The current highlighted position
        self.selected_wl = None  # in same unit as the range

        self.peaks = None  # list of peak data
        self.peak_offset = None
        self.range = None  # array of wl/px
        self.display_range = None  # array of wl/px displayed (subset of range)
        self.unit = None  # str
        self.type = None  # str
        # Cached computation of the peak curve. The global curve is index None
        self._curves = {}  # wavelength/None -> list of values
        self.list_labels = []
        self.width_labels = []
        self.amplitude_labels = []
        self.peak_labels = []

        self.line_width = 2
    # Event Handlers

    def on_left_down(self, evt):
        if self.active.value:
            base.DragMixin._on_left_down(self, evt)
            self._store_event_pos(evt)
            self.cnvs.Refresh()

        base.ViewOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        if self.active.value:
            base.DragMixin._on_left_up(self, evt)
            self._store_event_pos(evt)
            self.cnvs.Refresh()

        base.ViewOverlay.on_left_up(self, evt)

    def on_motion(self, evt):
        if self.active.value and self.left_dragging:
            self._store_event_pos(evt)
            self.cnvs.Refresh()

        base.ViewOverlay.on_motion(self, evt)

    # END Event Handlers

    def clear_labels(self):
        self.peaks = None

    def _store_event_pos(self, evt):
        """ Position the focus line at the position of the given mouse event """
        x, y = evt.Position
        if self.peaks is not None:
            # Store in the same format as the data, so it still works after resize
            x = max(min(self.view_width, x), 1)
            width = self.range[-1] - self.range[0]
            self.selected_wl = self.range[0] + x / self.view_width * width
        else:
            self.selected_wl = None

    def update_data(self, peak_data, peak_offset, spectrum_range, unit, type):
        """
        peak_data (list of tuple of 3 floats): series of (pos, width, amplitude)
        peak_offset (float): initial offset
        spectrum_range (list of floats): wavelength/pixel for each pixel in the original spectrum data
        unit (str): m or px
        type (str): peak fitting method ('gaussian_space', 'gaussian_energy', 'lorentzian_space', 'lorentzian_energy')
        """
        self.peaks = peak_data
        self.peak_offset = peak_offset
        self.range = spectrum_range
        self.unit = unit
        self.type = type
        self._curves = {}  # throw away the cache
        self.cnvs.Refresh()

    def draw(self, ctx):
        peaks = self.peaks
        rng = self.range

        if (peaks is None) or (self.type is None):
            return

        # If original range is too small, create a finer one
        if len(rng) < self.length * 0.9:
            rng = numpy.linspace(rng[0], rng[-1], self.length)

        # Compute the label and global curve on the first time needed
        if None not in self._curves:
            self.width_labels = []
            self.amplitude_labels = []
            self.peak_labels = []
            for pos, width, amplitude in peaks:
                self.peak_labels.append(units.readable_str(pos, self.unit, 3))
                self.width_labels.append(units.readable_str(width, self.unit, 3))
                self.amplitude_labels.append(units.readable_str(amplitude, None, 3))
            self._curves[None] = peak.Curve(rng, peaks, self.peak_offset, type=self.type)
        curve = self._curves[None]

        step = max(1, len(rng) // self.length)
        rng_first = rng[0]
        rng_last = rng[-1]
        rng_n = rng[1::step]
        mn, mx = min(curve), max(curve)
        if mn == mx:
            logging.info("Global peak curve is flat, not displaying")
            return

        client_size_x = self.cnvs.ClientSize.x
        client_size_y = self.cnvs.ClientSize.y

        ctx.set_line_width(self.line_width)
        ctx.set_dash([3])
        ctx.set_line_join(cairo.LINE_JOIN_MITER)
        ctx.set_source_rgba(*self.colour)
        curve_drawn = []
        curve_n = curve[1::step]
        for x, y in zip(rng_n, curve_n):
            x_canvas, y_canvas = self.cnvs.val_to_pos((x, y))
            ctx.line_to(x_canvas, y_canvas)
            curve_drawn.append((x_canvas, y_canvas))
        ctx.stroke()

        # Draw the peak and peak label
        peaks_canvpos = []
        # Depends on canvas size so always update
        for pos, width, amplitude in peaks:
            x_canvas, _ = self.cnvs.val_to_pos((pos, 0))
            peaks_canvpos.append(int(x_canvas))

        ctx.set_source_rgba(*self.colour_peaks)
        self.list_labels = []
        for p_label, p_pos in zip(self.peak_labels, peaks_canvpos):
            ctx.move_to(p_pos - 3, client_size_y)
            ctx.line_to(p_pos, client_size_y - 16)
            ctx.line_to(p_pos + 3, client_size_y)
            ctx.line_to(p_pos - 3, client_size_y)
            ctx.fill()

            peak_tuple = min(curve_drawn, key=lambda p:abs(p[0] - p_pos))
            peak_label = Label(
                text=p_label,
                pos=(p_pos, peak_tuple[1] - 20),
                font_size=12,
                flip=True,
                align=wx.ALIGN_LEFT | wx.ALIGN_TOP,
                colour=self.colour_peaks,  # default to white
                opacity=1.0,
                deg=None,
                background=None
            )
            self.labels.append(peak_label)
            self.list_labels.append(peak_label)

        # Draw the peak curve (if the user has selected a wavelength)
        if self.selected_wl is not None and peaks:
            # Find closest peak
            peak_i = util.index_closest(self.selected_wl, [p for (p, w, a) in peaks])  # peak pos
            peak_pos = peaks[peak_i][0]
            peak_margin = (rng_last - rng_first) / (5 * len(peaks))
            if abs(peak_pos - self.selected_wl) <= peak_margin:
                if peak_i not in self._curves:
                    self._curves[peak_i] = peak.Curve(rng, [peaks[peak_i]], self.peak_offset, type=self.type)
                single_curve = self._curves[peak_i]
                ctx.set_source_rgba(*self.colour)
                x_canvas, y_canvas = self.cnvs.val_to_pos((self.cnvs.data_xrange[0], min(0, self.cnvs.data_yrange[0])))
                ctx.move_to(x_canvas, y_canvas)
                curve_n = single_curve[1::step]
                for x, y in zip(rng_n, curve_n):
                    x_canvas, y_canvas = self.cnvs.val_to_pos((x, y))
                    ctx.line_to(x_canvas, y_canvas)

                x_canvas, y_canvas = self.cnvs.val_to_pos((self.cnvs.data_xrange[1], min(0, self.cnvs.data_yrange[0])))
                ctx.line_to(x_canvas, y_canvas)
                ctx.fill()
                # Add more info to that specific peak label
                self.list_labels[peak_i].text += "\nWidth: " + self.width_labels[peak_i] + "\nAmplitude: " + self.amplitude_labels[peak_i]

        for pl in self.list_labels:
            pl.draw(ctx, self.canvas_padding, self.view_width, self.view_height)


class DichotomyOverlay(base.ViewOverlay):
    """ This overlay allows the user to select a sequence of nested quadrants
    within the canvas. The quadrants are numbered 0 to 3, from the top left to
    the bottom right. The first quadrant is the biggest, with each subsequent
    quadrant being nested in the one before it.
    """

    TOP_LEFT = 0
    TOP_RIGHT = 1
    BOTTOM_LEFT = 2
    BOTTOM_RIGHT = 3

    def __init__(self, cnvs, sequence_va, colour=gui.SELECTION_COLOUR):
        """ :param sequence_va: (ListVA) VA to store the sequence in
        """
        base.ViewOverlay.__init__(self, cnvs)

        self.colour = conversion.hex_to_frgba(colour)
        # Color for quadrant that will expand the sequence
        self.hover_forw = conversion.hex_to_frgba(colour, 0.5)
        # Color for quadrant that will cut the sequence
        self.hover_back = change_brightness(self.hover_forw, -0.2)

        self.sequence_va = sequence_va
        self.sequence_rect = []

        # This attribute is used to track the position of the mouse cursor.
        # The first value denotes the smallest quadrant (in size) in the
        # sequence and the second one the quadrant index number that will
        # be added if the mouse is clicked.
        # This value should be set to (None, None) if the mouse is outside the
        # canvas or when we are not interested in updating the sequence.
        self.hover_pos = (None, None)

        # maximum number of sub-quadrants (6->2**6 smaller than the whole area)
        self.max_len = 6

        self.sequence_va.subscribe(self.on_sequence_change, init=True)

        # Disabling the overlay will allow the event handlers to ignore events
        self.active.value = False

    def on_sequence_change(self, seq):

        if not all(0 <= v <= 3 for v in seq):
            raise ValueError("Illegal quadrant values in sequence!")

        rect = 0, 0, self.view_width, self.view_height
        self.sequence_rect = [rect]

        for i, q in enumerate(seq):
            rect = self.index_to_rect(i, q)
            self.sequence_rect.append(rect)

        self.cnvs.Refresh()

    def _reset(self):
        """ Reset all attributes to their default values and get the dimensions
        from the cnvs canvas.
        """
        logging.debug("Reset")
        self.sequence_va.value = []

    # Event Handlers

    def on_leave(self, evt):
        """ Event handler called when the mouse cursor leaves the canvas """

        if self.active.value:
            # When the mouse cursor leaves the overlay, the current top quadrant
            # should be highlighted, so clear the hover_pos attribute.
            self.hover_pos = (None, None)
            self.cnvs.Refresh()
        else:
            base.ViewOverlay.on_leave(self, evt)

    def on_motion(self, evt):
        """ Mouse motion event handler """

        if self.active.value:
            self._update_hover(evt.GetPosition())
        else:
            base.ViewOverlay.on_motion(self, evt)

    def on_left_down(self, evt):
        """ Prevent the left mouse button event from propagating when the overlay is active"""
        if not self.active.value:
            base.ViewOverlay.on_motion(self, evt)

    def on_dbl_click(self, evt):
        """ Prevent the double click event from propagating if the overlay is active"""
        if not self.active.value:
            base.ViewOverlay.on_dbl_click(self, evt)

    def on_left_up(self, evt):
        """ Mouse button handler """

        if self.active.value:
            # If the mouse cursor is over a selectable quadrant
            if None not in self.hover_pos:
                idx, quad = self.hover_pos

                # If we are hovering over the 'top' quadrant, add it to the sequence
                if len(self.sequence_va.value) == idx:
                    new_seq = self.sequence_va.value + [quad]
                    new_seq = new_seq[:self.max_len]  # cut if too long
                # Jump to the desired quadrant otherwise, cutting the sequence
                else:
                    # logging.debug("Trim")
                    new_seq = self.sequence_va.value[:idx] + [quad]
                self.sequence_va.value = new_seq

                self._update_hover(evt.GetPosition())
        else:
            base.ViewOverlay.on_leave(self, evt)

    def on_size(self, evt):
        """ Called when size of canvas changes
        """
        # Force the re-computation of rectangles
        self.on_sequence_change(self.sequence_va.value)
        base.ViewOverlay.on_size(self, evt)

    # END Event Handlers

    def _update_hover(self, pos):
        idx, quad = self.quad_hover(pos)

        # Change the cursor into a hand if the quadrant being hovered over
        # can be selected. Use the default cursor otherwise
        if idx is None:
            self.cnvs.reset_dynamic_cursor()
            idx, quad = None, None
        else:
            self.cnvs.set_dynamic_cursor(wx.CURSOR_HAND)

        # Redraw only if the quadrant changed
        if self.hover_pos != (idx, quad):
            self.hover_pos = (idx, quad)
            self.cnvs.Refresh()

    def quad_hover(self, vpos):
        """ Return the sequence index number of the rectangle at position vpos
        and the quadrant vpos is over inside that rectangle.

        :param vpos: (int, int) The viewport x,y hover position
        """

        # Loop over the rectangles, smallest one first
        for i, (x, y, w, h) in reversed(list(enumerate(self.sequence_rect))):
            if x <= vpos.x <= x + w:
                if y <= vpos.y <= y + h:
                    # If vpos is within the rectangle, we can determine the
                    # quadrant.
                    # Remember that the quadrants are numbered as follows:
                    #
                    # 0 | 1
                    # --+--
                    # 2 | 3

                    # Construct the quadrant number by starting with 0
                    quad = 0

                    # If the position is in the left half, add 1 to the quadrant
                    if vpos.x > x + w / 2:
                        quad += 1
                    # If the position is in the bottom half, add 2
                    if vpos.y > y + h / 2:
                        quad += 2

                    return i, quad

        return None, None

    def index_to_rect(self, idx, quad):
        """ Translate given rectangle and quadrant into a view rectangle
            :param idx: (int) The index number of the rectangle in sequence_rect
                that we are going to use as a cnvs.
            :param quad: (int) The quadrant number
            :return: (int, int, int, int) Rectangle tuple of the form x, y, w, h
        """
        x, y, w, h = self.sequence_rect[idx]

        # The new rectangle will have half the size of the cnvs one
        w /= 2
        h /= 2

        # If the quadrant is in the right half, construct x by adding half the
        # width to x position of the cnvs rectangle.
        if quad in (self.TOP_RIGHT, self.BOTTOM_RIGHT):
            x += w

        # If the quadrant is in the bottom half, construct y by adding half the
        # height to the y position of the cnvs rectangle.
        if quad in (self.BOTTOM_LEFT, self.BOTTOM_RIGHT):
            y += h

        return x, y, w, h

    def draw(self, ctx):

        ctx.set_source_rgba(*self.colour)
        ctx.set_line_width(2)
        ctx.set_dash([2])
        ctx.set_line_join(cairo.LINE_JOIN_MITER)

        # Draw previous selections as dashed rectangles
        for rect in self.sequence_rect:
            # logging.debug("Drawing ", *args, **kwargs)
            ctx.rectangle(*rect)
        ctx.stroke()

        # If the mouse is over the canvas
        if None not in self.hover_pos:
            idx, quad = self.hover_pos

            # If the mouse is over the smallest selected quadrant
            if idx == len(self.sequence_va.value):
                # Mark quadrant to be added
                ctx.set_source_rgba(*self.hover_forw)
                rect = self.index_to_rect(idx, quad)
                ctx.rectangle(*rect)
                ctx.fill()
            else:
                # Mark higher quadrant to 'jump' to
                ctx.set_source_rgba(*self.hover_back)
                rect = self.index_to_rect(idx, quad)
                ctx.rectangle(*rect)
                ctx.fill()

                # Mark current quadrant
                ctx.set_source_rgba(*self.hover_forw)
                ctx.rectangle(*self.sequence_rect[-1])
                ctx.fill()

        # If the mouse is not over the canvas
        elif self.sequence_va.value and self.sequence_rect:
            # Mark the currently selected quadrant
            ctx.set_source_rgba(*self.hover_forw)
            ctx.rectangle(*self.sequence_rect[-1])
            ctx.fill()


class PolarOverlay(base.ViewOverlay):

    def __init__(self, cnvs):
        base.ViewOverlay.__init__(self, cnvs)

        self.canvas_padding = 0
        # Rendering attributes
        self.center_x = None
        self.center_y = None
        self.radius = None
        self.inner_radius = None
        self.tau = 2 * math.pi
        self.num_ticks = 6
        self.ticks = []

        self.ticksize = 10

        # Value attributes
        self.px, self.py = None, None
        self.tx, self.ty = None, None

        self.colour = conversion.hex_to_frgb(gui.SELECTION_COLOUR)
        self.colour_drag = conversion.hex_to_frgba(gui.SELECTION_COLOUR, 0.5)
        self.colour_highlight = conversion.hex_to_frgb(gui.FG_COLOUR_HIGHLIGHT)
        self.intensity_label = self.add_label("", align=wx.ALIGN_CENTER_HORIZONTAL,
                                              colour=self.colour_highlight)

        self.phi = None             # Phi angle in radians
        self.phi_line_rad = None    # Phi drawing angle in radians (is phi -90)
        self.phi_line_pos = None    # End point in pixels of the Phi line
        self.phi_label = self.add_label("", colour=self.colour,
                                        align=wx.ALIGN_CENTER_HORIZONTAL | wx.ALIGN_BOTTOM)
        self.theta = None           # Theta angle in radians
        self.theta_radius = None    # Radius of the theta circle in pixels
        self.theta_label = self.add_label("", colour=self.colour,
                                          align=wx.ALIGN_CENTER_HORIZONTAL)
        self.intersection = None    # The intersection of the circle and line in pixels

        self.dragging = False

        # Calculate the characteristic values for the first time
        self.on_size()

    # Property Getters/Setters
    @property
    def phi_rad(self):
        return self.phi

    @phi_rad.setter
    def phi_rad(self, phi_rad):
        self.phi = phi_rad
        self._calculate_phi()
        self.cnvs.Refresh()

    @property
    def phi_deg(self):
        return math.degrees(self.phi)

    @phi_deg.setter
    def phi_deg(self, phi_deg):
        self.phi_rad = math.radians(phi_deg)

    @property
    def theta_rad(self):
        return self.theta

    @theta_rad.setter
    def theta_rad(self, theta_rad):
        self.theta = theta_rad
        self.theta_radius = (theta_rad / (math.pi / 2)) * self.inner_radius
        self._calculate_theta()
        self.cnvs.Refresh()

    @property
    def theta_deg(self):
        return math.degrees(self.theta)

    @theta_deg.setter
    def theta_deg(self, theta_deg):
        self.theta_rad = math.radians(theta_deg)

    # END Property Getters/Setters

    def _calculate_phi(self, view_pos=None):
        """ Calcualate the Phi angle and the values to display the Phi line """
        if view_pos:
            vx, vy = view_pos
            dx, dy = vx - self.center_x, self.center_y - vy

            # Calculate the phi angle in radians
            # Atan2 gives the angle between the positive x axis and the point
            # dx,dy
            self.phi = math.atan2(dx, dy) % self.tau

        if self.phi:
            self.phi_line_rad = self.phi - math.pi / 2

            cos_phi_line = math.cos(self.phi_line_rad)
            sin_phi_line = math.sin(self.phi_line_rad)

            # Pixel to which to draw the Phi line to
            phi_x = self.center_x + self.radius * cos_phi_line
            phi_y = self.center_y + self.radius * sin_phi_line
            self.phi_line_pos = (phi_x, phi_y)

            # Calc Phi label pos

            # Calculate the view point on the line where to place the label
            if self.theta_radius and self.theta_radius > self.inner_radius / 2:
                radius = self.inner_radius * 0.25
            else:
                radius = self.inner_radius * 0.75

            x = self.center_x + radius * cos_phi_line
            y = self.center_y + radius * sin_phi_line

            self.phi_label.text = u"φ %0.1f°" % math.degrees(self.phi)
            self.phi_label.deg = math.degrees(self.phi_line_rad)

            # Now we calculate a perpendicular offset to the Phi line where
            # we can plot the label. It is also determined if the label should
            # flip, depending on the angle.

            if self.phi < math.pi:
                ang = -math.pi / 2.0  # -45 deg
                self.phi_label.flip = False
            else:
                ang = math.pi / 2.0  # 45 deg
                self.phi_label.flip = True

            # Calculate a point further down the line that we will rotate
            # around the calculated label x,y. By translating (-x and -y) we
            # 'move' the origin to label x,y
            rx = (self.center_x - x) + (radius + 5) * cos_phi_line
            ry = (self.center_y - y) + (radius + 5) * sin_phi_line

            # Apply the rotation
            lx = rx * math.cos(ang) - ry * math.sin(ang)
            ly = rx * math.sin(ang) + ry * math.cos(ang)

            # Translate back to our original origin
            lx += x
            ly += y

            self.phi_label.pos = (lx, ly)

    def _calculate_theta(self, view_pos=None):
        """ Calculate the Theta angle and the values needed to display it. """
        if view_pos:
            vx, vy = view_pos
            dx, dy = vx - self.center_x, self.center_y - vy
            # Get the radius and the angle for Theta
            self.theta_radius = min(math.sqrt(dx * dx + dy * dy),
                                    self.inner_radius)
            self.theta = (math.pi / 2) * (self.theta_radius / self.inner_radius)
        elif self.theta:
            self.theta_radius = (self.theta / (math.pi / 2)) * self.inner_radius
        else:
            return

        # Calc Theta label pos
        x = self.center_x
        y = self.center_y + self.theta_radius + 3

        theta_str = u"θ %0.1f°" % math.degrees(self.theta)

        self.theta_label.text = theta_str
        self.theta_label.pos = (x, y)

    def _calculate_intersection(self):
        if None not in (self.phi_line_rad, self.theta_radius):
            # Calculate the intersection between Phi and Theta
            x = self.center_x + self.theta_radius * math.cos(self.phi_line_rad)
            y = self.center_y + self.theta_radius * math.sin(self.phi_line_rad)
            self.intersection = (x, y)
        else:
            self.intersection = None

    def _calculate_display(self, view_pos=None):
        """ Calculate the values needed for plotting the Phi and Theta lines and labels

        If view_pos is not given, the current Phi and Theta angles will be used.

        """

        self._calculate_phi(view_pos)
        self._calculate_theta(view_pos)
        self._calculate_intersection()

        # if (view_pos and 0 < self.intersection[0] < self.cnvs.ClientSize.x and
        #         0 < self.intersection[1] < self.cnvs.ClientSize.y):
        #     # FIXME: Determine actual value here
        #     #self.intensity_label.text = ""
        #     pass

    # Event Handlers

    def on_left_down(self, evt):
        if self.active.value:
            self.dragging = True

        base.ViewOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        if self.active.value:
            self._calculate_display(evt.Position)
            self.dragging = False
            self.cnvs.Refresh()

        base.ViewOverlay.on_left_up(self, evt)

    def on_motion(self, evt):
        # Only change the values when the user is dragging
        if self.active.value and self.dragging:
            self._calculate_display(evt.Position)
            self.cnvs.Refresh()
        else:
            base.ViewOverlay.on_motion(self, evt)

    def on_enter(self, evt):
        if self.active.value:
            self.cnvs.set_default_cursor(wx.CROSS_CURSOR)
        else:
            base.ViewOverlay.on_enter(self, evt)

    def on_leave(self, evt):
        if self.active.value:
            self.cnvs.reset_default_cursor()
        else:
            base.ViewOverlay.on_leave(self, evt)

    def on_size(self, evt=None):
        # Calculate the characteristic values
        self.center_x = self.cnvs.ClientSize.x / 2
        self.center_y = self.cnvs.ClientSize.y / 2
        self.inner_radius = min(self.center_x, self.center_y)
        self.radius = self.inner_radius + (self.ticksize / 1.5)
        self.ticks = []

        # Top middle
        for i in range(self.num_ticks):
            # phi needs to be rotated 90 degrees counter clockwise, otherwise
            # 0 degrees will be at the right side of the circle
            phi = (self.tau / self.num_ticks * i) - (math.pi / 2)
            deg = round(math.degrees(phi))

            cos = math.cos(phi)
            sin = math.sin(phi)

            # Tick start and end poiint (outer and inner)
            ox = self.center_x + self.radius * cos
            oy = self.center_y + self.radius * sin
            ix = self.center_x + (self.radius - self.ticksize) * cos
            iy = self.center_y + (self.radius - self.ticksize) * sin

            # Tick label positions
            lx = self.center_x + (self.radius + 5) * cos
            ly = self.center_y + (self.radius + 5) * sin

            label = self.add_label(u"%d°" % (deg + 90),
                                   (lx, ly),
                                   colour=(0.8, 0.8, 0.8),
                                   deg=deg - 90,
                                   flip=True,
                                   align=wx.ALIGN_CENTRE_HORIZONTAL | wx.ALIGN_BOTTOM)

            self.ticks.append((ox, oy, ix, iy, label))

        self._calculate_display()

        if evt:
            base.ViewOverlay.on_size(self, evt)

    # END Event Handlers

    def draw(self, ctx):
        # Draw angle lines
        ctx.set_line_width(2.5)
        ctx.set_source_rgba(0, 0, 0, 0.2 if self.dragging else 0.5)

        if self.theta is not None:
            # Draw dark underline azimuthal circle
            ctx.arc(self.center_x, self.center_y,
                    self.theta_radius, 0, self.tau)
            ctx.stroke()

        if self.phi is not None:
            # Draw dark underline Phi line
            ctx.move_to(self.center_x, self.center_y)
            ctx.line_to(*self.phi_line_pos)
            ctx.stroke()

        # Light selection lines formatting
        ctx.set_line_width(2)
        ctx.set_dash([3])

        if self.dragging:
            ctx.set_source_rgba(*self.colour_drag)
        else:
            ctx.set_source_rgb(*self.colour)

        if self.theta is not None:
            # Draw azimuthal circle
            ctx.arc(self.center_x, self.center_y,
                    self.theta_radius, 0, self.tau)
            ctx.stroke()

            self.theta_label.draw(ctx, self.canvas_padding, self.view_width, self.view_height)

        if self.phi is not None:
            # Draw Phi line
            ctx.move_to(self.center_x, self.center_y)
            ctx.line_to(*self.phi_line_pos)
            ctx.stroke()

            self.phi_label.draw(ctx, self.canvas_padding, self.view_width, self.view_height)

        ctx.set_dash([])

        # ## Draw angle markings ###

        # Draw frame that covers everything outside the center circle
        ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        ctx.set_source_rgb(0.2, 0.2, 0.2)

        ctx.rectangle(0, 0, self.cnvs.ClientSize.x, self.cnvs.ClientSize.y)
        ctx.arc(self.center_x, self.center_y, self.inner_radius, 0, self.tau)
        # mouse_inside = not ctx.in_fill(float(self.vx or 0), float(self.vy or 0))
        ctx.fill()

        # Draw Azimuth degree circle
        ctx.set_line_width(2)
        ctx.set_source_rgb(0.5, 0.5, 0.5)
        ctx.arc(self.center_x, self.center_y, self.radius, 0, self.tau)
        ctx.stroke()

        # Draw Azimuth degree ticks
        ctx.set_line_width(1)
        for sx, sy, lx, ly, _ in self.ticks:
            ctx.move_to(sx, sy)
            ctx.line_to(lx, ly)
        ctx.stroke()

        # Draw tick labels, ignore padding in this case
        pad, self.canvas_padding = self.canvas_padding, 0

        for _, _, _, _, label in self.ticks:
            label.draw(ctx, self.canvas_padding, self.view_width, self.view_height)

        self.canvas_padding = pad

        if self.intensity_label.text and self.intersection:
            ctx.set_source_rgb(*self.colour_highlight)
            ctx.arc(self.intersection[0], self.intersection[1], 3, 0, self.tau)
            ctx.fill()

            x, y = self.intersection
            y -= 18
            if y < 40:
                y += 40

            self.intensity_label.pos = (x, y)
            self.intensity_label.draw(ctx, self.canvas_padding, self.view_width, self.view_height)


class PointSelectOverlay(base.ViewOverlay):
    """ Overlay for the selection of canvas points in view and physical coordinates """

    def __init__(self, cnvs):
        base.ViewOverlay.__init__(self, cnvs)
        # Prevent the cursor from resetting on clicks

        # Physical position of the last click
        self.v_pos = model.VigilantAttribute(None)
        self.p_pos = model.VigilantAttribute(None)

    # Event Handlers

    def on_enter(self, evt):
        if self.active.value:
            self.cnvs.set_default_cursor(wx.CROSS_CURSOR)
        else:
            base.ViewOverlay.on_enter(self, evt)

    def on_leave(self, evt):
        if self.active.value:
            self.cnvs.reset_default_cursor()
        else:
            base.ViewOverlay.on_leave(self, evt)

    def on_left_down(self, evt):
        if not self.active.value:
            base.ViewOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        if self.active.value:
            v_pos = evt.Position
            p_pos = self.cnvs.view_to_phys(v_pos, self.cnvs.get_half_buffer_size())

            self.v_pos.value = v_pos
            self.p_pos.value = p_pos
            logging.debug("Point selected (view, physical): %s, %s)",
                          self.v_pos.value, self.p_pos.value)
        else:
            base.ViewOverlay.on_left_up(self, evt)

    # END Event Handlers

    def draw(self, ctx):
        pass

class HistoryOverlay(base.ViewOverlay):
    """ Display rectangles on locations that the microscope was previously positioned at """

    def __init__(self, cnvs, history_list_va):
        base.ViewOverlay.__init__(self, cnvs)

        self.trail_colour = conversion.hex_to_frgb(gui.FG_COLOUR_HIGHLIGHT)
        self.pos_colour = conversion.hex_to_frgb(gui.FG_COLOUR_EDIT)
        self.fade = True  # Fade older positions in the history list
        self.history = history_list_va  # ListVA  of (center, size) tuples
        self.history.subscribe(self._on_history_update)

        self._merge_ratio = None

    def __str__(self):
        return "History (%d): \n" % len(self) + "\n".join([str(h) for h in self.history.value[-5:]])

    # # Event Handlers
    #
    # def on_enter(self, evt):
    #     base.ViewOverlay.on_enter(self, evt)
    #     self.cnvs.Refresh()
    #
    # def on_leave(self, evt):
    #     base.ViewOverlay.on_leave(self, evt)
    #     self.cnvs.Refresh()
    #
    # # END Event Handlers

    # TODO: might need rate limiter (but normally stage position is changed rarely)
    # TODO: Make the update of the canvas image the responsibility of the viewport
    def _on_history_update(self, _):
        wx.CallAfter(self.cnvs.request_drawing_update)

    def draw(self, ctx, scale=None, shift=None):
        """
        scale (0<float): ratio between the canvas pixel size and the pixel size
          of the drawing area. That's a trick to allow drawing both on the
          standard view and directly onto the thumbnail.
        shift (float, float): offset to add for positioning the drawing, when
          it is scaled
        """

        ctx.set_line_width(1)
        offset = self.cnvs.get_half_buffer_size()

        for i, (p_center, p_size) in enumerate(self.history.value):
            alpha = (i + 1) * (0.8 / len(self.history.value)) + 0.2 if self.fade else 1.0
            if self._merge_ratio is not None:
                alpha *= (1 - self._merge_ratio)
            v_center = self.cnvs.phys_to_view(p_center, offset)

            if scale:
                v_center = (shift[0] + v_center[0] * scale,
                            shift[1] + v_center[1] * scale)
                marker_size = (2, 2)
            elif p_size:
                marker_size = (int(p_size[0] * self.cnvs.scale),
                               int(p_size[0] * self.cnvs.scale))

                # Prevent the marker from becoming too small
                if marker_size[0] < 2 or marker_size[1] < 2:
                    marker_size = (3, 3)
            else:
                marker_size = (5, 5)

            if i < len(self.history.value) - 1:
                colour = self.trail_colour
            else:
                colour = self.pos_colour

            self._draw_rect(ctx, v_center, marker_size, colour, alpha)

    @staticmethod
    def _draw_rect(ctx, v_center, v_size, colour, alpha):

        ctx.set_source_rgba(0, 0, 0, alpha * 0.4)

        x = int(v_center[0] - v_size[0] / 2.0) + 0.5
        y = int(v_center[1] - v_size[1] / 2.0) + 0.5

        ctx.rectangle(x + 1, y + 1, v_size[0], v_size[1])
        ctx.stroke()

        ctx.set_source_rgba(colour[0], colour[1], colour[2], alpha)

        # Render rectangles of 3 pixels wide
        ctx.rectangle(x, y, v_size[0], v_size[1])
        ctx.stroke()

    def set_merge_ratio(self, merge_ratio):
        """
        Modifies the internal attribute _merge_ratio that controls the transparency 
        of the history overlay.
        """
        self._merge_ratio = merge_ratio
        wx.CallAfter(self.cnvs.request_drawing_update)


class SpotModeOverlay(base.ViewOverlay, base.DragMixin, base.SpotModeBase):
    """ Render the spot mode indicator in the center of the view

    If a position is provided, the spot will be drawn there.

    If the overlay is activated, the user can use the mouse cursor to select a position


    """
    def __init__(self, cnvs, spot_va=None):
        base.ViewOverlay.__init__(self, cnvs)
        base.DragMixin.__init__(self)
        base.SpotModeBase.__init__(self, cnvs, spot_va=spot_va)

        self.v_pos = None

    def on_spot_change(self, _):
        self._r_to_v()

    def on_size(self, evt):
        self._r_to_v()
        base.ViewOverlay.on_size(self, evt)

    def _v_to_r(self):
        if self.v_pos is None:
            self.r_pos.value = (0.5, 0.5)
        else:
            self.r_pos.value = (
                float(self.v_pos[0] / self.cnvs.view_width),
                float(self.v_pos[1] / self.cnvs.view_height)
            )

    def _r_to_v(self):
        try:
            self.v_pos = (
                int(self.cnvs.view_width * self.r_pos.value[0]),
                int(self.cnvs.view_height * self.r_pos.value[1])
            )
        except (TypeError, KeyError):
            self.v_pos = None

    def draw(self, ctx, shift=(0, 0), scale=1.0):

        if self.v_pos is None:
            return

        vx, vy = self.v_pos
        base.SpotModeBase.draw(self, ctx, vx, vy)

    def _activate(self):
        self._r_to_v()
        base.ViewOverlay._activate(self)

    def _deactivate(self):
        self.v_pos = None
        base.ViewOverlay._deactivate(self)
