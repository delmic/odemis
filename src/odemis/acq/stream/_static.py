# -*- coding: utf-8 -*-
"""
Created on 25 Jun 2014

@author: Éric Piel

Copyright © 2014-2015 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.

"""

# Contains all the static streams, which only provide projections of the data
# they were initialised with.

from __future__ import division

import collections
import logging
import gc
import math
import numpy
import copy
from odemis import model
from odemis.acq import calibration
from odemis.model import MD_POS, MD_POL_MODE, MD_POL_NONE, VigilantAttribute
from odemis.util import img, conversion, angleres, spectrum, find_closest
import threading
import weakref
import time

from ._base import Stream


class StaticStream(Stream):
    """
    Stream containing one static image.
    For testing and static images.
    """

    def __init__(self, name, raw, *args, **kwargs):
        """
        Note: parameters are different from the base class.
        raw (DataArray, DataArrayShadow or list of DataArray): The data to display.
        """
        super(StaticStream, self).__init__(name, None, None, None, raw=raw, *args, **kwargs)

        self._ht_needs_recompute = threading.Event()
        self._hthread = None

    def _shouldUpdateHistogram(self):
        """
        Ensures that the histogram VA will be updated in the "near future".
        """
        # If the previous request is still being processed, the event
        # synchronization allows to delay it (without accumulation).
        if self._hthread is None:
            self._hthread = threading.Thread(target=self._histogram_thread,
                                         args=(weakref.ref(self),),
                                         name="Histogram computation")
            self._hthread.daemon = True
            self._hthread.start()
        self._ht_needs_recompute.set()

    @staticmethod
    def _histogram_thread(wstream):
        """
        Called as a separate thread, and recomputes the histogram whenever
        it receives an event asking for it.
        wself (Weakref to a stream): the stream to follow
        """
        try:
            stream = wstream()
            name = stream.name.value
            ht_needs_recompute = stream._ht_needs_recompute
            # Only hold a weakref to allow the stream to be garbage collected
            # On GC, trigger im_needs_recompute so that the thread can end too
            wstream = weakref.ref(stream, lambda o: ht_needs_recompute.set())

            while True:
                del stream
                ht_needs_recompute.wait()  # wait until a new image is available
                stream = wstream()
                if stream is None:
                    logging.debug("Stream %s disappeared so ending histogram update thread", name)
                    break

                tstart = time.time()
                ht_needs_recompute.clear()
                stream._updateHistogram()
                tend = time.time()

                # sleep as much, to ensure we are not using too much CPU
                tsleep = max(0.25, tend - tstart)  # max 4 Hz
                time.sleep(tsleep)
        except Exception:
            logging.exception("histogram update thread failed")

        gc.collect()


class RGBStream(StaticStream):
    """
    A static stream which gets as input the actual RGB image
    """

    def __init__(self, name, raw, *args, **kwargs):
        """
        Note: parameters are different from the base class.
        raw (DataArray, DataArrayShadow or list of DataArray): The data to display.
        """
        raw = self._clean_raw(raw)
        super(RGBStream, self).__init__(name, raw, *args, **kwargs)

    def _init_projection_vas(self):
        ''' On RGBStream, the projection is done on RGBSpatialProjection
        '''
        pass

    def _init_thread(self):
        ''' The thread for updating the image on RGBStream resides on DataProjection
            TODO remove this function when all the streams become projectionless
        '''
        pass

    def _clean_raw(self, raw):
        '''
        Returns cleaned raw data or raises error if raw is not RGB(A) 
        '''
        # if raw is a DataArrayShadow, but not pyramidal, read the data to a DataArray
        if isinstance(raw, model.DataArrayShadow) and not hasattr(raw, 'maxzoom'):
            raw = [raw.getData()]
        else:
            raw = [raw]

        # Check it's RGB
        for d in raw:
            dims = d.metadata.get(model.MD_DIMS, "CTZYX"[-d.ndim::])
            ci = dims.find("C")  # -1 if not found
            if not (dims in ("CYX", "YXC") and d.shape[ci] in (3, 4)):
                raise ValueError("Data must be RGB(A)")
        return raw


class Static2DStream(StaticStream):
    """
    Stream containing one static image.
    For testing and static images.
    The static image could be 2D or a 3D stack of images with a z-index
    """
    def __init__(self, name, raw, *args, **kwargs):
        """
        Note: parameters are different from the base class.
        raw (DataArray or DataArrayShadow): The data to display.
        """
        # if raw is a DataArrayShadow, but not pyramidal, read the data to a DataArray
        if isinstance(raw, model.DataArrayShadow) and not hasattr(raw, 'maxzoom'):
            raw = [raw.getData()]
        else:
            raw = [raw]

        metadata = copy.copy(raw[0].metadata)

        # If there are 5 dims in CTZYX, eliminate CT and only take spatial dimensions
        if raw[0].ndim >= 3:
            dims = metadata.get(model.MD_DIMS, "CTZYX"[-raw[0].ndim::])
            if dims[-3:] != "ZYX":
                logging.warning("Metadata has %s dimensions, which may be invalid.", dims)
            if len(raw[0].shape) == 5:
                if any(x > 1 for x in raw[0].shape[:2]):
                    logging.error("Higher dimensional data is being discarded.")
                raw[0] = raw[0][0, 0]
            elif len(raw[0].shape) == 4:
                if any(x > 1 for x in raw[0].shape[:1]):
                    logging.error("Higher dimensional data is being discarded.")
                raw[0] = raw[0][0]

            # Squash the Z dimension if it's empty
            if  raw[0].shape[0] == 1:
                raw[0] = raw[0][0, :, :]
            metadata[model.MD_DIMS] = "CTZYX"[-raw[0].ndim::]

        # Define if z-index should be created.
        if len(raw[0].shape) == 3 and metadata[model.MD_DIMS] == "ZYX":
            try:
                pxs = metadata[model.MD_PIXEL_SIZE]
                pos = metadata[model.MD_POS]
                if len(pxs) < 3:
                    assert len(pxs) == 2
                    logging.warning(u"Metadata for 3D data invalid. Using default pixel size 10µm")
                    pxs = (pxs[0], pxs[1], 10e-6)
                    metadata[model.MD_PIXEL_SIZE] = pxs

                if len(pos) < 3:
                    assert len(pos) == 2
                    pos = (pos[0], pos[1], 0)
                    metadata[model.MD_POS] = pos
                    logging.warning(u"Metadata for 3D data invalid. Using default centre position 0")

            except KeyError:
                raise ValueError("Pixel size or position are missing from metadata")
            # Define a z-index
            self.zIndex = model.IntContinuous(0, (0, raw[0].shape[0] - 1))
            self.zIndex.subscribe(self._on_zIndex)

        # Copy back the metadata
        raw[0].metadata = copy.copy(metadata)

        super(Static2DStream, self).__init__(name, raw, *args, **kwargs)

    def _init_projection_vas(self):
        ''' On Static2DStream, the projection is done on RGBSpatialProjection
        '''
        pass

    def _init_thread(self):
        ''' The thread for updating the image on Static2DStream resides on DataProjection
            TODO remove this function when all the streams become projectionless
        '''
        pass

    def _on_zIndex(self, val):
        self._shouldUpdateHistogram()

    def _updateHistogram(self, data=None):
        if data is None and model.hasVA(self, "zIndex"):
            data = self.raw[0]
            dims = data.metadata.get(model.MD_DIMS, "CTZYX"[-data.ndim::])
            if dims == "ZYX" and data.ndim == 3:
                data = img.getYXFromZYX(data, self.zIndex.value)  # Remove extra dimensions (of length 1)
        super(Static2DStream, self)._updateHistogram(data)


class StaticSEMStream(Static2DStream):
    """
    Same as a StaticStream, but considered a SEM stream
    """

    def __init__(self, name, raw, *args, **kwargs):
        if "acq_type" not in kwargs:
            kwargs["acq_type"] = model.MD_AT_EM
        Static2DStream.__init__(self, name, raw, *args, **kwargs)


class StaticCLStream(Static2DStream):
    """
    Same as a StaticStream, but has a emission wavelength
    """

    def __init__(self, name, raw, *args, **kwargs):
        """
        Note: parameters are different from the base class.
        raw (DataArray of shape (111)YX): raw data. The metadata should
          contain at least MD_POS and MD_PIXEL_SIZE. It should also contain
          MD_OUT_WL.
        """
        try:
            em_range = raw.metadata[model.MD_OUT_WL]
            if isinstance(em_range, basestring):
                unit = None
            else:
                unit = "m"
            self.emission = VigilantAttribute(em_range, unit=unit,
                                              readonly=True)

        except KeyError:
            logging.warning("No emission wavelength for CL stream")

        # Do it at the end, as it forces it the update of the image
        if "acq_type" not in kwargs:
            kwargs["acq_type"] = model.MD_AT_CL
        Static2DStream.__init__(self, name, raw, *args, **kwargs)


