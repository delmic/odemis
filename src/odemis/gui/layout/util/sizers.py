# -*- coding: utf-8 -*-
"""
Created on 16 Sep 2026

@author: Tim Moerkerken

Copyright © 2026 Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms  of the GNU General Public License version 2 as published by the Free
Software  Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY;  without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR  PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

import wx
from contextlib import contextmanager
from typing import Generator


@contextmanager
def vbox() -> Generator[wx.BoxSizer, None, None]:
    """Yield a vertical BoxSizer for use as a visual grouping scope."""
    yield wx.BoxSizer(wx.VERTICAL)


@contextmanager
def hbox() -> Generator[wx.BoxSizer, None, None]:
    """Yield a horizontal BoxSizer for use as a visual grouping scope."""
    yield wx.BoxSizer(wx.HORIZONTAL)
