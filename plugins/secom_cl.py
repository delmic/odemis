# -*- coding: utf-8 -*-
"""
Created on 26 Jun 2013, updated June 2019.

@author: Éric Piel, Lennard Voortman, Sabrina Rossberger

This is an Odemis plugin to acquire a set of images from the CCD from various
e-beam spots on the sample along a grid.

The files are saved in TIFF, with the y, x positions of the ebeam (in nm) in the name,
the total number of ebeam positions in x and y, the physical distance between
positions in x and y, and the type of the acquisition.

================================================================================
This is free and unencumbered software released into the public domain.

Anyone is free to copy, modify, publish, use, compile, sell, or
distribute this software, either in source code form or as a compiled
binary, for any purpose, commercial or non-commercial, and by any
means.

In jurisdictions that recognize copyright laws, the author or authors
of this software dedicate any and all copyright interest in the
software to the public domain. We make this dedication for the benefit
of the public at large and to the detriment of our heirs and
successors. We intend this dedication to be an overt act of
relinquishment in perpetuity of all present and future rights to this
software under copyright law.

The software is provided "as is", without warranty of any kind,
express or implied, including but not limited to the warranties of
merchantability, fitness for a particular purpose and non-infringement.
In no event shall the authors be liable for any claim, damages or
other liability, whether in an action of contract, tort or otherwise,
arising from, out of or in connection with the software or the use or
other dealings in the software.
"""

from collections import OrderedDict
from concurrent.futures._base import CancelledError
import copy
import logging
import math
import numpy
from odemis import dataio, model, util, gui
from odemis.acq import leech, acqmng
from odemis.dataio import tiff
from odemis.gui.comp.overlay.world import RepetitionSelectOverlay
from odemis.gui.conf import get_acqui_conf
from odemis.gui.conf import util as cutil
from odemis.gui.model import TOOL_ROA, TOOL_RO_ANCHOR, TOOL_NONE
from odemis.gui.plugin import Plugin, AcquisitionDialog
from odemis.gui.util import call_in_wx_main, formats_to_wildcards
from odemis.util import img
from odemis.util.filename import guess_pattern, create_filename, update_counter
import os.path

import odemis.acq.stream as acqstream


# Exposure time of the AR CCD
EXP_TIME = 1  # s
# Binning for the AR CCD
BINNING = (1, 1)  # px, px

# file format
FMT = "TIFF"
# Filename format
FN_FMT = u"%(prefix)s_grid=%(xres)dx%(yres)d_stepsize=%(xstepsize)dx%(ystepsize)dnm_n=%(xpos)dx%(ypos)d_%(type)s.tiff"


def get_ccd_md(ccd):
    """
    Returns the Metadata associated with the optical detector, including the fine alignment corrections.
    :param ccd: (DigitalCamera) The optical detector.
    """
    # The only way to get the right info is to look at what metadata the
    # images will get
    md = copy.copy(ccd.getMetadata())
    img.mergeMetadata(md)  # apply correction info from fine alignment

    return md


def get_ccd_pxs(ccd):
    """
    Calculates the pixel size of the optical detector (projected on the sample).
    :param ccd: (DigitalCamera) The optical detector.
    :returns: (float, float) The pixel size of the optical detector.
    """
    md = get_ccd_md(ccd)

    pxs = md[model.MD_PIXEL_SIZE]
    # compensate for binning
    binning = ccd.binning.value
    pxs = [p / b for p, b in zip(pxs, binning)]

    return pxs


def get_ccd_fov(ccd):
    """
    Calculates the (theoretical) field of view of the optical detector.
    :param ccd: (DigitalCamera) The optical detector.
    :returns: (tuple of 4 floats) Position in physical coordinates m (l, t, b, r).
    """
    pxs = get_ccd_pxs(ccd)
    center = (0, 0)  # TODO use the fine alignment shift
    shape = ccd.shape[0:2]
    width = (shape[0] * pxs[0], shape[1] * pxs[1])
    logging.info("CCD width: " + str(width))
    logging.info("CCD shape: " + str(shape))
    logging.info("CCD pxs: " + str(pxs))
    logging.info("CCD center: " + str(pxs))

    phys_rect = [center[0] - width[0] / 2,  # left
                 center[1] - width[1] / 2,  # top
                 center[0] + width[0] / 2,  # right
                 center[1] + width[1] / 2]  # bottom

    return phys_rect


def get_sem_fov(emitter):
    """
    Calculates the (theoretical) scanning area of the SEM. Works even if the
    SEM has not sent any image yet.
    :param emitter: (Emitter) The e-beam scanner.
    :returns: (tuple of 4 floats) Position in physical coordinates m (l, t, b, r).
    """
    center = (0, 0)

    sem_width = (emitter.shape[0] * emitter.pixelSize.value[0],
                 emitter.shape[1] * emitter.pixelSize.value[1])
    sem_rect = [center[0] - sem_width[0] / 2,  # left
                center[1] - sem_width[1] / 2,  # top
                center[0] + sem_width[0] / 2,  # right
                center[1] + sem_width[1] / 2]  # bottom
    # TODO: handle rotation?

    return sem_rect


