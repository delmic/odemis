# -*- coding: utf-8 -*-

"""

@author: Rinze de Laat

Copyright © 2012 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

Content:

    This module contains classes describing various customized text fields used
    throughout Odemis.

"""
from builtins import str, chr # For Python 2 & 3

import locale
import logging
import math
import os
import re
import string
import sys

import wx
import wx.lib.mixins.listctrl as listmix

from odemis.gui import FG_COLOUR_DIS, FG_COLOUR_EDIT
from odemis.util import units
from odemis.util.units import decompose_si_prefix, si_scale_val

# Locale is needed for correct string sorting
locale.setlocale(locale.LC_ALL, "")

# The SuggestTextCtrl and ChoiceListCtrl class are adaptations of the
# TextCtrlAutoComplete class found at
# http://wiki.wxpython.org/index.cgi/TextCtrlAutoComplete
#
# Adaptation for Delmic by R. de Laat
#
# wxPython Custom Widget Collection 20060207
# Written By: Edward Flick (eddy -=at=- cdf-imaging -=dot=- com)
#             Michele Petrazzo (michele -=dot=- petrazzo -=at=- unipex =dot= it)
#             Will Sadkin (wsadkin-=at=- nameconnector -=dot=- com)
# Copyright 2006 (c) CDF Inc. ( http://www.cdf-imaging.com )
# Contributed to the wxPython project under the wxPython project's license.
#

class ChoiceListCtrl(wx.ListCtrl, listmix.ListCtrlAutoWidthMixin):
    """ Choice list used by the SuggestTextCtrl class """

    def __init__(self, *args, **kwargs):
        wx.ListCtrl.__init__(self, *args, **kwargs)
        listmix.ListCtrlAutoWidthMixin.__init__(self)


LIST_MIN_WIDTH = 300  # px


