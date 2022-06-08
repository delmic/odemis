import unittest

from util.pytest_log_filter import filter_test_log


class TestFilterTestLog(unittest.TestCase):
    # NOTE: This test case does not test much of the functionality, it mostly is an example on how to use the function filter_test_log
    """
    Test on the filter_test_log function
    """

    def test_sample_input(self):
        with open("test_input_pytest_log_filter.txt") as f:
            log_txt = f.read()
        filtered_log = filter_test_log(log_txt)
        self.assertIsInstance(filtered_log, str)


if __name__ == '__main__':
    unittest.main()
