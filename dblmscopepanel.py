#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 8 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Delmic Acquisition Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''

import wx
from dblmscopecanvas import DblMicroscopeCanvas

CROSSHAIR_PEN = wx.GREEN_PEN
CROSSHAIR_SIZE = 16
class DblMicroscopePanel(wx.Panel):
    """
    A draggable, flicker-free window class adapted to show pictures of two
    microscope simultaneously.

    """
    def __init__(self, *args, **kwargs):
        wx.Panel.__init__(self, *args, **kwargs)
        
        self.canvas = DblMicroscopeCanvas(self)
        self.canvas.SetCrossHair(True)

        # TODO create a little control that display the scale
        self.scaleDisplay = wx.StaticText(self, label="Area for the scale 5µm")
        self.hfwDisplay = wx.StaticText(self, label="HFW: 156µm")
        
        text1 = wx.StaticText(self, label="SE Detector")
        line =  wx.StaticLine(self, style=wx.LI_VERTICAL)
        text2 = wx.StaticText(self, label="Optical")
        
        self.mergeSlider = wx.Slider(self, wx.ID_ANY, 50, 0, 100, size=(100, 30), style=wx.SL_HORIZONTAL | wx.SL_AUTOTICKS | wx.SL_TICKS)
        self.mergeSlider.SetTick(50) # Only on Windows
#        wx.EVT_SLIDER(self.mergeSlider, self.OnSlider)
        self.mergeSlider.Bind(wx.EVT_SLIDER, self.OnSlider)

        
        # TODO: make the default size bigger
        # TODO: focus by default on the content, for keyboard
        mainSizer = wx.BoxSizer(wx.VERTICAL)
        legendSizer = wx.BoxSizer(wx.HORIZONTAL)
        scaleSizer = wx.BoxSizer(wx.VERTICAL)
        scaleSizer.Add(self.scaleDisplay, 0, wx.ALIGN_LEFT|wx.EXPAND)
        scaleSizer.Add(self.hfwDisplay, 0, wx.ALIGN_RIGHT|wx.EXPAND)
        
        imageSizer = wx.BoxSizer(wx.VERTICAL)
        imageSizerTop = wx.BoxSizer(wx.HORIZONTAL)
        imageSizerBottom = wx.BoxSizer(wx.HORIZONTAL)
        imageSizer.Add(imageSizerTop, 0, wx.ALIGN_CENTER)
        imageSizer.Add(imageSizerBottom, 0, wx.ALIGN_CENTER)
        
        imageSizerTop.Add(text1, 1, wx.ADJUST_MINSIZE|wx.LEFT|wx.RIGHT|wx.ALIGN_LEFT|wx.EXPAND, 5)
        imageSizerTop.Add(self.mergeSlider, 0, wx.ALIGN_CENTER)
#        legendSizer.AddStretchSpacer()
        imageSizerTop.Add(text2, 1, wx.ALIGN_RIGHT|wx.EXPAND|wx.ALIGN_CENTER_HORIZONTAL)
        
        legendSizer.Add(scaleSizer, 1, wx.ALIGN_CENTER|wx.EXPAND)
        legendSizer.Add(line, 0, wx.ALIGN_CENTER|wx.EXPAND)
        legendSizer.Add(imageSizer, 3, wx.ALIGN_CENTER|wx.EXPAND)
        mainSizer.Add(self.canvas, 10, wx.EXPAND)
        mainSizer.Add(legendSizer, 0, wx.EXPAND) # 0 = fixed size
        

        
        self.SetSizer(mainSizer)
        self.SetAutoLayout(True)
        mainSizer.Fit(self)
  
    def OnSlider(self, e):
        print self.mergeSlider.GetValue()
        self.canvas.SetMergeRatio(self.mergeSlider.GetValue()/100.0)
            
    # Change picture one/two        
    def SetImage(self, index, im, pos = None, mpp = None):
        self.canvas.SetImage(index, im, pos, mpp)
        
# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: