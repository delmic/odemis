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
from __future__ import division

import logging
import locale
import math
import sys

import wx
import wx.lib.mixins.listctrl as listmix

from odemis.util import units


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
        self._match_function = match_function
        self._screenheight = wx.SystemSettings.GetMetric(wx.SYS_SCREEN_Y)

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
        changeEvent = wx.CommandEvent(wx.wxEVT_COMMAND_ENTER, self.GetId())
        # Set the originating object for the event (ourselves)
        changeEvent.SetEventObject(self)

        # Watch for a possible listener of this event that will catch it and
        # eventually process it
        self.GetEventHandler().ProcessEvent(changeEvent)

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
        found = False

        choices = self._choices

        for numCh, choice in enumerate(choices):
            if self._match_function and self._match_function(text, choice):
                found = True
            elif choice.lower().startswith(text.lower()):
                found = True
            if found:
                self._showDropDown(True)
                item = self.dropdownlistbox.GetItem(numCh)
                toSel = item.GetId()
                self.dropdownlistbox.Select(toSel)
                break
        if not found:
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
        self._choices.sort(cmp=locale.strcoll)
        self._updateDataList(self._choices)
        self.dropdownlistbox.InsertColumn(0, "")
        for num, colVal in enumerate(self._choices):
            index = self.dropdownlistbox.InsertImageStringItem(sys.maxint,
                                                               colVal, -1)
            self.dropdownlistbox.SetStringItem(index, 0, colVal)
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
                          for x in xrange(dd.GetColumnCount())]
                self._select_callback(values)
            self.SetValue(itemtext)
            self.SetToolTip(wx.ToolTip(itemtext))
            self.SetInsertionPointEnd()
            self.SetSelection(-1, -1)
            self._showDropDown(False)
            self._send_change_event()

    def _showDropDown(self, show=True):
        """
        Either display the drop down list (show = True) or hide it (show = False).
        """
        if show:
            size = self.dropdown.GetSize()
            width, height = self . GetSizeTuple()
            x, y = self.ClientToScreenXY(0, height)
            if size.GetWidth() != width:
                size.SetWidth(width)
                self.dropdown.SetSize(size)
                self.dropdownlistbox.SetSize(self.dropdown.GetClientSize())
            if y + size.GetHeight() < self._screenheight:
                self.dropdown.SetPosition(wx.Point(x, y))
            else:
                self.dropdown.SetPosition(
                    wx.Point(x, y - height - size.GetHeight()))
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


class NumberValidator(wx.PyValidator):
    """ Base class used for number validation """

    def __init__(self, min_val=None, max_val=None, choices=None):
        """ Constructor """
        wx.PyValidator.__init__(self)
        self.Bind(wx.EVT_CHAR, self.OnChar)

        # String of legal characters
        self.legal = "0123456789"

        # this is a kludge because default value in XRC is 0:
        if min_val == 0 and max_val == 0:
            min_val = None
            max_val = None

        # Minimum and maximum allowed values
        self.min_val = min_val
        self.max_val = max_val
        self.choices = choices

        if min_val is not None and max_val is not None:
            if min_val > max_val:
                raise ValueError("Min value is bigger than max value: %r > %r" % (min_val, max_val))
        self._validate_choices()

        # Are negative values allowed?
        if ((min_val is None or min_val < 0) or
           (max_val is not None and max_val < 0) or
           (choices and min(choices) < 0)):
            self.legal += "-"

    def SetRange(self, minv, maxv):
        # TODO: check values and recompute .legal as in init
        self.min_val = minv
        self.max_val = maxv

    def GetRange(self):
        return self.min_val, self.max_val

    def _validate_choices(self):

        if self.choices:
            for c in self.choices:
                valid = self.validate_value(c)

                if not valid:
                    raise ValueError("Illegal value (%s) found in choices" % c)

    def _get_fld_text(self):
        """
        Returns the text contained in the window validated
        """
        # Special trick in (the very likely) case we are validating a NumberTextCtrl
        fld = self.GetWindow()
        if hasattr(fld, "GetValueStr"):
            val = fld.GetValueStr()
        else:
            val = fld.GetValue()
        return val

    def Clone(self): #pylint: disable=W0221
        raise NotImplementedError

    def OnChar(self, event):
        """ This method prevents the entry of illegal characters """
        key = event.GetKeyCode()

        # Allow control keys to propagate
        if key < wx.WXK_SPACE or key == wx.WXK_DELETE or key > 255:
            event.Skip()
            return

        # TODO: just check if the new value would conform to a regex
        # Allow legal characters to reach the text control
        if chr(key) in self.legal:
            logging.debug("Processing key '%s'", key)
            val = self._get_fld_text()

            if val is None:
                event.Skip()
                return
            else:
                val = unicode(val)

            start, end = self.GetWindow().GetSelection()
            val = val[:start] + chr(key) + val[end:]

            if chr(key) in ('.', ','):
                val = val.replace('..', '.')
                val = val.replace(',,', ',')

            logging.debug("Checking against %s", val)

            try:
                # if starting to write negative number or exponent => it's fine
                if val != "-" and not (val.endswith("e") or val.endswith("e-")):
                    val = self.cast(val)
                logging.debug("Key accepted")
                event.Skip()
            except ValueError:
                logging.debug("Key rejected")
                return

        # 'Eat' the event by not Skipping it, thus preventing it.
        # from reaching the text control
        return

    def Validate(self, win=None):
        """ This method is called when the 'Validate()' method is called on the
        parent of the TextCtrl to which this validator belongs. It can also
        be called as a standalone validation method.

        returns (boolean)
        """
        val = self._get_fld_text()
        validated = self.validate_value(val)
        logging.debug("Value {} is {} valid".format(val, "" if validated else "not"))
        return validated

    def validate_value(self, val):
        """ Validate the given value
        val (string)
        returns (boolean): True if the given string is valid
        """
        if val is False or val is None:
            return False

        try:
            num = self.cast(val)
        except ValueError:
            return False

        if self.choices and not num in self.choices:
            return False
        if self.min_val and num < self.min_val:
            return False
        if self.max_val and num > self.max_val:
            return False

        return True

    def GetNumber(self, val):
        """
        Return a number corresponding to the (string) value provided
        val (string): a string representing a number
        returns (None or number of the right type): the most meaningful value
          that would fit the validator for the given string, or None if the string
          is empty.
          If choices is set, it will pick the closest choice available
          If min_val or max_val are set, it will always return a value within bound
        """
        if not val:
            return None

        # remove illegal characters
        val = "".join([c for c in val if c in self.legal])

        # try hard to cast it to a legal value by removing anything meaningless at the end
        while len(val) > 0:
            try:
                num = self.cast(val)
                break
            except ValueError:
                pass
            val = val[:len(val)-1]

        if len(val) == 0:
            return None

        # find the closest value in choices
        if self.choices:
            num = min(self.choices, key=lambda x: abs(x - num))

        # bound the value by min/max
        msg = "Value {} out of range [{}, {}]"
        if self.min_val is not None and num < self.min_val:
            logging.debug(msg.format(num, self.min_val, self.max_val))
            num = self.min_val
        if self.max_val is not None and num > self.max_val:
            logging.debug(msg.format(num, self.min_val, self.max_val))
            num = self.max_val

        return num

    def cast(self, val):
        """ Try to cast the value string to the desired type """
        # To be overridden
        raise NotImplementedError


def _step_from_range(min_val, max_val):
    """ Dynamically create step size based on range """
    try:
        step = (max_val - min_val) / 255
        # To keep the inc/dec values 'clean', set the step
        # value to the nearest power of 10
        step = 10 ** round(math.log10(step))
        return step
    except ValueError:
        msg = "Error calculating step size for range [%s..%s]" % (min_val, max_val)
        logging.exception(msg)


