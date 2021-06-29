#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 19 Apr 2021

Copyright © 2021 Philip Winkler, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""
from odemis import model
import numpy


class FastEMROA(object):
    """ Representation of a FastEM ROA (region of acquisition). """

    def __init__(self, name, coordinates, roc):
        """
        :param name: (str) name of the ROA
        :param coordinates: (float, float, float, float) l, t, r, b coordinates in m
        :param roc: (FastEMROC) corresponding region of calibration
        """
        self.name = model.StringVA(name)
        self.coordinates = model.TupleContinuous(coordinates,
                                                 range=((-1, -1, -1, -1), (1, 1, 1, 1)),
                                                 cls=(int, float),
                                                 unit='m')
        self.roc = model.VigilantAttribute(roc)


class FastEMROC(object):
    """ Representation of a FastEM ROC (region of calibration). """

    def __init__(self, name, coordinates):
        """
        :param name: (str) name of the ROC
        :param coordinates: (float, float, float, float) l, t, r, b coordinates in m
        """
        self.name = model.StringVA(name)
        self.coordinates = model.TupleContinuous(coordinates,
                                                 range=((-1, -1, -1, -1), (1, 1, 1, 1)),
                                                 cls=(int, float),
                                                 unit='m')
        self.parameters = None  # calibration object with all relevant parameters


# TODO: replace fake testing functions with actual acquisition
import time
# The executor is a single object, independent of how many times the module is loaded.
_executor = model.CancellableThreadPoolExecutor(max_workers=1)
def acquire(roa, path):
    """
    :param roa: (FastEMROA) acquisition region to be acquired
    :param path: (str) path and filename of the acquisition on the server
    :returns: (ProgressiveFuture): acquisition future
    """
    # TODO: pass path through attribute on ROA instead of second argument?
    f = model.ProgressiveFuture()
    _executor.submitf(f, _run_fake_acquisition)
    return f


def _run_fake_acquisition():
    time.sleep(2)


def estimateTime(roas):
    return len(roas) * 2


# Overview acquisition
def acquireTiledArea(stream, stage, area, live_stream=None):
    f = model.ProgressiveFuture()
    _executor.submitf(f, _run_fake_overview_acquisition, stream, stage, area, live_stream)
    return f


def _run_fake_overview_acquisition(stream, stage, area, live_stream):
    """
    :param coords: (float, float, float, float) minx, miny, maxx, maxy coordinates of overview region
    :param sem_stream (SEMStream):
    :returns: (DataArray)
    """
    time.sleep(2)
    d = model.DataArray(numpy.random.random((500, 500)))
    return d
