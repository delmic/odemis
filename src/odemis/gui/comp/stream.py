# -*- coding: utf-8 -*-
"""
:author: Rinze de Laat <laat@delmic.com>
:copyright: © 2012 Rinze de Laat, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
    General Public License version 2 as published by the Free Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without
    even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
    General Public License for more details.

    You should have received a copy of the GNU General Public License along with Odemis. If not,
    see http://www.gnu.org/licenses/.

This module contains classes needed to construct stream panels.

Stream panels are custom, specialized controls that allow the user to view and manipulate various
data streams coming from the microscope.

"""

from __future__ import division

import collections
import logging
import wx
import wx.lib.newevent
from wx.lib.pubsub import pub

from decorator import decorator

from odemis import acq
from odemis.acq.stream import OpticalStream
from odemis.gui import FG_COLOUR_EDIT, FG_COLOUR_MAIN, BG_COLOUR_MAIN, BG_COLOUR_STREAM, \
    FG_COLOUR_DIS, FG_COLOUR_WARNING, FG_COLOUR_ERROR
from odemis.gui.comp.combo import ComboBox
from odemis.gui.comp.foldpanelbar import FoldPanelItem, FoldPanelBar
from odemis.gui.comp.slider import UnitFloatSlider, VisualRangeSlider, UnitIntegerSlider
from odemis.gui.comp.text import SuggestTextCtrl, UnitFloatCtrl, FloatTextCtrl
from odemis.gui.util import call_in_wx_main, wxlimit_invocation, dead_object_wrapper
from odemis.gui.util.widgets import VigilantAttributeConnector
from odemis.util import fluo
from odemis.util.conversion import wave2rgb
import odemis.gui.comp.buttons as buttons
import odemis.gui.img.data as img


stream_remove_event, EVT_STREAM_REMOVE = wx.lib.newevent.NewEvent()

# Values to control which option is available
OPT_NAME_EDIT = 1  # allow the renaming of the stream (for one time only)
OPT_BTN_REMOVE = 2  # remove the stream entry
OPT_BTN_SHOW = 4  # show/hide the stream image
OPT_BTN_UPDATE = 8  # update/stop the stream acquisition
OPT_BTN_TINT = 16  # tint of the stream (if the VA exists)

CAPTION_PADDING_RIGHT = 5
ICON_WIDTH, ICON_HEIGHT = 16, 16


@decorator
def control_bookkeeper(f, self, *args, **kwargs):
    """ Clear the default message, if needed, and advance the row count """
    result = f(self, *args, **kwargs)

    # This makes the 2nd column's width variable
    if not self.gb_sizer.IsColGrowable(1):
        self.gb_sizer.AddGrowableCol(1)

    # Redo FoldPanelBar layout
    win = self
    while not isinstance(win, FoldPanelBar):
        win = win.Parent
    win.Layout()
    self.num_rows += 1
    return result


