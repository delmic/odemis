#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Created on 13 okt 2019

@author: Thera Pals

Copyright © 2019 Thera Pals, Delmic

This script provides a command line interface for displaying a video with a sine wave overlay.

It implements the third step of the work instruction "DeScan galvo gain phase matching".
Create an oscilloscope trace on the diagnostic camera. This will look like a sine wave on the diagnostic camera.
Digitize the trace and determine the amplitude of the sine. This amplitude is the (residual) error of the DeScan-Y.
The amplitude will be displayed on the video, minimize the amplitude.

"""
from __future__ import division, print_function

import argparse
import logging
import sys
import threading

import numpy
import wx
import wx.lib.wxcairo  # should be imported before cairo
import cairo

from scipy import optimize
from skimage.filters import gaussian

from odemis import model
from odemis.cli.video_displayer import VideoDisplayer
from odemis.driver import ueye
from odemis.util.driver import get_backend_status, BACKEND_RUNNING

PIXEL_SIZE_UM = 3.45 / 50.


class VideoDisplayerSine(VideoDisplayer):
    """
    Very simple display for a continuous flow of images as a window with an overlay of a sine wave.
    It should be pretty much platform independent.
    """

    def __init__(self, title="Live image", size=(640, 480)):
        """
        Displays the window on the screen
        title: str
            Title of the window.
        size: 2-tuple int,int
            X and Y size of the window at initialisation.
            Note that the size of the window automatically adapts afterwards to the
            last received image.
        """
        self.img = None
        self.available = threading.Event()
        self.display = True
        self.app = ImageWindowApp(title, size)

        t = threading.Thread(target=self.image_update)
        t.daemon = True
        t.start()

    def new_image(self, data):
        """
        Update the window with the new image and fit a sine to the image.
        This overwrites new_image of the VideoDisplayer class.
        data: numpy.ndarray
            A 2D array containing the image (can be 3D if in RGB)
        """
        self.app.params, self.app.cols = fit_sine_to_image(data)
        super(VideoDisplayerSine, self).new_image(data)

    def store_new_image(self, image):
        """Store new image for next time you have time to process it"""
        self.img = image
        self.available.set()

    def image_update(self):
        """
        Update the image in the video displayer.

        """
        try:
            while self.display:
                self.available.wait()
                self.available.clear()
                if not self.display:
                    return
                self.new_image(self.img)
        except Exception:
            logging.exception("Failure during display")
        finally:
            logging.debug("Display thread ended")

    def waitQuit(self):
        super(VideoDisplayerSine, self).waitQuit()
        self.display = False
        self.available.set()  # Force the thread to check the .display flag


class ImageWindowApp(wx.App):
    """wx application that shows the window with an image."""

    def __init__(self, title, size):
        """
        Parameters
        ----------
        title: str
            Title of the window.
        size: tuple
            Initial size of the window.
        """
        wx.App.__init__(self, redirect=False)
        self.AppName = "Galvo Calibration CLI"
        self.frame = wx.Frame(None, title=title, size=size)

        self.panel = wx.Panel(self.frame)
        self.panel.Bind(wx.EVT_KEY_DOWN, self.OnKey)
        # just in case panel doesn't have the focus: also on the frame
        # (but it seems in Linux (GTK) frames don't receive key events anyway
        self.frame.Bind(wx.EVT_KEY_DOWN, self.OnKey)

        self.img = wx.Image(*size, clear=True)
        self.imageCtrl = wx.StaticBitmap(self.panel, wx.ID_ANY, wx.Bitmap(self.img))
        self.imageCtrlSine = wx.StaticBitmap(self.panel, wx.ID_ANY, wx.Bitmap(self.img))
        self.imageCtrlText = wx.StaticBitmap(self.panel, wx.ID_ANY, wx.Bitmap(self.img))
        self.panel.SetFocus()
        self.frame.Show()

    def update_view(self):
        """Update view with image and sine/text overlay when a new image is received."""
        logging.debug("Received a new image of %d x %d", *self.img.GetSize())
        self.frame.ClientSize = self.img.GetSize()

        height = self.img.Height
        width = self.img.Width
        point_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        ctx = cairo.Context(point_surface)
        ctx.set_source_rgba(0.5, 0.1, 0.1, 0.7)  # r, g, b, alpha
        ctx.scale(width, height)
        cols = numpy.arange(min(self.cols), max(self.cols))
        sin_y = _sine_wave(cols, *self.params) * self.magn / height
        sin_x = cols * self.magn / width
        sine = numpy.vstack((sin_x, sin_y)).T
        point_temp = numpy.array([0, 0])
        for point in sine:
            ctx.translate(*point_temp)  # translate back to the origin since point_temp is negative
            ctx.translate(*point)  # translate from the origin to the coordinate of the point
            point_temp = numpy.copy(-point)
            ctx.arc(0, 0, 0.0025, 0, 2 * numpy.pi)
            ctx.fill()
        text_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        info = [
            # TODO add voltages when incorporated with AWG's
            "E-beam scanner: {} V".format("N/A"),
            "Galvo scanner: {} V".format("N/A"),
            "Amplitude: {:.2f} um".format(PIXEL_SIZE_UM * numpy.abs(self.params[1])),
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

        self.imageCtrl.SetBitmap(wx.Bitmap(self.img))
        self.imageCtrlSine.SetBitmap(wx.lib.wxcairo.BitmapFromImageSurface(point_surface))
        self.imageCtrlText.SetBitmap(wx.lib.wxcairo.BitmapFromImageSurface(text_surface))

    def OnKey(self, event):
        """Destroy the frame when the user presses q or Q."""
        key = event.GetKeyCode()
        if key in [ord("q"), ord("Q")]:
            self.frame.Destroy()

        # everything else we don't process
        event.Skip()


def live_display(ccd, dataflow, kill_ccd=True):
    """
    Acquire an image from one (or more) dataflow(s) and display it with a fitted sine wave overlay.

    Parameters
    ----------
    ccd: odemis.model.DigitalCamera
        A camera object.
    dataflow: odemis.model.DataFlow
        Dataflow object to access.
    kill_ccd: bool
        True if it is required to terminate the ccd after closing the window.
    """
    # create a window
    window = VideoDisplayerSine("Live from %s.%s" % (ccd.role, "data"), ccd.resolution.value)

    def new_image_wrapper(df, image):
        window.store_new_image(image)

    try:
        dataflow.subscribe(new_image_wrapper)
        # wait until the window is closed
        window.waitQuit()
    finally:
        dataflow.unsubscribe(new_image_wrapper)
        if kill_ccd:
            ccd.terminate()


def fit_sine_to_image(image):
    """
    Fit a sine wave to a image containing a sine somewhere in the image.

    Parameters
    ----------
    image: ndarray of shape nxm
        Grayscale image with an oscilloscope like trace of a sine wave.

    Returns
    -------
    params: ndarray
        Optimal values for the frequency, amplitude, phase and offset of the sine wave.
    cols: ndarray
        x-coordinates of the location of the sine wave in the image.
    """
    # Filter out background noise.
    image = gaussian(image, 1)
    # Threshold at 0.5 of the max of the image, to filter out the smooth edges of the sine.
    image_thresh = image >= 0.5 * numpy.max(image)
    # Get coordinates where the image is above the threshold.
    rows, cols = numpy.where(image_thresh)
    rows = numpy.array([rows for _, rows in sorted(zip(cols, rows))])
    cols = numpy.sort(cols)
    # Make a guess for the parameters of the sine wave.
    # Values chosen to give a good guess given the instructions in the work instruction.
    guess_freq = 0.05
    guess_amplitude = 3 * numpy.std(rows) / (2 ** 0.5)
    guess_phase = 0.5
    guess_offset = numpy.mean(rows)
    p0 = [guess_freq, guess_amplitude, guess_phase, guess_offset]
    # Fit a sine through the coordinates.
    params, _ = optimize.curve_fit(_sine_wave, cols, rows, p0=p0)
    return params, cols


def _sine_wave(x, freq, amplitude, phase, offset):
    """
    Calculate the y-coordinates of a sine wave, given the x-coordinates, frequency, amplitude, phase and offset.

    Parameters
    ----------
    x: array, x-coordinates of the location of the sine wave in the image.
    freq: float, frequency of the sine wave.
    amplitude: float, amplitude of the sine wave.
    phase: float, phase of the sine wave.
    offset: float, offset of the sine wave.

    Returns
    -------
    ndarray, y-coordinates of the sine wave.
    """
    return numpy.sin(x * freq + phase) * amplitude + offset


def main(args):
    """
    Handles the command line arguments.

    Parameters
    ----------
    args: The list of arguments passed.

    Returns
    -------
    int
        Value to return to the OS as program exit code.
    """
    # arguments handling
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", dest="role", metavar="<component>",
                        help="Role of the camera to connect to via the Odemis back-end. E.g.: 'ccd'.")
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
        live_display(ccd, ccd.data, kill_ccd=False)
    else:
        ccd = ueye.Camera("camera", "ccd", device=None)
        live_display(ccd, ccd.data)
    return 0


if __name__ == '__main__':
    main(sys.argv)
