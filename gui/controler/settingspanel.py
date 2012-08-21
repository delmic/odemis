# -*- coding: utf-8 -*-

import inspect

import wx

# FIXME: when we import odemis.model, isinstance checking doesn't work
# for some reason.
import model

from odemis.gui.comp.foldpanelbar import FoldPanelItem
from odemis.gui.log import log

MAIN_FRAME = None

class SettingsPanel(object):
    """ Settings base class """

    def __init__(self, fp_panel, default_msg):
        self.fb_panel = fp_panel
        assert isinstance(self.fb_panel, FoldPanelItem)

        self.panel = wx.Panel(self.fb_panel)
        self.panel.SetBackgroundColour("#333333")
        self.panel.SetForegroundColour("#666666")

        self._sizer = wx.GridBagSizer()
        self._sizer.Add(wx.StaticText(self.panel, -1, default_msg),
                        (0, 0), flag=wx.ALL, border=5)

        self.panel.SetSizer(self._sizer)
        self.fb_panel.add_item(self.panel)

        self.num_values = 0

    def add_ro_value(self, label="", value=None):
        if self.num_values == 0:
            self.panel.SetForegroundColour("#DDDDDD")
            self.panel.GetChildren()[0].Destroy()

        self._sizer.Add(wx.StaticText(self.panel, -1, "%s:" % label),
                        (self.num_values, 0), flag=wx.ALL, border=5)
        self._sizer.Add(wx.StaticText(self.panel, -1, unicode(value)),
                        (self.num_values, 1), flag=wx.ALL, border=5)
        self.num_values += 1
        self.fb_panel.Parent.Layout()


    def add_value(self, label="", value=None):
        if self.num_values == 0:
            self.panel.SetForegroundColour("#DDDDDD")
            self.panel.GetChildren()[0].Destroy()


        self._sizer.Add(wx.StaticText(self.panel, -1, "%s:" % label),
                        (self.num_values, 0), flag=wx.ALL, border=5)
        if value:
            self._sizer.Add(wx.StaticText(self.panel, -1, unicode(value)),
                            (self.num_values, 1), flag=wx.ALL, border=5)
        self.num_values += 1
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
        self._optical_panel.add_ro_value("Camera", comp.name)

        vigil_attrs = []

        for name, value in inspect.getmembers(comp, lambda x: isinstance(x, model.VigilantAttributeBase)):
            log.warn(name)
            self._optical_panel.add_value(name, value)

