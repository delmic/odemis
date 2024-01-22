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

import wx
from odemis.gui.comp.overlay.base import (DragMixin, SpotModeBase, ViewOverlay,
                                          WorldOverlay)
from odemis.util.comp import compute_scanner_fov, get_fov_rect


class SpotModeWorldOverlay(WorldOverlay, DragMixin, SpotModeBase):
    """ Render the spot mode indicator in the center of the view

    If a position is provided, the spot will be drawn there.

    If the overlay is activated, the user can use the mouse cursor to select a position

    """

    def __init__(self, cnvs, spot_va=None, scanner=None):
        """
        scanner (None or HwComponent): The scanner component to which the relative
          spot position values refers to. If provided, the spot will be clipped
          to its FoV.
        """

        WorldOverlay.__init__(self, cnvs)
        DragMixin.__init__(self)
        SpotModeBase.__init__(self, cnvs, spot_va=spot_va)

        self.p_pos = None
        self._scanner = scanner  # component used to position the spot physically

    def on_spot_change(self, _):
        self._ratio_to_phys()
        self.cnvs.update_drawing()

    def on_size(self, evt):
        self._ratio_to_phys()
        WorldOverlay.on_size(self, evt)

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

    def convert_spot_ratio_to_phys(self, r_spot):
        """
        Convert the spot position represented as a ration into a physical position
        r_spot (2 floats or None): The spot position as a ratio
        returns (2 floats or None): spot in physical coordinates (m)
        """
        if r_spot in (None, (None, None)):
            return None

        # convert relative position to physical position
        try:
            sem_rect = self._get_scanner_rect()
        except ValueError:
            logging.warning("Trying to convert a scanner ROI, but no scanner set")
            return None

        # In physical coordinates Y goes up, but in ROI, Y goes down => "1-"
        phys_pos = (
            sem_rect[0] + r_spot[0] * (sem_rect[2] - sem_rect[0]),
            sem_rect[1] + (1 - r_spot[1]) * (sem_rect[3] - sem_rect[1])
        )

        return phys_pos

    def convert_spot_phys_to_ratio(self, p_spot):
        """
        Clip the physical spot to the SEM FoV and convert it into a ratio
        p_spot (2 floats): spot in physical coordinates (m)
        returns:
            p_spot (2 floats): The clipped physical spot
            r_spot (2 floats): The spot position as a ratio
        """
        # Position of the complete SEM scan in physical coordinates
        l, t, r, b = self._get_scanner_rect()

        # Take only the intersection so that that ROA is always inside the SEM scan
        p_spot = min(max(l, p_spot[0]), r), min(max(t, p_spot[1]), b)

        # Convert the ROI into relative value compared to the SEM scan
        # In physical coordinates Y goes up, but in ROI, Y goes down => "1-"
        r_spot = (
            (p_spot[0] - l) / (r - l),
            1 - (p_spot[1] - t) / (b - t)
        )

        return p_spot, r_spot

    def _phys_to_ratio(self):
        if self.p_pos is None:
            self.r_pos.value = (0.5, 0.5)
        else:
            # Since converting to a ratio possibly involves clipping, the p_pos is also updated
            p_pos, self.r_pos.value = self.convert_spot_phys_to_ratio(self.p_pos)
            self.p_pos = p_pos

    def _ratio_to_phys(self):
        try:
            self.p_pos = self.convert_spot_ratio_to_phys(self.r_pos.value)
        except (TypeError, KeyError):
            self.p_pos = None

    def draw(self, ctx, shift=(0, 0), scale=1.0):

        if self.p_pos is None:
            return

        bx, by = self.cnvs.phys_to_buffer(self.p_pos)
        ctx.translate(*self.offset_b)

        SpotModeBase.draw(self, ctx, bx, by)

    def on_left_down(self, evt):
        if self.active.value:
            DragMixin._on_left_down(self, evt)
        else:
            WorldOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        if self.active.value:
            DragMixin._on_left_up(self, evt)
            self.p_pos = self.cnvs.view_to_phys(evt.Position, self.offset_b)
            self._phys_to_ratio()
            self.cnvs.update_drawing()
        else:
            WorldOverlay.on_left_up(self, evt)

    def on_motion(self, evt):
        if self.active.value and self.left_dragging:
            self.p_pos = self.cnvs.view_to_phys(evt.Position, self.offset_b)
            self._phys_to_ratio()
            self.cnvs.update_drawing()
        else:
            WorldOverlay.on_left_up(self, evt)

    def on_enter(self, evt):
        if self.active.value:
            self.cnvs.set_default_cursor(wx.CROSS_CURSOR)
        else:
            WorldOverlay.on_enter(self, evt)

    def on_leave(self, evt):
        if self.active.value:
            self.cnvs.reset_default_cursor()
        else:
            WorldOverlay.on_leave(self, evt)

    def _activate(self):
        # callback for .active VA
        self._ratio_to_phys()
        WorldOverlay._activate(self)

    def _deactivate(self):
        # callback for .active VA
        self.p_pos = None
        WorldOverlay._deactivate(self)


class SpotModeViewOverlay(ViewOverlay, DragMixin, SpotModeBase):
    """ Render the spot mode indicator in the center of the view

    If a position is provided, the spot will be drawn there.

    If the overlay is activated, the user can use the mouse cursor to select a position


    """
    def __init__(self, cnvs, spot_va=None):
        ViewOverlay.__init__(self, cnvs)
        DragMixin.__init__(self)
        SpotModeBase.__init__(self, cnvs, spot_va=spot_va)

        self.v_pos = None

    def on_spot_change(self, _):
        self._r_to_v()

    def on_size(self, evt):
        self._r_to_v()
        ViewOverlay.on_size(self, evt)

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
        SpotModeBase.draw(self, ctx, vx, vy)

    def _activate(self):
        self._r_to_v()
        ViewOverlay._activate(self)

    def _deactivate(self):
        self.v_pos = None
        ViewOverlay._deactivate(self)
