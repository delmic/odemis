# -*- coding: utf-8 -*-

import locale
import  sys

import wx
import  wx.lib.mixins.listctrl as listmix

# this reads the environment and inits the right locale
locale.setlocale(locale.LC_ALL, "")


# wxPython Custom Widget Collection 20060207
# Written By: Edward Flick (eddy -=at=- cdf-imaging -=dot=- com)
#             Michele Petrazzo (michele -=dot=- petrazzo -=at=- unipex -=dot=- it)
#             Will Sadkin (wsadkin-=at=- nameconnector -=dot=- com)
# Copyright 2006 (c) CDF Inc. ( http://www.cdf-imaging.com )
# Contributed to the wxPython project under the wxPython project's license.
# http://wiki.wxpython.org/index.cgi/TextCtrlAutoComplete
#
# Adaptation for Delmic by R. de Laat
#

class ChoiceListCtrl(wx.ListCtrl, listmix.ListCtrlAutoWidthMixin):
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
            therest['style'] = wx.TE_PROCESS_ENTER | wx.BORDER_NONE | therest['style']
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
    """ This validator makes sure that only valid integers are entered.
    """

    def __init__(self, min_val, max_val):
        """ Constructor """
        wx.PyValidator.__init__(self)
        self.Bind(wx.EVT_CHAR, self.OnChar)

        # All legal characters
        self.legal = "0123456789"

        self.min_val = min_val
        self.max_val = max_val

    def Clone(self):    #pylint: disable=W0221
        """ Required method """
        return IntegerValidator(self.min_val, self.max_val)

    def Validate(self, win=None):#pylint: disable=W0221,W0613
        """ This method is called when the 'Validate()' method is called on the
        parent of the TextCtrl to which this validator belongs.)
        """
        fld = self.GetWindow()
        val = fld.GetValue()

        # print self.min_val, val, val < self.min_val
        # print self.max_val, val, val > self.max_val

        if self.min_val is not None and val < self.min_val:
            return False
        if self.max_val is not None and val > self.max_val:
            return False
        return True

    def OnChar(self, event):
        """ This method prevents the entry of illegal characters """
        key = event.GetKeyCode()
        # Allow control keys to propagate
        if key < wx.WXK_SPACE or key == wx.WXK_DELETE or key > 255:
            event.Skip()
            return

        # Allow legal characters to reach the text control
        if chr(key) in self.legal:
            fld = self.GetWindow()
            val = fld.GetValueStr()
            pos = self.GetWindow().GetInsertionPoint()
            val = val[:pos] + chr(key) + val[pos:]

            if len(val) < 2 or (len(val) > 1 and val[0] != "0"):
                try:
                    val = int(val)
                    event.Skip()
                except ValueError:
                    return
        # 'Eat' the event by not Skipping it, thus preventing it.
        # from reaching the text control
        return

class IntegerTextCtrl(wx.TextCtrl):
    def __init__(self, *args, **kwargs):

        min_val = max_val = None

        if 'min_val' in kwargs:
            min_val = kwargs['min_val']
            del kwargs['min_val']
        if 'max_val' in kwargs:
            max_val = kwargs['max_val']
            del kwargs['max_val']

        kwargs['validator'] = IntegerValidator(min_val, max_val)
        wx.TextCtrl.__init__(self, *args, **kwargs)

    def SetValue(self, val): #pylint: disable=W0221
        if isinstance(val, int):
            wx.TextCtrl.SetValue(self, unicode(val))
        else:
            wx.TextCtrl.SetValue(self, unicode(val))

    def GetValue(self): #pylint: disable=W0221
        return int(wx.TextCtrl.GetValue(self) or 0)

    def GetValueStr(self):
        return wx.TextCtrl.GetValue(self)

class UnitIntegerCtrl(IntegerTextCtrl):
    """ This class represents a text control which is capable of formatting
    it's content according to the unit it set to.

    When the value is set through the API, the units are shown.
    When the control gets the focus, the value is shown without the units
    When focus is lost, the units will be shown again.
    """

    def __init__(self, *args, **kwargs):

        if 'unit' in kwargs:
            self.unit = kwargs['unit']
            del kwargs['unit']
        else:
            raise ValueError("The 'unit' keyword parameter needs to be set.")

        IntegerTextCtrl.__init__(self, *args, **kwargs)

        val = args[2]
        self.num_val = None
        self.SetValue(val)

        self.Bind(wx.EVT_SET_FOCUS, self.on_focus)
        self.Bind(wx.EVT_KILL_FOCUS, self.on_kill_focus)
        self.Bind(wx.EVT_TEXT_ENTER, self.on_text_enter)

    def on_text_enter(self, evt):
        if not self.GetValidator().Validate():
            self.reset()

    def on_focus(self, evt):
        """ Remove the units from the displayed value on focus """
        self.reset()

    def on_kill_focus(self, evt):
        """ Display the current value with the units added when focus is
        lost .
        """
        if self.GetValidator().Validate():
            self.SetValue(self.GetValue())
        else:
            self.SetValue(self.num_val)

    def reset(self):
        IntegerTextCtrl.SetValue(self, "%s" % self.num_val)

    def SetValue(self, val): #pylint: disable=W0221
        """ Internally store the integer value 'val' and display it with
        the units added.
        """
        self.num_val = val
        wx.TextCtrl.SetValue(self, "%s %s" % (val, self.unit))

    def GetValueStr(self):
        return "%s %s" % (IntegerTextCtrl.GetValueStr(self), self.unit)