class StaticBrightfieldStream(Static2DStream):
    """
    Same as a StaticStream, but considered a Brightfield stream
    """
    pass


class StaticFluoStream(Static2DStream):
    """Static Stream containing images obtained via epifluorescence.

    It basically knows how to show the excitation/emission wavelengths,
    and how to taint the image.
    """

    def __init__(self, name, raw, *args, **kwargs):
        """
        Note: parameters are different from the base class.
        raw (DataArray of shape (111)YX): raw data. The metadata should
          contain at least MD_POS and MD_PIXEL_SIZE. It should also contain
          MD_IN_WL and MD_OUT_WL.
        """

        if "acq_type" not in kwargs:
            kwargs["acq_type"] = model.MD_AT_FLUO
        # Note: it will update the image, and changing the tint will do it again
        if "acq_type" not in kwargs:
            kwargs["acq_type"] = model.MD_AT_FLUO
        super(StaticFluoStream, self).__init__(name, raw, *args, **kwargs)

        # Wavelengths
        try:
            exc_range = raw.metadata[model.MD_IN_WL]
            self.excitation = VigilantAttribute(exc_range, unit="m",
                                                readonly=True)
        except KeyError:
            logging.warning("No excitation wavelength for fluorescence stream")

        default_tint = (0, 255, 0)  # green is most typical
        try:
            em_range = raw.metadata[model.MD_OUT_WL]
            if isinstance(em_range, basestring):
                unit = None
            else:
                unit = "m"
                default_tint = conversion.wave2rgb(numpy.mean(em_range))
            self.emission = VigilantAttribute(em_range, unit=unit,
                                              readonly=True)
        except KeyError:
            logging.warning("No emission wavelength for fluorescence stream")

        # colouration of the image
        tint = raw.metadata.get(model.MD_USER_TINT, default_tint)
        self.tint.value = tint


