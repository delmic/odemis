# -*- coding: utf-8 -*-
"""
Created on 5 Dev 2025

@author: Éric Piel

Gives ability to acquire a spectrum data, while keeping the raw CCD image (ie, without vertical binning)

Copyright © 2025 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
"""
import functools
import logging

from odemis import model
from odemis.acq.stream import MonochromatorSettingsStream
from odemis.gui.conf.data import get_local_vas
from odemis.gui.main import OdemisGUIApp
from odemis.gui.plugin import Plugin


class PhotoDetectorLivePlugin(Plugin):
    name = "Photo-detector live display"
    __version__ = "1.0"
    __author__ = "Éric Piel"
    __license__ = "GPLv2"

    def __init__(self, microscope: model.Microscope, main_app: OdemisGUIApp):
        super().__init__(microscope, main_app)
        main_data = self.main_app.main_data
        if not (microscope and main_data.photo_ds and main_data.role.startswith("sparc")):
            logging.info("%s plugin cannot load as the microscope is not a SPARC with a photo detector.",
                         self.name)
            return

        self._tab = self.main_app.main_data.getTabByName("sparc_acqui")
        stctrl = self._tab.streambar_controller
        for det in main_data.photo_ds:
            name = f"{det.name} alignment"
            act = functools.partial(self.add_photo_det_stream, name=name, detector=det)
            stctrl.add_action(name, act)

        # Note: no need to explicitly add the "Temporal Intensity" viewport, because it's normally
        # always created when a time-correlator is available, which is the assumption here.

    def add_photo_det_stream(self, name: str, detector: model.Detector):
        """ Create a Monochromator stream, using a photo-detector and add to to all compatible viewports"""
        main_data = self.main_app.main_data
        stctrl = self._tab.streambar_controller

        # Axes on the "LabCube", which are always affecting the time-correlator photo-detectors
        axes = {"density": ("density", main_data.tc_od_filter),
                "filter": ("band", main_data.tc_filter)}

        spg = stctrl._getAffectingSpectrograph(detector, default=main_data.spectrograph)
        axes.update({
            "wavelength": ("wavelength", spg),
            "grating": ("grating", spg),
            "iris-in": ("iris-in", spg),
            "slit-in": ("slit-in", spg),
            "slit-monochromator": ("slit-monochromator", spg),
       })

        # Also add light filter if it affects the detector
        filter_in = main_data.light_filter
        if filter_in and detector.name in filter_in.affects.value:
            axes["filter-in"] = ("band", filter_in)

        axes = stctrl._filter_axes(axes)

        photodet_stream = MonochromatorSettingsStream(
            name,
            detector,
            detector.data,
            main_data.ebeam,
            sstage=main_data.scan_stage,
            opm=main_data.opm,
            axis_map=axes,
            emtvas={"dwellTime"},
            detvas=get_local_vas(detector, main_data.hw_settings_config),
        )
        stctrl._set_default_spectrum_axes(photodet_stream)

        # Don't call _addRepStream(), because we only add a live stream, no acquisition stream

        stream_cont = stctrl._add_stream(photodet_stream, add_to_view=True)
        stream_cont.stream_panel.show_visible_btn(False)
        return stream_cont
