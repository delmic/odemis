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

from dblmscopecanvas import DblMicroscopeCanvas
from dblmscopeviewmodel import DblMscopeViewModel
from instrmodel import InstrumentalImage
from model import ActiveValue
from scalewindow import ScaleWindow
import units
import wx

CROSSHAIR_PEN = wx.GREEN_PEN
CROSSHAIR_SIZE = 16
class DblMicroscopePanel(wx.Panel):
    """
    A draggable, flicker-free window class adapted to show pictures of two
    microscope simultaneously.

    """
    def __init__(self, *args, **kwargs):
        wx.Panel.__init__(self, *args, **kwargs)
        
        parent = args[0]
        self.secom_model = parent.secom_model
        
        self.viewmodel = DblMscopeViewModel()
        self.canvas = DblMicroscopeCanvas(self)
        self.canvas.SetCrossHair(True)
        
        # The legend
        # Control for the selection before AddView(), which needs them
        self.viewComboLeft = wx.ComboBox(self, style=wx.CB_READONLY, size=(140,-1))
        self.viewComboRight = wx.ComboBox(self, style=wx.CB_READONLY, size=(140,-1))
        self.Bind(wx.EVT_COMBOBOX, self.OnComboLeft, self.viewComboLeft)
        self.Bind(wx.EVT_COMBOBOX, self.OnComboRight, self.viewComboRight)
        
        self.mergeSlider = wx.Slider(self, wx.ID_ANY, 50, 0, 100, size=(100, 30), style=wx.SL_HORIZONTAL | wx.SL_AUTOTICKS | wx.SL_TICKS)
        self.mergeSlider.SetLineSize(50)
        self.mergeSlider.Bind(wx.EVT_SLIDER, self.OnSlider)
        self.viewmodel.merge_ratio.bind(self.avOnMergeRatio, True)
        
        self.scaleDisplay = ScaleWindow(self)
        self.hfwDisplay = wx.StaticText(self, label="HFW: 156µm")
        lineDisplay = wx.StaticLine(self, style=wx.LI_VERTICAL)

        #                                      mainSizer
        #                    Canvas
        # legendSizer\/
        #|------scaleSizer---|---------------------imageSizer-----|
        #|                   l      imageSizerTop                 |
        #|-------------------l------------------------------------|
        #|                   l     imageSizerBottom               |
        #|                   l imageSizerBLeft l imageSizerBRight |
        #|-------------------|------------------------------------|
        mainSizer = wx.BoxSizer(wx.VERTICAL)
        legendSizer = wx.BoxSizer(wx.HORIZONTAL)
        scaleSizer = wx.BoxSizer(wx.VERTICAL)
        scaleSizer.Add(self.scaleDisplay, 1, wx.ALIGN_CENTER|wx.EXPAND)
        scaleSizer.Add(self.hfwDisplay, 1, wx.ALIGN_CENTER|wx.EXPAND)
        self.viewmodel.mpp.bind(self.avOnMPP, True)
        
        imageSizer = wx.BoxSizer(wx.VERTICAL)
        imageSizerTop = wx.BoxSizer(wx.HORIZONTAL)
        imageSizerBottom = wx.BoxSizer(wx.HORIZONTAL)
        imageSizer.Add(imageSizerTop, 1, wx.ALIGN_CENTER|wx.EXPAND)
        imageSizer.Add(imageSizerBottom, 1, wx.ALIGN_CENTER|wx.EXPAND)

        imageSizerTop.Add(self.viewComboLeft, 0, wx.ALIGN_CENTER)
        imageSizerTop.AddStretchSpacer()
        imageSizerTop.Add(self.mergeSlider, 2, wx.ALIGN_CENTER|wx.LEFT|wx.RIGHT, 3)
        imageSizerTop.AddStretchSpacer()
        imageSizerTop.Add(self.viewComboRight, 0, wx.ALIGN_CENTER)
        

        self.imageSizerBLeft = wx.BoxSizer(wx.HORIZONTAL)
        self.imageSizerBRight = wx.BoxSizer(wx.HORIZONTAL)
        # because the statictexts cannot be vertically centered
        sizervbl = wx.BoxSizer(wx.VERTICAL)
        sizervbl.AddStretchSpacer()
        sizervbl.Add(self.imageSizerBLeft, 0, wx.ALIGN_CENTER|wx.EXPAND)
        sizervbl.AddStretchSpacer()
        sizervbr = wx.BoxSizer(wx.VERTICAL)
        sizervbr.AddStretchSpacer()
        sizervbr.Add(self.imageSizerBRight, 0, wx.ALIGN_CENTER|wx.EXPAND)
        sizervbr.AddStretchSpacer()
        imageSizerBottom.Add(sizervbl, 1, wx.ALIGN_CENTER|wx.EXPAND)
        imageSizerBottom.Add(lineDisplay, 0, wx.ALIGN_CENTER|wx.EXPAND)
        imageSizerBottom.Add(sizervbr, 1, wx.ALIGN_CENTER|wx.EXPAND)
        
        line = wx.StaticLine(self, style=wx.LI_VERTICAL)
        legendSizer.Add(scaleSizer, 1, wx.ALIGN_CENTER|wx.LEFT|wx.RIGHT|wx.EXPAND, 3)
        legendSizer.Add(line, 0, wx.ALIGN_CENTER|wx.EXPAND)
        legendSizer.Add(imageSizer, 3, wx.ALIGN_CENTER|wx.EXPAND)
        mainSizer.Add(self.canvas, 10, wx.EXPAND)
        mainSizer.Add(legendSizer, 0, wx.EXPAND) # 0 = fixed minimal size
        
        emptyView = MicroscopeEmptyView()
        # display : left and right view
        self.displays = [(emptyView, self.viewComboLeft, self.imageSizerBLeft),
                         (emptyView, self.viewComboRight, self.imageSizerBRight)]

        # can be called only with display ready
        self.views = []
        self.AddView(emptyView)
        self.AddView(MicroscopeOpticalView(self, self.secom_model))
        self.AddView(MicroscopeSEView(self, self.secom_model))
        
        # Select the default views
        self.ChangeView(0, self.views[1].name)
        self.ChangeView(1, self.views[2].name)
    
        self.SetSizer(mainSizer)
        self.SetAutoLayout(True)
        mainSizer.Fit(self)
  
    def OnComboLeft(self, event):
        self.ChangeView(0, event.GetString())
        
    def OnComboRight(self, event):
        self.ChangeView(1, event.GetString())
    
    def OnSlider(self, event):
        """
        Merge ratio slider
        """
        self.viewmodel.merge_ratio.value = self.mergeSlider.GetValue() / 100.0
    
    def avOnMergeRatio(self, val):
        # round is important because int can cause unstable value
        # int(0.58*100) = 57
        self.mergeSlider.SetValue(round(val * 100))
    
    def avOnMPP(self, mpp):
        self.scaleDisplay.SetMPP(mpp)
        
        hfw = mpp * self.GetClientSize()[0]
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
        self.legendCtrl = {} # list of wx.Control to display in the legend
        self.canvas = None
        self.canvas_index = None # index (int) # TODO use active value
        self.outimage = None # ActiveValue of instrumental image
        self.inimage = InstrumentalImage(None, None, None) # instrumental image
    
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

    def Show(self, combo, sizer, outimage):
        self.outimage = outimage
        self.UpdateImage()
        
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

class MicroscopeOpticalView(MicroscopeView):
    def __init__(self, parent, datamodel, name="Optical"):
        MicroscopeView.__init__(self, name)
        
        self.datamodel = datamodel
        
        self.LegendMag = wx.StaticText(parent, label="Mag: ×680×4")
        self.LegendWl = wx.StaticText(parent)
        self.LegendET = wx.StaticText(parent)
        self.legendCtrl = [self.LegendMag, self.LegendWl, self.LegendET]
        
        datamodel.optical_emt_wavelength.bind(self.avWavelength)
        datamodel.optical_det_wavelength.bind(self.avWavelength, True)
        datamodel.optical_det_exposure_time.bind(self.avExposureTime, True)
        datamodel.optical_det_image.bind(self.avImage)
        
    def avWavelength(self, value):
        # need to know both wavelengthes, so just look into the values
        win = self.datamodel.optical_emt_wavelength.value
        wout = self.datamodel.optical_det_wavelength.value
        
        label = "Wavelength: " + str(win) + "nm/" + str(wout) + "nm"
        self.LegendWl.SetLabel(label)
    
    def avExposureTime(self, value):
        label = "Exposure: %ss" % units.to_string_si_prefix(value)
        self.LegendET.SetLabel(label)
        
    def avImage(self, value):
        self.inimage = value
        self.UpdateImage()

class MicroscopeSEView(MicroscopeView):
    def __init__(self, parent, datamodel, name="SE Detector"):
        MicroscopeView.__init__(self, name)
        
        self.datamodel = datamodel
                        
        self.LegendMag = wx.StaticText(parent, label="Mag: ×2600")
        self.LegendDwell = wx.StaticText(parent)
        self.LegendSpot = wx.StaticText(parent)
        self.LegendHV = wx.StaticText(parent)
        self.legendCtrl = [self.LegendMag, self.LegendDwell, self.LegendSpot,
                           self.LegendHV]
        
        datamodel.sem_emt_dwell_time.bind(self.avDwellTime, True)
        datamodel.sem_emt_spot.bind(self.avSpot, True)
        datamodel.sem_emt_hv.bind(self.avHV, True)
        datamodel.sem_det_image.bind(self.avImage)
        
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
        
    def avImage(self, value):
        self.inimage = value
        self.UpdateImage()
        
        

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: