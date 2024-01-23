# -*- coding: utf-8 -*-
"""
Created on 22 Aug 2012

@author: Éric Piel, Rinze de Laat, Philip Winkler

Copyright © 2012-2022 Éric Piel, Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.


### Purpose ###

This module contains classes to control the actions related to the acquisition
of microscope images.

"""

import logging
import math
from concurrent.futures._base import CancelledError

import wx

import odemis.gui.model as guimod
from odemis import model
from odemis.acq import align
from odemis.gui.comp import popup
from odemis.gui.comp.canvas import CAN_DRAG, CAN_FOCUS
from odemis.gui.util import call_in_wx_main
from odemis.gui.util.widgets import ProgressiveFutureConnector
from odemis.util import units


# TODO: merge with AutoCenterController because they share too many GUI elements
class FineAlignController(object):
    """
    Takes care of the fine alignment button and process on the SECOM lens
    alignment tab.
    Not an "acquisition" process per-se but actually very similar, the main
    difference being that the result is not saved as a file, but sent to the
    CCD (for calibration).

    Note: It needs the VA .fineAlignDwellTime on the main GUI data (contains
      the time to expose each spot to the ebeam).
    """

    # TODO: make the max diff dependant on the optical FoV?
    OVRL_MAX_DIFF = 10e-06  # m, don't be too picky
    OVRL_REPETITION = (4, 4)  # Not too many, to keep it fast

    def __init__(self, tab_data, tab_panel, main_frame):
        """
        tab_data (MicroscopyGUIData): the representation of the microscope GUI
        tab_panel: (wx.Panel): the tab that contains the viewports
        """
        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._tab_panel = tab_panel
        self._main_frame = main_frame
        self._sizer = self._tab_panel.pnl_align_tools.GetSizer()

        tab_panel.btn_fine_align.Bind(wx.EVT_BUTTON, self._on_fine_align)
        self._fa_btn_label = self._tab_panel.btn_fine_align.Label
        self._acq_future = None
        self._faf_connector = None

        # Make sure to reset the correction metadata if lens move
        self._main_data_model.aligner.position.subscribe(self._on_aligner_pos)

        self._main_data_model.fineAlignDwellTime.subscribe(self._on_dwell_time)
        self._tab_data_model.tool.subscribe(self._onTool, init=True)

    @call_in_wx_main
    def _onTool(self, tool):
        """
        Called when the tool (mode) is changed
        """
        # Don't enable during "acquisition", as we don't want to allow fine
        # alignment during auto centering. When button is "cancel", the tool
        # doesn't change, so it's never disabled.
        acquiring = self._main_data_model.is_acquiring.value
        # Only allow fine alignment when spot mode is on (so that the exposure
        # time has /some chances/ to represent the needed dwell time)
        spot = (tool == guimod.TOOL_SPOT)

        self._tab_panel.btn_fine_align.Enable(spot and not acquiring)
        self._update_est_time()

    def _on_dwell_time(self, dt):
        self._update_est_time()

    @call_in_wx_main
    def _update_est_time(self):
        """
        Compute and displays the estimated time for the fine alignment
        """
        if self._tab_data_model.tool.value == guimod.TOOL_SPOT:
            dt = self._main_data_model.fineAlignDwellTime.value
            t = align.find_overlay.estimateOverlayTime(dt, self.OVRL_REPETITION)
            t = math.ceil(t)  # round a bit pessimistic
            txt = u"~ %s" % units.readable_time(t, full=False)
        else:
            txt = u""
        self._tab_panel.lbl_fine_align.Label = txt

    def _on_aligner_pos(self, pos):
        """
        Called when the position of the lens is changed
        """
        # This means that the translation correction information from fine
        # alignment is not correct anymore, so reset it.
        self._tab_data_model.main.ccd.updateMetadata({model.MD_POS_COR: (0, 0)})

        # The main goal is to remove the "Successful" text if it there
        self._update_est_time()

    def _pause(self):
        """
        Pause the settings and the streams of the GUI
        """
        self._tab_panel.lens_align_tb.enable(False)
        self._tab_panel.btn_auto_center.Enable(False)

        # make sure to not disturb the acquisition
        for s in self._tab_data_model.streams.value:
            s.is_active.value = False

        # Prevent moving the stages
        for c in [self._tab_panel.vp_align_ccd.canvas,
                  self._tab_panel.vp_align_sem.canvas]:
            c.abilities -= {CAN_DRAG, CAN_FOCUS}

    def _resume(self):
        self._tab_panel.lens_align_tb.enable(True)
        self._tab_panel.btn_auto_center.Enable(True)

        # Restart the streams (which were being played)
        for s in self._tab_data_model.streams.value:
            s.is_active.value = s.should_update.value

        # Allow moving the stages
        for c in [self._tab_panel.vp_align_ccd.canvas,
                  self._tab_panel.vp_align_sem.canvas]:
            c.abilities |= {CAN_DRAG, CAN_FOCUS}

    def _on_fine_align(self, event):
        """
        Called when the "Fine alignment" button is clicked
        """
        self._pause()
        main_data = self._main_data_model
        main_data.is_acquiring.value = True

        logging.debug("Starting overlay procedure")
        f = align.FindOverlay(
            self.OVRL_REPETITION,
            main_data.fineAlignDwellTime.value,
            self.OVRL_MAX_DIFF,
            main_data.ebeam,
            main_data.ccd,
            main_data.sed,
            skew=True
        )
        logging.debug("Overlay procedure is running...")
        self._acq_future = f
        # Transform Fine alignment button into cancel
        self._tab_panel.btn_fine_align.Bind(wx.EVT_BUTTON, self._on_cancel)
        self._tab_panel.btn_fine_align.Label = "Cancel"

        # Set up progress bar
        self._tab_panel.lbl_fine_align.Hide()
        self._tab_panel.gauge_fine_align.Show()
        self._sizer.Layout()
        self._faf_connector = ProgressiveFutureConnector(f, self._tab_panel.gauge_fine_align)

        f.add_done_callback(self._on_fa_done)

    def _on_cancel(self, _):
        """
        Called during acquisition when pressing the cancel button
        """
        if not self._acq_future:
            msg = "Tried to cancel acquisition while it was not started"
            logging.warning(msg)
            return

        self._acq_future.cancel()
        # self._main_data_model.is_acquiring.value = False
        # all the rest will be handled by _on_fa_done()

    @call_in_wx_main
    def _on_fa_done(self, future):
        logging.debug("End of overlay procedure")
        main_data = self._main_data_model
        self._acq_future = None  # To avoid holding the ref in memory
        self._faf_connector = None

        try:
            trans_val, cor_md = future.result()
            opt_md, sem_md = cor_md

            # Save the optical correction metadata straight into the CCD
            main_data.ccd.updateMetadata(opt_md)

            # The SEM correction metadata goes to the ebeam
            main_data.ebeam.updateMetadata(sem_md)
        except CancelledError:
            self._tab_panel.lbl_fine_align.Label = "Cancelled"
        except Exception as ex:
            logging.warning("Failure during overlay: %s", ex)
            self._tab_panel.lbl_fine_align.Label = "Failed"
        else:
            self._main_frame.menu_item_reset_finealign.Enable(True)

            # Check whether the values make sense. If not, we still accept them,
            # but hopefully make it clear enough to the user that the calibration
            # should not be trusted.
            rot = opt_md.get(model.MD_ROTATION_COR, 0)
            rot0 = (rot + math.pi) % (2 * math.pi) - math.pi  # between -pi and pi
            rot_deg = math.degrees(rot0)
            opt_scale = opt_md.get(model.MD_PIXEL_SIZE_COR, (1, 1))[0]
            shear = sem_md.get(model.MD_SHEAR_COR, 0)
            scaling_xy = sem_md.get(model.MD_PIXEL_SIZE_COR, (1, 1))
            if (not abs(rot_deg) < 10 or  # Rotation < 10°
                not 0.9 < opt_scale < 1.1 or  # Optical mag < 10%
                not abs(shear) < 0.3 or  # Shear < 30%
                any(not 0.9 < v < 1.1 for v in scaling_xy)  # SEM ratio diff < 10%
               ):
                # Special warning in case of wrong magnification
                if not 0.9 < opt_scale < 1.1 and model.hasVA(main_data.lens, "magnification"):
                    lens_mag = main_data.lens.magnification.value
                    measured_mag = lens_mag / opt_scale
                    logging.warning("The measured optical magnification is %fx, instead of expected %fx. "
                                    "Check that the lens magnification and the SEM magnification are correctly set.",
                                    measured_mag, lens_mag)
                else:  # Generic warning
                    logging.warning(
                        u"The fine alignment values are very large, try on a different place on the sample. "
                        u"mag correction: %f, rotation: %f°, shear: %f, X/Y scale: %f/%f",
                        opt_scale, rot_deg, shear, scaling_xy[0], scaling_xy[1])

                title = "Fine alignment probably incorrect"
                lvl = logging.WARNING
                self._tab_panel.lbl_fine_align.Label = "Probably incorrect"
            else:
                title = "Fine alignment successful"
                lvl = logging.INFO
                self._tab_panel.lbl_fine_align.Label = "Successful"

            # Rotation is compensated in software on the FM image, but the user
            # can also change the SEM scan rotation, and re-run the alignment,
            # so show it clearly, for the user to take action.
            # The worse the rotation, the longer it's displayed.
            timeout = max(2, min(abs(rot_deg), 10))
            popup.show_message(
                self._tab_panel,
                title,
                u"Rotation: %s\nShear: %s\nX/Y Scaling: %s"
                % (units.readable_str(rot_deg, unit=u"°", sig=3),
                   units.readable_str(shear, sig=3),
                   units.readable_str(scaling_xy, sig=3)),
                timeout=timeout,
                level=lvl
            )
            logging.info(u"Fine alignment computed mag correction of %f, rotation of %f°, "
                         u"shear needed of %s, and X/Y scaling needed of %f/%f.",
                         opt_scale, rot, shear, scaling_xy[0], scaling_xy[1])

        # As the CCD image might have different pixel size, force to fit
        self._tab_panel.vp_align_ccd.canvas.fit_view_to_next_image = True

        main_data.is_acquiring.value = False
        self._tab_panel.btn_fine_align.Bind(wx.EVT_BUTTON, self._on_fine_align)
        self._tab_panel.btn_fine_align.Label = self._fa_btn_label
        self._resume()

        self._tab_panel.lbl_fine_align.Show()
        self._tab_panel.gauge_fine_align.Hide()
        self._sizer.Layout()
