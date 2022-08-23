# -*- coding: utf-8 -*-
"""
:created: 2015-02-20
:author: Rinze de Laat
:copyright: © 2015 Rinze de Laat, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
    General Public License version 2 as published by the Free Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
    the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
    Public License for more details.

    You should have received a copy of the GNU General Public License along with Odemis. If not,
    see http://www.gnu.org/licenses/.

"""
from odemis.gui.comp.slider import UnitFloatSlider
import time
import unittest
import wx

import odemis.gui.test as test
import odemis.gui.util.widgets as widgets
import odemis.model as model


# test.goto_manual()


class ConnectorTestCase(test.GuiTestCase):

    frame_class = test.test_gui.xrcbutton_frame

    def test_va_connector(self):
        va = model.FloatContinuous(0.3, (0.0, 1.0))

        slider = UnitFloatSlider(self.panel, value=0.5, size=(-1, 18), unit="s",
                                 min_val=0.0, max_val=1.0)

        self.add_control(slider, flags=wx.EXPAND | wx.ALL)

        self.assertEqual(slider.GetValue(), 0.5)

        test.gui_loop(0.5)

        # Setting and getting the value directly should give the same value
        slider.SetValue(0.6)
        test.gui_loop(0.2)
        self.assertEqual(slider.GetValue(), 0.6)

        # After connecting the VA the control should have the same value as the VA
        con = widgets.VigilantAttributeConnector(va, slider, events=wx.EVT_SLIDER)
        test.gui_loop(0.2)
        self.assertEqual(slider.GetValue(), 0.3)

        # Chaning the VA changes the control value
        va.value = 0.8
        test.gui_loop(0.2)
        self.assertEqual(slider.GetValue(), 0.8)

        # When pausing the VA, the control value should not change when the VA's does
        con.pause()
        va.value = 0.9
        test.gui_loop(0.2)
        self.assertEqual(slider.GetValue(), 0.8)

        # Resuming the connection should update the control to the VA's current value
        con.resume()
        test.gui_loop(0.2)
        self.assertEqual(slider.GetValue(), 0.9)

        # When the control is manipulated, the VA's value is updated
        slider.SetValue(0.1)
        slider._send_slider_update_event()  # Simulate a real user generated event
        test.gui_loop(0.2)
        self.assertEqual(va.value, 0.1)

        # When the connection is paused, changes in the control are not passed to the VA
        con.pause()
        slider.SetValue(0.2)
        slider._send_slider_update_event()  # Simulate a real user generated event
        test.gui_loop(0.2)
        self.assertEqual(va.value, 0.1)

        # Resuming causes the value of the **VA** to be passed to the control
        con.resume()
        test.gui_loop(0.2)
        self.assertEqual(slider.GetValue(), 0.1)

    def test_pf_connector(self):
        """
        Test ProgressiveFutureConnector
        """
        # Add a gauge (progress bar) and label for testing
        gauge = wx.Gauge(self.panel)
        stxt = wx.StaticText(self.panel)
        self.add_control(gauge, flags=wx.EXPAND | wx.ALL)
        self.add_control(stxt, flags=wx.EXPAND | wx.ALL)

        test.gui_loop(0.2)

        # Create the ProgressiveFuture
        now = time.time()
        pf = model.ProgressiveFuture(now, now + 60)  # one min
        # future.task_canceller = self.cancel_task

        # Create the connector
        pfc = widgets.ProgressiveFutureConnector(pf, bar=gauge, label=stxt)
        test.gui_loop(0.3)  # need to wait at least 0.25 s

        # Check ratio at beginning
        r1 = gauge.Value / gauge.Range
        self.assertLessEqual(r1, 0.1)
        self.assertGreater(len(stxt.LabelText), 6)

        # wait 2 s and see if the progress increased
        test.gui_loop(2)
        r2 = gauge.Value / gauge.Range
        self.assertGreater(r2, r1)

        # Make it look a lot longer => should update backwards
        pf.set_progress(end=now + 120)
        test.gui_loop(0.3)  # need to wait at least 0.25 s
        r3 = gauge.Value / gauge.Range
        self.assertLess(r3, r2)

        # wait 2 s and see if the progress increased
        test.gui_loop(2)
        r4 = gauge.Value / gauge.Range
        self.assertGreater(r4, r3)

        # Make it look a little longer => should not update
        pf.set_progress(end=now + 121)
        test.gui_loop(0.3)  # need to wait at least 0.25 s
        r5 = gauge.Value / gauge.Range
        self.assertEqual(r5, r4)

        # TODO: support cancelling the task
        # End the task (early)
        # pf.cancel()
        # test.gui_loop(0.3)  # need to wait at least 0.25 s
        # r6 = gauge.Value / gauge.Range
        # self.assertEqual(r6, 1)


if __name__ == "__main__":
    unittest.main()
