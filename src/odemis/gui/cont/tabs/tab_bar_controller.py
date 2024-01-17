# -*- coding: utf-8 -*-

"""
@author: Rinze de Laat, Éric Piel, Philip Winkler, Victoria Mavrikopoulou,
         Anders Muskens, Bassim Lazem

Copyright © 2012-2022 Rinze de Laat, Éric Piel, Delmic

Handles the switch of the content of the main GUI tabs.

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

import logging
import wx
# IMPORTANT: wx.html needs to be imported for the HTMLWindow defined in the XRC
# file to be correctly identified. See: http://trac.wxwidgets.org/ticket/3626
# This is not related to any particular wxPython version and is most likely permanent.
import wx.html

from odemis.gui.util.wx_adapter import fix_static_text_clipping

from odemis.gui.util import call_in_wx_main


class TabBarController(object):
    def __init__(self, tab_defs, main_frame, main_data):
        """
        tab_defs (dict of four entries string -> value):
           name -> string: internal name
           controller -> Tab class: class controlling the tab
           button -> Button: tab btn
           panel -> Panel: tab panel
        """
        self.main_frame = main_frame
        self._tabs = main_data.tab  # VA that we take care of
        self.main_data = main_data

        # create all the tabs that fit the microscope role
        tab_list, default_tab = self._create_needed_tabs(tab_defs, main_frame, main_data)

        if not tab_list:
            msg = "No interface known for microscope %s" % main_data.role
            raise LookupError(msg)

        for tab in tab_list:
            tab.button.Bind(wx.EVT_BUTTON, self.on_click)

        # When setting a value for an Enumerated VA, the value must be part of
        # its choices, and when setting its choices its current value must be
        # one of them. Therefore, we first set the current tab using the
        # `._value` attribute, so that the check will fail. We can then set the
        # `choices` normally.
        self._tabs._value = default_tab
        # Choices is a dict of Tab -> str: Tab controller -> name of the tab
        self._tabs.choices = {t: t.name for t in tab_list}
        # Indicate the value has changed
        self._tabs.notify(self._tabs.value)
        self._tabs.subscribe(self._on_tab_change, init=True)

        self._enabled_tabs = set()  # tabs which were enabled before starting acquisition
        self.main_data.is_acquiring.subscribe(self.on_acquisition)

        self._tabs_fixed_big_text = set()  # {str}: set of tab names which have been fixed

    def get_tabs(self):
        return self._tabs.choices

    @call_in_wx_main
    def on_acquisition(self, is_acquiring):
        if is_acquiring:
            # Remember which tab is already disabled, to not enable those afterwards
            self._enabled_tabs = set()
            for tab in self._tabs.choices:
                if tab.button.Enabled:
                    self._enabled_tabs.add(tab)
                    tab.button.Enable(False)
        else:
            if not self._enabled_tabs:
                # It should never happen, but just to protect in case it was
                # called twice in a row acquiring
                logging.warning("No tab to enable => will enable them all")
                self._enabled_tabs = set(self._tabs.choices)

            for tab in self._enabled_tabs:
                tab.button.Enable(True)

    def _create_needed_tabs(self, tab_defs, main_frame, main_data):
        """ Create the tabs needed by the current microscope

        Tabs that are not wanted or needed will be removed from the list and the associated
        buttons will be hidden in the user interface.

        returns tabs (list of Tabs): all the compatible tabs
                default_tab (Tab): the first tab to be shown
        """

        role = main_data.role
        logging.debug("Creating tabs belonging to the '%s' interface", role or "standalone")

        tabs = []  # Tabs
        buttons = set()  # Buttons used for the current interface
        main_sizer = main_frame.GetSizer()
        default_tab = None
        max_prio = -1

        for tab_def in tab_defs:
            priority = tab_def["controller"].get_display_priority(main_data)
            if priority is not None:
                assert priority >= 0
                tpnl = tab_def["panel"](self.main_frame)
                # Insert as "second" item, to be just below the buttons.
                # As only one tab is shown at a time, the exact order isn't important.
                main_sizer.Insert(1, tpnl, flag=wx.EXPAND, proportion=1)
                tab = tab_def["controller"](tab_def["name"], tab_def["button"],
                                            tpnl, main_frame, main_data)
                if max_prio < priority:
                    max_prio = priority
                    default_tab = tab
                tabs.append(tab)
                assert tab.button not in buttons
                buttons.add(tab.button)

        # Hides the buttons which are not used
        for tab_def in tab_defs:
            b = tab_def["button"]
            if b not in buttons:
                b.Hide()

        if len(tabs) <= 1:  # No need for tab buttons at all
            main_frame.pnl_tabbuttons.Hide()

        return tabs, default_tab

    @call_in_wx_main
    def _on_tab_change(self, tab):
        """ This method is called when the current tab has changed """
        logging.debug("Switch to tab %s", tab.name)
        for t in self._tabs.choices:
            if t.IsShown():
                t.Hide()
        tab.Show()
        self.main_frame.Layout()

        # Force resize, on the first time the tab is shown
        if tab.name not in self._tabs_fixed_big_text:
            fix_static_text_clipping(tab.panel)
            self._tabs_fixed_big_text.add(tab.name)

    def query_terminate(self):
        """
        Call each tab query_terminate to perform any action prior to termination
        :return: (bool) True to proceed with termination, False for canceling
        """
        for t in self._tabs.choices:
            if not t.query_terminate():
                logging.debug("Window closure vetoed by tab %s" % t.name)
                return False
        return True

    def terminate(self):
        """ Terminate each tab (i.e., indicate they are not used anymore) """

        for t in self._tabs.choices:
            t.terminate()

    def on_click(self, evt):
        evt_btn = evt.GetEventObject()
        for t in self._tabs.choices:
            if evt_btn == t.button:
                self._tabs.value = t
                break
        else:
            logging.warning("Couldn't find the tab associated to the button %s", evt_btn)

        evt.Skip()
