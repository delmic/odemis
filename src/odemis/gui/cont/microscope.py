# -*- coding: utf-8 -*-
"""
@author: Rinze de Laat

Copyright © 2012-2013 Rinze de Laat, Éric Piel, Delmic

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
from __future__ import division

from abc import ABCMeta
from concurrent.futures._base import CancelledError, CANCELLED, FINISHED
import logging
import math
import numpy
from odemis import model
from odemis.acq import _futures
from odemis.acq import align, stream
from odemis.acq._futures import executeTask
from odemis.gui.conf import get_calib_conf
from odemis.gui.model import STATE_ON, CHAMBER_PUMPING, CHAMBER_VENTING, \
    CHAMBER_VACUUM, CHAMBER_VENTED, CHAMBER_UNKNOWN, STATE_OFF
from odemis.gui.util import call_in_wx_main
from odemis.gui.util.widgets import ProgressiveFutureConnector
from odemis.gui.util.widgets import VigilantAttributeConnector
from odemis.gui.win.delphi import CalibrationProgressDialog
from odemis.model import getVAs, VigilantAttributeBase, InstantaneousFuture
import threading
import time
import wx

from odemis.gui import img
import odemis.gui.win.delphi as windelphi
import odemis.util.units as units


# Sample holder types in the Delphi, as defined by Phenom World
PHENOM_SH_TYPE_UNKNOWN = -1  # Reported when not yet registered
PHENOM_SH_TYPE_STANDARD = 1  # standard sample holder
PHENOM_SH_TYPE_OPTICAL = 200  # sample holder for the Delphi, containing a lens

DELPHI_OVERVIEW_POS = {"x": 0, "y": 0}  # good position of the stage for overview
DELPHI_OVERVIEW_FOCUS = {"z": 0.006}  # Good focus position for overview image on the Delphi sample holder
GOOD_FOCUS_OFFSET = 200e-06  # Offset from hole focus


class MicroscopeStateController(object):
    """
    This controller controls the main microscope buttons (ON/OFF,
    Pause, vacuum...) and updates the model. To query/change the status of a
    specific component, use the main data model directly.
    """
    __metaclass__ = ABCMeta
    btn_to_va = {}

    def __init__(self, tab_data, tab_panel, btn_prefix):
        """ Binds the 'hardware' buttons to their appropriate
        Vigilant Attributes in the model.MainGUIData

        tab_data (MicroscopyGUIData): the data model of the tab
        tab_panel: (wx.Panel): the microscope tab
        btn_prefix (string): common prefix of the names of the buttons
        """
        self._tab_data = tab_data
        self._tab_panel = tab_panel

        # Look for which buttons actually exist, and which VAs exist. Bind the fitting ones
        self._btn_controllers = {}

        for btn_name, (va_name, control_class) in self.btn_to_va.items():
            btn = getattr(tab_panel, btn_prefix + btn_name, None)
            if not btn:
                continue

            # First try on the tab model, then on the main model
            va = getattr(tab_data, va_name, None)
            if not va:
                va = getattr(tab_data.main, va_name, None)
                if not va:
                    btn.Hide()
                    continue

            logging.debug("Connecting button %s to %s", btn_name, va_name)
            btn_cont = control_class(btn, va, tab_data.main)
            self._btn_controllers[btn_name] = btn_cont

        if not self._btn_controllers:
            logging.warning("No microscope button found in tab %s", btn_prefix)


class HardwareButtonController(object):
    """
    Default button controller that on handles ON and OFF states
    """

    def __init__(self, btn_ctrl, va, _):
        self.btn = btn_ctrl
        self.vac = VigilantAttributeConnector(va, btn_ctrl, self._va_to_btn, self._btn_to_va,
                                              events=wx.EVT_BUTTON)

    def _va_to_btn(self, state):
        """ Change the button toggle state according to the given hardware state """
        self.btn.SetToggle(state != STATE_OFF)

    def _btn_to_va(self):
        """ Return the hardware state associated with the current button toggle state """
        return STATE_ON if self.btn.GetToggle() else STATE_OFF


class ChamberButtonController(HardwareButtonController):
    """ Controller that allows for the more complex state updates required by the chamber button """

    def __init__(self, btn_ctrl, va, main_data):
        """

        :param btn_ctrl: (ImageTextToggleButton) Button that controls and displays the chamber state
        :param va: (VigillantAttribute) The chamber state
        :param main_data: (MainGUIData) GUI microscope model

        """

        super(ChamberButtonController, self).__init__(btn_ctrl, va, main_data)
        self.main_data = main_data

        # If there is pressure information, assume it is a complete SEM chamber,
        # otherwise assume it uses a sample loader like the Phenom or Delphi.
        if 'pressure' in getVAs(main_data.chamber):
            main_data.chamber.pressure.subscribe(self._on_pressure_change, init=True)

            self._btn_icons = {
                'normal': img.getBitmap("icon/ico_press.png"),
                'working': img.getBitmap("icon/ico_press_orange.png"),
                'vacuum': img.getBitmap("icon/ico_press_green.png"),
            }

            self._tooltips = {
                CHAMBER_PUMPING: "Pumping...",
                CHAMBER_VENTING: "Venting...",
                CHAMBER_VENTED: "Pump the chamber",
                CHAMBER_VACUUM: "Vent the chamber",
            }
        else:
            self.btn.SetLabel("LOAD")

            self._btn_icons = {
                'normal': img.getBitmap("icon/ico_eject.png"),
                'working': img.getBitmap("icon/ico_eject_orange.png"),
                'vacuum': img.getBitmap("icon/ico_eject_green.png"),
            }
            self._tooltips = {
                CHAMBER_PUMPING: "Loading...",
                CHAMBER_VENTING: "Ejecting...",
                CHAMBER_VENTED: "Load the sample",
                CHAMBER_VACUUM: "Eject the sample",
            }

    def _va_to_btn(self, state):
        """ Change the button toggle state according to the given hardware state """
        # When the chamber is pumping or venting, it's considered to be working
        if state in {CHAMBER_PUMPING, CHAMBER_VENTING}:
            self.btn.SetIcon(self._btn_icons['working'])
        elif state == CHAMBER_VACUUM:
            self.btn.SetIcon(self._btn_icons['vacuum'])
            self.btn.SetLabel("UNLOAD")
            # In case the GUI is launched with the chamber pump turned on already, we need to
            # toggle the button by code.
            self.btn.SetToggle(True)
        elif state in {CHAMBER_VENTED, CHAMBER_UNKNOWN}:
            self.btn.SetIcon(self._btn_icons['normal'])
            self.btn.SetToggle(False)
            self.btn.SetLabel("LOAD")  # Extra spaces are needed for alignment
        else:
            logging.error("Unknown chamber state %d", state)

        # Set the tooltip
        if state in self._tooltips:
            self.btn.SetToolTipString(self._tooltips[state])

        self.btn.Refresh()

    def _btn_to_va(self):
        """ Return the hardware state associated with the current button toggle state

        When the button is pressed down (i.e. toggled), the chamber is expected to be pumping to
        create a vacuum. When the button is up (i.e. un-toggled), the chamber is expected to be
        venting.

        """

        logging.debug("Requesting change of chamber pressure")

        if self.btn.GetToggle():
            return CHAMBER_PUMPING
        else:
            return CHAMBER_VENTING

    @call_in_wx_main
    def _on_pressure_change(self, pressure_val):
        """ Set a formatted pressure value as the label of the button """

        str_value = units.readable_str(pressure_val, sig=2,
                                       unit=self.main_data.chamber.pressure.unit)
        if self.btn.Label != str_value:
            self.btn.Label = str_value
            self.btn.Refresh()


class SecomStateController(MicroscopeStateController):
    """
    This controller controls the main microscope buttons (ON/OFF,
    Pause, vacuum...) and updates the model.
    """
    # GUI toggle button (suffix) name -> VA name
    btn_to_va = {
        "sem": ("emState", HardwareButtonController),
        "opt": ("opticalState", HardwareButtonController),
        "press": ("chamberState", ChamberButtonController)
    }
    # The classes of streams that are affected by the chamber
    # only SEM, as optical might be used even vented
    cls_streams_involved = stream.EMStream

    def __init__(self, tab_data, tab_panel, btn_prefix, st_ctrl):
        super(SecomStateController, self).__init__(tab_data, tab_panel, btn_prefix)

        self._main_data = tab_data.main
        self._stream_controller = st_ctrl

        # Event for indicating sample reached overview position and phenom GUI
        # loading
        self._in_overview = threading.Event()
        self._phenom_load_done = True

        # Just to be able to disable the buttons when the chamber is vented
        self._sem_btn = getattr(tab_panel, btn_prefix + "sem")
        self._opt_btn = getattr(tab_panel, btn_prefix + "opt")

        # To be able to disable the button when loading (door is open),
        # or cancellation (venting or when moving from overview to SEM)
        # is impossible.
        self._press_btn = getattr(tab_panel, btn_prefix + "press")

        # To update the stream status
        self._status_list = []
        self._status_prev_streams = []
        tab_data.streams.subscribe(self._subscribe_current_stream_status)

        # To listen to change in play/pause
        self._active_prev_stream = None

        # Turn off the light, but set the power to a nice default value
        # TODO: do the same with the brightlight and backlight
        light = self._main_data.light
        if light is not None:
            # Turn off emissions
            try:
                emissions = [0.] * len(light.emissions.value)
                light.emissions.value = emissions
            except AttributeError:
                # No emission ? => turn off the power as only way to stop light
                light.power.value = 0

        # E-beam management
        # The SEM driver should do the right thing in most cases (ie, turn on
        # the beam and disable the blanker whenever scan is needed). However,
        # if not the whole SEM can be controlled from Odemis, the user still
        # uses the SEM GUI in parallel. That means:
        # * During optical acquisition, if the blanker cannot be controlled,
        #   we need to force the SEM control, to ensure the ebeam stays parked.
        # * When not doing any acquisition (ie, no stream playing and not in
        #   acquisition mode), if the blanker can be controlled, we need to
        #   unforce the blanker to allow the user to use the SEM GUI.
        ebeam = self._main_data.ebeam
        if (ebeam and model.hasVA(ebeam, "external") and
            not model.hasVA(ebeam, "blanker") and
            True in ebeam.external.choices
           ):
            # We need to force external to True (to force ebeam parking)
            # whenever a stream is playing or we are in acquisition mode
            # (otherwise, it'll set to False)
            tab_data.streams.subscribe(self._subscribe_current_stream_active)
            self._main_data.is_acquiring.subscribe(self._check_ebeam_external, init=True)

        # TODO: handle case of blanker present, but SEM control is limited
        # (ie, no hfw, or no focus or no voltage)

        # Manage the chamber
        if self._main_data.chamber:
            pressures = self._main_data.chamber.axes["pressure"].choices
            self._vacuum_pressure = min(pressures.keys())
            self._vented_pressure = max(pressures.keys())

            self._chamber_pump_future = InstantaneousFuture()  # used when pumping
            self._chamber_vent_future = InstantaneousFuture()  # used when pumping

            # if there is an overview camera, _and_ it has to be reached via a
            # special "pressure" state => note it down
            if self._main_data.overview_ccd:
                for p, pn in pressures.items():
                    if pn == "overview":
                        self._overview_pressure = p
                        break
                else:
                    self._overview_pressure = None

            self._main_data.chamberState.subscribe(self.on_chamber_state)
            ch_pos = self._main_data.chamber.position
            ch_pos.subscribe(self.on_chamber_pressure, init=True)
            ch_opened = self._main_data.chamber.opened
            ch_opened.subscribe(self.on_door_opened, init=False)

            # at init, if chamber is in overview position, start by pumping
            # (which will indirectly first acquire an image)
            if ch_pos.value["pressure"] == self._overview_pressure:
                self._main_data.chamberState.value = CHAMBER_PUMPING

        # disable optical and SEM buttons while there is a preparation process running
        self._main_data.is_preparing.subscribe(self.on_preparation)

    def on_preparation(self, is_preparing):
        # Make sure cannot switch stream during preparation
        self._sem_btn.Enable(not is_preparing)
        self._opt_btn.Enable(not is_preparing)

    def _subscribe_current_stream_active(self, streams):
        """ Find the active stream and subscribe to its is_active VA

        streams is sorted by Least Recently Used, so the first element is the newest and a possible
        2nd one, was the previous newest.

        """
        if self._active_prev_stream is not None:
            self._active_prev_stream.status.unsubscribe(self._check_ebeam_external)

        if streams:
            s = streams[0]
            s.is_active.subscribe(self._check_ebeam_external, init=True)
        else:
            s = None
        self._active_prev_stream = s

    def _check_ebeam_external(self, _):
        """
        Called whenever the acquisition state might have changed
        """
        # Consider that an acquisition is happening if either is_acquiring or
        # a stream (of the tab) is active
        streams = self._tab_data.streams.value
        is_active = (streams and streams[0].is_active.value)

        if self._main_data.is_acquiring.value or is_active:
            logging.debug("Acquisition active, forcing SEM external mode")
            self._main_data.ebeam.external.value = True
        else:
            # Note: it could be that it's just because we are in another tab
            # but that's fine as only one state controller exists (from the
            # acquisition tab)
            logging.debug("No acquisition, setting SEM external mode to auto")
            self._main_data.ebeam.external.value = None

    def _subscribe_current_stream_status(self, streams):
        """ Find the active stream + all the aligned streams and subscribe to
        their status VAs in order to decide what status message needs to be
        displayed.

        streams is sorted by Least Recently Used, so the first element is the newest and a possible
        2nd one, was the previous newest.

        """
        # First unsubscribe from the previous streams
        if len(self._status_prev_streams) != 0:
            for s in self._status_prev_streams:
                s.status.unsubscribe(self._on_active_stream_status)
                if model.hasVA(s, "calibrated"):
                    s.calibrated.unsubscribe(self._on_stream_calibrated)

        # Add the active one
        self._status_list = [streams[0]] if not model.hasVA(streams[0], "calibrated") else []

        # Now the aligned streams
        for stream in streams:
            if model.hasVA(stream, "calibrated"):
                self._status_list.append(stream)

        for s in self._status_list:
            s.status.subscribe(self._on_active_stream_status, init=True)
            if model.hasVA(s, "calibrated"):
                s.calibrated.subscribe(self._on_stream_calibrated, init=True)

        self._status_prev_streams = self._status_list

    @call_in_wx_main
    def _on_active_stream_status(self, (lvl, msg)):
        """ Display the given message, or clear it
        lvl, msg (int, str): same as Stream.status. Cleared when lvl is None.
        """
        action = None
        # If there are any aligned streams, give priority to showing their status
        for s in self._status_list:
            if s.is_active.value and model.hasVA(s, "calibrated") and (not s.calibrated.value):
                lvl, msg = s.status.value
                if (lvl is not None) and (not isinstance(msg, basestring)):
                    # it seems it also contains an action
                    msg, action = msg
                break
        else:
            for s in self._status_list:
                if model.hasVA(s, "calibrated") and (not s.calibrated.value):
                    lvl, msg = s.status.value
                    if (lvl is not None) and (not isinstance(msg, basestring)):
                        # it seems it also contains an action
                        msg, action = msg
                    break
        if action is None:
            action = ""
        # Might still be none
        if lvl is None:
            msg = ""
        self._show_status_icons(lvl, action)
        self._tab_panel.lbl_stream_status.SetLabel(msg)
        self._tab_panel.lbl_stream_status.SetToolTipString(action)

        # Whether the status is actually displayed or the progress bar is shown
        # is only dependent on _show_progress_indicators()

    @call_in_wx_main
    def _on_stream_calibrated(self, calibrated):
        # hide all the misaligned streams, unhide all calibrated streams
        for s in self._tab_data.streams.value:
            if model.hasVA(s, "calibrated"):
                if not s.calibrated.value:
                    for v in self._tab_data.views.value:
                        if (v.name.value == "SEM") and isinstance(s, stream.SEMStream):
                            continue
                        else:
                            v.removeStream(s)
                else:
                    for v in self._tab_data.views.value:
                        if (v.name.value == "Overview"):
                            continue
                        elif (v.name.value == "Optical") and isinstance(s, stream.SEMStream):
                            continue
                        elif (v.name.value == "SEM") and isinstance(s, stream.FluoStream):
                            continue
                        else:
                            v.addStream(s)

    def _show_status_icons(self, lvl, action=None):
        self._tab_panel.bmp_stream_status_info.Show(lvl in (logging.INFO, logging.DEBUG))
        self._tab_panel.bmp_stream_status_warn.Show(lvl == logging.WARN)
        self._tab_panel.bmp_stream_status_error.Show(lvl == logging.ERROR)
        self._tab_panel.pnl_hw_info.Layout()
        if action is not None:
            self._tab_panel.pnl_hw_info.SetToolTipString(action)

    def _show_progress_indicators(self, show_load, show_status):
        """
        Show or hide the loading progress indicators for the chamber and sample holder

        The stream status text will be hidden if the progress indicators are shown.
        """
        assert not (show_load and show_status), "Cannot display both simultaneously"
        self._tab_panel.pnl_load_status.Show(show_load)
        self._tab_panel.pnl_stream_status.Show(show_status)
        self._tab_panel.pnl_hw_info.Layout()

    def _set_ebeam_power(self, on):
        """ Set the ebeam power (if there is an ebeam that can be controlled)
        on (boolean): if True, we set the power on, other will turn it off
        """
        if self._main_data.ebeam is None:
            return

        power = self._main_data.ebeam.power
        if not isinstance(power, model.VigilantAttributeBase):
            # We cannot change the power => nothing else to try
            logging.debug("Ebeam doesn't support setting power")
            return

        if on:
            try:
                power.value = power.range[1]  # max!
            except (AttributeError, model.NotApplicableError):
                try:
                    # if enumerated: the second lowest
                    power.value = sorted(power.choices)[1]
                except (AttributeError, model.NotApplicableError):
                    logging.error("Unknown ebeam power range, setting to 1")
                    power.value = 1
        else:
            power.value = 0

    def _reset_streams(self):
        """
        Empty the data of the streams which might have no more meaning after
          loading a new sample.
        """
        for s in self._tab_data.streams.value:
            if not isinstance(s, self.cls_streams_involved):
                continue
            # Don't reset if the user is still/already playing it (eg: optical stream)
            if s.should_update.value:
                continue
            # TODO: better way to reset streams? => create new ones and copy just what we care about?
            if s.raw:
                s.raw = []
                s.image.value = None
                s.histogram._value = numpy.empty(0)
                s.histogram.notify(s.histogram._value)

    @call_in_wx_main
    def on_chamber_state(self, state):
        """ Set the desired pressure on the chamber when the chamber's state changes

        Only 'active' states (i.e. either CHAMBER_PUMPING or CHAMBER_VENTING)
        will start a change in pressure.
        """
        logging.debug("Chamber state changed to %d", state)
        # In any case, make sure the streams cannot be started
        if state == CHAMBER_VACUUM:
            # disabling the acquire button is done in the acquisition controller
            self._sem_btn.Enable(True)
            self._sem_btn.SetToolTip(None)
            self._opt_btn.Enable(True)
            self._opt_btn.SetToolTip(None)
            # TODO: enable overview move
            self._stream_controller.enableStreams(True)

            self._set_ebeam_power(True)
            # if no special place for overview, then we do it once after
            # the chamber is ready.
            if self._main_data.overview_ccd and self._overview_pressure is None:
                # TODO: maybe could be done before full vacuum, but we need a
                # way to know from the hardware.
                self._start_overview_acquisition()
        else:
            # TODO: disable overview move

            # Disable button and stop streams for the types affected by the chamber
            if (
                hasattr(self._tab_data, "emState") and
                issubclass(stream.SEMStream, self.cls_streams_involved)
            ):
                self._tab_data.emState.value = STATE_OFF
                self._sem_btn.Enable(False)
                self._sem_btn.SetToolTipString("Chamber must be under vacuum to activate the SEM")

            if (
                hasattr(self._tab_data, "opticalState") and
                issubclass(stream.CameraStream, self.cls_streams_involved)
            ):
                self._tab_data.opticalState.value = STATE_OFF
                self._opt_btn.Enable(False)
                self._opt_btn.SetToolTipString("Chamber must be under vacuum to activate "
                                               "the optical view")

            self._stream_controller.enableStreams(False, self.cls_streams_involved)

        # TODO: handle the "cancellation" (= the user click on the button while
        # it was in a *ING state = the latest move is not yet done or overview
        # acquisition is happening)

        # TODO: add warning/info message if the chamber move fails

        # Actually start the pumping/venting
        if state == CHAMBER_PUMPING:
            # in case the chamber was venting just ignore
            if self._chamber_vent_future.running() is False:
                self._chamber_pump_future.cancel()
                self._main_data.chamber.stop()
                self._start_chamber_pumping()
        elif state == CHAMBER_VENTING:
            self._chamber_pump_future.cancel()
            self._main_data.chamber.stop()
            self._start_chamber_venting()

    def _start_chamber_pumping(self):
        if self._overview_pressure is not None:
            # _on_overview_position() will take care of going further
            f = self._main_data.chamber.moveAbs({"pressure": self._overview_pressure})
            f.add_done_callback(self._on_overview_position)
        else:
            f = self._main_data.chamber.moveAbs({"pressure": self._vacuum_pressure})
            f.add_done_callback(self._on_vacuum)

        # TODO: if the future is a progressiveFuture, it will provide info
        # on when it will finish => display that in the progress bar. cf Delphi

        # reset the streams to avoid having data from the previous sample
        self._reset_streams()

        # Empty the stage history, as the interesting locations on the previous
        # sample have probably nothing in common with this new sample
        self._tab_data.stage_history.value = []

    def _on_vacuum(self, future):
        pass

    def _start_chamber_venting(self):
        # Pause all streams (SEM streams are most important, but it's
        # simpler for the user to stop all of them)
        for s in self._tab_data.streams.value:
            s.is_active.value = False
            s.should_update.value = False

        self._set_ebeam_power(False)
        self._chamber_vent_future = self._main_data.chamber.moveAbs({"pressure": self._vented_pressure})
        # Will actually be displayed only if the hw_info is shown
        self._press_btn.Enable(False)
        self._chamber_fc = ProgressiveFutureConnector(
            self._chamber_vent_future,
            self._tab_panel.gauge_load_time,
            self._tab_panel.lbl_load_time,
            full=False
        )
        self._chamber_vent_future.add_done_callback(self._on_vented)

    def _on_vented(self, future):
        self.on_chamber_pressure(self._main_data.chamber.position.value)
        wx.CallAfter(self._press_btn.Enable, True)

    # TODO: have multiple versions of this method depending on the type of chamber?
    # TODO: have a special states for CHAMBER_OVERVIEW_PRE_VACUUM and CHAMBER_OVERVIEW_POST_VACUUM?
    def on_chamber_pressure(self, position):
        """ Determine the state of the chamber when the pressure changes, and
        do the overview imaging if possible.

        This method can change the state from CHAMBER_PUMPING to CHAMBER_VACUUM
        or from CHAMBER_VENTING to CHAMBER_VENTED.
        """
        # Note, this can be called even if the pressure value hasn't changed.
        currentp = position["pressure"]
        pressures = self._main_data.chamber.axes["pressure"].choices
        logging.debug("Chamber reached pressure %s (%g Pa)",
                      pressures.get(currentp, "unknown"), currentp)

        if currentp <= self._vacuum_pressure:
            # Vacuum reached
            self._main_data.chamberState.value = CHAMBER_VACUUM
        elif currentp >= self._vented_pressure:
            # Chamber is opened
            self._main_data.chamberState.value = CHAMBER_VENTED
        elif currentp == self._overview_pressure:
            # It's all fine, it should automatically reach vacuum eventually
            # The advantage of not putting the call to _start_overview_acquisition()
            # here is that if the previous pressure was identical, we are not
            # doing it twice.
            pass
        else:
            # This can happen at initialisation if the chamber pressure is changing
            logging.info("Pressure position unknown: %s", currentp)
            # self._main_data.chamberState.value = CHAMBER_UNKNOWN

    @call_in_wx_main
    def on_door_opened(self, value):
        """
        Disable the load button when the chamber door is open.
        """
        if value:
            self._press_btn.Enable(False)
        else:
            self._press_btn.Enable(True)
            self._press_btn.SetToggle(True)
            self._phenom_load_done = False
            self._main_data.chamberState.value = CHAMBER_PUMPING

    def _on_overview_position(self, unused):
        logging.debug("Overview position reached")
        self._start_overview_acquisition()

    def _start_overview_acquisition(self):
        logging.debug("Starting overview acquisition")

        # Start autofocus, and the rest will be done asynchronously
        if self._main_data.overview_focus:
            # TODO: center the sample to the view
            # We are using the best accuracy possible: 0
            try:
                f = align.autofocus.AutoFocus(self._main_data.overview_ccd, None,
                                              self._main_data.overview_focus)
            except Exception:
                logging.exception("Failed to start auto-focus")
                self._on_overview_focused(None)
            else:
                f.add_done_callback(self._on_overview_focused)
        else:
            self._on_overview_focused(None)

    def _get_overview_stream(self):
        """
        return (Stream): the overview stream
        raise LookupError: if there is not such a stream
        """
        # For now, it's very ad-hoc: it's the only stream of the last view
        if len(self._tab_data.views.value) < 5:
            raise LookupError("Views don't contain overview stream")
        ovv = self._tab_data.views.value[-1]
        return list(ovv.getStreams())[0]

    def _on_overview_focused(self, future):
        """
        Called when the overview image is focused
        """
        # We cannot do much if the focus failed, so always go on...
        if future:
            try:
                future.result()
                logging.debug("Overview focused")
            except Exception:
                logging.info("Auto-focus on overview failed")
        else:
            logging.debug("Acquiring overview image after skipping auto-focus")

        # now acquire one image
        try:
            ovs = self._get_overview_stream()

            ovs.image.subscribe(self._on_overview_image)
            # start acquisition
            ovs.should_update.value = True
            ovs.is_active.value = True
        except Exception:
            logging.exception("Failed to start overview image acquisition")
            f = self._main_data.chamber.moveAbs({"pressure": self._vacuum_pressure})
            f.add_done_callback(self._on_vacuum)

        # TODO: have a timer to detect if no image ever comes, give up and move
        # to final pressure

    def _on_overview_image(self, image):
        """ Called once the overview image has been acquired """

        logging.debug("New overview image acquired")
        # Stop the stream (the image is immediately displayed in the view)
        try:
            ovs = self._get_overview_stream()
            ovs.is_active.value = False
            ovs.should_update.value = False
        except Exception:
            logging.exception("Failed to acquire overview image")

        if self._main_data.chamberState.value not in {CHAMBER_PUMPING, CHAMBER_VACUUM}:
            logging.warning("Receive an overview image while in state %s",
                            self._main_data.chamberState.value)
            return  # don't ask for vacuum

        # move further to fully under vacuum (should do nothing if already there)
        f = self._main_data.chamber.moveAbs({"pressure": self._vacuum_pressure})
        f.add_done_callback(self._on_vacuum)


class DelphiStateController(SecomStateController):
    """
    State controller with special features for the DEPHI (such as loading/running
    calibration when the sample holder is inserted).
    """

    cls_streams_involved = stream.Stream

    def __init__(self, *args, **kwargs):
        super(DelphiStateController, self).__init__(*args, **kwargs)
        self._main_frame = self._tab_panel.Parent
        self._calibconf = get_calib_conf()

        # Display the panel with the loading progress indicators
        self._tab_panel.pnl_hw_info.Show()

        # If starts with the sample fully loaded, check for the calibration now
        ch_pos = self._main_data.chamber.position
        self.good_focus = None
        if ch_pos.value["pressure"] == self._vacuum_pressure:
            # If it's loaded, the sample holder is registered for sure, and the
            # calibration should have already been done. Otherwise request
            # ejecting the sample holder
            try:
                self._load_holder_calib()
                if self._hole_focus is not None:
                    self.good_focus = self._hole_focus - GOOD_FOCUS_OFFSET
                    ff = self._main_data.ebeam_focus.moveAbs({"z": self.good_focus})
                    ff.result()
                ccd_md = self._main_data.ccd.getMetadata()
                good_hfw = (self._main_data.ccd.resolution.value[0] * ccd_md[model.MD_PIXEL_SIZE][0]) / 2
                self._main_data.ebeam.horizontalFoV.value = good_hfw
            except ValueError:
                dlg = wx.MessageDialog(self._main_frame,
                                       "Sample holder is loaded while there is no calibration information. "
                                       "We will now eject it.",
                                       "Missing calibration information",
                                       wx.OK | wx.ICON_WARNING)
                dlg.ShowModal()
                dlg.Destroy()
                self._main_data.chamberState.value = CHAMBER_VENTING
            self._show_progress_indicators(False, True)

        # Connect the Delphi recalibration to the menu item
        wx.EVT_MENU(
                self._main_frame,
                self._main_frame.menu_item_recalibrate.GetId(),
                self.request_holder_recalib
            )

        # Progress dialog for calibration
        self._dlg = None

    def _start_chamber_pumping(self):
        """
        Note: must be called in the main GUI thread
        """
        # Warning: if the sample holder is not yet registered, the Phenom will
        # not accept to even load it to the overview. That's why checking for
        # calibration/registration must be done immediately. Annoyingly, the
        # type ID of the sample holder is not reported until it's registered.

        if not self._check_holder_calib():
            return

        self._chamber_pump_future = self.DelphiLoading()
        self._chamber_fc = ProgressiveFutureConnector(
            self._chamber_pump_future,
            self._tab_panel.gauge_load_time,
            self._tab_panel.lbl_load_time,
            full=False
        )
        self._show_progress_indicators(True, False)

        # reset the streams to avoid having data from the previous sample
        self._reset_streams()

        # Empty the stage history, as the interesting locations on the previous
        # sample have probably nothing in common with this new sample
        self._tab_data.stage_history.value = []

        self._chamber_pump_future.add_done_callback(self._on_vacuum)

    @call_in_wx_main
    def _on_vacuum(self, future):
        """ Called when the vacuum is reached (ie, the future ended) """
        try:
            future.result()  # just to raise exception if failed
        except CancelledError:
            logging.info("Loading of the sample holder was cancelled")
        except Exception as exp:
            # something went wrong => just eject the sample holder
            logging.exception("Loading the sample holder failed")
            dlg = wx.MessageDialog(self._main_frame,
                                   "The loading of the sample holder failed.\n"
                                   "Error: %s\n\n"
                                   "If the problem persists, contact the support service.\n"
                                   "The sample holder will now be ejected." %
                                   (exp,),
                                   "Sample holder loading failed",
                                   wx.OK | wx.ICON_WARNING)
            dlg.ShowModal()
            dlg.Destroy()
            # Eject the sample holder
            self._main_data.chamberState.value = CHAMBER_VENTING
            return False

        super(DelphiStateController, self)._on_vacuum(future)
        self._show_progress_indicators(False, True)

    def _start_chamber_venting(self):
        """
        Note: must be called in the main GUI thread
        """
        # On the DELPHI, we also move the optical stage to 0,0 (= reference
        # position), so that referencing will be faster on next load.
        # We just need to be careful that the axis is referenced
        referenced = self._main_data.aligner.referenced.value
        pos = {"x": 0, "y": 0}
        for a in pos.keys():
            if not referenced.get(a, False):
                del pos[a]
        self._main_data.aligner.moveAbs(pos)

        super(DelphiStateController, self)._start_chamber_venting()
        self._show_progress_indicators(True, False)

    @call_in_wx_main
    def _on_vented(self, future):
        super(DelphiStateController, self)._on_vented(future)
        self._show_progress_indicators(False, False)

    def _load_holder_calib(self):
        """
        Load the sample holder calibration. This assumes that it is sure that
        the calibration data is present.
        """
        shid, sht = self._main_data.chamber.sampleHolder.value
        self._hole_focus = None

        if sht is None:
            logging.warn("No sample holder loaded!")
            return
        elif sht != PHENOM_SH_TYPE_OPTICAL:
            # Log the warning but load the calibration data
            logging.warn("Wrong sample holder type! We will try to load the "
                         "calibration data anyway...")

        calib = self._calibconf.get_sh_calib(shid)
        if calib is None:
            raise ValueError("Calibration data for sample holder is not present")

        # TODO: to be more precise on the stage rotation, we'll need to
        # locate the top and bottom holes of the sample holder, using
        # the SEM. So once the sample is fully loaded, new and more
        # precise calibration will be set.
        htop, hbot, hfoc, strans, sscale, srot, iscale, irot, iscale_xy, ishear, resa, resb, hfwa, spotshift = calib
        self._hole_focus = hfoc

        # update metadata to stage
        self._main_data.stage.updateMetadata({
            model.MD_POS_COR: strans,
            model.MD_PIXEL_SIZE_COR: sscale,
            model.MD_ROTATION_COR: srot
        })

        # also update the invert stage used in overview navigation
        self._main_data.overview_stage.updateMetadata({
            model.MD_POS_COR: (-strans[0], -strans[1]),
            model.MD_PIXEL_SIZE_COR: (1 / sscale[0], 1 / sscale[1]),
            model.MD_ROTATION_COR:-srot
        })

        # use image scaling as scaling correction metadata to ccd
        self._main_data.ccd.updateMetadata({
            model.MD_PIXEL_SIZE_COR: iscale,
        })

        # use image rotation as rotation of the SEM (note that this works fine
        # wrt to the stage referential because the rotation is very small)
        if self._main_data.ebeam.rotation.readonly:
            # normally only happens with the simulator
            self._main_data.ccd.updateMetadata({
                model.MD_ROTATION_COR: (-irot) % (2 * math.pi),
            })
        else:
            self._main_data.ebeam.rotation.value = irot
            # need to also set the rotation correction to indicate that the
            # acquired image should be seen straight (not rotated)
            self._main_data.ebeam.updateMetadata({model.MD_ROTATION_COR: irot})

        # update the shear correction and ebeam scaling
        self._main_data.ebeam.updateMetadata({model.MD_SHEAR_COR: ishear})
        self._main_data.ebeam.updateMetadata({
            model.MD_PIXEL_SIZE_COR: iscale_xy,
        })

        # update detector metadata with the SEM image and spot shift correction
        # values
        self._main_data.bsd.updateMetadata({
            model.MD_RESOLUTION_SLOPE: resa,
            model.MD_RESOLUTION_INTERCEPT: resb,
            model.MD_HFW_SLOPE: hfwa,
            model.MD_SPOT_SHIFT: spotshift
        })

    def _check_holder_calib(self):
        """
        Check whether the current sample holder has already been calibrated or
         still needs to go through the "first insertion calibration procedure".
         In that later case, the procedure will be started.
        return (bool): Whether the loading should continue
        """
        try:
            shid, sht = self._main_data.chamber.sampleHolder.value
            if shid is None or sht is None:
                # sample holder was just removed (or something went wrong)
                logging.warning("Failed to read sample holder ID %s, aborting load",
                                (shid, sht))
                return False

            logging.debug("Detected sample holder type %d, id %x", sht, shid)

            # ID number 0 typically indicates something went wrong and it
            # couldn't be read. So instead of asking the user to calibrate it,
            # just tell the user to try to insert the sample holder again.
            if shid == 0:
                dlg = wx.MessageDialog(self._main_frame,
                                       "The connection with the sample holder failed.\n\n"
                                       "Make sure the pins are clean and try re-inserting it.\n"
                                       "If the problem persists, contact the support service.\n"
                                       "The sample holder will now be ejected.\n",
                                       "Sample holder connection failed",
                                       wx.OK | wx.ICON_WARNING)
                dlg.ShowModal()
                dlg.Destroy()
                # Eject the sample holder
                self._main_data.chamberState.value = CHAMBER_VENTING
                return False

            # TODO: just subscribe to the change of sample holder?
            if (
                    isinstance(self._main_data.chamber.registeredSampleHolder,
                               VigilantAttributeBase) and
                    not self._main_data.chamber.registeredSampleHolder.value
            ):
                self.request_holder_calib()  # async
                return False

            # Note the sht is only defined after registration
            if sht != PHENOM_SH_TYPE_OPTICAL:
                logging.info("Sample holder doesn't seem to be an optical one "
                             "but will pretend it is...")
                # FIXME: For now it's needed because some sample holders have
                # not been correctly set and report PHENOM_SH_TYPE_STANDARD
                # Once they are fixed, just skip the calibration, and disable
                # the optical microscope.

            # Look in the config file if the sample holder is known, or needs
            # first-time calibration, and otherwise update the metadata
            if self._calibconf.get_sh_calib(shid) is None:
                self.request_holder_calib() # async
                return False # don't go further, as everything will be taken care

                # TODO: shall we also reference the optical focus? It'd be handy only
                # if the absolute position is used.
        except Exception:
            logging.exception("Failed to set calibration")

        return True

    @call_in_wx_main
    def request_holder_recalib(self, _=None):
        """ Recalibration the sample holder
        This method is asynchronous (running in the main GUI thread)
        _: (wx.Event) Empty place holder, so this method can be attached to menu items
        """

        shid, sht = self._main_data.chamber.sampleHolder.value

        if sht is None:  # No sample holder present
            wx.MessageBox(
                "Please make sure a sample holder with an empty glass is loaded",
                "Missing sample holder",
                style=wx.ICON_ERROR
            )
            return
        elif sht != PHENOM_SH_TYPE_OPTICAL:
            # Hopefully it's just because the sample holder has not been correctly registered
            logging.warn("Wrong sample holder type %d! We will try to calibrate anyway...", sht)

        # Returns 'yes' for automatic, 'no' for manual
        dlg = windelphi.RecalibrationDialog(self._main_frame)
        val = dlg.ShowModal()  # blocks
        dlg.Destroy()

        # Automatic recalibration
        if val == wx.ID_YES:
            logging.info("Starting automatic recalibration for sample holder")
            self._run_full_calibration(shid)
        # Manual recalibration
        elif val == wx.ID_NO:
            windelphi.ManualCalibration()
        else:
            logging.debug("Recalibration cancelled")

    @call_in_wx_main
    def request_holder_calib(self, _=None):
        """
        Handle all the actions needed when no calibration data is available
        for a sample holder (eg, it's the first time it is inserted)
        When this method returns, the sample holder will have been ejected,
        independently of whether the calibration has worked or not.
        This method is asynchronous (running in the main GUI thread)
        """

        shid, sht = self._main_data.chamber.sampleHolder.value
        logging.info("New sample holder %x inserted", shid)
        # Tell the user we need to do calibration, and it needs to have
        # the special sample => Eject (=Cancel) or Calibrate (=OK)
        # Also allow to "register" the sample holder, if that's the first time
        # it is inserted in the Phenom.

        if isinstance(self._main_data.chamber.registeredSampleHolder, VigilantAttributeBase):
            need_register = not self._main_data.chamber.registeredSampleHolder.value
        else:
            need_register = False

        dlg = windelphi.FirstCalibrationDialog(self._main_frame, shid, need_register)
        val = dlg.ShowModal()  # blocks
        regcode = dlg.registrationCode
        dlg.Destroy()

        if val == wx.ID_OK:
            if need_register:
                logging.debug("Trying to register sample holder with code %s", regcode)
                try:
                    self._main_data.chamber.registerSampleHolder(regcode)
                    shid, sht = self._main_data.chamber.sampleHolder.value
                except ValueError as ex:
                    dlg = wx.MessageDialog(self._main_frame,
                                           "Failed to register: %s" % ex,
                                           "Sample holder registration failed",
                                           wx.OK | wx.ICON_WARNING)
                    dlg.ShowModal()
                    dlg.Destroy()
                    # looks like recursive, but as it's call_in_wx_main, it will
                    # return immediately and be actually called later
                    self.request_holder_calib()
                    return

            if sht != PHENOM_SH_TYPE_OPTICAL:
                dlg = wx.MessageDialog(self._main_frame,
                                       "Sample holder type does not correspond to an optical one. "
                                       "This might cause problems with the alignment of the system. "
                                       "For now we will proceed to the calibration.",
                                       "Wrong sample holder type",
                                       wx.OK | wx.ICON_WARNING)
                dlg.ShowModal()
                dlg.Destroy()

            # Now run the full calibration
            logging.info("Starting first time calibration for sample holder")
            self._run_full_calibration(shid)
        else:
            logging.info("Calibration cancelled, ejecting the sample holder")

        # Eject the sample holder
        self._main_data.chamberState.value = CHAMBER_VENTING

    def _run_full_calibration(self, shid):
        # TODO: once the hole focus is not fixed, save it in the config too
        # Perform calibration and update the progress dialog
        calib_dialog = CalibrationProgressDialog(
            self._main_frame,
            self._main_data,
            self._calibconf,
            shid
        )
        calib_dialog.Center()
        calib_dialog.ShowModal()

    def _pressure_changed(self, value):
        if value["pressure"] == self._overview_pressure:
            self._in_overview.set()

    # Rough approximation of the times of each loading action:
    # * 5 sec to load to NavCam (will be much longer if Phenom is in stand-by)
    # * 2 s for moving to the stage center
    # * 1 s for loading the calibration value
    # * 5 s for referencing the optical stage
    # * 1 s for focusing the overview
    # * 2 s for overview acquisition
    # * 10 sec for alignment and overview acquisition
    # * 65 sec from NavCam to SEM
    DELPHI_LOADING_TIMES = (5, 2, 1, 5, 1, 2, 65)  # s

    def DelphiLoading(self):
        """
        Wrapper for DoDelphiLoading. It provides the ability to check the
        progress of the procedure.
        returns (ProgressiveFuture): Progress DoDelphiLoading
        """
        # Create ProgressiveFuture and update its state to RUNNING
        est_start = time.time() + 0.1
        f = model.ProgressiveFuture(start=est_start,
                                    end=est_start + sum(self.DELPHI_LOADING_TIMES))
        # will contain a future to cancel or CANCELLED or FINISHED
        f._delphi_load_state = None

        # Time for each action left
        f._actions_time = list(self.DELPHI_LOADING_TIMES)

        # Task to run
        f.task_canceller = self._CancelDelphiLoading
        f._delphi_load_lock = threading.Lock()

        # Run in separate thread
        delphi_load_thread = threading.Thread(target=executeTask,
                      name="Delphi Loading",
                      args=(f, self._DoDelphiLoading, f))

        delphi_load_thread.start()
        return f

    def _DoDelphiLoading(self, future):
        """
        It performs all the loading steps for Delphi.
        future (model.ProgressiveFuture): Progressive future provided by the wrapper
        raises:
            CancelledError() if cancelled
        """
        logging.debug("Delphi loading...")

        try:

            # If door was just closed, then wait for the Phenom GUI to complete
            # the move to overview. Then continue as usual.
            # FIXME: Maybe we can get rid of this once Phenom GUI can be disabled
            if not self._phenom_load_done:
                logging.debug("Waiting for Phenom GUI to move to overview position...")
                self._main_data.chamber.position.subscribe(self._pressure_changed)
                self._in_overview.wait()
                self._phenom_load_done = True
                self._main_data.chamber.position.unsubscribe(self._pressure_changed)
                self._in_overview.clear()

            if future._delphi_load_state == CANCELLED:
                return

            # _on_overview_position() will take care of going further
            future._actions_time.pop(0)
            pf = self._main_data.chamber.moveAbs({"pressure": self._overview_pressure})
            future._delphi_load_state = pf
            pf.add_update_callback(self._update_load_time)
            pf.result()

            if future._delphi_load_state == CANCELLED:
                return

            # Reference the (optical) stages
            future._actions_time.pop(0)
            f = self._main_data.stage.reference({"x", "y"})
            future._delphi_load_state = f
            f.result()

            if future._delphi_load_state == CANCELLED:
                return
            # Load calibration values (and move the slave stage to wherever
            # the master stage is :-/ )
            future._actions_time.pop(0)
            self._load_holder_calib()

            if future._delphi_load_state == CANCELLED:
                return
            # Move stage to center to be at a good position in overview
            # (actually, we only care about the SEM stage)
            future._actions_time.pop(0)
            f = self._main_data.stage.moveAbs(DELPHI_OVERVIEW_POS)
            future._delphi_load_state = f
            f.result()  # to be sure referencing doesn't cancel the move

            if future._delphi_load_state == CANCELLED:
                return
            # Focus the overview image
            future._actions_time.pop(0)
            f = self._main_data.overview_focus.moveAbs(DELPHI_OVERVIEW_FOCUS)
            future._delphi_load_state = f
            f.result()  # to be sure referencing doesn't cancel the move
            # TODO: do also (or instead) a autofocus,
            # or have a good focus position in the sample holder info

            if future._delphi_load_state == CANCELLED:
                return
            # now acquire one image
            future._actions_time.pop(0)
            ovs = self._get_overview_stream()
            f = _futures.wrapSimpleStreamIntoFuture(ovs)
            future._delphi_load_state = f
            f.result()

            if future._delphi_load_state == CANCELLED:
                return
            wx.CallAfter(self._press_btn.Enable, False)
            # move further to fully under vacuum (should do nothing if already there)
            future._actions_time.pop(0)
            pf = self._main_data.chamber.moveAbs({"pressure": self._vacuum_pressure})
            future._delphi_load_state = pf
            pf.add_update_callback(self._update_load_time)
            pf.result()
            # We know that a good initial focus value is a bit lower than the
            # one found while focusing on the holes
            if self._hole_focus is not None:
                self.good_focus = self._hole_focus - GOOD_FOCUS_OFFSET
                ff = self._main_data.ebeam_focus.moveAbs({"z": self.good_focus})
                ff.result()
            ccd_md = self._main_data.ccd.getMetadata()
            good_hfw = (self._main_data.ccd.resolution.value[0] * ccd_md[model.MD_PIXEL_SIZE][0]) / 2
            self._main_data.ebeam.horizontalFoV.value = good_hfw
            wx.CallAfter(self._press_btn.Enable, True)

        finally:
            with future._delphi_load_lock:
                if future._delphi_load_state == CANCELLED:
                    raise CancelledError()
                future._delphi_load_state = FINISHED

    def _CancelDelphiLoading(self, future):
        """
        Canceller of _DoDelphiLoading task.
        """
        logging.debug("Cancelling Delphi loading...")

        # wait to avoid unloading while Phenom GUI is still loading
        if not self._phenom_load_done:
            self._in_overview.wait()
            # FIXME: remove this asap
            time.sleep(1)

        with future._delphi_load_lock:
            state = future._delphi_load_state

            if state == FINISHED:
                return False
            future._delphi_load_state = CANCELLED

            if hasattr(state, "cancel"): # a future? => cancel it, to stop quicker
                state.cancel()
            logging.debug("Delphi loading cancelled.")

        return True

    def _update_load_time(self, future, start, end):
        """
        Called whenever a (sub)future of the load action updates the time info
        """
        # Kludge to detect that the progressive future has just been created
        # and doesn't contain yet the correct information.
        # TODO: Once Pyro is fixed, it should be possible to remove it.
        if end - start <= 0.1:
            return

        # Add the estimated time that the rest of the procedure will take
        est_end = end + sum(self._chamber_pump_future._actions_time)
        self._chamber_pump_future.set_progress(end=est_end)
        rem_time = est_end - time.time()
        logging.debug("Loading future remaining time: %f", rem_time)


# TODO SparcStateController?
#     "spectrometer": ("specState", HardwareButtonController),
#     "angular": ("arState", HardwareButtonController),
