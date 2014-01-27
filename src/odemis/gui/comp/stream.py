# -*- coding: utf-8 -*-

"""
@author: Rinze de Laat

Copyright © 2012 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.




This module contains classes needed to construct stream panels.

Stream panels are custom, specialized controls that allow the user to view and
manipulate various data streams coming from the microscope.

"""

import collections
import logging
import math
import numpy
from odemis import acq
from odemis.gui import FOREGROUND_COLOUR_EDIT, FOREGROUND_COLOUR, \
    BACKGROUND_COLOUR, STREAM_BACKGROUND_COLOUR, FOREGROUND_COLOUR_DIS
from odemis.gui.comp.foldpanelbar import FoldPanelItem
from odemis.gui.comp.slider import UnitFloatSlider, VisualRangeSlider
from odemis.gui.comp.text import SuggestTextCtrl, UnitIntegerCtrl, UnitFloatCtrl
from odemis.gui.util import call_after, wxlimit_invocation
from odemis.gui.util.widgets import VigilantAttributeConnector
import wx.lib.newevent
from wx.lib.pubsub import pub

import odemis.gui.comp.buttons as buttons
import odemis.gui.img.data as img
import odemis.gui.model.dye as dye
from odemis.util.conversion import wave2rgb


stream_remove_event, EVT_STREAM_REMOVE = wx.lib.newevent.NewEvent()


BUTTON_BORDER = 8
BUTTON_SIZE = (18, 18)

# Expanders are the stream controls that are always visible. They allow for
# the showing and hiding of sub-controls and they might offer controls and
# information themselves.

# Values to control which option is available
OPT_NAME_EDIT = 1 # allow the renaming of the stream (for one time only)
OPT_BTN_REMOVE = 2 # remove the stream entry
OPT_BTN_VISIBLE = 4 # show/hide the stream image
OPT_BTN_UPDATED = 8 # update/stop the stream acquisition
OPT_BTN_TINT = 16 # tint of the stream (if the VA exists)

