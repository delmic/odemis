
import logging
import wx
from odemis import gui, model
from odemis.acq.milling.tasks import  MillingTaskSettings
from odemis.gui.comp.text import UnitFloatCtrl
from odemis.gui.comp.combo import ComboBox

class MillingTaskPanel(wx.Panel):
    """Panel for Milling Settings"""

    def __init__(self, parent, task: MillingTaskSettings):
        super().__init__(parent=parent, name=task.name)
        self._parent = parent
        self.SetForegroundColour(gui.FG_COLOUR_EDIT)
        self.SetBackgroundColour(gui.BG_COLOUR_MAIN)

        self.main_sizer = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self.main_sizer)

        self._panel = wx.Panel(self, style=wx.TAB_TRAVERSAL | wx.NO_BORDER)
        self._panel.SetBackgroundColour(gui.BG_COLOUR_MAIN)
        self._panel.SetForegroundColour(gui.FG_COLOUR_MAIN)
        self._panel.SetFont(self.GetFont())

        self.gb_sizer = wx.GridBagSizer()
        self._panel.SetSizer(self.gb_sizer)

        self.main_sizer.Add(self._panel, 1, wx.ALL | wx.EXPAND, 5)

        self.num_rows = 0
        self.task = task

        # header
        title = self._add_side_label(task.name)
        font = title.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        font.SetPointSize(font.GetPointSize() + 1)
        title.SetFont(font)
        self.num_rows += 1

        # map of control fields
        self.ctrl_dict = {}

        CONFIG = {
            "current": {"label": "Current", "accuracy": 2, "unit": "A"},
            "align": {"label": "Align at milling current"},
            "mode": {"label": "Milling mode"},
            "width": {"label": "Width", "accuracy": 2, "unit": "m"},
            "height": {"label": "Height", "accuracy": 2, "unit": "m"},
            "depth": {"label": "Depth", "accuracy": 2, "unit": "m"},
            "spacing": {"label": "Spacing", "accuracy": 2, "unit": "m"},
        }

        unsupported_parameters = ["name", "rotation",
                                  "center", "channel",
                                  "field_of_view", "voltage",
                                  "rate", "dwell_time"]

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
        # row height for milling pattern propeties controls
        row_height = 18
        # column width for milling pattern properties controls
        min_col_width = 120
        self.gb_sizer.SetItemMinSize(value_ctrl, min_col_width, row_height)
        self.gb_sizer.SetItemMinSize(lbl_ctrl, min_col_width, row_height)

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
