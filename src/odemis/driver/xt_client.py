# -*- coding: utf-8 -*-
"""
Created on 16 Aug 2019

@author: Thera Pals, Kornee Kleijwegt, Sabrina Rossberger

Copyright © 2019-2021 Thera Pals, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your
option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see
http://www.gnu.org/licenses/.
"""
import logging
import math
import os
import queue
import re
import threading
import time
import zipfile
from concurrent import futures
from concurrent.futures import CancelledError
from typing import Any, Dict, Optional, Set, Tuple, Union

import Pyro5.api
import msgpack_numpy
import notify2
import numpy
import pkg_resources
from Pyro5.errors import CommunicationError

from odemis import model
from odemis import util
from odemis.model import (CancellableFuture, CancellableThreadPoolExecutor,
                          DataArray, HwError, ProgressiveFuture,
                          StringEnumerated, isasync)
from odemis.util.driver import isNearPosition, estimate_stage_movement_time

Pyro5.api.config.SERIALIZER = 'msgpack'
msgpack_numpy.patch()

XT_RUN = "run"
XT_STOP = "stop"

# Acquisition control messages
GEN_START = "S"  # Start acquisition
GEN_STOP = "E"  # Don't acquire image anymore
GEN_TERM = "T"  # Stop the generator

# Value to use on the FASTEM to activate the immersion mode
COMPOUND_LENS_FOCUS_IMMERSION = 2.0

# List of known supported resolutions.
# Although the API only provide the min/max of resolution in X/Y, not every value
# or combination works. Actually, setting X always changes Y to the corresponding
# value. Note that they are not all the same aspect ratio. "Legacy" resolutions
# are ~8/7 while the new ones are 3/2. The "legacy" resolutions end up with a
# larger vertical field of view.
RESOLUTIONS = (
    (512, 442),
    (1024, 884),
    (2048, 1768),
    (4096, 3536),
    (768, 512),
    (1536, 1024),
    (3072, 2048),
    (6144, 4096),
)

# Xtadapter debian package installation directory which contains xtadapter's zip files
XT_INSTALL_DIR = "/usr/share/xtadapter"


class Package(object):
    """
    A class containing relevant information about a xtadapter package.

    Attributes:
        adapter: The type of the xtadapter.
        bitness: The bitness 32bit or 64bit.
        name: The filename of the package.
        path: The absolute path of the package's zip file or exe file.
        version: The version of the xtadapter.

    """

    def __init__(self):
        self._adapter = None
        self._bitness = None
        self._name = None
        self._path = None
        self._version = None

    @property
    def adapter(self) -> str:
        return self._adapter

    @adapter.setter
    def adapter(self, value: str) -> None:
        self._adapter = value

    @property
    def bitness(self) -> str:
        return self._bitness

    @bitness.setter
    def bitness(self, value: str) -> None:
        self._bitness = value

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        self._name = value

    @property
    def path(self) -> str:
        return self._path

    @path.setter
    def path(self, value: str) -> None:
        self._path = value

    @property
    def version(self) -> str:
        return self._version

    @version.setter
    def version(self, value: str) -> None:
        self._version = value


def check_latest_package(
        directory: str, current_version: str, adapter: str, bitness: str, is_zip: bool
) -> Optional[Package]:
    """
    Check if a latest xtadapter package is available.

    :param directory: The directory to check if a latest xtadapter package is available.
    :param current_version: The currently installed xtadapter version.
    :param adapter: The adapter type e.g. xtadapter, fastem-xtadapter to be used for filtering.
    :param bitness: The executable bitness i.e. 32bit or 64bit to be used for filtering.
    :param is_zip: A flag stating if the package is a zip file, if not a zip file check if it is a folder.
    :return: A class containing relevant information about the latest xtadapter package, if not found return None.

    """
    # Dict where key is the version of xtadapter package and value is information about the package
    version_package = {}
    # Package is named as f"delmic-{adapter}-{bitness}bit-v{version}"
    # delmic-xtadapter-32bit-v1.11.2-dev
    # delmic-xtadapter-32bit-v1.11.2.zip
    # delmic-fastem-xtadapter-64bit-v1.11.2-dev
    regex = r"delmic-([a-zA-Z-]+)-(\d+)bit-v([\d.]+)"
    if is_zip:
        regex += r".*.zip"
    if os.path.isdir(directory):
        for entity in os.listdir(directory):
            result = re.search(regex, entity)
            entity_path = os.path.join(directory, entity)
            if result and (
                    entity.endswith(".zip") if is_zip
                    else os.path.isdir(entity_path) and len(
                        [entity for entity in os.listdir(entity_path) if entity.endswith(".exe")]) == 1
            ):
                package = Package()
                package.adapter = result.group(1)
                package.bitness = result.group(2) + "bit"
                package.version = result.group(3)
                package.name = entity
                package.path = entity_path
                # If not a zip file, assign the path of the executable
                if not is_zip:
                    for entity in os.listdir(package.path):
                        if entity.endswith(".exe"):
                            package.path = os.path.join(package.path, entity)
                            break
                # Filter using adapter type and bitness
                if bitness == package.bitness and adapter == package.adapter:
                    version_package[package.version] = package
        # Check if any version is newer than the current one
        versions = list(version_package)
        if versions:
            # Find the latest version
            latest_version = max(versions, key=lambda s: [int(u) for u in s.split(".")])
            if current_version < latest_version:
                logging.info("The latest version is {}.\n".format(latest_version))
                latest_package = version_package[latest_version]
                if "-dev" in latest_package.name:
                    logging.warning(
                        "{} is a development version.\n".format(latest_package.name)
                    )
                return latest_package
    return None

def check_and_transfer_latest_package(client: 'SEM') -> None:
    """Check if a latest xtadapter package is available and then transfer it."""
    try:
        package = None
        bitness = re.search(r"bitness:\s*([\da-z]+)", client._swVersion)
        bitness = bitness.group(1) if bitness is not None else None # should this be 64 by default?
        adapter = "xtadapter"
        if "xttoolkit" in client._swVersion:
            adapter = "fastem-xtadapter"
            current_version = re.search(r"xtadapter:\s*([\d.]+)", client._swVersion)
        elif "autoscript" in client._swVersion:
            adapter = "autoscript-xtadapter"
            current_version = re.search(r"adapter:\s*autoscript-([\d.]+)", client._swVersion)
        else:
            current_version = re.search(r"xtadapter:\s*([\d.]+)", client._swVersion)
        logging.debug(f"current version {current_version.group(1)} {client._swVersion}")
        current_version = current_version.group(1) if current_version is not None else None
        if current_version is not None and bitness is not None:
            package = check_latest_package(
                directory=XT_INSTALL_DIR,
                current_version=current_version,
                adapter=adapter,
                bitness=bitness,
                is_zip=True,
            )
        if package is not None:
            # Check if it's a proper zip file
            zip_file = zipfile.ZipFile(package.path)
            ret = zip_file.testzip()
            zip_file.close()
            if ret is None:
                # Open the package's zip file as bytes and transfer them
                with open(package.path, mode="rb") as f:
                    data = f.read()
                client.transfer_latest_package(data)
                # Notify the user that a newer xtadpater version is available
                notify2.init("Odemis")
                update = notify2.Notification(
                    "Update Delmic XT Adapter",
                    "Newer version {} is available on ThermoFisher Support PC.\n\n"
                    "How to update?\n\n1. Full stop Odemis and close Delmic XT Adapter.\n"
                    "2. Restart the Delmic XT Adapter to install it.".format(package.version))
                update.set_urgency(notify2.URGENCY_NORMAL)
                update.set_timeout(10000)  # 10 seconds
                update.show()
            else:
                logging.warning("{} is a bad file in {} not transferring latest package.".format(ret, package.path))
    except Exception:
        logging.warning("Failure during transfer latest xtadapter package (non critical)", exc_info=True)

