#-*- coding: utf-8 -*-
"""
:author:    Éric Piel
:copyright: © 2019 Éric Piel, Delmic

.. license::

    This file is part of Odemis.

    Odemis is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License version 2 as published by the Free
    Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
    FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
    details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""

import logging
from odemis.gui.util import call_in_wx_main
import sys
import wx
from wx.adv import NotificationMessage


@call_in_wx_main
def show_message(parent, title, message=None, timeout=3.0, level=logging.INFO):
    """ Show a small message popup for a short time

    :param parent: (wxWindow or None)
    :param title: (str) The title of the message
    :param message: (str or None) Extra text that will be displayed below the title
    :param timeout: (float) Timeout in seconds after which the message will automatically vanish
    :param level: (logging.*) The error level
    """
    message = message or ""
    flags = {logging.INFO: wx.ICON_INFORMATION,
             logging.WARNING: wx.ICON_WARNING,
             logging.ERROR: wx.ICON_ERROR}[level]

    # On Windows, if message is None, then no popup is shown at all
    if message is None and sys.platform.startswith('win'):
        message = " "

    m = NotificationMessage(title, message, parent=parent, flags=flags)
    m.Show(timeout)
