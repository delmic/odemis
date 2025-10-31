# -*- coding: utf-8 -*-
"""
Created on 23 Oct 2025

@author: Éric Piel

For testing purposes only. Acquires only the first pixel of each line in a spectrum acquisition.
And in-between each pixel, jumps to the last pixel of the line without acquiring it.

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

import functools
import logging
import time
from typing import Optional, List

import numpy

from odemis import model
from odemis.acq import scan
from odemis.acq.stream import SpectrumSettingsStream, SEMSpectrumMDStream, SEMCCDAcquirerHwSync
from odemis.gui.conf.data import get_local_vas
from odemis.gui.plugin import Plugin


class JumpingSEMCCDAcquirerHwSync(SEMCCDAcquirerHwSync):
    """
    Hacky acquired that acquires the first pixel of the line, then jumps to the last pixel of the line,
    without acquiring it, and repeats for all pixels.
    """
    def prepare_hardware(self, max_snapshot_duration: Optional[float] = None) -> None:
        """
        :param max_snapshot_duration: maximum exposure time for a single CCD image. If the
        requested exposure time is longer, it will be divided into multiple snapshots.
        This can be used when a leech period is short, to run in within a single pixel acquisition.
        :side effects: updates .snapshot_time and .integration_count
        """
        super().prepare_hardware(max_snapshot_duration)

        # Compute a scan vector, with the corresponding TTL pixel signal
        rep = self._rep
        roi = self._mdstream.roi.value
        rotation = self._mdstream.rotation.value
        pos, margin, md_cor = scan.generate_scan_vector(self._mdstream._emitter, rep, roi, rotation,
                                                        dwell_time=self._mdstream._emitter.dwellTime.value)
        # pixel_ttl_flat = scan.generate_scan_pixel_ttl(self._mdstream._emitter, rep, self._margin)
        # Now, let's mess it up! We take the value of the first pixel of each line, and use it for every second pixel
        pos.shape = (rep[1], rep[0] + self._margin, 2)
        jumping_shape = (rep[1], rep[0] * 2 + self._margin, 2)
        jumping_pos = numpy.empty(jumping_shape, dtype=float)
        jumping_pos[:, ::2, :] = pos[:, self._margin:self._margin+1, :]  # first pixel of each line
        jumping_pos[:, 1::2, :] = pos[:, -1:, :]  # last pixel of each line
        jumping_pos.shape = (-1, 2)  # flatten

        # Similarly for the pixel TTL: every 4 tick is a pixel trigger
        jumping_pixel_ttl = numpy.full((jumping_shape[0], jumping_shape[1] * 2), dtype=bool, fill_value=False)
        # First part of each pixel is high
        jumping_pixel_ttl[:, margin::4] = True
        jumping_pixel_ttl.shape = (-1,)  # flatten

        self._pos_flat = jumping_pos
        self._mdstream._emitter.scanPath.value = self._pos_flat
        self._mdstream._emitter.scanPixelTTL.value = jumping_pixel_ttl

        # Don't be surprised the acquisition is longer...
        self.snapshot_time *= 2


    # Special version of the method, because the SEM data is twice longer than expected => drop some data
    def complete_spatial_acquisition(self, pol_idx) -> List[Optional[model.DataArray]]:
        self._mdstream._ccd_df.unsubscribe(self._mdstream._hwsync_subscribers[self._mdstream._ccd_idx])

        das = []
        # Receive the complete SEM data at once, after scanning the whole area.
        for i, (s, sub, q) in enumerate(zip(self._mdstream._streams[:-1],
                                            self._mdstream._hwsync_subscribers[:-1],
                                            self._mdstream._acq_data_queue[:-1])):
            try:
                sem_data = q.get(timeout=self.snapshot_time * 3 + 5)
            except queue.Empty:
                raise TimeoutError(f"Timeout while waiting for SEM data after {time.time() - self._start_area_t} s")
            self._mdstream._check_cancelled()

            logging.debug("Got SEM data from %s", s)
            s._dataflow.unsubscribe(sub)

            # Convert the data from a (flat) vector acquisition to an image
            # As each line is twice longer (not including the margin), we need to drop the second half
            # of the line, to make the data match the expected shape. As we don't really care about
            # the SEM data in this test, it's fine.
            sem_data.shape = (self._rep[1], self._rep[0] * 2 + self._margin)
            sem_data = sem_data[:, :self._rep[0] + self._margin]  # drop second half of each line
            sem_data = sem_data.reshape((-1,))  # flatten again (with copy, as half of the data is discarded)
            sem_data = scan.vector_data_to_img(sem_data, self._rep, self._margin, self._md_cor)

            sem_data = self._mdstream._preprocessData(i, sem_data, (0, 0))
            das.append(sem_data)

        das.append(None)  # No data for the CCD, as was already processed
        return das


class JumpingSpectrumMDStream(SEMSpectrumMDStream):
    """
    Same as the normal SEMSpectrumMDStream, but honors the extra options of BlSpectrumSettingsStream.
    """
    def _runAcquisition(self, future):
        if hasattr(self, "useScanStage") and self.useScanStage.value:
            raise ValueError("Should not use scan stage")
        elif self._supports_hw_sync():
            acquirer = JumpingSEMCCDAcquirerHwSync(self)
        else:
            raise ValueError("Should only be used with hw sync capable mode")

        logging.debug("Will run acquisition with %s", acquirer.__class__.__name__)
        return self._run_acquisition_ccd(future, acquirer)


class JumpingSpecPlugin(Plugin):
    name = "Test spectrum acquisition with only first pixel of each line"
    __version__ = "1.0"
    __author__ = "Éric Piel"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)
        # Can only be used with a SPARC with spectrometer(s)
        main_data = self.main_app.main_data
        if microscope and main_data.role.startswith("sparc"):
            self._tab = self.main_app.main_data.getTabByName("sparc_acqui")
            stctrl = self._tab.streambar_controller

            sptms = main_data.spectrometers
            if not sptms:
                logging.info("%s plugin cannot load as there are no spectrometers",
                             self.name)
                return
        else:
            logging.info("%s plugin cannot load as the microscope is not a SPARC",
                         self.name)
            return

        for sptm in sptms:
            actname = "Jumping spectrum with %s" % (sptm.name,)

            act = functools.partial(self.addSpectrum, name=actname, detector=sptm)
            stctrl.add_action(actname, act)

    def addSpectrum(self, name, detector):
        """
        name (str): name of the stream
        detector (DigitalCamera): spectrometer to acquire the spectrum
        """
        logging.debug("Adding jumping spectrum stream for %s", detector.name)

        main_data = self.main_app.main_data
        stctrl = self._tab.streambar_controller

        spg = stctrl._getAffectingSpectrograph(detector, default=main_data.spectrograph)

        axes = {"wavelength": ("wavelength", spg),
                "grating": ("grating", spg),
                "slit-in": ("slit-in", spg),
               }

        # Also add light filter for the spectrum stream if it affects the detector
        for fw in (main_data.cl_filter, main_data.light_filter):
            if fw is None:
                continue
            if detector.name in fw.affects.value:
                axes["filter"] = ("band", fw)
                break

        axes = stctrl._filter_axes(axes)

        if model.hasVA(main_data.ebeam, "blanker"):
            blanker = main_data.ebeam.blanker
        else:
            logging.warning("E-beam doesn't support blanker, but trying to use a BlankerSpectrum stream")
            blanker = None

        spec_stream = SpectrumSettingsStream(
            name,
            detector,
            detector.data,
            main_data.ebeam,
            sstage=main_data.scan_stage,
            opm=main_data.opm,
            axis_map=axes,
            detvas=get_local_vas(detector, main_data.hw_settings_config),
        )
        stctrl._set_default_spectrum_axes(spec_stream)

        # Create the equivalent MDStream
        sem_stream = self._tab.tab_data_model.semStream
        sem_spec_stream = JumpingSpectrumMDStream("SEM " + name, [sem_stream, spec_stream])

        ret = stctrl._addRepStream(spec_stream, sem_spec_stream)

        return ret
