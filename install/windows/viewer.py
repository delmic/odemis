# -*- coding: utf-8 -*-

import logging
import sys
import os

# Needed, so the cairo DLL files will be found
if getattr(sys, 'frozen', False):
    os.environ['PATH'] = os.environ['PATH'] + ';' + os.path.dirname(sys.executable)
else:
    os.environ['PATH'] = os.environ['PATH'] + ';' + os.path.dirname(sys.argv[0])

from odemis.gui import log
from odemis.gui.main import OdemisGUIApp, installThreadExcepthook


def run(flavor):
    installThreadExcepthook()

    log.init_logger(logging.INFO)

    filename = None

    if len(sys.argv) > 1 and os.path.exists(sys.argv[-1]):
        filename = sys.argv[-1]

    app = OdemisGUIApp(standalone=flavor, file_name=filename)

    # Change exception hook so unexpected exception
    # get caught by the logger
    backup_excepthook, sys.excepthook = sys.excepthook, app.excepthook

    # Start the application
    app.MainLoop()
    app.Destroy()

    sys.excepthook = backup_excepthook
