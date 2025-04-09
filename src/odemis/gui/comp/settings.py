# -*- coding: utf-8 -*-
"""
.. codeauthor:: Rinze de Laat <laat@delmic.com>

Copyright © 2014 Rinze de Laat, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License version 2 as published by the Free
    Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
    PARTICULAR PURPOSE. See the GNU General Public License for more details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""
import wx
from decorator import decorator

import odemis.gui as gui
from odemis.gui import img
from odemis.gui.comp.buttons import ImageButton, ImageTextButton
from odemis.gui.comp.combo import ComboBox
from odemis.gui.comp.file import FileBrowser
from odemis.gui.comp.radio import GraphicalRadioButtonControl
from odemis.gui.comp.slider import Slider, UnitFloatSlider, UnitIntegerSlider
from odemis.gui.comp.spinner import UnitFloatSpinner, UnitIntegerSpinner
from odemis.gui.comp.text import UnitFloatCtrl, UnitIntegerCtrl
from odemis.gui.comp.toggle import GraphicalToggleButtonControl


@decorator
def control_bookkeeper(f, self, *args, **kwargs):
    """ Clear the default message, if needed, and advance the row count """
    self.clear_default_message()
    result = f(self, *args, **kwargs)
    # Redo FoldPanelBar layout
    if hasattr(self, "Parent"):
        parent = self.Parent
        if hasattr(parent, "Parent"):
            parent.Parent.Layout()
    self.Layout()
    self.num_rows += 1
    return result


class SettingsPanel(wx.Panel):

    def __init__(self, *args, **kwargs):
        default_msg = kwargs.pop('default_msg', "")
        super(SettingsPanel, self).__init__(*args, **kwargs)

        self.SetBackgroundColour(gui.BG_COLOUR_MAIN)
        self.SetForegroundColour(gui.FG_COLOUR_MAIN)

        self.num_rows = 0  # The number of rows in the GridBagSizer

        # The main sizer is used to create a margin on the inside of the panel
        self._main_sizer = wx.BoxSizer(wx.VERTICAL)
        # The GridBagSizer is use to create a 2-column lay-out for the settings controls
        self.gb_sizer = wx.GridBagSizer()
        self.gb_sizer.SetEmptyCellSize((0, 0))

        # The default message text is added here, because at least control needs to be present
        # before the growable column can be added.
        self.message_ctrl = None
        # A default message need to be inserted here, because otherwise AddGrowableCol will cause
        # an exception, since there wouldn't be any columns yet.
        self.set_default_message(default_msg)

        # Make the 2nd column expand
        self.gb_sizer.AddGrowableCol(1)

        self._main_sizer.Add(self.gb_sizer, proportion=1, flag=wx.ALL | wx.EXPAND, border=5)
        self.SetSizer(self._main_sizer)

    def Layout(self):
        """Layout the ControlsPanel."""

        if not self._main_sizer:
            return False  # we need to complete the creation first!

        oursz = self.GetSize()

        # move & resize the button and the static line
        self._main_sizer.SetDimension(
            0, 0, oursz.GetWidth(), self._main_sizer.GetMinSize().GetHeight()
        )
        self._main_sizer.Layout()

        return True

    def OnSize(self, event):
        """Handles the wx.EVT_SIZE event for ControlsPanel"""
        self.Layout()
        event.Skip()

    def set_default_message(self, msg):
        """ Set the default message in the settings panel """

        if not self.message_ctrl:
            self.message_ctrl = wx.StaticText(self, -1, msg)
            self.gb_sizer.Add(self.message_ctrl, (0, 0), (1, 2))
            self.num_rows = 1
        else:
            self.message_ctrl.SetLabel(msg)
        self.message_ctrl.Show()

    def clear_default_message(self):
        """ Remove the default message if it exists """
        if self.message_ctrl:
            self.message_ctrl.Hide()

    def clear_all(self):
        """ Remove all children """
        for c in self.GetChildren():
            c.Destroy()
        self.num_rows = 0
        self.Parent.Parent.Layout()

    # Control methods

    def _add_side_label(self, label_text, icon_path=None):
        """ Add a label with an optional icon to the left column at the current row.

        This method should only be called from another control-adding method!

        :param label_text: (str) The text for the label.
        :param icon_path: (Optional[str]) Path to the icon image.
        """

        self.clear_default_message()

        # Create a horizontal sizer to hold the icon and text
        h_sizer = wx.BoxSizer(wx.HORIZONTAL)

        # Add icon if provided
        if icon_path:
            icon = img.getBitmap(icon_path)
            icon_ctrl = wx.StaticBitmap(self, -1, icon)
            h_sizer.Add(icon_ctrl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=5)

        # Create label
        lbl_ctrl = wx.StaticText(self, -1, str(label_text))
        h_sizer.Add(lbl_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)

        # Add combined sizer to grid sizer
        self.gb_sizer.Add(h_sizer, (self.num_rows, 0),
                          flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)

        return lbl_ctrl

    @control_bookkeeper
    def add_divider(self):
        """ Add a horizontal divider to the panel """
        line_ctrl = wx.StaticLine(self, size=(-1, 1))
        line_ctrl.SetBackgroundColour(gui.BG_COLOUR_SEPARATOR)
        self.gb_sizer.Add(line_ctrl, (self.num_rows, 0), span=(1, 2),
                          flag=wx.ALL | wx.EXPAND, border=5)

    @control_bookkeeper
    def add_readonly_field(self, label_text, value=None, selectable=True):
        """ Adds a value to the control panel that cannot directly be changed by the user

        :param label_text: (str) Label text to display
        :param value: (None or object) Value to display next to the label.
           If None, only the label will be displayed. The object should be
           "stringable", so the safest is to ensure it's a string.
        :param selectable: (boolean) whether the value can be selected for copying by the user

        :return: (Ctrl, Ctrl or None) Label and value control

        """

        lbl_ctrl = self._add_side_label(label_text)

        # Convert value object to str, only if necessary.
        # => If value is already bytes or str, just pass it as-is to wx.TextCtrl.
        if not isinstance(value, (str, bytes)):
            value = str(value)

        if value is not None:
            if selectable:
                value_ctrl = wx.TextCtrl(self, value=value,
                                         style=wx.BORDER_NONE | wx.TE_READONLY)
                value_ctrl.MinSize = (-1, value_ctrl.BestSize[1])
                value_ctrl.SetForegroundColour(gui.FG_COLOUR_DIS)
                value_ctrl.SetBackgroundColour(gui.BG_COLOUR_MAIN)
                self.gb_sizer.Add(value_ctrl, (self.num_rows, 1),
                                  flag=wx.EXPAND | wx.ALIGN_CENTER_VERTICAL, border=5)
            else:
                value_ctrl = wx.StaticText(self, label=value)
                value_ctrl.SetForegroundColour(gui.FG_COLOUR_DIS)
                self.gb_sizer.Add(value_ctrl, (self.num_rows, 1),
                                  flag=wx.BOTTOM | wx.TOP, border=5)
        else:
            value_ctrl = None

        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_text_field(self, label_text, value=None, readonly=False, multiline=False):
        """ Add a label and text control to the settings panel

        :param label_text: (str) Label text to display
        :param value: (None or str) Value to display
        :param readonly: (boolean) Whether the value can be changed by the user
        :param multiline: (boolean) Whether text control style should be multiline

        :return: Label and text control

        """

        lbl_ctrl = self._add_side_label(label_text)
        value_ctrl = wx.TextCtrl(self, value=str(value or ""),
                                 style=wx.TE_PROCESS_ENTER
                                 | wx.BORDER_NONE
                                 | (wx.TE_READONLY if readonly else 0)
                                 | (wx.TE_MULTILINE if multiline else 0))
        value_ctrl.MinSize = (-1, value_ctrl.BestSize[1])

        if readonly:
            value_ctrl.SetForegroundColour(gui.FG_COLOUR_DIS)
        else:
            value_ctrl.SetForegroundColour(gui.FG_COLOUR_EDIT)
        value_ctrl.SetBackgroundColour(gui.BG_COLOUR_MAIN)
        self.gb_sizer.Add(value_ctrl, (self.num_rows, 1),
                          flag=wx.EXPAND | wx.ALIGN_CENTER_VERTICAL, border=5)

        return lbl_ctrl, value_ctrl

    def _add_slider(self, klass, label_text, value, conf, icon_path=None):
        """ Add a slider of type 'klass' to the settings panel """

        lbl_ctrl = self._add_side_label(label_text, icon_path=icon_path)
        value_ctrl = klass(self, value=value, **conf)
        self.gb_sizer.Add(value_ctrl, (self.num_rows, 1),
                          flag=wx.ALL | wx.EXPAND | wx.ALIGN_CENTER_VERTICAL, border=5)

        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_slider(self, label_text, value=None, conf=None):
        """ Add an integer value slider to the settings panel

        :param label_text: (str) Label text to display
        :param value: (None or int) Value to display
        :param conf: (None or dict) Dictionary containing parameters for the control

        """
        return self._add_slider(Slider, label_text, value, conf)

    @control_bookkeeper
    def add_integer_slider(self, label_text, value=None, conf=None):
        """ Add an integer value slider to the settings panel

        :param label_text: (str) Label text to display
        :param value: (None or int) Value to display
        :param conf: (None or dict) Dictionary containing parameters for the control

        """
        return self._add_slider(UnitIntegerSlider, label_text, value, conf)

    @control_bookkeeper
    def add_float_slider(self, label_text, value=None, conf=None, icon_path=None):
        """ Add a float value slider to the settings panel

        :param label_text: (str) Label text to display
        :param value: (None or float) Value to display
        :param conf: (None or dict) Dictionary containing parameters for the control

        """
        return self._add_slider(UnitFloatSlider, label_text, value, conf, icon_path)

    @control_bookkeeper
    def add_int_field(self, label_text, value=None, pos_col=1, span=wx.DefaultSpan, conf=None):
        """ Add an integer value field to the settings panel

        :param label_text: (str) Label text to display
        :param value: (None or int) Value to display
        :param conf: (None or dict) Dictionary containing parameters for the control
        :param pos_col: (int) The column index in the grid layout where the int field will be placed.
                        For example:
                        - `pos_col=0` positions the int field in the first column.
                        - `pos_col=1` positions it in the second column.
        :param span: (tuple) the row and column spanning attributes of items in a GridBagSizer.

        """

        return self._add_num_field(UnitIntegerCtrl, label_text, value, pos_col, span, conf)

    @control_bookkeeper
    def add_float_field(self, label_text, value=None, pos_col=1, span=wx.DefaultSpan, conf=None):
        """ Add a float value field to the settings panel

        :param label_text: (str) Label text to display
        :param value: (None or float) Value to display
        :param conf: (None or dict) Dictionary containing parameters for the control
        :param pos_col: (int) The column index in the grid layout where the float field will be placed.
                        For example:
                        - `pos_col=0` positions the float field in the first column.
                        - `pos_col=1` positions it in the second column.
        :param span: (tuple) the row and column spanning attributes of items in a GridBagSizer.

        """

        return self._add_num_field(UnitFloatCtrl, label_text, value, pos_col, span, conf)

    def _add_num_field(self, klass, label_text, value, pos_col, span, conf):

        lbl_ctrl = self._add_side_label(label_text)
        value_ctrl = klass(self, value=value, style=wx.NO_BORDER, **conf)
        self.gb_sizer.Add(value_ctrl, (self.num_rows, pos_col), span=span,
                          flag=wx.ALL | wx.EXPAND | wx.ALIGN_CENTER_VERTICAL, border=5)
        value_ctrl.SetForegroundColour(gui.FG_COLOUR_EDIT)
        value_ctrl.SetBackgroundColour(gui.BG_COLOUR_MAIN)

        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_checkbox_control(self, label_text, value=True, pos_col=1, span=wx.DefaultSpan, conf=None):
        """ Add a checkbox to the settings panel

        :param label_text: (str) Label text to display
        :param value: (bool) Value to display (True == checked)
        :param conf: (None or dict) Dictionary containing parameters for the control
        :param pos_col: (int) The column index in the grid layout where the checkbox will be placed.
                        For example:
                        - `pos_col=0` positions the checkbox in the first column.
                        - `pos_col=1` positions it in the second column.
        :param span: (tuple) the row and column spanning attributes of items in a GridBagSizer.

        """
        if conf is None:
            conf = {}

        lbl_ctrl = self._add_side_label(label_text)
        value_ctrl = wx.CheckBox(self, wx.ID_ANY, style=wx.ALIGN_RIGHT | wx.NO_BORDER, **conf)
        self.gb_sizer.Add(value_ctrl, (self.num_rows, pos_col), span=span,
                          flag=wx.EXPAND | wx.TOP | wx.BOTTOM, border=5)
        value_ctrl.SetValue(value)

        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_radio_control(self, label_text, value=None, conf=None):
        """ Add a series of radio buttons to the settings panel

        :param label_text: (str) Label text to display
        :param value: (None or float) Value to display
        :param conf: (None or dict) Dictionary containing parameters for the control

        """
        if conf is None:
            conf = {}

        lbl_ctrl = self._add_side_label(label_text)
        value_ctrl = GraphicalRadioButtonControl(self, -1, style=wx.NO_BORDER, **conf)
        self.gb_sizer.Add(value_ctrl, (self.num_rows, 1),
                          flag=wx.ALL | wx.EXPAND | wx.ALIGN_CENTER_VERTICAL, border=5)
        if value is not None:
            value_ctrl.SetValue(value)

        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_combobox_control(self, label_text, value=None, conf=None):
        """ Add a combo box to the settings panel

        :param label_text: (str) Label text to display
        :param value: (None or float) Value to display *NOT USED ATM*
        :param conf: (None or dict) Dictionary containing parameters for the control

        """
        if conf is None:
            conf = {}

        lbl_ctrl = self._add_side_label(label_text)
        cbstyle = wx.NO_BORDER | wx.TE_PROCESS_ENTER | conf.pop("style", 0)
        value_ctrl = ComboBox(self, wx.ID_ANY, pos=(0, 0), size=(-1, 16),
                              style=cbstyle, **conf)

        self.gb_sizer.Add(value_ctrl, (self.num_rows, 1),
                          flag=wx.ALL | wx.EXPAND | wx.ALIGN_CENTER_VERTICAL, border=5)

        if value:
            value_ctrl.SetValue(str(value))

        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_combobox_with_buttons_control(self, label_text, value=None, conf=None):
        """ Add a combo box to the settings panel

        :param label_text: (str) Label text to display
        :param value: (None or float) Value to display *NOT USED ATM*
        :param conf: (None or dict) Dictionary containing parameters for the control

        :return: Label and combobox control, the combobox control has add_btn and delete_btn attributes
            of the buttons which can be later used to create logic for adding and deleteting items in
            combobox.
        """
        if conf is None:
            conf = {}

        lbl_ctrl = self._add_side_label(label_text)
        cbstyle = wx.NO_BORDER | wx.TE_PROCESS_ENTER | conf.pop("style", 0)
        value_ctrl = ComboBox(self, wx.ID_ANY, pos=(0, 0), size=(-1, 16),
                              style=cbstyle, **conf)
        add_kwargs = {
            "icon": img.getBitmap("icon/ico_plus.png"),
            "height" : 16,
            "style": wx.ALIGN_CENTER,
        }
        delete_kwargs = {
            "icon": img.getBitmap("icon/ico_trash.png"),
            "height" : 16,
            "style": wx.ALIGN_CENTER,
        }
        add_button_ctrl = ImageButton(self, **add_kwargs)
        delete_button_ctrl = ImageButton(self, **delete_kwargs)
        setattr(value_ctrl, "add_btn", add_button_ctrl)
        setattr(value_ctrl, "delete_btn", delete_button_ctrl)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.Add(add_button_ctrl, 0, wx.ALL)
        button_sizer.Add(delete_button_ctrl, 0, wx.ALL)

        control_sizer = wx.BoxSizer(wx.VERTICAL)
        control_sizer.Add(value_ctrl, 0, wx.ALL | wx.EXPAND, 5)
        control_sizer.Add(button_sizer, 0, wx.ALL | wx.EXPAND, 5)

        self.gb_sizer.Add(control_sizer, (self.num_rows, 1),
                          flag=wx.ALL | wx.EXPAND)

        if value:
            value_ctrl.SetValue(str(value))

        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_file_button(self, label_text, value=None, clearlabel=None, dialog_style=wx.FD_OPEN, wildcard=None, **kwargs):

        # Create label
        lbl_ctrl = self._add_side_label(label_text)

        value_ctrl = FileBrowser(self,
                                 style=wx.BORDER_NONE | wx.TE_READONLY,
                                 dialog_style=dialog_style,
                                 clear_label=clearlabel or "",
                                 clear_btn=clearlabel is not None,
                                 file_path=value,
                                 wildcard=wildcard,
                                 default_dir=None,
                                 **kwargs)
        value_ctrl.SetForegroundColour(gui.FG_COLOUR_EDIT)
        value_ctrl.SetBackgroundColour(gui.BG_COLOUR_MAIN)

        self.gb_sizer.Add(value_ctrl,
                          (self.num_rows, 1),
                          flag=wx.EXPAND | wx.ALIGN_CENTER_VERTICAL,
                          border=5)

        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_toggle_control(self, label_text, values=None, conf=None):
        """Add a series of toggle buttons to the settings panel

        :param label_text: (str) Label text to display
        :param values: (None or float) Values to display as the state is toggled
        :param conf: (None or dict) Dictionary containing parameters for the control

        """
        if conf is None:
            conf = {}

        lbl_ctrl = self._add_side_label(label_text)
        value_ctrl = GraphicalToggleButtonControl(
            self, -1, style=wx.NO_BORDER, **conf
        )
        self.gb_sizer.Add(
            value_ctrl,
            (self.num_rows, 1),
            span=(1, 2),
            flag=wx.ALL | wx.EXPAND | wx.ALIGN_CENTER_VERTICAL,
            border=5,
        )

        if values is not None:
            value_ctrl.SetValue(values)

        return lbl_ctrl, value_ctrl

    def _add_spinner(self, klass, label_text, value, conf, *args):
        """Add a spinner of type 'klass' to the user settings panel"""
        if conf is None:
            conf = {}

        lbl_ctrl = self._add_side_label(label_text)
        value_ctrl = klass(
            self,
            id=wx.ID_ANY,
            pos=(0, 0),
            size=(5, 16),
            value=value,
            *args,
            **conf,
        )
        self.gb_sizer.Add(
            value_ctrl,
            (self.num_rows, 1),
            span=(1, 2),
            flag=wx.EXPAND | wx.ALL,
            border=5,
        )

        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_float_spinner(self, label_text, value="", conf=None):
        """Add a float value spinner to the user settings panel

        :param label_text: (str) Label text to display
        :param value: (None or float) Value to display
        :param conf: (None or dict) Dictionary containing parameters for the control

        """
        return self._add_spinner(UnitFloatSpinner, label_text, value, conf)

    @control_bookkeeper
    def add_integer_spinner(self, label_text, value="", conf=None):
        """Add a integer value spinner to the user settings panel

        :param label_text: (str) Label text to display
        :param value: (None or float) Value to display
        :param conf: (None or dict) Dictionary containing parameters for the control

        """
        return self._add_spinner(UnitIntegerSpinner, label_text, value, conf)

    @control_bookkeeper
    def add_dir_picker(self, label_text, value=None):
        """Add a label and directory control to the user settings panel

        :param label_text: (str) Label text to display
        :param value: (None or str) Value to display

        :return: (Ctrl, Ctrl) Label and text control

        """

        lbl_ctrl = self._add_side_label(label_text)
        value_ctrl = wx.DirPickerCtrl(
            self, path=str(value or ""), pos=(0, 0), size=(-1, 16)
        )
        value_ctrl.SetForegroundColour(gui.FG_COLOUR_DIS)
        value_ctrl.SetBackgroundColour(value_ctrl.Parent.BackgroundColour)
        self.gb_sizer.Add(
            value_ctrl, (self.num_rows, 1), flag=wx.EXPAND | wx.ALIGN_CENTER_VERTICAL
        )

        return lbl_ctrl, value_ctrl

    @control_bookkeeper
    def add_run_btn(self, label_text):
        """
        Add a generic run button and the corresponding side label to the gridbag sizer.
        :param label_text: (str) label text to display
        :returns: (wx.StaticText, ImageTextButton) side label and run button
        """
        lbl_ctrl = self._add_side_label(label_text)
        run_btn = ImageTextButton(self, label="Run", height=16, style=wx.ALIGN_CENTER)
        self.gb_sizer.Add(run_btn, (self.num_rows, 2), span=(1, 1),
                         flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.TOP | wx.BOTTOM, border=5)
        return lbl_ctrl, run_btn

# END Control methods
