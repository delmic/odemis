# -*- coding: utf-8 -*-

""" This module contains classes needed to construct stream panels.

Stream panels are custom, specialized controls that allow the user to view and
manipulate various data streams coming from the microscope.


@author: Rinze de Laat

Copyright © 2012 Rinze de Laat, Delmic

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

import collections

import wx
import wx.combo
import wx.lib.newevent

import odemis.gui.img.data as img

from odemis.gui.log import log
from .buttons import ImageButton, ImageToggleButton, \
    ImageTextToggleButton, ColourButton, PopupImageButton
from .text import SuggestTextCtrl, UnitIntegerCtrl
from .foldpanelbar import FoldPanelItem
from .slider import UnitIntegerSlider
from odemis.gui.util.conversion import wave2hex
from odemis.gui.img.data import getemptyBitmap
from odemis.gui import instrmodel

TEST_STREAM_LST = ["Aap", u"nöot", "noot", "mies", "kees", "vuur",
                  "quantummechnica", "Repelsteeltje", "", "XXX", "a", "aa",
                  "aaa", "aaaa", "aaaaa", "aaaaaa", "aaaaaaa"]

stream_remove_event, EVT_STREAM_REMOVE = wx.lib.newevent.NewEvent()

CAPTION_PADDING_RIGHT = 10
SCROLLBAR_WIDTH = 0

class Expander(wx.PyControl):
    """ An Expander is a header/button control at the top of a StreamPanelEntry.
    It provides a means to expand or collapse the StreamPanelEntry, as well as a
    label and various buttons offering easy access to much used functionality.

    Structure:

        + Expander
        |-- ImageButton     (remove stream button)
        |-- StaticText / SuggestTextCtrl (stream label)
        |-- ImageToggleButton (show/hide button)
        |-- ImageToggleButton (capture/pause button)

    The triangular fold icons are drawn in a separate routine.

    """

    def __init__(self, parent, stream, wid=wx.ID_ANY, pos=wx.DefaultPosition,
                 size=wx.DefaultSize, style=wx.NO_BORDER):
        wx.PyControl.__init__(self, parent, wid, pos, size, style)

        # This style *needs* to be set on MS Windows
        self.SetBackgroundStyle(wx.BG_STYLE_CUSTOM)

        self._parent = parent
        self._stream = stream
        self._label = stream.name.value
        self._label_ctrl = None

        self._label_color = parent.GetForegroundColour()

        self.Bind(wx.EVT_SIZE, self.OnSize)

        # ===== Fold icons

        self._foldIcons = wx.ImageList(16, 16)
        self._foldIcons.Add(img.getarr_down_sBitmap())
        self._foldIcons.Add(img.getarr_right_sBitmap())

        # ===== Remove button

        self._btn_rem = ImageButton(self, -1, img.getico_rem_strBitmap(), (10, 8), (18, 18),
                                    background_parent=parent)
        self._btn_rem.SetBitmaps(img.getico_rem_str_hBitmap())
        self._btn_rem.SetToolTipString("Remove stream")

        # ===== Visibility button

        self._btn_vis = ImageToggleButton(self, -1, img.getico_eye_closedBitmap(), (10, 8),
                                          (18, 18), background_parent=parent)
        self._btn_vis.SetBitmaps(img.getico_eye_closed_hBitmap(), img.getico_eye_openBitmap(), img.getico_eye_open_hBitmap())
        self._btn_vis.SetToolTipString("Show stream")

        # ===== Play button

        self._btn_play = ImageToggleButton(self, -1, img.getico_pauseBitmap(), (10, 8),
                                           (18, 18), background_parent=parent)
        self._btn_play.SetBitmaps(img.getico_pause_hBitmap(), img.getico_playBitmap(), img.getico_play_hBitmap())
        self._btn_play.SetToolTipString("Update stream")


        # Create and add sizer and populate with controls
        self._sz = wx.BoxSizer(wx.HORIZONTAL)

        self._sz.Add(self._btn_rem, 0, wx.ALL | wx.ALIGN_CENTRE_VERTICAL, 8)
        self._sz.AddStretchSpacer(1)
        self._sz.Add(self._btn_vis, 0, wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL, 8)
        self._sz.Add(self._btn_play, 0, wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL, 72)

        # add a colour button if the stream has a "tint" VA
        if hasattr(stream, "tint"):
            self._btn_color = ColourButton(self, -1,
                                           bitmap=getemptyBitmap(),
                                           size=(18,18),
                                           colour=stream.tint.value,
                                           background_parent=parent,
                                           use_hover=True)
            self._btn_color.SetToolTipString("Select colour")
            self._sz.Insert(2, self._btn_color, 0,
                            wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL, 8)
            self._btn_color.Bind(wx.EVT_BUTTON, self.on_color_click)
            stream.tint.subscribe(self.onStreamTint)

        self.SetSizer(self._sz)
        self._sz.Fit(self)
        self.Layout()

    def onStreamTint(self, colour):
        """ Update the colour button to reflect the provided colour """
        self._btn_color.set_colour(colour)
    
    def on_color_click(self, evt):
        # Remove the hover effect
        self._btn_color.OnLeave(evt)

        # set default colour to the current value
        cldata = wx.ColourData()
        cldata.SetColour(wx.Colour(*self._stream.tint.value))
        
        dlg = wx.ColourDialog(self, cldata)
        
        if dlg.ShowModal() == wx.ID_OK:
            colour = dlg.ColourData.GetColour().Get() # convert to a 3-tuple
            log.debug("Colour %r selected", colour)
            # this will automatically update the button's colour
            self._stream.tint.value = colour
            
    def DoGetBestSize(self, *args, **kwargs):
        """ Return the best size, which is the width of the parent and the
        height or the content (determined through the sizer).
        """
        width, dummy = self._parent.GetSize()
        dummy, height = self._sz.GetSize()

        return wx.Size(width, height)

    def OnSize(self, event):
        """ Handles the wx.EVT_SIZE event for the Expander class.
        :param `event`: a `wx.SizeEvent` event to be processed.
        """
        width = self._parent.GetSize().GetWidth()
        self.SetSize((width, -1))
        self.Layout()
        self.Refresh()

    def OnDrawExpander(self, dc):
        """ This method draws the expand/collapse icons.
        It needs to be called from the parent's paint event
        handler.
        """
        global SCROLLBAR_WIDTH
        SCROLLBAR_WIDTH = wx.SystemSettings_GetMetric(wx.SYS_VSCROLL_X) - 3

        wndRect = self.GetRect()
        drw = wndRect.GetRight() - 16 - CAPTION_PADDING_RIGHT - SCROLLBAR_WIDTH
        self._foldIcons.Draw(self._parent._collapsed, dc, drw,
                             (wndRect.GetHeight() - 16) / 2,
                             wx.IMAGELIST_DRAW_TRANSPARENT)

class FixedExpander(Expander):
    """ Expander for FixedStreamPanelEntries """

    def __init__(self, parent, stream, wid=wx.ID_ANY):
        Expander.__init__(self, parent, stream, wid)

        self._label_ctrl = wx.StaticText(self, -1, stream.name.value)
        self._sz.Remove(1)
        self._sz.Insert(1, self._label_ctrl, 1,
                        wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL, 8)



class CustomExpander(Expander):
    """ Expander for CustomStreamPanelEntries """

    def __init__(self, parent, stream, wid=wx.ID_ANY):
        Expander.__init__(self, parent, stream, wid)

        # We change afterward the label text into a full text entry
        self._label_ctrl = SuggestTextCtrl(self, id= -1, value=stream.name.value)
        # TODO link with the dye database
        self._label_ctrl.SetChoices(TEST_STREAM_LST)
        self._label_ctrl.SetBackgroundColour(self.Parent.GetBackgroundColour())
        self._label_ctrl.SetForegroundColour("#2FA7D4")
        
        # TODO make sure it changes the value of stream.name when it is updated
        self._label_ctrl.Bind(wx.EVT_TEXT_ENTER, self._onLabelChange)
        
        self._sz.Remove(1)
        self._sz.Insert(1, self._label_ctrl, 1,
                        wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL, 8)
        
    # GUI event handlers
    def _onLabelChange(self, evt):
        self._stream.name.value = self._label_ctrl.GetValue()
        
class StreamPanelEntry(wx.PyPanel):
    """ The StreamPanelEntry super class, a special case collapsible pane.

    The StreamPanelEntry consists of the following widgets:

        + StreamPanelEntry
        |--- FixedExpander
        |--+ Panel
           |-- ImageTextToggleButton
           |-- StaticText (contrast)
           |-- Slider
           |-- IntegerTextCtrl
           |-- StaticText (brightness)
           |-- Slider
           |-- IntegerTextCtrl
           |-- StaticText (excitation)
           |-- UnitIntegerCtrl
           |-- StaticText (emission)
           |-- UnitIntegerCtrl

    The expander_class class attribute contains the Expander subclass to be
    used when constructing an object.

    Most of the component's construction is done in the finalize() method, so
    we can allow for a delay. This is necessary when construction the component
    through an XML handler.
    
    It tries to represent the stream object as well as possible, so do not shows
    controls if the vigilant attributes are not there. 
    """

    expander_class = FixedExpander

    def __init__(self, parent, stream, wid=wx.ID_ANY, 
                 pos=wx.DefaultPosition, size=wx.DefaultSize,
                 style=wx.CP_DEFAULT_STYLE, agwStyle=0,
                 validator=wx.DefaultValidator, name="CollapsiblePane",
                 collapsed=True):
        """
        stream (Stream): the data model to be displayed (and modified by the user)
        """

        wx.PyPanel.__init__(self, parent, wid, pos, size, style, name)

        self.SetBackgroundColour("#4D4D4D")
        self.SetForegroundColour("#DDDDDD")
        self.stream = stream
        self._collapsed = True
        self._agwStyle = agwStyle | wx.CP_NO_TLW_RESIZE #|wx.CP_GTK_EXPANDER


        self._panel = wx.Panel(self, style=wx.TAB_TRAVERSAL | wx.NO_BORDER)
        self._panel.Hide()

        self._gbs = wx.GridBagSizer(3, 5)
        self._panel.SetSizer(self._gbs)

        self._expander = None

        self._sz = wx.BoxSizer(wx.HORIZONTAL)

        self.Bind(wx.EVT_SIZE, self.OnSize)

        # Process our custom 'collapsed' parameter.
        if not collapsed:
            self.collapse(collapsed)

    # ==== Event Handlers

    def on_remove(self, evt):
        log.debug("Removing stream panel '%s'", self.stream.name.value)
        fpb_item = self.Parent
        self.Destroy()
        fpb_item.Layout()

    def on_visibility(self, evt):
        # TODO need to let the currently focused view know (via view controller?)
        if self._expander._btn_vis.GetToggle():
            log.debug("Showing stream '%s'", self.stream.name.value)
            # FIXME how to get the ref?
            #self.micgui.currentView.value.addStream(self.stream)
        else:
            log.debug("Hiding stream '%s'", self.stream.name.value)
            #self.micgui.currentView.value.removeStream(self.stream)

    def on_play(self, evt):
        if self._expander._btn_play.GetToggle():
            log.debug("Activating stream '%s'", self.stream.name.value)
        else:
            log.debug("Pausing stream '%s'", self.stream.name.value)
        self.stream.updated.value = self._expander._btn_play.GetToggle()

    # END ==== Event Handlers

    def collapse(self, collapse=True):
        """ Collapses or expands the pane window.
        """

        if self._collapsed == collapse:
            return

        self.Freeze()

        # update our state
        self._panel.Show(not collapse)
        self._collapsed = collapse

        wx.CallAfter(self.Parent.FitStreams)

        self.Thaw()

        # update button label
        # NB: this must be done after updating our "state"
        #self._expander.SetLabel(self._label)

        #self.on_status_change(self.GetBestSize())

    def finalize(self):
        """ This method builds all the child controls
        A delay was needed in order for all the settings to be loaded from the
        XRC file (i.e. Font and background/foreground colours).
        """

        # ====== Add an expander button

        self.set_expander_button(self.expander_class(self, self.stream))
        self._sz.Add(self._expander, 0, wx.EXPAND)


        self._expander.Bind(wx.EVT_PAINT, self.on_draw_expander)
        if wx.Platform == "__WXMSW__":
            self._expander.Bind(wx.EVT_LEFT_DCLICK, self.on_button)

        self._expander.Bind(wx.EVT_LEFT_UP, self.OnToggle)

        # ====== Build panel controls

        self._panel.SetBackgroundColour(self.GetBackgroundColour())
        self._panel.SetForegroundColour("#DDDDDD")
        self._panel.SetFont(self.GetFont())

        # ====== Top row, auto contrast toggle button

        self._btn_auto_contrast = ImageTextToggleButton(self._panel, -1,
                                                img.getbtn_contrastBitmap(),
                                                label="Auto",
                                                size=(68, 26),
                                                style=wx.ALIGN_RIGHT)
        self._btn_auto_contrast.SetBitmaps(
                                        bmp_h=img.getbtn_contrast_hBitmap(),
                                        bmp_sel=img.getbtn_contrast_aBitmap())
        self._btn_auto_contrast.SetForegroundColour("#000000")
        self._gbs.Add(self._btn_auto_contrast, (0, 0), flag=wx.LEFT, border=34)


        # ====== Second row, brightness label, slider and value
        lbl_brightness = wx.StaticText(self._panel, -1, "brightness:")
        self._gbs.Add(lbl_brightness, (1, 0),
                      flag=wx.LEFT | wx.ALIGN_CENTRE_VERTICAL,
                      border=34)

        # FIXME: we need to ensure it's possible to have a value == 0 (and not just 1/201)
        self._sld_brightness = UnitIntegerSlider(
                              self._panel,
                              value=self.stream.brightness.value,
                              val_range=self.stream.brightness.range,
                              t_size=(40,-1),
                              unit="",
                              name="brightness_slider")

        self._gbs.Add(self._sld_brightness, (1, 1), flag=wx.EXPAND)

        # ====== Third row, brightness label, slider and value

        lbl_contrast = wx.StaticText(self._panel, -1, "contrast:")
        self._gbs.Add(lbl_contrast, (2, 0),
                      flag=wx.LEFT | wx.ALIGN_CENTRE_VERTICAL, border=34)

        self._sld_contrast = UnitIntegerSlider(
                             self._panel,
                             value=self.stream.contrast.value,
                             val_range=self.stream.contrast.range,
                             t_size=(40,-1),
                             unit="",
                             name="contrast_slider")

        self._gbs.Add(self._sld_contrast, (2, 1), flag=wx.EXPAND)

        self._gbs.AddGrowableCol(1)

        # ==== Bind events

        # Expander

        self._expander._btn_rem.Bind(wx.EVT_BUTTON, self.on_remove)
        self._expander._btn_vis.Bind(wx.EVT_BUTTON, self.on_visibility)
        self._expander._btn_play.Bind(wx.EVT_BUTTON, self.on_play)
        self.stream.updated.subscribe(self.onUpdatedChanged, init=True)

        # Panel controls
        # TODO reuse VigilantAttributeConnector, or at least refactor 
        
        self._sld_brightness.Bind(wx.EVT_MOTION, self.on_brightness_slide)
        self._sld_brightness.Bind(wx.EVT_LEFT_UP, self.on_brightness_slide)
        # self._txt_brightness.Bind(wx.EVT_TEXT_ENTER, self.on_brightness_entered)
        # self._txt_brightness.Bind(wx.EVT_CHAR, self.on_brightness_key)

        self._sld_contrast.Bind(wx.EVT_MOTION, self.on_contrast_slide)
        self._sld_contrast.Bind(wx.EVT_LEFT_UP, self.on_contrast_slide)
        # self._txt_contrast.Bind(wx.EVT_TEXT_ENTER, self.on_contrast_entered)
        # self._txt_contrast.Bind(wx.EVT_CHAR, self.on_contrast_key)

        self._btn_auto_contrast.Bind(wx.EVT_BUTTON, self.on_toggle_autocontrast)

        self._btn_auto_contrast.SetToggle(self.stream.auto_bc.value)
        self.on_toggle_autocontrast(None) # to ensure the controls are disabled if necessary


        if hasattr(self.stream, "excitation"):
            # Warning: stream.excitation is in m, we present everything in nm 
            lbl_excitation = wx.StaticText(self._panel, -1, "excitation:")
            self._gbs.Add(lbl_excitation, (3, 0),
                          flag=wx.LEFT | wx.ALIGN_CENTRE_VERTICAL, border=34)
    
            # TODO use the range of the VA
            self._txt_excitation = UnitIntegerCtrl(self._panel, -1, 
                    int(round(self.stream.excitation.value * 1e9)),
                    style=wx.NO_BORDER,
                    size=(50, -1), min_val=200, max_val=1000, unit='nm')
            self._txt_excitation.SetForegroundColour("#2FA7D4")
            self._txt_excitation.SetBackgroundColour(self.GetBackgroundColour())
    
            self._txt_excitation.Bind(wx.EVT_TEXT, self.on_excitation_text)
    
            self._gbs.Add(self._txt_excitation, (3, 1),
                          flag=wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT,
                          border=10)
            # TODO: is button a good choice? the user cannot click it, it's just
            # to show the wavelength
            self._btn_excitation = ColourButton(self._panel, -1,
                                bitmap=getemptyBitmap(),
                                size=(18,18),
                                colour=wave2hex(self.stream.excitation.value),
                                background_parent=self._panel)
            self._btn_excitation.SetToolTipString("Wavelength colour")
    
            self._gbs.Add(self._btn_excitation, (3, 2),
                          flag=wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT,
                          border=10)
            
            # TODO also a label for warnings


        if hasattr(self.stream, "emission"):
            lbl_emission = wx.StaticText(self._panel, -1, "emission:")
            self._gbs.Add(lbl_emission, (4, 0),
                          flag=wx.LEFT | wx.ALIGN_CENTRE_VERTICAL, border=34)
    
            self._txt_emission = UnitIntegerCtrl(self._panel, -1, 
                    int(round(self.stream.emission.value * 1e9)),
                    style=wx.NO_BORDER,
                    size=(50, -1), min_val=200, max_val=1000, unit='nm')
            self._txt_emission.SetForegroundColour("#2FA7D4")
            self._txt_emission.SetBackgroundColour(self.GetBackgroundColour())
    
            self._txt_emission.Bind(wx.EVT_TEXT, self.on_emission_text)
    
            self._gbs.Add(self._txt_emission, (4, 1),
                          flag=wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT,
                          border=10)
    
            self._btn_emission = ColourButton(self._panel, -1,
                                              bitmap=getemptyBitmap(),
                                              size=(18,18),
                                              colour=wave2hex(self.stream.emission.value),
                                              background_parent=self._panel)
            self._btn_emission.SetToolTipString("Wavelength colour")
    
            self._gbs.Add(self._btn_emission, (4, 2),
                          flag=wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT,
                          border=10)
    
    
    
    # VA subscriptions: reflect the changes on the stream to the GUI 
    def onUpdatedChanged(self, updated):
        self._expander._btn_play.SetToggle(self.stream.updated.value)


    # GUI events: update the stream when the user changes the values
        
    def on_toggle_autocontrast(self, evt):
        enabled = self._btn_auto_contrast.GetToggle()
        # disable the manual controls if it's on
        ctrl_enabled = not enabled
        self._sld_brightness.Enable(ctrl_enabled)
        # self._txt_brightness.Enable(ctrl_enabled)
        self._sld_contrast.Enable(ctrl_enabled)
        # self._txt_contrast.Enable(ctrl_enabled)

        self.stream.auto_bc.value = enabled

    def on_brightness_key(self, evt):
        key = evt.GetKeyCode()

        if key in (wx.WXK_UP, wx.WXK_DOWN):
            # self._sld_brightness.SetValue(self._txt_brightness.GetValue())
            # self._stream.optical_brightness.value = self._txt_brightness.GetValue()
            pass

        evt.Skip()

    # TODO just change the stream. the slider should automatically follow the stream
    def on_brightness_entered(self, evt):
        #self._sld_brightness.SetValue(int(self._txt_brightness.GetValue()))
        #self.stream.brightness.value = self._txt_brightness.GetValue()
        evt.Skip()

    def on_brightness_slide(self, evt):
        if self._sld_brightness.HasCapture():
            # self._txt_brightness.SetValue(self._sld_brightness.GetValue())
            self.stream.brightness.value = self._sld_brightness.GetValue()
        evt.Skip()

    def on_contrast_entered(self, evt):
        #self._sld_contrast.SetValue(int(self._txt_contrast.GetValue()))
        #self._stream.optical_contrast.value = self._txt_contrast.GetValue()
        evt.Skip()

    def on_contrast_slide(self, evt):
        if self._sld_contrast.HasCapture():
            # self._txt_contrast.SetValue(self._sld_contrast.GetValue())
            self.stream.contrast.value = self._sld_contrast.GetValue()
        evt.Skip()

    def on_contrast_key(self, evt):
        key = evt.GetKeyCode()

        if key in (wx.WXK_UP, wx.WXK_DOWN):
            # self._sld_contrast.SetValue(self._txt_contrast.GetValue())
            # self.stream.contrast.value = self._txt_contrast.GetValue()
            pass

        evt.Skip()

    def on_excitation_text(self, evt):
        log.debug("Excitation changed")
        obj = evt.GetEventObject()
        self.stream.excitation.value = obj.GetValue() * 1e-9
        
        colour = wave2hex(self.stream.excitation.value)
        log.debug("Changing colour to %s", colour)
        self._btn_excitation.set_colour(colour)
        
    def on_emission_text(self, evt):
        log.debug("Emission changed")
        obj = evt.GetEventObject()
        self.stream.emission.value = obj.GetValue() * 1e-9
        
        colour = wave2hex(self.stream.emission.value)
        log.debug("Changing colour to %s", colour)
        self._btn_emission.set_colour(colour)

        # changing emission should also change the tint
        self.stream.tint.value = colour
        
    def OnToggle(self, evt):
        """ Toggle the StreamPanelEntry

        Only toggle the view when the click was within the right 15% of the
        Expander, otherwise ignore it.
        """
        w = evt.GetEventObject().GetSize().GetWidth()

        if evt.GetX() > w * 0.85:
            self.collapse(not self._collapsed)
            # this change was generated by the user - send the event
            #ev = wx.CollapsiblePaneEvent(self, self.GetId(), self._collapsed)
            #self.GetEventHandler().ProcessEvent(ev)

        evt.Skip()

    def OnSize(self, event):
        """ Handles the wx.EVT_SIZE event for StreamPanelEntry
        """
        self.Layout()

    def on_button(self, event):
        """ Handles the wx.EVT_BUTTON event for StreamPanelEntry
        """

        if event.GetEventObject() != self._expander:
            event.Skip()
            return

        self.collapse(not self._collapsed)

        # this change was generated by the user - send the event
        #ev = wx.CollapsiblePaneEvent(self, self.GetId(), self._collapsed)
        #self.GetEventHandler().ProcessEvent(ev)

    def get_panel(self):
        """ Returns a reference to the pane window. Use the returned `wx.Window`
        as the parent of widgets to make them part of the collapsible area.
        """
        return self._panel

    def set_expander_button(self, button):
        """ Assign a new expander button to the stream panel.
        """

        if self._expander:
            self._sz.Replace(self._expander, button)
            self.Unbind(wx.EVT_BUTTON, self._expander)
            self._expander.Destroy()

        self._expander = button
        self.SetLabel(button.GetLabel())
        self.Bind(wx.EVT_BUTTON, self.on_button, self._expander)

        if self._panel:
            self._expander.MoveBeforeInTabOrder(self._panel)
        self.Layout()


    def get_button(self):
        """ Returns the button associated with StreamPanelEntry. """
        return self._expander

    def on_draw_expander(self, event):
        """ Handles the ``wx.EVT_PAINT`` event for the stream panel.
        :note: This is a drawing routine to paint the GTK-style expander.
        """

        dc = wx.AutoBufferedPaintDC(self._expander)
        dc.SetBackground(wx.Brush(self.GetBackgroundColour()))
        dc.Clear()

        self._expander.OnDrawExpander(dc)

    def Layout(self, *args, **kwargs):
        """ Layout the StreamPanelEntry. """

        if not self._expander or not self._panel or not self._sz:
            return False     # we need to complete the creation first!

        oursz = self.GetSize()

        # move & resize the button and the static line
        self._sz.SetDimension(0, 0, oursz.GetWidth(),
                              self._sz.GetMinSize().GetHeight())
        self._sz.Layout()

        if not self._collapsed:
            # move & resize the container window
            yoffset = self._sz.GetSize().GetHeight()
            self._panel.SetDimensions(0, yoffset, oursz.x, oursz.y - yoffset)

            # this is very important to make the pane window layout show
            # correctly
            self._panel.Show()
            self._panel.Layout()

        return True

    def DoGetBestSize(self, *args, **kwargs):
        """ Gets the size which best suits the window: for a control, it would
        be the minimal size which doesn't truncate the control, for a panel -
        the same size as it would have after a call to `Fit()`.
        """

        # do not use GetSize() but rather GetMinSize() since it calculates
        # the required space of the sizer
        sz = self._sz.GetMinSize()

        # when expanded, we need more space
        if not self._collapsed:
            pbs = self._panel.GetBestSize()
            sz.width = max(sz.GetWidth(), pbs.x)
            sz.height = sz.y + pbs.y

        return sz

    def Destroy(self, *args, **kwargs):
        """
        Delete the widget from the GUI
        """
        fpb_item = self.Parent
        wx.PyPanel.Destroy(self, *args, **kwargs)
        fpb_item.FitStreams()

    # def Layout(self):
    #     pcp.PyCollapsiblePane.Layout(self)
    #     #self._panel.SetBackgroundColour(self.GetBackgroundColour())


class FixedStreamPanelEntry(StreamPanelEntry): #pylint: disable=R0901
    """ A pre-defined stream panel """

    expander_class = FixedExpander

    def __init__(self, *args, **kwargs):
        StreamPanelEntry.__init__(self, *args, **kwargs)

    def finalize(self):
        StreamPanelEntry.finalize(self)

class CustomStreamPanelEntry(StreamPanelEntry): #pylint: disable=R0901
    """ A stream panel which can be altered by the user """

    expander_class = CustomExpander

    def __init__(self, *args, **kwargs):
        StreamPanelEntry.__init__(self, *args, **kwargs)



class StreamPanel(wx.Panel):
    """
    The whole panel containing stream entries and a button to add more streams
    There are multiple levels of visibility of a stream entry:
     * the stream entry is shown in the panel and has the visible icon on:
        The current view is compatible with the stream and has it in its list
        of streams. 
     * the stream entry is shown in the panel and has the visible icon off:
        The current view is compatible with the stream, but the stream is not
        in its list of streams
     * the stream entry is not present in the panel (hidden):
        The current view is not compatible with the stream 
    """

    DEFAULT_BORDER = 2
    DEFAULT_STYLE = wx.BOTTOM | wx.EXPAND

    def __init__(self):
        pre = wx.PrePanel()

        self._sz = None
        self._microscope = None # MicroscopeGUI
        self.txt_no_stream = None
        self.btn_add_stream = None

        self.entries = []
        self.menu_actions = collections.OrderedDict() # title => callback

        # the Create step is done later by XRC.
        self.PostCreate(pre)
        self.Bind(wx.EVT_WINDOW_CREATE, self.OnCreate)

    def OnCreate(self, evt):
        self.Unbind(wx.EVT_WINDOW_CREATE)
        log.debug("Creating StreamPanel")

        if self._sz is None:
            self._sz = wx.BoxSizer(wx.VERTICAL)
            self.SetSizer(self._sz)

        msg = "No stream available as both SEM and optical paths are off."

        #log.debug("Point size %s" % self.GetFont().GetPointSize())

        self.txt_no_stream = wx.StaticText(self, -1, msg)
        self._sz.Add(self.txt_no_stream, 0, wx.ALL | wx.ALIGN_CENTER, 10)

        self.btn_add_stream = PopupImageButton(self, -1,
                                               bitmap=img.getstream_addBitmap(),
                                               label="ADD STREAM",
                                               style=wx.ALIGN_CENTER)
        self.btn_add_stream.SetBitmaps(img.getstream_add_hBitmap())
        self._sz.Add(self.btn_add_stream, flag=wx.ALL, border=5)

        self._set_warning()
        

        #FIXME: dropdown not working atm
        #self.btn_add_stream.Bind(wx.EVT_LISTBOX, self.on_add_stream)
        self.btn_add_stream.Bind(wx.EVT_BUTTON, self.on_add_stream)

        self.FitStreams()

    def setMicroscope(self, microscope):
        self._microscope = microscope
        
        self._microscope.currentView.subscribe(self._onView, init=True)

    # === VA handlers
        
    def _onView(self, view):
        """
        Called when the current view changes
        """
        if not view:
            return
        
        # FIXME
        # hide/show the stream panel entries which are compatible with the view
        # (listed in view.stream_classes)
        
        # update the "visible" icon of each stream panel entry to match the list
        # of streams in the view
        # (listed in view.streams.getStreams())
        


    # === Event Handlers

    def on_add_stream(self, evt):
        # TODO: call the action of the menu
#        csp = CustomStreamPanelEntry(self, label="Custom Stream")
#        self.add_stream(csp)

        if "Filtered colour" in self.menu_actions:
            action = self.menu_actions["Filtered colour"]
            action()
        else:
            log.info("Don't know how to add a stream, need to implement a real menu")
        # evt_obj = evt.GetEventObject()
        # stream_name = evt_obj.GetStringSelection()

        # if self.menu_actions.has_key(stream_name):
        #     self.menu_actions[stream_name]()
        # else:
        #     raise KeyError("Stream named '%s' not found!", evt.data)

    def on_stream_remove(self, evt):
        eo = evt.GetEventObject()
        wx.CallAfter(eo.Destroy)

    def FitStreams(self):

        h = self._sz.GetMinSize().GetHeight()

        log.debug("Setting StreamPanel height to %s", h)
        self.SetSize((-1, h))

        # The panel size is cached in the _PanelSize attribute.
        # Make sure it's updated by calling ResizePanel

        p = self.Parent

        while not isinstance(p, FoldPanelItem):
            p = p.Parent

        p._refresh()

    def show_add_button(self):
        self.btn_add_stream.Show()
        self.FitStreams()

    def hide_add_button(self):
        self.btn_add_stream.Hide()
        self.FitStreams()

    def is_empty(self):
        return len(self.entries) == 0

    def get_size(self):
        return len(self.entries)

    # the order in which the streams are displayed
    STREAM_ORDER=[instrmodel.SEMStream,
                  instrmodel.BrightfieldStream,
                  instrmodel.FluoStream]
    # TODO maybe should be provided after init by the controller (like key of
    # sorted()), to separate the GUI from the model ?
    def _get_stream_order(self, stream):
        """
        Gives the "order" of the given stream, as defined in STREAM_ORDER.
        stream (Stream): a stream
        returns (0<= int): the order 
        """
        for i, c in enumerate(self.STREAM_ORDER):
            if isinstance(stream, c):
                return i

        log.warning("Stream of unknown order type %s", stream.__class__.__name__)
        return len(self.STREAM_ORDER)
        
    def add_stream(self, stream_panel_entry):
        """ This method adds a stream entry to the panel, after the appropriate
        position has been determined. 
        """
        # Insert the entry in the order of STREAM_ORDER. If there are already 
        # streams with the same type, insert after them.
        ins_pos = 0
        order_s = self._get_stream_order(stream_panel_entry.stream)
        for e in self.entries:
            order_e = self._get_stream_order(e.stream)
            if order_s < order_e:
                break
            ins_pos += 1
        
        log.debug("Inserting %s at position %s",
                  stream_panel_entry.stream.__class__.__name__,
                  ins_pos)

        stream_panel_entry.finalize()

        self.entries.insert(ins_pos, stream_panel_entry)

        self._set_warning()

        if self._sz is None:
            self._sz = wx.BoxSizer(wx.VERTICAL)
            self.SetSizer(self._sz)

        self._sz.InsertWindow(ins_pos, stream_panel_entry,
                              flag=self.DEFAULT_STYLE,
                              border=self.DEFAULT_BORDER)

        stream_panel_entry.Bind(EVT_STREAM_REMOVE, self.on_stream_remove)

        stream_panel_entry.Layout()

        #self.Refresh()
        self.FitStreams()

    def hide_stream(self, stream_pos):
        self.entries[stream_pos].Hide()
        self.Refresh()
        self.FitStreams()

    def remove_stream(self, stream_pos):
        stream_obj = self.entries.pop(stream_pos)
        wx.CallAfter(stream_obj.Destroy)
        self._set_warning()

    def clear(self):
        for dummy in range(len(self.entries)):
            self.remove_stream(0)
        self._set_warning()

    def _set_warning(self):
        """ Display a warning text when no streams are present, or show it
        otherwise.
        """
        if self.txt_no_stream is not None:
            if self.is_empty() :
                self.txt_no_stream.Show()
            else:
                self.txt_no_stream.Hide()

    def get_stream_position(self, stream_entry):
        """ Return the position of the given stream entry, with the top position
        being 0.
        """
        for i, e in enumerate(self.entries):
            if e == stream_entry:
                return i

        return None

    def set_actions(self, actions):
        """
        Set the action menu
        actions (collections.OrderedDict(string, callable)): dict of title => callback,
          ordered as it will appear
        """
        self.menu_actions = actions
        self.btn_add_stream.set_choices(self.menu_actions.keys())

    def get_actions(self):
        return self.menu_actions

    # TODO need to have actions enabled/disabled depending on the context:
    #  * if microscope if off/pause => disabled
    #  * if focused view is not about this type of stream => disabled
    #  * if there can be only one stream of this type, and it's already present => disabled
    # So maybe action comes also with a check_enabled callable which is called
    #   every time the menu is displayed and return (boolean) whether it should
    #   be disabled 
    def add_action(self, title, callback):
        """
        Add an action to the menu. It's added at the end of the list. If an 
        action with the same title exists, it is replaced.
        title (string): Text displayed in the menu
        callback (callable): function to call when the action is selected
        """
        self.menu_actions[title] = callback
        self.btn_add_stream.set_choices(self.menu_actions.keys())

    def remove_action(self, title):
        """
        Remove the given action, if it exists. Otherwise does nothing
        title (string): name of the action to remove
        """
        if title in self.menu_actions:
            del self.menu_actions[title]
            self.btn_add_stream.set_choices(self.menu_actions.keys())