class StaticARStream(StaticStream):
    """
    A angular resolved stream for one set of data.

    There is no directly nice (=obvious) format to store AR data.
    The difficulty is that data is somehow 4 dimensions: SEM-X, SEM-Y, CCD-X,
    CCD-Y. CCD-dimensions do not correspond directly to quantities, until
    converted into angle/angle (knowing the position of the pole).
    As it's possible that positions on the SEM are relatively random, and it
    is convenient to have a simple format when only one SEM pixel is scanned,
    we've picked the following convention:
     * each CCD image is a separate DataArray
     * each CCD image contains metadata about the SEM position (MD_POS, in m)
       pole (MD_AR_POLE, in px), and acquisition time (MD_ACQ_DATE)
     * multiple CCD images are grouped together in a list
    background VA is subtracted from the raw image when displayed, otherwise a
      baseline value is used.
    """

    def __init__(self, name, data, *args, **kwargs):
        """
        name (string)
        data (model.DataArray(Shadow) of shape (YX) or list of such DataArray(Shadow)).
         The metadata MD_POS, MD_AR_POLE and MD_POL_MODE should be provided
        """
        if not isinstance(data, collections.Iterable):
            data = [data]  # from now it's just a list of DataArray

        # TODO: support DAS, as a "delayed loading" by only calling .getData()
        # when the projection for the particular data needs to be computed (or
        # .raw needs to be accessed?)
        # Ensure all the data is a DataArray, as we don't handle (yet) DAS
        data = [d.getData() if isinstance(d, model.DataArrayShadow) else d for d in data]

        # find positions of each acquisition
        # (float, float, str or None)) -> DataArray: position on SEM + polarization -> data
        self._pos = {}
        self._sempos = {}
        polpos = set()
        for d in data:
            try:
                self._pos[d.metadata[MD_POS] + (d.metadata.get(MD_POL_MODE, None),)] = img.ensure2DImage(d)
                self._sempos[d.metadata[MD_POS]] = img.ensure2DImage(d)
                if MD_POL_MODE in d.metadata:
                    polpos.add(d.metadata.get(MD_POL_MODE))
            except KeyError:
                logging.info("Skipping DataArray without known position")

        # Cached conversion of the CCD image to polar representation
        # TODO: automatically fill it in a background thread
        self._polar = {}  # dict tuple 2 floats -> DataArray

        # SEM position VA
        # SEM position displayed, (None, None) == no point selected (x, y)
        self.point = model.VAEnumerated((None, None),
                     choices=frozenset([(None, None)] + list(self._sempos.keys())))

        if self._pos:
            # Pick one point, e.g., top-left
            bbtl = (min(x for x, y, pol in self._pos.keys() if x is not None),
                    min(y for x, y, pol in self._pos.keys() if y is not None))

            # top-left point is the closest from the bounding-box top-left
            def dis_bbtl(v):
                try:
                    return math.hypot(bbtl[0] - v[0], bbtl[1] - v[1])
                except TypeError:
                    return float("inf")  # for None, None
            self.point.value = min(self._sempos.keys(), key=dis_bbtl)

        # no need for init=True, as Stream.__init__ will update the image
        self.point.subscribe(self._onPoint)

        # polarization VA
        # check if any polarization analyzer data, (None) == no analyzer data (pol)
        if self._pos.keys()[0][-1]:
            # use first entry in acquisition to populate VA (acq could have 1 or 6 pol pos)
            self.polarization = model.VAEnumerated(self._pos.keys()[0][-1],
                                choices=polpos)

        if self._pos.keys()[0][-1]:
            self.polarization.subscribe(self._onPolarization)

        if "acq_type" not in kwargs:
            kwargs["acq_type"] = model.MD_AT_AR
        super(StaticARStream, self).__init__(name, list(self._pos.values()), *args, **kwargs)

    def _project2Polar(self, pos):
        """
        Return the polar projection of the image at the given position.
        pos (float, float, string or None): position (must be part of the ._pos)
        returns DataArray: the polar projection
        """
        if pos in self._polar:
            polard = self._polar[pos]
        else:
            # Compute the polar representation
            data = self._pos[pos]
            try:
                if numpy.prod(data.shape) > (1280 * 1080):
                    # AR conversion fails with very large images due to too much
                    # memory consumed (> 2Gb). So, rescale + use a "degraded" type that
                    # uses less memory. As the display size is small (compared
                    # to the size of the input image, it shouldn't actually
                    # affect much the output.
                    logging.info("AR image is very large %s, will convert to "
                                 "azimuthal projection in reduced precision.",
                                 data.shape)
                    y, x = data.shape
                    if y > x:
                        small_shape = 1024, int(round(1024 * x / y))
                    else:
                        small_shape = int(round(1024 * y / x)), 1024
                    # resize
                    data = img.rescale_hq(data, small_shape)

                # 2 x size of original image (on smallest axis) and at most
                # the size of a full-screen canvas
                size = min(min(data.shape) * 2, 1134)

                # TODO: First compute quickly a low resolution and then
                # compute a high resolution version.
                # TODO: could use the size of the canvas that will display
                # the image to save some computation time.

                # Get bg image, if existing. It must match the polarization (defaulting to MD_POL_NONE).
                bg_image = self._getBackground(data.metadata.get(MD_POL_MODE, MD_POL_NONE))

                if bg_image is None:
                    # Simple version: remove the background value
                    data0 = angleres.ARBackgroundSubtract(data)
                else:
                    data0 = img.Subtract(data, bg_image)  # metadata from data

                # Warning: allocates lot of memory, which will not be free'd until
                # the current thread is terminated.

                polard = angleres.AngleResolved2Polar(data0, size, hole=False)

                # TODO: don't hold too many of them in cache (eg, max 3 * 1134**2)
                self._polar[pos] = polard
            except Exception:
                logging.exception("Failed to convert to azimuthal projection")
                return data  # display it raw as fallback

        return polard

    def _getBackground(self, pol_mode):
        """
        Get background image from background VA
        :param pol_mode: metadata
        :return: (DataArray or None): the background image corresponding to the given polarization,
                 or None, if no background corresponds.
        """
        bg_data = self.background.value  # list containing DataArrays, DataArray or None

        if bg_data is None:
            return None

        if isinstance(bg_data, model.DataArray):
            bg_data = [bg_data]  # convert to list of bg images

        for bg in bg_data:
            # if no analyzer hardware, set MD_POL_MODE = "pass-through" (MD_POL_NONE)
            if bg.metadata.get(MD_POL_MODE, MD_POL_NONE) == pol_mode:
                # should be only one bg image with the same metadata entry
                return bg  # DataArray

        # Nothing found e.g. pol_mode = "rhc" but no bg image with "rhc"
        logging.debug("No background image with polarization mode %s ." % pol_mode)
        return None

    def _find_metadata(self, md):
        # For polar view, no PIXEL_SIZE nor POS
        return {}

    def _updateImage(self):
        """ Recomputes the image with all the raw data available for the current
        selected point.
        """
        if not self.raw:
            return

        pos = self.point.value
        try:
            if pos == (None, None):
                self.image.value = None
            else:
                if self._pos.keys()[0][-1]:
                    pol = self.polarization.value
                else:
                    pol = None
                polard = self._project2Polar(pos + (pol,))
                # update the histogram
                # TODO: cache the histogram per image
                # FIXME: histogram should not include the black pixels outside
                # of the circle. => use a masked array?
                # reset the drange to ensure that it doesn't depend on older data
                self._drange = None
                self._updateHistogram(polard)
                self.image.value = self._projectXY2RGB(polard)
        except Exception:
            logging.exception("Updating %s image", self.__class__.__name__)

    def _onPoint(self, pos):
        self._shouldUpdateImage()

    def _onPolarization(self, pos):
        self._shouldUpdateImage()

    def _setBackground(self, bg_data):
        """
        Called when the background is about to be changed
        :param bg_data: (None, DataArray or list of DataArrays) background image(s)
        :return: (None, DataArray or list of DataArrays)
        :raises: (ValueError) the background data is not compatible with the data
                 (ex: incompatible resolution (shape), encoding (data type), format (bits),
                 polarization of images).
        """
        if bg_data is None:
            # simple baseline background value will be subtracted
            return bg_data

        isDataArray = False
        if isinstance(bg_data, model.DataArray):
            bg_data = [bg_data]
            isDataArray = True

        bg_data = [img.ensure2DImage(d) for d in bg_data]

        for d in bg_data:
            # TODO check if MD_AR_POLE in MD? will fail in set_ar_background anyways,
            # but maybe nicer to check here already
            arpole = d.metadata[model.MD_AR_POLE]  # we expect the data has AR_POLE

            # TODO: allow data which is the same shape but lower binning by
            # estimating the binned image
            # Check the background data and all the raw data have the same resolution
            # TODO: how to handle if the .raw has different resolutions?
            for r in self.raw:
                if d.shape != r.shape:
                    raise ValueError("Incompatible resolution of background data "
                                     "%s with the angular resolved resolution %s." %
                                     (d.shape, r.shape))
                if d.dtype != r.dtype:
                    raise ValueError("Incompatible encoding of background data "
                                     "%s with the angular resolved encoding %s." %
                                     (d.dtype, r.dtype))
                try:
                    if d.metadata[model.MD_BPP] != r.metadata[model.MD_BPP]:
                        raise ValueError(
                            "Incompatible format of background data "
                            "(%d bits) with the angular resolved format "
                            "(%d bits)." %
                            (d.metadata[model.MD_BPP], r.metadata[model.MD_BPP]))
                except KeyError:
                    pass  # no metadata, let's hope it's the same BPP

                # check the AR pole is at the same position
                if r.metadata[model.MD_AR_POLE] != arpole:
                    logging.warning("Pole position of background data %s is "
                                    "different from the data %s.",
                                    arpole, r.metadata[model.MD_AR_POLE])

                if MD_POL_MODE in r.metadata:  # check if we have polarization analyzer hardware present
                    # check if we have at least one bg image with the corresponding MD_POL_MODE to the image data
                    if not any(bg_im.metadata[MD_POL_MODE] == r.metadata[MD_POL_MODE] for bg_im in bg_data):
                        raise ValueError("No AR background with polarization %s" % r.metadata[MD_POL_MODE])

        if isDataArray:
            return bg_data[0]
        else:  # list
            return bg_data

    def _onBackground(self, data):
        """Called after the background has changed"""
        # uncache all the polar images, and update the current image
        self._polar = {}
        super(StaticARStream, self)._onBackground(data)


class StaticSpectrumStream(StaticStream):
    """
    A Spectrum stream which displays only one static image/data.
    The main difference from the normal streams is that the data is 3D (a cube)
    The metadata should have a MD_WL_POLYNOMIAL or MD_WL_LIST
    Note that the data received should be of the (numpy) shape CYX or C11YX.
    When saving, the data will be converted to CTZYX (where TZ is 11)

    The histogram corresponds to the data after calibration, and selected via
    the spectrumBandwidth VA.

    If background VA is set, it is subtracted from the raw data.
    """

    def __init__(self, name, image, *args, **kwargs):
        """
        name (string)
        image (model.DataArray(Shadow) of shape (CYX), (C11YX), (CTYX), (CT1YX), (1T1YX)).
        The metadata MD_WL_POLYNOMIAL or MD_WL_LIST should be included in order to
        associate the C to a wavelength.
        The metadata MD_TIME_LIST should be included to associate the T to a timestamp

        .background is a DataArray of shape (CT111), where C & T have the same length as in the data.
        .efficiencyCompensation is always DataArray of shape C1111.

        """
        # Spectrum stream has in addition to normal stream:
        #  * information about the current bandwidth displayed (avg. spectrum)
        #  * coordinates of 1st point (1-point, line)
        #  * coordinates of 2nd point (line)

        # TODO: need to handle DAS properly, in case it's tiled (in XY), to avoid
        # loading too much data in memory.
        # Ensure the data is a DataArray, as we don't handle (yet) DAS
        if isinstance(image, model.DataArrayShadow):
            image = image.getData()

        if len(image.shape) == 3:
            # force 5D for CYX
            image = image[:, numpy.newaxis, numpy.newaxis, :, :]
        elif len(image.shape) == 4:
            # force 5D for CTYX
            image = image[:, :, numpy.newaxis, :, :]
        elif len(image.shape) != 5 or image.shape[2] != 1:
            logging.error("Cannot handle data of shape %s", image.shape)
            raise NotImplementedError("StaticSpectrumStream needs 3D or 4D data")

        # This is for "average spectrum" projection
        # cached list of wavelength for each pixel pos
        self._wl_px_values, unit_bw = spectrum.get_spectrum_range(image)
        min_bw, max_bw = self._wl_px_values[0], self._wl_px_values[-1]
        cwl = (max_bw + min_bw) / 2
        width = (max_bw - min_bw) / 12

        # The selected wavelength for a temporal spectrum display
        self.selected_wavelength = model.FloatContinuous(self._wl_px_values[0],
                                                   range=(min_bw, max_bw),
                                                   unit=unit_bw,
                                                   setter=self._setWavelength)

        # Is there time data?
        if image.shape[1] > 1:
            # cached list of wavelength for each pixel pos
            self._tl_px_values, unit_t = spectrum.get_time_range(image)
            min_t, max_t = self._tl_px_values[0], self._tl_px_values[-1]
            ct = (max_t + min_t) / 2

            # Create temporal data VA's

            self.selected_time = model.FloatContinuous(ct,
                                                   range=(min_t, max_t),
                                                   unit=unit_t,
                                                   setter=self._setTime)
            self.selected_time.value = ct

        # TODO: allow to pass the calibration data as argument to avoid
        # recomputing the data just after init?
        # Spectrum efficiency compensation data: None or a DataArray (cf acq.calibration)
        self.efficiencyCompensation = model.VigilantAttribute(None, setter=self._setEffComp)

        # low/high values of the spectrum displayed
        self.spectrumBandwidth = model.TupleContinuous(
                                    (cwl - width, cwl + width),
                                    range=((min_bw, min_bw), (max_bw, max_bw)),
                                    unit=unit_bw,
                                    cls=(int, long, float))

        # Whether the (per bandwidth) display should be split intro 3 sub-bands
        # which are applied to RGB
        self.fitToRGB = model.BooleanVA(False)

        # This attribute is used to keep track of any selected pixel within the
        # data for the display of a spectrum
        self.selected_pixel = model.TupleVA((None, None))  # int, int

        # first point, second point in pixels. It must be 2 elements long.
        self.selected_line = model.ListVA([(None, None), (None, None)], setter=self._setLine)

        # Peak method index, None if spectrum peak fitting curve is not displayed
        self.peak_method = model.VAEnumerated("gaussian", {"gaussian", "lorentzian", None})

        # The thickness of a point or a line (shared).
        # A point of width W leads to the average value between all the pixels
        # which are within W/2 from the center of the point.
        # A line of width W leads to a 1D spectrum taking into account all the
        # pixels which fit on an orthogonal line to the selected line at a
        # distance <= W/2.
        self.selectionWidth = model.IntContinuous(1, [1, 50], unit="px")

        self.fitToRGB.subscribe(self.onFitToRGB)
        self.spectrumBandwidth.subscribe(self.onSpectrumBandwidth)
        self.efficiencyCompensation.subscribe(self._onCalib)
        self.selectionWidth.subscribe(self._onSelectionWidth)

        # the raw data after calibration
        self.calibrated = model.VigilantAttribute(image)

        if "acq_type" not in kwargs:
            kwargs["acq_type"] = model.MD_AT_SPECTRUM
        super(StaticSpectrumStream, self).__init__(name, [image], *args, **kwargs)

        # Automatically select point/line if data is small (can only be done
        # after .raw is set)
        if image.shape[-2:] == (1, 1):  # Only one point => select it immediately
            self.selected_pixel.value = (0, 0)
        elif image.shape[-2] == 1:  # Horizontal line => select line immediately
            self.selected_line.value = [(0, 0), (image.shape[-1] - 1, 0)]
        elif image.shape[-1] == 1:  # Vertical line => select line immediately
            self.selected_line.value = [(0, 0), (0, image.shape[-2] - 1)]

    def _init_projection_vas(self):
        # override Stream._init_projection_vas.
        # This stream doesn't provide the projection(s) to an .image by itself.
        # This is handled by the projections:
        # MeanSpectrumProjection, SinglePointSpectrumProjection,
        # SinglePointChronoProjection, LineSpectrumProjection
        # TemporalSpectrumProjection, RGBSpatialSpectrumProjection
        pass

    def _init_thread(self):
        # override Stream._init_thread.
        # This stream doesn't provide the projection(s) to an .image by itself.
        # This is handled by the projections:
        # MeanSpectrumProjection, SinglePointSpectrumProjection,
        # SinglePointChronoProjection, LineSpectrumProjection
        # TemporalSpectrumProjection, RGBSpatialSpectrumProjection
        pass

    # The tricky part is we need to keep the raw data as .raw for things
    # like saving the stream or updating the calibration, but all the
    # display-related methods must work on the calibrated data.
    def _updateDRange(self, data=None):
        if data is None:
            data = self.calibrated.value
        super(StaticSpectrumStream, self)._updateDRange(data)

    def _updateHistogram(self, data=None):
        if data is None:
            spec_range = self._get_bandwidth_in_pixel()
            data = self.calibrated.value[spec_range[0]:spec_range[1] + 1]
        super(StaticSpectrumStream, self)._updateHistogram(data)

    def _setTime(self, value):
        return find_closest(value, self._tl_px_values)

    def _setWavelength(self, value):
        return find_closest(value, self._wl_px_values)

    def _onTimeSelect(self, _):
        # Update other VA's so that displays are updated.
        self.selected_pixel.notify(self.selected_pixel.value)

    def _onWavelengthSelect(self, _):
        # Update other VA's so that displays are updated.
        self.selected_pixel.notify(self.selected_pixel.value)

    def _setLine(self, line):
        """
        Checks that the value set could be correct
        """
        if len(line) != 2:
            raise ValueError("selected_line must be of length 2")

        shape = self.raw[0].shape[-1:-3:-1]
        for p in line:
            if p == (None, None):
                continue
            if len(p) != 2:
                raise ValueError("selected_line must contain only tuples of 2 ints")
            if not 0 <= p[0] < shape[0] or not 0 <= p[1] < shape[1]:
                raise ValueError("selected_line must only contain coordinates "
                                 "within %s" % (shape,))
            if not isinstance(p[0], int) or not isinstance(p[1], int):
                raise ValueError("selected_line must only contain ints but is %s"
                                 % (line,))

        return line

    def _get_bandwidth_in_pixel(self):
        """
        Return the current bandwidth in pixels index
        returns (2-tuple of int): low and high pixel coordinates (included)
        """
        low, high = self.spectrumBandwidth.value

        # Find the closest pixel position for the requested wavelength
        low_px = numpy.searchsorted(self._wl_px_values, low, side="left")
        low_px = min(low_px, len(self._wl_px_values) - 1) # make sure it fits
        # TODO: might need better handling to show just one pixel (in case it's
        # useful) as in almost all cases, it will end up displaying 2 pixels at
        # least
        if high == low:
            high_px = low_px
        else:
            high_px = numpy.searchsorted(self._wl_px_values, high, side="right")
            high_px = min(high_px, len(self._wl_px_values) - 1)

        logging.debug("Showing between %g -> %g nm = %d -> %d px",
                      low * 1e9, high * 1e9, low_px, high_px)
        assert low_px <= high_px
        return low_px, high_px

    # We don't have problems of rerunning this when the data is updated,
    # as the data is static.
    def _updateCalibratedData(self, bckg=None, coef=None):
        """
        Try to update the data with new calibration. The two parameters are
        the same as compensate_spectrum_efficiency(). The input data comes from
        .raw and the calibrated data is saved in .calibrated
        bckg (DataArray or None)
        coef (DataArray or None)
        raise ValueError: if the data and calibration data are not valid or
          compatible. In that case the current calibrated data is unchanged.
        """
        data = self.raw[0]

        if data is None:
            self.calibrated.value = None
            return

        if bckg is None and coef is None:
            # make sure to not display any other error
            self.calibrated.value = data
            return

        if not (set(data.metadata.keys()) &
                {model.MD_WL_LIST, model.MD_WL_POLYNOMIAL}):
            raise ValueError("Spectrum data contains no wavelength information")

        # will raise an exception if incompatible
        calibrated = calibration.compensate_spectrum_efficiency(data, bckg, coef)
        self.calibrated.value = calibrated

    def _setBackground(self, bckg):
        """
        Setter of the spectrum background
        raises ValueError if it's impossible to apply it (eg, no wavelength info)
        """
        # If the coef data is wrong, this function will fail with an exception,
        # and the value never be set.
        self._updateCalibratedData(bckg=bckg, coef=self.efficiencyCompensation.value)
        return bckg

    def _setEffComp(self, coef):
        """
        Setter of the spectrum efficiency compensation
        raises ValueError if it's impossible to apply it (eg, no wavelength info)
        """
        # If the coef data is wrong, this function will fail with an exception,
        # and the value never be set.
        self._updateCalibratedData(bckg=self.background.value, coef=coef)
        return coef

    def _force_selected_spectrum_update(self):
        # There is no explicit way to do it, so instead, pretend the pixel and
        # line have changed (to the same value).
        # TODO: It could be solved by using dataflows (in which case a new data
        # would come whenever settings change).
        if self.selected_pixel.value != (None, None):
            self.selected_pixel.notify(self.selected_pixel.value)

        if not (None, None) in self.selected_line.value:
            self.selected_line.notify(self.selected_line.value)

    def _onBackground(self, data):
        self._onCalib(data)
        # Skip super call, as we are taking care of all

    def _onCalib(self, unused):
        """
        called when the background or efficiency compensation is changed
        """
        # histogram will change as the pixel intensity is different
        self._updateHistogram()
        self._shouldUpdateImage()
        self._force_selected_spectrum_update()

    def _onSelectionWidth(self, width):
        """
        Called when the selection width is updated
        """
        # 0D and/or 1D spectrum will need updates
        self._force_selected_spectrum_update()

    def _onIntensityRange(self, irange):
        super(StaticSpectrumStream, self)._onIntensityRange(irange)
        self._force_selected_spectrum_update()

    def onFitToRGB(self, value):
        """
        called when fitToRGB is changed
        """
        self._shouldUpdateImage()

    def onSpectrumBandwidth(self, value):
        """
        called when spectrumBandwidth is changed
        """
        self._updateHistogram()
        self._shouldUpdateImage()

