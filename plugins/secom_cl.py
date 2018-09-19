#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 26 Jun 2013

@author: Éric Piel

This is a script to acquire a set of images from the CCD from various e-beam
spots on the sample along a grid.
Can also be used as a plugin.


run as:
./secom_cl --xrep 45 --yrep 5 --prefix filename-prefix

--prefix indicates the beginning of the filename.
The files are saved in TIFF, with the y, x positions (in nm) in the name.

'''

from __future__ import division

import argparse
from collections import OrderedDict
import copy
import itertools
import logging
import math
import numpy
from odemis import dataio, model, util, gui
import odemis
from odemis.acq import stream
from odemis.gui.conf import get_acqui_conf
from odemis.gui.plugin import Plugin, AcquisitionDialog
from odemis.util import img
import os.path
import sys
import threading
import time


# Exposure time of the AR CCD
EXP_TIME = 1 # s
# Binning for the AR CCD
BINNING = (1, 1) # px, px

# file format
FMT = "TIFF"
# Filename format
FN_FMT = u"%(prefix)s_grid=%(xres)dx%(yres)d_stepsize=%(stepsize).2fnm_n=%(idx)03d.tiff"

def get_ccd_md(ccd):
    """
    Returns the Metadata associated with the ccd, including fine alignment corrections.
    """
    # The only way to get the right info is to look at what metadata the
    # images will get
    md = copy.copy(ccd.getMetadata())
    img.mergeMetadata(md) # apply correction info from fine alignment

    return md

def get_ccd_pxs(ccd):
    """
    Returns the (theoretical) pixelsize of the CCD (projected on the sample).
    """
    md = get_ccd_md(ccd)

    pxs = md[model.MD_PIXEL_SIZE]
    # compensate for binning
    binning = ccd.binning.value
    pxs = [p / b for p, b in zip(pxs, binning)]

    return pxs

def get_ccd_fov(ccd):
    """
    Returns the (theoretical) field of view of the CCD.
    returns (tuple of 4 floats): position in physical coordinates m (l, t, b, r)
    """
    pxs = get_ccd_pxs(ccd)
    center = (0, 0)
    shape = ccd.shape[0:2]
    width = (shape[0] * pxs[0], shape[1] * pxs[1])
    logging.info("CCD width: " + str(width))
    logging.info("CCD shape: " + str(shape))
    logging.info("CCD pxs: " + str(pxs))
    logging.info("CCD center: " + str(pxs))

    phys_rect = [center[0] - width[0] / 2, # left
                 center[1] - width[1] / 2, # top
                 center[0] + width[0] / 2, # right
                 center[1] + width[1] / 2] # bottom

    return phys_rect

def get_sem_fov(ebeam):
    """
    Returns the (theoretical) scanning area of the SEM. Works even if the
    SEM has not sent any image yet.
    returns (tuple of 4 floats): position in physical coordinates m (l, t, b, r)
    """
    center = (0, 0)

    sem_width = (ebeam.shape[0] * ebeam.pixelSize.value[0],
                 ebeam.shape[1] * ebeam.pixelSize.value[1])
    sem_rect = [center[0] - sem_width[0] / 2, # left
                center[1] - sem_width[1] / 2, # top
                center[0] + sem_width[0] / 2, # right
                center[1] + sem_width[1] / 2] # bottom
    # TODO: handle rotation?

    return sem_rect

def convert_roi_ratio_to_phys(escan, roi):
    """
    Convert the ROI in relative coordinates (to the SEM FoV) into physical
     coordinates
    roi (4 floats): ltrb positions relative to the FoV
    return (4 floats): physical ltrb positions
    """
    sem_rect = get_sem_fov(escan)
    sem_rect = [x*1.5 for x in sem_rect] # Hack to allow for rotated SEM
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
    Convert the ROI in physical coordinates into a CCD ROI (in pixels)
    roi (4 floats): ltrb positions in m
    return (4 ints or None): ltrb positions in pixels, or None if no intersection
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
    shape = ccd.shape[0:2]
    pxroi = (int(proi[0] * shape[0]),
             int(proi[1] * shape[1]),
             int(math.ceil(proi[2] * shape[0])),
             int(math.ceil(proi[3] * shape[1])),
             )

    logging.info("pxroi: " + str(pxroi))

    # Limit the ROI to the one visible in the FoV
    trunc_roi = util.rect_intersect(pxroi, (0, 0) + shape)
    if trunc_roi is None:
        return None
    if trunc_roi != pxroi:
        logging.warning("CCD FoV doesn't cover the whole ROI, it would need "
                        "a ROI of %s in CCD referential.", pxroi)

    return trunc_roi

def sem_roi_to_ccd(escan, ccd, roi):
    """
    Converts a ROI defined in the SEM referential a ratio of FoV to a ROI
    which should cover the same physical area in the optical FoV.
    roi (0<=4 floats<=1): ltrb of the ROI
    return (0<=4 int): ltrb pixels on the CCD, when binning == 1
    """
    # convert ROI to physical position
    phys_rect = convert_roi_ratio_to_phys(escan,roi)
    logging.info("ROI defined at ({:.3e}, {:.3e}, {:.3e}, {:.3e}) m".format(*phys_rect))

    # convert physical position to CCD
    ccd_roi = convert_roi_phys_to_ccd(ccd, phys_rect)
    if ccd_roi is None:
        logging.error("Failed to find the ROI on the CCD, will use the whole CCD")
        ccd_roi = (0, 0) + ccd.shape[0:2]
    else:
        logging.info("Will use the CCD ROI %s", ccd_roi)

    return ccd_roi


class GridAcquirer(object):

    def __init__(self, res):
        """
        res (int, int): number of pixel in X and Y
        """
        self.res = res
        self.escan = model.getComponent(role="e-beam")
        try:
            self.edet = model.getComponent(role="se-detector")
        except LookupError:
            self.edet = model.getComponent(role="bs-detector")
        self.ccd = model.getComponent(role="ccd")

        self._must_stop = False

        self._ccd_data = []
        self._ccd_data_received = threading.Event()
        self._sem_data = []
        self._sem_data_received = threading.Event()

        self._hw_settings = None

    def save_hw_settings(self):

        res = self.escan.resolution.value
        scale = self.escan.scale.value
        trans = self.escan.translation.value
        dt = self.escan.dwellTime.value
        self._hw_settings = (res, scale, trans, dt)

    def resume_hw_settings(self):
        res, scale, trans, dt = self._hw_settings

        # order matters!
        self.escan.scale.value = scale
        self.escan.resolution.value = res
        self.escan.translation.value = trans
        self.escan.dwellTime.value = dt

    def calc_xy_pos(self):
        """
        Compute the X and Y positions of the ebeam
        Note: contrarily to usual, the Y is scanned fast, and X slowly
        returns: xyps (list of float,float): X/Y positions in the ebeam coordinates
                 pixelsize (float, float): pixelsize in m
        """
        # position is expressed in pixels, within the .translation ranges
        rngs = self.escan.translation.range
        # Note: currently the semcomedi driver doesn't allow to move to the very
        # border, even if when fuzzing is disabled, so need to remove one pixel
        widths = [rngs[1][0] - rngs[0][0] - 1, rngs[1][1] - rngs[0][1] - 1]
        stepsize = min(widths[0] / (self.res[0] - 1), widths[1] / (self.res[1] - 1))
        stepsizem = self.convert_xy_pos_to_m(stepsize, stepsize)
        logging.info("stepsize = %g nm", stepsizem[0] * 1e9)

        xps = []
        if self.res[0] == 1:
            xps.append(0)
        else:
            for n in range(self.res[0]):  # n: 0, 1 ,2 ,3 ,4 N_X-1
                x = n - ((self.res[0] - 1) / 2)  # distance from the iteration center => -2, -1, 0, 1, 2
                xps.append(stepsize * x)

        yps = []
        if self.res[1] == 1:
            yps.append(0)
        else:
            for n in range(self.res[1]):
                y = n - ((self.res[1] - 1) / 2)  # distance from the iteration center
                yps.append(stepsize * y)

        return list(itertools.product(xps, yps)), stepsizem

    def convert_xy_pos_to_m(self, x, y):
        """
        Convert a X and Y positions in m from the center
        Note: the SEM magnification must be calibrated
        x, y (floats)
        returns: xm, ym (floats): distance from the center in m
        """
        pxs = self.escan.pixelSize.value
        return x * pxs[0], y * pxs[1]

    def start_spot(self):
        """
        Start spot mode
        """
        # put a not too short dwell time to avoid acquisition to keep repeating,
        # and not too long to avoid using too much memory for acquiring one point.
        # Note: on the Delphi, the dwell time in spot mode is longer than what
        # is reported (fixed to ~50ms)
        dt = self.ccd.exposureTime.value / 2
        self.escan.dwellTime.value = self.escan.dwellTime.clip(dt)

        # only one point
        self.escan.scale.value = (1, 1)  # just to be sure
        self.escan.resolution.value = (1, 1)

        # subscribe to the data forever, which will keep the spot forever, but synchronised
        self.edet.data.synchronizedOn(self.edet.softwareTrigger)  # Wait for a trigger between each "scan" (of 1x1)
        self.edet.data.subscribe(self._receive_sem_data)

    def move_spot(self, x, y):
        """
        Move spot to a given position.
        It should already be started in spot mode
        x, y (floats): X, Y position
        """
        self._sem_data = []
        self._sem_data_received.clear()

        # Move the spot
        self.escan.translation.value = (x, y)
        # checks the hardware has accepted it
        act_tr = self.escan.translation.value
        if math.hypot(x - act_tr[0], y - act_tr[1]) > 1e-3:  # Anything below a thousand of a pixel is just float error
            logging.warning("Trans = %s instead of %s, will wait a bit" % (act_tr, (x, y)))
            # FIXME: why could waiting help? the semcomedi driver immediately sets the value
            time.sleep(0.1)
            act_tr = self.escan.translation.value
            if math.hypot(x - act_tr[0], y - act_tr[1]) > 1e-3:  # Anything below a thousand of a pixel is just float error
                raise IOError("Trans = %s instead of %s" % (act_tr, (x, y)))

        self.edet.softwareTrigger.notify()  # Go! (for one acquisition, and then the spot will stay there)

    def stop_spot(self):
        """
        Stop spot mode
        """
        # unsubscribe to the data, it will automatically stop the spot
        self.edet.data.unsubscribe(self._receive_sem_data)
        self.edet.data.synchronizedOn(None)

    def _receive_sem_data(self, df, data):
        """
        Store SEM data (when scanning spot mode typically)
        """
        self._sem_data.append(data)
        self._sem_data_received.set()
        if data.shape != (1,1):
            logging.warning("SEM data shape is %s while expected a spot", data.shape)

    def stop_acquisition(self):
        self._must_stop = True

    def start_ccd(self):
        self.ccd.data.synchronizedOn(self.ccd.softwareTrigger)
        self.ccd.data.subscribe(self._receive_ccd_data)

    def stop_ccd(self):
        self.ccd.data.unsubscribe(self._receive_ccd_data)
        self.ccd.data.synchronizedOn(None)

    def _receive_ccd_data(self, df, data):
        """
        Store CCD data
        """
        self._ccd_data.append(data)
        self._ccd_data_received.set()

    def acquire_ar(self, x, y, ccd_roi_idx):
        """
        Acquire an image from the CCD while having the e-beam at a spot position
        x, y (floats): spot position in the ebeam coordinates
        ccd_roi_idx: slice to crop the CCD image
        return (model.DataArray of shape (Y,X), model.DataArray of shape (1,1)):
          the CCD image and the SEM data (at the spot)
        """
        self.move_spot(x, y)

        # Start next CCD acquisition
        self._ccd_data = []
        self._ccd_data_received.clear()
        self.ccd.softwareTrigger.notify()

        # Wait for the CCD
        expt = self.ccd.exposureTime.value
        if not self._ccd_data_received.wait(expt * 2 + 2):
            raise IOError("Timed out: No CCD data received in time")

        if len(self._ccd_data) != 1:
            logging.warning("Received %d CCD data, while expected 1", len(self._ccd_data))
        d = self._ccd_data[0]
        d = d[ccd_roi_idx]  # crop

        if not self._sem_data_received.wait(3):
            logging.warning("No SEM data received, 3s after the CCD data")
        if len(self._sem_data) > 1:
            logging.warning("Received %d SEM data, while expected just 1", len(self._sem_data))

        return d, self._sem_data[0]

    def acquire_grid(self, fn_prefix):
        """
        returns (int): number of positions acquired
        """
        xyps, stepsizem = self.calc_xy_pos()
        logging.debug("Will scan on X/Y positions %s", xyps)

        self.save_hw_settings()

        # Uses the whole FoV of the CCD (and later we will crop it)
        # TODO: use translation and resolution to do the cropping
        self.ccd.resolution.value = self.ccd.resolution.range[1]
        expt = self.ccd.exposureTime.value

        # TODO: allow to select the ROI
        ccd_roi = sem_roi_to_ccd(self.escan, self.ccd, (0, 0, 1, 1))
        ccd_roi = [ccd_roi[0], ccd_roi[1],
                   ccd_roi[2], ccd_roi[3]]
        logging.info("ccd roi: %s", ccd_roi)
        ccd_roi_idx = (slice(ccd_roi[1], ccd_roi[3] + 1),
                       slice(ccd_roi[0], ccd_roi[2] + 1))  # + 1 to include the corners of the ROI

        sed_linear = []
        self.start_spot()
        self.start_ccd()
        n_pos = 0
        try:
            for x, y in xyps:
                xm, ym = self.convert_xy_pos_to_m(x, y)
                logging.info("Acquiring at position (%+f, %+f)", xm * 1e9, ym * 1e9)

                startt = time.time()
                d, sed = self.acquire_ar(x, y, ccd_roi_idx)
                endt = time.time()
                logging.debug("Took %g s (expected = %g s)", endt - startt, expt)

                sed_linear.append(sed)
                self.save_data(d, prefix=fn_prefix, xres=self.res[0], yres=self.res[1],
                               stepsize=stepsizem[0] * 1e9, idx=n_pos)
                n_pos += 1
                if self._must_stop:
                    logging.info("Stopping on request, after %d acquisitions", n_pos)
                    return
        finally:
            self.stop_ccd()
            self.stop_spot()
            self.resume_hw_settings()

        logging.debug("Assembling SEM data")
        fullsed = numpy.array(sed_linear)
        fullsed.shape = self.res  # numpy scans the last dim first
        fullsed = fullsed.T  # Get X as last dim, which is the numpy/Odemis convention
        md = sed_linear[0].metadata.copy()
        # md[model.MD_POS] # TODO: compute the center of the ROI
        md[model.MD_PIXEL_SIZE] = stepsizem
        fullsed = model.DataArray(fullsed, md)
        self.save_data(fullsed, prefix=fn_prefix + "_sem", xres=self.res[0], yres=self.res[1],
                       stepsize=stepsizem[0] * 1e9, idx=0)

    def save_data(self, data, **kwargs):
        """
        Saves the data into a file
        data (model.DataArray or list of model.DataArray): the data to save
        kwargs (dict (str->value)): values to substitute in the file name
        """
        exporter = dataio.get_converter(FMT)
        fn = FN_FMT % kwargs

        if os.path.exists(fn):
            # mostly to warn if multiple ypos/xpos are rounded to the same value
            logging.warning("Overwriting file '%s'.", fn)
        else:
            logging.info("Saving file '%s", fn)

        exporter.export(fn, data)


class CLAcqPlugin(Plugin):
    name = "CL acquisition for SECOM"
    __version__ = "1.0"
    __author__ = u"Éric Piel, Lennard Voortman"
    __license__ = "Public domain"

    # Describe how the values should be displayed
    # See odemis.gui.conf.data for all the possibilities
    vaconf = OrderedDict((
        ("xres", {
            "label": "Horiz. repetition",
            "control_type": odemis.gui.CONTROL_INT,  # no slider
            "accuracy": None,  # never simplify the numbers
        }),
        ("yres", {
            "label": "Vert. repetition",
            "control_type": odemis.gui.CONTROL_INT,  # no slider
            "accuracy": None,  # never simplify the numbers
        }),
        ("stepsize", {
            "control_type": odemis.gui.CONTROL_READONLY,
        }),
        ("exposureTime", {
            "range": (1e-6, 180),
            "scale": "log",
        }),
        ("binning", {
            "control_type": gui.CONTROL_RADIO,
        }),
        ("filename", {
            "control_type": odemis.gui.CONTROL_SAVE_FILE,
        }),
    ))

    def __init__(self, microscope, main_app):
        super(CLAcqPlugin, self).__init__(microscope, main_app)
        # Can only be used with a microscope
        if not microscope:
            return
        else:
            # Check which stream the microscope supports
            main_data = self.main_app.main_data
            if not (main_data.ccd and main_data.ebeam):
                return

        self.exposureTime = main_data.ccd.exposureTime
        self.binning = main_data.ccd.binning
        # Trick to pass the component (ccd to binning_1d_from_2d())
        self.vaconf["binning"]["choices"] = (lambda cp, va, cf:
                       gui.conf.util.binning_1d_from_2d(main_data.ccd, va, cf))
        self.xres = model.IntContinuous(10, (1, 1000), unit="px")
        self.yres = model.IntContinuous(10, (1, 1000), unit="px")
        self.stepsize = model.FloatVA(1e-6, unit="m")  # Just to show
        self.filename = model.StringVA("a.tiff")

        self.xres.subscribe(self._update_stepsize)
        self.yres.subscribe(self._update_stepsize)

        self.addMenu("Acquisition/CL acquisition...", self.start)

    def _get_new_filename(self):
        conf = get_acqui_conf()
        return os.path.join(
            conf.last_path,
            # u"%s%s" % (time.strftime("%Y%m%d-%H%M%S"), conf.last_extension)
            u"%s.tiff" % (time.strftime("%Y%m%d-%H%M%S"),)
        )

    def _get_sem_survey(self):
        """
        Finds the SEM survey stream in the acquisition tab
        return (SEMStream or None): None if not found
        """
        tab_data = self.main_app.main_data.tab.value.tab_data_model
        for s in tab_data.streams.value:
            if isinstance(s, stream.SEMStream):
                return s

        logging.warning("No SEM survey stream found")
        return None

    def _update_stepsize(self, _=None):
        """
        Update the stepsize based on X/Y repetition
        """

        escan = self.main_app.main_data.ebeam
        # Copy-paste of calc_xy_pos()
        rngs = escan.translation.range
        res = self.xres.value, self.yres.value
        # Note: currently the semcomedi driver doesn't allow to move to the very
        # border, even if when fuzzing is disabled, so need to remove one pixel
        widths = (rngs[1][0] - rngs[0][0] - 1, rngs[1][1] - rngs[0][1] - 1)

        if res == (1, 1):
            # When there is only one pixel, step size doesn't mean anything
            logging.info("Cannot compute pixel size when rep = 1x1")
            return

        stepsize = min(w / (r - 1) for w, r in zip(widths, res) if r > 1)
        pxs = escan.pixelSize.value
        self.stepsize.value = stepsize * pxs[0]

    def start(self):
        self.filename.value = self._get_new_filename()
        self._update_stepsize()

        dlg = AcquisitionDialog(self, "CL acquisition",
                                "Acquires a CCD image for each e-beam spot.\n")
        dlg.addSettings(self, self.vaconf)
        # dlg.addStream(self._get_sem_survey) # TODO: add survey + ROI selection tool
        dlg.addButton("Cancel")
        dlg.addButton("Acquire", self._acquire, face_colour='blue')
        ans = dlg.ShowModal()

        if ans == 0:
            logging.info("Acquisition cancelled")
        elif ans == 1:
            logging.info("Acquisition completed")
        else:
            logging.warning("Got unknown return code %s", ans)

        dlg.Destroy()

    def _acquire(self, dlg):
        acquirer = GridAcquirer((self.xres.value, self.yres.value))

        estt = self.xres.value * self.yres.value * (acquirer.ccd.exposureTime.value + 0.1) * 1.1
        f = model.ProgressiveFuture(end=time.time() + estt)
        f.task_canceller = lambda f: acquirer.stop_acquisition()  # To allow cancelling while it's running
        f.set_running_or_notify_cancel()  # Indicate the work is starting now
        dlg.showProgress(f)

        fn_prefix, fn_ext = os.path.splitext(self.filename.value)
        try:
            acquirer.acquire_grid(fn_prefix)
        except Exception:
            logging.exception("Failed to acquire the data")
        finally:
            f.set_result(None)  # Indicate it's over

        dlg.Close()


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser(description=
                         "Automated CL acquisition at multiple spot locations")

    parser.add_argument("--xrep", "-x", dest="xrep", type=int, required=True,
                        help="number of spots horizontally")
    parser.add_argument("--yrep", "-y", dest="yrep", type=int, required=True,
                        help="number of spots vertically")
    parser.add_argument("--prefix", "-p", dest="prefix", required=True,
                        help="prefix for the name of the files")

    logging.getLogger().setLevel(logging.INFO)  # put "DEBUG" level for more messages

    options = parser.parse_args(args[1:])
    fn_prefix = options.prefix
    xrep = options.xrep
    yrep = options.yrep

    try:
        acquirer = GridAcquirer((xrep, yrep))
        # configure CCD
        acquirer.ccd.exposureTime.value = EXP_TIME
        acquirer.ccd.binning.value = BINNING

        acquirer.acquire_grid(fn_prefix)
    except Exception:
        logging.exception("Unexpected error while performing action.")
        return 127

    return 0

if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)

