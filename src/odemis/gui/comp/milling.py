
import logging
import wx
from odemis import gui, model
from odemis.acq.milling.tasks import  MillingTaskSettings
from odemis.gui.comp.text import UnitFloatCtrl
from odemis.gui.comp.combo import ComboBox

class MillingTaskPanel(wx.Panel):
    """Panel for Milling Settings"""

    def __init__(self, parent, task: MillingTaskSettings):

        wx.Panel.__init__(self, parent=parent.pnl_patterns, name=task.name)
        self._parent = parent
        self.SetForegroundColour(gui.FG_COLOUR_EDIT)
        self.SetBackgroundColour(gui.BG_COLOUR_MAIN)

        self.main_sizer = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self.main_sizer)

        self.gb_sizer = wx.GridBagSizer()
        self.num_rows = 0

        self.task = task
        # self._header = MillingTaskPanelHeader(self)
        # # self._header.Bind(wx.EVT_LEFT_UP, self.on_toggle)
        # # self._header.Bind(wx.EVT_PAINT, self.on_draw_expander)

        # # self.Bind(wx.EVT_BUTTON, self.on_button, self._header)

        # # self._header.btn_remove.Bind(wx.EVT_BUTTON, self.on_remove_btn)
        # self._header.btn_show.Bind(wx.EVT_BUTTON, self.on_visibility_btn)
        # # if self._header.btn_peak is not None:
        #     # self._header.btn_peak.Bind(wx.EVT_BUTTON, self.on_peak_btn)

        # # if wx.Platform == "__WXMSW__":
        #     # self._header.Bind(wx.EVT_LEFT_DCLICK, self.on_button)

        # self.main_sizer.Add(self._header, 0, wx.EXPAND)


        self._panel = wx.Panel(self, style=wx.TAB_TRAVERSAL | wx.NO_BORDER)

        # Add a simple sizer so we can create padding for the panel
        # border_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.main_sizer.Add(self.gb_sizer, border=5, flag=wx.ALL | wx.EXPAND, proportion=1)

        # self._panel.SetSizer(border_sizer)

        self._panel.SetBackgroundColour(gui.BG_COLOUR_MAIN)
        self._panel.SetForegroundColour(gui.FG_COLOUR_MAIN)
        self._panel.SetFont(self.GetFont())

        self._panel.Show(True)
        self.main_sizer.Add(self._panel, 0, wx.EXPAND)

        # header
        self._add_side_label(task.name) # TODO: migrate to header
        self.num_rows += 1

        # map of control fields
        self.ctrl_dict = {}

        CONFIG = {
            "current": {"label": "Current", "accuracy": 2, "unit": "A"},
            "align": {"label": "Align at Milling Current"},
            "mode": {"label": "Milling Mode"},
            "width": {"label": "Width", "accuracy": 2, "unit": "m"},
            "height": {"label": "Height", "accuracy": 2, "unit": "m"},
            "depth": {"label": "Depth", "accuracy": 2, "unit": "m"},
            "spacing": {"label": "Spacing", "accuracy": 2, "unit": "m"},
        }

        unsupported_parameters = ["name", "rotation",
                                  "center", "channel",
                                  "field_of_view", "voltage"]

        for param in vars(task.milling):
            if param in unsupported_parameters:
                continue

            conf = CONFIG.get(param, {})
            label = conf.get("label", param)
            del conf["label"]

            val = getattr(task.milling, param)
            self._add_value_field(label, val, conf, param=param)

        pattern = task.patterns[0]

        for param in vars(pattern):

            if param in unsupported_parameters:
                continue

            conf = CONFIG.get(param, {})
            label = conf.get("label", param)
            del conf["label"]

            val = getattr(pattern, param)
            self._add_value_field(label, val, conf, param=param)

        # Fit sizer
        self.main_sizer.AddSpacer(5)
        self.SetSizerAndFit(self.main_sizer)
        self.Bind(wx.EVT_SIZE, self._on_size)
        self.Layout()
        self._parent.Refresh()

    def _add_value_field(self, label, val, conf, param: str):
        """Add a value field to the panel (label, ctrl)"""
        lbl_ctrl = self._add_side_label(label)
        value_ctrl = self._add_value_ctrl(val, conf)

        if value_ctrl is None:
            logging.debug(f"Unsupported parameter: {param}, {val}")
            return

        self.ctrl_dict[param] = value_ctrl
        self.gb_sizer.Add(value_ctrl, (self.num_rows, 1),
                        flag=wx.ALL | wx.EXPAND | wx.ALIGN_CENTER_VERTICAL,
                        border=5)

        value_ctrl.SetForegroundColour(gui.FG_COLOUR_EDIT)
        value_ctrl.SetBackgroundColour(gui.BG_COLOUR_MAIN)
        self.num_rows += 1

    def _add_value_ctrl(self, val, conf):
        """Add a control for a value"""
        value_ctrl = None
        if isinstance(val, model.StringEnumerated):
            value_ctrl = ComboBox(self._panel, value=val.value,
                        choices=val.choices, style=wx.CB_READONLY | wx.BORDER_NONE)
        if isinstance(val, model.FloatContinuous):
            value_ctrl = UnitFloatCtrl(self._panel, value=val.value,
                                        style=wx.NO_BORDER, **conf)
        if isinstance(val, model.BooleanVA):
            value_ctrl = wx.CheckBox(self._panel, **conf)
            value_ctrl.SetValue(val.value)

        return value_ctrl

    def _add_side_label(self, label_text, tooltip=None):
        """ Add a text label to the control grid

        This method should only be called from other methods that add control to the control grid

        :param label_text: (str)
        :return: (wx.StaticText)

        """

        lbl_ctrl = wx.StaticText(self._panel, -1, label_text)
        if tooltip:
            lbl_ctrl.SetToolTip(tooltip)

        self.gb_sizer.Add(lbl_ctrl, (self.num_rows, 0),
                        flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)
        return lbl_ctrl

        # ref: add_setting_entry

    def _on_size(self, event):
        """ Handle the wx.EVT_SIZE event for the Expander class """
        self.SetSize((self._parent.GetSize().x, -1))
        self.Layout()
        self.Refresh()
        event.Skip()

    def collapse(self, collapse):
        """ Collapses or expands the pane window """

        if self._collapsed == collapse:
            return

        self.Freeze()

        # update our state
        self._panel.Show(not collapse)
        self._collapsed = collapse

        # Call after is used, so the fit will occur after everything has been hidden or shown
        # wx.CallAfter(self.Parent.fit_streams)

        self.Thaw()

    # GUI events: update the stream when the user changes the values

    def on_visibility_btn(self, evt):
        # generate EVT_STREAM_VISIBLE
        return
        event = StreamVisibleEvent(visible=self._header.btn_show.GetToggle())
        wx.PostEvent(self, event)

