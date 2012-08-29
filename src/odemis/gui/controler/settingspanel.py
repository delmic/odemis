    #-*- coding: utf-8 -*-
"""
@author: Rinze de Laat

Copyright © 2012 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 2 of the License, or (at your option) any later
version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

import math
import re

import wx

import odemis.gui.comp.text as text

from ..comp.foldpanelbar import FoldPanelItem
from odemis.gui.log import log
from odemis.gui.comp.slider import CustomSlider
from odemis.model import getVAs, NotApplicableError, VigilantAttributeBase



MAIN_FRAME = None

# FIXME: Move hardcoded layout options to a more suitable place

BACKGROUND_COLOUR = "#333333"
FOREGROUND_COLOUR = "#DDDDDD"
FOREGROUND_COLOUR_DIS = "#666666"

CONTROL_NONE = 0    # No control needed or possible
CONTROL_LABEL = 1   # Static text for read only values
CONTROL_INT = 2     # Editable integer value
CONTROL_FLT = 3     # Editable float value
CONTROL_TEXT = 4    # Editable text value (with or without unit)
CONTROL_SLIDER = 5  # Value slider
CONTROL_RADIO = 6  # Choice buttons (like radio buttons)
CONTROL_COMBO = 7   # Drop down combo box

# Default settings for the different components.
# (Just a ccd for now, 2012-8-27)
# Values in the settings dictionary will be used to steer the default
# behaviours in represening values and the way in which they can be altered.
# All values are optional
# Format:
#   role
#       vigilant attribute
#           label
#           control_type
#           range
#           choices
#           unit
#           perc_to_val (a lambda function used in sliders)

SETTINGS = {
            "ccd":
            {
                "exposureTime":
                {
                    "control_type": CONTROL_SLIDER,
                    "range": (0.01, 3.00),
                    "scale": "exp",
                },
                "temperature":
                {

                },
                "binning":
                {
                    "control_type": CONTROL_INT,
                },
            }
           }

class VigilantAttributeConnector(object):

    def __init__(self, va, ctrl, sub_func):
        self.vigilattr = va
        self.ctrl = ctrl
        self.sub_func = sub_func

        self.vigilattr.subscribe(sub_func)

    def _connect_control_update(self):

        self.ctrl.Bind(wx.EVT_TEXT_ENTER, )

    def __del__(self):
        self.disconnect()

    def disconnect(self):
        self.vigilattr.unsubscribe(self.sub_func)



class SettingsPanel(object):
    """ Settings base class which describes an indirect wrapper for
    FoldPanelItems.

    Do not instantiate this class, but always inherit it.
    """

    def __init__(self, fp_panel, default_msg):
        self.fb_panel = fp_panel
        assert isinstance(self.fb_panel, FoldPanelItem)

        self.panel = wx.Panel(self.fb_panel)

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
        self.entries = []

    def _clear(self):
        # Remove default 'no content' label
        if self.num_entries == 0:
            self.panel.GetChildren()[0].Destroy()

    def _label_to_human(self, label):
        """ Converts a camel-case label into a human readible one
        """
        return re.sub(r"([A-Z])", r" \1", label).capitalize()

    def _determine_default_control(self, value):
        """ Determine the default control to use to represent a vigilant
        attribute in the settings panel.
        """
        if not value:
            log.warn("No value provided!")
            return CONTROL_NONE

        if value.readonly:
            return CONTROL_LABEL
        else:
            try:
                # This statement will raise an exception when no choices are
                # present
                log.debug("found choices %s", value.choices)

                max_items = 5
                max_len = 5
                # If there are too many choices, or their values are too long
                # in string representation, use a dropdown box

                choices_str = "".join([str(c) for c in value.choices])
                if len(value.choices) < max_items and \
                   len(choices_str) < max_items * max_len:
                    return CONTROL_RADIO
                else:
                    return CONTROL_COMBO
            except (AttributeError, NotApplicableError):
                pass

            try:
                # An exception will be raised if no range attribute is found
                log.debug("found range %s", value.range)
                if isinstance(value.value, (int, float)):
                    return CONTROL_SLIDER
            except (AttributeError, NotApplicableError):
                pass

            # Return default control
            return CONTROL_TEXT


    def add_label(self, label, value=None):
        """ Adds a label to the settings panel, accompanied by an immutable
        value if one's provided.
        """
        self._clear()
        # Create label
        lbl_ctrl = wx.StaticText(self.panel, -1, "%s:" % label)
        self._sizer.Add(lbl_ctrl, (self.num_entries, 0), flag=wx.ALL, border=5)

        value_ctrl = None

        if value:
            self.panel.SetForegroundColour(FOREGROUND_COLOUR_DIS)

            value_ctrl = wx.StaticText(self.panel, -1, unicode(value))
            self._sizer.Add(value_ctrl, (self.num_entries, 1),
                            flag=wx.ALL, border=5)
            self.panel.SetForegroundColour(FOREGROUND_COLOUR)

        self.num_entries += 1
        self.entries.append((lbl_ctrl, value_ctrl, value))

    def add_value(self, label, value, conf=None):
        """ Add a label/value pair to the settings panel.

        conf {dict}: Configuration items that may override default settings
        """
        assert isinstance(value, VigilantAttributeBase)

        # If no conf provided, set it to an empty dictionary
        conf = conf or {}

        # Remove any 'empty panel' warning
        self._clear()

        # Format label
        label = conf.get('label', self._label_to_human(label))
        # Add the label to the panel
        lbl_ctrl = wx.StaticText(self.panel, -1, "%s:" % label)
        self._sizer.Add(lbl_ctrl, (self.num_entries, 0), flag=wx.ALL, border=5)

        # If no value provided...
        if not value:
            log.warn("No value provided for %s", label)
            self.num_entries += 1
            self.fb_panel.Parent.Layout()
            self.entries.append((lbl_ctrl, None, None))
            return

        # Get unit from config, vattribute or use an empty one
        unit =  conf.get('unit', value.unit or "")

        control_type = conf.get('control_type',
                                self._determine_default_control(value))


        if control_type == CONTROL_NONE:
            # No value
            return

        elif control_type == CONTROL_LABEL:
            # Read only value
            # In this case the value need to be transformed into a string

            self.panel.SetForegroundColour(FOREGROUND_COLOUR_DIS)
            if isinstance(value.value, tuple):
                # Maximum number of chars per value
                txt = " x ".join(["%s %s" % (v, unit) for v in value.value])
            else:
                txt = u"%s %s" % (value.value, unit)
            new_ctrl = wx.StaticText(self.panel, -1, size=(200, -1))

            #value.subscribe(lambda v: new_ctrl.SetLabel(u"%s %s" % (v, unit)), True)

            self.panel.SetForegroundColour(FOREGROUND_COLOUR)

        elif control_type == CONTROL_SLIDER:

            rng = conf.get("range", value.range)

            new_ctrl = CustomSlider(self.panel, value=value.value,
                                    val_range=rng,
                                    size=(30, 15),
                                    pos=(-1, 10),
                                    style=wx.SL_HORIZONTAL,
                                    scale=conf.get('scale', None))

            step = (rng[1] - rng[0]) / 255.0
            # To keep the inc/dec values 'clean', set the step
            # value to the nearest power of 10
            step = 10 ** round(math.log10(step))

            if isinstance(value.value, int):
                log.debug("Adding int field to slider")
                klass = text.UnitIntegerCtrl
                # We want an integer.
                step = max(step, 1)
            else:
                log.debug("Adding float field to slider")
                klass = text.UnitFloatCtrl

            txt = klass(self.panel, -1,
                        value.value,
                        style=wx.NO_BORDER,
                        size=(60, -1),
                        min_val=rng[0],
                        max_val=rng[1],
                        unit=unit,
                        accuracy=2,
                        step=step)

            txt.SetForegroundColour("#2FA7D4")
            txt.SetBackgroundColour(self.panel.GetBackgroundColour())

            new_ctrl.set_linked_field(txt)
            txt.set_linked_slider(new_ctrl)

            self._sizer.Add(txt, (self.num_entries, 2), flag=wx.ALL, border=5)

        elif control_type == CONTROL_INT:
            rng = conf.get("range", (None, None))
            new_ctrl = text.UnitIntegerCtrl(self.panel,
                                            -1,
                                            style=wx.NO_BORDER,
                                            unit=unit,
                                            min_val=rng[0],
                                            max_val=rng[1])
            new_ctrl.SetForegroundColour("#2FA7D4")
            new_ctrl.SetBackgroundColour(self.panel.GetBackgroundColour())

            #value.subscribe(lambda v: new_ctrl.SetValue(v))


                #new_ctrl.SetValue("AAAAAA")

            value.subscribe(lambda v: log.warn("lambda"))
            value.subscribe(set_on_notify)
            f = get_func(new_ctrl.SetValue)
            #self.entries.append(f)
            value.subscribe(f)


        elif control_type == CONTROL_FLT:
            new_ctrl = text.UnitFloatCtrl(self.panel,
                                         -1,
                                          value.value,
                                          unit=unit,
                                          min_val=value.range[0],
                                          max_val=value.range[1])
            new_ctrl.SetForegroundColour("#2FA7D4")
            new_ctrl.SetBackgroundColour(self.panel.GetBackgroundColour())

        else:
            txt = unicode("%s %s" % (value.value, unit))
            new_ctrl = wx.StaticText(self.panel, -1, txt)



        self._sizer.Add(new_ctrl, (self.num_entries, 1),
                        flag=wx.ALL|wx.EXPAND, border=5)
        self.num_entries += 1
        self.entries.append((lbl_ctrl, new_ctrl, value))
        self.fb_panel.Parent.Layout()

def set_on_notify(v):
    log.warn("def")

def get_func(ctrl_func):
    def _listener(v):
        log.warn("funcy")
        ctrl_func(v)
    return _listener

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

        self.blah = []
    # Optical microscope settings

    def add_ccd(self, comp):

        self._optical_panel.add_label("Camera", comp.name)
        vigil_attrs = getVAs(comp)

        if SETTINGS.has_key("ccd"):

            for name, value in vigil_attrs.iteritems():

                if SETTINGS["ccd"].has_key(name):
                    self.blah.append(value)
                    self._optical_panel.add_value(name,
                                                  value,
                                                  SETTINGS["ccd"][name])
                else:
                    log.debug("No configuration found for %s attribute", name)
        else:
            log.warn("No CCD settings found! Generating default controls")

            for name, value in vigil_attrs.iteritems():
                self._optical_panel.add_value(name, value)


