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

from past.builtins import basestring, long
from collections.abc import Iterable
import copy
import gc
import logging
import math
import numpy
from odemis import model, util
from odemis.acq import calibration
from odemis.model import MD_POS, MD_POL_MODE, VigilantAttribute, MD_POL_S0
from odemis.util import img, conversion, spectrum, find_closest, almost_equal
import threading
import time
import weakref

from ._base import Stream, POL_POSITIONS_RESULTS, POL_POSITIONS

try:
    import arpolarimetry
except ImportError:
    arpolarimetry = None


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
            logging.exception("Histogram update thread failed")

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
        raw[0].metadata = metadata

        super(Static2DStream, self).__init__(name, raw, *args, **kwargs)

        # Colouration of the image
        if model.MD_USER_TINT in metadata:
            try:
                self.tint.value = img.md_format_to_tint(metadata[model.MD_USER_TINT])
            except (ValueError, TypeError) as ex:
                logging.warning("Failed to use tint '%s': %s.", metadata[model.MD_USER_TINT], ex)

        self.tint.subscribe(self._onTint)

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

    def _onTint(self, tint):
        """
        Store the new tint value as metadata
        """
        self.raw[0].metadata[model.MD_USER_TINT] = img.tint_to_md_format(tint)


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
        # Do it at the end, as it forces it the update of the image
        if "acq_type" not in kwargs:
            kwargs["acq_type"] = model.MD_AT_CL

        Static2DStream.__init__(self, name, raw, *args, **kwargs)
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
                default_tint = conversion.wavelength2rgb(numpy.mean(em_range))
            self.emission = VigilantAttribute(em_range, unit=unit,
                                              readonly=True)
        except KeyError:
            logging.warning("No emission wavelength for fluorescence stream")

        # Colouration of the image
        tint = raw.metadata.get(model.MD_USER_TINT, default_tint)
        try:
            self.tint.value = img.md_format_to_tint(tint)
        except (ValueError, TypeError) as ex:
            logging.warning("Failed to use tint '%s': %s.", tint, ex)
            self.tint.value = default_tint


