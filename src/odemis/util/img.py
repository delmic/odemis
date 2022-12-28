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

# various functions to convert and modify images (as DataArray)

import logging
import math
import numpy
from odemis import model
import scipy.ndimage
import cv2
import copy
from odemis.model import DataArray
from odemis.model import MD_DWELL_TIME, MD_EXP_TIME, TINT_FIT_TO_RGB, TINT_RGB_AS_IS
from odemis.util import get_best_dtype_for_acc, transform
from odemis.util.conversion import get_img_transformation_matrix, rgb_to_frgb
from typing import Tuple

import matplotlib.colors as colors
from matplotlib import cm

# See if the optimised (cython-based) functions are available
try:
    from odemis.util import img_fast
except ImportError:
    logging.warning("Failed to load optimised functions, slow version will be used.")
    img_fast = None

# This is a weave-based optimised version (but weave requires g++ installed)
#def DataArray2RGB_fast(data, irange, tint=(255, 255, 255)):
#    """
#    Do not call directly, use DataArray2RGB.
#    Fast version of DataArray2RGB, which is based on C code
#    """
#    # we use weave to do the assignment in C code
#    # this only gets compiled on the first call
#    import scipy.weave as weave
#    # ensure it's a basic ndarray, otherwise it confuses weave
#    data = data.view(numpy.ndarray)
#    w, h = data.shape
#    ret = numpy.empty((w, h, 3), dtype=numpy.uint8)
#    assert irange[0] < irange[1]
#    irange = numpy.array(irange, dtype=data.dtype) # ensure it's the same type
#    tintr = numpy.array([t / 255 for t in tint], dtype=numpy.float)
#
#    # TODO: special code when tint == white (should be 2x faster)
#    code = """
#    int impos=0;
#    int retpos=0;
#    float b = 255. / float(irange[1] - irange[0]);
#    float d;
#    for(int j=0; j<Ndata[1]; j++)
#    {
#        for (int i=0; i<Ndata[0]; i++)
#        {
#            // clip
#            if (data[impos] <= irange[0]) {
#                d = 0;
#            } else if (data[impos] >= irange[1]) {
#                d = 255;
#            } else {
#                d = float(data[impos] - irange[0]) * b;
#            }
#            // Note: can go x2 faster if tintr is skipped
#            ret[retpos++] = d * tintr[0];
#            ret[retpos++] = d * tintr[1];
#            ret[retpos++] = d * tintr[2];
#            impos++;
#        }
#    }
#    """
#    weave.inline(code, ["data", "ret", "irange", "tintr"])
#    return ret


def tint_to_md_format(tint):
    """
    Given a tint of a stream, which could be an RGB tuple or colormap object,
    put it into the format for metadata storage
    tint argument can be:
    - a list tuple RGB value (for a tint) or
    - a matplotlib.colors.Colormap object for a custom color map or
    - a string of value TINT_FIT_TO_RGB to indicate fit RGB color mapping
    - a string of value TINT_RGB_AS_IS that indicates no tint. Will be converted to a black tint
    returns (string or tuple) the tint name for metadata
    """
    if isinstance(tint, tuple) or isinstance(tint, list):
        return tint
    elif isinstance(tint, colors.Colormap):
        return tint.name
    elif tint in (TINT_FIT_TO_RGB, TINT_RGB_AS_IS):
        return tint
    else:
        raise ValueError("Unexpected tint %s" % (tint,))


def md_format_to_tint(user_tint):
    """
    Given a string or tuple value of user_tint in saved metadata, convert to a tint object
    Returns tint as:
    - a list tuple RGB value (for a tint)
    - a matplotlib.colors.Colormap object for a custom color map
    - a string of value TINT_FIT_TO_RGB to indicate fit RGB color mapping
    """
    if isinstance(user_tint, tuple) or isinstance(user_tint, list):
        return user_tint
    elif isinstance(user_tint, str):
        if user_tint != TINT_FIT_TO_RGB:
            try:
                return cm.get_cmap(user_tint)
            except NameError:
                raise ValueError("Invalid tint metadata colormap value %s" % (user_tint,))
        else:
            return TINT_FIT_TO_RGB
    else:
        raise TypeError("Invalid tint metadata type %s" % (user_tint,))


def findOptimalRange(hist, edges, outliers=0):
    """
    Find the intensity range fitting best an image based on the histogram.
    hist (ndarray 1D of 0<=int): histogram
    edges (tuple of 2 numbers): the values corresponding to the first and last
      bin of the histogram. To get an index, use edges = (0, len(hist)).
    outliers (0<float<0.5): ratio of outliers to discard (on both side). 0
      discards no value, 0.5 discards every value (and so returns the median).
    return (tuple of 2 values): the range (min and max values)
    """
    # If we got an histogram with only one value, don't try too hard.
    if len(hist) < 2:
        return edges

    if outliers == 0:
        # short-cut if no outliers: find first and last non null value
        inz = numpy.flatnonzero(hist)
        try:
            idxrng = inz[0], inz[-1]
        except IndexError:
            # No non-zero => data had no value => histogram of an empty array
            return edges
    else:
        # accumulate each bin into the next bin
        cum_hist = hist.cumsum()
        nval = cum_hist[-1]

        # If it's an histogram of an empty array, don't try too hard.
        if nval == 0:
            return edges

        # trick: if there are lots (>1%) of complete black and not a single
        # value just above it, it's a sign that the black is not part of the
        # signal and so is all outliers
        if hist[1] == 0 and cum_hist[0] / nval > 0.01 and cum_hist[0] < nval:
            cum_hist -= cum_hist[0] # don't count 0's in the outliers
            nval = cum_hist[-1]

        # find out how much is the value corresponding to outliers
        oval = int(round(outliers * nval))
        lowv, highv = oval, nval - oval

        # search for first bin equal or above lowv
        lowi = numpy.searchsorted(cum_hist, lowv, side="right")
        if hist[lowi] == lowv:
            # if exactly lowv -> remove this bin too, otherwise include the bin
            lowi += 1
        # same with highv (note: it's always found, so highi is always
        # within hist)
        highi = numpy.searchsorted(cum_hist, highv, side="left")

        idxrng = lowi, highi

    # convert index into intensity values
    a = edges[0]
    b = (edges[1] - edges[0]) / (hist.size - 1)
    # TODO: rng should be the same type as edges
    rng = (a + b * idxrng[0], a + b * idxrng[1])
    return rng


