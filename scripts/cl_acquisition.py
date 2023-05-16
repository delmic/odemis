#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 10 Apr 2013, updated in 2023

@author: Éric Piel

This is a simple example on how to acquire a "spectrum cube" on the SPARC in a
script. Run as:
./scripts/cl_acquisition.py --roi 0.25,0,0.75,1 --rep 2,4 --output spectrum_cube.h5

Note that the goal of this script is to demonstrate simple code.
It is not optimized for speed, nor tries to handle hardware errors.
Such optimized code can be found in odemis/src/acq/stream/_sync.py .

You first need to run the odemis backend with the SPARC config:
odemis-start

To change some configuration settings, you can use the cli:
# to change the center bandwidth of the spectrograph (to 520 nm):
odemis position spectrograph wavelength 520.0e-9
# to change the exposure time of the spectrometer (to 100 ms):
odemis set-attr spectrometer exposureTime 0.1
'''

import argparse
import logging
import sys
import threading
from typing import List, Tuple

import numpy
from numpy import ndarray
from odemis import dataio, model
from odemis.model import DataArray
from odemis.util import conversion

logging.getLogger().setLevel(logging.DEBUG)


class Acquirer(object):

    def __init__(self, repetition, roi):
        """
        :param repetition: number of positions in x,y
        :param roi: Region of interest, relative to the e-beam FoV (0→1)
        """
        self.repetition = repetition
        self.roi = roi

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

        # For SEM data acquisition
        self._last_sem_data = []  # DataArray of the last spot acquisition
        self._sem_data_received = threading.Event()

    def get_optical_image(self) -> DataArray:
        """
        Acquires an image from the CCD/spectrometer
        """
        return self.spect.data.get()

    def get_spot_positions(self) -> ndarray:
        """
        Compute the positions of the e-beam for each point in the RoI.
        Note that the points correspond to the *center* of squares dividing the
        RoI. So the first and last points are *not* at the border of the RoI,
        but a little bit away from the border.
        :return: (ndarray of floats of shape (Y,X,2)): each value is for a
          given Y,X in the rep grid -> 2 floats corresponding to the
          translation X,Y. Note that the dimension order is different between
          index and content, because X should be scanned first, so it's last
          dimension in the index.
        """
        rep = self.repetition
        roi = self.roi
        width = (roi[2] - roi[0], roi[3] - roi[1])

        # Take into account the "border" around each pixel
        pxs = (width[0] / rep[0], width[1] / rep[1])
        lim = (roi[0] + pxs[0] / 2, roi[1] + pxs[1] / 2,
               roi[2] - pxs[0] / 2, roi[3] - pxs[1] / 2)

        shape = self.escan.shape
        # convert into SEM translation coordinates: distance in px from center
        # (situated at 0.5, 0.5), can be floats
        lim_main = (shape[0] * (lim[0] - 0.5), shape[1] * (lim[1] - 0.5),
                    shape[0] * (lim[2] - 0.5), shape[1] * (lim[3] - 0.5))
        logging.info("Generating points in the SEM area %s, from rep %s and roi %s",
                     lim_main, rep, roi)

        pos = numpy.empty((rep[1], rep[0], 2), dtype=numpy.float)
        posy = pos[:, :, 1].swapaxes(0, 1)  # just a view to have Y as last dim
        posy[:, :] = numpy.linspace(lim_main[1], lim_main[3], rep[1])
        # fill the X dimension
        pos[:, :, 0] = numpy.linspace(lim_main[0], lim_main[2], rep[0])

        return pos

    def get_pixel_size(self) -> Tuple[float, float]:
        """
        Computes the pixel size (based on the repetition, roi and FoV of the
          e-beam). The RepetitionStream does provide a .pixelSize VA, which
          should contain the same value, but that VA is for use by the GUI.
        return (float, float): pixel size in m.
        """
        epxs = self.escan.pixelSize.value
        rep = self.repetition
        roi = self.roi
        eshape = self.escan.shape

        phy_size_x = (roi[2] - roi[0]) * epxs[0] * eshape[0]
        phy_size_y = (roi[3] - roi[1]) * epxs[1] * eshape[1]
        pxsx = phy_size_x / rep[0]
        pxsy = phy_size_y / rep[1]
        logging.debug("px size guessed = %s x %s", pxsx, pxsy)

        return (pxsx, pxsy)

    def get_center_pxs(self, datatl):
        """
        Computes the center and pixel size of the entire data based on the
        top-left data acquired.
        :param datatl (DataArray): first data array acquired
        :return:
            center (tuple of floats): position in m of the whole data
            pxs (tuple of floats): pixel size in m of the sub-pixels
        """
        # Compute center of area, based on the position of the first point (the
        # position of the other points can be wrong due to drift correction)
        center_tl = datatl.metadata[model.MD_POS]
        dpxs = datatl.metadata[model.MD_PIXEL_SIZE]
        tl = (center_tl[0] - (dpxs[0] * (datatl.shape[-1] - 1)) / 2,
              center_tl[1] + (dpxs[1] * (datatl.shape[-2] - 1)) / 2)
        logging.debug("Computed center of top-left pixel at at %s", tl)

        # Note: we don't rely on the MD_PIXEL_SIZE, because if the e-beam was in
        # spot mode (res 1x1), the scale is not always correct, which gives an
        # incorrect metadata.
        pxs = self.get_pixel_size()

        rep = self.repetition
        center = (tl[0] + (pxs[0] * (rep[0] - 1)) / 2,
                  tl[1] - (pxs[1] * (rep[1] - 1)) / 2)
        logging.debug("Computed data width to be %s x %s, with center at %s",
                      pxs[0] * rep[0], pxs[1] * rep[1], center)

        return center, pxs

    def assemble_sem_data(self, data: List[DataArray]) -> DataArray:
        """
        Assemble SEM data and metadata
        :param data: sorted with X fast, Y slow. Each DataArray should be of shape (1,1).
        :return: DataArray with the correct metadata and 5D shape (111YX)
        """
        # Get metadata: just use the one from the first DataArray acquired, and
        # update the few differences with the complete array.
        logging.debug("Assembling SEM data")
        md = data[0].metadata.copy()
        center, pxs = self.get_center_pxs(data[0])
        md[model.MD_PIXEL_SIZE] = pxs
        md[model.MD_POS] = center
        md[model.MD_DESCRIPTION] = "Secondary electrons"

        # Make a big array, and specify that the order is YX
        full_data = model.DataArray(data, metadata=md)
        xres, yres = self.repetition
        full_data.shape = (1, 1, 1, yres, xres)  # fails if data wasn't of shape 1,1
        return full_data

    def assemble_opt_data(self, data: List[DataArray], sem_da: DataArray) -> DataArray:
        """
        Assemble optical data and metadata
        :param data: sorted with X fast, Y slow. Each DataArray should contain a 1D image of shape (1, C)
        :param sem_da: An already assembled DataArray of the same X&Y, used for the metadata information
        :return: DataArray with the correct metadata and 5D shape (C11YX)
        """
        logging.debug("Assembling optical data")
        md = data[0].metadata.copy()
        md[model.MD_PIXEL_SIZE] = sem_da.metadata[model.MD_PIXEL_SIZE]
        md[model.MD_POS] = sem_da.metadata[model.MD_POS]
        md[model.MD_DESCRIPTION] = "CL spectrum"
        md[model.MD_DIMS] = "CTZYX"

        # Make a big array, and specify that the order is YX
        full_data = model.DataArray(data, metadata=md)  # shape = Y*X, 1, C
        full_data = full_data.swapaxes(0, 2)  # shape = C, 1, Y*X
        xres, yres = self.repetition
        full_data.shape = (full_data.shape[0], 1, 1, yres, xres)  # shape = C, 1, 1, Y, X

        return full_data

    def start_sem_acquisition(self):
        """
        Start SEM acquisition. It will keep acquiring (spot) data in the background
        until complete_sem_acquisition() is called
        """
        self._sem_data_received.clear()
        self._last_sem_data.clear()

        self.sed.data.subscribe(self.on_sem_data)

    def on_sem_data(self, df: model.DataFlow, da: DataArray) -> None:
        """
        Called for each new SEM data (typically, here, a 1x1 image)
        :param df: the dataflow that generated the data
        :param da: the data received
        """
        self._last_sem_data.append(da)

        if self._sem_data_received.is_set():
            # data was already received, but we keep acquiring as the optical data might still be acquiring
            logging.debug("Received extra SEM data")
            return
        self._sem_data_received.set()

    def complete_sem_acquisition(self) -> model.DataArray:
        """
        Wait for at least one SEM acquisition, and returns the data
        """
        self._sem_data_received.wait(5000)  # A very long time, but not infinity
        self.sed.data.unsubscribe(self.on_sem_data)
        return self._last_sem_data[0]

    def acquire_cube(self) -> Tuple[ndarray, ndarray]:
        """
        acquires two images: the SEM secondary electron image and the spectrum cube
        return (tuple of numpy.array):
           SEM data
           optical data
        """
        # The dwell time is defined by the exposure time on the spectrometer
        exp = self.spect.exposureTime.value  # s

        # Try to use as e-beam dwell time the same as exposure time
        # It's in spot mode, so it doesn't matter if the complete CCD acquisition
        # takes more time, as in the worse case it'll just end up acquiring a little
        # bit more (and for now the extra acquisition data is discarded).
        self.escan.dwellTime.value = self.escan.dwellTime.clip(exp)

        # Configure the SEM to spot mode
        self.escan.resolution.value = (1, 1)
        self.escan.scale.value = (1, 1)  # to be certain we can easily move the spot anywhere
        spot_pos = self.get_spot_positions()
        logging.debug("Generating %dx%d spots for %g s",
                      spot_pos.shape[1], spot_pos.shape[0], exp)

        sem_data = []
        opt_data = []

        # Loop on the spot positions
        for px_idx in numpy.ndindex(*self.repetition[::-1]):  # last dim (X) iterates first
            trans = tuple(spot_pos[px_idx])  # spot position

            self.escan.translation.value = trans

            # acquire sem (constantly)
            self.start_sem_acquisition()
            # acquire optical data
            opt_img = self.get_optical_image()
            opt_data.append(opt_img)

            # Finish the sem acquisition
            sed_img = self.complete_sem_acquisition()
            sem_data.append(sed_img)

        # build final arrays
        sem_da = self.assemble_sem_data(sem_data)
        opt_da = self.assemble_opt_data(opt_data, sem_da)

        return sem_da, opt_da


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser(description="CL spectrum acquisition")

    parser.add_argument("--roi", dest="roi", required=True,
                        help="e-beam ROI positions (left top bottom right, relative to the SEM "
                             "field of view)")
    parser.add_argument("--repetition", dest="repetition", required=True,
                        help="total of points in X and Y")
    parser.add_argument("--output", "-o", dest="filename", required=True,
                        help="Output filename")

    options = parser.parse_args(args[1:])

    try:
        if "." not in options.filename[-5:]:
            raise ValueError("Output argument must contain extension, "
                             "but got '%s'" % (options.filename,))

        roi = conversion.reproduce_typed_value([1.0], options.roi)
        if not all(0 <= r <= 1 for r in roi):
            raise ValueError("roi values must be between 0 and 1")

        repetition = conversion.reproduce_typed_value([1], options.repetition)
        if not all(1 <= r for r in repetition):
            raise ValueError("repetition values must be >= 1")

        acquirer = Acquirer(repetition, roi)
        sed_data, spect_data = acquirer.acquire_cube()

        exporter = dataio.find_fittest_converter(options.filename)
        exporter.export(options.filename, [sed_data, spect_data])
    except Exception:
        logging.exception("Unexpected error while performing action.")
        return 129

    return 0


if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    sys.exit(ret)
