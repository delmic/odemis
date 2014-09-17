# -*- coding: utf-8 -*-
'''
Created on 28 Aug 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
import wx


# code based on the wxPython demo TestDialog class
class FirstCalibrationDialog(wx.Dialog):
    """
    Dialog to ask for confirmation before starting the calibration for a new
    sample holder. It also allows the user to type the registration code for
    the sample holder.
    """
    def __init__(self, parent, register=True):
        """
        register (boolean): if True, will allow the user to enter the registration
         code. The value can be retrieved by reading .registrationCode
        """
        wx.Dialog.__init__(self, parent, wx.ID_ANY, size=(300, -1), title="New sample holder")

        # Little info message
        sizer = wx.BoxSizer(wx.VERTICAL)
        sz_label = self.CreateTextSizer(
            ("\n"
             "This sample holder has not yet been calibrated for this microscope.\n"
             "\n"
             "In order to proceed to the calibration, ensure that the special \n"
             "calibration sample is placed on the holder and press Calibrate.\n"
             "Otherwise, press Eject.\n")
        )
        sizer.Add(sz_label, 0, wx.ALIGN_CENTRE | wx.ALL, 5)

        # always put .text, for .registrationCode to always work
        self.text = wx.TextCtrl(self, -1, "", size=(80, -1))
        if register:
            box = wx.BoxSizer(wx.HORIZONTAL)

            label = wx.StaticText(self, -1, "Registration code:")
            box.Add(label, 0, wx.ALIGN_CENTRE | wx.ALL, 5)
            self.text.SetToolTipString("Enter the registration code for the sample holder "
                                       "provided by Phenom World for your DELPHI.")
            box.Add(self.text, 1, wx.ALIGN_CENTRE | wx.ALL, 5)

            sizer.Add(box, 0, wx.GROW | wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)

        # Add the buttons
        btnsizer = wx.StdDialogButtonSizer()

        # TODO: get some nice icons with the buttons?
        btn = wx.Button(self, wx.ID_OK, label="Calibrate")
        btn.SetDefault()
        btnsizer.AddButton(btn)

        btn = wx.Button(self, wx.ID_CANCEL, label="Eject")
        btnsizer.AddButton(btn)
        btnsizer.Realize()

        sizer.Add(btnsizer, 0, wx.ALIGN_RIGHT | wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)

        self.SetSizer(sizer)
        sizer.Fit(self)

        self.CentreOnParent()

    @property
    def registrationCode(self):
        return self.text.Value
