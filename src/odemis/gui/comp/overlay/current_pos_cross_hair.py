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

import odemis.gui as gui
import odemis.util.conversion as conversion
from odemis.gui.comp.overlay.base import WorldOverlay
from odemis.gui.comp.overlay.view import CenteredLineOverlay
from odemis.gui.util import call_in_wx_main


class CurrentPosCrossHairOverlay(WorldOverlay):
    """ Render a static cross hair to the current position of the stage"""

    def __init__(self, cnvs, colour=gui.CROSSHAIR_COLOR, size=gui.CROSSHAIR_SIZE, thickness=gui.CROSSHAIR_THICKNESS):
        WorldOverlay.__init__(self, cnvs)

        if not hasattr(cnvs.view, "stage_pos"):
            raise ValueError("CurrentPosCrossHairOverlay requires stage_pos VA on the view to function properly.")
        cnvs.view.stage_pos.subscribe(self._current_pos_updated, init=True)

        self.colour = conversion.hex_to_frgba(colour)
        self.size = size
        self.thickness = thickness

        self.crosshair = CenteredLineOverlay(self.cnvs)

    @call_in_wx_main
    def _current_pos_updated(self, _):
        """
        Called when current stage position updated
        """
        # Directly refresh the canvas (so the overlay draw is called with proper context)
        self.cnvs.update_drawing()

    def _get_current_stage_buffer_pos(self):
        """
        Get the buffer position of the current stage physical position
        :return: (float, float) buffer coordinates of current position
        """
        pos = self.cnvs.view.stage_pos.value
        half_size_offset = self.cnvs.get_half_buffer_size()
        # return physical position converted to buffer 'world' coordinates
        return self.cnvs.phys_to_buffer_pos(
            (pos['x'], pos['y']),
            self.cnvs.p_buffer_center,
            self.cnvs.scale,
            offset=half_size_offset,
        )

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """ Draw a cross hair to the Cairo context """
        center = self._get_current_stage_buffer_pos()
        self.crosshair.draw_crosshair(ctx, center, size=self.size, colour=self.colour, thickness=self.thickness)
