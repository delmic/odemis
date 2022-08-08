import copy
import logging
import os
import unittest

from odemis.acq.align.orsay_aperture import HighLevelAperture, NoApertureError
from odemis.driver import orsay
from odemis.driver.orsay import recursive_getattr
from odemis.driver.test.orsay_test import CONFIG_ORSAY, NO_SERVER_MSG, CONFIG_FIBAPERTURE

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

        cls._lastApertureNmbr = int(cls.oserver.datamodel.HybridAperture.SelectedDiaph.Max)

        cls.high_lvl_aperture = HighLevelAperture(cls.low_lvl_aperture)

    @classmethod
    def tearDownClass(cls):
        """
        Terminate the Orsay client
        """
        cls.oserver.terminate()

    def testGetApertureData(self):
        complete_data = self.high_lvl_aperture.getCombinedApertureData()
        self.assertEqual(len(complete_data), self._lastApertureNmbr)
        for aprtr_nmbr, data in complete_data.items():
            expected_keys = {"Lifetime", "Size", "Position", "Nominal probe-current", "Last measured current", "Worn-out"}
            self.assertEqual(data.keys(), expected_keys)
            self.assertEqual(data["Position"].keys(), {"x", "y"})

    def testAvailableApertures(self):
        self.high_lvl_aperture._updateAvailableApertures()

        if not self.high_lvl_aperture.available_apertures:
            self.skipTest("The available aperture dict is empty. Either meaning this test should fail or no apertures are available. \n"
                               "This test is skipped.")

        for aperture_size, aperture_numbers in self.high_lvl_aperture.available_apertures.items():
            self.assertIsInstance(aperture_size, float)
            self.assertIsInstance(aperture_numbers, set)
            # Test for each size if the number match the correct size
            for aprtr_nmbr in aperture_numbers:
                aprtr_size = float(recursive_getattr(self.oserver.datamodel.HybridAperture, f"Aperture{aprtr_nmbr}").Size.Actual)
                self.assertEqual(aperture_size, aprtr_size)


    def testSuggestReplacementAperture(self):
        if not self.high_lvl_aperture.available_apertures:
            self.skipTest("The available aperture dict is empty. Either meaning this test should fail or no apertures are available. \n"
                               "This test is skipped.")
        
        init_available_apertures = copy.deepcopy(self.high_lvl_aperture.available_apertures)
        aperture_size = list(self.high_lvl_aperture.available_apertures)[0]  # Any aperture size will do
        if len(self.high_lvl_aperture.available_apertures[aperture_size]) >= 1:
            suggested_aperture = self.high_lvl_aperture._suggestReplacementAperture(aperture_size)
            self.assertIsInstance(suggested_aperture, int)

        self.high_lvl_aperture.available_apertures[aperture_size] = {}
        with self.assertRaises(NoApertureError):
            self.high_lvl_aperture._suggestReplacementAperture(aperture_size)

        self.high_lvl_aperture.available_apertures = init_available_apertures


    def testCheckAperturePlate(self):
        if not self.high_lvl_aperture.available_apertures:
            self.skipTest(
                "The available aperture dict is empty. Either meaning this test should fail or no apertures are available. \n"
                "This test is skipped.")

        init_available_apertures = copy.deepcopy(self.high_lvl_aperture.available_apertures)

        aperture_size = list(self.high_lvl_aperture.available_apertures)[0]  # Any aperture size will do
        aperture_plate_state = self.high_lvl_aperture.checkAperturePlateState(min_available=self._lastApertureNmbr)

        for aperture_size, aperture_numbers in self.high_lvl_aperture.available_apertures.items():
            self.assertEqual(aperture_plate_state[aperture_size], len(aperture_numbers))

        self.high_lvl_aperture.available_apertures[aperture_size] = {}
        # No/zero apertures should be available after removing all available apertures
        self.assertEqual(self.high_lvl_aperture.checkAperturePlateState(min_available=0)[aperture_size], 0)

        self.high_lvl_aperture.available_apertures = init_available_apertures
