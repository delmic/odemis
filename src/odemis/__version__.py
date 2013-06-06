# -*- coding: utf-8 -*-
'''
Created on 26 Mar 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms 
of the GNU General Public License version 2 as published by the Free Software 
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; 
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR 
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with 
Odemis. If not, see http://www.gnu.org/licenses/.
'''
import logging
import os
import subprocess

# Generic information constants

def _get_version():
    # change directory to root
    rootdir = os.path.join(os.path.dirname(__file__), "..", "..") # odemis/src/odemis/../..
    
    if not os.path.isdir(rootdir) or not os.path.isdir(os.path.join(rootdir, ".git")):
        # TODO should fallback to a VERSION file
        return "version unknown"
    
    try:
        out = subprocess.check_output(args=["git", "describe", "--tags", "--dirty", "--always"],
                                      cwd=rootdir)
        return out.strip()
    except EnvironmentError:
        logging.warning("unable to run git")
        return "version unknown"

version = _get_version()
name = "Open Delmic Microscope Software"
shortname = "Odemis"
copyright = "Copyright © 2012-2013 Delmic"
license = "GNU General Public License version 2"
license_summary = (
"""Odemis is free software: you can redistribute it and/or modify it under the terms 
of the GNU General Public License version 2 as published by the Free Software 
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; 
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR 
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with 
Odemis. If not, see http://www.gnu.org/licenses/.
""")

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
