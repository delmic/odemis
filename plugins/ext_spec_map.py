# -*- coding: utf-8 -*-
"""
Created on 19 May 2022

@author: Éric Piel

Copyright © 2022 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
"""

# A simple GUI to acquire a spectrum map when the e-beam is controlled by an
# external system.
# This is how the acquisition is supposed to be set-up:
# * A system (eg, SEM software) which controls the e-beam scan (of MxN points).
#   The scan is expected to be raster-line type (eg, not zigzag), fast dimension
#   X, starting from the top, and slow dimension Y, starting from the left.
#   Each "point" may be a single position, or a more complex pattern such as a
#   square, to "fuzz" the beam over an area.
# * At the moment the e-beam is ready at a point position, a TTL signal goes high.
#   This TTL signal should be routed to the trigger of the spectrum camera.
#   The signal may be just a burst (ie, a quick high/low), or stay as long as the
#   e-beam is position on the pixel.
# * Sufficient time is given by the scanning system between each pixel, so that
#   the camera is ready for the next frame. The required time is displayed by
#   the plugin as "frame overhead".
# * For now, only cameras controlled via the andorcam2 driver are supported.
#   This could be extended to other drivers by adding the hardwareTrigger functionality.
# * Before starting the scanning, the user configures the spectrum camera with
#   this plugin, and press the START button to be "Ready to acquire".
# * Once the plugin has received the expected number of images, it reconstructs
#   the spectrum cube. The metadata is filled based on the information entered
#   by the user.

from collections import OrderedDict
from functools import partial
import logging
import math
import numpy
from odemis import dataio, model, gui, util
from odemis.acq.stream import SpectrumSettingsStream
from odemis.dataio import hdf5
from odemis.gui.conf import get_acqui_conf
from odemis.gui.conf.data import get_local_vas, get_stream_settings_config
from odemis.gui.conf.util import label_to_human
from odemis.gui.plugin import Plugin, AcquisitionDialog
from odemis.gui.util import call_in_wx_main, formats_to_wildcards
from odemis.util import limit_invocation
from odemis.util.dataio import splitext
from odemis.util.filename import guess_pattern, create_filename, update_counter
import os
import queue
import time
from typing import List, Tuple, Optional
import wx


