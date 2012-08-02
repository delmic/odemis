#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 8 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Delmic Acquisition Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''

import wx

import units

from odemis.gui.dblmscopecanvas import DblMicroscopeCanvas
from odemis.gui.dblmscopeviewmodel import DblMscopeViewModel
from odemis.gui.comp.scalewindow import ScaleWindow
from odemis.gui.comp.slider import CustomSlider
from odemis.gui.instrmodel import InstrumentalImage
from odemis.gui.img.data import getico_blending_optBitmap, \
    getico_blending_semBitmap
from odemis.gui.log import log

class DblMicroscopePanel(wx.Panel):
    """
    A draggable, flicker-free window class adapted to show pictures of two
    microscope simultaneously.

    """
    def __init__(self, *args, **kwargs):
        wx.Panel.__init__(self, *args, **kwargs)

        # Keep track of this panel's pseudo focus
        self._has_focus = False

        self.viewmodel = DblMscopeViewModel()

        self.canvas = DblMicroscopeCanvas(self)

        self._build_windows()


        try:
            self.secom_model = wx.GetApp().secom_model
        except AttributeError:
            msg = "Could not find SECOM model"

            # wx.MessageBox(msg,
            #               "Application error",
            #               style=wx.OK|wx.ICON_ERROR)
            log.error(msg)

            return

        # Control for the selection before AddView(), which needs them
        #self.viewComboLeft = wx.ComboBox(self, style=wx.CB_READONLY, size=(140, -1))
        #self.viewComboRight = wx.ComboBox(self, style=wx.CB_READONLY, size=(140, -1))

        #self.Bind(wx.EVT_COMBOBOX, self.OnComboLeft, self.viewComboLeft)
        #self.Bind(wx.EVT_COMBOBOX, self.OnComboRight, self.viewComboRight)


        self.viewmodel.mpp.subscribe(self.avOnMPP, True)  #pylint: disable=E1101



        emptyView = MicroscopeEmptyView()
        # display : left and right view
        self.displays = []
        #[(emptyView, self.viewComboLeft, self.imageSizerBLeft),
        #                 (emptyView, self.viewComboRight, self.imageSizerBRight)]

        # can be called only with display ready
        self.views = []
        self.AddView(emptyView)
        self.AddView(MicroscopeOpticalView(self, self.secom_model, self.viewmodel))
        self.AddView(MicroscopeSEView(self, self.secom_model, self.viewmodel))

        # Select the default views
        #self.ChangeView(0, self.views[1].name)
        #self.ChangeView(1, self.views[2].name)

        # sync microscope stage with the view
        self.viewmodel.center.value = self.secom_model.stage_pos.value
        self.viewmodel.center.subscribe(self.onViewCenter)

        self.Bind(wx.EVT_CHILD_FOCUS, self.OnChildFocus)

        self.viewmodel.merge_ratio.subscribe(self.avOnMergeRatio, True) #pylint: disable=E1101

        self.Bind(wx.EVT_SIZE, self.OnSize)

    def OnChildFocus(self, evt):
        self.SetFocus(True)
        evt.Skip()

    def HasFocus(self):
        return self._has_focus == True

    def SetFocus(self, focus):   #pylint: disable=W0221
        #wx.Panel.SetFocus(self)
        self._has_focus = focus
        if focus:
            self.SetBackgroundColour("#127BA6")
        else:
            self.SetBackgroundColour("#000000")

    def _build_windows(self):
        """ Construct and lay out all sub windows of this panel """

        font = wx.Font(8, wx.FONTFAMILY_DEFAULT,
                          wx.FONTSTYLE_NORMAL,
                          wx.FONTWEIGHT_NORMAL)
        self.SetFont(font)
        self.SetBackgroundColour("#1A1A1A")
        self.SetForegroundColour("#BBBBBB")

        ###################################
        # Standard legend widgets
        ###################################

        ##### Scale window

        legend_panel = wx.Panel(self)
        legend_panel.SetBackgroundColour("#1A1A1A")
        legend_panel.SetForegroundColour(self.GetForegroundColour())

        self.scaleDisplay = ScaleWindow(legend_panel)
        self.scaleDisplay.SetFont(font)


        #### Values`

        self.magni_label = wx.StaticText(legend_panel, wx.ID_ANY, "10x 10x")
        self.magni_label.SetToolTipString("Magnification Optic Electron")
        self.volta_label = wx.StaticText(legend_panel, wx.ID_ANY, "66 kV")
        self.volta_label.SetToolTipString("Voltage")
        self.dwell_label = wx.StaticText(legend_panel, wx.ID_ANY, "666 μs")
        self.dwell_label.SetToolTipString("Dwell")

        # Merge icons will be grabbed from gui.img.data
        ##### Merge slider

        self.mergeSlider = CustomSlider(legend_panel,
                    wx.ID_ANY,
                    50,
                    (0, 100),
                    size=(100, 12),
                    style=wx.SL_HORIZONTAL | wx.SL_AUTOTICKS | wx.SL_TICKS)
        self.mergeSlider.SetBackgroundColour(legend_panel.GetBackgroundColour())
        self.mergeSlider.SetForegroundColour("#4d4d4d")
        #self.mergeSlider.SetLineSize(50)

        self.bmpIconOpt = wx.StaticBitmap(legend_panel, wx.ID_ANY, getico_blending_optBitmap())
        self.bmpIconSem = wx.StaticBitmap(legend_panel, wx.ID_ANY, getico_blending_semBitmap())

        self.mergeSlider.Bind(wx.EVT_LEFT_UP, self.OnSlider)

        ###################################
        # Optional legend widgets
        ###################################

        self.hfwDisplay = wx.StaticText(legend_panel) # Horizontal Full Width
        #self.hfwDisplay.Hide()


        ###################################
        # Size composition
        ###################################

        #  Scale
        # +-------
        #  HFW text

        # leftColSizer = wx.BoxSizer(wx.VERTICAL)
        # leftColSizer.Add(self.scaleDisplay, flag=wx.EXPAND)
        # leftColSizer.Add(self.hfwDisplay, flag=wx.TOP, border=5)

        #  | Value label | Value label | Value label |
        # +-------
        #  (?????) empty for now

        labelSizer = wx.BoxSizer(wx.HORIZONTAL)
        labelSizer.Add(self.magni_label, flag=wx.RIGHT, border=10)
        labelSizer.Add(self.volta_label, flag=wx.RIGHT, border=10)
        labelSizer.Add(self.dwell_label, flag=wx.RIGHT, border=10)

        # midColSizer = wx.BoxSizer(wx.VERTICAL)
        # midColSizer.Add(labelSizer, flag=wx.ALIGN_CENTER_VERTICAL)

        #  | Icon | Slider | Icon |
        # +-------
        #  (?????) empty for now

        sliderSizer = wx.BoxSizer(wx.HORIZONTAL)

        sliderSizer.Add(self.bmpIconOpt, flag=wx.RIGHT, border=3)
        sliderSizer.Add(self.mergeSlider, flag=wx.EXPAND)
        sliderSizer.Add(self.bmpIconSem, flag=wx.LEFT, border=3)

        # rightColSizer = wx.BoxSizer(wx.VERTICAL)
        # rightColSizer.Add(sliderSizer)

        # leftColSizer | midColSizer | rightColSizer

        legendSizer = wx.GridBagSizer(10, 10)

        # First row

        legendSizer.Add(self.scaleDisplay,
                        (0, 0), flag=wx.ALIGN_CENTER_VERTICAL)
        legendSizer.Add(labelSizer,
                        (0, 1), flag=wx.ALIGN_CENTER_HORIZONTAL|wx.ALIGN_CENTER_VERTICAL)
        legendSizer.Add(sliderSizer,
                        (0, 2), flag=wx.ALIGN_CENTER_VERTICAL)

        # Second row
        legendSizer.Add(self.hfwDisplay,
                         (1, 0))

        legendSizer.AddGrowableCol(1)

        #  Canvas
        # +------
        #  Legend Sizer

        # legend_panel_sizer is needed to add a border around the legend
        legend_panel_sizer = wx.BoxSizer(wx.VERTICAL)
        legend_panel_sizer.Add(legendSizer, 1, border=5, flag=wx.ALL|wx.EXPAND)
        legend_panel.SetSizerAndFit(legend_panel_sizer)

        mainSizer = wx.BoxSizer(wx.VERTICAL)

        mainSizer.Add(self.canvas, 1,
                border=2, flag=wx.EXPAND|wx.TOP|wx.LEFT|wx.RIGHT)
        mainSizer.Add(legend_panel, 0,
                border=2, flag=wx.EXPAND|wx.BOTTOM|wx.LEFT|wx.RIGHT)

        self.SetSizerAndFit(mainSizer)
        self.SetAutoLayout(True)

    def OnComboLeft(self, event):
        self.ChangeView(0, event.GetString())

    def OnComboRight(self, event):
        self.ChangeView(1, event.GetString())

    def OnSlider(self, event):
        """
        Merge ratio slider
        """
        log.error("pew")
        self.viewmodel.merge_ratio.value = self.mergeSlider.GetValue() / 100.0
        event.Skip()

    def avOnMergeRatio(self, val):
        # round is important because int can cause unstable value
        # int(0.58*100) = 57
        self.mergeSlider.SetValue(round(val * 100))


    # We link only one way the position:
    #  * if the user moves the view => moves the stage to the same position
    #  * if the stage moves by itself, keep the view at the same place
    #    (and the acquired images will not be centred anymore)
    def onViewCenter(self, pos):
        self.secom_model.stage_pos.value = pos

    def avOnMPP(self, mpp):
        self.scaleDisplay.SetMPP(mpp)
        self.UpdateHFW()

    def OnSize(self, event):
        event.Skip() # process also by the parent
        self.UpdateHFW()

    def UpdateHFW(self):
        """ Optional. Physical width of the display"""
        hfw = self.viewmodel.mpp.value * self.GetClientSize()[0]
        label = "HFW: %sm" % units.to_string_si_prefix(hfw)
        self.hfwDisplay.SetLabel(label)

