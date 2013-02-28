# -*- coding: utf-8 -*-
"""
Created on 8 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or
modify it under the terms of the GNU General Public License as published by the
Free Software Foundation, either version 2 of the License, or (at your option)
any later version.

Odemis is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""
from __future__ import division

import logging

import wx

from odemis import gui
from odemis.gui.comp.scalewindow import ScaleWindow
from odemis.gui.comp.slider import Slider
from odemis.gui.dblmscopecanvas import DblMicroscopeCanvas
from odemis.gui.img.data import \
    getico_blending_optBitmap, getico_blending_semBitmap
from odemis.gui.model import OPTICAL_STREAMS, EM_STREAMS
from odemis.gui.util import call_after, units


class MicroscopeViewport(wx.Panel):
    """ A panel that shows a microscope view and its legend below it.
    """

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
        self.canvas = DblMicroscopeCanvas(self)

        ##### Legend
        # It's made of multiple controls positioned via sizers
        # TODO: allow the user to pick which information is displayed in the
        # legend
        self.legend_panel = wx.Panel(self)
        self.legend_panel.SetBackgroundColour(self.GetBackgroundColour())
        self.legend_panel.SetForegroundColour(self.GetForegroundColour())

        # Merge slider
        # TODO: should be possible to use VAConnector
        self.mergeSlider = Slider(self.legend_panel,
                    wx.ID_ANY,
                    50,
                    (0, 100),
                    size=(100, 12),
                    style=wx.SL_HORIZONTAL | wx.SL_AUTOTICKS | wx.SL_TICKS | wx.NO_BORDER)

        self.mergeSlider.SetBackgroundColour(self.legend_panel.GetBackgroundColour())
        self.mergeSlider.SetForegroundColour("#4d4d4d")
        self.mergeSlider.SetToolTipString("Merge ratio")
        #self.mergeSlider.SetLineSize(50)

        self.bmpIconOpt = wx.StaticBitmap(self.legend_panel, wx.ID_ANY, getico_blending_optBitmap())
        self.bmpIconSem = wx.StaticBitmap(self.legend_panel, wx.ID_ANY, getico_blending_semBitmap())

        # TODO: should make sure that a click _anywhere_ on the legend brings
        # the focus to the view

        # # Make sure that mouse clicks on the icons set the correct focus
        # self.bmpIconOpt.Bind(wx.EVT_LEFT_DOWN, self.OnChildFocus)
        # self.bmpIconSem.Bind(wx.EVT_LEFT_DOWN, self.OnChildFocus)

        self.mergeSlider.Bind(wx.EVT_LEFT_UP, self.OnSlider)
        # FIXME: dragging the slider should have immediate effect on the merge ratio
        # Need to bind on EVT_SLIDER (seems to be the new way, on any type of change),
        # EVT_SCROLL_CHANGED (seems to work only on windows and gtk, and only at the end)
        # EVT_SCROLL_THUMBTRACK (seems to work always, but only dragging, not key press)
        # Maybe our Slider control need to generate wx.wxEVT_COMMAND_SLIDER_UPDATED
        # when value is changed by user in order to have EVT_SLIDER passed?

        # Dragging the slider should set the focus to the right view
        self.mergeSlider.Bind(wx.EVT_LEFT_DOWN, self.OnChildFocus)

        # scale
        self.scaleDisplay = ScaleWindow(self.legend_panel)
        self.scaleDisplay.SetFont(font)

        # Horizontal Full Width text
        # TODO: allow the user to select/copy the text
        self.hfwDisplay = wx.StaticText(self.legend_panel)
        self.hfwDisplay.SetToolTipString("Horizontal Field Width")
        self.hfwDisplay.Bind(wx.EVT_LEFT_DOWN, self.OnChildFocus)

        # magnification
        self.LegendMag = wx.StaticText(self.legend_panel)
        self.LegendMag.SetToolTipString("Magnification")
        self.LegendMag.Bind(wx.EVT_LEFT_DOWN, self.OnChildFocus)

        # TODO more...
#        self.LegendWl = wx.StaticText(self.legend_panel)
#        self.LegendWl.SetToolTipString("Wavelength")
#        self.LegendET = wx.StaticText(self.legend_panel)
#        self.LegendET.SetToolTipString("Exposure Time")
#
#        self.LegendDwell = wx.StaticText(self.legend_panel)
#        self.LegendSpot = wx.StaticText(self.legend_panel)
#        self.LegendHV = wx.StaticText(self.legend_panel)

        # Sizer composition:
        #
        # +-------------------------------------------------------+
        # | +----+-----+ |    |         |    | +----+------+----+ |
        # | |Mag | HFW | | <> | <Scale> | <> | |Icon|Slider|Icon| |
        # | +----+-----+ |    |         |    | +----+------+----+ |
        # +-------------------------------------------------------+

        leftColSizer = wx.BoxSizer(wx.HORIZONTAL)
        leftColSizer.Add(self.LegendMag, border=10, flag=wx.ALIGN_CENTER|wx.RIGHT)
        leftColSizer.Add(self.hfwDisplay, border=10, flag=wx.ALIGN_CENTER)

        sliderSizer = wx.BoxSizer(wx.HORIZONTAL)
        sliderSizer.Add(self.bmpIconOpt, border=3,
                        flag=wx.ALIGN_CENTER|wx.RIGHT|wx.RESERVE_SPACE_EVEN_IF_HIDDEN)
        sliderSizer.Add(self.mergeSlider,
                        flag=wx.ALIGN_CENTER|wx.EXPAND|wx.RESERVE_SPACE_EVEN_IF_HIDDEN)
        sliderSizer.Add(self.bmpIconSem, border=3,
                        flag=wx.ALIGN_CENTER|wx.LEFT|wx.RESERVE_SPACE_EVEN_IF_HIDDEN)

        legendSizer = wx.BoxSizer(wx.HORIZONTAL)
        legendSizer.Add(leftColSizer, 0, flag=wx.EXPAND|wx.ALIGN_CENTER)
        legendSizer.AddStretchSpacer(1)
        legendSizer.Add(self.scaleDisplay, 2, border=2,
                        flag=wx.EXPAND|wx.ALIGN_CENTER|wx.RIGHT|wx.LEFT)
        legendSizer.AddStretchSpacer(1)
        legendSizer.Add(sliderSizer, 0, flag=wx.EXPAND|wx.ALIGN_CENTER)

        # legend_panel_sizer is needed to add a border around the legend
        legend_panel_sizer = wx.BoxSizer(wx.VERTICAL)
        legend_panel_sizer.Add(legendSizer, border=10, flag=wx.ALL|wx.EXPAND)
        self.legend_panel.SetSizerAndFit(legend_panel_sizer)

        # Put all together (canvas + legend)
        mainSizer = wx.BoxSizer(wx.VERTICAL)
        mainSizer.Add(self.canvas, 1,
                border=2, flag=wx.EXPAND|wx.TOP|wx.LEFT|wx.RIGHT)
        mainSizer.Add(self.legend_panel, 0,
                border=2, flag=wx.EXPAND|wx.BOTTOM|wx.LEFT|wx.RIGHT)

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

        self._microscope_view = microscope_view
        self._microscope_model = microscope_model

        # TODO: Center to current view position, with current mpp
        microscope_view.mpp.subscribe(self._onMPP, init=True)

        # set/subscribe merge ratio
        microscope_view.merge_ratio.subscribe(self._onMergeRatio, init=True)

        # subscribe to image, to update legend on streamtree/image change
        microscope_view.lastUpdate.subscribe(self._onImageUpdate, init=True)
        self.ShowMergeSlider(True) # FIXME: only if required by the view

        # canvas handles also directly some of the view properties
        self.canvas.setView(microscope_view)

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
        self.bmpIconOpt.Show(show)
        self.mergeSlider.Show(show)
        self.bmpIconSem.Show(show)

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
        label = "HFW: %s" % units.readable_str(hfw, "m")
        self.hfwDisplay.SetLabel(label)
        self.legend_panel.Layout()

    def UpdateMagnification(self):
        # TODO: shall we use the real density of the screen?
        # We could use real density but how much important is it?
        mppScreen = 0.00025 # 0.25 mm/px
        label = "Mag: "

        # three possibilities:
        # * no image => total mag (using current mpp)
        # * all images have same mpp => mag instrument * mag digital
        # * >1 mpp => total mag

        # get all the mpps
        mpps = set()
        for s in self._microscope_view.getStreams():
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
                label += u"÷" + units.readable_str(units.round_significant(1.0/magIm, 3))
            magDig = im_mpp / self._microscope_view.mpp.value
            if magDig >= 1:
                label += u" ×" + units.readable_str(units.round_significant(magDig, 3))
            else:
                label += u" ÷" + units.readable_str(units.round_significant(1.0/magDig, 3))
        else:
            # one magnification
            mag = mppScreen / self._microscope_view.mpp.value
            if mag >= 1:
                label += u"×" + units.readable_str(units.round_significant(mag, 3))
            else:
                label += u"÷" + units.readable_str(units.round_significant(1.0/mag, 3))

        self.LegendMag.SetLabel(label)
        self.legend_panel.Layout()

    ################################################
    ## VA handling
    ################################################

    @call_after
    def _onMergeRatio(self, val):
        # round is important because int can cause unstable value
        # int(0.58*100) = 57
        self.mergeSlider.SetValue(round(val * 100))


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
        self.scaleDisplay.SetMPP(mpp)
        self.UpdateHFWLabel()
        self.UpdateMagnification()
        # the MicroscopeView will send an event that the view has to be redrawn

    @call_after
    def _onImageUpdate(self, timestamp):
        # TODO: Just depend on the "merge" argument on the streamTree
        # For now we just duplicate the logic in _convertStreamsToImages():
        #  display iif both EM and OPT streams
        streams = self._microscope_view.streams.getStreams()
        has_opt = any(isinstance(s, OPTICAL_STREAMS) for s in streams)
        has_em = any(isinstance(s, EM_STREAMS) for s in streams)

        if (has_opt and has_em):
            self.ShowMergeSlider(True)
        else:
            self.ShowMergeSlider(False)
        # MergeSlider is displayed iif:
        # * Root operator of StreamTree accepts merge argument
        # * (and) Root operator of StreamTree has >= 2 images
#        if ("merge" in self._microscope_view.streams.kwargs and
#            len(self._microscope_view.streams.streams) >= 2):
#            self.ShowMergeSlider(True)
#        else:
#            self.ShowMergeSlider(False)

        # magnification might have changed (eg, different number of images)
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

    def OnSlider(self, event):
        """
        Merge ratio slider
        """
        if self._microscope_view is None:
            return

        self._microscope_view.merge_ratio.value = self.mergeSlider.GetValue() / 100
        event.Skip()

    def OnSize(self, event):
        event.Skip() # processed also by the parent
        self.UpdateHFWLabel()

    ## END Event handling

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: