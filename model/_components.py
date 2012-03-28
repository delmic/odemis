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


class HwComponent(object):
    """

    """
    
    def __init__(self, name, role):
        self.name = name
        self.role = role


class Microscope(HwComponent):
    """
    A component which represent the whole microscope. 
    It does nothing by itself, just contains other components. 
    """
    def __init__(self, name, role, children=None, **kwargs):
        HwComponent.__init__(self, name, role)
        if children:
            raise Exception("Microscope component cannot have children.")
        
        if kwargs:
            raise Exception("Microscope component cannot have initialisation arguments.")

        self.detectors = set()
        self.actuators = set()
        self.emitters = set()

class MockComponent(HwComponent):
    """
    A very special component which does nothing but can pretend to be any component
    It's used for validation of the instantiation model. 
    Do not use or inherit when writing a device driver!
    """
    # TODO: we could try to even mock more by accepting any properties or attributes
    def __init__(self, name, role, children=None, **kwargs):
        HwComponent.__init__(self, name, role)
        
        if not children:
            return
        self.children = set()
        for child_name, child_args in children.items():
            # we don't care of child_name as it's only for internal use in the real component
            self.children.add(MockComponent(**child_args))

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: