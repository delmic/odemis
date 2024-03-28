# -*- coding: utf-8 -*-
"""
Created on Wed Jul 05 2017

@author: Toon Coenen and Eric Piel

Plugin that allows hyperspectral momentum CL imaging with drift correction.


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

# Odemis plugin taking a mixed angular/spectral image.
# For each point scanned (by the e-beam), it obtains N images of the zenithal angle θ vs the wavelength.
# Where N varies to scan the azimuthal angle φ.
# Note: it's not currently possible to display the data directly in Odemis. You
# will need to use Matlab or Python to analyse it.

# TODO: Include AR alignment and solid angle-correction values to get proper angular mapping.
# TODO: Include UI in which ROI and drift correction can be used as well


from __future__ import division

from collections import OrderedDict
from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, RUNNING
import logging
import math
import numpy
from odemis import dataio, model, acq
from odemis.acq import stream, drift, acqmng
from odemis.acq.stream import UNDEFINED_ROI
import odemis.gui
from odemis.gui.conf import get_acqui_conf
import os.path
import threading
import time
import odemis.util.driver as udriver
from odemis.gui.plugin import Plugin, AcquisitionDialog
from odemis.util import executeAsyncTask


class SpectralARScanStream(stream.Stream):
    """
    Stream that allows to acquire a spectrum by scanning the wavelength of a
    spectrograph and acquiring with a monochromator
    """
    def __init__(self, name, detector, sed, emitter, spectrograph, lens_switch, lens_mover,
                 bigslit, oa, opm):
        """
        name (string): user-friendly name of this stream
        detector (Detector): the monochromator
        sed (Detector): the se-detector
        emitter (Emitter): the emitter (eg: ebeam scanner)
        spectrograph (Actuator): the spectrograph
        """
        self.name = model.StringVA(name)

        # Hardware Components
        self._detector = detector
        self._sed = sed
        self._emitter = emitter
        self._sgr = spectrograph
        self._opm = opm
        self._lsw = lens_switch
        self._lsm = lens_mover
        self._bigslit = bigslit
        self._oa = oa

        wlr = spectrograph.axes["wavelength"].range
        slitw = spectrograph.axes["slit-in"].range
        self.centerWavelength = model.FloatContinuous(500e-9, wlr, unit="m")
        self.slitWidth = model.FloatContinuous(100e-6, slitw, unit="m")
        # dwell time and exposure time are the same thing in this case
        self.dwellTime = model.FloatContinuous(1, range=detector.exposureTime.range,
                                               unit="s")
        self.emtTranslation = model.TupleContinuous((0, 0),
                                                    range=self._emitter.translation.range,
                                                    cls=(int, float),
                                                    unit="px")

        # Distance between the center of each pixel
        self.stepsize = model.FloatContinuous(1e-6, (1e-9, 1e-4), unit="m")

        # Region of acquisition. ROI form is LEFT Top RIGHT Bottom, relative to full field size
        self.roi = model.TupleContinuous((0, 0, 1, 1),
                                         range=((0, 0, 0, 0), (1, 1, 1, 1)),
                                         cls=(int, float))

        # For drift correction
        self.dcRegion = model.TupleContinuous(UNDEFINED_ROI,
                                              range=((0, 0, 0, 0), (1, 1, 1, 1)),
                                              cls=(int, float))
        self.dcDwellTime = model.FloatContinuous(emitter.dwellTime.range[0],
                                                 range=emitter.dwellTime.range, unit="s")

        #lens 1 and 2 movement range and stepsize. For now we will just scan half the mirror. We will need to hardcode an initial step
        #In total we moved about 10000 microns for l1 and l2. In total that means 20000 microns which
        #corresponds to 0.02 m. A good stepsize would be 500 um
        self.l1l2range = model.FloatContinuous(0.02, (0, 0.025), unit="m")
        self.l1l2stepsize = model.FloatContinuous(500e-6, (1e-6, 0.025), unit="m")


        #self.binning = model.VAEnumerated((1,1), choices=set([(1,1), (2,2), (2,3)]))
        # separate binning values because it can useful for experiment
        self.binninghorz = model.VAEnumerated(1, choices={1, 2, 4, 8, 16})
        self.binningvert = model.VAEnumerated(1, choices={1, 2, 4, 8, 16})
        self.nDC = model.IntContinuous(1, (1, 20))

        # For acquisition
        self._acq_thread = None
        self.ARspectral_data = None
        self.ARspectral_data_received = threading.Event()
        self.sem_data = []
        self.sem_data_received = threading.Event()
        self._hw_settings = None

    def acquire(self):
        """
        Runs the acquisition
        returns Future that will have as a result a DataArray with the 3D data
        """
        # Make sure the stream is prepared (= optical path set)
        # TODO: move the optical path change done in the plugin.acquire() to here
        # self.prepare().result()

        # Hard coded optical path (as the OPM doesn't know about this special mode)
        logging.info("Preparing optical path")
        # Configure the optical path for the CCD we need
        mvs = self._opm.selectorsToPath(self._detector.name)
        fs = [f for (f, c, p) in mvs]
        # move lens 2 into position
        lsw_md = self._lsw.getMetadata()
        if model.MD_FAV_POS_ACTIVE in lsw_md:  # EK style lens-switch
            f = self._lsw.moveAbs(lsw_md[model.MD_FAV_POS_ACTIVE])
            fs.append(f)
        else:  # Old hacky style lens-switch (cannot be aligned)
            for p, n in self._lsw.axes["x"].choices.items():
                if n == "on":
                    f = self._lsw.moveAbs({"x": p})
                    fs.append(f)
                    break

        # move big slit into position
        for p, n in self._bigslit.axes["x"].choices.items():
            if n == "off":
                f = self._bigslit.moveAbs({"x": p})
                fs.append(f)
                break

        # wait for all the moves to be over
        for f in fs:
            f.result()

        logging.debug("Optical path configured")

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
        nl1l2scans = int(self.l1l2range.value/self.l1l2stepsize.value)+1
        #include overhead for lens scanning. Now estimated on 2s per movement. Furthermore added 25s overhead time
        dt = self.dwellTime.value * npos * nl1l2scans * 1.1 + (npos*nl1l2scans*2.5) + 25

        # logic that only adds acquisition time for DC if a DC region is defined
        if self.dcRegion.value != UNDEFINED_ROI:
            dc = drift.AnchoredEstimator(self._emitter, self._sed,
                                         self.dcRegion.value, self.dcDwellTime.value)
            dctime = dc.estimateAcquisitionTime()
            nDC = self.nDC.value
            # time for spatial drift correction, for now we just assume that spatial drift correction is done every pixel but we could include actual number of scanned pixelsv
            dt += (npos * nDC * nl1l2scans + 1) * (dctime + 0.2) + (npos*nl1l2scans*1.75) + 25

        return dt

    def _cancelAcquisition(self, future):
        """
        to be able to cancel the acquisition
        """
        with future._acq_lock:
            if future._acq_state == FINISHED:
                return False  # too late
            future._acq_state = CANCELLED

        logging.debug("Cancelling acquisition of components %s and %s",
                      self._emitter.name, self._detector.name)

        self.ARspectral_data_received.set()  # To help end quickly
        self._detector.data.unsubscribe(self._receive_ARspectral_data)
        self.sem_data_received.set()
        self._sed.data.unsubscribe(self._receive_sem_data)

        # Wait for the thread to be complete (and hardware state restored)
        future._acq_done.wait(5)
        return True

    def _discard_data(self, sed, data):
        pass

    def _runAcquisition(self, future):
        # number of drift corrections per pixel
        nDC = self.nDC.value
        # Initialize spectrograph
        CENTERWL = self.centerWavelength.value
        SLIT_WIDTH = self.slitWidth.value
        #record starting (favourite) values lsw and lsm
        orig_pos_lsw = self._lsw.position.value
        orig_pos_lsm = self._lsm.position.value
        # move to appropriate center wavelength
        self._sgr.moveAbs({"wavelength": CENTERWL}).result()
        # set slit width
        self._sgr.moveAbs({"slit-in": SLIT_WIDTH}).result()

        dt = self.dwellTime.value
        self._emitter.dwellTime.value = dt
        #exposure time and dwell time should be the same in this case
        bins = (self.binninghorz.value,self.binningvert.value)
        self._detector.binning.value = bins
        #check if this is correct syntax
        specresx = self._detector.shape[0] // bins[0]
        specresy = self._detector.shape[1] // bins[1]
        self._detector.resolution.value = (specresx,specresy)
        # semfov, physwidth = self._get_sem_fov()
        #xyps, stepsize = self._calc_xy_pos()
        xres, yres = self.get_scan_res()
        xyps = self.calc_xy_pos(self.roi.value, self.stepsize.value)
        logging.debug("Will scan on X/Y positions %s", xyps)

        #phys_rect = convert_roi_ratio_to_phys(escan,roi)
        measurement_n = 0
        ARdata = []
        sedata = []
        NPOS = len(xyps)  # = xres * yres
        self._save_hw_settings()

        # drift correction vectors
        #dc_vect = (0, 0)
        # list instead of tuple, to allow changing just one item at a time
        tot_dc_vect = [0, 0]

        #number of l1/l2 scans steps. Add 1 extra because central position also needs to be measured in this case

        # Trick to go back to original plugin. If nl1l2scans is 1, l2 does not scan.
        nl1l2scans = int(self.l1l2range.value / self.l1l2stepsize.value) + 1

        #shift should be range divided by 4. One factor 2 to divide between motors. One factor 2 because favourite position is chosen as origin
        shift = self.l1l2range.value / 4.

        # TODO: Add L2 favourite position so that the absolute position can be logged

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
                self._detector.exposureTime.value = dt / nDC
                self._emitter.dwellTime.value = dt / nDC

                for x, y in xyps:

                    if nl1l2scans > 1:

                        #move to one side of mirror. Only when lens is being scanned
                        f1 = self._oa.moveAbs({"l1": orig_pos_lsm["x"] + shift})
                        f2 = self._oa.moveAbs({"l2": orig_pos_lsw["x"] + shift})
                        f1.result()
                        f2.result()
                    #Loop over number of lens positions
                    for kk in range(nl1l2scans):
                        sedatapix = []
                        sedatam = []
                        ARdatapix = []
                        ARdatam = []
                        #loop over number of drift corrections
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
                            logging.info("Acquiring scan number %d at position (%g, %g) for l1l2 position (%g), with drift correction of %s",
                                         ll + 1, xm, ym, kk+1, tot_dc_vect)
                            startt = time.time()
                            ARdat, sedat = self._acquire_ARspec(x, y, dt/nDC, future)
                            endt = time.time()
                            logging.debug("Took %g s (expected = %g s)", endt - startt, dt/nDC)
                            ARdatapix.append(ARdat)
                            sedatapix.append(sedat)
                            logging.debug("Memory used = %d bytes", udriver.readMemoryUsage())
                            drift_est.acquire()
                            dc_vect = drift_est.estimate()
                            tot_dc_vect[0] += dc_vect[0]
                            tot_dc_vect[1] += dc_vect[1]

                        measurement_n += 1
                        # TODO: update the future progress
                        logging.info("Acquired %d out of %d images", measurement_n, NPOS * nl1l2scans)

                        # Perform addition of measurements here which keeps other
                        # acquisitions the same and reduces memory required. We use 32 bits in this case as the data is 16 bits.
                        ARdatam = numpy.sum(ARdatapix, 0, dtype=numpy.float32)
                        # checks whether datavalue exceeds data-type range.
                        # Note: this works for integers only. For floats there is a separate numpy function
                        idt = numpy.iinfo(ARdatapix[0].dtype)
                        # we can choose different things here. For now we just force to clip the signal
                        ARdatam = numpy.clip(ARdatam, idt.min, idt.max)
                        # convert back to right datatype and (re)add metadata
                        ARdatam = model.DataArray(ARdatam.astype(ARdatapix[0].dtype), ARdatapix[0].metadata)
                        ARdata.append(ARdatam)

                        # For SE data just use mean because absolute scale is not relevant
                        sedatam = numpy.mean(sedatapix).astype(sedatapix[0].dtype)
                        # The brackets are required to give enough dimensions to make the rest happy
                        sedatam = model.DataArray([[[[sedatam]]]], sedatapix[0].metadata)
                        sedata.append(sedatam)
                        # move lens here. Minus sign is to move in direction of parking position to prevent issues with motor movement. If statement is a bit ugly
                        if nl1l2scans > 1:
                            #move both lenses in negative direction
                            f1 = self._oa.moveRel({"l1": -self.l1l2stepsize.value/2.})
                            f2 = self._oa.moveRel({"l2": -self.l1l2stepsize.value/2.})
                            f1.result()
                            f2.result()

            else:
                self._start_spot(1)
                for x, y in xyps:
                    self._detector.exposureTime.value = dt
                    xm, ym = self._convert_xy_pos_to_m(x, y)

                    if nl1l2scans > 1:
                        # move to one side of mirror. Only when lens is being scanned
                        f1 = self._oa.moveAbs({"l1": orig_pos_lsm["x"] + shift})
                        f2 = self._oa.moveAbs({"l2": orig_pos_lsw["x"] + shift})
                        f1.result()
                        f2.result()

                    for kk in range(nl1l2scans):
                        logging.info("Acquiring  at scan position (%g, %g) for l1l2 position (%g)", xm, ym, kk+1)
                        # dwelltime is used as input for the acquisition because it is different for with drift and without
                        startt = time.time()
                        ARdat, sedat = self._acquire_ARspec(x, y, self.dwellTime.value, future)
                        endt = time.time()
                        logging.debug("Took %g s (expected = %g s)", endt - startt, self.dwellTime.value)
                        ARdata.append(ARdat)
                        sedata.append(sedat)
                        logging.debug("Memory used = %d bytes", udriver.readMemoryUsage())
                        # number of scans that have been done. Could be printed to show progress
                        measurement_n += 1
                        # TODO: update the future progress
                        logging.info("Acquired %d out of %d images", measurement_n, NPOS*nl1l2scans)
                        if nl1l2scans > 1:
                            f1 = self._oa.moveRel({"l1": -self.l1l2stepsize.value / 2.})
                            f2 = self._oa.moveRel({"l2": -self.l1l2stepsize.value / 2.})
                            f1.result()
                            f2.result()

            self._stop_spot()
            stepsize = (self.stepsize.value, self.stepsize.value)
            ARdata[0].metadata[model.MD_POS] = sedata[0].metadata[model.MD_POS]
            full_ARdata = self._assemble_ARspectral_data(ARdata, (xres,yres), self.roi.value, stepsize, bins, specresx, nl1l2scans)
            full_sedata = self._assemble_sed_data(sedata, (xres,yres), self.roi.value, stepsize, nl1l2scans)

            if future._acq_state == CANCELLED:
                raise CancelledError()
            das = [full_ARdata, full_sedata]
            if drift_est:
                das.append(self._assembleAnchorData(drift_est.raw))

            return das

        except CancelledError:
            logging.info("AR spectral stream cancelled")
            self._stop_spot()
            with future._acq_lock:
                self._acq_state = FINISHED
            raise  # Just don't log the exception
        except Exception:
            logging.exception("Failure during AR spectral acquisition")
            raise
        finally:
            logging.debug("AR spectral acquisition finished")
            self._sed.data.unsubscribe(self._discard_data)
            future._acq_done.set()
            self._resume_hw_settings()

    def _acquire_ARspec(self, x, y, dwellT, future):
        """
        Acquire N images using CCD while having the e-beam at a spot position
        escan (model.Emitter): the e-beam scanner
        edet (model.Detector): any detector of the SEM
        x, y (floats): spot position in the ebeam coordinates
        """

        # TODO: maybe it is better to move these commands out of this function and into the master because these parameters should not change
        self._move_spot(x, y)

        # get data data
        startt = time.time()
        #dat = self._detector.data.get()
        self._detector.data.subscribe(self._receive_ARspectral_data)
        timeout = 1 + dwellT * 2.5
        if not self.ARspectral_data_received.wait(timeout):
            if future._acq_state == CANCELLED:
                raise CancelledError()
            logging.warning("No AR spectral data received, will retry")
            self._detector.data.unsubscribe(self._receive_ARspectral_data)
            time.sleep(0.1)
            self._detector.data.subscribe(self._receive_ARspectral_data)
            if not self.ARspectral_data_received.wait(timeout):
                raise IOError("No AR spectral data received twice in a row")
        if future._acq_state == CANCELLED:
            raise CancelledError()

        dat = self.ARspectral_data
        dat.shape += (1, 1)

        dur_cor = time.time() - startt
        if dur_cor < dwellT*0.99:
            logging.error("Data arrived after %g s, while expected at least %g s", dur_cor, dwellT)
        # wait for the SE data, in case it hasn't arrived yet
        if not self.sem_data_received.wait(3):
            logging.warning("No SEM data received, 3s after the AR spectral data")
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
        return center#, pxs

    def _assemble_ARspectral_data(self,ARdata,resolution,roi,stepsize,bins,specresx,nl1l2scans):
        """
        Assemble spectral AR data and metadata
        """
        #get metadata, no need to ask directly to the component because the metadata is already embedded in the first dataset
        wllist = self._sgr.getPixelToWavelength(specresx, self._detector.pixelSize.value[0] * bins[0])
        logging.debug("WL_LIST = %s (from CCD = %s", wllist, specresx)
        md = ARdata[0].metadata.copy()
        md[model.MD_WL_LIST] = wllist
        #md[model.MD_DWELL_TIME] = dt
        #md[model.MD_BINNING] = self.binning.value
        md[model.MD_DESCRIPTION] = "AR spectrum"
        md[model.MD_AR_POLE] = self._detector.getMetadata()[model.MD_AR_POLE]
        #force exposure time metadata to be full time on the pixel rather than dwelltime/nDC
        md[model.MD_EXP_TIME] = self.dwellTime.value
        xres, yres = resolution
        md[model.MD_PIXEL_SIZE] = stepsize
        md[model.MD_POS] = self._get_center_pxs(resolution, roi, ARdata[0])
        md[model.MD_DESCRIPTION] = "AR spectrum"

        logging.debug("Assembling hyperspectral AR data")
        full_ARdata = model.DataArray(ARdata, metadata=md)
        # reshaping matrix. This needs to be checked

        full_ARdata = full_ARdata.swapaxes(2, 0)

        # Check XY ordering
        full_ARdata = numpy.reshape(full_ARdata, [full_ARdata.shape[0], full_ARdata.shape[1], nl1l2scans, yres, xres])

        return full_ARdata

    def _assemble_sed_data(self,sedata,resolution,roi,stepsize,nl1l2scans):
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
        mdescan[model.MD_EXP_TIME] = self.dwellTime.value
        # possibly merge the metadata for escan and sed.
        logging.debug("Assembling SEM data")
        full_sedata = model.DataArray(sedata, metadata=mdescan)
        full_sedata = full_sedata.swapaxes(0, 3)
        full_sedata = numpy.reshape(full_sedata, [1, 1, nl1l2scans, yres, xres])
        #full_sedata = full_sedata.swapaxes(3, 4)
        return full_sedata

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

    def _start_spot(self, nDC):
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
        self.ARspectral_data = None
        self.ARspectral_data_received.clear()
        self.sem_data = []
        self.sem_data_received.clear()

        # Move the spot
        self._emitter.translation.value = (x, y)
        # checks the hardware has accepted it
        act_tr = self._emitter.translation.value

        if math.hypot(x-act_tr[0], y - act_tr[1]) > 1e-3: # Anything below a thousand of a pixel is just float error
            logging.warning("Trans = %s instead of %s, will wait a bit" % (act_tr, (x, y)))
            time.sleep(0.1)
            act_tr = self._emitter.translation.value
        #if math.hypot(x-act_tr[0], y - act_tr[1]) > 1e-3: # Anything below a thousand of a pixel is just float error
        #    raise IOError("Trans = %s instead of %s" % (act_tr, (x, y)))

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

    def _receive_ARspectral_data(self, df, data):
        """
        Store AR spectral data
        """
        self._detector.data.unsubscribe(self._receive_ARspectral_data)
        self.ARspectral_data = data
        self.ARspectral_data_received.set()

    def _save_hw_settings(self):
        #
        orig_pos_lsm = self._lsm.position.value
        orig_pos_lsw = self._lsw.position.value
        res = self._emitter.resolution.value
        scale = self._emitter.scale.value
        trans = self._emitter.translation.value
        dt = self._emitter.dwellTime.value
        self._hw_settings = (res, scale, trans, dt, orig_pos_lsw, orig_pos_lsm)

    def _resume_hw_settings(self):
        res, scale, trans, dt, orig_pos_lsw, orig_pos_lsm = self._hw_settings
        # order matters!
        self._emitter.scale.value = scale
        self._emitter.resolution.value = res
        self._emitter.translation.value = trans
        self._emitter.dwellTime.value = dt

        #return to "on" position. Because of large number of acquisitions it might be nice to reference
        f = self._lsw.reference({"x"})
        f.result()
        f = self._lsw.moveAbs(orig_pos_lsw)
        f.result()
        f = self._lsm.reference({"x"})
        f.result()
        f = self._lsm.moveAbs(orig_pos_lsm)
        f.result()



class ARspectral(Plugin):
    name = "AR Spectral 2D"
    __version__ = "1.1"
    __author__ = "Toon Coenen"
    __license__ = "GNU General Public License 2"

    vaconf = OrderedDict((
        ("stepsize", {
            "tooltip": "Distance between the center of each pixel",
            "scale": "log",
        }),
        ("res", {
            "control_type": odemis.gui.CONTROL_READONLY,
            "label": "repetition",
        }),
        ("roi", {
            "control_type": odemis.gui.CONTROL_NONE, # TODO: CONTROL_READONLY to show it
        }),
        ("centerWavelength", {
            "control_type": odemis.gui.CONTROL_FLT,  # no slider
        }),
        ("grating", {
        }),
        ("slitWidth", {
            "control_type": odemis.gui.CONTROL_FLT,  # no slider
        }),
        ("dwellTime", {
            "tooltip": "Time spent by the e-beam on each pixel",
            "range": (1e-9, 360),
            "scale": "log",
        }),
        ("nDC", {
            "tooltip": "Number of drift corrections per pixel",
            "range": (1, 100),
            "label": "Drift cor. per pixel",
        }),
        ("binninghorz", {
            "label": "Hor. binning",
            "tooltip": "Horizontal binning of the CCD",
            "control_type": odemis.gui.CONTROL_RADIO,
        }),
        ("binningvert", {
            "label": "Ver. binning",
            "tooltip": "Vertical binning of the CCD",
            "control_type": odemis.gui.CONTROL_RADIO,
        }),
        ("cam_res", {
            "control_type": odemis.gui.CONTROL_READONLY,
            "label": "Camera resolution",
            "accuracy": None,
        }),
        ("gain", {
        }),
        ("readoutRate", {
        }),
        ("l1l2range", {
            "label": "L1L2 Range",
            "tooltip": "Lens 1 and 2 scan range",
            # "scale": "log",
        }),
        ("l1l2stepsize", {
            "label": "L1L2 Step size",
            "tooltip": "Lens 1 and 2 scan step size",
            # "scale": "log",
        }),
        ("nl1l2scans", {
            "control_type": odemis.gui.CONTROL_READONLY,
            "label": "L1L2 steps",
        }),
        ("filename", {
            "control_type": odemis.gui.CONTROL_SAVE_FILE,
        }),
        ("expectedDuration", {
        }),
    ))

    def __init__(self, microscope, main_app):
        super(ARspectral, self).__init__(microscope, main_app)

        # Can only be used on a Sparc with a CCD
        if not microscope:
            return

        main_data = self.main_app.main_data
        self.ebeam = main_data.ebeam
        self.ccd = main_data.ccd
        #self.ccd = main_data.sp_ccd
        self.sed = main_data.sed
        self.sgrh = main_data.spectrograph
        if not all((self.ebeam, self.ccd, self.sed, self.sgrh)):
            logging.debug("Hardware not found, cannot use the plugin")
            return

        # TODO: handle SPARC systems which don't have such hardware
        bigslit = model.getComponent(role="slit-in-big")
        lsw = model.getComponent(role="lens-switch")
        lsm = model.getComponent(role="lens-mover")
        # TODO: from Odemis v3.3, this component should not be needed anymore, assuming the microscope
        # file is upgraded to EK support (lens-switch uses MultiplexActuator) and all movements
        # of the lenses can be done through the lens-mover (l1) and lens-switch (l2)
        oa = model.getComponent(name="Optical Actuators")

        # the SEM survey stream (will be updated when showing the window)
        self._survey_s = None

        # Create a stream for AR spectral measurement
        self._ARspectral_s = SpectralARScanStream("AR Spectrum", self.ccd, self.sed, self.ebeam,
                                                  self.sgrh, lsw, lsm, bigslit, oa, main_data.opm)

        # For reading the ROA and anchor ROI
        self._acqui_tab = main_app.main_data.getTabByName("sparc_acqui").tab_data_model

        # The settings to be displayed in the dialog
        # Trick: we use the same VAs as the stream, so they are directly synchronised
        self.centerWavelength = self._ARspectral_s.centerWavelength
        #self.numberOfPixels = self._ARspectral_s.numberOfPixels
        self.dwellTime = self._ARspectral_s.dwellTime
        self.slitWidth = self._ARspectral_s.slitWidth
        self.binninghorz = self._ARspectral_s.binninghorz
        self.binningvert = self._ARspectral_s.binningvert
        self.l1l2stepsize = self._ARspectral_s.l1l2stepsize
        self.l1l2range = self._ARspectral_s.l1l2range  # TODO: this information should be filled by default by the lens-switch.MD_POS_ACTIVE_RANGE (using min/max around the active position)

        self.nDC = self._ARspectral_s.nDC
        self.grating = model.IntEnumerated(self.sgrh.position.value["grating"],
                                           choices=self.sgrh.axes["grating"].choices,
                                           setter=self._onGrating)
        self.roi = self._ARspectral_s.roi
        self.stepsize = self._ARspectral_s.stepsize
        self.res = model.TupleVA((1, 1), unit="px")
        self.nl1l2scans = model.IntVA(1, unit="")
        self.cam_res = model.TupleVA((self.ccd.shape[0], self.ccd.shape[1]), unit="px")
        self.gain = self.ccd.gain
        self.readoutRate = self.ccd.readoutRate
        self.filename = model.StringVA("a.h5")
        self.expectedDuration = model.VigilantAttribute(1, unit="s", readonly=True)

        # Update the expected duration when values change, depends both on dwell time,# of pixels and number of l2 positions.
        self.dwellTime.subscribe(self._update_exp_dur)
        self.stepsize.subscribe(self._update_exp_dur)
        self.nDC.subscribe(self._update_exp_dur)
        self.l1l2stepsize.subscribe(self._update_exp_dur)
        self.l1l2range.subscribe(self._update_exp_dur)

        # subscribe to update X/Y res
        self.stepsize.subscribe(self._update_res)
        self.roi.subscribe(self._update_res)
        #subscribe to binning values for camera res
        self.binninghorz.subscribe(self._update_cam_res)
        self.binningvert.subscribe(self._update_cam_res)

        #subscribe L2 stepsize and range to get number of L2 steps
        self.l1l2stepsize.subscribe(self._update_nl1l2pos)
        self.l1l2range.subscribe(self._update_nl1l2pos)

        self.addMenu("Acquisition/AR Spectral l1l2...", self.start)

    def _update_exp_dur(self, _=None):
        """
        Called when VA that affects the expected duration is changed
        """
        at = self._ARspectral_s.estimateAcquisitionTime()

        if self._survey_s:
            at += self._survey_s.estimateAcquisitionTime()

        # Use _set_value as it's read only
        self.expectedDuration._set_value(round(at), force_write=True)

    def _update_res(self, _=None):
        """
        Update the scan resolution based on the step size
        """

        sem_width = (self.ebeam.shape[0] * self.ebeam.pixelSize.value[0],
                     self.ebeam.shape[1] * self.ebeam.pixelSize.value[1])
        ROI = self.roi.value
        if ROI == UNDEFINED_ROI:
            ROI = (0, 0, 1, 1)
        logging.info("ROI = %s", ROI)
        stepsize = self.stepsize.value

        # rounded resolution values (rounded down), note deal with resolution 0
        xres = ((ROI[2] - ROI[0]) * sem_width[0]) // stepsize
        yres = ((ROI[3] - ROI[1]) * sem_width[1]) // stepsize

        if xres == 0:
            xres = 1
        if yres == 0:
            yres = 1
        self.res.value = (int(xres), int(yres))

    def _update_nl1l2pos(self, _=None):

        self.nl1l2scans.value = int(self.l1l2range.value/self.l1l2stepsize.value)+1

    def _update_cam_res(self, _=None):
        """
        Update spectral camera resolution based on the binning
        """
        cam_xres = self.ccd.shape[0] // self.binninghorz.value
        cam_yres = self.ccd.shape[1] // self.binningvert.value

        self.cam_res.value = (int(cam_xres), int(cam_yres))

    def _onGrating(self, grating):
        """
        Called when the grating VA is changed
        return (int): the actual grating, once the move is over
        """
        f = self.sgrh.moveAbs({"grating": grating})
        f.result()  # wait for the move to finish
        return grating

#    def _update_exp_dur(self, _=None):
#        """
#        Called when VA that affects the expected duration is changed
#        """
#        expt = self._mchr_s.estimateAcquisitionTime()
#        if self._survey_s:
#            expt += self._survey_s.estimateAcquisitionTime()
#
#        # Use _set_value as it's read only
#        self.expectedDuration._set_value(expt, force_write=True)

    def _get_new_filename(self):
        conf = get_acqui_conf()
        return os.path.join(
            conf.last_path,
            u"%s%s" % (time.strftime("%Y%m%d-%H%M%S"), ".h5")
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

    def start(self):
        # get region and dwelltime for drift correction
        self._ARspectral_s.dcRegion.value = self._acqui_tab.driftCorrector.roi.value
        self._ARspectral_s.dcDwellTime.value = self._acqui_tab.driftCorrector.dwellTime.value

        # Update the grating position to its current position
        self.grating.value = self.sgrh.position.value["grating"]

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
        self._update_cam_res()
        self._update_nl1l2pos()

        # Create a window
        dlg = AcquisitionDialog(self, "AR Spectral lens scanning acquisition",
                                "Acquires a hyperspectral AR CL image\n"
                                "for different lens positions\n"
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
            logging.info("AR spectral acquisition cancelled")
        elif ans == 1:
            logging.info("AR spectral acquisition completed")
        else:
            logging.debug("Unknown return code %d", ans)

    def acquire(self, dlg):
        # Stop the spot stream and any other stream playing to not interfere with the acquisition
        try:
            str_ctrl = self.main_app.main_data.tab.value.streambar_controller
        except AttributeError: # Odemis v2.6 and earlier versions
            str_ctrl = self.main_app.main_data.tab.value.stream_controller
        stream_paused = str_ctrl.pauseStreams()

        strs = []
        if self._survey_s:
            strs.append(self._survey_s)

        strs.append(self._ARspectral_s)

        fn = self.filename.value
        exporter = dataio.find_fittest_converter(fn)

        try:
            f = acqmng.acquire(strs, self.main_app.main_data.settings_obs)
            dlg.showProgress(f)
            das, e = f.result()  # blocks until all the acquisitions are finished
        except CancelledError:
            pass
        finally:
            pass

        if not f.cancelled() and das:
            if e:
                logging.warning("AR spectral scan partially failed: %s", e)
            logging.debug("Will save data to %s", fn)
            logging.debug("Going to export data: %s", das)
            exporter.export(fn, das)

        dlg.Destroy()
