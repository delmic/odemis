#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 10 Apr 2013

@author: Éric Piel

This is a simple example on how to acquire a "spectrum cube" on the SPARC in a
script. Run as:
cl_acquisition output.h5

You first need to run the odemis backend with the SPARC config:
odemisd --log-level 2 install/linux/usr/share/odemis/sparc-amolf.odm.yaml

To change some configuration settings, you can use the cli:
# to change the yaw of the mirror (by 10 um):
odemis-cli --move MirrorMover rz -10
# to change the center bandwidth of the spectrograph (to 520 nm):
odemis-cli --position SP2300i wavelength 0.520
# to change the resolution of the SEM (i.e., number of points acquired):
odemis-cli --set-attr "EBeam ExtXY" resolution "20, 10"
# to change the exposure time of the spectrometer (to 100 ms):
odemis-cli --set-attr Spec10 exposureTime 0.1
# to change the binning of the Angle-Resolved camera:
odemis-cli --set-attr ARCam binning "2, 2"
'''

import logging
import numpy
from odemis import model, dataio
from odemis.model import MD_PIXEL_SIZE, MD_POS
import sys
import threading
import time


logging.getLogger().setLevel(logging.DEBUG)

# region of interest as left, top, right, bottom (in ratio from the whole area)
RECT_ROI = (0.25, 0.25, 0.75, 0.75)


class Acquirer(object):
    def __init__(self):
        # the ebeam scanner
        self.escan = None
        # the secondary electron detector
        self.sed = None
        # the spectrometer
        self.spect = None
        # find components by their role
        for c in model.getComponents():
            if c.role == "e-beam":
                self.escan = c
            elif c.role == "se-detector":
                self.sed = c
            elif c.role == "spectrometer":
                self.spect = c
        if not all((self.escan, self.sed, self.spect)):
            logging.error("Failed to find all the components")
            raise KeyError("Not all components found")

        self.set_roi(RECT_ROI)

        self.acq_left = 0 # how many points left to scan
        self.acq_spect_buf = [] # list that will receive the acquisition data (numpy arrays of shape (N,1))
        self.acq_complete = threading.Event()

    def set_roi(self, rect):
        # save the resolution
        res = self.escan.resolution.value
        center = ((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)
        width = (rect[2] - rect[0], rect[3] - rect[1])

        shape = self.escan.shape
        scale = (1 / width[0], 1 / width[1])
        # translation is distance from center (situated at 0.5, 0.5), can be floats
        trans = (shape[0] * (center[0] - 0.5), shape[1] * (center[1] - 0.5))
        # always in this order
        self.escan.scale.value = scale # might update the resolution & translation
        self.escan.resolution.value = res
        self.escan.translation.value = trans

    def acquire_cube(self):
        """
        acquires two images: the SEM secondary electron image and the spectrum cube
        return (tuple of numpy.array):
           sed_data
           spect_data
        """
        exp = self.spect.exposureTime.value #s
        sem_size = self.escan.resolution.value
        spect_size = self.spect.resolution.value
        assert spect_size[1] == 1 # it's supposed to be a band
        numberp = numpy.prod(sem_size)

        # magical formula to get a long enough dwell time.
        # works with PVCam, but is probably different with other drivers :-(
        readout = numpy.prod(spect_size) / self.spect.readoutRate.value + 0.01
        self.escan.dwellTime.value = (exp + readout) * 1.1 + 0.05 # 50ms to account for the overhead and extra image acquisition 
        # pixel write/read setup is pretty expensive ~10ms
        expected_duration = numberp * (self.escan.dwellTime.value + 0.01)
        logging.info("Starting acquisition of about %g s...", expected_duration)

        self.acq_spect_buf = []
        self.acq_left = numberp
        self.acq_complete.clear()

        # synchronize the two devices
        self.spect.data.synchronizedOn(self.escan.newPosition)

        startt = time.time()
        self.spect.data.subscribe(self.receive_spect_point)

        sed_data = self.sed.data.get()
        # wait the last point is fully acquired
        self.acq_complete.wait()
        endt = time.time()
        logging.info("Took %g s", endt - startt)
        self.spect.data.synchronizedOn(None)

        # create metadata for the spectrum cube from the SEM CL
        # Could be computed also like this:
#       eps = self._emitter.pixelSize.value
#       scale = self._emitter.scale.value
#       ps = (eps[0] * scale[0], eps[1] * scale[1])
#       md = {MD_PIXEL_SIZE: ps}
        md_sem = sed_data.metadata
        md = {}
        for m in [MD_PIXEL_SIZE, MD_POS]:
            if m in md_sem:
                md[m] = md_sem[m]

        # create a cube out of the spectral data acquired
        # dimensions must be wavelength, Y, X
        assert len(self.acq_spect_buf) == numberp
        # each element of acq_spect_buf has a shape of (1, N)
        # reshape to (N, 1)
        for e in self.acq_spect_buf:
            e.shape = e.shape[-1::-1]
        # concatenate into one big array of (N, numberp)
        spect_data = numpy.concatenate(self.acq_spect_buf, axis=1)
        # reshape to (N, Y, X)
        spect_data.shape = (spect_size[0], sem_size[1], sem_size[0])
        # copy the metadata from the first point
        md_spect = self.acq_spect_buf[0].metadata
        md_spect.update(md)
        spect_data = model.DataArray(spect_data, metadata=md_spect)

        return sed_data, spect_data

    def receive_spect_point(self, dataflow, data):
        """
        callback for each point scanned as seen by the spectrometer
        """
        self.acq_spect_buf.append(data)

        self.acq_left -= 1
        if self.acq_left <= 0:
            dataflow.unsubscribe(self.receive_spect_point)
            self.acq_complete.set()


if __name__ == '__main__':
    # must have one argument as name of the file which contains SEM and spectrogram acquisitions
    if len(sys.argv) != 2:
        logging.error("Must be called with exactly 1 argument")
        exit(1)
    filename = sys.argv[1]

    acquirer = Acquirer()
    sed_data, spect_data = acquirer.acquire_cube()
    # to tell the exporter that the 3rd dimension of the spectrum is the channel
    # it has to be the 5th dimension => insert two axes
    s = spect_data.shape
    spect_data.shape = (s[0], 1, 1, s[1], s[2])

    exporter = dataio.find_fittest_converter(filename)
    exporter.export(filename, [sed_data, spect_data])
