# -*- coding: utf-8 -*-
"""
Created on 2 Jul 2019

@author: Thera Pals

Copyright © 2012-2019 Thera Pals, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""
from __future__ import division

import numbers
import pkg_resources

import can
import canopen  # TODO package canopen and add to project requirements.

from odemis import model
from odemis.model import HwError

TARGET_POSITION_RANGE = (0, 100e-6)


class FocusTrackerCO(model.HwComponent):

    def __init__(self, name, role, channel, node_idx, **kwargs):
        """

        channel (str): channel name of can bus
        node_idx (int): node index of focus tracker
        """
        model.HwComponent.__init__(self, name, role, **kwargs)
        # Connect to the CANbus and the CANopen network.
        self.network = canopen.Network()
        bustype = 'socketcan' if channel != 'fake' else 'virtual'
        try:
            self.network.connect(bustype=bustype, channel=channel)
        except IOError as exp:
            if exp.errno == 19:
                raise HwError("Focus Tracker is not connected.")
            else:
                raise
        self.network.check()
        object_dict = pkg_resources.resource_filename("odemis.driver", "FocusTracker.eds")
        if channel == 'fake':
            self.node = FakeRemoteNode(node_idx, object_dict)
        else:
            self.node = canopen.RemoteNode(node_idx, object_dict)
        self.network.add_node(self.node)
        self._swVersion = "python-canopen v%s , python-can v%s" % (canopen.__version__, can.__version__)
        rev_num = self.node.sdo["Identity Object"]["Revision number"].raw
        minor = rev_num & 0xffff
        major = (rev_num >> 16) & 0xffff
        self._hwVersion = "{}.{}".format(major, minor)
        # Create SDO communication objects to communicate
        self._position_sdo = self.node.sdo["AI Input PV"][1]
        self._target_pos_sdo = self.node.sdo["CO Set Point W"][1]

        # Read PID gains from the device (and set the current metadata)
        self._proportional_gain_sdo = self.node.sdo['CO Proportional Band Xp1'][1]
        self._integral_gain_sdo = self.node.sdo['CO Integral Action Time Tn1'][1]
        self._derivative_gain_sdo = self.node.sdo['CO Derivative Action Time Tv1'][1]

        self._tracking_sdo = self.node.sdo['Controller On/ Off'][1]
        # set VAs only if there is hardware, channel = '' if no hardware
        self._metadata[model.MD_GAIN_P] = self._proportional_gain_sdo.raw
        self._metadata[model.MD_GAIN_I] = self._integral_gain_sdo.raw
        self._metadata[model.MD_GAIN_I] = self._derivative_gain_sdo.raw
        self.targetPosition = model.FloatContinuous(self._get_target_pos(), TARGET_POSITION_RANGE, unit="m",
                                                    getter=self._get_target_pos, setter=self._set_target_pos)
        self.position = model.FloatVA(self._get_position(), unit="m", readonly=True, getter=self._get_position)
        self.tracking = model.BooleanVA(self._get_tracking(), getter=self._get_tracking,
                                        setter=self._set_tracking)

    def terminate(self):
        """Disconnect from CAN bus."""
        if self.network:
            self._set_tracking(False)
            self.network.sync.stop()
            self.network.disconnect()
            self.network = None

        super(FocusTrackerCO, self).terminate()

    def updateMetadata(self, md):
        if model.MD_GAIN_P in md:
            md[model.MD_GAIN_P] = self._set_proportional(md[model.MD_GAIN_P])
            super(FocusTrackerCO, self).updateMetadata({model.MD_GAIN_P: md[model.MD_GAIN_P]})
        if model.MD_GAIN_I in md:
            md[model.MD_GAIN_I] = self._set_integral(md[model.MD_GAIN_I])
            super(FocusTrackerCO, self).updateMetadata({model.MD_GAIN_I: md[model.MD_GAIN_I]})
        if model.MD_GAIN_D in md:
            md[model.MD_GAIN_D] = self._set_derivative(md[model.MD_GAIN_D])
            super(FocusTrackerCO, self).updateMetadata({model.MD_GAIN_D: md[model.MD_GAIN_D]})

    def _get_position(self):
        """
        return (float): The current position of the laser on the linear ccd.
        """
        return self._position_sdo.raw * 1e-6

    def _get_target_pos(self):
        """
        return (float): The target position in meters of the laser on the linear ccd.
        """
        return self._target_pos_sdo.raw * 1e-6

    def _set_target_pos(self, value):
        """
        value (float, int): The target position in meters.
        return (float): The target position of the laser on the linear ccd.
        """
        self._target_pos_sdo.raw = value * 1e6
        return self._target_pos_sdo.raw

    def _set_tracking(self, value):
        """
        value (boolean): True if the focus should be tracked, False if the focus should not be tracked.
        return (boolean): Same as input value.
        """
        self._tracking_sdo.raw = value
        return value

    def _get_tracking(self):
        """
        return (boolean): True if the focus is tracked, False if the focus is not tracked.
        """
        return self._tracking_sdo.raw

    def _set_proportional(self, value):
        """
        value (int, float): Proportional gain value, can be any value and is rounded down to the nearest int
                            when a float is given.
        return (int): Same as input value, if input in correct range, else current gain.
        """
        if isinstance(value, numbers.Real) and value >= 0:
            self._proportional_gain_sdo.raw = value
        else:
            raise ValueError('value {} not accepted, only positive integers and floats accepted.'.format(value))
        return value

    def _get_proportional(self):
        """
        return (int): current proportional gain.
        """
        return self._proportional_gain_sdo.raw

    def _set_integral(self, value):
        """
        value (int, float): Integral gain value, can be any value and is rounded down to the nearest int
                            when a float is given.
        return (int): Same as input value, if input in correct range, else current gain.
        """
        if isinstance(value, numbers.Real) and value >= 0:
            self._integral_gain_sdo.raw = value
        else:
            raise ValueError('value {} not accepted, only positive integers and floats accepted.'.format(value))
        return value

    def _get_integral(self):
        """
        return (int): current integral gain.
        """
        return self._integral_gain_sdo.raw

    def _set_derivative(self, value):
        """
        value (int, float): Derivative gain value, can be any value and is rounded down to the nearest int
                            when a float is given.
        return (int): Same as input value, if input in correct range, else current gain.
        """
        if isinstance(value, numbers.Real) and value >= 0:
            self._derivative_gain_sdo.raw = value
        else:
            raise ValueError('value {} not accepted, only positive integers and floats accepted.'.format(value))
        return value

    def _get_derivative(self):
        """
        return (boolean): current derivative gain.
        """
        return self._derivative_gain_sdo.raw


class FakeRemoteNode(canopen.RemoteNode):
    def __init__(self, node_idx, object_dict):
        canopen.RemoteNode.__init__(self, node_idx, object_dict)
        sdo = SDODict()
        sdo.update({
            'AI Input PV': [0, FakeSDO(self.sdo["AI Input PV"][1], object_dict, init_value=10)],
            'CO Set Point W': [0, FakeSDO(self.sdo['CO Set Point W'][1], object_dict, init_value=20)],
            'Controller On/ Off': [0, FakeSDO(self.sdo['Controller On/ Off'][1], object_dict, init_value=False)],
            'CO Proportional Band Xp1': [0, FakeSDO(self.sdo['CO Proportional Band Xp1'][1], object_dict,
                                                    init_value=10.2)],
            'CO Integral Action Time Tn1': [0, FakeSDO(self.sdo['CO Integral Action Time Tn1'][1], object_dict,
                                                       init_value=10.2)],
            'CO Derivative Action Time Tv1': [0, FakeSDO(self.sdo['CO Derivative Action Time Tv1'][1], object_dict,
                                                         init_value=10.2)],
            "Identity Object": {"Revision number": FakeSDO(self.sdo["Identity Object"]["Revision number"], object_dict,
                                                           init_value=0x00010001)},
            'network': None  # needed to add the node to the network
        })
        self.sdo = sdo


class FakeSDO(canopen.sdo.base.Array):
    """Simulates an SDO object where the raw data can set and read from the canopen library."""

    def __init__(self, object_sdo, object_dict, init_value):
        canopen.sdo.base.Array.__init__(self, object_sdo, object_dict)
        self._raw = init_value

    @property
    def raw(self):
        return self._raw

    @raw.setter
    def raw(self, value):
        self._raw = value


class SDODict(dict):
    """Creates a dictionary that can be accessed with dots."""

    def __getattr__(self, name):
        return self[name]
