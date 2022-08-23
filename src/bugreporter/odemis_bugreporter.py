#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 8 October 2018

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

import base64
from collections import OrderedDict
from concurrent import futures
from concurrent.futures import Future
import configparser
import csv
from datetime import datetime
from future.moves.urllib.request import Request, urlopen
from glob import glob
import json
import logging
import notify2
import os
import re
import shlex
import socket
from subprocess import CalledProcessError
import subprocess
import time
import webbrowser
import wx
import zipfile

logging.getLogger().setLevel(logging.DEBUG)
DEFAULT_CONFIG = {"LOGLEVEL": "1"}

OS_TICKET_URL = "https://support.delmic.com/api/tickets.json"
CONFIG_FN = "bugreporter.config"  # Configuration file
NAMES_FN = "bugreporter_users.tsv"  # File to store the names and email address

TEST_SUPPORT_TICKET = (os.environ.get("TEST_SUPPORT_TICKET", 0) != 0)
RESPONSE_SUCCESS = 201
MAX_USERS = 50
GDPR_TEXT = ("When reporting an issue, technical data from the computer will be sent " +
    "to Delmic B.V. In addition to your name and email address, the data can " +
    "also contain some identifiable information about your work (e.g, " +
    "filenames of acquisitions). The sole purpose of collecting this data " +
    "is to diagnose issues and improve the quality of the system. The data may " +
    "be stored up to five years. The data will always be stored confidentially, " +
    "and never be shared with any third parties or used for any commercial purposes.")
DESCRIPTION_DEFAULT_TXT = ("Ways to reproduce the problem:\n1.\n2.\n3.\n\nCurrent behaviour:\n\n" +
    "Expected behaviour:\n\nAdditional Information (e.g. reproducibility, severity):\n")
# Constants for checking output of odemis-cli --check. Don't import from odemis.util.driver
# to keep the bugreporter as independent as possible from odemis.
BACKEND_RUNNING = 0
BACKEND_STOPPED = 2
BACKEND_STARTING = 3

# Topic IDs are used to indicate which "department" will be taking care of the ticket.
# They can be found from the urls at https://support.delmic.com/scp/helptopics.php
# (you need to be admin), or in the source code of https://support.delmic.com/open.php .
TOPIC_ID_DEFAULT = 10  # fallback to CL
TOPIC_IDS = {
    "cl": 10,  # CL solution (SECOM, SPARC...)
    "test": 12,  # for testing only (no one receives these tickets messages!)
    "asia": 13,  # Delmic Asia
    "cryo": 14,  # Cryo solution (METEOR, ENZEL...)
    "fast": 15,  # Fast imaging (FastEM...)
}


# The next to functions will be needed to parse odemis.conf
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
    try:
        f = open(configfile)
        for line in shlex.split(f, comments=True):
            tokens = line.split("=")
            if len(tokens) != 2:
                logging.warning("Can't parse '%s', skipping the line", line)
            else:
                _add_var_config(config, tokens[0], tokens[1])
    except Exception:
        logging.exception("Failed to parse the config file %s", configfile)

    return config


class OdemisBugreporter(object):
    """
    Class to create a bugreport. Contains functions for compressing the odemis files, opening
    a window asking for a bugreport description, and uploading the bugreport to
    osticket.
    """
    def __init__(self):

        self.zip_fn = None  # (str) Path to zip file.
        self._executor = futures.ThreadPoolExecutor(max_workers=4)

    def run(self):
        """
        Runs the compression and ticket creation in a separate thread. Starts the GUI.
        The create_ticket function waits until the compression is done and the user 
        has finished the report description before sending.
        """
        # Take a screenshot if the GUI is there
        ret_code = subprocess.call(['pgrep', '-f', 'odemis.gui.main'])
        if ret_code == 0:
            scfn = "/tmp/odemis-bug-screenshot.png"
            try:
                subprocess.call(['gnome-screenshot', '-f', scfn])
            except Exception as e:
                logging.warning("Failed to take a screenshot with Exception %s" % e)

        # Compress files in the background and set up ticket creation
        self._compress_files_f = self._executor.submit(self.compress_files)

        # Ask for user description in GUI
        app = wx.App()
        self.gui = BugreporterFrame(self)
        app.MainLoop()

        self._executor.shutdown()

    def create_ticket(self, api_key, fields, files=None):
        """
        Create ticket on osTicket server.
        :arg api_key: (String) API-Key
        :arg fields: (String --> String) dictionary containing keys name, email, subject, message
        :arg files: (None or list of Strings) pathname of zip files that should be attached
        :returns: (int) response code
        :raises ValueError: ticket upload failed
        :raises urllib.error.HTTPError: key not accepted
        :raises urllib.error.URLError: connection problem
        """
        if not files:
            files = []
        fields["attachments"] = []
        for fn in files:
            # File is open as bytes and converted to "bytes" with base64.
            # We convert it then to a string by "decoding" it from ascii.
            # The string is inserted with the rest of the dict fields (all strings).
            # The dict is converted to JSON, which is then encoded into bytes using UTF-8 encoding.
            # We probably could avoid the bytes -> string -> bytes conversion,
            # but it's easier as-is, and doesn't seem to be too costly.
            with open(fn, "rb") as f:
                encoded_data = base64.b64encode(f.read()).decode("ascii")
            att_desc = {os.path.basename(fn): "data:application/zip;base64,%s" % encoded_data}
            fields["attachments"].append(att_desc)

        description = json.dumps(fields).encode("utf-8")
        # data must be bytes, but the headers can be str or bytes
        req = Request(OS_TICKET_URL, data=description, headers={"X-API-Key": api_key})
        f = urlopen(req)
        response = f.getcode()
        f.close()
        if response == RESPONSE_SUCCESS:
            return
        else:
            raise ValueError('Ticket creation failed with error code %s.' % response)
        
    def search_api_key(self):
        """
        Searches for a valid osTicket key on the system. First, the customer key
        is checked, then the fallback.
        """
        customer_key_path = os.path.join(os.path.expanduser(u"~"), '.local', 'share',
                                         'odemis', 'osticket.key')
        fallback_key_path = '/usr/share/odemis/osticket.key'
        if os.path.isfile(customer_key_path):
            with open(customer_key_path, 'r') as key_file:
                api_key = key_file.read().strip('\n')
        elif os.path.isfile(fallback_key_path):
            with open(fallback_key_path, 'r') as key_file:
                api_key = key_file.read().strip('\n')
        else:
            raise LookupError("osTicket key not found.")
        return api_key

    def _get_future_output(self, command, timeout=None):
        """
        Runs a external command in a future, which returns its output
        command (list of str)
        timeout (None or 0<float): maximum time in second to wait for the command
          to end. If the command takes longer, it will be stopped, and the output
          generated so far will be returned.
        return Future returning a str, or raising the error
        """
        if timeout is not None:
            # subprocess doesn't have timeout argument in python 2.x, so we use
            # the unix command instead
            command = ["timeout", "%f" % timeout] + command
        return self._executor.submit(subprocess.check_output, command)

    def compress_files(self):
        """
        Compresses the relevant files to a zip archive which is saved in /home.
        :modifies self.zip_fn: filename of the zip archive
        """
        hostname = socket.gethostname()
        home_dir = os.path.expanduser(u"~")
        t = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.zip_fn = os.path.join(home_dir, 'Desktop', '%s-odemis-log-%s.zip' % (hostname, t))

        logging.debug("Will store bug report in %s", self.zip_fn)
        LOGFILE_BACKEND = '/var/log/odemis.log'
        files = [LOGFILE_BACKEND, os.path.join(home_dir, 'odemis-gui.log'),
                 os.path.join(home_dir, 'odemis-gui.log.1'), '/etc/odemis.conf', '/var/log/syslog',
                 os.path.join(home_dir, 'odemis-mic-selector.log'), '/tmp/odemis-bug-screenshot.png',
                 os.path.join(home_dir, 'odemis-model-selector.log'),  # another name for odemis-mic-selector.log
                 '/etc/odemis-settings.yaml']

        # If odemis.log is < 2M, it might not have enough info, so also get odemis.log.1
        if not os.path.exists(LOGFILE_BACKEND) or os.path.getsize(LOGFILE_BACKEND) < 2e6:
            files.append(LOGFILE_BACKEND + ".1")

        try:
            # Save yaml file, call MODEL_SELECTOR if needed
            odemis_config = parse_config("/etc/odemis.conf")
            models = []
            if odemis_config.get("MODEL"):
                models = [odemis_config["MODEL"]]
            elif odemis_config.get("MODEL_SELECTOR"):
                logging.debug("Calling %s", odemis_config["MODEL_SELECTOR"].rstrip().split(' '))
                try:
                    cmd = shlex.split(odemis_config["MODEL_SELECTOR"])
                    logging.debug("Getting the model filename using %s", cmd)
                    out = subprocess.check_output(cmd).decode("utf-8", "ignore_error").splitlines()
                    if out:
                        models = [out[0].strip()]
                    else:
                        logging.warning("Model selector failed to pick a model")
                except Exception as ex:
                    logging.warning("Failed to run model selector: %s", ex)

            if not models:
                # just pick every potential microscope model
                models = glob(os.path.join(odemis_config['CONFIGPATH'], '*.odm.yaml'))

            files.extend(models)

            # Add the latest overlay-report if it's possibly related (ie, less than a day old)
            overlay_reps = glob(os.path.join(home_dir, 'odemis-overlay-report', '*'))
            overlay_reps.sort(key=os.path.getmtime)
            if overlay_reps and (time.time() - os.path.getmtime(overlay_reps[-1])) / 3600 < 24:
                files.append(overlay_reps[-1])

            # Add the latest DELPHI calibration report if it's possibly related (ie, less than a day old)
            delphi_calib_reps = glob(os.path.join(home_dir, 'delphi-calibration-report', '*'))
            delphi_calib_reps.sort(key=os.path.getmtime)
            if delphi_calib_reps and (time.time() - os.path.getmtime(delphi_calib_reps[-1])) / 3600 < 24:
                files.append(delphi_calib_reps[-1])

            # Save hw status (if available)
            try:
                ret_code = subprocess.call(['odemis-cli', '--check'])
            except Exception as ex:
                logging.warning("Failed to run check backend status: %s", ex)
                ret_code = BACKEND_STOPPED
            outputs = {}
            if ret_code in (BACKEND_RUNNING, BACKEND_STARTING):
                outputs["odemis-hw-status.txt"] = self._get_future_output(['odemis-cli', '--list-prop', '*'], timeout=60)

            # Save USB hardware, processes, kernel name, IP address
            outputs["lsusb.txt"] = self._get_future_output(['/usr/bin/lsusb', '-v'], timeout=60)
            outputs["ps.txt"] = self._get_future_output(['/bin/ps', 'aux'], timeout=60)
            outputs["uname.txt"] = self._get_future_output(['/bin/uname', '-a'], timeout=5)
            outputs["ip.txt"] = self._get_future_output(['/bin/ip', 'address'], timeout=30)
            outputs["memory.txt"] = self._get_future_output(['/usr/bin/free'], timeout=5)
            outputs["df.txt"] = self._get_future_output(['/bin/df'], timeout=10)

            # Compress files
            with zipfile.ZipFile(self.zip_fn, "w", zipfile.ZIP_DEFLATED) as archive:
                for fn, ft in outputs.items():
                    try:
                        outp = ft.result(65)
                        archive.writestr(fn, outp)
                    except CalledProcessError as ex:
                        if ex.output:  # most probably exited due to timeout
                            logging.warning("Incomplete output of %s: %s", fn, ex)
                            archive.writestr(fn, ex.output)
                        else:  # Nothing received, probably the command is really wrong
                            logging.warning("Cannot save %s status: %s", fn, ex)
                    except Exception as ex:
                        logging.warning("Cannot save %s status: %s", fn, ex)

                for f in files:
                    if os.path.isfile(f):
                        logging.debug("Adding file %s", f)
                        archive.write(f, os.path.basename(f))
                    elif os.path.isdir(f):
                        logging.debug("Adding directory %s", f)
                        dirnamef = os.path.dirname(f)
                        for top, _, files in os.walk(f):
                            for subf in files:
                                full_path = os.path.join(top, subf)
                                archive.write(full_path, full_path[len(dirnamef) + 1:])
                    else:
                        logging.warning("Bugreporter could not find file %s", f)
        except Exception:
            logging.exception("Failed to store bug report")
            raise

    def _set_description(self, name, email, subject, message, topic_id: int):
        """
        Saves the description parameters for the ticket creation in a txt file, compresses
        the file and calls self.create_ticket.
        :arg name, email, summary, description: (String) arguments for corresponding dictionary keys
        topic_id (int): the topic ID (see TOPIC_IDS). If TEST_SUPPORT_TICKET is set,
          it's overridden by the "test" topic.
        """
        # Create ticket with special id when testing
        if TEST_SUPPORT_TICKET:
            logging.debug("Changing topic ID from %s to test ID", topic_id)
            topic_id = TOPIC_IDS["test"]

        self._compress_files_f.result()
        report_description = {'name': name,
                              'email': email,
                              'subject': subject,
                              'message': message,
                              'topicId': topic_id
        }

        description = (u'Name: %s\n' % name +
                       u'Email: %s\n' % email +
                       u'Summary: %s\n\n' % subject +
                       u'Description:\n%s' % message
                       )

        with zipfile.ZipFile(self.zip_fn, "a", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('description.txt', description.encode("utf-8"))
        api_key = self.search_api_key()
        wx.CallAfter(self.gui.wait_lbl.SetLabel, "Sending report...")
        self.create_ticket(api_key, report_description, [self.zip_fn])

    def send_report(self, name: str, email: str, subject: str, message: str, topic_id: int=TOPIC_ID_DEFAULT) -> Future:
        """
        Calls _set_description in a thread.
        :arg name, email, summary, description: (String) arguments for corresponding dictionary keys
        topic_id (int): the topic ID (see TOPIC_IDS). If TEST_SUPPORT_TICKET is set,
          it's overridden by the "test" topic.
        return Future (-> None): the handle to follow the report upload
        """
        return self._executor.submit(self._set_description, name, email, subject, message, topic_id)


class BugreporterFrame(wx.Frame):

    def __init__(self, controller):
        super(BugreporterFrame, self).__init__(None, title="Odemis problem description", size=(800, 900))

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Add input fields for name, email, summary and description
        name_sizer = wx.BoxSizer(wx.HORIZONTAL)
        name_lbl = wx.StaticText(panel, wx.ID_ANY, "Name:")
        name_ctrl = wx.TextCtrl(panel, wx.ID_ANY, size=(500, 23))
        name_ctrl.Bind(wx.EVT_TEXT, self.on_name_text)
        name_ctrl.Bind(wx.EVT_KEY_DOWN, self.on_name_key_down)
        name_ctrl.Bind(wx.EVT_KILL_FOCUS, self.on_name_focus)
        name_sizer.Add(name_lbl, 5, wx.EXPAND | wx.ALIGN_LEFT | wx.ALL, 10)
        name_sizer.Add(name_ctrl, 10, wx.EXPAND | wx.ALIGN_LEFT | wx.ALL, 5)
        sizer.Add(name_sizer)
        email_sizer = wx.BoxSizer(wx.HORIZONTAL)
        email_lbl = wx.StaticText(panel, wx.ID_ANY, "Email:")
        email_ctrl = wx.TextCtrl(panel, wx.ID_ANY, size=(500, 23))
        email_sizer.Add(email_lbl, 5, wx.EXPAND | wx.ALIGN_LEFT | wx.ALL, 10)
        email_sizer.Add(email_ctrl, 10, wx.EXPAND | wx.ALIGN_LEFT | wx.ALL, 5)
        sizer.Add(email_sizer)
        summary_sizer = wx.BoxSizer(wx.HORIZONTAL)
        summary_lbl = wx.StaticText(panel, wx.ID_ANY, "Summary:")
        summary_ctrl = wx.TextCtrl(panel, wx.ID_ANY, size=(500, 23))
        summary_sizer.Add(summary_lbl, 5, wx.EXPAND | wx.ALIGN_LEFT | wx.ALL, 10)
        summary_sizer.Add(summary_ctrl, 10, wx.EXPAND | wx.ALIGN_LEFT | wx.ALL, 5)
        sizer.Add(summary_sizer)
        description_lbl = wx.StaticText(panel, wx.ID_ANY, "Description:")
        description_ctrl = wx.TextCtrl(panel, wx.ID_ANY, value=DESCRIPTION_DEFAULT_TXT,
                                       size=(self.GetSize()[0], -1), style=wx.TE_MULTILINE)
        sizer.Add(description_lbl, 0, wx.EXPAND | wx.ALIGN_LEFT | wx.ALL, 10)
        sizer.Add(description_ctrl, 10, wx.EXPAND | wx.ALIGN_LEFT | wx.LEFT | wx.RIGHT, 10)

        # GDPR text
        gdpr_sizer = wx.BoxSizer(wx.HORIZONTAL)
        gdpr_lbl = wx.StaticText(panel, -1, GDPR_TEXT)
        gdpr_lbl.Wrap(gdpr_lbl.GetSize().width)
        gdpr_lbl.SetMinSize((-1, 100))  # High enough to fit all the text
        font = wx.Font(10, wx.NORMAL, wx.ITALIC, wx.NORMAL)
        gdpr_lbl.SetFont(font)
        gdpr_sizer.Add(gdpr_lbl, 10, wx.EXPAND | wx.ALIGN_LEFT | wx.ALL, 10)
        sizer.Add(gdpr_sizer, 0, wx.EXPAND)

        # Status update label
        # TODO: replace by an animated throbber
        wait_lbl = wx.StaticText(panel, wx.ID_ANY, "")
        sizer.Add(wait_lbl, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        # Cancel and send report buttons
        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        cancel_btn = wx.Button(panel, wx.ID_ANY, "Cancel")
        cancel_btn.Bind(wx.EVT_BUTTON, self.on_close)
        button_sizer.Add(cancel_btn, 0, wx.ALL, 10)
        report_btn = wx.Button(panel, wx.ID_ANY, "Report")
        report_btn.Bind(wx.EVT_BUTTON, self.on_report_btn)
        button_sizer.Add(report_btn, 0, wx.ALL, 10)
        sizer.Add(button_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)

        panel.SetSizer(sizer)

        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Centre()
        self.Layout()
        self.Show()

        # Make these elements class attributes to easily access the contents
        self.panel = panel
        self.name_ctrl = name_ctrl
        self.email_ctrl = email_ctrl
        self.summary_ctrl = summary_ctrl
        self.description_ctrl = description_ctrl
        self.wait_lbl = wait_lbl
        self.report_btn = report_btn

        self.bugreporter = controller

        # flag is set to False when the backspace is pressed
        self.make_suggestion = True

        conf_dir = os.path.join(os.path.expanduser(u"~"), '.config', 'odemis')
        if not os.path.exists(conf_dir):
            # create the directory so that we can store the config files later
            os.makedirs(conf_dir)
        self._config_path = os.path.join(conf_dir, CONFIG_FN)
        self._names_path = os.path.join(conf_dir, NAMES_FN)

        self._topic_id = self._read_config()
        logging.debug("Will use topic ID %s. Change by editing %s", self._topic_id, self._config_path)

        # Load known users, if not available make tsv file
        self.known_users = OrderedDict()
        try:
            with open(self._names_path) as f:
                reader = csv.reader(f, delimiter='\t')
                for name, email in reader:
                    self.known_users[name] = email
        except (OSError, IOError) as ex:  # IOError is more generic than OSError, and only needed on Python 2
            logging.error("Failed to read known users: %s", ex)

    def _read_config(self):
        """
        Read configuration from the bugreporter.config file, and return it
        return:
            topic_id (int): the topic ID to use to report bug. In the INI file,
              it can be stored either as a string from TOPIC_IDS, or an int.
        """
        config = configparser.ConfigParser()
        config.read(self._config_path)
        topic_id = TOPIC_ID_DEFAULT
        topic_str = config.get('REPORT', 'topic', fallback=str(TOPIC_ID_DEFAULT))
        try:
            if topic_str in TOPIC_IDS:
                topic_id = TOPIC_IDS[topic_str]
            else:  # Not a known department => maybe it's just an int?
                val = int(topic_str)
                if val <= 0:
                    raise ValueError("Topic ID must be > 0")
                topic_id = val
        except Exception as ex:
            logging.warning("Unknown topic ID '%s', falling back to %s. (%s)", topic_str, topic_id, ex)

        return topic_id

    def store_user_info(self, name, email):
        """
        Store the user name and email in the config file, so it can be suggested the
        next time the bugreporter is used.
        :arg name: (String) user name
        :arg email: (String) user email
        """
        # Add user to top of tsv file, truncate file if it contains too many users.
        # Adding the user to the top of the list ensures that the suggestion is made based
        # on the latest entry. Otherwise, if a typo occurred the first time the name was written,
        # the faulty name will always be suggested.
        if name in self.known_users.keys():
            del self.known_users[name]
        elif len(self.known_users.items()) >= MAX_USERS:
            oldest_entry = list(self.known_users.keys())[-1]
            del self.known_users[oldest_entry]
        # It would be nicer not to create a new ordered dictionary, but to move the
        # element internally. The python 3 version has such a function (move_to_end).
        prev_items = list(self.known_users.items())
        self.known_users = OrderedDict([(name, email)] + prev_items)
        # Overwrite tsv file
        with open(self._names_path, 'w+') as f:
            writer = csv.writer(f, delimiter='\t')
            writer.writerows(self.known_users.items())

    def on_name_key_down(self, evt):
        """
        If the pressed key is the backspace, delete suggestion and allow user to type new key.
        """
        if evt.GetKeyCode() in (wx.WXK_BACK, wx.WXK_DELETE):
            self.make_suggestion = False
        else:
            # If the user adds/replaces at the end of the text => auto-complete
            ip = self.name_ctrl.GetInsertionPoint()
            sel = self.name_ctrl.GetSelection()
            if sel[0] != sel[1]:
                ip = sel[1]
            self.make_suggestion = (ip == self.name_ctrl.GetLastPosition())

        evt.Skip()

    def on_name_text(self, evt):
        """
        Suggest a username from the configuration file.
        """
        if self.make_suggestion:
            full_text = evt.String
            if not full_text:
                return

            # Get typed text from input field (don't include suggestion)
            sel = self.name_ctrl.GetSelection()
            if sel[0] != sel[1]:
                typed = full_text[:len(full_text) - sel[0] + 1]
            else:
                typed = full_text

            # Suggest name from configuration file and select suggested part
            # Note about the use of wx.CallAfter: For some reason, ChangeValue causes a
            # text event to be triggered even though it's not supposed to. Through
            # the use of CallAfter, this behaviour is avoided. It is still
            # not clear what the reason for this is, but it seems to work.
            for name in self.known_users.keys():
                if name.upper().startswith(typed.upper()):
                    wx.CallAfter(self.name_ctrl.ChangeValue, name)
                    break
            else:
                wx.CallAfter(self.name_ctrl.ChangeValue, typed)
            wx.CallAfter(self.name_ctrl.SetSelection, len(typed), -1)

    def on_name_focus(self, _):
        """
        Suggest email address for username.
        """
        name = self.name_ctrl.GetValue()
        if name in self.known_users.keys():
            self.email_ctrl.ChangeValue(self.known_users[name])

    def on_report_btn(self, _):
        """
        Disable all widgets, send ticket, save name and email in configuration file.
        """
        name = self.name_ctrl.GetValue()
        email = self.email_ctrl.GetValue()
        summary = self.summary_ctrl.GetValue()
        description = self.description_ctrl.GetValue()

        if not name or not email or not summary or description == DESCRIPTION_DEFAULT_TXT:
            dlg = wx.MessageDialog(self, 'Please fill in all the fields.', '', wx.OK)
            val = dlg.ShowModal()
            dlg.Show()
            if val == wx.ID_OK:
                dlg.Destroy()
                return

        self.wait_lbl.SetLabel("Compressing files...")
        self.Layout()

        for widget in self.panel.GetChildren():
            widget.Enable(False)
        self.wait_lbl.Enable(True)

        # Store user info and pass description to bugreporter
        self.store_user_info(name, email)
        f = self.bugreporter.send_report(name, email, summary, description, self._topic_id)
        f.add_done_callback(self._on_report_sent)

    def _on_report_sent(self, future):
        try:
            future.result()
        except Exception as e:
            logging.exception("osTicket upload failed: %s", e)
            wx.CallAfter(self.open_failed_upload_dlg)
        else:
            # Show it went fine
            wx.CallAfter(self._on_report_sent_successful)

    def on_close(self, _):
        """
        Ask user for confirmation before closing the window
        """
        # Ask for confirmation if the user has already filled in a summary or description
        if self.summary_ctrl.GetValue() or self.description_ctrl.GetValue() != DESCRIPTION_DEFAULT_TXT:
            dlg = wx.MessageDialog(self, 'The report has not been sent. Do you want to quit?',
                                   '', wx.OK | wx.CANCEL)
            val = dlg.ShowModal()
            dlg.Show()
            if val == wx.ID_CANCEL:
                dlg.Destroy()
                for widget in self.panel.GetChildren():
                    widget.Enable(True)
            elif val == wx.ID_OK:
                self.Destroy()
        else:
            self.Destroy()

    def _on_report_sent_successful(self):
        """
        Called when the report was successfully uploaded.
        It will show a pop-up to confirm to the user, and close the window
        To be called in the main GUI thread.
        """
        # Note: notify2 doesn't need to be called from the main GUI thread
        notify2.init("Odemis")
        notif = notify2.Notification("Odemis bug-report successfully uploaded",
                     "You will shortly receive a confirmation by email.",
                     "dialog-info")
        notif.show()

        # On Ubuntu 18.04, with wxPython 4.0.1, the notification immediately hides
        # if the application is closed. So we just hide the window, and wait a little
        # while (5 s) before closing the window.
        self.Hide()
        wx.CallLater(5 * 1000, self.Destroy)

    def open_failed_upload_dlg(self):
        """
        Ask the user to use the website in case the upload to osTicket failed.
        """
        txt = ('The bug-report could not be uploaded. Please send the report '
               'by filling in the form on https://support.delmic.com and attaching the ZIP '
               'file "%s", found on the Desktop.\n\n'
               'Alternatively, you can send the file to support@delmic.com .\n\n'
               'After closing this window, the form will be automatically opened in your web browser.' %
               self.bugreporter.zip_fn)
        dlg = wx.MessageDialog(self, txt, 'Automatic report upload unsuccessful', wx.OK)
        val = dlg.ShowModal()
        dlg.Show()
        if val == wx.ID_OK:
            self.Destroy()
            webbrowser.open('https://support.delmic.com/open.php')


if __name__ == '__main__':
    bugreporter = OdemisBugreporter()
    bugreporter.run()
