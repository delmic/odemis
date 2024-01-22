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

import logging
import wx

import odemis.gui.comp.overlay.base as base
import odemis.model as model


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
