# -*- coding: utf-8 -*-
'''
Created on 26 Jul 2017

@author: Éric Piel

Copyright © 2017 Éric Piel, Delmic

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
import logging
import numpy
from odemis import model
import time
import unittest
from PIL import Image

from odemis.acq.stitching._weaver import CollageWeaver, MeanWeaver 
from odemis.acq.stitching import weave


logging.getLogger().setLevel(logging.DEBUG)



# @unittest.skip("skip")
class TestCollageWeaver(unittest.TestCase):

    # @unittest.skip("skip")
    def test_one_tile(self):
        """
        Test that when there is only one tile, it's returned as-is
        """
        img12 = numpy.zeros((2048, 1937), dtype=numpy.uint16) + 4000
        md = {
            model.MD_SW_VERSION: "1.0-test",
            model.MD_DESCRIPTION: u"test",  # tiff doesn't support É (but XML does)
            model.MD_ACQ_DATE: time.time(),
            model.MD_BPP: 12,
            model.MD_BINNING: (1, 2),  # px, px
            model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
            model.MD_POS: (1e-3, -30e-3),  # m
            model.MD_EXP_TIME: 1.2,  # s
            model.MD_IN_WL: (500e-9, 520e-9),  # m
        }
        intile = model.DataArray(img12, md)
        
        weaver = CollageWeaver()
        weaver.addTile(intile)
        outd = weaver.getFullImage()

        self.assertEqual(outd.shape, intile.shape)
        numpy.testing.assert_array_equal(outd, intile)
        self.assertEqual(outd.metadata, intile.metadata)

        # Same thing but with a typical SEM data
        img8 = numpy.zeros((256, 356), dtype=numpy.uint8) + 40
        md8 = {
            model.MD_DESCRIPTION: u"test sem",  # tiff doesn't support É (but XML does)
            model.MD_ACQ_DATE: time.time(),
            model.MD_PIXEL_SIZE: (1.3e-6, 1.3e-6),  # m/px
            model.MD_POS: (10e-3, 30e-3),  # m
            model.MD_DWELL_TIME: 1.2e-6,  # s
        }
        intile = model.DataArray(img8, md8)

        outd = weave([intile])

        self.assertEqual(outd.shape, intile.shape)
        numpy.testing.assert_array_equal(outd, intile)
        self.assertEqual(outd.metadata, intile.metadata)
        
    def test1(self):
        """
        Test on synthetic image
        """

        img = Image.open("images/test3.tiff")
        numTiles = 2
        overlap = 0.2
        [tiles,pos] = decomposeImageExact(img,overlap,numTiles)
        
        weaver = CollageWeaver()
        for i in range(len(pos)):
            weaver.addTile(tiles[i])
        
        sz = len(weaver.getFullImage())
        self.assertTrue((numpy.array_equal(weaver.getFullImage(),numpy.array(img)[:sz,:sz])))


# @unittest.skip("skip")
class TestMeanWeaver(unittest.TestCase):

    # @unittest.skip("skip")
    def test_one_tile(self):
        """
        Test that when there is only one tile, it's returned as-is
        """
        img12 = numpy.zeros((2048, 1937), dtype=numpy.uint16) + 4000
        md = {
            model.MD_SW_VERSION: "1.0-test",
            model.MD_DESCRIPTION: u"test",  # tiff doesn't support É (but XML does)
            model.MD_ACQ_DATE: time.time(),
            model.MD_BPP: 12,
            model.MD_BINNING: (1, 2),  # px, px
            model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
            model.MD_POS: (1e-3, -30e-3),  # m
            model.MD_EXP_TIME: 1.2,  # s
            model.MD_IN_WL: (500e-9, 520e-9),  # m
        }
        intile = model.DataArray(img12, md)
        
        weaver = MeanWeaver()
        weaver.addTile(intile)
        outd = weaver.getFullImage()

        self.assertEqual(outd.shape, intile.shape)
        numpy.testing.assert_array_equal(outd, intile)
        self.assertEqual(outd.metadata, intile.metadata)

        # Same thing but with a typical SEM data
        img8 = numpy.zeros((256, 356), dtype=numpy.uint8) + 40
        md8 = {
            model.MD_DESCRIPTION: u"test sem",  # tiff doesn't support É (but XML does)
            model.MD_ACQ_DATE: time.time(),
            model.MD_PIXEL_SIZE: (1.3e-6, 1.3e-6),  # m/px
            model.MD_POS: (10e-3, 30e-3),  # m
            model.MD_DWELL_TIME: 1.2e-6,  # s
        }
        intile = model.DataArray(img8, md8)

        outd = weave([intile])

        self.assertEqual(outd.shape, intile.shape)
        numpy.testing.assert_array_equal(outd, intile)
        self.assertEqual(outd.metadata, intile.metadata)
        
    def test1(self):
        """
        Test on synthetic image
        """

        img = Image.open("images/test3.tiff")
        numTiles = 2
        overlap = 0.2
        [tiles,pos] = decomposeImageExact(img,overlap,numTiles)
        
        weaver = MeanWeaver()
        for i in range(len(pos)):
            weaver.addTile(tiles[i])
        
        sz = len(weaver.getFullImage())
        self.assertTrue((numpy.array_equal(weaver.getFullImage(),numpy.array(img)[:sz,:sz])))
        
               
def decomposeImageExact(img, overlap=0.1, numTiles = 5, method="horizontalLines"):
    """ 
    Same as decomposeImage() from registrar_test, except it cuts the image exactly at the specified
    positions, without adding noise
    """
    
    tileSize = int(img.size[0]/numTiles)
    
    pos = []
    tiles = []
    for i in range(numTiles):
        for j in range(numTiles):
            # Positions top left            
            if method == "verticalLines":
                posX = int(i*(1-overlap)*tileSize)
                posY = int(j*(1-overlap)*tileSize)
            elif method == "horizontalLines":
                posX = int(j*(1-overlap)*tileSize)
                posY = int(i*(1-overlap)*tileSize)
            elif method == "horizontalZigzag":  
                if i%2 == 0:
                    posX = int(j*(1-overlap)*tileSize)
                else:
                    posX = int((numTiles-j-1)*(1-overlap)*tileSize) # reverse direction for every second row
                posY = int(i*(1-overlap)*tileSize)
            
            yMax = numTiles*(1-overlap)*tileSize    
            md = {
                model.MD_PIXEL_SIZE: [0.01,0.01],  # m/px
                model.MD_POS: ((posX+tileSize/2)*0.01,(yMax-posY+tileSize/2)*0.01),  # m
            }
            
            
            # Crop images
            cropped = img.crop((posX,posY,posX+tileSize,posY+tileSize))
            tile = numpy.array(cropped.convert('L'))
            
            # Create list of tiles and positions
            tile = model.DataArray(tile, md)
            
            tiles.append(tile)
            pos.append([(posX+tileSize/2)*0.01,(yMax-posY+tileSize/2)*0.01])
            
    return [tiles, pos] 



if __name__ == '__main__':
    unittest.main()
    
    
