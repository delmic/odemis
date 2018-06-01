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
from __future__ import division

import logging
import math
import numpy
from odemis import model
from odemis.acq.stream import UNDEFINED_ROI
from odemis.driver import simsem
from odemis.gui.comp.overlay import view as vol
from odemis.gui.comp.overlay import world as wol
from odemis.gui.model import TOOL_POINT, TOOL_LINE
from odemis.util.conversion import hex_to_frgb
import unittest
import wx

import odemis.gui as gui
import odemis.gui.comp.canvas as canvas
import odemis.gui.comp.miccanvas as miccanvas
import odemis.gui.model as guimodel
import odemis.gui.test as test
from odemis.util.comp import compute_scanner_fov, get_fov_rect

test.goto_manual()
logging.getLogger().setLevel(logging.DEBUG)

# To create a simulated SEM
CONFIG_SED = {"name": "sed", "role": "none"}
CONFIG_SCANNER = {"name": "scanner", "role": "ebeam"}
CONFIG_SEM = {"name": "sem_int",
                  "role": "none",
                  "children": {"detector0": CONFIG_SED, "scanner": CONFIG_SCANNER}
                  }


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
                test.gui_loop(0.05)

            ol.clear_labels()

    def test_text_view_overlay_align(self):
        cnvs = canvas.BitmapCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        ol = vol.TextViewOverlay(cnvs)
        cnvs.add_view_overlay(ol)

        ol.add_label("TextViewOverlay left",
                     pos=(ol.view_width / 2, 10),
                     colour=hex_to_frgb(gui.FG_COLOUR_EDIT))
        test.gui_loop(0.05)

        ol.add_label("TextViewOverlay right",
                     pos=(ol.view_width / 2, 26),
                     align=wx.ALIGN_RIGHT,
                     colour=hex_to_frgb(gui.FG_COLOUR_EDIT))
        test.gui_loop(0.05)

        ol.add_label("TextViewOverlay center",
                     pos=(ol.view_width / 2, 42),
                     align=wx.ALIGN_CENTER_HORIZONTAL,
                     colour=hex_to_frgb(gui.FG_COLOUR_EDIT))
        test.gui_loop(0.05)

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
        test.gui_loop(0.05)

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
        test.gui_loop(0.05)

        ol.add_label("top right",
                     pos=(ol.view_width, 0),
                     align=wx.ALIGN_RIGHT,
                     colour=hex_to_frgb(gui.FG_COLOUR_EDIT))
        test.gui_loop(0.05)

        ol.add_label("bottom left",
                     pos=(0, ol.view_height),
                     align=wx.ALIGN_BOTTOM,
                     colour=hex_to_frgb(gui.FG_COLOUR_EDIT))
        test.gui_loop(0.05)

        ol.add_label("bottom right",
                     pos=(ol.view_width, ol.view_height),
                     align=wx.ALIGN_RIGHT | wx.ALIGN_BOTTOM,
                     flip=False,
                     colour=hex_to_frgb(gui.FG_COLOUR_EDIT))
        test.gui_loop(0.05)

        ol.add_label("SHOULD NOT BE SEEN!",
                     pos=(ol.view_width, ol.view_height / 2),
                     align=wx.ALIGN_LEFT,
                     flip=False,
                     colour=hex_to_frgb(gui.FG_COLOUR_EDIT))
        test.gui_loop(0.05)

        ol.add_label("Visible because of flip",
                     pos=(ol.view_width, ol.view_height / 2),
                     align=wx.ALIGN_LEFT,
                     flip=True,
                     colour=hex_to_frgb(gui.FG_COLOUR_EDIT))
        test.gui_loop(0.05)

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
        test.gui_loop(0.05)

        sl = ol.add_label(u"█ you should only see red",
                          pos=(0, 0),
                          font_size=20,
                          colour=(1, 0, 0),
                          align=wx.ALIGN_LEFT)
        test.gui_loop(0.1)
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

        test.gui_loop(1)

        for l in (tl, tr, tc):
            l.deg = 15
            test.gui_loop(0.5)
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

        sol = vol.SpotModeOverlay(cnvs)
        sol.activate()
        cnvs.add_view_overlay(sol)
        cnvs.update_drawing()
        test.gui_loop()

    def test_streamicon_overlay(self):
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        sol = vol.PlayIconOverlay(cnvs)
        cnvs.add_view_overlay(sol)
        test.gui_loop(0.1)
        sol.hide_pause(False)
        test.gui_loop()
        sol.hide_pause(True)
        test.gui_loop(0.5)

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
        cnvs = miccanvas.TwoDPlotCanvas(self.panel)
        mlol = cnvs.markline_overlay
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        rgb = numpy.empty((30, 200, 3), dtype=numpy.uint8)
        data = model.DataArray(rgb)
        cnvs.set_2d_data(data, unit_x='m', unit_y='m',
                         range_x=[200e-9, 500e-9], range_y=[0, 20e-6])

        test.gui_loop()

        mlol.val.value = (201e-9, 10e-6)
        cnvs.Refresh()

        test.gui_loop(0.5)
        mlol.orientation = vol.MarkingLineOverlay.HORIZONTAL
        cnvs.Refresh()

        test.gui_loop(0.5)
        mlol.orientation = vol.MarkingLineOverlay.VERTICAL
        mlol.val.value = (301e-9, 12e-6)
        cnvs.Refresh()

        test.gui_loop(0.5)
        mlol.orientation = vol.MarkingLineOverlay.HORIZONTAL | vol.MarkingLineOverlay.VERTICAL
        mlol.val.value = (401e-9, 20e-6)
        cnvs.Refresh()

        test.gui_loop(0.5)
        # Out of the range
        mlol.val.value = (0, 0)
        cnvs.Refresh()
        test.gui_loop(0.5)

    def test_dichotomy_overlay(self):
        cnvs = miccanvas.SecomCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        lva = model.ListVA()

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

        test.gui_loop(0.1)

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
        slol.p_pos.subscribe(print_pos)

        cnvs.add_view_overlay(slol)

        test.gui_loop()

    def test_history_overlay(self):
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        cnvs.disable_drag()
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)
        history_va = model.ListVA()

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
        cnvs.update_drawing()

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
        rsol.add_label("Repetition fill will change in 2 seconds.",
                       pos, colour=(0.8, 0.2, 0.1))

        # cnvs.update_drawing()
        test.gui_loop(2)

        rsol.set_physical_sel((-0.1, -0.3, 0.4, 0.4))
        rsol.repetition = (50, 80)
        rsol.fill = wol.RepetitionSelectOverlay.FILL_GRID

        pos = cnvs.margins[0] + 10, cnvs.margins[1] + 10
        rsol.add_label("Repetition fill will change in 2 seconds.",
                       pos, colour=(0.8, 0.2, 0.1))

        # cnvs.update_drawing()
        test.gui_loop(2)

        # Fine grid => solid colour
        rsol.repetition = (500, 800)
        rsol.fill = wol.RepetitionSelectOverlay.FILL_GRID

        pos = cnvs.margins[0] + 10, cnvs.margins[1] + 10
        rsol.add_label("Repetition fill will change in 2 seconds.",
                       pos, colour=(0.8, 0.2, 0.1))

        # cnvs.update_drawing()
        test.gui_loop(2)

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

    def test_roa_select_overlay_va(self):

        sem = simsem.SimSEM(**CONFIG_SEM)
        for child in sem.children.value:
            if child.name == CONFIG_SCANNER["name"]:
                ebeam = child
        # Simulate a stage move
        ebeam.updateMetadata({model.MD_POS: (1e-3, -0.2e-3)})

        # but it should be a simple miccanvas
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        roa = model.TupleVA(UNDEFINED_ROI)
        rsol = wol.RepetitionSelectOverlay(cnvs, roa=roa, scanner=ebeam)
        rsol.activate()
        cnvs.add_world_overlay(rsol)
        cnvs.scale = 100000
        cnvs.update_drawing()

        # Undefined ROA => sel = None
        roi_back = rsol.get_physical_sel()
        self.assertEqual(roi_back, None)

        # Full FoV
        roa.value = (0, 0, 1, 1)
        test.gui_loop(0.1)
        # Expect the whole SEM FoV
        fov = compute_scanner_fov(ebeam)
        ebeam_rect = get_fov_rect(ebeam, fov)
        roi_back = rsol.get_physical_sel()

        for o, b in zip(ebeam_rect, roi_back):
            self.assertAlmostEqual(o, b, msg="ebeam FoV (%s) != ROI (%s)" % (ebeam_rect, roi_back))

        # Hald the FoV
        roa.value = (0.25, 0.25, 0.75, 0.75)
        test.gui_loop(0.1)
        # Expect the whole SEM FoV
        fov = compute_scanner_fov(ebeam)
        fov = (fov[0] / 2, fov[1] / 2)
        ebeam_rect = get_fov_rect(ebeam, fov)
        roi_back = rsol.get_physical_sel()

        for o, b in zip(ebeam_rect, roi_back):
            self.assertAlmostEqual(o, b, msg="ebeam FoV (%s) != ROI (%s)" % (ebeam_rect, roi_back))

        test.gui_loop()

        sem.terminate()

    def test_spot_mode_world_overlay(self):
        sem = simsem.SimSEM(**CONFIG_SEM)
        for child in sem.children.value:
            if child.name == CONFIG_SCANNER["name"]:
                ebeam = child
        # Simulate a stage move
        ebeam.updateMetadata({model.MD_POS: (1e-3, -0.2e-3)})

        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        cnvs.background_brush = wx.BRUSHSTYLE_CROSS_HATCH
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        spotPosition = model.TupleVA((0.1, 0.1))
        sol = wol.SpotModeOverlay(cnvs, spot_va=spotPosition, scanner=ebeam)
        sol.activate()
        cnvs.add_world_overlay(sol)
        cnvs.scale = 100000
        cnvs.update_drawing()
        test.gui_loop(1)

        spotPosition.value = (0.5, 0.5)

        test.gui_loop(1)

        spotPosition.value = (None, None)

        test.gui_loop()
        self.assertIsNone(sol.p_pos, None)

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
        width_va = model.IntVA(1)

        psol.connect_selection(model.TupleVA(), width_va)
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
        width_va = model.IntVA(1)
        line_va = model.TupleVA(((None, None), (None, None)))
        slol.connect_selection(line_va, width_va)
        view.mpp.value = 1e-06
        test.gui_loop()

        # Tool toggle for debugging

        tol = vol.TextViewOverlay(cnvs)
        tol.add_label("Right click to toggle tool", (10, 30))
        cnvs.add_view_overlay(tol)

        test.gui_loop()
        line_va.value = ((0, 0), (8, 8))
        test.gui_loop()

        # Also connect the pixel va
        pixel_va = model.TupleVA((8, 8))
        slol.connect_selection(line_va, width_va, pixel_va)
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

        point = model.VAEnumerated(phys_points[0], choices=frozenset(phys_points))

        pol.set_point(point)
        test.gui_loop()

        cnvs.update_drawing()
        test.gui_loop(0.5)

        point.value = (50 / 1.0e5, 50 / 1.0e5)

        test.gui_loop(0.5)

        # point = model.VAEnumerated(phys_points[0], choices=frozenset([(50 / 1.0e5, 50 / 1.0e5)]))
        # pol.set_point(point)

    def test_mirror_arc_overlay(self):
        cnvs = miccanvas.SparcARCanvas(self.panel)
        cnvs.scale = 20000
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        cnvs.flip = 0
        cnvs.update_drawing()

        def flip(evt):
            if cnvs.flip == wx.VERTICAL:
                cnvs.flip = 0
            else:
                cnvs.flip = wx.VERTICAL
            cnvs.update_drawing()
            evt.Skip()

        def zoom(evt):
            if evt.GetWheelRotation() > 0:
                cnvs.scale *= 1.1
            else:
                cnvs.scale *= 0.9
            cnvs.update_drawing()

        cnvs.Bind(wx.EVT_LEFT_DCLICK, flip)
        cnvs.Bind(wx.EVT_MOUSEWHEEL, zoom)

        test.gui_loop()

    # END World overlay test cases


if __name__ == "__main__":
    unittest.main()
    suit = unittest.TestSuite()
