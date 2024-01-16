# -*- coding: utf-8 -*-
'''
Created on 16 Jan 2024

@author: Éric Piel

Gives ability to acquire a Spectrum scan with the e-beam position following an arbitrary order.
The scan order has to be specified by a python function in this file.

Copyright © 2024 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
'''
import functools
import logging
from collections import OrderedDict
from typing import Any, Dict, Tuple, List

import numpy

import odemis
import odemis.gui.conf.util as confutil
import odemis.gui.conf.data as confdata
import odemis.gui.model
from odemis import model
from odemis.acq.stream import SEMCCDMDStream, PolarizedCCDSettingsStream, TemporalSpectrumStream, SEMSpectrumMDStream, \
    SpectrumSettingsStream
from odemis.gui.conf.data import get_local_vas
from odemis.gui.plugin import Plugin
from odemis.model import MD_DESCRIPTION, MD_DIMS, MD_PIXEL_SIZE, MD_POS, MD_ROTATION, MD_ROTATION_COR, MD_WL_LIST


# Use a normal settings stream for the spectrum, but a dedicated MDStream that changes the e-beam
# scan order.

class SEMSpectrumArbitraryOrderMDStream(SEMSpectrumMDStream):
    """
    Same as Spectrum stream, but different scan order.
    """

    def _checkboard_scan_order(self, rep: Tuple[int, int]) -> List[Tuple[int, int]]:
        """
        First scan every odd pixel, and then every even pixel. Like first scanning the white
        squares of a checkboard, and then the black squares.
        Example:
        192.3.4.
        .5.6.7.8
        :param rep:
        :return:
        """
        order = []
        # Add odd pixels
        for j in range(rep[1]):  # Y slow
            for i in range(rep[0]):  # X fast
                if (i + j) % 2 == 0:
                    order.append((i, j))

        # Add even pixels
        for j in range(rep[1]):
            for i in range(rep[0]):
                if (i + j) % 2 == 1:
                    order.append((i, j))

        return order

    def _33_scan_order(self, rep: Tuple[int, int]) -> List[Tuple[int, int]]:
        """
        Scan every 3 pixels, and then restart, but from a shift in the 3x3 initial point:
        Example:
        15926.
        ......
        ......
        37.48.
        :param rep:
        :return:
        """
        order = []
        # 3x3 base
        for y_base in range(3):
            for x_base in range(3):
                # Every 3rd pixel (within the repetition)
                for j in range(y_base, rep[1], 3):  # Y slow
                    for i in range(x_base, rep[0], 3):  # X fast
                        order.append((i, j))

        return order

    def _get_scan_order(self, rep: Tuple[int, int]) -> List[Tuple[int, int]]:
        """
        Computes the arbitrary scan order
        :param rep: x,y number of pixels to scan
        :return: list of positions (x, y) as indices to scan, in the scan order
        """
        #return self._checkboard_scan_order(rep)
        return self._33_scan_order(rep)

    def _getSpotPositions(self):
        """
        Compute the positions of the e-beam for each point in the ROI
        return (numpy ndarray of floats of shape (Y,X,2)): each value is for a
          given Y,X in the rep grid -> 2 floats corresponding to the
          translation X,Y. Note that the dimension order is different between
          index and content, because X should be scanned first, so it's last
          dimension in the index.
        """
        rep = tuple(self.repetition.value)
        roi = self.roi.value
        width = (roi[2] - roi[0], roi[3] - roi[1])

        # Take into account the "border" around each pixel
        pxs = (width[0] / rep[0], width[1] / rep[1])
        lim = (roi[0] + pxs[0] / 2, roi[1] + pxs[1] / 2)

        shape = self._emitter.shape
        # convert into SEM translation coordinates: distance in px from center
        # (situated at 0.5, 0.5), can be floats
        pos00 = (shape[0] * (lim[0] - 0.5), shape[1] * (lim[1] - 0.5))
        logging.debug("Generating points from %s with pxs %s, from rep %s and roi %s",
                      pos00, pxs, rep, roi)

        pos = numpy.empty((rep[1], rep[0], 2), dtype=float)
        order = self._get_scan_order(rep)
        logging.debug("Arbitrary order: %s", order)
        self._px_order = order

        raw_order = numpy.ndindex(*rep[::-1])
        for raw_idx, px_idx in zip(raw_order, order):
            beam_pos = (pos00[0] + px_idx[0] * pxs[0],
                        pos00[1] + px_idx[1] * pxs[1],)
            pos[raw_idx] = beam_pos

        logging.debug("Will acquire at beam pos: %s", pos)
        return pos

    def _assembleLiveData(self, n, raw_data, px_idx, rep, pol_idx):
        """
        :param px_idx: y, x position
        :param rep: x, y number of points in teh scan
        """
        # px_idx
        px_idx_flat = px_idx[0] * rep[0] + px_idx[1]
        act_px_idx = self._px_order[px_idx_flat][::-1]
        logging.debug("Converted back idx %s to %s", px_idx, act_px_idx)
        return super()._assembleLiveData(n, raw_data, act_px_idx, rep, pol_idx)




class SpectrumArbScOrPlugin(Plugin):
    name = "Spectrum acquisition in arbitrary scan order"
    __version__ = "1.0"
    __author__ = u"Éric Piel"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)
        # Can only be used with a SPARC with spetrometer and CCD camera
        main_data = self.main_app.main_data
        if not (microscope and main_data.role.startswith("sparc") and main_data.spectrometers):
            logging.info("%s plugin cannot load as the microscope is not a SPARC with spectrometer",
                         self.name)

        self._tab = self.main_app.main_data.getTabByName("sparc_acqui")
        stctrl = self._tab.streambar_controller
        spectrometers = main_data.spectrometers
        for sptm in spectrometers:
            if len(spectrometers) <= 1:
                actname = "Spectrum Arbitrary Scan"
            else:
                actname = "Spectrum Arbitrary Scan with %s" % (sptm.name,)
            act = functools.partial(self.add_stream, name=actname, detector=sptm)
            stctrl.add_action(actname, act)

    def add_stream(self, name: str, detector: "Detector"):
        """
        Create a new spectrum stream and MDStream.
        :param name:
        :param detector: the spectrometer () to use
        """
        # Mostly a copy of odemis.gui.cont.streams.SparcStreamsController.addSpectrum()
        main_data = self.main_app.main_data
        stctrl = self._tab.streambar_controller

        logging.debug("Adding spectrum arbitrary order stream for %s", detector.name)

        spectrograph = stctrl._getAffectingSpectrograph(detector)

        axes = {"wavelength": ("wavelength", spectrograph),
                "grating": ("grating", spectrograph),
                "slit-in": ("slit-in", spectrograph),
                }
        axes = stctrl._filter_axes(axes)

        # Also add light filter for the spectrum stream if it affects the detector
        for fw in (main_data.cl_filter, main_data.light_filter):
            if fw is None:
                continue
            if detector.name in fw.affects.value:
                axes["filter"] = ("band", fw)
                break

        sr_stream = SpectrumSettingsStream(
            name,
            detector,
            detector.data,
            main_data.ebeam,
            sstage=main_data.scan_stage,
            opm=main_data.opm,
            axis_map=axes,
            detvas=get_local_vas(detector, main_data.hw_settings_config),
        )
        stctrl._set_default_spectrum_axes(sr_stream)

        # Create the equivalent MDStream
        sem_stream = self._tab.tab_data_model.semStream
        sem_cl_stream = SEMSpectrumArbitraryOrderMDStream(name, [sem_stream, sr_stream])

        return stctrl._addRepStream(sr_stream, sem_cl_stream)
