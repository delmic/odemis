# -*- coding: utf-8 -*-
"""
Created on 13 December 2023

@author: Nandish Patel

Gives ability to use Import Export ROAs tool under Help > Development.

Copyright © 2023 Nandish Patel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
"""

import csv
from enum import Enum
import logging
import wx
from wx.lib.embeddedimage import PyEmbeddedImage

from odemis.gui.plugin import Plugin


IMPORT_IMAGE = PyEmbeddedImage(
    b"iVBORw0KGgoAAAANSUhEUgAAABgAAAAVCAYAAABc6S4mAAAAqUlEQVQ4T2NkwA3+45FDlmoA"
    b"chqBGKSeEV0PhgCSAnIsAGlXB+JbMHNoYQGK2bS0AGQRI60twIwUCuIAa5qghg/AQYErxVHL"
    b"ApwpmhwLbgBN0yAyj5AVBzBHEZVPyPEBsh6CllBqASiklgBxNL5IxuUKfJZjMw+rOSBDaG4B"
    b"Lt+RYjHOuKBGHOCNaEosaAd6vYJQfiDXAoLJk5r1AV5PkOMDQqGCIk9zCwB4bSsHCFUhIAAA"
    b"AABJRU5ErkJggg=="
)


EXPORT_IMAGE = PyEmbeddedImage(
    b"iVBORw0KGgoAAAANSUhEUgAAABgAAAAVCAYAAABc6S4mAAAAjUlEQVQ4T9WUgQqAIAxE51f3"
    b"DX11qbRYhd7NHFQgBJ4+79xMIrLlgb50CIpW/9GaOl/EXoCuCwXQkFEHenoY11sAdOIFULlb"
    b"EQuAJ22RPYDeHk1nswFLJq2jETEOtKfO6prtwDZthUQC6v4MADVT96n5BAA1l3V4d0NFNAoI"
    b"veTQMr0UBXPJnoge2v8DdsqAJBTcjQywAAAAAElFTkSuQmCC"
)


class CSVFieldnames(Enum):
    PROJECT_NAME = "project_name"
    ROA_NAME = "roa_name"
    ROA_COORDINATES_L = "roa_coordinates_l"
    ROA_COORDINATES_T = "roa_coordinates_t"
    ROA_COORDINATES_R = "roa_coordinates_r"
    ROA_COORDINATES_B = "roa_coordinates_b"
    ROC_2_NAME = "roc_2_name"
    ROC_2_COORDINATES_L = "roc_2_coordinates_l"
    ROC_2_COORDINATES_T = "roc_2_coordinates_t"
    ROC_2_COORDINATES_R = "roc_2_coordinates_r"
    ROC_2_COORDINATES_B = "roc_2_coordinates_b"
    ROC_3_NAME = "roc_3_name"
    ROC_3_COORDINATES_L = "roc_3_coordinates_l"
    ROC_3_COORDINATES_T = "roc_3_coordinates_t"
    ROC_3_COORDINATES_R = "roc_3_coordinates_r"
    ROC_3_COORDINATES_B = "roc_3_coordinates_b"

    @staticmethod
    def list():
        return list(map(lambda c: c.value, CSVFieldnames))


class ImportExportROAPlugin(Plugin):
    name = "Import Export ROA"
    __version__ = "1.0"
    __author__ = "Nandish Patel"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)

        # It only makes sense if the FASTEM acquisition tab is present
        try:
            self._acquisition_tab = main_app.main_data.getTabByName("fastem_acqui")
        except LookupError:
            logging.debug(
                "Not loading Import Export ROAs tool since acquisition tab is not present"
            )
            return

        self.addMenu("Help/Development/Import Export ROAs", self.import_export_roa)

    def import_export_roa(self):
        """Help/Development/Import Export ROAs callback."""
        ImportExportTool().show(
            project_list_controller=self._acquisition_tab._project_list_controller
        )


