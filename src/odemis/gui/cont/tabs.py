#-*- coding: utf-8 -*-

"""
@author: Rinze de Laat

Copyright © 2012 Rinze de Laat, Delmic

Custom (graphical) radio button control.

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 2 of the License, or (at your option) any later
version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

from odemis.gui.cont import settings
from odemis.gui.cont.acquisition import SecomAcquiController, \
    SparcAcquiController
from odemis.gui.cont.microscope import MicroscopeController
from odemis.gui.cont.streams import StreamController
from odemis.gui.cont.views import ViewController, ViewSelector
from odemis.gui.instrmodel import STATE_ON, STATE_OFF, STATE_PAUSE
from odemis.gui.model.stream import SpectrumStream, SEMStream, ARStream, \
    UNDEFINED_ROI
import logging
import wx
from wx.lib.pubsub import pub


class Tab(object):
    """ Small helper class representing a tab (tab button + panel) """

    def __init__(self, group, name, button, panel):
        self.group = group
        self.name = name
        self.button = button
        self.panel = panel
        self.active = True

        self.initialized = False

    def _show(self, show):

        if show and not self.initialized:
            self._initialize()
            self.initialized = True

        self.button.SetToggle(show)
        self.panel.Show(show)

    def show(self):
        self._show(True)

    def hide(self):
        self._show(False)

    def _initialize(self):
        pass

class SecomStreamsTab(Tab):

    def __init__(self, group, name, button, panel, main_frame, interface_model):
        super(SecomStreamsTab, self).__init__(group, name, button, panel)

        self.interface_model = interface_model
        self.main_frame = main_frame

        # Various controllers used for the live view and acquisition of images

        self._settings_controller = None
        self._view_controller = None
        self._view_selector = None
        self._acquisition_controller = None
        self._microscope_controller = None

    def _initialize(self):
        """ This method is called when the tab is first shown """

        if not self.interface_model:
            return

        self._settings_controller = settings.SecomSettingsController(
                                        self.main_frame,
                                        self.interface_model
                                    )

        # Order matters!
        # First we create the views, then the streams
        self._view_controller = ViewController(
                                    self.interface_model,
                                    self.main_frame
                                )

        self._stream_controller = StreamController(
                                        self.interface_model,
                                        self.main_frame.pnl_secom_streams
                                  )

        self._view_selector = ViewSelector(
                                    self.interface_model,
                                    self.main_frame
                              )

        self._acquisition_controller = SecomAcquiController(
                                            self.interface_model,
                                            self.main_frame
                                       )

        self._microscope_controller = MicroscopeController(
                                            self.interface_model,
                                            self.main_frame
                                      )

    @property
    def settings_controller(self):
        return self._settings_controller

    @property
    def stream_controller(self):
        return self._stream_controller

class SparcAcquisitionTab(Tab):

    def __init__(self, group, name, button, panel, main_frame, interface_model):
        super(SparcAcquisitionTab, self).__init__(group, name, button, panel)

        self.microscope_model = interface_model
        self.main_frame = main_frame

        # Various controllers used for the live view and acquisition of images

        self._settings_controller = None
        self._roi_streams = [] # stream which must have the same ROI as the SEM CL

        pub.subscribe(self.on_roi_changed, 'sparc.acq.selection.changed')

    def _initialize(self):
        """ This method is called when the tab is first shown """
        assert self.microscope_model is not None

        acq_view = self.microscope_model.acquisitionView # list of streams for acquisition

        # create the streams
        sem_stream = SEMStream(
                        "SEM live",
                        self.microscope_model.sed,
                        self.microscope_model.sed.data,
                        self.microscope_model.ebeam)
        self._sem_live_stream = sem_stream
        sem_stream.should_update.value = True
        acq_view.addStream(sem_stream) # it should also be saved

        # the SEM acquisition simultaneous to the CCDs
        semcl_stream = SEMStream(
                        "SEM CL", # name matters, used to find the stream for the ROI
                        self.microscope_model.sed,
                        self.microscope_model.sed.data,
                        self.microscope_model.ebeam)
        acq_view.addStream(semcl_stream)
        self._sem_cl_stream = semcl_stream

        if self.microscope_model.spectrometer:
            spec_stream = SpectrumStream(
                                        "Spectrum",
                                        self.microscope_model.spectrometer,
                                        self.microscope_model.spectrometer.data,
                                        self.microscope_model.ebeam)
            acq_view.addStream(spec_stream)
            self._roi_streams.append(spec_stream)

        if self.microscope_model.ccd:
            ar_stream = ARStream(
                                "Angular",
                                self.microscope_model.ccd,
                                self.microscope_model.ccd.data,
                                self.microscope_model.ebeam)
            acq_view.addStream(ar_stream)
            self._roi_streams.append(ar_stream)

        # indicate ROI must still be defined by the user
        #semcl_stream.roi.value = UNDEFINED_ROI
        #semcl_stream.roi.subscribe(self.onROI, init=True)

        # create a view on the microscope model
        # Needs SEM CL stream (could be avoided if we had a .roa on the microscope model)
        self._view_controller = ViewController(
                                    self.microscope_model,
                                    self.main_frame,
                                    [self.main_frame.vp_sparc_acq_view]
                                )
        mic_view = self.microscope_model.focussedView.value
        mic_view.addStream(sem_stream)

        # needs to have the AR and Spectrum streams on the acquisition view 
        self._settings_controller = settings.SparcSettingsController(
                                        self.main_frame,
                                        self.microscope_model,
                                    )

        self._acquisition_controller = SparcAcquiController(
                                            self.main_frame,
                                            self.microscope_model
                                       )

        # FIXME: for now we disable the AR from the acquisition view, because we
        # don't want to always acquire it, so we never acquire it. The good way
        # is to add/remove the stream according to the "instrument" state, in the
        # microscope controller
        acq_view.removeStream(ar_stream)

        # Turn on the live SEM stream
        self.microscope_model.emState.value = STATE_ON
        # and subscribe to activate the live stream accordingly
        # (especially needed to ensure at exit, all the streams are unsubscribed)
        # TODO: maybe should be handled by a simple stream controller?
        self.microscope_model.emState.subscribe(self.onEMState, init=True)

    @property
    def settings_controller(self):
        return self._settings_controller

    def onEMState(self, state):
        if state == STATE_OFF or state == STATE_PAUSE:
            self._sem_live_stream.is_active.value = False
        elif state == STATE_ON:
            self._sem_live_stream.is_active.value = True

    def on_roi_changed(self, real_selection):

        roi = UNDEFINED_ROI

        if real_selection:
            stream_image = self._sem_live_stream.image.value
            roi = stream_image.real_selection_to_unit(*real_selection)
            self._sem_cl_stream.roi.value = roi
            for s in self._roi_streams:
                s.roi.value = roi


class TabBarController(object):

    def __init__(self, tab_list, main_frame, interface_model):

        self.main_frame = main_frame

        self.tab_list = tab_list

        self.hide_all()

        if interface_model:
            self._filter_tabs(interface_model)

        for tab in self.tab_list:
            tab.button.Bind(wx.EVT_BUTTON, self.OnClick)
            tab.button.Bind(wx.EVT_KEY_UP, self.OnKeyUp)

        self.show(0)

        # IMPORTANT NOTE:
        #
        # When all tab panels are hidden on start-up, the MinSize attribute
        # of the main GUI frame will be set to such a low value, that most of
        # the interface will be invisible if the user takes the interface off of
        # 'full screen' view.
        # Also, Gnome's GDK library will start spewing error messages, saying
        # it cannot draw certain images, because the dimensions are 0x0.

        main_frame.SetMinSize((1400, 550))

    def _filter_tabs(self, interface_model):
        """ Filter the tabs according to the current interface model.

        Tabs that are not wanted or needed will be removed from the list and
        the associated buttons will be hidden in the user interface.
        """

        needed_group = interface_model.microscope.role

        logging.debug("Hiding tabs not belonging to the '%s' interface",
                      needed_group)

        for tab in [tab for tab in self.tab_list if tab.group != needed_group]:
            tab.button.Hide()
            self.tab_list.remove(tab)


    def __getitem__(self, name):
        return self.get_tab(name)

    def get_tab(self, tab_name_or_index):
        for i, tab in enumerate(self.tab_list):
            if i == tab_name_or_index or tab.name == tab_name_or_index:
                return tab

        raise LookupError

    def show(self, tab_name_or_index):
        for i, tab in enumerate(self.tab_list):
            if i == tab_name_or_index or tab.name == tab_name_or_index:
                tab.show()
            else:
                tab.hide()

    def hide_all(self):
        for tab in self.tab_list:
            tab.hide()

    def OnKeyUp(self, evt):
        evt_btn = evt.GetEventObject()

        if evt_btn.hasFocus and evt.GetKeyCode() == ord(" "):
            self.hide_all()
            self.main_frame.Freeze()

            for tab in self.tab_list:
                if evt_btn == tab.button:
                    tab.show()
                else:
                    tab.hide()

            self.main_frame.Layout()
            self.main_frame.Thaw()

    def OnClick(self, evt):
        logging.debug("Tab button click")

        evt_btn = evt.GetEventObject()

        self.hide_all()

        self.main_frame.Freeze()

        for tab in self.tab_list:
            if evt_btn == tab.button:
                tab.show()
            else:
                tab.hide()

        self.main_frame.Layout()
        self.main_frame.Thaw()

        #if not btn.GetToggle():
        evt.Skip()
