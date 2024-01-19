# -*- coding: utf-8 -*-

"""
@author: Rinze de Laat, Éric Piel, Philip Winkler, Victoria Mavrikopoulou,
         Anders Muskens, Bassim Lazem, Karishma Kumar

Copyright © 2012-2022 Rinze de Laat, Éric Piel, Delmic

Handles the switch of the content of the main GUI tabs.

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

import collections
from concurrent.futures import CancelledError
import logging
import pkg_resources
import wx

from odemis.gui.comp.overlay.cryo_feature import CryoFeatureOverlay
from odemis.model import BAND_PASS_THROUGH, InstantaneousFuture

from odemis import model
import odemis.acq.stream as acqstream
import odemis.gui.cont.streams as streamcont
import odemis.gui.cont.views as viewcont
import odemis.gui.model as guimod
import odemis.gui.util as guiutil
from odemis.acq.move import MILLING, IMAGING, FM_IMAGING, POSITION_NAMES
from odemis.acq.stream import OpticalStream
from odemis.gui.conf.data import get_local_vas
from odemis.gui.cont.tabs.tab import Tab
from odemis.gui.util import call_in_wx_main
from odemis.util import fluo
from odemis.util.driver import ATOL_LINEAR_POS


class MimasAlignTab(Tab):
    """
    Tab to perform the beam alignment of MIMAS.
    There are two alignments:
    * adjust the optical focus using the "align", while the stage Z is fixed.
    * adjust the X/Y of beam shift so that the optical lens and ion beam lens are centered
    """

    def __init__(self, name, button, panel, main_frame, main_data):
        tab_data = guimod.MicroscopyGUIData(main_data)
        super().__init__(name, button, panel, main_frame, tab_data)

        self._stage = main_data.stage
        self._focus = main_data.focus
        self._aligner = main_data.aligner
        self.panel = panel

        # Show the procedure steps
        doc_path = pkg_resources.resource_filename("odemis.gui", "doc/mimas_alignment.html")
        self.panel.html_alignment_doc.LoadPage(doc_path)

        # Connect the view (for now, only optical)
        vpv = collections.OrderedDict([
            (panel.vp_optical,
             {
                "name": "Optical",
                "stage": self._stage,
                "stream_classes": OpticalStream,
             }),
        ])

        self.view_controller = viewcont.ViewPortController(tab_data, panel, vpv)

        # Add a CryoFeatureOverlay to the canvas, not to show the features, but
        # to support moving the stage by double clicking, even if the stream is paused.
        # (So that the user can move the stage when looking at the FIB image in the separate computer)
        cnvs = panel.vp_optical.canvas
        cryofeature_overlay = CryoFeatureOverlay(cnvs, tab_data)
        cnvs.add_world_overlay(cryofeature_overlay)
        cryofeature_overlay.active.value = True

        # Add a sample overlay (if we have information)
        if main_data.sample_centers:
            panel.vp_optical.show_sample_overlay(main_data.sample_centers, main_data.sample_radius)

        # Create the Optical stream.
        # The focuser is "aligner" to calibrate the optical focus (while the stage Z stays constant)
        # It should typically show a widefield image, but we use a "FluoStream"
        # because that allows the user to still pick any light and filter in case it'd be useful.
        self._opt_stream = acqstream.FluoStream("Optical",
                                          main_data.ccd,
                                          main_data.ccd.data,
                                          main_data.light,
                                          main_data.light_filter,
                                          focuser=self._aligner,
                                          detvas=get_local_vas(main_data.ccd, main_data.hw_settings_config),
                                          )
        self._opt_stream.tint.value = (255, 255, 255)  # greyscale, as it's widefield
        # Select emission and excitation wavelengths to be widefield:
        # * pick the smallest excitation as default
        # * pick "pass-through" as default emission, and fallback to the smallest emission
        ex = min(self._opt_stream.excitation.choices, key=fluo.get_one_center)
        self._opt_stream.excitation.value = ex
        if BAND_PASS_THROUGH in self._opt_stream.emission.choices:
            em = BAND_PASS_THROUGH
        else:
            em = min(self._opt_stream.emission.choices, key=fluo.get_one_center)
            logging.info("No pass-through filter found, will use %s", em)
        self._opt_stream.emission.value = em

        # Create scheduler/stream bar controller
        self._streambar_controller = streamcont.StreamBarController(self.tab_data_model, panel.pnl_streams)

        self._opt_spe = self._streambar_controller.addStream(self._opt_stream)
        # remove the "remove" button and "eye" button
        self._opt_spe.stream_panel.show_visible_btn(False)
        self._opt_spe.stream_panel.show_remove_btn(False)

        # buttons icons of MIMAS alignment tab
        self.position_btns = {FM_IMAGING: panel.btn_position_opt,
                              MILLING: panel.btn_position_fib}
        panel.btn_position_opt.Bind(wx.EVT_BUTTON, self._set_flm_alignment_mode)
        panel.btn_position_fib.Bind(wx.EVT_BUTTON, self._set_fib_mode)
        # future to handle the move
        self._move_future = InstantaneousFuture()

        # Connect the "Reset Z alignment" button
        panel.btn_reset_alignment.Bind(wx.EVT_BUTTON, self._on_click_reset)

        # Disable the reset button when switching position
        main_data.is_acquiring.subscribe(self._on_acquisition, init=True)

        # Controls the stage movement based on the imaging mode
        self.posture_manager = main_data.posture_manager

    @call_in_wx_main
    def _on_acquisition(self, is_acquiring):
        """
        called when is_acquiring VA changes, to disallow pressing some buttons
        """
        # When acquiring, the tab is automatically disabled and should be left as-is
        self.panel.btn_reset_alignment.Enable(not is_acquiring)

        # When acquiring/milling, the tab is automatically disabled and should be left as-is
        # In particular, that's the state when moving between positions in the
        # Chamber tab, and the tab should wait for the move to be complete before
        # actually be enabled.
        if is_acquiring:
            self._stage.position.unsubscribe(self._on_stage_pos)
            self._aligner.position.unsubscribe(self._on_stage_pos)
        else:
            self._stage.position.subscribe(self._on_stage_pos)
            self._aligner.position.subscribe(self._on_stage_pos, init=True)

    def _on_click_reset(self, evt):
        """Reset the stage and align component, when the reset button is clicked."""
        # Note: it is blocking the GUI. However, the moves should be quite fast,
        # so it won't be blocking for long.
        # TODO: instead of blocking the GUI, disable the buttons, and after
        # starting the first move, add a "done_callback" to handle the moves in
        # the background.

        current_pos_label = self.posture_manager.getCurrentPostureLabel()

        if current_pos_label not in (FM_IMAGING, MILLING):
            logging.warning("Cannot reset Z alignment while current position is %s.",
                            POSITION_NAMES[current_pos_label])
            return

        # Move the Z stage back to the original position (same as the "focus"
        # position, but the metadata is actually on the stage)
        stage_pos = self._stage.getMetadata()[model.MD_FAV_POS_ACTIVE]
        f_stage = self._stage.moveAbs({"z": stage_pos["z"]})

        # Re-reference the optical lens. Typically it shouldn't be needed, but
        # it should always be safe and fast, and might add a tiny bit of move precision.
        f_aligner_ref = self._aligner.reference({"z"})

        # For the aligner, it depends on the state:
        # * in optical mode: move the aligner back to the default engage position (FAV_POS_ALIGN)
        # * in FIB mode: go back to FIB mode (because the referencing might have changed it)
        #  and update the engage position to the default position.
        align_md = self._aligner.getMetadata()
        align_pos = align_md[model.MD_FAV_POS_ALIGN]

        if current_pos_label == FM_IMAGING:
            f_aligner_mv = self._aligner.moveAbs(align_pos)
            self._aligner.updateMetadata({model.MD_FAV_POS_ACTIVE: align_pos})
        elif current_pos_label == MILLING:
            align_pos_deactive = align_md[model.MD_FAV_POS_DEACTIVE]
            f_aligner_mv = self._aligner.moveAbs(align_pos_deactive)
            self._aligner.updateMetadata({model.MD_FAV_POS_ACTIVE: align_pos})
        else:
            raise ValueError(f"Unexpected position {current_pos_label}")

        # Wait for all the moves to be completed
        f_stage.result()
        f_aligner_ref.result()
        f_aligner_mv.result()

    def _set_fib_mode(self, evt):
        """
        Sets the FIB mode with the retracted state of the objective and button visuals.
        """
        # Pause FLM stream
        self._streambar_controller.pauseStreams()

        self.tab_data_model.main.is_acquiring.value = True

        # Unpress the optical button and set the icon color of the pressed button to orange
        self.panel.btn_position_opt.SetValue(0)

        # create a future and update the appropriate controls after it is called
        self._move_future = self.posture_manager.cryoSwitchSamplePosition(MILLING)
        self._move_future.add_done_callback(self._on_pos_move_done)

    def _set_flm_alignment_mode(self, evt):
        """
        Sets the FLM alignment mode with the inserted position of the objective and button visuals.
        """
        self.tab_data_model.main.is_acquiring.value = True

        # Unpress the FIB button and set the icon color of the pressed button to orange
        self.panel.btn_position_fib.SetValue(0)

        # create a future and update the appropriate controls after it is called
        self._move_future = self.posture_manager.cryoSwitchSamplePosition(FM_IMAGING)
        self._move_future.add_done_callback(self._on_pos_move_done)

    @call_in_wx_main
    def _on_pos_move_done(self, future):
        """
        Done callback of any of the tab movements
        :param future: cancellable future of the move
        """
        try:
            future.result()
        except Exception as ex:
            # Something went wrong, don't go any further
            if not isinstance(ex, CancelledError):
                logging.warning("Failed to move aligner: %s", ex)

        self.tab_data_model.main.is_acquiring.value = False

        # Automatically activates the Optical stream at the end of a move
        current_pos_label = self.posture_manager.getCurrentPostureLabel()
        if current_pos_label == FM_IMAGING:
            self._opt_spe.stream.should_update.value = True

        # Make sure the button turns green
        self._update_movement_controls()

    @call_in_wx_main
    def _on_aligner_pos(self, pos):
        """
        Called every time the aligner moves, to update the state of the position buttons,
        and update the "engage" position (FAV_POS_ACTIVE).
        """
        # Don't update the buttons while the aligner is moving to a new position
        if not self._move_future.done():
            return

        self._update_movement_controls()

        # update ACTIVE POS iif stream is playing and not too close from the DEACTIVE
        if self._opt_stream.is_active.value:
            align_md = self._aligner.getMetadata()
            pos_deactive = align_md[model.MD_FAV_POS_DEACTIVE]
            try:
                if abs(pos_deactive["z"] - pos["z"]) < ATOL_LINEAR_POS:
                    logging.warning("Aligner focus near deactive position: %s vs %s", pos, pos_deactive)
                    return
            except KeyError:
                logging.warning("Aligner moved, but no Z position available")
                return

            logging.debug("Updating aligner engage position to %s", pos)
            self._aligner.updateMetadata({model.MD_FAV_POS_ACTIVE: pos})

    def _update_movement_controls(self):
        """
        Update the OPTICAL/FIB buttons according to the aligner position with enabling the pressed button and
        disabling other buttons.
        """
        # Check the status of objective/aligner
        current_pos_label = self.posture_manager.getCurrentPostureLabel()

        # turn green from orange icon when the position is reached
        # Keep the current button pressed and other buttons unpressed - toggle switch action
        currently_pressed = self.position_btns.get(current_pos_label)  # None if pos not supported

        for btn in self.position_btns.values():
            # Disable if in some odd position (and enable back otherwise)
            btn.Enable(current_pos_label in (FM_IMAGING, MILLING, IMAGING))

            if btn == currently_pressed:
                btn.SetValue(2)  # Completed
                btn.Refresh()
            else:
                # Sets the un-pressed buttons to orange, so that next time they are pressed, they start orange.
                btn.SetValue(0)

        # Only allow playing the optical stream when in optical mode
        if current_pos_label == FM_IMAGING:
            self._opt_spe.resume()
        else:
            self._opt_spe.pause()

    def Show(self, show=True):
        super().Show(show)

        # Pause streams when not displayed
        if show:
            # Update the buttons and the metadata when the aligner is moved
            self._aligner.position.subscribe(self._on_aligner_pos, init=True)
        else:
            self._streambar_controller.pauseStreams()
            self._aligner.position.unsubscribe(self._on_aligner_pos)

    def _on_stage_pos(self, _=None):
        """
        Called when the stage, or aligner are moved, enable the tab if position is imaging mode, disable otherwise
        """
        targets = (MILLING, FM_IMAGING, IMAGING)
        guiutil.enable_tab_on_stage_position(self.button,
                                             self.posture_manager,
                                             target=targets,
                                             tooltip="Alignment can only be performed in optical or FIB position")

    @classmethod
    def get_display_priority(cls, main_data):
        if main_data.role in ("mimas",):
            return 5
        else:
            return None