class Expander(wx.PyControl):
    """ This class describes a clickable control responsible for showing and
    hiding settings belonging to a specific stream.

    It functions both as a header and a button that expands or collapses a
    StreamPanel containing controls.

    The default buttons present are:

    * A remove button, which can be used to remove the StreamPanel
    * A visibility button, indicating whether the stream data should be/is shown
    * A play button, controlling whether or not 'live' data from the stream is
        to be used.

    Structure:

        + Expander
        |-- ImageButton: Removes the stream
        |-- Label: Name of the stream
        |-- ColourButton: Select tint of stream (optional)
        |-- ImageToggleButton: show/hide button
        |-- ImageToggleButton: update/pause button

    The triangular fold icons are directly drawn on the background.

    """

    def __init__(self,
                 parent,
                 stream,
                 wid=wx.ID_ANY,
                 pos=wx.DefaultPosition,
                 size=wx.DefaultSize,
                 style=wx.NO_BORDER,
                 options=(OPT_BTN_REMOVE | OPT_BTN_VISIBLE |
                          OPT_BTN_UPDATED | OPT_BTN_TINT)):

        wx.PyControl.__init__(self, parent, wid, pos, size, style)

        assert(isinstance(parent, StreamPanel))

        # This style *needs* to be set on MS Windows
        self.SetBackgroundStyle(wx.BG_STYLE_CUSTOM)

        # Stream details will be necessary in subclasses
        self._stream = stream
        self._label_ctrl = None
        self._options = options

        self.Bind(wx.EVT_SIZE, self.OnSize)

        # Create and add sizer and populate with controls
        self._sz = wx.BoxSizer(wx.HORIZONTAL)

        # ===== Fold icons

        self._foldIcons = wx.ImageList(16, 16)
        self._foldIcons.Add(img.getarr_down_sBitmap())
        self._foldIcons.Add(img.getarr_right_sBitmap())

        # ===== Remove button

        self._btn_rem = buttons.ImageButton(self,
                                            wx.ID_ANY,
                                            img.getico_rem_strBitmap(),
                                            (10, 8),
                                            BUTTON_SIZE,
                                            background_parent=parent)
        self._btn_rem.SetBitmaps(img.getico_rem_str_hBitmap())
        self._btn_rem.SetToolTipString("Remove stream")
        self._sz.Add(self._btn_rem,
                     0,
                     (wx.ALL | wx.ALIGN_CENTRE_VERTICAL |
                      wx.RESERVE_SPACE_EVEN_IF_HIDDEN),
                     BUTTON_BORDER)

        # ===== Label
        # Put the name of the stream as label
        if options & OPT_NAME_EDIT:
            self._label_ctrl = SuggestTextCtrl(
                                    self,
                                    id= -1,
                                    value=stream.name.value)
            self._label_ctrl.SetBackgroundColour(self.Parent.GetBackgroundColour())
            self._label_ctrl.SetForegroundColour(FOREGROUND_COLOUR_EDIT)
            self._label_ctrl.Bind(wx.EVT_COMMAND_ENTER, self._on_label_change)
        else:
            # Static name
            self._label_ctrl = wx.StaticText(self, -1, stream.name.value)

        self._sz.Add(self._label_ctrl,
                        1,
                        (wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL |
                         wx.RESERVE_SPACE_EVEN_IF_HIDDEN),
                        BUTTON_BORDER)

        # Callback when the label changes: (string (text) -> None)
        self.onLabelChange = None


        # ===== Tint (if the stream has it)

        if hasattr(stream, "tint"):
            self._btn_tint = buttons.ColourButton(self, -1,
                                           bitmap=img.getemptyBitmap(),
                                           size=(18, 18),
                                           colour=stream.tint.value,
                                           background_parent=parent,
                                           use_hover=True)
            self._btn_tint.SetToolTipString("Select colour")
            self._sz.Add(self._btn_tint,
                            0,
                            (wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL |
                             wx.RESERVE_SPACE_EVEN_IF_HIDDEN),
                            BUTTON_BORDER)
            self._btn_tint.Bind(wx.EVT_BUTTON, self._on_tint_click)
            stream.tint.subscribe(self._on_tint_value)

        # ===== Visibility button

        self._btn_vis = buttons.ImageToggleButton(self,
                                                  wx.ID_ANY,
                                                  img.getico_eye_closedBitmap(),
                                                  (10, 8),
                                                  BUTTON_SIZE,
                                                  background_parent=parent)
        self._btn_vis.SetBitmaps(img.getico_eye_closed_hBitmap(),
                                 img.getico_eye_openBitmap(),
                                 img.getico_eye_open_hBitmap())
        self._btn_vis.SetToolTipString("Show stream")

        self._sz.Add(self._btn_vis,
                     0,
                     (wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL |
                      wx.RESERVE_SPACE_EVEN_IF_HIDDEN),
                     BUTTON_BORDER)

        # ===== Play button

        self._btn_updated = buttons.ImageToggleButton(self,
                                                   wx.ID_ANY,
                                                   img.getico_pauseBitmap(),
                                                   (10, 8),
                                                   BUTTON_SIZE,
                                                   background_parent=parent)
        self._btn_updated.SetBitmaps(img.getico_pause_hBitmap(),
                                  img.getico_playBitmap(),
                                  img.getico_play_hBitmap())
        self._btn_updated.SetToolTipString("Update stream")
        self._vac_updated = VigilantAttributeConnector(
                                  self._stream.should_update,
                                  self._btn_updated,
                                  self._btn_updated.SetToggle,
                                  self._btn_updated.GetToggle,
                                  events=wx.EVT_BUTTON)
        self._sz.Add(self._btn_updated,
                     0,
                     (wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL |
                      wx.RESERVE_SPACE_EVEN_IF_HIDDEN),
                     BUTTON_BORDER)

        self._sz.AddSpacer((64, 16))

        self.SetSizer(self._sz)
        self._sz.Fit(self)

        # Hide buttons according to the options:
        if not (options & OPT_BTN_REMOVE):
            self.show_remove_btn(False)
        if not (options & OPT_BTN_VISIBLE):
            self.show_visible_btn(False)
        if not (options & OPT_BTN_UPDATED):
            self.show_updated_btn(False)
        if not (options & OPT_BTN_TINT):
            self.show_tint_btn(False)

        self.Layout()

    ###### Methods needed for layout and painting

    def DoGetBestSize(self, *args, **kwargs):
        """ Return the best size, which is the width of the parent and the
        height or the content (determined through the sizer).
        """
        return wx.Size(self.Parent.GetSize()[0], self._sz.GetSize()[1])

    def OnSize(self, event):
        """ Handles the wx.EVT_SIZE event for the Expander class.
        :param `event`: a `wx.SizeEvent` event to be processed.
        """
        width = self.Parent.GetSize().GetWidth()
        self.SetSize((width, -1))
        self.Layout()
        self.Refresh()
        event.Skip()

    def OnDrawExpander(self, dc):
        """ This method draws the expand/collapse icons.

        It needs to be called from the parent's paint event handler.
        """
        CAPTION_PADDING_RIGHT = 5
        ICON_WIDTH, ICON_HEIGHT = 16, 16

        win_rect = self.GetRect()
        x_pos = win_rect.GetRight() - ICON_WIDTH - CAPTION_PADDING_RIGHT

        if self._foldIcons:
            self._foldIcons.Draw(self.Parent._collapsed, dc, x_pos,
                             (win_rect.GetHeight() - ICON_HEIGHT) / 2,
                             wx.IMAGELIST_DRAW_TRANSPARENT)


    ###### Methods to show and hide the default buttons

    def _show_item(self, item, show):
        if show:
            self._sz.Show(item)
        else:
            self._sz.Hide(item)
        self._sz.Layout()

    def show_remove_btn(self, show):
        """ This method show or hides the remove button """
        self._show_item(self._btn_rem, show)

    def show_updated_btn(self, show):
        """ This method show or hides the play button """
        self._show_item(self._btn_updated, show)

    def show_visible_btn(self, show):
        """ This method show or hides the visible button """
        self._show_item(self._btn_vis, show)

    def show_tint_btn(self, show):
        """ This method show or hides the tint button """
        if hasattr(self, "_btn_tint"):
            self._show_item(self._btn_tint, show)

    def to_static_mode(self):
        """ This method hides or makes read-only any button or data that should
        not be changed during acquisition.
        """
        self.show_remove_btn(False)
        self.show_updated_btn(False)

        # TODO: label readonly (if editable)? Or in to_locked_mode?

    def to_locked_mode(self):
        self.to_static_mode()
        self.show_visible_btn(False)

    # VA subscriptions: reflect the changes on the stream to the GUI
    def _on_update_change(self, updated):
        self._btn_updated.SetToggle(self._stream.should_update.value)

    # GUI event handlers
    def _on_label_change(self, evt):
        if self.onLabelChange:
            self.onLabelChange(self._label_ctrl.GetValue()) #pylint: disable=E1102

    @call_after
    def _on_tint_value(self, colour):
        """ Update the colour button to reflect the provided colour """
        self._btn_tint.set_colour(colour)
        logging.debug("Changing tint of button to %s", colour)

    def _on_tint_click(self, evt):
        # Remove the hover effect
        self._btn_tint.OnLeave(evt)

        # set default colour to the current value
        cldata = wx.ColourData()
        cldata.SetColour(wx.Colour(*self._stream.tint.value))

        dlg = wx.ColourDialog(self, cldata)

        if dlg.ShowModal() == wx.ID_OK:
            colour = dlg.ColourData.GetColour().Get()  # convert to a 3-tuple
            logging.debug("Colour %r selected", colour)
            # this will automatically update the button's colour
            self._stream.tint.value = colour

    def set_label_choices(self, choices):
        """
        Set a list of choices from which the user can pick a pre-defined name
        choices (list of string)
        """
        self._label_ctrl.SetChoices(choices)


