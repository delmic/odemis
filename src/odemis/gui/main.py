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
from odemis.gui import main_xrc, instrmodel
from odemis.gui.controler.acquisition import AcquisitionController
from odemis.gui.controler.settingspanel import SettingsSideBar
from odemis.gui.controler.stream import StreamController
from odemis.gui.controler.tabs import TabBar
from odemis.gui.controler.viewpanel import ViewSelector
from odemis.gui.controler.views import ViewController
from odemis.gui.instrmodel import OpticalBackendConnected, InstrumentalImage
from odemis.gui.log import log, create_gui_logger
from odemis.gui.xmlh import odemis_get_resources
import Pyro4.errors
import logging
import os.path
import sys
import threading
import traceback
import wx
#from odemis.gui.controler.viewpanel import ViewSideBar






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

        try:
            self.microscope = model.getMicroscope()
            self.interface_model = instrmodel.MicroscopeGUI(self.microscope)
            self.secom_model = OpticalBackendConnected(self.microscope) # XXX remove
        except (IOError, Pyro4.errors.CommunicationError), e:
            log.exception("oei")
            msg = ("The Odemis GUI could not connect to the Odemis Daemon:\n\n"
                   "{0}\n\n"
                   "Launch GUI anyway?").format(e)

            answer = wx.MessageBox(msg,
                                   "Connection error",
                                    style=wx.YES|wx.NO|wx.ICON_ERROR)
            if answer == wx.NO:
                sys.exit(1)

        # Load the main frame
        self.main_frame = main_xrc.xrcfr_main(None)

        #self.main_frame.Bind(wx.EVT_CHAR, self.on_key)

        self.init_logger()
        self.init_gui()

        # Application successfully launched
        return True

    def init_logger(self):
        """ Initialize logging functionality """
        create_gui_logger(self.main_frame.txt_log)
        log.info("Starting Odemis GUI")


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

            #log.debug("Setting frame size to %sx%s", w, h)

            #self.main_frame.SetSize((w, h))
            #self.main_frame.SetPosition((0, 0))


            self.tabs = TabBar(self.main_frame,
                               [(self.main_frame.tab_btn_live,
                                 self.main_frame.pnl_tab_live),
                                (self.main_frame.tab_btn_gallery,
                                 self.main_frame.pnl_tab_gallery),
                               ])

            # FIXME (eric): why is this commented? If not needed => remove
            # Do a final layout of the fold panel bar
            #wx.CallAfter(self.main_frame.fpb_settings.FitBar)


            # Menu events

            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_quit.GetId(),
                        self.on_close_window)

            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_debug.GetId(),
                        self.on_debug)

            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_inspect.GetId(),
                        self.on_inspect)

            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_about.GetId(),
                        self.on_about)

            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_halt.GetId(),
                        self.on_stop_axes)

            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_load1.GetId(),
                        self.on_load_example1)

            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_load2.GetId(),
                        self.on_load_example2)

            # The escape accelerator has to be added manually, because for some
            # reason, the 'ESC' key will not register using XRCED.
            accel_tbl = wx.AcceleratorTable([
                (wx.ACCEL_NORMAL, wx.WXK_ESCAPE,
                 self.main_frame.menu_item_halt.GetId())
            ])

            self.main_frame.SetAcceleratorTable(accel_tbl)


            # Keep track of focus
            self.scope_panels = [self.main_frame.pnl_view_tl,
                                 self.main_frame.pnl_view_tr,
                                 self.main_frame.pnl_view_bl,
                                 self.main_frame.pnl_view_br]

            for scope_panel in self.scope_panels:
                scope_panel.Bind(wx.EVT_CHILD_FOCUS, self.OnScopePanelFocus)
            # to ensure at least one panel has the focus
            self.scope_panels[0].SetFocus(True)

            self.main_frame.Bind(wx.EVT_CLOSE, self.on_close_window)

            self.main_frame.Maximize()
            self.main_frame.Show()
            #self.main_frame.Raise()
            #self.main_frame.Refresh()
