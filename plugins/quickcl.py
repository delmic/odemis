#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 30 Nov 2017

@author: Éric Piel

Copyright © 2017 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
'''

# A simple GUI to acquire quickly CL data and export it to TIFF or PNG files.

from collections import OrderedDict
from concurrent.futures._base import CancelledError
import logging
import math
import numpy
from odemis import dataio, model, gui, acq, util
from odemis.acq import stream
from odemis.acq.stream import CLStream, SEMStream
from odemis.gui.comp import canvas
from odemis.gui.conf import get_acqui_conf
from odemis.gui.conf.data import get_local_vas
from odemis.gui.cont.settings import SettingsController
from odemis.gui.cont.streams import StreamBarController
from odemis.gui.main_xrc import xrcfr_plugin
from odemis.gui.model import ContentView, MicroscopyGUIData
from odemis.gui.plugin import Plugin, AcquisitionDialog
from odemis.gui.util import img
from odemis.util.filename import guess_pattern, create_filename, update_counter
from odemis.model import InstantaneousFuture
import os
import time
import wx
from odemis.util.dataio import splitext

# Set to "True" to show a "Save" button
ALLOW_SAVE = False


class ContentAcquisitionDialog(AcquisitionDialog):

    # Overrides the standard window to be able to create a ContentView which
    # follows the e-beam HFW and has a stage

    def __init__(self, plugin, title, text=None, stage=None, fov_hw=None):
        """
        Creates a modal window. The return code is the button number that was
          last pressed before closing the window.
        title (str): The title of the window
        text (None or str): If provided, it is displayed at the top of the window
        stage (None or actuator with x/y axes)
        fov_hw=None
        """
        xrcfr_plugin.__init__(self, plugin.main_app.main_frame)

        self.plugin = plugin

        self.SetTitle(title)

        self._acq_future_connector = None
        self.canvas = None
        self.buttons = []  # The buttons
        self.current_future = None
        self.btn_cancel.Bind(wx.EVT_BUTTON, self._cancel_future)

        self.setting_controller = SettingsController(self.fp_settings,
                                                     "No settings defined")

        # Create a minimal model for use in the streambar controller

        self._dmodel = MicroscopyGUIData(plugin.main_app.main_data)
        self.microscope_view = ContentView("Plugin View left", stage=stage, fov_hw=fov_hw)
        self.viewport_l.setView(self.microscope_view, self._dmodel)
        self._dmodel.focussedView.value = self.microscope_view
        self._dmodel.views.value = [self.microscope_view]
        self._viewports = (self.viewport_l,)

        self.streambar_controller = StreamBarController(
            self._dmodel,
            self.pnl_streams,
            ignore_view=True
        )

        self.Refresh()
        self.Fit()


class LiveCLStream(SEMStream):
    """
    Same as the SEMStream, but different class to convince the GUI it's a CLStream
    Also provide a special logScale mode where the projection is applied with a
    logarithmic scale (beta).
    """

    def __init__(self, name, detector, dataflow, emitter, **kwargs):
        super(LiveCLStream, self).__init__(name, detector, dataflow, emitter, **kwargs)
        self.logScale = model.BooleanVA(False)
        self.logScale.subscribe(self._on_log_scale)

    def _on_log_scale(self, uselog):
        # Force recomputing the intensity range
        self._drange = None
        self._shouldUpdateImage()

    def _projectXY2RGB(self, data, tint=(255, 255, 255)):
        """
        Project a 2D spatial DataArray into a RGB representation
        data (DataArray): 2D DataArray
        tint ((int, int, int)): colouration of the image, in RGB.
        return (DataArray): 3D DataArray
        """
        if not self.logScale.value:
            return super(LiveCLStream, self)._projectXY2RGB(data, tint)

        # Log scale:
        # Map irange to 1 -> e^N
        # Compute the log (= data goes from 0->N)
        # Map to RGB 0->255

        LOG_MAX = 8  # Map between 0 -> LOG_MAX (magical value that tends to work)
        irange = self._getDisplayIRange()
        data = numpy.clip(data, irange[0], irange[1])
        # Actually map data to 0 -> (e^N)-1, and compute log(x+1)
        data -= irange[0]
        data = data * ((math.exp(LOG_MAX) - 1) / (float(irange[1]) - float(irange[0])))
        data = numpy.log1p(data)

        rgbim = util.img.DataArray2RGB(data, (0, LOG_MAX), tint)
        rgbim.flags.writeable = False
        md = self._find_metadata(data.metadata)
        md[model.MD_DIMS] = "YXC"  # RGB format
        return model.DataArray(rgbim, md)


CLStream.register(LiveCLStream)


class QuickCLPlugin(Plugin):
    name = "Quick CL"
    __version__ = "1.1"
    __author__ = u"Éric Piel"
    __license__ = "GPLv2"

    # Describe how the values should be displayed
    # See odemis.gui.conf.data for all the possibilities
    vaconf = OrderedDict((
        ("filename", {
            "tooltip": "Each acquisition will be saved with the name and the number appended.",
            "control_type": gui.CONTROL_SAVE_FILE,
        }),
        ("hasDatabar", {
            "label": "Include data-bar",
        }),
        ("logScale", {
            "label": "Logarithmic scale",
        }),
        ("expectedDuration", {
        }),
    ))

    def __init__(self, microscope, main_app):
        super(QuickCLPlugin, self).__init__(microscope, main_app)
        # Can only be used with a SPARC with CL detector
        if not microscope:
            return
        main_data = self.main_app.main_data
        if not main_data.ebeam or not main_data.cld:
            return
        self.light = main_data.light
        self.ccd = main_data.ccd

        self.conf = get_acqui_conf()
        self.filename = model.StringVA("")
        self.filename.subscribe(self._on_filename)

        self.expectedDuration = model.VigilantAttribute(1, unit="s", readonly=True)

        self.hasDatabar = model.BooleanVA(False)

        # Only put the VAs that do directly define the image as local, everything
        # else should be global. The advantage is double: the global VAs will
        # set the hardware even if another stream (also using the e-beam) is
        # currently playing, and if the VAs are changed externally, the settings
        # will be displayed correctly (and not reset the values on next play).
        emtvas = set()
        hwemtvas = set()
        for vaname in get_local_vas(main_data.ebeam, main_data.hw_settings_config):
            if vaname in ("resolution", "dwellTime", "scale"):
                emtvas.add(vaname)
            else:
                hwemtvas.add(vaname)

        self._sem_stream = stream.SEMStream(
            "Secondary electrons",
            main_data.sed,
            main_data.sed.data,
            main_data.ebeam,
            focuser=main_data.ebeam_focus,
            hwemtvas=hwemtvas,
            hwdetvas=None,
            emtvas=emtvas,
            detvas=get_local_vas(main_data.sed, main_data.hw_settings_config),
        )

        # This stream is used both for rendering and acquisition.
        # We use a SEMStream to just have a basic live feed
        # TODO: one problem with using the SEMStream is that they get the same
        # icon, and might end-up switch in the view after hiding/showing.
        self._cl_stream = LiveCLStream(
            "CL intensity",
            main_data.cld,
            main_data.cld.data,
            main_data.ebeam,
            focuser=main_data.ebeam_focus,
            emtvas=emtvas,
            detvas=get_local_vas(main_data.cld, main_data.hw_settings_config),
            opm=main_data.opm,
        )
        # TODO: allow to type in the resolution of the CL?
        # TODO: add the cl-filter axis (or reset it to pass-through?)
        self.logScale = self._cl_stream.logScale

        if hasattr(self._cl_stream, "detGain"):
            self._cl_stream.detGain.subscribe(self._on_cl_gain)

        # Update the acquisition time when it might change (ie, the scan settings
        # change)
        self._cl_stream.emtDwellTime.subscribe(self._update_exp_dur)
        self._cl_stream.emtResolution.subscribe(self._update_exp_dur)

        self._dlg = None

        self.addMenu("Acquisition/Quick CL...\tF2", self.start)

    def _update_filename(self):
        """
        Set filename from pattern in conf file
        """
        fn = create_filename(self.conf.last_path, self.conf.fn_ptn, '.png', self.conf.fn_count)
        self.conf.fn_count = update_counter(self.conf.fn_count)

        # Update the widget, without updating the pattern and counter again
        self.filename.unsubscribe(self._on_filename)
        self.filename.value = fn
        self.filename.subscribe(self._on_filename)

    def _on_filename(self, fn):
        """
        Warn if extension not .png, store path and pattern in conf file
        """
        bn, ext = splitext(fn)
        if not ext.endswith(".png") and not ALLOW_SAVE:
            logging.warning("Only PNG format is recommended to use")

        # Store the directory so that next filename is in the same place
        p, bn = os.path.split(fn)
        if p:
            self.conf.last_path = p

        # Save pattern
        self.conf.fn_ptn, self.conf.fn_count = guess_pattern(fn)

    def _update_exp_dur(self, _=None):
        """
        Shows how long the CL takes to acquire
        """
        tott = self._cl_stream.estimateAcquisitionTime()
        tott = math.ceil(tott)  # round-up to 1s

        # Use _set_value as it's read only
        self.expectedDuration._set_value(tott, force_write=True)

    def _on_cl_gain(self, g):
        # This works around an annoyance on the current hardware/GUI:
        # the histogram range can only increase. However, for now the hardware
        # sends data in a small range, but at different value depending on the
        # gain. This causes the range to rapidly grow when changing the gain,
        # but once the actual data range is stable, it looks tiny on the whole
        # histogram. => Force resizing when changing gain.
        self._cl_stream._drange_unreliable = False
        logging.debug("Set the drange back to unreliable")

    # keycode to FoV ratio: 0.9 ~= 90% of the screen
    _key_to_move = {
        wx.WXK_LEFT: (-0.9, 0),
        wx.WXK_RIGHT: (0.9, 0),
        wx.WXK_UP: (0, 0.9),
        wx.WXK_DOWN: (0, -0.9),
    }

    def on_char(self, evt):
        key = evt.GetKeyCode()

        if (canvas.CAN_DRAG in self._canvas.abilities and
            key in self._key_to_move):
            move = self._key_to_move[key]
            if evt.ShiftDown():  # softer
                move = tuple(s / 8 for s in move)

            if self._dlg.microscope_view.fov_hw:
                fov_x = self._dlg.microscope_view.fov_hw.horizontalFoV.value
                shape = self._dlg.microscope_view.fov_hw.shape
                fov = (fov_x, fov_x * shape[1] / shape[0])
            else:
                fov = self._dlg.microscope_view.fov.value
            shift = [m * f for m, f in zip(move, fov)]
            self._dlg.microscope_view.moveStageBy(shift)

            # We "eat" the event, so the canvas will never react to it
        else:
            evt.Skip()  # Pretend we never got here in the first place

    def start(self):
        """
        Called when the menu entry is selected
        """
        main_data = self.main_app.main_data

        # Stop the streams of the active tab
        tab_data = main_data.tab.value.tab_data_model
        for s in tab_data.streams.value:
            s.should_update.value = False

        # First time, create a proper filename
        if not self.filename.value:
            self._update_filename()
        self._update_exp_dur()

        # immediately switch optical path, to save time
        main_data.opm.setPath(self._cl_stream)  # non-blocking

        # Add connection to SEM hFoV if possible
        fov_hw = None
        if main_data.ebeamControlsMag:
            fov_hw = main_data.ebeam
        dlg = ContentAcquisitionDialog(self, "Cathodoluminecense acquisition",
                                       stage=main_data.stage,
                                       fov_hw=fov_hw
                                       )
        self._dlg = dlg
        # Listen to the key events, to move the stage by 90% of the FoV when
        # pressing the arrow keys (instead of 100px).
        # Note: this only matters when the view is in focus
        # TODO: make it like the alignment tab, available everywhere
        if main_data.stage:
            self._canvas = dlg.viewport_l.canvas
            self._canvas.Bind(wx.EVT_CHAR, self.on_char)

        if fov_hw:
            dlg.viewport_l.canvas.fit_view_to_next_image = False

        # Ideally, the user should be able to pick the cf-filter, for now, we
        # just hard-code to "pass-through".
        # TODO: provide a cl-filter control in the CL stream
        if main_data.cl_filter and "band" in main_data.cl_filter.axes:
            # find the "pass-through"
            bdef = main_data.cl_filter.axes["band"]
            for b, bn in bdef.choices.items():
                if bn == "pass-through":
                    main_data.cl_filter.moveAbs({"band": b})
                    break
            else:
                logging.debug("Pass-through not found in the CL-filter")

        dlg.addStream(self._sem_stream)
        dlg.addStream(self._cl_stream)
        dlg.addSettings(self, self.vaconf)
        if ALLOW_SAVE:
            dlg.addButton("Save", self.save, face_colour='blue')
        dlg.addButton("Export", self.export, face_colour='blue')

        dlg.Maximize()
        dlg.ShowModal()

        # Window is closed

        # Make sure the streams are not playing anymore
        dlg.streambar_controller.pauseStreams()
        if dlg: # If dlg hasn't been destroyed yet
            dlg.Destroy()
        self._dlg = None

        # Update filename in main window
        tab_acqui = main_data.getTabByName("sparc_acqui")
        tab_acqui.acquisition_controller.update_fn_suggestion()

    def _acq_canceller(self, future):
        return future._cur_f.cancel()

    def _acquire(self, dlg, future):
        # Stop the streams
        dlg.streambar_controller.pauseStreams()

        # Acquire CL (to be sure it's the right data)
        ss = [self._cl_stream]
        dur = acq.estimateTime(ss)
        startt = time.time()
        future._cur_f = InstantaneousFuture()
        future.task_canceller = self._acq_canceller
        future.set_running_or_notify_cancel()  # Indicate the work is starting now
        future.set_progress(end=startt + dur)
        dlg.showProgress(future)

        future._cur_f = acq.acquire(ss)
        das, e = future._cur_f.result()
        if future.cancelled():
            raise CancelledError()

        if e:
            raise e

        return das

    def export(self, dlg):
        """
        Stores the current CL data into a PNG file
        """
        f = model.ProgressiveFuture()
        ss = [self._cl_stream]

        try:
            das = self._acquire(dlg, f)
        except CancelledError:
            logging.debug("Stopping acquisition + export, as it was cancelled")
            return
        except Exception as e:
            logging.exception("Failed to acquire CL data: %s", e)
            return

        # Note: the user never needs to store the raw data or the SEM data
        fn = self.filename.value
        exporter = dataio.find_fittest_converter(fn, allowlossy=True)

        try:
            rgbi = self._cl_stream.image.value
            if self.hasDatabar.value:
                # Use MPP and FoV so that the whole image is displayed, at 1:1
                view_pos = rgbi.metadata[model.MD_POS]
                pxs = rgbi.metadata[model.MD_PIXEL_SIZE]
                # Shape is YXC
                view_hfw = rgbi.shape[1] * pxs[0], rgbi.shape[0] * pxs[1]
                exdata = img.images_to_export_data(ss,
                                                   view_hfw, view_pos,
                                                   draw_merge_ratio=1.0,
                                                   raw=False,
                                                   interpolate_data=False,
                                                   logo=self.main_app.main_frame.legend_logo)
            else:
                exdata = rgbi
            exporter.export(fn, exdata)
        except Exception:
            logging.exception("Failed to store data in %s", fn)

        f.set_result(None)  # Indicate it's over
        self._update_filename()

    def save(self, dlg):
        """
        Stores the current CL data into a TIFF/HDF5 file
        """
        f = model.ProgressiveFuture()

        try:
            das = self._acquire(dlg, f)
        except CancelledError:
            logging.debug("Stopping acquisition + export, as it was cancelled")
            return
        except Exception as e:
            logging.exception("Failed to acquire CL data: %s", e)
            return

        fn = self.filename.value
        bn, ext = os.path.splitext(fn)
        if ext == ".png":
            logging.debug("Using HDF5 instead of PNG")
            fn = bn + ".h5"
        exporter = dataio.find_fittest_converter(fn)

        try:
            exporter.export(fn, das)
        except Exception:
            logging.exception("Failed to store data in %s", fn)

        f.set_result(None)  # Indicate it's over
        self._update_filename()
