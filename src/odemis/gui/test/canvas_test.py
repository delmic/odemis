#-*- coding: utf-8 -*-

"""
@author: Rinze de Laat

Copyright © 2013 Rinze de Laat, Delmic

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

#===============================================================================
# Test module for Odemis' gui.comp.buttons module
#===============================================================================

import unittest
import odemis.gui.comp.canvas as canvas
import odemis.gui.comp.canvas as canvas
import odemis.gui.comp.overlay as overlay

PLOTS = [
    ([0, 1, 2, 3, 4, 5], [2, 3, 7, 4, 8]),
    ([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126, 127], [15, 29, 29, 34, 42, 48, 62, 64, 71, 88, 94, 95, 104, 117, 124, 126, 140, 144, 155, 158, 158, 172, 186, 205, 214, 226, 234, 244, 248, 265, 280, 299, 312, 314, 317, 321, 333, 335, 337, 343, 346, 346, 352, 370, 379, 384, 392, 411, 413, 431, 438, 453, 470, 477, 487, 495, 509, 512, 519, 527, 535, 544, 550, 555, 561, 574, 579, 582, 601, 605, 616, 619, 620, 633, 642, 658, 668, 687, 702, 716, 732, 745, 763, 779, 780, 780, 793, 803, 815, 815, 832, 851, 851, 866, 873, 890, 896, 906, 918, 919, 921, 922, 933, 934, 949, 949, 952, 963, 974, 974, 989, 989, 1002, 1012, 1031, 1046, 1053, 1062, 1066, 1074, 1085, 1092, 1097, 1097, 1098, 1103, 1105, 1116]),
]

SCALES = [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0, 256.0]

VIEW_SIZE = (400, 400)

# View coordinates, with a top-left 0,0 origin
VIEW_COORDS = [
                (0, 0),
                (0, 349),
                (123, 0),
                (321, 322),
              ]

# Margin around the view
MARGINS = [(0, 0), (512, 512)]

# Buffer coordinates, with a top-left 0,0 origin
BUFF_COORDS = [
                (0, 0),
                (0, 349),
                (512 + 200, 512 + 200),
                (133, 0),
                (399, 399),
              ]

# The center of the buffer, in world coordinates
BUFFER_CENTER = [(0.0, 0.0)]

def gen_test_data():
    """ Help function to generate test data """
    from random import randrange

    sizes = (128, 256, 512, 1024, 2048)

    for _ in range(1):

        x_axis = []
        y_axis = []
        start = 0

        for s in sizes:
            for j in xrange(s):
                start += randrange(20)
                x_axis.append(j)
                y_axis.append(start)
            print "(%s, %s)," % (x_axis, y_axis)
        print ""

    import sys
    sys.exit()

class TestApp(wx.App):
    def __init__(self):
        odemis.gui.test.test_gui.get_resources = odemis_get_test_resources
        self.test_frame = None
        # gen_test_data()
        wx.App.__init__(self, redirect=False)

    def OnInit(self):
        self.test_frame = odemis.gui.test.test_gui.xrccanvas_frame(None)
        self.test_frame.SetSize((400, 400))
        self.test_frame.Center()
        self.test_frame.Layout()
        self.test_frame.Show()

        return True

def loop():
    app = wx.GetApp()
    if app is None:
        return

    while True:
        wx.CallAfter(app.ExitMainLoop)
        app.MainLoop()
        if not app.Pending():
            break

class CanvasTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = TestApp()
        cls.panel = cls.app.test_frame.canvas_panel
        cls.sizer = cls.panel.GetSizer()
        # NOTE!: Call Layout on the panel here, because otherwise the
        # controls layed out using XRC will not have the right sizes!
        loop()

    @classmethod
    def tearDownClass(cls):
        if not MANUAL:
            wx.CallAfter(cls.app.Exit)
        else:
            if INSPECT:
                from wx.lib import inspection
                inspection.InspectionTool().Show()
            cls.app.MainLoop()

    @classmethod
    def add_control(cls, ctrl, flags):
        cls.sizer.Add(ctrl, flag=flags|wx.ALL, border=5, proportion=1)
        return ctrl

    def test_plot_canvas(self):


        # Create and add a test plot canvas
        cnvs = canvas.PlotCanvas(self.panel)
        cnvs.SetBackgroundColour(wx.BLACK)
        cnvs.SetForegroundColour("#DDDDDD")
        cnvs.set_closed(canvas.CLOSE_STRAIGHT)
        self.add_control(cnvs, wx.EXPAND)

        loop()
        wx.MilliSleep(SLEEP_TIME)

        data = [
            (0.5, 0.5),
            (0.5, 4.5),
            (4.5, 4.5),
            (4.5, 0.5),
        ]

        loop()
        cnvs.set_2d_data(data)

        loop()
        wx.MilliSleep(SLEEP_TIME)

        cnvs.set_dimensions(0, 5, 0, 5)

        loop()
        wx.MilliSleep(SLEEP_TIME)

        cnvs.set_closed(canvas.CLOSE_BOTTOM)
        cnvs.reset_dimensions()

        loop()
        wx.MilliSleep(SLEEP_TIME)


        for plot in PLOTS:
            cnvs.set_1d_data(plot[0], plot[1])

            loop()
            wx.MilliSleep(SLEEP_TIME)


        ol = overlay.FocusLineOverlay(cnvs, "FocusLineOverlay")
        cnvs.add_ovelay(ol)

        # wx.MilliSleep(SLEEP_TIME)

        # data = [
        #     (0.1, 0.1),
        #     (0.1, 3.9),
        #     (3.9, 3.9),
        #     (3.9, 0.1),
        # ]

        # cnvs.set_2d_data(data)

        # loop()
        # cnvs.Update()

        # wx.MilliSleep(SLEEP_TIME)


    def test_buffer_to_world(self):

        for m in MARGINS:
            offset = tuple((x / 2) + y for x, y in zip(VIEW_SIZE, m))
            for bp in BUFF_COORDS:
                for s in SCALES:
                    for c in BUFFER_CENTER:
                        wp = canvas.buffer_to_world_pos(bp, c, s, offset)
                        nbp = canvas.world_to_buffer_pos(wp, c, s, offset)

                        err = ("{} -> {} -> {} "
                               "scale: {}, center: {}, offset: {}")
                        err = err.format(bp, wp, nbp, s, c, offset)
                        print err

                        self.assertAlmostEqual(bp[0], nbp[0], msg=err)
                        self.assertAlmostEqual(bp[1], nbp[1], msg=err)

if __name__ == "__main__":
    unittest.main()
