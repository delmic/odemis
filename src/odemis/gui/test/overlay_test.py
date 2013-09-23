#-*- coding: utf-8 -*-

"""
:author: Rinze de Laat
:copyright: © 2013 Rinze de Laat, Delmic

.. license::

    This file is part of Odemis.

    Odemis is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License version 2 as published by the Free
    Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
    FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
    details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""

#===============================================================================
# Test module for Odemis' gui.comp.overlay module
#===============================================================================

from odemis.gui import model
from odemis.gui.test import MANUAL, INSPECT, SLEEP_TIME, gui_loop
from odemis.gui.xmlh import odemis_get_test_resources
import logging
import odemis.gui.comp.miccanvas as miccanvas
import odemis.gui.comp.overlay as overlay
import odemis.gui.test as test
import odemis.gui.test.test_gui
import odemis.model as omodel
import unittest
import wx


MANUAL = True
logging.getLogger().setLevel(logging.DEBUG)

# test.goto_manual() # Keep the test frame open after the tests are run

class TestApp(wx.App):
    def __init__(self):
        odemis.gui.test.test_gui.get_resources = odemis_get_test_resources
        self.test_frame = None
        wx.App.__init__(self, redirect=False)

    def OnInit(self):
        self.test_frame = odemis.gui.test.test_gui.xrccanvas_frame(None)
        self.test_frame.SetSize((400, 400))
        self.test_frame.Center()
        self.test_frame.Layout()
        self.test_frame.Show()

        return True

def do_stuff(sequence):
    print "New sequence", sequence

class PlotCanvasTestCase(test.GuiTestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = TestApp()
        cls.panel = cls.app.test_frame.canvas_panel
        cls.sizer = cls.panel.GetSizer()

        # NOTE!: Call Layout on the panel here, because otherwise the
        # controls layed out using XRC will not have the right sizes!
        gui_loop()

    @classmethod
    def tearDownClass(cls):
        if not MANUAL:
            wx.CallAfter(cls.app.Exit)
        else:
            if INSPECT:
                from wx.lib import inspection
                inspection.InspectionTool().Show()
            cls.app.MainLoop()

    def test_view_select_overlay(self):
        # Create and add a test plot canvas
        # cnvs = canvas.PlotCanvas(self.panel)
        cnvs = miccanvas.SecomCanvas(self.panel)
        # TODO: setView() with a MicroscopeView

        cnvs.SetBackgroundColour(wx.BLACK)
        cnvs.SetForegroundColour("#DDDDDD")
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        vsol = overlay.ViewSelectOverlay(cnvs, "test selection")
        cnvs.WorldOverlays.append(vsol)
        cnvs.active_overlay = vsol
        cnvs.current_mode = model.TOOL_ROI

    def test_dichotomy_overlay(self):
        cnvs = miccanvas.SecomCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        lva = omodel.ListVA()

        dol = overlay.DichotomyOverlay(cnvs, lva)
        cnvs.ViewOverlays.append(dol)

        dol.sequence_va.subscribe(do_stuff, init=True)
        dol.enable()

        dol.sequence_va.value = [0, 1, 2, 3, 0]

    def test_spot_mode_overlay(self):
        cnvs = miccanvas.SecomCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        sol = overlay.SpotModeOverlay(cnvs)
        cnvs.ViewOverlays.append(sol)


if __name__ == "__main__":
    unittest.main()
