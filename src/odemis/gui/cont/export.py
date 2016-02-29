# -*- coding: utf-8 -*-
"""
:created: 2016-28-01
:author: Kimon Tsitsikas
:copyright: © 2015-2016 Kimon Tsitsikas, Delmic

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

from __future__ import division

import cairo
import logging
import math
import numpy
from odemis import model
from odemis.acq import stream
from odemis.dataio import get_converter
from odemis.gui import BLEND_SCREEN, BLEND_DEFAULT
from odemis.gui.comp.overlay.base import Label
from odemis.gui.util import formats_to_wildcards
from odemis.gui.util import get_picture_folder
from odemis.gui.util.img import add_alpha_byte, apply_rotation, apply_shear, apply_flip
from odemis.gui.util.img import format_rgba_darray, min_type
from odemis.util import units
import odemis.gui as gui
import odemis.util.conversion as conversion
from odemis.gui.comp.popup import Message
import os
import threading
import time
import wx


PR_PREFIX = "Print-ready"
PP_PREFIX = "Post-processing"
BAR_PLOT_COLOUR = (0.75, 0.75, 0.75)
SCALE_FACTOR = 4  # The factor by which we multiply the view window shape


class SpatialOptions(object):
    # Represents the options
    # Each VA is passed as a kwargs key to the export
    def __init__(self):
        self.interpolate = model.BooleanVA(True)

    conf = {}  # To override the way VAs are displayed


# Dict where keys are the available export types and value is a list with the
# available exporters for this type
EXPORTERS = {"spatial": ([("PNG", SpatialOptions), ("TIFF", SpatialOptions)],
                         [("Serialized TIFF", SpatialOptions)]),
             "AR": ([("PNG", None), ("TIFF", None)],
                    [("CSV", None)]),
             "spectrum": ([("PNG", None), ("TIFF", None)],
                          [("CSV", None)])}


class ExportController(object):
    """
    Manages the export of the data displayed in the focused view of a tab to
    an easy for post-process format.
    """

    def __init__(self, tab_data, main_frame, viewports):
        """
        tab_data: MicroscopyGUIData -- the representation of the microscope GUI
        main_frame: (wx.Frame): the whole GUI frame
        """

        self._data_model = tab_data
        self._main_data_model = tab_data.main
        self._main_frame = main_frame

        self._viewports = viewports.keys()
        self.images_cache = {}

        # TODO: drop this attribute, and just read tab_data.focussedView.value when needed
        # current focussed view
        self.microscope_view = None
        # subscribe to get notified about the current focused view
        tab_data.focussedView.subscribe(self.on_focussed_view, init=True)

        wx.EVT_MENU(self._main_frame,
                    self._main_frame.menu_item_export_as.GetId(),
                    self.start_export_as_viewport)
        self._main_frame.menu_item_export_as.Enable(False)

        # subscribe to get notified about tab changes
        self._main_data_model.tab.subscribe(self.on_tab_change, init=True)

    def on_tab_change(self, tab):
        """ Subscribe to the the current tab """
        if tab is not None and tab.name == 'analysis':
            # Only let export item to be enabled in AnalysisTab
            tab.tab_data_model.streams.subscribe(self.on_streams_change, init=True)
        else:
            self._main_frame.menu_item_export_as.Enable(False)

    def on_streams_change(self, streams):
        """ Enable Export menu item iff the tab has at least one stream """

        enabled = (len(streams) > 0)
        self._main_frame.menu_item_export_as.Enable(enabled)

    def start_export_as_viewport(self, event):
        """ Wrapper to run export_viewport in a separate thread."""
        filepath, exporter, export_format, export_type = self._get_export_info()
        if None not in (filepath, exporter, export_format, export_type):
            thread = threading.Thread(target=self.export_viewport,
                                      args=(filepath, exporter, export_format, export_type))
            thread.start()

    def _get_export_info(self):
        # TODO create ExportConfig
        # Set default to the first of the list
        export_type = self.get_export_type(self.microscope_view)
        formats = EXPORTERS[export_type]
        default_exporter = get_converter(formats[0][0][0])
        extension = default_exporter.EXTENSIONS[0]
        basename = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        filepath = os.path.join(get_picture_folder(), basename + extension)
        # filepath will be None if cancelled by user
        filepath, export_format, export_type = self.ShowExportFileDialog(filepath, default_exporter)
        # get rid of the prefix before you ask for the exporter
        if any(prefix in export_format.split(' ') for prefix in [PR_PREFIX, PP_PREFIX]):
            export_format = export_format.split(' ', 1)[1]
        exporter = get_converter(export_format)

        return filepath, exporter, export_format, export_type

    def export_viewport(self, filepath, exporter, export_format, export_type):
        """ Export the image from the focused view to the filesystem.

        :param filepath: (str) full path to the destination file
        :param exporter: (func) exporter to use for writing the file
        :param export_format: (str) the format name
        :param export_type: (str) spatial, AR or spectrum

        When no dialog is shown, the name of the file will follow the scheme
        `date`-`time`.tiff (e.g., 20120808-154812.tiff) and it will be saved
        in the user's picture directory.

        """

        try:
            # When exporting using the menu Export button the options to be
            # set by the user are ignored
            raw = export_format in [fmt[0] for fmt in EXPORTERS[export_type][1]]
            exported_data = self.export(export_type, raw)
            Message.show_message(self._main_frame,
                                 "Exported in %s" % (filepath,),
                                 timeout=3
                                 )
            # record everything to a file
            exporter.export(filepath, exported_data)

            logging.info("Exported file '%s'.", filepath)
        except Exception:
            logging.exception("Failed to export")

    def get_export_type(self, view):
        """
        Based on the given view gives the corresponding export type
        return (string): spatial, AR or spectrum
        """
        view_name = view.name.value
        # TODO: just use another dict
        if view_name == 'Angle-resolved':
            export_type = 'AR'
        elif view_name == 'Spectrum plot':
            export_type = 'spectrum'
        else:
            export_type = 'spatial'
        return export_type

    def on_focussed_view(self, view):
        """ Called when another focussed view changes.

        :param view: (MicroscopeView) The newly focussed view

        """
        self.microscope_view = view

    def export(self, export_type, raw=False, interpolate_data=True):
        """
        Returns the data to be exported with respect to the settings and options.

        :param export_type (string): spatial, AR or spectrum
        :param raw (boolean): raw data format if True
        :param interpolate_data (boolean): apply interpolation on data if True

        returns DataArray: the data to be exported, either an image or raw data

        """
        # TODO move 'interpolate_data' to kwargs and passed to all *_to_export_data()
        vp = self.get_viewport_by_view(self.microscope_view)
        # TODO: do not rely on self.ClientSize, should just use
        self.ClientSize = vp.canvas.ClientSize
        if export_type == 'AR':
            exported_data = self.ar_to_export_data(raw)
        elif export_type == 'spectrum':
            spectrum = vp.stream.get_pixel_spectrum()
            spectrum_range, unit = vp.stream.get_spectrum_range()
            exported_data = self.spectrum_to_export_data(spectrum, raw, unit, spectrum_range)
        else:
            export_type = 'spatial'
            self._convert_streams_to_images(not raw)
            if not self.images:
                return
            images = self.images
            exported_data = self.images_to_export_data(images, not raw, interpolate_data)
        return exported_data

    def get_viewport_by_view(self, view):
        """ Return the ViewPort associated with the given view """

        for vp in self._viewports:
            if vp.microscope_view == view:
                return vp
        raise IndexError("No ViewPort found for view %s" % view)

    def fit_to_content(self):
        """
        Adapt the scale to fit to the current content
        """

        # Find bounding box of all the content
        bbox = [None, None, None, None]  # ltrb in wu
        for im in self.images:
            if im is None:
                continue
            im_scale = im.metadata['dc_scale']
            w, h = im.shape[1] * im_scale[0], im.shape[0] * im_scale[1]
            c = im.metadata['dc_center']
            bbox_im = [c[0] - w / 2, c[1] - h / 2, c[0] + w / 2, c[1] + h / 2]
            if bbox[0] is None:
                bbox = bbox_im
            else:
                bbox = (min(bbox[0], bbox_im[0]), min(bbox[1], bbox_im[1]),
                        max(bbox[2], bbox_im[2]), max(bbox[3], bbox_im[3]))

        if bbox[0] is None:
            return  # no image => nothing to do

        # compute mpp so that the bbox fits exactly the visible part
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]  # wu
        if w == 0 or h == 0:
            logging.warning("Weird image size of %fx%f wu", w, h)
            return  # no image
        cs = self.ClientSize
        cw = max(1, cs[0])  # px
        ch = max(1, cs[1])  # px
        self.scale = min(ch / h, cw / w)  # pick the dimension which is shortest

    def ar_to_export_data(self, raw=False):
        streams = self.microscope_view.getStreams()

        if raw:
            # TODO implement raw export
            raise ValueError("Raw export is unsupported for AR data")
        else:
            # we expect just one stream
            wim = format_rgba_darray(streams[0].image.value)
            # image is always centered, fitting the whole canvass
            self.set_images([(wim, (0, 0), (1, 1), False, None, None, None, None, streams[0].name.value, None)])
            self.fit_to_content()

            # Make surface based on the maximum resolution
            data_to_draw = numpy.zeros((self.ClientSize.y, self.ClientSize.x, 4), dtype=numpy.uint8)
            surface = cairo.ImageSurface.create_for_data(
                data_to_draw, cairo.FORMAT_ARGB32, self.ClientSize.x, self.ClientSize.y)
            ctx = cairo.Context(surface)

            im = self.images[0]
            self._buffer_center = (0, 0)
            self._buffer_scale = (im.metadata['dc_scale'][0] / self.scale,
                                  im.metadata['dc_scale'][1] / self.scale)
            self._buffer_size = self.ClientSize.x, self.ClientSize.y

            self._draw_image(
                ctx,
                im,
                im.metadata['dc_center'],
                1.0,
                im_scale=im.metadata['dc_scale'],
                rotation=im.metadata['dc_rotation'],
                shear=im.metadata['dc_shear'],
                flip=im.metadata['dc_flip'],
                blend_mode=im.metadata['blend_mode'],
                interpolate_data=True
            )

            # TODO: don't use self., just pass as arguments
            self.colour = conversion.hex_to_frgb(gui.SELECTION_COLOUR)  # TODO used?
            self._font_name = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT).GetFaceName()
            self.ticksize = 10
            self.tau = 2 * math.pi
            self.num_ticks = 6
            self.ar_create_tick_labels()
            self.draw_ar(ctx)
            ar_plot = model.DataArray(data_to_draw)
            ar_plot.metadata[model.MD_DIMS] = 'YXC'
            return ar_plot

    def ar_create_tick_labels(self):
        # Calculate the characteristic values
        self.center_x = self.ClientSize.x / 2
        self.center_y = self.ClientSize.y / 2
        self.inner_radius = min(self.center_x, self.center_y)
        self.radius = self.inner_radius + (self.ticksize / 1.5)
        # TODO: just return ticks
        self.ticks = []

        # Top middle
        for i in range(self.num_ticks):
            # phi needs to be rotated 90 degrees counter clockwise, otherwise
            # 0 degrees will be at the right side of the circle
            phi = (self.tau / self.num_ticks * i) - (math.pi / 2)
            deg = round(math.degrees(phi))

            cos = math.cos(phi)
            sin = math.sin(phi)

            # Tick start and end point (outer and inner)
            ox = self.center_x + self.radius * cos
            oy = self.center_y + self.radius * sin
            ix = self.center_x + (self.radius - self.ticksize) * cos
            iy = self.center_y + (self.radius - self.ticksize) * sin

            # Tick label positions
            lx = self.center_x + (self.radius + 5) * cos
            ly = self.center_y + (self.radius + 5) * sin

            label = Label(
                text=u"%d°" % (deg + 90),
                pos=(lx, ly),
                font_size=12,
                flip=True,
                align=wx.ALIGN_CENTRE_HORIZONTAL | wx.ALIGN_BOTTOM,
                colour=(0.8, 0.8, 0.8),
                opacity=1.0,
                deg=deg - 90
            )

            self.ticks.append((ox, oy, ix, iy, label))

    def _write_label(self, ctx, l):
        # Code dublicated (with unused parts removed here) by
        # odemis.gui.comp.overlay.base.Overlay, maybe could be shared in
        # odemis.gui.util.img

        # Cache the current context settings
        ctx.save()
        ctx.select_font_face(self._font_name, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)

        # For some reason, fonts look a little bit smaller when Cairo
        # plots them at an angle. We compensate for that by increasing the size
        # by 1 point in that case, so the size visually resembles that of
        # straight text.
        if l.deg not in (0.0, 180.0, None):
            ctx.set_font_size(l.font_size + 1)
        else:
            ctx.set_font_size(l.font_size)

        rx, ry = l.pos
        phi = math.radians(l.deg) - math.pi
        ctx.translate(rx, ry)
        ctx.rotate(phi)
        ctx.translate(-rx, -ry)

        # Take care of newline characters
        parts = l.text.split("\n")

        # Calculate the rendering position
        x, y = l.pos

        lw, lh = 0, 0
        plh = l.font_size  # default to font size, but should always get updated
        for p in parts:
            plw, plh = ctx.text_extents(p)[2:4]
            lw = max(lw, plw)
            lh += plh

        # Cairo renders text from the bottom left, but we want to treat
        # the top left as the origin. So we need to add the height (lower the
        # render point), to make the given position align with the top left.
        y += plh

        # Horizontally align the label
        x -= lw / 2.0

        # Vertically align the label
        y -= lh

        l.render_pos = x, y
        l.text_size = lw, lh

        # Draw Shadow
        ctx.set_source_rgba(0.0, 0.0, 0.0, 0.7 * l.opacity)
        ofst = 0
        for part in parts:
            ctx.move_to(x + 1, y + 1 + ofst)
            ofst += l.font_size
            ctx.show_text(part)

        # Draw Text
        ctx.set_source_rgba(*(l.colour + (l.opacity,)))

        ofst = 0
        for part in parts:
            ctx.move_to(x, y + ofst)
            ofst += l.font_size + 1
            ctx.show_text(part)

        ctx.restore()

    def draw_ar(self, ctx):
        # Draw frame that covers everything outside the center circle
        ctx.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        ctx.set_source_rgb(0.2, 0.2, 0.2)

        ctx.rectangle(0, 0, self.ClientSize.x, self.ClientSize.y)
        ctx.arc(self.center_x, self.center_y, self.inner_radius, 0, self.tau)
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

        # Draw tick labels
        ctx.set_source_rgb(0.8, 0.8, 0.8)
        for _, _, _, _, label in self.ticks:
            self._write_label(ctx, label)

    def spectrum_to_export_data(self, spectrum, raw, unit, spectrum_range):
        if raw:
            return spectrum
        else:
            # Draw spectrumbar plot
            data = zip(spectrum_range, spectrum)
            self.fill_colour = BAR_PLOT_COLOUR
            data_to_draw = numpy.zeros((self.ClientSize.y, self.ClientSize.x, 4), dtype=numpy.uint8)
            surface = cairo.ImageSurface.create_for_data(
                data_to_draw, cairo.FORMAT_ARGB32, self.ClientSize.x, self.ClientSize.y)
            ctx = cairo.Context(surface)
            # calculate data characteristics
            horz, vert = zip(*data)
            min_x = min(horz)
            max_x = max(horz)
            min_y = min(vert)
            max_y = max(vert)
            range_x = (min_x, max_x)
            data_width = max_x - min_x
            range_y = (min_y, max_y)
            data_height = max_y - min_y
            self._bar_plot(ctx, data, data_width, range_x, data_height, range_y)

            # Draw horizontal scale legend
            self._value_range = (spectrum_range[0], spectrum_range[-1])
            self._orientation = wx.HORIZONTAL
            self._tick_spacing = 120
            self.unit = unit
            self.scale_width = 40
            self.scale_height = 30
            scale_x_draw = numpy.zeros((self.scale_height, self.ClientSize.x, 4), dtype=numpy.uint8)
            scale_x_draw.fill(25)
            surface = cairo.ImageSurface.create_for_data(
                scale_x_draw, cairo.FORMAT_ARGB32, self.ClientSize.x, self.scale_height)
            ctx = cairo.Context(surface)
            self.draw_scale(ctx)
            data_with_legend = numpy.append(data_to_draw, scale_x_draw, axis=0)

            # Draw vertical scale legend
            self._orientation = wx.VERTICAL
            self._tick_spacing = 80
            self._value_range = (min(spectrum), max(spectrum))
            self.unit = None
            scale_y_draw = numpy.zeros((self.ClientSize.y, self.scale_width, 4), dtype=numpy.uint8)
            scale_y_draw.fill(25)
            surface = cairo.ImageSurface.create_for_data(
                scale_y_draw, cairo.FORMAT_ARGB32, self.scale_width, self.ClientSize.y)
            ctx = cairo.Context(surface)
            self.draw_scale(ctx)

            # Extend y scale bar to fit the height of the bar plot with the x
            # scale bar attached
            extend = numpy.empty((self.scale_height, self.scale_width, 4), dtype=numpy.uint8)
            extend.fill(25)
            scale_y_draw = numpy.append(scale_y_draw, extend, axis=0)
            data_with_legend = numpy.append(scale_y_draw, data_with_legend, axis=1)

            spec_plot = model.DataArray(data_with_legend)
            spec_plot.metadata[model.MD_DIMS] = 'YXC'
            return spec_plot

    def value_to_pixel(self, value):
        """ Map range value to legend pixel position """
        if self._pixel_space is None:
            return None
        elif None not in (self._vtp_ratio, self._value_range):
            pixel = (value - self._value_range[0]) * self._vtp_ratio
            pixel = int(round(pixel))
        else:
            pixel = 0
        return pixel if self._orientation == wx.HORIZONTAL else self._pixel_space - pixel

    def draw_scale(self, ctx):
        if self._value_range is None:
            return

        self.calculate_ticks()
        csize = self.ClientSize

        # Set Font
        font = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT)

        ctx.select_font_face(font.GetFaceName(), cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(font.GetPointSize())

        ctx.set_source_rgb(*self.fill_colour)
        ctx.set_line_width(2)
        ctx.set_line_join(cairo.LINE_JOIN_MITER)

        max_width = 0
        prev_lpos = 0 if self._orientation == wx.HORIZONTAL else csize.y

        for i, (pos, val) in enumerate(self._tick_list):
            label = units.readable_str(val, self.unit, 3)
            _, _, lbl_width, lbl_height, _, _ = ctx.text_extents(label)

            if self._orientation == wx.HORIZONTAL:
                lpos = pos - (lbl_width // 2)
                lpos = max(min(lpos, csize.x - lbl_width - 2), 2)
                # print (i, prev_right, lpos)
                if prev_lpos < lpos:
                    ctx.move_to(lpos, lbl_height + 8)
                    ctx.show_text(label)
                    ctx.move_to(pos, 5)
                    ctx.line_to(pos, 0)
                prev_lpos = lpos + lbl_width
            else:
                max_width = max(max_width, lbl_width)
                lpos = pos + (lbl_height // 2)
                lpos = max(min(lpos, csize.y), 2)

                if prev_lpos >= lpos + 20 or i == 0:
                    ctx.move_to(self.scale_width - lbl_width - 9, lpos)
                    ctx.show_text(label)
                    ctx.move_to(self.scale_width - 5, pos)
                    ctx.line_to(self.scale_width, pos)
                prev_lpos = lpos + lbl_height

            ctx.stroke()

    def calculate_ticks(self):
        """ Calculate which values in the range to represent as ticks on the axis

        The result is stored in the _tick_list attribute as a list of pixel position and value pairs

        """

        if self._value_range is None:
            return

        min_val, max_val = self._value_range

        # Get the horizontal/vertical space in pixels
        self._pixel_space = self.ClientSize[self._orientation != wx.HORIZONTAL]

        if self._orientation == wx.HORIZONTAL:
            min_pixel = 0
        else:
            # Don't display ticks too close from the left border
            min_pixel = 10

        # Range width
        value_space = max_val - min_val
        if value_space == 0:
            logging.info("Trying to compute legend tick with empty range %s", self._value_range)
            self._vtp_ratio = None
            # Just one tick, at the origin
            pixel = max(min_pixel, self.value_to_pixel(min_val))
            self._tick_list = [(pixel, min_val)]
            return

        self._vtp_ratio = self._pixel_space / value_space

        num_ticks = self._pixel_space // self._tick_spacing
        # Calculate the best step size in powers of 10, so it will cover at
        # least the distance `val_dist`
        value_step = 1e-12

        # Increase the value step tenfold while it fits more than num_ticks times
        # in the range
        while value_step and value_space / value_step > num_ticks:
            value_step *= 10
        # logging.debug("Value step is %s after first iteration with range %s",
        #               value_step, value_space)

        # Divide the value step by two,
        while value_step and value_space / value_step < num_ticks:
            value_step /= 2
        # logging.debug("Value step is %s after second iteration with range %s",
        #               value_step, value_space)

        first_val = (int(min_val / value_step) + 1) * value_step if value_step else 0
        # logging.debug("Setting first tick at value %s", first_val)

        tick_values = [min_val]
        cur_val = first_val

        while cur_val < max_val:
            tick_values.append(cur_val)
            cur_val += value_step

        ticks = []
        for tick_value in tick_values:
            pixel = self.value_to_pixel(tick_value)
            pix_val = (pixel, tick_value)
            if pix_val not in ticks:
                if min_pixel <= pixel <= self._pixel_space:
                    ticks.append(pix_val)

        self._tick_list = ticks

    def _val_x_to_pos_x(self, val_x, data_width=None, range_x=None):
        """ Translate an x value to an x position in pixels
        The minimum x value is considered to be pixel 0 and the maximum is the canvas width. The
        parameter will be clipped if it's out of range.
        :param val_x: (float) The value to map
        :return: (float)
        """
        range_x = range_x or self.data_prop[1]
        data_width = data_width or self.data_prop[0]

        if data_width:
            # Clip val_x
            x = min(max(range_x[0], val_x), range_x[1])
            perc_x = (x - range_x[0]) / data_width
            return perc_x * self.ClientSize.x
        else:
            return 0

    def _val_y_to_pos_y(self, val_y, data_height=None, range_y=None):
        """ Translate an y value to an y position in pixels
        The minimum y value is considered to be pixel 0 and the maximum is the canvas width. The
        parameter will be clipped if it's out of range.
        :param val_y: (float) The value to map
        :return: (float)
        """
        range_y = range_y or self.data_prop[3]
        data_height = data_height or self.data_prop[2]

        if data_height:
            y = min(max(range_y[0], val_y), range_y[1])
            perc_y = (range_y[1] - y) / data_height
            return perc_y * self.ClientSize.y
        else:
            return 0

    def _bar_plot(self, ctx, data, data_width, range_x, data_height, range_y):
        """ Do a bar plot of the current `_data` """

        if len(data) < 2:
            return

        vx_to_px = self._val_x_to_pos_x
        vy_to_py = self._val_y_to_pos_y

        line_to = ctx.line_to
        ctx.set_source_rgb(*self.fill_colour)

        diff = (data[1][0] - data[0][0]) / 2.0
        px = vx_to_px(data[0][0] - diff, data_width, range_x)
        py = vy_to_py(0, data_height, range_y)

        ctx.move_to(px, py)
        # print "-", px, py

        for i, (vx, vy) in enumerate(data[:-1]):
            py = vy_to_py(vy, data_height, range_y)
            # print "-", px, py
            line_to(px, py)
            px = vx_to_px((data[i + 1][0] + vx) / 2.0, data_width, range_x)
            # print "-", px, py
            line_to(px, py)

        py = vy_to_py(data[-1][1], data_height, range_y)
        # print "-", px, py
        line_to(px, py)

        diff = (data[-1][0] - data[-2][0]) / 2.0
        px = vx_to_px(data[-1][0] + diff, data_width, range_x)
        # print "-", px, py
        line_to(px, py)

        py = vy_to_py(0, data_height, range_y)
        # print "-", px, py
        line_to(px, py)

        ctx.close_path()
        ctx.fill()

    def images_to_export_data(self, images, rgb=True, interpolate_data=True):
        # The list of images to export
        data_to_export = []

        # meters per pixel for the focussed window
        view_mpp = self.microscope_view.mpp.value
        vp = self.get_viewport_by_view(self.microscope_view)
        mpp_screen = 1e-3 * wx.DisplaySizeMM()[0] / wx.DisplaySize()[0]
        mag = mpp_screen / self.microscope_view.mpp.value
        hfw = self.microscope_view.mpp.value * vp.GetClientSize()[0]

        # Find min pixel size
        min_pxs = min([im.metadata['dc_scale'] for im in images])
        max_res = SCALE_FACTOR * self.ClientSize.y, SCALE_FACTOR * self.ClientSize.x

        # Check that resolution of all images remains within limits if we use
        # the smallest pixel size, otherwise adjust it
        for i, im in enumerate(images):
            phys_shape = [a * b for a, b in zip(im.shape[:2], im.metadata['dc_scale'])]
            resized_res = tuple([int(a / b) for a, b in zip(phys_shape, min_pxs)])
            clipped_resized_res = tuple(numpy.clip(resized_res, (1, 1), max_res))
            if resized_res != clipped_resized_res:
                min_pxs = tuple([a / b for a, b in zip(phys_shape, clipped_resized_res)])

        # Make surface based on the maximum resolution
        data_to_draw = numpy.zeros((max_res[0], max_res[1], 4), dtype=numpy.uint8)
        surface = cairo.ImageSurface.create_for_data(
            data_to_draw, cairo.FORMAT_ARGB32, max_res[1], max_res[0])
        ctx = cairo.Context(surface)

        # The buffer center is the same as the view window's center
        self._buffer_center = tuple(self.microscope_view.view_pos.value)
        self._buffer_scale = (view_mpp / SCALE_FACTOR,
                              view_mpp / SCALE_FACTOR)
        vp = self.get_viewport_by_view(self.microscope_view)
        self._buffer_size = max_res[1], max_res[0]

        # scale bar details
        bar_width = self._buffer_size[0] // 4
        actual_width = bar_width * self._buffer_scale[0]
        actual_width = units.round_significant(actual_width, 1)

        n = len(images)
        last_image = images.pop()
        # For every image, except the last
        i = 0
        for i, im in enumerate(images):
            if im.metadata['blend_mode'] == BLEND_SCREEN or (not rgb):
                # No transparency in case of "raw" export
                merge_ratio = 1.0
            else:
                merge_ratio = 1 - i / n

            self._draw_image(
                ctx,
                im,
                im.metadata['dc_center'],
                merge_ratio,
                im_scale=im.metadata['dc_scale'],
                rotation=im.metadata['dc_rotation'],
                shear=im.metadata['dc_shear'],
                flip=im.metadata['dc_flip'],
                blend_mode=im.metadata['blend_mode'],
                interpolate_data=interpolate_data
            )
            if not rgb:
                # Create legend
                legend_to_draw = numpy.zeros((n * (self._buffer_size[1] // 24) + (self._buffer_size[1] // 12), self._buffer_size[0], 4), dtype=numpy.uint8)
                legend_surface = cairo.ImageSurface.create_for_data(
                    legend_to_draw, cairo.FORMAT_ARGB32, self._buffer_size[0], n * (self._buffer_size[1] // 24) + (self._buffer_size[1] // 12))
                legend_ctx = cairo.Context(legend_surface)
                self._draw_legend(legend_ctx, images + [last_image], self._buffer_size, view_mpp, mag,
                                  hfw, bar_width, actual_width, last_image.metadata['date'], self.streams_data, im.metadata['name'])

                new_data_to_draw = numpy.zeros((data_to_draw.shape[0], data_to_draw.shape[1]), dtype=numpy.uint32)
                new_data_to_draw[:, :] = numpy.left_shift(data_to_draw[:, :, 2], 8, dtype=numpy.uint32) | data_to_draw[:, :, 1]
                new_data_to_draw[:, :] = new_data_to_draw[:, :] | numpy.left_shift(data_to_draw[:, :, 0], 16, dtype=numpy.uint32)
                new_data_to_draw[:, :] = new_data_to_draw[:, :] | numpy.left_shift(data_to_draw[:, :, 3], 24, dtype=numpy.uint32)
                new_data_to_draw = new_data_to_draw.astype(self._min_type)
                # Turn legend to grayscale
                new_legend_to_draw = legend_to_draw[:, :, 0] + legend_to_draw[:, :, 1] + legend_to_draw[:, :, 2]
                new_legend_to_draw = new_legend_to_draw.astype(self._min_type)
                new_legend_to_draw = numpy.where(new_legend_to_draw == 0, numpy.min(new_data_to_draw), numpy.max(new_data_to_draw))
                data_with_legend = numpy.append(new_data_to_draw, new_legend_to_draw, axis=0)
                # Clip background to baseline
                baseline = self.streams_data[im.metadata['name']][-1]
                data_with_legend = numpy.clip(data_with_legend, baseline, numpy.max(new_data_to_draw))
                data_to_export.append(model.DataArray(data_with_legend, im.metadata))

                data_to_draw = numpy.zeros((max_res[0], max_res[1], 4), dtype=numpy.uint8)
                surface = cairo.ImageSurface.create_for_data(
                    data_to_draw, cairo.FORMAT_ARGB32, max_res[1], max_res[0])
                ctx = cairo.Context(surface)

        if not images or last_image.metadata['blend_mode'] == BLEND_SCREEN or (not rgb):
            merge_ratio = 1.0
        else:
            merge_ratio = self.merge_ratio

        self._draw_image(
            ctx,
            last_image,
            last_image.metadata['dc_center'],
            merge_ratio,
            im_scale=last_image.metadata['dc_scale'],
            rotation=last_image.metadata['dc_rotation'],
            shear=last_image.metadata['dc_shear'],
            flip=last_image.metadata['dc_flip'],
            blend_mode=last_image.metadata['blend_mode'],
            interpolate_data=interpolate_data
        )
        # Create legend
        legend_to_draw = numpy.zeros((n * (self._buffer_size[1] // 24) + (self._buffer_size[1] // 12), self._buffer_size[0], 4), dtype=numpy.uint8)
        legend_surface = cairo.ImageSurface.create_for_data(
            legend_to_draw, cairo.FORMAT_ARGB32, self._buffer_size[0], n * (self._buffer_size[1] // 24) + (self._buffer_size[1] // 12))
        legend_ctx = cairo.Context(legend_surface)
        self._draw_legend(legend_ctx, images + [last_image], self._buffer_size, view_mpp, mag,
                          hfw, bar_width, actual_width, last_image.metadata['date'], self.streams_data, last_image.metadata['name'] if (not rgb) else None)
        if not rgb:
            new_data_to_draw = numpy.zeros((data_to_draw.shape[0], data_to_draw.shape[1]), dtype=numpy.uint32)
            new_data_to_draw[:, :] = numpy.left_shift(data_to_draw[:, :, 2], 8, dtype=numpy.uint32) | data_to_draw[:, :, 1]
            new_data_to_draw[:, :] = new_data_to_draw[:, :] | numpy.left_shift(data_to_draw[:, :, 0], 16, dtype=numpy.uint32)
            new_data_to_draw[:, :] = new_data_to_draw[:, :] | numpy.left_shift(data_to_draw[:, :, 3], 24, dtype=numpy.uint32)
            new_data_to_draw = new_data_to_draw.astype(self._min_type)
            # Turn legend to grayscale
            new_legend_to_draw = legend_to_draw[:, :, 0] + legend_to_draw[:, :, 1] + legend_to_draw[:, :, 2]
            new_legend_to_draw = new_legend_to_draw.astype(self._min_type)
            new_legend_to_draw = numpy.where(new_legend_to_draw == 0, numpy.min(new_data_to_draw), numpy.max(new_data_to_draw))
            data_with_legend = numpy.append(new_data_to_draw, new_legend_to_draw, axis=0)
            # Clip background to baseline
            baseline = self.streams_data[last_image.metadata['name']][-1]
            data_with_legend = numpy.clip(data_with_legend, baseline, numpy.max(new_data_to_draw))
        else:
            data_with_legend = numpy.append(data_to_draw, legend_to_draw, axis=0)
            data_with_legend[:, :, [2, 0]] = data_with_legend[:, :, [0, 2]]
            last_image.metadata[model.MD_DIMS] = 'YXC'
        data_to_export.append(model.DataArray(data_with_legend, last_image.metadata))
        return data_to_export

    def _draw_legend(self, legend_ctx, images, buffer_size, mpp, mag=None, hfw=None,
                     scale_bar_width=None, scale_actual_width=None, date=None, streams_data=None, stream_name=None):
        init_x_pos = 100
        upper_part = 0.25
        middle_part = 0.5
        lower_part = 0.85
        large_font = 40  # used for general data
        small_font = 30  # used for stream data
        n = len(images)
        # Just make cell dimensions analog to the image buffer dimensions
        big_cell_height = buffer_size[1] // 12
        small_cell_height = buffer_size[1] // 24
        cell_x_step = buffer_size[0] // 5
        legend_ctx.set_source_rgb(0, 0, 0)
        legend_ctx.rectangle(0, 0, buffer_size[0], n * small_cell_height + big_cell_height)
        legend_ctx.fill()
        legend_ctx.set_source_rgb(1, 1, 1)
        legend_ctx.set_line_width(1)

        # draw separation lines
        legend_y_pos = big_cell_height
        legend_ctx.move_to(0, legend_y_pos)
        legend_ctx.line_to(buffer_size[0], legend_y_pos)
        legend_ctx.stroke()
        for i in range(n - 1):
            legend_y_pos += small_cell_height
            legend_ctx.move_to(0, legend_y_pos)
            legend_ctx.line_to(buffer_size[0], legend_y_pos)
            legend_ctx.stroke()

        # write Magnification
        # TODO: Don't rely on a Microsoft font, just use DejaVu or something basic
        legend_ctx.select_font_face("Georgia", cairo.FONT_SLANT_NORMAL)
        legend_ctx.set_font_size(large_font)
        legend_x_pos = init_x_pos
        legend_y_pos = middle_part * big_cell_height
        legend_ctx.move_to(legend_x_pos, legend_y_pos)
        mag_text = u"Mag: × %s" % units.readable_str(units.round_significant(mag, 3))
#         if n == 1:
#             mag_dig = images[0].metadata['dc_scale'][0] / mpp
#             label = mag_text + u" (Digital: × %s)" % units.readable_str(units.round_significant(mag_dig, 2))
#         else:
        label = mag_text
        legend_ctx.show_text(label)

        # write HFW
        legend_x_pos += cell_x_step
        legend_ctx.move_to(legend_x_pos, legend_y_pos)
        hfw = units.round_significant(hfw, 4)
        label = u"HFW: %s" % units.readable_str(hfw, "m", sig=3)
        legend_ctx.show_text(label)

        # Draw scale bar
        legend_x_pos += cell_x_step
        legend_y_pos = upper_part * big_cell_height
        legend_ctx.move_to(legend_x_pos, legend_y_pos)
        legend_y_pos = middle_part * big_cell_height
        legend_ctx.line_to(legend_x_pos, legend_y_pos)
        legend_y_pos = lower_part * big_cell_height
        legend_ctx.move_to(legend_x_pos, legend_y_pos)
        label = units.readable_str(scale_actual_width, "m", sig=2)
        legend_ctx.show_text(label)
        legend_y_pos = middle_part * big_cell_height
        legend_ctx.move_to(legend_x_pos, legend_y_pos)
        legend_x_pos += scale_bar_width
        legend_ctx.line_to(legend_x_pos, legend_y_pos)
        legend_y_pos = upper_part * big_cell_height
        legend_ctx.line_to(legend_x_pos, legend_y_pos)
        legend_ctx.stroke()
        legend_x_pos += buffer_size[0] // 20
        legend_y_pos = middle_part * big_cell_height
        legend_ctx.move_to(legend_x_pos, legend_y_pos)

        # write acquisition date
        if date is not None:
            label = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(date))
            legend_ctx.show_text(label)

        # write stream data
        legend_y_pos = 0.75 * big_cell_height
        for name, data in streams_data.iteritems():
            legend_ctx.set_font_size(small_font)
            if name == stream_name:
                # in case of multifile, spot this particular stream with a
                # circle next to the stream name
                legend_x_pos = init_x_pos / 2
                legend_y_pos += small_cell_height
                legend_ctx.move_to(legend_x_pos, legend_y_pos)
                legend_ctx.arc(legend_x_pos, legend_y_pos, 10, 0, 2 * math.pi)
                legend_ctx.fill()
                legend_ctx.stroke()
                legend_x_pos = init_x_pos
                legend_ctx.move_to(legend_x_pos, legend_y_pos)
            else:
                legend_x_pos = init_x_pos
                legend_y_pos += small_cell_height
                legend_ctx.move_to(legend_x_pos, legend_y_pos)
            legend_ctx.show_text(name)
            legend_ctx.set_font_size(small_font)
            legend_x_pos += cell_x_step
            legend_y_pos_store = legend_y_pos
            for i, d in enumerate(data[:-1]):
                legend_ctx.move_to(legend_x_pos, legend_y_pos)
                legend_ctx.show_text(d)
                if (i % 2 == 1):
                    legend_x_pos += cell_x_step
                    legend_y_pos -= 0.4 * small_cell_height
                else:
                    legend_y_pos += 0.4 * small_cell_height
            legend_y_pos = legend_y_pos_store

    def _draw_image(self, ctx, im_data, w_im_center, opacity=1.0,
                    im_scale=(1.0, 1.0), rotation=None, shear=None, flip=None,
                    blend_mode=BLEND_DEFAULT, interpolate_data=True):
        """ Draw the given image to the Cairo context

        The buffer is considered to have it's 0,0 origin at the top left

        :param ctx: (cairo.Context) Cario context to draw on
        :param im_data: (DataArray) Image to draw
        :param w_im_center: (2-tuple float)
        :param opacity: (float) [0..1] => [transparent..opaque]
        :param im_scale: (float, float)
        :param rotation: (float) Clock-wise rotation around the image center in radians
        :param shear: (float) Horizontal shearing of the image data (around it's center)
        :param flip: (wx.HORIZONTAL | wx.VERTICAL) If and how to flip the image
        :param blend_mode: (int) Graphical blending type used for transparency

        """

        # Fully transparent image does not need to be drawn
        if opacity < 1e-8:
            logging.debug("Skipping draw: image fully transparent")
            return

        # Determine the rectangle the image would occupy in the buffer
        b_im_rect = self._calc_img_buffer_rect(im_data, im_scale, w_im_center)
        # print b_im_rect
        x, y, w, h = b_im_rect
        # Rotate if needed
        ctx.save()

        # apply transformations if needed
        apply_rotation(ctx, rotation, b_im_rect)
        apply_shear(ctx, shear, b_im_rect)
        apply_flip(ctx, flip, b_im_rect)

        ctx.translate(x, y)
        width_ratio = float(im_scale[0]) / float(self._buffer_scale[0])
        height_ratio = float(im_scale[1]) / float(self._buffer_scale[1])
        ctx.scale(width_ratio, height_ratio)

        if im_data.metadata.get('dc_keepalpha', True):
            im_format = cairo.FORMAT_ARGB32
        else:
            im_format = cairo.FORMAT_RGB24

        height, width, _ = im_data.shape

        # Note: Stride calculation is done automatically when no stride parameter is provided.
        stride = cairo.ImageSurface.format_stride_for_width(im_format, width)

        imgsurface = cairo.ImageSurface.create_for_data(im_data, im_format, width, height, stride)

        # In Cairo a pattern is the 'paint' that it uses to draw
        surfpat = cairo.SurfacePattern(imgsurface)
        # Set the filter, so we get best quality but slow scaling
        # In opposition to the GUI gallery tab, here we care more about the
        # quality of the exported image than being fast.
        if interpolate_data:
            surfpat.set_filter(cairo.FILTER_BEST)
        else:
            # In case of "raw" export try to maintain the original data
            surfpat.set_filter(cairo.FILTER_NEAREST)

        ctx.set_source(surfpat)
        ctx.set_operator(blend_mode)

        if opacity < 1.0:
            ctx.paint_with_alpha(opacity)
        else:
            ctx.paint()

        # Restore the cached transformation matrix
        ctx.restore()

    def _calc_img_buffer_rect(self, im_data, im_scale, w_im_center):
        """ Compute the rectangle containing the image in buffer coordinates

        The (top, left) value are relative to the 0,0 top left of the buffer.

        :param im_data: (DataArray) image data
        :param im_scale: (float, float) The x and y scales of the image
        :param w_im_center: (float, float) The center of the image in world coordinates

        :return: (float, float, float, float) top, left, width, height

        """

        # Scale the image
        im_h, im_w = im_data.shape[:2]
        scale_x, scale_y = im_scale
        scaled_im_size = (im_w * scale_x, im_h * scale_y)

        # Calculate the top left
        w_topleft = (w_im_center[0] - (scaled_im_size[0] / 2),
                     w_im_center[1] - (scaled_im_size[1] / 2))

        b_topleft = (round(((w_topleft[0] - self._buffer_center[0]) / self._buffer_scale[0]) + (self._buffer_size[0] / 2)),
                     round(((w_topleft[1] + self._buffer_center[1]) / self._buffer_scale[1]) + (self._buffer_size[1] / 2)))

        final_size = (scaled_im_size[0] / self._buffer_scale[0], scaled_im_size[1] / self._buffer_scale[1])
        return b_topleft + final_size

    def set_images(self, im_args):
        """ Set (or update) image

        :paran im_args: (list of tuples): Each element is either None or
            (im, w_pos, scale, keepalpha, rotation, name, blend_mode)

            0. im (wx.Image): the image
            1. w_pos (2-tuple of float): position of the center of the image (in world units)
            2. scale (float, float): scale of the image
            3. keepalpha (boolean): whether the alpha channel must be used to draw
            4. rotation (float): clockwise rotation in radians on the center of the image
            5. shear (float): horizontal shear relative to the center of the image
            6. flip (int): Image horz or vert flipping. 0 for no flip, wx.HORZ and wx.VERT otherwise
            7. blend_mode (int): blend mode to use for the image. Defaults to `source` which
                    just overrides underlying layers.
            8. name (str): name of the stream that the image originated from
            9. date (int): seconds since epoch

        """

        images = []

        for args in im_args:
            if args is None:
                images.append(None)
            else:
                im, w_pos, scale, keepalpha, rotation, shear, flip, blend_mode, name, date = args

                if not blend_mode:
                    blend_mode = BLEND_DEFAULT

                try:
                    depth = im.shape[2]

                    if depth == 3:
                        im = add_alpha_byte(im)
                    elif depth != 4:  # Both ARGB32 and RGB24 need 4 bytes
                        raise ValueError("Unsupported colour byte size (%s)!" % depth)
                except IndexError:
                    # Handle grayscale images pretending they are rgb
                    pass

                im.metadata['dc_center'] = w_pos
                im.metadata['dc_scale'] = scale
                im.metadata['dc_rotation'] = rotation
                im.metadata['dc_shear'] = shear
                im.metadata['dc_flip'] = flip
                im.metadata['dc_keepalpha'] = keepalpha
                im.metadata['blend_mode'] = blend_mode
                im.metadata['name'] = name
                im.metadata['date'] = date

                images.append(im)

        self.images = images

    def _get_ordered_images(self, rgb=True):
        """ Return the list of images to display, ordered bottom to top (=last to draw)

        The last image of the list will have the merge ratio applied (as opacity)

        """

        streams = self.microscope_view.getStreams()
        images_opt = []
        images_spc = []
        images_std = []
        streams_data = {}

        self._min_type = numpy.uint8
        for s in streams:
            if not s:
                # should not happen, but let's not completely fail on this
                logging.error("StreamTree has a None stream")
                continue

            if not hasattr(s, "image") or s.image.value is None:
                continue

            # FluoStreams are merged using the "Screen" method that handles colour
            # merging without decreasing the intensity.
            data_raw = s.raw[0]
            if rgb:
                data = s.image.value
            else:
                # Pretend to be rgb
                if numpy.can_cast(self._min_type, min_type(data_raw)):
                    self._min_type = min_type(data_raw)

                # Split the bits in R,G,B,A
                data = model.DataArray(numpy.zeros((data_raw.shape[0], data_raw.shape[1], 4), dtype=numpy.uint8),
                                       data_raw.metadata)
                data[:, :, 0] = numpy.right_shift(data_raw[:, :], 8) & 255
                data[:, :, 1] = data_raw[:, :] & 255
                data[:, :, 2] = numpy.right_shift(data_raw[:, :], 16) & 255
                data[:, :, 3] = numpy.right_shift(data_raw[:, :], 24) & 255

            if isinstance(s, stream.OpticalStream):
                images_opt.append((data, BLEND_SCREEN, s.name.value))
            elif isinstance(s, (stream.SpectrumStream, stream.CLStream)):
                images_spc.append((data, BLEND_DEFAULT, s.name.value))
            else:
                images_std.append((data, BLEND_DEFAULT, s.name.value))

            # metadata useful for the legend
            stream_data = []
            if data_raw.metadata.get(model.MD_EXP_TIME, None):
                stream_data.append(u"Exp. time: %s" % units.readable_str(data_raw.metadata[model.MD_EXP_TIME], "s"))
            if data_raw.metadata.get(model.MD_LIGHT_POWER, None):
                stream_data.append(units.readable_str(data_raw.metadata[model.MD_LIGHT_POWER], "W", sig=3))
            if data_raw.metadata.get(model.MD_EBEAM_VOLTAGE, None):
                stream_data.append(units.readable_str(data_raw.metadata[model.MD_EBEAM_VOLTAGE], "V", sig=3))
            if data_raw.metadata.get(model.MD_EBEAM_CURRENT, None):
                stream_data.append(units.readable_str(data_raw.metadata[model.MD_EBEAM_CURRENT], "A", sig=3))
            if data_raw.metadata.get(model.MD_DWELL_TIME, None):
                stream_data.append(u"dwelltime: %s" % units.readable_str(data_raw.metadata[model.MD_DWELL_TIME], "s"))
            if data_raw.metadata.get(model.MD_FILTER_NAME, None):
                stream_data.append(data_raw.metadata[model.MD_FILTER_NAME])
            if data_raw.metadata.get(model.MD_IN_WL, None):
                stream_data.append(u"ex.: %s" % units.readable_str(numpy.average(data_raw.metadata[model.MD_IN_WL]), "m", sig=3))
            if data_raw.metadata.get(model.MD_OUT_WL, None):
                stream_data.append(u"em.: %s" % units.readable_str(numpy.average(data_raw.metadata[model.MD_OUT_WL]), "m", sig=3))
            if isinstance(s, stream.OpticalStream):
                baseline = data_raw.metadata.get(model.MD_BASELINE, 0)
            else:
                baseline = numpy.min(data_raw)
            stream_data.append(baseline)
            streams_data[s.name.value] = stream_data

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

        return images_opt + images_std + images_spc, streams_data

    def physical_to_world_pos(self, phy_pos):
        """ Translate physical coordinates into world coordinates.
        Works both for absolute and relative values.

        :param phy_pos: (float, float) "physical" coordinates in m
        :return: (float, float)
        """
        # The y value needs to be flipped between physical and world coordinates.
        return phy_pos[0], -phy_pos[1]

    def _convert_streams_to_images(self, rgb=True):
        """ Temporary function to convert the StreamTree to a list of images as
        the export function currently expects.

        """
        images, streams_data = self._get_ordered_images(rgb)

        # add the images in order
        ims = []
        im_cache = {}
        for rgbim, blend_mode, name in images:
            # TODO: convert to RGBA later, in canvas and/or cache the conversion
            # On large images it costs 100 ms (per image and per canvas)

            if not rgb:
                # TODO use another method to fake rgba format
                rgba_im = format_rgba_darray(rgbim)
            else:
                # Get converted RGBA image from cache, or create it and cache it
                im_id = id(rgbim)
                if im_id in self.images_cache:
                    rgba_im = self.images_cache[im_id]
                    im_cache[im_id] = rgba_im
                else:
                    rgba_im = format_rgba_darray(rgbim)
                    im_cache[im_id] = rgba_im

            keepalpha = False
            date = rgbim.metadata.get(model.MD_ACQ_DATE, None)
            scale = rgbim.metadata[model.MD_PIXEL_SIZE]
            pos = self.physical_to_world_pos(rgbim.metadata[model.MD_POS])
            rot = rgbim.metadata.get(model.MD_ROTATION, 0)
            shear = rgbim.metadata.get(model.MD_SHEAR, 0)
            flip = rgbim.metadata.get(model.MD_FLIP, 0)

            ims.append((rgba_im, pos, scale, keepalpha, rot, shear, flip, blend_mode,
                        name, date))

        # Replace the old cache, so the obsolete RGBA images can be garbage collected
        self.images_cache = im_cache
        self.set_images(ims)
        self.streams_data = streams_data
        self.merge_ratio = self.microscope_view.stream_tree.kwargs.get("merge", 0.5)

    def ShowExportFileDialog(self, filename, default_exporter):
        """
        filename (string): full filename to propose by default
        default_exporter (module): default exporter to be used
        return (string or None): the new filename (or the None if the user cancelled)
                (string): the format name
                (string): spatial, AR or spectrum
        """
        # TODO use ExportConfig

        # Find the available formats (and corresponding extensions) according
        # to the export type
        export_type = self.get_export_type(self.microscope_view)
        formats_to_ext = self.get_export_formats(export_type)

        # current filename
        path, base = os.path.split(filename)
        wildcards, formats = formats_to_wildcards(formats_to_ext)
        dialog = wx.FileDialog(self._main_frame,
                               message="Choose a filename and destination",
                               defaultDir=path,
                               defaultFile="",
                               style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
                               wildcard=wildcards)

        # just default to the first format in EXPORTS[export_type]
        default_fmt = default_exporter.FORMAT
        try:
            idx = formats.index(default_fmt)
        except ValueError:
            idx = 0
        dialog.SetFilterIndex(idx)

        # Strip the extension, so that if the user changes the file format,
        # it will not have 2 extensions in a row.
        if base.endswith(default_exporter.EXTENSIONS[0]):
            base = base[:-len(default_exporter.EXTENSIONS[0])]
        dialog.SetFilename(base)

        # Show the dialog and check whether is was accepted or cancelled
        if dialog.ShowModal() != wx.ID_OK:
            return None, default_fmt, export_type

        # New location and name have been selected...
        # Store the path
        path = dialog.GetDirectory()

        # Store the format
        fmt = formats[dialog.GetFilterIndex()]

        # Check the filename has a good extension, or add the default one
        fn = dialog.GetFilename()
        ext = None
        for extension in formats_to_ext[fmt]:
            if fn.endswith(extension) and len(extension) > len(ext or ""):
                ext = extension

        if ext is None:
            if fmt == default_fmt and default_exporter.EXTENSIONS[0] in formats_to_ext[fmt]:
                # if the format is the same (and extension is compatible): keep
                # the extension. This avoid changing the extension if it's not
                # the default one.
                ext = default_exporter.EXTENSIONS[0]
            else:
                ext = formats_to_ext[fmt][0]  # default extension
            fn += ext

        return os.path.join(path, fn), fmt, export_type

    def get_export_formats(self, export_type):
        """
        Find the available file formats for the given export_type
        export_type (string): spatial, AR or spectrum
        return (dict string -> list of strings): name of each format -> list of
            extensions.
        """
        pr_formats, pp_formats = EXPORTERS[export_type]

        export_formats = {}
        # Look dynamically which format is available
        # First the print-ready formats
        for format_data in pr_formats:
            exporter = get_converter(format_data[0])
            export_formats[PR_PREFIX + " " + exporter.FORMAT] = exporter.EXTENSIONS
        # Now for post-processing formats
        for format_data in pp_formats:
            exporter = get_converter(format_data[0])
            export_formats[PP_PREFIX + " " + exporter.FORMAT] = exporter.EXTENSIONS

        if not export_formats:
            logging.error("No file converter found!")

        return export_formats