class StaticARStream(StaticStream):
    """
    A angular resolved stream for one data set.

    There is no directly nice (=obvious) format to store AR data.
    The difficulty is that the data is somehow 4 dimensional: SEM-X, SEM-Y, CCD-X, CCD-Y.
    CCD-dimensions do not correspond directly to quantities, until
    converted into angle/angle (knowing the position of the pole).
    As it's possible that positions on the SEM are relatively random, and it
    is convenient to have a simple format when only one SEM pixel is scanned,
    we've picked the following convention:
     * each CCD image is a separate DataArray
     * each CCD image contains metadata about the SEM position (MD_POS [m]),
       pole (MD_AR_POLE [px]), and acquisition time (MD_ACQ_DATE)
     * multiple CCD images are grouped together in a list
    The background VA is subtracted from the raw image when displayed, otherwise a
    baseline value is used.
    """

    def __init__(self, name, data, *args, **kwargs):
        """
        :param name: (string)
        :param data: (model.DataArray(Shadow) of shape (YX) or list of such DataArray(Shadow)).
        The metadata MD_POS, MD_AR_POLE and MD_POL_MODE should be provided
        """
        if not isinstance(data, Iterable):
            data = [data]  # from now it's just a list of DataArray

        # TODO: support DAS, as a "delayed loading" by only calling .getData()
        # when the projection for the particular data needs to be computed (or
        # .raw needs to be accessed?)
        # Ensure all the data is a DataArray, as we don't handle (yet) DAS
        data = [d.getData() if isinstance(d, model.DataArrayShadow) else d for d in data]

        # find positions of each acquisition
        # (float, float, str or None)) -> DataArray: position on SEM + polarization -> data
        self._pos = {}

        sempositions = set()
        polpositions = set()

        for d in data:
            try:
                sempos_cur = d.metadata[MD_POS]

                # When reading data: floating point error (slightly different keys for same ebeam pos)
                # -> check if there is already a position specified, which is very close by
                # (and therefore the same ebeam pos) and replace with that ebeam position
                # (e.g. all polarization positions for the same ebeam positions will have exactly the same ebeam pos)
                for sempos in sempositions:
                    if almost_equal(sempos_cur[0], sempos[0]) and almost_equal(sempos_cur[1], sempos[1]):
                        sempos_cur = sempos
                        break
                self._pos[sempos_cur + (d.metadata.get(MD_POL_MODE, None),)] = img.ensure2DImage(d)

                sempositions.add(sempos_cur)
                if MD_POL_MODE in d.metadata:
                    polpositions.add(d.metadata[MD_POL_MODE])

            except KeyError:
                logging.info("Skipping DataArray without known position")

        # SEM position VA
        # SEM position displayed, (None, None) == no point selected (x, y)
        self.point = model.VAEnumerated((None, None),
                                        choices=frozenset([(None, None)] + list(sempositions)))

        if self._pos:
            # Pick one point, e.g., top-left
            bbtl = (min(x for x, y in sempositions if x is not None),
                    min(y for x, y in sempositions if y is not None))

            # top-left point is the closest from the bounding-box top-left
            def dis_bbtl(v):
                try:
                    return math.hypot(bbtl[0] - v[0], bbtl[1] - v[1])
                except TypeError:
                    return float("inf")  # for None, None
            self.point.value = min(sempositions, key=dis_bbtl)

        # check if any polarization analyzer data, (None) == no analyzer data (pol)
        if polpositions:
            # Check that for every position, all the polarizations are available,
            # as the GUI expects all the combinations possible, and weird errors
            # will happen when one is missing.
            for pos in sempositions:
                for pol in polpositions:
                    if pos + (pol,) not in self._pos:
                        logging.warning("Polarization data is not complete: missing %s,%s/%s",
                                        pos[0], pos[1], pol)

            # use first entry in acquisition to populate VA (acq could have 1 or 6 pol pos)
            current_pol = util.sorted_according_to(polpositions, POL_POSITIONS)[0]
            self.polarization = model.VAEnumerated(current_pol, choices=polpositions)

            # Add a polarimetry VA containing the polarimetry image results.
            # Note: Polarimetry analysis are only possible if all 6 images per ebeam pos exist.
            # Also check if arpolarimetry package can be imported as might not be installed.
            if polpositions >= set(POL_POSITIONS):
                if arpolarimetry:
                    self.polarimetry = model.VAEnumerated(MD_POL_S0, choices=set(POL_POSITIONS_RESULTS))
                else:
                    logging.warning("arpolarimetry module missing, will not provide polarimetry display")

        if "acq_type" not in kwargs:
            kwargs["acq_type"] = model.MD_AT_AR

        super(StaticARStream, self).__init__(name, list(self._pos.values()), *args, **kwargs)

    def _init_projection_vas(self):
        # override Stream._init_projection_vas.
        # This stream doesn't provide the projection(s) to an .image by itself.
        # This is handled by the projections:
        # ARProjection, ARRawProjection, ARPolarimetryProjection
        pass

    def _init_thread(self):
        # override Stream._init_thread.
        # This stream doesn't provide the projection(s) to an .image by itself.
        # This is handled by the projections:
        # ARProjection, ARRawProjection, ARPolarimetryProjection
        pass

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


