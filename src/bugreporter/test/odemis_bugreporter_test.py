#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 17 October 2018

@author: Philip Winkler

Copyright © 2018 Philip Winkler, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.

"""
from bugreporter.odemis_bugreporter import OdemisBugreporter, BugreporterFrame, _validate_user_attachment_size
from urllib.error import HTTPError
import logging
import odemis
import os
import socket
import time
import unittest
import wx
import zipfile

REPORTER_TEST_ID = 12
# Set TEST_NO_SUPPORT_TICKET=1 to skip test cases which create a ticket, so we don't fill up
# the osticket database with test messages.
TEST_NO_SUPPORT_TICKET = (os.environ.get("TEST_NO_SUPPORT_TICKET", 0) != 0)
TEST_SUPPORT_TEAM_EMAIL = "winkler@delmic.com"

TEST_KEY_PATH = os.path.dirname(odemis.__file__) + '/../../install/linux/usr/share/odemis/osticket.key'


class TestOdemisBugreporter(unittest.TestCase):

    def setUp(self):
        self.bugreporter = OdemisBugreporter()

    def test_search_api_key(self):
        """
        Tests search_api_key function.
        """
        # If test computer has key saved in .local/share/odemis or /usr/share/odemis,
        # check this key, otherwise expect LookupError
        customer_key_path = os.path.join(os.path.expanduser("~"), '.local', 'share',
                                         'odemis', 'osticket.key')
        fallback_key_path = os.path.join('/usr', 'share', 'odemis', 'osticket.key')
        if not os.path.isfile(customer_key_path) and not os.path.isfile(fallback_key_path):
            self.assertRaises(LookupError, self.bugreporter.search_api_key)
        else:
            try:
                api_key = self.bugreporter.search_api_key()
                self.assertEqual(type(api_key), str, "API key needs to be a string.")
                self.assertFalse('\n' in api_key, "API key should not contain newline character")
            except Exception as e:
                self.fail("search_api_key failed with exception %s" % e)

    def test_source_api_key(self):
        """
        Tests format of source api key.
        """
        with open(TEST_KEY_PATH, 'r') as key_file:
            api_key = key_file.read().strip('\n')
        self.assertEqual(type(api_key), str, "API key needs to be a string.")
        self.assertFalse('\n' in api_key, "API key should not contain newline character")

    def test_create_ticket(self):
        """
        Tests ticket creation on osticket server.
        """
        if TEST_NO_SUPPORT_TICKET == 1:
            logging.info('Skipping "test_create_ticket"')
            return

        with open(TEST_KEY_PATH, 'r') as key_file:
            api_key = key_file.read().strip('\n')

        description = {
            'name': 'TÉstingteam member',
            'email': 'winkler@delmic.com',
            'subject': 'Bugreporter test',
            'message': "This is a test, including some non-ascii characters like µ or 你好",
            'topicId': REPORTER_TEST_ID,
            'installation': socket.gethostname(),
            }

        # Check without a file
        try:
            self.bugreporter.create_ticket(api_key, description)
        except Exception as e:
            self.fail('Uploading without a file failed with exception %s' % e)

    def test_wrong_api_key(self):
        """Create a ticket with an incorrect API key"""
        description = {
            'name': 'Testingteam member',
            'email': 'winkler@delmic.com',
            'subject': 'Bugreporter test',
            'message': "This is a test",
            'topicId': REPORTER_TEST_ID,
            'installation': "test",
            }
        incorrect_api_key = 'xxxxxxx'
        self.assertRaises(HTTPError, self.bugreporter.create_ticket,
                           incorrect_api_key, description)

    def test_large_file(self):
        """
        Tests whether large files are compressed and uploaded properly.
        """
        # Add large file to list of files that will be compressed
        if not os.path.isdir(os.path.expanduser("~") + '/odemis-overlay-report'):
            os.mkdir(os.path.expanduser("~") + '/odemis-overlay-report')
        fn = os.path.expanduser("~") + '/odemis-overlay-report/bugreporter_test'
        with open(fn, 'w+') as f:
            f.write('x' * int(5e8))  # 500 MB file

        # Compress
        try:
            self.bugreporter.compress_files()
        except Exception as e:
            self.fail("Compression of large file failed with exception %s" % e)
        self.assertTrue(zipfile.is_zipfile(self.bugreporter.zip_fn))

        files = ['odemis.log', 'odemis-gui.log', 'odemis-gui.log.1', 'odemis.conf', 'syslog',
                 'odemis-mic-selector.log', 'odemis-bug-screenshot.png']

        zip_file = zipfile.ZipFile(self.bugreporter.zip_fn)
        for f in files:
            if os.path.isfile(f):
                self.assertTrue(f in zip_file.namelist(), "File %s not found in archive." % f)

        # Create ticket
        if TEST_NO_SUPPORT_TICKET != 1:
            with open(TEST_KEY_PATH, 'r') as key_file:
                api_key = key_file.read().strip('\n')

            description = {
                'name': 'Testingteam member',
                'email': TEST_SUPPORT_TEAM_EMAIL,
                'subject': 'Bugreporter test',
                'message': "This is a test.",
                'topicId': REPORTER_TEST_ID,
                # No installation field, should still work (it's optional, and old bug reporters didn't provide it)
                # 'installation': "test"
            }

            try:
                self.bugreporter.create_ticket(api_key, description, [self.bugreporter.zip_fn])
            except Exception as e:
                os.remove(self.bugreporter.zip_fn)
                os.remove(fn)
                self.fail('Uploading with a large file failed with exception %s' % e)

        os.remove(self.bugreporter.zip_fn)
        os.remove(fn)

    def test_compress_file(self):
        """Test log file compression"""
        self.bugreporter.compress_files()
        self.assertTrue(zipfile.is_zipfile(self.bugreporter.zip_fn))
        os.remove(self.bugreporter.zip_fn)

    def test_add_user_attachment(self):
        """Test adding user attachments to the bugreporter archive"""

        bugreporter = OdemisBugreporter()
        bugreporter.compress_files() # wait for system files to be compressed

        # create user attachment
        path = os.path.expanduser("~") + '/odemis-overlay-report/attachments'
        if not os.path.isdir(path):
            os.mkdir(path)
        valid_1_fn = os.path.join(path, "user_attachment_valid_1")
        valid_2_fn = os.path.join(path, "user_attachment_valid_2")

        # remove files if they exist
        if os.path.isfile(valid_1_fn):
            os.remove(valid_1_fn)
        if os.path.isfile(valid_2_fn):
            os.remove(valid_2_fn)

        for fn in [valid_1_fn, valid_2_fn]:
            with open(fn, 'w+') as f:
                f.write('x' * int(4e7))  # 40 MB file

        # test the case where no user attachments are added
        bugreporter.user_attachments = []
        bugreporter._compress_user_attachments()
        self.assertEqual(bugreporter.attachment_zip_fn, None)

        # add user attachments to bugreporter
        bugreporter.user_attachments.extend([valid_1_fn, valid_2_fn])

        # validate user attachments
        valid = _validate_user_attachment_size(bugreporter.user_attachments)
        self.assertTrue(valid, "User attachments are not valid.")

        # compress user attachments
        bugreporter._compress_user_attachments()

        # check attachment is in zip file
        zip_file = zipfile.ZipFile(bugreporter.attachment_zip_fn)
        for f in bugreporter.user_attachments:
            if os.path.isfile(f):
                self.assertTrue(os.path.basename(f) in zip_file.namelist(), "File %s not found in archive." % f)

        # clean up
        os.remove(bugreporter.zip_fn)
        os.remove(bugreporter.attachment_zip_fn)
        os.remove(valid_1_fn)
        os.remove(valid_2_fn)

    def test_validate_user_attachment_size(self):
        """Test the size validation of user attachments"""

        # add user attachment
        path = os.path.expanduser("~") + '/odemis-overlay-report'
        if not os.path.isdir(path):
            os.mkdir(path)
        valid_fn = os.path.join(path, "user_attachment_valid")
        valid_fn2 = os.path.join(path, "user_attachment_valid2")
        invalid_fn = os.path.join(path, "user_attachment_invalid")
        with open(valid_fn, 'w+') as f:
            f.write('x' * int(5e7))  # 50 MB file

        with open(valid_fn2, 'w+') as f:
            f.write('x' * int(9e7))  # 90 MB file

        with open(invalid_fn, 'w+') as f:
            f.write('x' * int(5e8))  # 500 MB file

        # return true files below 100MB
        user_attachments = [valid_fn]
        self.assertTrue(_validate_user_attachment_size(user_attachments))

        # return false, sum of files too large
        user_attachments = [valid_fn, valid_fn2]
        self.assertFalse(_validate_user_attachment_size(user_attachments))

        # return false file too large
        user_attachments = [invalid_fn]
        self.assertFalse(_validate_user_attachment_size(user_attachments))

        # clean up
        os.remove(valid_fn)
        os.remove(invalid_fn)

    def test_window(self):
        # Note: UIActionSimulator.Text() doesn't seem to work on wxPython 4.0.7,
        # but it works again on wxPython 4.1. It worked on wxPython 4.0.1.
        wx_ver = tuple(int(v) for v in wx.__version__.split("."))
        if wx_ver < (4, 1, 0):
            logging.warning("Test case is known to fail on wxPython 4.0.7 due to buggy UIActionSimulator.Text")

        bugreporter = OdemisBugreporter()
        # Special verison of .run(), which simulates inputs
        bugreporter._compress_files_f = bugreporter._executor.submit(bugreporter.compress_files)
        app = wx.App()
        gui = BugreporterFrame(bugreporter)
        bugreporter.gui = gui
        # Create simulator and focus field
        sim = wx.UIActionSimulator()
        self.gui_loop(0.1)

        # Fill up the form
        gui.name_ctrl.SetFocus()
        # TODO: how to simulate typing non ascii-characters? .Char() + modifiers?
        # sim.Text(b"Tstingteam member")
        gui.name_ctrl.SetValue("TÉstingteam member")
        self.gui_loop(0.1)

        gui.email_ctrl.SetFocus()
        gui.email_ctrl.SetValue(TEST_SUPPORT_TEAM_EMAIL)
        # sim.Text(TEST_SUPPORT_TEAM_EMAIL) # "@" doesn't work
        self.gui_loop(0.1)

        gui.summary_ctrl.SetFocus()
        sim.Text(b"Bugreporter test")
        self.gui_loop(0.1)

        gui.description_ctrl.SetFocus()
        sim.Text(b"This is a test")
        self.gui_loop(0.1)

        # Simulates a "click" on the button by pressing Enter
        gui.report_btn.SetFocus()
        sim.Char(ord("\r"))
        self.gui_loop(0.1)

        try:
            # If sent successfully, the window should close after a few seconds
            self.gui_loop(2)
            bugreporter._compress_files_f.result()
            self.gui_loop(2)
            self.assertFalse(gui)  # wxPython widgets which are destroyed are considered "False"
            bugreporter._executor.shutdown()  # wait for the background tasks to complete
        finally:
            # app.MainLoop()  # DEBUG: For leaving the window afterwards
            if gui:
                gui.Destroy()

    def gui_loop(self, slp=0):
        """
        Execute the main loop for the GUI until all the current events are processed
        slp (0<=float): time to wait (s)
        """
        start = time.time()
        app = wx.GetApp()
        if app is None:
            return

        while True:
            wx.CallAfter(app.ExitMainLoop)
            app.MainLoop()

            if time.time() > (start + slp):
                break


if __name__ == '__main__':
    unittest.main()
