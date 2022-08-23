# -*- coding: utf-8 -*-
"""
Created on Tue Jun 07 08:28:42 2016

@author: Toon Coenen

Plugin that allows 2D imaging with the time correlator

Gives ability to acquire a CL intensity stream using the AR camera.

Copyright © 2017 Toon Coenen, Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
"""

from collections import OrderedDict
from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, RUNNING
import logging
import math
import numpy
from odemis import dataio, model
from odemis.acq import stream, drift, acqmng
from odemis.acq.stream import UNDEFINED_ROI
import odemis.gui
from odemis.gui.conf import get_acqui_conf
from odemis.gui.plugin import Plugin, AcquisitionDialog
from odemis.util import executeAsyncTask
import os.path
from past.builtins import long
import threading
import time

import odemis.util.driver as udriver


# TODO: Include fuzzing
# All the acquisition code is in this standard Stream interface, so that it can
# be handled automatically by the Odemis acquisition manager.
class CorrelatorScanStream(stream.Stream):
    """
    Stream that allows to acquire a 2D spatial map with the time correlator for lifetime mapping or g(2) mapping 
    """
    def __init__(self, name, detector, sed, emitter, opm=None):
        """
        name (string): user-friendly name of this stream
        detector (Detector): the monochromator
        sed (Detector): the se-detector
        emitter (Emitter): the emitter (eg: ebeam scanner)
        spectrograph (Actuator): the spectrograph
        """
        self.name = model.StringVA(name)

        # Hardware Components, detector is the correlator, sed is the secondary electron image and the emitter is the electron beam
        self._detector = detector
        self._sed = sed
        self._emitter = emitter
        self._opm = opm

        self.is_active = model.BooleanVA(False)

        #dwell time and exposure time are the same thing in this case
        self.dwellTime = model.FloatContinuous(1, range=self._emitter.dwellTime.range,
                                               unit="s")
        # pixelDuration of correlator, this can be shortened once implemented as choices.
        self.pixelDuration = model.FloatEnumerated(512e-12,
                                choices={4e-12, 8e-12, 16e-12, 32e-12, 64e-12, 128e-12, 256e-12, 512e-12},
                                unit="s",
                                )
        #Sync Offset time correlator
        self.syncOffset = self._detector.syncOffset
        #Sync Divider time correlator
        self.syncDiv = self._detector.syncDiv 
        
        # Distance between the center of each pixel
        self.stepsize = model.FloatContinuous(1e-6, (1e-9, 1e-4), unit="m")

        # Region of acquisition. ROI form is LEFT Top RIGHT Bottom, relative to full field size
        self.roi = model.TupleContinuous((0, 0, 1, 1),
                                         range=((0, 0, 0, 0), (1, 1, 1, 1)),
                                         cls=(int, long, float))

        # Cropvalue that  can be used to crop the data for better visualization in odemis
        self.cropvalue = model.IntContinuous(1024, (1, 65536), unit="px")

        # For drift correction
        self.dcRegion = model.TupleContinuous(UNDEFINED_ROI,
                                              range=((0, 0, 0, 0), (1, 1, 1, 1)),
                                              cls=(int, long, float))
        self.dcDwellTime = model.FloatContinuous(emitter.dwellTime.range[0],
                                                 range=emitter.dwellTime.range, unit="s")
        #number of drift corrections per scanning pixel
        self.nDC = model.IntContinuous(1, (1, 20))

        # For acquisition
        self.tc_data = None
        self.tc_data_received = threading.Event()
        self.sem_data = []
        self.sem_data_received = threading.Event()
        self._hw_settings = None

    def acquire(self):
        """
        Runs the acquisition
        returns Future that will have as a result a DataArray with the 3D data
        """
        # Make sure the stream is prepared (= optical path set)
        # TODO: on the SPARCv1, which doesn't support time-correlator mode,
        # this fails => need to use a different (and supported) mode.
        self.prepare().result()

        est_start = time.time() + 0.1
        # Create a "Future", which is an object that can be used to follow the
        # task completion while it's going on, and get the result.
        f = model.ProgressiveFuture(start=est_start,
                                    end=est_start + self.estimateAcquisitionTime())
        f.task_canceller = self._cancelAcquisition
        f._acq_state = RUNNING
        f._acq_lock = threading.Lock()
        f._acq_done = threading.Event()

        # run task in separate thread
        executeAsyncTask(f, self._runAcquisition, args=(f,))
        return f

    def get_scan_res(self):
        sem_width = (self._emitter.shape[0] * self._emitter.pixelSize.value[0],
                     self._emitter.shape[1] * self._emitter.pixelSize.value[1])
        ROI = self.roi.value
        stepsize = self.stepsize.value
        # rounded resolution values (rounded down), note deal with resolution 0
        xres = ((ROI[2] - ROI[0]) * sem_width[0]) // stepsize
        yres = ((ROI[3] - ROI[1]) * sem_width[1]) // stepsize
        if xres == 0:
            xres = 1
        if yres == 0:
            yres = 1
        return int(xres), int(yres)

    def estimateAcquisitionTime(self):
        """
        Estimate the time it will take for the measurement. The number of pixels still has to be defined in the stream part
        """
        xres, yres = self.get_scan_res()
        npos = xres * yres

        dt = self.dwellTime.value * npos * 1.1

        # logic that only adds acquisition time for DC if a DC region is defined
        if self.dcRegion.value != UNDEFINED_ROI:
            dc = drift.AnchoredEstimator(self._emitter, self._sed,
                                         self.dcRegion.value, self.dcDwellTime.value)
            dctime = dc.estimateAcquisitionTime()
            nDC = self.nDC.value
            # time for spatial drift correction, for now we just assume that spatial drift correction is done every pixel but we could include actual number of scanned pixelsv
            dt += (npos * nDC + 1) * (dctime + 0.1)

        return dt

    # code that can cancel the acquisition
    def _cancelAcquisition(self, future):
        with future._acq_lock:
            if future._acq_state == FINISHED:
                return False  # too late
            future._acq_state = CANCELLED

        logging.debug("Cancelling acquisition of components %s and %s",
                      self._emitter.name, self._detector.name)

        self.tc_data_received.set()  # To help end quickly
        self._detector.data.unsubscribe(self._receive_tc_data)
        self.sem_data_received.set()
        self._sed.data.unsubscribe(self._receive_sem_data)

        # Wait for the thread to be complete (and hardware state restored)
        future._acq_done.wait(5)
        return True

    def _runAcquisition(self, future):

        self._detector.pixelDuration.value = self.pixelDuration.value
        logging.debug("Syncoffset used %s", self.syncOffset.value)
        logging.debug("SyncDiv used %s", self.syncDiv.value)

        # number of drift corrections per pixel
        nDC = self.nDC.value
        # semfov, physwidth = self._get_sem_fov()
        #xyps, stepsize = self._calc_xy_pos()
        xres, yres = self.get_scan_res()
        xyps = self.calc_xy_pos(self.roi.value, self.stepsize.value)
        logging.debug("Will scan on X/Y positions %s", xyps)

        #phys_rect = convert_roi_ratio_to_phys(escan,roi)
        measurement_n = 0
        cordata = []
        sedata = []
        NPOS = len(xyps)  # = xres * yres

        self._save_hw_settings()

        # a list (instead of a tuple) for the summation to work on each element independently
        tot_dc_vect = [0, 0]

        #check whether a drift region is defined
        if self.dcRegion.value != UNDEFINED_ROI:
            drift_est = drift.AnchoredEstimator(self._emitter, self._sed,
                                                self.dcRegion.value,
                                                self.dcDwellTime.value)
            drift_est.acquire()
        else:
            drift_est = None

        try:
            if drift_est:
                self._start_spot(nDC)
                # re-adjust dwell time for number of drift corrections
                self._detector.dwellTime.value = self.dwellTime.value / nDC
                self._emitter.dwellTime.value = self.dwellTime.value / nDC

                for x, y in xyps:
                    sedatapix = []
                    cordatapix = []

                    for ll in range(self.nDC.value):
                        # add total drift vector at this point
                        xc = x - tot_dc_vect[0]
                        yc = y - tot_dc_vect[1]

                        # check if drift correction leads to an x,y position outside of scan region
                        cx, cy = self._emitter.translation.clip((xc, yc))
                        if (cx, cy) != (xc, yc):
                            logging.error("Drift of %s px caused acquisition region out "
                                          "of bounds: needed to scan spot at %s.",
                                          tot_dc_vect, (xc, yc))
                        xc, yc = (cx, cy)
                        xm, ym = self._convert_xy_pos_to_m(xc, yc)
                        logging.info("Acquiring scan number %d at position (%g, %g), with drift correction of %s",
                                     ll + 1, xm, ym, tot_dc_vect)
                        startt = time.time()
                        cordat, sedat = self._acquire_correlator(xc, yc, self.dwellTime.value/nDC, future)
                        endt = time.time()
                        logging.debug("Took %g s (expected = %g s)", endt - startt, self.dwellTime.value/nDC)
                        cordatapix.append(cordat)
                        sedatapix.append(sedat)
                        logging.debug("Memory used = %d bytes", udriver.readMemoryUsage())
                        drift_est.acquire()
                        # drift correction vectors
                        dc_vect = drift_est.estimate()
                        tot_dc_vect[0] += dc_vect[0]
                        tot_dc_vect[1] += dc_vect[1]

                    measurement_n += 1
                    # TODO: update the future progress
                    logging.info("Acquired %d out of %d pixels", measurement_n, NPOS)

                    # Perform addition of measurements here which keeps other
                    # acquisitions the same and reduces memory required.
                    cordatam = numpy.sum(cordatapix, 0, dtype=numpy.float64)
                    # checks whether datavalue exceeds data-type range.
                    # Note: this works for integers only. For floats there is a separate numpy function
                    idt = numpy.iinfo(cordatapix[0].dtype)
                    # we can choose different things here. For now we just force to clip the signal
                    cordatam = numpy.clip(cordatam, idt.min, idt.max)
                    # convert back to right datatype and (re)add metadata
                    cordatam = model.DataArray(cordatam.astype(cordatapix[0].dtype), cordatapix[0].metadata)
                    cordata.append(cordatam)

                    # For SE data just use mean because absolute scale is not relevant
                    sedatam = numpy.mean(sedatapix).astype(sedatapix[0].dtype)
                    # The brackets are required to give enough dimensions to make the rest happy
                    sedatam = model.DataArray([[[[sedatam]]]], sedatapix[0].metadata)
                    sedata.append(sedatam)

            else:
                self._start_spot(1)
                for x, y in xyps:
                    self._detector.dwellTime.value = self.dwellTime.value
                    xm, ym = self._convert_xy_pos_to_m(x, y)
                    logging.info("Acquiring at position (%g, %g)", xm, ym)
                    startt = time.time()
                    # dwelltime is used as input for the acquisition because it is different for with drift and without
                    cordat, sedat = self._acquire_correlator(x, y, self.dwellTime.value, future)
                    endt = time.time()
                    logging.debug("Took %g s (expected = %g s)", endt - startt, self.dwellTime.value)
                    cordata.append(cordat)
                    sedata.append(sedat)
                    logging.debug("Memory used = %d bytes", udriver.readMemoryUsage())
                    # number of scans that have been done. Could be printed to show progress
                    measurement_n += 1
                    # TODO: update the future progress
                    logging.info("Acquired %d out of %d pixels", measurement_n, NPOS)

            self._stop_spot()
            stepsize = (self.stepsize.value, self.stepsize.value)
            cordata[0].metadata[model.MD_POS] = sedata[0].metadata[model.MD_POS]
            full_cordata = self._assemble_correlator_data(cordata, (xres, yres), self.roi.value, stepsize)
            full_sedata = self._assemble_sed_data(sedata, (xres, yres), self.roi.value, stepsize)

            if future._acq_state == CANCELLED:
                raise CancelledError()
            das = [full_cordata, full_sedata]
            if drift_est:
                das.append(self._assembleAnchorData(drift_est.raw))

            return das

        except CancelledError:
            logging.info("Time correlator stream cancelled")
            with future._acq_lock:
                self._acq_state = FINISHED
            raise  # Just don't log the exception
        except Exception:
            logging.exception("Failure during Correlator acquisition")
            raise
        finally:
            logging.debug("TC acquisition finished")
            # Make sure all detectors are stopped
            self._stop_spot()
            self._detector.data.unsubscribe(self._receive_tc_data)
            future._acq_done.set()
            self._resume_hw_settings()

    def _get_center_pxs(self, rep, roi, datatl):
        """
        rep
        roi
        datatl (DataArray): first data array acquired
        return:
            center (tuple of floats): position in m of the whole data
            pxs (tuple of floats): pixel size in m
        """
        # Pixel size is the size of field of view divided by the repetition
        emt_pxs = self._emitter.pixelSize.value
        emt_shape = self._emitter.shape[:2]
        fov = (emt_shape[0] * emt_pxs[0], emt_shape[1] * emt_pxs[1])
        rel_width = (roi[2] - roi[0], roi[3] - roi[1])
        pxs = (rel_width[0] * fov[0] / rep[0], rel_width[1] * fov[1] / rep[1])

        # Compute center of area, based on the position of the first point (the
        # position of the other points can be wrong due to drift correction)
        center_tl = datatl.metadata[model.MD_POS]
        tl = (center_tl[0] - (pxs[0] * (datatl.shape[-1] - 1)) / 2,
              center_tl[1] + (pxs[1] * (datatl.shape[-2] - 1)) / 2)
        center = (tl[0] + (pxs[0] * (rep[0] - 1)) / 2,
                  tl[1] - (pxs[1] * (rep[1] - 1)) / 2)
        logging.debug("Computed data width to be %s x %s",
                      pxs[0] * rep[0], pxs[1] * rep[1])
        return center  # , pxs

    def _assemble_correlator_data(self, cordata, resolution, roi, stepsize):
        """
        Assemble time-correlator data and metadata
        """
        #get metadata, no need to ask directly to the component because the metadata is already embedded in the first dataset
        md = cordata[0].metadata.copy()
        xres, yres = resolution
        md[model.MD_PIXEL_SIZE] = stepsize
        md[model.MD_POS] = self._get_center_pxs(resolution, roi, cordata[0])
        md[model.MD_DESCRIPTION] = "Time correlator"
        #force exposure time metadata to be full time on the pixel rather than dwelltime/nDC
        md[model.MD_DWELL_TIME] = self.dwellTime.value
        logging.debug("Assembling correlator data")
        full_cordata = model.DataArray(cordata, metadata=md)
        # reshaping matrix. This is probably a silly way but it works
        full_cordata = full_cordata.swapaxes(0, 3)
        full_cordata = full_cordata.swapaxes(1, 2)
        # Check XY ordering
        full_cordata = numpy.reshape(full_cordata, [1, full_cordata.shape[1], 1, yres, xres])
        #full_cordata = full_cordata.swapaxes(3, 4)
        full_cordata.metadata[model.MD_DIMS] = "CTZYX"
        return full_cordata

    def _assemble_sed_data(self,sedata,resolution,roi,stepsize):
        """
        Assemble sed data and metadata
        """
        #get metadata, no need to ask directly to the component because the metadata is already embedded in the first dataset
        mdescan = sedata[0].metadata.copy()
        xres, yres = resolution
        mdescan[model.MD_PIXEL_SIZE] = stepsize
        mdescan[model.MD_POS] = self._get_center_pxs(resolution, roi, sedata[0])
        mdescan[model.MD_DESCRIPTION] = "Secondary electrons"
        #force exposure time metadata to be full time on the pixel rather than dwelltime/nDC
        mdescan[model.MD_DWELL_TIME] = self.dwellTime.value
        # possibly merge the metadata for escan and sed.
        logging.debug("Assembling SEM data")
        full_sedata = model.DataArray(sedata, metadata=mdescan)
        full_sedata = full_sedata.swapaxes(0, 3)
        full_sedata = numpy.reshape(full_sedata, [1, 1, 1, yres, xres])
        #full_sedata = full_sedata.swapaxes(3, 4)
        return full_sedata

