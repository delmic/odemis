# -*- coding: utf-8 -*-
"""
Created on 01 Mar 2016

@author: Éric Piel

Copyright © 2016 Éric Piel, Delmic

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

from functools import partial
from abc import ABCMeta, abstractmethod, abstractproperty
import glob
import imp
import inspect
import logging
from wx.lib.agw.infobar import AutoWrapStaticText

from odemis import util
import odemis
from odemis.gui.comp.buttons import ImageTextButton
from odemis.gui.cont.settings import SettingsController
from odemis.gui.cont.streams import StreamBarController
from odemis.gui.main_xrc import xrcfr_plugin
from odemis.gui.model import MicroscopeView, MicroscopyGUIData
from odemis.gui.util import call_in_wx_main, get_home_folder
from odemis.gui.util.widgets import ProgressiveFutureConnector
from odemis.model import VigilantAttribute, getVAs
import os
import threading
import wx


def find_plugins():
    """
    return (list of str): list of the filenames of the plugins available
    """
    hf = get_home_folder()
    # Order of paths matters, so that the user can override a system plugin
    if os.name == "nt":
        paths = (os.path.join(odemis.__path__[0], u"plugins/"),
                 # There is no official place for putting per-user system
                 # overriding file, so just put with the config file.
                 os.path.join(hf, u".config/odemis/plugins/")
                 )
    else:  # hopefully this is Linux
        paths = (u"/usr/share/odemis/plugins/",
                 u"/usr/share/local/odemis/plugins/",
                 os.path.join(hf, u".local/share/odemis/plugins/"),
                 )

    plugins = {}  # script name -> full path
    for p in paths:
        for fn in glob.glob(p + u"*.py"):
            if os.path.isfile(fn):
                # Discard previous plugin with same name
                sn = os.path.basename(fn)
                plugins[sn] = fn
    return sorted(plugins.values())


def load_plugin(filename, microscope, main_app):
    """
    Load and instantiate each plugin present in a plugin file

    Note: if the plugin fails to load, it will not raise an error, but just return an empty list and
    log the error.

    Args:
        filename (str): path to the python file containing one or more Plugin class
        microscope (Microscope or None): the main back-end component. If the GUI is running as a
            viewer only, then it is None.
        main_app (wx.App): the main GUI component.

    Returns:
        (list of instances of Plugin): each instance of plugin created

    """
    ret = []

    # Load module
    logging.debug("Searching '%s' for Plugins...", filename)
    logger = logging.getLogger()
    prev_loglev = logger.getEffectiveLevel()
    try:
        # Use the name of the script as sub-module of this module
        # eg: aab.py -> odemis.gui.plugin.aab
        dirn, bsn = os.path.split(filename)
        mn, ext = os.path.splitext(bsn)
        if ext == ".pyc":
            pm = imp.load_compiled(__name__ + "." + mn, filename)
        elif ext == ".py":
            pm = imp.load_source(__name__ + "." + mn, filename)
        else:
            raise ValueError("Unsupported extension '%s'" % (ext,))
    except Exception:
        logging.info("Skipping script %s, which failed to load", filename, exc_info=True)
        return ret

    if logger.getEffectiveLevel() != prev_loglev:
        # It's easy to put a line at the top of a script that changes the logging
        # level, but after importing that script, the whole GUI log level would
        # be modified, so put it back.
        logging.info("Resetting logging level that was modified during import")
        logger.setLevel(prev_loglev)

    # For each subclass of Plugin in the module, start it by instantiating it
    found_plugin = False
    for n, pc in inspect.getmembers(pm, inspect.isclass):
        # We only want Plugin subclasses, not even the Plugin class itself
        if not issubclass(pc, Plugin) or pc is Plugin:
            continue

        # Don't try to instantiate abstract classes
        # TODO: the issue with this test is that if the plugin doesn't provide
        # one of the abstract method or property (due to a programming error),
        # it's considered an abstract class
        # if inspect.isabstract(pc):
        #     continue

        logging.debug("Trying to instantiate %s (%s) of '%s' with microscope %s",
                      pc.name, n, filename, microscope)
        found_plugin = True
        try:
            ip = pc(microscope, main_app)
        except Exception:
            logging.warning("Failed to instantiate %s of '%s'", n,
                            filename, exc_info=True)
        else:
            logging.info("Created Plugin %s from '%s'", ip, os.path.basename(filename))
            ret.append(ip)

    if not found_plugin:
        logging.info("Script %s contains no plugin", filename)

    return ret


class Plugin(object):
    """
    This is the root class for Odemis GUI plugins.
    Every plugin must be a subclass of that class.
    When starting, the GUI will look in the following directories for python
    scripts and instantiate all subclasses of odemis.gui.plugin.Plugin:
      /usr/share/odemis/plugins/
      /usr/local/share/odemis/plugins/
      $HOME/.local/share/odemis/plugins/
    """
    __metaclass__ = ABCMeta

    # The following 4 attributes must be overridden
    @abstractproperty
    def name(self):
        return None

    @abstractproperty
    def __version__(self):
        return None

    @abstractproperty
    def __author__(self):
        return None

    @abstractproperty
    def __license__(self):
        return None

    def __str__(self):
        name = self.name
        if name is None:  # For abstract classes
            name = self.__class__.__name__

        if self.__version__:
            v = " v%s" % (self.__version__)
        else:
            v = ""

        # TODO: include filename too?
        return "%s%s" % (name, v)

    @abstractmethod
    def __init__(self, microscope, main_app):
        """
        Note: when overriding this method, make sure to call the original
        method too with the following line:
        super(MyPluginClass, self).__init__(microscope, main_app)
        microscope (Microscope or None): the main back-end component.
          If the GUI is running as a viewer only, then it is None.
        main_app (wx.App): the main GUI component.
        """
        self.microscope = microscope
        self.main_app = main_app

    def terminate(self):
        """
        Called when the plugin should stop (ie, when the GUI ends)
        Note that it is not possible to prevent the GUI from ending. If you
        wish to do so because an acquisition is going on, you should instead set
        main_app.main_data.is_acquiring.value to True when starting the acquisition.
        """
        pass

    @call_in_wx_main
    def addMenu(self, entry, callback):
        """
        Adds a menu entry in the main GUI menu.
        entry (str): the complete path for the entry.
          It should have at least one group specified.
          If a group is non existing it will automatically be created.
          To add a keyboard shortcut, add it after a \t.
          For instance: "View/Fancy acquisition..."
                    or: "New group/Subgroup/The action\tCtrl+A"
        callback (callable): function to call when that entry is selected.
        raise ValueError: If the entry doesn't have a group or a name
        """
        # TODO: have a way to disable the menu on some conditions
        # Either return MenuItem (but cannot be call_in_wx_main anymore)
        # or pass a BooleanVA which indicate when the menu is enabled,
        # or just pass a list of tabs where the menu is enabled.
        # TODO: allow to pass a BooleanVA instead of callable => make it a checkable menu item
        main_frame = self.main_app.main_frame

        # Split the entry into groups and entry name
        path = entry.split("/")
        if len(path) < 2:
            raise ValueError("Failed to find a group and a name in '%s'" % (entry,))

        # Find or create group and subgroups
        # Nicely, in wxwidgets, the root level is a different class (MenuBar vs
        # Menu) with slightly different methods
        p = path[0]
        if not p:
            raise ValueError("Path contains empty group name '%s'" % (p,))
        root_group = main_frame.GetMenuBar()
        sub_group_idx = root_group.FindMenu(p)
        if sub_group_idx == wx.NOT_FOUND:
            logging.debug("Creating new menu group %s", p)
            curr_group = wx.Menu()
            # Insert as second last to keep 'Help' last
            menulen = root_group.GetMenuCount()
            root_group.Insert(menulen - 1, curr_group, p)
        else:
            curr_group = root_group.GetMenu(sub_group_idx)

        # All sub-levels are wx.Menu
        for p in path[1:-1]:
            if not p:
                raise ValueError("Path contains empty group name '%s'" % (p,))

            sub_group_id = curr_group.FindItem(p)
            if sub_group_id == wx.NOT_FOUND:
                logging.debug("Creating new menu group %s", p)
                sub_group = wx.Menu()
                curr_group.AppendSubMenu(sub_group, p)
            else:
                mi = curr_group.FindItemById(sub_group_id)
                sub_group = mi.GetSubMenu()
                if sub_group is None:
                    raise ValueError("Cannot create menu group %s, which is already an entry" % (p,))

            curr_group = sub_group

        # TODO: if adding for the first time to standard menu, first add a separator
        # Add the menu item
        menu_item = curr_group.Append(wx.ID_ANY, path[-1])

        # Attach the callback function
        def menu_callback_wrapper(evt):
            try:
                callback()
            except Exception:
                logging.exception("Error when processing menu entry %s of plugin %s",
                                  path[-1], self)
        wx.EVT_MENU(main_frame, menu_item.Id, menu_callback_wrapper)

    def showAcquisition(self, filename):
        """
        Show the analysis (aka Gallery) tab and opens the given acquisition file.
        filename (str): filename of the file to open.
        """
        analysis_tab = self.main_app.main_data.getTabByName('analysis')
        self.main_app.main_data.tab.value = analysis_tab
        analysis_tab.load_data(filename)


class AcquisitionDialog(xrcfr_plugin):
    def __init__(self, plugin, title, text=None):
        """
        Creates a modal window. The return code is the button number that was
          last pressed before closing the window.
        title (str): The title of the window
        text (None or str): If provided, it is displayed at the top of the window
        """

        super(AcquisitionDialog, self).__init__(plugin.main_app.main_frame)

        self.plugin = plugin

        self.SetTitle(title)

        if text is not None:
            self.lbl_description = AutoWrapStaticText(self.pnl_desc, "")
            self.lbl_description.SetBackgroundColour(self.pnl_desc.GetBackgroundColour())
            self.pnl_desc.GetSizer().Add(self.lbl_description, flag=wx.EXPAND | wx.ALL, border=10)
            self.lbl_description.SetLabel(text)

        self.entries = []  # Setting entries
        self._acq_future_connector = None
        self.canvas = None
        self.buttons = []  # The buttons
        self.current_future = None
        self.btn_cancel.Bind(wx.EVT_BUTTON, self._cancel_future)

        self.setting_controller = SettingsController(self.fp_settings,
                                                     "No settings defined")

        # Create a minimal model for use in the streambar controller

        data_model = MicroscopyGUIData(plugin.main_app.main_data)
        self.microscope_view = MicroscopeView("Plugin View")
        data_model.focussedView = VigilantAttribute(self.microscope_view)
        self.viewport_l.setView(self.microscope_view, data_model)

        self.microscope_view_r = MicroscopeView("Plugin View2")
        self.viewport_r.setView(self.microscope_view_r, data_model)

        self.streambar_controller = StreamBarController(
            data_model,
            self.pnl_streams,
            ignore_view=True
        )

        self.Refresh()
        self.Fit()

    @call_in_wx_main
    def addSettings(self, objWithVA, conf=None):
        """
        Adds settings as one widget on a line for each VigilantAttribute (VA) in
         the given object. Each setting entry created is added to .entries.
        objWithVA (object): an object with VAs.

        conf (None or dict of str->config): allows to override the automatic
          selection of the VA widget. See odemis.gui.conf.data for documentation.

        raise:
            LookupError: if no VA is found on the objWithVA

        """
        vas = getVAs(objWithVA)
        if not vas:
            raise LookupError("No VAs found!")

        if not conf:
            conf = {}
        vas_names = util.sorted_according_to(vas.keys(), conf.keys())

        for name in vas_names:
            va = vas[name]
            self.setting_controller.add_setting_entry(name, va, None,
                                                      conf=conf.get(name, None))

    @call_in_wx_main
    def addButton(self, label, callback=None, face_colour='def'):
        """
        Add a button at the bottom right of the window. If some buttons are already
        present, they are shifted to the left.

        label (str): text on the button,
        callback (None or callable): the function to be called when the button
          is pressed (with the dialog as argument). If callback is None,
          pressing the button will close the window and the button number will
          be the return code of the dialog.

        """
        btnid = len(self.buttons)
        btn = ImageTextButton(self.pnl_buttons, label=label, height=48,
                              style=wx.ALIGN_CENTER, face_colour=face_colour)
        self.buttons.append(btn)
        sizer = self.pnl_buttons.GetSizer()
        sizer.Add(btn, proportion=1, flag=wx.ALL | wx.ALIGN_RIGHT, border=10)

        if callback is not None and callable(callback):
            # Wrap the callback, to run in a separate thread, so it doesn't block
            # the GUI.
            def button_callback_wrapper(evt, btnid=btnid):
                try:
                    self.SetReturnCode(btnid)
                    t = threading.Thread(target=callback, args=(self,),
                                         name="Callback for button %s" % (label,))
                    t.start()
                except Exception:
                    logging.exception("Error when processing button %s of plugin %s",
                                      label, self.plugin)
            btn.Bind(wx.EVT_BUTTON, button_callback_wrapper)
        else:
            btn.Bind(wx.EVT_BUTTON, partial(self.on_close, btnid))

        self.Fit()

    @call_in_wx_main
    def addStream(self, stream, index=0):
        """
        Adds a stream to the canvas, and a stream entry to the stream panel.
        It also ensures the panel box and canvas are shown.

        Note: If this method is not called, the stream panel and canvas are hidden.

        stream(Stream): Stream to be added
        index(0 or 1): Index of the viewport to add the stream.
        returns (StreamController): the stream entry

        """
        if index == 0:
            viewport = self.viewport_l
            microscope_view = self.microscope_view
        else:
            viewport = self.viewport_r
            microscope_view = self.microscope_view_r

        if not self.fp_streams.IsShown() or not viewport.IsShown():
            self.fp_streams.Show()
            viewport.Show()
            self.Layout()
            self.Fit()
            self.Update()

        if stream:
            self.streambar_controller.addStream(stream)
            microscope_view.addStream(stream)

    @call_in_wx_main
    def showProgress(self, future):
        """
        Shows a progress bar, based on the status of the progressive future given.
        As long as the future is not finished, the buttons are disabled.

        future (None or Future): The progressive future to show the progress with
          the progress bar. If future is None, it will hide the progress bar.
          If future is cancellable, show a cancel button next to the progress bar.

        """

        if future is not None and not future.cancelled():
            self.current_future = future
            self.enable_buttons(False)

        self.Layout()
        self.Update()

        if self.current_future is None:
            self._acq_future_connector = None
            return
        else:
            if hasattr(self.current_future, "add_update_callback"):
                self._acq_future_connector = ProgressiveFutureConnector(self.current_future,
                                                                        self.gauge_progress,
                                                                        self.lbl_gauge)
            else:
                # TODO: just pulse the gauge at a "good" frequency (need to use a timer)
                self.gauge_progress.Pulse()

            if hasattr(self.current_future, 'task_canceller'):

                self.btn_cancel.Enable()
            else:
                self.btn_cancel.Disable()

        future.add_done_callback(self._on_future_done)

        # TODO: if the future is cancellable (ie, has task_canceller), allow to
        # press the "cancel" button, if such button exists, otherwise provide
        # such a button. That button will call cancel() on the future.

    @call_in_wx_main
    def enable_buttons(self, enable):
        """ Enable or disable all the buttons in the button panel """
        for btn in self.pnl_buttons.GetChildren():
            btn.Enable(enable)

    def _cancel_future(self, _):
        """ Cancel the future if it's there and running """
        if self.current_future is not None and not self.current_future.cancelled():
            if self.current_future.cancel():
                logging.debug("Future cancelled")
            else:
                logging.debug("Failed to cancel future")

    @call_in_wx_main
    def _on_future_done(self, _):
        """ Hide the gauge and label when the future finishes """
        self.gauge_progress.SetValue(0)
        self.lbl_gauge.SetLabel("")
        self.btn_cancel.Disable()
        self.enable_buttons(True)
        self.Layout()
        self.Update()

    def on_close(self, btnid, _):
        logging.debug("Closing window")
        self.streambar_controller.clear()
        self.EndModal(btnid)

    @call_in_wx_main
    def Destroy(self, *args, **kwargs):
        self.streambar_controller.clear()
        # save the return code, as Destroy() automatically sets it to wx.ID_CANCEL
        # but we want to keep the value potentially set by the button.
        rc = self.ReturnCode
        logging.debug("Destroying acquisition dialog")
        super(AcquisitionDialog, self).Destroy(*args, **kwargs)
        self.ReturnCode = rc
        logging.debug("Dialog destroyed")
