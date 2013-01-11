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
from __future__ import division
from libtiff import TIFF
from odemis import __version__, model
import libtiff.libtiff_ctypes as T # for the constant names
#pylint: disable=E1101
import logging
import numpy
import time
import xml.etree.ElementTree as ET

# Note concerning the image format: it follows the numpy convention. The first
# dimension is the height, and second one is the width. (This is so because
# in memory the height is the slowest changing dimension, so it is first in C
# order.)
# So an image of W horizontal pixels, H vertical pixels, and 3 colours is an
# array of shape (H, W, 3). It is recommended to have the image in memory in C
# order (but that should not matter).
# PIL and wxPython have images with the size expressed as (width, height), although
# in memory it corresponds to the same representation.

# User-friendly name
FORMAT = "TIFF"
# list of file-name extensions possible, the first one is the default when saving a file 
EXTENSIONS = [".ome.tiff", ".ome.tif", ".tiff", ".tif"]


# For tags, see convert.py of libtiff.py which has some specific for microscopy
# Or use the LSM format (from Carl Zeiss)?

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

def _convertToTiffTag(metadata):
    """
    Converts DataArray tags to libtiff tags.
    metadata (dict of tag -> value): the metadata of a DataArray
    returns (dict of tag -> value): the metadata as compatible for libtiff
    """
    tiffmd = {}
    # we've got choice between inches and cm... so it's easy 
    tiffmd[T.TIFFTAG_RESOLUTIONUNIT] = T.RESUNIT_CENTIMETER
    for key, val in metadata.items():
        if key == model.MD_SW_VERSION:
            tiffmd[T.TIFFTAG_SOFTWARE] = __version__.shortname + " " + val
        elif key == model.MD_HW_NAME:
            tiffmd[T.TIFFTAG_MAKE] = val
        elif key == model.MD_HW_VERSION:
            tiffmd[T.TIFFTAG_MODEL] = val
        elif key == model.MD_ACQ_DATE:
            tiffmd[T.TIFFTAG_DATETIME] = time.strftime("%Y:%m:%d %H:%M:%S", time.gmtime(val))
        elif key == model.MD_PIXEL_SIZE:
            # convert m/px -> px/cm
            try:
                tiffmd[T.TIFFTAG_XRESOLUTION] = (1 / val[0]) / 100
                tiffmd[T.TIFFTAG_YRESOLUTION] = (1 / val[1]) / 100
            except ZeroDivisionError:
                logging.debug("Pixel size tag is incorrect: %r", val)
        elif key == model.MD_POS:
            # convert m -> cm
            # XYPosition doesn't support negative values. So instead, we shift
            # everything by 1 m, which should be enough as samples are typically
            # a few cm big. The most important is that the image positions are  
            # correct relatively to each other (for a given sample).
            tiffmd[T.TIFFTAG_XPOSITION] = 100 + val[0] * 100
            tiffmd[T.TIFFTAG_YPOSITION] = 100 + val[1] * 100
        elif key == model.MD_ROTATION:
            # TODO: should use the coarse grain rotation to update Orientation
            # and update rotation information to -45< rot < 45 -> maybe GeoTIFF's ModelTransformationTag?
            # or actually rotate the data?
            logging.info("Metadata tag '%s' skipped when saving TIFF file", key)
        # TODO MD_BPP : the actual bit size of the detector 
        elif key == model.MD_DESCRIPTION:
            # We don't use description as it's used for OME-TIFF
            tiffmd[T.TIFFTAG_PAGENAME] = val
        # TODO save the brightness and contrast applied by the user?
        # Could use GrayResponseCurve, DotRange, or TransferFunction?
        # TODO save the tint applied by the user? maybe WhitePoint can help
        # TODO save username as "Artist" ? => not gonna fly if the user is "odemis"
        else:
            logging.debug("Metadata tag '%s' skipped when saving TIFF file", key)
    
    return tiffmd
    
def _convertToOMEMD(images):
    """
    Converts DataArray tags to OME-TIFF tags.
    images (list of DataArrays): the images that will be in the TIFF file, in order
    returns (string): the XML data as compatible with OME
    Note: the first element of images should be in the IFD 0, second element in
      IFD 1, etc.
    """
    # An OME-TIFF is a TIFF file with one OME-XML metadata embedded. For an
    # overview of OME-XML, see:
    # http://www.openmicroscopy.org/Schemas/Documentation/Generated/OME-2012-06/ome.html
    
    # It is not very clear in OME how to express that it's different acquisitions
    # of the _same_ sample by different instruments. However, our interpretation
    # of the format is the following:
