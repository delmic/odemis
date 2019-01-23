#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Created on 14 Jan 2019

@author: Thera Pals

This script provides a command line interface for displaying a video with a spot grid overlay.

"""
from __future__ import division, print_function

import argparse
import wx.lib.wxcairo  # should be imported before cairo
import cairo
import logging
import numpy
from odemis import model
from odemis.acq.align.spot import FindGridSpots
from odemis.driver import ueye
from odemis.gui.util.img import NDImage2wxImage
from odemis.util import img
from odemis.util.driver import get_backend_status, BACKEND_RUNNING
from scipy import ndimage
import sys
import threading
import wx

MAX_WIDTH = 2000  # px
PIXEL_SIZE_UM = 3.45 / 50.


class VideoDisplayer(object):
    """
    Very simple display for a continuous flow of images as a window with an overlay of a grid of spots.
    It should be pretty much platform independent.
    """

    def __init__(self, title="Live image", size=(640, 480), gridsize=None):
        """
        Displays the window on the screen
        size (2-tuple int,int): X and Y size of the window at initialisation
        Note that the size of the window automatically adapts afterwards to the
        coming pictures
        """
        self.app = ImageWindowApp(title, size)
        self.gridsize = (14, 14) if gridsize is None else gridsize

    def new_image(self, data):
        """
        Update the window with the new image (the window is resize to have the image
        at ratio 1:1)
        data (numpy.ndarray): an 2D array containing the image (can be 3D if in RGB)
        """
        if data.ndim == 3 and 3 in data.shape:  # RGB
            rgb = img.ensureYXC(data)
        elif numpy.prod(data.shape) == data.shape[-1]:  # 1D image => bar plot
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
        else:  # Greyscale (hopefully)
            mn, mx, mnp, mxp = ndimage.extrema(data)
            logging.info("Image data from %s to %s", mn, mx)
            rgb = img.DataArray2RGB(data)  # auto brightness/contrast
        self.app.spots, self.app.translation, self.app.scaling, self.app.rotation = FindGridSpots(data, self.gridsize)
        self.app.img = NDImage2wxImage(rgb)
        wx.CallAfter(self.app.update_view)

    def waitQuit(self):
        """
        returns when the window is closed (or the user pressed Q)
        """
        self.app.MainLoop()  # TODO we could use a Event if multiple accesses must be supported


class ImageWindowApp(wx.App):
    def __init__(self, title, size):
        wx.App.__init__(self, redirect=False)
        self.AppName = "Spot Grid CLI"
        self.frame = wx.Frame(None, title=title, size=size)

        self.panel = wx.Panel(self.frame)
        self.panel.Bind(wx.EVT_KEY_DOWN, self.OnKey)
        # just in case panel doesn't have the focus: also on the frame
        # (but it seems in Linux (GTK) frames don't receive key events anyway
        self.frame.Bind(wx.EVT_KEY_DOWN, self.OnKey)

        if wx.MAJOR_VERSION <= 3:
            self.img = wx.EmptyImage(*size, clear=True)
            self.imageCtrl = wx.StaticBitmap(self.panel, wx.ID_ANY, wx.BitmapFromImage(self.img))
            self.imageCtrlSpots = wx.StaticBitmap(self.panel, wx.ID_ANY, wx.BitmapFromImage(self.img))
            self.imageCtrlText = wx.StaticBitmap(self.panel, wx.ID_ANY, wx.BitmapFromImage(self.img))
        else:
            self.img = wx.Image(*size, clear=True)
            self.imageCtrl = wx.StaticBitmap(self.panel, wx.ID_ANY, wx.Bitmap(self.img))
            self.imageCtrlSpots = wx.StaticBitmap(self.panel, wx.ID_ANY, wx.Bitmap(self.img))
            self.imageCtrlText = wx.StaticBitmap(self.panel, wx.ID_ANY, wx.Bitmap(self.img))
        self.panel.SetFocus()
        self.frame.Show()

    def update_view(self):
        logging.debug("Received a new image of %d x %d", *self.img.GetSize())
        self.frame.ClientSize = self.img.GetSize()

        height = self.img.Height
        width = self.img.Width
        spot_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        ctx = cairo.Context(spot_surface)
        ctx.set_source_rgb(0.5, 0.1, 0.1)
        ctx.scale(width, height)
        spots = numpy.abs(self.spots) / numpy.array([width, height])
        spot_temp = numpy.array([0, 0])
        for spot in spots:
            ctx.translate(*spot_temp)  # translate back to the origin since spot_temp is negative
            ctx.translate(*spot)  # translate from the origin to the coordinate of the spot
            spot_temp = numpy.copy(-spot)
            ctx.arc(0, 0, 0.0025, 0, 2 * numpy.pi)
            ctx.fill()
        text_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        info = [
            "rotation: {:.1f} deg".format(numpy.rad2deg(self.rotation)),
            "pitch-x: {:.2f} um".format(PIXEL_SIZE_UM * self.scaling[0]),
            "pitch-y: {:.2f} um".format(PIXEL_SIZE_UM * self.scaling[1]),
            "translation-x: {:.1f} um".format(PIXEL_SIZE_UM * self.translation[0]),
            "translation-y: {:.1f} um".format(PIXEL_SIZE_UM * self.translation[1]),
        ]
        ctx2 = cairo.Context(text_surface)
        ctx2.set_source_rgb(1.00, 0.83, 0.00)
        font_size = 20
        ctx2.set_font_size(font_size)
        ctx2.translate(font_size, font_size)
        # Cairo doesn't do multiline text plotting, so loop over the text and show at a lower location.
        for text in info:
            ctx2.translate(0, font_size)
            ctx2.show_text(text)
            ctx2.stroke()

        if wx.MAJOR_VERSION <= 3:
            self.imageCtrl.SetBitmap(wx.BitmapFromImage(self.img))
            self.imageCtrlSpots.SetBitmap(wx.lib.wxcairo.BitmapFromImageSurface(spot_surface))
            self.imageCtrlText.SetBitmap(wx.lib.wxcairo.BitmapFromImageSurface(text_surface))
        else:
            self.imageCtrl.SetBitmap(wx.Bitmap(self.img))
            self.imageCtrlSpots.SetBitmap(wx.lib.wxcairo.BitmapFromImageSurface(spot_surface))
            self.imageCtrlText.SetBitmap(wx.lib.wxcairo.BitmapFromImageSurface(text_surface))

    def OnKey(self, event):
        key = event.GetKeyCode()
        if key in [ord("q"), ord("Q")]:
            self.frame.Destroy()

        # everything else we don't process
        event.Skip()


class ImagePasser(object):
    def __init__(self):
        self.image = None
        self.available = threading.Event()
        self.display = True


def image_update(imp, window):
    try:
        while imp.display:
            imp.available.wait()
            imp.available.clear()
            if not imp.display:
                return
            window.new_image(imp.image)
    except Exception:
        logging.exception("Failure during display")
    finally:
        logging.debug("Display thread ended")


def live_display(ccd, dataflow, kill_ccd=True, gridsize=None):
    """
    Acquire an image from one (or more) dataflow and display it with a spot grid overlay.
    ccd: a camera object
    dataflow_name: name of the dataflow to access
    kill_ccd: True if it is required to terminate the ccd after closing the window
    gridsize: size of the grid of spots.
    """
    # create a window
    window = VideoDisplayer("Live from %s.%s" % (ccd.role, "data"), ccd.resolution.value, gridsize)
    im_passer = ImagePasser()
    t = threading.Thread(target=image_update, args=(im_passer, window))
    t.daemon = True
    t.start()

    def new_image_wrapper(df, image):
        im_passer.image = image
        im_passer.available.set()

    try:
        dataflow.subscribe(new_image_wrapper)
        # wait until the window is closed
        window.waitQuit()
    finally:
        im_passer.display = False
        im_passer.available.set()  # Force the thread to check the .display flag
        dataflow.unsubscribe(new_image_wrapper)
        if kill_ccd:
            ccd.terminate()


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """
    # arguments handling
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", dest="role", metavar="<component>",
                        help="display and update an image on the screen")
    parser.add_argument("--gridsize", dest="gridsize", nargs=2, metavar="<gridsize>", type=int, default=None,
                        help="size of the grid of spots in x y, default 14 14")
    parser.add_argument("--log-level", dest="loglev", metavar="<level>", type=int, choices=[0, 1, 2],
                        default=0, help="set verbosity level (0-2, default = 0)")
    options = parser.parse_args(args[1:])
    # Set up logging before everything else
    loglev_names = [logging.WARNING, logging.INFO, logging.DEBUG]
    loglev = loglev_names[min(len(loglev_names) - 1, options.loglev)]

    # change the log format to be more descriptive
    handler = logging.StreamHandler()
    logging.getLogger().setLevel(loglev)
    handler.setFormatter(logging.Formatter('%(asctime)s (%(module)s) %(levelname)s: %(message)s'))
    logging.getLogger().addHandler(handler)

    if options.role:
        if get_backend_status() != BACKEND_RUNNING:
            raise ValueError("Backend is not running while role command is specified.")
        ccd = model.getComponent(role=options.role)
        live_display(ccd, ccd.data, kill_ccd=False, gridsize=options.gridsize)
    else:
        ccd = ueye.Camera("camera", "ccd", device=None)
        ccd.SetFrameRate(2)
        live_display(ccd, ccd.data, gridsize=options.gridsize)
    return 0


if __name__ == '__main__':
    main(sys.argv)
