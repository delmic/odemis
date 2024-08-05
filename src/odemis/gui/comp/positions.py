import logging
import math
import wx
from odemis import gui, model
from odemis.gui.comp.text import UnitFloatCtrl


class EditMeteorPositionsDialog(wx.Dialog):
    """Panel for METEOR Stage Positions"""

    def __init__(self, parent, stage_md: dict):
        wx.Panel.__init__(self, parent=parent)
        self._parent = parent
        self._update_pressed = False

        self.SetForegroundColour(gui.FG_COLOUR_EDIT)
        self.SetBackgroundColour(gui.BG_COLOUR_MAIN)
        self.SetTitle("Edit METEOR Positions")

        self.main_sizer = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self.main_sizer)

        self.gb_sizer = wx.GridBagSizer()
        self.num_rows = 0

        self._panel = wx.Panel(self, style=wx.TAB_TRAVERSAL | wx.NO_BORDER)

        # Add a simple sizer so we can create padding for the panel
        border_sizer = wx.BoxSizer(wx.HORIZONTAL)
        border_sizer.Add(self.gb_sizer, border=5, flag=wx.ALL | wx.EXPAND, proportion=1)

        self._panel.SetSizer(border_sizer)

        self._panel.SetBackgroundColour(gui.BG_COLOUR_MAIN)
        self._panel.SetForegroundColour(gui.FG_COLOUR_MAIN)
        self._panel.SetFont(self.GetFont())

        self._panel.Show(True)
        self.main_sizer.Add(self._panel, 0, wx.EXPAND)

        # header
        lbl_ctrl = wx.StaticText(self._panel, -1, "Positions")
        lbl_ctrl.SetFont(wx.Font(12, wx.DEFAULT, wx.NORMAL, wx.BOLD))
        self.gb_sizer.Add(
            lbl_ctrl,
            (self.num_rows, 0),
            flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL,
            border=5,
        )

        lbl_ctrl = wx.StaticText(self._panel, -1, "Tilt")
        self.gb_sizer.Add(
            lbl_ctrl,
            (self.num_rows, 1),
            flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL,
            border=5,
        )
        lbl_ctrl = wx.StaticText(self._panel, -1, "Rotation")
        self.gb_sizer.Add(
            lbl_ctrl,
            (self.num_rows, 2),
            flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL,
            border=5,
        )
        self.num_rows += 1

        # map of control fields
        self.ctrl_dict = {}

        fm_fav_pos = stage_md[model.MD_FAV_FM_POS_ACTIVE]
        sem_fav_pos = stage_md[model.MD_FAV_SEM_POS_ACTIVE]
        mill_fav_pos = stage_md[model.MD_FAV_MILL_POS_ACTIVE]
        fib_fav_pos = stage_md[model.MD_FAV_FIB_POS_ACTIVE]

        conf = {"unit": "°", "accuracy": 2}

        pos: dict = {
            "SEM": sem_fav_pos,
            "MILL": mill_fav_pos,
            "FIB": fib_fav_pos,
            "FM": fm_fav_pos,
        }

        for name, fav_pos in pos.items():
            lbl_ctrl = wx.StaticText(self._panel, -1, name)
            self.gb_sizer.Add(
                lbl_ctrl,
                (self.num_rows, 0),
                flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL,
                border=5,
            )
            rx_val = fav_pos["rx"]
            rz_val = fav_pos["rz"]

            rx_value_ctrl = UnitFloatCtrl(
                self._panel, value=math.degrees(rx_val), style=wx.NO_BORDER, **conf
            )
            rz_value_ctrl = UnitFloatCtrl(
                self._panel, value=math.degrees(rz_val), style=wx.NO_BORDER, **conf
            )
            self.gb_sizer.Add(
                rx_value_ctrl,
                (self.num_rows, 1),  # span=(1, 3),
                flag=wx.ALL | wx.EXPAND | wx.ALIGN_CENTER_VERTICAL,
                border=5,
            )
            self.gb_sizer.Add(
                rz_value_ctrl,
                (self.num_rows, 2),  # span=(3, 5),
                flag=wx.ALL | wx.EXPAND | wx.ALIGN_CENTER_VERTICAL,
                border=5,
            )

            self.ctrl_dict[name] = {"rx": rx_value_ctrl, "rz": rz_value_ctrl}

            rx_value_ctrl.SetForegroundColour(gui.FG_COLOUR_EDIT)
            rx_value_ctrl.SetBackgroundColour(gui.BG_COLOUR_MAIN)
            rz_value_ctrl.SetForegroundColour(gui.FG_COLOUR_EDIT)
            rz_value_ctrl.SetBackgroundColour(gui.BG_COLOUR_MAIN)
            self.num_rows += 1

        # make text bold, size 12
        lbl_ctrl = wx.StaticText(self._panel, -1, "Calibration")
        lbl_ctrl.SetFont(wx.Font(12, wx.DEFAULT, wx.NORMAL, wx.BOLD))
        self.gb_sizer.Add(
            lbl_ctrl,
            (self.num_rows, 0),
            flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL,
            border=5,
        )
        lbl_ctrl = wx.StaticText(self._panel, -1, "X")
        self.gb_sizer.Add(
            lbl_ctrl,
            (self.num_rows, 1),
            flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL,
            border=5,
        )
        lbl_ctrl = wx.StaticText(self._panel, -1, "Y")
        self.gb_sizer.Add(
            lbl_ctrl,
            (self.num_rows, 2),
            flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL,
            border=5,
        )
        self.num_rows += 1

        trans_cor = stage_md[model.MD_POS_TRANS_COR]
        pos_cor = stage_md[model.MD_POS_COR]

        conf = {"unit": "m", "accuracy": 3}

        pos: dict = {
            "Rotation": pos_cor,
            "Translation": trans_cor,
        }

        for name, val_cor in pos.items():
            lbl_ctrl = wx.StaticText(self._panel, -1, name)
            self.gb_sizer.Add(
                lbl_ctrl,
                (self.num_rows, 0),
                flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL,
                border=5,
            )
            x_val, y_val = val_cor

            x_value_ctrl = UnitFloatCtrl(
                self._panel, value=x_val, style=wx.NO_BORDER, **conf
            )
            y_value_ctrl = UnitFloatCtrl(
                self._panel, value=y_val, style=wx.NO_BORDER, **conf
            )
            self.gb_sizer.Add(
                x_value_ctrl,
                (self.num_rows, 1),
                flag=wx.ALL | wx.EXPAND | wx.ALIGN_CENTER_VERTICAL,
                border=5,
            )
            self.gb_sizer.Add(
                y_value_ctrl,
                (self.num_rows, 2),
                flag=wx.ALL | wx.EXPAND | wx.ALIGN_CENTER_VERTICAL,
                border=5,
            )

            self.ctrl_dict[name] = {"x": x_value_ctrl, "y": y_value_ctrl}

            x_value_ctrl.SetForegroundColour(gui.FG_COLOUR_EDIT)
            x_value_ctrl.SetBackgroundColour(gui.BG_COLOUR_MAIN)
            y_value_ctrl.SetForegroundColour(gui.FG_COLOUR_EDIT)
            y_value_ctrl.SetBackgroundColour(gui.BG_COLOUR_MAIN)
            self.num_rows += 1

        # add update button
        self.update_button = wx.Button(self._panel, -1, "Update")
        self.update_button.Bind(wx.EVT_BUTTON, self._on_update)
        self.gb_sizer.Add(
            self.update_button,
            (self.num_rows, 0),
            flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL,
            border=5,
        )
        self.update_button = wx.Button(self._panel, -1, "Cancel")
        self.update_button.Bind(wx.EVT_BUTTON, self._on_cancel)
        self.gb_sizer.Add(
            self.update_button,
            (self.num_rows, 1),
            flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL,
            border=5,
        )

        # Fit sizer
        self.main_sizer.AddSpacer(5)
        self.Fit()
        self.Layout()

    def _on_cancel(self, event):
        logging.debug("Cancel button clicked")
        event.Skip()
        self.Close()

    def _on_update(self, event):
        logging.debug("Update button clicked")
        event.Skip()

        self.stage_md = {}

        name_map: dict = {
            "SEM": model.MD_FAV_SEM_POS_ACTIVE,
            "MILL": model.MD_FAV_MILL_POS_ACTIVE,
            "FIB": model.MD_FAV_FIB_POS_ACTIVE,
            "FM": model.MD_FAV_FM_POS_ACTIVE,
            "Rotation": model.MD_POS_COR,
            "Translation": model.MD_POS_TRANS_COR,
        }

        for name, ctrl in self.ctrl_dict.items():
            if name in ["Rotation", "Translation"]:
                self.stage_md[name_map[name]] = (
                    (ctrl["x"].GetValue()),
                    (ctrl["y"].GetValue()),
                )
            else:
                self.stage_md[name_map[name]] = {
                    "rx": math.radians(ctrl["rx"].GetValue()),
                    "rz": math.radians(ctrl["rz"].GetValue()),
                }

        logging.debug(f"Updated stage metadata: {self.stage_md}")

        self._update_pressed = True
        self.Close()

        # ref: add_setting_entry
