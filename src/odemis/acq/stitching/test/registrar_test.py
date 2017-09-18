# -*- coding: utf-8 -*-
'''
Created on 26 Jul 2017

@author: Éric Piel, Philip Winkler

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

from __future__ import division
import random
import logging
from odemis import model
import numpy as np
import time
import unittest


from odemis.acq.stitching import *

from PIL import Image



logging.getLogger().setLevel(logging.DEBUG)

# TODO: test more than one tile using IdentityRegistrar
# TODO: test ShiftRegistrar on white image

# @unittest.skip("skip")
class TestIdentityRegistrar(unittest.TestCase):

    # @unittest.skip("skip")
    def test_one_tile(self):
        """
        Test that when there is only one tile, it's returned as-is
        """
        img12 = np.zeros((2048, 1937), dtype=np.uint16) + 4000
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

        registrar = IdentityRegistrar()
        registrar.addTile(intile)
        
        
        self.assertEqual(registrar.getPositions()[0][0], intile.metadata[model.MD_POS])

        # Same thing but with a typical SEM data
        img8 = np.zeros((256, 356), dtype=np.uint8) + 40
        md8 = {
            model.MD_DESCRIPTION: u"test sem",  # tiff doesn't support É (but XML does)
            model.MD_ACQ_DATE: time.time(),
            model.MD_PIXEL_SIZE: (1.3e-6, 1.3e-6),  # m/px
            model.MD_POS: (10e-3, 30e-3),  # m
            model.MD_DWELL_TIME: 1.2e-6,  # s
        }
        intile = model.DataArray(img8, md8)
        
        registrar = IdentityRegistrar()
        registrar.addTile(intile)

        self.assertAlmostEqual(registrar.getPositions()[0][0], intile.metadata[model.MD_POS],5)
        
        
class TestShiftRegistrar(unittest.TestCase):   
    def test_one_tile(self):
        """
        Test that when there is only one tile, it's returned as-is
        """
        img12 = np.zeros((2048, 1937), dtype=np.uint16) + 4000
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

        registrar = ShiftRegistrar()
        registrar.addTile(intile)
        
        
        self.assertEqual(registrar.getPositions()[0][0], intile.metadata[model.MD_POS])

        # Same thing but with a typical SEM data
        img8 = np.zeros((256, 356), dtype=np.uint8) + 40
        md8 = {
            model.MD_DESCRIPTION: u"test sem",  # tiff doesn't support É (but XML does)
            model.MD_ACQ_DATE: time.time(),
            model.MD_PIXEL_SIZE: (1.3e-6, 1.3e-6),  # m/px
            model.MD_POS: (10e-3, 30e-3),  # m
            model.MD_DWELL_TIME: 1.2e-6,  # s
        }
        intile = model.DataArray(img8, md8)
        
        registrar = ShiftRegistrar()
        registrar.addTile(intile)

        self.assertAlmostEqual(registrar.getPositions()[0][0], intile.metadata[model.MD_POS],5)
        
        
    def test1(self):
        """ Test on decomposed image with known shift """
        img = Image.open("images/test3.tiff")
        numTiles = 2
        overlap = 0.2
        [tiles,pos] = decomposeImage(img,overlap,numTiles)
        
        registrar = ShiftRegistrar()
    
        for i in range(len(pos)):
            registrar.addTile(tiles[i])
            calculatedPositions = registrar.getPositions()[0]
            self.assertAlmostEqual(calculatedPositions[i][0],pos[i][0],places=1)
            self.assertAlmostEqual(calculatedPositions[i][1],pos[i][1],places=1)
            
    def test2(self):
        """ Test different overlap """
        img = Image.open("images/test3.tiff")
        numTiles = 2
        overlap = 0.1
        [tiles,pos] = decomposeImage(img,overlap,numTiles)
        
        registrar = ShiftRegistrar()
        for i in range(len(pos)):
            registrar.addTile(tiles[i])
            calculatedPositions = registrar.getPositions()[0]
            self.assertAlmostEqual(calculatedPositions[i][0],pos[i][0],places=1)
            self.assertAlmostEqual(calculatedPositions[i][1],pos[i][1],places=1)

    def test3(self):
        """ Test more tiles """
        img = Image.open("images/test3.tiff")
        numTiles = 4
        overlap = 0.4
        [tiles,pos] = decomposeImage(img,overlap,numTiles)
        
        registrar = ShiftRegistrar()
        for i in range(len(pos)):
            registrar.addTile(tiles[i])
            calculatedPositions = registrar.getPositions()[0]
            self.assertAlmostEqual(calculatedPositions[i][0],pos[i][0],places=1)
            self.assertAlmostEqual(calculatedPositions[i][1],pos[i][1],places=1)
           
    def test4(self):
        """ Test on different image """
        img = Image.open("images/test2.tiff")
        numTiles = 2
        overlap = 0.2
        [tiles,pos] = decomposeImage(img,overlap,numTiles)
        
        registrar = ShiftRegistrar()
        for i in range(len(pos)):
            registrar.addTile(tiles[i])
            calculatedPositions = registrar.getPositions()[0]
            self.assertAlmostEqual(calculatedPositions[i][0],pos[i][0],places=1)
            self.assertAlmostEqual(calculatedPositions[i][1],pos[i][1],places=1)

    def test5(self):
        """ Test acquisition in vertical direction """
        img = Image.open("images/test2.tiff")
        numTiles = 2
        overlap = 0.2
        [tiles,pos] = decomposeImage(img,overlap,numTiles,"verticalLines")
        
        registrar = ShiftRegistrar()
        for i in range(len(pos)):
            registrar.addTile(tiles[i])
            calculatedPositions = registrar.getPositions()[0]
            self.assertAlmostEqual(calculatedPositions[i][0],pos[i][0],places=1)
            self.assertAlmostEqual(calculatedPositions[i][1],pos[i][1],places=1)
            
    def test6(self):
        """ Test acquisition in horizontal zigzag direction  """
        img = Image.open("images/test2.tiff")
        numTiles = 2
        overlap = 0.2
        [tiles,pos] = decomposeImage(img,overlap,numTiles,"horizontalZigzag")
        
        registrar = ShiftRegistrar()
        for i in range(len(pos)):
            registrar.addTile(tiles[i])
            calculatedPositions = registrar.getPositions()[0]
            self.assertAlmostEqual(calculatedPositions[i][0],pos[i][0],places=1)
            self.assertAlmostEqual(calculatedPositions[i][1],pos[i][1],places=1)
                
    def test7(self):
        """ Test case not generated by decompose.py file and manually cropped """
        img = Image.open("images/test.png")
        cropped1 = img.crop((0,0,400,400))
        cropped2 = img.crop((322,4,882,404))
  
        registrar = ShiftRegistrar()
        tile1 = model.DataArray(np.array(cropped1.convert("L")),{
                model.MD_PIXEL_SIZE: [400,400],  # m/px
                model.MD_POS: (200, 200),  # m
            })
        tile2 = model.DataArray(np.array(cropped2.convert("L")),{
                model.MD_PIXEL_SIZE: [400,400],  # m/px
                model.MD_POS: (200, 520),  # m
            })
        registrar.addTile(tile1)
        registrar.addTile(tile2)
        calculatedPositions = registrar.getPositions()[0]
        self.assertAlmostEqual(calculatedPositions[1][0],522,places=1)
        self.assertAlmostEqual(calculatedPositions[1][1],204,places=1)          
        
def decomposeImage(img, overlap=0.1, numTiles = 5, method="horizontalLines"):
    """ 
    Decomposes image into tiles for testing. The tiles overlap and their center positions are subject to random noise.
    Returns list of tiles and list of the actual positions. 
    img: square image obtained by PIL module
    numTiles: number of desired tiles in each direction
    method: acquisition method, "horizontalLines" scans image by row and starts at the left for each row,
    "verticalLines" scans image by columns starting at the top for each row, and "horizontalZigzag" scans 
    a row, then scans the next row in reverse, etc. mimicking the behaviour of DELMIC microscopes. 
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
                
            md = {
                model.MD_PIXEL_SIZE: [tileSize,tileSize],  # m/px
                model.MD_POS: (posY+tileSize//2,posX+tileSize//2),  # m
            }
             
            # Add noise
            maxNoise = int(0.2*overlap*tileSize)
            random.seed(1)
            noise = [random.randrange(-maxNoise,maxNoise) for _ in range(2)]
            if i>0 or j>0:
                posX = max(0,int(posX + noise[0]))
                posY = max(0,int(posY + noise[1]))
            
            # Crop images
            cropped = img.crop((posX,posY,posX+tileSize,posY+tileSize))
            tile = np.array(cropped.convert('L'))
            
            # Create list of tiles and positions
            tile = model.DataArray(tile, md)
            
            tiles.append(tile)
            pos.append([posX+tileSize//2,posY+tileSize//2])
            
    return [tiles, pos] 
    
if __name__ == '__main__':
    unittest.main()
