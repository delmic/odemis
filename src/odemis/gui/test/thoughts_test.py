#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on April 1st, 2026 by the Odemis team.

Copyright © 2026 Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
"""

import datetime
import unittest
from unittest.mock import MagicMock, patch

import wx

from odemis.gui.win.thoughts import THOUGHTS, _decode_jokes, show_important_thought_dialog


class TestDecodeJokes(unittest.TestCase):
    """Tests for the joke decoding helper."""

    def test_count(self):
        """All jokes from THOUGHTS must be decodable."""
        self.assertEqual(len(_decode_jokes()), len(THOUGHTS))

    def test_non_empty(self):
        """Every decoded joke must be a non-empty string."""
        for joke in _decode_jokes():
            self.assertIsInstance(joke, str)
            self.assertTrue(joke.strip())

    def test_raw_entries_are_bytes(self):
        """Raw THOUGHT entries must be bytes literals (base64 encoded)."""
        for entry in THOUGHTS:
            self.assertIsInstance(entry, bytes)


class TestShowImportantThoughtDialog(unittest.TestCase):
    """Tests for the show_important_thought_dialog function."""

    @classmethod
    def setUpClass(cls):
        cls.app = wx.App(False)
        cls.frame = wx.Frame(None)

    @classmethod
    def tearDownClass(cls):
        cls.frame.Destroy()
        if cls.app:
            cls.app.Destroy()

    def _make_mock_dialog(self, answer):
        """Return a mock wx.MessageDialog that returns the given answer."""
        mock_dlg = MagicMock()
        mock_dlg.ShowModal.return_value = answer
        return mock_dlg

    def test_shows_on_april_first(self):
        """Dialog must be shown when today is April 1st."""
        april_first = datetime.date(2025, 4, 1)
        mock_dlg = self._make_mock_dialog(wx.ID_NO)
        with patch("odemis.gui.win.thoughts.datetime") as mock_dt, \
             patch("odemis.gui.win.thoughts.wx.MessageDialog", return_value=mock_dlg):
            mock_dt.date.today.return_value = april_first
            show_important_thought_dialog(self.frame)
            mock_dlg.ShowModal.assert_called_once()
            mock_dlg.Destroy.assert_called_once()

    def test_no_dialog_on_other_days(self):
        """Dialog must NOT be shown on any day other than April 1st."""
        for date in [datetime.date(2025, 3, 31), datetime.date(2025, 4, 2),
                     datetime.date(2025, 1, 1), datetime.date(2025, 12, 25)]:
            with patch("odemis.gui.win.thoughts.datetime") as mock_dt, \
                 patch("odemis.gui.win.thoughts.wx.MessageDialog") as mock_cls:
                mock_dt.date.today.return_value = date
                show_important_thought_dialog(self.frame)
                mock_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
