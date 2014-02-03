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

from odemis.gui.comp.overlay import view as vol
from odemis.gui.comp.overlay import world as wol
import logging
import odemis.gui.comp.miccanvas as miccanvas
import odemis.gui.comp.canvas as canvas
import odemis.gui.test as test
import odemis.gui.model as gmodel
import odemis.model as omodel
import unittest
import wx

test.goto_manual()
logging.getLogger().setLevel(logging.DEBUG)
# test.set_sleep_time(1000)


def do_stuff(value):
    """ Test function that can be used to subscribe to VAs """
    print "value", value

class OverlayTestCase(test.GuiTestCase):

    frame_class = test.test_gui.xrccanvas_frame

    def test_text_view_overlay_size(self):
        cnvs = canvas.BitmapCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        ol = vol.TextViewOverlay(cnvs)
        cnvs.view_overlays.append(ol)

        for f in (False, True):
            msg = "TextViewOverlay sizes test {} flip"
            size = 0
            y = 0
            for i in range(10):
                y += 12 + size
                size = 10 + i * 3
                ol.add_label(msg.format("with" if f else "without") ,
                             font_size=size, pos=(0, y), flip=f)
                test.gui_loop(50)

            ol.clear()

    def test_text_view_overlay_align(self):
        cnvs = canvas.BitmapCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        ol = vol.TextViewOverlay(cnvs)
        cnvs.view_overlays.append(ol)

        ol.add_label("TextViewOverlay left",
                     pos=(ol.view_width / 2, 10))
        test.gui_loop(50)

        ol.add_label("TextViewOverlay right",
                     pos=(ol.view_width / 2, 26),
                     align=wx.ALIGN_RIGHT)
        test.gui_loop(50)

        ol.add_label("TextViewOverlay center",
                     pos=(ol.view_width / 2, 42),
                          align=wx.ALIGN_CENTER)
        test.gui_loop(50)

        ol.add_label("|",
                     pos=(ol.view_width / 2, 58),
                     align=wx.ALIGN_CENTER)
        ol.add_label("|",
                     pos=(ol.view_width / 2, 74),
                     align=wx.ALIGN_CENTER)
        ol.add_label("Relative to the center",
                     pos=(ol.view_width / 2, 90),
                     align=wx.ALIGN_CENTER)
        test.gui_loop(50)

        # Example on how a right aligned label can be kept on the right on resize
        def realign(evt):
            for label in ol.labels:
                label.pos = (ol.view_width / 2, label.pos[1])
            evt.Skip()
        cnvs.Bind(wx.EVT_SIZE, realign)

        ol.canvas_padding = 0

        ol.add_label("top left",
                     pos=(0, 0),
                     align=wx.ALIGN_LEFT)
        test.gui_loop(50)

        ol.add_label("top right",
                     pos=(ol.view_width, 0),
                     align=wx.ALIGN_RIGHT)
        test.gui_loop(50)

        ol.add_label("bottom left",
                     pos=(0, ol.view_height),
                     align=wx.ALIGN_BOTTOM)
        test.gui_loop(50)

        ol.add_label("bottom right",
                     pos=(ol.view_width, ol.view_height),
                     align=wx.ALIGN_RIGHT|wx.ALIGN_BOTTOM,
                     flip=False)
        test.gui_loop(50)

        ol.add_label("SHOULD NOT BE SEEN!",
                     pos=(ol.view_width, ol.view_height / 2),
                     align=wx.ALIGN_LEFT,
                     flip=False)
        test.gui_loop(50)

        ol.add_label("Visible because of flip",
                     pos=(ol.view_width, ol.view_height / 2),
                     align=wx.ALIGN_LEFT,
                     flip=True)
        test.gui_loop(50)


    def test_text_view_overlay_rotate(self):
        cnvs = canvas.BitmapCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)
        ol = vol.TextViewOverlay(cnvs)
        ol.canvas_padding = 0
        cnvs.view_overlays.append(ol)

        # Text should exactly overlap

        rl = ol.add_label(u"█ you should only see red",
                          pos=(0, 0),
                          font_size=20,
                          deg=0,
                          flip=False)
        test.gui_loop(50)

        sl = ol.add_label(u"█ you should only see red",
                          pos=(0, 0),
                          font_size=20,
                          colour=(1, 0, 0),
                          align=wx.ALIGN_LEFT)

        test.gui_loop(500)
        self.assertEqual(rl.render_pos, sl.render_pos)

        ol.clear()



        sl = ol.add_label(u"█ no rotate",
                          pos=(0, 0),
                          font_size=20,
                          colour=(1, 0, 0),
                          align=wx.ALIGN_LEFT)

        tl = ol.add_label(u"█ rotate",
                          pos=(140, 0),
                          font_size=20,
                          deg=0,
                          flip=False)

        tr = ol.add_label(u"rotate █",
                          pos=(ol.view_width, 0),
                          font_size=20,
                          align=wx.ALIGN_RIGHT,
                          deg=0,
                          flip=False)



        def _animate(evt):
            angle_step = 30
            for i in range(390 // angle_step):
                tl.deg = i * angle_step
                tr.deg = i * angle_step
                test.gui_loop(100)
                cnvs.Refresh()
            evt.Skip()

        cnvs.Bind(wx.EVT_LEFT_UP, _animate)


    # @unittest.skip("simple")
    def test_polar_overlay(self):
        cnvs = miccanvas.AngularResolvedCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        test.gui_loop()

        test.sleep(1000)

        cnvs.polar_overlay.phi_deg = 60
        cnvs.polar_overlay.theta_deg = 60

    # @unittest.skip("simple")
    def test_points_select_overlay(self):
        # Create stuff
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        mmodel = test.FakeMicroscopeModel()
        view = mmodel.focussedView.value
        cnvs.setView(view, mmodel)

        # Manually add the overlay
        pol = overlay.PointsOverlay(cnvs)
        cnvs.world_overlays.append(pol)
        cnvs.active_overlay = pol

        cnvs.current_mode = gmodel.TOOL_POINT
        pol.enable(True)

        from itertools import product

        # phys_points = product(xrange(-1000, 1001,bbbb 50), xrange(-1000, 1001, 50))
        phys_points = product(xrange(-200, 201, 50), xrange(-200, 201, 50))
        phys_points = [(a / 1.0e5, b / 1.0e5) for a, b in phys_points]

        point = omodel.VAEnumerated(
                    phys_points[0],
                    choices=frozenset(phys_points))

        pol.set_point(point)


        test.gui_loop()
        # test.sleep(1000)

        view.mpp.value = view.mpp.value
        test.gui_loop()

    # @unittest.skip("simple")
    def test_pixel_select_overlay(self):
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        cnvs.current_mode = gmodel.TOOL_POINT
        mmodel = test.FakeMicroscopeModel()
        view = mmodel.focussedView.value
        cnvs.setView(view, mmodel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)
        cnvs.current_mode = gmodel.TOOL_POINT


        psol = overlay.PixelSelectOverlay(cnvs)
        psol.enabled = True
        cnvs.world_overlays.append(psol)
        cnvs.active_overlay = psol

        # psol.set_values(33, (0.0, 0.0), (30, 30))
        psol.set_values(1e-05, (0.0, 0.0), (17, 19), omodel.TupleVA())
        view.mpp.value = 1e-06
        test.gui_loop()

    # @unittest.skip("simple")
    def test_view_select_overlay(self):
        # Create and add a miccanvas
        cnvs = miccanvas.SecomCanvas(self.panel)

        cnvs.SetBackgroundColour(wx.WHITE)
        cnvs.SetForegroundColour("#DDDDDD")
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        vsol = overlay.ViewSelectOverlay(cnvs, "test selection")
        cnvs.view_overlays.append(vsol)
        cnvs.active_overlay = vsol
        cnvs.current_mode = miccanvas.MODE_SECOM_ZOOM

    # @unittest.skip("simple")
    def test_roa_select_overlay(self):
        # Create and add a miccanvas
        # TODO: Sparc canvas because it's now the only one which supports
        # TOOL_ROA
        # but it should be a simple miccanvas
        cnvs = miccanvas.SparcAcquiCanvas(self.panel)

        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        rsol = overlay.RepetitionSelectOverlay(cnvs, "Region of acquisition")
        cnvs.world_overlays.append(rsol)
        cnvs.active_overlay = rsol
        cnvs.current_mode = gmodel.TOOL_ROA

        test.gui_loop()
        wroi = [-0.1, 0.3, 0.2, 0.4] # in m
        rsol.set_physical_sel(wroi)
        test.gui_loop()
        wroi_back = rsol.get_physical_sel()
        for o, b in zip(wroi, wroi_back):
            self.assertAlmostEqual(o, b,
                       msg="wroi (%s) != bak (%s)" % (wroi, wroi_back))

        rsol.repetition = (3, 2)
        rsol.fill = overlay.FILL_GRID
        test.gui_loop()

        rsol.repetition  = (4, 5)
        rsol.fill = overlay.FILL_POINT
        test.gui_loop()

    # @unittest.skip("simple")
    def test_dichotomy_overlay(self):
        cnvs = miccanvas.SecomCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        lva = omodel.ListVA()

        dol = overlay.DichotomyOverlay(cnvs, lva)
        cnvs.view_overlays.append(dol)
        cnvs.active_overlay = dol

        dol.sequence_va.subscribe(do_stuff, init=True)
        dol.enable()

        dol.sequence_va.value = [0, 1, 2, 3, 0]

        test.gui_loop()

    # @unittest.skip("simple")
    def test_spot_mode_overlay(self):
        cnvs = miccanvas.SecomCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        sol = overlay.SpotModeOverlay(cnvs)
        cnvs.view_overlays.append(sol)

        test.gui_loop()



if __name__ == "__main__":
    #unittest.main()

    suit = unittest.TestSuite()
    # suit.addTest(OverlayTestCase("test_text_view_overlay_size") )
    # suit.addTest(OverlayTestCase("test_text_view_overlay_align") )
    suit.addTest(OverlayTestCase("test_text_view_overlay_rotate") )
    runner = unittest.TextTestRunner()
    runner.run(suit)