class ExternalAcquisition:
    """
    All data & settings needed to acquire an external acquisition
    """
    def __init__(self, main_data, spec: model.Detector):
        """
        main_data (MainGUIData): the GUI model
        spec: the spectrometer to use
        """
        self.main_data = main_data
        self.spectrometer = spec

        if (not hasattr(spec, "hardwareTrigger")
            or not isinstance(spec.hardwareTrigger, model.EventBase)
            or not issubclass(spec.hardwareTrigger.get_type(), model.HwTrigger)
        ):
            raise ValueError(f"Spectrometer {spec.name} has no hardware trigger")

        # Storage settings
        self.conf = get_acqui_conf()
        self.filename = model.StringVA("")
        self.filename.subscribe(self._on_filename)

        # Time info
        self.expectedDuration = model.VigilantAttribute(1, unit="s", readonly=True)

        # 2ms overhead by default
        self.frameOverhead = model.FloatContinuous(2e-6, range=(0, 1000), unit="s", readonly=True)
        if model.hasVA(spec, "frameDuration"):
            self.frameDuration = spec.frameDuration
        else:
            logging.warning("Spectrometer doesn't provide frameDuration, frame duration will not be precise")
            # TODO: if not present, fall back to exposure time + readout rate * resolution
            self.frameDuration = model.FloatContinuous(0, range=(0, 1000), unit="s", readonly=True)
            # self.stream.exposureTime.subscribe(self._update_exp_dur)
            # self.stream.readoutRate.subscribe(self._update_exp_dur)
            # self.stream.binning.subscribe(self._update_exp_dur)

        # Position of the center of the image according to the external e-beam
        self.posX = model.FloatContinuous(1e-3, range=(-1, 1), unit="m")
        self.posY = model.FloatContinuous(1e-3, range=(-1, 1), unit="m")

        # Live spectrum stream, for controlling the camera settings, the
        # spectrograph axes, and repetition/pixel size info
        # TODO: use a fake emitter for the repetition/pixelsize
        self.stream = SpectrumSettingsStream(
            f"Spectrum with {spec.name}",
            spec,
            spec.data,
            main_data.ebeam,
            hwdetvas=get_local_vas(spec, main_data.hw_settings_config),
            opm=main_data.opm,
        )

        # Replace repetition and pixel size VAs by simpler versions which accepts anything
        self.stream.pixelSize = model.FloatContinuous(20e-6, range=(0, 1), unit="m")
        self.stream.repetition = model.ResolutionVA((10, 10), ((1, 1), (1000, 1000)))
        # Fuzzing is controlled by the e-beam and has not effect here => remove it
        del self.stream.fuzzing

        # Update the acquisition time when it might change
        self.stream.exposureTime.subscribe(self._update_exp_dur, init=True)
        self.stream.readoutRate.subscribe(self._update_exp_dur)
        self.stream.binning.subscribe(self._update_exp_dur)
        self.frameDuration.subscribe(self._update_exp_dur)
        self.stream.repetition.subscribe(self._update_exp_dur)

        # To store data during acquisition
        self._data_q = queue.Queue()  # type: queue.Queue[model.DataArray]

    @limit_invocation(0.2)  # s, => max 5Hz
    def _update_exp_dur(self, _=None):
        """
        Shows how long the acquisition takes.
        """
        frame_p = self.spectrometer.frameDuration.value
        overhead = frame_p - self.stream.exposureTime.value
        if overhead < 0:  # it's wrong, probably because the frameDuration hasn't been updated
            logging.warning("Frame duration probably incorrect, play the stream to update it")
            overhead = 0
        overhead = math.ceil(overhead * 1e3) * 1e-3  # round-up to the ms

        rep = self.stream.repetition.value
        tott = math.ceil(frame_p * rep[0] * rep[1])  # round-up to 1s

        # Use _set_value as it's read only
        self.expectedDuration._set_value(tott, force_write=True)
        self.frameOverhead._set_value(overhead, force_write=True)
        logging.debug("Frame overhead = %s", self.frameOverhead.value)

    def _on_filename(self, fn):
        """
        Store path and pattern in conf file.
        :param fn: (str) The filename to be stored.
        """
        # Store the directory so that next filename is in the same place
        p, bn = os.path.split(fn)
        if p:
            self.conf.last_path = p

        # Save pattern
        self.conf.fn_ptn, self.conf.fn_count = guess_pattern(fn)

    def update_filename(self):
        """
        Set filename from pattern in conf file.
        """
        fn = create_filename(self.conf.last_path, self.conf.fn_ptn, '.h5', self.conf.fn_count)
        self.conf.fn_count = update_counter(self.conf.fn_count)

        # Update the widget, without updating the pattern and counter again
        self.filename.unsubscribe(self._on_filename)
        self.filename.value = fn
        self.filename.subscribe(self._on_filename)

    def _build_spec_map(self, specs: List[model.DataArray], rep: Tuple[int, int]) -> model.DataArray:
        # Fill up the missing spectra if not enough (by assuming that the missing
        # ones are at the end)
        if len(specs) == 0:
            raise ValueError("At least one spectrum should be provided")

        # Spectra should be of shape (1, C)
        assert specs[0].shape[-1] > 1

        n_missings = (rep[0] * rep[1]) - len(specs)
        if n_missings:
            logging.debug("Filling up spectrum cube with %d empty spectra", n_missings)
            fake_spec = numpy.zeros(specs[0].shape, dtype=specs[0].dtype)
            specs += [fake_spec] * n_missings

        # Start with array of (Y*X)xC shape, and go to C, 1, 1, Y, X
        da = model.DataArray(numpy.array(specs))  # (Y*X), 1, C
        da.shape = (1, 1, rep[1], rep[0], specs[0].shape[-1])  # 1, 1, Y, X, C
        da = numpy.moveaxis(da, -1, 0)  # C, 1, 1, Y, X

        md = specs[0].metadata.copy()
        md[model.MD_DIMS] = "CTZYX"
        md.pop(model.MD_POS, None)  # This is not correct anymore (we could adjust it, but anyway it'll be overridden)
        da.metadata = md

        return da

    def _cancel_acq(self, evt):
        """
        Cancel button handler
        """
        self._data_q.put(None)  # Fake "data" to unblock the main thread waiting
        return True

    def _acq_canceller(self, future):
        """
        Callback function when future is cancelled (via the cancel button)
        """
        self._data_q.put(None)  # Fake "data" to unblock the main thread waiting
        return True

    def _on_spec_data(self, df: model.DataFlow, data: model.DataArray):
        logging.debug("Received data of shape %s", data.shape)
        self._data_q.put(data)

    def _acquire_spec(self, dlg, future) -> Tuple[Optional[model.DataArray], bool]:
        """
        returns:
            da: the acquired spectrum cube, or None if it was cancelled before
               any image was received.
            cancelled: True if the user cancelled the acquisition before the end
        """
        cancelled = False

        rep = self.stream.repetition.value
        num_acqs = rep[0] * rep[1]
        # each spectrum acquired, in order
        specs = []  # type: List[model.DataArray]
        self._data_q = queue.Queue()  # reset the queue

        # Configure optical path
        dlg.setAcquisitionInfo("Preparing optical path...", logging.WARNING)
        f = self.main_data.opm.setPath(self.stream)
        while not f.done():
            wx.CallAfter(dlg.gauge_progress.Pulse)
            time.sleep(0.1)

        # Prepare camera
        self.spectrometer.data.synchronizedOn(self.spectrometer.hardwareTrigger)
        self.spectrometer.dropOldFrames.value = False
        self.spectrometer.data.subscribe(self._on_spec_data)

        # give a lot of time to be entirely sure that the camera is ready
        time.sleep(0.5)

        try:
            # Indicate it's ready
            # On the first image received, we will show a progress bar
            dlg.setAcquisitionInfo("Ready to acquire", logging.WARNING)
            wx.CallAfter(dlg.gauge_progress.SetValue, 0)
            wx.CallAfter(dlg.gauge_progress.SetRange, num_acqs)

            # Acquire (even if it was live, to be sure the data is up-to-date)
            # Will stop when all images are received or (future) cancelled
            while len(specs) < num_acqs:
                spec = self._data_q.get()  # wait for a new data (or cancel event)

                # Check it's cancelled (which ever of the 2 signals arrives first)
                if spec is None or future.cancelled():  # Cancelled?
                    logging.debug("Stopping acquisition, as it was cancelled")
                    cancelled = True
                    break

                specs.append(spec)

                if len(specs) == 1:  # First time
                    # Hide the message
                    dlg.setAcquisitionInfo(None)

                self._update_progress(dlg, len(specs))

        finally:
            logging.info("Acquisition stopped after %d data (expected %d)", len(specs), rep[0] * rep[1])
            self.spectrometer.data.unsubscribe(self._on_spec_data)
            self.spectrometer.data.synchronizedOn(None)
            self.spectrometer.dropOldFrames.value = True  # revert to the default

        if specs:  # at least one data => build 3D spec
            if len(specs) < num_acqs:
                dlg.setAcquisitionInfo("Acquisition cancelled before all spectra received", logging.WARNING)
            da = self._build_spec_map(specs, rep)

            # Update metadata based on info from the user
            md = da.metadata
            md[model.MD_DESCRIPTION] = self.stream.name.value
            md[model.MD_POS] = (self.posX.value, self.posY.value)
            md[model.MD_PIXEL_SIZE] = (self.stream.pixelSize.value, self.stream.pixelSize.value)

            return da, cancelled
        else:
            dlg.setAcquisitionInfo("Acquisition cancelled", logging.WARNING)
            return None, cancelled

    def acquire(self, dlg):
        """
        Acquire the data, and store it
        dlg: AcquisitionDialog
        """
        # Stop the streams
        dlg.streambar_controller.pauseStreams()
        dlg.pauseSettings()
        f = model.ProgressiveFuture()

        # Show the gauge, with the cancel button
        # dlg.current_future = None
        self._show_gauge_panel(dlg)

        try:
            try:
                da, cancelled = self._acquire_spec(dlg, f)
            except Exception as e:
                logging.exception("Failed to acquire CL data: %s", e)
                return

            if da is not None:
                # Save the file
                fn = self.filename.value
                exporter = dataio.find_fittest_converter(fn)

                # Add two fake SEM streams, to make the file exactly the same
                # as the standard spectrum acquisition (with 3 streams)
                fakemd = {
                    model.MD_DESCRIPTION: "Fake SEM data",
                    model.MD_PIXEL_SIZE: (1e-9, 1e-9),  # m
                    model.MD_DWELL_TIME: 1e-6,  # s
                    model.MD_POS: da.metadata.get(model.MD_POS, (0, 0))
                }
                sem0 = model.DataArray(numpy.zeros((1, 1)), fakemd)
                sem1 = model.DataArray(numpy.zeros(da.shape[-2:]), fakemd)
                try:
                    exporter.export(fn, [sem0, sem1, da])
                except Exception:
                    logging.exception("Failed to store data in %s", fn)

        finally:
            self._hide_gauge_panel(dlg)
            dlg.resumeSettings()

        # If data acquired -> Close the dialog (and show data in the analysis tab)
        if da is not None:
            dlg.Close()

    @call_in_wx_main
    def _update_progress(self, dlg, n: int):
        """
        Updates the position of the progress bar to the specified number
        """
        dlg.gauge_progress.SetValue(n)
        dlg.lbl_gauge.SetLabel(f"{n}")

    @call_in_wx_main
    def _show_gauge_panel(self, dlg):
        """
        Display the gauge panel and disable the buttons
        """
        dlg.enable_buttons(False)
        dlg.lbl_gauge.SetLabel("")
        dlg.pnl_gauge.Show(True)
        dlg.btn_cancel.Enable()
        dlg.Layout()

    @call_in_wx_main
    def _hide_gauge_panel(self, dlg):
        """
        Put back the gauge panel away, for allowing to change the settings again
        """
        dlg.enable_buttons(True)
        dlg.pnl_gauge.Show(False)
        dlg.btn_cancel.Disable()
        dlg.Layout()

