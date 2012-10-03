#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 6 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

from .comp.canvas import DraggableCanvas, WorldToBufferPoint
from .img.data import gettest_patternImage
from odemis.gui.log import log
import threading
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
        self.view = None

        self.Bind(wx.EVT_MOUSEWHEEL, self.OnWheel)

        # TODO: If it's too resource consuming, which might want to create just our own thread
        # FIXME: "stop all axes" should also cancel the next timer
        self._moveFocusLock = threading.Lock()
        self._moveFocusDistance = [0, 0]
        # TODO deduplicate!
        self._moveFocus0Timer = wx.PyTimer(self._moveFocus0)
        self._moveFocus1Timer = wx.PyTimer(self._moveFocus1)

#        self.WorldOverlays.append(CrossHairOverlay("Blue", CROSSHAIR_SIZE, (-10,-10))) # debug
#        self.WorldOverlays.append(CrossHairOverlay("Red", CROSSHAIR_SIZE, (10,10))) # debug

        #self.SetImage(0, gettest_patternImage(), (0.0, 0.0), 0.5)
        #self.SetImage(1, gettest_patternImage(), (0.0, 0.0), 0.5)

    def setView(self, view):
        """
        Set the view that this canvas is displaying/representing
        Can be called only once, at initialisation.
        """
        # This is a kind of kludge, see mscviewport.MicroscopeViewport for details 
        assert(self.view is None)
        
        self.view = view

        # meter per "world unit"
        # for conversion between "world pos" in the canvas and a real unit
        # mpp == mpwu => 1 world coord == 1 px => scale == 1
        self.mpwu = self.view.mpp.value  #m/wu
        # Should not be changed! 
        # FIXME!!

        self.view.mpp.subscribe(self._onMPP)
        self.view.crosshair.subscribe(self._onCrossHair, init=True)
        self.view.view_pos.subscribe(self._onViewCenter, init=True)
        
        # any image changes
        view.lastUpdate.subscribe(self._onViewImageUpdate, init=True)
    
    def _onViewImageUpdate(self, t):
        self.ShouldUpdateDrawing()
        # TODO canvas should update thumbnail from time to time
        # => override 

    def get_screenshot(self):
        bitmap = wx.EmptyBitmap(*self.ClientSize)

        memory = wx.MemoryDC()
        memory.SelectObject(bitmap)
        #set pen, do drawing.
        memory.SelectObject(wx.NullBitmap)

        return wx.ImageFromBitmap(bitmap)
        
    def onViewCenter(self, value):
        """
        An external component asks us to move the view
        """
        pos = (value[0] / self.mpwu, value[1] / self.mpwu)
        self.ReCenterBuffer(pos)

    def ReCenterBuffer(self, pos):
        """
        Update the position of the buffer on the world
        pos (2-tuple float): the world coordinates of the center of the buffer
        """
        # FIXME
        DraggableCanvas.ReCenterBuffer(self, pos)

        new_pos = self.world_pos_requested
        physical_pos = (new_pos[0] * self.mpwu, new_pos[1] * self.mpwu)
        self.viewmodel.center.value = physical_pos

    def onExtraAxisMove(self, axis, shift):
        """
        called when the extra dimensions are modified (right drag)
        axis (0<int): the axis modified
            0 => X
            1 => Y
        shift (int): relative amount of pixel moved
            >0: toward up/right
        """
        focus = [self.view.focus0, self.view.focus1][axis]
        if focus is not None:
            # conversion: 1 unit => 0.1 μm (so a whole screen, ~44000u, is a couple of mm)
            # TODO this should be adjusted by the lens magnification:
            # the higher the magnification, the smaller is the change (=> proportional ?)
            # negative == go up == closer from the sample
            val = 0.1e-6 * shift # m
            assert(abs(val) < 0.01) # a move of 1 cm is a clear sign of bug

            self.queueMoveFocus(axis, val)

    def queueMoveFocus(self, axis, shift, period = 0.1):
        """
        Move the focus, but at most every period, to avoid accumulating
        many slow small moves.
        axis (0,1): axis/focus number
        shift (float): distance of the focus move
        period (second): maximum time to wait before it will be moved
        """
        # update the complete move to do
        with self._moveFocusLock:
            self._moveFocusDistance[axis] += shift

        # start the timer if not yet started
        timer = [self._moveFocus0Timer, self._moveFocus1Timer][axis]
        if not timer.IsRunning():
            timer.Start(period * 1000.0, oneShot=True)

    def _moveFocus0(self):
        with self._moveFocusLock:
            shift = self._moveFocusDistance[0]
            self._moveFocusDistance[0] = 0
        log.debug("Moving focus0 by %f μm", shift * 1e6)
        self.view.focus0.moveRel({"z": shift})

    def _moveFocus1(self):
        with self._moveFocusLock:
            shift = self._moveFocusDistance[1]
            self._moveFocusDistance[1] = 0
        log.debug("Moving focus1 by %f μm", shift * 1e6)
        self.view.focus1.moveRel({"z": shift})

    def _onCrossHair(self, activated):
        """
        Activate or disable the display of a cross in the middle of the view
        activated = true if the cross should be displayed
        """
        # We don't specifically know about the crosshair, so look for it in the static overlays
        ch = None
        for o in self.ViewOverlays:
            if isinstance(o, CrossHairOverlay):
                ch = o
                break

        if activated:
            if not ch:
                ch = CrossHairOverlay(CROSSHAIR_COLOR, CROSSHAIR_SIZE)
                self.ViewOverlays.append(ch)
                self.Refresh(eraseBackground=False)
        else:
            if ch:
                self.ViewOverlays.remove(ch)
                self.Refresh(eraseBackground=False)

    def Zoom(self, inc):
        """
        Zoom by the given factor
        inc (float): scale the current view by 2^inc
        ex:  # 1 => *2 ; -1 => /2; 2 => *4...
        """
        scale = 2.0 ** inc
        self.view.mpp.value /= scale

    def _onMPP(self, mpp):
        self.scale = self.mpwu / mpp
        self.ShouldUpdateDrawing()

    def avOnImage(self, image):
        # FIXME: use stream.getImage
        for i in range(len(self.Images)):
            iim = self.viewmodel.images[i].value
            if iim.image:
                scale = float(iim.mpp) / self.mpwu
                pos = (iim.center[0] / self.mpwu, iim.center[1] / self.mpwu)
                #pos = iim.center
                self.SetImage(i, iim.image, pos, scale)
                #self.ReCenterBuffer(pos)
            else:
                #TODO： that should be better, but we don't do it for now, to detect when to reset mpp
                #self.SetImage(i, None) # removes the image
                pass

    # Zoom/merge management
    def OnWheel(self, event):
        change = event.GetWheelRotation() / event.GetWheelDelta()
        if event.ShiftDown():
            change *= 0.2 # softer

        if event.CmdDown(): # = Ctrl on Linux/Win or Cmd on Mac
            ratio = self.view.merge_ratio.value + (change * 0.1) 
            ratio = sorted(self.view.merge_ratio.range + (ratio,))[1] # clamp
            self.view.merge_ratio.value = ratio
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