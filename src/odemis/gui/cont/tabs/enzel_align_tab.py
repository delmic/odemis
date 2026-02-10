# -*- coding: utf-8 -*-

"""
@author: Rinze de Laat, Éric Piel, Philip Winkler, Victoria Mavrikopoulou,
         Anders Muskens, Bassim Lazem, Stefan Sneep

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

from odemis.gui.comp.overlay.centered_line import (HORIZONTAL_LINE, CROSSHAIR,
                                                   CenteredLineOverlay)
from odemis.gui.comp.popup import show_message
from odemis.gui.util.wx_adapter import fix_static_text_clipping

from odemis import model
import odemis.acq.stream as acqstream
from odemis.gui.cont.stream_bar import EnzelAlignmentStreamsBarController
import odemis.gui.cont.views as viewcont
import odemis.gui.model as guimod
import odemis.gui.util as guiutil
from odemis.acq.move import ALIGNMENT, THREE_BEAMS
from odemis.gui.conf.data import get_local_vas
from odemis.gui.cont.stream import StreamController
from odemis.gui.cont.tabs.tab import Tab
from odemis.gui.util import call_in_wx_main
from odemis.gui.util.widgets import VigilantAttributeConnector


class EnzelAlignTab(Tab):
    """
    Tab to perform the 3 beam alignment of Enzel so that the FIB, SEM and FLM look at the same point.
    """
    def __init__(self, name, button, panel, main_frame, main_data):
        tab_data = guimod.EnzelAlignGUIData(main_data)
        super(EnzelAlignTab, self).__init__(name, button, panel, main_frame, tab_data)
        self.set_label("ALIGNMENT")
        self._stream_controllers = []
        self._stage = main_data.stage
        self._stage_global = main_data.stage_global
        self._aligner = main_data.aligner


        # Controls the stage movement based on the imaging mode
        self.posture_manager = main_data.posture_manager

        # Check if the stage has FAV positions in its metadata
        for stage in (self._stage, self._aligner):
            if not {model.MD_FAV_POS_DEACTIVE, model.MD_FAV_POS_ACTIVE}.issubset(stage.getMetadata()):
                raise ValueError('The stage %s is missing FAV_POS_DEACTIVE and/or FAV_POS_ACTIVE metadata.' % stage)

        viewports = panel.pnl_two_streams_grid.viewports
        # Even though we have 3 viewports defined in main.xrc only 2 should be shown by default.
        for vp in viewports[2:]:
            vp.Shown = False

        vpv = collections.OrderedDict([
            (
                viewports[0],
                {
                    "name"          : "FIB image",
                    "cls"           : guimod.MicroscopeView,
                    "stream_classes": acqstream.FIBStream,
                }
            ),
            (
                viewports[1],
                {
                    "name"          : "SEM image",
                    "cls"           : guimod.MicroscopeView,
                    "stage"         : main_data.stage,
                    "stream_classes": acqstream.EMStream,
                },
            ),
            (
                viewports[2],
                {
                    "name"          : "Optical image",
                    "cls"           : guimod.MicroscopeView,
                    "stage"         : main_data.stage,
                    "stream_classes": acqstream.CameraStream,
                },
            ),
        ])
        self.view_controller = viewcont.ViewPortController(tab_data, panel, vpv)

        # Create FIB stream
        self._fib_stream = acqstream.FIBStream("FIB",
                                               main_data.ion_sed,
                                               main_data.ion_sed.data,
                                               main_data.ion_beam,
                                               forcemd={model.MD_POS: (0, 0)}, )
        self._fib_stream.single_frame_acquisition.value = True
        viewports[0].canvas.disable_drag()
        self.tab_data_model.streams.value.append(self._fib_stream)

        # Create SEM stream
        self._sem_stream = acqstream.SEMStream("SEM",
                                               main_data.sed,
                                               main_data.sed.data,
                                               main_data.ebeam,
                                               hwdetvas=get_local_vas(main_data.sed, main_data.hw_settings_config),
                                               hwemtvas=get_local_vas(main_data.ebeam, main_data.hw_settings_config),
                                               acq_type=model.MD_AT_EM,
                                               blanker=None
                                               )

        # Create the Optical FM stream  (no focuser added on purpose)
        self._opt_stream = acqstream.FluoStream("Optical",
                                                  main_data.ccd,
                                                  main_data.ccd.data,
                                                  main_data.light,
                                                  main_data.light_filter,
                                                  detvas=get_local_vas(main_data.ccd, main_data.hw_settings_config),
                                                  forcemd={model.MD_ROTATION: 0,
                                                           model.MD_SHEAR   : 0},
                                                  )
        self.tab_data_model.streams.value.append(self._opt_stream)

        self.tab_data_model.views.value[1].interpolate_content.value = True
        self.tab_data_model.streams.value.append(self._sem_stream)

        self._FIB_view_and_control = {"viewport": self.panel.pnl_two_streams_grid.viewports[0],
                                      "stream"  : self._fib_stream}
        self._SEM_view_and_control = {"viewport": self.panel.pnl_two_streams_grid.viewports[1],
                                      "stream"  : self._sem_stream}
        self._FLM_view_and_control = {"viewport": self.panel.pnl_two_streams_grid.viewports[2],
                                      "stream"  : self._opt_stream}

        # Create scheduler/stream bar controller
        self.stream_bar_controller = EnzelAlignmentStreamsBarController(self.tab_data_model)

        self._z_move_future = None  # Future attribute for the alignment stage moves
        self._flm_move_future = None  # Future attirbute for the alignment stage moves

        # Enable all controls but by default show none
        self.panel.pnl_z_align_controls.Enable(True)
        self.panel.pnl_sem_align_controls.Enable(True)
        self.panel.pnl_flm_align_controls.Enable(True)
        self.panel.pnl_z_align_controls.Show(False)
        self.panel.pnl_sem_align_controls.Show(False)
        self.panel.pnl_flm_align_controls.Show(False)

        # Create a dict matching the control buttons to functions with the appropriate action
        z_aligner_control_functions = {panel.stage_align_btn_m_aligner_z:
                                           lambda evt: self._z_alignment_controls("z", -1, evt),
                                       panel.stage_align_btn_p_aligner_z:
                                           lambda evt: self._z_alignment_controls("z", 1, evt),
                                       }
        sem_aligner_control_functions = {panel.beam_shift_btn_m_aligner_x:
                                             lambda evt: self._sem_alignment_controls("x", -1, evt),
                                         panel.beam_shift_btn_p_aligner_x:
                                             lambda evt: self._sem_alignment_controls("x", 1, evt),
                                         panel.beam_shift_btn_m_aligner_y:
                                             lambda evt: self._sem_alignment_controls("y", -1, evt),
                                         panel.beam_shift_btn_p_aligner_y:
                                             lambda evt: self._sem_alignment_controls("y", 1, evt),
                                         }
        flm_aligner_control_functions = {panel.flm_align_btn_m_aligner_x:
                                             lambda evt: self._flm_alignment_controls("x", -1, evt),
                                         panel.flm_align_btn_p_aligner_x:
                                             lambda evt: self._flm_alignment_controls("x", 1, evt),
                                         panel.flm_align_btn_m_aligner_y:
                                             lambda evt: self._flm_alignment_controls("y", -1, evt),
                                         panel.flm_align_btn_p_aligner_y:
                                             lambda evt: self._flm_alignment_controls("y", 1, evt),
                                         panel.flm_align_btn_m_aligner_z:
                                             lambda evt: self._flm_alignment_controls("z", -1, evt),
                                         panel.flm_align_btn_p_aligner_z:
                                             lambda evt: self._flm_alignment_controls("z", 1, evt),
                                         }

        self._combined_aligner_control_functions = {**z_aligner_control_functions,
                                                    **sem_aligner_control_functions,
                                                    **flm_aligner_control_functions}

        # Bind alignment control buttons to defined control functions
        for btn, function in self._combined_aligner_control_functions.items():
            btn.Bind(wx.EVT_BUTTON, function)

        # Vigilant attribute connectors for the slider
        self._step_size_controls_va_connector = VigilantAttributeConnector(tab_data.step_size,
                                                                           self.panel.controls_step_size_slider,
                                                                           events=wx.EVT_SCROLL_CHANGED)

        # Alignment modes with the corresponding buttons
        self._align_modes = {
            guimod.Z_ALIGN: panel.btn_align_z,
            guimod.SEM_ALIGN: panel.btn_align_sem,
            guimod.FLM_ALIGN: panel.btn_align_flm,
        }

        # Bind the align mode buttons
        for btn in self._align_modes.values():
            btn.Bind(wx.EVT_BUTTON, self._set_align_mode)

        self.tab_data_model.align_mode.subscribe(self._on_align_mode, init=True)

        # Bind the custom alignment button to set the latest alignment defined by the user
        panel.btn_custom_alignment.Bind(wx.EVT_BUTTON, self._on_click_custom_alignment)

        # Disable the tab when the stage is not at the right position
        main_data.is_acquiring.subscribe(self._on_acquisition, init=True)

    def terminate(self):
        super().terminate()
        self._stage.position.unsubscribe(self._on_stage_pos)
        # make sure the streams are stopped
        for s in self.tab_data_model.streams.value:
            s.is_active.value = False

    def _set_align_mode(self, evt):
        """
        Links the view to the model by setting the correct mode on the model whenever an alignment mode button is pressed.

        :param evt (GenButtonEvent): Clicked event
        """
        clicked_align_button = evt.GetEventObject()

        # Set the correct mode in the model
        for mode, button in self._align_modes.items():
            if clicked_align_button == button:
                self.tab_data_model.align_mode.value = mode
                return

    def _on_align_mode(self, align_mode):
        """
        Subscriber for the current alignment mode. (un)toggles the correct buttons and calls the setters of each mode.
        Which in their turn actually changes the mode with the corresponding streams to be displayed, the
        instructions and the controls.

        :param align_mode (string): Current mode (Z_ALIGN, SEM_ALIGN, FLM_ALIGN)
        """
        # Un/toggle all the buttons.
        for mode, btn in self._align_modes.items():
            btn.SetToggle(mode == align_mode)  # False for buttons != to the align_mode

        if align_mode == guimod.Z_ALIGN:
            self._set_z_alignment_mode()
        elif align_mode == guimod.SEM_ALIGN:
            self._set_sem_alignment_mode()
        elif align_mode == guimod.FLM_ALIGN:
            self._set_flm_alignment_mode()

    # Bind the buttons to the button move to custom alignment
    def _on_click_custom_alignment(self, evt):
        # First move the stage then the objective
        future_stage_move = self._stage.moveAbs(self._stage.getMetadata()[model.MD_FAV_POS_ACTIVE])

        def move_aligner(f):
            try:
                f.result()
            except CancelledError:
                logging.warning("Stage move cancelled")
                return
            except Exception as ex:
                logging.error("Stage move failed: %s", ex)
                return

            f2 = self._aligner.moveAbs(self._aligner.getMetadata()[model.MD_FAV_POS_ACTIVE])
            try:
                f2.result()
            except CancelledError:
                logging.warning("Aligner move cancelled")
                return
            except Exception as ex:
                logging.error("Aligner move failed: %s", ex)
                return

        future_stage_move.add_done_callback(move_aligner)

    def _set_z_alignment_mode(self):
        """
        Sets the Z alignment mode with the correct streams, stream controllers, instructions and alignment controls.
        """
        self.stream_bar_controller.pauseStreams()
        self._set_top_and_bottom_stream_and_settings(self._FIB_view_and_control,
                                                     self._SEM_view_and_control)

        # Adjust the overlay to a horizontal line in the FIB viewport
        for viewport in self.panel.pnl_two_streams_grid.viewports:
            if viewport.view.stream_classes is acqstream.FIBStream:
                for FIB_line_overlay in viewport.canvas.view_overlays:
                    if isinstance(FIB_line_overlay, CenteredLineOverlay):
                        FIB_line_overlay.shape = HORIZONTAL_LINE
                        break

        doc_path = pkg_resources.resource_filename("odemis.gui", "doc/enzel_z_alignment.html")
        self.panel.html_alignment_doc.LoadPage(doc_path)

        self.panel.controls_step_size_slider.SetRange(100e-9, 100e-6)
        self.panel.controls_step_size_slider.set_position_value(100e-9)

        self.panel.pnl_z_align_controls.Show(True)
        self.panel.pnl_sem_align_controls.Show(False)
        self.panel.pnl_flm_align_controls.Show(False)
        fix_static_text_clipping(self.panel)
        self.tab_data_model.align_mode.value = guimod.Z_ALIGN

    def _z_alignment_controls(self, axis, proportion, evt):
        """
        Links the Z alignment mode control buttons to the stage and updates the correct metadata.

        :param axis (str): axis which is controlled by the button.
        :param proportion (int): direction of the movement represented by the button (-1/1)
        :param evt (GenButtonEvent): Clicked event
        """
        # Only proceed if there is no currently running target_position
        if self._z_move_future and not self._z_move_future.done():
            logging.debug("The stage is still moving, this movement isn't performed.")
            return

        if self._z_move_future and not self._z_move_future.button.Enabled:
            logging.error("The stream is still updating and hence button activity is suppressed.")
            return
        evt.theButton.Enabled = False  # Disable the button to prevent queuing of movements and unnecessary refreshing.

        self._z_move_future = self._stage_global.moveRel(
                {axis: proportion * self.tab_data_model.step_size.value})
        self._z_move_future.button = evt.theButton
        self._z_move_future.add_done_callback(self._z_alignment_move_done)

    @call_in_wx_main
    def _z_alignment_move_done(self, future):
        self.stream_bar_controller.refreshStreams((self._fib_stream,
                                                   self._sem_stream))
        # Save the new position in the metadata
        self._stage.updateMetadata({model.MD_FAV_POS_ACTIVE: self._stage.position.value})
        future.button.Enabled = True

    def _set_sem_alignment_mode(self):
        """
        Sets the SEM alignment mode with the correct streams, stream controllers, instructions and alignment controls.
        """
        self.stream_bar_controller.pauseStreams()
        self._set_top_and_bottom_stream_and_settings(self._SEM_view_and_control,
                                                     self._FIB_view_and_control)

        # Adjust the overlay to a normal crosshair in the FIB viewport
        fib_viewport = self._FIB_view_and_control["viewport"]
        for overlay in fib_viewport.canvas.view_overlays:
            if isinstance(overlay, CenteredLineOverlay):
                overlay.shape = CROSSHAIR
                break

        doc_path = pkg_resources.resource_filename("odemis.gui", "doc/enzel_sem_alignment.html")
        self.panel.html_alignment_doc.LoadPage(doc_path)

        self.panel.controls_step_size_slider.SetRange(1e-6,
                                                      min(50e-6, abs(self._sem_stream.emitter.shift.range[1][0])))
        self.panel.controls_step_size_slider.set_position_value(10e-6)

        self.panel.pnl_z_align_controls.Show(False)
        self.panel.pnl_sem_align_controls.Show(True)
        self.panel.pnl_flm_align_controls.Show(False)
        fix_static_text_clipping(self.panel)
        self.tab_data_model.align_mode.value = guimod.SEM_ALIGN

    def _sem_alignment_controls(self, axis, proportion, evt):
        """
        Links the SEM alignments mode control buttons to the stage.

        :param axis (str): axis which is controlled by the button.
        :param proportion (int): direction of the movement represented by the button (-1/1)
        :param evt (GenButtonEvent): Clicked event
        """
        evt.theButton.Enabled = False
        # Beam shift control
        self._sem_move_beam_shift_rel({axis: proportion * self.tab_data_model.step_size.value})
        self.stream_bar_controller.refreshStreams((self._sem_stream,))
        evt.theButton.Enabled = True

    def _sem_move_beam_shift_rel(self, shift):
        """
        Provides relative control of the beam shift similar to the MoveRel functionality of the stages.

        :param shift (dict --> float): Relative movement of the beam shift with the axis as keys (x/y)
        """
        shiftVA = self._sem_stream.emitter.shift
        try:
            if "x" in shift:
                shiftVA.value = (shiftVA.value[0] + shift["x"], shiftVA.value[1])
        except IndexError:
            show_message(wx.GetApp().main_frame,
                         "Reached the limits of the beam shift, cannot move any further in x direction.",
                         timeout=3.0, level=logging.WARNING)
            logging.error("Reached the limits of the beam shift, cannot move any further in x direction.")

        try:
            if "y" in shift:
                shiftVA.value = (shiftVA.value[0], shiftVA.value[1] + shift["y"])
        except IndexError:
            show_message(wx.GetApp().main_frame,
                         "Reached the limits of the beam shift, cannot move any further in y direction.",
                         timeout=3.0, level=logging.WARNING)
            logging.error("Reached the limits of the beam shift, cannot move any further in y direction.")

        updated_stage_pos = self._stage.getMetadata()[model.MD_FAV_POS_ACTIVE]
        updated_stage_pos.update({"x": self._stage.position.value["x"], "y": self._stage.position.value["y"]})
        self._stage.updateMetadata({model.MD_FAV_POS_ACTIVE: updated_stage_pos})

    def _set_flm_alignment_mode(self):
        """
        Sets the FLM alignment mode with the correct streams, stream controllers, instructions and alignment controls.
        """
        self.stream_bar_controller.pauseStreams()
        self._set_top_and_bottom_stream_and_settings(self._FLM_view_and_control,
                                                     self._SEM_view_and_control)

        doc_path = pkg_resources.resource_filename("odemis.gui", "doc/enzel_flm_alignment.html")
        self.panel.html_alignment_doc.LoadPage(doc_path)

        self.panel.controls_step_size_slider.SetRange(100e-9, 50e-6)
        self.panel.controls_step_size_slider.set_position_value(10e-6)

        self.panel.pnl_z_align_controls.Show(False)
        self.panel.pnl_sem_align_controls.Show(False)
        self.panel.pnl_flm_align_controls.Show(True)
        fix_static_text_clipping(self.panel)

        self.tab_data_model.align_mode.value = guimod.FLM_ALIGN

    def _flm_alignment_controls(self, axis, proportion, evt):
        """
        Links the FLM alignments mode control buttons to the stage and updates the correct metadata..

        :param axis (str): axis which is controlled by the button.
        :param proportion (int): direction of the movement represented by the button (-1/1)
        :param evt (GenButtonEvent): Clicked event
        """
        # Only proceed if there is no currently running target_position
        if self._flm_move_future and not self._flm_move_future.done():
            logging.debug("The stage is still moving, this movement isn't performed.")
            return

        if self._flm_move_future and not self._flm_move_future.button.Enabled:
            logging.error("The stream is still updating and hence button activity is suppressed.")
            return

        evt.theButton.Enabled = False

        self._flm_move_future = self._aligner.moveRel({axis: proportion * self.tab_data_model.step_size.value})
        self._flm_move_future.button = evt.theButton
        self._flm_move_future.add_done_callback(self._flm_alignment_move_done)

    @call_in_wx_main
    def _flm_alignment_move_done(self, future):
        self._aligner.updateMetadata({model.MD_FAV_POS_ACTIVE: self._aligner.position.value})
        updated_stage_pos = self._stage.getMetadata()[model.MD_FAV_POS_ACTIVE]
        updated_stage_pos.update({"x": self._stage.position.value["x"], "y": self._stage.position.value["y"]})
        # Save the new position in the metadata
        self._stage.updateMetadata({model.MD_FAV_POS_ACTIVE: updated_stage_pos})
        future.button.Enabled = True

    @call_in_wx_main
    def _set_top_and_bottom_stream_and_settings(self, top, bottom):
        """
        Sets the top and bottom view and stream bar in a 2*1 viewport.

        :param top (dict): The top stream and corresponding viewport accessible via the keys 'stream'/'viewport'
        :param bottom(dict): The bottom stream and corresponding viewport accessible via the keys 'stream'/'viewport'
        """
        if top is bottom:
            raise ValueError("The bottom stream is equal to the top stream, this isn't allowed in a 2*1 mode.")

        # Pause all streams
        self.stream_bar_controller.pauseStreams()

        self.tab_data_model.visible_views.value = [top['viewport'].view, bottom['viewport'].view]

        # Destroy the old stream controllers  # TODO This is a temporary fix and it could be handled better
        for stream_controller in self._stream_controllers:
            stream_controller._on_stream_panel_destroy()

        # Keep a reference to the stream controllers so the garbage collector does not delete them.
        self._stream_controllers = []
        # Replace the settings of the top stream controller with the new StreamController
        for stream_settings in self.panel.top_settings.stream_panels:
            self.panel.top_settings.remove_stream_panel(stream_settings)
        new_top_stream_controller = StreamController(self.panel.top_settings,
                                                     top["stream"],
                                                     self.tab_data_model,
                                                     view=top['viewport'].view)
        new_top_stream_controller.stream_panel.show_remove_btn(False)
        self._stream_controllers.append(new_top_stream_controller)

        # Replace the settings of the bottom stream controller with the new StreamController
        for stream_settings in self.panel.bottom_settings.stream_panels:
            self.panel.bottom_settings.remove_stream_panel(stream_settings)
        new_bottom_stream_controller = StreamController(self.panel.bottom_settings,
                                                        bottom["stream"],
                                                        self.tab_data_model,
                                                        view=bottom['viewport'].view)
        new_bottom_stream_controller.stream_panel.show_remove_btn(False)
        self._stream_controllers.append(new_bottom_stream_controller)

        for view in self.tab_data_model.visible_views.value:
            if hasattr(view, "stream_classes") and isinstance(top["stream"], view.stream_classes):
                new_top_stream_controller.stream_panel.show_visible_btn(False)
                if not top["stream"] in view.stream_tree:
                    view.addStream(top["stream"])  # Only add streams to a view on which it hasn't been displayed.

            elif hasattr(view, "stream_classes") and isinstance(bottom["stream"], view.stream_classes):
                new_bottom_stream_controller.stream_panel.show_visible_btn(False)
                if not bottom["stream"] in view.stream_tree:
                    view.addStream(bottom["stream"])  # Only add streams to a view on which it hasn't been displayed.

    def _on_acquisition(self, is_acquiring):
        # When acquiring, the tab is automatically disabled and should be left as-is
        # In particular, that's the state when moving between positions in the
        # Chamber tab, and the tab should wait for the move to be complete before
        # actually be enabled.
        if is_acquiring:
            self._stage.position.unsubscribe(self._on_stage_pos)
        else:
            self._stage.position.subscribe(self._on_stage_pos, init=True)

    def _on_stage_pos(self, pos):
        """
        Called when the stage is moved, enable the tab if position is imaging mode, disable otherwise

        :param pos: (dict str->float or None) updated position of the stage
        """
        targets = (ALIGNMENT, THREE_BEAMS)
        guiutil.enable_tab_on_stage_position(self, self.posture_manager, targets,
                                             tooltip="Alignment can only be performed in the three beams mode")

    def Show(self, show=True):
        super().Show(show)

        # pause streams when not displayed
        if not show:
            self.stream_bar_controller.pauseStreams()

    @classmethod
    def get_display_priority(cls, main_data):
        if main_data.role in ("enzel",):
            return 1
        else:
            return None
