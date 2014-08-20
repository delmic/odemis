# -*- coding: utf-8 -*-
"""
Created on 8 Feb 2012

:author: Éric Piel
:copyright: © 2012 Éric Piel, Delmic

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

import collections
import logging

import wx

from odemis import gui, model
from odemis.acq.stream import OPTICAL_STREAMS, EM_STREAMS
from odemis.gui import BG_COLOUR_LEGEND, FG_COLOUR_LEGEND
from odemis.gui.comp import miccanvas
from odemis.gui.comp.canvas import CAN_DRAG, CAN_FOCUS
from odemis.gui.comp.legend import InfoLegend, AxisLegend
from odemis.gui.img.data import getico_blending_goalBitmap
from odemis.gui.model import CHAMBER_VACUUM
from odemis.gui.util import call_after
from odemis.model import VigilantAttributeBase, NotApplicableError
from odemis.util import units


class ViewPort(wx.Panel):

    # Default classes for the canvas and the legend. These may be overridden
    # in subclasses
    canvas_class = miccanvas.DblMicroscopeCanvas
    legend_class = None

    def __init__(self, *args, **kwargs):
        """Note: The MicroscopeViewport is not fully initialised until setView()
        has been called.
        """
        wx.Panel.__init__(self, *args, **kwargs)

        self._microscope_view = None  # model.MicroscopeView
        self._tab_data_model = None # model.MicroscopyGUIData

        # Keep track of this panel's pseudo focus
        self._has_focus = False

        font = wx.Font(8, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        self.SetFont(font)
        self.SetBackgroundColour(BG_COLOUR_LEGEND)
        self.SetForegroundColour(FG_COLOUR_LEGEND)

        # main widget
        self.canvas = self.canvas_class(self)

        # Put all together (canvas + legend)

        self.legend = None

        main_sizer = wx.BoxSizer(wx.VERTICAL)

        if self.legend_class is not None:
            if isinstance(self.legend_class, collections.Iterable):
                # FIXME: don't use a list, it will confuse everything.
                # => Just use legend_bottom, legend_left
                self.legend = [self.legend_class[0](self),
                               self.legend_class[1](self)]

                self.legend[1].orientation = self.legend[1].VERTICAL
                self.legend[1].MinSize = (40, -1)

                grid_sizer = wx.GridBagSizer()

                grid_sizer.Add(self.canvas, pos=(0, 1), flag=wx.EXPAND)

                grid_sizer.Add(self.legend[0], pos=(1, 1), flag=wx.EXPAND)
                grid_sizer.Add(self.legend[1], pos=(0, 0), flag=wx.EXPAND)

                filler = wx.Panel(self)
                filler.SetBackgroundColour(BG_COLOUR_LEGEND)
                grid_sizer.Add(filler, pos=(1, 0), flag=wx.EXPAND)

                grid_sizer.AddGrowableRow(0, 1)
                grid_sizer.AddGrowableCol(1, 1)
                # grid_sizer.RemoveGrowableCol(0)

                # Focus the view when a child element is clicked
                for lp in self.legend:
                    lp.Bind(wx.EVT_LEFT_DOWN, self.OnChildFocus)

                main_sizer.Add(grid_sizer, 1,
                        border=2, flag=wx.EXPAND | wx.ALL)
            else:
                main_sizer.Add(self.canvas, 1,
                    border=2, flag=wx.EXPAND | wx.TOP | wx.LEFT | wx.RIGHT)
                # It's made of multiple controls positioned via sizers
                # TODO: allow the user to pick which information is displayed
                # in the legend
                # pylint: disable=E1102, E1103
                self.legend = self.legend_class(self)
                self.legend.Bind(wx.EVT_LEFT_DOWN, self.OnChildFocus)

                main_sizer.Add(self.legend, 0, border=2, flag=wx.EXPAND|wx.BOTTOM|wx.LEFT|wx.RIGHT)
        else:
            main_sizer.Add(self.canvas, 1,
                border=2, flag=wx.EXPAND | wx.ALL)

        self.SetSizerAndFit(main_sizer)
        main_sizer.Fit(self)
        self.SetAutoLayout(True)

        self.Bind(wx.EVT_CHILD_FOCUS, self.OnChildFocus)
        self.Bind(wx.EVT_SIZE, self.OnSize)

    def __str__(self):
        return "{0} {2} {1}".format(
            self.__class__.__name__,
            self._microscope_view.name.value if self._microscope_view else "",
            id(self))

    __repr__ = __str__

    @property
    def microscope_view(self):
        return self._microscope_view

    def setView(self, microscope_view, tab_data):
        raise NotImplementedError

    ################################################
    ## Panel control
    ################################################

    def ShowLegend(self, show):
        """ Show or hide the merge slider """
        self.legend.Show(show)  #pylint: disable=E1103

    def HasFocus(self, *args, **kwargs):
        return self._has_focus == True

    def SetFocus(self, focus):   #pylint: disable=W0221
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
    ## GUI Event handling
    ################################################

    def OnChildFocus(self, evt):
        """ When one of it's child widgets is clicked, this viewport should be
        considered as having the focus.
        """
        if self._microscope_view and self._tab_data_model:
            # This will take care of doing everything necessary
            # Remember, the notify method of the vigilant attribute will
            # only fire if the values changes.
            self._tab_data_model.focussedView.value = self._microscope_view

        evt.Skip()

    def OnSize(self, evt):
        evt.Skip()  # processed also by the parent

    def Refresh(self, *args, **kwargs):
        """ Refresh the ViewPort while making sure the legends get redrawn as well """
        if self.legend:
            for legend in self.legend:
                    legend.clear()

        super(ViewPort, self).Refresh(*args, **kwargs)


class OverviewVierport(ViewPort):
    """ A Viewport containing a downscaled overview image of the loaded sample

    If a chamber state can be tracked,
    """

    canvas_class = miccanvas.OverviewCanvas

    def __init__(self, *args, **kwargs):
        super(OverviewVierport, self).__init__(*args, **kwargs)
        #Remove all abilities, because the overview should have none
        self.tab_data = None

    def setView(self, microscope_view, tab_data):
        """ Attach the MicroscopeView associated with the overview """
        # Hide the cross hair overlay
        microscope_view.show_crosshair.value = False
        self.canvas.setView(microscope_view, tab_data)

        self.tab_data = tab_data

        # Track chamber state if possible
        if tab_data.main.chamber:
            tab_data.main.chamberState.subscribe(self._on_chamber_state_change)

    def _on_chamber_state_change(self, chamber_state):
        """ Watch position changes in the PointSelectOverlay if the chamber is ready """

        if chamber_state == CHAMBER_VACUUM:
            self.canvas.point_select_overlay.p_pos.subscribe(self._on_position_select)
        elif self.canvas.active_overlays:
            self.canvas.point_select_overlay.p_pos.unsubscribe(self._on_position_select)

    def _on_position_select(self, p_pos):
        """ Set the physical view position """

        if self.tab_data:
            focussed_view = self.tab_data.focussedView.value
            focussed_view.view_pos.value = p_pos
            focussed_view.moveStageToView()


class MicroscopeViewport(ViewPort):
    """ A panel that shows a microscope view and its legend below it.

    This is a generic class, that should be inherited by more specific classes.
    """

    legend_class = InfoLegend

    def __init__(self, *args, **kwargs):
        """Note: The MicroscopeViewport is not fully initialised until setView()
        has been called.
        """
        # Call parent constructor at the end, because it needs the legend panel
        ViewPort.__init__(self, *args, **kwargs)

        # Bind on EVT_SLIDER to update even while the user is moving
        self.legend.Bind(wx.EVT_LEFT_UP, self.OnSlider)
        self.legend.Bind(wx.EVT_SLIDER, self.OnSlider)


    def setView(self, microscope_view, tab_data):
        """
        Set the microscope view that this viewport is displaying/representing
        *Important*: Should be called only once, at initialisation.

        :param microscope_view:(model.MicroscopeView)
        :param tab_data: (model.MicroscopyGUIData)
        """

        # This is a kind of a kludge, as it'd be best to have the viewport
        # created after the microscope view, but they are created independently
        # via XRC.
        assert(self._microscope_view is None)

        # import traceback
        # traceback.print_stack()

        self._microscope_view = microscope_view
        self._tab_data_model = tab_data

        # TODO: Center to current view position, with current mpp
        microscope_view.mpp.subscribe(self._onMPP, init=True)

        # set/subscribe merge ratio
        microscope_view.merge_ratio.subscribe(self._onMergeRatio, init=True)

        # subscribe to image, to update legend on streamtree/image change
        microscope_view.lastUpdate.subscribe(self._onImageUpdate, init=True)

        # canvas handles also directly some of the view properties
        self.canvas.setView(microscope_view, tab_data)

    ################################################
    ## Panel control
    ################################################

    def ShowMergeSlider(self, show):
        """ Show or hide the merge slider """
        self.legend.bmp_slider_left.Show(show)
        self.legend.merge_slider.Show(show)
        self.legend.bmp_slider_right.Show(show)

    def UpdateHFWLabel(self):
        """ Physical width of the display"""
        if not self._microscope_view:
            return
        hfw = self._microscope_view.mpp.value * self.GetClientSize()[0]
        hfw = units.round_significant(hfw, 4)
        label = u"HFW: %s" % units.readable_str(hfw, "m", sig=3)
        self.legend.set_hfw_label(label)

    def UpdateMagnification(self):
        # TODO: shall we use the real density of the screen?
        # We could use real density but how much important is it?
        # The 24" @ 1920x1200 screens from Dell have an mpp value of 0.000270213
        mpp_screen = 0.00025  # 0.25 mm/px
        label = u"Mag: "

        # three possibilities:
        # * no image => total mag (using current mpp)
        # * all images have same mpp => mag instrument * mag digital
        # * >1 mpp => total mag

        # Gather all different image mpp values
        mpps = set()
        for im in self._microscope_view.stream_tree.getImages():
            try:
                mpps.add(im.metadata[model.MD_PIXEL_SIZE][0])
            except KeyError:
                pass

        # If there's only one mpp value (i.e. there's only one image, or they all have the same
        # mpp value)...
        if len(mpps) == 1:
            # Two magnifications:
            #
            # 1st: The magnification that occurs by rendering the image to the screen
            mpp_im = mpps.pop()
            mag_im = mpp_screen / mpp_im  # as if 1 im.px == 1 sc.px

            if mag_im >= 1:
                label += u"×" + units.readable_str(units.round_significant(mag_im, 3))
            else:
                label += u"÷" + units.readable_str(units.round_significant(1.0 / mag_im, 3))

            # 2nd: The magnification that occurs by changing the mpp value of the view (i.e digitial
            # zoom)
            mag_dig = mpp_im / self._microscope_view.mpp.value

            if mag_dig >= 1:
                label += u" ×" + units.readable_str(units.round_significant(mag_dig, 3))
            else:
                label += u" ÷" + units.readable_str(units.round_significant(1.0 / mag_dig, 3))
        else:
            # One magnification: The image mpp is ignored
            mag = mpp_screen / self._microscope_view.mpp.value
            if mag >= 1:
                label += u"×" + units.readable_str(units.round_significant(mag, 3))
            else:
                label += u"÷" + units.readable_str(units.round_significant(1.0 / mag, 3))

        self.legend.set_mag_label(label)

    ################################################
    ## VA handling
    ################################################

    @call_after
    def _onMergeRatio(self, val):
        # round is important because int can cause unstable value
        # int(0.58*100) = 57
        self.legend.merge_slider.SetValue(round(val * 100))

    @call_after
    def _onMPP(self, mpp):
        self.legend.scale_win.SetMPP(mpp)
        self.UpdateHFWLabel()
        self.UpdateMagnification()
        # the MicroscopeView will send an event that the view has to be redrawn

    def _checkMergeSliderDisplay(self):
        """
        Update the MergeSlider display and icons depending on the state
        """
        # MergeSlider is displayed if:
        # * Root operator of StreamTree accepts merge argument
        # * (and) Root operator of StreamTree has >= 2 images
        if ("merge" in self._microscope_view.stream_tree.kwargs and
            len(self._microscope_view.stream_tree) >= 2):

            # How is the order guaranteed? (Left vs Right)
            sc = self._microscope_view.stream_tree[0]
            self.legend.set_stream_type(wx.LEFT, sc.__class__)

            sc = self._microscope_view.stream_tree[1]
            self.legend.set_stream_type(wx.RIGHT, sc.__class__)

            self.ShowMergeSlider(True)
        else:
            self.ShowMergeSlider(False)


    @call_after
    def _onImageUpdate(self, timestamp):
        self._checkMergeSliderDisplay()

        # magnification might have changed (eg, image with different binning)
        self.UpdateMagnification()

    ################################################
    ## GUI Event handling
    ################################################

    def OnSlider(self, evt):
        """
        Merge ratio slider
        """
        if self._microscope_view is None:
            return

        val = self.legend.merge_slider.GetValue() / 100
        self._microscope_view.merge_ratio.value = val
        evt.Skip()

    def OnSize(self, evt):
        evt.Skip() # processed also by the parent
        self.UpdateHFWLabel()

    def OnSliderIconClick(self, evt):
        evt.Skip()

        if self._microscope_view is None:
            return

        if evt.GetEventObject() == self.legend.bmp_slider_left:
            self.legend.merge_slider.set_to_min_val()
        else:
            self.legend.merge_slider.set_to_max_val()

        val = self.legend.merge_slider.GetValue() / 100
        self._microscope_view.merge_ratio.value = val
        evt.Skip()

    ## END Event handling


class SecomViewport(MicroscopeViewport):

    canvas_class = miccanvas.SecomCanvas

    def __init__(self, *args, **kwargs):
        super(SecomViewport, self).__init__(*args, **kwargs)
        self._orig_abilities = set()

    def setView(self, microscope_view, tab_data):
        super(SecomViewport, self).setView(microscope_view, tab_data)
        self._orig_abilities = self.canvas.abilities & {CAN_DRAG, CAN_FOCUS}
        self._microscope_view.stream_tree.should_update.subscribe(self.hide_pause, init=True)

        # If a HorizontalFoV vattribute is present, we keep an eye on it
        if isinstance(self._tab_data_model.main.ebeam.horizontalFoV, VigilantAttributeBase):
            self._tab_data_model.main.ebeam.horizontalFoV.subscribe(self._on_hfw_set_mpp)

    def hide_pause(self, is_playing):
        #pylint: disable=E1101
        self.canvas.icon_overlay.hide_pause(is_playing)
        if hasattr(self._microscope_view, "stage_pos"):
            # disable/enable move and focus change
            if is_playing:
                self.canvas.abilities |= self._orig_abilities
            else:
                self.canvas.abilities -= {CAN_DRAG, CAN_FOCUS}

    def _checkMergeSliderDisplay(self):
        # Overridden to avoid displaying merge slide if only SEM or only Optical
        # display iif both EM and OPT streams
        streams = self._microscope_view.getStreams()
        has_opt = any(isinstance(s, OPTICAL_STREAMS) for s in streams)
        has_em = any(isinstance(s, EM_STREAMS) for s in streams)

        if has_opt and has_em:
            self.ShowMergeSlider(True)
        else:
            self.ShowMergeSlider(False)

    def track_view_mpp(self):
        """ Keep track of changes in the MicroscopeView's mpp value """
        if isinstance(self._tab_data_model.main.ebeam.horizontalFoV, VigilantAttributeBase):
            logging.info("Tracking mpp on %s" % self)
            self.microscope_view.mpp.subscribe(self._on_mpp_set_hfw)

    def untrack_view_mpp(self):
        """ Ignore changes in the MicroscopeView's mpp value """
        if isinstance(self._tab_data_model.main.ebeam.horizontalFoV, VigilantAttributeBase):
            logging.info("UnTracking mpp on %s" % self)
            self.microscope_view.mpp.unsubscribe(self._on_mpp_set_hfw)

    def _on_hfw_set_mpp(self, hfw):
        """ Change the mpp value of the MicroscopeView when the HFW changes

        We set the mpp value of the MicroscopeView by assigning the microscope's hfw value to
        the Canvas' hfw value, which will cause the the Canvas to calculate a new mpp value
        and assign it to View's mpp attribute.

        """

        logging.info("Calculating mpp from hfw for viewport %s" % self)
        self.canvas.horizontal_field_width = hfw

    def _on_mpp_set_hfw(self, mpp):
        """ Set the microscope's hfw when the MicroscopeView's mpp value changes

        The canvas calculates the new hfw value.

        """

        logging.info("Calculating hfw from mpp for viewport %s" % self)
        hfw = self.canvas.horizontal_field_width

        try:
            # TODO: Test with a simulated SEM that has HFW choices
            choices = self._tab_data_model.main.ebeam.horizontalFoV.choices
            # Get the choice that matches hfw most closely
            hfw = min(choices, key=lambda choice: abs(choice - hfw))
        except NotApplicableError:
            hfw = self._tab_data_model.main.ebeam.horizontalFoV.clip(hfw)

        self._tab_data_model.main.ebeam.horizontalFoV.value = hfw


class SparcAcquisitionViewport(MicroscopeViewport):

    canvas_class = miccanvas.SparcAcquiCanvas

    def __init__(self, *args, **kwargs):
        super(SparcAcquisitionViewport, self).__init__(*args, **kwargs)


class SparcAlignViewport(MicroscopeViewport):
    """
    Very simple viewport with no zoom or move allowed
    """
    canvas_class = miccanvas.SparcAlignCanvas

    def __init__(self, *args, **kwargs):
        super(SparcAlignViewport, self).__init__(*args, **kwargs)
        # TODO: should be done on the fly by _checkMergeSliderDisplay()
        # change SEM icon to Goal
        # pylint: disable=E1103
        self.legend.bmp_slider_right.SetBitmap(getico_blending_goalBitmap())


class PlotViewport(ViewPort):
    """ Class for displaying plotted data """

    # Default class
    canvas_class = miccanvas.ZeroDimensionalPlotCanvas
    legend_class = (AxisLegend, AxisLegend)

    def __init__(self, *args, **kwargs):
        ViewPort.__init__(self, *args, **kwargs)
        # We need a local reference to the spectrum stream, because if we rely
        # on the reference within the MicorscopeView, it might be replaced
        # before we get an explicit chance to unsubscribe event handlers
        self.spectrum_stream = None

    def clear(self):
        #pylint: disable=E1103, E1101
        self.canvas.clear()
        self.Refresh()

    def OnSize(self, evt):
        evt.Skip() # processed also by the parent

    @property
    def microscope_view(self):
        return self._microscope_view

    def connect_stream(self, should_update=None):
        """ This method will connect this ViewPort to the Spectrum Stream so it
        it can react to spectrum pixel selection.
        """
        if should_update:
            ss = self.microscope_view.stream_tree.spectrum_streams

            # There should be exactly one Spectrum stream. In the future there
            # might be scenarios where there are more than one.
            if len(ss) != 1:
                raise ValueError("Unexpected number of Spectrum Streams found!")

            self.spectrum_stream = ss[0]
            self.spectrum_stream.selected_pixel.subscribe(self._on_pixel_select)

    def _on_pixel_select(self, pixel):
        """ Pixel selection event handler """
        if pixel == (None, None):
            # TODO: handle more graciously when pixel is unselected?
            logging.warning("Don't know what to do when no pixel is selected")
            return
        data = self.spectrum_stream.get_pixel_spectrum()
        domain = self.spectrum_stream.get_spectrum_range()
        unit_x = self.spectrum_stream.spectrumBandwidth.unit
        self.legend[0].unit = unit_x
        self.canvas.set_1d_data(domain, data, unit_x)
        self.Refresh()

    def setView(self, microscope_view, tab_data):
        """
        Set the microscope view that this viewport is displaying/representing
        *Important*: Should be called only once, at initialisation.

        :param microscope_view:(model.View)
        :param tab_data: (model.MicroscopyGUIData)

        TODO: rename `microscope_view`, since this parameter is a regular view
        """

        # This is a kind of a kludge, as it'd be best to have the viewport
        # created after the microscope view, but they are created independently
        # via XRC.
        assert(self._microscope_view is None)

        # import traceback
        # traceback.print_stack()

        self._microscope_view = microscope_view
        self._tab_data_model = tab_data

        # canvas handles also directly some of the view properties
        self.canvas.setView(microscope_view, tab_data)

        # Keep an eye on the stream tree, so we can (re)connect when it changes
        microscope_view.stream_tree.should_update.subscribe(self.connect_stream)


class AngularResolvedViewport(ViewPort):

    # Default class
    canvas_class = miccanvas.AngularResolvedCanvas
    legend_class = None

    def __init__(self, *args, **kwargs):
        ViewPort.__init__(self, *args, **kwargs)

    def setView(self, microscope_view, tab_data):
        """
        """

        # This is a kind of a kludge, as it'd be best to have the viewport
        # created after the microscope view, but they are created independently
        # via XRC.
        assert(self._microscope_view is None)

        # import traceback
        # traceback.print_stack()

        self._microscope_view = microscope_view
        self._tab_data_model = tab_data

        # canvas handles also directly some of the view properties
        self.canvas.setView(microscope_view, tab_data)

