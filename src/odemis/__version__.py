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
import os
import subprocess

# Generic information constants

def _get_version():
    if not os.path.isdir(".git"):
        # TODO should fallback to a VERSION file
        return "version unknown"
    
    try:
        p = subprocess.Popen(["git", "describe",
                              "--tags", "--dirty", "--always"],
                             stdout=subprocess.PIPE)
        return p.stdout.read().strip()
    except EnvironmentError:
        print "unable to run git"
        return "version unknown"

version = _get_version()
name = "Open Delmic Microscope Software"
shortname = "Odemis"
copyright = "Copyright © 2012 Delmic"

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
