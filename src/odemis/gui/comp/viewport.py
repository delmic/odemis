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
from odemis import gui
from odemis.gui.comp.legend import InfoLegend, AxisLegend
from odemis.gui.img.data import getico_blending_goalBitmap
from odemis.gui.model import OPTICAL_STREAMS, EM_STREAMS
from odemis.gui.util import call_after, units
import logging
import odemis.gui.canvas as canvas
import wx



class MicroscopeViewport(wx.Panel):
    """ A panel that shows a microscope view and its legend below it.

    This is a generic class, that should be inherited by more specific classes.
    """

    # Default class
    canvas_class = canvas.DblMicroscopeCanvas

    def __init__(self, *args, **kwargs):
        """Note: The MicroscopeViewport is not fully initialised until setView()
        has been called.
        """
        wx.Panel.__init__(self, *args, **kwargs)

        self._microscope_view = None  # instrmodel.MicroscopeView
        self._microscope_model = None # instrmodel.MicroscopeModel

        # Keep track of this panel's pseudo focus
        self._has_focus = False

        font = wx.Font(8, wx.FONTFAMILY_DEFAULT,
                          wx.FONTSTYLE_NORMAL,
                          wx.FONTWEIGHT_NORMAL)
        self.SetFont(font)
        self.SetBackgroundColour("#1A1A1A")
        self.SetForegroundColour("#BBBBBB")

        # main widget
        self.canvas = self.canvas_class(self)

        ##### Legend
        # It's made of multiple controls positioned via sizers
        # TODO: allow the user to pick which information is displayed in the
        # legend
        self.legend_panel = InfoLegend(self) #wx.Panel(self)

        # Focus the view when a child element is clicked
        self.legend_panel.Bind(wx.EVT_LEFT_DOWN, self.OnChildFocus)

        # Bind on EVT_SLIDER to update even while the user is moving
        self.legend_panel.Bind(wx.EVT_LEFT_UP, self.OnSlider)
        self.legend_panel.Bind(wx.EVT_SLIDER, self.OnSlider)

        # Put all together (canvas + legend)
        mainSizer = wx.BoxSizer(wx.VERTICAL)
        mainSizer.Add(self.canvas, 1,
                border=2, flag=wx.EXPAND | wx.TOP | wx.LEFT | wx.RIGHT)
        mainSizer.Add(self.legend_panel, 0,
                border=2, flag=wx.EXPAND | wx.BOTTOM | wx.LEFT | wx.RIGHT)

        self.SetSizerAndFit(mainSizer)
        self.SetAutoLayout(True)

        self.Bind(wx.EVT_CHILD_FOCUS, self.OnChildFocus)
        self.Bind(wx.EVT_SIZE, self.OnSize)


    def setView(self, microscope_view, microscope_model):
        """
        Set the microscope view that this viewport is displaying/representing
        *Important*: Should be called only once, at initialisation.

        :param microscope_view:(instrmodel.MicroscopeView)
        :param microscope_model: (instrmodel.MicroscopeModel)
        """

        # This is a kind of a kludge, as it'd be best to have the viewport
        # created after the microscope view, but they are created independently
        # via XRC.
        assert(self._microscope_view is None)

        # import traceback
        # traceback.print_stack()

        self._microscope_view = microscope_view
        self._microscope_model = microscope_model

        # TODO: Center to current view position, with current mpp
        microscope_view.mpp.subscribe(self._onMPP, init=True)

        # set/subscribe merge ratio
        microscope_view.merge_ratio.subscribe(self._onMergeRatio, init=True)

        # subscribe to image, to update legend on streamtree/image change
        microscope_view.lastUpdate.subscribe(self._onImageUpdate, init=True)

        # canvas handles also directly some of the view properties
        self.canvas.setView(microscope_view, microscope_model)

        # TODO: that should not be the current values, but the values of
        # the current image (so, taken from the metadata).
        # microscope_model.sem_emt_dwell_time.subscribe(self.avDwellTime, True)
        # microscope_model.sem_emt_spot.subscribe(self.avSpot, True)
        # microscope_model.sem_emt_hv.subscribe(self.avHV, True)

        # microscope_model.optical_emt_wavelength.subscribe(self.avWavelength)
        # microscope_model.optical_det_wavelength.subscribe(self.avWavelength, True)
        # microscope_model.optical_det_exposure_time.subscribe(self.avExposureTime, True)

    @property
    def mic_view(self):
        return self._microscope_view

    @property
    def mic_model(self):
        return self._microscope_model

    ################################################
    ## Panel control
    ################################################

    def ShowMergeSlider(self, show):
        """ Show or hide the merge slider """
        self.legend_panel.bmpSliderLeft.Show(show)
        self.legend_panel.mergeSlider.Show(show)
        self.legend_panel.bmpSliderRight.Show(show)

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

    def UpdateHFWLabel(self):
        """ Physical width of the display"""
        if not self._microscope_view:
            return
        hfw = self._microscope_view.mpp.value * self.GetClientSize()[0]
        hfw = units.round_significant(hfw, 4)
        label = u"HFW: %s" % units.readable_str(hfw, "m", sig=3)
        self.legend_panel.set_hfw_label(label)

    def UpdateMagnification(self):
        # TODO: shall we use the real density of the screen?
        # We could use real density but how much important is it?
        mppScreen = 0.00025 # 0.25 mm/px
        label = u"Mag: "

        # three possibilities:
        # * no image => total mag (using current mpp)
        # * all images have same mpp => mag instrument * mag digital
        # * >1 mpp => total mag

        # get all the mpps
        mpps = set()
        for s in self._microscope_view.getStreams():
            if hasattr(s, "image"):
                im = s.image.value
                if im and im.mpp:
                    mpps.add(im.mpp)

        if len(mpps) == 1:
            # two magnifications
            im_mpp = mpps.pop()
            magIm = mppScreen / im_mpp # as if 1 im.px == 1 sc.px
            if magIm >= 1:
                label += u"×" + units.readable_str(units.round_significant(magIm, 3))
            else:
                label += u"÷" + units.readable_str(units.round_significant(1.0 / magIm, 3))
            magDig = im_mpp / self._microscope_view.mpp.value
            if magDig >= 1:
                label += u" ×" + units.readable_str(units.round_significant(magDig, 3))
            else:
                label += u" ÷" + units.readable_str(units.round_significant(1.0 / magDig, 3))
        else:
            # one magnification
            mag = mppScreen / self._microscope_view.mpp.value
            if mag >= 1:
                label += u"×" + units.readable_str(units.round_significant(mag, 3))
            else:
                label += u"÷" + units.readable_str(units.round_significant(1.0 / mag, 3))

        self.legend_panel.set_mag_label(label)

    ################################################
    ## VA handling
    ################################################

    @call_after
    def _onMergeRatio(self, val):
        # round is important because int can cause unstable value
        # int(0.58*100) = 57
        self.legend_panel.mergeSlider.SetValue(round(val * 100))


    # TODO need to subscribe to view_center, or done by canvas and delete this?
    # We link only one way the position:
    #  * if the user moves the view => moves the stage to the same position
    #  * if the stage moves by itself, keep the view at the same place
    #    (and the acquired images will not be centred anymore)
    def _onViewCenter(self, pos):
        if self._microscope_view is None:
            return

        self._microscope_view.view_pos.value = pos
        self._microscope_view.moveStageToView()

    @call_after
    def _onMPP(self, mpp):
        self.legend_panel.scaleDisplay.SetMPP(mpp)
        self.UpdateHFWLabel()
        self.UpdateMagnification()
        # the MicroscopeView will send an event that the view has to be redrawn

    def _checkMergeSliderDisplay(self):
        """
        Update the MergeSlider display and icons depending on the state
        """
        # MergeSlider is displayed iif:
        # * Root operator of StreamTree accepts merge argument
        # * (and) Root operator of StreamTree has >= 2 images
        if ("merge" in self._microscope_view.stream_tree.kwargs and
            len(self._microscope_view.getStreams()) >= 2):
            self.ShowMergeSlider(True)
        else:
            self.ShowMergeSlider(False)

        # TODO: update icons depending on type of streams

    @call_after
    def _onImageUpdate(self, timestamp):
        self._checkMergeSliderDisplay()

        # magnification might have changed (eg, image with different binning)
        self.UpdateMagnification()

    # @call_after
    # def avWavelength(self, value):
    #     # need to know both wavelengths, so just look into the values
    #     win = self.datamodel.optical_emt_wavelength.value
    #     wout = self.datamodel.optical_det_wavelength.value

    #     label = unicode(win) + " nm/" + unicode(wout) + " nm"
    #     self.LegendWl.SetLabel(label)

    # @call_after
    # def avExposureTime(self, value):
    #     label = unicode("%0.2f s" % (value))
    #     self.LegendET.SetLabel(label)
    #     self.Parent.Layout()

    # @call_after
    # def avDwellTime(self, value):
    #     label = "Dwell: %ss" % units.to_string_si_prefix(value)
    #     self.LegendDwell.SetLabel(label)

    # @call_after
    # def avSpot(self, value):
    #     label = "Spot: %g" % value
    #     self.LegendSpot.SetLabel(label)

    # @call_after
    # def avHV(self, value):
    #     label = "HV: %sV" % units.to_string_si_prefix(value)
    #     self.LegendHV.SetLabel(label)

    ################################################
    ## GUI Event handling
    ################################################

    def OnChildFocus(self, evt):
        """ When one of it's child widgets is clicked, this viewport should be
        considered as having the focus.
        """
        if self._microscope_view and self._microscope_model:
            # This will take care of doing everything necessary
            # Remember, the notify method of the vigilant attribute will
            # only fire if the values changes.
            self._microscope_model.focussedView.value = self._microscope_view

        evt.Skip()

    def OnSlider(self, evt):
        """
        Merge ratio slider
        """
        if self._microscope_view is None:
            return

        val = self.legend_panel.mergeSlider.GetValue() / 100
        self._microscope_view.merge_ratio.value = val
        evt.Skip()

    def OnSize(self, evt):
        evt.Skip() # processed also by the parent
        self.UpdateHFWLabel()

    def OnSliderIconClick(self, evt):
        evt.Skip()

        if self._microscope_view is None:
            return

        if(evt.GetEventObject() == self.legend_panel.bmpSliderLeft):
            self.legend_panel.mergeSlider.set_to_min_val()
        else:
            self.legend_panel.mergeSlider.set_to_max_val()

        val = self.legend_panel.mergeSlider.GetValue() / 100
        self._microscope_view.merge_ratio.value = val
        evt.Skip()

    ## END Event handling