#
#            if log.level == logging.DEBUG:
#                self.goto_debug_mode()


            self.settings_controler = SettingsSideBar(self.main_frame,
                                                      self.microscope) # TODO use interface_model

            #print_microscope_tree(microscope)

            # Order matters!
            # First we create the views, then the streams
            self.view_controller = ViewController(self.interface_model,
                                                      self.main_frame)
            self.stream_controller = StreamController(self.interface_model,
                                                      self.main_frame.pnl_stream)
            
            self.view_selector = ViewSelector(self.interface_model,
                                              self.main_frame)
            
            
            self.acquisition_controller = AcquisitionController(self.interface_model,
                                                                self.main_frame)

            # Main on/off buttons => only optical for now
            # FIXME: special _bitmap_ toggle button doesn't seem to generate
            # EVT_TOGGLEBUTTON
            # self.main_frame.btn_toggle_opt.Bind(wx.EVT_TOGGLEBUTTON,
            #                                     self.on_toggle_opt)
            self.main_frame.btn_toggle_opt.Bind(wx.EVT_BUTTON,
                                                self.on_toggle_opt)

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

    def OnScopePanelFocus(self, evt):
        """ Un-focus all panels
        When the user tries to focus a scope panel, the event will first pass
        through here, so this is where we unfocus all panels, after which
        the event moves on and focusses the desired panel.
        """
        evt.Skip()
        for scope_panel in self.scope_panels:
            scope_panel.SetFocus(False)


    # TODO update to MicroscopeGUI (self.interface_model)
    def on_load_example1(self, e):
        """ Open the two files for example """
        try:
            pos = self.secom_model.stage_pos.value
            name1 = os.path.join(os.path.dirname(__file__),
                                 "1-optical-rot7.png")
            im1 = InstrumentalImage(wx.Image(name1), 7.14286e-7, pos)

            pos = (pos[0] + 2e-6, pos[1] - 1e-5)
            name2 = os.path.join(os.path.dirname(__file__), "1-sem-bse.png")
            im2 = InstrumentalImage(wx.Image(name2), 4.54545e-7, pos)

            self.secom_model.sem_det_image.value = im2
            self.secom_model.optical_det_image.value = im1
        except e:
            log.exception("Failed to load example")

    def on_load_example2(self, e):
        """ Open the two files for example """
        try:
            pos = self.secom_model.stage_pos.value
            name2 = os.path.join(os.path.dirname(__file__), "3-sem.png")
            im2 = InstrumentalImage(wx.Image(name2), 2.5e-07, pos)

            pos = (pos[0] + 5.5e-06, pos[1] + 1e-6)
            name1 = os.path.join(os.path.dirname(__file__), "3-optical.png")
            im1 = InstrumentalImage(wx.Image(name1), 1.34e-07, pos)

            self.secom_model.sem_det_image.value = im2
            self.secom_model.optical_det_image.value = im1
        except e:
            log.exception("Failed to load example")

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

    def on_toggle_opt(self, event):
        if self.interface_model:
            if event.isDown: # if ToggleEvent, could use isChecked()
                self.interface_model.opticalState.value = instrmodel.STATE_ON
            else:
                self.interface_model.opticalState.value = instrmodel.STATE_OFF

    def on_about(self, evt):
        message = ("%s\nVersion %s.\n\n%s.\nLicensed under the %s." %
                   (__version__.name,
                    __version__.version,
                    __version__.copyright,
                    __version__.license))
        dlg = wx.MessageDialog(self.main_frame, message,
                               "About " + __version__.shortname, wx.OK)
        dlg.ShowModal() # blocking
        dlg.Destroy()

    def on_inspect(self, evt):
        from wx.lib.inspection import InspectionTool
        InspectionTool().Show()

    def on_debug(self, evt=None): #pylint: disable=W0613
        """ Show or hides the log text field according to the debug menu item.
        """
        self.main_frame.pnl_log.Show(self.main_frame.menu_item_debug.IsChecked())
        self.main_frame.Layout()

    def on_close_window(self, evt=None): #pylint: disable=W0613
        """ This method cleans up and closes the Odemis GUI. """

        logging.info("Exiting Odemis")

        # Put cleanup actions here (like disconnect from odemisd)
        self.secom_model.turnOff()

        #self.dlg_startup.Destroy()
        self.main_frame.Destroy()
        sys.exit(0)

    def excepthook(self, type, value, trace): #pylint: disable=W0622
        """ Method to intercept unexpected errors that are not caught
        anywhere else and redirects them to the logger. """
        exc = traceback.format_exception(type, value, trace)
        log.error("".join(exc))

        # When an exception occurs, automatically got to debug mode.
        if not isinstance(value, NotImplementedError):
            self.goto_debug_mode()

class OdemisOutputWindow(object):
    """ Helper class which allows ``wx`` to display uncaught
        messages in the way defined by the :py:mod:`log` module. """
    def __init__(self):
        pass

    def write(self, txt):
        if txt.strip() != "":
            print_catch_logger = logging.getLogger()
            print_catch_logger.error("[CAP] %s" % txt.strip())

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