class SEM(model.HwComponent):
    """
    Driver to communicate with XT software on TFS microscopes. XT is the software TFS uses to control their microscopes.
    To use this driver the XT adapter developed by Delmic should be running on the TFS PC. Communication to the
    Microscope server is done via Pyro5.

    By adding the using 'mb-scanner' child instead of a 'scanner' child this driver extends the class with the addition
    of the XTtoolkit functionality. XTtoolkit provides extra functionality for the FAST-EM project which xtlib does not
    provide, it is a development library by TFS. To use this driver the XT adapter developed by Delmic should be
    running on the TFS PC. In the user configuration file `delmic-xt-config.ini` on the Microscope PC, xt_type must
    be set to "xttoolkit".
    """

    def __init__(self, name: str, role: str, children: Dict[str, dict], address: str, daemon=None,
                 **kwargs) -> None:
        """
        :param address: (str)
            server address and port of the Microscope server, e.g. "PYRO:Microscope@localhost:4242"
        """

        model.HwComponent.__init__(self, name, role, daemon=daemon, **kwargs)
        self._proxy_access = threading.Lock()
        try:
            self.server = Pyro5.api.Proxy(address)
            self.server._pyroTimeout = 30  # seconds
            self._swVersion = self.server.get_software_version()
            self._hwVersion = self.server.get_hardware_version()
            if "adapter; autoscript" in self._swVersion:
                raise HwError("The connected server is not an xt server, but an autoscript server. Please check the xt adapter configuration."
                              "The server software version is '%s'." % self._swVersion)
            logging.debug(
                f"Successfully connected to xtadapter with software version {self._swVersion} and "
                f"hardware version {self._hwVersion}")
        except CommunicationError as err:
            raise HwError("Failed to connect to XT server '%s'. Check that the "
                          "uri is correct and XT server is"
                          " connected to the network. %s" % (address, err))
        except OSError as err:
            raise HwError("XT server reported error: %s." % (err,))

        # Transfer latest xtadapter package if available
        # The transferred package will be a zip file in the form of bytes
        check_and_transfer_latest_package(self)

        # Create the scanner type child(ren)
        # Check if at least one of the required scanner types is instantiated
        scanner_types = ["scanner", "fib-scanner", "mb-scanner"]  # All allowed scanners types
        if not any(scanner_type in children for scanner_type in scanner_types):
            raise KeyError("SEM was not given any scanner as child. "
                           "One of 'scanner', 'fib-scanner' or 'mb-scanner' need to be included as child")

        has_detector = "detector" in children

        if "scanner" in children:
            kwargs = children["scanner"]
            self._scanner = Scanner(parent=self, daemon=daemon, has_detector=has_detector, **kwargs)
            self.children.value.add(self._scanner)

        if "mb-scanner" in children:
            if "scanner" in children:
                raise NotImplementedError("The combination of both an multi-beam scanner and single beam scanner at "
                                          "the same time is not supported")
            kwargs = children["mb-scanner"]
            if "xttoolkit" not in self._swVersion.lower():
                raise TypeError("XTtoolkit must be running to instantiate the multi-beam scanner child.")
            self._scanner = MultiBeamScanner(parent=self, daemon=daemon, has_detector=has_detector, **kwargs)
            self.children.value.add(self._scanner)

        if "fib-scanner" in children:
            kwargs = children["fib-scanner"]
            self._fib_scanner = FibScanner(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._fib_scanner)

        # create the stage child, if requested
        if "stage" in children:
            ckwargs = children["stage"]
            self._stage = Stage(parent=self, daemon=daemon, **ckwargs)
            self.children.value.add(self._stage)

        # create the chamber child, if requested
        if "chamber" in children:
            ckwargs = children["chamber"]
            self._chamber = Chamber(parent=self, daemon=daemon, **ckwargs)
            self.children.value.add(self._chamber)

        # create a focuser, if requested
        if "focus" in children:
            ckwargs = children["focus"]
            if "xttoolkit" in self._swVersion.lower():
                self._focus = XTTKFocus(parent=self, daemon=daemon, **ckwargs)
            else:
                self._focus = Focus(parent=self, daemon=daemon, **ckwargs)
            self.children.value.add(self._focus)

        if "detector" in children:
            ckwargs = children["detector"]
            if "xttoolkit" in self._swVersion.lower():
                self._detector = XTTKDetector(parent=self, daemon=daemon, address=address, **ckwargs)
            else:
                self._detector = Detector(parent=self, daemon=daemon, **ckwargs)
            self.children.value.add(self._detector)

    def terminate(self):
        for child in self.children.value:
            child.terminate()

        if hasattr(self, "server"):
            del self.server  # to let the proxy close the connection

        super().terminate()

    def transfer_latest_package(self, data: bytes) -> None:
        """
        Transfer a (new) xtadapter package.
        Note:
            Pyro has a 1 gigabyte message size limitation.
            https://pyro5.readthedocs.io/en/latest/tipstricks.html#binary-data-transfer-file-transfer
        :param data: The package's zip file data in bytes.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.transfer_latest_package(data)

    def move_stage(self, position: Dict[str, float], rel: bool = False) -> None:
        """
        Move the stage the given position in meters. This is non-blocking. Throws an error when the requested position
        is out of range.

        :param position:
            Absolute or relative position to move the stage to per axes in m. Axes are 'x' and 'y'.
        :param rel:
            If True the staged is moved relative to the current position of the stage, by the distance specified in
            position. If False the stage is moved to the absolute position.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.move_stage(position, rel)

    def set_raw_coordinate_system(self, raw_coordinates: bool = True):
        """
        Read raw z coordinate or linked z coordinate when requesting stage coordinates
        :param raw_coordinates: (bool) If True, request raw z stage coordinates, otherwise request linked Z coordinates
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_raw_coordinate_system(raw_coordinates)

    def get_raw_coordinate_system(self):
        """
        Get raw coordinates system of the stage
        :return: (bool) True if stage is read in raw coordinates, otherwise, False
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_raw_coordinate_system()

    def stage_is_moving(self) -> bool:
        """
        :return: True if the stage is moving and False if the stage is not moving.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.stage_is_moving()

    def stop_stage_movement(self) -> None:
        """Stop the movement of the stage."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.stop_stage_movement()

    def get_stage_position(self) -> Dict[str, float]:
        """
        :return: The axes of the stage as keys with their corresponding position.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_stage_position()

    def stage_info(self) -> Dict[str, Any]:
        """
        :return: the unit and range of the stage position.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.stage_info()

    def get_latest_image(self, channel_name: str) -> numpy.ndarray:
        """
        Acquire an image observed via the currently set channel.

        Note: the channel needs to be stopped before an image can be acquired.
        To acquire multiple consecutive images the channel needs to be started and stopped.
        This causes the acquisition speed to be approximately 1 fps.

        :return: The acquired image.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            image = self.server.get_latest_image(channel_name)
            return image

    def set_scan_mode(self, mode: str) -> None:
        """
        Set the scan mode.

        :param mode: Name of desired scan mode, one of: unknown, external, full_frame, spot, or line.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_scan_mode(mode)

    def get_scan_mode(self) -> str:
        """
        Get the scan mode.

        :return: Name of set scan mode, one of: unknown, external, full_frame, spot, or line.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_scan_mode()

    def set_selected_area(self, start_position: Tuple[int, int], size: Tuple[int, int]):
        """
        Specify a selected area in the scan field area.

        :param start_position:
            (x, y) of where the area starts in pixel, (0,0) is at the top left.
        :param size: (width, height) of the size in pixel.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_selected_area(start_position, size)

    def get_selected_area(self) -> Tuple[int, int, int, int]:
        """
        :return: (x, y, width, height) in pixels
            The current selected area. If selected area is not active it returns the stored selected area.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            x, y, width, height = self.server.get_selected_area()
            return x, y, width, height

    def selected_area_info(self) -> Dict[str, Any]:
        """
        :return: the unit and range of set selected area.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.selected_area_info()

    def reset_selected_area(self) -> None:
        """Reset the selected area to select the entire image."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.reset_selected_area()

    def set_scanning_size(self, x: float) -> None:
        """
        Set the size of the to be scanned area (aka field of view or the size, which can be scanned with the current
        settings).

        :param x: size for X in meters.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_scanning_size(x)

    def get_scanning_size(self) -> Tuple[float, float]:
        """
        :return: x and y scanning size in meters.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_scanning_size()

    def scanning_size_info(self) -> Dict[str, Any]:
        """
        :return: the scanning size unit and range.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.scanning_size_info()

    def set_ebeam_spotsize(self, spotsize: float) -> None:
        """
        Setting the spot size of the ebeam.

        :param spotsize: desired spotsize, unitless
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_ebeam_spotsize(spotsize)

    def get_ebeam_spotsize(self) -> float:
        """
        :return: the current spotsize of the electron beam (unitless).
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_ebeam_spotsize()

    def spotsize_info(self) -> Dict[str, Any]:
        """
        :returns: the unit and range of the spotsize. Unit is None means the spotsize is unitless.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.spotsize_info()

    def set_dwell_time(self, dwell_time: float) -> None:
        """
        :param dwell_time: dwell time in seconds
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_dwell_time(dwell_time)

    def get_dwell_time(self) -> float:
        """
        :return: the dwell time in seconds.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_dwell_time()

    def dwell_time_info(self) -> Dict[str, Any]:
        """
        :return: range of the dwell time and corresponding unit.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.dwell_time_info()

    def set_ht_voltage(self, voltage: float) -> None:
        """
        Set the high voltage.

        :param voltage: Desired high voltage value in volt.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_ht_voltage(voltage)

    def get_ht_voltage(self) -> float:
        """
        :return: the HT Voltage in volt.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_ht_voltage()

    def ht_voltage_info(self) -> Dict[str, Any]:
        """
        :return: the unit and range of the HT Voltage.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.ht_voltage_info()

    def blank_beam(self) -> None:
        """Blank the electron beam."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.blank_beam()

    def unblank_beam(self) -> None:
        """Unblank the electron beam."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.unblank_beam()

    def beam_is_blanked(self) -> bool:
        """
        :return: True if the beam is blanked and False if the beam is not blanked.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.beam_is_blanked()

    def pump(self) -> None:
        """Pump the microscope's chamber. Note that pumping takes some time. This is blocking."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            # Pump/vent functions can take a long time, so change timeout
            self.server._pyroTimeout = 300  # seconds
            try:
                self.server.pump()
            except TimeoutError:
                logging.warning("Pumping timed out after %s s. Check the xt user interface for the current status " +
                                "of the chamber", self.server._pyroTimeout)
            finally:
                self.server._pyroTimeout = 30  # seconds

    def get_vacuum_state(self) -> str:
        """
        :return: the vacuum state of the microscope chamber to see if it is pumped or vented,
            possible states: "vacuum", "vented", "prevac", "pumping", "venting","vacuum_error"
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_vacuum_state()

    def vent(self) -> None:
        """Vent the microscope's chamber. Note that venting takes time (appr. 3 minutes). This is blocking."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            # Pump/vent functions can take a long time, so change timeout
            self.server._pyroTimeout = 300  # seconds
            try:
                self.server.vent()
            except TimeoutError:
                logging.warning("Venting timed out after %s s. Check the xt user interface for the current status " +
                                "of the chamber", self.server._pyroTimeout)
            finally:
                self.server._pyroTimeout = 30  # seconds

    def get_pressure(self) -> float:
        """
        :return: the chamber pressure in pascal, or -1 in case the system is vented.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            pressure = self.server.get_pressure()
            return pressure

    def pressure_info(self) -> Dict[str, Any]:
        """
        :return: the unit and range of the pressure.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.pressure_info()

    def home_stage(self) -> None:
        """Home stage asynchronously. This is non-blocking."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.home_stage()

    def is_homed(self) -> bool:
        """
        :return: True if the stage is homed and False otherwise.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.is_homed()

    def set_channel_state(self, name: str, state: bool) -> None:
        """
        Stop or start running the channel. This is non-blocking.

        :param name: name of channel.
        :param state: Desired state of the channel, if True set state to run, if False set state to stop.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_channel_state(name, state)

    def wait_for_state_changed(self, desired_state: str, name: str, timeout: int = 10) -> None:
        """
        Wait until the state of the channel has changed to the desired state, if it has not changed after a certain
        timeout an error will be raised.

        :param desired_state: The state the channel should change into; XT_RUN or XT_STOP.
        :param name: name of channel.
        :param timeout: Amount of time in seconds to wait until the channel state has changed.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.wait_for_state_changed(desired_state, name, timeout)

    def get_channel_state(self, name: str) -> str:
        """
        :param name: name of the channel to request the status from
        :return: the state of the channel: XT_RUN or XT_STOP."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_channel_state(name)

    def get_free_working_distance(self) -> float:
        """
        :return: the free working distance in meters.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_free_working_distance()

    def set_free_working_distance(self, free_working_distance: float) -> None:
        """
        Set the free working distance.

        :param free_working_distance: free working distance in meters.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_free_working_distance(free_working_distance)

    def fwd_info(self) -> Dict[str, Any]:
        """
        :return: the unit and range of the free working distance.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.fwd_info()

    def get_fwd_follows_z(self) -> bool:
        """
        :return: True if Z follows free working distance.
        When Z follows FWD and Z-axis of stage moves, FWD is updated to keep image in focus.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_fwd_follows_z()

    def set_fwd_follows_z(self, follow_z: bool) -> None:
        """
        Set if z should follow the free working distance. When Z follows FWD and Z-axis of stage moves, FWD is updated
        to keep image in focus.

        :param follow_z: True if Z should follow free working distance.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_fwd_follows_z(follow_z)

    def set_autofocusing(self, name: str, state: str) -> None:
        """
        Set the state of autofocus, beam must be turned on. This is non-blocking.

        :param name: Name of one of the electron channels, the channel must be running.
        :param state: XT_RUN or XT_STOP
            If state is start, autofocus starts. State stop, stops the autofocus.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_autofocusing(name, state)

    def is_autofocusing(self, channel_name: str) -> bool:
        """
        :param channel_name: Holds the channels name on which the state is checked.
        :return: True if autofocus is running and False if autofocus is not running.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.is_autofocusing(channel_name)

    def set_auto_contrast_brightness(self, name: str, state: str) -> None:
        """
        Set the state of auto contrast brightness. This is non-blocking.

        :param name: Name of one of the electron channels.
        :param state: XT_RUN or XT_STOP
            IIf state is start, auto contrast brightness starts. State stop, stops the auto contrast brightness.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_auto_contrast_brightness(name, state)

    def is_running_auto_contrast_brightness(self, channel_name: str) -> bool:
        """
        :param channel_name: Holds the channels name on which the state is checked.
        :return: True if auto contrast brightness is running and False if auto contrast brightness is not
        running.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.is_running_auto_contrast_brightness(channel_name)

    def get_beam_shift(self) -> Tuple[float, float]:
        """
        :return: the current beam shift (DC coils position) x and y values in meters.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return tuple(self.server.get_beam_shift())

    def set_beam_shift(self, x_shift: float, y_shift: float) -> None:
        """Set the current beam shift (DC coils position) values in meters."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_beam_shift(x_shift, y_shift)

    def beam_shift_info(self) -> Dict[str, Any]:
        """
        :return: the unit and xy-range of the beam shift (DC coils position).
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.beam_shift_info()

    def get_stigmator(self) -> Tuple[float, float]:
        """
        Retrieves the current stigmator x and y values. This stigmator corrects for the astigmatism of the probe shape.
        In MBSEM systems this stigmator also controls the individual probe shape within the multi-probe pattern
        (stigmator: secondary, physical position column: upper).

        :return: current x and y values of stigmator, unitless.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return tuple(self.server.get_stigmator())

    def set_stigmator(self, x: float, y: float) -> None:
        """
        Sets the stigmator x and y values. This stigmator corrects for the astigmatism of the probe shape.
        In MBSEM systems this stigmator also controls the individual probe shape within the multi-probe pattern
        (stigmator: secondary, physical position column: upper).

        :param x: x value of stigmator, unitless.
        :param y: y value of stigmator, unitless.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_stigmator(x, y)

    def stigmator_info(self) -> Dict[str, Any]:
        """
        Returns the unit and range of the stigmator. This stigmator corrects for the astigmatism of the probe shape.
        In MBSEM systems this stigmator also controls the individual probe shape within the multi-probe pattern
        (stigmator: secondary, physical position column: upper).

        :return: a dictionary with keys: "unit", "range"
            'unit': returns physical unit of the stigmator, typically None.
            'range': returns dict with keys 'x' and 'y' -> returns range of axis (tuple of length 2).
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.stigmator_info()

    def get_rotation(self) -> float:
        """
        :return: the current rotation value in rad.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_rotation()

    def set_rotation(self, rotation: float) -> None:
        """
        :param rotation: the current rotation value in rad.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_rotation(rotation)

    def rotation_info(self) -> Dict[str, Any]:
        """
        :return: the unit and range of the rotation.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.rotation_info()

    def set_resolution(self, resolution: tuple) -> None:
        """
        Set the resolution of the image.

        :return resolution: The resolution of the image in pixels as (width, height). Options: (768, 512),
                            (1536, 1024), (3072, 2048), (6144, 4096)
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_resolution(resolution)

    def get_resolution(self) -> Tuple:
        """
        :return: the resolution of the image as (width, height).
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return tuple(self.server.get_resolution())

    def resolution_info(self) -> Dict[str, Any]:
        """
        :return: the unit and range of the resolution of the image.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.resolution_info()

    def set_beam_power(self, state: bool) -> None:
        """
        Turn on or off the beam power.

        :param state: True to turn on the beam and False to turn off the beam.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_beam_power(state)

    def get_beam_is_on(self) -> bool:
        """
        :return: True if the beam is on and False if the beam is off.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_beam_is_on()

    def get_delta_pitch(self) -> float:
        """
        Get the delta pitch. The pitch is the distance between two neighboring beams within the multiprobe pattern
        which is pre-set by TFS. The delta pitch adjusts the pre-set pitch.

        :return: delta pitch in [um], the adjustment in the pitch from the factory pre-set pitch.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_delta_pitch()

    def set_delta_pitch(self, delta_pitch: float) -> None:
        """
        Set the delta pitch. The pitch is the distance between two neighboring beams within the multiprobe pattern
        which is pre-set by TFS. The delta pitch adjusts the pre-set pitch.

        :param delta_pitch: The adjustment in the pitch from the factory pre-set pitch in [um].
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_delta_pitch(delta_pitch)

    def delta_pitch_info(self) -> Dict[str, Any]:
        """
        :return: a dict with the 'unit' and 'range' of the delta pitch.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.delta_pitch_info()

    def get_secondary_stigmator(self) -> Tuple[float, float]:
        """
        Retrieves the current secondary stigmator x and y values. Within the MBSEM system
        there are two stigmators to correct for both beamlet astigmatism as well
        as multi-probe shape. Only available on MBSEM systems.

        Notes
        -----
        Will be deprecated as soon as FumoBeta is refurbished.
        The primary stigmator does not have its own method, because it gets the
        same value as get_stigmator()

        :return: current x and y values of stigmator, unitless.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return tuple(self.server.get_secondary_stigmator())

    def set_secondary_stigmator(self, x: float, y: float) -> None:
        """
        Sets the secondary stigmator x and y values. Within the MBSEM system
        there are two stigmators to correct for both beamlet astigmatism as well
        as multi-probe shape. Only available on MBSEM systems.

        Notes
        -----
        Will be deprecated as soon as FumoBeta is refurbished.
        The primary stigmator does not have its own method, because it sets the
        same value as set_stigmator()

        :param x: x value of stigmator, unitless.
        :param y: y value of stigmator, unitless.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_secondary_stigmator(x, y)

    def secondary_stigmator_info(self) -> dict:
        """
        Returns the unit and range of the secondary stigmator. Only available on MBSEM systems.

        Notes
        -----
        Will be deprecated as soon as FumoBeta is refurbished.
        The primary stigmator does not have its own method, because it gets the
        same info as stigmator_info()

        :return: a dictionary with keys: "unit", "range"
            'unit': returns physical unit of the stigmator, typically None.
            'range': returns dict with keys 'x' and 'y' -> returns range of axis (tuple of length 2).
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.secondary_stigmator_info()

    def get_pattern_stigmator(self) -> Tuple[float, float]:
        """
        Retrieves the current pattern stigmator x and y values. Only available on MBSEM systems.
        This stigmator corrects for the astigmatism of the multi-probe pattern shape
        (stigmator: primary, physical position column: lower).

        :return: current x and y values of stigmator, unitless.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return tuple(self.server.get_pattern_stigmator())

    def set_pattern_stigmator(self, x: float, y: float) -> None:
        """
        Sets the pattern stigmator x and y values. Only available on MBSEM systems.
        This stigmator corrects for the astigmatism of the multi-probe pattern shape
        (stigmator: primary, physical position column: lower).

        :param x: x value of stigmator, unitless.
        :param y: y value of stigmator, unitless.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_pattern_stigmator(x, y)

    def pattern_stigmator_info(self) -> Dict[str, Any]:
        """
        Returns the unit and range of the pattern stigmator. Only available on MBSEM systems.
        This stigmator corrects for the astigmatism of the multi-probe pattern shape
        (stigmator: primary, physical position column: lower).

        :return: a dictionary with keys: "unit", "range"
            'unit': returns physical unit of the stigmator, typically None.
            'range': returns dict with keys 'x' and 'y' -> returns range of axis (tuple of length 2).
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.pattern_stigmator_info()

    def get_dc_coils(self) -> list:
        """
        Get the four values of the dc coils.

        :return: list of tuples of two floats, len 4
            A list of 4 tuples containing 2 values (floats) of each of the 4 dc coils, in the order:
            [x lower, x upper, y lower, y upper].
            These 4 items describe 4x2 transformation matrix for a required beam shift using DC coils.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_dc_coils()

    def get_use_case(self) -> str:
        """
        Get the current use case state. The use case reflects whether the system
        is currently in multi-beam or single beam mode.

        :return: 'MultiBeamTile' or 'SingleBeamlet'
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_use_case()

    def set_use_case(self, state: str) -> None:
        """
        Set the current use case state. The use case reflects whether the system
        is currently in multi-beam or single beam mode.

        :param state: 'MultiBeamTile' or 'SingleBeamlet'
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_use_case(state)

    def get_mpp_orientation(self) -> float:
        """
        Get the current multi probe pattern orientation in degrees.

        :return: Multi probe orientation in degrees
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_mpp_orientation()

    def mpp_orientation_info(self) -> Dict[str, Any]:
        """
        Get the current multi probe pattern orientation information, contains the range and the unit (defined as
        degrees).

        :return: The range of the rotation for the keyword "range" and the 'degrees' for the keyword "unit"
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.mpp_orientation_info()

    def get_beamlet_index(self) -> Tuple[int, int]:
        """
        Get the current beamlet index which is represented by the two values in a grid (x,y).

        :return: Beamlet index (typical range 1 - 8)
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return tuple(int(i) for i in self.server.get_beamlet_index())

    def set_beamlet_index(self, beamlet_idx: Tuple[int, int]) -> None:
        """
        Set the current beamlet index which is represented by the two values in a grid (x,y).

        :param beamlet_idx: Beamlet index (typical range 1 - 8)
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_beamlet_index(*tuple(float(i) for i in beamlet_idx))

    def beamlet_index_info(self) -> Dict[str, any]:
        """
        Get the current beamlet index information, contains the range in x and y direction (1 - 8).

        :return: The range of the beamlet index for the keyword "range" and in that
            sub dictionary either of the keywords "x"/"y". The unit in the keyword "unit".
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.beamlet_index_info()

    def get_compound_lens_focusing_mode(self) -> float:
        """
        Get the compound lens focus mode. Used to adjust the immersion mode.

        :return: (0<= float <= 10) the focusing mode (0 means no immersion)
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_compound_lens_focusing_mode()

    def set_compound_lens_focusing_mode(self, mode: float) -> None:
        """
        Set the compound lens focus mode. Used to adjust the immersion mode.

        :param mode: (0<= float <= 10) focusing mode (0 means no immersion)
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_compound_lens_focusing_mode(mode)

    def compound_lens_focusing_mode_info(self) -> Dict[str, any]:
        """
        Get the min/max values of the compound lens focus mode.

        :return: The range of the focusing mode for the key "range"
           (as a tuple min/max). The unit for key "unit", as a string or None.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.compound_lens_focusing_mode_info()

    def scan_image(self, channel_name: str = 'electron1') -> None:
        """
        Start the scan of a single image and block until the image is done scanning.
        This function does not return the image, to get the image call get_latest_image after this function.
        Only works for systems running XTToolkit.

        :param channel_name: Name of one of the channels.

        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.scan_image(channel_name)

    def start_autofocusing_flash(self) -> None:
        """
        Start running autofocus through FLASH. This is a blocking call.

        Scan mode must be full frame, use case SingleBeamlet, and the beam must be turned on and unblanked.

        FLASH is a python script provided by TFS to run autofocus, autostigmation and lens alignment.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.start_autofocusing_flash()

    def start_autostigmating_flash(self) -> None:
        """
        Start running auto stigmation through FLASH. This is a blocking call.

        Scan mode must be full frame, use case SingleBeamlet, and the beam must be turned on and unblanked.

        FLASH is a python script provided by TFS to run autofocus, autostigmation and lens alignment.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.start_autostigmating_flash()

    def start_autostig(self, n_images: int = 17, sweep_range: float = 0.015, dwell_time: float = 3e-6,
                       resolution: Tuple[int, int] = (1536, 1024),
                       reduced_area_size: float = 0.5, save_results: bool = False,
                       results_fp: Optional[str] = None) -> Tuple[float, float]:
        """
        Preform the autostig routine for determining the optimal stigmator setting. This is a blocking call.
        NOTE: will be available from xtadapter version 1.11.5 and higher.

        :param n_images:
            Determines the number of images taken per stigmator axis, n_images+floor(n_images/2) images are taken
            per axis. The default is 17.
        :param sweep_range:
            Sweep range, sweep is preformed from -sweep_range to sweep_range. The default is 0.015.
        :param dwell_time:
            Dwell time to use in seconds. The default is 3e-6.
        :param resolution:
            Resolution to use. The default is Size(width=1536, height=1024).
        :param reduced_area_size:
            Reduced area size to use. The default is 0.5.
        :param save_results:
            Whether to save the results. The default is False.
        :param results_fp:
            Folder to save the results in. If None, results will not be saved. The default is None.

        :return optimal_stigmation: Optimal (x, y) stigmator setting.
        :raises: TimeoutError when autostigmation exceeds the timeout of 300 seconds.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server._pyroTimeout = 300  # seconds
            try:
                optimal_stigmation = self.server.start_autostig(n_images, sweep_range, dwell_time, resolution,
                                                                reduced_area_size, save_results, results_fp)
            except TimeoutError:
                logging.error(
                    "Autostigmation timed out after %s s. Check the xt user interface, "
                    "autostigmation might still be running.",
                    self.server._pyroTimeout
                )
                raise
            finally:
                self.server._pyroTimeout = 30  # seconds
            return optimal_stigmation

    def start_auto_lens_centering_flash(self) -> None:
        """
        Start running lens alignment through FLASH. This is a blocking call.

        Scan mode must be full frame, use case SingleBeamlet, and the beam must be turned on and unblanked.

        FLASH is a python script provided by TFS to run autofocus, autostigmation and lens alignment.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.start_auto_lens_centering_flash()

    def set_contrast(self, contrast: float, channel_name: str = 'electron1') -> None:
        """
        Set the contrast of the scanned image to a specified factor.

        :param contrast: Value the brightness should be set to as a factor between 0 and 1.
        :param channel_name: Name of one of the electron channels.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_contrast(contrast, channel_name)

    def get_contrast(self, channel_name: str = 'electron1') -> float:
        """
        Get the contrast of the scanned image.

        :param channel_name: Name of one of the electron channels.

        :return contrast: Returns value of current contrast as a factor between 0 and 1.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_contrast(channel_name)

    def contrast_info(self) -> Dict[str, Any]:
        """
        :return: the contrast unit [-] and range [0, 1].
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.contrast_info()

    def set_brightness(self, brightness: float, channel_name: str = 'electron1') -> None:
        """
        Set the brightness of the scanned image to a specified factor.

        :param brightness: Value the brightness should be set to as a factor between 0 and 1.
        :param channel_name: Name of one of the electron channels.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_brightness(brightness, channel_name)

    def get_brightness(self, channel_name: str = 'electron1') -> float:
        """
        Get the brightness of the scanned image.

        :param channel_name: Name of one of the electron channels.
        :return: Returns value of current brightness as a factor between 0 and 1.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_brightness(channel_name)

    def brightness_info(self) -> Dict[str, Any]:
        """
        :return: the brightness unit [-] and range [0, 1].
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.brightness_info()


class Scanner(model.Emitter):
    """
    This is an extension of the model.Emitter class. It contains Vigilant
    Attributes for magnification, accel voltage, blanking, spotsize, beam shift,
    rotation and dwell time. Whenever one of these attributes is changed, its
    setter also updates another value if needed.
    """

    def __init__(self, name: str, role: str, parent: model.HwComponent, hfw_nomag: float, channel: str = "electron1",
                 has_detector: bool = False, **kwargs):
        """
        :param channel: Name of one of the electron channels.
        :param has_detector: True if a Detector is also controlled. In this case,
          the .resolution, .scale and associated VAs will be provided too.
        """
        model.Emitter.__init__(self, name, role, parent=parent, **kwargs)

        self.channel = channel  # Name of the electron channel used.

        # will take care of executing auto contrast/brightness and auto stigmator asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        self._hfw_nomag = hfw_nomag
        self._has_detector = has_detector

        voltage_info = self.parent.ht_voltage_info()
        init_voltage = numpy.clip(self.parent.get_ht_voltage(), voltage_info['range'][0], voltage_info['range'][1])
        self.accelVoltage = model.FloatContinuous(
            init_voltage,
            voltage_info["range"],
            unit=voltage_info["unit"],
            setter=self._setVoltage
        )

        blanker_choices = {True: 'blanked', False: 'unblanked'}
        if has_detector:
            blanker_choices[None] = 'auto'

        self.blanker = model.VAEnumerated(
            None if has_detector else self.parent.beam_is_blanked(),
            setter=self._setBlanker,
            choices=blanker_choices)

        spotsize_info = self.parent.spotsize_info()
        self.spotSize = model.FloatContinuous(
            self.parent.get_ebeam_spotsize(),
            spotsize_info["range"],
            unit=spotsize_info["unit"],
            setter=self._setSpotSize)

        beam_shift_info = self.parent.beam_shift_info()
        range_x = beam_shift_info["range"]["x"]
        range_y = beam_shift_info["range"]["y"]
        self.shift = model.TupleContinuous(
            self.parent.get_beam_shift(),
            ((range_x[0], range_y[0]), (range_x[1], range_y[1])),
            cls=(int, float),
            unit=beam_shift_info["unit"],
            setter=self._setBeamShift)

        rotation_info = self.parent.rotation_info()
        self.rotation = model.FloatContinuous(
            self.parent.get_rotation(),
            rotation_info["range"],
            unit=rotation_info["unit"],
            setter=self._setRotation)

        scanning_size_info = self.parent.scanning_size_info()
        fov = self.parent.get_scanning_size()[0]
        self.horizontalFoV = model.FloatContinuous(
            fov,
            unit=scanning_size_info["unit"],
            range=scanning_size_info["range"]["x"],
            setter=self._setHorizontalFoV)
        self.horizontalFoV.subscribe(self._onHorizontalFoV)

        mag = self._hfw_nomag / fov
        mag_range_max = self._hfw_nomag / scanning_size_info["range"]["x"][0]
        mag_range_min = self._hfw_nomag / scanning_size_info["range"]["x"][1]
        self.magnification = model.FloatContinuous(mag, unit="",
                                                   range=(mag_range_min, mag_range_max),
                                                   readonly=True)
        # To provide some rough idea of the step size when changing focus
        # Depends on the pixelSize, so will be updated whenever the HFW changes
        self.depthOfField = model.FloatContinuous(1e-6, range=(0, 1e3),
                                                  unit="m", readonly=True)
        self._updateDepthOfField()

        if has_detector:
            dwell_time_info = self.parent.dwell_time_info()
            self.dwellTime = model.FloatContinuous(
                self.parent.get_dwell_time(),
                dwell_time_info["range"],
                unit=dwell_time_info["unit"],
                setter=self._setDwellTime)
            # when the range has changed, clip the current dwell time value to the new range
            self.dwellTime.clip_on_range = True

            rng = self.parent.resolution_info()["range"]
            self._shape = (rng["x"][1], rng["y"][1])
            # pixelSize is the same as MD_PIXEL_SIZE, with scale == 1
            # == smallest size/ between two different ebeam positions
            pxs = (fov / self._shape[0],
                   fov / self._shape[0])
            self.pixelSize = model.VigilantAttribute(pxs, unit="m", readonly=True)

            # .resolution is the number of pixels actually scanned. It's almost
            # fixed to full frame, with the exceptions of the resolutions which
            # are a different aspect ratio from the shape are "more than full frame".
            # So it's read-only and updated when the scale is updated.
            resolution = tuple(self.parent.get_resolution())
            res_choices = set(r for r in RESOLUTIONS
                              if (rng["x"][0] <= r[0] <= rng["x"][1] and rng["y"][0] <= r[1] <= rng["y"][1])
                              )
            self.resolution = model.VAEnumerated(resolution, res_choices, unit="px", readonly=True)
            self._resolution = resolution

            # (float, float) as a ratio => how big is a pixel, compared to pixelSize
            # it basically works the same as binning, but can be float.
            # Defined as the scale to match the allowed resolutions, with pixels
            # always square (ie, scale is always the same in X and Y).
            scale = (self._shape[0] / resolution[0],) * 2
            scale_choices = set((self._shape[0] / r[0],) * 2 for r in res_choices)
            self.scale = model.VAEnumerated(scale, scale_choices, unit="", setter=self._setScale)
            self.scale.subscribe(self._onScale, init=True)  # to update metadata

            # Just to make some code happy
            self.translation = model.VigilantAttribute((0, 0), unit="px", readonly=True)

        emode = self._isExternal()
        self.external = model.BooleanVA(emode, setter=self._setExternal)

        # Refresh regularly the values, from the hardware, starting from now
        self._updateSettings()
        self._va_poll = util.RepeatingTimer(5, self._updateSettings, "Settings polling")
        self._va_poll.start()

    def terminate(self):
        if self._va_poll:
            self._va_poll.cancel()
            self._va_poll = None
        super().terminate()

    def _updateSettings(self) -> None:
        """
        Read all the current settings from the SEM and reflects them on the VAs
        """
        logging.debug("Updating SEM settings")
        try:
            external = self._isExternal()
            if external != self.external.value:
                self.external._value = external
                self.external.notify(external)
            # Read dwellTime and resolution settings from the SEM and reflects them on the VAs only
            # when external is False i.e. the scan mode is 'full_frame'.
            # If external is True i.e. the scan mode is 'external' the dwellTime and resolution are
            # disabled and hence no need to reflect settings on the VAs.
            if self._has_detector and not self.external.value:
                dwell_time = self.parent.get_dwell_time()
                if dwell_time != self.dwellTime.value:
                    self.dwellTime._value = dwell_time
                    self.dwellTime.notify(dwell_time)
                    self._updateResolution()
            voltage = self.parent.get_ht_voltage()
            v_range = self.accelVoltage.range
            if not v_range[0] <= voltage <= v_range[1]:
                logging.info("Voltage {} V is outside of range {}, clipping to nearest value.".format(voltage, v_range))
                voltage = self.accelVoltage.clip(voltage)
            if voltage != self.accelVoltage.value:
                self.accelVoltage._value = voltage
                self.accelVoltage.notify(voltage)
            blanked = self.parent.beam_is_blanked()  # blanker status on the HW
            # if blanker is in auto mode (None), don't care about HW status (self-regulated)
            if self.blanker.value is not None and blanked != self.blanker.value:
                self.blanker._value = blanked
                self.blanker.notify(blanked)
            spot_size = self.parent.get_ebeam_spotsize()
            if spot_size != self.spotSize.value:
                self.spotSize._value = spot_size
                self.spotSize.notify(spot_size)
            beam_shift = self.parent.get_beam_shift()
            if beam_shift != self.shift.value:
                self.shift._value = beam_shift
                self.shift.notify(beam_shift)
            rotation = self.parent.get_rotation()
            if rotation != self.rotation.value:
                self.rotation._value = rotation
                self.rotation.notify(rotation)
            fov = self.parent.get_scanning_size()[0]
            if fov != self.horizontalFoV.value:
                self.horizontalFoV._value = fov
                mag = self._hfw_nomag / fov
                self.magnification._value = mag
                self.horizontalFoV.notify(fov)
                self.magnification.notify(mag)
        except Exception:
            logging.exception("Unexpected failure when polling settings")

    def _setScale(self, value: Tuple[float, float]) -> Tuple[float, float]:
        """
        :param value: (1 < float, 1 < float): increase of size between pixels compared to
            the original pixel size. It will adapt the resolution to
            have the same ROI (just different amount of pixels scanned)
        :return: the actual value used
        """
        # Pick the resolution which matches the scale in X
        res_x = int(round(self._shape[0] / value[0]))
        res = next(r for r in self.resolution.choices if r[0] == res_x)

        # TODO: instead of setting both X and Y, only set X, and read back Y?
        # This would be slightly more flexible in case the XT lib supports other
        # resolutions than the hard-coded ones. For now we assume the hard-coded
        # ones are all the possibles ones.
        self.parent.set_resolution(res)
        self.resolution._set_value(res, force_write=True)

        return value

    def _onScale(self, s) -> None:
        self._updatePixelSize()

    def _updateResolution(self) -> None:
        """
        To be called to read the server resolution and update the corresponding VAs
        """
        resolution = tuple(self.parent.get_resolution())
        if resolution != self.resolution.value:
            scale = (self._shape[0] / resolution[0],) * 2
            self.scale._value = scale  # To not call the setter
            self.resolution._set_value(resolution, force_write=True)
            self.scale.notify(scale)

    def _updatePixelSize(self) -> None:
        """
        Update the pixel size using the horizontalFoV.
        """
        fov = self.horizontalFoV.value
        # The pixel size is equal in x and y.
        pxs = (fov / self._shape[0],
               fov / self._shape[0])
        # pixelSize is read-only, so we change it only via _value
        self.pixelSize._value = pxs
        self.pixelSize.notify(pxs)
        # If scaled up, the pixels are bigger
        pxs_scaled = (pxs[0] * self.scale.value[0], pxs[1] * self.scale.value[1])
        self._metadata[model.MD_PIXEL_SIZE] = pxs_scaled

    def _setDwellTime(self, dwell_time: float) -> float:
        self.parent.set_dwell_time(dwell_time)
        # Cannot set the dwell_time on the parent if the scan mode is 'external'
        # hence return the requested value itself
        if self._isExternal():
            return dwell_time
        return self.parent.get_dwell_time()

    def _setVoltage(self, voltage: float) -> float:
        self.parent.set_ht_voltage(voltage)
        return self.parent.get_ht_voltage()

    def _setBlanker(self, blank: Union[bool, None]) -> Union[bool, None]:
        """
        :param blank: True if the electron beam should blank, False if it should be unblanked,
            None if it should be blanked/unblanked automatically. Only useful when using the Detector or the
            XTTKDetector component. Not useful when operating the SEM in external mode.
        :return: True if the electron beam is blanked, False if it is unblanked. See Notes for edge case,
            None if it should be blanked/unblanked automatically.
        """
        if blank is None:
            # TODO Blanker should explicitly be set based on whether we are scanning or not.
            return None

        if blank:
            self.parent.blank_beam()
        else:
            self.parent.unblank_beam()
        return self.parent.beam_is_blanked()

    def _setSpotSize(self, spotsize: float) -> float:
        self.parent.set_ebeam_spotsize(spotsize)
        return self.parent.get_ebeam_spotsize()

    def _setBeamShift(self, beam_shift: Tuple[float, float]) -> Tuple[float, float]:
        self.parent.set_beam_shift(*beam_shift)
        return self.parent.get_beam_shift()

    def _setRotation(self, rotation: float) -> float:
        self.parent.set_rotation(rotation)
        return self.parent.get_rotation()

    def _setHorizontalFoV(self, fov: float) -> float:
        self.parent.set_scanning_size(fov)
        fov = self.parent.get_scanning_size()[0]
        mag = self._hfw_nomag / fov
        self.magnification._value = mag
        self.magnification.notify(mag)
        return fov

    def _onHorizontalFoV(self, fov: float) -> None:
        self._updateDepthOfField()
        if self._has_detector:
            # the dwell time range is dependent on the magnification/horizontalFoV
            self._updateDwellTimeRng()
            self._updatePixelSize()

    def _updateDepthOfField(self) -> None:
        fov = self.horizontalFoV.value
        # Formula was determined by experimentation
        K = 100  # Magical constant that gives a not too bad depth of field
        dof = K * (fov / 1024)
        self.depthOfField._set_value(dof, force_write=True)

    def _updateDwellTimeRng(self) -> None:
        """The dwell time range is dependent on the magnification/horizontalFoV, the range whenever the fov updates."""
        self.dwellTime._set_range(self.parent.dwell_time_info()["range"])

    def _isExternal(self) -> bool:
        """
        :return: True if the scan mode is 'external', False if the scan mode is different than 'external'.
        """
        return self.parent.get_scan_mode().lower() == "external"

    def _setExternal(self, external: bool) -> bool:
        """
        Switching between internal and external control of the SEM.

        :param external: True is external, False is full frame mode.
        :return: True if the scan mode should be 'external'.
            False if the scan mode should be internally controlled by the SEM.
        """
        scan_mode = "external" if external else "full_frame"
        self.parent.set_scan_mode(scan_mode)
        # The dwellTime and scale VA setter can only reflect changes on the SEM server side (parent)
        # after the external VA is set to False i.e. 'full_frame'
        if not external and self._has_detector:
            if self.dwellTime.value != self.parent.get_dwell_time():
                # Set the VA value again to reflect changes on the parent
                self.dwellTime.value = self.dwellTime.value
            if self.resolution.value != tuple(self.parent.get_resolution()):
                # Set the VA value again to reflect changes on the parent
                self.scale.value = self.scale.value
        return external

    def prepareForScan(self) -> None:
        """
        Make sure the beam is unblanked when the blanker is in 'auto' mode before starting to scan.
        """
        if self.blanker.value is None:
            self.parent.unblank_beam()

    def finishScan(self) -> None:
        """
        Make sure the beam is blanked when the blanker is in 'auto' mode at the end of scanning.
        """
        if self.blanker.value is None:
            self.parent.blank_beam()


