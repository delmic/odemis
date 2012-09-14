#!/usr/bin/env python
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

import wx

from .util import units
from .log import log

from .dblmscopecanvas import DblMicroscopeCanvas
from .dblmscopeviewmodel import DblMscopeViewModel
from .comp.scalewindow import ScaleWindow
from .comp.slider import CustomSlider
from .img.data import getico_blending_optBitmap, getico_blending_semBitmap
from .microscopeview import MicroscopeEmptyView, MicroscopeOpticalView, \
    MicroscopeSEView

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
        self.legend_panel = wx.Panel(self)
        # put here so MicroscopeOpticalView will pick up on it
        self.legend_panel.SetForegroundColour("#BBBBBB")

        try:
            self.secom_model = wx.GetApp().secom_model
            self.viewmodel.opt_focus = self.secom_model.opt_focus
        except AttributeError:
            msg = "Could not find SECOM model"
            log.error(msg)
            return

        # Select the default views
        #self.ChangeView(0, self.views[1].name)
        #self.ChangeView(1, self.views[2].name)
        #FIXME: this is a BIG shortcut => need streams

        self.opt_view = MicroscopeOpticalView(self.legend_panel, self.secom_model, self.viewmodel)
        self.sem_view = MicroscopeSEView(self.legend_panel, self.secom_model, self.viewmodel)

        # Create all sub widgets used by this wx.Panel
        self._build_windows()

        self.sem_view.Show(self.viewmodel.images[0])
        self.opt_view.Show(self.viewmodel.images[1])

        # Control for the selection before AddView(), which needs them
        #self.viewComboLeft = wx.ComboBox(self, style=wx.CB_READONLY, size=(140, -1))
        #self.viewComboRight = wx.ComboBox(self, style=wx.CB_READONLY, size=(140, -1))

        #self.Bind(wx.EVT_COMBOBOX, self.OnComboLeft, self.viewComboLeft)
        #self.Bind(wx.EVT_COMBOBOX, self.OnComboRight, self.viewComboRight)


#        self.views = []
#        emptyView = MicroscopeEmptyView()
        # display : left and right view
        self.displays = []
        #[(emptyView, self.viewComboLeft, self.imageSizerBLeft),
        #                 (emptyView, self.viewComboRight, self.imageSizerBRight)]

        # TODO change to use streams: set of streams visible in this panel
        # can be called only with display ready
#        self.AddView(emptyView)
#        self.AddView(MicroscopeOpticalView(self, self.secom_model, self.viewmodel))
#        self.AddView(MicroscopeSEView(self, self.secom_model, self.viewmodel))



        # sync microscope stage with the view
        self.viewmodel.center.value = self.secom_model.stage_pos.value


        self.Bind(wx.EVT_CHILD_FOCUS, self.OnChildFocus)

        self._set_subscriptions()

        self.ShowBlendingSlider(True)
#        self.ShowBlendingSlider(self.canvas.ImageCount() > 0)

        self.Bind(wx.EVT_SIZE, self.OnSize)

    def _set_subscriptions(self):
        """ Set all needed subscriptions to VigilantAttributes """
        self.viewmodel.mpp.subscribe(self.avOnMPP, True)  #pylint: disable=E1101
        self.viewmodel.center.subscribe(self.onViewCenter)
        self.viewmodel.merge_ratio.subscribe(self.avOnMergeRatio, True) #pylint: disable=E1101


    def _build_windows(self):
        """ Construct and lay out all sub windows of this panel """

        self.canvas = DblMicroscopeCanvas(self)

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


        self.legend_panel.SetBackgroundColour("#1A1A1A")
        self.legend_panel.SetForegroundColour(self.GetForegroundColour())

        self.scaleDisplay = ScaleWindow(self.legend_panel)
        self.scaleDisplay.SetFont(font)


        #### Values

        # self.magni_label = wx.StaticText(self.legend_panel, wx.ID_ANY, "10x 10x")
        # self.magni_label.SetToolTipString("Magnification Optic Electron")
        # self.volta_label = wx.StaticText(self.legend_panel, wx.ID_ANY, "66 kV")
        # self.volta_label.SetToolTipString("Voltage")
        # self.dwell_label = wx.StaticText(self.legend_panel, wx.ID_ANY, "666 μs")
        # self.dwell_label.SetToolTipString("Dwell")

        # Merge icons will be grabbed from gui.img.data
        ##### Merge slider

        self.blendingSlider = CustomSlider(self.legend_panel,
                    wx.ID_ANY,
                    50,
                    (0, 100),
                    size=(100, 12),
                    style=wx.SL_HORIZONTAL | wx.SL_AUTOTICKS | wx.SL_TICKS)

        self.blendingSlider.SetBackgroundColour(self.legend_panel.GetBackgroundColour())
        self.blendingSlider.SetForegroundColour("#4d4d4d")
        #self.blendingSlider.SetLineSize(50)

        self.bmpIconOpt = wx.StaticBitmap(self.legend_panel, wx.ID_ANY, getico_blending_optBitmap())
        self.bmpIconSem = wx.StaticBitmap(self.legend_panel, wx.ID_ANY, getico_blending_semBitmap())

        self.blendingSlider.Bind(wx.EVT_LEFT_UP, self.OnSlider)

        ###################################
        # Optional legend widgets
        ###################################

        self.hfwDisplay = wx.StaticText(self.legend_panel) # Horizontal Full Width
        self.hfwDisplay.Hide()

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

        for c in self.opt_view.legend_controls:
            labelSizer.Add(c, flag=wx.RIGHT, border=20)

        # labelSizer.Add(self.magni_label, flag=wx.RIGHT, border=20)
        # labelSizer.Add(self.volta_label, flag=wx.RIGHT, border=20)
        # labelSizer.Add(self.dwell_label, flag=wx.RIGHT, border=20)

        # midColSizer = wx.BoxSizer(wx.VERTICAL)
        # midColSizer.Add(labelSizer, flag=wx.ALIGN_CENTER_VERTICAL)

        #  | Icon | Slider | Icon |
        # +-------
        #  (?????) empty for now

        self.sliderSizer = wx.BoxSizer(wx.HORIZONTAL)

        self.sliderSizer.Add(self.bmpIconOpt, flag=wx.RIGHT, border=3)
        self.sliderSizer.Add(self.blendingSlider, flag=wx.EXPAND)
        self.sliderSizer.Add(self.bmpIconSem, flag=wx.LEFT, border=3)

        # rightColSizer = wx.BoxSizer(wx.VERTICAL)
        # rightColSizer.Add(self.sliderSizer)

        # leftColSizer | midColSizer | rightColSizer

        legendSizer = wx.GridBagSizer(10, 10)

        # First row

        legendSizer.Add(labelSizer,
                        (0, 0), flag=wx.ALIGN_CENTER_VERTICAL)
        legendSizer.Add(self.scaleDisplay,
                        (0, 1), flag=wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_CENTER_HORIZONTAL)
        legendSizer.Add(self.sliderSizer,
                        (0, 2), flag=wx.ALIGN_CENTER_VERTICAL)

        # Second row
        legendSizer.Add(self.hfwDisplay, (1, 0))

        legendSizer.AddGrowableCol(1)

        #  Canvas
        # +------
        #  Legend Sizer

        # legend_panel_sizer is needed to add a border around the legend
        legend_panel_sizer = wx.BoxSizer(wx.VERTICAL)
        legend_panel_sizer.Add(legendSizer, 1, border=10, flag=wx.ALL|wx.EXPAND)
        self.legend_panel.SetSizerAndFit(legend_panel_sizer)

        mainSizer = wx.BoxSizer(wx.VERTICAL)

        mainSizer.Add(self.canvas, 1,
                border=2, flag=wx.EXPAND|wx.TOP|wx.LEFT|wx.RIGHT)
        mainSizer.Add(self.legend_panel, 0,
                border=2, flag=wx.EXPAND|wx.BOTTOM|wx.LEFT|wx.RIGHT)

        self.SetSizerAndFit(mainSizer)
        self.SetAutoLayout(True)


    ################################################
    ## Panel control
    ################################################

    def ShowBlendingSlider(self, show):
        # print self.sliderSizer.GetMinSize()
        # print self.sliderSizer.GetSize()
        self.bmpIconOpt.Show(show)
        self.blendingSlider.Show(show)
        self.bmpIconSem.Show(show)


    ## END Panel control


    ################################################
    ## Event handling
    ################################################

    def OnChildFocus(self, evt):
        self.SetFocus(True)
        evt.Skip()

    def HasFocus(self, *args, **kwargs):
        return self._has_focus == True

    def SetFocus(self, focus):   #pylint: disable=W0221
        #wx.Panel.SetFocus(self)
        self._has_focus = focus

        if focus:
            self.SetBackgroundColour("#127BA6")
        else:
            self.SetBackgroundColour("#000000")


    ## END Event handling

    def OnComboLeft(self, event):
        self.ChangeView(0, event.GetString())

    def OnComboRight(self, event):
        self.ChangeView(1, event.GetString())

    def OnSlider(self, event):
        """
        Merge ratio slider
        """
        self.viewmodel.merge_ratio.value = self.blendingSlider.GetValue() / 100.0
        event.Skip()

    def avOnMergeRatio(self, val):
        # round is important because int can cause unstable value
        # int(0.58*100) = 57
        self.blendingSlider.SetValue(round(val * 100))


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
#        FIXME: this is broken, and never to be used again!!
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
            self.blendingSlider.Hide()
        else:
            self.blendingSlider.Show()

        # TODO: find out if that's the nice behaviour, or should just keep it?
        if needSwap:
            self.viewmodel.merge_ratio.value = (1.0 -  self.viewmodel.merge_ratio.value)

        assert(self.displays[0] != self.displays[1] or
               isinstance(self.displays[0], MicroscopeEmptyView))






# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: