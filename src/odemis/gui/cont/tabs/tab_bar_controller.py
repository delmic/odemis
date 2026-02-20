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

from odemis.gui.util.wx_adapter import fix_static_text_clipping

from odemis.gui.util import call_in_wx_main


class TabController(object):
    def __init__(self, tab_list, tab_va, main_frame, main_data, default_tab):
        """
        :param: tab_list: (List[Tab]) list of odemis.gui.cont.tabs.tab.Tab objects.
        :param: tab_va: (VAEnumerated) va which stores the current tab and all
            available tabs in choices.
        :param: main_frame: odemis.gui.main_xrc.xrcfr_main
        :param: main_data: (MainGUIData) the main GUI data.
        :param: default_tab: (Tab) the default_tab to be shown.
        :param: use_main_data_tab: (bool) flag to use the MainGUIData tab VAEnumerated.
        """
        self.main_frame = main_frame
        self.main_data = main_data
        self._tab = tab_va

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
        self._tab._value = default_tab
        # Choices is a dict of Tab -> str: Tab controller -> name of the tab
        self._tab.choices = {t: t.name for t in tab_list}
        # Indicate the value has changed
        self._tab.notify(self._tab.value)
        self._tab.subscribe(self._on_tab_change, init=True)

        self.main_data.is_acquiring.subscribe(self.on_acquisition)

        self._tab_fixed_big_text = set()  # {str}: set of tab names which have been fixed

    def get_tabs(self):
        return self._tab.choices

    @call_in_wx_main
    def on_acquisition(self, is_acquiring):
        # Generic term for "busy", not always acquiring. Can also be moving the stage.
        if is_acquiring:
            # Remember which tab is already disabled, to not enable those afterwards
            for tab in self._tab.choices:
                tab.should_be_enabled = tab.button.Enabled
                tab.button.Enable(False)
        else:
            for tab in self._tab.choices:
                # Check if button should be enabled based on its state
                # (which may have been set by stage position callback)
                tab.button.Enable(tab.should_be_enabled)

    @call_in_wx_main
    def _on_tab_change(self, tab):
        """ This method is called when the current tab has changed """
        logging.debug("Switch to tab %s", tab.name)
        for t in self._tab.choices:
            if t.IsShown():
                t.Hide()
        tab.Show()
        self.main_frame.Layout()

        # Force resize, on the first time the tab is shown
        if tab.name not in self._tab_fixed_big_text:
            fix_static_text_clipping(tab.panel)
            self._tab_fixed_big_text.add(tab.name)

    def query_terminate(self):
        """
        Call each tab query_terminate to perform any action prior to termination
        :return: (bool) True to proceed with termination, False for canceling
        """
        for t in self._tab.choices:
            if not t.query_terminate():
                logging.debug("Window closure vetoed by tab %s" % t.name)
                return False
        return True

    def terminate(self):
        """ Terminate each tab (i.e., indicate they are not used anymore) """

        for t in self._tab.choices:
            t.terminate()

    def on_click(self, evt):
        evt_btn = evt.GetEventObject()
        for t in self._tab.choices:
            if evt_btn == t.button:
                self._tab.value = t
                break
        else:
            logging.warning("Couldn't find the tab associated to the button %s", evt_btn)

        evt.Skip()


class TabBarController(TabController):
    def __init__(self, tab_defs, main_frame, main_data):
        """
        tab_defs (dict of four entries string -> value):
           name -> string: internal name
           controller -> Tab class: class controlling the tab
           button -> Button: tab btn
           panel -> Panel: tab panel
        """

        # create all the tabs that fit the microscope role
        tab_list, default_tab = self._create_needed_tabs(tab_defs, main_frame, main_data)
        super().__init__(tab_list, main_data.tab, main_frame, main_data, default_tab=default_tab)

    def _create_needed_tabs(self, tab_defs, main_frame, main_data):
        """ Create the tabs needed by the current microscope. The tab's parent is the main_frame.

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
                tpnl = tab_def["panel"](main_frame)
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
                tab.button.Show()

        # The Odemis Viewer has Analysis and Correlation tabs. The Correlation tab is disabled
        # by default and in this case hide the tab buttons panel. In general, hide the tab buttons
        # panel if there is only 1 tab or no tabs.
        if main_data.is_viewer or len(tabs) <= 1:
            main_frame.pnl_tabbuttons.Hide()

        return tabs, default_tab
