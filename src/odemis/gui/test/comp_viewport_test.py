#-*- coding: utf-8 -*-

"""
@author: Rinze de Laat

Copyright © 2013 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""
from collections import deque
import logging
import math
import numpy
import threading
import time
import unittest
import wx
from builtins import range

import odemis.gui.comp.canvas as canvas
import odemis.gui.comp.viewport as viewport
import odemis.gui.test as test

logging.getLogger().setLevel(logging.DEBUG)

test.goto_manual()

MODES = [canvas.PLOT_MODE_POINT, canvas.PLOT_MODE_LINE, canvas.PLOT_MODE_BAR]

PLOTS = [
    ([3, 5, 8], [2, 3, 1]),
    ([2, 3, 4], [2, 3, 1]),
    ([0, 1, 2, 3, 4, 5], [0, 0, 5, 2, 4, 0]),
    ([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126, 127], [15, 29, 29, 34, 42, 48, 62, 64, 71, 88, 94, 95, 104, 117, 124, 126, 140, 144, 155, 158, 158, 172, 186, 205, 214, 226, 234, 244, 248, 265, 280, 299, 312, 314, 317, 321, 333, 335, 337, 343, 346, 346, 352, 370, 379, 384, 392, 411, 413, 431, 438, 453, 470, 477, 487, 495, 509, 512, 519, 527, 535, 544, 550, 555, 561, 574, 579, 582, 601, 605, 616, 619, 620, 633, 642, 658, 668, 687, 702, 716, 732, 745, 763, 779, 780, 780, 793, 803, 815, 815, 832, 851, 851, 866, 873, 890, 896, 906, 918, 919, 921, 922, 933, 934, 949, 949, 952, 963, 974, 974, 989, 989, 1002, 1012, 1031, 1046, 1053, 1062, 1066, 1074, 1085, 1092, 1097, 1097, 1098, 1103, 1105, 1116]),
    ([17, 36, 40, 43, 44, 62, 79, 83, 99, 104, 116, 133, 147, 152, 171, 185, 193, 195, 201, 210, 225, 241, 246, 254, 269, 270, 272, 280, 286, 304, 323, 336, 344, 345, 351, 355, 374, 381, 400, 408, 425, 444, 449, 456, 466, 482, 489, 506, 507, 516, 526, 542, 561, 576, 581, 593, 595, 602, 604, 618, 633, 639, 647, 656, 667, 670, 689, 691, 705, 721, 725, 738, 750, 767, 768, 776, 786, 797, 809, 815, 832, 840, 857, 867, 869, 878, 889, 892, 905, 907, 915, 934, 952, 957, 971, 985, 1003, 1019, 1032, 1042, 1046, 1058, 1077, 1089, 1100, 1104, 1109, 1121, 1124, 1127, 1132, 1145, 1148, 1155, 1170, 1171, 1183, 1184, 1196, 1208, 1214, 1229, 1235, 1236, 1239], [0.0, 0.6365122726989454, 1.2723796780808552, 1.906958002160726, 2.53960433695571, 3.1696777318320972, 3.7965398428692474, 4.419555579582602, 5.0380937483505495, 5.651527691893277, 6.259235924155733, 6.860602759951489, 7.455018938729595, 8.041882241832447, 8.620598102619352, 9.19058020883762, 9.75125109663092, 10.30204273558309, 10.842397104204696, 11.371766755279262, 11.889615370496443, 12.395418303810198, 12.888663112971502, 13.368850078697054, 13.835492710948023, 14.28811824180589, 14.72626810444606, 15.149498397723974, 15.557380335903012, 15.949500683068614, 16.325462171788427, 16.68488390559441, 17.027401744879036, 17.352668675814723, 17.660355161922638, 17.9501494779348, 18.22175802561112, 18.474905631191508, 18.70933582418163, 18.924811097189927, 19.12111314655259, 19.29804309350273, 19.4554216856597, 19.593089478634386, 19.710906997566514, 19.8087548784303, 19.886533988965265, 19.94416552910976, 19.98159111083536, 19.998772817301322, 19.995693241269127, 19.972355502738214, 19.928783245785024, 19.86502061460857, 19.78113220880678, 19.677203017928953, 19.55333833537061, 19.40966365169799, 19.246324527510243, 19.063486445968188, 18.861334645138946, 18.640073930326405, 18.39992846657756, 18.141141551575032, 17.863975369145777, 17.568710723635768, 17.255646755419708, 16.925100637834095, 16.577407255840527, 16.212918866744978, 15.832004743316626, 15.435050799667964, 15.022459200275, 14.594647952533888, 14.152050483266645, 13.695115199605024, 13.2243050346975, 12.740096978699476, 12.242981595522055, 11.733462525828823, 11.212055976784281, 10.679290199070756, 10.135704951703774, 9.581850955187997, 9.018289333567836, 8.445591045937874, 7.86433630798924, 7.275114004177816, 6.678521091109944, 6.075161992749953, 5.465647988062376, 4.850596591709154, 4.230630928429297, 3.6063791017348503, 2.9784735575626757, 2.3475504435268717, 1.7142489644208867, 1.0792107346223752, 0.44307912805674976, 0.19350137362182113, 0.829885833971738, 1.4654295151659982, 2.0994885311930926, 2.731420500194814, 3.3605851952802257, 3.986345193156318, 4.608066519918359, 5.225119293345488, 5.836878361050961, 6.44272393384048, 7.042042213636877, 7.63422601533515, 8.218675381957603, 8.794798192486061, 9.3620107617552, 9.919738431799397, 10.467416154053764, 11.004489061819742, 11.530413032415103, 12.044655238438986, 12.546694687593318, 13.03602275051382, 13.512143676075766, 13.974575093652511, 14.422848501817834]),
]

BAD_PLOTS = [
    ([1, 2, 3, 4], [2, 3, 1]),  # x,y not the same size
]

# Not proper X => should show warning
INCORRECT_PLOTS = [
    ([0, 1, 3, 3, 4, 5], [0, 0, 5, 2, 4, 0]),  # Duplicated X
    ([6, 5, 8], [2, 3, 1]),  # X not ordered
]

RANGED_PLOTS = [
    ((0, 10), (0, 10), [3, 5, 8], [2, 3, 1]),
    ((1, 5), (1, 4), [2, 3, 4], [2, 3, 1]),
    ((-1, 6), (0, 10), [0, 1, 2, 3, 4, 5], [0, 0, 5, 2, 4, 0]),
    ((-1, 128), (0, 2000), [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126, 127], [15, 29, 29, 34, 42, 48, 62, 64, 71, 88, 94, 95, 104, 117, 124, 126, 140, 144, 155, 158, 158, 172, 186, 205, 214, 226, 234, 244, 248, 265, 280, 299, 312, 314, 317, 321, 333, 335, 337, 343, 346, 346, 352, 370, 379, 384, 392, 411, 413, 431, 438, 453, 470, 477, 487, 495, 509, 512, 519, 527, 535, 544, 550, 555, 561, 574, 579, 582, 601, 605, 616, 619, 620, 633, 642, 658, 668, 687, 702, 716, 732, 745, 763, 779, 780, 780, 793, 803, 815, 815, 832, 851, 851, 866, 873, 890, 896, 906, 918, 919, 921, 922, 933, 934, 949, 949, 952, 963, 974, 974, 989, 989, 1002, 1012, 1031, 1046, 1053, 1062, 1066, 1074, 1085, 1092, 1097, 1097, 1098, 1103, 1105, 1116]),
    ((10, 1400), (0, 40), [17, 36, 40, 43, 44, 62, 79, 83, 99, 104, 116, 133, 147, 152, 171, 185, 193, 195, 201, 210, 225, 241, 246, 254, 269, 270, 272, 280, 286, 304, 323, 336, 344, 345, 351, 355, 374, 381, 400, 408, 425, 444, 449, 456, 466, 482, 489, 506, 507, 516, 526, 542, 561, 576, 581, 593, 595, 602, 604, 618, 633, 639, 647, 656, 667, 670, 689, 691, 705, 721, 725, 738, 750, 767, 768, 776, 786, 797, 809, 815, 832, 840, 857, 867, 869, 878, 889, 892, 905, 907, 915, 934, 952, 957, 971, 985, 1003, 1019, 1032, 1042, 1046, 1058, 1077, 1089, 1100, 1104, 1109, 1121, 1124, 1127, 1132, 1145, 1148, 1155, 1170, 1171, 1183, 1184, 1196, 1208, 1214, 1229, 1235, 1236, 1239], [0.0, 0.6365122726989454, 1.2723796780808552, 1.906958002160726, 2.53960433695571, 3.1696777318320972, 3.7965398428692474, 4.419555579582602, 5.0380937483505495, 5.651527691893277, 6.259235924155733, 6.860602759951489, 7.455018938729595, 8.041882241832447, 8.620598102619352, 9.19058020883762, 9.75125109663092, 10.30204273558309, 10.842397104204696, 11.371766755279262, 11.889615370496443, 12.395418303810198, 12.888663112971502, 13.368850078697054, 13.835492710948023, 14.28811824180589, 14.72626810444606, 15.149498397723974, 15.557380335903012, 15.949500683068614, 16.325462171788427, 16.68488390559441, 17.027401744879036, 17.352668675814723, 17.660355161922638, 17.9501494779348, 18.22175802561112, 18.474905631191508, 18.70933582418163, 18.924811097189927, 19.12111314655259, 19.29804309350273, 19.4554216856597, 19.593089478634386, 19.710906997566514, 19.8087548784303, 19.886533988965265, 19.94416552910976, 19.98159111083536, 19.998772817301322, 19.995693241269127, 19.972355502738214, 19.928783245785024, 19.86502061460857, 19.78113220880678, 19.677203017928953, 19.55333833537061, 19.40966365169799, 19.246324527510243, 19.063486445968188, 18.861334645138946, 18.640073930326405, 18.39992846657756, 18.141141551575032, 17.863975369145777, 17.568710723635768, 17.255646755419708, 16.925100637834095, 16.577407255840527, 16.212918866744978, 15.832004743316626, 15.435050799667964, 15.022459200275, 14.594647952533888, 14.152050483266645, 13.695115199605024, 13.2243050346975, 12.740096978699476, 12.242981595522055, 11.733462525828823, 11.212055976784281, 10.679290199070756, 10.135704951703774, 9.581850955187997, 9.018289333567836, 8.445591045937874, 7.86433630798924, 7.275114004177816, 6.678521091109944, 6.075161992749953, 5.465647988062376, 4.850596591709154, 4.230630928429297, 3.6063791017348503, 2.9784735575626757, 2.3475504435268717, 1.7142489644208867, 1.0792107346223752, 0.44307912805674976, 0.19350137362182113, 0.829885833971738, 1.4654295151659982, 2.0994885311930926, 2.731420500194814, 3.3605851952802257, 3.986345193156318, 4.608066519918359, 5.225119293345488, 5.836878361050961, 6.44272393384048, 7.042042213636877, 7.63422601533515, 8.218675381957603, 8.794798192486061, 9.3620107617552, 9.919738431799397, 10.467416154053764, 11.004489061819742, 11.530413032415103, 12.044655238438986, 12.546694687593318, 13.03602275051382, 13.512143676075766, 13.974575093652511, 14.422848501817834]),
    ((10, 3000000), (20, 3000000), list(range(20, 1800000, 50000)), list(range(20, 1800000, 50000))),
]

BAD_RANGED_PLOTS = [
    ((5, 10), (0, 10), [3, 5, 8], [2, 3, 1]),
    ((0, 5), (0, 10), [3, 5, 8], [2, 3, 1]),
    ((5, 5), (0, 10), [3, 5, 8], [2, 3, 1]),
]


class ViewportTestCase(test.GuiTestCase):

    frame_class = test.test_gui.xrccanvas_frame

    def _generate_sine_list(self, period, amp=1):

        sine_list = []
        step_size = (math.pi * 2) / period

        for i in range(period):
            sine_list.append(math.sin(i * step_size) * amp)

        return deque(sine_list)

#     @unittest.skip("simple")
    def test_threaded_plot(self):

        vwp = viewport.PointSpectrumViewport(self.panel)
        vwp.canvas.SetBackgroundColour("#333")
        vwp.canvas.SetForegroundColour("#A0CC27")
        self.add_control(vwp, wx.EXPAND, proportion=1)

        vwp.canvas.set_plot_mode(canvas.PLOT_MODE_BAR)
        vwp.canvas.set_plot_mode(canvas.PLOT_MODE_LINE)
        # vwp.canvas.set_plot_mode(canvas.PLOT_MODE_POINT)

        data_size = 100
        xs = range(data_size)
        ys = self._generate_sine_list(data_size)

        is_done = threading.Event()

        def rotate(q, v):
            # v.bottom_legend.unit = 'm'
            scale = 1.001

            timeout = time.time() + 8
            while time.time() < timeout:
                v.canvas.set_1d_data(xs, ys, unit_x='m', unit_y='g')
                q[-1] *= scale
                q.rotate(1)

                v.bottom_legend.range = (min(xs), max(xs))
                v.bottom_legend.SetToolTip(u"Time (s)")

                v.left_legend.range = (min(ys), max(ys))
                v.left_legend.SetToolTip(u"Count per second")

                time.sleep(0.01)

            is_done.set()

        t = threading.Thread(target=rotate, args=(ys, vwp))
        # Setting Daemon to True, will cause the thread to exit when the parent does
        t.setDaemon(True)
        t.start()

        for i in range(10):  # Fail after 10s not yet finished
            test.gui_loop(1)
            if is_done.is_set():
                return

        self.assertTrue(is_done.is_set())

#     @unittest.skip("simple")
    def test_plot_viewport(self):

#         vwp = viewport.PlotViewport(self.panel)
        vwp = viewport.PointSpectrumViewport(self.panel)
        vwp.canvas.SetBackgroundColour("#333")
        self.add_control(vwp, wx.EXPAND, proportion=1)
        vwp.canvas.SetForegroundColour("#27C4CC")

        for mode in MODES:
            vwp.canvas.set_plot_mode(mode)

            """
            # Note: With the new version of the plotting canvas, which can
            # be navigated, all ranges will be accepted, and no ValueError is raised
            for plot in BAD_RANGED_PLOTS:
                with self.assertRaises(ValueError):
                    logging.debug("Testing range X = %s, range Y = %s", plot[0], plot[1])
                    vwp.canvas.set_1d_data(plot[2],
                                           plot[3],
                                           range_x=plot[0],
                                           range_y=plot[1])
                    vwp.canvas.draw()
                    test.gui_loop(0.3)
            """

            vwp.Refresh()

            for plot in BAD_PLOTS:
                with self.assertRaises(ValueError):
                    vwp.canvas.set_1d_data(plot[0], plot[1])
                    vwp.canvas.draw()
                    test.gui_loop(0.1)

            vwp.Refresh()

            for plot in INCORRECT_PLOTS:
                vwp.canvas.set_1d_data(plot[0], plot[1])
                vwp.bottom_legend.range = (min(plot[0]), max(plot[0]))
                vwp.left_legend.range = (min(plot[1]), max(plot[1]))
                test.gui_loop(0.1)

            vwp.Refresh()

            for plot in PLOTS:
                vwp.canvas.set_1d_data(plot[0], plot[1])
                vwp.bottom_legend.range = (min(plot[0]), max(plot[0]))
                vwp.left_legend.range = (min(plot[1]), max(plot[1]))
                test.gui_loop(0.1)

            vwp.Refresh()

            for plot in RANGED_PLOTS[:-1]:
                vwp.canvas.set_1d_data(plot[2],
                                       plot[3],
                                       range_x=plot[0],
                                       range_y=plot[1])
                vwp.bottom_legend.range = (min(plot[0]), max(plot[0]))
                vwp.left_legend.range = (min(plot[1]), max(plot[1]))
                test.gui_loop(0.1)

            # Test setting ranges
            for plot in RANGED_PLOTS[:-1]:
                range_x = plot[0]
                range_y = plot[1]
                # data width and height
                w = abs(range_x[1] - range_x[0])
                h = abs(range_y[1] - range_y[0])

                vwp.canvas.set_1d_data(plot[2],
                                       plot[3],
                                       range_x=range_x,
                                       range_y=range_y)
                vwp.bottom_legend.range = (min(plot[0]), max(plot[0]))
                vwp.left_legend.range = (min(plot[1]), max(plot[1]))

                # Test setting bad ranges
                test_xrange = (range_x[1], range_x[0])
                with self.assertRaises(ValueError):
                    vwp.hrange.value = test_xrange

                test_yrange = (range_y[1], range_y[0])
                with self.assertRaises(ValueError):
                    vwp.vrange.value = test_yrange

                # Test setting ranges that fall within the data ranges
                test_xrange = (range_x[0] + w * 0.2, range_x[1] - w * 0.2)
                vwp.hrange.value = test_xrange
                self.assertEqual(vwp.hrange.value, test_xrange)

                test_yrange = (range_y[0] + h * 0.2, range_y[1] - h * 0.2)
                vwp.vrange.value = test_yrange
                self.assertEqual(vwp.vrange.value, test_yrange)

                test.gui_loop(0.1)

            vwp.Refresh()

    def test_spatialspectrum_viewport(self):
        vwp = viewport.LineSpectrumViewport(self.panel)

        self.add_control(vwp, wx.EXPAND, proportion=1)


class MicroscopeViewportTestCase(test.GuiTestCase):
    frame_class = test.test_gui.xrccanvas_frame

    def test_resize(self):

        self.tmodel = self.create_simple_tab_model()
        self.view = self.tmodel.focussedView.value

        vwp = viewport.MicroscopeViewport(self.panel)
        self.add_control(vwp, wx.EXPAND, proportion=1)

        orig_mpp = 1e-6  # m/px (default)
        self.view.mpp.value = orig_mpp

        vwp.setView(self.view, self.tmodel)
        self.canvas = vwp.canvas

        # check the initial size of the buffer field of view
        test.gui_loop(0.1)
        fov = self.view.fov.value
        fov_buf = self.view.fov_buffer.value

        # check the new values of fov_buffer after changing .mpp (=zooming out)
        self.view.mpp.value *= 10
        exp_fov = fov[0] * 10, fov[1] * 10
        exp_fovb = fov_buf[0] * 10, fov_buf[1] * 10
        test.gui_loop(0.1)
        numpy.testing.assert_almost_equal(exp_fov, self.view.fov.value)
        numpy.testing.assert_almost_equal(exp_fovb, self.view.fov_buffer.value)

        # Increase the canvas size by 2 in both dim
        # -> FoV stays the same
        # -> FoV buffer is the same or smaller than before (because the margins are fixed)
        # -> MPP decreases by 2
        big_mpp = self.view.mpp.value
        fsize = self.frame.Size
        csize = self.canvas.Size
        csize_big = int(csize[0] * 2), int(csize[1] * 2)
        csize_diff = csize_big[0] - csize[0], csize_big[1] - csize[1]
        self.frame.SetSize((fsize[0] + csize_diff[0], fsize[1] + csize_diff[1]))
        test.gui_loop(0.1)
        self.assertEqual(csize_big, tuple(self.canvas.Size))

        self.assertAlmostEqual(self.view.mpp.value, big_mpp / 2)
        numpy.testing.assert_almost_equal(exp_fov, self.view.fov.value)
        new_fovb = self.view.fov_buffer.value
        self.assertLessEqual(new_fovb[0], exp_fovb[0])
        self.assertLessEqual(new_fovb[1], exp_fovb[1])


if __name__ == "__main__":
    unittest.main()
