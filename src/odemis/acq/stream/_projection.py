# -*- coding: utf-8 -*-
'''
Created on 24 Jan 2017

@author: Guilherme Stiebler

Copyright © 2017 Guilherme Stiebler, Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
'''

import threading
import weakref
import logging
import time
import math
import gc
import numpy

from odemis.acq.stream import POL_POSITIONS
from odemis.model import TINT_FIT_TO_RGB

try:
    import arpolarimetry
except ImportError:
    pass  # The projection using this module should never be instantiated then.

from odemis import model
from odemis.util import img, angleres
from scipy import ndimage
from odemis.model import MD_PIXEL_SIZE, MD_POL_EPHI, MD_POL_EX, MD_POL_EY, MD_POL_EZ, MD_POL_ETHETA, MD_POL_DS0, \
    MD_POL_S0, MD_POL_DOP, MD_POL_DOLP, MD_POL_UP
from odemis.acq.stream._static import StaticSpectrumStream
from abc import abstractmethod


class DataProjection(object):

    def __init__(self, stream):
        '''
        stream (Stream): the Stream to project
        '''
        self.stream = stream
        self.name = stream.name
        self._im_needs_recompute = threading.Event()
        weak = weakref.ref(self)
        self._imthread = threading.Thread(target=self._image_thread,
                                          args=(weak,),
                                          name="Image computation")
        self._imthread.daemon = True
        self._imthread.start()

        # DataArray or None: RGB projection of the raw data
        self.image = model.VigilantAttribute(None)

    # FIXME: shouldn't be an abstractmethod, if not all the sub-classes overridde it
    @abstractmethod
    def projectAsRaw(self):
        """
        Project the data as raw; not RGB. This will allow the data to be processed
        by the caller.

        returns: None if failure, or a DataArray
        """
        return None

    @staticmethod
    def _image_thread(wprojection):
        """ Called as a separate thread, and recomputes the image whenever it receives an event
        asking for it.

        Args:
            wprojection (Weakref to a DataProjection): the data projection to follow

        """

        try:
            projection = wprojection()
            name = "%s:%x" % (projection.stream.name.value, id(projection))
            im_needs_recompute = projection._im_needs_recompute
            # Only hold a weakref to allow the stream to be garbage collected
            # On GC, trigger im_needs_recompute so that the thread can end too
            wprojection = weakref.ref(projection, lambda o: im_needs_recompute.set())

            tnext = 0
            while True:
                del projection
                im_needs_recompute.wait()  # wait until a new image is available
                projection = wprojection()

                if projection is None:
                    logging.debug("Projection %s disappeared so ending image update thread", name)
                    break

                tnow = time.time()

                # sleep a bit to avoid refreshing too fast
                tsleep = tnext - tnow
                if tsleep > 0.0001:
                    time.sleep(tsleep)

                tnext = time.time() + 0.1  # max 10 Hz
                im_needs_recompute.clear()
                projection._updateImage()
        except Exception:
            logging.exception("image update thread failed")

        gc.collect()

    def _shouldUpdateImage(self):
        """
        Ensures that the image VA will be updated in the "near future".
        """
        # If the previous request is still being processed, the event
        # synchronization allows to delay it (without accumulation).
        self._im_needs_recompute.set()


class RGBProjection(DataProjection):

    def __init__(self, stream):
        '''
        stream (Stream): the Stream to project
        '''
        super(RGBProjection, self).__init__(stream)

        self.image = model.VigilantAttribute(None)

        # Don't call at init, so don't set metadata if default value
        self.stream.tint.subscribe(self._onTint)
        self.stream.intensityRange.subscribe(self._onIntensityRange)

    def _find_metadata(self, md):
        return self.stream._find_metadata(md)

    @property
    def raw(self):
        if hasattr(self, "_raw"):
            return self._raw
        else:
            return self.stream.raw

    def _onIntensityRange(self, irange):
        logging.debug("Intensity range changed to %s", irange)
        self._shouldUpdateImageEntirely()

    def _onTint(self, value):
        self._shouldUpdateImageEntirely()

    def _shouldUpdateImageEntirely(self):
        """
        Indicate that the .image should be computed _and_ that all the previous
        tiles cached (and visible in the new image) have to be recomputed too
        """
        # set projected tiles cache as invalid
        self._projectedTilesInvalid = True
        self._shouldUpdateImage()

    def onTint(self, value):
        if len(self.stream.raw) > 0:
            raw = self.stream.raw[0]
        else:
            raw = None

        if raw is not None:
            # If the image is pyramidal, the exported image is based on tiles from .raw.
            # And the metadata from raw will be used to generate the metadata of the merged
            # image from the tiles. So, in the end, the exported image metadata will be based
            # on the raw metadata
            raw.metadata[model.MD_USER_TINT] = img.tint_to_md_format(value)

        self._shouldUpdateImage()

    def _project2RGB(self, data, tint=(255, 255, 255)):
        """
        Project a 2D DataArray into a RGB representation
        data (DataArray): 2D DataArray
        tint ((int, int, int)): colouration of the image, in RGB.
        return (DataArray): 3D DataArray
        """
        # TODO replace by local irange
        irange = self.stream._getDisplayIRange()
        rgbim = img.DataArray2RGB(data, irange, tint)
        rgbim.flags.writeable = False
        # Commented to prevent log flooding
        # if model.MD_ACQ_DATE in data.metadata:
        #     logging.debug("Computed RGB projection %g s after acquisition",
        #                    time.time() - data.metadata[model.MD_ACQ_DATE])
        md = self._find_metadata(data.metadata)
        md[model.MD_DIMS] = "YXC"  # RGB format
        return model.DataArray(rgbim, md)

    def projectAsRaw(self):
        """ Project a raw image without converting to RGB
        """
        raw = img.ensure2DImage(self.stream.raw[0])
        md = self._find_metadata(raw.metadata)
        return model.DataArray(raw, md)

    def _updateImage(self):
        """ Recomputes the image with all the raw data available
        """
        # logging.debug("Updating image")
        if not self.stream.raw:
            return

        try:
            raw = img.ensure2DImage(self.stream.raw[0])
            self.image.value = self._project2RGB(raw, self.stream.tint.value)
        except Exception:
            logging.exception("Updating %s %s image", self.__class__.__name__, self.stream.name.value)


class ARProjection(RGBProjection):
    """
    An ARProjection is a typical projection used to show raw 2D angle resolved images.
    Its .image contains a DataArray 2D RGB (shape YXC), with metadata MD_PIXEL_SIZE and MD_POS.
    The .background and .point VA are still on the stream passed in the __init__ as the same
    background image and ebeam position can be used for multiple projections connected to the same stream.
    """

    def __init__(self, stream):
        """
        :param stream: (Stream) The stream the projection is connected to.
        """
        super(ARProjection, self).__init__(stream)

        self.stream.point.subscribe(self._onPoint)
        self.stream.background.subscribe(self._onBackground)

        self._shouldUpdateImage()

    # overrides method in RGBProjection
    def _find_metadata(self, md):
        """
        Find the useful metadata for the projection.
        :returns: (dict) Metadata dictionary.
        """
        # Note: for polar view, no PIXEL_SIZE nor POS

        new_md = {}
        if model.MD_ACQ_DATE in md:
            new_md[model.MD_ACQ_DATE] = md[model.MD_ACQ_DATE]
        if model.MD_POL_MODE in md:
            new_md[model.MD_POL_MODE] = md[model.MD_POL_MODE]

        return new_md

    def _onBackground(self, data):
        """
        Called after the background has changed.
        :param data: (list) List of data arrays.
        """
        self._shouldUpdateImage()

    def _onPoint(self, pos):
        """
        Called when a new ebeam position has been selected.
        :param pos: (float, float) One key of .point.choices.
        """
        self._shouldUpdateImage()

    def _getBackground(self, pol_mode):
        """
        Get the background image from the .background VA on the stream.
        It must match the polarization position.
        :param pol_mode: (str) The polarization mode, the background is requested for.
        :return: (DataArray or None) The background image corresponding to the requested polarization
                position or None, if no matching background can be found.
        """
        bg_data = self.stream.background.value  # list containing DataArrays, DataArray or None

        if bg_data is None:
            return None

        if isinstance(bg_data, model.DataArray):
            bg_data = [bg_data]  # convert to list of bg images

        for bg in bg_data:
            # if no analyzer hardware, set MD_POL_MODE = "pass-through" (MD_POL_NONE)
            if bg.metadata.get(model.MD_POL_MODE, model.MD_POL_NONE) == pol_mode:
                # should be only one bg image with the same metadata entry
                return bg  # DataArray

        # Nothing found e.g. pol_mode = "rhc" but no bg image with "rhc"
        logging.debug("No background image with polarization mode %s ." % pol_mode)
        return None

    def _processBackground(self, data, pol_mode, clip_data=True):
        """
        Process the background on the raw data. Try to get the background image on the stream
        and subtracts it if available. Otherwise process do a simple background processing.
        :param data: (DataArray) The data that will be background corrected.
        :param pol_mode: (str) The polarization mode, the background is requested for.
        :param clip_data: (bool) If True, data is clipped at 0. If False (e.g. csv export), negative values are kept.
        :return: (DataArray) The background corrected data.
        """
        bg_image = self._getBackground(pol_mode)
        if bg_image is None:
            # Simple version: remove the background value, will clip data
            data_corr = angleres.ARBackgroundSubtract(data)
        else:
            if clip_data:
                data_corr = img.Subtract(data, bg_image)  # metadata from data
            else:
                # subtract bg image, but don't clip (keep negative values for export)
                data_corr = (data.astype(numpy.float64) - bg_image.astype(numpy.float64))

        return data_corr

    def _resizeImage(self, data, size):
        """
        Resize the image.
        :param data: (2D DataArray) Image to resize.
        :param size: (int) Size of the resized image in px. Size of the largest dimension. The aspect
                     ratio is kept, when computing the other dimension.
        :returns: (2D DataArray) Resized image.
        """
        # Note: AR conversion might fail with very large images due to too much memory consumed (> 2Gb).
        # So, rescale + use a "degraded" type that uses less memory. As the display size is small (compared
        # to the size of the input image, it shouldn't actually affect much the output.

        logging.info("AR image is very large %s, will convert to projection in reduced precision.", data.shape)

        y, x = data.shape
        if y > x:
            small_shape = size, int(round(size * x / y))
        else:
            small_shape = int(round(size * y / x)), size
        # resize
        image_resized = img.rescale_hq(data, small_shape)

        return image_resized


class ARRawProjection(ARProjection):
    """
    An ARRawProjection is a typical projection used to show raw 2D angle resolved images
    projected to polar representation.
    Its .image contains a DataArray 2D RGB (shape YXC), with metadata MD_PIXEL_SIZE and MD_POS.
    The .background and .point VA are still on the stream passed in the __init__ as the same
    background image and ebeam position can be used for multiple projections connected to the same stream.
    Additionally, if the stream has an attribute "polarization" it will calculate the polar
    representations for all polarization positions available from the raw ar data.
    """

    def __init__(self, stream):
        """
        :param stream: (Stream) The stream the projection is connected to.
        """
        super(ARRawProjection, self).__init__(stream)

        # Cached conversion of the detector image to polar representation
        self._polar_cache = {}  # dict tuple (float, float, str or None) -> DataArray
        # represents (ebeam posX, ebeam posY, polarization pos)

        if hasattr(stream, "polarization"):
            self.polarization = self.stream.polarization  # make it an attribute of the projection
            self.polarization.subscribe(self._onPolarization)

    def _project2Polar(self, ebeam_pos, pol_pos):
        """
        Return the polar projection of the image at the given position.
        :param ebeam_pos: (float, float), string or None) Ebeam position (must be part of the .stream._pos).
        :param pol_pos: (str or None) Polarization position (must be part of the .stream._pos).
        :returns: (2D DataArray) The polar projection.
        """
        # Note: Need a copy of the link to the dict. If self._polar_cache is reset while
        # still running this method, the dict might get new entries again, though it should be empty.
        polar_cache = self._polar_cache

        try:
            polar_data = polar_cache.setdefault(ebeam_pos, {})[pol_pos]
        except KeyError:
            # Compute the polar representation
            data = self.stream._pos[ebeam_pos + (pol_pos,)]
            # TODO: stream._pos can be then also be structured ebeam_pos/pol_pos.
            #   That would also simplify the check for the correct bg image etc.

            try:
                # Correct image for background. It must match the polarization (defaulting to MD_POL_NONE).
                calibrated = self._processBackground(data, data.metadata.get(model.MD_POL_MODE, model.MD_POL_NONE))

                # resize if too large to not run into memory problems
                if numpy.prod(calibrated.shape) > (1280 * 1080):
                    calibrated = self._resizeImage(calibrated, size=1024)

                # define the size of the image for polar representation in GUI
                # 2 x size of original/raw image (on smallest axis) and at most
                # the size of a full-screen canvas (1134)
                output_size = min(min(calibrated.shape) * 2, 1134)

                # TODO: could use the size of the canvas that will display the image to save some computation time.

                # Warning: allocates lot of memory, which will not be free'd until
                # the current thread is terminated.
                polar_data = angleres.AngleResolved2Polar(calibrated, output_size, hole=False)

                # TODO: don't hold too many of them in cache (eg, max 3 * 1134**2)
                polar_cache[ebeam_pos][pol_pos] = polar_data
            except Exception:
                logging.exception("Failed to convert to azimuthal projection")
                return data  # display its raw as fallback

        return polar_data

    def _updateImage(self):
        """
        Recomputes the image for the current ebeam position and polarization position requested.
        """
        if not self.raw:
            return

        ebeam_pos = self.stream.point.value
        try:
            if ebeam_pos == (None, None):
                self.image.value = None
            else:
                if hasattr(self.stream, "polarization"):
                    pol_pos = self.stream.polarization.value
                else:
                    pol_pos = None

                polar_data = self._project2Polar(ebeam_pos, pol_pos)

                # update the histogram
                # TODO: cache the histogram per image
                # FIXME: histogram should not include the black pixels outside
                # of the circle. => use a masked array?
                # reset the drange to ensure that it doesn't depend on older data
                self.stream._drange = None
                self.stream._updateHistogram(polar_data)

                self.image.value = self._project2RGB(polar_data, self.stream.tint.value)
        except Exception:
            logging.exception("Updating %s image", self.__class__.__name__)

    def _onPolarization(self, pos):
        """
        Called when the polarization VA has changed.
        :param pos: (str) Polarization position requested.
        """
        self._shouldUpdateImage()

    def _onBackground(self, data):
        """
        Called after the background has changed.
        :param data: (list) List of data arrays.
        """
        # un-cache all the polar images
        self._polar_cache = {}
        super(ARRawProjection, self)._onBackground(data)

    def projectAsRaw(self):
        """
        Returns the raw data for the currently selected pixel (ebeam position).
        :returns: (DataArray or dict: MD_POL_* -> DataArray)
            If there is no polarization, a single DataArray of shape 90,360 is returned
            corresponding to the data for angle of theta/phi.
            If there is polarization, a dictionary is returned containing data
            as described, but for every polarization analyzer positions.
        """
        ebeam_pos = self.stream.point.value  # ebeam pos selected
        data_dict = {}

        if hasattr(self, "polarization"):
            pol_positions = self.polarization.choices
        else:
            pol_positions = [None]

        for pol_pos in pol_positions:
            data = self.stream._pos[ebeam_pos + (pol_pos,)]

            # Correct image for background. It must match the polarization (defaulting to MD_POL_NONE).
            calibrated = self._processBackground(data, data.metadata.get(model.MD_POL_MODE, model.MD_POL_NONE),
                                                 clip_data=False)

            # resize if too large to not run into memory problems
            if numpy.prod(calibrated.shape) > (800 * 800):
                calibrated = self._resizeImage(calibrated, size=768)

            output_size = (90, 360)  # Note: increase if data is high def

            # calculate raw theta/phi representation
            data = angleres.AngleResolved2Rectangular(calibrated, output_size, hole=False)

            data.metadata[model.MD_ACQ_TYPE] = model.MD_AT_AR
            data_dict[pol_pos] = data

        # TODO for now we distinguish in export between dict and array...
        if len(pol_positions) > 1:
            return data_dict  # return the dict
        else:
            return next(iter(data_dict.values()))  # only one data array in dict

    def projectAsVis(self):
        """
        Returns the special (polar) visualized data as shown in the GUI of the polarimetry visualization for
        the currently selected pixel (ebeam position).
        :returns: (dict: MD_POL_* -> DataArray) Dictionary containing all images for the polarimetry visualization
                  for one pixel with metadata.
        """
        ebeam_pos = self.stream.point.value  # ebeam pos selected
        data_dict = {}

        if hasattr(self, "polarization"):
            for pol_pos in self.polarization.choices:
                data = self._project2Polar(ebeam_pos, pol_pos)
                data = self._project2RGB(data, self.stream.tint.value)
                data_dict[pol_pos] = data
        else:  # standard single AR image
            data_dict[None] = self.image.value

        return data_dict


