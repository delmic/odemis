#-*- coding: utf-8 -*-

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

from Pyro4.core import isasync
from collections import OrderedDict
import collections
import logging
import math
from odemis import dataio, model
from odemis.gui.comp import overlay
from odemis.gui.comp.stream import StreamPanel
from odemis.gui.cont import settings, tools
from odemis.gui.cont.acquisition import SecomAcquiController, \
    SparcAcquiController
from odemis.gui.cont.actuators import ActuatorController
from odemis.gui.cont.microscope import MicroscopeStateController
from odemis.gui.model.img import InstrumentalImage
from odemis.gui.util import get_picture_folder, formats_to_wildcards, conversion, \
    units, call_after
import os.path
import pkg_resources
import weakref
import wx

import odemis.gui.cont.streams as streamcont
import odemis.gui.cont.views as viewcont
import odemis.gui.model as guimodel
import odemis.gui.model.stream as streammod


class Tab(object):
    """ Small helper class representing a tab (tab button + panel) """

    def __init__(self, name, button, panel, main_frame, tab_data, label=None):
        self.name = name
        self.label = label
        self.button = button
        self.panel = panel
        self.main_frame = main_frame
        self.tab_data_model = tab_data

    def Show(self, show=True):
        self.button.SetToggle(show)
        if show:
            self._connect_22view_event()
            self._connect_crosshair_event()

        self.panel.Show(show)

    def _connect_22view_event(self):
        """ If the tab has a 2x2 view, this method will connect it to the 2x2
        view menu item (or ensure it's disabled).
        """
        if len(self.tab_data_model.views.value) >= 4:
            # We assume it has a 2x2 view layout
            def set_22_menu_check(viewlayout):
                """Called when the view layout changes"""
                is_22 = viewlayout == guimodel.VIEW_LAYOUT_22
                self.main_frame.menu_item_22view.Check(is_22)

            def on_switch_22(evt):
                """Called when menu changes"""
                if self.tab_data_model.viewLayout.value == guimodel.VIEW_LAYOUT_22:
                    self.tab_data_model.viewLayout.value = guimodel.VIEW_LAYOUT_ONE
                else:
                    self.tab_data_model.viewLayout.value = guimodel.VIEW_LAYOUT_22

            # Bind the function to the menu item, so it keeps the reference.
            # The VigillantAttribute will not unsubscribe it, until replaced.
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
            self.main_frame.menu_item_22view.vamethod = None # drop VA subscr.

    def _connect_crosshair_event(self):
        """ If the tab contains views with a crosshair overlay, it will connect
        an event to the view menu allowing for the toggling to the visibility
        of those crosshairs.
        """
        # only if there's a focussed view that we can track
        if hasattr(self.tab_data_model, 'focussedView'):
            def set_cross_check(fv):
                """Called when focused view changes"""
                is_shown = fv.show_crosshair.value
                self.main_frame.menu_item_cross.Check(is_shown)
                # TODO: just (un)subscribe to the show_crosshair
                # (for now it works because the menu is the only place to change it)

            def on_switch_crosshair(evt):
                """Called when menu changes"""
                show = self.main_frame.menu_item_cross.IsChecked()
                foccused_view = self.tab_data_model.focussedView.value
                foccused_view.show_crosshair.value = show

            # Bind the function to the menu item, so it keeps the reference.
            # The VigillantAttribute will not unsubscribe it, until replaced.
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
            self.main_frame.menu_item_cross.vamethod = None # drop VA subscription

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

class SecomStreamsTab(Tab):

    def __init__(self, name, button, panel, main_frame, main_data):

        tab_data = guimodel.LiveViewGUIData(main_data)
        super(SecomStreamsTab, self).__init__(name, button, panel,
                                              main_frame, tab_data)


        # Order matters!
        # First we create the views, then the streams
        self._view_controller = viewcont.ViewController(
                                    self.tab_data_model,
                                    self.main_frame,
                                    [self.main_frame.vp_secom_tl,
                                     self.main_frame.vp_secom_tr,
                                     self.main_frame.vp_secom_bl,
                                     self.main_frame.vp_secom_br]
                                )

        self._settings_controller = settings.SecomSettingsController(
                                        self.main_frame,
                                        self.tab_data_model
                                    )

        self._stream_controller = streamcont.StreamController(
                                        self.tab_data_model,
                                        self.main_frame.pnl_secom_streams
                                  )
        buttons = OrderedDict([
                (self.main_frame.btn_secom_view_all,
                        (None, self.main_frame.lbl_secom_view_all)),
                (self.main_frame.btn_secom_view_tl,
                        (self.main_frame.vp_secom_tl,
                         self.main_frame.lbl_secom_view_tl)),
                (self.main_frame.btn_secom_view_tr,
                        (self.main_frame.vp_secom_tr,
                         self.main_frame.lbl_secom_view_tr)),
                (self.main_frame.btn_secom_view_bl,
                        (self.main_frame.vp_secom_bl,
                         self.main_frame.lbl_secom_view_bl)),
                (self.main_frame.btn_secom_view_br,
                        (self.main_frame.vp_secom_br,
                         self.main_frame.lbl_secom_view_br))
                   ])

        self._view_selector = viewcont.ViewSelector(
                                    self.tab_data_model,
                                    self.main_frame,
                                    buttons
                              )

        self._acquisition_controller = SecomAcquiController(
                                            self.tab_data_model,
                                            self.main_frame
                                       )

        self._state_controller = MicroscopeStateController(
                                            self.tab_data_model,
                                            self.main_frame,
                                            "live_btn_"
                                      )

        # To automatically play/pause a stream when turning on/off a microscope,
        # and add the stream on the first time.
        # Note: weakref, so that if a stream is removed, we don't turn it back
        # on
        if hasattr(main_data, 'opticalState'):
            self._opt_streams_enabled = False
            self._opt_stream_to_restart = set() # weakref set of Streams
            main_data.opticalState.subscribe(self.onOpticalState)

        if hasattr(main_data, 'emState'):
            self._sem_streams_enabled = False
            self._sem_stream_to_restart = set()
            main_data.emState.subscribe(self.onEMState)

        # Toolbar
        tb = self.main_frame.secom_toolbar
        # TODO: Add the buttons when the functionality is there
        #tb.add_tool(tools.TOOL_ROI, self.tab_data_model.tool)
        #tb.add_tool(tools.TOOL_RO_ZOOM, self.tab_data_model.tool)
        tb.add_tool(tools.TOOL_ZOOM_FIT, self.onZoomFit)

    @property
    def settings_controller(self):
        return self._settings_controller

    @property
    def stream_controller(self):
        return self._stream_controller

    def onZoomFit(self, event):
        self._view_controller.fitCurrentViewToContent()

    # TODO: also pause the streams when leaving the tab

    # TODO: how to prevent the user from turning on camera/light again from the
    #   stream panel when the microscope is off? => either stream panel "update"
    #   icon is disabled/enable (decided by the stream controller), or the event
    #   handler checks first that the appropriate microscope is On or Off.


    def onOpticalState(self, state):
        enabled = (state == guimodel.STATE_ON) and self.IsShown()
        if self._opt_streams_enabled == enabled:
            return # no change
        else:
            self._opt_streams_enabled = enabled

        if enabled:
            # check whether we need to create a (first) bright-field stream
            has_bf = any(
                        isinstance(s, streammod.BrightfieldStream)
                        for s in self.tab_data_model.streams.value)
            if not has_bf:
                sp = self._stream_controller.addBrightfield(add_to_all_views=True)
                sp.show_remove_btn(False)

            self._stream_controller.resumeStreams(self._opt_stream_to_restart)
        else:
            paused_st = self._stream_controller.pauseStreams(streammod.OPTICAL_STREAMS)
            self._opt_stream_to_restart = weakref.WeakSet(paused_st)

    def onEMState(self, state):
        enabled = (state == guimodel.STATE_ON) and self.IsShown()
        if self._sem_streams_enabled == enabled:
            return # no change
        else:
            self._sem_streams_enabled = enabled

        if enabled:
            # check whether we need to create a (first) SEM stream
            has_sem = any(
                        isinstance(s, streammod.EM_STREAMS)
                        for s in self.tab_data_model.streams.value)
            if not has_sem:
                sp = self._stream_controller.addSEMSED(add_to_all_views=True)
                sp.show_remove_btn(False)

            self._stream_controller.resumeStreams(self._sem_stream_to_restart)
        else:
            paused_st = self._stream_controller.pauseStreams(streammod.EM_STREAMS)
            self._sem_stream_to_restart = weakref.WeakSet(paused_st)

    def Show(self, show=True):
        Tab.Show(self, show=show)

        # Force the check for the stream update
        main_data = self.tab_data_model.main
        if hasattr(main_data, 'opticalState'):
            self.onOpticalState(main_data.opticalState.value)
        if hasattr(main_data, 'emState'):
            self.onEMState(main_data.emState.value)


