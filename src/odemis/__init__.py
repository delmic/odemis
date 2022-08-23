# -*- coding: utf-8 -*-
'''
Created on 26 Mar 2012

@author: Éric Piel

Copyright © 2012-2021 Éric Piel, Delmic

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

# Generic metadata about the package

def _get_version_git():
    """
    Get the version via git
    raises LookupError if no version info found
    """
    # change directory to root
    rootdir = os.path.join(os.path.dirname(__file__), "..", "..") # odemis/src/odemis/../..

    if not os.path.isdir(rootdir) or not os.path.isdir(os.path.join(rootdir, ".git")):
        raise LookupError("Not in a git directory")

    try:
        out = subprocess.check_output(args=["git", "describe", "--tags", "--dirty", "--always"],
                                      cwd=rootdir)
        ver = out.strip().decode("utf-8", errors="replace")
        if ver.startswith("v"):
            ver = ver[1:]
        return ver
    except OSError:
        raise LookupError("Unable to run git")
    except subprocess.CalledProcessError as ex:
        logging.warning("Failed to run git: %s", ex)
        raise LookupError("Execution of git failed")


def _get_version_setuptools():
    """
    Gets the version via setuptools/pkg_resources
    raises LookupError if no version info found
    """
    import pkg_resources
    try:
        return pkg_resources.get_distribution("odemis").version
    except pkg_resources.DistributionNotFound:
        raise LookupError("Not packaged via setuptools")

def _get_version():
    try:
        return _get_version_git()
    except LookupError:
        # fallback to setuptools (if it's not in git, it should be packaged)
        try:
            return _get_version_setuptools()
        except LookupError:
            # Last attempt: see if there is a version file
            import sys
            if getattr(sys, 'frozen', False):
                path = os.path.join(os.path.dirname(sys.executable), 'version.txt')
                if os.path.exists(path):
                    with open(path, 'r') as f:
                        return f.readline().strip()

            logging.warning("Unable to find the actual version")
            return "Unknown"


def get_version_simplified():
    """
    This function returns a version string of the form "M.N.PP(.QQQ)", where
    QQQ is the commit number in case of unstable release.
    """
    return ".".join(__version__.split("-")[:2])


__version__ = _get_version()
__fullname__ = "Open Delmic Microscope Software"
__shortname__ = "Odemis"
__copyright__ = "Copyright © 2012-2022 Delmic"
__authors__ = ["Éric Piel", "Rinze de Laat", "Kimon Tsitsikas",
               "Philip Winkler", "Anders Muskens", "Sabrina Rossberger",
               "Thera Pals", "Victoria Mavrikopoulou", "Kornee Kleijwegt",
               "Bassim Lazem", "Mahmoud Barazi", "Arthur Helsloot"]
__license__ = "GNU General Public License version 2"
__licensetxt__ = (
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
