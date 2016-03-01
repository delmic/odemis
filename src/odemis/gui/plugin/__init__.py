# -*- coding: utf-8 -*-
'''
Created on 01 Mar 2016

@author: Éric Piel

Copyright © 2016 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

import wx

class Plugin(object):
    """
    This is the root class for Odemis GUI plugins.
    Every plugin must be a subclass of that class.
    When starting, the GUI will look in the following directories for python
    scripts and intanciate all subclasses of odemis.gui.plugin.Plugin:
      /usr/share/odemis/plugins/
      /usr/local/share/odemis/plugins/
      $HOME/.local/share/odemis/plugins/
    """
    __metaclass__ = ABCMeta
    # TODO force something like @abstractattribute
    __version__ = None
    __author__ = None
    __licence__ = None

    def __init__(self, microscope, main_app):
        """
        Note: when overriding this method, make sure to call the original
        method too with the follwing line:
        super(MyPluginClass, self).__init__(microscope, main_app)
        microscope (Microscope or None): the main back-end component.
          If the GUI is running as a viewer only, then it is None.
        main_app (wx.App): the main GUI component. 
        """
        self.microscope = microscope
        self.main_app = main_app

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
        main_frame = self.main_app.main_frame
        
        # Split the entry into groups and entry name
        path = entry.split("/")
        if len(path) < 2:
            raise ValueError("Failed to find a group and a name in '%s' % (entry,))

        # Find or create group and subgroups
        curr_group = main_frame.GetMenuBar()
        for g in path[:-1]:
            if not p:
                raise ValueError("Path contains empty group name '%s'" % (p,))
            # Look for the group, if not present => add it as last one, expected
            # for the very first level, where Help is very last.
            sub_group = curr_group.FindMenu(g)
            if not sub_group:
                logging.debug("Creating new menu group %s", g)
                curr_group.AppendMenu()
        # TODO
        
        wx.EVT_MENU(main_frame, menu_item.GetId(), lambda evt: callback())
    
    
    def showAcquisition(filename):
        """
        Show the analysis (aka Gallery) tab and opens the given acquisition file.
        filename (str): filename of the file to open.
        """
        self.main_app.tab_controller.open_tab('analysis')
        self.main_app.main_data.tab.value.load_data(filename)
        
        
class AcquisitionDialog(object):
    def __init__(self, plugin, title, text=None):
        """
        Creates a modal window. The return code is the button number that was
          last pressed before closing the window.
        title (str): The title of the window
        text (None or str): If provided, it is displayed at the top of the window 
        """
        self.text = None  # the wx.StaticText used to show the text
        self.entries = [] # Setting entries
        self.canvas = None
        self.buttons = [] # The buttons
        # TODO

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
        # TODO
        pass
        

    def addButton(self, label, callback=None):
        """
        Add a button at the bottom right of the window. If some buttons are already
        present, they are shifted to the left.
        label (str): text on the button,
        callback (None or callable): the function to be called when the button
          is pressed (with the dialog as argument). If callback is None, 
          pressing the button will close the window and the button number will
          be the return code of the dialog.
        """
        # TODO
        pass

    def addStream(self, stream):
       """
       Adds a stream to the canvas, and a stream entry to the stream panel.
       It also ensure the panel box and canvas as shown. 
       Note: If this method is not called, the stream panel and canvas are hidden. 
       returns (StreamController): the stream entry
       """
       # TODO
       pass

    def showProgress(self, future):
       """
       Shows a progress bar, based on the status of the progressive future given.
       As long as the future is not finished, the buttons are disabled.
       future (None or Future): The progressive future to show the progress with
         the progress bar. If future is None, it will hide the progress bar.
         If future is cancellable, show a cancel button next to the progress bar.
       """

       # TODO
       pass
