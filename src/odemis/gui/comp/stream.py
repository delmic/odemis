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
import logging
import math

import wx
from wx.lib.pubsub import pub

import odemis.gui
import odemis.gui.comp.buttons as buttons
import odemis.gui.img.data as img

from odemis import model
from odemis.gui import instrmodel, FOREGROUND_COLOUR_EDIT
from odemis.gui.comp.foldpanelbar import FoldPanelItem
from odemis.gui.comp.slider import UnitIntegerSlider
from odemis.gui.comp.text import SuggestTextCtrl, UnitIntegerCtrl, \
    IntegerTextCtrl
from odemis.gui.util import call_after
from odemis.gui.util.conversion import wave2rgb
from odemis.gui.util.widgets import VigilantAttributeConnector


TEST_STREAM_LST = ["Aap", u"nöot", "noot", "mies", "kees", "vuur",
                  "quantummechnica", "Repelsteeltje", "", "XXX", "a", "aa",
                  "aaa", "aaaa", "aaaaa", "aaaaaa", "aaaaaaa", "aAa"]

stream_remove_event, EVT_STREAM_REMOVE = wx.lib.newevent.NewEvent()


class Expander(wx.PyControl):
    """ This class describes a clickable control responsible for showing and
    hiding stream data.

    It functions both as a header and a button that expands or collapses a
    panel containg controls.

    Structure:

        + Expander
        |-- ImageButton: Removes the stream
        |-- Spacer: Temporary place holder for other controls
        |-- ImageToggleButton (show/hide button)
        |-- ImageToggleButton (capture/pause button)

    The triangular fold icons are drawn in a separate routine.

    """

    def __init__(self, parent, stream, wid=wx.ID_ANY, pos=wx.DefaultPosition,
                 size=wx.DefaultSize, style=wx.NO_BORDER):
        wx.PyControl.__init__(self, parent, wid, pos, size, style)

        # This style *needs* to be set on MS Windows
        self.SetBackgroundStyle(wx.BG_STYLE_CUSTOM)

        self._stream = stream
        self._label_ctrl = None

        self.Bind(wx.EVT_SIZE, self.OnSize)

        # ===== Fold icons

        self._foldIcons = wx.ImageList(16, 16)
        self._foldIcons.Add(img.getarr_down_sBitmap())
        self._foldIcons.Add(img.getarr_right_sBitmap())

        # ===== Remove button

        self._btn_rem = buttons.ImageButton(self,
                                            wx.ID_ANY,
                                            img.getico_rem_strBitmap(),
                                            (10, 8),
                                            (18, 18),
                                            background_parent=parent)
        self._btn_rem.SetBitmaps(img.getico_rem_str_hBitmap())
        self._btn_rem.SetToolTipString("Remove stream")

        # ===== Visibility button

        self._btn_vis = buttons.ImageToggleButton(self,
                                                  wx.ID_ANY,
                                                  img.getico_eye_closedBitmap(),
                                                  (10, 8),
                                                  (18, 18),
                                                  background_parent=parent)
        self._btn_vis.SetBitmaps(img.getico_eye_closed_hBitmap(),
                                 img.getico_eye_openBitmap(),
                                 img.getico_eye_open_hBitmap())
        self._btn_vis.SetToolTipString("Show stream")

        # ===== Play button

        self._btn_play = buttons.ImageToggleButton(self,
                                                   wx.ID_ANY,
                                                   img.getico_pauseBitmap(),
                                                   (10, 8),
                                                   (18, 18),
                                                   background_parent=parent)
        self._btn_play.SetBitmaps(img.getico_pause_hBitmap(),
                                  img.getico_playBitmap(),
                                  img.getico_play_hBitmap())
        self._btn_play.SetToolTipString("Update stream")


        # Create and add sizer and populate with controls
        self._sz = wx.BoxSizer(wx.HORIZONTAL)

        self._sz.Add(self._btn_rem, 0, wx.ALL | wx.ALIGN_CENTRE_VERTICAL, 8)
        # If a label is to be inserted, it can replace this spacer (index = 1)
        self._sz.AddStretchSpacer(0)
        self._sz.Add(self._btn_vis, 0, wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL, 8)
        self._sz.Add(self._btn_play, 0, wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL, 72)

        self.SetSizer(self._sz)
        self._sz.Fit(self)
        self.Layout()

    def DoGetBestSize(self, *args, **kwargs):
        """ Return the best size, which is the width of the parent and the
        height or the content (determined through the sizer).
        """
        width, _ = self.Parent.GetSize()
        _, height = self._sz.GetSize()

        return wx.Size(width, height)

    def OnSize(self, event):
        """ Handles the wx.EVT_SIZE event for the Expander class.
        :param `event`: a `wx.SizeEvent` event to be processed.
        """
        width = self.Parent.GetSize().GetWidth()
        self.SetSize((width, -1))
        self.Layout()
        self.Refresh()

    def OnDrawExpander(self, dc):
        """ This method draws the expand/collapse icons.
        It needs to be called from the parent's paint event
        handler.
        """
        scrollbar_width = wx.SystemSettings_GetMetric(wx.SYS_VSCROLL_X) - 3
        caption_padding_right = 10

        wndRect = self.GetRect()
        drw = wndRect.GetRight() - 16 - caption_padding_right - scrollbar_width
        self._foldIcons.Draw(self.Parent._collapsed, dc, drw,
                             (wndRect.GetHeight() - 16) / 2,
                             wx.IMAGELIST_DRAW_TRANSPARENT)

    def to_acquisition_mode(self):
        """ This method hides or makes read-only any button or data that should
        not be changed during acquisition.
        """

        self._btn_rem.Hide()
        self._btn_vis.Hide()

        # Insert spacer to add some left side padding.
        self._sz.InsertSpacer(0, (5, 16))


class StandardExpander(Expander):
    """ Expander with a fixed name label """

    def __init__(self, parent, stream, wid=wx.ID_ANY):
        Expander.__init__(self, parent, stream, wid)

        self._label_ctrl = wx.StaticText(self, -1, stream.name.value)
        # Replace spacer with control
        self._sz.Remove(1)
        self._sz.Insert(1, self._label_ctrl, 1,
                       wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL, 8)


class DyeExpander(Expander):
    """ Expander with a editable name label, linked to a predefined list of
    dyes.

    This widget also contains an changeable colour indicator.
    """

    def __init__(self, parent, stream, wid=wx.ID_ANY):
        Expander.__init__(self, parent, stream, wid)

        # add a colour button if the stream has a "tint" VA
        if hasattr(stream, "tint"):
            self._btn_color = buttons.ColourButton(self, -1,
                                           bitmap=img.getemptyBitmap(),
                                           size=(18, 18),
                                           colour=stream.tint.value,
                                           background_parent=parent,
                                           use_hover=True)
            self._btn_color.SetToolTipString("Select colour")
            self._sz.Insert(2, self._btn_color, 0,
                            wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL, 8)
            self._btn_color.Bind(wx.EVT_BUTTON, self.on_color_click)
            stream.tint.subscribe(self.set_tint)

        self._label_ctrl = SuggestTextCtrl(self, id= -1, value=stream.name.value)
        self._label_ctrl.SetBackgroundColour(self.Parent.GetBackgroundColour())
        self._label_ctrl.SetForegroundColour(FOREGROUND_COLOUR_EDIT)
        self._label_ctrl.Bind(wx.EVT_COMMAND_ENTER, self._on_label_change)

        # Replace spacer with control
        self._sz.Remove(1)
        self._sz.Insert(1, self._label_ctrl, 1,
                        wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL, 8)

        # Callback when the label changes: (string (text) -> None)
        self.onLabelChange = None

    # GUI event handlers
    def _on_label_change(self, evt):
        if self.onLabelChange:
            self.onLabelChange(self._label_ctrl.GetValue())  #pylint: disable=E1102

    @call_after
    def set_tint(self, colour):
        """ Update the colour button to reflect the provided colour """
        self._btn_color.set_colour(colour)
        logging.debug("Changing tint of button to %s", colour)

    def on_color_click(self, evt):
        # Remove the hover effect
        self._btn_color.OnLeave(evt)

        # set default colour to the current value
        cldata = wx.ColourData()
        cldata.SetColour(wx.Colour(*self._stream.tint.value))

        dlg = wx.ColourDialog(self, cldata)

        if dlg.ShowModal() == wx.ID_OK:
            colour = dlg.ColourData.GetColour().Get()  # convert to a 3-tuple
            logging.debug("Colour %r selected", colour)
            # this will automatically update the button's colour
            self._stream.tint.value = colour

    def to_acquisition_mode(self):
        """ This method hides or makes read-only any button or data that should
        not be changed during acquisition.
        """

        Expander.to_acquisition_mode(self)

        self._sz.Remove(1)
        self._label_ctrl.Destroy()

        self._label_ctrl = wx.StaticText(self, -1, self._stream.name.value)
        # Replace spacer with control
        self._sz.Remove(1)
        self._sz.Insert(1, self._label_ctrl, 1,
                       wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL, 8)

        if hasattr(self._stream, "tint"):
            self._btn_color.SetBitmapHover(None)
            self._btn_color.Unbind(wx.EVT_BUTTON)

    def SetChoices(self, choices):
        """
        Set a list of choices from which the user can pick a pre-defined name
        choices (list of string)
        """
        self._label_ctrl.SetChoices(choices)



class StreamPanel(wx.PyPanel):
    """ The StreamPanel super class, a special case collapsible panel.

    The StreamPanel consists of the following widgets:

        + StreamPanel
        |--- StandardExpander
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

    expander_class = StandardExpander

    def __init__(self, parent, stream, microscope_model, wid=wx.ID_ANY,
                 pos=wx.DefaultPosition, size=wx.DefaultSize,
                 style=wx.CP_DEFAULT_STYLE, agwStyle=0,
                 validator=wx.DefaultValidator, name="CollapsiblePane",
                 collapsed=True):
        """
        stream (Stream): the data model to be displayed (and modified by the user)
        microscope_model (GUIMicroscope): the microscope GUI, where there is
                         focussedView
        """

        wx.PyPanel.__init__(self, parent, wid, pos, size, style, name)

        self.SetBackgroundColour("#4D4D4D")
        self.SetForegroundColour("#DDDDDD")
        self.stream = stream
        self._microscope = microscope_model
        self._collapsed = True
        self._agwStyle = agwStyle | wx.CP_NO_TLW_RESIZE  # |wx.CP_GTK_EXPANDER


        self._panel = wx.Panel(self, style=wx.TAB_TRAVERSAL | wx.NO_BORDER)
        self._panel.Hide()

        self._gbs = wx.GridBagSizer(8, 5)

        # Add a simple sizer so we can create padding at the bottom of the panel
        border_sizer = wx.BoxSizer(wx.HORIZONTAL)
        border_sizer.Add(self._gbs,
                         border=10,
                         flag=wx.BOTTOM|wx.EXPAND,
                         proportion=1)

        self._panel.SetSizer(border_sizer)

        self._expander = None

        self._sz = wx.BoxSizer(wx.HORIZONTAL)

        self.Bind(wx.EVT_SIZE, self.OnSize)

        # Process our custom 'collapsed' parameter.
        if not collapsed:
            self.collapse(collapsed)


    def finalize(self):
        """ This method builds all the child controls
        A delay was needed in order for all the settings to be loaded from the
        XRC file (i.e. Font and background/foreground colours).
        """

        self.row_count = 0

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

        self._btn_auto_contrast = buttons.ImageTextToggleButton(self._panel, -1,
                                                img.getbtn_contrastBitmap(),
                                                label="Auto",
                                                size=(68, 26),
                                                style=wx.ALIGN_RIGHT)
        self._btn_auto_contrast.SetBitmaps(
                                        bmp_h=img.getbtn_contrast_hBitmap(),
                                        bmp_sel=img.getbtn_contrast_aBitmap())
        self._btn_auto_contrast.SetForegroundColour("#000000")
        self._gbs.Add(self._btn_auto_contrast, (self.row_count, 0),
                      flag=wx.LEFT, border=11)
        self.row_count += 1

        # ====== Second row, brightness label, slider and value

        lbl_brightness = wx.StaticText(self._panel, -1, "Brightness")
        self._gbs.Add(lbl_brightness, (self.row_count, 0),
                      flag=wx.LEFT | wx.ALIGN_CENTRE_VERTICAL,
                      border=11)

        self._sld_brightness = UnitIntegerSlider(
                              self._panel,
                              value=self.stream.brightness.value,
                              val_range=self.stream.brightness.range,
                              t_size=(40, -1),
                              unit=None,
                              name="brightness_slider")

        self._vac_brightness = VigilantAttributeConnector(self.stream.brightness,
                                             self._sld_brightness,
                                             events=wx.EVT_SLIDER)
        # span is 2, because emission/excitation have 2 controls
        self._gbs.Add(self._sld_brightness, pos=(self.row_count, 1),
                      span=(1, 2), flag=wx.EXPAND | wx.RIGHT, border=18)
        self.row_count += 1

        # ====== Third row, contrast label, slider and value

        lbl_contrast = wx.StaticText(self._panel, -1, "Contrast")
        self._gbs.Add(lbl_contrast, (self.row_count, 0),
                      flag=wx.LEFT | wx.ALIGN_CENTRE_VERTICAL, border=11)

        self._sld_contrast = UnitIntegerSlider(
                             self._panel,
                             value=self.stream.contrast.value,
                             val_range=self.stream.contrast.range,
                             t_size=(40, -1),
                             unit=None,
                             name="contrast_slider")

        self._vac_contrast = VigilantAttributeConnector(self.stream.contrast,
                                             self._sld_contrast,
                                             events=wx.EVT_SLIDER)

        self._gbs.Add(self._sld_contrast, pos=(self.row_count, 1),
                      span=(1, 2), flag=wx.EXPAND | wx.RIGHT, border=18)
        self.row_count += 1

        self._gbs.AddGrowableCol(1)

        # ==== Bind events

        # Expander

        self._expander._btn_rem.Bind(wx.EVT_BUTTON, self.on_remove_btn)
        self._expander._btn_vis.Bind(wx.EVT_BUTTON, self.on_visibility_btn)
        self._expander._btn_play.Bind(wx.EVT_BUTTON, self.on_play_btn)
        self.stream.updated.subscribe(self.onUpdatedChanged, init=True)

        # initialise _btn_play
        self.setVisible(self.stream in self._microscope.focussedView.value.getStreams())

        # Panel controls
        # TODO reuse VigilantAttributeConnector, or at least refactor

        self._btn_auto_contrast.Bind(wx.EVT_BUTTON, self.on_toggle_autocontrast)

        self._btn_auto_contrast.SetToggle(self.stream.auto_bc.value)
        self.on_toggle_autocontrast(None)  # to ensure the controls are disabled if necessary

    def get_stream_mod(self):
        return self.stream

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

    # API

    def Layout(self, *args, **kwargs):
        """ Layout the StreamPanel. """

        if not self._expander or not self._panel or not self._sz:
            return False  # we need to complete the creation first!

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
        fpb_item._fitStreams()


    def setVisible(self, visible):
        """
        Set the "visible" toggle button.
        Note: it does not add/remove it to the current view.
        """
        # TODO: check that we don't call on_visibility_btn()
        self._expander._btn_vis.SetToggle(visible)

    def collapse(self, collapse=True):
        """ Collapses or expands the pane window.
        """

        if self._collapsed == collapse:
            return

        self.Freeze()

        # update our state
        self._panel.Show(not collapse)
        self._collapsed = collapse

        wx.CallAfter(self.Parent._fitStreams)

        self.Thaw()

    # VA subscriptions: reflect the changes on the stream to the GUI
    def onUpdatedChanged(self, updated):
        self._expander._btn_play.SetToggle(self.stream.updated.value)

    # TODO need to subscribe to brightness/contrast, wavelengthes, tint...

    # GUI events: update the stream when the user changes the values

    def on_remove_btn(self, evt):
        logging.debug("Remove button clicked for '%s'", self.stream.name.value)

        # generate EVT_STREAM_REMOVE
        event = stream_remove_event(spanel=self)
        wx.PostEvent(self, event)

    def on_visibility_btn(self, evt):
        view = self._microscope.focussedView.value
        if not view:
            return
        if self._expander._btn_vis.GetToggle():
            logging.debug("Showing stream '%s'", self.stream.name.value)
            view.addStream(self.stream)
        else:
            logging.debug("Hiding stream '%s'", self.stream.name.value)
            view.removeStream(self.stream)

    def on_play_btn(self, evt):
        if self._expander._btn_play.GetToggle():
            logging.debug("Activating stream '%s'", self.stream.name.value)
        else:
            logging.debug("Pausing stream '%s'", self.stream.name.value)
        self.stream.updated.value = self._expander._btn_play.GetToggle()

    def on_toggle_autocontrast(self, evt):
        enabled = self._btn_auto_contrast.GetToggle()
        # disable the manual controls if it's on
        ctrl_enabled = not enabled
        self._sld_brightness.Enable(ctrl_enabled)
        self._sld_contrast.Enable(ctrl_enabled)

        self.stream.auto_bc.value = enabled


    def OnToggle(self, evt):
        """ Toggle the StreamPanel """

        w = evt.GetEventObject().GetSize().GetWidth()

        if evt.GetX() > w * 0.85:
            self.collapse(not self._collapsed)
        else:
            evt.Skip()

    def OnSize(self, event):
        """ Handles the wx.EVT_SIZE event for StreamPanel
        """
        self.Layout()

    def on_button(self, event):
        """ Handles the wx.EVT_BUTTON event for StreamPanel
        """

        if event.GetEventObject() != self._expander:
            event.Skip()
            return

        self.collapse(not self._collapsed)

    def on_draw_expander(self, event):
        """ Handles the ``wx.EVT_PAINT`` event for the stream panel.
        :note: This is a drawing routine to paint the GTK-style expander.
        """

        dc = wx.AutoBufferedPaintDC(self._expander)
        dc.SetBackground(wx.Brush(self.GetBackgroundColour()))
        dc.Clear()

        self._expander.OnDrawExpander(dc)

    def to_acquisition_mode(self):
        """ This method hides or makes read-only any button or data that should
        not be changed during acquisition.
        """

        self._expander.to_acquisition_mode()

        # ====== Fourth row, accumulation label, text field and value

        lbl_accum = wx.StaticText(self._panel, -1, "Accumulation")
        self._gbs.Add(lbl_accum, (self.row_count, 0),
                      flag=wx.LEFT | wx.ALIGN_CENTRE_VERTICAL, border=11)

        self._txt_accum = IntegerTextCtrl(self._panel,
                                          value=1,
                                          min_val=1,
                                          key_inc=True,
                                          step=1,
                                          style=wx.NO_BORDER)
        self._txt_accum.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR_EDIT)
        self._txt_accum.SetBackgroundColour(self._panel.GetBackgroundColour())

        self._gbs.Add(self._txt_accum, (self.row_count, 1), flag=wx.EXPAND)

        self.row_count += 1

        # ====== Fifth row, interpolation label, text field and value

        lbl_interp = wx.StaticText(self._panel, -1, "Interpolation")
        self._gbs.Add(lbl_interp, (self.row_count, 0),
                      flag=wx.LEFT | wx.ALIGN_CENTRE_VERTICAL, border=11)

        choices = ["None", "Linear", "Cubic"]
        self._cmb_interp = wx.combo.OwnerDrawnComboBox(self._panel,
                                                   -1,
                                                   value=choices[0],
                                                   pos=(0, 0),
                                                   size=(100, 16),
                                                   style=wx.NO_BORDER |
                                                         wx.CB_DROPDOWN |
                                                         wx.TE_PROCESS_ENTER |
                                                         wx.CB_READONLY,
                                                    choices=choices)

        self._cmb_interp.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR_EDIT)
        self._cmb_interp.SetBackgroundColour(self._panel.GetBackgroundColour())
        self._cmb_interp.SetButtonBitmaps(img.getbtn_downBitmap(),
                                          pushButtonBg=False)


        self._gbs.Add(self._cmb_interp, (self.row_count, 1), flag=wx.EXPAND)

        self.row_count += 1

        #self._gbs.AddSpacer((5, 5), (self.row_count, 0))



class StandardStreamPanel(StreamPanel):  # pylint: disable=R0901
    """ A pre-defined stream panel """

    expander_class = StandardExpander

    def __init__(self, *args, **kwargs):
        StreamPanel.__init__(self, *args, **kwargs)


    def finalize(self):
        StreamPanel.finalize(self)
        #self._gbs.AddSpacer((5, 5), (self.row_count, 0))


class DyeStreamPanel(StreamPanel):
    """ A stream panel which can be altered by the user """

    expander_class = DyeExpander

    def __init__(self, *args, **kwargs):
        StreamPanel.__init__(self, *args, **kwargs)

    def finalize(self):
        StreamPanel.finalize(self)

        if hasattr(self.stream, "excitation") and hasattr(self.stream, "emission"):
            # handle the auto-completion of dye names
            # TODO: shall we do something better than remove the incompatible dyes?
            # * mark them a different colour in the list (don't know how to do that)?
            # * show a warning message when they are picked?
            self._expander.SetChoices(self._getCompatibleDyes())
            self._expander.onLabelChange = self._onNewName

            # Excitation and emission are a text input + a color display
            # Warning: stream.excitation is in m, we present everything in nm
            lbl_excitation = wx.StaticText(self._panel, -1, "Excitation")
            self._gbs.Add(lbl_excitation, (self.row_count, 0),
                          flag=wx.LEFT | wx.ALIGN_CENTRE_VERTICAL, border=11)

            self._txt_excitation = UnitIntegerCtrl(self._panel, -1,
                    int(round(self.stream.excitation.value * 1e9)),
                    style=wx.NO_BORDER,
                    size=(50, -1),
                    min_val=int(math.ceil(self.stream.excitation.range[0] * 1e9)),
                    max_val=int(self.stream.excitation.range[1] * 1e9),
                    unit='nm')
            self._txt_excitation.SetForegroundColour("#2FA7D4")
            self._txt_excitation.SetBackgroundColour(self.GetBackgroundColour())

            self._gbs.Add(self._txt_excitation, (self.row_count, 1),
                          flag=wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT,
                          border=18)

            # A button, but not clickable, just to show the wavelength
            self._btn_excitation = buttons.ColourButton(self._panel, -1,
                                bitmap=img.getemptyBitmap(),
                                size=(18, 18),
                                colour=wave2rgb(self.stream.excitation.value),
                                background_parent=self._panel)
            self._btn_excitation.SetToolTipString("Wavelength colour")

            self._gbs.Add(self._btn_excitation, (self.row_count, 2),
                          flag=wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT,
                          border=18)
            self.row_count += 1

            self._vac_excitation = VigilantAttributeConnector(
                  self.stream.excitation, self._txt_excitation,
                  va_2_ctrl=self._excitation_2_ctrl, # to convert to nm + update btn
                  ctrl_2_va=self._excitation_2_va, # to convert from nm
                  events=wx.EVT_COMMAND_ENTER)

            # TODO also a label for warnings

            # Emission
            lbl_emission = wx.StaticText(self._panel, -1, "Emission")
            self._gbs.Add(lbl_emission, (self.row_count, 0),
                          flag=wx.LEFT | wx.ALIGN_CENTRE_VERTICAL, border=11)

            self._txt_emission = UnitIntegerCtrl(self._panel, -1,
                    int(round(self.stream.emission.value * 1e9)),
                    style=wx.NO_BORDER,
                    size=(50, -1),
                    min_val=int(math.ceil(self.stream.emission.range[0] * 1e9)),
                    max_val=int(self.stream.emission.range[1] * 1e9),
                    unit='nm')
            self._txt_emission.SetForegroundColour("#2FA7D4")
            self._txt_emission.SetBackgroundColour(self.GetBackgroundColour())

            self._gbs.Add(self._txt_emission, (self.row_count, 1),
                          flag=wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT,
                          border=10)

            self._btn_emission = buttons.ColourButton(self._panel, -1,
                                              bitmap=img.getemptyBitmap(),
                                              size=(18, 18),
                                              colour=wave2rgb(self.stream.emission.value),
                                              background_parent=self._panel)
            self._btn_emission.SetToolTipString("Wavelength colour")

            self._gbs.Add(self._btn_emission, (self.row_count, 2),
                          flag=wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT,
                          border=10)
            self.row_count += 1

            self._vac_emission = VigilantAttributeConnector(
                  self.stream.emission, self._txt_emission,
                  va_2_ctrl=self._emission_2_ctrl, # to convert to nm + update btn
                  ctrl_2_va=self._emission_2_va, # to convert from nm
                  events=wx.EVT_COMMAND_ENTER)
        else:
            logging.warning("DyeStreamPanel associated to a stream without "
                            "excitation/emission")

    def _getCompatibleDyes(self):
        """
        Find the names of the dyes in the database which are compatible with the
         hardware.
        return (list of string): names of all the dyes which are compatible
        """
        # we expect excitation and emission to have a range
        x_range = self.stream.excitation.range
        e_range = self.stream.emission.range

        dyes = []
        for name, (xwl, ewl) in instrmodel.DyeDatabase.items():
            if (x_range[0] <= xwl and xwl <= x_range[1] and
                e_range[0] <= ewl and ewl <= e_range[1]):
                dyes.append(name)

        return dyes

    def _onNewName(self, txt):
        # update the name of the stream
        self.stream.name.value = txt

        # update the excitation and emission wavelength
        if txt in instrmodel.DyeDatabase:
            xwl, ewl = instrmodel.DyeDatabase[txt]
            try:
                self.stream.excitation.value = xwl
            except model.OutOfBoundError:
                logging.info("Excitation at %g nm is out of bound", xwl * 1e9)
            try:
                self.stream.emission.value = ewl
                colour = wave2rgb(self.stream.emission.value)
                # changing emission should also change the tint
                self.stream.tint.value = colour
            except model.OutOfBoundError:
                logging.info("Emission at %g nm is out of bound", ewl * 1e9)

    def _excitation_2_va(self):
        """
        Called when the text is changed (by the user).
        returns a value to set for the VA
        """
        # logging.debug("Excitation changed")
        wl = (self._txt_excitation.GetValue() or 0) * 1e-9
        # FIXME: need to turn the text red if the value is too small (bigger,
        # maybe not necessary) => inside the widget?
        wl = sorted(self.stream.excitation.range + (wl,))[1]
        return wl

    def _excitation_2_ctrl(self, value):
        """
        Called to update the widgets (text + colour display) when the VA changes.
        returns nothing
        """
        self._txt_excitation.ChangeValue(int(round(value * 1e9)))
        colour = wave2rgb(value)
        # logging.debug("Changing colour to %s", colour)
        self._btn_excitation.set_colour(colour)

    def _emission_2_va(self):
        """
        Called when the text is changed (by the user).
        Also updates the tint as a side-effect.
        returns a value to set for the VA
        """
        wl = (self._txt_emission.GetValue() or 0) * 1e-9
        wl = sorted(self.stream.emission.range + (wl,))[1]

        # changing emission should also change the tint
        colour = wave2rgb(wl)
        self.stream.tint.value = colour

        return wl

    def _emission_2_ctrl(self, value):
        """
        Called to update the widgets (text + colour display) when the VA changes.
        returns nothing
        """
        self._txt_emission.ChangeValue(int(round(value * 1e9)))
        colour = wave2rgb(value)
        # logging.debug("Changing colour to %s", colour)
        self._btn_emission.set_colour(colour)


class StreamBar(wx.Panel):
    """
    The whole panel containing stream panels and a button to add more streams
    There are multiple levels of visibility of a stream panel:
     * the stream panel is shown in the panel and has the visible icon on:
        The current view is compatible with the stream and has it in its list
        of streams.
     * the stream panel is shown in the panel and has the visible icon off:
        The current view is compatible with the stream, but the stream is not
        in its list of streams
     * the stream panel is not present in the panel (hidden):
        The current view is not compatible with the stream
    """

    DEFAULT_BORDER = 2
    DEFAULT_STYLE = wx.BOTTOM | wx.EXPAND
    # the order in which the streams are displayed
    STREAM_ORDER = [instrmodel.SEMStream,
                    instrmodel.BrightfieldStream,
                    instrmodel.FluoStream]


    def __init__(self, *args, **kwargs):

        add_btn = kwargs.pop('add_button', False)

        wx.Panel.__init__(self, *args, **kwargs)

        self._microscope = None # GUIMicroscope

        self.stream_panels = []
        self.menu_actions = collections.OrderedDict()  # title => callback

        self._sz = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self._sz)

        msg = "No stream available as both SEM and optical paths are off."

        # logging.debug("Point size %s" % self.GetFont().GetPointSize())

        self.txt_no_stream = wx.StaticText(self, -1, msg)
        self._sz.Add(self.txt_no_stream, 0, wx.ALL | wx.ALIGN_CENTER, 10)

        self.btn_add_stream = None

        if add_btn:
            self.btn_add_stream = buttons.PopupImageButton(
                                               self, -1,
                                               bitmap=img.getstream_addBitmap(),
                                               label="ADD STREAM",
                                               style=wx.ALIGN_CENTER)

            self.btn_add_stream.SetForegroundColour("#999999")
            self.btn_add_stream.SetBitmaps(img.getstream_add_hBitmap(),
                                           img.getstream_add_aBitmap())
            self._sz.Add(self.btn_add_stream, flag=wx.ALL, border=10)

            self._set_warning()

            self.btn_add_stream.Bind(wx.EVT_BUTTON, self.on_add_stream)

        self._fitStreams()

    def _fitStreams(self):
        h = self._sz.GetMinSize().GetHeight()

        logging.debug("Setting StreamBar height to %s", h)
        self.SetSize((-1, h))

        # The panel size is cached in the _PanelSize attribute.
        # Make sure it's updated by calling ResizePanel

        p = self.Parent

        while not isinstance(p, FoldPanelItem):
            p = p.Parent

        p._refresh()

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

        logging.warning("Stream of unknown order type %s", stream.__class__.__name__)
        return len(self.STREAM_ORDER)

    # === VA handlers

    # Moved to stream controller

    # === Event Handlers

    def on_add_stream(self, evt):
        # TODO: call the action of the menu
        if "Filtered colour" in self.menu_actions:
            evt.Skip()
            #action = self.menu_actions["Filtered colour"]
            #action()
        else:
            logging.info("Don't know how to add a stream, need to implement a real menu")
        # evt_obj = evt.GetEventObject()
        # stream_name = evt_obj.GetStringSelection()

    def on_stream_remove(self, evt):
        logging.debug("StreamBar received remove event %r", evt)
        # delete stream panel
        self.remove_stream_panel(evt.spanel)

        # Publish removal notification
        logging.debug("Sending stream.remove message")
        pub.sendMessage("stream.remove", stream=evt.spanel.stream)

    # === API of the stream panel
    def show_add_button(self):
        if self.btn_add_stream:
            self.btn_add_stream.Show()
            self._fitStreams()

    def hide_add_button(self):
        if self.btn_add_stream:
            self.btn_add_stream.Hide()
            self._fitStreams()

    def is_empty(self):
        return len(self.stream_panels) == 0

    def get_size(self):
        """ Return the number of stream contained withing the StreamBar """
        return len(self.stream_panels)

    def add_stream(self, spanel, show):
        """
        This method adds a stream panel to the stream bar. The appropriate
        position is automatically determined.
        spanel (StreamPanel): a stream panel
        """
        # Insert the spanel in the order of STREAM_ORDER. If there are already
        # streams with the same type, insert after them.
        ins_pos = 0
        order_s = self._get_stream_order(spanel.stream)
        for e in self.stream_panels:
            order_e = self._get_stream_order(e.stream)
            if order_s < order_e:
                break
            ins_pos += 1

        logging.debug("Inserting %s at position %s",
                      spanel.stream.__class__.__name__,
                      ins_pos)

        spanel.finalize()

        self.stream_panels.insert(ins_pos, spanel)

        self._set_warning()

        if self._sz is None:
            self._sz = wx.BoxSizer(wx.VERTICAL)
            self.SetSizer(self._sz)

        self._sz.InsertWindow(ins_pos, spanel,
                              flag=self.DEFAULT_STYLE,
                              border=self.DEFAULT_BORDER)

        spanel.Bind(EVT_STREAM_REMOVE, self.on_stream_remove)

        spanel.Layout()

        # hide the stream if the current view is not compatible
        spanel.Show(show)
        self._fitStreams()


    def get_stream_panels(self):
        return self.stream_panels

    def remove_stream_panel(self, spanel):
        """
        Removes a stream panel
        Deletion of the actual stream must be done separately.
        """
        self.stream_panels.remove(spanel)
        wx.CallAfter(spanel.Destroy)
        self._set_warning()

    def _set_warning(self):
        """ Display a warning text when no streams are present, or show it
        otherwise.
        """
        if self.txt_no_stream is not None:
            self.txt_no_stream.Show(self.is_empty())

    def get_actions(self):
        return self.menu_actions

    # TODO need to have actions enabled/disabled depending on the context:
    #  * if microscope if off/pause => disabled
    #  * if focused view is not about this type of stream => disabled
    #  * if there can be only one stream of this type, and it's already present
    #    => disabled
    # TODO: Add 'check_enabled' functions to the 'add_choice' method call that
    # determine whether the choice should be enabled or disabled (by returning
    # True or False)
    def add_action(self, title, callback, check_enabled=None):
        """
        Add an action to the menu. It's added at the end of the list. If an
        action with the same title exists, it is replaced.
        title (string): Text displayed in the menu
        callback (callable): function to call when the action is selected
        """
        if self.btn_add_stream is None:
            logging.error("No add button present!")
        else:
            logging.debug("Adding %s action to stream panel", title)
            self.menu_actions[title] = callback
            self.btn_add_stream.add_choice(title, callback, check_enabled)

    def remove_action(self, title):
        """
        Remove the given action, if it exists. Otherwise does nothing
        title (string): name of the action to remove
        """
        if title in self.menu_actions:
            logging.debug("Removing %s action from stream panel", title)
            del self.menu_actions[title]
            self.btn_add_stream.set_choices(self.menu_actions)