class ARPolarimetryProjection(ARProjection):
    """
    An ARPolarimetryProjection is a typical projection used to show the polarimetry results in polar
    representation of the raw 2D angle resolved images acquired with a polarization analyzer.
    It is only instantiated when the stream has an attribute "polarimetry" (meaning the raw data was
    acquired for 6 different polarization analyzer positions).
    Its .image contains a DataArray 2D RGB (shape YXC), with metadata MD_PIXEL_SIZE and MD_POS.
    The .background and .point VA are still on the stream passed in the __init__ as the same
    background image and ebeam position can be used for multiple projections connected to the same stream.
    """

    def __init__(self, stream):
        """
        :param stream: (Stream) The stream the projection is connected to.
        """
        # If the stream does not have polarimetry data, the GUI tries anyway to have a projection.
        # In this case we have a simple projection, which has no data ever.
        if not hasattr(stream, "polarimetry"):
            self.image = model.VigilantAttribute(None)
            return

        super(ARPolarimetryProjection, self).__init__(stream)

        # Cached conversion of the raw 6 polarization images to:
        #   *Stokes parameters in the detector plane (4 images)
        #   *Stokes parameters in the sample plane (4 images)
        #   *E-fields in polar and Cartesian coordinates (5 images)
        #   *DOPs (degree of polarization) (4 images)
        # All images in polar representation.
        self._polarimetry_cache = {}  # dict tuple (float, float) -> dict(MD_POL_* (str) -> DataArray)
        # represents (ebeam posX, ebeam posY) -> (polarimetry pos -> DataArray)

        # TODO: share the raw data with the cache from ARRawProjection
        # Same as above, but the raw (aka rectangular representation -> phi/theta) images.
        # Images are background corrected.
        self._polarimetry_cache_raw = {}  # dict tuple (float, float) -> dict(MD_POL_* (str) -> DataArray)

        self.polarimetry = self.stream.polarimetry  # make VA an attribute of the projection
        self.polarimetry.subscribe(self._onPolarimetry)

    def _getRawData(self, ebeam_pos):
        """
        Gets the 6 polarization images for one pixel (ebeam) position and collects them in a dict.
        :param ebeam_pos: (tuple of 2 float) The ebeam position, the data is requested for.
        :returns: (dict: MD_POL_* (str) -> DataArray) A dictionary containing the 6 raw images -
                  one for each polarization analyzer position.
        """
        data_raw = {}
        for polpos in POL_POSITIONS:
            data_raw[polpos] = self.stream._pos[ebeam_pos + (polpos,)]

        return data_raw

    def _projectAsRaw(self, ebeam_pos):
        """
        Calculates the raw polarimetry visualization (rectangular phi/theta representation) of the images at the
        requested ebeam and polarimetry position.
        :param ebeam_pos: (float, float) Current ebeam (pixel) position (must be part of the .stream._pos).
        :returns: (dict(MD_POL_* (str) -> DataArray)) Cached conversion of the polarimetry
                  visualization results as raw images (aka rectangular representation -> phi/theta) for one ebeam
                  position. Images are background corrected.
        """
        # Note: Need a copy of the link to the dict. If self._polarimetry_cache is reset while
        # still running this method, the dict might get new entries again, though it should be empty.
        polarimetry_cache_raw = self._polarimetry_cache_raw

        # Note: Method needs about 4sec to display the image for selecting a new ebeam position
        if ebeam_pos not in polarimetry_cache_raw:
            # Compute the polarimetry representation
            data_raw = self._getRawData(ebeam_pos)  # get the 6 images for requested ebeam pos

            try:
                # Convert data into rectangular format (theta-phi-representation).
                # Check if rectangular converted representation already was calculated for requested ebeam pos.
                # Note: This calc is very time consuming. Takes about 3.6 sec for one ebeam pos (conversion of 6 images)
                # and tested on an image of size (256, 1024).

                # TODO get the raw/bg processed data from polar_cache, as now we do bg subtraction twice
                calibrated_raw = {}

                # TODO allow variable input size? Calc based on raw data? E.g. with binning
                # The number of pixels (theta, phi) of the output image.
                output_size = (400, 600)  # defines the resolution of the displayed image

                for pol, raw in data_raw.items():

                    # Correct image for background. It must match the polarization (defaulting to MD_POL_NONE).
                    calibrated = self._processBackground(raw, raw.metadata.get(model.MD_POL_MODE, model.MD_POL_NONE))

                    # check if image is too large and we might run into memory trouble -> resize
                    if numpy.prod(calibrated.shape) > (1280 * 1080):
                        calibrated = self._resizeImage(calibrated, size=1024)

                    # calculate the rectangular representation (phi/theta) of the background corrected raw images
                    calibrated_raw[pol] = angleres.AngleResolved2Rectangular(calibrated, output_size, hole=False)

                # Get the center wavelength of the filter used (no filter aka "pass-through" use fallback)
                # Does not matter from which of the 6 images as they all were recorded with the same filter
                band = next(iter(calibrated_raw.values())).metadata.get(model.MD_OUT_WL)
                if isinstance(band, tuple):  # wl is usually tuple of min/max value
                    wl = sum(band) / len(band)
                else:  # handles if band is str
                    # TODO if type is "str", support center wavelength based on color
                    wl = 650e-9

                # Warning: allocates lot of memory, which will not be free'd until
                # the current thread is terminated.

                # Calculate the polarimetry results for the requested ebeam pos (pixel):
                # Note: Takes about 0.25 sec to calc all polarimetry results for one ebeam pos
                # and tested on an image of size (256, 1024)
                polarimetry_cache_raw[ebeam_pos] = arpolarimetry.calcPolarimetry(calibrated_raw, wl)

                # set acq type on metadata
                md = {model.MD_ACQ_TYPE: model.MD_AT_AR}
                for polpos in polarimetry_cache_raw[ebeam_pos]:
                    # Note: already background corrected data in dict
                    polarimetry_cache_raw[ebeam_pos][polpos].metadata.update(md)

            except Exception:
                logging.exception("Failed to calculate raw polarimetry results for visualization.")
                return None

        return polarimetry_cache_raw[ebeam_pos]

    def _project2RGBPolar(self, ebeam_pos, pol_pos, cache_raw):
        """
        Returns the RGB polar representation of the polarimetry visualization at the requested ebeam and
        polarimetry position for GUI display.
        :param ebeam_pos: (float, float) Current ebeam (pixel) position (must be part of the .stream._pos).
        :param pol_pos: (str) Polarimetry position.
        :param cache_raw: (dict(MD_POL_* (str) -> DataArray)) Cached conversion of the polarimetry visualization
                          results as raw images (aka rectangular representation -> phi/theta) for one ebeam
                          position. Images are background corrected.
        :returns: (DataArray) The polarimetry visualization projection.
        """
        # Note: Need a copy of the link to the dict. If self._polarimetry_cache is reset while
        # still running this method, the dict might get new entries again, though it should be empty.
        polarimetry_cache = self._polarimetry_cache

        if ebeam_pos in polarimetry_cache and pol_pos in polarimetry_cache[ebeam_pos]:
            polarimetry_data = polarimetry_cache[ebeam_pos][pol_pos]
        else:
            try:
                # Note: Takes 0.24 sec to convert one image for display and tested on an image of size (256, 1024)
                # Create empty dict for processed ebeam position in cache
                polarimetry_cache[ebeam_pos] = {}

                # select a color map based on the data
                if pol_pos in [MD_POL_EPHI, MD_POL_ETHETA, MD_POL_EX, MD_POL_EY, MD_POL_EZ]:
                    data = numpy.abs(cache_raw[pol_pos])
                    max_val = data.max()
                    if max_val > 0:
                        plotorder = int(math.log10(max_val))
                    else:
                        # max_val == 0, because bg image and image are the same
                        # Let's not make too much fuss about it.
                        plotorder = 0

                    cache_raw[pol_pos] = data / 10 ** plotorder
                    colormap = "inferno"
                elif pol_pos in [MD_POL_DS0, MD_POL_S0, MD_POL_DOP, MD_POL_DOLP, MD_POL_UP]:
                    colormap = "viridis"
                else:
                    colormap = "seismic"

                # define the size of the image for polar representation in GUI
                # 3 x size of original/raw image (on smallest axis) and at most
                # the size of a full-screen canvas (1134)
                output_size = min(min(next(iter(self.stream.raw)).shape) * 3, 1134)

                # Convert the data to polar representation for GUI display.
                polarimetry_data = angleres.Rectangular2Polar(cache_raw[pol_pos], output_size,
                                                              colormap=colormap)

                new_md = self._find_metadata(polarimetry_data.metadata)
                new_md[model.MD_DIMS] = "YXC"
                polarimetry_cache[ebeam_pos][pol_pos] = model.DataArray(polarimetry_data, new_md)

                # return an array
                polarimetry_data = polarimetry_cache[ebeam_pos][pol_pos]

            except Exception:
                logging.exception("Failed to convert the raw polarimetry data to RGB polar representation.")
                return None

        return polarimetry_data

    def _updateImage(self):
        """
        Recomputes the image for the current ebeam position and polarimetry visualization requested.
        """

        if not self.raw:
            return

        # TODO check looks like it is called twice when loading a new image/bg image
        # TODO most likely on the histogram update, calc histogram in this class

        ebeam_pos = self.stream.point.value
        try:
            if ebeam_pos == (None, None):
                self.image.value = None
            else:
                pol_pos = self.stream.polarimetry.value

                # Calculate the raw polarimetry data (phi/theta representation).
                # Note: If already done before for the requested pixel (ebeam pos), method will immediately return.
                cache_raw = self._projectAsRaw(ebeam_pos)
                if cache_raw is None:
                    self.image.value = None
                    return

                # Project the raw polarimetry data to RGB polar representation.
                polarimetry_data = self._project2RGBPolar(ebeam_pos, pol_pos, cache_raw)

                # TODO histogram on projection instead of stream
                # self.stream._drange = None
                # image_greyscale = img.RGB2Greyscale(self.stream._polarimetry[pos + (pol,)])
                # self.stream._updateHistogram(image_greyscale)

                self.image.value = polarimetry_data

        except Exception:
            logging.exception("Updating %s image", self.__class__.__name__)

    def _onPolarimetry(self, pos):
        """
        Called when the polarimetry VA has changed.
        :param pos: (str) Polarimetry visualization requested.
        """
        self._shouldUpdateImage()

    def _onBackground(self, data):
        """
        Called after the background has changed.
        :param data: (list) List of data arrays.
        """
        # un-cache all the polar images
        self._polarimetry_cache = {}
        self._polarimetry_cache_raw = {}
        super(ARPolarimetryProjection, self)._onBackground(data)

    def projectAsRaw(self):
        """
        Returns the raw data of the polarimetry visualization for the currently selected pixel (ebeam position).
        :returns: (dict: MD_POL_* (str) -> DataArray) Dictionary containing all images for the polarimetry visualization
                  for one pixel with metadata.
        """
        return self._projectAsRaw(self.stream.point.value)

    def projectAsVis(self):
        """
        Returns the special (polar) visualized data as shown in the GUI of the polarimetry visualization for
        the currently selected pixel (ebeam position).
        :returns: (dict: MD_POL_* (str) -> DataArray) Dictionary containing all images for the polarimetry visualization
                  for one pixel with metadata.
        """
        ebeam_pos = self.stream.point.value  # ebeam pos selected
        data_dict = {}

        for pol_pos in self.polarimetry.choices:
            try:
                # the visualized data is only calculated when the user selects the corresponding
                # value in the legend --> therefore there might be still some visualizations that need calculation
                data = self._polarimetry_cache[ebeam_pos][pol_pos]
            except KeyError:
                # Note: If this method is called, while cache is empty e.g. the bg image was loaded (which empties
                # the cache), recalculate raw (should always work).
                raw = self._projectAsRaw(ebeam_pos)
                data = self._project2RGBPolar(ebeam_pos, pol_pos, raw)

            data_dict[pol_pos] = model.DataArray(data, data.metadata)

        return data_dict


