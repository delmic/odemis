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

from __future__ import division

import logging
import numpy
from odemis import model
from odemis.acq.stream import SEMCCDMDStream, ARSettingsStream
from odemis.gui.conf.data import get_local_vas
from odemis.gui.plugin import Plugin


class SEMCLCCDStream(SEMCCDMDStream):
    """
    Multiple detector Stream made of SEM + AR (converted into a single pixel).
    It handles acquisition, but not rendering (so .image always returns an empty
    image).
    """
    def _runAcquisition(self, future):
        self._ccd_md = None
        return super(SEMCLCCDStream, self)._runAcquisition(future)

    def _preprocessData(self, n, data, i):
        """
        return (int): mean of the data
        """
        # We only care about the CCD data
        if n < len(self.streams) - 1:
            return super(SEMCLCCDStream, self)._preprocessData(n, data, i)

        if not self._ccd_md:
            self._ccd_md = data.metadata

        # We could return the sum, but it's probably overkill as the original
        # data type contains probably enough precision, and would need special
        # handling of very large data type.
        return data.mean().astype(data.dtype)

    def _onCompletedData(self, n, raw_das):
        # Only override for the CCD data
        if n < len(self._streams) - 1:
            r = super(SEMCLCCDStream, self)._onCompletedData(n, raw_das)
            return r

        # Same as sem data, but without computing the data position from the
        # CCD metadata
        md = self._ccd_md.copy()
        sem_data = self._raw[0]  # _onCompletedData() should be called in order
        md[model.MD_POS] = sem_data.metadata[model.MD_POS]
        md[model.MD_DESCRIPTION] = self._streams[n].name.value
        # Make sure it doesn't contain metadata related to AR
        for k in (model.MD_AR_POLE, model.MD_AR_FOCUS_DISTANCE,
                  model.MD_AR_HOLE_DIAMETER, model.MD_AR_PARABOLA_F,
                  model.MD_AR_XMAX, model.MD_ROTATION):
            md.pop(k, None)

        try:
            # handle sub-pixels (aka fuzzing)
            sem_shape = sem_data.shape[-1:-3:-1]  # 1,1,1,Y,X -> X, Y
            rep = self.repetition.value
            tile_shape = (sem_shape[0] / rep[0], sem_shape[1] / rep[1])
            pxs = (sem_data.metadata[model.MD_PIXEL_SIZE][0] * tile_shape[0],
                   sem_data.metadata[model.MD_PIXEL_SIZE][1] * tile_shape[1])
            md[model.MD_PIXEL_SIZE] = pxs
        except KeyError:
            logging.warning("Metadata missing from the SEM data")

        # concatenate data into one big array of (number of pixels,1)
        flat_list = [ar.flatten() for ar in raw_das]
        rep_one = numpy.concatenate(flat_list)
        # reshape to (Y, X)
        rep_one.shape = rep[::-1]
        rep_one = model.DataArray(rep_one, metadata=md)

        self._raw.append(rep_one)


class CLiCCDPlugin(Plugin):
    name = "CL intensity CCD"
    __version__ = "1.0"
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

        # TODO: special live stream?
        ar_stream = ARSettingsStream(
            "CL intensity on CCD",
            main_data.ccd,
            main_data.ccd.data,
            main_data.ebeam,
            sstage=main_data.scan_stage,
            opm=main_data.opm,
            detvas=get_local_vas(main_data.ccd),
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

        stctrl = self._tab.streambar_controller
        return stctrl._addRepStream(ar_stream, sem_cl_stream,
                                  vas=("repetition", "pixelSize", "fuzzing"),
                                  axes={"band": main_data.light_filter}
                                  )

