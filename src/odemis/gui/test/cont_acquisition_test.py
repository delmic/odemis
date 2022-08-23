#-*- coding: utf-8 -*-

"""
author: Éric Piel <piel@delmic.com>

Copyright © 2021 Éric Piel, Delmic

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
from odemis.gui.cont.acquisition import SnapshotController
import time
import unittest


class SnapshotControllerTestCase(unittest.TestCase):

    def test_get_display_outputs(self):

        outputs = SnapshotController.get_display_outputs()
        self.assertGreaterEqual(len(outputs), 1)
        for o in outputs:
            self.assertIsInstance(o, str)

    def test_set_output_brightness(self):
        outputs = SnapshotController.get_display_outputs()

        # We cannot really check that it worked, but at least it shouldn't raise an exception
        SnapshotController.set_output_brightness(outputs, 0.8)

        time.sleep(1)

        # Put it back
        SnapshotController.set_output_brightness(outputs, 1)


if __name__ == "__main__":
    # gen_test_data()
    unittest.main()
