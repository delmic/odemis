#-*- coding: utf-8 -*-

"""
:author: Rinze de Laat
:copyright: © 2013 Rinze de Laat, Delmic

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

import cairo
import collections
import logging
import math

from odemis.gui import FG_COLOUR_DIS
from odemis.gui import img
from odemis.gui.comp.scalewindow import ScaleWindow
from odemis.gui.comp.slider import Slider
from odemis.gui.comp.radio import GraphicalRadioButtonControl
from odemis.gui.comp.buttons import ImageToggleButton
from odemis.gui.comp.combo import ComboBox
from odemis.gui.util import wxlimit_invocation, call_in_wx_main
from odemis.gui.util.conversion import wxcol_to_frgb
from odemis.gui.util.img import calculate_ticks
from odemis.model import MD_AT_SPECTRUM, MD_AT_AR, MD_AT_FLUO, \
                         MD_AT_CL, MD_AT_OVV_FULL, MD_AT_OVV_TILES, \
                         MD_AT_EM, MD_AT_HISTORY, MD_AT_SLIT
import wx

import odemis.util.units as units


class InfoLegend(wx.Panel):
    """ This class describes a legend containing the default controls that
    provide information about live data streams.

    TODO: give this class a more descriptive name

    """

    def __init__(self, parent, wid=-1, pos=(0, 0), size=wx.DefaultSize,
                 style=wx.NO_BORDER):

        style = style | wx.NO_BORDER
        super(InfoLegend, self).__init__(parent, wid, pos, size, style)

        # Cannot be a constant because loading bitmaps only works after wx.App
        # has been created.
        self._type_to_icon = (
            (MD_AT_AR, img.getBitmap("icon/ico_blending_ang.png")),
            (MD_AT_SPECTRUM, img.getBitmap("icon/ico_blending_spec.png")),
            (MD_AT_EM, img.getBitmap("icon/ico_blending_sem.png")),
            (MD_AT_OVV_TILES, img.getBitmap("icon/ico_blending_map.png")),
            (MD_AT_OVV_FULL, img.getBitmap("icon/ico_blending_navcam.png")),
            (MD_AT_HISTORY, img.getBitmap("icon/ico_blending_history.png")),
            (MD_AT_CL, img.getBitmap("icon/ico_blending_opt.png")),
            (MD_AT_FLUO, img.getBitmap("icon/ico_blending_opt.png")),
            (MD_AT_SLIT, img.getBitmap("icon/ico_blending_slit.png")),
        )

        self.SetBackgroundColour(parent.GetBackgroundColour())
        self.SetForegroundColour(parent.GetForegroundColour())

        ### Create child windows

        # Merge slider
        # TODO: should be possible to use VAConnector
        self.merge_slider = Slider(self,
                                   wx.ID_ANY,
                                   50,  # val
                                   0, 100,
                                   size=(100, 12),
                                   style=(wx.SL_HORIZONTAL |
                                          wx.SL_AUTOTICKS |
                                          wx.SL_TICKS |
                                          wx.NO_BORDER)
        )

        self.merge_slider.SetBackgroundColour(parent.GetBackgroundColour())
        self.merge_slider.SetForegroundColour(FG_COLOUR_DIS) # "#4d4d4d"
        self.merge_slider.SetToolTip("Merge ratio")

        self.bmp_slider_left = wx.StaticBitmap(self,
                                               wx.ID_ANY,
                                               img.getBitmap("icon/ico_blending_opt.png"))
        self.bmp_slider_right = wx.StaticBitmap(self,
                                                wx.ID_ANY,
                                                img.getBitmap("icon/ico_blending_sem.png"))

        # Horizontal Field Width text
        self.hfw_text = wx.TextCtrl(self, style=wx.NO_BORDER | wx.CB_READONLY)
        self.hfw_text.SetBackgroundColour(parent.GetBackgroundColour())
        self.hfw_text.SetForegroundColour(parent.GetForegroundColour())
        self.hfw_text.SetToolTip("Horizontal Field Width")

        # Magnification text
        self.magnification_text = wx.TextCtrl(self, style=wx.NO_BORDER | wx.CB_READONLY)
        self.magnification_text.SetBackgroundColour(parent.GetBackgroundColour())
        self.magnification_text.SetForegroundColour(parent.GetForegroundColour())
        self.magnification_text.SetToolTip("Magnification")

        # Z position text
        self.zPos_text = wx.TextCtrl(self, style=wx.NO_BORDER | wx.CB_READONLY)
        self.zPos_text.SetBackgroundColour(parent.GetBackgroundColour())
        self.zPos_text.SetForegroundColour(parent.GetForegroundColour())
        self.zPos_text.SetToolTip("Z Position")
        self.zPos_text.Hide()

        # Scale window
        self.scale_win = ScaleWindow(self)

        ## Child window layout

        # Sizer composition:
        #
        # +-------------------------------------------------------+
        # |  <Mag>  | <HFW> |    <Scale>    |  [Icon|Slider|Icon] |
        # +-------------------------------------------------------+

        slider_sizer = wx.BoxSizer(wx.HORIZONTAL)
        # TODO: need to have the icons updated according to the streams type
        slider_sizer.Add(
            self.bmp_slider_left, 0,
            border=3,
            flag=wx.ALIGN_CENTER | wx.RIGHT)
        slider_sizer.Add(
            self.merge_slider, 1,
            flag=wx.ALIGN_CENTER | wx.EXPAND)
        slider_sizer.Add(
            self.bmp_slider_right, 0,
            border=3,
            flag=wx.ALIGN_CENTER | wx.LEFT)

        control_sizer = wx.BoxSizer(wx.HORIZONTAL)
        control_sizer.Add(self.magnification_text, 2, border=10,
                          flag=wx.ALIGN_CENTER | wx.RIGHT | wx.EXPAND)
        control_sizer.Add(self.hfw_text, 2, border=10,
                          flag=wx.ALIGN_CENTER | wx.RIGHT | wx.EXPAND)
        control_sizer.Add(self.scale_win, 3, border=10,
                          flag=wx.ALIGN_CENTER | wx.RIGHT | wx.EXPAND)
        control_sizer.Add(self.zPos_text, 2, border=10,
                          flag=wx.ALIGN_CENTER | wx.RIGHT | wx.EXPAND)
        control_sizer.Add(slider_sizer, 0, border=10, flag=wx.ALIGN_CENTER | wx.RIGHT)
        # border_sizer is needed to add a border around the legend
        border_sizer = wx.BoxSizer(wx.VERTICAL)
        border_sizer.Add(control_sizer, border=6, flag=wx.ALL | wx.EXPAND)

        self.SetSizerAndFit(border_sizer)

        ## Event binding

        # Dragging the slider should set the focus to the right view
        self.merge_slider.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)
        self.merge_slider.Bind(wx.EVT_LEFT_UP, self.OnLeftUp)

        # Make sure that mouse clicks on the icons set the correct focus
        self.bmp_slider_left.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)
        self.bmp_slider_right.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)

        # Set slider to min/max
        self.bmp_slider_left.Bind(wx.EVT_LEFT_UP, parent.OnSliderIconClick)
        self.bmp_slider_right.Bind(wx.EVT_LEFT_UP, parent.OnSliderIconClick)

        self.hfw_text.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)
        self.magnification_text.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)
        self.zPos_text.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)

        # Explicitly set the
        # self.SetMinSize((-1, 40))

    def clear(self):
        pass

    # Make mouse events propagate to the parent
    def OnLeftDown(self, evt):
        evt.ResumePropagation(1)
        evt.Skip()

    def OnLeftUp(self, evt):
        evt.ResumePropagation(1)
        evt.Skip()

    def set_hfw_label(self, label):
        approx_width = len(label) * 7
        self.hfw_text.SetMinSize((approx_width, -1))
        self.hfw_text.SetValue(label)
        self.Layout()

    def set_mag_label(self, label):
        # TODO: compute the real size needed (using GetTextExtent())
        approx_width = len(label) * 7
        self.magnification_text.SetMinSize((approx_width, -1))
        self.magnification_text.SetValue(label)
        self.Layout()

    def set_zPos_label(self, label):
        """
        label (unicode or None): if None, zPos is hidden, otherwise show the value
        """
        if label is None:
            self.zPos_text.Hide()
        else:
            self.zPos_text.Show()
            # TODO: compute the real size needed (using GetTextExtent())
            approx_width = len(label) * 7
            self.zPos_text.SetMinSize((approx_width, -1))
            self.zPos_text.SetValue(label)
        self.Layout()

    def set_stream_type(self, side, acq_type):
        """
        Set the stream type, to put the right icon on the merge slider

        :param side: (wx.LEFT or wx.RIGHT): whether this set the left or right
            stream
        :param acq_type: (String): acquisition type associated with stream
        """

        for t, type_icon in self._type_to_icon:
            if acq_type == t:
                icon = type_icon
                break
        else:
            # Don't fail too bad
            icon = img.getBitmap("icon/ico_blending_opt.png")
            if self.merge_slider.IsShown():
                logging.warning("Failed to find icon for stream of type %s",
                                acq_type)
        if side == wx.LEFT:
            self.bmp_slider_left.SetBitmap(icon)
        else:
            self.bmp_slider_right.SetBitmap(icon)


class AxisLegend(wx.Panel):
    """
    The axis legend displayed in plots

    Properties:
    .unit: Unit displayed
    .range: the range of the scale
    .lo_ellipsis, hi_ellipsis: These properties, when true, show an ellipsis
        on the first and/or last tick marks
        e.g.: ...-10,  -5 , 0, 5, 10...
    .lock_va: a boolean VA which is connected to the lock toggle button.
        Typically, this button shows when the scale is locked from manipulation.
        If this property is set to None, the lock button will be hidden.
    """

    def __init__(self, parent, wid=wx.ID_ANY, pos=(0, 0), size=wx.DefaultSize,
                 style=wx.NO_BORDER, orientation=wx.HORIZONTAL):

        style |= wx.NO_BORDER
        wx.Panel.__init__(self, parent, wid, pos, size, style)

        self.SetBackgroundColour(parent.GetBackgroundColour())
        self.SetForegroundColour(parent.GetForegroundColour())

        self.tick_colour = wxcol_to_frgb(self.ForegroundColour)

        self.Bind(wx.EVT_PAINT, self.on_paint)
        self.Bind(wx.EVT_SIZE, self.on_size)

        self._orientation = orientation
        self._max_tick_width = 32  # Largest pixel width of any label in use
        self._tick_spacing = 50
        self._unit = None
        self._lo_ellipsis = False
        self._hi_ellipsis = False
        self._lock_va = None

        # Explicitly set the min size
        if self._orientation == wx.HORIZONTAL:
            self.SetMinSize((-1, 32))
        else:
            self.SetMinSize((42, -1))

        # The following properties are volatile, meaning that they can change often
        self._value_range = None  # 2-tuple with the minimum and maximum value
        self._tick_list = None  # List of 2-tuples, containing the pixel position and value
        self._pixel_space = None  # Number of available pixels

        # Axis lock button
        # This button allows a user to lock the scales from manipulation
        bmp = img.getBitmap("menu/btn_lock.png")
        bmpa = img.getBitmap("menu/btn_lock_a.png")
        bmph = img.getBitmap("menu/btn_lock.png")

        self.lockBtn = ImageToggleButton(self, pos=(0, 0), bitmap=bmp, size=(24, 24))
        self.lockBtn.bmpSelected = bmpa
        self.lockBtn.bmpHover = bmph
        self.lockBtn.Hide()

        self.on_size()  # Force a refresh

        if self._orientation == wx.HORIZONTAL:
            self.SetCursor(wx.Cursor(wx.CURSOR_SIZEWE))
        else:
            self.SetCursor(wx.Cursor(wx.CURSOR_SIZENS))

    @property
    def unit(self):
        return self._unit

    @property
    def lo_ellipsis(self):
        return self._lo_ellipsis

    @property
    def hi_ellipsis(self):
        return self._hi_ellipsis

    @unit.setter
    def unit(self, val):
        if self._unit != val:
            self._unit = val
            self.Refresh()

    @lo_ellipsis.setter
    def lo_ellipsis(self, val):
        if self._lo_ellipsis != val:
            self._lo_ellipsis = bool(val)
            self.Refresh()

    @hi_ellipsis.setter
    def hi_ellipsis(self, val):
        if self._hi_ellipsis != val:
            self._hi_ellipsis = bool(val)
            self.Refresh()

    @property
    def lock_va(self):
        return self._lock_va

    @lock_va.setter
    def lock_va(self, va):
        """
        Note: The caller must run in the main GUI Thread
        """
        if self._lock_va != va:
            if self._lock_va is not None:
                self._lock_va.unsubscribe(self._on_lock_va_change)

            # Show the lock button
            self.lockBtn.Show()
            self._lock_va = va

            self.lockBtn.Bind(wx.EVT_BUTTON, self._on_lock_click)
            self._lock_va.subscribe(self._on_lock_va_change)

            self.Refresh()

    def _on_lock_click(self, evt):
        self._lock_va.value = evt.isDown

    @call_in_wx_main
    def _on_lock_va_change(self, new_value):
        self.lockBtn.SetToggle(new_value)

    @property
    def range(self):
        return self._value_range

    @range.setter
    def range(self, val):
        if self._value_range != val:
            self._value_range = val
            self.Refresh()

    def clear(self):
        self._value_range = None
        self._tick_list = None
        self.Refresh()

    def on_size(self, _=None):
        if self._value_range:
            self.Refresh()

        if self._orientation == wx.HORIZONTAL:
            self.lockBtn.SetPosition((self.ClientSize.x - 24, 0))
        else:
            self.lockBtn.SetPosition((self.ClientSize.x - self.ClientSize.x / 2 - 12, 0))

    def on_mouse_enter(self, _=None):
        if self._orientation == wx.HORIZONTAL:
            self.SetCursor(wx.Cursor(wx.CURSOR_SIZEWE))
        else:
            self.SetCursor(wx.Cursor(wx.CURSOR_SIZENS))

    def on_mouse_leave(self, _=None):
        self.SetCursor(wx.Cursor(wx.CURSOR_ARROW))

    @wxlimit_invocation(0.2)
    def Refresh(self):
        """
        Refresh, which can be called safely from other threads
        """
        if self:
            wx.Panel.Refresh(self)

    def on_paint(self, _):
        if self._value_range is None:
            return

        # shared function with the export method
        csize = self.ClientSize
        self._tick_list = calculate_ticks(self._value_range, csize, self._orientation, self._tick_spacing)

        if self.lock_va is not None:
            if self._orientation == wx.HORIZONTAL:
                csize.x -= 24

            elif self._orientation == wx.VERTICAL:
                self._tick_list = [(pos, val) for (pos, val) in self._tick_list if pos > 24]

        # If min and max are very close, we need more significant numbers to
        # ensure the values displayed are different (ex 17999 -> 18003)
        rng = self._value_range
        if rng[0] == rng[1]:
            sig = None
        else:
            ratio_rng = max(abs(v) for v in rng) / (max(rng) - min(rng))
            sig = max(3, 1 + math.ceil(math.log10(ratio_rng * len(self._tick_list))))

        # Set Font
        font = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT)
        ctx = wx.lib.wxcairo.ContextFromDC(wx.PaintDC(self))
        ctx.select_font_face(font.GetFaceName(), cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(font.GetPointSize())

        ctx.set_source_rgb(*self.tick_colour)
        ctx.set_line_width(2)
        ctx.set_line_join(cairo.LINE_JOIN_MITER)

        max_width = 0
        prev_lpos = 0 if self._orientation == wx.HORIZONTAL else csize.y

        for i, (pos, val) in enumerate(self._tick_list):
            label = units.readable_str(val, self.unit, sig)

            if i == 0 and self._lo_ellipsis:
                label = u"…"

            if i == len(self._tick_list) - 1 and self._hi_ellipsis:
                label = u"…"

            _, _, lbl_width, lbl_height, _, _ = ctx.text_extents(label)

            if self._orientation == wx.HORIZONTAL:
                lpos = pos - (lbl_width // 2)
                lpos = max(min(lpos, csize.x - lbl_width - 2), 2)
                # print (i, prev_right, lpos)
                if prev_lpos < lpos:
                    ctx.move_to(lpos, lbl_height + 8)
                    ctx.show_text(label)
                    ctx.move_to(pos, 5)
                    ctx.line_to(pos, 0)
                prev_lpos = lpos + lbl_width
            else:
                max_width = max(max_width, lbl_width)
                lpos = pos + (lbl_height // 2)
                lpos = max(min(lpos, csize.y), 2)

                if prev_lpos >= lpos + 20 or i == 0:
                    ctx.move_to(csize.x - lbl_width - 9, lpos)
                    ctx.show_text(label)
                    ctx.move_to(csize.x - 5, pos)
                    ctx.line_to(csize.x, pos)
                prev_lpos = lpos + lbl_height

            ctx.stroke()

        if self._orientation == wx.VERTICAL and max_width != self._max_tick_width:
            self._max_tick_width = max_width
            self.SetMinSize((self._max_tick_width + 14, -1))
            self.Parent.GetSizer().Layout()


class RadioLegend(wx.Panel):
    """
    This class describes a legend containing radio buttons,
    where each button represents a possible selection of a VA.
    Optionally, for a larger selection of possible VA values,
    the VA values can be sorted according to functionality in
    a dict and the legend will represent grouped values in
    a combination of a drop down menu and radio buttons.
    """

    def __init__(self, parent, wid=-1, pos=(0, 0), size=wx.DefaultSize, style=wx.NO_BORDER):
        wx.Panel.__init__(self, parent, wid, pos, size, style)

        # Store the colours now, as we'll need them whenever the radio button choices change, and by that time the
        # parent (ARViewport) might be focused, which causes it to have a different background colour.
        self.bg_color = parent.GetBackgroundColour()
        self.fg_color = parent.GetForegroundColour()

        self.SetBackgroundColour(self.bg_color)
        self.SetForegroundColour(self.fg_color)

        # descriptive VA text
        self.text = wx.StaticText(self, label="", style=wx.NO_BORDER)
        self.text.SetBackgroundColour(self.bg_color)
        self.text.SetForegroundColour(self.fg_color)
        self.text.SetToolTip("Position currently displayed")

        # current position displayed in the legend (or None)
        # can be radio buttons or text only depending on the number of positions
        self.pos_display = None
        self.pos_display_combo = None
        self.control_sizer = wx.BoxSizer(wx.HORIZONTAL)

        # border_sizer is needed to add a border around the legend
        self.border_sizer = wx.BoxSizer(wx.VERTICAL)
        self.border_sizer.Add(self.control_sizer, border=8, flag=wx.ALL | wx.EXPAND)

        self.SetSizer(self.border_sizer)

        # dict used if legend consists of combo box (keys) plus radio buttons (values)
        self._choices_dict = None  # (dict str -> tuple of str)
        self._legend_on = False  # keeps track of, if a new value was requested via the legend or the stream panel
        self._callback = None  # (callable): function called when the value of the legend changes

    def clear(self):
        """
        Remove the display widget, to not show anything.
        It will be added again next time the entries are set.
        """
        self.control_sizer.Clear()
        # destroy the old widgets (otherwise they are still displayed in the GUI)
        if self.pos_display:
            self.pos_display.Destroy()
        if self.pos_display_combo:
            self.pos_display_combo.Destroy()
        self.Refresh()

    @wxlimit_invocation(0.2)
    def Refresh(self):
        """
        Refresh, which can be called safely from other threads.
        """
        if self:
            wx.Panel.Refresh(self)

    def OnGroupSelectionCombo(self, evt=None):
        """
        The combo box selection is requested and the corresponding radio buttons are
        created. The callback function, which changes the position VA on the
        stream/projection is called. The _callback function can be overwritten by the viewport.
        In the case of an event, this method was called by selecting a new position in the
        combo box (dropdown menu).
        :param evt: (wx.EVT_COMBOBOX or None) If event, the method was triggered via changing
                    the current value of the combo box.
        """
        if self.pos_display:
            self.pos_display.Destroy()

        key = self.pos_display_combo.GetValue()
        choices_radio = self._choices_dict[key]
        self.createRadioButtons(choices_radio)
        if self._legend_on:  # only call callback fct when change requested via legend (and not e.g. settings panel)
            self._callback(choices_radio[0])  # set first value as default when combo box value was changed via legend

        self.control_sizer.Add(self.pos_display, 2, border=10, flag=wx.ALIGN_CENTER | wx.RIGHT)

        # refresh layout
        self.border_sizer.Layout()
        self.Parent.Layout()  # need to be called as method is called after self.Layout im main thread

    def OnPositionButton(self, event):
        """
        In the case of an event (select a button), the radio button selection is
        requested and the callback function, which changes the position is called.
        The _callback function can be overwritten by the viewport.
        :param event: (wx.EVT_BUTTON) Event triggered by selecting a radio button in the legend.
        """
        pos = self.pos_display.GetValue()
        self._callback(pos)

    def set_callback(self, func):
        """
        Sets the callback function that is executed when a new radio button selection
        is requested (in OnPositionButton). The _callback function can be overwritten by the viewport.
        :param func: (callable) Callback function that is called when a new pos is requested.
                     Function param is "pos" (string): The position to be set in the legend.
        """
        self._callback = func

    def set_value(self, pos):
        """
        Sets the new position requested in the legend. Method should be called from the main
        GUI thread only.
        :param pos: (str) New radio button selection to be set active.
        """
        # Only called when the position is changed externally to update the widgets state.

        # Changing the value via the settings panel widgets, needs a bit special handling.
        # Note: No need to call the callback function as the stream VA is connected to settings panel widget.
        if self.pos_display_combo and pos not in self.pos_display.choices:
            self._legend_on = False
            # Get the matching combo box pos for the requested pos via the settings panel.
            # This is also the pos that needs to be active in the radio buttons.
            pos_combo, _ = self._matchRadioPosWithComboPos(self._choices_dict, pos)
            self.pos_display_combo.SetValue(pos_combo)  # set the value on the combo box
            self.OnGroupSelectionCombo()  # create the corresponding radio buttons
            self.pos_display.SetValue(pos)  # set the value on the radio button explicitly
            self._legend_on = True
        else:
            self.pos_display.SetValue(pos)

    def createRadioButtons(self, choices, value=None):
        """
        Creates the radio buttons in the legend.
        :param choices: (list or tuple) Choices to be set for the radio buttons.
        :param value: (str or None) Radio button that should be active.
        """
        self.pos_display = GraphicalRadioButtonControl(self, choices=choices, labels=choices)
        self.pos_display.SetToolTip("Select a position for display.")
        self.pos_display.Bind(wx.EVT_BUTTON, self.OnPositionButton)
        if value:
            self.pos_display.SetValue(value)  # set the value

    def createComboBox(self, choices, value):
        """
        Creates the combo box in the legend.
        :param choices: (list or tuple) Choices to be set for the combo box.
        :param value: (str) Combo box value that should be selected.
        :returns: (tuple) Choices for creating the corresponding radio buttons.
        """
        choices_combo = choices.keys()
        self.pos_display_combo = ComboBox(self, wx.ID_ANY, choices=choices_combo, labels=choices_combo,
                                          style=wx.NO_BORDER | wx.TE_PROCESS_ENTER | wx.CB_READONLY,
                                          pos=(0, 0), size=(290, 16))
        self.pos_display_combo.SetToolTip("Select a position for display.")
        self.pos_display_combo.Bind(wx.EVT_COMBOBOX, self.OnGroupSelectionCombo)

        pos_combo, choices_radio = self._matchRadioPosWithComboPos(choices, value)
        self.pos_display_combo.SetValue(pos_combo)  # set value

        return choices_radio

    def _matchRadioPosWithComboPos(self, choices, value):
        """
        Match the value for the radio button with the value to be set on the combo box.
        :param choices: (dict) Dictionary, whose keys are the choices for the combo box, and
                        whose values are the choices for the radio buttons.
        :param value: (str) Combo box value that should be selected.
        :returns: pos_combo (str) Value to be selected on the combo box.
                  choices_radio (tuple): Choices used to create the corresponding radio buttons.
        """
        for pos_combo, choices_radio in choices.items():
            if value in choices_radio:
                return pos_combo, choices_radio

    def createStaticText(self, value):
        """
        Creates the static text in the legend.
        Used, if only one choice (aka one image) needs to be displayed.
        :param value: (str) Value that should be displayed in the text.
        """
        self.pos_display = wx.TextCtrl(self, value=value, style=wx.NO_BORDER | wx.CB_READONLY)
        self.pos_display.SetBackgroundColour(self.bg_color)
        self.pos_display.SetForegroundColour(self.fg_color)

    @call_in_wx_main
    def set_pos_entries(self, choices, default_value, name):
        """
        Create radio buttons or a combination of radio buttons and a combo box, to switch between
        positions within legend. If only one pos present in data, display pos as static text.
        :param choices: (set or dict -> tuple or dict) Positions found in data. If too many choices,
                        it is possible to sort them in a dict and pass it to the radio legend.
                        Radio legend will then create a legend consisting of a drop down menu/combo box
                        (keys of dict) combined with radio buttons (values of dict).
        :param default_value: (str) Default value that should be active.
        :param name: (str) Text to be displayed describing the radio legend.
        """
        self.clear()

        # add descriptive text to legend
        self.control_sizer.Add(self.text, 0, border=10, flag=wx.ALIGN_CENTER | wx.RIGHT)
        self.text.SetLabel(name)  # set the descriptive text for the legend

        if len(choices) > 1:
            if isinstance(choices, collections.Mapping):  # create combo box + radio buttons
                self._choices_dict = choices

                choices_radio = self.createComboBox(choices, default_value)
                self.createRadioButtons(choices_radio, default_value)  # create radio buttons

                self.control_sizer.Add(self.pos_display_combo, 1, border=10, flag=wx.ALIGN_CENTER | wx.RIGHT)
                self.control_sizer.Add(self.pos_display, 2, border=10, flag=wx.ALIGN_CENTER | wx.RIGHT)

                self._legend_on = True  # True while not changes requested via stream panel

            elif isinstance(choices, collections.Iterable):  # only create radio buttons
                # if we have multiple choices -> select between choices with radio button
                self.createRadioButtons(choices, default_value)  # create radio buttons
                self.control_sizer.Add(self.pos_display, 1, border=10, flag=wx.ALIGN_CENTER | wx.RIGHT)

            else:
                raise TypeError("Choices for radio legend must be of type 'frozenset' or 'dict'.")

        else:  # if only one choice -> only text displayed
            self.createStaticText(default_value)
            self.control_sizer.Add(self.pos_display, 1, border=10, flag=wx.ALIGN_CENTER | wx.RIGHT)

        # refresh layout
        self.border_sizer.Layout()
        self.Parent.Layout()  # need to be called as method is called after self.Layout im main thread
