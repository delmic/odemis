#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 2 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Delmic Acquisition Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''
import ctypes
import math
import os
import time
import wx

# A class for smooth, flicker-less display of anything on a window, with drag 
# and zoom capability a bit like:
# wx.canvas, wx.BufferedWindow, BufferedCanvas, wx.floatcanvas, wx.scrolledwindow...
# The main differences are:
#  * when dragging the window the surrounding margin is already computed
#  * You can draw at any coordinate, and it's displayed if the user has dragged the canvas close from the area.
#  * Built-in optimised zoom/transparency for 2 images
# Maybe could be replaced by a GLCanvas + magic, or a Cairo Canvas
class DraggableCanvas(wx.Panel):
    """
    A draggable, buffered window class.

    To use it, instantiate it and then put what you want to display in the lists:
    * Images: for the two images to display
    * Overlays: for additional objects to display (should have a Draw(dc) method)
    * StaticOverlays: for additional objects that stay at an absolute position
    
    The idea = three layers of decreasing area size:
    * The whole world, which can have infinite dimensions, but needs a redraw
    * The buffer, which contains a precomputed image of the world big enough that a drag cannot bring it outside of the viewport
    * The viewport, which is what the user sees

    """
    def __init__(self, parent):
        wx.Panel.__init__(self, parent, style=wx.NO_FULL_REPAINT_ON_RESIZE)
        self.Overlays = [] # on top of the pictures, relative position
        self.StaticOverlays = [] # on top, stays at an absolute position
        self.Images = [None, None]
        #self.available_im = (wx.Image("02701s.jpg"), wx.Image("03330c.jpg"))
        self.merge_ratio = 0.3 # 0<float<1 of how much to see the first picture
        self.zoom = 0 # float, can also be negative
        self.zoom_range = (-10.0, 10.0)
#        self.available_im[0].InitAlpha()
#        self.available_im[1].InitAlpha()
        
        self.world_pos = (0,0) # centred
        
        # buffer = the whole image to be displayed
        self._dcBuffer =  wx.MemoryDC()

        self.buffer_size = (1, 1) # very small first, so that for sure it'll be resized with OnSize
        self.ResizeBuffer(self.buffer_size)
        # When resizing, margin to put around the current size
        self.margin = 512
        
        if os.name == "nt":
            # Avoids flickering on windows, but prevents black background on Linux...
            self.SetBackgroundStyle(wx.BG_STYLE_CUSTOM)
        self.SetBackgroundColour('black')# black (grey is for debugging)
