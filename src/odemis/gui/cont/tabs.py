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
import os.path
import scipy.misc
import weakref

import pkg_resources
import wx

# IMPORTANT: wx.html needs to be imported for the HTMLWindow defined in the XRC
# file to be correctly identified. See: http://trac.wxwidgets.org/ticket/3626
# This is not related to any particular wxPython version and is most likely permanent.
from wx import html

from odemis import dataio, model
from odemis.acq import calibration
from odemis.gui.comp import overlay
from odemis.gui.comp.canvas import CAN_ZOOM
from odemis.gui.comp.popup import Message
from odemis.gui.comp.scalewindow import ScaleWindow
from odemis.gui.comp.stream import StreamPanel
from odemis.gui.conf import get_acqui_conf
from odemis.gui.cont import settings, tools
from odemis.gui.cont.actuators import ActuatorController
from odemis.gui.cont.microscope import SecomStateController, DelphiStateController
from odemis.gui.util import call_in_wx_main
from odemis.gui.util.img import scale_to_alpha
from odemis.util import units
import odemis.acq.stream as streammod
import odemis.gui.cont.acquisition as acqcont
import odemis.gui.cont.streams as streamcont
import odemis.gui.cont.views as viewcont
import odemis.gui.model as guimod
import odemis.gui.util as guiutil
import odemis.gui.util.align as align
from odemis.driver.actuator import ConvertStage
from odemis.acq.align import AutoFocus
from odemis.acq import stream


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
        :return:

        """

        tab_data = guimod.LiveViewGUIData(main_data)
        super(SecomStreamsTab, self).__init__(name, button, panel, main_frame, tab_data)

        self.main_data = main_data

        buttons = collections.OrderedDict([
            (
                self.main_frame.btn_secom_view_all,
                (None, self.main_frame.lbl_secom_view_all)),
            (
                self.main_frame.btn_secom_view_tl,
                (self.main_frame.vp_secom_tl, self.main_frame.lbl_secom_view_tl)),
            (
                self.main_frame.btn_secom_view_tr,
                (self.main_frame.vp_secom_tr, self.main_frame.lbl_secom_view_tr)),
            (
                self.main_frame.btn_secom_view_bl,
                (self.main_frame.vp_secom_bl, self.main_frame.lbl_secom_view_bl)),
            (
                self.main_frame.btn_secom_view_br,
                (self.main_frame.vp_secom_br, self.main_frame.lbl_secom_view_br)),
            (
                self.main_frame.btn_secom_overview,
                (self.main_frame.vp_overview_sem, self.main_frame.lbl_secom_overview)),
        ])

        # Order matters!
        # First we create the views, then the streams
        self.view_controller = viewcont.ViewPortController(
            self.tab_data_model,
            self.main_frame,
            self.main_frame.pnl_secom_grid.viewports
        )

        self.overview_controller = viewcont.OverviewController(
                                                        self.tab_data_model,
                                                        self.main_frame.vp_overview_sem.canvas)
        ovv = self.main_frame.vp_overview_sem.microscope_view
        if main_data.overview_ccd:
            # Overview camera can be RGB => in that case len(shape) == 4
            if len(main_data.overview_ccd.shape) == 4:
                overview_stream = streammod.RGBCameraStream("Overview", main_data.overview_ccd,
                                                            main_data.overview_ccd.data, None)
            else:
                overview_stream = streammod.BrightfieldStream("Overview", main_data.overview_ccd,
                                                              main_data.overview_ccd.data, None)

            ovv.addStream(overview_stream)
            # TODO: add it to self.tab_data_model.streams?
            # In any case, to support displaying Overview in the normal 2x2
            # views we'd need to have a special Overview class

        self._settings_controller = settings.SecomSettingsController(
            self.main_frame,
            self.tab_data_model
        )

        self._stream_controller = streamcont.StreamController(
            self.tab_data_model,
            self.main_frame.pnl_secom_streams
        )

        # Toolbar
        self.tb = self.main_frame.secom_toolbar
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

        self._view_selector = viewcont.ViewButtonController(
            self.tab_data_model,
            self.main_frame,
            buttons,
            self.main_frame.pnl_secom_grid.viewports
        )

        self._acquisition_controller = acqcont.SecomAcquiController(
            self.tab_data_model,
            self.main_frame
        )

        if main_data.role == "delphi":
            state_controller_cls = DelphiStateController
        else:
            state_controller_cls = SecomStateController

        self._state_controller = state_controller_cls(
            self.tab_data_model,
            self.main_frame,
            "live_btn_",
            self._stream_controller
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
                self._add_em_stream = self._stream_controller.addSEMSED
            elif main_data.bsd:
                self._add_em_stream = self._stream_controller.addSEMBSD
            else:
                logging.error("No EM detector found")

        main_data.chamberState.subscribe(self.on_chamber_state, init=True)
        if not main_data.chamber:
            main_frame.live_btn_press.Hide()

        self._ensure_base_streams()

    @property
    def settings_controller(self):
        return self._settings_controller

    @property
    def stream_controller(self):
        return self._stream_controller

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
        if isinstance(s, stream.StaticStream):
            pass
        elif isinstance(s, stream.SEMStream):
            detector = s.detector
            emitter = s.emitter
            focus = self.main_data.ebeam_focus
        elif isinstance(s, stream.CameraStream):
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
        """
        try:
            curr_s = self.tab_data_model.streams.value[0]
        except IndexError:
            d, e, f = None, None, None
        else:
            # enable only if focuser is available, and no autofocus happening
            d, e, f = self._get_focus_hw(curr_s)

        f_enable = all((updated, d, f))
        if f_enable == False:
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
            has_opt = any(isinstance(s, streammod.OpticalStream)
                          for s in self.tab_data_model.streams.value)
            if not has_opt:
                self._stream_controller.addFluo(add_to_all_views=True, play=False)
                # don't forbid to remove it, as for the user it can be easier to
                # remove than change all the values

        if hasattr(self.tab_data_model, 'emState'):
            has_sem = any(isinstance(s, streammod.EMStream)
                          for s in self.tab_data_model.streams.value)
            if not has_sem:
                sp = self._add_em_stream(add_to_all_views=True, play=False)
                sp.show_remove_btn(False)

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
                if isinstance(s, streammod.OpticalStream):
                    opts = s
                    break
            else: # Could happen if the user has deleted all the optical streams
                sp = self._stream_controller.addFluo(add_to_all_views=True)
                opts = sp.stream

            self._stream_controller.resumeStreams({opts})
            # focus the view
            self.view_controller.focusViewWithStream(opts)
        else:
            self._stream_controller.pauseStreams(streammod.OpticalStream)

    def onEMState(self, state):
        if state == guimod.STATE_ON:
            # Use the last SEM stream played
            for s in self.tab_data_model.streams.value:
                if isinstance(s, streammod.EMStream):
                    sems = s
                    break
            else: # Could happen if the user has deleted all the optical streams
                sp = self._add_em_stream(add_to_all_views=True)
                sp.show_remove_btn(False)
                sems = sp.stream

            self._stream_controller.resumeStreams({sems})
            # focus the view
            self.view_controller.focusViewWithStream(sems)
        else:
            self._stream_controller.pauseStreams(streammod.EMStream)

    def Show(self, show=True):
        assert (show != self.IsShown()) # we assume it's only called when changed
        Tab.Show(self, show=show)

        # pause / restart streams when not displayed
        if show:
            # TODO: double check the chamber state hasn't changed in between
            # We should never turn on the streams if the chamber is not in vacuum
            self._stream_controller.resumeStreams(self._streams_to_restart)
        else:
            paused_st = self._stream_controller.pauseStreams()
            self._streams_to_restart = weakref.WeakSet(paused_st)