def getOutliers(data, outliers=0):
    """
    Finds the minimum and maximum values when discarding a given percentage of outliers.
    :param data: (DataArray) The data containing the image.
    :param outliers: (0<float<0.5) Ratio of outliers to discard (on each side).
                      0 discards no value, 0.5 discards every value (and so returns the median).
    :return: (tuple of 2 values) The range (min and max value).
    """
    hist, edges = histogram(data)

    return findOptimalRange(hist, edges, outliers)


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
        raise ValueError("Cannot compact histogram of length %d to length %d" %
                         hist.size, length)
    elif hist.size == length:
        return hist
    elif hist.size % length != 0:
        # Very costly (in CPU time) and probably a sign something went wrong
        logging.warning("Length of histogram = %d, not multiple of %d",
                         hist.size, length)
        # add enough zeros at the end to make it a multiple
        hist = numpy.append(hist, numpy.zeros(length - hist.size % length, dtype=hist.dtype))
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
            if data.itemsize > 2:
                # range is too big to be used as is => look really at the data
                irange = (int(data.view(numpy.ndarray).min()),
                          int(data.view(numpy.ndarray).max()))
        else:
            # cast to ndarray to ensure a scalar (instead of a DataArray)
            irange = (data.view(numpy.ndarray).min(), data.view(numpy.ndarray).max())

    # short-cuts (for the most usual types)
    if data.dtype.kind in "bu" and irange[0] == 0 and data.itemsize <= 2 and len(data) > 0:
        # TODO: for int (irange[0] < 0), treat as unsigned, and swap the first
        # and second halves of the histogram.
        # TODO: for 32 or 64 bits with full range, convert to a view looking
        # only at the 2 high bytes.
        length = irange[1] - irange[0] + 1
        hist = numpy.bincount(data.flat, minlength=length)
        edges = (0, hist.size - 1)
        if edges[1] > irange[1]:
            logging.warning("Unexpected value %d outside of range %s", edges[1], irange)
    else:
        if data.dtype.kind in "biu":
            length = min(8192, irange[1] - irange[0] + 1)
        else:
            # For floats, it will automatically find the minimum and maximum
            length = 256
        hist, all_edges = numpy.histogram(data, bins=length, range=irange)
        edges = (max(irange[0], all_edges[0]),
                 min(irange[1], all_edges[-1]))

    return hist, edges


