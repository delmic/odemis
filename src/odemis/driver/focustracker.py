# -*- coding: utf-8 -*-
"""
Created on 2 Jul 2019

@author: Thera Pals & Éric Piel

Copyright © 2019-2021 Thera Pals, Éric Piel, Delmic

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
from can import CanError
import can
from canopen import node
import canopen  # TODO add to project requirements.
from canopen.nmt import NmtError
import logging
import numbers
from odemis import model, util
import os
import pkg_resources
import random
import time


# The main objects that could be of interest are:
# 0x6100 AI Input FV  # Field Value: location of the laser spot on the linear CCD (in px)
# 0x6126 AI Scaling Factor  # Conversion ratio from Input FV to Input PV (µm/px)
# 0x6130 AI Input PV  # Process Value: location converted to focus position (unit: µm)
# 0x6138 AI Tare zero  # Net PV = Input PV - Tare zero (unit: µm)
# 0x6139 AI Autotare  # write 0x61726174 ('tara' in hex), to set Tare zero to the current value
# 0x6140 AI Net PV  # Net PV = Input PV - Tare zero (unit: µm)

POS_SDO = "AI Net PV"

# Expected values, to compare to what is returned by the device
VENDOR_ID = 0xc0ffee  # Not an official ID, but tasty enough for our internal use
PRODUCT_CODE = 0x0001

# The maximum acceptable duration since a position update.
# If the latest known position is "older", the getter will explicitly read the position.
MAX_POS_AGE = 0.1  # s


class FocusTrackerCO(model.HwComponent):
    """
    Driver for the in-house (Delmic) focus tracker device.
    It's connected via CANopen.
    """
    def __init__(self, name, role, channel, node_idx, datasheet=None, inverted=None, ** kwargs):
        """
        channel (str): channel name of can bus (eg, "can0"). Use "fake" for a simulator.
        node_idx (int): node index of focus tracker
        datasheet (str or None): absolute or relative path to .dcf configuration file
          This can be used to set default parameters value. If None, it will use the
          default .eds file.
        inverted (set of str): pass {"z"} to invert the reported position (ie, * -1).
        """
        model.HwComponent.__init__(self, name, role, **kwargs)

        if inverted is None:
            inverted = set()
        if set(inverted) > {"z"}:
            raise ValueError("Only axis z exists, but got inverted axes: %s." %
                                 (", ".join(inverted),))
        self._inverted = "z" in inverted

        # Conveniently, python-canopen accepts both an opened File and a filename (str)
        if datasheet is None:
            logging.debug("Using default focus tracker datasheet")
            datasheet = pkg_resources.resource_filename("odemis.driver", "FocusTracker.eds")
        elif not os.path.isabs(datasheet):
            # For relative path, use the current path as root
            datasheet = os.path.join(os.path.dirname(__file__), datasheet)

        self.network, self.node = self._connect(channel, node_idx, datasheet)

        # For recovery
        self._channel = channel
        self._node_idx = node_idx
        self._datasheet = datasheet

        # Do not leave canopen log to DEBUG, even if the general log level is set
        # to DEBUG, because it generates logs for every CAN packet, which is too much.
        canlog = logging.getLogger("canopen")
        canlog.setLevel(max(canlog.getEffectiveLevel(), logging.INFO))

        self._swVersion = "python-canopen v%s, python-can v%s" % (canopen.__version__, can.__version__)
        rev_num = self.node.sdo["Identity object"]["Revision number"].raw
        major, minor = (rev_num >> 16) & 0xffff, rev_num & 0xffff
        sn = self.node.sdo["Identity object"]["Serial number"].raw
        self._hwVersion = "Focus tracker {}.{} (s/n : {})".format(major, minor, sn)
        logging.info("Connected to %s", self._hwVersion)

        # Create SDO communication objects to communicate
        self._position_sdo = self.node.sdo[POS_SDO][1]

        # The position is updated by messages sent regularly (50Hz) from the device.
        # However, we cannot rely only on this mechanism to update the position,
        # as it wouldn't detect loss of connection, and would silently report
        # old values. So, whenever the position is explicitly read, we check it
        # was updated recently, and if not, attempt to recover, or raise an error.
        self._last_pos_update = 0
        self.position = model.VigilantAttribute({"z": 0}, unit="m", readonly=True, getter=self._get_position)
        # Note that the range of the position is undefined, even in pixels, the
        # value can go out of the actual CCD, as it could be that the gaussian
        # pick is outside. In addition, the scale factor could in theory change
        # on-the-fly (although, typically only during calibration).
        self._updatePosition(self._read_position())

        # Set callback for the position update
        self._configure_device()

        # TODO: add a heartbeat monitor to automatically attempt connection recovery

    def _connect(self, channel, node_idx, datasheet):
        """
        return network, node
        raise HwError() if the device is not connected
        raise ValueError(): if the device doesn't seem the right one
        """

        # Connect to the CANbus and the CANopen network.
        network = canopen.Network()
        bustype = 'socketcan' if channel != 'fake' else 'virtual'
        try:
            network.connect(bustype=bustype, channel=channel)
            network.check()
        except CanError:
            raise model.HwError("CAN network %s not found." % (channel,))
        except OSError as ex:
            if ex.errno == 19:  # No such device
                raise model.HwError("CAN network %s not found." % (channel,))
            raise

        # Tell CANopen what we *expect* to find
        if channel == 'fake':
            node = FakeRemoteNode(node_idx, datasheet)
        else:
            node = canopen.RemoteNode(node_idx, datasheet)
        # Note: add_node() supports a "upload_eds" flag to read the object dict from
        # the device. However the current firmware doesn't support that.
        network.add_node(node)

        # Check the device is there, and also force the state to be updated
        try:
            if channel != "fake":
                node.nmt.wait_for_heartbeat(timeout=5)
        except NmtError:
            raise model.HwError("Focus tracker not found on channel %s with ID %s" % (channel, node_idx))

        logging.debug("Device is in state %s", node.nmt.state)

        # If the device is stopped, it won't answer any SDO
        if node.nmt.state not in ("OPERATIONAL", "PRE-OPERATIONAL"):
            node.nmt.state = "PRE-OPERATIONAL"
            logging.debug("Turning on the device to state %s", node.nmt.state)

        # Check that the device has the right Vendor ID and Product code, mostly
        # in case the node index corresponds to a different device, also on the network.
        vid = node.sdo["Identity object"]["Vendor-ID"].raw
        pcode = node.sdo["Identity object"]["Product code"].raw
        if vid != VENDOR_ID or pcode != PRODUCT_CODE:
            raise ValueError("Device %d on channel %s doesn't seem to be a FocusTracker (vendor 0x%04x, product 0x%04x)" %
                             (node_idx, channel, vid, pcode))

        return network, node

    def _configure_device(self):
        # Configure for automatic transmission (Transmit Process Data Object)
        # For some background info, see https://canopen.readthedocs.io/en/latest/pdo.html
        # The focus tracker typically sends the position at ~50Hz.
        self.node.nmt.state = "PRE-OPERATIONAL"

        # Read PDO configuration from node
        self.node.tpdo.read()
        # Need to reset, as it can only send one variable at a time. (TPDOs
        # apparently can send 8bytes at a time, while the values take 4 bytes,
        # so maybe it's a bug in the device?)
        self.node.tpdo[1].clear()
        self.node.tpdo[1].add_variable(POS_SDO, 1)
        self.node.tpdo[1].enabled = True
        self.node.tpdo.save()

        # Change state to operational (NMT start)
        self.node.nmt.state = "OPERATIONAL"
        
        self.node.tpdo[1].add_callback(self._on_tpdo)

    def terminate(self):
        """Disconnect from CAN bus."""
        if self.network:

            # Turn "off" the device (stops sending TPDOs)
            self.node.nmt.state = "STOPPED"

            self.network.sync.stop()
            self.network.disconnect()
            self.network = None

        super().terminate()

    def _try_recover(self):
        self.state._set_value(model.HwError("Connection lost, reconnecting..."), force_write=True)
        # Retry to connect to the device, infinitely
        while True:
            if self.network:
                try:
                    self.network.disconnect()
                except Exception:
                    logging.exception("Failed closing the previous network")
                self.network = None
                self.node = None

            try:
                logging.debug("Searching for the device %d on bus %s", self._node_idx, self._channel)
                self.network, self.node = self._connect(self._channel, self._node_idx, self._datasheet)
                self._position_sdo = self.node.sdo[POS_SDO][1]
                self._configure_device()
            except model.HwError as ex:
                logging.info("%s", ex)
            except Exception:
                logging.exception("Unexpected error while trying to recover device")
                raise
            else:
                # We found it back!
                break
        # it now should be accessible again
        self.state._set_value(model.ST_RUNNING, force_write=True)
        logging.info("Recovered device on bus %s", self._channel)

    def updateMetadata(self, md):
        if model.MD_POS_COR in md:
            # Set the MD_POS_COR as Tare zero, so that we don't need to do the
            # subtraction ourselves... and it's stays stored as long as the device
            # is powered up (main advantage).
            pos_cor = md[model.MD_POS_COR]
            if not isinstance(pos_cor, numbers.Real):
                raise ValueError("MD_POS_COR must be a float, but got %s" % (pos_cor,))
            self.node.sdo["AI Tare zero"][1].raw = pos_cor * 1e6
            # Read back the actual value (to read the floating error caused by float32)
            md[model.MD_POS_COR] = self.node.sdo["AI Tare zero"][1].raw * 1e-6
            logging.info("Updated MD_POS_COR to %s", md[model.MD_POS_COR])

            # Force an update of the position, with the new shift
            self._updatePosition(self._read_position())

        model.HwComponent.updateMetadata(self, md)

    def _read_position(self):
        """
        return (float): The current position of the laser on the linear ccd, in m
        """
        try:
            pos = self._position_sdo.raw
        except CanError:
            logging.exception("Error reading position, will try to reconnect")
            # TODO: should this be blocking? Or maybe stop after a timeout?
            self._try_recover()  # Blocks until the device is reconnected
            pos = self._position_sdo.raw

        return pos * 1e-6

    def _on_tpdo(self, pdos):
        """
        Callback when the TPDOs are received
        pdos (pdo.Map): the variables received
        """
        # This normally happens at 50Hz, so no log
        # logging.debug("received TPDO with %s = %s", pdos[0].name, pdos[0].raw)
        pos = pdos[0].raw * 1e-6
        # TODO: this is updated very often, and is blocking the reception. So it
        # might be safer to update the position in a separate thread
        self._updatePosition(pos)

    def _updatePosition(self, pos):
        if self._inverted:
            pos = -pos

        # This normally happens at 50Hz, so no log
        # logging.debug("Reporting new position at %s", pos)
        p = {"z": pos}
        self.position._set_value(p, force_write=True)
        self._last_pos_update = time.time()

    def _get_position(self):
        """
        getter of the .position VA
        """
        if self._last_pos_update < time.time() - MAX_POS_AGE:
            # Force reading the position explicitly (and possibly fail explicitly)
            logging.info("Reading position explicitly as last update was %g s ago",
                         time.time() - self._last_pos_update)
            pos = self._read_position()
            self._updatePosition(pos)

        return self.position._value


# The size of the CCD, plus a margin corresponding to where the gaussian peak
# could be when it's on the border.
INPUT_FV_RANGE = [-50, 4096 + 50]  # px
class FakeRemoteNode(canopen.RemoteNode):

    # Note: in reality, idx and subidx can be either a string or a int.
    # We only support one, so pick the same as in the actual driver.
    _fake_values = [
        # idx, subidx, initial value
        ('AI Input FV', 1, 100),
        ('AI Scaling Factor', 1, 1),
        ('AI Input PV', 1, 100),
        ('AI Tare zero', 1, 0),
        ('AI Net PV', 1, 100),
        ("Identity object", "Vendor-ID", VENDOR_ID),
        ("Identity object", "Product code", PRODUCT_CODE),
        ("Identity object", "Revision number", 0x00010001),
        ("Identity object", "Serial number", 0x123fa4e),
    ]

    def __init__(self, node_idx, object_dict):
        super().__init__(node_idx, object_dict)

        self.tpdo = FakeTPDO(self)
        self.tpdo[1].map.append(self.sdo[POS_SDO][1])
        self._tpdo_updater = util.RepeatingTimer(0.08, self._updateTPDO, "TPDO updater")
        self._tpdo_updater.start()


    def add_sdo(self, rx_cobid, tx_cobid):
        # Called at init, to create the SdoClient
        client = SdoClientOverlay(rx_cobid, tx_cobid, self.object_dictionary)

        # Create fake arrays
        fake_sdos = {}
        for idx, subidx, v in self._fake_values:
            sdo_array = fake_sdos.setdefault(idx, {})
            sdo_array[subidx] = FakeSdoVariable(client[idx][subidx], self.object_dictionary[idx][subidx], init_value=v)

        # Force recomputing everything when Tare zero or Scaling Factor are set
        fake_sdos['AI Tare zero'][1].callback = self._updateTPDO
        fake_sdos['AI Scaling Factor'][1].callback = self._updateTPDO

        client.overlay.update(fake_sdos)

        self.sdo_channels.append(client)
        if self.network is not None:
            self.network.subscribe(client.tx_cobid, client.on_response)
        return client

    def _updateTPDO(self, _=None):
        # Generate a new position, randomly a little bit away from the previous position
        pos = self.sdo["AI Input FV"][1].raw
        pos = max(INPUT_FV_RANGE[0], min(pos + random.randint(-2, 2), INPUT_FV_RANGE[1]))
        self.sdo["AI Input FV"][1].raw = pos
        self.sdo["AI Input PV"][1].raw = pos * self.sdo["AI Scaling Factor"][1].raw
        self.sdo["AI Net PV"][1].raw = self.sdo["AI Input PV"][1].raw - self.sdo["AI Tare zero"][1].raw

        self.tpdo[1][f"{POS_SDO}.{POS_SDO} 1"].raw = self.sdo[POS_SDO][1].raw

        # Send the new pos
        self.tpdo._notify()


class FakeSdoVariable(canopen.sdo.base.Variable):
    """Simulates an SDO Variable object where the raw data can be set and read."""

    def __init__(self, object_sdo, object_dict, init_value, callback=None):
        super().__init__(object_sdo, object_dict)
        self._raw = init_value
        self.callback = callback

    @property
    def raw(self):
        return self._raw

    @raw.setter
    def raw(self, value):
        self._raw = value
        if self.callback:
            self.callback(self)


class FakeTPDO(canopen.pdo.TPDO):

    def read(self):
        pass

    def save(self):
        pass

    def _notify(self):
        for i, map in self.map.items():
            for callback in map.callbacks:
                callback(map)


class SdoClientOverlay(canopen.sdo.SdoClient):
    """Creates a dictionary that can be accessed with dots."""

    def __init__(self, rx_cobid, tx_cobid, od):
        super().__init__(rx_cobid, tx_cobid, od)
        self.overlay = {}

    def __getitem__(self, idx):
        try:
            return self.overlay[idx]
        except KeyError:
            return super().__getitem__(idx)
