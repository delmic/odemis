# -*- coding: utf-8 -*-
'''
Created on 17 Jul 2012

@author: Éric Piel

Copyright © 2012-2020 Éric Piel, Delmic

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
# Don't import unicode_literals to avoid issues with external functions. Code works on python2 and python3.
from past.builtins import basestring
from builtins import range

import calendar
from datetime import datetime
import json
from libtiff import TIFF
import logging
import math
import numpy
from odemis import model, util
import odemis
from odemis.model import DataArrayShadow, AcquisitionData
from odemis.util import spectrum, img, fluo
from odemis.util.conversion import get_tile_md_pos, JsonExtraEncoder
import operator
import os
import re
import sys
import threading
import time
import uuid

import libtiff.libtiff_ctypes as T  # for the constant names
import xml.etree.ElementTree as ET

#pylint: disable=E1101
# Note about libtiff: it's a pretty ugly library, with 2 different wrappers.
# We use only the C wrapper (not the Python implementation).
# Note concerning the image format: it follows the numpy convention. The first
# dimension is the height, and second one is the width. (This is so because
# in memory the height is the slowest changing dimension, so it is first in C
# order.)
# So an RGB image of W horizontal pixels and H vertical pixels is an
# array of shape (H, W, 3). It is recommended to have the image in memory in C
# order (but that should not matter). For raw data, the convention is to have
# 5 dimensions in the order CTZYX.
# PIL and wxPython have images with the size expressed as (width, height), although
# in memory it corresponds to the same representation.
# User-friendly name
FORMAT = "TIFF"
# list of file-name extensions possible, the first one is the default when saving a file
EXTENSIONS = [u".ome.tiff", u".ome.tif", u".tiff", u".tif"]

STIFF_SPLIT = ".0."  # pattern to replace with the "stiff" multiple file

CAN_SAVE_PYRAMID = True # indicates the support for pyramidal export
TILE_SIZE = 256 # Tile size of pyramidal images
LOSSY = False

# We try to make it as much as possible looking like a normal (multi-page) TIFF,
# with as much metadata as possible saved in the known TIFF tags. In addition,
# we ensure it's compatible with OME-TIFF, which support much more metadata, and
# more than 3 dimensions for the data.
# Note that it could be possible to use more TIFF tags, from DNG and LSM for
# example.
# See also MicroManager OME-TIFF format with additional tricks for ImageJ:
# http://micro-manager.org/wiki/Micro-Manager_File_Formats

# TODO: make sure that _all_ the metadata is saved, either in TIFF tags, OME-TIFF,
# or in a separate mechanism.


def _convertToTiffTag(metadata):
    """
    Converts DataArray tags to libtiff tags.
    metadata (dict of tag -> value): the metadata of a DataArray
    returns (dict of tag -> value): the metadata as compatible for libtiff
    """
    # Note on strings: We accept unicode and utf-8 encoded strings, but TIFF
    # only supports ASCII (7-bits) officially. It seems however that utf-8
    # strings are often accepted, so for now we use that. If it turns out to
    # bring too much problem we might need .encode("ascii", "ignore") or
    # unidecode (but they are lossy).

    tiffmd = {T.TIFFTAG_RESOLUTIONUNIT: T.RESUNIT_CENTIMETER,
              T.TIFFTAG_SOFTWARE: (u"%s %s" % (odemis.__shortname__, odemis.__version__)).encode("utf-8", "ignore")}
    # we've got choice between inches and cm... so it's easy
    for key, val in metadata.items():
        if key == model.MD_HW_NAME:
            tiffmd[T.TIFFTAG_MAKE] = val.encode("utf-8", "ignore")
        elif key == model.MD_HW_VERSION:
            v = val
            if model.MD_SW_VERSION in metadata:
                v += u" (driver %s)" % (metadata[model.MD_SW_VERSION],)
            tiffmd[T.TIFFTAG_MODEL] = v.encode("utf-8", "ignore")
        elif key == model.MD_ACQ_DATE:
            tiffmd[T.TIFFTAG_DATETIME] = time.strftime("%Y:%m:%d %H:%M:%S", time.gmtime(val)).encode("utf-8", "ignore")
        elif key == model.MD_PIXEL_SIZE:
            # convert m/px -> px/cm
            # Note: apparently some reader (Word?) try to use this to display
            # the image, which ends up in very very small images.
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
            pos_cm = [100 + v * 100 for v in val]
            tiffmd[T.TIFFTAG_XPOSITION] = max(0, pos_cm[0])
            tiffmd[T.TIFFTAG_YPOSITION] = max(0, pos_cm[1])
            if [tiffmd[T.TIFFTAG_XPOSITION], tiffmd[T.TIFFTAG_YPOSITION]] != pos_cm[:2]:
                logging.warning("Position metadata clipped to avoid negative position %s", pos_cm)

#         elif key == model.MD_ROTATION:
            # TODO: should use the coarse grain rotation to update Orientation
            # and update rotation information to -45< rot < 45 -> maybe GeoTIFF's ModelTransformationTag?
            # or actually rotate the data?
        # TODO MD_BPP : the actual bit size of the detector
        # Use SMINSAMPLEVALUE and SMAXSAMPLEVALUE ?
        # N = SPP in the specification, but libtiff duplicates the values
        elif key == model.MD_DESCRIPTION:
            # We don't use description as it's used for OME-TIFF
            tiffmd[T.TIFFTAG_PAGENAME] = val.encode("utf-8", "ignore")
        # TODO save the brightness and contrast applied by the user?
        # Could use GrayResponseCurve, DotRange, or TransferFunction?
        # TODO save the tint applied by the user? maybe WhitePoint can help
        # TODO save username as "Artist" ? => not gonna fly if the user is "odemis"
        else:
            logging.debug("Metadata tag '%s' skipped when saving TIFF metadata", key)

    return tiffmd


def _GetFieldDefault(tfile, tag, default=None):
    """
    Same as TIFF.GetField(), but if the tag is not defined, return default
    Note: the C libtiff has GetFieldDefaulted() which returns the default value
    of the specification, this function is different.
    tag (int or string): tag id or name
    default (value): value to return if the tag is not defined
    """
    ret = tfile.GetField(tag)
    if ret is None:
        return default
    else:
        return ret

# factor for value -> m
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

    # Set MD_DIMS to "YXC" or "CYX" in case it looks like RGB
    samples_pp = _GetFieldDefault(tfile, T.TIFFTAG_SAMPLESPERPIXEL, 1) # this number includes extra samples
    if samples_pp > 1:
        planar_config = _GetFieldDefault(tfile, T.TIFFTAG_PLANARCONFIG, T.PLANARCONFIG_CONTIG)
        if planar_config == T.PLANARCONFIG_CONTIG:
            md[model.MD_DIMS] = "YXC"
        elif planar_config == T.PLANARCONFIG_SEPARATE:
            md[model.MD_DIMS] = "CYX"

    # scale + position
    resunit = _GetFieldDefault(tfile, T.TIFFTAG_RESOLUTIONUNIT, T.RESUNIT_INCH)
    factor = resunit_to_m.get(resunit, 1) # default to 1

    xres = tfile.GetField(T.TIFFTAG_XRESOLUTION)
    yres = tfile.GetField(T.TIFFTAG_YRESOLUTION)

    if xres is not None and yres is not None:
        try:
            md[model.MD_PIXEL_SIZE] = (factor / xres, factor / yres)
        except ZeroDivisionError:
            pass

    xpos = tfile.GetField(T.TIFFTAG_XPOSITION)
    ypos = tfile.GetField(T.TIFFTAG_YPOSITION)
    if xpos is not None and ypos is not None:
        # -1 m for the shift
        md[model.MD_POS] = (factor * xpos - 1, factor * ypos - 1)

    # informative metadata
    val = tfile.GetField(T.TIFFTAG_PAGENAME)
    if val is not None:
        md[model.MD_DESCRIPTION] = val.decode("utf-8", "ignore")
#     val = tfile.GetField(T.TIFFTAG_SOFTWARE)
#     if val is not None:
#         md[model.MD_SW_VERSION] = val
    val = tfile.GetField(T.TIFFTAG_MAKE)
    if val is not None:
        md[model.MD_HW_NAME] = val.decode("utf-8", "ignore")
    val = tfile.GetField(T.TIFFTAG_MODEL)
    if val is not None:
        md[model.MD_HW_VERSION] = val.decode("utf-8", "ignore")
    val = tfile.GetField(T.TIFFTAG_DATETIME)
    if val is not None:
        try:
            t = calendar.timegm(time.strptime(val.decode("utf-8", "ignore"), "%Y:%m:%d %H:%M:%S"))
            md[model.MD_ACQ_DATE] = t
        except (OverflowError, ValueError):
            logging.info("Failed to parse date '%s'", val)

    return md


def _isThumbnail(tfile):
    """
    Detects whether the current image of a file is a thumbnail or not
    returns (boolean): True if the image is a thumbnail, False otherwise
    """
    # Best method is to check for the official TIFF flag
    subft = _GetFieldDefault(tfile, T.TIFFTAG_SUBFILETYPE, 0)
    if subft & T.FILETYPE_REDUCEDIMAGE:
        return True

    return False


def _guessModelName(das):
    """
    Detect the model of the Delmic microscope from the type of images acquired
    das (list of DataArrays)
    return (String or None): None if unknown, or the name of the model
    """
    # If any image has MD_IN_WL => fluorescence => SECOM
    # If any image has MD_WL_* => spectra => SPARC
    # If any image has MD_AR_POLE => angular resolved => SPARC
    for da in das:
        md = da.metadata
        if model.MD_WL_LIST in md or model.MD_AR_POLE in md:
            return "SPARC"
        elif model.MD_IN_WL in md:
            return "SECOM"

    return None


def _indent(elem, level=0):
    """
    In-place pretty-print formatter
    From http://effbot.org/zone/element-lib.htm#prettyprint
    elem (ElementTree)
    """
    i = u"\n" + level * u"    "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + u"    "
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
        for elem in elem:
            _indent(elem, level + 1)
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i


_ROI_NS = "http://www.openmicroscopy.org/Schemas/ROI/2012-06"


def _convertToOMEMD(images, multiple_files=False, findex=None, fname=None, uuids=None):
    """
    Converts DataArray tags to OME-TIFF tags.
    images (list of DataArrays): the images that will be in the TIFF file, in order
      They should have 5 dimensions in this order: CTZYX, with the exception that
      all the first dimensions of size 1 can be skipped.
    multiple_files (boolean): whether the data is distributed across multiple
      files or not.
    findex (int): index of this particular file.
    fname (str or None): filename if data is distributed in multiple files
    uuids (list of str): list that contains all the file uuids
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
#      + Experiment (*)    # To describe the acquisition settings and type of microscopy
#        + Description     # Free form description of the acquisition
#      + Experimenter      # To describe the user
#      + Instrument (*)    # To describe the acquisition technical details for each
#        + Microscope      # set of emitter/detector.
#        + LightSource (*)
#          . LightSourceID
#          . Power (?)
#          . PowerUnit (?) # default is mW - new in 2015
#        + Detector (*)
#          . DetectorID
#        + Objective (*)
#        + Filter (*)
#      + Image (*)         # To describe a set of images by the same instrument
#        + Description     # Not sure what to put (Image has "Name" attribute) => simple user note?
#        + AcquisitionDate # time of acquisition of the (first) image
#        + ExperimentRef
#        + ExperimenterRef
#        + InstrumentRef
#        + ImagingEnvironment # To describe the physical conditions (temp...)
#        + Transform       # Affine transform (to record the rotation...)
#        + Pixels          # technical dimensions of the images (XYZ, T, C)
#          + Channel (*)   # emitter settings for the given channel (light wavelength)
#            . ExcitationWavelength
#            . EmissionWavelength
#            + DetectorSettings
#              . DetectorID
#              . Binning
#            + LightSourceSettings
#              . LightSourceID
#              . Attenuation (?)
#              . Current   # Extension: EBEAM_CURRENT
#              + Current * # Extension: EBEAM_CURRENT_TIME
#          + Plane (*)     # physical dimensions/position of each images
#          + TiffData (*)  # where to find the data in the tiff file (IFD)
#                          # we explicitly reference each DataArray to avoid
#                          # potential ordering problems of the "Image" elements
#        + ROIRef (*)
#        + ARData ({0, 1})
#        + POLData ({0, 1})
#        + StreakCamData ({0, 1})
#        + ExtraSettings   # To store all hw settings
#      + ROI (*)
#        . ID
#        . Name
#        + Union
#          + Shape (*)
#            . ID
#            . TheC
#            + Point
#                . X
#                . Y

# For fluorescence microscopy, the schema usage is straight-forwards. For SEM
# images, some metadata might be difficult to fit. For spectrum acquisitions
# (i.e., a C11YX cube), we use the channels to encode each spectrum.
# For AR acquisition, CCDX and CCDY are mapped to X/Y, and AR_POLE is recorded
# as an ROI Point with Name "PolePosition" and coordinates in pixel (like AR_POLE).

    # To create and manipulate the XML, we use the Python ElementTree API.

    # Note: pylibtiff has a small OME support, but it's so terrible that we are
    # much better ignoring it completely
    if multiple_files:
        root = ET.Element('OME', attrib={
                "xmlns": "http://www.openmicroscopy.org/Schemas/OME/2012-06",
                "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
                "UUID": "%s" % uuids[findex],
                "xsi:schemaLocation": "http://www.openmicroscopy.org/Schemas/OME/2012-06 http://www.openmicroscopy.org/Schemas/OME/2012-06/ome.xsd",
                })
    else:
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
                                })
    model_name = _guessModelName(images)
    if model_name:
        micro.attrib["Model"] = model_name

    # for each set of images from the same instrument, add them
    # In case of multiple files the ifd of the groups is used just for the
    # hw components enumeration.
    groups = _findImageGroups(images)

    # Detectors, Objectives & LightSource: one per group of data with same metadata
    for ifd, g in groups.items():
        did = ifd # our convention: ID is the first IFD
        da0 = g[0]

        # Add an experiment description to contain MD_HW_NOTE, which is in
        # theory any free-form text about the hardware settings, but in practice
        # it's the list of all the settings for all the hardware involved in
        # that acquisition
        if model.MD_HW_NOTE in da0.metadata:
            experiment = ET.SubElement(root, "Experiment",
                                       attrib={"ID": "Experiment:%d" % did})

            description = ET.SubElement(experiment, "Description")
            description.text = da0.metadata[model.MD_HW_NOTE]

        if model.MD_HW_NAME in da0.metadata:
            obj = ET.SubElement(instr, "Detector", attrib={
                                "ID": "Detector:%d" % did,
                                "Model": da0.metadata[model.MD_HW_NAME]})

        if (model.MD_LIGHT_POWER in da0.metadata or
            model.MD_EBEAM_CURRENT in da0.metadata or
            model.MD_EBEAM_CURRENT_TIME in da0.metadata
           ):
            obj = ET.SubElement(instr, "LightSource",
                                attrib={"ID": "LightSource:%d" % did})
            if model.MD_LIGHT_POWER in da0.metadata:
                pwr = da0.metadata[model.MD_LIGHT_POWER] * 1e3  # in mW
                obj.attrib["Power"] = "%.15f" % pwr

        if model.MD_LENS_MAG in da0.metadata:
            mag = da0.metadata[model.MD_LENS_MAG]
            obj = ET.SubElement(instr, "Objective", attrib={
                                "ID": "Objective:%d" % did,
                                "CalibratedMagnification": "%.15f" % mag})
            if model.MD_LENS_NAME in da0.metadata:
                obj.attrib["Model"] = da0.metadata[model.MD_LENS_NAME]

        # Make sure all string metadata values are unicode strings (not bytes),
        # otherwise serialization will fail later on
        for da in g:
            for key, value in da.metadata.items():
                if isinstance(value, bytes):
                    try:
                        da.metadata[key] = value.decode('utf-8')
                    except:
                        logging.warning("Failed to decode value of metadata '%s'.", key)
                        da.metadata[key] = ''

    # TODO: filters (with TransmittanceRange)

    rois = {} # dict str->ET.Element (ROI ID -> ROI XML element)
    fname_index = 0
    for ifd, g in groups.items():
        if multiple_files:
            # Remove path from filename
            path, bname = os.path.split(fname)
            tokens = bname.rsplit(STIFF_SPLIT, 1)
            part_fname = tokens[0] + "." + str(fname_index) + "." + tokens[1]
            _addImageElement(root, g, ifd, rois, part_fname, uuids[fname_index])
            fname_index += 1
        else:
            _addImageElement(root, g, ifd, rois)

    # ROIs have to come _after_ images, so add them only now
    root.extend(list(rois.values()))

    # TODO add tag to each image with "Odemis", so that we can find them back
    # easily in a database?

    # make it more readable
    _indent(root)
    ometxt = (b'<?xml version="1.0" encoding="UTF-8"?>' +
              ET.tostring(root, encoding="utf-8"))
    return ometxt


def _findElementByID(root, eid, tag=None):
    """
    Find the element with the given ID.
    Note: OME conformant documents cannot have multiple elements with the same
      ID. It is assumed to be correct.
    root (ET.Element): the root element to start the search
    eid (str): the ID to match
    tag (str or None): the tag of the element to match. If None, any element
      with the given ID will be looked for.
    return (ET.Element): the element with .ID=eid, or None
    """
    for el in root.iter(tag):
        if "ID" in el.attrib and el.attrib["ID"] == eid:
            return el

    return None


def _updateMDFromOME(root, das):
    """
    Updates the metadata of DAs according to OME XML
    root (ET.Element): the root (i.e., OME) element of the XML description
    data (list of DataArrays): DataArrays at the same place as the TIFF IFDs
    return None: only the metadata of DA's inside is updated
    """
    # For each Image in the XML, gorge ourself from all the metadata we can
    # find, and then use it to update the metadata of each IFD referenced.
    # In case of multiple files, add an offset to the ifd based on the number of
    # images found in the files that are already accessed
    ifd_offset = 0

    for ime in root.findall("Image"):
        md = {}
        try:
            md[model.MD_DESCRIPTION] = ime.attrib["Name"]
        except KeyError:
            pass

        try:
            md[model.MD_USER_NOTE] = ime.attrib["Description"]
        except KeyError:
            pass

        acq_date = ime.find("AcquisitionDate")
        if acq_date is not None:
            try:
                # the earliest time of all the acquisitions in this image
                val = acq_date.text
                md[model.MD_ACQ_DATE] = calendar.timegm(time.strptime(val, "%Y-%m-%dT%H:%M:%S"))
            except (OverflowError, ValueError):
                pass

        expse = ime.find("ExperimentRef")
        if expse is not None:
            try:
                exp = _findElementByID(root, expse.attrib["ID"], "Experiment")
                exp_des = exp.find("Description")
                md[model.MD_HW_NOTE] = exp_des.text
            except (AttributeError, KeyError, ValueError):
                pass

        detse = ime.find("DetectorSettings")
        try:
            offset = detse.attrib["Offset"]
            md[model.MD_BASELINE] = float(offset)
        except (AttributeError, KeyError, ValueError):
            pass

        objse = ime.find("ObjectiveSettings")
        try:
            obje = _findElementByID(root, objse.attrib["ID"], "Objective")
            mag = obje.attrib["CalibratedMagnification"]
            md[model.MD_LENS_MAG] = float(mag)
        except (AttributeError, KeyError, ValueError):
            pass

        extrase = ime.find("ExtraSettings")
        try:
            md[model.MD_EXTRA_SETTINGS] = json.loads(extrase.text)
        except (AttributeError, KeyError, ValueError):
            pass  
   
        # rotation (and mirroring and translation, but we don't support this)
        trans_mat = ime.find("Transform")
        if trans_mat is not None:
            try:
                # It may include shear
                cossh = float(trans_mat.attrib["A00"])
                sinv = float(trans_mat.attrib["A01"])
                cosv = float(trans_mat.attrib["A11"])
                sinsh = float(trans_mat.attrib["A10"])
                rot = math.atan2(sinv, cosv) % (2 * math.pi)
                scaling_y = math.sqrt(math.pow(sinv, 2) + math.pow(cosv, 2))
                scaling_x = cossh * math.cos(rot) - sinsh * math.sin(rot)
                shear = (cossh * math.sin(rot) + sinsh * math.cos(rot)) / scaling_x
                if not (util.almost_equal(scaling_y, 1) and
                        util.almost_equal(scaling_x, 1)):
                    logging.warning("Image metadata has complex transformation "
                                    "which is not supported by Odemis.")
                md[model.MD_ROTATION] = rot
                if not (util.almost_equal(shear, 0)):
                    md[model.MD_SHEAR] = shear
            except (AttributeError, KeyError, ValueError):
                pass

        pxe = ime.find("Pixels") # there must be only one per Image

        try:
            psx = float(pxe.attrib["PhysicalSizeX"]) * 1e-6  # µm -> m
            psy = float(pxe.attrib["PhysicalSizeY"]) * 1e-6
            md[model.MD_PIXEL_SIZE] = (psx, psy)
            # PIXEL_SIZE has already been updated. If the PhysicalSizeZ is not present
            # the code will stop the metadata will be 2D, and if it's present,
            # the metadata will be changed to 3D.
            psz = float(pxe.attrib["PhysicalSizeZ"]) * 1e-6
            md[model.MD_PIXEL_SIZE] = (psx, psy, psz)
        except (KeyError, ValueError):
            pass

        try:
            md[model.MD_BPP] = int(pxe.attrib["SignificantBits"])
        except (KeyError, ValueError):
            pass

        try:
            dims = pxe.get("DimensionOrder")[::-1]
            md[model.MD_DIMS] = dims
        except (KeyError, ValueError):
            pass

        hd_2_ifd, hdims = _getIFDsFromOME(pxe, offset=ifd_offset)
        ifd_offset += len(hd_2_ifd)

        # Channels are a bit tricky, because apparently they are associated to
        # each C only by the order they are specified.
        wl_list = [] # we'll know it only once all the channels are passed
        for chan, che in enumerate(pxe.findall("Channel")):

            mdc = {}
            try:
                mdc[model.MD_DESCRIPTION] = che.attrib["Name"]
            except KeyError:
                pass

            try:
                # We didn't store the exact bandwidth, but guess it based on the
                # type of microscopy method
                h_width = 1e-9
                if "ContrastMethod" in che.attrib:
                    cm = che.attrib["ContrastMethod"]
                    if cm == "Fluorescence":
                        h_width = 10e-9
                    elif cm == "Brightfield":
                        h_width = 100e-9

                iwl = float(che.attrib["ExcitationWavelength"]) * 1e-9 # nm -> m
                mdc[model.MD_IN_WL] = (iwl - h_width, iwl + h_width)
            except (KeyError, ValueError):
                pass

            try:
                if "EmissionWavelength" in che.attrib:
                    owl = float(che.attrib["EmissionWavelength"]) * 1e-9 # nm -> m
                    if ("AcquisitionMode" in che.attrib) and (che.attrib["AcquisitionMode"] == "SpectralImaging"):
                        # Spectrum => on the whole data cube
                        wl_list.append(owl)
                    else:
                        # Fluorescence
                        mdc[model.MD_OUT_WL] = (owl - 1e-9, owl + 1e-9)
                else:
                    fl = che.find("Filter")
                    if fl is not None:
                        ftype = fl.attrib["Type"]
                        mdc[model.MD_OUT_WL] = ftype

            except (KeyError, ValueError):
                pass

            try:
                hex_str = che.attrib["Color"] # hex string
                hex_str = hex_str[-8:] # almost copy of conversion.hex_to_rgb
                tint = tuple(int(hex_str[i:i + 2], 16) for i in [0, 2, 4, 6])
                mdc[model.MD_USER_TINT] = tint[:3] # only RGB
            except (KeyError, ValueError):
                pass

            # TODO: parse detector

            d_settings = che.find("DetectorSettings")
            if d_settings is not None:
                try:
                    bin_str = d_settings.attrib["Binning"]
                    m = re.match(r"(?P<b1>\d+)\s*x\s*(?P<b2>\d+)", bin_str)
                    mdc[model.MD_BINNING] = (int(m.group("b1")), int(m.group("b2")))
                except KeyError:
                    pass
                try:
                    mdc[model.MD_GAIN] = float(d_settings.attrib["Gain"])
                except (KeyError, ValueError):
                    pass
                try:
                    ror = float(d_settings.attrib["ReadOutRate"]) # MHz
                    mdc[model.MD_READOUT_TIME] = 1e-6 / ror # s
                except (KeyError, ValueError):
                    pass
                try:
                    mdc[model.MD_EBEAM_VOLTAGE] = float(d_settings.attrib["Voltage"])
                except (KeyError, ValueError):
                    pass

            # Get light source info
            ls_settings = che.find("LightSourceSettings")
            if ls_settings is not None:
                try:
                    ls = _findElementByID(root, ls_settings.attrib["ID"], "LightSource")
                    try:
                        pwr = float(ls.attrib["Power"]) * 1e-3  # mW -> W
                        mdc[model.MD_LIGHT_POWER] = pwr
                    except (KeyError, ValueError):
                        pass
                except (KeyError, LookupError):
                    logging.info("LightSourceSettings without LightSource")
                try:
                    cur = float(ls_settings.attrib["Current"])  # A
                    mdc[model.MD_EBEAM_CURRENT] = cur
                except (KeyError, ValueError):
                    pass

                cot = []
                for cure in ls_settings.findall("Current"):
                    try:
                        st = cure.attrib["Time"]
                        dt = datetime.strptime(st, "%Y-%m-%dT%H:%M:%S.%f")
                        # TODO: with Python 3, replace by dt.replace(tzinfo=timezone.utc).timestamp()
                        epoch = datetime(1970, 1, 1)
                        t = (dt - epoch).total_seconds()
                    except (KeyError, ValueError):
                        logging.debug("Skipping current (over time) entry without time")
                        continue
                    try:
                        cur = float(cure.text)
                    except ValueError:
                        logging.debug("Failed to parse current (over time) value")
                    cot.append((t, cur))

                if cot:
                    cot.sort(key=lambda e: e[0])  # Sort by time
                    mdc[model.MD_EBEAM_CURRENT_TIME] = cot

            # update all the IFDs related to this channel
            try:
                ci = hdims.index("C")
                chans = [slice(None)] * len(hdims)
                chans[ci] = chan
                chans = tuple(chans)
            except ValueError:
                if chan > 0:
                    raise ValueError("Multiple channels information but C dimension is low")
                chans = slice(None)  # all of the IFDs

            for ifd in hd_2_ifd[chans].flat:
                if ifd == -1:
                    continue # no IFD known, it's alright, might be just 3D array
                try:
                    da = das[ifd]
                except IndexError:
                    # That typically happens if not all the series of a
                    # serialized TIFF could be opened.
                    logging.warning("IFD %d not present, cannot update its metadata", ifd)
                    continue
                if da is None:
                    continue # might be a thumbnail, it's alright
                # First apply the global MD, then per-channel
                da.metadata.update(md)
                da.metadata.update(mdc)

        nbchan = chan + 1
        # Update metadata of each da, so that they will be merged
        if wl_list:
            if len(wl_list) != nbchan:
                logging.warning("WL_LIST has length %d, while expected %d",
                                len(wl_list), nbchan)
            for ifd in hd_2_ifd.flat:
                if ifd == -1:
                    continue
                try:
                    da = das[ifd]
                except IndexError:
                    logging.warning("IFD %d not present, cannot update its metadata", ifd)
                    continue
                if da is None:
                    continue
                da.metadata.update({model.MD_WL_LIST: wl_list})

        # Plane (= one per high dim -> IFD)
        deltats = {}  # T -> DeltaT
        for ple in pxe.findall("Plane"):
            mdp = {}
            pos = []
            try:
                for d in hdims:
                    ds = int(ple.attrib["The%s" % d]) # required tag
                    pos.append(ds)
            except KeyError:
                logging.warning("Failed to parse Plane element, skipping metadata")
                continue

            try:
                t = int(ple.attrib["TheT"])
                deltats[t] = float(ple.attrib["DeltaT"])  # s  if key exists -> overwritten
                # TODO can we put this code somewhere else, as time_list should be the same for all in c-dim
            except (KeyError, ValueError):
                pass

            try:
                # FIXME: could actually be the dwell time (if scanned)
                mdp[model.MD_EXP_TIME] = float(ple.attrib["ExposureTime"]) # s
            except (KeyError, ValueError):
                pass

            try:
                mdp[model.MD_INTEGRATION_COUNT] = float(ple.attrib["IntegrationCount"])  # s
            except (KeyError, ValueError):
                pass

            try:
                # We assume it's in meters, as we write it (but there is no official unit)
                psx = float(ple.attrib["PositionX"])
                psy = float(ple.attrib["PositionY"])
                mdp[model.MD_POS] = (psx, psy)
                # MD_POS is already updated, but if a Z position is also present,
                # we should add it as well. If not, this part will trigger a KeyError
                psz = float(ple.attrib["PositionZ"])
                mdp[model.MD_POS] = (psx, psy, psz)
            except (KeyError, ValueError):
                pass

            ifd = hd_2_ifd[tuple(pos)]
            if ifd == -1:
                continue # no IFD known, it's alright, might be just 3D array
            try:
                da = das[ifd]
            except IndexError:
                logging.warning("IFD %d not present, cannot update its metadata", ifd)
                continue
            if da is None:
                continue # might be a thumbnail, it's alright
            da.metadata.update(mdp)

        # Update metadata of each da, so that they will be merged
        if deltats:
            time_list = [v for k, v in sorted(deltats.items(), key=lambda item: item[0])]
            if len(time_list) != len(deltats.keys()):
                logging.warning("TIME_LIST has length %d, while expected %d",
                                len(time_list), len(deltats.keys()))
            for ifd in hd_2_ifd.flat:
                if ifd == -1:
                    continue
                try:
                    da = das[ifd]
                except IndexError:
                    logging.warning("IFD %d not present, cannot update its metadata", ifd)
                    continue
                if da is None:
                    continue
                da.metadata.update({model.MD_TIME_LIST: time_list})

        # Mirror data
        md = {}
        ardata = ime.find("ARData")  # there must be only one per Image
        try:
            xma = float(ardata.attrib["XMax"])
            hol = float(ardata.attrib["HoleDiameter"])
            foc = float(ardata.attrib["FocusDistance"])
            par = float(ardata.attrib["ParabolaF"])
            md[model.MD_AR_XMAX] = xma
            md[model.MD_AR_HOLE_DIAMETER] = hol
            md[model.MD_AR_FOCUS_DISTANCE] = foc
            md[model.MD_AR_PARABOLA_F] = par
        except (AttributeError, KeyError, ValueError):
            pass

        try:
            mpta = float(ardata.attrib["MirrorPosTopOffset"])
            mptb = float(ardata.attrib["MirrorPosTopSlope"])
            md[model.MD_AR_MIRROR_TOP] = (mpta, mptb)
            mpba = float(ardata.attrib["MirrorPosBottomOffset"])
            mpbb = float(ardata.attrib["MirrorPosBottomSlope"])
            md[model.MD_AR_MIRROR_BOTTOM] = (mpba, mpbb)
        except (AttributeError, KeyError, ValueError):
            pass

        # polarization analyzer
        poldata = ime.find("POLData")  # there must be only one per Image
        try:
            pol = str(poldata.attrib["Polarization"])
            md[model.MD_POL_MODE] = pol
        except (AttributeError, KeyError, ValueError):
            pass
        try:
            posqwp = float(poldata.attrib["QuarterWavePlate"])
            md[model.MD_POL_POS_QWP] = posqwp
        except (AttributeError, KeyError, ValueError):
            pass
        try:
            poslinpol = float(poldata.attrib["LinearPolarizer"])
            md[model.MD_POL_POS_LINPOL] = poslinpol
        except (AttributeError, KeyError, ValueError):
            pass

        # streak camera
        # TODO shorten code: if timeRange then also the others should be there??
        streakCamData = ime.find("StreakCamData")  # there must be only one per Image
        try:
            timeRange = float(streakCamData.attrib["TimeRange"])
            md[model.MD_STREAK_TIMERANGE] = timeRange
        except (AttributeError, KeyError, ValueError):
            pass
        try:
            MCPGain = int(streakCamData.attrib["MCPGain"])
            md[model.MD_STREAK_MCPGAIN] = MCPGain
        except (AttributeError, KeyError, ValueError):
            pass
        try:
            streakMode = bool(streakCamData.attrib["StreakMode"])
            md[model.MD_STREAK_MODE] = streakMode
        except (AttributeError, KeyError, ValueError):
            pass
        try:
            triggerDelay = float(streakCamData.attrib["TriggerDelay"])
            md[model.MD_TRIGGER_DELAY] = triggerDelay
        except (AttributeError, KeyError, ValueError):
            pass
        try:
            triggerRate = float(streakCamData.attrib["TriggerRate"])
            md[model.MD_TRIGGER_RATE] = triggerRate
        except (AttributeError, KeyError, ValueError):
            pass

        # ROIs (for now we only care about PolePosition)
        for roirfe in ime.findall("ROIRef"):
            try:
                roie = _findElementByID(root, roirfe.attrib["ID"], "ROI")
                unione = roie.find("Union")
                shpe = unione.find("Shape")
                name = roie.attrib["Name"]
            except (AttributeError, KeyError, ValueError):
                continue

            if name == "PolePosition":
                try:
                    pointe = shpe.find("Point")
                    pos = float(pointe.attrib["X"]), float(pointe.attrib["Y"])
                except (AttributeError, KeyError, ValueError):
                    continue
                md[model.MD_AR_POLE] = pos

                # In theory, the shape can specify CTZ, and when not, it's applied to
                # all. Currently we only support (not) specifying C.
                try:
                    chan = int(shpe.attrib["TheC"])
                    ci = hdims.index("C")
                    chans = [slice(None)] * len(hdims)
                    chans[ci] = chan
                    chans = tuple(chans)
                except (KeyError, ValueError):
                    chans = slice(None)  # all

                # update all the IFDs related to this channel
                for ifd in hd_2_ifd[chans].flat:
                    if ifd == -1:
                        continue
                    try:
                        da = das[ifd]
                    except IndexError:
                        logging.warning("IFD %d not present, cannot update its metadata", ifd)
                        continue
                    if da is None:
                        continue
                    # First apply the global MD, then per-channel
                    da.metadata.update(md)

        # TODO make it a separate method (it is called multiple times in this method here...)
        for ifd in hd_2_ifd.flat:
            if ifd == -1:
                continue
            try:
                da = das[ifd]
            except IndexError:
                logging.warning("IFD %d not present, cannot update its metadata", ifd)
                continue
            if da is None:
                continue
            da.metadata.update(md)


def _getIFDsFromOME(pxe, offset=0):
    """
    Return the IFD containing the data with the low dimensions for each high dimension of an array.
    pxe (ElementTree): the element to Pixels of an image
    offset (int): ifd offset based on the number of images found in the previous
        files
    return:
      hdc_2_ifd (numpy.array of int): the value is the IFD number or -1 if not
         specified. Shape is the shape of the high dimensions.
      hdims (str): ordered list of the high dimensions.
    """
    dims = pxe.get("DimensionOrder", "XYZTC")[::-1]

    # Guess how many are they "high" dimensions out of how many IFDs are referenced
    nbifds = 0
    for tfe in pxe.findall("TiffData"):
        # TODO: if no IFD specified, PC should default to all the IFDs
        # (but for now all the files we write have PC=1)
        pc = int(tfe.get("PlaneCount", "1"))
        nbifds += pc

    hdims = ""
    hdshape = []
    for d in dims:
        hdims += d
        ds = int(pxe.get("Size%s" % d, "1"))
        hdshape.append(ds)
        needed_ifds = numpy.prod(hdshape)
        if needed_ifds > nbifds:
            if hdims == "C" and hdshape[0] in (3, 4) and nbifds == 1:
                logging.debug("High dims are %s = %s, while only 1 IFD, guessing it's a RGB image")
                hdshape = [1]
            else:
                logging.warning("High dims are %s = %s, which would require %d IFDs, but only %d seem present",
                                hdims, hdshape, needed_ifds, nbifds)
            break
        ldims = dims.replace(hdims, "")  # low dim = dims - hdims
        if len(ldims) <= 3 and needed_ifds == nbifds:
            # If the next ldim is not XY and =1, consider it to be high dim
            nxtdim = ldims[0]
            nxtshape = int(pxe.get("Size%s" % nxtdim, "1"))
            if nxtdim not in "XY" and nxtshape == 1:
                hdims += nxtdim
                hdshape.append(nxtshape)
            logging.debug("Guessing high dims are %s = %s", hdims, hdshape)
            break
        # More dims in high dims needed
    else:
        logging.warning("All dims concidered high dims (%s = %s), but still not enough to use all %d IFDs referenced",
                        hdims, hdshape, nbifds)

    imsetn = numpy.empty(hdshape, dtype=numpy.int)
    imsetn[:] = -1
    for tfe in pxe.findall("TiffData"):
        # UUID: can indicate data from a different file. For now, we only load
        # data from this specific file.
        # TODO: have an option to either drop these data, or load the other
        # file (if it exists). cf tiff series.
        ifd = int(tfe.get("IFD", "0"))

        # check if it belongs to a different file. In this case add the offset.
        uuide = tfe.find("UUID")  # zero or one
        if uuide is not None:
            ifd += offset

        pos = []
        for d in hdims:
            ds = int(tfe.get("First%s" % d, "0"))
            pos.append(ds)
        # TODO: if no IFD specified, PC should default to all the IFDs
        # (but for now all the files we write have PC=1)
        pc = int(tfe.get("PlaneCount", "1"))

        # If PlaneCount is > 1: it's in the same order as DimensionOrder
        for i in range(pc):
            imsetn[tuple(pos)] = ifd + i
            if i == (pc - 1): # don't compute next position if it's over
                break
            # compute next position (with modulo)
            pos[-1] += 1
            for d in range(len(pos) - 1, -1, -1):
                if pos[d] >= hdshape[d]:
                    pos[d] = 0
                    pos[d - 1] += 1 # will fail if d = 0, on purpose

    return imsetn, hdims

# List of metadata which is allowed to merge (and possibly loose it partially)
WHITELIST_MD_MERGE = frozenset([model.MD_FILTER_NAME,
                                model.MD_HW_NAME, model.MD_HW_VERSION,
                                model.MD_SW_VERSION, model.MD_LENS_NAME,
                                model.MD_SENSOR_TEMP, model.MD_ACQ_DATE])


def _canBeMerged(das):
    """
    Check whether multiple DataArrays can be merged into a larger DA without
      metadata loss.
    Note: this is about merging DataArrays for use in Odemis. For use in
      OME-TIFF, the conditions are different.
    das (list of DataArrays): all the DataArrays
    return (boolean): True if they can be merged, False otherwise.
    """
    if len(das) <= 1:
        return True

    shape = das[0].shape
    md = das[0].metadata.copy()
    for da in das[1:]:
        # shape must be the same
        if shape != da.shape:
            return False
        # all the important metadata must be the same, or not defined
        for mdk in (set(md.keys()) | set(da.metadata.keys())):
            if (mdk not in WHITELIST_MD_MERGE and
                md.get(mdk) != da.metadata.get(mdk)):
                return False

    return True


def _countNeededIFDs(da):
    """
    return the number of IFD (aka TIFF pages, aka planes) needed for storing the given array
    da (DataArray): can have any dimensions, should be ordered ...CTZYX
    return (int > 1)
    """
    # Storred as a sequence of 2D arrays... excepted if it contains RGB images,
    # then we store RGB images (i.e., 3D arrays).
    dims = da.metadata.get(model.MD_DIMS, "CTZYX"[-da.ndim::])
    if len(dims) != da.ndim:
        logging.warning("MD_DIMS %s doesn't fit the data shape %s", dims, da.shape)
        dims = "CTZYX"[-da.ndim::] # fallback to the default

    shaped = {d: s for d, s in zip(dims, da.shape)}
    rep_hdim = [shaped.get(d, 1) for d in "CTZ"]
    # RGB iif C = 3 or 4 and TZ = 1,1
    if rep_hdim[0] in (3, 4) and rep_hdim[1:] == [1, 1]:  # RGB
        rep_hdim[0] = 1
    return numpy.prod(rep_hdim)


def _findImageGroups(das):
    """
    Find groups of images which should be considered part of the same acquisition
    (aka "Image" in OME-XML). Mainly to put optical images at different wavelenghts
    together.
    das (list of DataArray): all the images of the final TIFF file
    returns (dict int -> list of DataArrays):
        IFD (index) of the first DataArray of a group -> "group" of DataArrays
    """
    # FIXME: never do it for pyramidal images for now, as we don't support reading
    # them back?
    # We consider images to be part of the same group if they have:
    # * signs to be an optical image
    # * same shape
    # * metadata that show they were acquired by the same instrument
    groups = dict()
    current_ifd = 0
    prev_da = None

    for da in das:
        # check if it can be part of the current group (compare just to the previous DA)
        if (prev_da is None
            or da.shape[0] != 1  # If C != 1 => not possible to merge (C is always first dimension)
            or (model.MD_IN_WL not in da.metadata or model.MD_OUT_WL not in da.metadata)
            or prev_da.shape != da.shape
            or prev_da.metadata.get(model.MD_HW_NAME, None) != da.metadata.get(model.MD_HW_NAME, None)
            or prev_da.metadata.get(model.MD_HW_VERSION, None) != da.metadata.get(model.MD_HW_VERSION, None)
            or prev_da.metadata.get(model.MD_PIXEL_SIZE) != da.metadata.get(model.MD_PIXEL_SIZE)
            or prev_da.metadata.get(model.MD_LIGHT_POWER) != da.metadata.get(model.MD_LIGHT_POWER)
            or prev_da.metadata.get(model.MD_POS) != da.metadata.get(model.MD_POS)
            or prev_da.metadata.get(model.MD_ROTATION, 0) != da.metadata.get(model.MD_ROTATION, 0)
            or prev_da.metadata.get(model.MD_SHEAR, 0) != da.metadata.get(model.MD_SHEAR, 0)
           ):
            # new group
            group_ifd = current_ifd
        groups.setdefault(group_ifd, []).append(da)

        # increase ifd by the number of planes
        current_ifd += _countNeededIFDs(da)
        prev_da = da

    return groups


def _dtype2OMEtype(dtype):
    """
    Converts a numpy dtype to a OME type
    dtype (numpy.dtype)
    returns (string): OME type
    """
    # OME type is one of : int8, int16, int32, uint8, uint16, uint32, float,
    # bit, double, complex, double-complex
    # Meaning is not explicit, but probably the same as TIFF/C:
    # Float is 4 bytes, while double is 8 bytes
    # So complex is 8 bytes, and double complex == 16 bytes
    if dtype.kind in "ui":
        # int and uint seems to be compatible in general
        return "%s" % dtype
    elif dtype.kind == "f":
        if dtype.itemsize <= 4:
            return "float"
        else:
            return "double"
    elif dtype.kind == "c":
        if dtype.itemsize <= 4:
            return "complex"
        else:
            return "double-complex"
    elif dtype.kind == "b":
        return "bit"
    else:
        raise NotImplementedError("Data type %s is not supported by OME" % dtype)


def _addImageElement(root, das, ifd, rois, fname=None, fuuid=None):
    """
    Add the metadata of a list of DataArray to a OME-XML root element
    root (Element): the root element
    das (list of DataArray): all the images to describe. Each DataArray
     can have up to 5 dimensions in the order CTZYX. IOW, RGB images have C=3.
    ifd (int): the IFD of the first DataArray
    rois (dict str -> ET element): all ROIs added so, and will be updated as
      needed.
    fname (str or None): filename if data is distributed in multiple files
    fuuid (str or None): uuid if data is distributed in multiple files
    Note: the images in das must be added in the final TIFF in the same order
     and contiguously
    """
    assert(len(das) > 0)
    # all image have the same shape?
    assert all(das[0].shape == im.shape for im in das)

    idnum = len(root.findall("Image"))
    ime = ET.SubElement(root, "Image", attrib={"ID": "Image:%d" % idnum})

    # compute a common metadata
    globalMD = {}
    for da in das:
        globalMD.update(da.metadata)

    globalAD = None
    if model.MD_ACQ_DATE in globalMD:
        # Need to find the earliest time
        globalAD = min(d.metadata[model.MD_ACQ_DATE]
                        for d in das if model.MD_ACQ_DATE in d.metadata)
        ad = ET.SubElement(ime, "AcquisitionDate")
        ad.text = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(globalAD))

    # add ExperimentRef as image element
    if model.MD_HW_NOTE in globalMD:
        expse = ET.SubElement(ime, "ExperimentRef",
                              attrib={"ID": "Experiment:%d" % idnum})

    # find out about the common attribute (Name)
    if model.MD_DESCRIPTION in globalMD:
        ime.attrib["Name"] = globalMD[model.MD_DESCRIPTION]

    # find out about the common sub-elements (time, user note, shape)
    if model.MD_USER_NOTE in globalMD:
        desc = ET.SubElement(ime, "Description")
        desc.text = globalMD[model.MD_USER_NOTE]

    if model.MD_LENS_MAG in globalMD:
        # add ref to Objective
        ose = ET.SubElement(ime, "ObjectiveSettings",
                            attrib={"ID": "Objective:%d" % ifd})

    if model.MD_BASELINE in globalMD:
        # add ref to Objective
        dse = ET.SubElement(ime, "DetectorSettings",
                            attrib={"ID": "Detector:%d" % ifd,
                                    "Offset": "%.15f" % globalMD[model.MD_BASELINE]})

    if model.MD_ROTATION in globalMD or model.MD_SHEAR in globalMD:
        # globalMD.get(model.MD_ROTATION, 0)
        rot = globalMD.get(model.MD_ROTATION, 0)
        sinr, cosr = math.sin(rot), math.cos(rot)
        she = globalMD.get(model.MD_SHEAR, 0)
        # Note: Transform was suggested in 2013 (and mistakenly shown as official
        # for a short while) but it's just our extention.
        # It was suggested to use a special key/value pair in MapAnnotation instead.
        trane = ET.SubElement(ime, "Transform")
        trans_mat = [[cosr + sinr * she, sinr, 0],
                     [-sinr + cosr * she, cosr, 0]]
        for i in range(2):
            for j in range(3):
                trane.attrib["A%d%d" % (i, j)] = "%.15f" % trans_mat[i][j]

    if model.MD_EXTRA_SETTINGS in globalMD:
        sett = globalMD[model.MD_EXTRA_SETTINGS]
        extrase = ET.SubElement(ime, "ExtraSettings")
        try:
            extrase.text = json.dumps(sett, cls=JsonExtraEncoder)  # serialize hw settings
        except Exception as ex:
            logging.error("Failed to save ExtraSettings metadata, exception %s" % ex)
            extrase.text = ''
    # Find a dimension along which the DA can be concatenated. That's a
    # dimension which is of size 1.
    # For now, if there are many possibilities, we pick the first one.
    da0 = das[0]
    dshape = da0.shape
    dims = globalMD.get(model.MD_DIMS, "CTZYX"[-len(dshape)::])

    if len(dshape) < 5:
        dshape = [1] * (5 - len(dshape)) + list(dshape)
    while len(dims) < 5:
        # Extend the dimension names with the missing ones in the default order
        # ex: YXC -> TZYXC
        for d in "XYZTC":
            if d not in dims:
                dims = d + dims
                break

    if 1 not in dshape:
        raise ValueError("No dimension found to concatenate images: %s" % dshape)
    concat_axis = dshape.index(1)

    # global shape (same as dshape, but the axis of concatenation is the number of images)
    gshape = list(dshape)
    gshape[concat_axis] = len(das)
    gshape = tuple(gshape)

    # Note: it seems officially OME-TIFF doesn't support RGB TIFF (instead,
    # each colour should go in a separate channel). However, that'd defeat
    # the purpose of the thumbnail, and it seems at OMERO handles this
    # not too badly (all the other images get 3 components).
    is_rgb = (len(das) == 1 and dshape[dims.index("C")] in (3, 4))

    # TODO: check that all the DataArrays have the same shape
    pixels = ET.SubElement(ime, "Pixels", attrib={
                              "ID": "Pixels:%d" % idnum,
                              "DimensionOrder": dims[::-1],  # numpy shape is reversed
                              "Type": "%s" % _dtype2OMEtype(da0.dtype),
                              "SizeX": "%d" % gshape[dims.index("X")],
                              "SizeY": "%d" % gshape[dims.index("Y")],
                              "SizeZ": "%d" % gshape[dims.index("Z")],
                              "SizeT": "%d" % gshape[dims.index("T")],
                              "SizeC": "%d" % gshape[dims.index("C")],
                              })
    # Add optional values
    if model.MD_PIXEL_SIZE in globalMD:
        pxs = globalMD[model.MD_PIXEL_SIZE]
        pixels.attrib["PhysicalSizeX"] = "%.15f" % (pxs[0] * 1e6) # in µm
        pixels.attrib["PhysicalSizeY"] = "%.15f" % (pxs[1] * 1e6)
        if len(pxs) == 3:
            pixels.attrib["PhysicalSizeZ"] = "%.15f" % (pxs[2] * 1e6)

    # Note: TimeIncrement can be used to replace DeltaT if the duration is always
    # the same (which it is), but it also means it starts at 0, which is not
    # always the case with TIME_OFFSET. => use DeltaT

    if model.MD_BPP in globalMD:
        bpp = globalMD[model.MD_BPP]
        pixels.attrib["SignificantBits"] = "%d" % bpp # in bits

    # For each DataArray, add a Channel, TiffData, and Plane, but be careful
    # because they all have to be grouped and in this order.

    # For spectrum images, 1 channel per wavelength. As suggested on the
    # OME-devel mailing list, we use EmissionWavelength to store it.
    # http://trac.openmicroscopy.org.uk/ome/ticket/7355 mentions spectrum can
    # be stored as a fake Filter with the CutIn/CutOut wavelengths, but that's
    # not so pretty.
    wl_list = None
    if model.MD_WL_LIST in globalMD:
        try:
            wl_list = spectrum.get_wavelength_per_pixel(da0)
        except Exception:
            logging.warning("Spectrum metadata is insufficient to be saved")

    time_list = None
    if model.MD_TIME_LIST in globalMD:
        try:
            time_list = spectrum.get_time_per_pixel(da0)
        except Exception:
            logging.warning("Temporal spectrum metadata is insufficient to be saved")

    subid = 0
    for da in das:
        if is_rgb or len(da.shape) < 5:
            num_chan = 1
        else:
            num_chan = da.shape[0]
        for c in range(num_chan):
            chan = ET.SubElement(pixels, "Channel", attrib={
                                   "ID": "Channel:%d:%d" % (idnum, subid)})
            if is_rgb:
                chan.attrib["SamplesPerPixel"] = "%d" % dshape[0]

            # Name can be different for each channel in case of fluroescence
            if model.MD_DESCRIPTION in da.metadata:
                chan.attrib["Name"] = da.metadata[model.MD_DESCRIPTION]

            # TODO Fluor attrib for the dye?
            # TODO create a Filter with the cut range?
            if model.MD_IN_WL in da.metadata:
                iwl = da.metadata[model.MD_IN_WL]
                xwl = fluo.get_one_center(iwl) * 1e9  # in nm
                chan.attrib["ExcitationWavelength"] = "%d" % round(xwl)

                # if input wavelength range is small, it means we are in epifluoresence
                if abs(iwl[-1] - iwl[0]) < 100e-9:
                    chan.attrib["IlluminationType"] = "Epifluorescence"
                    chan.attrib["AcquisitionMode"] = "WideField"
                    chan.attrib["ContrastMethod"] = "Fluorescence"
                else:
                    chan.attrib["IlluminationType"] = "Epifluorescence"
                    chan.attrib["AcquisitionMode"] = "WideField"
                    chan.attrib["ContrastMethod"] = "Brightfield"

            if model.MD_OUT_WL in da.metadata:
                owl = da.metadata[model.MD_OUT_WL]
                if isinstance(owl, basestring):
                    filter = ET.SubElement(chan, "Filter", attrib={
                                    "ID": "Filter:%d:%d" % (idnum, subid)})
                    filter.attrib["Type"] = owl
                elif model.MD_IN_WL in da.metadata:
                    # Use excitation wavelength in case of multiple bands
                    iwl = da.metadata[model.MD_IN_WL]
                    ewl = fluo.get_one_center_em(owl, iwl) * 1e9  # in nm
                    chan.attrib["EmissionWavelength"] = "%d" % round(ewl)
                else:
                    ewl = fluo.get_one_center(owl) * 1e9  # in nm
                    chan.attrib["EmissionWavelength"] = "%d" % round(ewl)

            if wl_list is not None and len(wl_list) > 0:
                if "EmissionWavelength" in chan.attrib:
                    logging.warning("DataArray contains both OUT_WL (%s) and "
                                    "incompatible WL_LIST metadata",
                                    chan.attrib["EmissionWavelength"])
                else:
                    chan.attrib["AcquisitionMode"] = "SpectralImaging"
                    # It should be an int, but that looses too much precision
                    # TODO: in 2015 schema, it's now PositiveFloat
                    chan.attrib["EmissionWavelength"] = "%.15f" % (wl_list[c] * 1e9)

            if model.MD_USER_TINT in da.metadata:
                # user tint is 3 tuple int or a string
                # colour is hex RGBA (eg: #FFFFFFFF)
                tint = da.metadata[model.MD_USER_TINT]
                if isinstance(tint, tuple):
                    if len(tint) == 3:
                        tint = tuple(tint) + (255,)  # need alpha channel
                    hex_str = "".join("%.2x" % c for c in tint)  # copy of conversion.rgb_to_hex()
                    chan.attrib["Color"] = "#%s" % hex_str

            # Add info on detector
            attrib = {}
            if model.MD_BINNING in da.metadata:
                attrib["Binning"] = "%dx%d" % da.metadata[model.MD_BINNING]
            if model.MD_GAIN in da.metadata:
                attrib["Gain"] = "%.15f" % da.metadata[model.MD_GAIN]
            if model.MD_READOUT_TIME in da.metadata:
                ror = (1 / da.metadata[model.MD_READOUT_TIME]) / 1e6 # MHz
                attrib["ReadOutRate"] = "%.15f" % ror
            if model.MD_EBEAM_VOLTAGE in da.metadata:
                # Schema only mentions PMT, but we use it for the e-beam too
                attrib["Voltage"] = "%.15f" % da.metadata[model.MD_EBEAM_VOLTAGE] # V

            if attrib:
                # detector of the group has the same id as first IFD of the group
                attrib["ID"] = "Detector:%d" % ifd
                ds = ET.SubElement(chan, "DetectorSettings", attrib=attrib)

            # Add info on the light source: same structure as Detector
            attrib = {}
            if model.MD_EBEAM_CURRENT in da.metadata:
                attrib["Current"] = "%.15f" % da.metadata[model.MD_EBEAM_CURRENT]  # A

            cot = da.metadata.get(model.MD_EBEAM_CURRENT_TIME)
            if attrib or cot or model.MD_LIGHT_POWER in da.metadata:
                attrib["ID"] = "LightSource:%d" % ifd
                ds = ET.SubElement(chan, "LightSourceSettings", attrib=attrib)
                # This is a non-standard metadata, it defines the emitter, which
                # OME considers to always be a LightSource (although in this case it's
                # an e-beam). To associate time -> current, we use a series of elements
                # "Current" with an attribute date (same format as AcquisitionDate),
                # and the current in A as the value.
                if cot:
                    for t, cur in cot:
                        st = datetime.utcfromtimestamp(t).strftime("%Y-%m-%dT%H:%M:%S.%f")
                        cote = ET.SubElement(ds, "Current", attrib={"Time": st})
                        cote.text = "%.18f" % cur

            subid += 1

    # TiffData Element: describe every single IFD image (ie, XY plane) in the
    # same order as the data will be written: higher dims iterated the numpy style,
    # TODO: could be more compact for DAs of dim > 2, with PlaneCount = first dim > 1?
    subid = 0
    hdims = ""
    rep_hdim = []
    for d, s in zip(dims, gshape):
        if d not in "XY":
            hdims += d
            if is_rgb and d == "C":
                s = 1
            rep_hdim.append(s)

    for index in numpy.ndindex(*rep_hdim):
        if fname is not None:
            tde = ET.SubElement(pixels, "TiffData", attrib={
                        # Since we have multiple files ifd is 0
                        "IFD": "%d" % subid,
                        "FirstC": "%d" % index[hdims.index("C")],
                        "FirstT": "%d" % index[hdims.index("T")],
                        "FirstZ": "%d" % index[hdims.index("Z")],
                        "PlaneCount": "1"
                        })
            f_name = ET.SubElement(tde, "UUID", attrib={
                                    "FileName": "%s" % fname})
            f_name.text = fuuid
        else:
            tde = ET.SubElement(pixels, "TiffData", attrib={
                                    "IFD": "%d" % (ifd + subid),
                                    "FirstC": "%d" % index[hdims.index("C")],
                                    "FirstT": "%d" % index[hdims.index("T")],
                                    "FirstZ": "%d" % index[hdims.index("Z")],
                                    "PlaneCount": "1"
                                    })
        subid += 1

    # Plane Element
    subid = 0
    for index in numpy.ndindex(*rep_hdim):
        da = das[index[concat_axis]]
        plane = ET.SubElement(pixels, "Plane", attrib={
                               "TheC": "%d" % index[hdims.index("C")],
                               "TheT": "%d" % index[hdims.index("T")],
                               "TheZ": "%d" % index[hdims.index("Z")],
                               })
        # Note: we used to store ACQ_DATE also in this attribute (in addition to
        # AcquisitionDate) in order to save the different acquisition date for
        # each C. However, that's not the point of this field, and it's pretty
        # much never useful to know the slightly different acquisition dates for
        # each C.
        # We now just store TIME_OFFSET + PIXEL_DUR info
        # TODO in future only use TIME_LIST
        if model.MD_PIXEL_DUR in da.metadata:
            t = index[hdims.index("T")]
            deltat = da.metadata.get(model.MD_TIME_OFFSET) + da.metadata[model.MD_PIXEL_DUR] * t
            plane.attrib["DeltaT"] = "%.15f" % deltat
        if time_list is not None:
            plane.attrib["DeltaT"] = "%.15f" % time_list[index[1]]

        if model.MD_EXP_TIME in da.metadata:
            exp = da.metadata[model.MD_EXP_TIME]
            plane.attrib["ExposureTime"] = "%.15f" % exp
        elif model.MD_DWELL_TIME in da.metadata:
            # save it as is (it's the time each pixel receives "energy")
            exp = da.metadata[model.MD_DWELL_TIME]
            plane.attrib["ExposureTime"] = "%.15f" % exp

        # integration count: number of samples/images per px (ebeam) position
        if model.MD_INTEGRATION_COUNT in da.metadata:
            exp = da.metadata[model.MD_INTEGRATION_COUNT]
            plane.attrib["IntegrationCount"] = "%d" % exp

        # Note that Position has no official unit, which prevents Tiling to be
        # usable. In one OME-TIFF official example of tiles, they use pixels
        # (and ModuloAlongT "tile")
        if model.MD_POS in da.metadata:
            pos = da.metadata[model.MD_POS]
            plane.attrib["PositionX"] = "%.15f" % pos[0] # any unit is allowed => m
            plane.attrib["PositionY"] = "%.15f" % pos[1]

            if len(pos) == 3:
                plane.attrib["PositionZ"] = "%.15f" % pos[2]

        subid += 1

    # ROIs (= ROIRefs + new ROI elements)
    # For now, we use them only to store AR_POLE metadata
    for chan, da in enumerate(das):
        # Note: we assume the
        if model.MD_AR_POLE in da.metadata:
            rid = _createPointROI(rois, "PolePosition",
                                  da.metadata[model.MD_AR_POLE],
                                  shp_attrib={"TheC": "%d" % chan})
            ET.SubElement(ime, "ROIRef", attrib={"xmlns": _ROI_NS, "ID": rid})

    # Store mirror data if any
    if any(rd in globalMD for rd in [model.MD_AR_XMAX,
                                     model.MD_AR_HOLE_DIAMETER,
                                     model.MD_AR_FOCUS_DISTANCE,
                                     model.MD_AR_PARABOLA_F,
                                     model.MD_AR_MIRROR_TOP,
                                     model.MD_AR_MIRROR_BOTTOM
                                     ]):

        ardata = ET.SubElement(ime, "ARData")
        if model.MD_AR_XMAX in globalMD:
            ardata.attrib["XMax"] = "%.15f" % globalMD[model.MD_AR_XMAX]
        if model.MD_AR_HOLE_DIAMETER in globalMD:
            ardata.attrib["HoleDiameter"] = "%.15f" % globalMD[model.MD_AR_HOLE_DIAMETER]
        if model.MD_AR_FOCUS_DISTANCE in globalMD:
            ardata.attrib["FocusDistance"] = "%.15f" % globalMD[model.MD_AR_FOCUS_DISTANCE]
        if model.MD_AR_PARABOLA_F in globalMD:
            ardata.attrib["ParabolaF"] = "%.15f" % globalMD[model.MD_AR_PARABOLA_F]

        if model.MD_AR_MIRROR_TOP in globalMD:
            mt = globalMD[model.MD_AR_MIRROR_TOP]
            ardata.attrib["MirrorPosTopOffset"] = "%.15f" % mt[0]
            ardata.attrib["MirrorPosTopSlope"] = "%.15f" % mt[1]
        if model.MD_AR_MIRROR_BOTTOM in globalMD:
            mb = globalMD[model.MD_AR_MIRROR_BOTTOM]
            ardata.attrib["MirrorPosBottomOffset"] = "%.15f" % mb[0]
            ardata.attrib["MirrorPosBottomSlope"] = "%.15f" % mb[1]

    # Store polarization analyzer MD data if any
    if any(rd in globalMD for rd in [model.MD_POL_MODE,
                                     model.MD_POL_POS_QWP,
                                     model.MD_POL_POS_LINPOL]):

        poldata = ET.SubElement(ime, "POLData")
        if model.MD_POL_MODE in globalMD:
            poldata.attrib["Polarization"] = "%s" % globalMD[model.MD_POL_MODE]
        if model.MD_POL_POS_QWP in globalMD:
            poldata.attrib["QuarterWavePlate"] = "%.15f" % globalMD[model.MD_POL_POS_QWP]
        if model.MD_POL_POS_LINPOL in globalMD:
            poldata.attrib["LinearPolarizer"] = "%.15f" % globalMD[model.MD_POL_POS_LINPOL]

    # Store streak camera MD data if any
    if any(rd in globalMD for rd in [model.MD_STREAK_TIMERANGE,
                                     model.MD_STREAK_MCPGAIN,
                                     model.MD_STREAK_MODE,
                                     model.MD_TRIGGER_DELAY,
                                     model.MD_TRIGGER_RATE]):

        streakCamData = ET.SubElement(ime, "StreakCamData")
        if model.MD_STREAK_TIMERANGE in globalMD:
            streakCamData.attrib["TimeRange"] = "%.9f" % globalMD[model.MD_STREAK_TIMERANGE]
        if model.MD_STREAK_MCPGAIN in globalMD:
            streakCamData.attrib["MCPGain"] = "%d" % globalMD[model.MD_STREAK_MCPGAIN]
        if model.MD_STREAK_MODE in globalMD:
            streakCamData.attrib["StreakMode"] = "%s" % globalMD[model.MD_STREAK_MODE]
        if model.MD_TRIGGER_DELAY in globalMD:
            streakCamData.attrib["TriggerDelay"] = "%.12f" % globalMD[model.MD_TRIGGER_DELAY]
        if model.MD_TRIGGER_RATE in globalMD:
            streakCamData.attrib["TriggerRate"] = "%f" % globalMD[model.MD_TRIGGER_RATE]


def _createPointROI(rois, name, p, shp_attrib=None):
    """
    Create a new Point ROI XML element and add it to the dict
    rois (dict str-> ET.Element): list of all current ROIs
    name (str or None): name of the ROI (if None -> no name)
    p (tuple of 2 floats): values to put in X and Y (arbitrary)
    shp_attrib (dict or None): attributes for the shape element
    return id (str): ROI ID for referencing
    """
    shp_attrib = shp_attrib or {}
    # Find an ID not yet used
    for n in range(len(rois), -1, -1):
        rid = "ROI:%d" % n
        if not rid in rois: # very likely
            shapeid = "Shape:%d" % n # for now, assume 1 shape <-> 1 ROI
            break
    else:
        raise IndexError("Couldn't find an available ID")

    # Create ROI/Union/Shape/Point
    roie = ET.Element("ROI", attrib={"xmlns": _ROI_NS, "ID": rid})
    if name is not None:
        roie.attrib["Name"] = name
    unione = ET.SubElement(roie, "Union")
    shapee = ET.SubElement(unione, "Shape", attrib={"ID": shapeid})
    shapee.attrib.update(shp_attrib)
    pointe = ET.SubElement(shapee, "Point", attrib={"X": "%.15f" % p[0],
                                                    "Y": "%.15f" % p[1]})

    # Add the element to all the ROIs
    rois[rid] = roie
    return rid


def _mergeCorrectionMetadata(da):
    """
    Create a new DataArray with metadata updated to with the correction metadata
    merged.
    da (DataArray): the original data
    return (DataArray): new DataArray (view) with the updated metadata
    """
    md = da.metadata.copy() # to avoid modifying the original one
    img.mergeMetadata(md)
    return model.DataArray(da, md) # create a view


def _saveAsMultiTiffLT(filename, ldata, thumbnail, compressed=True, multiple_files=False,
                       file_index=None, uuid_list=None, pyramid=False):
    """
    Saves a list of DataArray as a multiple-page TIFF file.
    filename (string): name of the file to save
    ldata (list of DataArray): list of 2D data of int or float. Should have at least one array
    thumbnail (None or DataArray): see export
    compressed (boolean): whether the file is LZW compressed or not.
    multiple_files (boolean): whether the data is distributed across multiple
      files or not.
    file_index (int): index of this particular file.
    uuid_list (list of str): list that contains all the file uuids
    pyramid (boolean): whether the file should be saved in the pyramid format or not.
      In this format, each image is saved along with different zoom levels
    """
    if multiple_files:
        # Add index
        tokens = filename.rsplit(STIFF_SPLIT, 1)
        if len(tokens) < 2:
            raise ValueError("The filename '%s' doesn't contain '%s'." % (filename, STIFF_SPLIT))
        orig_filename = tokens[0] + "." + str(file_index) + "." + tokens[1]
        f = TIFF.open(orig_filename, mode='w')
    else:
        f = TIFF.open(filename, mode='w')

    # According to this page: http://www.openmicroscopy.org/site/support/file-formats/ome-tiff/ome-tiff-data
    # LZW is a good trade-off between compatibility and small size (reduces file
    # size by about 2). => that's why we use it by default
    if compressed:
        compression = "lzw"
    else:
        compression = None

    # merge correction metadata (as we cannot save them separatly in OME-TIFF)
    ldata = [_mergeCorrectionMetadata(da) for da in ldata]

    # OME tags: a XML document in the ImageDescription of the first image
    if thumbnail is not None:
        thumbnail = _mergeCorrectionMetadata(thumbnail)
        # If thumbnail is 3 dims, but doesn't defined MD_DIMS, don't concider
        # it as (CT)ZYX, but an RGB as either CYX or YXC.
        if model.MD_DIMS not in thumbnail.metadata and len(thumbnail.shape) == 3:
            if thumbnail.shape[0] in (3, 4):
                dims = "CYX"
            else:
                dims = "YXC" # The most likely actually
            thumbnail.metadata[model.MD_DIMS] = dims
            logging.debug("Add MD_DIMS = %s to thumbnail metadata", thumbnail.metadata[model.MD_DIMS])
        alldata = [thumbnail] + ldata
    else:
        alldata = ldata

    # TODO: reorder the data so that data from the same sensor are together
    ometxt = _convertToOMEMD(alldata, multiple_files, findex=file_index, fname=filename, uuids=uuid_list)

    if thumbnail is not None:
        # save the thumbnail just as the first image
        # FIXME: Note that this is contrary to the specification which states:
        # "If multiple subfiles are written, the first one must be the
        # full-resolution image." The main problem is that most thumbnailers
        # use the first image as thumbnail. Maybe we should provide our own
        # clever thumbnailer?
        f.SetField(T.TIFFTAG_IMAGEDESCRIPTION, ometxt)
        ometxt = None
        f.SetField(T.TIFFTAG_PAGENAME, b"Composited image")
        # Flag for saying it's a thumbnail
        f.SetField(T.TIFFTAG_SUBFILETYPE, T.FILETYPE_REDUCEDIMAGE)

        # Warning, upstream Pylibtiff has a bug: it can only write RGB images are
        # organised as 3xHxW, while normally in numpy, it's HxWx3.
        # Our version is fixed

        # write_rgb makes it clever to detect RGB vs. Greyscale
        f.write_image(thumbnail, compression=compression, write_rgb=True)


        # TODO also save it as thumbnail of the image (in limited size)
        # see  http://www.libtiff.org/man/thumbnail.1.html

        # libtiff.py doesn't support yet SubIFD's so it's not going to fly


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

    if multiple_files:
        groups = _findImageGroups(alldata)
        sorted_x = sorted(groups.items(), key=operator.itemgetter(0))
        # Only get the corresponding data for this file
        ldata = sorted_x[file_index][1]

    # TODO: to keep the code simple, we should just first convert the DAs into
    # 2D or 3D DAs and put it in an dict original DA -> DAs
    for data in ldata:
        # TODO: see if we need to set FILETYPE_PAGE + Page number for each image? data?
        tags = _convertToTiffTag(data.metadata)
        if ometxt: # save OME tags if not yet done
            f.SetField(T.TIFFTAG_IMAGEDESCRIPTION, ometxt)
            ometxt = None

        # if metadata indicates YXC format just handle it as RGB
        if data.metadata.get(model.MD_DIMS) == 'YXC' and data.shape[-1] in (3, 4):
            write_rgb = True
            hdim = data.shape[:-3]
        # TODO: handle RGB for C at any position before and after XY, but iif TZ=11
        # for data > 2D: write as a sequence of 2D images or RGB images
        elif data.ndim == 5 and data.shape[0] == 3:  # RGB
            # Write an RGB image, instead of 3 images along C
            write_rgb = True
            hdim = data.shape[1:3]
            data = numpy.rollaxis(data, 0, -2) # move C axis near YX
        else:
            write_rgb = False
            hdim = data.shape[:-2]

        for i in numpy.ndindex(*hdim):
            # Save metadata (before the image)
            for key, val in tags.items():
                try:
                    f.SetField(key, val)
                except Exception:
                    logging.exception("Failed to store tag %s with value '%s'", key, val)
            if data[i].dtype in [numpy.int64, numpy.uint64]:
                c = None # libtiff doesn't support compression on these types
            else:
                c = compression
            write_image(f, data[i], write_rgb=write_rgb, compression=c, pyramid=pyramid)


def _genResizedShapes(data):
    """
    Generates a list of tuples with the size of the resized images
    data (DataArray): The original image
    return (list of tuples): List of the tuples with the size of the resized images
    """
    # initializes the first shape with the shape of the input DataArray
    shape = data.shape
    dims = data.metadata.get(model.MD_DIMS, "CTZYX"[-data.ndim:])

    resized_shapes = []
    z = 0
    while shape[dims.index("X")] >= TILE_SIZE and shape[dims.index("Y")] >= TILE_SIZE:
        z += 1
        # Calculate the shape of the ith resampled image
        # Copy the dimensions other than X and Y from the input DataArray shape
        shape = tuple(s // 2**z if d in "XY" else s for s,d in zip(data.shape, dims))
        resized_shapes.append(shape)

    return resized_shapes


def write_image(f, arr, compression=None, write_rgb=False, pyramid=False):
    """
    f (libtiff file handle): Handle of a TIFF file
    arr (DataArray): DataArray to be written to the file
    compression (boolean): Compression type to be used on the TIFF file
    write_rgb (boolean): True if the image is RGB, False if the image is grayscale
    pyramid (boolean): whether the file should be saved in the pyramid format or not.
      In this format, each image is saved along with different zoom levels
    """
    # if not pyramid, just save the image in the TIFF file, and return
    if not pyramid:
        f.write_image(arr, compression=compression, write_rgb=write_rgb)
        return

    # TODO: for pyramidal images, we should follow the OME-TIFF 6 format
    # https://docs.openmicroscopy.org/ome-model/6.0.1/ome-tiff/specification.html#sub-resolutions
    # (It should be very similar to the current implementation)

    # generate the sizes of the zoom levels to be generated and saved
    resized_shapes = _genResizedShapes(arr)

    # do not write the SUBIFD tag when there are no subimages
    if len(resized_shapes) > 0:
        # LibTIFF will automatically write the next N directories as subdirectories
        # when this tag is present.
        f.SetField(T.TIFFTAG_SUBIFD, [0] * len(resized_shapes))

    # write the original image
    f.write_tiles(arr, TILE_SIZE, TILE_SIZE, compression, write_rgb)
    # generate the rescaled images and write the tiled image
    for resized_shape in resized_shapes:
        # rescale the image
        subim = img.rescale_hq(arr, resized_shape)

        # Before writting the actual data, we set the special metadata
        f.SetField(T.TIFFTAG_SUBFILETYPE, T.FILETYPE_REDUCEDIMAGE)
        # write the tiled image to the TIFF file
        f.write_tiles(subim, TILE_SIZE, TILE_SIZE, compression, write_rgb)


def export(filename, data, thumbnail=None, compressed=True, multiple_files=False, pyramid=False):
    '''
    Write a TIFF file with the given image and metadata
    filename (unicode): filename of the file to create (including path)
    data (list of model.DataArray, or model.DataArray): the data to export.
       Metadata is taken directly from the DA object. If it's a list, a multiple
       page file is created. It must have 5 dimensions in this order: Channel,
       Time, Z, Y, X. However, all the first dimensions of size 1 can be omitted
       (ex: an array of 111YX can be given just as YX, but RGB images are 311YX,
       so must always be 5 dimensions).
    thumbnail (None or numpy.array): Image used as thumbnail
      for the file. Can be of any (reasonable) size. Must be either 2D array
      (greyscale) or 3D with last dimension of length 3 (RGB). If the exporter
      doesn't support it, it will be dropped silently.
    compressed (boolean): whether the file is compressed or not.
    multiple_files (boolean): whether the data is distributed across multiple
      files or not.
    '''
    if isinstance(data, list):
        if multiple_files:
            if thumbnail is not None:
                logging.warning("Thumbnail is not supported for multiple files "
                                "export and thus it is discarded.")
            nfiles = len(_findImageGroups(data))
            # Create the whole list of uuid's to pass it to each file
            uuid_list = []
            for i in range(nfiles):
                uuid_list.append(uuid.uuid4().urn)
            for i in range(nfiles):
                # TODO: Take care of thumbnails
                _saveAsMultiTiffLT(filename, data, None, compressed,
                                   multiple_files, i, uuid_list, pyramid)
        else:
            _saveAsMultiTiffLT(filename, data, thumbnail, compressed, pyramid=pyramid)
    else:
        # TODO should probably not enforce it: respect duck typing
        assert(isinstance(data, model.DataArray))
        _saveAsMultiTiffLT(filename, [data], thumbnail, compressed, pyramid=pyramid)


def read_data(filename):
    """
    Read an TIFF file and return its content (skipping the thumbnail).
    filename (unicode): filename of the file to read
    return (list of model.DataArray): the data to import (with the metadata
     as .metadata). It might be empty.
     Warning: reading back a file just exported might give a smaller number of
     DataArrays! This is because export() tries to aggregate data which seems
     to be from the same acquisition but on different dimensions C, T, Z.
     read_data() cannot separate them back explicitly.
    raises:
        IOError in case the file format is not as expected.
    """
    acd = open_data(filename)
    return [acd.content[n].getData() for n in range(len(acd.content))]


def read_thumbnail(filename):
    """
    Read the thumbnail data of a given TIFF file.
    filename (unicode): filename of the file to read
    return (list of model.DataArray): the thumbnails attached to the file. If
     the file contains multiple thumbnails, all of them are returned. If it
     contains none, an empty list is returned.
    raises:
        IOError in case the file format is not as expected.
    """
    acd = open_data(filename)
    return [acd.thumbnails[n].getData() for n in range(len(acd.thumbnails))]


def open_data(filename):
    """
    Opens a TIFF file, and return an AcquisitionData instance
    filename (string): path to the file
    return (AcquisitionData): an opened file
    """
    # TODO: support filename to be a File or Stream (but it seems very difficult
    # to do it without looking at the .filename attribute)
    # see http://pytables.github.io/cookbook/inmemory_hdf5_files.html
    return AcquisitionDataTIFF(filename)


class DataArrayShadowTIFF(DataArrayShadow):
    """
    This class implements the read of a TIFF file
    It has all the useful attributes of a DataArray and the actual data.
    """

    def __new__(cls, tiff_info, *args, **kwargs):
        """
        Returns an instance of DataArrayShadowTIFF or DataArrayShadowPyramidalTIFF,
        depending if the image is pyramidal or not.
        """
        if isinstance(tiff_info, list):
            tiff_handle = tiff_info[0]['handle']
        else:
            tiff_handle = tiff_info['handle']
        num_tcols = tiff_handle.GetField(T.TIFFTAG_TILEWIDTH)
        num_trows = tiff_handle.GetField(T.TIFFTAG_TILELENGTH)
        if num_tcols and num_trows:
            subcls = DataArrayShadowPyramidalTIFF
        else:
            subcls = DataArrayShadowTIFF
        return super(DataArrayShadowTIFF, cls).__new__(subcls)

    def __init__(self, tiff_info, shape, dtype, metadata=None):
        """
        Constructor
        tiff_info (dictionary or list of dictionaries): Information about the source tiff file
            and directory from which the image should be read. It can be a dictionary or
            a list of dictionaries. It is a list of dictionaries when
            the DataArray has multiple pixelData
            The dictionary (or each dictionary in the list) has 2 values:
            'tiff_file' (handle): Handle of the tiff file
            'dir_index' (int): Index of the directory
        shape (tuple of int): The shape of the corresponding DataArray
        dtype (numpy.dtype): The data type
        metadata (dict str->val): The metadata
        """
        self.tiff_info = tiff_info

        DataArrayShadow.__init__(self, shape, dtype, metadata)

    def getData(self):
        """
        Fetches the whole data (at full resolution) of image.
        return DataArray: the data, with its metadata
        """
        # if tiff_info is a list, it means that self.content[n]
        # is a DataArrayShadow with multiple images
        if isinstance(self.tiff_info, list):
            return self._readAndMergeImages()
        else:
            image = self._readImage(self.tiff_info)
            return model.DataArray(image, metadata=self.metadata.copy())

    def _readImage(self, tiff_info):
        """
        Reads the image of a given directory
        tiff_info (dictionary): Information about the source tiff file and directory from which
            the image should be read. It has 2 values:
            'tiff_file' (handle): Handle of the tiff file
            'dir_index' (int): Index of the directory
            'lock' (threading.Lock): The lock that controls the access to the TIFF file
        return (numpy.array): The image
        """
        with tiff_info['lock']:
            tiff_info['handle'].SetDirectory(tiff_info['dir_index'])
            image = tiff_info['handle'].read_image()
        return image

    def _readAndMergeImages(self):
        """
        Read the images from file, and merge them into a higher dimension DataArray.
        return (DataArray): the merge of all the DAs. The shape is hdim_index.shape
            + shape of original DataArrayShadow. The metadata is the metadata of the first
            DataArrayShadow of the list
        """
        imset = numpy.empty(self.shape, self.dtype)
        for tiff_info_item in self.tiff_info:
            image = self._readImage(tiff_info_item)
            imset[tiff_info_item['hdim_index']] = image

        return model.DataArray(imset, metadata=self.metadata)


class DataArrayShadowPyramidalTIFF(DataArrayShadowTIFF):
    """
    This class implements the read of a TIFF file
    It has all the useful attributes of a DataArray and the actual data. It also implements
    the reading of a pyramidal TIFF file. IOW, reading subdirectories and tiles.
    """

    def __init__(self, tiff_info, shape, dtype, metadata=None):
        """
        Constructor
        tiff_info (dictionary or list of dictionaries): Information about the source tiff file
            and directory from which the image should be read. It can be a dictionary or
            a list of dictionaries. It is a list of dictionaries when
            the DataArray has multiple pixelData
            The dictionary (or each dictionary in the list) has 2 values:
            'tiff_file' (handle): Handle of the tiff file
            'dir_index' (int): Index of the directory
            'lock' (threading.Lock): The lock that controls the access to the TIFF file
        shape (tuple of int): The shape of the corresponding DataArray
        dtype (numpy.dtype): The data type
        metadata (dict str->val): The metadata
        """
        self.tiff_info = tiff_info
        if isinstance(tiff_info, list):
            tiff_info0 = tiff_info[0]
        else:
            tiff_info0 = tiff_info
        tiff_file = tiff_info0['handle']

        num_tcols = tiff_file.GetField(T.TIFFTAG_TILEWIDTH)
        num_trows = tiff_file.GetField(T.TIFFTAG_TILELENGTH)
        if num_tcols is None or num_trows is None:
            raise ValueError("The image is not tiled")

        with tiff_info0['lock']:
            tiff_file.SetDirectory(tiff_info0['dir_index'])
            sub_ifds = tiff_file.GetField(T.TIFFTAG_SUBIFD)

        # add the number of subdirectories, and the main image
        if sub_ifds:
            maxzoom = len(sub_ifds)
        else:
            maxzoom = 0

        tile_shape = (num_tcols, num_trows)

        DataArrayShadow.__init__(self, shape, dtype, metadata, maxzoom, tile_shape)

    def getTile(self, x, y, zoom):
        '''
        Fetches one tile
        x (0<=int): X index of the tile.
        y (0<=int): Y index of the tile
        zoom (0<=int): zoom level to use. The total shape of the image is shape / 2**zoom.
            The number of tiles available in an image is ceil((shape//zoom)/tile_shape)
        return (DataArray): the shape of the DataArray is typically of shape
        '''
        # get information about how to retrieve the actual pixels from the TIFF file
        tiff_info = self.tiff_info
        if isinstance(tiff_info, list):
            # TODO Implement the reading of the subdata when tiff_info is a list.
            # It is the case when the DataArray has multiple pixelData (eg, when data has more than 2D).
            raise NotImplementedError("DataArray has multiple pixelData")

        with tiff_info['lock']:
            tiff_file = tiff_info['handle']
            tiff_file.SetDirectory(tiff_info['dir_index'])

            if zoom != 0:
                # get an array of offsets, one for each subimage
                sub_ifds = tiff_file.GetField(T.TIFFTAG_SUBIFD)
                if not sub_ifds:
                    raise ValueError("Image does not have zoom levels")

                if not (0 <= zoom <= len(sub_ifds)):
                    raise ValueError("Invalid Z value %d" % (zoom,))

                # set the offset of the subimage. Z=0 is the main image
                tiff_file.SetSubDirectory(sub_ifds[zoom - 1])

            orig_pixel_size = self.metadata.get(model.MD_PIXEL_SIZE, (1, 1))

            # calculate the pixel size of the tile for the zoom level
            tile_pixel_size = tuple(ps * 2 ** zoom for ps in orig_pixel_size)

            xp = x * self.tile_shape[0]
            yp = y * self.tile_shape[1]
            tile = tiff_file.read_one_tile(xp, yp)
            tile = model.DataArray(tile, self.metadata.copy())
            tile.metadata[model.MD_PIXEL_SIZE] = tile_pixel_size
            # calculate the center of the tile
            tile.metadata[model.MD_POS] = get_tile_md_pos((x, y), self.tile_shape, tile, self)

        return tile


class AcquisitionDataTIFF(AcquisitionData):
    """
    Implements AcquisitionData for TIFF files
    """
    def __init__(self, filename):
        """
        Constructor
        filename (string): The name of the TIFF file
        """
        # lock to avoid race conditions when accessing the TIFF file (as libtiff
        # uses multiple calls to access a specific IFD/tile + tag.
        self._lock = threading.Lock()
        tiff_file = TIFF.open(filename, mode='r')
        try:
            data, thumbnails = self._getAllOMEDataArrayShadows(filename, tiff_file)
        except ValueError as ex:
            logging.info("Failed to use the OME data (%s), will use standard TIFF",
                         ex)
            data, thumbnails = self._getAllDataArrayShadows(tiff_file, self._lock)

        # In case we open a basic TIFF file not generated by Odemis, this is a
        # very common "corner case": only one image, and no metadata. At least,
        # let's name it the same as the filename.
        if len(data) == 1 and model.MD_DESCRIPTION not in data[0].metadata:
            data[0].metadata[model.MD_DESCRIPTION] = os.path.splitext(os.path.basename(filename))[0]

        AcquisitionData.__init__(self, tuple(data), tuple(thumbnails))

    def _getAllDataArrayShadows(self, tfile, lock):
        """
        Create the all DataArrayShadows for the given TIFF file
        tfile (tiff handle): Handle for the TIFF file
        lock (threading.Lock): The lock that controls the access to the TIFF file
        return:
            data (list of DataArrayShadows or None): DataArrayShadows
               for each IFD representing a proper image. None are inserted for
               IFDs which don't correspond to data (ie, it's a thumbnail)
            thumbnails (list of DataArrayShadows): DataArrayShadows for all the
               thumbnails images found in the file
        """
        data = []
        thumbnails = []
        # iterates all the directories of the TIFF file
        for dir_index in self._iterDirectories(tfile):
            das, is_thumb = self._createDataArrayShadows(tfile, dir_index, lock)
            if is_thumb:
                data.append(None)
                thumbnails.append(das)
            else:
                data.append(das)

        return data, thumbnails

    def _getAllOMEDataArrayShadows(self, filename, tfile):
        """
        Create the all DataArrayShadows for the given TIFF file and use the OME
          information to find data from other files and to fill the metadata.
        filename (str): the name of the TIFF file
        tfile (tiff handle): Handle for the TIFF file
        return:
            data (list of DataArrayShadows or None): DataArrayShadows
               for each IFD representing a proper image. None are inserted for
               IFDs which don't correspond to data (ie, it's a thumbnail)
            thumbnails (list of DataArrayShadows): DataArrayShadows for all the
               thumbnails images found in the file
        raise ValueError:
            If the OME metadata is not present
        """
        # If looks like OME TIFF, reconstruct >2D data and add metadata
        # Warning: we support what we write, not the whole OME-TIFF specification.
        try:
            omeroot = self._getOMEXML(tfile)
        except LookupError as ex:
            raise ValueError("%s" % (ex,))

        # TODO: flip the whole procedure on its head: based on the OME metadata,
        # load the right metadata. If that doesn't go fine, just fallback to
        # reading the data from the TIFF.
        data, thumbnails = [], []
        try:
            # take care of multiple file distribution
            # Keep track of the UUID/files that were already opened
            uuids_read = {}  # str -> str: UUID -> file path
            for tiff_data in omeroot.findall("Image/Pixels/TiffData"):
                uuide = tiff_data.find("UUID")
                if uuide is None:
                    # uuid attribute is only part of multiple files distribution
                    continue

                try:
                    u = uuid.UUID(uuide.text)
                    ofn = uuide.get("FileName")
                except (ValueError, KeyError) as ex:
                    logging.warning("Failed to decode UUID %s: %s", uuide.text, ex)
                    continue

                if u in uuids_read:
                    continue  # Already done

                try:
                    sfn, stfile = self._findFileByUUID(u, ofn, filename)
                except LookupError:
                    logging.warning("File '%s' enlisted in the OME-XML header is missing.", u)
                    # To keep the metadata update synchronised, we need to
                    # put a place-holder.
                    # TODO: instead of guessing the "IFD" based on the order
                    # in the Pixels, we should use the IFD + UUID of each
                    # TiffData and map it to the file/IFD.
                    data.append(None)
                    continue

                # TODO: we could have a separate lock per file?
                d, t = self._getAllDataArrayShadows(stfile, self._lock)
                data.extend(d)
                thumbnails.extend(t)
                uuids_read[u] = sfn

            if not data:
                # Nothing loading (not even the current file) => load this file
                data, thumbnails = self._getAllDataArrayShadows(tfile, self._lock)

            _updateMDFromOME(omeroot, data)
            data = AcquisitionDataTIFF._foldArrayShadowsFromOME(omeroot, data)
        except Exception:
            logging.exception("Failed to decode OME XML")
            raise ValueError("Failure during OME XML decoding")

        # Remove all the None (=thumbnails) from the list
        data = [i for i in data if i is not None]
        return data, thumbnails

    def _findFileByUUID(self, suuid, orig_fn, root_fn):
        """
        Find the file with the given UUID. In addition to immediately
        looking for the orig_fn, it _may_ look at other files which could
        have the UUID.
        suuid (str): UUID of the file searched for
        orig_fn (str): most probable name of the file (just the basename
          is fine)
        root_fn (str): path to the file where UUID reference was found,
            should contain the whole path
        return filename (str): the whole path of the file found
               tfile (tiff_file): opened file
        raise LookupError:
            if no file could be found
        """
        path, root_bn = os.path.split(root_fn)
        _, orig_bn = os.path.split(orig_fn)

        def try_filename(fn):
            # try to find and open the enlisted file
            try:
                tfile = TIFF.open(fn, mode='r')
            except TypeError:
                # No file found
                raise LookupError("File not found")

            try:
                omeroot = self._getOMEXML(tfile)
                fuuid = uuid.UUID(omeroot.attrib["UUID"])
            except (LookupError, KeyError, ValueError):
                logging.info("Found file %s, but couldn't read UUID", fn)
                raise LookupError("File has not UUID")

            if fuuid != suuid:
                logging.warning("Found file %s, but UUID is %s instead of %s",
                                fn, fuuid, suuid)
            return full_fn, tfile

        # Look in the same directory as the root file
        full_fn = os.path.join(path, orig_bn)
        try:
            return try_filename(full_fn)
        except LookupError:
            pass

        # In case the root file has been renamed, let's try to rename the
        # file we are looking for in the same way.
        # IOW, get the XXXXX.n.ome.tiff from root and use the n from orig.
        m_root = re.search(r"(?P<b>.*)(?P<n>\.\d+)(?P<ext>(\.ome)?(\.\w+)?)$", root_bn)
        m_orig = re.search(r"(?P<b>.*)(?P<n>\.\d+)(?P<ext>(\.ome)?(\.\w+)?)$", orig_bn)
        if m_root and m_orig:
            try_bn = m_root.groupdict()["b"] + m_orig.groupdict()["n"] + m_root.groupdict()["ext"]
            if try_bn != orig_bn:
                full_fn = os.path.join(path, try_bn)
                try:
                    return try_filename(full_fn)
                except LookupError:
                    pass

        raise LookupError("Failed to find file with UUID %s" % (suuid,))

    def _getOMEXML(self, tfile):
        """
        return (xml.Element): the OME XML root in the given file
        raise LookupError: if no OME XML text found
        """
        # It's OME TIFF, if it has a valid ome-tiff XML in the first T.TIFFTAG_IMAGEDESCRIPTION
        tfile.SetDirectory(0)
        desc = tfile.GetField(T.TIFFTAG_IMAGEDESCRIPTION)

        if (desc and ((desc.startswith(b"<?xml") and b"<ome " in desc.lower()) or
                      desc[:4].lower() == b'<ome')):
            try:
                desc = re.sub(b'xmlns="http://www.openmicroscopy.org/Schemas/OME/....-.."',
                              b"", desc, count=1)
                desc = re.sub(b'xmlns="http://www.openmicroscopy.org/Schemas/ROI/....-.."',
                              b"", desc)
                root = ET.fromstring(desc)
                if root.tag.lower() == "ome":
                    return root
                raise LookupError("XML data is not OME: %s" % (desc,))
            except ET.ParseError as ex:
                raise LookupError("OME XML couldn't be parsed: %s" % (ex,))

        raise LookupError("No OME XML data found")

    @staticmethod
    def _createDataArrayShadows(tfile, dir_index, lock):
        """
        Create the DataArrayShadow from the TIFF metadata for the current directory
        tfile (tiff handle): Handle for the TIFF file
        dir_index (int): Index of the directory in the TIFF file
        lock (threading.Lock): The lock that controls the access to the TIFF file
        return:
            das (DataArrayShadows): DataArrayShadows representing the image
            is_thumbnail (bool): True if the image is a thumbnail
        """
        bits = tfile.GetField(T.TIFFTAG_BITSPERSAMPLE)
        sample_format = tfile.GetField(T.TIFFTAG_SAMPLEFORMAT)
        typ = tfile.get_numpy_type(bits, sample_format)

        width = tfile.GetField(T.TIFFTAG_IMAGEWIDTH)
        height = tfile.GetField(T.TIFFTAG_IMAGELENGTH)
        samples_pp = tfile.GetField(T.TIFFTAG_SAMPLESPERPIXEL)
        if samples_pp is None:  # default is 1
            samples_pp = 1

        md = _readTiffTag(tfile)  # reads tag of the current image

        shape = (height, width)
        if samples_pp > 1:
            shape = shape + (samples_pp,)

        # add handle and directory information to be used when the actual
        # pixels of the image are read
        # This information is temporary. It is not needed outside the AcquisitionDataTIFF class,
        # and it is not a part of DataArrayShadow class
        # It can also be a a list of tiff_info,
        # in case the DataArray has multiple pixelData (eg, when data has more than 2D).
        # Add also the lock of the TIFF file
        tiff_info = {'handle': tfile, 'dir_index': dir_index, 'lock': lock}
        das = DataArrayShadowTIFF(tiff_info, shape, typ, md)

        return das, _isThumbnail(tfile)

    @staticmethod
    def _foldArrayShadowsFromOME(root, das):
        """
        Reorganize DataArrayShadows with more than 2 dimensions according to OME XML
        Note: it expects _updateMDFromOME has been run before and so each array
        has its metadata filled up.
        Note: Officially OME supports only base arrays of 2D. But we also support
        base arrays of 3D if the data is RGB (3rd dimension has length 3).
        root (ET.Element): the root (i.e., OME) element of the XML description
        das (list of DataArrayShadows): DataArrayShadows at the same place as the TIFF IFDs
        return (list of DataArrayShadows): new shorter list of DASs positions
        """
        omedas = []

        n = 0 # just for logging
        # In case of multiple files, add an offset to the ifd based on the number of
        # images found in the files that are already accessed
        ifd_offset = 0
        for ime in root.findall("Image"):
            n += 1
            pxe = ime.find("Pixels") # there must be only one per Image

            # The relation between channel and planes is not very clear. Each channel
            # can have multiple SamplesPerPixel, apparently to indicate they have
            # multiple planes. However, the Interleaved attribute is global for
            # Pixels, and seems to imply that RGB data could be saved as a whole,
            # although OME-TIFF normally only has 2D arrays.
            # So far the understanding is Channel refers to the "Logical channels",
            # and Plane refers to the C dimension.
    #        spp = int(pxe.get("Channel/SamplesPerPixel", "1"))

            imsetn, hdims = _getIFDsFromOME(pxe, offset=ifd_offset)
            ifd_offset += len(imsetn)
            # For now we expect RGB as (SPP=3,) SizeC=3, PlaneCount=1, and 1 3D IFD,
            # or as (SPP=3,) SizeC=3, PlaneCount=3 and 3 2D IFDs.

            fifd = imsetn.flat[0]
            if fifd == -1:
                logging.debug("Skipping metadata update for image %d", n)
                continue

            # Check if the IFDs are 2D or 3D, based on the first one
            try:
                fim = das[fifd]
            except IndexError:
                logging.warning("IFD %d not present, cannot update its metadata", fifd)
                continue
            if fim is None:
                continue # thumbnail

            # Handle if the IFD data is 3D. Officially OME-TIFF expects all the data
            # in IFDs to be 2D, but we allow to have RGB images too (so dimension C).
            dims = pxe.get("DimensionOrder", "XYZTC")[::-1]
            if fim.ndim == 3:
                planedims = dims.replace("TZ", "")  # remove TZ
                ci = planedims.index("C")
                if fim.shape[ci] > 1:
                    if "C" in hdims:
                        logging.warning("TiffData %d seems RGB but hdims (%s) contains C too",
                                        fifd, hdims)
                    if imsetn.ndim >= 3:
                        logging.error("_getIFDsFromOME reported %d high dims, but TiffData has shape %s",
                                      imsetn.ndim, fim.shape)

            # Short-circuit for dataset with only one IFD
            if all(d == 1 for d in imsetn.shape):
                omedas.append(fim)
                continue

            if -1 in imsetn:
                raise ValueError("Not all IFDs defined for image %d" % (len(omedas) + 1,))

            # TODO: Position might also be different. Probably should be grouped
            # by position too, as Odemis doesn't support such case.

            # In Odemis, arrays can be merged along C _only_ if they are continuous
            # (like for a spectrum acquisition). If it's like fluorescence, with
            # complex channel metadata, they need to be kept separated.
            # Check for all the C with T=0, Z=0 (should be the same at other indices)
            try:
                ci = hdims.index("C")
                chans = [0] * len(hdims)
                chans[ci] = slice(None)
                chans = tuple(chans)
            except ValueError:
                chans = slice(None)  # all of the IFDs
            das_tz0n = list(imsetn[chans])
            das_tz0 = [das[i] for i in das_tz0n]
            if not _canBeMerged(das_tz0):
                for sub_imsetn in imsetn:
                    # Combine all the IFDs into a (1+)4D array
                    sub_imsetn.shape = (1,) + sub_imsetn.shape
                    shadow_shape = AcquisitionDataTIFF._mergeDAShadowsShape(das, sub_imsetn)
                    omedas.append(shadow_shape)
            else:
                # Combine all the IFDs into a 5D array
                shadow_shape = AcquisitionDataTIFF._mergeDAShadowsShape(das, imsetn)
                omedas.append(shadow_shape)

        # Updating MD_DIMS to remove too many dims if the array is no 5 dims
        for da in omedas:
            try:
                dims = da.metadata[model.MD_DIMS]
                da.metadata[model.MD_DIMS] = dims[-da.ndim:]
            except KeyError:
                pass

        return omedas

    @staticmethod
    def _mergeDAShadowsShape(das, hdim_index):
        """
        Merge multiple DataArrayShadows into a higher dimension DataArrayShadow.
        das (list of DataArrays): ordered list of DataArrayShadows (can contain more
            arrays than what is used in the high dimension arrays
        hdim_index (ndarray of int >= 0): an array representing the higher
            dimensions of the final merged arrays. Each value is the index of the
            small array in das.
        return (DataArrayShadow): the merge of all the DAs. The shape is hdim_index.shape
            + shape of original DataArrayShadow. The metadata is the metadata of the first
            DataArrayShadow inserted
        """
        fim = das[hdim_index.flat[0]]
        tshape = hdim_index.shape + fim.shape
        # it will hold the list of the information about each of the merged
        # DataArrayShadow instance
        tiff_info_list = []
        for hi, i in numpy.ndenumerate(hdim_index):
            # add the index of the DataArrayShadow in the merged DataArrayShadow
            das[i].tiff_info['hdim_index'] = hi
            tiff_info_list.append(das[i].tiff_info)

        if len(tiff_info_list) == 1:
            # Optimisation: if there is actually only one (because it's split
            # over C), make it a simple DAS.
            # That's especially useful for now, as Pyramidal DAS don't support
            # high dimensions yet.
            tiff_info_list = tiff_info_list[0]
            del tiff_info_list['hdim_index']
            tshape = fim.shape

        # add the information about each of the merged DataArrayShadows
        mergedDataArrayShadow = DataArrayShadowTIFF(tiff_info_list, tshape, fim.dtype, fim.metadata)
        return mergedDataArrayShadow

    @staticmethod
    def _iterDirectories(tiff_file):
        """
        Iterate on the directories of a tiff file
        tiff_file (tiff handle): The tiff file handle to be iterated on
        return (int): The index of the directory
        """
        tiff_file.SetDirectory(0)
        dir_index = 0
        yield dir_index
        while not tiff_file.LastDirectory():
            tiff_file.ReadDirectory()
            dir_index += 1
            yield dir_index