class ExtSpectrumMapPlugin(Plugin):
    name = "External Spectrum Map"
    __version__ = "1.0"
    __author__ = "Éric Piel"
    __license__ = "GPLv2"

    # Describe how the values should be displayed (order matters)
    # See odemis.gui.conf.data for all the possibilities
    vaconf = OrderedDict((
        ("filename", {
            "control_type": gui.CONTROL_SAVE_FILE,
            "wildcard": formats_to_wildcards({hdf5.FORMAT: hdf5.EXTENSIONS})[0],
        }),
        ("posX", {
            "label": "Position X",
            "control_type": gui.CONTROL_FLT,
            "accuracy": 9,
            "tooltip": "Center position of the acquisition (U)",
        }),
        ("posY", {
            "label": "Position Y",
            "control_type": gui.CONTROL_FLT,
            "accuracy": 9,
            "tooltip": "Center position of the acquisition (V)",
        }),
        ("frameOverhead", {
            "tooltip": "Extra time needed by the camera for each frame (only updated when stream is playing)",
        }),
        ("frameDuration", {
            "tooltip": "Total time needed by the camera for each frame (only updated when stream is playing)",
        }),
        ("expectedDuration", {
        }),
    ))

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)
        # Can only be used with a SPARC with spectrometer
        if not microscope:
            return

        main_data = self.main_app.main_data
        if not main_data.spectrometers:
            logging.warning("Microscope doesn't have any spectrometer")
            return

        # Show one menu per spectrometer, in alphabetical order
        specs = sorted(main_data.spectrometers, key=lambda s: s.name)
        self._acqs = []
        for i, spec in enumerate(specs):
            acq = ExternalAcquisition(main_data, spec)
            self._acqs.append(acq)

            # Add a menu, with a short-cut starting from F2
            self.addMenu(f"Acquisition/External Spectrum Map with {spec.name}...\tF{i+2}",
                         partial(self.start, acq))

    def start(self, acq):
        """
        Called when the menu entry is selected
        """
        main_data = self.main_app.main_data

        # Force the acquisition tab to be selected, and make sure that all
        # streams are paused
        acqui_tab = main_data.getTabByName("sparc_acqui")
        self.main_app.main_data.tab.value = acqui_tab
        acqui_tab.streambar_controller.pauseStreams()

        # First time, create a proper filename
        acq.update_filename()

        # immediately switch optical path, to save time
        main_data.opm.setPath(acq.stream)  # non-blocking

        dlg = AcquisitionDialog(self, "External spectrum acquisition")

        dlg.addStream(acq.stream, 2)  # 2 = spectrum view

        self._setup_sbar_cont(dlg.streambar_controller)
        dlg.addSettings(acq, self.vaconf)
        dlg.addButton("Close")
        dlg.addButton("Start", acq.acquire, face_colour='blue')
        dlg.btn_cancel.Bind(wx.EVT_BUTTON, acq._cancel_acq)

        dlg.Size = (1300, 800)  # px
        ans = dlg.ShowModal()  # Block until the window is closed

        # Make sure the streams are not playing anymore
        dlg.streambar_controller.pauseStreams()
        dlg.Destroy()

        if ans in (0, wx.ID_CLOSE, wx.ID_CANCEL):
            logging.info("Window closed without acquisition")
        elif ans == 1:
            logging.info("Spectrum acquisition completed")
            self.showAcquisition(acq.filename.value)
        else:
            logging.warning("Got unknown return code %s", ans)

    def _show_axes(self, sctrl, axes):
        """
        Show axes in settings panel for a given stream.
        sctrl (StreamController): stream controller
        axes (str -> (str, Actuator or None)): list of axes to display, with their
          display name, and actual axis name and component
        """
        stream_configs = get_stream_settings_config()
        stream_config = stream_configs.get(type(sctrl.stream), {})

        # Add Axes (in same order as config)
        va_names = util.sorted_according_to(axes.keys(), list(stream_config.keys()))
        for va_name in va_names:
            axis_name, comp = axes[va_name]
            if comp is None:
                logging.debug("Skipping axis %s for non existent component",
                              va_name)
                continue
            if va_name not in comp.axes:
                logging.debug("Skipping non existent axis %s on component %s",
                              va_name, comp.name)
                continue
            conf = stream_config.get(va_name)
            conf["label"] = label_to_human(va_name)
            sctrl.add_axis_entry(va_name, comp, conf)

    def _getAffectingSpectrograph(self, comp):
        """
        Find which spectrograph matters for the given component (ex, spectrometer)
        comp (Component): the hardware which is affected by a spectrograph
        return (None or Component): the spectrograph affecting the component
        """
        cname = comp.name
        main_data = self.main_app.main_data
        for spg in (main_data.spectrograph, main_data.spectrograph_ded):
            if spg is not None and cname in spg.affects.value:
                return spg
        else:
            logging.warning("No spectrograph found affecting component %s", cname)
            # spg should be None, but in case it's an error in the microscope file
            # and actually, there is a spectrograph, then use that one
            return main_data.spectrograph

    @call_in_wx_main
    def _setup_sbar_cont(self, ss_cont):
        """
        Finish setting up the spectrum stream
        """
        # The following code needs to be run asynchronously to make sure the streams are added to
        # the streambar controller first in .addStream.
        scont = ss_cont.stream_controllers[0]
        main_data = self.main_app.main_data

        # Add spectrograph axis to the stream
        spg = self._getAffectingSpectrograph(scont.stream.detector)
        axes = {"wavelength": ("wavelength", spg),
                "grating": ("grating", spg),
                "slit-in": ("slit-in", spg),
               }

        # Also add light filter for the spectrum stream if it affects the detector
        for fw in (main_data.cl_filter, main_data.light_filter):
            if fw is None:
                continue
            if scont.stream.detector.name in fw.affects.value:
                axes["filter"] = ("band", fw)
                break

        self._show_axes(scont, axes)

        # Don't allow removing the stream
        scont.stream_panel.show_remove_btn(False)
