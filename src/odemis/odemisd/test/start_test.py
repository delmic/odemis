import threading
import time
import unittest
import subprocess

from odemis.util import testing
from odemis.odemisd.start import find_window, main
from odemis.acq.test.stream_test import SECOM_CONFIG

class StartTestCase(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        testing.stop_backend()


    def setUp(self):
        try:
            testing.stop_backend()
        except IOError:
            pass

        if find_window("Odemis"):
            subprocess.check_call(["pkill", "-f", "odemis.gui.main"])
            time.sleep(1)  # Wait for the Odemis GUI to properly close

    def tearDown(self):
        if find_window("Odemis"):
            subprocess.check_call(["pkill", "-f", "odemis.gui.main"])
            time.sleep(1) # Wait for the Odemis GUI to properly close

        # Cannot join the gui thread if the GUI is still running. Therefore, close the GUI first.
        if hasattr(self, "_gui_thread"):
            self._gui_thread.join(2)

    def test_odemis_start_default(self):
        def thread_call():
            # Since the back-end has already started only the GUI is started.
            main([" ", SECOM_CONFIG])

        self._gui_thread = threading.Thread(target=thread_call, name="GUI/Back-end thread")
        self._gui_thread.deamon = False
        self._gui_thread.start()
        time.sleep(30)  # Give the back-end & GUI time to properly start

        self.assertTrue(find_window("Odemis"))


    @unittest.skip("This is a test which can only be run manually, see docstring on how to use it.")
    def test_odemis_start_with_an_error(self):
        '''
        To run this test an error needs to be raised when starting the GUI.
        This error can add manually by adding for example "raise ValueError" to a tab which is used by SECOM. The user
        should also close the pop-up windows which show the logs so that the process 'main(["", SECOM_CONFIG])' finishes.
        '''
        def thread_call():
            # Since the back-end has already started only the GUI is started.
            self.error_code = main(["", SECOM_CONFIG])

        self._gui_thread = threading.Thread(target=thread_call, name="GUI/Back-end thread")
        self._gui_thread.deamon = False
        self._gui_thread.start()
        time.sleep(30)  # Give the back-end & GUI time to properly start
        time.sleep(10)  # Give the test-user time to close the pop-up dialogs for "main" in the thread to finish

        self.assertFalse(find_window("Odemis"))
        self.assertTrue(hasattr(self, "error_code"))
        self.assertGreater(self.error_code, 0)


if __name__ == '__main__':
    unittest.main()
