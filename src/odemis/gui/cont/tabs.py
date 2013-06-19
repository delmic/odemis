#-*- coding: utf-8 -*-

"""
@author: Rinze de Laat

Copyright © 2012 Rinze de Laat, Delmic

Custom (graphical) radio button control.

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

from __future__ import division
from collections import OrderedDict, namedtuple
from odemis.gui import instrmodel
from odemis.gui.cont import settings
from odemis.gui.cont.acquisition import SecomAcquiController, \
    SparcAcquiController
from odemis.gui.cont.microscope import MicroscopeController
from odemis.gui.cont.streams import StreamController
from odemis.gui.cont.views import ViewController, ViewSelector
from odemis.gui.instrmodel import STATE_ON, STATE_OFF, STATE_PAUSE
from odemis.gui.model.img import InstrumentalImage
from odemis.gui.model.stream import SpectrumStream, SEMStream, ARStream, \
    UNDEFINED_ROI, StaticStream, CameraStream
from odemis.gui.util import widgets
import logging
import pkg_resources
import wx




class Tab(object):
    """ Small helper class representing a tab (tab button + panel) """

    def __init__(self, name, button, panel, label=None):
        self.name = name
        self.label = label
        self.button = button
        self.panel = panel

    def Show(self, show=True):
        self.button.SetToggle(show)
        self.panel.Show(show)

    def Hide(self):
        self.Show(False)

    def set_label(self, label):
        self.button.SetLabel(label)

    def get_label(self):
        return self.button.GetLabel()

    def _initialize(self):
        pass

class SecomStreamsTab(Tab):

    def __init__(self, name, button, panel, main_frame, microscope):
        super(SecomStreamsTab, self).__init__(name, button, panel)

        self.interface_model = instrmodel.LiveGUIModel(microscope)
        self.main_frame = main_frame

        # Various controllers used for the live view and acquisition of images

        self._settings_controller = None
        self._view_controller = None
        self._view_selector = None
        self._acquisition_controller = None
        self._microscope_controller = None

        self._settings_controller = settings.SecomSettingsController(
                                        self.main_frame,
                                        self.interface_model
                                    )

        # Order matters!
        # First we create the views, then the streams
        self._view_controller = ViewController(
                                    self.interface_model,
                                    self.main_frame,
                                    [self.main_frame.vp_secom_tl,
                                     self.main_frame.vp_secom_tr,
                                     self.main_frame.vp_secom_bl,
                                     self.main_frame.vp_secom_br]
                                )

        self._stream_controller = StreamController(
                                        self.interface_model,
                                        self.main_frame.pnl_secom_streams
                                  )
        # btn -> (viewport, label)
        ViewportLabel = namedtuple('ViewportLabel', ['vp', 'lbl'])

        buttons = OrderedDict(
            [
                (self.main_frame.btn_secom_view_all,
                    ViewportLabel(
                        None,
                        self.main_frame.lbl_secom_view_all)),
                (self.main_frame.btn_secom_view_tl,
                        ViewportLabel(
                            self.main_frame.vp_secom_tl,
                            self.main_frame.lbl_secom_view_tl)),
                (self.main_frame.btn_secom_view_tr,
                        ViewportLabel(
                            self.main_frame.vp_secom_tr,
                            self.main_frame.lbl_secom_view_tr)),
                (self.main_frame.btn_secom_view_bl,
                        ViewportLabel(
                            self.main_frame.vp_secom_bl,
                            self.main_frame.lbl_secom_view_bl)),
                (self.main_frame.btn_secom_view_br,
                        ViewportLabel(
                            self.main_frame.vp_secom_br,
                            self.main_frame.lbl_secom_view_br))
            ]
        )

        self._view_selector = ViewSelector(
                                    self.interface_model,
                                    self.main_frame,
                                    buttons
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

    def __init__(self, name, button, panel, main_frame, microscope):
        super(SparcAcquisitionTab, self).__init__(name, button, panel)

        self.interface_model = instrmodel.AcquisitionGUIModel(microscope)
        self.main_frame = main_frame

        # Various controllers used for the live view and acquisition of images

        self._settings_controller = None
        self._view_controller = None
        self._acquisition_controller = None

        self._roi_streams = [] # stream which must have the same ROI as the SEM CL
        self._prev_rois = {} # stream -> roi (tuple of4 floats)
        self._spec_stream = None
        self._ar_stream = None

        acq_view = self.interface_model.acquisitionView # list of streams for acquisition

        # create the streams
        sem_stream = SEMStream(
                        "SEM live",
                        self.interface_model.sed,
                        self.interface_model.sed.data,
                        self.interface_model.ebeam)
        self._sem_live_stream = sem_stream
        sem_stream.should_update.value = True
        acq_view.addStream(sem_stream) # it should also be saved

        # the SEM acquisition simultaneous to the CCDs
        semcl_stream = SEMStream(
                        "SEM CL", # name matters, used to find the stream for the ROI
                        self.interface_model.sed,
                        self.interface_model.sed.data,
                        self.interface_model.ebeam)
        acq_view.addStream(semcl_stream)
        self._sem_cl_stream = semcl_stream

        # TODO: link the Spectrometer/Angle resolved buttons to add/remove the
        # streams. Both from the setting panels, the acquisition view and
        # from ._roi_streams .

        if self.interface_model.spectrometer:
            spec_stream = SpectrumStream(
                                        "Spectrum",
                                        self.interface_model.spectrometer,
                                        self.interface_model.spectrometer.data,
                                        self.interface_model.ebeam)
            acq_view.addStream(spec_stream)
            self._roi_streams.append(spec_stream)
            spec_stream.roi.subscribe(self.onSpecROI)
            self._spec_stream = spec_stream

        if self.interface_model.ccd:
            ar_stream = ARStream(
                                "Angular",
                                self.interface_model.ccd,
                                self.interface_model.ccd.data,
                                self.interface_model.ebeam)
            acq_view.addStream(ar_stream)
            self._roi_streams.append(ar_stream)
            ar_stream.roi.subscribe(self.onARROI)
            self._ar_stream = ar_stream

        # indicate ROI must still be defined by the user
        semcl_stream.roi.value = UNDEFINED_ROI
        semcl_stream.roi.subscribe(self.onROI, init=True)

        # create a view on the microscope model
        # Needs SEM CL stream (could be avoided if we had a .roa on the microscope model)
        self._view_controller = ViewController(
                                    self.interface_model,
                                    self.main_frame,
                                    [self.main_frame.vp_sparc_acq_view]
                                )
        mic_view = self.interface_model.focussedView.value
        mic_view.addStream(sem_stream)

        # needs to have the AR and Spectrum streams on the acquisition view
        self._settings_controller = settings.SparcSettingsController(
                                        self.main_frame,
                                        self.interface_model,
                                    )

        # FIXME: for now we disable the AR from the acquisition view, because we
        # don't want to always acquire it, so we never acquire it. The good way
        # is to add/remove the stream according to the "instrument" state, in the
        # microscope controller.
        # We always create ar_stream because the setting controller needs to
        # initialise the widgets with it.
        if self._ar_stream:
            self._roi_streams.remove(ar_stream)
            acq_view.removeStream(ar_stream)

        # needs settings_controller
        self._acquisition_controller = SparcAcquiController(
                                            self.main_frame,
                                            self.interface_model,
                                            self.settings_controller
                                       )

        # Turn on the live SEM stream
        self.interface_model.emState.value = STATE_ON
        # and subscribe to activate the live stream accordingly
        # (especially needed to ensure at exit, all the streams are unsubscribed)
        # TODO: maybe should be handled by a simple stream controller?
        self.interface_model.emState.subscribe(self.onEMState, init=True)

    @property
    def settings_controller(self):
        return self._settings_controller

    def onEMState(self, state):
        if state == STATE_OFF or state == STATE_PAUSE:
            self._sem_live_stream.is_active.value = False
        elif state == STATE_ON:
            self._sem_live_stream.is_active.value = True

    def onROI(self, roi):
        """
        called when the SEM CL roi (region of acquisition) is changed
        """
        # Updating the ROI requires a bit of care, because the streams might
        # update back their ROI with a modified value. It should normally
        # converge, but we must absolutely ensure it will never cause infinite
        # loops.
        for s in self._roi_streams:
            s.roi.value = roi

    def onSpecROI(self, roi):
        """
        called when the Spectrometer roi is changed
        """
        # if only one stream => copy to ROI, otherwise leave it as is
        if len(self._roi_streams) == 1 and self._spec_stream in self._roi_streams:
            # unsubscribe to be sure it won't call us back directly
            self._sem_cl_stream.roi.unsubscribe(self.onROI)
            self._sem_cl_stream.roi.value = roi
            self._sem_cl_stream.roi.subscribe(self.onROI)

    def onARROI(self, roi):
        """
        called when the Angle resolved roi is changed
        """
        # if only one stream => copy to ROI, otherwise leave it as is
        if len(self._roi_streams) == 1 and self._ar_stream in self._roi_streams:
            # unsubscribe to be sure it won't call us back directly
            self._sem_cl_stream.roi.unsubscribe(self.onROI)
            self._sem_cl_stream.roi.value = roi
            self._sem_cl_stream.roi.subscribe(self.onROI)

class AnalysisTab(Tab):

    def __init__(self, name, button, panel, main_frame, microscope=None):
        """
        microscope will be used only to select the type of views
        """
        super(AnalysisTab, self).__init__(name, button, panel)

        # Doesn't need a microscope
        if microscope:
            role = microscope.role
        else:
            role = None
        self.interface_model = instrmodel.AnalysisGUIModel(role=role)
        self.main_frame = main_frame

        # Various controllers used for the live view and acquisition of images
        self._settings_controller = None
        self._view_controller = None
        self._acquisition_controller = None
        self._stream_controller = None

        self._view_controller = ViewController(
                                    self.interface_model,
                                    self.main_frame,
                                    [self.main_frame.vp_inspection_tl,
                                     self.main_frame.vp_inspection_tr,
                                     self.main_frame.vp_inspection_bl,
                                     self.main_frame.vp_inspection_br],
                                )

        self._stream_controller = StreamController(
                                        self.interface_model,
                                        self.main_frame.pnl_sparc_streams
                                  )


        self._settings_controller = settings.AnalysisSettingsController(
                                        self.main_frame,
                                        self.interface_model
                                    )

        # btn -> (viewport, label)
        ViewportLabel = namedtuple('ViewportLabel', ['vp', 'lbl'])

        buttons = {
            self.main_frame.btn_sparc_view_all:
                ViewportLabel(None, self.main_frame.lbl_sparc_view_all),
            self.main_frame.btn_sparc_view_tl:
                ViewportLabel(
                    self.main_frame.vp_inspection_tl,
                    self.main_frame.lbl_sparc_view_tl),
            self.main_frame.btn_sparc_view_tr:
                ViewportLabel(
                    self.main_frame.vp_inspection_tr,
                    self.main_frame.lbl_sparc_view_tr),
            self.main_frame.btn_sparc_view_bl:
                ViewportLabel(
                    self.main_frame.vp_inspection_bl,
                    self.main_frame.lbl_sparc_view_bl),
            self.main_frame.btn_sparc_view_br:
                ViewportLabel(
                    self.main_frame.vp_inspection_br,
                    self.main_frame.lbl_sparc_view_br)}

        self._view_selector = ViewSelector(
                                    self.interface_model,
                                    self.main_frame,
                                    buttons
                              )


    @property
    def stream_controller(self):
        return self._stream_controller

class MirrorAlignTab(Tab):
    """
    Tab for the mirror alignment calibration on the Sparc
    """
    # TODO: If this tab is not initially hidden in the XRC file, gtk error
    # will show up when the GUI is launched. Even further (odemis) errors may
    # occur. The reason for this is still unknown.

    def __init__(self, name, button, panel, main_frame, microscope=None):
        super(MirrorAlignTab, self).__init__(name, button, panel)

        self.interface_model = instrmodel.ActuatorGUIModel(microscope)
        self.main_frame = main_frame

        # Various controllers used for the live view and acquisition of images
        self._settings_controller = None
        self._view_controller = None
        self._acquisition_controller = None
        self._stream_controller = None
        self._ccd_stream = None

        # TODO add setting and view controller (add variable vp_sparc_align)
        # create the stream to the AR image + goal image

        if self.interface_model.ccd:
            # Not ARStream as this is for multiple repetitions, and we just care
            # on the direct display (without even
            ccd_stream = CameraStream("Angular",
                                 self.interface_model.ccd,
                                 self.interface_model.ccd.data,
                                 self.interface_model.ebeam)
            self._ccd_stream = ccd_stream

            goal_im = pkg_resources.resource_stream("odemis.gui.img",
                                                    "ma_goal_image_5_13.png")
            mpp = 13e-6 # m
            # TODO: how to ensure ar_stream is the same mpp?
            #  * Force in the viewport?
            #  * Force mpp in ARStream?
            #  * duplicate from ar_stream?
            goal_iim = InstrumentalImage(wx.ImageFromStream(goal_im), mpp, (0, 0))
            goal_stream = StaticStream("Goal", goal_iim)
            # create a view on the microscope model
            self._view_controller = ViewController(
                                        self.interface_model,
                                        self.main_frame,
                                        [self.main_frame.vp_sparc_align]
                                    )
            mic_view = self.interface_model.focussedView.value
            mic_view.addStream(ccd_stream)
            mic_view.addStream(goal_stream)
            mic_view.show_crosshair.value = False
            mic_view.merge_ratio.value = 1
            ccd_stream.should_update.value = True
        else:
            logging.warning("No CCD available for mirror alignment feedback")

        # TODO: needs to have the AR streams on the acquisition view
        self._settings_controller = settings.SparcAlignSettingsController(
                                        self.main_frame,
                                        self.interface_model,
                                    )

        # TODO: need contrast/brightness for the AR stream

        # TODO: Should go to a new controller "actuator controller"?
        # Bind sizesteps
        self.va_connectors = []
        for an, ss in self.interface_model.stepsizes.items():
            slider_name = "slider_" + an
            try:
                slider = getattr(self.main_frame, slider_name)
            except AttributeError:
                logging.exception("No slider in GUI found for stepsize %s", an)
                continue

            slider.SetRange(*ss.range)

            vac = widgets.VigilantAttributeConnector(ss, slider,
                                                     events=wx.EVT_SLIDER)
            self.va_connectors.append(vac)

        # Bind buttons
        for axis in self.interface_model.axes:
            for suffix, factor in [("m", -1), ("p", 1)]:
                # something like "btn_align_pry"
                btn_name = "btn_align_" + suffix + axis
                try:
                    btn = getattr(self.main_frame, btn_name)
                except AttributeError:
                    logging.exception("No button in GUI found for axis %s", axis)
                    continue

                def btn_action(evt, axis=axis, factor=factor):
                    self.interface_model.step(axis, factor)

                btn.Bind(wx.EVT_BUTTON, btn_action)

        # Keybinding
        self.main_frame.pnl_tab_sparc_align.Bind(wx.EVT_KEY_DOWN, self.on_key)

    # TODO: should be one per microscope role or axes names??
    # WXK -> (args for interface_model.step)
    key_bindings_secom = {
                    wx.WXK_LEFT: ("x", -1),
                    wx.WXK_RIGHT: ("x", 1),
                    wx.WXK_DOWN: ("y", -1),
                    wx.WXK_UP: ("y", 1),
                    wx.WXK_PAGEDOWN: ("z", -1),
                    wx.WXK_PAGEUP: ("z", 1),
                    wx.WXK_NUMPAD_LEFT: ("r", -1),
                    wx.WXK_NUMPAD_RIGHT: ("r", 1),
                    wx.WXK_NUMPAD_DOWN: ("l", -1),
                    wx.WXK_NUMPAD_UP: ("l", 1),
                    # same but with NumLock
                    wx.WXK_NUMPAD4: ("r", -1),
                    wx.WXK_NUMPAD6: ("r", 1),
                    wx.WXK_NUMPAD2: ("l", -1),
                    wx.WXK_NUMPAD8: ("l", 1),
                    }
    key_bindings_sparc = {
                    wx.WXK_LEFT: ("x", -1),
                    wx.WXK_RIGHT: ("x", 1),
                    wx.WXK_DOWN: ("y", -1),
                    wx.WXK_UP: ("y", 1),
#                    wx.WXK_PAGEDOWN: ("z", -1),
#                    wx.WXK_PAGEUP: ("z", 1),
                    wx.WXK_NUMPAD_LEFT: ("rz", -1),
                    wx.WXK_NUMPAD_RIGHT: ("rz", 1),
                    wx.WXK_NUMPAD_DOWN: ("ry", -1),
                    wx.WXK_NUMPAD_UP: ("ry", 1),
                    # same but with NumLock
                    wx.WXK_NUMPAD4: ("rz", -1),
                    wx.WXK_NUMPAD6: ("rz", 1),
                    wx.WXK_NUMPAD2: ("ry", -1),
                    wx.WXK_NUMPAD8: ("ry", 1),
                    }

    def on_key(self, event):
        key = event.GetKeyCode()
        if key in self.key_bindings_sparc:
            self.interface_model.step(*self.key_bindings_sparc[key])
        else:
            # everything else we don't process
            event.Skip()

    def Show(self, show=True):
        Tab.Show(self, show=show)

        # TODO: put the SEM at 0,0... or let the user pick a point

        # Turn on the camera only when displaying this tab
        if show:
            if self._ccd_stream:
                self._ccd_stream.is_active.value = True
        else:
            if self._ccd_stream:
                self._ccd_stream.is_active.value = False

class TabBarController(object):

    def __init__(self, tab_rules, main_frame, microscope):
        """
        tab_rules (list of 5-tuples (string, string, Tab class, button, panel): list
          of all the possible tabs. Each tuple is:
          microscope role(s) (string or tuple of strings/None), internal name,
          class, tab btn, tab panel. If role is None, it will match when there
          is no microscope (microscope is None). TODO: support "*" for matching
          anything?
        """
        self.main_frame = main_frame

        # create all the tabs that fit the microscope role
        self.tab_list = self._filter_tabs(tab_rules, main_frame, microscope)
        if not self.tab_list:
            raise LookupError("No interface known for microscope %s" % microscope.role)
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

    def _filter_tabs(self, tab_defs, main_frame, microscope):
        """
        Filter the tabs according to the role of the microscope, and creates
        the ones needed.

        Tabs that are not wanted or needed will be removed from the list and
        the associated buttons will be hidden in the user interface.
        returns (list of Tabs):
        """
        if microscope:
            role = microscope.role
        else:
            role = None
        logging.debug("Creating tabs belonging to the '%s' interface",
                      role or "no backend")

        tabs = [] # Tabs
        for troles, tlabels, tname, tclass, tbtn, tpnl in tab_defs:

            if role in troles:
                tab = tclass(tname, tbtn, tpnl, main_frame, microscope)
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

