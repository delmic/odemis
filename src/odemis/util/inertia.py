# -*- coding: utf-8 -*-
'''
Created on 24 Aug 2015

@author: Kimon Tsitsikas

Copyright © 2015 Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

import numpy
from odemis import model
from odemis.util import img


def CalculateMomentOfInertia(raw_data, background):
    """
    Calculates the moment of inertia for a given optical image
    raw_data (model.DataArray): The optical image
    background (model.DataArray): Background image that we use for substraction
    returns (float): moment of inertia
    """
    depth = 2 ** background.metadata.get(model.MD_BPP, background.dtype.itemsize * 8)
    hist, edges = img.histogram(background, (0, depth - 1))
    range_max = img.findOptimalRange(hist, edges, outliers=1e-06)[1]
    # 1.3 corresponds to 3 times the noise
    # data = numpy.clip(raw_data - 1.3 * background, 0, numpy.inf)
    # alternative background substraction
    data = numpy.clip(raw_data - range_max, 0, numpy.inf)
    rows, cols = data.shape
    x = numpy.linspace(1, cols, num=cols)
    y = numpy.linspace(1, rows, num=rows)
    ysum = numpy.dot(data.T, y).T.sum()
    xsum = numpy.dot(data, x).sum()

    data_sum = data.sum(dtype=numpy.int64)
    cY = ysum / data_sum
    cX = xsum / data_sum
    xx = (x - cX) ** 2
    yy = numpy.power(y - cY, 2)
    XX = numpy.ndarray(shape=(rows, cols))
    YY = numpy.ndarray(shape=(rows, cols))
    XX[:] = xx
    YY.T[:] = yy
    diff = XX + YY
    totDist = numpy.sqrt(diff)
    rmsDist = data * totDist
    Mdist = rmsDist.sum() / data_sum
    return Mdist
