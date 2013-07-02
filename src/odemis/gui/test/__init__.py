# -*- coding: utf-8 -*-
"""
Created on 1 Jul 2013

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

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
# Common configuration and code for the GUI test cases
import wx

# If manual is set to True, the window of a test will be kept open at the end
MANUAL = False

SLEEP_TIME = 100 # ms: time to sleep between actions (to slow down the tests)



def gui_loop():
    """
    Execute the main loop for the GUI until all the current events are processed
    """
    app = wx.GetApp()
    if app is None:
        return

    while True:
        wx.CallAfter(app.ExitMainLoop)
        app.MainLoop()
        if not app.Pending():
            break
