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
from PIL import Image

#class DAGuiApp(wx.App):
#    def __init__(self, redirect=False, filename=None):
#        wx.App.__init__(self, redirect, filename)
#        self.frame = wx.Frame(None, wx.ID_ANY, title='Delmic Acquisition')
#
#        self.panel = wx.Panel(self.frame, wx.ID_ANY)

OFFICIAL_NAME="Delmic Acquisition"

class DAGuiFrame(wx.Frame):
    """
    Main window for DAGui.
    """
    def __init__(self):
        wx.Frame.__init__(self, None, title=OFFICIAL_NAME) # TODO almost fullscreen size=(800,600) 
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
        
        helpmenu = wx.Menu()
        menuAbout = helpmenu.Append(wx.ID_ABOUT, "&About", "Information about this program")
        self.Bind(wx.EVT_MENU, self.OnAbout, menuAbout)
        menuBar.Append(helpmenu, "&Help")
        
        self.SetMenuBar(menuBar)
        
        
        # Last directory visited
        self.dirname = ""
        self.filename = ""
        
        
        
        
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

if __name__ == '__main__':
    app = wx.App(redirect=False) # Errors go to the console
    frame = DAGuiFrame()
    app.MainLoop()

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: