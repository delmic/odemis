# -*- coding: utf-8 -*-
"""
@author: Rinze de Laat

Copyright © 2012-2013 Rinze de Laat, Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 2 of the License, or (at your option) any later
version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""
import logging
import odemis.gui.instrmodel as instrmodel
import wx

class MicroscopeController(object):
    """ This controller class controls the main microscope buttons and updates
    the model. To query/change the status of a special microscope, use the model.
    """
    def __init__(self, interface_model, main_frame):
        """
        interface_model: MicroscopeModel
        main_frame: (wx.Frame): the frame which contains the 4 viewports
        """
        self.interface_model = interface_model

        # Microscope buttons
        self.btn_pause = main_frame.btn_toggle_pause
        self.btn_pressure = main_frame.btn_toggle_press

        # TODO: for the Sparc, if only one microscope: hide everything, as this
        # microscope should always be ON.
        
        # GUI toggle button -> VA name 
        # cannot be directly the VA, because it might not exists 
        btn_to_va = {main_frame.btn_toggle_sem: "emState",
                     main_frame.btn_toggle_opt: "opticalState",
                     main_frame.btn_toggle_spectrometer: "specState",
                     main_frame.btn_toggle_angular: "arState",
                     }
        for btn, vaname in btn_to_va.items():
            try:
                va = getattr(interface_model, vaname)
            except AttributeError:
                # This microscope is not available
                btn.Hide()
                # TODO: need to update layout?
                continue
            
            # Event handler
            def on_toggle(event, va=va, vaname=vaname):
                logging.debug("%s toggle button pressed", vaname)
                if event.isDown:
                    va.value = instrmodel.STATE_ON
                else:
                    va.value = instrmodel.STATE_OFF
            # FIXME: special _bitmap_ toggle button doesn't seem to generate
            # EVT_TOGGLEBUTTON
            btn.Bind(wx.EVT_BUTTON, on_toggle)
                    
        # TODO: do something with pause and pressure
