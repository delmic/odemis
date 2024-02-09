# -*- coding: utf-8 -*-

"""
@author: Rinze de Laat, Éric Piel, Philip Winkler, Victoria Mavrikopoulou,
         Anders Muskens, Bassim Lazem, Patrick Cleeve

Copyright © 2012-2022 Rinze de Laat, Éric Piel, Delmic

Handles the switch of the content of the main GUI tabs.

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

import collections
import logging
import os.path
from typing import List, Optional
import wx

from odemis.gui import conf

from odemis import dataio
from odemis import model
import odemis.gui
import odemis.gui.cont.export as exportcont
from odemis.gui.cont.stream_bar import StreamBarController
import odemis.gui.cont.views as viewcont
import odemis.gui.model as guimod
import odemis.gui.util as guiutil
from odemis.acq.stream import OpticalStream, EMStream, StaticStream
from odemis.gui.cont.correlation import CorrelationController
from odemis.gui.model import TOOL_ACT_ZOOM_FIT
from odemis.gui.util import call_in_wx_main
from odemis.gui.cont.tabs.tab import Tab
from odemis.util.dataio import data_to_static_streams, open_acquisition, open_files_and_stitch


class CorrelationTab(Tab):

    def __init__(self, name: str,
                 button: odemis.gui.comp.buttons.TabButton,
                 panel: wx.Panel,
                 main_frame: odemis.gui.main_xrc.xrcfr_main,
                 main_data: odemis.gui.model.MainGUIData):
        """ Correlation tab for the correlation of multiple streams"""

        tab_data = guimod.CryoCorrelationGUIData(main_data)
        super().__init__(name, button, panel, main_frame, tab_data)

        self.main_data = main_data

        # create the views, view_controller, and then add streams
        vpv = self._create_views(panel.pnl_correlaton_grid.viewports)
        self.view_controller = viewcont.ViewPortController(tab_data, panel, vpv)

        # Connect the view selection buttons
        buttons = collections.OrderedDict([
            (panel.btn_correlation_view_all,
                (None, panel.lbl_correlation_view_all)),
            (panel.btn_correlation_view_tl,
                (panel.vp_correlation_tl, panel.lbl_correlation_view_tl)),
            (panel.btn_correlation_view_tr,
                (panel.vp_correlation_tr, panel.lbl_correlation_view_tr)),
            (panel.btn_correlation_view_bl,
                (panel.vp_correlation_bl, panel.lbl_correlation_view_bl)),
            (panel.btn_correlation_view_br,
                (panel.vp_correlation_br, panel.lbl_correlation_view_br)),
        ])

        # view selector
        self._view_selector = viewcont.ViewButtonController(
            tab_data,
            panel,
            buttons,
            panel.pnl_correlaton_grid.viewports
        )

        # stream bar controller
        self._streambar_controller = StreamBarController(
            tab_data,
            panel.pnl_correlation_streams,
            static=True,
        )

        self._streambar_controller.add_action("From file...", self._on_add_file)
        self._streambar_controller.add_action("From tileset...", self._on_add_tileset)
        self.panel.fp_correlation_streams.Show(True) # show stream bar panel always

        # correlation controller
        self._correlation_controller = CorrelationController(
            tab_data,
            panel,
            self,
            panel.pnl_correlaton_grid.viewports
        )

        # export controller
        self.export_controller = exportcont.ExportController(tab_data, main_frame, panel, vpv)

        self.conf = conf.get_acqui_conf()

        # Toolbar
        self.tb = panel.correlation_toolbar
        for t in guimod.TOOL_ORDER:
            if t in tab_data.tool.choices:
                self.tb.add_tool(t, tab_data.tool)
        # Add fit view to content to toolbar
        self.tb.add_tool(TOOL_ACT_ZOOM_FIT, self.view_controller.fitViewToContent)

    @property
    def streambar_controller(self):
        return self._streambar_controller

    @property
    def correlation_controller(self):
        return self._correlation_controller

    def _create_views(self, viewports: list) -> collections.OrderedDict:
        """
        Create views depending on the actual hardware present
        :param viewports: list of viewports
        :return OrderedDict: as needed for the ViewPortController
        """
        # Acquired data at the top, live data at the bottom
        vpv = collections.OrderedDict([
            (viewports[0], # focused view
             {"name": "FLM Overview",
              "stream_classes": OpticalStream,
              }),
            (viewports[1],
             {"name": "SEM Overview",
              "stream_classes": EMStream,
              }),
            (viewports[2],
             {"name": "Overlay 1",
              "stream_classes": StaticStream,
              }),
            (viewports[3],
             {"name": "Overlay 2",
              "stream_classes": StaticStream,
              }),
        ])

        return vpv

    # from analysis/gallery tab
    def _on_add_file(self) -> None:
        """
        Called when the user requests to extend the current acquisition with
        an extra file.
        """
        self.select_acq_file(True)

    def _on_add_tileset(self) -> None:
        self.select_acq_file(extend=True, tileset=True)

    def load_tileset(self, filenames: List[str], extend: bool = False) -> None:
        data = open_files_and_stitch(filenames) # TODO: allow user defined registration / weave methods
        self.load_streams(data)

    def load_data(self, filename: str, fmt: str = None, extend: bool = False) -> None:
        data = open_acquisition(filename, fmt)
        self.load_streams(data)

    def select_acq_file(self, extend: bool = False, tileset: bool = False):
        """ Open an image file using a file dialog box

        extend (bool): if False, will ensure that the previous streams are closed.
        If True, will add the new file to the current streams opened.
        tileset (bool): if True, open files as tileset and stitch together
        return (boolean): True if the user did pick a file, False if it was
        cancelled.
        """
        # Find the available formats (and corresponding extensions)
        formats_to_ext = dataio.get_available_formats(os.O_RDONLY)

        path = self.conf.last_path

        wildcards, formats = guiutil.formats_to_wildcards(formats_to_ext, include_all=True)
        msg = "Choose a file to load" if not tileset else "Choose a tileset to load"
        dialog = wx.FileDialog(self.panel,
                            message=msg,
                            defaultDir=path,
                            defaultFile="",
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE,
                            wildcard=wildcards)

        # Show the dialog and check whether is was accepted or cancelled
        if dialog.ShowModal() != wx.ID_OK:
            return False

        # Detect the format to use
        fmt = formats[dialog.GetFilterIndex()]

        if tileset:
            filenames = dialog.GetPaths()
            self.load_tileset(filenames, extend=extend)
        else:
            for filename in dialog.GetPaths():
                if extend:
                    logging.debug("Extending the streams with file %s", filename)
                else:
                    logging.debug("Current file set to %s", filename)

                self.load_data(filename, fmt, extend=extend)
                extend = True  # If multiple files loaded, the first one is considered main one

    @call_in_wx_main
    def load_streams(self, data: List[model.DataArray]) -> None:
        """ Load the data in the overview viewports
        :param data: (list[model.DataArray]) list of data arrays to load as streams
        """

        # Create streams from data, add to correlation controller
        streams = data_to_static_streams(data)
        self.correlation_controller.add_streams(streams)

        # fit to content
        for vp in self.panel.pnl_correlaton_grid.viewports:
            vp.canvas.fit_view_to_content()

    def add_streams(self, streams:list) -> None:
        """add streams to correlation tab
        :param streams: (list[Stream]) list of streams to add"""
        self.correlation_controller.add_streams(streams)

    def clear_streams(self) -> None:
        """clears streams from the correlation tab"""
        self.correlation_controller.clear_streams()

    @classmethod
    def get_display_priority(cls, main_data) -> Optional[int]:
        if main_data.role == "meteor":
            return 1
        else:
            return None
