# -*- coding: utf-8 -*-

"""
:created: 27 Aug 2014
:author: Éric Piel
:copyright: © 2014 Éric Piel, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the terms  of the GNU
    General Public License version 2 as published by the Free Software  Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;  without
    even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR  PURPOSE. See the GNU
    General Public License for more details.

    You should have received a copy of the GNU General Public License along with Odemis. If not,
    see http://www.gnu.org/licenses/.

"""

from past.builtins import basestring
import logging
import odemis.gui.conf.file as conffile
import odemis.gui as gui
from odemis.util import testing
import os
import shutil
import unittest
from unittest.case import skip

logging.getLogger().setLevel(logging.DEBUG)


class ConfigTest(object):
    """ Generic test setup/teardown methods for testing one configuration """

    # .conf_class must be defined

    def setUp(self):
        # save the real user file to be able to do whatever we like
        filename = os.path.join(conffile.CONF_PATH, self.conf_class.file_name)
        backname = filename + u".testbak"
        if os.path.exists(filename):
            logging.info("Saving file %s", filename)
            shutil.copy2(filename, backname)
            self.backname = backname
        else:
            self.backname = None

        self.filename = filename

    def tearDown(self):
        if self.backname:
            logging.info("Restoring file %s", self.filename)
            shutil.copy2(self.backname, self.filename)
        else:
            try:
                os.remove(self.filename)
            except OSError:
                pass
            else:
                logging.info("Deleting test file %s", self.filename)

        # Reset the module globals
        gui.conf.CONF_GENERAL = None
        gui.conf.CONF_ACQUI = None
        gui.conf.CONF_CALIB = None


class GeneralConfigTest(ConfigTest, unittest.TestCase):

    conf_class = gui.conf.file.GeneralConfig

    def test_simple(self):
        conf = gui.conf.get_general_conf()
        path = conf.get_manual()
        if path is not None:
            self.assertTrue(os.path.exists(path))

        path = conf.get_manual("secom")
        if path is not None:
            self.assertTrue(os.path.exists(path))

        path = conf.get_dev_manual()
        if path is not None:
            self.assertTrue(os.path.exists(path))

    def test_save(self):
        conf = gui.conf.get_general_conf()
        conf.set("calibration", "ar_file", u"booo")

        # reset
        del conf
        gui.conf.CONF_GENERAL = None

        conf = gui.conf.get_general_conf()
        path = conf.get("calibration", "ar_file")
        self.assertEqual(path, u"booo")

    def test_save_unicode(self):
        conf = gui.conf.get_general_conf()
        conf.set("calibration", "ar_file", u"booµ")

        # reset
        del conf
        gui.conf.CONF_GENERAL = None

        conf = gui.conf.get_general_conf()
        path = conf.get("calibration", "ar_file")
        self.assertEqual(path, u"booµ")

    def test_default(self):
        try:
            os.remove(self.filename)
        except OSError:
            pass

        conf = gui.conf.get_general_conf()
        path = conf.get("calibration", "ar_file")
        self.assertEqual(path, u"")

        path = conf.get("calibration", "spec_file")
        self.assertEqual(path, u"")

        path = conf.get_manual()
        self.assertTrue(path.endswith(u".pdf"))


class AcquisitionConfigTest(ConfigTest, unittest.TestCase):

    conf_class = gui.conf.file.AcquisitionConfig

    def test_simple(self):
        conf = gui.conf.get_acqui_conf()
        self.assertIsInstance(conf.last_path, basestring)
        self.assertIsInstance(conf.last_format, basestring)
        self.assertLess(len(conf.last_extension), 12)

    def test_save(self):
        # Will fail if setting the properties goes wrong
        conf = gui.conf.get_acqui_conf()
        conf.last_path = u"/home/booo/"
        conf.last_format = "HDF5"
        conf.last_extension = ".h5"
        conf.fn_ptn = u"{timelng}-test {cnt}"

    def test_save_unicode(self):
        conf = gui.conf.get_acqui_conf()
        conf.last_path = u"/home/boooµ/"
        conf.last_format = "HDF5"
        conf.last_extension = ".h5"
        conf.fn_ptn = u"{timelng}-test {cnt} µm value"


class CalibrationConfigTest(ConfigTest, unittest.TestCase):

    conf_class = gui.conf.file.CalibrationConfig

    def test_simple(self):
        conf = gui.conf.get_calib_conf()

        # non existing id should return None
        calib = conf.get_sh_calib(0)
        self.assertIs(calib, None)

    def test_save(self):
        conf = gui.conf.get_calib_conf()

        shid = 125166841353
        # try with a bit annoying numbers
        htop = (-0.5, 1e-6)
        hbot = (5e9, -1.55158e-6)
        hfoc = 0.006
        ofoc = -0.001e-6
        strans = (5.468e-3, -365e-6)
        sscale = (1.1, 0.9)
        srot = 0.1
        iscale = (13.1, 13.1)
        irot = 5.9606
        iscale_xy = (1.01, 0.9)
        ishear = 1.1
        resa = (8.09, 2.16)
        resb = (-157.5, -202.9)
        hfwa = (-0.953, -0.009)
        scaleshift = (0.029, -2.90e-05)

        orig_calib = (htop, hbot, hfoc, ofoc, strans, sscale, srot, iscale, irot,
                      iscale_xy, ishear, resa, resb, hfwa, scaleshift)
        conf.set_sh_calib(shid, *orig_calib)

        # read back from memory
        back_calib = conf.get_sh_calib(shid)
        for o, b in zip(orig_calib, back_calib):
            if isinstance(o, tuple):
                testing.assert_tuple_almost_equal(o, b)
            else:
                self.assertAlmostEqual(o, b)

        # read back from file
        del conf
        gui.conf.CONF_CALIB = None

        conf = gui.conf.get_calib_conf()
        back_calib = conf.get_sh_calib(shid)
        for o, b in zip(orig_calib, back_calib):
            if isinstance(o, tuple):
                testing.assert_tuple_almost_equal(o, b)
            else:
                self.assertAlmostEqual(o, b)

if __name__ == "__main__":
    unittest.main()