class SparcAcquisitionTab(Tab):

    def __init__(self, name, button, panel, main_frame, main_data):
        tab_data = guimodel.ScannedAcquisitionGUIData(main_data)
        super(SparcAcquisitionTab, self).__init__(name, button, panel,
                                                  main_frame, tab_data)

        self._spec_stream = None
        self._ar_stream = None

        # list of streams for acquisition
        acq_view = self.tab_data_model.acquisitionView

        # create the streams
        sem_stream = streammod.SEMStream(
                        "SEM survey",
                        main_data.sed,
                        main_data.sed.data,
                        main_data.ebeam)
        self._sem_live_stream = sem_stream
        sem_stream.should_update.value = False
        acq_view.addStream(sem_stream) # it should also be saved

        # the SEM acquisition simultaneous to the CCDs
        semcl_stream = streammod.SEMStream(
                "SEM CL", # name matters, used to find the stream for the ROI
                main_data.sed,
                main_data.sed.data,
                main_data.ebeam
        )
        acq_view.addStream(semcl_stream)
        self._sem_cl_stream = semcl_stream


        if main_data.spectrometer:
            spec_stream = streammod.SpectrumStream(
                                        "Spectrum",
                                        main_data.spectrometer,
                                        main_data.spectrometer.data,
                                        main_data.ebeam)
            acq_view.addStream(spec_stream)
            spec_stream.roi.subscribe(self.onSpecROI)
            self._spec_stream = spec_stream

        if main_data.ccd:
            ar_stream = streammod.ARStream(
                                "Angular",
                                main_data.ccd,
                                main_data.ccd.data,
                                main_data.ebeam)
            acq_view.addStream(ar_stream)
            ar_stream.roi.subscribe(self.onARROI)
            self._ar_stream = ar_stream


        # indicate ROI must still be defined by the user
        semcl_stream.roi.value = streammod.UNDEFINED_ROI
        semcl_stream.roi.subscribe(self.onROI, init=True)

        # create a view on the tab model
        # Needs SEM CL stream (could be avoided if we had a .roa on the
        # tab model)
        self._view_controller = viewcont.ViewController(
                                    self.tab_data_model,
                                    self.main_frame,
                                    [self.main_frame.vp_sparc_acq_view]
                                )
        mic_view = self.tab_data_model.focussedView.value
        mic_view.addStream(sem_stream)  #pylint: disable=E1103

        # needs to have the AR and Spectrum streams on the acquisition view
        self._settings_controller = settings.SparcSettingsController(
                                        self.main_frame,
                                        self.tab_data_model,
                                    )
        # Bind the Spectrometer/Angle resolved buttons to add/remove the
        # streams. Both from the setting panels and the acquisition view.
        if self._ar_stream and self._spec_stream:
            main_frame.acq_btn_spectrometer.Bind(wx.EVT_BUTTON, self.onToggleSpec)
            main_frame.acq_btn_angular.Bind(wx.EVT_BUTTON, self.onToggleAR)
            # TODO: listen to acq_view.streams and hide/show setting accordingly
            main_frame.fp_settings_sparc_spectrum.Hide()
            main_frame.fp_settings_sparc_angular.Hide()
            acq_view.removeStream(spec_stream)
            acq_view.removeStream(ar_stream)
        else:
            # TODO: if only one detector => hide completely the buttons
            pass # non-available settings are already hidden

        # needs settings_controller
        self._acquisition_controller = SparcAcquiController(
                                            self.main_frame,
                                            self.tab_data_model,
                                            self.settings_controller
                                       )

        # Repetition visualisation
        self._hover_stream = None # stream for which the repetition must be displayed

        # Grab the repetition entries, so we can use it to hook extra event
        # handlers to it.
        self.spec_rep = self._settings_controller.spectro_rep_ent
        if self.spec_rep:
            self.spec_rep.va.subscribe(self.on_rep_change)
            self.spec_rep.ctrl.Bind(wx.EVT_SET_FOCUS, self.on_rep_focus)
            self.spec_rep.ctrl.Bind(wx.EVT_KILL_FOCUS, self.on_rep_focus)
            self.spec_rep.ctrl.Bind(wx.EVT_ENTER_WINDOW, self.on_spec_rep_enter)
            self.spec_rep.ctrl.Bind(wx.EVT_LEAVE_WINDOW, self.on_spec_rep_leave)
        self.spec_pxs = self._settings_controller.spec_pxs_ent
        if self.spec_pxs:
            self.spec_pxs.va.subscribe(self.on_rep_change)
            self.spec_pxs.ctrl.Bind(wx.EVT_SET_FOCUS, self.on_rep_focus)
            self.spec_pxs.ctrl.Bind(wx.EVT_KILL_FOCUS, self.on_rep_focus)
            self.spec_pxs.ctrl.Bind(wx.EVT_ENTER_WINDOW, self.on_spec_rep_enter)
            self.spec_pxs.ctrl.Bind(wx.EVT_LEAVE_WINDOW, self.on_spec_rep_leave)
        self.angu_rep = self._settings_controller.angular_rep_ent
        if self.angu_rep:
            self.angu_rep.va.subscribe(self.on_rep_change)
            self.angu_rep.ctrl.Bind(wx.EVT_SET_FOCUS, self.on_rep_focus)
            self.angu_rep.ctrl.Bind(wx.EVT_KILL_FOCUS, self.on_rep_focus)
            self.angu_rep.ctrl.Bind(wx.EVT_ENTER_WINDOW, self.on_ar_rep_enter)
            self.angu_rep.ctrl.Bind(wx.EVT_LEAVE_WINDOW, self.on_ar_rep_leave)

        # Toolbar
        tb = self.main_frame.sparc_acq_toolbar
        tb.add_tool(tools.TOOL_ROA, self.tab_data_model.tool)
        # TODO: Add the buttons when the functionality is there
        #tb.add_tool(tools.TOOL_POINT, self.tab_data_model.tool)
        #tb.add_tool(tools.TOOL_RO_ZOOM, self.tab_data_model.tool)
        tb.add_tool(tools.TOOL_ZOOM_FIT, self.onZoomFit)

    # Special event handlers for repetition indication in the ROI selection

    def update_roa_rep(self):
        # Here is the global rule (in order):
        # * if mouse is hovering an entry for AR or spec => display repetition for this one
        # * if an entry for AR or spec has focus => display repetition for this one
        # * don't display repetition

        if self._hover_stream:
            stream = self._hover_stream
        elif self.spec_rep.ctrl.HasFocus() or self.spec_pxs.ctrl.HasFocus():
            stream = self._spec_stream
        elif self.angu_rep.ctrl.HasFocus():
            stream = self._ar_stream
        else:
            stream = None

        # Convert stream to right display
        cvs = self.main_frame.vp_sparc_acq_view.canvas
        if stream is None:
            cvs.showRepetition(None)
        else:
            rep = stream.repetition.value
            if isinstance(stream, streammod.AR_STREAMS):
                style = overlay.FILL_POINT
            else:
                style = overlay.FILL_GRID
            cvs.showRepetition(rep, style)

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

    def onZoomFit(self, event):
        self._view_controller.fitCurrentViewToContent()

    def Show(self, show=True):
        Tab.Show(self, show=show)

        # Turn on the SEM stream only when displaying this tab
        if show:
            self._sem_live_stream.is_active.value = True
        else:
            self._sem_live_stream.is_active.value = False

    def terminate(self):
        # ensure we are not acquiring anything
        self._sem_live_stream.is_active.value = False

    def onROI(self, roi):
        """
        called when the SEM CL roi (region of acquisition) is changed
        """
        # Updating the ROI requires a bit of care, because the streams might
        # update back their ROI with a modified value. It should normally
        # converge, but we must absolutely ensure it will never cause infinite
        # loops.
        for s in self.tab_data_model.acquisitionView.getStreams():
            if isinstance(s, streammod.AR_STREAMS + streammod.SPECTRUM_STREAMS):
#                logging.debug("setting roi of %s to %s", s.name.value, roi)
                s.roi.value = roi

    def onSpecROI(self, roi):
        """
        called when the Spectrometer roi is changed
        """
        # only copy ROI if the stream is activated
        if self._spec_stream in self.tab_data_model.acquisitionView.getStreams():
            # unsubscribe to be sure it won't call us back directly
            self._sem_cl_stream.roi.unsubscribe(self.onROI)
            self._sem_cl_stream.roi.value = roi
            self._sem_cl_stream.roi.subscribe(self.onROI)

    def onARROI(self, roi):
        """
        called when the Angle Resolved roi is changed
        """
        # copy ROI only if it's activated, and spectrum is not, otherwise
        # Spectrum plays the role of "master of ROI".
        streams = self.tab_data_model.acquisitionView.getStreams()
        if self._ar_stream in streams and self._spec_stream not in streams:
            # unsubscribe to be sure it won't call us back directly
            self._sem_cl_stream.roi.unsubscribe(self.onROI)
            self._sem_cl_stream.roi.value = roi
            self._sem_cl_stream.roi.subscribe(self.onROI)

    def onToggleSpec(self, evt):
        """
        called when the Spectrometer button is toggled
        """
        acq_view = self.tab_data_model.acquisitionView

        btn = evt.GetEventObject()
        if btn.GetToggle():
            # TODO: only remove AR if hardware to switch between optical path
            # is not available (but for now, it's never available)
            # Remove AR if currently activated
            self.main_frame.fp_settings_sparc_angular.Hide()
            acq_view.removeStream(self._ar_stream)
            self.main_frame.acq_btn_angular.SetToggle(False)

            # Add Spectrometer stream
            self.main_frame.fp_settings_sparc_spectrum.Show()
            acq_view.addStream(self._spec_stream)
            self._spec_stream.roi.value = self._sem_cl_stream.roi.value
        else:
            self.main_frame.fp_settings_sparc_spectrum.Hide()
            acq_view.removeStream(self._spec_stream)

    def onToggleAR(self, evt):
        """
        called when the AR button is toggled
        """
        acq_view = self.tab_data_model.acquisitionView

        btn = evt.GetEventObject()
        if btn.GetToggle():
            # Remove Spectrometer if currently activated
            self.main_frame.fp_settings_sparc_spectrum.Hide()
            acq_view.removeStream(self._spec_stream)
            self.main_frame.acq_btn_spectrometer.SetToggle(False)

            # Add AR stream
            self.main_frame.fp_settings_sparc_angular.Show()
            acq_view.addStream(self._ar_stream)
            self._ar_stream.roi.value = self._sem_cl_stream.roi.value
        else:
            self.main_frame.fp_settings_sparc_angular.Hide()
            acq_view.removeStream(self._ar_stream)

class AnalysisTab(Tab):

    def __init__(self, name, button, panel, main_frame, main_data):
        """
        microscope will be used only to select the type of views
        """
        # TODO: automatically change the display type based on the acquisition
        # displayed
        tab_data = guimodel.AnalysisGUIData(main_data)
        super(AnalysisTab, self).__init__(name, button, panel,
                                          main_frame, tab_data)

        self._view_controller = viewcont.ViewController(
                                    self.tab_data_model,
                                    self.main_frame,
                                    [self.main_frame.vp_inspection_tl,
                                     self.main_frame.vp_inspection_tr,
                                     self.main_frame.vp_inspection_bl,
                                     self.main_frame.vp_inspection_br,
                                     self.main_frame.vp_inspection_plot],
                                )

        self._stream_controller = streamcont.StreamController(
                                        self.tab_data_model,
                                        self.main_frame.pnl_inspection_streams,
                                        static=True
                                  )

        self._settings_controller = settings.AnalysisSettingsController(
                                        self.main_frame,
                                        self.tab_data_model
                                    )

        buttons = OrderedDict([
            (self.main_frame.btn_sparc_view_all,
                    (None, self.main_frame.lbl_sparc_view_all)),
            (self.main_frame.btn_sparc_view_tl,
                    (self.main_frame.vp_inspection_tl,
                     self.main_frame.lbl_sparc_view_tl)),
            (self.main_frame.btn_sparc_view_tr,
                    (self.main_frame.vp_inspection_tr,
                     self.main_frame.lbl_sparc_view_tr)),
            (self.main_frame.btn_sparc_view_bl,
                    (self.main_frame.vp_inspection_bl,
                     self.main_frame.lbl_sparc_view_bl)),
            (self.main_frame.btn_sparc_view_br,
                    (self.main_frame.vp_inspection_br,
                     self.main_frame.lbl_sparc_view_br))
               ])

        self._view_selector = viewcont.ViewSelector(
                                    self.tab_data_model,
                                    self.main_frame,
                                    buttons
                              )

        self.main_frame.btn_open_image.Bind(
                            wx.EVT_BUTTON,
                            self.on_file_open_button
        )

        # Toolbar
        self.tb = self.main_frame.ana_toolbar
        # TODO: Add the buttons when the functionality is there
        #tb.add_tool(tools.TOOL_RO_ZOOM, self.tab_data_model.tool)
        self.tb.add_tool(tools.TOOL_POINT, self.tab_data_model.tool)
        self.tb.enable_button(tools.TOOL_POINT, False)
        self.tb.add_tool(tools.TOOL_ZOOM_FIT, self.onZoomFit)

        # TODO:
        #   - Where should the swap take place (including the data loading) when
        #     a point is selected?
        #   - How should we handle the case that when the zoom is too small and
        #     a single pixel on the screen is larger than a pixel in the
        #     spectrum data?
        #   - How to handle swapping back and forth between 1x1 and 2x2 view

        self.tab_data_model.tool.subscribe(self._onTool, init=True)

    @property
    def stream_controller(self):
        return self._stream_controller

    def onZoomFit(self, event):
        self._view_controller.fitCurrentViewToContent()

    def on_file_open_button(self, evt):
        """ Open an image file using a file dialog box
        """

        # Find the available formats (and corresponding extensions)
        formats_to_ext = dataio.get_available_formats(os.O_RDONLY)

        fi = self.tab_data_model.fileinfo.value
        #pylint: disable=E1103
        if fi and fi.file_name:
            path, _ = os.path.split(fi.file_name)
        else:
            path = get_picture_folder()

        wildcards, formats = formats_to_wildcards(formats_to_ext, include_all=True)
        dialog = wx.FileDialog(self.panel,
                               message="Choose a file to load",
                               defaultDir=path,
                               defaultFile="",
                               style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
                               wildcard=wildcards)

        # Show the dialog and check whether is was accepted or cancelled
        if dialog.ShowModal() != wx.ID_OK:
            return

        # Reset any mode tool when a new image is being opened
        self.tab_data_model.tool.value = guimodel.TOOL_NONE

        # Reset the view
        self._view_controller.reset()

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
                logging.warning("Couldn't guess format from filename '%s',"
                                " will use %s.", fn, fmt)

        converter = dataio.get_exporter(fmt)
        try:
            data = converter.read_data(fn)
        except Exception: #pylint: disable=W0703
            logging.exception("Failed to open file '%s' with format %s", fn, fmt)

        self.display_new_data(fn, data)
        spec_cls = (streammod.SpectrumStream, streammod.StaticSpectrumStream)

        for strm in self.tab_data_model.streams.value:
            if isinstance(strm, spec_cls):
                iimg = strm.image.value
                for viewport in self._view_controller.viewports:
                    if hasattr(viewport.canvas, "point_overlay"):
                        ol = viewport.canvas.point_overlay
                        ol.set_values(
                                    iimg.mpp,
                                    iimg.center,
                                    iimg.get_pixel_size(),
                                    strm.selected_pixel
                        )
                self.tb.enable_button(tools.TOOL_POINT, True)
                break
        else:
            self.tb.enable_button(tools.TOOL_POINT, False)



    def display_new_data(self, filename, data):
        """
        Display a new data set (removing all references to the current one)
        filename (string): Name of the file containing the data.
        data (list of model.DataArray): the data to display. Should have at
         least one DataArray.
        """
        fi = guimodel.FileInfo(filename)

        # remove all the previous streams
        self._stream_controller.clear()

        # Force the canvases to fit to the content
        for vp in [self.main_frame.vp_inspection_tl,
                   self.main_frame.vp_inspection_tr,
                   self.main_frame.vp_inspection_bl,
                   self.main_frame.vp_inspection_br]:
            vp.microscope_view.getMPPFromNextImage = False
            vp.canvas.fitViewToNextImage = True

        acq_date = fi.metadata.get(model.MD_ACQ_DATE, None)
        # Add each data as a stream of the correct type
        for d in data:
            try:
                im_acq_date = d.metadata[model.MD_ACQ_DATE]
                acq_date = min(acq_date or im_acq_date, im_acq_date)
            except KeyError: # no MD_ACQ_DATE
                pass # => don't update the acq_date

            # Streams only support 2D data (e.g., no multiple channels like RGB)
            # excepted for spectrums which have a 3rd dimensions on dim 5.
            # So if it's the case => separate into one stream per channel
            cdata = self._split_channels(d)

            for cd in cdata:
                # TODO: be more clever to detect the type of stream
                if (model.MD_WL_LIST in cd.metadata or
                    model.MD_WL_POLYNOMIAL in cd.metadata or
                    (len(cd.shape) >= 5 and cd.shape[-5] > 1)):
                    desc = cd.metadata.get(model.MD_DESCRIPTION, "Spectrum")
                    cls = streammod.StaticSpectrumStream
                elif ((model.MD_IN_WL in cd.metadata and
                      model.MD_OUT_WL in cd.metadata) or
                      model.MD_USER_TINT in cd.metadata):
                    # No explicit way to distinguish between Brightfield and Fluo,
                    # so guess it's Brightfield iif:
                    # * No tint
                    # * (and) Large band for excitation wl (> 100 nm)
                    in_wl = d.metadata[model.MD_IN_WL]
                    if (model.MD_USER_TINT in cd.metadata or
                        in_wl[1] - in_wl[0] < 100e-9):
                        # Fluo
                        desc = cd.metadata.get(model.MD_DESCRIPTION, "Filtered colour")
                        cls = streammod.StaticFluoStream
                    else:
                        # Brigthfield
                        desc = cd.metadata.get(model.MD_DESCRIPTION, "Brightfield")
                        cls = streammod.StaticBrightfieldStream
                elif model.MD_IN_WL in cd.metadata: # no MD_OUT_WL
                    desc = cd.metadata.get(model.MD_DESCRIPTION, "Brightfield")
                    cls = streammod.StaticBrightfieldStream
                else:
                    desc = cd.metadata.get(model.MD_DESCRIPTION, "Secondary electrons")
                    cls = streammod.StaticSEMStream
                # TODO: ARStreams

                self._stream_controller.addStatic(desc, cd, cls=cls,
                                                  add_to_all_views=True)
        if acq_date:
            fi.metadata[model.MD_ACQ_DATE] = acq_date
        self.tab_data_model.fileinfo.value = fi

    @call_after
    def _onTool(self, tool):
        """
        Called when the tool (mode) is changed
        """

        # Reset the viewports when the spot tool is not selected
        # Doing it this way, causes some unnecessary calls to the reset method
        # but it cannot be avoided. Subscribing to the tool VA will only
        # tell us what the new tool is and not what the previous, if any, was.
        if tool != guimodel.TOOL_POINT:
            self._view_controller.reset()

    def _split_channels(self, data):
        """
        Separate a DataArray into multiple DataArrays along the 3rd dimension
        (channel).
        data (DataArray): can be any shape
        Returns (list of DataArrays): a list of one DataArray (if no splitting
        is needed) or more (if splitting happened). The metadata is the same
        (object) for all the DataArrays.
        """
        # Anything to split?
        if len(data.shape) >= 3 and data.shape[-3] > 1:
            # multiple channels => split
            das = []
            for c in range(data.shape[-3]):
                das.append(data[..., c, :, :]) # metadata ref is copied
            return das
        else:
            # return just one DA
            return [data]


