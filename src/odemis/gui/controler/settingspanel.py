#-*- coding: utf-8 -*-

import wx

from ..comp.foldpanelbar import FoldPanelItem
from odemis.gui.log import log
from odemis.model import getVAs, getROAttributes

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
                        (0, 0), flag=wx.ALL, border=5)
        self.panel.SetForegroundColour(FOREGROUND_COLOUR)

        self.panel.SetSizer(self._sizer)
        self.fb_panel.add_item(self.panel)

        self.num_entries = 0

    def _clear(self):
        # Remove default 'no content' label
        if self.num_entries == 0:
            self.panel.GetChildren()[0].Destroy()

    def add_label(self, label, value):
        self._clear()
         # Create label
        self._sizer.Add(wx.StaticText(self.panel, -1, "%s:" % label),
                        (self.num_entries, 0), flag=wx.ALL, border=5)

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
        self._sizer.Add(wx.StaticText(self.panel, -1, "%s:" % label),
                        (self.num_entries, 0), flag=wx.ALL, border=5)

        # If a value is provided
        if value:

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
                self._sizer.Add(wx.StaticText(self.panel, -1, unicode(value.value)),
                                (self.num_entries, 1), flag=wx.ALL, border=5)

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