class FibScanner(model.Emitter):
    """
    This is an extension of the model.Emitter class for controlling the FIB (focused ion beam). Currently via XTLib
    only minimal control of the FIB is possible.
    """

    def __init__(self, name: str, role: str, parent: model.HwComponent, channel: str = "ion2", **kwargs) -> None:
        """

        :param name:
        :param role:
        :param parent:
        :param channel: Specifies the type of scanner (alphabetic part) and the quadrant it is displayed in
            (numerical part) on which the scanning feed is displayed on the Microscope PC in the Microscope
            control window of TFS. The quadrants are numbered 1 (top left) - 4 (right bottom).
            Usually the top right quadrant, ion2 is set as default for the FIB image.
        """
        model.Emitter.__init__(self, name, role, parent=parent, **kwargs)

        self.channel = channel  # Name of the ion channel used.

        # Fictional values for the interface. Xtlib doesn't support reading/controlling these values
        self._shape = (4096, 4096)
        res = self._shape[:2]
        self.resolution = model.ResolutionVA(res, (res, res), readonly=True)

    def prepareForScan(self) -> None:
        """
        Make sure the scan mode is in "full_frame" and not in "external" before starting to scan.
        """
        # Currently the XT interface autoblanks the FIB, control of the FIB blanker is
        # not supported so we do not blank the beam at the start and end of the scan.

        # Note: ideally this would be based on an external VA, which when put to
        # 'auto' would switch to full frame at the start of a scan.
        if self.parent.get_scan_mode() != "full_frame":
            self.parent.set_scan_mode("full_frame")
            current_mode = self.parent.get_scan_mode()
            if current_mode != "full_frame":
                raise HwError("Couldn't set full_frame as scan mode on the XT client mode. Current mode is: %s" %
                              current_mode)

    def finishScan(self) -> None:
        """Call when done with scanning."""
        # We do not change the scan mode, because it is fine to leave it in full frame.
        pass


class Detector(model.Detector):
    """
    This is an extension of model.Detector class. It performs the main functionality
    of the SEM. It sets up a Dataflow and notifies it every time that an SEM image
    is captured.
    """

    def __init__(self, name: str, role: str, parent: model.HwComponent, **kwargs) -> None:
        # The acquisition is based on a FSM that roughly looks like this:
        # Event\State |    Stopped    |   Acquiring    | Receiving data |
        #    START    | Ready for acq |        .       |       .        |
        #    DATA     |       .       | Receiving data |       .        |
        #    STOP     |       .       |     Stopped    |    Stopped     |
        #    TERM     |     Final     |      Final     |     Final      |

        model.Detector.__init__(self, name, role, parent=parent, **kwargs)
        self._shape = (256,)  # Depth of the image
        self.data = SEMDataFlow(self)

        if hasattr(self.parent, "_scanner") and hasattr(self.parent, "_fib_scanner"):
            self.scanner = StringEnumerated(self.parent._scanner.name,
                                            choices={self.parent._scanner.name, self.parent._fib_scanner.name},
                                            setter=self._set_scanner)
            self._set_scanner(self.parent._scanner.name)  # Call setter to instantiate ._scanner attribute
        elif hasattr(self.parent, "_scanner"):
            self._scanner = self.parent._scanner
        elif hasattr(self.parent, "_fib_scanner"):
            self._scanner = self.parent._fib_scanner
        else:
            raise ValueError("No Scanner available")

        brightness_info = self.parent.brightness_info()
        self.brightness = model.FloatContinuous(
            self.parent.get_brightness(),
            brightness_info["range"],
            unit=brightness_info["unit"],
            setter=self._setBrightness)

        contrast_info = self.parent.contrast_info()
        self.contrast = model.FloatContinuous(
            self.parent.get_contrast(),
            contrast_info["range"],
            unit=contrast_info["unit"],
            setter=self._setContrast)

        self._genmsg = queue.Queue()  # GEN_*
        self._generator = None

        # Refresh regularly the values, from the hardware, starting from now
        self._updateSettings()
        self._va_poll = util.RepeatingTimer(5, self._updateSettings, "Settings polling detector")
        self._va_poll.start()

    def terminate(self) -> None:
        if self._generator:
            self.stop_generate()
            self._genmsg.put(GEN_TERM)
            self._generator.join(5)
            self._generator = None
        if self._va_poll:
            self._va_poll.cancel()
            self._va_poll = None
        super().terminate()

    def start_generate(self) -> None:
        self._genmsg.put(GEN_START)
        if not self._generator or not self._generator.is_alive():
            logging.info("Starting acquisition thread")
            self._generator = threading.Thread(target=self._acquire,
                                               name="XT acquisition thread")
            self._generator.start()

    def stop_generate(self) -> None:
        self._scanner.finishScan()
        self._genmsg.put(GEN_STOP)

    def _acquire(self) -> None:
        """
        Acquisition thread
        Managed via the ._genmsg Queue
        """
        try:
            while True:
                # Wait until we have a start (or terminate) message
                self._acq_wait_start()
                logging.debug("Preparing acquisition")
                while True:
                    if self._acq_should_stop():
                        break
                    # TODO When switching e-beam <--> FIB, handle calling finishScan on the old scanner and
                    #  prepareForScan on the new scanner.
                    self._scanner.prepareForScan()
                    self.parent.set_channel_state(self._scanner.channel, True)
                    # The channel needs to be stopped to acquire an image, therefore immediately stop the channel.
                    self.parent.set_channel_state(self._scanner.channel, False)

                    md = self._scanner._metadata.copy()
                    if hasattr(self._scanner, "dwellTime"):
                        md[model.MD_DWELL_TIME] = self._scanner.dwellTime.value
                    if hasattr(self._scanner, "rotation"):
                        md[model.MD_ROTATION] = self._scanner.rotation.value

                    # Estimated time for an acquisition is the dwell time times the total amount of pixels in the image.
                    if hasattr(self._scanner, "dwellTime") and hasattr(self._scanner, "resolution"):
                        n_pixels = self._scanner.resolution.value[0] * self._scanner.resolution.value[1]
                        est_acq_time = self._scanner.dwellTime.value * n_pixels
                    else:
                        # Acquisition time is unknown => assume it will be long
                        est_acq_time = 5 * 60  # 5 minutes

                    # Wait for the acquisition to be received
                    logging.debug("Starting one image acquisition")
                    try:
                        if self._acq_wait_data(est_acq_time + 20):
                            logging.debug("Stopping measurement early")
                            self.stop_acquisition()
                            break
                    except TimeoutError as err:
                        logging.error(err)
                        self.stop_acquisition()
                        break

                    # Retrieve the image
                    image = self.parent.get_latest_image(self._scanner.channel)
                    md.update(self._metadata)
                    da = DataArray(image, md)
                    logging.debug("Notify dataflow with new image.")
                    self.data.notify(da)
            logging.debug("Acquisition stopped")
        except TerminationRequested:
            logging.debug("Acquisition thread requested to terminate")
        except Exception as err:
            logging.exception("Failure in acquisition thread: {}".format(err))
        finally:
            self._generator = None

    def stop_acquisition(self) -> None:
        """
        Stop acquiring images.
        """
        # Stopping the channel once stops it after the acquisition is done.
        self.parent.set_channel_state(self._scanner.channel, False)
        if self.parent.get_channel_state(self._scanner.channel) == XT_STOP:
            return
        else:  # Channel is canceling
            logging.debug("Channel not fully stopped will try again.")
            time.sleep(0.5)
            # Stopping it twice does a full stop.
            self.parent.set_channel_state(self._scanner.channel, False)

    def _acq_should_stop(self, timeout: Optional[float] = None) -> bool:
        """
        Indicate whether the acquisition should now stop or can keep running.
        Non blocking.
        Note: it expects that the acquisition is running.

        :param timeout: timeout to wait for the message from the acquisition queue
        :return: True if needs to stop, False if can continue
        raise TerminationRequested: if a terminate message was received
        """
        try:
            if timeout is None:
                msg = self._get_acq_msg(block=False)
            else:
                msg = self._get_acq_msg(timeout=timeout)
        except queue.Empty:
            # No message so no need to stop
            return False

        if msg == GEN_TERM:
            raise TerminationRequested()
        elif msg == GEN_STOP:
            return True
        else:
            logging.warning("Skipped message: %s", msg)
            return False

    def _acq_wait_data(self, timeout: float = 0) -> bool:
        """
        Block until data or a stop message is received.
        Note: it expects that the acquisition is running.

        :param timeout: (0<=float) how long to wait to check (use 0 to not wait)
        :return: True if needs to stop, False if data is ready
        :raises: TerminationRequested if a terminate message was received
        """
        tend = time.time() + timeout
        t = time.time()
        logging.debug("Waiting for %g s:", tend - t)
        while self.parent.get_channel_state(self._scanner.channel) != XT_STOP:
            t = time.time()
            if t > tend:
                raise TimeoutError("Acquisition timeout after %g s" % timeout)

            if self._acq_should_stop(timeout=0.1):
                return True

        return False  # Data received

    def _acq_wait_start(self) -> None:
        """
        Blocks until the acquisition should start.
        Note: it expects that the acquisition is stopped.

        :raises: TerminationRequested if a terminate message was received
        """
        while True:
            msg = self._get_acq_msg(block=True)
            if msg == GEN_TERM:
                raise TerminationRequested()
            elif msg == GEN_START:
                return

            # Duplicate Stop
            logging.debug("Skipped message %s as acquisition is stopped", msg)

    def _get_acq_msg(self, **kwargs) -> str:
        """
        Read one message from the acquisition queue

        :return: message
        :raises: queue.Empty: if no message on the queue
        """
        msg = self._genmsg.get(**kwargs)
        if msg in (GEN_START, GEN_STOP, GEN_TERM):
            logging.debug("Acq received message %s", msg)
        else:
            logging.warning("Acq received unexpected message %s", msg)
        return msg

    def _set_scanner(self, scanner_name: str) -> str:
        """
        Setter for changing the scanner which will be used. The correct scanner object is also updated in
        ._scanner.

        :param scanner_name: contains mode, can be either 'scanner' or 'fib-scanner'
        :return: The set mode
        """
        if scanner_name == self.parent._scanner.name:
            self._scanner = self.parent._scanner
        elif scanner_name == self.parent._fib_scanner.name:
            self._scanner = self.parent._fib_scanner

        return scanner_name

    def _updateSettings(self) -> None:
        """
        Reads all the current settings from the Detector and reflects them on the VAs
        """
        brightness = self.parent.get_brightness(self._scanner.channel)
        if brightness != self.brightness.value:
            self.brightness._value = brightness
            self.brightness.notify(brightness)
        contrast = self.parent.get_contrast(self._scanner.channel)
        if contrast != self.contrast.value:
            self.contrast._value = contrast
            self.contrast.notify(contrast)

    def _setBrightness(self, brightness: float) -> float:
        self.parent.set_brightness(brightness, self._scanner.channel)
        return self.parent.get_brightness(self._scanner.channel)

    def _setContrast(self, contrast: float) -> float:
        self.parent.set_contrast(contrast, self._scanner.channel)
        return self.parent.get_contrast(self._scanner.channel)

    @isasync
    def applyAutoContrastBrightness(self) -> futures.Future:
        """
        Wrapper for running the automatic setting of the contrast brightness functionality asynchronously. It
        automatically sets the contrast and the brightness via XT, the beam must be turned on and unblanked. Auto
        contrast brightness functionality works best if there is a feature visible in the image. This call is
        non-blocking.

        :return: Future object

        """
        # Create ProgressiveFuture and update its state
        est_start = time.time() + 0.1
        f = ProgressiveFuture(start=est_start,
                              end=est_start + 20)  # Rough time estimation
        f._auto_contrast_brightness_lock = threading.Lock()
        f._must_stop = threading.Event()  # Cancel of the current future requested
        f.task_canceller = self._cancelAutoContrastBrightness
        f._channel_name = self._scanner.channel
        return self._scanner._executor.submitf(f, self._applyAutoContrastBrightness, f)

    def _applyAutoContrastBrightness(self, future: futures.Future) -> None:
        """
        Starts applying auto contrast brightness and checks if the process is finished for the ProgressiveFuture object.

        :param future: the future to start running.
        """
        channel_name = future._channel_name
        with future._auto_contrast_brightness_lock:
            if future._must_stop.is_set():
                raise CancelledError()
            self.parent.set_auto_contrast_brightness(channel_name, XT_RUN)
            time.sleep(0.5)  # Wait for the auto contrast brightness to start

        # Wait until the microscope is no longer performing auto contrast brightness
        while self.parent.is_running_auto_contrast_brightness(channel_name):
            future._must_stop.wait(0.1)
            if future._must_stop.is_set():
                raise CancelledError()

    def _cancelAutoContrastBrightness(self, future: futures.Future) -> bool:
        """
        Cancels the auto contrast brightness. Non-blocking.

        :param future: the future to stop.
        :return: True if it successfully cancelled (stopped) the move.
        """
        future._must_stop.set()  # Tell the thread taking care of auto contrast brightness it's over.

        with future._auto_contrast_brightness_lock:
            logging.debug("Cancelling auto contrast brightness")
            try:
                self.parent.set_auto_contrast_brightness(future._channel_name, XT_STOP)
                return True
            except OSError as error_msg:
                logging.warning("Failed to cancel auto brightness contrast: %s", error_msg)
                return False


# Very approximate values
PRESSURE_VENTED = 100e3  # Pa
PRESSURE_PUMPED = 10e-3  # Pa


class Chamber(model.Actuator):
    """
    Component representing the vacuum chamber. Changing the pressure is possible by moving the "vacuum" axis,
    which accepts two position values: 0 for vented, 1 for vacuum.
    """

    def __init__(self, name: str, role: str, parent: model.HwComponent, **kwargs) -> None:
        axes = {"vacuum": model.Axis(choices={PRESSURE_VENTED: "vented",
                                              PRESSURE_PUMPED: "vacuum"})}
        model.Actuator.__init__(self, name, role, parent=parent, axes=axes, **kwargs)

        self.position = model.VigilantAttribute({}, readonly=True)
        info = self.parent.pressure_info()
        self.pressure = model.FloatContinuous(info["range"][0], info["range"], readonly=True, unit=info["unit"])
        self._refreshPressure()

        self._polling_thread = util.RepeatingTimer(5, self._refreshPressure, "Pressure polling")
        self._polling_thread.start()

        self._executor = CancellableThreadPoolExecutor(max_workers=1)

    def stop(self, axes: Optional[str] = None) -> None:
        self._executor.cancel()
        if self._executor._queue:
            logging.warning("Stopping the pumping/venting process is not supported.")

    @isasync
    def moveRel(self, shift):
        raise NotImplementedError("Relative movements are not implemented for vacuum control. Use moveAbs instead.")

    @isasync
    def moveAbs(self, pos: Dict[str, float]) -> futures.Future:
        self._checkMoveAbs(pos)
        return self._executor.submit(self._changePressure, pos["vacuum"])

    def _changePressure(self, target: int) -> None:
        """
        :param target: 0 or 1; 0 for pumping to vacuum, 1 for venting the chamber
        """
        self._refreshPressure()
        if target == self.position.value["vacuum"]:
            logging.info("Chamber state already %s, doing nothing.", target)
            return

        if target == PRESSURE_PUMPED:
            self.parent.pump()
        else:
            self.parent.vent()
        self._refreshPressure()

    def _refreshPressure(self) -> None:
        # Position (vacuum state)
        state = self.parent.get_vacuum_state()
        val = {"vacuum": PRESSURE_PUMPED if state == "vacuum" else PRESSURE_VENTED}
        self.position._set_value(val, force_write=True)

        # Pressure
        pressure = self.parent.get_pressure()
        if pressure != -1:  # -1 is returned when the chamber is vented
            self.pressure._set_value(pressure, force_write=True)
            logging.debug("Updated chamber pressure, %s Pa, vacuum state %s.", pressure, val["vacuum"])
        else:
            pressure = 100e3  # ambient pressure, Pa
            self.pressure._set_value(pressure, force_write=True)
            logging.warning("Couldn't read pressure value, assuming ambient pressure %s.", pressure)

    def terminate(self) -> None:
        self._polling_thread.cancel()
        super().terminate()


class TerminationRequested(Exception):
    """
    Generator termination requested.
    """
    pass


class SEMDataFlow(model.DataFlow):
    """
    This is an extension of model.DataFlow. It receives notifications from the
    detector component once the SEM output is captured. This is the dataflow to
    which the SEM acquisition streams subscribe.
    """

    def __init__(self, detector: model.Detector):
        """
        :param detector: the detector that the dataflow corresponds to
        """
        model.DataFlow.__init__(self)
        self._detector = detector

    # start/stop_generate are _never_ called simultaneously (thread-safe)
    def start_generate(self) -> None:
        self._detector.start_generate()

    def stop_generate(self) -> None:
        self._detector.stop_generate()


STAGE_WAIT_DURATION = 20e-03  # s
STAGE_FRACTION_TOTAL_MOVE = 0.01  # tolerance of the requested stage movement
# The accuracy of the stage was checked using the Move() API command. The accuracy of the movement was not dependent
# on the step size (100µm to 1mm). In the worse cases, we read 1µm inaccuracy in X & Y, and 5µm in Z. With some margin,
# we use 10µm tolerance to detect the stage has reached the target position.
STAGE_TOL_LINEAR = 10e-06  # in m, minimum tolerance
STAGE_TOL_ROTATION = 0.00436  # in radians (0.25 degrees), minimum tolerance
# In the scenarios, where the stage movement was more than 10 percent away from the requested position after the
# maximum waiting time, we raise an error such that the caller can detect the issue.
# When the stage moves to the requested position within this margin but still not within the above mentioned tolerances,
# only a warning is logged and subsequent stage movements are carried out as it is considered safe.
STAGE_FRACTION_MOVE_FAILURE = 0.1  # raise error if the current positon is more than 10% of the requested move
STAGE_TOL_LINEAR_FAILURE = 100e-06  # in m, minimum tolerance for detecting failure
STAGE_TOL_ROTATION_FAILURE = 0.01745  # in radians (1 degrees), minimum tolerance for detecting failure


class Stage(model.Actuator):
    """
    This is an extension of the model.Actuator class. It provides functions for
    moving the TFS stage and updating the position.
    """

    def __init__(self, name: str, role: str, parent: model.HwComponent, rng: dict = None, raw_coordinates: bool = False,
                 **kwargs) -> None:
        """
        :param rng: The range of stage axes in which linear axes x,y and z are in "meters" and rotational axes are
         in "radians"
        :param raw_coordinates: True, if stage is read in raw coordinates i.e. the z is the distance of the stage from
         chamber floor. When False, z is the stage distance from the SEM pole piece. x and y should not theoretically
         change. In practice offset correction in x and y is applied to keep these axes same across both the coordinate
         systems.
        """
        self._raw_coordinates = raw_coordinates
        # We use a "hybrid" coordinate system: the main advantage of the raw coordinate system
        # is that the Z axis is never "linked" to the working distance. When the Z axis is linked,
        # it's oriented in the other direction (0 is at the pole-piece, and an increase goes down),
        # and is dependent on the SEM working distance (focus).
        # In raw coordinates, Z always means the same thing, independent of what the user has done
        # in the GUI. However, for the other axes, there might be an offset, but as it's convenient
        # to show the same value as in the TFS GUI, we compensate for that offset.
        self._raw_offset = {"x": 0, "y": 0, "rx": 0, "rz": 0}

        if rng is None:
            rng = {}
        stage_info = parent.stage_info()
        if "x" not in rng:
            rng["x"] = stage_info["range"]["x"]
        if "y" not in rng:
            rng["y"] = stage_info["range"]["y"]
        if "z" not in rng:
            rng["z"] = stage_info["range"]["z"]
        if "rx" not in rng:
            rng["rx"] = stage_info["range"]["t"]
        if "rz" not in rng:
            rng["rz"] = stage_info["range"]["r"]

        axes_def = {
            "x": model.Axis(unit=stage_info["unit"]["x"], range=rng["x"]),
            "y": model.Axis(unit=stage_info["unit"]["y"], range=rng["y"]),
            "z": model.Axis(unit=stage_info["unit"]["z"], range=rng["z"]),
            "rx": model.Axis(unit=stage_info["unit"]["t"], range=rng["rx"]),
            "rz": model.Axis(unit=stage_info["unit"]["r"], range=rng["rz"]),
        }

        model.Actuator.__init__(self, name, role, parent=parent, axes=axes_def,
                                **kwargs)
        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        self.position = model.VigilantAttribute({}, unit=stage_info["unit"],
                                                readonly=True)
        self._updatePosition()

        # update the offset if raw stage coordinates are read
        self._switch_coordinate_system(raw_coordinates)

        # Refresh regularly the position
        self._pos_poll = util.RepeatingTimer(5, self._refreshPosition, "Stage position polling")
        self._pos_poll.start()

    def terminate(self):
        if self._executor:
            self._executor.cancel()
            self._executor.shutdown()
            self._executor = None
        if self._pos_poll:
            self._pos_poll.cancel()
            self._pos_poll = None
        super().terminate()

    def _switch_coordinate_system(self, raw_coordinates: bool) -> None:
        """
        Select the coordinate system for the stage and calculate the offset in raw coordinates,
        to keep the x,y,rx,rz coordinates the same as the linked coordinates.
        :param raw_coordinates: True if stage coordinates are read in raw system else False
        """
        if raw_coordinates:
            self.parent.set_raw_coordinate_system(False)
            pos_linked = self._getPosition()
            self.parent.set_raw_coordinate_system(True)
            pos = self._getPosition()
            for axis in self._raw_offset.keys():
                self._raw_offset[axis] = pos_linked[axis] - pos[axis]
            logging.debug(f"The raw coordinates offset is {self._raw_offset}. "
                          f"Computed from raw stage coordinates: {pos}, specimen stage coordinates: {pos_linked}")
        else:
            # Make sure the system is read in the linked coordinate system
            try:
                self.parent.set_raw_coordinate_system(False)
            except (AttributeError, OSError) as error_msg:
                logging.warning("Delmic XT Adapter >= 1.12.0 is required to set the raw coordinate system. "
                                "Failed to call the raw coordinate system for the stage: %s", error_msg)
                # Do not raise an error if non-raw coordinates are requested, because non-raw is the default in old
                # versions of the xtadapter

    def _updatePosition(self) -> None:
        """
        update the position VA
        """
        old_pos = self.position.value
        pos = self._getPosition()
        if self._raw_coordinates:
            # correct for the offset such that the stage coordinates displayed in
            # TFS software is the same as Odemis
            for axis, offset in self._raw_offset.items():
                if axis in pos:
                    pos[axis] += offset

            # Make sure the full rotations are within the range (because the SEM actually reports
            # values within -2pi -> 2pi rad)
            for an in ("rx", "rz"):
                rng = self.axes[an].range
                # To handle both rotations 0->2pi and inverted: -2pi -> 0.
                if util.almost_equal(rng[1] - rng[0], 2 * math.pi):
                    pos[an] = (pos[an] - rng[0]) % (2 * math.pi) + rng[0]

        self.position._set_value(self._applyInversion(pos), force_write=True)
        if old_pos != self.position.value:
            logging.debug("Updated position to %s", self.position.value)

    def _refreshPosition(self) -> None:
        """
        Called regularly to update the current position
        """
        # We don't use the VA setters, to avoid sending back to the hardware a
        # set request
        logging.debug("Updating SEM stage position")
        try:
            self._updatePosition()
        except Exception:
            logging.exception("Unexpected failure when updating position")

    def _getPosition(self) -> Dict[str, float]:
        """Get position and translate the axes names to be Odemis compatible."""
        pos = self.parent.get_stage_position()
        pos["rx"] = pos.pop("t")
        pos["rz"] = pos.pop("r")
        # Make sure the full rotations are within the range (because the SEM
        # actually allows rotation in both directions)
        for an in ("rx", "rz"):
            rng = self.axes[an].range
            # To handle both rotations 0->2pi and inverted: -2pi -> 0.
            # TODO: for inverted rotations of 2pi, it would probably be more
            # user-friendly to still report 0->2pi as range.
            if util.almost_equal(rng[1] - rng[0], 2 * math.pi):
                pos[an] = (pos[an] - rng[0]) % (2 * math.pi) + rng[0]
        return pos

    def _moveTo(self, future: futures.Future, pos: Dict[str, float], rel: bool = False, timeout: float = 60) -> None:
        with future._moving_lock:
            try:
                if future._must_stop.is_set():
                    raise CancelledError()
                if rel:
                    logging.debug("Moving by shift {}".format(pos))
                else:
                    logging.debug("Moving to position {}".format(pos))

                # Correct for offset compensation in absolute movements
                if self._raw_coordinates and not rel:
                    for axis, offset in self._raw_offset.items():
                        if axis in pos:
                            pos[axis] -= offset

                if "rx" in pos.keys():
                    pos["t"] = pos.pop("rx")
                if "rz" in pos.keys():
                    pos["r"] = pos.pop("rz")

                orig_pos = self.parent.get_stage_position()

                self.parent.move_stage(pos, rel=rel)
                time.sleep(0.1)  # It takes a little while before the stage is being reported as moving

                # Get the target position in absolute coordinates
                if rel:
                    target_pos = {ax: val + orig_pos[ax] for ax, val in pos.items()}
                else:
                    target_pos = pos

                # Wait until the move is over.
                # Don't check for future._must_stop because anyway the stage will
                # stop moving, and so it's nice to wait until we know the stage is
                # not moving.
                moving = True
                tstart = time.time()
                last_pos_update = 0
                while moving:
                    # Take the opportunity to update .position (every 100 ms)
                    now = time.time()
                    if now - last_pos_update > 0.1:
                        self._updatePosition()
                        last_pos_update = now

                    if time.time() > tstart + timeout:
                        self.parent.stop_stage_movement()
                        logging.error("Timeout after submitting stage move. Aborting move.")
                        break

                    # Wait for a little while so that we do not keep using the CPU all the time.
                    time.sleep(STAGE_WAIT_DURATION)
                    moving = self.parent.stage_is_moving()
                    if not moving:
                        # Be a little careful, because sometimes, half-way through a move
                        # the stage is reported not moving for a short while (usually,
                        # when one axis finished moving, and another is about to start).
                        self._updatePosition()
                        logging.debug("Confirming the stage really stopped")

                        time.sleep(STAGE_WAIT_DURATION)
                        moving = self.parent.stage_is_moving()
                        if moving:
                            logging.warning("Stage reported stopped but moving again, will wait longer")
                else:
                    logging.debug("Stage move reported completed")

                # If it was cancelled, Abort() has stopped the stage before, and
                # we still have waited until the stage stopped moving. Now let
                # know the user that the move is not complete.
                if future._must_stop.is_set():
                    raise CancelledError()

                # The stage is not moving anymore, however in some rare cases, the server still reports the old
                # position for a short while (typically < 1s). This can cause all sorts of confusion in the calling
                # code, as it would seem that the move didn't have any effect, and later on, after the position
                # is eventually updated that the stage has moved unexpectedly. So, wait until close to the target position.

                # TODO: base the timeout on stage movement time estimation, needs to be checked on hardware
                wait_stage_move = 10  # s
                expected_end_time = time.time() + wait_stage_move  # s

                # Update rotational and linear tolerances according to the magnitude of requested change
                linear_axes_to_check = {"x", "y", "z"}.intersection(target_pos.keys())
                rotational_axes_to_check = {"t", "r"}.intersection(target_pos.keys())
                current_pos = self.parent.get_stage_position()
                movement_req = {ax: STAGE_FRACTION_TOTAL_MOVE * abs(target_pos[ax] - current_pos[ax]) for ax in
                                target_pos.keys()}
                tol_linear = STAGE_TOL_LINEAR
                tol_rotation = STAGE_TOL_ROTATION
                if linear_axes_to_check:
                    movement_req_linear = [movement_req[ax] for ax in linear_axes_to_check]
                    tol_linear = max(min(movement_req_linear), STAGE_TOL_LINEAR)  # m
                    tol_lin_failure_per_axis = {ax: max(abs(target_pos[ax] - orig_pos[ax]) *
                                                STAGE_FRACTION_MOVE_FAILURE, STAGE_TOL_LINEAR_FAILURE)
                                                for ax in linear_axes_to_check}

                if rotational_axes_to_check:
                    movement_req_rotational = [movement_req[ax] for ax in rotational_axes_to_check]
                    tol_rotation = max(min(movement_req_rotational), STAGE_TOL_ROTATION)  # radians
                    tol_rot_failure_per_axis = {ax: max(abs(target_pos[ax] - orig_pos[ax]) *
                                                STAGE_FRACTION_MOVE_FAILURE, STAGE_TOL_ROTATION_FAILURE)
                                                for ax in rotational_axes_to_check}

                axes_to_check = linear_axes_to_check | rotational_axes_to_check
                while axes_to_check:
                    if isNearPosition(current_pos=current_pos, target_position=target_pos,
                                      axes=axes_to_check, rot_axes=rotational_axes_to_check,
                                      atol_linear=tol_linear, atol_rotation=tol_rotation):
                        # Drop axes from the original position, which are not important because they
                        # haven't been requested to move.
                        orig_pos = {a: orig_pos[a] for a, nv in target_pos.items()
                                    if nv != orig_pos[a]}
                        logging.debug("Position has updated fully: from %s -> %s", orig_pos,
                                      current_pos)
                        break

                    if time.time() > expected_end_time:
                        logging.warning("Stage position after %s s is %s instead of target pos: %s. "
                                        "Giving up waiting.", wait_stage_move, current_pos, target_pos)

                        # Raise an error if the stage ended-up too far from the requested position
                        failures = {}
                        for axis in linear_axes_to_check:
                            delta = abs(current_pos[axis] - target_pos[axis])
                            if delta > tol_lin_failure_per_axis[axis]:
                                failures[axis] = (delta, tol_lin_failure_per_axis[axis])

                        for axis in rotational_axes_to_check:
                            delta = abs(current_pos[axis] - target_pos[axis])
                            if delta > tol_rot_failure_per_axis[axis]:
                                failures[axis] = (delta, tol_rot_failure_per_axis[axis])

                        if failures:
                            msgs = [f"{axis}: error={delta:.6f} > tol={tol:.6f}" for axis, (delta, tol) in failures.items()]
                            raise TimeoutError("Stage move failure. Off-target axes: " + ", ".join(msgs))

                        break

                    if future._must_stop.is_set():
                        raise CancelledError()

                    logging.debug("Waiting a little longer as position has not updated fully: %s != %s. "
                                  "The linear and rotational tolerances are %s m and %s rad respectively.",
                                  current_pos, target_pos, tol_linear, tol_rotation)
                    time.sleep(STAGE_WAIT_DURATION)
                    current_pos = self.parent.get_stage_position()

            except Exception:
                if future._must_stop.is_set():
                    raise CancelledError()
                raise
            finally:
                future._was_stopped = True
                # Update the position, even if the move didn't entirely succeed
                self._updatePosition()

    def _doMoveRel(self, future: futures.Future, shift: Dict[str, float]):
        """
        :param shift: position in internal coordinates (ie, axes in the same
           direction as the hardware expects)
        """
        # We don't check the target position fit the range, the xt-adapter will take care of that
        self._moveTo(future, shift, rel=True)

    @isasync
    def moveRel(self, shift: Dict[str, float]) -> None:
        """
        Shift the stage the given position in meters. This is non-blocking.
        Throws an error when the requested position is out of range.

        :param shift:
            Relative shift to move the stage to per axes in m for 'x', 'y', 'z' in rad for 'rx', 'rz'.
            Axes are 'x', 'y', 'z', 'rx' and 'rz'.
        """
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        shift = self._applyInversion(shift)

        f = self._createFuture()
        f = self._executor.submitf(f, self._doMoveRel, f, shift)
        return f

    def _doMoveAbs(self, future: futures.Future, pos: dict) -> None:
        self._moveTo(future, pos, rel=False)

    @isasync
    def moveAbs(self, pos: Dict[str, float]) -> None:
        """
        Move the stage the given position in meters. This is non-blocking.
        Throws an error when the requested position is out of range.

        :param pos:
            Absolute position to move the stage to per axes in m for 'x', 'y', 'z' in rad for 'rx', 'rz'.
            Axes are 'x', 'y', 'z', 'rx' and 'rz'.
        """
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversion(pos)

        f = self._createFuture()
        f = self._executor.submitf(f, self._doMoveAbs, f, pos)
        return f

    def stop(self, axes: Optional[Set[str]] = None) -> None:
        """Stop the movement of the stage."""
        self._executor.cancel()
        self.parent.stop_stage_movement()
        try:
            self._updatePosition()
        except Exception:
            logging.exception("Unexpected failure when updating position")

    def _createFuture(self) -> CancellableFuture:
        """
        :return: a future that can be used to manage a move
        """
        f = CancellableFuture()
        f._moving_lock = threading.Lock()  # taken while moving
        f._must_stop = threading.Event()  # cancel of the current future requested
        f._was_stopped = False  # if cancel was successful
        f.task_canceller = self._cancelCurrentMove
        return f

    def _cancelCurrentMove(self, future: futures.Future) -> bool:
        """
        Cancels the current move (both absolute or relative). Non-blocking.

        :param future: the future to stop. Unused, only one future must be
         running at a time.
        :return: True if it successfully cancelled (stopped) the move.
        """
        # The difficulty is to synchronise correctly when:
        #  * the task is just starting (not finished requesting axes to move)
        #  * the task is finishing (about to say that it finished successfully)
        logging.debug("Cancelling current move")
        future._must_stop.set()  # tell the thread taking care of the move it's over
        self.parent.stop_stage_movement()

        with future._moving_lock:
            if not future._was_stopped:
                logging.debug("Cancelling failed")
            return future._was_stopped


