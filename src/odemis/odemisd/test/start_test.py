import subprocess
import threading
import time
import unittest
from unittest import mock

import notify2

from odemis.acq.test.stream_test import SECOM_CONFIG
from odemis.odemisd.start import find_window, main
from odemis.util import testing


class StartTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Patch notify2.init and notify2.Notification for all tests in this class
        cls.notify2_patcher = mock.patch.multiple(
            notify2,
            init=mock.DEFAULT,
            Notification=mock.DEFAULT,
        )
        cls.notify2_mocks = cls.notify2_patcher.start()

    @classmethod
    def tearDownClass(cls):
        cls.notify2_patcher.stop()
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
        self._gui_thread.daemon = False
        self._gui_thread.start()
        time.sleep(30)  # Give the back-end & GUI time to properly start

        # assert start.get_notify_object()
        self.notify2_mocks['init'].assert_called_once_with('Odemis')
        # assert start.BackendStarter.show_popup, internally start.BackendStarter._notif.update
        notification_instance_mock = self.notify2_mocks['Notification'].return_value
        notification_instance_mock.update.assert_called_with(
            "Odemis back-end successfully started",
            "Graphical interface will now start.",
            "odemis"
        )
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
        self._gui_thread.daemon = False
        self._gui_thread.start()
        time.sleep(30)  # Give the back-end & GUI time to properly start
        time.sleep(10)  # Give the test-user time to close the pop-up dialogs for "main" in the thread to finish

        self.assertFalse(find_window("Odemis"))
        self.assertTrue(hasattr(self, "error_code"))
        self.assertGreater(self.error_code, 0)


if __name__ == '__main__':
    unittest.main()
