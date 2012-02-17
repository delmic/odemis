#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 6 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Delmic Acquisition Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''

from dblmscopeviewmodel import DblMscopeViewModel
from draggablecanvas import DraggableCanvas, WorldToBufferPoint
import math
import wx

CROSSHAIR_COLOR = wx.GREEN
CROSSHAIR_SIZE = 16
class DblMicroscopeCanvas(DraggableCanvas):
    """
    A draggable, flicker-free window class adapted to show pictures of two
    microscope simultaneously.
    
    It knows size and position of what is represented in a picture and display
    the pictures accordingly.
    
    It also provides various typical overlays (ie, drawings) for microscope views.
    """
    def __init__(self, *args, **kwargs):
        DraggableCanvas.__init__(self, *args, **kwargs)
        
        parent = args[0]
        self.viewmodel = parent.viewmodel
        # meter per "world unit"
        # for conversion between "world pos" in the canvas and a real unit
        # mpp == mpwu => 1 world coord == 1 px => scale == 1
        self.mpwu = self.viewmodel.mpp.value  #m/wu
        # Should not be changed!
        
        self.viewmodel.mpp.bind(self.avOnMPP)
        self.viewmodel.images[0].bind(self.avOnImage)
        self.viewmodel.images[1].bind(self.avOnImage, True)
        self.viewmodel.merge_ratio.bind(self.avOnMergeRatio, True)
        
        self.Bind(wx.EVT_MOUSEWHEEL, self.OnWheel)
        
        self.Overlays.append(CrossHairOverlay("Blue", CROSSHAIR_SIZE)) # debug
        self.Overlays.append(CrossHairOverlay("Red", CROSSHAIR_SIZE, (10,10))) # debug
    # Add/remove crosshair
    def SetCrossHair(self, activated):
        """
        Activate or disable the display of a cross in the middle of the view
        activated = true if the cross should be displayed
        """ 
        # We don't specifically know about the crosshair, so look for it in the static overlays
        ch = None
        for o in self.StaticOverlays:
            if isinstance(o, CrossHairOverlay):
                ch = o
                break
        
        if activated:
            if not ch:
                ch = CrossHairOverlay(CROSSHAIR_COLOR, CROSSHAIR_SIZE)
                self.StaticOverlays.append(ch)
                self.Refresh(False)
        else:
            if ch:
                self.StaticOverlays.remove(ch)
                self.Refresh(False)
                
    def HasCrossHair(self):
        """
        returns true if the cross is activated, false otherwise
        """
        for o in self.StaticOverlays:
            if isinstance(o, CrossHairOverlay):
                return True
        return False
    
    # Add/remove overlays

#    # Change hfw
#    def SetHFW(self, hfw):
#        """
#        Set the horizontal field width of the image
#        hfw (0.0<float): the width
#        """
#        assert(0.0 < hfw)
#        view_width = self.ClientSize[0]
#        if view_width < 1:
#            view_width = 1
#        self.mpp.value = float(hfw) / view_width
#        
#    def GetHFW(self):
#        return self.mpp.value * self.ClientSize[0]
     

    def avOnMergeRatio(self, val):
        self.merge_ratio = val
        self.ShouldUpdateDrawing()
     
    def Zoom(self, inc):
        """
        Zoom by the given factor
        inc (float): scale the current view by 2^inc
        ex:  # 1 => *2 ; -1 => /2; 2 => *4...
        """
        scale = math.pow(2.0, inc)
        self.viewmodel.mpp.value /= scale

    def avOnMPP(self, mpp):
        self.scale = self.mpwu / mpp
        
#        # update the scaling of the images
#        for i in range(len(self.Images)):
#            if self.Images[i]:
#                impp = self.viewmodel.images[i].value.mpp
#                self.Images[i]._dc_scale = impp / mpp
                
        self.ShouldUpdateDrawing()
    
    def avOnImage(self, image):
        for i in range(len(self.Images)):
            iim = self.viewmodel.images[i].value
            if iim.image:
                scale = float(iim.mpp) / self.mpwu
                pos = (iim.center[0] * self.mpwu, iim.center[1] * self.mpwu)
                self.SetImage(i, iim.image, pos, scale)
            else:
                self.SetImage(i, None)

    # Zoom/merge management
    def OnWheel(self, event):
        change = event.GetWheelRotation() / event.GetWheelDelta()
        if event.ShiftDown():
            change *= 0.2 # softer
        
        if event.CmdDown(): # = Ctrl on Linux/Win or Cmd on Mac
            self.viewmodel.merge_ratio.value += change * 0.1
        else:
            self.Zoom(change)

### Here come all the classes for drawing overlays
class CrossHairOverlay(object):
    def __init__(self, color=CROSSHAIR_COLOR, size=CROSSHAIR_SIZE, center=(0,0)):
        self.pen = wx.Pen(color)
        self.size = size
        self.center = center
        
    def Draw(self, dc, shift=(0,0), scale=1.0):
        """
        Draws the crosshair
        dc (wx.DC)
        shift (2-tuple float): shift for the coordinate conversion
        scale (float): scale for the coordinate conversion
        """
        dc.SetPen(self.pen)
        
        tl = (self.center[0] - self.size,
              self.center[1] - self.size)
        br = (self.center[0] + self.size,
              self.center[1] + self.size)
        tl_s = WorldToBufferPoint(tl, shift, scale)
        br_s = WorldToBufferPoint(br, shift, scale)
        center = WorldToBufferPoint(self.center, shift, scale)

        dc.DrawLine(tl_s[0], center[1], br_s[0], center[1])
        dc.DrawLine(center[0], tl_s[1], center[0], br_s[1]) 
        

        
# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: