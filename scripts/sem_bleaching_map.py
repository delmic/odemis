#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 20 Mar 2014

@author: Éric Piel

This is a script to scan a region with a e-beam and observe the reduction
of fluorescence on an optical image for each point scanned (due to bleaching).

run as:
./scripts/sem_bleaching_map.py --dt=0.01 --roi=0.1,0.1,0.2,0.2 --pxs=7800 --subpx=49 --output filename.h5

--dt defines the dwell time when scanning (time required to bleach one spot/sub-pixel)
--roi the top-left and bottom-right points of the region to scan (relative to
      the SEM field of view).
--pxs the distance between the centers of two consecutive spots, in nm. It 
      should be compatible with the current SEM magnification.
--subpx the number of subpixels (must be a square of a integer).
      For each pixel acquired by the CCD, each subpixel is scanned by the ebeam.
--output indicates the name of the file which will contain all the output. It 
         should finish by .h5 (for HDF5) or .tiff (for TIFF).
--lpower defines the excitation light power on the first fluorescence pixel. The value can be 
		 between 0.004 and 0.4 (in W). Power will be gradually scaled up to the maximum (0.4 W) 
         on the last pixel to compensate photobleaching effect in the signal drop.

You first need to run the odemis backend with the SECOM config. For instance,
start Odemis, and close the graphical interface. Alternatively you can start
just the back-end with a command such as:
odemisd --log-level 2 install/linux/usr/share/odemis/secom-tud.odm.yaml

To change some configuration settings, you can use the cli:
# ensure the magnification is correct
odemis-cli --set-attr "EBeam ExtXY" magnification 5000
# to select the CCD exposure time:
odemis-cli --set-attr "Clara" exposureTime 0.1 # in s
# to select the excitation wavelength (light source)
odemis-cli --set-attr "Spectra" emissions "0,0,1,0"
'''
from __future__ import division

import argparse
import copy
import logging
import math
import numpy
from odemis import model, dataio, util
from odemis.util import driver, units
from odemis.util import img
import os
import sys

CCD_ROI = False # If True, the CCD ROI will be set to fit the SEM ROI.
# Otherwise the whole image will be taken.

logging.getLogger().setLevel(logging.INFO) # put "DEBUG" level for more messages

class Acquirer(object):
    def __init__(self, dt, roi, pxs, subpx=1, lpower=0.2):
        """
        pxs (0<float): distance in m between center of each tile 
        subpx (1<=int): number of sub-pixels
        """
        if subpx < 1 or (math.sqrt(subpx) % 1) != 0:
            raise ValueError("subpx must be square of an integer")
        
        self.dt = dt
        self.roi = roi
        subpx_x = math.trunc(math.sqrt(subpx))
        self.pxs = pxs
        self.subpxs = pxs / subpx_x
        logging.info("Sub-pixels will be spaced by %f nm", self.subpxs * 1e9)
        self.tile_shape = (subpx_x, subpx_x) # for now always the same in x and y
        self.lpower = lpower

        # Get the components we need
        self.sed = model.getComponent(role="se-detector")
        self.ebeam = model.getComponent(role="e-beam")
        self.light = model.getComponent(role="light")
        self.ccd = model.getComponent(role="ccd")
        self.stage = model.getComponent(role="stage")
        
        lprng = self.light.power.range
        if not (lprng[0] < lpower < lprng[1]):
            raise ValueError("starting light power value must be between %s", lprng)

        # counter-intuitively, to get the smaller pixel size, no sub-pixel
        # should be used. That's because when there is no sub-pixel we can use
        # spot mode, which allows to go at less than scale=1.
        # It could easily be fixed by allowsing scale down to 0.1 in the driver.

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

        self._hw_settings = () # will be used to save/resume SEM settings

    def get_sem_fov(self):
        """
        Returns the (theoretical) scanning area of the SEM. Works even if the
        SEM has not sent any image yet.
        returns (tuple of 4 floats): position in physical coordinates m (l, t, b, r)
        """
        try:
            pos = self.stage.position.value
            center = (pos["x"], pos["y"])
        except KeyError:
            # no info, not problem => just relative to the center of the SEM
            center = (0, 0)

        sem_width = (self.ebeam.shape[0] * self.ebeam.pixelSize.value[0],
                     self.ebeam.shape[1] * self.ebeam.pixelSize.value[1])
        sem_rect = [center[0] - sem_width[0] / 2, # left
                    center[1] - sem_width[1] / 2, # top
                    center[0] + sem_width[0] / 2, # right
                    center[1] + sem_width[1] / 2] # bottom
        # TODO: handle rotation?

        return sem_rect

    def get_ccd_fov(self):
        """
        Returns the (theoretical) field of view of the CCD.
        returns (tuple of 4 floats): position in physical coordinates m (l, t, b, r)
        """
        # The only way to get the right info is to look at what metadata the
        # images will get
        md = copy.copy(self.ccd.getMetadata())
        img.mergeMetadata(md) # apply correction info from fine alignment

        shape = self.ccd.shape[0:2]
        pxs = md[model.MD_PIXEL_SIZE]
        # compensate for binning
        binning = self.ccd.binning.value
        pxs = [p / b for p, b in zip(pxs, binning)]
        center = md.get(model.MD_POS, (0, 0))

        width = (shape[0] * pxs[0], shape[1] * pxs[1])
        phys_rect = [center[0] - width[0] / 2, # left
                     center[1] - width[1] / 2, # top
                     center[0] + width[0] / 2, # right
                     center[1] + width[1] / 2] # bottom

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
        logging.info("ROI defined at %s m", phys_rect)

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
            # centered. => Compute the minimal centered rectangle that includes the
            # roi.
            center = [s / 2 for s in self.ccd.shape[0:2]]
            hwidth = (max(abs(roi[0] - center[0]), abs(roi[2] - center[0])),
                      max(abs(roi[1] - center[1]), abs(roi[3] - center[1])))
    #        big_roi = (center[0] - hwidth[0], center[1] - hwidth[1],
    #                   center[0] + hwidth[0], center[1] + hwidth[1])
            res = [int(math.ceil(w * 2)) for w in hwidth]

            self.ccd.resolution.value = self.ccd.resolution.clip(res)

            # maximum binning (= resolution = 1 px for the whole image)
            # CCD resolution is automatically updated to fit
            self.ccd.binning.value = self.ccd.binning.clip(res)

            # FIXME: it's a bit tricky for the user to have the binning set
            # automatically, while the exposure time is fixed.

            # TODO: check that the physical area of the final ROI is still within
            # the original ROI
        else:
            self.ccd.resolution.value = self.ccd.resolution.range[1]
            logging.info("Using the whole CCD area")

        logging.info("CCD res = %s, binning = %s",
                      self.ccd.resolution.value,
                      self.ccd.binning.value)
        # Just to be sure, turn off the light
        self.light.power.value = 0

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

        # TODO: check this will allow the spots to be regularly spaced between
        # tiles
        if self.tile_shape == (1, 1):
            # scale doesn't matter, so just use 1
            self.ebeam.scale.value = (1, 1)
        else:
            # scale is the ratio between the goal pixel size and pixel size at scale=1
            sem_pxs = self.ebeam.pixelSize.value
            scale = (self.subpxs / sem_pxs[0], self.subpxs / sem_pxs[1])
            self.ebeam.scale.value = scale
        self.ebeam.resolution.value = self.tile_shape # just a spot

    def calc_xy_pos(self, roi, pxs):
        """
        Compute the X and Y positions of the ebeam
        roi (0<=4 floats<=1): ltrb of the ROI
        pxs (float): distance between each pixel (in m, in both directions) 
        return (array of floats shape Y,X,2) positions in the ebeam coordinates
               (X, Y) in SEM referential for each spot to be scanned.
        """
        # position is expressed in pixels, within the .translation ranges
        full_width = self.ebeam.shape[0:2]
        sem_pxs = self.ebeam.pixelSize.value
        scale = (pxs / sem_pxs[0], pxs / sem_pxs[1]) # it's ok to have something a bit < 1
        
        rel_width = [roi[2] - roi[0], roi[3] - roi[1]]
        rel_center = [(roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2]
        
        px_width = [full_width[0] * rel_width[0], full_width[1] * rel_width[1]]
        px_center = [full_width[0] * (rel_center[0] - 0.5),
                     full_width[1] * (rel_center[1] - 0.5)]
        
        # number of points to scan
        rep = [int(max(1, px_width[0] / scale[0])),
               int(max(1, px_width[1] / scale[1]))]  
        
        # There is not necessarily an exact number of pixels fitting in the ROI,
        # so need to update the width.
        px_width = [rep[0] * scale[0], rep[1] * scale[1]]
        # + scale/2 is to put the spot at the center of each pixel
        lt = [px_center[0] - px_width[0] / 2 + scale[0] / 2,
              px_center[1] - px_width[1] / 2 + scale[1] / 2]
        
        # Note: currently the semcomedi driver doesn't allow to move to the very
        # border, so any roi must be at least > 0.5  and below < rngs - 0.5,
        # which could happen if scale < 1 and ROI on the border.
        
        # Compute positions based on scale and repetition
        pos = numpy.ndarray((rep[1], rep[0], 2)) # Y, X, 2
        # TODO: this is slow, use numpy.linspace (cf semcomedi)
        for i in numpy.ndindex(rep[1], rep[0]):
            pos[i] = [lt[0] + i[1] * scale[0], lt[1] + i[0] * scale[1]]

        return pos
    
    def bleach_spot(self, pos):
        """
        Bleach one spot, by scanning over each subpixel around that spot
        pos (0<=2 floats): X/Y position of the e-beam in SEM coordinates
        return (ndarray): the SED acquisition during this bleaching 
        """
        # position the ebeam
        self.ebeam.translation.value = pos
        
        # .get() has an advantage over subscribe + unsubscribe to ensure the
        # ebeam stays at the spot (almost) just the requested dwell time
        # TODO: check in semcomedi that it really happens.
        data = self.sed.data.get()
        
        if data.shape != self.tile_shape[::-1]:
            logging.error("Expected to get a SEM image of %s, but acquired %s points",
                          self.tile_shape, data.shape[::-1])

        return data

    def get_fluo_image(self):
        """
        Acquire a CCD image and convert it into a intensity count
        return (DataArray): the light intensity (currently, the mean)
        """
        # TODO: see if there are better ways to measure the intensity (ex:
        # use average over multiple frames after discarding outliers to 
        # compensate for cosmic rays)
        
        # Turn on the light
        # TODO: allow user to specify power (instead of using the maximum)
        self.light.power.value = self.lpower
        
        # Acquire an image of the fluorescence
        data = self.ccd.data.get()
        
        # Turn off the light again
        self.light.power.value = 0

        return data

    def ccd_image_to_count(self, data, roi):
        """
        Convert a CCD image into a one value (count)
        return (float): the light intensity (currently, the mean)
        """
        # TODO: crop to the actual CCD ROI
        # compute intensity
        # We use a view to compute the mean to ensure to get a float (and not
        # a DataArray of one value)
        # TODO: divide by physical area size, in order to have normalised values
        # between acquisitions?
#        pxs = data.metadata[model.MD_PIXEL_SIZE]
#        area = numpy.prod(data.shape) * numpy.prod(pxs) # m²
        datar = data[roi[1]:roi[3] + 1, roi[0]:roi[2] + 1]

        intensity = datar.view(numpy.ndarray).mean()
        return intensity


    def assemble_tiles(self, shape, data, roi, pxs):
        """
        Convert a series of tiles acquisitions into an image (2D)
        shape (2 x 0<ints): Number of tiles in the output (Y, X)
        data (ndarray of shape N, T, S): the values, 
         ordered in blocks of TxS with X first, then Y. N = Y*X.
         Each element along N is tiled on the final data.
        roi (4 0<=floats<=1): ROI relative to the SEM FoV used to compute the
          spots positions
        pxs (0<float): distance (in m) between 2 tile centers, used to compute the 
          spots positions
        return (DataArray of shape Y*T, X*S): the data with the correct metadata
        """
        N, T, S = data.shape
        if T == 1 and S == 1:
            # fast path: the data is already ordered
            arr = data
            # reshape to get a 2D image
            arr.shape = shape
        else:
            # need to reorder data by tiles
            Y, X = shape
            # change N to Y, X
            arr = data.reshape((Y, X, T, S))
            # change to Y, T, X, S by moving the "T" axis
            arr = numpy.rollaxis(arr, 2, 1)
            # and apply the change in memory (= 1 copy)
            arr = numpy.ascontiguousarray(arr)
            # reshape to apply the tiles
            arr.shape = (Y * T, X * S)

        # set the metadata
        phys_roi = self.convert_roi_ratio_to_phys(roi)
        center = ((phys_roi[0] + phys_roi[2]) / 2,
                  (phys_roi[1] + phys_roi[3]) / 2)
        md = {model.MD_POS: center,
              model.MD_PIXEL_SIZE: (pxs / S, pxs / T)}
        
        return model.DataArray(arr, md)

    def estimate_acq_time(self, shape):
        """
        Estimate the acquisition time. Assumes the CCD is already configured for
        acquisition
        shape (2 int): number of pixels to be acquired in each position
        return (float): time (in s) of the total acquisition
        """
        num_spots = numpy.prod(shape)
        sem_time = self.dt * num_spots * numpy.prod(self.tile_shape)
        
        res = self.ccd.resolution.value
        ro_time = numpy.prod(res) / self.ccd.readoutRate.value
        ccd_time = (self.ccd.exposureTime.value + ro_time) * num_spots
        
        return sem_time + ccd_time
    
    def save_data(self, data, fn):
        """
        Saves the data into a file
        data (model.DataArray or list of model.DataArray): the data to save
        fn (unicode): filename of the file to save
        """
        exporter = dataio.find_fittest_converter(fn)
        
        # TODO: put the first data in a StaticStream to get a thumbnail
    
        if os.path.exists(fn):
            # mostly to warn if multiple ypos/xpos are rounded to the same value
            logging.warning("Overwriting file '%s'.", fn)
        else:
            logging.info("Saving file '%s'", fn)
    
        exporter.export(fn, data)

    def acquire(self, fn):
        self.save_hw_settings()
        
        # it's not possible to keep in memory all the CCD images, so we save
        # them one by one in separate files (fn-ccd-XXXX.h5)
        fn_base, fn_ext = os.path.splitext(fn)

        try:
            spots = self.calc_xy_pos(self.roi, self.pxs)
            shape = spots.shape[0:2]
            logging.info("Will scan %d x %d pixels.", shape[1], shape[0])
            
            ccd_roi = self.sem_roi_to_ccd(self.roi)
            self.configure_ccd(ccd_roi)
            self.configure_sem_for_tile()
            
            dur = self.estimate_acq_time(shape)
            logging.info("Estimated acquisition time: %s",
                         units.readable_time(round(dur)))
            
            # TODO: acquire a "Optical survey" image?

            # Let's go!
            sem_data = []
            # start with the original fluorescence count (no bleaching)

            n = 0
            ccd_data = self.get_fluo_image()
            self.save_data(ccd_data, "%s-ccd-%05d%s" % (fn_base, n, fn_ext))
            ccd_count = [self.ccd_image_to_count(ccd_data, ccd_roi)]
            try:
                # TODO: support drift correction (cf ar_spectral_ph)
                for i in numpy.ndindex(shape):
                    logging.info("Acquiring pixel %d,%d", i[1], i[0])
                    sem_data.append(self.bleach_spot(spots[i].tolist()))
                    ccd_data = self.get_fluo_image()
                    n += 1
                    self.save_data(ccd_data, "%s-ccd-%05d%s" % (fn_base, n, fn_ext))
                    ccd_count.append(self.ccd_image_to_count(ccd_data, ccd_roi))
                
                # TODO: acquire a "SEM survey" image?
                 
                # Reconstruct the complete images
                logging.info("Reconstructing final images")
                sem_array = numpy.array(sem_data) # put everything in a big array
                sem_final = self.assemble_tiles(shape, sem_array, self.roi, self.pxs)
                sem_final.metadata[model.MD_DWELL_TIME] = self.dt
                sem_final.metadata[model.MD_DESCRIPTION] = "SEM"
                
                # compute the diff
                ccd_count = numpy.array(ccd_count) # force to be one big array
                ccd_diff = ccd_count[0:-1] - ccd_count[1:]
                ccd_diff.shape += (1, 1) # add info that each element is one pixel
                ccd_final = self.assemble_tiles(shape, ccd_diff, self.roi, self.pxs)
                ccd_md = self.ccd.getMetadata()
                ccd_final.metadata[model.MD_IN_WL] = ccd_md[model.MD_IN_WL]
                ccd_final.metadata[model.MD_DESCRIPTION] = "Light diff"
                
#                 ccd_data = model.DataArray(ccd_data)
#                 ccd_data.metadata[model.MD_EXP_TIME] = self.ccd.exposureTime.value
#                 ccd_data.metadata[model.MD_DESCRIPTION] = "Raw CCD intensity"
            except Exception:
                # TODO: try to save the data as is
                raise
            
            # Save the data
            data = [sem_final, ccd_final]
            self.save_data(data, fn)
        finally:
            self.resume_hw_settings()


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser(description=
                     "SEM fluorescence bleaching map")

    parser.add_argument("--dt", "-d", dest="dt", type=float, required=True,
                        help="ebeam (bleaching) dwell time in s (for each sub-pixel)")
    parser.add_argument("--pxs", "-p", dest="pxs", type=float, required=True,
                        help="distance between 2 spots in nm")
    parser.add_argument("--subpx", "-s", dest="subpx", type=int, default=1,
                        help="number of sub-pixels scanned by the ebeam for "
                            "each pixel acquired by the CCD. Must be a the "
                            "square of an integer.")
    parser.add_argument("--roi", dest="roi", required=True,
                        help="e-beam ROI positions (ltrb, relative to the SEM "
                             "field of view)")
    parser.add_argument("--lpower", "-lp", dest="lpower", type=float, default=0.02,
                        help="excitation light power on the first fluorescence pixel. "
                            "Choose the value between 0.004 and 0.4 (in W). "
                            "Power will be gradually scaled up to the maximum (0.4 W) "
                            "on the last pixel to compensate photobleaching effect "
                            "in the signal drop.")
    parser.add_argument("--output", "-o", dest="filename", required=True,
                        help="name of the file output")

    options = parser.parse_args(args[1:])

    roi = driver.reproduceTypedValue([1.0], options.roi)
    if not all(0 <= r <= 1 for r in roi):
        raise ValueError("roi values must be between 0 and 1")

    a = Acquirer(options.dt, roi, options.pxs * 1e-9, options.subpx, options.lpower)
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