import logging
from collections import OrderedDict

import matplotlib.colors as colors
import wx
import wx.lib.newevent
from decorator import decorator

from odemis import gui
from odemis.gui import (
    BG_COLOUR_MAIN,
    BG_COLOUR_STREAM,
    FG_COLOUR_DIS,
    FG_COLOUR_EDIT,
    FG_COLOUR_MAIN,
    FG_COLOUR_RADIO_ACTIVE,
    img,
)
from odemis.gui.comp import buttons
from odemis.gui.comp.buttons import ImageTextButton
from odemis.gui.comp.combo import ColorMapComboBox, ComboBox
from odemis.gui.comp.file import FileBrowser
from odemis.gui.comp.foldpanelbar import FoldPanelBar
from odemis.gui.comp.radio import GraphicalRadioButtonControl
from odemis.gui.comp.slider import (
    Slider,
    UnitFloatSlider,
    UnitIntegerSlider,
    VisualRangeSlider,
)
from odemis.gui.comp.stream_bar import StreamBar
from odemis.gui.comp.text import (
    FloatTextCtrl,
    SuggestTextCtrl,
    UnitFloatCtrl,
    UnitIntegerCtrl,
)
from odemis.gui.conf.data import COLORMAPS
from odemis.gui.evt import StreamPeakEvent, StreamRemoveEvent, StreamVisibleEvent
from odemis.gui.util import call_in_wx_main, ignore_dead
from odemis.gui.util.widgets import VigilantAttributeConnector
from odemis.model import TINT_FIT_TO_RGB, TINT_RGB_AS_IS

# Values to control which option is available
OPT_NAME_EDIT = 1  # allow the renaming of the stream (for one time only)
OPT_BTN_REMOVE = 2  # remove the stream entry
OPT_BTN_SHOW = 4  # show/hide the stream image
OPT_BTN_UPDATE = 8  # update/stop the stream acquisition
OPT_BTN_TINT = 16  # tint of the stream (if the VA exists)
OPT_BTN_PEAK = 32  # show/hide the peak fitting data
OPT_FIT_RGB = 64  # allow a Fit RGB colormap (for spectrum stremas)
OPT_NO_COLORMAPS = 128  # do not allow additional colormaps. Typical for an RGB image

TINT_CUSTOM_TEXT = u"Custom tint…"

CAPTION_PADDING_RIGHT = 5
ICON_WIDTH, ICON_HEIGHT = 16, 16

class MillingTaskPanelHeader(wx.Control):
    """ This class describes a clickable control responsible for expanding and collapsing the
    StreamPanel to which it belongs.

    It can also contain various sub buttons that allow for stream manipulation.

    """

    BUTTON_SIZE = (18, 18)  # The pixel size of the button
    BUTTON_BORDER_SIZE = 9  # Border space around the buttons

    def __init__(self, parent, wid=wx.ID_ANY, pos=wx.DefaultPosition, size=wx.DefaultSize,
                 style=wx.NO_BORDER):
        assert(isinstance(parent, MillingTaskPanel))
        super(MillingTaskPanelHeader, self).__init__(parent, wid, pos, size, style)

        self.SetBackgroundColour(self.Parent.BackgroundColour)

        # This style enables us to draw the background with our own paint event handler
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)

        # Callback when the label changes: (string (text) -> None)
        self.label_change_callback = None

        # Create and add sizer and populate with controls
        self._sz = wx.BoxSizer(wx.HORIZONTAL)

        # Fold indicator icon, drawn directly in the background in a fixed position
        self._foldIcons = wx.ImageList(16, 16)
        self._foldIcons.Add(img.getBitmap("icon/arr_down_s.png"))
        self._foldIcons.Add(img.getBitmap("icon/arr_right_s.png"))

        # Add the needed controls to the sizer

        # self.btn_remove = self._add_remove_btn() if self.Parent.options & OPT_BTN_REMOVE else None
        # if self.Parent.options & OPT_NAME_EDIT:
            # self.ctrl_label = self._add_suggest_ctrl()
        # else:
        self.ctrl_label = self._add_label_ctrl()
        # self.combo_colormap = self._add_colormap_combo() if self.Parent.options & OPT_BTN_TINT else None
        self.btn_show = self._add_visibility_btn()

        # Add spacer for creating padding on the right side of the header panel
        self._sz.Add((24, 1), 0)

        # Set the sizer of the Control
        self.SetSizerAndFit(self._sz)

        self.Bind(wx.EVT_SIZE, self.on_size)
        self.Layout()

    # Control creation methods

    def _add_remove_btn(self):
        """ Add a button for stream removal """
        btn_rem = buttons.ImageButton(self,
                                      bitmap=img.getBitmap("icon/ico_clear.png"),
                                      size=self.BUTTON_SIZE)
        btn_rem.bmpHover = img.getBitmap("icon/ico_clear_h.png")
        btn_rem.SetToolTip("Remove stream")
        self._add_ctrl(btn_rem)
        return btn_rem

    def _add_label_ctrl(self):
        """ Add a label control to the header panel """
        label_ctrl = wx.StaticText(self, wx.ID_ANY, self.Parent.task.name,
                                   style=wx.ST_ELLIPSIZE_END)
        # In case the name is too long, at least we can see it full with a mouse hover
        label_ctrl.SetToolTip(self.Parent.task.name)
        label_ctrl.SetBackgroundColour(self.Parent.GetBackgroundColour())
        label_ctrl.SetForegroundColour(FG_COLOUR_MAIN)
        self._add_ctrl(label_ctrl, stretch=True)
        return label_ctrl

    def _add_colormap_combo(self):
        """ Add the colormap combobox (in place of tint btn) """
        cbstyle = wx.NO_BORDER | wx.TE_PROCESS_ENTER

        # Determine possible choices
        if not isinstance(self.Parent.stream.tint.value, colors.Colormap):
            custom_tint = self.Parent.stream.tint.value
        else:
            custom_tint = (0, 0, 0)

        if self.Parent.options & OPT_NO_COLORMAPS:
            self.colormap_choices = OrderedDict([
                            ("Original", TINT_RGB_AS_IS),
                           ])
        else:
            self.colormap_choices = OrderedDict([
                            ("Grayscale", (255, 255, 255)),
                            ])

        # store the index
        self._colormap_original_idx = len(self.colormap_choices) - 1

        if self.Parent.options & OPT_FIT_RGB:
            self.colormap_choices["Fit to RGB"] = TINT_FIT_TO_RGB

        # store the index
        self._colormap_fitrgb_idx = len(self.colormap_choices) - 1

        self.colormap_choices.update(OrderedDict([
                        ("Red tint", (255, 0, 0)),
                        ("Green tint", (0, 255, 0)),
                        ("Blue tint", (0, 0, 255)),
                        (TINT_CUSTOM_TEXT, custom_tint),
                       ]))
        # store the index
        self._colormap_customtint_idx = len(self.colormap_choices) - 1

        if not self.Parent.options & OPT_NO_COLORMAPS:
            self.colormap_choices.update(COLORMAPS)  # add the predefined color maps

        colormap_combo = ColorMapComboBox(self, wx.ID_ANY, pos=(0, 0), labels=list(self.colormap_choices.keys()),
                                          choices=list(self.colormap_choices.values()), size=(88, 16),
                                          style=cbstyle)

        # determine which value to select
        for index, value in enumerate(self.colormap_choices.values()):
            if self.Parent.stream.tint.value == value:
                if self.Parent.options & OPT_NO_COLORMAPS:
                    colormap_combo.SetSelection(0)
                else:
                    colormap_combo.SetSelection(index)
                break
        else:
            # Set to grayscale by default
            colormap_combo.SetSelection(0)

        colormap_combo.Bind(wx.EVT_COMBOBOX, self._on_colormap_click)
        self.Parent.stream.tint.subscribe(self._on_colormap_value)
        self._add_ctrl(colormap_combo)
        return colormap_combo

    def _add_visibility_btn(self):
        """ Add the visibility toggle button to the stream panel header """
        visibility_btn = buttons.ImageToggleButton(self,
                                                              bitmap=img.getBitmap("icon/ico_eye_closed.png"))
        visibility_btn.bmpHover = img.getBitmap("icon/ico_eye_closed_h.png")
        visibility_btn.bmpSelected = img.getBitmap("icon/ico_eye_open.png")
        visibility_btn.bmpSelectedHover = img.getBitmap("icon/ico_eye_open_h.png")

        visibility_btn.SetToolTip("Toggle pattern visibility")
        self._add_ctrl(visibility_btn)
        return visibility_btn

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

    def show_peak_btn(self, show):
        """ Show or hide the peak button """
        self._show_ctrl(self.btn_peak, show)

    def show_show_btn(self, show):
        """ Show or hide the show button """
        self._show_ctrl(self.btn_show, show)

    def enable_remove_btn(self, enabled):
        """ Enable or disable the remove button """
        self.btn_remove.Enable(enabled)

    def enable_updated_btn(self, enabled):
        """ Enable or disable the update button """
        self.btn_update.Enable(enabled)

    def enable_show_btn(self, enabled):
        """ Enable or disable the show button """
        self.btn_show.Enable(enabled)

    def enable_peak_btn(self, enabled):
        """ Enable or disable the peak button """
        self.btn_peak.Enable(enabled)

    def enable_colormap_combo(self, enabled):
        """ Enable or disable colormap dropdown """
        self.combo_colormap.Enable(enabled)

    def enable(self, enabled):
        """ Enable or disable all buttons that are present """

        if self.btn_remove:
            self.enable_remove_btn(enabled)

        if self.btn_update:
            self.enable_updated_btn(enabled)

        if self.btn_show:
            self.enable_show_btn(enabled)

        if self.btn_peak:
            self.enable_peak_btn(enabled)

        if self.combo_colormap:
            self.enable_colormap_combo(enabled)

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
        self.show_peak_btn(False)

    # END Show/hide/disable controls

    # GUI event handlers

    def _on_label_change(self, evt):
        """ Call the label change callback when the label value changes """
        if callable(self.label_change_callback):
            self.label_change_callback(self.ctrl_label.GetValue())

    @call_in_wx_main
    @ignore_dead
    def _on_colormap_value(self, colour):
        """ Update the colormap selector to reflect the provided colour """
        # determine which value to select
        for index, value in enumerate(self.colormap_choices.values()):
            if colour == value:
                self.combo_colormap.SetSelection(index)
                break
            elif colour == TINT_FIT_TO_RGB:
                self.combo_colormap.SetSelection(self._colormap_fitrgb_idx)  # fit to RGB
                break
        else:  # Can't find the colour => it's custom tint
            if isinstance(colour, tuple):
                self.colormap_choices[TINT_CUSTOM_TEXT] = colour
                self.combo_colormap.SetClientData(self._colormap_customtint_idx, colour)
            else:
                logging.warning("Got unknown colormap, which is not a tint: %s", colour)

            self.combo_colormap.SetSelection(self._colormap_customtint_idx)  # custom tint

    @call_in_wx_main
    def _on_colormap_click(self, evt):
        """ Handle the mouse click event on the tint button """

        # check the value of the colormap
        index = self.combo_colormap.GetSelection()
        name, tint = list(self.colormap_choices.items())[index]

        if name == TINT_CUSTOM_TEXT:
            # Set default colour to the current value
            cldata = wx.ColourData()
            cldata.SetColour(wx.Colour(*tint))

            dlg = wx.ColourDialog(self, cldata)

            if dlg.ShowModal() == wx.ID_OK:
                tint = dlg.ColourData.GetColour().Get(includeAlpha=False)  # convert to a 3-tuple
                logging.debug("Colour %r selected", tint)
                # Setting the VA will automatically update the button's colour
                self.colormap_choices[TINT_CUSTOM_TEXT] = tint
                self.combo_colormap.SetClientData(index, tint)
            else:
                self._on_colormap_value(self.Parent.stream.tint.value)
                return

        self.Parent.stream.tint.value = tint

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
        if self.Parent.options & OPT_NAME_EDIT:
            self.ctrl_label.SelectAll()
