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

from odemis.gui.comp.overlay.base import WorldOverlay


class StagePointSelectOverlay(WorldOverlay):
    """ Overlay for moving the stage (in physical coordinates) upon the selection of canvas points"""

    def on_dbl_click(self, evt):
        if self.active:
            v_pos = evt.Position
            p_pos = self.cnvs.view_to_phys(v_pos, self.cnvs.get_half_buffer_size())
            # directly move the stage to the selected physical position
            self.cnvs.view.moveStageTo(p_pos)
        else:
            WorldOverlay.on_dbl_click(self, evt)

    def on_left_down(self, evt):
        if self.active:
            # let the canvas handle dragging
            self.cnvs.on_left_down(evt)
        else:
            WorldOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        if self.active:
            # let the canvas handle dragging
            self.cnvs.on_left_up(evt)
        else:
            WorldOverlay.on_left_up(self, evt)

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        pass
