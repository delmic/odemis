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
import math
import numpy
from odemis import model
from odemis.acq import align, stream
from odemis.gui.conf import get_calib_conf
from odemis.gui.model import STATE_ON, CHAMBER_PUMPING, CHAMBER_VENTING, \
    CHAMBER_VACUUM, CHAMBER_VENTED, CHAMBER_UNKNOWN, STATE_OFF
from odemis.gui.util import call_after
from odemis.gui.util.widgets import VigilantAttributeConnector
from odemis.gui.win import delphi
from odemis.model import getVAs
import wx

import odemis.gui.img.data as imgdata
import odemis.util.units as units


# Sample holder types in the Delphi, as defined by Phenom World
PHENOM_SH_TYPE_STANDARD = 1 # standard sample holder
# FIXME: need to find out the real type number
PHENOM_SH_TYPE_OPTICAL = 1  # sample holder for the Delphi, containing a lens

DELPHI_OVERVIEW_POS = {"x": 0, "y": 0} # good position of the stage for overview
DELPHI_OVERVIEW_FOCUS = {"z":-0.017885} # good focus position for overview


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

        if role == "delphi":
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
        if role == "delphi":
            return {
                CHAMBER_PUMPING: "Loading...",
                CHAMBER_VENTING: "Ejecting...",
                CHAMBER_VENTED: "Load the sample",
                CHAMBER_VACUUM: "Eject the sample",
            }
        else:
            return {
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
        elif state in {CHAMBER_VENTED, CHAMBER_UNKNOWN}:
            self.btn.SetBitmapLabel(self._btn_faces['normal']['normal'])
            self.btn.SetBitmaps(
                bmp_h=self._btn_faces['normal']['hover'],
                bmp_sel=self._btn_faces['normal']['active']
            )
            self.btn.SetToggle(False)
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

        self._main_data = tab_data.main
        self._stream_controller = st_ctrl

        # Just to be able to disable the buttons when the chamber is vented
        self._sem_btn = getattr(main_frame, btn_prefix + "sem")
        self._opt_btn = getattr(main_frame, btn_prefix + "opt")
        self._acq_btn = getattr(main_frame, btn_prefix + "opt")

        # The classes of streams that are affected by the chamber
        if tab_data.main.role == "delphi":
            self._cls_streams_involved = stream.Stream
        else: # SECOM => only SEM as optical might be used even vented
            self._cls_streams_involved = stream.EM_STREAMS

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

            self._main_data.chamberState.subscribe(self.onChamberState)
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
            # disabling the acquire button is done in the acquisition controller
            self._sem_btn.Enable(True)
            self._sem_btn.SetToolTip(None)
            self._opt_btn.Enable(True)
            self._opt_btn.SetToolTip(None)
            # TODO: enable overview move
            self._stream_controller.enableStreams(True)
        else:
            # TODO: disable overview move
            # Stop SEM streams & disable SEM button
            if hasattr(self._main_data, "semState"):
                self._main_data.semState = STATE_OFF
            self._sem_btn.Enable(False)
            self._sem_btn.SetToolTipString("Chamber must be under vacuum to activate the SEM")

            # Disable Optical button (if DELPHI)
            if self._main_data.role == "delphi":
                if hasattr(self._main_data, "opticalState"):
                    self._main_data.opticalState = STATE_OFF
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
                if self._main_data.role == "delphi":
                    f.add_done_callback(self._delphi_prepare_stage)
                else:
                    f.add_done_callback(self._start_overview_acquisition)
            else:
                f = self._main_data.chamber.moveAbs({"pressure": self._vacuum_pressure})

            # reset the streams to avoid having data from the previous sample
            self._reset_streams()
        elif state == CHAMBER_VENTING:
            self._main_data.chamber.stop()

            # On the DELPHI, we also move the optical stage to 0,0 (= reference
            # position), so that referencing will be faster on next load
            if self._main_data.role == "delphi":
                self._main_data.aligner.moveAbs({"x": 0, "y": 0})

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

    def _delphi_prepare_stage(self, unused):
        """
        Check the sample holder and moves the stage to the default position if
        it's a delphi sample holder (= with a lens and a stage)
        Should be called in overview position
        """
        try:
            # Move the focus to a good position
            self._main_data.overview_focus.moveAbs(DELPHI_OVERVIEW_FOCUS)
            # Move to stage to center to be at a good position in overview
            f = self._main_data.stage.moveAbs(DELPHI_OVERVIEW_POS)

            # TODO: just subscribe to the change? So we could detect a new sample
            # holder even before it's in overview position?
            try:
                shid, sht = self._main_data.chamber.sampleHolder.value
                logging.debug("Detected sample holder type %d, id %d", sht, shid)
            except Exception:
                logging.exception("Failed to find sample holder ID")
                shid, sht = 1, PHENOM_SH_TYPE_STANDARD # assume it's a standard holder

            if sht == PHENOM_SH_TYPE_OPTICAL:
                f.result() # to be sure referencing doesn't cancel the move

                # TODO: look in the config file if the sample holder is known, or needs
                # first-time calibration (and ask the user to continue), and otherwise
                # update the metadata
                calibconf = get_calib_conf()
                calib = calibconf.get_sh_calib(shid)
                if calib is None:
                    self._request_delphi_holder_calib(shid)
                    return # don't go further, as everything has been taken care of
                else:
                    # TODO: to be more precise on the stage rotation, we'll need to
                    # locate the top and bottom holes of the sample holder, using
                    # the SEM. So once the sample is fully loaded, new and more
                    # precise calibration will be set.
                    self._apply_delphi_holder_calib(*calib)

                # Reference the (optical) stage
                f = self._main_data.stage.reference({"x", "y"})
                # TODO: shall we also reference the optical focus? It'd be handy only
                # if the absolute position is used.
        except Exception:
            logging.exception("Failed to set calibration")

        # continue business as usual
        f.add_done_callback(self._start_overview_acquisition)

    @call_after
    def _request_delphi_holder_calib(self, shid):
        """
        Handle all the actions needed when no calibration data is available
        for a sample holder (eg, it's the first time it is inserted)
        When this method returns, the sample holder will have been ejected,
        independently of whether the calibration has worked or not.
        """
        logging.info("New sample holder %x inserted", shid)
        # TODO: tell the user we need to do calibration, and it needs to have
        # the special sample => Eject (=Cancel) or Calibrate (=Continue)
        # TODO: the Phenom backend seems to also require a code to allow to
        # insert a new sample holder => ask the user for it here, and send it
        # to the phenom backend? How? A special method on the chamber component?
        # Eventually, it needs to call RegisterSampleHolder.

        dlg = delphi.FirstCalibrationDialog(self._main_frame, register=True)
        val = dlg.ShowModal() # blocks
        regcode = dlg.registrationCode
        dlg.Destroy()

        if val == wx.ID_OK:
            logging.info("Calibration should ensue with code %s", regcode)
            # FIXME: actually run the calibration
            logging.error("Calibration not yet supported")
        else:
            logging.info("Calibration cancelled")

        # Eject the sample holder
        self._main_data.chamberState.value = CHAMBER_VENTING

    def _apply_delphi_holder_calib(self, htop, hbot, strans, sscale, srot, iscale, irot):
        """
        Configure/update the components according to the calibration
        htop (2 floats): position of the top hole (unused)
        hbot (2 floats): position of the bottom hole (unused)
        strans (2 floats): stage translation
        sscale (2 floats > 0): stage scaling
        srot (float): stage rotation (rad)
        iscale (2 floats > 0): image scaling
        irot (float): image rotation (rad)
        """
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
            self._main_data.ccd.updateMetadata({model.MD_ROTATION_COR: irot})

    def _start_overview_acquisition(self, unused=None):
        logging.debug("Starting overview acquisition")

        # Start autofocus, and the rest will be done asynchronously
        if self._main_data.overview_focus:
            # TODO: center the sample to the view
            # We are using the best accuracy possible: 0
            try:
                f = align.autofocus.AutoFocus(self._main_data.overview_ccd, None,
                                              self._main_data.overview_focus, 0)
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
        try:
            future.result()
            logging.debug("Overview focused")
        except Exception:
            logging.info("Auto-focus on overview failed")

        # now acquire one image
        try:
            ovs = self._get_overview_stream()

            ovs.image.subscribe(self._on_overview_image)
            # start acquisition
            ovs.should_update.value = True
            ovs.is_active.value = True
        except Exception:
            logging.exception("Failed to start overview image acquisition")
            self._main_data.chamber.moveAbs({"pressure": self._vacuum_pressure})

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

        if not self._main_data.chamberState.value in {CHAMBER_PUMPING, CHAMBER_VACUUM}:
            logging.warning("Receive an overview image while in state %s",
                            self._main_data.chamberState.value)
            return # don't ask for vacuum

        # move further to fully under vacuum (should do nothing if already there)
        self._main_data.chamber.moveAbs({"pressure": self._vacuum_pressure})


# TODO SparcStateController?
#     "spectrometer": ("specState", HardwareButtonController),
#     "angular": ("arState", HardwareButtonController),
