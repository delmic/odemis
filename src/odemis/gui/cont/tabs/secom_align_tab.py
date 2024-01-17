# -*- coding: utf-8 -*-

"""
@author: Rinze de Laat, Éric Piel, Philip Winkler, Victoria Mavrikopoulou,
         Anders Muskens, Bassim Lazem

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
import logging
import math
import numpy
import pkg_resources
import wx
# IMPORTANT: wx.html needs to be imported for the HTMLWindow defined in the XRC
# file to be correctly identified. See: http://trac.wxwidgets.org/ticket/3626
# This is not related to any particular wxPython version and is most likely permanent.
import wx.html

from odemis import model
import odemis.acq.stream as acqstream
import odemis.gui.cont.acquisition as acqcont
import odemis.gui.cont.views as viewcont
import odemis.gui.model as guimod
import odemis.gui.util as guiutil
import odemis.gui.util.align as align
from odemis.driver.actuator import ConvertStage
from odemis.gui.comp.canvas import CAN_ZOOM
from odemis.gui.comp.scalewindow import ScaleWindow
from odemis.gui.conf.data import get_local_vas
from odemis.gui.cont.actuators import ActuatorController
from odemis.gui.cont.streams import StreamController
from odemis.gui.cont.tabs.tab import Tab
from odemis.gui.model import TOOL_SPOT, TOOL_NONE, TOOL_DICHO
from odemis.gui.util import call_in_wx_main
from odemis.util import units


class SecomAlignTab(Tab):
    """ Tab for the lens alignment on the SECOM and SECOMv2 platform

    The streams are automatically active when the tab is shown
    It provides three ways to move the "aligner" (= optical lens position):
     * raw (via the A/B or X/Y buttons)
     * dicho mode (move opposite of the relative position of the ROI center)
     * spot mode (move equal to the relative position of the spot center)

    """

    def __init__(self, name, button, panel, main_frame, main_data):
        tab_data = guimod.SecomAlignGUIData(main_data)
        super(SecomAlignTab, self).__init__(name, button, panel, main_frame, tab_data)
        self.set_label("ALIGNMENT")
        panel.vp_align_sem.ShowLegend(False)

        # For the SECOMv1, we need to convert A/B to Y/X (with an angle of 45°)
        # Note that this is an approximation of the actual movements.
        # In the current SECOM design, B affects both axes (not completely in a
        # linear fashion) and A affects mostly X (not completely in a linear
        # fashion). By improving the model (=conversion A/B <-> X/Y), the GUI
        # could behave in a more expected way to the user, but the current
        # approximation is enough to do the calibration relatively quickly.
        if "a" in main_data.aligner.axes:
            self._aligner_xy = ConvertStage("converter-ab", "stage",
                                            dependencies={"orig": main_data.aligner},
                                            axes=["b", "a"],
                                            rotation=math.radians(45))
            self._convert_to_aligner = self._convert_xy_to_ab
        else:  # SECOMv2 => it's directly X/Y
            if "x" not in main_data.aligner.axes:
                logging.error("Unknown axes in lens aligner stage")
            self._aligner_xy = main_data.aligner
            self._convert_to_aligner = lambda x: x

        # vp_align_sem is connected to the stage
        vpv = collections.OrderedDict([
            (
                panel.vp_align_ccd,  # focused view
                {
                    "name": "Optical CL",
                    "cls": guimod.ContentView,
                    "stage": self._aligner_xy,
                    "stream_classes": acqstream.CameraStream,
                }
            ),
            (
                panel.vp_align_sem,
                {
                    "name": "SEM",
                    "cls": guimod.MicroscopeView,
                    "stage": main_data.stage,
                    "stream_classes": acqstream.EMStream,
                },
            )
        ])

        self.view_controller = viewcont.ViewPortController(
            self.tab_data_model,
            self.panel,
            vpv
        )

        if main_data.ccd:
            # Create CCD stream
            # Force the "temperature" VA to be displayed by making it a hw VA
            hwdetvas = set()
            if model.hasVA(main_data.ccd, "temperature"):
                hwdetvas.add("temperature")
            opt_stream = acqstream.CameraStream("Optical CL",
                                                main_data.ccd,
                                                main_data.ccd.data,
                                                emitter=None,
                                                focuser=main_data.focus,
                                                hwdetvas=hwdetvas,
                                                detvas=get_local_vas(main_data.ccd, main_data.hw_settings_config),
                                                forcemd={model.MD_ROTATION: 0,
                                                         model.MD_SHEAR: 0}
                                                )

            # Synchronise the fine alignment dwell time with the CCD settings
            opt_stream.detExposureTime.value = main_data.fineAlignDwellTime.value
            opt_stream.detBinning.value = opt_stream.detBinning.range[0]
            opt_stream.detResolution.value = opt_stream.detResolution.range[1]
            opt_stream.detExposureTime.subscribe(self._update_fa_dt)
            opt_stream.detBinning.subscribe(self._update_fa_dt)
            self.tab_data_model.tool.subscribe(self._update_fa_dt)
        elif main_data.photo_ds and main_data.laser_mirror:
            # We use arbitrarily the detector with the first name in alphabetical order, just
            # for reproducibility.
            photod = min(main_data.photo_ds, key=lambda d: d.role)
            # A SEM stream fits better than a CameraStream to the confocal
            # hardware with scanner + det (it could be called a ScannedStream).
            # TODO: have a special stream which can combine the data from all
            # the photodectors, to get more signal. The main annoyance is what
            # to do with the settings (gain/offset) for all of these detectors).
            opt_stream = acqstream.SEMStream("Optical CL",
                                             photod,
                                             photod.data,
                                             main_data.laser_mirror,
                                             focuser=main_data.focus,
                                             hwdetvas=get_local_vas(photod, main_data.hw_settings_config),
                                             emtvas=get_local_vas(main_data.laser_mirror, main_data.hw_settings_config),
                                             forcemd={model.MD_ROTATION: 0,
                                                      model.MD_SHEAR: 0},
                                             acq_type=model.MD_AT_CL
                                             )
            opt_stream.emtScale.value = opt_stream.emtScale.clip((8, 8))
            opt_stream.emtDwellTime.value = opt_stream.emtDwellTime.range[0]
            # They are 3 settings for the laser-mirror:
            # * in standard/spot mode (full FoV acquisition)
            # * in dichotomy mode (center of FoV acquisition)
            # * outside of this tab (stored with _lm_settings)
            self._lm_settings = (None, None, None, None)
        else:
            logging.error("No optical detector found for SECOM alignment")

        opt_stream.should_update.value = True
        self.tab_data_model.streams.value.insert(0, opt_stream) # current stream
        self._opt_stream = opt_stream
        # To ensure F6 (play/pause) works: very simple stream scheduler
        opt_stream.should_update.subscribe(self._on_ccd_should_update)
        self._ccd_view = panel.vp_align_ccd.view
        self._ccd_view.addStream(opt_stream)
        # create CCD stream panel entry
        ccd_spe = StreamController(panel.pnl_opt_streams, opt_stream, self.tab_data_model)
        ccd_spe.stream_panel.flatten()  # removes the expander header
        # force this view to never follow the tool mode (just standard view)
        panel.vp_align_ccd.canvas.allowed_modes = {TOOL_NONE}

        # To control the blanker if it's not automatic (None)
        # TODO: remove once the CompositedScanner supports automatic blanker.
        if (model.hasVA(main_data.ebeam, "blanker") and
            None not in main_data.ebeam.blanker.choices
           ):
            blanker = main_data.ebeam.blanker
        else:
            blanker = None

        # No streams controller, because it does far too much (including hiding
        # the only stream entry when SEM view is focused)
        # Use all VAs as HW VAs, so the values are shared with the streams tab
        sem_stream = acqstream.SEMStream("SEM", main_data.sed,
                                         main_data.sed.data,
                                         main_data.ebeam,
                                         hwdetvas=get_local_vas(main_data.sed, main_data.hw_settings_config),
                                         hwemtvas=get_local_vas(main_data.ebeam, main_data.hw_settings_config),
                                         acq_type=model.MD_AT_EM,
                                         blanker=blanker
                                         )
        sem_stream.should_update.value = True
        self.tab_data_model.streams.value.append(sem_stream)
        self._sem_stream = sem_stream
        self._sem_view = panel.vp_align_sem.view
        self._sem_view.addStream(sem_stream)

        sem_spe = StreamController(self.panel.pnl_sem_streams, sem_stream, self.tab_data_model)
        sem_spe.stream_panel.flatten()  # removes the expander header

        spot_stream = acqstream.SpotSEMStream("Spot", main_data.sed,
                                              main_data.sed.data, main_data.ebeam,
                                              blanker=blanker)
        self.tab_data_model.streams.value.append(spot_stream)
        self._spot_stream = spot_stream

        # Adapt the zoom level of the SEM to fit exactly the SEM field of view.
        # No need to check for resize events, because the view has a fixed size.
        if not main_data.ebeamControlsMag:
            panel.vp_align_sem.canvas.abilities -= {CAN_ZOOM}
            # prevent the first image to reset our computation
            panel.vp_align_sem.canvas.fit_view_to_next_image = False
            main_data.ebeam.pixelSize.subscribe(self._onSEMpxs, init=True)

        self._stream_controllers = (ccd_spe, sem_spe)
        self._sem_spe = sem_spe  # to disable it during spot mode

        # Update the SEM area in dichotomic mode
        self.tab_data_model.dicho_seq.subscribe(self._onDichoSeq, init=True)

        # Bind actuator buttons and keys
        self._actuator_controller = ActuatorController(self.tab_data_model, panel, "lens_align_")
        self._actuator_controller.bind_keyboard(panel)

        # Toolbar
        tb = panel.lens_align_tb
        tb.add_tool(TOOL_DICHO, self.tab_data_model.tool)
        tb.add_tool(TOOL_SPOT, self.tab_data_model.tool)

        # Dichotomy mode: during this mode, the label & button "move to center" are
        # shown. If the sequence is empty, or a move is going, it's disabled.
        self._aligner_move = None  # the future of the move (to know if it's over)
        panel.lens_align_btn_to_center.Bind(wx.EVT_BUTTON, self._on_btn_to_center)

        # If SEM pxs changes, A/B or X/Y are actually different values
        main_data.ebeam.pixelSize.subscribe(self._update_to_center)

        # Fine alignment panel
        pnl_sem_toolbar = panel.pnl_sem_toolbar
        fa_sizer = pnl_sem_toolbar.GetSizer()
        scale_win = ScaleWindow(pnl_sem_toolbar)
        self._on_mpp = guiutil.call_in_wx_main_wrapper(scale_win.SetMPP)  # need to keep ref
        self._sem_view.mpp.subscribe(self._on_mpp, init=True)
        fa_sizer.Add(scale_win, proportion=3, flag=wx.TOP | wx.LEFT, border=10)
        fa_sizer.Layout()

        if main_data.ccd:
            # TODO: make these controllers also work on confocal
            # For Fine alignment, the procedure might need to be completely reviewed
            # For auto centering, it's mostly a matter of updating align.AlignSpot()
            # to know about scanners.
            self._fa_controller = acqcont.FineAlignController(self.tab_data_model,
                                                              panel,
                                                              main_frame)

            self._ac_controller = acqcont.AutoCenterController(self.tab_data_model,
                                                               self._aligner_xy,
                                                               panel)

        # Documentation text on the left panel
        # TODO: need different instructions in case of confocal microscope
        doc_path = pkg_resources.resource_filename("odemis.gui", "doc/alignment.html")
        panel.html_alignment_doc.SetBorders(0)  # sizer already give us borders
        panel.html_alignment_doc.LoadPage(doc_path)

        # Trick to allow easy html editing: double click to reload
        # def reload_page(evt):
        #     evt.GetEventObject().LoadPage(path)

        # panel.html_alignment_doc.Bind(wx.EVT_LEFT_DCLICK, reload_page)

        self.tab_data_model.tool.subscribe(self._onTool, init=True)
        main_data.chamberState.subscribe(self.on_chamber_state, init=True)

    def _on_ccd_should_update(self, update):
        """
        Very basic stream scheduler (just one stream)
        """
        self._opt_stream.is_active.value = update

    def Show(self, show=True):
        Tab.Show(self, show=show)

        main_data = self.tab_data_model.main
        # Store/restore previous confocal settings when entering/leaving the tab
        lm = main_data.laser_mirror
        if show and lm:
            # Must be done before starting the stream
            self._lm_settings = (lm.scale.value,
                                 lm.resolution.value,
                                 lm.translation.value,
                                 lm.dwellTime.value)

        # Turn on/off the streams as the tab is displayed.
        # Also directly modify is_active, as there is no stream scheduler
        for s in self.tab_data_model.streams.value:
            if show:
                s.is_active.value = s.should_update.value
            else:
                s.is_active.value = False

        if not show and lm and None not in self._lm_settings:
            # Must be done _after_ stopping the stream
            # Order matters
            lm.scale.value = self._lm_settings[0]
            lm.resolution.value = self._lm_settings[1]
            lm.translation.value = self._lm_settings[2]
            lm.dwellTime.value = self._lm_settings[3]
            # To be sure that if it's called when already not shown, we don't
            # put old values again
            self._lm_settings = (None, None, None, None)

        # Freeze the stream settings when an alignment is going on
        if show:
            # as we expect no acquisition active when changing tab, it will always
            # lead to subscriptions to VA
            main_data.is_acquiring.subscribe(self._on_acquisition, init=True)
            # Move aligner on tab showing to FAV_POS_ACTIVE position
            # (if all axes are referenced and there are indeed active and deactive positions metadata)
            md = self._aligner_xy.getMetadata()
            if {model.MD_FAV_POS_ACTIVE, model.MD_FAV_POS_DEACTIVE}.issubset(md.keys()) \
                    and all(self._aligner_xy.referenced.value.values()):
                f = self._aligner_xy.moveAbs(md[model.MD_FAV_POS_ACTIVE])
                self._actuator_controller._enable_buttons(False)
                f.add_done_callback(self._on_align_move_done)
        else:
            main_data.is_acquiring.unsubscribe(self._on_acquisition)
            self._aligner_xy.position.unsubscribe(self._on_align_pos)

    def terminate(self):
        super(SecomAlignTab, self).terminate()
        # make sure the streams are stopped
        for s in self.tab_data_model.streams.value:
            s.is_active.value = False

    @call_in_wx_main
    def on_chamber_state(self, state):
        # Lock or enable lens alignment
        in_vacuum = state in {guimod.CHAMBER_VACUUM, guimod.CHAMBER_UNKNOWN}
        self.button.Enable(in_vacuum)
        self.highlight(in_vacuum)

    @call_in_wx_main
    def _onTool(self, tool):
        """
        Called when the tool (mode) is changed
        """
        shown = self.IsShown() # to make sure we don't play streams in the background

        # Reset previous mode
        if tool != TOOL_DICHO:
            # reset the sequence
            self.tab_data_model.dicho_seq.value = []
            self.panel.pnl_move_to_center.Show(False)
            self.panel.pnl_align_tools.Show(self.tab_data_model.main.ccd is not None)

            if self.tab_data_model.main.laser_mirror:  # confocal => go back to scan
                # TODO: restore the previous values
                if self._opt_stream.roi.value == (0.5, 0.5, 0.5, 0.5):
                    self._opt_stream.is_active.value = False
                    self._opt_stream.roi.value = (0, 0, 1, 1)
                    self._opt_stream.emtScale.value = self._opt_stream.emtScale.clip((8, 8))
                    self._opt_stream.emtDwellTime.value = self._opt_stream.emtDwellTime.range[0]
                    self._opt_stream.is_active.value = self._opt_stream.should_update.value
                    # Workaround the fact that the stream has no local res,
                    # so the hardware limits the dwell time based on the previous
                    # resolution used.
                    # TODO: fix the stream to set the dwell time properly (set the res earlier)
                    self._opt_stream.emtDwellTime.value = self._opt_stream.emtDwellTime.range[0]
                    self.panel.vp_align_ccd.canvas.fit_view_to_next_image = True

        if tool != TOOL_SPOT:
            self._spot_stream.should_update.value = False
            self._spot_stream.is_active.value = False
            self._sem_stream.should_update.value = True
            self._sem_stream.is_active.value = shown
            self._sem_spe.resume()

        # Set new mode
        if tool == TOOL_DICHO:
            self.panel.pnl_move_to_center.Show(True)
            self.panel.pnl_align_tools.Show(False)

            if self.tab_data_model.main.laser_mirror:  # confocal => got spot mode
                # Start the new settings immediately after
                self._opt_stream.is_active.value = False
                # TODO: could using a special "Confocal spot mode" stream simplify?
                # TODO: store the previous values
                self._opt_stream.roi.value = (0.5, 0.5, 0.5, 0.5)
                # The scale ensures that _the_ pixel takes the whole screen
                # TODO: if the refit works properly, it shouldn't be needed
                self._opt_stream.emtScale.value = self._opt_stream.emtScale.range[1]
                self._opt_stream.emtDwellTime.value = self._opt_stream.emtDwellTime.clip(0.1)
                self.panel.vp_align_ccd.canvas.fit_view_to_next_image = True
                self._opt_stream.is_active.value = self._opt_stream.should_update.value
                self._opt_stream.emtDwellTime.value = self._opt_stream.emtDwellTime.clip(0.1)
            # TODO: with a standard CCD, it'd make sense to also use a very large binning
        elif tool == TOOL_SPOT:
            # Do not show the SEM settings being changed during spot mode, and
            # do not allow to change the resolution/scale
            self._sem_spe.pause()

            self._sem_stream.should_update.value = False
            self._sem_stream.is_active.value = False
            self._spot_stream.should_update.value = True
            self._spot_stream.is_active.value = shown

            # TODO: support spot mode and automatically update the survey image each
            # time it's updated.
            # => in spot-mode, listen to stage position and magnification, if it
            # changes reactivate the SEM stream and subscribe to an image, when image
            # is received, stop stream and move back to spot-mode. (need to be careful
            # to handle when the user disables the spot mode during this moment)

        self.panel.pnl_move_to_center.Parent.Layout()

    def _onDichoSeq(self, seq):
        roi = align.dichotomy_to_region(seq)
        logging.debug("Seq = %s -> roi = %s", seq, roi)
        self._sem_stream.roi.value = roi

        self._update_to_center()

    @call_in_wx_main
    def _on_acquisition(self, is_acquiring):
        """
        Called when an "acquisition" is going on
        """
        # (Un)freeze the stream settings
        if is_acquiring:
            for stream_controller in self._stream_controllers:
                stream_controller.enable(False)
                stream_controller.pause()
        else:
            for stream_controller in self._stream_controllers:
                if (self.tab_data_model.tool.value == TOOL_SPOT and
                    stream_controller is self._sem_spe):
                    continue
                stream_controller.resume()
                stream_controller.enable(True)

    def _update_fa_dt(self, unused=None):
        """
        Called when the fine alignment dwell time must be recomputed (because
        the CCD exposure time or binning has changed. It will only be updated
        if the SPOT mode is active (otherwise the user might be setting for
        different purpose).
        """
        # Only update fineAlignDwellTime when spot tool is selected
        if self.tab_data_model.tool.value != TOOL_SPOT:
            return

        # dwell time is the based on the exposure time for the spot, as this is
        # the best clue on what works with the sample.
        main_data = self.tab_data_model.main
        binning = self._opt_stream.detBinning.value
        dt = self._opt_stream.detExposureTime.value * numpy.prod(binning)
        main_data.fineAlignDwellTime.value = main_data.fineAlignDwellTime.clip(dt)

    # "Move to center" functions
    @call_in_wx_main
    def _update_to_center(self, _=None):
        # Enable a special "move to SEM center" button iif:
        # * seq is not empty
        # * (and) no move currently going on
        seq = self.tab_data_model.dicho_seq.value
        if seq and (self._aligner_move is None or self._aligner_move.done()):
            roi = self._sem_stream.roi.value
            move = self._computeROICenterMove(roi)
            # Convert to a text like "A = 45µm, B = -9µm"
            mov_txts = []
            for a in sorted(move.keys()):
                v = units.readable_str(move[a], unit="m", sig=2)
                mov_txts.append("%s = %s" % (a.upper(), v))

            lbl = "Approximate center away by:\n%s." % ", ".join(mov_txts)
            enabled = True

            # TODO: Warn if move is bigger than previous move (or simply too big)
        else:
            lbl = "Pick a sub-area to approximate the SEM center.\n"
            enabled = False

        self.panel.lens_align_btn_to_center.Enable(enabled)
        lbl_ctrl = self.panel.lens_align_lbl_approc_center
        lbl_ctrl.SetLabel(lbl)
        lbl_ctrl.Wrap(lbl_ctrl.Size[0])
        self.panel.Layout()

    def _on_btn_to_center(self, event):
        """
        Called when a click on the "move to center" button happens
        """
        # computes the center position
        seq = self.tab_data_model.dicho_seq.value
        roi = align.dichotomy_to_region(seq)
        move = self._computeROICenterMove(roi)

        # disable the button to avoid another move
        self.panel.lens_align_btn_to_center.Disable()

        # run the move
        logging.debug("Moving by %s", move)
        self._aligner_move = self.tab_data_model.main.aligner.moveRel(move)
        self._aligner_move.add_done_callback(self._on_move_to_center_done)

    def _on_move_to_center_done(self, future):
        """
        Called when the move to the center is done
        """
        # reset the sequence as it's going to be completely different
        logging.debug("Move over")
        self.tab_data_model.dicho_seq.value = []

    def _computeROICenterMove(self, roi):
        """
        Computes the move require to go to the center of ROI, in the aligner
         coordinates
        roi (tuple of 4: 0<=float<=1): left, top, right, bottom (in ratio)
        returns (dict of str -> floats): relative move needed
        """
        # compute center in X/Y coordinates
        pxs = self.tab_data_model.main.ebeam.pixelSize.value
        eshape = self.tab_data_model.main.ebeam.shape
        fov_size = (eshape[0] * pxs[0], eshape[1] * pxs[1])  # m
        l, t, r, b = roi
        center = {"x": fov_size[0] * ((l + r) / 2 - 0.5),
                  "y":-fov_size[1] * ((t + b) / 2 - 0.5)} # physical Y is reversed
        logging.debug("center of ROI at %s", center)

        # The move is opposite direction of the relative center
        shift_xy = {"x":-center["x"], "y":-center["y"]}
        shift = self._convert_to_aligner(shift_xy)
        # Drop the moves if very close to it (happens often with A/B as they can
        # be just on the axis)
        for a, v in shift.items():
            if abs(v) < 1e-10:
                shift[a] = 0

        return shift

    def _convert_xy_to_ab(self, shift):
        # same formula as ConvertStage._convertPosToChild()
        ang = math.radians(45) # Used to be -135° when conventions were inversed

        return {"b": shift["x"] * math.cos(ang) - shift["y"] * math.sin(ang),
                "a": shift["x"] * math.sin(ang) + shift["y"] * math.cos(ang)}

    def _onSEMpxs(self, pixel_size):
        """ Called when the SEM pixel size changes, which means the FoV changes

        pixel_size (tuple of 2 floats): in meter
        """
        eshape = self.tab_data_model.main.ebeam.shape
        fov_size = (eshape[0] * pixel_size[0], eshape[1] * pixel_size[1])  # m
        semv_size = self.panel.vp_align_sem.Size  # px

        # compute MPP to fit exactly the whole FoV
        mpp = (fov_size[0] / semv_size[0], fov_size[1] / semv_size[1])
        best_mpp = max(mpp)  # to fit everything if not same ratio
        best_mpp = self._sem_view.mpp.clip(best_mpp)
        self._sem_view.mpp.value = best_mpp

    def _on_align_pos(self, pos):
        """
        Called when the aligner is moved (and the tab is shown)
        :param pos: (dict str->float or None) updated position of the aligner
        """
        if not self.IsShown():
            # Shouldn't happen, but for safety, double check
            logging.warning("Alignment mode changed while alignment tab not shown")
            return
        # Check if updated position is close to FAV_POS_DEACTIVE
        md = self._aligner_xy.getMetadata()
        dist_deactive = math.hypot(pos["x"] - md[model.MD_FAV_POS_DEACTIVE]["x"],
                                   pos["y"] - md[model.MD_FAV_POS_DEACTIVE]["y"])
        if dist_deactive <= 0.1e-3:  # (within 0.1mm of deactive is considered deactivated.)
            logging.warning("Aligner seems parked, not updating FAV_POS_ACTIVE")
            return
        # Save aligner position as the "calibrated" one
        self._aligner_xy.updateMetadata({model.MD_FAV_POS_ACTIVE: pos})

    def _on_align_move_done(self, f=None):
        """
        Wait for the movement of the aligner to it default active position,
        then subscribe to its position to update the active position.
        f (future): future of the movement
        """
        if f:
            f.result()  # to fail & log if the movement failed
        # re-enable the axes buttons
        self._actuator_controller._enable_buttons(True)
        # Subscribe to lens aligner movement to update its FAV_POS_ACTIVE metadata
        self._aligner_xy.position.subscribe(self._on_align_pos)

    @classmethod
    def get_display_priority(cls, main_data):
        if main_data.role in ("secom",):
            return 1
        else:
            return None