class RGBSpatialProjection(RGBProjection):
    """
    An RGBSpatialProjection is a typical projection used to show 2D images.
    Its .image contains a DataArray 2D RGB (shape YXC), with metadata MD_PIXEL_SIZE and MD_POS.

    Depending on which type of stream is passed at creation, a more suitable subclass of
    RGBSpatialProjection might be created (via the use of the __new__ operator).
    That is the recommended way to create a RGBSpatialProjection.
    """

    def __new__(cls, stream):

        if isinstance(stream, StaticSpectrumStream):
            return super(RGBSpatialProjection, cls).__new__(RGBSpatialSpectrumProjection)
        else:
            return super(RGBSpatialProjection, cls).__new__(cls)

    def __init__(self, stream):
        '''
        stream (Stream): the Stream to project
        '''
        super(RGBSpatialProjection, self).__init__(stream)

        # handle z stack
        if model.hasVA(stream, "zIndex"):
            self.zIndex = stream.zIndex
            self.zIndex.subscribe(self._onZIndex)

        if stream.raw and isinstance(stream.raw[0], model.DataArrayShadow):
            # The raw tiles corresponding to the .image, updated whenever .image is updated
            self._raw = (())  # 2D tuple of DataArrays
            raw = stream.raw[0]
            md = raw.metadata
            # get the pixel size of the full image
            ps = md[model.MD_PIXEL_SIZE]
            max_mpp = ps[0] * (2 ** raw.maxzoom)
            # sets the mpp as the X axis of the pixel size of the full image
            mpp_rng = (ps[0], max_mpp)
            self.mpp = model.FloatContinuous(max_mpp, mpp_rng, setter=self._set_mpp)
            full_rect = img.getBoundingBox(raw)
            minx, miny, maxx, maxy = full_rect
            rect_range = ((minx, miny, minx, miny), (maxx, maxy, maxx, maxy))
            self.rect = model.TupleContinuous(full_rect, rect_range)
            self.mpp.subscribe(self._onMpp)
            self.rect.subscribe(self._onRect)
            # initialize the projected tiles cache
            self._projectedTilesCache = {}
            # initialize the raw tiles cache
            self._rawTilesCache = {}
            # When True, the projected tiles cache should be invalidated
            self._projectedTilesInvalid = True

        self._shouldUpdateImage()

    def _onMpp(self, mpp):
        self._shouldUpdateImage()

    def _onRect(self, rect):
        self._shouldUpdateImage()

    def _set_mpp(self, mpp):
        ps0 = self.mpp.range[0]
        exp = math.log(mpp / ps0, 2)
        exp = round(exp)
        return ps0 * 2 ** exp

    def _projectXY2RGB(self, data, tint=(255, 255, 255)):
        """
        Project a 2D spatial DataArray into a RGB representation
        data (DataArray): 2D DataArray
        tint ((int, int, int)): colouration of the image, in RGB.
        return (DataArray): 3D DataArray
        """
        # TODO replace by local irange
        irange = self.stream._getDisplayIRange()
        rgbim = img.DataArray2RGB(data, irange, tint)
        rgbim.flags.writeable = False
        # Commented to prevent log flooding
        # if model.MD_ACQ_DATE in data.metadata:
        #     logging.debug("Computed RGB projection %g s after acquisition",
        #                    time.time() - data.metadata[model.MD_ACQ_DATE])
        md = self._find_metadata(data.metadata)
        md[model.MD_DIMS] = "YXC"  # RGB format
        return model.DataArray(rgbim, md)

    def getPixelCoordinates(self, p_pos):
        """
        Translate physical coordinates into data pixel coordinates
        Args:
            p_pos(tuple float, float): the position in physical coordinates

        Returns(tuple int, int or None): the position in pixel coordinates or None if it's outside of the image

        """
        return self.stream.getPixelCoordinates(p_pos)

    def getRawValue(self, pixel_pos):
        """
        Translate pixel coordinates into raw pixel value
        Args:
            pixel_pos(tuple int, int): the position in pixel coordinates

        Returns: the raw "value" of the position. In case the raw data has more than 2 dimensions, it returns an array.
        Raise LookupError if raw data not found

        """

        raw = self.stream.raw
        if not raw:
            raise LookupError("Failed to find raw data for %s stream" % (self.stream,))
        # if raw is a DataArrayShadow, the image is pyramidal
        if isinstance(raw[0], model.DataArrayShadow):
            tx, px = divmod(pixel_pos[0], raw[0].tile_shape[0])
            ty, py = divmod(pixel_pos[1], raw[0].tile_shape[1])
            raw_tile = raw[0].getTile(tx, ty, 0)
            return raw_tile[py, px]
        else:
            return raw[0][pixel_pos[1], pixel_pos[0]]

    def _onZIndex(self, value):
        self._shouldUpdateImage()

    def getBoundingBox(self):
        '''
        Get the bounding box of the whole image, whether it`s tiled or not.
        return (tuple of floats(minx, miny, maxx, maxy)): Tuple with the bounding box
        '''
        if hasattr(self, 'rect'):
            rng = self.rect.range
            return rng[0][0], rng[0][1], rng[1][0], rng[1][1]
        else:
            return self.stream.getBoundingBox(self.image.value)

    def _zFromMpp(self):
        """
        Return the zoom level based on the current .mpp value
        return (int): The zoom level based on the current .mpp value
        """
        md = self.stream.raw[0].metadata
        ps = md[model.MD_PIXEL_SIZE]
        return int(math.log(self.mpp.value / ps[0], 2))

    def _rectWorldToPixel(self, rect):
        """
        Convert rect from world coordinates to pixel coordinates
        rect (tuple containing x1, y1, x2, y2): Rect on world coordinates where x1 < x2 and y1 < y2
        return (tuple containing x1, y1, x2, y2): Rect on pixel coordinates where x1 < x2 and y1 < y2
        """
        das = self.stream.raw[0]
        md = das.metadata
        ps = md.get(model.MD_PIXEL_SIZE, (1e-6, 1e-6))
        pos = md.get(model.MD_POS, (0, 0))
        # Removes the center coordinates of the image. After that, rect will be centered on 0, 0
        rect = (
            rect[0] - pos[0],
            rect[1] - pos[1],
            rect[2] - pos[0],
            rect[3] - pos[1]
        )
        dims = md.get(model.MD_DIMS, "CTZYX"[-das.ndim::])
        img_shape = (das.shape[dims.index('X')], das.shape[dims.index('Y')])

        # Converts rect from physical to pixel coordinates.
        # The received rect is relative to the center of the image, but pixel coordinates
        # are relative to the top-left corner. So it also needs to sum half image.
        # The -1 are necessary on the right and bottom sides (y coordinates), as the coordinates
        # of a pixel are -1 relative to the side of the pixel
        # The '-' before ps[1] plus switching the miny,maxy of world coordinates is necessary
        # due to the fact that Y in pixel coordinates grows down, and Y in physical coordinates grows up
        return (
            int(round(rect[0] / ps[0] + img_shape[0] / 2)),
            int(round(rect[3] / (-ps[1]) + img_shape[1] / 2)),
            int(round(rect[2] / ps[0] + img_shape[0] / 2)) - 1,
            int(round(rect[1] / (-ps[1]) + img_shape[1] / 2)) - 1,
        )

    def _getTile(self, x, y, z, prev_raw_cache, prev_proj_cache):
        """
        Get a tile from a DataArrayShadow. Uses cache.
        The cache for projected tiles and the cache for raw tiles has always the same tiles
        x (int): X coordinate of the tile
        y (int): Y coordinate of the tile
        z (int): zoom level where the tile is
        prev_raw_cache (dictionary): raw tiles cache from the
            last execution of _updateImage
        prev_proj_cache (dictionary): projected tiles cache from the
            last execution of _updateImage
        return (DataArray, DataArray): raw tile and projected tile
        """
        # the key of the tile on the cache
        tile_key = "%d-%d-%d" % (x, y, z)

        # if the raw tile has been already cached, read it from the cache
        if tile_key in prev_raw_cache:
            raw_tile = prev_raw_cache[tile_key]
        elif tile_key in self._rawTilesCache:
            raw_tile = self._rawTilesCache[tile_key]
        else:
            # The tile was not cached, so it must be read from the file
            raw_tile = self.stream.raw[0].getTile(x, y, z)

        # if the projected tile has been already cached, read it from the cache
        if tile_key in prev_proj_cache:
            proj_tile = prev_proj_cache[tile_key]
        elif tile_key in self._projectedTilesCache:
            proj_tile = self._projectedTilesCache[tile_key]
        else:
            # The tile was not cached, so it must be projected again
            proj_tile = self._projectTile(raw_tile)

        # cache raw and projected tiles
        self._rawTilesCache[tile_key] = raw_tile
        self._projectedTilesCache[tile_key] = proj_tile
        return raw_tile, proj_tile

    def _projectTile(self, tile):
        """
        Project the tile
        tile (DataArray): Raw tile
        return (DataArray): Projected tile
        """
        dims = tile.metadata.get(model.MD_DIMS, "CTZYX"[-tile.ndim::])
        ci = dims.find("C")  # -1 if not found
        # handle the tint
        tint = self.stream.tint.value

        if dims in ("CYX", "YXC") and tile.shape[ci] in (3, 4):  # is RGB?
            # Take the RGB data as-is, just needs to make sure it's in the right order
            tile = img.ensureYXC(tile)
            if isinstance(tint, tuple):  # Tint not white => adjust the RGB channels
                if tint != (255, 255, 255):
                    tile = tile.copy()
                    # Explicitly only use the first 3 values, to leave the alpha channel as-is
                    numpy.multiply(tile[..., 0:3], numpy.asarray(tint) / 255, out=tile[..., 0:3], casting="unsafe")
            else:
                logging.warning("Tuple Tint expected: got %s", tint)

            tile.flags.writeable = False
            # merge and ensures all the needed metadata is there
            tile.metadata = self.stream._find_metadata(tile.metadata)
            tile.metadata[model.MD_DIMS] = "YXC"  # RGB format
            return tile
        elif dims in ("ZYX",) and model.hasVA(self.stream, "zIndex"):
            tile = img.getYXFromZYX(tile, self.stream.zIndex.value)
            tile.metadata[model.MD_DIMS] = "ZYX"
        else:
            tile = img.ensure2DImage(tile)

        return self._projectXY2RGB(tile, tint)

    def _getTilesFromSelectedArea(self):
        """
        Get the tiles inside the region defined by .rect and .mpp
        return (DataArray, DataArray): Raw tiles and projected tiles
        """

        # This custom exception is used when the .mpp or .rect values changes while
        # generating the tiles. If the values changes, everything needs to be recomputed
        class NeedRecomputeException(Exception):
            pass

        das = self.stream.raw[0]

        # store the previous cache to use in this execution
        prev_raw_cache = self._rawTilesCache
        prev_proj_cache = self._projectedTilesCache
        # Execute at least once. If mpp and rect changed in
        # the last execution of the loops, execute again
        need_recompute = True
        while need_recompute:
            z = self._zFromMpp()
            rect = self._rectWorldToPixel(self.rect.value)
            # convert the rect coords to tile indexes
            rect = [l / (2 ** z) for l in rect]
            rect = [int(math.floor(l / das.tile_shape[0])) for l in rect]
            x1, y1, x2, y2 = rect
            # the 4 lines below avoids that lots of old tiles
            # stays in instance caches
            prev_raw_cache.update(self._rawTilesCache)
            prev_proj_cache.update(self._projectedTilesCache)
            # empty current caches
            self._rawTilesCache = {}
            self._projectedTilesCache = {}

            raw_tiles = []
            projected_tiles = []
            need_recompute = False
            try:
                for x in range(x1, x2 + 1):
                    rt_column = []
                    pt_column = []

                    for y in range(y1, y2 + 1):
                        # the projected tiles cache is invalid
                        if self._projectedTilesInvalid:
                            self._projectedTilesCache = {}
                            prev_proj_cache = {}
                            self._projectedTilesInvalid = False
                            raise NeedRecomputeException()

                        # check if the image changed in the middle of the process
                        if self._im_needs_recompute.is_set():
                            self._im_needs_recompute.clear()
                            # Raise the exception, so everything will be calculated again,
                            # but using the cache from the last execution
                            raise NeedRecomputeException()

                        raw_tile, proj_tile = \
                            self._getTile(x, y, z, prev_raw_cache, prev_proj_cache)
                        rt_column.append(raw_tile)
                        pt_column.append(proj_tile)

                    raw_tiles.append(tuple(rt_column))
                    projected_tiles.append(tuple(pt_column))

            except NeedRecomputeException:
                # image changed
                need_recompute = True

        return tuple(raw_tiles), tuple(projected_tiles)

    def _updateImage(self):
        """ Recomputes the image with all the raw data available
        """
        # logging.debug("Updating image")
        raw = self.stream.raw
        if not raw:
            return

        try:
            if isinstance(raw[0], model.DataArrayShadow):
                # DataArrayShadow => need to get each tile individually
                self._raw, projected_tiles = self._getTilesFromSelectedArea()
                self.image.value = projected_tiles
            else:
                self.image.value = self._projectTile(raw[0])

        except Exception:
            logging.exception("Updating %s %s image", self.__class__.__name__, self.stream.name.value)

    def projectAsRaw(self):
        """ Project a raw image without converting to RGB

        Handles tiles as well as regular DataArray's
        """
        raw = self.stream.raw

        if isinstance(raw[0], model.DataArrayShadow):
            raw_tiles, _ = self._getTilesFromSelectedArea()
            raw = img.mergeTiles(raw_tiles)
            return raw
        else:
            return super(RGBSpatialProjection, self).projectAsRaw()


