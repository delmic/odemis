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
from abc import ABCMeta
import logging
import numpy
from odemis import model
from odemis.acq import align, stream
from odemis.gui.model import STATE_ON, CHAMBER_PUMPING, CHAMBER_VENTING, \
    CHAMBER_VACUUM, CHAMBER_VENTED, CHAMBER_UNKNOWN, STATE_OFF
from odemis.gui.util import call_after
from odemis.gui.util.widgets import VigilantAttributeConnector
from odemis.model import getVAs
import wx

import odemis.gui.img.data as imgdata
import odemis.util.units as units


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

        # Look for which buttons actually exist, and which VAs exist. Bind the
        # fitting ones
        self._btn_controllers = []

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
            self._btn_controllers.append(btn_cont)

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
    """
    Controller that allows for the more complex state updates required by the chamber button
    """

    def __init__(self, btn_ctrl, va, main_data):
        """
        :type btn_ctrl: odemis.gui.comp.buttons.ImageTextToggleButton

        """
        super(ChamberButtonController, self).__init__(btn_ctrl, va, main_data)
        self.main_data = main_data

        # Since there are various factors that determine what images will be used as button faces,
        # (so, not just the button state!) we will explicitly define them in this class.
        self._btn_faces = self._determine_button_faces(main_data.role)
        self._tooltips = self._determine_tooltip(main_data.role)

        if 'pressure' in getVAs(main_data.chamber):
            main_data.chamber.pressure.subscribe(self._update_label, init=True)
        else:
            self.btn.SetLabel("CHAMBER")

    def _determine_button_faces(self, role):
        """ Determine what button faces to use depending on values found in main_data """

        if role == "secommini":
            return {
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
        else:
            return {
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

    def _determine_tooltip(self, role):
        if role == "secommini":
            return {
                CHAMBER_PUMPING: "Loading...",
                CHAMBER_VENTING: "Ejecting...",
                CHAMBER_VENTED: "Load the sample",
                CHAMBER_VACUUM: "Eject the sample",
                }
        else:
            return{
                CHAMBER_PUMPING: "Pumping...",
                CHAMBER_VENTING: "Venting...",
                CHAMBER_VENTED: "Pump the chamber",
                CHAMBER_VACUUM: "Vent the chamber",
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

            # In case the GUI is launched with the chamber pump turned on already, we need to
            # toggle the button by code.
            self.btn.SetToggle(True)
        else:
            self.btn.SetBitmapLabel(self._btn_faces['normal']['normal'])
            self.btn.SetBitmaps(
                bmp_h=self._btn_faces['normal']['hover'],
                bmp_sel=self._btn_faces['normal']['active']
            )

        # Set the tooltip
        if state in self._tooltips:
            self.btn.SetToolTipString(self._tooltips[state])

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

    @call_after
    def _update_label(self, pressure_val):
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

    def __init__(self, tab_data, main_frame, btn_prefix, st_ctrl):
        super(SecomStateController, self).__init__(tab_data, main_frame, btn_prefix)

        self._tab_data = tab_data
        self._main_data = tab_data.main
        self._stream_controller = st_ctrl

        # Just to be able to disable the buttons when the chamber is vented
        self._sem_btn = getattr(main_frame, btn_prefix + "sem")
        self._opt_btn = getattr(main_frame, btn_prefix + "opt")

        # The classes of streams that are afffected by the chamber
        if tab_data.main.role == "secommini":
            self._cls_streams_involved = stream.Stream
        else: # SECOM => only SEM as optical might be used even vented
            self._cls_streams_involved = stream.EM_STREAMS

        # Optical state is almost entirely handled by the streams, but for the
        # light power we still handle it globally
        if hasattr(tab_data, "opticalState"):
            tab_data.opticalState.subscribe(self._onOpticalState)

        # Manage the chamber
        if hasattr(self._main_data, "chamberState"):
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

            self._main_data.chamberState.subscribe(self.onChamberState)
            ch_pos = self._main_data.chamber.position
            ch_pos.subscribe(self.on_chamber_pressure, init=True)

            # at init, if chamber is in overview position, start by pumping
            # (which will indirectly first acquire an image)
            if ch_pos.value["pressure"] == self._overview_pressure:
                self._main_data.chamberState.value = CHAMBER_PUMPING

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

    def _setEbeamPower(self, on):
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
                power.value = power.range[1] # max!
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
            if not isinstance(s, self._cls_streams_involved):
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

    @call_after
    def onChamberState(self, state):
        """ Set the desired pressure on the chamber when the chamber's state changes

        Only 'active' states (i.e. either CHAMBER_PUMPING or CHAMBER_VENTING)
        will start a change in pressure.
        """
        logging.debug("Chamber state changed to %d", state)
        # In any case, make sure the streams cannot be started
        if state == CHAMBER_VACUUM:
            self._sem_btn.Enable(True)
            self._sem_btn.SetToolTip(None)
            self._opt_btn.Enable(True)
            self._opt_btn.SetToolTip(None)
            # TODO: enable overview move
            self._stream_controller.enableStreams(True)
        else:
            # TODO: disable overview move
            # Disable SEM button
            self._sem_btn.Enable(False)
            self._sem_btn.SetToolTipString("Chamber must be under vacuum to activate the SEM")

            # Disable Optical button (if SECOMmini)
            if self._main_data.role == "secommini":
                self._opt_btn.Enable(False)
                self._opt_btn.SetToolTipString("Chamber must be under vacuum to activate the optical view")

            self._stream_controller.enableStreams(False, self._cls_streams_involved)

        # TODO: handle the "cancellation" (= the user click on the button while
        # it was in a *ING state = the latest move is not yet done or overview
        # acquisition is happening)

        # TODO: add warning/info message if the chamber move fails

        # Actually start the pumping/venting
        if state == CHAMBER_PUMPING:
            # in case the chamber was venting, or has several queued move, will
            # reset everything
            self._main_data.chamber.stop()

            if self._overview_pressure is not None:
                # TODO: check if we already are in overview state ?
                # _start_overview_acquisition() will take care of going further
                f = self._main_data.chamber.moveAbs({"pressure": self._overview_pressure})
                f.add_done_callback(self._start_overview_acquisition)
            else:
                f = self._main_data.chamber.moveAbs({"pressure": self._vacuum_pressure})

            # reset the streams to avoid having data from the previous sample
            self._reset_streams()
        elif state == CHAMBER_VENTING:
            self._main_data.chamber.stop()

            # Pause all streams (SEM streams are most important, but it's
            # simpler for the user to stop all of them)
            for s in self._tab_data.streams.value:
                s.is_active.value = False
                s.should_update.value = False

            self._setEbeamPower(False)
            f = self._main_data.chamber.moveAbs({"pressure": self._vented_pressure})
        elif state == CHAMBER_VACUUM:
            self._setEbeamPower(True)
            # if no special place for overview, then we do it once after
            # the chamber is ready.
            if self._main_data.overview_ccd and self._overview_pressure is None:
                # TODO: maybe could be done before full vacuum, but we need a
                # way to know from the hardware.
                self._start_overview_acquisition()

        # TODO: if the future is a progressiveFuture, it will provide info
        # on when it will finish => display that (in the tooltip of the chamber
        # button)

    # TODO: have multiple versions of this method depending on the type of
    # chamber?
    # TODO: have a special states for CHAMBER_OVERVIEW_PRE_VACUUM and
    # CHAMBER_OVERVIEW_POST_VACUUM ?
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
            #self._main_data.chamberState.value = CHAMBER_UNKNOWN

    def _start_overview_acquisition(self, unused=None):
        logging.debug("Starting overview acquisition")

        # Start autofocus, and the rest will be done asynchronously
        if self._main_data.overview_focus:
            # TODO: center the sample to the view
            # We are using the best accuracy possible: 0
            f = align.autofocus.AutoFocus(self._main_data.overview_ccd, None,
                                          self._main_data.overview_focus, 0)
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
        try:
            future.result()
            logging.debug("Overview focused")
        except Exception:
            logging.info("Auto-focus on overview failed")

        # now acquire one image
        try:
            ovs = self._get_overview_stream()
        except LookupError:
            logging.exception("Failed to acquire overview image")
            return

        ovs.image.subscribe(self._on_overview_image)
        # start acquisition
        ovs.should_update.value = True
        ovs.is_active.value = True

        # TODO: have a timer to detect if no image ever comes, give up and move
        # to final pressure

    def _on_overview_image(self, image):
        """
        Called once the overview image has been acquired
        """
        logging.debug("New overview image acquired")
        # Stop the stream (the image is immediately displayed in the view)
        try:
            ovs = self._get_overview_stream()
        except LookupError:
            logging.exception("Failed to acquire overview image")
            return

        ovs.is_active.value = False
        ovs.should_update.value = False

        if not self._main_data.chamberState.value in {CHAMBER_PUMPING, CHAMBER_VACUUM}:
            logging.warning("Receive an overview image while in state %s",
                            self._main_data.chamberState.value)
            return # don't ask for vacuum

        # move further to fully under vacuum (should do nothing if already there)
        f = self._main_data.chamber.moveAbs({"pressure": self._vacuum_pressure})


# TODO SparcStateController?
#     "spectrometer": ("specState", HardwareButtonController),
#     "angular": ("arState", HardwareButtonController),
