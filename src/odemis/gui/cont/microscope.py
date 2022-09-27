# -*- coding: utf-8 -*-
"""
@author: Rinze de Laat

Copyright © 2012-2016 Rinze de Laat, Éric Piel, Delmic

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
from past.builtins import basestring
from concurrent.futures._base import CancelledError, CANCELLED, FINISHED
import logging
import math
import numpy
from odemis import model
from odemis.acq import _futures
from odemis.acq import align, stream
from odemis.acq.align import delphi
from odemis.gui import img
from odemis.gui.conf import get_calib_conf
from odemis.gui.model import STATE_ON, CHAMBER_PUMPING, CHAMBER_VENTING, \
    CHAMBER_VACUUM, CHAMBER_VENTED, CHAMBER_UNKNOWN, STATE_OFF
from odemis.gui.util import call_in_wx_main
from odemis.gui.util.widgets import ProgressiveFutureConnector, VigilantAttributeConnector, \
    EllipsisAnimator
from odemis.model import InstantaneousFuture
from odemis.util import executeAsyncTask
import threading
import time
import wx

import odemis.gui.win.delphi as windelphi
import odemis.util.units as units


# Sample holder types in the Delphi, as defined by Phenom World
PHENOM_SH_TYPE_UNKNOWN = -1  # Reported when not yet registered
PHENOM_SH_TYPE_STANDARD = 1  # standard sample holder
PHENOM_SH_TYPE_OPTICAL = 200  # sample holder for the Delphi, containing a lens

DELPHI_OVERVIEW_POS = {"x": 0, "y": 0}  # good position of the stage for overview
DELPHI_OVERVIEW_FOCUS = {"z": 0.006}  # Good focus position for overview image on the Delphi sample holder


class HardwareButtonController(object):
    """
    Default button controller that on handles ON and OFF states
    """

    def __init__(self, btn_ctrl, va, tooltips=None):
        """
        tooltips (None or dict value -> str): Tooltip string for each state.
        """
        self.btn = btn_ctrl
        self.va = va
        self._tooltips = tooltips or {}
        self.vac = VigilantAttributeConnector(va, btn_ctrl, self._va_to_btn, self._btn_to_va,
                                              events=wx.EVT_BUTTON)

    def _va_to_btn(self, state):
        """ Change the button toggle state according to the given hardware state """
        self.btn.SetToggle(state != STATE_OFF)
        self._update_tooltip()

    def _btn_to_va(self):
        """ Return the hardware state associated with the current button toggle state """
        return STATE_ON if self.btn.GetToggle() else STATE_OFF

    def _update_tooltip(self):
        if not self.btn.Enabled:
            return
        state = self.va.value
        if state in self._tooltips:
            self.btn.SetToolTip(self._tooltips[state])

    def Enable(self, enabled=True):
        self.btn.Enable(enabled)
        self._update_tooltip()


class ChamberButtonController(HardwareButtonController):
    """ Controller that allows for the more complex state updates required by the chamber button """

    def __init__(self, btn_ctrl, va, main_data, pressure_ctrl=None):
        """

        :param btn_ctrl: (ImageTextToggleButton) Button that controls and displays the chamber state
        :param va: (VigillantAttribute) The chamber state
        :param main_data: (MainGUIData) GUI microscope model
        :param pressure_ctrl (wxStaticText, None): control for showing the numerical pressure value.
            If None, show the pressure on the button.

        """
        self.main_data = main_data

        # If there is pressure information, assume it is a complete SEM chamber,
        # otherwise assume it uses a sample loader like the Phenom or Delphi.
        if model.hasVA(main_data.chamber, "pressure"):
            self._btn_icons = {
                'normal': img.getBitmap("icon/ico_press.png"),
                'working': img.getBitmap("icon/ico_press_orange.png"),
                'vacuum': img.getBitmap("icon/ico_press_green.png"),
            }

            tooltips = {
                CHAMBER_PUMPING: "Pumping...",
                CHAMBER_VENTING: "Venting...",
                CHAMBER_VENTED: "Pump the chamber",
                CHAMBER_VACUUM: "Vent the chamber",
            }
        else:
            btn_ctrl.SetLabel("LOAD")

            self._btn_icons = {
                'normal': img.getBitmap("icon/ico_eject.png"),
                'working': img.getBitmap("icon/ico_eject_orange.png"),
                'vacuum': img.getBitmap("icon/ico_eject_green.png"),
            }
            tooltips = {
                CHAMBER_PUMPING: "Loading...",
                CHAMBER_VENTING: "Unloading...",
                CHAMBER_VENTED: "Load the sample",
                CHAMBER_VACUUM: "Unload the sample",
            }
        super(ChamberButtonController, self).__init__(btn_ctrl, va, tooltips)

        if model.hasVA(main_data.chamber, "pressure"):
            main_data.chamber.pressure.subscribe(self._on_pressure_change, init=True)

        if pressure_ctrl is None:
            self._pressure_ctrl = btn_ctrl
        else:
            self._pressure_ctrl = pressure_ctrl

    def _va_to_btn(self, state):
        """ Change the button toggle state according to the given hardware state """
        # When the chamber is pumping or venting, it's considered to be working
        if state in {CHAMBER_PUMPING, CHAMBER_VENTING}:
            self.btn.SetIcon(self._btn_icons['working'])
        elif state == CHAMBER_VACUUM:
            self.btn.SetIcon(self._btn_icons['vacuum'])
            self.btn.SetLabel("LOADED")
            # In case the GUI is launched with the chamber pump turned on already, we need to
            # toggle the button by code.
            self.btn.SetToggle(True)
        elif state in {CHAMBER_VENTED, CHAMBER_UNKNOWN}:
            self.btn.SetIcon(self._btn_icons['normal'])
            self.btn.SetToggle(False)
            self.btn.SetLabel("UNLOADED")  # Extra spaces are needed for alignment
        else:
            logging.error("Unknown chamber state %d", state)

        self._update_tooltip()
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
        if self._pressure_ctrl.Label != str_value:
            self._pressure_ctrl.Label = str_value
            self._pressure_ctrl.Refresh()


class SecomStateController(object):
    """
    This controller controls the main microscope buttons (ON/OFF,
    Pause, vacuum...) and updates the model.
    """
    # The classes of streams that are affected by the chamber
    # only SEM, as optical might be used even vented
    cls_streams_involved = stream.EMStream

    def __init__(self, tab_data, tab_panel, st_ctrl):
        """ Binds the 'hardware' buttons to their appropriate
        Vigilant Attributes in the tab and GUI models

        tab_data (MicroscopyGUIData): the data model of the tab
        tab_panel: (wx.Panel): the microscope tab
        st_ctrl (StreamBarController): to start/stop streams
        """
        self._main_data = tab_data.main
        self._tab_data = tab_data
        self._tab_panel = tab_panel
        self._stream_controller = st_ctrl

        # Just to be able to disable the buttons when the chamber is vented
        try:
            va = tab_data.emState
        except AttributeError:
            tab_panel.btn_sem.Hide()
        else:
            self._sem_btn_ctrl = HardwareButtonController(tab_panel.btn_sem, va,
                                  tooltips={STATE_OFF: "Activate the SEM view",
                                            STATE_ON: "Switch off the SEM view"}
                                  )

        try:
            va = tab_data.opticalState
        except AttributeError:
            tab_panel.btn_opt.Hide()
        else:
            self._opt_btn_ctrl = HardwareButtonController(tab_panel.btn_opt, va,
                                  tooltips={STATE_OFF: "Activate the optical view",
                                            STATE_ON: "Switch off the optical view"}
                                  )

        if self._main_data.chamber:
            self._press_btn_ctrl = ChamberButtonController(tab_panel.btn_press,
                                                           self._main_data.chamberState,
                                                           self._main_data)
        else:
            tab_panel.btn_press.Hide()

        # Turn off the light, but set the power to a nice default value
        # TODO: do the same with the brightlight and backlight
        light = self._main_data.light
        if light is not None:
            light.power.value = light.power.range[0]

        # To listen to change in play/pause
        self._active_prev_stream = None

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
            pressures = self._main_data.chamber.axes["vacuum"].choices
            self._vacuum_pressure = min(pressures.keys())
            self._vented_pressure = max(pressures.keys())

            self._chamber_pump_future = InstantaneousFuture()  # used when pumping
            self._chamber_vent_future = InstantaneousFuture()  # used when pumping

            # if there is an overview camera, _and_ it has to be reached via a
            # special "pressure" state => note it down
            self._overview_pressure = None
            if self._main_data.overview_ccd:
                for p, pn in pressures.items():
                    if pn == "overview":
                        self._overview_pressure = p
                        break

            self._main_data.chamberState.subscribe(self.on_chamber_state)
            ch_pos = self._main_data.chamber.position
            ch_pos.subscribe(self.on_chamber_pressure, init=True)

        # disable optical and SEM buttons while there is a preparation process running
        self._main_data.is_preparing.subscribe(self.on_preparation)

    @call_in_wx_main
    def on_preparation(self, is_preparing):
        # Make sure cannot switch stream during preparation
        if hasattr(self, "_sem_btn_ctrl"):
            self._sem_btn_ctrl.Enable(not is_preparing)

        if hasattr(self, "_opt_btn_ctrl"):
            self._opt_btn_ctrl.Enable(not is_preparing)
        # TODO: should disable play/pause of streams + menu too

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

    def _set_ebeam_power(self, on):
        """ Set the ebeam power (if there is an ebeam that can be controlled)
        on (boolean): if True, we set the power on, other will turn it off
        """
        if self._main_data.ebeam is None:
            return

        if not model.hasVA(self._main_data.ebeam, "power"):
            # We cannot change the power => nothing else to try
            logging.debug("Ebeam doesn't support setting power")
            return

        power = self._main_data.ebeam.power
        if on:
            try:
                power.value = power.range[1]  # max!
            except AttributeError:
                try:
                    # if enumerated: the biggest
                    power.value = max(power.choices)
                except AttributeError:
                    logging.error("Unknown ebeam power range, setting to 1")
                    power.value = 1
        else:
            try:
                # if enumerated: the lowest
                power.value = min(power.choices)
            except AttributeError:
                power.value = 0

    def _reset_streams(self):
        """
        Empty the data of the streams which might have no more meaning after
          loading a new sample.
        Also remove the rulers
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

        # Remove all the rulers from all the canvas
        for vp in (self._tab_panel.vp_secom_tl, self._tab_panel.vp_secom_tr,
                   self._tab_panel.vp_secom_bl, self._tab_panel.vp_secom_br,
                   self._tab_panel.vp_overview_sem):
            # We cannot call .clear() on each viewport, as that would remove too
            # many things (including the just acquired overview stream).
            if vp.canvas.gadget_overlay:
                vp.canvas.gadget_overlay.clear()

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
            if hasattr(self, "_sem_btn_ctrl"):
                self._sem_btn_ctrl.Enable(True)
            if hasattr(self, "_opt_btn_ctrl"):
                self._opt_btn_ctrl.Enable(True)
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
                self._sem_btn_ctrl.Enable(False)
                self._tab_panel.btn_sem.SetToolTip(u"Please insert a sample first")

            if (
                hasattr(self._tab_data, "opticalState") and
                issubclass(stream.CameraStream, self.cls_streams_involved)
            ):
                self._tab_data.opticalState.value = STATE_OFF
                self._opt_btn_ctrl.Enable(False)
                self._tab_panel.btn_opt.SetToolTip(u"Please insert a sample first")

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
            f = self._main_data.chamber.moveAbs({"vacuum": self._overview_pressure})
            f.add_done_callback(self._on_overview_position)
        else:
            f = self._main_data.chamber.moveAbs({"vacuum": self._vacuum_pressure})
            f.add_done_callback(self._on_vacuum)

        # TODO: if the future is a progressiveFuture, it will provide info
        # on when it will finish => display that in the progress bar. cf Delphi

        # reset the streams to avoid having data from the previous sample
        self._reset_streams()

    def _on_vacuum(self, future):
        pass

    def _start_chamber_venting(self):
        # Pause all streams (SEM streams are most important, but it's
        # simpler for the user to stop all of them)
        for s in self._tab_data.streams.value:
            s.is_active.value = False
            s.should_update.value = False

        self._set_ebeam_power(False)
        self._chamber_vent_future = self._main_data.chamber.moveAbs({"vacuum": self._vented_pressure})
        # Will actually be displayed only if the hw_info is shown
        self._press_btn_ctrl.Enable(False)
        self._chamber_fc = ProgressiveFutureConnector(
            self._chamber_vent_future,
            self._tab_panel.gauge_load_time,
            self._tab_panel.lbl_load_time,
            full=False
        )
        self._chamber_vent_future.add_done_callback(self._on_vented)

    def _on_vented(self, future):
        self.on_chamber_pressure(self._main_data.chamber.position.value)
        wx.CallAfter(self._press_btn_ctrl.Enable, True)

    # TODO: have multiple versions of this method depending on the type of chamber?
    # TODO: have a special states for CHAMBER_OVERVIEW_PRE_VACUUM and CHAMBER_OVERVIEW_POST_VACUUM?
    def on_chamber_pressure(self, position):
        """ Determine the state of the chamber when the pressure changes, and
        do the overview imaging if possible.

        This method can change the state from CHAMBER_PUMPING to CHAMBER_VACUUM
        or from CHAMBER_VENTING to CHAMBER_VENTED.
        """
        # Note, this can be called even if the pressure value hasn't changed.
        currentp = position["vacuum"]
        pressures = self._main_data.chamber.axes["vacuum"].choices
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
            f = self._main_data.chamber.moveAbs({"vacuum": self._vacuum_pressure})
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
        f = self._main_data.chamber.moveAbs({"vacuum": self._vacuum_pressure})
        f.add_done_callback(self._on_vacuum)


class DelphiStateController(SecomStateController):
    """
    State controller with special features for the DEPHI (such as loading/running
    calibration when the sample holder is inserted).
    """

    cls_streams_involved = stream.Stream

    def __init__(self, tab_data, tab_panel, *args, **kwargs):
        self._calibconf = get_calib_conf()
        self._main_frame = tab_panel.Parent

        # Known focus values, from the calibration config
        self._hole_focus = None
        self.good_focus = None
        self.good_optical_focus = None

        # Event for indicating sample reached overview position and phenom GUI
        # loading
        self._in_overview = threading.Event()
        self._phenom_load_done = True

        self._first_calib_dlg = None

        super(DelphiStateController, self).__init__(tab_data, tab_panel, *args, **kwargs)

        # Display the panel with the loading progress indicators
        self._tab_panel.pnl_hw_info.Show()

        # To update the stream status
        self._status_prev_streams = []
        self._ellipsis_animator = None  # animator for messages containing ellipsis character
        tab_data.streams.subscribe(self._subscribe_current_stream_status, init=True)

        # Last stage and focus move time
        self._last_pos = self._main_data.stage.position.value.copy()
        self._last_pos.update(self._main_data.focus.position.value)
        self._move_time = time.time()
        self._main_data.stage.position.subscribe(self._on_move, init=True)
        self._main_data.focus.position.subscribe(self._on_move, init=True)

        # To update the display/hide the stream status according to visibility
        self._views_list = []
        self._views_prev_list = []
        tab_data.views.subscribe(self._subscribe_current_view_visibility, init=True)

        ch_opened = self._main_data.chamber.opened
        ch_opened.subscribe(self.on_door_opened)

        # At init, depending on the position of the sample loader, we can do a
        # few things:
        # * if in overview position, start by pumping (which will indirectly first acquire an image)
        # * if in SEM position, check for the calibration immediately
        ch_pos = self._main_data.chamber.position
        if ch_pos.value["vacuum"] == self._overview_pressure:
            self._main_data.chamberState.value = CHAMBER_PUMPING
        elif ch_pos.value["vacuum"] == self._vacuum_pressure:
            # If it's loaded, the sample holder is registered for sure, and the
            # calibration should have already been done. Otherwise request
            # ejecting the sample holder
            try:
                # TODO: if the opt stage/focus are not referenced => do something
                # clever. Note: it's probably because Odemis backend just started
                # while the SEM is loaded. With some chance the stage was referenced
                # but Odemis doesn't know. Alternatively, it has never been
                # referenced, which is bad. => reference and try to move back to
                # the previous position.
                self._load_holder_calib()
                ccd_md = self._main_data.ccd.getMetadata()
                good_hfw = (self._main_data.ccd.resolution.value[0] * ccd_md[model.MD_PIXEL_SIZE][0]) / 2
                self._main_data.ebeam.horizontalFoV.value = good_hfw
                self._show_progress_indicators(False, True)
            except ValueError:
                dlg = wx.MessageDialog(self._main_frame,
                                       "Sample holder is loaded while there is no calibration information. "
                                       "We will now eject it.",
                                       "Missing calibration information",
                                       wx.OK | wx.ICON_WARNING)
                dlg.ShowModal()
                dlg.Destroy()
                self._main_data.chamberState.value = CHAMBER_VENTING

        # Connect the Delphi recalibration to the menu item
        self._main_frame.Bind(
            wx.EVT_MENU,
            self.request_holder_recalib,
            id=self._main_frame.menu_item_recalibrate.GetId()
        )

        # Progress dialog for calibration
        self._dlg = None

    def _on_move(self, pos):
        """
        Called when the stage or focus moves (changes position)
        pos (dict): new position
        """
        # Check if the position has really changed, as some stage tend to
        # report "new" position even when no actual move has happened
        if self._last_pos == pos:
            return
        self._last_pos.update(pos)
        self._move_time = time.time()
        self._remove_misaligned()
        self.decide_status()

    def _subscribe_current_view_visibility(self, views):
        """
        Subscribe to the list of visible streams of each view.
        """
        if len(self._views_prev_list) != 0:
            for v in self._views_prev_list:
                v.stream_tree.flat.unsubscribe(self.decide_status)

        self._views_list = views
        for v in self._views_list:
            v.stream_tree.flat.subscribe(self.decide_status, init=True)

        self._views_prev_list = self._views_list

    def _subscribe_current_stream_status(self, streams):
        """ Find all the streams that have a status or calibrated VA and
        subscribe to them in order to decide what status message needs to be
        displayed.
        """
        # First unsubscribe from the previous streams
        if len(self._status_prev_streams) != 0:
            for s in self._status_prev_streams:
                s.status.unsubscribe(self.decide_status)
                if model.hasVA(s, "calibrated"):
                    s.calibrated.unsubscribe(self.decide_status)

        for s in streams:
            s.status.subscribe(self.decide_status)
            # status is actually only used to inform about the spot alignment
            # progress, not when the stream goes misaligned (to decide this
            # message we use decide_status). For this the calibrated VA is used
            # by AlignedSEMStream
            if model.hasVA(s, "calibrated"):
                s.calibrated.subscribe(self.decide_status)

        # just to initialize
        self.decide_status()

        self._status_prev_streams = streams

    @call_in_wx_main
    def decide_status(self, _=None):
        """
        Decide the status displayed based on the visible and calibrated streams.
        If the message contains the special character … (ellipsis), it will be
         animated.
        """
        if self._ellipsis_animator:
            # cancel if there is an ellipsis animator updating the status message
            self._ellipsis_animator.cancel()
            self._ellipsis_animator = None

        action = None
        lvl = None
        msg = ""

        misaligned = False
        for s in self._tab_data.streams.value:
            if None not in s.status.value:
                lvl, msg = s.status.value
                if not isinstance(msg, basestring):
                    # it seems it also contains an action
                    msg, action = msg

        visible_streams = set()
        for v in self._tab_data.views.value:
            if v.name.value != "Overview":
                for s in v.stream_tree.flat.value:
                    stream_img = s.image.value
                    if not s.should_update.value and stream_img is None:
                        continue
                    else:
                        visible_streams.add(s)
                    if ((stream_img is not None and model.hasVA(s, "calibrated") and not s.calibrated.value) or
                            self._is_misaligned(s)):
                        misaligned = True

        # If there is a stream status we will display it anyway
        if lvl is None:
            msg = ""
            # If there is just one or no stream displayed, there is no need to
            # show any status
            if len(visible_streams) > 1 and misaligned:
                lvl = logging.WARNING
                msg = u"Displayed streams might be misaligned"
                action = u"Update any stream acquired in old position"

        if action is None:
            action = ""
        if u"…" in msg:
            self._ellipsis_animator = EllipsisAnimator(msg, self._tab_panel.lbl_stream_status)
            self._ellipsis_animator.start()
        else:
            self._tab_panel.lbl_stream_status.SetLabel(msg)
        self._show_status_icons(lvl, action)

    @call_in_wx_main
    def _remove_misaligned(self):
        # TODO: this work on the DELPHI because the stage is open-loop.
        # So when there is no user-requested move, the position doesn't change.
        # However, if the stage was closed-loop, the position would keep slightly
        # updating (because of drift, encoder error, etc.). This would cause any
        # stream not currently playing to automatically hide.
        # That's why that code _cannot_ be used as-is on a SECOM. Anyway, on the
        # SECOM, a stage move doesn't move the lenses, so the misalignment is
        # much less significant.

        # Hide all the misaligned streams
        for s in self._tab_data.streams.value:
            if ((model.hasVA(s, "calibrated") and (not s.calibrated.value)) or
                    self._is_misaligned(s)):
                for v in self._tab_data.views.value:
                    # Never hide an active stream
                    if s in v.stream_tree.flat.value and not s.should_update.value:
                        v.removeStream(s)

    def _is_misaligned(self, stream):
        return (not stream.should_update.value and ((stream.image.value is not None) and
                stream.image.value.metadata.get(model.MD_ACQ_DATE, time.time()) < self._move_time))

    def _show_status_icons(self, lvl, action):
        """
        lvl (None or int): level of the message. None => no icon shown
        action (str): the tooltip on the message (explaining what to do to fix the error)
        """
        self._tab_panel.bmp_stream_status_info.Show(lvl in (logging.INFO, logging.DEBUG))
        self._tab_panel.bmp_stream_status_warn.Show(lvl == logging.WARN)
        self._tab_panel.bmp_stream_status_error.Show(lvl == logging.ERROR)
        self._tab_panel.pnl_stream_status.SetToolTip(action)
        self._tab_panel.pnl_hw_info.Layout()

    def _show_progress_indicators(self, show_load, show_status):
        """
        Show or hide the loading progress indicators for the chamber and sample holder

        The stream status text will be hidden if the progress indicators are shown.
        """
        assert not (show_load and show_status), "Cannot display both simultaneously"
        logging.debug("Load shown: %s, status shown: %s", show_load, show_status)
        self._tab_panel.pnl_load_status.Show(show_load)
        self._tab_panel.pnl_stream_status.Show(show_status)
        self._tab_panel.pnl_hw_info.Layout()

    @call_in_wx_main
    def on_door_opened(self, value):
        """
        Disable the load button when the chamber door is open, or there is no sample
        """
        loadable = not value and None not in self._main_data.chamber.sampleHolder.value
        self._press_btn_ctrl.Enable(loadable)
        if loadable:  # Immediately start loading while at it...
            if self._main_data.chamberState.value in (CHAMBER_VENTED, CHAMBER_UNKNOWN):
                self._tab_panel.btn_press.SetToggle(True)
                self._phenom_load_done = False
                self._main_data.chamberState.value = CHAMBER_PUMPING
            else:
                # That's a little weird, but it could be just spurious notification
                # of "closed" -> "closed".
                logging.info("Door closed (again?) while the chamber was not vented")
        else:
            self._tab_panel.btn_press.SetToolTip(u"Please insert a sample first")
            # In case we asked to eject/calibrate the sample, stop asking
            if self._first_calib_dlg:
                self._first_calib_dlg.Close()

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

        self._chamber_pump_future.add_done_callback(self._on_vacuum)

    def _set_ebeam_power(self, on):
        # The Delphi has a ebeam.power but it shouldn't be turned on/off during
        # (un)loading, as it takes time, and the Phenom already does the right
        # thing.
        pass

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
        # TODO: just move "stage" instead, to make the position update properly
        referenced = self._main_data.aligner.referenced.value
        pos = {"x": 0, "y": 0}
        for a in list(pos.keys()):
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
            logging.warning("No sample holder loaded!")
            return
        elif sht != PHENOM_SH_TYPE_OPTICAL:
            # Log the warning but load the calibration data
            logging.warning("Wrong sample holder type! We will try to load the "
                         "calibration data anyway...")

        calib = self._calibconf.get_sh_calib(shid)
        if calib is None:
            raise ValueError("Calibration data for sample holder (%x) is not present" %
                             (shid,))

        # TODO: to be more precise on the stage rotation, we'll need to
        # locate the top and bottom holes of the sample holder, using
        # the SEM. So once the sample is fully loaded, new and more
        # precise calibration will be set.
        htop, hbot, hfoc, ofoc, strans, sscale, srot, iscale, irot, iscale_xy, ishear, resa, resb, hfwa, scaleshift = calib
        self._hole_focus = hfoc
        self.good_optical_focus = ofoc

        # update metadata to stage
        self._main_data.stage.updateMetadata({
            model.MD_POS_COR: strans,
            model.MD_PIXEL_SIZE_COR: sscale,
            model.MD_ROTATION_COR: srot
        })

        # The overview image has metadata in SEM coordinates => convert to optical
        # stage coordinates.
        # In practice, the overview camera and the internal SEM position of the
        # Phenom are not perfectly aligned anyway. The Phenom calibration only
        # allow to correct for translation. As the scale and rotation correction
        # for optical-> SEM stage are normally tiny, it's unlikely to really help.
        # They are here mostly only for the sake of making the code look right.
        ovs = self._get_overview_stream()
        ovs._forcemd = {
            model.MD_POS_COR: strans,
            model.MD_PIXEL_SIZE_COR: (1 / sscale[0], 1 / sscale[1]),
            model.MD_ROTATION_COR: srot,
        }

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
        self._main_data.ebeam.updateMetadata({
            model.MD_SHEAR_COR: ishear,
            model.MD_PIXEL_SIZE_COR: iscale_xy,
        })

        # Update ebeam scanner with the SEM image and spot shift correction
        self._main_data.ebeam.updateMetadata({
            model.MD_RESOLUTION_SLOPE: resa,
            model.MD_RESOLUTION_INTERCEPT: resb,
            model.MD_HFW_SLOPE: hfwa,
            model.MD_SPOT_SHIFT: scaleshift
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
                logging.warning("Failed to read sample holder ID %x (type %d), aborting load",
                                shid, sht)
                # Eject the sample holder (mainly to update the load button
                # state, since there is nothing really loaded)
                self._main_data.chamberState.value = CHAMBER_VENTING
                return False

            logging.debug("Detected sample holder type %d, ID %x", sht, shid)

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
            if (model.hasVA(self._main_data.chamber, "registeredSampleHolder") and
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
            # first-time calibration
            self._calibconf.read()  # Force reloading it (in case it was changed by re-calibration)
            if self._calibconf.get_sh_calib(shid) is None:
                self.request_holder_calib()  # async
                return False  # don't go further, as everything will be taken care

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
            logging.warning("Wrong sample holder type %d! We will try to calibrate anyway...", sht)

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

        if model.hasVA(self._main_data.chamber, "registeredSampleHolder"):
            need_register = not self._main_data.chamber.registeredSampleHolder.value
        else:
            need_register = False

        dlg = windelphi.FirstCalibrationDialog(self._main_frame, shid, need_register)
        self._first_calib_dlg = dlg  # To close it when the sample is ejected by other means
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
        calib_dialog = windelphi.CalibrationProgressDialog(
            self._main_frame,
            self._main_data,
            shid
        )
        calib_dialog.Title = "Sample holder automatic calibration"
        calib_dialog.Center()
        calib_dialog.ShowModal()

    def _pressure_changed(self, value):
        if value["vacuum"] == self._overview_pressure:
            self._in_overview.set()

    # Rough approximation of the times of each loading action:
    # * 5 sec to load to NavCam (will be much longer if Phenom is in stand-by)
    # * 1 s for loading the calibration value
    # * 5 s for referencing the optical stage
    # * 2 s for moving to the stage center
    # * 1 s for focusing the overview
    # * 2 s for overview acquisition
    # * 65 sec from NavCam to SEM
    DELPHI_LOADING_TIMES = (5, 1, 5, 2, 1, 2, 65)  # s

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
        executeAsyncTask(f, self._DoDelphiLoading, args=(f,))
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

            # Move to overview (NavCam) mode
            future._actions_time.pop(0)
            pf = self._main_data.chamber.moveAbs({"vacuum": self._overview_pressure})
            future._delphi_load_state = pf
            pf.add_update_callback(self._update_load_time)
            pf.result()

            if future._delphi_load_state == CANCELLED:
                return

            # Load calibration values
            future._actions_time.pop(0)
            self._load_holder_calib()

            if future._delphi_load_state == CANCELLED:
                return

            # Reference the (optical) stages
            future._actions_time.pop(0)
            f = self._main_data.stage.reference({"x", "y"})
            future._delphi_load_state = f
            f.add_update_callback(self._update_load_time)
            f.result()

            if self.good_optical_focus is not None:
                f = self._main_data.focus.reference({"z"})
                future._delphi_load_state = f
                try:
                    f.result()
                except Exception as ex:
                    # It's annoying (and it's a sign of an issue with the hardware),
                    # but we can deal with the focus not being referenced
                    logging.error("Focus referencing failed: %s. Optical focus will need to be done manually.", ex)
            else:
                logging.warning("Optical focus undefined, a new sample holder calibration is recommended")

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
            wx.CallAfter(self._press_btn_ctrl.Enable, False)
            # move further to fully under vacuum (should do nothing if already there)
            future._actions_time.pop(0)
            pf = self._main_data.chamber.moveAbs({"vacuum": self._vacuum_pressure})
            future._delphi_load_state = pf
            pf.add_update_callback(self._update_load_time)
            pf.result()

            # We know that a good initial focus value is a bit lower than the
            # one found while focusing on the holes
            ff = None
            if self._hole_focus is not None:
                self.good_focus = self._hole_focus - delphi.GOOD_FOCUS_OFFSET
                ff = self._main_data.ebeam_focus.moveAbs({"z": self.good_focus})

            of = None
            if self.good_optical_focus is not None:
                if self._main_data.focus.referenced.value.get("z", False):
                    of = self._main_data.focus.moveAbs({"z": self.good_optical_focus})
                else:
                    logging.warning("Focus axis is not referenced, so cannot be set to a known focus position")

            if ff:
                ff.result()
            if of:
                of.result()

            ccd_md = self._main_data.ccd.getMetadata()
            good_hfw = (self._main_data.ccd.resolution.value[0] * ccd_md[model.MD_PIXEL_SIZE][0]) / 2
            self._main_data.ebeam.horizontalFoV.value = good_hfw
            wx.CallAfter(self._press_btn_ctrl.Enable, True)

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

            if hasattr(state, "cancel"):  # a future? => cancel it, to stop quicker
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


class FastEMStateController(object):
    """
    Manages the chamber pressure and ebeam power states.
    """

    def __init__(self, tab_data, tab_panel):
        """ Binds the 'hardware' buttons (pressure and ebeam) to their appropriate
        Vigilant Attributes in the tab and GUI models

        tab_data (MicroscopyGUIData): ebeam, chamber
        tab_panel: (wx.Panel): the microscope tab
        """
        self._main_data = tab_data.main
        self._tab_data = tab_data
        self._tab_panel = tab_panel

        # E-beam management
        if model.hasVA(self._main_data.ebeam, "power"):
            tooltips = {STATE_OFF: "Turn E-beam on", STATE_ON: "Turn E-beam off"}
            self._ebeam_btn_ctrl = HardwareButtonController(tab_panel.btn_ebeam,
                                                            self._main_data.emState,
                                                            tooltips)
            self._main_data.emState.subscribe(self._on_ebeam_state)
            self._main_data.ebeam.power.subscribe(self._on_ebeam_power, init=True)
        else:
            tab_panel.btn_ebeam.Show(False)
            logging.warning("Ebeam doesn't have 'power' VA.")

        # Chamber management
        if self._main_data.chamber:
            self._press_btn_ctrl = ChamberButtonController(tab_panel.btn_pressure,
                                                           self._main_data.chamberState,
                                                           self._main_data,
                                                           pressure_ctrl=tab_panel.pressure_label)
            self._main_data.chamberState.subscribe(self.on_chamber_state)

            vacuum_values = self._main_data.chamber.axes["vacuum"].choices
            self._vacuum_pos = min(vacuum_values.keys())
            self._vented_pos = max(vacuum_values.keys())
            self._main_data.chamber.position.subscribe(self.on_chamber_pos, init=True)
        else:
            tab_panel.btn_pressure.Show(False)
            logging.warning("Microscope doesn't have a chamber component.")

    def _on_ebeam_state(self, _):
        self._main_data.ebeam.power.value = (self._main_data.emState.value == STATE_ON)

    def _on_ebeam_power(self, power):
        self._main_data.emState.value = STATE_ON if power else STATE_OFF

    def on_chamber_state(self, state):
        """
        Set the desired pressure on the chamber when the chamber's state changes
        Only 'active' states (i.e. either CHAMBER_PUMPING or CHAMBER_VENTING)
        will start a change in pressure.
        """
        logging.debug("Chamber state changed to %d", state)
        chamber = self._main_data.chamber
        if state == CHAMBER_PUMPING:
            wx.CallAfter(self._press_btn_ctrl.Enable, False)
            f = chamber.moveAbs({"vacuum": self._vacuum_pos})
            f.add_done_callback(self._on_future_done)
        elif state == CHAMBER_VENTING:
            wx.CallAfter(self._press_btn_ctrl.Enable, False)
            f = chamber.moveAbs({"vacuum": self._vented_pos})
            f.add_done_callback(self._on_future_done)

    def _on_future_done(self, f):
        try:
            f.result()
        except Exception as ex:
            logging.error("Future of chamber move failed with exception '%s'.", ex)

        self._main_data.emState.value = STATE_ON if self._main_data.ebeam.power.value else STATE_OFF

        current_vacuum = self._main_data.chamber.position.value["vacuum"]
        if current_vacuum == self._vacuum_pos:
            self._main_data.chamberState.value = CHAMBER_VACUUM
        elif current_vacuum == self._vented_pos:
            self._main_data.chamberState.value = CHAMBER_VENTED
        else:
            self._main_data.chamberState.value = CHAMBER_UNKNOWN
        wx.CallAfter(self._press_btn_ctrl.Enable, True)

    def on_chamber_pos(self, position):
        """
        Determine the state of the chamber when the pressure changes on the hardware.
        """
        if self._main_data.chamberState.value in (CHAMBER_PUMPING, CHAMBER_VENTING):
            # PUMPING/VENTING states are used to signal a pump/vent request from the GUI,
            # don't update the state while a pump/vent process is in progress.
            return

        current_vacuum = position["vacuum"]
        if current_vacuum == self._vacuum_pos:
            self._main_data.chamberState.value = CHAMBER_VACUUM
        elif current_vacuum == self._vented_pos:
            self._main_data.chamberState.value = CHAMBER_VENTED
        else:
            self._main_data.chamberState.value = CHAMBER_UNKNOWN