class Focus(model.Actuator):
    """
    This is an extension of the model.Actuator class. It provides functions for
    moving the SEM focus (as it's considered an axis in Odemis)
    """

    def __init__(self, name: str, role: str, parent: model.HwComponent, **kwargs) -> None:
        """
        :param axes: names of the axes
        """

        fwd_info = parent.fwd_info()
        axes_def = {
            "z": model.Axis(unit=fwd_info["unit"], range=fwd_info["range"]),
        }

        model.Actuator.__init__(self, name, role, parent=parent, axes=axes_def, **kwargs)

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        # RO, as to modify it the server must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute({}, unit="m", readonly=True)
        self._updatePosition()

        if not hasattr(self.parent, "_scanner"):
            raise ValueError("Required scanner child was not provided."
                             "An ebeam or multi-beam scanner is a required child component for the Focus class")

        # Refresh regularly the position
        self._pos_poll = util.RepeatingTimer(5, self._refreshPosition, "Focus position polling")
        self._pos_poll.start()

    def terminate(self):
        if self._pos_poll:
            self._pos_poll.cancel()
            self._pos_poll = None
        super().terminate()

    @isasync
    def applyAutofocus(self, detector: model.Detector) -> futures.Future:
        """
        Wrapper for running the autofocus functionality asynchronously. It sets the state of autofocus,
        the beam must be turned on and unblanked. Also, a reasonable manual focus is needed. When the image is too far
        out of focus, an incorrect focus can be found using the autofocus functionality.
        This call is non-blocking.

        :param detector:
            Detector component that stores information about which channel to use for autofocusing.
        :return: Future object
        """
        # Create ProgressiveFuture and update its state
        est_start = time.time() + 0.1
        f = ProgressiveFuture(start=est_start,
                              end=est_start + 11)  # rough time estimation
        f._autofocus_lock = threading.Lock()
        f._must_stop = threading.Event()  # cancel of the current future requested
        f.task_canceller = self._cancelAutoFocus
        f._channel_name = detector._scanner.channel
        return self._executor.submitf(f, self._applyAutofocus, f)

    def _applyAutofocus(self, future: futures.Future) -> None:
        """
        Starts autofocussing and checks if the autofocussing process is finished for ProgressiveFuture.

        :param future: the future to start running.
        """
        channel_name = future._channel_name
        with future._autofocus_lock:
            if future._must_stop.is_set():
                raise CancelledError()
            self.parent.set_autofocusing(channel_name, XT_RUN)
            time.sleep(0.5)  # Wait for the autofocussing to start

        # Wait until the microscope is no longer autofocussing
        while self.parent.is_autofocusing(channel_name):
            future._must_stop.wait(0.1)
            if future._must_stop.is_set():
                raise CancelledError()

    def _cancelAutoFocus(self, future: futures.Future) -> bool:
        """
        Cancels the autofocussing. Non-blocking.

        :param future: the future to stop.
        :return: True if it successfully cancelled (stopped) the move.
        """
        future._must_stop.set()  # tell the thread taking care of autofocussing it's over

        with future._autofocus_lock:
            logging.debug("Cancelling autofocussing")
            try:
                self.parent.set_autofocusing(future._channel_name, XT_STOP)
                return True
            except OSError as error_msg:
                logging.warning("Failed to cancel autofocus: %s", error_msg)
                return False

    def _updatePosition(self) -> None:
        """
        update the position VA
        """
        z = self.parent.get_free_working_distance()
        self.position._set_value({"z": z}, force_write=True)

    def _refreshPosition(self) -> None:
        """
        Called regularly to update the current position
        """
        # We don't use the VA setters, to avoid sending back to the hardware a
        # set request
        logging.debug("Updating SEM focus position")
        try:
            self._updatePosition()
        except Exception:
            logging.exception("Unexpected failure when updating position")

    def _doMoveRel(self, foc: float) -> None:
        """
        move by foc

        :param foc: relative change in m
        """
        try:
            foc += self.parent.get_free_working_distance()
            self.parent.set_free_working_distance(foc)
        finally:
            # Update the position, even if the move didn't entirely succeed
            self._updatePosition()

    def _doMoveAbs(self, foc: float) -> None:
        """
        move to pos

        :param foc: unit m
        """
        try:
            self.parent.set_free_working_distance(foc)
        finally:
            # Update the position, even if the move didn't entirely succeed
            self._updatePosition()

    @isasync
    def moveRel(self, shift: Dict[str, float]) -> futures.Future:
        """
        :param shift: shift in m
        """
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)

        foc = shift["z"]
        f = self._executor.submit(self._doMoveRel, foc)
        return f

    @isasync
    def moveAbs(self, pos: Dict[str, float]) -> futures.Future:
        """
        :param pos: pos in m
        """
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)

        foc = pos["z"]
        f = self._executor.submit(self._doMoveAbs, foc)
        return f

    def stop(self, axes: Optional[Set[str]] = None) -> None:
        """
        Stop the last command
        """
        # Empty the queue (and already stop the stage if a future is running)
        self._executor.cancel()
        logging.debug("Cancelled all ebeam focus moves")

        try:
            self._updatePosition()
        except Exception:
            logging.exception("Unexpected failure when updating position")


class MultiBeamScanner(Scanner):
    """
    This class extends behaviour of the xt_client.Scanner class with XTtoolkit functionality.

    xt_client.Scanner contains Vigilant Attributes for magnification, accel voltage, blanking, spotsize, beam shift,
    rotation and dwell time. This class adds XTtoolkit functionality via the Vigilant Attributes for the delta pitch,
    beam stigmator, pattern stigmator, the beam shift transformation matrix (read-only),
    multiprobe rotation (read-only), beamlet index, beam mode (multi/single beam)
    Whenever one of these attributes is changed, its setter also updates another value if needed.
    """

    def __init__(self, name: str, role: str, parent: model.HwComponent,
                 hfw_nomag: float, channel: str = "electron1", **kwargs) -> None:
        self.channel = channel  # Name of the electron channel used.

        # First instantiate the xt_toolkit VA's then call the __init__ of the super for the update thread.
        self.parent = parent

        # Add XTtoolkit specific VA's
        delta_pitch_info = self.parent.delta_pitch_info()
        assert delta_pitch_info["unit"] == "um", "Delta pitch unit is incorrect, current: {}, should be: um.".format(
            delta_pitch_info["unit"])
        self.deltaPitch = model.FloatContinuous(
            self.parent.get_delta_pitch() * 1e-6,
            unit="m",
            range=tuple(i * 1e-6 for i in delta_pitch_info["range"]),
            setter=self._setDeltaPitch,
        )

        beam_stigmator_info = self.parent.stigmator_info()
        beam_stigmator_range_x = beam_stigmator_info["range"]["x"]
        beam_stigmator_range_y = beam_stigmator_info["range"]["y"]
        beam_stigmator_range = tuple((i, j) for i, j in zip(beam_stigmator_range_x, beam_stigmator_range_y))
        self.beamStigmator = model.TupleContinuous(
            tuple(self.parent.get_stigmator()),
            unit=beam_stigmator_info["unit"],
            range=beam_stigmator_range,
            setter=self._setBeamStigmator)

        pattern_stigmator_info = self.parent.pattern_stigmator_info()
        pattern_stigmator_range_x = pattern_stigmator_info["range"]["x"]
        pattern_stigmator_range_y = pattern_stigmator_info["range"]["y"]
        pattern_stigmator_range = tuple((i, j) for i, j in zip(pattern_stigmator_range_x,
                                                               pattern_stigmator_range_y))
        self.patternStigmator = model.TupleContinuous(
            tuple(self.parent.get_pattern_stigmator()),
            unit=pattern_stigmator_info["unit"],
            range=pattern_stigmator_range,
            setter=self._setPatternStigmator)

        self.beamShiftTransformationMatrix = model.ListVA(
            self.parent.get_dc_coils(),
            unit=None,
            readonly=True)

        self.multiprobeRotation = model.FloatVA(
            math.radians(self.parent.get_mpp_orientation()),
            unit="rad",
            readonly=True)

        beamlet_index_info = self.parent.beamlet_index_info()
        beamlet_index_range_x = beamlet_index_info["range"]["x"]
        beamlet_index_range_y = beamlet_index_info["range"]["y"]
        beamlet_index_range = tuple((int(i), int(j)) for i, j in zip(beamlet_index_range_x, beamlet_index_range_y))
        beamlet_index = self.parent.get_beamlet_index()
        self.beamletIndex = model.TupleContinuous(
            tuple(int(i) for i in beamlet_index),  # convert tuple values to integers.,
            unit=None,
            range=beamlet_index_range,
            setter=self._setBeamletIndex
        )

        # The compound lens focusing mode accepts value between 0 and 10. However,
        # in practice, we use it to switch between immersion mode or not.
        # So instead of passing a float, we just provide a boolean, with the immersion
        # mode always set to the same (hard-coded, TFS approved) value.
        # When reading, anything above 0 is considered in immersion.
        focusing_mode_info = self.parent.compound_lens_focusing_mode_info()
        focusing_mode_range = focusing_mode_info["range"]
        assert focusing_mode_range[0] <= COMPOUND_LENS_FOCUS_IMMERSION <= focusing_mode_range[1]
        focusing_mode = self.parent.get_compound_lens_focusing_mode()
        self.immersion = model.BooleanVA(
            focusing_mode > 0,
            setter=self._setImmersion
        )

        multibeam_mode = (self.parent.get_use_case() == 'MultiBeamTile')
        self.multiBeamMode = model.BooleanVA(
            multibeam_mode,
            setter=self._setMultiBeamMode
        )

        self.power = model.BooleanVA(
            self.parent.get_beam_is_on(),
            setter=self._setBeamPower
        )

        # TODO If _updateSettings is updated move this to the top of the __init__ function. For now the update thread
        #  can only be started after the MB VA's are initialized.
        # Instantiate the super scanner class with the update thread
        super(MultiBeamScanner, self).__init__(name, role, parent, hfw_nomag, **kwargs)

    @isasync
    def applyAutoStigmator(self) -> None:
        """
        Wrapper for autostigmation function, non-blocking.
        """
        est_start = time.time() + 0.1
        f = ProgressiveFuture(start=est_start,
                              end=est_start + 90)  # Rough time estimation
        adapter_version = re.search(r"xtadapter:\s*([\d.]+)", self.parent._swVersion)
        adapter_version = adapter_version.group(1)
        # The new autostig is only supported in xtadapter v1.11.5 and higher
        if pkg_resources.parse_version(adapter_version) < pkg_resources.parse_version("1.11.5"):
            logging.debug(f"Xtadapter version is {adapter_version}, therefore using an old autostigmation version.")
            f = self._executor.submitf(f, self.parent.start_autostigmating_flash)
        else:
            f = self._executor.submitf(f, self.parent.start_autostig)
        return f

    def _updateSettings(self) -> None:
        """
        Read all the current settings from the SEM and reflects them on the VAs
        """
        # TODO When the new approach of adding update function to a list is implemented in xt_client.py instead of
        #  overwriting the method updateSettings the method _updateMBSettings can be added to the list of
        #  functions to be updated. That means the overwritten part in this class is no longer needed.

        # Polling XT client settings
        super(MultiBeamScanner, self)._updateSettings()
        # Polling XTtoolkit settings
        try:
            self._updateHFWRange()
            delta_pitch = self.parent.get_delta_pitch() * 1e-6
            if delta_pitch != self.deltaPitch.value:
                self.deltaPitch._value = delta_pitch
                self.deltaPitch.notify(delta_pitch)
            beam_stigmator = self.parent.get_stigmator()
            if beam_stigmator != self.beamStigmator.value:
                self.beamStigmator._value = beam_stigmator
                self.beamStigmator.notify(beam_stigmator)
            pattern_stigmator = self.parent.get_pattern_stigmator()
            if pattern_stigmator != self.patternStigmator.value:
                self.patternStigmator._value = pattern_stigmator
                self.patternStigmator.notify(pattern_stigmator)
            beam_shift_transformation_matrix = self.parent.get_dc_coils()
            if beam_shift_transformation_matrix != self.beamShiftTransformationMatrix.value:
                self.beamShiftTransformationMatrix._value = beam_shift_transformation_matrix
                self.beamShiftTransformationMatrix.notify(beam_shift_transformation_matrix)
            mpp_rotation = math.radians(self.parent.get_mpp_orientation())
            if mpp_rotation != self.multiprobeRotation.value:
                self.multiprobeRotation._value = mpp_rotation
                self.multiprobeRotation.notify(mpp_rotation)
            beamlet_index = tuple(int(i) for i in self.parent.get_beamlet_index())
            if beamlet_index != self.beamletIndex.value:
                self.beamletIndex._value = beamlet_index
                self.beamletIndex.notify(beamlet_index)
            immersion = self.parent.get_compound_lens_focusing_mode() > 0
            if immersion != self.immersion.value:
                self.immersion._value = immersion
                self.immersion.notify(immersion)
            multibeam_mode = (self.parent.get_use_case() == 'MultiBeamTile')
            if multibeam_mode != self.multiBeamMode.value:
                self.multiBeamMode._value = multibeam_mode
                self.multiBeamMode.notify(multibeam_mode)
            power = self.parent.get_beam_is_on()
            if power != self.power.value:
                self.power._value = power
                self.power.notify(power)
        except Exception:
            logging.exception("Unexpected failure when polling XTtoolkit settings")

    def _setDeltaPitch(self, delta_pitch: float) -> float:
        self.parent.set_delta_pitch(delta_pitch * 1e6)  # Convert from meters to micrometers.
        return self.parent.get_delta_pitch() * 1e-6

    def _setBeamStigmator(self, beam_stigmator_value: Tuple[float, float]) -> Tuple[float, float]:
        self.parent.set_stigmator(*beam_stigmator_value)
        return self.parent.get_stigmator()

    def _setPatternStigmator(self, pattern_stigmator_value: Tuple[float, float]) -> Tuple[float, float]:
        self.parent.set_pattern_stigmator(*pattern_stigmator_value)
        return self.parent.get_pattern_stigmator()

    def _setBeamletIndex(self, beamlet_index: Tuple[int, int]) -> Tuple[int, int]:
        self.parent.set_beamlet_index(beamlet_index)
        new_beamlet_index = self.parent.get_beamlet_index()
        return tuple(int(i) for i in new_beamlet_index)  # convert tuple values to integers.

    def _setBeamPower(self, power_value: bool) -> bool:
        self.parent.set_beam_power(power_value)
        return self.parent.get_beam_is_on()

    def _setImmersion(self, immersion: bool) -> bool:
        # immersion disabled -> focusing mode = 0
        # immersion enabled -> focusing mode = COMPOUND_LENS_FOCUS_IMMERSION
        self.parent.set_compound_lens_focusing_mode(COMPOUND_LENS_FOCUS_IMMERSION if immersion else 0)
        # The immersion mode affects the HFW maximum
        # Note: if the HFW is set to a value which is out of range in the new settings,
        # the XT server takes care of adjusting the HFW to a value within range.
        self._updateHFWRange()
        return self.parent.get_compound_lens_focusing_mode() > 0

    def _updateHFWRange(self) -> None:
        """
        To be called when the field of view range might have changed.
        This can happen when some settings are changed.
        If the range is changed, the VA subscribers will be updated.
        """
        hfov_range = tuple(self.parent.scanning_size_info()["range"]["x"])
        if self.horizontalFoV.range != hfov_range:
            logging.debug("horizontalFoV range changed to %s", hfov_range)

            fov = self.parent.get_scanning_size()[0]
            self.horizontalFoV._value = fov
            self.horizontalFoV.range = hfov_range  # Does the notification

            self.magnification._value = self._hfw_nomag / fov
            mag_range_max = self._hfw_nomag / hfov_range[0]
            mag_range_min = self._hfw_nomag / hfov_range[1]
            self.magnification.range = (mag_range_min, mag_range_max)

    def _setMultiBeamMode(self, multi_beam_mode: bool) -> bool:
        if multi_beam_mode:
            self.parent.set_use_case('MultiBeamTile')
        else:
            self.parent.set_use_case('SingleBeamlet')

        return (self.parent.get_use_case() == 'MultiBeamTile')


class XTTKDetector(Detector):
    """
    This is an extension of xt_client.Detector class. It overwrites the image acquisition
    to work with image acquisition in XTToolkit.
    """

    def __init__(self, name: str, role: str, parent: model.HwComponent, address: str, **kwargs):
        # The acquisition is based on a FSM that roughly looks like this:
        # Event\State |    Stopped    |   Acquiring    | Receiving data |
        #    START    | Ready for acq |        .       |       .        |
        #    DATA     |       .       | Receiving data |       .        |
        #    STOP     |       .       |     Stopped    |    Stopped     |
        #    TERM     |     Final     |      Final     |     Final      |
        self._cancel_access = threading.Lock()
        try:
            # A second connection to the xtadapter is needed to properly cancel a scan.
            # The scan_image call is blocking, therefore we cannot cancel a scan
            # using the same thread and connection the scan_image call is made.
            self.cancel_connection = Pyro5.api.Proxy(address)
            self.cancel_connection._pyroTimeout = 30  # seconds
        except Exception as err:
            raise HwError("Failed to connect to XT server '%s'. Check that the "
                          "uri is correct and XT server is"
                          " connected to the network. %s" % (address, err))
        Detector.__init__(self, name, role, parent=parent, **kwargs)
        self._shape = (2 ** 16,)  # Depth of the image

    def stop_generate(self) -> None:
        logging.debug("Stopping image acquisition")
        self._genmsg.put(GEN_STOP)
        with self._cancel_access:
            self.cancel_connection._pyroClaimOwnership()
            # Directly calling set_channel_state does not work with XTToolkit.
            # Therefore, call get_channel_state, before trying to stop the channel.
            # WARNING: This code does not properly cancel the current acquisition.
            self.cancel_connection.get_channel_state(self.parent._scanner.channel)
            self.cancel_connection.set_channel_state(self.parent._scanner.channel, False)
            # Stop twice, to make sure the channel fully stops.
            self.cancel_connection.set_channel_state(self.parent._scanner.channel, False)
        if self.parent._scanner.blanker.value is None:
            self.parent.blank_beam()
        logging.debug("Stopped generate image acquisition")

    def _acquire(self) -> None:
        """
        Acquisition thread
        Managed via the ._genmsg Queue
        """
        try:
            while True:
                # Wait until we have a start (or terminate) message
                self._acq_wait_start()
                logging.debug("Preparing acquisition")
                while True:
                    logging.debug("Start acquiring an image")

                    # HACK: as cancellation doesn't work, to avoid acquiring an extra image
                    # at the end of an acquisition, we wait a bit before starting the next frame.
                    # This gives enough time for the client after receiving the previous frame to
                    # decide that this is enough. Especially worthy when acquiring a single image.
                    if self._acq_should_stop(timeout=0.2):
                        logging.debug("Image acquisition should stop, exiting loop")
                        break

                    if hasattr(self._scanner, "blanker"):
                        if self._scanner.blanker.value is None:
                            self.parent.unblank_beam()

                    md = self.parent._scanner._metadata.copy()

                    logging.debug("Start a complete scan of an image")
                    md[model.MD_ACQ_DATE] = time.time()
                    try:
                        self.parent.scan_image()
                    except OSError as err:
                        if err.errno == -2147467260:  # -2147467260 corresponds to: Operation aborted.
                            logging.debug("Scan image aborted.")
                            # Operation aborted, indicating acquisition should stop.
                            break
                        else:
                            raise

                    # Add metadata to the image
                    md.update(self._metadata)
                    md[model.MD_DWELL_TIME] = self.parent._scanner.dwellTime.value
                    md[model.MD_ROTATION] = self.parent._scanner.rotation.value

                    # Acquire the image
                    image = self.parent.get_latest_image(self.parent._scanner.channel)

                    da = DataArray(image, md)
                    logging.debug("Notify dataflow with new image of shape: %s.", image.shape)
                    self.data.notify(da)
            logging.debug("Acquisition stopped")
        except TerminationRequested:
            logging.debug("Acquisition thread requested to terminate")
        except Exception as err:
            logging.exception("Failure in acquisition thread: %s", err)
        finally:
            self._generator = None


class XTTKFocus(Focus):
    """
    This is an extension of xt_client.Focus class. It overwrites the autofocus function to use the one
    provided by the flash script.
    """

    @isasync
    def applyAutofocus(self, detector: model.Detector) -> futures.Future:
        """
        Wrapper for autofocus flash function, non-blocking.

        :param detector:
            The detector is unused for XTTK focusing, because it is not possible to select a channel for flash focusing.
        :return: Future object
        """
        est_start = time.time() + 0.1
        f = ProgressiveFuture(start=est_start,
                              end=est_start + 20)  # Rough time estimation
        f = self._executor.submitf(f, self.parent.start_autofocusing_flash)
        return f
