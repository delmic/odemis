# -*- coding: utf-8 -*-
"""
Created on 8 Feb 2012

:author: Éric Piel
:copyright: © 2012-2015 Éric Piel, Delmic

.. license::

    This file is part of Odemis.

    Odemis is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License version 2 as published by the Free
    Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
    FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
    details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""

from __future__ import division

from abc import abstractmethod, ABCMeta
from concurrent.futures._base import CancelledError
from functools import partial
import logging
import math
from odemis import gui, model, util
from odemis.acq.stream import EMStream, SpectrumStream, \
    StaticStream, CLStream, FluoStream, \
    StaticFluoStream, Stream, DataProjection, POL_POSITIONS
from odemis.gui import BG_COLOUR_LEGEND, FG_COLOUR_LEGEND
from odemis.gui.comp import miccanvas, overlay
from odemis.gui.comp.canvas import CAN_DRAG, CAN_FOCUS
from odemis.gui.comp.legend import InfoLegend, AxisLegend, RadioLegend
from odemis.gui.img import getBitmap
from odemis.gui.model import CHAMBER_VACUUM, CHAMBER_UNKNOWN
from odemis.gui.util import call_in_wx_main
from odemis.gui.util.raster import rasterize_line
from odemis.model import NotApplicableError
from odemis.util import no_conflict
from odemis.util import units, spectrum, peak
import wx
import time


def get_original_stream(s_or_p):
    """
    To support Streams which provide projection on their own, and Streams which
    rely on a separate projection, find the original Stream.
    s_or_p (Stream or DataProjection or None)
    return:
       stream (Stream or None): the corresponding stream
    """
    if isinstance(s_or_p, DataProjection):
        # Projection: easy, the stream is p.stream
        return s_or_p.stream

    if isinstance(s_or_p, Stream):
        # Stream: then it's supposed to provide a projection by its own
        if not hasattr(s_or_p, "image"):
            # We could raise an Exception, but let's see, maybe the caller will
            # not care anyway
            logging.warning("Got a non-projectable stream to be projected: %s", s_or_p)
        return s_or_p

    if s_or_p is None:
        return None

    raise ValueError("%s is not a Stream nor a DataProjection" % (s_or_p,))


class ViewPort(wx.Panel):

    # Default classes for the canvas and the legend. These may be overridden
    # in subclasses
    canvas_class = None
    bottom_legend_class = None
    left_legend_class = None

    def __init__(self, *args, **kwargs):
        """Note: The MicroscopeViewport is not fully initialised until setView()
        has been called.
        """

        wx.Panel.__init__(self, *args, **kwargs)

        self._view = None  # model.MicroscopeView
        self._tab_data_model = None  # model.MicroscopyGUIData

        # Keep track of this panel's pseudo focus
        self._has_focus = False

        font = wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        self.SetFont(font)
        self.SetBackgroundColour(BG_COLOUR_LEGEND)
        self.SetForegroundColour(FG_COLOUR_LEGEND)

        # This attribute can be used to track the (GridBag) sizer position of the viewport (if any)
        self.sizer_pos = None

        # main widget
        self.canvas = self.canvas_class(self)

        # Put all together (canvas + legend)
        self.bottom_legend = None
        self.left_legend = None

        main_sizer = wx.BoxSizer(wx.VERTICAL)
        if (
                self.bottom_legend_class and self.left_legend_class
        ):
            self.bottom_legend = self.bottom_legend_class(self)
            self.left_legend = self.left_legend_class(self, orientation=wx.VERTICAL)

            grid_sizer = wx.GridBagSizer()
            grid_sizer.Add(self.canvas, pos=(0, 1), flag=wx.EXPAND)
            grid_sizer.Add(self.bottom_legend, pos=(1, 1), flag=wx.EXPAND)
            grid_sizer.Add(self.left_legend, pos=(0, 0), flag=wx.EXPAND)

            filler = wx.Panel(self)
            filler.SetBackgroundColour(BG_COLOUR_LEGEND)
            grid_sizer.Add(filler, pos=(1, 0), flag=wx.EXPAND)

            grid_sizer.AddGrowableRow(0, 1)
            grid_sizer.AddGrowableCol(1, 1)
            # grid_sizer.RemoveGrowableCol(0)

            # Focus the view when a child element is clicked
            self.bottom_legend.Bind(wx.EVT_LEFT_DOWN, self.OnChildFocus)
            self.left_legend.Bind(wx.EVT_LEFT_DOWN, self.OnChildFocus)

            main_sizer.Add(grid_sizer, 1, border=2, flag=wx.EXPAND | wx.ALL)
        elif self.bottom_legend_class:
            main_sizer.Add(self.canvas, proportion=1, border=2,
                           flag=wx.EXPAND | wx.TOP | wx.LEFT | wx.RIGHT)
            # It's made of multiple controls positioned via sizers
            # TODO: allow the user to pick which information is displayed
            # in the legend
            self.bottom_legend = self.bottom_legend_class(self)
            self.bottom_legend.Bind(wx.EVT_LEFT_DOWN, self.OnChildFocus)

            main_sizer.Add(self.bottom_legend, proportion=0, border=2,
                           flag=wx.EXPAND | wx.BOTTOM | wx.LEFT | wx.RIGHT)
        elif self.left_legend_class:
            raise NotImplementedError("Only left legend not handled")
        else:
            main_sizer.Add(self.canvas, 1, border=2, flag=wx.EXPAND | wx.ALL)

        self.SetSizerAndFit(main_sizer)
        main_sizer.Fit(self)
        self.SetAutoLayout(True)

        self.Bind(wx.EVT_CHILD_FOCUS, self.OnChildFocus)
        self.Bind(wx.EVT_SIZE, self.OnSize)
        self.Bind(wx.EVT_WINDOW_DESTROY, self._on_destroy, source=self)

    def _on_destroy(self, evt):
        # Drop references
        self._view = None
        self._tab_data_model = None

    def __str__(self):
        return "{0} {2} {1}".format(
            self.__class__.__name__,
            self._view.name.value if self._view else "",
            id(self))

    __repr__ = __str__

    @property
    def view(self):
        return self._view

    def clear(self):
        self.canvas.clear()
        if self.bottom_legend:
            self.bottom_legend.clear()
        if self.left_legend:
            self.left_legend.clear()
        self.Refresh()

    def setView(self, view, tab_data):
        raise NotImplementedError

    ################################################
    # Panel control
    ################################################

    def ShowLegend(self, show):
        """ Show or hide the merge slider """
        self.bottom_legend.Show(show)

    def HasFocus(self, *args, **kwargs):
        return self._has_focus is True

    def SetFocus(self, focus):
        """ Set the focus on the viewport according to the focus parameter.

        focus:  A boolean value.

        """

        logging.debug(["Removing focus from %s", "Setting focus to %s"][focus], id(self))

        self._has_focus = focus
        if focus:
            self.SetBackgroundColour(gui.BORDER_COLOUR_FOCUS)
        else:
            self.SetBackgroundColour(gui.BORDER_COLOUR_UNFOCUS)

    ################################################
    # GUI Event handling
    ################################################

    def OnChildFocus(self, evt):
        """ Give the focus to the view if one of the child widgets is clicked """
        if self._view and self._tab_data_model:
            # This will take care of doing everything necessary
            # Remember, the notify method of the vigilant attribute will
            # only fire if the values changes.
            self._tab_data_model.focussedView.value = self._view

        evt.Skip()

    def OnSize(self, evt):
        evt.Skip()  # processed also by the parent

    def Disable(self, *args, **kwargs):
        logging.debug("Disabling %s", self.canvas)
        wx.Panel.Disable(self, *args, **kwargs)
        self.canvas.Disable(*args, **kwargs)

    def Enable(self, *args, **kwargs):
        logging.debug("Enabling %s", self.canvas)
        wx.Panel.Enable(self, *args, **kwargs)
        self.canvas.Enable(*args, **kwargs)


class CameraViewport(ViewPort):
    """ Simple viewport for displaying a video feed, with any added parameters """
    canvas_class = miccanvas.BitmapCanvas


class MicroscopeViewport(ViewPort):
    """ A panel that shows a microscope view and its legend(s)

    This is a generic class, that should be inherited by more specific classes.

    """
    canvas_class = miccanvas.DblMicroscopeCanvas
    bottom_legend_class = InfoLegend

    def __init__(self, *args, **kwargs):
        """Note: The MicroscopeViewport is not fully initialised until setView()
        has been called.
        """
        # Call parent constructor at the end, because it needs the legend panel
        super(MicroscopeViewport, self).__init__(*args, **kwargs)

        if self.bottom_legend:
            # Bind on EVT_SLIDER to update even while the user is moving
            self.bottom_legend.Bind(wx.EVT_LEFT_UP, self.OnSlider)
            self.bottom_legend.Bind(wx.EVT_SLIDER, self.OnSlider)

        # Find out screen pixel density for "magnification" value. This works
        # only with monitors/OS which report correct values. It's unlikely to
        # work with a projector!
        # The 24" @ 1920x1200 screens from Dell have an mpp value of 0.00027 m/px
        self._mpp_screen = 1e-3 * wx.DisplaySizeMM()[0] / wx.DisplaySize()[0]

        self.stage_limit_overlay = None

        self._previous_size = self.canvas.ClientSize
        self.canvas.Bind(wx.EVT_SIZE, self.OnCanvasSize)

    def setView(self, view, tab_data):
        """
        Set the microscope view that this viewport is displaying/representing
        *Important*: Should be called only once, at initialisation.

        :param view:(model.MicroscopeView)
        :param tab_data: (model.MicroscopyGUIData)
        """

        # This is a kind of a kludge, as it'd be best to have the viewport
        # created after the microscope view, but they are created independently
        # via XRC.
        assert(self._view is None)

        # import traceback
        # traceback.print_stack()

        self._view = view
        self._tab_data_model = tab_data

        # TODO: Center to current view position, with current mpp
        view.mpp.subscribe(self._on_view_mpp, init=True)

        # set/subscribe merge ratio
        view.merge_ratio.subscribe(self._onMergeRatio, init=True)

        # subscribe to image, to update legend on stream tree/image change
        view.lastUpdate.subscribe(self._onImageUpdate, init=True)

        # By default, cannot focus, unless the child class allows it
        self.canvas.abilities.discard(CAN_FOCUS)
        # TODO:Listen instead to the StreamTree so that only visible streams affect display.
        tab_data.streams.subscribe(self._on_stream_change, init=True)
        if model.hasVA(tab_data, "zPos"):
            tab_data.zPos.subscribe(self._on_zPos_change, init=True)

        # canvas handles also directly some of the view properties
        self.canvas.setView(view, tab_data)

        # Immediately sets the view FoV based on the current canvas size
        self._set_fov_from_mpp()

        if view.fov_hw:
            logging.info("Tracking mpp on %s" % self)
            # The view FoV changes either when the mpp changes or on resize,  but resize typically
            # causes an update of the mpp (to keep the FoV) so no need to listen to resize.
            self.view.mpp.subscribe(self._on_view_mpp_change)
            view.fov_hw.horizontalFoV.subscribe(self._on_hw_fov_change, init=True)

    ################################################
    #  Panel control
    ################################################

    def ShowMergeSlider(self, show):
        """ Show or hide the merge slider """
        if self.bottom_legend:
            self.bottom_legend.bmp_slider_left.Show(show)
            self.bottom_legend.merge_slider.Show(show)
            self.bottom_legend.bmp_slider_right.Show(show)

    def UpdateHFWLabel(self):
        """ Physical width of the display"""
        if not self._view or not self.bottom_legend:
            return
        hfw = self._view.mpp.value * self.GetClientSize()[0]
        hfw = units.round_significant(hfw, 4)
        label = u"HFW: %s" % units.readable_str(hfw, "m", sig=3)
        self.bottom_legend.set_hfw_label(label)

    def UpdateZposLabel(self):
        if self.bottom_legend:
            # Check if z position should be displayed
            if self._tab_data_model.zPos.range == (0, 0):
                label = None
            else:
                label = u"Z Pos.: %s" % units.readable_str(self._tab_data_model.zPos.value, unit=self._tab_data_model.zPos.unit, sig=3)

            self.bottom_legend.set_zPos_label(label)

    def UpdateMagnification(self):
        # Total magnification
        mag = self._mpp_screen / self._view.mpp.value
        label = u"Mag: × %s" % units.readable_str(units.round_significant(mag, 3))

        # Gather all different image mpp values
        mpps = set()
        for im, stream in self._view.stream_tree.getImages():
            try:
                if hasattr(stream, 'mpp'): # im is a tuple of tuple of tiles
                    mpps.add(stream.mpp.min)
                else:
                    md = im.metadata
                    mpps.add(md[model.MD_PIXEL_SIZE][0])
            except KeyError:
                pass

        # If there's only one mpp value (i.e. there's only one image, or they
        # all have the same mpp value), indicate the digital zoom.
        if len(mpps) == 1:
            mpp_im = mpps.pop()
            # mag_im = self._mpp_screen / mpp_im  # as if 1 im.px == 1 sc.px
            mag_dig = mpp_im / self._view.mpp.value
            label += u" (Digital: × %s)" % units.readable_str(units.round_significant(mag_dig, 2))

        if self.bottom_legend:
            self.bottom_legend.set_mag_label(label)

    ################################################
    #  VA handling
    ################################################

    @call_in_wx_main
    def _onMergeRatio(self, val):
        # round is important because int can cause unstable value
        # int(0.58*100) = 57
        if self.bottom_legend:
            self.bottom_legend.merge_slider.SetValue(round(val * 100))

    @call_in_wx_main
    def _on_view_mpp(self, mpp):
        fov = self.get_fov_from_mpp()
        if fov:
            self.view.fov.value = fov
        buf_fov = self.get_buffer_fov_from_mpp()
        if buf_fov:
            self.view.fov_buffer.value = buf_fov

        if self.bottom_legend:
            self.bottom_legend.scale_win.SetMPP(mpp)
            self.UpdateHFWLabel()
            self.UpdateMagnification()
            # the MicroscopeView will send an event that the view has to be redrawn

    def _on_zPos_change(self, val):
        self.UpdateZposLabel()

    def _checkMergeSliderDisplay(self):
        """
        Update the MergeSlider display and icons depending on the state
        """

        if not self.bottom_legend:
            return

        # MergeSlider is displayed if:
        # * Root operator of StreamTree accepts merge argument
        # * (and) Root operator of StreamTree has >= 2 images
        if (
                "merge" in self._view.stream_tree.kwargs and
                len(self._view.stream_tree) >= 2
        ):
            streams = self._view.getStreams()
            all_opt = all(isinstance(s, (FluoStream, StaticFluoStream)) for s in streams)

            # If all images are optical, assume they are merged using screen blending and no
            # merge ratio is required
            if all_opt:
                self.ShowMergeSlider(False)
            else:
                # TODO: How is the order guaranteed? (Left vs Right)
                # => it should be done in the MicroscopeView when adding a stream
                # For now, special hack for the MicroscopeCanvas which always sets
                # the EM image as "right" (ie, it's drawn last).
                # If there is EM and Spectrum or CL, the spectrum/CL image is always
                # set as "right" (ie, it's drawn last).
                # Note: in practice, there is no AR spatial stream, so it's
                # never mixed with any other stream.
                if (
                        any(isinstance(s, EMStream) for s in streams) and
                        any(isinstance(s, (FluoStream, StaticFluoStream)) for s in streams)
                ):
                    # TODO: don't hard-code MD_AT_FLUO and MD_AT_EM but use the
                    # acquisitionType of the corresponding streams.
                    self.bottom_legend.set_stream_type(wx.LEFT, model.MD_AT_FLUO)
                    self.bottom_legend.set_stream_type(wx.RIGHT, model.MD_AT_EM)
                elif (
                        any(isinstance(s, EMStream) for s in streams) and
                        any(isinstance(s, SpectrumStream) for s in streams)
                ):
                    self.bottom_legend.set_stream_type(wx.LEFT, model.MD_AT_EM)
                    self.bottom_legend.set_stream_type(wx.RIGHT, model.MD_AT_SPECTRUM)
                elif (
                        any(isinstance(s, EMStream) for s in streams) and
                        any(isinstance(s, CLStream) for s in streams)
                ):
                    self.bottom_legend.set_stream_type(wx.LEFT, model.MD_AT_EM)
                    self.bottom_legend.set_stream_type(wx.RIGHT, model.MD_AT_CL)
                else:
                    self.bottom_legend.set_stream_type(wx.LEFT, streams[0].acquisitionType.value)
                    self.bottom_legend.set_stream_type(wx.RIGHT, streams[1].acquisitionType.value)

                self.ShowMergeSlider(True)
        else:
            self.ShowMergeSlider(False)

    @call_in_wx_main
    def _onImageUpdate(self, _):
        self._checkMergeSliderDisplay()

        # magnification might have changed (eg, image with different binning)
        self.UpdateMagnification()

    ################################################
    # GUI Event handling
    ################################################

    def OnSlider(self, evt):
        """
        Merge ratio slider
        """
        if self._view is None or not self.bottom_legend:
            return

        val = self.bottom_legend.merge_slider.GetValue() / 100
        self._view.merge_ratio.value = val
        evt.Skip()

    def OnCanvasSize(self, evt):
        new_size = evt.Size

        # Update the mpp, so that _about_ the same data will be displayed.
        if self.view and self._previous_size != new_size:
            if self.view.fov_hw:
                # Connected to the HW FoV => ensure that the HW FoV stays constant
                hfov = self.view.fov_hw.horizontalFoV.value
                shape = self.view.fov_hw.shape
                fov = (hfov, hfov * shape[1] / shape[0])
                self.set_mpp_from_fov(fov)
            else:
                # Keep the area of the FoV constant
                prev_area = self._previous_size[0] * self._previous_size[1]
                new_area = new_size[0] * new_size[1]
                if prev_area > 0 and new_area > 0:
                    ratio = math.sqrt(prev_area) / math.sqrt(new_area)
                    mpp = ratio * self.view.mpp.value
                    mpp = self.view.mpp.clip(mpp)
                    logging.debug("Updating mpp to %g due to canvas resize from %s to %s, to keep area",
                                  mpp, self._previous_size, new_size)
                    self.view.mpp.value = mpp
        self._previous_size = new_size

        self._set_fov_from_mpp()
        evt.Skip()  # processed also by the parent

    def OnSize(self, evt):
        self.UpdateHFWLabel()
        evt.Skip()  # processed also by the parent

    def OnSliderIconClick(self, evt):
        if not self._view or not self.bottom_legend:
            return

        if evt.GetEventObject() == self.bottom_legend.bmp_slider_left:
            self.bottom_legend.merge_slider.set_to_min_val()
        else:
            self.bottom_legend.merge_slider.set_to_max_val()

        val = self.bottom_legend.merge_slider.GetValue() / 100
        self._view.merge_ratio.value = val
        evt.Skip()

    # END Event handling

    def _on_hw_fov_change(self, hfov):
        """ Set the microscope view's mpp value when the hardware's FoV changes """
        # Vertical FoV is proportional to the horizontal one, based on the shape
        shape = self.view.fov_hw.shape
        fov = (hfov, hfov * shape[1] / shape[0])
        logging.debug("FoV VA changed to %s on %s", fov, self)
        self.set_mpp_from_fov(fov)

    def _on_view_mpp_change(self, mpp):
        """
        Set the microscope's HFW when the MicroscopeView's mpp value changes
         (or the viewport size changes)

        """
        fov = self._set_fov_from_mpp()

        # Only change the FoV of the hardware if it's displayed on screen (so we
        # don't interfere when the viewport is in a hidden tab)
        if self.IsShownOnScreen():
            logging.debug("View mpp changed to %s on %s", mpp, self)
            if fov is None:
                return

            fov_va = self.view.fov_hw.horizontalFoV
            shape = self.view.fov_hw.shape
            # Compute the hfov, so that the whole HW FoV just fully fit
            hfov = min(fov[0], fov[1] * shape[0] / shape[1])

            try:
                # TODO: Test with a simulated SEM that has HFW choices
                choices = fov_va.choices
                # Get the choice that matches hfw most closely
                hfov = util.find_closest(hfov, choices)
            except (AttributeError, NotApplicableError):
                hfov = fov_va.clip(hfov)

            logging.debug("Setting hardware FoV to %s", hfov)
            # Disable temporarily setting mpp when HFW changes to avoid loops
            fov_va.unsubscribe(self._on_hw_fov_change)
            fov_va.value = hfov
            fov_va.subscribe(self._on_hw_fov_change)

    def _get_fov_from_mpp(self, view_size_px):
        """
        Return the field of view of the canvas
        view_size_px (float, float): View size in pixels
        :return: (None or float,float) Field width and height in meters
        """
        # Trick: we actually return the smallest of the FoV dimensions, so
        # that we are sure the microscope image will fit fully (if it's square)

        if self.view and all(v > 0 for v in view_size_px):
            mpp = self.view.mpp.value
            fov = (mpp * view_size_px[0], mpp * view_size_px[1])
            logging.debug("Computed FoV (%g x %s px) = %s on %s",
                          mpp, view_size_px, fov, self)
            return fov

        return None

    def get_fov_from_mpp(self):
        """
        Return the field of view of the canvas
        :return: (None or float,float) Field width and height in meters
        """
        return self._get_fov_from_mpp(self.canvas.ClientSize)

    def get_buffer_fov_from_mpp(self):
        """
        Return the field of view of the canvas
        :return: (None or float,float) Field width and height in meters
        """
        return self._get_fov_from_mpp(self.canvas.buffer_size)

    def set_mpp_from_fov(self, fov):
        """
        Set the mpp of the microscope view according to the given FoV.
        If the FoV is not the same ratio as the view, it will pick the biggest
          mpp that ensures the whole FoV is shown.
        Similar to canvas.fit_to_content(), but doesn't need a content, just the
          hardware settings.
        fov (float, float): horizontal/vertical size of the field of view in m.
        """
        view_size_px = self.canvas.ClientSize

        if self.view and all(v > 0 for v in view_size_px):
            mpp = max(phy / px for phy, px in zip(fov, view_size_px))
            mpp = self.view.mpp.clip(mpp)
            logging.debug("Setting view mpp to %s using given fov %s for %s", mpp, fov, self)
            # Disable temporarily setting the HFW from the mpp to avoid loops.
            self.view.mpp.unsubscribe(self._on_view_mpp_change)
            self.view.mpp.value = mpp
            self.view.mpp.subscribe(self._on_view_mpp_change)

    def _set_fov_from_mpp(self):
        """
        Updates the view.fov and .fov_buffer based on the canvas size (in px)
          and the mpp

        return (tuple of float): the FoV set
        """
        fov = self.get_fov_from_mpp()
        if fov is not None and self.view:
            self.view.fov.value = fov
            self.view.fov_buffer.value = self.get_buffer_fov_from_mpp()

        return fov

    def show_stage_limit_overlay(self):
        if not self.stage_limit_overlay:
            self.stage_limit_overlay = overlay.world.BoxOverlay(self.canvas)
        self.canvas.add_world_overlay(self.stage_limit_overlay)
        wx.CallAfter(self.canvas.request_drawing_update)

    def hide_stage_limit_overlay(self):
        if self.stage_limit_overlay:
            self.canvas.remove_world_overlay(self.stage_limit_overlay)
            wx.CallAfter(self.canvas.request_drawing_update)

    def set_stage_limits(self, roi):
        """
        roi (4 x float): ltrb, in m from the centre
        """
        if not self.stage_limit_overlay:
            self.stage_limit_overlay = overlay.world.BoxOverlay(self.canvas)
        self.stage_limit_overlay.set_dimensions(roi)
        
    def _on_stream_change(self, streams):
        """
        When the streams are changed, check if z-index is supported. If so,
        add the focus tool to the canvas abilities. Otherwise, remove it.
        """
        if any(model.hasVA(s, "zIndex") for s in streams):
            self.canvas.abilities.add(CAN_FOCUS)
        else:
            self.canvas.abilities.discard(CAN_FOCUS)


class OverviewViewport(MicroscopeViewport):
    """ A Viewport containing a downscaled overview image of the loaded sample """

    canvas_class = miccanvas.OverviewCanvas
    bottom_legend_class = InfoLegend

    def __init__(self, *args, **kwargs):
        super(OverviewViewport, self).__init__(*args, **kwargs)
        self.Parent.Bind(wx.EVT_SIZE, self.OnSize)

    def OnSize(self, evt):
        super(OverviewViewport, self).OnSize(evt)
        self.canvas.fit_view_to_content(True)

    def setView(self, view, tab_data):
        """ Attach the MicroscopeView associated with the overview """

        super(OverviewViewport, self).setView(view, tab_data)

        self.canvas.point_select_overlay.p_pos.subscribe(self._on_position_select)
        # Only allow moving when chamber is under vacuum
        tab_data.main.chamberState.subscribe(self._on_chamber_state_change, init=True)

    def _on_chamber_state_change(self, chamber_state):
        """ Watch position changes in the PointSelectOverlay if the chamber is ready """

        # If state is unknown, it's probably going to be unknown forever, so
        # we have to allow (and in the worst case the user will be able to move
        # while the chamber is opened)
        if (chamber_state in {CHAMBER_VACUUM, CHAMBER_UNKNOWN} and
                self._view.has_stage()):
            self.canvas.point_select_overlay.activate()
        else:
            self.canvas.point_select_overlay.deactivate()

    def _on_position_select(self, p_pos):
        """ Set the physical view position
        """
        if self._tab_data_model:
            if self._view.has_stage():
                self._view.moveStageTo(p_pos)


class LiveViewport(MicroscopeViewport):
    """
    Used to display live streams on Secom and Delphi.
    The main difference is the handling of the pause state, which prevents
    stage move and indicate it via an icon.
    """

    def __init__(self, *args, **kwargs):
        super(LiveViewport, self).__init__(*args, **kwargs)
        self._orig_abilities = set()

    def setView(self, view, tab_data):
        # Must be before calling the super, as the super drops CAN_FOCUS automatically
        self._orig_abilities = self.canvas.abilities & {CAN_DRAG, CAN_FOCUS}
        super(LiveViewport, self).setView(view, tab_data)
        tab_data.streams.subscribe(self._on_stream_change)
        view.stream_tree.should_update.subscribe(self._on_stream_play,
                                                            init=True)

    def _on_stream_play(self, is_playing):
        """
        Called whenever view contains a stream playing or not.
        Used to update the drag/focus capabilities
        """
        self.canvas.play_overlay.hide_pause(is_playing)
        if CAN_DRAG in self._orig_abilities and self._view.has_stage():
            # disable/enable move
            if is_playing:
                self.canvas.abilities.add(CAN_DRAG)
            else:
                self.canvas.abilities.discard(CAN_DRAG)
        # check focus ability too
        self._on_stream_change()

    def _on_stream_change(self, streams=None):
        """
        Called whenever the current (playing) stream changes.
        Used to update the focus capability based on the stream
        """
        if CAN_FOCUS not in self._orig_abilities:
            return
        # find out the current playing stream in the view
        for s in self._view.getStreams():
            if s.should_update.value:
                can_focus = s.focuser is not None
                logging.debug("current stream can focus: %s", can_focus)
                break
        else:
            logging.debug("Found no playing stream")
            can_focus = False

        if can_focus:
            self.canvas.abilities.add(CAN_FOCUS)
        else:
            self.canvas.abilities.discard(CAN_FOCUS)


class ARLiveViewport(LiveViewport):
    """
    LiveViewport dedicated to show AR images.
    Never allow to move/zoom, and do not show pause icon if no stream.
    """

    canvas_class = miccanvas.SparcARCanvas

    def __init__(self, *args, **kwargs):
        super(ARLiveViewport, self).__init__(*args, **kwargs)
        # TODO: should be done on the fly by _checkMergeSliderDisplay()
        # change SEM icon to Goal
        if self.bottom_legend:
            self.bottom_legend.bmp_slider_right.SetBitmap(getBitmap("icon/ico_blending_goal.png"))

    def setView(self, view, tab_data):
        super(ARLiveViewport, self).setView(view, tab_data)
        view.lastUpdate.subscribe(self._on_stream_update, init=True)

    def _on_stream_update(self, _):
        """ Hide the play icon overlay if no stream are present """
        show = len(self._view.stream_tree) > 0
        self.canvas.play_overlay.show = show

    def SetFlip(self, orientation):
        """ Flip the canvas in the given direction

        :param orientation: (None or int) wx.VERTICAL (logical or) wx.HORIZONTAL,
         or None for no flipping
        """
        self.canvas.flip = orientation or 0

    def show_mirror_overlay(self, activate=True):
        """ Activate the mirror overlay to enable user manipulation """
        self.canvas.add_world_overlay(self.canvas.mirror_ol)
        if activate:
            self.canvas.mirror_ol.activate()

    def hide_mirror_overlay(self):
        """ Deactivate the mirror overlay to disable user manipulation """
        self.canvas.mirror_ol.deactivate()
        self.canvas.remove_world_overlay(self.canvas.mirror_ol)


# TODO: rename to something more generic? RawLiveViewport?
class ARAcquiViewport(ARLiveViewport):
    """
    Same as ARLiveViewport, but without legend
    """
    bottom_legend_class = None


class AngularResolvedViewport(ViewPort):
    """
    Viewport to show the (static) AR images with polar projection
    """

    # Default class
    canvas_class = miccanvas.AngularResolvedCanvas
    bottom_legend_class = RadioLegend

    def __init__(self, *args, **kwargs):
        super(AngularResolvedViewport, self).__init__(*args, **kwargs)

    def setView(self, view, tab_data):
        """
        Set the microscope view that this viewport is displaying/representing
        *Important*: Should be called only once, at initialisation.

        :param view:(model.View)
        :param tab_data: (model.MicroscopyGUIData)
        """
        assert(self._view is None)

        self._view = view
        self._tab_data_model = tab_data
        # current stream displayed (or None)
        self.stream = None

        # canvas handles also directly some of the view properties
        self.canvas.setView(view, tab_data)

        view.stream_tree.flat.subscribe(self.connect_stream)

        self.bottom_legend.set_pol_callback(self.on_legend_pol_change)

    def connect_stream(self, streams):
        """ Find the most appropriate stream in the view to be displayed, and make sure the display
        is updated when the stream is updated.

        """
        if not streams:
            stream = None
        elif len(streams) > 1:
            logging.warning("Found %d streams, will pick one randomly", len(streams))
            if self.stream in streams:
                stream = self.stream  # don't change
            else:
                stream = streams[0]
        else:
            stream = streams[0]

        if self.stream is stream:
            logging.debug("Not reconnecting to stream as it's already connected")
            return

        # Disconnect from the old VAs
        if self.stream:
            if hasattr(self.stream, 'polarization'):
                logging.debug("Disconnecting %s from polarization VA", stream)
                self.stream.polarization.unsubscribe(self.on_va_pol_change)

        # Connect to the new VA
        self.stream = stream

        # get polarization positions and set callback function
        if hasattr(self.stream, "polarization"):
            logging.debug("Connecting %s to polarization VA", stream)
            self.update_legend_pol_choices()
            self.stream.polarization.subscribe(self.on_va_pol_change)
            self.bottom_legend.Show(True)
        # if no polarization VA or if no stream (= None)
        else:
            # clear legend and also automatically drop callback
            self.bottom_legend.clear()
            self.bottom_legend.Show(False)
        self.Layout()

    def on_legend_pol_change(self, pol):
        """
        Called when the user changes the polarization in the legend.
        Changes the polarization VA.
        """
        self.stream.polarization.value = pol

    def on_va_pol_change(self, pol):
        """
        Called when the .polarization VA value has changed.
        Sets the correct polarization in the legend if the polarization VA was
        changed via the dropdown menu within the stream settings.
        """
        self.bottom_legend.set_pol_value(pol)

    def update_legend_pol_choices(self):
        """
        Get the choices of the polarization VA.
        Set the callback function in the legend, which should be called when a different
        polarization position is requested.
        """
        choices = self.stream.polarization.choices
        if choices == frozenset(POL_POSITIONS):
            choices = POL_POSITIONS  # use the ordered tuple to keep legend ordered later
        default = self.stream.polarization.value
        self.bottom_legend.set_pol_entries(choices, default)


class PlotViewport(ViewPort):
    """ Abstract Class for displaying plotted data """
    __metaclass__ = no_conflict.classmaker(right_metas=(ABCMeta,))

    # Default class
    canvas_class = miccanvas.BarPlotCanvas
    bottom_legend_class = AxisLegend
    left_legend_class = AxisLegend

    def __init__(self, *args, **kwargs):
        super(PlotViewport, self).__init__(*args, **kwargs)
        # We need a local reference to the projection (and stream), because if
        # we rely on the reference within the MicroscopeView, it might be
        # replaced before we get an explicit chance to unsubscribe event handlers.
        self._projection = None  # Might be the same as _stream, if it has its own .image
        self._stream = None

    def setView(self, view, tab_data):
        """
        Set the microscope view that this viewport is displaying/representing
        *Important*: Should be called only once, at initialisation.

        :param view:(model.View)
        :param tab_data: (model.MicroscopyGUIData)
        """

        # This is a kind of a kludge, as it'd be best to have the viewport
        # created after the microscope view, but they are created independently
        # via XRC.
        assert(self._view is None)

        # import traceback
        # traceback.print_stack()

        self._view = view
        self._tab_data_model = tab_data
        self._stream_subs = []  # _on_stream_play subscribers

        # canvas handles also directly some of the view properties
        self.canvas.setView(view, tab_data)

        # Keep an eye on the stream tree, so we can (re)connect when it changes
        view.stream_tree.flat.subscribe(self._on_stream_tree, init=True)

        # For the play/pause icon
        view.stream_tree.should_update.subscribe(self._on_streams_play, init=True)
        view.stream_tree.flat.subscribe(self._on_stream_update, init=True)

    def _on_stream_update(self, projs):
        """
        Hide the play icon overlay if no stream are present (or they are all static)
        """
        if len(projs) > 0:
            # Any stream not static?
            show = any(not isinstance(get_original_stream(o), StaticStream) for o in projs)
        else:
            show = False
        self.canvas.play_overlay.show = show

    def _on_streams_play(self, is_playing):
        """
        Update the status of the play/pause icon overlay
        """
        self.canvas.play_overlay.hide_pause(is_playing)

    def Refresh(self, *args, **kwargs):
        """
        Refresh the ViewPort while making sure the legends get redrawn as well
        Can be called safely from other threads.
        """
        self.left_legend.Refresh()
        self.bottom_legend.Refresh()
        self.canvas.Refresh()

    def _on_stream_tree(self, projs):
        """
        Called when the stream_tree is changed.
        projs (list of Streams or Projections)
        Find the most appropriate stream in the view to be displayed, and make
        sure the display is updated when the stream is updated.
        """
        # Disconnect all previous subscribers
        self._stream_subs = []  # Automatic unsubscription

        # Most of the time, there is only one stream, but in some cases, there might be more.
        if not projs:
            proj = None
        elif len(projs) > 1:
            # => pick the first one playing
            for o in projs:
                s = get_original_stream(o)
                if s.should_update.value:
                    proj = o
                    break
            else:  # no stream playing
                logging.warning("Found %d streams, will pick one randomly", len(projs))
                if self._projection in projs:
                    proj = self._projection  # don't change
                else:
                    proj = projs[0]

            for o in projs:
                s = get_original_stream(o)
                f = partial(self._on_stream_play, s)
                s.should_update.subscribe(f)
                self._stream_subs.append(f)  # Hold the ref to avoid unsubscription
        else:
            proj = projs[0]

        self._connect_projection(proj)

    def _on_stream_play(self, stream, is_playing):
        # Force the stream playing to be shown
        if is_playing:
            self._connect_projection(stream)

    def _connect_projection(self, proj):
        """
        Adjust the viewport to show the given projection (of a stream)
        """
        if self._projection is proj:
            # logging.debug("not reconnecting to stream as it's already connected")
            return

        # Disconnect the old stream
        if self._projection:
            logging.debug("Disconnecting %s from PlotViewPort", self._projection)
            if hasattr(self._stream, "peak_method"):
                self._stream.peak_method.unsubscribe(self._on_peak_method)
            self._projection.image.unsubscribe(self._on_new_data)

        # Connect the new stream
        self._stream, self._projection = get_original_stream(proj), proj
        if proj:
            logging.debug("Connecting %s to PlotViewPort", proj)

            # If there is a DataProjection, peak_method is still "global" on the
            # Stream (shared between all the projections)
            if hasattr(self._stream, "peak_method"):
                self._stream.peak_method.subscribe(self._on_peak_method, init=True)
            self._projection.image.subscribe(self._on_new_data, init=True)
        else:
            logging.info("No stream to plot found")
            self.clear()  # Remove legend ticks and clear plot

    @abstractmethod
    def _on_new_data(self, data):
        pass

    def _on_peak_method(self, state):
        pass


class NavigablePlotViewport(PlotViewport):
    """ Abstract Class for displaying plotted data

    Unlike PlotViewport, the NavigablePlotViewport can be manipulated by the user.
    The plot can be zoomed and panned
        - Mousewheel over an axis zooms that axis.
        - Left mouse drag over an axis pans that axis.
        - Mousewheel over the canvas zooms the horizontal axis
        - Middle mouse button drag on the canvas pans the plot

    This is controlled by VA's
    .hrange: Tuple VA with the horizontal scale range
    .vrange: Tuple VA with the vertical scale range
    .hrange_lock, .vrange_lock: Bool VA that locks H/V axes from manipulation

    Note: clamping of the ranges is handle by the mouse events, because
    the clamping behavior must function differently for pans, drags, and scrolling

    """
    __metaclass__ = no_conflict.classmaker(right_metas=(ABCMeta,))

    # Default class
    canvas_class = miccanvas.NavigableBarPlotCanvas

    def __init__(self, *args, **kwargs):
        super(NavigablePlotViewport, self).__init__(*args, **kwargs)

        self.hrange = model.TupleVA(setter=self.update_hrange)
        self.bottom_legend.tick_spacing = 80
        self.hrange_lock = model.BooleanVA(False)
        self.bottom_legend.lock_va = self.hrange_lock

        self.vrange = model.TupleVA(setter=self.update_vrange)
        self.vrange_lock = model.BooleanVA(False)
        self.left_legend.lock_va = self.vrange_lock

        # Indicate a left mouse button drag in the canvas
        # Note: *only* use it indicate that the *canvas* is performing an operation related to
        # dragging!
        self._dragging = False
        self._hdrag_scale_factor = -0.1
        self._vdrag_scale_factor = -0.1
        self._vmargin = 2  # factor for the upper margin of data display

        # The amount of pixels shifted in the current drag event
        self.drag_shift = (0, 0)  # cnvs, cnvs
        #  initial position of mouse when started dragging
        self.drag_init_pos = (0, 0)  # cnvs, cnvs

        # Track if the legend was dragged. It should be reset to False at the
        # end of button up handlers, giving overlay the chance to check if a
        # drag occurred.

        self.canvas.Bind(wx.EVT_MOUSEWHEEL, self.on_hlegend_scroll)

        self.canvas.Bind(wx.EVT_MIDDLE_DOWN, self.on_drag_start)
        self.canvas.Bind(wx.EVT_MIDDLE_UP, self.on_drag_end)
        self.canvas.Bind(wx.EVT_MOTION, self.on_canvas_motion)
        self.canvas.Bind(miccanvas.EVT_FIT_VIEW_TO_CONTENT, self.on_fit_view)

        self.left_legend.Bind(wx.EVT_MOUSEWHEEL, self.on_vlegend_scroll)
        self.left_legend.Bind(wx.EVT_LEFT_DOWN, self.on_drag_start)
        self.left_legend.Bind(wx.EVT_LEFT_UP, self.on_drag_end)
        self.left_legend.Bind(wx.EVT_MOTION, self.on_vlegend_motion)

        self.bottom_legend.Bind(wx.EVT_MOUSEWHEEL, self.on_hlegend_scroll)
        self.bottom_legend.Bind(wx.EVT_LEFT_DOWN, self.on_drag_start)
        self.bottom_legend.Bind(wx.EVT_LEFT_UP, self.on_drag_end)
        self.bottom_legend.Bind(wx.EVT_MOTION, self.on_hlegend_motion)
        
    def on_fit_view(self, _):
        """
        Called when a fit view to content event occurs.

        Sets the display ranges to the data ranges.
        """

        if not self.canvas.has_data():
            return

        # Unlock the axis locks
        self.hrange_lock.value = False
        self.vrange_lock.value = False

        hcontent = self.canvas.get_hcontent_range()

        if hcontent is not None:
            self.hrange.value = hcontent
        else:
            self.hrange.value = self.canvas.data_xrange

        self.vrange.value = self.canvas.data_yrange

        logging.debug("Fitting view to content: H %s V %s", self.hrange.value, self.vrange.value)

    def update_hrange(self, hrange):
        """
        Setter for VA hrange

        Refreshes the plot and axis legend displays with the new scale
        """
        if hrange[0] > hrange[1]:
            raise ValueError("Bad range tuple. First element should be less than the second")

        self.canvas.set_ranges(hrange, self.vrange.value)

        self.bottom_legend.range = hrange
        if self.canvas.has_data():
            self.bottom_legend.lo_ellipsis = self.canvas.display_xrange[0] > self.canvas.data_xrange[0]
            self.bottom_legend.hi_ellipsis = self.canvas.display_xrange[1] < self.canvas.data_xrange[1]
        self.bottom_legend.Refresh()

        logging.debug("HRange: %s", hrange)
        return hrange

    def update_vrange(self, vrange):
        """
        Setter for VA vrange

        Refreshes the plot and axis legend displays with the new scale
        """
        if vrange[0] > vrange[1]:
            raise ValueError("Bad range tuple. First element should be less than the second")

        self.canvas.set_ranges(self.hrange.value, vrange)

        self.left_legend.range = vrange
        if self.canvas.has_data():
            self.left_legend.lo_ellipsis = self.canvas.display_yrange[0] > self.canvas.data_yrange[0]
            self.left_legend.hi_ellipsis = self.canvas.display_yrange[1] < self.canvas.data_yrange[1]
        self.left_legend.Refresh()

        logging.debug("VRange: %s", vrange)
        return vrange

    def on_hlegend_scroll(self, evt=None):
        """ Scroll event for the bottom legend.

        Zooms the x-scale of the data and refreshes display
        """
        if not self.canvas.has_data():
            return

        rot = evt.GetWheelRotation() / evt.GetWheelDelta()
        span = self.hrange.value[1] - self.hrange.value[0]
        zoom_centre = self.canvas.pos_x_to_val_x(evt.Position[0])
        scale = 0.1
        rng = self.hrange.value

        # proportion of zoom_centre
        prop = (zoom_centre - rng[0]) / span
        
        # zoom around the centre point
        if rot < 0:
            new_span = span * (1 + scale)
        else:
            new_span = span * (1 - scale)

        lo = zoom_centre - prop * new_span
        hi = lo + new_span

        # Clamp the ranges - this functions differently for scrolls than pans
        if lo < self.canvas.data_xrange[0]:
            lo = self.canvas.data_xrange[0]
        if hi > self.canvas.data_xrange[1]:
            hi = self.canvas.data_xrange[1]

        self.hrange.value = (lo, hi)

    def on_vlegend_scroll(self, evt=None):
        """ Scroll event for the left legend.

        Zooms the y-scale of the data and refreshes display
        """
        if not self.canvas.has_data():
            return

        rot = evt.GetWheelRotation() / evt.GetWheelDelta()
        span = self.vrange.value[1] - self.vrange.value[0]
        # zoom_centre = self.canvas.pos_x_to_val_x(evt.Position[0])
        zoom_centre = self.canvas.pos_y_to_val_y(evt.Position[1])
        scale = 0.1
        rng = self.vrange.value

        # proportion of zoom_centre
        prop = (zoom_centre - rng[0]) / span

        # zoom around the centre point
        if rot < 0:
            new_span = span * (1 + scale)
        else:
            new_span = span * (1 - scale)

        lo = zoom_centre - prop * new_span
        hi = lo + new_span

        # Clamp the ranges - this functions differently for scrolls than pans
        lo = max(self.canvas.data_yrange[0], lo)
        hi = min(hi, self.canvas.data_yrange[1] + (self.canvas.data_yrange[1] - self.canvas.data_yrange[0]) * self._vmargin)

        self.vrange.value = (lo, hi)

    def on_drag_start(self, evt):
        """ Start a dragging procedure """
        if not self.canvas.has_data():
            return

        self._dragging = True

        pos = evt.Position
        self.drag_init_pos = (self.canvas.pos_x_to_val_x(pos[0]),
                              self.canvas.pos_y_to_val_y(pos[1]))

        logging.debug("Drag started at %s", self.drag_init_pos)

    def on_drag_end(self, evt):
        """ End the dragging procedure """
        self._dragging = False

    def on_hlegend_motion(self, evt):
        """ Process mouse motion

        Set the drag shift and refresh the image if dragging is enabled and the left mouse button is
        down.

        """
        if self._dragging:

            v_pos = (self.canvas.pos_x_to_val_x(evt.Position[0]),
                self.canvas.pos_y_to_val_y(evt.Position[1]))

            self.drag_shift = (v_pos[0] - self.drag_init_pos[0],
                          v_pos[1] - self.drag_init_pos[1])

            # Rescale the horizontal axis
            (lo, hi) = self.hrange.value

            lo += (self.drag_shift[0] * self._hdrag_scale_factor)
            hi += (self.drag_shift[0] * self._hdrag_scale_factor)

            if lo < self.canvas.data_xrange[0]:
                lo = self.canvas.data_xrange[0]
                hi = self.canvas.display_xrange[1]
            if hi > self.canvas.data_xrange[1]:
                hi = self.canvas.data_xrange[1]
                lo = self.canvas.display_xrange[0]

            self.hrange.value = (lo, hi)

    def on_vlegend_motion(self, evt):
        """ Process mouse motion

        Set the drag shift and refresh the image if dragging is enabled and the left mouse button is
        down.

        """
        if self._dragging:

            v_pos = (self.canvas.pos_x_to_val_x(evt.Position[0]),
                self.canvas.pos_y_to_val_y(evt.Position[1]))

            self.drag_shift = (v_pos[0] - self.drag_init_pos[0],
                          v_pos[1] - self.drag_init_pos[1])

            # Rescale the vertical axis
            (lo, hi) = self.vrange.value
            lo += (self.drag_shift[1] * self._vdrag_scale_factor)
            hi += (self.drag_shift[1] * self._vdrag_scale_factor)

            if lo < self.canvas.data_yrange[0]:
                lo = self.canvas.data_yrange[0]
                hi = self.canvas.display_yrange[1]
            if hi > self.canvas.data_yrange[1] + self.canvas.data_yrange[1] * self._vmargin:
                hi = self.canvas.data_yrange[1] + self.canvas.data_yrange[1] * self._vmargin
                lo = self.canvas.display_yrange[0]

            self.vrange.value = (lo, hi)

    def on_canvas_motion(self, evt):
        """ Process mouse motion

        Set the drag shift and refresh the image if dragging is enabled and the left mouse button is
        down.

        """
        if self._dragging:

            v_pos = (self.canvas.pos_x_to_val_x(evt.Position[0]),
                     self.canvas.pos_y_to_val_y(evt.Position[1]))

            self.drag_shift = (v_pos[0] - self.drag_init_pos[0],
                          v_pos[1] - self.drag_init_pos[1])

            (lo, hi) = self.hrange.value
            lo += (self.drag_shift[0] * self._hdrag_scale_factor)
            hi += (self.drag_shift[0] * self._hdrag_scale_factor)
            if lo < self.canvas.data_xrange[0]:
                lo = self.canvas.data_xrange[0]
                hi = self.canvas.display_xrange[1]
            if hi > self.canvas.data_xrange[1]:
                hi = self.canvas.data_xrange[1]
                lo = self.canvas.display_xrange[0]
            self.hrange.value = (lo, hi)

            (lo, hi) = self.vrange.value
            lo += (self.drag_shift[1] * self._vdrag_scale_factor)
            hi += (self.drag_shift[1] * self._vdrag_scale_factor)

            if lo < self.canvas.data_yrange[0]:
                lo = self.canvas.data_yrange[0]
                hi = self.canvas.display_yrange[1]
            if hi > self.canvas.data_yrange[1] + self.canvas.data_yrange[1] * self._vmargin:
                hi = self.canvas.data_yrange[1] + self.canvas.data_yrange[1] * self._vmargin
                lo = self.canvas.display_yrange[0]

            self.vrange.value = (lo, hi)


class PointSpectrumViewport(NavigablePlotViewport):
    """
    Shows the spectrum of a point -> bar plot + legend
    Legend axes are wavelength/intensity.
    """

    def __init__(self, *args, **kwargs):
        super(PointSpectrumViewport, self).__init__(*args, **kwargs)
        self._curve_overlay = None

    def setView(self, view, tab_data):
        self._peak_fitter = peak.PeakFitter()
        self._peak_future = model.InstantaneousFuture()
        self._curve_overlay = overlay.view.CurveOverlay(self.canvas)

        super(PointSpectrumViewport, self).setView(view, tab_data)

        wx.CallAfter(self.bottom_legend.SetToolTip, "Wavelength")
        wx.CallAfter(self.left_legend.SetToolTip, "Intensity")

    def clear(self):
        # Try to clear previous curve (if already initialised)
        if self._curve_overlay is not None:
            self._curve_overlay.clear_labels()
        super(PointSpectrumViewport, self).clear()

    def _on_new_data(self, data):
        """
        Called when a new data is available (in a live stream, or because the
           selected pixel has changed)
        data (1D DataArray)
        """
        if data is not None and data.size:
            wll, unit_x = spectrum.get_spectrum_range(data)
            wl_rng = wll[0], wll[-1]
            data_rng = min(data), max(data)

            self.canvas.set_1d_data(wll, data, range_x=wl_rng, range_y=data_rng, unit_x=unit_x)

            if not self.hrange_lock.value:
                hcontent = self.canvas.get_hcontent_range()
                if hcontent is not None:
                    self.hrange.value = hcontent
                else:
                    self.hrange.value = wl_rng
            if not self.vrange_lock.value:
                self.vrange.value = data_rng

            self.bottom_legend.unit = unit_x

            if hasattr(self._stream, "peak_method") and self._stream.peak_method.value is not None:
                # cancel previous fitting if there is one in progress
                self._peak_future.cancel()
                self._curve_overlay.clear_labels()
                self.spectrum_range = wll
                self.unit_x = unit_x
                # TODO: try to find more peaks (= small window) based on width?
                # => so far not much success
                # ex: dividerf = 1 + math.log(self.stream.selectionWidth.value)
                self._peak_future = self._peak_fitter.Fit(data, wll,
                                                          type=self._stream.peak_method.value)
                self._peak_future.add_done_callback(self._update_peak)
        else:
            self.clear()
        self.Refresh()

    def _on_peak_method(self, state):
        if state is not None:
            self.canvas.add_view_overlay(self._curve_overlay)
            self._curve_overlay.activate()
            if self._projection is not None:
                # Force update of the peak_method
                self._on_new_data(self._projection.image.value)
        else:
            self._curve_overlay.deactivate()
            self.canvas.remove_view_overlay(self._curve_overlay)
            self.canvas.Refresh()

    @call_in_wx_main
    def _update_peak(self, f):
        try:
            peak_data, peak_offset = f.result()
            self._curve_overlay.update_data(peak_data, peak_offset,
                                            self.spectrum_range, self.unit_x,
                                            self._stream.peak_method.value)
            logging.debug("Received peak data")
        except CancelledError:
            logging.debug("Peak fitting in progress was cancelled")
        except ValueError:
            logging.info("Peak fitting failed on the data")
            self._curve_overlay.clear_labels()
            self.canvas.Refresh()
        except Exception:
            logging.error("Error while try to find peaks", exc_info=True)
            self._curve_overlay.clear_labels()
            self.canvas.Refresh()


class ChronographViewport(NavigablePlotViewport):
    """
    Shows the chronograph of a 0D detector reading -> bar plot + legend
    Legend axes are time/intensity.
    Intensity is between min/max of data.
    """

    def setView(self, view, tab_data):
        super(ChronographViewport, self).setView(view, tab_data)
        wx.CallAfter(self.bottom_legend.SetToolTip, "Time")
        wx.CallAfter(self.left_legend.SetToolTip, "Intensity")

    def _connect_projection(self, proj):
        super(ChronographViewport, self)._connect_projection(proj)
        if proj:
            # Show "count / s" if we know the data is normalized
            im = proj.image.value
            if im is not None and im.metadata.get(model.MD_DET_TYPE) == model.MD_DT_NORMAL:
                wx.CallAfter(self.left_legend.SetToolTip, "Count per second")
            else:
                wx.CallAfter(self.left_legend.SetToolTip, "Intensity")

    def _on_new_data(self, data):
        if data is not None and data.size:
            x, unit_x = spectrum.get_time_range(data)
            # If there is a known period, use it
            if hasattr(self._stream, "windowPeriod"):
                range_x = (min(x[0], -self._stream.windowPeriod.value), x[-1])
            else:
                range_x = (x[0], x[-1])

            # Put the data axis with -5% of min and +5% of max:
            # the margin hints the user the display is not clipped
            extrema = (float(min(data)), float(max(data)))  # float() to avoid numpy arrays
            data_width = extrema[1] - extrema[0]
            if data_width == 0:
                range_y = (0, extrema[1] * 1.05)
            else:
                range_y = (max(0, extrema[0] - data_width * 0.05),
                           extrema[1] + data_width * 0.05)

            if not self.hrange_lock.value:
                hcontent = self.canvas.get_hcontent_range()
                if hcontent is not None:
                    self.hrange.value = hcontent
                else:
                    self.hrange.value = range_x
            if not self.vrange_lock.value:
                self.vrange.value = range_y

            self.canvas.set_1d_data(x, data, range_x=range_x, range_y=range_y, unit_x=unit_x)

            self.bottom_legend.unit = unit_x
            self.canvas.set_ranges(self.hrange.value, self.vrange.value)

        else:
            self.clear()
        self.Refresh()


# TODO: share some code with PlotViewPort
class TwoDViewPort(ViewPort):
    """
    An abstract class to show a (non draggable) 2D image, with axes on both sides
    """
    __metaclass__ = no_conflict.classmaker(right_metas=(ABCMeta,))

    canvas_class = miccanvas.TwoDPlotCanvas
    bottom_legend_class = AxisLegend
    left_legend_class = AxisLegend

    def __init__(self, *args, **kwargs):
        """Note: The MicroscopeViewport is not fully initialised until setView()
        has been called.
        """
        super(TwoDViewPort, self).__init__(*args, **kwargs)
        self._projection = None  # Might be the same as _stream, if it has its own .image
        self._stream = None

        # TODO: the image from the projection (both LineSpectrum and TemporalSpectrum)
        # are currently shown as one pixel per original pixel. However, the
        # original pixels are *not* linearly spaced. So either the projection
        # should compensate for this (difficult), or the axis legends should be
        # non linear (easier).

    def Refresh(self, *args, **kwargs):
        """
        Refresh the ViewPort while making sure the legends get redrawn as well
        Can be called safely from other threads
        """
        self.left_legend.Refresh()
        self.bottom_legend.Refresh()
        # Note: this is not thread safe, so would need to be in a CallAfter()
        # super(TwoDViewPort, self).Refresh(*args, **kwargs)
        wx.CallAfter(self.canvas.update_drawing)

    def setView(self, view, tab_data):
        """
        Set the microscope view that this viewport is displaying/representing
        *Important*: Should be called only once, at initialisation.

        :param view:(model.View)
        :param tab_data: (model.MicroscopyGUIData)
        """

        # This is a kind of a kludge, as it'd be best to have the viewport
        # created after the microscope view, but they are created independently
        # via XRC.
        assert(self._view is None)

        self._view = view
        self._tab_data_model = tab_data

        view.show_crosshair.value = False

        # canvas handles also directly some of the view properties
        self.canvas.setView(view, tab_data)

        # Keep an eye on the stream tree, so we can (re)connect when it changes
        view.stream_tree.flat.subscribe(self.connect_stream, init=True)

        # For the play/pause icon
        view.stream_tree.should_update.subscribe(self._on_stream_play, init=True)
        view.lastUpdate.subscribe(self._on_stream_update, init=True)

    def _on_stream_play(self, is_playing):
        """
        Update the status of the play/pause icon overlay
        """
        self.canvas.play_overlay.hide_pause(is_playing)

    def _on_stream_update(self, _):
        """
        Hide the play icon overlay if no stream are present (or they are all static)
        """
        ss = self._view.getStreams()
        if len(ss) > 0:
            # Any stream not static?
            show = any(not isinstance(s, (StaticStream, DataProjection)) for s in ss)
        else:
            show = False
        self.canvas.play_overlay.show = show

    def connect_stream(self, projs):
        """
        Called when the stream_tree is changed.
        projs (list of Streams or Projections)
        Find the most appropriate stream in the view to be displayed, and make
        sure the display is updated when the stream is updated.
        """
        # Most of the time, there is only one stream, but in some cases, there might be more.
        if not projs:
            proj = None
        elif len(projs) > 1:
            # => pick the first one playing
            for o in projs:
                s = get_original_stream(o)
                if s.should_update.value:
                    proj = o
                    break
            else:  # no stream playing
                logging.warning("Found %d streams, will pick one randomly", len(projs))
                if self._projection in projs:
                    proj = self._projection  # don't change
                else:
                    proj = projs[0]
        else:
            proj = projs[0]

        if self._projection is proj:
            logging.debug("Not reconnecting to stream as it's already connected")
            return

        # Disconnect the old stream
        if self._projection:
            logging.debug("Disconnecting %s from TwoDViewPort", self._projection)
            self._projection.image.unsubscribe(self._on_new_data)

        # Connect the new stream
        self._stream, self._projection = get_original_stream(proj), proj
        if proj:
            logging.debug("Connecting %s to TwoDViewPort", proj)
            self._projection.image.subscribe(self._on_new_data, init=True)
        else:
            logging.info("No stream to plot found")
            self.clear()  # Remove legend ticks and clear plot

    @abstractmethod
    def _on_new_data(self, data):
        pass


class TemporalSpectrumViewport(TwoDViewPort):
    """
    Shows a temporal spectrum image from a streak camera with time vertically and
    wavelength horizontally.
    """

    def __init__(self, *args, **kwargs):
        super(TemporalSpectrumViewport, self).__init__(*args, **kwargs)
        self.canvas.markline_overlay.val.subscribe(self._on_overlay_selection)

    def _on_overlay_selection(self, pos):
        """
        Called whenever the markline position of the overlay changes, to set
        the selected wavelength & time in the stream (so that the SinglePoint
        projections are updated).
        """
        if self._stream:
            if hasattr(self._stream, "selected_wavelength"):
                self._stream.selected_wavelength.value = pos[0]
            if hasattr(self._stream, "selected_time"):
                self._stream.selected_time.value = pos[1]

    def setView(self, view, tab_data):
        super(TemporalSpectrumViewport, self).setView(view, tab_data)
        wx.CallAfter(self.bottom_legend.SetToolTip, "Wavelength")
        wx.CallAfter(self.left_legend.SetToolTip, "Time")

    def connect_stream(self, projs):
        super(TemporalSpectrumViewport, self).connect_stream(projs)
        stream, proj = self._stream, self._projection

        if hasattr(stream, "selected_time"):
            pos = self.canvas.markline_overlay.val
            pos.value = (stream.selected_wavelength.value, stream.selected_time.value)

    def _on_new_data(self, data):
        if data is not None and data.size:
            wl, unit_x = spectrum.get_spectrum_range(data)
            spectrum_range = (min(wl), max(wl))
            times, unit_y = spectrum.get_time_range(data)
            time_range = (max(times), min(times))  # inverted, to show the 0 at the top

            self.canvas.set_2d_data(data, unit_x, unit_y, spectrum_range, time_range)

            self.bottom_legend.unit = unit_x
            self.left_legend.unit = unit_y
            self.bottom_legend.range = spectrum_range
            self.left_legend.range = time_range
        else:
            self.clear()
        self.Refresh()


class LineSpectrumViewport(TwoDViewPort):
    """
    A viewport for showing 1D spectrum: an image with wavelength horizontally and
    space vertically.
    """
    def __init__(self, *args, **kwargs):
        """Note: The MicroscopeViewport is not fully initialised until setView()
        has been called.
        """
        super(LineSpectrumViewport, self).__init__(*args, **kwargs)
        self.canvas.markline_overlay.val.subscribe(self.on_spectrum_motion)

    def on_spectrum_motion(self, val):
        """
        Connects the .selected_pixel based on the vertical position of the overlay.
        The goal is that when the user picks a different position along the line,
        the corresponding pixel is selected, and eventually the spectrum will be
        shown (in a separate viewport).
        """

        if val and self._stream:
            rng = self.left_legend.range
            rat = (val[1] - rng[0]) / (rng[1] - rng[0])
            line = self._stream.selected_line.value
            line_pixels = rasterize_line(*line)
            self._stream.selected_pixel.value = line_pixels[int(len(line_pixels) * rat)]

    def setView(self, view, tab_data):
        super(LineSpectrumViewport, self).setView(view, tab_data)
        wx.CallAfter(self.bottom_legend.SetToolTip, "Wavelength")
        wx.CallAfter(self.left_legend.SetToolTip, "Distance from origin")

    def _on_new_data(self, data):
        if data is not None:
            spectrum_range, unit_x = spectrum.get_spectrum_range(data)
            line_length = data.shape[0] * data.metadata[model.MD_PIXEL_SIZE][1]

            self.bottom_legend.unit = unit_x
            self.bottom_legend.range = (spectrum_range[0], spectrum_range[-1])
            unit_y = "m"
            self.left_legend.unit = unit_y
            self.left_legend.range = (0, line_length)

            self.canvas.set_2d_data(data, unit_x, unit_y,
                                    self.bottom_legend.range, self.left_legend.range)
        else:
            self.clear()

        self.Refresh()
