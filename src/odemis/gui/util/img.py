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
from __future__ import division
import logging
import math
import numpy
import scipy.misc
import wx

# various functions to convert and modify images (DataArray and wxImage)

# TODO: compute histogram. There are 3 ways in numpy:
# * x=numpy.bincount(a, minlength=depth);x.min();x.max() => fast (~0.03s for 
#   a 2048x2048 array) but only works only on flat array
#   with uint8 and uint16 and creates 2**16 bins if uint16 (so need to do a 
#   bytescale + a second bincount)
# * numpy.histogram(a, bins=256, range=(0,depth)) => slow (~0.09s for a 
#   2048x2048 array) but works exactly as needed directly in every case.
# * see weave? (~ 0.01s for 2048x2048 array) eg: 
#  timeit.timeit("counts=numpy.zeros((2**16), dtype=numpy.uint32); weave.inline( code, ['counts', 'idxa'])", "import numpy;from scipy import weave; code=r\"for (int i=0; i<Nidxa[0]; i++) { COUNTS1( IDXA1(i)>>8)++; }\"; idxa=numpy.ones((2048*2048), dtype=numpy.uint16)+15", number=100) 
# for comparison, a.min() + a.max() are 0.01s for 2048x2048 array

def FindOptimalBC(data, depth):
    """
    Computes the (mathematically) optimal brightness and contrast. It returns the
    brightness and contrast values used by DataArray2wxImage in auto contrast/
    brightness.
    data (numpy.ndarray of unsigned int): 2D image greyscale
    depth (1<int): maximum value possibly encoded (12 bits => 4096)
    returns (-1<=float<=1, -1<=float<=1): brightness and contrast 
    """
    assert(depth >= 1)

    # inverse algorithm than in DataArray2wxImage(), using the min/max
    hd = (depth-1)/2
    d0 = float(data.min())
    d255 = float(data.max())

    if d255 == d0:
        # infinite contrast => clip to 1
        C = depth
    else:
        C = (depth - 1) / (d255 - d0)
    B = hd - (d0 + d255)/2

    brightness = B / (depth - 1)
    contrast = math.log(C, depth)

    return brightness, contrast 

