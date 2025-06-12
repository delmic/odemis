# -*- coding: utf-8 -*-
"""
Created on 19 Jul 2017

@author: Éric Piel, Thera Pals

Copyright © 2017-2022 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
"""

from abc import ABCMeta
import logging
import copy
import numpy
from abc import abstractmethod
from odemis import model, util
from odemis.util import img


# This is a series of classes which use different methods to generate a large
# image out of "tile" images.
# TODO: version with a gradient, with pixels where multiple data is available
# are composed based on a weight dependent on how far the pixel is from the center.
# TODO: take into account skew and rotation to compute position, or even to
# directly copy the image already transformed.
# TODO: handle higher dimensions by just copying them as-is

class Weaver(metaclass=ABCMeta):
    """
    Abstract class representing a weaver.
    A weaver assembles a set of small images with MD_POS metadata (tiles) into one large image.
    """

    def __init__(self, adjust_brightness=False):
        """
        adjust_brightness (bool): True if brightness correction should be applied (useful in case of
        tiles with strong bleaching/depletion effects)
        """
        self.tiles = []
        self.adjust_brt = adjust_brightness
        self.tbbx_px = None  # the bounding boxes of each tile in pixel coordinates
        self.gbbx_px = None  # the global bounding box of the weaved image in pixel coordinates
        self.gbbx_phy = None  # the global bounding box of the weaved image in physical coordinates
        self.stage_bare_pos = None # the stage-bare position of the weaved image

    def addTile(self, tile):
        """
        Adds one tile to the weaver.
        tile (2D DataArray): the image must have at least MD_POS and
        MD_PIXEL_SIZE metadata. All provided tiles should have the same dtype.
        """
        # Merge the correction metadata inside each image (to keep the rest of the
        # code simple)
        if isinstance(tile, model.DataArrayShadow):
            raise TypeError(f"Tile must be a loaded DataArray, not a DataArrayShadow. To load a DataArrayShadow, call: tile = tile.getData()")
        if not isinstance(tile, model.DataArray):
            raise TypeError(f"Tile must be a DataArray, not {type(tile)}")
        tile = model.DataArray(tile, tile.metadata.copy())
        img.mergeMetadata(tile.metadata)
        self.tiles.append(tile)

    def getFullImage(self):
        """
        Assembles the tiles into a large image.
        return (2D DataArray): same dtype as the tiles, with shape corresponding to the bounding box of the tiles.
        """
        # WARNING on rotation:
        # The total image rotation is the sum of the "standard" rotation,
        # relative to the sample coordinates, and the scan rotation.
        # The scan rotation is not used when displaying the images, because it causes images to be displayed 'upside down' from
        # how the user regularly sees the images.
        # However, when stitching SEM images, typically that is applied without changing the sample coordinates,
        # so usually the stitched image needs to take that rotation into account to be correct but it interferes with SEM live overview acquisition.
        # To properly handle this, create a plugin which rotates the images by the scan rotation for importing SEM images
        rotation = self.tiles[0].metadata.get(model.MD_ROTATION, 0)  # + self.tiles[0].metadata.get(model.MD_BEAM_SCAN_ROTATION, 0)
        center_of_rot = self.tiles[0].metadata[model.MD_POS]

        tiles = []
        # Rotate all tiles by the inverse of the rotation, such that each tile is aligned with the horizontal axis.
        for tile in self.tiles:
            tiles.append(img.rotate_img_metadata(tile, -rotation, center_of_rot))
        self.tiles = tiles

        self.tbbx_px, self.gbbx_px, self.gbbx_phy, self.stage_bare_pos = self.get_bounding_boxes(self.tiles)
        im = self.weave_tiles()
        md = self.get_final_metadata(self.tiles[0].metadata.copy())
        weaved_image = img.rotate_img_metadata(model.DataArray(im, md), rotation, center_of_rot)

        return weaved_image

    @abstractmethod
    def weave_tiles(self):
        """
        Weave the tiles into a single image.
        return (2D DataArray): The weaved image.
        """
        pass

    @staticmethod
    def get_bounding_boxes(tiles: list):
        """
        Compute the bounding box of each tile in pixel coordinates,
        and the global bounding box of all tiles in physical and pixel coordinates.

        :param tiles: list of all tiles (DataArrays).

        :return tbbx_px: (list of tuples) the ltrb bounding boxes of each tile in pixel coordinates
        :return gbbx_px: (list of tuples) the global ltrb bounding box of the weaved image in pixel coordinates
        :return gbbx_phy: (list of tuples) the global ltrb bounding box of the weaved image in physical coordinates
        :return mean_stage_bare_pos: Dict[str, float] the mean stage-bare position of the weaved image, typically 5D
        """

        # Get a fixed pixel size by using the first one
        # TODO: use the mean, in case they are all slightly different due to
        # correction?
        pxs = tiles[0].metadata[model.MD_PIXEL_SIZE]

        tbbx_phy = []  # tuples of ltrb in physical coordinates
        stage_bare_coords = []  # dict of stage-bare coordinates
        for t in tiles:
            c = t.metadata[model.MD_POS]
            w = t.shape[-1], t.shape[-2]
            if not util.almost_equal(pxs[0], t.metadata[model.MD_PIXEL_SIZE][0], rtol=0.01):
                logging.warning("Tile @ %s has a unexpected pixel size (%g vs %g)",
                                c, t.metadata[model.MD_PIXEL_SIZE][0], pxs[0])
            bbx = (c[0] - (w[0] * pxs[0] / 2), c[1] - (w[1] * pxs[1] / 2),
                   c[0] + (w[0] * pxs[0] / 2), c[1] + (w[1] * pxs[1] / 2))

            tbbx_phy.append(bbx)

            # add the stage-bare position
            sbc = t.metadata.get(model.MD_STAGE_POSITION_RAW, None)
            if sbc is not None:
                stage_bare_coords.append(copy.deepcopy(sbc))

        # get the mean of stage-bare coords for each axis
        mean_stage_bare_pos = None
        if stage_bare_coords:
            axes = stage_bare_coords[0].keys()
            mean_stage_bare_pos = {k: numpy.mean([r[k] for r in stage_bare_coords]) for k in axes}

        gbbx_phy = (min(b[0] for b in tbbx_phy), min(b[1] for b in tbbx_phy),
                    max(b[2] for b in tbbx_phy), max(b[3] for b in tbbx_phy))

        # Compute the bounding-boxes in pixel coordinates
        tbbx_px = []

        # that's the origin (Y is max as Y is inverted)
        glt = gbbx_phy[0], gbbx_phy[3]
        for bp, t in zip(tbbx_phy, tiles):
            lt = (int(round((bp[0] - glt[0]) / pxs[0])),
                  int(round(-(bp[3] - glt[1]) / pxs[1])))
            w = t.shape[-1], t.shape[-2]
            bbx = (lt[0], lt[1],
                   lt[0] + w[0], lt[1] + w[1])
            tbbx_px.append(bbx)

        gbbx_px = (min(b[0] for b in tbbx_px), min(b[1] for b in tbbx_px),
                   max(b[2] for b in tbbx_px), max(b[3] for b in tbbx_px))

        assert gbbx_px[0] == gbbx_px[1] == 0
        if numpy.greater(gbbx_px[-2:], 4 * numpy.sum(tbbx_px[-2:])).any():
            # Overlap > 50% or missing tiles
            logging.warning("Global area much bigger than sum of tile areas")
        return tbbx_px, gbbx_px, gbbx_phy, mean_stage_bare_pos

    def get_final_metadata(self, md: dict) -> dict:
        """
        :param md: The metadata which needs to be updated with the final position and dimension.

        Return the metadata of the final output image to have the correct position and dimensions.
        """
        if self.gbbx_phy is None:
            raise ValueError("Image needs to be weaved before getting final metadata.")

        # TODO: check this is also correct based on lt + half shape * pxs
        c_phy = ((self.gbbx_phy[0] + self.gbbx_phy[2]) / 2,
                 (self.gbbx_phy[1] + self.gbbx_phy[3]) / 2)

        md[model.MD_POS] = c_phy
        md[model.MD_DIMS] = "YX"

        # add stage bare position to metadata if available
        if self.stage_bare_pos is not None:
            md[model.MD_STAGE_POSITION_RAW] = self.stage_bare_pos
            try: # might not be present, but required for meteor
                md[model.MD_EXTRA_SETTINGS]["Stage"]["position"][0] = self.stage_bare_pos
            except KeyError:
                pass
        return md

    def _adjust_brightness(self, tile, tiles):
        """
        Adjusts the brightness of a tile, so its mean corresponds to the mean of a list of tiles.
        :param tile (DataArray): tile to adjust
        :param tiles (2D DataArray): input tiles
        :returns (2D DataArray): tiles with adjusted brightness
        """
        # This is a very simple algorithm. In reality, not every tile should have the same brightness. A better
        # way to handle it would be to do local brightness adjustments, e.g. by comparing the overlapping
        # regions.
        # In general, even this simple calculation helps to improve the quality of the overall image
        # if there are a lot of bleaching/deposition effects, which cause a small number of tiles to have
        # a very different (typically higher) brightness than the others.
        tile = copy.deepcopy(tile)  # don't change the input tile
        im_brt = numpy.mean(tiles)
        tile_brt = numpy.mean(tile)
        diff = im_brt - tile_brt
        # To avoid overflows, we need to clip the results to the dtype range.
        if numpy.issubdtype(tile.dtype, numpy.integer):
            maxval = numpy.iinfo(tile.dtype).max
        elif numpy.issubdtype(tile.dtype, float):
            maxval = numpy.finfo(tile.dtype).max
        else:
            maxval = numpy.inf
        tile = tile + numpy.minimum(maxval - tile, diff)  # clip to maxval
        return tile


class CollageWeaver(Weaver):
    """
    Very straight-forward version, which just paste the images where their center
    position is. It expects that the pixel size for all the images are identical.
    It doesn't take into account the rotation and skew metadata.
    tiles (iterable of 2D DataArray): each image must have at least MD_POS and
      MD_PIXEL_SIZE metadata. They should all have the same dtype.
    border (None or value): if there is a value, it's used around each image, to
     highlight the position
    return (2D DataArray): same dtype as the tiles, with shape corresponding to
      the bounding box.
    """

    def weave_tiles(self):
        """
        Weave tiles by pasting the tiles where their center position is.
        return (2D DataArray): The weaved image.
        """
        # Paste each tile
        logging.debug("Generating global image of size %dx%d px",
                      self.gbbx_px[-2], self.gbbx_px[-1])

        # Create a background of the image using the minimum value of self.tiles
        im = numpy.ones((self.gbbx_px[-1], self.gbbx_px[-2]), dtype=self.tiles[0].dtype) * numpy.amin(self.tiles)

        for b, t in zip(self.tbbx_px, self.tiles):
            if self.adjust_brt:
                t = self._adjust_brightness(t, self.tiles)
            im[b[1]:b[1] + t.shape[0], b[0]:b[0] + t.shape[1]] = t
            # TODO: border
        return im


