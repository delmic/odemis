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


import __version__
import argparse
import model
import sys
from odemisd import modelgen

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
    opt_grp.add_argument("--daemonize", "-D", dest="daemon", action="store_true", default=True,
                        help="Daemonize after startup")
    opt_grp.add_argument('--validate', dest="validate", action="store_true", default=False,
                        help="Validate the microscope description file and exit")
    opt_grp.add_argument("--log-level", dest="loglev", metavar="LEVEL", type=int,
                        default=0, help="Set verbosity level (default = 0)")
    opt_grp.add_argument("--log-target", dest="logtarget", metavar="{auto,stderr,filename}",
                        help="Specify the log target (auto, stderr, filename)")
    parser.add_argument("model", metavar="file.odm.yaml", nargs=1, type=open, 
                        help="Microscope model instantiation file (*.odm.yaml)")

    options = parser.parse_args(args[1:])
    
    # TODO see python-daemon for creating daemon
    
    # Daemon management
    if options.kill:
        raise NotImplementedError() # TODO
        return 0
    
    if options.check:
        raise NotImplementedError() # TODO
        return 0
    
    try:
        inst_model = modelgen.get_instantiation_model(options.model[0])
    except modelgen.OdemisSyntaxError:
        # the error message is already logged
        return 127
    
    try:
        comps, mic = modelgen.instantiate_model(inst_model, options.validate)
    except modelgen.OdemisSemanticError:
        # the error message is already logged
        return 127

#    a = model.StringProperty()
#    c = model.HwComponent({'name': "component"})
#    print a, c

if __name__ == '__main__':
    exit(main(sys.argv))
    
# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
