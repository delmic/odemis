# -*- coding: utf-8 -*-

"""
Created on 1 Dec 2015

@author: Éric Piel

Copyright © 2015 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

# test the functions of the gui.util.updater module
import logging
import unittest
import wx
from odemis.gui.util import updater

logging.getLogger().setLevel(logging.DEBUG)


class TestWindowsUpdater(unittest.TestCase):
    def test_version(self):
        # That should work on any OS
        u = updater.WindowsUpdater()

        rv = u.get_remote_version()
        # Note: if no internet access, it would be acceptable to get None

        self.assertIn(len(rv.split('.')), (3, 4))

        # u.check_for_update()

    def test_downloader(self):
        app = wx.App()
        app.main_frame = wx.Frame()
        u = updater.WindowsUpdater()
        rv = u.get_remote_version()
        self.assertIsInstance(rv, str)
        u.download_installer(rv)
        # u.show_update_dialog(rv)

    # TODO: test more methods


if __name__ == "__main__":
    unittest.main()
