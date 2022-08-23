#!/usr/bin/env python3
# coding=utf-8

"""
Created on 2 Feb 2016

@author: Rinze de Laat

Copyright © 2016 Rinze de Laat and Éric Piel, Delmic

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
import argparse
import glob
import logging
from odemis import model
import os
import subprocess
import sys
import threading
import time


logging.getLogger().setLevel(logging.INFO)


# Path of this module
MY_PATH = os.path.abspath(os.path.dirname(__file__))
# Odemis root path
ODEMIS_PATH = os.path.abspath(os.path.join(MY_PATH, '../'))
# Default path to the config files
SIM_CONF_PATH = "%s/install/linux/usr/share/odemis/sim" % ODEMIS_PATH
# Odemis commands
CMD_STOP = ["%s/install/linux/usr/bin/odemis-stop" % ODEMIS_PATH]
# TODO: We should use start.py here to test starting properly. However, this doesn't work
# as long as we want to support both python interpreters, so we directly call main.py for now.
CMD_START = ["%s/src/odemis/odemisd/main.py" % ODEMIS_PATH, "--log-target"]
CMD_GUI = ["%s/src/odemis/gui/main.py" % ODEMIS_PATH, "--log-level", "2", "--log-target"]


class OdemisThread(threading.Thread):
    """ Thread used to run Odemis commands """

    def __init__(self, name, cmd):
        """
        cmd (list of str): command and arguments to pass
        """
        super(OdemisThread, self).__init__(name=name)
        self.cmd = cmd

        self.proc = None
        self.stdout = None
        self.stderr = None
        self.returncode = 0

    def run(self):
        logging.debug("Running command %s", self.cmd)
        self.proc = subprocess.Popen(self.cmd,
                                     shell=False,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)

        self.stdout, self.stderr = self.proc.communicate()
        # print self.stdout
        # print self.stderr
        self.returncode = self.proc.returncode

    def kill(self):
        self.proc.kill()


def wait_backend_ready():
    """ Wait until the backend is ready of clearly failed
    return (bool): True if the backend is ready, False if it failed to start
    """
    left = 30  # s

    tstart = time.time()
    # First, wait a bit to make sure the backend is started
    logging.info("Sleeping for %g seconds...", left)
    time.sleep(5)

    left -= 5
    try:
        model._core._microscope = None  # force reset of the microscope
        microscope = model.getMicroscope()
        nghosts = len(microscope.ghosts.value) # Components still to start
    except Exception:
        logging.error("Back-end unreachable, probably failed to start")
        return False

    try:
        while left > 0:
            left -= 1
            time.sleep(1)

            # TODO: detect the backend stopped
            prev_nghosts = nghosts
            nghosts = len(microscope.ghosts.value)
            if nghosts == 0:
                break  # Everything is started
            elif nghosts < prev_nghosts:
                # Allow to wait 3 s more per component started
                left += 3 * (prev_nghosts - nghosts)

        logging.info("Back-end took %d s to start", time.time() - tstart)
    except KeyboardInterrupt:
        logging.info("Sleep interrupted...")

    return True


def test_config(sim_conf, path_root, logpath):
    """ Test one running a backend and GUI with a given microscope file
    sim_conf (str): full filename of the microscope file to start
    path_root (str): beginning of the sim_conf, which is not useful for the user
    logpath (str): directory where to store the log files
    return (bool): True if no error running the whole system, False otherwise
    """
    assert sim_conf.startswith(path_root)
    sim_conf_fn = os.path.basename(sim_conf)

    # sim_conf_path = os.path.join(SIM_CONF_PATH, sim_conf)
    test_name = "test_%s" % "".join(c if c.isalnum() else '_' for c in sim_conf[len(path_root):])
    dlog_path = os.path.join(logpath, 'odemisd_%s.log' % test_name)
    guilog_path = os.path.join(logpath, 'gui_%s.log' % test_name)

    # Clear any old log files might have been left behind
    try:
        os.remove(dlog_path)
    except OSError:
        pass
    try:
        os.remove(guilog_path)
    except OSError:
        pass

    logging.info("Starting %s backend", sim_conf)
    cmd = [sys.executable] + CMD_START + [dlog_path, sim_conf]
    backend = OdemisThread("Backend %s" % sim_conf_fn, cmd)
    backend.start()

    # Wait for the back end to load
    if wait_backend_ready():
        logging.info("Starting %s GUI", sim_conf)
        cmd = [sys.executable] + CMD_GUI + [os.path.abspath(guilog_path)]
        gui = OdemisThread("GUI %s" % sim_conf_fn, cmd)
        gui.start()

        # Wait for the GUI to load
        logging.info("Waiting for 10s for the GUI to run")
        time.sleep(10)

        # TODO: do typical "stuff" in the GUI (based on the microscope type)
    else:
        gui = None

    # Trick: the backend tends to spill out some ERRORs at termination. They are
    # not really important. So for now, we check the log just before termination,
    # to only display the errors which are important.
    passed = True
    if backend.returncode != 0:  # 'ok' return code
        logging.error("Back-end failed to start, with return code %d", backend.returncode)
        return False
    elif os.path.exists(dlog_path):
        # TODO: make error/exception detection in log files more intelligent?
        # TODO: backend always start with an "ERROR" from Pyro, trying to connect to existing backend
        #  => drop to warning?
        odemisd_log = open(dlog_path).read()
        if "ERROR:" in odemisd_log:
            logging.error("Found %d %s in back-end log of %s, see %s",
                          odemisd_log.count("ERROR:"), "ERROR", sim_conf_fn, dlog_path)
            passed = False
        if "WARNING:" in odemisd_log:
            logging.warning("Found %d %s in back-end log of %s, see %s",
                          odemisd_log.count("WARNING:"), "WARNING", sim_conf_fn, dlog_path)
    else:
        logging.warning("Backend log file not found: %s", dlog_path)

    logging.info("Stopping %s", sim_conf)
    stop = OdemisThread("Stop %s" % sim_conf_fn, CMD_STOP)
    stop.start()
    stop.join()

    # If 'start' is still running, kill it forcibly (It might be stuck displaying the log
    # window)
    if backend.is_alive():
        logging.error("Back-end still running after requesting to stop")
        backend.kill()

    if gui and gui.returncode not in (-15, 143):  # SIGTERM return code, direct and via bash
        if gui.returncode == 255:
            logging.warning("Back-end might have not finish loading before the GUI was started")
        logging.error("GUI failed to start, with return code %d", gui.returncode)
        return False
    elif os.path.exists(guilog_path):
        gui_log = open(guilog_path).read()
        if "ERROR:" in gui_log:
            logging.error("Found %d %s in GUI log of %s, see %s",
                          gui_log.count("ERROR:"), "ERROR", sim_conf_fn, guilog_path)
            passed = False
        if "WARNING:" in gui_log:
            logging.warning("Found %d %s in GUI log of %s, see %s",
                          gui_log.count("WARNING:"), "WARNING", sim_conf_fn, guilog_path)
    else:
        logging.warning("GUI log file not found: %s", guilog_path)

    return passed


def _get_common_root(paths):
    """
    return (str): the longest path common to all paths
    """
    if not paths or os.sep not in paths[0]:
        return ""

    root, _ = paths[0].rsplit(os.sep, 1)
    for p in paths[1:]:
        while not p.startswith(root):
            splitted = root.rsplit(os.sep, 1)
            if len(splitted) < 2:
                return ""
            root = splitted[0]

    return root


def main(args):
    """
    args (list of str): paths to search for microscope files that will be used
      to start the backend. Only the files ending with -sim.odm.yaml are tested
    """
    parser = argparse.ArgumentParser()

    parser.add_argument("--log-path", dest="logpath", default="/tmp/",
                        help="Directory where the logs will be saved")
    parser.add_argument("paths", nargs='*',
                        help="Paths to search for microscope files that will be used "
                             "to start the backend. Only the files ending with -sim.odm.yaml are tested")
    options = parser.parse_args(args[1:])

    paths = options.paths
    if not paths:
        paths = [SIM_CONF_PATH]

    all_passed = True
    try:
        # Stop any running back-ends
        logging.info("Halting any Odemis instances...")
        halt = OdemisThread("Odemis halting thread", CMD_STOP)
        halt.start()
        halt.join()
        logging.debug("Done")

        # TODO: recursive?
        # Load the Yaml config files for the simulated hardware
        sim_conf_files = []
        for root_path in paths:
            sim_conf_files.extend(glob.glob(root_path + '/*-sim.odm.yaml'))

        if not sim_conf_files:
            raise ValueError("No simulator yaml files in %s" % (paths,))

        proot = _get_common_root(sim_conf_files)
        logging.debug("Found common conf file root = %s", proot)

        # Create a test and add it to the test case for each configuration found
        for sim_conf in sim_conf_files:
            logging.info("Testing %s", sim_conf)
            try:
                passed = test_config(sim_conf, proot, options.logpath)
                if passed:
                    print("OK", file=sys.stderr)
                else:
                    print("FAILED", file=sys.stderr)
            except Exception:
                logging.exception("Failure in testing %s", sim_conf)
                print("ERROR", file=sys.stderr)
                passed = False

            all_passed = all_passed and passed

    except ValueError as exp:
        logging.error("%s", exp)
        return 127
    except Exception:
        logging.exception("Unexpected error while performing action.")
        return 130

    if all_passed:
        return 0
    else:
        return 1

if __name__ == '__main__':
    ret = main(sys.argv)
    exit(ret)

