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

import collections
import logging
import math
import numpy
from odemis import dataio, model
from odemis.acq import calibration
from odemis.acq.align import AutoFocus
from odemis.acq.stream import OpticalStream, SpectrumStream, CLStream, EMStream, \
    ARStream, CLSettingsStream, ARSettingsStream, MonochromatorSettingsStream, RGBCameraStream, BrightfieldStream
from odemis.driver.actuator import ConvertStage
from odemis.gui.comp.canvas import CAN_ZOOM
from odemis.gui.comp.popup import Message
from odemis.gui.comp.scalewindow import ScaleWindow
from odemis.gui.comp.viewport import MicroscopeViewport, AngularResolvedViewport, \
    PlotViewport, SpatialSpectrumViewport
from odemis.gui.conf import get_acqui_conf
from odemis.gui.conf.data import get_hw_settings, get_stream_settings_config
from odemis.gui.cont import settings, tools
from odemis.gui.cont.actuators import ActuatorController
from odemis.gui.cont.microscope import SecomStateController, DelphiStateController
from odemis.gui.cont.streams import StreamController
from odemis.gui.util import call_in_wx_main
from odemis.gui.util.img import scale_to_alpha
from odemis.util import units
import os.path
import pkg_resources
import scipy.misc
import threading
import weakref
import wx
# IMPORTANT: wx.html needs to be imported for the HTMLWindow defined in the XRC
# file to be correctly identified. See: http://trac.wxwidgets.org/ticket/3626
# This is not related to any particular wxPython version and is most likely permanent.
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

        self.name = name
        self.label = label
        self.button = button
        self.panel = panel
        self.main_frame = main_frame
        self.tab_data_model = tab_data
        self.notification = False

    def Show(self, show=True):
        self.button.SetToggle(show)
        if show:
            self._connect_22view_event()
            self._connect_crosshair_event()

            self.clear_notification()

        self.panel.Show(show)

    def _connect_22view_event(self):
        """ If the tab has a 2x2 view, this method will connect it to the 2x2
        view menu item (or ensure it's disabled).
        """
        if len(self.tab_data_model.views.value) >= 4:
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
            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_22view.GetId(),
                        on_switch_22)
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
            wx.EVT_MENU(self.main_frame,
                        self.main_frame.menu_item_cross.GetId(),
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

    def notify(self):
        """ Put the tab in 'notification' mode to indicate a change has occurred """
        if not self.notification:
            self.button.notify(True)
            self.notification = True

    def clear_notification(self):
        """ Clear the notification mode if it's active """
        if self.notification:
            self.button.notify(False)
            self.notification = False


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
        vpv = self._create_views(main_data, main_frame.pnl_secom_grid.viewports)
        self.view_controller = viewcont.ViewPortController(tab_data, main_frame, vpv)

        # Special overview button selection
        self.overview_controller = viewcont.OverviewController(tab_data,
                                            main_frame.vp_overview_sem.canvas)
        ovv = self.main_frame.vp_overview_sem.microscope_view
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
                main_frame.btn_secom_view_all,
                (None, main_frame.lbl_secom_view_all)),
            (
                main_frame.btn_secom_view_tl,
                (main_frame.vp_secom_tl, main_frame.lbl_secom_view_tl)),
            (
                main_frame.btn_secom_view_tr,
                (main_frame.vp_secom_tr, main_frame.lbl_secom_view_tr)),
            (
                main_frame.btn_secom_view_bl,
                (main_frame.vp_secom_bl, main_frame.lbl_secom_view_bl)),
            (
                main_frame.btn_secom_view_br,
                (main_frame.vp_secom_br, main_frame.lbl_secom_view_br)),
            (
                main_frame.btn_secom_overview,
                (main_frame.vp_overview_sem, main_frame.lbl_secom_overview)),
        ])

        self._view_selector = viewcont.ViewButtonController(
            tab_data,
            main_frame,
            buttons,
            main_frame.pnl_secom_grid.viewports
        )

        self._settingbar_controller = settings.SecomSettingsController(
            main_frame,
            tab_data
        )

        self._streambar_controller = streamcont.SecomStreamsController(
            tab_data,
            main_frame.pnl_secom_streams
        )

        # Toolbar
        self.tb = main_frame.secom_toolbar
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

        self._acquisition_controller = acqcont.SecomAcquiController(
            tab_data,
            main_frame
        )

        if main_data.role == "delphi":
            state_controller_cls = DelphiStateController
        else:
            state_controller_cls = SecomStateController

        self._state_controller = state_controller_cls(
            tab_data,
            main_frame,
            "live_btn_",
            self._streambar_controller
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
            main_frame.live_btn_press.Hide()

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
                  "focus": main_data.focus,
                  "stream_classes": OpticalStream,
                  }),
                (viewports[1],
                 {"name": "SEM",
                  # centered on content, even on Delphi when POS_COR used to
                  # align on the optical streams
                  "cls": guimod.ContentView,
                  "stage": main_data.stage,
                  "focus": main_data.ebeam_focus,
                  "stream_classes": EMStream,
                  }),
                (viewports[2],
                 {"name": "Combined 1",
                  "stage": main_data.stage,
                  "focus": main_data.focus,
                  "stream_classes": (EMStream, OpticalStream),
                  }),
                (viewports[3],
                 {"name": "Combined 2",
                  "stage": main_data.stage,
                  "focus": main_data.focus,
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
                                 "focus": main_data.ebeam_focus,
                                 "stream_classes": EMStream,
                                 }

        # If Optical only: all optical
        elif not main_data.ebeam and main_data.light:
            logging.info("Creating Optical only viewport layout")
            vpv = collections.OrderedDict()
            for i, viewport in enumerate(viewports):
                vpv[viewport] = {"name": "Optical %d" % (i + 1),
                                 "stage": main_data.stage,
                                 "focus": main_data.focus,
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
                    "focus": main_data.focus,
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

        return vpv

    def _get_focus_hw(self, s):
        """
        Finds the hardware required to focus a given stream
        s (Stream)
        return:
             (HwComponent) detector
             (HwComponent) emitter
             (HwComponent) focus
        """
        detector = None
        emitter = None
        focus = None
        # Slightly different depending on the stream type, especially as the
        # stream doesn't have information on the focus, we need to "guess"
        if isinstance(s, acqstream.StaticStream):
            pass
        elif isinstance(s, acqstream.SEMStream):
            detector = s.detector
            emitter = s.emitter
            focus = self.main_data.ebeam_focus
        elif isinstance(s, acqstream.CameraStream):
            detector = s.detector
            focus = self.main_data.focus
        # TODO: handle overview stream
        else:
            logging.info("Doesn't know how to focus stream %s", type(s).__name__)

        return detector, emitter, focus

    def _onAutofocus(self, state):
        # Determine which stream is active
        if state == guimod.TOOL_AUTO_FOCUS_ON:
            try:
                curr_s = self.tab_data_model.streams.value[0]
            except IndexError:
                d, e, f = None, None, None
            else:
                # enable only if focuser is available, and no autofocus happening
                d, e, f = self._get_focus_hw(curr_s)

            if all((d, f)):
                self._autofocus_f = AutoFocus(d, e, f)
                self._autofocus_f.add_done_callback(self._on_autofocus_done)
            else:
                # Should never happen as normally the menu/icon are disabled
                logging.info("Autofocus cannot run as no hardware is available")
                self.tab_data_model.autofocus_active.value = guimod.TOOL_AUTO_FOCUS_OFF
        else:
            if self._autofocus_f is not None:
                self._autofocus_f.cancel()

    @call_in_wx_main
    def _on_autofocus_done(self, future):
        self.tab_data_model.autofocus_active.value = guimod.TOOL_AUTO_FOCUS_OFF

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
        Used to update the autofocus menu and button
        """
        try:
            curr_s = self.tab_data_model.streams.value[0]
        except IndexError:
            d, e, f = None, None, None
        else:
            # enable only if focuser is available, and no autofocus happening
            d, e, f = self._get_focus_hw(curr_s)

        f_enable = all((updated, d, f))
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
                self._streambar_controller.addFluo(add_to_all_views=True, play=False)
                # don't forbid to remove it, as for the user it can be easier to
                # remove than change all the values

        if hasattr(self.tab_data_model, 'emState'):
            has_sem = any(isinstance(s, acqstream.EMStream)
                          for s in self.tab_data_model.streams.value)
            if not has_sem:
                stream_cont = self._add_em_stream(add_to_all_views=True, play=False)
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
                sp = self._streambar_controller.addFluo(add_to_all_views=True)
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
                sp = self._add_em_stream(add_to_all_views=True)
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
        tab_data = guimod.ScannedAcquisitionGUIData(main_data)
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

        # This stream is used both for rendering and acquisition
        sem_stream = acqstream.SEMStream(
            "Secondary electrons survey",
            main_data.sed,
            main_data.sed.data,
            main_data.ebeam,
            emtvas=get_hw_settings(main_data.ebeam),
            detvas=get_hw_settings(main_data.sed),
        )
        # TODO: do not put local magnification/hfw VA, but just the global one,
        # so that if SEM is not playing, but CLi (or other stream with e-beam as
        # emitter) is, then it's possible to change it.
        self._sem_live_stream = sem_stream
        sem_stream.should_update.value = True  # TODO: put it in _streams_to_restart instead?
        self.tab_data_model.acquisitionView.addStream(sem_stream)  # it should also be saved

        # This stream is a bit tricky, because it will play (potentially)
        # simultaneously as another one, and it changes the SEM settings at
        # play and pause.
        spot_stream = acqstream.SpotSEMStream("Spot", main_data.sed,
                                              main_data.sed.data, main_data.ebeam)
        self.tab_data_model.spotStream = spot_stream
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
        self.tab_data_model.semStream = semcl_stream

        # drift correction is disabled until a roi is selected
        semcl_stream.dcRegion.value = acqstream.UNDEFINED_ROI
        # Set anchor region dwell time to the same value as the SEM survey
        sem_stream.emtDwellTime.subscribe(self._copyDwellTimeToAnchor, init=True)

        # Add the SEM stream to the view
        self.tab_data_model.streams.value.append(sem_stream)
        # To make sure the spot mode is stopped when the tab loses focus
        self.tab_data_model.streams.value.append(spot_stream)

        viewports = main_frame.pnl_sparc_grid.viewports
        for vp in viewports[:4]:
            assert(isinstance(vp, MicroscopeViewport) or isinstance(vp, PlotViewport))

        # Connect the views
        # TODO: make them different depending on the hardware available?
        #       If so, to what? Does having multiple SEM views help?
        vpv = collections.OrderedDict([
            (viewports[0],
             {"name": "SEM",
              "cls": guimod.ContentView,  # Center on content (instead of stage)
              "stage": main_data.stage,
              "focus": main_data.ebeam_focus,
              "stream_classes": (EMStream, CLSettingsStream),
              }),
            (viewports[1],  # focused view
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

        self.view_controller = viewcont.ViewPortController(tab_data, main_frame, vpv)

        # Connect the view selection buttons
        buttons = collections.OrderedDict([
            (
                main_frame.btn_sparc_view_all,
                (None, main_frame.lbl_sparc_view_all)),
            (
                main_frame.btn_sparc_view_tl,
                (main_frame.vp_sparc_tl, main_frame.lbl_sparc_view_tl)),
            (
                main_frame.btn_sparc_view_tr,
                (main_frame.vp_sparc_tr, main_frame.lbl_sparc_view_tr)),
            (
                main_frame.btn_sparc_view_bl,
                (main_frame.vp_sparc_bl, main_frame.lbl_sparc_view_bl)),
            (
                main_frame.btn_sparc_view_br,
                (main_frame.vp_sparc_br, main_frame.lbl_sparc_view_br)),
        ])

        self._view_selector = viewcont.ViewButtonController(
            tab_data,
            main_frame,
            buttons,
            viewports
        )

        # Toolbar
        self.tb = self.main_frame.sparc_acq_toolbar
        self.tb.add_tool(tools.TOOL_ROA, self.tab_data_model.tool)
        self.tb.add_tool(tools.TOOL_RO_ANCHOR, self.tab_data_model.tool)
        self.tb.add_tool(tools.TOOL_SPOT, self.tab_data_model.tool)
        # TODO: Add the buttons when the functionality is there
        #self.tb.add_tool(tools.TOOL_POINT, self.tab_data_model.tool)
        #self.tb.add_tool(tools.TOOL_RO_ZOOM, self.tab_data_model.tool)
        self.tb.add_tool(tools.TOOL_ZOOM_FIT, self.view_controller.fitViewToContent)

        self.tab_data_model.tool.subscribe(self.on_tool_change)

        # Create Stream Bar Controller
        self._stream_controller = streamcont.SparcStreamsController(
            self.tab_data_model,
            self.main_frame.pnl_sparc_streams,
            self.view_controller,
            ignore_view=True  # Show all stream panels, independent of any selected viewport
        )

        # The sem stream is always visible, so add it by default
        sem_stream_cont = self._stream_controller.addStream(sem_stream, add_to_all_views=True)
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

        main_data.is_acquiring.subscribe(self.on_acquisition)

        self._acquisition_controller = acqcont.SparcAcquiController(
            tab_data,
            main_frame,
            self.stream_controller,
        )

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

    def on_acquisition(self, is_acquiring):
        # TODO: Make sure nothing can be modified during acquisition

        self.tb.enable(not is_acquiring)
        self.main_frame.vp_sparc_tl.Enable(not is_acquiring)
        self.main_frame.btn_sparc_change_file.Enable(not is_acquiring)

    def _copyDwellTimeToAnchor(self, dt):
        """
        Use the sem stream dwell time as the anchor dwell time
        """
        self.tab_data_model.semStream.dcDwellTime.value = dt

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
        viewports = main_frame.pnl_inspection_grid.viewports
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

        self.view_controller = viewcont.ViewPortController(tab_data, main_frame, vpv)

        # Connect view selection button
        buttons = collections.OrderedDict([
            (
                main_frame.btn_inspection_view_all,
                (None, main_frame.lbl_inspection_view_all)
            ),
            (
                main_frame.btn_inspection_view_tl,
                (main_frame.vp_inspection_tl, main_frame.lbl_inspection_view_tl)
            ),
            (
                main_frame.btn_inspection_view_tr,
                (main_frame.vp_inspection_tr, main_frame.lbl_inspection_view_tr)
            ),
            (
                main_frame.btn_inspection_view_bl,
                (main_frame.vp_inspection_bl, main_frame.lbl_inspection_view_bl)
            ),
            (
                main_frame.btn_inspection_view_br,
                (main_frame.vp_inspection_br, main_frame.lbl_inspection_view_br)
            )
        ])

        self._view_selector = viewcont.ViewButtonController(tab_data,
                                                            main_frame,
                                                            buttons,
                                                            viewports)

        # Toolbar
        self.tb = main_frame.ana_toolbar
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
            main_frame.pnl_inspection_streams,
            static=True
        )

        # Show the file info and correction selection
        self._settings_controller = settings.AnalysisSettingsController(
            main_frame,
            tab_data
        )
        self._settings_controller.setter_ar_file = self.set_ar_background
        self._settings_controller.setter_spec_bck_file = self.set_spec_background
        self._settings_controller.setter_spec_file = self.set_spec_comp

        self.main_frame.btn_open_image.Bind(
            wx.EVT_BUTTON,
            self.on_file_open_button
        )

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
        fn = dialog.GetPath()
        logging.debug("Current file set to %s", fn)

        fmt = formats[dialog.GetFilterIndex()]
        if fmt is None:
            # Try to guess from the extension
            for f, exts in formats_to_ext.items():
                if any([fn.endswith(e) for e in exts]):
                    fmt = f
                    break
            else:
                # pick a random format hoping it's the right one
                fmt = formats[1]
                logging.warning("Couldn't guess format from filename '%s', will use %s.", fn, fmt)

        Message.show_message(self.main_frame, "Opening file")
        self.load_data(fmt, fn)
        return True

    def on_file_open_button(self, evt):
        self.select_acq_file()

    def load_data(self, fmt, fn):
        converter = dataio.get_converter(fmt)
        try:
            data = converter.read_data(fn)
        except Exception:
            logging.exception("Failed to open file '%s' with format %s", fn, fmt)

        self.display_new_data(fn, data)

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
        self.main_frame.vp_inspection_plot.clear()
        self.main_frame.vp_spatialspec.clear()
        self.main_frame.vp_angular.clear()

        # Force the canvases to fit to the content
        for vp in [self.main_frame.vp_inspection_tl,
                   self.main_frame.vp_inspection_tr,
                   self.main_frame.vp_inspection_bl,
                   self.main_frame.vp_inspection_br]:
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
        streams = self._stream_controller.data_to_static_streams(data)

        # Spectrum and AR streams are, for now, considered mutually exclusive
        spec_streams = [s for s in streams if isinstance(s, acqstream.SpectrumStream)]
        ar_streams = [s for s in streams if isinstance(s, acqstream.ARStream)]

        # TODO: Move viewport related code to ViewPortController
        if spec_streams:

            # ########### Track pixel and line selection

            for spec_stream in spec_streams:
                iimg = spec_stream.image.value

                # We need to get the dimensions so we can determine the
                # resolution. Remember that in Matrix notation, the
                # number of rows (is vertical size), comes first. So we
                # need to 'swap' the values to get the (x,y) resolution.
                height, width = iimg.shape[0:2]

                # Set the PointOverlay values for each viewport
                for viewport in self.view_controller.viewports:
                    if hasattr(viewport.canvas, "pixel_overlay"):
                        ol = viewport.canvas.pixel_overlay
                        ol.set_data_properties(
                            iimg.metadata[model.MD_PIXEL_SIZE][0],
                            iimg.metadata[model.MD_POS],
                            (width, height)
                        )
                        ol.connect_selection(spec_stream.selected_pixel, spec_stream.selectionWidth)

                    if hasattr(viewport.canvas, "line_overlay"):
                        ol = viewport.canvas.line_overlay
                        ol.set_data_properties(
                            iimg.metadata[model.MD_PIXEL_SIZE][0],
                            iimg.metadata[model.MD_POS],
                            (width, height)
                        )
                        ol.connect_selection(spec_stream.selected_line,
                                             spec_stream.selectionWidth,
                                             spec_stream.selected_pixel)

                spec_stream.selected_pixel.subscribe(self._on_pixel_select, init=True)
                spec_stream.selected_line.subscribe(self._on_line_select, init=True)

            # ########### Combined views and spectrum view visible

            new_visible_views[0:2] = self._def_views[2:4]  # Combined
            new_visible_views[2] = self.main_frame.vp_spatialspec.microscope_view
            new_visible_views[3] = self.main_frame.vp_inspection_plot.microscope_view

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
            new_visible_views[2] = self.main_frame.vp_angular.microscope_view
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
            self._stream_controller.addStream(s, add_to_all_views=True)

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
                converter = dataio.find_fittest_exporter(fn)
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
                converter = dataio.find_fittest_exporter(fn)
                data = converter.read_data(fn)
                # will raise exception if doesn't contain good calib data
                cdata = calibration.get_spectrum_data(data) # FIXME

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
                converter = dataio.find_fittest_exporter(fn)
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
            ang_view = self.main_frame.vp_angular.microscope_view
            self.tab_data_model.focussedView.value = ang_view

    def _on_pixel_select(self, _):
        """ Event handler for when a spectrum pixel is selected """

        # If we're in 1x1 view, we're bringing the plot to the front
        # if self.tab_data_model.viewLayout.value == guimod.VIEW_LAYOUT_ONE:
        #     plot_view = self.main_frame.vp_inspection_plot.microscope_view
        #     self.tab_data_model.focussedView.value = plot_view

        if self.tab_data_model.viewLayout.value == guimod.VIEW_LAYOUT_ONE:
            self.tab_data_model.viewLayout.value = guimod.VIEW_LAYOUT_22

    def _on_line_select(self, _):
        """ Event handler for when a spectrum line is selected """

        # If we're in 1x1 view, we're bringing the plot to the front
        # if self.tab_data_model.viewLayout.value == guimod.VIEW_LAYOUT_ONE:
        #     spatial_view = self.main_frame.vp_spatialspec.microscope_view
        #     self.tab_data_model.focussedView.value = spatial_view

        if self.tab_data_model.viewLayout.value == guimod.VIEW_LAYOUT_ONE:
            self.tab_data_model.viewLayout.value = guimod.VIEW_LAYOUT_22


class LensAlignTab(Tab):
    """ Tab for the lens alignment on the Secom platform
    The streams are automatically active when the tab is shown
    It provides three ways to move the "aligner" (= optical lens position):
     * raw (via the A/B or X/Y buttons)
     * dicho mode (move opposite of the relative position of the ROI center)
     * spot mode (move equal to the relative position of the spot center)
    """

    def __init__(self, name, button, panel, main_frame, main_data):
        tab_data = guimod.ActuatorGUIData(main_data)
        super(LensAlignTab, self).__init__(name, button, panel,
                                           main_frame, tab_data)

        # TODO: we should actually display the settings of the streams (...once they have it)
        self._settings_controller = settings.LensAlignSettingsController(
            self.main_frame,
            self.tab_data_model
        )

        main_frame.vp_align_sem.ShowLegend(False)

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
        else: # SECOMv2 => it's directly X/Y
            if "x" not in main_data.aligner.axes:
                logging.error("Unknown axes in lens aligner stage")
            self._aligner_xy = main_data.aligner
            self._convert_to_aligner = lambda x: x

        # vp_align_sem is connected to the stage
        vpv = collections.OrderedDict([
            (
                main_frame.vp_align_ccd,  # focused view
                {
                    "name": "Optical CL",
                    "cls": guimod.ContentView,
                    "stage": self._aligner_xy,
                    "focus": main_data.focus,
                    "stream_classes": acqstream.CameraStream,
                }
            ),
            (
                main_frame.vp_align_sem,
                {
                    "name": "SEM",
                    "stage": main_data.stage,
                    "stream_classes": acqstream.EMStream,
                },
            )
        ])

        self.view_controller = viewcont.ViewPortController(
            self.tab_data_model,
            self.main_frame,
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
        self._sem_view = main_frame.vp_align_sem.microscope_view
        self._sem_view.addStream(sem_stream)

        spot_stream = acqstream.SpotSEMStream("Spot", main_data.sed,
                                              main_data.sed.data, main_data.ebeam)
        self.tab_data_model.streams.value.append(spot_stream)
        self._spot_stream = spot_stream

        # Adapt the zoom level of the SEM to fit exactly the SEM field of view.
        # No need to check for resize events, because the view has a fixed size.
        main_frame.vp_align_sem.canvas.abilities -= set([CAN_ZOOM])
        # prevent the first image to reset our computation
        main_frame.vp_align_sem.canvas.fit_view_to_next_image = False
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
                                            forcemd={model.MD_ROTATION: 0,
                                                     model.MD_SHEAR: 0}
                                            )
        ccd_stream.should_update.value = True
        self.tab_data_model.streams.value.insert(0, ccd_stream) # current stream
        self._ccd_stream = ccd_stream
        self._ccd_view = main_frame.vp_align_ccd.microscope_view
        self._ccd_view.addStream(ccd_stream)
        # create CCD stream panel entry
        stream_bar = self.main_frame.pnl_secom_align_streams
        ccd_spe = StreamController(stream_bar, ccd_stream, self.tab_data_model)
        ccd_spe.stream_panel.flatten()  # removes the expander header
        # force this view to never follow the tool mode (just standard view)
        main_frame.vp_align_ccd.canvas.allowed_modes = set([guimod.TOOL_NONE])

        # Bind actuator buttons and keys
        self._actuator_controller = ActuatorController(self.tab_data_model,
                                                       main_frame,
                                                       "lens_align_")
        self._actuator_controller.bind_keyboard(main_frame.pnl_tab_secom_align)

        # Toolbar
        tb = main_frame.lens_align_tb
        tb.add_tool(tools.TOOL_DICHO, self.tab_data_model.tool)
        tb.add_tool(tools.TOOL_SPOT, self.tab_data_model.tool)

        # Dichotomy mode: during this mode, the label & button "move to center" are
        # shown. If the sequence is empty, or a move is going, it's disabled.
        self._aligner_move = None  # the future of the move (to know if it's over)
        main_frame.lens_align_btn_to_center.Bind(wx.EVT_BUTTON,
                                                 self._on_btn_to_center)

        # Fine alignment panel
        pnl_sem_toolbar = main_frame.pnl_sem_toolbar
        fa_sizer = pnl_sem_toolbar.GetSizer()
        scale_win = ScaleWindow(pnl_sem_toolbar)
        self._on_mpp = guiutil.call_in_wx_main_wrapper(scale_win.SetMPP)  # need to keep ref
        self._sem_view.mpp.subscribe(self._on_mpp, init=True)
        fa_sizer.Add(scale_win, proportion=3, flag=wx.ALIGN_RIGHT | wx.TOP | wx.LEFT, border=10)
        fa_sizer.Layout()

        self._fa_controller = acqcont.FineAlignController(self.tab_data_model,
                                                          main_frame,
                                                          self._settings_controller)

        self._ac_controller = acqcont.AutoCenterController(self.tab_data_model,
                                                           self._aligner_xy,
                                                           main_frame,
                                                           self._settings_controller)

        # Documentation text on the left panel
        doc_path = pkg_resources.resource_filename("odemis.gui", "doc/alignment.html")
        main_frame.html_alignment_doc.SetBorders(0)  # sizer already give us borders
        main_frame.html_alignment_doc.LoadPage(doc_path)

        # Trick to allow easy html editing: double click to reload
        # def reload_page(evt):
        #     evt.GetEventObject().LoadPage(path)

        # main_frame.html_alignment_doc.Bind(wx.EVT_LEFT_DCLICK, reload_page)

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
        super(LensAlignTab, self).terminate()
        # make sure the streams are stopped
        for s in self.tab_data_model.streams.value:
            s.is_active.value = False

    @call_in_wx_main
    def on_chamber_state(self, state):
        # Lock or enable lens alignment
        if state in {guimod.CHAMBER_VACUUM, guimod.CHAMBER_UNKNOWN}:
            self.button.Enable()
            self.notify()
        else:
            self.button.Disable()
            self.clear_notification()

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
            self.main_frame.pnl_move_to_center.Show(False)
            self.main_frame.pnl_align_tools.Show(True)

        if tool != guimod.TOOL_SPOT:
            self._spot_stream.should_update.value = False
            self._spot_stream.is_active.value = False
            self._sem_stream.should_update.value = True
            self._sem_stream.is_active.value = True and shown

        # Set new mode
        if tool == guimod.TOOL_DICHO:
            self.main_frame.pnl_move_to_center.Show(True)
            self.main_frame.pnl_align_tools.Show(False)
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

        self.main_frame.pnl_move_to_center.Parent.Layout()

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
    def _update_to_center(self):
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

        self.main_frame.lens_align_btn_to_center.Enable(enabled)
        lbl_ctrl = self.main_frame.lens_align_lbl_approc_center
        lbl_ctrl.SetLabel(lbl)
        lbl_ctrl.Wrap(lbl_ctrl.Size[0])
        self.main_frame.Layout()

    def _on_btn_to_center(self, event):
        """
        Called when a click on the "move to center" button happens
        """
        # computes the center position
        seq = self.tab_data_model.dicho_seq.value
        roi = align.dichotomy_to_region(seq)
        move = self._computeROICenterMove(roi)

        # disable the button to avoid another move
        self.main_frame.lens_align_btn_to_center.Disable()

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
        """
        Called when the SEM pixel size changes, which means the FoV changes
        pixel_size (tuple of 2 floats): in meter
        """
        # in dicho search, it means A/B or X/Y are actually different values
        self._update_to_center()

        eshape = self.tab_data_model.main.ebeam.shape
        fov_size = (eshape[0] * pixel_size[0], eshape[1] * pixel_size[1])  # m
        semv_size = self.main_frame.vp_align_sem.Size  # px

        # compute MPP to fit exactly the whole FoV
        mpp = (fov_size[0] / semv_size[0], fov_size[1] / semv_size[1])
        best_mpp = max(mpp)  # to fit everything if not same ratio
        best_mpp = self._sem_view.mpp.clip(best_mpp)
        self._sem_view.mpp.value = best_mpp


class MirrorAlignTab(Tab):
    """
    Tab for the mirror alignment calibration on the Sparc
    """
    # TODO: If this tab is not initially hidden in the XRC file, gtk error
    # will show up when the GUI is launched. Even further (odemis) errors may
    # occur. The reason for this is still unknown.

    def __init__(self, name, button, panel, main_frame, main_data):
        tab_data = guimod.ActuatorGUIData(main_data)
        super(MirrorAlignTab, self).__init__(name, button, panel,
                                             main_frame, tab_data)
        self._ccd_stream = None
        self._goal_stream = None
        # TODO: add on/off button for the CCD and connect the MicroscopeStateController

        self._settings_controller = settings.SparcAlignSettingsController(
            main_frame,
            tab_data,
        )

        self._stream_controller = streamcont.StreamBarController(
            tab_data,
            main_frame.pnl_sparc_align_streams,
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

            # The mirror center (with the lens set) is defined as pole position
            # in the microscope configuration file.
            goal_im = self._getGoalImage(main_data)
            self._goal_stream = acqstream.RGBStream("Goal", goal_im)

            # create a view on the microscope model
            vpv = collections.OrderedDict([
                (
                    main_frame.vp_sparc_align,
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
                main_frame,
                vpv
            )
            mic_view = self.tab_data_model.focussedView.value
            mic_view.show_crosshair.value = False
            mic_view.merge_ratio.value = 1

            ccd_spe = self._stream_controller.addStream(ccd_stream)
            ccd_spe.stream_panel.flatten()
            ccd_stream.should_update.value = True
        else:
            self.view_controller = None
            logging.warning("No CCD available for mirror alignment feedback")

        # One of the goal of changing the raw/pitch is to optimise the light
        # reaching the optical fiber to the spectrometer
        # TODO: add a way to switch the selector mirror. For now, it's always
        # switched to AR, and it's up to the user to manually switch it to
        # spectrometer.
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
        # * fiber-align: move yaw, pitch and x/Y of fiber with scount feedback
        self._alignbtn_to_mode = {main_frame.btn_align_chamber: "chamber-view",
                                  main_frame.btn_align_mirror: "mirror-align",
                                  main_frame.btn_align_fiber: "fiber-align"}

        # TODO: move mode detection in the model, and hide buttons for which
        # no mode exist.
        if main_data.spectrometer is None:
            # Note: if no fiber alignment actuators, but a spectrometer, it's
            # still good to provide the mode, as the user can do it manually.
            main_frame.btn_align_fiber.Show(False)
            del self._alignbtn_to_mode[main_frame.btn_align_fiber]

        if main_data.ccd is None:
            # No AR => only one mode possible => hide the buttons
            main_frame.pnl_alignment_btns.Show(False)
            tab_data.align_mode.value = "fiber-align"
        else:
            for btn in self._alignbtn_to_mode:
                btn.Bind(wx.EVT_BUTTON, self._onClickAlignButton)

        tab_data.align_mode.subscribe(self._onAlignMode, init=True)

        self._actuator_controller = ActuatorController(tab_data,
                                                       main_frame,
                                                       "mirror_align_")

        # Bind keys
        self._actuator_controller.bind_keyboard(main_frame.pnl_tab_sparc_align)

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
            self.main_frame.vp_sparc_align.canvas.flip = wx.VERTICAL
            # Hide goal image
            self._stream_controller.removeStream(self._goal_stream)
            self._ccd_stream.should_update.value = True
            self.main_frame.pnl_sparc_trans.Enable(True)
            self.main_frame.pnl_sparc_fib.Enable(False)
        elif mode == "mirror-align":
            # Show image normally
            self.main_frame.vp_sparc_align.canvas.flip = 0
            # Show the goal image (= add it, if it's not already there)
            streams = self.main_frame.vp_sparc_align.microscope_view.getStreams()
            if self._goal_stream not in streams:
                self._stream_controller.addStream(self._goal_stream, visible=False)
            self._ccd_stream.should_update.value = True
            self.main_frame.pnl_sparc_trans.Enable(True)
            self.main_frame.pnl_sparc_fib.Enable(False)
        else:
            if self._ccd_stream:
                self._ccd_stream.should_update.value = False
            self.main_frame.pnl_sparc_trans.Enable(False)
            self.main_frame.pnl_sparc_fib.Enable(True)

        # This is blocking on the hardware => run in a separate thread
        # TODO: Probably better is that setPath returns a future (and cancel it
        # when hiding the panel)
        threading.Thread(target=self.tab_data_model.main.opm.setPath,
                         args=(mode,)).start()

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
        ccd_sz = tuple(int(p * l * 1e6) for p, l in zip(pxs, ccd_res))
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
            mode = self.tab_data_model.align_mode.value
            threading.Thread(target=self.tab_data_model.main.opm.setPath,
                             args=(mode,)).start()
        # when hidden, the new tab shown is in charge to request the right
        # optical path mode, if needed.

    def terminate(self):
        for s in (self._ccd_stream, self._scount_stream, self._spot_stream):
            if s:
                s.is_active.value = False


class TabBarController(object):
    def __init__(self, tab_rules, main_frame, main_data):
        """
        tab_rules (list of 5-tuples (string, string, Tab class, button, panel):
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
        self._tab = main_data.tab  # VA that we take care of
        self.main_data = main_data

        # create all the tabs that fit the microscope role
        tab_list = self._create_needed_tabs(tab_rules, main_frame, main_data)
        if not tab_list:
            msg = "No interface known for microscope %s" % main_data.role
            raise LookupError(msg)

        for tab in tab_list:
            tab.button.Bind(wx.EVT_BUTTON, self.OnClick)

        # Enumerated VA is picky and wants to have choices/value fitting
        # To bootstrap, we set the new value without check
        self._tab._value = tab_list[0]
        # Choices is a dict tab -> name of the tab
        choices = {t: t.name for t in tab_list}
        self._tab.choices = choices
        self._tab.subscribe(self._on_tab_change)
        # force the switch to the first tab
        self._tab.notify(self._tab.value)

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

    def on_acquisition(self, is_acquiring):
        for tab in self._tab.choices:
            tab.button.Enable(not is_acquiring)

    def _create_needed_tabs(self, tab_defs, main_frame, main_data):
        """ Create the tabs needed by the current microscope

        Tabs that are not wanted or needed will be removed from the list and
        the associated buttons will be hidden in the user interface.
        returns (list of Tabs): all the compatible tabs
        """
        role = main_data.role
        logging.debug("Creating tabs belonging to the '%s' interface",
                      role or "no backend")

        tabs = []  # Tabs
        for troles, tlabels, tname, tclass, tbtn, tpnl in tab_defs:

            if role in troles:
                tab = tclass(tname, tbtn, tpnl, main_frame, main_data)
                tab.set_label(tlabels[troles.index(role)])
                tabs.append(tab)
            else:
                # hide the widgets of the tabs not needed
                logging.debug("Discarding tab %s", tname)

                tbtn.Hide()  # this actually removes the tab
                tpnl.Hide()

        return tabs

    def _on_tab_change(self, tab):
        """ This method is called when the current tab has changed """

        try:
            self.main_frame.Freeze()
            for t in self._tab.choices:
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
        """
        Terminate each tab (i.e.,indicate they are not used anymore)
        """
        for t in self._tab.choices:
            t.terminate()

    def OnClick(self, evt):

        # if .value:
        #     logging.warn("Acquisition in progress, tabs frozen")
        #     evt_btn = evt.GetEventObject()
        #     evt_btn.SetValue(not evt_btn.GetValue())
        #     return

        # ie, mouse click or space pressed
        logging.debug("Tab button click")

        evt_btn = evt.GetEventObject()
        for t in self._tab.choices:
            if evt_btn == t.button:
                self._tab.value = t
                break
        else:
            logging.warning("Couldn't find the tab associated to the button %s",
                            evt_btn)

        evt.Skip()

