import logging
import unittest

import numpy
from odemis import model
from src.openapi_server.models.mega_field_meta_data import MegaFieldMetaData
from src.openapi_server.models.cell_parameters import CellParameters as CellAcqParameters
from src.openapi_server.models.field_meta_data import FieldMetaData
from datetime import datetime
import time

from odemis.driver.technolution import AcquisitionServer, ASMDataFlow, MirrorDescanner
from odemis.driver import technolution

URL = "http://localhost:8080/v1"
_METHOD_GET = 1
_METHOD_POST = 2


class TestAcquisitionServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        AcquisitionServer.ASMAPICall(URL + "/scan/finish_mega_field", _METHOD_POST, 204)
        CONFIG_SCANNER = {"name": "EBeamScanner", "role": "multibeam"}
        CONFIG_DESCANNER = {"name": "MirrorDescanner", "role": "galvo"}
        CONFIG_MPPC = {"name": "MPPC", "role": "mppc"}

        cls.ASM_manager = AcquisitionServer("ASM", "main", URL, children={"EBeamScanner"   : CONFIG_DESCANNER,
                                                                     "MirrorDescanner": CONFIG_DESCANNER,
                                                                     "MPPC"           : CONFIG_MPPC})
        for child in cls.ASM_manager.children.value:
            if child.name == CONFIG_MPPC["name"]:
                cls.MPPC = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.EBeamScanner = child
            elif child.name == CONFIG_DESCANNER["name"]:
                cls.MirrorDescanner = child

    @classmethod
    def tearDownClass(cls):
        pass

    def setUp(self):
        pass

    def tearDown(self):
        self.MPPC.terminate()
        time.sleep(0.2)

    def test_clock_frequency_call(self):
        #Test class method
        clock_freq = AcquisitionServer.ASMAPICall(URL + "/scan/clock_frequency", _METHOD_GET, 200)['frequency']
        self.assertIsInstance(clock_freq, int)

        #Test method via object
        clock_freq = self.ASM_manager.ASMAPICall(URL + "/scan/clock_frequency", _METHOD_GET, 200)['frequency']
        self.assertIsInstance(clock_freq, int)

    def test_finish_mega_field_call(self):
        expected_status_code = 204
        # Test class method
        status_code = AcquisitionServer.ASMAPICall(URL + "/scan/finish_mega_field", _METHOD_POST, expected_status_code)
        self.assertEqual(status_code, expected_status_code)

        #Test method via object
        status_code = self.ASM_manager.ASMAPICall(URL + "/scan/finish_mega_field", _METHOD_POST, expected_status_code)
        self.assertEqual(status_code, expected_status_code)

class TestEBeamScanner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        AcquisitionServer.ASMAPICall(URL + "/scan/finish_mega_field", _METHOD_POST, 204)
        CONFIG_SCANNER = {"name": "EBeamScanner", "role": "multibeam"}
        CONFIG_DESCANNER = {"name": "MirrorDescanner", "role": "galvo"}
        CONFIG_MPPC = {"name": "MPPC", "role": "mppc"}

        cls.ASM_manager = AcquisitionServer("ASM", "main", URL, children={"EBeamScanner"   : CONFIG_DESCANNER,
                                                                     "MirrorDescanner": CONFIG_DESCANNER,
                                                                     "MPPC"           : CONFIG_MPPC})
        for child in cls.ASM_manager.children.value:
            if child.name == CONFIG_MPPC["name"]:
                cls.MPPC = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.EBeamScanner = child
            elif child.name == CONFIG_DESCANNER["name"]:
                cls.MirrorDescanner = child


    @classmethod
    def tearDownClass(cls):
        pass

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_clock_VAs(self):
        self.assertEqual(
            self.EBeamScanner.clockPeriod.value,
            1 / AcquisitionServer.ASMAPICall(URL + "/scan/clock_frequency", _METHOD_GET, 200)['frequency'])

    def test__shape_VA(self):
        self.EBeamScanner._shape.value = (7000, 7000)
        self.assertEqual(self.EBeamScanner._shape.value, (7000, 7000))

    def test_resolution_VA(self):
        min_res = self.EBeamScanner.resolution.range[0][0]
        max_res = self.EBeamScanner.resolution.range[1][0]

        # Check if small resolution values are allowed
        self.EBeamScanner.resolution.value = (min_res + 5, min_res + 5)
        self.assertEqual(self.EBeamScanner.resolution.value, (min_res + 5, min_res + 5))

        #Check if big resolutions values are allowed
        self.EBeamScanner.resolution.value = (max_res - 200, max_res - 200)
        self.assertEqual(self.EBeamScanner.resolution.value,(max_res - 200, max_res - 200))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.EBeamScanner.resolution.value = (max_res + 10, max_res + 10)

        self.assertEqual(self.EBeamScanner.resolution.value, (max_res - 200, max_res - 200))

        with self.assertRaises(IndexError):
            self.EBeamScanner.resolution.value = (min_res  - 1, min_res  - 1)
        self.assertEqual(self.EBeamScanner.resolution.value, (max_res - 200, max_res - 200))

        # Check if it is allowed to have non-square resolutions
        self.EBeamScanner.resolution.value = (6000, 6500)
        self.assertEqual(self.EBeamScanner.resolution.value, (6000, 6500))

    def test_dwellTime_VA(self):
        min_dwellTime = self.EBeamScanner.dwellTime.range[0]
        max_dwellTime = self.EBeamScanner.dwellTime.range[1]

        self.EBeamScanner.dwellTime.value = 10 * min_dwellTime
        self.assertEqual(self.EBeamScanner.dwellTime.value, 10 * min_dwellTime)

        self.EBeamScanner.dwellTime.value = 1000 * min_dwellTime
        self.assertEqual(self.EBeamScanner.dwellTime.value, 1000 * min_dwellTime)

        self.EBeamScanner.dwellTime.value = min_dwellTime
        self.assertEqual(self.EBeamScanner.dwellTime.value, min_dwellTime)

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.EBeamScanner.dwellTime.value = 1.2 * max_dwellTime
        self.assertEqual(self.EBeamScanner.dwellTime.value, min_dwellTime)

        with self.assertRaises(IndexError):
            self.EBeamScanner.dwellTime.value = 0.5 * min_dwellTime
        self.assertEqual(self.EBeamScanner.dwellTime.value, min_dwellTime)

    def test_pixelSize(self):
        min_pixelSize = self.EBeamScanner.pixelSize.range[0][0]
        max_pixelSize = self.EBeamScanner.pixelSize.range[1][0]

        # Check if small pixelSize values are allowed
        self.EBeamScanner.pixelSize.value = (min_pixelSize + 5, min_pixelSize + 5)
        self.assertEqual(self.EBeamScanner.pixelSize.value, (min_pixelSize + 5, min_pixelSize + 5))

        #Check if big pixelSize values are allowed
        self.EBeamScanner.pixelSize.value = (max_pixelSize - 200, max_pixelSize - 200)
        self.assertEqual(self.EBeamScanner.pixelSize.value,(max_pixelSize - 200, max_pixelSize - 200))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.EBeamScanner.pixelSize.value = (max_pixelSize + 10, max_pixelSize + 10)
        self.assertEqual(self.EBeamScanner.pixelSize.value, (max_pixelSize - 200, max_pixelSize - 200))

        with self.assertRaises(IndexError):
            self.EBeamScanner.pixelSize.value = (min_pixelSize - 1, min_pixelSize - 1)
        self.assertEqual(self.EBeamScanner.pixelSize.value, (max_pixelSize - 200, max_pixelSize - 200))

        # Check if setter prevents settings of non-square pixelSize
        self.EBeamScanner.pixelSize.value = (600, 500)
        self.assertEqual(self.EBeamScanner.pixelSize.value, (600, 600))

    def test_rotation_VA(self):
        min_rotation = self.EBeamScanner.rotation.range[0]
        max_rotation = self.EBeamScanner.rotation.range[1]

        # Check if small rotation values are allowed
        self.EBeamScanner.rotation.value = 0.1 * max_rotation
        self.assertEqual(self.EBeamScanner.rotation.value, 0.1 * max_rotation)

        # Check if big rotation values are allowed
        self.EBeamScanner.rotation.value = 0.9 * max_rotation
        self.assertEqual(self.EBeamScanner.rotation.value, 0.9 * max_rotation)

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.EBeamScanner.rotation.value = 1.1 * max_rotation
        self.assertEqual(self.EBeamScanner.rotation.value, 0.9 * max_rotation)

        with self.assertRaises(IndexError):
            self.EBeamScanner.rotation.value = (-0.1 * max_rotation)
        self.assertEqual(self.EBeamScanner.rotation.value, 0.9 * max_rotation)

    def test_scanFlyback_VA(self):
        self.EBeamScanner.scanFlyback.value = 7
        self.assertEqual(self.EBeamScanner.scanFlyback.value, 7)

        self.EBeamScanner.scanFlyback.value = 20
        self.assertEqual(self.EBeamScanner.scanFlyback.value, 20)

    def test_scanOffset_VA(self):
        min_scanOffset = self.EBeamScanner.scanOffset.range[0][0]
        max_scanOffset = self.EBeamScanner.scanOffset.range[1][0]

        # Check if small scanOffset values are allowed
        self.EBeamScanner.scanOffset.value = (0.1 * max_scanOffset, 0.1 * max_scanOffset)
        self.assertEqual(self.EBeamScanner.scanOffset.value, (0.1 * max_scanOffset, 0.1 * max_scanOffset))

        # Check if big scanOffset values are allowed
        self.EBeamScanner.scanOffset.value = (0.9 * max_scanOffset, 0.9 * max_scanOffset)
        self.assertEqual(self.EBeamScanner.scanOffset.value, (0.9 * max_scanOffset, 0.9 * max_scanOffset))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.EBeamScanner.scanOffset.value = (1.2 * max_scanOffset, 1.2 * max_scanOffset)
        self.assertEqual(self.EBeamScanner.scanOffset.value, (0.9 * max_scanOffset, 0.9 * max_scanOffset))

        with self.assertRaises(IndexError):
            self.EBeamScanner.scanOffset.value = (1.2 * min_scanOffset, 1.2 * min_scanOffset)
        self.assertEqual(self.EBeamScanner.scanOffset.value, (0.9 * max_scanOffset, 0.9 * max_scanOffset))

    def test_scanGain_VA(self):
        min_scanGain = self.EBeamScanner.scanGain.range[0][0]
        max_scanGain = self.EBeamScanner.scanGain.range[1][0]

        # Check if small scanGain values are allowed
        self.EBeamScanner.scanGain.value = (0.1 * max_scanGain, 0.1 * max_scanGain)
        self.assertEqual(self.EBeamScanner.scanGain.value, (0.1 * max_scanGain, 0.1 * max_scanGain))

        # Check if big scanGain values are allowed
        self.EBeamScanner.scanGain.value = (0.9 * max_scanGain, 0.9 * max_scanGain)
        self.assertEqual(self.EBeamScanner.scanGain.value, (0.9 * max_scanGain, 0.9 * max_scanGain))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.EBeamScanner.scanGain.value = (1.2 * max_scanGain, 1.2 * max_scanGain)
        self.assertEqual(self.EBeamScanner.scanGain.value, (0.9 * max_scanGain, 0.9 * max_scanGain))

        with self.assertRaises(IndexError):
            self.EBeamScanner.scanGain.value = (1.2 * min_scanGain, 1.2 * min_scanGain)
        self.assertEqual(self.EBeamScanner.scanGain.value, (0.9 * max_scanGain, 0.9 * max_scanGain))

    def test_scanDelay_VA(self):
        min_scanDelay = self.EBeamScanner.scanDelay.range[0][0]
        max_scanDelay = self.EBeamScanner.scanDelay.range[1][0]

        # set _mppc.acqDelay > max_scanDelay to allow all options to be set
        self.EBeamScanner.parent._mppc.acqDelay.value = 0.9 * max_scanDelay

        # Check if small scanDelay values are allowed
        self.EBeamScanner.scanDelay.value = (int(0.1 * max_scanDelay), int(0.1 * max_scanDelay))
        self.assertEqual(self.EBeamScanner.scanDelay.value, (0.1 * max_scanDelay, 0.1 * max_scanDelay))

        # Check if big scanDelay values are allowed
        self.EBeamScanner.scanDelay.value = (int(0.9 * max_scanDelay), int(0.9 * max_scanDelay))
        self.assertEqual(self.EBeamScanner.scanDelay.value, (0.9 * max_scanDelay, 0.9 * max_scanDelay))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.EBeamScanner.scanDelay.value = (int(1.2 * max_scanDelay), int(1.2 * max_scanDelay))
        self.assertEqual(self.EBeamScanner.scanDelay.value, (0.9 * max_scanDelay, 0.9 * max_scanDelay))

        with self.assertRaises(IndexError):
            self.EBeamScanner.scanDelay.value = (int(-0.2 * max_scanDelay), int(-0.2 * max_scanDelay))
        self.assertEqual(self.EBeamScanner.scanDelay.value, (0.9 * max_scanDelay, 0.9 * max_scanDelay))

        # Check if setter prevents from setting negative values for self.EBeamScanner.parent._mppc.acqDelay.value - self.EBeamScanner.scanDelay.value[0]
        self.EBeamScanner.scanDelay.value = (min_scanDelay, min_scanDelay)
        self.EBeamScanner.parent._mppc.acqDelay.value = 0.5 * max_scanDelay
        self.EBeamScanner.scanDelay.value = (int(0.6 * max_scanDelay), int(0.6 * max_scanDelay))
        self.assertEqual(self.EBeamScanner.scanDelay.value, (min_scanDelay, min_scanDelay))

