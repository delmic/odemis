# -*- coding: utf-8 -*-
'''
Created on 26 Mar 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
import os
import subprocess

# Generic information constants

def _get_version():
    if not os.path.isdir(".git"):
        # TODO should fallback to a VERSION file
        return "version unknown"
    
    try:
        out = subprocess.check_output(["git", "describe", "--tags", "--dirty", "--always"])
        return out.strip()
    except EnvironmentError:
        print "unable to run git"
        return "version unknown"

version = _get_version()
name = "Open Delmic Microscope Software"
shortname = "Odemis"
copyright = "Copyright © 2012 Delmic"
license = "GNU General Public License version 2"

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
