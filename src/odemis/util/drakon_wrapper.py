"""
Created on 4 August 2021

@author: Arthur Helsloot

Copyright © 2021 Arthur Helsloot, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

# before running any drakon test diagram, run this in a terminal:
# odemis-start ~/development/odemis/install/linux/usr/share/odemis/sim/enzel-sim.odm.yaml --nogui


from odemis import model
import logging
from logging import DEBUG, ERROR, CRITICAL, INFO, WARNING


def pytofail(f):
    """
    Decorator that prevents f from raising an exception, but instead would return the exception as its second return
    parameter. The first return parameter is the result of f, or None if an exception was raised.
    Every function called from a Drakon flow diagram should have this decorator.

    :param (executable) f: function to execute
    :return: (any) ans, (bool) fail: ans is the response of the function call, fail is the exception raised by f, or
        False if f succeeds
    """
    def wrapped(*args):
        try:
            ans = f(*args)
            fail = None
        except Exception as ex:
            ans = None
            fail = ex
        return ans, fail
    return wrapped


def pytofailmethod(f):
    def wrapped(self, *args):
        try:
            self.ans = f(self, *args)
            self.fail = None
        except Exception as ex:
            self.ans = None
            self.fail = ex
    return wrapped


@pytofail
def log(msg, level=DEBUG):
    logging.log(level, msg)


class AllHardware:
    def __init__(self):
        self.ans = None  # updated after every call/setter
        self.fail = None  # updated after every call/setter

        # get some hardware components
        self.cooler = model.getComponent(role="cooler")
        self.stage = model.getComponent(name="5DOF Stage")

    @property
    def heating(self):
        return self.cooler.heating.value

    @heating.setter
    @pytofailmethod
    def heating(self, v):
        self.cooler.heating.value = v

    @pytofailmethod
    def move_x_to(self, pos):
        f = self.stage.moveAbs({'x': pos})
        f.result()
        msg = "X moved to %f" % self.stage.position.value['x']
        print(msg)
        return msg

    @pytofailmethod
    def reference_stage(self):
        f = self.stage.reference()
        return f.result()



hw = AllHardware()
