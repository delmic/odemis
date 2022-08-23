# -*- coding: utf-8 -*-
'''
Created on 22 Mar 2017

@author: Éric Piel

Gives ability to acquire a CL intensity stream using the AR camera.

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

import logging
import numpy
from odemis import model
from odemis.acq.stream import SEMCCDMDStream, ARSettingsStream
from odemis.gui.conf.data import get_local_vas
from odemis.gui.plugin import Plugin
from odemis.model import MD_PIXEL_SIZE, MD_POS, MD_DIMS, MD_DESCRIPTION


class SEMCLCCDStream(SEMCCDMDStream):
    """
    Multiple detector Stream made of SEM + AR (converted into a single pixel).
    It handles acquisition, but not rendering (so .image always returns an empty
    image).
    """

    def _preprocessData(self, n, data, i):
        """
        return (int): mean of the data
        """
        # We only care about the CCD data
        if n < len(self.streams) - 1:
            return super(SEMCLCCDStream, self)._preprocessData(n, data, i)

        # Computing the sum or the mean is theoretically equivalent, but the sum
        # provides gigantic values while the mean gives values in the same order
        # as in the original data. Note that we cannot use the original dtype
        # because if it's an integer it will likely cause quantisation (as a
        # large part of the image received is identical for all the e-beam
        # positions scanned).
        return data.mean()

    def _assembleLiveData(self, n, raw_data, px_idx, rep, pol_idx):
        if n != self._ccd_idx:
            return super(SEMCLCCDStream, self)._assembleLiveData(n, raw_data, px_idx, rep, pol_idx)

        if pol_idx > len(self._live_data[n]) - 1:
            # New polarization => new DataArray
            md = raw_data.metadata.copy()
            # Compute metadata based on SEM metadata
            semmd = self._live_data[0][pol_idx].metadata
            # handle sub-pixels (aka fuzzing)
            md[MD_PIXEL_SIZE] = (semmd[MD_PIXEL_SIZE][0] * self._emitter.resolution.value[0],
                                 semmd[MD_PIXEL_SIZE][1] * self._emitter.resolution.value[1])
            md[MD_POS] = self._live_data[0][pol_idx].metadata[MD_POS]
            md[MD_DIMS] = "YX"
            md[MD_DESCRIPTION] = self._streams[n].name.value
            # Make sure it doesn't contain metadata related to AR
            for k in (model.MD_AR_POLE, model.MD_AR_MIRROR_BOTTOM, model.MD_AR_MIRROR_TOP,
                      model.MD_AR_FOCUS_DISTANCE, model.MD_AR_HOLE_DIAMETER, model.MD_AR_PARABOLA_F,
                      model.MD_AR_XMAX, model.MD_ROTATION, model.MD_WL_LIST):
                md.pop(k, None)

            da = numpy.zeros(shape=(rep[1], rep[0]), dtype=raw_data.dtype)
            self._live_data[n].append(model.DataArray(da, md))

        self._live_data[n][pol_idx][px_idx] = raw_data


class CLiCCDPlugin(Plugin):
    name = "CL intensity CCD"
    __version__ = "1.2"
    __author__ = u"Éric Piel"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super(CLiCCDPlugin, self).__init__(microscope, main_app)
        # Can only be used with a SPARC with AR camera
        main_data = self.main_app.main_data
        if microscope and main_data.ccd and main_data.role.startswith("sparc"):
            self._tab = self.main_app.main_data.getTabByName("sparc_acqui")
            stctrl = self._tab.streambar_controller
            stctrl.add_action("CL intensity on CCD", self.addst)
        else:
            logging.info("%s plugin cannot load as the microscope is not a SPARC with AR",
                         self.name)
        # TODO: also support same functionality with sp-ccd

    def addst(self):
        main_data = self.main_app.main_data
        stctrl = self._tab.streambar_controller

        axes = stctrl._filter_axes({"filter": ("band", main_data.light_filter)})

        # TODO: special live stream?
        ar_stream = ARSettingsStream(
            "CL intensity on CCD",
            main_data.ccd,
            main_data.ccd.data,
            main_data.ebeam,
            sstage=main_data.scan_stage,
            opm=main_data.opm,
            axis_map=axes,
            detvas=get_local_vas(main_data.ccd, main_data.hw_settings_config),
        )
        # TODO: Allow very large binning on the CCD

        # Make sure the binning is not crazy (especially can happen if CCD is shared for spectrometry)
        if model.hasVA(ar_stream, "detBinning"):
            b = ar_stream.detBinning.value
            if b[0] != b[1] or b[0] > 16:
                ar_stream.detBinning.value = ar_stream.detBinning.clip((1, 1))
                ar_stream.detResolution.value = ar_stream.detResolution.range[1]

        # Create the equivalent MDStream
        sem_stream = self._tab.tab_data_model.semStream
        sem_cl_stream = SEMCLCCDStream("SEM CLi CCD", [sem_stream, ar_stream])

        return stctrl._addRepStream(ar_stream, sem_cl_stream)

