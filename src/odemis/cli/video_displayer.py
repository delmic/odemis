# -*- coding: utf-8 -*-
'''
Created on 20 Jul 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
'''
import cairo
import logging
import numpy
from odemis.gui.util.img import NDImage2wxImage
from odemis.util import img
from scipy import ndimage
import wx

MAX_WIDTH = 2000


class VideoDisplayer(object):
    '''
    Very simple display for a continuous flow of images as a window
    It should be pretty much platform independent.
    '''

    def __init__(self, title="Live image", size=(640, 480)):
        '''
        Displays the window on the screen
        size (2-tuple int,int): X and Y size of the window at initialisation
        Note that the size of the window automatically adapts afterwards to the
        coming pictures
        '''
        self.app = ImageWindowApp(title, size)

    def new_image(self, data):
        """
        Update the window with the new image (the window is resize to have the image
        at ratio 1:1)
        data (numpy.ndarray): an 2D array containing the image (can be 3D if in RGB)
        """
        if data.ndim == 3 and 3 in data.shape: # RGB
            rgb = img.ensureYXC(data)
        elif numpy.prod(data.shape) == 1:  # single point => show text
            text = "%g" % (data.flat[0],)
            logging.info("Data value is: %s", text)
            # Create a big enough white space (30x200 px) of BGRA format
            rgb = numpy.empty((30, 200, 4), dtype=numpy.uint8)
            rgb.fill(255)
            # Get a Cairo context for that image
            surface = cairo.ImageSurface.create_for_data(
                rgb, cairo.FORMAT_ARGB32, rgb.shape[1], rgb.shape[0])
            ctx = cairo.Context(surface)
            # Draw a black text of 20 px high
            ctx.set_source_rgb(0, 0, 0)
            ctx.select_font_face("Sans", cairo.FONT_SLANT_NORMAL)
            ctx.set_font_size(20)
            ctx.move_to(5, 20)
            ctx.show_text(text)
            del ctx  # ensure the context is flushed
        elif numpy.prod(data.shape) == data.shape[-1]: # 1D image => bar plot
            # TODO: add "(plot)" to the window title
            # Create a simple bar plot of X x 400 px
            lenx = data.shape[-1]
            if lenx > MAX_WIDTH:
                binning = lenx // MAX_WIDTH
                data = data[..., 0::binning]
                logging.debug("Compressed data from %d to %d elements", lenx, data.shape[-1])
                lenx = data.shape[-1]
            leny = 400
            miny = min(0, data.min())
            maxy = data.max()
            diffy = maxy - miny
            if diffy == 0:
                diffy = 1
            logging.info("Plot data from %s to %s", miny, maxy)
            rgb = numpy.zeros((leny, lenx, 3), dtype=numpy.uint8)
            for i, v in numpy.ndenumerate(data):
                # TODO: have the base at 0, instead of miny, so that negative values are columns going down
                h = leny - int(((v - miny) * leny) / diffy)
                rgb[h:-1, i[-1], :] = 255
        else: # Greyscale (hopefully)
            mn, mx, mnp, mxp = ndimage.extrema(data)
            logging.info("Image data from %s to %s", mn, mx)
            rgb = img.DataArray2RGB(data) # auto brightness/contrast

        self.app.img = NDImage2wxImage(rgb)
        disp_size = wx.Display(0).GetGeometry().GetSize()
        if disp_size[0] < rgb.shape[1] or disp_size[1] < rgb.shape[0]:
            asp_ratio = rgb.shape[1] / rgb.shape[0]
            if disp_size[0] / disp_size[1] < asp_ratio:
                self.app.magn = disp_size[0] / rgb.shape[1]
            else:
                self.app.magn = disp_size[1] / rgb.shape[0]
            self.app.img.Rescale(rgb.shape[1] * self.app.magn,
                                 rgb.shape[0] * self.app.magn,
                                 rgb.shape[2])
        else:
            self.app.magn = 1

        wx.CallAfter(self.app.update_view)

    def waitQuit(self):
        """
        returns when the window is closed (or the user pressed Q)
        """
        self.app.MainLoop() # TODO we could use a Event if multiple accesses must be supported


class ImageWindowApp(wx.App):
    def __init__(self, title, size):
        wx.App.__init__(self, redirect=False)
        self.AppName = "Odemis CLI"
        self.frame = wx.Frame(None, title=title, size=size)

        self.panel = wx.Panel(self.frame)
        self.panel.Bind(wx.EVT_KEY_DOWN, self.OnKey)
        # just in case panel doesn't have the focus: also on the frame
        # (but it seems in Linux (GTK) frames don't receive key events anyway
        self.frame.Bind(wx.EVT_KEY_DOWN, self.OnKey)

        self.img = wx.Image(*size, clear=True)
        self.imageCtrl = wx.StaticBitmap(self.panel, wx.ID_ANY, wx.Bitmap(self.img))

        self.panel.SetFocus()
        self.frame.Show()

    def update_view(self):
        logging.debug("Received a new image of %d x %d", *self.img.GetSize())
        self.frame.ClientSize = self.img.GetSize()
        if wx.MAJOR_VERSION <= 3:
            self.imageCtrl.SetBitmap(wx.BitmapFromImage(self.img))
        else:
            self.imageCtrl.SetBitmap(wx.Bitmap(self.img))

    def OnKey(self, event):
        key = event.GetKeyCode()
        if key in [ord("q"), ord("Q")]:
            self.frame.Destroy()

        # everything else we don't process
        event.Skip()
