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
from __future__ import division

import wx
from odemis.gui.win.dialog_xrc import xrcprogress_dialog
from odemis.gui.util.widgets import ProgessiveFutureConnector
import logging
from odemis.util import units
from odemis.gui.util import call_in_wx_main, ignore_dead
from odemis import model
from odemis.acq._futures import executeTask
from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, \
    RUNNING
import odemis.acq.align.delphi as aligndelphi
import threading
import time
import math
from odemis.acq import align

DELPHI_OPT_GOOD_FOCUS = 0.03826  # somehow possibly not too bad focus position

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
        self._started = False
        self.cancel_btn.Bind(wx.EVT_BUTTON, self.on_cancel)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.info_txt.SetLabel("Calibration of the sample holder in progress")
        self.Fit()
        # self.Layout()  # to put the gauge at the right place
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

    @call_in_wx_main
    def on_calib_done(self, future):
        """ Callback called when the calibration is finished (either successfully or cancelled) """
        # bind button back to direct closure
        self.cancel_btn.Bind(wx.EVT_BUTTON, self.on_close)
        try:
            htop, hbot, hfoc, strans, sscale, srot, iscale, irot, resa, resb, hfwa, spotshift = future.result(1)  # timeout is just for safety
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
        self._calibconf.set_sh_calib(self._shid, htop, hbot, hfoc, strans,
                                     sscale, srot, iscale, irot, resa, resb, hfwa,
                                     spotshift)

        self.update_calibration_time(0)
        self.time_txt.SetLabel("Calibration completed.")
        # As the action is complete, rename "Cancel" to "Close"
        self.cancel_btn.SetLabel("Close")

    @call_in_wx_main
    @ignore_dead
    def on_calib_update(self, future, past, left):
        """ Callback called when the calibration time is updated (either successfully or cancelled) """
        self.update_calibration_time(left)

def DelphiCalibration(main_data, overview_pressure, vacuum_pressure, vented_pressure,
                      first_insertion=True, known_first_hole_f=None, known_second_hole_f=None,
                      known_focus_f=None, known_offset_f=None, known_rotation_f=None,
                      known_scaling_f=None):
    """
    Wrapper for DoDelphiCalibration. It provides the ability to check the
    progress of the procedure.
    main_data (odemis.gui.model.MainGUIData)
    overview_pressure (float): NavCam pressure value
    vacuum_pressure (float): Pressure under vacuum
    vented_pressure (float): Pressure when vented
    first_insertion (Boolean): If True it is the first insertion of this sample
                                holder
    known_first_hole (tuple of floats): Hole coordinates found in the calibration file
    known_second_hole (tuple of floats): Hole coordinates found in the calibration file
    known_focus (float): Focus used for hole detection #m
    known_offset (tuple of floats): Offset of sample holder found in the calibration file #m,m
    known_rotation (float): Rotation of sample holder found in the calibration file #radians
    known_scaling (tuple of floats): Scaling of sample holder found in the calibration file
    returns (ProgressiveFuture): Progress DoDelphiCalibration
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateDelphiCalibration())
    f._delphi_calib_state = RUNNING

    # Task to run
    f.task_canceller = _CancelDelphiCalibration
    f._delphi_calib_lock = threading.Lock()

    # Run in separate thread
    delphi_calib_thread = threading.Thread(target=executeTask,
                  name="Delphi Calibration",
                  args=(f, _DoDelphiCalibration, f, main_data, overview_pressure,
                        vacuum_pressure, vented_pressure, first_insertion,
                        known_first_hole_f, known_second_hole_f, known_focus_f,
                        known_offset_f, known_rotation_f, known_scaling_f))

    delphi_calib_thread.start()
    return f

def _DoDelphiCalibration(future, main_data, overview_pressure, vacuum_pressure,
                         vented_pressure, first_insertion=True, known_first_hole_f=None,
                         known_second_hole_f=None, known_focus_f=None, known_offset_f=None,
                         known_rotation_f=None, known_scaling_f=None):
    """
    It performs all the calibration steps for Delphi including the lens alignment,
    the conversion metadata update and the fine alignment.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    main_data (odemis.gui.model.MainGUIData)
    overview_pressure (float): NavCam pressure value
    vacuum_pressure (float): Pressure under vacuum
    vented_pressure (float): Pressure when vented
    first_insertion (Boolean): If True it is the first insertion of this sample
                                holder
    known_first_hole (tuple of floats): Hole coordinates found in the calibration file
    known_second_hole (tuple of floats): Hole coordinates found in the calibration file
    known_focus (float): Focus used for hole detection #m
    known_offset (tuple of floats): Offset of sample holder found in the calibration file #m,m
    known_rotation (float): Rotation of sample holder found in the calibration file #radians
    known_scaling (tuple of floats): Scaling of sample holder found in the calibration file
    returns (tuple of floats): Hole top
            (tuple of floats): Hole bottom
            (float): Focus used for hole detection
            (tuple of floats): Stage translation
            (tuple of floats): Stage scale
            (float): Stage rotation
            (tuple of floats): Image scale
            (float): Image rotation
            (tuple of floats): Resolution-related shift slope
            (tuple of floats): Resolution-related shift intercept
            (tuple of floats): HFW-related shift slope
            (tuple of floats): Spot shift percentage
    raises:
        CancelledError() if cancelled
    """
    logging.debug("Delphi calibration...")

    try:
        if future._delphi_calib_state == CANCELLED:
            raise CancelledError()

        try:
            # We need access to the separate sem and optical stages, which form
            # the "stage". They are not found in the model, but we can find them
            # as children of stage (on the DELPHI), and distinguish them by
            # their role.
            sem_stage = None
            opt_stage = None
            logging.debug("Find SEM and optical stages...")
            for c in main_data.stage.children.value:
                if c.role == "sem-stage":
                    sem_stage = c
                elif c.role == "align":
                    opt_stage = c

            if not sem_stage or not opt_stage:
                raise KeyError("Failed to find SEM and optical stages")

            # Initial calibration
            if first_insertion == True:
                # Move to the overview position first
                f = main_data.chamber.moveAbs({"pressure": overview_pressure})
                f.result()

                # Clear all the previous calibration
                logging.debug("Clear all the previous calibration...")
                main_data.stage.updateMetadata({
                          model.MD_POS_COR: (0, 0),
                          model.MD_PIXEL_SIZE_COR: (1, 1),
                          model.MD_ROTATION_COR: 0
                          })

                # Reference the (optical) stage
                logging.debug("Reference the (optical) stage...")
                f = opt_stage.reference({"x", "y"})
                f.result()

                logging.debug("Reference the focus...")
                f = main_data.focus.reference({"z"})
                f.result()

                # SEM stage to (0,0)
                logging.debug("Move to the center of SEM stage...")
                f = sem_stage.moveAbs({"x":0, "y":0})
                f.result()

                # Calculate offset approximation
                try:
                    logging.debug("Starting lens alignment...")
                    f = aligndelphi.LensAlignment(main_data.overview_ccd, sem_stage)
                    position = f.result()
                    logging.debug("SEM position after lens alignment: %s", position)
                except Exception:
                    raise IOError("Lens alignment failed.")

                # Update progress of the future
                future.set_end_time(time.time() + 14 * 60)

                # Just to check if move makes sense
                f = sem_stage.moveAbs({"x": position[0], "y": position[1]})
                f.result()

                # Move to SEM
                f = main_data.chamber.moveAbs({"pressure": vacuum_pressure})
                f.result()

                # Update progress of the future
                logging.debug("Try to update the remaining time...")
                future.set_end_time(time.time() + 12.5 * 60)

                # Compute stage calibration values
                try:
                    logging.debug("Starting conversion update...")
                    f = aligndelphi.UpdateConversion(main_data.ccd,
                                                     main_data.bsd,
                                                     main_data.ebeam,
                                                     sem_stage, opt_stage,
                                                     main_data.ebeam_focus,
                                                     main_data.focus,
                                                     main_data.stage,
                                                     first_insertion=True,
                                                     sem_position=position)
                    htop, hbot, hfoc, strans, srot, sscale, resa, resb, hfwa, spotshift = f.result()
                except Exception:
                    raise IOError("Conversion update failed.")
                # Update progress of the future
                logging.debug("Try to update the remaining time...")
                future.set_end_time(time.time() + 60)

                # Proper hfw for spot grid to be within the ccd fov
                main_data.ebeam.horizontalFoV.value = 80e-06

                # Run the optical fine alignment
                # TODO: reuse the exposure time
                f = align.FindOverlay((4, 4),
                                      0.5,  # s, dwell time
                                      10e-06,  # m, maximum difference allowed
                                      main_data.ebeam,
                                      main_data.ccd,
                                      main_data.bsd)
                trans_val, cor_md = f.result()
                iscale = cor_md[model.MD_PIXEL_SIZE_COR]
                irot = -cor_md[model.MD_ROTATION_COR] % (2 * math.pi)
                return htop, hbot, hfoc, strans, sscale, srot, iscale, irot, resa, resb, hfwa, spotshift
            # Secondary calibration
            else:
                # Move to SEM
                f = main_data.chamber.moveAbs({"pressure": vacuum_pressure})
                f.result()

                # Compute stage calibration values
                try:
                    logging.debug("Starting conversion update...")
                    f = aligndelphi.UpdateConversion(main_data.ccd,
                                                     main_data.bsd,
                                                     main_data.ebeam,
                                                     sem_stage, opt_stage,
                                                     main_data.ebeam_focus,
                                                     main_data.focus,
                                                     main_data.stage,
                                                     first_insertion=False,
                                                     known_first_hole=known_first_hole_f,
                                                     known_second_hole=known_second_hole_f,
                                                     known_focus=known_focus_f,
                                                     known_offset=known_offset_f,
                                                     known_rotation=known_rotation_f,
                                                     known_scaling=known_scaling_f)
                    f.result()
                except Exception:
                    raise IOError("Conversion update failed.")

                # We don't really care about the values returned since the
                # update of the metadata has already been taken care of
                return None
        except Exception:
            raise IOError("Delphi calibration failed.")
    finally:
        with future._delphi_calib_lock:
            if future._delphi_calib_state == CANCELLED:
                raise CancelledError()
            future._delphi_calib_state = FINISHED

def _CancelDelphiCalibration(future):
    """
    Canceller of _DoDelphiCalibration task.
    """
    logging.debug("Cancelling Delphi calibration...")

    with future._delphi_calib_lock:
        if future._delphi_calib_state == FINISHED:
            return False
        future._delphi_calib_state = CANCELLED
        logging.debug("Delphi calibration cancelled.")

    return True

def estimateDelphiCalibration():
    """
    Estimates Delphi calibration procedure duration
    returns (float):  process estimated time #s
    """
    # Rough approximation
    return 15 * 60  # s
