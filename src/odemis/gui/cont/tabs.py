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
from collections import OrderedDict
from odemis import dataio, model
from odemis.gui.cont import settings, tools
from odemis.gui.cont.acquisition import SecomAcquiController, \
    SparcAcquiController
from odemis.gui.cont.actuators import ActuatorController
from odemis.gui.cont.microscope import MicroscopeStateController
from odemis.gui.model.img import InstrumentalImage
from odemis.gui.util import get_picture_folder, formats_to_wildcards
import collections
import logging
import odemis.gui.cont.streams as streamcont
import odemis.gui.cont.views as viewcont
import odemis.gui.model as guimodel
import odemis.gui.model.stream as streammod
import os.path
import pkg_resources
import weakref
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
        super(SecomStreamsTab, self).__init__(name, button, panel)

        self.tab_data_model = guimodel.LiveViewGUIData(main_data)
        self.main_frame = main_frame

        # Various controllers used for the live view and acquisition of images
        self._view_controller = None
        self._settings_controller = None
        self._view_selector = None
        self._acquisition_controller = None
        self._microscope_controller = None

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
        # Note: weakref, so that if a stream is removed, we don't turn it back on
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
        tb.AddTool(tools.TOOL_RO_UPDATE, self.tab_data_model.tool)
        tb.AddTool(tools.TOOL_RO_ZOOM, self.tab_data_model.tool)
        tb.AddTool(tools.TOOL_ZOOM_FIT, self.onZoomFit)

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
            has_bf = any(isinstance(s, streammod.BrightfieldStream) for s in self.tab_data_model.streams)
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
            has_sem = any(isinstance(s, streammod.EM_STREAMS) for s in self.tab_data_model.streams)
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
        self.onOpticalState(main_data.opticalState.value)
        self.onEMState(main_data.emState.value)


