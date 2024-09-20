# -*- coding: utf-8 -*-
"""
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
"""
import functools
import inspect
import logging
from collections import OrderedDict
from typing import Tuple, List

import numpy

import odemis
from odemis import model
from odemis.acq.stream import SEMSpectrumMDStream, SpectrumSettingsStream
from odemis.gui.conf import data, util
from odemis.gui.conf.data import get_local_vas
from odemis.gui.plugin import Plugin


# This plugin provides a new type of SpectrumStream. The implementation actually uses the
# standard SpectrumSettingsStream, and only needs to provide a dedicated MDStream that overrides
# the standard SEMSpectrumMDStream. It changes the e-beam position for each pixel.


# Special functions to define the scan strategies: their name *must start with "scan_order_"*
# The first line of the docstring will be the name as show in the GUI.

def scan_order_checkerboard(rep: Tuple[int, int]) -> List[Tuple[int, int]]:
    """
    Checkerboard
    First scan every odd pixel, and then every even pixel. Like first scanning the white
    squares of a checkerboard, and then the black squares.
    Example:
    192.3.4.
    .5.6.7.8
    See _get_scan_order() for the parameter documentation
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


def scan_order_3x3(rep: Tuple[int, int]) -> List[Tuple[int, int]]:
    """
    3x3
    Scan every 3 pixels, and then restart, but from a shift in the 3x3 initial point:
    Example:
    15926.
    ......
    ......
    37.48.
    See _get_scan_order() for the parameter documentation
    """
    return _scan_order_mxn(rep, (3, 3))


def scan_order_4x4(rep: Tuple[int, int]) -> List[Tuple[int, int]]:
    """
    4x4
    """
    return _scan_order_mxn(rep, (4, 4))

def _scan_order_mxn(rep: Tuple[int, int], skip: Tuple[int, int]) -> List[Tuple[int, int]]:
    """
    Scan every MxN pixels, and then restart, but from the next point (+1 in X, then +1 in Y)
    in the MxN initial area.
    skip: Tuple of 2 integers, the number of pixels to skip in X and Y between each point
    See _get_scan_order() for the "rep" parameter documentation
    """
    order = []
    # 3x3 base
    for y_base in range(skip[1]):
        for x_base in range(skip[0]):
            # Every 3rd pixel (within the repetition)
            for j in range(y_base, rep[1], skip[1]):  # Y slow
                for i in range(x_base, rep[0], skip[0]):  # X fast
                    order.append((i, j))

    return order


def _find_scan_order_strategies():
    """
    Find all functions that start with "scan_order_" and return them as a dictionary
    """
    strategies = {}
    for name, func in globals().items():
        if name.startswith("scan_order_") and callable(func):
            # Name is the first line of the docstring
            docstring = inspect.cleandoc(func.__doc__)
            friendly_name = docstring.split("\n")[0].strip()
            strategies[friendly_name] = func
    return strategies


class SpectrumArbitraryOrderSettingsStream(SpectrumSettingsStream):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._scan_strategies = _find_scan_order_strategies()
        default_strategy = sorted(self._scan_strategies.keys())[0]
        self.scanStrategy = model.VAEnumerated(default_strategy, choices=self._scan_strategies.keys())


class SEMSpectrumArbitraryOrderMDStream(SEMSpectrumMDStream):
    """
    Same as Spectrum stream, but different scan order. The idea is that for some samples it might
    be better to not immediately scan the pixel adjacent to each other as there can be physical
    interaction. So instead, scan pixels in a different time order.
    The actual scan order is to be selected by modifying ._get_scan_order() (and making it
    call a different function that one of the two examples).
    """

    # overrides SEMCCDStream method, to disable hardware sync
    def _supports_hw_sync(self) -> bool:
        # arbitrary scan order does not support hardware sync (at least for now)
        return False

    # overrides SEMCCDStream method, to disable scan stage acquisition
    def _runAcquisitionScanStage(self, future):
        # This could be implemented, by overriding _getScanStagePositions(), but that's not yet done
        raise NotImplementedError("Arbitrary scan order does not support scan stage acquisition")

    def _get_scan_order(self, rep: Tuple[int, int]) -> List[Tuple[int, int]]:
        """
        Computes the arbitrary scan order
        :param rep: x,y number of pixels to scan
        :return: list of positions (x, y) as indices to scan, in the scan order
        """
        strategy = self._sccd.scanStrategy.value
        strategy_func = self._sccd._scan_strategies[strategy]
        return strategy_func(rep)

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
        pxs_rel = (width[0] / rep[0], width[1] / rep[1])
        lim = (roi[0] + pxs_rel[0] / 2, roi[1] + pxs_rel[1] / 2)

        shape = self._emitter.shape
        # convert into SEM translation coordinates: distance in px from center
        # (situated at 0.5, 0.5), can be floats
        pos00 = (shape[0] * (lim[0] - 0.5), shape[1] * (lim[1] - 0.5))
        pxs_sem = shape[0] * pxs_rel[0], shape[1] * pxs_rel[1]
        logging.debug("Generating points from %s with pxs %s, from rep %s and roi %s",
                      pos00, pxs_sem, rep, roi)

        pos = numpy.empty((rep[1], rep[0], 2), dtype=float)
        order = self._get_scan_order(rep)
        logging.debug("Arbitrary order: %s", order)
        self._px_order = order

        raw_order = numpy.ndindex(*rep[::-1])
        for raw_idx, px_idx in zip(raw_order, order):
            beam_pos = (pos00[0] + px_idx[0] * pxs_sem[0],
                        pos00[1] + px_idx[1] * pxs_sem[1],)
            pos[raw_idx] = beam_pos

        logging.debug("Will acquire at beam pos: %s", pos)
        return pos

    def _assembleLiveData(self, n: int, raw_data: "DataArray",
                          px_idx: Tuple[int, int], px_pos: Tuple[float, float],
                          rep: Tuple[int, int], pol_idx: int = 0):
        """
        Wrapper for _assembleLiveData() to convert back the standard px_idx (eg, (0,0), (0,1), (0,2)...)
        into the index that was actually scanned at that moment.
        :param px_idx: y, x position
        :param pxs_pos: position of center of data in m: x, y
        :param rep: x, y number of points in the scan
        For other parameters, see MultipleDetectorStream._assembleLiveData()
        """
        px_idx_flat = px_idx[0] * rep[0] + px_idx[1]
        act_px_idx = self._px_order[px_idx_flat][::-1]
        logging.debug("Converted back idx %s to %s", px_idx, act_px_idx)
        return super()._assembleLiveData(n, raw_data, act_px_idx, px_pos, rep, pol_idx)


class SpectrumArbitraryScanOrderPlugin(Plugin):
    name = "Spectrum acquisition in arbitrary scan order"
    __version__ = "1.1"
    __author__ = u"Éric Piel"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)
        # Can only be used with a SPARC with spectrometer and CCD camera
        main_data = self.main_app.main_data
        if not (microscope and main_data.role.startswith("sparc") and main_data.spectrometers):
            logging.info("%s plugin cannot load as the microscope is not a SPARC with spectrometer",
                         self.name)
            return

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

        # We "patch" the gui.conf.data for our special stream
        data.STREAM_SETTINGS_CONFIG[SpectrumArbitraryOrderSettingsStream] = (
            OrderedDict((
                ("scanStrategy", {
                    "label": "Scan strategy",
                    "tooltip": "Select the scan strategy to use",
                }),
                ("wavelength", {
                    "tooltip": "Center wavelength of the spectrograph",
                    "control_type": odemis.gui.CONTROL_FLT,
                    "range": (0.0, 1900e-9),
                    "key_step_min": 1e-9,
                }),
                ("grating", {}),
                ("slit-in", {
                    "label": "Input slit",
                    "tooltip": "Opening size of the spectrograph input slit.\nA wide opening means more light and a worse resolution.",
                }),
                ("filter", {  # from filter
                    "choices": util.format_band_choices,
                }),
            ))
        )

    def add_stream(self, name: str, detector: "Detector"):
        """
        Create a new spectrum stream and MDStream.
        :param name: Name of the new stream to be created
        :param detector: The spectrometer to use
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

        sr_stream = SpectrumArbitraryOrderSettingsStream(
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