class RGBSpatialSpectrumProjection(RGBSpatialProjection):
    """
    This child of RGBSpatialProjection is created when a Spectrum stream is detected by the
    RGBSpatialProjection upon class creation in the __new__ function.
    """

    def __init__(self, stream):

        super(RGBSpatialSpectrumProjection, self).__init__(stream)
        stream.selected_pixel.subscribe(self._on_selected_pixel)
        stream.calibrated.subscribe(self._on_new_spec_data)
        if hasattr(stream, "spectrumBandwidth"):
            stream.spectrumBandwidth.subscribe(self._on_spectrumBandwidth)
        if hasattr(stream, "tint"):
            stream.tint.subscribe(self._on_tint)
        self._updateImage()

    def _on_tint(self, _):
        self._shouldUpdateImage()

    def _on_new_spec_data(self, _):
        self._shouldUpdateImage()

    def _on_selected_pixel(self, _):
        self._shouldUpdateImage()

    def _on_spectrumBandwidth(self, _):
        self._shouldUpdateImage()

    def projectAsRaw(self):
        try:
            data = self.stream.calibrated.value
            raw_md = self.stream.calibrated.value.metadata
            md = {k: raw_md[k] for k in (model.MD_PIXEL_SIZE, model.MD_POS, model.MD_THETA_LIST) if k in raw_md}

            # Average time or theta values if they exist (iow, flatten axis 1).
            if data.shape[1] > 1:
                data = numpy.mean(data, axis=1)
                data = data[:, 0, :, :]
            else:
                data = data[:, 0, 0, :, :]

            # pick only the data inside the bandwidth
            spec_range = self.stream._get_bandwidth_in_pixel()

            logging.debug("Spectrum range picked: %s px", spec_range)

            av_data = numpy.mean(data[spec_range[0]:spec_range[1] + 1], axis=0)
            av_data = img.ensure2DImage(av_data).astype(data.dtype)
            return model.DataArray(av_data, md)

        except Exception:
            logging.exception("Projecting %s %s raw image", self.__class__.__name__, self.stream.name.value)

    def getRawValue(self, pixel_pos):
        """
        Translate pixel coordinates into raw pixel value
            Args:
            pixel_pos(tuple int, int): the position in pixel coordinates

        Returns(float): the raw value of the position
        """
        spec = self.stream.calibrated.value[..., pixel_pos[1], pixel_pos[0]]

        # Average time or theta values if they exist (iow, flatten axis 1).
        if spec.shape[1] > 1:
            data = numpy.mean(spec, axis=1)
            data = data[:, 0]
        else:
            data = spec[:, 0, 0]

        # pick only the data inside the bandwidth
        spec_range = self.stream._get_bandwidth_in_pixel()

        # TODO: update the condition with self.stream.tint.value != "fittorgb"
        if not hasattr(self.stream, "fitToRGB") or not self.stream.fitToRGB.value:
            # Use .tolist() to force scalar (instead of array of 0 dims)
            av_data = numpy.mean(data[spec_range[0]:spec_range[1] + 1]).tolist()
            return av_data
        else:
            # divide the range into 3 sub-ranges (BRG) of almost the same length
            len_rng = spec_range[1] - spec_range[0] + 1
            brange = [spec_range[0], int(round(spec_range[0] + len_rng / 3)) - 1]
            grange = [brange[1] + 1, int(round(spec_range[0] + 2 * len_rng / 3)) - 1]
            rrange = [grange[1] + 1, spec_range[1]]
            # ensure each range contains at least one pixel
            brange[1] = max(brange)
            grange[1] = max(grange)
            rrange[1] = max(rrange)

            av_b = numpy.mean(data[brange[0]:brange[1] + 1]).tolist()
            av_g = numpy.mean(data[grange[0]:grange[1] + 1]).tolist()
            av_r = numpy.mean(data[rrange[0]:rrange[1] + 1]).tolist()

            return av_b, av_g, av_r

    def _updateImage(self):
        """
        Recomputes the image with all the raw data available

        Project a spectrum cube (CTYX) to XY space in RGB, by averaging the
          intensity over all the wavelengths (selected by the user)
        data (DataArray or None): if provided, will use the cube, otherwise,
          will use the whole data from the stream.
        Updates self.image with  a DataArray YXC of uint8 or YX of same data type as data: average
          intensity over the selected wavelengths
        """

        try:
            data = self.stream.calibrated.value
            raw_md = self.stream.calibrated.value.metadata

            # Average time or theta values if they exist (iow, flatten axis 1).
            if data.shape[1] > 1:
                data = numpy.mean(data, axis=1)
                data = data[:, 0, :, :]
            else:
                data = data[:, 0, 0, :, :]

            # pick only the data inside the bandwidth
            spec_range = self.stream._get_bandwidth_in_pixel()

            logging.debug("Spectrum range picked: %s px", spec_range)

            irange = self.stream._getDisplayIRange()  # will update histogram if not yet present

            if self.stream.tint.value != TINT_FIT_TO_RGB:
                # TODO: use better intermediary type if possible?, cf semcomedi
                av_data = numpy.mean(data[spec_range[0]:spec_range[1] + 1], axis=0)
                av_data = img.ensure2DImage(av_data)
                rgbim = img.DataArray2RGB(av_data, irange, self.stream.tint.value)

            else:
                # Note: For now this method uses three independent bands. To give
                # a better sense of continuum, and be closer to reality when using
                # the visible light's band, we should take a weighted average of the
                # whole spectrum for each band. But in practice, that would be less
                # useful.

                # divide the range into 3 sub-ranges (BRG) of almost the same length
                len_rng = spec_range[1] - spec_range[0] + 1
                brange = [spec_range[0], int(round(spec_range[0] + len_rng / 3)) - 1]
                grange = [brange[1] + 1, int(round(spec_range[0] + 2 * len_rng / 3)) - 1]
                rrange = [grange[1] + 1, spec_range[1]]
                # ensure each range contains at least one pixel
                brange[1] = max(brange)
                grange[1] = max(grange)
                rrange[1] = max(rrange)

                # FIXME: unoptimized, as each channel is duplicated 3 times, and discarded
                av_data = numpy.mean(data[rrange[0]:rrange[1] + 1], axis=0)
                av_data = img.ensure2DImage(av_data)
                rgbim = img.DataArray2RGB(av_data, irange)
                av_data = numpy.mean(data[grange[0]:grange[1] + 1], axis=0)
                av_data = img.ensure2DImage(av_data)
                gim = img.DataArray2RGB(av_data, irange)
                rgbim[:, :, 1] = gim[:, :, 0]
                av_data = numpy.mean(data[brange[0]:brange[1] + 1], axis=0)
                av_data = img.ensure2DImage(av_data)
                bim = img.DataArray2RGB(av_data, irange)
                rgbim[:, :, 2] = bim[:, :, 0]

            rgbim.flags.writeable = False
            md = self._find_metadata(raw_md)
            md[model.MD_DIMS] = "YXC"  # RGB format
            raw = model.DataArray(rgbim, md)
            self.image.value = raw

        except Exception:
            logging.exception("Updating %s %s image", self.__class__.__name__, self.stream.name.value)


