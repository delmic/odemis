# -*- coding: utf-8 -*-
"""
Created on 23 Aug 2012

@author: Éric Piel

Copyright © 2012-2013 Éric Piel & Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms  of the GNU General Public License version 2 as published by the Free
Software  Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY;  without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR  PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

# various functions to convert and modify images (DataArray and wxImage)

from __future__ import division

import logging
import math
from matplotlib import mlab, tri
import numpy
from odemis import model
from odemis import dataio
import scipy.misc
import wx


# Variables to be used in CropMirror and AngleResolved2Polar
# These values correspond to SPARC 2014
AR_XMAX = 13.25e-3  # m, the distance between the parabola origin and the cutoff position
AR_HOLE_DIAMETER = 0.6e-3  # m, diameter the hole in the mirror
AR_FOCUS_DISTANCE = 0.5e-3  # m, the vertical mirror cutoff, iow the distance between the mirror and the sample
AR_PARABOLA_F = 2.5e-3  # m, parabola_parameter=1/4f

def findOptimalRange(hist, edges, outliers=0):
    """
    Find the intensity range fitting best an image based on the histogram.
    hist (ndarray 1D of 0<=int): histogram
    edges (tuple of 2 numbers): the values corresponding to the first and last
      bin of the histogram. To get an index, use edges = (0, len(hist)).
    outliers (0<float<0.5): ratio of outliers to discard (on both side). 0
      discards no value, 0.5 discards every value (and so returns the median).
    """
    if outliers == 0:
        # short-cut if no outliers: find first and last non null value
        inz = numpy.flatnonzero(hist)
        idxrng = inz[0], inz[-1]
    else:
        # accumulate each bin into the next bin
        cum_hist = hist.cumsum()

        # find out how much is the value corresponding to outliers
        nval = cum_hist[-1]
        oval = int(round(outliers * nval))
        lowv, highv = oval, nval - oval

        # search for first bin equal or above lowv
        lowi = numpy.searchsorted(cum_hist, lowv, side="right")
        # if exactly lowv -> remove this bin too, otherwise include the bin
        if hist[lowi] == lowv:
            lowi += 1
        # same with highv (note: it's always found, so highi is always
        # within hist)
        highi = numpy.searchsorted(cum_hist, highv, side="left")

        idxrng = lowi, highi

    # convert index into intensity values
    a = edges[0]
    b = (edges[1] - edges[0]) / (hist.size - 1)
    rng = (a + b * idxrng[0], a + b * idxrng[1])
    return rng

def compactHistogram(hist, length):
    """
    Make a histogram smaller by summing bins together
    hist (ndarray 1D of 0<=int): histogram
    length (0<int<=hist.size): final length required. It must be a multiple of
     the length of hist
    return (ndarray 1D of 0<=int): histogram representing the same bins, but
      accumulated together as necessary to only have "length" bins.
    """
    if hist.size < length:
        raise ValueError("Cannot compact histogram of length %d to length %d",
                         hist.size, length)
    elif hist.size == length:
        return hist
    elif hist.size % length != 0:
        # Very costly (in CPU time) and probably a sign something went wrong
        logging.warning("Length of histogram = %d, not multiple of %d",
                         hist.size, length)
        # add enough zeros at the end to make it a multiple
        hist = numpy.concatenate(hist, numpy.zeros(length - hist.size % length))
    # Reshape to have on first axis the length, and second axis the bins which
    # must be accumulated.
    chist = hist.reshape(length, hist.size // length)
    return numpy.sum(chist, 1)

# TODO: compute histogram faster. There are several ways:
# * x=numpy.bincount(a.flat, minlength=depth) => fast (~0.03s for
#   a 2048x2048 array) but only works on flat array with uint8 and uint16 and
#   creates 2**16 bins if uint16 (so need to do a reshape and sum on top of it)
# * numpy.histogram(a, bins=256, range=(0,depth)) => slow (~0.09s for a
#   2048x2048 array) but works exactly as needed directly in every case.
# * see weave? (~ 0.01s for 2048x2048 array of uint16) eg:
#  timeit.timeit("counts=numpy.zeros((2**16), dtype=numpy.uint32);
#  weave.inline( code, ['counts', 'idxa'])", "import numpy;from scipy import weave; code=r\"for (int i=0; i<Nidxa[0]; i++) { COUNTS1( IDXA1(i)>>8)++; }\"; idxa=numpy.ones((2048*2048), dtype=numpy.uint16)+15", number=100)
# * see cython?
# for comparison, a.min() + a.max() are 0.01s for 2048x2048 array

def histogram(data, irange=None):
    """
    Compute the histogram of the given image.
    data (numpy.ndarray of numbers): greyscale image
    irange (None or tuple of 2 unsigned int): min/max values to be found
      in the data. None => auto (min, max will be detected from the data)
    return hist, edges:
     hist (ndarray 1D of 0<=int): number of pixels with the given value
      Note that the length of the returned histogram is not fixed. If irange
      is defined and data is integer, the length is always equal to
      irange[1] - irange[0] + 1.
     edges (tuple of numbers): lowest and highest bound of the histogram.
       edges[1] is included in the bin. If irange is defined, it's the same
       values.
    """
    if irange is None:
        if data.dtype.kind in "biu":
            idt = numpy.iinfo(data.dtype)
            irange = (idt.min, idt.max)
        else:
            # cast to ndarray to ensure a scalar (instead of a DataArray)
            irange = (numpy.array(data).min(), numpy.array(data).max())

    # short-cuts (for the most usual types)
    if data.dtype.kind in "biu" and irange[0] >= 0:
        # TODO: for int (irange[0] < 0), treat as unsigned, and swap the first
        # and second halves of the histogram.
        length = irange[1] - irange[0] + 1
        hist = numpy.bincount(data.flat, minlength=length)
        edges = (0, hist.size - 1)
        if edges[1] > irange[1]:
            logging.warning("Unexpected value %d outside of range", edges[1])
    else:
        if data.dtype.kind in "biu":
            length = irange[1] - irange[0] + 1
        else:
            # For floats, it will automatically find the minimum and maximum
            length = 256
        hist, all_edges = numpy.histogram(data, bins=length, range=irange)
        edges = (all_edges[0], all_edges[-1])

    return hist, edges

def FindOptimalBC(data, depth):
    """
    Computes the (mathematically) optimal brightness and contrast.

    It returns the brightness and contrast values used by DataArray2wxImage in
    auto contrast/brightness.

    :param data: (numpy.ndarray of unsigned int) 2D image greyscale
    :param depth: (1<int): maximum value possibly encoded (12 bits => 4096)
    :return: (-1<=float<=1, -1<=float<=1): brightness and contrast
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

# TODO: try to do cumulative histogram value mapping (=histogram equalization)?
# => might improve the greys, but might be "too" clever
def DataArray2RGB(data, irange=None, tint=(255, 255, 255)):
    """
    :param data: (numpy.ndarray of unsigned int) 2D image greyscale (unsigned
        float might work as well)
    :param irange: (None or tuple of 2 unsigned int) min/max intensities mapped
        to black/white
        None => auto (min, max are from the data);
        0, max val of data => whole range is mapped.
        min must be < max, and must be of the same type as data.dtype.
    :param tint: (3-tuple of 0 < int <256) RGB colour of the final image (each
        pixel is multiplied by the value. Default is white.
    :return: (numpy.ndarray of 3*shape of uint8) converted image in RGB with the
        same dimension
    """
    # TODO: add a depth value to override idt.max? (allows to avoid clip when
    #       not useful
    # TODO: handle signed values
    assert(len(data.shape) == 2) # => 2D with greyscale

    # fit it to 8 bits and update brightness and contrast at the same time
    if irange is None:
        # automatic scaling (not so fast as min and max must be found)
        drescaled = scipy.misc.bytescale(data)
    elif data.dtype == "uint8" and irange == (0, 255):
        # short-cut when data is already the same type
        logging.debug("Applying direct range mapping to RGB")
        drescaled = data
        # TODO: also write short-cut for 16 bits by reading only the high byte?
    else:
        # If data might go outside of the range, clip first
        if data.dtype.kind in "iu":
            # no need to clip if irange is the whole possible range
            idt = numpy.iinfo(data.dtype)
            # trick to ensure B&W if there is only one value allowed
            if irange[0] >= irange[1]:
                if irange[0] > idt.min:
                    irange = [irange[1] - 1, irange[1]]
                else:
                    irange = [irange[0], irange[0] + 1]
            if irange[0] > idt.min or irange[1] < idt.max:
                data = data.clip(*irange)
        else: # floats et al. => always clip
            # TODO: might not work correctly if range is in middle of data
            # values trick to ensure B&W image
            if irange[0] >= irange[1] and irange[0] > float(data.min()):
                force_white = True
            else:
                force_white = False
            data = data.clip(*irange)
            if force_white:
                irange = [irange[1] - 1, irange[1]]
        drescaled = scipy.misc.bytescale(data, cmin=irange[0], cmax=irange[1])


    # Now duplicate it 3 times to make it rgb (as a simple approximation of
    # greyscale)
    # dstack doesn't work because it doesn't generate in C order (uses strides)
    # apparently this is as fast (or even a bit better):

    # 0 copy (1 malloc)
    rgb = numpy.empty(data.shape + (3,), dtype="uint8", order='C')

    # Tint (colouration)
    if tint == (255, 255, 255):
        # fast path when no tint
        # Note: it seems numpy.repeat() is 10x slower ?!
        # a = numpy.repeat(drescaled, 3)
        # a.shape = data.shape + (3,)
        rgb[:, :, 0] = drescaled # 1 copy
        rgb[:, :, 1] = drescaled # 1 copy
        rgb[:, :, 2] = drescaled # 1 copy
    else:
        rtint, gtint, btint = tint
        # multiply by a float, cast back to type of out, and put into out array
        # TODO: multiplying by float(x/255) is the same as multiplying by int(x)
        #       and >> 8
        numpy.multiply(drescaled, rtint / 255, out=rgb[:, :, 0])
        numpy.multiply(drescaled, gtint / 255, out=rgb[:, :, 1])
        numpy.multiply(drescaled, btint / 255, out=rgb[:, :, 2])

    return rgb

