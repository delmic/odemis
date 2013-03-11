#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@author: Rinze de Laat

Copyright © 2012 Rinze de Laat, Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 2 of the License, or (at your option) any later
version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

from odemis import __version__, model
from odemis.gui import main_xrc, instrmodel, log
from odemis.gui.conf import get_general_conf
from odemis.gui.cont import set_main_tab_controller, get_main_tab_controller
from odemis.gui.model.dye import DyeDatabase
from odemis.gui.model.img import InstrumentalImage
from odemis.gui.model.stream import StaticSEMStream
from odemis.gui.xmlh import odemis_get_resources
import Pyro4.errors
import logging
import odemis.gui.cont.tabs as tabs
import os.path
import scipy.io
import sys
import threading
import traceback
import wx
from odemis.model._dataflow import MD_PIXEL_SIZE, MD_POS



class OdemisGUIApp(wx.App):
    """ This is Odemis' main GUI application class
    """

    def __init__(self):
        # Replace the standard 'get_resources' with our augmented one, that
        # can handle more control types. See the xhandler package for more info.
        main_xrc.get_resources = odemis_get_resources

        # Declare attributes BEFORE calling the super class constructor
        # because it will call 'OnInit' which uses them.

        # Startup Dialog frame
        self.dlg_startup = None

        # HTTP documentation http server process
        self.http_proc = None

        self.microscope = None
        self.interface_model = None
        self.main_frame = None

        # Output catcher using a helper class
        wx.App.outputWindowClass = OdemisOutputWindow

        # Constructor of the parent class
        # ONLY CALL IT AT THE END OF :py:method:`__init__` BECAUSE OnInit will
        # be called
        # and it needs the attributes defined in this constructor!
        wx.App.__init__(self, redirect=True)

        # TODO: need to set WM_CLASS to a better value than "main.py". For now
        # almost all wxPython windows get agglomerated together and Odemis is
        # named "FirstStep" sometimes.
        # Not clear whether wxPython supports it. http://trac.wxwidgets.org/ticket/12778
        # Maybe just change the name of this module to something more unique? (eg, odemis.py)

    def OnInit(self):
        """ Application initialization, automatically run from the :wx:`App`
        constructor.
        """

        self.microscope = None
        self.interface_model = None

        try:
            self.microscope = model.getMicroscope()
            self.interface_model = instrmodel.MicroscopeModel(self.microscope)
        except (IOError, Pyro4.errors.CommunicationError), e:
            logging.exception("Failed to connect to back-end")
            msg = ("The Odemis GUI could not connect to the Odemis back-end:\n\n"
                   "{0}\n\n"
                   "Launch user interface anyway?").format(e)

            answer = wx.MessageBox(msg,
                                   "Connection error",
                                    style=wx.YES|wx.NO|wx.ICON_ERROR)
            if answer == wx.NO:
                sys.exit(1)

        # Load the main frame
        self.main_frame = main_xrc.xrcfr_main(None)

        #self.main_frame.Bind(wx.EVT_CHAR, self.on_key)

        log.create_gui_logger(self.main_frame.txt_log)
        logging.info("***********************************************")
        logging.info("************  Starting Odemis GUI  ************")
        logging.info("***********************************************")

        self.init_gui()

        # Application successfully launched
        return True

    def init_gui(self):
        """ This method binds events to menu items and initializes
        GUI controls """

        try:
            # Add frame icon
            ib = wx.IconBundle()
            ib.AddIconFromFile(os.path.join(self._module_path(),
                                            "img/icon128.png"),
                                            wx.BITMAP_TYPE_ANY)
            self.main_frame.SetIcons(ib)

            #logging.debug("Setting frame size to %sx%s", w, h)

            #self.main_frame.SetSize((w, h))
            #self.main_frame.SetPosition((0, 0))


            # Menu events

            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_quit.GetId(),
                        self.on_close_window)

            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_debug.GetId(),
                        self.on_debug)

            gc = get_general_conf()

            if os.path.exists(gc.html_dev_doc):
                self.main_frame.menu_item_htmldoc.Enable(True)

            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_htmldoc.GetId(),
                        self.on_htmldoc)

            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_inspect.GetId(),
                        self.on_inspect)

            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_about.GetId(),
                        self.on_about)

            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_halt.GetId(),
                        self.on_stop_axes)

            if self.interface_model.microscope.role == "secom":
                # TODO: only activate if we are in the live view tab
                wx.EVT_MENU(self.main_frame,
                            self.main_frame.menu_item_load1.GetId(),
                            self.on_load_example_secom1)

                wx.EVT_MENU(self.main_frame,
                            self.main_frame.menu_item_load2.GetId(),
                            self.on_load_example_secom2)
            elif self.interface_model.microscope.role == "sparc":
                # TODO only activate if we are in the analysis tab? Or automatically switch?
                wx.EVT_MENU(self.main_frame,
                            self.main_frame.menu_item_load1.GetId(),
                            self.on_load_example_sparc1)
                self.main_frame.menu_item_load2.Enable(False)
            else:
                self.main_frame.menu_item_load1.Enable(False)
                self.main_frame.menu_item_load2.Enable(False)


            # The escape accelerator has to be added manually, because for some
            # reason, the 'ESC' key will not register using XRCED.
            accel_tbl = wx.AcceleratorTable([
                (wx.ACCEL_NORMAL, wx.WXK_ESCAPE,
                 self.main_frame.menu_item_halt.GetId())
            ])

            self.main_frame.SetAcceleratorTable(accel_tbl)

            self.main_frame.Bind(wx.EVT_CLOSE, self.on_close_window)
            self.main_frame.Maximize()
            self.main_frame.Show()


            # List of all possible tabs used in Odemis' main GUI
            tab_list = [tabs.SecomStreamsTab(
                            "secom",
                            "secom_live",
                            self.main_frame.btn_tab_secom_streams,
                            self.main_frame.pnl_tab_secom_streams,
                            self.main_frame,
                            self.interface_model),
                        tabs.Tab(
                            "secom",
                            "secom_gallery",
                            self.main_frame.btn_tab_secom_gallery,
                            self.main_frame.pnl_tab_secom_gallery),
                        tabs.SparcAcquisitionTab(
                            "sparc",
                            "sparc_acqui",
                            self.main_frame.btn_tab_sparc_acqui,
                            self.main_frame.pnl_tab_sparc_acqui,
                            self.main_frame,
                            self.interface_model),
                        tabs.Tab(
                            "sparc",
                            "sparc_analysis",
                            self.main_frame.btn_tab_sparc_analysis,
                            self.main_frame.pnl_tab_sparc_analysis),
                        ]

            # Create the main tab controller and store a global reference
            # in the odemis.gui.cont package
            set_main_tab_controller(tabs.TabBarController(tab_list,
                                                          self.main_frame,
                                                          self.interface_model))

            #self.settings_controller = SettingsBarController(self.interface_model,
            #                                           self.main_frame)

            # Order matters!
            # First we create the views, then the streams
            # self.view_controller = ViewController(self.interface_model,
            #                                           self.main_frame)
            # self.stream_controller = StreamController(self.interface_model,
            #                                           self.main_frame.pnl_secom_streams)

            # self.view_selector = ViewSelector(self.interface_model,
            #                                   self.main_frame)

            # self.acquisition_controller = AcquisitionController(self.interface_model,
            #                                                     self.main_frame)

            # self.microscope_controller = MicroscopeController(self.interface_model,
            #                                                   self.main_frame)

        except Exception:  #pylint: disable=W0703
            self.excepthook(*sys.exc_info())
            #raise


    def init_config(self):
        """ Initialize GUI configuration """
        # TODO: Process GUI configuration here
        pass

    def _module_path(self):
        encoding = sys.getfilesystemencoding()
        return os.path.dirname(unicode(__file__, encoding))

    def on_load_example_secom1(self, e):
        """ Open the two files for example """
        try:
            pos = self.interface_model.focussedView.value.view_pos.value #pylint: disable=E1101
            name1 = os.path.join(os.path.dirname(__file__),
                                 "img/example/1-optical-rot7.png")
            im1 = InstrumentalImage(wx.Image(name1), 7.14286e-7, pos)

            pos = (pos[0] + 2e-6, pos[1] - 1e-5)
            name2 = os.path.join(os.path.dirname(__file__),
                                 "img/example/1-sem-bse.png")
            im2 = InstrumentalImage(wx.Image(name2), 4.54545e-7, pos)

            mtc = get_main_tab_controller()
            stream_controller = mtc['secom_live'].stream_controller

            stream_controller.addStatic("Fluorescence", im1)
            stream_controller.addStatic("Secondary electrons", im2,
                                        cls=StaticSEMStream)
        except e:
            logging.exception("Failed to load example")

    def on_load_example_secom2(self, e):
        """ Open the two files for example """
        try:
            pos = self.interface_model.focussedView.value.view_pos.value #pylint: disable=E1101
            name2 = os.path.join(os.path.dirname(__file__),
                                 "img/example/3-sem.png")
            im2 = InstrumentalImage(wx.Image(name2), 2.5e-07, pos)

            pos = (pos[0] + 5.5e-06, pos[1] + 1e-6)
            name1 = os.path.join(os.path.dirname(__file__),
                                 "img/example/3-optical.png")
            im1 = InstrumentalImage(wx.Image(name1), 1.34e-07, pos)

            mtc = get_main_tab_controller()
            stream_controller = mtc['secom_live'].stream_controller

            stream_controller.addStatic("Fluorescence", im1)
            stream_controller.addStatic("Secondary electrons", im2,
                                        cls=StaticSEMStream)
        except e:
            logging.exception("Failed to load example")

    def on_load_example_sparc1(self, e):
        """ Open a SEM view and spectrum cube for example
            Must be in the analysis tab of the Sparc
        """
        # It uses raw data, not images
        try:
            name1 = os.path.join(os.path.dirname(__file__),
                                 "img/example/s1-sem-bse.mat")
            md = {model.MD_PIXEL_SIZE: (178e-9, 178e-9),
                  model.MD_POS: (0,0)}
            semdata = model.DataArray(scipy.io.loadmat(name1)["sem"], md)

            name2 = os.path.join(os.path.dirname(__file__),
                                 "img/example/s1-spectrum.mat")
            md = {model.MD_PIXEL_SIZE: (178e-9, 178e-9),
                  model.MD_POS: (0,0),
                  # 335px : 409nm -> 695 nm (about linear)
                  model.MD_WL_POLYNOMIAL: [552e-9, 0.85373e-9] 
                  }
            specdata = model.DataArray(scipy.io.loadmat(name2)["spectraldat"], md)

            mtc = get_main_tab_controller()
            stream_controller = mtc['sparc_analysis'].stream_controller

            stream_controller.addStatic("Secondary electrons", semdata,
                                        cls=StaticSEMStream)
            stream_controller.addStatic("Spectrogram", specdata)
        except e:
            logging.exception("Failed to load example")


    def goto_debug_mode(self):
        """ This method sets the application into debug mode, setting the
        log level and opening the log panel. """
        self.main_frame.menu_item_debug.Check()
        self.on_debug()

    def on_timer(self, event): #pylint: disable=W0613
        """ Timer stuff """
        pass

    def on_stop_axes(self, evt):
        if self.interface_model:
            self.interface_model.stopMotion()
        else:
            evt.Skip()

    def on_about(self, evt):

        info = wx.AboutDialogInfo()
        info.SetIcon(wx.Icon(os.path.join(self._module_path(), "img/icon128.png"),
                             wx.BITMAP_TYPE_PNG))
        info.Name = __version__.shortname
        info.Version = __version__.version
        info.Description = __version__.name
        info.Copyright = __version__.copyright
        info.WebSite = ("http://delmic.com", "delmic.com")
        info.Licence = __version__.license_summary
        info.Developers = ["Éric Piel", "Rinze de Laat"]
        # info.DocWriter = '???'
        # info.Artist = '???'
        # info.Translator = '???'

        if DyeDatabase:
            info.Developers += ["", "Dye database from http://fluorophores.org"]
            info.Licence += ("""
The dye database is provided as-is, from the Fluorobase consortium.
The Fluorobase consortium provide this data and software in good faith, but make
no warranty, expressed or implied, nor assume any legal liability or
responsibility for any purpose for which they are used. For further information
see http://www.fluorophores.org/disclaimer/.
""")
        wx.AboutBox(info)

    def on_inspect(self, evt):
        from wx.lib.inspection import InspectionTool
        InspectionTool().Show()

    def on_htmldoc(self, evt):
        import subprocess
        self.http_proc = subprocess.Popen(
            ["python", "-m", "SimpleHTTPServer"],
            stderr=subprocess.STDOUT,
            stdout=subprocess.PIPE,
            cwd=os.path.dirname(get_general_conf().html_dev_doc))

        import webbrowser
        webbrowser.open('http://localhost:8000')


        #subprocess.call(('xdg-open', HTML_DOC))

    def on_debug(self, evt=None): #pylint: disable=W0613
        """ Show or hides the log text field according to the debug menu item.
        """
        self.main_frame.pnl_log.Show(self.main_frame.menu_item_debug.IsChecked())
        self.main_frame.Layout()

    def on_close_window(self, evt=None): #pylint: disable=W0613
        """ This method cleans up and closes the Odemis GUI. """

        logging.info("Exiting Odemis")

        if self.interface_model:
            # Put cleanup actions here (like disconnect from odemisd)
            self.interface_model.opticalState.value = instrmodel.STATE_OFF
            self.interface_model.emState.value = instrmodel.STATE_OFF

        #self.dlg_startup.Destroy()
        self.main_frame.Destroy()
        if self.http_proc:
            self.http_proc.terminate()  #pylint: disable=E1101
        sys.exit(0)

    def excepthook(self, type, value, trace): #pylint: disable=W0622
        """ Method to intercept unexpected errors that are not caught
        anywhere else and redirects them to the logger. """
        # in case of error here, don't call again, it'd create infinite recurssion
        sys.excepthook = sys.__excepthook__

        try:
            exc = traceback.format_exception(type, value, trace)
            logging.error("".join(exc))

            # When an exception occurs, automatically got to debug mode.
            if not isinstance(value, NotImplementedError):
                self.goto_debug_mode()
        finally:
            # put us back
            sys.excepthook = self.excepthook

class OdemisOutputWindow(object):
    """ Helper class which allows ``wx`` to display uncaught
        messages in the way defined by the :py:mod:`log` module. """
    def __init__(self):
        pass

    def write(self, txt):
        if txt.strip() != "":
            logging.error("[CAP] %s", txt.strip())

def installThreadExcepthook():
    """ Workaround for sys.excepthook thread bug
    http://spyced.blogspot.com/2007/06/workaround-for-sysexcepthook-bug.html

    Call once from ``__main__`` before creating any threads.
    If using psyco, call

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

def main():
    log.init_logger()

    # Create application
    app = OdemisGUIApp()
    # Change exception hook so unexpected exception
    # get caught by the logger
    backup_excepthook, sys.excepthook = sys.excepthook, app.excepthook

    # Start the application
    app.MainLoop()
    app.Destroy()

    sys.excepthook = backup_excepthook

if __name__ == '__main__':
    installThreadExcepthook()
    main()
