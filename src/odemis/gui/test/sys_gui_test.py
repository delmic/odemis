#!/usr/bin/env python
# coding=utf-8

"""
Created on 2 Feb 2016

@author: Rinze de Laat

Copyright © 2016 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.

----------------------------------------------------------------------------------------------------

This module contains tests for checking that Odemis will launch correctly for each simulated
hardware configuration file in this repository. This includes both the back-end and the GUI.

"""

import os
import shutil
import subprocess
import sys
import threading
import unittest
from time import sleep

# Path of this module
MY_PATH = os.path.abspath(os.path.dirname(__file__))
# Path to the test log files
ODEMISD_LOG_PATH = os.path.join(MY_PATH, "odemisd_test.log")
GUI_LOG_PATH = os.path.join(MY_PATH, "gui_test.log")
# Odemis root path
ODEMIS_PATH = os.path.abspath(os.path.join(MY_PATH, '../../../../'))
# Path to the config files
SIM_CONF_PATH = "%s/install/linux/usr/share/odemis/sim" % ODEMIS_PATH
# Odemis commands
CMD_STOP = "%s/install/linux/usr/bin/odemis-stop " % ODEMIS_PATH
CMD_START = "%s/install/linux/usr/bin/odemis-start -n -l %s " % (ODEMIS_PATH, ODEMISD_LOG_PATH)
CMD_GUI = "%s/install/linux/usr/bin/odemis-gui --logfile %s --log-level 2" % (ODEMIS_PATH,
                                                                              GUI_LOG_PATH)
# These string are searched for in the log files and if any are found, an error is assumed to have
# occurred.
ERROR_TRIGGER = ("EXCEPTION:", "ERROR:", "WARNING:")


# Clear any old log files might have been left behind
try:
    os.remove(ODEMISD_LOG_PATH)
except OSError:
    pass
finally:
    try:
        os.remove(GUI_LOG_PATH)
    except OSError:
        pass

# Load the Yaml config files for the simulated hardware
sim_conf_files = [filename for filename in os.listdir(SIM_CONF_PATH) if filename[-4:] == 'yaml']


class OdemisThread(threading.Thread):
    """ Thread used to run Odemis commands """

    def __init__(self, name, cmd):
        super(OdemisThread, self).__init__(name=name)

        self.proc = None

        self.stdout = None
        self.stderr = None

        self.cmd = cmd
        self.returncode = 0

    def run(self):
        print "  %s" % self.cmd
        self.proc = subprocess.Popen(self.cmd.split(),
                                     shell=False,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)

        self.stdout, self.stderr = self.proc.communicate()
        # print self.stdout
        # print self.stderr
        self.returncode = self.proc.returncode

    def kill(self):
        self.proc.kill()


def test_sleep(t):
    """ Sleep for t seconds """
    try:
        print ""
        for s in range(t):
            sys.stdout.write("  Sleeping for {0} seconds...\r".format(t - s))
            sys.stdout.flush()
            sleep(1)
        print
    except KeyboardInterrupt:
        print "\n  Sleep interrupted..."


def copy_log(log_in_path, log_out_name):
    """ Copy log_in to log_out for later inspection """
    log_out_path = os.path.join('/tmp', log_out_name)
    print "\n  Copying log\n    %s\n  to\n    %s\n" % (log_in_path, log_out_path)
    shutil.copy(log_in_path, log_out_path)
    return log_out_path


def backend_fail(config_name, msg):
    """ Copy the back-end log and raise an exception """
    log_path = copy_log(ODEMISD_LOG_PATH, 'odemisd-%s-test.log' % config_name)

    class BackendFailure(Exception):
        pass

    raise BackendFailure("%s: %s\nLog copied to %s" % (config_name, msg, log_path))


def gui_fail(config_name, msg):
    """ Copy the GUI log and raise an exception """
    log_path = copy_log(GUI_LOG_PATH, 'gui-%s-test.log' % config_name)

    class GuiFailure(Exception):
        pass

    raise GuiFailure("%s: %s\nLog copied to %s" % (config_name, msg, log_path))


class HardwareConfigTestCase(unittest.TestCase):
    """ Test case that's dynamically going to be filled with tests """
    pass


# Stop any running back-ends
print "\n* Halting any Odemis instances...\n"
halt = OdemisThread("Odemis Halting thread", CMD_STOP)
halt.start()
halt.join()
print "\n  Done"


def generate_config_test(sim_conf):
    """ Create and return a test method to be added to the test case """
    def test_simulated_hardware_config(self):
        sim_conf_path = os.path.join(SIM_CONF_PATH, sim_conf)

        print "\n* Starting %s backend\n" % sim_conf
        cmd = CMD_START + sim_conf_path
        start = OdemisThread("Backend %s" % sim_conf, cmd)
        start.start()

        # Wait for the back end to load
        test_sleep(30)

        print "\n* Starting %s GUI\n" % sim_conf
        gui = OdemisThread("GUI %s" % sim_conf, CMD_GUI)
        gui.start()

        # Wait for the GUI to load
        test_sleep(10)

        print "\n* Stopping %s\n" % sim_conf
        stop = OdemisThread("Stop %s" % sim_conf, CMD_STOP)
        stop.start()
        stop.join()

        # If 'start' is still running, kill it forcibly (It might be stuck displaying the log
        # window)
        if start.is_alive():
            start.kill()

        print ""

        try:
            if start.returncode != 0:  # 'ok' return code
                msg = "Bad return code %s for 'odemis-start' script!" % start.returncode
                print "  %s" % msg
                backend_fail(sim_conf, msg)
            elif gui.returncode != 143:  # SIGTERM return code
                msg = "Bad return code %s for 'odemis-gui' script!" % gui.returncode
                print "  %s" % msg
                if gui.returncode == 255:
                    print "  Did the back-end finish loading before the GUI was started?"
                gui_fail(sim_conf, msg)
            else:
                # TODO: make error/exception detection in log files more intelligent?

                odemisd_log = open(ODEMISD_LOG_PATH).read()

                for lbl in ERROR_TRIGGER:
                    if lbl in odemisd_log:
                        msg = "%s found in back-end log!" % lbl
                        print "  %s" % msg
                        backend_fail(sim_conf, msg)

                gui_log = open(GUI_LOG_PATH).read().lower()

                for lbl in ERROR_TRIGGER:
                    if lbl in gui_log:
                        msg = "%s found in GUI log!" % lbl
                        print "  %s" % msg
                        gui_fail(sim_conf, msg)
        finally:
            # Remove log files
            try:
                os.remove(ODEMISD_LOG_PATH)
            except OSError:
                pass
            finally:
                try:
                    os.remove(GUI_LOG_PATH)
                except OSError:
                    pass

    return test_simulated_hardware_config


# Create a test and add it to the test case for each configuration found
for sim_conf in sim_conf_files[:1]:
    test = generate_config_test(sim_conf)
    test_name = "test_%s" % "".join((c if c.isalnum() else '_' for c in sim_conf))
    setattr(HardwareConfigTestCase, test_name, test)


if __name__ == "__main__":
    unittest.main()
