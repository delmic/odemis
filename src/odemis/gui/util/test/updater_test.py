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
from __future__ import division
from odemis.gui.util import updater
import logging
import unittest

logging.getLogger().setLevel(logging.DEBUG)


class TestWindowsUpdater(unittest.TestCase):
    def test_version(self):
        # That should work on any OS
        u = updater.WindowsUpdater()
        lv = u.get_local_version()
        # Should be #.#.### or #.#.#.###
        self.assertIn(len(lv.split('.')), (3, 4))

        rv, rsize = u.get_remote_version()
        # Note: if no internet access, it would be acceptable to get None

        self.assertIn(len(rv.split('.')), (3, 4))
        self.assertGreater(rsize, 1000)

        # u.check_for_update()

    # TODO: test more methods

if __name__ == "__main__":
    unittest.main()
