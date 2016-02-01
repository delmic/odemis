#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Created on 30 Sept 2015

@author: Rinze de Laat

Copyright © 2015 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.

"""

import logging
from odemis.gui import test, log
import random
import threading
import unittest


log.init_logger(logging.DEBUG)
test.goto_manual()

LOG_FUNCTIONS = (logging.debug, logging.info, logging.warn, logging.error, logging.exception)


class TestLogWindow(test.GuiTestCase):
    frame_class = test.test_gui.xrclog_frame
    frame_size = (800, 200)

    def test_log_window(self):
        log.create_gui_logger(self.frame.txt_log)

        def log_msg():
            for i in xrange(50000):
                random.choice(LOG_FUNCTIONS)("WEEEEEE %d" % i)
                threading._sleep(0.0001)

        t = threading.Thread(target=log_msg)
        # Setting Daemon to True, will cause the thread to exit when the parent does
        t.setDaemon(True)
        t.start()

        test.gui_loop()


if __name__ == "__main__":
    unittest.main()
    suit = unittest.TestSuite()
