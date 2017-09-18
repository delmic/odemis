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

from PIL import Image
import unittest
from odemis.acq.stitching import register
from registrar_test import decomposeImage
from odemis import model

class TestStitching(unittest.TestCase):

    # @unittest.skip("skip")
    def test_register(self):
        """
        Test register wrapper function
        """
        
        img = Image.open("images/test3.tiff")
        numTiles = 2
        overlap = 0.2
        [tiles,pos] = decomposeImage(img,overlap,numTiles,"horizontalZigzag")
        
        updatedTiles = register(tiles,"REGISTER_SHIFT")

        for i in range(len(updatedTiles)):
            calculatedPosition = updatedTiles[i].metadata[model.MD_POS]
            self.assertAlmostEqual(calculatedPosition[0],pos[i][0],places=1)
            self.assertAlmostEqual(calculatedPosition[1],pos[i][1],places=1)
        
    # @unittest.skip("skip")
    def test_dep_tiles(self):
        """
        Test register wrapper function, when dependent tiles are present
        """
        
        img = Image.open("images/test3.tiff")
        numTiles = 2
        overlap = 0.2
        [tiles,pos] = decomposeImage(img,overlap,numTiles,"horizontalZigzag")
        newTiles = []
        for i in range(len(tiles)):
            newTiles.append(tuple((tiles[i],tiles[i])))
        updatedTiles = register(newTiles,"REGISTER_SHIFT")

        for i in range(len(updatedTiles)):
            calculatedPosition = updatedTiles[i][0].metadata[model.MD_POS]
            self.assertAlmostEqual(calculatedPosition[0],pos[i][0],places=1)
            self.assertAlmostEqual(calculatedPosition[1],pos[i][1],places=1)
            
            # Dependent tile
            calculatedPosition = updatedTiles[i][1].metadata[model.MD_POS]
            self.assertAlmostEqual(calculatedPosition[0],pos[i][0],places=1)
            self.assertAlmostEqual(calculatedPosition[1],pos[i][1],places=1)
        
    
if __name__ == '__main__':
    unittest.main()