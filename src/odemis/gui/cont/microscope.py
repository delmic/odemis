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
import logging
from odemis.gui import model
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

            btn = getattr(main_frame, btn_prefix + btn_name, None)
            if not btn:
                continue

            va = getattr(tab_data, va_name, None)
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
        self.btn.SetToggle(state != model.STATE_OFF)

    def _btn_to_va(self):
        """ Return the hardware state associated with the current button toggle state """
        return model.STATE_ON if self.btn.GetToggle() else model.STATE_OFF


class ChamberButtonController(HardwareButtonController):
    """
    Controller that allows for the more complex state updates required by the chamber button
    """

    def __init__(self, btn_ctrl, va, main_data):
        """
        :type btn_ctrl: odemis.gui.comp.buttons.ImageTextToggleButton

        """
        super(ChamberButtonController, self).__init__(btn_ctrl, va, main_data)

        # Since there are various factors that determine what images will be used as button faces,
        # (so, not just the button state!) we will explicitly define them in this class.
        self.btn_faces = {}
        self._determine_button_faces(main_data.role)

        self.pressure_va = None
        self.main_data = main_data

        if main_data.chamber:
            if 'pressure' in getVAs(main_data.chamber):
                self.pressure_va = getattr(main_data.chamber, 'pressure')
                self.pressure_va.subscribe(self._update_label, init=True)
            else:
                # TODO: Increase button image size so the full 'CHAMBER' text will fit (also
                # slightly decrease the size of the 'eject' symbol.
                self.btn.SetLabel("CHAMBER")

    def _determine_button_faces(self, role):
        """ Determine what button faces to use depending on values found in main_data """

        if role == "secommini":
            self.btn_faces = {
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
            self.btn_faces = {
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

    def _va_to_btn(self, state):
        """ Change the button toggle state according to the given hardware state

        If the va indicates an 'ON' state, we subscribe to the pressure va and unsubscribe if it's
        off.

        """

        # When the chamber is pumping or venting, it's considered to be working
        if state in (model.CHAMBER_PUMPING, model.CHAMBER_VENTING):
            self.btn.SetBitmapLabel(self.btn_faces['working']['normal'])
            self.btn.SetBitmaps(
                bmp_h=self.btn_faces['working']['hover'],
                bmp_sel=self.btn_faces['working']['active']
            )
        elif state == model.CHAMBER_VACUUM:
            self.btn.SetBitmapLabel(self.btn_faces['vacuum']['normal'])
            self.btn.SetBitmaps(
                bmp_h=self.btn_faces['vacuum']['hover'],
                bmp_sel=self.btn_faces['vacuum']['active']
            )

            # In case the GUI is launched with the chamber pump turned on already, we need to
            # toggle the button by code.
            self.btn.SetToggle(True)
        else:
            self.btn.SetBitmapLabel(self.btn_faces['normal']['normal'])
            self.btn.SetBitmaps(
                bmp_h=self.btn_faces['normal']['hover'],
                bmp_sel=self.btn_faces['normal']['active']
            )

        # Set the tooltip
        if state == model.CHAMBER_PUMPING:
            self.btn.SetToolTipString("Pumping...")
        elif state == model.CHAMBER_VENTING:
            self.btn.SetToolTipString("Venting...")
        elif state == model.CHAMBER_VENTED:
            self.btn.SetToolTipString("Pump the chamber")
        elif state == model.CHAMBER_VACUUM:
            self.btn.SetToolTipString("Vent the chamber")

    def _btn_to_va(self):
        """ Return the hardware state associated with the current button toggle state

        When the button is pressed down (i.e. toggled), the chamber is expected to be pumping to
        create a vacuum. When the button is up (i.e. un-toggled), the chamber is expected to be
        venting.

        """

        if self.btn.GetToggle():
            return model.CHAMBER_PUMPING
        else:
            return model.CHAMBER_VENTING

    @call_after
    def _update_label(self, pressure_val):
        """ Set a formatted pressure value as the label of the button """

        str_value = units.readable_str(pressure_val, sig=1, unit=self.pressure_va.unit)
        if self.btn.Label != str_value:
            self.btn.Label = str_value
            self.btn.Refresh()


# GUI toggle button (suffix) name -> VA name
BTN_TO_VA = {
    "sem": ("emState", HardwareButtonController),
    "opt": ("opticalState", HardwareButtonController),
    "press": ("chamberState", ChamberButtonController),
    "spectrometer": ("specState", HardwareButtonController),
    "angular": ("arState", HardwareButtonController),
}
