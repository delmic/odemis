# -*- coding: utf-8 -*-
"""
Created on 22 Aug 2012

@author: Éric Piel, Rinze de Laat, Philip Winkler

Copyright © 2012-2022 Éric Piel, Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.


### Purpose ###

This module contains classes to control the actions related to the acquisition
of microscope images.

"""

import logging
import math
from concurrent.futures._base import CancelledError

import wx

from odemis import model
from odemis.acq.align import z_localization
from odemis.acq.stream import FluoStream
from odemis.gui import conf
from odemis.gui.comp import popup
from odemis.gui.util import call_in_wx_main
from odemis.gui.util.widgets import ProgressiveFutureConnector, VigilantAttributeConnector
from odemis.util import units
from odemis.util.filename import create_filename


class CryoZLocalizationController(object):
    """
    Controller to handle the Z localization for the ENZEL/METEOR with a stigmator.
    """

    def __init__(self, tab_data, panel, tab):
        self._panel = panel
        self._tab_data = tab_data
        self._tab = tab
        self._stigmator = tab_data.main.stigmator

        # Note: there could be some (odd) configurations with a stigmator, but
        # no stigmator calibration (yet). In that case, we should still move the
        # stigmator to 0. Hence, it's before anything else.
        if self._stigmator:
            # Automatically move it to 0 at init, and then after every Z localization
            # (even if no calibration data)
            self._stigmator.moveAbs({"rz": 0})

        # If the hardware doesn't support for Z localization, hide everything and don't control anything
        if not hasattr(tab_data, "stigmatorAngle"):
            self._panel.btn_z_localization.Hide()
            self._panel.lbl_z_localization.Hide()
            self._panel.lbl_stigmator_angle.Hide()
            self._panel.cmb_stigmator_angle.Hide()
            self._panel.menu_localization_streams.Hide()
            self._panel.Layout()
            return

        # Connect the button and combobox
        self._panel.btn_z_localization.Bind(wx.EVT_BUTTON, self._on_z_localization)
        self._localization_btn_label = self._panel.btn_z_localization.GetLabel()

        # Connect menu for stream selection for Localization z
        self._acq_future = model.InstantaneousFuture()
        self._menu_to_stream = {}
        self._selected_stream = None
        self._panel.menu_localization_streams.Bind(wx.EVT_BUTTON, self._create_stream_menu)
        self._tab_data.streams.subscribe(self._on_streams, init=True)

        # Fill the combobox with the available stigmator angles
        for angle in sorted(tab_data.stigmatorAngle.choices):
            angle_str = units.to_string_pretty(math.degrees(angle), 3, "°")
            self._panel.cmb_stigmator_angle.Append(angle_str, angle)

        self._cmb_vac = VigilantAttributeConnector(
            va=self._tab_data.stigmatorAngle,
            value_ctrl=self._panel.cmb_stigmator_angle,
            events=wx.EVT_COMBOBOX,
            va_2_ctrl=self._cmb_stig_angle_set,
            ctrl_2_va=self._cmb_stig_angle_get
        )
        self._acq_future_connector = None  # ProgressiveFutureConnector, if running

        # TODO: listen to the current stream, to update the time estimation

        # To check that a feature is selected
        tab_data.main.currentFeature.subscribe(self._check_button_available, init=True)

        # To disable the button during acquisition
        tab_data.main.is_acquiring.subscribe(self._check_button_available)

    def _cmb_stig_angle_get(self):
        """
        Change the current angle based on the dropdown selection
        """
        i = self._panel.cmb_stigmator_angle.GetSelection()
        if i == wx.NOT_FOUND:
            logging.warning("cmb_stigmator_angle has unknown value.")
            return
        angle = self._panel.cmb_stigmator_angle.GetClientData(i)
        return angle

    def _cmb_stig_angle_set(self, value):
        ctrl = self._panel.cmb_stigmator_angle
        for i in range(ctrl.GetCount()):
            d = ctrl.GetClientData(i)
            if d == value:
                logging.debug("Setting combobox value to %s", ctrl.Items[i])
                ctrl.SetSelection(i)
                break
        else:
            logging.warning("Combobox stigmator angle has no value %s", value)

    @call_in_wx_main
    def _check_button_available(self, _):
        # Only possible to run the function iff:
        # * A feature is selected
        # * Not acquiring
        # * Localization process is running
        # * TODO: there is a FluoStream
        has_feature = self._tab_data.main.currentFeature.value is not None
        is_acquiring = self._tab_data.main.is_acquiring.value
        # While running the localization method
        # button turns in cancel button
        is_runnning = not self._acq_future.done()

        self._panel.btn_z_localization.Enable(has_feature and not is_acquiring or is_runnning)

    def _on_streams(self, streams):
        if self._selected_stream in streams:
            return  # Everything is fine
        # Find a good stream (or None if no stream)
        self._selected_stream = next((s for s in streams if isinstance(s, FluoStream)), None)

    def _create_stream_menu(self, evt):
        """Display active list of streams in the menu and check the selected stream when toggle button is clicked"""
        menu = wx.Menu()
        # Get the list of streams from stream controller to keep the display order of streams in menu,
        # same as, display order of streams in the "Streams" panel
        streams = [stream_cont.stream for stream_cont in self._tab.streambar_controller.stream_controllers if
                   isinstance(stream_cont.stream, FluoStream)]
        self._menu_to_stream = {}
        for stream in streams:
            label = stream.name.value
            menu_id = wx.Window.NewControlId()
            menu_item = wx.MenuItem(menu, menu_id, label, kind=wx.ITEM_RADIO)
            menu.Bind(wx.EVT_MENU, self._on_stream_selection, id=menu_id)
            self._menu_to_stream[menu_id] = stream
            menu.Append(menu_item)
            menu_item.Check(stream == self._selected_stream)

        # Blocking function, which returns only once when
        # The user has selected a stream, or closed the menu
        self._panel.menu_localization_streams.PopupMenu(menu,
                                                        (
                                                        0, self._panel.menu_localization_streams.GetSize().GetHeight()))
        self._panel.menu_localization_streams.SetToggle(False)

    def _on_stream_selection(self, evt):
        """Get and save the stream option when an aption is selected in the pop-up menu"""
        menu_id = evt.GetId()
        self._selected_stream = self._menu_to_stream[menu_id]

    def _on_z_localization(self, evt):
        """Start or cancel the localization method when the button is clicked"""
        # If localization is running, cancel it, otherwise start one
        if self._acq_future.done():
            self._start_z_localization()
        else:
            self._acq_future.cancel()

    def _start_z_localization(self):
        """
        Called on button press, to start the localization
        """
        s = self._selected_stream
        if s is None:
            raise ValueError("No FM stream available to acquire a image of the the feature")

        # The button is disabled when no feature is selected, but better check
        feature = self._tab_data.main.currentFeature.value
        if feature is None:
            raise ValueError("Select a feature first to specify the Z localization in X/Y")
        pos = self._tab_data.main.posture_manager.to_sample_stage_from_stage_position(feature.stage_position.value)

        # Disable the GUI and show the progress bar
        self._tab.streambar_controller.pauseStreams()
        self._tab.streambar_controller.pause()

        self._panel.lbl_z_localization.Hide()
        self._panel.gauge_z_localization.Show()
        self._tab_data.main.is_acquiring.value = True
        self._panel.Layout()

        # Store the acquisition somewhere, for debugging purposes
        acq_conf = conf.get_acqui_conf()
        fn = create_filename(acq_conf.pj_last_path, "{datelng}-{timelng}-superz", ".ome.tiff")
        assert fn.endswith(".ome.tiff")

        # The angles of stigmatorAngle should come from MD_CALIB, so it's relatively safe
        angle = self._tab_data.stigmatorAngle.value

        self._acq_future = z_localization.measure_z(self._stigmator, angle, (pos["x"], pos["y"]), s, logpath=fn)
        self._panel.btn_z_localization.SetLabel("Cancel")

        self._acq_future_connector = ProgressiveFutureConnector(self._acq_future,
                                                                self._panel.gauge_z_localization)

        self._acq_future.add_done_callback(self._on_measure_z_done)

    @call_in_wx_main
    def _on_measure_z_done(self, f):
        """
        Called when measure_z() is completed (can also happen if cancelled or failed)
        """
        try:
            self._panel.btn_z_localization.Enable(True)
            self._panel.btn_z_localization.SetLabel(self._localization_btn_label)

            zshift, warning = f.result()

            # focus position: the base for the shift computed by the z localization
            zpos_acq = self._tab_data.main.focus.position.value["z"]

            logging.debug("Feature located at %s + %s m", zpos_acq, zshift)
            zpos = zpos_acq + zshift

            # Sanity check: typically, the Z localization is for localization within a few µm.
            if abs(zshift) > 100e-6:
                warning = 7

            # Update the feature Z pos, and move there
            feature = self._tab_data.main.currentFeature.value
            feature.fm_focus_position.value = {"z": zpos}
            if warning:
                # Update the Z pos, but do not move there.
                logging.warning("Z pos shift detected of %s, but not going there as it had warning %s", zshift, warning)
                popup.show_message(self._tab.main_frame, "Z localization unreliable",
                                   "The Z localization could not locate the depth with sufficient certainty.",
                                   level=logging.WARNING)
            else:
                f = self._tab_data.main.focus.moveAbs({"z": zpos})
                # Don't wait for it to be complete, the user will notice anyway
        except CancelledError:
            logging.debug("Z localization cancelled")
        finally:
            self._panel.btn_z_localization.Enable()
            self._panel.gauge_z_localization.Hide()
            self._panel.lbl_z_localization.Show()
            self._tab_data.main.is_acquiring.value = False
            self._tab.streambar_controller.resume()
            self._panel.Layout()