class SecomViewport(MicroscopeViewport):

    canvas_class = canvas.SecomCanvas

    def __init__(self, *args, **kwargs):
        super(SecomViewport, self).__init__(*args, **kwargs)

    def setView(self, microscope_view, microscope_model):
        super(SecomViewport, self).setView(microscope_view, microscope_model)
        self._microscope_view.stream_tree.should_update.subscribe(
                                                        self.hide_pause,
                                                        init=True
        )

    def hide_pause(self, hide_pause):
        #pylint: disable=E1101
        self.canvas.icon_overlay.hide_pause(hide_pause)
        self.canvas.lock_stage_drag(not hide_pause)
        self.canvas.Refresh()

    def _checkMergeSliderDisplay(self):
        # Overridden to avoid displaying merge slide if only SEM or only Optical
        # display iif both EM and OPT streams
        streams = self._microscope_view.getStreams()
        has_opt = any(isinstance(s, OPTICAL_STREAMS) for s in streams)
        has_em = any(isinstance(s, EM_STREAMS) for s in streams)

        if (has_opt and has_em):
            self.ShowMergeSlider(True)
        else:
            self.ShowMergeSlider(False)

class SparcAcquisitionViewport(MicroscopeViewport):

    canvas_class = canvas.SparcAcquiCanvas

    def __init__(self, *args, **kwargs):
        super(SparcAcquisitionViewport, self).__init__(*args, **kwargs)

class SparcAnalysisViewport(MicroscopeViewport):

    def __init__(self, *args, **kwargs):
        super(SparcAnalysisViewport, self).__init__(*args, **kwargs)

class SparcAlignViewport(MicroscopeViewport):
    """
    Very simple viewport with no zoom or move allowed
    """
    canvas_class = canvas.SparcAlignCanvas

    def __init__(self, *args, **kwargs):
        super(SparcAlignViewport, self).__init__(*args, **kwargs)
        # TODO: should be done on the fly by _checkMergeSliderDisplay()
        # change SEM icon to Goal
        # FIXME: create goal icon
        self.legend_panel.bmpSliderRight.SetBitmap(getico_blending_goalBitmap())


class PlotViewport(wx.Panel):

    # Default class
    canvas_class = canvas.ZeroDimensionalPlotCanvas

    def __init__(self, *args, **kwargs):
        wx.Panel.__init__(self, *args, **kwargs)

        # Keep track of this panel's pseudo focus
        self._has_focus = False

        font = wx.Font(8, wx.FONTFAMILY_DEFAULT,
                          wx.FONTSTYLE_NORMAL,
                          wx.FONTWEIGHT_NORMAL)
        self.SetFont(font)
        self.SetBackgroundColour("#1A1A1A")
        self.SetForegroundColour("#BBBBBB")

        # main widget
        self.canvas = self.canvas_class(self)

        # legend
        self.legend = AxisLegend(self)

        # Put all together (canvas + legend)
        mainSizer = wx.BoxSizer(wx.VERTICAL)
        mainSizer.Add(self.canvas, 1,
                border=2, flag=wx.EXPAND | wx.TOP | wx.LEFT | wx.RIGHT)
        mainSizer.Add(self.legend, 0,
                border=2, flag=wx.EXPAND | wx.BOTTOM | wx.LEFT | wx.RIGHT)

        self.SetSizerAndFit(mainSizer)
        self.SetAutoLayout(True)

        self.Bind(wx.EVT_CHILD_FOCUS, self.OnChildFocus)
        self.Bind(wx.EVT_SIZE, self.OnSize)

    def OnSize(self, evt):
        evt.Skip() # processed also by the parent

    def OnChildFocus(self, evt):
        evt.Skip()
