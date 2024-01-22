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

import odemis.gui.comp.overlay.base as base


class TextViewOverlay(base.ViewOverlay):
    """ Render the present labels to the screen """

    def __init__(self, cnvs):
        base.ViewOverlay.__init__(self, cnvs)

    def draw(self, ctx):
        if self.labels:
            self._write_labels(ctx)
