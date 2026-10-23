# -*- coding: utf-8 -*-
"""
Copyright © 2026 Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

import configparser
import tempfile
import unittest
from pathlib import Path

import wx

from odemis.gui.win.data_sharing_dialog import DataSharingDialog
from odemis.util.datacollector import DataCollector, DataCollectorConfig


class DataSharingDialogTest(unittest.TestCase):
    """Test the dialog to choose the data sharing settings."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = wx.App(False)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.Destroy()

    def test_data_sharing_dialog_saves_selection_on_close(self) -> None:
        """Closing the data sharing dialog sets the session consent to the selected checkbox."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Config file where the sharing is disabled by default
            cfg_path = Path(tmp_dir) / "data_sharing.config"
            cfg_path.write_text("[general]\nacquisition_analytics = false\n", encoding="utf-8")
            cfg = DataCollectorConfig.__new__(DataCollectorConfig)
            cfg.file_path = cfg_path
            cfg._cp = configparser.ConfigParser(interpolation=None)
            cfg._read()
            # DataCollector using that config, without the background worker
            dc = DataCollector()
            dc._config = cfg
            dc._init_ok = True

            # Initially, the default (disabled) is selected
            dlg = DataSharingDialog(None, dc, "sparc2")
            (_, default_cb), (_, session_cb) = dlg._checkboxes
            self.assertTrue(default_cb.GetValue())
            self.assertFalse(session_cb.GetValue())
            # Simulate the user clicking on the "enabled for the session" checkbox
            # (SetValue() doesn't send the event, so send it explicitly)
            session_cb.SetValue(True)
            evt = wx.CommandEvent(wx.EVT_CHECKBOX.typeId, session_cb.GetId())
            evt.SetEventObject(session_cb)
            session_cb.GetEventHandler().ProcessEvent(evt)
            # The other checkbox should get automatically unchecked
            self.assertFalse(default_cb.GetValue())
            # Closing the window saves the selection, for the session only: the default is unchanged
            dlg.Close()
            dlg.Destroy()
            self.assertTrue(dc.get_consent())
            self.assertFalse(dc.get_consent_default())


if __name__ == "__main__":
    unittest.main()