#    def _get_md(self):
#        """
#        Returns the Metadata associated with the correlator, ebeam scanning and sed
#        """
#        # The only way to get the right info is to look at what metadata the
#        # images will get
#        md = self._detector.getMetadata()
#        mdescan = self._emitter.getMetadata()
#        mdsed = self._sed.getMetadata()

#        return md, mdescan, mdsed

    def _assembleAnchorData(self, data_list):
        """
        Take all the data acquired for the anchor region

        data_list (list of N DataArray of shape 2D (Y, X)): all the anchor data
        return (DataArray of shape (1, N, 1, Y, X))
        """
        assert len(data_list) > 0
        assert data_list[0].ndim == 2

        # extend the shape to TZ dimensions to allow the concatenation on T
        for d in data_list:
            d.shape = (1, 1) + d.shape

        anchor_data = numpy.concatenate(data_list)
        anchor_data.shape = (1,) + anchor_data.shape

        # copy the metadata from the first image (which contains the original
        # position of the anchor region, without drift correction)
        md = data_list[0].metadata.copy()
        md[model.MD_DESCRIPTION] = "Anchor region"
        md[model.MD_AD_LIST] = tuple(d.metadata[model.MD_ACQ_DATE] for d in data_list)
        return model.DataArray(anchor_data, metadata=md)

    def _get_sem_fov(self):
        """
        Returns the (theoretical) scanning area of the SEM. Works even if the
        SEM has not sent any image yet.
        returns (tuple of 4 floats): position in physical coordinates m (l, t, b, r)
        """
        center = (0, 0)

        sem_width = (self._emitter.shape[0] * self._emitter.pixelSize.value[0],
                     self._emitter.shape[1] * self._emitter.pixelSize.value[1])
        sem_rect = [center[0] - sem_width[0] / 2, # left
                    center[1] - sem_width[1] / 2,  # top
                    center[0] + sem_width[0] / 2,  # right
                    center[1] + sem_width[1] / 2]  # bottom

        phys_width = (sem_rect[2] - sem_rect[0],
                      sem_rect[3] - sem_rect[1])

        return sem_rect, phys_width

    def calc_xy_pos(self, roi, pxs):
        """
        Compute the X and Y positions of the ebeam
        roi (0<=4 floats<=1): ltrb of the ROI
        pxs (float): distance between each pixel (in m, in both directions) 
        return (list of Y*X tuples of 2 floats) positions in the ebeam coordinates
               (X, Y) in SEM referential for each spot to be scanned.
        """
        # position is expressed in pixels, within the .translation ranges
        full_width = self._emitter.shape[0:2]
        sem_pxs = self._emitter.pixelSize.value
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
        #pos = numpy.ndarray((rep[1], rep[0], 2)) # Y, X, 2
        pos = []
        # TODO: this is slow, use numpy.linspace (cf semcomedi)
        for i in numpy.ndindex(rep[1], rep[0]):
            pos.append((lt[0] + i[1] * scale[0], lt[1] + i[0] * scale[1]))

        return pos

    def _convert_xy_pos_to_m(self, x, y):
        """
        Convert a X and Y positions into m from the center
        Note: the SEM magnification must be calibrated
        escan (model.Emitter): the e-beam scanner
        x, y (floats)
        returns: xnm, ynm (floats): distance from the center in nm
        """
        pxs = self._emitter.pixelSize.value
        # TODO: change to m
        return x * pxs[0], y * pxs[1]

    def _start_spot(self,nDC):
        """
       Start spot mode at a given position
       self._emitter): the e-beam scanner
       self._sed: SE detector
        """
        # put a not too short dwell time to avoid acquisition to keep repeating,
        # and not too long to avoid using too much memory for acquiring one point.
        self._emitter.dwellTime.value = self.dwellTime.value/nDC

        # only one point
        self._emitter.scale.value = (1, 1) # just to be sure
        self._emitter.resolution.value = (1, 1)

        # subscribe to the data forever, which will keep the spot forever, but synchronised
        # self._sed.data.synchronizedOn(self._sed.softwareTrigger) # Wait for a trigger between each "scan" (of 1x1)
        # self._sed.data.subscribe(self._receive_sem_data)

    def _move_spot(self, x, y):
        """
        Move spot to a given position.
        It should already be started in spot mode
        self._emitter): the e-beam scanner
        self._sed: SE detector
        x, y (floats): X, Y position
        """
        # Prepare to receive new data
        self.tc_data = None
        self.tc_data_received.clear()
        self.sem_data = []
        self.sem_data_received.clear()

        # Move the spot
        self._emitter.translation.value = (x, y)
        # checks the hardware has accepted it
        act_tr = self._emitter.translation.value

        if math.hypot(x-act_tr[0], y - act_tr[1]) > 1e-3: # Anything below a thousand of a pixel is just float error
            logging.warning("Ebeam trans = %s instead of requested %s", act_tr, (x, y))

        self._sed.data.subscribe(self._receive_sem_data)
        # self._sed.softwareTrigger.notify() # Go! (for one acquisition, and then the spot will stay there)

    def _pause_spot(self):
        self._sed.data.unsubscribe(self._receive_sem_data)

    def _stop_spot(self):
        """
        Stop spot mode
        self._emitter): the e-beam scanner
        self._sed: SE detector
        """

        # unsubscribe to the data, it will automatically stop the spot
        self._sed.data.unsubscribe(self._receive_sem_data)
        # self._sed.data.synchronizedOn(None)
        logging.debug("SED unsynchronized")

    def _receive_sem_data(self, df, data):
        """
        Store SEM data (when scanning spot mode typically)
        """
        self.sem_data.append(data)
        self.sem_data_received.set()

    def _receive_tc_data(self, df, data):
        """
        Store time-correlator data
        """
        self._detector.data.unsubscribe(self._receive_tc_data)
        self.tc_data = data
        self.tc_data_received.set()

    def _acquire_correlator(self, x, y,dwellT, future):
        """
        Acquire N images from the correlator while having the e-beam at a spot position
        escan (model.Emitter): the e-beam scanner
        edet (model.Detector): any detector of the SEM
        correlator  the time correlator
        x, y (floats): spot position in the ebeam coordinates
        """

        # TODO: maybe it is better to move these commands out of this function and into the master because these parameters should not change
        self._move_spot(x, y)

        # get correlator data
        startt = time.time()
        #dat = self._detector.data.get()
        self._detector.data.subscribe(self._receive_tc_data)
        timeout = 1 + dwellT * 1.5
        if not self.tc_data_received.wait(timeout):
            if future._acq_state == CANCELLED:
                raise CancelledError()
            logging.warning("No time-correlator data received, will retry")
            self._detector.data.unsubscribe(self._receive_tc_data)
            time.sleep(0.1)
            self._detector.data.subscribe(self._receive_tc_data)
            if not self.tc_data_received.wait(timeout):
                raise IOError("No time-correlator data received twice in a row")
        if future._acq_state == CANCELLED:
            raise CancelledError()

        dat = self.tc_data
        dat.shape += (1, 1)

        dur_cor = time.time() - startt
        if dur_cor < dwellT * 0.99:
            logging.error("Correlator data arrived after %g s, while expected at least %g s", dur_cor, dwellT)
        # wait for the SE data, in case it hasn't arrived yet
        if not self.sem_data_received.wait(3):
            logging.warning("No SEM data received, 3s after the correlator data")
        if not self.sem_data_received.wait(dwellT):
            raise IOError("No SEM data received")
        self._pause_spot()

        if future._acq_state == CANCELLED:
            raise CancelledError()

        if len(self.sem_data) > 1:
            logging.info("Received %d SEM data, while expected just 1", len(self.sem_data))

        sedat = self.sem_data[0]
        sedat.shape += (1, 1)

    # TODO: it might actually be better to just give the whole list, and
    # the exporter will take care of assembling the data, while keeping the
    # acquisition date correct for each image.

    # insert a new axis, for N

    # Make a DataArray with the metadata from the first point
    #full_data = model.DataArray(dat,metadata=md)

        return dat, sedat

    def _save_hw_settings(self):
        res = self._emitter.resolution.value
        scale = self._emitter.scale.value
        trans = self._emitter.translation.value
        dt = self._emitter.dwellTime.value
        self._hw_settings = (res, scale, trans, dt)

    def _resume_hw_settings(self):
        res, scale, trans, dt = self._hw_settings
        # order matters!
        self._emitter.scale.value = scale
        self._emitter.resolution.value = res
        self._emitter.translation.value = trans
        self._emitter.dwellTime.value = dt


# the plugin itself
class Correlator2D(Plugin):
    name = "CL time correlator acquisition"
    __version__ = "1.1"
    __author__ = "Toon Coenen"
    __license__ = "GNU General Public License 2"

    vaconf = OrderedDict((
        ("stepsize", {
            "tooltip": "Distance between the center of each pixel",
            "scale": "log",
        }),
        ("xres", {
            "control_type": odemis.gui.CONTROL_READONLY,
            "label": "x-resolution",
        }),
        ("yres", {
            "control_type": odemis.gui.CONTROL_READONLY,
            "label": "y-resolution",
        }),
        ("roi", {
            "control_type": odemis.gui.CONTROL_NONE, # TODO: CONTROL_READONLY to show it
        }),
        ("dwellTime", {
            "tooltip": "Time spent by the e-beam on each pixel",
            "range": (1e-9, 3600),
            "scale": "log",
        }),
        ("nDC", {
            "tooltip": "Number of drift corrections per pixel",
            "range": (1, 100),
            "label": "Drif cor. per pixel",
        }),
        ("pixelDuration", {
            "label": "Time resolution",
        }),
        ("syncOffset", {
        }),
        ("syncDiv", {
            "label": "Sync divider",
        }),
        ("cropvalue", {
            "accuracy": None,  # Don't round value in plugin UI
        }),
        ("filename", {
            "control_type": odemis.gui.CONTROL_SAVE_FILE,
        }),
        ("expectedDuration", {
        }),
    ))

    def __init__(self, microscope, main_app):
        # Called when the plugin is loaed (ie, at GUI initialisation)
        super(Correlator2D, self).__init__(microscope, main_app)
        if not microscope or microscope.role not in ("sparc", "sparc2"):
            return
        try:
            self.ebeam = model.getComponent(role="e-beam")
            self.correlator = model.getComponent(role="time-correlator")
            self.sed = model.getComponent(role="se-detector")

        except LookupError:
            logging.debug("Hardware not found, cannot use the plugin")
            return

        # the SEM survey stream (will be updated when showing the window)
        self._survey_s = None

        # Create a stream for correlator spectral measurement
        self._correlator_s = CorrelatorScanStream("Correlator data", self.correlator, self.sed, self.ebeam,
                                                  main_app.main_data.opm)

        # For reading the ROA and anchor ROI
        self._acqui_tab = main_app.main_data.getTabByName("sparc_acqui").tab_data_model

        self.dwellTime = self._correlator_s.dwellTime
        self.pixelDuration = self._correlator_s.pixelDuration
        self.syncOffset = self._correlator_s.syncOffset
        self.syncDiv = self._correlator_s.syncDiv

        # The scanning positions are defined by ROI (as selected in the acquisition tab) + stepsize
        # Based on these values, the scanning resolution is computed
        self.roi = self._correlator_s.roi
        self.stepsize = self._correlator_s.stepsize
        self.cropvalue = self._correlator_s.cropvalue
        self.xres = model.IntVA(1, unit="px")
        self.yres = model.IntVA(1, unit="px")

        self.nDC = self._correlator_s.nDC

        self.filename = model.StringVA("a.h5")
        self.expectedDuration = model.VigilantAttribute(1, unit="s", readonly=True)

        # Update the expected duration when values change, depends both dwell time and # of pixels
        self.dwellTime.subscribe(self._update_exp_dur)
        self.stepsize.subscribe(self._update_exp_dur)
        self.nDC.subscribe(self._update_exp_dur)

        # subscribe to update X/Y res
        self.stepsize.subscribe(self._update_res)
        self.roi.subscribe(self._update_res)

        self.addMenu("Acquisition/CL time correlator scan...", self.start)

    def _update_exp_dur(self, _=None):
        """
        Called when VA that affects the expected duration is changed
        """
        at = self._correlator_s.estimateAcquisitionTime()

        if self._survey_s:
            at += self._survey_s.estimateAcquisitionTime()

        # Use _set_value as it's read only
        self.expectedDuration._set_value(round(at), force_write=True)

    def _update_res(self, _=None):
        """
        Update the resolution based on the step size
        """

        sem_width = (self.ebeam.shape[0] * self.ebeam.pixelSize.value[0],
                     self.ebeam.shape[1] * self.ebeam.pixelSize.value[1])
        ROI = self.roi.value
        if ROI == UNDEFINED_ROI:
            ROI = (0, 0, 1, 1)
        logging.info("ROI = %s", ROI)
        stepsize = self.stepsize.value

        #rounded resolution values (rounded down), note deal with resolution 0
        xres = ((ROI[2] - ROI[0]) * sem_width[0]) // stepsize
        yres = ((ROI[3] - ROI[1]) * sem_width[1]) // stepsize

        if xres == 0:
            xres = 1
        if yres == 0:
            yres = 1
        self.xres.value = int(xres)
        self.yres.value = int(yres)

    def _get_new_filename(self):
        conf = get_acqui_conf()
        return os.path.join(
            conf.last_path,
            u"%s%s" % (time.strftime("%Y%m%d-%H%M%S"), ".h5") #conf.last_extension)
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
            else:
                logging.info("Skipping stream %s", s)

        logging.warning("No SEM survey stream found")
        return None

    def start(self):
        # get region and dwelltime for drift correction
        self._correlator_s.dcRegion.value = self._acqui_tab.driftCorrector.roi.value
        self._correlator_s.dcDwellTime.value = self._acqui_tab.driftCorrector.dwellTime.value

        # get survey
        self._survey_s = self._get_sem_survey()

        # For ROI:
        roi = self._acqui_tab.semStream.roi.value
        if roi == UNDEFINED_ROI:
            roi = (0, 0, 1, 1)
        self.roi.value = roi
        logging.debug("ROA = %s", self.roi.value)

        self._update_exp_dur()
        self._update_res()

        # Create a window
        dlg = AcquisitionDialog(self, "Time correlator acquisition",
                                "Acquires a scan using the time correlator\n"
                                "Specify the relevant settings and start the acquisition\n"
                                )

        self.filename.value = self._get_new_filename()
        dlg.addSettings(self, conf=self.vaconf)
        dlg.addButton("Close")
        dlg.addButton("Acquire", self.acquire, face_colour='blue')

        # Show the window, and wait until the acquisition is over
        ans = dlg.ShowModal()

        # The window is closed
        if ans == 0:
            logging.info("Correlator acquisition cancelled")
        elif ans == 1:
            logging.info("Correlator acquisition completed")
        else:
            logging.debug("Unknown return code %d", ans)

        dlg.Destroy()

    def acquire(self, dlg):
        # Stop the spot stream and any other stream playing to not interfere with the acquisition
        try:
            str_ctrl = self.main_app.main_data.tab.value.streambar_controller
        except AttributeError:  # Odemis v2.6 and earlier
            str_ctrl = self.main_app.main_data.tab.value.stream_controller
        stream_paused = str_ctrl.pauseStreams()

        strs = []
        if self._survey_s:
            strs.append(self._survey_s)

        strs.append(self._correlator_s)

        fn = self.filename.value
        exporter = dataio.find_fittest_converter(fn)

        try:
            f = acqmng.acquire(strs, self.main_app.main_data.settings_obs)
            dlg.showProgress(f)
            das, e = f.result()  # blocks until all the acquisitions are finished
        except CancelledError:
            pass
        finally:
            logging.debug("Resuming streams after end")
            str_ctrl.resumeStreams(stream_paused)
            logging.debug("Acquisition ended")

        if not f.cancelled() and das:
            if e:
                logging.warning("Correlator scan partially failed: %s", e)
            logging.debug("Will save data to %s", fn)
            logging.debug("Going to export data: %s", das)
            exporter.export(fn, das)

            self.showAcquisition(fn)

        if not f.cancelled():
            dlg.Close()