class LensAlignTab(Tab):
    """ Tab for the lens alignment on the Secom platform
    The streams are automatically active when the tab is shown
    """

    def __init__(self, name, button, panel, main_frame, main_data):
        tab_data = guimodel.ActuatorGUIData(main_data)
        super(LensAlignTab, self).__init__(name, button, panel,
                                           main_frame, tab_data)


        # TODO: we should actually display the settings of the streams (...once they have it)
        self._settings_controller = settings.LensAlignSettingsController(
                                        self.main_frame,
                                        self.tab_data_model
                                    )

        main_frame.vp_align_sem.ShowLegend(False)

        # See axes convention: A/B are 135° from Y/X
        # Note that this is an approximation of the actual movements.
        # In the current SECOM design, B affects both axes (not completely in a
        # linear fashion) and A affects mostly X (not completely in a linear
        # fashion). By improving the model (=conversion A/B <-> X/Y), the GUI
        # could behave in a more expected way to the user, but the current
        # approximation is enough to do the calibration relatively quickly.
        self._stage_ab = InclinedStage("converter-ab", "stage",
                                       children={"aligner": main_data.aligner},
                                       axes=["b", "a"],
                                       angle=135)
        # vp_align_sem is connected to the stage
        vpv = collections.OrderedDict([
                (main_frame.vp_align_ccd,  # focused view
                 {"name": "Optical",
                  "stage": self._stage_ab,
                  "focus1": main_data.focus,
                  "stream_classes": (streammod.CameraNoLightStream,),
                  }),
                (main_frame.vp_align_sem,
                 {"name": "SEM",
                  "stage": main_data.stage,
                  "stream_classes": streammod.EM_STREAMS,
                  },
                 )
                                       ])
        self._view_controller = viewcont.ViewController(
                                    self.tab_data_model,
                                    self.main_frame,
                                    vpv)

        # No stream controller, because it does far too much (including hiding
        # the only stream entry when SEM view is focused)
        sem_stream = streammod.SEMStream("SEM", main_data.sed,
                                         main_data.sed.data, main_data.ebeam)
        self._sem_stream = sem_stream
        self._sem_view = main_frame.vp_align_sem.microscope_view
        self._sem_view.addStream(sem_stream)
        # Adapt the zoom level of the SEM to fit exactly the SEM field of view.
        # No need to check for resize events, because the view has a fixed size.
        main_frame.vp_align_sem.canvas.canZoom = False
        # prevent the first image to reset our computation
        self._sem_view.getMPPFromNextImage = False
        main_data.ebeam.pixelSize.subscribe(self._onSEMpxs, init=True)

        # Update the SEM area in dichotomic mode
        self.tab_data_model.dicho_seq.subscribe(self._onDichoSeq, init=True)

        # create CCD stream
        ccd_stream = streammod.CameraNoLightStream("Optical",
                                     main_data.ccd,
                                     main_data.ccd.data,
                                     main_data.light,
                                     position=self._stage_ab.position)
        self._ccd_stream = ccd_stream
        ccd_view = main_frame.vp_align_ccd.microscope_view
        ccd_view.addStream(ccd_stream)
        # create CCD stream panel entry
        stream_bar = self.main_frame.pnl_secom_align_streams
        ccd_spe = StreamPanel(stream_bar, ccd_stream, self.tab_data_model)
        stream_bar.add_stream(ccd_spe, True)
        ccd_spe.flatten() # removes the expander header
        # Fit CCD image to screen
        self._ccd_view = ccd_view
        ccd_view.getMPPFromNextImage = False
        # No need to check for resize events as it's handled by the canvas
        main_frame.vp_align_ccd.canvas.fitViewToNextImage = True
        # force this view to never follow the tool mode (just standard view)
        main_frame.vp_align_ccd.canvas.allowedModes = set([guimodel.TOOL_NONE])

        # Bind actuator buttons and keys
        self._actuator_controller = ActuatorController(self.tab_data_model,
                                                       main_frame,
                                                       "lens_align_")
        self._actuator_controller.bind_keyboard(main_frame.pnl_tab_secom_align)

        # Toolbar
        tb = main_frame.lens_align_tb
        tb.add_tool(tools.TOOL_DICHO, self.tab_data_model.tool)
        tb.add_tool(tools.TOOL_SPOT, self.tab_data_model.tool)

        # Dicho mode: during this mode, the label & button "move to center" are
        # shown. If the sequence is empty, or a move is going, it's disabled.
        self._ab_move = None # the future of the move (to know if it's over)
        main_frame.lens_align_btn_to_center.Bind(wx.EVT_BUTTON,
                                                 self._on_btn_to_center)

        # Hack warning: Move the scale window from the hidden viewport legend
        # next to the toolbar.
        tb_sizer = tb.GetSizer()
        main_frame.vp_align_sem.legend_panel.scaleDisplay.Reparent(tb)
        tb_sizer.Add(
            main_frame.vp_align_sem.legend_panel.scaleDisplay,
            flag=wx.EXPAND)

        self.tab_data_model.tool.subscribe(self._onTool, init=True)

    def Show(self, show=True):
        Tab.Show(self, show=show)

        # Turn on/off the streams as the tab is displayed.
        # Also directly modify is_active, as there is no stream scheduler
        self._sem_stream.should_update.value = show
        self._sem_stream.is_active.value = show
        self._ccd_stream.should_update.value = show
        self._ccd_stream.is_active.value = show

        # TODO: save and restore SEM state (for now, it does nothing anyway)
        # Turn on (or off) SEM
        # main_data = self.tab_data_model.main
        # state = guimodel.STATE_ON if show else guimodel.STATE_PAUSE
        # main_data.emState.value = state

    def terminate(self):
        super(LensAlignTab, self).terminate()
        # make sure the streams are stopped
        self._sem_stream.is_active.value = False
        self._ccd_stream.is_active.value = False

    @call_after
    def _onTool(self, tool):
        """
        Called when the tool (mode) is changed
        """
        # Reset previous mode
        if tool != guimodel.TOOL_DICHO:
            # reset the sequence
            self.tab_data_model.dicho_seq.value = []
            self.main_frame.lens_align_btn_to_center.Show(False)
            self.main_frame.lens_align_lbl_approc_center.Show(False)

        if tool != guimodel.TOOL_SPOT:
            self._sem_stream.spot.value = False


        # Set new mode
        if tool == guimodel.TOOL_DICHO:
            self.main_frame.lens_align_btn_to_center.Show(True)
            self.main_frame.lens_align_lbl_approc_center.Show(True)
        elif tool == guimodel.TOOL_SPOT:
            self._sem_stream.spot.value = True
            # TODO: until the settings are directly connected to the hardware,
            # we need to disable/freeze the SEM settings in spot mode.

            # TODO: support spot mode and automatically update the survey image each
            # time it's updated.
            # => in spot-mode, listen to stage position and magnification, if it
            # changes reactivate the SEM stream and subscribe to an image, when image
            # is received, stop stream and move back to spot-mode. (need to be careful
            # to handle when the user disables the spot mode during this moment)

    def _onDichoSeq(self, seq):
        roi = conversion.dichotomy_to_region(seq)
        logging.debug("Seq = %s -> roi = %s", seq, roi)
        self._sem_stream.roi.value = roi

        self._update_to_center()

    @call_after
    def _update_to_center(self):
        # Enable a special "move to SEM center" button iif:
        # * seq is not empty
        # * (and) no move currently going on
        seq = self.tab_data_model.dicho_seq.value
        if seq and (self._ab_move is None or self._ab_move.done()):
            roi = self._sem_stream.roi.value
            a, b = self._computeROICenterAB(roi)
            a_txt = units.readable_str(a, unit="m", sig=2)
            b_txt = units.readable_str(b, unit="m", sig=2)
            lbl = "Approximate center away by:\nA = %s, B = %s." % (a_txt, b_txt)
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
        roi = conversion.dichotomy_to_region(seq)
        a, b = self._computeROICenterAB(roi)

        # disable the button to avoid another move
        self.main_frame.lens_align_btn_to_center.Disable()

        # run the move
        move = {"a": a, "b": b}
        aligner = self.tab_data_model.main.aligner
        logging.debug("Moving by %s", move)
        self._ab_move = aligner.moveRel(move)
        self._ab_move.add_done_callback(self._on_move_to_center_done)

    def _on_move_to_center_done(self, future):
        """
        Called when the move to the center is done
        """
        # reset the sequence as it's going to be completely different
        logging.debug("Move over")
        self.tab_data_model.dicho_seq.value = []

    def _computeROICenterAB(self, roi):
        """
        Computes the position of the center of ROI, in the A/B coordinates
        roi (tuple of 4: 0<=float<=1): left, top, right, bottom (in ratio)
        returns (tuple of 2: floats): relative coordinates of center in A/B
        """
        # compute center in X/Y coordinates
        pxs = self.tab_data_model.main.ebeam.pixelSize.value
        eshape = self.tab_data_model.main.ebeam.shape
        fov_size = (eshape[0] * pxs[0], eshape[1] * pxs[1]) # m
        l, t, r, b = roi
        xc, yc = (fov_size[0] * ((l + r) / 2 - 0.5),
                  fov_size[1] * ((t + b) / 2 - 0.5))

        # same formula as InclinedStage._convertPosToChild()
        ang = math.radians(-135)
        ac, bc = [xc * math.cos(ang) - yc * math.sin(ang),
                  xc * math.sin(ang) + yc * math.cos(ang)]

        # Force values to 0 if very close to it (happens often as can be on just
        # on the axis)
        if abs(ac) < 1e-10:
            ac = 0
        if abs(bc) < 1e-10:
            bc = 0

        return ac, bc

    def _onSEMpxs(self, pxs):
        """
        Called when the SEM pixel size changes, which means the FoV changes
        pxs (tuple of 2 floats): in meter
        """
        # in dicho search, it means A, B are actually different values
        self._update_to_center()

        eshape = self.tab_data_model.main.ebeam.shape
        fov_size = (eshape[0] * pxs[0], eshape[1] * pxs[1]) # m
        semv_size = self.main_frame.vp_align_sem.Size # px

        # compute MPP to fit exactly the whole FoV
        mpp = (fov_size[0] / semv_size[0], fov_size[1] / semv_size[1])
        best_mpp = max(mpp) # to fit everything if not same ratio
        self._sem_view.mpp.value = best_mpp


class InclinedStage(model.Actuator):
    """
    Fake stage component (with X/Y axis) that converts two axes and shift them
     by a given angle.
    """
    def __init__(self, name, role, children, axes, angle=0):
        """
        children (dict str -> actuator): name to actuator with 2+ axes
        axes (list of string): names of the axes for x and y
        angle (float in degrees): angle of inclination (counter-clockwise) from
          virtual to physical
        """
        assert len(axes) == 2
        if len(children) != 1:
            raise ValueError("StageIncliner needs 1 child")

        model.Actuator.__init__(self, name, role, axes={"x", "y"})

        self._child = children.values()[0]
        self._axes_child = {"x": axes[0], "y": axes[1]}
        self._angle = angle

        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute(
                                    {"x": 0, "y": 0},
                                    unit="m", readonly=True)
        # it's just a conversion from the child's position
        self._child.position.subscribe(self._updatePosition, init=True)

        # No speed, not needed
        #self.speed = model.MultiSpeedVA(init_speed, [0., 10.], "m/s")

    def _convertPosFromChild(self, pos_child):
        a = math.radians(self._angle)
        xc, yc = pos_child
        pos = [xc * math.cos(a) - yc * math.sin(a),
               xc * math.sin(a) + yc * math.cos(a)]
        return pos

    def _convertPosToChild(self, pos):
        a = math.radians(-self._angle)
        x, y = pos
        posc = [x * math.cos(a) - y * math.sin(a),
                x * math.sin(a) + y * math.cos(a)]
        return posc

    def _updatePosition(self, pos_child):
        """
        update the position VA when the child's position is updated
        """
        # it's read-only, so we change it via _value
        vpos_child = [pos_child[self._axes_child["x"]],
                      pos_child[self._axes_child["y"]]]
        vpos = self._convertPosFromChild(vpos_child)
        self.position._value = {"x": vpos[0],
                                "y": vpos[1]}
        self.position.notify(self.position.value)

    @isasync
    def moveRel(self, shift):

        # shift is a vector, conversion is identical to a point
        vshift = [shift.get("x", 0), shift.get("y", 0)]
        vshift_child = self._convertPosToChild(vshift)

        shift_child = {self._axes_child["x"]: vshift_child[0],
                       self._axes_child["y"]: vshift_child[1]}
        f = self._child.moveRel(shift_child)
        return f

    # For now we don't support moveAbs(), not needed

    def stop(self, axes=None):
        # This is normally never used (child is directly stopped)
        self._child.stop()


