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
# TODO: test with just one tile

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

    def addTile(self,tile):
        """
        tile (2D DataArray): the image must have at least MD_POS and
        MD_PIXEL_SIZE metadata. All provided tiles should have the same dtype.
        """
        
        self.tiles.append(tile)
    
       
    def getFullImage(self):
        """
        return (2D DataArray): same dtype as the tiles, with shape corresponding to the bounding box. 
        """
             
        # Sort the tiles by time, to avoid random order in "Z", and high-light the
        # acquisition order.
        tiles = sorted(self.tiles, key=lambda t: t.metadata.get(model.MD_ACQ_DATE, 0))
       
        # Merge the correction metadata inside each image (to keep the rest of the
        # code simple)

        tiles = [model.DataArray(t, t.metadata.copy()) for t in tiles]
        
        for t in tiles:
            img.mergeMetadata(t.metadata)
    
        # Compute the bounding box of each tile and the global bounding box
    
        # Get a fixed pixel size by using the first one
        # TODO: use the mean, in case they are all slightly different due to correction?
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

        glt = gbbx_phy[0], gbbx_phy[3]  # that's the origin (Y is max as Y is inverted)
        for bp, t in zip(tbbx_phy, tiles):
            lt = (int((bp[0] - glt[0]) / pxs[0]), int(-(bp[3] - glt[1]) / pxs[1]))
            w = t.shape[-1], t.shape[-2]
            bbx = (lt[0], lt[1],
                   lt[0] + w[0], lt[1] + w[1])
            tbbx_px.append(bbx)
            
        gbbx_px = (min(b[0] for b in tbbx_px), min(b[1] for b in tbbx_px),
                   max(b[2] for b in tbbx_px), max(b[3] for b in tbbx_px))
    
        assert gbbx_px[0] == gbbx_px[1] == 0

        # TODO: warn if the global area is much bigger than the sum of the tile area
    
        # Paste each tile
        logging.debug("Generating global image of size %dx%d px", gbbx_px[-2], gbbx_px[-1])
        im = numpy.empty((gbbx_px[-1], gbbx_px[-2]), dtype=tiles[0].dtype)
        # Use minimum of the values in the tiles for background
        im[:] = numpy.amin(tiles)
        for b, t in zip(tbbx_px, tiles):
            im[b[1]:b[1] + t.shape[0], b[0]:b[0] + t.shape[1]] = t
            # TODO: border
    
        # Update metadata
        # TODO: check this is also correct based on lt + half shape * pxs
        c_phy = ((gbbx_phy[0] + gbbx_phy[2]) / 2, (gbbx_phy[1] + gbbx_phy[3]) / 2)
        md = tiles[0].metadata.copy()
        md[model.MD_POS] = c_phy
    
        return model.DataArray(im, md)
    
       
    
class MeanWeaver(object):
    """
    Pixels of the final image which are corresponding to several tiles are computed as an 
    average of the pixel of each tile.
    """
    
    def __init__(self):
        self.tiles = []
    
    def addTile(self,tile):
        self.tiles.append(tile)
    
    
    def getFullImage(self):
        """
        return (2D DataArray): same dtype as the tiles, with shape corresponding to the bounding box. 
        """
             
        # Sort the tiles by time, to avoid random order in "Z", and high-light the
        # acquisition order.
        tiles = sorted(self.tiles, key=lambda t: t.metadata.get(model.MD_ACQ_DATE, 0))
       
        # Merge the correction metadata inside each image (to keep the rest of the
        # code simple)

        tiles = [model.DataArray(t, t.metadata.copy()) for t in tiles]
        
        for t in tiles:
            img.mergeMetadata(t.metadata)
    
        # Compute the bounding box of each tile and the global bounding box
    
        # Get a fixed pixel size by using the first one
        # TODO: use the mean, in case they are all slightly different due to correction?
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

        glt = gbbx_phy[0], gbbx_phy[3]  # that's the origin (Y is max as Y is inverted)
        for bp, t in zip(tbbx_phy, tiles):
            lt = (int((bp[0] - glt[0]) / pxs[0]), int(-(bp[3] - glt[1]) / pxs[1]))
            w = t.shape[-1], t.shape[-2]
            bbx = (lt[0], lt[1],
                   lt[0] + w[0], lt[1] + w[1])
            tbbx_px.append(bbx)
   
        gbbx_px = (min(b[0] for b in tbbx_px), min(b[1] for b in tbbx_px),
                   max(b[2] for b in tbbx_px), max(b[3] for b in tbbx_px))
    
        assert gbbx_px[0] == gbbx_px[1] == 0

        # TODO: warn if the global area is much bigger than the sum of the tile area
    
        # Paste each tile
        logging.debug("Generating global image of size %dx%d px", gbbx_px[-2], gbbx_px[-1])
        im = numpy.empty((gbbx_px[-1], gbbx_px[-2]), dtype=tiles[0].dtype)
        # TODO: what value to use for background? Use minimum of the values in the tiles
        im[:] = numpy.amin(tiles)
       
        if len(tiles) > 1:
            # Overlap
            diff = max(tiles[1].metadata[model.MD_POS][0]-tiles[0].metadata[model.MD_POS][0],
                       tiles[1].metadata[model.MD_POS][1]-tiles[0].metadata[model.MD_POS][1])
            sz_m = 2*tiles[0].metadata[model.MD_POS][0]
            ovrlp = (sz_m-diff)/sz_m
            
            prev_t = None
            prev_b = None
            prev_im = None
            
            for b, t in zip(tbbx_px, tiles):
                im[b[1]:b[1] + t.shape[0], b[0]:b[0] + t.shape[1]] = t
                
                # Take mean of overlap region from previous and current stitched image
                if prev_im != None:
                    if t.metadata[model.MD_POS][0] > prev_t.metadata[model.MD_POS][0]:
                        # horizontal
                        prev_cropped = prev_im[prev_b[1]:prev_b[1] + t.shape[0], prev_b[0]+(1-ovrlp)*t.shape[0]:prev_b[0] + t.shape[1]]
                        im_cropped = im[prev_b[1]:prev_b[1] + t.shape[0], prev_b[0]+(1-ovrlp)*t.shape[0]:prev_b[0] + t.shape[1]]                    
                        
                        im[prev_b[1]:prev_b[1] + t.shape[0], prev_b[0]+(1-ovrlp)*t.shape[0]:prev_b[0] + t.shape[1]] =\
                           numpy.mean([prev_cropped,im_cropped],axis=0)
                           
                    else:
                        prev_cropped = prev_im[prev_b[1]+(1-ovrlp)*t.shape[0]:prev_b[1] + t.shape[0], prev_b[0]:prev_b[0] + t.shape[1]]
                        im_cropped = im[prev_b[1]+(1-ovrlp)*t.shape[0]:prev_b[1] + t.shape[0], prev_b[0]:prev_b[0] + t.shape[1]]
    
                        
                        im[prev_b[1]+(1-ovrlp)*t.shape[0]:prev_b[1] + t.shape[0], prev_b[0]:prev_b[0] + t.shape[1]] =\
                           numpy.mean([prev_cropped,im_cropped],axis=0)              
                
                
                prev_im = im
                prev_b = b
                prev_t = t
                
        else:
            im = tiles[0]
            

        # Update metadata
        # TODO: check this is also correct based on lt + half shape * pxs
        c_phy = ((gbbx_phy[0] + gbbx_phy[2]) / 2, (gbbx_phy[1] + gbbx_phy[3]) / 2)
        md = tiles[0].metadata.copy()
        md[model.MD_POS] = c_phy
    
        return model.DataArray(im, md)
    
    
    
    
