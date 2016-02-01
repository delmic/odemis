# -*- coding: utf-8 -*-

"""
@author: Rinze de Laat

Copyright © 2012-2013 Rinze de Laat, Éric Piel, Delmic

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

from __future__ import division

import pkg_resources
import collections
from concurrent.futures import CancelledError
import logging
import math
import numpy
from odemis import dataio, model
from odemis.acq import calibration
from odemis.acq.align import AutoFocus
from odemis.acq.stream import OpticalStream, SpectrumStream, CLStream, EMStream, \
    ARStream, CLSettingsStream, ARSettingsStream, MonochromatorSettingsStream, RGBCameraStream, BrightfieldStream
from odemis.driver.actuator import ConvertStage
import odemis.gui
from odemis.gui.comp.canvas import CAN_ZOOM
from odemis.gui.comp.popup import Message
from odemis.gui.comp.scalewindow import ScaleWindow
from odemis.gui.comp.viewport import MicroscopeViewport, AngularResolvedViewport, \
    PlotViewport, SpatialSpectrumViewport
from odemis.gui.conf import get_acqui_conf
from odemis.gui.conf.data import get_local_vas, get_stream_settings_config
from odemis.gui.cont import settings, tools
from odemis.gui.cont.actuators import ActuatorController
from odemis.gui.cont.microscope import SecomStateController, DelphiStateController
from odemis.gui.cont.streams import StreamController
from odemis.gui.util import call_in_wx_main
from odemis.gui.util.img import scale_to_alpha
from odemis.gui.util.widgets import ProgressiveFutureConnector, AxisConnector
from odemis.util import units
from odemis.util.dataio import data_to_static_streams
import os.path
import scipy.misc
import weakref
# IMPORTANT: wx.html needs to be imported for the HTMLWindow defined in the XRC
# file to be correctly identified. See: http://trac.wxwidgets.org/ticket/3626
# This is not related to any particular wxPython version and is most likely permanent.
import wx
import wx.html

import odemis.acq.stream as acqstream
import odemis.gui.cont.acquisition as acqcont
import odemis.gui.cont.streams as streamcont
import odemis.gui.cont.views as viewcont
import odemis.gui.model as guimod
import odemis.gui.util as guiutil
import odemis.gui.util.align as align


class Tab(object):
    """ Small helper class representing a tab (tab button + panel) """

    def __init__(self, name, button, panel, main_frame, tab_data, label=None):
        """
        :type name: str
        :type button: odemis.gui.comp.buttons.TabButton
        :type panel: wx.Panel
        :type main_frame: odemis.gui.main_xrc.xrcfr_main
        :type tab_data: odemis.gui.model.LiveViewGUIData
        :type label: str or None

        """
        logging.debug("Initialising tab %s", name)

        self.name = name
        self.label = label
        self.button = button
        self.panel = panel
        self.main_frame = main_frame
        self.tab_data_model = tab_data
        self.highlighted = False
        self.focussed_viewport = None

    def Show(self, show=True):
        self.button.SetToggle(show)
        if show:
            self._connect_22view_event()
            self._connect_crosshair_event()

            self.highlight(False)

        self.panel.Show(show)

    def _connect_22view_event(self):
        """ If the tab has a 2x2 view, this method will connect it to the 2x2
        view menu item (or ensure it's disabled).
        """
        if hasattr(self.tab_data_model, 'views') and len(self.tab_data_model.views.value) >= 4:
            # We assume it has a 2x2 view layout
            def set_22_menu_check(viewlayout):
                """Called when the view layout changes"""
                is_22 = viewlayout == guimod.VIEW_LAYOUT_22
                self.main_frame.menu_item_22view.Check(is_22)

            def on_switch_22(evt):
                """Called when menu changes"""
                if self.tab_data_model.viewLayout.value == guimod.VIEW_LAYOUT_22:
                    self.tab_data_model.viewLayout.value = guimod.VIEW_LAYOUT_ONE
                else:
                    self.tab_data_model.viewLayout.value = guimod.VIEW_LAYOUT_22

            # Bind the function to the menu item, so it keeps the reference.
            # The VigilantAttribute will not unsubscribe it, until replaced.
            self.main_frame.menu_item_22view.vamethod = set_22_menu_check
            self.tab_data_model.viewLayout.subscribe(set_22_menu_check, init=True)
            # Assigning an event handler to the menu item, overrides
            # any previously assigned ones.
            wx.EVT_MENU(self.main_frame, self.main_frame.menu_item_22view.GetId(), on_switch_22)
            self.main_frame.menu_item_22view.Enable()
        else:
            self.main_frame.menu_item_22view.Enable(False)
            self.main_frame.menu_item_22view.Check(False)
            self.main_frame.menu_item_22view.vamethod = None  # drop VA subscr.

    def _connect_crosshair_event(self):
        """ Connect the cross hair menu event to the focused view and its
        `show_crosshair` VA to the menu item
        """
        # only if there's a focussed view that we can track
        if hasattr(self.tab_data_model, 'focussedView'):

            def set_cross_check(fv):
                """Called when focused view changes"""
                if hasattr(fv, "show_crosshair"):
                    fv.show_crosshair.subscribe(self.main_frame.menu_item_cross.Check, init=True)
                    self.main_frame.menu_item_cross.Enable(True)
                else:
                    self.main_frame.menu_item_cross.Enable(False)
                    self.main_frame.menu_item_cross.Check(False)

            def on_switch_crosshair(evt):
                """Called when menu changes"""
                foccused_view = self.tab_data_model.focussedView.value
                # Extra check, which shouldn't be needed since if there's no
                # `show_crosshair`, this code should never be called.
                if hasattr(foccused_view, "show_crosshair"):
                    show = self.main_frame.menu_item_cross.IsChecked()
                    foccused_view.show_crosshair.value = show

            # Bind the function to the menu item, so it keeps the reference.
            # The VigilantAttribute will not unsubscribe it, until replaced.
            self.main_frame.menu_item_cross.vamethod = set_cross_check
            self.tab_data_model.focussedView.subscribe(set_cross_check, init=True)
            # Assigning an event handler to the menu item, overrides
            # any previously assigned ones.
            wx.EVT_MENU(self.main_frame, self.main_frame.menu_item_cross.GetId(),
                        on_switch_crosshair)
            self.main_frame.menu_item_cross.Enable()
        else:
            # If the right elements are not found, simply disable the menu item
            self.main_frame.menu_item_cross.Enable(False)
            self.main_frame.menu_item_cross.Check(False)
            self.main_frame.menu_item_cross.vamethod = None  # drop VA subscr.

    def Hide(self):
        self.Show(False)

    def IsShown(self):
        return self.panel.IsShown()

    def terminate(self):
        """
        Called when the tab is not used any more
        """
        pass

    def set_label(self, label):
        self.button.SetLabel(label)

    def get_label(self):
        return self.button.GetLabel()

    def highlight(self, on=True):
        """ Put the tab in 'highlighted' mode to indicate a change has occurred """
        if self.highlighted != on:
            self.button.highlight(on)
            self.highlighted = on

    def make_default(self):
        """ Try and make the current tab the default

        This will only work when no tab has been set.
        """

        if not self.tab_data_model.main.tab.value:
            # Use the '_value' attribute, because the tab choices might not have been set yet
            self.tab_data_model.main.tab._value = self
        else:
            logging.warn("Could not make the '%s' tab default, '%s' already is.",
                         self, self.tab_data_model.main.tab.value.name)


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

        self.main_data = main_data

        # Order matters!
        # First we create the views, then the streams
        vpv = self._create_views(main_data, panel.pnl_secom_grid.viewports)
        self.view_controller = viewcont.ViewPortController(tab_data, panel, vpv)

        # Special overview button selection
        self.overview_controller = viewcont.OverviewController(tab_data,
                                                               panel.vp_overview_sem.canvas)
        ovv = self.panel.vp_overview_sem.microscope_view
        if main_data.overview_ccd:
            # Overview camera can be RGB => in that case len(shape) == 4
            if len(main_data.overview_ccd.shape) == 4:
                overview_stream = acqstream.RGBCameraStream("Overview", main_data.overview_ccd,
                                                            main_data.overview_ccd.data, None)
            else:
                overview_stream = acqstream.BrightfieldStream("Overview", main_data.overview_ccd,
                                                              main_data.overview_ccd.data, None)

            ovv.addStream(overview_stream)
            # TODO: add it to self.tab_data_model.streams?
            # In any case, to support displaying Overview in the normal 2x2
            # views we'd need to have a special Overview class

        # Connect the view selection buttons
        buttons = collections.OrderedDict([
            (
                panel.btn_secom_view_all,
                (None, panel.lbl_secom_view_all)),
            (
                panel.btn_secom_view_tl,
                (panel.vp_secom_tl, panel.lbl_secom_view_tl)),
            (
                panel.btn_secom_view_tr,
                (panel.vp_secom_tr, panel.lbl_secom_view_tr)),
            (
                panel.btn_secom_view_bl,
                (panel.vp_secom_bl, panel.lbl_secom_view_bl)),
            (
                panel.btn_secom_view_br,
                (panel.vp_secom_br, panel.lbl_secom_view_br)),
            (
                panel.btn_secom_overview,
                (panel.vp_overview_sem, panel.lbl_secom_overview)),
        ])

        self._view_selector = viewcont.ViewButtonController(
            tab_data,
            panel,
            buttons,
            panel.pnl_secom_grid.viewports
        )

        self._settingbar_controller = settings.SecomSettingsController(
            panel,
            tab_data
        )

        self._streambar_controller = streamcont.SecomStreamsController(
            tab_data,
            panel.pnl_secom_streams
        )

        # Toolbar
        self.tb = panel.secom_toolbar
        # TODO: Add the buttons when the functionality is there
        # tb.add_tool(tools.TOOL_ROI, self.tab_data_model.tool)
        # tb.add_tool(tools.TOOL_RO_ZOOM, self.tab_data_model.tool)
        # Add fit view to content to toolbar
        self.tb.add_tool(tools.TOOL_ZOOM_FIT, self.view_controller.fitViewToContent)
        # auto focus
        self._autofocus_f = model.InstantaneousFuture()
        self.tb.add_tool(tools.TOOL_AUTO_FOCUS, self.tab_data_model.autofocus_active)
        self.tb.enable_button(tools.TOOL_AUTO_FOCUS, False)
        self.tab_data_model.autofocus_active.subscribe(self._onAutofocus)
        tab_data.streams.subscribe(self._on_current_stream)

        self._acquisition_controller = acqcont.SecomAcquiController(tab_data, panel)

        if main_data.role == "delphi":
            state_controller_cls = DelphiStateController
        else:
            state_controller_cls = SecomStateController

        self._state_controller = state_controller_cls(
            tab_data,
            panel,
            "live_btn_",
            self._streambar_controller
        )

        # Connect the Delphi recalibration to the menu item
        if main_data.role == "delphi":
            wx.EVT_MENU(
                    self.main_frame,
                    self.main_frame.menu_item_recalibrate.GetId(),
                    self._state_controller.request_holder_recalib
            )

        # For remembering which streams are paused when hiding the tab
        self._streams_to_restart = set()  # set of weakref to the streams

        # To automatically play/pause a stream when turning on/off a microscope,
        # and add the stream on the first time.
        if hasattr(tab_data, 'opticalState'):
            tab_data.opticalState.subscribe(self.onOpticalState)

        if hasattr(tab_data, 'emState'):
            tab_data.emState.subscribe(self.onEMState)
            # decide which kind of EM stream to add by default
            if main_data.sed:
                self._add_em_stream = self._streambar_controller.addSEMSED
            elif main_data.bsd:
                self._add_em_stream = self._streambar_controller.addSEMBSD
            else:
                logging.error("No EM detector found")

        main_data.chamberState.subscribe(self.on_chamber_state, init=True)
        if not main_data.chamber:
            panel.live_btn_press.Hide()

        self._ensure_base_streams()

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
        if (main_data.ebeam and main_data.light):
            # Viewport type checking to avoid mismatches
            for vp in viewports[:4]:
                assert(isinstance(vp, MicroscopeViewport))

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
                  "stream_classes": EMStream,
                  }),
                (viewports[2],
                 {"name": "Combined 1",
                  "stage": main_data.stage,
                  "stream_classes": (EMStream, OpticalStream),
                  }),
                (viewports[3],
                 {"name": "Combined 2",
                  "stage": main_data.stage,
                  "stream_classes": (EMStream, OpticalStream),
                  }),
            ])
        # If SEM only: all SEM
        # Works also for the Sparc, as there is no other emitter, and we don't
        # need to display anything else anyway
        elif main_data.ebeam and not main_data.light:
            logging.info("Creating SEM only viewport layout")
            vpv = collections.OrderedDict()
            for i, viewport in enumerate(viewports):
                vpv[viewport] = {"name": "SEM %d" % (i + 1),
                                 "stage": main_data.stage,
                                 "stream_classes": EMStream,
                                 }

        # If Optical only: all optical
        elif not main_data.ebeam and main_data.light:
            logging.info("Creating Optical only viewport layout")
            vpv = collections.OrderedDict()
            for i, viewport in enumerate(viewports):
                vpv[viewport] = {"name": "Optical %d" % (i + 1),
                                 "stage": main_data.stage,
                                 "stream_classes": OpticalStream,
                                 }
        else:
            logging.warning("No known microscope configuration, creating %d "
                            "generic views", len(viewports))
            vpv = collections.OrderedDict()
            for i, viewport in enumerate(viewports):
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

        # If there are 5 viewports, we'll assume that the last one is an overview camera stream
        if len(viewports) == 5:
            logging.debug("Inserting Overview viewport")
            vpv[viewports[4]] = {
                "cls": guimod.OverviewView,
                "name": "Overview",
                "stage": main_data.stage,
                "stream_classes": (RGBCameraStream, BrightfieldStream),
            }

        # Add connection to SEM hFoV if possible (on SEM-only views)
        if main_data.ebeamControlsMag:
            for vp, v in vpv.items():
                if v.get("stream_classes") == EMStream:
                    v["fov_va"] = main_data.ebeam.horizontalFoV
                    vp.canvas.fit_view_to_next_image = False

        return vpv

    def _onAutofocus(self, active):
        # Determine which stream is active
        if active:
            try:
                curr_s = self.tab_data_model.streams.value[0]
            except IndexError:
                # Should not happen as the menu/icon should be disabled
                logging.info("No stream to run the autofocus")
                self.tab_data_model.autofocus_active.value = False
                return

            # run only if focuser is available
            if curr_s.focuser:
                self._autofocus_f = AutoFocus(curr_s.detector, curr_s.emitter, curr_s.focuser)
                self._autofocus_f.add_done_callback(self._on_autofocus_done)
            else:
                # Should never happen as normally the menu/icon are disabled
                logging.info("Autofocus cannot run as no hardware is available")
                self.tab_data_model.autofocus_active.value = False
        else:
            self._autofocus_f.cancel()

    def _on_autofocus_done(self, future):
        self.tab_data_model.autofocus_active.value = False

    def _on_current_stream(self, streams):
        """
        Called when some VAs affecting the current stream change
        """
        # Try to get the current stream
        try:
            curr_s = streams[0]
        except IndexError:
            curr_s = None

        if curr_s:
            curr_s.should_update.subscribe(self._on_stream_update, init=True)
        else:
            wx.CallAfter(self.tb.enable_button, tools.TOOL_AUTO_FOCUS, False)

    def _on_stream_update(self, updated):
        """
        Called when the current stream changes play/pause
        Used to update the autofocus button
        """
        # TODO: just let the menu controller also update toolbar (as it also
        # does the same check for the menu entry)
        try:
            curr_s = self.tab_data_model.streams.value[0]
        except IndexError:
            f = None
        else:
            f = curr_s.focuser

        f_enable = all((updated, f))
        if not f_enable:
            self.tab_data_model.autofocus_active.value = False
        wx.CallAfter(self.tb.enable_button, tools.TOOL_AUTO_FOCUS, f_enable)

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
                self._streambar_controller.addFluo(add_to_view=True, play=False)
                # don't forbid to remove it, as for the user it can be easier to
                # remove than change all the values

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
                sp = self._streambar_controller.addFluo(add_to_view=True)
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
            else: # Could happen if the user has deleted all the optical streams
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

        # pause / restart streams when not displayed
        if show:
            # TODO: double check the chamber state hasn't changed in between
            # We should never turn on the streams if the chamber is not in vacuum
            self._streambar_controller.resumeStreams(self._streams_to_restart)
        else:
            paused_st = self._streambar_controller.pauseStreams()
            self._streams_to_restart = weakref.WeakSet(paused_st)


class SparcAcquisitionTab(Tab):
    def __init__(self, name, button, panel, main_frame, main_data):
        tab_data = guimod.SparcAcquisitionGUIData(main_data)
        super(SparcAcquisitionTab, self).__init__(name, button, panel, main_frame, tab_data)

        # Create the streams (first, as SEM viewport needs SEM concurrent stream):
        # * SEM (survey): live stream displaying the current SEM view (full FoV)
        # * Spot SEM: live stream to set e-beam into spot mode
        # * SEM (concurrent): SEM stream used to store SEM settings for final acquisition.
        #           That's tab_data.semStream
        # When one new stream is added, it actually creates two streams:
        # * XXXSettingsStream: for the live view and the settings
        # * MDStream: for the acquisition (view)

        # For remembering which streams are paused when hiding the tab
        self._streams_to_restart = set()  # set of weakref to the streams

        # Only put the VAs that do directly define the image as local, everything
        # else should be global. The advantage is double: the global VAs will
        # set the hardware even if another stream (also using the e-beam) is
        # currently playing, and if the VAs are changed externally, the settings
        # will be displayed correctly (and not reset the values on next play).
        emtvas = set()
        hwemtvas = set()
        for vaname in get_local_vas(main_data.ebeam):
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
            detvas=get_local_vas(main_data.sed),
        )

        sem_stream.should_update.value = True  # TODO: put it in _streams_to_restart instead?
        tab_data.acquisitionView.addStream(sem_stream)  # it should also be saved

        # This stream is a bit tricky, because it will play (potentially)
        # simultaneously as another one, and it changes the SEM settings at
        # play and pause.
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
            "Secondary electrons concurrent",  # name matters, used to find the stream for the ROI
            main_data.sed,
            main_data.sed.data,
            main_data.ebeam
            # No local VAs,
        )
        tab_data.semStream = semcl_stream

        # drift correction is disabled until a roi is selected
        semcl_stream.dcRegion.value = acqstream.UNDEFINED_ROI
        # Set anchor region dwell time to the same value as the SEM survey
        sem_stream.emtDwellTime.subscribe(self._copyDwellTimeToAnchor, init=True)

        # Add the SEM stream to the view
        tab_data.streams.value.append(sem_stream)
        # To make sure the spot mode is stopped when the tab loses focus
        tab_data.streams.value.append(spot_stream)

        viewports = panel.pnl_sparc_grid.viewports
        for vp in viewports[:4]:
            assert(isinstance(vp, (MicroscopeViewport, PlotViewport)))

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
            (viewports[3],
             {"name": "Monochromator",
              "stream_classes": MonochromatorSettingsStream,
              }),
        ])
        # Add connection to SEM hFoV if possible
        if main_data.ebeamControlsMag:
            vpv[viewports[0]]["fov_va"] = main_data.ebeam.horizontalFoV
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
                (panel.vp_sparc_br, panel.lbl_sparc_view_br)),
        ])

        self._view_selector = viewcont.ViewButtonController(tab_data, panel, buttons, viewports)

        # Toolbar
        self.tb = self.panel.sparc_acq_toolbar
        self.tb.add_tool(tools.TOOL_ROA, tab_data.tool)
        self.tb.add_tool(tools.TOOL_RO_ANCHOR, tab_data.tool)
        self.tb.add_tool(tools.TOOL_SPOT, tab_data.tool)
        # TODO: Add the buttons when the functionality is there
        # self.tb.add_tool(tools.TOOL_POINT, tab_data.tool)
        # self.tb.add_tool(tools.TOOL_RO_ZOOM, tab_data.tool)
        self.tb.add_tool(tools.TOOL_ZOOM_FIT, self.view_controller.fitViewToContent)

        tab_data.tool.subscribe(self.on_tool_change)

        # Create Stream Bar Controller
        self._stream_controller = streamcont.SparcStreamsController(
            tab_data,
            panel.pnl_sparc_streams,
            self.view_controller,
            ignore_view=True  # Show all stream panels, independent of any selected viewport
        )

        # The sem stream is always visible, so add it by default
        sem_stream_cont = self._stream_controller.addStream(sem_stream, add_to_view=True)
        sem_stream_cont.stream_panel.show_remove_btn(False)
        sem_stream_cont.stream_panel.show_visible_btn(False)

        # TODO: move the entry to the "acquisition" panel?
        # We add on the SEM live stream panel, the VA for the SEM concurrent stream
        self.sem_dcperiod_ent = sem_stream_cont.add_setting_entry(
            "dcPeriod",
            semcl_stream.dcPeriod,
            None,  # component
            get_stream_settings_config()[acqstream.SEMStream]["dcPeriod"]
        )
        semcl_stream.dcRegion.subscribe(self._onDCRegion, init=True)

        # add "Use scan stage" check box if scan_stage is present
        sstage = main_data.scan_stage
        if sstage:
            # Move the scan stage to the center (so that scan has maximum range)
            ssaxes = sstage.axes
            posc = {"x": sum(ssaxes["x"].range) / 2,
                    "y": sum(ssaxes["y"].range) / 2}
            sstage.moveAbs(posc)

            self.scan_stage_ent = sem_stream_cont.add_setting_entry(
                "useScanStage",
                tab_data.useScanStage,
                None,  # component
                get_stream_settings_config()[acqstream.SEMStream]["useScanStage"]
            )

            # Draw the limits on the SEM view
            tab_data.useScanStage.subscribe(self._on_use_scan_stage, init=True)
            roi = (ssaxes["x"].range[0] - posc["x"],
                   ssaxes["y"].range[0] - posc["y"],
                   ssaxes["x"].range[1] - posc["x"],
                   ssaxes["y"].range[1] - posc["y"])
            panel.vp_sparc_tl.set_stage_limits(roi)

        main_data.is_acquiring.subscribe(self.on_acquisition)

        self._acquisition_controller = acqcont.SparcAcquiController(
            tab_data,
            panel,
            self.stream_controller
        )

        # Force SEM view fit to content when magnification is updated
        if not main_data.ebeamControlsMag:
            main_data.ebeam.magnification.subscribe(self._onSEMMag)

    @property
    def stream_controller(self):
        return self._stream_controller

    def on_tool_change(self, tool):
        """ Ensure spot position is always defined when using the spot """
        if tool == guimod.TOOL_SPOT:
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

    def on_acquisition(self, is_acquiring):
        # TODO: Make sure nothing can be modified during acquisition

        self.tb.enable(not is_acquiring)
        self.panel.vp_sparc_tl.Enable(not is_acquiring)
        self.panel.btn_sparc_change_file.Enable(not is_acquiring)

    def _copyDwellTimeToAnchor(self, dt):
        """
        Use the sem stream dwell time as the anchor dwell time
        """
        self.tab_data_model.semStream.dcDwellTime.value = dt

    def _on_use_scan_stage(self, use):
        if use:
            self.panel.vp_sparc_tl.show_stage_limit_overlay()
        else:
            self.panel.vp_sparc_tl.hide_stage_limit_overlay()

    @call_in_wx_main
    def _onDCRegion(self, roi):
        """
        Called when the Anchor region changes.
        Used to enable/disable the drift correction period control
        """
        enabled = (roi != acqstream.UNDEFINED_ROI)
        self.sem_dcperiod_ent.lbl_ctrl.Enable(enabled)
        self.sem_dcperiod_ent.value_ctrl.Enable(enabled)

    def Show(self, show=True):
        assert (show != self.IsShown())  # we assume it's only called when changed
        super(SparcAcquisitionTab, self).Show(show)

        # pause / restart streams when not displayed
        if show:
            # TODO: double check the chamber state hasn't changed in between
            # We should never turn on the streams if the chamber is not in vacuum
            self._stream_controller.resumeStreams(self._streams_to_restart)
        else:
            paused_st = self._stream_controller.pauseStreams()
            self._streams_to_restart = weakref.WeakSet(paused_st)

    def terminate(self):
        # make sure the streams are stopped
        for s in self.tab_data_model.streams.value:
            s.is_active.value = False


# Different states of the mirror stage positions
MIRROR_NOT_REFD = 0
MIRROR_PARKED = 1
MIRROR_BAD = 2  # not parked, but not fully engaged either
MIRROR_ENGAGED = 3

# Position of the mirror to be under the e-beam, when we don't know better
# Note: the exact position is reached by mirror alignment procedure
MIRROR_POS_PARKED = {"l": 0, "s": 0}  # (Hopefully) constant, and same as reference position
MIRROR_ONPOS_RADIUS = 2e-3  # m, distance from a position that is still considered that position


class ChamberTab(Tab):
    def __init__(self, name, button, panel, main_frame, main_data):
        """ SPARC2 chamber view tab """

        tab_data = guimod.ChamberGUIData(main_data)
        super(ChamberTab, self).__init__(name, button, panel, main_frame, tab_data)

        # future to handle the move
        self._move_future = model.InstantaneousFuture()
        # list of moves that still need to be executed, FIFO
        self._next_moves = collections.deque()  # tuple callable, arg

        # Position to where to go when requested to be engaged
        try:
            self._pos_engaged = main_data.mirror.getMetadata()[model.MD_FAV_POS_ACTIVE]
        except KeyError:
            raise ValueError("Mirror actuator has no metadata FAV_POS_ACTIVE")

        mstate = self._get_mirror_state()
        # If mirror stage not engaged, make this tab the default
        if mstate != MIRROR_ENGAGED:
            self.make_default()

        self._update_mirror_status()

        # Create stream & view
        self._stream_controller = streamcont.StreamBarController(
            tab_data,
            panel.pnl_streams,
            locked=True
        )

        # create a view on the microscope model
        vpv = collections.OrderedDict((
            (self.panel.vp_chamber,
                {
                    "name": "Chamber view",
                }
            ),
        ))
        self.view_controller = viewcont.ViewPortController(tab_data, panel, vpv)
        view = self.tab_data_model.focussedView.value
        view.show_crosshair.value = False

        # With the lens, the image must be flipped to keep the mirror at the top and the sample
        # at the bottom.
        self.panel.vp_chamber.SetFlip(wx.VERTICAL)

        # Just one stream: chamber view
        self._ccd_stream = acqstream.CameraStream("Chamber view",
                                                  main_data.ccd, main_data.ccd.data,
                                                  emitter=None,
                                                  focuser=main_data.focus,
                                                  detvas=get_local_vas(main_data.ccd))
        # Make sure image has square pixels and full FoV
        self._ccd_stream.detBinning.value = (1, 1)
        self._ccd_stream.detResolution.value = self._ccd_stream.detResolution.range[1]
        ccd_spe = self._stream_controller.addStream(self._ccd_stream)
        ccd_spe.stream_panel.flatten()  # No need for the stream name
        self._ccd_stream.should_update.value = True

        panel.btn_switch_mirror.Bind(wx.EVT_BUTTON, self._on_switch_btn)
        panel.btn_cancel.Bind(wx.EVT_BUTTON, self._on_cancel)

        # Show position of the stage via the progress bar
        # Note: we could have used the progress bar to represent the progress of
        # the move. However, it's a lot of work because the future is not a
        # progressive future, so the progress would need to be guessed based on
        # time or position. In addition, it would not give any cue to the user
        # about the current position of the mirror when no move is happening.
        main_data.mirror.position.subscribe(self._update_progress_bar, init=True)
        self._pulse_timer = wx.PyTimer(self._pulse_progress_bar)  # only used during referencing

    def _get_mirror_state(self):
        """
        Return the state of the mirror stage (in term of position)
        Note: need self._pos_engaged
        return (MIRROR_*)
        """
        mirror = self.tab_data_model.main.mirror
        if not all(mirror.referenced.value.values()):
            return MIRROR_NOT_REFD

        pos = mirror.position.value
        dist_parked = math.hypot(pos["l"] - MIRROR_POS_PARKED["l"],
                                 pos["s"] - MIRROR_POS_PARKED["s"])
        dist_engaged = math.hypot(pos["l"] - self._pos_engaged["l"],
                                  pos["s"] - self._pos_engaged["s"])
        if dist_parked <= MIRROR_ONPOS_RADIUS:
            return MIRROR_PARKED
        elif dist_engaged <= MIRROR_ONPOS_RADIUS:
            return MIRROR_ENGAGED
        else:
            return MIRROR_BAD

    @call_in_wx_main
    def _update_progress_bar(self, pos):
        """
        Update the progress bar, based on the current position/state of the mirror.
        Called when the position of the mirror changes.
        pos (dict str->float): current position of the mirror stage
        """
        # If not yet referenced, the position is pretty much meaning less
        # => just say it's "somewhere in the middle".
        mirror = self.tab_data_model.main.mirror
        if not all(mirror.referenced.value.values()):
            # In case it pulses, wxGauge still holds the old value, so it might
            # think it doesn't need to refresh unless the value changes.
            self.panel.gauge_move.Value = 0
            self.panel.gauge_move.Value = 50  # Range is 100 => 50%
            return

        # We map the position between Parked -> Engaged. The basic is simple,
        # we could just map the current position on the segment. But we also
        # want to show a position "somewhere in the middle" when the mirror is
        # at a random bad position.
        dist_parked = math.hypot(pos["l"] - MIRROR_POS_PARKED["l"],
                                 pos["s"] - MIRROR_POS_PARKED["s"])
        dist_engaged = math.hypot(pos["l"] - self._pos_engaged["l"],
                                  pos["s"] - self._pos_engaged["s"])
        tot_dist = math.hypot(MIRROR_POS_PARKED["l"] - self._pos_engaged["l"],
                              MIRROR_POS_PARKED["s"] - self._pos_engaged["s"])
        ratio_to_engaged = dist_engaged / tot_dist
        ratio_to_parked = dist_parked / tot_dist
        if ratio_to_parked < ratio_to_engaged:
            val = ratio_to_parked * 100
        else:
            val = (1 - ratio_to_engaged) * 100

        val = min(max(0, int(round(val))), 100)
        logging.debug("dist (%f/%f) over %f m -> %d %%", dist_parked, dist_engaged,
                      tot_dist, val)
        self.panel.gauge_move.Value = val

    def _pulse_progress_bar(self):
        """
        Stupid method that changes the progress bar. Used to indicate something
         is happening (during referencing).
        """
        mirror = self.tab_data_model.main.mirror
        if not all(mirror.referenced.value.values()):
            self.panel.gauge_move.Pulse()

    def _on_switch_btn(self, evt):
        """
        Called when the Park/Engage button is pressed.
          Start move either for referencing/parking, or for putting the mirror in position
        """
        if not evt.isDown:
            # just got unpressed -> that means we need to stop the current move
            return self._on_cancel(evt)

        if self._next_moves:
            logging.warning("Going to move mirror while still %d moves scheduled",
                            len(self._next_moves))

        # Decide which move to do
        # Note: s axis can only be moved when near engaged pos. So:
        #  * when parking/referencing => move s first, then l
        #  * when engaging => move l first, then s
        mstate = self._get_mirror_state()
        mirror = self.tab_data_model.main.mirror
        if mstate == MIRROR_PARKED:
            # => Engage
            f1 = (mirror.moveAbs, {"l": self._pos_engaged["l"]})
            f2 = (mirror.moveAbs, {"s": self._pos_engaged["s"]})
            moves = (f1, f2)
            btn_text = "ENGAGING MIRROR"
        elif mstate == MIRROR_NOT_REFD:
            # => Reference
            f1 = (mirror.reference, {"s"})
            f2 = (mirror.reference, {"l"})
            moves = (f1, f2)
            btn_text = "PARKING MIRROR"
            # position doesn't update during referencing, so just pulse
            self._pulse_timer.Start(250.0)  # 4 Hz
        else:
            # => Park
            # Use standard move to show the progress of the mirror position, but
            # finish by (normally fast) referencing to be sure it really moved
            # to the parking position.
            f1 = (mirror.moveAbs, {"s": MIRROR_POS_PARKED["s"]})
            f2 = (mirror.moveAbs, {"l": MIRROR_POS_PARKED["l"]})
            f3 = (mirror.reference, {"s"})
            f4 = (mirror.reference, {"l"})
            moves = (f1, f2, f3, f4)
            btn_text = "PARKING MIRROR"

        self._next_moves.extend(moves)
        c, a = self._next_moves.popleft()
        self._move_future = c(a)
        self._move_future.add_done_callback(self._on_move_done)

        self.tab_data_model.main.is_acquiring.value = True

        self.panel.btn_cancel.Enable()
        self.panel.btn_switch_mirror.SetLabel(btn_text)

    def _on_move_done(self, future):
        """
        Called when one (sub) move is over, (either successfully or not)
        """
        try:
            future.result()
        except Exception as ex:
            # Something went wrong, don't go any further
            if not isinstance(ex, CancelledError):
                logging.warning("Failed to move mirror: %s", ex)
            logging.debug("Cancelling next %d moves", len(self._next_moves))
            self._next_moves.clear()
            self._on_full_move_end()

        # Schedule next sub-move
        try:
            c, a = self._next_moves.popleft()
        except IndexError:  # We're done!
            self._on_full_move_end()
        else:
            self._move_future = c(a)
            self._move_future.add_done_callback(self._on_move_done)

    def _on_cancel(self, evt):
        """
        Called when the cancel button is pressed, or the move button is untoggled
        """
        # Remove any other queued moves
        self._next_moves.clear()
        # Cancel the running move
        self._move_future.cancel()
        logging.info("Mirror move cancelled")

    @call_in_wx_main
    def _on_full_move_end(self):
        """
        Called when a complete move (L+S) is over (either successfully or not)
        """
        # In case it was referencing
        self._pulse_timer.Stop()

        # It's a toggle button, and the user toggled down, so need to untoggle it
        self.panel.btn_switch_mirror.SetValue(False)
        self.panel.btn_cancel.Disable()
        self.tab_data_model.main.is_acquiring.value = False
        self._update_mirror_status()
        # Just in case the referencing updated position before the pulse was stopped
        self._update_progress_bar(self.tab_data_model.main.mirror.position.value)

    def _update_mirror_status(self):
        """
        Check the current hardware status and update the button text and info
        text based on this.
        Note: must be called within the main GUI thread
        """
        mstate = self._get_mirror_state()

        if mstate == MIRROR_NOT_REFD:
            txt_warning = ("Parking the mirror at least once is required in order "
                           "to reference the actuators.")
        elif mstate == MIRROR_BAD:
            txt_warning = "The mirror is neither fully parked nor entirely engaged."
        else:
            txt_warning = None

        self.panel.pnl_ref_msg.Show(txt_warning is not None)
        if txt_warning:
            self.panel.txt_warning.SetLabel(txt_warning)
            self.panel.txt_warning.Wrap(self.panel.pnl_ref_msg.Size[0] - 16)

        if mstate == MIRROR_PARKED:
            btn_text = "ENGAGE MIRROR"
        else:
            btn_text = "PARK MIRROR"

        self.panel.btn_switch_mirror.SetLabel(btn_text)

        # If the mirror is parked, we still allow the user to go to acquisition
        # but it's unlikely to be a good idea => indicate that something needs
        # to be done here first. Also prevent to use alignment tab, as we don't
        # want to let the use move the mirror all around the chamber.
        self.highlight(mstate != MIRROR_ENGAGED)

        try:
            tab_align = self.tab_data_model.main.getTabByName("sparc2_align")
            tab_align.button.Enable(mstate == MIRROR_ENGAGED)
        except LookupError:
            logging.debug("Failed to find the alignment tab")

    def Show(self, show=True):
        Tab.Show(self, show=show)

        # Start chamber view when tab is displayed, and otherwise, stop it
        self._ccd_stream.should_update.value = show

        # If there is an actuator, disable the lens
        if show:
            self.tab_data_model.main.opm.setPath("chamber-view")
            # Update if the mirror has been aligned
            self._pos_engaged = self.tab_data_model.main.mirror.getMetadata()[model.MD_FAV_POS_ACTIVE]
            # Just in case the mirror was moved from another client (eg, cli)
            self._update_mirror_status()

        # When hidden, the new tab shown is in charge to request the right
        # optical path mode, if needed.

    def terminate(self):
        self._ccd_stream.is_active.value = False


class AnalysisTab(Tab):
    """ Handle the loading and displaying of acquisition files

    Creation
    ~~~~~~~~

    During creation, the following controllers are created:

    ViewPortController
      Processes the given viewports by creating views for them, determining which stream classes
      those views can handle and finally assigning them to their viewport.

    StreamController
      Keeps track of the available streams, which are all static

    ViewButtonController
        blah blah

    Loading Data
    ~~~~~~~~~~~~

    In the `load_data` method the file data is loaded using the appropriate converter. It's then
    passed on to the `display_new_data` method, which analyzes which static streams need to be
    created. The StreamController is then asked to create the actual stream object and it also adds
    them to every view which supports that (sub)type of stream.

    """

    def __init__(self, name, button, panel, main_frame, main_data):
        """
        microscope will be used only to select the type of views
        """
        # TODO: automatically change the display type based on the acquisition
        # displayed
        tab_data = guimod.AnalysisGUIData(main_data)
        super(AnalysisTab, self).__init__(name, button, panel, main_frame, tab_data)

        # Connect viewports
        viewports = panel.pnl_inspection_grid.viewports
        # Viewport type checking to avoid mismatches
        for vp in viewports[:4]:
            assert(isinstance(vp, MicroscopeViewport))
        assert(isinstance(viewports[4], AngularResolvedViewport))
        assert(isinstance(viewports[5], PlotViewport))
        assert(isinstance(viewports[6], SpatialSpectrumViewport))

        vpv = collections.OrderedDict([
            (viewports[0],  # focused view
             {"name": "Optical",
              "stream_classes": (OpticalStream, SpectrumStream, CLStream),
              }),
            (viewports[1],
             {"name": "SEM",
              "stream_classes": EMStream,
              }),
            (viewports[2],
             {"name": "Combined 1",
              "stream_classes": (EMStream, OpticalStream, SpectrumStream, CLStream),
              }),
            (viewports[3],
             {"name": "Combined 2",
              "stream_classes": (EMStream, OpticalStream, SpectrumStream, CLStream),
              }),
            (viewports[4],
             {"name": "Angle-resolved",
              "stream_classes": ARStream,
              }),
            (viewports[5],
             {"name": "Spectrum plot",
              "stream_classes": SpectrumStream,
              }),
            (viewports[6],
             {"name": "Spatial spectrum",
              "stream_classes": (SpectrumStream, CLStream),
              }),
        ])

        self.view_controller = viewcont.ViewPortController(tab_data, panel, vpv)

        # Connect view selection button
        buttons = collections.OrderedDict([
            (
                panel.btn_inspection_view_all,
                (None, panel.lbl_inspection_view_all)
            ),
            (
                panel.btn_inspection_view_tl,
                (panel.vp_inspection_tl, panel.lbl_inspection_view_tl)
            ),
            (
                panel.btn_inspection_view_tr,
                (panel.vp_inspection_tr, panel.lbl_inspection_view_tr)
            ),
            (
                panel.btn_inspection_view_bl,
                (panel.vp_inspection_bl, panel.lbl_inspection_view_bl)
            ),
            (
                panel.btn_inspection_view_br,
                (panel.vp_inspection_br, panel.lbl_inspection_view_br)
            )
        ])

        self._view_selector = viewcont.ViewButtonController(tab_data, panel, buttons, viewports)

        # Toolbar
        self.tb = panel.ana_toolbar
        # TODO: Add the buttons when the functionality is there
        # tb.add_tool(tools.TOOL_RO_ZOOM, self.tab_data_model.tool)
        self.tb.add_tool(tools.TOOL_POINT, self.tab_data_model.tool)
        self.tb.enable_button(tools.TOOL_POINT, False)
        self.tb.add_tool(tools.TOOL_LINE, self.tab_data_model.tool)
        self.tb.enable_button(tools.TOOL_LINE, False)
        self.tb.add_tool(tools.TOOL_ZOOM_FIT, self.view_controller.fitViewToContent)

        # save the views to be able to reset them later
        self._def_views = list(tab_data.visible_views.value)

        # Show the streams (when a file is opened)
        self._stream_controller = streamcont.StreamBarController(
            tab_data,
            panel.pnl_inspection_streams,
            static=True
        )

        # Show the file info and correction selection
        self._settings_controller = settings.AnalysisSettingsController(
            panel,
            tab_data
        )
        self._settings_controller.setter_ar_file = self.set_ar_background
        self._settings_controller.setter_spec_bck_file = self.set_spec_background
        self._settings_controller.setter_spec_file = self.set_spec_comp

        self.panel.btn_open_image.Bind(wx.EVT_BUTTON, self.on_file_open_button)

        self.tab_data_model.tool.subscribe(self._onTool)

    @property
    def stream_controller(self):
        return self._stream_controller

    def select_acq_file(self):
        """ Open an image file using a file dialog box

        return (boolean): True if the user did pick a file, False if it was
        cancelled.
        """
        # Find the available formats (and corresponding extensions)
        formats_to_ext = dataio.get_available_formats(os.O_RDONLY)

        fi = self.tab_data_model.acq_fileinfo.value

        if fi and fi.file_name:
            path, _ = os.path.split(fi.file_name)
        else:
            config = get_acqui_conf()
            path = config.last_path

        wildcards, formats = guiutil.formats_to_wildcards(formats_to_ext, include_all=True)
        dialog = wx.FileDialog(self.panel,
                               message="Choose a file to load",
                               defaultDir=path,
                               defaultFile="",
                               style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
                               wildcard=wildcards)

        # Show the dialog and check whether is was accepted or cancelled
        if dialog.ShowModal() != wx.ID_OK:
            return False

        # Detect the format to use
        filename = dialog.GetPath()
        logging.debug("Current file set to %s", filename)

        fmt = formats[dialog.GetFilterIndex()]

        Message.show_message(self.main_frame, "Opening file")
        self.load_data(filename, fmt)
        return True

    def on_file_open_button(self, _):
        self.select_acq_file()

    def load_data(self, filename, fmt=None):

        if fmt is None:
            formats_to_ext = dataio.get_available_formats(os.O_RDONLY)
            _, formats = guiutil.formats_to_wildcards(formats_to_ext, include_all=True)

            # Try to guess from the extension
            for f, exts in formats_to_ext.items():
                if any(filename.endswith(e) for e in exts):
                    fmt = f
                    break
            else:
                # pick a random format hoping it's the right one
                fmt = formats[1]
                logging.warning("Couldn't guess format from filename '%s', will use %s.",
                                filename, fmt)

        converter = dataio.get_converter(fmt)
        try:
            data = converter.read_data(filename)
        except Exception:
            logging.exception("Failed to open file '%s' with format %s", filename, fmt)

        self.display_new_data(filename, data)

    @call_in_wx_main
    def display_new_data(self, filename, data):
        """
        Display a new data set (removing all references to the current one)

        filename (string): Name of the file containing the data.
        data (list of model.DataArray): the data to display. Should have at
         least one DataArray.
        """

        # Reset tool, layout and visible views
        self.tab_data_model.tool.value = guimod.TOOL_NONE
        self.tab_data_model.viewLayout.value = guimod.VIEW_LAYOUT_22

        new_visible_views = list(self._def_views)  # Use a copy

        # Create a new file info model object
        fi = guimod.FileInfo(filename)

        # Remove all the previous streams
        self._stream_controller.clear()
        # Clear any old plots
        self.panel.vp_inspection_plot.clear()
        self.panel.vp_spatialspec.clear()
        self.panel.vp_angular.clear()

        # Force the canvases to fit to the content
        for vp in [self.panel.vp_inspection_tl,
                   self.panel.vp_inspection_tr,
                   self.panel.vp_inspection_bl,
                   self.panel.vp_inspection_br]:
            vp.canvas.fit_view_to_next_image = True

        # Fetch the acquisition date
        acq_date = fi.metadata.get(model.MD_ACQ_DATE, None)

        # Add each data as a stream of the correct type
        for d in data:
            # Get the earliest acquisition date
            try:
                im_acq_date = d.metadata[model.MD_ACQ_DATE]
                acq_date = min(acq_date or im_acq_date, im_acq_date)
            except KeyError:  # no MD_ACQ_DATE
                pass  # => don't update the acq_date

        if acq_date:
            fi.metadata[model.MD_ACQ_DATE] = acq_date
        self.tab_data_model.acq_fileinfo.value = fi

        # Create streams from data
        streams = data_to_static_streams(data)

        # Spectrum and AR streams are, for now, considered mutually exclusive
        spec_streams = [s for s in streams if isinstance(s, acqstream.SpectrumStream)]
        ar_streams = [s for s in streams if isinstance(s, acqstream.ARStream)]

        # TODO: Move viewport related code to ViewPortController
        # TODO: to support multiple (types of) streams (eg, AR+Spec+Spec), do
        # this every time the streams are hidden/displayed.
        if spec_streams:

            # ########### Track pixel and line selection

            for spec_stream in spec_streams:
                sraw = spec_stream.raw[0]

                # We need to get the dimensions so we can determine the
                # resolution. Remember that in Matrix notation, the
                # number of rows (is vertical size), comes first. So we
                # need to 'swap' the values to get the (x,y) resolution.
                height, width = sraw.shape[-2], sraw.shape[-1]

                # Set the PointOverlay values for each viewport
                for viewport in self.view_controller.viewports:
                    if hasattr(viewport.canvas, "pixel_overlay"):
                        ol = viewport.canvas.pixel_overlay
                        ol.set_data_properties(
                            sraw.metadata[model.MD_PIXEL_SIZE][0],
                            sraw.metadata[model.MD_POS],
                            (width, height)
                        )
                        ol.connect_selection(spec_stream.selected_pixel, spec_stream.selectionWidth)

                    if hasattr(viewport.canvas, "line_overlay"):
                        ol = viewport.canvas.line_overlay
                        ol.set_data_properties(
                            sraw.metadata[model.MD_PIXEL_SIZE][0],
                            sraw.metadata[model.MD_POS],
                            (width, height)
                        )
                        ol.connect_selection(spec_stream.selected_line,
                                             spec_stream.selectionWidth,
                                             spec_stream.selected_pixel)

                spec_stream.selected_pixel.subscribe(self._on_pixel_select, init=True)
                spec_stream.selected_line.subscribe(self._on_line_select, init=True)

            # ########### Combined views and spectrum view visible

            new_visible_views[0:2] = self._def_views[2:4]  # Combined
            new_visible_views[2] = self.panel.vp_spatialspec.microscope_view
            new_visible_views[3] = self.panel.vp_inspection_plot.microscope_view

            # ########### Update tool menu

            self.tb.enable_button(tools.TOOL_POINT, True)
            self.tb.enable_button(tools.TOOL_LINE, True)

        elif ar_streams:

            # ########### Track point selection

            for ar_stream in ar_streams:
                for viewport in self.view_controller.viewports:
                    if hasattr(viewport.canvas, "points_overlay"):
                        ol = viewport.canvas.points_overlay
                        ol.set_point(ar_stream.point)

                ar_stream.point.subscribe(self._on_point_select, init=True)

            # ########### Combined views and Angular view visible

            new_visible_views[0] = self._def_views[1] # SEM only
            new_visible_views[1] = self._def_views[2] # Combined 1
            new_visible_views[2] = self.panel.vp_angular.microscope_view
            new_visible_views[3] = self._def_views[3] # Combined 2

            # ########### Update tool menu

            self.tb.enable_button(tools.TOOL_POINT, True)
            self.tb.enable_button(tools.TOOL_LINE, False)
        else:
            # ########### Update tool menu
            self.tb.enable_button(tools.TOOL_POINT, False)
            self.tb.enable_button(tools.TOOL_LINE, False)

        # Only show the panels that fit the current streams
        self._settings_controller.show_calibration_panel(len(ar_streams) > 0, len(spec_streams) > 0)

        # Load the Streams and their data into the model and views
        for s in streams:
            self._stream_controller.addStream(s, add_to_view=True)

        # Reload current calibration on the new streams (must be done after .streams is set)
        if spec_streams:
            try:
                self.set_spec_background(self.tab_data_model.spec_bck_cal.value)
            except ValueError:
                logging.warning(u"Calibration file not accepted any more '%s'",
                                self.tab_data_model.spec_bck_cal.value)
                self.tab_data_model.spec_bck_cal.value = u""  # remove the calibration

            try:
                self.set_spec_comp(self.tab_data_model.spec_cal.value)
            except ValueError:
                logging.warning(u"Calibration file not accepted any more '%s'",
                                self.tab_data_model.spec_cal.value)
                self.tab_data_model.spec_cal.value = u""  # remove the calibration

        if ar_streams:
            try:
                self.set_ar_background(self.tab_data_model.ar_cal.value)
            except ValueError:
                logging.warning(u"Calibration file not accepted any more '%s'",
                                self.tab_data_model.ar_cal.value)
                self.tab_data_model.ar_cal.value = u""  # remove the calibration

        # Update the visible views if they've changed
        for vold, vnew in zip(self.tab_data_model.visible_views.value, new_visible_views):
            if vold != vnew:
                self.tab_data_model.visible_views.value = new_visible_views
                break

    def set_ar_background(self, fn):
        """
        Load the data from the AR background file and apply to streams
        return (unicode): the filename as it has been accepted
        raise ValueError if the file is not correct or calibration cannot be applied
        """
        try:
            if fn == u"":
                logging.debug("Clearing AR background")
                cdata = None
            else:
                logging.debug("Loading AR background data")
                converter = dataio.find_fittest_converter(fn, mode=os.O_RDONLY)
                data = converter.read_data(fn)
                # will raise exception if doesn't contain good calib data
                cdata = calibration.get_ar_data(data)

            # Apply data to the relevant streams
            ar_strms = [s for s in self.tab_data_model.streams.value
                        if isinstance(s, acqstream.ARStream)]

            # This might raise more exceptions if calibration is not compatible
            # with the data.
            for strm in ar_strms:
                strm.background.value = cdata

        except Exception, err:
            logging.info("Failed using file %s as AR background", fn, exc_info=True)
            msg = "File '%s' not suitable as angle-resolved background:\n\n%s"
            dlg = wx.MessageDialog(self.main_frame,
                                   msg % (fn, err),
                                   "Unusable AR background file",
                                   wx.OK | wx.ICON_STOP)
            dlg.ShowModal()
            dlg.Destroy()
            raise ValueError("File '%s' not suitable" % fn)

        return fn

    def set_spec_background(self, fn):
        """
        Load the data from a spectrum (background) file and apply to streams
        return (unicode): the filename as it has been accepted
        raise ValueError if the file is not correct or calibration cannot be applied
        """
        try:
            if fn == u"":
                logging.debug("Clearing spectrum background")
                cdata = None
            else:
                logging.debug("Loading spectrum background")
                converter = dataio.find_fittest_converter(fn, mode=os.O_RDONLY)
                data = converter.read_data(fn)
                # will raise exception if doesn't contain good calib data
                cdata = calibration.get_spectrum_data(data)

            spec_strms = [s for s in self.tab_data_model.streams.value
                          if isinstance(s, acqstream.SpectrumStream)]

            for strm in spec_strms:
                strm.background.value = cdata

        except Exception, err:  # pylint: disable=W0703
            logging.info("Failed using file %s as spectrum background", fn, exc_info=True)
            msg = "File '%s' not suitable for spectrum background:\n\n%s"
            dlg = wx.MessageDialog(self.main_frame,
                                   msg % (fn, err),
                                   "Unusable spectrum background file",
                                   wx.OK | wx.ICON_STOP)
            dlg.ShowModal()
            dlg.Destroy()
            raise ValueError("File '%s' not suitable" % fn)

        return fn

    def set_spec_comp(self, fn):
        """
        Load the data from a spectrum calibration file and apply to streams
        return (unicode): the filename as it has been accepted
        raise ValueError if the file is not correct or calibration cannot be applied
        """
        try:
            if fn == u"":
                logging.debug("Clearing spectrum efficiency compensation")
                cdata = None
            else:
                logging.debug("Loading spectrum efficiency compensation")
                converter = dataio.find_fittest_converter(fn, mode=os.O_RDONLY)
                data = converter.read_data(fn)
                # will raise exception if doesn't contain good calib data
                cdata = calibration.get_spectrum_efficiency(data)

            spec_strms = [s for s in self.tab_data_model.streams.value
                          if isinstance(s, acqstream.SpectrumStream)]

            for strm in spec_strms:
                strm.efficiencyCompensation.value = cdata

        except Exception, err:  #pylint: disable=W0703
            logging.info("Failed using file %s as spec eff coef", fn, exc_info=True)
            msg = "File '%s' not suitable for spectrum efficiency compensation:\n\n%s"
            dlg = wx.MessageDialog(self.main_frame,
                                   msg % (fn, err),
                                   "Unusable spectrum efficiency file",
                                   wx.OK | wx.ICON_STOP)
            dlg.ShowModal()
            dlg.Destroy()
            raise ValueError("File '%s' not suitable" % fn)

        return fn

    @guiutil.call_in_wx_main
    def _onTool(self, tool):
        """ Called when the tool (mode) is changed """

        # Reset the viewports when the spot tool is not selected
        # Doing it this way, causes some unnecessary calls to the reset method
        # but it cannot be avoided. Subscribing to the tool VA will only
        # tell us what the new tool is and not what the previous, if any, was.
        # if tool != guimod.TOOL_POINT:
        #     self.tab_data_model.visible_views.value = self._def_views
        pass

    def _on_point_select(self, _):
        """ Event handler for when a point is selected """
        # If we're in 1x1 view, we're bringing the plot to the front
        if self.tab_data_model.viewLayout.value == guimod.VIEW_LAYOUT_ONE:
            ang_view = self.panel.vp_angular.microscope_view
            self.tab_data_model.focussedView.value = ang_view

    def _on_pixel_select(self, _):
        """ Event handler for when a spectrum pixel is selected """

        # If we're in 1x1 view, we're bringing the plot to the front
        # if self.tab_data_model.viewLayout.value == guimod.VIEW_LAYOUT_ONE:
        #     plot_view = self.panel.vp_inspection_plot.microscope_view
        #     self.tab_data_model.focussedView.value = plot_view

        if self.tab_data_model.viewLayout.value == guimod.VIEW_LAYOUT_ONE:
            self.tab_data_model.viewLayout.value = guimod.VIEW_LAYOUT_22

    def _on_line_select(self, _):
        """ Event handler for when a spectrum line is selected """

        # If we're in 1x1 view, we're bringing the plot to the front
        # if self.tab_data_model.viewLayout.value == guimod.VIEW_LAYOUT_ONE:
        #     spatial_view = self.panel.vp_spatialspec.microscope_view
        #     self.tab_data_model.focussedView.value = spatial_view

        if self.tab_data_model.viewLayout.value == guimod.VIEW_LAYOUT_ONE:
            self.tab_data_model.viewLayout.value = guimod.VIEW_LAYOUT_22


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

        # TODO: we should actually display the settings of the streams (...once they have it)
        self._settings_controller = settings.LensAlignSettingsController(
            self.panel,
            self.tab_data_model
        )

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
                                            children={"orig": main_data.aligner},
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

        # TODO: put all the settings as local, so that they don't change when
        # going to spot mode
        # No stream controller, because it does far too much (including hiding
        # the only stream entry when SEM view is focused)
        sem_stream = acqstream.SEMStream("SEM", main_data.sed,
                                         main_data.sed.data, main_data.ebeam)
        sem_stream.should_update.value = True
        self.tab_data_model.streams.value.append(sem_stream)
        self._sem_stream = sem_stream
        self._sem_view = panel.vp_align_sem.microscope_view
        self._sem_view.addStream(sem_stream)

        spot_stream = acqstream.SpotSEMStream("Spot", main_data.sed,
                                              main_data.sed.data, main_data.ebeam)
        self.tab_data_model.streams.value.append(spot_stream)
        self._spot_stream = spot_stream

        # Adapt the zoom level of the SEM to fit exactly the SEM field of view.
        # No need to check for resize events, because the view has a fixed size.
        if not main_data.ebeamControlsMag:
            panel.vp_align_sem.canvas.abilities -= {CAN_ZOOM}
            # prevent the first image to reset our computation
            panel.vp_align_sem.canvas.fit_view_to_next_image = False
            main_data.ebeam.pixelSize.subscribe(self._onSEMpxs, init=True)

        # Update the SEM area in dichotomic mode
        self.tab_data_model.dicho_seq.subscribe(self._onDichoSeq, init=True)

        # TODO: when paused via the shortcut or menu, really pause it
        #   => use a stream scheduler?
        # TODO: exposureTime as local setting, so that it's not changed when
        # going to acquisition tab
        # create CCD stream
        ccd_stream = acqstream.CameraStream("Optical CL",
                                            main_data.ccd,
                                            main_data.ccd.data,
                                            main_data.light,
                                            focuser=main_data.focus,
                                            forcemd={model.MD_ROTATION: 0,
                                                     model.MD_SHEAR: 0}
                                            )
        ccd_stream.should_update.value = True
        self.tab_data_model.streams.value.insert(0, ccd_stream) # current stream
        self._ccd_stream = ccd_stream
        self._ccd_view = panel.vp_align_ccd.microscope_view
        self._ccd_view.addStream(ccd_stream)
        # create CCD stream panel entry
        stream_bar = self.panel.pnl_secom_align_streams
        ccd_spe = StreamController(stream_bar, ccd_stream, self.tab_data_model)
        ccd_spe.stream_panel.flatten()  # removes the expander header
        # force this view to never follow the tool mode (just standard view)
        panel.vp_align_ccd.canvas.allowed_modes = {guimod.TOOL_NONE}

        # Bind actuator buttons and keys
        self._actuator_controller = ActuatorController(self.tab_data_model, panel, "lens_align_")
        self._actuator_controller.bind_keyboard(panel)

        # Toolbar
        tb = panel.lens_align_tb
        tb.add_tool(tools.TOOL_DICHO, self.tab_data_model.tool)
        tb.add_tool(tools.TOOL_SPOT, self.tab_data_model.tool)

        # Dichotomy mode: during this mode, the label & button "move to center" are
        # shown. If the sequence is empty, or a move is going, it's disabled.
        self._aligner_move = None  # the future of the move (to know if it's over)
        panel.lens_align_btn_to_center.Bind(wx.EVT_BUTTON,
                                                 self._on_btn_to_center)

        # If SEM pxs changes, A/B or X/Y are actually different values
        main_data.ebeam.pixelSize.subscribe(self._update_to_center)

        # Fine alignment panel
        pnl_sem_toolbar = panel.pnl_sem_toolbar
        fa_sizer = pnl_sem_toolbar.GetSizer()
        scale_win = ScaleWindow(pnl_sem_toolbar)
        self._on_mpp = guiutil.call_in_wx_main_wrapper(scale_win.SetMPP)  # need to keep ref
        self._sem_view.mpp.subscribe(self._on_mpp, init=True)
        fa_sizer.Add(scale_win, proportion=3, flag=wx.ALIGN_RIGHT | wx.TOP | wx.LEFT, border=10)
        fa_sizer.Layout()

        self._fa_controller = acqcont.FineAlignController(self.tab_data_model,
                                                          panel,
                                                          main_frame,
                                                          self._settings_controller)

        self._ac_controller = acqcont.AutoCenterController(self.tab_data_model,
                                                           self._aligner_xy,
                                                           panel,
                                                           self._settings_controller)

        # Documentation text on the left panel
        doc_path = pkg_resources.resource_filename("odemis.gui", "doc/alignment.html")
        panel.html_alignment_doc.SetBorders(0)  # sizer already give us borders
        panel.html_alignment_doc.LoadPage(doc_path)

        # Trick to allow easy html editing: double click to reload
        # def reload_page(evt):
        #     evt.GetEventObject().LoadPage(path)

        # panel.html_alignment_doc.Bind(wx.EVT_LEFT_DCLICK, reload_page)

        self.tab_data_model.tool.subscribe(self._onTool, init=True)
        main_data.chamberState.subscribe(self.on_chamber_state, init=True)

    def Show(self, show=True):
        Tab.Show(self, show=show)

        # Turn on/off the streams as the tab is displayed.
        # Also directly modify is_active, as there is no stream scheduler
        for s in self.tab_data_model.streams.value:
            if show:
                s.is_active.value = s.should_update.value
            else:
                s.is_active.value = False

        # update the fine alignment dwell time when CCD settings change
        main_data = self.tab_data_model.main
        if show:
            # as we expect no acquisition active when changing tab, it will always
            # lead to subscriptions to VA
            main_data.is_acquiring.subscribe(self._on_acquisition, init=True)
        else:
            main_data.is_acquiring.unsubscribe(self._on_acquisition)

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
        if tool != guimod.TOOL_DICHO:
            # reset the sequence
            self.tab_data_model.dicho_seq.value = []
            self.panel.pnl_move_to_center.Show(False)
            self.panel.pnl_align_tools.Show(True)

        if tool != guimod.TOOL_SPOT:
            self._spot_stream.should_update.value = False
            self._spot_stream.is_active.value = False
            self._sem_stream.should_update.value = True
            self._sem_stream.is_active.value = True and shown

        # Set new mode
        if tool == guimod.TOOL_DICHO:
            self.panel.pnl_move_to_center.Show(True)
            self.panel.pnl_align_tools.Show(False)
        elif tool == guimod.TOOL_SPOT:
            self._sem_stream.should_update.value = False
            self._sem_stream.is_active.value = False
            self._spot_stream.should_update.value = True
            self._spot_stream.is_active.value = True and shown
            # TODO: until the settings are directly connected to the hardware,
            # or part of the stream, we need to disable/freeze the SEM settings
            # in spot mode.

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

    def _on_acquisition(self, is_acquiring):
        # A bit tricky because (in theory), could happen in any tab
        self._subscribe_for_fa_dt(not is_acquiring)

    def _subscribe_for_fa_dt(self, subscribe=True):
        # Make sure that we don't update fineAlignDwellTime unless:
        # * The tab is shown
        # * Acquisition is not going on
        # * Spot tool is selected
        # (wouldn't be needed if the VAs where on the stream itself)

        ccd = self.tab_data_model.main.ccd
        if subscribe:
            ccd.exposureTime.subscribe(self._update_fa_dt)
            ccd.binning.subscribe(self._update_fa_dt)
            self.tab_data_model.tool.subscribe(self._update_fa_dt)
        else:
            ccd.exposureTime.unsubscribe(self._update_fa_dt)
            ccd.binning.unsubscribe(self._update_fa_dt)
            self.tab_data_model.tool.unsubscribe(self._update_fa_dt)

    def _update_fa_dt(self, unused=None):
        """
        Called when the fine alignment dwell time must be recomputed (because
        the CCD exposure time or binning has changed. It will only be updated
        if the SPOT mode is active (otherwise the user might be setting for
        different purpose.
        """
        if self.tab_data_model.tool.value != guimod.TOOL_SPOT:
            return

        # dwell time is the based on the exposure time for the spot, as this is
        # the best clue on what works with the sample.
        main_data = self.tab_data_model.main
        binning = main_data.ccd.binning.value
        dt = main_data.ccd.exposureTime.value * numpy.prod(binning)
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


class SparcAlignTab(Tab):
    """
    Tab for the mirror/fiber alignment on the SPARC
    """
    # TODO: If this tab is not initially hidden in the XRC file, gtk error
    # will show up when the GUI is launched. Even further (odemis) errors may
    # occur. The reason for this is still unknown.

    def __init__(self, name, button, panel, main_frame, main_data):
        tab_data = guimod.SparcAlignGUIData(main_data)
        super(SparcAlignTab, self).__init__(name, button, panel, main_frame, tab_data)

        self._ccd_stream = None
        # TODO: add on/off button for the CCD and connect the MicroscopeStateController

        self._settings_controller = settings.SparcAlignSettingsController(
            panel,
            tab_data,
        )

        self._stream_controller = streamcont.StreamBarController(
            tab_data,
            panel.pnl_sparc_align_streams,
            locked=True
        )

        # create the stream to the AR image + goal image
        if main_data.ccd:
            ccd_stream = acqstream.CameraStream(
                "Angle-resolved sensor",
                main_data.ccd,
                main_data.ccd.data,
                main_data.ebeam)
            self._ccd_stream = ccd_stream

            # create a view on the microscope model
            vpv = collections.OrderedDict([
                (panel.vp_sparc_align,
                    {
                        "name": "Optical",
                        "stream_classes": None,  # everything is good
                        # no stage, or would need a fake stage to control X/Y of the
                        # mirror
                        # no focus, or could control yaw/pitch?
                    }
                ),
            ])
            self.view_controller = viewcont.ViewPortController(
                tab_data,
                panel,
                vpv
            )
            mic_view = self.tab_data_model.focussedView.value
            mic_view.show_crosshair.value = False
            mic_view.merge_ratio.value = 1

            ccd_spe = self._stream_controller.addStream(ccd_stream)
            ccd_spe.stream_panel.flatten()
            ccd_stream.should_update.value = True

            # Connect polePosition of lens to mirror overlay (via the polePositionPhysical VA)
            mirror_ol = self.panel.vp_sparc_align.canvas.mirror_ol
            lens = main_data.lens
            try:
                # The lens is not set, but the CCD metadata is still set as-is.
                # So need to compensate for the magnification, and flip.
                # (That's also the reason it's not possible to move the
                # pole position, as it should be done with the lens)
                m = lens.magnification.value
                mirror_ol.set_mirror_dimensions(-lens.parabolaF.value / m,
                                                lens.xMax.value / m,
                                                lens.focusDistance.value / m,
                                                lens.holeDiameter.value / m)
            except (AttributeError, TypeError) as ex:
                logging.warning("Failed to get mirror dimensions: %s", ex)
        else:
            self.view_controller = None
            logging.warning("No CCD available for mirror alignment feedback")

        # One of the goal of changing the raw/pitch is to optimise the light
        # reaching the optical fiber to the spectrometer
        if main_data.spectrometer:
            # Only add the average count stream
            self._scount_stream = acqstream.CameraCountStream("Spectrum count",
                                                              main_data.spectrometer,
                                                              main_data.spectrometer.data,
                                                              main_data.ebeam)
            self._scount_stream.should_update.value = True
            self._scount_stream.windowPeriod.value = 30  # s
            self._spec_graph = self._settings_controller.spec_graph
            self._txt_mean = self._settings_controller.txt_mean
            self._scount_stream.image.subscribe(self._on_spec_count, init=True)
        else:
            self._scount_stream = None

        # Force a spot at the center of the FoV
        # Not via stream controller, so we can avoid the scheduler
        spot_stream = acqstream.SpotSEMStream("SpotSEM", main_data.sed,
                                              main_data.sed.data, main_data.ebeam)
        self._spot_stream = spot_stream

        # Switch between alignment modes
        # * chamber-view: see the mirror and the sample in the chamber
        # * mirror-align: move x, y, yaw, and pitch with AR feedback
        # * fiber-align: move yaw, pitch and x/y of fiber with scount feedback
        self._alignbtn_to_mode = {panel.btn_align_chamber: "chamber-view",
                                  panel.btn_align_mirror: "mirror-align",
                                  panel.btn_align_fiber: "fiber-align"}

        # Remove the modes which are not supported by the current hardware
        for btn, mode in self._alignbtn_to_mode.items():
            if mode in tab_data.align_mode.choices:
                btn.Bind(wx.EVT_BUTTON, self._onClickAlignButton)
            else:
                btn.Destroy()
                del self._alignbtn_to_mode[btn]

        if len(tab_data.align_mode.choices) <= 1:
            # only one mode possible => hide the buttons
            panel.pnl_alignment_btns.Show(False)

        tab_data.align_mode.subscribe(self._onAlignMode)

        self._actuator_controller = ActuatorController(tab_data, panel, "mirror_align_")

        # Bind keys
        self._actuator_controller.bind_keyboard(panel)

    def _onClickAlignButton(self, evt):
        """
        Called when one of the Mirror/Optical fiber button is pushed
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
        # untoggling the other button will be done when the VA is updated
        self.tab_data_model.align_mode.value = mode

    @call_in_wx_main
    def _onAlignMode(self, mode):
        """
        Called when the align_mode changes (because the user selected a new one)
        mode (str): the new alignment mode
        """
        # Ensure the toggle buttons are correctly set
        for btn, m in self._alignbtn_to_mode.items():
            btn.SetToggle(mode == m)

        # Disable controls/streams which are useless (to guide the user)
        if mode == "chamber-view":
            # With the lens, the image must be flipped to keep the mirror at the
            # top and the sample at the bottom.
            self.panel.vp_sparc_align.SetFlip(wx.VERTICAL)
            # Hide goal image
            self.panel.vp_sparc_align.hide_mirror_overlay()
            self._ccd_stream.should_update.value = True
            self.panel.pnl_sparc_trans.Enable(True)
            self.panel.pnl_fibaligner.Enable(False)
        elif mode == "mirror-align":
            # Show image normally
            self.panel.vp_sparc_align.SetFlip(None)
            # Show the goal image. Don't allow to move it, so that it's always
            # at the same position, and can be used to align with a fixed pole position.
            self.panel.vp_sparc_align.show_mirror_overlay(activate=False)
            self._ccd_stream.should_update.value = True
            self.panel.pnl_sparc_trans.Enable(True)
            self.panel.pnl_fibaligner.Enable(False)
        else:
            if self._ccd_stream:
                self._ccd_stream.should_update.value = False
            self.panel.pnl_sparc_trans.Enable(False)
            self.panel.pnl_fibaligner.Enable(True)

        # This is blocking on the hardware => run in a separate thread
        # TODO: Probably better is that setPath returns a future (and cancel it
        # when hiding the panel)
        self.tab_data_model.main.opm.setPath(mode)

    @call_in_wx_main
    def _on_spec_count(self, scount):
        """
        Called when a new spectrometer data comes in (and so the whole intensity
        window data is updated)
        scount (DataArray)
        """
        if len(scount) > 0:
            # Indicate the raw value
            v = scount[-1]
            if v < 1:
                txt = units.readable_str(float(scount[-1]), sig=6)
            else:
                txt = "%d" % round(v)  # to make it clear what is small/big
            self._txt_mean.SetValue(txt)

            # fit min/max between 0 and 1
            ndcount = scount.view(numpy.ndarray)  # standard NDArray to get scalars
            vmin, vmax = ndcount.min(), ndcount.max()
            b = vmax - vmin
            if b == 0:
                b = 1
            disp = (scount - vmin) / b

            # insert 0s at the beginning if the window is not (yet) full
            dates = scount.metadata[model.MD_ACQ_DATE]
            dur = dates[-1] - dates[0]
            if dur == 0:  # only one tick?
                dur = 1  # => make it 1s large
            exp_dur = self._scount_stream.windowPeriod.value
            missing_dur = exp_dur - dur
            nb0s = int(missing_dur * len(scount) / dur)
            if nb0s > 0:
                disp = numpy.concatenate([numpy.zeros(nb0s), disp])
        else:
            disp = []
        self._spec_graph.SetContent(disp)

    def _getGoalImage(self, main_data):
        """
        main_data (model.MainGUIData)
        returns (model.DataArray): RGBA DataArray of the goal image for the
          current hardware
        """
        ccd = main_data.ccd
        lens = main_data.lens

        # TODO: automatically generate the image? Shouldn't be too hard with
        # cairo, it's just 3 circles and a line.

        # The goal image depends on the physical size of the CCD, so we have
        # a file for each supported sensor size.
        pxs = ccd.pixelSize.value
        ccd_res = ccd.shape[0:2]
        ccd_sz = tuple(int(round(p * l * 1e6)) for p, l in zip(pxs, ccd_res))
        try:
            goal_rs = pkg_resources.resource_stream("odemis.gui.img",
                                                    "calibration/ma_goal_5_13_sensor_%d_%d.png" % ccd_sz)
        except IOError:
            logging.warning(u"Failed to find a fitting goal image for sensor "
                            u"of %dx%d µm" % ccd_sz)
            # pick a known file, it's better than nothing
            goal_rs = pkg_resources.resource_stream("odemis.gui.img",
                                                    "calibration/ma_goal_5_13_sensor_13312_13312.png")
        goal_im = model.DataArray(scipy.misc.imread(goal_rs))
        # No need to swap bytes for goal_im. Alpha needs to be fixed though
        goal_im = scale_to_alpha(goal_im)
        # It should be displayed at the same scale as the actual image.
        # In theory, it would be direct, but as the backend doesn't know when
        # the lens is on or not, it's considered always on, and so the optical
        # image get the pixel size multiplied by the magnification.

        # The resolution is the same as the maximum sensor resolution, if not,
        # we adapt the pixel size
        im_res = (goal_im.shape[1], goal_im.shape[0])  #pylint: disable=E1101,E1103
        scale = ccd_res[0] / im_res[0]
        if scale != 1:
            logging.warning("Goal image has resolution %s while CCD has %s",
                            im_res, ccd_res)

        # Pxs = sensor pxs / lens mag
        mag = lens.magnification.value
        goal_md = {model.MD_PIXEL_SIZE: (scale * pxs[0] / mag, scale * pxs[1] / mag),  # m
                   model.MD_POS: (0, 0),
                   model.MD_DIMS: "YXC", }

        goal_im.metadata = goal_md
        return goal_im

    def Show(self, show=True):
        Tab.Show(self, show=show)

        # Turn on the camera and SEM only when displaying this tab
        if self._ccd_stream:
            self._ccd_stream.is_active.value = show
        if self._spot_stream:
            self._spot_stream.is_active.value = show

        if self._scount_stream:
            active = self._scount_stream.should_update.value and show
            self._scount_stream.is_active.value = active

        # If there is an actuator, disable the lens
        if show:
            self._onAlignMode(self.tab_data_model.align_mode.value)
        # when hidden, the new tab shown is in charge to request the right
        # optical path mode, if needed.

    def terminate(self):
        for s in (self._ccd_stream, self._scount_stream, self._spot_stream):
            if s:
                s.is_active.value = False


# Moment of inertia ROI width
MOI_ROI_WIDTH = 0.5  # => take half of the full frame (in each dimension)


class Sparc2AlignTab(Tab):
    """
    Tab for the mirror/fiber alignment on the SPARCv2. Note that the basic idea
    is similar to the SPARC(v1), but the actual procedure is entirely different.
    """

    def __init__(self, name, button, panel, main_frame, main_data):
        tab_data = guimod.Sparc2AlignGUIData(main_data)
        super(Sparc2AlignTab, self).__init__(name, button, panel, main_frame, tab_data)

        # Reference and move the lens to its default position
        if not main_data.lens_mover.referenced.value["x"]:
            # TODO: have the actuator automatically reference on init?
            f = main_data.lens_mover.reference({"x"})
            f.add_done_callback(self._moveLensToActive)
        else:
            self._moveLensToActive()

        # Documentation text on the right panel for mirror alignement
        doc_path = pkg_resources.resource_filename("odemis.gui", "doc/sparc2_moi_goals.html")
        panel.html_moi_doc.SetBorders(0)
        panel.html_moi_doc.LoadPage(doc_path)

        # Create stream & view
        self._stream_controller = streamcont.StreamBarController(
            tab_data,
            panel.pnl_streams,
            locked=True
        )

        # Create the views. Note that lens/center views use BrightfieldStream,
        # instead of the more generic CameraStream, to avoid picking up
        # CameraCountStream.
        vpv = collections.OrderedDict((
            (self.panel.vp_align_lens,
                {
                    "cls": guimod.ContentView,
                    "name": "Lens alignment",
                    "stream_classes": acqstream.BrightfieldStream,
                }
            ),
            (self.panel.vp_moi,
                {
                    "cls": guimod.ContentView,
                    "name": "Moment of Inertia",
                    "stream_classes": acqstream.MomentOfInertiaLiveStream,
                    # To allow to move the mirror by dragging / double click:
                    "stage": main_data.mirror_xy,
                }
            ),
            (self.panel.vp_align_center,
                {
                    "cls": guimod.ContentView,
                    "name": "Center alignment",
                    "stream_classes": (acqstream.BrightfieldStream, acqstream.RGBStream),
                }
            ),
            (self.panel.vp_align_fiber,
                {
                    "name": "Spectrum average",
                    "stream_classes": acqstream.CameraCountStream,
                }
            ),
        ))
        # Add connection to SEM hFoV if possible
        if main_data.ebeamControlsMag:
            vpv[self.panel.vp_moi]["fov_va"] = main_data.ebeam.horizontalFoV
            self.panel.vp_moi.canvas.fit_view_to_next_image = False

        self.view_controller = viewcont.ViewPortController(tab_data, panel, vpv)
        self.panel.vp_align_lens.microscope_view.show_crosshair.value = False
        self.panel.vp_align_center.microscope_view.show_crosshair.value = False

        # The streams:
        # * MomentOfInertia stream (mois): used to align the mirror. Used both
        #   for the spatial image and the spot size measurement, and for the
        #   chronograph of the MoI at center.
        # * spectrograph line (specline): used for focusing/showing a line in
        #   lens alignment.
        # * AR CCD (ccd): Used to show CL spot during the alignment of the lens1,
        #   _and_ to show the mirror shadow in center alignment
        # * Spectrum count (speccnt): Used to show how much light is received
        #   by the spectrometer over time (30s).
        # * ebeam spot (spot): Used to force the ebeam to spot mode in lens
        #   and center alignment.

        # TODO: have a special stream that does CCD + ebeam spot? (to avoid the ebeam spot)

        # Focuser here too so that it's possible to focus with right mouse button,
        # and also menu controller beleives it's possible to autofocus.
        ccd_stream = acqstream.BrightfieldStream(
                            "Angle-resolved sensor",
                            main_data.ccd,
                            main_data.ccd.data,
                            emitter=None,
                            focuser=main_data.focus,
                            detvas=get_local_vas(main_data.ccd),
                            forcemd={model.MD_POS: (0, 0)}  # Just in case the stage is there
                            )
        # Make sure the binning is not crazy (especially can happen if CCD is shared for spectrometry)
        ccd_stream.detBinning.value = ccd_stream.detBinning.clip((2, 2))
        self._ccd_stream = ccd_stream

        ccd_spe = self._stream_controller.addStream(ccd_stream,
                            add_to_view=self.panel.vp_align_lens.microscope_view)
        ccd_spe.stream_panel.flatten()

        speclines = acqstream.BrightfieldStream(
                            "Spectrograph line",
                            main_data.ccd,
                            main_data.ccd.data,
                            main_data.brightlight,
                            focuser=main_data.focus,
                            detvas=get_local_vas(main_data.ccd),
                            forcemd={model.MD_POS: (0, 0)}
                            )
        speclines.tint.value = (0, 64, 255)  # colour it blue
        # Fixed values, known to work well for autofocus
        speclines.detExposureTime.value = speclines.detExposureTime.clip(0.2)
        speclines.detBinning.value = speclines.detBinning.clip((2, 2))
        b = speclines.detBinning.value
        max_res = speclines.detResolution.range[1]
        res = max_res[0] // b[0], max_res[1] // b[1]
        speclines.detResolution.value = speclines.detResolution.clip(res)
        try:
            speclines.detReadoutRate.value = speclines.detReadoutRate.range[1]
        except AttributeError:
            speclines.detReadoutRate.value = max(speclines.detReadoutRate.choices)
        self._specline_stream = speclines
        # TODO: make the legend display a merge slider (currently not happening
        # because both streams are optical)
        # Add it as second stream, so that it's displayed with the default 0.3 merge ratio
        self._stream_controller.addStream(speclines, visible=False,
                            add_to_view=self.panel.vp_align_lens.microscope_view)

        # MomentOfInertiaStream needs an SEM stream and a CCD stream
        self.panel.vp_moi.canvas.abilities -= {CAN_ZOOM}
        moisem = acqstream.SEMStream("SEM for MoI", main_data.sed, main_data.sed.data, main_data.ebeam)
        mois = acqstream.MomentOfInertiaLiveStream("MoI",
                           main_data.ccd, main_data.ccd.data, main_data.ebeam,
                           moisem,
                           hwemtvas={'magnification'},  # Hardware VAs will not be duplicated as ent/det VAs
                           # we don't want resolution to mess up with detROI
                           detvas=get_local_vas(main_data.ccd, hidden={"resolution"}))
        # Pick some typically good settings
        mois.repetition.value = (9, 9)
        mois.detExposureTime.value = mois.detExposureTime.clip(0.01)
        mois.detBinning.value = mois.detBinning.clip((8, 8))
        try:
            mois.detReadoutRate.value = mois.detReadoutRate.range[1]
        except AttributeError:
            mois.detReadoutRate.value = max(mois.detReadoutRate.choices)
        self._moi_stream = mois
        # Update ROI based on the lens pole position
        main_data.lens.polePosition.subscribe(self._onPolePosition, init=True)
        mois_spe = self._stream_controller.addStream(mois,
                         add_to_view=self.panel.vp_moi.microscope_view)
        mois_spe.stream_panel.flatten()  # No need for the stream name
        # TODO: add ways to show:
        # * Chronograph of the MoI at the center
        self._addMoIEntries(mois_spe.stream_panel)
        mois.image.subscribe(self._onNewMoI)

        # The center align view share the same CCD stream (and settings)
        self.panel.vp_align_center.microscope_view.addStream(ccd_stream)

        # Connect polePosition of lens to mirror overlay (via the polePositionPhysical VA)
        mirror_ol = self.panel.vp_align_center.canvas.mirror_ol
        lens = main_data.lens
        try:
            mirror_ol.set_mirror_dimensions(lens.parabolaF.value,
                                            lens.xMax.value,
                                            lens.focusDistance.value,
                                            lens.holeDiameter.value)
        except (AttributeError, TypeError) as ex:
            logging.warning("Failed to get mirror dimensions: %s", ex)
        mirror_ol.set_hole_position(tab_data.polePositionPhysical)
        self.panel.vp_align_center.show_mirror_overlay()


        # Force a spot at the center of the FoV
        # Not via stream controller, so we can avoid the scheduler
        spot_stream = acqstream.SpotSEMStream("SpotSEM", main_data.sed,
                                              main_data.sed.data, main_data.ebeam)
        spot_stream.should_update.value = True
        self._spot_stream = spot_stream
        # Make sure it only plays when the CCD (or spec count) stream plays
        ccd_stream.should_update.subscribe(self._on_ccd_stream_play)

        # chronograph of spectrometer if "fiber-align" mode is present
        if "fiber-align" in tab_data.align_mode.choices:
            speccnts = acqstream.CameraCountStream("Spectrum average",
                                   main_data.spectrometer,
                                   main_data.spectrometer.data,
                                   emitter=None,
                                   detvas=get_local_vas(main_data.spectrometer),
                                   )
            speccnt_spe = self._stream_controller.addStream(speccnts,
                                add_to_view=self.panel.vp_align_fiber.microscope_view)
            speccnt_spe.stream_panel.flatten()
            self._speccnt_stream = speccnts
            speccnts.should_update.subscribe(self._on_ccd_stream_play)
        else:
            self._speccnt_stream = None

        # Switch between alignment modes
        # * mirror-align: move x, y of mirror with moment of inertia feedback
        # * lens-align: first auto-focus spectrograph, then align lens1
        # * goal-align: find the center of the AR image using a "Goal" image
        # * fiber-align: move x, y of the fibaligner with mean of spectrometer as feedback
        self._alignbtn_to_mode = collections.OrderedDict((
            (panel.btn_align_lens, "lens-align"),
            (panel.btn_align_mirror, "mirror-align"),
            (panel.btn_align_centering, "center-align"),
            (panel.btn_align_fiber, "fiber-align"),
        ))

        # The GUI mode to the optical path mode
        self._mode_to_opm = {
            "mirror-align": "mirror-align",
            "lens-align": "mirror-align",  # if autofocus is needed: spec-focus (first)
            "center-align": "ar",
            "fiber-align": "fiber-align",
        }
        # Note: ActuatorController hides the fiber alignment panel if not needed.
        for btn, mode in self._alignbtn_to_mode.items():
            if mode in tab_data.align_mode.choices:
                btn.Bind(wx.EVT_BUTTON, self._onClickAlignButton)
            else:
                btn.Destroy()
                del self._alignbtn_to_mode[btn]

        self._layoutModeButtons()
        tab_data.align_mode.subscribe(self._onAlignMode)

        # Bind moving buttons & keys
        self._actuator_controller = ActuatorController(tab_data, panel, "")
        self._actuator_controller.bind_keyboard(panel)

        # TODO: warn user if the mirror stage is too far from the official engaged
        # position. => The S axis will fail!

        if main_data.focus:
            # Focus position axis -> AxisConnector
            z = main_data.focus.axes["z"]
            self.panel.slider_focus.SetRange(z.range[0], z.range[1])
            self._ac_focus = AxisConnector("z", main_data.focus, self.panel.slider_focus,
                                           events=wx.EVT_SCROLL_CHANGED)

            # Bind autofocus (the complex part is to get the menu entry working too)
            self.panel.btn_autofocus.Bind(wx.EVT_BUTTON, self._onClickFocus)
            self._autofocus_f = model.InstantaneousFuture()
            self._pfc_autofocus = None
            self._autofocused = False  # to force autofocus on the first trial
            tab_data.autofocus_active.subscribe(self._onAutofocus)

            # Make sure the calibration light is off
            emissions = [0.] * len(main_data.brightlight.emissions.value)
            main_data.brightlight.emissions.value = emissions
            main_data.brightlight.power.value = main_data.brightlight.power.range[0]

        # Bind MoI background acquisition
        self.panel.btn_bkg_acquire.Bind(wx.EVT_BUTTON, self._onBkgAcquire)

        # Force MoI view fit to content when magnification is updated
        if not main_data.ebeamControlsMag:
            main_data.ebeam.magnification.subscribe(self._onSEMMag)

    def _layoutModeButtons(self):
        """
        Positions the mode buttons in a nice way: on one line if they fit,
         otherwise on two lines.
        """
        # If 3 buttons or less, keep them all on a single line, otherwise,
        # spread on two lines. (Works up to 6 buttons)
        btns = self._alignbtn_to_mode.keys()
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

    def _onPolePosition(self, pole_pos):
        """
        Called when the polePosition VA is updated
        """
        ccd = self.tab_data_model.main.ccd
        cntr_roi = (pole_pos[0] / ccd.shape[0],
                    pole_pos[1] / ccd.shape[1])
        # Clip center so we have enough space before the bounds
        h_width = MOI_ROI_WIDTH / 2
        cntr_roi = numpy.clip(cntr_roi, (h_width, h_width), (1 - h_width, 1 - h_width))
        self._moi_stream.detROI.value = (cntr_roi[0] - h_width, cntr_roi[1] - h_width,
                                         cntr_roi[0] + h_width, cntr_roi[1] + h_width)

    def _addMoIEntries(self, cont):
        """
        Add the chronogram, MoI value entry and spot size entry
        :param stream_cont: (Container aka StreamPanel)

        """
        # Add a intensity/time graph
        # self.spec_graph = hist.Histogram(setting_cont.panel, size=(-1, 40))
        # self.spec_graph.SetBackgroundColour("#000000")
        # setting_cont.add_widgets(self.spec_graph)

        # the "MoI" value bellow the chronogram
        lbl_moi, txt_moi = cont.add_text_field("Moment of inertia", readonly=True)
        tooltip_txt = "Moment of inertia at the center (smaller is better), and range"
        lbl_moi.SetToolTipString(tooltip_txt)
        txt_moi.SetToolTipString(tooltip_txt)
        # Change font size and colour
        f = txt_moi.GetFont()
        f.PointSize = 12
        txt_moi.SetFont(f)
        txt_moi.SetForegroundColour(odemis.gui.FG_COLOUR_MAIN)
        self._txt_moi = txt_moi

        lbl_ss, txt_ss = cont.add_text_field("Spot intensity", readonly=True)
        tooltip_txt = "Spot intensity at the center (bigger is better)"
        lbl_ss.SetToolTipString(tooltip_txt)
        txt_ss.SetToolTipString(tooltip_txt)
        # Change font size and colour
        f = txt_ss.GetFont()
        f.PointSize = 12
        txt_ss.SetFont(f)
        txt_ss.SetForegroundColour(odemis.gui.FG_COLOUR_MAIN)
        self._txt_ss = txt_ss

    def _moveLensToActive(self, f=None):
        """
        Move the first lens (lens-mover) to its default active position
        f (future): future of the referencing
        """
        if f:
            f.result()  # to fail & log if the referencing failed

        lm = self.tab_data_model.main.lens_mover
        try:
            lpos = lm.getMetadata()[model.MD_FAV_POS_ACTIVE]
        except KeyError:
            logging.exception("Lens-mover actuator has no metadata FAV_POS_ACTIVE")
        lm.moveAbs(lpos)

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

        assert(self.IsShown())

        # Disable controls/streams which are useless (to guide the user)
        self._stream_controller.pauseStreams()

        main = self.tab_data_model.main

        # Things to do at the end of a mode
        if mode != "fiber-align" and main.spec_sel:
            main.spec_sel.position.unsubscribe(self._onFiberPos)

        # This is blocking on the hardware => run in a separate thread
        op_mode = self._mode_to_opm[mode]
        f = main.opm.setPath(op_mode)

        # Focused view must be updated before the stream to play is changed,
        # as the scheduler automatically adds the stream to the current view.
        # The scheduler also automatically pause all the other streams.
        if mode == "lens-align":
            self.tab_data_model.focussedView.value = self.panel.vp_align_lens.microscope_view
            self._ccd_stream.should_update.value = True
            self.panel.pnl_mirror.Enable(True)  # also allow to move the mirror here
            self.panel.pnl_lens_mover.Enable(True)
            self.panel.pnl_focus.Enable(True)
            self.panel.pnl_moi_settings.Show(False)
            self.panel.pnl_fibaligner.Enable(False)
            # TODO: in this mode, if focus change, update the focus image once
            # (by going to spec-focus mode, turning the light, and acquiring an
            # AR image). Problem is that it takes about 10s.
        elif mode == "mirror-align":
            self.tab_data_model.focussedView.value = self.panel.vp_moi.microscope_view
            self._moi_stream.should_update.value = True
            self.panel.pnl_mirror.Enable(True)
            self.panel.pnl_lens_mover.Enable(False)
            self.panel.pnl_focus.Enable(False)
            self.panel.pnl_moi_settings.Show(True)
            self.panel.pnl_fibaligner.Enable(False)
        elif mode == "center-align":
            self.tab_data_model.focussedView.value = self.panel.vp_align_center.microscope_view
            self._ccd_stream.should_update.value = True
            self.panel.pnl_mirror.Enable(False)
            self.panel.pnl_lens_mover.Enable(False)
            self.panel.pnl_focus.Enable(False)
            self.panel.pnl_moi_settings.Show(False)
            self.panel.pnl_fibaligner.Enable(False)
        elif mode == "fiber-align":
            self.tab_data_model.focussedView.value = self.panel.vp_align_fiber.microscope_view
            self._speccnt_stream.should_update.value = True
            self.panel.pnl_mirror.Enable(False)
            self.panel.pnl_lens_mover.Enable(False)
            self.panel.pnl_focus.Enable(False)
            self.panel.pnl_moi_settings.Show(False)
            self.panel.pnl_fibaligner.Enable(True)
            # Disable the buttons until the fiber box is ready
            self.panel.btn_m_fibaligner_x.Enable(False)
            self.panel.btn_p_fibaligner_x.Enable(False)
            self.panel.btn_m_fibaligner_y.Enable(False)
            self.panel.btn_p_fibaligner_y.Enable(False)
            f.add_done_callback(self._on_fibalign_done)
        else:
            raise ValueError("Unknown alignment mode %s!" % mode)
        # To adapt to the pnl_moi_settings showing on/off
        self.panel.html_moi_doc.Parent.Layout()

    def _on_fibalign_done(self, f):
        """
        Called when the optical path mode is fiber-align and ready
        """
        # Make sure the user can move the X axis only once at ACTIVE position
        self.tab_data_model.main.spec_sel.position.subscribe(self._onFiberPos)
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
        # the SEM image in the original SEM software while still being able to
        # move the mirror
        ccdupdate = self._ccd_stream.should_update.value
        if self._speccnt_stream:
            spcupdate = self._speccnt_stream.should_update.value
        else:
            spcupdate = False
        self._spot_stream.is_active.value = any((ccdupdate, spcupdate))

    def _onClickFocus(self, evt):
        """
        Called when the autofocus button is pressed.
        It will start auto focus procedure... or stop it (if it's currently active)
        """
        self.tab_data_model.autofocus_active.value = not self.tab_data_model.autofocus_active.value

    @call_in_wx_main
    def _onAutofocus(self, active):
        if active:
            if self.tab_data_model.align_mode.value != "lens-align":
                logging.info("Autofocus requested outside of lens alignment mode, not doing anything")
                return

            self.panel.btn_autofocus.SetLabel("Cancel")

            # Go to the special focus mode (=> close the slit & turn on the lamp)
            self._stream_controller.pauseStreams()
            s = self._specline_stream
            # Turn on the light
            bl = self.tab_data_model.main.brightlight
            bl.power.value = bl.power.range[1]
            s.should_update.value = True  # the stream will set all the emissions to 1
            self.tab_data_model.main.opm.setPath("spec-focus").result()
            self._autofocus_f = AutoFocus(s.detector, s.emitter, s.focuser)
            self._autofocus_f.add_done_callback(self._on_autofocus_done)

            # Update GUI
            self._pfc_autofocus = ProgressiveFutureConnector(self._autofocus_f, self.panel.gauge_autofocus)
        else:
            # Cancel task, if we reached here via the GUI cancel button
            self._autofocus_f.cancel()
            self.panel.btn_autofocus.SetLabel("Auto focus")

    def _on_autofocus_done(self, future):
        bl = self.tab_data_model.main.brightlight
        bl.power.value = bl.power.range[0]
        # That VA will take care of updating all the GUI part
        self.tab_data_model.autofocus_active.value = False
        # Go back to normal mode
        self._onAlignMode("lens-align")

    def _onBkgAcquire(self, evt):
        """
        Called when the user presses the "Acquire background" button (for MoI stream)
        """
        # Stop e-beam, in case it's connected to a beam blanker
        self._moi_stream.should_update.value = False

        # TODO: if there is no blanker available, put the spec-det-selector to
        # the other port, so that the ccd get almost no signal

        # Disable button to give a feedback that acquisition is taking place
        self.panel.btn_bkg_acquire.Disable()

        # Acquire asynchronously
        # TODO: make sure we don't get the latest CCD image that was being acquired
        self.tab_data_model.main.ccd.data.subscribe(self._on_bkg_data)

    def _on_bkg_data(self, df, data):
        """
        Called with a raw CCD image corresponding to background acquisition.
        """
        # Stop the acquisition, and pass that data to the MoI stream
        df.unsubscribe(self._on_bkg_data)
        self._moi_stream.background.value = data
        self._moi_stream.should_update.value = True

        wx.CallAfter(self.panel.btn_bkg_acquire.Enable)

    @call_in_wx_main
    def _onNewMoI(self, rgbim):
        """
        Called when a new MoI image is available.
        We actually don't use the RGB image, but it's a sign that there is new
        MoI and spot size info to display.
        rgbim (DataArray): RGB image of the MoI
        """
        if rgbim is None:
            return

        # Note: center position is typically 4,4 as the repetition is fixed to 9x9.
        # To be sure, we recompute it every time
        center = (rgbim.shape[1] - 1) // 2, (rgbim.shape[0] - 1) // 2  # px
        moi, moi_mm = self._moi_stream.getRawValue(center)
        ss = self._moi_stream.getSpotIntensity()

        # If value is None => text is ""
        txt_moi = units.readable_str(moi, sig=3)
        if moi_mm is not None:
            mn, mx = moi_mm
            txt_moi += u" (%s → %s)" % (units.readable_str(mn, sig=3), units.readable_str(mx, sig=3))
        self._txt_moi.SetValue(txt_moi)

        self._txt_ss.SetValue(units.readable_str(ss, sig=3))

    def _onSEMMag(self, mag):
        """
        Called when user enters a new SEM magnification
        """
        # Restart the stream and fit view to content when we get a new image
        if self._moi_stream.is_active.value:
            # Restarting is nice because it will get a new image faster, but
            # the main advantage is that it avoids receiving one last image
            # with the old magnification, which would confuse fit_view_to_next_image.
            self._moi_stream.is_active.value = False
            self._moi_stream.is_active.value = True
        self.panel.vp_moi.canvas.fit_view_to_next_image = True

    def _onLensPos(self, pos):
        """
        Called when the lens is moved (and the tab is shown)
        """
        # Save the lens position as the "calibrated" one
        lm = self.tab_data_model.main.lens_mover
        lm.updateMetadata({model.MD_FAV_POS_ACTIVE: pos})

    def _onMirrorPos(self, pos):
        """
        Called when the mirror is moved (and the tab is shown)
        """
        # Save mirror position as the "calibrated" one
        m = self.tab_data_model.main.mirror
        m.updateMetadata({model.MD_FAV_POS_ACTIVE: pos})

    def _onFiberPos(self, pos):
        """
        Called when the spec-selector (wrapper to the X axis of fiber-aligner)
          is moved (and the fiber align mode is active)
        """
        # Save the axis position as the "calibrated" one
        ss = self.tab_data_model.main.spec_sel
        ss.updateMetadata({model.MD_FAV_POS_ACTIVE: pos})

    def Show(self, show=True):
        Tab.Show(self, show=show)

        main = self.tab_data_model.main
        # Select the right optical path mode and the plays right stream
        if show:
            mode = self.tab_data_model.align_mode.value
            self._onAlignMode(mode)
            main.lens_mover.position.subscribe(self._onLensPos)
            main.mirror.position.subscribe(self._onMirrorPos)
        else:
            # when hidden, the new tab shown is in charge to request the right
            # optical path mode, if needed.
            self._stream_controller.pauseStreams()
            # Cancel autofocus (if it happens to run)
            self.tab_data_model.autofocus_active.value = False

            main.lens_mover.position.unsubscribe(self._onLensPos)
            main.mirror.position.unsubscribe(self._onMirrorPos)
            if main.spec_sel:
                main.spec_sel.position.unsubscribe(self._onFiberPos)

    def terminate(self):
        self._stream_controller.pauseStreams()
        self.tab_data_model.autofocus_active.value = False


class TabBarController(object):
    def __init__(self, tab_defs, main_frame, main_data):
        """
        tab_defs (list of 5-tuples (string, string, Tab class, button, panel):
            list of all the possible tabs. Each tuple is:
                - microscope role(s) (string or tuple of strings/None)
                - internal name(s)
                - class
                - tab btn
                - tab panel.
            If role is None, it will match when there is no microscope
            (main_data.microscope is None).

        TODO: support "*" for matching anything?

        """
        self.main_frame = main_frame
        self._tabs = main_data.tab  # VA that we take care of
        self.main_data = main_data

        # create all the tabs that fit the microscope role
        tab_list, default_tab = self._create_needed_tabs(tab_defs, main_frame, main_data)

        if not tab_list:
            msg = "No interface known for microscope %s" % main_data.role
            raise LookupError(msg)

        for tab in tab_list:
            tab.button.Bind(wx.EVT_BUTTON, self.on_click)

        # When setting a value for an Enumerated VA, the value must be part of its choices, and
        # when setting it's choices its current value must be one of them. Therefore, we first set
        # the current tab using the `._value` attribute, so that the check will not occur. We can
        # then set the `choices` normally.
        # Note: One of the created Tab controllers might have concluded that it should be default
        # tab. In that case, the current tab value will already have been set, overriding the tab
        # definition passed to this class.
        if not self._tabs.value:
            self._tabs._value = default_tab or tab_list[0]

        # Choices is a dict tab -> name of the tab
        choices = {t: t.name for t in tab_list}
        self._tabs.choices = choices
        self._tabs.subscribe(self._on_tab_change)
        # force the switch to the first tab
        self._tabs.notify(self._tabs.value)

        # IMPORTANT NOTE:
        #
        # When all tab panels are hidden on start-up, the MinSize attribute
        # of the main GUI frame will be set to such a low value that most of
        # the interface will be invisible if the user takes the interface out of
        # 'full screen' view.
        # Also, Gnome's GDK library will start spewing error messages, saying
        # it cannot draw certain images, because the dimensions are 0x0.
        main_frame.SetMinSize((1400, 550))

        self.main_data.is_acquiring.subscribe(self.on_acquisition)

    def get_tabs(self):
        return self._tabs.choices

    def open_tab(self, tab_name):
        for tab in self._tabs.choices:
            if tab.name == tab_name:
                self._tabs.value = tab
                return tab
        else:
            logging.warn("Could not open tab '%s", tab_name)

        return None

    def on_acquisition(self, is_acquiring):
        for tab in self._tabs.choices:
            tab.button.Enable(not is_acquiring)

    def _create_needed_tabs(self, tab_defs, main_frame, main_data):
        """ Create the tabs needed by the current microscope

        Tabs that are not wanted or needed will be removed from the list and the associated
        buttons will be hidden in the user interface.

        returns (list of Tabs): all the compatible tabs

        """

        role = main_data.role
        logging.debug("Creating tabs belonging to the '%s' interface", role or "no backend")

        tabs = []  # Tabs
        main_sizer = main_frame.GetSizer()
        index = 1
        default_tab = None

        for tab_def in tab_defs:
            if role in tab_def["roles"]:
                tpnl = tab_def["panel"](self.main_frame)
                main_sizer.Insert(index, tpnl, flag=wx.EXPAND, proportion=1)
                index += 1
                tab = tab_def["controller"](tab_def["name"], tab_def["button"], tpnl, main_frame,
                                            main_data)
                tab.set_label(tab_def["roles"][role].get("label", tab_def["label"]))
                if tab_def["roles"][role].get("default", False):
                    default_tab = tab
                tabs.append(tab)
            else:
                tab_def["button"].Hide()  # Hide the tab button

        return tabs, default_tab

    def _on_tab_change(self, tab):
        """ This method is called when the current tab has changed """

        try:
            self.main_frame.Freeze()
            for t in self._tabs.choices:
                if t.IsShown():
                    t.Hide()
        finally:
            self.main_frame.Thaw()
        # It seems there is a bug in wxWidgets which makes the first .Show() not
        # work when the frame is frozen. So always call it after Thaw(). Doesn't
        # seem to cause too much flickering.
        tab.Show()
        self.main_frame.Layout()

    def terminate(self):
        """ Terminate each tab (i.e.,indicate they are not used anymore)CameraViewport """

        for t in self._tabs.choices:
            t.terminate()

    def on_click(self, evt):

        # if .value:
        #     logging.warn("Acquisition in progress, tabs frozen")
        #     evt_btn = evt.GetEventObject()
        #     evt_btn.SetValue(not evt_btn.GetValue())
        #     return

        # ie, mouse click or space pressed
        logging.debug("Tab button click")

        evt_btn = evt.GetEventObject()
        for t in self._tabs.choices:
            if evt_btn == t.button:
                self._tabs.value = t
                break
        else:
            logging.warning("Couldn't find the tab associated to the button %s", evt_btn)

        evt.Skip()

