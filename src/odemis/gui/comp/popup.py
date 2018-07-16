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

from __future__ import division

from odemis.gui import BG_COLOUR_NOTIFY
from odemis.gui.util import call_in_wx_main
import wx

# TODO: use standard Ubuntu popup on Ubuntu?


@call_in_wx_main
def show_message(parent, title, message=None, timeout=3.0, bgcolour=BG_COLOUR_NOTIFY):
    """ Show a small message popup for a short time

    :param parent: (wxWindow)
    :param title: (str) The title of the message
    :param message: (str or None) Extra text that will be displayed below the title
    :param timeout: (float) Timeout in seconds after which the message will automatically vanish
    :param bgcolour: (str) The background colour of the window
    """

    mo = Message(parent, title, message, bgcolour)
    mo.Flash(timeout)
    # TODO: destroy the window after it's not used, or does wxPython do it automatically?


class Message(wx.PopupTransientWindow):
    """ Display short messages and warning to the user """

    def __init__(self, parent, title, message=None, bgcolour=BG_COLOUR_NOTIFY, style=wx.SIMPLE_BORDER):
        wx.PopupTransientWindow.__init__(self, parent, style)

        self.panel = wx.Panel(self)
        self.panel.SetBackgroundColour(bgcolour)
        self.sizer = wx.BoxSizer(wx.VERTICAL)

        self.title_txt = wx.StaticText(self.panel, -1,)
        font = wx.Font(
            16,
            wx.FONTFAMILY_DEFAULT,
            wx.FONTSTYLE_NORMAL,
            wx.FONTWEIGHT_BOLD
        )
        self.title_txt.SetFont(font)
        self.title_txt.SetLabel(title)
        self.sizer.Add(self.title_txt, 0, wx.ALL, 16)

        if message:
            self.message_txt = wx.StaticText(self.panel, -1,)
            font = wx.Font(
                8,
                wx.FONTFAMILY_DEFAULT,
                wx.FONTSTYLE_NORMAL,
                wx.FONTWEIGHT_NORMAL
            )
            self.message_txt.SetFont(font)
            self.message_txt.SetLabel(message)
            self.sizer.Add(self.message_txt, 0, wx.RIGHT | wx.BOTTOM | wx.LEFT, 16)

        self.panel.SetSizer(self.sizer)

        self.sizer.Fit(self.panel)
        self.sizer.Fit(self)
        self.Layout()

    def Flash(self, timeout):
        """
        Display the Message window for a given amount of time
        timeout (float): display duration in s
        """

        ox, oy = self.Parent.GetPosition()
        pw, ph = self.Parent.GetSize()
        mw, mh = self.GetSize()
        pos = ox + (pw - mw) // 2, oy + (ph - mh) // 2

        self.Position(pos, (0, 0))

        self.Popup()
        wx.CallLater(timeout * 1000, self.Dismiss)

        # The Yield call forces wxPython to take control and process any event (i.e. the preceding
        # Refresh call), making sure the Message is shown before the data loading begins.
        wx.Yield()
