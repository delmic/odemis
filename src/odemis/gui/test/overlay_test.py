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

class OverlayTestCase(test.GuiTestCase):

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

    def xtest_view_select_overlay(self):
        # Create and add a miccanvas
        cnvs = miccanvas.SecomCanvas(self.panel)

        cnvs.SetBackgroundColour(wx.WHITE)
        cnvs.SetForegroundColour("#DDDDDD")
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        vsol = overlay.ViewSelectOverlay(cnvs, "test selection")
        cnvs.WorldOverlays.append(vsol)
        cnvs.active_overlay = vsol
        cnvs.current_mode = model.TOOL_ROI

        # FIXME: this overlay seems to not work (black selection?)
        # but as for now we don't use it, no need to fix it yet...


    def test_roa_select_overlay(self):
        # Create and add a miccanvas
        # TODO: Sparc canvas because it's now the only one which supports TOOL_ROA
        # but it should be a simple miccanvas
        cnvs = miccanvas.SparcAcquiCanvas(self.panel)

        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        rsol = overlay.RepetitionSelectOverlay(cnvs, "Region of acquisition")
        cnvs.WorldOverlays.append(rsol)
        cnvs.active_overlay = rsol
        cnvs.current_mode = model.TOOL_ROA

        gui_loop(SLEEP_TIME)
        wroi = [-0.1, 0.3, 0.2, 0.4] # in m
        rsol.set_physical_sel(wroi)
        gui_loop(SLEEP_TIME)
        wroi_back = rsol.get_physical_sel()
        for o, b in zip(wroi, wroi_back):
            self.assertAlmostEqual(o, b,
                       msg="wroi (%s) != bak (%s)" % (wroi, wroi_back))

        rsol.set_repetition((3, 2))
        rsol.grid_fill()
        gui_loop(SLEEP_TIME)

#        rsol.set_repetition((4, 5))
#        rsol.point_fill()
#        gui_loop(SLEEP_TIME)

    def xtest_dichotomy_overlay(self):
        cnvs = miccanvas.SecomCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        lva = omodel.ListVA()

        dol = overlay.DichotomyOverlay(cnvs, lva)
        cnvs.ViewOverlays.append(dol)

        dol.sequence_va.subscribe(do_stuff, init=True)
        dol.enable()

        dol.sequence_va.value = [0, 1, 2, 3, 0]

    def xtest_spot_mode_overlay(self):
        cnvs = miccanvas.SecomCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        sol = overlay.SpotModeOverlay(cnvs)
        cnvs.ViewOverlays.append(sol)


if __name__ == "__main__":
    unittest.main()
