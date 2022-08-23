#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 10 apr 2012

@author: Éric Piel
Testing class for main.py of odemisd.

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms 
of the GNU General Public License version 2 as published by the Free Software 
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; 
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR 
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with 
Odemis. If not, see http://www.gnu.org/licenses/.
'''
try:
    import StringIO
except ImportError:  # Python 3 naming
    import io as StringIO
import logging
from odemis import model
import odemis
from odemis.odemisd import main
from odemis.util import timeout, testing
import os
import subprocess
import sys
import time
import unittest
import yaml


logging.getLogger().setLevel(logging.DEBUG)

# ODEMISD_CMD = "/usr/bin/python2 -m odemis.odemisd.main"
ODEMISD_CMD = [sys.executable, os.path.dirname(odemis.__file__) + "/odemisd/main.py"]
SIM_CONFIG = os.path.join(os.path.dirname(__file__), "optical-sim.odm.yaml")

class TestCommandLine(unittest.TestCase):
    """
    This contains test cases for the command-line level of odemisd.
    """
    @classmethod
    def create_tests(cls):
        configs_pass = ["optical-sim.odm.yaml",
           "example-optical-odemisd-config.odm.yaml",
           "example-combined-actuator.odm.yaml",
           "example-secom.odm.yaml",
           ]

        i = 0
        for config in configs_pass:
            setattr(cls, "test_pass_%d" % i, cls.create_test_validate_pass(config))
            i += 1

        configs_error = ["syntax-error-1.odm.yaml",
                         "syntax-error-2.odm.yaml",
                         # Skipped: for now, double key def is only a warning
                         # "syntax-error-3.odm.yaml",
                         "semantic-error-1.odm.yaml",
                         "semantic-error-2.odm.yaml",
                         # This one can only be detected on a real instantiation
                         # "semantic-error-3.odm.yaml",
                         # TODO: handle cyclic creation error
                         # "semantic-error-4.odm.yaml",
                         # wrong affects
                         "semantic-error-5.odm.yaml",
                         # unused components
                         "semantic-error-6.odm.yaml",
                         # test creating a component without parent and without class
                         "semantic-error-7.odm.yaml",
                         # test with multiple role
                         "semantic-error-8.odm.yaml",
                         "semantic-error-md.odm.yaml",
                        ]

        i = 0
        for config in configs_error:
            setattr(cls, "test_error_%d" % i, cls.create_test_validate_error(config))
            i += 1

    @staticmethod
    def create_test_validate_pass(filename):
        def test_validate_pass(self):
            logging.info("testing run with %s (should succeed)", filename)
            cmdline = "odemisd --log-level=2 --log-target=test.log --validate %s" % filename
            ret = main.main(cmdline.split())
            self.assertEqual(ret, 0, "error detected in correct config "
                                     "file '%s'" % filename)
            os.remove("test.log")
        return test_validate_pass

    @staticmethod
    def create_test_validate_error(filename):
        def test_validate_error(self):
            logging.info("testing run with %s (should fail)", filename)
            cmdline = "odemisd --log-level=2 --log-target=test.log --validate %s" % filename
            ret = main.main(cmdline.split())
            self.assertNotEqual(ret, 0, "no error detected in erroneous config "
                                        "file '%s'" % filename)
            os.remove("test.log")
        return test_validate_error

    def setUp(self):
        # reset the logging (because otherwise it accumulates)
        if logging.root:
            del logging.root.handlers[:]

        # save the stdout in case it's modified
        # NOTE: it seems unittest does this already, but that's just in case
        self.saved_stdout = sys.stdout

    def tearDown(self):
        sys.stdout = self.saved_stdout
        # Make sure the backend is stopped
        try:
            testing.stop_backend()
        except Exception:
            try:  # Try harder
                subprocess.call(["sudo", "odemis-stop"])
            except Exception:
                pass  # Odemis is not installed, too bad
        time.sleep(1)  # give it some time to finish

    def test_error_command_line(self):
        """
        It checks handling when no config file is provided
        """
        try:
            cmdline = "odemisd --validate"
            ret = main.main(cmdline.split())
        except SystemExit as exc: # because it's handled by argparse
            ret = exc.code
        self.assertNotEqual(ret, 0, "trying to run erroneous '%s'" % cmdline)

    def test_log(self):
        cmdline = "odemisd --log-level=2 --log-target=test.log --validate %s" % SIM_CONFIG
        ret = main.main(cmdline.split())
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)

        # a log file?
        st = os.stat("test.log") # this tests also that the file is created
        self.assertGreater(st.st_size, 0)
        os.remove("test.log")

    def test_help(self):
        """
        It checks handling help option
        """
        try:
            # change the stdout
            out = StringIO.StringIO()
            sys.stdout = out

            cmdline = "odemisd --help"
            ret = main.main(cmdline.split())
        except SystemExit as exc:
            ret = exc.code
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)

        output = out.getvalue()
        self.assertTrue("positional arguments" in output)

    @timeout(15)
    def test_daemon(self):
        # First there should be not backend running
        cmdline = "odemisd --log-level=2 --log-target=test.log --check"
        ret = main.main(cmdline.split())
        self.assertEqual(ret, 2, "Backend is said to be running")

        # run the backend as a daemon
        # we cannot run it normally as the child would also think he's in a unittest
        cmdline = "--log-level=2 --log-target=testdaemon.log --daemonize %s" % SIM_CONFIG
        ret = subprocess.call(ODEMISD_CMD + cmdline.split())
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)

        time.sleep(1) # give some time to start

        # now it should say it's starting, and eventually running
        ret = self._wait_backend_starts(5)
        self.assertEqual(ret, 0, "backend status check returned %d" % (ret,))

        # stop the backend
        cmdline = "odemisd --log-level=2 --log-target=test.log --kill"
        ret = main.main(cmdline.split())
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)

        time.sleep(5) # give some time to stop

        # backend should be stopped by now
        cmdline = "odemisd --log-level=2 --log-target=test.log --check"
        ret = main.main(cmdline.split())
        self.assertEqual(ret, 2, "Back-end not stopped: ret = %d" % ret)

        os.remove("test.log")
        os.remove("testdaemon.log")

    @timeout(10)
    def test_error_instantiate(self):
        """Test config files which should fail on instantiation"""
        # Only for the config files that cannot fail on a standard validation
        filename = "semantic-error-3.odm.yaml"
        cmdline = "odemisd --log-target=test.log %s" % filename
        ret = main.main(cmdline.split())
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)

        # now it should say it's starting, and eventually failed
        ret = self._wait_backend_starts(5)
        if ret == 0:
            # We also need to stop the backend then
            cmdline = "odemisd --kill"
            ret = main.main(cmdline.split())
            self.fail("no error detected in erroneous config file '%s'" % filename)

        os.remove("test.log")

    @timeout(20)
    def test_multiple_parents_old(self):
        """Test creating component with multiple parents"""
        filename = "multiple-parents-old-style.odm.yaml"
        cmdline = "--log-level=2 --log-target=testdaemon.log --daemonize %s" % filename
        ret = subprocess.call(ODEMISD_CMD + cmdline.split())
        self.assertEqual(ret, 0, "trying to run '%s' gave status %d" % (cmdline, ret))

        # eventually it should say it's running
        ret = self._wait_backend_starts(10)
        self.assertEqual(ret, 0, "backend status check returned %d" % (ret,))

        # stop the backend
        cmdline = "odemisd --log-level=2 --log-target=test.log --kill"
        ret = main.main(cmdline.split())
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)

        time.sleep(5)  # give some time to stop
        ret = main.main(cmdline.split())
        os.remove("test.log")
        os.remove("testdaemon.log")

    @timeout(20)
    def test_multiple_parents_new(self):
        """Test creating component with multiple parents"""
        filename = "multiple-parents-new-style.odm.yaml"
        cmdline = "--log-level=2 --log-target=testdaemon.log --daemonize %s" % filename
        ret = subprocess.call(ODEMISD_CMD + cmdline.split())
        self.assertEqual(ret, 0, "trying to run '%s' gave status %d" % (cmdline, ret))

        # eventually it should say it's running
        ret = self._wait_backend_starts(10)
        self.assertEqual(ret, 0, "backend status check returned %d" % (ret,))

        # stop the backend
        cmdline = "odemisd --log-level=2 --log-target=test.log --kill"
        ret = main.main(cmdline.split())
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)

        time.sleep(5) # give some time to stop
        ret = main.main(cmdline.split())
        os.remove("test.log")
        os.remove("testdaemon.log")

    @timeout(20)
    def test_properties_set(self):
        """Test creating component with specific properties"""
        filename = "example-secom.odm.yaml"
        cmdline = "--log-level=2 --log-target=testdaemon.log --daemonize %s" % filename
        ret = subprocess.call(ODEMISD_CMD + cmdline.split())
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)

        # eventually it should say it's running
        ret = self._wait_backend_starts(10)
        self.assertEqual(ret, 0, "backend status check returned %d" % (ret,))

        # Check that the specific properties are set
        ccd = model.getComponent(role="ccd")
        self.assertEqual(ccd.exposureTime.value, 0.3)

        ebeam = model.getComponent(role="e-beam")
        self.assertEqual(ebeam.horizontalFoV.value, 1e-6)

        # stop the backend
        cmdline = "odemisd --log-level=2 --log-target=test.log --kill"
        ret = main.main(cmdline.split())
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)

        time.sleep(5) # give some time to stop
        ret = main.main(cmdline.split())
        os.remove("test.log")
        os.remove("testdaemon.log")

    @timeout(40)
    def test_persistent_data(self):
        """Test initialization with persistent data"""

        # Start backend with persistent data specified in yaml file
        filename = "sim-persistent.odm.yaml"
        cmdline = "odemisd --log-level=2 --log-target=test.log --check"
        if os.path.isfile("test-settings.yaml"):
            os.remove("test-settings.yaml")
        ret = main.main(cmdline.split())
        self.assertEqual(ret, 2, "Backend is said to be running")
        cmdline = "--log-level=2 --log-target=test.log --daemonize --settings=test-settings.yaml %s" % filename
        ret = subprocess.call(ODEMISD_CMD + cmdline.split())
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        time.sleep(1)  # give some time to start
        ret = self._wait_backend_starts(10)
        self.assertEqual(ret, 0, "backend status check returned %d" % (ret,))

        # Check if default properties for stage.speed and SimCam metadata are set
        stage = model.getComponent(role='stage')
        ccd = model.getComponent(role='ccd')
        self.assertEqual(stage.speed.value, {'x': 0.1, 'y': 0.1})
        init_pos_cor = [5.e-6, -62.1e-6]
        self.assertEqual(ccd.getMetadata()[model.MD_POS_COR], init_pos_cor)

        # New property and metadata values
        res = [512, 512]
        gain = 1.0
        expt = 3.0
        pxs = [5e-07, 5e-07]
        speed = {'x': 0.3, 'y': 0.3}
        pos_cor = (-9.e-6, -7.3e-6)  # Will be stored as a _list_

        # Change persistent data
        ccd.resolution.value = res
        ccd.gain.value = gain
        ccd.exposureTime.value = expt
        ccd.updateMetadata({model.MD_SENSOR_PIXEL_SIZE: pxs,
                            model.MD_POS_COR: pos_cor})
        stage = model.getComponent(role='stage')
        stage.speed.value = speed

        time.sleep(1)  # make sure the backend is done writing the VA values to the settings file
        # Check if persistent VAs are written to file before closing
        with open('test-settings.yaml', 'r+') as f:
            f_content = yaml.safe_load(f)
        self.assertEqual(f_content["Andor SimCam"]["properties"]["resolution"], res)
        self.assertEqual(f_content["Andor SimCam"]["properties"]["gain"], gain)
        self.assertEqual(f_content["Andor SimCam"]["properties"]["exposureTime"], expt)

        # End backend => metadata should also be saved
        cmdline = "odemisd --log-level=2 --log-target=test.log --kill"
        main.main(cmdline.split())
        time.sleep(5)
        cmdline = "odemisd --log-level=2 --log-target=test.log --check"
        ret = main.main(cmdline.split())
        self.assertEqual(ret, 2, "Backend is said to be running")

        with open('test-settings.yaml', 'r+') as f:
            f_content = yaml.safe_load(f)
        self.assertEqual(f_content["Andor SimCam"]["metadata"]["SENSOR_PIXEL_SIZE"], pxs)
        self.assertEqual(f_content["Andor SimCam"]["metadata"]["POS_COR"], list(pos_cor))

        # Restart backend
        logging.debug("Restarting backend...")
        cmdline = "--log-level=2 --log-target=test.log --daemonize --settings=test-settings.yaml %s" % filename
        ret = subprocess.call(ODEMISD_CMD + cmdline.split())
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        time.sleep(1)  # give some time to start
        ret = self._wait_backend_starts(5)
        self.assertEqual(ret, 0, "backend status check returned %d" % (ret,))

        # Check values of persistent data
        ccd = model.getComponent(role="ccd")
        self.assertEqual(ccd.resolution.value, tuple(res))
        self.assertEqual(ccd.gain.value, gain)
        self.assertEqual(ccd.exposureTime.value, expt)
        self.assertEqual(ccd.getMetadata()[model.MD_SENSOR_PIXEL_SIZE], pxs)
        self.assertEqual(ccd.getMetadata()[model.MD_POS_COR], list(pos_cor))
        stage = model.getComponent(role="stage")
        self.assertEqual(stage.speed.value, speed)

        # Clean up
        cmdline = "odemisd --log-level=2 --log-target=test.log --kill"
        ret = main.main(cmdline.split())
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        time.sleep(5)  # give some time to stop
        ret = main.main(cmdline.split())
        os.remove("test.log")
        os.remove("test-settings.yaml")

    @timeout(40)
    def test_persistent_data_broken_file(self):
        """Test initialization with persistent data with a broken file"""

        # Create broken settings file
        broken_data = {'non_existing_comp': {'properties': {'exposureTime': 5}},
                       'FakeRedStoneStage': {'properties': {'not_a_property': 10}},
                       'Andor SimCam': {'unknown_type': {'': 10}},
                       '': {'properties': {'not_a_property': 10}}}

        with open("test-settings-broken.yaml", "w+") as f:
            yaml.dump(broken_data, f)

        # Same test as before, should still work with other persistent data
        # The broken data should be ignored and a warning should be issued.

        # Start backend with persistent data specified in yaml file
        filename = "sim-persistent.odm.yaml"
        cmdline = "odemisd --log-level=2 --log-target=test.log --check"
        ret = main.main(cmdline.split())
        self.assertEqual(ret, 2, "Backend is said to be running")
        cmdline = "--log-level=2 --log-target=test.log --daemonize --settings=test-settings-broken.yaml %s" % filename
        ret = subprocess.call(ODEMISD_CMD + cmdline.split())
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        time.sleep(1)  # give some time to start
        ret = self._wait_backend_starts(10)
        self.assertEqual(ret, 0, "backend status check returned %d" % (ret,))

        # Check if default properties for stage.speed are set properly
        stage = model.getComponent(role='stage')
        self.assertEqual(stage.speed.value, {'x': 0.1, 'y': 0.1})

        # New property and metadata values
        res = [512, 512]
        gain = 1.0
        expt = 3.0
        pxs = [5e-07, 5e-07]
        speed = {'x': 0.3, 'y': 0.3}

        # Change persistent data
        ccd = model.getComponent(role='ccd')
        ccd.resolution.value = res
        ccd.gain.value = gain
        ccd.exposureTime.value = expt
        ccd.updateMetadata({model.MD_SENSOR_PIXEL_SIZE: pxs})
        stage = model.getComponent(role='stage')
        stage.speed.value = speed

        # Check if persistent VAs are written to file before closing
        time.sleep(1)
        with open('test-settings-broken.yaml', 'r') as f:
            f_content = yaml.safe_load(f)
        self.assertEqual(f_content["Andor SimCam"]["properties"]["resolution"], res)
        self.assertEqual(f_content["Andor SimCam"]["properties"]["gain"], gain)
        self.assertEqual(f_content["Andor SimCam"]["properties"]["exposureTime"], expt)

        # Restart backend
        cmdline = "odemisd --log-level=2 --log-target=test.log --kill"
        main.main(cmdline.split())
        time.sleep(5)
        cmdline = "odemisd --log-level=2 --log-target=test.log --check"
        ret = main.main(cmdline.split())
        self.assertEqual(ret, 2, "Backend is said to be running")
        cmdline = "--log-level=2 --log-target=test.log --daemonize --settings=test-settings-broken.yaml %s" % filename
        ret = subprocess.call(ODEMISD_CMD + cmdline.split())
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        time.sleep(1)  # give some time to start
        ret = self._wait_backend_starts(5)
        self.assertEqual(ret, 0, "backend status check returned %d" % (ret,))

        # Check values of persistent data
        ccd = model.getComponent(role="ccd")
        self.assertEqual(ccd.resolution.value, tuple(res))
        self.assertEqual(ccd.gain.value, gain)
        self.assertEqual(ccd.exposureTime.value, expt)
        self.assertEqual(ccd.getMetadata()[model.MD_SENSOR_PIXEL_SIZE], pxs)
        stage = model.getComponent(role="stage")
        self.assertEqual(stage.speed.value, speed)

        # Clean up
        cmdline = "odemisd --log-level=2 --log-target=test.log --kill"
        ret = main.main(cmdline.split())
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        time.sleep(5)  # give some time to stop
        ret = main.main(cmdline.split())
        os.remove("test.log")
        os.remove("test-settings-broken.yaml")

    @timeout(20)
    def test_no_persistent(self):
        """Test initialization with persistent data but without a settings file"""
        # Start backend without any settings file defined. As it's not running
        # as root, it will run without persistent data stored.
        # => just show warnings in the log file, and runs fine.
        filename = "sim-persistent.odm.yaml"
        cmdline = "odemisd --log-level=2 --log-target=test.log --check"
        ret = main.main(cmdline.split())
        self.assertEqual(ret, 2, "Backend is said to be running")
        cmdline = "--log-level=2 --log-target=test.log --daemonize %s" % filename
        ret = subprocess.call(ODEMISD_CMD + cmdline.split())
        self.assertEqual(ret, 0, "trying to run '%s'" % cmdline)
        time.sleep(1)  # give some time to start
        ret = self._wait_backend_starts(10)
        self.assertEqual(ret, 0, "backend status check returned %d" % (ret,))

        # Restart backend
        cmdline = "odemisd --log-level=2 --log-target=test.log --kill"
        main.main(cmdline.split())
        time.sleep(5)
        cmdline = "odemisd --log-level=2 --log-target=test.log --check"
        ret = main.main(cmdline.split())
        self.assertEqual(ret, 2, "Backend is said to be running")
        os.remove("test.log")

    def _wait_backend_starts(self, timeout=5):
        """
        Wait until the backend status is different from "STARTING" (3)
        timeout (0<float): maximum time to wait
        return (int): the new status
        """
        time.sleep(1)
        end = time.time() + timeout
        cmdline = "odemisd --log-level=2 --log-target=test.log --check"
        while time.time() < end:
            ret = main.main(cmdline.split())
            if ret == 3:
                logging.info("Backend is starting...")
                time.sleep(1)
            else:
                break
        else:
            self.fail("Backend still in starting status after %g s" % timeout)

        return ret

# extends the class fully at module
TestCommandLine.create_tests()

if __name__ == '__main__':
    unittest.main()


# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
