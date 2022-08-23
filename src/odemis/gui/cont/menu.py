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

import logging
from odemis import model, gui
from odemis.acq import stream
from odemis.gui import DYE_LICENCE
from odemis.gui.comp import popup
import odemis.gui.conf
from odemis.gui.model import CHAMBER_VACUUM, CHAMBER_UNKNOWN
from odemis.gui.model.dye import DyeDatabase
from odemis.gui.util import call_in_wx_main
from odemis.util import driver
import os
import subprocess
import sys
import wx


# Menu controller:
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
        main_frame.Bind(wx.EVT_MENU, self._on_open, id=main_frame.menu_item_open.GetId())

        # /File/Save (as), is handled by the snapshot controller

        menu_file = main_frame.GetMenuBar().GetMenu(0)
        # Assign 'Reset fine alignment' functionality (if the tab exists)
        if "secom_align" in main_data.tab.choices.values():
            main_frame.Bind(wx.EVT_MENU, self._on_reset_align, id=main_frame.menu_item_reset_finealign.GetId())
        else:
            menu_file.Remove(main_frame.menu_item_reset_finealign)

        # Assign 'Reset overview' functionality (if the tab exists)
        if main_data.role == "secom":
            main_frame.Bind(wx.EVT_MENU, self._on_reset_overview, id=main_frame.menu_item_reset_overview.GetId())
            self._main_frame.menu_item_reset_overview.Enable(True)
        else:
            menu_file.Remove(main_frame.menu_item_reset_overview)

        if main_data.microscope:
            main_frame.Bind(wx.EVT_MENU, self.on_stop_axes, id=main_frame.menu_item_halt.GetId())
        else:
            menu_file.Remove(main_frame.menu_item_halt)

        # /File/Recalibrate Sample Holder (only on Delphi)
        # The event is handled by the DelphiStateController
        if main_data.role != "delphi":
            menu_file.Remove(main_frame.menu_item_recalibrate)

        # /File/Quit is handled by main

        # /View

        # /View/2x2 is handled by the tab controllers
        # /View/cross hair is handled by the tab controllers

        self._prev_streams = None  # latest tab.streams VA represented
        self._prev_stream = None  # latest stream represented by the menu
        self._prev_autofocus = None  # latest tab.autofocus_active VA

        # /View/Play Stream
        main_frame.Bind(wx.EVT_MENU, self._on_play_stream, id=main_frame.menu_item_play_stream.GetId())

        main_data.is_acquiring.subscribe(self._on_acquisition)
        main_data.chamberState.subscribe(self._on_chamber_state)
        # FIXME: it seems that even disabled, pressing F6 will toggle/untoggle
        # the entry (but not call _on_play_stream()).
        # Using wx.EVT_UPDATE_UI doesn't seem to help

        # View/Auto Brightness/Contrast
        main_frame.Bind(wx.EVT_MENU, self._on_auto_bc, id=main_frame.menu_item_auto_cont.GetId())

        # View/Auto Focus
        main_frame.Bind(wx.EVT_MENU, self._on_auto_focus, id=main_frame.menu_item_auto_focus.GetId())

        # View/fit to content
        main_frame.Bind(wx.EVT_MENU, self._on_fit_content, id=main_frame.menu_item_fit_content.GetId())
        main_data.tab.subscribe(self._on_tab_change, init=True)

        # /Help
        gc = odemis.gui.conf.get_general_conf()

        # /Help/Manual
        main_frame.Bind(wx.EVT_MENU, self._on_manual, id=main_frame.menu_item_manual.GetId())
        if gc.get_manual(main_data.role):
            main_frame.menu_item_manual.Enable(True)

        # /Help/Development/Manual
        main_frame.Bind(wx.EVT_MENU, self._on_dev_manual, id=main_frame.menu_item_devmanual.GetId())
        if gc.get_dev_manual():
            main_frame.menu_item_devmanual.Enable(True)

        # /Help/Development/Inspect GUI
        main_frame.Bind(wx.EVT_MENU, self._on_inspect, id=main_frame.menu_item_inspect.GetId())

        # /Help/Development/Debug
        main_frame.Bind(wx.EVT_MENU, self._on_debug_menu, id=main_frame.menu_item_debug.GetId())
        main_data.debug.subscribe(self._on_debug_va, init=True)

        # TODO: make it work on Windows too
        # /Help/Report a problem...
        if sys.platform.startswith('win32'):
            main_frame.menu_item_bugreport.Enable(False)
        else:
            main_frame.Bind(wx.EVT_MENU, self._on_bugreport, id=main_frame.menu_item_bugreport.GetId())

        # /Help/Check for update
        if os.name == 'nt' and getattr(sys, 'frozen', False):
            main_frame.Bind(wx.EVT_MENU, self._on_update, id=main_frame.menu_item_update.GetId())
        else:
            menu = main_frame.menu_item_update.GetMenu()
            menu.Remove(main_frame.menu_item_update)
            main_frame.menu_item_update.Destroy()

        # /Help/About
        main_frame.Bind(wx.EVT_MENU, self._on_about, id=main_frame.menu_item_about.GetId())

    def _on_update(self, evt):
        import odemis.gui.util.updater as updater
        u = updater.WindowsUpdater()
        u.check_for_update()

    def on_stop_axes(self, evt):
        if self._main_data:
            popup.show_message(self._main_frame, "Stopping motion on every axes", timeout=1)
            self._main_data.stopMotion()
        else:
            evt.Skip()

    def _on_reset_align(self, evt):
        """
        Removes metadata info for the alignment
        """
        # Technically, we cannot "remove" metadata, but we can set it to the
        # default value
        ccdmd = {model.MD_POS_COR: (0, 0),
                 model.MD_ROTATION_COR: 0,
                 model.MD_PIXEL_SIZE_COR: (1, 1)}
        self._main_data.ccd.updateMetadata(ccdmd)

        # TODO: if SEM has been rotated (via rotation VA + MD_ROTATION_COR), remove it too?
        semmd = {model.MD_SHEAR_COR: 0,
                 model.MD_PIXEL_SIZE_COR: (1, 1)}
        self._main_data.ebeam.updateMetadata(semmd)

        # Will be enabled next time fine alignment is set
        self._main_frame.menu_item_reset_finealign.Enable(False)

    def _on_reset_overview(self, _):
        """
        Empty the overview view (on SECOM)
        """
        live_tab = self._main_data.getTabByName("secom_live")
        live_tab.overview_controller.reset_ovv()

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

    @call_in_wx_main
    def _on_tab_change(self, tab):
        tab_data = tab.tab_data_model

        # Autofocus
        if self._prev_autofocus:
            self._prev_autofocus.unsubscribe(self._on_auto_focus_state)

        # Autofocus menu disabled if the tab doesn't support it.
        if hasattr(tab.tab_data_model, 'autofocus_active'):
            # supported => current stream decides if it's enabled
            tab.tab_data_model.autofocus_active.subscribe(self._on_auto_focus_state, init=True)
            self._prev_autofocus = tab.tab_data_model.autofocus_active
        else:
            self._main_frame.menu_item_auto_focus.Enable(False)

        # Current stream
        if self._prev_streams:
            self._prev_streams.unsubscribe(self._on_current_stream)

        tab_data.streams.subscribe(self._on_current_stream, init=True)
        self._prev_streams = tab_data.streams

        # Fit to content
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

        self._main_frame.menu_item_auto_cont.Enable(enable)
        if not enable:
            self._main_frame.menu_item_auto_cont.Check(False)

        self._update_stream_play_pause(curr_s)
        if curr_s:
            curr_s.should_update.subscribe(self._on_stream_update, init=True)
            if hasattr(curr_s, "auto_bc"):
                curr_s.auto_bc.subscribe(self._on_stream_autobc, init=True)
        else:
            self._main_frame.menu_item_auto_focus.Enable(False)

    def _update_stream_play_pause(self, curr_s):
        """
        Update the play/pause menu entry (ie, enabled & checked)
        Depends on the current stream, and various global state.
        Must be called within GUI thread.
        curr_s (Stream or None): the current stream. None if there is no stream.
        """
        main_data = self._main_data
        # Can play/pause iff:
        #  * It's a Stream, but not Static
        #  * The sample is loaded/chamber is under vacuum
        #    TODO: actually, on the SECOM it's fine to play opt streams when
        #    chamber is vented. cf StateController.cls_streams_involved
        #  * No acquisition is active (excepted for the SPARC chamber tab, as
        #    moving the mirror counts as an "acquisition", but we want to play
        #    the stream anyway)
        # TODO: replace the last checks by looking if the stream entry play/pause
        # button is enabled? or create a VA TabDataModel.PlayableStreams, which
        # contains the stream classes which are _currently_ playable?
        can_play = (curr_s is not None and
                    not isinstance(curr_s, stream.StaticStream) and
                    ((main_data.chamberState.value in {CHAMBER_VACUUM, CHAMBER_UNKNOWN} and
                      not main_data.is_acquiring.value) or
                     main_data.tab.value.name == "sparc_chamber"))

        self._main_frame.menu_item_play_stream.Enable(can_play)
        if not can_play:
            self._main_frame.menu_item_play_stream.Check(False)

    def _on_chamber_state(self, _):
        wx.CallAfter(self._update_stream_play_pause, self._prev_stream)

    def _on_acquisition(self, _):
        wx.CallAfter(self._update_stream_play_pause, self._prev_stream)

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
        tab = self._main_data.tab.value
        if tab.name == "sparc2_align":
            # TODO: autofocus enabling is quite a mess on this tab. Eventually,
            # it should be the same code that controls the autofocus button.
            f_enable = (tab.tab_data_model.align_mode.value == "lens-align" and
                        self._main_data.focus is not None)
        elif hasattr(tab.tab_data_model, 'autofocus_active'):
            f_enable = (updated and curr_s.focuser is not None)
        else:
            f_enable = False
        self._main_frame.menu_item_auto_focus.Enable(f_enable)

    @call_in_wx_main
    def _on_stream_autobc(self, autobc):
        """
        Called when the current stream changes Auto BC
        """
        self._main_frame.menu_item_auto_cont.Check(autobc)

    def _on_play_stream(self, evt):
        """
        Called when the play/pause menu entry is modified (or F6 pressed)
        """
        try:
            curr_s = self._get_current_stream()
        except LookupError:
            return

        if not self._main_frame.menu_item_play_stream.IsEnabled():
            logging.warning("Cannot play/pause stream when menu entry is disabled")
            return

        # TODO: should never happen => remove?
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

        info = wx.adv.AboutDialogInfo()
        info.SetIcon(gui.icon)
        info.Name = gui.name
        info.Version = odemis.__version__
        info.Description = odemis.__fullname__
        info.Copyright = odemis.__copyright__
        info.WebSite = ("http://delmic.com", "delmic.com")
        info.License = odemis.__licensetxt__
        info.Developers = odemis.__authors__
        # info.DocWriters = ['???']
        # info.Artists = ['???']
        # info.Translators = ['???']

        if DyeDatabase:
            info.Developers += ["", "Dye database from Fluorophores.org http://fluorophores.org"]
            info.License += DYE_LICENCE

        # Show the plugins
        app = wx.GetApp()
        if app.plugins:
            # Add a flag so it appears in big that some plugins are loaded
            info.Description += " (+ plugins)"
            info.Developers += ["", "Plugins:"]
            for p in app.plugins:
                info.Developers += [u"%s by %s under %s license" %
                                    (p, p.__author__, p.__license__)]

        try:
            mem_usage = driver.readMemoryUsage() / 2 ** 20  # MiB
            info.Description += "\n(%0.2f MiB memory used)" % mem_usage
        except NotImplementedError:
            pass

        wx.adv.AboutBox(info)

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
