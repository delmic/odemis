# -*- coding: utf-8 -*-
"""
Created on 29 Jul 2025

@author: Nandish Patel
Copyright © 2025 Nandish Patel, Delmic

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
import os
import queue
import statistics
import threading
from typing import List, Optional, Union

import matplotlib.pyplot as plt
import numpy
import wx
from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg

import odemis.gui as gui
from odemis import dataio, model
from odemis.dataio import hdf5
from odemis.gui.comp.settings import SettingsPanel
from odemis.gui.cont.tabs.tab import Tab
from odemis.gui.plugin import Plugin
from odemis.gui.util import get_picture_folder


class CancelledError(Exception):
    pass


class Sparc2AlignmentDataCollectorPlugin(Plugin):
    name = "SPARC2 mirror alignment data collector"
    __version__ = "1.0"
    __author__ = "Nandish Patel"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)

        # It only makes sense if the SPARC2 alignment tab is present
        try:
            self.alignment_tab = main_app.main_data.getTabByName("sparc2_align")
        except LookupError:
            logging.debug(
                "Not loading SPARC2 alignment data collector as alignment tab is not present"
            )
            return

        self.mirror = main_app.main_data.mirror
        self.mirror_xy = main_app.main_data.mirror_xy
        self.stage = main_app.main_data.stage
        self.ebeam = main_app.main_data.ebeam
        self.ebeam_focus = main_app.main_data.ebeam_focus

        # Ensure components are available
        if not (self.mirror and self.mirror_xy and self.stage and self.ebeam and self.ebeam_focus):
            logging.debug("Not loading SPARC2 mirror alignment data collector as essential components not found")
            return

        self.parent = self.main_app.GetTopWindow()

        self.addMenu("Help/Development/Mirror alignment data collector", self._alignment_data_collector)

    def _alignment_data_collector(self):
        dlg = AlignmentDataCollectorDialog(
            self.parent,
            self.alignment_tab,
            self.mirror,
            self.mirror_xy,
            self.stage,
            self.ebeam,
            self.ebeam_focus,
        )
        dlg.Show()


class AlignmentDataCollectorDialog(wx.Dialog):
    def __init__(
        self,
        parent,
        tab: Tab,
        mirror: model.HwComponent,
        mirror_xy: model.HwComponent,
        stage: model.HwComponent,
        ebeam: model.HwComponent,
        ebeam_focus: model.HwComponent,
    ):
        super().__init__(parent, title="SPARC2 mirror alignment cube acquisition")
        self.tab = tab
        self.mirror = mirror
        self.mirror_xy = mirror_xy
        self.stage = stage
        self.ebeam = ebeam
        self.ebeam_focus = ebeam_focus
        self.n_images = 0
        self._exporter = dataio.get_converter(hdf5.FORMAT)
        self._running = False
        self._cancel_requested = False
        self._pause_requested = False
        self._resume_requested = threading.Event()
        self._aligned_pos = None
        self._save_queue = queue.Queue()
        self._save_thread = threading.Thread(target=self._saving_thread, daemon=True)

        self._create_widgets()
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.Bind(wx.EVT_SIZE, self._on_dialog_size)
        self._save_thread.start()

    def _on_dialog_size(self, evt):
        """Resize the panel upon dialog size."""
        x, y = self.GetSize()
        self.panel.SetSize((x, y))
        self.panel.Layout()
        self.panel.Refresh()
        evt.Skip()

    def on_evt_minus_dl(self, evt):
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        # Call on_text_enter explicitly as it is not binded
        ctrl.on_text_enter(evt)
        if self.cbox_equal_dl.GetValue():
            self.plus_dl.SetValue(ctrl.GetValue())

    def on_evt_minus_ds(self, evt):
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        # Call on_text_enter explicitly as it is not binded
        ctrl.on_text_enter(evt)
        if self.cbox_equal_ds.GetValue():
            self.plus_ds.SetValue(ctrl.GetValue())

    def on_evt_minus_dz(self, evt):
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        # Call on_text_enter explicitly as it is not binded
        ctrl.on_text_enter(evt)
        if self.cbox_equal_dz.GetValue():
            self.plus_dz.SetValue(ctrl.GetValue())

    def on_evt_plus_dl(self, evt):
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        # Call on_text_enter explicitly as it is not binded
        ctrl.on_text_enter(evt)
        if self.cbox_equal_dl.GetValue():
            self.minus_dl.SetValue(ctrl.GetValue())

    def on_evt_plus_ds(self, evt):
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        # Call on_text_enter explicitly as it is not binded
        ctrl.on_text_enter(evt)
        if self.cbox_equal_ds.GetValue():
            self.minus_ds.SetValue(ctrl.GetValue())

    def on_evt_plus_dz(self, evt):
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        # Call on_text_enter explicitly as it is not binded
        ctrl.on_text_enter(evt)
        if self.cbox_equal_dz.GetValue():
            self.minus_dz.SetValue(ctrl.GetValue())

    def on_evt_cbox_equal_dl(self, evt):
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        if ctrl.GetValue():
            self.plus_dl.SetValue(self.minus_dl.GetValue())

    def on_evt_cbox_equal_ds(self, evt):
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        if ctrl.GetValue():
            self.plus_ds.SetValue(self.minus_ds.GetValue())

    def on_evt_cbox_equal_dz(self, evt):
        ctrl = evt.GetEventObject()
        if not ctrl:
            return

        if ctrl.GetValue():
            self.plus_dz.SetValue(self.minus_dz.GetValue())

    def _add_dir_dialog(self, label_text: str, sizer: wx.Sizer, value: Optional[str] = None) -> wx.TextCtrl:
        """
        Add a label, text control, and browse button to the panel.

        This manually implements a directory picker, with the ability to create
        a new directory.

        :param label_text: Label text to display.
        :param sizer: The wx sizer in which the widgets needs to be added.
        :param value: Initial path to display in the text control.

        :return: The created text control.
        """
        lbl_ctrl = wx.StaticText(self, -1, str(label_text))

        path_display = wx.TextCtrl(self, value=str(value or ""), style=wx.TE_READONLY)
        path_display.SetForegroundColour(gui.FG_COLOUR_DIS)
        path_display.SetBackgroundColour(self.GetBackgroundColour())

        def on_browse(evt):
            current_path = path_display.GetValue()

            with wx.DirDialog(self, "Choose a directory:", defaultPath=current_path) as dialog:
                if dialog.ShowModal() == wx.ID_OK:
                    # If the user clicked OK, update the text control
                    new_path = dialog.GetPath()
                    path_display.SetValue(new_path)

        browse_button = wx.Button(self, label="Browse")
        browse_button.Bind(wx.EVT_BUTTON, on_browse)

        hbox = wx.BoxSizer(wx.HORIZONTAL)
        hbox.Add(lbl_ctrl, flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)
        hbox.Add(path_display, 1, flag=wx.EXPAND | wx.ALIGN_CENTER_VERTICAL, border=5)
        hbox.Add(browse_button, flag=wx.LEFT | wx.ALIGN_CENTER_VERTICAL, border=5)

        sizer.Add(hbox, flag=wx.ALL | wx.EXPAND, border=5)

        return path_display

    def _create_widgets(self):
        """Create and initialize all GUI widgets for the mirror alignment data collector dialog."""
        self.panel = wx.Panel(self, size=(800, 850))
        vbox = wx.BoxSizer(wx.VERTICAL)
        self.settings_panel = SettingsPanel(self.panel)

        # Parameters
        _, self.minus_dl = self.settings_panel.add_float_field(
            label_text="-dl",
            value=0.0,
            conf={
                "min_val": 0.0,
                "max_val": statistics.mean(self.mirror.axes["l"].range),
                "unit": "m",
                "accuracy": 6,
                "key_step": 1e-6,
            },
        )
        # Unbind _NumberTextCtrl on_text_enter first to make use of on_evt_minus_dl
        # on_evt_minus_dl first calls on_text_enter and then does additional things
        self.minus_dl.Unbind(wx.EVT_TEXT_ENTER, handler=self.minus_dl.on_text_enter)
        self.minus_dl.Bind(wx.EVT_TEXT_ENTER, self.on_evt_minus_dl)

        _, self.plus_dl = self.settings_panel.add_float_field(
            label_text="+dl",
            value=0.0,
            conf={
                "min_val": 0.0,
                "max_val": statistics.mean(self.mirror.axes["l"].range),
                "unit": "m",
                "accuracy": 6,
                "key_step": 1e-6,
            },
        )
        self.plus_dl.Unbind(wx.EVT_TEXT_ENTER, handler=self.plus_dl.on_text_enter)
        self.plus_dl.Bind(wx.EVT_TEXT_ENTER, self.on_evt_plus_dl)

        _, self.cbox_equal_dl = self.settings_panel.add_checkbox_control("Equal ±dl")
        self.cbox_equal_dl.Bind(wx.EVT_CHECKBOX, self.on_evt_cbox_equal_dl)

        _, self.minus_ds = self.settings_panel.add_float_field(
            label_text="-ds",
            value=0.0,
            conf={
                "min_val": 0.0,
                "max_val": statistics.mean(self.mirror.axes["s"].range),
                "unit": "m",
                "accuracy": 6,
                "key_step": 1e-6,
            },
        )
        self.minus_ds.Unbind(wx.EVT_TEXT_ENTER, handler=self.minus_ds.on_text_enter)
        self.minus_ds.Bind(wx.EVT_TEXT_ENTER, self.on_evt_minus_ds)

        _, self.plus_ds = self.settings_panel.add_float_field(
            label_text="+ds",
            value=0.0,
            conf={
                "min_val": 0.0,
                "max_val": statistics.mean(self.mirror.axes["s"].range),
                "unit": "m",
                "accuracy": 6,
                "key_step": 1e-6,
            },
        )
        self.plus_ds.Unbind(wx.EVT_TEXT_ENTER, handler=self.plus_ds.on_text_enter)
        self.plus_ds.Bind(wx.EVT_TEXT_ENTER, self.on_evt_plus_ds)

        _, self.cbox_equal_ds = self.settings_panel.add_checkbox_control("Equal ±ds")
        self.cbox_equal_ds.Bind(wx.EVT_CHECKBOX, self.on_evt_cbox_equal_ds)

        _, self.minus_dz = self.settings_panel.add_float_field(
            label_text="-dz",
            value=0.0,
            conf={
                "min_val": 0.0,
                "max_val": statistics.mean(self.stage.axes["z"].range),
                "unit": "m",
                "accuracy": 6,
                "key_step": 1e-6,
            },
        )
        self.minus_dz.Unbind(wx.EVT_TEXT_ENTER, handler=self.minus_dz.on_text_enter)
        self.minus_dz.Bind(wx.EVT_TEXT_ENTER, self.on_evt_minus_dz)

        _, self.plus_dz = self.settings_panel.add_float_field(
            label_text="+dz",
            value=0.0,
            conf={
                "min_val": 0.0,
                "max_val": statistics.mean(self.stage.axes["z"].range),
                "unit": "m",
                "accuracy": 6,
                "key_step": 1e-6,
            },
        )
        self.plus_dz.Unbind(wx.EVT_TEXT_ENTER, handler=self.plus_dz.on_text_enter)
        self.plus_dz.Bind(wx.EVT_TEXT_ENTER, self.on_evt_plus_dz)

        _, self.cbox_equal_dz = self.settings_panel.add_checkbox_control("Equal ±dz")
        self.cbox_equal_dz.Bind(wx.EVT_CHECKBOX, self.on_evt_cbox_equal_dz)

        _, self.nl = self.settings_panel.add_int_field(
            label_text="nl",
            value=1,
            conf={
                "min_val": 1,
                "max_val": 10000,
                "key_step": 1,
            },
        )
        _, self.ns = self.settings_panel.add_int_field(
            label_text="ns",
            value=1,
            conf={
                "min_val": 1,
                "max_val": 10000,
                "key_step": 1,
            },
        )
        _, self.nz = self.settings_panel.add_int_field(
            label_text="nz",
            value=1,
            conf={
                "min_val": 1,
                "max_val": 10000,
                "key_step": 1,
            },
        )

        vbox.Add(self.settings_panel, flag=wx.ALL | wx.EXPAND, border=5)

        self.path = self._add_dir_dialog("Save path", sizer=vbox, value=get_picture_folder())

        # Status label + progress
        self.status_lbl = wx.StaticText(self.panel, label="Status: Idle")
        vbox.Add(self.status_lbl, flag=wx.LEFT | wx.RIGHT | wx.TOP, border=5)
        self.progress = wx.Gauge(self.panel, range=100)
        vbox.Add(self.progress, flag=wx.LEFT | wx.RIGHT | wx.EXPAND, border=5)

        # Matplotlib 3D
        self.figure = plt.figure()
        self.ax = self.figure.add_subplot(111, projection="3d")
        self.canvas = FigureCanvasWxAgg(self.panel, -1, self.figure)
        vbox.Add(self.canvas, proportion=0, flag=wx.EXPAND | wx.ALL, border=5)

        # Buttons
        hbox_btns = wx.BoxSizer(wx.HORIZONTAL)
        self.start_btn = wx.Button(self.panel, label="Start Acquisition")
        self.start_btn.Bind(wx.EVT_BUTTON, self._on_start)
        hbox_btns.Add(self.start_btn)

        self.pause_btn = wx.Button(self.panel, label="Pause")
        self.pause_btn.Disable()
        self.pause_btn.Bind(wx.EVT_BUTTON, self._on_pause)
        hbox_btns.Add(self.pause_btn, flag=wx.LEFT, border=5)

        self.resume_btn = wx.Button(self.panel, label="Resume")
        self.resume_btn.Disable()
        self.resume_btn.Bind(wx.EVT_BUTTON, self._on_resume)
        hbox_btns.Add(self.resume_btn, flag=wx.LEFT, border=5)

        self.cancel_btn = wx.Button(self.panel, label="Cancel")
        self.cancel_btn.Disable()
        self.cancel_btn.Bind(wx.EVT_BUTTON, self._on_cancel)
        hbox_btns.Add(self.cancel_btn, flag=wx.LEFT, border=5)

        vbox.Add(hbox_btns, flag=wx.ALL | wx.ALIGN_CENTER, border=5)

        self.SetSizerAndFit(vbox)
        self.Layout()

    def _enqueue_save(self, filepath: str, raw_data: Union[model.DataArray, List[model.DataArray]]):
        """
        Enqueue a save operation for the given data to be processed by the saving thread.

        :param filepath: The path where the data should be saved.
        :param raw_data: The data to be saved, either a single DataArray or a list of DataArrays.

        This method adds the save request to the internal queue, allowing saving to occur
        asynchronously in a background thread.
        """
        self._save_queue.put((filepath, raw_data))

    def _saving_thread(self):
        """
        Background thread that processes save requests from the queue.

        Continuously retrieves (filepath, raw_data) tuples from the internal save queue,
        saves the data using the exporter, and marks each task as done.
        """
        try:
            while True:
                filepath, raw_data = self._save_queue.get()
                logging.info("Saving data %s in thread", filepath)
                self._exporter.export(filepath, raw_data)
                self._save_queue.task_done()
        except Exception:
            logging.exception("Failure in the saving thread")
        finally:
            logging.debug("Saving thread done")

    def _on_start(self, _):
        """Event handler for the 'Start Acquisition' button."""
        if self._running:
            return

        try:
            minus_dl = float(self.minus_dl.GetValue())
            minus_ds = float(self.minus_ds.GetValue())
            minus_dz = float(self.minus_dz.GetValue())
            plus_dl = float(self.plus_dl.GetValue())
            plus_ds = float(self.plus_ds.GetValue())
            plus_dz = float(self.plus_dz.GetValue())
            nl = int(self.nl.GetValue())
            ns = int(self.ns.GetValue())
            nz = int(self.nz.GetValue())
            path = self.path.GetValue()
        except Exception as e:
            wx.MessageBox(f"Invalid parameters: {e}", "Error", wx.ICON_ERROR)
            return

        self._running = True
        self._cancel_requested = False
        self.start_btn.Disable()
        self.pause_btn.Enable()
        self.cancel_btn.Enable()
        self.status_lbl.SetLabel("Status: Running...")
        self.n_images = nl * ns * nz
        self.progress.SetRange(self.n_images)
        self.progress.SetValue(0)

        self._aligned_pos = {
            "l": self.mirror.position.value["l"],
            "s": self.mirror.position.value["s"],
            "x": self.mirror_xy.position.value["x"],
            "y": self.mirror_xy.position.value["y"],
            "z": self.stage.position.value["z"],
        }

        thread = threading.Thread(
            target=self._run_acquisition,
            args=(
                minus_dl,
                plus_dl,
                minus_ds,
                plus_ds,
                minus_dz,
                plus_dz,
                nl,
                ns,
                nz,
                path,
            ),
            daemon=True,
        )
        thread.start()

    def _on_cancel(self, _):
        """Event handler for the 'Cancel' button."""
        self._cancel_requested = True
        self.status_lbl.SetLabel("Status: Cancel requested...")
        self.cancel_btn.Disable()

    def _restore_position(self):
        """Restore the mirror and stage positions to the previously stored aligned position."""
        if self._aligned_pos:
            self.mirror.moveAbs({"l": self._aligned_pos["l"]}).result()
            self.mirror.moveAbs({"s": self._aligned_pos["s"]}).result()
            self.stage.moveAbs({"z": self._aligned_pos["z"]}).result()

    def _update_plot(self, l, s, z):
        self.ax.clear()
        self.ax.set_xlabel("Mirror l (m)", fontsize=10)
        self.ax.set_ylabel("Mirror s (m)", fontsize=10)
        self.ax.set_zlabel("Stage z (m)", fontsize=10)
        self.ax.set_title("Acquisition Progress")
        self.ax.scatter(l, s, z, c="gray", alpha=0.5)
        self.ax.scatter([l[-1]], [s[-1]], [z[-1]], c="red", label="Current")
        self.ax.scatter(
            self._aligned_pos["l"],
            self._aligned_pos["s"],
            self._aligned_pos["z"],
            c="blue",
            label="Aligned",
        )
        self.ax.legend()
        self.canvas.draw()

    def _run_acquisition(
        self,
        minus_dl: float,
        plus_dl: float,
        minus_ds: float,
        plus_ds: float,
        minus_dz: float,
        plus_dz: float,
        nl: int,
        ns: int,
        nz: int,
        path: str
    ):
        """
        Perform the mirror alignment cube acquisition.

        Iterates over the specified ranges for mirror (l, s) and stage (z) positions,
        acquires images at each position, updates metadata, saves the data asynchronously,
        and updates the progress plot and status. Handles cancellation and pausing requests,
        and restores the original aligned position when finished or paused.

        :param minus_dl: Negative offset for mirror l axis.
        :param plus_dl: Positive offset for mirror l axis.
        :param minus_ds: Negative offset for mirror s axis.
        :param plus_ds: Positive offset for mirror s axis.
        :param minus_dz: Negative offset for stage z axis.
        :param plus_dz: Positive offset for stage z axis.
        :param nl: Number of steps for mirror l axis.
        :param ns: Number of steps for mirror s axis.
        :param nz: Number of steps for stage z axis.
        :param path: Directory path to save acquired data.
        """
        l0 = self._aligned_pos["l"]
        s0 = self._aligned_pos["s"]
        z0 = self._aligned_pos["z"]
        l_min, l_max = self.mirror.axes["l"].range
        s_min, s_max = self.mirror.axes["s"].range
        z_min, z_max = self.stage.axes["z"].range

        l_values = numpy.clip(numpy.linspace(l0 - minus_dl, l0 + plus_dl, nl), l_min, l_max)
        s_values = numpy.clip(numpy.linspace(s0 - minus_ds, s0 + plus_ds, ns), s_min, s_max)
        z_values = numpy.clip(numpy.linspace(z0 - minus_dz, z0 + plus_dz, nz), z_min, z_max)

        visited_l, visited_s, visited_z = [], [], []

        try:
            idx = 0
            # Pause the ccd stream
            self.tab._ccd_stream.is_active.value = False
            self.tab._ccd_stream.should_update.value = False
            # Pausing the ccd stream causes the e-beam to be blanked and mode as internal
            # make the mode as external and unblank the beam to get valid ccd data
            self.ebeam.external.value = True
            self.ebeam.blanker.value = False
            for l in l_values:
                if self._cancel_requested:
                    raise CancelledError("Cancelled by user.")
                self.mirror.moveAbs({"l": l}).result()
                for s in s_values:
                    if self._cancel_requested:
                        raise CancelledError("Cancelled by user.")
                    self.mirror.moveAbs({"s": s}).result()
                    for z in z_values:
                        if self._cancel_requested:
                            raise CancelledError("Cancelled by user.")
                        if self._pause_requested:
                            self._restore_position()
                            wx.CallAfter(self.status_lbl.SetLabel, "Status: Paused on aligned position")
                            # Once paused, play the ccd stream for the user to check the ccd image
                            # the user can do some re-alignment if necessary
                            self.tab._ccd_stream.is_active.value = True
                            self.tab._ccd_stream.should_update.value = True
                            self._resume_requested.clear()
                            self._resume_requested.wait()
                            # Once resumed, update the aligned position
                            # and make the ccd stream and e-beam ready for acquiring again
                            self._aligned_pos = {
                                "l": self.mirror.position.value["l"],
                                "s": self.mirror.position.value["s"],
                                "x": self.mirror_xy.position.value["x"],
                                "y": self.mirror_xy.position.value["y"],
                                "z": self.stage.position.value["z"],
                            }
                            self.tab._ccd_stream.is_active.value = False
                            self.tab._ccd_stream.should_update.value = False
                            self.ebeam.external.value = True
                            self.ebeam.blanker.value = False
                            self.mirror.moveAbs({"l": l}).result()
                            self.mirror.moveAbs({"s": s}).result()
                            wx.CallAfter(self.status_lbl.SetLabel, "Status: Resumed")
                            logging.debug("Resumed. New aligned position: %s", self._aligned_pos)
                        self.stage.moveAbs({"z": z}).result()

                        l = self.mirror.position.value["l"]
                        s = self.mirror.position.value["s"]
                        x = self.mirror_xy.position.value["x"]
                        y = self.mirror_xy.position.value["y"]
                        z = self.stage.position.value["z"]
                        forcemd = {}
                        forcemd[model.MD_EXTRA_SETTINGS] = {
                            "l": l,
                            "s": s,
                            "x": x,
                            "y": y,
                            "z": z,
                            "dl": self._aligned_pos["l"] - l,
                            "ds": self._aligned_pos["s"] - s,
                            "dx": self._aligned_pos["x"] - x,
                            "dy": self._aligned_pos["y"] - y,
                            "dz": self._aligned_pos["z"] - z,
                            "l_aligned": self._aligned_pos["l"],
                            "s_aligned": self._aligned_pos["s"],
                            "x_aligned": self._aligned_pos["x"],
                            "y_aligned": self._aligned_pos["y"],
                            "z_aligned": self._aligned_pos["z"],
                            "wd": self.ebeam_focus.position.value["z"],
                        }

                        filepath = os.path.join(path, f"{idx}_snapshot_{l}_{s}_{z}" + hdf5.EXTENSIONS[0])
                        logging.debug(f"Acquiring l={l} s={s} z={z}")
                        data = self.tab._ccd_stream.getSingleFrame()
                        data.metadata.update(forcemd)
                        self.tab._ccd_stream._onNewData(self.tab._ccd_stream._dataflow, data)
                        raw_data = self.tab._ccd_stream.raw
                        self._enqueue_save(filepath, raw_data)

                        visited_l.append(l)
                        visited_s.append(s)
                        visited_z.append(z)
                        idx += 1

                        wx.CallAfter(self._update_plot, visited_l, visited_s, visited_z)
                        wx.CallAfter(self.progress.SetValue, idx)
                        wx.CallAfter(self.status_lbl.SetLabel, f"Status: Running... ({idx}/{self.n_images} images acquired)")
        except CancelledError:
            wx.CallAfter(self.status_lbl.SetLabel, "Status: Cancelled by user")
        except Exception as e:
            wx.CallAfter(self.status_lbl.SetLabel, f"Error: {e}")
            logging.exception("Mirror alignment cube acquisition failed.")
        else:
            wx.CallAfter(self.status_lbl.SetLabel, "Status: Done")
        finally:
            self._cancel_requested = False
            self._running = False
            # Finally pause the ccd stream and blank the e-beam
            # For an unsupervised acquisition this state is considered safe
            self.tab._ccd_stream.is_active.value = False
            self.tab._ccd_stream.should_update.value = False
            self.ebeam.blanker.value = True
            self._restore_position()
            wx.CallAfter(self.start_btn.Enable)
            wx.CallAfter(self.pause_btn.Disable)
            wx.CallAfter(self.resume_btn.Disable)
            wx.CallAfter(self.cancel_btn.Disable)

    def _on_pause(self, _):
        """Event handler for the 'Pause' button."""
        self._pause_requested = True
        self.pause_btn.Disable()
        self.resume_btn.Enable()
        self.status_lbl.SetLabel("Status: Pausing...")

    def _on_resume(self, _):
        """Event handler for the 'Resume' button."""
        self._pause_requested = False
        self._resume_requested.set()
        self.resume_btn.Disable()
        self.pause_btn.Enable()
        self.status_lbl.SetLabel("Status: Resuming...")

    def _on_close(self, evt):
        """
        Event handler for the dialog close event.

        Prevents closing the dialog if an acquisition is still running.
        Otherwise, allows the dialog to close normally.
        """
        if self._running:
            wx.MessageBox("Acquisition is still _running.", "Warning")
            return
        evt.Skip()
