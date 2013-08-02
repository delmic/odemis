# -*- coding: utf-8 -*-
'''
Created on 17 Jul 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

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
from libtiff import TIFF
from odemis import model
import libtiff.libtiff_ctypes as T # for the constant names
import logging
import numpy
import odemis
import time
import xml.etree.ElementTree as ET
#pylint: disable=E1101

# Note about libtiff: it's a pretty ugly library, with 2 different wrappers.
# We use only the C wrapper (not the Python implementation).



# monkey patching of TIFF.read_image() because the original version cannot read RGB
np = numpy
def _TIFF_read_image(self, verbose=False):
    """ Read image from TIFF and return it as an array.
    """
    width = self.GetField('ImageWidth')
    height = self.GetField('ImageLength')
    samples_pp = self.GetField('SamplesPerPixel') # this number includes extra samples
    if samples_pp is None:
        samples_pp = 1
    bits = self.GetField('BitsPerSample') # samples_pp values
    # The version of libtiff we have doesn't support count =, so assume they are all the same
    # anyway, even libtiff doesn't support different values per sample
#    bits = [bits] * samples_pp
    planar_config = self.GetField('PlanarConfig') # default is contig
    if planar_config is None:
        planar_config = T.PLANARCONFIG_CONTIG
    sample_format = self.GetField('SampleFormat')
    compression = self.GetField('Compression')

    typ = self.get_numpy_type(bits, sample_format)
    if typ is None:
        raise NotImplementedError("%d bits" % bits)
    # TODO: might need special support if bits < 8

    if samples_pp == 1:
        # only 2 dimensions array
        arr = np.empty((height, width), typ)
    else:
        if planar_config == T.PLANARCONFIG_CONTIG:
            arr = np.empty((height, width, samples_pp), typ)
        elif planar_config == T.PLANARCONFIG_SEPARATE:
            # Each sample must be of the same type, as numpy doesn't support
            # dimensions of different types (alternatively, we could just return
            # a list of arrays, with one array per plane).
            arr = np.empty((samples_pp, height, width), typ)
        else:
            raise NotImplementedError("Planarconfig = %d" % planar_config)
    size = arr.nbytes

    if compression == T.COMPRESSION_NONE:
        ReadStrip = self.ReadRawStrip
    else:
        ReadStrip = self.ReadEncodedStrip

    pos = 0
    for strip in range(self.NumberOfStrips()):
        elem = ReadStrip(strip, arr.ctypes.data + pos, max(size - pos, 0))
        pos += elem
    return arr

TIFF.read_image = _TIFF_read_image

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
            tiffmd[T.TIFFTAG_SOFTWARE] = odemis.__shortname__ + " " + val
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
        # Use SMINSAMPLEVALUE and SMAXSAMPLEVALUE ?
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

def _rational2float(rational):
    """
    Converts a rational number (from libtiff) to a float
    rational (numpy array of shape 1 with numer and denom fields): numerator, denominator
    returns (float): numer/ denom
    """
    return rational["numer"][0] / rational["denom"][0]

def _GetFieldDefault(tfile, tag, default=None):
    """
    Same as TIFF.GetField(), but if the tag is not defined, return default
    Note: the C libtiff has GetFieldDefaulted() which returns the dafault value
    of the specification, this function is different.
    tag (int or string): tag id or name
    default (value): value to return if the tag is not defined
    """
    ret = tfile.GetField(tag)
    if ret is None:
        return default
    else:
        return ret

# factor for value -> me
resunit_to_m = {T.RESUNIT_INCH: 0.0254, T.RESUNIT_CENTIMETER: 0.01}
def _readTiffTag(tfile):
    """
    Reads the tiff tags of the current page and convert them into metadata
    It tries to do the reverse of _convertToTiffTag(). Support for other
    metadata and other ways to encode metadata is best-effort only.
    tfile (TIFF): the opened tiff file
    return (dict of tag -> value): the metadata of a DataArray
    """
    md = {}

    # scale + position
    resunit = _GetFieldDefault(tfile, T.TIFFTAG_RESOLUTIONUNIT, T.RESUNIT_INCH)
    factor = resunit_to_m.get(resunit, 1) # default to 1

    xres = tfile.GetField(T.TIFFTAG_XRESOLUTION)
    yres = tfile.GetField(T.TIFFTAG_YRESOLUTION)
    if xres is not None and yres is not None:
        md[model.MD_PIXEL_SIZE] = (factor / _rational2float(xres),
                                   factor / _rational2float(yres))

    xpos = tfile.GetField(T.TIFFTAG_XPOSITION)
    ypos = tfile.GetField(T.TIFFTAG_YPOSITION)
    if xpos is not None and ypos is not None:
        # -1 m for the shift
        md[model.MD_POS] = (factor / _rational2float(xpos) - 1,
                            factor / _rational2float(yres) - 1)

    # informative metadata
    val = tfile.GetField(T.TIFFTAG_PAGENAME)
    if val is not None:
        md[model.MD_DESCRIPTION] = val
    val = tfile.GetField(T.TIFFTAG_SOFTWARE)
    if val is not None:
        md[model.MD_SW_VERSION] = val
    val = tfile.GetField(T.TIFFTAG_MAKE)
    if val is not None:
        md[model.MD_HW_NAME] = val
    val = tfile.GetField(T.TIFFTAG_MODEL)
    if val is not None:
        md[model.MD_HW_VERSION] = val
    val = tfile.GetField(T.TIFFTAG_MODEL)
    if val is not None:
        try:
            t = time.mktime(time.strptime(val, "%Y:%m:%d %H:%M:%S"))
            md[model.MD_ACQ_DATE] = t
        except (OverflowError, ValueError):
            logging.info("Failed to parse date '%s'", val)

    return md

def _isThumbnail(tfile):
    """
    Detects wheter the current image if a file is a thumbnail or not
    returns (boolean): True if the image is a thumbnail, False otherwise
    """
    # Best method is to check for the official TIFF flag
    subft = _GetFieldDefault(tfile, T.TIFFTAG_SUBFILETYPE, 0)
    if subft & T.FILETYPE_REDUCEDIMAGE:
        return True

    return False

def _convertToOMEMD(images):
    """
    Converts DataArray tags to OME-TIFF tags.
    images (list of DataArrays): the images that will be in the TIFF file, in order
    returns (string): the XML data as compatible with OME
    Note: the images will be considered from the same detectors only if they are
      consecutive (and the metadata confirms the detector is the same)
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
    groups, first_ifd = _findImageGroups(images)

    # Detectors
    for i, g in enumerate(groups):
        did = first_ifd[i] # our convention: ID is the first IFD
        da0 = g[0]
        if model.MD_HW_NAME in da0.metadata:
            detect = ET.SubElement(instr, "Detector", attrib={
                                      "ID": "Detector:%d" % did,
                                      "Model": da0.metadata[model.MD_HW_NAME]})

    # Objectives
    for i, g in enumerate(groups):
        oid = first_ifd[i] # our convention: ID is the first IFD
        da0 = g[0]
        if model.MD_LENS_MAG in da0.metadata:
            obj = ET.SubElement(instr, "Objective", attrib={
                      "ID": "Objective:%d" % oid,
                      "CalibratedMagnification": "%f" % da0.metadata[model.MD_LENS_MAG]
                      })
            if model.MD_LENS_NAME in da0.metadata:
                obj.attrib["Model"] = da0.metadata[model.MD_LENS_NAME]

    for i, g in enumerate(groups):
        _addImageElement(root, g, first_ifd[i])

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
    returns :
        groups (list of list of DataArray): a set of "groups", each group is 
         a list which contains the DataArrays 
        first_ifd (list of int): the IFD (index) of the first image of the group 
    """
    # We consider images to be part of the same group if they have:
    # * same shape
    # * metadata that show they were acquired by the same instrument
    groups = []
    first_ifd = []

    ifd = 0
    prev_da = None
    for da in das:
        # check if it can be part of the current group (compare just to the previous DA)
        if (prev_da is None or
            prev_da.shape != da.shape or
            prev_da.metadata.get(model.MD_HW_NAME, None) != da.metadata.get(model.MD_HW_NAME, None) or
            prev_da.metadata.get(model.MD_HW_VERSION, None) != da.metadata.get(model.MD_HW_VERSION, None)
            ):
            # new group
            groups.append([da])
            first_ifd.append(ifd)
        else:
            # same group
            groups[-1].append(da)
        
        # increase ifd by the number of planes (= everything above 2D)
        ifd += numpy.prod(da.shape[:-2])
            
    return groups, first_ifd

def _addImageElement(root, das, ifd):
    """
    Add the metadata of a list of DataArray to a OME-XML root element
    root (Element): the root element
    das (list of DataArray): all the images to describe. Each DataArray
     can have up to 5 dimensions in the order CTZYX. IOW, RGB images have C=3. 
    ifd (int): the IFD of the first DataArray
    Note: the images in das must be added in the final TIFF in the same order
     and contiguously
    """
    assert(len(das) > 0)
    # all image have the same shape?
    assert all([das[0].shape == im.shape for im in das]) 

    idnum = len(root.findall("Image"))
    ime = ET.SubElement(root, "Image", attrib={"ID": "Image:%d" % idnum})

    # compute a common metadata
    globalMD = {}
    for da in das:
        globalMD.update(da.metadata)

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

    # Find a dimension along which the DA can be concatenated. That's a
    # dimension which is of size 1. 
    # For now, if there are many possibilities, we pick the first one.
    da0 = das[0]
    
    dshape = das[0].shape 
    if len(dshape) < 5:
        dshape = [1] * (5-len(dshape)) + list(dshape)
    if not 1 in dshape:
        raise ValueError("No dimension found to concatenate images: %s" % dshape)
    concat_axis = dshape.index(1)
    # global shape (same as dshape, but the axis of concatenation is the number of images)
    gshape = list(dshape)
    gshape[concat_axis] = len(das)
    gshape = tuple(gshape)
    
    # TODO: check that all the DataArrays have the same shape
    pixels = ET.SubElement(ime, "Pixels", attrib={
                              "ID": "Pixels:%d" % idnum,
                              "DimensionOrder": "XYZTC", # we don't have ZT so it doesn't matter
                              "Type": "%s" % da0.dtype, # seems to be compatible in general
                              "SizeX": "%d" % gshape[4], # numpy shape is reversed
                              "SizeY": "%d" % gshape[3],
                              "SizeZ": "%d" % gshape[2],
                              "SizeT": "%d" % gshape[1],
                              "SizeC": "%d" % gshape[0],
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
    for da in das:
        # RGB?
        # Note: it seems officially OME-TIFF doesn't support RGB TIFF (instead,
        # each colour should go in a separate channel). However, that'd defeat
        # the purpose of the thumbnail, and it seems at OMERO handles this
        # not too badly (all the other images get 3 components).
        is_rgb = (len(da.shape) == 5 and da.shape[0] == 3)
        
        if is_rgb or len(da.shape) < 5:
            num_chan = 1
        else:
            num_chan = da.shape[0]
        for c in range(num_chan):
            chan = ET.SubElement(pixels, "Channel", attrib={
                                   "ID": "Channel:%d:%d" % (idnum, subid)})
            if is_rgb:
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
                attrib["Binning"] = "%dx%d" % da.metadata[model.MD_BINNING]
            if model.MD_GAIN in da.metadata:
                attrib["Gain"] = "%f" % da.metadata[model.MD_GAIN]
            if model.MD_READOUT_TIME in da.metadata:
                ror = (1 / da.metadata[model.MD_READOUT_TIME]) / 1e6 #MHz
                attrib["ReadOutRate"] = "%f" % ror
    
            if attrib:
                # detector of the group has the same id as first IFD of the group
                attrib["ID"] = "Detector:%d" % ifd
                ds = ET.SubElement(chan, "DetectorSettings", attrib=attrib)

            subid += 1

    # TiffData Element: describe every single IFD image
    subid = 0
    for index in numpy.ndindex(gshape[:-2]):
        tde = ET.SubElement(pixels, "TiffData", attrib={
                                "IFD": "%d" % (ifd + subid),
                                "FirstZ": "%d" % index[2],
                                "FirstT": "%d" % index[1],
                                "FirstC": "%d" % index[0],
                                "PlaneCount": "1"
                                })
        subid += 1

    # Plane Element
    subid = 0
    for index in numpy.ndindex(gshape[:-2]):
        da = das[index[concat_axis]]
        plane = ET.SubElement(pixels, "Plane", attrib={
                               "TheZ": "%d" % index[2],
                               "TheT": "%d" % index[1],
                               "TheC": "%d" % index[0],
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
    f = TIFF.open(filename, mode='w')

    # According to this page: http://www.openmicroscopy.org/site/support/file-formats/ome-tiff/ome-tiff-data
    # LZW is a good trade-off between compatibility and small size (reduces file
    # size by about 2). => that's why we use it by default
    if compressed:
        compression = "lzw"
    else:
        compression = None

    # OME tags: a XML document in the ImageDescription of the first image
    if thumbnail is not None:
        # OME expects channel as 5th dimension. If thumbnail is RGB as HxWx3,
        # reorganise as 3x1x1xHxW
        if len(thumbnail.shape) == 3:
            OME_thumbnail = numpy.rollaxis(thumbnail, 2)
            OME_thumbnail = OME_thumbnail[:,numpy.newaxis,numpy.newaxis,:,:] #5D
        else:
            OME_thumbnail = thumbnail
        alldata = [OME_thumbnail] + ldata
    else:
        alldata = ldata
    # TODO: reorder the data so that data from the same sensor are together
    ometxt = _convertToOMEMD(alldata)

    if thumbnail is not None:
        # save the thumbnail just as the first image
        # FIXME: Note that this is contrary to the specification which states:
        # "If multiple subfiles are written, the first one must be the
        # full-resolution image." The main problem is that most thumbnailers
        # use the first image as thumbnail. Maybe we should provide our own
        # clever thumbnailer?

        f.SetField(T.TIFFTAG_IMAGEDESCRIPTION, ometxt)
        ometxt = None
        f.SetField(T.TIFFTAG_PAGENAME, "Composited image")
        # Flag for saying it's a thumbnail
        f.SetField(T.TIFFTAG_SUBFILETYPE, T.FILETYPE_REDUCEDIMAGE)

        # FIXME:
        # Pylibtiff has a bug: it thinks that RGB image are organised as
        # 3xHxW, while normally in numpy, it's HxWx3. (cf scipy.imread)
        # So we need to swap the axes
        if len(thumbnail.shape) == 3:
            thumbnail = numpy.rollaxis(thumbnail, 2) # a new view

        # write_rgb makes it clever to detect RGB vs. Greyscale
        f.write_image(thumbnail, compression=compression, write_rgb=True)
        

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
            f.SetField(key, val)

        # TODO: see if we need to set FILETYPE_PAGE + Page number for each image? data?

        if ometxt: # save OME tags if not yet done
            f.SetField(T.TIFFTAG_IMAGEDESCRIPTION, ometxt)
            ometxt = None

        # for data > 2D: write as a sequence of 2D images
        for i in numpy.ndindex(data.shape[:-2]):
            f.write_image(data[i], compression=compression)

def _thumbsFromTIFF(filename):
    """
    Read thumbnails from an TIFF file.
    return (list of model.DataArray)
    """
    f = TIFF.open(filename, mode='r')
    # open each image/page as a separate data
    data = []
    for image in f.iter_images():
        if _isThumbnail(f):
            md = _readTiffTag(f) # reads tag of the current image
            da = model.DataArray(image, metadata=md)
            data.append(da)

    return data

def _dataFromTIFF(filename):
    """
    Read microscopy data from a TIFF file.
    filename (string): path of the file to read
    return (list of model.DataArray)
    """
    f = TIFF.open(filename, mode='r')
    
    # open each image/page as a separate image
    data = []
    for image in f.iter_images():
        # If it's a thumbnail, skip it, but leave the space free to not mess with the IFD number
        if _isThumbnail(f):
            data.append(None)
            continue
        md = _readTiffTag(f) # reads tag of the current image
        da = model.DataArray(image, metadata=md)
        data.append(da)
        
    # If looks like OME TIFF, reconstruct >2D data and add metadata
    # It's OME TIFF, if it has a valid ome-tiff XML in the first T.TIFFTAG_IMAGEDESCRIPTION
    # Warning: we support what we write, not the whole OME-TIFF specification.
    if False:
        return _reconstructFromOMETIFF(xml, data)
    

    # Remove all the None (=thumnails) from the list
    data = [i for i in data if i is not None]
    return data


def export(filename, data, thumbnail=None):
    '''
    Write a TIFF file with the given image and metadata
    filename (string): filename of the file to create (including path)
    data (list of model.DataArray, or model.DataArray): the data to export,
        must be 2D or more of int or float. Metadata is taken directly from the data
        object. If it's a list, a multiple page file is created. The order of the
        dimensions is Channel, Time, Z, Y, X.
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

def read_data(filename):
    """
    Read an TIFF file and return its content (skipping the thumbnail).
    filename (string): filename of the file to read
    return (list of model.DataArray): the data to import (with the metadata 
     as .metadata). It might be empty.
     Warning: reading back a file just exported might give a smaller number of
     DataArrays! This is because export() tries to aggregate data which seems
     to be from the same acquisition but on different dimensions C, T, Z.
     read_data() cannot separate them back explicitly.
    raises:
        IOError in case the file format is not as expected.
    """
    # TODO: support filename to be a File or Stream (but it seems very difficult
    # to do it without looking at the .filename attribute)
    # see http://pytables.github.io/cookbook/inmemory_hdf5_files.html

    return _dataFromTIFF(filename)

def read_thumbnail(filename):
    """
    Read the thumbnail data of a given TIFF file.
    filename (string): filename of the file to read
    return (list of model.DataArray): the thumbnails attached to the file. If 
     the file contains multiple thumbnails, all of them are returned. If it 
     contains none, an empty list is returned.
    raises:
        IOError in case the file format is not as expected.
    """
    # TODO: support filename to be a File or Stream

    return _thumbsFromTIFF(filename)
