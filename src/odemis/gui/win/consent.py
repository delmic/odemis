# -*- coding: utf-8 -*-
"""
Consent dialog for Odemis data collection.
"""

import wx


class ConsentDialog(wx.Dialog):
    """Dialog asking user consent for data sharing."""

    RESULT_OPT_IN = int(wx.NewIdRef())
    RESULT_OPT_OUT = int(wx.NewIdRef())
    RESULT_REMIND_LATER = int(wx.NewIdRef())

    def __init__(self, parent: wx.Window, remind_days: int) -> None:
        title = "Share data with Delmic"
        super().__init__(parent, wx.ID_ANY, title=title, size=(560, -1))

        sizer = wx.BoxSizer(wx.VERTICAL)
        message = (
            "Help improve Odemis by sharing anonymized diagnostic and measurement data.\n\n"
            "You can change this choice later from Help > Share data with Delmic."
        )
        label = wx.StaticText(self, wx.ID_ANY, message)
        label.Wrap(520)
        sizer.Add(label, 0, wx.ALL | wx.EXPAND, 12)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_opt_out = wx.Button(self, wx.ID_ANY, "Opt out")
        btn_remind_later = wx.Button(self, wx.ID_ANY, f"Remind me in {remind_days} days")
        btn_opt_in = wx.Button(self, wx.ID_ANY, "Opt in")
        btn_opt_out.SetDefault()

        btn_opt_in.Bind(wx.EVT_BUTTON, self._on_opt_in)
        btn_opt_out.Bind(wx.EVT_BUTTON, self._on_opt_out)
        btn_remind_later.Bind(wx.EVT_BUTTON, self._on_remind_later)
        self.Bind(wx.EVT_CLOSE, self._on_close)

        button_sizer.Add(btn_opt_out, 0, wx.RIGHT, 8)
        button_sizer.Add(btn_remind_later, 0, wx.RIGHT, 8)
        button_sizer.Add(btn_opt_in, 0)
        sizer.Add(button_sizer, 0, wx.ALL | wx.ALIGN_RIGHT, 12)

        self.SetSizer(sizer)
        sizer.Fit(self)
        self.CentreOnParent()

    def _on_opt_in(self, _evt: wx.CommandEvent) -> None:
        self.EndModal(self.RESULT_OPT_IN)

    def _on_opt_out(self, _evt: wx.CommandEvent) -> None:
        self.EndModal(self.RESULT_OPT_OUT)

    def _on_remind_later(self, _evt: wx.CommandEvent) -> None:
        self.EndModal(self.RESULT_REMIND_LATER)

    def _on_close(self, _evt: wx.CloseEvent) -> None:
        self.EndModal(self.RESULT_REMIND_LATER)