class LineSpectrumProjection(RGBProjection):
    """
    Project a spectrum from the selected_line of the stream.
    """

    def __init__(self, stream):

        super(LineSpectrumProjection, self).__init__(stream)

        if model.hasVA(self.stream, "selected_time"):
            self.stream.selected_time.subscribe(self._on_selected_time)
        if model.hasVA(self.stream, "selected_angle"):
            self.stream.selected_angle.subscribe(self._on_selected_angle)
        self.stream.selectionWidth.subscribe(self._on_selected_width)
        self.stream.selected_line.subscribe(self._on_selected_line)
        self.stream.calibrated.subscribe(self._on_new_data)
        self._shouldUpdateImage()

    def _on_new_data(self, _):
        self._shouldUpdateImage()

    def _on_selected_width(self, _):
        self._shouldUpdateImage()

    def _on_selected_line(self, _):
        self._shouldUpdateImage()

    def _on_selected_time(self, _):
        self._shouldUpdateImage()

    def _on_selected_angle(self, _):
        self._shouldUpdateImage()

    def _find_metadata(self, md):
        return md  # The data from _computeSpec() should already be correct

    def _computeSpec(self):
        """
        Compute the 1D spectrum from the stream.calibrated VA using the
        selected_time, selected_line, and width.

        return DataArray of of shape XC: the line spectrum
           the distance increases in X and the wavelength increases in C
        """

        if ((None, None) in self.stream.selected_line.value or
                self.stream.calibrated.value.shape[0] == 1):
            return None

        if model.hasVA(self.stream, "selected_time"):
            t = numpy.searchsorted(self.stream._tl_px_values, self.stream.selected_time.value)
        elif model.hasVA(self.stream, "selected_angle"):
            t = numpy.searchsorted(self.stream._thetal_px_values, self.stream.selected_angle.value)
        else:
            t = 0

        spec2d = self.stream.calibrated.value[:, t, 0, :, :]  # same data but remove useless dims
        width = self.stream.selectionWidth.value

        # Number of points to return: the length of the line
        start, end = self.stream.selected_line.value
        v = (end[0] - start[0], end[1] - start[1])
        l = math.hypot(*v)
        n = 1 + int(l)
        if l < 1:  # a line of just one pixel is considered not valid
            return None

        # FIXME: if the data has a width of 1 (ie, just a line), and the
        # requested width is an even number, the output is empty (because all
        # the interpolated points are outside of the data.

        # Coordinates of each point: ndim of data (5-2), pos on line (Y), spectrum (X)
        # The line is scanned from the end till the start so that the spectra
        # closest to the origin of the line are at the bottom.
        coord = numpy.empty((3, width, n, spec2d.shape[0]))
        coord[0] = numpy.arange(spec2d.shape[0])  # spectra = all
        coord_spc = coord.swapaxes(2, 3)  # just a view to have (line) space as last dim
        coord_spc[-1] = numpy.linspace(start[0], end[0], n)  # X axis
        coord_spc[-2] = numpy.linspace(start[1], end[1], n)  # Y axis

        # Spread over the width
        # perpendicular unit vector
        pv = (-v[1] / l, v[0] / l)
        width_coord = numpy.empty((2, width))
        spread = (width - 1) / 2
        width_coord[-1] = numpy.linspace(pv[0] * -spread, pv[0] * spread, width)  # X axis
        width_coord[-2] = numpy.linspace(pv[1] * -spread, pv[1] * spread, width)  # Y axis

        coord_cw = coord[1:].swapaxes(0, 2).swapaxes(1, 3)  # view with coordinates and width as last dims
        coord_cw += width_coord

        # Interpolate the values based on the data
        if width == 1:
            # simple version for the most usual case
            spec1d = ndimage.map_coordinates(spec2d, coord[:, 0, :, :], order=1)
        else:
            # FIXME: the mean should be dependent on how many pixels inside the
            # original data were pick on each line. Currently if some pixels fall
            # out of the original data, the outside pixels count as 0.
            # force the intermediate values to float, as mean() still needs to run
            spec1d_w = ndimage.map_coordinates(spec2d, coord, output=numpy.float, order=1)
            spec1d = spec1d_w.mean(axis=0)
        assert spec1d.shape == (n, spec2d.shape[0])

        # Use metadata to indicate spatial distance between pixel
        pxs_data = self.stream.calibrated.value.metadata[MD_PIXEL_SIZE]

        if pxs_data[0] is not None:
            pxs = math.hypot(v[0] * pxs_data[0], v[1] * pxs_data[1]) / (n - 1)
        else:
            logging.warning("Pixel size should have two dimensions")
            return None

        raw_md = self.stream.calibrated.value.metadata
        md = raw_md.copy()
        md[model.MD_DIMS] = "XC"  # wavelength horizontal, distance vertical
        md[MD_PIXEL_SIZE] = (None, pxs)  # for the spectrum, use get_spectrum_range()
        return model.DataArray(spec1d, md)

    def projectAsRaw(self):
        try:
            return self._computeSpec()
        except Exception:
            logging.exception("Projected raw %s image", self.__class__.__name__)
            return None

    def _updateImage(self):
        """
        Recomputes the image with all the raw data available and
        return the 1D spectrum representing the (average) spectrum

        Updates self.image.value to None or DataArray with 3 dimensions:
          first axis (vertical, named X) is spatial (along the line, starting from 0 at the top),
          second axis (horizontal, named C) is spectrum.
          third axis is colour (RGB, but actually always greyscale)
          MD_PIXEL_SIZE[1] contains the spatial distance between each spectrum
          If the selected_line is not valid, .image is set to None
        """
        try:
            spec1d = self._computeSpec()

            if spec1d is None:
                self.image.value = None
                return

            # We cannot use the stream histogram, as it's linked to the spatial view,
            # which is limited to the spectrum center/band, while this data always
            # shows all the spectrum range all the time
            # => compute based on the AutoBC settings.

            if self.stream.auto_bc.value:
                hist, edges = img.histogram(spec1d)
                irange = img.findOptimalRange(hist, edges,
                                              self.stream.auto_bc_outliers.value / 100)
            else:
                # use the values requested by the user
                irange = sorted(self.stream.intensityRange.value)

            # Scale and convert to RGB image
            rgbim = img.DataArray2RGB(spec1d, irange, self.stream.tint.value)
            rgbim.flags.writeable = False
            md = self._find_metadata(spec1d.metadata)
            md[model.MD_DIMS] = "YXC"  # RGB format
            self.image.value = model.DataArray(rgbim, md)
        except Exception:
            logging.exception("Updating %s image", self.__class__.__name__)


