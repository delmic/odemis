#-*- coding: utf-8 -*-
"""
@author: Rinze de Laat

Copyright © 2012-2013 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.


### Purpose ###

This module contains classes to control the settings controls in the right
setting column of the user interface.


..NOTE:
    This module is a prime candiate for a refactoring session!!!

"""

import collections
import logging
import numbers
import re
import time

import wx
from wx.lib.pubsub import pub

import odemis.gui
import odemis.dataio
import odemis.gui.comp.text as text
import odemis.gui.util

from odemis import model
from odemis.acq.stream import SpectrumStream, ARStream
from odemis.gui.comp.combo import ComboBox
from odemis.gui.comp.file import FileBrowser
from odemis.gui.comp.foldpanelbar import FoldPanelItem
from odemis.gui.comp.radio import GraphicalRadioButtonControl
from odemis.gui.comp.slider import UnitIntegerSlider, UnitFloatSlider
from odemis.gui.conf import get_calibration_conf
from odemis.gui.conf.settingspanel import CONFIG
from odemis.gui.util.widgets import VigilantAttributeConnector, AxisConnector
from odemis.model import getVAs, NotApplicableError, VigilantAttributeBase
from odemis.util.driver import reproduceTypedValue
from odemis.util.units import readable_str

import odemis.util.units as utun


####### Utility functions #######

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

def bind_menu(se):
    """
    Add a menu to reset a setting entry to the original (current) value
    :param se: (SettingEntry)

    Note: se must have a valid label, ctrl and va at least
    """
    orig_val = se.va.value

    def reset_value(evt):
        se.va.value = orig_val
        wx.CallAfter(pub.sendMessage, 'setting.changed', setting_ctrl=se.ctrl)

    def show_reset_menu(evt):
        # No menu needed if value hasn't changed
        if se.va.value == orig_val:
            return # TODO: or display it greyed out?

        menu = wx.Menu()
        mi = wx.MenuItem(menu, wx.NewId(), 'Reset value')

        eo = evt.GetEventObject()
        eo.Bind(wx.EVT_MENU, reset_value, mi)

        menu.AppendItem(mi)
        eo.PopupMenu(menu)

    se.ctrl.Bind(wx.EVT_CONTEXT_MENU, show_reset_menu)
    se.label.Bind(wx.EVT_CONTEXT_MENU, show_reset_menu)


####### Classes #######

