# -*- coding: utf-8 -*-
import argparse
import logging
import sys
import os

# Needed, so the cairo DLL files will be found
if getattr(sys, 'frozen', False):
    os.environ['PATH'] = os.environ['PATH'] + ';' + os.path.dirname(sys.executable)
else:
    os.environ['PATH'] = os.environ['PATH'] + ';' + os.path.dirname(sys.argv[0])

import odemis
from odemis.gui import log
from odemis.gui.main import OdemisGUIApp, installThreadExcepthook


def run(flavor):

    args = sys.argv

    # arguments handling
    parser = argparse.ArgumentParser(prog="odemis-viewer",
                                     description=odemis.__fullname__)

    # nargs="?" to allow to pass just -f without argument, for the Linux desktop
    # file to work easily.
    parser.add_argument('-f', '--file', dest="file_name", nargs="?", default=None,
                        help="File to display")
    parser.add_argument("--log-level", dest="loglev", metavar="<level>", type=int,
                        default=0, help="set verbosity level (0-2, default = 0)")

    options = parser.parse_args(args[1:])

    # Set up logging before everything else
    if options.loglev < 0:
        parser.error("log-level must be positive.")

    installThreadExcepthook()

    loglev_names = [logging.WARNING, logging.INFO, logging.DEBUG]
    loglev = loglev_names[min(len(loglev_names) - 1, options.loglev)]
    log.init_logger(loglev)

    app = OdemisGUIApp(standalone=flavor, file_name=options.file_name)

    # Change exception hook so unexpected exception
    # get caught by the logger
    backup_excepthook, sys.excepthook = sys.excepthook, app.excepthook

    # Start the application
    app.MainLoop()
    app.Destroy()

    sys.excepthook = backup_excepthook
