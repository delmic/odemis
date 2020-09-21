# -*- coding: utf-8 -*-
'''
Created on 19 Jul 2017

@author: Éric Piel

Copyright © 2017 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

import logging
import numpy
from odemis import model, util
from odemis.util import img


# This is a series of classes which use different methods to generate a large
# image out of "tile" images.
# TODO: version with a gradient, with pixels where multiple data is available
# are composed based on a weight dependent on how far the pixel is from the center.
# TODO: take into account skew and rotation to compute position, or even to
# directly copy the image already transformed.
# TODO: handle higher dimensions by just copying them as-is

class CollageWeaver(object):
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

    def __init__(self):
        self.tiles = []

    def addTile(self, tile):
        """
        tile (2D DataArray): the image must have at least MD_POS and
        MD_PIXEL_SIZE metadata. All provided tiles should have the same dtype.
        """
        # Merge the correction metadata inside each image (to keep the rest of the
        # code simple)
        tile = model.DataArray(tile, tile.metadata.copy())
        img.mergeMetadata(tile.metadata)
        self.tiles.append(tile)

    def getFullImage(self):
        """
        return (2D DataArray): same dtype as the tiles, with shape corresponding to the bounding box. 
        """

        tiles = self.tiles

        # Compute the bounding box of each tile and the global bounding box

        # Get a fixed pixel size by using the first one
        # TODO: use the mean, in case they are all slightly different due to
        # correction?
        pxs = tiles[0].metadata[model.MD_PIXEL_SIZE]

        tbbx_phy = []  # tuples of ltrb in physical coordinates
        for t in tiles:
            c = t.metadata[model.MD_POS]
            w = t.shape[-1], t.shape[-2]
            if not util.almost_equal(pxs[0], t.metadata[model.MD_PIXEL_SIZE][0], rtol=0.01):
                logging.warning("Tile @ %s has a unexpected pixel size (%g vs %g)",
                                c, t.metadata[model.MD_PIXEL_SIZE][0], pxs[0])
            bbx = (c[0] - (w[0] * pxs[0] / 2), c[1] - (w[1] * pxs[1] / 2),
                   c[0] + (w[0] * pxs[0] / 2), c[1] + (w[1] * pxs[1] / 2))

            tbbx_phy.append(bbx)

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

        # Paste each tile
        logging.debug("Generating global image of size %dx%d px",
                      gbbx_px[-2], gbbx_px[-1])
        im = numpy.empty((gbbx_px[-1], gbbx_px[-2]), dtype=tiles[0].dtype)
        # Use minimum of the values in the tiles for background
        im[:] = numpy.amin(tiles)
        for b, t in zip(tbbx_px, tiles):
            im[b[1]:b[1] + t.shape[0], b[0]:b[0] + t.shape[1]] = t
            # TODO: border

        # Update metadata
        # TODO: check this is also correct based on lt + half shape * pxs
        c_phy = ((gbbx_phy[0] + gbbx_phy[2]) / 2,
                 (gbbx_phy[1] + gbbx_phy[3]) / 2)
        md = tiles[0].metadata.copy()
        md[model.MD_POS] = c_phy
        md[model.MD_DIMS] = "YX"
        return model.DataArray(im, md)


class CollageWeaverReverse(object):
    """
    Similar to CollageWeaver, but only fills parts of the global image with the new tile that
    are still empty. This is desirable if the quality of the overlap regions is much better the first
    time a region is imaged due to bleaching effects. The result is equivalent to a collage that starts 
    with the last tile and pastes the older tiles in reverse order of acquisition.
    """

    def __init__(self):
        self.tiles = []

    def addTile(self, tile):
        # Merge the correction metadata inside each image (to keep the rest of the
        # code simple)
        tile = model.DataArray(tile, tile.metadata.copy())
        img.mergeMetadata(tile.metadata)
        self.tiles.append(tile)

    def getFullImage(self):
        """
        return (2D DataArray): same dtype as the tiles, with shape corresponding to the bounding box. 
        """
        tiles = self.tiles

        # Compute the bounding box of each tile and the global bounding box

        # Get a fixed pixel size by using the first one
        # TODO: use the mean, in case they are all slightly different due to
        # correction?
        pxs = tiles[0].metadata[model.MD_PIXEL_SIZE]

        tbbx_phy = []  # tuples of ltrb in physical coordinates
        for t in tiles:
            c = t.metadata[model.MD_POS]
            w = t.shape[-1], t.shape[-2]
            if not util.almost_equal(pxs[0], t.metadata[model.MD_PIXEL_SIZE][0], rtol=0.01):
                logging.warning("Tile @ %s has a unexpected pixel size (%g vs %g)",
                                c, t.metadata[model.MD_PIXEL_SIZE][0], pxs[0])
            bbx = (c[0] - (w[0] * pxs[0] / 2), c[1] - (w[1] * pxs[1] / 2),
                   c[0] + (w[0] * pxs[0] / 2), c[1] + (w[1] * pxs[1] / 2))

            tbbx_phy.append(bbx)

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

        # Paste each tile
        logging.debug("Generating global image of size %dx%d px",
                      gbbx_px[-2], gbbx_px[-1])
        im = numpy.empty((gbbx_px[-1], gbbx_px[-2]), dtype=tiles[0].dtype)
        # Use minimum of the values in the tiles for background
        im[:] = numpy.amin(tiles)

        # The mask is multiplied with the tile, thereby creating a tile with a gradient
        mask = numpy.zeros((gbbx_px[-1], gbbx_px[-2]), dtype=numpy.bool)

        for b, t in zip(tbbx_px, tiles):
            # Part of image overlapping with tile
            roi = im[b[1]:b[1] + t.shape[0], b[0]:b[0] + t.shape[1]]
            moi = mask[b[1]:b[1] + t.shape[0], b[0]:b[0] + t.shape[1]]

            # Insert image at positions that are still empty
            roi[~moi] = t[~moi]

            # Update mask
            mask[b[1]:b[1] + t.shape[0], b[0]:b[0] + t.shape[1]] = True

        # Update metadata
        # TODO: check this is also correct based on lt + half shape * pxs
        c_phy = ((gbbx_phy[0] + gbbx_phy[2]) / 2,
                 (gbbx_phy[1] + gbbx_phy[3]) / 2)
        md = tiles[0].metadata.copy()
        md[model.MD_POS] = c_phy
        md[model.MD_DIMS] = "YX"
        return model.DataArray(im, md)


class MeanWeaver(object):
    """
    Pixels of the final image which are corresponding to several tiles are computed as an 
    average of the pixel of each tile.
    """

    def __init__(self):
        self.tiles = []

    def addTile(self, tile):
        # Merge the correction metadata inside each image (to keep the rest of the
        # code simple)
        tile = model.DataArray(tile, tile.metadata.copy())
        img.mergeMetadata(tile.metadata)
        self.tiles.append(tile)

    def getFullImage(self):
        """
        return (2D DataArray): same dtype as the tiles, with shape corresponding to the bounding box. 
        """
        tiles = self.tiles

        # Compute the bounding box of each tile and the global bounding box

        # Get a fixed pixel size by using the first one
        # TODO: use the mean, in case they are all slightly different due to
        # correction?
        pxs = tiles[0].metadata[model.MD_PIXEL_SIZE]

        tbbx_phy = []  # tuples of ltrb in physical coordinates
        for t in tiles:
            c = t.metadata[model.MD_POS]
            w = t.shape[-1], t.shape[-2]
            if not util.almost_equal(pxs[0], t.metadata[model.MD_PIXEL_SIZE][0], rtol=0.01):
                logging.warning("Tile @ %s has a unexpected pixel size (%g vs %g)",
                                c, t.metadata[model.MD_PIXEL_SIZE][0], pxs[0])
            bbx = (c[0] - (w[0] * pxs[0] / 2), c[1] - (w[1] * pxs[1] / 2),
                   c[0] + (w[0] * pxs[0] / 2), c[1] + (w[1] * pxs[1] / 2))

            tbbx_phy.append(bbx)

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

        # Weave tiles by using a smooth gradient. The part of the tile that does not overlap
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
                      gbbx_px[-2], gbbx_px[-1])
        im = numpy.empty((gbbx_px[-1], gbbx_px[-2]), dtype=tiles[0].dtype)
        # Use minimum of the values in the tiles for background
        im[:] = numpy.amin(tiles)

        # The mask is multiplied with the tile, thereby creating a tile with a gradient
        mask = numpy.zeros((gbbx_px[-1], gbbx_px[-2]), dtype=numpy.bool)

        for b, t in zip(tbbx_px, tiles):
            # Part of image overlapping with tile
            roi = im[b[1]:b[1] + t.shape[0], b[0]:b[0] + t.shape[1]]
            moi = mask[b[1]:b[1] + t.shape[0], b[0]:b[0] + t.shape[1]]

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
            # select the this region.

            # Use weights to create gradient in overlapping region
            roi[moi] = (t * (1 - w))[moi] + (roi * w)[moi]

            # Update mask
            mask[b[1]:b[1] + t.shape[0], b[0]:b[0] + t.shape[1]] = True

        # Update metadata
        # TODO: check this is also correct based on lt + half shape * pxs
        c_phy = ((gbbx_phy[0] + gbbx_phy[2]) / 2,
                 (gbbx_phy[1] + gbbx_phy[3]) / 2)
        md = tiles[0].metadata.copy()
        md[model.MD_POS] = c_phy
        md[model.MD_DIMS] = "YX"
        return model.DataArray(im, md)