class StaticSpectrumStream(StaticStream):
    """
    A stream which displays only one static image/data. The data can be of type
    spectrum (C11YX), temporal spectrum (CT1YX), time correlator (1T1YX) or
    angular spectrum (CA1YX) in case of ek imaging.
    The main difference from the normal streams is that the data is 3D or 4D.
    The metadata should have a MD_WL_LIST or MD_TIME_LIST or MD_THETA_LIST.
    When saving, the data will be converted to CTZYX or CAZYX.

    The histogram corresponds to the data after calibration, and selected via
    the spectrumBandwidth VA.

    If background VA is set, it is subtracted from the raw data.
    """

    def __init__(self, name, image, *args, **kwargs):
        """
        name (string)
        image (model.DataArray(Shadow) of shape (CYX), (C11YX), (CTYX), (CT1YX), (1T1YX), (CAYX), (CA1YX)).
        The metadata MD_WL_LIST can be included in order to associate the C to a wavelength.
        The metadata MD_TIME_LIST can be included to associate the T to a timestamp.
        The metadata MD_THETA_LIST can be included to associate the A to a theta stamp.

        .background is a DataArray of shape (CT111/CA111), where C & T/A have the same length as in the data.
        .efficiencyCompensation is always DataArray of shape C1111.

        """
        # streams have in addition to a normal stream:
        #  * information about the current bandwidth displayed (avg. spectrum) if applicable
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
            # force 5D for CTYX/CAYX
            image = image[:, :, numpy.newaxis, :, :]
        elif len(image.shape) != 5 or image.shape[2] != 1:
            logging.error("Cannot handle data of shape %s", image.shape)
            raise NotImplementedError("StaticSpectrumStream needs 3D or 4D data")

        default_dims = "CTZYX"
        if model.MD_THETA_LIST in image.metadata:
            # Special trick to handle angular spectrum data, as it's usually only 5 dimensions
            default_dims = "CAZYX"
        dims = image.metadata.get(model.MD_DIMS, default_dims[-image.ndim::])

        # This is for "average spectrum" projection
        # cached list of wavelength for each pixel pos
        wl, unit_bw = spectrum.get_spectrum_range(image)
        self._wl_px_values = numpy.array(wl, copy=False)  # Force it to be a numpy array
        min_bw, max_bw = self._wl_px_values[0], self._wl_px_values[-1]
        cwl = (max_bw + min_bw) / 2
        width = (max_bw - min_bw) / 12

        # TODO should be only available if data has spectrum dimension (e.g. chronograph)
        # The selected wavelength for a temporal spectrum display
        # Is there wl data?
        if image.shape[0] > 1:
            self.selected_wavelength = model.FloatContinuous(self._wl_px_values[0],
                                                       range=(min_bw, max_bw),
                                                       unit=unit_bw,
                                                       setter=self._setWavelength)

        # Is there time or theta data?
        if image.shape[1] > 1:
            # cached list of angle or timestamps for each position in the second dimension
            if dims[1] == "A":
                theta_list, unit_theta = spectrum.get_angle_range(image)
                # Only keep valid values (ie, not the NaN)
                # Note, the .calibrated data will have the same columns removed
                self._thetal_px_values = numpy.array([theta for theta in theta_list if not math.isnan(theta)])
                min_theta, max_theta = min(self._thetal_px_values), max(self._thetal_px_values)

                # Allows to select the angle as any value within the range, and the
                # setter will automatically "snap" it to the closest existing theta stamp
                self.selected_angle = model.FloatContinuous(self._thetal_px_values[0],
                                                            range=(min_theta, max_theta),
                                                            unit=unit_theta,
                                                            setter=self._setAngle)
            else:  # let's assume the second dimension is time
                if dims[1] != "T":
                    logging.warning("StaticSpectrumStream expected dim 2 as T, but dims are %s", dims)

                # If time metadata is not found, "px" will be used as unit.
                tl, unit_t = spectrum.get_time_range(image)
                self._tl_px_values = numpy.array(tl, copy=False)  # Force it to be a numpy array
                min_t, max_t = self._tl_px_values[0], self._tl_px_values[-1]

                # Allow to select the time as any value within the range, and the
                # setter will automatically "snap" it to the closest existing timestamp
                self.selected_time = model.FloatContinuous(self._tl_px_values[0],
                                                           range=(min_t, max_t),
                                                           unit=unit_t,
                                                           setter=self._setTime)

        # This attribute is used to keep track of any selected pixel within the
        # data for the display of a spectrum
        self.selected_pixel = model.TupleVA((None, None))  # int, int

        # first point, second point in pixels. It must be 2 elements long.
        self.selected_line = model.ListVA([(None, None), (None, None)], setter=self._setLine)

        # The thickness of a point or a line (shared).
        # A point of width W leads to the average value between all the pixels
        # which are within W/2 from the center of the point.
        # A line of width W leads to a 1D spectrum taking into account all the
        # pixels which fit on an orthogonal line to the selected line at a
        # distance <= W/2.
        self.selectionWidth = model.IntContinuous(1, [1, 50], unit="px")
        self.selectionWidth.subscribe(self._onSelectionWidth)

        # Peak method index, None if spectrum peak fitting curve is not displayed
        self.peak_method = model.VAEnumerated("gaussian", {"gaussian", "lorentzian", None})

        # TODO: allow to pass the calibration data as argument to avoid
        # recomputing the data just after init?
        # Spectrum efficiency compensation data: None or a DataArray (cf acq.calibration)
        self.efficiencyCompensation = model.VigilantAttribute(None, setter=self._setEffComp)
        self.efficiencyCompensation.subscribe(self._onCalib)

        # Is there spectrum data?
        if image.shape[0] > 1:
            # low/high values of the spectrum displayed
            self.spectrumBandwidth = model.TupleContinuous(
                                        (cwl - width, cwl + width),
                                        range=((min_bw, min_bw), (max_bw, max_bw)),
                                        unit=unit_bw,
                                        cls=(int, long, float))
            self.spectrumBandwidth.subscribe(self.onSpectrumBandwidth)

        # the raw data after calibration
        self.calibrated = model.VigilantAttribute(None)
        # Immediately compute it, without any correction, as it can still be
        # different from image if MD_THETA_LIST contains NaNs (which it typically does)
        self._updateCalibratedData(image, bckg=None, coef=self.efficiencyCompensation.value)

        if "acq_type" not in kwargs:
            if image.shape[0] > 1 and image.shape[1] > 1:
                if dims[1] == "A":
                    kwargs["acq_type"] = model.MD_AT_EK
                else:
                    # Already warned when creating selected_time, so no extra warning
                    kwargs["acq_type"] = model.MD_AT_TEMPSPECTRUM
            elif image.shape[0] > 1:
                kwargs["acq_type"] = model.MD_AT_SPECTRUM
            elif image.shape[1] > 1:
                kwargs["acq_type"] = model.MD_AT_TEMPORAL
            else:
                logging.warning("SpectrumStream data has no spectrum or time/theta dimension, shape = %s",
                                image.shape)

        super(StaticSpectrumStream, self).__init__(name, [image], *args, **kwargs)


        self.tint.subscribe(self.onTint)

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

    def _setAngle(self, value):
        return find_closest(value, self._thetal_px_values)

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
        data = self.raw[0]
        if data.shape[0] <= 1:  # There is no C dimension
            return 0, 0

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
    def _updateCalibratedData(self, data=None, bckg=None, coef=None):
        """
        Try to update the data with a new calibration. The two parameters are
        the same as apply_spectrum_corrections(). The input data comes from
        .raw and the calibrated data is saved in .calibrated
        :param bckg: (DataArray or None) The background image.
        :param coef: (DataArray or None) The spectrum efficiency correction data.
        :raise ValueError: If the data and calibration data are not valid or
          compatible. In that case the current calibrated data is unchanged.
        """
        if data is None:
            data = self.raw[0]  # only one image in .raw for spectrum, temporal spectrum and chronograph

        if data is None:  # Very unlikely, but in that case, don't try too hard
            self.calibrated.value = None
            return

        # If MD_THETA_LIST, the length of the A dimension might be reduced
        calibrated = calibration.apply_spectrum_corrections(data, bckg, coef)
        self.calibrated.value = calibrated

    def _setBackground(self, bckg):
        """
        Setter of the background.
        :param bckg: ()
        :raises ValueError if it's impossible to apply it (eg, no wavelength info)
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


class FastEMOverviewStream(StaticSEMStream):
    # For now just a StaticStream with a different name, so the canvas can automatically select the right
    # blending option ("blend screen" on non-overlapping positions = simple pasting without blending)
    pass
