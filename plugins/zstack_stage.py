# -*- coding: utf-8 -*-
"""
Created on 12 Feb 2026

@author: Éric Piel

Plugin for the METEOR (and MIMAS).
Acquire a z-stack using the sample stage z axis instead of the focus actuator.
Uses the same z-stack parameters as the Localization tab.

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
import time
from typing import Dict, List

import wx

from odemis import dataio, model
from odemis.acq import acqmng
from odemis.acq.stream import BrightfieldStream, FluoStream, Stream
from odemis.gui.conf import get_acqui_conf
from odemis.gui.plugin import Plugin
from odemis.gui.util import call_in_wx_main
from odemis.gui.util.widgets import ProgressiveFutureConnector
from odemis.util.comp import generate_zlevels
from odemis.util.filename import create_filename


class StageZStackPlugin(Plugin):
    name = "Stage Z Stack"
    __version__ = "1.0"
    __author__ = "Éric Piel"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)

        # Can only be used with a microscope with a stage
        self.main_data = main_app.main_data

        if not microscope or self.main_data.stage is None:
            logging.info("Stage Z-Stack plugin not available: no stage found")
            return

        # Check that the stage has a z-axis
        if 'z' not in self.main_data.stage.axes:
            logging.info("Stage Z-Stack plugin not available: stage has no z-axis")
            return

        try:
            tab = self.main_data.getTabByName("cryosecom-localization")
            self.tab_data = tab.tab_data_model
            self.tab_panel = tab.panel
        except LookupError:
            logging.info("Stage Z-Stack plugin not available: Localization tab not found")
            return

        self.stage = self.main_data.stage
        self._acq_future = None
        self._gauge_future_conn = None
        self._original_stage_pos = None

        # Add menu entry
        self.addMenu("Acquisition/Stage Z-Stack...", self.start)
        logging.info("Stage Z-Stack plugin loaded")

    def start(self):
        """Called when the menu entry is selected"""
        tab = self.main_data.tab.value

        # Check that we're on the correct tab
        if tab.name != "cryosecom-localization":
            box = wx.MessageDialog(
                self.main_app.main_frame,
                "Stage Z-Stack acquisition must be done from the LOCALIZATION tab.",
                "Stage Z-Stack acquisition not possible",
                wx.OK | wx.ICON_STOP
            )
            box.ShowModal()
            box.Destroy()
            return

        # Check if there are streams to acquire
        if not self.tab_data.acquisitionStreams.value:
            box = wx.MessageDialog(
                self.main_app.main_frame,
                "No streams available for acquisition. Please add streams first.",
                "Stage Z-Stack acquisition not possible",
                wx.OK | wx.ICON_STOP
            )
            box.ShowModal()
            box.Destroy()
            return

        # Pause live streams
        tab.streambar_controller.pauseStreams()

        # Store original stage position
        self._original_stage_pos = self.stage.position.value

        # Start acquisition, which will continue in the background and call _on_acquisition_done when finished
        try:
            self._acquire()
        except Exception as e:
            logging.exception("Failed to start stage z-stack acquisition: %s", e)

            # Restore stage position
            self.stage.moveAbs(self._original_stage_pos)

    def _acquire(self):
        """Start the z-stack acquisition"""
        # Get acquisition streams
        acq_streams = self.tab_data.acquisitionStreams.value

        logging.debug("Acquisition streams: %s", acq_streams)

        # Generate z-levels based on current stage position and z-stack parameters
        try:
            zmin = self.tab_data.zMin.value
            zmax = self.tab_data.zMax.value
            zstep = self.tab_data.zStep.value

            logging.info(
                "Generating z-levels with zmin=%g, zmax=%g, zstep=%g",
                zmin, zmax, zstep
            )

            levels = generate_zlevels(self.stage, (zmin, zmax), zstep)

            logging.info("Generated %d z-levels: %s", len(levels), levels)

        except (ValueError, IndexError, ZeroDivisionError, KeyError) as e:
            logging.exception("Failed to generate z-levels: %s", e)
            return

        # Only apply z-stack to optical streams (not SEM streams)
        zlevels: Dict[Stream, List[float]] = {
            s: levels for s in acq_streams
            if isinstance(s, (FluoStream, BrightfieldStream))
        }

        if not zlevels:
            logging.warning("No optical streams found for z-stack acquisition")
            return

        logging.info("Applying z-stack to %d streams", len(zlevels))

        # Start the acquisition
        self.main_data.is_acquiring.value = True

        for s in acq_streams:
            # Hack: we temporarily change .focuser of the streams.
            # This is a little dangerous, as if something goes wrong, the streams
            # stay as-is. The better way would be to duplicate the streams, but
            # that's not so simple with the different types of streams, and copying
            # the values of the VAs.
            if isinstance(s, (FluoStream, BrightfieldStream)):
                s._focuser = self.stage

        self._acq_future = acqmng.acquireZStack(
            acq_streams,
            zlevels,
            self.main_data.settings_obs
        )

        # link the acquisition gauge to the acquisition future
        self._gauge_future_conn = ProgressiveFutureConnector(
            future=self._acq_future,
            bar=self.tab_panel.gauge_cryosecom_acq,
            label=self.tab_panel.txt_cryosecom_left_time,
            full=False,
        )

        logging.info("Stage Z-Stack acquisition started")

        # Show progress in the status bar or create a simple progress dialog
        # For now, we'll just add a callback to handle completion
        self._acq_future.add_done_callback(self._on_acquisition_done)

    @call_in_wx_main
    def _on_acquisition_done(self, future):
        """Called when the acquisition is complete"""
        self.main_data.is_acquiring.value = False
        self._acq_future = None

        # Hack: restore the focuser
        acq_streams = self.tab_data.acquisitionStreams.value
        for s in acq_streams:
            if isinstance(s, (FluoStream, BrightfieldStream)):
                s._focuser = self.main_data.focuser

        # Restore original stage position
        logging.debug("Restoring stage to original position: %s", self._original_stage_pos)
        self.stage.moveAbs(self._original_stage_pos)
        # No wait, as it's fast (~1s), and in the worst case the user will notice it and wait

        # Get the acquisition results
        try:
            data, exp = future.result()

            if exp:
                logging.warning("Acquisition completed with exceptions: %s", exp)
                dlg = wx.MessageDialog(
                    self.main_app.main_frame,
                    f"Acquisition only partially completed:\n{exp}",
                    "Stage Z-Stack acquisition completed",
                    wx.OK | wx.ICON_WARNING
                )
                dlg.ShowModal()
                dlg.Destroy()

            # Save the data automatically
            self._save_data(data)

            logging.info("Stage Z-Stack acquisition completed successfully")

        except Exception as e:
            logging.exception("Stage Z-Stack acquisition failed: %s", e)
            dlg = wx.MessageDialog(
                self.main_app.main_frame,
                f"Acquisition failed:\n{e}",
                "Stage Z-Stack acquisition failed",
                wx.OK | wx.ICON_ERROR
            )
            dlg.ShowModal()
            dlg.Destroy()

    def _save_data(self, data: List) -> None:
        """Save the acquired data to a file"""
        try:
            # Generate filename with timestamp
            config = get_acqui_conf()
            basename = time.strftime("%Y%m%d-%H%M%S-stage-zstack")
            filename = f"{config.last_path}/{basename}{config.last_extension}"

            logging.info("Saving stage z-stack data to: %s", filename)

            # Find appropriate exporter and save
            exporter = dataio.find_fittest_converter(filename)
            exporter.export(filename, data)

            logging.info("Data saved successfully to: %s", filename)

        except Exception as e:
            logging.exception("Failed to save data: %s", e)