class SparcAcquisitionTab(Tab):
    def __init__(self, name, button, panel, main_frame, main_data):
        tab_data = guimod.ScannedAcquisitionGUIData(main_data)
        super(SparcAcquisitionTab, self).__init__(name, button, panel, main_frame, tab_data)

        buttons = collections.OrderedDict([
            (
                self.main_frame.btn_sparc_view_all,
                (None, self.main_frame.lbl_sparc_view_all)),
            (
                self.main_frame.btn_sparc_view_tl,
                (self.main_frame.vp_sparc_acq_view, self.main_frame.lbl_sparc_view_tl)),
            (
                self.main_frame.btn_sparc_view_tr,
                (self.main_frame.vp_sparc_tr, self.main_frame.lbl_sparc_view_tr)),
            (
                self.main_frame.btn_sparc_view_bl,
                (self.main_frame.vp_sparc_bl, self.main_frame.lbl_sparc_view_bl)),
            (
                self.main_frame.btn_sparc_view_br,
                (self.main_frame.vp_sparc_br, self.main_frame.lbl_sparc_view_br)),
        ])

        # Toolbar
        self.tb = self.main_frame.sparc_acq_toolbar
        self.tb.add_tool(tools.TOOL_ROA, self.tab_data_model.tool)
        self.tb.add_tool(tools.TOOL_RO_ANCHOR, self.tab_data_model.tool)
        # TODO: Add the buttons when the functionality is there
        #self.tb.add_tool(tools.TOOL_POINT, self.tab_data_model.tool)
        #self.tb.add_tool(tools.TOOL_RO_ZOOM, self.tab_data_model.tool)

        # Create the streams:
        # * SEM (survey): live stream displaying the current SEM view (full FoV)
        # * SEM CL: SEM stream used to store SEM settings for final acquisition
        # * Spectrum: Repetition stream used to store settings for spectrum
        # * SEM/Spec: MD stream composed of the SEM CL+Spectrum streams
        # * AR: Repetition stream used to store settings for AR
        # * SEM/AR: MD stream composed of the SEM CL+AR streams
        # * SpecCount: Count stream for the live intensity of the spectrometer
        # On acquisition, only the SEM and SEM/Spec (or SEM/AR) are explicitly
        # used.
        self._spec_stream = None
        self._sem_spec_stream = None
        self._ar_stream = None
        self._sem_ar_stream = None
        self._scount_stream = None

        acq_view = self.tab_data_model.acquisitionView
        sem_stream = streammod.SEMStream(
            "SEM survey",
            main_data.sed,
            main_data.sed.data,
            main_data.ebeam)
        self._sem_live_stream = sem_stream
        sem_stream.should_update.value = True
        sem_stream.should_update.subscribe(self._on_sem_update)
        acq_view.addStream(sem_stream)  # it should also be saved

        # the SEM acquisition simultaneous to the CCDs
        semcl_stream = streammod.SEMStream(
            "SEM CL",  # name matters, used to find the stream for the ROI
            main_data.sed,
            main_data.sed.data,
            main_data.ebeam
        )
        self._sem_cl_stream = semcl_stream
        self.tab_data_model.semStream = semcl_stream

        vas_settings = []  # VAs that can affect the acquisition time

        if main_data.spectrometer:
            spec_stream = streammod.SpectrumSettingsStream(
                "Spectrum",
                main_data.spectrometer,
                main_data.spectrometer.data,
                main_data.ebeam)
            spec_stream.roi.subscribe(self.onSpecROI)
            vas_settings.append(spec_stream.repetition)
            self._spec_stream = spec_stream
            self._sem_spec_stream = streammod.SEMSpectrumMDStream("SEM Spectrum",
                                                                  semcl_stream,
                                                                  spec_stream)
            acq_view.addStream(self._sem_spec_stream)

            self._scount_stream = streammod.CameraCountStream("Spectrum count",
                                                              main_data.spectrometer,
                                                              main_data.spectrometer.data,
                                                              main_data.ebeam)
            self._scount_stream.should_update.value = True
            self._scount_stream.windowPeriod.value = 30  # s

        if main_data.ccd:
            ar_stream = streammod.ARSettingsStream(
                "Angular",
                main_data.ccd,
                main_data.ccd.data,
                main_data.ebeam)
            ar_stream.roi.subscribe(self.onARROI)
            vas_settings.append(ar_stream.repetition)
            self._ar_stream = ar_stream
            self._sem_ar_stream = streammod.SEMARMDStream("SEM AR",
                                                          semcl_stream,
                                                          ar_stream)
            acq_view.addStream(self._sem_ar_stream)

        # indicate ROI must still be defined by the user
        semcl_stream.roi.value = streammod.UNDEFINED_ROI
        semcl_stream.roi.subscribe(self.onROI)
        # TODO: try to see if it works better:
        # provide our own ROA VA, with setter actually setting either spec or ar
        # stream's ROI, and returning the value they settle on. Or override the
        # setter of the ROI VA.

        # drift correction is disabled until a roi is selected
        semcl_stream.dcRegion.value = streammod.UNDEFINED_ROI
        vas_settings.append(semcl_stream.dcRegion)
        vas_settings.append(semcl_stream.dcPeriod)
        # Set anchor region dwell time to the same value as the SEM survey
        main_data.ebeam.dwellTime.subscribe(self._copyDwellTimeToAnchor, init=True)

        # create a view on the tab model
        self.view_controller = viewcont.ViewPortController(
            self.tab_data_model,
            self.main_frame,
            self.main_frame.pnl_sparc_grid.viewports
        )

        self._view_selector = viewcont.ViewButtonController(
            self.tab_data_model,
            self.main_frame,
            buttons,
            self.main_frame.pnl_sparc_grid.viewports
        )

        self.tb.add_tool(tools.TOOL_ZOOM_FIT, self.view_controller.fitViewToContent)

        # Add the SEM stream to the focussed (only) view
        self.tab_data_model.streams.value.append(sem_stream)

        if self._ar_stream:
            opt_mic_view = self.tab_data_model.views.value[0]
            opt_mic_view.addStream(self._ar_stream)

        self._stream_controller = streamcont.StreamController(
            self.tab_data_model,
            self.main_frame.pnl_sparc_streams
        )

        self._stream_controller.addStream(sem_stream, add_to_all_views=True)

        # needs to have the AR and Spectrum streams on the acquisition view
        self._settings_controller = settings.SparcSettingsController(
            self.main_frame,
            self.tab_data_model,
            sem_stream=self._sem_cl_stream,
            spec_stream=self._spec_stream,
            ar_stream=self._ar_stream
        )

        # Bind the Spectrometer/Angle resolved buttons to add/remove the
        # streams. Both from the setting panels and the acquisition view.
        if self._ar_stream and self._spec_stream:
            # TODO: probably cleaner to listen to arState and specState VAs +
            # instantiate a MicroscopeStateController?
            # Or get rid of the MicroscopeStateController?
            main_frame.acq_btn_spectrometer.Bind(wx.EVT_BUTTON, self.onToggleSpec)
            main_frame.acq_btn_angular.Bind(wx.EVT_BUTTON, self.onToggleAR)
            if main_data.ar_spec_sel:
                # use the current position to select the default instrument
                # TODO: or just don't select anything, as if the GUI was closed
                # in the alignment tab, it will always end up in AR.
                if main_data.ar_spec_sel.position.value["rx"] == 0:  # AR on
                    self.main_frame.acq_btn_angular.SetToggle(True)
                    self._show_spec(False)
                else:  # Spec on
                    self.main_frame.acq_btn_spectrometer.SetToggle(True)
                    self._scount_stream.should_update.value = True
                    self._show_ar(False)
                    # Show() will take care of setting the lenses
            else:
                # disable everything
                self._show_ar(False)
                self._show_spec(False)
        else:
            # only one detector => hide completely the buttons
            main_frame.sparc_button_panel.Hide()

        main_data.is_acquiring.subscribe(self.on_acquisition)

        # needs settings_controller
        self._acquisition_controller = acqcont.SparcAcquiController(
            self.tab_data_model,
            self.main_frame,
            self.settings_controller,
            semcl_stream.roi,
            vas_settings,
        )

        # Repetition visualisation
        self._hover_stream = None  # stream for which the repetition must be displayed

        # Grab the repetition entries, so we can use it to hook extra event
        # handlers to it.
        self.spec_rep = self._settings_controller.spectro_rep_ent
        if self.spec_rep:
            self.spec_rep.vigilattr.subscribe(self.on_rep_change)
            self.spec_rep.value_ctrl.Bind(wx.EVT_SET_FOCUS, self.on_rep_focus)
            self.spec_rep.value_ctrl.Bind(wx.EVT_KILL_FOCUS, self.on_rep_focus)
            self.spec_rep.value_ctrl.Bind(wx.EVT_ENTER_WINDOW, self.on_spec_rep_enter)
            self.spec_rep.value_ctrl.Bind(wx.EVT_LEAVE_WINDOW, self.on_spec_rep_leave)
        self.spec_pxs = self._settings_controller.spec_pxs_ent
        if self.spec_pxs:
            self.spec_pxs.vigilattr.subscribe(self.on_rep_change)
            self.spec_pxs.value_ctrl.Bind(wx.EVT_SET_FOCUS, self.on_rep_focus)
            self.spec_pxs.value_ctrl.Bind(wx.EVT_KILL_FOCUS, self.on_rep_focus)
            self.spec_pxs.value_ctrl.Bind(wx.EVT_ENTER_WINDOW, self.on_spec_rep_enter)
            self.spec_pxs.value_ctrl.Bind(wx.EVT_LEAVE_WINDOW, self.on_spec_rep_leave)
        self.angu_rep = self._settings_controller.angular_rep_ent
        if self.angu_rep:
            self.angu_rep.vigilattr.subscribe(self.on_rep_change)
            self.angu_rep.value_ctrl.Bind(wx.EVT_SET_FOCUS, self.on_rep_focus)
            self.angu_rep.value_ctrl.Bind(wx.EVT_KILL_FOCUS, self.on_rep_focus)
            self.angu_rep.value_ctrl.Bind(wx.EVT_ENTER_WINDOW, self.on_ar_rep_enter)
            self.angu_rep.value_ctrl.Bind(wx.EVT_LEAVE_WINDOW, self.on_ar_rep_leave)
        # AR settings don't have pixel size

        # Connect the spectrograph count stream to the graph
        if self._scount_stream:
            self._spec_graph = self._settings_controller.spec_graph
            self._txt_mean = self._settings_controller.txt_mean
            self._scount_stream.image.subscribe(self._on_spec_count, init=True)

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

    def _on_sem_update(self, update):
        # very simple version of a stream scheduler
        self._sem_live_stream.is_active.value = update

    def on_acquisition(self, is_acquiring):
        # We don't call set_lenses() now, so that if the user thinks he's more
        # clever he can change the switches manually beforehand (and it's faster).

        # Disable spectrometer count stream during acquisition
        if self._scount_stream:
            active = self._scount_stream.should_update.value and (not is_acquiring)
            self._scount_stream.is_active.value = active

        # Don't change anchor region during acquisition (this can happen
        # because the dwell time VA is directly attached to the hardware,
        # instead of actually being the dwell time of the sem survey stream)
        if is_acquiring:
            self._sem_cl_stream.dcDwellTime.unsubscribe(self._copyDwellTimeToAnchor)
        else:
            self._sem_cl_stream.dcDwellTime.subscribe(self._copyDwellTimeToAnchor)

        # Make sure nothing can be modified during acquisition
        self.main_frame.acq_btn_spectrometer.Enable(not is_acquiring)
        self.main_frame.acq_btn_angular.Enable(not is_acquiring)
        self.tb.enable(not is_acquiring)
        self.main_frame.vp_sparc_acq_view.Enable(not is_acquiring)
        self.main_frame.btn_sparc_change_file.Enable(not is_acquiring)

    def _copyDwellTimeToAnchor(self, dt):
        self._sem_cl_stream.dcDwellTime.value = dt

    # Special event handlers for repetition indication in the ROI selection

    def update_roa_rep(self):
        # Here is the global rule (in order):
        # * if mouse is hovering an entry for AR or spec => display repetition for this one
        # * if an entry for AR or spec has focus => display repetition for this one
        # * don't display repetition

        if self._hover_stream:
            stream = self._hover_stream
        elif (self.spec_rep and
                  (self.spec_rep.value_ctrl.HasFocus() or self.spec_pxs.value_ctrl.HasFocus())):
            stream = self._spec_stream
        elif self.angu_rep and self.angu_rep.value_ctrl.HasFocus():
            stream = self._ar_stream
        else:
            stream = None

        # Convert stream to right display
        cvs = self.main_frame.vp_sparc_acq_view.canvas
        if stream is None:
            cvs.show_repetition(None)
        else:
            rep = stream.repetition.value
            if isinstance(stream, streammod.ARStream):
                style = overlay.world.RepetitionSelectOverlay.FILL_POINT
            else:
                style = overlay.world.RepetitionSelectOverlay.FILL_GRID
            cvs.show_repetition(rep, style)

    def on_rep_focus(self, evt):
        """
        Called when any control related to the repetition get/loose focus
        """
        self.update_roa_rep()
        evt.Skip()

    def on_rep_change(self, rep):
        """
        Called when any repetition VA is changed
        """
        self.update_roa_rep()

    def on_spec_rep_enter(self, evt):
        self._hover_stream = self._spec_stream
        self.update_roa_rep()
        evt.Skip()

    def on_spec_rep_leave(self, evt):
        self._hover_stream = None
        self.update_roa_rep()
        evt.Skip()

    def on_ar_rep_enter(self, evt):
        self._hover_stream = self._ar_stream
        self.update_roa_rep()
        evt.Skip()

    def on_ar_rep_leave(self, evt):
        self._hover_stream = None
        self.update_roa_rep()
        evt.Skip()

    @property
    def settings_controller(self):
        return self._settings_controller

    def Show(self, show=True):
        Tab.Show(self, show=show)

        # Turn on the SEM stream only when displaying this tab
        self._sem_live_stream.should_update.value = show
        if self._scount_stream:
            active = self._scount_stream.should_update.value and show
            self._scount_stream.is_active.value = active

        if show:
            self._set_lenses()
            # don't put switches back when hiding, to avoid unnecessary moves

    def terminate(self):
        # ensure we are not acquiring anything
        self._sem_live_stream.should_update.value = False
        if self._scount_stream:
            self._scount_stream.is_active.value = False

    def onROI(self, roi):
        """
        called when the SEM CL roi (region of acquisition) is changed
        """
        # Updating the ROI requires a bit of care, because the streams might
        # update back their ROI with a modified value. It should normally
        # converge, but we must absolutely ensure it will never cause infinite
        # loops.
        for s in self.tab_data_model.acquisitionView.getStreams():
            if isinstance(s, streammod.SEMCCDMDStream):
                # logging.debug("setting roi of %s to %s", s.name.value, roi)
                s._rep_stream.roi.value = roi

    def onSpecROI(self, roi):
        """
        called when the Spectrometer roi is changed
        """
        # only copy ROI if the stream is activated
        if self._sem_spec_stream in self.tab_data_model.acquisitionView.getStreams():
            # unsubscribe to be sure it won't call us back directly
            self._sem_cl_stream.roi.unsubscribe(self.onROI)
            self._sem_cl_stream.roi.value = roi
            self._sem_cl_stream.roi.subscribe(self.onROI)

    def onARROI(self, roi):
        """
        called when the Angle Resolved roi is changed
        """
        # copy ROI only if it's activated, and spectrum is not, otherwise
        # Spectrum plays the role of "master of ROI" if both streams are
        # simultaneously active (even if it should not currently happen)
        streams = self.tab_data_model.acquisitionView.getStreams()
        if self._sem_ar_stream in streams and self._sem_spec_stream not in streams:
            # unsubscribe to be sure it won't call us back directly
            self._sem_cl_stream.roi.unsubscribe(self.onROI)
            self._sem_cl_stream.roi.value = roi
            self._sem_cl_stream.roi.subscribe(self.onROI)

    def _set_lenses(self):
        """
        Set the lenses ready (as defined by the current stream)
        """
        # Enable the lens and put the mirror to the right position
        streams = self.tab_data_model.acquisitionView.getStreams()
        if self._sem_ar_stream in streams:  # AR on
            ar_spec_pos = 0
        elif self._sem_spec_stream in streams:  # Spec on
            ar_spec_pos = math.radians(90)
        else:  # no stream => nothing to do (yet)
            return

        if self.tab_data_model.main.lens_switch:
            # convention is: 90° == on (lens)
            self.tab_data_model.main.lens_switch.moveAbs({"rx": math.radians(90)})

        if self.tab_data_model.main.ar_spec_sel:
            # convention is: 90° == on (mirror) == spectrometer
            self.tab_data_model.main.ar_spec_sel.moveAbs({"rx": ar_spec_pos})

    def _show_spec(self, show=True):
        """
        Show (or hide) the widgets for spectrum acquisition settings
        It is fine to call it multiple times with the same value, or even if
        no spectrum acquisition can be done (in which case nothing will happen)
        show (bool)
        """
        if not self._sem_spec_stream:
            return

        self.main_frame.fp_settings_sparc_spectrum.Show(show)
        acq_view = self.tab_data_model.acquisitionView
        if show:
            acq_view.addStream(self._sem_spec_stream)
        else:
            acq_view.removeStream(self._sem_spec_stream)

        # (De)Activate live count stream
        self._scount_stream.should_update.value = show
        self._scount_stream.is_active.value = show

        if show:
            self._set_lenses()
            # don't put switches back when hiding, to avoid unnecessary moves

    def _show_ar(self, show=True):
        """
        Show (or hide) the widgets for AR acquisition settings
        It is fine to call it multiple times with the same value, or even if
        no AR acquisition can be done (in which case nothing will happen)
        show (bool)
        """
        if not self._sem_ar_stream:
            return

        self.main_frame.fp_settings_sparc_angular.Show(show)
        acq_view = self.tab_data_model.acquisitionView
        if show:
            acq_view.addStream(self._sem_ar_stream)
        else:
            acq_view.removeStream(self._sem_ar_stream)

        if show:
            self._set_lenses()
            # don't put switches back when hiding, to avoid unnecessary moves

    def onToggleSpec(self, evt):
        """
        called when the Spectrometer button is toggled
        """
        btn = evt.GetEventObject()
        show = btn.GetToggle()
        # TODO: only remove AR if hardware to switch between optical path
        # is not available (but for now, it's never available)
        self._show_ar(False)
        self.main_frame.acq_btn_angular.SetToggle(False)

        self._show_spec(show)
        if show:
            self._spec_stream.roi.value = self._sem_cl_stream.roi.value

    def onToggleAR(self, evt):
        """
        called when the AR button is toggled
        """
        btn = evt.GetEventObject()
        show = btn.GetToggle()
        self._show_spec(False)
        self.main_frame.acq_btn_spectrometer.SetToggle(False)

        self._show_ar(show)
        if show:
            self._ar_stream.roi.value = self._sem_cl_stream.roi.value


