import unittest

from util.pytest_log_filter import filter_test_log


class TestFilterTestLog(unittest.TestCase):
    """
    Test on the filter_test_log function
    """

    def test_sample_input_ubuntu_18_04(self):
        """Test that the logs are filtered correctly on Ubuntu 18.04"""
        with open("test_input_pytest_log_filter_18_04.txt") as f:
            log_txt = f.read()
        filtered_log = filter_test_log(log_txt)
        self.assertEqual(5, len(filtered_log.split("\n")))
        self.assertTrue(filtered_log.startswith("Running /home/testing"))

    def test_sample_input_ubuntu_20_04(self):
        """Test that the logs are filtered correctly on Ubuntu 20.04"""
        with open("test_input_pytest_log_filter_20_04.txt") as f:
            log_txt = f.read()
        filtered_log = filter_test_log(log_txt)
        self.assertEqual(3, len(filtered_log.split("\n")))
        self.assertTrue(filtered_log.startswith("Running /home/testing"))

    def test_sample_empty_str(self):
        """Test that the logs are filtered correctly and the size is reduced for an empty string"""
        log_txt = ""
        filtered_log = filter_test_log(log_txt)
        self.assertEqual(None, filtered_log)


if __name__ == '__main__':
    unittest.main()
