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

import odemis.gui as gui
import odemis.gui.img as guiimg
import wx
from odemis import util
from odemis.acq.stream import UNDEFINED_ROI
from odemis.gui.comp.overlay.base import SEL_MODE_EDIT
from odemis.gui.comp.overlay.world_select import WorldSelectOverlay
from odemis.util.comp import compute_scanner_fov, get_fov_rect


class RepetitionSelectOverlay(WorldSelectOverlay):
    """
    Same as world selection overlay, but can also display a repetition over it.
    The type of display for the repetition is set by the .fill and repetition
    attributes. You must redraw the canvas for it to be updated.
    """

    FILL_NONE = 0
    FILL_GRID = 1
    FILL_POINT = 2

    def __init__(self, cnvs, roa=None, scanner=None, colour=gui.SELECTION_COLOUR):
        """
        roa (None or VA of 4 floats): If not None, it's linked to the rectangle
          displayed (ie, when the user changes the rectangle, its value is
          updated, and when its value changes, the rectangle is redrawn
          accordingly). Value is relative to the scanner (if passed), and otherwise it's absolute (in m).
        scanner (None or HwComponent): The scanner component to which the relative
         ROA. If None, the roa argument is interpreted as absolute physical coordinates (m). If it's a HwComponent, the roa will be interpreted as a ratio of its fielf of viewd.


        """
        WorldSelectOverlay.__init__(self, cnvs, colour)

        self._fill = self.FILL_NONE
        self._repetition = (0, 0)

        self._roa = roa
        self._scanner = scanner
        if roa:
            self._roa.subscribe(self.on_roa, init=True)

        self._bmp = None  # used to cache repetition with FILL_POINT
        # ROI for which the bmp is valid
        self._bmp_bpos = (None, None, None, None)

    @property
    def fill(self):
        return self._fill

    @fill.setter
    def fill(self, val):
        assert(val in [self.FILL_NONE, self.FILL_GRID, self.FILL_POINT])
        self._fill = val
        self._bmp = None

    @property
    def repetition(self):
        return self._repetition

    @repetition.setter
    def repetition(self, val):
        assert(len(val) == 2)
        self._repetition = val
        self._bmp = None

    def _get_scanner_rect(self):
        """
        Returns the (theoretical) scanning area of the scanner. Works even if the
        scanner has not send any image yet.
        returns (tuple of 4 floats): position in physical coordinates m (l, t, r, b)
        raises ValueError if scanner is not set or not actually a scanner
        """
        if self._scanner is None:
            raise ValueError("Scanner not set")
        fov = compute_scanner_fov(self._scanner)
        return get_fov_rect(self._scanner, fov)

    def convert_roi_phys_to_ratio(self, phys_rect):
        """
        Convert and truncate the ROI in physical coordinates to the coordinates
          relative to the SEM FoV. It also ensures the ROI can never be smaller
          than a pixel (of the scanner).
        phys_rect (None or 4 floats): physical position of the lt and rb points
        return (4 floats): ltrb positions relative to the FoV
        """
        # Get the position of the overlay in physical coordinates
        if phys_rect is None:
            return UNDEFINED_ROI

        # Position of the complete scan in physical coordinates
        sem_rect = self._get_scanner_rect()

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
        shape = self._scanner.shape
        rel_pixel_size = (1 / shape[0], 1 / shape[1])
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
        roi (4 floats): ltrb positions relative to the FoV
        return (None or 4 floats): physical position of the lt and rb points, or
          None if no ROI is defined
        """
        if roi == UNDEFINED_ROI:
            return None

        # convert relative position to physical position
        try:
            sem_rect = self._get_scanner_rect()
        except ValueError:
            logging.warning("Trying to convert a scanner ROI, but no scanner set")
            return None

        # In physical coordinates Y goes up, but in ROI, Y goes down => "1-"
        phys_rect = (sem_rect[0] + roi[0] * (sem_rect[2] - sem_rect[0]),
                     sem_rect[1] + (1 - roi[3]) * (sem_rect[3] - sem_rect[1]),
                     sem_rect[0] + roi[2] * (sem_rect[2] - sem_rect[0]),
                     sem_rect[1] + (1 - roi[1]) * (sem_rect[3] - sem_rect[1]))

        return phys_rect

    def on_roa(self, roa):
        """ Update the ROA overlay with the new roa VA data

        roi (tuple of 4 floats): left, top, right, bottom position relative to the SEM image

        """
        if self._scanner:
            phys_rect = self.convert_roi_ratio_to_phys(roa)
        else:
            phys_rect = roa

        self.set_physical_sel(phys_rect)
        wx.CallAfter(self.cnvs.request_drawing_update)

    def on_left_up(self, evt):
        WorldSelectOverlay.on_left_up(self, evt)
        if self._roa:
            if self.active.value:
                if self.get_size() != (None, None):
                    phys_rect = self.get_physical_sel()
                    if self._scanner:
                        rect = self.convert_roi_phys_to_ratio(phys_rect)
                    else:
                        rect = phys_rect

                    # Update VA. We need to unsubscribe to be sure we don't received
                    # intermediary values as the VA is modified by the stream further on, and
                    # VA don't ensure the notifications are ordered (so the listener could
                    # receive the final value, and then our requested ROI value).
                    self._roa.unsubscribe(self.on_roa)
                    self._roa.value = rect
                    self._roa.subscribe(self.on_roa, init=True)
                else:
                    self._roa.value = UNDEFINED_ROI

        else:
            logging.warning("Expected ROA not found!")

    def _draw_points(self, ctx):
        # Calculate the offset of the center of the buffer relative to the
        # top left of the buffer
        offset = self.cnvs.get_half_buffer_size()

        # The start and end position, in buffer coordinates. The return
        # values may extend beyond the actual buffer when zoomed in.
        b_pos = (self.cnvs.phys_to_buffer(self.p_start_pos, offset) +
                 self.cnvs.phys_to_buffer(self.p_end_pos, offset))
        b_pos = self._normalize_rect(b_pos)
        # logging.debug("start and end buffer pos: %s", b_pos)

        # Calculate the width and height in buffer pixels. Again, this may
        # be wider and higher than the actual buffer.
        width = b_pos[2] - b_pos[0]
        height = b_pos[3] - b_pos[1]

        # logging.debug("width and height: %s %s", width, height)

        # Clip the start and end positions using the actual buffer size
        start_x, start_y = self.cnvs.clip_to_buffer(b_pos[:2])
        end_x, end_y = self.cnvs.clip_to_buffer(b_pos[2:4])

        # logging.debug(
        #     "clipped start and end: %s", (start_x, start_y, end_x, end_y))

        rep_x, rep_y = self.repetition

        # The step size in pixels
        step_x = width / rep_x
        step_y = height / rep_y

        if width // 3 < rep_x or height // 3 < rep_y:
            # If we cannot fit enough 3 bitmaps into either direction,
            # then we just fill a semi transparent rectangle
            logging.debug("simple fill")
            r, g, b, _ = self.colour
            ctx.set_source_rgba(r, g, b, 0.5)
            ctx.rectangle(
                start_x, start_y,
                int(end_x - start_x), int(end_y - start_y))
            ctx.fill()
            ctx.stroke()
        else:
            # This cairo-way would work, but it's a little slow
            #             r, g, b, _ = self.colour
            #             ctx.set_source_rgba(r, g, b, 0.9)
            #             ctx.set_line_width(1)
            #
            #             # The number of repetitions that fits into the buffer clipped
            #             # selection
            #             buf_rep_x = int((end_x - start_x) / step_x)
            #             buf_rep_y = int((end_y - start_y) / step_y)
            #             buf_shift_x = (b_pos[0] - start_x) % step_x + step_x / 2  # - 3 / 2
            #             buf_shift_y = (b_pos[1] - start_y) % step_y + step_y / 2  # - 3 / 2
            #
            #             for i in range(buf_rep_x):
            #                 for j in range(buf_rep_y):
            #                     ctx.arc(start_x + buf_shift_x + i * step_x,
            #                             start_y + buf_shift_y + j * step_y,
            #                             2, 0, 2 * math.pi)
            #                     ctx.stroke()

            # check whether the cache is still valid
            cl_pos = (start_x, start_y, end_x, end_y)
            if not self._bmp or self._bmp_bpos != cl_pos:
                # Cache the image as it's quite a lot of computations
                half_step_x = step_x / 2
                half_step_y = step_y / 2

                # The number of repetitions that fits into the buffer
                # clipped selection
                buf_rep_x = int((end_x - start_x) / step_x)
                buf_rep_y = int((end_y - start_y) / step_y)

                logging.debug("Rendering %sx%s points", buf_rep_x, buf_rep_y)

                point = guiimg.getBitmap("dot.png")
                point_dc = wx.MemoryDC()
                point_dc.SelectObject(point)
                point.SetMaskColour(wx.BLACK)

                horz_dc = wx.MemoryDC()
                horz_bmp = wx.Bitmap(int(end_x - start_x), 3)
                horz_dc.SelectObject(horz_bmp)
                horz_dc.SetBackground(wx.BLACK_BRUSH)
                horz_dc.Clear()

                blit = horz_dc.Blit
                for i in range(buf_rep_x):
                    x = int(i * step_x + half_step_x)
                    blit(x, 0, 3, 3, point_dc, 0, 0)

                total_dc = wx.MemoryDC()
                self._bmp = wx.Bitmap(int(end_x - start_x), int(end_y - start_y))
                total_dc.SelectObject(self._bmp)
                total_dc.SetBackground(wx.BLACK_BRUSH)
                total_dc.Clear()

                blit = total_dc.Blit
                for j in range(buf_rep_y):
                    y = int(j * step_y + half_step_y)
                    blit(0, y, int(end_x - start_x), 3, horz_dc, 0, 0)

                self._bmp.SetMaskColour(wx.BLACK)
                self._bmp_bpos = cl_pos

            self.cnvs.dc_buffer.DrawBitmap(self._bmp,
                int(start_x + (b_pos[0] - start_x) % step_x),
                int(start_y + (b_pos[1] - start_y) % step_y),
                useMask=True
            )

    def _draw_grid(self, ctx):
        # Calculate the offset of the center of the buffer relative to the
        # top left op the buffer
        offset = self.cnvs.get_half_buffer_size()

        # The start and end position, in buffer coordinates. The return
        # values may extend beyond the actual buffer when zoomed in.
        b_pos = (self.cnvs.phys_to_buffer(self.p_start_pos, offset) +
                 self.cnvs.phys_to_buffer(self.p_end_pos, offset))
        b_pos = self._normalize_rect(b_pos)
        # logging.debug("start and end buffer pos: %s", b_pos)

        # Calculate the width and height in buffer pixels. Again, this may
        # be wider and higher than the actual buffer.
        width = b_pos[2] - b_pos[0]
        height = b_pos[3] - b_pos[1]

        # logging.debug("width and height: %s %s", width, height)

        # Clip the start and end positions using the actual buffer size
        start_x, start_y = self.cnvs.clip_to_buffer(b_pos[:2])
        end_x, end_y = self.cnvs.clip_to_buffer(b_pos[2:4])

        # logging.debug("clipped start and end: %s", (start_x, start_y, end_x, end_y))

        rep_x, rep_y = self.repetition

        # The step size in pixels
        step_x = width / rep_x
        step_y = height / rep_y

        r, g, b, _ = self.colour

        # If there are more repetitions in either direction than there
        # are pixels, just fill a semi transparent rectangle
        if width < rep_x or height < rep_y:
            ctx.set_source_rgba(r, g, b, 0.5)
            ctx.rectangle(
                start_x, start_y,
                int(end_x - start_x), int(end_y - start_y))
            ctx.fill()
        else:
            ctx.set_source_rgba(r, g, b, 0.9)
            ctx.set_line_width(1)
            # ctx.set_antialias(cairo.ANTIALIAS_DEFAULT)

            # The number of repetitions that fits into the buffer clipped
            # selection
            buf_rep_x = int((end_x - start_x) / step_x)
            buf_rep_y = int((end_y - start_y) / step_y)
            buf_shift_x = (b_pos[0] - start_x) % step_x
            buf_shift_y = (b_pos[1] - start_y) % step_y

            for i in range(1, buf_rep_x):
                ctx.move_to(start_x + buf_shift_x + i * step_x, start_y)
                ctx.line_to(start_x + buf_shift_x + i * step_x, end_y)

            for i in range(1, buf_rep_y):
                ctx.move_to(start_x, start_y - buf_shift_y + i * step_y)
                ctx.line_to(end_x, start_y - buf_shift_y + i * step_y)

            ctx.stroke()

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """ Draw the selection as a rectangle and the repetition inside of that """

        mode_cache = self.selection_mode

        if self.p_start_pos and self.p_end_pos and 0 not in self.repetition:
            if self.fill == self.FILL_POINT:
                self._draw_points(ctx)
                self.selection_mode = SEL_MODE_EDIT
            elif self.fill == self.FILL_GRID:
                self._draw_grid(ctx)
                self.selection_mode = SEL_MODE_EDIT

        WorldSelectOverlay.draw(self, ctx, shift, scale)
        self.selection_mode = mode_cache
