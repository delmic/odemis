#-*- coding: utf-8 -*-
"""
:author:    Rinze de Laat
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

import wx
import odemis.gui.comp.slider as slider
import odemis.gui.comp.text as text
import odemis.gui as gui



class SpectrogramSlider(wx.Panel):
    """ This class combines a BandwidthSlider and center/bandwidth value
    UnitFloatCtrl text fields into one logical control.

    Note: The values for the center and the bandwidth are only obtusely limited,
    meaning that values should be more intelligently limited elsewhere, e.g. in
    a VigilantAttribute.
    """

    def __init__(self, parent, wid=wx.ID_ANY, value=(390e-9, 700e-9),
             min_val=390e-9, max_val=700e-9, size=(-1, -1),
             pos=wx.DefaultPosition, style=wx.NO_BORDER,
             name="SpectrogramSlider",):

        wx.Panel.__init__(self, parent, wid, pos, size, style, name)

        font = wx.Font(8, wx.FONTFAMILY_DEFAULT,
                          wx.FONTSTYLE_NORMAL,
                          wx.FONTWEIGHT_NORMAL)
        self.SetFont(font)

        # Slider
        self.slider = slider.BandwidthSlider(self,
                                             value=value,
                                             min_val=min_val,
                                             max_val=max_val)

        self.slider.SetBackgroundColour(gui.BACKGROUND_COLOUR)
        self.slider.SetForegroundColour(gui.FOREGROUND_COLOUR)

        main_sizer = wx.BoxSizer(wx.VERTICAL)
        main_sizer.Add(self.slider, 0, flag=wx.EXPAND)

        text_sizer = wx.BoxSizer(wx.HORIZONTAL)

        # Center Label

        center_label = wx.StaticText(self, label="Center:")
        center_label.SetForegroundColour(gui.FOREGROUND_COLOUR)
        text_sizer.Add(center_label, border=3, flag=wx.ALL)

        # Center Text Control
        bandwidth_val = self.slider.get_bandwidth_value()

        center_val = self.slider.get_center_value()
        # center_min = max(min_val + (bandwidth_val / 2), min_val)
        # center_max = min(max_val - (bandwidth_val / 2), max_val)
        center_min = min_val
        center_max = max_val

        self.center_txt = text.UnitFloatCtrl(
                                        self,
                                        value=center_val,
                                        style=wx.NO_BORDER,
                                        size=(-1, 14),
                                        min_val=center_min,
                                        max_val=center_max,
                                        unit='m',
                                        accuracy=3)
        self.center_txt.SetForegroundColour(gui.FOREGROUND_COLOUR_EDIT)
        self.center_txt.SetBackgroundColour(gui.BACKGROUND_COLOUR)
        text_sizer.Add(self.center_txt, border=3, flag=wx.ALL)

        # Bandwidth Label

        bandwidth_label = wx.StaticText(self, label="Bandwidth:")
        bandwidth_label.SetForegroundColour(gui.FOREGROUND_COLOUR)
        text_sizer.Add(bandwidth_label, border=3, flag=wx.ALL)

        # Bandwidth Text Control

        self.bandwidth_txt = text.UnitFloatCtrl(
                                        self,
                                        value=bandwidth_val,
                                        style=wx.NO_BORDER,
                                        size=(-1, 14),
                                        min_val=0,
                                        max_val=max_val - min_val,
                                        unit='m',
                                        accuracy=3)
        self.bandwidth_txt.SetForegroundColour(gui.FOREGROUND_COLOUR_EDIT)
        self.bandwidth_txt.SetBackgroundColour(gui.BACKGROUND_COLOUR)
        text_sizer.Add(self.bandwidth_txt, border=3, flag=wx.ALL)

        main_sizer.Add(text_sizer, 0, flag=wx.EXPAND)

        self.SetSizer(main_sizer)

        self.center_txt.Bind(wx.EVT_COMMAND_ENTER, self._update_slider)
        self.bandwidth_txt.Bind(wx.EVT_COMMAND_ENTER, self._update_slider)


    def SetContent(self, content):
        self.slider.SetContent(content)

    def set_center_value(self, value):
        self.slider.set_center_value(value)
        self.center_txt.SetValue(value)

    def set_bandwidth_value(self, value):
        self.slider.set_bandwidth_value(value)
        self.bandwidth_txt.SetValue(value)

    def _update_slider(self, evt):
        """ Private event handler called when the slider should be updated, for
            example when a linked text field receives a new value.
        """

        # If the slider is not being dragged
        if not self.HasCapture():
            center_val = self.center_txt.GetValue()
            bandwi_val = self.bandwidth_txt.GetValue()

            self.slider.set_bandwidth_value(bandwi_val)
            self.slider.set_center_value(center_val)
            self.slider._send_slider_update_event()
            self.slider._send_scroll_event()
            evt.Skip()

# TODO: refactor both slider classes because they share a lot of code.

class HistogramSlider(wx.Panel):

    def __init__(self, parent, wid=wx.ID_ANY, value=(0, 255),
             min_val=0, max_val=255, size=(-1, -1),
             pos=wx.DefaultPosition, style=wx.NO_BORDER,
             name="HistogramSlider",):

        wx.Panel.__init__(self, parent, wid, pos, size, style, name)

        font = wx.Font(8, wx.FONTFAMILY_DEFAULT,
                          wx.FONTSTYLE_NORMAL,
                          wx.FONTWEIGHT_NORMAL)
        self.SetFont(font)

        # Slider
        self.slider = slider.VisualRangeSlider(self,
                                             value=value,
                                             min_val=min_val,
                                             max_val=max_val)

        self.slider.SetBackgroundColour(gui.BACKGROUND_COLOUR)
        self.slider.SetForegroundColour(gui.FOREGROUND_COLOUR)

        main_sizer = wx.BoxSizer(wx.VERTICAL)
        main_sizer.Add(self.slider, 0, flag=wx.EXPAND)

        text_sizer = wx.BoxSizer(wx.HORIZONTAL)

        # Center Label

        left_bound_label = wx.StaticText(self, label="Minimum:")
        left_bound_label.SetForegroundColour(gui.FOREGROUND_COLOUR)
        text_sizer.Add(left_bound_label, border=3, flag=wx.ALL)

        # Center Text Control

        left_bound, right_bound = self.slider.GetValue()

        self.left_bound_txt = text.IntegerTextCtrl(
                                        self,
                                        value=left_bound,
                                        style=wx.NO_BORDER,
                                        size=(-1, 14),
                                        min_val=min_val,
                                        max_val=max_val)
        self.left_bound_txt.SetForegroundColour(gui.FOREGROUND_COLOUR_EDIT)
        self.left_bound_txt.SetBackgroundColour(gui.BACKGROUND_COLOUR)
        text_sizer.Add(self.left_bound_txt, border=3, flag=wx.ALL)

        # Bandwidth Label

        right_bound_label = wx.StaticText(self, label="Maximum:")
        right_bound_label.SetForegroundColour(gui.FOREGROUND_COLOUR)
        text_sizer.Add(right_bound_label, border=3, flag=wx.ALL)

        # Bandwidth Text Control

        self.right_bound_txt = text.IntegerTextCtrl(
                                        self,
                                        value=right_bound,
                                        style=wx.NO_BORDER,
                                        size=(-1, 14),
                                        min_val=min_val,
                                        max_val=max_val)
        self.right_bound_txt.SetForegroundColour(gui.FOREGROUND_COLOUR_EDIT)
        self.right_bound_txt.SetBackgroundColour(gui.BACKGROUND_COLOUR)
        text_sizer.Add(self.right_bound_txt, border=3, flag=wx.ALL)

        main_sizer.Add(text_sizer, 0, flag=wx.EXPAND)

        self.SetSizer(main_sizer)

        self.left_bound_txt.Bind(wx.EVT_COMMAND_ENTER, self._update_slider)
        self.right_bound_txt.Bind(wx.EVT_COMMAND_ENTER, self._update_slider)


    def SetContent(self, content):
        self.slider.SetContent(content)


    def SetValue(self, value):
        self.slider.SetValue(value)
        l, r = value
        self.left_bound_txt.SetValue(l)
        self.right_bound_txt.SetValue(r)


    def GetValue(self, value):
        return self.slider.GetValue()

    def _update_slider(self, evt):
        """ Private event handler called when the slider should be updated, for
            example when a linked text field receives a new value.
        """

        # If the slider is not being dragged
        if not self.HasCapture():
            left_bound = self.left_bound_txt.GetValue()
            right_bound = self.right_bound_txt.GetValue()

            self.slider.SetValue((left_bound, right_bound))
            self.slider._send_slider_update_event()
            self.slider._send_scroll_event()
            evt.Skip()