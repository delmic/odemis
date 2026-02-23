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


from collections import OrderedDict
import functools
import logging
from odemis import model, dataio
import odemis
from odemis.acq import calibration
from odemis.acq.stream import SpectrumSettingsStream, SEMSpectrumMDStream
from odemis.dataio import get_available_formats
from odemis.gui.conf import util
from odemis.gui.conf.data import get_local_vas
from odemis.gui.plugin import Plugin
from odemis.gui.util import formats_to_wildcards
from odemis.util import img
import os
import wx


class BlSpectrumSettingsStream(SpectrumSettingsStream):

    def __init__(self, name, detector, dataflow, emitter, blanker=None, **kwargs):
        """
        See SpectrumSettingsStream for the standard options
        blanker (BooleanVA or None): None in case e-beam doesn't support blanker. Otherwise, you have the option to
        control whether the blanker is enabled (True), disabled (False), or automatically enabled whenever a
        scanning takes place (None).
        """
        super().__init__(name, detector, dataflow, emitter, **kwargs)
        self._blanker = blanker

        # Background file name: a str (the full path) or None (no background)
        self.backgroundFile = model.VigilantAttribute(None, setter=self.onBackgroundFile)

    def _prepare_opm(self):
        self._activateBlanker()
        return super()._prepare_opm()

    def _activateBlanker(self):
        try:
            # In case the user sets the blanker to False, it will remain disabled. The user can still override the blanker state.
            if self._blanker and self._blanker.value is None:
                logging.debug("Forcing the blanker on")
                self._blanker.value = True
        except Exception:
            logging.exception("Failed to activate the blanker")

    def _resetBlanker(self):
        try:
            # In case the blanker is set to False by the user, it remains False.
            if self._blanker and self._blanker.value:
                logging.debug("Resetting the blanker to auto")
                self._blanker.value = None
        except Exception:
            logging.exception("Failed to set the blanker to automatic mode")

    def _unlinkHwAxes(self):
        super()._unlinkHwAxes()
        self._resetBlanker()

    def onBackgroundFile(self, fn: str) -> str:

        try:
            if fn is None:
                logging.debug("Clearing spectrum background")
                cdata = None
            else:
                logging.debug("Loading spectrum background")
                converter = dataio.find_fittest_converter(fn, mode=os.O_RDONLY)
                data = converter.read_data(fn)
                # will raise exception if doesn't contain good calib data
                cdata = calibration.get_spectrum_data(data)  # get the background image (can be an averaged image)

            self.background.value = cdata  # update the background VA on the stream -> recomputes image displayed
            return fn

        except Exception as err:
            logging.info("Failed using file %s as background for currently loaded data", fn, exc_info=True)
            msg = "File '%s' not suitable as background for currently loaded data:\n\n%s"
            dlg = wx.MessageDialog(None,
                                   msg % (fn, err),
                                   "Unusable spectrum background file",
                                   wx.OK | wx.ICON_STOP)
            dlg.ShowModal()
            dlg.Destroy()
            return self.background.value  # previous value

    # Overrides method of LiveStream
    def _onBackground(self, data):
        """Called when the background is changed"""
        # Accept anything
        self._shouldUpdateHistogram()

    # Overrides method of SpectrumSettingsStream
    def _updateImage(self):
        """
        Convert the raw data to a spectrum. The background, if present, is first
        subtracted.
        """
        if not self.raw:
            return

        try:
            data = self.raw[0]

            # Subtract the background (without caring about the wavelength)
            try:
                bckg = self.background.value
                if bckg is not None:
                    data = img.Subtract(data, bckg)
            except Exception as ex:
                logging.info("Failed to apply spectrum correction: %s", ex)

            # Just copy the raw data into the image, removing useless extra dimensions
            im = data[:, 0, 0, 0, 0]
            im.metadata = im.metadata.copy()
            im.metadata[model.MD_DIMS] = "C"
            self.image.value = im

        except Exception:
            logging.exception("Updating %s %s image", self.__class__.__name__, self.name.value)


class BlSEMSpectrumMDStream(SEMSpectrumMDStream):
    """
    Same as the normal SEMSpectrumMDStream, but honors the extra options of BlSpectrumSettingsStream.
    """
    def _runAcquisition(self, future):
        try:
            # The SEMCCDAcquirer.prepare_hardware() forces the blanker off, only if it's None.
            # So we set it to True here, and reset it afterwards.
            self._sccd._activateBlanker()
            return super()._runAcquisition(future)
        finally:
            self._sccd._resetBlanker()


LIVE_STREAM_CONFIG = OrderedDict((
            ("wavelength", {
                "tooltip": "Center wavelength of the spectrograph",
                "control_type": odemis.gui.CONTROL_FLT,
                "range": (0.0, 1900e-9),
                "key_step_min": 1e-9,
            }),
            ("grating", {}),
            ("slit-in", {
                "label": "Input slit",
                "tooltip": "Opening size of the spectrograph input slit.\nA wide opening means more light and a worse resolution.",
            }),
            ("filter", {  # from filter
                "choices": util.format_band_choices,
            }),
            ("backgroundFile", {
                "control_type": odemis.gui.CONTROL_OPEN_FILE,
                "label": "Background",
                "clearlabel": "None",
                "wildcard": formats_to_wildcards(get_available_formats(os.O_RDONLY), include_all=True)[0]
            }),
        ))


class BlExtraPlugin(Plugin):
    name = "Force blanker on during spectrum acquisition"
    __version__ = "1.1"
    __author__ = "Éric Piel, Victoria Mavrikopoulou"
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

        odemis.gui.conf.data.STREAM_SETTINGS_CONFIG[BlSpectrumSettingsStream] = LIVE_STREAM_CONFIG

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