class SettingEntry(object):
    """
    Represents a setting entry in the panel. It merely associates the VA to
    the widgets that allow to control it.
    """
    # TODO: merge with VAC?
    def __init__(self, name, va=None, comp=None, label=None, ctrl=None, vac=None):
        """
        :param name: (string): name of the va in the component (as-is)
        :param va: (VA): the actual VigilanAttribute
        :param comp: (model.Component): the component that has this VA
        :param label: (wx.LabelTxt): a widget which displays the name of the VA
        :param ctrl: (wx.Window): a widget that allows to change the value
        :param vac: (VigilantAttributeController): the object that ensures the
            connection between the VA and the widget
        """

        self.name = name
        self.va = va
        self.comp = comp
        self.label = label
        self.ctrl = ctrl
        self.vac = vac

    def __repr__(self):
        msg = "Name: %s, label: %s, comp: %s, ctrl: %s"
        return msg % (self.name,
                      self.label.GetLabel() if self.label else None,
                      self.comp.name if self.comp else None,
                      type(self.ctrl) if self.ctrl else None)

    def highlight(self, active=True):
        """
        Highlight the setting entry (ie, the name label becomes bright coloured)
        active (boolean): whether it should be highlighted or not
        """
        if not self.label:
            return

        if active:
            self.label.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR_HIGHLIGHT)
        else:
            self.label.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR)


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

        assert isinstance(fold_panel, FoldPanelItem)

        self.fold_panel = fold_panel

        self.panel = wx.Panel(self.fold_panel)


        self.panel.SetBackgroundColour(odemis.gui.BACKGROUND_COLOUR)
        self.panel.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR)

        self.highlight_change = highlight_change

        self._main_sizer = wx.BoxSizer()
        self._gb_sizer = wx.GridBagSizer(0, 0)

        self._gb_sizer.Add(wx.StaticText(self.panel, -1, default_msg), (0, 1))

        self.panel.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR_DIS)
        self.panel.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR)

        self.panel.SetSizer(self._main_sizer)
        self._main_sizer.Add(self._gb_sizer,
                             proportion=1,
                             flag=wx.ALL | wx.EXPAND,
                             border=5)

        self.fold_panel.add_item(self.panel)

        self._gb_sizer.AddGrowableCol(1)

        self.num_entries = 0
        self.entries = [] # list of SettingEntry

    def Hide(self, *args, **kwargs):
        self.panel.Hide(*args, **kwargs)

    def Show(self, *args, **kwargs):
        self.panel.Show(*args, **kwargs)

    def pause(self):
        """ Pause VigilantAttributeConnector related control updates """
        for entry in self.entries:
            if entry.vac:
                entry.vac.pause()

    def resume(self):
        """ Pause VigilantAttributeConnector related control updates """
        for entry in self.entries:
            if entry.vac:
                entry.vac.resume()

    def enable(self, enabled):
        for entry in self.entries:
            if entry.ctrl:
                entry.ctrl.Enable(enabled)

    def _clear(self):
        """ Remove default 'no content' label """
        if self.num_entries == 0:
            cl = self.panel.GetChildren()
            if cl:
                cl[0].Destroy()

    def clear(self):
        """ Remove all entries"""
        if self.num_entries > 0:
            for c in self.panel.GetChildren():
                c.Destroy()
            self.num_entries = 0

    def _label_to_human(self, label):
        """ Converts a camel-case label into a human readable one
        """
        # add space after each upper case
        # then, make the first letter uppercase and all the other ones lowercase
        return re.sub(r"([A-Z])", r" \1", label).capitalize()

    def _determine_default_control(self, va):
        """ Determine the default control to use to represent a vigilant
        attribute in the settings panel.
        va (VigillantAttribute)
        return (odemis.gui.CONTROL_*)
        """
        if not va:
            logging.warn("No VA provided!")
            return odemis.gui.CONTROL_NONE

        if va.readonly:
            return odemis.gui.CONTROL_LABEL
        else:
            try:
                # This statement will raise an exception when no choices are
                # present
                logging.debug("found choices %s", va.choices)

                max_items = 5
                max_len = 5
                # If there are too many choices, or their values are too long
                # in string representation, use a dropdown box

                choices_str = "".join([str(c) for c in va.choices])
                if len(va.choices) <= 1:
                    # not much choices really
                    return odemis.gui.CONTROL_LABEL
                elif len(va.choices) < max_items and \
                   len(choices_str) < max_items * max_len:
                    return odemis.gui.CONTROL_RADIO
                else:
                    return odemis.gui.CONTROL_COMBO
            except (AttributeError, NotApplicableError):
                pass

            try:
                # An exception will be raised if no range attribute is found
                logging.debug("found range %s", va.range)
                # TODO: if unit is "s" => scale=exp
                if isinstance(va.value, (int, long, float)):
                    return odemis.gui.CONTROL_SLIDER
            except (AttributeError, NotApplicableError):
                pass

            # Return default control
            return odemis.gui.CONTROL_TEXT

    def _get_va_meta(self, va, conf):
        """ Retrieve the range and choices values from the vigilant attribute
        or override them with the values provided in the configuration.
        """

        minv, maxv = conf.get("range", (None, None))
        try:
            if (minv, maxv) == (None, None):
                minv, maxv = va.range
            else: # merge
                # TODO: handle iterables
                minv, maxv = max(minv, va.range[0]), min(maxv, va.range[1])
        except (AttributeError, NotApplicableError):
            pass
        # Ensure the range encompasses the current value
        if minv is not None and maxv is not None:
            val = va.value
            if isinstance(val, numbers.Real):
                minv, maxv = min(minv, val), max(maxv, val)

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
        unit = conf.get('unit', va.unit or "")

        return minv, maxv, choices, unit

    def add_label(self, label, value=None, selectable=True):
        """ Adds a label to the settings panel, accompanied by an immutable
        value if one's provided.

        value (None or stringable): value to display after the label
        selectable (boolean): whether the value can be selected by the user
          (iow, copy/pasted)
        returns (SettingEntry): the new SettingEntry created
        """
        self._clear()
        # Create label
        lbl_ctrl = wx.StaticText(self.panel, wx.ID_ANY, unicode(label))
        self._gb_sizer.Add(lbl_ctrl, (self.num_entries, 0),
                           flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)

        if value and not selectable:
            value_ctrl = wx.StaticText(self.panel, label=unicode(value))
            value_ctrl.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR_DIS)
            self._gb_sizer.Add(value_ctrl, (self.num_entries, 1),
                               flag=wx.ALL, border=5)
        elif value and selectable:
            value_ctrl = wx.TextCtrl(self.panel, value=unicode(value),
                                     style=wx.BORDER_NONE | wx.TE_READONLY)
            value_ctrl.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR_DIS)
            value_ctrl.SetBackgroundColour(odemis.gui.BACKGROUND_COLOUR)
            self._gb_sizer.Add(value_ctrl, (self.num_entries, 1),
                               flag=wx.ALL | wx.EXPAND | wx.ALIGN_CENTER_VERTICAL,
                               border=5)
        else:
            value_ctrl = None

        ne = SettingEntry(name=label, label=lbl_ctrl, ctrl=value_ctrl)
        self.entries.append(ne)
        self.num_entries += 1

        return ne

    def add_browse_button(self, label, file_name=None):
        self._clear()
        # Create label
        lbl_ctrl = wx.StaticText(self.panel, wx.ID_ANY, unicode(label))
        self._gb_sizer.Add(lbl_ctrl, (self.num_entries, 0),
                           flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)

        value_ctrl = FileBrowser(self.panel,
                                 style=wx.BORDER_NONE | wx.TE_READONLY,
                                 clear=True)
        value_ctrl.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR_EDIT)
        value_ctrl.SetBackgroundColour(odemis.gui.BACKGROUND_COLOUR)

        if file_name:
            value_ctrl.SetValue(file_name)

        self._gb_sizer.Add(value_ctrl,
                           (self.num_entries, 1),
                           flag=wx.ALL | wx.EXPAND | wx.ALIGN_CENTER_VERTICAL,
                           border=5)

        ne = SettingEntry(name=label, label=lbl_ctrl, ctrl=value_ctrl)
        self.entries.append(ne)
        self.num_entries += 1

        return ne

    def add_divider(self):
        line = wx.StaticLine(self.panel, size=(-1, 1))
        self._gb_sizer.Add(
                line,
                (self.num_entries, 0),
                span=(1, 2),
                flag=wx.ALL | wx.EXPAND,
                border=5
        )
        self.num_entries += 1


    def add_value(self, name, vigil_attr, comp, conf=None):
        """ Add a name/value pair to the settings panel.

        :param name: (string): name of the value
        :param vigil_attr: (VigilantAttribute)
        :param comp: (Component): the component that contains this VigilantAttribute
        :param conf: ({}): Configuration items that may override default settings
        """
        assert isinstance(vigil_attr, VigilantAttributeBase)

        # If no conf provided, set it to an empty dictionary
        conf = conf or {}

        # Get the range and choices
        min_val, max_val, choices, unit = self._get_va_meta(vigil_attr, conf)

        format = conf.get("format", True)

        if choices:
            # choice_fmt is an iterable of tuples: choice -> formatted choice
            # (like a dict, but keeps order)
            if isinstance(choices, dict):
                # it's then already value -> string (user-friendly display)
                choices_fmt = choices.items()
            elif (format and len(choices) > 1 and
                  all([isinstance(c, numbers.Real) for c in choices])):
                # choices = sorted(choices)
                fmt, prefix = utun.si_scale_list(choices)
                choices_fmt = zip(choices, [u"%g" % c for c in fmt])
                unit = prefix + unit
            else:
                choices_fmt = [(c, choice_to_str(c)) for c in choices]

            choices_fmt = sorted(choices_fmt) # sort 2-tuples = according to first value in tuple

        # Get the defined type of control or assign a default one
        try:
            control_type = conf['control_type']
        except KeyError:
            control_type = self._determine_default_control(vigil_attr)

        # Change radio type to fitting type depending on its content
        if control_type == odemis.gui.CONTROL_RADIO:
            if len(choices_fmt) <= 1: # only one choice => force label
                control_type = odemis.gui.CONTROL_LABEL
            elif len(choices_fmt) > 10: # too many choices => combo
                control_type = odemis.gui.CONTROL_COMBO
            else:
                # choices names too long => combo
                max_len = max([len(f) for _, f in choices_fmt])
                if max_len > 6:
                    control_type = odemis.gui.CONTROL_COMBO

        # Special case, early stop
        if control_type == odemis.gui.CONTROL_NONE:
            # No value, not even label
            # Just an empty entry, so that the settings are saved during acquisition
            ne = SettingEntry(name, vigil_attr, comp)
            self.entries.append(ne)
            # don't increase num_entries, as it doesn't add any graphical element
            return

        # Remove any 'empty panel' warning
        self._clear()

        # Format label
        label = conf.get('label', self._label_to_human(name))
        # Add the label to the panel
        lbl_ctrl = wx.StaticText(self.panel, -1, u"%s" % label)
        self._gb_sizer.Add(lbl_ctrl, (self.num_entries, 0), flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)

        # the Vigilant Attribute Connector connects the wx control to the
        # vigilant attribute.
        vac = None

        logging.debug("Adding VA %s", label)
        # Create the needed wxPython controls
        if control_type == odemis.gui.CONTROL_LABEL:
            new_ctrl, vac = self._create_label(self.panel, vigil_attr, unit)

        elif control_type == odemis.gui.CONTROL_TEXT:
            # TODO: should be a free entry text, like combobox
            new_ctrl = wx.TextCtrl(self.panel, style=wx.BORDER_NONE | wx.TE_READONLY)
            new_ctrl.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR_DIS)
            new_ctrl.SetBackgroundColour(odemis.gui.BACKGROUND_COLOUR)

            val = vigil_attr.value # only format if it's a number
            if (isinstance(val, (int, long, float)) or
                (isinstance(val, collections.Iterable) and len(val) > 0
                  and isinstance(val[0], (int, long, float)))
                ):
                def format_value(value, unit=unit):
                    new_ctrl.SetValue(readable_str(value, unit, sig=3))
            else:
                format_value = None
            vac = VigilantAttributeConnector(vigil_attr,
                                             new_ctrl,
                                             format_value)

        elif control_type == odemis.gui.CONTROL_SLIDER:
            # The slider is accompanied by an extra number text field

            if "type" in conf:
                if conf["type"] == "integer":
                    klass = UnitIntegerSlider
                else:
                    klass = UnitFloatSlider
            else:
                # guess from value(s)
                known_values = [vigil_attr.value, min_val, max_val]
                if choices is not None:
                    known_values.extend(list(choices))
                if any(isinstance(v, float) for v in known_values):
                    klass = UnitFloatSlider
                else:
                    klass = UnitIntegerSlider

            new_ctrl = klass(self.panel,
                             value=vigil_attr.value,
                             min_val=min_val,
                             max_val=max_val,
                             scale=conf.get('scale', None),
                             unit=unit,
                             t_size=(50, -1),
                             accuracy=conf.get('accuracy', 4))

            vac = VigilantAttributeConnector(vigil_attr,
                                             new_ctrl,
                                             events=wx.EVT_SLIDER)

            new_ctrl.Bind(wx.EVT_SLIDER, self.on_setting_changed)

        elif control_type == odemis.gui.CONTROL_INT:
            if unit == "": # don't display unit prefix if no unit
                unit = None
            new_ctrl = text.UnitIntegerCtrl(self.panel,
                                            style=wx.NO_BORDER,
                                            unit=unit,
                                            min_val=min_val,
                                            max_val=max_val,
                                            choices=choices)
            new_ctrl.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR_EDIT)
            new_ctrl.SetBackgroundColour(self.panel.GetBackgroundColour())

            vac = VigilantAttributeConnector(vigil_attr,
                                             new_ctrl,
                                             events=wx.EVT_COMMAND_ENTER)

            new_ctrl.Bind(wx.EVT_COMMAND_ENTER, self.on_setting_changed)
            # new_ctrl.Bind(wx.EVT_TEXT, self.on_setting_changed)


        elif control_type == odemis.gui.CONTROL_FLT:
            if unit == "": # don't display unit prefix if no unit
                unit = None
            new_ctrl = text.UnitFloatCtrl(self.panel,
                                          style=wx.NO_BORDER,
                                          unit=unit,
                                          min_val=min_val,
                                          max_val=max_val,
                                          choices=choices,
                                          accuracy=conf.get('accuracy', 5))
            new_ctrl.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR_EDIT)
            new_ctrl.SetBackgroundColour(self.panel.GetBackgroundColour())

            vac = VigilantAttributeConnector(vigil_attr,
                                             new_ctrl,
                                             events=wx.EVT_COMMAND_ENTER)

            new_ctrl.Bind(wx.EVT_COMMAND_ENTER, self.on_setting_changed)
            # new_ctrl.Bind(wx.EVT_TEXT, self.on_setting_changed)

        elif control_type == odemis.gui.CONTROL_RADIO:
            new_ctrl = GraphicalRadioButtonControl(
                                    self.panel,
                                    - 1,
                                    size=(-1, 16),
                                    choices=[c for c, _ in choices_fmt],
                                    style=wx.NO_BORDER,
                                    labels=[f for _, f in choices_fmt],
                                    units=unit)

            if conf.get('type', None) == "1d_binning":
                # need to convert back and forth between 1D and 2D
                # from 2D to 1D (just pick X)
                def radio_set(value, ctrl=new_ctrl):
                    v = value[0]
                    logging.debug("Setting Radio value to %d", v)
                    # it's fine to set a value not in the choices, it will
                    # just not set any of the buttons.
                    return ctrl.SetValue(v)

                # from 1D to 2D (both identical)
                def radio_get(ctrl=new_ctrl):
                    value = ctrl.GetValue()
                    return (value, value)
            elif conf.get('type', None) == "1std_binning":
                # need to convert back and forth between 1D and 2D
                # from 2D to 1D (just pick X)
                def radio_set(value, ctrl=new_ctrl):
                    v = value[0]
                    logging.debug("Setting Radio value to %d", v)
                    # it's fine to set a value not in the choices, it will
                    # just not set any of the buttons.
                    return ctrl.SetValue(v)

                # from 1D to 2D (don't change dimensions >1)
                def radio_get(ctrl=new_ctrl, va=vigil_attr):
                    value = ctrl.GetValue()
                    new_val = list(va.value)
                    new_val[0] = value
                    return new_val
            else:
                radio_get = None
                radio_set = None

            vac = VigilantAttributeConnector(vigil_attr,
                                             new_ctrl,
                                             va_2_ctrl=radio_set,
                                             ctrl_2_va=radio_get,
                                             events=wx.EVT_BUTTON)

            new_ctrl.Bind(wx.EVT_BUTTON, self.on_setting_changed)

        elif control_type == odemis.gui.CONTROL_COMBO:

            new_ctrl = ComboBox(
                        self.panel,
                        wx.ID_ANY,
                        value='', pos=(0, 0), size=(100, 16),
                        style=wx.BORDER_NONE | wx.TE_PROCESS_ENTER)

            def _eat_event(evt):
                """ Quick and dirty empty function used to 'eat'
                mouse wheel events
                """
                # TODO: This solution only makes sure that the control's value
                # doesn't accidentally get altered when it gets hit by a mouse
                # wheel event. However, it also stop the event from propagating
                # so the containing scrolled window will not scroll either.
                # (If the event is skipped, the control will change value again)
                pass

            new_ctrl.Bind(wx.EVT_MOUSEWHEEL, _eat_event)

            # Set choices
            for choice, formatted in choices_fmt:
                new_ctrl.Append(u"%s %s" % (formatted, unit), choice)

            # A small wrapper function makes sure that the value can
            # be set by passing the actual value (As opposed to the text label)
            def cb_set(value, ctrl=new_ctrl, unit=unit):
                for i in range(ctrl.Count):
                    if ctrl.GetClientData(i) == value:
                        logging.debug("Setting ComboBox value to %s", ctrl.Items[i])
                        return ctrl.SetValue(ctrl.Items[i])
                else:
                    logging.debug("No existing label found for value %s", value)
                    # entering value as free text
                    txt = readable_str(value, unit)
                    return ctrl.SetValue(txt)

            # equivalent wrapper function to retrieve the actual value
            def cb_get(ctrl=new_ctrl, va=vigil_attr):
                value = ctrl.GetValue()
                # Try to use the predefined value if it's available
                for i in range(ctrl.Count):
                    if ctrl.Items[i] == value:
                        logging.debug("Getting CB value %s", ctrl.GetClientData(i))
                        return ctrl.GetClientData(i)
                else:
                    logging.debug("Trying to parse CB free value %s", value)
                    cur_val = va.value
                    # Try to find a good corresponding value inside the string
                    new_val = reproduceTypedValue(cur_val, value)
                    if isinstance(new_val, collections.Iterable):
                        # be less picky, by shortening the number of values if it's too many
                        new_val = new_val[:len(cur_val)]

                    # if it ends up being the same value as before the CB will
                    # not update, so force it now
                    if cur_val == new_val:
                        cb_set(cur_val)
                    return new_val

            vac = VigilantAttributeConnector(
                    vigil_attr,
                    new_ctrl,
                    va_2_ctrl=cb_set,
                    ctrl_2_va=cb_get,
                    events=(wx.EVT_COMBOBOX, wx.EVT_TEXT_ENTER))

            new_ctrl.Bind(wx.EVT_COMBOBOX, self.on_setting_changed)
            new_ctrl.Bind(wx.EVT_TEXT_ENTER, self.on_setting_changed)

        else:
            logging.error("Unknown control type %s", control_type)

        self._gb_sizer.Add(new_ctrl, (self.num_entries, 1),
                        flag=wx.ALL | wx.EXPAND | wx.ALIGN_CENTER_VERTICAL, border=5)

        ne = SettingEntry(name, vigil_attr, comp, lbl_ctrl, new_ctrl, vac)
        self.entries.append(ne)
        self.num_entries += 1

        if self.highlight_change:
            bind_menu(ne)

        self.fold_panel.Parent.Layout()

        return ne

    def _create_label(self, panel, vigil_attr, unit):
        # Read only value
        new_ctrl = wx.TextCtrl(panel, style=wx.BORDER_NONE | wx.TE_READONLY)
        new_ctrl.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR_DIS)
        new_ctrl.SetBackgroundColour(odemis.gui.BACKGROUND_COLOUR)

        val = vigil_attr.value # only format if it's a number
        if (isinstance(val, (int, long, float)) or
            (isinstance(val, collections.Iterable) and len(val) > 0
              and isinstance(val[0], (int, long, float)))
            ):
            def format_value(value, unit=unit):
                new_ctrl.SetValue(readable_str(value, unit, sig=3))
        else:
            format_value = None
        vac = VigilantAttributeConnector(vigil_attr,
                                         new_ctrl,
                                         format_value)

        return new_ctrl, vac

    def add_axis(self, name, comp, conf=None):
        """
        Add a widget to the setting panel to control an axis

        :param name: (string): name of the axis
        :param comp: (Component): the component that contains this axis
        :param conf: ({}): Configuration items that may override default settings
        """
        # If no conf provided, set it to an empty dictionary
        conf = conf or {}

        # Format label
        label = conf.get('label', self._label_to_human(name))
        # Add the label to the panel
        lbl_ctrl = wx.StaticText(self.panel, -1, u"%s" % label)
        self._gb_sizer.Add(lbl_ctrl, (self.num_entries, 0),
                           flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)

        logging.debug("Adding Axis control %s", label)

        ad = comp.axes[name]
        pos = comp.position.value[name]
        unit = ad.unit

        # If axis has .range (continuous) => slider
        # If axis has .choices (enumerated) => combo box
        if hasattr(ad, "range"):
            minv, maxv = ad.range

            new_ctrl = UnitFloatSlider(self.panel,
                             value=pos,
                             min_val=minv,
                             max_val=maxv,
                             unit=unit,
                             t_size=(50, -1),
                             accuracy=conf.get('accuracy', 3))

            # don't bind to wx.EVT_SLIDER, which happens as soon as the slider moves,
            # but to EVT_SCROLL_CHANGED, which happens when the user has made his
            # mind. This avoid too many unnecessary actuator moves and disabling the
            # widget too early.
            ac = AxisConnector(name, comp, new_ctrl, events=wx.EVT_SCROLL_CHANGED)
        else:
            # format the choices
            choices = ad.choices
            if isinstance(choices, dict):
                # it's then already value -> string (user-friendly display)
                choices_fmt = choices.items()
            elif (unit and len(choices) > 1 and
                  all([isinstance(c, numbers.Real) for c in choices])):
                fmt, prefix = utun.si_scale_list(choices)
                choices_fmt = zip(choices, [u"%g" % c for c in fmt])
                unit = prefix + unit
            else:
                choices_fmt = [(c, choice_to_str(c)) for c in choices]

            choices_fmt = sorted(choices_fmt) # sort 2-tuples = according to first value in tuple

            new_ctrl = ComboBox(
                        self.panel,
                        wx.ID_ANY,
                        value='', pos=(0, 0), size=(100, 16),
                        # FIXME: should be readonly, but it fails with GetInsertionPoint
                        style=wx.BORDER_NONE | wx.TE_PROCESS_ENTER | wx.CB_READONLY)

            def _eat_event(evt):
                """ Quick and dirty empty function used to 'eat'
                mouse wheel events
                """
                pass
            new_ctrl.Bind(wx.EVT_MOUSEWHEEL, _eat_event)

            # Set choices
            if unit is None:
                unit = ""
            for choice, formatted in choices_fmt:
                new_ctrl.Append(u"%s %s" % (formatted, unit), choice)

            # A small wrapper function makes sure that the value can
            # be set by passing the actual value (As opposed to the text label)
            def cb_set(value, ctrl=new_ctrl, unit=unit):
                for i in range(ctrl.Count):
                    if ctrl.GetClientData(i) == value:
                        logging.debug("Setting ComboBox value to %s", ctrl.Items[i])
                        return ctrl.SetValue(ctrl.Items[i])
                else:
                    logging.warning("No existing label found for value %s", value)
                    return ctrl.GetValue()

            # equivalent wrapper function to retrieve the actual value
            def cb_get(ctrl=new_ctrl, name=name):
                value = ctrl.GetValue()
                # Try to use the predefined value if it's available
                for i in range(ctrl.Count):
                    if ctrl.Items[i] == value:
                        logging.debug("Getting CB value %s", ctrl.GetClientData(i))
                        return ctrl.GetClientData(i)
                else:
                    logging.error("Failed to find value %s for axis %s", value, name)

            ac = AxisConnector(name, comp, new_ctrl,
                                pos_2_ctrl=cb_set,
                                ctrl_2_pos=cb_get,
                                events=(wx.EVT_COMBOBOX, wx.EVT_TEXT_ENTER))

        self._gb_sizer.Add(new_ctrl, (self.num_entries, 1),
                           flag=wx.ALL | wx.EXPAND | wx.ALIGN_CENTER_VERTICAL,
                           border=5)
        # AxisConnector follows VigilantAttributeConnector interface, so can be
        # used (duck typing).
        ne = SettingEntry(name, None, comp, lbl_ctrl, new_ctrl, ac)
        self.entries.append(ne)
        self.num_entries += 1

        if self.highlight_change:
            bind_menu(ne)

        self.fold_panel.Parent.Layout()

    def add_metadata(self, key, value):
        """
        Adds an entry representing a specific metadata. According to the
         metadata key, the right representation is used for the value.
        key (model.MD_*): the metadata key
        value (depends on the metadata): the value to display
        """
        # By default the key is a nice user-readable string
        label = unicode(key)

        # Convert value to a nice string according to the metadata type
        try:
            if key == model.MD_ACQ_DATE:
                # convert to a date using the user's preferences
                nice_str = time.strftime(u"%c", time.localtime(value))
            else:
                # Still try to beautify a bit if it's a number
                if (isinstance(value, (int, long, float)) or
                    (isinstance(value, collections.Iterable) and len(value) > 0
                      and isinstance(value[0], (int, long, float)))
                    ):
                    nice_str = readable_str(value, sig=3)
                else:
                    nice_str = unicode(value)
        except Exception:
            logging.exception("Trying to convert metadata %s", key)
            nice_str = "N/A"

        self.add_label(label, nice_str)

    def on_setting_changed(self, evt):
        logging.debug("Setting has changed")
        evt_obj = evt.GetEventObject()
        # Make sure the message is sent form the main thread
        wx.CallAfter(pub.sendMessage, 'setting.changed', setting_ctrl=evt_obj)
        evt.Skip()

    def Refresh(self):
        self.panel.Layout()

        p = self.panel.Parent
        while p:
            if isinstance(p, wx.ScrolledWindow):
                p.FitInside()
                p = None
            else:
                p = p.Parent