#    + OME
#      + Experiment        # To describe the type of microscopy
#      + Experimenter      # To describe the user
#      + Instrument (*)    # To describe the acquisition technical details for each
#        + Microscope      # set of emitter/detector.
#        + LightSource (*)
#        + Detector (*)
#        + Objective (*)
#        + Filter (*)
#      + Image (*)         # To describe a set of images by the same instrument
#        + Description     # Not sure what to put (Image has "Name" attribute) => simple user note?
#        + AcquisitionDate # time of acquisition of the (first) image
#        + ExperimentRef
#        + ExperimenterRef
#        + InstrumentRef
#        + ImagingEnvironment # To describe the physical conditions (temp...)
#        + Pixels          # technical dimensions of the images (XYZ, T, C) 
#          + Channel (*)   # emitter settings for the given channel (light wavelength)
#            + DetectorSettings 
#          + Plane (*)     # physical dimensions/position of each images
#          + TiffData (*)  # where to find the data in the tiff file (IFD)
#                          # we explicitly reference each dataarray to avoid
#                          # potential ordering problems of the "Image" elements  
#          
    
    # To create and manipulate the XML, we use the Python ElementTree API.
    
    # TODO: it seems pylibtiff has a small OME support, need to investigate 
    # how much could be used.
    root = ET.Element('OME', attrib={
            "xmlns": "http://www.openmicroscopy.org/Schemas/OME/2012-06",
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xsi:schemaLocation": "http://www.openmicroscopy.org/Schemas/OME/2012-06 http://www.openmicroscopy.org/Schemas/OME/2012-06/ome.xsd",
            })
    com_txt = ("Warning: this comment is an OME-XML metadata block, which "
               "contains crucial dimensional parameters and other important "
               "metadata. Please edit cautiously (if at all), and back up the "
               "original data before doing so. For more information, see the "
               "OME-TIFF web site: http://ome-xml.org/wiki/OmeTiff.")
    root.append(ET.Comment(com_txt))
    
    # add the microscope
    instr = ET.SubElement(root, "Instrument", attrib={
                                      "ID": "Instrument:0"})
    micro = ET.SubElement(instr, "Microscope", attrib={
                               "Manufacturer": "Delmic",
                               "Model": "SECOM", # FIXME: should depend on the metadata
                                })
    
    # for each set of images from the same instrument, add them
    groups = _findImageGroups(images)
    
    # Detectors
    for g in groups:
        id = min(g) # ID is the smallest ID of the images
        da0 = images[id]
        if model.MD_HW_NAME in da0.metadata:
            detect = ET.SubElement(instr, "Detector", attrib={
                                      "ID": "Detector:%d" % id,
                                      "Model": da0.metadata[model.MD_HW_NAME]})
    
    # Objectives
    for g in groups:
        id = min(g) # ID is the smallest ID of the images
        da0 = images[id]
        if model.MD_OPT_MAG in da0.metadata:
            obj = ET.SubElement(instr, "Objective", attrib={
                      "ID": "Objective:%d" % id,
                      "CalibratedMagnification": "%f" % da0.metadata[model.MD_OPT_MAG]
                      })
    
    for g in groups:
        _addImageElement(root, images, list(g))
    
    # TODO add tag to each image with "Odemis", so that we can find them back
    # easily in a database?
    
    ometxt = ('<?xml version="1.0" encoding="UTF-8"?>' +
              ET.tostring(root, encoding="utf-8")) 
    return ometxt 

def _findImageGroups(das):
    """
    Find groups of images which should be considered part of the same acquisition
    (aka "Image" in OME-XML).
    das (list of DataArray): all the images of the final TIFF file
    returns (list of list of int): a set of "groups", each group is represented
      by a set of indexes (of the images being part of the group)
    """
    # We consider images to be part of the same group if they have:
    # * same shape
    # * metadata that show they were acquired by the same instrument
    groups = []
    
    for i, da in enumerate(das):
        # try to find a matching group (compare just to the first picture)
        found = False
        for g in groups:
            da0 = das[g[0]]
            if da0.shape != da.shape:
                continue
            if (da0.metadata.get(model.MD_HW_NAME, None) != da.metadata.get(model.MD_HW_NAME, None) or
                da0.metadata.get(model.MD_HW_VERSION, None) != da.metadata.get(model.MD_HW_VERSION, None)):
                continue
            g.append(i)
            found = True
            break
        
        if not found:
            # if not, create a new group
            groups.append([i])
    
    return groups
    
