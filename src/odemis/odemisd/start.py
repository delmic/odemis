#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 3 Nov 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.

"""

import argparse
import logging
import os
import re
import shlex
import sys
import time
import notify2


logging.getLogger().setLevel(logging.DEBUG)

# The config file might contain additional paths for finding odemis, so we
# need to parse it and override the path before loading the rest

DEFAULT_CONFIG = {"LOGLEVEL": "1"}

# Used to parse the back-end log, and display it nicely
RE_MSG_BE_START = "Starting Odemis back-end"
RE_MSG_GUI_LOG_START = "************  Starting Odemis GUI  ************"
RE_MSG_BE_FAILURE = "Failed to instantiate the model due to component"
RE_MSG_BE_TRACEBACK = "Full traceback of the error follows"
RE_MSG_GUI_FAILURE = "Traceback (most recent call last)"
RE_MSG_BE_HEADER = r"[0-9]+-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9]+\t\S+\t\S+:"

# String as returned by xprop WM_CLASS
GUI_WM_CLASS = "Odemis"


def _add_var_config(config, var, content):
    """ Add one variable to the config, handling substitution

    Args:
        config: (dict) Configuration to add the found values to
        var: (str) The name of the variable
        content: (str) Value of the variable

    Returns:
        dict: The `config` dictionary is returned with the found values added

    """

    # variable substitution
    m = re.search(r"(\$\w+)", content)
    while m:
        subvar = m.group(1)[1:]
        # First try to use a already known variable, and fallback to environ
        try:
            subcont = config[subvar]
        except KeyError:
            try:
                subcont = os.environ[subvar]
            except KeyError:
                logging.warning("Failed to find variable %s", subvar)
                subcont = ""
        # substitute (might do several at a time, but it's fine)
        content = content.replace(m.group(1), subcont)
        m = re.search(r"(\$\w+)", content)

    logging.debug("setting %s to %s", var, content)
    config[var] = content


def parse_config(configfile):
    """  Parse `configfile` and return a dictionary of its values

    The configuration file was originally designed to be parsed as a bash script. So each line looks
    like:

        VAR=$VAR2/log

    Args:
        configfile: (str) Path to the configuration file

    Returns:
        dict str->str: Config file as name of variable -> value

    """

    config = DEFAULT_CONFIG.copy()
    f = open(configfile)
    for line in shlex.split(f, comments=True):
        tokens = line.split("=")
        if len(tokens) != 2:
            logging.warning("Can't parse '%s', skipping the line", line)
        else:
            _add_var_config(config, tokens[0], tokens[1])

    return config


odemis_config = parse_config("/etc/odemis.conf")

# Updates the python path if requested
if "PYTHONPATH" in odemis_config:
    logging.debug("PYTHONPATH from config: '%s'", odemis_config["PYTHONPATH"])
    os.environ["PYTHONPATH"] = odemis_config["PYTHONPATH"]
    # Insert at the beginning, to ensure it has higher priority
    for p in reversed(odemis_config["PYTHONPATH"].split(":")):
        if p and p not in sys.path:
            sys.path.insert(1, p)
    logging.debug("Updated sys.path to %s", sys.path)


# Continue loading the other modules, with the updated path
import threading
import subprocess
from Pyro4.errors import CommunicationError
import odemis
from odemis import model
from odemis.util import driver
from odemis.model import ST_RUNNING, ST_UNLOADED
import wx


def get_notify_object():
    notify2.init("Odemis")
    notif = notify2.Notification("")
    return notif


def display_log(type, logfile):
    """
    Shows the most recent log containing the "possible" traceback of an error in the process/file specified.

    :param type (str): Type of process that was tried to be started (e.g. "Odemis back-end", "Odemis GUI")
    :param logfile (str): Localtion of the matching log file (e.g. "/var/log/odemis.log", "~/odemis-gui.log")
    """
    title = f"Log message of {type}"
    caption = f"Failure during {type} initialisation"
    f = open(logfile, "r")
    lines = f.readlines()

    # Start at the beginning of the most recent log (skipping the log from
    # previous runs)
    for i, l in enumerate(reversed(lines)):
        if RE_MSG_BE_START in l:
            lines = lines[-(i + 1):]
            break
        elif RE_MSG_GUI_LOG_START in l:
            lines = lines[-(i + 1):]
            break

    # First show a "simple" error message, by looking for:
    # "Failed to instantiate the model due to component" (+all the backtrace)
    # "Traceback (most recent call last)" (+all the backtrace)
    # If it exists -> only show it, and have a "Show details" button

    failurelb = None
    failurebt = None
    failurele = None  # First line which is not the backtrace
    for i, l in enumerate(lines):
        if failurelb is None:
            if RE_MSG_BE_FAILURE in l:
                failurelb = i
            elif RE_MSG_GUI_FAILURE in l:
                failurelb = i  # First traceback of the GUI error
        elif failurebt is None:
            if RE_MSG_BE_TRACEBACK in l:  # Also works for the GUI
                failurebt = i  # Beginning of backtrace
        else:
            if re.match(RE_MSG_BE_HEADER, l):  # Also works for the GUI
                failurele = i  # End of backtrace
                break

    app = wx.App()  # This variable is created to show the log_frame. Required even though it is unused.
    if failurelb is not None:
        failmsg = "".join(lines[failurelb:failurele])
        failmsg += "\nWould you like to see the full log message now?"
        box = wx.MessageDialog(None, failmsg,
                               caption,
                               wx.YES_NO | wx.YES_DEFAULT | wx.ICON_ERROR | wx.CENTER)
        ans = box.ShowModal()  # Waits for the window to be closed
        if ans == wx.ID_NO:
            return

    fullmsg = "".join(lines)

    # At least, skip everything not related to this last run
    log_frame = create_log_frame(title, fullmsg)
    log_frame.ShowModal()

def create_log_frame(title, msg):
    frame = wx.Dialog(None, title=title, size=(800, 800),
                      style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
    text = wx.TextCtrl(frame, value=msg, style=wx.TE_MULTILINE | wx.TE_READONLY)

    textsizer = wx.BoxSizer()
    textsizer.Add(text, 1, flag=wx.ALL | wx.EXPAND)

    btnsizer = frame.CreateButtonSizer(wx.CLOSE)

    sizer = wx.BoxSizer(wx.VERTICAL)
    sizer.Add(textsizer, 1, flag=wx.ALL | wx.EXPAND, border=5)
    sizer.Add(btnsizer, 0, flag=wx.EXPAND | wx.BOTTOM, border=5)
    frame.SetSizer(sizer)

    # Show the end of the log (which is most likely showing the error)
    text.ShowPosition(text.GetLastPosition())
    frame.CenterOnScreen()
    return frame

class BackendStarter(object):
    def __init__(self, config, nogui=False):
        self._config = config

        # For displaying wx windows
        logging.debug("Creating app")
        self._app = wx.App()

        self._nogui = nogui

        self._notif = get_notify_object()

        # For listening to component states
        self._mic = None
        self._comp_state = {}  # str -> state
        self._known_comps = []  # str (name of component)
        self._backend_done = threading.Event()

        self._component_frame = None

        # For reacting to SIGNINT (only once)
        # self._main_thread = threading.current_thread()

    def _create_component_frame(self, micname):
        frame = wx.Dialog(None, title="Starting Odemis (v%s) for %s..." % (odemis.__version__, micname),
                          size=(800, 800),
                          # No close button
                          style=wx.CAPTION | wx.RESIZE_BORDER)
        frame.SetMinClientSize((400, 200)) 
        self._list = wx.ListCtrl(frame,
                                 style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_NO_SORT_HEADER)
        self._list.InsertColumn(0, "Component")
        self._list.InsertColumn(1, "Status")
        self._list.SetColumnWidth(0, 200)
        self._list.SetColumnWidth(1, 590)

        textsizer = wx.BoxSizer()
        textsizer.Add(self._list, 1, flag=wx.ALL | wx.EXPAND)

        btnsizer = frame.CreateButtonSizer(wx.CANCEL)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(textsizer, 1, flag=wx.ALL | wx.EXPAND, border=5)
        sizer.Add(btnsizer, 0, flag=wx.EXPAND | wx.BOTTOM, border=5)
        frame.SetSizer(sizer)

        frame.CenterOnScreen()
        return frame

    def show_popup(self, summary, message="", icon="dialog-info"):
        self._notif.update(summary, message, icon)
        self._notif.show()

    def proc_communicate(self, p):
        """
        Does the same as "out, err = p.communicate()", but (almost) immediately
        returns when the process ends.
        returns (list of strings, list of strings): out, err
        """
        # To be able to directly pass the bytes to the output (display)
        if sys.version_info.major >= 3:
            raw_output = sys.stdout.buffer
        else:  # Python 2
            raw_output = sys.stdout

        def read_file(f, store):
            try:
                for l in f:
                    store.append(l)
                    # logging.debug("Read %d bytes", len(l))
                    # Also pass on the output
                    raw_output.write(l)
            except Exception:
                logging.exception("failed to read")
            finally:
                logging.info("read ended")

        out = []
        err = []
        tout = threading.Thread(target=read_file, args=(p.stdout, out))
        tout.daemon = True
        tout.start()
        terr = threading.Thread(target=read_file, args=(p.stderr, err))
        terr.daemon = True
        terr.start()

        p.wait()
        time.sleep(0.1)  # Make sure we got all the data from the pipes
        out = [o.decode("utf-8", "ignore_error") for o in out]
        err = [e.decode("utf-8", "ignore_error") for e in err]
        return out, err

    def start_backend(self, modelfile):
        """ Start the backend and returns when it's fully instantiated or has failed to do so

        It will display a simple window indicating its progress.

        Args:
            modelfile: (str) Path to the model definition Yaml file

        """
        # install cgroup, for memory protection
        if (
            not os.path.exists("/sys/fs/cgroup/memory/odemisd") and
            os.path.exists("/usr/bin/cgcreate")
        ):
            logging.info("Creating cgroup")
            subprocess.call(["sudo", "/usr/bin/cgcreate", "-a", ":odemis", "-g", "memory:odemisd"])

        logging.info("Starting back-end...")
        odemisd_cmd = ["sudo", "odemisd", "--daemonize",
                 "--log-level", self._config["LOGLEVEL"],
                 "--log-target", self._config["LOGFILE"],
                 modelfile]
        logging.debug("Running: %s", " ".join(odemisd_cmd))

        # odemisd likes to start as root to be able to create /var/run files, but then
        # drop its privileges to the odemis group
        # Note: sudo drops all the env variables, so /etc/odemis.conf might be
        # interpreted differently in odemisd than what we've just read
        p = subprocess.Popen(odemisd_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # block until the backend returns (typically because it forks a daemon)
        out, err = self.proc_communicate(p)
        if p.returncode != 0:
            # If it immediately fails, it's easy
            logging.error("Backend returned code %s", p.returncode)
            self.show_popup("Odemis back-end failed to start",
                            "For more information type odemis-start in a terminal.",
                            "dialog-warning")
            show_error_box("Error starting Odemis back-end",
                           "Unexpected error (%d) while starting Odemis back-end:\n"
                           "%s\n"
                           "Try starting Odemis again, and use the \"Report a "
                           "problem\" function if the issue continues.\n\n"
                           "The full message is the following:\n %s" %
                           (p.returncode, err[-1], "".join(err)))
            raise ValueError("Starting back-end failed")

    # def _on_sigint(self, signum, frame):
    #     # TODO: ensure this is only processed by the main thread?
    #     if threading.current_thread() == self._main_thread:
    #         logging.warning("Received signal %d: stopping", signum)
    #         raise KeyboardInterrupt("Received signal %d" % signum)
    #     else:
    #         logging.info("Skipping signal %d in sub-thread", signum)

    def wait_backend_is_ready(self):
        """ Block until the back-end is fully ready (when all the components are ready)

        Raises:
            IOError: If the back-end eventually fails to start

        """

        # Get a connection to the back-end
        end_time = time.time() + 5  # 5s max to start the backend
        backend = None

        while self._mic is None:
            try:
                backend = model.getContainer(model.BACKEND_NAME, validate=False)
                self._mic = backend.getRoot()
            except (IOError, CommunicationError) as exp:
                if (
                    isinstance(exp, CommunicationError) and
                    "Permission denied" in str(exp)
                ):
                    raise  # No hope
                if time.time() > end_time:
                    logging.exception("Timeout waiting for back-end to start")
                    self.show_popup(
                        "Odemis back-end failed to start",
                        ("For more information look at the log messages in %s "
                         "or type odemis-start in a terminal.") % self._config["LOGFILE"],
                        "dialog-warning"
                    )
                    raise IOError("Back-end failed to start")
                else:
                    logging.debug("Waiting a bit more for the backend to appear")
                    time.sleep(1)

        try:
            self._mic.ghosts.subscribe(self._on_ghosts, init=True)
        except (IOError, CommunicationError):
            self.show_popup(
                "Odemis back-end failed to start",
                ("For more information look at the log messages in %s "
                 "or type odemis-start in a terminal.") % self._config["LOGFILE"],
                "dialog-warning"
            )
            raise IOError("Back-end failed to fully instantiate")

        # create a window with the list of the components
        self._component_frame = self._create_component_frame(self._mic.name)

        # In theory Python raise KeyboardInterrupt on SIGINT, which is what we
        # need. But that doesn't happen if in a wait(), and in addition, when
        # there are several threads, only one of them receives the exception.
        # signal.signal(signal.SIGINT, self._on_sigint)

        # Check in background if the back-end is ready
        check_thread = threading.Thread(target=self._watch_backend_status, args=(backend, self._mic))
        check_thread.start()

        # Show status window until the backend is ready (or failed to start)
        ret = self._component_frame.ShowModal()
        # Blocking until the window is closed. It return either:
        # * ID_CANCEL => the user doesn't want to start finally
        # * ID_EXIT => Error in the backend
        # * ID_OK => the backend is ready
        logging.debug("Window closed with: %d", ret)
        # TODO: detect Ctrl+C and interpret as pressing "Cancel"
        # except KeyboardInterrupt:
        #     # self._frame.Destroy()
        #     self._mic.ghosts.unsubscribe(self._on_ghosts)
        #     self._frame.EndModal(wx.ID_CANCEL)
        #     logging.info("Stopping the backend")
        #     backend.terminate()
        #     self._backend_done.set()
        #     raise

        # make sure check_thread and ghost listener stop
        self._backend_done.set()
        try:
            if ret != wx.ID_EXIT:
                self._mic.ghosts.unsubscribe(self._on_ghosts)
        except Exception:
            # Can happen if the backend failed
            pass

        # status = driver.get_backend_status()
        # if status == driver.BACKEND_RUNNING:
        if ret == wx.ID_OK:
            self.show_popup("Odemis back-end successfully started",
                            "" if self._nogui else "Graphical interface will now start.",
                            "dialog-info")
        # elif status in (driver.BACKEND_DEAD, driver.BACKEND_STOPPED):
        elif ret == wx.ID_EXIT:
            self.show_popup(
                "Odemis back-end failed to start",
                ("For more information look at the log messages in %s "
                 "or type odemis-start in a terminal.") % self._config["LOGFILE"],
                "dialog-warning")
            raise IOError("Back-end failed to fully instantiate")
        elif ret == wx.ID_CANCEL:
            logging.info("Stopping the backend")
            backend.terminate()
            self.show_popup("Odemis back-end start cancelled")
            raise ValueError("Back-end start cancelled by the user")
        else:
            logging.warning("Unexpected return code %d", ret)

    def _watch_backend_status(self, backend, mic):
        """ Close the component frame when the backend is fully up or down (because of errors) """
        ret = wx.ID_OK

        while True:
            # Sleep a bit
            self._backend_done.wait(1)
            try:
                backend.ping()

                # Hack, to work-around issue in _on_ghosts() sometimes blocking:
                # Check ourselves if all the components are started, so that even
                # in case _on_ghosts() is blocked, we at least move on eventually.
                if not mic.ghosts.value:
                    self._backend_done.set()
            except (IOError, CommunicationError):
                logging.info("Back-end failure detected")
                ret = wx.ID_EXIT
                break
            if self._backend_done.is_set():
                logging.debug("Back-end observation over")
                break

        wx.CallAfter(self._component_frame.EndModal, ret)

    def _show_component(self, name, state):
        print("Component %s: %s" % (name, state))
        # It needs to run in the GUI thread
        wx.CallAfter(self._show_component_in_frame, name, state)

    def _show_component_in_frame(self, name, state):
        try:
            index = self._known_comps.index(name)
        except ValueError:
            index = len(self._known_comps)
            self._known_comps.append(name)
            self._list.InsertItem(index, name)

        if isinstance(state, Exception):
            colour = "#DD3939"  # Red
        elif state == ST_RUNNING:
            colour = "#39DD39"  # Green
        elif state == ST_UNLOADED:
            colour = "#808080"  # Grey
        else:
            colour = "#000000"  # Black
        item = self._list.GetItem(index)
        item.SetTextColour(colour)
        self._list.SetItem(item)

        # Most of the states can be directly displayed for the user, but
        # "unloaded" is weird for a hardware component
        if state == ST_UNLOADED:
            txt_state = u"uninitialized"
        else:
            txt_state = u"%s" % state

        self._list.SetItem(index, 1, txt_state)

    def _on_ghosts(self, ghosts):
        """ Called when the .ghosts changes """
        # The components running fine
        for c in self._mic.alive.value:
            # FIXME: reading .state sometimes blocks, somewhere in the Python server,
            # usually when the "SEM scan interface" starts.
            state = c.state.value
            if self._comp_state.get(c.name) != state:
                self._comp_state[c.name] = state
                self._show_component(c.name, state)

        # Now the defective ones
        for cname, state in ghosts.items():
            if isinstance(state, Exception):
                # Exceptions are different even if just a copy
                statecmp = str(state)
            else:
                statecmp = state
            if self._comp_state.get(cname) != statecmp:
                self._comp_state[cname] = statecmp
                self._show_component(cname, state)

        # No more ghosts, means all hardware is ready
        if not ghosts:
            self._backend_done.set()

    def display_backend_log(self):
        display_log("Odemis back-end", self._config["LOGFILE"])


def find_window(wm_class):
    """
    wm_class (str): the WM_CLASS to match (eg, as reported by xprop)
    return (bool): True if at least one window is found with this class
    """
    # Ask xprop for the window (but without actually reading the properties)
    try:
        found = subprocess.call(["/usr/bin/xprop", "-name", wm_class],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as ex:
        # In case xprop is not installed (unlikely)
        logging.warning("Failed to search for %s: %s", wm_class, ex)
        return False

    # Found -> 0
    # Not found -> error number (1)
    return found == 0


def show_error_box(caption, message):
    """
    Shows an error message in a graphical window.
    It blocks until the window is closed.
    caption (str): the window title
    message (str): the error text
    """
    app = wx.App()
    box = wx.MessageDialog(None, message, caption, wx.OK | wx.ICON_ERROR | wx.CENTER)
    box.ShowModal() # Waits for the window to be closed
    app.Yield() # Hides the window


def run_model_selector(cmd_str):
    """
    Gets the model file from the model selector
    cmd_str (str): the whole command line, which will be parsed (as in a shell)
    return (str): the model filename to use
    raise ValueError: if the model selector failed
    """
    cmd = shlex.split(cmd_str)
    logging.debug("Getting the model filename using %s", cmd)
    try:
        out = subprocess.check_output(cmd).decode("utf-8", "ignore_error").splitlines()
        if not out:
            show_error_box("Error starting Odemis back-end",
                           "No microscope file name provided by MODEL_SELECTOR %s.\n\n"
                           "Check all the hardware is connected and try again to start Odemis." % (cmd,))
            raise ValueError("No microscope file name provided by %s" % (cmd,))
        elif len(out) > 1:
            logging.warning("Model selector returned multiple lines: %s", out)
        modelfile = out[0].strip()
        logging.info("Will use model %s", modelfile)
    except subprocess.CalledProcessError as ex:
        if ex.output:
            output = ":\n%s" % (ex.output.decode("utf-8", "ignore_error").strip(),)
        else:
            output = "."
        show_error_box("Error starting Odemis back-end",
                       "Failed to pick the microscope file with MODEL_SELECTOR.\n"
                       "The model selector (%s) exited with error %d%s\n\n"
                       "Check all the hardware is connected and try again to start Odemis." %
                       (ex.cmd[0], ex.returncode, output))
        raise ValueError("Failed to pick the microscope file: %s" % (ex,))

    return modelfile


def main(args):
    """ Parse the command line arguments and launch the Odemis daemon

    Args:
        args ([str]): The arguments passed to the executable

    Returns:
        int: Value to return to the OS as program exit code

    """

    # arguments handling
    parser = argparse.ArgumentParser(prog="odemis-start",
                                     description="Odemis starter program")

    parser.add_argument('model_conf', nargs='?', help="Model definition (yaml) file to use")
    parser.add_argument('-s', '--selector', dest='model_sel',
                        help="Model selector command (instead of using a model definition)")
    parser.add_argument('-n', '--nogui', dest='nogui', action='store_true',
                        help="Don't launch the GUI after the back end has started")
    parser.add_argument('-l', '--log-target', dest='logtarget',
                        help="Location of the back end log file")

    options = parser.parse_args(args[1:])

    if options.model_sel and options.model_conf:
        raise ValueError("Cannot have both a model selector and model definition")

    # Use the log level for ourselves first
    try:
        loglevel = int(odemis_config["LOGLEVEL"])
        if options.logtarget:
            odemis_config['LOGFILE'] = options.logtarget
    except ValueError:
        loglevel = 1
        odemis_config["LOGLEVEL"] = "%d" % loglevel

    # Set up logging before everything else
    if loglevel < 0:
        logging.error("Log-level must be positive.")
        return 127
    # TODO: allow to put logging level so low that nothing is ever output
    loglevel_names = [logging.WARNING, logging.INFO, logging.DEBUG]
    loglevel = loglevel_names[min(len(loglevel_names) - 1, loglevel)]
    logging.getLogger().setLevel(loglevel)

    # pyrolog = logging.getLogger("Pyro4")
    # pyrolog.setLevel(min(pyrolog.getEffectiveLevel(), logging.DEBUG))

    try:
        status = driver.get_backend_status()
        if status != driver.BACKEND_RUNNING:
            starter = BackendStarter(odemis_config, options.nogui)
            # TODO: if backend running but with a different model, also restart it
            if status == driver.BACKEND_DEAD:
                logging.warning("Back-end is not responding, will restart it...")
                # subprocess.call(["/usr/bin/pkill", "-f", config["BACKEND"]])
                subprocess.call(["sudo", "/usr/bin/odemis-stop"])
                time.sleep(3)

            try:
                if status in (driver.BACKEND_DEAD, driver.BACKEND_STOPPED):
                    starter.show_popup("Starting Odemis back-end")

                    # Get the model file in this order:
                    # 1. the command line (either model selector or model file)
                    # 2. the model file
                    # 3. the model selector
                    if options.model_conf:
                        modelfile = options.model_conf
                    elif options.model_sel:
                        modelfile = run_model_selector(options.model_sel)
                    elif "MODEL" in odemis_config:
                        modelfile = odemis_config["MODEL"]
                        if "MODEL_SELECTOR" in odemis_config:
                            logging.warning("Both MODEL and MODEL_SELECTOR defined in odemis.conf, using MODEL")
                    elif "MODEL_SELECTOR" in odemis_config:
                        modelfile = run_model_selector(odemis_config["MODEL_SELECTOR"])
                    else:
                        raise ValueError("No microscope model specified")

                    starter.start_backend(modelfile)
                if status in (driver.BACKEND_DEAD, driver.BACKEND_STOPPED, driver.BACKEND_STARTING):
                    starter.wait_backend_is_ready()
            except ValueError:
                raise  # Typically cancelled by user
            except IOError:
                starter.display_backend_log()
                raise
            except Exception as ex:
                # Something went very wrong (ie, back-end is blocked half-way
                # starting) => stop backend
                proc = subprocess.Popen(["sudo", "/usr/bin/odemis-stop"])
                show_error_box("Error starting Odemis back-end",
                               "Unexpected error while starting Odemis back-end:\n"
                               "%s\n\n"
                               "Try starting Odemis again, and use the \"Report a "
                               "problem\" function if the issue continues." % ex)
                proc.wait()
                raise
        else:
            logging.debug("Back-end already started, so not starting again")

        if not options.nogui:
            if status == driver.BACKEND_RUNNING and find_window(GUI_WM_CLASS):
                # TODO: bring window to focus? Unminimize if it is minimized
                # (see wmctrl or xdotool)
                logging.info("GUI already started, so not starting again")
            else:
                # Return when the GUI is done
                logging.info("Starting the GUI...")

                gui_start_time = time.time()
                try:
                    subprocess.check_call(["odemis-gui", "--log-level", odemis_config["LOGLEVEL"]])
                except subprocess.CalledProcessError as error:
                    # Only show a trace back when the GUI process stops within 10 seconds, then the cause is expected
                    # to be an error not the user closing the GUI/ a full stop. Only errors that prevents the GUI
                    # window from even appearing should be caught.
                    if time.time() < gui_start_time + 10:
                        error_code_gui = error.returncode

                        notif = get_notify_object()
                        notif.update("Failed to start the GUI",
                                     f"For more information look at the log file odemis-gui.log in the home folder.'",
                                     "dialog-warning")
                        notif.show()

                        display_log("Odemis GUI", os.path.expanduser("~/odemis-gui.log"))

                        return error_code_gui
                    else: # The GUI closed more than 10 seconds after starting. A normal startup is assumed.
                        logging.warning(f"The GUI closed with the error code {error.returncode}.")

    except ValueError as exp:
        logging.error("%s", exp)
        return 127
    except IOError as exp:
        logging.error("%s", exp)
        return 129
    except Exception:
        logging.exception("Unexpected error while performing action.")
        return 130

    return 0

if __name__ == '__main__':
    ret_code = main(sys.argv)
    exit(ret_code)