class CollageWeaverReverse(Weaver):
    """
    Similar to CollageWeaver, but only fills parts of the global image with the new tile that
    are still empty. This is desirable if the quality of the overlap regions is much better the first
    time a region is imaged due to bleaching effects. The result is equivalent to a collage that starts
    with the last tile and pastes the older tiles in reverse order of acquisition.
    """

    def weave_tiles(self):
        """
        Weave tiles by filling parts of the global image that are still empty with the new tile.
        return (2D DataArray): The weaved image.
        """
        # Paste each tile
        logging.debug("Generating global image of size %dx%d px",
                      self.gbbx_px[-2], self.gbbx_px[-1])
        # Create a background of the image using the minimum value of self.tiles
        im = numpy.ones((self.gbbx_px[-1], self.gbbx_px[-2]), dtype=self.tiles[0].dtype) * numpy.amin(self.tiles)

        # The mask is multiplied with the tile, thereby creating a tile with a gradient
        mask = numpy.zeros((self.gbbx_px[-1], self.gbbx_px[-2]), dtype=bool)

        for b, t in zip(self.tbbx_px, self.tiles):
            # Part of image overlapping with tile
            roi = im[b[1]:b[1] + t.shape[0], b[0]:b[0] + t.shape[1]]
            moi = mask[b[1]:b[1] + t.shape[0], b[0]:b[0] + t.shape[1]]

            if self.adjust_brt:
                t = self._adjust_brightness(t, self.tiles)

            # Insert image at positions that are still empty
            roi[~moi] = t[~moi]

            # Update mask
            mask[b[1]:b[1] + t.shape[0], b[0]:b[0] + t.shape[1]] = True
        return im


