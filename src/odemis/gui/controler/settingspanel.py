#-*- coding: utf-8 -*-
'''
@author: Rinze de Laat 

Copyright © 2012 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
import math
import re

import wx

import odemis.gui.comp.text as text

from ..comp.foldpanelbar import FoldPanelItem
from odemis.gui.log import log
from odemis.gui.comp.slider import CustomSlider
from odemis.model import getVAs, NotApplicableError



MAIN_FRAME = None

BACKGROUND_COLOUR = "#333333"
FOREGROUND_COLOUR = "#DDDDDD"
FOREGROUND_COLOUR_DIS = "#666666"

class SettingsPanel(object):
    """ Settings base class which describes an indirect wrapper for
    FoldPanelItems.

    Do not instantiate this class, but always inherit it.
    """

    def __init__(self, fp_panel, default_msg):
        self.fb_panel = fp_panel
        assert isinstance(self.fb_panel, FoldPanelItem)

        self.panel = wx.Panel(self.fb_panel)
        # FIXME: Move hardcoded layout options to a more suitable place
        self.panel.SetBackgroundColour(BACKGROUND_COLOUR)
        self.panel.SetForegroundColour(FOREGROUND_COLOUR)

        self._sizer = wx.GridBagSizer()

        self.panel.SetForegroundColour(FOREGROUND_COLOUR_DIS)
        self._sizer.Add(wx.StaticText(self.panel, -1, default_msg),
                        (0, 1), flag=wx.ALL, border=5)
        self.panel.SetForegroundColour(FOREGROUND_COLOUR)

        self.panel.SetSizer(self._sizer)
        self.fb_panel.add_item(self.panel)

        self._sizer.AddGrowableCol(1)

        self.num_entries = 0

    def _clear(self):
        # Remove default 'no content' label
        if self.num_entries == 0:
            self.panel.GetChildren()[0].Destroy()

    def add_label(self, label, value=None):
        self._clear()
         # Create label
        self._sizer.Add(wx.StaticText(self.panel, -1, "%s:" % label),
                        (self.num_entries, 0), flag=wx.ALL, border=5)

        if value:
            self.panel.SetForegroundColour(FOREGROUND_COLOUR_DIS)

            self._sizer.Add(wx.StaticText(self.panel, -1, unicode(value)),
                            (self.num_entries, 1),
                            flag=wx.ALL,
                            border=5)
            self.panel.SetForegroundColour(FOREGROUND_COLOUR)
        self.num_entries += 1


    def add_value(self, label, value):
        self._clear()

        # Create label

        label = re.sub(r"([A-Z])", r" \1", label)
        label = label.capitalize()

        self._sizer.Add(wx.StaticText(self.panel, -1, "%s:" % label),
                        (self.num_entries, 0), flag=wx.ALL, border=5)

        if not value:
            log.warn("No value provided for %s", label)
            return

        unit =  value.unit or ""

        if value.readonly:
            self.panel.SetForegroundColour(FOREGROUND_COLOUR_DIS)

            if isinstance(value.value, tuple):
                lbl = " x ".join(["%s %s" % (v, unit) for v in value.value])
            else:
                lbl = unicode("%s %s" % (value.value, unit))

            self._sizer.Add(wx.StaticText(self.panel, -1, lbl),
                            (self.num_entries, 1), flag=wx.ALL, border=5)
            self.panel.SetForegroundColour(FOREGROUND_COLOUR)
        else:

            has_choices = True
            try:
                log.debug("%s has choices %s", label, value.choices)
            except (AttributeError, NotApplicableError):
                has_choices = False

            has_range = True

            try:
                log.debug("%s has range %s", label, value.range)
            except (AttributeError, NotApplicableError):
                has_range = False

            val  = value.value
            ctrl = None

            if has_choices:
                pass
            elif has_range:
                if isinstance(val, (int, float)):
                    ctrl = CustomSlider(self.panel, value=val,
                                        val_range=value.range,
                                        size=(30, 15),
                                        pos=(-1, 10),
                                        style=wx.SL_HORIZONTAL)

                    # Experience showed that 255 steps is a nice number
                    # for keyboard inc/dec of the value
                    step = (value.range[1] - value.range[0]) / 255.0
                    # To keep the inc/dec values 'clean', set the step
                    # value to the nearest power of 10
                    step = 10 ** round(math.log10(step))

                    if isinstance(val, int):
                        klass = text.UnitIntegerCtrl
                        # We want an integer.
                        step = max(step, 1)
                    else:
                        klass = text.UnitFloatCtrl

                    txt = klass(self.panel, -1,
                                val,
                                style=wx.NO_BORDER,
                                size=(60, -1),
                                min_val=value.range[0],
                                max_val=value.range[1],
                                unit=unit,
                                accuracy=2,
                                step=step)

                    txt.SetForegroundColour("#2FA7D4")
                    txt.SetBackgroundColour(self.panel.GetBackgroundColour())

                    ctrl.set_linked_field(txt)
                    txt.set_linked_slider(ctrl)

                    self._sizer.Add(txt, (self.num_entries, 2),
                            flag=wx.ALL, border=5)

                # if isinstance(val, int):
                #     ctrl = text.UnitIntegerCtrl(self.panel,
                #                                 -1,
                #                                 val,
                #                                 unit=unit,
                #                                 min_val=value.range[0],
                #                                 max_val=value.range[1])
                # elif isinstance(val, float):
                #      ctrl = text.UnitFloatCtrl(self.panel,
                #                               -1,
                #                               val,
                #                               unit=unit,
                #                               min_val=value.range[0],
                #                               max_val=value.range[1])

            # Default representation
            if not ctrl:
                lbl = unicode("%s %s" % (val, unit))
                ctrl = wx.StaticText(self.panel, -1, lbl)

            self._sizer.Add(ctrl, (self.num_entries, 1),
                            flag=wx.ALL|wx.EXPAND, border=5)

        self.num_entries += 1

        self.fb_panel.Parent.Layout()


class StreamPanel(object):
    pass

class SemSettingsPanel(SettingsPanel):
    pass

class OpticalSettingsPanel(SettingsPanel):
    pass

class SettingsSideBar(object):
    """ The main controller class for the settigns panel in the live view.

    This class can be used to set, get and otherwise manipulate the content
    of the setting panel.
    """

    def __init__(self, main_frame):
        global MAIN_FRAME
        MAIN_FRAME = main_frame

        self._stream_panel = StreamPanel()
        self._sem_panel = SemSettingsPanel(
                                    MAIN_FRAME.fp_sem_settings,
                                    "No SEM found")
        self._optical_panel = OpticalSettingsPanel(
                                    MAIN_FRAME.fp_optical_settings,
                                    "No optical microscope found")

    # Optical microscope settings

    def add_ccd(self, comp):
        self._optical_panel.add_label("Camera", comp.name)

        vigil_attrs = getVAs(comp)

        for name, value in vigil_attrs.iteritems():
            log.warn("%s %s", value.value, type(value.value))
            self._optical_panel.add_value(name, value)


