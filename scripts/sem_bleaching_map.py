#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 20 Mar 2014

@author: Éric Piel, modified by Aaro Vakevainen, modified by Lennard Voortman

This is a script to scan a region with a e-beam and observe the reduction
of fluorescence on an optical image for each point scanned (due to bleaching).

run as:
./scripts/sem_bleaching_map.py --dt=600e-6 --dtsem=30e-6 --roi=0.4,0.4,0.95,0.95 --pxs=376 --subpx=16 --lpower=0.255 --emission=1 --exptime=1 --output=filename.tiff --ovw-power=0.255 --ovw-emission=1 --ovw-exp-time=1 --gridpitch=4 --drift=3 --anchor=0.01,0.01,0.1,0.1

To get information on the parameters, use sem_bleaching_map.py --help

You first need to run the odemis backend with the SECOM config. For instance,
start Odemis, and close the graphical interface. Alternatively you can start
just the back-end:
odemis-start *odm.yaml --nogui

To change some configuration settings, you can use the cli:
# ensure the magnification is correct
odemis-cli --set-attr "EBeam ExtXY" magnification 5000
# to select the CCD exposure time:
odemis-cli --set-attr "Clara" exposureTime 0.1 # in s
"""
import argparse
import copy
import logging
import math
import numpy
from odemis import model, dataio, util
from odemis.acq import drift
from odemis.util import conversion, units
from odemis.util import img
import os
import time
import sys
import threading

CCD_ROI = False  # If True, the CCD ROI will be set to fit the SEM ROI. Otherwise the whole image will be taken.
MARGIN_ACQ = 30  # how many pixels outside the ROI are saved (frame of s CCD pixels width)
MARGIN_BLEACHING = 5  # how many pixels outside the ROI are bleached (frame of s CCD pixels width (is approximated by a number of EM pixels))

logging.getLogger().setLevel(logging.INFO)  # put "DEBUG" level for more messages


class Acquirer(object):
    def __init__(self, dt, dtsem, roi, pxs, subpx=1, gridpitch=0, lpower=0.2, emission=0, exptime=2.0, olpower=None,
                 oemission=None, oexptime=None, dperiod=None, anchor=None):
        """
        pxs (0<float): distance in m between center of each tile
        subpx (1<=int): number of subpixels (must be a square of an integer)
        dperiod (0<float): drift correction period in # pixels
        anchor (0<=4 floats<=1): anchor region for drift correction
        lpower (list of float/ list of int): excitation light power on the first fluorescence pixel (in W)
        emission (list of float/ list of int): emissions source (numbered from 0 to n-1) used for the SR pixels
        exptime (list of float/ list of int): exposure time used for SR pixels (in seconds)
        """
        if subpx < 1 or (math.sqrt(subpx) % 1) != 0:
            raise ValueError("subpx must be square of an integer")

        self.dt = dt
        self.dtsem = dtsem
        self.roi = roi
        subpx_x = math.trunc(math.sqrt(subpx))
        self.pxs = pxs
        self.subpxs = pxs / subpx_x
        logging.info("Sub-pixels will be spaced by %f nm", self.subpxs * 1e9)
        self.tile_shape = (subpx_x, subpx_x)  # for now always the same in x and y
        self.gridpitch = (gridpitch, gridpitch)
        self.lpower = lpower
        self.exptime = exptime
        self.olpower = olpower
        self.oexptime = oexptime
        self.emission_index = emission

        # For drift correction
        self.drift = (0.0, 0.0)
        self.anchor = anchor
        self.dperiod = dperiod

        # Get the components
        self.sed = model.getComponent(role="se-detector")
        self.ebeam = model.getComponent(role="e-beam")
        self.light = model.getComponent(role="light")
        self.ccd = model.getComponent(role="ccd")
        self.stage = model.getComponent(role="stage")

        self.ccd_acq_complete = threading.Event()
        self._spotsimage = None

        # store the fanSpeed and the targetTemperature to later use them as the CCD acquisition values
        self.fanspeed = self.ccd.fanSpeed.value
        self.targettemperature = self.ccd.targetTemperature.value

        # scale is the distance between the SR pixels in SEM pixels at ebeam.scale=1
        self.scale = (pxs / self.ebeam.pixelSize.value[0], pxs / self.ebeam.pixelSize.value[1])

        lprng = self.light.power.range
        if not (lprng[0][self.emission_index] <= lpower <= lprng[1][self.emission_index]):
            raise ValueError("starting light power value must be between %s" % (lprng,))

        # counter-intuitively, to get the smaller pixel size, no sub-pixel
        # should be used. That's because when there is no sub-pixel we can use
        # spot mode, which allows to go at less than scale=1.
        # It could easily be fixed by allowing scale down to 0.1 in the driver.

        if (self.ebeam.pixelSize.value[0] > 10 * pxs or
                self.ebeam.pixelSize.value[0] > self.subpxs):
            raise ValueError("Pixel size requested (%g nm/%g nm) is too small "
                             "compared to recommended SEM pixel size (%g nm)"
                             % (pxs * 1e9, self.subpxs * 1e9,
                                self.ebeam.pixelSize.value[0] * 1e9))

        elif self.ebeam.pixelSize.value[0] > pxs:
            logging.warning("Pixel size requested (%g nm) is smaller than "
                            "recommended SEM pixel size (%g nm) at the current "
                            "mag.",
                            pxs * 1e9, self.ebeam.pixelSize.value[0] * 1e9)

        self._hw_settings = ()  # will be used to save/resume SEM settings

    def get_sem_fov(self):
        """
        Returns the (theoretical) scanning area of the SEM. Works even if the
        SEM has not sent any image yet.
        returns (tuple of 4 floats): position in physical coordinates m (l, t, r, b)
        """
        try:
            pos = self.stage.position.value
            center = (pos["x"], pos["y"])
        except KeyError:
            # no info, not problem => just relative to the center of the SEM
            center = (0, 0)

        sem_width = (self.ebeam.shape[0] * self.ebeam.pixelSize.value[0],
                     self.ebeam.shape[1] * self.ebeam.pixelSize.value[1])
        sem_rect = [center[0] - sem_width[0] / 2,  # left
                    center[1] - sem_width[1] / 2,  # top
                    center[0] + sem_width[0] / 2,  # right
                    center[1] + sem_width[1] / 2]  # bottom
        # TODO: handle rotation?

        return sem_rect

    def get_ccd_md(self):
        """
        Returns the Metadata associated with the ccd, including fine alignment corrections.
        """
        # The only way to get the right info is to look at what metadata the images will get
        md = copy.copy(self.ccd.getMetadata())
        img.mergeMetadata(md)  # apply correction info from fine alignment

        return md

    def get_ccd_pxs(self):
        """
        Returns the (theoretical) pixelsize of the CCD (projected on the sample).
        """
        md = self.get_ccd_md()

        pxs = md[model.MD_PIXEL_SIZE]
        # compensate for binning
        binning = self.ccd.binning.value
        pxs = [p / b for p, b in zip(pxs, binning)]

        return pxs

    def get_ccd_fov(self):
        """
        Returns the (theoretical) field of view of the CCD.
        returns (tuple of 4 floats): position in physical coordinates m (l, t, b, r)
        """
        pxs = self.get_ccd_pxs()

        md = self.get_ccd_md()
        center = md.get(model.MD_POS, (0, 0))

        shape = self.ccd.shape[0:2]

        width = (shape[0] * pxs[0], shape[1] * pxs[1])
        phys_rect = [center[0] - width[0] / 2,  # left
                     center[1] - width[1] / 2,  # top
                     center[0] + width[0] / 2,  # right
                     center[1] + width[1] / 2]  # bottom

        return phys_rect

    def convert_roi_ratio_to_phys(self, roi):
        """
        Convert the ROI in relative coordinates (to the SEM FoV) into physical
         coordinates
        roi (4 floats): ltrb positions relative to the FoV
        return (4 floats): physical ltrb positions
        """
        sem_rect = self.get_sem_fov()
        logging.debug("SEM FoV = %s", sem_rect)
        phys_width = (sem_rect[2] - sem_rect[0],
                      sem_rect[3] - sem_rect[1])

        # In physical coordinates Y goes up, but in ROI, Y goes down => "1-"
        phys_rect = (sem_rect[0] + roi[0] * phys_width[0],
                     sem_rect[1] + (1 - roi[3]) * phys_width[1],
                     sem_rect[0] + roi[2] * phys_width[0],
                     sem_rect[1] + (1 - roi[1]) * phys_width[1]
                     )

        return phys_rect

    def convert_roi_phys_to_ccd(self, roi):
        """
        Convert the ROI in physical coordinates into a CCD ROI (in pixels)
        roi (4 floats): ltrb positions in m
        return (4 ints or None): ltrb positions in pixels, or None if no intersection
        """
        ccd_rect = self.get_ccd_fov()
        logging.debug("CCD FoV = %s", ccd_rect)
        phys_width = (ccd_rect[2] - ccd_rect[0],
                      ccd_rect[3] - ccd_rect[1])

        # convert to a proportional ROI
        proi = ((roi[0] - ccd_rect[0]) / phys_width[0],
                (roi[1] - ccd_rect[1]) / phys_width[1],
                (roi[2] - ccd_rect[0]) / phys_width[0],
                (roi[3] - ccd_rect[1]) / phys_width[1],
                )
        # inverse Y (because physical Y goes down, while pixel Y goes up)
        proi = (proi[0], 1 - proi[3], proi[2], 1 - proi[1])

        # convert to pixel values, rounding to slightly bigger area
        shape = self.ccd.shape[0:2]
        pxroi = (int(proi[0] * shape[0]),
                 int(proi[1] * shape[1]),
                 int(math.ceil(proi[2] * shape[0])),
                 int(math.ceil(proi[3] * shape[1])),
                 )

        # Limit the ROI to the one visible in the FoV
        trunc_roi = util.rect_intersect(pxroi, (0, 0) + shape)
        if trunc_roi is None:
            return None
        if trunc_roi != pxroi:
            logging.warning("CCD FoV doesn't cover the whole ROI, it would need "
                            "a ROI of %s in CCD referential.", pxroi)

        return trunc_roi

    def sem_roi_to_ccd(self, roi):
        """
        Converts a ROI defined in the SEM referential a ratio of FoV to a ROI
        which should cover the same physical area in the optical FoV.
        roi (0<=4 floats<=1): ltrb of the ROI
        return (0<=4 int): ltrb pixels on the CCD, when binning == 1
        """
        # convert ROI to physical position
        phys_rect = self.convert_roi_ratio_to_phys(roi)
        logging.info("ROI defined at ({:.3e}, {:.3e}, {:.3e}, {:.3e}) m".format(*phys_rect))

        # convert physical position to CCD
        ccd_roi = self.convert_roi_phys_to_ccd(phys_rect)
        if ccd_roi is None:
            logging.error("Failed to find the ROI on the CCD, will use the whole CCD")
            ccd_roi = (0, 0) + self.ccd.shape[0:2]
        else:
            logging.info("Will use the CCD ROI %s", ccd_roi)

        return ccd_roi

    def configure_ccd(self, roi):
        """
        Configure the CCD resolution and binning to have the minimum acquisition
        region that fit in the given ROI and with the maximum binning possible.
        roi (0<=4 int): ltrb pixels on the CCD, when binning == 1
        """
        # Set the resolution (using binning 1, to use the direct values)
        self.ccd.binning.value = (1, 1)

        if CCD_ROI:
            # TODO: with andorcam3, translation is possible
            # As translation is not possible, the acquisition region must be
            # centered. => Compute the minimal centered rectangle that includes the roi.
            center = [s / 2 for s in self.ccd.shape[0:2]]
            hwidth = (max(abs(roi[0] - center[0]), abs(roi[2] - center[0])),
                      max(abs(roi[1] - center[1]), abs(roi[3] - center[1])))
            res = [int(math.ceil(w * 2)) for w in hwidth]

            self.ccd.resolution.value = self.ccd.resolution.clip(res)

            # TODO: check that the physical area of the final ROI is still within the original ROI
        else:
            self.ccd.resolution.value = self.ccd.resolution.range[1]
            logging.info("Using the whole CCD area")

        logging.info("CCD res = %s, binning = %s",
                     self.ccd.resolution.value,
                     self.ccd.binning.value)

        # Just to be sure, turn off the light
        self.light.power.value = self.light.power.range[0]

    def save_hw_settings(self):
        res = self.ebeam.resolution.value
        scale = self.ebeam.scale.value
        trans = self.ebeam.translation.value
        dt = self.ebeam.dwellTime.value
        lpower = self.light.power.value

        self._hw_settings = (res, scale, trans, dt, lpower)

    def resume_hw_settings(self):
        res, scale, trans, dt, lpower = self._hw_settings
        # order matters!
        self.ebeam.scale.value = scale
        self.ebeam.resolution.value = res
        self.ebeam.translation.value = trans
        self.ebeam.dwellTime.value = dt
        self.light.power.value = lpower

    def configure_sem_for_tile(self):
        """
        Configure the SEM to be able to acquire one spot
        """
        self.ebeam.dwellTime.value = self.dt

        # TODO: check this will allow the spots to be regularly spaced between tiles
        if self.tile_shape == (1, 1):
            # scale doesn't matter, so just use 1
            self.ebeam.scale.value = (1, 1)
        else:
            # scale is the ratio between the goal pixel size and pixel size at scale=1
            sem_pxs = self.ebeam.pixelSize.value
            scale = (self.subpxs / sem_pxs[0], self.subpxs / sem_pxs[1])
            self.ebeam.scale.value = scale
        self.ebeam.resolution.value = self.tile_shape  # just a spot

    def configure_sem_for_survey(self, roi):
        """
        Configure the SEM for regular survey image
        """
        self.ebeam.dwellTime.value = self.dtsem
        self.ebeam.scale.value = (1, 1)

        sem_pxs = self.ebeam.pixelSize.value
        ccd_pxs = self.get_ccd_pxs()

        full_width = self.ebeam.resolution.range[1]

        # calculate the width of the border in rel coordinates
        b_rel = [MARGIN_BLEACHING * ccd_pxs[0] / (full_width[0] * sem_pxs[0]),
                 MARGIN_BLEACHING * ccd_pxs[1] / (full_width[1] * sem_pxs[1])]

        # Turn off the cooling and fan to reduce vibrations
        self.ccd.fanSpeed.value = 0
        self.ccd.targetTemperature = 25
        time.sleep(0.5)  # wait half a second for vibrations to stop

        rel_width = [(roi[2] - roi[0]) + 2.0 * b_rel[0], (roi[3] - roi[1]) + 2.0 * b_rel[1]]
        rel_center = [(roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2]

        trans = [full_width[0] * (rel_center[0] - 0.5) - self.drift[0],
                 full_width[1] * (rel_center[1] - 0.5) - self.drift[1]]

        resolution = [int(full_width[0] * rel_width[0]),
                      int(full_width[1] * rel_width[1])]

        # configure the ebeam location
        self.ebeam.translation.value = trans  # px (0 = center)
        self.ebeam.resolution.value = self.ebeam.resolution.clip(resolution)

    def acquire_sem_survey(self, roi):
        """
        Configures and Acquires the SEM for regular survey image
        """
        self.configure_sem_for_survey(roi)
        data = self.sed.data.get()
        # turn on cooling of the scmos
        self.ccd.fanSpeed.value = self.fanspeed
        self.ccd.targetTemperature = self.targettemperature

        return data

    def calc_rep(self, roi):
        """
        Compute the X and Y center of the ebeam
        roi (0<=4 floats<=1): ltrb of the ROI
        """
        # position is expressed in pixels, within the .translation ranges
        full_width = self.ebeam.shape[0:2]
        scale = self.scale

        rel_width = [roi[2] - roi[0], roi[3] - roi[1]]
        px_width = [full_width[0] * rel_width[0], full_width[1] * rel_width[1]]

        # number of points to scan
        rep = [int(max(1, px_width[0] / scale[0])),
               int(max(1, px_width[1] / scale[1]))]

        return rep

    def check_gridpitch(self, rep, gridpitch):
        """
        Checks whether grid scanning is enabled, and updates rep to be divisible by gridpitch
        """
        if rep[0] < gridpitch[0] or rep[1] < gridpitch[1]:
            logging.warning("Disabling grid scanning because number of SR pixels %s, smaller than gridpitch %s", rep,
                            gridpitch)
            self.gridpitch = (rep[1], rep[0])  # this effectively disables grid scanning
        elif gridpitch[0] > 0 and gridpitch[1] > 0:
            # we need to account for the gridpitch
            # rep must be a multiple of gridpitch
            logging.info("Target nr of SR pixels before grid scanning: %s", rep)
            rep = [int(rep[0] / gridpitch[0]) * gridpitch[0],
                   int(rep[1] / gridpitch[1]) * gridpitch[1]]
        else:
            self.gridpitch = (rep[1], rep[0])  # this effectively disables grid scanning

        return rep

    def update_roi(self, roi, rep):
        """
        Update the ROI after defining the exact number of SR pixels
        roi (0<=4 floats<=1): ltrb of the ROI
        rep (2 ints): number of SR pixels in X and Y
        return (0<=4 floats<=1): ltrb of the ROI
        """
        full_width = self.ebeam.shape[0:2]
        scale = self.scale

        rel_center = [(roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2]

        # There is not necessarily an exact number of pixels fitting in the ROI,
        # so need to update the width.
        px_width = [rep[0] * scale[0], rep[1] * scale[1]]
        rel_width = [px_width[0] / full_width[0], px_width[1] / full_width[1]]

        roi = [rel_center[0] - rel_width[0] / 2,
               rel_center[1] - rel_width[1] / 2,
               rel_center[0] + rel_width[0] / 2,
               rel_center[1] + rel_width[1] / 2]

        return roi

    def calc_xy_pos(self, roi, rep):
        """
        Compute the X and Y positions of the ebeam
        px_center (2 floats): X and Y position of the ebeam in SEM referential (pixels)
        rep (2 ints): number of SR pixels in X and Y

        return (array of floats shape Y,X,2) positions in the ebeam coordinates
               (X, Y) in SEM referential for each spot to be scanned.
        """

        scale = self.scale
        full_width = self.ebeam.shape[0:2]

        # + scale/2 is to put the spot at the center of each pixel
        lt = [full_width[0] * (roi[0] - 0.5) + scale[0] / 2,
              full_width[1] * (roi[1] - 0.5) + scale[1] / 2]

        # Note: currently the semcomedi driver doesn't allow to move to the very
        # border, so any roi must be at least > 0.5  and below < rngs - 0.5,
        # which could happen if scale < 1 and ROI on the border.

        # Compute positions based on scale and repetition
        pos = numpy.ndarray((rep[1], rep[0], 2))  # Y, X, 2
        # TODO: this is slow, use numpy.linspace (cf semcomedi)
        for i in numpy.ndindex(rep[1], rep[0]):
            pos[i] = [lt[0] + i[1] * scale[0], lt[1] + i[0] * scale[1]]

        return pos

    def bleach_borders(self, roi):
        """
        Bleach the borders using the ebeam, and return the acquired SEM signal
        roi (0<=4 floats<=1): ltrb of the ROI
        return (DataArray) acquired SEM signal.
        """
        sem_pxs = self.ebeam.pixelSize.value
        ccd_pxs = self.get_ccd_pxs()

        rel_width = [roi[2] - roi[0], roi[3] - roi[1]]
        rel_center = [(roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2]

        px_width = [self.ebeam.shape[0] * rel_width[0], self.ebeam.shape[1] * rel_width[1]]
        px_center = [self.ebeam.shape[0] * (rel_center[0] - 0.5), self.ebeam.shape[1] * (rel_center[1] - 0.5)]

        # we want to set the ebeam scale to the same value we use for the bleaching experiment
        ebeam_scale = (self.subpxs / sem_pxs[0], self.subpxs / sem_pxs[1])  # this is also correct when tile_shape==(1,1)
        self.ebeam.scale.value = ebeam_scale

        # set the dwelltime to the same value used for bleaching
        self.ebeam.dwellTime.value = self.dt

        # calculate the width of the border in pixels (using the calculated e-beam scale)
        b_px = [int(max(1, MARGIN_BLEACHING*ccd_pxs[0] / (ebeam_scale[0]*sem_pxs[0]))),
                int(max(1, MARGIN_BLEACHING*ccd_pxs[1] / (ebeam_scale[1]*sem_pxs[1])))]  # in px at current scale

        # the center of the border is offset by half the roi, and half the border
        px_border_trans = [px_width[0]/2 + b_px[0]/2 * ebeam_scale[0],
                           px_width[1]/2 + b_px[1]/2 * ebeam_scale[1]]  # in px when scale=1
        px_roi_width = [int(px_width[0]/ebeam_scale[0]),
                        int(px_width[1]/ebeam_scale[1])]  # in px at current scale

        # The borders are acquired in the following order
        #
        #  11111111111
        #  11111111111
        #  11111111111
        #  222     333
        #  222 ROI 333
        #  222     333
        #  44444444444
        #  44444444444
        #  44444444444

        data1 = self.acquire_SEM([px_roi_width[0] + 2 * b_px[0], b_px[1]],
                                 [px_center[0], px_center[1] - px_border_trans[1]])
        data2 = self.acquire_SEM([b_px[0], px_roi_width[1]],
                                 [px_center[0] - px_border_trans[0], px_center[1]])
        data3 = self.acquire_SEM([b_px[0], px_roi_width[1]],
                                 [px_center[0] + px_border_trans[0], px_center[1]])
        data4 = self.acquire_SEM([px_roi_width[0] + 2 * b_px[0], b_px[1]],
                                 [px_center[0], px_center[1] + px_border_trans[1]])

        # In order to reassemble the borders into one figure, we need to fill the ROI
        zeros_ROI = numpy.zeros((px_roi_width[1], px_roi_width[0]), data1.dtype)

        # Concatenate the data to get the final image
        data = numpy.concatenate((data1, numpy.concatenate((data2, zeros_ROI, data3), 1), data4), 0)

        # set the metadata
        phys_roi = self.convert_roi_ratio_to_phys(roi)
        center = ((phys_roi[0] + phys_roi[2]) / 2,
                  (phys_roi[1] + phys_roi[3]) / 2)
        md = {model.MD_POS: center,
              model.MD_PIXEL_SIZE: (self.subpxs, self.subpxs),
              model.MD_DWELL_TIME: self.dt,
              model.MD_DESCRIPTION: "SEM border"}

        return model.DataArray(data, md)

    def acquire_SEM(self, resolution, translation):
        self.ebeam.resolution.value = resolution
        self.ebeam.translation.value = translation
        data = self.sed.data.get()
        data = numpy.array(data)
        return data

    def bleach_spot(self, pos):
        """
        Bleach one spot, by scanning over each subpixel around that spot
        pos (0<=2 floats): X/Y position of the e-beam in SEM coordinates
        return (ndarray): the SED acquisition during this bleaching
        """
        # position the ebeam
        self.ebeam.translation.value = (pos[0] - self.drift[0],
                                        pos[1] - self.drift[1])

        # .get() has an advantage over subscribe + unsubscribe to ensure the
        # ebeam stays at the spot (almost) just the requested dwell time
        # TODO: check in semcomedi that it really happens.
        data = self.sed.data.get()

        if data.shape != self.tile_shape[::-1]:
            logging.error("Expected to get a SEM image of %s, but acquired %s points",
                          self.tile_shape, data.shape[::-1])

        return data

    def get_fluo_image(self, lpower=None, exptime=None):
        """
        Acquire a CCD image and convert it into a intensity count
        return (DataArray): the light intensity (currently, the mean)
        """
        # TODO: see if there are better ways to measure the intensity (ex:
        # use average over multiple frames after discarding outliers to
        # compensate for cosmic rays)

        if lpower is None:
            lpower = self.lpower

        if exptime is None:
            exptime = self.exptime

        # Set exposure time
        self.ccd.exposureTime.value = exptime

        # The light is turned on by the specified value
        self.light.power.value[self.emission_index] = lpower

        # Acquire an image of the fluorescence
        data = self.ccd.data.get()

        # Turn off the light again
        self.light.power.value = self.light.power.range[0]
        time.sleep(0.1)  # wait 0.1s for fluo/phosphorescence to go out

        return data

    def acquire_and_save_optical_survey(self, fn, fn_pattern):
        """
        Check whether an overview image is required, then acquire the images by cycling through the different settings
        for exposuretime and power and finally store them.
        """
        fn_base, fn_ext = os.path.splitext(fn)

        if self.olpower:
            self.ccd.binning.value = (1, 1)

            for i, (olp, oexp) in enumerate(zip(self.olpower, self.oexptime)):
                logging.info("Acquiring optical survey nr %i, excitation: %s, power: %fW, exptime: %fs ", i,
                             olp, oexp)

                ccd_data = self.get_fluo_image(olp, oexp)
                self.save_data(ccd_data, "%s-%s-%i%s" % (fn_base, fn_pattern, i, fn_ext))

    def assemble_tiles(self, shape, data, roi, gridpitch):
        """
        Convert a series of tiles acquisitions into an image (2D)
        shape (2 x 0<ints): Number of tiles in the output (Y, X)
        data (ndarray of shape N, T, S): the values,
         ordered in blocks of TxS with X first, then Y. N = Y*X.
         Each element along N is tiled on the final data.
        roi (4 0<=floats<=1): ROI relative to the SEM FoV used to compute the
          spots positions
        return (DataArray of shape Y*T, X*S): the data with the correct metadata
        """
        N, T, S = data.shape
        Y, X = shape
        Ys = Y / gridpitch[1]
        Xs = X / gridpitch[0]

        if T == 1 and S == 1 and Ys == 1 and Xs == 1:
            # fast path: the data is already ordered
            arr = data
            # reshape to get a 2D image
            arr.shape = shape
        else:
            # need to reorder data by tiles

            # change N to g,g,Ys,Xs
            arr = data.reshape((gridpitch[1], gridpitch[0], Ys, Xs, T, S))
            # change to Ys, g, T, Xs, g, S by rolling axis
            arr = numpy.rollaxis(arr, 2, 0)  # result: Ys g g Xs T S
            arr = numpy.rollaxis(arr, 4, 2)  # result: Ys g T g Xs S
            arr = numpy.rollaxis(arr, 4, 3)  # result: Ys g T Xs g S
            # and apply the change in memory (= 1 copy)
            arr = numpy.ascontiguousarray(arr)
            # reshape to apply the tiles
            arr.shape = (Y * T, X * S)

        # set the metadata
        phys_roi = self.convert_roi_ratio_to_phys(roi)
        center = ((phys_roi[0] + phys_roi[2]) / 2,
                  (phys_roi[1] + phys_roi[3]) / 2)
        md = {model.MD_POS: center,
              model.MD_PIXEL_SIZE: (self.subpxs, self.subpxs)}

        return model.DataArray(arr, md)

    def estimate_acq_time_gridscan(self, shape):
        """
        Estimate the acquisition time for a single gridscan frame.
        shape (2 int): number of pixels to be acquired
        return (float): time (in s) of the total gridscan
        """
        num_spots = numpy.prod(shape)
        dt = self.ebeam.dwellTime.value
        sem_time = dt * num_spots * numpy.prod(self.tile_shape)

        return sem_time

    def estimate_acq_time(self, shape):
        """
        Estimate the acquisition time. Assumes the CCD is already configured for
        acquisition
        shape (2 int): number of pixels to be acquired in each position
        return (float): time (in s) of the total acquisition
        """
        num_spots = numpy.prod(shape)
        sem_time = self.dt * num_spots * numpy.prod(self.tile_shape)

        # TODO: update to account for gridscanning, and the updated ROI

        roi = self.roi  # 0->1 (ltrb)
        full_width = self.ebeam.resolution.range[1]  # == self.ebeam.shape[0:2]
        rel_width = [(roi[2] - roi[0]), (roi[3] - roi[1])]
        num_spots_2 = int(full_width[0] * rel_width[0]) * int(full_width[1] * rel_width[1])
        sem_time_2 = self.dtsem * num_spots_2

        res = self.ccd.resolution.value
        ro_time = numpy.prod(res) / self.ccd.readoutRate.value
        ccd_time = (self.ccd.exposureTime.value + ro_time) * num_spots

        return sem_time + sem_time_2 + ccd_time

    def on_ccd_data(self, df, data):
        self.ccd.data.unsubscribe(self.on_ccd_data)
        self._spotsimage = data
        self.ccd_acq_complete.set()

    def save_data(self, data, fn):
        """
        Saves the data into a file
        data (model.DataArray or list of model.DataArray): the data to save
        fn (unicode): filename of the file to save
        """
        exporter = dataio.find_fittest_converter(fn)

        if os.path.exists(fn):
            # mostly to warn if multiple ypos/xpos are rounded to the same value
            logging.warning("Overwriting file '%s'.", fn)
        else:
            logging.info("Saving file '%s'", fn)

        exporter.export(unicode(fn), data)

    def acquire(self, fn):
        self.save_hw_settings()

        # it's not possible to keep in memory all the CCD images, so we save
        # them one by one in separate files (fn-ccd-XXXX.tiff)
        fn_base, fn_ext = os.path.splitext(fn)

        try:
            cycles = 1

            anchor = self.anchor

            logging.info("Target ROI is [{:.3f},{:.3f},{:.3f},{:.3f}]".format(*self.roi))

            rep = self.calc_rep(self.roi)  # returns nr of SR pixels in x,y
            rep = self.check_gridpitch(rep, self.gridpitch)

            roi = self.update_roi(self.roi, rep)
            logging.info(
                "Updated ROI to [{:.3f},{:.3f},{:.3f},{:.3f}], in order to fit integer number of SR spots".format(*roi))

            spots = self.calc_xy_pos(roi, rep)  # spots are defined
            shape = spots.shape[0:2]

            logging.info("Will scan %d x %d pixels.", shape[1], shape[0])
            logging.info("Using %d x %d gridscans.", self.gridpitch[0], self.gridpitch[1])

            dur = self.estimate_acq_time(shape)
            logging.info("Estimated acquisition time: %s",
                         units.readable_time(round(dur)))

            # Let's go!
            logging.info("Acquiring full image 0")
            self.acquire_and_save_optical_survey(fn, "fullBefore")

            logging.info("Starting with bleaching a border around the ROI")
            sem_border = self.bleach_borders(roi)
            self.save_data(sem_border, "%s-SEM_bleached_border%s" % (fn_base, fn_ext))

            ccd_roi = self.sem_roi_to_ccd(roi)
            ccd_roi = [ccd_roi[0] - MARGIN_ACQ, ccd_roi[1] - MARGIN_ACQ,
                       ccd_roi[2] + MARGIN_ACQ, ccd_roi[3] + MARGIN_ACQ]
            ccd_roi_idx = (slice(ccd_roi[1], ccd_roi[3] + 1),
                           slice(ccd_roi[0], ccd_roi[2] + 1))  # + 1 because we want to include the corners of the ROI

            self.configure_ccd(ccd_roi)
            self.configure_sem_for_tile()

            # start with the original fluorescence count (no bleaching)
            ccd_data = self.get_fluo_image()
            ccd_data_to_save = ccd_data[ccd_roi_idx]

            # Set up the drift correction (using the dwell time used for overview)
            if anchor:
                logging.info("Starting with pre-exposing the anchor region")
                de = drift.AnchoredEstimator(self.ebeam, self.sed, anchor, self.dtsem)
                for ii in range(13):
                    de.acquire()

                de = drift.AnchoredEstimator(self.ebeam, self.sed, anchor, self.dtsem)
                px_iter = de.estimateCorrectionPeriod(self.dperiod, 1.0, self.gridpitch)
                de.acquire()  # original anchor region
                self.save_data(de.raw[-1], "%s%02d-driftAnchor-%05d%s" % (fn_base, 0, 0, fn_ext))
                self.driftlog = [(0, 0)]
                self.drifttime = [time.time()]

                next_dc = px_iter.next()

            for ii in range(cycles):
                # TODO: fix this super-arbitrary dwell time change
                if ii >= 1:
                    self.ebeam.dwellTime.value = 2 * self.dt

                n = 0
                sem_data = []

                # save the fluorescence without bleaching (or end of previous cycle)
                self.save_data(ccd_data_to_save, "%s%02d-ccd-%05d%s" % (fn_base, ii, n, fn_ext))
                for g in numpy.ndindex(self.gridpitch):
                    n += 1

                    spotssubset = spots[g[0]::self.gridpitch[0], g[1]::self.gridpitch[1], :]
                    subsetshape = spotssubset.shape[0:2]

                    dur_frame = self.estimate_acq_time_gridscan(subsetshape)
                    overhead_est = 1 * dur_frame + 0.1

                    self.ccd.exposureTime.value = dur_frame + overhead_est
                    logging.info("Setting exposuretime for spotsimage to: %s (includes %s overhead)",
                                 units.readable_time((dur_frame + overhead_est)),
                                 units.readable_time((overhead_est)))
                    self.ccd_acq_complete.clear()
                    self.ccd.data.subscribe(self.on_ccd_data)
                    time.sleep(0.2)

                    logging.info("Bleaching pixels (%d,%d) + (%d,%d)*(0 -> %d, 0 -> %d)",
                                 g[1], g[0],
                                 self.gridpitch[1], self.gridpitch[0],
                                 subsetshape[1] - 1, subsetshape[0] - 1)
                    for i in numpy.ndindex(subsetshape):
                        bl_subset = self.bleach_spot(spotssubset[i].tolist())
                        sem_data.append(bl_subset)

                    self.ccd_acq_complete.wait()
                    if self.ccd_acq_complete.is_set():
                        logging.info("SEM bleaching took longer than expected")

                    spotsimage_to_save = self._spotsimage[ccd_roi_idx]
                    self.save_data(spotsimage_to_save, "%s%02d-spots-%05d%s" % (fn_base, ii, n, fn_ext))

                    ccd_data = self.get_fluo_image()
                    # ccd_data_to_save = ccd_data
                    ccd_data_to_save = ccd_data[ccd_roi_idx]
                    self.save_data(ccd_data_to_save, "%s%02d-ccd-%05d%s" % (fn_base, ii, n, fn_ext))

                    if anchor:
                        # Check whether the drift-correction needs to take place
                        if n >= next_dc:
                            de.acquire()  # take a new
                            d = de.estimate()

                            self.drift = (self.drift[0] + d[0], self.drift[1] + d[1])
                            logging.info("Drift estimated to {:.1f}, {:.1f} px".format(*self.drift))
                            next_dc = n + px_iter.next()

                            self.save_data(de.raw[-1], "%s%02d-driftAnchor-%05d%s" % (fn_base, ii, n, fn_ext))

                            self.drifttime.append(time.time())
                            self.driftlog.append(self.drift)

                # Reconstruct the complete images
                logging.info("Reconstructing SEM image from tiles")
                sem_array = numpy.array(sem_data)  # put everything in a big array
                sem_final = self.assemble_tiles(shape, sem_array, roi, self.gridpitch)
                sem_final.metadata[model.MD_DWELL_TIME] = self.dt
                sem_final.metadata[model.MD_DESCRIPTION] = "SEM"
                self.save_data(sem_final, "%s%02d_SEM_during_scan%s" % (fn_base, ii, fn_ext))

                # acquire a full image again after bleaching
            logging.info("Acquiring full image 1")
            self.acquire_and_save_optical_survey(fn, "fullAfter")

            if anchor:
                de.acquire()  # take a new
                d = de.estimate()

                self.drift = (self.drift[0] + d[0], self.drift[1] + d[1])
                logging.info("Drift estimated to {:.1f}, {:.1f} px".format(*self.drift))
                self.save_data(de.raw[-1], "%s-driftAnchor-final%s" % (fn_base, fn_ext))

            # take the SEM image again with specified dwell time and step size
            logging.info("Acquiring SEM survey")
            sem_survey_data = self.acquire_sem_survey(roi)
            sem_survey_data.metadata[model.MD_DESCRIPTION] = "SEM survey"
            self.save_data(sem_survey_data, "%s_SEM_survey%s" % (fn_base, fn_ext))

        finally:
            self.resume_hw_settings()
            self.light.power.value = self.light.power.range[0]  # makes sure light won't stay on


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser(description=
                                     "SEM fluorescence bleaching map")

    parser.add_argument("--dt", dest="dt", type=float, required=True,
                        help="ebeam (bleaching) dwell time in s (for each sub-pixel)")
    parser.add_argument("--dtsem", dest="dtsem", type=float, required=True,
                        help="SEM dwell time in s, for taking the SEM image after "
                             "bleaching procedure")
    parser.add_argument("--pxs", dest="pxs", type=float, required=True,
                        help="distance between 2 consecutive spots in nm")
    parser.add_argument("--subpx", dest="subpx", type=int, default=1,
                        help="number of sub-pixels scanned by the ebeam for "
                             "each pixel acquired by the CCD. Must be a "
                             "square of an integer.")
    parser.add_argument("--gridpitch", dest="gridpitch", type=int, default=0,
                        help="pitch for gridscanning in number of SR pixels."
                             "Setting pitch to 0 disables gridscanning.")
    parser.add_argument("--roi", dest="roi", required=True,
                        help="e-beam ROI positions (ltrb, relative to the SEM "
                             "field of view)")
    parser.add_argument("--lpower", dest="lpower", type=float, default=0.02,
                        help="excitation light power on the first fluorescence pixel (in W).")
    parser.add_argument("--emission", dest="emission", type=int, default=0,
                        help="emissions source (numbered from 0 to n-1) used "
                             "for the SR pixels.")
    parser.add_argument("--exptime", dest="exptime", type=float, default=2.0,
                        help="exposure time used for the SR pixels (in seconds).")
    parser.add_argument("--ovw-power", dest="olpower", type=float, nargs="+", default=[],
                        help="excitation light power used for overview image"
                             "that is acquired before and after the scan. There"
                             "can be several power values corresponding to different"
                             "emission sources, each of them will be used"
                             "successively")
    parser.add_argument("--ovw-emission", dest="oemission", type=int, nargs="+", default=[],
                        help="emissions source (numbered from 0 to n-1) used "
                             "for overview image that is acquired before and "
                             "after the scan. There can be several emission sources"
                             "each of them will be used successively")
    parser.add_argument("--ovw-exp-time", dest="oexptime", type=float, nargs="+", default=[],
                        help="exposure time that is used for overview image"
                             "that is acquired before and after the scan."
                             "There can be several exposure times corresponding"
                             "to different emission sources and they will be used "
                             "successively")
    parser.add_argument("--output", dest="filename", required=True,
                        help="name of the output file. It should finish by"
                             ".h5 (for HDF5) or .tiff (for TIFF).")
    parser.add_argument("--drift", dest="drift", type=float, default=None,
                        help="apply drift correction every nth SR pixel")
    parser.add_argument("--anchor", dest="anchor", default=None,
                        help="top-left and bottom-right points of the anchor region")

    options = parser.parse_args(args[1:])

    roi = conversion.reproduce_typed_value([1.0], options.roi)
    if not all(0 <= r <= 1 for r in roi):
        raise ValueError("roi values must be between 0 and 1")

    if not len(options.olpower) == len(options.oemission) == len(options.oexptime):
        raise ValueError(
            "overviewlightpower, overviewlightemissions and overviewexposuretime should have the same number of arguments.")

    if options.anchor is None or options.drift is None:
        anchor = None
    else:
        anchor = conversion.reproduce_typed_value([1.0], options.anchor)
        if not all(0 <= a <= 1 for a in anchor):
            raise ValueError("anchor values must be between 0 and 1")

    a = Acquirer(options.dt, options.dtsem, roi, options.pxs * 1e-9, options.subpx, options.gridpitch, options.lpower,
                 options.emission, options.exptime, options.olpower, options.oemission, options.oexptime,
                 options.drift, anchor)
    a.acquire(options.filename)


if __name__ == '__main__':
    try:
        main(sys.argv)
    except ValueError as e:
        logging.error(e)
        ret = 127
    except Exception:
        logging.exception("Error while running the acquisition")
        ret = 128
    else:
        ret = 0
    exit(ret)