# Deprecated
def DataArray2wxImage(data, depth=None, brightness=None, contrast=None,
                      tint=(255, 255, 255)):
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
    # TODO: handle signed values
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
        # always be expressed between -1 and 1
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

def ensure2DImage(data):
    """
    Reshape data to make sure it's 2D by trimming all the low dimensions (=1).
    Odemis' convention is to have data organized as CTZYX. If CTZ=111, then it's
    a 2D image, but it has too many dimensions for functions which want only 2D.
    data (DataArray): the data to reshape
    return DataArray: view to the same data but with 2D shape
    raise ValueError: if the data is not 2D (CTZ != 111)
    """
    d = data.view()
    if len(d.shape) < 2:
        d.shape = (1,) * (2 - len(d.shape)) + d.shape
    elif len(d.shape) > 2:
        d.shape = d.shape[-2:] # raise ValueError if it will not work

    return d

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


# TODO: use VIPS to be fast?
def Average(images, rect, mpp, merge=0.5):
    """
    mix the given images into a big image so that each pixel is the average of each
     pixel (separate operation for each colour channel).
    images (list of InstrumentalImages)
    merge (0<=float<=1): merge ratio of the first and second image (IOW: the
      first image is weighted by merge and second image by (1-merge))
    """
    # TODO: is ok to have a image = None?


    # TODO: (once the operator callable is clearly defined)
    raise NotImplementedError()


def AngleResolved2Polar(data, output_size, hole=True):
    """
    Converts an angle resolved image to polar representation
    data (model.DataArray): The image that was projected on the CCD after being
      relefted on the parabolic mirror. The flat line of the D shape is
      expected to be horizontal, at the top. It needs PIXEL_SIZE and AR_POLE
      metadata. Pixel size is the sensor pixel size * binning / magnification.
    output_size (int): The size of the output DataArray (assumed to be square)
    hole (boolean): Crop the pole if True
    returns (model.DataArray): converted image in polar view
    """
    assert(len(data.shape) == 2)  # => 2D with greyscale
    image = data

    # Get the metadata
    try:
        pixel_size = data.metadata[model.MD_PIXEL_SIZE]
        mirror_x, mirror_y = data.metadata[model.MD_AR_POLE]
    except KeyError:
        raise ValueError("Metadata required: MD_PIXEL_SIZE, MD_AR_POLE.")

    # Crop the input image to half circle
    cropped_image = CropHalfCircle(image, pixel_size, mirror_y)

    # Round to the nearest even number
    # FIXME: result.shape is output_size + 1. Need to either acept odd number,
    # and maybe also make it working with even numbers?
    h_output_size = int(output_size / 2)
    if output_size % 2 == 0:
        logging.warn("Even number as output size. It will be rounded to %d.",
                     2 * h_output_size + 1)

    theta_data = numpy.empty(shape=cropped_image.shape)
    phi_data = numpy.empty(shape=cropped_image.shape)
    omega_data = numpy.empty(shape=cropped_image.shape)

    # For each pixel of the input ndarray, input metadata is used to
    # calculate the corresponding theta, phi and radiant intensity
    image_x, image_y = cropped_image.shape
    jj = numpy.linspace(0, image_y - 1, image_y)
    xpix = mirror_x - jj

    for i in xrange(image_x):
        ypix = (i - mirror_y) + (2 * AR_PARABOLA_F) / pixel_size[1]
        theta, phi, omega = FindAngle(xpix, ypix, pixel_size)

        theta_data[i, :] = theta
        phi_data[i, :] = phi
        omega_data[i, :] = cropped_image[i] / omega

    # Convert into polar coordinates
    theta = theta_data * (h_output_size / math.pi * 2)
    phi = phi_data
    theta_data = numpy.cos(phi) * theta
    phi_data = numpy.sin(phi) * theta

    # Interpolation into 2d array
