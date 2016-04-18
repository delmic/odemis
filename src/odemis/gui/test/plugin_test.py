# -*- coding: utf-8 -*-

"""
Created on 01 Mar 2016

@author: Éric Piel

Copyright © 2016 Éric Piel, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
    General Public License version 2 as published by the Free Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
    the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
    Public License for more details.

    You should have received a copy of the GNU General Public License along with Odemis. If not,
    see http://www.gnu.org/licenses/.

"""

from __future__ import division

import logging
from odemis import model, dataio
from odemis.gui import plugin
import time
import unittest

from odemis.gui.model import MainGUIData
from odemis.gui.plugin import Plugin, AcquisitionDialog
import odemis.gui.test as test
from odemis.gui.test.comp_stream_test import FakeFluoStream
from odemis.gui.xmlh import odemis_get_resources
from odemis.gui import main_xrc, CONTROL_SAVE_FILE

logging.getLogger().setLevel(logging.DEBUG)

test.goto_manual()

main_xrc.get_resources = odemis_get_resources


class SimplePlugin(Plugin):
    name = "Example plugin"
    __version__ = "1.0.1"
    __author__ = "Éric Piel"
    __license__ = "GNU General Public License 2"

    def __init__(self, microscope, main_app):
        super(SimplePlugin, self).__init__(microscope, main_app)
        self.addMenu("Acquisition/Fancy acquisition...", self.start)
        self.importantValue = model.FloatContinuous(2, (0, 10), unit="s")
        self.filename = model.StringVA("boo.h5")

    def start(self):
        dlg = AcquisitionDialog(self, "Fancy Acquisition", "Enter everything")
        dlg.addSettings(self, conf={"filename": {"control_type": CONTROL_SAVE_FILE}})
        dlg.addButton("Cancel")
        dlg.addButton("Acquire", self.acquire, face_colour='blue')

        stream = FakeFluoStream("Fluo Stream")
        dlg.addStream(stream)
        stream = FakeFluoStream("Fluo Stream")
        dlg.addStream(stream)

        ans = dlg.ShowModal()

        if ans == 0:
            # Ignore errors about a missing analysis tab
            try:
                self.showAcquisition(self.filename.value)
            except AttributeError:
                pass

    def acquire(self, dlg):
        f = model.ProgressiveFuture()
        f.task_canceller = lambda l: True  # To allow cancelling while it's running
        dlg.showProgress(f)

        d = []
        for i in range(10):
            f.set_progress(end=time.time() + (10 - i))
            if self.microscope is not None:
                d.append(self.microscope.ccd.data.get())
            if f.cancelled():
                return

        if d:
            dataio.hdf5.export(self.filename.value, d)
            dlg.Destroy()


class PluginTestCase(test.GuiTestCase):

    frame_class = test.test_gui.xrccanvas_frame

    def test_find_plugins(self):
        """ Test that find_plugins can find plugin modules"""
        paths = plugin.find_plugins()
        self.assertGreater(len(paths), 0)

    def test_load_plugin(self):
        # Try to load the example plugin present in this module
        ps = plugin.load_plugin(__file__, None, self.app)
        self.assertEqual(len(ps), 1)
        self.assertEqual(ps[0].name, SimplePlugin.name)

    def test_add_menu(self):
        self.frame.SetSize((400, 60))
        orig_menu_len = self.frame.GetMenuBar().GetMenuCount()
        self.app.main_data = MainGUIData(None)
        sp = SimplePlugin(self.app.main_data, self.app)
        sp.addMenu("TestRec/Recursive/Very Long/Finally the entry\tCtrl+T",
                   self._on_menu_entry)
        # Reuse the path
        sp.addMenu("TestRec/Recursive/Another entry\tCtrl+E",
                   self._on_menu_entry)

        test.gui_loop(100)

        # There should be two more main menus
        end_menu_len = self.frame.GetMenuBar().GetMenuCount()
        self.assertEqual(end_menu_len, orig_menu_len + 2)

        # The last menu should still be "Help"
        ltxt = self.frame.GetMenuBar().GetMenuLabel(end_menu_len - 1)
        self.assertEqual(ltxt, "Help")

    def _on_menu_entry(self):
        logging.info("Entry menu got pressed")

if __name__ == "__main__":
    unittest.main()
