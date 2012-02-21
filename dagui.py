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

from dblmscopecanvas import DblMicroscopeCanvas
from dblmscopepanel import DblMicroscopePanel
from instrmodel import SECOMModel, InstrumentalImage
import os
import wx

OFFICIAL_NAME = "Delmic Acquisition"

class DAGuiFrame(wx.Frame):
    """
    Main window for DAGui.
    """
    def __init__(self):
        wx.Frame.__init__(self, None, size=(1024,768), title=OFFICIAL_NAME) # TODO almost fullscreen 
        self.Bind(wx.EVT_CLOSE, self.OnClose)
        
        # Statusbar
        #self.CreateStatusBar() # XXX needed?
        self.secom_model = SECOMModel()
        
        # Setting up the menu.
        menuBar = wx.MenuBar()
        
        filemenu = wx.Menu()
        menuOpen = filemenu.Append(wx.ID_OPEN, "&Open...", "Select an image to display")
        self.Bind(wx.EVT_MENU, self.OnOpen, menuOpen)
        menuLdEx1 = filemenu.Append(wx.ID_ANY, "&Load example 1\tCtrl+L", "Loads the first example of pictures")
        self.Bind(wx.EVT_MENU, self.OnLoadExample1, menuLdEx1)
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
        
        # TODO add toolbar, option pane 
        # The main frame
        self.panel = DblMicroscopePanel(self, wx.ID_ANY)
        self.viewmodel = self.panel.viewmodel
        self.menuCross.Check(self.viewmodel.crosshair.value) # TODO ensure sync
        
        # Finish by displaying the window
        self.Show(True)

    def OnAbout(self, e):
        message = ("Delmic Acquisition Software for managing microscope.\n" +
                   "Copyright © 2012 Delmic B.V.\n" + 
                   "Licensed under the GNU General Public License version 2")
        dlg = wx.MessageDialog(self, message,
                               "About " + OFFICIAL_NAME, wx.OK)
        dlg.ShowModal() # blocking
        dlg.Destroy()

    def OnClose(self, e):
        self.Destroy()
        
    def OnOpen(self, e):
        """ Open a file"""
        dlg = wx.FileDialog(self, "Choose one or two pictures", self.dirname, "",
                            "Image file (.tif, .jpg, .png)|*.tif;*.tiff;*.png;*.jpg;*.jpeg|Any file (*.*)|*.*",
                            wx.OPEN | wx.MULTIPLE)
        filenames = []
        if dlg.ShowModal() == wx.ID_OK:
            filenames = dlg.GetFilenames()
            self.dirname = dlg.GetDirectory()
        dlg.Destroy()
        
        for i, f in enumerate(filenames):
            try:
                fullname = os.path.join(self.dirname, f)
                im = InstrumentalImage(wx.Image(fullname),  0.0001 + (0.000015 *i), (0.00001,0.00001))

                if i == 0:
                    self.secom_model.sem_det_image.value = im
                elif i == 1:
                    self.secom_model.optical_det_image.value = im
            except e:
                print e

    def OnLoadExample1(self, e):
        """ Open the two files for example """
        try:
            name1 = "1-optical-rot7.png"
            im1 = InstrumentalImage(wx.Image(name1), 7.14286e-7, (0.0,0.0))
            self.secom_model.optical_det_image.value = im1
            
            name2 = "1-sem-bse.png"
            im2 = InstrumentalImage(wx.Image(name2), 4.54545e-7, (2e-6, -1e-5))
            self.secom_model.sem_det_image.value = im2
        except e:
            print e

    def ToggleCross(self, e):
        """
        Callback for view/crosshair menu
        """
        # TODO update when viewmodel changes
        self.viewmodel.crosshair.value = e.IsChecked()

if __name__ == '__main__':
    app = wx.App(redirect=False) # Errors go to the console
    app.SetAppName(OFFICIAL_NAME)
    frame = DAGuiFrame()
    app.MainLoop()

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: