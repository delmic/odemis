#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 31 jan 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Delmic Acquisition Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''

import os
import wx
from dblmscopecanvas import DblMicroscopeCanvas

OFFICIAL_NAME="Delmic Acquisition"

class DAGuiFrame(wx.Frame):
    """
    Main window for DAGui.
    """
    def __init__(self):
        wx.Frame.__init__(self, None, size=(1024,768), title=OFFICIAL_NAME) # TODO almost fullscreen 
        self.Bind(wx.EVT_CLOSE, self.OnClose)
        
        # Statusbar
        self.CreateStatusBar() # XXX needed?
        
        # Setting up the menu.
        menuBar = wx.MenuBar()
        
        filemenu = wx.Menu()
        menuOpen = filemenu.Append(wx.ID_OPEN, "&Open...", "Select an image to display")
        self.Bind(wx.EVT_MENU, self.OnOpen, menuOpen)
        filemenu.AppendSeparator()
        menuExit = filemenu.Append(wx.ID_EXIT, "E&xit", "Terminate the program")
        self.Bind(wx.EVT_MENU, self.OnClose, menuExit)
        menuBar.Append(filemenu,"&File")
        
        viewmenu = wx.Menu()
        # Keep a ref to be able to modify it when crosshair is toggled
        self.menuCross = viewmenu.Append(wx.ID_ANY, "&Crosshair", "Display a cross on the center of the view",
                                    kind=wx.ITEM_CHECK)
        self.Bind(wx.EVT_MENU, self.ToggleCross, self.menuCross)
        menuBar.Append(viewmenu, "&View")
        
        helpmenu = wx.Menu()
        menuAbout = helpmenu.Append(wx.ID_ABOUT, "&About", "Information about this program")
        self.Bind(wx.EVT_MENU, self.OnAbout, menuAbout)
        menuBar.Append(helpmenu, "&Help")
        
        self.SetMenuBar(menuBar)
        
        
        # Last directory visited (for file open)
        self.dirname = ""
        self.filename = ""
        
        # TODO add legend, toolbar, option pane
        self.content = DblMicroscopeCanvas(self)
        self.content.SetCrossHair(True)
        self.menuCross.Check(True)
#        print self.content.HasCrossHair()
        
        # Finish by displaying the window
        self.Show(True)

    def OnAbout(self, e):
        dlg = wx.MessageDialog(self, "Delmic Acquisition Software for managing microscope.",
                               "About " + OFFICIAL_NAME, wx.OK)
        dlg.ShowModal() # blocking
        dlg.Destroy()

    def OnClose(self, e):
        self.Destroy()
        
    def OnOpen(self, e):
        """ Open a file"""
        dlg = wx.FileDialog(self, "Choose a file", self.dirname, "", "*.tiff", wx.OPEN)
        if dlg.ShowModal() == wx.ID_OK:
            self.filename = dlg.GetFilename()
            self.dirname = dlg.GetDirectory()
            f = open(os.path.join(self.dirname, self.filename), 'r')
            self.control.SetValue(f.read())
            f.close()
        dlg.Destroy()
        
    def ToggleCross(self, e):
        self.content.SetCrossHair(e.IsChecked())

if __name__ == '__main__':
    app = wx.App(redirect=False) # Errors go to the console
    app.SetAppName(OFFICIAL_NAME)
    frame = DAGuiFrame()
    app.MainLoop()

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: