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
import logging
import numpy
import wx
# IMPORTANT: wx.html needs to be imported for the HTMLWindow defined in the XRC
# file to be correctly identified. See: http://trac.wxwidgets.org/ticket/3626
# This is not related to any particular wxPython version and is most likely permanent.
import wx.html

from odemis import model
import odemis.acq.stream as acqstream
import odemis.gui.cont.streams as streamcont
import odemis.gui.cont.views as viewcont
import odemis.gui.model as guimod
from odemis.gui.cont import settings
from odemis.gui.cont.actuators import ActuatorController
from odemis.gui.cont.tabs.tab import Tab
from odemis.gui.util import call_in_wx_main
from odemis.util import units


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
        self.set_label("ALIGNMENT")

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
        self._ccd_stream = None
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
            mic_view.interpolate_content.value = False
            mic_view.show_crosshair.value = False
            mic_view.show_pixelvalue.value = False
            mic_view.merge_ratio.value = 1

            ccd_spe = self._stream_controller.addStream(ccd_stream)
            ccd_spe.stream_panel.flatten()
            ccd_stream.should_update.value = True

            # Connect polePosition of lens to mirror overlay (via the polePositionPhysical VA)
            mirror_ol = self.panel.vp_sparc_align.mirror_ol
            lens = main_data.lens
            try:
                # The lens is not set, but the CCD metadata is still set as-is.
                # So need to compensate for the magnification, and flip.
                # (That's also the reason it's not possible to move the
                # pole position, as it should be done with the lens)
                # Note: up to v2.2 we were using precomputed png files for each
                # CCD size. Some of them contained (small) error in the mirror size,
                # which will make this new display look a bit bigger.
                m = lens.magnification.value
                mirror_ol.set_mirror_dimensions(lens.parabolaF.value / m,
                                                lens.xMax.value / m,
                                                -lens.focusDistance.value / m,
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
        for btn, mode in list(self._alignbtn_to_mode.items()):
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
        elif mode == "fiber-align":
            if self._ccd_stream:
                self._ccd_stream.should_update.value = False
            # Note: we still allow mirror translation, because on some SPARCs
            # with small mirrors, it's handy to align the mirror using the
            # spectrometer feedback.
            self.panel.pnl_sparc_trans.Enable(True)
            self.panel.pnl_fibaligner.Enable(True)
        else:
            raise ValueError("Unknown alignment mode %s." % mode)

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
            dates = scount.metadata[model.MD_TIME_LIST]
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

#     def _getGoalImage(self, main_data):
#         """
#         main_data (model.MainGUIData)
#         returns (model.DataArray): RGBA DataArray of the goal image for the
#           current hardware
#         """
#         ccd = main_data.ccd
#         lens = main_data.lens
#
#         # TODO: automatically generate the image? Shouldn't be too hard with
#         # cairo, it's just 3 circles and a line.
#
#         # The goal image depends on the physical size of the CCD, so we have
#         # a file for each supported sensor size.
#         pxs = ccd.pixelSize.value
#         ccd_res = ccd.shape[0:2]
#         ccd_sz = tuple(int(round(p * l * 1e6)) for p, l in zip(pxs, ccd_res))
#         try:
#             goal_rs = pkg_resources.resource_stream("odemis.gui.img",
#                                                     "calibration/ma_goal_5_13_sensor_%d_%d.png" % ccd_sz)
#         except IOError:
#             logging.warning(u"Failed to find a fitting goal image for sensor "
#                             u"of %dx%d µm" % ccd_sz)
#             # pick a known file, it's better than nothing
#             goal_rs = pkg_resources.resource_stream("odemis.gui.img",
#                                                     "calibration/ma_goal_5_13_sensor_13312_13312.png")
#         goal_im = model.DataArray(scipy.misc.imread(goal_rs))
#         # No need to swap bytes for goal_im. Alpha needs to be fixed though
#         goal_im = scale_to_alpha(goal_im)
#         # It should be displayed at the same scale as the actual image.
#         # In theory, it would be direct, but as the backend doesn't know when
#         # the lens is on or not, it's considered always on, and so the optical
#         # image get the pixel size multiplied by the magnification.
#
#         # The resolution is the same as the maximum sensor resolution, if not,
#         # we adapt the pixel size
#         im_res = (goal_im.shape[1], goal_im.shape[0])  #pylint: disable=E1101,E1103
#         scale = ccd_res[0] / im_res[0]
#         if scale != 1:
#             logging.warning("Goal image has resolution %s while CCD has %s",
#                             im_res, ccd_res)
#
#         # Pxs = sensor pxs / lens mag
#         mag = lens.magnification.value
#         goal_md = {model.MD_PIXEL_SIZE: (scale * pxs[0] / mag, scale * pxs[1] / mag),  # m
#                    model.MD_POS: (0, 0),
#                    model.MD_DIMS: "YXC", }
#
#         goal_im.metadata = goal_md
#         return goal_im

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

    @classmethod
    def get_display_priority(cls, main_data):
        # For SPARCv1, (with no "parkable" mirror)
        if main_data.role == "sparc":
            mirror = main_data.mirror
            if mirror and set(mirror.axes.keys()) == {"l", "s"}:
                return None # => will use the Sparc2AlignTab
            else:
                return 5

        return None
