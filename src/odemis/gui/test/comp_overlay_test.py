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
from builtins import range
import copy
import logging
import math
import numpy
from odemis import model
from odemis.acq.stream import UNDEFINED_ROI
from odemis.driver import simsem
from odemis.driver.tmcm import TMCLController
from odemis.gui.comp.overlay import view as vol
from odemis.gui.comp.overlay import world as wol
from odemis.gui.comp.overlay.view import HORIZONTAL_LINE, VERTICAL_LINE, CROSSHAIR
from odemis.gui.comp.overlay.world import EKOverlay
from odemis.gui.comp.viewport import ARLiveViewport
from odemis.gui.model import TOOL_POINT, TOOL_LINE, TOOL_RULER, TOOL_LABEL, FeatureOverviewView
from odemis.gui.util.img import wxImage2NDImage
from odemis.util import mock
from odemis.util.comp import compute_scanner_fov, get_fov_rect
from odemis.util.conversion import hex_to_frgb
from odemis.util.testing import assert_array_not_equal, assert_pos_not_almost_equal
import time
import unittest
import wx

import odemis.gui as gui
import odemis.gui.comp.canvas as canvas
import odemis.gui.comp.miccanvas as miccanvas
import odemis.gui.model as guimodel
import odemis.gui.test as test

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

        hol = vol.CenteredLineOverlay(cnvs, shape=CROSSHAIR)
        cnvs.add_view_overlay(hol)
        test.gui_loop()

        hol = vol.CenteredLineOverlay(cnvs, shape=HORIZONTAL_LINE)
        cnvs.add_view_overlay(hol)
        test.gui_loop()

        hol = vol.CenteredLineOverlay(cnvs, shape=VERTICAL_LINE)
        cnvs.add_view_overlay(hol)
        test.gui_loop()

    def test_current_pos_crosshair_overlay(self):
        """
        Test behaviour of CurrentPosCrossHairOverlay
        """
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        tab_mod = self.create_simple_tab_model()
        # create a dummy stage to attach to the view
        stage = TMCLController(name="test", role="test",
                               port="/dev/fake3",
                               axes=["x", "y"],
                               ustepsize=[1e-6, 1e-6],
                               rng=[[-3e-3, 3e-3], [-3e-3, 3e-3]],
                               refproc="Standard")

        # Add a tiled area view to the tab model
        logging.debug(stage.position.value)
        fview = FeatureOverviewView("fakeview", stage=stage)
        tab_mod.views.value.append(fview)
        tab_mod.focussedView.value = fview
        cnvs.setView(fview, tab_mod)
        cnvs.view.show_crosshair.value = False

        slol = wol.CurrentPosCrossHairOverlay(cnvs)
        slol.active.value = True
        cnvs.add_world_overlay(slol)
        # stage start at 0,0 (cross hair at center) -> move bt 1mm, 1mm -> then back to 0,0
        stage.moveAbs({'x': 1e-3, 'y': 1e-3}).result()
        img = wx.Bitmap.ConvertToImage(cnvs._bmp_buffer)
        buffer_ch_move = wxImage2NDImage(img)
        test.gui_loop(1)

        stage.moveAbs({'x': 0, 'y': 0}).result()
        cnvs.update_drawing()
        cnvs._dc_buffer.SelectObject(wx.NullBitmap)  # Flush the buffer
        cnvs._dc_buffer.SelectObject(cnvs._bmp_buffer)
        img = wx.Bitmap.ConvertToImage(cnvs._bmp_buffer)
        buffer_ch_center = wxImage2NDImage(img)
        assert_array_not_equal(buffer_ch_center, buffer_ch_move,
                               msg="Buffers are equal, which means the crosshair didn't change on stage movement.")

        test.gui_loop()

    def test_spot_mode_overlay(self):
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        cnvs.background_brush = wx.BRUSHSTYLE_CROSS_HATCH
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        sol = vol.SpotModeOverlay(cnvs)
        sol.active.value = True
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
        vsol.active.value = True
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
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        lva = model.ListVA()

        dol = vol.DichotomyOverlay(cnvs, lva)
        cnvs.add_view_overlay(dol)

        def do_stuff(value):
            """ Test function that can be used to subscribe to VAs """
            print("Testing VA subscriber received value ", value)

        self.dummy = do_stuff
        dol.sequence_va.subscribe(do_stuff, init=True)
        dol.sequence_va.value = [0, 1, 2, 3, 0]
        dol.active.value = True

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
        slol.active.value = True

        def print_pos(pos):
            logging.debug(pos)

        self.dummy = print_pos

        slol.v_pos.subscribe(print_pos)
        slol.p_pos.subscribe(print_pos)

        cnvs.add_view_overlay(slol)

        test.gui_loop()

    def test_cryo_feature_overlay(self):
        """
        Test behavior of CryoFeatureOverlay
        """
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        tab_mod = self.create_cryo_tab_model()
        # Create a dummy stage & focus to attach to the view
        stage = TMCLController(name="test_stage", role="stage",
                               port="/dev/fake3",
                               axes=["x", "y"],
                               ustepsize=[1e-6, 1e-6],
                               rng=[[-3e-3, 3e-3], [-3e-3, 3e-3]],
                               refproc="Standard")

        focus = TMCLController(name="test_focus", role="focus",
                               port="/dev/fake3",
                               axes=["z"],
                               ustepsize=[1e-6],
                               rng=[[-3e-3, 3e-3]],
                               refproc="Standard")
        tab_mod.main.stage = stage
        tab_mod.main.focus = focus

        fview = FeatureOverviewView("fakeview", stage=stage)
        tab_mod.views.value.append(fview)
        tab_mod.focussedView.value = fview
        cnvs.setView(fview, tab_mod)
        # Save current empty canvas
        img = wx.Bitmap.ConvertToImage(cnvs._bmp_buffer)
        buffer_clear = wxImage2NDImage(img)

        cryofeature_overlay = wol.CryoFeatureOverlay(cnvs, tab_mod)
        cnvs.add_world_overlay(cryofeature_overlay)
        cryofeature_overlay.active.value = True

        # Add features to the tab's features list
        tab_mod.add_new_feature(0, 0)
        tab_mod.add_new_feature(0.001, 0.001)

        cnvs._dc_buffer.SelectObject(wx.NullBitmap)  # Flush the buffer
        cnvs._dc_buffer.SelectObject(cnvs._bmp_buffer)
        img = wx.Bitmap.ConvertToImage(cnvs._bmp_buffer)
        buffer_feature = wxImage2NDImage(img)
        # Compare empty canvas with the added _features_va
        assert_array_not_equal(buffer_clear, buffer_feature,
                               msg="Buffers are equal, which means the added _features_va didn't appear on the canvas.")
        test.gui_loop()

    def test_stage_point_select_mode_overlay(self):
        """
        Test behavior of StagePointSelectOverlay
        """
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        tab_mod = self.create_simple_tab_model()
        # create a dummy stage to attach to the view
        stage = TMCLController(name="test", role="test",
                                    port="/dev/fake3",
                                    axes=["x", "y"],
                                    ustepsize=[1e-6, 1e-6],
                                    rng=[[-3e-3, 3e-3], [-3e-3, 3e-3]],
                                    refproc="Standard")

        # Add a tiled area view to the tab model
        logging.debug(stage.position.value)
        fview = FeatureOverviewView("fakeview", stage=stage)
        tab_mod.views.value.append(fview)
        tab_mod.focussedView.value = fview
        cnvs.setView(fview, tab_mod)

        slol = wol.StagePointSelectOverlay(cnvs)
        slol.active.value = True
        cnvs.add_world_overlay(slol)

        initial_pos = copy.deepcopy(stage.position.value)
        # simulate double click by passing the mouse event to on_dbl_click
        evt = wx.MouseEvent()
        evt.x = 10
        evt.y = 10
        slol.on_dbl_click(evt)
        test.gui_loop(1)
        # stage should have been moved from initial position
        assert_pos_not_almost_equal(stage.position.value, initial_pos)
        logging.debug(stage.position.value)

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
        for i in range(0, steps * step_size, step_size):
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
        wsol.active.value = True
        cnvs.add_world_overlay(wsol)

        tol = vol.TextViewOverlay(cnvs)
        tol.add_label("Right click to toggle tool")
        cnvs.add_view_overlay(tol)

        wsol.w_start_pos = (-2e-05, -2e-05)
        wsol.w_end_pos = (2e-05, 2e-05)

        test.gui_loop()

        def toggle(evt):
            if wsol.active.value:
                wsol.active.value = False
            else:
                wsol.active.value = True
            evt.Skip()

        cnvs.Bind(wx.EVT_RIGHT_UP, toggle)

    def test_roa_select_overlay(self):
        # but it should be a simple miccanvas
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)

        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        rsol = wol.RepetitionSelectOverlay(cnvs)
        rsol.active.value = True
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
            rsol.active.value = not rsol.active.value
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
        rsol.active.value = True
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
        sol.active.value = True
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
        psol.active.value = True
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
            if psol.active.value:
                psol.active.value = False
            else:
                psol.active.value = True
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
        slol.active.value = True

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
            if slol.active.value:
                slol.active.value = False
            else:
                slol.active.value = True
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

    def test_ruler_overlay(self):
        logging.getLogger().setLevel(logging.DEBUG)
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)

        tab_mod = self.create_simple_tab_model()
        tab_mod.tool.choices |= {TOOL_RULER, TOOL_LABEL}
        view = tab_mod.focussedView.value
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)
        cnvs.setView(view, tab_mod)
        gol = cnvs.gadget_overlay

        test.gui_loop(0.1)

        # Gadget overlay with no tools
        img = wx.Bitmap.ConvertToImage(cnvs._bmp_buffer)
        buffer_empty = wxImage2NDImage(img)
        self.assertTrue(numpy.all(buffer_empty == 0))

        # Create a "big ruler"
        p_start_pos = (-0.00055, -0.00055)
        p_end_pos = (0.00055, 0.00055)
        ruler = wol.RulerGadget(cnvs, p_start_pos, p_end_pos)
        gol._tools.append(ruler)

        # Create a 10 px ruler
        v_start_pos = (400, 400)
        v_end_pos = (400, 410)
        offset = cnvs.get_half_buffer_size()
        p_start_pos = cnvs.view_to_phys(v_start_pos, offset)
        p_end_pos = cnvs.view_to_phys(v_end_pos, offset)
        ruler = wol.RulerGadget(cnvs, p_start_pos, p_end_pos)
        gol._tools.append(ruler)

        # Create a 1 px ruler
        b_start_pos = (599, 670)
        b_end_pos = (599, 671)
        offset = cnvs.get_half_buffer_size()
        p_start_pos = cnvs.buffer_to_phys(b_start_pos, offset)
        p_end_pos = cnvs.buffer_to_phys(b_end_pos, offset)
        ruler = wol.RulerGadget(cnvs, p_start_pos, p_end_pos)
        gol._tools.append(ruler)

        # Add one ruler that will become the selected ruler
        p_start_pos = (0, 0)
        p_end_pos = (0, 0.00035)
        selected_ruler = wol.RulerGadget(cnvs, p_start_pos, p_end_pos)
        gol._tools.append(selected_ruler)

        # Update drawing
        cnvs.update_drawing()
        test.gui_loop(0.1)

        # Ruler overlay with 4 rulers
        cnvs._dc_buffer.SelectObject(wx.NullBitmap)  # Flush the buffer
        cnvs._dc_buffer.SelectObject(cnvs._bmp_buffer)
        img = wx.Bitmap.ConvertToImage(cnvs._bmp_buffer)
        new_buffer = wxImage2NDImage(img)
        assert_array_not_equal(buffer_empty, new_buffer,
                        msg="Buffers are equal, which means that the rulers were not drawn")

        # Make the last ruler the selected one (highlighted)
        gol._selected_tool = selected_ruler
        cnvs.update_drawing()
        test.gui_loop(0.1)

        # Ruler overlay with 4 rulers, 1 of them is selected (highlighted)
        cnvs._dc_buffer.SelectObject(wx.NullBitmap)  # Flush the buffer
        cnvs._dc_buffer.SelectObject(cnvs._bmp_buffer)
        img = wx.Bitmap.ConvertToImage(cnvs._bmp_buffer)
        sel_buffer = wxImage2NDImage(img)
        assert_array_not_equal(new_buffer, sel_buffer,
                        msg="Buffers are equal, which means that the label was not drawn")

        # Create a label
        v_start_pos = (500, 500)
        v_end_pos = (500, 510)
        offset = cnvs.get_half_buffer_size()
        p_start_pos = cnvs.view_to_phys(v_start_pos, offset)
        p_end_pos = cnvs.view_to_phys(v_end_pos, offset)
        label = wol.LabelGadget(cnvs, p_start_pos, p_end_pos)
        label.text = 'A label is added'
        gol._tools.append(label)

        # Update drawing
        cnvs.update_drawing()
        test.gui_loop(0.1)

        # Gadget overlay with 4 rulers and 1 label
        # Ruler overlay with 4 rulers
        cnvs._dc_buffer.SelectObject(wx.NullBitmap)  # Flush the buffer
        cnvs._dc_buffer.SelectObject(cnvs._bmp_buffer)
        img = wx.Bitmap.ConvertToImage(cnvs._bmp_buffer)
        lab_buffer = wxImage2NDImage(img)
        assert_array_not_equal(sel_buffer, lab_buffer,
                               msg="Buffers are equal, which means that the label was not drawn")

    def test_label_overlay(self):
        logging.getLogger().setLevel(logging.DEBUG)
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)

        tab_mod = self.create_simple_tab_model()
        tab_mod.tool.choices |= {TOOL_RULER, TOOL_LABEL}
        view = tab_mod.focussedView.value
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)
        cnvs.setView(view, tab_mod)
        gol = cnvs.gadget_overlay

        test.gui_loop(0.1)

        # gadget overlay with no tools
        img = wx.Bitmap.ConvertToImage(cnvs._bmp_buffer)
        buffer_empty = wxImage2NDImage(img)
        self.assertTrue(numpy.all(buffer_empty == 0))

        # Create a "big label"
        p_start_pos = (-0.00055, -0.00055)
        p_end_pos = (0.00055, 0.00055)
        label = wol.LabelGadget(cnvs, p_start_pos, p_end_pos)
        label.text = 'label1'
        gol._tools.append(label)

        # Create a 10 px label
        v_start_pos = (500, 500)
        v_end_pos = (500, 510)
        offset = cnvs.get_half_buffer_size()
        p_start_pos = cnvs.view_to_phys(v_start_pos, offset)
        p_end_pos = cnvs.view_to_phys(v_end_pos, offset)
        label = wol.LabelGadget(cnvs, p_start_pos, p_end_pos)
        label.text = 'label2'
        gol._tools.append(label)

        # Create a 1 px label
        b_start_pos = (599, 670)
        b_end_pos = (599, 671)
        offset = cnvs.get_half_buffer_size()
        p_start_pos = cnvs.buffer_to_phys(b_start_pos, offset)
        p_end_pos = cnvs.buffer_to_phys(b_end_pos, offset)
        label = wol.LabelGadget(cnvs, p_start_pos, p_end_pos)
        label.text = 'label3'
        gol._tools.append(label)

        # Add one ruler that will become the selected ruler
        p_start_pos = (0, 0)
        p_end_pos = (0, 0.00035)
        selected_label = wol.LabelGadget(cnvs, p_start_pos, p_end_pos)
        selected_label.text = 'label4'
        gol._tools.append(selected_label)

        # update drawing
        cnvs.update_drawing()
        test.gui_loop(0.1)

        # ruler overlay with 4 labels
        cnvs._dc_buffer.SelectObject(wx.NullBitmap)  # Flush the buffer
        cnvs._dc_buffer.SelectObject(cnvs._bmp_buffer)
        img = wx.Bitmap.ConvertToImage(cnvs._bmp_buffer)
        new_buffer = wxImage2NDImage(img)
        assert_array_not_equal(buffer_empty, new_buffer,
                               msg="Buffers are equal, which means that the labels were not drawn")

        # make the last label the selected one (highlighted)
        gol._selected_tool = selected_label
        gol._selected_tool.text = 'selected_label'
        cnvs.update_drawing()
        test.gui_loop(0.1)

        # ruler overlay with 4 labels, 1 of them is selected (highlighted)
        cnvs._dc_buffer.SelectObject(wx.NullBitmap)  # Flush the buffer
        cnvs._dc_buffer.SelectObject(cnvs._bmp_buffer)
        img = wx.Bitmap.ConvertToImage(cnvs._bmp_buffer)
        sel_buffer = wxImage2NDImage(img)
        assert_array_not_equal(new_buffer, sel_buffer,
                               msg="Buffers are equal, which means that the labels were not drawn")

        # Create a ruler
        v_start_pos = (500, 500)
        v_end_pos = (500, 510)
        offset = cnvs.get_half_buffer_size()
        p_start_pos = cnvs.view_to_phys(v_start_pos, offset)
        p_end_pos = cnvs.view_to_phys(v_end_pos, offset)
        ruler = wol.RulerGadget(cnvs, p_start_pos, p_end_pos)
        gol._tools.append(ruler)

        # Update drawing
        cnvs.update_drawing()
        test.gui_loop(0.1)

        # Gadget overlay with 4 rulers and 1 label
        # Ruler overlay with 4 rulers
        cnvs._dc_buffer.SelectObject(wx.NullBitmap)  # Flush the buffer
        cnvs._dc_buffer.SelectObject(cnvs._bmp_buffer)
        img = wx.Bitmap.ConvertToImage(cnvs._bmp_buffer)
        rul_buffer = wxImage2NDImage(img)
        assert_array_not_equal(sel_buffer, rul_buffer,
                               msg="Buffers are equal, which means that the ruler was not drawn")

    def test_line_select_overlay(self):
        logging.getLogger().setLevel(logging.DEBUG)
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)

        tab_mod = self.create_simple_tab_model()
        view = tab_mod.focussedView.value

        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)
        cnvs.setView(view, tab_mod)
        cnvs.current_mode = TOOL_LINE

        lsol = wol.LineSelectOverlay(cnvs)
        lsol.active.value = True
        lsol.enabled = True

        lsol.w_start_pos = (1e-4, 1e-4)
        lsol.w_end_pos = (-1e-4, -1e-4)
        cnvs.add_world_overlay(lsol)

        # Tool toggle for debugging

        tol = vol.TextViewOverlay(cnvs)
        tol.add_label("Right click to toggle tool activation", (10, 10))
        cnvs.add_view_overlay(tol)

        def toggle(evt):
            if lsol.active.value:
                lsol.active.value = False
            else:
                lsol.active.value = True
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
        pol.active.value = True

        test.gui_loop()

        from itertools import product

        phys_points = product(range(-200, 201, 50), range(-200, 201, 50))
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

    def test_ek_overlay(self):
        """
        Test EKOverlay is created and display without error
        """
        cnvs = miccanvas.SparcARCanvas(self.panel)
        cnvs.scale = 200000  # Scale that works for the given mirror lines
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        # Create a CCD (the role matters)
        img = model.DataArray(numpy.zeros((512, 768), dtype=numpy.uint16))
        img[:, 768 // 2] = 10000
        img.metadata = {
            model.MD_WL_LIST: [],
            model.MD_PIXEL_SIZE: (5e-6, 5e-6)
        }
        ccd = mock.FakeCCD(img)  # role is always "ccd"

        class FakeTabData():
            def __init__(self):
                self.mirrorPositionTopPhys = model.TupleContinuous((100e-6, 0),
                                           ((-1e18, -1e18), (1e18, 1e18)), unit="m",
                                           cls=(int, float))
                self.mirrorPositionBottomPhys = model.TupleContinuous((-100e-6, 0),
                                           ((-1e18, -1e18), (1e18, 1e18)), unit="m",
                                           cls=(int, float)
                                           )

        fake_tab_data = FakeTabData()

        # Note: for now, as the image is not shown in the canvas (would need a live stream)
        ek_ol = EKOverlay(cnvs)
        cnvs.add_world_overlay(ek_ol)
        ek_ol.active.value = True
        ek_ol.create_ek_mask(ccd, fake_tab_data)
        # ek_ol.set_mirror_dimensions(lens.parabolaF.value,
        #                             lens.xMax.value,
        #                             lens.focusDistance.value)

        # First, no wavelength, so a warning at the center
        cnvs.request_drawing_update()
        test.gui_loop(1.0)

        # Add the wavelength => the lines should be shown
        ccd.updateMetadata({model.MD_WL_LIST: [500e-9 + i * 1e-9 for i in range(img.shape[-1])]})
        cnvs.request_drawing_update()
        test.gui_loop()

    def test_mirror_arc_overlay(self):
        vp = ARLiveViewport(self.panel)
        vp.show_mirror_overlay()
        self.add_control(vp, wx.EXPAND, proportion=1, clear=True)

        cnvs = vp.canvas
        cnvs.scale = 20000
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