def _addImageElement(root, das, idx):
    """
    Add the metadata of a list of DataArray to a OME-XML root element 
    root (Element): the root element
    das (list of DataArray): all the images of the final TIFF file
    idx (list of int): the indexes of DataArray to add
    """
    assert(len(idx) > 0)
    
    idnum = len(root.findall("Image"))
    ime = ET.SubElement(root, "Image", attrib={"ID": "Image:%d" % idnum})

    # compute a common metadata
    globalMD = {}
    for i in idx:
        globalMD.update(das[i].metadata)
    
    # find out about the common attribute (Name)
    if model.MD_DESCRIPTION in globalMD:
        ime.attrib["Name"] = globalMD[model.MD_DESCRIPTION]
    
    # find out about the common sub-elements (time, user note, shape)  
    if model.MD_USER_NOTE in globalMD:
        desc = ET.SubElement(ime, "Description")
        desc.text = globalMD[model.MD_USER_NOTE]
    
    # TODO: should be the earliest time?
    globalAD = None
    if model.MD_ACQ_DATE in globalMD:
        ad = ET.SubElement(ime, "AcquisitionDate")
        globalAD = globalMD[model.MD_ACQ_DATE]
        ad.text = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(globalAD))

    # count the number of channels (RGB=3)
    nb_channels = 0
    for i in idx:
        da = das[i]
        if len(da.shape) == 3:
            nb_channels += da.shape[2]
        else:
            nb_channels += 1
    
    # TODO: check that all the DataArrays have the same shape
    da0 = das[idx[0]]
    pixels = ET.SubElement(ime, "Pixels", attrib={
                              "ID": "Pixels:%d" % idnum,
                              "DimensionOrder": "XYZTC", # we don't have ZT so it doesn't matter
                              "Type": "%s" % da0.dtype, # seems to be compatible in general
                              "SizeX": "%d" % da0.shape[1], # numpy shape is reversed
                              "SizeY": "%d" % da0.shape[0],
                              "SizeZ": "1", # for now, always one
                              "SizeT": "1", # for now, always one
                              "SizeC": "%d" % nb_channels,
                              })
    # Add optional values
    if model.MD_PIXEL_SIZE in globalMD:
        pxs = globalMD[model.MD_PIXEL_SIZE]
        pixels.attrib["PhysicalSizeX"] = "%f" % (pxs[0] * 1e6) # in µm
        pixels.attrib["PhysicalSizeY"] = "%f" % (pxs[1] * 1e6) # in µm
    
    # For each DataArray, add a Channel, TiffData, and Plane, but be careful 
    # because they all have to be grouped and in this order.
    
    # Channel Element
    subid = 0
    for i in idx:
        da = das[i]
        chan = ET.SubElement(pixels, "Channel", attrib={
                               "ID": "Channel:%d:%d" % (idnum, subid)})
        # RGB?
        # Note: it seems officially OME-TIFF doesn't support RGB TIFF (instead, 
        # each colour should go in a separate channel). However, that'd defeat
        # the purpose of the thumbnail, and it seems at OMERO handles this
        # not too badly (all the other images get 3 components).
        if len(da.shape) == 3 and da.shape[2] == 3:
            chan.attrib["SamplesPerPixel"] = "3"
        
        # TODO Name attrib for Filtered color streams?
        if model.MD_DESCRIPTION in da.metadata:
            chan.attrib["Name"] = da.metadata[model.MD_DESCRIPTION]
            
        # TODO Color attrib for tint?
        # TODO Fluor attrib for the dye?
        if model.MD_IN_WL in da.metadata:
            iwl = da.metadata[model.MD_IN_WL]
            xwl = numpy.mean(iwl) * 1e9 # in nm
            chan.attrib["ExcitationWavelength"] = "%f" % xwl
            
            # if input wavelength range is small, it means we are in epifluoresence
            if abs(iwl[1] - iwl[0]) < 100e-9:
                chan.attrib["IlluminationType"] = "Epifluorescence"
                chan.attrib["AcquisitionMode"] = "WideField"
                chan.attrib["ContrastMethod"] = "Fluorescence"
            else:
                chan.attrib["IlluminationType"] = "Epifluorescence"
                chan.attrib["AcquisitionMode"] = "WideField"
                chan.attrib["ContrastMethod"] = "Brightfield"

        if model.MD_OUT_WL in da.metadata:
            owl = da.metadata[model.MD_OUT_WL]
            ewl = numpy.mean(owl) * 1e9 # in nm
            chan.attrib["EmissionWavelength"] = "%f" % ewl
        
        # Add info on detector
        attrib = {}
        if model.MD_BINNING in da.metadata:
            attrib["Binning"] = "{0}x{0}".format(da.metadata[model.MD_BINNING])
        if model.MD_GAIN in da.metadata:
            attrib["Gain"] = "%f" % da.metadata[model.MD_GAIN]
        if model.MD_READOUT_TIME in da.metadata:
            ror = (1 / da.metadata[model.MD_READOUT_TIME]) / 1e6 #MHz
            attrib["ReadOutRate"] = "%f" % ror
            
        if attrib:
            # detector of the group has the same id as the lowest id in the group
            attrib["ID"] = "Detector:%d" % min(idx)
            ds = ET.SubElement(chan, "DetectorSettings", attrib=attrib)
        
        subid += 1

    # TiffData Element
    subid = 0
    for i in idx:
        da = das[i]
        tde = ET.SubElement(pixels, "TiffData", attrib={
                                "IFD": "%d" % i,
                                "FirstC": "%d" % subid,
                                "PlaneCount": "1"
                                })
        subid += 1
        
    # Plane Element
    subid = 0
    for i in idx:
        da = das[i]
        plane = ET.SubElement(pixels, "Plane", attrib={
                               "TheZ": "0",
                               "TheT": "0",
                               "TheC": "%d" % subid,
                               })
        if model.MD_ACQ_DATE in da.metadata:
            diff = da.metadata[model.MD_ACQ_DATE] - globalAD
            plane.attrib["DeltaT"] = "%.12f" % diff
            
        if model.MD_EXP_TIME in da.metadata:
            exp = da.metadata[model.MD_EXP_TIME]
            plane.attrib["ExposureTime"] = "%.12f" % exp
        elif model.MD_DWELL_TIME in da.metadata:
            # typical for scanning techniques => more or less correct
            exp = da.metadata[model.MD_DWELL_TIME] * numpy.prod(da.shape)
            plane.attrib["ExposureTime"] = "%.12f" % exp
        
        # Note that Position has no official unit, which prevent Tiling to be
        # usable. In one OME-TIFF official example of tiles, they use pixels
        # (and ModuloAlongT "tile")
        if model.MD_POS in da.metadata:
            pos = da.metadata[model.MD_POS]
            plane.attrib["PositionX"] = "%.12f" % pos[0] # any unit is allowed => m
            plane.attrib["PositionY"] = "%.12f" % pos[1]
        
        subid += 1
        