class TestMirrorDescanner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.MirrorDescanner = MirrorDescanner("MirrorDescanner", role=None, parent=None)

    def test_rotation_VA(self):
        min_rotation = self.MirrorDescanner.rotation.range[0]
        max_rotation = self.MirrorDescanner.rotation.range[1]

        # Check if small rotation values are allowed
        self.MirrorDescanner.rotation.value = 0.1 * max_rotation
        self.assertEqual(self.MirrorDescanner.rotation.value, 0.1 * max_rotation)

        # Check if big rotation values are allowed
        self.MirrorDescanner.rotation.value = 0.9 * max_rotation
        self.assertEqual(self.MirrorDescanner.rotation.value, 0.9 * max_rotation)

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.MirrorDescanner.rotation.value = 1.1 * max_rotation
        self.assertEqual(self.MirrorDescanner.rotation.value, 0.9 * max_rotation)

        with self.assertRaises(IndexError):
            self.MirrorDescanner.rotation.value = (-0.1 * max_rotation)
        self.assertEqual(self.MirrorDescanner.rotation.value, 0.9 * max_rotation)

    def test_scanOffset_VA(self):
        min_scanOffset = self.MirrorDescanner.scanOffset.range[0][0]
        max_scanOffset = self.MirrorDescanner.scanOffset.range[1][0]

        # Check if small scanOffset values are allowed
        self.MirrorDescanner.scanOffset.value = (0.1 * max_scanOffset, 0.1 * max_scanOffset)
        self.assertEqual(self.MirrorDescanner.scanOffset.value, (0.1 * max_scanOffset, 0.1 * max_scanOffset))

        # Check if big scanOffset values are allowed
        self.MirrorDescanner.scanOffset.value = (0.9 * max_scanOffset, 0.9 * max_scanOffset)
        self.assertEqual(self.MirrorDescanner.scanOffset.value, (0.9 * max_scanOffset, 0.9 * max_scanOffset))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.MirrorDescanner.scanOffset.value = (1.2 * max_scanOffset, 1.2 * max_scanOffset)
        self.assertEqual(self.MirrorDescanner.scanOffset.value, (0.9 * max_scanOffset, 0.9 * max_scanOffset))

        with self.assertRaises(IndexError):
            self.MirrorDescanner.scanOffset.value = (1.2 * min_scanOffset, 1.2 * min_scanOffset)
        self.assertEqual(self.MirrorDescanner.scanOffset.value, (0.9 * max_scanOffset, 0.9 * max_scanOffset))

    def test_scanGain_VA(self):
        min_scanGain = self.MirrorDescanner.scanGain.range[0][0]
        max_scanGain = self.MirrorDescanner.scanGain.range[1][0]

        # Check if small scanGain values are allowed
        self.MirrorDescanner.scanGain.value = (0.1 * max_scanGain, 0.1 * max_scanGain)
        self.assertEqual(self.MirrorDescanner.scanGain.value, (0.1 * max_scanGain, 0.1 * max_scanGain))

        # Check if big scanGain values are allowed
        self.MirrorDescanner.scanGain.value = (0.9 * max_scanGain, 0.9 * max_scanGain)
        self.assertEqual(self.MirrorDescanner.scanGain.value, (0.9 * max_scanGain, 0.9 * max_scanGain))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.MirrorDescanner.scanGain.value = (1.2 * max_scanGain, 1.2 * max_scanGain)
        self.assertEqual(self.MirrorDescanner.scanGain.value, (0.9 * max_scanGain, 0.9 * max_scanGain))

        with self.assertRaises(IndexError):
            self.MirrorDescanner.scanGain.value = (1.2 * min_scanGain, 1.2 * min_scanGain)
        self.assertEqual(self.MirrorDescanner.scanGain.value, (0.9 * max_scanGain, 0.9 * max_scanGain))


class TestMPPC(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        AcquisitionServer.ASMAPICall(URL + "/scan/finish_mega_field", _METHOD_POST, 204)
        CONFIG_SCANNER = {"name": "EBeamScanner", "role": "multibeam"}
        CONFIG_DESCANNER = {"name": "MirrorDescanner", "role": "galvo"}
        CONFIG_MPPC = {"name": "MPPC", "role": "mppc"}

        cls.ASM_manager = AcquisitionServer("ASM", "main", URL, children={"EBeamScanner"   : CONFIG_DESCANNER,
                                                                          "MirrorDescanner": CONFIG_DESCANNER,
                                                                          "MPPC"           : CONFIG_MPPC})
        for child in cls.ASM_manager.children.value:
            if child.name == CONFIG_MPPC["name"]:
                cls.MPPC = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.EBeamScanner = child
            elif child.name == CONFIG_DESCANNER["name"]:
                cls.MirrorDescanner = child

    @classmethod
    def tearDownClass(cls):
        pass

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test__shape_VA(self):
        self.MPPC._shape.value = (10, 10, 7000)
        self.assertEqual(self.MPPC._shape.value, (10, 10, 7000))

    def test_path_VA(self):
        self.MPPC.path.value = "testing_path"
        self.assertEqual(self.MPPC.path.value, "testing_path")

        #Check if forbidden chracters are refused and the path remains unchanged
        self.MPPC.path.value = "@testing_path"
        self.assertEqual(self.MPPC.path.value, "testing_path")

    def test_file_name_VA(self):
        self.MPPC.filename.value = "testing_file_name"
        self.assertEqual(self.MPPC.filename.value, "testing_file_name")
        self.MPPC.filename.value = "@testing_file_name"
        self.assertEqual(self.MPPC.filename.value, "testing_file_name")

    def test_externalStorageURL_VA(self):
        #TODO K.K. update test to new way of storing these values
        # Host
        self.MPPC.externalStorageURL_ftp.value = "testing_host"
        self.assertEqual(self.MPPC.externalStorageURL_path.value, "testing_host")

        # User
        self.MPPC.externalStorageURL_ftp.value = "testing_user"
        self.assertEqual(self.MPPC.externalStorageURL_ftp.value, "testing_user")

        # Password
        self.MPPC.externalStorageURL_ftp.value = "testing_password"
        self.assertEqual(self.MPPC.externalStorageURL_ftp.value, "testing_password")

    def test_acqDelay_VA(self):
        # set _mppc.acqDelay > max_scanDelay to allow all options to be set
        max_acqDelay = 1000.0

        # Check if big acqDelay values are allowed
        self.MPPC.acqDelay.value = 1.5 * max_acqDelay
        self.assertEqual(self.MPPC.acqDelay.value, 1.5 * max_acqDelay)

        # Lower EBeamScanner scanDelay value so that acqDelay can be changed freely
        self.EBeamScanner.scanDelay.value = (1, 1)
        self.assertEqual(self.EBeamScanner.scanDelay.value, (1, 1))

        # Check if small acqDelay values are allowed
        self.MPPC.acqDelay.value = 0.1 * max_acqDelay
        self.assertEqual(self.MPPC.acqDelay.value, 0.1 * max_acqDelay)

        # Change EBeamScanner scanDelay value so that acqDelay can be changed (first change acqDelay to allow this)
        self.MPPC.acqDelay.value = 2 * max_acqDelay
        self.EBeamScanner.scanDelay.value = (int(max_acqDelay), int(max_acqDelay))

        # Check if setter prevents from setting negative values for self.MPPC.acqDelay.value -
        self.MPPC.acqDelay.value = 0.5 * max_acqDelay
        self.assertEqual(self.MPPC.acqDelay.value, 2 * max_acqDelay)
        self.assertEqual(self.EBeamScanner.scanDelay.value, (max_acqDelay, max_acqDelay))

    def test_cellTranslation(self):
        self.MPPC.cellTranslation.value = [[[10 + j, 20 + j] for j in range(i,i+self.MPPC._shape.value[0])]
                                             for i in range(0,self.MPPC._shape.value[1]*self.MPPC._shape.value[0],self.MPPC._shape.value[0])]
        self.assertEqual(
                self.MPPC.cellTranslation.value,
                [[[10 + j, 20 + j] for j in range(i, i + self.MPPC._shape.value[0])]
                 for i in range(0, self.MPPC._shape.value[1] * self.MPPC._shape.value[0], self.MPPC._shape.value[0])]
        )

    def test_celldarkOffset(self):
        self.MPPC.celldarkOffset.value = [[j for j in range(i, i+self.MPPC._shape.value[0])] for i in range(0,
                                            self.MPPC._shape.value[1]*self.MPPC._shape.value[0], self.MPPC._shape.value[0])]
        self.assertEqual(
                self.MPPC.celldarkOffset.value,
                [[j for j in range(i, i + self.MPPC._shape.value[0])] for i in range(0,self.MPPC._shape.value[1] *
                                        self.MPPC._shape.value[0],self.MPPC._shape.value[0])]
                )

    def test_celldigitalGain(self):
        self.MPPC.cellTranslation.value = [[j for j in range(i, i+self.MPPC._shape.value[0])] for i in range(0,
                                            self.MPPC._shape.value[1]*self.MPPC._shape.value[0], self.MPPC._shape.value[0])]
        self.assertEqual(
                self.MPPC.cellTranslation.value,
                [[j for j in range(i, i + self.MPPC._shape.value[0])] for i in range(0,self.MPPC._shape.value[1] *
                                        self.MPPC._shape.value[0],self.MPPC._shape.value[0])]
                )

    def test_cellCompleteResolution(self):
        min_res = self.MPPC.cellCompleteResolution.range[0][0]
        max_res = self.MPPC.cellCompleteResolution.range[1][0]

        # Check if small resolution values are allowed
        self.MPPC.cellCompleteResolution.value = (min_res + 5, min_res + 5)
        self.assertEqual(self.MPPC.cellCompleteResolution.value, (min_res + 5, min_res + 5))

        #Check if big resolutions values are allowed
        self.MPPC.cellCompleteResolution.value = (max_res - 200, max_res - 200)
        self.assertEqual(self.MPPC.cellCompleteResolution.value, (max_res - 200, max_res - 200))

        # Check if VA refuses to set limits outside allowed range
        with self.assertRaises(IndexError):
            self.MPPC.cellCompleteResolution.value = (max_res + 10, max_res + 10)

        self.assertEqual(self.MPPC.cellCompleteResolution.value, (max_res - 200, max_res - 200))

        with self.assertRaises(IndexError):
            self.MPPC.cellCompleteResolution.value = (min_res - 1, min_res - 1)
        self.assertEqual(self.MPPC.cellCompleteResolution.value, (max_res - 200, max_res - 200))

        # Check if setter prevents settings of non-square resolutions
        self.MPPC.cellCompleteResolution.value = (int(0.2 * max_res), int(0.5 * max_res))
        self.assertEqual(self.MPPC.cellCompleteResolution.value, (int(0.2 * max_res), int(0.5 * max_res)))


class Test_ASM_HwComponent(unittest.TestCase):
    """
    Test method to test the wrapper without using the dataflow by directly acting on the HwComponent
    """
    @classmethod
    def setUpClass(cls):
        AcquisitionServer.ASMAPICall(URL + "/scan/finish_mega_field", _METHOD_POST, 204)
        CONFIG_SCANNER = {"name": "EBeamScanner", "role": "multibeam"}
        CONFIG_DESCANNER = {"name": "MirrorDescanner", "role": "galvo"}
        CONFIG_MPPC = {"name": "MPPC", "role": "mppc"}

        cls.ASM_manager = AcquisitionServer("ASM", "main", URL, children={"EBeamScanner"   : CONFIG_DESCANNER,
                                                                     "MirrorDescanner": CONFIG_DESCANNER,
                                                                     "MPPC"           : CONFIG_MPPC})
        for child in cls.ASM_manager.children.value:
            if child.name == CONFIG_MPPC["name"]:
                cls.MPPC = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.EBeamScanner = child
            elif child.name == CONFIG_DESCANNER["name"]:
                cls.MirrorDescanner = child

    @classmethod
    def tearDownClass(cls):
        pass

    def setUp(self):
        pass

    def tearDown(self):
        self.MPPC.terminate()
        time.sleep(0.2)

    def test_connection_and_clock_frequency(self):
        clock_freq = AcquisitionServer.ASMAPICall(URL + "/scan/clock_frequency", _METHOD_GET, 200)['frequency']
        self.assertEqual(self.EBeamScanner.clockPeriod.value, 1/clock_freq)

    def test_acquire_mega_field(self):
        self.MPPC.start_acquisition()

        for x in range(4):
            for y in range(4):
                self.MPPC.get_next_field((x, y))
                self.assertEqual(self.MPPC._field_data,
                                 FieldMetaData(x * self.EBeamScanner._shape.value[0],
                                               y * self.EBeamScanner._shape.value[1])
                                 )

        self.MPPC.stop_acquisition()

    def test_acquire_single_field(self):
        # Test one single field at location 0,0
        self.MPPC.acquire_single_field(field_num=(0, 0))
        self.assertEqual(self.MPPC._field_data,
                         FieldMetaData(0 * self.EBeamScanner._shape.value[0],
                                       0 * self.EBeamScanner._shape.value[1])
                         )

        # Test another single field at location 3,3
        x = y = 3
        image = self.MPPC.acquire_single_field(field_num=(x, y))
        self.assertEqual(self.MPPC._field_data,
                         FieldMetaData(x * self.EBeamScanner._shape.value[0],
                                       y * self.EBeamScanner._shape.value[1])
                         )
        self.assertIsInstance(image, model.DataArray)


class Test_ASMDataFlow(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        CONFIG_SCANNER = {"name": "EBeamScanner", "role": "multibeam"}
        CONFIG_DESCANNER = {"name": "MirrorDescanner", "role": "galvo"}
        CONFIG_MPPC = {"name": "MPPC", "role": "mppc"}

        cls.ASM_manager = AcquisitionServer("ASM", "main", URL, children={"EBeamScanner"   : CONFIG_DESCANNER,
                                                                     "MirrorDescanner": CONFIG_DESCANNER,
                                                                     "MPPC"           : CONFIG_MPPC})
        for child in cls.ASM_manager.children.value:
            if child.name == CONFIG_MPPC["name"]:
                cls.MPPC = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.EBeamScanner = child
            elif child.name == CONFIG_DESCANNER["name"]:
                cls.MirrorDescanner = child

    @classmethod
    def tearDownClass(cls):
        cls.MPPC.terminate()
        time.sleep(0.2)

    def setUp(self):
        AcquisitionServer.ASMAPICall(URL + "/scan/finish_mega_field", _METHOD_POST, 204)
        pass

    def tearDown(self):
        pass

    def test_connection_and_clock_frequency(self):
        clock_freq = AcquisitionServer.ASMAPICall(URL + "/scan/clock_frequency", _METHOD_GET, 200)['frequency']
        self.assertIsInstance(self.EBeamScanner.clockPeriod.value, float)
        self.assertEqual(self.EBeamScanner.clockPeriod.value, 1/clock_freq)

    def test_subscribe_mega_field(self):
        #TODO K.K. test fails when entire file is runned, does pass if runned seperatly
        field_images = (3, 4)
        global counter
        counter = 0

        def image_received(dataflow, image):
            global counter
            counter += 1
            print("image received")

        dataflow = self.MPPC.dataFlow
        dataflow.subscribe(image_received)

        for x in range(field_images[0]):
            for y in range(field_images[1]):
                dataflow.next((x, y))
                self.assertEqual(self.MPPC._field_data,
                                 FieldMetaData(x * self.EBeamScanner._shape.value[0],
                                               y * self.EBeamScanner._shape.value[1])
                                 )

        dataflow.stop_generate()
        time.sleep(5)
        self.assertEqual(field_images[0] * field_images[1], counter)
        del counter

    def test_get_field(self):
        Dataflow = ASMDataFlow(self.ASM_manager, self.MPPC.start_acquisition, self.MPPC.get_next_field,
                               self.MPPC.stop_acquisition,
                               self.MPPC.acquire_single_field)
        image = Dataflow.get()
        self.assertIsInstance(image, model.DataArray)

    def test_subscribe_get_field(self):
        def image_received(dataflow, image):
            print("image received")

        Dataflow = self.MPPC.dataFlow

        image = Dataflow.get()
        self.assertIsInstance(image, model.DataArray)

        Dataflow.subscribe(image_received)
        with self.assertRaises(Exception):
            #Check that image is not received is already on subscriber is present
            image = Dataflow.get()

    def test_terminate(self):
        #TODO K.K. test fails when entire file is runned, does pass if runned seperatly
        field_images = (3, 4)
        termination_point = (1, 3)
        global counter
        counter = 0

        def image_received(dataflow, image):
            global counter
            counter += 1
            print("image received")

        dataflow = self.MPPC.dataFlow
        dataflow.subscribe(image_received)

        for x in range(field_images[0]):
            for y in range(field_images[1]):
                if x == termination_point[0] and y == termination_point[1]:
                    print("Send terminating command")
                    self.MPPC.terminate()
                    time.sleep(0.5)
                    self.assertEqual(self.MPPC.acq_queue.qsize(), 0,
                                     "queue was not cleared properly and is not empty")
                    time.sleep(0.5)

                dataflow.next((x, y))
                self.assertEqual(self.MPPC._field_data,
                                 FieldMetaData(x * self.EBeamScanner._shape.value[0],
                                               y * self.EBeamScanner._shape.value[1])
                                 )
                time.sleep(0.5)

        self.assertEqual(self.MPPC._acquisition_in_progress, None)
        self.assertEqual((termination_point[0] * field_images[1]) + termination_point[1], counter)
        del counter

if __name__ == '__main__':
    unittest.main()