class PixelTemporalSpectrumProjection(RGBProjection):
    """
    Project a temporal spectrum (typically from streak camera data) as a 2D
    RGB image of time vs. wavelength, for a given "selected_pixel".
    """

    def __init__(self, stream):

        super(PixelTemporalSpectrumProjection, self).__init__(stream)
        self.stream.selectionWidth.subscribe(self._on_selection_width)
        self.stream.selected_pixel.subscribe(self._on_selected_pixel)
        self.stream.calibrated.subscribe(self._on_new_data)

        self._shouldUpdateImage()

    def _on_new_data(self, _):
        self._shouldUpdateImage()

    def _on_selection_width(self, _):
        self._shouldUpdateImage()

    def _on_selected_pixel(self, _):
        self._shouldUpdateImage()

    def _find_metadata(self, md):
        return md  # The data from _computeSpec() should already be correct

    def _computeSpec(self):
        """
        Compute the temporal spectrum data array

        return (DataArray of shape TC, or None): The array, with the minimum time
          and wavelength at pixel 0,0 (ie, top-left).
          In case of failure or no selected_pixel, it returns None.
        """
        data = self.stream.calibrated.value

        if (self.stream.selected_pixel.value == (None, None) or
                (data.shape[1] == 1 or data.shape[0] == 1)):
            return None

        x, y = self.stream.selected_pixel.value

        spec2d = self.stream.calibrated.value[:, :, 0, :, :]  # same data but remove useless dims
        md = dict(data.metadata)
        md[model.MD_DIMS] = "TC"

        # We treat width as the diameter of the circle which contains the center
        # of the pixels to be taken into account
        width = self.stream.selectionWidth.value
        if width == 1:  # short-cut for simple case
            data = spec2d[:, :, y, x]
            data = numpy.swapaxes(data, 0, 1)
            return model.DataArray(data, md)

        radius = width / 2
        mean = img.mean_within_circle(spec2d, (x, y), radius)

        return model.DataArray(mean.astype(spec2d.dtype), md)

    def projectAsRaw(self):
        """
        Returns the raw for the current selected_pixel (with the calibration).
        return (DataArray of shape TC, or None): array with metadata
        """
        return self._computeSpec()

    def _updateImage(self):
        """
        Recomputes the image with all the raw data available

        Updates self.image with the temporal spectrum image for the given .selected_pixel
        """
        try:
            data = self._computeSpec()
            if data is not None:
                self.image.value = self._project2RGB(data, self.stream.tint.value)
            else:
                self.image.value = None

        except Exception:
            logging.exception("Updating %s %s image", self.__class__.__name__, self.stream.name.value)


class PixelAngularSpectrumProjection(RGBProjection):
    """
    Projects an angular spectrum as a 2D RGB image of angle vs wavelength, for a given "selected_pixel".
    """

    def __init__(self, stream):

        super(PixelAngularSpectrumProjection, self).__init__(stream)
        self.stream.selectionWidth.subscribe(self._on_selection_width)
        self.stream.selected_pixel.subscribe(self._on_selected_pixel)
        self.stream.calibrated.subscribe(self._on_new_data)

        self._shouldUpdateImage()

    def _on_new_data(self, _):
        self._shouldUpdateImage()

    def _on_selection_width(self, _):
        self._shouldUpdateImage()

    def _on_selected_pixel(self, _):
        self._shouldUpdateImage()

    def _find_metadata(self, md):
        return md

    def _computeSpec(self):
        """
        Computes the angular spectrum data array for a given pixel.
        Updates the metadata MD_DIMS with the new shape of the DataArray (2 dims: AC)
        where A represents the angle in EK imaging and C is the wavelength.
        Returns: DataArray (of shape AC, or None) after calibration with metadata.
        """
        data = self.stream.calibrated.value

        if (self.stream.selected_pixel.value == (None, None) or
                (data.shape[1] == 1 or data.shape[0] == 1)):
            return None

        x, y = self.stream.selected_pixel.value

        # Shape is CA1YX
        spec2d = self.stream.calibrated.value[:, :, 0, :, :]  # same data but remove useless dims
        md = dict(data.metadata)
        md[model.MD_DIMS] = "AC"

        # Width represents the diameter of the circle of the pixels,
        # whose centers are to be taken into account
        width = self.stream.selectionWidth.value
        if width == 1:  # short-cut for simple case
            data = spec2d[:, :, y, x]
            data = numpy.swapaxes(data, 0, 1)
            return model.DataArray(data, md)

        radius = width / 2
        mean = img.mean_within_circle(spec2d, (x, y), radius)

        mean = numpy.swapaxes(mean, 0, 1)
        return model.DataArray(mean.astype(spec2d.dtype), md)

    def projectAsRaw(self):
        """
        Returns the raw data for the current selected_pixel
        """
        return self._computeSpec()

    def _updateImage(self):
        """
        Recomputes the image with all the raw data available
        Updates the image with the angular spectrum DataArray for the given .selected_pixel
        """
        try:
            data = self._computeSpec()
            if data is not None:
                self.image.value = self._project2RGB(data, self.stream.tint.value)
            else:
                self.image.value = None
        except Exception:
            logging.exception("Updating %s %s image", self.__class__.__name__, self.stream.name.value)


