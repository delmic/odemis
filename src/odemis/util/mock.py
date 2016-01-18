# -*- coding: utf-8 -*-
'''
Created on 28 Jul 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
# Fake component for testing purpose

from __future__ import division
import logging
from odemis import model


class MockComponent(model.HwComponent):
    """
    A very special component which does nothing but can pretend to be any component
    It's used for validation of the instantiation model.
    Do not use or inherit when writing a device driver!
    """
    def __init__(self, name, role, _realcls, parent=None, children=None, _vas=None, daemon=None, **kwargs):
        """
        _realcls (class): the class we pretend to be
        _vas (list of string): a list of mock vigilant attributes to create
        """
        model.HwComponent.__init__(self, name, role, daemon=daemon, parent=parent)
        if len(kwargs) > 0:
            logging.debug("Component '%s' got init arguments %r", name, kwargs)

        # Special handling of actuators, for actuator wrappers
        # Can not be generic for every roattribute, as we don't know what to put as value
        if issubclass(_realcls, model.Actuator):
            self.axes = {"x": model.Axis(range=[-1, 1])}
            # make them roattributes for proxy
            self._odemis_roattributes = ["axes"]

        if _vas is not None:
            for va in _vas:
                self.__dict__[va] = model.VigilantAttribute(None)

        if not children:
            children = {}

        cc = set()
        for child_name, child_args in children.items():
            # we don't care of child_name as it's only for internal use in the real component

            if isinstance(child_args, dict): # delegation
                # the real class is unknown, so just give a generic one
                logging.debug("Instantiating mock child component %s", child_name)
                child = MockComponent(_realcls=model.HwComponent, parent=self, daemon=daemon, **child_args)
            else: # explicit creation (already done)
                child = child_args

            cc.add(child)

        # use explicit setter to be sure the changes are notified
        self.children.value = self.children.value | cc

    # To pretend being a PowerSupplier
    def supply(self, sup):
        logging.debug("Pretending to power on components %s", sup)
        return model.InstantaneousFuture()
