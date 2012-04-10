#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 26 Mar 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Open Delmic Microscope Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''


from odemisd import modelgen
import __version__
import argparse
import logging
import sys

# This is the cli interface of odemisd, which allows to start the back-end
# It parses the command line and accordingly reads the microscope instantiation
# file, generates a model out of it, and then provides it to the front-end 

def main(args):
    """
    Contains the console handling code for the daemon
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    #print args
    # arguments handling 
    parser = argparse.ArgumentParser(description=__version__.name)

    parser.add_argument('--version', action='version', 
                        version=__version__.name + " " + __version__.version + " – " + __version__.copyright)
    dm_grp = parser.add_argument_group('Daemon management')
    dm_grp.add_argument("--kill", "-k", dest="kill", action="store_true", default=False,
                        help="Kill a running daemon")
    dm_grp.add_argument("--check", dest="check", action="store_true", default=False,
                        help="Check for a running daemon (only returns exit code)")
    opt_grp = parser.add_argument_group('Options')
    opt_grp.add_argument("--daemonize", "-D", action="store_true", dest="daemon",
                         default=False, help="Daemonize after startup")
    opt_grp.add_argument('--validate', dest="validate", action="store_true", default=False,
                        help="Validate the microscope description file and exit")
    opt_grp.add_argument("--log-level", dest="loglev", metavar="LEVEL", type=int,
                        default=0, help="Set verbosity level (0-2, default = 0)")
    opt_grp.add_argument("--log-target", dest="logtarget", metavar="{auto,stderr,filename}",
                default="auto", help="Specify the log target (auto, stderr, filename)")
    parser.add_argument("model", metavar="file.odm.yaml", nargs=1, type=open, 
                        help="Microscope model instantiation file (*.odm.yaml)")

    options = parser.parse_args(args[1:])
    
    # Set up logging before everything else
    if options.loglev < 0:
        parser.error("log-level must be positive.")
    loglev_names = [logging.WARNING, logging.INFO, logging.DEBUG]
    loglev = loglev_names[min(len(loglev_names) - 1, options.loglev)]
    
    # auto = {odemis.log if daemon, stderr otherwise} 
    if options.logtarget == "auto":
        # default to SysLogHandler ?
        if options.daemon:
            handler = logging.FileHandler("odemis.log")
        else:
            handler = logging.StreamHandler()
    elif options.logtarget == "stderr":
        handler = logging.StreamHandler()
    else:
        handler = logging.FileHandler(options.logtarget)
    logging.getLogger().setLevel(loglev)
    handler.setFormatter(logging.Formatter('%(asctime)s (%(module)s) %(levelname)s: %(message)s'))
    logging.getLogger().addHandler(handler)
    
    # TODO see python-daemon for creating daemon
    
    # Daemon management
    if options.kill:
        raise NotImplementedError() # TODO
        return 0
    
    if options.check:
        raise NotImplementedError() # TODO
        return 0
    
    try:
        logging.debug("model instantiation file is: %s", options.model[0].name)
        inst_model = modelgen.get_instantiation_model(options.model[0])
        logging.info("model has been read successfully")
    except modelgen.ParseError:
        logging.exception("Error while parsing file %s", options.model[0].name)
        return 127
    
    try:
        comps, mic = modelgen.instantiate_model(inst_model, options.validate)
        logging.info("model has been instantiated successfully")
        logging.debug("model microscope is %s", mic.name) 
        logging.debug("model components are %s", ", ".join([c.name for c in comps])) 
    except:
        logging.exception("When instantiating file %s", options.model[0].name)
        return 127
    
    logging.warning("nothing else to do")
#    a = model.StringProperty()
#    c = model.HwComponent({'name': "component"})
#    print a, c

if __name__ == '__main__':
    exit(main(sys.argv))
    
# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