class ImportExportTool(object):
    """
    The class ImportExportTool is a singleton that manages creating and showing
    ImportExportFrame class.

    """

    # Note: This is the Borg design pattern which ensures that all
    # instances of this class are actually using the same set of
    # instance data.  See
    # http://aspn.activestate.com/ASPN/Cookbook/Python/Recipe/66531
    __shared_state = None

    def __init__(self):
        if not ImportExportTool.__shared_state:
            ImportExportTool.__shared_state = self.__dict__
        else:
            self.__dict__ = ImportExportTool.__shared_state

        if not hasattr(self, "initialized"):
            self.initialized = False

    def init(self, pos=wx.DefaultPosition, size=wx.Size(300, 100), app=None):
        """
        Init is used to set some parameters that will be used later
        when the inspection tool is shown.  Suitable defaults will be
        used for all of these parameters if they are not provided.

        :param pos:   The default position to show the frame at
        :param size:  The default size of the frame
        :param app:  A reference to the :class:`App` object.
        """
        self._frame = None
        self._pos = pos
        self._size = size
        self._app = app
        if not self._app:
            self._app = wx.GetApp()
        self.initialized = True

    def show(self, project_list_controller=None):
        """
        Creates the import export feature frame and raises it if neccessary.

        :param pos: The default position to show the frame at.
        :param size: The default size of the frame.
        :param app: A reference to the class App object.
        :param project_list_controller: (FastEMProjectListController) class which creates/removes new
            FastEM projects.

        """
        if not self.initialized:
            self.init()

        parent = self._app.GetTopWindow()
        if not self._frame:
            self._frame = ImportExportFrame(
                parent=parent,
                pos=self._pos,
                size=self._size,
                project_list_controller=project_list_controller,
            )

        self._frame.Show()
        if self._frame.IsIconized():
            self._frame.Iconize(False)
        self._frame.Raise()


