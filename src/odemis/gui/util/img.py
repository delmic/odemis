# -*- coding: utf-8 -*-
'''
Created on 23 Aug 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or
modify it under the terms of the GNU General Public License as published by the
Free Software Foundation, either version 2 of the License, or (at your option)
any later version.

Odemis is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
'''
from odemis.gui.log import log
import numpy
import scipy
import time
import wx

# various functions to convert and modify images (DataArray and wxImage)

def DataArray2wxImage(data, depth=None, brightness=None, contrast=None):
    """
    data (DataArray of unsigned int): 2D image greyscale (unsigned float might work as well)
    depth (None or 1<int): maximum value possibly encoded (12 bits => 4096)
        None => brightness and contrast auto
    brightness (None or -1<=float<=1): brightness change.
        None => auto. 0 => no change. -1 => fully black
    contrast  (None or -1<=float<=1): contrast change.
        None => auto. 0 => no change. -1 => fully grey, 1 => white/black only
    Note: if auto, both contrast and brightness must be None
    returns (wxImage): rgb (888) converted image with the same dimension
    """
    assert(len(data.shape) == 2) # => 2D with greyscale
    size = data.shape[0:2]
    
    # fit it to 8 bits and update brightness and contrast at the same time 
    if brightness is None and contrast is None:
        drescaled = scipy.misc.bytescale(data)
    elif brightness == 0 and contrast == 0:
        assert(depth is not None)
        log.info("Applying brightness and contrast 0 with depth = %d", depth)
        if depth == 256:
            drescaled = data
        else:
            drescaled = scipy.misc.bytescale(data, cmin=0, cmax=depth)
    else:
        # manual brightness and contrast
        assert(depth is not None)
        assert(contrast is not None)
        assert(brightness is not None)
        log.info("Applying brightness %f and contrast %f with depth = %d", brightness, contrast, depth)
        # see http://docs.opencv.org/doc/tutorials/core/basic_linear_transform/basic_linear_transform.html
        # and http://pippin.gimp.org/image-processing/chap_point.html
        # contrast is typically between 1/(depth/2) -> depth/2: = (depth/2)^our_contrast 
        # brightness: newpixel = origpix + brightness*depth
        # contrast: newpixel = (origpix - depth/2) * contrast + depth/2
        # truncate
        # in Python this is:
        # corrected = (data - depth/2.0) * ((depth/2.0) ** contrast) + (depth/2.0 + brightness * depth)
        # numpy.clip(corrected, 0, depth, corrected) # inplace
        # drescaled_orig = scipy.misc.bytescale(corrected, cmin=0, cmax=depth)

        # There are 2 ways to speed it up:
        # * lookup table (not tried)
        # * use the fact that it's a linear transform, like bytescale (that's what we do) => 30% speed-up 
        hd = depth/2.0
        a = hd ** contrast
        b = hd * a - (hd + brightness * depth)
        d0 = b/a
        d256 = (b + depth)/a
        # bytescale: linear mapping cmin, cmax -> low, high; and then take the low byte (can overflow)
        drescaled = scipy.misc.bytescale(data.clip(d0, d256), cmin=d0, cmax=d256)
        

    # TODO: shall we also handle colouration here?
        
    # Now duplicate it 3 times to make it rgb (as a simple approximation of greyscale)
    # dstack doesn't work because it doesn't generate in C order (uses strides)
    # apparently this is as fast (or even a bit better):
    rgb = numpy.empty(size + (3,), dtype="uint8") # 0 copy (1 malloc)
    rgb[:,:,0] = drescaled # 1 copy
    rgb[:,:,1] = drescaled # 1 copy
    rgb[:,:,2] = drescaled # 1 copy
    return wx.ImageFromBuffer(*size, dataBuffer=rgb) # 0 copy
