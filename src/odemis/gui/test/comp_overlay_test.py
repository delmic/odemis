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

import math
from odemis.gui.cont.tools import TOOL_LINE
from odemis.gui.model import TOOL_POINT

from odemis.util.conversion import hex_to_frgb
from odemis.gui.comp.overlay import view as vol
from odemis.gui.comp.overlay import world as wol
import logging
import odemis.gui as gui
import odemis.gui.comp.miccanvas as miccanvas
import odemis.gui.comp.canvas as canvas
import odemis.gui.test as test
import odemis.gui.model as guimodel
import odemis.model as omodel
import unittest
import wx


test.goto_manual()
# logging.getLogger().setLevel(logging.DEBUG)
# test.set_sleep_time(1000)


class OverlayTestCase(test.GuiTestCase):

    frame_class = test.test_gui.xrccanvas_frame

    # View overlay test cases

    def test_text_view_overlay_size(self):
        cnvs = canvas.BitmapCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        ol = vol.TextViewOverlay(cnvs)
        cnvs.add_view_overlay(ol)

        for f in (False, True):
            msg = "TextViewOverlay sizes test {} flip"
            size = 0
            y = 0
            for i in range(10):
                y += 12 + size
                size = 10 + i * 3
                ol.add_label(msg.format("with" if f else "without"),
                             font_size=size, pos=(0, y), flip=f,
                             colour=hex_to_frgb(gui.FG_COLOUR_EDIT))
                test.gui_loop(50)

            ol.clear_labels()

    def test_text_view_overlay_align(self):
        cnvs = canvas.BitmapCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        ol = vol.TextViewOverlay(cnvs)
        cnvs.add_view_overlay(ol)

        ol.add_label("TextViewOverlay left",
                     pos=(ol.view_width / 2, 10),
                     colour=hex_to_frgb(gui.FG_COLOUR_EDIT))
        test.gui_loop(50)

        ol.add_label("TextViewOverlay right",
                     pos=(ol.view_width / 2, 26),
                     align=wx.ALIGN_RIGHT,
                     colour=hex_to_frgb(gui.FG_COLOUR_EDIT))
        test.gui_loop(50)

        ol.add_label("TextViewOverlay center",
                     pos=(ol.view_width / 2, 42),
                     align=wx.ALIGN_CENTER_HORIZONTAL,
                     colour=hex_to_frgb(gui.FG_COLOUR_EDIT))
        test.gui_loop(50)

        ol.add_label("|",
                     pos=(ol.view_width / 2, 58),
                     align=wx.ALIGN_CENTER_HORIZONTAL,
                     colour=hex_to_frgb(gui.FG_COLOUR_EDIT))
        ol.add_label("|",
                     pos=(ol.view_width / 2, 74),
                     align=wx.ALIGN_CENTER_HORIZONTAL,
                     colour=hex_to_frgb(gui.FG_COLOUR_EDIT))
        ol.add_label("Relative to the center",
                     pos=(ol.view_width / 2, 90),
                     align=wx.ALIGN_CENTER_HORIZONTAL,
                     colour=hex_to_frgb(gui.FG_COLOUR_EDIT))
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
                     align=wx.ALIGN_LEFT,
                     colour=hex_to_frgb(gui.FG_COLOUR_EDIT))
        test.gui_loop(50)

        ol.add_label("top right",
                     pos=(ol.view_width, 0),
                     align=wx.ALIGN_RIGHT,
                     colour=hex_to_frgb(gui.FG_COLOUR_EDIT))
        test.gui_loop(50)

        ol.add_label("bottom left",
                     pos=(0, ol.view_height),
                     align=wx.ALIGN_BOTTOM,
                     colour=hex_to_frgb(gui.FG_COLOUR_EDIT))
        test.gui_loop(50)

        ol.add_label("bottom right",
                     pos=(ol.view_width, ol.view_height),
                     align=wx.ALIGN_RIGHT | wx.ALIGN_BOTTOM,
                     flip=False,
                     colour=hex_to_frgb(gui.FG_COLOUR_EDIT))
        test.gui_loop(50)

        ol.add_label("SHOULD NOT BE SEEN!",
                     pos=(ol.view_width, ol.view_height / 2),
                     align=wx.ALIGN_LEFT,
                     flip=False,
                     colour=hex_to_frgb(gui.FG_COLOUR_EDIT))
        test.gui_loop(50)

        ol.add_label("Visible because of flip",
                     pos=(ol.view_width, ol.view_height / 2),
                     align=wx.ALIGN_LEFT,
                     flip=True,
                     colour=hex_to_frgb(gui.FG_COLOUR_EDIT))
        test.gui_loop(50)

    def test_text_view_overlay_rotate(self):
        cnvs = canvas.BitmapCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)
        ol = vol.TextViewOverlay(cnvs)
        ol.canvas_padding = 0
        cnvs.add_view_overlay(ol)

        # Text should exactly overlap

        rl = ol.add_label(u"█ you should only see red",
                          pos=(0, 0),
                          font_size=20,
                          deg=0,
                          flip=False,
                          colour=hex_to_frgb(gui.FG_COLOUR_EDIT))
        test.gui_loop(50)

        sl = ol.add_label(u"█ you should only see red",
                          pos=(0, 0),
                          font_size=20,
                          colour=(1, 0, 0),
                          align=wx.ALIGN_LEFT)
        test.gui_loop(100)
        self.assertEqual(rl.render_pos, sl.render_pos)

        ol.clear_labels()

        ol.add_label(u"█ no rotate",
                     pos=(200, 0),
                     font_size=20,
                     colour=(1, 0, 0),
                     align=wx.ALIGN_LEFT)

        tl = ol.add_label(u"█ rotate left",
                          pos=(200, 25),
                          font_size=20,
                          deg=0,
                          flip=False,
                          colour=hex_to_frgb(gui.FG_COLOUR_EDIT))

        tr = ol.add_label(u"rotate right █",
                          pos=(200, 50),
                          font_size=20,
                          align=wx.ALIGN_RIGHT,
                          deg=0,
                          flip=False,
                          colour=hex_to_frgb(gui.FG_COLOUR_EDIT))

        tc = ol.add_label(u"rotate center █",
                          pos=(200, 75),
                          font_size=20,
                          align=wx.ALIGN_CENTRE_HORIZONTAL,
                          deg=0,
                          flip=False,
                          colour=hex_to_frgb(gui.FG_COLOUR_EDIT))

        test.gui_loop(1000)

        for l in (tl, tr, tc):
            l.deg = 15
            test.gui_loop(500)
            cnvs.Refresh()

    def test_crosshair_overlay(self):
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        hol = vol.CrossHairOverlay(cnvs)
        cnvs.add_view_overlay(hol)

        test.gui_loop()

    def test_spot_mode_overlay(self):
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        cnvs.background_brush = wx.BRUSHSTYLE_CROSS_HATCH
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        sol = wol.SpotModeOverlay(cnvs)
        cnvs.add_world_overlay(sol)
        cnvs.update_drawing()
        test.gui_loop()

    def test_streamicon_overlay(self):
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        sol = vol.PlayIconOverlay(cnvs)
        cnvs.add_view_overlay(sol)
        test.gui_loop(100)
        sol.hide_pause(False)
        test.gui_loop()
        sol.hide_pause(True)
        test.gui_loop(500)

    def test_focus_overlay(self):
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        class FakeFocus(object):
            def moveRel(self, dummy):
                pass

        tab_mod = self.create_simple_tab_model()
        view = tab_mod.focussedView.value
        view._focus = [FakeFocus(), FakeFocus()]
        cnvs.setView(view, tab_mod)

        test.gui_loop()

    def test_view_select_overlay(self):
        test.goto_manual()
        # Create and add a miccanvas
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        vsol = vol.ViewSelectOverlay(cnvs)
        vsol.activate()
        cnvs.add_view_overlay(vsol)
        # cnvs.current_mode = guimodel.TOOL_ZOOM
        test.gui_loop()

    def test_marking_line_overlay(self):
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        mlol = vol.MarkingLineOverlay(cnvs, orientation=1)
        mlol.activate()
        cnvs.add_view_overlay(mlol)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)
        test.gui_loop()

        mlol.v_pos.value = (100, 100)
        cnvs.Refresh()

        test.gui_loop(500)
        mlol.orientation = 2
        mlol.v_pos.value = (200, 200)
        cnvs.Refresh()

        test.gui_loop(500)
        mlol.orientation = 3
        mlol.v_pos.value = (300, 300)
        cnvs.Refresh()

    def test_dichotomy_overlay(self):
        cnvs = miccanvas.SecomCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        lva = omodel.ListVA()

        dol = vol.DichotomyOverlay(cnvs, lva)
        cnvs.add_view_overlay(dol)

        def do_stuff(value):
            """ Test function that can be used to subscribe to VAs """
            print "Testing VA subscriber received value ", value

        self.dummy = do_stuff
        dol.sequence_va.subscribe(do_stuff, init=True)
        dol.sequence_va.value = [0, 1, 2, 3, 0]
        dol.activate()

        test.gui_loop()

    def test_polar_overlay(self):
        cnvs = miccanvas.AngularResolvedCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        test.gui_loop()

        test.sleep(10)

        cnvs.polar_overlay.phi_deg = 60
        cnvs.polar_overlay.theta_deg = 60
        cnvs.polar_overlay.intensity_label.text = "101"

        test.gui_loop()

    def test_point_select_mode_overlay(self):
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        slol = vol.PointSelectOverlay(cnvs)
        slol.activate()

        def print_pos(pos):
            logging.debug(pos)

        self.dummy = print_pos

        slol.v_pos.subscribe(print_pos)
        slol.w_pos.subscribe(print_pos)
        if slol.p_pos:
            slol.p_pos.subscribe(print_pos)

        cnvs.add_view_overlay(slol)

        test.gui_loop()

    def test_history_overlay(self):
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        cnvs.disable_drag()
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)
        history_va = omodel.ListVA()

        hol = vol.HistoryOverlay(cnvs, history_va)
        cnvs.add_view_overlay(hol)

        test.gui_loop()

        for j in range(4):
            offset = ((j - 2) * 75)

            for i in range(10):
                history_va.value.append(
                    (((j * i) + offset, (j * -i) + offset), None)
                )

            for i in range(10):
                history_va.value.append(
                    (((j * -i) + offset, (j * -i) + offset), None)
                )

            for i in range(10):
                history_va.value.append(
                    (((j * -i) + offset, (j * i) + offset), None)
                )

            for i in range(10):
                history_va.value.append(
                    (((j * i) + offset, (j * i) + offset), None)
                )

        test.gui_loop()

        steps = 800
        step_size = 16
        for i in xrange(0, steps * step_size, step_size):
            phi = (math.pi * 2) / steps
            x = (100 * i / (steps * 5)) * math.cos(i * phi)
            y = (100 * i / (steps * 5)) * math.sin(i * phi)
            # hol.history.append(((x, y), None))
            history_va.value.append(
                ((x, y), None)
            )
        # print "Done generating history"

        test.gui_loop()

    # END View overlay test cases

    # World overlay test cases

    def test_world_select_overlay(self):
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)

        tab_mod = self.create_simple_tab_model()
        view = tab_mod.focussedView.value

        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)
        cnvs.setView(view, tab_mod)

        wsol = wol.WorldSelectOverlay(cnvs)
        wsol.activate()
        cnvs.add_world_overlay(wsol)

        tol = vol.TextViewOverlay(cnvs)
        tol.add_label("Right click to toggle tool")
        cnvs.add_view_overlay(tol)

        wsol.w_start_pos = (-2e-05, -2e-05)
        wsol.w_end_pos = (2e-05, 2e-05)

        test.gui_loop()

        def toggle(evt):
            if wsol.active:
                wsol.deactivate()
            else:
                wsol.activate()
            evt.Skip()

        cnvs.Bind(wx.EVT_RIGHT_UP, toggle)

    def test_roa_select_overlay(self):
        # but it should be a simple miccanvas
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)

        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        rsol = wol.RepetitionSelectOverlay(cnvs)
        rsol.activate()
        cnvs.add_world_overlay(rsol)
        cnvs.scale = 400

        test.gui_loop()
        wroi = [-0.1, 0.3, 0.2, 0.4]  # in m
        rsol.set_physical_sel(wroi)
        test.gui_loop()
        wroi_back = rsol.get_physical_sel()

        for o, b in zip(wroi, wroi_back):
            self.assertAlmostEqual(o, b, msg="wroi (%s) != bak (%s)" % (wroi, wroi_back))

        rsol.repetition = (3, 2)
        rsol.fill = wol.RepetitionSelectOverlay.FILL_POINT

        pos = cnvs.margins[0] + 10,  cnvs.margins[1] + 10
        rsol.add_label("Repetition fill will change in 3 seconds.",
                       pos, colour=(0.8, 0.2, 0.1))

        cnvs.update_drawing()
        test.gui_loop()

        tol = vol.TextViewOverlay(cnvs)
        tol.add_label("Right click to toggle tool")
        cnvs.add_view_overlay(tol)

        def toggle(evt):
            if rsol.active:
                rsol.deactivate()
            else:
                rsol.activate()
            evt.Skip()

        cnvs.Bind(wx.EVT_RIGHT_UP, toggle)

        test.gui_loop()

    def test_pixel_select_overlay(self):
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)

        tab_mod = self.create_simple_tab_model()
        view = tab_mod.focussedView.value

        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)
        # FIXME: when setView is called *before* the add_control, the picture goes black and no
        # pixels are visible
        cnvs.setView(view, tab_mod)
        cnvs.current_mode = TOOL_POINT

        psol = wol.PixelSelectOverlay(cnvs)
        psol.activate()
        psol.enabled = True

        cnvs.add_world_overlay(psol)

        psol.set_data_properties(1e-05, (0.0, 0.0), (17, 19))
        width_va = omodel.IntVA(1)

        psol.connect_selection(omodel.TupleVA(), width_va)
        view.mpp.value = 1e-06

        psol._selected_pixel_va.value = (8, 8)
        test.gui_loop()

        # Tool toggle for debugging

        tol = vol.TextViewOverlay(cnvs)
        tol.add_label("Right click to toggle tool", (10, 30))
        cnvs.add_view_overlay(tol)

        def toggle(evt):
            if psol.active:
                psol.deactivate()
            else:
                psol.activate()
            evt.Skip()

        cnvs.Bind(wx.EVT_RIGHT_UP, toggle)

        cnvs.disable_drag()

        def on_key(evt):
            k = evt.GetKeyCode()

            if k == wx.WXK_DOWN and width_va.value > 1:
                width_va.value -= 1
            elif k == wx.WXK_UP:
                width_va.value += 1
            else:
                pass

        cnvs.Bind(wx.EVT_KEY_UP, on_key)

    def test_spectrum_line_select_overlay(self):
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)

        tab_mod = self.create_simple_tab_model()
        view = tab_mod.focussedView.value

        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)
        cnvs.setView(view, tab_mod)
        cnvs.current_mode = TOOL_POINT

        slol = wol.SpectrumLineSelectOverlay(cnvs)
        slol.activate()

        cnvs.add_world_overlay(slol)

        slol.set_data_properties(1e-05, (0.0, 0.0), (17, 19))
        width_va = omodel.IntVA(1)
        slol.connect_selection(omodel.TupleVA(), width_va)
        view.mpp.value = 1e-06
        test.gui_loop()

        # Tool toggle for debugging

        tol = vol.TextViewOverlay(cnvs)
        tol.add_label("Right click to toggle tool", (10, 30))
        cnvs.add_view_overlay(tol)

        test.gui_loop()
        slol._selected_line_va.value = ((0, 0), (8, 8))
        test.gui_loop()

        def toggle(evt):
            if slol.active:
                slol.deactivate()
            else:
                slol.activate()
            evt.Skip()

        cnvs.Bind(wx.EVT_RIGHT_UP, toggle)

        cnvs.disable_drag()

        def on_key(evt):
            k = evt.GetKeyCode()

            if k == wx.WXK_DOWN and width_va.value > 1:
                width_va.value -= 1
            elif k == wx.WXK_UP:
                width_va.value += 1
            else:
                pass

        cnvs.Bind(wx.EVT_KEY_UP, on_key)

    def test_line_select_overlay(self):
        logging.getLogger().setLevel(logging.DEBUG)
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)

        tab_mod = self.create_simple_tab_model()
        view = tab_mod.focussedView.value

        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)
        cnvs.setView(view, tab_mod)
        cnvs.current_mode = TOOL_LINE

        lsol = wol.LineSelectOverlay(cnvs)
        lsol.activate()
        lsol.enabled = True

        lsol.w_start_pos = (1e-4, 1e-4)
        lsol.w_end_pos = (-1e-4, -1e-4)
        cnvs.add_world_overlay(lsol)

        # Tool toggle for debugging

        tol = vol.TextViewOverlay(cnvs)
        tol.add_label("Right click to toggle tool activation", (10, 10))
        cnvs.add_view_overlay(tol)

        def toggle(evt):
            if lsol.active:
                lsol.deactivate()
            else:
                lsol.activate()
            evt.Skip()

        cnvs.Bind(wx.EVT_RIGHT_UP, toggle)

    def test_points_select_overlay(self):
        # Create stuff
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)

        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        tab_mod = self.create_simple_tab_model()
        view = tab_mod.focussedView.value
        view.mpp.value = 1e-5
        cnvs.setView(view, tab_mod)

        # Manually add the overlay
        pol = wol.PointsOverlay(cnvs)
        cnvs.add_world_overlay(pol)

        cnvs.current_mode = guimodel.TOOL_POINT
        pol.activate()

        test.gui_loop()

        from itertools import product

        phys_points = product(xrange(-200, 201, 50), xrange(-200, 201, 50))
        phys_points = [(a / 1.0e5, b / 1.0e5) for a, b in phys_points]

        point = omodel.VAEnumerated(phys_points[0], choices=frozenset(phys_points))

        pol.set_point(point)
        test.gui_loop()

        cnvs.update_drawing()
        test.sleep(500)

        point.value = (50 / 1.0e5, 50 / 1.0e5)

        test.sleep(500)

        # point = omodel.VAEnumerated(phys_points[0], choices=frozenset([(50 / 1.0e5, 50 / 1.0e5)]))
        # pol.set_point(point)

    # END World overlay test cases


if __name__ == "__main__":
    unittest.main()
    suit = unittest.TestSuite()
