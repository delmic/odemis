# -*- coding: utf-8 -*-
"""
Created on 8 June 2020

@author: Éric Piel, Victoria Mavrikopoulou

Gives ability to force the blanker on during spectrum acquisition.

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
from odemis import model
from odemis.acq.stream import SpectrumSettingsStream, SEMSpectrumMDStream
from odemis.gui.conf.data import get_local_vas
from odemis.gui.plugin import Plugin


class BlSpectrumSettingsStream(SpectrumSettingsStream):

    def __init__(self, name, detector, dataflow, emitter, blanker=None, **kwargs):
        """
        See SpectrumSettingsStream for the standard options
        blanker (BooleanVA or None): None in case e-beam doesn't support blanker. Otherwise, you have the option to
        control whether the blanker is enabled (True), disabled (False), or automatically enabled whenever a
        scanning takes place (None).
        """
        super(BlSpectrumSettingsStream, self).__init__(name, detector, dataflow, emitter, **kwargs)
        self._blanker = blanker

    def _prepare_opm(self):
        self._activateBlanker()
        return super(BlSpectrumSettingsStream, self)._prepare_opm()

    def _activateBlanker(self):
        try:
            # In case the user sets the blanker to False, it will remain disabled. The user can still override the blanker state.
            if self._blanker and self._blanker.value is None:
                logging.debug("Forcing the blanker on")
                self._blanker.value = True
        except Exception:
            logging.exception("Failed to activate the blanker")

    def _unlinkHwAxes(self):
        super(BlSpectrumSettingsStream, self)._unlinkHwAxes()
        try:
            # In case the blanker is set to False by the user, it remains False.
            if self._blanker and self._blanker.value:
                logging.debug("Resetting the blanker to auto")
                self._blanker.value = None
        except Exception:
            logging.exception("Failed to set the blanker tp automatic mode")


class BlSEMSpectrumMDStream(SEMSpectrumMDStream):
    """
    Same as the normal SEMSpectrumMDStream, but honors the extra options of BlSpectrumSettingsStream.
    """

    def _adjustHardwareSettings(self):
        self._sccd._activateBlanker()
        return SEMSpectrumMDStream._adjustHardwareSettings(self)

    def _runAcquisition(self, future):
        try:
            return super(BlSEMSpectrumMDStream, self)._runAcquisition(future)
        finally:
            self._sccd._unlinkHwAxes()


class BlExtraPlugin(Plugin):
    name = "Force blanker on during spectrum acquisition"
    __version__ = "1.0"
    __author__ = u"Éric Piel, Victoria Mavrikopoulou"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super(BlExtraPlugin, self).__init__(microscope, main_app)
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
            if len(sptms) <= 1:
                actname = "Spectrum"
            else:
                actname = "Spectrum with %s" % (sptm.name,)

            # Remove the standard action (which must have exactly the same name)
            stctrl.remove_action(actname)

            act = functools.partial(self.addSpectrum, name=actname, detector=sptm)
            stctrl.add_action(actname, act)

    def addSpectrum(self, name, detector):
        """
        name (str): name of the stream
        detector (DigitalCamera): spectrometer to acquire the spectrum
        """
        logging.debug("Adding spectrum stream for %s", detector.name)

        main_data = self.main_app.main_data
        stctrl = self._tab.streambar_controller

        spg = stctrl._getAffectingSpectrograph(detector)

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

        spec_stream = BlSpectrumSettingsStream(
            name,
            detector,
            detector.data,
            main_data.ebeam,
            sstage=main_data.scan_stage,
            opm=main_data.opm,
            axis_map=axes,
            detvas=get_local_vas(detector, main_data.hw_settings_config),
            blanker=blanker
        )
        stctrl._set_default_spectrum_axes(spec_stream)

        # Create the equivalent MDStream
        sem_stream = self._tab.tab_data_model.semStream
        sem_spec_stream = BlSEMSpectrumMDStream("SEM " + name, [sem_stream, spec_stream])

        ret = stctrl._addRepStream(spec_stream, sem_spec_stream)

        # Force the ROI to full FoV, as for the alignment with the SEM image, we need to have always a full image
        spec_stream.roi.value = (0, 0, 1, 1)

        return ret
