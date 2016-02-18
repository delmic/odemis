#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@author: Rinze de Laat

Copyright © 2012-2014 Rinze de Laat, Éric Piel, Delmic

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

import Pyro4.errors
import argparse
import logging
import sys
import threading
import traceback
import wx
from wx.lib.pubsub import pub
import os

from odemis import model, gui
import odemis
from odemis.gui import main_xrc, log
from odemis.gui.conf import get_general_conf
from odemis.gui.cont import acquisition
from odemis.gui.cont.menu import MenuController
from odemis.gui.util import call_in_wx_main
from odemis.gui.xmlh import odemis_get_resources
from odemis.util import driver
import odemis.gui.cont.tabs as tabs
import odemis.gui.img.data as imgdata
import odemis.gui.model as guimodel

# Ensure that the current working directory is the same as the location of this file
if getattr(sys, 'frozen', False):
    path = os.path.abspath(sys.executable)
else:
    path = os.path.abspath(__file__)
os.chdir(os.path.dirname(path))


class OdemisGUIApp(wx.App):
    """ This is Odemis' main GUI application class
    """

    def __init__(self, standalone=False, file_name=None):
        """

        Args:
            standalone: (bool or str) False, if not standalone, name string otherwise
            file_name: (str) Path to the file to open on launch

        """
        # Replace the standard 'get_resources' with our augmented one, that
        # can handle more control types. See the xhandler package for more info.
        main_xrc.get_resources = odemis_get_resources

        # Declare attributes BEFORE calling the super class constructor
        # because it will call 'OnInit' which uses them.

        self.main_data = None
        self.main_frame = None
        self.tab_controller = None
        self._is_standalone = standalone
        self._snapshot_controller = None
        self._menu_controller = None

        # User input devices
        self.dev_powermate = None

        l = logging.getLogger()
        self.log_level = l.getEffectiveLevel()

        if not self._is_standalone:
            try:
                driver.speedUpPyroConnect(model.getMicroscope())
            except Exception:
                logging.exception("Failed to speed up start up")

        # Output catcher using a helper class
        wx.App.outputWindowClass = OdemisOutputWindow

        # Constructor of the parent class
        # ONLY CALL IT AT THE END OF :py:method:`__init__` BECAUSE OnInit will
        # be called
        # and it needs the attributes defined in this constructor!
        wx.App.__init__(self, redirect=True)

        if file_name:
            tab = self.tab_controller.open_tab('analysis')
            wx.CallLater(500, tab.load_data, file_name)

    def OnInit(self):
        """ Initialize the GUI

        This method is automatically called from the :wx:`App` constructor

        """

        if self._is_standalone:
            microscope = None
            gui.icon = imgdata.catalog['ico_gui_viewer_256'].GetIcon()
            gui.name = odemis.__shortname__ + " Viewer"

            if "delphi" == self._is_standalone:
                gui.logo = imgdata.getlogo_delphiBitmap()
        else:
            gui.icon = imgdata.catalog['ico_gui_full_256'].GetIcon()
            gui.name = odemis.__shortname__
            try:
                microscope = model.getMicroscope()
            except (IOError, Pyro4.errors.CommunicationError), e:
                logging.exception("Failed to connect to back-end")
                msg = ("The Odemis GUI could not connect to the Odemis back-end:"
                       "\n\n{0}\n\n"
                       "Launch user interface anyway?").format(e)

                answer = wx.MessageBox(msg,
                                       "Connection error",
                                        style=wx.YES | wx.NO | wx.ICON_ERROR)
                if answer == wx.NO:
                    sys.exit(1)
                microscope = None

        logging.info("\n\n************  Starting Odemis GUI  ************\n")
        logging.info("Odemis GUI v%s (from %s)", odemis.__version__, __file__)
        logging.info("wxPython v%s", wx.version())

        # TODO: if microscope.ghost is not empty => wait and/or display a special
        # "hardware status" tab.

        self.main_data = guimodel.MainGUIData(microscope)
        # Load the main frame
        self.main_frame = main_xrc.xrcfr_main(None)

        self.init_gui()
        log.create_gui_logger(self.main_frame.txt_log, self.main_data.debug, self.main_data.level)

        try:
            from odemis.gui.dev.powermate import Powermate
            self.dev_powermate = Powermate(self.main_data)
        except (LookupError, NotImplementedError) as ex:
            logging.debug("Not using Powermate: %s", ex)
        except Exception:
            logging.exception("Failed to load Powermate support")

        if os.name == 'nt' and getattr(sys, 'frozen', False):
            if get_general_conf().get("viewer", "update") == "yes":
                import odemis.gui.util.updater as updater
                u = updater.WindowsUpdater()
                wx.CallLater(1000, u.check_for_update)

        # Application successfully launched
        return True

    def init_gui(self):
        """ This method binds events to menu items and initializes GUI controls """

        try:
            # Add frame icon
            ib = wx.IconBundle()
            ib.AddIcon(gui.icon)
            self.main_frame.SetIcons(ib)
            self.main_frame.SetTitle(gui.name)

            self.main_data.debug.subscribe(self.on_debug_va, init=True)
            self.main_data.level.subscribe(self.on_level_va, init=False)

            # List of all possible tabs used in Odemis' main GUI
            # microscope role(s), internal name, class, tab btn, tab panel
            # order matters, as the first matching tab is be the default one

            tab_defs = [
                {
                    # Unique name of the tab
                    "name": "secom_live",
                    # Default label for the tab (Might be overridden in the roles section
                    "label": "STREAMS",
                    # The microscope roles for which the tab is valid
                    "roles": {
                        "secom": {},
                        "delphi": {},
                        "sem": {},
                        "optical": {},
                    },
                    # Tab controller for this tab
                    "controller": tabs.SecomStreamsTab,
                    # Tab button for this tab
                    "button": self.main_frame.btn_tab_secom_streams,
                    # Constructor of the tab panel
                    "panel": main_xrc.xrcpnl_tab_secom_streams
                },
                {
                    "name": "secom_align",
                    "label": "LENS ALIGNMENT",
                    "roles": {
                        "secom": {},
                    },
                    "controller": tabs.SecomAlignTab,
                    "button": self.main_frame.btn_tab_secom_align,
                    "panel": main_xrc.xrcpnl_tab_secom_align
                },
                {
                    "name": "sparc_align",
                    "label": "ALIGNMENT",
                    "roles": {
                        "sparc": {},
                    },
                    "controller": tabs.SparcAlignTab,
                    "button": self.main_frame.btn_tab_sparc_align,
                    "panel": main_xrc.xrcpnl_tab_sparc_align
                },
                {
                    "name": "sparc2_align",
                    "label": "ALIGNMENT",
                    "roles": {
                        "sparc2": {"default": True},
                    },
                    "controller": tabs.Sparc2AlignTab,
                    "button": self.main_frame.btn_tab_sparc2_align,
                    "panel": main_xrc.xrcpnl_tab_sparc2_align
                },
                {
                    "name": "sparc_acqui",
                    "label": "ACQUISITION",
                    "roles": {
                        "sparc-simplex": {},
                        "sparc": {},
                        "sparc2": {},
                    },
                    "controller": tabs.SparcAcquisitionTab,
                    "button": self.main_frame.btn_tab_sparc_acqui,
                    "panel": main_xrc.xrcpnl_tab_sparc_acqui
                },
                {
                    "name": "sparc_chamber",
                    "label": "CHAMBER",
                    "roles": {
                        "sparc2": {},
                    },
                    "controller": tabs.ChamberTab,
                    "button": self.main_frame.btn_tab_sparc_chamber,
                    "panel": main_xrc.xrcpnl_tab_sparc_chamber
                },
                {
                    "name": "analysis",
                    "label": "GALLERY",
                    "roles": {
                        None: {},
                        "secom": {},
                        "delphi": {},
                        "sem": {},
                        "optical": {},
                        "sparc-simplex": {"label": "ANALYSIS"},
                        "sparc": {"label": "ANALYSIS"},
                        "sparc2": {"label": "ANALYSIS"},
                    },
                    "controller": tabs.AnalysisTab,
                    "button": self.main_frame.btn_tab_inspection,
                    "panel": main_xrc.xrcpnl_tab_inspection
                },
            ]

            # Create the main tab controller and store a global reference
            # in the odemis.gui.cont package
            self.tab_controller = tabs.TabBarController(tab_defs, self.main_frame, self.main_data)

            # Connect the log panel button of each tab
            def toggle_log_panel(_):
                self.main_data.debug.value = not self.main_frame.pnl_log.IsShown()

            for tab in self.tab_controller.get_tabs():
                if hasattr(tab.panel, 'btn_log'):
                    tab.panel.btn_log.Bind(wx.EVT_BUTTON, toggle_log_panel)
            self.main_frame.btn_log.Bind(wx.EVT_BUTTON, toggle_log_panel)

            self._menu_controller = MenuController(self.main_data, self.main_frame)
            # Menu events
            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_quit.GetId(),
                        self.on_close_window)

            self.main_frame.Bind(wx.EVT_CLOSE, self.on_close_window)

            # To handle "Save snapshot" menu
            self._snapshot_controller = acquisition.SnapshotController(self.main_data,
                                                                       self.main_frame)

            # Update the logo if a non-default logo is defined
            if gui.logo:
                self.main_frame.logo.SetBitmap(gui.logo)

            self.main_frame.Maximize()  # must be done before Show()
            # making it very late seems to make it smoother
            wx.CallAfter(self.main_frame.Show)

        except Exception:
            self.excepthook(*sys.exc_info())
            # Re-raise the exception, so the program will exit. If this is not
            # done and exception will prevent the GUI from being shown, while
            # the program keeps running in the background.
            raise

    @call_in_wx_main
    def on_debug_va(self, enabled):
        """ This method (un)sets the application into debug mode, setting the log level and
        opening the log panel. """

        self.main_frame.pnl_log.Show(enabled)

        l = logging.getLogger()
        if enabled:
            self.log_level = l.getEffectiveLevel()
            l.setLevel(logging.DEBUG)
            for tab in self.tab_controller.get_tabs():
                if hasattr(tab.panel, 'btn_log'):
                    tab.panel.btn_log.Hide()
                    # Reset highest log level
                    self.main_data.level.value = 0
        else:
            for tab in self.tab_controller.get_tabs():
                if hasattr(tab.panel, 'btn_log'):
                    tab.panel.btn_log.Show()
            l.setLevel(self.log_level)
        self.main_frame.Layout()

    @call_in_wx_main
    def on_level_va(self, log_level):
        """ Set the log button color """

        colour = 'def'

        if log_level >= logging.ERROR:
            colour = 'red'
        elif log_level >= logging.WARNING:
            colour = 'orange'

        for tab in self.tab_controller.get_tabs():
            if hasattr(tab.panel, 'btn_log'):
                tab.panel.btn_log.set_face_colour(colour)

    def on_close_window(self, evt=None):
        """ This method cleans up and closes the Odemis GUI. """
        logging.info("Exiting Odemis")

        if self.main_data.is_acquiring.value:
            msg = ("Acquisition in progress!\n\n"
                   "Please cancel the current acquisition operation before exiting Odemis.")
            dlg = wx.MessageDialog(self.main_frame, msg, "Exit", wx.OK | wx.ICON_STOP)
            # if dlg.ShowModal() == wx.ID_NO:
            dlg.ShowModal()
            dlg.Destroy()  # frame
            return

        if self.dev_powermate:
            self.dev_powermate.terminate()

        try:
            pub.unsubAll()
            # let all the tabs know we are stopping
            self.tab_controller.terminate()
        except Exception:
            logging.exception("Error during GUI shutdown")

        try:
            log.stop_gui_logger()
        except Exception:
            logging.exception("Error stopping GUI logging")

        self.main_frame.Destroy()

    def excepthook(self, etype, value, trace):
        """ Method to intercept unexpected errors that are not caught
        anywhere else and redirects them to the logger.
        Note that exceptions caught and logged will appear in the text pane,
        but not cause it to pop-up (as this method will not be called).
        """
        # in case of error here, don't call again, it'd create infinite recursion
        if sys and traceback:
            sys.excepthook = sys.__excepthook__

            try:
                exc = traceback.format_exception(etype, value, trace)
                try:
                    remote_tb = value._pyroTraceback
                    rmt_exc = "Remote exception %s" % ("".join(remote_tb),)
                except AttributeError:
                    rmt_exc = ""
                logging.error("".join(exc) + rmt_exc)

            finally:
                # put us back
                sys.excepthook = self.excepthook
        # python is ending... can't rely on anything
        else:
            print etype, value, trace