def convert_roi_ratio_to_phys(emitter, roi):
    """
    Convert the ROI in relative coordinates (to the SEM FoV) into physical coordinates.
    :param emitter: (Emitter) The e-beam scanner.
    :param roi: (4 floats) ltrb positions relative to the FoV.
    :returns: (4 floats) Physical ltrb positions.
    """
    sem_rect = get_sem_fov(emitter)
    # sem_rect = [x*1.5 for x in sem_rect]  # Hack to allow for rotated SEM
    logging.info("SEM FoV = %s", sem_rect)
    phys_width = (sem_rect[2] - sem_rect[0],
                  sem_rect[3] - sem_rect[1])

    # In physical coordinates Y goes up, but in ROI, Y goes down => "1-"
    phys_rect = (sem_rect[0] + roi[0] * phys_width[0],
                 sem_rect[1] + (1 - roi[3]) * phys_width[1],
                 sem_rect[0] + roi[2] * phys_width[0],
                 sem_rect[1] + (1 - roi[1]) * phys_width[1]
                 )

    return phys_rect


def convert_roi_phys_to_ccd(ccd, roi):
    """
    Convert the ROI in physical coordinates into a optical detector (ccd) ROI (in pixels).
    :param roi: (4 floats) The roi (ltrb) position in m.
    :returns: (4 ints or None) The roi (ltrb) position in pixels, or None if no intersection.
    """
    ccd_rect = get_ccd_fov(ccd)
    logging.info("CCD FoV = %s", ccd_rect)
    phys_width = (ccd_rect[2] - ccd_rect[0],
                  ccd_rect[3] - ccd_rect[1])

    logging.info("phys width: " + str(phys_width))
    logging.info("roi: " + str(roi))
    logging.info("ccd rect: " + str(ccd_rect))

    # convert to a proportional ROI
    proi = ((roi[0] - ccd_rect[0]) / phys_width[0],
            (roi[1] - ccd_rect[1]) / phys_width[1],
            (roi[2] - ccd_rect[0]) / phys_width[0],
            (roi[3] - ccd_rect[1]) / phys_width[1],
            )
    # inverse Y (because physical Y goes down, while pixel Y goes up)
    proi = (proi[0], 1 - proi[3], proi[2], 1 - proi[1])
    logging.info("proi: " + str(proi))

    # convert to pixel values, rounding to slightly bigger area
    res = ccd.resolution.value
    pxroi = (int(proi[0] * res[0]),
             int(proi[1] * res[1]),
             int(math.ceil(proi[2] * res[0])),
             int(math.ceil(proi[3] * res[1])),
             )

    logging.info("pxroi: " + str(pxroi))

    # Limit the ROI to the one visible in the FoV
    trunc_roi = util.rect_intersect(pxroi, (0, 0) + res)
    if trunc_roi is None:
        return None
    if trunc_roi != pxroi:
        logging.warning("CCD FoV doesn't cover the whole ROI, it would need "
                        "a ROI of %s in CCD referential.", pxroi)

    return trunc_roi


def sem_roi_to_ccd(emitter, detector, roi, margin=0):
    """
    Converts a ROI defined in the SEM referential a ratio of FoV to a ROI
    which should cover the same physical area in the optical FoV.
    :param emitter: (Emitter) The e-beam scanner.
    :param detector: (DigitalCamera) The optical detector.
    :param roi: (0<=4 floats<=1) left-top-right-bottom pixels of the ROI.
    :param margin: (float) Extra space around the optical FoV, that should be not cropped.
    :returns: (0<=4 int) left-top-right-bottom pixels on the detector, when binning == 1.
    """
    # convert ROI to physical position
    phys_rect = convert_roi_ratio_to_phys(emitter, roi)
    logging.info("ROI defined at ({:.3e}, {:.3e}, {:.3e}, {:.3e}) m".format(*phys_rect))

    # Add the margin
    phys_rect = (phys_rect[0] - margin, phys_rect[1] - margin,
                 phys_rect[2] + margin, phys_rect[3] + margin)
    logging.info("ROI with margin defined at ({:.3e}, {:.3e}, {:.3e}, {:.3e}) m".format(*phys_rect))

    # convert physical position to CCD
    ccd_roi = convert_roi_phys_to_ccd(detector, phys_rect)
    if ccd_roi is None:
        logging.error("Failed to find the ROI on the CCD, will use the whole CCD")
        ccd_roi = (0, 0) + detector.resolution.value
    else:
        logging.info("Will use the CCD ROI %s", ccd_roi)

    return ccd_roi


class SECOMCLSettingsStream(acqstream.CCDSettingsStream):
    """
    A cl settings stream, for a set of points (on the SEM).
    The live view is just the raw CCD image.
    """
    def __init__(self, name, detector, dataflow, emitter, **kwargs):
        """
        :param detector: (DigitalCamera) The optical detector (ccd).
        :param dataflow: (DataFlow) The dataflow of the detector.
        :param emitter: (Emitter) The component that generates energy and
                        also controls the position of the energy (the e-beam of the SEM).
        """
        if "acq_type" not in kwargs:
            kwargs["acq_type"] = model.MD_AT_CL

        # Skip the RepetitionStream.__init__ because it gets confused with pixelSize being
        # two floats.
        acqstream.LiveStream.__init__(self, name, detector, dataflow, emitter)

        self._scanner = emitter

        # Region of acquisition (ROI) + repetition is sufficient, but pixel size is nicer for the user.
        # As the settings are over-specified, whenever ROI, repetition, or pixel
        # size changes, one (or more) other VA is updated to keep everything
        # consistent. In addition, there are also hardware constraints, which
        # must also be satisfied. The main rules followed are:
        #  * Try to keep the VA which was changed (by the user) as close as
        #    possible to the requested value (within hardware limits).
        # So in practice, the three setters behave in this way:
        #  * region of acquisition set: ROI (as requested) + repetition (current) → PxS (updated)
        #  * pixel size set: PxS (as requested) + ROI (current) → repetition (updated)
        #    The ROA is adjusted to ensure the repetition is a round number and acceptable by the hardware.
        #  * repetition set: Rep (as requested) + ROI (current) → PxS (updated)
        #    The repetition is adjusted to fit the hardware limits

        # Region of interest as left, top, right, bottom (in ratio from the
        # whole area of the emitter => between 0 and 1)
        # We overwrite the VA provided by LiveStream to define a setter.
        self.roi = model.TupleContinuous((0, 0, 1, 1),
                                         range=((0, 0, 0, 0), (1, 1, 1, 1)),
                                         cls=(int, float),
                                         setter=self._setROI)

        # Start with pixel size to fit 1024 px, as it's typically a sane value
        # for the user (and adjust for the hardware).
        spxs = emitter.pixelSize.value  # m, size at scale = 1
        sshape = emitter.shape  # px, max number of pixels scanned
        phy_size_x = spxs[0] * sshape[0]  # m
        phy_size_y = spxs[1] * sshape[1]  # m
        pxs = (phy_size_x / 1024, phy_size_y / 1024)

        roi, rep, pxs = self._updateROIAndPixelSize(self.roi.value, pxs)

        # The number of pixels acquired in each dimension. It will be assigned to the resolution
        # of the emitter (but cannot be directly set, as one might want to use the emitter while
        # configuring the stream).
        self.repetition = model.ResolutionVA(rep,
                                             emitter.resolution.range,
                                             setter=self._setRepetition)

        # The size of the pixel (IOW, the distance between the center of two
        # consecutive pixels or the "pitch"). Value can vary for vertical and horizontal direction.
        # The actual range is dynamic, as it changes with the magnification.
        self.pixelSize = model.TupleContinuous(pxs,
                                               range=((0, 0), (1, 1)),
                                               unit="m",
                                               cls=(int, float),
                                               setter=self._setPixelSize)

        # Typical user wants density much lower than SEM.
        self.pixelSize.value = tuple(numpy.array(self.pixelSize.value) * 50)

        # Maximum margin is half the CCD FoV.
        ccd_rect = get_ccd_fov(detector)
        max_margin = max(ccd_rect[2] - ccd_rect[0], ccd_rect[3] - ccd_rect[1]) / 2
        # roi_margin (0 <= float): extra margin (in m) around the SEM area to select the CCD ROI.
        self.roi_margin = model.FloatContinuous(0, (0, max_margin), unit="m")

        # Exposure time of each pixel is the exposure time of the detector.
        # The dwell time of the emitter will be adapted before the acquisition.

        # Update the pixel size whenever SEM magnification changes.
        # This allows to keep the ROI at the same place in the SEM FoV.
        # Note: This is to be done only if the user needs to manually update the magnification.
        self.magnification = self._scanner.magnification
        self._prev_mag = self.magnification.value
        self.magnification.subscribe(self._onMagnification)

    def _setPixelSize(self, pxs):
        """
        Ensures pixel size is within the current allowed range, try to keep sames ROI and update repetition.
        :param pxs: (float, float) The requested pixel size.
        :returns: (float, float) The new (valid) pixel size.
        """
        roi, rep, pxs = self._updateROIAndPixelSize(self.roi.value, pxs)

        # update roi and rep without going through the checks
        self.roi._value = roi
        self.roi.notify(roi)
        self.repetition._value = rep
        self.repetition.notify(rep)

        return pxs

    def _setRepetition(self, repetition):
        """
        Find a fitting repetition, try to keep the same ROI and update pixel size. Try using the current ROI by making
        sure that the repetition is ints (pixelSize and roi changes are notified but the setter is not called).
        :param repetition: (tuple of 2 ints) The requested repetition (might be clamped).
        :returns: (tuple of 2 ints): The new (valid) repetition.
        """
        roi = self.roi.value
        spxs = self._scanner.pixelSize.value
        sshape = self._scanner.shape
        phy_size = (spxs[0] * sshape[0], spxs[1] * sshape[1])  # max physical ROI

        # Clamp the repetition to be sure it's correct (it'll be clipped against the scanner
        # resolution later on, to be sure it's compatible with the hardware).
        rep = self.repetition.clip(repetition)

        # If ROI is undefined => link repetition and pxs as if ROI is full
        if roi == acqstream.UNDEFINED_ROI:
            pxs = (phy_size[0] / rep[0], phy_size[1] / rep[1])
            roi, rep, pxs = self._updateROIAndPixelSize((0, 0, 1, 1), pxs)
            self.pixelSize._value = pxs
            self.pixelSize.notify(pxs)
            return rep

        # The basic principle is that the center and surface of the ROI stay.
        # We only adjust the X/Y ratio and the pixel size based on the new repetition.

        prev_rep = self.repetition.value
        prev_pxs = self.pixelSize.value

        # Keep area and adapt ROI (to the new repetition ratio).
        pxs = (prev_pxs[0] * prev_rep[0] / rep[0], prev_pxs[1] * prev_rep[1] / rep[1])
        roi = self._adaptROI(roi, rep, pxs)
        logging.debug("Estimating roi = %s, rep = %s, pxs = %s", roi, rep, pxs)

        roi, rep, pxs = self._updateROIAndPixelSize(roi, pxs)
        # update roi and pixel size without going through the checks
        self.roi._value = roi
        self.roi.notify(roi)
        self.pixelSize._value = pxs
        self.pixelSize.notify(pxs)

        return rep

    def _getPixelSizeRange(self):
        """
        Calculates the min and max value possible for the pixel size at the current magnification.
        :returns: (tuple of tuple of 2 floats) Min and max values of the pixel size [m].
        """
        # Two things to take care of:
        # * current pixel size of the scanner (which depends on the magnification)
        # * merge horizontal/vertical dimensions into one fits-all

        # The current scanner pixel size is the minimum size
        spxs = self._scanner.pixelSize.value
        min_pxs = spxs
        min_scale = self._scanner.scale.range[0]
        if min_scale < (1, 1):
            # Pixel size can be smaller if not scanning the whole FoV
            min_pxs = tuple(numpy.array(min_pxs) * numpy.array(min_scale))
        shape = self._scanner.shape
        # The maximum pixel size is if we acquire a single pixel for the whole FoV
        max_pxs = (spxs[0] * shape[0], spxs[1] * shape[1])

        return min_pxs, max_pxs

    def _setROI(self, roi):
        """
        Ensures that the ROI is always an exact number of pixels, keep the current repetition
        and update the pixel size.
        :param roi: (tuple of 4 floats) The requested ROI (ltbr).
        :returns: (tuple of 4 floats) The new (valid) ROI (ltbr).
        """
        pxs = self.pixelSize.value

        old_roi = self.roi.value
        if old_roi != acqstream.UNDEFINED_ROI and roi != acqstream.UNDEFINED_ROI:
            old_size = (old_roi[2] - old_roi[0], old_roi[3] - old_roi[1])
            new_size = (roi[2] - roi[0], roi[3] - roi[1])

            # -> keep old rep
            # -> Adjust ROI and pxs to be the same area as requested ROI
            rep = self.repetition.value
            scale = numpy.array(new_size) / numpy.array(old_size)
            # Rep should stay the same, adjust pxs based on requested area.
            pxs = (pxs[0] * scale[0], pxs[1] * scale[1])
            roi = self._adaptROI(roi, rep, pxs)

        roi, rep, pxs = self._updateROIAndPixelSize(roi, pxs)
        # update repetition without going through the checks
        self.repetition._value = rep
        self.repetition.notify(rep)
        self.pixelSize._value = pxs
        self.pixelSize.notify(pxs)

        return roi

    def _updateROIAndPixelSize(self, roi, pxs):
        """
        Adapt the ROI and pixel size so that they are correct. It checks that they are within bounds
        and if not, make them fit in the bounds by adapting the repetition.
        :param roi: (4 floats) The ROI requested (might be slightly changed).
        :param pxs: (float, float) The requested pixel size.
        :returns:
                  (4 floats) The new ROI (ltbr).
                  (2 ints) The new repetition.
                  (2 floats) The new pixel size.
        """
        # If ROI is undefined => link rep and pxs as if the ROI was full
        if roi == acqstream.UNDEFINED_ROI:
            _, rep, pxs = self._updateROIAndPixelSize((0, 0, 1, 1), pxs)
            return roi, rep, pxs

        # compute the ROI.
        roi = self._fitROI(roi)

        # Compute the pixel size for a given scanner px size and ensure it's within range.
        spxs = self._scanner.pixelSize.value  # px size of scanner for given magnification (pitch size)
        scale = numpy.array([pxs[0] / spxs[0], pxs[1] / spxs[1]])
        min_scale = numpy.array(self._scanner.scale.range[0])
        max_scale = numpy.array([self._scanner.shape[0], self._scanner.shape[1]])
        # calculate scaling between scanner px size and px size (pitch, distance between ebeam positions)
        scale = numpy.maximum(min_scale, numpy.minimum(scale, max_scale))
        pxs = tuple(scale * numpy.array(spxs))  # tuple (x, y)

        # Compute the repetition (ints) that fits the ROI with the requested pixel size.
        sshape = self._scanner.shape
        roi_size = (roi[2] - roi[0], roi[3] - roi[1])
        rep = (int(round(sshape[0] * roi_size[0] / scale[0])),
               int(round(sshape[1] * roi_size[1] / scale[1])))

        logging.debug("First trial with roi = %s, rep = %s, pxs = %s", roi, rep, pxs)

        # Ensure it's really compatible with the hardware
        rep = self._scanner.resolution.clip(rep)

        # Update the ROI so that it's exactly "pixel size * repetition", while keeping its center fixed.
        roi = self._adaptROI(roi, rep, pxs)
        roi = self._fitROI(roi)

        # Double check we didn't end up with scale out of range.
        pxs_range = self._getPixelSizeRange()
        if not pxs_range[0][0] <= pxs[0] <= pxs_range[1][0] or not pxs_range[0][1] <= pxs[1] <= pxs_range[1][1]:
            logging.error("Computed impossibly small pixel size %s, with range %s", pxs, pxs_range)
            # TODO: revert to some *acceptable* values for ROI + rep + PxS?

        logging.debug("Computed roi = %s, rep = %s, pxs = %s", roi, rep, pxs)

        return tuple(roi), tuple(rep), tuple(pxs)

    def _adaptROI(self, roi, rep, pxs):
        """
        Computes the ROI, so that it's exactly "pixel size * repetition", while keeping its center fixed
        :param roi: (4 floats) The current ROI, just to know its center.
        :param rep: (2 ints) The repetition (e-beam positions).
        :param pxs: (float, float) The pixel size (pitch size, distance between 2 e-beam positions).
        :returns: (4 floats) The adapted roi (ltrb).
        """
        # Rep + PxS (+ center of ROI) -> ROI
        roi_center = ((roi[0] + roi[2]) / 2,
                      (roi[1] + roi[3]) / 2)
        spxs = self._scanner.pixelSize.value
        sshape = self._scanner.shape
        phy_size = (spxs[0] * sshape[0], spxs[1] * sshape[1])  # max physical ROI
        roi_size = (rep[0] * pxs[0] / phy_size[0],
                    rep[1] * pxs[1] / phy_size[1])
        roi = (roi_center[0] - roi_size[0] / 2,
               roi_center[1] - roi_size[1] / 2,
               roi_center[0] + roi_size[0] / 2,
               roi_center[1] + roi_size[1] / 2)

        return roi

    def _onMagnification(self, mag):
        """
        Called when the SEM magnification is updated. Update the pixel size so that the ROI stays at
        the same place in the SEM FoV and with the same repetition.
        The bigger the magnification is, the smaller should be the pixel size.
        :param mag: (float) The new magnification.
        """
        ratio = self._prev_mag / mag
        self._prev_mag = mag
        self.pixelSize._value = tuple(numpy.array(self.pixelSize._value) * ratio)
        self.pixelSize.notify(self.pixelSize._value)


