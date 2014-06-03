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
from odemis.gui.util.widgets import VigilantAttributeConnector
import logging
import wx

# GUI toggle button (suffix) name -> VA name
btn_to_va = {"sem": "emState",
             "opt": "opticalState",
             "spectrometer": "specState",
             "angular": "arState",
             "press": "vacuum_state",  #TODO
             }
            # TODO: pause button

class MicroscopeStateController(object):
    """ This controller controls the main microscope buttons (ON/OFF,
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
        main_data = tab_data.main

        # Look for which buttons actually exist, and which VAs exist. Bind the
        # fitting ones
        self._callbacks = []
        self._va_connectors = []
        for btn_name, vaname in btn_to_va.items():
            try:
                btn = getattr(main_frame, btn_prefix + btn_name)
            except AttributeError:
                continue

            try:
                va = getattr(main_data, vaname)
            except AttributeError:
                # This microscope is not available
                btn.Hide()
                continue
            logging.debug("Connecting button %s to %s", btn_name, vaname)

            vac = VigilantAttributeConnector(va, btn,
                    lambda s, btn=btn: btn.SetToggle(s != model.STATE_OFF),
                    lambda btn=btn: model.STATE_ON if btn.GetToggle() else model.STATE_OFF,
                    events=wx.EVT_BUTTON)
            self._va_connectors.append(vac)

        if not self._va_connectors:
            logging.warning("No microscope button found in tab %s", btn_prefix)
