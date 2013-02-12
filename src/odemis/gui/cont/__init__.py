# -*- coding: utf-8 -*-
"""
Created on 12 Feb 2013

@author: Rinze de Laat

Copyright © 2013 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 2 of the License, or (at your option) any later
version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.


### Purpose ###

This package is is a place holder for controllers which are used in various
places throughout Odemis.

"""

_main_tab_controller = None


def set_main_tab_controller(mtc):
    global _main_tab_controller
    _main_tab_controller = mtc

def get_main_tab_controller():
    global _main_tab_controller

    if not _main_tab_controller:
        raise ValueError("Main tab controller not set!")

    return _main_tab_controller