class SparcAcquisitionTab(Tab):

    def __init__(self, name, button, panel, main_frame, main_data):
        super(SparcAcquisitionTab, self).__init__(name, button, panel)

        self.tab_data_model = guimodel.ScannedAcquisitionGUIData(main_data)
        self.main_frame = main_frame

        # Various controllers used for the live view and acquisition of images

        self._settings_controller = None
        self._view_controller = None
        self._acquisition_controller = None

        self._roi_streams = [] # stream which must have the same ROI as the SEM CL
        self._prev_rois = {} # stream -> roi (tuple of4 floats)
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

        # TODO: link the Spectrometer/Angle resolved buttons to add/remove the
        # streams. Both from the setting panels, the acquisition view and
        # from ._roi_streams .

        if main_data.spectrometer:
            spec_stream = streammod.SpectrumStream(
                                        "Spectrum",
                                        main_data.spectrometer,
                                        main_data.spectrometer.data,
                                        main_data.ebeam)
            acq_view.addStream(spec_stream)
            self._roi_streams.append(spec_stream)
            spec_stream.roi.subscribe(self.onSpecROI)
            self._spec_stream = spec_stream

        if main_data.ccd:
            ar_stream = streammod.ARStream(
                                "Angular",
                                main_data.ccd,
                                main_data.ccd.data,
                                main_data.ebeam)
            acq_view.addStream(ar_stream)
            self._roi_streams.append(ar_stream)
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

        # FIXME: for now we disable the AR from the acquisition view, because we
        # don't want to always acquire it, so we never acquire it. The good way
        # is to add/remove the stream according to the "instrument" state, in
        # the microscope controller.
        # We always create ar_stream because the setting controller needs to
        # initialise the widgets with it.
        if self._ar_stream:
            self._roi_streams.remove(ar_stream)
            acq_view.removeStream(ar_stream)

        # needs settings_controller
        self._acquisition_controller = SparcAcquiController(
                                            self.main_frame,
                                            self.tab_data_model,
                                            self.settings_controller
                                       )

        # TODO: maybe don't use this: just is_active + direct link of the
        # buttons. At least, don't use the MicroscopeStateController
        # to hide/show the instrument settings
        # Turn on the live SEM stream
        main_data.emState.value = guimodel.STATE_ON
        # and subscribe to activate the live stream accordingly
        # (also needed to ensure at exit, all the streams are unsubscribed)
        # TODO: maybe should be handled by a simple stream controller?
        main_data.emState.subscribe(self.onEMState, init=True)

        # Repetition visualisation

        # Grab the repetition entries, so we can use it to hook extra event
        # handlers to it.
        self.spec_rep = self.settings_controller.spectro_rep_ent
        if self.spec_rep:
            self.spec_rep.va.subscribe(self.on_spec_rep_change)
            self.spec_rep.ctrl.Bind(wx.EVT_SET_FOCUS, self.on_spec_rep_focus)
            self.spec_rep.ctrl.Bind(wx.EVT_KILL_FOCUS, self.on_spec_rep_unfocus)
            self.spec_rep.ctrl.Bind(wx.EVT_ENTER_WINDOW, self.on_spec_rep_enter)
            self.spec_rep.ctrl.Bind(wx.EVT_LEAVE_WINDOW, self.on_spec_rep_leave)

        self.angu_rep = self.settings_controller.angular_rep_ent
        if self.angu_rep:
            self.angu_rep.va.subscribe(self.on_angu_rep_change)
            mic_view.mpp.subscribe(self.on_angu_rep_change)
            self.angu_rep.ctrl.Bind(wx.EVT_SET_FOCUS, self.on_angu_rep_focus)
            self.angu_rep.ctrl.Bind(wx.EVT_KILL_FOCUS, self.on_angu_rep_focus)
            self.angu_rep.ctrl.Bind(wx.EVT_ENTER_WINDOW, self.on_angu_rep_enter)
            self.angu_rep.ctrl.Bind(wx.EVT_LEAVE_WINDOW, self.on_angu_rep_leave)

        # Toolbar

        tb = self.main_frame.sparc_acq_toolbar
        tb.AddTool(tools.TOOL_RO_ACQ, self.tab_data_model.tool)
        tb.AddTool(tools.TOOL_POINT, self.tab_data_model.tool)
        tb.AddTool(tools.TOOL_RO_ZOOM, self.tab_data_model.tool)
        tb.AddTool(tools.TOOL_ZOOM_FIT, self.onZoomFit)

    # Special event handlers for repetition indication in the ROI selection

    # Spectrometer
    def on_spec_rep_change(self, rep):
        self.update_spec_rep()

    def update_spec_rep(self, show=False):
        overlay = self.main_frame.vp_sparc_acq_view.canvas.roi_overlay

        if self.spec_rep.ctrl.HasFocus() or show:
            overlay.set_repetition(self.spec_rep.va.value)
            overlay.grid_fill()
        else:
            overlay.clear_fill()

    def on_spec_rep_focus(self, evt):
        self.update_spec_rep()
        evt.Skip()

    on_spec_rep_unfocus = on_spec_rep_focus

    def on_spec_rep_enter(self, evt):
        self.update_spec_rep(True)
        evt.Skip()

    def on_spec_rep_leave(self, evt):
        if not self.spec_rep.ctrl.HasFocus():
            self.update_spec_rep(False)
        evt.Skip()

    # Angular Resolved
    def on_angu_rep_change(self, rep):
        self.update_angu_rep()

    def update_angu_rep(self, show=False):
        overlay = self.main_frame.vp_sparc_acq_view.canvas.roi_overlay

        if self.angu_rep.ctrl.HasFocus() or show:
            overlay.set_repetition(self.angu_rep.va.value)
            overlay.point_fill()
        else:
            overlay.clear_fill()

    def on_angu_rep_focus(self, evt):
        self.update_angu_rep()
        evt.Skip()

    on_angu_rep_unfocus = on_angu_rep_focus

    def on_angu_rep_enter(self, evt):
        self.update_angu_rep(True)
        evt.Skip()

    def on_angu_rep_leave(self, evt):
        if not self.angu_rep.ctrl.HasFocus():
            self.update_angu_rep(False)
        evt.Skip()


    @property
    def settings_controller(self):
        return self._settings_controller

    def onZoomFit(self, event):
        self._view_controller.fitCurrentViewToContent()

    def onEMState(self, state):
        if state in [guimodel.STATE_OFF, guimodel.STATE_PAUSE]:
            self._sem_live_stream.is_active.value = False
        elif state == guimodel.STATE_ON:
            self._sem_live_stream.is_active.value = True

    def Show(self, show=True):
        Tab.Show(self, show=show)

        # Turn on the SEM stream only when displaying this tab
        if show:
            self.onEMState(self.tab_data_model.main.emState.value)
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

            overlay = self.main_frame.vp_sparc_acq_view.canvas.roi_overlay
            overlay.set_repetition(self.spec_rep.va.value)

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

            overlay = self.main_frame.vp_sparc_acq_view.canvas.roi_overlay
            overlay.set_repetition(self.angu_rep.va.value)

