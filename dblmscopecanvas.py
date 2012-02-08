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

import wx
from draggablecanvas import DraggableCanvas

CROSSHAIR_PEN = wx.GREEN_PEN
CROSSHAIR_SIZE = 16
class DblMicroscopeCanvas(DraggableCanvas):
    """
    A draggable, flicker-free window class adapted to show pictures of two
    microscope simultaneously.
    
    It knows size and position of what is represented in a picture and display
    the pictures accordingly.
    
    It also provides various typical overlays (ie, drawings) for microscope views.
    """
    def __init__(self, parent):
        DraggableCanvas.__init__(self, parent)
        
        # meter per pixel = image density => field of view
        self.mpp = 0.0001 # 1 px = 0.1mm <~> zoom = 0
        wx.EVT_MOUSEWHEEL(self, self.OnWheel)
        
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
                ch = CrossHairOverlay(CROSSHAIR_PEN, CROSSHAIR_SIZE)
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

    # Change hfw
    def SetHFW(self, hfw):
        """
        Set the horizontal field width of the image
        hfw (0.0<float): the width
        """
        assert(0.0 < hfw)
        view_width = self.ClientSize[0]
        if view_width < 1:
            view_width = 1
        self.SetMPP(float(hfw) / view_width)
        
    def GetHFW(self):
        return self.GetMPP() * self.ClientSize[0]
     
    def SetMPP(self, mpp):
        """
        Directly set the meter per pixel value
        mpp (0.0<float): 
        See SetHFW()
        """
        assert(0.0 < mpp)
        
        self.mpp = mpp
        # update the scaling of the images
        for i in self.Images:
            if i:
                i.scale = i._dmc_mpp / mpp
        self.SetZoom(0)

    def GetMPP(self):
        return self.mpp
    
    # Change picture one/two
    def SetImage(self, index, im, pos = None, mpp = None):
        """
        Set (or update) the image
        index (int, 0 or 1): index number of the image
        im (wx.Image): the image, or None to remove the current image
        pos (2-tuple of float): position of the center of the image (in meters)
        mpp (0.0<float): meters per pixel, the size of one pixel
        """
        assert(0 <= index and index <= 1)
        
        if not im:
            self.Images[index] = None
            return
        
        im._dc_center = pos
        im._dmc_mpp = mpp # for later updates of the scale
        im._dc_scale = float(mpp) / self.mpp
        im.InitAlpha()
        self.Images[index] = im
        self.ShouldUpdateDrawing()

    # Zoom/merge management
    def OnWheel(self, event):
        change = event.GetWheelRotation() / event.GetWheelDelta()
        if event.ShiftDown():
            change *= 0.2 # softer
        
        if event.CmdDown(): # = Ctrl on Linux/Win or Cmd on Mac
            self.SetMergeRatio(self.GetMergeRatio() + change * 0.1)
        else:
            self.SetZoom(self.GetZoom() + change)


### Here come all the classes for drawing overlays
class CrossHairOverlay():
    def __init__(self, pen, size, center = (0,0)):
        self.pen = pen
        self.size = size
        self.center = center
        
    def Draw(self, dc):
        dc.SetPen(self.pen)
        # at the centre of the screen
        dc.DrawLine(self.center[0]-self.size, self.center[1],
                    self.center[0] + self.size, self.center[1])
        dc.DrawLine(self.center[0], self.center[1] - self.size,
                    self.center[0], self.center[1] + self.size) 
        
# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: