import logging
import math
import os
import time
import unittest

from odemis.model import NotSettableError
from odemis.driver import xt_toolkit_client, xt_client

logging.basicConfig(level=logging.INFO)

TEST_NOHW = (os.environ.get("TEST_NOHW", 0) != 0)

# arguments used for the creation of basic components
CONFIG_SCANNER = {"name": "scanner", "role": "ebeam", "hfw_nomag": 1}
CONFIG_STAGE = {"name": "stage", "role": "stage",
                "inverted": ["x"],
                }
CONFIG_FOCUS = {"name": "focuser", "role": "ebeam-focus"}
CONFIG_DETECTOR = {"name": "detector", "role": "se-detector", "channel_name": "electron1"}
CONFIG_SEM = {"name": "sem", "role": "sem", "address": "PYRO:Microscope@192.168.31.138:4242",
              "children": {"scanner": CONFIG_SCANNER,
                           "focus": CONFIG_FOCUS,
                           "stage": CONFIG_STAGE,
                           "detector": CONFIG_DETECTOR,
                           }
              }


class TestMicroscope(unittest.TestCase):
    """
    Test communication with the server using the Microscope client class.
    """

    @classmethod
    def setUpClass(cls):

        cls.microscope = xt_toolkit_client.SEM(**CONFIG_SEM)

        for child in cls.microscope.children.value:
            if child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child
            elif child.name == CONFIG_FOCUS["name"]:
                cls.efocus = child
            elif child.name == CONFIG_STAGE["name"]:
                cls.stage = child
            elif child.name == CONFIG_DETECTOR["name"]:
                cls.detector = child

    @classmethod
    def tearDownClass(cls):
        cls.detector.terminate()

    def setUp(self):
        if TEST_NOHW:
            self.skipTest("No hardware available.")
        if self.microscope.get_vacuum_state() != 'vacuum':
            self.skipTest("Chamber needs to be in vacuum, please pump.")
        self.xt_type = "xttoolkit" if "xttoolkit" in self.microscope.swVersion.lower() else "xtlib"

    def test_type_scanner_child(self):
        # Check if the scanner class is of the correct type
        self.assertIsInstance(self.scanner, xt_toolkit_client.Scanner)

        # Check if the xt_client scanner class is correctly overwritten and does not exist as a child anymore.
        for child in self.microscope.children.value:
            self.assertIsNot(type(child), xt_client.Scanner)


class TestScanner(unittest.TestCase):
    """
    Test the Scanner class, its methods, and the VA's it has.
    """

    @classmethod
    def setUpClass(cls):

        cls.microscope = xt_toolkit_client.SEM(**CONFIG_SEM)

        for child in cls.microscope.children.value:
            if child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child
            elif child.name == CONFIG_FOCUS["name"]:
                cls.efocus = child
            elif child.name == CONFIG_STAGE["name"]:
                cls.stage = child

    def setUp(self):
        if TEST_NOHW:
            self.skipTest("No hardware available.")
        if self.microscope.get_vacuum_state() != 'vacuum':
            self.skipTest("Chamber needs to be in vacuum, please pump.")
        self.xt_type = "xttoolkit" if "xttoolkit" in self.microscope.swVersion.lower() else "xtlib"

    def test_pitch_VA(self):
        # TODO change VA and method names with pitch to delta pitch
        current_value = self.scanner.pitch.value
        # Test if directly changing the value via the VA works. Not always will the entirety of the range be
        # allowed. Negative delta pitch is limited by the voltage it can apply. Therefore the max range and the 0
        # value is tested.
        for test_pitch in (0.0, self.scanner.pitch.range[1]):
            self.scanner.pitch.value = test_pitch
            self.assertEqual(test_pitch, self.scanner.pitch.value)
            self.assertEqual(test_pitch, self.microscope.get_pitch() * 1e-6)

        # Test if errors are produced when a value outside of the range is set.
        with self.assertRaises(IndexError):
            self.scanner.pitch.value = 1.2 * self.scanner.pitch.range[1]
        self.assertEqual(test_pitch, self.microscope.get_pitch() * 1e-6)

        # Test if the value is automatically updated when the value is not changed via the VA
        self.microscope.set_pitch(0)
        time.sleep(6)
        self.assertEqual(0, self.scanner.pitch.value)

        self.scanner.pitch.value = current_value

    def test_beam_stigmator_VA(self):
        current_value = self.scanner.beamStigmator.value
        # Test if directly changing it via the VA works
        for test_stigmator_value in self.scanner.beamStigmator.range:
            self.scanner.beamStigmator.value = test_stigmator_value
            self.assertEqual(test_stigmator_value, tuple(self.microscope.get_primary_stigmator()))

        # Test if errors are produced when a value outside of the range is set.
        with self.assertRaises(IndexError):
            self.scanner.beamStigmator.value =  tuple(1.2 * i for i in self.scanner.beamStigmator.range[1])
        self.assertEqual(test_stigmator_value, tuple(self.microscope.get_primary_stigmator()))

        # Test if the value is automatically updated when the value is not changed via the VA
        self.microscope.set_primary_stigmator(0, 0)
        time.sleep(6)
        self.assertEqual((0, 0), tuple(self.scanner.beamStigmator.value))

        self.scanner.beamStigmator.value = current_value

    def test_pattern_stigmator_VA(self):
        current_value = self.scanner.patternStigmator.value

        # Test if directly changing it via the VA works
        for test_stigmator_value in self.scanner.patternStigmator.range:
            self.scanner.patternStigmator.value = test_stigmator_value
            self.assertEqual(test_stigmator_value, tuple(self.microscope.get_secondary_stigmator()))

        # Test if errors are produced when a value outside of the range is set.
        with self.assertRaises(IndexError):
            self.scanner.patternStigmator.value = tuple(1.2 * i for i in self.scanner.patternStigmator.range[1])
        self.assertEqual(test_stigmator_value, tuple(self.microscope.get_secondary_stigmator()))

        # Test if the value is automatically updated when the value is not changed via the VA
        self.microscope.set_secondary_stigmator(0, 0)
        time.sleep(6)
        self.assertEqual((0, 0), tuple(self.scanner.patternStigmator.value))

        self.scanner.patternStigmator.value = current_value

    def test_beam_shift_transformation_matrix_VA(self):
        beamShiftTransformationMatrix = self.scanner.beamShiftTransformationMatrix
        self.assertIsInstance(beamShiftTransformationMatrix.value, list)
        self.assertEqual(len(beamShiftTransformationMatrix.value), 4)
        for row_transformation_matrix in beamShiftTransformationMatrix.value:
            self.assertIsInstance(row_transformation_matrix, list)
            self.assertEqual(len(row_transformation_matrix), 2)
            self.assertIsInstance(row_transformation_matrix[0], float)
            self.assertIsInstance(row_transformation_matrix[1], float)

        # Check if VA is read only
        with self.assertRaises(NotSettableError):
            self.scanner.beamShiftTransformationMatrix.value = beamShiftTransformationMatrix

    def test_multiprobe_rotation_VA(self):
        mpp_rotation = self.scanner.multiprobeRotation.value
        self.assertIsInstance(mpp_rotation, float)
        # Currently the range of the value can be quite big due to different designs for microscopes.
        self.assertGreaterEqual(mpp_rotation,  - math.radians(90))
        self.assertLessEqual(mpp_rotation, math.radians(90))

        # Check if VA is read only
        with self.assertRaises(NotSettableError):
            self.scanner.multiprobeRotation.value = mpp_rotation

    def test_aperture_index_VA(self):
        current_value = self.scanner.apertureIndex.value

        # Test if directly changing it via the VA works
        for test_aperture_index in self.scanner.apertureIndex.range:
            self.scanner.apertureIndex.value = test_aperture_index
            self.assertEqual(test_aperture_index, self.microscope.get_aperture_index())

        # Test if errors are produced when a value outside of the range is set.
        with self.assertRaises(IndexError):
            self.scanner.apertureIndex.value = 1.2 * self.scanner.apertureIndex.range[1]
        self.assertEqual(test_aperture_index, self.microscope.get_aperture_index())

        # Test if the value is automatically updated when the value is not changed via the VA
        self.microscope.set_aperture_index(0)
        time.sleep(6)
        self.assertEqual(0, self.scanner.apertureIndex.value)

        self.scanner.apertureIndex.value = current_value

    def test_beamlet_index_VA(self):
        current_value = self.scanner.beamletIndex.value

        # Test if directly changing it via the VA works
        for test_beamlet_index in self.scanner.beamletIndex.range:
            self.scanner.beamletIndex.value = test_beamlet_index
            self.assertEqual(test_beamlet_index, self.microscope.get_beamlet_index())

        # Test if errors are produced when a value outside of the range is set.
        with self.assertRaises(IndexError):
            self.scanner.beamletIndex.value = tuple(int(2 * i) for i in self.scanner.beamletIndex.range[1])
        self.assertEqual(test_beamlet_index, self.microscope.get_beamlet_index())

        # Test if the value is automatically updated when the value is not changed via the VA
        self.microscope.set_beamlet_index(self.scanner.beamletIndex.range[0])
        time.sleep(6)
        self.assertEqual(self.scanner.beamletIndex.range[0], self.scanner.beamletIndex.value)

        self.scanner.beamletIndex.value = current_value
        self.assertEqual(current_value, self.microscope.get_beamlet_index())
        self.assertEqual(current_value, self.scanner.beamletIndex.value)

    def test_multiprobe_mode_VA(self):
        current_beam_mode = self.scanner.multiBeamMode.value
        current_aperture_index = self.scanner.apertureIndex.value
        current_beamlet_index = self.scanner.beamletIndex.value

        for multi_beam_boolean in [True, False, True, False, True]:
            self.scanner.multiBeamMode.value = multi_beam_boolean
            time.sleep(6)
            self.assertEqual(self.scanner.multiBeamMode.value, multi_beam_boolean)
            self.assertEqual(self.microscope.get_use_case(), 'MultiBeamTile' if multi_beam_boolean else 'SingleBeamlet')
            # Check if aperture and beamlet index do not change while switching beam modes.
            self.assertEqual(self.microscope.get_aperture_index(), current_aperture_index)
            self.assertEqual(self.microscope.get_beamlet_index(), current_beamlet_index)

        # Test if the value is automatically updated when the value is not changed via the VA
        self.microscope.set_use_case('SingleBeamlet')
        time.sleep(6)
        self.assertEqual(False, self.scanner.multiBeamMode.value)

        self.scanner.multiBeamMode.value = current_beam_mode


if __name__ == '__main__':
    unittest.main()