class MirrorAlignTab(Tab):
    """
    Tab for the mirror alignment calibration on the Sparc
    """
    # TODO: If this tab is not initially hidden in the XRC file, gtk error
    # will show up when the GUI is launched. Even further (odemis) errors may
    # occur. The reason for this is still unknown.

    def __init__(self, name, button, panel, main_frame, main_data):
        tab_data = guimodel.ActuatorGUIData(main_data)
        super(MirrorAlignTab, self).__init__(name, button, panel,
                                             main_frame, tab_data)


        self._stream_controller = streamcont.StreamController(
                                        self.tab_data_model,
                                        self.main_frame.pnl_sparc_align_streams,
                                        locked=True
                                  )
        self._ccd_stream = None
        # TODO: add on/off button for the CCD and connect the MicroscopeStateController

        # create the stream to the AR image + goal image
        if main_data.ccd:
            # Not ARStream as this is for multiple repetitions, and we just care
            # about what's on the CCD
            ccd_stream = streammod.CameraStream(
                                    "Angular resolved sensor",
                                     main_data.ccd,
                                     main_data.ccd.data,
                                     main_data.ebeam)
            self._ccd_stream = ccd_stream


            # TODO: need to know the mirror center according to the goal image
            # (metadata using pypng?)
            goal_im = pkg_resources.resource_stream(
                            "odemis.gui.img",
                            "calibration/ma_goal_image_5_13_no_lens.png")
            mpp = 13e-6 # m (not used if everything goes fine)
            goal_iim = InstrumentalImage(
                            wx.ImageFromStream(goal_im),
                            mpp,
                            (0, 0))
            goal_stream = streammod.StaticStream("Goal", goal_iim)

            # create a view on the microscope model
            # TODO: A dirty 'trick' to get this to work was adding an empty
            # 'stream_classes' list. Why didn't this viewport have stream
            # classes to begin with?
            vpv = collections.OrderedDict([
                (main_frame.vp_sparc_align,
                 {"name": "Optical",
                  "stream_classes": None,
                  # no stage, or would need a fake stage to control X/Y of the
                  # mirror
                  # no focus, or could control yaw/pitch?
                  }),
                                       ])
            self._view_controller = viewcont.ViewController(
                                        self.tab_data_model,
                                        self.main_frame,
                                        vpv
                                    )
            mic_view = self.tab_data_model.focussedView.value
            mic_view.show_crosshair.value = False    #pylint: disable=E1103
            mic_view.merge_ratio.value = 1           #pylint: disable=E1103

            ccd_spe = self._stream_controller.addStream(ccd_stream)
            ccd_spe.flatten()
            self._stream_controller.addStream(goal_stream, visible=False)
            ccd_stream.should_update.value = True
        else:
            self._view_controller = None
            logging.warning("No CCD available for mirror alignment feedback")

        if main_data.ebeam:
            # SEM, just for the spot mode
            # Not via stream controller, so we can avoid the scheduler
            sem_stream = streammod.SEMStream("SEM", main_data.sed,
                                             main_data.sed.data, main_data.ebeam)
            self._sem_stream = sem_stream
            self._sem_stream.spot.value = True
        else:
            self._sem_stream = None

        self._settings_controller = settings.SparcAlignSettingsController(
                                        self.main_frame,
                                        self.tab_data_model,
                                    )

        self._actuator_controller = ActuatorController(self.tab_data_model,
                                                       main_frame,
                                                       "mirror_align_")

        # Bind keys
        self._actuator_controller.bind_keyboard(main_frame.pnl_tab_sparc_align)

    def Show(self, show=True):
        Tab.Show(self, show=show)

        # Turn on the camera and SEM only when displaying this tab
        if self._ccd_stream:
            self._ccd_stream.is_active.value = show
        if self._sem_stream:
            self._sem_stream.is_active.value = show

    def terminate(self):
        if self._ccd_stream:
            self._ccd_stream.is_active.value = False
        if self._sem_stream:
            self._sem_stream.is_active.value = False

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

        # create all the tabs that fit the microscope role
        self.tab_list = self._filter_tabs(tab_rules, main_frame, main_data)
        if not self.tab_list:
            msg = "No interface known for microscope %s" % main_data.role
            raise LookupError(msg)
        self.switch(0)

        for tab in self.tab_list:
            tab.button.Bind(wx.EVT_BUTTON, self.OnClick)

        # IMPORTANT NOTE:
        #
        # When all tab panels are hidden on start-up, the MinSize attribute
        # of the main GUI frame will be set to such a low value that most of
        # the interface will be invisible if the user takes the interface out of
        # 'full screen' view.
        # Also, Gnome's GDK library will start spewing error messages, saying
        # it cannot draw certain images, because the dimensions are 0x0.
        main_frame.SetMinSize((1400, 550))

    def _filter_tabs(self, tab_defs, main_frame, main_data):
        """
        Filter the tabs according to the role of the microscope, and creates
        the ones needed.

        Tabs that are not wanted or needed will be removed from the list and
        the associated buttons will be hidden in the user interface.
        returns (list of Tabs): all the compatible tabs
        """
        role = main_data.role
        logging.debug("Creating tabs belonging to the '%s' interface",
                      role or "no backend")

        tabs = [] # Tabs
        for troles, tlabels, tname, tclass, tbtn, tpnl in tab_defs:

            if role in troles:
                tab = tclass(tname, tbtn, tpnl, main_frame, main_data)
                tab.set_label(tlabels[troles.index(role)])
                tabs.append(tab)
            else:
                # hide the widgets of the tabs not needed
                logging.debug("Discarding tab %s", tname)

                tbtn.Hide() # this actually removes the tab
                tpnl.Hide()

        return tabs

    def __getitem__(self, name):
        return self._get_tab(name)

    def __setitem__(self, name, tab):
        self.tab_list.append(tab)

    def __delitem__(self, name):
        for tab in self.tab_list:
            if tab.name == name:
                tab.remove(tab)
                break

    def __len__(self):
        return len(self.tab_list)

    def _get_tab(self, tab_name_or_index):
        for i, tab in enumerate(self.tab_list):
            if i == tab_name_or_index or tab.name == tab_name_or_index:
                return tab

        raise LookupError("Tab '{}' not found".format(tab_name_or_index))

    def switch(self, tab_name_or_index):
        try:
            self.main_frame.Freeze()
            for tab in self.tab_list:
                tab.Hide()
        finally:
            self.main_frame.Thaw()
        # It seems there is a bug in wxWidgets which makes the first .Show() not
        # work when the frame is frozen. So always call it after Thaw(). Doesn't
        # seem to cause too much flickering.
        self._get_tab(tab_name_or_index).Show()
        self.main_frame.Layout()

    def terminate(self):
        """
        Terminate each tab (i.e.,indicate they are not used anymore)
        """
        for tab in self.tab_list:
            tab.terminate()

    def OnClick(self, evt):
        # ie, mouse click or space pressed
        logging.debug("Tab button click")

        evt_btn = evt.GetEventObject()
        for tab in self.tab_list:
            if evt_btn == tab.button:
                self.switch(tab.name)
                break
        else:
            logging.warning("Couldn't find the tab associated to the button %s",
                            evt_btn)

        evt.Skip()

