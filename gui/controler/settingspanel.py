# -*- coding: utf-8 -*-

import wx

from odemis.gui.comp.foldpanelbar import FoldPanelItem
from odemis.gui.log import log

MAIN_FRAME = None

class SettingsPanel(object):
    """ Settings base class """

    def __init__(self):
        assert isinstance(self.fb_panel, FoldPanelItem)

        self.panel = wx.Panel(self.fb_panel)
        self.panel.SetBackgroundColour("#333333")
        self.panel.SetForegroundColour("#DDDDDD")
        self._sizer = wx.GridBagSizer()
        self.panel.SetSizer(self._sizer)

        self.fb_panel.add_item(self.panel)

class StreamPanel(object):
    pass

class SemSettingsPanel(SettingsPanel):
    def __init__(self):
        self.fb_panel = MAIN_FRAME.fp_sem_settings
        super(SemSettingsPanel, self).__init__()

class OpticalSettingsPanel(SettingsPanel):

    def __init__(self):
        self.fb_panel = MAIN_FRAME.fp_optical_settings
        super(OpticalSettingsPanel, self).__init__()


    def add_ccd(self, comp):
        log.debug("Adding CCD")
        self._sizer.Add(wx.StaticText(self.panel, -1, ":D"), (0, 0))
        self.fb_panel.Parent.Layout()

class SettingsSideBar(object):
    """ The main controller class for the settigns panel in the live view.

    This class can be used to set, get and otherwise manipulate the content
    of the setting panel.
    """

    def __init__(self, main_frame):
        global MAIN_FRAME
        MAIN_FRAME = main_frame

        self._stream_panel = StreamPanel()
        self._sem_panel = SemSettingsPanel()
        self._optical_panel = OpticalSettingsPanel()

    # Optical microscope settings

    def add_ccd(self, comp):
        self._optical_panel.add_ccd(comp)
