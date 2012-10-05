# -*- coding: utf-8 -*-
'''
Created on 17 Jul 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from libtiff import TIFF
from odemis import model
from odemis import __version__
from osgeo import gdal_array
import Image
import gdal
import libtiff
import time

# User-friendly name
FORMAT = "TIFF"
# list of file-name extensions possible, the first one is the default when saving a file 
EXTENSIONS = [".tiff", ".tif"]


# Conversion from our internal tagname convention to (gdal) TIFF tagname
# string -> (string, callable)
DATagToTiffTag = {model.MD_SW_VERSION: ("TIFFTAG_SOFTWARE", str),
                  model.MD_HW_NAME: ("TIFFTAG_HOSTCOMPUTER", str),
                  model.MD_ACQ_DATE: ("TIFFTAG_DATETIME", lambda x: time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime(x)))
                  }

# Export part
# GDAL Python API is documented here: http://gdal.org/python/
def _saveAsTiffGDAL(data, filename):
    """
    Saves a DataArray as a TIFF file.
    data (ndarray): 2D data of int or float
    filename (string): name of the file to save
    """
    driver = gdal.GetDriverByName("GTiff")
    
    # gdal expects the data to be in 'F' order, but it's in 'C'
    # TODO check this from numpy!
    data.shape = (data.shape[1], data.shape[0])
    ds = gdal_array.OpenArray(data)
    data.shape = (data.shape[1], data.shape[0])
    for key, val in data.metadata.items():
        if key in DATagToTiffTag:
            ds.SetMetadataItem(DATagToTiffTag[key][0], DATagToTiffTag[key][1](val))
    driver.CreateCopy(filename, ds, options=["PROFILE=BASELINE"]) # , options=["COMPRESS=LZW"] # LZW makes test image bigger
    
def _saveAsMultiTiffGDAL(ldata, filename):
    """
    Saves a list of DataArray as a multiple-page TIFF file.
    ldata (list of ndarray): list of 2D data of int or float. Should have at least one array
    filename (string): name of the file to save
    """
    # FIXME: This doesn't work because GDAL generates one channel per image 
    # (band) instead of one _page_. It might be possible to use the notion of 
    # subdatasets but it's not clear of GTiff supports them. 
    # see http://www.gdal.org/gdal_tutorial.html
    # and http://www.gdal.org/frmt_hdf5.html 
    # and http://osgeo-org.1560.n6.nabble.com/ngpython-and-GetSubDataSets-td3760233.html
    assert(len(ldata) > 0)
    driver = gdal.GetDriverByName("GTiff")
    
    data0 = ldata[0]
    datatype = gdal_array.NumericTypeCodeToGDALTypeCode(data0.dtype.type)
    # FIXME: we assume all the data is the same shape and type
    ds = driver.Create(filename, data0.shape[1], data0.shape[0], len(ldata), datatype)
    
    for i, data in enumerate(ldata):
        data.shape = (data.shape[1], data.shape[0])
        ds.GetRasterBand(i+1).WriteArray(data)
        data.shape = (data.shape[1], data.shape[0])
        
def _saveAsTiffPIL(array, filename):
    """
    Saves an array as a TIFF file.
    array (ndarray): 2D array of int or float
    filename (string): name of the file to save
    """
    # Two memory copies for one conversion! because of the stride, fromarray() does as bad
    pil_im = Image.fromarray(array)
    #pil_im = Image.fromstring('I', size, array.tostring(), 'raw', 'I;16', 0, -1)
    # 16bits files are converted to 32 bit float TIFF with PIL
    pil_im.save(filename, "TIFF")


# For tags, see convert.py of libtiff.py which has some specific for microscopy
# Or use the LSM format (from Carl Zeiss)?

DATagToLibTiffTag = {model.MD_SW_VERSION: ("SOFTWARE", 
                        lambda x: __version__.shortname + " " + str(x)),
                  model.MD_HW_NAME: ("MAKE", str), # Scanner manufacturer   
                  model.MD_HW_VERSION: ("MODEL", str), # Scanner name
                  model.MD_ACQ_DATE: ("DATETIME", lambda x: time.strftime("%Y:%m:%d %H:%M:%S", time.gmtime(x)))
                  }
#TIFFTAG_DOCUMENTNAME
#TIFFTAG_ARTIST
#TIFFTAG_COPYRIGHT
# MODEL
# MAKE
# XPOSITION
# YPOSITION
#TIFFTAG_XRESOLUTION
#TIFFTAG_YRESOLUTION
#TIFFTAG_RESOLUTIONUNIT
#TIFFTAG_IMAGEDESCRIPTION
# TODO how to put our own tags? => use ome xml in ImageDescription?

def _saveAsTiffLT(filename, data, thumbnail):
    """
    Saves a DataArray as a TIFF file.
    filename (string): name of the file to save
    data (ndarray): 2D data of int or float
    """
    _saveAsMultiTiffLT(filename, [data], thumbnail)

def _saveAsMultiTiffLT(filename, ldata, thumbnail):
    """
    Saves a list of DataArray as a multiple-page TIFF file.
    filename (string): name of the file to save
    ldata (list of ndarray): list of 2D data of int or float. Should have at least one array
    """
    tif = TIFF.open(filename, mode='w')

    if thumbnail is not None:
        # save the thumbnail just as the first image
        tif.SetField("ImageDescription", "Composited image")

        # FIXME:
        # libtiff has a bug: it thinks that RGB image are organised as
        # 3xMxN, while normally in numpy, it's MxNx3. (cf scipy.imread) 
        # So we need to swap the axes
        if len(thumbnail.shape) == 3:
            thumbnail = thumbnail.swapaxes(2,0).swapaxes(2,1) # a new view
        
        # write_rgb makes it clever to detect RGB vs. Greyscale
        tif.write_image(thumbnail, compression="lzw", write_rgb=True)
        
        # TODO also save it as thumbnail of the image (in limited size)
        # see  http://www.libtiff.org/man/thumbnail.1.html
        
        # It seems that libtiff.py doesn't support yet SubIFD's so it's not 
        # going to fly
        
        
        # from http://stackoverflow.com/questions/11959617/in-a-tiff-create-a-sub-ifd-with-thumbnail-libtiff
#        //Define the number of sub-IFDs you are going to write
#        //(assuming here that we are only writing one thumbnail for the image):
#        int number_of_sub_IFDs = 1;
#        toff_t sub_IFDs_offsets[1] = { 0UL };
#        
#        //set the TIFFTAG_SUBIFD field:
#        if(!TIFFSetField(created_TIFF, TIFFTAG_SUBIFD, number_of_sub_IFDs, 
#            sub_IFDs_offsets))
#        {
#            //there was an error setting the field
#        }
#        
#        //Write your main image raster data to the TIFF (using whatever means you need,
#        //such as TIFFWriteRawStrip, TIFFWriteEncodedStrip, TIFFWriteEncodedTile, etc.)
#        //...
#        
#        //Write your main IFD like so:
#        TIFFWriteDirectory(created_TIFF);
#        
#        //Now here is the trick: like the comment in the libtiff source states, the 
#        //next n directories written will be sub-IFDs of the main IFD (where n is 
#        //number_of_sub_IFDs specified when you set the TIFFTAG_SUBIFD field)
#        
#        //Set up your sub-IFD
#        if(!TIFFSetField(created_TIFF, TIFFTAG_SUBFILETYPE, FILETYPE_REDUCEDIMAGE))
#        {
#            //there was an error setting the field
#        }
#        
#        //set the rest of the required tags here, as well as any extras you would like
#        //(remember, these refer to the thumbnail, not the main image)
#        //...
#        
#        //Write this sub-IFD:
#        TIFFWriteDirectory(created_TIFF);
        
    for data in ldata:
        # Save metadata (before the image)
        for key, val in data.metadata.items():
            if key in DATagToLibTiffTag:
                tag, converter = DATagToLibTiffTag[key]
                tif.SetField(tag, converter(val))

        tif.write_image(data, compression="lzw")

def export(filename, data, thumbnail=None):
    '''
    Write a TIFF file with the given image and metadata
    filename (string): filename of the file to create (including path)
    data (list of model.DataArray, or model.DataArray): the data to export, 
        must be 2D of int or float. Metadata is taken directly from the data 
        object. If it's a list, a multiple page file is created.
    thumbnail (None or numpy.array): Image used as thumbnail for the file. Can be of any
      (reasonable) size. Must be either 2D array (greyscale) or 3D with last 
      dimension of length 3 (RGB). If the exporter doesn't support it, it will
      be dropped silently.
    '''
    if isinstance(data, list):
        _saveAsMultiTiffLT(filename, data, thumbnail)
    else:
        # TODO should probably not enforce it: respect duck typing
        assert(isinstance(data, model.DataArray))
        _saveAsTiffLT(filename, data, thumbnail)
    
    