from __future__ import division
import random
import numpy as np
from odemis import model

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

