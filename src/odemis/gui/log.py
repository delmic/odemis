#-*- coding: utf-8 -*-
"""
@author: Rinze de Laat

Copyright © 2012 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 2 of the License, or (at your option) any later
version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/. """

import logging
import os.path
from logging.handlers import RotatingFileHandler

import wx

LOG_FILE = "odemis-gui.log"

LOG_LINES = 500
log = None

# CRITICAL = 50
# FATAL = CRITICAL
# ERROR = 40
# WARNING = 30
# WARN = WARNING
# INFO = 20
DEBUG = logging.DEBUG
NOTSET = 0

_current_level = DEBUG

def set_level(level=NOTSET):
    log.setLevel(level)

def get_logger():
    logging.basicConfig(format=" - %(levelname)s \t%(message)s")
    l = logging.getLogger()
    l.setLevel(_current_level)
    l.handlers[0].setFormatter(
      logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))

    return l

def create_gui_logger(log_field):
    gui_format = logging.Formatter('%(asctime)s - %(module)s - %(message)s', '%H:%M:%S')
    text_field_handler = TextFieldHandler()
    text_field_handler.setTextField(log_field)
    text_field_handler.setFormatter(gui_format)
    log.debug("Switching to GUI logger")
    log.addHandler(text_field_handler)

    for handler in log.handlers:
        if not isinstance(handler, TextFieldHandler):
            log.removeHandler(handler)

    # Path to the log file
    logfile_path = os.path.join(os.path.expanduser("~"), LOG_FILE)
    # Maximum size of the log file before it's rotated
    max_logfile_size = 512**2
    # Maximum number of (rotated) log files
    max_logfile_count = 1
    # Formatting string for logging messages to file
    file_format = logging.Formatter(("%(asctime)s - "
                                     "%(levelname)s\t%(module)s(%(lineno)d):  "
                                     "%(message)s"), '%Y-%m-%d %H:%M:%S')

    file_handler = RotatingFileHandler(logfile_path, 'a',
                                       max_logfile_size,
                                       max_logfile_count)

    file_handler.setFormatter(file_format)
    log.addHandler(file_handler)

class TextFieldHandler(logging.Handler):
    """ Custom log handler, used to output log entries to a text field. """
    def __init__(self):
        """ Call the parent constructor and initialize the handler """
        logging.Handler.__init__(self)
        self.textfield = None

    def setTextField(self, textfield):
        self.textfield = textfield
        self.textfield.Clear()

    def emit(self, record):
        """ Write a record, in color, to a text field. """
        if self.textfield is not None:
            color = "#777777"

            if record.levelno > logging.WARNING:
                color = "#B00B2C"
            elif record.levelno > logging.INFO:
                color = "#C87000"
            elif record.levelno > logging.DEBUG:
                color = "#555555"
            else:
                color = "#777777"

            # Do the actual writing in a CallAfter, so logging won't interfere
            # with the GUI drawing process.
            wx.CallAfter(self.write_to_field, record, color)

    def write_to_field(self, record, color):

        while self.textfield.GetNumberOfLines() > LOG_LINES:
            # Removes the characters from position 0 up to and including the first line break
            self.textfield.Remove(0, self.textfield.GetValue().find('\n') + 1)
            #self.textfield.Remove(self.textfield.GetValue().rfind('\n'), len(self.textfield.GetValue()))

        self.textfield.SetDefaultStyle(wx.TextAttr(color, None))
        self.textfield.AppendText(''.join([self.format(record), '\n']))
        self.textfield.LineUp()


def is_debug(self):
    return True

if log is None:
    log = get_logger()