def guessDRange(data):
    """
    Guess the data range of the data given.
    data (None or DataArray): data on which to base the guess
    return (2 values)
    """
    if data.dtype.kind in "biu":
        try:
            depth = 2 ** data.metadata[model.MD_BPP]
            if depth <= 1:
                logging.warning("Data reports a BPP of %d",
                                data.metadata[model.MD_BPP])
                raise ValueError()  # fall back to data type

            if data.dtype.kind == "i":
                drange = (-depth // 2, depth // 2 - 1)
            else:
                drange = (0, depth - 1)
        except (KeyError, ValueError):
            idt = numpy.iinfo(data.dtype)
            drange = (idt.min, idt.max)
    else:
        raise TypeError("Cannot guess drange for data of kind %s" % data.dtype.kind)

    return drange


def isClipping(data, drange=None):
    """
    Check whether the given image has clipping pixels. Clipping is detected
    by checking if a pixel value is the maximum value possible.
    data (numpy.ndarray): image to check
    drange (None or tuple of 2 values): min/max possible values contained.
      If None, it will try to guess it.
    return (bool): True if there are some clipping pixels
    """
    if drange is None:
        drange = guessDRange(data)
    return drange[1] in data


# TODO: try to do cumulative histogram value mapping (=histogram equalization)?
# => might improve the greys, but might be "too" clever
def DataArray2RGB(data, irange=None, tint=(255, 255, 255)):
    """
    :param data: (numpy.ndarray of unsigned int) 2D image greyscale (unsigned
        float might work as well)
    :param irange: (None or tuple of 2 values) min/max intensities mapped
        to black/white
        None => auto (min, max are from the data);
        0, max val of data => whole range is mapped.
        min must be < max, and must be of the same type as data.dtype.
    :param tint: Could be:
        - (3-tuple of 0 < int <256) RGB colour of the final image (each
        pixel is multiplied by the value. Default is white.
        - colors.Colormap Object
    :return: (numpy.ndarray of 3*shape of uint8) converted image in RGB with the
        same dimension
    """
    # TODO: handle signed values
    assert(data.ndim == 2) # => 2D with greyscale

    # Discard the DataArray aspect and just get the raw array, to be sure we
    # don't get a DataArray as result of the numpy operations
    data = data.view(numpy.ndarray)

    # fit it to 8 bits and update brightness and contrast at the same time
    if irange is None:
        irange = (numpy.nanmin(data), numpy.nanmax(data))
        if math.isnan(irange[0]):
            logging.warning("Trying to convert all-NaN data to RGB")
            data = numpy.nan_to_num(data)
            irange = (0, 1)
    else:
        # ensure irange is the same type as the data. It ensures we don't get
        # crazy values, and also that numpy doesn't get confused in the
        # intermediary dtype (cf .clip()).
        irange = numpy.array(irange, data.dtype)
        # TODO: warn if irange looks too different from original value?
        if irange[0] == irange[1]:
            logging.info("Requested RGB conversion with null-range %s", irange)

    # Determine if it is necessary to deal with the color map
    # Otherwise, continue with the old method

    if isinstance(tint, colors.Colormap):
        # Normalize the data to the interval [0, 1.0]
        # TODO: Add logarithmic normalization with LogNorm
        # norm = colors.LogNorm(vmin=data.min(), vmax=data.max())
        norm = colors.Normalize(vmin=irange[0], vmax=irange[1], clip=True)
        rgb = tint(norm(data))  # returns an rgba array
        rgb = rgb[:, :, :3]  # discard alpha channel
        out = numpy.empty(rgb.shape, dtype=numpy.uint8)
        numpy.multiply(rgb, 255, casting='unsafe', out=out)
        return out

    if data.dtype == numpy.uint8 and irange[0] == 0 and irange[1] == 255:
        # short-cut when data is already the same type
        # logging.debug("Applying direct range mapping to RGB")
        drescaled = data
        # TODO: also write short-cut for 16 bits by reading only the high byte?
    else:
        # If data might go outside of the range, clip first
        if data.dtype.kind in "iu":
            # no need to clip if irange is the whole possible range
            idt = numpy.iinfo(data.dtype)
            # Ensure B&W if there is only one value allowed
            if irange[0] >= irange[1]:
                if irange[0] > idt.min:
                    irange = (irange[0] - 1, irange[0])
                else:
                    irange = (irange[0], irange[0] + 1)

            if img_fast:
                try:
                    # only (currently) supports uint16
                    return img_fast.DataArray2RGB(data, irange, tint)
                except ValueError as exp:
                    logging.info("Fast conversion cannot run: %s", exp)
                except Exception:
                    logging.exception("Failed to use the fast conversion")

            if irange[0] > idt.min or irange[1] < idt.max:
                data = data.clip(*irange)
        else: # floats et al. => always clip
            # Ensure B&W if there is just one value allowed
            if irange[0] >= irange[1]:
                irange = (irange[0] - 1e-9, irange[0])
            data = data.clip(*irange)

        dshift = data - irange[0]
        if data.dtype == numpy.uint8:
            drescaled = dshift  # re-use memory for the result
        else:
            # TODO: could directly use one channel of the 'rgb' variable?
            drescaled = numpy.empty(data.shape, dtype=numpy.uint8)
        # Ideally, it would be 255 / (irange[1] - irange[0]) + 0.5, but to avoid
        # the addition, we can just use 255.99, and with the rounding down, it's
        # very similar.
        b = 255.99 / (irange[1] - irange[0])
        numpy.multiply(dshift, b, out=drescaled, casting="unsafe")

    # Now duplicate it 3 times to make it RGB (as a simple approximation of
    # greyscale)
    # dstack doesn't work because it doesn't generate in C order (uses strides)
    # apparently this is as fast (or even a bit better):

    # 0 copy (1 malloc)
    rgb = numpy.empty(data.shape + (3,), dtype=numpy.uint8, order='C')

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
        numpy.multiply(drescaled, rtint / 255, out=rgb[:, :, 0], casting="unsafe")
        numpy.multiply(drescaled, gtint / 255, out=rgb[:, :, 1], casting="unsafe")
        numpy.multiply(drescaled, btint / 255, out=rgb[:, :, 2], casting="unsafe")

    return rgb


def getColorbar(color_map, width, height, alpha=False):
    """
    Returns an RGB gradient rectangle or colorbar (as numpy array with 2 dim of RGB tuples)
    based on the color map inputed
    color_map: (matplotlib colormap object)
    width (int): pixel width of output rectangle
    height (int): pixel height of output rectangle
    alpha: (bool): set to true if you want alpha channel
    return: numpy Array of uint8 RGB tuples
    """
    assert isinstance(width, int) and width > 0
    assert isinstance(height, int) and height > 0
    gradient = numpy.linspace(0.0, 1.0, width)
    gradient = numpy.tile(gradient, (height, 1))
    gradient = color_map(gradient)
    if not alpha:
        gradient = gradient[:, :, :3]  # discard alpha channel  # convert to rgb
    gradient = numpy.multiply(gradient, 255)  # convert to rgb
    return gradient.astype(numpy.uint8)


def tintToColormap(tint):
    """
    Convert a tint to a matplotlib.colors.Colormap object
    tint argument can be:
    - a list tuple RGB value (for a tint) or
    - a matplotlib.colors.Colormap object for a custom color map (then it is just returned as is) or
    - a string of value TINT_FIT_TO_RGB to indicate fit RGB color mapping
    - a string of value TINT_RGB_AS_IS that indicates no tint. Will be converted to a rainbow colormap
    name (string): the name argument of the new colormap object
    returns matplotlib.colors.Colormap object
    """
    if isinstance(tint, colors.Colormap):
        return tint
    elif isinstance(tint, tuple) or isinstance(tint, list):  # a tint RGB value
        # make a gradient from black to the selected tint
        tint = colors.LinearSegmentedColormap.from_list("",
            [(0, 0, 0), rgb_to_frgb(tint)])
    elif tint == TINT_RGB_AS_IS:
        tint = cm.get_cmap('hsv')
    elif tint == TINT_FIT_TO_RGB:  # tint Fit to RGB constant
        tint = colors.ListedColormap([(0, 0, 1), (0, 1, 0), (1, 0, 0)], 'Fit to RGB')
    else:
        raise TypeError("Invalid tint type: %s" % (tint,))
    return tint


def getYXFromZYX(data, zIndex=0):
    """
    Extracts an XY plane from a ZYX image at the index given by zIndex (int)
    Returns the data array, which is now 2D. The metadata of teh resulting 2D
    image is updated such that MD_POS reflects the position of the 3D slice.

    data: an image DataArray typically with 3 dimensions
    zIndex: the index of the XY plane to extract from the image.
    returns: 2D image DataArray

    """

    d = data.view()
    if d.ndim < 2:
        d.shape = (1,) * (2 - d.ndim) + d.shape

    elif d.ndim > 2:
        d.shape = d.shape[-3:]  # raise ValueError if it will not work
        d = d[zIndex]  # Remove z

        # Handle updating metadata
        pxs = d.metadata.get(model.MD_PIXEL_SIZE)
        pos = d.metadata.get(model.MD_POS)
        if pxs is not None and pos is not None and len(pxs) == 3:
            height = d.shape[0] * pxs[2]  # ZYX order
            if len(pos) == 3:
                d.metadata[model.MD_POS] = (pos[0], pos[1], pos[2] - height / 2 + zIndex * pxs[2])
            else:
                logging.warning("Centre Position metadata missing third dimension. Assuming 0.")
                d.metadata[model.MD_POS] = (pos[0], pos[1], -height / 2 + zIndex * pxs[2])

    return d


def ensure2DImage(data):
    """
    Reshape data to make sure it's 2D by trimming all the low dimensions (=1).
    Odemis' convention is to have data organized as CTZYX. If CTZ=111, then it's
    a 2D image, but it has too many dimensions for functions which want only 2D.
    If it has a 3D pixel size (voxels) then it must be ZYX, so this should be handled.
    data (DataArray): the data to reshape
    return DataArray: view to the same data but with 2D shape
    raise ValueError: if the data is not 2D (CTZ != 111)
    """
    d = data.view()
    if d.ndim < 2:
        d.shape = (1,) * (2 - d.ndim) + d.shape
    elif d.ndim > 2:
        d.shape = d.shape[-2:]  # raise ValueError if it will not work

    return d


def RGB2Greyscale(data):
    """
    Converts an RGB image to a greyscale image.
    Note: it currently adds the 3 channels together, but this should not be
      assumed to hold true.
    data (ndarray of YX3 uint8): RGB image (alpha channel can be on the 4th channel)
    returns (ndarray of YX uint16): a greyscale representation.
    """
    if data.shape[-1] not in {3, 4}:
        raise ValueError("Data passed has %d colour channels, which is not RGB" %
                         (data.shape[-1],))
    if data.dtype != numpy.uint8:
        logging.warning("RGB data should be uint8, but is %s type", data.dtype)

    imgs = data[:, :, 0].astype(numpy.uint16)
    imgs += data[:, :, 1]
    imgs += data[:, :, 2]

    return imgs


def ensureYXC(data):
    """
    Ensure that a RGB image is in YXC order in memory, to fit RGB24 or RGB32
    format.
    data (DataArray): 3 dimensions RGB data
    return (DataArray): same data, if necessary reordered in YXC order
    """
    if data.ndim != 3:
        raise ValueError("data has not 3 dimensions (%d dimensions)" % data.ndim)

    md = data.metadata.copy()
    dims = md.get(model.MD_DIMS, "CYX")

    if dims == "CYX":
        # CYX, change it to YXC, by rotating axes
        data = numpy.rollaxis(data, 2) # XCY
        data = numpy.rollaxis(data, 2) # YXC
        dims = "YXC"

    if not dims == "YXC":
        raise NotImplementedError("Don't know how to handle dim order %s" % (dims,))

    if data.shape[-1] not in {3, 4}:
        logging.warning("RGB data has C dimension of length %d, instead of 3 or 4", data.shape[-1])

    if data.dtype != numpy.uint8:
        logging.warning("RGB data should be uint8, but is %s type", data.dtype)

    data = numpy.ascontiguousarray(data) # force memory placement
    md[model.MD_DIMS] = dims
    return model.DataArray(data, md)


def rescale_hq(data, shape):
    """
    Resize the image to the new given shape (smaller or bigger). It tries to
    smooth the pixels. Metadata is updated.
    data (DataArray or numpy.array): Data to be rescaled
    shape (tuple): the new shape of the image. It needs to be the same length as the data.shape.
    return (DataArray or numpy.array): The image rescaled. It has the same shape
        as the 'shape' parameter. The returned object has the same type of the 'data' parameter
    """
    if 0 in shape:
        raise ValueError("Requested shape is %s, but it should be at least 1 px in each dimension" % (shape,))

    scale = tuple(n / o for o, n in zip(data.shape, shape))

    if hasattr(data, "metadata"):
        dims = data.metadata.get(model.MD_DIMS, "CTZYX"[-data.ndim::])
        ci = dims.find("C")  # -1 if not found
    else:
        ci = -1

    if data.ndim == 2 or (data.ndim == 3 and ci == 2 and scale[ci] == 1):
        # TODO: if C is not last dim, reshape (ie, call ensureYXC())
        # TODO: not all dtypes are supported by OpenCV (eg, uint32)
        # This is a normal spatial image
        if any(s < 1 for s in scale):
            interpolation = cv2.INTER_AREA  # Gives best looking when shrinking
        else:
            interpolation = cv2.INTER_LINEAR
        # If a 3rd dim, OpenCV will apply the resize on each C independently
        out = cv2.resize(data, (shape[1], shape[0]), interpolation=interpolation)
    else:
        # Weird number of dimensions => default to the less pretty but more
        # generic scipy version
        out = numpy.empty(shape, dtype=data.dtype)
        scipy.ndimage.interpolation.zoom(data, zoom=scale, output=out, order=1, prefilter=False)

    # Update the metadata
    if hasattr(data, "metadata"):
        out = model.DataArray(out, dict(data.metadata))
        # update each metadata which is linked to the pixel size
        # Metadata that needs to be divided by the scale (zoom => decrease)
        for k in {model.MD_PIXEL_SIZE, model.MD_BINNING}:
            try:
                ov = data.metadata[k]
            except KeyError:
                continue
            try:
                out.metadata[k] = tuple(o / s for o, s in zip(ov, scale))
            except Exception:
                logging.exception("Failed to update metadata '%s' when rescaling by %s",
                                  k, scale)
        # Metadata that needs to be multiplied by the scale (zoom => increase)
        for k in {model.MD_AR_POLE}:
            try:
                ov = data.metadata[k]
            except KeyError:
                continue
            try:
                out.metadata[k] = tuple(o * s for o, s in zip(ov, scale))
            except Exception:
                logging.exception("Failed to update metadata '%s' when rescaling by %s",
                                  k, scale)

    return out


def Subtract(a, b):
    """
    Subtract 2 images, with clipping if needed
    a (DataArray)
    b (DataArray or scalar)
    return (DataArray): a - b, with same dtype and metadata as a
    """
    # TODO: see if it is more useful to upgrade the type to a bigger if overflow
    if a.dtype.kind in "bu":
        # avoid underflow so that 1 - 2 = 0 (and not 65536)
        return numpy.maximum(a, b) - b
    else:
        # TODO handle under/over-flows with integer types (127 - (-1) => -128)
        return a - b


def Bin(data, binning):
    """
    Combines adjacent pixels together, by summing them, in a similar way that
      it's done on a CCD.
    data (DataArray of shape YX): the data to bin. The dimensions should be
      multiple of the binning.
    binning (1<=int, 1<=int): binning in X and Y
    return (DataArray of shape Y'X', with the same dtype as data): all cluster of
      pixels of binning are summed into a single pixel. If it goes above the maximum
      value, it's clipped to this maximum value.
      The metadata PIXEL_SIZE and BINNING are updated (multiplied by the binning).
      If data has MD_BASELINE (the average minimum value), the entire data will
      be subtracted so that MD_BASELINE is kept. In other words,
      baseline * (Bx*By - 1) is subtracted. If it would lead to negative value,
      then the data is clipped to 0 and MD_BASELINE adjusted (increased).
    """
    assert data.ndim == 2
    orig_dtype = data.dtype
    orig_shape = data.shape
    if binning[0] < 1 or binning[1] < 1:
        raise ValueError("Binning must be > 0, but got %s" % (binning,))

    # Reshape the data to go from YX to Y'ByX'Bx, so that we can sum on By and Bx
    new_shape = orig_shape[0] // binning[1], orig_shape[1] // binning[0]
    if (new_shape[0] * binning[1], new_shape[1] * binning[0]) != orig_shape:
        raise ValueError("Data shape %s not multiple of binning %s" % (orig_shape, new_shape))
    data = data.reshape(new_shape[0], binning[1], new_shape[1], binning[0])

    data = numpy.sum(data, axis=(1, 3))  # uint64 (if data.dtype is int)
    assert data.shape == new_shape
    orig_bin = data.metadata.get(model.MD_BINNING, (1, 1))
    data.metadata[model.MD_BINNING] = orig_bin[0] * binning[0], orig_bin[1] * binning[1]
    if model.MD_PIXEL_SIZE in data.metadata:
        pxs = data.metadata[model.MD_PIXEL_SIZE]
        data.metadata[model.MD_PIXEL_SIZE] = pxs[0] * binning[0], pxs[1] * binning[1]

    # Subtract baseline (aka black level) to avoid it from being multiplied,
    # so instead of having "Sum(data) + Sum(bl)", we have "Sum(data) + bl".
    try:
        baseline = data.metadata[model.MD_BASELINE]
        baseline_sum = binning[0] * binning[1] * baseline
        # If the baseline is too high compared to the actual black, we
        # could end up subtracting too much, and values would underflow
        # => be extra careful and never subtract more than min value.
        minv = float(data.min())
        extra_bl = baseline_sum - baseline
        if extra_bl > minv:
            extra_bl = minv
            logging.info("Baseline reported at %d * %d, but lower values found, so only subtracting %d",
                         baseline, orig_shape[0], extra_bl)

        # Same as "data -= extra_bl", but also works if extra_bl < 0
        numpy.subtract(data, extra_bl, out=data, casting="unsafe")
        data.metadata[model.MD_BASELINE] = baseline_sum - extra_bl
    except KeyError:
        pass

    # If int, revert to original type, with data clipped (not overflowing)
    if orig_dtype.kind in "biu":
        idtype = numpy.iinfo(orig_dtype)
        data = data.clip(idtype.min, idtype.max).astype(orig_dtype)

    return data


# TODO: use VIPS to be fast?
def Average(images, rect, mpp, merge=0.5):
    """
    mix the given images into a big image so that each pixel is the average of each
     pixel (separate operation for each colour channel).
    images (list of RGB DataArrays)
    merge (0<=float<=1): merge ratio of the first and second image (IOW: the
      first image is weighted by merge and second image by (1-merge))
    """
    # TODO: is ok to have a image = None?


    # TODO: (once the operator callable is clearly defined)
    raise NotImplementedError()

# TODO: add operator Screen


def mean_within_circle(data: model.DataArray, center: Tuple[float, float], radius: float) -> model.DataArray:
    """
    Compute the mean value of the points within a circle.
    It only keeps the pixels whose center is within the circle. It can handle
    data with more than 2 dimensions. With more than two dimensions, the circle
    is based on the last two dimensions (Y, X), and the rest of the dimensions are
    treated independently.

    data (DataArray of shape ..., Y, X): the data where to compute the mean.
       All the extra dimensions are kept in the result
    center: the x, y coordinates of the center of the circle.
    radius: the radius of the circle which contains the center of the pixels to be
      taken into account.

    returns (DataArray of type float, with same shape as data minus X&Y):
        the mean of data that corresponds to points in the circle.
    """
    n = 0
    datasum = numpy.zeros(data.shape[:-2], dtype=numpy.float64)

    # Scan the square around the circle, and only pick the points in the circle
    for px in range(max(0, int(center[0] - radius)),
                    min(int(center[0] + radius) + 1, data.shape[-1])):
        for py in range(max(0, int(center[1] - radius)),
                        min(int(center[1] + radius) + 1, data.shape[-2])):
            if math.hypot(center[0] - px, center[1] - py) <= radius:
                n += 1
                datasum += data[..., py, px]

    mean = datasum / n
    return mean


def mergeMetadata(current, correction=None):
    """
    Applies the correction metadata to the current metadata.

    This function is used in order to apply the correction metadata
    generated by the overlay stream to the optical images.

    In case there is some correction metadata (i.e. MD_*_COR) in the current
    dict this is updated with the corresponding metadata found in correction
    dict. However, if this particular metadata is not present in correction dict
    while it exists in current dict, it remains as is and its current value is
    used e.g. in fine alignment for Delphi, MD_ROTATION_COR of the SEM image is
    already present in the current metadata to compensate for MD_ROTATION, thus
    it is omitted in the correction metadata returned by the overlay stream.

    current (dict): original metadata, it will be updated, with the *_COR
      metadata removed if it was present.
    correction (dict or None): metadata with correction information, if None,
      will use current to find the correction metadata.
    """
    if correction is not None:
        current.update(correction)

    # TODO: rotation and position correction should use addition, not subtraction
    if model.MD_ROTATION_COR in current:
        # Default rotation is 0 rad if not specified
        rotation_cor = current[model.MD_ROTATION_COR]
        rotation = current.get(model.MD_ROTATION, 0)
        current[model.MD_ROTATION] = (rotation - rotation_cor) % (math.pi * 2)

    if model.MD_POS_COR in current:
        # Default position is (0, 0) if not specified
        position_cor = current[model.MD_POS_COR]
        position = current.get(model.MD_POS, (0, 0))

        current[model.MD_POS] = (position[0] - position_cor[0],
                                 position[1] - position_cor[1])

    if model.MD_SHEAR_COR in current:
        # Default shear is 0 if not specified
        shear_cor = current[model.MD_SHEAR_COR]
        shear = current.get(model.MD_SHEAR, 0)

        current[model.MD_SHEAR] = shear - shear_cor

    # There is no default pixel size (though in some case sensor pixel size can
    # be used as a fallback)
    if model.MD_PIXEL_SIZE in current:
        pxs = current[model.MD_PIXEL_SIZE]
        # Do the correction for 2D and 3D
        pxs_cor = current.get(model.MD_PIXEL_SIZE_COR, (1,) * len(pxs))
        current[model.MD_PIXEL_SIZE] = tuple(p * pc for p, pc in zip(pxs, pxs_cor))

    elif model.MD_PIXEL_SIZE_COR in current:
        logging.info("Cannot correct pixel size of data with unknown pixel size")

    # remove correction metadata (to make it clear the correction has been applied)
    for k in (model.MD_ROTATION_COR, model.MD_PIXEL_SIZE_COR, model.MD_POS_COR, model.MD_SHEAR_COR):
        if k in current:
            del current[k]


def getTilesSize(tiles):
    """
    Get the size in pixels of the image formed by the tiles
    tiles (tuple of tuple of DataArray): Tiles
    return (h, w): The size in pixels of the image formed by the tiles
    """
    # calculates the height of the image, summing the heights of the tiles of the first column
    height = 0
    for tile in tiles[0]:
        height += tile.shape[0]

    # calculates the width of the image, summing the width of the tiles of the first row
    width = 0
    for tiles_column in tiles:
        width += tiles_column[0].shape[1]

    return height, width


def getCenterOfTiles(tiles, result_shape):
    """ Calculates the center of the result image
    It is based on the formula for calculating the position of a pixel in world coordinates:
    CT = CI + TMAT * DC
    where:
      CT: center of the tile in pixel coordinates
      CI: center of the image in world coordinates
      DC: delta of the centers in pixel coordinates
      TMAT: transformation matrix
    From the formula above, comes the following formula:
    CI = CT - TMAT * DC,
    which is used below

    tiles (tuple of tuple of DataArray): Tiles
    result_shape (height, width): Size in pixels of the result image from the tiles
    return (x, y): Physical coordinates of the center of the image
    """

    first_tile = tiles[0][0]
    ft_md = first_tile.metadata

    dims = ft_md.get(model.MD_DIMS, "CTZYX"[-first_tile.ndim::])
    ft_shape = [first_tile.shape[dims.index('X')], first_tile.shape[dims.index('Y')]]
    # center of the tile in pixel coordinates
    center_tile_pixel = [d / 2 for d in ft_shape]
    # center of the image in pixel coordinates
    center_image_pixel = [d / 2 for d in result_shape[::-1]]
    # distance between the center of the tile and the center of the image, in pixel coordinates
    dist_centers_tile_pixels = [ct - ci for ct, ci in zip(center_tile_pixel, center_image_pixel)]
    # converts the centers distance, so this variable can be multiplied by the transformation matrix
    dist_centers_tile_pixels = numpy.array(dist_centers_tile_pixels).transpose()
    # transformation matrix
    tmat = get_img_transformation_matrix(first_tile.metadata)
    # distance of the centers converted to world coordinates
    dist_centers_w = tmat @ dist_centers_tile_pixels
    # convert the variable from a numpy.matrix to a numpy.array
    dist_centers_w = numpy.ravel(dist_centers_w)
    # center of the tile in world coordinates
    center_tile_w = first_tile.metadata[model.MD_POS]
    # center of the image in world coordinates
    image_pos = center_tile_w - dist_centers_w
    return tuple(image_pos)


def mergeTiles(tiles):
    """"
    Merge tiles into one DataArray
    tiles (tuple of tuple of DataArray): Tiles to be merged
    return (DataArray): Merge of all the tiles
    """
    first_tile = tiles[0][0]
    ft_md = first_tile.metadata

    result_shape = getTilesSize(tiles)
    # TODO must work when the channel dimension is not the last
    if first_tile.ndim == 3:
        result_shape = result_shape + (first_tile.shape[2],)

    result = numpy.empty(result_shape, dtype=first_tile.dtype)
    result = model.DataArray(result, ft_md.copy())

    width_sum = 0
    # copy the tiles to the result image
    for tiles_column in tiles:
        tile_width = tiles_column[0].shape[1]
        height_sum = 0
        for tile in tiles_column:
            tile_height = tile.shape[0]
            bottom = height_sum + tile_height
            right = width_sum + tile_width
            result[height_sum:bottom, width_sum:right] = tile
            height_sum += tile_height

        width_sum += tile_width

    result.metadata[model.MD_POS] = getCenterOfTiles(tiles, result_shape[:2])

    return result


def rotate_img_metadata(image, rotation, center_of_rot):
    """
    Updates the metadata of an image to rotate its position around a given center of rotation.

    :param image: (DataArray) The image for which the metadata will be rotated
    :param rotation: (float) [rad] Rotation by which to rotate the image, positive value is counter-clockwise rotation.
    :param center_of_rot: (float, float) [m] Center of rotation (x, y), in the  same coordinate system as MD_POS of the
                                         image.
    :returns: (DataArray) The image with the metadata MD_POS and MD_ROTATION updated, the input data remains unchanged:
        MD_POS: old position + shift based on input rotation [m]
        MD_ROTATION: new rotation + input rotation [rad]
    """
    img = DataArray(image, image.metadata.copy())
    pos = img.metadata[model.MD_POS]

    transl = transform.RigidTransform(translation=center_of_rot)
    rot = transform.RigidTransform(rotation=rotation)

    # Translate so that the center of rotation is at (0,0), because the rigid transform rotates around (0, 0)
    p1 = transl.inverse().apply(pos)
    p2 = rot.apply(p1)  # rotate the position
    p = transl.apply(p2)  # translate back to the original center

    img.metadata[model.MD_POS] = (p[0], p[1])
    original_rotation = img.metadata.get(model.MD_ROTATION, 0)
    img.metadata[model.MD_ROTATION] = original_rotation + rotation
    return img


def getBoundingBox(content):
    """
    Compute the physical bounding-box of the given DataArray(Shadow)
    content (DataArray(Shadow)): The data of the image
    return (tuple(minx, miny, maxx, maxy)): left,top,right,bottom positions in world coordinates where top < bottom and left < right
    raise LookupError if metadata is not available
    """
    # TODO: also handle if passed a 2D array of images? (as returned for pyramidal images)
    md = content.metadata.copy()
    mergeMetadata(md)  # apply the corrections

    # get the pixel size of the full image
    try:
        pxs = md[model.MD_PIXEL_SIZE]
    except KeyError:
        raise LookupError("Cannot compute physical coordinates without MD_PIXEL_SIZE")
    if None in pxs:
        # Some detectors set it to None when the dimensions are not
        raise LookupError("Pixel size %s is not proper meters" % (pxs,))

    dims = md.get(model.MD_DIMS, "CTZYX"[-content.ndim::])
    img_shape = (content.shape[dims.index('X')], content.shape[dims.index('Y')])
    # half shape on world coordinates
    half_shape_wc = (img_shape[0] * pxs[0] / 2, img_shape[1] * pxs[1] / 2)

    md_pos = md.get(model.MD_POS, (0.0, 0.0))  # center
    rect = (
        md_pos[0] - half_shape_wc[0],
        md_pos[1] - half_shape_wc[1],
        md_pos[0] + half_shape_wc[0],
        md_pos[1] + half_shape_wc[1],
    )

    # TODO: if MD_SHEAR or MD_ROTATION => need more
    # Compute the location of all the 4 corners, and then pick the bounding box of them

    return rect


class ImageIntegrator(object):
    """
    Integrate the images one after another. Once the first image is acquired, calculate the best type for fitting
    the image to avoid saturation and overflow. At the end of acquisition, take the average of integrated data if
    the detector is DT_NORMAL and subtract the baseline from the final integrated image.
    """
    def __init__(self, steps):
        """
        steps: (int) the total number of images that need to be integrated
        """
        self.steps = steps  # can be changed by the caller, on the fly
        self._step = 0
        self._img = None
        self._best_dtype = None

    def append(self, img):
        """
        Integrate two images (the new acquired image with the previous integrated one if exists) and return the
        new integrated image. It will reset the ._img after reaching the number of integration counts, notifying
        that the integration of the acquired images is completed.
        Args:
            img(model.DataArray): the image that should be integrated with the previous (integrated) one, if exists
        Returns:
            img(model.DataArray): the integrated image with the updated metadata
        """
        self._step += 1
        if self._img is None:
            orig_dtype = img.dtype
            self._best_dtype = get_best_dtype_for_acc(orig_dtype, self.steps)
            integ_img = img
            self._img = integ_img

        else:
            integ_img = self._img
            # The sum starts as a duplicate of the first image, on the second image received
            if self._step == 2:
                data = integ_img.astype(self._best_dtype, copy=True)
                integ_img = model.DataArray(data, integ_img.metadata.copy())

            numpy.add(integ_img, img, out=integ_img)

            # update the metadata of the integrated image in every integration step
            md = integ_img.metadata
            self.add_integration_metadata(md, img.metadata)

            # At the end of the acquisition, check if the detector type is DT_NORMAL and then take the average by
            # dividing with the number of acquired images (integration count) for every pixel position and restoring
            # the original dtype.
            if self._step == self.steps:
                det_type = md.get(model.MD_DET_TYPE, model.MD_DT_INTEGRATING)
                if det_type == model.MD_DT_NORMAL:  # SEM
                    orig_dtype = img.dtype
                    if orig_dtype.kind in "biu":
                        integ_img = numpy.floor_divide(integ_img, self._step, dtype=orig_dtype, casting='unsafe')
                    else:
                        integ_img = numpy.true_divide(integ_img, self._step, dtype=orig_dtype, casting='unsafe')
                elif det_type != model.MD_DT_INTEGRATING:  # not optical either
                    logging.warning("Unknown detector type %s for image integration.", det_type)
                # The baseline, if exists, should also be subtracted from the integrated image.
                if model.MD_BASELINE in md:
                    integ_img, md = self.subtract_baseline(integ_img, md)

                integ_img = model.DataArray(integ_img, md)

            self._img = integ_img

        # reset the ._img and ._step once you reach the integration count
        if self._step >= self.steps:
            self._step = 0
            self._img = None

        return integ_img

    def add_integration_metadata(self, mda, mdb):
        """
        add mdb to mda, and update mda with the result
        returns dict: mda, which has been updated
        """
        if MD_DWELL_TIME in mda:
            mda[model.MD_DWELL_TIME] += mdb.get(model.MD_DWELL_TIME, 0)
        if MD_EXP_TIME in mda:
            mda[model.MD_EXP_TIME] += mdb.get(model.MD_EXP_TIME, 0)
        mda[model.MD_INTEGRATION_COUNT] = mda.get(model.MD_INTEGRATION_COUNT, 1) + mdb.get(model.MD_INTEGRATION_COUNT, 1)

        return mda

    def subtract_baseline(self, data, md):
        """
        Subtract accumulated baselines from the data so that the data only has one.
        Args:
            data: the data after the integration of all images
            md: metadata of the integrated image
        Returns:
            data, md: the updated data and metadata after the subtraction of the baseline
        """
        baseline = md[model.MD_BASELINE]
        # Subtract the baseline (aka black level) from the final integrated image.
        # Remove the baseline from n-1 images, keep one baseline as bg level.
        minv = float(data.min())

        # If the baseline is too high compared to the actual black, we could end up subtracting too much,
        # and values would underflow => be extra careful and never subtract more than the min value.
        baseline_sum = self._step * baseline  # sum of baselines for n images.
        extra_bl = (self._step - 1) * baseline  # sum of baselines for n-1 images, keep one baseline as bg level.
        # check if we underflow the data values
        if extra_bl > minv:
            extra_bl = minv
            logging.info("Baseline reported at %d * %d, but lower values found, so only subtracting %d",
                         baseline, self.steps, extra_bl)

        # Same as "data -= extra_bl", but also works if extra_bl < 0
        numpy.subtract(data, extra_bl, out=data, casting="unsafe")
        # replace the metadata of the image
        md[model.MD_BASELINE] = baseline_sum - extra_bl

        return data, md


def assembleZCube(images, zlevels):
        """
        Construct xyz cube from a  z stack of images
        :param images:  (list of DataArray of shape YX) list of z ordered images
        :param zlevels:  (list of float) list of focus positions
        :return: (DataArray of shape ZYX) the data array of the xyz cube
        """
        # images is a list of 3 dim data arrays.
        # Will fail on purpose if the images contain more than 2 dimensions
        ret = numpy.array([im.reshape(im.shape[-2:]) for im in images])

        # Add back metadata
        metadata3d = copy.copy(images[0].metadata)
        # Extend pixel size to 3D
        ps_x, ps_y = metadata3d[model.MD_PIXEL_SIZE]
        ps_z = (zlevels[-1] - zlevels[0]) / (len(zlevels) - 1) if len(zlevels) > 1 else 1e-6

        # Compute cube centre
        c_x, c_y = metadata3d[model.MD_POS]
        c_z = (zlevels[0] + zlevels[-1]) / 2  # Assuming zlevels are ordered
        metadata3d[model.MD_POS] = (c_x, c_y, c_z)

        # For a negative pixel size, convert to a positive and flip the z axis
        if ps_z < 0:
            ret = numpy.flipud(ret)
            ps_z = -ps_z

        metadata3d[model.MD_PIXEL_SIZE] = (ps_x, ps_y, ps_z)
        metadata3d[model.MD_DIMS] = "ZYX"

        ret = DataArray(ret, metadata3d)

        return ret


def apply_flood_fill(input_array, start):
    """
    Flood fills a 2-Dimensional numpy array with truth values from a given start position with the 4-connected method.
    The input array contains a shape represented with truth values for the borders of the shape.

    Example
    --------
    >>> input_array = numpy.array(
        [[0, 0, 0, 0, 0, 0,],
         [0, 0, 1, 1, 0, 0,],
         [0, 1, 0, 0, 1, 0,],
         [0, 1, 0, 0, 1, 0,],
         [0, 0, 1, 1, 1, 0,],
         [0, 0, 0, 0, 0, 0,]])

    >>> apply_flood_fill(input_array, (2, 2))
    array([[0, 0, 0, 0, 0, 0],
           [0, 0, 1, 1, 0, 0],
           [0, 1, 1, 1, 1, 0],
           [0, 1, 1, 1, 1, 0],
           [0, 0, 1, 1, 1, 0],
           [0, 0, 0, 0, 0, 0]])


    :param input_array: (ndarray(bool)) binary array of size MxN containing the shape to be filled
    :param start: (tuple(int, int)) position from which to start flood fill
    :return: (ndarray(bool)) array of size MxN containing the filled shape, with filled values True
    """
    if start >= input_array.shape:
        raise ValueError(f"Start position of {start} does not lie within the array.")

    input_array = input_array.copy()
    max_area = input_array.shape[0] * input_array.shape[1]
    pixel_queue = [start]
    overflow_counter = 0
    while len(pixel_queue) > 0:
        # To prevent the possibility of an infinite loop count the number of iterations. If it is larger than the
        # total number of values in the array raise an error since this should not be possible.
        if overflow_counter > max_area:
            raise ValueError(f"Number of loop iterations is higher than maximum iterations possible ({max_area}) in "
                             f"array of shape: {input_array.shape}")
        # Set the current pixel to the first element of the queue and remove it from the queue
        row, col = pixel_queue.pop(0)
        if not input_array[row, col]:
            input_array[row, col] = True  # Fill current pixel
            if not input_array[max(row - 1, 0), col]:  # Check north of pixel
                pixel_queue.append((max(row - 1, 0), col))
            if not input_array[row, max(col - 1, 0)]:  # Check west of pixel
                pixel_queue.append((row, max(col - 1, 0)))
            if not input_array[min(row + 1, input_array.shape[0] - 1), col]:  # Check south of pixel
                pixel_queue.append((min(row + 1, input_array.shape[0] - 1), col))
            if not input_array[row, min(col + 1, input_array.shape[1] - 1)]:  # Check east of pixel
                pixel_queue.append((row, min(col + 1, input_array.shape[1] - 1)))
        overflow_counter += 1
    return input_array
