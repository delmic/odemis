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

from ..util import units
from ..log import log

from ..dblmscopecanvas import DblMicroscopeCanvas
from .scalewindow import ScaleWindow
from .slider import Slider
from ..img.data import getico_blending_optBitmap, getico_blending_semBitmap

class MicroscopeViewport(wx.Panel):
    """
    A draggable, flicker-free window class adapted to show pictures of two
    microscope simultaneously.

    """
    def __init__(self, *args, **kwargs):
        """
        Note: This is not fully initialised until setView() has been called
        """
        wx.Panel.__init__(self, *args, **kwargs)
        
        self.view = None # the MicroscopeView that this viewport is displaying (=model)
        
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
        
        ##### Scale window
        self.legend_panel = wx.Panel(self)
        self.legend_panel.SetBackgroundColour(self.GetBackgroundColour())
        self.legend_panel.SetForegroundColour(self.GetForegroundColour())

        self.scaleDisplay = ScaleWindow(self.legend_panel)
        self.scaleDisplay.SetFont(font)

        # Merge icons will be grabbed from gui.img.data
        ##### Merge slider
        # TODO should be possible to use VAConnector
        self.mergeSlider = Slider(self.legend_panel,
                    wx.ID_ANY,
                    50,
                    (0, 100),
                    size=(100, 12),
                    style=wx.SL_HORIZONTAL | wx.SL_AUTOTICKS | wx.SL_TICKS)

        self.mergeSlider.SetBackgroundColour(self.legend_panel.GetBackgroundColour())
        self.mergeSlider.SetForegroundColour("#4d4d4d")
        #self.mergeSlider.SetLineSize(50)

        self.bmpIconOpt = wx.StaticBitmap(self.legend_panel, wx.ID_ANY, getico_blending_optBitmap())
        self.bmpIconSem = wx.StaticBitmap(self.legend_panel, wx.ID_ANY, getico_blending_semBitmap())

        self.mergeSlider.Bind(wx.EVT_LEFT_UP, self.OnSlider)

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

#        for c in self.opt_view.legend_controls:
#            labelSizer.Add(c, flag=wx.RIGHT, border=20)


        #  | Icon | Slider | Icon |
        # +-------
        #  (?????) empty for now

        self.sliderSizer = wx.BoxSizer(wx.HORIZONTAL)

        self.sliderSizer.Add(self.bmpIconOpt, flag=wx.RIGHT, border=3)
        self.sliderSizer.Add(self.mergeSlider, flag=wx.EXPAND)
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

        self.Bind(wx.EVT_CHILD_FOCUS, self.OnChildFocus)
        self.Bind(wx.EVT_SIZE, self.OnSize)
        

    def setView(self, view):
        """
        Set the view that this viewport is displaying/representing
        Can be called only once, at initialisation.
        """
        # This is a kind of kludge, as it'd be best to have the viewport created
        # after the view, but they are created independently via xrc. 
        assert(self.view is None)
        
        self.view = view
        
        # TODO Center to current view position, with current mpp
        view.mpp.subscribe(self._onMPP, init=True)
        
        # set/subscribe merge ratio
        view.merge_ratio.subscribe(self._onMergeRatio, init=True)
        self.ShowMergeSlider(True) # FIXME: only if required by the view
        
        # canvas handles also directly some of the view properties
        self.canvas.setView(view)
        
    ################################################
    ## Panel control
    ################################################

    def ShowMergeSlider(self, show):
        # print self.sliderSizer.GetMinSize()
        # print self.sliderSizer.GetSize()
        self.bmpIconOpt.Show(show)
        self.mergeSlider.Show(show)
        self.bmpIconSem.Show(show)

    def HasFocus(self, *args, **kwargs):
        return self._has_focus == True

    def SetFocus(self, focus):   #pylint: disable=W0221
        #wx.Panel.SetFocus(self)
        self._has_focus = focus

        if focus:
            self.SetBackgroundColour("#127BA6")
        else:
            self.SetBackgroundColour("#000000")

    def UpdateHFW(self):
        """ Optional. Physical width of the display"""
        if self.view is None:
            return
        hfw = self.view.mpp.value * self.GetClientSize()[0]
        label = "HFW: %sm" % units.to_string_si_prefix(hfw)
        self.hfwDisplay.SetLabel(label)

    ## END Panel control

    ################################################
    ## VA handling
    ################################################
    
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
        if self.view is None:
            return
        
#        self.view.stage_pos.value = pos
        self.view.view_pos.value = pos
        self.view.moveStageToView()

    def _onMPP(self, mpp):
        self.scaleDisplay.SetMPP(mpp)
        # the MicroscopeView will send an event that the view has to be redrawn
        
    ################################################
    ## GUI Event handling
    ################################################

    def OnChildFocus(self, evt):
        # TODO need to do this:
#        if self.view:
#            self._microscope.currentView.value = self.view
        # instead of this:
        self.SetFocus(True)
        
        evt.Skip()

    def OnSlider(self, event):
        """
        Merge ratio slider
        """
        if self.view is None:
            return
        
        self.view.merge_ratio.value = self.mergeSlider.GetValue() / 100.0
        event.Skip()

    def OnSize(self, event):
        event.Skip() # process also by the parent
        self.UpdateHFW()

    ## END Event handling

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: