# -*- coding: utf-8 -*-
"""
Created on 28 Aug 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.

"""

from concurrent.futures._base import CancelledError
import logging
from odemis.acq.align.delphi import DelphiCalibration
from odemis.gui import model
from odemis.gui.conf import get_calib_conf
from odemis.gui.util import call_in_wx_main
from odemis.gui.util.widgets import ProgressiveFutureConnector
from odemis.gui.win.dialog_xrc import xrcprogress_dialog
import subprocess
import sys
import threading
import wx


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
            self.text.SetToolTip("Enter the registration code for the sample holder "
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


class RecalibrationDialog(wx.MessageDialog):
    """ Dialog that guides user through re-calibration of the Delhi

    The three default buttons that are defined are:

    wx.ID_YES - Automatic recalibration
    wx.ID_NO - Manual recalibration
    wx.ID_CANCEL - Cancel recalibration

    """

    def __init__(self, parent):
        super(RecalibrationDialog, self).__init__(
            parent,
            style=wx.YES_NO | wx.CANCEL | wx.YES_DEFAULT,
            message="Select the type of calibration to perform.",
            caption="Recalibrate sample holder")
        self.SetExtendedMessage(
                "Recalibration of the sample holder is generally "
                "only needed after the Delphi has been physically moved.\n"
                "Always make sure that an empty glass sample is present.\n"
                "\n"
                "Automatic calibration attempts to run all the "
                "calibration automatically and takes about 15 minutes.\n"
                "\n"
                "Manual calibration allows you to select which part of the "
                "calibration to re-run and to assist it. It can take up to 45 minutes.")

        self.EnableLayoutAdaptation(True)
        self.SetYesNoLabels("&Automatic", "&Manual")


class CalibrationProgressDialog(xrcprogress_dialog):
    """ Wrapper class responsible for the connection between delphi calibration
    future and the xrcprogress_dialog.
    """
    def __init__(self, parent, main_data, shid):
        xrcprogress_dialog.__init__(self, parent)

        # ProgressiveFuture for the ongoing calibration
        self._main_data = main_data
        self._shid = shid
        self.calib_future = None
        self._calib_future_connector = None
        self._started = False
        self.cancel_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.info_txt.SetLabel("Calibration of the sample holder in progress")
        self.calib_future = DelphiCalibration(main_data)
        self._calib_future_connector = ProgressiveFutureConnector(self.calib_future,
                                                                  self.gauge,
                                                                  self.time_txt)
        self.calib_future.add_done_callback(self.on_calib_done)
        self.Fit()

    def Fit(self):
        # On a wxFrame, it typically does nothing => adjust to panel
        child = self.Children[0]
        child.Layout()
        child.Fit()
        self.ClientSize = child.BestSize

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

    def on_run_manual(self, evt):
        """
        Handle the "Run" manual calibration button
        """
        ManualCalibration()
        self.Destroy()

    @call_in_wx_main
    def on_calib_done(self, future):
        """ Callback called when the calibration is finished (either successfully or cancelled) """
        # bind button back to direct closure
        self.info_txt.SetLabel("Calibration of the sample holder ended")
        self.cancel_btn.Bind(wx.EVT_BUTTON, self.on_close)

        # Eject the sample holder
        self._main_data.chamberState.value = model.CHAMBER_VENTING

        try:
            shcalib = future.result(1)  # timeout is just for safety
        except CancelledError:
            # hide progress bar (+ put pack estimated time)
            self.time_txt.SetLabel("Calibration cancelled.")
            self.cancel_btn.SetLabel("Close")
            self.gauge.Hide()
            self.Fit()
            return
        except Exception as e:
            # Suggest to the user to run the semi-manual calibration
            self.calib_future.cancel()
            self.time_txt.SetLabel("Automatic calibration failed:\n"
                                   "%s\n\n"
                                   "Please follow the manual calibration procedure. \n"
                                   "Press Run to start." % (e,))
            self.cancel_btn.SetLabel("Run")
            self.cancel_btn.Bind(wx.EVT_BUTTON, self.on_run_manual)
            self.Fit()
            return

        # Update the calibration file
        calibconf = get_calib_conf()
        calibconf.set_sh_calib(self._shid, *shcalib)

        # self.update_calibration_time(0)
        self.time_txt.SetLabel("Calibration completed.")
        # As the action is complete, rename "Cancel" to "Close"
        self.cancel_btn.SetLabel("Close")


def ManualCalibration():
    """
    Run the manual calibration (in a separate thread so that the GUI is still
    accessible)
    """
    threading.Thread(target=_threadManualCalib).start()


def _threadManualCalib():
    logging.info("Starting manual calibration for sample holder")
    ret = subprocess.call(["gnome-terminal", "-e", sys.executable + " -m odemis.acq.align.delphi_man_calib"])
    if ret != 0:
        logging.error("Manual calibration returned %d", ret)
