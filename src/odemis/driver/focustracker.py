# -*- coding: utf-8 -*-
"""
Created on 2 Jul 2019

@author: Thera Pals, Philip Winkler

Copyright © 2012-2021 Thera Pals, Philip Winkler, Delmic

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

import can
import canopen
from canopen.nmt import NmtError

import logging

from odemis import model
from odemis.model import HwError


class FocusTrackerCO(model.HwComponent):
    """
    Driver for the Delmic focus tracker. The focus tracker reads a ccd signal which can be used to find
    back a previous stage position corresponding to a good focus.

    Functions
    =========
    .distance_to_target --> int: difference between current output and target output in ADC counts (0 to 2 ** 16 - 1)
    .set_target() --> None: read current ccd output from the hw and set it as target

    VAs
    ===
    .ccd_output (IntVA): reads current ccd output from the hardware and reports value in ADC counts (0 to 2 ** 16 - 1)
    .ccd_target (IntVA): target output value in ADC counts, -1 if not specified
    """

    def __init__(self, name, role, channel, node_id, datasheet, **kwargs):
        """
        channel (str): channel name of can bus
        node_idx (int): node index of focus tracker
        """
        model.HwComponent.__init__(self, name, role, **kwargs)

        # Connect to the CANbus and the CANopen network.
        self._network, self._node = self._openCanNode(channel, node_id, datasheet)

        self._swVersion = "python-canopen v%s , python-can v%s" % (canopen.__version__, can.__version__)
        major, minor = self._get_version()
        self._hwVersion = "{}.{}".format(major, minor)

        self.ccd_target = model.IntVA(-1, unit="ADC counts (0 to 2**16-1)", readonly=True)
        self.ccd_output = model.IntVA(self._get_output(), unit="ADC counts (0 to 2**16-1)",
                                      readonly=True, getter=self._get_output)

    def distance_to_target(self):
        return self.ccd_output.value - self.ccd_target.value

    def set_target(self):
        output = self.ccd_output.value
        self.ccd_target._value = output
        self.ccd_target.notify(output)

    def _get_output(self):
        """
        return (float): The current position of the laser on the linear ccd.
        """
        output = self._node.sdo["Voltage output PID"][1].raw
        logging.debug("Current ccd output: %s, target: %s", output, self.ccd_target.value)
        return output

    def _get_version(self):
        """
        return (int, int):
             Firmware major version number
             Firmware minor version number
        """
        rev_num = self._node.sdo["Identity Object"]["Revision number"].raw
        minor = rev_num & 0xffff
        major = (rev_num >> 16) & 0xffff
        return major, minor

    @staticmethod
    def _openCanNode(channel, nodeid, datasheet):
        """
        raise HwError: if the CAN port cannot be opened
        """
        # For debugging purpose
        if channel == "fake":
            return None, FakeRemoteNode(nodeid, datasheet)

        # Start with creating a network representing one CAN bus
        network = canopen.Network()

        # Connect to the CAN bus
        try:
            network.connect(bustype='socketcan', channel=channel)
            network.check()
        except can.CanError as ex:
            raise HwError("Failed to establish connection on channel %s, ex: %s" % (channel, ex))
        except OSError:
            raise HwError("CAN adapter not found on channel %s." % (channel,))

        # Add some nodes with corresponding Object Dictionaries
        node = canopen.BaseNode402(nodeid, datasheet)
        network.add_node(node)

        # Reset network
        try:
            node.nmt.state = 'RESET COMMUNICATION'
            node.nmt.wait_for_bootup(15)
            logging.debug('Device state after reset = {0}'.format(node.nmt.state))
        except NmtError:
            raise HwError("Node with id %s not present on channel %s." % (nodeid, channel))

        # Transmit SYNC every 100 ms
        network.sync.start(0.1)

        try:
            node.load_configuration()
            node.setup_402_state_machine()
        except ValueError as ex:
            raise HwError("Exception connecting to state machine for node %s on %s: %s." % (nodeid, channel, ex))
        return network, node

    def terminate(self):
        """Disconnect from CAN bus."""
        if self._network:
            self._network.sync.stop()
            self._network.disconnect()
            self._network = None

        super(FocusTrackerCO, self).terminate()


class FakeRemoteNode(canopen.RemoteNode):
    def __init__(self, node_idx, object_dict):
        canopen.RemoteNode.__init__(self, node_idx, object_dict)
        sdo = SDODict()
        sdo.update({
            'Voltage output PID': [0, FakeSDO(self.sdo["Voltage output PID"][1], object_dict, init_value=10)],
            "Identity Object": {"Revision number": FakeSDO(self.sdo["Identity Object"]["Revision number"], object_dict,
                                                           init_value=0x00010001)},
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
