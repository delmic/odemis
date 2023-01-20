#!/usr/bin/env python3
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

from odemis.gui.util import wx_adapter
import Pyro4.errors
import argparse
import logging
from odemis import model, gui
import odemis
from odemis.gui import main_xrc, log, img, plugin
from odemis.gui.cont import acquisition
from odemis.gui.cont.menu import MenuController
from odemis.gui.cont.temperature import TemperatureController
from odemis.gui.util import call_in_wx_main
from odemis.gui.xmlh import odemis_get_resources
import sys
import threading
import traceback
import wx
from wx.lib.pubsub import pub
import warnings

import odemis.gui.cont.tabs as tabs
import odemis.gui.model as guimodel


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
        self._temperature_controller = None
        self._menu_controller = None
        self.plugins = []  # List of instances of plugin.Plugins

        # User input devices
        self.dev_powermate = None

        l = logging.getLogger()
        self.log_level = l.getEffectiveLevel()

        # Output catcher using a helper class
        wx.App.outputWindowClass = OdemisOutputWindow

        # Constructor of the parent class
        # ONLY CALL IT AT THE END OF :py:method:`__init__` BECAUSE OnInit will
        # be called
        # and it needs the attributes defined in this constructor!
        wx.App.__init__(self, redirect=True)

        if file_name:
            tab = self.main_data.getTabByName('analysis')
            self.main_data.tab.value = tab
            wx.CallLater(500, tab.load_data, file_name)

    def OnInit(self):
        """ Initialize the GUI

        This method is automatically called from the :wx:`App` constructor

        """

        gui.legend_logo = "legend_logo_delmic.png"
        if self._is_standalone:
            microscope = None
            gui.icon = img.getIcon("icon/ico_gui_viewer_256.png")
            gui.name = odemis.__shortname__ + " Viewer"

            if "delphi" == self._is_standalone:
                gui.logo = img.getBitmap("logo_delphi.png")
                gui.legend_logo = "legend_logo_delphi.png"
        else:
            gui.icon = img.getIcon("icon/ico_gui_full_256.png")
            gui.name = odemis.__shortname__
            try:
                microscope = model.getMicroscope()
            except (IOError, Pyro4.errors.CommunicationError) as e:
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
            else:
                if microscope.role == "delphi":
                    gui.logo = img.getBitmap("logo_delphi.png")
                    gui.legend_logo = "legend_logo_delphi.png"

        # TODO: if microscope.ghost is not empty => wait and/or display a special
        # "hardware status" tab.

        if microscope and microscope.role == "mbsem":
            self.main_data = guimodel.FastEMMainGUIData(microscope)
        else:
            self.main_data = guimodel.MainGUIData(microscope)
        # Load the main frame
        self.main_frame = main_xrc.xrcfr_main(None)

        self.init_gui()

        try:
            from odemis.gui.dev.powermate import Powermate
            self.dev_powermate = Powermate(self.main_data)
        except (LookupError, NotImplementedError) as ex:
            logging.debug("Not using Powermate: %s", ex)
        except Exception:
            logging.exception("Failed to load Powermate support")

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

            # IMPORTANT NOTE:
            # As all tab panels are hidden on start-up, the MinSize attribute
            # of the main GUI frame will be set to such a low value that most of
            # the interface will be invisible if the user takes the interface out of
            # 'full screen' view.
            # Also, Gnome's GDK library will start spewing error messages, saying
            # it cannot draw certain images, because the dimensions are 0x0.
            self.main_frame.SetMinSize((1000, 550))
            self.main_frame.Maximize()  # must be done before Show()

            # List of all possible tabs used in Odemis' main GUI
            tab_defs = [
                {
                    # Unique name of the tab
                    "name": "secom_live",
                    # Tab controller for this tab
                    "controller": tabs.SecomStreamsTab,
                    # Tab button for this tab
                    "button": self.main_frame.btn_tab_secom_streams,
                    # Constructor of the tab panel
                    "panel": main_xrc.xrcpnl_tab_secom_streams
                },
                {
                    "name": "cryosecom-localization",
                    "controller": tabs.LocalizationTab,
                    "button": self.main_frame.btn_tab_localization,
                    "panel": main_xrc.xrcpnl_tab_localization
                },
                {
                    "name": "secom_align",
                    "controller": tabs.SecomAlignTab,
                    "button": self.main_frame.btn_tab_align,
                    "panel": main_xrc.xrcpnl_tab_secom_align
                },
                {
                    "name"      : "enzel_align",
                    "controller": tabs.EnzelAlignTab,
                    "button"    : self.main_frame.btn_tab_align_enzel,
                    "panel"     : main_xrc.xrcpnl_tab_enzel_align
                },
                {
                    "name": "mimas_align",
                    "controller": tabs.MimasAlignTab,
                    "button": self.main_frame.btn_tab_align_enzel,  # enzel alignment button is fine
                    "panel": main_xrc.xrcpnl_tab_mimas_align
                },
                {
                    "name": "sparc_align",
                    "controller": tabs.SparcAlignTab,
                    "button": self.main_frame.btn_tab_align,
                    "panel": main_xrc.xrcpnl_tab_sparc_align
                },
                {
                    "name": "sparc2_align",
                    "controller": tabs.Sparc2AlignTab,
                    "button": self.main_frame.btn_tab_align,
                    "panel": main_xrc.xrcpnl_tab_sparc2_align
                },
                {
                    "name": "sparc_acqui",
                    "controller": tabs.SparcAcquisitionTab,
                    "button": self.main_frame.btn_tab_sparc_acqui,
                    "panel": main_xrc.xrcpnl_tab_sparc_acqui
                },
                {
                    "name": "fastem_overview",
                    "controller": tabs.FastEMOverviewTab,
                    "button": self.main_frame.btn_tab_fastem_overview,
                    "panel": main_xrc.xrcpnl_tab_fastem_overview
                },
                {
                    "name": "fastem_acqui",
                    "controller": tabs.FastEMAcquisitionTab,
                    "button": self.main_frame.btn_tab_fastem_acqui,
                    "panel": main_xrc.xrcpnl_tab_fastem_acqui
                },
                {
                    "name": "fastem_chamber",
                    "controller": tabs.FastEMChamberTab,
                    "button": self.main_frame.btn_tab_fastem_chamber,
                    "panel": main_xrc.xrcpnl_tab_fastem_chamber
                },
                {
                    "name": "sparc_chamber",
                    "controller": tabs.ChamberTab,
                    "button": self.main_frame.btn_tab_sparc_chamber,
                    "panel": main_xrc.xrcpnl_tab_sparc_chamber
                },
                {
                    "name": "cryosecom_chamber",
                    "controller": tabs.CryoChamberTab,
                    "button": self.main_frame.btn_tab_cryosecom_chamber,
                    "panel": main_xrc.xrcpnl_tab_cryosecom_chamber
                },
                {
                    "name": "analysis",
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

            self.main_data.debug.subscribe(self.on_debug_va, init=True)
            self.main_data.level.subscribe(self.on_level_va, init=True)
            log.create_gui_logger(self.main_frame.txt_log, self.main_data.debug, self.main_data.level)

            self._menu_controller = MenuController(self.main_data, self.main_frame)
            # Menu events
            self.main_frame.Bind(wx.EVT_MENU, self.on_close_window, id=self.main_frame.menu_item_quit.GetId())

            self.main_frame.Bind(wx.EVT_CLOSE, self.on_close_window)

            # To handle "Save snapshot" menu
            self._snapshot_controller = acquisition.SnapshotController(self.main_data,
                                                                       self.main_frame)

            # Update the logo if a non-default logo is defined
            if gui.logo:
                self.main_frame.logo.SetBitmap(gui.logo)
            # Update legend logo filepath
            self.main_frame.legend_logo = gui.legend_logo

            # Now starts the plugins, after the rest of the GUI is ready
            pfns = plugin.find_plugins()
            for p in pfns:
                pis = plugin.load_plugin(p, self.main_data.microscope, self)
                self.plugins.extend(pis)

            # add temperature controller
            if self.main_data.sample_thermostat:
                self._temperature_controller = TemperatureController(self.main_frame, self.main_data.sample_thermostat)

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
        """
        Sets (or unset) the application into "debug mode", opening the log panel
        """
        self.main_frame.pnl_log.Show(enabled)

        for tab in self.tab_controller.get_tabs():
            if hasattr(tab.panel, 'btn_log'):
                tab.panel.btn_log.Show(not enabled)

        # Reset highest log level
        self.main_data.level.value = 0
        self.main_frame.Layout()

    @call_in_wx_main
    def on_level_va(self, log_level):
        """ Set the log button color """
        # As this function is called in the main thread, it might not be called
        # in the called order. Therefore, the log level might not be up-to-date.
        # => Read the log level from the model, which contains the latest value.
        log_level = self.main_data.level.value

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

        # Check if there's any action to do before tab termination
        # Do not terminate if returned False
        if not self.tab_controller.query_terminate():
            return

        for p in self.plugins:
            try:
                p.terminate()
            except Exception:
                logging.exception("Failed to end the plugin properly")
        self.plugins = []

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
            print("%s: %s\n%s" % (etype, value, trace))

    def showwarning(self, message, category, filename, lineno, file=None, line=None):
        """
        Called when a warning is generated.
        The default behaviour is to write it on stderr, which would lead to it
        being shown as an error.
        """
        warn = warnings.formatwarning(message, category, filename, lineno, line)
        logging.warning(warn)


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

    # HACK: odemis.model sets it at 16, because some hardware components needs
    # a lot of simultaneous connections. One the client side, there is almost
    # no server created, so it's useless... excepted for the callback of
    # RemoteFutures (corresponding to actuator moves). But even in such case,
    # 1 connection should be enough in every case. To be safe, we set it to 2.
    # That helps to reduce memory usage.
    Pyro4.config.THREADPOOL_MINTHREADS = 2

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
    parser.add_argument('--log-target', dest='logtarget',
                        help="Location of the GUI log file")

    options = parser.parse_args(args[1:])

    # Cannot use the internal feature, because it doesn't support multiline
    if options.version:
        print(odemis.__fullname__ + " " + odemis.__version__ + "\n" +
              odemis.__copyright__ + "\n" +
              "Licensed under the " + odemis.__license__)
        return 0

    # Set up logging before everything else
    if options.loglev < 0:
        parser.error("log-level must be positive.")
    loglev_names = [logging.WARNING, logging.INFO, logging.DEBUG]
    loglev = loglev_names[min(len(loglev_names) - 1, options.loglev)]
    log.init_logger(loglev, options.logtarget)

    if 'linux' in sys.platform:
        # Set WM_CLASS on linux, needed to get connected to the right icon.
        # wxPython doesn't do it, see http://trac.wxwidgets.org/ticket/12778
        try:
            import gi
            from gi.repository import GLib

            # Must be done before the first window is displayed
            name = odemis.__shortname__
            if options.standalone:
                name += "-standalone"
            GLib.set_prgname(name)
        except Exception:
            logging.info("Failed to set WM_CLASS")

    logging.info("\n\n************  Starting Odemis GUI  ************\n")
    logging.info("Odemis GUI v%s (from %s) using Python %d.%d",
                 odemis.__version__, __file__, sys.version_info[0], sys.version_info[1])
    logging.info("wxPython v%s", wx.version())

    if wx.MAJOR_VERSION <= 3:
        logging.error("wxPython 3 is not supported anymore")
        app = wx.App()
        wx.MessageBox("Your system is using an old version of wxPython (%s) which is not supported anymore.\n"
                      "Please update with \"sudo apt install python3-wxgtk4.0\"." % (wx.version(),),
                      "Library needs to be updated",
                      style=wx.OK | wx.ICON_ERROR)
        return 129

    # Create application
    app = OdemisGUIApp(standalone=options.standalone, file_name=options.file_name)

    # Change exception hook so unexpected exception get caught by the logger,
    # and warnings are shown as warnings in the log.
    backup_excepthook, sys.excepthook = sys.excepthook, app.excepthook
    warnings.showwarning = app.showwarning

    # Start the application
    app.MainLoop()
    app.Destroy()

    sys.excepthook = backup_excepthook

if __name__ == '__main__':
    installThreadExcepthook()
    main(sys.argv)
