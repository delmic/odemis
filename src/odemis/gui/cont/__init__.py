# -*- coding: utf-8 -*-
"""
Created on 12 Feb 2013

@author: Rinze de Laat

Copyright © 2013 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms 
of the GNU General Public License version 2 as published by the Free Software 
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; 
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR 
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with 
Odemis. If not, see http://www.gnu.org/licenses/.


### Purpose ###

This package contains Controller classes which 'control' groups of widgets and
other GUI components as logical units.

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