class ImportExportFrame(wx.Frame):
    """
    This class is the frame that holds the Import Export ROAs tool. The toolbar is managed here.

    :param project_list_controller: (FastEMProjectListController) class which creates/removes new
            FastEM projects.

    """

    def __init__(self, title="Import Export ROAs Tool", project_list_controller=None, *args, **kw):
        kw["title"] = title
        wx.Frame.__init__(self, *args, **kw)

        self._project_list_controller = project_list_controller

        self.make_tool_bar()

        self.Bind(wx.EVT_CLOSE, self.on_close)
        if self.Parent:
            tlw = self.Parent.GetTopLevelParent()
            tlw.Bind(wx.EVT_CLOSE, self.on_close)

    def make_tool_bar(self):
        """Create the Import Export ROAs toolbar."""
        tbar = self.CreateToolBar(wx.TB_HORIZONTAL | wx.TB_FLAT | wx.TB_TEXT | wx.NO_BORDER)
        tbar.SetToolBitmapSize(wx.Size(24, 24))

        import_bmp = IMPORT_IMAGE.GetBitmap()
        export_bmp = EXPORT_IMAGE.GetBitmap()

        import_tool = tbar.AddTool(-1, "Import", import_bmp, shortHelp="Import ROAs")
        export_tool = tbar.AddTool(-1, "Export", export_bmp, shortHelp="Export ROAs")
        tbar.Realize()

        self.Bind(wx.EVT_TOOL, self.on_import, import_tool)
        self.Bind(wx.EVT_TOOL, self.on_export, export_tool)

    def on_import(self, evt):
        """On Import ROA tool click."""
        evt.Skip()
        with wx.FileDialog(
            self,
            "Import ROAs from a CSV file",
            wildcard="CSV files (*.csv)|*.csv",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as fileDialog:
            if fileDialog.ShowModal() == wx.ID_CANCEL:
                return  # the user changed their mind

            # Proceed loading the file chosen by the user
            pathname = fileDialog.GetPath()
        try:
            with open(pathname, newline="") as csvfile:
                reader = csv.DictReader(csvfile)
                data = {}
                # Firstly extract the csv file data in a structured way
                # {"project_name": [roa]} where roa is dict containing
                # roa, roc_2, roc_3 name and its coordinates respectively
                for row in reader:
                    roas = {}
                    for fieldname in CSVFieldnames:
                        if fieldname == CSVFieldnames.PROJECT_NAME:
                            project_name = row[fieldname.value]
                        else:
                            roas[fieldname.value] = row[fieldname.value]
                    if project_name in data:
                        data[project_name].append(roas)
                    else:
                        data[project_name] = [roas]
                # Secondly create project control and update the roa's and roc's
                # data accordingly
                for project_name, roas in data.items():
                    project_ctrl = self._project_list_controller._add_project(
                        None, name=project_name
                    )
                    for roa in roas:
                        roa_name = roa[CSVFieldnames.ROA_NAME.value]
                        roa_coordinates = (
                            float(roa[CSVFieldnames.ROA_COORDINATES_L.value]),
                            float(roa[CSVFieldnames.ROA_COORDINATES_T.value]),
                            float(roa[CSVFieldnames.ROA_COORDINATES_R.value]),
                            float(roa[CSVFieldnames.ROA_COORDINATES_B.value]),
                        )
                        roc_2_name = roa[CSVFieldnames.ROC_2_NAME.value]
                        roc_2_coordinates = (
                            float(roa[CSVFieldnames.ROC_2_COORDINATES_L.value]),
                            float(roa[CSVFieldnames.ROC_2_COORDINATES_T.value]),
                            float(roa[CSVFieldnames.ROC_2_COORDINATES_R.value]),
                            float(roa[CSVFieldnames.ROC_2_COORDINATES_B.value]),
                        )
                        roc_3_name = roa[CSVFieldnames.ROC_3_NAME.value]
                        roc_3_coordinates = (
                            float(roa[CSVFieldnames.ROC_3_COORDINATES_L.value]),
                            float(roa[CSVFieldnames.ROC_3_COORDINATES_T.value]),
                            float(roa[CSVFieldnames.ROC_3_COORDINATES_R.value]),
                            float(roa[CSVFieldnames.ROC_3_COORDINATES_B.value]),
                        )
                        project_ctrl.regions_calib_2.value[int(roc_2_name)].name.value = roc_2_name
                        project_ctrl.regions_calib_2.value[
                            int(roc_2_name)
                        ].coordinates.value = roc_2_coordinates
                        project_ctrl.regions_calib_3.value[int(roc_3_name)].name.value = roc_3_name
                        project_ctrl.regions_calib_3.value[
                            int(roc_3_name)
                        ].coordinates.value = roc_3_coordinates
                        roa_ctrl = project_ctrl._on_btn_roa(None, name=roa_name)
                        roa_ctrl.model.coordinates.value = roa_coordinates
        except Exception as ex:
            logging.exception(ex)
            wx.MessageBox(
                f"Importing {pathname} failed, please see log.", "Error", wx.OK | wx.ICON_ERROR
            )

    def on_export(self, evt):
        """On Export ROA tool click."""
        evt.Skip()
        with wx.FileDialog(
            self,
            "Export ROAs to a CSV file",
            wildcard="CSV files (*.csv)|*.csv",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as fileDialog:
            if fileDialog.ShowModal() == wx.ID_CANCEL:
                return  # the user changed their mind

            # save the current contents in the file
            pathname = fileDialog.GetPath()
            if not pathname.endswith(".csv"):
                pathname += ".csv"
            try:
                with open(pathname, "w", newline="") as csvfile:
                    data = {}
                    writer = csv.DictWriter(csvfile, fieldnames=CSVFieldnames.list())
                    writer.writeheader()
                    project_ctrls = self._project_list_controller.project_ctrls
                    for project_ctrl in project_ctrls.values():
                        data[CSVFieldnames.PROJECT_NAME.value] = project_ctrl.model.name.value
                        roas = project_ctrl.model.roas.value
                        for roa in roas:
                            data[CSVFieldnames.ROA_NAME.value] = roa.name.value
                            roa_coordinates = roa.coordinates.value
                            data[CSVFieldnames.ROA_COORDINATES_L.value] = roa_coordinates[0]
                            data[CSVFieldnames.ROA_COORDINATES_T.value] = roa_coordinates[1]
                            data[CSVFieldnames.ROA_COORDINATES_R.value] = roa_coordinates[2]
                            data[CSVFieldnames.ROA_COORDINATES_B.value] = roa_coordinates[3]
                            roc_2 = roa.roc_2.value
                            data[CSVFieldnames.ROC_2_NAME.value] = roc_2.name.value
                            roc_2_coordinates = roc_2.coordinates.value
                            data[CSVFieldnames.ROC_2_COORDINATES_L.value] = roc_2_coordinates[0]
                            data[CSVFieldnames.ROC_2_COORDINATES_T.value] = roc_2_coordinates[1]
                            data[CSVFieldnames.ROC_2_COORDINATES_R.value] = roc_2_coordinates[2]
                            data[CSVFieldnames.ROC_2_COORDINATES_B.value] = roc_2_coordinates[3]
                            roc_3 = roa.roc_3.value
                            data[CSVFieldnames.ROC_3_NAME.value] = roc_3.name.value
                            roc_3_coordinates = roc_3.coordinates.value
                            data[CSVFieldnames.ROC_3_COORDINATES_L.value] = roc_3_coordinates[0]
                            data[CSVFieldnames.ROC_3_COORDINATES_T.value] = roc_3_coordinates[1]
                            data[CSVFieldnames.ROC_3_COORDINATES_R.value] = roc_3_coordinates[2]
                            data[CSVFieldnames.ROC_3_COORDINATES_B.value] = roc_3_coordinates[3]
                            writer.writerow(data)
            except Exception as ex:
                logging.exception(ex)
                wx.MessageBox(
                    f"Exporting {pathname} failed, please see log.", "Error", wx.OK | wx.ICON_ERROR
                )

    def on_close(self, evt):
        evt.Skip()
        if not self:
            return
        if self.Parent:
            tlw = self.Parent.GetTopLevelParent()
            tlw.Unbind(wx.EVT_CLOSE, handler=self.on_close)
