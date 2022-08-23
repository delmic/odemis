# -*- coding: utf-8 -*-
"""
Created on 24 Apr 2020

@author: Éric Piel

Copyright © 2020 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
"""

# Add an option to export a spatial spectrum stream (with X,Y,wavelength dimensions)
# into a CSV file "flatten" into the following format:
# Wavelength (m)    X (m)    Y (m)         intensity
# 5.97E-07       330.0e-6  10.0e-6          1564
#   :                :       :               :
# (varies fastest) ...    (varies slowest)  ...

import csv
import logging
from odemis import model
from odemis.acq.stream import StaticSpectrumStream
from odemis.gui.conf import get_acqui_conf
from odemis.gui.plugin import Plugin
from odemis.gui.util import formats_to_wildcards
from odemis.util import spectrum
import os
import wx


class SpecCSVPlugin(Plugin):
    name = "Export Spectrum to 3D CSV"
    __version__ = "1.0"
    __author__ = u"Éric Piel"
    __license__ = "Public domain"

    def __init__(self, microscope, main_app):
        super(SpecCSVPlugin, self).__init__(microscope, main_app)
        self.addMenu("File/Export spectrum to 3D CSV...", self.export)

    def export(self):
        analysis_tab = self.main_app.main_data.getTabByName('analysis')

        # Search for a spectrum stream
        for s in analysis_tab.tab_data_model.streams.value:
            if isinstance(s, StaticSpectrumStream):
                specs = s
                break
        else:
            box = wx.MessageDialog(self.main_app.main_frame,
                       "No spectrum stream found to export.",
                       "Failed to find spectrum stream", wx.OK | wx.ICON_STOP)
            box.ShowModal()
            box.Destroy()
            return

        # Select a file name
        path = get_acqui_conf().last_export_path
        wildcards, uformats = formats_to_wildcards({"CSV": [u"csv"]})
        dialog = wx.FileDialog(self.main_app.main_frame,
                           message="Choose a filename and destination",
                           defaultDir=path,
                           defaultFile="",
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
                           wildcard=wildcards)

        # Show the dialog and check whether is was accepted or cancelled
        if dialog.ShowModal() != wx.ID_OK:
            return

        # Get the full filename, and add an extension if needed
        path = dialog.GetDirectory()
        fn = dialog.GetFilename()
        if not fn.lower().endswith(".csv"):
            fn += ".csv"

        fullfn = os.path.join(path, fn)

        # Export
        self.export_3d_csv(fullfn, specs.calibrated.value)

    @staticmethod
    def export_3d_csv(filename, da):
        """
        filename (str)
        da (DataArray of shape C11YX)
        """
        logging.debug("Will export data of shape %s to %s", da.shape, filename)

        # Get the metadata
        spectrum_range, unit = spectrum.get_spectrum_range(da)
        pxs = da.metadata.get(model.MD_PIXEL_SIZE, (1, 1))

        # Remove useless dimensions -> CYX
        da = da[:, 0, 0, :, :]

        with open(filename, 'w') as fd:
            csv_writer = csv.writer(fd)
            csv_writer.writerow(["wavelength (%s)" % unit, "X (m)", "Y (m)", "intensity"])

            # Y
            for iy in range(da.shape[-2]):
                # X
                for ix in range(da.shape[-1]):
                    # Wavelength
                    for wl, data in zip(spectrum_range, da[:, iy, ix]):
                        csv_writer.writerow([wl, ix * pxs[0], iy * pxs[1], data])