class MeanWeaver(Weaver):
    """
    Pixels of the final image which are corresponding to several tiles are computed as an
    average of the pixel of each tile.
    """

    def weave_tiles(self):
        """
        Weave tiles by using a smooth gradient.
        return (2D DataArray): The weaved image.
        """
        #  The part of the tile that does not overlap
        # with any previous tiles is inserted into the part of the
        # ovv image that is still empty. This part is determined by a mask, which indicates
        # the parts of the image that already contain image data (True) and the ones that are still
        # empty (False). For the overlapping parts, the tile is multiplied with weights corresponding
        # to a gradient that has its maximum at the center of the tile and
        # smoothly decreases toward the edges. The function for creating the weights is
        # a distance measure resembling the maximum-norm, i.e. equidistant points lie
        # on a rectangle (instead of a circle like for the euclidean norm). Additionally,
        # the x and y values generating this norm are raised to the power of 6 to
        # create a steeper gradient. The value 6 is quite arbitrary and was found to give
        # good results during experimentation.
        # The part of the overview image that overlaps with the new tile is multiplied with the
        # complementary weights (1 -  weights) and the weighted overlapping parts of the new tile and
        # the ovv image are added, so the resulting image contains a gradient in the overlapping regions
        # between all the tiles that have been inserted before and the newly inserted tile.

        # Paste each tile
        logging.debug("Generating global image of size %dx%d px",
                      self.gbbx_px[-2], self.gbbx_px[-1])
        # Create a background of the image using the minimum value of self.tiles
        im = numpy.ones((self.gbbx_px[-1], self.gbbx_px[-2]), dtype=self.tiles[0].dtype) * numpy.amin(self.tiles)

        # The mask is multiplied with the tile, thereby creating a tile with a gradient
        mask = numpy.zeros((self.gbbx_px[-1], self.gbbx_px[-2]), dtype=bool)

        for b, t in zip(self.tbbx_px, self.tiles):
            # Part of image overlapping with tile
            roi = im[b[1]:b[1] + t.shape[0], b[0]:b[0] + t.shape[1]]
            moi = mask[b[1]:b[1] + t.shape[0], b[0]:b[0] + t.shape[1]]

            if self.adjust_brt:
                self._adjust_brightness(t, self.tiles)
            # Insert image at positions that are still empty
            roi[~moi] = t[~moi]

            # Create gradient in overlapping region. Ratio between old image and new tile values determined by
            # distance to the center of the tile

            # Create weight matrix with decreasing values from its center that
            # has the same size as the tile.
            sz = numpy.array(roi.shape)
            hh, hw = sz / 2  # half-height, half-width
            x = numpy.linspace(-hw, hw, sz[1])
            y = numpy.linspace(-hh, hh, sz[0])
            xx, yy = numpy.meshgrid((x / hw) ** 6, (y / hh) ** 6)
            w = numpy.maximum(xx, yy)
            # Hardcoding a weight function is quite arbitrary and might result in
            # suboptimal solutions in some cases.
            # Alternatively, different weights might be used. One option would be to select
            # a fixed region on the sides of the image, e.g. 20% (expected overlap), and
            # only apply a (linear) gradient to these parts, while keeping the new tile for the
            # rest of the region. However, this approach does not solve the hardcoding problem
            # since the overlap region is still arbitrary. Future solutions might adaptively
            # select this region.

            # Use weights to create gradient in overlapping region
            roi[moi] = (t * (1 - w))[moi] + (roi * w)[moi]

            # Update mask
            mask[b[1]:b[1] + t.shape[0], b[0]:b[0] + t.shape[1]] = True
        return im