def DataArray2wxImage(data, depth=None, brightness=None, contrast=None, tint=(255, 255, 255)):
    """
    data (numpy.ndarray of unsigned int): 2D image greyscale (unsigned float might work as well)
    depth (None or 1<int): maximum value possibly encoded (12 bits => depth=4096)
        Note: if brightness and contrast auto it is not required.
    brightness (None or -1<=float<=1): brightness change.
        None => auto. 0 => no change. -1 => fully black, 1 => fully white
    contrast  (None or -1<=float<=1): contrast change.
        None => auto. 0 => no change. -1 => fully grey, 1 => white/black only
    Note: if auto, both contrast and brightness must be None
    tint (3-tuple of 0 < int <256): RGB colour of the final image (each pixel is 
        multiplied by the value. Default is white. 
    returns (wxImage): rgb (888) converted image with the same dimension
    """
    assert(len(data.shape) == 2) # => 2D with greyscale
    
    # fit it to 8 bits and update brightness and contrast at the same time 
    if brightness is None and contrast is None:
        drescaled = scipy.misc.bytescale(data)
    elif brightness == 0 and contrast == 0:
        assert(depth is not None)
        logging.debug("Applying brightness and contrast 0 with depth = %d", depth)
        if depth == 256:
            drescaled = data
        else:
            drescaled = scipy.misc.bytescale(data, cmin=0, cmax=depth-1)
    else:
        # manual brightness and contrast
        assert(depth is not None)
        assert(contrast is not None)
        assert(brightness is not None)
        logging.debug("Applying brightness %f and contrast %f with depth = %d", brightness, contrast, depth)
        # see http://docs.opencv.org/doc/tutorials/core/basic_linear_transform/basic_linear_transform.html
        # and http://pippin.gimp.org/image-processing/chap_point.html
        # However we apply brightness first (before contrast) so that it can
        # always be experessed between -1 and 1
        # contrast is between 1/(depth) -> (depth): = depth^our_contrast 
        # brightness: newpixel = origpix + brightness*(depth-1)
        # contrast: newpixel = (origpix - depth-1/2) * contrast + depth-1/2
        # truncate
        # in Python this is:
        # corrected = (data + (brightness * (depth-1)) - (depth-1)/2.0) * (depth ** contrast) + (depth-1)/2.0 
        # numpy.clip(corrected, 0, depth, corrected) # inplace
        # drescaled_orig = scipy.misc.bytescale(corrected, cmin=0, cmax=depth-1)

        # There are 2 ways to speed it up:
        # * lookup table (not tried)
        # * use the fact that it's a linear transform, like bytescale (that's what we do) => 30% speed-up 
        #   => finc cmin (origpix when newpixel=0) and cmax (origpix when newpixel=depth-1)
        B = brightness * (depth - 1)
        C = depth ** contrast
        hd = (depth - 1) / 2
        d0 = hd - B - hd/C
        d255 = hd - B + hd/C
        # bytescale: linear mapping cmin, cmax -> low, high; and then take the low byte (can overflow)
        # Note: always do clipping, because it's relatively cheap and d0 >0 or d255 < depth is only corner case
        drescaled = scipy.misc.bytescale(data.clip(d0, d255), cmin=d0, cmax=d255)
        

    # Now duplicate it 3 times to make it rgb (as a simple approximation of greyscale)
    # dstack doesn't work because it doesn't generate in C order (uses strides)
    # apparently this is as fast (or even a bit better):
    rgb = numpy.empty(data.shape + (3,), dtype="uint8", order='C') # 0 copy (1 malloc)
    
    # Tint (colouration)
    if tint == (255, 255, 255):
        # fast path when no tint
        # TODO: try numpy.tile(drescaled, 3)
        rgb[:,:,0] = drescaled # 1 copy
        rgb[:,:,1] = drescaled # 1 copy
        rgb[:,:,2] = drescaled # 1 copy
    else:
        rtint, gtint, btint = tint
        # multiply by a float, cast back to type of out, and put into out array
        numpy.multiply(drescaled, rtint / 255, out=rgb[:,:,0])
        numpy.multiply(drescaled, gtint / 255, out=rgb[:,:,1])
        numpy.multiply(drescaled, btint / 255, out=rgb[:,:,2])

    return NDImage2wxImage(rgb)

# Note: it's also possible to directly generate a wx.Bitmap from a buffer, but
# always implies a memory copy.
def NDImage2wxImage(image):
    assert(len(image.shape) == 3 and image.shape[2] == 3)
    size = image.shape[1::-1]
    return wx.ImageFromBuffer(*size, dataBuffer=image) # 0 copy

def wxImage2NDImage(image, keep_alpha=True):
    """
    Converts a wx.Image into a numpy array.
    image (wx.Image): the image to convert of size MxN
    keep_alpha (boolean): keep the alpha channel when converted 
    returns (numpy.ndarray): a numpy array of shape NxMx3 (RGB) or NxMx4 (RGBA) 
    Note: Alpha not yet supported.
    """
    if keep_alpha and image.HasAlpha():
        shape = image.Height, image.Width, 4
        raise NotImplementedError()
    else:
        shape = image.Height, image.Width, 3

    return numpy.ndarray(buffer=image.DataBuffer, shape=shape, dtype=numpy.uint8) 


# TODO use VIPS to be fast?
def Average(images, rect, mpp, merge=0.5):
    """
    mix the given images into a big image so that each pixel is the average of each
     pixel (separate operation for each colour channel).
    images (list of InstrumentalImages)
    merge (0<=float<=1): merge ratio of the first and second image (IOW: the
      first image is weighted by merge and second image by (1-merge))
    """
    # TODO is ok to have a image = None?
    
    
    # TODO (once the operator callable is clearly defined)
    raise NotImplementedError()