class AnalysisTab(Tab):
    """ Handle the loading and displaying of acquisistion files

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

        buttons = collections.OrderedDict([
            (
                self.main_frame.btn_inspection_view_all,
                (None, self.main_frame.lbl_inspection_view_all)
            ),
            (
                self.main_frame.btn_inspection_view_tl,
                (self.main_frame.vp_inspection_tl, self.main_frame.lbl_inspection_view_tl)
            ),
            (
                self.main_frame.btn_inspection_view_tr,
                (self.main_frame.vp_inspection_tr, self.main_frame.lbl_inspection_view_tr)
            ),
            (
                self.main_frame.btn_inspection_view_bl,
                (self.main_frame.vp_inspection_bl, self.main_frame.lbl_inspection_view_bl)
            ),
            (
                self.main_frame.btn_inspection_view_br,
                (self.main_frame.vp_inspection_br, self.main_frame.lbl_inspection_view_br)
            )
        ])

        # The view controller also has special code for the sparc to create the
        # right type of view.
        self.view_controller = viewcont.ViewPortController(
            self.tab_data_model,
            self.main_frame,
            self.main_frame.pnl_inspection_grid.viewports
        )

        # Toolbar
        self.tb = self.main_frame.ana_toolbar
        # TODO: Add the buttons when the functionality is there
        # tb.add_tool(tools.TOOL_RO_ZOOM, self.tab_data_model.tool)
        self.tb.add_tool(tools.TOOL_POINT, self.tab_data_model.tool)
        self.tb.enable_button(tools.TOOL_POINT, False)
        self.tb.add_tool(tools.TOOL_LINE, self.tab_data_model.tool)
        self.tb.enable_button(tools.TOOL_LINE, False)
        self.tb.add_tool(tools.TOOL_ZOOM_FIT, self.view_controller.fitViewToContent)

        # FIXME: Way too hacky approach to get the right viewport shown,
        # so we need to rethink and re-do it. Might involve letting the
        # view controller be more clever and able to create viewports and
        # position them in the sizer.
        # Also see the button definition below.
        # if main_data.role == "sparc":
        #     vp_bottom_left = self.main_frame.vp_angular
        #     ar_view = vp_bottom_left.microscope_view
        #     tab_data.visible_views.value[2] = ar_view  # switch views
        # else:
        #     vp_bottom_left = self.main_frame.vp_inspection_bl

        # save the views to be able to reset them later
        self._def_views = list(tab_data.visible_views.value)

        self._stream_controller = streamcont.StreamController(
            self.tab_data_model,
            self.main_frame.pnl_inspection_streams,
            static=True
        )

        self._settings_controller = settings.AnalysisSettingsController(
            self.main_frame,
            self.tab_data_model
        )
        self._settings_controller.setter_ar_file = self.set_ar_background
        self._settings_controller.setter_spec_bck_file = self.set_spec_background
        self._settings_controller.setter_spec_file = self.set_spec_comp

        self._view_selector = viewcont.ViewButtonController(
            self.tab_data_model,
            self.main_frame,
            buttons,
            self.main_frame.pnl_inspection_grid.viewports
        )

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
        spec_streams = [s for s in streams if isinstance(s, streammod.SpectrumStream)]
        ar_streams = [s for s in streams if isinstance(s, streammod.ARStream)]

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
                        if isinstance(s, streammod.ARStream)]

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
                          if isinstance(s, streammod.SpectrumStream)]

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
                          if isinstance(s, streammod.SpectrumStream)]

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
                    "stream_classes": streammod.CameraStream,
                }
            ),
            (
                main_frame.vp_align_sem,
                {
                    "name": "SEM",
                    "stage": main_data.stage,
                    "stream_classes": streammod.EMStream,
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
        sem_stream = streammod.SEMStream("SEM", main_data.sed,
                                         main_data.sed.data, main_data.ebeam)
        sem_stream.should_update.value = True
        self.tab_data_model.streams.value.append(sem_stream)
        self._sem_stream = sem_stream
        self._sem_view = main_frame.vp_align_sem.microscope_view
        self._sem_view.addStream(sem_stream)

        spot_stream = streammod.SpotSEMStream("Spot", main_data.sed,
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
        # create CCD stream
        ccd_stream = streammod.CameraStream("Optical CL",
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
        ccd_spe = StreamPanel(stream_bar, ccd_stream, self.tab_data_model)
        stream_bar.add_stream_panel(ccd_spe, True)
        ccd_spe.flatten()  # removes the expander header
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
            self._sem_stream.is_active.value = True

        # Set new mode
        if tool == guimod.TOOL_DICHO:
            self.main_frame.pnl_move_to_center.Show(True)
            self.main_frame.pnl_align_tools.Show(False)
        elif tool == guimod.TOOL_SPOT:
            self._sem_stream.should_update.value = False
            self._sem_stream.is_active.value = False
            self._spot_stream.should_update.value = True
            self._spot_stream.is_active.value = True
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

        self._stream_controller = streamcont.StreamController(
            self.tab_data_model,
            self.main_frame.pnl_sparc_align_streams,
            locked=True
        )
        self._ccd_stream = None
        # TODO: add on/off button for the CCD and connect the MicroscopeStateController

        self._settings_controller = settings.SparcAlignSettingsController(
            self.main_frame,
            self.tab_data_model,
        )

        # create the stream to the AR image + goal image
        if main_data.ccd:
            ccd_stream = streammod.CameraStream(
                "Angle-resolved sensor",
                main_data.ccd,
                main_data.ccd.data,
                main_data.ebeam)
            self._ccd_stream = ccd_stream

            # The mirror center (with the lens set) is defined as pole position
            # in the microscope configuration file.
            goal_im = self._getGoalImage(main_data)
            goal_stream = streammod.RGBStream("Goal", goal_im)

            # create a view on the microscope model
            vpv = collections.OrderedDict([
                (main_frame.vp_sparc_align,
                 {"name": "Optical",
                  "stream_classes": None,  # everything is good
                  # no stage, or would need a fake stage to control X/Y of the
                  # mirror
                  # no focus, or could control yaw/pitch?
                 }),
            ])
            self.view_controller = viewcont.ViewPortController(
                self.tab_data_model,
                self.main_frame,
                vpv
            )
            mic_view = self.tab_data_model.focussedView.value
            mic_view.show_crosshair.value = False
            mic_view.merge_ratio.value = 1

            ccd_spe = self._stream_controller.addStream(ccd_stream)
            ccd_spe.flatten()
            # TODO: use addStatic ?
            self._stream_controller.addStream(goal_stream, visible=False)
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
            self._scount_stream = streammod.CameraCountStream("Spectrum count",
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
            self.main_frame.fp_settings_sparc_spectrum.Show(False)

        if main_data.ebeam:
            # Force a spot at the center of the FoV
            # Not via stream controller, so we can avoid the scheduler
            spot_stream = streammod.SpotSEMStream("SpotSEM", main_data.sed,
                                                  main_data.sed.data, main_data.ebeam)
            self._spot_stream = spot_stream
        else:
            self._spot_stream = None

        # Save the current filter
        if main_data.light_filter:
            self._prev_filter = main_data.light_filter.position.value["band"]
            self._move_filter_f = model.InstantaneousFuture()  # "fake" move

        self._actuator_controller = ActuatorController(self.tab_data_model,
                                                       main_frame,
                                                       "mirror_align_")

        # Bind keys
        self._actuator_controller.bind_keyboard(main_frame.pnl_tab_sparc_align)

    # TODO: factorize with SparcAcquisitionTab
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
        main_data = self.tab_data_model.main
        if show:
            if main_data.lens_switch:
                # convention is: 0 rad == off (no lens)
                main_data.lens_switch.moveAbs({"rx": 0})
            if main_data.ar_spec_sel:
                # convention is: 0 rad == off (no mirror) == AR
                main_data.ar_spec_sel.moveAbs({"rx": 0})

            # pick a filter which is pass-through (=empty)
            if main_data.light_filter:
                fltr = main_data.light_filter
                # find the right filter
                for p, d in fltr.axes["band"].choices.items():
                    if d == "pass-through":
                        if self._move_filter_f.done():
                            # Don't save if it's not yet in the previous value
                            # (can happen when quickly switching between tabs)
                            self._prev_filter = fltr.position.value["band"]
                        else:
                            self._move_filter_f.cancel()
                        fltr.moveAbs({"band": p})
                        break
                else:
                    logging.info("Failed to find pass-through filter")
        else:
            # don't put it back lenses when hiding, to avoid unnessary moves
            if main_data.light_filter:
                # If the user has just started to change the filter it won't be
                # recorded... not sure how to avoid it easily, so for now we'll
                # accept this little drawback.
                f = main_data.light_filter.moveAbs({"band": self._prev_filter})
                self._move_filter_f = f

    def terminate(self):
        if self._ccd_stream:
            self._ccd_stream.is_active.value = False
        if self._spot_stream:
            self._spot_stream.is_active.value = False


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

