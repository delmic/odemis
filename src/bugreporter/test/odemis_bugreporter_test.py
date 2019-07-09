#!/usr/bin/env python
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

import unittest
import os
import logging
import zipfile
from ..odemis_bugreporter import OdemisBugreporter
from future.moves.urllib.error import HTTPError
import odemis

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
        customer_key_path = os.path.join(os.path.expanduser(u"~"), '.local', 'share',
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
            'name': u'TÉstingteam member',
            'email': u'winkler@delmic.com',
            'subject': u'Bugreporter test',
            'message': u"This is a test, including some non-ascii characters like µ or 你好",
            'topicId': REPORTER_TEST_ID,
            'attachments': [],
            }

        # Check without a file
        try:
            self.bugreporter.create_ticket(api_key, description)
        except Exception as e:
            self.fail('Uploading without a file failed with exception %s' % e)

    def test_wrong_api_key(self):
        """Create a ticket with an incorrect API key"""
        description = {
            'name': u'Testingteam member',
            'email': u'winkler@delmic.com',
            'subject': u'Bugreporter test',
            'message': u"This is a test",
            'topicId': REPORTER_TEST_ID,
            'attachments': [],
            }
        incorrect_api_key = 'xxxxxxx'
        self.assertRaises(HTTPError, self.bugreporter.create_ticket,
                           incorrect_api_key, description)

    def test_large_file(self):
        """
        Tests whether large files are compressed and uploaded properly.
        """
        # Add large file to list of files that will be compressed
        if not os.path.isdir(os.path.expanduser(u"~") + '/odemis-overlay-report'):
            os.mkdir(os.path.expanduser(u"~") + '/odemis-overlay-report')
        fn = os.path.expanduser(u"~") + '/odemis-overlay-report/bugreporter_test'
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
                # zip_file.namelist() doesn't contain '/' as first element
                self.assertTrue(f[1:] in zip_file.namelist(), "File %s not found in archive." % f)

        # Create ticket
        if TEST_NO_SUPPORT_TICKET != 1:
            with open(TEST_KEY_PATH, 'r') as key_file:
                api_key = key_file.read().strip('\n')
    
            description = {
                'name': 'Testingteam member',
                'email': 'winkler@delmic.com',
                'subject': 'Bugreporter test',
                'message': "This is a test.",
                'topicId': REPORTER_TEST_ID,
                'attachments': [],
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

if __name__ == '__main__':
    unittest.main()