class SuggestTextCtrl(wx.TextCtrl, listmix.ColumnSorterMixin):
    def __init__(self, parent, choices=None, drop_down_click=True,
                 col_fetch=-1, col_search=0, hide_on_no_match=True,
                 select_callback=None, entry_callback=None, match_function=None,
                 **text_kwargs):
        """
        Constructor works just like wx.TextCtrl except you can pass in a
        list of choices.  You can also change the choice list at any time
        by calling SetChoices.

        When a choice is picked, or the user has finished typing, a
        EVT_COMMAND_ENTER is sent.

        """
        text_kwargs['style'] = wx.TE_PROCESS_ENTER | wx.BORDER_NONE | text_kwargs.get('style', 0)
        super(SuggestTextCtrl, self).__init__(parent, **text_kwargs)

        # Some variables
        self._drop_down_click = drop_down_click
        self._choices = choices
        self._lastinsertionpoint = 0
        self._hide_on_no_match = hide_on_no_match
        self._select_callback = select_callback
        self._entry_callback = entry_callback
        self._match_function = self._match_start_text if match_function is None else match_function
        self._screen_size = (wx.SystemSettings.GetMetric(wx.SYS_SCREEN_X),
                             wx.SystemSettings.GetMetric(wx.SYS_SCREEN_Y))

        # sort variable needed by listmix
        self.itemDataMap = dict()

        # Load and sort data
        if not self._choices:
            self._choices = []
            # raise ValueError, "Pass me at least one of multiChoices OR choices"

        # widgets
        self.dropdown = wx.PopupWindow(self)

        # Control the style
        flags = wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_SORT_ASCENDING
        flags = flags | wx.LC_NO_HEADER

        # Create the list and bind the events
        self.dropdownlistbox = ChoiceListCtrl(self.dropdown, style=flags, pos=wx.Point(0, 0))

        ln = 1
        # else: ln = len(choices)
        listmix.ColumnSorterMixin.__init__(self, ln)
        # load the data
        # self.SetChoices(choices)

        gp = self

        while gp is not None:
            gp.Bind(wx.EVT_MOVE, self.onControlChanged, gp)
            gp.Bind(wx.EVT_SIZE, self.onControlChanged, gp)
            gp = gp.GetParent()

        self.Bind(wx.EVT_KILL_FOCUS, self.onControlChanged, self)
        self.Bind(wx.EVT_TEXT, self.onEnteredText, self)
        self.Bind(wx.EVT_KEY_DOWN, self.onKeyDown, self)

        # If need drop down on left click
        if drop_down_click:
            self.Bind(wx.EVT_LEFT_DOWN, self.onClickToggleDown, self)
            self.Bind(wx.EVT_LEFT_UP, self.onClickToggleUp, self)

        self.dropdown.Bind(wx.EVT_LISTBOX, self.onListItemSelected, self.dropdownlistbox)

        self.dropdownlistbox.Bind(wx.EVT_LEFT_DOWN, self.onListClick)
        self.dropdownlistbox.Bind(wx.EVT_LEFT_DCLICK, self.onListDClick)
        # This causes the text the user is typing to directly auto-fill with
        # the closest possibility.
        # self.dropdown.Bind(wx.EVT_LIST_ITEM_SELECTED, self.onListDClick)
        self.dropdownlistbox.Bind(wx.EVT_LIST_COL_CLICK, self.onListColClick)

        # TODO: needed?
        self.il = wx.ImageList(16, 16)
        self.dropdownlistbox.SetImageList(self.il, wx.IMAGE_LIST_SMALL)
        self._ascending = True

    def _send_change_event(self):
        """
        Sends an event EVT_COMMAND_ENTER to notify that the value has changed
        """
        changeEvent = wx.CommandEvent(wx.wxEVT_COMMAND_ENTER, self.Id)
        wx.PostEvent(self, changeEvent)

    def GetListCtrl(self):
        return self.dropdownlistbox

    # -- event methods

    def onListClick(self, evt):
        toSel, dummy = self.dropdownlistbox.HitTest(evt.GetPosition())
        #no values on position, return
        if toSel == -1:
            return
        self.dropdownlistbox.Select(toSel)

    def onListDClick(self, evt):
        self._setValueFromSelected()

    def onListColClick(self, evt):
        col = evt.GetColumn()
        #reverse the sort
        if col == self._col_search:
            self._ascending = not self._ascending
        self.SortListItems(evt.GetColumn(), ascending=self._ascending)
        self._col_search = evt.GetColumn()
        evt.Skip()

    def _match_start_text(self, text, choice):
        """
        Default match function: match if the choices starts with the text, case
          insensitive
        text (str)
        choice (str)
        return bool
        """
        return choice.lower().startswith(text.lower())

    def onEnteredText(self, event):
        text = event.GetString()
        if self._entry_callback:
            self._entry_callback()
        if not text:
            # control is empty; hide dropdown if shown:
            if self.dropdown.IsShown():
                self._showDropDown(False)
            event.Skip()
            return

        found = None
        choices = self._choices

        # Find the first match, based on the match function
        for numCh, choice in enumerate(choices):
            if self._match_function(text, choice):
                found = numCh
                break
        else:
            # Nothing found. Instead of completely giving up, try to match any part of the text
            for numCh, choice in enumerate(choices):
                if text.lower() in choice.lower():
                    found = numCh
                    break

        if found is not None:
            # Found an entry => select it
            self._showDropDown(True)
            item = self.dropdownlistbox.GetItem(found)
            toSel = item.GetId()
            self.dropdownlistbox.Select(toSel)
        else:
            # Nothing found => unselect any item still selected
            self.dropdownlistbox.Select(self.dropdownlistbox.GetFirstSelected(), False)
            if self._hide_on_no_match:
                self._showDropDown(False)

        self._listItemVisible()
        event.Skip()

    def onKeyDown(self, event):
        """ Do some work when the user press on the keys:
            up and down: move the cursor
            left and right: move the search
        """
        sel = self.dropdownlistbox.GetFirstSelected()
        KC = event.GetKeyCode()
        if KC == wx.WXK_DOWN:
            if sel < self.dropdownlistbox.GetItemCount() - 1:
                self.dropdownlistbox.Select(sel + 1)
                self._listItemVisible()
            self._showDropDown()
        elif KC == wx.WXK_UP:
            if sel > 0:
                self.dropdownlistbox.Select(sel - 1)
                self._listItemVisible()
            self._showDropDown()
        elif KC == wx.WXK_RETURN or KC == wx.WXK_NUMPAD_ENTER:
            visible = self.dropdown.IsShown()
            if visible:
                self._setValueFromSelected()
            else:
                self._send_change_event()
        elif KC == wx.WXK_ESCAPE:
            self._showDropDown(False)
        else:
            event.Skip()

    def onListItemSelected(self, event):
        self._setValueFromSelected()
        event.Skip()

    def onClickToggleDown(self, event):
        self._lastinsertionpoint = self.GetInsertionPoint()
        event.Skip()

    def onClickToggleUp(self, event):
        if self.GetInsertionPoint() == self._lastinsertionpoint:
            self._showDropDown(not self.dropdown.IsShown())
        event.Skip()

    def onControlChanged(self, event):
        if self and self.IsShown():
            self._showDropDown(False)

        if isinstance(event, wx.FocusEvent):
            # KILL_FOCUS => that means the user is happy with the current value
            self._send_change_event()

        event.Skip()

    def SetChoices(self, choices):
        """
        Sets the choices available in the popup wx.ListBox.
        The items will be sorted case insensitively.
        """
        self._choices = choices
        flags = wx.LC_REPORT | wx.LC_SINGLE_SEL | \
                wx.LC_SORT_ASCENDING | wx.LC_NO_HEADER
        self.dropdownlistbox.SetWindowStyleFlag(flags)
        if not isinstance(choices, list):
            self._choices = list(choices)
        self._choices.sort(key=str.lower)
        self._updateDataList(self._choices)
        self.dropdownlistbox.InsertColumn(0, "")
        for num, colVal in enumerate(self._choices):
            index = self.dropdownlistbox.InsertItem(sys.maxsize, colVal, -1)
            self.dropdownlistbox.SetItem(index, 0, colVal)
            self.dropdownlistbox.SetItemData(index, num)
        self._setListSize()
        # there is only one choice for both search and fetch if setting a
        # single column:
        self._col_search = 0
        self._col_fetch = -1

    def GetChoices(self):
        return self._choices

    def Setselect_callback(self, cb=None):
        self._select_callback = cb

    def Setentry_callback(self, cb=None):
        self._entry_callback = cb

    def Setmatch_function(self, mf=None):
        if mf is None:
            mf = self._match_start_text
        self._match_function = mf

    #-- Internal methods
    def _setValueFromSelected(self):
        """ Sets the wx.TextCtrl value from the selected wx.ListCtrl item.
        Will do nothing if no item is selected in the wx.ListCtrl.
        """
        sel = self.dropdownlistbox.GetFirstSelected()
        if sel > -1:
            if self._col_fetch != -1:
                col = self._col_fetch
            else:
                col = self._col_search
            itemtext = self.dropdownlistbox.GetItem(sel, col).GetText()
            if self._select_callback:
                dd = self.dropdownlistbox
                values = [dd.GetItem(sel, x).GetText()
                          for x in range(dd.GetColumnCount())]
                self._select_callback(values)
            self.SetValue(itemtext)
            self.SetToolTip(itemtext)
            self.SetInsertionPointEnd()
            self.SetSelection(-1, -1)
            self._showDropDown(False)
            self._send_change_event()

    def _showDropDown(self, show=True):
        """
        Either display the drop down list (show = True) or hide it (show = False).
        """
        if show:
            dwidth, dheight = self.dropdown.GetSize()
            # Use the width of the text control
            width, height = self.GetSize()
            width = max(width, LIST_MIN_WIDTH)
            x, y = self.ClientToScreen(0, height)
            if dwidth != width:
                self.dropdown.SetSize((width, dheight))
                self.dropdownlistbox.SetSize(self.dropdown.GetClientSize())

            # Make sure it fits in the screen
            if x + width > self._screen_size[0]:
                x = self._screen_size[0] - width  # Touch the side of the screen
            if y + dheight > self._screen_size[1]:
                y -= height + dheight  # Drop "up" instead of drop down
            self.dropdown.SetPosition(wx.Point(x, y))
        self.dropdown.Show(show)

    def _listItemVisible(self):
        """
        Moves the selected item to the top of the list ensuring it is always visible.
        """
        toSel = self.dropdownlistbox.GetFirstSelected()
        if toSel == -1:
            return
        self.dropdownlistbox.EnsureVisible(toSel)

    def _updateDataList(self, choices):
        #delete, if need, all the previous data
        if self.dropdownlistbox.GetColumnCount() != 0:
            self.dropdownlistbox.DeleteAllColumns()
            self.dropdownlistbox.DeleteAllItems()
        #and update the dict
        if choices:
            for numVal, data in enumerate(choices):
                self.itemDataMap[numVal] = data
        else:
            numVal = 0
        self.SetColumnCount(numVal)

    def _setListSize(self):
        choices = self._choices
        longest = 0
        for choice in choices:
            longest = max(len(choice), longest)
        longest += 3
        itemcount = min(len(choices), 7) + 2
        charheight = self.dropdownlistbox.GetCharHeight()
        charwidth = self.dropdownlistbox.GetCharWidth()
        self.popupsize = wx.Size(charwidth * longest, charheight * itemcount)
        self.dropdownlistbox.SetSize(self.popupsize)
        self.dropdown.SetClientSize(self.popupsize)


class _NumberValidator(wx.Validator):

    def __init__(self, min_val=None, max_val=None, choices=None, unit=None):
        """ Constructor """
        super(_NumberValidator, self).__init__()

        self.Bind(wx.EVT_CHAR, self.on_char)

        # this is a kludge because default value in XRC is 0:
        if min_val == 0 and max_val == 0:
            min_val = None
            max_val = None

        # Minimum and maximum allowed values
        self.min_val = min_val
        self.max_val = max_val
        self.choices = choices
        self.unit = unit

        if None not in (min_val, max_val) and min_val > max_val:
            raise ValueError("Min value is bigger than max value: %r > %r" % (min_val, max_val))
        self._validate_choices()

        # Build a regular expression pattern against which we can match the data that is being
        # entered

        reg_data = {
            'negative_sign': '',
            'unit': r"[ ]*[GMkmµunp]?(%s)?" % unit if unit else ''
        }

        if (
                (min_val is None or min_val < 0) or
                (max_val is not None and max_val < 0) or
                (choices and min(choices) < 0)
        ):
            reg_data['negative_sign'] = '-'

        # Update the regular expression with the variables we've discovered
        self.entry_regex = self.entry_regex.format(**reg_data)
        # Compile the regex pattern what will be used for validation
        self.entry_pattern = re.compile(self.entry_regex)

    def set_value_range(self, min_val, max_val):
        # TODO: check values and recompute .legal as in init
        self.min_val = min_val
        self.max_val = max_val

    def GetRange(self):
        return self.min_val, self.max_val

    def _validate_choices(self):
        """ Validate all the choice values, if choice values are defined """

        if self.choices:
            for c in self.choices:
                if not self._is_valid_value(c):
                    raise ValueError("Illegal value (%s) found in choices" % c)

    def _is_valid_value(self, val):
        """ Validate the given value

        Args:
            val (str):

        Returns:
            (boolean): True if the given string is valid

        """

        # Don't fail on empty string
        if val is False or val is None:
            return False

        try:
            num = self._cast(val)
        except ValueError:
            return False

        if self.choices and num not in self.choices:
            return False
        if self.min_val and num < self.min_val:
            return False
        if self.max_val and num > self.max_val:
            return False

        return True

    def _get_str_value(self):
        """ Return the string value of the wx.Window to which this validator belongs """

        # Special trick in, the very likely, case we are validating a NumberTextCtrl, which has it's
        # default 'GetValue' method replaced with one that returns number instances

        fld = self.GetWindow()

        if hasattr(fld, "get_value_str"):
            val = fld.get_value_str()
        else:
            val = fld.GetValue()
        return val

    def Clone(self):
        raise NotImplementedError

    def on_char(self, event):
        """ This method prevents the entry of illegal characters """
        ukey = event.GetUnicodeKey()
        # Allow control keys to propagate (most of them are WXK_NONE in unicode)
        if ukey < wx.WXK_SPACE:
            event.Skip()
            return

        field_val = str(self._get_str_value())
        start, end = self.GetWindow().GetSelection()
        field_val = field_val[:start] + chr(ukey) + field_val[end:]

        if not field_val or self.entry_pattern.match(field_val):
            # logging.debug("Field value %s accepted using %s", "field_val", self.entry_regex)
            event.Skip()
        else:
            logging.debug("Field value %s NOT accepted using %s", field_val, self.entry_regex)

    def Validate(self, win=None):
        """ This method is called when the 'Validate()' method is called on the
        parent of the TextCtrl to which this validator belongs. It can also
        be called as a standalone validation method.

        returns (boolean)
        """
        is_valid = self._is_valid_value(self._get_str_value())
        # logging.debug("Value '%s' is %s valid", self._get_str_value(), "" if is_valid else "not")
        return is_valid

    def get_validated_number(self, str_val):
        """ Return a validated number represented by the string value provided

        If choices is set, it will pick the closest matching value available.
        If min_val or max_val are set, it will always return a value within bounds.

        Args:
            str_val (string): a string representing a number

        Returns:
            (None or number of the right type): the most meaningful value that would fit the
            validator for the given string or None if the string is empty.

        """

        if not str_val:
            return None

        # Aggressively try to cast the string to a legal value by removing characters
        while len(str_val):
            try:
                num = self._cast(str_val)
                break
            except ValueError:
                pass
            str_val = str_val[:-1]

        if not str_val:
            return None

        # Find the closest value in choices
        if self.choices:
            num = min(self.choices, key=lambda x: abs(x - num))

        # bound the value by min/max
        msg = "Truncating out of range [{}, {}] value {}"
        if self.min_val is not None and num < self.min_val:
            logging.debug(msg.format(self.min_val, self.max_val, num))
            num = self.min_val
        if self.max_val is not None and num > self.max_val:
            logging.debug(msg.format(self.min_val, self.max_val, num))
            num = self.max_val

        return num

    def _cast(self, str_val):
        """ Cast the value string to the desired type

        Args:
            str_val (str): Value to cast

        Returns:
            number: Scaled and correctly typed number value

        """

        raise NotImplementedError


def _step_from_range(min_val, max_val):
    """ Dynamically create step size based on range """
    try:
        step = (max_val - min_val) * 1e-9
        # To keep the inc/dec values 'clean', set the step
        # value to the nearest power of 10
        step = 10 ** round(math.log10(step))
        return step
    except ValueError:
        msg = "Error calculating step size for range [%s..%s]" % (min_val, max_val)
        logging.exception(msg)


class PatternValidator(wx.Validator):

    def __init__(self, pattern):
        """ pattern (str): regex pattern of allowed entries """
        super().__init__()
        self.Bind(wx.EVT_CHAR, self.on_char)
        self._pattern = pattern
        self.pattern_compiled = re.compile(pattern)

    def _is_valid_value(self, val):
        """ Validate the given value

        Args:
            val (str):

        Returns:
            (boolean): True if the given string is valid

        """
        return bool(self.pattern_compiled.fullmatch(val))

    def on_char(self, event):
        """ This method prevents the entry of illegal characters """
        ukey = event.GetUnicodeKey()
        # Allow control keys to propagate (most of them are WXK_NONE in unicode)
        if ukey < wx.WXK_SPACE:
            event.Skip()
            return

        field_val = str(self.GetWindow().GetValue())
        start, end = self.GetWindow().GetSelection()
        field_val = field_val[:start] + chr(ukey) + field_val[end:]

        # Make sure the entire string matches the pattern (i.e. the match length equals the string length)
        if self._is_valid_value(field_val):
            # logging.debug("Field value %s accepted using %s", field_val, self.entry_pattern)
            event.Skip()
        else:
            logging.debug("Field value %s NOT accepted using %s", field_val, self._pattern)

    def Validate(self, win=None):
        """ This method is called when the 'Validate()' method is called on the
        parent of the TextCtrl to which this validator belongs. It can also
        be called as a standalone validation method.

        returns (boolean)
        """
        is_valid = self._is_valid_value(self.GetWindow().GetValue())
        # logging.debug("Value '%s' is %s valid", self._get_str_value(), "" if is_valid else "not")
        return is_valid

    def Clone(self):
        """ Required method """
        return PatternValidator(self._pattern)


class _NumberTextCtrl(wx.TextCtrl):
    """ A base text control specifically tailored to contain numerical data

    Use .GetValue() and .SetValue()/.ChangeValue() to get/set the raw value
    (number). SetValue and ChangeValue are identical but the first one generates
    an event as if the user had typed something in.
    To get the string that is displayed by the control, use .get_value_str() and .SetValueStr().

    Generates a wxEVT_COMMAND_ENTER whenever a new number is set by the user.
    This happens typically when loosing the focus or when pressing the [Enter] key.

    """

    _num_type = None  # type of the input widget

    def __init__(self, *args, **kwargs):
        """

        Args:
            validator (Validator): Validator that checks the value entered by the user
            key_step (number or None): By how much the value should be changed on key up/down.
                If specified, all key combinations are ignored and step size is fixed and change is linear.
                If None, the change in the values will be not linear.
            key_step_min (number or None): By how much the value should be changed when zero.
            accuracy (None or int): How many significant digits to keep when cleanly displayed. If
                None, it is never truncated.

        """

        # Make sure that a validator is provided
        if "validator" not in kwargs:
            raise ValueError("Validator required!")

        # The step size for when the up and down keys are pressed
        self.key_step = kwargs.pop('key_step', None)
        self.accuracy = kwargs.pop('accuracy', None)
        self.key_step_min = kwargs.pop('key_step_min', None)  # calculation based on min/max values

        # For the wx.EVT_TEXT_ENTER event to work, the TE_PROCESS_ENTER style needs to be set, but
        # setting it in XRC throws an error. A possible workaround is to include the style by hand
        kwargs['style'] = kwargs.get('style', 0) | wx.TE_PROCESS_ENTER | wx.BORDER_NONE

        if len(args) > 2:
            val = args[2]
            args = args[:2]
        else:
            val = kwargs.pop('value', None)

        # The
        self._number_value = val

        wx.TextCtrl.__init__(self, *args, **kwargs)

        self.SetBackgroundColour(self.Parent.BackgroundColour)
        self.SetForegroundColour(FG_COLOUR_EDIT)

        # Set the value so it will be validated to be a valid number
        if val is not None:
            self.SetValue(self._number_value)

        if self.key_step_min or self.key_step:
            self.Bind(wx.EVT_CHAR, self.on_char)

        self.Bind(wx.EVT_KILL_FOCUS, self.on_kill_focus)
        self.Bind(wx.EVT_SET_FOCUS, self.on_focus)
        self.Bind(wx.EVT_TEXT_ENTER, self.on_text_enter)

    def _display_pretty(self):
        if self._number_value is None:
            str_val = u""
        else:
            str_val = units.readable_str(self._number_value, sig=self.accuracy)
        wx.TextCtrl.ChangeValue(self, str_val)

    def Disable(self):
        self.Enable(False)

    def Enable(self, enable=True):
        # TODO: Find a better way to deal with this hack that was put in place because under
        # MS Windows the background colour cannot (at all?) be set when a control is disabled
        if os.name == 'nt':
            self.SetEditable(enable)

            if enable:
                self.SetForegroundColour(FG_COLOUR_EDIT)
            else:
                self.SetForegroundColour(FG_COLOUR_DIS)
        else:
            super(_NumberTextCtrl, self).Enable(enable)

    def SetValue(self, val):
        """ Set the numerical value of the text field

        Args:
            val (numerical type): The value to set the field to

        """
        self.ChangeValue(val)

    def GetValue(self):
        """ Return the numerical value of the text field or None if no (valid) value is present

        Warning: we return the last validated value, not the current value in the text field

        """
        return self._number_value

    def ChangeValue(self, val):
        """ Set the value of the text field

        No checks are done on the value to be correct. If this is needed, use the validator.

        Args:
            val (numerical type): The value to set the field to

        """

        self._number_value = val
        # logging.debug("Setting value to '%s' for %s", val, self.__class__.__name__)
        self._display_pretty()

    def get_value_str(self):
        """ Return the value of the control as a string """
        return wx.TextCtrl.GetValue(self)

    def set_value_str(self, val):
        wx.TextCtrl.SetValue(self, val)

    def change_value_str(self, val):
        """ Set the value of the field, without generating a change event """
        wx.TextCtrl.ChangeValue(self, val)

    def SetValueRange(self, minv, maxv):
        """ Same as SetRange of a slider """
        self.Validator.set_value_range(minv, maxv)

    def GetValueRange(self):
        return self.GetValidator().GetRange()

    def _set_number_value(self, str_number):
        """ Parse the given number string and set the internal number value

        This method is used when the enter key is pressed, or when the text field loses focus, i.e.
        situations where we always need to leave a valid and well formatted value.

        """

        prev_num = self._number_value
        if str_number is None or str_number == "":
            num = None
        else:
            # set new value even if not validated, so that we reach the boundaries
            num = self.GetValidator().get_validated_number(str_number)
            # TODO: turn the text red temporarily if not valid?
            # if not validated:
            # logging.debug("Converted '%s' into '%s'", str_number, self._number_value)

        if num is None:
            logging.debug("Skipping number field set to %r as it would be None, and reverting to %s", str_number, prev_num)
            return

        if prev_num != num:
            self._number_value = num
            self._send_change_event()

    # Event handlers

    def _send_change_event(self):
        """ Create and send a change event (wxEVT_COMMAND_ENTER) """
        changeEvent = wx.CommandEvent(wx.wxEVT_COMMAND_ENTER, self.Id)
        wx.PostEvent(self, changeEvent)

    def on_char(self, evt):
        """ This event handler increases or decreases the value when the following key combinations are pressed:
        * up/down cursor: increase/decrease by step size with one magnitude less than value
        * up/down cursor + Shift: increase/decrease by step size with two magnitudes less than value
        * up/down cursor + Ctrl: increase/decrease by step size with same magnitudes than value
        If "key_step" is specified, only this step size will be used, when up/down cursor is pressed.
        If an integer input value, the value will be increased/decreased by a value of at least one.
        If the value is zero, the min step size will be used.
        If no "key_step" or "key_step_min" are provided, this method is not bound to the input widget.
        The event is ignored otherwise.
        """

        key = evt.GetKeyCode()
        prev_num = self._number_value
        num = self._number_value

        if (key == wx.WXK_UP or key == wx.WXK_DOWN) and self.IsEditable():
            if evt.ShiftDown():
                k = 0.01  # decrease step size of one magnitude (two less then mag of value)
            elif evt.ControlDown():
                k = 1  # increase step size of one magnitude (same mag as value)
            else:
                k = 0.1  # default step size with one magnitude less than value

            num = (num or 0)

            if key == wx.WXK_UP:
                if self.key_step:
                    # Note: If step size (key step) explicitly specified, only arrows needed to change value
                    num += self.key_step
                else:
                    num += self.get_log_step(num, k)
            elif key == wx.WXK_DOWN:
                if self.key_step:
                    # Note: If step size (key step) explicitly specified, only arrows needed to change value
                    num -= self.key_step
                else:
                    num += self.get_log_step(num, -k)

        else:
            # Skip the event, so it can be processed in the regular way
            # (As in validate typed numbers etc.)
            evt.Skip()
            return

        val = u"%r" % num  # GetNumber needs a string
        self._number_value = self.GetValidator().get_validated_number(val)

        if prev_num != self._number_value:
            self._display_pretty()  # Update the GUI immediately
            self._send_change_event()

    def get_log_step(self, value, k):
        """
        Calculates the step size by which the current value should be increased/decreased.
        :parameter value: (float) Current value displayed in widget and that should be increased/decreased
        :parameter k: (float) Order by which the value should be increased/decreases.
            Is dependent on key combination pressed.
        :return: (float) Step by which the current value should be increased/decreased (already contains the sign).
        """
        if k < 0:  # arrow down was pressed -> decrease of value requested
            # Up/down keys are not just "opposite" (down = -up). They must compensate each other so that in
            # (almost) all cases pressing up then down (or down then up) returns to the original value.
            # If down was just "-up", this wouldn't work on the magnitude transitions. For example 9->10.
            # So, instead, we compute the "retraction" of the "up function" (which is injective).
            # In practice, this results in computing the magnitude of a "little bit" smaller value.
            value *= (1 + k / 10)
        try:
            magnitude = int(math.floor(math.log10(abs(value))))
            if magnitude <= -12:
                raise ValueError("Value is so small, so set it zero.")
        except ValueError:
            return math.copysign(self.key_step_min, k)
        step = (10 ** magnitude) * k
        if self.key_step_min and abs(step) < self.key_step_min:
            step = math.copysign(self.key_step_min, step)

        return step

    def on_focus(self, evt):
        """ Select the number part (minus any unit indication) of the data in the text field """
        number_length = len(self.get_value_str().rstrip(string.ascii_letters + u" µ"))
        wx.CallAfter(self.SetSelection, 0, number_length)
        evt.Skip()

    def on_kill_focus(self, evt):
        """ Display the current number value as a formatted string when the focus is lost """
        wx.CallAfter(self.SetSelection, 0, 0)
        str_val = wx.TextCtrl.GetValue(self)
        self._set_number_value(str_val)
        self._display_pretty()
        evt.Skip()

    def on_text_enter(self, evt):
        """ Process [enter] key presses """

        # almost the same as on_kill_focus, but still display raw
        wx.CallAfter(self.SetSelection, 0, 0)
        str_val = wx.TextCtrl.GetValue(self)
        logging.debug("New text entered in %s: %s", self.__class__.__name__, str_val)
        self._set_number_value(str_val)
        self._display_pretty()

    # END Event handlers


class UnitNumberCtrl(_NumberTextCtrl):

    def __init__(self, *args, **kwargs):
        """
        unit (None or string): if None then behave like NumberTextCtrl
        """
        self.unit = kwargs.pop('unit', None)
        _NumberTextCtrl.__init__(self, *args, **kwargs)

    def _display_pretty(self):
        if self._number_value is None:
            str_val = u""
        elif self._number_value == 0 and self.unit not in units.IGNORE_UNITS:
            # Special case with 0: readable_str return just "0 unit", without
            # prefix. This is technically correct, but quite inconvenient and
            # a little strange when the typical value has a prefix (eg, nm, kV).
            # => use prefix of key_step (as it's a "small value")
            if self.key_step:
                _, prefix = units.get_si_scale(self.key_step)
            elif self.key_step_min:
                _, prefix = units.get_si_scale(self.key_step_min)
            else:
                prefix = ""
            str_val = "0 %s%s" % (prefix, self.unit)
        else:
            str_val = units.readable_str(self._number_value, self.unit, self.accuracy)
        # Get the length of the number, without the unit (number and unit are separated by a space)
        number_length = str_val.find(" ")
        if number_length < 0:  # No space found -> only numbers
            number_length = len(str_val)
        wx.TextCtrl.ChangeValue(self, str_val)
        # Select the number value
        wx.CallAfter(self.SetSelection, number_length, number_length)


#########################################
# Integer controls
#########################################

class IntegerValidator(_NumberValidator):
    """ This validator can be used to make sure only valid characters are
    entered into a control (digits and a minus symbol).
    It can also validate if the value that is present is a valid integer.
    """

    def __init__(self, min_val=None, max_val=None, choices=None, unit=None):
        # The regular expression to check the validity of what is being typed, is a bit different
        # from a regular expression that would validate an entire string, because we need to check
        # validity as the user types
        self.entry_regex = r"[+{negative_sign}]?[\d]*{unit}$"
        _NumberValidator.__init__(self, min_val, max_val, choices, unit)

    def Clone(self):
        """ Required method """
        return IntegerValidator(self.min_val, self.max_val, self.choices, self.unit)

    def _cast(self, str_val):
        """ Cast the string value to an integer and return it

        Args:
            str_val (str): A string representing a number value

        Returns:
            (int)

        Raises:
            ValueError: When the string cannot be parsed correctly

        """
        if self.unit and str_val.endswith(self.unit):
            # Help it to find the right unit (important for complicated ones like 'px')
            str_val, si_prefix, unit = decompose_si_prefix(str_val, self.unit)
        else:
            str_val, si_prefix, unit = decompose_si_prefix(str_val)
        return int(si_scale_val(float(str_val), si_prefix))


