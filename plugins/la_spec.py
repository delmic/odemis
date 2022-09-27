# -*- coding: utf-8 -*-
'''
Created on 4 Feb 2019

@author: Éric Piel

Gives ability to acquire a "large-area" spectrum stream with:
 * The "lens 2" active, which reduces the signal, but increases the sample area
   where the light can be collected (ie, larger field-of-view ~ 150µm).
 * The polarization analyzer can be set to a specific mode (instead of pass-through)

--------------------------------------------------------------------------------
Copyright © 2019 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
'''

from collections import OrderedDict
from concurrent import futures
import functools
import logging
import numpy
from odemis import model
from odemis.acq.stream import SpectrumSettingsStream, POL_POSITIONS, SEMSpectrumMDStream
import odemis.gui
from odemis.gui.conf import data
from odemis.gui.conf.data import get_local_vas
from odemis.gui.plugin import Plugin
from odemis.model import MD_POL_NONE, MD_DESCRIPTION
from odemis.util import executeAsyncTask

import odemis.gui.conf.util as confutil


class LASpectrumSettingsStream(SpectrumSettingsStream):

    def __init__(self, name, detector, dataflow, emitter, l2=None, analyzer=None, **kwargs):
        """
        See SpectrumSettingsStream for the standard options
        l2 (None or Actuator with "x" axis): to move the lens 2 (aka "lens-switch")
        analyzer (None or Actuator with "pol" axis): the polarization analyzer.
          It should have at least the 7 "standard" positions
        """
        super(LASpectrumSettingsStream, self).__init__(name, detector, dataflow, emitter, **kwargs)

        self.l2 = l2
        if l2:
            # Convert the boolean to the actual position.
            # Two possibilities:
            # * "New" style for EK is to have POS_ACTIVE and POS_DEACTIVE metadata,
            # * Two choices positions, named "on" and "off"
            self._toLens2Pos = None
            md = l2.getMetadata()
            if model.MD_FAV_POS_ACTIVE in md:
                self._toLens2Pos = md[model.MD_FAV_POS_ACTIVE]["x"]
            else:
                for pos, pos_name in l2.axes["x"].choices.items():
                    if pos_name == "on":
                        self._toLens2Pos = pos
            if self._toLens2Pos is None:
                raise ValueError("Lens 2 actuator should have an FAV_POS_ACTIVE metadata or 'on' position, but only %s" %
                                 (list(l2.axes["x"].choices.values()),))

        # Polarization stored on the stream.
        # We don't use the standard "global" axes trick, so that it's possible
        # to have multiple streams, each with a different polarization.
        self.analyzer = analyzer
        if analyzer:
            # Hardcode the 6 pol pos + pass-through
            positions = set(POL_POSITIONS) | {MD_POL_NONE}
            # check positions specified in the microscope file are correct
            for pos in positions:
                if pos not in analyzer.axes["pol"].choices:
                    raise ValueError("Polarization analyzer %s misses position '%s'" % (analyzer, pos))
            self.polarization = model.VAEnumerated(MD_POL_NONE, choices=positions)

            # Not used, but the MDStream expects it as well.
            self.acquireAllPol = model.BooleanVA(False)

    # Copy from ARSettingsStream (to only change the axis when playing)

    def _prepare_opm(self):
        # Return a future which calls the OPM _and_ updates the "special" axes
        f = futures.Future()
        executeAsyncTask(f, self._set_optical_path)
        return f

    def _set_optical_path(self):
        f = super(LASpectrumSettingsStream, self)._prepare_opm()
        f.result()
        # Take care of the axes as soon as the OPM is done
        # Note: it's sub-optimal, as the OPM will explicitly move the axes away
        # while we maybe end-up putting them back.
        self._changeLensAxes()

    def _changeLensAxes(self, _=None):
        """"
        Move the special axes (ie, l2 and polarization)
        Waits until movement is completed.
        """
        # We cannot do it in _linkHwAxes(), as the OPM would reset the axes
        # (as _linkHwAxes() is called in the is_active setter, while the OPM is
        # called as a subscriber of is_active, so just after)
        # => moved to .prepare()
        fs = []
        if self.l2:
            logging.debug("Moving l2 to position %s.", self._toLens2Pos)
            fs.append(self.l2.moveAbs({"x": self._toLens2Pos}))

        if self.analyzer:
            try:
                logging.debug("Moving polarization analyzer to position %s.", self.polarization.value)
                fs.append(self.analyzer.moveAbs({"pol": self.polarization.value}))
            except Exception:
                logging.exception("Failed to move polarization analyzer.")
            self.polarization.subscribe(self._onPolarization)
            # TODO: ideally it would also listen to the analyzer.position VA
            # and update the polarization VA whenever the axis has moved

        for f in fs:
            try:
                logging.debug("Waiting for future %s", f)
                f.result(60)
            except Exception:
                logging.exception("Failed to move axis.")

    def _unlinkHwAxes(self):
        """"unsubscribe local axes: unlink VA from hardware axis"""
        super(LASpectrumSettingsStream, self)._unlinkHwAxes()

        if self.analyzer:
            self.polarization.unsubscribe(self._onPolarization)

    def _onPolarization(self, pol):
        """
        Move actuator axis for polarization analyzer.
        Not synchronized with stream as stream is already active.
        """
        f = self.analyzer.moveAbs({"pol": pol})
        f.add_done_callback(self._onPolarizationMove)

    def _onPolarizationMove(self, f):
        try:
            f.result()
        except Exception:
            logging.exception("Failed to move polarization analyzer.")

LENS2_MOVE_TIME = 5  # s


class LASEMSpectrumMDStream(SEMSpectrumMDStream):
    """
    Same as the normal SEMSpectrumMDStream, but honors the extra options of
    LASpectrumSettingsStream.
    """

    def estimateAcquisitionTime(self):
        total_time = super(LASEMSpectrumMDStream, self).estimateAcquisitionTime()

        # Add time to move the lens 2
        if self._sccd.l2:
            total_time += LENS2_MOVE_TIME

        return total_time

    def _adjustHardwareSettings(self):
        # Ideally this would be done in .acquire(), just after .prepare(), or in .prepare()
        # But as they return futures, it's a little more complex to do than just
        # here.
        self._sccd._changeLensAxes()
        return SEMSpectrumMDStream._adjustHardwareSettings(self)

    def _runAcquisition(self, future):
        try:
            return super(LASEMSpectrumMDStream, self)._runAcquisition(future)
        finally:
            self._sccd._unlinkHwAxes()

    def _assembleFinalData(self, n, data):
        super(LASEMSpectrumMDStream, self)._assembleFinalData(n, data)

        # In case there are several similar streams, add the polarization to the
        # stream name to make it easier to differentiate them.
        if self._analyzer and self._polarization.value != MD_POL_NONE:
            da = self._raw[n]
            da.metadata[MD_DESCRIPTION] += " (%s)" % (self._polarization.value,)


class SpecExtraPlugin(Plugin):
    name = "Large area spectrum stream"
    __version__ = "1.1"
    __author__ = u"Éric Piel"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super(SpecExtraPlugin, self).__init__(microscope, main_app)
        # Can only be used with a SPARC with spectrometer(s)
        main_data = self.main_app.main_data
        if microscope and main_data.role.startswith("sparc"):
            self._tab = self.main_app.main_data.getTabByName("sparc_acqui")
            stctrl = self._tab.streambar_controller

            try:
                # It's normally not handled by the GUI, to need to get it ourselves
                self._lens2 = model.getComponent(role="lens-switch")
            except LookupError:
                logging.info("%s plugin cannot load as there is no lens 2",
                             self.name)
                return

            if hasattr(main_data, "spectrometers"):  # From Odemis 2.10
                sptms = main_data.spectrometers
            else:  # Odemis 2.9
                sptms = [main_data.spectrometer, main_data.spectrometer_int]
                sptms = [s for s in sptms if s is not None]
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
                actname = "Large Area Spectrum"
            else:
                actname = "Large Area Spectrum with %s" % (sptm.name,)
            act = functools.partial(self.addSpectrum, name=actname, detector=sptm)
            stctrl.add_action(actname, act)

        # We "patch" the gui.conf.data for our special stream
        data.STREAM_SETTINGS_CONFIG[LASpectrumSettingsStream] = (
            OrderedDict((
                ("polarization", {
                    # "control_type": odemis.gui.CONTROL_COMBO,
                }),
                ("wavelength", {
                    "tooltip": "Center wavelength of the spectrograph",
                    "control_type": odemis.gui.CONTROL_FLT,
                    "range": (0.0, 1900e-9),
                }),
                ("grating", {}),
                ("slit-in", {
                    "label": "Input slit",
                    "tooltip": u"Opening size of the spectrograph input slit.\nA wide opening means more light and a worse resolution.",
                }),
                ("filter", {  # filter.band
                    "choices": confutil.format_band_choices,
                }),
            ))
        )

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

        spec_stream = LASpectrumSettingsStream(
            name,
            detector,
            detector.data,
            main_data.ebeam,
            l2=self._lens2,
            analyzer=main_data.pol_analyzer,
            sstage=main_data.scan_stage,
            opm=main_data.opm,
            axis_map=axes,
            detvas=get_local_vas(detector, main_data.hw_settings_config),
        )
        stctrl._set_default_spectrum_axes(spec_stream)

        # Create the equivalent MDStream
        sem_stream = self._tab.tab_data_model.semStream
        sem_spec_stream = LASEMSpectrumMDStream("SEM " + name,
                                                        [sem_stream, spec_stream])

        return stctrl._addRepStream(spec_stream, sem_spec_stream)
