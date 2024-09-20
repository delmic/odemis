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
import wx

from odemis import model
import odemis.acq.stream as acqstream
import odemis.gui.cont.acquisition as acqcont
from odemis.gui.cont.settings import EBeamBlankerSettingsController
from odemis.gui.cont.stream_bar import SparcStreamsController
import odemis.gui.cont.views as viewcont
import odemis.gui.model as guimod
from odemis.acq import leech
from odemis.acq.stream import SpectrumStream, TemporalSpectrumStream, \
    EMStream, \
    AngularSpectrumStream, CLSettingsStream, ARSettingsStream, MonochromatorSettingsStream, \
    ScannedTemporalSettingsStream
from odemis.gui.comp.viewport import MicroscopeViewport, \
    PlotViewport, TemporalSpectrumViewport
from odemis.gui.conf.data import get_local_vas, get_stream_settings_config
from odemis.gui.cont.tabs.tab import Tab
from odemis.gui.model import TOOL_SPOT, TOOL_ACT_ZOOM_FIT, TOOL_NONE
from odemis.gui.util import call_in_wx_main


class SparcAcquisitionTab(Tab):
    def __init__(self, name, button, panel, main_frame, main_data):
        tab_data = guimod.SparcAcquisitionGUIData(main_data)
        super(SparcAcquisitionTab, self).__init__(name, button, panel, main_frame, tab_data)
        self.set_label("ACQUISITION")

        # Create the streams (first, as SEM viewport needs SEM concurrent stream):
        # * SEM (survey): live stream displaying the current SEM view (full FoV)
        # * Spot SEM: live stream to set e-beam into spot mode
        # * SEM (concurrent): SEM stream used to store SEM settings for final acquisition.
        #           That's tab_data.semStream
        # When one new stream is added, it actually creates two streams:
        # * XXXSettingsStream: for the live view and the settings
        # * MDStream: for the acquisition (view)

        # Only put the VAs that do directly define the image as local, everything
        # else should be global. The advantage is double: the global VAs will
        # set the hardware even if another stream (also using the e-beam) is
        # currently playing, and if the VAs are changed externally, the settings
        # will be displayed correctly (and not reset the values on next play).
        emtvas = set()
        hwemtvas = set()
        for vaname in get_local_vas(main_data.ebeam, main_data.hw_settings_config):
            if vaname in ("resolution", "dwellTime", "scale"):
                emtvas.add(vaname)
            else:
                hwemtvas.add(vaname)

        # This stream is used both for rendering and acquisition
        sem_stream = acqstream.SEMStream(
            "Secondary electrons survey",
            main_data.sed,
            main_data.sed.data,
            main_data.ebeam,
            focuser=main_data.ebeam_focus,
            hwemtvas=hwemtvas,
            hwdetvas=None,
            emtvas=emtvas,
            detvas=get_local_vas(main_data.sed, main_data.hw_settings_config)
        )

        # Check the settings are proper for a survey stream (as they could be
        # left over from spot mode)
        # => full FoV + not too low scale + not too long dwell time
        if hasattr(sem_stream, "emtDwellTime"):
            if sem_stream.emtDwellTime.value > 100e-6:
                sem_stream.emtDwellTime.value = sem_stream.emtDwellTime.clip(10e-6)
        if hasattr(sem_stream, "emtScale"):
            if any(s > 16  for s in sem_stream.emtScale.value):
                sem_stream.emtScale.value = sem_stream.emtScale.clip((16, 16))
            sem_scale = sem_stream.emtScale.value
        else:
            sem_scale = 1, 1
        if hasattr(sem_stream, "emtResolution"):
            max_res = sem_stream.emtResolution.range[1]
            res = max_res[0] // sem_scale[0], max_res[1] // sem_scale[1]
            sem_stream.emtResolution.value = sem_stream.emtResolution.clip(res)

        tab_data.acquisitionStreams.add(sem_stream)  # it should also be saved

        tab_data.fovComp = main_data.ebeam
        # This stream is a bit tricky, because it will play (potentially)
        # simultaneously as another one, and it changes the SEM settings at
        # play and pause.
        # The stream controller takes care of turning on/off the stream when
        # another stream needs it, or the tool mode selects it.
        spot_stream = acqstream.SpotSEMStream("Spot", main_data.sed,
                                              main_data.sed.data, main_data.ebeam)
        tab_data.spotStream = spot_stream
        # TODO: add to tab_data.streams and move the handling to the stream controller?
        tab_data.spotPosition.subscribe(self._onSpotPosition)

        # TODO: when there is an active monochromator stream, copy its dwell time
        # to the spot stream (so that the dwell time is correct). Otherwise, use
        # 0.1s dwell time for the spot stream (affects only the refreshing of
        # position). => The goal is just to reset the dwell time after monochromator
        # is paused? There are easier ways.

        # the SEM acquisition simultaneous to the CCDs
        semcl_stream = acqstream.SEMStream(
            "Secondary electrons concurrent",
            main_data.sed,
            main_data.sed.data,
            main_data.ebeam
        )
        tab_data.semStream = semcl_stream
        tab_data.roa = semcl_stream.roi
        # Force the ROA to be defined by the user on first use
        tab_data.roa.value = acqstream.UNDEFINED_ROI

        tab_data.driftCorrector = leech.AnchorDriftCorrector(semcl_stream.emitter,
                                                             semcl_stream.detector)

        # drift correction is disabled until a ROI is selected
        tab_data.driftCorrector.roi.value = acqstream.UNDEFINED_ROI
        # Set anchor region dwell time to the same value as the SEM survey
        sem_stream.emtDwellTime.subscribe(self._copyDwellTimeToAnchor, init=True)

        # Add the SEM stream to the view
        tab_data.streams.value.append(sem_stream)
        # To make sure the spot mode is stopped when the tab loses focus
        tab_data.streams.value.append(spot_stream)

        viewports = panel.pnl_sparc_grid.viewports

        tab_data.streams.subscribe(self._arrangeViewports, init=True)

        for vp in viewports[:4]:
            assert(isinstance(vp, (MicroscopeViewport, PlotViewport, TemporalSpectrumViewport)))

        # Connect the views
        # TODO: make them different depending on the hardware available?
        #       If so, to what? Does having multiple SEM views help?
        vpv = collections.OrderedDict([
            (viewports[0],
             {"name": "SEM",
              "cls": guimod.ContentView,  # Center on content (instead of stage)
              "stage": main_data.stage,
              "stream_classes": (EMStream, CLSettingsStream),
              }),
            (viewports[1],
             {"name": "Angle-resolved",
              "stream_classes": ARSettingsStream,
              }),
            (viewports[2],
             {"name": "Spectrum",
              "stream_classes": SpectrumStream,
              }),
        ])

        # Depending on HW choose which viewport should initialized. We don't initialize viewports
        # which will never be needed.
        # For the 2x2 view, we need at least 4 viewports, so in case we don't have enough viewports,
        # we fill it up by default with a monochromator viewport
        if main_data.streak_ccd:
            vpv[viewports[3]] = {
                "name": "Temporal Spectrum",
                "stream_classes": TemporalSpectrumStream,
            }
            viewport_br = panel.vp_sparc_ts
        if main_data.isAngularSpectrumSupported():
            vpv[viewports[5]] = {
                "name": "AR Spectrum",
                "stream_classes": AngularSpectrumStream,
            }
            viewport_br = panel.vp_sparc_as
        if main_data.monochromator or main_data.time_correlator or len(vpv) < 4:
            vpv[viewports[4]] = {
                "name": "Temporal Intensity",
                "stream_classes": (MonochromatorSettingsStream, ScannedTemporalSettingsStream),
            }
            viewport_br = panel.vp_sparc_br

        # Hide the viewports which are not at the bottom-right at init
        for vp in (panel.vp_sparc_ts, panel.vp_sparc_as, panel.vp_sparc_br):
            vp.Shown = vp is viewport_br

        # Add connection to SEM hFoV if possible
        if main_data.ebeamControlsMag:
            vpv[viewports[0]]["fov_hw"] = main_data.ebeam
            viewports[0].canvas.fit_view_to_next_image = False

        self.view_controller = viewcont.ViewPortController(tab_data, panel, vpv)

        # Connect the view selection buttons
        buttons = collections.OrderedDict([
            (
                panel.btn_sparc_view_all,
                (None, panel.lbl_sparc_view_all)),
            (
                panel.btn_sparc_view_tl,
                (panel.vp_sparc_tl, panel.lbl_sparc_view_tl)),
            (
                panel.btn_sparc_view_tr,
                (panel.vp_sparc_tr, panel.lbl_sparc_view_tr)),
            (
                panel.btn_sparc_view_bl,
                (panel.vp_sparc_bl, panel.lbl_sparc_view_bl)),
            (
                panel.btn_sparc_view_br,
                (viewport_br, panel.lbl_sparc_view_br)),
        ])

        self._view_selector = viewcont.ViewButtonController(tab_data, panel, buttons, viewports)

        # Toolbar
        self.tb = self.panel.sparc_acq_toolbar
        for t in guimod.TOOL_ORDER:
            if t in tab_data.tool.choices:
                self.tb.add_tool(t, tab_data.tool)
        self.tb.add_tool(TOOL_ACT_ZOOM_FIT, self.view_controller.fitViewToContent)
        # TODO: autofocus tool if there is an ebeam-focus

        tab_data.tool.subscribe(self.on_tool_change)

        # Will show the (pulsed) ebeam blanker settings, if available, otherwise will do nothing
        self._ebeam_blanker_ctrl = EBeamBlankerSettingsController(panel, tab_data)

        # Create Stream Bar Controller
        self._stream_controller = SparcStreamsController(
            tab_data,
            panel.pnl_sparc_streams,
            ignore_view=True,  # Show all stream panels, independent of any selected viewport
            view_ctrl=self.view_controller,
        )

        # The sem stream is always visible, so add it by default
        sem_stream_cont = self._stream_controller.addStream(sem_stream, add_to_view=True)
        sem_stream_cont.stream_panel.show_remove_btn(False)
        sem_stream_cont.stream_panel.show_visible_btn(False)

        # FIXME
        # Display on the SEM live stream panel, the extra settings of the SEM concurrent stream
        # * Drift correction period
        # * Probe current activation & period (if supported)
        # * Scan stage (if supported)
        self.dc_period_ent = sem_stream_cont.add_setting_entry(
            "dcPeriod",
            tab_data.driftCorrector.period,
            None,  # component
            get_stream_settings_config()[acqstream.SEMStream]["dcPeriod"]
        )
        tab_data.driftCorrector.roi.subscribe(self._on_dc_roi, init=True)

        if main_data.pcd:
            # Create a "leech" that we can add/remove to the SEM stream
            self._pcd_acquirer = leech.ProbeCurrentAcquirer(main_data.pcd, main_data.pcd_sel)
            self.pcd_active_ent = sem_stream_cont.add_setting_entry(
                "pcdActive",
                tab_data.pcdActive,
                None,  # component
                get_stream_settings_config()[acqstream.SEMStream]["pcdActive"]
            )
            self.pcdperiod_ent = sem_stream_cont.add_setting_entry(
                "pcdPeriod",
                self._pcd_acquirer.period,
                None,  # component
                get_stream_settings_config()[acqstream.SEMStream]["pcdPeriod"]
            )
            # Drop the top border from the period entry to get it closer
            for si in sem_stream_cont.stream_panel.gb_sizer.GetChildren():
                if si.GetWindow() in (self.pcdperiod_ent.lbl_ctrl, self.pcdperiod_ent.value_ctrl):
                    si.Flag &= ~wx.TOP
            tab_data.pcdActive.subscribe(self._on_pcd_active, init=True)

        # add "Use scan stage" check box if scan_stage is present
        sstage = main_data.scan_stage
        if sstage:
            ssaxes = sstage.axes
            posc = {"x": sum(ssaxes["x"].range) / 2,
                    "y": sum(ssaxes["y"].range) / 2}
            # In case of a 'independent' scan stage, move the scan stage
            # to the center (so that scan has maximum range)
            if main_data.stage.name not in sstage.affects.value:
                sstage.moveAbs(posc)

            self.scan_stage_ent = sem_stream_cont.add_setting_entry(
                "useScanStage",
                tab_data.useScanStage,
                None,  # component
                get_stream_settings_config()[acqstream.SEMStream]["useScanStage"]
            )

            tab_data.useScanStage.subscribe(self._on_use_scan_stage, init=True)

            # draw the limits on the SEM view
            roi = (ssaxes["x"].range[0] - posc["x"],
                   ssaxes["y"].range[0] - posc["y"],
                   ssaxes["x"].range[1] - posc["x"],
                   ssaxes["y"].range[1] - posc["y"])
            panel.vp_sparc_tl.set_stage_limits(roi)

        # On the sparc-simplex, there is no alignment tab, so no way to check
        # the CCD temperature. => add it at the bottom of the SEM stream
        if main_data.role == "sparc-simplex" and model.hasVA(main_data.spectrometer, "temperature"):
            self._ccd_temp_ent = sem_stream_cont.add_setting_entry(
                "ccdTemperature",
                main_data.spectrometer.temperature,
                main_data.spectrometer,
                get_stream_settings_config()[acqstream.SEMStream]["ccdTemperature"]
            )

        main_data.is_acquiring.subscribe(self.on_acquisition)

        self._acquisition_controller = acqcont.SparcAcquiController(
            tab_data,
            panel,
            self._stream_controller
        )

        # Force SEM view fit to content when magnification is updated
        if not main_data.ebeamControlsMag:
            main_data.ebeam.magnification.subscribe(self._onSEMMag)

    @property
    def streambar_controller(self):
        return self._stream_controller

    @property
    def acquisition_controller(self):
        return self._acquisition_controller

    def _arrangeViewports(self, streams):
        """
        Called when the .streams is updated, when a new stream is playing. The .streams is reordered every
        time a new stream is playing. Playing stream becomes the first in the list.
        It picks the last playing stream between the TemporalSpectrumStream and the AngularSpectrumStream.
        """
        # Check if there is one of the stream that matters
        for s in streams:
            if isinstance(s, TemporalSpectrumStream):
                br_view = self.panel.vp_sparc_ts.view
                break
            elif isinstance(s, AngularSpectrumStream):
                br_view = self.panel.vp_sparc_as.view
                break
            elif isinstance(s, (MonochromatorSettingsStream, ScannedTemporalSettingsStream)):  # Monochromator/Time-correlator
                br_view = self.panel.vp_sparc_br.view
                break
        else:  # No need to care
            return

        # Switch the last view to the most fitting one
        self.tab_data_model.visible_views.value[3] = br_view

    def on_tool_change(self, tool):
        """ Ensure spot position is always defined when using the spot """
        if tool == TOOL_SPOT:
            # Put the spot position at a "good" place if not yet defined
            if self.tab_data_model.spotPosition.value == (None, None):
                roa = self.tab_data_model.semStream.roi.value
                if roa == acqstream.UNDEFINED_ROI:
                    # If no ROA => just at the center of the FoV
                    pos = (0.5, 0.5)
                else:  # Otherwise => in the center of the ROI
                    pos = ((roa[0] + roa[2]) / 2, (roa[1] + roa[3]) / 2)

                self.tab_data_model.spotPosition.value = pos
            # TODO: reset the spot position as defined in the spec?
            # Too much reset for the user and not really helpful?

    def _onSpotPosition(self, pos):
        """
        Called when the spot position is changed (via the overlay)
        """
        if None not in pos:
            assert len(pos) == 2
            assert all(0 <= p <= 1 for p in pos)
            # Just use the same value for LT and RB points
            self.tab_data_model.spotStream.roi.value = (pos + pos)

    def _onSEMMag(self, mag):
        """
        Called when user enters a new SEM magnification
        """
        # Restart the stream and fit view to content when we get a new image
        cur_stream = self.tab_data_model.streams.value[0]
        ebeam = self.tab_data_model.main.ebeam
        if cur_stream.is_active.value and cur_stream.emitter is ebeam:
            # Restarting is nice because it will get a new image faster, but
            # the main advantage is that it avoids receiving one last image
            # with the old magnification, which would confuse fit_view_to_next_image.
            cur_stream.is_active.value = False
            cur_stream.is_active.value = True
        self.panel.vp_sparc_tl.canvas.fit_view_to_next_image = True

    @call_in_wx_main
    def on_acquisition(self, is_acquiring):
        # TODO: Make sure nothing can be modified during acquisition

        self._ebeam_blanker_ctrl.enable(not is_acquiring)
        self.tb.enable(not is_acquiring)
        self.panel.vp_sparc_tl.Enable(not is_acquiring)
        # TODO: Leave the canvas accessible, but only forbid moving the stage and
        # if the mpp changes, do not update the horizontalFoV of the e-beam.
        # For now, as a hack, we re-enable the legend to allow changing the merge
        # ratio between SEM and CL.
        if is_acquiring:
            self.panel.vp_sparc_tl.bottom_legend.Enable(True)

        self.panel.btn_sparc_change_file.Enable(not is_acquiring)

    def _copyDwellTimeToAnchor(self, dt):
        """
        Use the sem stream dwell time as the anchor dwell time
        """
        self.tab_data_model.driftCorrector.dwellTime.value = dt

    @call_in_wx_main
    def _on_pcd_active(self, active):
        acq_leeches = self.tab_data_model.semStream.leeches
        if active:
            # ensure the leech is present
            if self._pcd_acquirer not in acq_leeches:
                acq_leeches.append(self._pcd_acquirer)
        else:
            # ensure the leech is not present
            try:
                acq_leeches.remove(self._pcd_acquirer)
            except ValueError:
                pass

        self.pcdperiod_ent.lbl_ctrl.Enable(active)
        self.pcdperiod_ent.value_ctrl.Enable(active)

    def _on_use_scan_stage(self, use):
        if use:
            self.panel.vp_sparc_tl.show_stage_limit_overlay()
        else:
            self.panel.vp_sparc_tl.hide_stage_limit_overlay()

    @call_in_wx_main
    def _on_dc_roi(self, roi):
        """
        Called when the Anchor region changes.
        Used to enable/disable the drift correction period control
        """
        enabled = (roi != acqstream.UNDEFINED_ROI)
        self.dc_period_ent.lbl_ctrl.Enable(enabled)
        self.dc_period_ent.value_ctrl.Enable(enabled)

        # The driftCorrector should be a leech iif drift correction is enabled
        dc = self.tab_data_model.driftCorrector
        sems = self.tab_data_model.semStream
        if enabled:
            if dc not in sems.leeches:
                self.tab_data_model.semStream.leeches.append(dc)
        else:
            try:
                sems.leeches.remove(dc)
            except ValueError:
                pass  # It was already not there

    def Show(self, show=True):
        assert (show != self.IsShown())  # we assume it's only called when changed
        super(SparcAcquisitionTab, self).Show(show)

        # pause streams when not displayed
        if not show:
            self._stream_controller.pauseStreams()
            # Also stop the spot mode (as it's not useful for the spot mode to
            # restart without any stream playing when coming back, and special
            # care would be needed to restart the spotStream in this case)
            if self.tab_data_model.tool.value == TOOL_SPOT:
                self.tab_data_model.tool.value = TOOL_NONE

    def terminate(self):
        # make sure the streams are stopped
        for s in self.tab_data_model.streams.value:
            s.is_active.value = False

    @classmethod
    def get_display_priority(cls, main_data):
        # For SPARCs
        if main_data.role in ("sparc-simplex", "sparc", "sparc2"):
            return 1
        else:
            return None
