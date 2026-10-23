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

from odemis.gui.test import gui_loop
from odemis.gui.win.data_sharing_dialog import DataSharingDialog
from odemis.util.datacollector import DataCollector, DataCollectorConfig


class DataSharingDialogTest(unittest.TestCase):
    """Test the dialog to choose the data sharing settings."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = wx.App(False)
        # As in the real GUI, the dialog is created once the main window is already shown.
        # Otherwise, the first window is laid out with the wrong text size (too short).
        cls.frame = wx.Frame(None)
        cls.frame.Show()
        gui_loop()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.frame.Destroy()
        cls.app.Destroy()

    def test_data_sharing_dialog_saves_selection_on_close(self) -> None:
        """Closing the data sharing dialog sets the session consent to the selected radio button."""
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
            dlg = DataSharingDialog(self.frame, dc, "sparc2")
            dlg.Show()
            gui_loop(2)  # Keep the window visible for 2 s

            (_, default_rb), (_, session_rb) = dlg._radios
            self.assertTrue(default_rb.GetValue())
            self.assertFalse(session_rb.GetValue())
            # Select the "enabled for the session" radio button
            # (the native group handling unselects the other one)
            session_rb.SetValue(True)
            self.assertFalse(default_rb.GetValue())
            gui_loop(2)  # Show the new selection for 2 s

            # Closing the window saves the selection, for the session only: the default is unchanged
            dlg.Close()
            dlg.Destroy()
            self.assertTrue(dc.get_consent())
            self.assertFalse(dc.get_consent_default())


if __name__ == "__main__":
    unittest.main()
