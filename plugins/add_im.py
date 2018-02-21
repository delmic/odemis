# -*- coding: utf-8 -*-
'''
Created on 19 Jul 2017

@author: Éric Piel

Gives ability to add extra acquisition files into an already opened acquisition

Copyright © 2017 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
'''

from __future__ import division

import logging
from odemis import dataio
from odemis.gui.conf import get_acqui_conf
from odemis.gui.plugin import Plugin
import os
import wx

import odemis.gui.util as guiutil
import odemis.util.dataio as udataio


class ImageAdderPlugin(Plugin):
    name = "Extra image adder"
    __version__ = "1.0"
    __author__ = u"Éric Piel"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super(ImageAdderPlugin, self).__init__(microscope, main_app)

        # Add a menu entry in the "Add Stream" button under the streams
        tab = self.main_app.main_data.getTabByName("analysis")
        tab.stream_bar_controller._stream_bar.show_add_button()
        tab.stream_bar_controller.add_action("From file...", self.pick_image)

    def pick_image(self):
        # Use a good default folder to start with
        tab = self.main_app.main_data.getTabByName("analysis")
        tab_data = tab.tab_data_model
        fi = tab_data.acq_fileinfo.value

        if fi and fi.file_name:
            path, _ = os.path.split(fi.file_name)
        else:
            config = get_acqui_conf()
            path = config.last_path

        # TODO: allow multiple file selection
        # Find the available formats (and corresponding extensions)
        formats_to_ext = dataio.get_available_formats(os.O_RDONLY)
        wildcards, formats = guiutil.formats_to_wildcards(formats_to_ext, include_all=True)
        dialog = wx.FileDialog(self.main_app.main_frame,
                               message="Choose a file to load",
                               defaultDir=path,
                               defaultFile="",
                               style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
                               wildcard=wildcards)

        # Show the dialog and check whether is was accepted or cancelled
        if dialog.ShowModal() != wx.ID_OK:
            return None

        # TODO: support AR and spectrum streams
        # Open the file into static streams
        filename = dialog.GetPath()
        data = udataio.open_acquisition(filename)
        streams = udataio.data_to_static_streams(data)

        # Add a new (removable) stream panels
        for s in streams:
            scont = tab.stream_bar_controller.addStream(s, add_to_view=True)
            scont.stream_panel.show_remove_btn(True)