class StreamPanelHeader(wx.Control):
    """ This class describes a clickable control responsible for expanding and collapsing the
    StreamPanel to which it belongs.

    It can also contain various sub buttons that allow for stream manipulation.

    """

    BUTTON_SIZE = (18, 18)  # The pixel size of the button
    BUTTON_BORDER_SIZE = 8  # Border space around the buttons

    def __init__(self, parent, wid=wx.ID_ANY, pos=wx.DefaultPosition, size=wx.DefaultSize,
                 style=wx.NO_BORDER,
                 options=(OPT_BTN_REMOVE | OPT_BTN_SHOW | OPT_BTN_UPDATE | OPT_BTN_TINT)):
        assert(isinstance(parent, StreamPanel))
        super(StreamPanelHeader, self).__init__(parent, wid, pos, size, style)

        # This style enables us to draw the background with our own paint event handler
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)

        self._options = options

        # Callback when the label changes: (string (text) -> None)
        self.label_change_callback = None

        # Create and add sizer and populate with controls
        self._sz = wx.BoxSizer(wx.HORIZONTAL)

        # Fold indicator icon, drawn directly in the background in a fixed position
        self._foldIcons = wx.ImageList(16, 16)
        self._foldIcons.Add(img.getarr_down_sBitmap())
        self._foldIcons.Add(img.getarr_right_sBitmap())

        # Add the needed controls to the sizer

        self.btn_remove = self._add_remove_btn() if options & OPT_BTN_REMOVE else None
        if options & OPT_NAME_EDIT:
            self.ctrl_label = self._add_suggest_ctrl()
        else:
            self.ctrl_label = self._add_label_ctrl()
        self.btn_tint = self._add_tint_btn()
        self.btn_show = self._add_visibility_btn() if options & OPT_BTN_SHOW else None
        self.btn_update = self._add_update_btn() if options & OPT_BTN_UPDATE else None

        # The spacer is responsible for creating padding on the right side of the header panel
        self._sz.AddSpacer((64, 16))

        # Set the sizer of the Control
        self.SetSizerAndFit(self._sz)

        self.Bind(wx.EVT_SIZE, self.on_size)
        self.Layout()

    # Control creation methods

    def _add_remove_btn(self):
        """ Add a button for stream removal """
        btn_rem = buttons.ImageButton(
            self,
            wx.ID_ANY,
            img.getico_rem_strBitmap(),
            size=self.BUTTON_SIZE,
            background_parent=self.Parent
        )
        btn_rem.SetBitmaps(img.getico_rem_str_hBitmap())
        btn_rem.SetToolTipString("Remove stream")
        self._add_ctrl(btn_rem)
        return btn_rem

    def _add_suggest_ctrl(self):
        """ Add a suggest control to the header panel """
        suggest_ctrl = SuggestTextCtrl(self, id=-1, value=self.Parent.stream.name.value)
        suggest_ctrl.SetBackgroundColour(self.Parent.GetBackgroundColour())
        suggest_ctrl.SetForegroundColour(FG_COLOUR_EDIT)
        suggest_ctrl.Bind(wx.EVT_COMMAND_ENTER, self._on_label_change)

        self._add_ctrl(suggest_ctrl, stretch=True)
        return suggest_ctrl

    def _add_label_ctrl(self):
        """ Add a label control to the header panel """
        label_ctrl = wx.StaticText(self, -1, self.Parent.stream.name.value)
        self._add_ctrl(label_ctrl, stretch=True)
        return label_ctrl

    def _add_tint_btn(self):
        """ Add a tint button to the stream header if the stream has a tint attribute """

        if not hasattr(self.Parent.stream, "tint"):
            return None

        tint_btn = buttons.ColourButton(
            self, -1,
            bitmap=img.getemptyBitmap(),
            size=self.BUTTON_SIZE,
            colour=self.Parent.stream.tint.value,
            background_parent=self.Parent,
            use_hover=True
        )
        tint_btn.SetToolTipString("Select colour")

        # Tint event handlers
        tint_btn.Bind(wx.EVT_BUTTON, self._on_tint_click)
        self.Parent.stream.tint.subscribe(self._on_tint_value)

        self._add_ctrl(tint_btn)
        return tint_btn

    def _add_visibility_btn(self):
        """ Add the visibility toggle button to the stream panel header """
        visibility_btn = buttons.ImageToggleButton(
            self, -1,
            bitmap=img.getico_eye_closedBitmap(),
            size=self.BUTTON_SIZE,
            background_parent=self.Parent
        )
        visibility_btn.SetBitmaps(
            img.getico_eye_closed_hBitmap(),
            img.getico_eye_openBitmap(),
            img.getico_eye_open_hBitmap()
        )
        visibility_btn.SetToolTipString("Show stream")
        self._add_ctrl(visibility_btn)
        return visibility_btn

    def _add_update_btn(self):
        """ Add a button for (de)activation of the stream """
        update_btn = buttons.ImageToggleButton(
            self, -1,
            bitmap=img.getico_pauseBitmap(),
            size=self.BUTTON_SIZE,
            background_parent=self.Parent
        )
        update_btn.SetBitmaps(
            img.getico_pause_hBitmap(),
            img.getico_playBitmap(),
            img.getico_play_hBitmap()
        )
        update_btn.SetToolTipString("Update stream")

        self._vac_updated = VigilantAttributeConnector(
            self.Parent.stream.should_update,
            update_btn,
            update_btn.SetToggle,
            update_btn.GetToggle,
            events=wx.EVT_BUTTON
        )
        self._add_ctrl(update_btn)
        return update_btn

    def _add_ctrl(self, ctrl, stretch=False):
        """ Add the given control to the header panel

        :param ctrl: (wx.Control) Control to add to the header panel
        :param stretch: True if the control should expand to fill space

        """

        # Only the first element has a left border
        border = wx.ALL if self._sz.IsEmpty() else wx.RIGHT

        self._sz.Add(
            ctrl,
            proportion=1 if stretch else 0,
            flag=(border | wx.ALIGN_CENTRE_VERTICAL | wx.RESERVE_SPACE_EVEN_IF_HIDDEN),
            border=self.BUTTON_BORDER_SIZE
        )

    # END Control creation methods

    # Layout and painting

    def on_size(self, event):
        """ Handle the wx.EVT_SIZE event for the Expander class """
        self.SetSize((self.Parent.GetSize().x, -1))
        self.Layout()
        self.Refresh()
        event.Skip()

    def on_draw_expander(self, dc):
        """ Draw the expand/collapse arrow icon

        It needs to be called from the parent's paint event handler.
        """
        win_rect = self.GetRect()
        x_pos = win_rect.GetRight() - ICON_WIDTH - CAPTION_PADDING_RIGHT

        self._foldIcons.Draw(
            1 if self.Parent.collapsed else 0,
            dc,
            x_pos,
            (win_rect.GetHeight() - ICON_HEIGHT) // 2,
            wx.IMAGELIST_DRAW_TRANSPARENT
        )

    # END Layout and painting

    # Show/hide/disable controls

    def _show_ctrl(self, ctrl, show):
        """ Show or hide the given control """
        if ctrl:
            self._sz.Show(ctrl, show)
            self._sz.Layout()

    def show_remove_btn(self, show):
        """ Show or hide the remove button """
        self._show_ctrl(self.btn_remove, show)

    def show_updated_btn(self, show):
        """ Show or hide the update button """
        self._show_ctrl(self.btn_update, show)

    def show_show_btn(self, show):
        """ Show or hide the show button """
        self._show_ctrl(self.btn_show, show)

    def show_tint_btn(self, show):
        """ Show or hide the tint button """
        self._show_ctrl(self.btn_tint, show)

    def enable_updated_btn(self, enabled):
        """ Enable or disable the update button """
        self.btn_update.Enable(enabled)

    def to_static_mode(self):
        """ Remove or disable the controls not needed for a static view of the stream """
        self.show_remove_btn(False)
        self.show_updated_btn(False)
        if isinstance(self.ctrl_label, SuggestTextCtrl):
            self.ctrl_label.Disable()

    def to_locked_mode(self):
        """ Remove or disable all controls """
        self.to_static_mode()
        self.show_show_btn(False)

    # END Show/hide/disable controls

    # GUI event handlers

    def _on_label_change(self, evt):
        """ Call the label change callback when the label value changes """
        if callable(self.label_change_callback):
            self.label_change_callback(self.ctrl_label.GetValue())

    @call_in_wx_main
    def _on_tint_value(self, colour):
        """ Update the colour button to reflect the provided colour """
        self.btn_tint.set_colour(colour)

    def _on_tint_click(self, evt):
        """ Handle the mouse click event on the tint button """
        # Remove the hover effect
        self.btn_tint.OnLeave(evt)

        # Set default colour to the current value
        cldata = wx.ColourData()
        cldata.SetColour(wx.Colour(*self.Parent.stream.tint.value))

        dlg = wx.ColourDialog(self, cldata)

        if dlg.ShowModal() == wx.ID_OK:
            colour = dlg.ColourData.GetColour().Get()  # convert to a 3-tuple
            logging.debug("Colour %r selected", colour)
            # Setting the VA will automatically update the button's colour
            self.Parent.stream.tint.value = colour

    # END GUI event handlers

    def set_label_choices(self, choices):
        """ Assign a list of predefined labels to the suggest control form which the user may choose

        :param choices: [str]

        """
        try:
            self.ctrl_label.SetChoices(choices)
        except AttributeError:
            raise TypeError("SuggestTextCtrl required, %s found!!" % type(self.ctrl_label))

    def set_focus_on_label(self):
        """ Set the focus on the label (and select the text if it's editable) """
        self.ctrl_label.SetFocus()
        if self._options & OPT_NAME_EDIT:
            self.ctrl_label.SelectAll()


class StreamPanel(wx.Panel):
    """ The StreamPanel class, a special case collapsible panel.

    The StreamPanel consists of the following widgets:

        StreamPanel
            BoxSizer
                StreamPanelHeader
                Panel
                    BoxSizer
                        GridBagSizer

    Additional controls can be added to the GridBagSizer in the 'finalize' method.

    The controls contained within a StreamPanel are typically connected to the VigilantAttribute
    properties of the Stream it's representing.

    """

    def __init__(self, parent, stream, options=0,
                 wid=wx.ID_ANY, pos=wx.DefaultPosition, size=wx.DefaultSize,
                 style=wx.CP_DEFAULT_STYLE, name="StreamPanel", collapsed=False):
        """
        :param parent: (StreamBar) The parent widget.
        :param stream: (Stream) The stream data model to be displayed to and
            modified by the user.
        :param tab_data: (MicroscopyGUIData) The microscope data model,
            TODO: This parameter and related property should be moved to the stream controller!

        """

        assert(isinstance(parent, StreamBar))
        wx.Panel.__init__(self, parent, wid, pos, size, style, name)

        self.stream = stream  # TODO: Should this also be moved to the StreamController?
        # Dye attributes
        self._btn_excitation = None
        self._btn_emission = None

        # Appearance
        # self._agwStyle = agwStyle | wx.CP_NO_TLW_RESIZE  # |wx.CP_GTK_EXPANDER
        self.SetBackgroundColour(BG_COLOUR_STREAM)
        self.SetForegroundColour(FG_COLOUR_MAIN)

        # State

        self._collapsed = collapsed

        # Child widgets

        self.main_sizer = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self.main_sizer)

        self._header = None
        self._panel = None

        self._prev_drange = None

        self.gb_sizer = wx.GridBagSizer()

        # Counter that keeps track of the number of rows containing controls inside this panel
        self.num_rows = 0

        # Event handling
        self.Bind(wx.EVT_SIZE, self.OnSize)

        self._create_controls()

    def _create_controls(self):
        """ Set up the basic structure for the controls that are going to be used """

        # Create stream header

        expand_opt = (OPT_BTN_REMOVE | OPT_BTN_SHOW | OPT_BTN_UPDATE | OPT_BTN_TINT)

        if (
                self._has_dye(self.stream) and
                not (self.stream.excitation.readonly or self.stream.emission.readonly)
        ):
            expand_opt |= OPT_NAME_EDIT

        self._header = StreamPanelHeader(self, options=expand_opt)
        self._header.Bind(wx.EVT_LEFT_UP, self.on_toggle)
        self._header.Bind(wx.EVT_PAINT, self.on_draw_expander)

        self.Bind(wx.EVT_BUTTON, self.on_button, self._header)

        self._header.btn_remove.Bind(wx.EVT_BUTTON, self.on_remove_btn)
        self._header.btn_show.Bind(wx.EVT_BUTTON, self.on_visibility_btn)

        if wx.Platform == "__WXMSW__":
            self._header.Bind(wx.EVT_LEFT_DCLICK, self.on_button)

        self.main_sizer.Add(self._header, 0, wx.EXPAND)

        # Create the control panel

        self._panel = wx.Panel(self, style=wx.TAB_TRAVERSAL | wx.NO_BORDER)

        # Add a simple sizer so we can create padding for the panel
        border_sizer = wx.BoxSizer(wx.HORIZONTAL)
        border_sizer.Add(self.gb_sizer, border=5, flag=wx.ALL | wx.EXPAND, proportion=1)

        self._panel.SetSizer(border_sizer)

        self._panel.SetBackgroundColour(BG_COLOUR_MAIN)
        self._panel.SetForegroundColour(FG_COLOUR_MAIN)
        self._panel.SetFont(self.GetFont())

        self.collapse()

        self.main_sizer.Add(self._panel, 0, wx.EXPAND)

    @property
    def collapsed(self):
        return self._collapsed

    @property
    def header_change_callback(self):
        return self._header.label_change_callback

    @header_change_callback.setter
    def header_change_callback(self, f):
        self._header.label_change_callback = f

    def set_header_choices(self, choices):
        self._header.set_label_choices(choices)

    def flatten(self):
        """ Unfold the stream panel and hide the header """
        self.collapse(False)
        self._header.Show(False)

    def set_focus_on_label(self):
        """ Focus the text label in the header """
        self._header.set_focus_on_label()

    def Layout(self, *args, **kwargs):
        """ Layout the StreamPanel. """

        if not self._header or not self._panel or not self.main_sizer:
            return False  # we need to complete the creation first!

        oursz = self.GetSize()

        # move & resize the button and the static line
        self.main_sizer.SetDimension(0, 0, oursz.GetWidth(),
                                     self.main_sizer.GetMinSize().GetHeight())
        self.main_sizer.Layout()

        if not self._collapsed:
            # move & resize the container window
            yoffset = self.main_sizer.GetSize().GetHeight()
            if oursz.y - yoffset > 0:
                self._panel.SetDimensions(0, yoffset, oursz.x, oursz.y - yoffset)
                # this is very important to make the pane window layout show
                # correctly
                self._panel.Show()
                self._panel.Layout()

        return True

    def DoGetBestSize(self, *args, **kwargs):
        """ Gets the size which best suits the window

        For a control, it would be the minimal size which doesn't truncate the control, for a panel
        the same size as it would have after a call to `Fit()`.

        TODO: This method seems deprecated. Test if it's really so.

        """

        # do not use GetSize() but rather GetMinSize() since it calculates
        # the required space of the sizer
        sz = self.main_sizer.GetMinSize()

        # when expanded, we need more space
        if not self._collapsed:
            pbs = self._panel.GetBestSize()
            sz.width = max(sz.GetWidth(), pbs.x)
            sz.height = sz.y + pbs.y

        return sz

    def Destroy(self, *args, **kwargs):
        """ Delete the widget from the GUI

        TODO: Is this method still necessary? If it's stull needed, it's content can probably still
        be cleaned up.

        """

        # Avoid receiving data after the object is deleted
        if hasattr(self, "_sld_hist"):
            self.stream.histogram.unsubscribe(self.on_histogram)
        if hasattr(self, "_sld_spec"):
            self.stream.image.unsubscribe(self.on_new_spec_data)

        fpb_item = self.Parent
        super(StreamPanel, self).Destroy(*args, **kwargs)
        fpb_item.fit_streams()

    def set_visible(self, visible):
        """ Set the "visible" toggle button of the stream panel """
        self._header.btn_show.SetToggle(visible)

    def collapse(self, collapse=None):
        """ Collapses or expands the pane window """

        if collapse is not None and self._collapsed == collapse:
            return

        self.Freeze()

        # update our state
        self._panel.Show(not collapse)
        self._collapsed = collapse

        wx.CallAfter(self.Parent.fit_streams)

        self.Thaw()

    # GUI events: update the stream when the user changes the values

    def on_remove_btn(self, evt):
        logging.debug("Remove button clicked for '%s'", self.stream.name.value)

        # generate EVT_STREAM_REMOVE
        event = stream_remove_event(spanel=self)
        wx.PostEvent(self, event)

    def on_visibility_btn(self, evt):
        # TODO: Move to controller. Screen widget should not need to know about
        # microscopes and focused views.
        view = self._tab_data_model.focussedView.value
        if not view:
            return
        if self._header.btn_show.GetToggle():
            logging.debug("Showing stream '%s'", self.stream.name.value)
            view.addStream(self.stream)
        else:
            logging.debug("Hiding stream '%s'", self.stream.name.value)
            view.removeStream(self.stream)

    # Manipulate expander buttons

    def show_updated_btn(self, show):
        self._header.show_updated_btn(show)

    def enable_updated_btn(self, enabled):
        self._header.enable_updated_btn(enabled)

    def show_remove_btn(self, show):
        self._header.show_remove_btn(show)

    def show_visible_btn(self, show):
        self._header.show_show_btn(show)

    def OnSize(self, event):
        """ Handles the wx.EVT_SIZE event for StreamPanel
        """
        self.Layout()
        event.Skip()

    def on_toggle(self, evt):
        """ Detect click on the collapse button of the StreamPanel """

        w = evt.GetEventObject().GetSize().GetWidth()

        if evt.GetX() > w * 0.85:
            self.collapse(not self._collapsed)
        else:
            evt.Skip()

    def on_button(self, event):
        """ Handles the wx.EVT_BUTTON event for StreamPanel """

        if event.GetEventObject() != self._header:
            event.Skip()
            return

        self.collapse(not self._collapsed)

    def on_draw_expander(self, event):
        """ Handle the ``wx.EVT_PAINT`` event for the stream panel
        :note: This is a drawing routine to paint the GTK-style expander.
        """

        dc = wx.AutoBufferedPaintDC(self._header)
        dc.SetBackground(wx.Brush(self.GetBackgroundColour()))
        dc.Clear()

        self._header.on_draw_expander(dc)

    def to_static_mode(self):
        """ Hide or make read-only any button or data that should not change during acquisition """
        self._header.to_static_mode()

    def to_locked_mode(self):
        """ Hide or make read-only all buttons and data controls"""
        self._header.to_static_mode()
        self._header.to_locked_mode()

    # ===== For brightness/contrast

    @control_bookkeeper
    def add_autobc_ctrls(self):
        """ Create and return controls needed for (auto) brightness and contrast manipulation """

        btn_autobc = buttons.ImageTextToggleButton(self._panel, wx.ID_ANY,
                                                   img.getbtn_contrastBitmap(),
                                                   label="Auto", label_delta=1,
                                                   style=wx.ALIGN_RIGHT)

        btn_autobc.SetToolTipString("Toggle auto brightness and contrast")
        btn_autobc.SetBitmaps(bmp_h=img.getbtn_contrast_hBitmap(),
                              bmp_sel=img.getbtn_contrast_aBitmap())
        btn_autobc.SetForegroundColour("#000000")

        lbl_bc_outliers = wx.StaticText(self._panel, -1, "Outliers")
        sld_bc_outliers = UnitFloatSlider(
            self._panel,
            value=self.stream.auto_bc_outliers.value,
            min_val=self.stream.auto_bc_outliers.range[0],
            max_val=self.stream.auto_bc_outliers.range[1],
            unit="%",
            scale="cubic",
            accuracy=2
        )

        sld_bc_outliers.SetToolTipString("Percentage of values to ignore "
                                         "in auto brightness and contrast")

        autobc_sz = wx.BoxSizer(wx.HORIZONTAL)
        autobc_sz.Add(btn_autobc, 0, flag=wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT, border=5)
        autobc_sz.Add(lbl_bc_outliers, 0, flag=wx.ALIGN_CENTRE_VERTICAL | wx.LEFT, border=5)
        autobc_sz.Add(sld_bc_outliers, 1,
                      flag=wx.ALIGN_CENTRE_VERTICAL | wx.LEFT | wx.EXPAND, border=5)
        self.gb_sizer.Add(autobc_sz, (self.num_rows, 0), span=(1, 3),
                          flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.ALL, border=5)

        return btn_autobc, sld_bc_outliers

    @control_bookkeeper
    def add_outliers_ctrls(self):
        hist_min = self.stream.intensityRange.range[0][0]
        hist_max = self.stream.intensityRange.range[1][1]

        sld_hist = VisualRangeSlider(self._panel, size=(-1, 40),
                                     value=self.stream.intensityRange.value,
                                     min_val=hist_min, max_val=hist_max)

        sld_hist.SetBackgroundColour("#000000")

        # span is 2, because emission/excitation have 2 controls
        self.gb_sizer.Add(sld_hist, pos=(self.num_rows, 0), span=(1, 3), border=5,
                          flag=wx.EXPAND | wx.TOP | wx.RIGHT | wx.LEFT)
        self.num_rows += 1

        # Low/ High values are in raw data. So it's typically uint, but could
        # be float for some weird cases. So we make them float, with high
        # accuracy to avoid rounding.

        lbl_lowi = wx.StaticText(self._panel, -1, "Low")
        tooltip_txt = "Value mapped to black"
        lbl_lowi.SetToolTipString(tooltip_txt)

        txt_lowi = FloatTextCtrl(self._panel, -1,
                                 self.stream.intensityRange.value[0],
                                 style=wx.NO_BORDER, size=(-1, 14),
                                 min_val=hist_min, max_val=hist_max,
                                 step=1, accuracy=6)
        txt_lowi.SetBackgroundColour(BG_COLOUR_MAIN)
        txt_lowi.SetForegroundColour(FG_COLOUR_EDIT)
        txt_lowi.SetToolTipString(tooltip_txt)

        lbl_highi = wx.StaticText(self._panel, -1, "High")
        tooltip_txt = "Value mapped to white"
        lbl_highi.SetToolTipString(tooltip_txt)
        txt_highi = FloatTextCtrl(self._panel, -1,
                                  self.stream.intensityRange.value[1],
                                  style=wx.NO_BORDER, size=(-1, 14),
                                  min_val=hist_min, max_val=hist_max,
                                  step=1, accuracy=6)
        txt_highi.SetBackgroundColour(BG_COLOUR_MAIN)
        txt_highi.SetForegroundColour(FG_COLOUR_EDIT)
        txt_highi.SetToolTipString(tooltip_txt)

        lh_sz = wx.BoxSizer(wx.HORIZONTAL)

        lh_sz.Add(lbl_lowi, 0, border=5, flag=wx.ALIGN_CENTRE_VERTICAL | wx.LEFT)
        lh_sz.Add(txt_lowi, 1, border=5,
                  flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.RIGHT | wx.LEFT)
        lh_sz.Add(lbl_highi, 0, border=5, flag=wx.ALIGN_CENTRE_VERTICAL | wx.LEFT)
        lh_sz.Add(txt_highi, 1, border=5,
                  flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.RIGHT | wx.LEFT)
        self.gb_sizer.Add(lh_sz, (self.num_rows, 0), span=(1, 3), border=5,
                          flag=wx.BOTTOM | wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND)

        return sld_hist, txt_lowi, txt_highi

    # ===== For separate Optical stream settings

    def _add_side_label(self, label_text):
        """ Add a text label to the control grid

        This method should only be called from other methods that add control to the control grid

        :param label_text: (str)
        :return: (wx.StaticText)

        """

        lbl_ctrl = wx.StaticText(self._panel, -1, label_text)
        self.gb_sizer.Add(lbl_ctrl, (self.num_rows, 0),
                          flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)
        return lbl_ctrl

    # Setting Control Addition Methods

    @control_bookkeeper
    def add_exposure_time_ctrl(self, value=None, conf=None):
        lbl_ctrl = self._add_side_label("Exposure time")
        value_ctrl = UnitFloatSlider(self._panel, value=value, **conf if conf else {})
        self.gb_sizer.Add(value_ctrl, (self.num_rows, 1), span=(1, 3),
                          flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.ALL, border=5)

        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_light_power_ctrl(self, value=None, conf=None):
        lbl_ctrl = self._add_side_label("Power")
        value_ctrl = UnitFloatSlider(self._panel, value=value, **conf if conf else {})
        self.gb_sizer.Add(value_ctrl, (self.num_rows, 1), span=(1, 3),
                          flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.ALL, border=5)
        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_divider(self):
        line_ctrl = wx.StaticLine(self._panel, size=(-1, 1))
        self.gb_sizer.Add(line_ctrl, (self.num_rows, 0), span=(1, 3),
                          flag=wx.ALL | wx.EXPAND, border=5)

    @control_bookkeeper
    def add_dye_excitation_ctrl(self, excitation_va):
        lbl_ctrl, value_ctrl, lbl_exc_peak, btn_excitation = self._add_filter_line("Excitation",
                                                                                   excitation_va)
        return lbl_ctrl, value_ctrl, lbl_exc_peak, btn_excitation

    @control_bookkeeper
    def add_dye_emission_ctrl(self, emission_va):
        lbl_ctrl, value_ctrl, lbl_em_peak, btn_emission = self._add_filter_line("Emission",
                                                                                emission_va)
        return lbl_ctrl, value_ctrl, lbl_em_peak, btn_emission

    def _add_filter_line(self, name, va, center_wl=0, va_2_ctrl=None, ctrl_2_va=None):
        """ Create the controls for dye emission/excitation colour filter setting

        :param name: (str): the label name
        :param va: (VigilantAttribute) the VA for the emission/excitation (contains a band)
        :param center_wl: (float) center wavelength of the current band of the VA

        :return: (4 wx.Controls) the respective controls created

        """

        # Note: va.value is in m, but we present everything in nm
        lbl_ctrl = self._add_side_label(name)

        # will contain both the combo box and the peak label
        exc_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.gb_sizer.Add(exc_sizer, (self.num_rows, 1), flag=wx.EXPAND)

        band = va.value

        if va.readonly or len(va.choices) <= 1:
            hw_set = wx.TextCtrl(self._panel,
                                 value=self._to_readable_band(band),
                                 size=(-1, 16),
                                 style=wx.BORDER_NONE | wx.TE_READONLY)
            hw_set.SetBackgroundColour(self._panel.BackgroundColour)
            hw_set.SetForegroundColour(FG_COLOUR_DIS)
            exc_sizer.Add(hw_set, 1,
                          flag=wx.LEFT | wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL, border=5)
        else:
            hw_set = ComboBox(self._panel,
                              value=self._to_readable_band(band),
                              size=(-1, 16),
                              style=wx.CB_READONLY | wx.BORDER_NONE)

            ex_choices = sorted(va.choices, key=self._get_one_center)
            for b in ex_choices:
                hw_set.Append(self._to_readable_band(b), b)

            # To avoid catching mouse wheels events when scrolling the panel
            hw_set.Bind(wx.EVT_MOUSEWHEEL, lambda e: None)

            exc_sizer.Add(hw_set, 1,
                          flag=wx.ALL | wx.ALIGN_CENTRE_VERTICAL,
                          border=5)

        # Label for peak information
        lbl_peak = wx.StaticText(self._panel)
        exc_sizer.Add(lbl_peak, 1,
                      flag=wx.ALL | wx.ALIGN_CENTRE_VERTICAL | wx.ALIGN_LEFT,
                      border=5)

        # A button, but not clickable, just to show the wavelength
        # If a dye is selected, the colour of the peak is used, otherwise we
        # use the hardware setting
        btn_color = buttons.ColourButton(self._panel, -1,
                                       bitmap=img.getemptyBitmap(),
                                       colour=wave2rgb(center_wl),
                                       background_parent=self._panel)
        self.gb_sizer.Add(btn_color,
                          (self.num_rows, 2),
                          flag=wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL | wx.ALIGN_RIGHT,
                          border=5)
        self.update_peak_label_fit(lbl_peak, btn_color, None, band)

        return lbl_ctrl, hw_set, lbl_peak, btn_color

    # END Setting Control Addition Methods

    # ====== For the dyes

    # FIXME: Remove this method
    @staticmethod
    def _has_dye(stream):
        """
        return True if the stream looks like a stream using dye.
        """
        return hasattr(stream, "excitation") and hasattr(stream, "emission")

    @staticmethod
    def _to_readable_band(band):
        """
        Convert a emission or excitation band into readable text
        band ((list of) tuple of 2 or 5 floats): either the min/max
          of the band or the -99%, -25%, middle, +25%, +99% of the band in m.
        return (unicode): readable string.
        """
        # if one band => center/bandwidth nm (bandwidth not displayed if < 5nm)
        #   ex: 453/19 nm
        # if multi-band => center, center... nm
        #   ex: 453, 568, 968 nm
        if not isinstance(band[0], collections.Iterable):
            b = band
            center_nm = int(round(fluo.get_center(b) * 1e9))

            width = b[-1] - b[0]
            if width > 5e-9:
                width_nm = int(round(width * 1e9))
                return u"%d/%d nm" % (center_nm, width_nm)
            else:
                return u"%d nm" % center_nm
        else:  # multi-band
            centers = []
            for c in fluo.get_center(band):
                center_nm = int(round(c * 1e9))
                centers.append(u"%d" % center_nm)
            return u", ".join(centers) + " nm"

    @staticmethod
    def _get_one_center(band):
        """
        Return the center of a band, and if it's a multi-band, return just one
        of the centers.
        return (float): wavelength in m
        """
        if isinstance(band[0], collections.Iterable):
            return fluo.get_center(band[0])
        else:
            return fluo.get_center(band)

    @staticmethod
    def update_peak_label_fit(lbl_ctrl, col_ctrl, wl, band):
        """ Changes the colour & tooltip of the peak label based on how well it fits to the given
        band setting.

        :param lbl_ctrl: (wx.StaticText) control to update the foreground colour
        :param col_ctrl: (wx.ButtonColour) just to update the tooltip
        :param wl: (None or float) the wavelength of peak of the dye or None if no dye
        :param band: ((list of) tuple of 2 or 5 floats) the band of the hw setting

        """

        if None in (lbl_ctrl, col_ctrl):
            return

        if wl is None:
            # No dye known => no peak information
            lbl_ctrl.LabelText = u""
            lbl_ctrl.SetToolTip(None)
            col_ctrl.SetToolTipString(u"Centre wavelength colour")
        else:
            wl_nm = int(round(wl * 1e9))
            lbl_ctrl.LabelText = u"Peak at %d nm" % wl_nm
            col_ctrl.SetToolTipString(u"Peak wavelength colour")

            fit = fluo.estimate_fit_to_dye(wl, band)
            # Update colour
            colour = {fluo.FIT_GOOD: FG_COLOUR_DIS,
                      fluo.FIT_BAD: FG_COLOUR_WARNING,
                      fluo.FIT_IMPOSSIBLE: FG_COLOUR_ERROR}[fit]
            lbl_ctrl.SetForegroundColour(colour)

            # Update tooltip string
            tooltip = {fluo.FIT_GOOD: u"The peak is inside the band %d→%d nm",
                       fluo.FIT_BAD: u"Some light might pass through the band %d→%d nm",
                       fluo.FIT_IMPOSSIBLE: u"The peak is too far from the band %d→%d nm"}[fit]
            if isinstance(band[0], collections.Iterable):  # multi-band
                band = fluo.find_best_band_for_dye(wl, band)
            low, high = [int(round(b * 1e9)) for b in (band[0], band[-1])]
            lbl_ctrl.SetToolTipString(tooltip % (low, high))

    def sync_tint_on_emission(self, ewl, xwl):
        """
        Set the tint to the same colour as emission, if no dye has been
         selected. If a dye is selected, it's dependent on the dye information.
        ewl ((tuple of) tuple of floats): emission wavelength
        wwl ((tuple of) tuple of floats): excitation wavelength
        """
        if self._dye_ewl is None: # if dye is used, keep the peak wavelength
            ewl_center = fluo.get_one_center_em(ewl, xwl)
            if self._dye_prev_ewl_center == ewl_center:
                return
            self._dye_prev_ewl_center = ewl_center
            colour = wave2rgb(ewl_center)
            logging.debug("Synchronising tint to %s", colour)
            self.stream.tint.value = colour

    # ===== Wavelength bandwidth for SpectrumSettingsStream

    def _has_wl(self, stream):
        """
        return True if the stream looks like a stream with wavelength
        """
        return hasattr(stream, "spectrumBandwidth")
                #and hasattr(stream, "fitToRGB")

    def _add_wl_controls(self):
        # ====== Top row, fit RGB toggle button

        self._btn_fit_rgb = buttons.ImageTextToggleButton(
                                                self._panel,
                                                wx.ID_ANY,
                                                img.getbtn_spectrumBitmap(),
                                                label="RGB",
                                                label_delta=1,
                                                style=wx.ALIGN_RIGHT)
        tooltip = "Toggle sub-bandwidths to Blue/Green/Red display"
        self._btn_fit_rgb.SetToolTipString(tooltip)
        self._btn_fit_rgb.SetBitmaps(bmp_h=img.getbtn_spectrum_hBitmap(),
                                     bmp_sel=img.getbtn_spectrum_aBitmap())
        self._btn_fit_rgb.SetForegroundColour("#000000")
        self.gb_sizer.Add(self._btn_fit_rgb,
                                 (self.num_rows, 0),
                                 flag=wx.LEFT | wx.TOP,
                                 border=5)
        self.num_rows += 1
        self._vac_fit_rgb = VigilantAttributeConnector(
            self.stream.fitToRGB,
            self._btn_fit_rgb,
            self._btn_fit_rgb.SetToggle,
            self._btn_fit_rgb.GetToggle,
            events=wx.EVT_BUTTON
        )

        # ====== Second row, center label, slider and value

        wl = self.stream.spectrumBandwidth.value
        wl_rng = (self.stream.spectrumBandwidth.range[0][0],
                  self.stream.spectrumBandwidth.range[1][1])
        self._sld_spec = VisualRangeSlider(self._panel,
                                           size=(-1, 40),
                                           value=wl,
                                           min_val=wl_rng[0],
                                           max_val=wl_rng[1])
        self._sld_spec.SetBackgroundColour("#000000")
        self._vac_center = VigilantAttributeConnector(
                                self.stream.spectrumBandwidth,
                                self._sld_spec,
                                events=wx.EVT_SLIDER)

        # span is 3, because emission/excitation have 2 controls
        self.gb_sizer.Add(self._sld_spec,
                                 pos=(self.num_rows, 0),
                                 span=(1, 3),
                                 flag=wx.EXPAND | wx.TOP | wx.LEFT | wx.RIGHT,
                                 border=5)
        self.num_rows += 1

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
                                          unit=self.stream.spectrumBandwidth.unit)  # m or px
        self._txt_scenter.SetBackgroundColour(BG_COLOUR_MAIN)
        self._txt_scenter.SetForegroundColour(FG_COLOUR_EDIT)
        self._txt_scenter.SetToolTipString(tooltip_txt)

        def get_center(va=self.stream.spectrumBandwidth, ctrl=self._txt_scenter):
            """
            Return the low/high values for the bandwidth, from the requested center
            """
            # ensure the low/high values are always within the allowed range
            wl = va.value
            wl_rng = (va.range[0][0], va.range[1][1])

            width = wl[1] - wl[0]
            ctr_rng = wl_rng[0] + width // 2, wl_rng[1] - width // 2
            req_center = ctrl.GetValue()
            new_center = min(max(ctr_rng[0], req_center), ctr_rng[1])

            if req_center != new_center:
                # VA might not change => update value ourselves
                ctrl.SetValue(new_center)

            return (new_center - width // 2, new_center + width // 2)

        self._vac_scenter = VigilantAttributeConnector(
            self.stream.spectrumBandwidth,
            self._txt_scenter,
            lambda r: self._txt_scenter.SetValue((r[0] + r[1]) / 2),
            get_center,
            events=wx.EVT_COMMAND_ENTER
        )

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
        self._txt_sbw.SetBackgroundColour(BG_COLOUR_MAIN)
        self._txt_sbw.SetForegroundColour(FG_COLOUR_EDIT)
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
            new_width = max(min(max_width, req_width), max_width // 1024)

            if req_width != new_width:
                # VA might not change => update value ourselves
                ctrl.SetValue(new_width)

            return (center - new_width // 2, center + new_width // 2)

        self._vac_sbw = VigilantAttributeConnector(
            self.stream.spectrumBandwidth,
            self._txt_sbw,
            lambda r: self._txt_sbw.SetValue(r[1] - r[0]),
            get_bandwidth,
            events=wx.EVT_COMMAND_ENTER
        )

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
        self.gb_sizer.Add(cb_wl_sz, (self.num_rows, 0), span=(1, 3),
                                 flag=wx.BOTTOM | wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND,
                                 border=5)
        self.num_rows += 1

        # TODO: should the stream have a way to know when the raw data has changed? => just a
        # spectrum VA, like histogram VA
        self.stream.image.subscribe(self.on_new_spec_data, init=True)

        # Add the selectionWidth VA
        if hasattr(self.stream, "selectionWidth"):
            lbl_selection_width = wx.StaticText(self._panel, -1, "Width")
            self._sld_selection_width = UnitIntegerSlider(
                self._panel,
                value=self.stream.selectionWidth.value,
                min_val=self.stream.selectionWidth.range[0],
                max_val=self.stream.selectionWidth.range[1],
                unit="px",
            )
            tooltip_txt = "Width of the point or line selected"
            lbl_selection_width.SetToolTipString(tooltip_txt)
            self._sld_selection_width.SetToolTipString(tooltip_txt)
            self._vac_selection_width = VigilantAttributeConnector(self.stream.selectionWidth,
                                                                   self._sld_selection_width,
                                                                   events=wx.EVT_SLIDER)

            self.gb_sizer.Add(lbl_selection_width,
                                     (self.num_rows, 0),
                                     flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.ALL,
                                     border=5)
            self.gb_sizer.Add(self._sld_selection_width,
                                     (self.num_rows, 1), span=(1, 2),
                                     flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.ALL,
                                     border=5)
            self.num_rows += 1


    @wxlimit_invocation(0.2)
    def on_new_spec_data(self, _):
        # Display the global spectrum in the visual range slider
        gspec = self.stream.getMeanSpectrum()
        if len(gspec) <= 1:
            logging.warning("Strange spectrum of len %d", len(gspec))
            return

        # make it fit between 0 and 1
        if len(gspec) >= 5:
            # skip the 2 biggest peaks
            s_values = numpy.sort(gspec)
            mins, maxs = s_values[0], s_values[-3]
        else:
            mins, maxs = gspec.min(), gspec.max()

        base = mins # for spectrum, 0 has little sense, just care of the min
        try:
            coef = 1 / (maxs - base)
        except ZeroDivisionError:
            coef = 1

        gspec = (gspec - base) * coef
        wx.CallAfter(dead_object_wrapper(self._sld_spec.SetContent), gspec.tolist())


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
    STREAM_ORDER = (
        acq.stream.SEMStream,
        acq.stream.StaticSEMStream,
        acq.stream.BrightfieldStream,
        acq.stream.StaticStream,
        acq.stream.FluoStream,
        acq.stream.SpectrumSettingsStream,
        acq.stream.ARSettingsStream,
    )

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
            self.btn_add_stream = buttons.PopupImageButton(self, -1,
                                                           bitmap=img.getstream_addBitmap(),
                                                           label="ADD STREAM",
                                                           style=wx.ALIGN_CENTER)

            self.btn_add_stream.SetForegroundColour("#999999")
            self.btn_add_stream.SetBitmaps(img.getstream_add_hBitmap(),
                                           img.getstream_add_aBitmap())
            self._sz.Add(self.btn_add_stream, flag=wx.ALL, border=10)

            self._set_warning()

            self.btn_add_stream.Bind(wx.EVT_BUTTON, self.on_add_stream)

        self.fit_streams()

    def fit_streams(self):
        h = self._sz.GetMinSize().GetHeight()

        self.SetSize((-1, h))

        # The panel size is cached in the _PanelSize attribute.
        # Make sure it's updated by calling ResizePanel

        p = self.Parent

        while not isinstance(p, FoldPanelItem):
            p = p.Parent

        p.Refresh()

    # TODO: maybe should be provided after init by the controller (like key of
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
            self.fit_streams()

    def hide_add_button(self):
        if self.btn_add_stream:
            self.btn_add_stream.Hide()
            self.fit_streams()

    def is_empty(self):
        return len(self.stream_panels) == 0

    def get_size(self):
        """ Return the number of streams contained within the StreamBar """
        return len(self.stream_panels)

    def add_stream_panel(self, spanel, show=True):
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

        logging.debug("Inserting %s at position %s", spanel.stream.__class__.__name__, ins_pos)

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
        self.fit_streams()

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

