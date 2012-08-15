# -*- coding: utf-8 -*-

"""
"""

import logging
import sys
import threading
import traceback
import os.path

import wx

import Pyro4.errors

import odemis.model
import odemis.gui.main_xrc

from odemis.gui.xmlh import odemis_get_resources
from odemis.gui.log import log, create_gui_logger
from odemis.gui.instrmodel import OpticalBackendConnected

class OdemisGUIApp(wx.App):
    """ This is Odemis' main GUI application class
    """

    def __init__(self):
        # Replace the standard 'get_resources' with our augmented one, that
        # can handle more control types. See the xhandler package for more info.
        odemis.gui.main_xrc.get_resources = odemis_get_resources

        # Declare attributes BEFORE calling the super class constructor
        # because it will call 'OnInit' which uses them.

        # Reference to the main application frame which provides references
        # to screen widgets of interest.
        self.fr_main = None

        # Startup Dialog frame
        self.dlg_startup = None

        # Output catcher using a helper class
        wx.App.outputWindowClass = OdemisOutputWindow

        # Constructor of the parent class
        # ONLY CALL IT AT THE END OF :py:method:`__init__` BECAUSE OnInit will be called
        # and it needs the attributes defined in this constructor!
        wx.App.__init__(self, redirect=False)

    def OnInit(self):
        """ Application initialization, automatically run from the :wx:`App` constructor.
        """

        try:
            self.microscope = odemis.model.getMicroscope()
            self.secom_model = OpticalBackendConnected(self.microscope)
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
        self.fr_main = odemis.gui.main_xrc.xrcfr_main(None)

        self.init_logger()
        self.init_gui()

        # Application successfully launched
        return True


    def init_logger(self):
        """ Initialize logging functionality """
        #create_gui_logger(self.fr_main.txt_log)
        log.info("Starting Odemis GUI version x.xx")


    def init_gui(self):
        """ This method binds events to menu items and initializes
        GUI controls """

        try:
            # Add frame icon
            ib = wx.IconBundle()
            ib.AddIconFromFile(os.path.join(self._module_path(), "img/odemis.ico"), wx.BITMAP_TYPE_ANY)
            self.fr_main.SetIcons(ib)

            _, _, w, h = wx.ClientDisplayRect()

            h -= 28

            log.debug("Setting frame size to %sx%s", w, h)

            self.fr_main.SetSize((w, h))
            self.fr_main.SetPosition((0, 0))

            # Do a final layout of the fold panel bar
            #wx.CallAfter(self.fr_main.fpb_settings.FitBar)

            ##################################################
            # TEST CODE
            ##################################################
            def dodo(evt):
                from odemis.gui.comp.stream import FixedStreamPanelEntry
                fp = FixedStreamPanelEntry(self.fr_main.pnl_stream,
                                           label="First Fixed Stream")
                self.fr_main.pnl_stream.add_stream(fp)

            self.fr_main.btn_aquire.Bind(wx.EVT_BUTTON, dodo)

            from wx.lib.inspection import InspectionTool
            InspectionTool().Show()


            # Menu events

            wx.EVT_MENU(self.fr_main,
                        self.fr_main.menu_item_debug.GetId(),
                        self.on_debug)

            # wx.EVT_MENU(self.fr_main, self.fr_main.menu_item_exit.GetId(), self.on_close_window)
            # wx.EVT_MENU(self.fr_main, self.fr_main.menu_item_debug.GetId(), self.on_debug)
            # wx.EVT_MENU(self.fr_main, self.fr_main.menu_item_error.GetId(), self.on_send_report)
            # wx.EVT_MENU(self.fr_main, self.fr_main.menu_item_activate.GetId(), self.on_activate)
            # wx.EVT_MENU(self.fr_main, self.fr_main.menu_item_update.GetId(), elit.updater.Updater.check_for_update)

            # Keep track of focus
            self.scope_panels = [self.fr_main.pnl_view_tl,
                                 self.fr_main.pnl_view_tr,
                                 self.fr_main.pnl_view_bl,
                                 self.fr_main.pnl_view_br]

            for scope_panel in self.scope_panels:
                scope_panel.Bind(wx.EVT_CHILD_FOCUS, self.OnScopePanelFocus)


            self.fr_main.Bind(wx.EVT_CLOSE, self.on_close_window)

            self.fr_main.Show()
            self.fr_main.Raise()
            self.fr_main.Refresh()

            if log.level == logging.DEBUG:
                self.goto_debug_mode()


        except Exception:
            self.excepthook(*sys.exc_info())
            raise


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


    def goto_debug_mode(self):
        """ This method sets the application into debug mode, setting the
        log level and opening the log panel. """
        self.fr_main.menu_item_debug.Check()
        self.on_debug()

    def on_timer(self, event): #pylint: disable=W0613
        """ Timer stuff """
        pass

    def on_debug(self, evt=None): #pylint: disable=W0613
        """ Show or hides the log text field according to the debug menu item. """
        self.fr_main.txt_log.Show(self.fr_main.menu_item_debug.IsChecked())
        self.fr_main.Layout()

    def on_close_window(self, evt=None): #pylint: disable=W0613
        """ This method cleans up and closes the Odemis GUI. """

        logging.info("Exiting Odemis")

        # Put cleanup actions here (like disconnect from odemisd)

        #self.dlg_startup.Destroy()
        self.fr_main.Destroy()
        sys.exit(0)

    def excepthook(self, type, value, trace): #pylint: disable=W0622
        """ Method to intercept unexpected errors that are not caught
        anywhere else and redirects them to the logger. """
        exc = traceback.format_exception(type, value, trace)

        log.error("".join(exc))

        if not isinstance(value, NotImplementedError):
            # TODO: create custom dialogs for Odemis

            # msg = "Unexpected error!"
            # answer = elit.dialog.error_report_dialog(self.fr_main, msg, 'Onverwachte fout!')
            # if  answer == wx.ID_YES:
            #     try:
            #         print "Sending error report"
            #         self.fr_main.Hide()
            #         elit.util.report_error()
            #         print "Error report sent"
            #     except: #pylint: disable=W0702
            #         logging.exception("Error report failed")

            # sys.exit will only terminate the thread it's called from,
            # so on_close_window is called to make sure everything
            # is cleaned up before exiting.

            #self.on_close_window()
            #sys.exit(1)
            pass

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
    """ `Workaround for sys.excepthook thread bug <http://spyced.blogspot.com/2007/06/workaround-for-sysexcepthook-bug.html>`_

        Call once from ``__main__`` before creating any threads.
        If using psyco, call

        .. code-block:: python

            psyco.cannotcompile(threading.Thread.run)

        since this replaces a new-style class method.
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
