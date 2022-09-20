# -*- coding: utf-8 -*-
'''
Created on 10 Mar 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
# Optimised versions of the functions of odemis.util.img

import cython

# import both numpy and the Cython declarations for numpy
import numpy
cimport numpy

# TODO: use fused types to support multiple types (but needs Cython >= 0.20)
ctypedef numpy.uint16_t uint16_t

# nogil allows multi-threading but prevents use of any Python objects or call
@cython.cdivision(True)
cdef void cDataArray2RGB(uint16_t* data, int datalen, uint16_t irange0, uint16_t irange1,
                         int* tint, numpy.uint8_t* ret) nogil:
    cdef double b = 255. / <double>(irange1 - irange0)
    cdef double br = (b * <double>tint[0]) / 255.
    cdef double bg = (b * <double>tint[1]) / 255.
    cdef double bb = (b * <double>tint[2]) / 255.

    cdef numpy.uint8_t di
    cdef double df
    cdef int retpos = 0

    if tint[0] == tint[1] == tint[2] == 255:
        # optimised version, without tinting (about 2x faster)
        for i in range(datalen):
            # clip
            if data[i] <= irange0:
                di = 0
            elif data[i] >= irange1:
                di = 255
            else:
                di = <numpy.uint8_t> ((data[i] - irange0) * b + 0.5)
            ret[retpos] = di
            retpos += 1
            ret[retpos] = di
            retpos += 1
            ret[retpos] = di
            retpos += 1
    else:
        for i in range(datalen):
            # clip
            if data[i] <= irange0:
                ret[retpos] = 0
                retpos += 1
                ret[retpos] = 0
                retpos += 1
                ret[retpos] = 0
                retpos += 1
            elif data[i] >= irange1:
                ret[retpos] = tint[0]
                retpos += 1
                ret[retpos] = tint[1]
                retpos += 1
                ret[retpos] = tint[2]
                retpos += 1
            else:
                df = (data[i] - irange0)
                ret[retpos] = <numpy.uint8_t> (df * br + 0.5)
                retpos += 1
                ret[retpos] = <numpy.uint8_t> (df * bg + 0.5)
                retpos += 1
                ret[retpos] = <numpy.uint8_t> (df * bb + 0.5)
                retpos += 1

# This function is probably not needed, but I have no idea how to instantiate
# a numpy array which can be passed as a pointer
@cython.boundscheck(False)
@cython.wraparound(False)
def wrapDataArray2RGB(numpy.ndarray[uint16_t, ndim=2] data not None,
                  irange,
                  tint,
                  numpy.ndarray[numpy.uint8_t, ndim=3] ret not None):
    cdef int ctint[3]
    ctint[0] = tint[0]
    ctint[1] = tint[1]
    ctint[2] = tint[2]
    cDataArray2RGB(&data[0,0], data.size, irange[0], irange[1], ctint, &ret[0,0,0])


def DataArray2RGB(data, irange, tint=(255, 255, 255)):
    if not data.flags.c_contiguous:
        raise ValueError("Optimised version only works with C-contiguous arrays")
    if data.dtype != numpy.uint16:
        # Note: cython automatically detects such errors, but it seems that with
        # ctyhon 0.23, it can leak memory.
        raise ValueError("Optimised version only works on uint16 (got %s)" % (data.dtype,))
    # Note: we could also make an optimised version for F-contiguous arrays,
    # but it's not clear when it'd be useful. For more complex arrays, it's also
    # probably possible to generate a faster version than numpy, but I don't
    # know how.
    if irange[0] >= irange[1]:
        raise ValueError("irange needs to be a tuple of low/high values")
    ret = numpy.empty(data.shape + (3,), dtype=numpy.uint8)
    wrapDataArray2RGB(data, irange, tint, ret)
    return ret

