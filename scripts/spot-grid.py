#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 14 Jan 2019

@author: Thera Pals

This script provides a command line interface for displaying a video with a spot grid overlay.

"""
import argparse
import logging
import os
import sys
import threading

import cairo
import numpy
import wx
import wx.lib.wxcairo

from odemis import dataio, model
from odemis.cli.video_displayer import VideoDisplayer
from odemis.driver import ueye
from odemis.gui.conf.file import AcquisitionConfig
from odemis.util.driver import BACKEND_RUNNING, get_backend_status
from odemis.util.registration import estimate_grid_orientation_from_img, unit_gridpoints
from odemis.util.transform import (
    AffineTransform,
    to_physical_space_transform,
    to_pixel_index_transform,
)

MAX_WIDTH = 2000  # px
PIXEL_SIZE_SAMPLE_PLANE = 3.45e-6  # m
DEFAULT_MAGNIFICATION = 40
PIXEL_SIZE = PIXEL_SIZE_SAMPLE_PLANE / DEFAULT_MAGNIFICATION
DEFAULT_PITCH = 3.2e-6
# 0.75 is a safety factor to allow for some variation in spot positions
MIN_DIST_SPOTS = int(0.75 * DEFAULT_PITCH / PIXEL_SIZE)


class VideoDisplayerGrid(VideoDisplayer):
    """
    Very simple display for a continuous flow of images as a window with an overlay of a grid of spots.
    It should be pretty much platform independent.
    """

    def __init__(self, title="Live image", size=(640, 480), gridsize=None, pixel_size=PIXEL_SIZE,
                 min_dist_spots=MIN_DIST_SPOTS):
        """
        Displays the window on the screen
        size (2-tuple int,int): X and Y size of the window at initialisation
        pixel_size (float): pixel size in m
        min_dist_spots (int): minimum distance between spots in pixels
        Note that the size of the window automatically adapts afterwards to the
        coming pictures
        """
        self.app = ImageWindowApp(title, size, pixel_size)
        self.gridsize = (8, 8) if gridsize is None else gridsize
        self.min_dist_spots = min_dist_spots
        self.acqui_conf = AcquisitionConfig()

    def new_image(self, data):
        """
        Update the window with the new image (the window is resize to have the image
        at ratio 1:1)
        data (numpy.ndarray): an 2D array containing the image (can be 3D if in RGB)
        """
        try:
            tform_ji, _ = estimate_grid_orientation_from_img(
                data,
                self.gridsize,
                AffineTransform,
                sigma=1.45,
                threshold_rel=self.acqui_conf.spot_grid_threshold,
                min_distance=self.min_dist_spots,
            )
            grid = unit_gridpoints(self.gridsize, mode="ji")
            self.app.spots = tform_ji.apply(grid)

            tform_xy = (
                    to_physical_space_transform(data.shape, self.app.pixel_size)
                    @ tform_ji
                    @ to_pixel_index_transform()
            )

            self.app.translation = tform_xy.translation
            self.app.scale = tform_xy.scale
            self.app.rotation = tform_xy.rotation
            self.app.squeeze = tform_xy.squeeze
            self.app.shear = tform_xy.shear
        except ValueError as err:
            logging.warning("No grid found on image, cannot display spots: %s", err)
            self.app.spots = None

        super(VideoDisplayerGrid, self).new_image(data)

    def waitQuit(self):
        """
        returns when the window is closed (or the user pressed Q)
        """
        self.app.MainLoop()  # TODO we could use a Event if multiple accesses must be supported


class ImageWindowApp(wx.App):
    def __init__(self, title, size, pixel_size):
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

        self.pixel_size = pixel_size

    def update_view(self):
        logging.debug("Received a new image of %d x %d", *self.img.GetSize())
        self.frame.ClientSize = self.img.GetSize()

        height = self.img.Height
        width = self.img.Width
        spot_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        # only draw spots if they are found in the image
        if self.spots is not None:
            ctx = cairo.Context(spot_surface)

            # the matrix applied here makes Cairo accept coordinates of the form `(j, i)`.
            matrix = cairo.Matrix(0, 1, 1, 0, 0, 0)
            ctx.set_matrix(matrix)

            # window shape may be unequal to image shape.
            ctx.scale(self.magn, self.magn)

            # draw a polygon connecting all spots
            ctx.save()
            ctx.set_source_rgb(0.98, 0.91, 0.62)
            ctx.set_line_width(2)
            ctx.set_dash((10, 4))
            spot = self.spots[0]
            ctx.move_to(*spot)
            for spot in self.spots[1:]:
                ctx.line_to(*spot)
            ctx.stroke()
            ctx.restore()

            # draw a square at the first spot, and a circle at every other spot
            ctx.save()
            ctx.set_source_rgb(0.8, 0.1, 0.1)
            ctx.set_line_width(2)
            spot = self.spots[0]
            ctx.rectangle(spot[0] - 8, spot[1] - 8, 16, 16)
            ctx.stroke()
            for spot in self.spots[1:]:
                ctx.arc(spot[0], spot[1], 4, 0, 2 * numpy.pi)
                ctx.fill()
            ctx.restore()

        text_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        # only show the text if there are spots in the image
        if self.spots is not None:
            info = [
                "rotation: {:.1f} deg".format(numpy.rad2deg(self.rotation)),
                "pitch: {:.3f} um".format(1e6 * self.scale),
                "translation-x: {:.3f} um".format(1e6 * self.translation[0]),
                "translation-y: {:.3f} um".format(1e6 * self.translation[1]),
                "squeeze: {:.5f}".format(self.squeeze),
                "shear: {:.5f} ".format(self.shear),
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


class StaticCCD(model.DigitalCamera):
    def __init__(self, name, role, array, **kwargs):
        super(StaticCCD, self).__init__(name, role, **kwargs)
        self.array = array
        self.data = StaticImageDataFlow(self)
        res = self.array.shape
        self.resolution = model.ResolutionVA(res, (res, res), readonly=True)


class StaticImageDataFlow(model.DataFlow):
    def __init__(self, detector):
        model.DataFlow.__init__(self)
        self._detector = detector

    def start_generate(self):
        self.notify(self._detector.array)


def live_display(ccd, dataflow, pixel_size, kill_ccd=True, gridsize=None, min_dist_spots=MIN_DIST_SPOTS):
    """
    Acquire an image from one (or more) dataflow and display it with a spot grid overlay.
    ccd: a camera object
    dataflow: dataflow to access
    pixel_size (float): pixel size in m
    kill_ccd: True if it is required to terminate the ccd after closing the window
    gridsize: size of the grid of spots.
    min_dist_spots: minimum distance between spots in pixels
    """
    # create a window
    window = VideoDisplayerGrid("Live from %s.%s" % (ccd.role, "data"), ccd.resolution.value, gridsize, pixel_size,
                                min_dist_spots)
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
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--role", dest="role", metavar="<component>",
                       help="display and update an image on the screen")
    group.add_argument("--file", metavar="<filename>", dest="filename",
                       help="display and update an image on the screen")
    parser.add_argument("--gridsize", dest="gridsize", nargs=2, metavar="<gridsize>", type=int, default=None,
                        help="size of the grid of spots in x y, default 8 8")
    parser.add_argument("--magnification", dest="magnification", type=float,
                        help="magnification (typically 40 or 50)")
    parser.add_argument("--pitch", dest="pitch", type=float, default=None,
                        help="pitch in meters (typically 3.2e-6)")
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

    # Magnification: use cli input value. If none is specified, try to read out lens magnification.
    try:
        lens = model.getComponent(role="lens")
        lens_mag = lens.magnification.value
    except Exception as ex:
        logging.debug("Failed to read magnification from lens, ex: %s", ex)
        lens_mag = None

    if options.magnification:
        magnification = options.magnification
        if lens_mag and lens_mag != magnification:
            logging.warning("Requested magnification %s differs from lens magnification %s.",
                            magnification, lens_mag)
    elif lens_mag:
        magnification = lens_mag
        logging.debug("No magnification specified, using lens magnification %s.", lens_mag)
    else:
        magnification = DEFAULT_MAGNIFICATION
        logging.warning("No magnification specified, falling back to %s.", magnification)
    pixel_size = PIXEL_SIZE_SAMPLE_PLANE / magnification

    if not options.pitch:
        try:
            mppc = model.getComponent(role="mppc")
            mppc_md = mppc.getMetadata()
            pitch = mppc_md.get(model.MD_CALIB, {}).get("pitch", 3.2e-6)
        except Exception as ex:
            logging.debug("Failed to read pitch from mppc, ex: %s", ex)
            pitch = DEFAULT_PITCH
    else:
        pitch = options.pitch

    # 0.75 is a safety factor to allow for some variation in spot positions
    min_dist_spots = int(0.75 * pitch / pixel_size)

    if options.filename:
        logging.info("Will process image file %s" % options.filename)
        converter = dataio.find_fittest_converter(options.filename, default=None, mode=os.O_RDONLY)
        data = converter.read_data(options.filename)[0]
        fakeccd = StaticCCD(options.filename, "fakeccd", data)
        live_display(fakeccd, fakeccd.data, pixel_size, gridsize=options.gridsize, min_dist_spots=min_dist_spots)
    elif options.role:
        if get_backend_status() != BACKEND_RUNNING:
            raise ValueError("Backend is not running while role command is specified.")
        ccd = model.getComponent(role=options.role)
        live_display(ccd, ccd.data, pixel_size, kill_ccd=False, gridsize=options.gridsize,
                     min_dist_spots=min_dist_spots)
    else:
        ccd = ueye.Camera("camera", "ccd", device=None)
        ccd.SetFrameRate(2)
        live_display(ccd, ccd.data, pixel_size, gridsize=options.gridsize, min_dist_spots=min_dist_spots)
    return 0


if __name__ == "__main__":
    main(sys.argv)
