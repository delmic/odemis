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
import logging
import os

import canopen  # TODO package canopen and add to project requirements.

import odemis
from odemis import model


class FocusTrackerCO(model.HwComponent):

    def __init__(self, name, role, channel, node_idx, **kwargs):
        """

        channel (str): channel name of can bus
        node_idx (int): node index of focus tracker
        """
        model.HwComponent.__init__(self, name, role, **kwargs)
        # Connect to the CANbus and the CANopen network.
        self.network = canopen.Network()
        self.network.connect(bustype='socketcan', channel=channel)
        self.network.check()
        self.node = canopen.RemoteNode(node_idx, os.path.dirname(odemis.__file__) + "/driver/FocusTracker.eds")
        self.network.add_node(self.node)
        # Create SDO communication objects to communicate
        self._current_pos_sdo = self.node.sdo["AI Input PV"][1]
        self._target_pos_sdo = self.node.sdo["CO Set Point W"][1]

        # Read PID gains from the device (and set the current metadata)
        self.proportional_gain_sdo = self.node.sdo['CO Proportional Band Xp1'][1]
        self.integral_gain_sdo = self.node.sdo['CO Integral Action Time Tn1'][1]
        self.derivative_gain_sdo = self.node.sdo['CO Derivative Action Time Tv1'][1]

        self._tracking_sdo = self.node.sdo['Controller On/ Off'][1]
        # set VAs only if there is hardware, channel = '' if no hardware
        if channel != '':
            self._metadata[model.MD_GAIN_P] = self.proportional_gain_sdo.raw
            self._metadata[model.MD_GAIN_I] = self.integral_gain_sdo.raw
            self._metadata[model.MD_GAIN_I] = self.derivative_gain_sdo.raw
            self.target_pos = model.FloatVA(self._target_pos_sdo.raw, unit="m", readonly=True,
                                            getter=self.get_target_pos)
            self.current_pos = model.FloatVA(self._current_pos_sdo.raw, unit="m", readonly=True,
                                             getter=self.get_current_pos)
            self.tracking = model.BooleanVA(self._tracking_sdo.raw, getter=self.get_tracking, setter=self.set_tracking)

    def terminate(self):
        """Disconnect from CAN bus."""
        if self.network:
            self.network.sync.stop()
            self.network.disconnect()
            self.network = None

    def updateMetadata(self, md):
        if model.MD_GAIN_P in md:
            md[model.MD_GAIN_P] = self.set_proportional(md[model.MD_GAIN_P])
        if model.MD_GAIN_I in md:
            md[model.MD_GAIN_I] = self.set_integral(md[model.MD_GAIN_I])
        if model.MD_GAIN_D in md:
            md[model.MD_GAIN_D] = self.set_derivative(md[model.MD_GAIN_D])
        super(FocusTrackerCO, self).updateMetadata(md)

    def get_available_nodes(self):
        """
        return (list): a list of id's of nodes in the network
        """
        return self.network.scanner.nodes

    def get_object_dictionary(self):
        """
        return (dict): the object dictionary of the focus tracker.
        """
        return self.node.object_dictionary

    def get_current_pos(self):
        """
        return (float): The current position of the laser on the linear ccd.
        """
        return self._current_pos_sdo.raw * 1e-6

    def get_target_pos(self):
        """
        return (float): The target position of the laser on the linear ccd.
        """
        return self._target_pos_sdo.raw * 1e-6

    def set_tracking(self, value):
        """
        value (boolean): True if the focus should be tracked, False if the focus should not be tracked.
        return (boolean): Same as input value.
        """
        self._tracking_sdo.raw = value
        return value

    def get_tracking(self):
        """
        return (boolean): True if the focus is tracked, False if the focus is not tracked.
        """
        return self._tracking_sdo.raw

    def set_proportional(self, value):
        """
        value (int, float): Proportional gain value, can be any value and is rounded down to the nearest int
                            when a float is given.
        return (int): Same as input value, if input in correct range, else current gain.
        """
        if isinstance(value, (float, int)):
            self.proportional_gain_sdo.raw = value
        else:
            logging.info('value {} not accepted, only integers and floats accepted.'.format(value))
            value = self.proportional_gain_sdo.raw
        return value

    def get_proportional(self):
        """
        return (int): current proportional gain.
        """
        return self.proportional_gain_sdo.raw

    def set_integral(self, value):
        """
        value (int, float): Integral gain value, can be any value and is rounded down to the nearest int
                            when a float is given.
        return (int): Same as input value, if input in correct range, else current gain.
        """
        if isinstance(value, (float, int)):
            self.integral_gain_sdo.raw = value
        else:
            logging.info('value {} not accepted, only integers and floats accepted.'.format(value))
            value = self.proportional_gain_sdo.raw
        return value

    def get_integral(self):
        """
        return (int): current integral gain.
        """
        return self.integral_gain_sdo.raw

    def set_derivative(self, value):
        """
        value (int, float): Derivative gain value, can be any value and is rounded down to the nearest int
                            when a float is given.
        return (int): Same as input value, if input in correct range, else current gain.
        """
        if isinstance(value, (float, int)):
            self.derivative_gain_sdo.raw = value
        else:
            logging.info('value {} not accepted, only integers and floats accepted.'.format(value))
            value = self.proportional_gain_sdo.raw
        return value

    def get_derivative(self):
        """
        return (boolean): current derivative gain.
        """
        return self.derivative_gain_sdo.raw


class FocusTrackerCOSimulator(FocusTrackerCO):
    def __init__(self, name, role, channel='', node_idx=0x10, **kwargs):
        """
        channel (str): channel name of can bus, '' for fake can bus.
        node_idx (int): name of node of focus tracker
        """
        FocusTrackerCO.__init__(self, name, role, channel, node_idx, **kwargs)
        object_dict = self.get_object_dictionary()
        self._current_pos_sdo = FakeSDO(self._current_pos_sdo.sdo_node, object_dict['AI Input PV'], init_value=10)
        self.current_pos = model.FloatVA(self._current_pos_sdo.raw, unit="m", readonly=True,
                                         getter=self.get_current_pos)
        self._target_pos_sdo = FakeSDO(self._target_pos_sdo.sdo_node, object_dict['CO Set Point W'], init_value=20)
        self.target_pos = model.FloatVA(self._target_pos_sdo.raw, unit="m", readonly=True, getter=self.get_target_pos)
        self._tracking_sdo = FakeSDO(self._tracking_sdo.sdo_node, object_dict['Controller On/ Off'], init_value=False)
        self.tracking = model.BooleanVA(self._tracking_sdo.raw, getter=self.get_tracking, setter=self.set_tracking)
        self.proportional_gain_sdo = FakeSDO(self.proportional_gain_sdo.sdo_node,
                                             object_dict['CO Proportional Band Xp1'], init_value=10.2)
        self.integral_gain_sdo = FakeSDO(self.integral_gain_sdo.sdo_node, object_dict['CO Integral Action Time Tn1'],
                                         init_value=10.2)
        self.derivative_gain_sdo = FakeSDO(self.derivative_gain_sdo.sdo_node,
                                           object_dict['CO Derivative Action Time Tv1'], init_value=10.2)


class FakeSDO(canopen.sdo.base.Array):
    """Simulates an SDO object where the raw data can set and read from the canopen library."""

    def __init__(self, object_sdo, object_dictionary, init_value):
        canopen.sdo.base.Array.__init__(self, object_sdo, object_dictionary)
        self._raw = init_value

    @property
    def raw(self):
        return self._raw

    @raw.setter
    def raw(self, value):
        self._raw = value