def _saveAsTiffLT(filename, data, thumbnail):
    """
    Saves a DataArray as a TIFF file.
    filename (string): name of the file to save
    data (ndarray): 2D data of int or float
    """
    _saveAsMultiTiffLT(filename, [data], thumbnail)

def _saveAsMultiTiffLT(filename, ldata, thumbnail, compressed=True):
    """
    Saves a list of DataArray as a multiple-page TIFF file.
    filename (string): name of the file to save
    ldata (list of DataArray): list of 2D data of int or float. Should have at least one array
    thumbnail (None or DataArray): see export
    compressed (boolean): whether the file is LZW compressed or not.
    """
    tif = TIFF.open(filename, mode='w')
    
    # According to this page: http://www.openmicroscopy.org/site/support/file-formats/ome-tiff/ome-tiff-data
    # LZW is a good trade-off between compatibility and small size (reduces file
    # size by about 2). => that's why we use it by default
    if compressed:
        compression = "lzw"
    else:
        compression = None

    # OME tags: a XML document in the ImageDescription of the first image
    if thumbnail is not None:
        alldata = [thumbnail] + ldata
    else:
        alldata = ldata
    ometxt = _convertToOMEMD(alldata)
    
    if thumbnail is not None:
        # save the thumbnail just as the first image
        tif.SetField(T.TIFFTAG_IMAGEDESCRIPTION, ometxt)
        ometxt = None
        tif.SetField(T.TIFFTAG_PAGENAME, "Composited image")

        # FIXME:
        # libtiff has a bug: it thinks that RGB image are organised as
        # 3xHxW, while normally in numpy, it's HxWx3. (cf scipy.imread)
        # So we need to swap the axes
        if len(thumbnail.shape) == 3:
            thumbnail = numpy.rollaxis(thumbnail, 2) # a new view
        
        # write_rgb makes it clever to detect RGB vs. Greyscale
        tif.write_image(thumbnail, compression=compression, write_rgb=True)
        
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
        tags = _convertToTiffTag(data.metadata)
        for key, val in tags.items():
            tif.SetField(key, val)
        
        if ometxt: # save OME tags if not yet done
            tif.SetField(T.TIFFTAG_IMAGEDESCRIPTION, ometxt)
            ometxt = None
        
        tif.write_image(data, compression=compression)

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
    
    