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
import numpy
from odemis import util
from odemis.gui.comp.overlay.base import Label
from odemis.util import peak
import wx

import odemis.gui as gui
import odemis.gui.comp.overlay.base as base
import odemis.util.conversion as conversion
import odemis.util.units as units


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
