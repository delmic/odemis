# -*- coding: utf-8 -*-

"""
:author: Rinze de Laat <laat@delmic.com>
:copyright: © 2013 Rinze de Laat, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the terms  of the GNU
    General Public License version 2 as published by the Free Software  Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;  without
    even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR  PURPOSE. See the GNU
    General Public License for more details.

    You should have received a copy of the GNU General Public License along with Odemis. If not,
    see http://www.gnu.org/licenses/.

Module purpose
--------------

This module contains functions that help in the generation of dynamic configuration values.

"""

from __future__ import division
from collections import OrderedDict
import collections
import logging
import math
import numbers
import re
import wx
from wx.lib.pubsub import pub

from odemis import model
import odemis.gui
from odemis.model import NotApplicableError


# Setting choice generators, used to create certain value choices for settings controls

def resolution_from_range(comp, va, conf, init=None):
    """ Construct a list of resolutions depending on range values

    init (set or None): values that will be always in the choices. If None, it will just ensure that
        the current value is present.

    """

    cur_val = va.value

    if len(cur_val) != 2:
        logging.warning("Got a resolution not of length 2: %s", cur_val)
        return {cur_val: str(cur_val)}

    try:
        if init is None:
            choices = {cur_val}
        else:
            choices = init
        num_pixels = cur_val[0] * cur_val[1]
        res = va.range[1]  # start with max resolution

        for _ in range(10):
            choices.add(res)
            res = (res[0] // 2, res[1] // 2)

            if len(choices) >= 4 and (res[0] * res[1] < num_pixels):
                break

        return OrderedDict({v: "%d x %d" % v for v in choices})
        # return sorted(choices)  # return a list, to be sure it's in order
    except NotApplicableError:
        return {cur_val: str(cur_val)}


def resolution_from_range_plus_point(comp, va, conf):
    """ Same as resolution_from_range() but also add a 1x1 value """
    return resolution_from_range(comp, va, conf, init={va.value, (1, 1)})


def binning_1d_from_2d(comp, va, conf):
    """ Find simple binnings available in one dimension

    We assume pixels are always square. The binning provided by a camera is normally a 2-tuple of
    integers.

    """

    cur_val = va.value
    if len(cur_val) != 2:
        logging.warning("Got a binning not of length 2: %s, will try anyway", cur_val)

    try:
        choices = {cur_val[0]}
        minbin = max(va.range[0])
        maxbin = min(va.range[1])

        # add up to 5 binnings
        b = int(math.ceil(minbin))  # in most cases, that's 1
        for _ in range(6):
            if minbin <= b <= maxbin:
                choices.add(b)

            if len(choices) >= 5 and b >= cur_val[0]:
                break

            b *= 2

        choices = sorted(list(choices))
        return OrderedDict(tuple(((v, v), str(int(v))) for v in choices))
    except NotApplicableError:
        return {(cur_val[0], cur_val[0]): str(cur_val[0])}


def binning_firstd_only(comp, va, conf):
    """ Find simple binnings available in the first dimension

    The second dimension stays at a fixed size.

    """

    cur_val = va.value[0]

    try:
        choices = {cur_val}
        minbin = va.range[0][0]
        maxbin = va.range[1][0]

        # add up to 5 binnings
        b = int(math.ceil(minbin))  # in most cases, that's 1
        for _ in range(6):
            if minbin <= b <= maxbin:
                choices.add(b)

            if len(choices) >= 5 and b >= cur_val:
                break

            b *= 2

        return sorted(choices)  # return a list, to be sure it's in order
    except NotApplicableError:
        return [cur_val]


def hfw_choices(comp, va, conf):
    """ Return a list of HFW choices

    If the VA has predefined choices, return those. Otherwise calculate the choices using the range
    of the VA.

    """

    try:
        choices = va.choices
    except NotApplicableError:
        mi, ma, = va.range
        choices = [mi]
        step = 1
        while choices[-1] < ma:
            choices.append(mi * 10 ** step)
            step += 1

    return choices


def mag_if_no_hfw_ctype(comp, va, conf):
    """ Return the control type for e-beam magnification

    This control is only useful if horizontalFoV is available.

    :return: (int) The control type

    """

    if (hasattr(comp, "horizontalFoV") and isinstance(comp.horizontalFoV,
                                                      model.VigilantAttributeBase)):
        return odemis.gui.CONTROL_NONE
    else:
        # Just use a text field => it's for copy-paste
        return odemis.gui.CONTROL_FLT

# END Setting choice generators


def determine_default_control(va):
    """ Determine the default control to use to represent a vigilant attribute

    :param va: (VigilantAttribute)

    :return: (odemis.gui.CONTROL_*)

    """

    if not va:
        logging.warn("No VA provided!")
        return odemis.gui.CONTROL_NONE

    if va.readonly:
        # Uncomment this line to hide Read only VAs by default
        # return odemis.gui.CONTROL_NONE
        return odemis.gui.CONTROL_READONLY
    else:
        try:
            # This statement will raise an exception when no choices are present
            logging.debug("Found choices %s", va.choices)

            max_num_choices = 5
            max_value_len = 5

            # If there are too many choices, or their values are too long in string
            # representation, use a drop-down box

            choices_str = "".join([str(c) for c in va.choices])

            if len(va.choices) <= 1:
                # One or no choices, so the control can be read only
                return odemis.gui.CONTROL_READONLY
            elif (len(va.choices) < max_num_choices and
                  len(choices_str) < max_num_choices * max_value_len):
                # Short strings values can be accommodated by radio buttons
                return odemis.gui.CONTROL_RADIO
            else:
                # Combo boxes (drop down) are used otherwise
                return odemis.gui.CONTROL_COMBO
        except (AttributeError, NotApplicableError):
            pass

        try:
            # An exception will be raised if no range attribute is found
            logging.debug("Found range %s", va.range)

            # TODO: if unit is "s" => scale=exp
            if isinstance(va.value, (int, long, float)):
                # If the value is a number with a range, return the slider control
                return odemis.gui.CONTROL_SLIDER
        except (AttributeError, NotApplicableError):
            pass

        # Return default control
        return odemis.gui.CONTROL_TEXT


def bind_setting_context_menu(settings_entry):
    """ Add a context menu to the settings entry to reset it to its original value

    The added menu is used in the acquisition window, to give the user the ability to reset values
    that have been adjusted by Odemis.

    :param settings_entry: (SettingEntry) Must at least have a valid label, ctrl and va

    """

    orig_val = settings_entry.vigilattr.value

    def reset_value(_):
        """ Reset the value of the setting VA back to its original value """
        settings_entry.vigilattr.value = orig_val
        wx.CallAfter(pub.sendMessage, 'setting.changed')

    def show_reset_menu(evt):
        """ Create and show a context menu which has a menu item to reset the settings's value """
        menu = wx.Menu()
        mi = wx.MenuItem(menu, wx.NewId(), 'Reset value')
        eo = evt.GetEventObject()
        eo.Bind(wx.EVT_MENU, reset_value, mi)
        menu.AppendItem(mi)
        # Disable the menu item if the value has not changed
        disable = settings_entry.vigilattr.value != orig_val
        mi.Enable(disable)
        # Show the menu
        eo.PopupMenu(menu)

    # Bind the menu to both the label and the value controls
    settings_entry.value_ctrl.Bind(wx.EVT_CONTEXT_MENU, show_reset_menu)
    settings_entry.lbl_ctrl.Bind(wx.EVT_CONTEXT_MENU, show_reset_menu)


def get_va_meta(comp, va, conf):
    """ Retrieve the range and choices values from the vigilant attribute or override them
    with the values provided in the configuration.

    """

    r = conf.get("range", (None, None))
    minv, maxv = (None, None)

    try:
        if callable(r):
            minv, maxv = r(comp, va, conf)
        elif r == (None, None):
            minv, maxv = va.range
        else:
            # Intersect the two ranges
            # TODO: handle iterables
            minv, maxv = r
            minv, maxv = max(minv, va.range[0]), min(maxv, va.range[1])
    except (AttributeError, NotApplicableError):
        pass

    # Ensure the range encompasses the current value
    if None not in (minv, maxv):
        val = va.value
        if isinstance(val, numbers.Real):
            minv, maxv = min(minv, val), max(maxv, val)

    choices = conf.get("choices", None)

    try:
        if callable(choices):
            choices = choices(comp, va, conf)
        elif choices is None:
            choices = va.choices
        elif hasattr(va, "choices") and isinstance(va.choices, set):
            # Intersect the two choice sets
            choices &= va.choices
        elif hasattr(va, "choices") and isinstance(va.choices, collections.Mapping):  # dicts
            # Only keep the items of va.choices which are also choices
            choices = {x: va.choices[x] for x in va.choices if x in choices}
        elif hasattr(va, "range") and isinstance(va.range, collections.Iterable):
            # Ensure that each choice is within the range
            rng = va.range
            choices = set(c for c in choices if rng[0] <= c <= rng[1])
    except (AttributeError, NotApplicableError):
        pass

    # Ensure the choices contain the current value
    if choices is not None and va.value not in choices:
        logging.info("Current value %s not in choices %s", va.value, choices)
        if isinstance(choices, set):
            choices.add(va.value)
        elif isinstance(choices, dict):
            choices[va.value] = unicode(va.value)
        else:
            logging.warning("Don't know how to extend choices of type %s", type(choices))

    # Get unit from config, vigilant attribute or use an empty one
    unit = conf.get('unit', va.unit or "")

    return minv, maxv, choices, unit


def choice_to_str(choice):
    if not isinstance(choice, collections.Iterable):
        choice = [unicode(choice)]
    return u" x ".join([unicode(c) for c in choice])


def label_to_human(camel_label):
    """ Convert a camel-case label into a human readable string """

    # Add space after each upper case, then make the first letter uppercase and all the other ones
    # lowercase
    return re.sub(r"([A-Z])", r" \1", camel_label).capitalize()
