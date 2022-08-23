# -*- coding: utf-8 -*-
'''
Created on 19 Dec 2013

@author: Kimon Tsitsikas

Copyright © 2012-2013 Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms 
of the GNU General Public License version 2 as published by the Free Software 
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; 
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR 
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with 
Odemis. If not, see http://www.gnu.org/licenses/.
'''
from concurrent import futures
import logging
from odemis import model, dataio
from odemis.acq import align
from odemis.driver import semcomedi, andorcam2
from odemis.util import testing
import os
import time
import unittest


# logging.basicConfig(format=" - %(levelname)s \t%(message)s")
logging.getLogger().setLevel(logging.DEBUG)
# _frm = "%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s"
# logging.getLogger().handlers[0].setFormatter(logging.Formatter(_frm))

# CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
# SECOM_LENS_CONFIG = CONFIG_PATH + "sim/secom-sim-lens-align.odm.yaml"  # 4x4


# arguments used for the creation of basic components
CONFIG_SED = {"name": "sed", "role": "sed", "channel":5, "limits": [-3, 3]}
CONFIG_SCANNER = {"name": "scanner", "role": "ebeam", "limits": [[-5, 5], [3, -3]],
                  "channels": [0, 1], "settle_time": 10e-6, "hfw_nomag": 0.25,
                  "max_res": [16384, 16384], "park": [8, 8]}
CONFIG_SEM = {"name": "sem", "role": "sem", "device": "/dev/comedi0",
              "children": {"detector0": CONFIG_SED, "scanner": CONFIG_SCANNER}
              }


class TestOverlay(unittest.TestCase):
    """
    Test Overlay functions
    """
    backend_was_running = False

    def setUp(self):
        self.sem = None
        self.sed = None
        self.ebeam = None
        self.ccd = None

    def tearDown(self):
        if self.sem:
            self.sem.terminate()

        if self.ccd:
            self.ccd.terminate()

    def _prepare_hardware(self, ebeam_kwargs=None, ebeam_mag=2000, ccd_img=None):
        if ccd_img is None:
            localpath = os.path.dirname(andorcam2.__file__)
            imgpath = os.path.abspath(os.path.join(localpath, "andorcam2-fake-spots-4x4.h5"))
        else:
            # Force absolute path, to be able to accept path relative from here
            localpath = os.path.dirname(__file__)
            imgpath = os.path.abspath(os.path.join(localpath, ccd_img))
        fakeccd = andorcam2.AndorCam2(name="camera", role="ccd", device="fake", image=imgpath)
        # Set the pixel size from the image (as there is no lens + md_updater)
        converter = dataio.find_fittest_converter(imgpath, mode=os.O_RDONLY)
        img = converter.read_data(imgpath)[0]
        fakeccd.updateMetadata({model.MD_PIXEL_SIZE: img.metadata[model.MD_PIXEL_SIZE]})
        self.ccd = fakeccd

        # Force a ratio and hfw_nomag
        conf_scan = CONFIG_SCANNER.copy()
        if ebeam_kwargs:
            conf_scan.update(ebeam_kwargs)
        conf_sem = CONFIG_SEM.copy()
        conf_sem["children"]["scanner"] = conf_scan

        self.sem = semcomedi.SEMComedi(**conf_sem)
        for child in self.sem.children.value:
            if child.name == CONFIG_SED["name"]:
                self.sed = child
            elif child.name == CONFIG_SCANNER["name"]:
                self.ebeam = child
                self.ebeam.magnification.value = ebeam_mag

    # @unittest.skip("skip")
    def test_find_overlay(self):
        """
        Test FindOverlay
        """
        self._prepare_hardware()
        f = align.FindOverlay((4, 4), 0.1, 10e-06, self.ebeam, self.ccd, self.sed, skew=True)

        t, (opt_md, sem_md) = f.result()
        self.assertEqual(len(t), 5)
        self.assertIn(model.MD_PIXEL_SIZE_COR, opt_md)
        self.assertIn(model.MD_SHEAR_COR, sem_md)

    # @unittest.skip("skip")
    def test_find_overlay_failure(self):
        """
        Test FindOverlay failure due to low maximum allowed difference
        """
        self._prepare_hardware()
        f = align.FindOverlay((4, 4), 1e-6, 1e-08, self.ebeam, self.ccd, self.sed, skew=True)

        with self.assertRaises(ValueError):
            f.result()

    # @unittest.skip("skip")
    def test_find_overlay_cancelled(self):
        """
        Test FindOverlay cancellation
        """
        self._prepare_hardware()
        f = align.FindOverlay((6, 6), 10e-06, 1e-07, self.ebeam, self.ccd, self.sed, skew=True)
        time.sleep(0.04)  # Cancel almost after the half grid is scanned

        f.cancel()
        self.assertTrue(f.cancelled())
        self.assertTrue(f.done())
        with self.assertRaises(futures.CancelledError):
            f.result()

    def test_bad_mag(self):
        self._prepare_hardware(ccd_img="overlay_4_4_test_1.tiff")

        # original mag was 35 (SEM pxs size = 432e-9 m), which was incorrect by x2
        for mag in (30, 40, 70, 1000):
            self.ebeam.magnification.value = mag

            f = align.FindOverlay((4, 4), 0.001, 10e-06, self.ebeam, self.ccd, self.sed, skew=True)

            t, (opt_md, sem_md) = f.result()
            self.assertEqual(len(t), 5)
            self.assertIn(model.MD_PIXEL_SIZE_COR, opt_md)
            self.assertIn(model.MD_SHEAR_COR, sem_md)

    def test_ratio_4_3(self):
        """
        Test FindOverlay when the SEM image ratio is not 1:1 (but 4:3)
        """
        ebeam_kwargs = {
            "max_res": [4096, 3072],
            "hfw_nomag": 0.177,
        }
        # SEM mag = 150x  ~ SEM pxs size = 288e-9 m
        self._prepare_hardware(ebeam_kwargs, 150, "overlay_4_4_test_2.tiff")

        f = align.FindOverlay((4, 4), 0.001, 10e-06, self.ebeam, self.ccd, self.sed, skew=True)

        t, (opt_md, sem_md) = f.result()
        self.assertEqual(len(t), 5)
        self.assertIn(model.MD_PIXEL_SIZE_COR, opt_md)
        self.assertIn(model.MD_SHEAR_COR, sem_md)

    def test_ratio_3_4(self):
        """
        Try also (unsual) 3:4
        """
        ebeam_kwargs = {
            "max_res": [3072, 4096],
            "hfw_nomag": 0.132,
        }
        # SEM mag = 150x  ~ SEM pxs size = 288e-9 m
        self._prepare_hardware(ebeam_kwargs, 150, "overlay_4_4_test_2.tiff")

        f = align.FindOverlay((4, 4), 0.001, 10e-06, self.ebeam, self.ccd, self.sed, skew=True)

        t, (opt_md, sem_md) = f.result()
        self.assertEqual(len(t), 5)
        self.assertIn(model.MD_PIXEL_SIZE_COR, opt_md)
        self.assertIn(model.MD_SHEAR_COR, sem_md)


if __name__ == '__main__':
    unittest.main()
#     suite = unittest.TestLoader().loadTestsFromTestCase(TestOverlay)
#     unittest.TextTestRunner(verbosity=2).run(suite)

