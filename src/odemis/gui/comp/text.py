# -*- coding: utf-8 -*-

""" This module contains classes describing various customized text fields used
throughout Odemis.

@author: Rinze de Laat

Copyright © 2012 Rinze de Laat, Delmic

This file is part of Open Delmic Microscope Software.

Delmic Acquisition Software is free software: you can redistribute it and/or
modify it under the terms of the GNU General Public License as published by the
Free Software Foundation, either version 2 of the License, or (at your option)
any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
details.

You should have received a copy of the GNU General Public License along with
Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.

"""

import locale
import sys

import wx
import wx.lib.mixins.listctrl as listmix

from odemis.gui.log import log

# Locale is needed for correct string sorting
locale.setlocale(locale.LC_ALL, "")


# The SuggestTextCtrl and ChoiceListCtrl class are addaptations of the
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
    def __init__(self, parent, ID= -1, pos=wx.DefaultPosition,
                 size=wx.DefaultSize, style=0):
        wx.ListCtrl.__init__(self, parent, ID, pos, size, style)
        listmix.ListCtrlAutoWidthMixin.__init__(self)

class SuggestTextCtrl (wx.TextCtrl, listmix.ColumnSorterMixin):
    def __init__ (self, parent, colNames=None, choices=None,
                  showHead=True, dropDownClick=True,
                  colFetch= -1, colSearch=0, hideOnNoMatch=True,
                  selectCallback=None, entryCallback=None, matchFunction=None,
                  **therest) :
        """
        Constructor works just like wx.TextCtrl except you can pass in a
        list of choices.  You can also change the choice list at any time
        by calling setChoices.
        """
        if 'style' in therest:
            therest['style'] = wx.TE_PROCESS_ENTER | \
                               wx.BORDER_NONE | \
                               therest['style']
        else:
            therest['style'] = wx.TE_PROCESS_ENTER | wx.BORDER_NONE
        wx.TextCtrl.__init__(self, parent, **therest)

        #Some variables
        self._dropDownClick = dropDownClick
        self._choices = choices
        self._lastinsertionpoint = 0
        self._hideOnNoMatch = hideOnNoMatch
        self._selectCallback = selectCallback
        self._entryCallback = entryCallback
        self._matchFunction = matchFunction
        self._screenheight = wx.SystemSettings.GetMetric(wx.SYS_SCREEN_Y)

        #sort variable needed by listmix
        self.itemDataMap = dict()

        #Load and sort data
        if not self._choices:
            self._choices = []
            #raise ValueError, "Pass me at least one of multiChoices OR choices"

        #widgets
        self.dropdown = wx.PopupWindow(self)

        #Control the style
        flags = wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_SORT_ASCENDING
        flags = flags | wx.LC_NO_HEADER


        #Create the list and bind the events
        self.dropdownlistbox = ChoiceListCtrl(self.dropdown, style=flags,
                                 pos=wx.Point(0, 0))

        ln = 1
        #else: ln = len(choices)
        listmix.ColumnSorterMixin.__init__(self, ln)
        #load the data
        #self.SetChoices(choices)

        gp = self

        while gp != None :
            gp.Bind (wx.EVT_MOVE , self.onControlChanged, gp)
            gp.Bind (wx.EVT_SIZE , self.onControlChanged, gp)
            gp = gp.GetParent()

        self.Bind(wx.EVT_KILL_FOCUS, self.onControlChanged, self)
        self.Bind(wx.EVT_TEXT , self.onEnteredText, self)
        self.Bind(wx.EVT_KEY_DOWN , self.onKeyDown, self)

        #If need drop down on left click
        if dropDownClick:
            self.Bind (wx.EVT_LEFT_DOWN , self.onClickToggleDown, self)
            self.Bind (wx.EVT_LEFT_UP , self.onClickToggleUp, self)

        self.dropdown.Bind(wx.EVT_LISTBOX , self.onListItemSelected, self.dropdownlistbox)
        self.dropdownlistbox.Bind(wx.EVT_LEFT_DOWN, self.onListClick)
        self.dropdownlistbox.Bind(wx.EVT_LEFT_DCLICK, self.onListDClick)
        self.dropdownlistbox.Bind(wx.EVT_LIST_COL_CLICK, self.onListColClick)
        self.il = wx.ImageList(16, 16)
        self.dropdownlistbox.SetImageList(self.il, wx.IMAGE_LIST_SMALL)
        self._ascending = True

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
        if col == self._colSearch:
            self._ascending = not self._ascending
        self.SortListItems(evt.GetColumn(), ascending=self._ascending)
        self._colSearch = evt.GetColumn()
        evt.Skip()

    def onEnteredText(self, event):
        text = event.GetString()
        if self._entryCallback:
            self._entryCallback()
        if not text:
            # control is empty; hide dropdown if shown:
            if self.dropdown.IsShown():
                self._showDropDown(False)
            event.Skip()
            return
        found = False

        choices = self._choices

        for numCh, choice in enumerate(choices):
            if self._matchFunction and self._matchFunction(text, choice):
                found = True
            elif choice.lower().startswith(text.lower()) :
                found = True
            if found:
                self._showDropDown(True)
                item = self.dropdownlistbox.GetItem(numCh)
                toSel = item.GetId()
                self.dropdownlistbox.Select(toSel)
                break
        if not found:
            self.dropdownlistbox.Select(self.dropdownlistbox.GetFirstSelected(), False)
            if self._hideOnNoMatch:
                self._showDropDown(False)
        self._listItemVisible()
        event.Skip ()

    def onKeyDown (self, event) :
        """ Do some work when the user press on the keys:
            up and down: move the cursor
            left and right: move the search
        """
        skip = True
        sel = self.dropdownlistbox.GetFirstSelected()
        visible = self.dropdown.IsShown()
        KC = event.GetKeyCode()
        if KC == wx.WXK_DOWN :
            if sel < self.dropdownlistbox.GetItemCount () - 1:
                self.dropdownlistbox.Select (sel + 1)
                self._listItemVisible()
            self._showDropDown ()
            skip = False
        elif KC == wx.WXK_UP :
            if sel > 0 :
                self.dropdownlistbox.Select (sel - 1)
                self._listItemVisible()
            self._showDropDown ()
            skip = False
        elif KC == wx.WXK_LEFT :
            return
        elif KC == wx.WXK_RIGHT:
            return
        if visible :
            if event.GetKeyCode() == wx.WXK_RETURN :
                self._setValueFromSelected()
                skip = False
            if event.GetKeyCode() == wx.WXK_ESCAPE :
                self._showDropDown(False)
                skip = False
        if skip :
            event.Skip()

    def onListItemSelected (self, event):
        self._setValueFromSelected()
        event.Skip()

    def onClickToggleDown(self, event):
        self._lastinsertionpoint = self.GetInsertionPoint()
        event.Skip ()

    def onClickToggleUp (self, event) :
        if self.GetInsertionPoint() == self._lastinsertionpoint :
            self._showDropDown (not self.dropdown.IsShown())
        event.Skip ()

    def onControlChanged(self, event):
        try:
            if self.IsShown():
                self._showDropDown(False)
        except wx.PyDeadObjectError:
            pass
        event.Skip()

    def SetChoices(self, choices):
        """
        Sets the choices available in the popup wx.ListBox.
        The items will be sorted case insensitively.
        """
        self._choices = choices
        flags = wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_SORT_ASCENDING | wx.LC_NO_HEADER
        self.dropdownlistbox.SetWindowStyleFlag(flags)
        if not isinstance(choices, list):
            self._choices = list(choices)
        #prevent errors on "old" systems
        if sys.version.startswith("2.3"):
            self._choices.sort(lambda x, y: cmp(x.lower(), y.lower()))
        else:
            self._choices.sort(cmp=locale.strcoll)
        self._updateDataList(self._choices)
        self.dropdownlistbox.InsertColumn(0, "")
        for num, colVal in enumerate(self._choices):
            index = self.dropdownlistbox.InsertImageStringItem(sys.maxint, colVal, -1)
            self.dropdownlistbox.SetStringItem(index, 0, colVal)
            self.dropdownlistbox.SetItemData(index, num)
        self._setListSize()
        # there is only one choice for both search and fetch if setting a single column:
        self._colSearch = 0
        self._colFetch = -1

    def GetChoices(self):
        return self._choices

    def SetSelectCallback(self, cb=None):
        self._selectCallback = cb

    def SetEntryCallback(self, cb=None):
        self._entryCallback = cb

    def SetMatchFunction(self, mf=None):
        self._matchFunction = mf

    #-- Internal methods
    def _setValueFromSelected(self) :
        """ Sets the wx.TextCtrl value from the selected wx.ListCtrl item.
        Will do nothing if no item is selected in the wx.ListCtrl.
        """
        sel = self.dropdownlistbox.GetFirstSelected()
        if sel > -1:
            if self._colFetch != -1:
                col = self._colFetch
            else:
                col = self._colSearch
            itemtext = self.dropdownlistbox.GetItem(sel, col).GetText()
            if self._selectCallback:
                dd = self.dropdownlistbox
                values = [dd.GetItem(sel, x).GetText()
                          for x in xrange(dd.GetColumnCount())]
                self._selectCallback(values)
            self.SetValue(itemtext)
            self.SetInsertionPointEnd ()
            self.SetSelection (-1, -1)
            self._showDropDown (False)

    def _showDropDown (self, show=True) :
        """
        Either display the drop down list (show = True) or hide it (show = False).
        """
        if show :
            size = self.dropdown.GetSize()
            width, height = self . GetSizeTuple()
            x, y = self . ClientToScreenXY (0, height)
            if size.GetWidth() != width :
                size.SetWidth(width)
                self.dropdown.SetSize(size)
                self.dropdownlistbox.SetSize(self.dropdown.GetClientSize())
            if y + size.GetHeight() < self._screenheight :
                self.dropdown . SetPosition (wx.Point(x, y))
            else:
                self.dropdown . SetPosition (wx.Point(x, y - height - size.GetHeight()))
        self.dropdown.Show(show)

    def _listItemVisible(self) :
        """
        Moves the selected item to the top of the list ensuring it is always visible.
        """
        toSel = self.dropdownlistbox.GetFirstSelected ()
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
        for choice in choices :
            longest = max(len(choice), longest)
        longest += 3
        itemcount = min(len(choices) , 7) + 2
        charheight = self.dropdownlistbox.GetCharHeight()
        charwidth = self.dropdownlistbox.GetCharWidth()
        self.popupsize = wx.Size(charwidth * longest, charheight * itemcount)
        self.dropdownlistbox.SetSize (self.popupsize)
        self.dropdown.SetClientSize(self.popupsize)


class IntegerValidator(wx.PyValidator):
    """ This validator can be used to make sure only valid characters are
    entered into a control (digits and a minus symbol).
    It can also validate if the value that is present is a valid integer.
    """

    def __init__(self, min_val=None, max_val=None):
        """ Constructor """
        wx.PyValidator.__init__(self)
        self.Bind(wx.EVT_CHAR, self.OnChar)

        # All legal characters
        self.legal = "-0123456789"

        self.min_val = min_val
        self.max_val = max_val

    def Clone(self):    #pylint: disable=W0221
        """ Required method """
        return IntegerValidator(self.min_val, self.max_val)

    def Validate(self, win=None):#pylint: disable=W0221,W0613
        """ This method is called when the 'Validate()' method is called on the
        parent of the TextCtrl to which this validator belongs. It can also
        be called as a stan-alone validation method.

        """

        fld = self.GetWindow()
        val = fld.GetValue()

        validated, _ = self.validate_value(val)

        log.debug("Value {} is {}valid".format(val, "" if validated else "not"))

        return validated

    def validate_value(self, val):
        """ Validate the given value

        This method returns a 2-tuple of which the first element is a boolean
        indication if the validation succeeded (True) and the second element
        is equal to the 'val' argument, or to the min/max value if the value
        exceeded its bounds.

        """

        msg = "Value {} out of range [{}, {}]"

        if val is not None and val != "-":
            if self.min_val is not None and val < self.min_val:
                log.debug(msg.format(val, self.min_val, self.max_val))
                return False, self.min_val
            if self.max_val is not None and val > self.max_val:
                log.debug(msg.format(val, self.min_val, self.max_val))
                return False, self.max_val
        return True, val

    def OnChar(self, event):
        """ This method prevents the entry of illegal characters """
        key = event.GetKeyCode()

        # Allow control keys to propagate
        if key < wx.WXK_SPACE or key == wx.WXK_DELETE or key > 255:
            event.Skip()
            return

        # Allow legal characters to reach the text control
        if chr(key) in self.legal:
            log.debug("Processing key '%s'", key)

            fld = self.GetWindow()
            val = fld.GetValue()

            if val is None:
                event.Skip()
                return
            else:
                val = unicode(val)

            #pos = self.GetWindow().GetInsertionPoint()
            start, end = fld.GetSelection()
            val = val[:start] + chr(key) + val[end:]

            log.debug("Checking against %s", val)

            try:
                if val != "-":
                    val = int(val)
                log.debug("Key accepted")
                event.Skip()
            except ValueError:
                log.debug("Key rejected")
                return

        # 'Eat' the event by not Skipping it, thus preventing it.
        # from reaching the text control
        return

class IntegerTextCtrl(wx.TextCtrl):
    """ This class describes a text field that may only hold integer data.

    The 'min_val' and 'max_val' keyword arguments may be used to set limits on
    the value contained within the control.

    When the 'key_inc' argument is set, the value can be altered by the up and
    down cursor keys.

    If the object is created with an invalid integer value a ValueError
    exception will be raised.

    """
    def __init__(self, *args, **kwargs):

        min_val = kwargs.pop('min_val', None)
        max_val = kwargs.pop('max_val', None)
        key_inc = kwargs.pop('key_inc', True)

        # For the wx.EVT_TEXT_ENTER event to work, the TE_PROCESS_ENTER
        # style needs to be set, but setting it in XRC throws an error
        # A possible workaround is to include the style by hand
        kwargs['style'] = kwargs.get('style', 0) | wx.TE_PROCESS_ENTER
        kwargs['validator'] = IntegerValidator(min_val, max_val)

        wx.TextCtrl.__init__(self, *args, **kwargs)

        val = args[2] if len(args) > 2 else kwargs.get('value', None)

        # Set the value so it will be validated to be a valid integer
        if val:
            self.SetValue(val)

        if key_inc:
            self.Bind(wx.EVT_CHAR, self.on_char)

        self.Bind(wx.EVT_KILL_FOCUS, self.on_kill_focus)
        self.Bind(wx.EVT_SET_FOCUS, self.on_focus)

        self.Bind(wx.EVT_TEXT_ENTER, self.on_text_enter)

    def GetValue(self): #pylint: disable=W0221
        """ Return the value as an integer, or None if no (valid) value is
        present.
        """
        val = wx.TextCtrl.GetValue(self)

        return self._check_value(val)


    def _check_value(self, val):
        try:
            return int(val)
        except ValueError:
            if val is None or len(val) == 0:
                return None
            else:
                log.error("Illegal %s value %s",
                              self.__class__.__name__, val)
                wx.CallAfter(self.SetFocus)
                return None
        return None

    def GetValueStr(self):
        """ Return the value of the control as a string """
        return wx.TextCtrl.GetValue(self)

    def SetValue(self, val): #pylint: disable=W0221
        """ Set the value of the control or raise and exception when the value
        is not a valid integer.
        """
        try:
            log.debug("Setting value to '%s' for %s",
                      val, self.__class__.__name__)
            if val:
                val = int(val)
            wx.TextCtrl.SetValue(self, unicode(val))
        except ValueError:
            raise ValueError("Value '%s' is not a valid integer." % val)

    SetValueStr = SetValue

    def reset(self):
        """ Set the content of the text control to just the numerical value """
        self.SetValue(unicode(self.GetValue() or ""))

    def on_text_enter(self, evt):
        val = self.GetValue()
        wx.CallAfter(self.SetSelection, 0, 0)
        if val:
            validated, new_val = self.GetValidator().validate_value(val)
            if validated:
                self.SetValue(val)
            else:
                self.SetValue(new_val)

        evt.Skip()

    def on_char(self, evt):
        """ This event handler increases or decreases the integer value when
        the up/down cursor keys are pressed.

        The event is ignored otherwise.
        """
        key = evt.GetKeyCode()
        val = self.GetValue()

        if key == wx.WXK_UP:
            val += 1
        elif key == wx.WXK_DOWN:
            val -= 1
        else:
            evt.Skip()
            return

        validated, val = self.GetValidator().validate_value(val)
        if validated:
            self.SetValue(val)

    def on_focus(self, evt):
        """ Remove the units from the displayed value on focus """
        self.reset()
        wx.CallAfter(self.SetSelection, -1, -1)

    def on_kill_focus(self, evt):
        """ Display the current value with the units added when focus is
        lost .
        """

        val = self.GetValue()
        wx.CallAfter(self.SetSelection, 0, 0)

        if val:
            validated, new_val = self.GetValidator().validate_value(val)
            if validated:
                self.SetValueStr(val)
            else:
                self.SetValueStr(new_val)


class UnitIntegerCtrl(IntegerTextCtrl):
    """ This class represents a text control which is capable of formatting
    it's content according to the unit it set to: '<int value> <unit str>'

    The value defaults to 0 if none is provided. The 'unit' argument is
    manditory.

    When the value is set through the API, the units are shown.
    When the control gets the focus, the value is shown without the units
    When focus is lost, the units will be shown again.
    """

    def __init__(self, *args, **kwargs):

        self.unit = kwargs.pop('unit', "")

        IntegerTextCtrl.__init__(self, *args, **kwargs)

        val = args[2] if len(args) > 2 else kwargs.get('value', None)
        if val:
            self.SetValueStr(val)

    # def on_focus(self, evt):
    #     """ Remove the units from the displayed value on focus """
    #     self.reset()
    #     wx.CallAfter(self.SetSelection, -1, -1)

    # def on_kill_focus(self, evt):
    #     """ Display the current value with the units added when focus is
    #     lost .
    #     """

    #     val = self.GetValue()
    #     wx.CallAfter(self.SetSelection, 0, 0)

    #     if val:
    #         validated, new_val = self.GetValidator().validate_value(val)
    #         if validated:
    #             self.SetValueStr(val)
    #         else:
    #             self.SetValueStr(new_val)


    def SetValueStr(self, val):
        self.SetValue(val)
        wx.TextCtrl.SetValue(self, "%s %s" % (val, self.unit))

    def GetValue(self):
        """ Return the value as an integer
        If the field is empty, None will be returned. If and illegal value is
        present, an exception will be raised.
        """
        val = wx.TextCtrl.GetValue(self)

        # Strip the unit symbols
        if val.endswith(self.unit):
            val = val[:-len(self.unit)]

        return self._check_value(val)

    def GetValueStr(self):
        return "%s %s" % (IntegerTextCtrl.GetValueStr(self), self.unit)
