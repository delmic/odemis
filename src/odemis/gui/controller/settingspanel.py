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

### Purpose ###

This module contains classes to control the settings controls in the right
setting column of the user interface.

"""

import collections
import logging
import re

import wx.combo

import odemis.gui
import odemis.gui.comp.text as text
import odemis.gui.img.data as img
import odemis.gui.util.units as utun
from ..comp.foldpanelbar import FoldPanelItem
from odemis.gui import FOREGROUND_COLOUR_HIGHLIGHT, FOREGROUND_COLOUR_EDIT
from odemis.gui.comp.radio import GraphicalRadioButtonControl
from odemis.gui.comp.slider import UnitIntegerSlider, UnitFloatSlider
from odemis.gui.util.widgets import VigilantAttributeConnector
from odemis.gui.util.units import readable_str
from odemis.model import getVAs, NotApplicableError, VigilantAttributeBase, \
    NotSettableError



# Utility functions

def resolution_from_range(va, conf):
    """ Try and get the maximum value of range and use
    that to construct a list of resolutions
    """
    try:
        logging.debug("Generating resolutions...")
        res = [max(va.range)]

        for dummy in range(3):
            width = res[-1][0] / 2
            height = res[-1][1] / 2
            res.append((width, height))
        return res

    except NotApplicableError:
        return set()

def choice_to_str(choice):
    if not isinstance(choice, collections.Iterable):
        choice = [unicode(choice)]
    return u" x ".join([unicode(c) for c in choice])

def traverse(seq_val):
    if isinstance(seq_val, collections.Iterable):
        for value in seq_val:
            for subvalue in traverse(value):
                yield subvalue
    else:
        yield seq_val

def bind_highlight(ctrl, vat, *evt_types):
    def_val = vat.value
    def hl(evt):
        eo = evt.GetEventObject()
        if eo.GetValue() == def_val:
            eo.SetForegroundColour(FOREGROUND_COLOUR_EDIT)
        else:
            eo.SetForegroundColour(FOREGROUND_COLOUR_HIGHLIGHT)

    for e in evt_types:
        ctrl.Bind(e, hl)

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
                    "control_type": odemis.gui.CONTROL_SLIDER,
                    "scale": "log",
                    "range": (0.01, 3.00),
                    "type": "float",
                },
                "binning":
                {
                    "control_type": odemis.gui.CONTROL_RADIO,
                    "choices": set([1, 2, 4]),
                },
                "resolution":
                {
                    "control_type": odemis.gui.CONTROL_COMBO,
                    "choices": resolution_from_range,
                },
            # what we don't want to display:
                "targetTemperature":
                {
                    "control_type": odemis.gui.CONTROL_NONE,
                },
                "fanSpeed":
                {
                    "control_type": odemis.gui.CONTROL_NONE,
                },
                "pixelSize":
                {
                    "control_type": odemis.gui.CONTROL_NONE,
                },
            },
            "e-beam":
            {
                "energy":
                {
                    "format": True
                },
                "spotSize":
                {
                    "format": True
                },
                "dwellTime":
                {
                    "control_type": odemis.gui.CONTROL_SLIDER,
                    "range": (1e-9, 0.1),
                    "scale": "log",
                    "type": "float",
                },
                "resolution":
                {
                    "control_type": odemis.gui.CONTROL_COMBO,
                    "choices": resolution_from_range,
                },
                "magnification": # force using just a text field => it's for copy-paste
                {
                    "control_type": odemis.gui.CONTROL_FLT,
                },
            }
        }


class SettingsPanel(object):
    """ Settings base class which describes an indirect wrapper for
    FoldPanelItems.

    :param fold_panel: (FoldPanelItem) Parent window
    :param default_msg: (str) Text message which will be shown if the
        SettingPanel does not contain any child windows.
    :param highlight_change: (bool) If set to True, the values will be
        highlighted when they match the cached values.
    NOTE: Do not instantiate this class, but always inherit it.
    """

    def __init__(self, fold_panel, default_msg, highlight_change=False):
        self.fold_panel = fold_panel
        assert isinstance(self.fold_panel, FoldPanelItem)

        self.panel = wx.Panel(self.fold_panel)


        self.panel.SetBackgroundColour(odemis.gui.BACKGROUND_COLOUR)
        self.panel.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR)

        self.highlight_change = highlight_change

        self._main_sizer = wx.BoxSizer()
        self._sizer = wx.GridBagSizer()

        self.panel.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR_DIS)
        self._sizer.Add(wx.StaticText(self.panel, -1, default_msg),
                        (0, 1))
        self.panel.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR)

        self.panel.SetSizer(self._main_sizer)
        self._main_sizer.Add(self._sizer, proportion=1,
                                          flag=wx.RIGHT|wx.EXPAND,
                                          border=10)

        self.fold_panel.add_item(self.panel)

        self._sizer.AddGrowableCol(1)

        self.num_entries = 0
        self.entries = []

        # This attribute can be used to save and restore the current state
        # (as in, all the values of the controls) of the SettingsPanel.
        self._values_cache = []

        #self.panel.SetSize((380, -1))
        #print self.panel.Refresh()

    def store(self):
        """ Store the current control values into an internal values cache """
        # Clear the current cache
        self._values_cache = []

        for entry in self.entries:
            value = None

            if "vaco" in entry:
                value = entry["vaco"].vigilattr.value
            elif hasattr(entry["val_ctrl"], "GetValue"):
                value = entry["val_ctrl"].GetValue()

            logging.debug("Storing value %s for %s",
                          value,
                          entry["lbl_ctrl"].GetLabel())
            self._values_cache.append(value)

    def restore(self):
        """ Restore the control values from the internal values cache """
        for value, entry in zip(self._values_cache, self.entries):
            if value:
                logging.debug("Restoring value %s for %s",
                              value,
                              entry["lbl_ctrl"].GetLabel())

                if "vaco" in entry:
                    try:
                        entry["vaco"].vigilattr.value = value
                    except NotSettableError:
                        pass
                elif hasattr(entry["val_ctrl"], "SetValue"):
                    entry["val_ctrl"].SetValue(value)
                else:
                    continue

    def pause(self):
        """ Pause VigilantAttributeConnector related control updates """
        for entry in [e for e in self.entries if "vaco" in e]:
            entry["vaco"].pause()

    def resume(self):
        """ Pause VigilantAttributeConnector related control updates """
        for entry in [e for e in self.entries if "vaco" in e]:
            entry["vaco"].resume()

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
            logging.warn("No value provided!")
            return odemis.gui.CONTROL_NONE

        if value.readonly:
            return odemis.gui.CONTROL_LABEL
        else:
            try:
                # This statement will raise an exception when no choices are
                # present
                logging.debug("found choices %s", value.choices)

                max_items = 5
                max_len = 5
                # If there are too many choices, or their values are too long
                # in string representation, use a dropdown box

                choices_str = "".join([str(c) for c in value.choices])
                if len(value.choices) < max_items and \
                   len(choices_str) < max_items * max_len:
                    return odemis.gui.CONTROL_RADIO
                else:
                    return odemis.gui.CONTROL_COMBO
            except (AttributeError, NotApplicableError):
                pass

            try:
                # An exception will be raised if no range attribute is found
                logging.debug("found range %s", value.range)
                # TODO: if unit is "s" => scale=exp
                if isinstance(value.value, (int, float)):
                    return odemis.gui.CONTROL_SLIDER
            except (AttributeError, NotApplicableError):
                pass

            # Return default control
            return odemis.gui.CONTROL_TEXT

    def _get_rng_choice_unit(self, va, conf):
        """ Retrieve the range and choices values from the vigilant attribute
        or override them with the values provided in the configuration.
        """

        rng = conf.get("range", None)
        try:
            if rng is None:
                rng = va.range
            else: # merge
                rng = [max(rng[0], va.range[0]), min(rng[1], va.range[1])]
        except (AttributeError, NotApplicableError):
            pass

        choices = conf.get("choices", None)
        try:
            if callable(choices):
                choices = choices(va, conf)
            elif choices is None:
                choices = va.choices
            else: # merge = intersection
                # TODO: if va.range but no va.choices, ensure that
                # choices is within va.range
                choices &= va.choices
        except (AttributeError, NotApplicableError):
            pass

        # Get unit from config, vattribute or use an empty one
        unit =  conf.get('unit', va.unit or "")

        return rng, choices, unit

    def add_label(self, label, value=None):
        """ Adds a label to the settings panel, accompanied by an immutable
        value if one's provided.
        """
        self._clear()
        # Create label
        lbl_ctrl = wx.StaticText(self.panel, -1, "%s" % label)
        self._sizer.Add(lbl_ctrl, (self.num_entries, 0), flag=wx.ALL, border=5)

        value_ctrl = None

        if value:
            self.panel.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR_DIS)

            value_ctrl = wx.StaticText(self.panel, -1, unicode(value))
            self._sizer.Add(value_ctrl, (self.num_entries, 1),
                            flag=wx.ALL, border=5)
            self.panel.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR)

        self.num_entries += 1
        self.entries.append({"lbl_ctrl": lbl_ctrl,
                             "val_ctrl": value_ctrl,
                             "value": value})

    def add_value(self, label, value, conf=None):
        """ Add a label/value pair to the settings panel.

        conf {dict}: Configuration items that may override default settings
        """
        assert isinstance(value, VigilantAttributeBase)

        # If no conf provided, set it to an empty dictionary
        conf = conf or {}


        # Get the range and choices
        rng, choices, unit = self._get_rng_choice_unit(value, conf)

        format = conf.get("format", False)

        if choices:
            if format and all([isinstance(c, (int, float)) for c in choices]):
                choices_formatted, prefix = utun.si_scale_list(choices)
                choices_formatted = [u"%g" % c for c in choices_formatted]
                unit = prefix + unit
            else:
                choices_formatted = [choice_to_str(c) for c in choices]

        # Get the defined type of control or assign a default one
        control_type = conf.get('control_type',
                                self._determine_default_control(value))

        # Special case, early stop
        if control_type == odemis.gui.CONTROL_NONE:
            # No value, not even label
            return

        # Remove any 'empty panel' warning
        self._clear()

        # Format label
        label = conf.get('label', self._label_to_human(label))
        # Add the label to the panel
        lbl_ctrl = wx.StaticText(self.panel, -1, "%s" % label)
        self._sizer.Add(lbl_ctrl, (self.num_entries, 0), flag=wx.ALL, border=5)

        # the Vigilant Attribute Connector connects the wx control to the
        # vigilatn attribute.
        vac = None

        logging.debug("Adding VA %s", label)
        # Create the needed wxPython controls
        if control_type == odemis.gui.CONTROL_LABEL:
            # Read only value
            # In this case the value need to be transformed into a string

            self.panel.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR_DIS)
            new_ctrl = wx.StaticText(self.panel, -1, size=(200, -1))
            self.panel.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR)

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

        elif control_type == odemis.gui.CONTROL_SLIDER:
            # The slider is accompanied by an extra number text field

            if conf.get('type', "integer") == "integer":
                klass = UnitIntegerSlider
            else:
                klass = UnitFloatSlider

            new_ctrl = klass(self.panel,
                             value=value.value,
                             val_range=rng,
                             scale=conf.get('scale', None),
                             unit=unit,
                             t_size=(50, -1))

            vac = VigilantAttributeConnector(value,
                                             new_ctrl,
                                             new_ctrl.SetValue,
                                             events=wx.EVT_SLIDER)

            if self.highlight_change:
                bind_highlight(new_ctrl, value, wx.EVT_SLIDER)

        elif control_type == odemis.gui.CONTROL_INT:
            if unit == "": # don't display unit prefix if no unit
                unit = None
            new_ctrl = text.UnitIntegerCtrl(self.panel,
                                            style=wx.NO_BORDER,
                                            unit=unit,
                                            min_val=rng[0],
                                            max_val=rng[1],
                                            choices=choices)
            new_ctrl.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR_EDIT)
            new_ctrl.SetBackgroundColour(self.panel.GetBackgroundColour())

            vac = VigilantAttributeConnector(value,
                                             new_ctrl,
                                             events=wx.EVT_COMMAND_ENTER)

            if self.highlight_change:
                bind_highlight(new_ctrl, value,
                               wx.EVT_TEXT, wx.EVT_COMMAND_ENTER)

        elif control_type == odemis.gui.CONTROL_FLT:
            if unit == "": # don't display unit prefix if no unit
                unit = None
            new_ctrl = text.UnitFloatCtrl(self.panel,
                                          style=wx.NO_BORDER,
                                          unit=unit,
                                          min_val=rng[0],
                                          max_val=rng[1],
                                          choices=choices,
                                          accuracy=6)
            new_ctrl.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR_EDIT)
            new_ctrl.SetBackgroundColour(self.panel.GetBackgroundColour())

            vac = VigilantAttributeConnector(value,
                                             new_ctrl,
                                             events=wx.EVT_COMMAND_ENTER)

            if self.highlight_change:
                bind_highlight(new_ctrl, value,
                               wx.EVT_TEXT, wx.EVT_COMMAND_ENTER)


        elif control_type == odemis.gui.CONTROL_RADIO:
            new_ctrl = GraphicalRadioButtonControl(self.panel,
                                                   -1,
                                                   size=(-1, 16),
                                                   choices=choices,
                                                   style=wx.NO_BORDER,
                                                   labels=choices_formatted,
                                                   units=unit)
            vac = VigilantAttributeConnector(value,
                                             new_ctrl,
                                             new_ctrl.SetValue,
                                             events=wx.EVT_BUTTON)

            if self.highlight_change:
                bind_highlight(new_ctrl, value,
                               wx.EVT_BUTTON)

        elif control_type == odemis.gui.CONTROL_COMBO:

            new_ctrl = wx.combo.OwnerDrawnComboBox(self.panel,
                                                   -1,
                                                   value='',
                                                   pos=(0, 0),
                                                   size=(100, 16),
                                                   style=wx.NO_BORDER |
                                                         wx.CB_DROPDOWN |
                                                         wx.TE_PROCESS_ENTER |
                                                         wx.CB_READONLY)


            # Set colours
            new_ctrl.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR_EDIT)
            new_ctrl.SetBackgroundColour(self.panel.GetBackgroundColour())

            new_ctrl.SetButtonBitmaps(img.getbtn_downBitmap(),
                                      pushButtonBg=False)

            def _eat_event(evt):
                """ Quick and dirty empty function used to 'eat'
                mouse wheel events"""
                pass

            new_ctrl.Bind(wx.EVT_MOUSEWHEEL, _eat_event)

            # Set choices
            for choice, formatted in zip(choices, choices_formatted):
                new_ctrl.Append(u"%s %s" % (formatted, unit), choice)

            def _getvalue_wrapper(ctrl, func):
                def wrapper():
                    value = func()
                    for i in range(ctrl.Count):
                        if ctrl.Items[i] == value:
                            logging.debug("Getting ComboBox value %s",
                                      ctrl.GetClientData(i))
                            return ctrl.GetClientData(i)
                return wrapper

            new_ctrl.GetValue = _getvalue_wrapper(new_ctrl, new_ctrl.GetValue)


            # A small wrapper function makes sure that the value can
            # be set by passing the actual value (As opposed to the text label)
            def _setvalue_wrapper(ctrl, func):
                def wrapper(value):
                    for i in range(ctrl.Count):
                        if ctrl.GetClientData(i) == value:
                            logging.debug("Setting ComboBox value to %s",
                                      ctrl.Items[i])
                            return func(ctrl.Items[i])
                    logging.warning("No matching label found for value %s!", value)
                return wrapper

            new_ctrl.SetValue = _setvalue_wrapper(new_ctrl, new_ctrl.SetValue)

            vac = VigilantAttributeConnector(
                    value,
                    new_ctrl,
                    new_ctrl.SetValue,
                    new_ctrl.GetValue,
                    events=(wx.EVT_COMBOBOX, wx.EVT_TEXT_ENTER))

            if self.highlight_change:
                bind_highlight(new_ctrl, value,
                               wx.EVT_COMBOBOX, wx.EVT_TEXT_ENTER)

        else:
            txt = readable_str(value.value, unit)
            new_ctrl = wx.StaticText(self.panel, -1, txt)

        #if self.highlight_change and hasattr(new_ctrl, 'SetValue'):
        #    new_ctrl.SetForegroundColour(FOREGROUND_COLOUR_HIGHLIGHT)

        self._sizer.Add(new_ctrl, (self.num_entries, 1),
                        flag=wx.ALL|wx.EXPAND, border=5)

        self.num_entries += 1
        self.entries.append({"lbl_ctrl": lbl_ctrl,
                             "val_ctrl": new_ctrl,
                             "value": value.value,
                             "vaco": vac})
        self.fold_panel.Parent.Layout()

def set_on_notify(v):
    logging.warn("def")

def get_func(ctrl_func):
    def _listener(v):
        logging.warn("funcy")
        ctrl_func(v)
    return _listener

class SemSettingsPanel(SettingsPanel):
    pass

class OpticalSettingsPanel(SettingsPanel):
    pass

class SettingsSideBar(object):
    """ The main controller class for the settings panel in the live view and
    acquisition frame.

    This class can be used to set, get and otherwise manipulate the content
    of the setting panel.
    """

    def __init__(self, interface_model, parent_frame, highlight_change=False):
        self._parent_frame = parent_frame
        self._interface_model = interface_model

        self._sem_panel = SemSettingsPanel(
                                    parent_frame.fp_sem_settings,
                                    "No SEM found",
                                    highlight_change)

        self._optical_panel = OpticalSettingsPanel(
                                    parent_frame.fp_optical_settings,
                                    "No optical microscope found",
                                    highlight_change)

        self.settings_panels = [self._sem_panel, self._optical_panel]

        # Query Odemis daemon (Should move this to separate thread)
        if interface_model.ccd:
            self.add_ccd(interface_model.ccd)
        # TODO allow to change light.power

        if interface_model.ebeam:
            self.add_ebeam(interface_model.ebeam)

        # Save the current values so they can be used for highlighting
        if highlight_change:
            self.store()

    def store(self):
        """ Store all values in the used SettingsPanels """
        for panel in self.settings_panels:
            panel.store()

    def restore(self):
        """ Restore all values to the used SettingsPanels """
        for panel in self.settings_panels:
            panel.restore()

    def pause(self):
        """ Pause VigilantAttributeConnector related control updates """
        for panel in self.settings_panels:
            panel.pause()

    def resume(self):
        """ Resume VigilantAttributeConnector related control updates """
        for panel in self.settings_panels:
            panel.resume()

    # Optical microscope settings
    def add_ccd(self, comp):
        self._optical_panel.add_label("Camera", comp.name)

        vigil_attrs = getVAs(comp)
        for name, value in vigil_attrs.items():
            if comp.role in SETTINGS and name in SETTINGS[comp.role]:
                conf = SETTINGS[comp.role][name]
            else:
                conf = None
            self._optical_panel.add_value(name, value, conf)

    def add_ebeam(self, comp):
        self._sem_panel.add_label("SEM", comp.name)

        vigil_attrs = getVAs(comp)
        for name, value in vigil_attrs.items():
            if comp.role in SETTINGS and name in SETTINGS[comp.role]:
                conf = SETTINGS[comp.role][name]
            else:
                conf = None
            self._sem_panel.add_value(name, value, conf)