class IntegerTextCtrl(_NumberTextCtrl):
    """ This class describes a text field that may only hold integer data.

    The 'min_val' and 'max_val' keyword arguments may be used to set limits on
    the value contained within the control.

    When the 'key_inc' argument is set, the value can be altered by the up and
    down cursor keys.

    The 'choices' keyword argument can be used to pass an iterable containing
    valid values

    If the object is created with an invalid integer value a ValueError
    exception will be raised.

    """

    _num_type = int  # type of input

    # TODO: should use the same parameter as NumberSlider: val_range instead
    # of min_val/max_val

    # TODO: refactor to have IntegerTextCtrl a UnitIntegerCtrl with unit=None?

    def __init__(self, *args, **kwargs):
        min_val = kwargs.pop('min_val', None)
        max_val = kwargs.pop('max_val', None)
        choices = kwargs.pop('choices', None)

        kwargs['validator'] = IntegerValidator(min_val, max_val, choices)
        if 'key_step' not in kwargs and 'key_step_min' not in kwargs and (min_val != max_val):
            kwargs['key_step_min'] = max(int(round(_step_from_range(min_val, max_val))), 1)

        _NumberTextCtrl.__init__(self, *args, **kwargs)

    def SetValue(self, val):
        _NumberTextCtrl.SetValue(self, int(val))


class UnitIntegerCtrl(UnitNumberCtrl):
    """ This class represents a text control which is capable of formatting
    it's content according to the unit it set to: '<int value> <unit str>'

    The value defaults to 0 if none is provided. The 'unit' argument is
    mandatory.

    When the value is set through the API, the units are shown.
    When the control gets the focus, the value is shown without the units
    When focus is lost, the units will be shown again.
    """

    _num_type = int  # type of input

    def __init__(self, *args, **kwargs):
        min_val = kwargs.pop('min_val', None)
        max_val = kwargs.pop('max_val', None)
        choices = kwargs.pop('choices', None)
        unit = kwargs.get('unit', None)

        kwargs['validator'] = IntegerValidator(min_val, max_val, choices, unit)

        if 'key_step' not in kwargs and 'key_step_min' not in kwargs and (min_val != max_val):
            kwargs['key_step_min'] = max(int(round(_step_from_range(min_val, max_val))), 1)

        UnitNumberCtrl.__init__(self, *args, **kwargs)

    def SetValue(self, val):
        UnitNumberCtrl.SetValue(self, int(val))


#########################################
# Float controls
#########################################

class FloatValidator(_NumberValidator):
    def __init__(self, min_val=None, max_val=None, choices=None, unit=None):
        # The regular expression to check the validity of what is being typed, is a bit different
        # from a regular expression that would validate an entire string, because we need to check
        # validity as the user types
        self.entry_regex = r"[+{negative_sign}]?[\d]*[.]?[\d]*[eE]?[+-]?[\d]*{unit}$"
        _NumberValidator.__init__(self, min_val, max_val, choices, unit)

    def Clone(self):
        """ Required method """
        return FloatValidator(self.min_val, self.max_val, self.choices, self.unit)

    def _cast(self, str_val):
        """ Cast the string value to a float and return it

        Args:
            str_val (str): A string representing a number value

        Returns:
            (float)

        Raises:
            ValueError: When the string cannot be parsed correctly

        """

        if self.unit and str_val.endswith(self.unit):
            # Help it to find the right unit (important for complicated ones like 'px')
            str_val, si_prefix, unit = decompose_si_prefix(str_val, self.unit)
        else:
            str_val, si_prefix, unit = decompose_si_prefix(str_val)
        return si_scale_val(float(str_val), si_prefix)


class FloatTextCtrl(_NumberTextCtrl):
    def __init__(self, *args, **kwargs):

        min_val = kwargs.pop('min_val', None)
        max_val = kwargs.pop('max_val', None)
        choices = kwargs.pop('choices', None)

        kwargs['validator'] = FloatValidator(min_val, max_val, choices)
        if 'key_step' not in kwargs and 'key_step_min' not in kwargs and (min_val != max_val):
            kwargs['key_step_min'] = _step_from_range(min_val, max_val)

        _NumberTextCtrl.__init__(self, *args, **kwargs)


class UnitFloatCtrl(UnitNumberCtrl):
    def __init__(self, *args, **kwargs):
        min_val = kwargs.pop('min_val', None)
        max_val = kwargs.pop('max_val', None)
        choices = kwargs.pop('choices', None)
        unit = kwargs.get('unit', None)

        kwargs['validator'] = FloatValidator(min_val, max_val, choices, unit)

        if 'key_step' not in kwargs and 'key_step_min' not in kwargs and (min_val != max_val):
            kwargs['key_step_min'] = _step_from_range(min_val, max_val)

        kwargs['accuracy'] = kwargs.get('accuracy', None)

        UnitNumberCtrl.__init__(self, *args, **kwargs)
