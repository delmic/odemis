#-*- coding: utf-8 -*-
"""
:author:    Rinze de Laat
:copyright: © 2014 Rinze de Laat, Delmic

.. license::

    This file is part of Odemis.

    Odemis is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License version 2 as published by the Free
    Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
    FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
    details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""

from odemis.gui import BG_COLOUR_NOTIFY
from odemis.gui.util import call_after
import wx


class Message(wx.PopupTransientWindow):
    """ Display short messages and warning to the user """

    def __init__(self, parent, style=wx.SIMPLE_BORDER):
        wx.PopupTransientWindow.__init__(self, parent, style)

        self.message = ""

        self.panel = wx.Panel(self)
        self.panel.SetBackgroundColour(BG_COLOUR_NOTIFY)

        self.title_txt = wx.StaticText(self.panel, -1,)
        font = wx.Font(16, wx.FONTFAMILY_DEFAULT,
                           wx.FONTSTYLE_NORMAL,
                           wx.FONTWEIGHT_BOLD)
        self.title_txt.SetFont(font)

        self.message_txt = wx.StaticText(self.panel, -1,)
        font = wx.Font(8, wx.FONTFAMILY_DEFAULT,
                           wx.FONTSTYLE_NORMAL,
                           wx.FONTWEIGHT_NORMAL)
        self.message_txt.SetFont(font)

        self.sizer = wx.BoxSizer(wx.VERTICAL)
        self.sizer.Add(self.title_txt, 0, wx.ALL, 16)
        self.sizer.Add(self.message_txt, 0, wx.RIGHT|wx.BOTTOM|wx.LEFT, 16)
        self.panel.SetSizer(self.sizer)

    @classmethod
    def show_message(cls, parent, title,
                     message=None, timeout=1.5, bgcolour=BG_COLOUR_NOTIFY):
        mo = Message(parent)
        mo._show_message(title, message, timeout, bgcolour)

    @call_after
    def _show_message(self, title, message, timeout, bgcolour):

        self.panel.SetBackgroundColour(bgcolour)

        self.title_txt.SetLabel(title)

        if message:
            self.message_txt.SetLabel(message)
            self.message_txt.Show()
        else:
            self.message_txt.Hide()

        self.sizer.Fit(self.panel)
        self.sizer.Fit(self)
        self.Layout()

        pw, ph = self.Parent.GetSize()
        mw, mh = self.GetSize()
        pos = (pw / 2) - (mw / 2), (ph / 2) - (mh / 2)

        self.Position(pos, (0, 0))

        self.Popup()
        wx.FutureCall(timeout * 1000, self.Dismiss)
