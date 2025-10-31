# -*- coding: utf-8 -*-
"""
Created on 1 Sep 2023

@author: Éric Piel

Gives ability to acquire a spectrum data, while keeping the raw CCD image (ie, without vertical binning)

Copyright © 2023 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
"""

import logging
from collections import OrderedDict
from typing import Any, Dict, Tuple

import numpy

import odemis
import odemis.gui.conf.util as confutil
import odemis.gui.conf.data as confdata
import odemis.gui.model
from odemis import model
from odemis.acq.stream import SEMCCDMDStream, PolarizedCCDSettingsStream, TemporalSpectrumStream
from odemis.gui.conf.data import get_local_vas
from odemis.gui.plugin import Plugin
from odemis.model import MD_DESCRIPTION, MD_DIMS, MD_PIXEL_SIZE, MD_POS, MD_ROTATION, MD_ROTATION_COR, MD_WL_LIST


# Simulate a "Temporal Spectrum" stream... but with the time dimension being "unknown".
# This allows Odemis to display directly the data (almost) correctly

class SpectrumRawSettingsStream(PolarizedCCDSettingsStream):

    def __init__(self, name, detector, dataflow, emitter, spectrometer, spectrograph, **kwargs):
        if "acq_type" not in kwargs:
            kwargs["acq_type"] = model.MD_AT_TEMPSPECTRUM

        super().__init__(name, detector, dataflow, emitter, **kwargs)

        self.spectrometer = spectrometer
        self.spectrograph = spectrograph

        # For SPARC: typical user wants density much lower than SEM
        self.pixelSize.value *= 6  # increase default value to decrease default repetition rate

        # Instantiates horizontal and vertical binning
        full_vbin = detector.resolution.range[1][1]  # theoretical binning for full vertical binning
        try:
            hw_choices = detector.binning.choices
            h_choices = {b for b in {1, 2, 4, 8, 16} if any(hb[0] == b for hb in hw_choices)}
            self.horizontalBinning = model.VAEnumerated(1, choices=h_choices)
            v_choices = {b for b in {1, 2, 4, 8, 16, full_vbin // 2, full_vbin} if any(hb[1] == b for hb in hw_choices)}
            self.verticalBinning = model.VAEnumerated(1, choices=v_choices)
        except AttributeError:
            logging.info("The VA of the detector.binning doesn't support .choices")
            try:
                hw_range = detector.binning.range
                h_choices = {b for b in {1, 2, 4, 8, 16} if hw_range[0][0] <= b <= hw_range[1][0]}
                self.horizontalBinning = model.VAEnumerated(1, choices=h_choices)
                v_choices = {b for b in {1, 2, 4, 8, 16, full_vbin // 2, full_vbin} if hw_range[0][1] <= b <= hw_range[1][1]}
                self.verticalBinning = model.VAEnumerated(1, choices=v_choices)
            except AttributeError:
                logging.info("The VA of the detector.binning doesn't support .range so instantiate read-only VAs "
                             "for both horizontal and vertical binning")
                self.horizontalBinning = model.VigilantAttribute(1, readonly=True)
                self.verticalBinning = model.VigilantAttribute(1, readonly=True)

        self.wl_inverted = False  # variable shows if the wavelength list is inverted

        # This is a little tricky: we don't directly need the spectrometer, the
        # 1D image of the CCD, as we are interested in the raw image. However,
        # we care about the wavelengths and the spectrometer might be inverted
        # in order to make sure the wavelength is in the correct direction (ie,
        # lowest pixel = lowest wavelength). So we need to do the same on the
        # raw image. However, there is no "official" way to connect the
        # spectrometer(s) to their raw CCD. So we rely on the fact that
        # typically this is a wrapper, so we can check using the .dependencies.
        try:
            # check transpose in X (1 or -1), and invert if it's inverted (-1)
            self.wl_inverted = (self.spectrometer.transpose[0] == -1)
        except (AttributeError, TypeError) as ex:
            # A very unlikely case where the spectrometer has no .transpose or it's not a tuple
            logging.warning("%s: assuming that the wavelengths are not inverted", ex)

    def _prepare_opm(self):
        if self._opm is None:
            return model.InstantaneousFuture()

        # Force the optical path to spectrum, as typically the detector is used
        # to guess, but "ccd" would lead it to pretty much a random mode.
        logging.debug("Setting optical path for %s", self.name.value)
        f = self._opm.setPath("spectral", self.detector)
        return f

    def _onNewData(self, dataflow, data):
        """
        Stores the dimension order CAZYX in the metadata MD_DIMS. This convention records the data
        in such an order where C is the channel, A is the angle and ZYX the standard axes dimensions.

        Saves the list of angles in the new metadata MD_THETA_LIST, used only when EK imaging
        is applied.

        Calculates the wavelength list and checks whether the highest wavelengths are at the smallest
        indices. In such a case it swaps the wavelength axis of the CCD.
        """
        # Note: we cannot override PIXEL_SIZE as it is needed to compute MD_THETA_LIST
        # during acquisition => Create a new DataArray with a different metadata.
        md = data.metadata.copy()

        md[model.MD_DIMS] = "TC"  # By default, this is YX

        if self.wl_inverted:
            data = data[:, ::-1, ...]  # invert C

        # Sets POS and PIXEL_SIZE from the e-beam (which is in spot mode). Useful when taking snapshots.
        epxs = self.emitter.pixelSize.value
        md[model.MD_PIXEL_SIZE] = epxs
        emd = self.emitter.getMetadata()
        pos = emd.get(model.MD_POS, (0, 0))
        trans = self.emitter.translation.value
        md[model.MD_POS] = (pos[0] + trans[0] * epxs[0],
                            pos[1] - trans[1] * epxs[1])  # Y is inverted

        data = model.DataArray(data, metadata=md)
        super()._onNewData(dataflow, data)

    def _find_metadata(self, md):
        """
        Finds the useful metadata for a 2D spatial projection from the metadata of a raw image.
        :returns: (dict) Metadata dictionary (MD_* -> value).
        """
        simple_md = super()._find_metadata(md)
        if model.MD_WL_LIST in md:
            simple_md[model.MD_WL_LIST] = md[model.MD_WL_LIST]
        return simple_md

    def _linkHwVAs(self):
        """
        Subscribes the detector resolution and binning to the spectrum and angular binning VAs.
        """
        super()._linkHwVAs()
        self.verticalBinning.subscribe(self._onBinning, init=True)
        self.horizontalBinning.subscribe(self._onBinning, init=True)

    def _unlinkHwVAs(self):
        """
        Unsubscribes the detector resolution and binning and update the GUI
        """
        super()._unlinkHwVAs()
        self.verticalBinning.unsubscribe(self._onBinning)
        self.horizontalBinning.unsubscribe(self._onBinning)

    def _onBinning(self, _=None):
        """
        Callback, which updates the binning on the detector and calculates spectral resolution
        based on the spectrum and angular binning values.
        Only called when stream is active.
        """
        binning = (self.horizontalBinning.value, self.verticalBinning.value)
        try:
            self._detector.binning.value = binning
        except Exception:
            logging.exception("Failed to set the camera binning to %s", binning)

        actual_binning = self._detector.binning.value
        if actual_binning != binning:
            logging.warning("Detector accepted binning %s instead of requested %s",
                            actual_binning, binning)

        try:
            cam_xres = self._detector.shape[0] // actual_binning[0]
            cam_yres = self._detector.shape[1] // actual_binning[1]
            self._detector.resolution.value = (int(cam_xres), int(cam_yres))
        except Exception:
            logging.exception("Failed to set camera resolution on detector %s", self._detector)


class SEMSpectrumRawMDStream(SEMCCDMDStream):
    """
    Multiple detector Stream made of SEM + CCD
    It handles acquisition. The live data is automatically handled by SEMCCDMDStream, and uses the
    sub-streams.
    """
    def _prepare(self):
        return self._sccd._prepare()

    def _assembleLiveData(self, n, raw_data, px_idx, px_pos,
                          rep: Tuple[int, int], pol_idx: int):
        if n != self._ccd_idx:
            return super()._assembleLiveData(n, raw_data, px_idx, px_pos, rep, pol_idx)

        # Raw data format is YC, where Y is the CCD Y... but there is already the Y of the e-beam.
        # So we report it as a T dimension, which makes the data exporter happy and Odemis viewer happy.
        # Final data format is CTZYX with spec_res, ccdy_res, 1 , X , Y with X, Y = 1 at one ebeam position
        spec_res = raw_data.shape[-1]
        ccdy_res = raw_data.shape[-2]

        if pol_idx > len(self._live_data[n]) - 1:
            # New polarization => new DataArray
            md = raw_data.metadata.copy()
            # Update metadata to match the SEM metadata
            rotation = self.rotation.value
            md.update({MD_POS: self._roa_center_phys,
                       MD_PIXEL_SIZE: self._pxs,
                       MD_ROTATION: rotation,
                       MD_DIMS: "CTZYX",
                       MD_DESCRIPTION: self._streams[n].name.value})

            # Note: MD_WL_LIST is normally present.
            if MD_WL_LIST in md and spec_res != len(md[MD_WL_LIST]):
                # Not a big deal, can happen if wavelength = 0
                logging.warning("MD_WL_LIST is length %s, while spectrum res is %s",
                              len(md[MD_WL_LIST]), spec_res)

            # Make sure it doesn't contain metadata related to AR
            for k in (model.MD_AR_POLE, model.MD_AR_MIRROR_BOTTOM, model.MD_AR_MIRROR_TOP,
                      model.MD_AR_FOCUS_DISTANCE, model.MD_AR_HOLE_DIAMETER, model.MD_AR_PARABOLA_F,
                      model.MD_AR_XMAX):
                md.pop(k, None)

            # Shape of spectrum data = CT1YX
            da = numpy.zeros(shape=(spec_res, ccdy_res, 1, rep[1], rep[0]), dtype=raw_data.dtype)
            self._live_data[n].append(model.DataArray(da, md))

        raw_data = raw_data.T  # transpose to (wavelength, CCDY aka time)
        if self._sccd.wl_inverted:  # Flip the wavelength axis if needed
            raw_data = raw_data[::-1, ...]  # invert C
        self._live_data[n][pol_idx][:, :, 0, px_idx[0], px_idx[1]] = raw_data.reshape(spec_res, ccdy_res)

    def _assembleFinalData(self, n, data):
        """
        :param n: (int) number of the current stream which is assembled into ._raw
        :param data: all acquired data of the stream
        This function post-processes/organizes the data for a stream and exports it into ._raw.
        """
        if n != self._ccd_idx:
            return super()._assembleFinalData(n, data)

        if len(data) > 1:  # Multiple polarizations => keep them separated, and add the polarization name to the description
            for d in data:
                d.metadata[model.MD_DESCRIPTION] += " " + d.metadata[model.MD_POL_MODE]

        self._raw.extend(data)


# To force the "Temporal Spectrum" view to show the live stream
TemporalSpectrumStream.register(SpectrumRawSettingsStream)

# Configuration/customization of the stream panel in the GUI
SPECTRUM_RAW_CONFIG = OrderedDict((
    ("integrationTime", {
        "control_type": odemis.gui.CONTROL_SLIDER,
        "scale": "log",
        "type": "float",
        "accuracy": 2,
        "tooltip": "Readout camera exposure time.",
    }),
    ("integrationCounts", {
        "tooltip": "Number of images that are integrated, if requested exposure"
                   "time exceeds the camera exposure time limit.",
    }),
    ("wavelength", {
        "tooltip": "Center wavelength of the spectrograph",
        "control_type": odemis.gui.CONTROL_FLT,
        "range": (0.0, 1900e-9),
        "key_step_min": 1e-9,
    }),
    ("grating", {}),
    ("slit-in", {
        "label": "Input slit",
        "tooltip": "Opening size of the spectrograph input slit."
    }),
    ("filter", {  # from filter
        "choices": confutil.format_band_choices,
    }),
    ("horizontalBinning", {
        "tooltip": "Horizontal binning of the CCD",
        "control_type": odemis.gui.CONTROL_RADIO,
    }),
    ("verticalBinning", {
        "tooltip": "Vertical binning of the CCD",
        "control_type": odemis.gui.CONTROL_RADIO,
    }),
    ("polarization", {
        "control_type": odemis.gui.CONTROL_COMBO,
    }),
    ("acquireAllPol", {
        "control_type": odemis.gui.CONTROL_CHECK,
        "label": "All polarizations",
        "tooltip": "Record all possible polarization positions sequentially in one acquisition."
    }),
))


class SpectrumRawPlugin(Plugin):
    name = "Spectrum Raw acquisition"
    __version__ = "1.2"
    __author__ = "Éric Piel"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)
        # Can only be used with a SPARC with spetrometer and CCD camera
        main_data = self.main_app.main_data
        if not (microscope and main_data.ccd and main_data.spectrograph and main_data.role.startswith("sparc")):
            logging.info("%s plugin cannot load as the microscope is not a SPARC with CCD",
                         self.name)
            return

        self._tab = self.main_app.main_data.getTabByName("sparc_acqui")
        stctrl = self._tab.streambar_controller
        stctrl.add_action("Spectrum Raw", self.addst)
        # TODO: also support same functionality with all ccd* and sp-ccd*

        # Add the Temporal Spectrum viewport if it's not already created
        viewports = self._tab.panel.pnl_sparc_grid.viewports
        vpv = {
            viewports[3]: {
                "name": "Temporal Spectrum",
                "stream_classes": TemporalSpectrumStream,
            },
        }
        self._add_viewport(vpv)

        # We "patch" the gui.conf.data for our special stream
        confdata.STREAM_SETTINGS_CONFIG[SpectrumRawSettingsStream] = SPECTRUM_RAW_CONFIG

    def _add_viewport(self, vpv: Dict["Viewport", Dict[str, Any]]):
        """
        Add (non-initilized) viewports to the tab
        Similar to what ViewPortController does, but can do it after init.
        """
        tab_model = self._tab.tab_data_model
        view_ctrl = self._tab.view_controller
        view_ctrl._viewports.extend(vpv.keys())
        views = tab_model.views.value.copy()
        for vp, vkwargs in vpv.items():
            # skip if already existing
            name = vkwargs["name"]
            if any(name == v.name.value for v in views):
                logging.debug("Skipping recreation of view %s", name)
                continue

            vp.Shown = False  # we know it's not to be shown yet
            vcls = vkwargs.pop("cls", odemis.gui.model.MicroscopeView)
            view = vcls(**vkwargs)

            views.append(view)
            vp.setView(view, tab_model)

        tab_model.views.value = views

        # Is this the "old" ViewportGrid (ie < Odemis v3.4)?
        if hasattr(view_ctrl._grid_panel, "show_grid_viewports"):
            # Force the first 4 viewports connected to be shown (at init), otherwise that confuses the (old) ViewportGrid
            first_four_views = [vp for vp in view_ctrl._grid_panel.viewports if vp.view is not None][:4]
            view_ctrl._grid_panel.set_visible_viewports(first_four_views)
            # Update the model
            tab_model.visible_views.value = [vp.view for vp in first_four_views]

    def addst(self):
        main_data = self.main_app.main_data
        stctrl = self._tab.streambar_controller

        detvas = get_local_vas(main_data.ccd, main_data.hw_settings_config)
        # For ek acquisition we use a horizontal and a vertical binning
        # which are instantiated in the AngularSpectrumSettingsStream.
        # Removes binning from local (GUI) VAs to use a vertical and horizontal binning
        detvas.remove('binning')

        if main_data.ccd.exposureTime.range[1] < 3600:  # 1h
            # Removes exposureTime from local (GUI) VAs to use a new one, which allows to integrate images
            detvas.remove("exposureTime")

        spectrograph = stctrl._getAffectingSpectrograph(main_data.ccd, default=main_data.spectrograph)
        spectrometer = stctrl._find_spectrometer(main_data.ccd)

        axes = {"wavelength": ("wavelength", spectrograph),
                "grating": ("grating", spectrograph),
                "slit-in": ("slit-in", spectrograph),
                "filter": ("band", main_data.light_filter),
                }
        axes = stctrl._filter_axes(axes)

        sr_stream = SpectrumRawSettingsStream(
            "Spectrum Raw",
            main_data.ccd,
            main_data.ccd.data,
            main_data.ebeam,
            spectrometer,
            spectrograph,
            analyzer=main_data.pol_analyzer,
            sstage=main_data.scan_stage,
            opm=main_data.opm,
            axis_map=axes,
            detvas=detvas,
        )
        stctrl._set_default_spectrum_axes(sr_stream)

        # Create the equivalent MDStream
        sem_stream = self._tab.tab_data_model.semStream
        sem_cl_stream = SEMSpectrumRawMDStream("SEM Spectrum Raw", [sem_stream, sr_stream])

        return stctrl._addRepStream(sr_stream, sem_cl_stream)