class StreamPanel(wx.PyPanel):
    """ The StreamPanel class, a special case collapsible panel.

    The StreamPanel consists of the following widgets:

        ├ StreamPanel
        └┬ Expander
         ├ Panel
         └┬ BoxSizer
          └─ GridBagSizer

    Additional controls can be added to the GridBagSizer in the 'finalize'
    method.

    Most of the component's construction is done in the finalize() method, so
    we can allow for a delay. This is necessary when construction the component
    through an XML handler.

    The controls contained within a StreamPanel are typically connected to the
    VigilantAttribute properties of the Stream it's representing.
    """

    def __init__(self,
                 parent,
                 stream,
                 tab_data,
                 wid=wx.ID_ANY,
                 pos=wx.DefaultPosition,
                 size=wx.DefaultSize,
                 style=wx.CP_DEFAULT_STYLE,
                 agwStyle=0,
                 validator=wx.DefaultValidator,
                 name="StreamPanel",
                 collapsed=True):
        """
        :param parent: (StreamBar) The parent widget.
        :param stream: (Stream) The stream data model to be displayed to and
            modified by the user.
        :param tab_data: (MicroscopyGUIData) The microscope data model,
            TODO: This parameter and related property should be moved to the
            stream controller!
        """
        assert(isinstance(parent, StreamBar))

        wx.PyPanel.__init__(self, parent, wid, pos, size, style, name)

        # Data models
        self.stream = stream
        self._tab_data_model = tab_data

        # Appearance
        self._agwStyle = agwStyle | wx.CP_NO_TLW_RESIZE  # |wx.CP_GTK_EXPANDER
        self.SetBackgroundColour(STREAM_BACKGROUND_COLOUR)
        self.SetForegroundColour(FOREGROUND_COLOUR)

        # State
        self._collapsed = True

        # Child widgets

        self._panel = wx.Panel(self, style=wx.TAB_TRAVERSAL | wx.NO_BORDER)
        self._panel.Hide()

        # Main sizer for control layout
        self._gbs = wx.GridBagSizer()

        # Add a simple sizer so we can create padding for the panel
        border_sizer = wx.BoxSizer(wx.HORIZONTAL)
        border_sizer.Add(self._gbs,
                         border=5,
                         flag=wx.ALL | wx.EXPAND,
                         proportion=1)
        self._panel.SetSizer(border_sizer)

        # Counter that keeps track of the number of rows containing controls
        # inside this panel
        self.row_count = 0
        self._expander = None
        self._sz = wx.BoxSizer(wx.HORIZONTAL)
        self.Bind(wx.EVT_SIZE, self.OnSize)

        # Process our custom 'collapsed' parameter.
        if not collapsed:
            self.collapse(collapsed)

    def finalize(self):
        """ Controls should be added to the panel using this method. This is
        so timing issues will not rise when the panel is instantiated.
        """
        # ====== Add an expander button

        expand_opt = (OPT_BTN_REMOVE | OPT_BTN_VISIBLE | OPT_BTN_UPDATED |
                      OPT_BTN_TINT)
        if (self._has_dye(self.stream) and not
            (self.stream.excitation.readonly or self.stream.emission.readonly)):
            expand_opt |= OPT_NAME_EDIT

        self.set_expander_button(Expander(self, self.stream, options=expand_opt))
        self._sz.Add(self._expander, 0, wx.EXPAND)

        self._expander.Bind(wx.EVT_PAINT, self.on_draw_expander)
        self._expander._btn_rem.Bind(wx.EVT_BUTTON, self.on_remove_btn)
        self._expander._btn_vis.Bind(wx.EVT_BUTTON, self.on_visibility_btn)

        # ====== Build panel controls
        self._panel.SetBackgroundColour(BACKGROUND_COLOUR)
        self._panel.SetForegroundColour(FOREGROUND_COLOUR)
        self._panel.SetFont(self.GetFont())


        if self._has_bc(self.stream):
            self._add_bc_controls()

        if self._has_dye(self.stream):
            self._add_dye_controls()

        if self._has_wl(self.stream):
            self._add_wl_controls()

        # FIXME: only add if some controls are available
        self._gbs.AddGrowableCol(1) # This makes the 2nd column's width variable

        if wx.Platform == "__WXMSW__":
            self._expander.Bind(wx.EVT_LEFT_DCLICK, self.on_button)

        # ==== Bind events
        vis = self.stream in self._tab_data_model.focussedView.value.getStreams()
        self.setVisible(vis)

    def flatten(self):
        """ This method hides the expander header button and makes the controls
        inside visible.
        """
        self._expander.Show(False)
        self.collapse(False)

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
        self._expander.Bind(wx.EVT_LEFT_UP, self.OnToggle)

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
        # Avoid receiving data after the object is deleted
        if hasattr(self, "_sld_hist"):
            self.stream.histogram.unsubscribe(self._onHistogram)
        if hasattr(self, "_sld_spec"):
            self.stream.image.unsubscribe(self.on_new_spec_data)

        fpb_item = self.Parent
        wx.PyPanel.Destroy(self, *args, **kwargs)
        fpb_item._fitStreams()


    def setVisible(self, visible):
        """ Set the "visible" toggle button.
        Note: it does not add/remove it to the current view.
        """
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


    # GUI events: update the stream when the user changes the values

    def on_remove_btn(self, evt):
        logging.debug("Remove button clicked for '%s'", self.stream.name.value)

        # generate EVT_STREAM_REMOVE
        event = stream_remove_event(spanel=self)
        wx.PostEvent(self, event)

    def on_visibility_btn(self, evt):
        # TODO: Move to controller. Screen widget should not need to know about
        # microscopes and focussed views.
        view = self._tab_data_model.focussedView.value
        if not view:
            return
        if self._expander._btn_vis.GetToggle():
            logging.debug("Showing stream '%s'", self.stream.name.value)
            view.addStream(self.stream)
        else:
            logging.debug("Hiding stream '%s'", self.stream.name.value)
            view.removeStream(self.stream)


    # Manipulate expander buttons

    def show_updated_btn(self, show):
        self._expander.show_updated_btn(show)

    def show_remove_btn(self, show):
        self._expander.show_remove_btn(show)

    def show_visible_btn(self, show):
        self._expander.show_visible_btn(show)

    def OnSize(self, event):
        """ Handles the wx.EVT_SIZE event for StreamPanel
        """
        self.Layout()
        event.Skip()

    def OnToggle(self, evt):
        """ Detect click on the collapse button of the StreamPanel """

        w = evt.GetEventObject().GetSize().GetWidth()

        if evt.GetX() > w * 0.85:
            self.collapse(not self._collapsed)
        else:
            evt.Skip()

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

    def to_static_mode(self):
        """ This method hides or makes read-only any button or data that should
        not be changed during acquisition.
        """
        self._expander.to_static_mode()

        # TODO: add when function implemented (and should be dependent on the
        # Stream VAs)
