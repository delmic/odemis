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
import logging
import math
import numpy
from odemis import model
from odemis.acq import align, stream
from odemis.gui.conf import get_calib_conf
from odemis.gui.model import STATE_ON, CHAMBER_PUMPING, CHAMBER_VENTING, \
    CHAMBER_VACUUM, CHAMBER_VENTED, CHAMBER_UNKNOWN, STATE_OFF
from odemis.gui.util import call_in_wx_main, ignore_dead
from odemis.gui.util.widgets import VigilantAttributeConnector
from odemis.model import getVAs, VigilantAttributeBase
import wx
import odemis.gui.img.data as imgdata
import odemis.gui.win.delphi as windelphi
import odemis.util.units as units
from odemis.gui.win.delphi import CalibrationProgressDialog
import time
from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, \
    RUNNING
from odemis.acq._futures import executeTask
import threading
from odemis.gui.util.widgets import ProgessiveFutureConnector

# Sample holder types in the Delphi, as defined by Phenom World
PHENOM_SH_TYPE_STANDARD = 1  # standard sample holder
# FIXME: need to find out the real type number
PHENOM_SH_TYPE_OPTICAL = 1023  # sample holder for the Delphi, containing a lens

DELPHI_OVERVIEW_POS = {"x": 0, "y": 0}  # good position of the stage for overview


class MicroscopeStateController(object):
    """
    This controller controls the main microscope buttons (ON/OFF,
    Pause, vacuum...) and updates the model. To query/change the status of a
    specific component, use the main data model directly.
    """
    __metaclass__ = ABCMeta
    btn_to_va = {}

    def __init__(self, tab_data, main_frame, btn_prefix):
        """ Binds the 'hardware' buttons to their appropriate
        Vigilant Attributes in the model.MainGUIData

        tab_data (MicroscopyGUIData): the data model of the tab
        main_frame: (wx.Frame): the main frame of the GUI
        btn_prefix (string): common prefix of the names of the buttons
        """
        self._tab_data = tab_data
        self._main_frame = main_frame

        # Look for which buttons actually exist, and which VAs exist. Bind the fitting ones
        self._btn_controllers = {}

        for btn_name, (va_name, control_class) in self.btn_to_va.items():
            btn = getattr(main_frame, btn_prefix + btn_name, None)
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

            self._btn_faces = {
                'normal': {
                    'normal': imgdata.btn_press.Bitmap,
                    'hover': imgdata.btn_press_h.Bitmap,
                    'active': imgdata.btn_press_a.Bitmap,
                },
                'working': {
                    'normal': imgdata.btn_press_orange.Bitmap,
                    'hover': imgdata.btn_press_orange_h.Bitmap,
                    'active': imgdata.btn_press_orange_a.Bitmap,
                },
                'vacuum': {
                    'normal': imgdata.btn_press_green.Bitmap,
                    'hover': imgdata.btn_press_green_h.Bitmap,
                    'active': imgdata.btn_press_green_a.Bitmap,
                }
            }

            self._tooltips = {
                CHAMBER_PUMPING: "Pumping...",
                CHAMBER_VENTING: "Venting...",
                CHAMBER_VENTED: "Pump the chamber",
                CHAMBER_VACUUM: "Vent the chamber",
            }
        else:
            self.btn.SetLabel("LOAD      ")  # Extra spaces are needed for alignment
            self._btn_faces = {
                'normal': {
                    'normal': imgdata.btn_eject.Bitmap,
                    'hover': imgdata.btn_eject_h.Bitmap,
                    'active': imgdata.btn_eject_a.Bitmap,
                },
                'working': {
                    'normal': imgdata.btn_eject_orange.Bitmap,
                    'hover': imgdata.btn_eject_orange_h.Bitmap,
                    'active': imgdata.btn_eject_orange_a.Bitmap,
                },
                'vacuum': {
                    'normal': imgdata.btn_eject_green.Bitmap,
                    'hover': imgdata.btn_eject_green_h.Bitmap,
                    'active': imgdata.btn_eject_green_a.Bitmap,
                }
            }
            self._tooltips = {
                CHAMBER_PUMPING: "Loading...",
                CHAMBER_VENTING: "Ejecting...",
                CHAMBER_VENTED: "Load the sample",
                CHAMBER_VACUUM: "Eject the sample",
            }

    def _va_to_btn(self, state):
        """ Change the button toggle state according to the given hardware state
        """
        # When the chamber is pumping or venting, it's considered to be working
        if state in {CHAMBER_PUMPING, CHAMBER_VENTING}:
            self.btn.SetBitmapLabel(self._btn_faces['working']['normal'])
            self.btn.SetBitmaps(
                bmp_h=self._btn_faces['working']['hover'],
                bmp_sel=self._btn_faces['working']['active']
            )
        elif state == CHAMBER_VACUUM:
            self.btn.SetBitmapLabel(self._btn_faces['vacuum']['normal'])
            self.btn.SetBitmaps(
                bmp_h=self._btn_faces['vacuum']['hover'],
                bmp_sel=self._btn_faces['vacuum']['active']
            )

            self.btn.SetLabel("UNLOAD  ")  # Extra spaces are needed for alignment
            # In case the GUI is launched with the chamber pump turned on already, we need to
            # toggle the button by code.
            self.btn.SetToggle(True)
        elif state in {CHAMBER_VENTED, CHAMBER_UNKNOWN}:
            self.btn.SetBitmapLabel(self._btn_faces['normal']['normal'])
            self.btn.SetBitmaps(
                bmp_h=self._btn_faces['normal']['hover'],
                bmp_sel=self._btn_faces['normal']['active']
            )
            self.btn.SetToggle(False)
            self.btn.SetLabel("LOAD      ")  # Extra spaces are needed for alignment
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

    def __init__(self, tab_data, main_frame, btn_prefix, st_ctrl):
        super(SecomStateController, self).__init__(tab_data, main_frame, btn_prefix)

        self._main_data = tab_data.main
        self._stream_controller = st_ctrl

        # Just to be able to disable the buttons when the chamber is vented
        self._sem_btn = getattr(main_frame, btn_prefix + "sem")
        self._opt_btn = getattr(main_frame, btn_prefix + "opt")
        self._acq_btn = getattr(main_frame, btn_prefix + "opt")

        # Optical state is almost entirely handled by the streams, but for the
        # light power we still handle it globally
        if hasattr(tab_data, "opticalState"):
            tab_data.opticalState.subscribe(self._onOpticalState)

        # Manage the chamber
        if self._main_data.chamber:
            pressures = self._main_data.chamber.axes["pressure"].choices
            self._vacuum_pressure = min(pressures.keys())
            self._vented_pressure = max(pressures.keys())

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

            # at init, if chamber is in overview position, start by pumping
            # (which will indirectly first acquire an image)
            if ch_pos.value["pressure"] == self._overview_pressure:
                self._main_data.chamberState.value = CHAMBER_PUMPING

    # TODO: move to acq.stream (because there is nothing we can do better here)
    def _onOpticalState(self, state):
        """ Event handler for when the state of the optical microscope changes
        """
        # In general, the streams are in charge of turning on/off the emitters
        # However, as a "trick", for the light with .emissions they leave the
        # power as-is so that the power is the same between all streams.
        # So we need to turn it on the first time.
        if state == STATE_ON:
            light = self._main_data.light
            # if power is above 0 already, it's probably the user who wants
            # to force to a specific value, respect that.
            if light is None or light.power.value != 0:
                return

            # pick a nice value (= slightly more than 0), if not already on
            try:
                # if continuous: 10 %
                light.power.value = light.power.range[1] * 0.1
            except (AttributeError, model.NotApplicableError):
                try:
                    # if enumerated: the second lowest
                    light.power.value = sorted(light.power.choices)[1]
                except (AttributeError, model.NotApplicableError):
                    logging.error("Unknown light power range, setting to 1 W")
                    light.power.value = 1

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
            # in case the chamber was venting, or has several queued move, will reset everything
            self._main_data.chamber.stop()
            self._start_chamber_pumping()
        elif state == CHAMBER_VENTING:
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
        # on when it will finish => display that (in the tooltip of the chamber
        # button)

        # reset the streams to avoid having data from the previous sample
        self._reset_streams()

        # Empty the stage history, as the interesting locations on the previous
        # sample have probably nothing in common with this new sample
        self._tab_data.stage_history.value = []

    def _on_vacuum(self, _):
        pass

    def _start_chamber_venting(self):
        # Pause all streams (SEM streams are most important, but it's
        # simpler for the user to stop all of them)
        for s in self._tab_data.streams.value:
            s.is_active.value = False
            s.should_update.value = False

        self._set_ebeam_power(False)
        f = self._main_data.chamber.moveAbs({"pressure": self._vented_pressure})
        # f.add_update_callback(self._on_vent_update)
        f.add_done_callback(self._on_vented)

    @call_in_wx_main
    def _on_vent_update(self, past, left):
        self._main_frame.lbl_load_time.SetLabel(str(left))

    def _on_vented(self, future):
        self.on_chamber_pressure(self._main_data.chamber.position.value)

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
        self._calibconf = get_calib_conf()

        # Display the panel with the loading progress indicators
        self._main_frame.pnl_hw_info.Show()

        # If starts with the sample fully loaded, check for the calibration now
        ch_pos = self._main_data.chamber.position
        if ch_pos.value["pressure"] == self._vacuum_pressure:
            # If it's loaded, the sample holder is registered for sure, and the
            # calibration should have already been done.
            self._load_holder_calib()

        # Progress dialog for calibration
        self._dlg = None

    def _show_progress_indicators(self, show):
        """ Show or hide the loading progress indicators for the chamber and sample holder

        The loading status text will be hidden if the progress indicators are shown.

        """

        self._main_frame.gauge_load_time.Show(show)
        self._main_frame.lbl_load_time.Show(show)
        if show:
            self._main_frame.lbl_load_status.Show(False)
        self._main_frame.gauge_load_time.Parent.Layout()

    def _show_progress_status(self, show):
        """ Show or hide the loading status text

        The loading progress controls will be hidden if the status is shown.

        """

        if show:
            self._main_frame.gauge_load_time.Show(False)
            self._main_frame.lbl_load_time.Show(False)
        self._main_frame.lbl_load_status.Show(show)
        self._main_frame.lbl_load_status.Parent.Layout()

    def _start_chamber_pumping(self):
        # Warning: if the sample holder is not yet registered, the Phenom will
        # not accept to even load it to the overview. That's why checking for
        # calibration/registration must be done immediately. Annoyingly, the
        # type ID of the sample holder is not reported until it's registered.

        if not self._check_holder_calib():
            return

        self._show_progress_indicators(True)
        self.load_future = self.DelphiLoading()
        self._load_future_connector = ProgessiveFutureConnector(self.load_future,
                                                                 self._main_frame.gauge_load_time,
                                                                 self._main_frame.lbl_load_time)

    def _start_chamber_venting(self):
        # On the DELPHI, we also move the optical stage to 0,0 (= reference
        # position), so that referencing will be faster on next load
        self._main_data.aligner.moveAbs({"x": 0, "y": 0})

        self._show_progress_indicators(True)
        super(DelphiStateController, self)._start_chamber_venting()

    def _on_vented(self, future):
        super(DelphiStateController, self)._on_vented(future)
        self._show_progress_indicators(False)

    def _on_vacuum(self, _):
        self._show_progress_indicators(False)

    def _on_overview_position(self, unused):
        """
        Move the stage to good position for the overview acquisition, and load
        the calibration if it's an optical sample holder.
        """
        # Move to stage to center to be at a good position in overview
        f = self._main_data.stage.moveAbs(DELPHI_OVERVIEW_POS)
        f.result()  # to be sure referencing doesn't cancel the move

        self._load_holder_calib()

        # Reference the (optical) stage
        f = self._main_data.stage.reference({"x", "y"})
        f.result()

        # continue business as usual
        self._start_overview_acquisition()

    def _start_overview_acquisition(self):
        logging.debug("Starting overview acquisition")
        # FIXME: need to check if autofocus is needed sometimes (and improves
        # over the default good position set by the driver)
        self._on_overview_focused(None)

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
            self.rest_time_est = 0  # s, see estimateDelphiLoading()
            f.add_update_callback(self.on_step_update)
            f.add_done_callback(self._on_vacuum)

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
        self.rest_time_est = 0  # s, see estimateDelphiLoading()
        f.add_update_callback(self.on_step_update)
        f.add_done_callback(self._on_vacuum)

    def _load_holder_calib(self):
        """
        Load the sample holder calibration. This assumes that it is sure that
        the calibration data is present.
        """
        shid, sht = self._main_data.chamber.sampleHolder.value
        # TODO: only try to load if PHENOM_SH_TYPE_OPTICAL, otherwise just return

        calib = self._calibconf.get_sh_calib(shid)
        if calib is None:
            raise ValueError("Calibration data for sample holder is not present")

        # TODO: to be more precise on the stage rotation, we'll need to
        # locate the top and bottom holes of the sample holder, using
        # the SEM. So once the sample is fully loaded, new and more
        # precise calibration will be set.
        htop, hbot, hfoc, strans, sscale, srot, iscale, irot, iscale_xy, ishear, resa, resb, hfwa, spotshift = calib

        # update metadata to stage
        self._main_data.stage.updateMetadata({
            model.MD_POS_COR: strans,
            model.MD_PIXEL_SIZE_COR: sscale,
            model.MD_ROTATION_COR: srot
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
            logging.debug("Detected sample holder type %d, id %d", sht, shid)

            # ID number 0 typically indicates something went wrong and it
            # couldn't be read. So instead of asking the user to calibrate it,
            # just tell the user to try to insert the sample holder again.
            if shid == 0:
                dlg = wx.MessageDialog(self._main_frame,
                                       "The connection with the sample holder failed.\n\n"
                                       "Make sure the pins are clean and try re-inserting it.\n"
                                       "If the problem persists, contact the support service.",
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
                self._request_holder_calib() # async
                return False

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
                self._request_holder_calib() # async
                return False # don't go further, as everything will be taken care

                # TODO: shall we also reference the optical focus? It'd be handy only
                # if the absolute position is used.
        except Exception:
            logging.exception("Failed to set calibration")

        return True

    @call_in_wx_main
    def _request_holder_calib(self):
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
        # TODO: the Phenom backend also requires a code to allow to
        # insert a new sample holder => if sample is not registered, ask the
        # user for it.
        # How? A special VA .sampleRegistered + method .registerSample() on the
        # chamber component? Eventually, it needs to call RegisterSampleHolder.

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
                except ValueError as ex:
                    dlg = wx.MessageDialog(self._main_frame,
                                           "Failed to register: %s" % ex,
                                           "Sample holder registration failed",
                                           wx.OK | wx.ICON_WARNING)
                    dlg.ShowModal()
                    dlg.Destroy()
                    # looks like recursive, but as it's call_in_wx_main, it will
                    # return immediately and be actually called later
                    self._request_holder_calib()
                    return

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
        calib_dialog = CalibrationProgressDialog(self._main_frame, self._main_data,
                                                 self._overview_pressure, self._vacuum_pressure,
                                                 self._vented_pressure, self._calibconf, shid)
        calib_dialog.Center()
        calib_dialog.ShowModal()


# TODO SparcStateController?
#     "spectrometer": ("specState", HardwareButtonController),
#     "angular": ("arState", HardwareButtonController),

    def DelphiLoading(self):
        """
        Wrapper for DoDelphiLoading. It provides the ability to check the
        progress of the procedure.
        returns (ProgressiveFuture): Progress DoDelphiLoading
        """
        # Create ProgressiveFuture and update its state to RUNNING
        est_start = time.time() + 0.1
        f = model.ProgressiveFuture(start=est_start,
                                    end=est_start + self.estimateDelphiLoading())
        f._delphi_load_state = RUNNING

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
            if future._delphi_load_state == CANCELLED:
                raise CancelledError()

            try:
                # _on_overview_position() will take care of going further
                f = self._main_data.chamber.moveAbs({"pressure": self._overview_pressure})
                self.rest_time_est = 75  # s, see estimateDelphiLoading()
                f.add_update_callback(self.on_step_update)
                f.add_done_callback(self._on_overview_position)

                # reset the streams to avoid having data from the previous sample
                self._reset_streams()

                # Empty the stage history, as the interesting locations on the previous
                # sample have probably nothing in common with this new sample
                self._tab_data.stage_history.value = []

                return
            except Exception:
                raise IOError("Delphi loading failed.")
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

        with future._delphi_load_lock:
            if future._delphi_load_state == FINISHED:
                return False
            future._delphi_load_state = CANCELLED
            logging.debug("Delphi loading cancelled.")

        return True

    def estimateDelphiLoading(self):
        """
        Estimates Delphi loading procedure duration
        returns (float):  process estimated time #s
        """
        # Rough approximation
        # 5 sec from unload to NavCam, 10 sec for alignment and overview acquisition
        # and 65 sec from NavCam to SEM
        return 80  # s

    @call_in_wx_main
    @ignore_dead
    def on_step_update(self, future, start, end):
        # Add the estimated time that the rest of the procedure will take
        self.update_load_time(end + self.rest_time_est)

    def update_load_time(self, end):
        self.load_future.set_end_time(end)
        rem_time = end - time.time()
        logging.debug("Loading future remaining time: %f", rem_time)
