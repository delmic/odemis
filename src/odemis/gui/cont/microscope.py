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
import odemis.gui.instrmodel as instrmodel
import wx

class MicroscopeController(object):
    """ This controller class controls the main microscope buttons and updates
    the model. To query/change the status of a special microscope, use the
    model.
    """

    bound = False
    callbacks = []

    def __init__(self, microscope_model, main_frame):
        """
        interface_model: MicroscopeModel
        main_frame: (wx.Frame): the frame which contains the 4 viewports
        """
        self.microscope_model = microscope_model

        # Microscope buttons
        self.btn_pause = main_frame.btn_toggle_pause
        self.btn_pressure = main_frame.btn_toggle_press

    @classmethod
    def bind_buttons(cls, microscope_model, main_frame):
        if not cls.bound:
            # TODO: for the Sparc, if only one microscope: hide everything, as this
            # microscope should always be ON.

            # GUI toggle button -> VA name
            # We cannot directly associate the buttons with a VA, because they might
            # not exist!
            btn_to_va = {main_frame.btn_lens_toggle_sem: "emState",
                         main_frame.btn_lens_toggle_opt: "opticalState",
                         main_frame.btn_toggle_sem: "emState",
                         main_frame.btn_toggle_opt: "opticalState",
                         main_frame.btn_toggle_spectrometer: "specState",
                         main_frame.btn_toggle_angular: "arState",
                         }

            for btn, vaname in btn_to_va.items():
                try:
                    va = getattr(microscope_model, vaname)
                except AttributeError:
                    # This microscope is not available
                    btn.Hide()
                    # TODO: need to update layout?
                    continue

                def on_va(state, btn=btn):
                    btn.SetToggle(state == instrmodel.STATE_ON)

                cls.callbacks.append(on_va)
                va.subscribe(on_va)


                # Event handler
                def on_toggle(event, va=va, vaname=vaname):
                    msg = "{0} toggle button pressed for mic {1}".format(
                                vaname, id(microscope_model)
                    )
                    logging.warn(msg)
                    if event.isDown:
                        va.value = instrmodel.STATE_ON
                    else:
                        va.value = instrmodel.STATE_OFF

                # FIXME: special _bitmap_ toggle button doesn't seem to generate
                # EVT_TOGGLEBUTTON
                btn.Bind(wx.EVT_BUTTON, on_toggle)
                cls.bound = True