class MeanSpectrumProjection(DataProjection):
    """
    Compute the global spectrum of the data as an average over all the pixels
    returns (numpy.ndarray of float): average intensity for each wavelength
     You need to use the metadata of the raw data to find out what is the
     wavelength for each pixel, but the range of wavelengthBandwidth is
     the same as the range of this spectrum.
    """

    def __init__(self, stream):
        super(MeanSpectrumProjection, self).__init__(stream)
        self.stream.calibrated.subscribe(self._on_new_spec_data)
        self._shouldUpdateImage()

    def _on_new_spec_data(self, _):
        self._shouldUpdateImage()

    # TODO: have an area VA which allows to specify the 2D region
    # within which the spectrum should be computed
    def _updateImage(self):
        """
        Recomputes the image with all the raw data available
        """
        data = self.stream.calibrated.value
        md = dict(data.metadata)
        md[model.MD_DIMS] = "C"

        # flatten all but the C dimension, for the average
        data = data.reshape((data.shape[0], numpy.prod(data.shape[1:])))
        av_data = numpy.mean(data, axis=1)

        self.image.value = model.DataArray(av_data, md)


class SinglePointSpectrumProjection(DataProjection):
    """
    Projects the (0D) spectrum belonging to the selected pixel. Displays the spectrum intensity for
    a single value of time (TemporalSpectrum is present) or angle (AngularSpectrum is present).
    """

    def __init__(self, stream):

        super(SinglePointSpectrumProjection, self).__init__(stream)
        self.stream.selected_pixel.subscribe(self._on_selected_pixel)
        self.stream.selectionWidth.subscribe(self._on_selected_width)
        if model.hasVA(self.stream, "selected_time"):
            self.stream.selected_time.subscribe(self._on_selected_time)
        if model.hasVA(self.stream, "selected_angle"):
            self.stream.selected_angle.subscribe(self._on_selected_angle)
        self.stream.calibrated.subscribe(self._on_new_spec_data, init=True)

    def _on_new_spec_data(self, _):
        self._shouldUpdateImage()

    def _on_selected_pixel(self, _):
        self._shouldUpdateImage()

    def _on_selected_width(self, _):
        self._shouldUpdateImage()

    def _on_selected_time(self, _):
        self._shouldUpdateImage()

    def _on_selected_angle(self, _):
        self._shouldUpdateImage()

    def _computeSpec(self):
        """
        Compute the spectrum from the stream with the current parameters.

        Returns: a 1-D DataArray or None if the spectrum could not be computed
        """
        data = self.stream.calibrated.value

        if (self.stream.selected_pixel.value == (None, None) or
                data is None or data.shape[0] == 1):
            return None

        x, y = self.stream.selected_pixel.value
        # t represents either the selected time or selected angle pixel value
        if model.hasVA(self.stream, "selected_time"):
            t = numpy.searchsorted(self.stream._tl_px_values, self.stream.selected_time.value)
        elif model.hasVA(self.stream, "selected_angle"):
            t = numpy.searchsorted(self.stream._thetal_px_values, self.stream.selected_angle.value)
        else:
            t = 0
        spec2d = self.stream.calibrated.value[:, t, 0, :, :]  # same data but remove useless dims

        md = dict(data.metadata)
        md[model.MD_DIMS] = "C"

        # We treat width as the diameter of the circle which contains the center
        # of the pixels to be taken into account
        width = self.stream.selectionWidth.value
        if width == 1:  # short-cut for simple case
            data = spec2d[:, y, x]
            return model.DataArray(data, md)

        radius = width / 2
        mean = img.mean_within_circle(spec2d, (x, y), radius)

        return model.DataArray(mean, md)

    def projectAsRaw(self):
        return self._computeSpec()

    def _updateImage(self):
        """
        Recomputes the image with all the raw data available

        Update .image to None or DataArray with 1 dimension: the spectrum of the given
        pixel or None if no spectrum is selected.
        """
        try:
            self.image.value = self._computeSpec()
        except Exception:
            logging.exception("Updating %s %s image", self.__class__.__name__, self.stream.name.value)


class SinglePointTemporalProjection(DataProjection):
    """
    Project the (0D) temporal data belonging to the selected pixel.
    """

    def __init__(self, stream):

        super(SinglePointTemporalProjection, self).__init__(stream)
        self.stream.selected_pixel.subscribe(self._on_selected_pixel)
        self.stream.selectionWidth.subscribe(self._on_selected_width)
        if model.hasVA(self.stream, "selected_wavelength"):
            self.stream.selected_wavelength.subscribe(self._on_selected_wl)
            self.stream.calibrated.subscribe(self._on_new_spec_data)
        self._shouldUpdateImage()

    def _on_new_spec_data(self, _):
        self._shouldUpdateImage()

    def _on_selected_pixel(self, _):
        self._shouldUpdateImage()

    def _on_selected_width(self, _):
        self._shouldUpdateImage()

    def _on_selected_wl(self, _):
        self._shouldUpdateImage()

    def _computeSpec(self):

        if self.stream.selected_pixel.value == (None, None) or self.stream.calibrated.value.shape[1] == 1:
            return None

        x, y = self.stream.selected_pixel.value
        if model.hasVA(self.stream, "selected_wavelength"):
            c = numpy.searchsorted(self.stream._wl_px_values, self.stream.selected_wavelength.value)
        else:
            c = 0
        chrono2d = self.stream.calibrated.value[c, :, 0, :, :]  # same data but remove useless dims

        md = {model.MD_DIMS: "T"}
        if model.MD_TIME_LIST in chrono2d.metadata:
            md[model.MD_TIME_LIST] = chrono2d.metadata[model.MD_TIME_LIST]

        # We treat width as the diameter of the circle which contains the center
        # of the pixels to be taken into account
        width = self.stream.selectionWidth.value
        if width == 1:  # short-cut for simple case
            data = chrono2d[:, y, x]
            return model.DataArray(data, md)

        radius = width / 2
        mean = img.mean_within_circle(chrono2d, (x, y), radius)

        return model.DataArray(mean.astype(chrono2d.dtype), md)

    def projectAsRaw(self):
        return self._computeSpec()

    def _updateImage(self):
        """
        Recomputes the image with all the raw data available

        Updates .image with None or a DataArray with 1 dimension: the spectrum of the given
         pixel or None if no spectrum is selected.
        """
        try:
            self.image.value = self._computeSpec()

        except Exception:
            logging.exception("Updating %s %s image", self.__class__.__name__, self.stream.name.value)


class SinglePointAngularProjection(DataProjection):
    """
    Projects the (0D) angular data belonging to the selected pixel. Displays the angular data for a
    selected wavelength or a mean of wavelengths (depending on the width value)
    """

    def __init__(self, stream):

        super(SinglePointAngularProjection, self).__init__(stream)
        self.stream.selected_pixel.subscribe(self._on_selected_pixel)
        self.stream.selectionWidth.subscribe(self._on_selected_width)
        if model.hasVA(self.stream, "selected_wavelength"):
            self.stream.selected_wavelength.subscribe(self._on_selected_wl)
            self.stream.calibrated.subscribe(self._on_new_spec_data)
        self._shouldUpdateImage()

    def _on_new_spec_data(self, _):
        self._shouldUpdateImage()

    def _on_selected_pixel(self, _):
        self._shouldUpdateImage()

    def _on_selected_width(self, _):
        self._shouldUpdateImage()

    def _on_selected_wl(self, _):
        self._shouldUpdateImage()

    def _computeSpec(self):

        if self.stream.selected_pixel.value == (None, None) or self.stream.calibrated.value.shape[1] == 1:
            return None

        x, y = self.stream.selected_pixel.value
        if model.hasVA(self.stream, "selected_wavelength"):
            c = numpy.searchsorted(self.stream._wl_px_values, self.stream.selected_wavelength.value)
        else:
            c = 0
        angle2d = self.stream.calibrated.value[c, :, 0, :, :]  # same data but remove useless dims

        md = {model.MD_DIMS: "A"}
        if model.MD_THETA_LIST in angle2d.metadata:
            md[model.MD_THETA_LIST] = angle2d.metadata[model.MD_THETA_LIST]

        # We treat width as the diameter of the circle which contains the center
        # of the pixels to be taken into account
        width = self.stream.selectionWidth.value
        if width == 1:  # short-cut for simple case
            data = angle2d[:, y, x]
            return model.DataArray(data, md)

        radius = width / 2
        mean = img.mean_within_circle(angle2d, (x, y), radius)

        return model.DataArray(mean.astype(angle2d.dtype), md)

    def projectAsRaw(self):
        return self._computeSpec()

    def _updateImage(self):
        """
        Recomputes the image with all the raw data available

        Updates .image with None or a DataArray with 1 dimension: the spectrum of the given
         pixel or None if no spectrum is selected.
        """
        try:
            self.image.value = self._computeSpec()

        except Exception:
            logging.exception("Updating %s %s image", self.__class__.__name__, self.stream.name.value)
