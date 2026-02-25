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
from functools import partial
import logging
import numpy
import wx

from odemis import model
import odemis.acq.stream as acqstream
import odemis.gui.cont.acquisition as acqcont
from odemis.gui.cont.stream_bar import SecomStreamsController
import odemis.gui.cont.views as viewcont
import odemis.gui.model as guimod
from odemis.acq.align import AutoFocus
from odemis.acq.stream import OpticalStream, EMStream, FIBStream, \
    RGBCameraStream, BrightfieldStream, RGBUpdatableStream, \
    ScannedTCSettingsStream
from odemis.gui.conf.data import get_local_vas
from odemis.gui.cont import settings
from odemis.gui.cont.microscope import SecomStateController, DelphiStateController
from odemis.gui.cont.tabs.tab import Tab
from odemis.gui.model import TOOL_SPOT, TOOL_ACT_ZOOM_FIT, TOOL_AUTO_FOCUS
from odemis.gui.util import call_in_wx_main
from odemis.gui.util.widgets import ScannerFoVAdapter

# Preferable autofocus values to be set when triggering autofocus in delphi
AUTOFOCUS_BINNING = (8, 8)
AUTOFOCUS_HFW = 300e-06  # m


class SecomStreamsTab(Tab):
    def __init__(self, name, button, panel, main_frame, main_data):
        """
        :type name: str
        :type button: odemis.gui.comp.buttons.TabButton
        :type panel: wx._windows.Panel
        :type main_frame: odemis.gui.main_xrc.xrcfr_main
        :type main_data: odemis.gui.model.MainGUIData
        """

        tab_data = guimod.LiveViewGUIData(main_data)
        super(SecomStreamsTab, self).__init__(name, button, panel, main_frame, tab_data)
        self.set_label("STREAMS")

        self.main_data = main_data

        # First we create the views, then the streams
        vpv = self._create_views(main_data, panel.pnl_secom_grid.viewports)

        # If a time_correlator is present, there is a ROA based on the laser_mirror
        if main_data.time_correlator:
            tab_data.fovComp = main_data.laser_mirror

        # When using the confocal microscope, we don't have a real "global"
        # hardware settings. Instead, we have a mock stream with all the common
        # settings passed as local settings, and it will be "attached" to all
        # the confocal streams, which will eventually be acquired simultaneously.
        # TODO: as the ScannedFluoStream will be folded into a single
        # ScannedFluoMDStream, maybe we could directly use that stream to put
        # the settings.
        # Note: This means there is only one light power for all the confocal
        # streams, which in theory could be incorrect if multiple excitation
        # wavelengths were active simultaneously and independently. However,
        # Odemis doesn't currently supports such setup anyway, so no need to
        # complicate the GUI with a separate power setting on each stream.
        fov_adp = None
        if main_data.laser_mirror:
            # HACK: ideally, the laser_mirror would be the "scanner", but there
            # is no such concept on the basic Stream, and no "scanner_vas" either.
            # So instead, we declare it as a detector, and it works surprisingly
            # fine.
            detvas = get_local_vas(main_data.laser_mirror, main_data.hw_settings_config)
            detvas -= {"resolution", "scale"}
            conf_set_stream = acqstream.ScannerSettingsStream("Confocal shared settings",
                                      detector=main_data.laser_mirror,
                                      dataflow=None,
                                      emitter=main_data.light,
                                      detvas=detvas,
                                      emtvas=get_local_vas(main_data.light, main_data.hw_settings_config),
                                      )

            # Set some nice default values
            if conf_set_stream.power.value == 0 and hasattr(conf_set_stream.power, "range"):
                # cf StreamBarController._ensure_power_non_null()
                # Default to power = 10% (if 0)
                conf_set_stream.power.value = conf_set_stream.power.range[1] * 0.1

            # TODO: the period should always be set to the min? And not even be changed?
            if hasattr(conf_set_stream, "emtPeriod"):
                # Use max frequency
                conf_set_stream.emtPeriod.value = conf_set_stream.emtPeriod.range[0]

            tab_data.confocal_set_stream = conf_set_stream

            # Link the .zoom to "all" (1) the optical view
            fov_adp = ScannerFoVAdapter(conf_set_stream)
            for vp, v in vpv.items():
                if v.get("stream_classes") == OpticalStream:
                    v["fov_hw"] = fov_adp
                    vp.canvas.fit_view_to_next_image = False

        # Order matters!
        self.view_controller = viewcont.ViewPortController(tab_data, panel, vpv)

        # Unhide a special view for a ScannedTCSettingsStream used for FLIM
        # if a time correlator is present
        if main_data.time_correlator:
            panel.vp_flim_chronograph.Show()

        # Special overview button selection
        self.overview_controller = viewcont.OverviewController(main_data, self,
                                                               panel.vp_overview_sem.canvas,
                                                               self.panel.vp_overview_sem.view,
                                                               panel.pnl_secom_streams,
                                                               )

        # Connect the view selection buttons
        buttons = collections.OrderedDict([
            (panel.btn_secom_view_all,
                (None, panel.lbl_secom_view_all)),
            (panel.btn_secom_view_tl,
                (panel.vp_secom_tl, panel.lbl_secom_view_tl)),
            (panel.btn_secom_view_tr,
                (panel.vp_secom_tr, panel.lbl_secom_view_tr)),
            (panel.btn_secom_view_bl,
                (panel.vp_secom_bl, panel.lbl_secom_view_bl)),
            (panel.btn_secom_view_br,
                (panel.vp_secom_br, panel.lbl_secom_view_br)),
            (panel.btn_secom_overview,
                (panel.vp_overview_sem, panel.lbl_secom_overview)),
        ])
        if main_data.time_correlator:
            buttons[panel.btn_secom_view_br] = (panel.vp_flim_chronograph, panel.lbl_secom_view_br)

        self._view_selector = viewcont.ViewButtonController(
            tab_data,
            panel,
            buttons,
            panel.pnl_secom_grid.viewports
        )

        if TOOL_SPOT in tab_data.tool.choices:
            spot_stream = acqstream.SpotScannerStream("Spot", main_data.tc_detector,
                                              main_data.tc_detector.data, main_data.laser_mirror)
            tab_data.spotStream = spot_stream
            # TODO: add to tab_data.streams and move the handling to the stream controller?
            tab_data.spotPosition.subscribe(self._onSpotPosition)
            tab_data.tool.subscribe(self.on_tool_change)

        self._settingbar_controller = settings.SecomSettingsController(
            panel,
            tab_data
        )

        self._streambar_controller = SecomStreamsController(
            tab_data,
            panel.pnl_secom_streams,
            view_ctrl=self.view_controller
        )

        # Toolbar
        self.tb = panel.secom_toolbar
        for t in guimod.TOOL_ORDER:
            if t in tab_data.tool.choices:
                self.tb.add_tool(t, tab_data.tool)
        # Add fit view to content to toolbar
        self.tb.add_tool(TOOL_ACT_ZOOM_FIT, self.view_controller.fitViewToContent)
        # auto focus
        self._autofocus_f = model.InstantaneousFuture()
        self.tb.add_tool(TOOL_AUTO_FOCUS, self.tab_data_model.autofocus_active)
        self.tb.enable_button(TOOL_AUTO_FOCUS, False)
        self.tab_data_model.autofocus_active.subscribe(self._onAutofocus)
        tab_data.streams.subscribe(self._on_current_stream)

        # To automatically play/pause a stream when turning on/off a microscope,
        # and add the stream on the first time.
        if hasattr(tab_data, 'opticalState'):
            tab_data.opticalState.subscribe(self.onOpticalState)
            if main_data.ccd:
                self._add_opt_stream = self._streambar_controller.addFluo
            elif main_data.photo_ds:
                # Use the first photo-detector in alphabetical order
                pd0 = min(main_data.photo_ds, key=lambda d: d.role)
                self._add_opt_stream = partial(self._streambar_controller.addConfocal,
                                               detector=pd0)
            else:
                logging.error("No optical detector found")

        if hasattr(tab_data, 'emState'):
            tab_data.emState.subscribe(self.onEMState)
            # decide which kind of EM stream to add by default
            if main_data.sed:
                self._add_em_stream = self._streambar_controller.addSEMSED
            elif main_data.bsd:
                self._add_em_stream = self._streambar_controller.addSEMBSD
            else:
                logging.error("No EM detector found")

        if main_data.ion_beam:
            fib_stream = acqstream.FIBStream("Fib scanner",
                                             main_data.sed,
                                             main_data.sed.data,
                                             main_data.ion_beam,
                                             )
            tab_data.fib_stream = fib_stream
            self._streambar_controller.addStream(fib_stream, add_to_view=True, visible=True, play=False)


        # Create streams before state controller, so based on the chamber state,
        # the streams will be enabled or not.
        self._ensure_base_streams()

        self._acquisition_controller = acqcont.SecomAcquiController(tab_data, panel)

        if main_data.role == "delphi":
            state_controller_cls = DelphiStateController
        else:
            state_controller_cls = SecomStateController

        self._state_controller = state_controller_cls(
            tab_data,
            panel,
            self._streambar_controller
        )

        main_data.chamberState.subscribe(self.on_chamber_state, init=True)


    @property
    def settingsbar_controller(self):
        return self._settingbar_controller

    @property
    def streambar_controller(self):
        return self._streambar_controller

    def _create_views(self, main_data, viewports):
        """
        Create views depending on the actual hardware present
        return OrderedDict: as needed for the ViewPortController
        """

        # If both SEM and Optical are present (= SECOM & DELPHI)
        if main_data.ebeam and main_data.light:

            logging.info("Creating combined SEM/Optical viewport layout")
            vpv = collections.OrderedDict([
                (viewports[0],  # focused view
                 {"name": "Optical",
                  "stage": main_data.stage,
                  "stream_classes": OpticalStream,
                  }),
                (viewports[1],
                 {"name": "SEM",
                  # centered on content, even on Delphi when POS_COR used to
                  # align on the optical streams
                  "cls": guimod.ContentView,
                  "stage": main_data.stage,
                  "stream_classes": EMStream
                  }),
                (viewports[2],
                 {"name": "Combined 1",
                  "stage": main_data.stage,
                  "stream_classes": (EMStream, OpticalStream),
                  }),
            ])

            if main_data.time_correlator:
                vpv[viewports[4]] = {
                  "name": "FLIM",
                  "stage": main_data.stage,
                  "stream_classes": (ScannedTCSettingsStream,),
                  }

            else:
                vpv[viewports[3]] = {
                  "name": "Combined 2",
                  "stage": main_data.stage,
                  "stream_classes": (EMStream, OpticalStream),
                 }

        # If SEM only: all SEM
        # Works also for the Sparc, as there is no other emitter, and we don't
        # need to display anything else anyway
        elif main_data.ebeam and not main_data.light:
            logging.info("Creating SEM only viewport layout")
            vpv = collections.OrderedDict()
            for i, viewport in enumerate(viewports[:4]):
                vpv[viewport] = {"name": "SEM %d" % (i + 1),
                                 "stage": main_data.stage,
                                 "stream_classes": EMStream,
                                 }

        # If Optical only: all optical
        elif not main_data.ebeam and main_data.light:
            logging.info("Creating Optical only viewport layout")
            vpv = collections.OrderedDict()
            for i, viewport in enumerate(viewports[:4]):
                vpv[viewport] = {"name": "Optical %d" % (i + 1),
                                 "stage": main_data.stage,
                                 "stream_classes": OpticalStream,
                                 }
        else:
            logging.warning("No known microscope configuration, creating 4 "
                            "generic views")
            vpv = collections.OrderedDict()
            for i, viewport in enumerate(viewports[:4]):
                vpv[viewport] = {
                    "name": "View %d" % (i + 1),
                    "stage": main_data.stage,
                    "stream_classes": None,  # everything
                }

        # Insert a Chamber viewport into the lower left position if a chamber camera is present
        if main_data.chamber_ccd and main_data.chamber_light:
            logging.debug("Inserting Chamber viewport")
            vpv[viewports[2]] = {
                "name": "Chamber",
                "stream_classes": (RGBCameraStream, BrightfieldStream),
            }

        # If there are 6 viewports, we'll assume that the last one is an overview camera stream
        if len(viewports) == 6:
            logging.debug("Inserting Overview viewport")
            vpv[viewports[5]] = {
                "cls": guimod.FixedOverviewView,
                "name": "Overview",
                "stage": main_data.stage,
                "stream_classes": (RGBUpdatableStream, RGBCameraStream, BrightfieldStream),
            }

        # Add connection to SEM hFoV if possible (on SEM-only views)
        if main_data.ebeamControlsMag:
            for vp, v in vpv.items():
                if v.get("stream_classes") == EMStream:
                    v["fov_hw"] = main_data.ebeam
                    vp.canvas.fit_view_to_next_image = False

        if main_data.ion_beam:
            vpv[viewports[3]] = {
                "name" : "FIB",
                "stream_classes": FIBStream,
                "stage": main_data.stage,
            }

        return vpv

    def _onSpotPosition(self, pos):
        """
        Called when the spot position is changed (via the overlay)
        """
        if None not in pos:
            assert len(pos) == 2
            assert all(0 <= p <= 1 for p in pos)
            # Just use the same value for LT and RB points
            self.tab_data_model.spotStream.roi.value = (pos + pos)
            logging.debug("Updating spot stream roi to %s", self.tab_data_model.spotStream.roi.value)

    def _onAutofocus(self, active):
        # Determine which stream is active
        if active:
            try:
                self.curr_s = self.tab_data_model.streams.value[0]
            except IndexError:
                # Should not happen as the menu/icon should be disabled
                logging.info("No stream to run the autofocus")
                self.tab_data_model.autofocus_active.value = False
                return

            # run only if focuser is available
            if self.curr_s.focuser:
                # TODO: maybe this can be done in a less hard-coded way
                self.orig_hfw = None
                self.orig_binning = None
                self.orig_exposureTime = None
                init_focus = None
                emt = self.curr_s.emitter
                det = self.curr_s.detector
                # Set binning to 8 in case of optical focus to avoid high SNR
                # and limit HFW in case of ebeam focus to avoid extreme brightness.
                # Also apply ACB before focusing in case of ebeam focus.
                if self.curr_s.focuser.role == "ebeam-focus" and self.main_data.role == "delphi":
                    self.orig_hfw = emt.horizontalFoV.value
                    emt.horizontalFoV.value = min(self.orig_hfw, AUTOFOCUS_HFW)
                    f = det.applyAutoContrastBrightness()
                    f.result()
                    # Use the good initial focus value if known
                    init_focus = self._state_controller.good_focus

                    # TODO: for the DELPHI, it should also be possible to use the
                    # opt_focus value from calibration as a "good value"
                else:
                    if model.hasVA(det, "binning"):
                        self.orig_binning = det.binning.value
                        det.binning.value = max(self.orig_binning, AUTOFOCUS_BINNING)
                        if model.hasVA(det, "exposureTime"):
                            self.orig_exposureTime = det.exposureTime.value
                            bin_ratio = numpy.prod(self.orig_binning) / numpy.prod(det.binning.value)
                            expt = self.orig_exposureTime * bin_ratio
                            det.exposureTime.value = det.exposureTime.clip(expt)

                self._autofocus_f = AutoFocus(det, emt, self.curr_s.focuser, good_focus=init_focus)
                self._autofocus_f.add_done_callback(self._on_autofocus_done)
            else:
                # Should never happen as normally the menu/icon are disabled
                logging.info("Autofocus cannot run as no hardware is available")
                self.tab_data_model.autofocus_active.value = False
        else:
            self._autofocus_f.cancel()

    def _on_autofocus_done(self, future):
        self.tab_data_model.autofocus_active.value = False
        if self.orig_hfw is not None:
            self.curr_s.emitter.horizontalFoV.value = self.orig_hfw
        if self.orig_binning is not None:
            self.curr_s.detector.binning.value = self.orig_binning
        if self.orig_exposureTime is not None:
            self.curr_s.detector.exposureTime.value = self.orig_exposureTime

    def _on_current_stream(self, streams):
        """
        Called when some VAs affecting the current stream change
        """
        # Try to get the current stream
        try:
            self.curr_s = streams[0]
        except IndexError:
            self.curr_s = None

        if self.curr_s:
            self.curr_s.should_update.subscribe(self._on_stream_update, init=True)
        else:
            wx.CallAfter(self.tb.enable_button, TOOL_AUTO_FOCUS, False)

    def _on_stream_update(self, updated):
        """
        Called when the current stream changes play/pause
        Used to update the autofocus button
        """
        # TODO: just let the menu controller also update toolbar (as it also
        # does the same check for the menu entry)
        try:
            self.curr_s = self.tab_data_model.streams.value[0]
        except IndexError:
            f = None
        else:
            f = self.curr_s.focuser

        f_enable = all((updated, f))
        if not f_enable:
            self.tab_data_model.autofocus_active.value = False
        wx.CallAfter(self.tb.enable_button, TOOL_AUTO_FOCUS, f_enable)

    def terminate(self):
        super(SecomStreamsTab, self).terminate()
        # make sure the streams are stopped
        for s in self.tab_data_model.streams.value:
            s.is_active.value = False

    def _ensure_base_streams(self):
        """
        Make sure there is at least one optical and one SEM stream present
        """
        if hasattr(self.tab_data_model, 'opticalState'):
            has_opt = any(isinstance(s, acqstream.OpticalStream)
                          for s in self.tab_data_model.streams.value)
            if not has_opt:
                # We don't forbid to remove it, as for the user it can be easier
                # to remove than change all the values
                self._add_opt_stream(add_to_view=True, play=False)

        if hasattr(self.tab_data_model, 'emState'):
            has_sem = any(isinstance(s, acqstream.EMStream)
                          for s in self.tab_data_model.streams.value)
            if not has_sem:
                stream_cont = self._add_em_stream(add_to_view=True, play=False)
                stream_cont.stream_panel.show_remove_btn(False)

    @call_in_wx_main
    def on_chamber_state(self, state):
        if state == guimod.CHAMBER_PUMPING:
            # Ensure we still have both optical and SEM streams
            self._ensure_base_streams()

    # TODO: move to stream controller?
    # => we need to update the state of optical/sem when the streams are play/paused
    # Listen to this event to just add (back) a stream if none is left when turning on?
    def onOpticalState(self, state):
        if state == guimod.STATE_ON:
            # Pick the last optical stream that played (.streams is ordered)
            for s in self.tab_data_model.streams.value:
                if isinstance(s, acqstream.OpticalStream):
                    opts = s
                    break
            else: # Could happen if the user has deleted all the optical streams
                sp = self._add_opt_stream(add_to_view=True)
                opts = sp.stream

            self._streambar_controller.resumeStreams({opts})
            # focus the view
            self.view_controller.focusViewWithStream(opts)
        else:
            self._streambar_controller.pauseStreams(acqstream.OpticalStream)

    def onEMState(self, state):
        if state == guimod.STATE_ON:
            # Use the last SEM stream played
            for s in self.tab_data_model.streams.value:
                if isinstance(s, acqstream.EMStream):
                    sems = s
                    break
            else:  # Could happen if the user has deleted all the EM streams
                sp = self._add_em_stream(add_to_view=True)
                sp.show_remove_btn(False)
                sems = sp.stream

            self._streambar_controller.resumeStreams({sems})
            # focus the view
            self.view_controller.focusViewWithStream(sems)
        else:
            self._streambar_controller.pauseStreams(acqstream.EMStream)

    def Show(self, show=True):
        assert (show != self.IsShown()) # we assume it's only called when changed
        super(SecomStreamsTab, self).Show(show)

        # pause streams when not displayed
        if not show:
            self._streambar_controller.pauseStreams()

    @classmethod
    def get_display_priority(cls, main_data):
        # For SECOM/DELPHI and all simple microscopes
        if main_data.role in ("secom", "delphi", "sem", "optical"):
            return 2
        else:
            return None

    def on_tool_change(self, tool):
        """ Ensure spot position is always defined when using the spot """
        if tool == TOOL_SPOT:
            # Put the spot position at a "good" place if not yet defined
            if self.tab_data_model.spotPosition.value == (None, None):
                roa = self.tab_data_model.roa.value
                if roa == acqstream.UNDEFINED_ROI:
                    # If no ROA => just at the center of the FoV
                    pos = (0.5, 0.5)
                else:  # Otherwise => in the center of the ROI
                    pos = ((roa[0] + roa[2]) / 2, (roa[1] + roa[3]) / 2)

                self.tab_data_model.spotPosition.value = pos
            # TODO: reset the spot position as defined in the spec?
            # Too much reset for the user and not really helpful?