#        parent.SetBackgroundColour('Grey') # black # maybe it's not so clever to do it here    

        # view = the area displayed
        self.drag_shift = (0,0) # Current (dragging) position = centred
        self.dragging = False
        
        # timer to give a delay before redrawing so we wait to see if there are several events waiting
        self.DrawTimer = wx.PyTimer(self.OnDrawTimer)
        
        wx.EVT_PAINT(self, self.OnPaint)
        wx.EVT_SIZE(self, self.OnSize)
        
        wx.EVT_LEFT_DOWN(self, self.OnLeftDown)
        wx.EVT_LEFT_UP(self, self.OnLeftUp)
        wx.EVT_MOTION(self, self.OnMouseMotion)
        wx.EVT_MOUSEWHEEL(self, self.OnWheel)
        wx.EVT_RIGHT_DOWN(self, self.OnRightDown)
        wx.EVT_RIGHT_UP(self, self.OnRightUp)
        wx.EVT_CHAR(self, self.OnChar)
        
        self.n = 0
        
    def GetZoom(self):
        """
        return the zoom value (float)
        Just to be symmetrical with SetZoom
        """
        return self.zoom
    
    def SetZoom(self, val):
        """
        Change the zoom value (targetted) and update the screen
        val (minzoom<float<maxzoom): the actual size is 2^zoom if outside of the authorised values, it is clamped
        """
        self.zoom = sorted(self.zoom_range + (val,))[1] # clamp
        self.ShouldUpdateDrawing()
    
    def GetMergeRatio(self):
        """
        return the merge value (float)
        Just to be symmetrical with SetMergeRatio
        """
        return self.merge_ratio
    
    def SetMergeRatio(self, val):
        """
        Change the merge ratio  and update the screen
        val (0<float<1): the merge ratio, if outside of the authorised values, it is clamped
        """
        self.merge_ratio = sorted((0.0, 1.0) + (val,))[1] # clamp
        self.ShouldUpdateDrawing()

    def OnChar(self, event):
        key = event.GetKeyCode()
        
        change = 16
        if event.ShiftDown():
            change = 2 # softer
            
        if key == wx.WXK_LEFT:
            self.ShiftView((-change,0))
        elif key == wx.WXK_RIGHT:
            self.ShiftView((change,0))
        elif key == wx.WXK_DOWN:
            self.ShiftView((0,change))
        elif key == wx.WXK_UP:
            self.ShiftView((0,-change))
            
    def OnRightDown(self, event):
        pass

    def OnRightUp(self, event):
        self.ShouldUpdateDrawing()
    
    def ShiftView(self, shift):
        self.ReCenterBuffer((self.world_pos[0] + shift[0],
                            self.world_pos[1] + shift[1]))
            
    def ReCenterBufferAroundView(self):
        # self.drag_shift is the delta we want to apply
        new_pos = (self.world_pos[0] + self.drag_shift[0],
                   self.world_pos[1] + self.drag_shift[1])
        self.drag_shift = (0, 0)
        self.ReCenterBuffer(new_pos)
        
    def ReCenterBuffer(self, pos):
        self.world_pos = pos
        self.ShouldUpdateDrawing() # XXX could maybe be more clever and only request redraw for the outside region
        
    def OnLeftDown(self, event):
        self.dragging = True
        self.drag_init_pos = self.ClientToScreen(event.GetPositionTuple())
        self.drag_init_viewpos = self.drag_shift
        self.SetCursor(wx.StockCursor(wx.CURSOR_SIZING))
        if not self.HasCapture():
            self.CaptureMouse()
        
        self.t_start = time.time()
        self.n = 0
        
    def OnLeftUp(self, event):
        self.dragging = False
        self.SetCursor(wx.STANDARD_CURSOR)
        if self.HasCapture():
            self.ReleaseMouse()
        self.ReCenterBufferAroundView()
        
        t_now = time.time()
        fps = self.n / float(t_now - self.t_start)
        print "Display speed: " + str(fps) + " fps."

    
    def OnMouseMotion(self, event):
        if self.dragging:
            pos = self.ClientToScreen(event.GetPositionTuple())
            shift = (pos[0] - self.drag_init_pos[0],
                     pos[1] - self.drag_init_pos[1])
            self.drag_shift = (self.drag_init_viewpos[0] + shift[0],
                        self.drag_init_viewpos[1] + shift[1])
            self.Refresh()

    def OnWheel(self, event):
        change =  event.GetWheelRotation() / event.GetWheelDelta()
        if event.ShiftDown():
            change *= 0.2 # softer
        
        if event.CmdDown(): # = Ctrl on Linux/Win or Cmd on Mac
            self.SetMergeRatio(self.GetMergeRatio() + change * 0.1)
        else:
            self.SetZoom(self.GetZoom() + change)

    # TODO see if with Numpy it's faster (~less memory copy), cf http://wiki.wxpython.org/WorkingWithImages
    # Could also see gdk_pixbuf_composite()
    def RescaleImageOptimized(self, im, scale, center):
        """
        Rescale an image considering it will be displayed centred at 'center' on the buffer
        Does not modify the original image
        return a copy of the image rescaled, it can be of any size 
            + .tl on the image for the top-left point, if there is a shift
        """
        if scale == 1.0:
            ret = im.Copy() # TODO: should see how to avoid (it slows down quite a bit)
            ret.tl = None
        elif scale < 1.0:
            orig_size = im.GetSize()
            final_size = (int(orig_size[0] * scale), int(orig_size[1] * scale))
            ret = im.Scale(*final_size)   
            ret.tl = None
        elif scale > 1.0:
            # We could end-up with a lot of the computation useless, so crop it
            orig_size = im.GetSize()
            full_size = (int(orig_size[0] * scale), int(orig_size[1] * scale))
            full_rect = (center[0] - full_size[0]/2, center[1] - full_size[1]/2,
                         full_size[0], full_size[1])
            # where is the buffer in the world?
            buffer_rect = (-self.world_pos[0], -self.world_pos[1],
                           self.buffer_size[0], self.buffer_size[1])
            goal_rect = wx.IntersectRect(full_rect, buffer_rect)
            # where is this rect in the original image?
            # Note that width and length must be "double rounded up" to account
            # for the round down of the origin
            unscaled_rect = (int((goal_rect[0] - full_rect[0]) / scale), # rounding down
                             int((goal_rect[1] - full_rect[1]) / scale),
                             math.ceil(goal_rect[2] / scale + 0.5), # 2 x rounding up
                             math.ceil(goal_rect[3] / scale + 0.5)) # XXX could be improved
            # like goal_rect but taking into account rounding
            final_rect = ((unscaled_rect[0] * scale) + full_rect[0],
                          (unscaled_rect[1] * scale) + full_rect[1],
                          unscaled_rect[2] * scale,
                          unscaled_rect[3] * scale)
            imcropped = im.GetSubImage(unscaled_rect)
            ret = imcropped.Rescale(final_rect[2], final_rect[3])
            # need to save it as the cropped part is not centred anymore
            ret.tl = (final_rect[0], final_rect[1])
        return ret
        
    def DrawImageCentred(self, dc, im, center):
        if not im.tl:
            size = im.GetSize()
            tl = (center[0] - (size[0] / 2),
                  center[1] - (size[1] / 2))
        else:
            tl = im.tl
        dc.DrawBitmapPoint(wx.BitmapFromImage(im), tl)
        
    @staticmethod
    def memsetObject(bufferObject, value):
        "Note, dangerous"
        data = ctypes.POINTER(ctypes.c_char)()
        size = ctypes.c_int()
        ctypes.pythonapi.PyObject_AsCharBuffer(ctypes.py_object(bufferObject), ctypes.pointer(data), ctypes.pointer(size))
        ctypes.memset(data, value, size.value)

    def _DrawImageTransparentRescaled(self, dc, im, center, ratio = 1.0, scale = 1.0):
        if ratio <= 0.0:
            return
        
        print "scale=", scale
        imscaled = self.RescaleImageOptimized(im, scale, center)
        print "ratio=", ratio
        if ratio < 1.0:
            # im2merged = im2scaled.AdjustChannels(1.0,1.0,1.0,ratio)
            # TODO Check if we could speed up by caching the alphabuffer 
            abuf = imscaled.GetAlphaBuffer()
            self.memsetObject(abuf, int(255 * ratio))
        self.DrawImageCentred(dc, imscaled, center)
    
    def DrawMergedImages(self, dc, im1, im2, center, ratio = 0.5, scale = 1.0):
        """
        Draw the two images on the DC, centred around drag_shift, with their own scale,
        and an opacity of "ratio" for im1. They should be of the same size ratio.
        dc: wx.DC
        im1, im2 (wx.Image): the images
        center (x, y): center position 
        ratio (0<float<1): how much to merge the images
        scale (0<float): the scaling of the images in addition to their own 
        """
        t_start = time.time()
        
        if im1:
            scale1 = im1._dc_scale * scale
        if im2:
            scale2 = im2._dc_scale * scale
            
        # There can be no image or just one image
        if not im1:
            if not im2:
                return
            self._DrawImageTransparentRescaled(dc, im2, center, scale=scale2)
        elif not im2:
            self._DrawImageTransparentRescaled(dc, im1, center, scale=scale1)
        # The biggest picture should be drawn first, so that the outside is not
        # mixed with the black background
        elif (im1.GetWidth() * scale1 >= im2.GetWidth() * scale2):
            self._DrawImageTransparentRescaled(dc, im1, center, scale=scale1)
            self._DrawImageTransparentRescaled(dc, im2, center, 1.0 - ratio, scale2)
        else:
            self._DrawImageTransparentRescaled(dc, im2, center, scale=scale2)
            self._DrawImageTransparentRescaled(dc, im1, center, ratio, scale1)
        
        t_now = time.time()
        fps = 1.0 / float(t_now - t_start)
        print "Display speed: " + str(fps) + " fps."

    def Draw(self, dc):
        dc.Clear()
        # 1 => *2 ; -1 => /2; 2 => *4...
        scale = math.pow(2.0, self.zoom)
        self.DrawMergedImages(dc, self.Images[0], self.Images[1], (0,0),
                              self.merge_ratio, scale)
