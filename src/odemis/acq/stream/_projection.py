#!/usr/bin/env python
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

from __future__ import division

import threading
import weakref
import logging
import time
import math
import gc
import numpy

from odemis import model
from odemis.util import img
from scipy import ndimage
from odemis.model import MD_PIXEL_SIZE
from odemis.acq.stream._static import StaticSpectrumStream


class DataProjection(object):

    def __init__(self, stream):
        '''
        stream (Stream): the Stream to project
        '''
        self.stream = stream
        self. acquisitionType = stream.acquisitionType
        self._im_needs_recompute = threading.Event()
        weak = weakref.ref(self)
        self._imthread = threading.Thread(target=self._image_thread,
                                          args=(weak,),
                                          name="Image computation")
        self._imthread.daemon = True
        self._imthread.start()

        # DataArray or None: RGB projection of the raw data
        self.image = model.VigilantAttribute(None)

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


class RGBSpatialProjection(DataProjection):

    def __new__(cls, stream):

        if isinstance(stream, StaticSpectrumStream):
            return super(RGBSpatialProjection, cls).__new__(RGBSpatialSpectrumProjection, stream)
        else:
            return super(RGBSpatialProjection, cls).__new__(RGBSpatialProjection, stream)

    def __init__(self, stream):
        '''
        stream (Stream): the Stream to project
        '''
        super(RGBSpatialProjection, self).__init__(stream)

        self.should_update = model.BooleanVA(False)
        self.name = stream.name
        self.image = model.VigilantAttribute(None)

        # Don't call at init, so don't set metadata if default value
        self.stream.tint.subscribe(self._onTint)
        self.stream.intensityRange.subscribe(self._onIntensityRange)
        self.stream.auto_bc.subscribe(self._onAutoBC)
        self.stream.auto_bc_outliers.subscribe(self._onOutliers)

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
            maxLineSpectrumProjection_mpp = ps[0] * (2 ** raw.maxzoom)
            # sets the mpp as the X axis of the pixel size of the full image
            mpp_rng = (ps[0], max_mpp)
            self.mpp = model.FloatContinuous(max_mpp, mpp_rng, setter=self._set_mpp)

            full_rect = img._getBoundingBox(raw)
            l, t, r, b = full_rect
            rect_range = ((l, b, l, b), (r, t, r, t))
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

    def _find_metadata(self, md):
        return self.stream._find_metadata(md)

    @property
    def raw(self):
        if hasattr(self, "_raw"):
            return self._raw
        else:
            return self.stream.raw

    def _onAutoBC(self, enabled):
        # if changing to auto: B/C might be different from the manual values
        if enabled:
            self.needImageUpdate()

    def _onOutliers(self, outliers):
        if self.stream.auto_bc.value:
            self.needImageUpdate()

    def _onIntensityRange(self, irange):
        # If auto_bc is active, it updates intensities (from _updateImage()),
        # so no need to refresh image again.
        if not self.stream.auto_bc.value:
            self.needImageUpdate()

    def _onTint(self, value):
        self.needImageUpdate()

    def needImageUpdate(self):
        # set projected tiles cache as invalid
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
        md[model.MD_DIMS] = "YXC" # RGB format
        return model.DataArray(rgbim, md)

    def _onZIndex(self, value):
        self._shouldUpdateImage()

    def getBoundingBox(self):
        ''' Get the bounding box of the whole image, whether it`s tiled or not.
        return (tuple of floats(l,t,r,b)): Tuple with the bounding box
        Raises:
            ValueError: If the .image member is not set
        '''
        if hasattr(self, 'rect'):
            rng = self.rect.range
            return (rng[0][0], rng[0][1], rng[1][0], rng[1][1])
        else:
            im = self.image.value
            if im is None:
                raise ValueError("Stream's image not defined")
            md = im.metadata
            pxs = md.get(model.MD_PIXEL_SIZE, (1e-6, 1e-6))
            pos = md.get(model.MD_POS, (0, 0))
            w, h = im.shape[1] * pxs[0], im.shape[0] * pxs[1]
            return [pos[0] - w / 2, pos[1] - h / 2, pos[0] + w / 2, pos[1] + h / 2]

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
        rect (tuple containing x1, y1, x2, y2): Rect on world coordinates
        return (tuple containing x1, y1, x2, y2): Rect on pixel coordinates
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
        # The -1 are necessary on the right and bottom sides, as the coordinates of a pixel
        # are -1 relative to the side of the pixel
        # The '-' before ps[1] is necessary due to the fact that 
        # Y in pixel coordinates grows down, and Y in physical coordinates grows up
        return (
            int(round(rect[0] / ps[0] + img_shape[0] / 2)),
            int(round(rect[1] / (-ps[1]) + img_shape[1] / 2)),
            int(round(rect[2] / ps[0] + img_shape[0] / 2)) - 1,
            int(round(rect[3] / (-ps[1]) + img_shape[1] / 2)) - 1,
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
        # is RGB
        if dims in ("CYX", "YXC") and tile.shape[ci] in (3, 4):
            # Just pass the RGB data on
            tile = img.ensureYXC(tile)
            tile.flags.writeable = False
            # merge and ensures all the needed metadata is there
            tile.metadata = self.stream._find_metadata(tile.metadata)
            tile.metadata[model.MD_DIMS] = "YXC" # RGB format
            return tile
        elif dims in ("ZYX",):
            if tile.ndim != 2 and model.hasVA(self, "zIndex"):
                tile = img.getYXFromZYX(tile, self.zIndex.value)  # Remove extra dimensions (of length 1)
                tile.metadata[model.MD_DIMS] = "ZYX"
        else:
            tile = img.ensure2DImage(tile)

        return self._projectXY2RGB(tile, self.stream.tint.value)

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
            logging.exception("Updating %s %s image", self.__class__.__name__, self.name.value)


class SinglePointSpectrumProjection(DataProjection):
    """
    Project the (0D) spectrum belonging to the selected pixel.
    See get_spectrum_range() to know the wavelength values for each index of
     the spectrum dimension
    return (None or DataArray with 1 dimension): the spectrum of the given
     pixel or None if no spectrum is selected.
    """
    def __init__(self, stream):

        super(SinglePointSpectrumProjection, self).__init__(stream)

        if hasattr(stream, "selected_pixel"):
            self.selected_pixel = stream.selected_pixel
            self.selected_pixel.subscribe(self._on_selected_pixel)

        if hasattr(stream, "selectionWidth"):
            self.selectionWidth = stream.selectionWidth
            self.selectionWidth.subscribe(self._on_selected_width)
            
        if hasattr(stream, "selected_time"):
            self.selected_time = stream.selected_time
            self.selected_time.subscribe(self._on_selected_time)

    def _on_selected_pixel(self, _):
        self._shouldUpdateImage()

    def _on_selected_width(self, _):
        self._shouldUpdateImage()

    def _on_selected_time(self, _):
        self._shouldUpdateImage()

    def _updateImage(self):
        """ Recomputes the image with all the raw data available
        """
        # logging.debug("Updating image")
        if self.selected_pixel.value == (None, None):
            return

        try:
            # if .raw is a list of DataArray, .image is a complete image
            if isinstance(self.stream.raw, list):
                x, y = self.selected_pixel.value
                if model.hasVA(self.stream, "selected_time"):
                    t = self.stream._tl_px_values.index(self.selected_time.value)
                else:
                    t = 0
                spec2d = self.stream._calibrated[:, t, 0, :, :]  # same data but remove useless dims

                # We treat width as the diameter of the circle which contains the center
                # of the pixels to be taken into account
                width = self.selectionWidth.value
                if width == 1:  # short-cut for simple case
                    raw = spec2d[:, y, x]
                    self.image.value = raw
                    return

                # There are various ways to do it with numpy. As typically the spectrum
                # dimension is big, and the number of pixels to sum is small, it seems
                # the easiest way is to just do some kind of "clever" mean. Using a
                # masked array would also work, but that'd imply having a huge mask.
                radius = width / 2
                n = 0
                # TODO: use same cleverness as mean() for dtype?
                datasum = numpy.zeros(spec2d.shape[0], dtype=numpy.float64)
                # Scan the square around the point, and only pick the points in the circle
                for px in range(max(0, int(x - radius)),
                                min(int(x + radius) + 1, spec2d.shape[-1])):
                    for py in range(max(0, int(y - radius)),
                                    min(int(y + radius) + 1, spec2d.shape[-2])):
                        if math.hypot(x - px, y - py) <= radius:
                            n += 1
                            datasum += spec2d[:, py, px]

                mean = datasum / n
                raw = model.DataArray(mean.astype(spec2d.dtype))

                self.image.value = raw

        except Exception:
            logging.exception("Updating %s %s image", self.__class__.__name__, self.name.value)

class SinglePointChronoProjection(DataProjection):
    """
    Project the (0D) spectrum belonging to the selected pixel.
    See get_spectrum_range() to know the wavelength values for each index of
     the spectrum dimension
    return (None or DataArray with 1 dimension): the spectrum of the given
     pixel or None if no spectrum is selected.
    """
    def __init__(self, stream):

        super(SinglePointChronoProjection, self).__init__(stream)

        if hasattr(stream, "selected_pixel"):
            self.selected_pixel = stream.selected_pixel
            self.selected_pixel.subscribe(self._on_selected_pixel)

        if hasattr(stream, "selectionWidth"):
            self.selectionWidth = stream.selectionWidth
            self.selectionWidth.subscribe(self._on_selected_width)
            
        if hasattr(stream, "selected_wavelength"):
            self.selected_wavelength = stream.selected_wavelength
            self.selected_wavelength.subscribe(self._on_selected_wl)

        if hasattr(stream, "selected_time"):
            self.selected_time = stream.selected_time
            self.selected_time.subscribe(self._on_selected_time)

    def _on_selected_pixel(self, _):
        self._shouldUpdateImage()

    def _on_selected_width(self, _):
        self._shouldUpdateImage()

    def _on_selected_wl(self, _):
        self._shouldUpdateImage()

    def _on_selected_time(self, _):
        self._shouldUpdateImage()

    def _updateImage(self):
        """ Recomputes the image with all the raw data available
        """
        # logging.debug("Updating image")
        if self.selected_pixel.value == (None, None):
            return

        try:
            # if .raw is a list of DataArray, .image is a complete image
            if isinstance(self.stream.raw, list):
                x, y = self.selected_pixel.value
                c = self.stream._wl_px_values.index(self.selected_wavelength.value)
                chrono2d = self.stream._calibrated[c, :, 0, :, :]  # same data but remove useless dims

                # We treat width as the diameter of the circle which contains the center
                # of the pixels to be taken into account
                width = self.selectionWidth.value
                if width == 1:  # short-cut for simple case
                    raw = chrono2d[:, y, x]
                    self.image.value = raw
                    return

                # There are various ways to do it with numpy. As typically the spectrum
                # dimension is big, and the number of pixels to sum is small, it seems
                # the easiest way is to just do some kind of "clever" mean. Using a
                # masked array would also work, but that'd imply having a huge mask.
                radius = width / 2
                n = 0
                # TODO: use same cleverness as mean() for dtype?
                datasum = numpy.zeros(chrono2d.shape[0], dtype=numpy.float64)
                # Scan the square around the point, and only pick the points in the circle
                for px in range(max(0, int(x - radius)),
                                min(int(x + radius) + 1, chrono2d.shape[-1])):
                    for py in range(max(0, int(y - radius)),
                                    min(int(y + radius) + 1, chrono2d.shape[-2])):
                        if math.hypot(x - px, y - py) <= radius:
                            n += 1
                            datasum += chrono2d[:, py, px]

                mean = datasum / n
                raw = model.DataArray(mean.astype(chrono2d.dtype))

                self.image.value = raw

        except Exception:
            logging.exception("Updating %s image", self.__class__.__name__)


class LineSpectrumProjection(DataProjection):

    def __init__(self, stream):

        self.raw_display = False

        super(LineSpectrumProjection, self).__init__(stream)

        if hasattr(stream, "selected_time"):
            self.selected_time = stream.selected_time
            self.selected_time.subscribe(self._on_selected_time)

        if hasattr(stream, "selectionWidth"):
            self.selectionWidth = stream.selectionWidth
            self.selectionWidth.subscribe(self._on_selected_width)

        if hasattr(stream, "selected_line"):
            self.selected_line = stream.selected_line
            self.selected_line.subscribe(self._on_selected_line)

    def _on_selected_width(self, _):
        self._shouldUpdateImage()

    def _on_selected_line(self, _):
        self._shouldUpdateImage()

    def _on_selected_time(self, _):
        self._shouldUpdateImage()

    def _updateImage(self):
        """ Recomputes the image with all the raw data available
        """

        """ Return the 1D spectrum representing the (average) spectrum

        Call get_spectrum_range() to know the wavelength values for each index
          of the spectrum dimension.
        raw (bool): if True, will return the "raw" values (ie, same data type as
          the original data). Otherwise, it will return a RGB image.
        return (None or DataArray with 3 dimensions): first axis (Y) is spatial
          (along the line), second axis (X) is spectrum. If not raw, third axis
          is colour (RGB, but actually always greyscale). Note: when not raw,
          the beginning of the line (Y) is at the "bottom".
          MD_PIXEL_SIZE[1] contains the spatial distance between each spectrum
          If the selected_line is not valid, it will return None
        """

        if (None, None) in self.selected_line.value:
            return

        try:
            if model.hasVA(self.stream, "selected_time"):
                t = self.stream._tl_px_values.index(self.selected_time.value)
            else:
                t = 0

            spec2d = self.stream._calibrated[:, 0, t, :, :]  # same data but remove useless dims
            width = self.selectionWidth.value

            # Number of points to return: the length of the line
            start, end = self.selected_line.value
            v = (end[0] - start[0], end[1] - start[1])
            l = math.hypot(*v)
            n = 1 + int(l)
            if l < 1:  # a line of just one pixel is considered not valid
                return

            # FIXME: if the data has a width of 1 (ie, just a line), and the
            # requested width is an even number, the output is empty (because all
            # the interpolated points are outside of the data.

            # Coordinates of each point: ndim of data (5-2), pos on line (Y), spectrum (X)
            # The line is scanned from the end till the start so that the spectra
            # closest to the origin of the line are at the bottom.
            coord = numpy.empty((3, width, n, spec2d.shape[0]))
            coord[0] = numpy.arange(spec2d.shape[0])  # spectra = all
            coord_spc = coord.swapaxes(2, 3)  # just a view to have (line) space as last dim
            coord_spc[-1] = numpy.linspace(end[0], start[0], n)  # X axis
            coord_spc[-2] = numpy.linspace(end[1], start[1], n)  # Y axis

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
                spec1d = spec1d_w.mean(axis=0).astype(spec2d.dtype)
            assert spec1d.shape == (n, spec2d.shape[0])

            # Use metadata to indicate spatial distance between pixel
            pxs_data = self.stream._calibrated.metadata[MD_PIXEL_SIZE]
            pxs = math.hypot(v[0] * pxs_data[0], v[1] * pxs_data[1]) / (n - 1)
            md = self.stream._calibrated.metadata
            md[MD_PIXEL_SIZE] = (None, pxs)  # for the spectrum, use get_spectrum_range()

            if self.raw_display:
                raw = model.DataArray(spec1d[::-1, :], md)
            else:
                # Scale and convert to RGB image
                if self.stream.auto_bc.value:
                    hist, edges = img.histogram(spec1d)
                    irange = img.findOptimalRange(hist, edges,
                                                  self.stream.auto_bc_outliers.value / 100)
                else:
                    # use the values requested by the user
                    irange = sorted(self.stream.intensityRange.value)
                rgb8 = img.DataArray2RGB(spec1d, irange)

                raw = model.DataArray(rgb8, md)

            self.image.value = raw

        except Exception:
            logging.exception("Updating %s %s image", self.__class__.__name__, self.name.value)


class TemporalSpectrumProjection(RGBSpatialProjection):

    def __init__(self, stream):

        self.raw_display = False

        super(TemporalSpectrumProjection, self).__init__(stream)

        if hasattr(stream, "selected_time"):
            self.selected_time = stream.selected_time
            self.selected_time.subscribe(self._on_selected_time)

        if hasattr(stream, "selectionWidth"):
            self.selectionWidth = stream.selectionWidth
            self.selectionWidth.subscribe(self._on_selected_width)

        if hasattr(stream, "selected_pixel"):
            self.selected_pixel = stream.selected_pixel
            self.selected_pixel.subscribe(self._on_selected_pixel)

        if hasattr(stream, "selected_wavelength"):
            self.selected_wavelength = stream.selected_wavelength
            self.selected_wavelength.subscribe(self._on_selected_wl)

    def _find_metadata(self, md):
        return self.stream._calibrated.metadata

    def _on_selected_width(self, _):
        self._shouldUpdateImage()

    def _on_selected_pixel(self, _):
        self._shouldUpdateImage()

    def _on_selected_time(self, _):
        self._shouldUpdateImage()

    def _on_selected_wl(self, _):
        self._shouldUpdateImage()

    def _updateImage(self):
        """ Recomputes the image with all the raw data available
        """

        """
        returns a the temporal spectrum image for the given .selected_pixel
        """
        if self.selected_pixel.value == (None, None):
            return None

        try:
            x, y = self.selected_pixel.value

            spec2d = self.stream._calibrated[:, :, 0, :, :]  # same data but remove useless dims

            # md = self.stream._find_metadata(self.stream._calibrated.metadata)
            md = self.stream._calibrated.metadata
            # We treat width as the diameter of the circle which contains the center
            # of the pixels to be taken into account
            width = self.selectionWidth.value
            if width == 1:  # short-cut for simple case
                raw = model.DataArray(spec2d[:, :, y, x], md)
                self.image.value = self._projectXY2RGB(raw)
                return

            # There are various ways to do it with numpy. As typically the spectrum
            # dimension is big, and the number of pixels to sum is small, it seems
            # the easiest way is to just do some kind of "clever" mean. Using a
            # masked array would also work, but that'd imply having a huge mask.
            radius = width / 2
            n = 0
            # TODO: use same cleverness as mean() for dtype?
            datasum = numpy.zeros((spec2d.shape[0], spec2d.shape[1]), dtype=numpy.float64)
            # Scan the square around the point, and only pick the points in the circle
            for px in range(max(0, int(x - radius)),
                            min(int(x + radius) + 1, spec2d.shape[-1])):
                for py in range(max(0, int(y - radius)),
                                min(int(y + radius) + 1, spec2d.shape[-2])):
                    if math.hypot(x - px, y - py) <= radius:
                        n += 1
                        datasum += spec2d[:, :, py, px]

            mean = datasum / n
            raw = model.DataArray(mean.astype(spec2d.dtype), md)
            self.image.value = self._projectXY2RGB(raw)

        except Exception:
            logging.exception("Updating %s %s image", self.__class__.__name__, self.name.value)

class RGBSpatialSpectrumProjection(RGBSpatialProjection):

    def __init__(self, stream):

        self.raw_display = False

        super(RGBSpatialSpectrumProjection, self).__init__(stream)

        if hasattr(stream, "selected_pixel"):
            self.selected_pixel = stream.selected_pixel
            self.selected_pixel.subscribe(self._on_selected_pixel)

        if hasattr(stream, "selected_time"):
            self.selected_time = stream.selected_time
            self.selected_time.subscribe(self._on_selected_time)

    def _on_selected_time(self, _):
        self._shouldUpdateImage()

    def _on_selected_pixel(self, _):
        self._shouldUpdateImage()

    def _updateImage(self):
        """ Recomputes the image with all the raw data available
        """

        """
        Project a spectrum cube (CTYX) to XY space in RGB, by averaging the
          intensity over all the wavelengths (selected by the user)
        data (DataArray or None): if provided, will use the cube, otherwise,
          will use the whole data from the stream.
        raw (bool): if True, will return the "raw" values (ie, same data type as
          the original data). Otherwise, it will return a RGB image.
        return (DataArray YXC of uint8 or YX of same data type as data): average
          intensity over the selected wavelengths
        """
        
        try:
            data = self.stream._calibrated
            md = self.stream._calibrated.metadata
    
            # pick only the data inside the bandwidth
            spec_range = self.stream._get_bandwidth_in_pixel()
            if model.hasVA(self.stream, "selected_time"):
                t = self.stream._tl_px_values.index(self.selected_time.value)
            else:
                t = 0
            logging.debug("Spectrum range picked: %s px", spec_range)
    
            if self.raw_display:
                av_data = numpy.mean(data[spec_range[0]:spec_range[1] + 1, t], axis=0)
                av_data = img.ensure2DImage(av_data).astype(data.dtype)
                raw = model.DataArray(av_data, md)

                self.image.value = self._projectXY2RGB(raw)

            else:
                irange = self.stream._getDisplayIRange()  # will update histogram if not yet present
    
                if not self.stream.fitToRGB.value:
                    # TODO: use better intermediary type if possible?, cf semcomedi
                    av_data = numpy.mean(data[spec_range[0]:spec_range[1] + 1, t], axis=0)
                    av_data = img.ensure2DImage(av_data)
                    rgbim = img.DataArray2RGB(av_data, irange)

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
                    av_data = numpy.mean(data[rrange[0]:rrange[1] + 1, t], axis=0)
                    av_data = img.ensure2DImage(av_data)
                    rgbim = img.DataArray2RGB(av_data, irange)
                    av_data = numpy.mean(data[grange[0]:grange[1] + 1, t], axis=0)
                    av_data = img.ensure2DImage(av_data)
                    gim = img.DataArray2RGB(av_data, irange)
                    rgbim[:, :, 1] = gim[:, :, 0]
                    av_data = numpy.mean(data[brange[0]:brange[1] + 1, t], axis=0)
                    av_data = img.ensure2DImage(av_data)
                    bim = img.DataArray2RGB(av_data, irange)
                    rgbim[:, :, 2] = bim[:, :, 0]
    
                rgbim.flags.writeable = False
                md[model.MD_DIMS] = "YXC"  # RGB format
    
                raw = model.DataArray(rgbim, md)

                self.image.value = raw

        except Exception:
            logging.exception("Updating %s %s image", self.__class__.__name__, self.name.value)
