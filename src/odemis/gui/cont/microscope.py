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
from odemis.gui import model
import odemis.util.units as units
from odemis.gui.util.widgets import VigilantAttributeConnector
import odemis.gui.img.data as imgdata
import logging
import wx


class MicroscopeStateController(object):
    """
    This controller controls the main microscope buttons (ON/OFF,
    Pause, vacuum...) and updates the model. To query/change the status of a
    specific component, use the main data model directly.

    """

    def __init__(self, tab_data, main_frame, btn_prefix):
        """ Binds the 'hardware' buttons to their appropriate
        Vigilant Attributes in the model.MainGUIData

        tab_data (MicroscopyGUIData): the data model of the tab
        main_frame: (wx.Frame): the main frame of the GUI
        btn_prefix (string): common prefix of the names of the buttons
        """

        # Look for which buttons actually exist, and which VAs exist. Bind the
        # fitting ones
        # self._callbacks = []
        self._btn_controllers = []

        for btn_name, (va_name, control_class) in BTN_TO_VA.items():
            try:
                btn = getattr(main_frame, btn_prefix + btn_name)
            except AttributeError:
                continue

            try:
                va = getattr(tab_data.main, va_name)
            except AttributeError:
                # This microscope is not available
                btn.Hide()
                continue

            logging.debug("Connecting button %s to %s", btn_name, va_name)

            btn_cont = control_class(btn, va, tab_data.main)

            self._btn_controllers.append(btn_cont)


        if not self._btn_controllers:
            logging.warning("No microscope button found in tab %s", btn_prefix)


class HardwareButtonController(object):
    """
    Default button controller that on handles ON and Off states
    """

    def __init__(self, btn_ctrl, va, _):
        self.btn = btn_ctrl
        self.vac = VigilantAttributeConnector(va, btn_ctrl, self._va_to_btn, self._btn_to_va,
                                              events=wx.EVT_BUTTON)

    def _va_to_btn(self, state):
        """ Change the button toggle state according to the given hardware state """
        self.btn.SetToggle(state != model.STATE_OFF)

    def _btn_to_va(self):
        """ Return the hardware state associated with the current button toggle state """
        return model.STATE_ON if self.btn.GetToggle() else model.STATE_OFF


class ChamberButtonController(HardwareButtonController):
    """ Controller that allows for the more complex state updates required by the chamber button """

    def __init__(self, btn_ctrl, va, main_data):
        """
        :type btn_ctrl: odemis.gui.comp.buttons.ImageTextToggleButton

        """

        super(ChamberButtonController, self).__init__(btn_ctrl, va, main_data)

        # TODO: grab this va from main_data
        # self.pressure_va = None  # This VA will indicate the current pressure in the chamber
        self.pressure_va = main_data.pressure


    def _va_to_btn(self, state):
        """ Change the button toggle state according to the given hardware state

        If the va indicates an 'ON' state, we subscribe to the pressure va and unsubscribe if it's
        off.
        """

        # Determine what the chamber is doing
        is_working = state in (model.CHAMBER_PUMPING, model.CHAMBER_VENTING)
        has_vacuum = state == model.CHAMBER_VACUUM

        # Set the appropriate images
        if is_working:
            self.btn.SetBitmapLabel(imgdata.btn_eject_orange.getBitmap())
            self.btn.SetBitmapHover(imgdata.btn_eject_orange_h.getBitmap())
            self.btn.SetBitmapSelected(imgdata.btn_eject_orange_a.getBitmap())

            self.pressure_va.subscribe(self._update_label, init=True)
        elif has_vacuum:
            self.btn.SetBitmapLabel(imgdata.btn_eject_green.getBitmap())
            self.btn.SetBitmapHover(imgdata.btn_eject_green_h.getBitmap())
            self.btn.SetBitmapSelected(imgdata.btn_eject_green_a.getBitmap())

            self.pressure_va.unsubscribe(self._update_label)
        else:
            self.btn.SetBitmapLabel(imgdata.btn_eject.getBitmap())
            self.btn.SetBitmapHover(imgdata.btn_eject_h.getBitmap())
            self.btn.SetBitmapSelected(imgdata.btn_eject_a.getBitmap())

            self.pressure_va.unsubscribe(self._update_label)

        self.btn.SetToggle(is_working or has_vacuum)

    def _btn_to_va(self):
        """ Return the hardware state associated with the current button toggle state """
        return model.STATE_ON if self.btn.GetToggle() else model.STATE_OFF

    def _update_label(self, value):
        """ Set a formatted pressure value as the label of the button"""
        str_value = units.readable_str(value, sig=3, unit=self.pressure_va.unit)
        self.btn.SetLabel(str_value)


# GUI toggle button (suffix) name -> VA name
BTN_TO_VA = {"sem": ("emState", HardwareButtonController),
             "opt": ("opticalState", HardwareButtonController),
             "spectrometer": ("specState", HardwareButtonController),
             "angular": ("arState", HardwareButtonController),
             "press": ("vacuum_state", ChamberButtonController),
             }
