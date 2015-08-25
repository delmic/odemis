# -*- coding: utf-8 -*-

import logging
import sys
import os

# Needed, so the cairo DLL files will be found
if hasattr(sys, 'frozen'):
    os.environ['PATH'] = os.environ['PATH'] + ';' + os.path.dirname(sys.executable)
else:
    os.environ['PATH'] = os.environ['PATH'] + ';' + os.path.dirname(sys.argv[0])

# sys.path.append(os.path.abspath('../src'))
# print "\n".join(sys.path)

from odemis.gui import log
from odemis.gui.main import OdemisGUIApp, installThreadExcepthook

installThreadExcepthook()

log.init_logger(logging.WARNING)
app = OdemisGUIApp(standalone=True)

# Change exception hook so unexpected exception
# get caught by the logger
backup_excepthook, sys.excepthook = sys.excepthook, app.excepthook

# Start the application
app.MainLoop()
app.Destroy()

sys.excepthook = backup_excepthook
