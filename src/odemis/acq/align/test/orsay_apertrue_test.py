import copy
import os
import unittest
from unittest.mock import MagicMock

from odemis.acq.align.orsay_aperture import HighLevelAperture, NoApertureError, APERTURE_ALREADY_WORN_OUT, \
    NON_MATCHING_APERTURE_SIZE
from odemis.driver import orsay
from odemis.driver.orsay import recursive_getattr
from odemis.driver.test.orsay_test import CONFIG_ORSAY, NO_SERVER_MSG, CONFIG_FIBAPERTURE, CONFIG_SCANNER, \
    CONFIG_FIBBEAM

TEST_NOHW = os.environ.get("TEST_NOHW", "0")  # Default to Hw testing
if TEST_NOHW == "sim":
    # For simulation, make sure to have the Orsay Physics Control Server installed and running.
    CONFIG_ORSAY["host"] = "192.168.56.101"  # IP address of the simulated Orsay Physics Control Server
elif TEST_NOHW == "0":
    TEST_NOHW = False
elif TEST_NOHW == "1":
    TEST_NOHW = True
else:
    raise ValueError("Unknown value of environment variable TEST_NOHW=%s" % TEST_NOHW)


class TestHighLevelAperture(unittest.TestCase):
    """
    Tests for the Focused Ion Beam (FIB) Scanner
    """

    oserver = None

    @classmethod
    def setUpClass(cls):
        """
        Setup the Orsay client
        """
        if TEST_NOHW == True:
            raise unittest.SkipTest(NO_SERVER_MSG)

        cls.oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        cls.datamodel = cls.oserver.datamodel
        for child in cls.oserver.children.value:
            if child.name == CONFIG_FIBAPERTURE["name"]:
                cls.low_lvl_aperture = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child
            if child.name == CONFIG_FIBBEAM["name"]:
                cls.fibbeam = child

        cls._lastApertureNmbr = int(cls.oserver.datamodel.HybridAperture.SelectedDiaph.Max)

        cls.high_lvl_aperture = HighLevelAperture(cls.low_lvl_aperture, cls.scanner, cls.fibbeam )

    @classmethod
    def tearDownClass(cls):
        """
        Terminate the Orsay client
        """
        cls.oserver.terminate()
        
    def setUp(self):
        """
        Saves the initial settings which can be reset after every running every test case
        """
        self.high_lvl_aperture._updateAvailableApertures()
        self.init_aptr_data = copy.deepcopy(self.high_lvl_aperture._high_level_aptr_data)
        self.init_available_apertures = copy.deepcopy(self.high_lvl_aperture.available_apertures)
        self.init_replacement_needed = self.high_lvl_aperture.replacement_needed.value

    def tearDown(self):
        """
        Resetting the settings to the initial settings
        """
        self.high_lvl_aperture._high_level_aptr_data = self.init_aptr_data
        self.high_lvl_aperture.available_apertures = self.init_available_apertures
        self.high_lvl_aperture.replacement_needed.value = self.init_replacement_needed
        self.high_lvl_aperture._updateAvailableApertures()

    def test_listenerProbeCurrent(self):
        """
        Test if listenerProbeCurrent correctly updates the worn out state when the aperture status is found to be
        bad/good using MagicMock.
        """
        original_getCurrentApertureStatus = self.high_lvl_aperture.getCurrentApertureStatus

        # Mock getCurrentApertureStatus to return False so that the aperture status is bad.
        self.high_lvl_aperture.getCurrentApertureStatus = MagicMock(return_value=False)
        # Order the possible_probe_currents such that None is the last value.
        possible_probe_currents = sorted(pc for pc in self.scanner.probeCurrent.choices if pc is not None)
        self.scanner.probeCurrent.value = possible_probe_currents[0]
        init_aperture = self.low_lvl_aperture.selectedAperture.value
        # Call all listeners also if the previous value was the same as the one set
        self.scanner.probeCurrent.notify(possible_probe_currents[0])
        self.assertTrue(self.high_lvl_aperture._high_level_aptr_data[init_aperture]["Worn-out"])


        # Reset replacement worn-out rate
        self.high_lvl_aperture._high_level_aptr_data[init_aperture]["Worn-out"] = False

        # Mock getCurrentApertureStatus to return False so that the aperture status is good.
        self.high_lvl_aperture.getCurrentApertureStatus = MagicMock(return_value=True)
        possible_probe_currents = list(self.scanner.probeCurrent.choices)
        self.scanner.probeCurrent.value = possible_probe_currents[0]
        # Call all listeners also if the previous value was the same as the one set
        self.scanner.probeCurrent.notify(possible_probe_currents[0])
        self.assertFalse(self.high_lvl_aperture._high_level_aptr_data[init_aperture]["Worn-out"])

        # Reset the original method to replace the mock method.
        self.high_lvl_aperture.getCurrentApertureStatus = original_getCurrentApertureStatus

    def test_getCurrentApertureStatus(self):
        """
        Test if the getCurrentApertureStatus returns a correct aperture state when the measurements change using MagicMock.
        """

        original_performFaradayCupMeasurement = self.scanner._performFaradayCupMeasurement

        expected_probe_current = self.scanner.probeCurrent.value
        if not expected_probe_current:  # If the expected_probe_current is none set it to another value
            self.scanner.probeCurrent.value = sorted(pc for pc in self.scanner.probeCurrent.choices if pc is not None)[1]
            expected_probe_current = self.scanner.probeCurrent.value

        # Return 3 times + 20 the expected probe so that the aperture status is bad/return False (+20 allows it to also work for zero)
        self.scanner._performFaradayCupMeasurement = MagicMock(return_value=3*expected_probe_current+20)

        aperture_status = self.high_lvl_aperture.getCurrentApertureStatus(allowed_deviation=0.2)
        self.assertFalse(aperture_status)

        # Return the expected probe so that the aperture status is good/return True
        self.scanner._performFaradayCupMeasurement = MagicMock(return_value=expected_probe_current)
        aperture_status = self.high_lvl_aperture.getCurrentApertureStatus(allowed_deviation=0.2)
        self.assertTrue(aperture_status)

        self.scanner._performFaradayCupMeasurement = original_performFaradayCupMeasurement

    def test_setNewAperture(self):
        probe_current = self.scanner.probeCurrent.value
        if not probe_current:  # If the probe_current is none set it to another value
            self.scanner.probeCurrent.value = sorted(pc for pc in self.scanner.probeCurrent.choices if pc is not None)[1]
            probe_current = self.scanner.probeCurrent.value

        # Return 3 times + 20 the expected probe current and check if that value is updated in the high level aperture dict.
        self.scanner._performFaradayCupMeasurement = MagicMock(return_value=3*probe_current+20)

        current_aperture_size = self.low_lvl_aperture.sizeSelectedAperture.value
        new_aperture = self.high_lvl_aperture._suggestReplacementAperture(current_aperture_size)
        self.high_lvl_aperture.setNewAperture(probe_current, new_aperture)
        aperture_current_data = self.high_lvl_aperture._high_level_aptr_data[new_aperture]
        self.assertEqual(aperture_current_data["Nominal probe-current"], 3*probe_current+20)
        self.assertEqual(aperture_current_data["Last measured current"], 3*probe_current+20)

        # Return the expected probe current and check if that value is updated in the high level aperture dict.
        self.scanner._performFaradayCupMeasurement = MagicMock(return_value=probe_current)

        self.high_lvl_aperture.setNewAperture(probe_current, new_aperture)
        aperture_current_data = self.high_lvl_aperture._high_level_aptr_data[new_aperture]
        self.assertEqual(aperture_current_data["Nominal probe-current"], probe_current)
        self.assertEqual(aperture_current_data["Last measured current"], probe_current)

    def test_validateNewAperture(self):
        probe_current = self.scanner.probeCurrent.value
        if not probe_current:  # If the probe_current is none set it to another value
            self.scanner.probeCurrent.value = sorted(pc for pc in self.scanner.probeCurrent.choices if pc is not None)[1]
            probe_current = self.scanner.probeCurrent.value

        # Check default functioning of the method
        current_aperture_size = self.low_lvl_aperture.sizeSelectedAperture.value
        new_aperture = self.high_lvl_aperture._suggestReplacementAperture(current_aperture_size)
        aperture_validation = self.high_lvl_aperture.validateNewAperture(probe_current, new_aperture)
        self.assertIsNone(aperture_validation)

        # Check if an already worn out aperture response is given if an aperture is owrn out
        self.high_lvl_aperture._high_level_aptr_data[new_aperture]["Worn-out"] = True
        aperture_validation = self.high_lvl_aperture.validateNewAperture(probe_current, new_aperture)
        self.assertEqual(aperture_validation, APERTURE_ALREADY_WORN_OUT)
        self.high_lvl_aperture._high_level_aptr_data[new_aperture]["Worn-out"] = False

        current_aperture_size = self.low_lvl_aperture.sizeSelectedAperture.value
        for aperture_number, aperture in self.low_lvl_aperture._apertureDict.items():
            if aperture["Size"] != current_aperture_size:
                new_aperture = aperture_number
                break
        # Check if a non matching aperture size return is given if the aperture size doesn't match
        aperture_validation = self.high_lvl_aperture.validateNewAperture(probe_current, new_aperture)
        self.assertEqual(aperture_validation, NON_MATCHING_APERTURE_SIZE)

    def test_updateHighLevelApertureData(self):
        self.high_lvl_aperture._high_level_aptr_data[0]["Worn-out"] = "Random string to check stuff"  # Resting this value is done in the teardown
        self.assertEqual(self.high_lvl_aperture.getCombinedApertureData()[0]["Worn-out"], "Random string to check stuff")

    def test_setApertureWornOut(self):
        self.high_lvl_aperture._high_level_aptr_data[0]["Worn-out"] = False  # Resting this value is done in the teardown
        self.high_lvl_aperture._setApertureWornOut(0)
        self.assertTrue(self.high_lvl_aperture._high_level_aptr_data[0]["Worn-out"])
        self.assertTrue(self.high_lvl_aperture.getCombinedApertureData()[0]["Worn-out"])

    def test_getCombinedApertureData(self):
        complete_data = self.high_lvl_aperture.getCombinedApertureData()
        self.assertEqual(len(complete_data), self._lastApertureNmbr)
        for aprtr_nmbr, data in complete_data.items():
            expected_keys = {"Lifetime", "Size", "Position", "Nominal probe-current", "Last measured current", "Worn-out"}
            self.assertEqual(data.keys(), expected_keys)
            self.assertEqual(data["Position"].keys(), {"x", "y"})

    def test_updateAvailableApertures(self):
        if self.high_lvl_aperture.checkAperturePlateState():
            self.skipTest("There are to few apertures available to complete this test. This test is skipped.")
        aperture_size = list(self.high_lvl_aperture.available_apertures.keys())[0]  # Any aperture size will do

        # Test what happens all apertures of a certain size are worn-out
        self.high_lvl_aperture.replacement_needed.value = False  # Make sure replacement_needed is set to False
        # Set all apertures of one size to worn out
        for aperture in self.high_lvl_aperture.available_apertures[aperture_size]:
            self.high_lvl_aperture._high_level_aptr_data[aperture]["Worn-out"] = True
        self.high_lvl_aperture._updateAvailableApertures()

        self.assertTrue(self.high_lvl_aperture.replacement_needed.value)
        self.assertEqual(0, len(self.high_lvl_aperture.available_apertures[aperture_size]))

        self.high_lvl_aperture.replacement_needed.value = False  # Set replacement_needed back to False

        # Test with only one aperture of a size not worn out
        for aperture in self.high_lvl_aperture.available_apertures[aperture_size]:
            self.high_lvl_aperture._high_level_aptr_data[aperture]["Worn-out"] = True
        else:
            self.high_lvl_aperture._high_level_aptr_data[aperture]["Worn-out"] = False # Only set the last aperture to be not worn out
        self.high_lvl_aperture._updateAvailableApertures()

        self.assertTrue(self.high_lvl_aperture.replacement_needed.value)
        self.assertEqual(1, len(self.high_lvl_aperture.available_apertures[aperture_size]))

    def test_availableApertures(self):
        if self.high_lvl_aperture.checkAperturePlateState():
            self.skipTest("There are to few apertures available to complete this test. This test is skipped.")

        for aperture_size, aperture_numbers in self.high_lvl_aperture.available_apertures.items():
            self.assertIsInstance(aperture_size, float)
            self.assertIsInstance(aperture_numbers, set)
            # Test for each size if the number match the correct size
            for aprtr_nmbr in aperture_numbers:
                aprtr_size = float(recursive_getattr(self.oserver.datamodel.HybridAperture, f"Aperture{aprtr_nmbr}").Size.Actual)
                self.assertEqual(aperture_size, aprtr_size)

    def test_suggestReplacementAperture(self):
        if self.high_lvl_aperture.checkAperturePlateState():
            self.skipTest("There are to few apertures available to complete this test. This test is skipped.")

        aperture_size = list(self.high_lvl_aperture.available_apertures)[0]  # Any aperture size will do
        if len(self.high_lvl_aperture.available_apertures[aperture_size]) >= 1:
            suggested_aperture = self.high_lvl_aperture._suggestReplacementAperture(aperture_size)
            self.assertIsInstance(suggested_aperture, int)

        self.high_lvl_aperture.available_apertures[aperture_size] = {}
        with self.assertRaises(NoApertureError):
            self.high_lvl_aperture._suggestReplacementAperture(aperture_size)

    def test_checkAperturePlate(self):
        if not all(len(avail_aprtrs) > 1 for avail_aprtrs in self.high_lvl_aperture.available_apertures.values()):
            self.skipTest("There are to few apertures available to complete this test. This test is skipped.")

        aperture_size = list(self.high_lvl_aperture.available_apertures)[0]  # Any aperture size will do
        aperture_plate_state = self.high_lvl_aperture.checkAperturePlateState(min_available=self._lastApertureNmbr)

        for aperture_size, aperture_numbers in self.high_lvl_aperture.available_apertures.items():
            self.assertEqual(aperture_plate_state[aperture_size], len(aperture_numbers))

        # Set all apertures of one size to worn out
        for aperture in self.high_lvl_aperture._high_level_aptr_data:
            self.high_lvl_aperture._high_level_aptr_data[aperture]["Worn-out"] = True
        self.high_lvl_aperture._updateAvailableApertures()
        # No/zero apertures should be available after removing all available apertures
        self.assertEqual(self.high_lvl_aperture.checkAperturePlateState(min_available=0)[aperture_size], 0)

        # Set all apertures to be not worn out
        for aperture in self.high_lvl_aperture._high_level_aptr_data:
            self.high_lvl_aperture._high_level_aptr_data[aperture]["Worn-out"] = False
        self.high_lvl_aperture._updateAvailableApertures()
        # There should be no lack of available apertures after making all apertures not worn out
        self.assertEqual(self.high_lvl_aperture.checkAperturePlateState(min_available=0), None)

        # Test with only one aperture of a size not worn out
        for aperture in self.high_lvl_aperture._high_level_aptr_data:
            self.high_lvl_aperture._high_level_aptr_data[aperture]["Worn-out"] = True
        else:
            self.high_lvl_aperture._high_level_aptr_data[aperture]["Worn-out"] = False # Only set the last aperture to be not worn out

        self.high_lvl_aperture._updateAvailableApertures()
        # Only one aperture for one size should be available after setting all to worn out
        for aperture_size, avail_aprtrs in self.high_lvl_aperture.checkAperturePlateState(min_available=1).items():
            if aperture_size == self.high_lvl_aperture.getCombinedApertureData()[aperture]["Size"]:
                self.assertEqual(self.high_lvl_aperture.checkAperturePlateState(min_available=1)[aperture_size], 1)
            else:
                self.assertEqual(self.high_lvl_aperture.checkAperturePlateState(min_available=1)[aperture_size], 0)