class AnalysisTab(Tab):

    def __init__(self, name, button, panel, main_frame, main_data):
        """
        microscope will be used only to select the type of views
        """
        super(AnalysisTab, self).__init__(name, button, panel)

        # TODO: automatically change the display type based on the acquisition displayed
        self.tab_data_model = guimodel.AnalysisGUIData(main_data)
        self.main_frame = main_frame

        # Various controllers used for the live view and acquisition of images
        self._settings_controller = None
        self._view_controller = None
        self._acquisition_controller = None
        self._stream_controller = None

        # TODO: make sure it works with role=None, microscope=None
        self._view_controller = viewcont.ViewController(
                                    self.tab_data_model,
                                    self.main_frame,
                                    [self.main_frame.vp_inspection_tl,
                                     self.main_frame.vp_inspection_tr,
                                     self.main_frame.vp_inspection_bl,
                                     self.main_frame.vp_inspection_br],
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
        tb = self.main_frame.ana_toolbar
        tb.AddTool(tools.TOOL_RO_ZOOM, self.tab_data_model.tool)
        tb.AddTool(tools.TOOL_POINT, self.tab_data_model.tool)
        tb.AddTool(tools.TOOL_ZOOM_FIT, self.onZoomFit)

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
        except Exception:
            logging.exception("Failed to open file '%s' with format %s", fn, fmt)

        self._display_new_data(fn, data)


    def _display_new_data(self, filename, data):
        """
        Display a new data set (removing all references to the current one)
        filename (string): Name of the file containing the data.
        data (list of model.DataArray): the data to display. Should have at
         least one DataArray.
        """
        fi = guimodel.FileInfo(filename)

        # remove all the previous streams
        self._stream_controller.clear()

        acq_date = fi.metadata.get(model.MD_ACQ_DATE, None)
        # Add each data as a stream of the correct type
        for d in data:
            try:
                im_acq_date = d.metadata[model.MD_ACQ_DATE]
                acq_date = min(acq_date or im_acq_date, im_acq_date)
            except KeyError: # no MD_ACQ_DATE
                pass # => don't update the acq_date

            # TODO: be more clever to detect the type of stream
            if (model.MD_WL_LIST in d.metadata or
                model.MD_WL_POLYNOMIAL in d.metadata or
                (len(d.shape) >= 5 and d.shape[-5] > 1)):
                desc = d.metadata.get(model.MD_DESCRIPTION, "Spectrum")
                self._stream_controller.addStatic(
                                            desc, d,
                                            cls=streammod.StaticSpectrumStream,
                                            add_to_all_views=True)
            elif (model.MD_IN_WL in d.metadata and
                  model.MD_OUT_WL in d.metadata):
                # TODO: handle bright-field (which also has in/out wl)
                desc = d.metadata.get(model.MD_DESCRIPTION, "Filtered colour")
                self._stream_controller.addStatic(
                                            desc, d,
                                            cls=streammod.StaticFluoStream,
                                            add_to_all_views=True)
            else:
                desc = d.metadata.get(
                                    model.MD_DESCRIPTION,
                                    "Secondary electrons")
                self._stream_controller.addStatic(
                                            desc, d,
                                            cls=streammod.StaticSEMStream,
                                            add_to_all_views=True)
        if acq_date:
            fi.metadata[model.MD_ACQ_DATE] = acq_date
        self.tab_data_model.fileinfo.value = fi


class LensAlignTab(Tab):
    """ Tab for the lens alignment on the Secom platform
    """

    def __init__(self, name, button, panel, main_frame, main_data):
        super(LensAlignTab, self).__init__(name, button, panel)

        self.tab_data_model = guimodel.ActuatorGUIData(main_data)
        self.main_frame = main_frame

        self._settings_controller = settings.LensAlignSettingsController(
                                        self.main_frame,
                                        self.tab_data_model
                                    )

        main_frame.vp_align_sem.ShowLegend(False)

        # TODO: vp_align_ccd must be connected to the aligner, with axes
        # conversion.
        # vp_align_sem is connected to the stage
        vpv = collections.OrderedDict([
                (self.main_frame.vp_align_ccd,  # focused view
                 {"name": "Optical",
                  "stage": None, # TODO: a fake stage connected to AB?
                  "focus1": main_data.focus,
                  "stream_classes": (streammod.CameraNoLightStream,),
                  }),
                (self.main_frame.vp_align_sem,
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

        self._stream_controller = streamcont.StreamController(
                                        self.tab_data_model,
                                        self.main_frame.pnl_secom_align_streams,
                                        locked=True
                                  )
        # Allow both streams to be active simultaneously (that's the whole point)
        self._stream_controller.setSchedPolicy(streamcont.SCHED_ALL)
        # TODO: the SEM view should always fit exactly the whole SEM scan area
        # => listen to magnification and update mpp (or call fit-to-screen)?
        # => listen to image update

        # Both streams always have should_update=True excepted if:
        # * the tab is hidden
        # * the corresponding microscope is off
        ss = self._stream_controller.addSEMSED(add_to_all_views=True, visible=False)
        self._sem_stream = ss
        ccd_stream = streammod.CameraNoLightStream("Optical",
                                     main_data.ccd,
                                     main_data.ccd.data,
                                     main_data.light,
                                     fixedpos=True)
        self._ccd_stream = ccd_stream
        self._stream_controller.addStream(ccd_stream, add_to_all_views=True)

        # They take care of immediately stopping the stream for now
        main_data.opticalState.subscribe(self.onOpticalState, init=True)
        main_data.emState.subscribe(self.onEMState, init=True)

        self._state_controller = MicroscopeStateController(
                                            self.tab_data_model,
                                            self.main_frame,
                                            "lens_align_btn_"
                                      )

        # TODO: CCD view must not have image moving depending on MD_POS of stage
        # only during dragging, based on A/B

        self._actuator_controller = ActuatorController(self.tab_data_model,
                                                       main_frame,
                                                       "lens_align_")

        # Bind keys
        self._actuator_controller.bind_keyboard(main_frame.pnl_tab_secom_align)

        # TODO toolbar

        # TODO: support spot mode and automatically update the survey image each
        # time it's updated.
        # => in spot-mode, listen to stage position and magnification, if it
        # changes reactivate the SEM stream and subscribe to an image, when image
        # is received, stop stream and move back to spot-mode. (need to be careful
        # to handle when the user disables the spot mode during this moment)



    def onOpticalState(self, state):
        """ Event handler for when the state of the optical microscope changes
        """
        su = (state == guimodel.STATE_ON) and self.IsShown()
        self._ccd_stream.should_update.value = su

    def onEMState(self, state):
        su = (state == guimodel.STATE_ON) and self.IsShown()
        self._sem_stream.should_update.value = su

    def Show(self, show=True):
        Tab.Show(self, show=show)

        # Force the check for the stream update
        main_data = self.tab_data_model.main
        self.onOpticalState(main_data.opticalState.value)
        self.onEMState(main_data.emState.value)

class MirrorAlignTab(Tab):
    """
    Tab for the mirror alignment calibration on the Sparc
    """
    # TODO: If this tab is not initially hidden in the XRC file, gtk error
    # will show up when the GUI is launched. Even further (odemis) errors may
    # occur. The reason for this is still unknown.

    def __init__(self, name, button, panel, main_frame, main_data):
        super(MirrorAlignTab, self).__init__(name, button, panel)

        self.tab_data_model = guimodel.ActuatorGUIData(main_data)
        self.main_frame = main_frame

        # Very simple, so most controllers are not needed
        self._settings_controller = None
        self._view_controller = None
        self._acquisition_controller = None
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
            self._view_controller = viewcont.ViewController(
                                        self.tab_data_model,
                                        self.main_frame,
                                        [self.main_frame.vp_sparc_align]
                                    )
            mic_view = self.tab_data_model.focussedView.value
            mic_view.show_crosshair.value = False    #pylint: disable=E1103
            mic_view.merge_ratio.value = 1           #pylint: disable=E1103

            # TODO: Do not put goal stream in the stream panel, we don't need
            # any settings.
            # TODO: don't allow to be removed/hidden/paused/folded
            self._stream_controller.addStream(ccd_stream)
            self._stream_controller.addStream(goal_stream, visible=False)
            ccd_stream.should_update.value = True

        else:
            logging.warning("No CCD available for mirror alignment feedback")

        self._settings_controller = settings.SparcAlignSettingsController(
                                        self.main_frame,
                                        self.tab_data_model,
                                    )

        # TODO: need contrast/brightness for the AR stream

        self._actuator_controller = ActuatorController(self.tab_data_model,
                                                       main_frame,
                                                       "mirror_align_")

        # Bind keys
        self._actuator_controller.bind_keyboard(main_frame.pnl_tab_sparc_align)

    def Show(self, show=True):
        Tab.Show(self, show=show)

        # TODO: put the SEM at in spot mode at 0,0

        # Turn on the camera only when displaying this tab
        if show:
            if self._ccd_stream:
                self._ccd_stream.is_active.value = True
        else:
            if self._ccd_stream:
                self._ccd_stream.is_active.value = False

    def terminate(self):
        if self._ccd_stream:
            self._ccd_stream.is_active.value = False

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
        returns (list of Tabs):
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