class SemSettingsPanel(SettingsPanel):
    pass

class OpticalSettingsPanel(SettingsPanel):
    pass

class AngularSettingsPanel(SettingsPanel):
    pass

class SpectrumSettingsPanel(SettingsPanel):
    pass

class FileInfoSettingsPanel(SettingsPanel):
    pass

class SettingsBarController(object):
    """ The main controller class for the settings panel in the live view and
    acquisition frame.

    This class can be used to set, get and otherwise manipulate the content
    of the setting panel.
    """

    def __init__(self, tab_data, highlight_change=False):
        self._tab_data_model = tab_data
        self.settings_panels = []


    def pause(self):
        """ Pause VigilantAttributeConnector related control updates """
        for panel in self.settings_panels:
            panel.pause()

    def resume(self):
        """ Resume VigilantAttributeConnector related control updates """
        for panel in self.settings_panels:
            panel.resume()

    @property
    def entries(self):
        """
        A list of all the setting entries of all the panels
        """
        entries = []
        for panel in self.settings_panels:
            entries.extend(panel.entries)
        return entries

    def get_entry(self, name):
        """ TODO: not very useful, because name are not unique """
        for panel in self.settings_panels:
            for entry in panel.entries:
                if entry.name == name:
                    return entry
        return None

    def enable(self, enabled):
        for panel in self.settings_panels:
            panel.enable(enabled)

    def add_component(self, label, comp, panel):

        self.settings_panels.append(panel)

        try:
            name = "Name" # for exception handling only
            panel.add_label(label, comp.name, selectable=False)
            vigil_attrs = getVAs(comp)
            for name, value in vigil_attrs.items():
                if comp.role in CONFIG and name in CONFIG[comp.role]:
                    conf = CONFIG[comp.role][name]
                else:
                    logging.info("No config found for %s: %s", comp.role, name)
                    conf = None
                panel.add_value(name, value, comp, conf)
        except TypeError:
            msg = "Error adding %s setting for: %s"
            logging.exception(msg, comp.name, name)

    def add_stream(self, stream):
        pass


class SecomSettingsController(SettingsBarController):

    def __init__(self, parent_frame, tab_data, highlight_change=False):
        super(SecomSettingsController, self).__init__(tab_data,
                                                      highlight_change)
        main_data = tab_data.main

        self._sem_panel = SemSettingsPanel(
                                    parent_frame.fp_settings_secom_sem,
                                    "No SEM found",
                                    highlight_change)

        self._optical_panel = OpticalSettingsPanel(
                                    parent_frame.fp_settings_secom_optical,
                                    "No optical microscope found",
                                    highlight_change)

        # Query Odemis daemon (Should move this to separate thread)
        if main_data.ccd:
            self.add_component("Camera",
                                main_data.ccd,
                                self._optical_panel)

            if main_data.light:
                self._optical_panel.add_divider()

                self._optical_panel.add_value(
                                        "power",
                                        main_data.light.power,
                                        main_data.light,
                                        CONFIG["light"]["power"]
                                        )

        if main_data.ebeam:
            self.add_component("SEM", main_data.ebeam, self._sem_panel)

class LensAlignSettingsController(SettingsBarController):

    def __init__(self, parent_frame, tab_data, highlight_change=False):
        super(LensAlignSettingsController, self).__init__(tab_data,
                                                          highlight_change)
        main_data = tab_data.main

        self._sem_panel = SemSettingsPanel(
                                    parent_frame.fp_lens_sem_settings,
                                    "No SEM found",
                                    highlight_change)

        self._optical_panel = OpticalSettingsPanel(
                                    parent_frame.fp_lens_opt_settings,
                                    "No optical microscope found",
                                    highlight_change)

        # Query Odemis daemon (Should move this to separate thread)
        if main_data.ccd:
            self.add_component("Camera",
                                main_data.ccd,
                                self._optical_panel)

        # TODO: allow to change light.power

        if main_data.ebeam:
            self.add_component("SEM", main_data.ebeam, self._sem_panel)

class SparcSettingsController(SettingsBarController):

    def __init__(self, parent_frame, tab_data, highlight_change=False):
        super(SparcSettingsController, self).__init__(tab_data,
                                                      highlight_change)
        main_data = tab_data.main

        main_data.is_acquiring.subscribe(self.on_acquisition)

        self._sem_panel = SemSettingsPanel(
                                    parent_frame.fp_settings_sparc_sem,
                                    "No SEM found",
                                    highlight_change)
        self._angular_panel = AngularSettingsPanel(
                                    parent_frame.fp_settings_sparc_angular,
                                    "No angular camera found",
                                    highlight_change)
        self._spectrum_panel = SpectrumSettingsPanel(
                                    parent_frame.fp_settings_sparc_spectrum,
                                    "No spectrometer found",
                                    highlight_change)

        # Somewhat of a hack to get direct references to a couple of controls
        self.angular_rep_ent = None
        self.spectro_rep_ent = None
        self.spec_pxs_ent = None

        if main_data.ebeam:
            self.add_component(
                    "SEM",
                    main_data.ebeam,
                    self._sem_panel
            )

        acq_streams = tab_data.acquisitionView.getStreams()

        if main_data.spectrometer:
            self.add_component(
                    "Spectrometer",
                    main_data.spectrometer,
                    self._spectrum_panel
            )

            self._spectrum_panel.add_divider()
            spectrum_streams = [s for s in acq_streams if isinstance(s, SpectrumStream)]
            assert len(spectrum_streams) <= 1 # there should be one or none
            for s in spectrum_streams:
                self.spectro_rep_ent = self._spectrum_panel.add_value(
                                            "repetition",
                                            s.repetition,
                                            None,  #component
                                            CONFIG["streamspec"]["repetition"])

                self.spec_pxs_ent = self._spectrum_panel.add_value(
                                            "pixelSize",
                                            s.pixelSize,
                                            None,  #component
                                            CONFIG["streamspec"]["pixelSize"])

                # # Added for debug only
                # self._spectrum_panel.add_value(
                #         "roi",
                #         s.roi,
                #         None,  #component
                #         CONFIG["streamspec"]["roi"])

            # Add spectrograph control if available
            if main_data.spectrograph:
                # Without the "wavelength" axis, it's boring
                if "wavelength" in main_data.spectrograph.axes:
                    self._spectrum_panel.add_axis(
                        "wavelength",
                        main_data.spectrograph,
                        CONFIG["spectrograph"]["wavelength"])
                if "grating" in main_data.spectrograph.axes:
                    self._spectrum_panel.add_axis(
                        "grating",
                        main_data.spectrograph,
                        CONFIG["spectrograph"]["grating"])

        else:
            parent_frame.fp_settings_sparc_spectrum.Hide()

        if main_data.ccd:
            self.add_component("Camera", main_data.ccd, self._angular_panel)

            self._angular_panel.add_divider()
            ar_streams = [s for s in acq_streams if isinstance(s, ARStream)]
            assert len(ar_streams) <= 1 # there should be one or none
            for s in ar_streams:
                self.angular_rep_ent = self._angular_panel.add_value(
                                        "repetition",
                                        s.repetition,
                                        None,  #component
                                        CONFIG["streamar"]["repetition"])

        else:
            parent_frame.fp_settings_sparc_angular.Hide()

    def on_acquisition(self, is_acquiring):
        self.enable(not is_acquiring)

class AnalysisSettingsController(SettingsBarController):

    def __init__(self, parent, tab_data):
        super(AnalysisSettingsController, self).__init__(tab_data)

        self.parent = parent

        self._acq_file_panel = FileInfoSettingsPanel(
                                    parent.fp_inspect_file_info,
                                    "No file loaded")

        self._cal_file_panel = FileInfoSettingsPanel(
                                    parent.fp_inspect_file_info,
                                    "No calibration file loaded")
        self._cal_file_panel.Hide()

        parent.Layout()

        self.tab_data = tab_data

        self.tab_data.acq_fileinfo.subscribe(self.on_acqfile_change, init=True)
        self.tab_data.cal_fileinfo.subscribe(self.on_calfile_change, init=True)

    def on_acqfile_change(self, fi):
        """ Update the data we wish to display from the FileInfo object """

        self._acq_file_panel.clear()

        if fi:
            se_file = self._acq_file_panel.add_label("File", fi.acq_file_basename)
            se_file.ctrl.SetInsertionPointEnd()


            se_path = self._acq_file_panel.add_label("Path", fi.acq_file_path)
            # show the end of the path (usually more important)
            se_path.ctrl.SetInsertionPointEnd()

            for key, value in fi.metadata.items():
                self._acq_file_panel.add_metadata(key, value)

            self._acq_file_panel.Refresh()

    def on_calfile_change(self, fi):
        """ Update the calibartion file controls """

        self._cal_file_panel.clear()

        if fi:
            btn_entry = self._cal_file_panel.add_browse_button(
                                "Calibration file", fi.acq_file_name)
            wildcards, _ = odemis.gui.util.formats_to_wildcards(
                                    odemis.dataio.get_available_formats(),
                                    include_all=True)
            btn_entry.ctrl.SetWildcard(wildcards)
            btn_entry.ctrl.SetValue(fi.acq_file_name)
            pth_entry = self._cal_file_panel.add_label("Path",
                                                   fi.acq_file_path or " ")

            def on_changed(evt):
                filebrowser = evt.GetEventObject()
                fi.acq_file_name = filebrowser.GetValue()
                pth_entry.ctrl.SetValue(filebrowser.path)

                conf = get_calibration_conf()
                conf.set("history", "last", filebrowser.GetValue() or "")
                conf.write()

            btn_entry.ctrl.Bind(wx.EVT_TEXT, on_changed)

            self._cal_file_panel.Refresh()
            self._cal_file_panel.Show()
            self.parent.Layout()
        else:
            self._cal_file_panel.Hide()
            self.parent.Layout()

class SparcAlignSettingsController(SettingsBarController):

    def __init__(self, parent_frame, tab_data):
        super(SparcAlignSettingsController, self).__init__(tab_data)
        main_data = tab_data.main

        self._ar_panel = AngularSettingsPanel(
                                parent_frame.fp_sparc_settings,
                                "No angular resolved camera found")

        # Query Odemis daemon (Should move this to separate thread)
        if main_data.ccd:
            self.add_component("Camera", main_data.ccd, self._ar_panel)