#        print "New bitmap drawing"
        
        # Each overlay draws itself
        # TODO: pass the scale, or use the scale of the DC?
        for data in self.Overlays:
            data.Draw(dc)

    def DrawStaticOverlays(self, dc):
        dc.SetDeviceOrigin(self.ClientSize[0]/2, self.ClientSize[1]/2)
        for o in self.StaticOverlays:
            o.Draw(dc)

    def OnPaint(self, event):
        self.n += 1 # for fps
    
        dc = wx.PaintDC(self)
        margin = ((self.buffer_size[0] - self.ClientSize[0])/2,
                  (self.buffer_size[1] - self.ClientSize[1])/2)

#        dc.BlitPointSize(self.drag_shift, self.buffer_size, self._dcBuffer, (0,0))
        dc.BlitPointSize((0,0), self.ClientSize, self._dcBuffer, 
                         (margin[0] - self.drag_shift[0], margin[1] - self.drag_shift[1]))
        # TODO do this only when drag_shift changes, and record the modified region before and put back after.
        self.DrawStaticOverlays(dc)

    def OnSize(self, event):
        # Make sure the buffer is always at least the same size as the Window or bigger
        new_size = (max(self.buffer_size[0], self.ClientSize[0] + self.margin * 2),
                    max(self.buffer_size[1], self.ClientSize[1] + self.margin * 2))
       
        # recenter the view
        if (new_size != self.buffer_size):
            self.ResizeBuffer(new_size)
            self.ReCenterBuffer((new_size[0]/2, new_size[1]/2))
        else:
            self.Refresh(eraseBackground=False) # because it's centred so everything moves

    def ResizeBuffer(self, size):
        # Make new offscreen bitmap: this bitmap will always have the
        # current drawing in it
        self._buffer = wx.EmptyBitmap(*size)
        self.buffer_size = size
        self._dcBuffer.SelectObject(self._buffer)
        self._dcBuffer.SetBackground(wx.BLACK_BRUSH) # On Linux necessary after every select object
        
    def ShouldUpdateDrawing(self, period = 0.1):
        """
        Schedule the update of the buffer
        period (second): maximum time to wait before it will be updated
        """
        if not self.DrawTimer.IsRunning():
            self.DrawTimer.Start(period * 1000.0, oneShot=True)
        else:
            print self.DrawTimer.GetInterval()

    def OnDrawTimer(self):
        self.UpdateDrawing()
    
    def UpdateDrawing(self):
        """
        Redraws everything (that is viewed in the buffer)
        """
        # set and reset the origin here because Blit in onPaint gets "confused" with values > 2048
        self._dcBuffer.SetDeviceOriginPoint(self.world_pos)
        self.Draw(self._dcBuffer)
        self._dcBuffer.SetDeviceOriginPoint((0,0))
        self.Refresh(eraseBackground=False) # eraseBackground doesn't seem to matter, but just in case...
        # TODO maybe just queue it...?
        self.Update() # not really necessary as refresh causes an onPaint event soon, but makes it slightly sooner, so smoother
    
# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: