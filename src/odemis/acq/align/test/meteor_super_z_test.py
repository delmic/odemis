import unittest
from odemis.util import almost_equal
from odemis.dataio.tiff import open_data
from odemis.acq.align.meteor_super_z import determine_z_position


# Images and calibration data from the z-stack: 2021-06-28-17-20-07zstack_-28.432deg_step50nm_4.80884319rad
CALIB_DATA = {
                'x': {'a': -0.24759672307261632, 'b': 1.0063089478825507, 'c': 653.0753677001792,  'd': 638.8463397122532,  'w0': 11.560179003062268},
                'y': {'a': 0.5893174060828265, 'b': 0.23950839318911246, 'c': 1202.1980639514566,  'd': 425.6030263781317, 'w0': 11.332043010740446},
                'feature_angle': -3.1416,
                'upsample_factor': 5,
                'z_least_confusion': 9.418563712742548e-07,
                'z_calibration_range': (-9.418563712742548e-07, 8.781436287257452e-07)
              }

class TestDetermineZPosition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        z_stack_step_size = 50
        cls.precision = z_stack_step_size * 0.45  # Precision should be better than the step within a z stack

    def test_determine_z_position(self):
        """
        Test for known data the outcome of the function determine_z_position
        """
        # Test on an image below focus
        image = open_data("images/super_z_single_beed_aprox_500nm_under_focus.tif").content[0].getData()
        expected_outcome_image_1 = -592.5e-9  # Value determined using the function determine_z_position
        z, warn_flag = determine_z_position(image, CALIB_DATA)
        self.assertEqual(warn_flag, 0)
        self.assertTrue(almost_equal(expected_outcome_image_1, z, atol=self.precision))

        # Test on an image which is roughly in focus/point of least confusion
        image = open_data("images/super_z_single_beed_semi_in_focus.tif").content[0].getData()
        expected_outcome_image_2 = -62.8e-9  # Value determined using the function determine_z_position
        z, warn_flag = determine_z_position(image, CALIB_DATA)
        self.assertEqual(warn_flag, 0)
        self.assertTrue(almost_equal(expected_outcome_image_2, z, atol=self.precision))

        # Test on an image which is above focus
        image = open_data("images/super_z_single_beed_aprox_500nm_above_focus.tif").content[0].getData()
        expected_outcome_image_3 = 420.6e-9  # Value determined using the function determine_z_position
        z, warn_flag = determine_z_position(image, CALIB_DATA)
        self.assertEqual(warn_flag, 0)
        self.assertTrue(almost_equal(expected_outcome_image_3, z, atol=self.precision))

        # Test on an image where no feature visible because it is just white noise
        image = open_data("images/super_z_no_beed_just_noise.tif").content[0].getData()
        _, warn_flag = determine_z_position(image, CALIB_DATA)
        self.assertEqual(warn_flag, 4)  # Since the entire image is noise the warnflag should be 4

        # Test on an image where no feature visible because it is entirely white
        image = open_data("images/super_z_no_beed_just_white.tif").content[0].getData()
        _, warn_flag = determine_z_position(image, CALIB_DATA)
        self.assertEqual(warn_flag, 5)  # Since the entire image is white the warn_flag should be 5

        # Change the range so warn_flag 6 is raised with an image which is just above focus
        calib_data_limited_range = CALIB_DATA.copy()
        calib_data_limited_range["z_calibration_range"] = (-1e-10, 1e-10)
        image = open_data("images/super_z_single_beed_aprox_500nm_above_focus.tif").content[0].getData()
        expected_outcome_image_3 = 420.6e-9  # Value determined using the function determine_z_position
        z, warn_flag = determine_z_position(image, calib_data_limited_range)
        self.assertEqual(warn_flag, 6)
        self.assertTrue(almost_equal(expected_outcome_image_3, z, atol=self.precision))


if __name__ == '__main__':
    unittest.main()
