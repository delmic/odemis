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
from concurrent.futures import CancelledError
from functools import partial
import logging
import math
import pkg_resources
import time
import wx

from odemis.gui.util.wx_adapter import fix_static_text_clipping

from odemis import model
import odemis.acq.stream as acqstream
import odemis.gui
import odemis.gui.cont.streams as streamcont
import odemis.gui.cont.views as viewcont
import odemis.gui.model as guimod
from odemis.acq.align.autofocus import GetSpectrometerFocusingDetectors
from odemis.acq.align.autofocus import Sparc2AutoFocus, Sparc2ManualFocus
from odemis.gui.conf.data import get_local_vas, get_hw_config
from odemis.gui.conf.util import create_axis_entry
from odemis.gui.cont import settings
from odemis.gui.cont.tabs._constants import MIRROR_ONPOS_RADIUS, MIRROR_POS_PARKED
from odemis.gui.cont.tabs.tab import Tab
from odemis.gui.cont.actuators import ActuatorController
from odemis.gui.util import call_in_wx_main, wxlimit_invocation
from odemis.gui.util.widgets import ProgressiveFutureConnector, AxisConnector
from odemis.util import units, spot, limit_invocation, almost_equal, driver


class Sparc2AlignTab(Tab):
    """
    Tab for the mirror/fiber/lens/ek/streak-camera alignment on the SPARCv2. Note that the basic idea
    is similar to the SPARC(v1), but the actual procedure is entirely different.
    """

    def __init__(self, name, button, panel, main_frame, main_data):
        tab_data = guimod.Sparc2AlignGUIData(main_data)
        super(Sparc2AlignTab, self).__init__(name, button, panel, main_frame, tab_data)
        self.set_label("ALIGNMENT")

        # Typically the actuators are automatically referenced at back-end init.
        # But if that's not the case, let's try to do it now.
        for lens_act in (main_data.lens_mover, main_data.lens_switch):
            if lens_act and not lens_act.referenced.value["x"]:
                logging.info("%s not referenced, will reference it now", lens_act.name)
                f = lens_act.reference({"x"})
                on_ref = partial(self._on_reference_end, comp=lens_act)
                f.add_done_callback(on_ref)

        if main_data.fibaligner:
            # Reference and move the fiber aligner Y to its default position (if
            # it has a favourite position, as the older versions didn't support
            # referencing, so just stayed physically as-is).
            fib_fav_pos = main_data.fibaligner.getMetadata().get(model.MD_FAV_POS_ACTIVE)
            if fib_fav_pos:
                try:
                    refd = main_data.fibaligner.referenced.value
                    not_refd = {a for a in fib_fav_pos.keys() if not refd[a]}
                    if any(not_refd):
                        f = main_data.fibaligner.reference(not_refd)
                        f.add_done_callback(self._moveFibAlignerToActive)
                    else:
                        self._moveFibAlignerToActive()
                except Exception:
                    logging.exception("Failed to move fiber aligner to %s", fib_fav_pos)

        # Documentation text on the right panel for alignment
        self.doc_path = pkg_resources.resource_filename("odemis.gui", "doc/sparc2_header.html")
        panel.html_moi_doc.SetBorders(0)
        panel.html_moi_doc.LoadPage(self.doc_path)

        self._mirror_settings_controller = None
        if model.hasVA(main_data.lens, "configuration"):
            self._mirror_settings_controller = settings.MirrorSettingsController(panel, tab_data)
            self._mirror_settings_controller.enable(False)

        if main_data.streak_ccd:
            self._streak_settings_controller = settings.StreakCamAlignSettingsController(panel, tab_data)
            self.streak_ccd = main_data.streak_ccd
            self.streak_delay = main_data.streak_delay
            self.streak_unit = main_data.streak_unit
            self.streak_lens = main_data.streak_lens

            # !!Note: In order to make sure the default value shown in the GUI corresponds
            # to the correct trigger delay from the MD, we call the setter of the .timeRange VA,
            # which sets the correct value for the .triggerDelay VA from MD-lookup
            # Cannot be done in the driver, as MD from yaml is updated after initialization of HW!!
            self.streak_unit.timeRange.value = self.streak_unit.timeRange.value

        # Create stream & view
        self._stream_controller = streamcont.StreamBarController(
            tab_data,
            panel.pnl_streams,
            locked=True  # streams cannot be hidden/removed and fixed to the current view
        )

        # Create the views.
        vpv = collections.OrderedDict((
            (self.panel.vp_align_lens,
                {
                    "name": "Lens alignment",
                    "stream_classes": acqstream.CameraStream,
                }
            ),
            (self.panel.vp_align_center,
                {
                    "cls": guimod.ContentView,
                    "name": "Center alignment",
                    "stream_classes": acqstream.CameraStream,
                }
            ),
            (self.panel.vp_align_ek,
             {
                 "cls": guimod.ContentView,
                 "name": "Center alignment in EK",
                 "stream_classes": acqstream.AngularSpectrumSettingsStream,
             }
            ),
            (self.panel.vp_align_fiber,
                {
                    "name": "Spectrum average",
                    "stream_classes": acqstream.CameraCountStream,
                }
            ),
            (self.panel.vp_align_streak,
             {
                 "name": "Trigger delay calibration",
                 "stream_classes": acqstream.StreakCamStream,
             }
             ),
        ))

        self.view_controller = viewcont.ViewPortController(tab_data, panel, vpv)
        self.panel.vp_align_lens.view.show_crosshair.value = False
        self.panel.vp_align_center.view.show_crosshair.value = False
        self.panel.vp_align_ek.view.show_crosshair.value = False
        self.panel.vp_align_streak.view.show_crosshair.value = True
        self.panel.vp_align_lens.view.show_pixelvalue.value = False
        self.panel.vp_align_center.view.show_pixelvalue.value = False
        self.panel.vp_align_ek.view.show_pixelvalue.value = False
        self.panel.vp_align_streak.view.show_pixelvalue.value = True

        # The streams:
        # * Alignment/AR CCD (ccd): Used to show CL spot during the alignment
        #   of the lens1, lens2, _and_ to show the mirror shadow in center alignment.
        # * spectrograph line (specline): used for focusing/showing a (blue)
        #   line in lens alignment.
        # * Alignment/AR CCD (mois): Also show CL spot, but used in the mirror
        #   alignment mode with MoI and spot intensity info on the panel.
        # * Spectrum count (speccnt): Used to show how much light is received
        #   by the spectrometer over time (30s).
        # * Angular Spectrum Alignment (as): For EK centering. Provides lines
        #   to center the pattern in order to correct the chromatic aberration.
        # * ebeam spot (spot): Used to force the ebeam to spot mode in lens
        #   and center alignment.
        # Note: the mirror alignment used a MomentOfInertia stream, it's
        #   supposed to make things easier (and almost automatic) but it doesn't
        #   work in all cases/samples. So we use now just rely on the direct CCD
        #   view.

        # TODO: have a special stream that does CCD + ebeam spot? (to avoid the ebeam spot)

        # Force a spot at the center of the FoV
        # Not via stream controller, so we can avoid the scheduler
        spot_stream = acqstream.SpotSEMStream("SpotSEM", main_data.sed,
                                              main_data.sed.data, main_data.ebeam)
        spot_stream.should_update.value = True
        self._spot_stream = spot_stream

        self._ccd_stream = None
        self._as_stream = None
        # The ccd stream panel entry object is kept as attribute
        # to enable/disable ccd stream panel during focus mode
        self._ccd_spe = None

        # TODO: do a similar procedure by creating an AR spectrum stream (???)
        if main_data.ccd:
            # Force the "temperature" VA to be displayed by making it a hw VA
            hwdetvas = set()
            if model.hasVA(main_data.ccd, "temperature"):
                hwdetvas.add("temperature")
            ccd_stream = acqstream.CameraStream(
                                "Angle-resolved sensor",
                                main_data.ccd,
                                main_data.ccd.data,
                                emitter=None,
                                # focuser=ccd_focuser, # no focus on right drag, would be too easy to change mistakenly
                                hwdetvas=hwdetvas,
                                detvas=get_local_vas(main_data.ccd, main_data.hw_settings_config),
                                forcemd={model.MD_POS: (0, 0),  # Just in case the stage is there
                                         model.MD_ROTATION: 0},  # Force the CCD as-is
                                acq_type=model.MD_AT_AR,  # For the merge slider icon
                                )
            self._setFullFoV(ccd_stream, (2, 2))
            self._ccd_stream = ccd_stream

            self._ccd_spe = self._stream_controller.addStream(ccd_stream,
                                add_to_view=self.panel.vp_align_lens.view)
            self._ccd_spe.stream_panel.flatten()

            self._addMoIEntries(self._ccd_spe.stream_panel)
            ccd_stream.image.subscribe(self._onNewMoI)

            # To activate the SEM spot when the CCD plays
            ccd_stream.should_update.subscribe(self._on_ccd_stream_play)
        elif main_data.sp_ccd:
            # Hack: if there is no CCD, let's display at least the sp-ccd.
            # It might or not be useful. At least we can show the temperature.
            hwdetvas = set()
            if model.hasVA(main_data.sp_ccd, "temperature"):
                hwdetvas.add("temperature")
            ccd_stream = acqstream.CameraStream(
                                "Alignment CCD for mirror",
                                main_data.sp_ccd,
                                main_data.sp_ccd.data,
                                emitter=None,
                                hwdetvas=hwdetvas,
                                detvas=get_local_vas(main_data.sp_ccd, main_data.hw_settings_config),
                                forcemd={model.MD_POS: (0, 0),  # Just in case the stage is there
                                         model.MD_ROTATION: 0}  # Force the CCD as-is
                                )
            # Make sure the binning is not crazy (especially can happen if CCD is shared for spectrometry)
            self._setFullFoV(ccd_stream, (2, 2))
            self._ccd_stream = ccd_stream

            self._ccd_spe = self._stream_controller.addStream(ccd_stream,
                                add_to_view=self.panel.vp_align_lens.view)
            self._ccd_spe.stream_panel.flatten()

            # To activate the SEM spot when the CCD plays
            ccd_stream.should_update.subscribe(self._on_ccd_stream_play)
        else:
            self.panel.btn_bkg_acquire.Show(False)

        # For running autofocus (can only one at a time)
        self._autofocus_f = model.InstantaneousFuture()
        self._autofocus_align_mode = None  # Which mode is autofocus running on
        self._pfc_autofocus = None  # For showing the autofocus progress

        # Focuser on stream so menu controller believes it's possible to autofocus.
        if main_data.focus and main_data.ccd and main_data.ccd.name in main_data.focus.affects.value:
            ccd_focuser = main_data.focus
        else:
            ccd_focuser = None
        # TODO: handle if there are two spectrometers with focus (but for now,
        # there is no such system)
        if ccd_focuser:
            # Focus position axis -> AxisConnector
            z = main_data.focus.axes["z"]
            self.panel.slider_focus.SetRange(z.range[0], z.range[1])
            self._ac_focus = AxisConnector("z", main_data.focus, self.panel.slider_focus,
                                           events=wx.EVT_SCROLL_CHANGED)

            # Bind autofocus (the complex part is to get the menu entry working too)
            self.panel.btn_autofocus.Bind(wx.EVT_BUTTON, self._onClickFocus)
            tab_data.autofocus_active.subscribe(self._onAutofocus)

        else:
            self.panel.pnl_focus.Show(False)

        # Add autofocus in case there is a focusable spectrometer after the optical fiber.
        # Pick the focuser which affects at least one component common with the
        # fiber aligner.
        fib_focuser = None
        if main_data.fibaligner:
            aligner_affected = set(main_data.fibaligner.affects.value)
            for focuser in (main_data.spec_ded_focus, main_data.focus):
                if focuser is None:
                    continue
                # Is there at least one component affected by the focuser which
                # is also affected by the fibaligner?
                if set(focuser.affects.value) & aligner_affected:
                    fib_focuser = focuser
                    break

        if fib_focuser:
            if ccd_focuser == fib_focuser:
                # a ccd can never be after an optical fiber (only sp-ccd)
                logging.warning("Focus %s seems to affect the 'ccd' but also be after "
                                "the optical fiber ('fiber-aligner').", fib_focuser.name)
            # Bind autofocus
            # Note: we can use the same functions as for the ccd_focuser, because
            # we'll distinguish which autofocus to run based on the align_mode.
            self.panel.btn_fib_autofocus.Bind(wx.EVT_BUTTON, self._onClickFocus)
            tab_data.autofocus_active.subscribe(self._onAutofocus)
        else:
            self.panel.pnl_fib_focus.Show(False)

        # Manual focus mode initialization
        # Add all the `blue` streams, one for each detector to adjust the focus
        self._focus_streams = []
        # Future object to keep track of turning on/off the manual focus mode
        self._mf_future = model.InstantaneousFuture()
        self._enableFocusComponents(manual=False, ccd_stream=True)

        if ccd_focuser:  # TODO: handle fib_focuser as well
            # Create a focus stream for each Spectrometer detector
            self._focus_streams = self._createFocusStreams(ccd_focuser, main_data.hw_settings_config)
            for focus_stream in self._focus_streams:
                # Add the stream to the stream bar controller
                # so that it's displayed with the default 0.3 merge ratio
                self._stream_controller.addStream(focus_stream, visible=False,
                                                  add_to_view=False)

                # Remove the stream from the focused view initially
                self.tab_data_model.focussedView.value.removeStream(focus_stream)
                # Subscribe to stream's should_update VA in order to view/hide it
                focus_stream.should_update.subscribe(self._ensureOneFocusStream)

            # Bind spectrograph available gratings to the focus panel gratings combobox
            create_axis_entry(self, 'grating', main_data.spectrograph)

            if self._focus_streams:
                # Set the focus panel detectors combobox items to the focus streams detectors
                self.panel.cmb_focus_detectors.Items = [s.detector.name for s in self._focus_streams]
                self.panel.cmb_focus_detectors.Bind(wx.EVT_COMBOBOX, self._onFocusDetectorChange)
                self.panel.cmb_focus_detectors.SetSelection(0)

        self._ts_stream = None
        if main_data.streak_ccd:
            # Don't show the time range, as it's done by the StreakCamAlignSettingsController
            streak_unit_vas = (get_local_vas(main_data.streak_unit, main_data.hw_settings_config)
                               -{"timeRange"})
            tsStream = acqstream.StreakCamStream(
                                "Calibration trigger delay for streak camera",
                                main_data.streak_ccd,
                                main_data.streak_ccd.data,
                                main_data.streak_unit,
                                main_data.streak_delay,
                                emitter=None,
                                detvas=get_local_vas(main_data.streak_ccd, main_data.hw_settings_config),
                                streak_unit_vas=streak_unit_vas,
                                forcemd={model.MD_POS: (0, 0),  # Just in case the stage is there
                                         model.MD_ROTATION: 0},  # Force the CCD as-is
                                )

            self._setFullFoV(tsStream, (2, 2))
            self._ts_stream = tsStream

            streak = self._stream_controller.addStream(tsStream,
                             add_to_view=self.panel.vp_align_streak.view)
            streak.stream_panel.flatten()  # No need for the stream name

            # Add the standard spectrograph axes (wavelength, grating, slit-in),
            # but in-between inserts the special slit-in-big axis, which is a
            # separate hardware.
            # It allows to switch the slit of the spectrograph between "fully
            # opened" and opened according to the slit-in (aka "closed").
            def add_axis(axisname, comp):
                hw_conf = get_hw_config(comp, main_data.hw_settings_config)
                streak.add_axis_entry(axisname, comp, hw_conf.get(axisname))

            add_axis("grating", main_data.spectrograph)
            add_axis("wavelength", main_data.spectrograph)
            add_axis("x", main_data.slit_in_big)
            add_axis("slit-in", main_data.spectrograph)

            # To activate the SEM spot when the camera plays
            # (ebeam centered in image)
            tsStream.should_update.subscribe(self._on_ccd_stream_play)

        if "ek-align" in tab_data.align_mode.choices:
            detvas = get_local_vas(main_data.ccd, main_data.hw_settings_config)
            detvas.remove('binning')
            detvas.remove('exposureTime')
            spectrometer = self._find_spectrometer(main_data.ccd)

            # Note: the stream takes care of flipping the image if necessary to ensure
            # that the lower wavelengths are on the left.
            as_stream = acqstream.AngularSpectrumAlignmentStream(
                "AR Spectrum",
                main_data.ccd,
                main_data.ccd.data,
                main_data.ebeam,
                spectrometer,
                main_data.spectrograph,
                forcemd={model.MD_POS: (0, 0),  # Just in case the stage is there
                         model.MD_ROTATION: 0},  # Force the CCD as-is
                analyzer=main_data.pol_analyzer,
                detvas=detvas,
            )
            as_panel = self._stream_controller.addStream(as_stream,
                                                         add_to_view=self.panel.vp_align_ek.view)

            # Add the standard spectrograph axes (wavelength, grating, slit-in).

            def add_axis(axisname, comp, label=None):
                if comp is None:
                    return
                hw_conf = get_hw_config(comp, main_data.hw_settings_config)
                axis_conf = hw_conf.get(axisname, {})
                if label:
                    axis_conf = axis_conf.copy()
                    axis_conf["label"] = label
                as_panel.add_axis_entry(axisname, comp, axis_conf)

            add_axis("grating", main_data.spectrograph)
            add_axis("wavelength", main_data.spectrograph)
            add_axis("slit-in", main_data.spectrograph)
            add_axis("band", main_data.light_filter, "Filter")

            as_stream.should_update.subscribe(self._on_ccd_stream_play)

            self._as_stream = as_stream

            self.panel.vp_align_ek.view.addStream(as_stream)
            self.ek_ol = self.panel.vp_align_ek.ek_ol
            self.ek_ol.create_ek_mask(main_data.ccd, tab_data)
            # The mirror dimensions as set via _onMirrorDimensions()
            self.panel.vp_align_ek.show_ek_overlay()

        if "center-align" in tab_data.align_mode.choices:
            # The center align view share the same CCD stream (and settings)
            self.panel.vp_align_center.view.addStream(ccd_stream)

            # Connect polePosition of lens to mirror overlay (via the polePositionPhysical VA)
            self.mirror_ol = self.panel.vp_align_center.mirror_ol
            self.lens = main_data.lens
            self.lens.focusDistance.subscribe(self._onMirrorDimensions, init=True)
            self.mirror_ol.set_hole_position(tab_data.polePositionPhysical)
            self.panel.vp_align_center.show_mirror_overlay()

            # TODO: As this view uses the same stream as lens-align, the view
            # will be updated also when lens-align is active, which increases
            # CPU load, without reason. Ideally, the view controller/canvas
            # should be clever enough to detect this and not actually cause a
            # redraw.

        # chronograph of spectrometer if "fiber-align" mode is present
        self._speccnt_stream = None
        self._fbdet2 = None
        if "fiber-align" in tab_data.align_mode.choices:
            # Need to pick the right/best component which receives light via the fiber
            photods = []
            fbaffects = main_data.fibaligner.affects.value
            # First try some known, good and reliable detectors
            for d in (main_data.spectrometers + main_data.photo_ds):
                if d is not None and d.name in fbaffects:
                    photods.append(d)

            if not photods:
                # Take the first detector
                for dname in fbaffects:
                    try:
                        d = model.getComponent(name=dname)
                    except LookupError:
                        logging.warning("Failed to find component %s affected by fiber-aligner", dname)
                        continue
                    if hasattr(d, "data") and isinstance(d.data, model.DataFlowBase):
                        photods.append(d)

            if photods:
                logging.debug("Using %s as fiber alignment detector", photods[0].name)
                speccnts = acqstream.CameraCountStream("Spectrum average",
                                       photods[0],
                                       photods[0].data,
                                       emitter=None,
                                       detvas=get_local_vas(photods[0], main_data.hw_settings_config),
                                       )
                speccnt_spe = self._stream_controller.addStream(speccnts,
                                    add_to_view=self.panel.vp_align_fiber.view)
                # Special for the time-correlator: some of its settings also affect
                # the photo-detectors.
                if main_data.time_correlator:
                    if model.hasVA(main_data.time_correlator, "syncDiv"):
                        speccnt_spe.add_setting_entry("syncDiv",
                                                      main_data.time_correlator.syncDiv,
                                                      main_data.time_correlator,
                                                      main_data.hw_settings_config["time-correlator"].get("syncDiv")
                                                      )

                if main_data.tc_od_filter:
                    speccnt_spe.add_axis_entry("density", main_data.tc_od_filter)
                    speccnt_spe.add_axis_entry("band", main_data.tc_filter)
                speccnt_spe.stream_panel.flatten()
                self._speccnt_stream = speccnts
                speccnts.should_update.subscribe(self._on_ccd_stream_play)

                if len(photods) > 1 and photods[0] in main_data.photo_ds and photods[1] in main_data.photo_ds:
                    self._fbdet2 = photods[1]
                    _, self._det2_cnt_ctrl = speccnt_spe.stream_panel.add_text_field("Detector 2", "", readonly=True)
                    self._det2_cnt_ctrl.SetForegroundColour("#FFFFFF")
                    f = self._det2_cnt_ctrl.GetFont()
                    f.PointSize = 12
                    self._det2_cnt_ctrl.SetFont(f)
                    speccnts.should_update.subscribe(self._on_fbdet1_should_update)
            else:
                logging.warning("Fiber-aligner present, but found no detector affected by it.")

        if main_data.light_aligner and not ("light_aligner", "z") in tab_data.axes:
            self.panel.btn_p_light_aligner_z.Show(False)
            self.panel.lbl_p_light_aligner_z.Show(False)
            self.panel.btn_m_light_aligner_z.Show(False)
            self.panel.lbl_m_light_aligner_z.Show(False)
            self.panel.Layout()

        if main_data.spec_switch:
            self.panel.btn_spec_switch_retract.Bind(wx.EVT_BUTTON, self._on_spec_switch_btn)
            self.panel.btn_spec_switch_engage.Bind(wx.EVT_BUTTON, self._on_spec_switch_btn)

            # move the spec_switch mirror to the default (retracted) position
            spec_switch_data = main_data.spec_switch.getMetadata()
            spec_switch_xpos = main_data.spec_switch.position.value["x"]
            spec_switch_xmd_deactive = spec_switch_data[model.MD_FAV_POS_DEACTIVE]["x"]
            spec_switch_xmd_active = spec_switch_data[model.MD_FAV_POS_ACTIVE]["x"]

            # if the spec_switch mirror is not positioned either on ACTIVE or
            # DEACTIVE position move it to the default (DEACTIVE) position
            if ((not almost_equal(spec_switch_xpos, spec_switch_xmd_deactive)) and
                    (not almost_equal(spec_switch_xpos, spec_switch_xmd_active))):
                # execute a move without tracking using a progress bar so
                # no update to the GUI when the alignment tab is hidden
                self._spec_switch_f = main_data.spec_switch.moveAbs({"x": spec_switch_xmd_deactive})

            # future and progress connector for tracking the progress of the gauge when moving
            self._pfc_spec_switch = None

        # Switch between alignment modes
        # * lens-align: first auto-focus spectrograph, then align lens1
        # * mirror-align: move x, y of mirror with moment of inertia feedback
        # * center-align: find the center of the AR image using a mirror mask
        # * lens2-align: first auto-focus spectrograph, then align lens 2
        # * ek-align: define the pole_pos and edges of EK imaging using the EK overlay/mask
        # * fiber-align: move x, y of the fibaligner with mean of spectrometer as feedback
        # * streak-align: vertical and horizontal alignment of the streak camera,
        #                 change of the mag due to changed input optics and
        #                 calibration of the trigger delays for temporal resolved acq
        # * light-in-align: engage or retract the folding mirror to switch to
        #                     internal or external spectrograph
        self._alignbtn_to_mode = collections.OrderedDict((
            (panel.btn_align_lens, "lens-align"),
            (panel.btn_align_mirror, "mirror-align"),
            (panel.btn_align_lens2, "lens2-align"),
            (panel.btn_align_centering, "center-align"),
            (panel.btn_align_ek, "ek-align"),
            (panel.btn_align_fiber, "fiber-align"),
            (panel.btn_align_streakcam, "streak-align"),
            (panel.btn_align_light_in, "light-in-align"),
        ))

        # The GUI mode to the optical path mode (see acq.path.py)
        self._mode_to_opm = {
            "mirror-align": "mirror-align",
            "lens-align": "mirror-align",  # if autofocus is needed: spec-focus (first)
            "lens2-align": "lens2-align",  # if autofocus is needed: spec-focus (first)
            "center-align": "ar",
            "ek-align": "ek-align",
            "fiber-align": "fiber-align",
            "streak-align": "streak-align",
            "light-in-align": "light-in-align",
        }
        # Note: ActuatorController automatically hides the unnecessary alignment panels, based on the axes present.
        for btn, mode in list(self._alignbtn_to_mode.items()):
            if mode in tab_data.align_mode.choices:
                btn.Bind(wx.EVT_BUTTON, self._onClickAlignButton)
            else:
                btn.Destroy()
                del self._alignbtn_to_mode[btn]

        self._layoutModeButtons()
        tab_data.align_mode.subscribe(self._onAlignMode)

        self.panel.btn_manual_focus.Bind(wx.EVT_BUTTON, self._onManualFocus)

        if main_data.brightlight:
            # Make sure the calibration light is off
            main_data.brightlight.power.value = main_data.brightlight.power.range[0]

        # Bind moving buttons & keys
        self._actuator_controller = ActuatorController(tab_data, panel, "")
        self._actuator_controller.bind_keyboard(panel)

        # TODO: warn user if the mirror stage is too far from the official engaged
        # position. => The S axis will fail!

        # Bind background acquisition
        self.panel.btn_bkg_acquire.Bind(wx.EVT_BUTTON, self._onBkgAcquire)
        self._min_bkg_date = None
        self._bkg_im = None
        # TODO: Have a warning text to indicate there is no background image?
        # TODO: Auto remove the background when the image shape changes?
        # TODO: Use a toggle button to show the background is in use or not?

    def _on_fbdet1_should_update(self, should_update):
        if should_update:
            self._fbdet2.data.subscribe(self._on_fbdet2_data)
        else:
            self._fbdet2.data.unsubscribe(self._on_fbdet2_data)

    @wxlimit_invocation(0.5)
    def _on_fbdet2_data(self, df, data):
        self._det2_cnt_ctrl.SetValue("%s" % data[-1])

    def _layoutModeButtons(self):
        """
        Positions the mode buttons in a nice way: on one line if they fit,
         otherwise on two lines.
        """
        # If 3 buttons or less, keep them all on a single line, otherwise,
        # spread on two lines. (Works up to 6 buttons)
        btns = list(self._alignbtn_to_mode.keys())
        if len(btns) == 1:
            btns[0].Show(False)  # No other choice => no need to choose
            return
        elif len(btns) == 4:
            # Spread over two columns
            width = 2
        else:
            width = 3

        # Position each button at the next position in the grid
        gb_sizer = btns[0].Parent.GetSizer().GetChildren()[0].GetSizer()
        for i, btn in enumerate(btns):
            pos = (i // width, i % width)
            gb_sizer.SetItemPosition(btn, pos)

    def _setFullFoV(self, stream, binning=(1, 1)):
        """
        Change the settings of the stream to ensure it acquires a full image (FoV)
        stream (CameraStream): CCD stream (with .detResolution)
        binning (int, int): the binning to use (if the camera supports it)
        """
        if hasattr(stream, "detBinning"):
            stream.detBinning.value = stream.detBinning.clip(binning)
            b = stream.detBinning.value
        else:
            b = (1, 1)

        if hasattr(stream, "detResolution"):
            max_res = stream.detResolution.range[1]
            res = max_res[0] // b[0], max_res[1] // b[1]
            stream.detResolution.value = stream.detResolution.clip(res)

    def _addMoIEntries(self, cont):
        """
        Add the MoI value entry and spot size entry
        :param stream_cont: (Container aka StreamPanel)

        """
        # the "MoI" value bellow the streams
        lbl_moi, txt_moi = cont.add_text_field("Moment of inertia", readonly=True)
        tooltip_txt = "Moment of inertia at the center (smaller is better)"
        lbl_moi.SetToolTip(tooltip_txt)
        txt_moi.SetToolTip(tooltip_txt)
        # Change font size and colour
        f = txt_moi.GetFont()
        f.PointSize = 12
        txt_moi.SetFont(f)
        txt_moi.SetForegroundColour(odemis.gui.FG_COLOUR_MAIN)
        self._txt_moi = txt_moi

        lbl_ss, txt_ss = cont.add_text_field("Spot intensity", readonly=True)
        tooltip_txt = "Spot intensity at the center (bigger is better)"
        lbl_ss.SetToolTip(tooltip_txt)
        txt_ss.SetToolTip(tooltip_txt)
        # Change font size and colour
        f = txt_ss.GetFont()
        f.PointSize = 12
        txt_ss.SetFont(f)
        txt_ss.SetForegroundColour(odemis.gui.FG_COLOUR_MAIN)
        self._txt_ss = txt_ss

    def _on_reference_end(self, f, comp):
        """
        Called at the end of a referencing move, to report any error.
        f (Future): the Future corresponding to the referencing
        comp (Component): component which was referencing
        """
        try:
            f.result()
        except Exception as ex:
            logging.error("Referencing of %s failed: %s", comp.name, ex)

    def _moveFibAlignerToActive(self, f=None):
        """
        Move the fiber aligner to its default active position
        f (future): future of the referencing
        """
        if f:
            f.result()  # to fail & log if the referencing failed

        fiba = self.tab_data_model.main.fibaligner
        try:
            fpos = fiba.getMetadata()[model.MD_FAV_POS_ACTIVE]
        except KeyError:
            logging.exception("Fiber aligner actuator has no metadata FAV_POS_ACTIVE")
            return
        fiba.moveAbs(fpos)

    def _onClickAlignButton(self, evt):
        """ Called when one of the Mirror/Optical fiber button is pushed

        Note: in practice they can never be unpushed by the user, so this happens
          only when the button is toggled on.

        """

        btn = evt.GetEventObject()
        if not btn.GetToggle():
            logging.warning("Got event from button being untoggled")
            return

        try:
            mode = self._alignbtn_to_mode[btn]
        except KeyError:
            logging.warning("Unknown button %s pressed", btn)
            return

        # un-toggling the other button will be done when the VA is updated
        self.tab_data_model.align_mode.value = mode

    @call_in_wx_main
    def _onAlignMode(self, mode):
        """
        Called when the align_mode changes (because the user selected a new one)
        Takes care of setting the right optical path, and updating the GUI widgets
         displayed
        mode (str): the new alignment mode
        """
        # Ensure the toggle buttons are correctly set
        for btn, m in self._alignbtn_to_mode.items():
            btn.SetToggle(mode == m)

        if not self.IsShown():
            # Shouldn't happen, but for safety, double check
            logging.warning("Alignment mode changed while alignment tab not shown")
            return

        # Disable controls/streams which are useless (to guide the user)
        self._stream_controller.pauseStreams()
        # Cancel autofocus (if it happens to run)
        self.tab_data_model.autofocus_active.value = False
        # Disable manual focus components and cancel already running procedure
        self.panel.btn_manual_focus.SetValue(False)
        self._enableFocusComponents(manual=False, ccd_stream=True)
        self._mf_future.cancel()

        main = self.tab_data_model.main

        # Things to do at the end of a mode
        if mode != "fiber-align":
            if main.spec_sel:
                main.spec_sel.position.unsubscribe(self._onSpecSelPos)
            if main.fibaligner:
                main.fibaligner.position.unsubscribe(self._onFiberPos)
        if mode != "lens-align":
            if main.lens_mover:
                main.lens_mover.position.unsubscribe(self._onLensPos)
        if mode != "lens2-align":
            if main.lens_switch:
                main.lens_switch.position.unsubscribe(self._onLensSwitchPos)
        if mode != "center-align":
            if main.light_aligner:
                main.light_aligner.position.unsubscribe(self._onLightAlignPos)
        if mode != "light-in-align":
            if main.spec_switch:
                main.spec_switch.position.unsubscribe(self._onSpecSwitchPos)
            if main.light_aligner:
                main.light_aligner.position.unsubscribe(self._onLightAlignPos)

        # This is running in a separate thread (future). In most cases, no need to wait.
        op_mode = self._mode_to_opm[mode]
        if mode == "fiber-align" and self._speccnt_stream:
            # In case there are multiple detectors after the fiber-aligner, it's
            # necessary to pass the actual detector that we want.
            f = main.opm.setPath(op_mode, self._speccnt_stream.detector)
        else:
            f = main.opm.setPath(op_mode)
        f.add_done_callback(self._on_align_mode_done)

        # Focused view must be updated before the stream to play is changed,
        # as the scheduler automatically adds the stream to the current view.
        # The scheduler also automatically pause all the other streams.
        if mode == "lens-align":
            self.tab_data_model.focussedView.value = self.panel.vp_align_lens.view
            if self._ccd_stream:
                self._ccd_stream.should_update.value = True
            if self._mirror_settings_controller:
                self._mirror_settings_controller.enable(False)
            self.panel.pnl_mirror.Show(True)
            self.panel.pnl_lens_mover.Show(True)
            self.panel.pnl_lens_mover.Enable(False)  # Will be enabled once the lens is at the correct place
            self.panel.pnl_lens_switch.Show(False)
            self.panel.pnl_focus.Show(True)
            self.panel.gauge_autofocus.Enable(True)
            self.panel.btn_autofocus.Enable(True)
            self.panel.pnl_fibaligner.Show(False)
            self.panel.pnl_streak.Show(False)
            self.panel.pnl_spec_switch.Show(False)
            self.panel.pnl_light_aligner.Show(False)

            self.panel.pnl_moi_settings.Show(True)
            self.panel.btn_bkg_acquire.Enable(True)
            # TODO: in this mode, if focus change, update the focus image once
            # (by going to spec-focus mode, turning the light, and acquiring an
            # AR image). Problem is that it takes about 10s.
            f.add_done_callback(self._on_lens_align_done)
        elif mode == "mirror-align":
            self.tab_data_model.focussedView.value = self.panel.vp_align_lens.view
            if self._ccd_stream:
                self._ccd_stream.should_update.value = True
            if self._mirror_settings_controller:
                self._mirror_settings_controller.enable(False)
            self.panel.pnl_mirror.Show(True)
            self.panel.pnl_lens_mover.Show(False)
            self.panel.pnl_lens_switch.Show(False)
            self.panel.pnl_focus.Show(False)
            self.panel.pnl_fibaligner.Show(False)
            self.panel.pnl_streak.Show(False)
            self.panel.pnl_spec_switch.Show(False)
            self.panel.pnl_light_aligner.Show(False)

            self.panel.pnl_moi_settings.Show(True)
            self.panel.btn_bkg_acquire.Enable(True)
        elif mode == "lens2-align":
            self.tab_data_model.focussedView.value = self.panel.vp_align_lens.view
            self._ccd_stream.should_update.value = True
            if self._mirror_settings_controller:
                self._mirror_settings_controller.enable(False)
            self.panel.pnl_mirror.Show(False)
            self.panel.pnl_lens_mover.Show(False)
            self.panel.pnl_lens_switch.Show(True)
            self.panel.pnl_lens_switch.Enable(False)  # Will be enabled once the lens is at the correct place
            self.panel.pnl_focus.Show(True)
            self.panel.gauge_autofocus.Enable(True)
            self.panel.btn_autofocus.Enable(True)
            self.panel.pnl_fibaligner.Show(False)
            self.panel.pnl_streak.Show(False)
            self.panel.pnl_spec_switch.Show(False)
            self.panel.pnl_light_aligner.Show(False)

            self.panel.pnl_moi_settings.Show(False)
            # TODO: same as lens-align after focus change
            f.add_done_callback(self._on_lens_switch_align_done)
        elif mode == "center-align":
            self.tab_data_model.focussedView.value = self.panel.vp_align_center.view
            self._ccd_stream.should_update.value = True
            if self._mirror_settings_controller:
                self._mirror_settings_controller.enable(True)
            self.panel.pnl_mirror.Show(False)
            self.panel.pnl_lens_mover.Show(False)
            self.panel.pnl_lens_switch.Show(False)
            self.panel.pnl_focus.Show(False)
            self.panel.pnl_fibaligner.Show(False)
            self.panel.pnl_streak.Show(False)
            self.panel.pnl_spec_switch.Show(False)
            # If light-aligner available, allow to adjust it in this view too,
            # as the lens 2 is active, which allows to further align the light input.
            if main.light_aligner:
                self.panel.pnl_light_aligner.Show(True)
                main.light_aligner.position.subscribe(self._onLightAlignPos)
            else:
                self.panel.pnl_light_aligner.Show(False)

            self.panel.pnl_moi_settings.Show(True)
            # Shows the "Acquire/remove background" button, so that if a background has been previously
            # acquired it can be removed. As the optical path is changed, the background can easily
            # be different.
            # TODO: use a different stream from the one in lens-align, so that they don't share the
            # same background data.
            self.panel.btn_bkg_acquire.Enable(True)
        elif mode == "ek-align":
            self.tab_data_model.focussedView.value = self.panel.vp_align_ek.view
            self._as_stream.should_update.value = True
            if self._mirror_settings_controller:
                self._mirror_settings_controller.enable(False)
            self.panel.pnl_mirror.Show(False)
            self.panel.pnl_lens_mover.Show(False)
            self.panel.pnl_lens_switch.Show(False)
            self.panel.pnl_focus.Show(False)
            self.panel.pnl_fibaligner.Show(False)
            self.panel.pnl_streak.Show(False)
            self.panel.pnl_spec_switch.Show(False)
            self.panel.pnl_light_aligner.Show(False)

            self.panel.pnl_moi_settings.Show(False)
        elif mode == "fiber-align":
            self.tab_data_model.focussedView.value = self.panel.vp_align_fiber.view
            if self._speccnt_stream:
                self._speccnt_stream.should_update.value = True
            if self._mirror_settings_controller:
                self._mirror_settings_controller.enable(False)
            self.panel.pnl_mirror.Show(False)
            self.panel.pnl_lens_mover.Show(False)
            self.panel.pnl_lens_switch.Show(False)
            self.panel.pnl_focus.Show(False)
            self.panel.pnl_fibaligner.Show(True)
            # Disable the buttons until the fiber box is ready
            self.panel.btn_m_fibaligner_x.Enable(False)
            self.panel.btn_p_fibaligner_x.Enable(False)
            self.panel.btn_m_fibaligner_y.Enable(False)
            self.panel.btn_p_fibaligner_y.Enable(False)
            self.panel.pnl_spec_switch.Show(False)
            self.panel.pnl_light_aligner.Show(False)
            self.panel.pnl_streak.Show(False)

            self.panel.pnl_moi_settings.Show(False)

            f.add_done_callback(self._on_fibalign_done)
        elif mode == "streak-align":
            self.tab_data_model.focussedView.value = self.panel.vp_align_streak.view
            if self._ts_stream:
                self._ts_stream.should_update.value = True
                self._ts_stream.auto_bc.value = False  # default manual brightness/contrast
                self._ts_stream.intensityRange.value = self._ts_stream.intensityRange.clip((100, 1000))  # default range
            if self._mirror_settings_controller:
                self._mirror_settings_controller.enable(False)
            self.panel.pnl_mirror.Show(False)
            self.panel.pnl_lens_mover.Show(False)
            self.panel.pnl_lens_switch.Show(False)
            self.panel.pnl_focus.Show(True)
            self.panel.btn_autofocus.Enable(False)
            self.panel.gauge_autofocus.Enable(False)
            self.panel.pnl_fibaligner.Show(False)
            self.panel.pnl_streak.Show(True)
            self.panel.pnl_spec_switch.Show(False)
            self.panel.pnl_light_aligner.Show(False)

            self.panel.pnl_moi_settings.Show(False)
        elif mode == "light-in-align":
            self.tab_data_model.focussedView.value = self.panel.vp_align_lens.view  # allows to see the focused slit line
            self._ccd_stream.should_update.value = True
            if self._mirror_settings_controller:
                self._mirror_settings_controller.enable(False)
            self.panel.pnl_mirror.Show(False)
            self.panel.pnl_lens_mover.Show(False)
            self.panel.pnl_lens_switch.Show(False)
            if main.mirror:
                self.panel.pnl_focus.Show(False)
            else:
                # If no mirror => ELIM => allow to focus, but only manually
                self.panel.pnl_focus.Show(True)
                self.panel.btn_autofocus.Enable(False)
                self.panel.gauge_autofocus.Enable(False)
            self.panel.pnl_fibaligner.Show(False)
            self.panel.pnl_streak.Show(False)
            self.panel.pnl_light_aligner.Show(True)
            if main.spec_switch:
                self.panel.pnl_spec_switch.Show(True)
                self.panel.pnl_spec_switch.Enable(False)  # Wait until the spec-switch is engaged
            else:
                self.panel.pnl_spec_switch.Show(False)

            self.panel.pnl_moi_settings.Show(False)
            f.add_done_callback(self._on_light_in_align_done)
        else:
            raise ValueError("Unknown alignment mode %s!" % mode)

        # clear documentation panel when different mode is requested
        # only display doc for selected mode
        self.panel.html_moi_doc.LoadPage(self.doc_path)
        pages = []
        if mode == "lens-align":
            if self._focus_streams:
                pages.append("doc/sparc2_autofocus.html")
            pages.append("doc/sparc2_lens.html")
        elif mode == "mirror-align":
            pages.append("doc/sparc2_mirror.html")
        elif mode == "lens2-align":
            pages.append("doc/sparc2_lens_switch.html")
        elif mode == "center-align":
            pages.append("doc/sparc2_centering.html")
        elif mode == "ek-align":
            pages.append("doc/sparc2_ek.html")
        elif mode == "fiber-align":
            pages.append("doc/sparc2_fiber.html")
        elif mode == "streak-align":
            pages.append("doc/sparc2_streakcam.html")
        elif mode == "light-in-align":
            # Several modules have this mode, but require different alignment procedure.
            # So we have to detect precisely which module is present (FSLM, FPLM, or ELIM).
            main = self.tab_data_model.main
            if main.spec_switch:  # only FSLM has spec-switch
                pages.append("doc/sparc2_light_in_fslm.html")
            elif main.mirror and main.mirror.name in main.light_aligner.affects.value:
                # FPLM affects the mirror, not the ELIM
                pages.append("doc/sparc2_light_in_fplm.html")
            else:  # default to ELIM
                pages.append("doc/sparc2_light_in_elim.html")
        else:
            logging.warning("Could not find alignment documentation for mode %s requested." % mode)

        for p in pages:
            doc_cnt = pkg_resources.resource_string("odemis.gui", p)
            self.panel.html_moi_doc.AppendToPage(doc_cnt)

        # Reposition and adjust the size of the various widgets, as they have changed
        fix_static_text_clipping(self.panel)  # for the widgets shown for the first time
        self.panel.Layout()

    def _on_align_mode_done(self, f):
        """Not essential but good for logging and debugging."""
        try:
            f.result()
        except:
            logging.exception("Failed changing alignment mode.")
        else:
            logging.debug("Optical path was updated.")

    @call_in_wx_main
    def _on_lens_align_done(self, f):
        # Has no effect now, as OPM future are not cancellable (but it makes the
        # code more future-proof)
        if f.cancelled():
            return

        # TODO: if the mode is already left, don't do it
        # Updates L1 ACTIVE position when the user moves it
        if self.tab_data_model.main.lens_mover:
            self.tab_data_model.main.lens_mover.position.subscribe(self._onLensPos)
            self.panel.pnl_lens_mover.Enable(True)

    @call_in_wx_main
    def _on_lens_switch_align_done(self, f):
        # Has no effect now, as OPM future are not cancellable (but it makes the
        # code more future-proof)
        if f.cancelled():
            return

        # TODO: if the mode is already left, don't do it
        # Updates L2 ACTIVE position when the user moves it
        if self.tab_data_model.main.lens_switch:
            self.tab_data_model.main.lens_switch.position.subscribe(self._onLensSwitchPos)
            self.panel.pnl_lens_switch.Enable(True)

    @call_in_wx_main
    def _on_light_in_align_done(self, f):
        # Has no effect now, as OPM future are not cancellable (but it makes the
        # code more future-proof)
        if f.cancelled():
            return

        # updates the spec-switch ACTIVE position when the user moves it
        if self.tab_data_model.main.spec_switch:
            self.panel.pnl_spec_switch.Enable(True)
            self._adjust_spec_switch_button_state()

        # updates the light-aligner ACTIVE position when the user moves it
        if self.tab_data_model.main.light_aligner:
            self.tab_data_model.main.light_aligner.position.subscribe(self._onLightAlignPos)

    @call_in_wx_main
    def _on_fibalign_done(self, f):
        """
        Called when the optical path mode is fiber-align and ready
        """
        # Has no effect now, as OPM future are not cancellable (but it makes the
        # code more future-proof)
        if f.cancelled():
            return
        logging.debug("Fiber aligner finished moving")

        # The optical path manager queues the futures. So the mode might already
        # have been changed to another one, while this fiber-align future only
        # finishes now. Without checking for this, the fiber selector position
        # will be listen to, while in another mode.
        # Alternatively, it could happen that during a change to fiber-align,
        # the tab is changed.
        if self.tab_data_model.align_mode.value != "fiber-align":
            logging.debug("Not listening fiber selector as mode is now %s",
                          self.tab_data_model.align_mode.value)
            return
        elif not self.IsShown():
            logging.debug("Not listening fiber selector as alignment tab is not shown")
            return

        # Make sure the user can move the X axis only once at ACTIVE position
        if self.tab_data_model.main.spec_sel:
            self.tab_data_model.main.spec_sel.position.subscribe(self._onSpecSelPos)
        if self.tab_data_model.main.fibaligner:
            self.tab_data_model.main.fibaligner.position.subscribe(self._onFiberPos)
        self.panel.btn_m_fibaligner_x.Enable(True)
        self.panel.btn_p_fibaligner_x.Enable(True)
        self.panel.btn_m_fibaligner_y.Enable(True)
        self.panel.btn_p_fibaligner_y.Enable(True)

    def _on_ccd_stream_play(self, _):
        """
        Called when the ccd_stream.should_update or speccnt_stream.should_update
         VA changes.
        Used to also play/pause the spot stream simultaneously
        """
        # Especially useful for the hardware which force the SEM external scan
        # when the SEM stream is playing. Because that allows the user to see
        # the SEM image in the original SEM software (by pausing the stream),
        # while still being able to move the mirror.
        ccdupdate = self._ccd_stream and self._ccd_stream.should_update.value
        spcupdate = self._speccnt_stream and self._speccnt_stream.should_update.value
        ekccdupdate = self._as_stream and self._as_stream.should_update.value
        streakccdupdate = self._ts_stream and self._ts_stream.should_update.value
        self._spot_stream.is_active.value = any((ccdupdate, spcupdate, ekccdupdate, streakccdupdate))

    def _filter_axes(self, axes):
        """
        Given an axes dict from config, filter out the axes which are not
          available on the current hardware.
        axes (dict str -> (str, Actuator or None)): VA name -> axis+Actuator
        returns (dict): the filtered axes
        """
        return {va_name: (axis_name, comp)
                for va_name, (axis_name, comp) in axes.items()
                if comp and axis_name in comp.axes}

    def _find_spectrometer(self, detector):
        """
        Find a spectrometer which wraps the given detector
        return (Detector): the spectrometer
        raise LookupError: if nothing found.
        """
        main_data = self.tab_data_model.main
        for spec in main_data.spectrometers:
            # Check by name as the components are actually Pyro proxies, which
            # might not be equal even if they point to the same component.
            if (model.hasVA(spec, "dependencies") and
                    detector.name in (d.name for d in spec.dependencies.value)
            ):
                return spec

        raise LookupError("No spectrometer corresponding to %s found" % (detector.name,))

    def _onClickFocus(self, evt):
        """
        Called when the autofocus button is pressed.
        It will start auto focus procedure... or stop it (if it's currently active)
        """
        self.tab_data_model.autofocus_active.value = not self.tab_data_model.autofocus_active.value

    def _createFocusStreams(self, focuser, hw_settings_config):
        """
        Initialize a stream to see the focus, and the slit for lens alignment.
        :param focuser: the focuser to get the components for (currently ccd_focuser)
        :param hw_settings_config: hw config for initializing BrightfieldStream
        :return: all created focus streams
        """
        focus_streams = []
        dets = GetSpectrometerFocusingDetectors(focuser)

        # Sort to have the first stream corresponding to the same detector as the
        # stream in the alignment view. As this stream will be used for showing
        # the slit line after the autofocus.
        if self._ccd_stream:
            ccd = self._ccd_stream.detector
            dets = sorted(dets, key=lambda d: d.name == ccd.name, reverse=True)

        # One "line" stream per detector
        # Add a stream to see the focus, and the slit for lens alignment.
        # As it is a BrightfieldStream, it will turn on the emitter when
        # active.
        for d in dets:
            speclines = acqstream.BrightfieldStream(
                "Spectrograph line ({name})".format(name=d.name),
                d,
                d.data,
                emitter=None,  # actually, the brightlight, but the autofocus procedure takes care of it
                focuser=focuser,
                detvas=get_local_vas(d, hw_settings_config),
                forcemd={model.MD_POS: (0, 0),
                         model.MD_ROTATION: 0},
                acq_type=model.MD_AT_SLIT,
            )
            speclines.tint.value = odemis.gui.FOCUS_STREAM_COLOR
            # Fixed values, known to work well for autofocus
            speclines.detExposureTime.value = speclines.detExposureTime.clip(0.2)
            self._setFullFoV(speclines, (2, 2))
            if model.hasVA(speclines, "detReadoutRate"):
                try:
                    speclines.detReadoutRate.value = speclines.detReadoutRate.range[1]
                except AttributeError:
                    speclines.detReadoutRate.value = max(speclines.detReadoutRate.choices)
            focus_streams.append(speclines)

        return focus_streams

    @call_in_wx_main
    def _ensureOneFocusStream(self, _):
        """
        Ensures that only one focus stream is shown at a time
        Called when a focus stream "should_update" state changes
        Add/Remove the stream to the current view (updating the StreamTree)
        Set the focus detectors combobox selection to the the stream's detector
        # Pick the stream to be shown:
        #  1. Pick the stream which is playing (should_update=True)
        #  2. Pick the focus stream which is already shown (in v.getStreams())
        #  3. Pick the first focus stream
        """
        focusedview = self.tab_data_model.focussedView.value
        should_update_stream = next((s for s in self._focus_streams if s.should_update.value), None)
        if should_update_stream:
            # This stream should be shown, remove the other ones first
            for st in focusedview.getStreams():
                if st in self._focus_streams and st is not should_update_stream:
                    focusedview.removeStream(st)
            if not should_update_stream in focusedview.stream_tree:
                focusedview.addStream(should_update_stream)
            # Set the focus detectors combobox selection
            try:
                istream = self._focus_streams.index(should_update_stream)
                self.panel.cmb_focus_detectors.SetSelection(istream)
            except ValueError:
                logging.error("Unable to find index of the focus stream")
        else:
            # Check if there are any focus stream currently shown
            if not any(s in self._focus_streams for s in focusedview.getStreams()):
                # Otherwise show the first one
                focusedview.addStream(self._focus_streams[0])
                self.panel.cmb_focus_detectors.SetSelection(0)

    def _onFocusDetectorChange(self, evt):
        """
        Handler for focus detector combobox selection change
        Play the stream associated with the chosen detector
        """
        # Find the stream related to this selected detector
        idx = self.panel.cmb_focus_detectors.GetSelection()
        stream = self._focus_streams[idx]
        # Play this stream (set both should_update and is_active with True)
        stream.should_update.value = True
        stream.is_active.value = True

        # Move the optical path selectors for the detector (spec-det-selector in particular)
        # The moves will happen in the background.
        opm = self.tab_data_model.main.opm
        opm.selectorsToPath(stream.detector.name)

    def add_combobox_control(self, label_text, value=None, conf=None):
        """ Add a combo box to the focus panel
        # Note: this is a hack. It must be named so, to look like a StreamPanel
        """
        # No need to create components, defined already on the xrc file
        return self.panel.cmb_focus_gratings_label, self.panel.cmb_focus_gratings

    def _enableFocusComponents(self, manual, ccd_stream=False):
        """
        Enable or disable focus GUI components (autofocus button, position slider, detector and grating comboboxes)
        Manual focus button is not included as it's only disabled once => during auto focus
        :param manual (bool): if manual focus is enabled/disabled
        :param ccd_stream (bool): if ccd_stream panel should be enabled/disabled
        """
        self.panel.slider_focus.Enable(manual)
        self.panel.cmb_focus_detectors.Enable(manual)
        self.panel.cmb_focus_gratings.Enable(manual)

        # Autofocus button is inverted
        self.panel.btn_autofocus.Enable(not manual)

        # Enable/Disable ccd stream panel
        if self._ccd_spe and self._ccd_spe.stream_panel.Shown:
            self._ccd_spe.stream_panel.Enable(ccd_stream)

    @call_in_wx_main
    def _onManualFocus(self, event):
        """
        Called when manual focus btn receives an event.
        """
        main = self.tab_data_model.main
        bl = main.brightlight
        align_mode = self.tab_data_model.align_mode.value
        gauge = self.panel.gauge_autofocus
        self._mf_future.cancel()  # In case it's still changing, immediately stop (the gauge)

        if event.GetEventObject().GetValue():  # manual focus btn toggled
            # Set the optical path according to the align mode
            if align_mode == "streak-align":
                opath = "streak-focus"
            elif align_mode in ("lens-align", "lens2-align", "light-in-align"):
                opath = "spec-focus"
            else:
                self._stream_controller.pauseStreams()
                logging.warning("Manual focus requested not compatible with requested alignment mode %s. Do nothing.",
                                align_mode)
                return

            if align_mode == "streak-align":
                # force wavelength 0
                # TODO: Make sure it's the correct position on the workflow, maybe do it for all modes?
                main.spectrograph.moveAbsSync({"wavelength": 0})

                self.panel.slider_focus.Enable(True)
                self.panel.cmb_focus_gratings.Enable(True)
                # Do no enable detector selection, as only the streak-ccd is available
                # TODO: update the combobox to indicate the current detector is the streak-ccd
            else:
                if align_mode in ("lens-align", "lens2-align", "light-in-align"):
                    self._enableFocusComponents(manual=True, ccd_stream=False)
                self._stream_controller.pauseStreams()
                self.panel.btn_bkg_acquire.Enable(False)

            self._mf_future = Sparc2ManualFocus(main.opm, bl, opath, toggled=True)
            self._mf_future.add_done_callback(self._onManualFocusReady)
            # Update GUI
            self._pfc_manual_focus = ProgressiveFutureConnector(self._mf_future, gauge)
        else:  # manual focus button is untoggled
            # First pause the streams, so that image of the slit (almost closed) is the final image
            self._stream_controller.pauseStreams()
            # Go back to previous mode (=> open the slit & turn off the lamp)
            # Get the optical path from align mode
            opath = self._mode_to_opm[align_mode]

            self._mf_future = Sparc2ManualFocus(main.opm, bl, opath, toggled=False)
            self._mf_future.add_done_callback(self._onManualFocusFinished)
            # Update GUI
            self._pfc_manual_focus = ProgressiveFutureConnector(self._mf_future, gauge)

    @call_in_wx_main
    def _onManualFocusReady(self, future):
        """
        Called when starting manual focus is done
        Make sure there's a shown focus stream, then enable/disable manual focus components
        """
        if future.cancelled():
            return

        align_mode = self.tab_data_model.align_mode.value
        if align_mode != "streak-align" and self._focus_streams:
            istream = self.panel.cmb_focus_detectors.GetSelection()
            self._focus_streams[istream].should_update.value = True

    def _onManualFocusFinished(self, future):
        """
        Called when finishing manual focus is done
        """
        if future.cancelled():
            return

        self._onAlignMode(self.tab_data_model.align_mode.value)

    @call_in_wx_main
    def _onAutofocus(self, active):
        if active:
            main = self.tab_data_model.main
            align_mode = self.tab_data_model.align_mode.value
            if align_mode in ("lens-align", "lens2-align", "light-in-align"):
                focus_mode = "spec-focus"
                ss = self._focus_streams
                btn = self.panel.btn_autofocus
                gauge = self.panel.gauge_autofocus
            elif align_mode == "fiber-align":
                focus_mode = "spec-fiber-focus"
                ss = []  # No stream to play
                btn = self.panel.btn_fib_autofocus
                gauge = self.panel.gauge_fib_autofocus
            else:
                logging.info("Autofocus requested outside of lens or fiber alignment mode, not doing anything")
                return

            # GUI stream bar controller pauses the stream
            btn.SetLabel("Cancel")
            self.panel.btn_manual_focus.Enable(False)
            self._enableFocusComponents(manual=False, ccd_stream=False)
            self._stream_controller.pauseStreams()
            self.panel.btn_bkg_acquire.Enable(False)

            # No manual autofocus for now
            self._autofocus_f = Sparc2AutoFocus(focus_mode, main.opm, ss, start_autofocus=True)
            self._autofocus_align_mode = align_mode
            self._autofocus_f.add_done_callback(self._on_autofocus_done)

            # Update GUI
            self._pfc_autofocus = ProgressiveFutureConnector(self._autofocus_f, gauge)
        else:
            # Cancel task, if we reached here via the GUI cancel button
            self._autofocus_f.cancel()

            if self._autofocus_align_mode in ("lens-align", "lens2-align"):
                btn = self.panel.btn_autofocus
            elif self._autofocus_align_mode == "fiber-align":
                btn = self.panel.btn_fib_autofocus
            else:
                logging.error("Unexpected autofocus mode '%s'", self._autofocus_align_mode)
                return
            btn.SetLabel("Auto focus")

    @call_in_wx_main
    def _on_autofocus_done(self, future):
        try:
            future.result()
        except CancelledError:
            pass
        except Exception:
            logging.exception("Autofocus failed")

        # That VA will take care of updating all the GUI part
        self.tab_data_model.autofocus_active.value = False
        # Go back to normal mode. Note: it can be "a little weird" in case the
        # autofocus was stopped due to changing mode, but it should end-up doing
        # just twice the same thing, with the second time being a no-op.
        self._onAlignMode(self.tab_data_model.align_mode.value)

        # Enable manual focus when done running autofocus
        self.panel.btn_manual_focus.Enable(True)

    @call_in_wx_main
    def _on_spec_switch_btn(self, event):
        """
        Called when one of the retract and engage spec-switch mirror buttons are pressed.
        It will start movement for the spec-switch mirror (retract/engage).
        GUI is adjusted in this method and therefore make the call in main.
        """
        main = self.tab_data_model.main
        spec_switch = main.spec_switch.getMetadata()
        # assign the button which got the click event
        btn = event.GetEventObject()

        if btn.GetLabel() in {"Engage", "Engaged"}:
            # set the requested position to ACTIVE and change the appearance of the retract and engage buttons
            pos = spec_switch[model.MD_FAV_POS_ACTIVE]
            self._change_spec_switch_btn_lbl(btn, "Cancel", wx.BLACK)
            self._change_spec_switch_btn_lbl(self.panel.btn_spec_switch_retract, "Retract", wx.BLACK)
            # pause the stream when engaging the mirror
            self._ccd_stream.should_update.value = False
        elif btn.GetLabel() in {"Retract", "Retracted"}:
            # set the requested position to DEACTIVE and change the appearance of the retract and engage buttons
            pos = spec_switch[model.MD_FAV_POS_DEACTIVE]
            self._change_spec_switch_btn_lbl(btn, "Cancel", wx.BLACK)
            self._change_spec_switch_btn_lbl(self.panel.btn_spec_switch_engage, "Engage", wx.BLACK)
            # continue the stream when retracting the mirror
            self._ccd_stream.should_update.value = True
        elif btn.GetLabel() == "Cancel":
            self._spec_switch_f.cancel()
            self._adjust_spec_switch_button_state()
            return

        # disable the alignment buttons when moving the mirror
        self.panel.btn_m_spec_switch_x.Enable(False)
        self.panel.btn_p_spec_switch_x.Enable(False)

        # show the gauge if it is hidden
        self.panel.gauge_specswitch.Show()

        # unsubscribe to position changes to prevent overwriting FAV_POS
        if self.tab_data_model.main.spec_switch:
            self.tab_data_model.main.spec_switch.position.unsubscribe(self._onSpecSwitchPos)

        # move the mirror to the right position using a progressive future
        self._spec_switch_f = driver.ProgressiveMove(main.spec_switch, pos)
        self._spec_switch_f.add_done_callback(self._on_specswitch_button_done)
        # take care of a moving gauge while moving the mirror
        self._pfc_spec_switch = ProgressiveFutureConnector(self._spec_switch_f, self.panel.gauge_specswitch)

    @call_in_wx_main
    def _on_specswitch_button_done(self, future):
        """
        Called when pressing one of the spec-switch buttons to retract or engage the mirror.
        After a button is pressed, either the movement is done or it was cancelled.
        """
        try:
            future.result()
        except CancelledError:
            pass
        except Exception:
            logging.exception("Failure during the move of spec-switch")

        # reset the labels on both buttons to support rollback after calling cancelling
        self._change_spec_switch_btn_lbl(self.panel.btn_spec_switch_retract, "Retract", wx.BLACK)
        self._change_spec_switch_btn_lbl(self.panel.btn_spec_switch_engage, "Engage", wx.BLACK)

        # check if the state of the buttons need adjustments
        self._adjust_spec_switch_button_state()
        self._pfc_spec_switch = None

        # hide the gauge as there is no convenient way to reset it
        self.panel.gauge_specswitch.Hide()

    def _change_spec_switch_btn_lbl(self, button: wx.Button, label: str, fg_color: str):
        """
        Small support method to change the button properties for the spec-switch mirror.
        There are currently buttons both for retracting and engaging the mirror.
        :param button: wx.ImageTextButton button object
        :param label: text of the button label
        :param fg_color: color of the button label
        """
        button.SetForegroundColour(fg_color)
        button.SetLabel(label)

    def _adjust_spec_switch_button_state(self):
        """
        Here the state of the buttons for retracting and engaging the spec-switch mirror is checked
        and handled according to the current position.
        Only when the mirror is at the engaged (FAV_POS_ACTIVE) position it can be aligned manually.
        """
        # disable the manual alignment buttons on default
        self.panel.btn_m_spec_switch_x.Enable(False)
        self.panel.btn_p_spec_switch_x.Enable(False)

        # request the metadata to be able to compare with FAV_POS
        spec_switch_md = self.tab_data_model.main.spec_switch.getMetadata()

        # when the mirror is in the ACTIVE position enable the
        # option to align it manually and to update the FAV_POS
        if almost_equal(self.tab_data_model.main.spec_switch.position.value["x"],
                        spec_switch_md[model.MD_FAV_POS_ACTIVE]["x"], atol=1e-5):
            self.tab_data_model.main.spec_switch.position.subscribe(self._onSpecSwitchPos)
            # enable the manual alignment buttons when the mirror is engaged
            self.panel.btn_m_spec_switch_x.Enable(True)
            self.panel.btn_p_spec_switch_x.Enable(True)
            self._change_spec_switch_btn_lbl(self.panel.btn_spec_switch_engage,
                                             "Engaged", odemis.gui.FG_COLOUR_RADIO_ACTIVE)
            self._change_spec_switch_btn_lbl(self.panel.btn_spec_switch_retract,
                                             "Retract", wx.BLACK)
        # when the mirror is in the DEACTIVE position disable manual alignment and update of FAV_POS
        elif almost_equal(self.tab_data_model.main.spec_switch.position.value["x"],
                          spec_switch_md[model.MD_FAV_POS_DEACTIVE]["x"], atol=1e-5):
            self._change_spec_switch_btn_lbl(self.panel.btn_spec_switch_retract,
                                             "Retracted", odemis.gui.FG_COLOUR_RADIO_ACTIVE)
            self._change_spec_switch_btn_lbl(self.panel.btn_spec_switch_engage,
                                             "Engage", wx.BLACK)
        else:
            self._change_spec_switch_btn_lbl(self.panel.btn_spec_switch_engage,
                                             "Engage", odemis.gui.FG_COLOUR_ERROR)
            self._change_spec_switch_btn_lbl(self.panel.btn_spec_switch_retract,
                                             "Retract", odemis.gui.FG_COLOUR_ERROR)

    def _onBkgAcquire(self, evt):
        """
        Called when the user presses the "Acquire background" button
        """
        if self._bkg_im is None:
            # Stop e-beam, in case it's connected to a beam blanker
            self._ccd_stream.should_update.value = False

            # TODO: if there is no blanker available, put the spec-det-selector to
            # the other port, so that the ccd get almost no signal? Or it might be
            # better to just rely on the user to blank from the SEM software?

            # Disable button to give a feedback that acquisition is taking place
            self.panel.btn_bkg_acquire.Disable()

            # Acquire asynchronously
            # We store the time to ensure we don't use the latest CCD image
            self._min_bkg_date = time.time()
            self.tab_data_model.main.ccd.data.subscribe(self._on_bkg_data)
        else:
            logging.info("Removing background data")
            self._bkg_im = None
            self._ccd_stream.background.value = None
            self.panel.btn_bkg_acquire.SetLabel("Acquire background")

    def _on_bkg_data(self, df, data):
        """
        Called with a raw CCD image corresponding to background acquisition.
        """
        try:
            if data.metadata[model.MD_ACQ_DATE] < self._min_bkg_date:
                logging.debug("Got too old image, probably not background yet")
                return
        except KeyError:
            pass  # no date => assume it's new enough
        # Stop the acquisition, and pass the data to the streams
        df.unsubscribe(self._on_bkg_data)
        self._bkg_im = data
        self._ccd_stream.background.value = data
        self._ccd_stream.should_update.value = True

        wx.CallAfter(self.panel.btn_bkg_acquire.SetLabel, "Remove background")
        wx.CallAfter(self.panel.btn_bkg_acquire.Enable)

    @limit_invocation(1)  # max 1 Hz
    def _onNewMoI(self, rgbim):
        """
        Called when a new MoI image is available.
        We actually don't use the RGB image, but it's a sign that there is new
        MoI and spot size info to display.
        rgbim (DataArray): RGB image of the MoI
        """
        try:
            data = self._ccd_stream.raw[0]
        except IndexError:
            return  # No data => next time will be better

        # TODO: Show a warning if the background image has different settings
        # than the current CCD image
        background = self._bkg_im
        if background is not None and background.shape != data.shape:
            logging.debug("Background has a different resolution, cannot be used")
            background = None

        # Note: this can take a long time (on a large image), so we must not do
        # it in the main GUI thread. limit_invocation() ensures we are in a
        # separate thread, and that's why the GUI update is separated.
        moi = spot.MomentOfInertia(data, background)
        ss = spot.SpotIntensity(data, background)
        self._updateMoIValues(moi, ss)

    @call_in_wx_main
    def _updateMoIValues(self, moi, ss):
        # If value is None => text is ""
        txt_moi = units.readable_str(moi, sig=3)
        self._txt_moi.SetValue(txt_moi)
        # Convert spot intensity from ratio to %
        self._txt_ss.SetValue(u"%.4f %%" % (ss * 100,))

    @call_in_wx_main
    def _onMirrorDimensions(self, focusDistance):
        try:
            self.mirror_ol.set_mirror_dimensions(self.lens.parabolaF.value,
                                                 self.lens.xMax.value,
                                                 self.lens.focusDistance.value,
                                                 self.lens.holeDiameter.value)
        except (AttributeError, TypeError) as ex:
            logging.warning("Failed to get mirror dimensions: %s", ex)

        if "ek-align" in self.tab_data_model.align_mode.choices:
            try:
                self.ek_ol.set_mirror_dimensions(self.lens.parabolaF.value,
                                                 self.lens.xMax.value,
                                                 self.lens.focusDistance.value)
            except (AttributeError, TypeError) as ex:
                logging.warning("Failed to get mirror dimensions for EK alignment: %s", ex)

    def _onLensPos(self, pos):
        """
        Called when the lens is moved (and the tab is shown)
        """
        if not self.IsShown():
            # Might happen if changing quickly between tab
            logging.warning("Received active lens position while outside of alignment tab")
            return

        # Save the lens position as the "calibrated" one
        lm = self.tab_data_model.main.lens_mover
        lm.updateMetadata({model.MD_FAV_POS_ACTIVE: pos})

    def _onLensSwitchPos(self, pos):
        """
        Called when the lens2 is moved (and the tab is shown)
        """
        if not self.IsShown():
            # Might happen if changing quickly between tab
            logging.warning("Received active lens position while outside of alignment tab")
            return

        # Save the lens position as the "calibrated" one
        lm = self.tab_data_model.main.lens_switch
        lm.updateMetadata({model.MD_FAV_POS_ACTIVE: pos})

    def _onMirrorPos(self, pos):
        """
        Called when the mirror is moved (and the tab is shown)
        """
        # HACK: This is actually a hack of a hack. Normally, the user can only
        # access the alignment tab if the mirror is engaged, so it should never
        # get close from the parked position. However, for maintenance, it's
        # possible to hack the GUI and enable the tab even if the mirror is
        # not engaged. In such case, if by mistake the mirror moves, we should
        # not set the "random" position as the engaged position.
        dist_parked = math.hypot(pos["l"] - MIRROR_POS_PARKED["l"],
                                 pos["s"] - MIRROR_POS_PARKED["s"])
        if dist_parked <= MIRROR_ONPOS_RADIUS:
            logging.warning("Mirror seems parked, not updating FAV_POS_ACTIVE")
            return

        # Save mirror position as the "calibrated" one
        m = self.tab_data_model.main.mirror
        m.updateMetadata({model.MD_FAV_POS_ACTIVE: pos})

    def _onSpecSelPos(self, pos):
        """
        Called when the spec-selector (wrapper to the X axis of fiber-aligner)
          is moved (and the fiber align mode is active)
        """
        if self.tab_data_model.main.tab.value != self:
            # Should never happen, but for safety, we double check
            logging.warning("Received active fiber position while outside of alignment tab")
            return
        # TODO: warn if pos is equal to the DEACTIVE value (within 1%)

        # Save the axis position as the "calibrated" one
        ss = self.tab_data_model.main.spec_sel
        logging.debug("Updating the active fiber X position to %s", pos)
        ss.updateMetadata({model.MD_FAV_POS_ACTIVE: pos})

    def _onSpecSwitchPos(self, pos):
        """
        Called when the light-aligner is moved (and the light-out-alignment mode is active)
        """
        if self.tab_data_model.main.tab.value != self:
            # Should never happen, but for safety, we double check
            logging.warning("Received active spec-switch position while outside of alignment tab")
            return

        spec_switch = self.tab_data_model.main.spec_switch
        logging.debug("Updating the spec switch X position to %s", pos)
        spec_switch.updateMetadata({model.MD_FAV_POS_ACTIVE: pos})

    def _onLightAlignPos(self, pos):
        """
        Called when the light-aligner is moved (and the light-out-alignment mode is active)
        """
        if self.tab_data_model.main.tab.value != self:
            # Should never happen, but for safety, we double check
            logging.warning("Received active light-aligner position while outside of alignment tab")
            return

        light_align = self.tab_data_model.main.light_aligner
        logging.debug("Updating the light aligner X position to %s", pos)
        light_align.updateMetadata({model.MD_FAV_POS_ACTIVE: pos})

    def _onFiberPos(self, pos):
        """
        Called when the fiber-aligner is moved (and the fiber align mode is active),
          for updating the Y position. (X pos is handled by _onSpecSelPos())
        """
        if self.tab_data_model.main.tab.value != self:
            # Should never happen, but for safety, we double check
            logging.warning("Received active fiber position while outside of alignment tab")
            return

        # Update the fibaligner with the Y axis, if it supports it
        fiba = self.tab_data_model.main.fibaligner
        fib_fav_pos = fiba.getMetadata().get(model.MD_FAV_POS_ACTIVE, {})

        # If old hardware, without FAV_POS: just do nothing
        if fib_fav_pos and "y" in fib_fav_pos:
            fib_fav_pos["y"] = pos["y"]
            logging.debug("Updating the active fiber Y position to %s", fib_fav_pos)
            fiba.updateMetadata({model.MD_FAV_POS_ACTIVE: fib_fav_pos})

    def Show(self, show=True):
        Tab.Show(self, show=show)

        main = self.tab_data_model.main
        # Select the right optical path mode and the plays right stream
        if show:
            # Reset the zoom level in the Lens alignment view
            # Mostly to workaround the fact that at start-up the canvas goes
            # through weird sizes, which can cause the initial zoom level to be
            # too high. It's also a failsafe, in case the user has moved to a
            # view position/zoom which could be confusing when coming back.
            self.panel.vp_align_lens.canvas.fit_view_to_content()

            mode = self.tab_data_model.align_mode.value
            self._onAlignMode(mode)
            if main.mirror:
                main.mirror.position.subscribe(self._onMirrorPos)

            # Reset the focus progress bar (as any focus action has been cancelled)
            wx.CallAfter(self.panel.gauge_autofocus.SetValue, 0)
        else:
            # when hidden, the new tab shown is in charge to request the right
            # optical path mode, if needed.
            self._stream_controller.pauseStreams()
            # Cancel autofocus (if it happens to run)
            self.tab_data_model.autofocus_active.value = False

            # Cancel manual focus: just reset the button, as the rest is just
            # optical-path moves.
            self.panel.btn_manual_focus.SetValue(False)

            # Turn off the brightlight, if it was on
            bl = main.brightlight
            if bl:
                bl.power.value = bl.power.range[0]

            if main.lens_mover:
                main.lens_mover.position.unsubscribe(self._onLensPos)
            if main.lens_switch:
                main.lens_switch.position.unsubscribe(self._onLensSwitchPos)
            if main.mirror:
                main.mirror.position.unsubscribe(self._onMirrorPos)
            if main.spec_sel:
                main.spec_sel.position.unsubscribe(self._onSpecSelPos)
            if main.spec_switch:
                main.spec_switch.position.unsubscribe(self._onSpecSwitchPos)
                main.spec_switch.position.unsubscribe(self._onLightAlignPos)
            if main.fibaligner:
                main.fibaligner.position.unsubscribe(self._onFiberPos)
            if main.light_aligner:
                main.light_aligner.position.unsubscribe(self._onLightAlignPos)
            # Also fit to content now, so that next time the tab is displayed, it's ready
            self.panel.vp_align_lens.canvas.fit_view_to_content()

    def terminate(self):
        self._stream_controller.pauseStreams()
        self.tab_data_model.autofocus_active.value = False

    @classmethod
    def get_display_priority(cls, main_data):
        # For SPARCs with a "parkable" mirror.
        if main_data.role in ("sparc", "sparc2"):
            mirror = main_data.mirror
            if mirror and set(mirror.axes.keys()) == {"l", "s"}:
                return 5
            elif main_data.light_aligner:
                # Special case for ELIM module: no mirror, but light-aligner
                return 5

        return None
