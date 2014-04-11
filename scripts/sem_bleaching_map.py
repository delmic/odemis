#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 20 Mar 2014

@author: Éric Piel

This is a script to scan a region with a e-beam and observe the reduction
of fluorescence on an optical image for each point scanned (due to bleaching).

run as:
./scripts/sem_bleaching_map.py --dt=0.1 --roi=0.1,0.2,0.1,0.2 --pxs=78 --12-thres=0.1 --output filename.h5

--dt defines the dwell time when scanning (time required to bleach one spot)
--roi the top-left and bottom-right points of the region to scan (relative to
      the SEM field of view).
--pxs the distance between the centers of two consecutive spots, in nm. It 
      should be compatible with the current SEM magnification.
--12-thres defines the threshold to pass from 1D scanning to 2D scanning in
          percentage of reduction of light intensity
--output indicates the name of the file which will contain all the output. It 
         should finish by .h5 (for HDF5) or .tiff (for TIFF).

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
import logging
import math
import numpy
from odemis import model, dataio
from odemis.util import driver, units
import os
import sys


class Acquirer(object):
    def __init__(self, dt, roi, pxs):
        """
        pxs (float): distance in m
        """
        self.dt = dt
        self.roi = roi
        self.pxs = pxs

        # Get the components we need
        self.sed = model.getComponent(role="se-detector")
        self.ebeam = model.getComponent(role="e-beam")
        self.light = model.getComponent(role="light")
        self.ccd = model.getComponent(role="ccd")
        self.stage = model.getComponent(role="stage")
        
        if self.ebeam.pixelSize.value[0] > 10 * pxs:
            raise ValueError("Pixel size requested (%g nm) is too small "
                             "compared to recommended SEM pixel size (%g nm)"
                             % (pxs * 1e9, self.ebeam.pixelSize.value[0] * 1e9))
            
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
        raises AttributeError in case no SEM is found
        """
        try:
            pos = self.stage.posision.value
            sem_center = (pos["x"], pos["y"])
        except KeyError:
            # no info, not problem => just relative to the center of the SEM
            sem_center = (0, 0)
        
        sem_width = (self.ebeam.shape[0] * self.ebeam.pixelSize.value[0],
                     self.ebeam.shape[1] * self.ebeam.pixelSize.value[1])
        sem_rect = [sem_center[0] - sem_width[0] / 2, # left
                    sem_center[1] - sem_width[1] / 2, # top
                    sem_center[0] + sem_width[0] / 2, # right
                    sem_center[1] + sem_width[1] / 2] # bottom

        return sem_rect

    def convert_roi_ratio_to_phys(self, roi):
        """
        Convert the ROI in relative coordinates (to the SEM FoV) into physical
         coordinates
        roi (4 floats): ltrb positions relative to the FoV
        return (4 floats): physical ltrb positions
        """
        sem_rect = self.get_sem_fov

        # In physical coordinates Y goes up, but in ROI, Y goes down => "1-"
        phys_rect = (sem_rect[0] + roi[0] * (sem_rect[2] - sem_rect[0]),
                     sem_rect[1] + (1 - roi[3]) * (sem_rect[3] - sem_rect[1]),
                     sem_rect[0] + roi[2] * (sem_rect[2] - sem_rect[0]),
                     sem_rect[1] + (1 - roi[1]) * (sem_rect[3] - sem_rect[1]))

        return phys_rect

    def sem_roi_to_ccd(self, roi):
        """
        Converts a ROI defined in the SEM referential a ratio of FoV to a ROI
        which should cover the same physical area in the optical FoV.
        roi (0<=4 floats<=1): ltrb of the ROI
        return (0<=4 int): ltrb pixels on the CCD, when binning == 1
        """
        # convert ROI to physical position
        phys_rect = self.convert_roi_ratio_to_phys(roi)

        # convert physical position to CCD
        ccd_sh = self.ccd.shape[0:2]
        # TODO: how? use calibration metadata? Rotation?

        # FIXME: for now we just return the whole CCD
        ccd_roi = (0, 0) + ccd_sh

        return ccd_roi

    def configure_ccd(self, roi):
        """
        Configure the CCD resolution and binning to have the minimum acquisition
        region that fit in the given ROI and with the maximum binning possible.
        roi (0<=4 int): ltrb pixels on the CCD, when binning == 1
        """
        # As translation is not possible, the acquisition region must be
        # centered. => Compute the minimal centered rectangle that includes the
        # roi.
        center = [s / 2 for s in self.ccd.shape[0:2]]
        hwidth = (max(abs(roi[0] - center[0]), abs(roi[2] - center[0])),
                  max(abs(roi[1] - center[1]), abs(roi[3] - center[1])))
#        big_roi = (center[0] - hwidth[0], center[1] - hwidth[1],
#                   center[0] + hwidth[0], center[1] + hwidth[1])
        res = [int(math.ceil(w * 2)) for w in hwidth]

        # Set the resolution (using binning 1, to use the direct values)
        self.ccd.binning.value = (1, 1)
        self.ccd.resolution.value = self.ccd.resolution.clip(res)

        # maximum binning (= resolution = 1 px for the whole image)
        # CCD resolution is automatically updated to fit
        self.ccd.binning.value = self.ccd.binning.clip(res)

        # FIXME: it's a bit tricky to have the binning set automatically, while
        # the exposure time is fixed.
        logging.debug("CCD res = %s, binning = %s",
                      self.ccd.resolution.value,
                      self.ccd.binning.value)
        
        # TODO: check that the physical area of the final ROI is still within
        # the original ROI
        
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

    
    def configure_sem_for_spot(self):
        """
        Configure the SEM to be able to acquire one spot
        """
        self.ebeam.dwellTime.value = self.dt
        # For spot, the easiest is to put the scale to 1, so translation can be
        # anywhere
        self.ebeam.scale.value = (1, 1)
        self.ebeam.resolution.value = (1, 1) # just a spot

    def calc_xy_pos(self, roi, pxs):
        """
        Compute the X and Y positions of the ebeam
        roi (0<=4 floats<=1): ltrb of the ROI
        pxs (float): distance between each pixel (in m, in both directions) 
        return (array of floats shape Y,X,2) positions in the ebeam coordinates
               (X, Y) in SEM referential for each spot to be scanned.
        """
        # position is expressed in pixels, within the .translation ranges
        rngs = self.ebeam.translation.range
        full_width = [rngs[1][0] - rngs[0][0], rngs[1][1] - rngs[0][1]]
        sem_pxs = self.ebeam.pixelSize.value
        scale = (pxs / sem_pxs[0], pxs / sem_pxs[1]) # it's ok to have something a bit < 1
        
        rel_width = [roi[2] - roi[0], roi[3] - roi[1]]
        rel_center = [(roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2]
        
        px_width = [full_width[0] * rel_width[0], full_width[1] * rel_width[1]]
        px_center = [full_width[0] * rel_center[0], full_width[1] * rel_center[1]]
        
        # number of points to scan 
        rep = [int(max(1, px_width[0] / scale[0])),
               int(max(1, px_width[1] / scale[1]))]  
        
        # There is not necessarily an exact number of pixels fitting in the ROI,
        # so need to update the width and center it.
        px_width = [rep[0] * scale[0], rep[1] * scale[1]]
        # + scale/2 is to put the spot at the center of each pixel
        lt = [px_center[0] - px_width[0] / 2 + scale[0] / 2,
              px_center[1] - px_width[1] / 2 + scale[1] / 2]
        
        # Note: currently the semcomedi driver doesn't allow to move to the very
        # border, so any roi must be at least > 0.5  and below < rngs - 0.5,
        # which could happen if scale < 1 and ROI on the border.
        
        # Compute positions based on scale and repetition
        pos = numpy.ndarray((rep[1], rep[0], 2)) # Y, X, 2
        for i in numpy.ndindex(rep):
            pos[i] = [lt[0] + i[1] * scale[0], lt[1] + i[0] * scale[1]]

        return pos
    
    def bleach_spot(self, pos):
        """
        Bleach one spot
        pos (0<=2 floats): X/Y position of the e-beam in SEM coordinates
        return (number): the SED acquisition during this bleaching 
        """
        # position the ebeam
        self.ebeam.translation = pos
        
        # .get() has an advantage over subscribe + unsubscribe to ensure the
        # ebeam stays at the spot (almost) just the requested dwell time
        # FIXME: this is not ensured, and it might take up to a 1s from time to
        # time!
        data = self.sed.data.get()
        
        if data.shape != (1, 1):
            logging.error("Expected to acquire a spot, but acquired %s points", 
                          data.shape)

        return data[0, 0]

    def get_fluo_count(self):
        """
        Acquire a CCD image and convert it into a intensity count
        return (float): the light intensity (currently, the mean)
        """
        # TODO: see if there are better ways to measure the intensity (ex:
        # use average over multiple frames after discarding outliers to 
        # compensate for cosmic rays)
        
        # Turn on the light
        # TODO: allow to use less power
        self.light.power.value = self.light.power.range[1]
        
        # Acquire an image of the fluorescence
        data = self.ccd.data.get()
        
        # Turn off the light again
        self.light.power.value = 0
        
        # compute intensity
        # We use a view to compute the mean to ensure to get a float (and not
        # a DataArray of one value)
        # TODO: divide by physical area size, in order to have normalised values
        # between acquisitions?
        intensity = data.view(numpy.ndarray).mean() 
        return intensity


#    def scan_line_per_spot(self):
#        # scans one line, spot per spot, returning a SED line and CCD light diff line
#        pass
#
#    def scan_line(self):
#        # scans one line, in one go, and returns a SED line and an (average) CCD light diff line
#        pass
#
#
#    def assemble_lines(self, lines):
#        """
#        Convert a series of lines (1D images) into an image (2D)
#        """
#        pass

    def assemble_spots(self, shape, data, roi, pxs):
        """
        Convert a series of spots acquisitions into an image (2D)
        shape (2 0<ints): Expected shape of the output (Y, X)
        data (list or ndarray of numbers): the values, ordered with X first
        roi (4 0<=floats<=1): ROI relative to the SEM FoV used to compute the
          spots positions
        pxs (0 float): distance (in m) between 2 spots used to compute the 
          spots positions
        return (DataArray): the data with the correct metadata
        """
        # Put all the data into one big array (should work even with a list of
        # DataArray of shape 1,1)
        arr = numpy.array(data)
        # reshape to get a 2D image
        arr.shape = shape
        
        # set the metadata
        phys_roi = self.convert_roi_ratio_to_phys(roi)
        center = ((phys_roi[0] + phys_roi[2]) / 2,
                  (phys_roi[1] + phys_roi[3]) / 2)
        md = {model.MD_POS: center,
              model.MD_PIXEL_SIZE: pxs}
        
        return model.DataArray(arr, md)

    def estimate_acq_time(self, shape):
        """
        Estimate the acquisition time. Assumes the CCD is already configured for
        acquisition
        shape (2 int): number of pixels to be acquired in each position
        return (float): time (in s) of the total acquisition
        """
        num_spots = numpy.prod(shape)
        sem_time = self.dt * num_spots
        
        res = self.ccd.resolutions.value
        ro_time = numpy.prod(res) / self.ccd.readoutRate.value
        ccd_time = (self.ccd.exposureTime.value + ro_time) * num_spots
        
        return sem_time + ccd_time
    
    def save_data(self, data, fn):
        """
        Saves the data into a file
        data (model.DataArray or list of model.DataArray): the data to save
        fn (unicode): filename of the file to save
        """
        exporter = dataio.find_fittest_exporter(fn)
        
        # TODO: put the first data in a StaticStream to get a thumbnail
    
        if os.path.exists(fn):
            # mostly to warn if multiple ypos/xpos are rounded to the same value
            logging.warning("Overwriting file '%s'.", fn)
        else:
            logging.info("Saving file '%s", fn)
    
        exporter.export(fn, data)

    def acquire(self, fn):
        self.save_hw_settings()

        try:
            spots = self.calc_xy_pos(self.roi, self.pxs)
            shape = spots.shape[0:2]
            logging.info("Will scan %d x %d pixels.", shape[0], shape[1])
            
            ccd_roi = self.sem_roi_to_ccd(self.roi)
            self.configure_ccd(ccd_roi)
            self.configure_sem_for_spot()
            
            dur = self.estimate_acq_time(shape)
            logging.info("Estimated acquisition time: %s",
                         units.readable_time(dur))
            
            # TODO: acquire a "Optical survey" image?

            # Let's go!
            sem_data = []
            # start with the original fluorescence count (no bleaching)
            ccd_data = [self.get_fluo_count()]
            try:
                for i, pos in enumerate(spots):
                    logging.info("Acquiring pixel %s", i)
                    sem_data.append(self.bleach_spot(pos))
                    ccd_data.append(self.get_fluo_count())
                
                # TODO: acquire a "SEM survey" image ?
                 
                # Reconstruct the complete images
                logging.info("Reconstructing final images")
                sem_final = self.assemble_spots(shape, sem_data, self.roi, self.pxs)
                sem_final.metadata[model.MD_DWELL_TIME] = self.dt
                sem_final.metadata[model.MD_DESCRIPTION] = "SEM"
                
                # compute the diff
                ccd_data = numpy.array(ccd_data) # force to be one big array
                ccd_diff = ccd_data[0:-2] - ccd_data[1:-1]
                ccd_final = self.assemble_spots(shape, ccd_diff, self.roi, self.pxs)
                ccd_final.metadata[model.MD_DESCRIPTION] = "Light diff"
                
                ccd_data = model.DataArray(ccd_data)
                ccd_data.metadata[model.MD_EXP_TIME] = self.ccd.exposureTime.value
                ccd_data.metadata[model.MD_DESCRIPTION] = "Raw CCD intensity" 
            except Exception:
                # TODO: try to save the data as is
                raise
            
            # Save the data
            data = [sem_final, ccd_final, ccd_data]
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
                        help="ebeam (bleaching) dwell time in s")
    parser.add_argument("--pxs", "-p", dest="pxs", type=float, required=True,
                        help="distance between 2 spots in nm")
    parser.add_argument("--roi", dest="roi", required=True,
                        help="e-beam ROI positions (ltrb, relative to the SEM "
                             "field of view)")
    parser.add_argument("--output", "-o", dest="filename", required=True,
                        help="name of the file output")

    options = parser.parse_args(args[1:])

    roi = driver.reproduceTypedValue([1.0], options.roi)
    if not all(0 <= r <= 1 for r in roi):
        raise ValueError("roi values must be between 0 and 1")

    a = Acquirer(options.dt, roi, options.pxs * 1e-9)
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

