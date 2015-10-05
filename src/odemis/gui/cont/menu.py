# -*- coding: utf-8 -*-
"""
Created on 2 Jul 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
"""

from __future__ import division

import subprocess
import sys
import wx

from odemis import model, gui
from odemis.acq import stream
from odemis.gui import DYE_LICENCE
from odemis.gui.comp.popup import Message
import odemis.gui.conf
from odemis.gui.model.dye import DyeDatabase
from odemis.gui.util import call_in_wx_main


# Menu controller:
# noinspection PyArgumentList
class MenuController(object):
    """ This controller handles (some of) the menu actions.
    Some other actions are directly handled by the main class or the specific
    tab controller.
    """

    def __init__(self, main_data, main_frame):
        """ Binds the menu actions.

        main_data (MainGUIData): the representation of the microscope GUI
        main_frame: (wx.Frame): the main frame of the GUI
        """
        self._main_data = main_data
        self._main_frame = main_frame

        # /File
        # /File/Open...
        wx.EVT_MENU(main_frame,
                    main_frame.menu_item_open.GetId(),
                    self._on_open)

        # /File/Save (as), is handled by the snapshot controller

        # Assign 'Reset fine alignment' functionality (if the tab exists)
        try:
            main_data.getTabByName("secom_align")
        except LookupError:
            menu_file = main_frame.GetMenuBar().GetMenu(0)
            menu_file.RemoveItem(main_frame.menu_item_reset_finealign)
        else:
            wx.EVT_MENU(main_frame,
                        main_frame.menu_item_reset_finealign.GetId(),
                        self._on_reset_align)

        if main_data.microscope:
            wx.EVT_MENU(main_frame,
                        main_frame.menu_item_halt.GetId(),
                        self.on_stop_axes)
        else:
            menu_file = main_frame.GetMenuBar().GetMenu(0)
            menu_file.RemoveItem(main_frame.menu_item_halt)

        # /File/Quit is handled by main

        # /View

        # /View/2x2 is handled by the tab controllers
        # /View/cross hair is handled by the tab controllers

        # TODO: disable next 3 if no current stream
        self._prev_streams = None  # latest tab.streams VA represented
        self._prev_stream = None  # latest stream represented by the menu

        # /View/Play Stream
        wx.EVT_MENU(main_frame,
                    main_frame.menu_item_play_stream.GetId(),
                    self._on_play_stream)
        # FIXME: it seems that even disabled, pressing F6 will toggle/untoggle
        # the entry (but not call _on_play_stream()).
        # Using wx.EVT_UPDATE_UI doesn't seem to help

        # View/Auto Brightness/Contrast
        wx.EVT_MENU(main_frame,
                    main_frame.menu_item_auto_cont.GetId(),
                    self._on_auto_bc)

        # View/Auto Focus
        wx.EVT_MENU(main_frame,
                    main_frame.menu_item_auto_focus.GetId(),
                    self._on_auto_focus)

        # TODO: add auto focus to toolbar

        # View/fit to content
        wx.EVT_MENU(main_frame,
                    main_frame.menu_item_fit_content.GetId(),
                    self._on_fit_content)
        main_data.tab.subscribe(self._on_tab_change, init=True)

        # /Help
        gc = odemis.gui.conf.get_general_conf()

        # /Help/Manual
        wx.EVT_MENU(main_frame,
                    main_frame.menu_item_manual.GetId(),
                    self._on_manual)
        if gc.get_manual(main_data.role):
            main_frame.menu_item_manual.Enable(True)

        # /Help/Development/Manual
        wx.EVT_MENU(main_frame,
                    main_frame.menu_item_devmanual.GetId(),
                    self._on_dev_manual)
        if gc.get_dev_manual():
            main_frame.menu_item_devmanual.Enable(True)

        # /Help/Development/Inspect GUI
        wx.EVT_MENU(main_frame,
                    main_frame.menu_item_inspect.GetId(),
                    self._on_inspect)

        # /Help/Development/Debug
        wx.EVT_MENU(main_frame,
                    main_frame.menu_item_debug.GetId(),
                    self._on_debug_menu)
        main_data.debug.subscribe(self._on_debug_va, init=True)

        # TODO: make it work on Windows too
        # /Help/Report a problem...
        if sys.platform.startswith('win32'):
            main_frame.menu_item_bugreport.Enable(False)
        else:
            wx.EVT_MENU(main_frame,
                        main_frame.menu_item_bugreport.GetId(),
                        self._on_bugreport)

        # /Help/About
        wx.EVT_MENU(main_frame,
                    main_frame.menu_item_about.GetId(),
                    self._on_about)

        tab = self._main_data.tab.value
        if hasattr(tab.tab_data_model, 'autofocus_active'):
            tab.tab_data_model.autofocus_active.subscribe(self._on_auto_focus_state)

    def on_stop_axes(self, evt):
        if self._main_data:
            Message.show_message(self._main_frame, "Stopping motion on every axes")
            self._main_data.stopMotion()
        else:
            evt.Skip()

    def _on_reset_align(self, evt):
        """
        Removes metadata info for the alignment
        """
        # Technically, we cannot "remove" metadata, but we can set it to the
        # default value
        md = {model.MD_POS_COR: (0, 0),
              model.MD_ROTATION_COR: 0,
              model.MD_PIXEL_SIZE_COR: (1, 1)}

        self._main_data.ccd.updateMetadata(md)

        # Will be enabled next time fine alignment is set
        self._main_frame.menu_item_reset_finealign.Enable(False)

    def _get_current_stream(self):
        """
        Find the current stream of the current tab
        return (Stream): the current stream
        raises:
            LookupError: if no stream present at all
        """
        tab = self._main_data.tab.value
        tab_data = tab.tab_data_model
        try:
            return tab_data.streams.value[0]
        except IndexError:
            raise LookupError("No stream")

    def _on_tab_change(self, tab):
        tab_data = tab.tab_data_model
        if self._prev_streams:
            self._prev_streams.unsubscribe(self._on_current_stream)

        if hasattr(tab_data, "streams"):
            self._prev_streams = tab_data.streams
            tab_data.streams.subscribe(self._on_current_stream, init=True)

        # Handle fit to content
        fit_enable = hasattr(tab, "view_controller") and tab.view_controller is not None
        self._main_frame.menu_item_fit_content.Enable(fit_enable)

    @call_in_wx_main
    def _on_current_stream(self, streams):
        """
        Called when some VAs affecting the current stream change
        """
        # Try to get the current stream, if it fails, it means we should
        # disable the related menu items
        try:
            curr_s = streams[0]
        except IndexError:
            curr_s = None
        enable = curr_s is not None

        if self._prev_stream:
            self._prev_stream.should_update.unsubscribe(self._on_stream_update)
            if hasattr(self._prev_stream, "auto_bc"):
                self._prev_stream.auto_bc.unsubscribe(self._on_stream_autobc)
        self._prev_stream = curr_s

        static = isinstance(curr_s, stream.StaticStream)
        pp_enable = enable and not static
        self._main_frame.menu_item_play_stream.Enable(pp_enable)
        if not pp_enable:
            self._main_frame.menu_item_play_stream.Check(False)

        self._main_frame.menu_item_auto_cont.Enable(enable)
        if not enable:
            self._main_frame.menu_item_auto_cont.Check(False)

        if curr_s:
            curr_s.should_update.subscribe(self._on_stream_update, init=True)
            if hasattr(curr_s, "auto_bc"):
                curr_s.auto_bc.subscribe(self._on_stream_autobc, init=True)
        else:
            self._main_frame.menu_item_auto_focus.Enable(False)

    @call_in_wx_main
    def _on_auto_focus_state(self, active):
        # Be able to cancel current autofocus
        accel = self._main_frame.menu_item_auto_focus.GetAccel().ToString()
        if active:
            self._main_frame.menu_item_auto_focus.SetItemLabel("Stop Auto Focus\t" + accel)
        else:
            self._main_frame.menu_item_auto_focus.SetItemLabel("Auto Focus\t" + accel)
        tab = self._main_data.tab.value
        streams = tab.tab_data_model.streams.value
        self._on_current_stream(streams)

    @call_in_wx_main
    def _on_stream_update(self, updated):
        """
        Called when the current stream changes play/pause
        """
        try:
            curr_s = self._get_current_stream()
        except LookupError:
            return

        static = isinstance(curr_s, stream.StaticStream)
        self._main_frame.menu_item_play_stream.Check(updated and not static)

        # enable only if focuser is available
        f_enable = (updated and curr_s.focuser is not None)
        self._main_frame.menu_item_auto_focus.Enable(f_enable)

    @call_in_wx_main
    def _on_stream_autobc(self, autobc):
        """
        Called when the current stream changes Auto BC
        """
        self._main_frame.menu_item_auto_cont.Check(autobc)

    def _on_play_stream(self, evt):
        try:
            curr_s = self._get_current_stream()
        except LookupError:
            return

        # StaticStreams have a should_update, but nothing happens
        if isinstance(curr_s, stream.StaticStream):
            return

        # inverse the current status
        curr_s.should_update.value = not curr_s.should_update.value

    def _on_auto_bc(self, evt):
        """
        Toggle the AutoBC of the current stream
        """
        try:
            curr_s = self._get_current_stream()
        except LookupError:
            return

        if hasattr(curr_s, "auto_bc"):
            # inverse the current status
            curr_s.auto_bc.value = not curr_s.auto_bc.value

    def _on_fit_content(self, evt):
        """
        Adjust the MPP of the current view to have the content just fit
        """
        tab = self._main_data.tab.value
        if hasattr(tab, "view_controller") and tab.view_controller is not None:
            tab.view_controller.fitViewToContent()

    def _on_auto_focus(self, evt):
        tab = self._main_data.tab.value
        if tab.tab_data_model.autofocus_active.value:
            tab.tab_data_model.autofocus_active.value = False
        else:
            tab.tab_data_model.autofocus_active.value = True

    def _on_open(self, evt):
        """
        Same as "select image..." of the gallery, but automatically switch to the
        gallery tab (if a file is selected)
        """
        analysis_tab = self._main_data.getTabByName("analysis")
        if analysis_tab.select_acq_file():
            # show the new tab
            self._main_data.tab.value = analysis_tab

    def _on_bugreport(self, evt):
        # Popen, so that it's non-blocking
        subprocess.Popen("odemis-bug-report")

    def _on_about(self, evt):
        info = wx.AboutDialogInfo()
        info.SetIcon(gui.icon)
        info.Name = gui.name
        info.Version = odemis.__version__
        info.Description = odemis.__fullname__
        info.Copyright = odemis.__copyright__
        info.WebSite = ("http://delmic.com", "delmic.com")
        info.Licence = odemis.__licensetxt__
        info.Developers = odemis.__authors__
        # info.DocWriter = '???'
        # info.Artist = '???'
        # info.Translator = '???'

        if DyeDatabase:
            info.Developers += ["", "Dye database from http://fluorophores.org"]
            info.Licence += DYE_LICENCE
        wx.AboutBox(info)

    def _on_manual(self, evt):
        gc = odemis.gui.conf.get_general_conf()
        subprocess.Popen(['xdg-open', gc.get_manual(self._main_data.role)])

    def _on_dev_manual(self, evt):
        gc = odemis.gui.conf.get_general_conf()
        subprocess.Popen(['xdg-open', gc.get_dev_manual()])

    def _on_inspect(self, evt):
        from wx.lib.inspection import InspectionTool
        InspectionTool().Show()

    # def on_htmldoc(self, evt):
    #     """ Launch Python's SimpleHTTPServer in a separate process and have it
    #     serve the source code documentation as created by Sphinx
    #     """
    #     self.http_proc = subprocess.Popen(
    #         ["python", "-m", "SimpleHTTPServer"],
    #         stderr=subprocess.STDOUT,
    #         stdout=subprocess.PIPE,
    #         cwd=os.path.dirname(odemis.gui.conf.get_general_conf().html_dev_doc))
    #
    #     import webbrowser
    #     webbrowser.open('http://localhost:8000')


    @call_in_wx_main
    def _on_debug_va(self, enabled):
        """ Update the debug menu check """
        self._main_frame.menu_item_debug.Check(enabled)

    def _on_debug_menu(self, evt):
        """ Update the debug VA according to the menu
        """
        # TODO: use event?
        self._main_data.debug.value = self._main_frame.menu_item_debug.IsChecked()