#        # ====== Fourth row, accumulation label, text field and value
#
#        lbl_accum = wx.StaticText(self._panel, -1, "Accumulation")
#        self._gbs.Add(lbl_accum, (self.row_count, 0),
#                      flag=wx.ALL, border=5)
#
#        self._txt_accum = IntegerTextCtrl(self._panel,
#                                          size=(-1, 14),
#                                          value=1,
#                                          min_val=1,
#                                          key_inc=True,
#                                          step=1,
#                                          style=wx.NO_BORDER)
#        self._txt_accum.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR_EDIT)
#        self._txt_accum.SetBackgroundColour(self._panel.GetBackgroundColour())
#
#        self._gbs.Add(self._txt_accum, (self.row_count, 1),
#                                        flag=wx.EXPAND | wx.ALL,
#                                        border=5)
#
#        self.row_count += 1
#
#        # ====== Fifth row, interpolation label, text field and value
#
#        lbl_interp = wx.StaticText(self._panel, -1, "Interpolation")
#        self._gbs.Add(lbl_interp, (self.row_count, 0),
#                      flag=wx.ALL, border=5)
#
#        choices = ["None", "Linear", "Cubic"]
#        self._cmb_interp = wx.combo.OwnerDrawnComboBox(self._panel,
#                                                   - 1,
#                                                   value=choices[0],
#                                                   pos=(0, 0),
#                                                   size=(100, 16),
#                                                   style=wx.NO_BORDER |
#                                                         wx.CB_DROPDOWN |
#                                                         wx.TE_PROCESS_ENTER |
#                                                         wx.CB_READONLY |
#                                                         wx.EXPAND,
#                                                    choices=choices)
#
#        self._cmb_interp.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR_EDIT)
#        self._cmb_interp.SetBackgroundColour(self._panel.GetBackgroundColour())
#        self._cmb_interp.SetButtonBitmaps(img.getbtn_downBitmap(),
#                                          pushButtonBg=False)
#
#
#        self._gbs.Add(self._cmb_interp, (self.row_count, 1),
#                                         flag=wx.EXPAND | wx.ALL,
#                                         border=5,
#                                         span=(1, 2))
#
#        self.row_count += 1

    def to_locked_mode(self):
        self.to_static_mode()
        self._expander.to_locked_mode()

    # ===== For brightness/contrast

    def _has_bc(self, stream):
        return (hasattr(stream, "auto_bc") and hasattr(stream, "intensityRange"))

    def _add_bc_controls(self):
        """ Add the widgets related to brightness/contrast
          ├ Toggle button (AutoBC)
          ├ StaticText (Outliers)
          ├ UnitFloatSlider (AutoBC Outliers)
          ├ BandwidthSlider (Histogram -> Low/High intensity)
          ├ FloatTextCtrl (Low intensity)
          ├ FloatTextCtrl (High intensity)
        """
        # ====== Top row, auto contrast toggle button

        self._btn_autobc = buttons.ImageTextToggleButton(self._panel, -1,
                                                img.getbtn_contrastBitmap(),
                                                label="Auto",
                                                size=(68, 26),
                                                style=wx.ALIGN_RIGHT)

        tooltip = "Toggle auto brightness and contrast"
        self._btn_autobc.SetToolTipString(tooltip)
        self._btn_autobc.SetBitmaps(
                                        bmp_h=img.getbtn_contrast_hBitmap(),
                                        bmp_sel=img.getbtn_contrast_aBitmap())
        self._btn_autobc.SetForegroundColour("#000000")
        self._vac_autobc = VigilantAttributeConnector(self.stream.auto_bc,
                                  self._btn_autobc,
                                  self._btn_autobc.SetToggle,
                                  self._btn_autobc.GetToggle,
                                  events=wx.EVT_BUTTON)

        lbl_bc_outliers = wx.StaticText(self._panel, -1, "Outliers")
        self._sld_bc_outliers = UnitFloatSlider(
                                    self._panel,
                                    value=self.stream.auto_bc_outliers.value,
                                    min_val=self.stream.auto_bc_outliers.range[0],
                                    max_val=self.stream.auto_bc_outliers.range[1],
                                    t_size=(40, -1),
                                    unit="%",
                                    scale="cubic",
                                    accuracy=2)

        self._sld_bc_outliers.SetToolTipString("Percentage of values to ignore "
                                               "in auto brightness and contrast")
        self._vac_bc_outliers = VigilantAttributeConnector(
                                             self.stream.auto_bc_outliers,
                                             self._sld_bc_outliers,
                                             events=wx.EVT_SLIDER)

        autobc_sz = wx.BoxSizer(wx.HORIZONTAL)
        autobc_sz.Add(self._btn_autobc, 0,
                  flag=wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT,
                  border=5)
        autobc_sz.Add(lbl_bc_outliers, 0,
                  flag=wx.ALIGN_CENTRE_VERTICAL | wx.LEFT,
                  border=5)
        autobc_sz.Add(self._sld_bc_outliers, 1,
                  flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND,
                  border=5)
        self._gbs.Add(autobc_sz, (self.row_count, 0), span=(1, 3),
                      flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.ALL,
                      border=5)
        self.row_count += 1

        # ====== Second row, histogram
        ir_rng = (self.stream.intensityRange.range[0][0],
                  self.stream.intensityRange.range[1][1])
        self._sld_hist = VisualRangeSlider(
                                self._panel,
                                size=(-1, 40),
                                value=self.stream.intensityRange.value,
                                min_val=ir_rng[0],
                                max_val=ir_rng[1],
        )
        self._sld_hist.SetBackgroundColour("#000000")
        self._vac_hist = VigilantAttributeConnector(
                                self.stream.intensityRange,
                                self._sld_hist,
                                events=wx.EVT_SLIDER)
        self.stream.histogram.subscribe(self._onHistogram, init=True)

        # span is 2, because emission/excitation have 2 controls
        self._gbs.Add(self._sld_hist, pos=(self.row_count, 0),
                      span=(1, 3),
                      flag=wx.EXPAND | wx.TOP | wx.RIGHT | wx.LEFT,
                      border=5)
        self.row_count += 1

        # ====== Third row, text fields for intensity (ratios)

        lbl_lowi = wx.StaticText(self._panel, -1, "Low")
        tooltip_txt = "Value mapped to black"
        lbl_lowi.SetToolTipString(tooltip_txt)
        self._txt_lowi = UnitFloatCtrl(self._panel, -1,
                    self.stream.intensityRange.value[0] * 100,
                    style=wx.NO_BORDER,
                    size=(-1, 14),
                    min_val=0,
                    max_val=100,
                    unit='%')
        self._txt_lowi.SetBackgroundColour(BACKGROUND_COLOUR)
        self._txt_lowi.SetForegroundColour(FOREGROUND_COLOUR_EDIT)
        self._txt_lowi.SetToolTipString(tooltip_txt)
        def get_lowi(va=self.stream.intensityRange, ctrl=self._txt_lowi):
            lv = ctrl.GetValue() / 100
            hv = va.value[1]
            # clamp low range to max high range
            if hv < lv:
                lv = hv
                ctrl.SetValue(lv * 100)
            return lv, hv
        self._vac_lowi = VigilantAttributeConnector(self.stream.intensityRange,
                          self._txt_lowi,
                          lambda r: self._txt_lowi.SetValue(r[0] * 100),
                          get_lowi,
                          events=wx.EVT_COMMAND_ENTER)

        lbl_highi = wx.StaticText(self._panel, -1, "High")
        tooltip_txt = "Value mapped to white"
        lbl_highi.SetToolTipString(tooltip_txt)
        self._txt_highi = UnitFloatCtrl(self._panel, -1,
                    self.stream.intensityRange.value[1] * 100,
                    style=wx.NO_BORDER,
                    size=(-1, 14),
                    min_val=0,
                    max_val=100,
                    unit='%')
        self._txt_highi.SetBackgroundColour(BACKGROUND_COLOUR)
        self._txt_highi.SetForegroundColour(FOREGROUND_COLOUR_EDIT)
        self._txt_highi.SetToolTipString(tooltip_txt)
        def get_highi(va=self.stream.intensityRange, ctrl=self._txt_highi):
            lv = va.value[0]
            hv = ctrl.GetValue() / 100
            # clamp high range to at least low range
            if hv < lv:
                hv = lv
                ctrl.SetValue(hv * 100)
            return lv, hv

        self._vac_highi = VigilantAttributeConnector(self.stream.intensityRange,
                          self._txt_highi,
                          lambda r: self._txt_highi.SetValue(r[1] * 100),
                          get_highi,
                          events=wx.EVT_COMMAND_ENTER)

        lh_sz = wx.BoxSizer(wx.HORIZONTAL)
        lh_sz.Add(lbl_lowi, 0,
                  flag=wx.ALIGN_CENTRE_VERTICAL | wx.LEFT,
                  border=5)
        lh_sz.Add(self._txt_lowi, 1,
                  flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.RIGHT | wx.LEFT,
                  border=5)
        lh_sz.Add(lbl_highi, 0,
                  flag=wx.ALIGN_CENTRE_VERTICAL | wx.LEFT,
                  border=5)
        lh_sz.Add(self._txt_highi, 1,
                  flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.RIGHT | wx.LEFT,
                  border=5)
        self._gbs.Add(lh_sz, (self.row_count, 0), span=(1, 3),
                      flag=wx.BOTTOM | wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND,
                      border=5)
        self.row_count += 1

        # Can only do that once all the controls are here
        self.stream.auto_bc.subscribe(self._onAutoBC, init=True)

    @call_after
    def _onAutoBC(self, enabled):
        # disable the manual controls if it's on
        self._sld_bc_outliers.Enable(enabled)
        self._sld_hist.Enable(not enabled)
        self._txt_lowi.Enable(not enabled)
        self._txt_highi.Enable(not enabled)

    def _onHistogram(self, hist):
        # TODO: don't update when folded: it's useless => unsubscribe
        # hist is a ndarray of ints, content is a list of values between 0 and 1
        if len(hist):
            lhist = numpy.log1p(hist) # log histogram is easier to read
            norm_hist = lhist / float(lhist.max())
            # ndarrays work too, but slower to display
            norm_hist = norm_hist.tolist()
        else:
            norm_hist = []

        wx.CallAfter(self._sld_hist.SetContent, norm_hist)

    # ====== For the dyes
    def _has_dye(self, stream):
        """
        return True if the stream looks like a stream using dye.
        """
        return hasattr(stream, "excitation") and hasattr(stream, "emission")

    def _add_dye_controls(self):
        """
        Adds the widgets related to the dyes (FluoStream)
          ├ StaticText (excitation)
          ├ UnitIntegerCtrl
          ├ StaticText (emission)
          └ UnitIntegerCtrl
        """

        # handle the auto-completion of dye names
        # TODO: Do not remove the incompatible dyes (because we might be wrong,
        # and it leads the user to wonder why the dye is not there if he's
        # looking for it):
        # * mark them a "disabled" colour in the list. (how to do that?)
        # * show a warning message when they are picked.
        if not self.stream.excitation.readonly:
            self._expander.set_label_choices(self._getCompatibleDyes())
            self._expander.onLabelChange = self._onNewDyeName

        # Excitation and emission are a text input + a colour display
        # Warning: stream.excitation is in m, we present everything in nm
        lbl_excitation = wx.StaticText(self._panel, -1, "Excitation")
        self._gbs.Add(lbl_excitation, (self.row_count, 0),
                      flag=wx.ALL, border=5)

        if self.stream.excitation.readonly:
            self._txt_excitation = wx.TextCtrl(self._panel,
                       value="%d nm" % round(self.stream.excitation.value * 1e9),
                       style=wx.BORDER_NONE | wx.TE_READONLY)
            self._txt_excitation.SetForegroundColour(FOREGROUND_COLOUR_DIS)
        else:
            min_val = int(math.ceil(self.stream.excitation.range[0] * 1e9))
            max_val = int(self.stream.excitation.range[1] * 1e9)
            # if the range is very small, they might not be min < max
            min_val, max_val = (min(min_val, max_val), max(min_val, max_val))

            self._txt_excitation = UnitIntegerCtrl(self._panel, -1,
                    int(round(self.stream.excitation.value * 1e9)),
                    style=wx.NO_BORDER,
                    size=(-1, 14),
                    min_val=min_val,
                    max_val=max_val,
                    unit='nm')

            self._txt_excitation.SetForegroundColour(FOREGROUND_COLOUR_EDIT)
            self._vac_excitation = VigilantAttributeConnector(
                  self.stream.excitation, self._txt_excitation,
                  va_2_ctrl=self._excitation_2_ctrl, # to convert to nm + update btn
                  ctrl_2_va=self._excitation_2_va, # to convert from nm
                  events=wx.EVT_COMMAND_ENTER)

        self._txt_excitation.SetBackgroundColour(BACKGROUND_COLOUR)

        self._gbs.Add(self._txt_excitation, (self.row_count, 1),
                      flag=wx.ALL | wx.ALIGN_CENTRE_VERTICAL,
                      border=5)

        # A button, but not clickable, just to show the wavelength
        self._btn_excitation = buttons.ColourButton(self._panel, -1,
                            bitmap=img.getemptyBitmap(),
                            colour=wave2rgb(self.stream.excitation.value),
                            background_parent=self._panel)
        self._btn_excitation.SetToolTipString("Wavelength colour")

        self._gbs.Add(self._btn_excitation, (self.row_count, 2),
                      flag=wx.RIGHT | wx.ALIGN_RIGHT,
                      border=5)
        self.row_count += 1


        # TODO also a label for warnings

        # Emission
        lbl_emission = wx.StaticText(self._panel, -1, "Emission")
        self._gbs.Add(lbl_emission, (self.row_count, 0),
                      flag=wx.ALL, border=5)

        if self.stream.emission.readonly:
            self._txt_emission = wx.TextCtrl(self._panel,
                       value="%d nm" % round(self.stream.emission.value * 1e9),
                       style=wx.BORDER_NONE | wx.TE_READONLY)
            self._txt_emission.SetForegroundColour(FOREGROUND_COLOUR_DIS)
        else:
            min_val = int(math.ceil(self.stream.emission.range[0] * 1e9))
            max_val = int(self.stream.emission.range[1] * 1e9)
            # if the range is very small, they might not be min < max
            min_val, max_val = (min(min_val, max_val), max(min_val, max_val))

            self._txt_emission = UnitIntegerCtrl(self._panel, -1,
                    int(round(self.stream.emission.value * 1e9)),
                    style=wx.NO_BORDER,
                    size=(-1, 14),
                    min_val=min_val,
                    max_val=max_val,
                    unit='nm')

            self._txt_emission.SetForegroundColour(FOREGROUND_COLOUR_EDIT)
            self._vac_emission = VigilantAttributeConnector(
                  self.stream.emission, self._txt_emission,
                  va_2_ctrl=self._emission_2_ctrl, # to convert to nm + update btn
                  ctrl_2_va=self._emission_2_va, # to convert from nm
                  events=wx.EVT_COMMAND_ENTER)

        self._txt_emission.SetBackgroundColour(BACKGROUND_COLOUR)

        self._gbs.Add(self._txt_emission, (self.row_count, 1),
                      flag=wx.ALL | wx.ALIGN_CENTRE_VERTICAL,
                      border=5)

        self._btn_emission = buttons.ColourButton(self._panel, -1,
                                          bitmap=img.getemptyBitmap(),
                                          colour=wave2rgb(self.stream.emission.value),
                                          background_parent=self._panel)
        self._btn_emission.SetToolTipString("Wavelength colour")

        self._gbs.Add(self._btn_emission, (self.row_count, 2),
                      flag=wx.RIGHT | wx.ALIGN_RIGHT,
                      border=5)
        self.row_count += 1

    def _getCompatibleDyes(self):
        """
        Find the names of the dyes in the database which are compatible with the
         hardware.
        return (list of string): names of all the dyes which are compatible
        """
        # we expect excitation and emission to have a range
        x_range = list(self.stream.excitation.range)
        e_range = list(self.stream.emission.range)

        # TODO: for now be very indulgent, as there is no user feedback about
        # the hardware not being compatible, and we are only looking at
        # the peak values. In the future, it might be better to display every
        # dye, and add a big warning if it looks incompatible with the hardware.
        x_range[0] -= 20e-9
        x_range[1] += 20e-9
        e_range[0] -= 20e-9
        e_range[1] += 20e-9
        dyes = []
        for name, (xwl, ewl) in dye.DyeDatabase.items():
            if (x_range[0] <= xwl <= x_range[1] and
                e_range[0] <= ewl <= e_range[1]):
                dyes.append(name)

        return dyes

    def _onNewDyeName(self, txt):
        # update the name of the stream
        self.stream.name.value = txt

        # update the excitation and emission wavelength
        if txt in dye.DyeDatabase:
            xwl, ewl = dye.DyeDatabase[txt]
            try:
                self.stream.excitation.value = xwl
            except IndexError:
                logging.info("Excitation at %g nm is out of bound", xwl * 1e9)
            try:
                self.stream.emission.value = ewl
                colour = wave2rgb(self.stream.emission.value)
                # changing emission should also change the tint
                self.stream.tint.value = colour
            except IndexError:
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

    # ===== Wavelength bandwidth

    def _has_wl(self, stream):
        """
        return True if the stream looks like a stream with wavelength
        """
        return (hasattr(stream, "spectrumBandwidth"))
                #and hasattr(stream, "fitToRGB")

    def _add_wl_controls(self):
        # ====== Top row, fit RGB toggle button

        self._btn_fit_rgb = buttons.ImageTextToggleButton(
                                                self._panel,
                                                - 1,
                                                img.getbtn_spectrumBitmap(),
                                                label="RGB",
                                                size=(68, 26),
                                                style=wx.ALIGN_RIGHT)
        tooltip = "Toggle sub-bandwidths to Red/Green/Blue display"
        self._btn_fit_rgb.SetToolTipString(tooltip)
        self._btn_fit_rgb.SetBitmaps(bmp_h=img.getbtn_spectrum_hBitmap(),
                                     bmp_sel=img.getbtn_spectrum_aBitmap())
        self._btn_fit_rgb.SetForegroundColour("#000000")
        self._gbs.Add(self._btn_fit_rgb,
                      (self.row_count, 0),
                      flag=wx.LEFT | wx.TOP,
                      border=5)
        self.row_count += 1
        self._vac_fit_rgb = VigilantAttributeConnector(
                                  self.stream.fitToRGB,
                                  self._btn_fit_rgb,
                                  self._btn_fit_rgb.SetToggle,
                                  self._btn_fit_rgb.GetToggle,
                                  events=wx.EVT_BUTTON)

        # ====== Second row, center label, slider and value

        wl = self.stream.spectrumBandwidth.value
        wl_rng = (self.stream.spectrumBandwidth.range[0][0],
                  self.stream.spectrumBandwidth.range[1][1])
        self._sld_spec = VisualRangeSlider(
                                self._panel,
                                size=(-1, 40),
                                value=wl,
                                min_val=wl_rng[0],
                                max_val=wl_rng[1],
        )
        self._sld_spec.SetBackgroundColour("#000000")
        self._vac_center = VigilantAttributeConnector(
                                self.stream.spectrumBandwidth,
                                self._sld_spec,
                                events=wx.EVT_SLIDER)

        # span is 3, because emission/excitation have 2 controls
        self._gbs.Add(self._sld_spec, pos=(self.row_count, 0),
                      span=(1, 3),
                      flag=wx.EXPAND | wx.TOP | wx.LEFT | wx.RIGHT,
                      border=5)
        self.row_count += 1

        # ====== Third row, text fields for intensity (ratios)
        tooltip_txt = "Center wavelength of the spectrum"
        lbl_scenter = wx.StaticText(self._panel, -1, "Center")
        lbl_scenter.SetToolTipString(tooltip_txt)
        self._txt_scenter = UnitFloatCtrl(self._panel, -1,
                    (wl[0] + wl[1]) / 2,
                    style=wx.NO_BORDER,
                    size=(-1, 14),
                    min_val=wl_rng[0],
                    max_val=wl_rng[1],
                    unit=self.stream.spectrumBandwidth.unit) # m or px
        self._txt_scenter.SetBackgroundColour(BACKGROUND_COLOUR)
        self._txt_scenter.SetForegroundColour(FOREGROUND_COLOUR_EDIT)
        self._txt_scenter.SetToolTipString(tooltip_txt)

        def get_center(va=self.stream.spectrumBandwidth, ctrl=self._txt_scenter):
            """
            Return the low/high values for the bandwidth, from the requested center
            """
            # ensure the low/high values are always within the allowed range
            wl = va.value
            wl_rng = (va.range[0][0], va.range[1][1])

            width = wl[1] - wl[0]
            ctr_rng = wl_rng[0] + width / 2, wl_rng[1] - width / 2
            req_center = ctrl.GetValue()
            new_center = min(max(ctr_rng[0], req_center), ctr_rng[1])

            if req_center != new_center:
                # VA might not change => update value ourselves
                ctrl.SetValue(new_center)

            wl = (new_center - width / 2, new_center + width / 2)
            return wl

        self._vac_scenter = VigilantAttributeConnector(self.stream.spectrumBandwidth,
                          self._txt_scenter,
                          lambda r: self._txt_scenter.SetValue((r[0] + r[1]) / 2),
                          get_center,
                          events=wx.EVT_COMMAND_ENTER)

        tooltip_txt = "Bandwidth of the spectrum"
        lbl_sbw = wx.StaticText(self._panel, -1, "Bandwidth")
        lbl_sbw.SetToolTipString(tooltip_txt)
        self._txt_sbw = UnitFloatCtrl(self._panel, -1,
                    (wl[1] - wl[0]),
                    style=wx.NO_BORDER,
                    size=(-1, 14),
                    min_val=0,
                    max_val=(wl_rng[1] - wl_rng[0]),
                    unit=self.stream.spectrumBandwidth.unit)
        self._txt_sbw.SetBackgroundColour(BACKGROUND_COLOUR)
        self._txt_sbw.SetForegroundColour(FOREGROUND_COLOUR_EDIT)
        self._txt_sbw.SetToolTipString(tooltip_txt)

        def get_bandwidth(va=self.stream.spectrumBandwidth, ctrl=self._txt_sbw):
            """
            Return the low/high values for the bandwidth, from the requested bandwidth
            """
            # ensure the low/high values are always within the allowed range
            wl = va.value
            wl_rng = (va.range[0][0], va.range[1][1])

            center = (wl[0] + wl[1]) / 2
            max_width = max(center - wl_rng[0], wl_rng[1] - center) * 2
            req_width = ctrl.GetValue()
            new_width = max(min(max_width, req_width), max_width / 1024)

            if req_width != new_width:
                # VA might not change => update value ourselves
                ctrl.SetValue(new_width)

            wl = (center - new_width / 2, center + new_width / 2)
            return wl

        self._vac_sbw = VigilantAttributeConnector(self.stream.spectrumBandwidth,
                          self._txt_sbw,
                          lambda r: self._txt_sbw.SetValue(r[1] - r[0]),
                          get_bandwidth,
                          events=wx.EVT_COMMAND_ENTER)

        cb_wl_sz = wx.BoxSizer(wx.HORIZONTAL)
        cb_wl_sz.Add(lbl_scenter, 0,
                  flag=wx.ALIGN_CENTRE_VERTICAL | wx.LEFT,
                  border=5)
        cb_wl_sz.Add(self._txt_scenter, 1,
                  flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.RIGHT | wx.LEFT,
                  border=5)
        cb_wl_sz.Add(lbl_sbw, 0,
                  flag=wx.ALIGN_CENTRE_VERTICAL | wx.LEFT,
                  border=5)
        cb_wl_sz.Add(self._txt_sbw, 1,
                  flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.RIGHT | wx.LEFT,
                  border=5)
        self._gbs.Add(cb_wl_sz, (self.row_count, 0), span=(1, 3),
                      flag=wx.BOTTOM | wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND,
                      border=5)
        self.row_count += 1

        # TODO: should the stream have a way to know when the raw data has changed? => just a spectrum VA, like histogram VA
        self.stream.image.subscribe(self.on_new_spec_data, init=True)

    @wxlimit_invocation(0.2)
    def on_new_spec_data(self, image):
        # Display the global spectrum in the visual range slider
        gspec = self.stream.getMeanSpectrum()
        if len(gspec) <= 1:
            logging.warning("Strange spectrum of len %d", len(gspec))
            return

        # make it fit between 0 and 1
        if len(gspec) >= 5:
            # skip the 2 biggest peaks
            s_values = numpy.sort(gspec)
            mins, maxs = s_values[2], s_values[-3]
        else:
            mins, maxs = gspec.min(), gspec.max()

        base = min(mins, 0) # to make sure big values look big
        try:
            coef = 1. / (maxs - base)
        except ZeroDivisionError:
            coef = 1

        gspec = gspec + base
        gspec *= coef
        wx.CallAfter(self._sld_spec.SetContent, gspec.tolist())


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
    STREAM_ORDER = [acq.stream.SEMStream,
                    acq.stream.StaticSEMStream,
                    acq.stream.BrightfieldStream,
                    acq.stream.CameraNoLightStream,
                    acq.stream.StaticStream,
                    acq.stream.FluoStream,
                    acq.stream.SpectrumStream,
                    acq.stream.ARStream,
                    ]


    def __init__(self, *args, **kwargs):

        add_btn = kwargs.pop('add_button', False)

        wx.Panel.__init__(self, *args, **kwargs)

        self.stream_panels = []
        self.menu_actions = collections.OrderedDict()  # title => callback

        self._sz = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self._sz)

        msg = "No streams available."

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

        msg = "Stream of unknown order type %s"
        logging.warning(msg, stream.__class__.__name__)
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
        """ Return the number of streams contained within the StreamBar """
        return len(self.stream_panels)

    def add_stream(self, spanel, show=True):
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


    def remove_stream_panel(self, spanel):
        """
        Removes a stream panel
        Deletion of the actual stream must be done separately.
        """
        self.stream_panels.remove(spanel)
        # CallAfter is used to make sure all GUI updates occur in the main
        # thread
        wx.CallAfter(spanel.Destroy)
        self._set_warning()

    def clear(self):
        """ Remove all stream panels """
        for p in list(self.stream_panels):
            self.remove_stream_panel(p)

    def _set_warning(self):
        """ Display a warning text when no streams are present, or show it
        otherwise.
        """
        if self.txt_no_stream is not None:
            self.txt_no_stream.Show(self.is_empty())

    def get_actions(self):
        return self.menu_actions

    # TODO need to have actions enabled/disabled depending on the context:
    #  * if microscope is off/pause => disabled
    #  * if focused view is not about this type of stream => disabled
    #  * if there can be only one stream of this type, and it's already present
    #    => disabled
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

