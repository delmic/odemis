# -*- coding: utf-8 -*-
"""
:author: Rinze de Laat <laat@delmic.com>
:copyright: © 2012-2021 Rinze de Laat, Philip Winkler, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
    General Public License version 2 as published by the Free Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without
    even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
    General Public License for more details.

    You should have received a copy of the GNU General Public License along with Odemis. If not,
    see http://www.gnu.org/licenses/.

This module contains classes needed to construct stream panels.

Stream panels are custom, specialized controls that allow the user to view and manipulate various
data streams coming from the microscope.

"""

import wx.lib.newevent

# Used by StreamPanel and StreamBar
stream_remove_event, EVT_STREAM_REMOVE = wx.lib.newevent.NewEvent()

# Used by FastEMProjectPanelHeader and StreamPanelHeader
CAPTION_PADDING_RIGHT = 5
ICON_WIDTH, ICON_HEIGHT = 16, 16
