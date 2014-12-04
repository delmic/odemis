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
from odemis.gui.win.dialog_xrc import xrcprogress_dialog
from odemis.gui.util.widgets import ProgessiveFutureConnector
from odemis.gui.cont.acquisition import DelphiCalibration
import logging
from odemis.util import units
from odemis.gui.util import call_after
from concurrent.futures._base import CancelledError
from odemis import model

# code based on the wxPython demo TestDialog class
class FirstCalibrationDialog(wx.Dialog):
    """
    Dialog to ask for confirmation before starting the calibration for a new
    sample holder. It also allows the user to type the registration code for
    the sample holder.
    """
    def __init__(self, parent, shid, register=True):
        """
        register (boolean): if True, will allow the user to enter the registration
         code. The value can be retrieved by reading .registrationCode
        """
        wx.Dialog.__init__(self, parent, wx.ID_ANY, size=(300, -1), title="New sample holder")

        # Little info message
        sizer = wx.BoxSizer(wx.VERTICAL)
        sz_label = self.CreateTextSizer(
            ("\n"
             "This sample holder (%016x) has not yet been calibrated\n"
             "for this microscope.\n"
             "\n"
             "In order to proceed to the calibration, ensure that the special\n"
             "calibration sample is placed on the holder and press Calibrate.\n"
             "Otherwise, press Eject.\n" % (shid,))
        )
        sizer.Add(sz_label, 0, wx.ALIGN_CENTRE | wx.ALL, 5)

        if register:
            box = wx.BoxSizer(wx.HORIZONTAL)

            label = wx.StaticText(self, -1, "Registration code:")
            box.Add(label, 0, wx.ALIGN_CENTRE | wx.ALL, 5)
            self.text = wx.TextCtrl(self, -1, "", size=(80, -1))
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
        if hasattr(self, "text"):
            return self.text.Value
        else:
            return None

class CalibrationProgressDialog(xrcprogress_dialog):
    """ Wrapper class responsible for the connection between delphi calibration
    future and the xrcprogress_dialog.
    """
    def __init__(self, parent, main_data, overview_pressure, vacuum_pressure, vented_pressure,
                 calibconf, shid):
        xrcprogress_dialog.__init__(self, parent)

        # ProgressiveFuture for the ongoing calibration
        self._calibconf = calibconf
        self._shid = shid
        self.calib_future = None
        self._calib_future_connector = None

        self.info_txt.SetLabel("Calibration of the sample holder in progress")
        self.cancel_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.gauge.Show()
        self.Layout()  # to put the gauge at the right place

        self.calib_future = DelphiCalibration(main_data, overview_pressure, vacuum_pressure,
                                              vented_pressure)
        self._calib_future_connector = ProgessiveFutureConnector(self.calib_future,
                                                                 self.gauge,
                                                                 self.time_txt)
        self.calib_future.add_done_callback(self.on_calib_done)
        self.calib_future.add_update_callback(self.on_calib_update)

    def update_calibration_time(self, time):
        txt = "Time remaining: {}"
        txt = txt.format(units.readable_time(time))

        self.time_txt.SetLabel(txt)

    def on_close(self, evt):
        """ Close event handler that executes various cleanup actions
        """
        if self.calib_future:
            msg = "Cancelling calibration due to closing the calibration window"
            logging.info(msg)
            self.calib_future.cancel()

        self.Destroy()

    def on_cancel(self, evt):
        """ Handle calibration cancel button click """
        if not self.calib_future:
            logging.warning("Tried to cancel calibration while it was not started")
            return

        logging.debug("Cancel button clicked, stopping calibration")
        self.calib_future.cancel()
        # all the rest will be handled by on_acquisition_done()

    @call_after
    def on_calib_done(self, future):
        """ Callback called when the calibration is finished (either successfully or cancelled) """
        # bind button back to direct closure
        self.cancel_btn.Bind(wx.EVT_BUTTON, self.on_close)
        try:
            htop, hbot, strans, sscale, srot, iscale, irot, resa, resb, hfwa, spotshift = future.result(1)  # timeout is just for safety
        except CancelledError:
            # hide progress bar (+ put pack estimated time)
            self.update_calibration_time(0)
            self.time_txt.SetLabel("Calibration cancelled.")
            self.cancel_btn.SetLabel("Close")
            self.gauge.Hide()
            self.Layout()
            return
        except Exception:
            # We cannot do much: just warn the user and pretend it was cancelled
            self.calib_future.cancel()
            self.update_calibration_time(0)
            self.time_txt.SetLabel("Calibration failed.")
            self.cancel_btn.SetLabel("Close")
            # leave the gauge, to give a hint on what went wrong.
            return

        # Update the calibration file
        self._calibconf.set_sh_calib(self._shid, htop, hbot, strans, sscale, srot, iscale, irot,
                                     resa, resb, hfwa, spotshift)

        self.update_calibration_time(0)
        self.time_txt.SetLabel("Calibration completed.")
        # As the action is complete, rename "Cancel" to "Close"
        self.cancel_btn.SetLabel("Close")

    def on_calib_update(self, future, past, left):
        """ Callback called when the calibration time is updated (either successfully or cancelled) """
        self.update_calibration_time(left)
