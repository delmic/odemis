# -*- coding: utf-8 -*-
"""
Created on 1 Jul 2013

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""
import logging
import os
import os.path
import random
import time
import unittest
from builtins import range
from unittest.mock import patch

import numpy
import wx

import odemis.gui.model as gmodel
from odemis import model
from odemis.acq.move import FM_IMAGING, MeteorTFS3PostureManager
from odemis.driver.tmcm import TMCLController
from odemis.gui.model import MainGUIData
from odemis.gui.xmlh import odemis_get_test_resources
from . import test_gui

# Common configuration and code for the GUI test cases
MANUAL = False
INSPECT = False

def goto_manual():
    """ Call this function as soon as possible, to go to manual mode, where
    the test GUI will stay open after finishing the test case. """
    global MANUAL
    MANUAL = False if os.environ.get('NOMANUAL') == '1' else True


def goto_inspect():
    global INSPECT
    INSPECT = True


def gui_loop(slp=0):
    """
    Execute the main loop for the GUI until all the current events are processed
    slp (0<=float): time to wait (s)
    """
    start = time.time()
    app = wx.GetApp()
    if app is None:
        return

    while True:
        wx.CallAfter(app.ExitMainLoop)
        app.MainLoop()

        if time.time() > (start + slp):
            break


def sleep(ms):
    wx.MilliSleep(ms)


def set_log_level(level=logging.DEBUG):
    logging.getLogger().setLevel(level)


# Default wxPython App that can be used as a basis for testing
class GuiTestApp(wx.App):

    test_frame = None

    def __init__(self, frame):
        test_gui.get_resources = odemis_get_test_resources
        self.test_frame = frame
        self.module_name = ""

        self.main_data = MainGUIData(None)

        # gen_test_data()
        wx.App.__init__(self, redirect=False)

    # In wxPython4, it seems to always be called, and that causes logging of all events received during the tests
    # def FilterEvent(self, evt):
    #     print evt
    #     return -1

    def OnInit(self):
        self.test_frame = self.test_frame(None)  # odemis.gui.test.test_gui.xrccanvas_frame(None)
        self.main_frame = self.test_frame  # Just for compatibility with the real app

        # Process menu items if any
        menu_bar = self.test_frame.GetMenuBar()
        if menu_bar:

            for item in menu_bar.GetMenu(0).GetMenuItems():

                if item.ItemLabelText == "Inspect":
                    def inspect(event):
                        from wx.lib import inspection
                        inspection.InspectionTool().Show()

                    self.test_frame.Bind(wx.EVT_MENU, inspect, id=item.GetId())
                elif item.ItemLabelText == "Quit":
                    def close(event):
                        self.test_frame.Close()

                    self.test_frame.Bind(wx.EVT_MENU, close, id=item.GetId())

        import __main__
        self.module_name = os.path.basename(__main__.__file__)
        self.test_frame.SetTitle(self.module_name)

        self.test_frame.Show()

        return True

    def panel_finder(self, win=None):
        """ Find the first child panel of win """

        win = win or self.test_frame

        for c in win.GetChildren():
            if isinstance(c, wx.Panel):
                return c
            else:
                return self.panel_finder(c)
        return None


# TestCase base class, with GuiTestApp support
class GuiTestCase(unittest.TestCase):

    frame_class = None
    app_class = None
    frame_size = (400, 400)

    @classmethod
    def setUpClass(cls):
        if not cls.frame_class:
            raise ValueError("No frame_class set!")
        cls.app_class = cls.app_class or GuiTestApp
        cls.app = cls.app_class(cls.frame_class)
        # Enable event filter, so they are printed to console
        # cls.app.SetCallFilterEvent(True)
        cls.frame = cls.app.test_frame
        cls.frame.SetSize(cls.frame_size)
        cls.frame.Center()
        cls.frame.Layout()
        cls.panel = cls.app.panel_finder(cls.app.test_frame)
        cls.sizer = cls.panel.GetSizer()

        # NOTE!: Call Layout on the panel here, because otherwise the
        # controls laid out using XRC will not have the right sizes!
        gui_loop()

    @classmethod
    def tearDownClass(cls):
        if not MANUAL:
            cls.app.test_frame.Destroy()
        elif INSPECT:
            from wx.lib import inspection
            inspection.InspectionTool().Show()

        cls.app.MainLoop()
        del cls.app  # Makes sure everything is cleaned up (before starting a new app)

    def setUp(self):
        self.app.test_frame.SetTitle(
            "%s > %s" % (self.app.module_name, self._testMethodName))

    @classmethod
    def add_control(cls, ctrl, flags=0, border=10, proportion=0, clear=False, label=None):
        if clear:
            cls.remove_all()

        flags = flags or wx.ALL

        if label is not None:
            lbl = wx.StaticText(ctrl.Parent, -1, label)
            bs = wx.BoxSizer()
            bs.Add(lbl, proportion=0, flag=wx.RIGHT, border=10)
            bs.Add(ctrl, proportion=-1, flag=wx.EXPAND)
            cls.sizer.Add(bs, flag=flags, border=border, proportion=proportion)
        else:
            cls.sizer.Add(ctrl, flag=flags, border=border, proportion=proportion)
        cls.sizer.Layout()
        return ctrl

    @classmethod
    def remove_all(cls):
        cls.sizer.Clear(True)
        cls.sizer.Layout()

    def create_simple_tab_model(self):
        main = gmodel.MainGUIData(None)  # no microscope backend
        tab = gmodel.MicroscopyGUIData(main)

        # Add one view
        fview = gmodel.MicroscopeView("fakeview")
        tab.views.value.append(fview)
        tab.focussedView.value = fview

        return tab

    def create_cryo_tab_model(self):
        stage = TMCLController(name="Stage",
                               role="stage-bare",
                               port="/dev/fake6",
                               axes=["x", "y", "z", "rx", "rz"],
                               ustepsize=[1e-6, 1e-6, 1e-6, 1e-6, 1e-6],
                               rng=[[-6, 6], [-6, 6], [-6, 6], [-6, 6], [-6, 6]],
                               refproc="Standard")

        stage.updateMetadata({
            model.MD_CALIB: {
                "version": "tfs_3",
                "dx": 0.0506252, "dy": 0.0049832,  # mirroring values between SEM - METEOR
                "pre-tilt": 0.6108652381980153,  # rad, 35°
            },
            model.MD_SAMPLE_CENTERS: {"GRID 1": {'x': 0, 'y': 0, 'z': 0},
                                      "GRID 2": {'x': 2.98e-3, 'y': 2.46e-3, 'z': 0}},
            model.MD_FAV_FM_POS_ACTIVE: {"rx": 0.12213888553625313, "rz": 5.06145},
            model.MD_FAV_SEM_POS_ACTIVE: {"rx": 0, "rz": 0},
            model.MD_FAV_POS_DEACTIVE: {'rx': 0, 'rz': 1.9076449, 'x': -0.01529, 'y': 0.0506, 'z': 0.01975},
            model.MD_SEM_IMAGING_RANGE: {"x": [-10.e-3, 10.e-3], "y": [-5.e-3, 10.e-3], "z": [-0.5e-3, 8.e-3]},
            model.MD_FM_IMAGING_RANGE: {"x": [0.040, 0.054], "y": [-5.e-3, 10.e-3], "z": [-0.5e-3, 8.e-3]},
        })
        focus = TMCLController(name="Focuser", role="focus",
                               port="/dev/fake3",
                               axes=["z"],
                               ustepsize=[1e-6],
                               rng=[[-3e-3, 3e-3]],
                               refproc="Standard")

        light = model.HwComponent(name="Light", role="light")
        ccd = model.HwComponent(name="Camera", role="ccd")

        class FakeMicroscope:
            """Mock class to simulate a microscope with a stage and focus controller without running the backend"""

            def __init__(self):
                self.name = "FakeMicroscope"
                self.role = "meteor"
                self.alive = model.ListVA([focus, stage])

        def getComponents():
            return {stage, focus, light, ccd}

        def getComponent(name=None, role=None):
            for c in getComponents():
                if name is not None and c.name != name:
                    continue
                if role is not None and c.role != role:
                    continue
                return c
            raise KeyError(name or role)

        microscope = FakeMicroscope()
        # Mock getMicroscope to return FakeMicroscope
        with patch('odemis.model.getComponent', side_effect=getComponent),\
             patch('odemis.model.getComponents', side_effect=getComponents):
            main = gmodel.CryoMainGUIData(microscope)
            main.posture_manager = MeteorTFS3PostureManager(microscope)

        # add role, features and currentFeature directly
        main.role = "meteor"
        main.features = model.ListVA()
        main.currentFeature = model.VigilantAttribute(None)

        tab = gmodel.CryoGUIData(main)

        # Add one view
        fview = gmodel.MicroscopeView("fakeview")
        tab.views.value.append(fview)
        tab.view_posture = model.VigilantAttribute(FM_IMAGING)
        tab.focussedView.value = fview

        return tab


# Dummy classes for testing purposes

class Object(object):
    pass


class FakeMicroscopeModel(object):
    """
    Imitates a MicroscopeModel wrt stream entry: it just needs a focussedView
    """
    def __init__(self):
        fview = gmodel.MicroscopeView("fakeview")
        self.focussedView = model.VigilantAttribute(fview)

        self.main = Object()
        self.main.light = None
        self.main.ebeam = None
        self.main.debug = model.VigilantAttribute(fview)
        self.focussedView = model.VigilantAttribute(fview)

        self.light = None
        self.light_filter = None
        self.ccd = None
        self.sed = None
        self.ebeam = None
        self.tool = None
        self.subscribe = None


# Utility functions
def set_img_meta(img, pixel_size, pos):
    img.metadata[model.MD_PIXEL_SIZE] = pixel_size
    img.metadata[model.MD_POS] = pos


def generate_img_data(width, height, depth, alpha=255, color=None):
    """ Create an image of the given dimensions

    :type width: int
    :type height: int
    :type depth: int
    :type alpha: int
    :param color: (int, int, int) If a color is defined, that color will be used to fill the image

    """

    shape = (height, width, depth)
    rgb = numpy.empty(shape, dtype=numpy.uint8)

    if width > 100 or height > 100:
        if color:
            color += (alpha,)
            tl = color
            tr = color
            bl = color
            br = color
        else:
            tl = random_color(alpha=alpha)
            tr = random_color(alpha=alpha)
            bl = random_color(alpha=alpha)
            br = random_color(alpha=alpha)

        rgb = numpy.zeros(shape, dtype=numpy.uint8)

        rgb[..., -1, 0] = numpy.linspace(tr[0], br[0], height)
        rgb[..., -1, 1] = numpy.linspace(tr[1], br[1], height)
        rgb[..., -1, 2] = numpy.linspace(tr[2], br[2], height)

        rgb[..., 0, 0] = numpy.linspace(tl[0], bl[0], height)
        rgb[..., 0, 1] = numpy.linspace(tl[1], bl[1], height)
        rgb[..., 0, 2] = numpy.linspace(tl[2], bl[2], height)

        for i in range(height):
            sr, sg, sb = rgb[i, 0, :3]
            er, eg, eb = rgb[i, -1, :3]

            rgb[i, :, 0] = numpy.linspace(int(sr), int(er), width)
            rgb[i, :, 1] = numpy.linspace(int(sg), int(eg), width)
            rgb[i, :, 2] = numpy.linspace(int(sb), int(eb), width)

        if depth == 4:
            rgb[..., 3] = min(255, max(alpha, 0))

    else:
        for w in range(width):
            for h in range(height):
                if color:
                    rgb[h, w] = color + (alpha,)
                else:
                    rgb[h, w] = random_color((230, 230, 255), alpha)

    return model.DataArray(rgb)


def random_color(mix_color=None, alpha=255):
    """ Generate a random color, possibly tinted using mix_color """

    red = random.randint(0, 255)
    green = random.randint(0, 255)
    blue = random.randint(0, 255)

    if mix_color:
        red = (red - mix_color[0]) / 2
        green = (green - mix_color[1]) / 2
        blue = (blue - mix_color[2]) / 2

    a = alpha / 255.0

    return red * a, green * a, blue * a, alpha