class SECOMCLSEMMDStream(acqstream.SEMCCDMDStream):
    """
    Multiple detector Stream made of SEM + SECOM CL acquisition.
    It handles acquisition, but not rendering (so .image always returns an empty image).
    """
    def __init__(self, name, streams):
        """
        :param streams: ([Stream]) The streams to acquire.
        """
        super(SECOMCLSEMMDStream, self).__init__(name, streams)

        self.filename = model.StringVA("a.tiff")
        self.firstOptImg = None  # save the first optical image for display in analysis tab

    def _runAcquisition(self, future):
        """
        Acquires images from the multiple detectors via software synchronisation.
        Acquires images via moving the ebeam.
        :param future: (ProgressiveFuture) The current future running for the whole acquisition.
        :returns: (list of DataArrays): All the data acquired (self.raw).
        """
        self.ccd_roi = sem_roi_to_ccd(self._emitter, self._ccd, self.roi.value, self._sccd.roi_margin.value)

        return super(SECOMCLSEMMDStream, self)._runAcquisition(future)

    def _preprocessData(self, n, data, i):
        """
        Pre-process the raw data, just after it was received from the detector.
        :param n: (0<=int) The detector/stream index.
        :param data: (DataArray) The data as received from the detector, from _onData(),
                     and with MD_POS updated to the current position of the e-beam.
        :param i: (int, int) The iteration number in X, Y.
        :returns: (value) The value as needed by _assembleFinalData.
        """
        if n != self._ccd_idx:
            return super(SECOMCLSEMMDStream, self)._preprocessData(n, data, i)

        ccd_roi = self.ccd_roi
        data = data[ccd_roi[1]: ccd_roi[3] + 1, ccd_roi[0]: ccd_roi[2] + 1]  # crop

        cpos = self._get_center_pos(data, self.ccd_roi)
        sname = self._streams[n].name.value

        data.metadata[model.MD_DESCRIPTION] = sname
        # update center position of optical image (should be the same for all optical images)
        data.metadata[model.MD_POS] = cpos

        # Hack: To avoid memory issues, we save the optical image immediately after being acquired.
        # Thus, we do not keep all the images in cache until the end of the acquisition.
        fn = self.filename.value
        logging.debug("Will save CL data to %s", fn)
        fn_prefix, fn_ext = os.path.splitext(self.filename.value)

        self.save_data(data,
                       prefix=fn_prefix,
                       xres=self.repetition.value[0],
                       yres=self.repetition.value[1],
                       xstepsize=self._getPixelSize()[0] * 1e9,
                       ystepsize=self._getPixelSize()[1] * 1e9,
                       xpos=i[1]+1,  # start counting with 1
                       ypos=i[0]+1,
                       type="optical"
                       )

        # Return something, but not the data to avoid data being cached.
        return model.DataArray(numpy.array([0]))

    def _assembleLiveData(self, n, raw_data, px_idx, rep, pol_idx):
        if n != self._ccd_idx:
            return super(SECOMCLSEMMDStream, self)._assembleLiveData(n, raw_data, px_idx, rep, pol_idx)

        # For other streams (CL) don't do a live update
        return

    def _assembleFinalData(self, n, data):
        """
        Called at the end of an entire acquisition. It should assemble the data and append it to ._raw .
        :param n: (0<=int) The index of the detector.
        :param raw_das: (list) List of data acquired for given detector n.
        """
        if n != self._ccd_idx:
            super(SECOMCLSEMMDStream, self)._assembleFinalData(n, data)

        # For other streams (CL) don't do anything
        return

    def _get_center_pos(self, data, crop_roi):
        """
        Calculate the center of the region of acquisition based on the center of the optical detector (ccd).
        :param data: () TODO
        :param crop_roi: () TODO
        :returns: (float, float) The center of the region of acquisition.
        """
        center_det_abs = self._ccd.getMetadata()[model.MD_POS]  # absolute position in space
        res_det = self._ccd.resolution.value  # detector shape/binning
        pxs = data.metadata[model.MD_PIXEL_SIZE]  # including the binning
        # TODO: pixel size cor

        center_roa = numpy.array(((crop_roi[0] + (crop_roi[2] - crop_roi[0])/2) * pxs[0],
                                  (crop_roi[1] + (crop_roi[3] - crop_roi[1])/2) * -pxs[1]))
        center_det = numpy.array((0.5 * res_det[0] * pxs[0],
                                  0.5 * res_det[1] * -pxs[1]))
        shift = center_roa - center_det  # in [m]
        center_roa_abs = center_det_abs + shift

        return tuple(center_roa_abs)

    def _getPixelSize(self):
        """
        Computes the pixel size (based on the repetition, roi and FoV of the
        e-beam). The RepetitionStream does provide a .pixelSize VA, which
        should contain the same value, but that VA is for use by the GUI.
        :returns: (float, float) The pixel size in m.
        """
        epxs = self._emitter.pixelSize.value
        rep = self.repetition.value
        roi = self.roi.value
        eshape = self._emitter.shape
        phy_size_x = (roi[2] - roi[0]) * epxs[0] * eshape[0]
        phy_size_y = (roi[3] - roi[1]) * epxs[1] * eshape[1]
        pxsy = phy_size_y / rep[1]
        pxsx = phy_size_x / rep[0]
        logging.debug("px size guessed = %s x %s", pxsx, pxsy)

        return (pxsx, pxsy)

    def save_data(self, data, **kwargs):
        """
        Saves the data into a file.
        :param data: (model.DataArray or list of model.DataArray) The data to save.
        :param kwargs: (dict (str->value)) The values to substitute in the file name.
        """
        # export to single tiff files
        exporter = dataio.get_converter(FMT)

        fn = FN_FMT % kwargs

        # Save first image for display in analysis tab
        if (kwargs["xpos"], kwargs["ypos"]) == (1, 1):
            self.firstOptImg = fn

        if os.path.exists(fn):
            # mostly to warn if multiple ypos/xpos are rounded to the same value
            logging.warning("Overwriting file '%s'.", fn)
        else:
            logging.info("Saving file '%s", fn)

        exporter.export(fn, data)


class CLAcqPlugin(Plugin):
    """
    This is a script to acquire a set of optical images from the detector (ccd) for various e-beam
    spots on the sample along a grid. Can also be used as a plugin.
    """
    name = "CL acquisition for SECOM"
    __version__ = "2.0"
    __author__ = u"Éric Piel, Lennard Voortman, Sabrina Rossberger"
    __license__ = "Public domain"

    # Describe how the values should be displayed
    # See odemis.gui.conf.data for all the possibilities
    vaconf = OrderedDict((
        ("repetition", {
        }),
        ("pixelSize", {
        }),
        ("exposureTime", {
            "range": (1e-6, 180),
            "scale": "log",
        }),
        ("binning", {
            "control_type": gui.CONTROL_RADIO,
        }),
        ("roi_margin", {
            "label": "ROI margin",
            "tooltip": "Extra space around the SEM area to store on the CCD"
        }),
        ("filename", {
            "control_type": gui.CONTROL_SAVE_FILE,
            "wildcard": formats_to_wildcards({tiff.FORMAT: tiff.EXTENSIONS})[0],
        }),
        ("period", {
            "label": "Drift corr. period",
            "tooltip": u"Maximum time after running a drift correction (anchor region acquisition)",
            "control_type": gui.CONTROL_SLIDER,
            "scale": "log",
            "range": (1, 300),  # s, the VA allows a wider range, not typically needed
            "accuracy": 2,
        }),
        ("tool", {
            "label": "Selection tools",
            "control_type": gui.CONTROL_RADIO,
            "choices": {TOOL_NONE: u"drag", TOOL_ROA: u"ROA", TOOL_RO_ANCHOR: u"drift"},
        }),
        ("expectedDuration", {
        }),
    ))

    def __init__(self, microscope, main_app):
        """
        :param microscope: (Microscope or None) The main back-end component.
        :param main_app: (wx.App) The main GUI component.
        """
        super(CLAcqPlugin, self).__init__(microscope, main_app)

        # Can only be used with a microscope
        if not microscope:
            return
        else:
            # Check which stream the microscope supports
            self.main_data = self.main_app.main_data
            if not (self.main_data.ccd and self.main_data.ebeam):
                return

        self.exposureTime = self.main_data.ccd.exposureTime
        self.binning = self.main_data.ccd.binning
        # Trick to pass the component (ccd to binning_1d_from_2d())
        self.vaconf["binning"]["choices"] = (lambda cp, va, cf:
                                             cutil.binning_1d_from_2d(self.main_data.ccd, va, cf))

        self._survey_stream = None
        self._optical_stream = acqstream.BrightfieldStream(
                                    "Optical",
                                    self.main_data.ccd,
                                    self.main_data.ccd.data,
                                    emitter=None,
                                    focuser=self.main_data.focus)
        self._secom_cl_stream = SECOMCLSettingsStream(
                                "Secom-CL",
                                self.main_data.ccd,
                                self.main_data.ccd.data,
                                self.main_data.ebeam)
        self._sem_stream = acqstream.SEMStream(
                                "Secondary electrons concurrent",
                                self.main_data.sed,
                                self.main_data.sed.data,
                                self.main_data.ebeam)

        self._secom_sem_cl_stream = SECOMCLSEMMDStream("SECOM SEM CL", [self._sem_stream,
                                                                        self._secom_cl_stream])

        self._driftCorrector = leech.AnchorDriftCorrector(self.main_data.ebeam,
                                                          self.main_data.sed)

        self.conf = get_acqui_conf()
        self.expectedDuration = model.VigilantAttribute(1, unit="s", readonly=True)
        self.exposureTime.subscribe(self._update_exp_dur)

        self.filename = self._secom_sem_cl_stream.filename  # duplicate VA
        self.filename.subscribe(self._on_filename)

        self.addMenu("Acquisition/CL acquisition...", self.start)

    def _on_filename(self, fn):
        """
        Store path and pattern in conf file.
        :param fn: (str) The filename to be stored.
        """
        # Store the directory so that next filename is in the same place
        p, bn = os.path.split(fn)
        if p:
            self.conf.last_path = p

        # Save pattern
        self.conf.fn_ptn, self.conf.fn_count = guess_pattern(fn)

    def _update_filename(self):
        """
        Set filename from pattern in conf file.
        """
        fn = create_filename(self.conf.last_path, self.conf.fn_ptn, '.tiff', self.conf.fn_count)
        self.conf.fn_count = update_counter(self.conf.fn_count)

        # Update the widget, without updating the pattern and counter again
        self.filename.unsubscribe(self._on_filename)
        self.filename.value = fn
        self.filename.subscribe(self._on_filename)

    def _get_sem_survey(self):
        """
        Finds the SEM stream in the acquisition tab.
        :returns: (SEMStream or None) None if not found.
        """
        tab_data = self.main_app.main_data.tab.value.tab_data_model
        for s in tab_data.streams.value:
            if isinstance(s, acqstream.SEMStream):
                return s

        logging.warning("No SEM stream found")
        return None

    def _update_exp_dur(self, _=None):
        """
        Called when VA that affects the expected duration is changed.
        """
        if self._survey_stream is None:
            return

        strs = [self._survey_stream, self._secom_sem_cl_stream]

        dur = acqmng.estimateTime(strs)
        logging.debug("Estimating %g s acquisition for %d streams", dur, len(strs))
        # Use _set_value as it's read only
        self.expectedDuration._set_value(math.ceil(dur), force_write=True)

    def _on_dc_roi(self, roi):
        """
        Called when the Anchor region changes.
        Used to enable/disable the drift correction period control.
        :param roi: (4 x 0<=float<=1) The anchor region selected (tlbr).
        """
        enabled = (roi != acqstream.UNDEFINED_ROI)

        # The driftCorrector should be a leech if drift correction is enabled
        dc = self._driftCorrector
        if enabled:
            if dc not in self._sem_stream.leeches:
                self._sem_stream.leeches.append(dc)
        else:
            try:
                self._sem_stream.leeches.remove(dc)
            except ValueError:
                pass  # It was already not there

    @call_in_wx_main
    def _on_rep(self, rep):
        """
        Force the ROI in the canvas to show the e-beam positions.
        :param rep: (int, int) The repetition (e-beam positions) to be displayed.
        """
        self._dlg.viewport_l.canvas.show_repetition(rep, RepetitionSelectOverlay.FILL_POINT)

    def start(self):
        """
        Displays the plugin window.
        """
        self._update_filename()
        str_ctrl = self.main_app.main_data.tab.value.streambar_controller
        str_ctrl.pauseStreams()

        dlg = AcquisitionDialog(self, "CL acquisition",
                                "Acquires a CCD image for each e-beam spot.\n")
        self._dlg = dlg
        self._survey_stream = self._get_sem_survey()

        dlg.SetSize((1500, 1000))

        # Hack to force the canvas to have a region of acquisition (ROA) and anchor region (drift) overlay.
        dlg._dmodel.tool.choices = {
            TOOL_NONE,
            TOOL_ROA,
            TOOL_RO_ANCHOR,
        }

        dlg._dmodel.roa = self._secom_cl_stream.roi  # region of acquisition selected (x_tl, y_tl, x_br, y_br)
        dlg._dmodel.fovComp = self.main_data.ebeam  # size (x, y) of sem image for given magnification
        dlg._dmodel.driftCorrector = self._driftCorrector
        dlg.viewport_l.canvas.view = None
        dlg.viewport_l.canvas.setView(dlg.view, dlg._dmodel)
        dlg.viewport_r.canvas.allowed_modes = {}
        dlg.viewport_r.canvas.view = None
        dlg.viewport_r.canvas.setView(dlg.view_r, dlg._dmodel)

        self.repetition = self._secom_cl_stream.repetition  # ebeam positions to acquire
        self.repetition.subscribe(self._on_rep, init=True)
        self.pixelSize = self._secom_cl_stream.pixelSize  # pixel size per ebeam pos
        self.roi_margin = self._secom_cl_stream.roi_margin
        self.period = self._driftCorrector.period  # time between to drift corrections
        self.tool = dlg._dmodel.tool  # tools to select ROA and anchor region for drift correction
        self._driftCorrector.roi.subscribe(self._on_dc_roi, init=True)

        # subscribe to update estimated acquisition time
        self.repetition.subscribe(self._update_exp_dur, init=True)
        self.period.subscribe(self._update_exp_dur)
        self._driftCorrector.roi.subscribe(self._update_exp_dur)

        dlg.addSettings(self, self.vaconf)
        dlg.addStream(self._survey_stream)
        dlg.addStream(self._optical_stream)

        dlg.addButton("Cancel")
        dlg.addButton("Acquire", self._acquire, face_colour='blue')

        ans = dlg.ShowModal()

        if ans == 0:
            logging.info("Acquisition cancelled")
        elif ans == 1:
            logging.info("Acquisition completed")
        else:
            logging.warning("Got unknown return code %s", ans)

        self._dlg = None
        self._survey_stream = None
        dlg.Destroy()

    def save_hw_settings(self):
        """
        Saves the current e-beam settings (only e-beam!).
        """
        res = self.main_data.ebeam.resolution.value
        scale = self.main_data.ebeam.scale.value
        trans = self.main_data.ebeam.translation.value
        dt = self.main_data.ebeam.dwellTime.value
        self._hw_settings = (res, scale, trans, dt)

    def resume_hw_settings(self):
        """
        Restores the saved e-beam settings.
        """
        res, scale, trans, dt = self._hw_settings

        # order matters!
        self.main_data.ebeam.scale.value = scale
        self.main_data.ebeam.resolution.value = res
        self.main_data.ebeam.translation.value = trans
        self.main_data.ebeam.dwellTime.value = dt

    def _acquire(self, dlg):
        """
        Starts the synchronized acquisition, pauses the currently playing streams and exports the
        acquired SEM data. Opens the survey, concurrent and first optical image in the analysis tab.
        :param dlg: (AcquisitionDialog) The plugin window.
        """
        self._dlg.streambar_controller.pauseStreams()
        self.save_hw_settings()

        self.fns = []

        strs = [self._survey_stream, self._secom_sem_cl_stream]

        fn = self.filename.value
        fn_prefix, fn_ext = os.path.splitext(self.filename.value)

        try:
            f = acqmng.acquire(strs, self.main_app.main_data.settings_obs)
            dlg.showProgress(f)
            das, e = f.result()  # blocks until all the acquisitions are finished
        except CancelledError:
            pass
        finally:
            self.resume_hw_settings()

        if not f.cancelled() and das:
            if e:
                logging.warning("SECOM CL acquisition failed: %s", e)
            logging.debug("Will save CL data to %s", fn)

            # export the SEM images
            self.save_data(das,
                           prefix=fn_prefix,
                           xres=self.repetition.value[0],
                           yres=self.repetition.value[1],
                           xstepsize=self.pixelSize.value[0] * 1e9,
                           ystepsize=self.pixelSize.value[1] * 1e9,
                           idx=0)

            # Open analysis tab, with 3 files
            self.showAcquisition(self._secom_sem_cl_stream.firstOptImg)
            analysis_tab = self.main_data.getTabByName('analysis')
            for fn_img in self.fns:
                analysis_tab.load_data(fn_img, extend=True)

        dlg.Close()

    def save_data(self, data, **kwargs):
        """
        Saves the data into a file.
        :param data: (model.DataArray or list of model.DataArray) The data to save.
        :param kwargs: (dict (str->value)) Values to substitute in the file name.
        """
        # export to single tiff files
        exporter = dataio.get_converter(FMT)

        for d in data[:2]:  # only care about the sem ones, the optical images are already saved
            if d.metadata.get(model.MD_DESCRIPTION) == "Anchor region":
                kwargs["type"] = "drift"
            elif d.metadata.get(model.MD_DESCRIPTION) == "Secondary electrons concurrent":
                kwargs["type"] = "concurrent"
            else:
                kwargs["type"] = "survey"

            kwargs["xpos"] = 0
            kwargs["ypos"] = 0
            fn = FN_FMT % kwargs

            # The data is normally ordered: survey, concurrent, drift
            # => first 2 files are the ones we care about
            if kwargs["idx"] < 2:
                self.fns.append(fn)

            if os.path.exists(fn):
                # mostly to warn if multiple ypos/xpos are rounded to the same value
                logging.warning("Overwriting file '%s'.", fn)
            else:
                logging.info("Saving file '%s", fn)

            exporter.export(fn, d)
            kwargs["idx"] += 1
