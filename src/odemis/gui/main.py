#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@author: Rinze de Laat

Copyright © 2012-2014 Rinze de Laat, Éric Piel, Delmic

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

import Pyro4.errors
import argparse
import logging
from odemis import model
from odemis.gui import main_xrc, log
import odemis.gui.conf
from odemis.gui.cont import acquisition
from odemis.gui.model.dye import DyeDatabase
from odemis.gui.util import call_after
from odemis.gui.xmlh import odemis_get_resources
from odemis.util import driver
import os.path
import subprocess
import sys
import threading
import traceback
import wx
from wx.lib.pubsub import pub

import odemis.gui.cont.tabs as tabs
import odemis.gui.img.data as imgdata
import odemis.gui.model as guimodel


class OdemisGUIApp(wx.App):
    """ This is Odemis' main GUI application class
    """

    def __init__(self, standalone=False):
        """
        standalone (boolean): do not try to connect to the backend
        """
        # Replace the standard 'get_resources' with our augmented one, that
        # can handle more control types. See the xhandler package for more info.
        main_xrc.get_resources = odemis_get_resources

        # Declare attributes BEFORE calling the super class constructor
        # because it will call 'OnInit' which uses them.

        # HTTP documentation http server process
        self.http_proc = None

        self.main_data = None
        self.main_frame = None
        self._tab_controller = None
        self._is_standalone = standalone

        if not standalone:
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

    def OnInit(self):
        """ Application initialization, automatically run from the :wx:`App`
        constructor.
        """

        if self._is_standalone:
            microscope = None
        else:
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

        self.main_data = guimodel.MainGUIData(microscope)
        # Load the main frame
        self.main_frame = main_xrc.xrcfr_main(None)

        self.init_gui()

        log.create_gui_logger(self.main_frame.txt_log, self.main_data.debug)
        logging.info("\n\n************  Starting Odemis GUI  ************\n")
        logging.info(wx.version())

        # Application successfully launched
        return True

    def init_gui(self):
        """ This method binds events to menu items and initializes
        GUI controls """

        try:
            # Add frame icon
            ib = wx.IconBundle()
            ib.AddIcon(imgdata.catalog['icon128'].GetIcon())
            self.main_frame.SetIcons(ib)

            # TODO: move to menu controller?
            # Menu events
            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_quit.GetId(),
                        self.on_close_window)

            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_debug.GetId(),
                        self.on_debug_menu)
            # no need for init as we know debug is False at init.
            self.main_data.debug.subscribe(self.on_debug_va)

            gc = odemis.gui.conf.get_general_conf()

            if gc.get_manual(self.main_data.role):
                self.main_frame.menu_item_manual.Enable(True)

            # TODO: rename htmldoc to devmanual and change from
            # "Source code documentation" to "Developper documentation"
            if gc.get_dev_manual():
                self.main_frame.menu_item_htmldoc.Enable(True)

            # TODO:
            # File/Open... Ctrl+O -> same as "select image..." in analysis tab
            # (but can be called from anywhere and will automatically switch to
            # analysis tab if not cancelled)

            # TODO:
            # View/Play stream F6 -> toggle menu that play/pause the current
            # stream (defaulting to optical stream in SECOM)
            # Disabled if current stream is None or is StaticStream

            # TODO:
            # View/Auto brightness/contrast F9 -> toggle menu that enable/
            # disable the auto BC of the current stream. Disabled if current
            # stream is None or has no .AutoBC VA.

            # TODO:
            # View/Auto focus F10 -> run auto focus on the current stream
            # Disabled if the current stream is None

            # Note: "snapshot" menu is handled by acquisition controller
            # TODO: change snapshot shortcuts to Ctrl+S and Shift+Ctrl+S

            # TODO: re-organise the Help menu:
            # * Report a bug... (Opens a mail client to send an email to us?)

            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_manual.GetId(),
                        self.on_manual)

            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_htmldoc.GetId(),
                        self.on_dev_manual)

            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_inspect.GetId(),
                        self.on_inspect)

            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_about.GetId(),
                        self.on_about)

            # TODO: Display "Esc" as accelerator in the menu (wxPython doesn't
            # seem to like it)
            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_halt.GetId(),
                        self.on_stop_axes)

            # The escape accelerator has to be added manually, because for some
            # reason, the 'ESC' key will not register using XRCED.
            accel_tbl = wx.AcceleratorTable([
                (wx.ACCEL_NORMAL, wx.WXK_ESCAPE,
                 self.main_frame.menu_item_halt.GetId())
            ])

            self.main_frame.SetAcceleratorTable(accel_tbl)

            self.main_frame.Bind(wx.EVT_CLOSE, self.on_close_window)
            self.main_frame.Maximize() # must be done before Show()

            # List of all possible tabs used in Odemis' main GUI
            # microscope role(s), internal name, class, tab btn, tab panel
            # order matters, as the first matching tab is be the default one

            tab_defs = [
                (
                    ("secom",),
                    ("LENS ALIGNMENT",),
                    "secom_align",
                    tabs.LensAlignTab,
                    self.main_frame.btn_tab_secom_align,
                    self.main_frame.pnl_tab_secom_align
                ),
                (
                    ("secom", "sem", "optical"),
                    ("STREAMS", "STREAMS", "STREAMS"),
                    "secom_live",
                    tabs.SecomStreamsTab,
                    self.main_frame.btn_tab_secom_streams,
                    self.main_frame.pnl_tab_secom_streams
                ),
                (
                    ("sparc",),
                    ("MIRROR ALIGNMENT",),
                    "sparc_align",
                    tabs.MirrorAlignTab,
                    self.main_frame.btn_tab_sparc_align,
                    self.main_frame.pnl_tab_sparc_align
                ),
                (
                    ("sparc",),
                    ("ACQUISITION",),
                    "sparc_acqui",
                    tabs.SparcAcquisitionTab,
                    self.main_frame.btn_tab_sparc_acqui,
                    self.main_frame.pnl_tab_sparc_acqui
                ),
                (
                    (None, "secom", "sem", "optical", "sparc"),
                    ("GALLERY", "GALLERY", "GALLERY", "GALLERY", "ANALYSIS"),
                    "analysis",
                    tabs.AnalysisTab,
                    self.main_frame.btn_tab_inspection,
                    self.main_frame.pnl_tab_inspection),
            ]

            # Create the main tab controller and store a global reference
            # in the odemis.gui.cont package
            tc = tabs.TabBarController(
                            tab_defs,
                            self.main_frame,
                            self.main_data)
            self._tab_controller = tc

            # To handle "Save snapshot" menu
            self._snapshot_controller = acquisition.SnapshotController(
                                                        self.main_data,
                                                        self.main_frame)

            # making it very late seems to make it smoother
            wx.CallAfter(self.main_frame.Show)
            logging.debug("Frame will be displayed soon")
        except Exception:  #pylint: disable=W0703
            self.excepthook(*sys.exc_info())
            # Re-raise the exception, so the program will exit. If this is not
            # done and exception will prevent the GUI from being shown, while
            # the program keeps running in the background.
            raise


    def init_config(self):
        """ Initialize GUI configuration """
        # TODO: Process GUI configuration here
        pass

    def _module_path(self):
        encoding = sys.getfilesystemencoding()
        return os.path.dirname(unicode(__file__, encoding))

    def on_stop_axes(self, evt):
        if self.main_data:
            self.main_data.stopMotion()
        else:
            evt.Skip()

    def on_about(self, evt):

        info = wx.AboutDialogInfo()
        info.SetIcon(imgdata.catalog['icon128'].GetIcon())
        info.Name = odemis.__shortname__
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
            info.Licence += ("""
The dye database is provided as-is, from the Fluorobase consortium.
The Fluorobase consortium provides this data and software in good faith, but
akes no warranty, expressed or implied, nor assumes any legal liability or
responsibility for any purpose for which they are used. For further information
see http://www.fluorophores.org/disclaimer/.
""")
        wx.AboutBox(info)

    def on_manual(self, evt):
        gc = odemis.gui.conf.get_general_conf()
        subprocess.Popen(['xdg-open', gc.get_manual(self.main_data.role)])

    def on_dev_manual(self, evt):
        gc = odemis.gui.conf.get_general_conf()
        subprocess.Popen(['xdg-open', gc.get_dev_manual()])

    def on_inspect(self, evt):
        from wx.lib.inspection import InspectionTool
        InspectionTool().Show()
#
#     def on_htmldoc(self, evt):
#         """ Launch Python's SimpleHTTPServer in a separate process and have it
#         serve the source code documentation as created by Sphinx
#         """
#         self.http_proc = subprocess.Popen(
#             ["python", "-m", "SimpleHTTPServer"],
#             stderr=subprocess.STDOUT,
#             stdout=subprocess.PIPE,
#             cwd=os.path.dirname(odemis.gui.conf.get_general_conf().html_dev_doc))
#
#         import webbrowser
#         webbrowser.open('http://localhost:8000')

    def on_debug_menu(self, evt):
        """ Update the debug VA according to the menu
        """
        self.main_data.debug.value = self.main_frame.menu_item_debug.IsChecked()

    @call_after
    def on_debug_va(self, enabled):
        """ This method (un)sets the application into debug mode, setting the
        log level and opening the log panel. """
        self.main_frame.menu_item_debug.Check(enabled)
        self.main_frame.pnl_log.Show(enabled)
        self.main_frame.Layout()

    def on_close_window(self, evt=None): #pylint: disable=W0613
        """ This method cleans up and closes the Odemis GUI. """
        logging.info("Exiting Odemis")

        if self.main_data.is_acquiring.value:
            msg = ("Acquisition in progress!\n\n"
                   "Please cancel the current acquistion operation before exiting Odemis." )
            dlg = wx.MessageDialog(self.main_frame, msg, "Exit", wx.OK | wx.ICON_STOP)
            # if dlg.ShowModal() == wx.ID_NO:
            dlg.ShowModal()
            dlg.Destroy() # frame
            return

        try:
            # Put cleanup actions here (like disconnect from odemisd)

            pub.unsubAll()

            # Stop live view
            try:
                self.main_data.opticalState.value = guimodel.STATE_OFF
            except AttributeError:
                pass # just no such microscope present
            try:
                self.main_data.emState.value = guimodel.STATE_OFF
            except AttributeError:
                pass

            # let all the tabs know we are stopping
            self._tab_controller.terminate()

            if self.http_proc:
                self.http_proc.terminate()  #pylint: disable=E1101
        except Exception:
            logging.exception("Error during GUI shutdown")

        try:
            log.stop_gui_logger()
        except Exception:
            logging.exception("Error stopping GUI logging")

        self.main_frame.Destroy()

    def excepthook(self, etype, value, trace): #pylint: disable=W0622
        """ Method to intercept unexpected errors that are not caught
        anywhere else and redirects them to the logger.
        Note that exceptions caught and logged will appear in the text pane,
        but not cause it to pop-up (as this method will not be called).
        """
        # in case of error here, don't call again, it'd create infinite recursion
        if sys and traceback:
            sys.excepthook = sys.__excepthook__

            try:
                exc = traceback.format_exception(type, value, trace)
                logging.error("".join(exc))

                # When an exception occurs, automatically got to debug mode.
                if not isinstance(value, NotImplementedError):
                    try:
                        self.main_data.debug.value = True
                    except:
                        pass
            finally:
                # put us back
                sys.excepthook = self.excepthook
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
            except:
                sys.excepthook(*sys.exc_info())
        self.run = run_with_except_hook
    threading.Thread.__init__ = init

def main(args):
    """
    args is the list of arguments passed
    """

    # arguments handling
    parser = argparse.ArgumentParser(prog="odemis-cli",
                                     description=odemis.__fullname__)

    parser.add_argument('--version', dest="version", action='store_true',
                        help="show program's version number and exit")
    parser.add_argument('--standalone', dest="standalone", action='store_true',
                        default=False, help="just display simple interface, "
                        "without trying to connect to the back-end")
    parser.add_argument("--log-level", dest="loglev", metavar="<level>", type=int,
                        default=0, help="set verbosity level (0-2, default = 0)")

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
    log.init_logger(loglev)

    if 'linux' in sys.platform:
        # Set WM_CLASS on linux, needed to get connected to the right icon.
        # wxPython doesn't do it, see http://trac.wxwidgets.org/ticket/12778
        try:
            # Also possible via Xlib, but more complicated
            import gtk
            # without it, it will crash cf https://groups.google.com/forum/#!topic/wxpython-users/KO_hmLxeDKA
            gtk.remove_log_handlers()
            # Must be done before the first window is displayed
            name = "Odemis"
            if options.standalone:
                name += "-standalone"
            gtk.gdk.set_program_class(name)
        except Exception:
            logging.info("Failed to set WM_CLASS")

    # Create application
    app = OdemisGUIApp(standalone=options.standalone)
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
