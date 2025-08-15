# Test framework: Python unittest (built-in). These tests follow the existing unittest.TestCase structure.
import unittest

from util.pytest_log_filter import filter_test_log


class TestFilterTestLog(unittest.TestCase):
    """
    Test on the filter_test_log function
    """

    def test_sample_input_ubuntu_18_04_summary(self):
        """Test that the logs are filtered correctly on Ubuntu 18.04"""
        with open("test_input_pytest_log_filter_18_04.txt") as f:
            log_txt = f.read()
        filtered_log = filter_test_log(log_txt, 'summary')
        self.assertEqual(5, len(filtered_log.split("\n")))
        self.assertTrue(filtered_log.startswith("Running "))

    def test_sample_input_ubuntu_18_04_warning(self):
        """Test that the logs are filtered correctly on Ubuntu 18.04"""
        with open("test_input_pytest_log_filter_18_04.txt") as f:
            log_txt = f.read()
        filtered_log = filter_test_log(log_txt, 'warning')
        self.assertEqual(11, len(filtered_log.split("\n")))
        self.assertTrue(filtered_log.startswith("Running "))

    def test_sample_input_ubuntu_20_04_summary(self):
        """Test that the logs are filtered correctly on Ubuntu 20.04"""
        with open("test_input_pytest_log_filter_20_04.txt") as f:
            log_txt = f.read()
        filtered_log = filter_test_log(log_txt, 'summary')
        self.assertEqual(3, len(filtered_log.split("\n")))
        self.assertTrue(filtered_log.startswith("Running "))

    def test_sample_input_ubuntu_20_04_no_summary(self):
        """Test that the logs are filtered correctly on Ubuntu 20.04"""
        with open("test_input_pytest_log_filter_20_04.txt") as f:
            log_txt = f.read()
        # remove the summary part to test the case where no summary is present
        log_txt = log_txt.split("short test summary")[0]
        filtered_log = filter_test_log(log_txt, 'summary')
        self.assertTrue(filtered_log.startswith("Running"))
        self.assertTrue("FAILED" in filtered_log)

    def test_sample_input_ubuntu_20_04_warning(self):
        """Test that the logs are filtered correctly on Ubuntu 20.04"""
        with open("test_input_pytest_log_filter_20_04.txt") as f:
            log_txt = f.read()
        filtered_log = filter_test_log(log_txt, 'warning')
        self.assertEqual(7, len(filtered_log.split("\n")))
        self.assertTrue(filtered_log.startswith("Running "))

    def test_sample_empty_str(self):
        """Test that the logs are filtered correctly and the size is reduced for an empty string"""
        log_txt = ""
        filtered_log = filter_test_log(log_txt)
        self.assertEqual(None, filtered_log)


if __name__ == '__main__':
    unittest.main()

    def test_invalid_mode_defaults_to_summary_behavior_or_graceful(self):
        """
        Test that an invalid mode does not crash and yields a sensible, non-empty filtered output.
        The function should be resilient to unexpected inputs for the 'mode' argument.
        """
        log_txt = (
            "collected 3 items\\n"
            "test_a.py::test_ok PASSED\\n"
            "test_b.py::test_warn PASSED\\n"
            "test_c.py::test_fail FAILED\\n"
            "=========================== short test summary info ===========================\\n"
            "FAILED test_c.py::test_fail - AssertionError: expected 1 == 2\\n"
        )
        filtered = filter_test_log(log_txt, 'nonexistent-mode')
        # We expect the function to not return None for non-empty logs
        self.assertIsNotNone(filtered)
        self.assertTrue(isinstance(filtered, str))
        self.assertTrue(len(filtered.strip()) > 0)

    def test_none_input_returns_none(self):
        """
        When the input log is None, the filter should return None (graceful handling).
        """
        filtered = filter_test_log(None)
        self.assertIsNone(filtered)

    def test_summary_mode_with_minimal_log_snippet(self):
        """
        A minimal log that includes a short test summary should produce a compact, prefixed output.
        """
        log_txt = (
            "============================= test session starts =============================\\n"
            "platform linux -- Python 3.10.0, pytest-7.2.0\\n"
            "collected 2 items\\n"
            "test_sample.py::test_one PASSED\\n"
            "test_sample.py::test_two FAILED\\n"
            "=========================== short test summary info ===========================\\n"
            "FAILED test_sample.py::test_two - AssertionError: boom\\n"
            "============================== 1 failed, 1 passed =============================\\n"
        )
        filtered = filter_test_log(log_txt, 'summary')
        self.assertIsNotNone(filtered)
        self.assertTrue(filtered.startswith("Running"))
        # Should keep the failure line from the summary
        self.assertIn("FAILED test_sample.py::test_two", filtered)

    def test_summary_mode_when_no_summary_section(self):
        """
        When no short test summary section exists, the function should still return a useful
        compact output that includes failure indicators.
        """
        log_txt = (
            "============================= test session starts =============================\\n"
            "platform linux -- Python 3.10.0, pytest-7.2.0\\n"
            "collected 1 item\\n"
            "test_sample.py::test_two FAILED\\n"
            "=================================== FAILURES ===================================\\n"
            "__________________________________ test_two ____________________________________\\n"
            "E   AssertionError: boom\\n"
            "============================== 1 failed in 0.10s ===============================\\n"
        )
        # Explicitly cut off any potential summary to simulate missing summary
        log_txt = log_txt.split("short test summary")[0]
        filtered = filter_test_log(log_txt, 'summary')
        self.assertIsNotNone(filtered)
        # Existing tests check this property, so we reuse the same assertion shape
        self.assertTrue(filtered.startswith("Running"))
        self.assertIn("FAILED", filtered)

    def test_warning_mode_collects_warnings_only(self):
        """
        Ensure the warning mode includes warnings (and relevant prefix), but not full tracebacks or noise.
        """
        log_txt = (
            "============================= test session starts =============================\\n"
            "collected 2 items\\n"
            "test_warn.py::test_warn PASSED\\n"
            "test_warn.py::test_warn_again PASSED\\n"
            "=============================== warnings summary ===============================\\n"
            "test_warn.py:12: UserWarning: something odd\\n"
            "  warnings.warn('something odd')\\n"
            "test_warn.py:25: DeprecationWarning: deprecated thing\\n"
            "  warnings.warn('deprecated thing', DeprecationWarning)\\n"
            "======================== 2 passed, 2 warnings in 0.12s ========================\\n"
        )
        filtered = filter_test_log(log_txt, 'warning')
        self.assertIsNotNone(filtered)
        self.assertTrue(filtered.startswith("Running "))
        self.assertIn("UserWarning: something odd", filtered)
        self.assertIn("DeprecationWarning: deprecated thing", filtered)

    def test_warning_mode_with_no_warnings(self):
        """
        If there are no warnings, warning mode should still yield a compact output (likely with the prefix)
        and not be None.
        """
        log_txt = (
            "============================= test session starts =============================\\n"
            "collected 1 item\\n"
            "test_ok.py::test_ok PASSED\\n"
            "============================== 1 passed in 0.01s ==============================\\n"
        )
        filtered = filter_test_log(log_txt, 'warning')
        self.assertIsNotNone(filtered)
        self.assertTrue(filtered.startswith("Running "))

    def test_non_prefixed_log_still_returns_compact_output(self):
        """
        Some build systems might strip the 'Running' line or similar prefixes from the raw log.
        The function should still return a compact string and not crash.
        """
        log_txt = (
            "collected 1 item\\n"
            "test_ok.py::test_ok PASSED\\n"
            "============================== 1 passed in 0.01s ==============================\\n"
        )
        filtered = filter_test_log(log_txt, 'summary')
        self.assertIsNotNone(filtered)
        # Even if it doesn't start with "Running" in this synthetic case, the function
        # should still return a succinct result. We do not enforce the prefix here.
        self.assertTrue(len(filtered.strip()) > 0)

    def test_large_input_does_not_crash_and_returns_result(self):
        """
        Stress test with a large synthesized log to ensure the function scales and returns a valid output.
        """
        header = (
            "============================= test session starts =============================\\n"
            "platform linux -- Python 3.10.0, pytest-7.2.0\\n"
        )
        body = "".join([f"test_many.py::test_{i:04d} PASSED\\n" for i in range(0, 2000)])
        tail = (
            "=========================== short test summary info ===========================\\n"
            "========================= 2000 passed in 3.21s =========================\\n"
        )
        log_txt = header + "collected 2000 items\\n" + body + tail
        filtered = filter_test_log(log_txt, 'summary')
        self.assertIsNotNone(filtered)
        self.assertTrue(len(filtered.strip()) > 0)
        # Should not include the entire body; confirm it's significantly smaller than the raw log
        self.assertLess(len(filtered), len(log_txt))

    def test_bytes_input_is_handled_gracefully(self):
        """
        If bytes are passed by mistake, function should not crash.
        It may return None or a string; both are acceptable as long as it handles gracefully.
        """
        log_bytes = b"collected 1 item\n test_ok.py::test_ok PASSED\n"
        try:
            filtered = filter_test_log(log_bytes, 'summary')  # type: ignore[arg-type]
        except Exception as exc:
            self.fail(f"filter_test_log raised an exception on bytes input: {exc!r}")
        # Accept either None (cannot parse) or a non-empty string result
        self.assertTrue(filtered is None or (isinstance(filtered, str) and len(filtered.strip()) > 0))