class NumberTextCtrl(wx.TextCtrl):
    """ A base text control specifically tailored to contain numerical data
    The main behaviour is that when it has the focus, it just displays the number
    as raw as possible (in the standard unit), and if it's out of focus, it displays
    a beautiful value (not too digits, with unit and unit multiplicator if needed).

    Use .GetValue() and .SetValue()/.ChangeValue() to get/set the raw value
    (number). SetValue and ChangeValue are identical but the first one generates
    an event as if the user had typed something in.
    To get the actual string displayed, use .GetValueStr() and .SetValueStr(),
    but in general this shouldn't be needed.

    Generates a wxEVT_COMMAND_ENTER whenever a new number is set by the user.
    This happens typically when loosing the focus or when pressing "Enter" key.

    """

    def __init__(self, *args, **kwargs):
        """
        validator (Validator): instance that checks the value entered by the user
        key_inc (boolean): whether up/down should change the value
        step (number): by how much the value should be changed on key up/down
        accuracy (None or int): how many significant digits to keep when cleanly
          displayed. If None, it is never truncated.
        """
        # Make sure that a validator is provided
        try:
            self._validator = kwargs["validator"]
        except AttributeError:
            raise ValueError("No validator set!")

        key_inc = kwargs.pop('key_inc', True)
        self.step = kwargs.pop('step', 0)
        self.accuracy = kwargs.pop('accuracy', None)

        # For the wx.EVT_TEXT_ENTER event to work, the TE_PROCESS_ENTER
        # style needs to be set, but setting it in XRC throws an error
        # A possible workaround is to include the style by hand
        kwargs['style'] = kwargs.get('style', 0) | wx.TE_PROCESS_ENTER

        if len(args) > 2:
            val = args[2]
            args = args[:2]
        else:
            val = kwargs.pop('value', None)

        # the raw value: a number or None
        self.number = val

        wx.TextCtrl.__init__(self, *args, **kwargs)

        # Set the value so it will be validated to be a valid number
        if val is not None:
            self.SetValue(self.number)

        if key_inc:
            self.Bind(wx.EVT_CHAR, self.on_char)

        self.Bind(wx.EVT_KILL_FOCUS, self.on_kill_focus)
        self.Bind(wx.EVT_SET_FOCUS, self.on_focus)
        self.Bind(wx.EVT_TEXT_ENTER, self.on_text_enter)

    def _display_raw(self):
        """ Set the current text to raw style (no truncation/no unit) """

        if self.number is None:
            str_val = u""
        else:
            if hasattr(self, 'unit'):
                unit = self.unit  #pylint: disable=E1101
            else:
                unit = None
            if self.accuracy is None:
                accuracy = None
            else:
                accuracy = self.accuracy + 1
            str_val = units.to_string_pretty(self.number, accuracy, unit)
        wx.TextCtrl.ChangeValue(self, str_val)

    def _display_pretty(self):
        if self.number is None:
            str_val = u""
        else:
            str_val = units.readable_str(self.number, sig=self.accuracy)
        wx.TextCtrl.ChangeValue(self, str_val)

    def GetValue(self):
        """ Return the value as an integer, or None if no (valid) value is
        present.
        """
        # Warning: we return the last value accepted, not the current value in
        # the text field
        return self.number

    def SetValue(self, val):
        self.ChangeValue(val)
        # TODO: call _send_change_event() ? => in this case we need to change
        # all Odemis to use ChangeValue instead of SetValue()

    def ChangeValue(self, val):
        """ Set the value of the control

        No checks are done on the value to be correct. If this is needed, use the validator.

        """

        self.number = val
        # logging.debug(
        #         "Setting value to '%s' for %s",
        #         val, self.__class__.__name__)

        if self.HasFocus():
            logging.info("Received the new value '%s' to set while in focus", val)
            self._display_raw()
        else:
            self._display_pretty()

    def GetValueStr(self):
        """ Return the value of the control as a string """
        return wx.TextCtrl.GetValue(self)

    def SetValueStr(self, val):
        wx.TextCtrl.SetValue(self, val)

    def ChangeValueStr(self, val):
        wx.TextCtrl.ChangeValue(self, val)

    def SetValueRange(self, minv, maxv):
        """ Same as SetRange of a slider """
        self.GetValidator().SetRange(minv, maxv)

    def GetValueRange(self):
        return self.GetValidator().GetRange()

    def _processNewText(self, str_val):
        """ Called internally when a new text is entered by the user
        It processes the new text and set the number
        """
        prev_num = self.number
        if str_val is None or str_val == "":
            self.number = None
        else:
            # set new value even if not validated, so that we reach the boundaries
            self.number = self.GetValidator().GetNumber(str_val)
            # TODO: turn the text red temporarily if not valid?
            # if not validated:
            # logging.debug("Value '%s' not valid, using '%s'", str_val, val)

        if prev_num != self.number:
            self._send_change_event()

    # Event handlers

    def _send_change_event(self):
        changeEvent = wx.CommandEvent(wx.wxEVT_COMMAND_ENTER, self.GetId())
        # Set the originating object for the event (ourselves)
        changeEvent.SetEventObject(self)

        # Watch for a possible listener of this event that will catch it and
        # eventually process it
        self.GetEventHandler().ProcessEvent(changeEvent)

    def on_text_enter(self, evt):
        logging.debug("New text entered in %s", self.__class__.__name__)
        # almost the same as on_kill_focus, but still display raw
        wx.CallAfter(self.SetSelection, 0, 0)
        str_val = wx.TextCtrl.GetValue(self)
        self._processNewText(str_val)
        self._display_raw() # display the new value as understood

    def on_char(self, evt):
        """ This event handler increases or decreases the integer value when
        the up/down cursor keys are pressed.

        The event is ignored otherwise.
        """

        key = evt.GetKeyCode()
        prev_num = self.number
        num = self.number

        if key == wx.WXK_UP and self.step:
            num = (num or 0) + self.step
        elif key == wx.WXK_DOWN and self.step:
            num = (num or 0) - self.step
        else:
            # Skip the event, so it can be processed in the regular way
            # (As in validate typed numbers etc.)
            evt.Skip()
            return

        val = u"%r" % num # GetNumber needs a string
        self.number = self.GetValidator().GetNumber(val)
        # if not validated:
        #     logging.debug("Reached invalid value %s", val)

        if prev_num != self.number:
            self._display_raw() # we assume we have the focus
            self._send_change_event()

    def on_focus(self, evt):
        """ Remove the units from the displayed value on focus """
        self._display_raw()
        wx.CallAfter(self.SetSelection, -1, -1)

    def on_kill_focus(self, evt):
        """ Display the current value with the units added when focus is
        lost .
        """
        wx.CallAfter(self.SetSelection, 0, 0)
        str_val = wx.TextCtrl.GetValue(self)
        self._processNewText(str_val)
        self._display_pretty()
        # SKip the EVT_KILL_FOCUS event when the value is set
        evt.Skip()

    # END Event handlers


class UnitNumberCtrl(NumberTextCtrl):

    def __init__(self, *args, **kwargs):
        """
        unit (None or string): if None then behave like NumberTextCtrl
        """
        self.unit = kwargs.pop('unit', None)
        NumberTextCtrl.__init__(self, *args, **kwargs)

    def _display_pretty(self):
        if self.number is None:
            str_val = u""
        else:
            str_val = units.readable_str(self.number, self.unit, self.accuracy)
        wx.TextCtrl.ChangeValue(self, str_val)


#########################################
# Integer controls
#########################################

class IntegerValidator(NumberValidator):
    """ This validator can be used to make sure only valid characters are
    entered into a control (digits and a minus symbol).
    It can also validate if the value that is present is a valid integer.
    """

    def __init__(self, min_val=None, max_val=None, choices=None):
        """ Constructor """
        NumberValidator.__init__(self, min_val, max_val, choices)


    def Clone(self):    #pylint: disable=W0221
        """ Required method """
        return IntegerValidator(self.min_val, self.max_val, self.choices)

    def cast(self, val):
        if isinstance(val, (str, unicode)):
            val = float(val)
        return int(val)


class IntegerTextCtrl(NumberTextCtrl):
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

    # TODO: should use the same parameter as NumberSlider: val_range instead
    # of min_val/max_val

    # TODO: refactor to have IntegerTextCtrl a UnitIntegerCtrl with unit=None?

    def __init__(self, *args, **kwargs):
        min_val = kwargs.pop('min_val', None)
        max_val = kwargs.pop('max_val', None)
        choices = kwargs.pop('choices', None)

        kwargs['validator'] = IntegerValidator(min_val, max_val, choices)
        kwargs['step'] = kwargs.get('step', 1)

        NumberTextCtrl.__init__(self, *args, **kwargs)

    def SetValue(self, val): #pylint: disable=W0221
        NumberTextCtrl.SetValue(self, int(val))


class UnitIntegerCtrl(UnitNumberCtrl):
    """ This class represents a text control which is capable of formatting
    it's content according to the unit it set to: '<int value> <unit str>'

    The value defaults to 0 if none is provided. The 'unit' argument is
    mandatory.

    When the value is set through the API, the units are shown.
    When the control gets the focus, the value is shown without the units
    When focus is lost, the units will be shown again.
    """

    def __init__(self, *args, **kwargs):
        min_val = kwargs.pop('min_val', None)
        max_val = kwargs.pop('max_val', None)
        choices = kwargs.pop('choices', None)
        kwargs['validator'] = IntegerValidator(min_val, max_val, choices)

        if 'step' not in kwargs and (min_val != max_val):
            kwargs['step'] = max(int(round(_step_from_range(min_val, max_val))), 1)

        UnitNumberCtrl.__init__(self, *args, **kwargs)

    def SetValue(self, val):
        UnitNumberCtrl.SetValue(self, int(val))


#########################################
# Float controls
#########################################

class FloatValidator(NumberValidator):
    def __init__(self, min_val=None, max_val=None, choices=None):
        """ Constructor """
        NumberValidator.__init__(self, min_val, max_val, choices)
        # More legal characters for floats
        self.legal += ".e-" # - is for the exponent (e.g., 1e-6)

    def Clone(self):
        """ Required method """
        return FloatValidator(self.min_val, self.max_val, self.choices)

    def cast(self, val):
        return float(val)


class FloatTextCtrl(NumberTextCtrl):
    def __init__(self, *args, **kwargs):
        min_val = kwargs.pop('min_val', None)
        max_val = kwargs.pop('max_val', None)
        choices = kwargs.pop('choices', None)

        kwargs['validator'] = FloatValidator(min_val, max_val, choices)
        kwargs['step'] = kwargs.get('step', 0.1)
        kwargs['accuracy'] = kwargs.get('accuracy', 3) # decimal places

        NumberTextCtrl.__init__(self, *args, **kwargs)


class UnitFloatCtrl(UnitNumberCtrl):
    def __init__(self, *args, **kwargs):
        min_val = kwargs.pop('min_val', None)
        max_val = kwargs.pop('max_val', None)
        choices = kwargs.pop('choices', None)

        kwargs['validator'] = FloatValidator(min_val, max_val, choices)

        if 'step' not in kwargs and (min_val != max_val):
            kwargs['step'] = _step_from_range(min_val, max_val)

        kwargs['accuracy'] = kwargs.get('accuracy', 3) # decimal places

        UnitNumberCtrl.__init__(self, *args, **kwargs)
