# -*- coding: utf-8 -*-

"""

:author: Nandish Patel
:copyright: © 2024 Nandish Patel, Delmic

.. license::
    This file is part of Odemis.

    Odemis is free software: you can redistribute it and/or modify it under the terms
    of the GNU General Public License version 2 as published by the Free Software
    Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
    without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
    PURPOSE. See the GNU General Public License for more details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""

# This module contains various custom spinner classes used throughout the odemis
# project.
#
# All these classes are supported within XRCED as long as the xmlh/delmic.py
# and xmlh/xh_delmic.py modules are available (e.g. through a symbolic link)
# in XRCED's plugin directory.

import wx

from odemis.gui import FG_COLOUR_EDIT


class UnitIntegerSpinner(wx.SpinCtrl):
    def __init__(
        self,
        parent,
        id=wx.ID_ANY,
        value=wx.EmptyString,
        size=wx.DefaultSize,
        pos=wx.DefaultPosition,
        **kwargs
    ):
        min_val = float(kwargs.pop("min", 0.0))
        max_val = float(kwargs.pop("max", 100.0))
        initial_val = float(kwargs.pop("initial", 0.0))
        style = float(kwargs.pop("style", wx.SP_ARROW_KEYS))
        super(UnitIntegerSpinner, self).__init__(
            parent,
            id=id,
            value=value,
            pos=pos,
            size=size,
            min=min_val,
            max=max_val,
            initial=initial_val,
            styple=style,
        )
        self.SetForegroundColour(FG_COLOUR_EDIT)
        self.SetBackgroundColour(self.Parent.BackgroundColour)
        self.current_value = initial_val
        self.Bind(wx.EVT_SPINCTRL, self.on_spin)

    def on_spin(self, evt):
        self.current_value = int(self.GetValue())


class UnitFloatSpinner(wx.SpinCtrlDouble):
    def __init__(
        self,
        parent,
        id=wx.ID_ANY,
        value=wx.EmptyString,
        size=wx.DefaultSize,
        pos=wx.DefaultPosition,
        **kwargs
    ):
        min_val = float(kwargs.pop("min", 0.0))
        max_val = float(kwargs.pop("max", 100.0))
        initial_val = float(kwargs.pop("initial", 0.0))
        increment = float(kwargs.pop("inc", 0.1))
        style = float(kwargs.pop("style", wx.SP_ARROW_KEYS))
        super(UnitFloatSpinner, self).__init__(
            parent,
            id=id,
            value=value,
            pos=pos,
            size=size,
            min=min_val,
            max=max_val,
            initial=initial_val,
            inc=increment,
            style=style,
        )
        self.SetForegroundColour(FG_COLOUR_EDIT)
        self.SetBackgroundColour(self.Parent.BackgroundColour)
        self.current_value = initial_val
        self.Bind(wx.EVT_SPINCTRLDOUBLE, self.on_spin_double)

    def on_spin_double(self, evt):
        self.current_value = float(self.GetValue())
