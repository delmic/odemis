import logging
import wx
from odemis import gui
from odemis.gui.comp.text import UnitFloatCtrl


class EditMeteorCalibrationDialog(wx.Dialog):
    """Panel for METEOR Stage Positions"""

    def __init__(self, parent, md_calib: dict):
        wx.Panel.__init__(self, parent=parent)
        self._parent = parent

        self.md_calib = md_calib

        self.SetForegroundColour(gui.FG_COLOUR_EDIT)
        self.SetBackgroundColour(gui.BG_COLOUR_MAIN)
        self.SetTitle("Edit METEOR Calibration")

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

        # map of control fields
        self.ctrl_dict = {}

        # make text bold, size 12
        lbl_ctrl = wx.StaticText(self._panel, -1, "Calibration")
        lbl_ctrl.SetFont(wx.Font(12, wx.DEFAULT, wx.NORMAL, wx.BOLD))
        self.gb_sizer.Add(
            lbl_ctrl,
            (self.num_rows, 0),
            flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL,
            border=5,
        )
        lbl_ctrl = wx.StaticText(self._panel, -1, "dx")
        self.gb_sizer.Add(
            lbl_ctrl,
            (self.num_rows, 1),
            flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL,
            border=5,
        )
        lbl_ctrl = wx.StaticText(self._panel, -1, "dy")
        self.gb_sizer.Add(
            lbl_ctrl,
            (self.num_rows, 2),
            flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL,
            border=5,
        )
        self.num_rows += 1

        # get the metadata from the stage
        trans_cor = md_calib.get("trans-dx", None), md_calib.get("trans-dy", None)
        pos_cor = md_calib.get("dx", None), md_calib.get("dy", None)

        conf = {"unit": "m", "accuracy": 3}

        pos: dict = {
            "Top-View": pos_cor,
            "FIB-View": trans_cor,
        }
        rng = 250e-6 # +/- 250um

        # TODO: change the y-calibration to use yz-pretilt correction rather than stage-bare

        for name, val_cor in pos.items():
            x_val, y_val = val_cor
            if x_val is None or y_val is None:
                continue # trans-cor not defined
            lbl_ctrl = wx.StaticText(self._panel, -1, name)
            self.gb_sizer.Add(
                lbl_ctrl,
                (self.num_rows, 0),
                flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL,
                border=5,
            )

            # set the range of the x and y values
            xmin = x_val - rng
            xmax = x_val + rng
            ymin = y_val - rng
            ymax = y_val + rng

            x_value_ctrl = UnitFloatCtrl(
                self._panel, value=x_val, style=wx.NO_BORDER,
                min_val=xmin, max_val=xmax, **conf
            )
            y_value_ctrl = UnitFloatCtrl(
                self._panel, value=y_val, style=wx.NO_BORDER,
                min_val=ymin, max_val=ymax, **conf
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

        # add a label below the controls
        txt = f"Editing the calibration is currently restricted to +/- {rng*1e6}um. \nPlease edit the configuration file directly for larger changes."
        lbl_ctrl = wx.StaticText(self._panel, -1, txt)
        lbl_ctrl.Wrap(375)  # Wrap to 350 pixels

        lbl_ctrl.SetFont(wx.Font(8, wx.DEFAULT, wx.NORMAL, wx.NORMAL))
        self.gb_sizer.Add(
            lbl_ctrl,
            (self.num_rows, 0),
            span=(1, 3),
            flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL,
            border=5,
        )
        self.num_rows += 1

        # add update button
        self.update_button = wx.Button(self._panel, wx.ID_OK, "Update")
        self.update_button.Bind(wx.EVT_BUTTON, self._on_update)
        self.gb_sizer.Add(
            self.update_button,
            (self.num_rows, 0),
            flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL,
            border=5,
        )
        # add cancel button
        self.cancel_button = wx.Button(self._panel, wx.ID_CANCEL, "Cancel")
        self.cancel_button.Bind(wx.EVT_BUTTON, self._on_cancel)
        self.gb_sizer.Add(
            self.cancel_button,
            (self.num_rows, 1),
            flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL,
            border=5,
        )
        # Use blue color for the update button
        update_bg_color = wx.Colour(0, 100, 200)  # Blue color
        self.update_button.SetBackgroundColour(update_bg_color)
        self.update_button.SetForegroundColour(wx.WHITE)

        cancel_bg_color = wx.Colour(200, 0, 0)  # Red color
        self.cancel_button.SetBackgroundColour(cancel_bg_color)
        self.cancel_button.SetForegroundColour(wx.WHITE)

        # Fit sizer
        self.main_sizer.AddSpacer(5)
        self.Fit()
        self.Layout()

    def _on_cancel(self, event):
        logging.debug("Cancel button clicked")
        event.Skip()
        self.EndModal(wx.ID_CANCEL)

    def _on_update(self, event):
        logging.debug("Update button clicked")
        event.Skip()

        for name, ctrl in self.ctrl_dict.items():
            if name == "Top-View":
                x_val = ctrl["x"].GetValue()
                y_val = ctrl["y"].GetValue()
                self.md_calib.update({"dx": x_val, "dy": y_val})

            elif name == "FIB-View":
                x_val = ctrl["x"].GetValue()
                y_val = ctrl["y"].GetValue()
                self.md_calib.update({"trans-dx": x_val, "trans-dy": y_val})

        logging.debug(f"Updated calibration stage metadata: {self.md_calib}")

        self.EndModal(wx.ID_OK)
