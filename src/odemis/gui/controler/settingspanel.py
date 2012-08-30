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
from odemis.model import getVAs, NotApplicableError, VigilantAttributeBase, \
    OutOfBoundError



MAIN_FRAME = None

# FIXME: Move hardcoded layout options to a more suitable place

BACKGROUND_COLOUR = "#333333"
FOREGROUND_COLOUR = "#DDDDDD"
FOREGROUND_COLOUR_DIS = "#666666"
FOREGROUND_COLOUR_EDIT = "#2FA7D4"

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
                # "temperature":
                # {

                # },
                # "binning":
                # {
                #     "control_type": CONTROL_INT,
                # },
            }
           }

def _call_after_wrapper(f):
    def wrapped(value):
        print "WEEEEEEE"
        wx.CallAfter(f, value)
    return wrapped

class VigilantAttributeConnector(object):
    """ This class connects a vigilant attribute with a wxPython control,
    making sure that the changes in one are automatically reflected in the
    other.
    """
    def __init__(self, vigilattr, ctrl, sub_func, change_event=None):
        self.vigilattr = vigilattr
        self.ctrl = ctrl
        self.sub_func = _call_after_wrapper(sub_func)
        self.change_event = change_event

        # Subscribe to the vigilant attribute and initialize
        self.vigilattr.subscribe(self.sub_func, True)
        # If necessary, bind the provided change event
        if change_event:
            self.ctrl.Bind(change_event, self._on_value_change)

    def _on_value_change(self, evt):
        """ This method is called when the value of the control is
        changed.
        """
        try:
            log.warn("Assign value to vigilant attribute")
            self.vigilattr.value = self.ctrl.GetValue()
        except OutOfBoundError:
            log.error("Illegal value")
        finally:
            evt.Skip()

    def disconnect(self):
        log.debug("Disconnecting VigilantAttributeConnector")
        self.ctrl.Unbind(self.change_event, self._on_value_change)
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

    def _get_rng_and_choice(self, va, conf):
        """ Retrieve the range and choices values from the vigilant attribute
        or override them with the values provided in the configration.
        """
        rng = (None, None)
        choices = None

        try:
            rng = conf.get("range", va.range)
        except (AttributeError, NotApplicableError):
            pass

        try:
            choices = conf.get("choices", va.choices)
        except (AttributeError, NotApplicableError):
            pass

        return rng, choices

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
            self.fb_panel.Parent.Layout()
            self.num_entries += 1
            return


        # Get unit from config, vattribute or use an empty one
        unit =  conf.get('unit', value.unit or "")
        # Get the range and choices
        rng, choices = self._get_rng_and_choice(value, conf)
        # Get the defined type of control or assign a default one
        control_type = conf.get('control_type',
                                self._determine_default_control(value))


        # the Vigilant Attribute Connector connects the wx control to the
        # vigilatn attribute.
        vac = None

        # Create the needed wxPython controls

        if control_type == CONTROL_NONE:
            # No value
            return

        elif control_type == CONTROL_LABEL:
            # Read only value
            # In this case the value need to be transformed into a string

            self.panel.SetForegroundColour(FOREGROUND_COLOUR_DIS)
            new_ctrl = wx.StaticText(self.panel, -1, size=(200, -1))
            self.panel.SetForegroundColour(FOREGROUND_COLOUR)

            def format_label(value):
                if isinstance(value, tuple):
                    # Maximum number of chars per value
                    txt = " x ".join(["%s %s" % (v, unit) for v in value])
                else:
                    txt = u"%s %s" % (value, unit)
                new_ctrl.SetLabel(txt)

            vac = VigilantAttributeConnector(value,
                                             new_ctrl,
                                             format_label)

        elif control_type == CONTROL_SLIDER:
            # The slider is accompanied by an extra number text field

            new_ctrl = CustomSlider(self.panel, value=value.value,
                                    val_range=rng,
                                    size=(30, 15),
                                    pos=(-1, 10),
                                    style=wx.SL_HORIZONTAL,
                                    scale=conf.get('scale', None))

            # Dynamically create step size based on range
            step = (rng[1] - rng[0]) / 255.0
            # To keep the inc/dec values 'clean', set the step
            # value to the nearest power of 10
            step = 10 ** round(math.log10(step))

            # Determing the type of number text control to link with the slider
            if isinstance(value.value, int):
                log.debug("Adding int field to slider")
                klass = text.UnitIntegerCtrl
                # Enforce integer stepping
                step = max(step, 1)
            else:
                log.debug("Adding float field to slider")
                klass = text.UnitFloatCtrl

            txt_ctrl = klass(self.panel, -1,
                            value.value,
                            style=wx.NO_BORDER,
                            size=(60, -1),
                            min_val=rng[0],
                            max_val=rng[1],
                            unit=unit,
                            accuracy=2,
                            step=step)

            # Assign default colour scheme to the text control
            txt_ctrl.SetForegroundColour(FOREGROUND_COLOUR_EDIT)
            txt_ctrl.SetBackgroundColour(self.panel.GetBackgroundColour())

            new_ctrl.set_linked_field(txt_ctrl)
            txt_ctrl.set_linked_slider(new_ctrl)

            self._sizer.Add(txt_ctrl, (self.num_entries, 2), flag=wx.ALL, border=5)

            # Only connect one control to the vigilant attribute.
            # Communication between the controls should keep them 'synchronized'
            # value wise.
            vat = VigilantAttributeConnector(value,
                                             txt_ctrl,
                                             txt_ctrl.SetValueStr,
                                             wx.EVT_TEXT_ENTER)

            # vas = VigilantAttributeConnector(value,
            #                                  new_ctrl,
            #                                  new_ctrl.SetValue,
            #                                  wx.EVT_LEFT_UP)
            vac = vat

        elif control_type == CONTROL_INT:
            new_ctrl = text.UnitIntegerCtrl(self.panel,
                                            -1,
                                            style=wx.NO_BORDER,
                                            unit=unit,
                                            min_val=rng[0],
                                            max_val=rng[1],
                                            choices=choices)
            new_ctrl.SetForegroundColour(FOREGROUND_COLOUR_EDIT)
            new_ctrl.SetBackgroundColour(self.panel.GetBackgroundColour())

            #value.subscribe(lambda v: new_ctrl.SetValue(v))

            #new_ctrl.SetValue("AAAAAA")

            # value.subscribe(lambda v: log.warn("lambda"))
            # value.subscribe(set_on_notify)
            # f = get_func(new_ctrl.SetValue)
            #self.entries.append(f)
            # value.subscribe(f)

            vac = VigilantAttributeConnector(value,
                                             new_ctrl,
                                             new_ctrl.SetValueStr,
                                             wx.EVT_TEXT_ENTER)


        elif control_type == CONTROL_FLT:
            new_ctrl = text.UnitFloatCtrl(self.panel,
                                         -1,
                                          value.value,
                                          unit=unit,
                                          min_val=value.range[0],
                                          max_val=value.range[1])
            new_ctrl.SetForegroundColour(FOREGROUND_COLOUR_EDIT)
            new_ctrl.SetBackgroundColour(self.panel.GetBackgroundColour())

        else:
            txt = unicode("%s %s" % (value.value, unit))
            new_ctrl = wx.StaticText(self.panel, -1, txt)



        self._sizer.Add(new_ctrl, (self.num_entries, 1),
                        flag=wx.ALL|wx.EXPAND, border=5)

        self.num_entries += 1
        self.entries.append(vac)
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

    # Optical microscope settings

    def add_ccd(self, comp):

        self._optical_panel.add_label("Camera", comp.name)
        vigil_attrs = getVAs(comp)

        if SETTINGS.has_key("ccd"):

            for name, value in vigil_attrs.iteritems():

                if SETTINGS["ccd"].has_key(name):
                    self._optical_panel.add_value(name,
                                                  value,
                                                  SETTINGS["ccd"][name])
                else:
                    log.debug("No configuration found for %s attribute", name)
        else:
            log.warn("No CCD settings found! Generating default controls")

            for name, value in vigil_attrs.iteritems():
                self._optical_panel.add_value(name, value)