# TODO: It would make sense to inherit from RGBStream, however, it relies on
# DataProjection, and currently the DataProjection doesn't support .raw being
# updated. So we need to use the "old" way of directly computing the projection,
# as for the live streams. Eventually, when DataProjection supports updated .raw,
# we could simplify/merge the two stream classes.

class RGBUpdatableStream(StaticStream):
    """
    Similar to RGBStream, but contains an update function that allows to modify the
    raw data.
    """

    def __init__(self, name, raw, *args, **kwargs):
        raw = self._clean_raw(raw)
        super(RGBUpdatableStream, self).__init__(name, raw, *args, **kwargs)

    def _clean_raw(self, raw):
        '''
        Returns cleaned raw data or raises error if raw is not RGB(A) 
        '''
        # if raw is a DataArrayShadow, but not pyramidal, read the data to a DataArray
        if isinstance(raw, model.DataArrayShadow) and not hasattr(raw, 'maxzoom'):
            raw = [raw.getData()]
        else:
            raw = [raw]

        # Check it's RGB
        for d in raw:
            dims = d.metadata.get(model.MD_DIMS, "CTZYX"[-d.ndim::])
            ci = dims.find("C")  # -1 if not found
            if not (dims in ("CYX", "YXC") and d.shape[ci] in (3, 4)):
                raise ValueError("Data must be RGB(A)")
        return raw

    def update(self, raw):
        """
        Updates self.raw with new data
        """

        self.raw = self._clean_raw(raw)
        self._shouldUpdateImage()