#    # Change picture one/two
#    def SetImage(self, index, im, pos = None, mpp = None):
#        self.canvas.SetImage(index, im, pos, mpp)
#
    def AddView(self, view):
        self.views.append(view)

        # update the combo boxes
        for d in self.displays:
            d[1].Append(view.name)

    def ChangeView(self, display, viewName):
        """
        Select a view and update the legend with it
        If selecting a view already displayed on the other side, it will swap them
        If less than 2 non-empty views => slider is disabled
        display: index of the display to update
        viewName (string): the name of the view
        combo: the combobox which has to be updated
        sizer: the sizer containing the controls
        """
        # find the view
        view = None
        for v in self.views:
            if v.name == viewName:
                view = v
                break
        if not view:
            raise LookupError("Unknown view " + viewName)

        (prevView, combo, sizer) = self.displays[display]
        oppDisplay = 1 - display
        (oppView, oppCombo, oppSizer) = self.displays[oppDisplay]

        needSwap = ((oppView == view) and not isinstance(view, MicroscopeEmptyView))

        # Remove old view(s)
        prevView.Hide(combo, sizer)
        if needSwap:
            oppView.Hide(oppCombo, oppSizer)
            oppView = prevView

        # Show new view
        view.Show(combo, sizer, self.viewmodel.images[display])
        self.displays[display] = (view, combo, sizer)
        if needSwap:
            oppView.Show(oppCombo, oppSizer, self.viewmodel.images[oppDisplay])
            self.displays[oppDisplay] = (oppView, oppCombo, oppSizer)

        # Remove slider if not 2 views
        if isinstance(view, MicroscopeEmptyView) or isinstance(oppView, MicroscopeEmptyView):
            self.mergeSlider.Hide()
        else:
            self.mergeSlider.Show()

        # TODO: find out if that's the nice behaviour, or should just keep it?
        if needSwap:
            self.viewmodel.merge_ratio.value = (1.0 -  self.viewmodel.merge_ratio.value)

        assert(self.displays[0] != self.displays[1] or
               isinstance(self.displays[0], MicroscopeEmptyView))




class MicroscopeView(object):
    """
    Interface for defining a type of view from the microscope (such as CCD, SE...) with all
    its values in legend.
    """
    def __init__(self, name):
        """
        name (string): user friendly name
        """
        self.name = name #
        self.legendCtrl = [] # list of wx.Control to display in the legend
        self.outimage = None # ActiveValue of instrumental image
        self.inimage = InstrumentalImage(None, None, None) # instrumental image
        self.sizer = None

    def Hide(self, combo, sizer):
        # Remove and hide all the previous controls in the sizer
        for c in self.legendCtrl:
            sizer.Detach(c)
            c.Hide()

        # For spacers: everything else in the sizer
        for c in sizer.GetChildren():
            sizer.Remove(0)

        if self.outimage:
            self.outimage.value = self.inimage

        self.sizer = None

    def Show(self, combo, sizer, outimage):
        self.outimage = outimage
        self.UpdateImage()
        self.sizer = sizer

        # Put the new controls
        first = True
        for c in self.legendCtrl:
            if first:
                first = False
            else:
                sizer.AddStretchSpacer()
            sizer.Add(c, 0, wx.ALIGN_CENTER_HORIZONTAL|wx.LEFT|wx.RIGHT|wx.EXPAND, 3)
            c.Show()

        #Update the combobox
        combo.Selection = combo.FindString(self.name)

        sizer.Layout()

    def UpdateImage(self):
        if self.outimage:
            self.outimage.value = self.inimage

class MicroscopeEmptyView(MicroscopeView):
    """
    Special view containing nothing
    """
    def __init__(self, name="None"):
        MicroscopeView.__init__(self, name)

class MicroscopeImageView(MicroscopeView):
    def __init__(self, parent, iim, viewmodel, name="Image"):
        MicroscopeView.__init__(self, name)

        self.viewmodel = viewmodel
        self.LegendMag = wx.StaticText(parent)
        self.legendCtrl.append(self.LegendMag)

        iim.subscribe(self.avImage)
        viewmodel.mpp.subscribe(self.avMPP, True)

    def avImage(self, value):
        self.inimage = value
        # This method might be called from any thread
        # GUI can be updated only from the GUI thread, so just send an event
        wx.CallAfter(self.UpdateImage)
        wx.CallAfter(self.avMPP, None)

    def avMPP(self, unused):
        # TODO: shall we use the real density of the screen?
        # We could use real density but how much important is it?
        mppScreen = 0.00025 # 0.25 mm/px
        label = "Mag: "
        if self.inimage.mpp:
            magIm = mppScreen / self.inimage.mpp # as if 1 im.px == 1 sc.px
            if magIm >= 1:
                label += "×" + str(units.round_significant(magIm, 3))
            else:
                label += "/" + str(units.round_significant(1.0/magIm, 3))
            magDig =  self.inimage.mpp / self.viewmodel.mpp.value
            if magDig >= 1:
                label += " ×" + str(units.round_significant(magDig, 3))
            else:
                label += " /" + str(units.round_significant(1.0/magDig, 3))
        self.LegendMag.SetLabel(label)

        if self.sizer:
            self.sizer.Layout()

class MicroscopeOpticalView(MicroscopeImageView):
    def __init__(self, parent, datamodel, viewmodel, name="Optical"):
        MicroscopeImageView.__init__(self, parent, datamodel.optical_det_image,
                                     viewmodel, name)

        self.datamodel = datamodel
        self.viewmodel = viewmodel

        self.LegendWl = wx.StaticText(parent)
        self.LegendET = wx.StaticText(parent)
        self.legendCtrl += [self.LegendWl, self.LegendET]

        datamodel.optical_emt_wavelength.subscribe(self.avWavelength)
        datamodel.optical_det_wavelength.subscribe(self.avWavelength, True)
        datamodel.optical_det_exposure_time.subscribe(self.avExposureTime, True)

    def avWavelength(self, value):
        # need to know both wavelengthes, so just look into the values
        win = self.datamodel.optical_emt_wavelength.value
        wout = self.datamodel.optical_det_wavelength.value

        label = "Wavelength: " + str(win) + "nm/" + str(wout) + "nm"
        self.LegendWl.SetLabel(label)

    def avExposureTime(self, value):
        label = "Exposure: %ss" % units.to_string_si_prefix(value)
        self.LegendET.SetLabel(label)

class MicroscopeSEView(MicroscopeImageView):
    def __init__(self, parent, datamodel, viewmodel, name="SE Detector"):
        MicroscopeImageView.__init__(self, parent, datamodel.sem_det_image,
                                     viewmodel, name)

        self.datamodel = datamodel
        self.viewmodel = viewmodel

        self.LegendDwell = wx.StaticText(parent)
        self.LegendSpot = wx.StaticText(parent)
        self.LegendHV = wx.StaticText(parent)
        self.legendCtrl += [ self.LegendDwell, self.LegendSpot,
                           self.LegendHV]

        datamodel.sem_emt_dwell_time.subscribe(self.avDwellTime, True)
        datamodel.sem_emt_spot.subscribe(self.avSpot, True)
        datamodel.sem_emt_hv.subscribe(self.avHV, True)

    # TODO need to use the right dimensions for the units
    def avDwellTime(self, value):
        label = "Dwell: %ss" % units.to_string_si_prefix(value)
        self.LegendDwell.SetLabel(label)

    def avSpot(self, value):
        label = "Spot: %g" % value
        self.LegendSpot.SetLabel(label)

    def avHV(self, value):
        label = "HV: %sV" % units.to_string_si_prefix(value)
        self.LegendHV.SetLabel(label)

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: