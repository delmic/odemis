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
from odemis import model
from osgeo import gdal_array
from libtiff import TIFF
import Image
import gdal
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
#TIFFTAG_DOCUMENTNAME
#TIFFTAG_IMAGEDESCRIPTION
#TIFFTAG_ARTIST
#TIFFTAG_COPYRIGHT
#TIFFTAG_XRESOLUTION
#TIFFTAG_YRESOLUTION
#TIFFTAG_RESOLUTIONUNIT
# TODO how to put our own tags?

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


# TODO support thumbnails: http://www.libtiff.org/man/thumbnail.1.html
def _saveAsTiffLT(data, filename):
    """
    Saves a DataArray as a TIFF file.
    data (ndarray): 2D data of int or float
    filename (string): name of the file to save
    """
    tif = TIFF.open(filename, mode='w')
    tif.write_image(data)

def _saveAsMultiTiffLT(ldata, filename):
    """
    Saves a list of DataArray as a multiple-page TIFF file.
    ldata (list of ndarray): list of 2D data of int or float. Should have at least one array
    filename (string): name of the file to save
    """
    tif = TIFF.open(filename, mode='w')

    for data in ldata:    
        tif.write_image(data)

# TODO interface must support thumbnail export as well
def export(data, filename):
    '''
    Write a TIFF file with the given image and metadata
    data (list of model.DataArray, or model.DataArray): the data to export, 
        must be 2D of int or float. Metadata is taken directly from the data 
        object. If it's a list, a multiple page file is created.
    filename (string): filename of the file to create (including path)
    '''
    if isinstance(data, list):
        _saveAsMultiTiffLT(data, filename)
    else:
        # TODO should probably not enforce it: respect duck typing
        assert(isinstance(data, model.DataArray))
        _saveAsTiffLT(data, filename)
    
    