#    xi = numpy.linspace(-h_output_size, h_output_size, 2 * h_output_size + 1)
#    yi = numpy.linspace(-h_output_size, h_output_size, 2 * h_output_size + 1)
#    qz = mlab.griddata(phi_data.flat, theta_data.flat, omega_data.flat, xi, yi, interp="linear")

    # FIXME: need rotation (=swap axes), but swapping theta/phi slows down the
    # interpolation by 3 ?!
    triang = tri.delaunay.Triangulation(theta_data.flat, phi_data.flat)
    interp = triang.linear_interpolator(omega_data.flat, default_value=0)
    qz = interp[-h_output_size:h_output_size:complex(0, 2 * h_output_size + 1), # Y
                -h_output_size:h_output_size:complex(0, 2 * h_output_size + 1)] # X
    qz = qz.swapaxes(0, 1)[:, ::-1] # rotate by 90°
    result = model.DataArray(qz, image.metadata)

    return result


def FindAngle(xpix, ypix, pixel_size):
    """
    For given pixels, finds the angle of the corresponding ray 
    xpix (numpy.array): x coordinates of the pixels
    ypix (float): y coordinate of the pixel
    pixel_size (2 floats): CCD pixelsize (X/Y)
    returns (3 numpy.arrays): theta, phi (the corresponding spherical coordinates for each pixel in ccd) 
                              and omega (solid angle)
    """
    y = xpix * pixel_size[0]
    z = ypix * pixel_size[1]
    r2 = y ** 2 + z ** 2
    xfocus = (1 / (4 * AR_PARABOLA_F)) * r2 - AR_PARABOLA_F
    xfocus2plusr2 = xfocus ** 2 + r2
    sqrtxfocus2plusr2 = numpy.sqrt(xfocus2plusr2)
    
    # theta
    theta = numpy.arccos(z / sqrtxfocus2plusr2)
    
    # phi
    phi = numpy.arctan2(y, xfocus) % (2 * math.pi)

    # omega
#    omega = (pixel_size[0] * pixel_size[1]) * ((1 / (2 * AR_PARABOLA_F)) * r2 - xfocus) / (sqrtxfocus2plusr2 * xfocus2plusr2)
    omega = (pixel_size[0] * pixel_size[1]) * ((1 / (4 * AR_PARABOLA_F)) * r2 + AR_PARABOLA_F) / (sqrtxfocus2plusr2 * xfocus2plusr2)

    return theta, phi, omega


def CropHalfCircle(data, eff_pixel_size, mirror_y, hole=True):
    """
    Crops the image to half circle shape based on AR_FOCUS_DISTANCE and AR_XMAX
    data (model.DataArray): The DataArray with the image, will be modified
    eff_pixel_size (float): pixel_size * binning / magnification # m
    mirror_y (float): y coordinate of MD_AR_POLE
    hole (boolean): Crop the pole if True
    returns (model.DataArray): Cropped image
    """
    X, Y = data.shape
    center_x, y = data.metadata[model.MD_AR_POLE]

    # Calculate the y coordinate of the cutoff of half circle
    center_y = mirror_y - ((2 * AR_PARABOLA_F - AR_FOCUS_DISTANCE) / eff_pixel_size[1])

    # Compute the radius
    r = (2 * math.sqrt(AR_XMAX * AR_PARABOLA_F)) / eff_pixel_size[1]
    y, x = numpy.ogrid[-center_y:X - center_y, -center_x:Y - center_x]
    circle_mask = x * x + y * y <= r * r

    # Create half circle mask
    circle_mask[:center_y, :] = False
    image = numpy.where(circle_mask, data, 0)
    
    # Crop the pole making hole of AR_HOLE_DIAMETER
    if hole == True:
        r = (AR_HOLE_DIAMETER / 2) / eff_pixel_size[1]
        y, x = numpy.ogrid[-mirror_y:X - mirror_y, -center_x:Y - center_x]
        circle_mask_hole = x * x + y * y <= r * r
        image = numpy.where(circle_mask_hole, 0, image)

    return image