class OdemisOutputWindow(object):
    """ Helper class which allows ``wx`` to display uncaught
        messages in the way defined by the :py:mod:`log` module. """
    def __init__(self):
        pass

    def write(self, txt):
        if txt.strip() != "":
            logging.error("[CAP] %s", txt.strip())

    # Just to comply with the interface of sys.stdout
    def flush(self):
        pass


def installThreadExcepthook():
    """ Workaround for sys.excepthook thread bug
    http://spyced.blogspot.com/2007/06/workaround-for-sysexcepthook-bug.html

    Call once from ``__main__`` before creating any threads.
    """
    init_old = threading.Thread.__init__

    def init(self, *args, **kwargs):
        init_old(self, *args, **kwargs)
        run_old = self.run

        def run_with_except_hook(*args, **kw):
            try:
                run_old(*args, **kw)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                sys.excepthook(*sys.exc_info())

        self.run = run_with_except_hook
    threading.Thread.__init__ = init


def main(args):
    """
    args is the list of arguments passed
    """

    # arguments handling
    parser = argparse.ArgumentParser(prog="odemis-gui",
                                     description=odemis.__fullname__)

    # nargs="?" to allow to pass just -f without argument, for the Linux desktop
    # file to work easily.
    parser.add_argument('-f', '--file', dest="file_name", nargs="?", default=None,
                        help="File to display")
    parser.add_argument('--version', dest="version", action='store_true',
                        help="show program's version number and exit")
    parser.add_argument('--standalone', dest="standalone", action='store_true',
                        default=False, help="just display simple interface, "
                        "without trying to connect to the back-end")
    parser.add_argument("--log-level", dest="loglev", metavar="<level>", type=int,
                        default=0, help="set verbosity level (0-2, default = 0)")
    parser.add_argument('--logfile', dest='logfile', help="Location of the GUI log file")

    options = parser.parse_args(args[1:])

    # Cannot use the internal feature, because it doesn't support multiline
    if options.version:
        print (odemis.__fullname__ + " " + odemis.__version__ + "\n" +
               odemis.__copyright__ + "\n" +
               "Licensed under the " + odemis.__license__)
        return 0

    # Set up logging before everything else
    if options.loglev < 0:
        parser.error("log-level must be positive.")
    loglev_names = [logging.WARNING, logging.INFO, logging.DEBUG]
    loglev = loglev_names[min(len(loglev_names) - 1, options.loglev)]
    log.init_logger(loglev, options.logfile)

    if 'linux' in sys.platform:
        # Set WM_CLASS on linux, needed to get connected to the right icon.
        # wxPython doesn't do it, see http://trac.wxwidgets.org/ticket/12778
        try:
            # Also possible via Xlib, but more complicated
            import gtk
            # Without it, it will crash cf. See:
            # https://groups.google.com/forum/#!topic/wxpython-users/KO_hmLxeDKA
            gtk.remove_log_handlers()
            # Must be done before the first window is displayed
            name = odemis.__shortname__
            if options.standalone:
                name += "-standalone"
            gtk.gdk.set_program_class(name)
        except Exception:
            logging.info("Failed to set WM_CLASS")

    # Create application
    app = OdemisGUIApp(standalone=options.standalone, file_name=options.file_name)

    # Change exception hook so unexpected exception
    # get caught by the logger
    backup_excepthook, sys.excepthook = sys.excepthook, app.excepthook

    # Start the application
    app.MainLoop()
    app.Destroy()

    sys.excepthook = backup_excepthook

if __name__ == '__main__':
    installThreadExcepthook()
    main(sys.argv)
