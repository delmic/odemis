# -*- coding: utf-8 -*-
"""
Created on 20 Feb 2024

Copyright © 2024 Delmic

This file is part of Odemis.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your
option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see
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
from concurrent.futures import CancelledError
from typing import Optional, Union

import msgpack_numpy
import notify2
import numpy
import Pyro5.api
import pkg_resources
from Pyro5.errors import CommunicationError

from odemis import model
from odemis import util
from odemis.model import (CancellableFuture, CancellableThreadPoolExecutor,
                          DataArray, HwError, ProgressiveFuture,
                          StringEnumerated, isasync)

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
                else os.path.isdir(entity_path) and len([entity for entity in os.listdir(entity_path) if entity.endswith(".exe")]) == 1
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


class FIBSEM(model.HwComponent):
    """
    Driver to communicate with autoscript software on TFS microscopes. autoscript is the software TFS uses to control their microscopes.
    To use this driver the autoscript adapter developed by Delmic should be running on the TFS PC. Communication to the
    Microscope server is done via Pyro5.

    By adding the using 'mb-scanner' child instead of a 'scanner' child this driver extends the class with the addition
    of the XTtoolkit functionality. XTtoolkit provides extra functionality for the FAST-EM project which xtlib does not
    provide, it is a development library by TFS. To use this driver the XT adapter developed by Delmic should be
    running on the TFS PC. In the user configuration file `delmic-xt-config.ini` on the Microscope PC, xt_type must
    be set to "xttoolkit".
    """

    def __init__(self, name, role, children, address, daemon=None,
                 **kwargs):
        """
        Parameters
        ----------
        address: str
            server address and port of the Microscope server, e.g. "PYRO:Microscope@localhost:4242"
        timeout: float
            Time in seconds the client should wait for a response from the server.
        """

        # print versions of msgpack, msgpack-numpy, pyro
        print("msgpack version: %s", pkg_resources.get_distribution("msgpack").version)
        print("msgpack-numpy version: %s", pkg_resources.get_distribution("msgpack-numpy").version)
        print("Pyro5 version: %s", pkg_resources.get_distribution("Pyro5").version)
        print("Pyro4 version: %s", pkg_resources.get_distribution("Pyro4").version)

        model.HwComponent.__init__(self, name, role, daemon=daemon, **kwargs)
        self._proxy_access = threading.Lock()
        try:
            self.server = Pyro5.api.Proxy(address)
            self.server._pyroTimeout = 30  # seconds
            self._swVersion = self.server.get_software_version()
            self._hwVersion = self.server.get_hardware_version()
            logging.debug(
                f"Successfully connected to autoscript server with software version {self._swVersion} and hardware"
                f"version {self._hwVersion}")
        except CommunicationError as err:
            import msgpack
            info = f"""PYRO 5: {pkg_resources.get_distribution('Pyro5').version}, 
            msgpack-numpy: {pkg_resources.get_distribution('msgpack-numpy').version}, 
            msgpack_numpy_file: {msgpack_numpy.__file__},
            msgpack version: {pkg_resources.get_distribution('msgpack').version}
            msgpack_file: {msgpack.__file__}

            This is likely a dependencies issue:
            make sure you have the following matching versions of msgpack and msgpack-numpy installed:
            msgpack==0.5.6, msgpack-numpy==0.4.4
            or
            msgpack==1.0.3 msgpack-numpy==0.4.8
            """
            raise HwError("Failed to connect to autoscript server '%s'. Check that the "
                          "uri is correct and autoscript server is"
                          " connected to the network. %s %s" % (address, err, info))
        except OSError as err:
            raise HwError("XT server reported error: %s." % (err,))

        # Transfer latest xtadapter package if available
        # The transferred package will be a zip file in the form of bytes
        # self.check_and_transfer_latest_package()

        # Create the scanner type child(ren)
        # Check if at least one of the required scanner types is instantiated
        # scanner_types = ["scanner", "fib-scanner", "mb-scanner"]  # All allowed scanners types
        # if not any(scanner_type in children for scanner_type in scanner_types):
        #     raise KeyError("SEM was not given any scanner as child. "
        #                    "One of 'scanner', 'fib-scanner' or 'mb-scanner' need to be included as child")

        has_detector = "detector" in children
        has_fib_detector = "fib-detector" in children

        if "scanner" in children: # TODO: rename to sem-scanner
            kwargs = children["scanner"]
            self._scanner = Scanner(parent=self, daemon=daemon, channel="electron", has_detector=has_detector, **kwargs)
            self.children.value.add(self._scanner)

        if "fib-scanner" in children:
            kwargs = children["fib-scanner"]
            self._fib_scanner = Scanner(parent=self, daemon=daemon, channel="ion", has_detector=has_fib_detector, **kwargs)
            self.children.value.add(self._fib_scanner)

        # create the stage child, if requested
        if "stage" in children:
            ckwargs = children["stage"]
            self._stage = Stage(parent=self, daemon=daemon, **ckwargs)
            self.children.value.add(self._stage)

        # # create the chamber child, if requested
        # if "chamber" in children:
        #     ckwargs = children["chamber"]
        #     self._chamber = Chamber(parent=self, daemon=daemon, **ckwargs)
        #     self.children.value.add(self._chamber)

        # # create a focuser, if requested
        # if "focus" in children:
        #     ckwargs = children["focus"]
        #     self._focus = Focus(parent=self, daemon=daemon, **ckwargs)
        #     self.children.value.add(self._focus)

        if "detector" in children:
            ckwargs = children["detector"]
            self._detector = Detector(parent=self, daemon=daemon, channel="electron", **ckwargs)
            self.children.value.add(self._detector)
        
        if "fib-detector" in children:
            ckwargs = children["fib-detector"]
            self._fib_detector = Detector(parent=self, daemon=daemon, channel="ion", **ckwargs)
            self.children.value.add(self._fib_detector)

    def transfer_latest_package(self, data: bytes) -> None:
        """
        Transfer the latest xtadapter package.

        Note:
            Pyro has a 1 gigabyte message size limitation.
            https://pyro5.readthedocs.io/en/latest/tipstricks.html#binary-data-transfer-file-transfer

        :param data: The package's zip file data in bytes.

        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.transfer_latest_package(data)

    def check_and_transfer_latest_package(self) -> None:
        """Check if a latest xtadapter package is available and then transfer it."""
        try:
            package = None
            bitness = re.search(r"bitness:\s*([\da-z]+)", self._swVersion)
            bitness = bitness.group(1) if bitness is not None else None
            adapter = "xtadapter"
            if "xttoolkit" in self._swVersion:
                adapter = "fastem-xtadapter"
            current_version = re.search(r"xtadapter:\s*([\d.]+)", self._swVersion)
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
                    self.transfer_latest_package(data)
                    # Notify the user that a newer xtadpater version is available
                    notify2.init("Odemis")
                    update = notify2.Notification(
                        "Update Delmic XT Adapter", "Newer version {} is available on ThermoFisher Support PC.\n\n"
                        "How to update?\n\n1. Full stop Odemis and close Delmic XT Adapter.\n2. Restart the Delmic XT Adapter "
                        "to install it.".format(package.version))
                    update.set_urgency(notify2.URGENCY_NORMAL)
                    update.set_timeout(10000)    # 10 seconds
                    update.show()
                else:
                    logging.warning("{} is a bad file in {} not transferring latest package.".format(ret, package.path))
        except Exception as err:
            logging.exception(err)

    def list_available_channels(self) -> list:
        """List all available channels
        :return: (list) of available channels.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.list_available_channels()

    def move_stage_absolute(self, position: dict) -> None:
        """ Move the stage the given position. This is blocking.
        :param position: dict, absolute position to move the stage to per axes. 
            Axes are 'x', 'y', 'z', 't', 'r'.
            The unit is meters for axes 'x', 'y' and 'z', and radians for axes 't', 'r'.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.move_stage_absolute(position)

    def move_stage_relative(self, position: dict) -> None:
        """
        Move the stage by the given relative position. This is blocking.
        :param position: dict, relative position to move the stage to per axes in m. 
            Axes are 'x', 'y', 'z', 'r', 't'. The units are meters for x, y, z and radians for r, t.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.move_stage_relative(position)

    def stop_stage_movement(self):
        """Stop the movement of the stage."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.stop_stage_movement()

    def get_stage_position(self) -> dict:
        """
        :return: (dict) the axes of the stage as keys with their corresponding position.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_stage_position()

    def stage_info(self):
        """Returns: (dict) the unit and range of the stage position."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.stage_info()

    def set_default_stage_coordinate_system(self, coordinate_system: str) -> None:
        """
        Set the default stage coordinate system.
        :param coordinate_system: (str) Name of the coordinate system to set as default.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_default_stage_coordinate_system(coordinate_system)

    def home_stage(self):
        """Home stage asynchronously. This is non-blocking."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.home_stage()

    def is_homed(self):
        """Returns: (bool) True if the stage is homed and False otherwise."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.is_homed()
    
    def link(self, state: bool) -> bool:
        """
        Link Z-axis to free working distance. When linked, the stage z-axis 
        follows the free working distance. This is blocking.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            is_linked = self.server.link(state)
            return is_linked
         
    def is_linked(self):
        """
        Returns: (bool) True if Z follows free working distance.
        When Z follows FWD and Z-axis of stage moves, FWD is updated to keep image in focus.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.is_linked()

#### CHAMBER CONTROL

    def pump(self):
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

    def vent(self):
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

    def get_chamber_state(self):
        """Returns: (str) the vacuum state of the microscope chamber to see if it is pumped or vented,
        possible states: "vacuum", "vented", "prevac", "pumping", "venting","vacuum_error" """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_chamber_state()


    def get_pressure(self):
        """Returns: (float) the chamber pressure in pascal, or -1 in case the system is vented."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            pressure = self.server.get_pressure()
            return pressure

    def pressure_info(self):
        """Returns (dict): the unit and range of the pressure."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.pressure_info()

##### BEAM CONTROL

    def set_scan_mode(self, mode: str, channel: str, value: Union[float, dict] = None) -> None:
        """
        Set the scan mode.
        :param mode: (str) Name of desired scan mode, one of: unknown, external, full_frame, spot, or line.
        :param channel: (str) Name of the channel to set the scan mode for.
        :param value: (float or dict) Value of the scan mode.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_scan_mode(mode=mode, channel=channel, value=value)

    def get_scan_mode(self, channel: str):
        """
        Get the scan mode.
        :param channel: (str) Name of the channel to get the scan mode for.
        :return: (str) Name of set scan mode, one of: unknown, external, full_frame, spot, or line.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_scan_mode(channel)

    def scan_mode_info(self, channel: str) -> list:
        """Returns: (dict) the unit and range of the scan mode."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.scan_mode_info(channel)
        
    def set_reduced_area(self, reduced_area: dict, channel: str) -> None:
        """
        Specify a selected area in the scan field area.
        :param reduced_area: (dict) the reduced area (left, top, width, height) as % of image (0 - 1).
        :param channel: (str) Name of the channel to set the reduced area for. 
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_selected_area(reduced_area, channel)

    def reset_reduced_area(self, channel: str) -> None:
        """Reset the selected area to select the entire image."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.reset_reduced_area()

    def set_spotsize(self, spotsize: float, channel: str) -> None:
        """
        Setting the spot size of the selected beam.
        :param spotsize: (float) the spot size of the beam in meters.
        :param channel: (str) Name of the channel to set the spot size for.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_spotsize(spotsize, channel)

    def get_spotsize(self, channel: str) -> float:
        """Returns: (float) the current spotsize of the selected beam (unitless)."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_spotsize(channel)

    def spotsize_info(self, channel: str) -> dict:
        """Returns: (dict) the unit and range of the spotsize. Unit is None means the spotsize is unitless."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.spotsize_info(channel)

    def set_dwell_time(self, dwell_time: float, channel: str) -> None:
        """
        :param dwell_time: (float) the dwell time in seconds.
        :param channel: (str) Name of the channel to set the dwell time for.

        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_dwell_time(dwell_time, channel)

    def get_dwell_time(self, channel: str) -> float:
        """Returns: (float) the dwell time in seconds."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_dwell_time(channel)

    def dwell_time_info(self, channel: str) -> dict:
        """Returns: (dict) range of the dwell time and corresponding unit."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.dwell_time_info(channel)
        
    def set_field_of_view(self, value: float, channel: str) -> None:
        """
        Set the field of view. 
        :param value: (float) the field of view in meters.
        :param channel: (str) Name of the channel to set the field of view for.
        :return: None
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_field_of_view(value, channel)

    def get_field_of_view(self, channel: str) -> float:
        """
        :param channel: (str) Name of the channel to get the field of view for.
        :return: (float) the field of view in meters.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_field_of_view(channel)

    def field_of_view_info(self, channel: str) -> dict:
        """Returns: (dict) the scanning size unit and range."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.field_of_view_info(channel)

    def set_high_voltage(self, voltage: float, channel: str) -> None:
        """
        Set the high voltage.
        :param voltage: (float) the high voltage in volt.
        :param channel: (str) Name of the channel to set the high voltage for.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_high_voltage(voltage, channel)

    def get_high_voltage(self, channel: str) -> float:
        """:return (float) the high voltage in volt."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_high_voltage(channel)

    def high_voltage_info(self, channel: str) -> dict:
        """:return (dict) the unit and range of the high voltage."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.high_voltage_info(channel)

    def set_beam_current(self, current: float, channel:str) -> None:
        """
        Set the beam current.
        :param current: (float) the beam current in ampere.
        :param channel: (str) Name of the channel to set the beam current for.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_beam_current(current, channel)

    def get_beam_current(self, channel: str) -> float:
        """Returns: (float) the beam current in ampere."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_beam_current(channel)
        
    def beam_current_info(self, channel: str) -> dict:
        """Returns: (dict) the unit and range of the beam current."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.beam_current_info(channel)

    def blank_beam(self, channel: str) -> None:
        """Blank the selected beam."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.blank_beam(channel)

    def unblank_beam(self, channel: str) -> None:
        """Unblank the selected beam."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.unblank_beam(channel)

    def beam_is_blanked(self, channel: str) -> bool:
        """return: (bool) True if the beam is blanked and False if the beam is not blanked."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.beam_is_blanked(channel)

    def beam_is_installed(self, channel: str) -> bool:
        """return: (bool) True if the beam is installed and False if the beam is not installed."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.beam_is_installed(channel)
        
    def get_working_distance(self, channel: str) -> float:
        """Returns: (float) the working distance in meters.
        :param channel: (str) Name of the channel to get the free working distance for.
        :return: (float) the working distance in meters.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_working_distance(channel)

    def set_working_distance(self, working_distance: float, channel: str) -> None:
        """
        Set the working distance.
        :param working_distance: (float) the free working distance in meters.
        :param channel: (str) Name of the channel to set the free working distance for.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_working_distance(working_distance, channel)

    def working_distance_info(self, channel: str) -> dict:
        """Returns the unit and range of the working distance.
        :param channel: (str) Name of the channel to get the free working distance for.
        :return: (dict) the unit and range of the working distance."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.working_distance_info(channel)

    def get_beam_shift(self, channel: str) -> tuple:
        """Returns: (float) the current beam shift (DC coils position) x and y values in meters."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return tuple(self.server.get_beam_shift(channel))

    def set_beam_shift(self, x_shift, y_shift, channel: str) -> None:
        """Set the current beam shift (DC coils position) values in meters."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_beam_shift(x_shift, y_shift, channel)

    def move_beam_shift(self, x_shift: float, y_shift: float, channel: str) -> None:
        """Move the beam shift values in meters.
        :param x_shift: (float) the x value of the beam shift in meters.
        :param y_shift: (float) the y value of the beam shift in meters.
        :param channel: (str) Name of the channel to move the beam shift for.
        :return None"""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.move_beam_shift(x_shift, y_shift, channel)

    def beam_shift_info(self, channel: str) -> dict:
        """Returns: (dict) the unit and xy-range of the beam shift (DC coils position)."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.beam_shift_info(channel)

    def get_stigmator(self, channel: str) -> tuple:
        """
        Retrieves the current stigmator x and y values. 
        This stigmator corrects for the astigmatism of the probe shape.
        :param channel: str, name of the channel
        :return: tuple, (x, y) current stigmator values, unitless
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return tuple(self.server.get_stigmator(channel))

    def set_stigmator(self, x: float , y: float, channel: str) -> None:
        """
        Set the current stigmator x and y values.
        This stigmator corrects for the astigmatism of the probe shape.
        :param x: float, the x value of the stigmator, unitless
        :param y: float, the y value of the stigmator, unitless
        :param channel: str, name of the channel
        :return None
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_stigmator(x, y, channel)

    def stigmator_info(self, channel: str):
        """
        Returns the unit and range of the stigmator. This stigmator corrects for the astigmatism of the probe shape.

        Returns
        -------
        dict, keys: "unit", "range"
        'unit': returns physical unit of the stigmator, typically None.
        'range': returns dict with keys 'x' and 'y' -> returns range of axis (tuple of length 2).
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.stigmator_info(channel)

    def get_scan_rotation(self, channel: str) -> float:
        """Returns: (float) the current rotation value in rad."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_scan_rotation(channel)

    def set_scan_rotation(self, rotation: float, channel: str):
        """Set the current rotation value in rad."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_scan_rotation(rotation, channel)

    def scan_rotation_info(self, channel: str) -> dict:
        """Returns: (dict) the unit and range of the rotation."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.scan_rotation_info(channel)

    def set_resolution(self, resolution: list, channel: str) -> None:
        """
        Set the resolution of the image.
        :param resolution: (tuple) The resolution of the image in pixels as (width, height). 
            Options: (768, 512), (1536, 1024), (3072, 2048), (6144, 4096). 
            Technically can be anything, but not all modes are supported for non-standard res
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_resolution(resolution, channel)

    def get_resolution(self, channel: str) -> tuple:
        """Returns the resolution of the image as (width, height)."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return tuple(self.server.get_resolution(channel))

    def resolution_info(self, channel: str) -> dict:
        """Returns the unit and range of the resolution of the image."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.resolution_info(channel)

    def turn_beam_on(self, state: bool, channel: str) -> None:
        """
        Turn the beam on or off.
        :param state: (bool) True to turn the beam on and False to turn the beam off.
        :param channel: (str) Name of the channel to turn the beam on or off for.
        :return None
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.turn_beam_on(state, channel)

    def beam_is_on(self, channel: str):
        """Returns True if the beam is on and False if the beam is off."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.beam_is_on(channel)

#### CHANNEL CONTROL

#### DETECTOR CONTROL
    
    def set_detector_mode(self, mode: str, channel: str) -> None:
        """
        Set the mode of the detector.
        :param mode: (str) Name of the mode to set the detector to.
        :param channel: (str) Name of one of the channels.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_detector_mode(mode, channel)
        
    def get_detector_mode(self, channel: str) -> str:
        """
        Get the mode of the detector.
        :param channel: (str) Name of one of the channels.
        :return: (str) Name of the mode the detector is set to.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_detector_mode(channel)
        
    def detector_mode_info(self, channel: str) -> dict:
        """Returns the mode of the detector."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.detector_mode_info(channel)
        
    def set_detector_type(self, detector_type: str, channel: str) -> None:
        """
        Set the type of the detector.
        :param detector_type: (str) Name of the type to set the detector to.
        :param channel: (str) Name of one of the channels.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_detector_type(detector_type, channel)

    def get_detector_type(self, channel: str) -> str:
        """
        Get the type of the detector.
        :param channel: (str) Name of one of the channels.
        :return: (str) Name of the type the detector is set to.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_detector_type(channel)
        
    def detector_type_info(self, channel: str) -> dict:
        """Returns the type of the detector."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.detector_type_info(channel)

    def set_contrast(self, contrast: float, channel: str):
        """
        Set the contrast of the scanned image to a specified factor.
        :param contrast: (float) Value the contrast should be set to as a factor between 0 and 1.
        :param channel: (str) Name of one of the channels.

        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.set_contrast(contrast, channel)

    def get_contrast(self, channel: str) -> float:
        """
        Get the contrast of the scanned image.  
        :param channel: (str) Name of one of the channels.
        :return: (float) Returns value of current contrast as a factor between 0 and 1.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_contrast(channel)

    def contrast_info(self, channel: str) -> dict:
        """Returns the contrast unit [-] and range [0, 1]."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.contrast_info(channel)

    def set_brightness(self, brightness: float, channel: str):
        """
        Set the brightness of the scanned image to a specified factor.
        :param brightness: (float) Value the brightness should be set to as a factor between 0 and 1.
        :param channel: (str) Name of one of the channels.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.set_brightness(brightness, channel)

    def get_brightness(self, channel: str) -> float:
        """
        Get the brightness of the scanned image.
        :param channel_name: (str) Name of one of the channels.
        :return: (float) Returns value of current brightness as a factor between 0 and 1.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_brightness(channel)

    def brightness_info(self, channel: str) -> dict:
        """Returns the brightness unit [-] and range [0, 1]."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.brightness_info(channel)

    def get_detector_state(self, channel: str) -> str:
        """
        Get the state of the detector.
        :param channel: (str) Name of one of the channels.
        :return: (str) Name of the state the detector is set to.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_detector_state(channel)



#### IMAGING CONTROL

    def get_active_view(self) -> int:
        """Returns: (int) the active view."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_active_view()
        

    def get_active_device(self) -> int:
        """Returns: (int) the active device."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_active_device()
        
    def set_active_view(self, view: int) -> None:
        """Set the active view."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_active_view(view)

    def set_active_device(self, device: int) -> None:
        """Set the active device."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_active_device(device)

    def acquire_image(self, channel: str ) -> numpy.ndarray:
        """
        Acquire an image from the detector.
        :param channel: (str) Name of one of the channels.
        :return: (numpy.ndarray) the acquired image.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.acquire_image(channel)
    
    def get_last_image(self, channel: str, wait_for_frame: bool = True) -> numpy.ndarray:
        """
        Get the last acquired image from the detector.
        :param channel: (str) Name of one of the channels.
        :return: (numpy.ndarray) the last acquired image.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_last_image(channel, wait_for_frame)

    def start_acquisition(self, channel:str) -> None:
        """
        Start the acquisition of images.
        :param channel: (str) Name of one of the channels.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.start_acquisition(channel)


    def stop_acquisition(self, channel: str, wait_for_frame: bool = True) -> None:
        """
        Stop the acquisition of images.
        :param channel: (str) Name of one of the channels.
        :param wait_for_frame: (bool) If True, the function will wait until the current frame is acquired.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.stop_acquisition(channel, wait_for_frame)

    def get_imaging_state(self, channel:str) -> str:
        """
        Get the state of the imaging.
        :param channel: (str) Name of one of the channels.
        :return: (str) Name of the state the imaging is set to.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_imaging_state(channel)

    def set_scanning_filter(self, channel:str, filter_type: int, n_frames: int  = 1) -> None:
        """
        Set the scanning filter for the detector.
        :param channel: (str) Name of one of the channels.
        :param filter_type: (int) Type of the filter to set [1: None, 2: Averaging, 3: Integrating]
        :param n_frames: (int) Number of frames to average over.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_scanning_filter(channel, filter_type, n_frames)

    def get_scanning_filter(self, channel: str) -> dict:
        """
        Get the scanning filter for the detector.
        :param channel: (str) Name of one of the channels.
        :return: (dict) the scanning filter type and number of frames.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_scanning_filter(channel)
    
    def get_scanning_filter_info(self, channel:str ) -> dict:
        """
        Get the scanning filter info for the detector.
        :param channel: (str) Name of one of the channels.
        :return: (dict) the scanning filter type and number of frames.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_scanning_filter_info(channel)

#### AUTO FUNCTIONS

    def run_auto_contrast_brightness(self, channel: str) -> None:
        """Run auto contrast brightness function
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.run_auto_contrast_brightness(channel, parameters={})


#### MILLING CONTROL

    def create_rectangle(self, pattern_dict: dict) -> dict:
        """
        Create a rectangle milling pattern.
        :param pattern_dict: (dict) Dictionary containing the pattern parameters.
        :return: (dict) Dictionary containing the pattern parameters.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.create_rectangle(pattern_dict)

    def create_cleaning_cross_section(self, pattern_dict: dict) -> dict:
        """
        Create a cleaning cross section milling pattern.
        :param pattern_dict: (dict) Dictionary containing the pattern parameters.
        :return: (dict) Dictionary containing the pattern parameters.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.create_cleaning_cross_section(pattern_dict)
        
    def create_regular_cross_section(self, pattern_dict: dict) -> dict:
        """
        Create a regular cross section milling pattern.
        :param pattern_dict: (dict) Dictionary containing the pattern parameters.
        :return: (dict) Dictionary containing the pattern parameters.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.create_regular_cross_section(pattern_dict)

    def create_line(self, pattern_dict: dict) -> dict:
        """
        Create a line milling pattern.
        :param pattern_dict: (dict) Dictionary containing the pattern parameters.
        :return: (dict) Dictionary containing the pattern parameters.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.create_line(pattern_dict)

    def create_circle(self, pattern_dict: dict) -> dict:
        """
        Create a circle milling pattern.
        :param pattern_dict: (dict) Dictionary containing the pattern parameters.
        :return: (dict) Dictionary containing the pattern parameters.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.create_circle(pattern_dict)
    
    def start_milling(self) -> None:
        """Start the milling. This is non-blocking"""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.start_milling()

    def run_milling(self) -> None:
        """Run the milling. This is blocking."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.run_milling()

    def pause_milling(self) -> None:
        """Pause the milling. This can be resumed."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.pause_milling()

    def stop_milling(self) -> None:
        """Stop the milling. This cannot be resumed, must be re-started."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.stop_milling()

    def resume_milling(self) -> None:
        """Resume the milling."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.resume_milling()

    def get_patterning_state(self) -> str:
        """Returns: (str) the state of the patterning."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_patterning_state()
        
    def get_patterning_mode(self) -> str:
        """Returns: (str) the mode of the patterning."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_patterning_mode()
        
    def set_patterning_mode(self, mode: str) -> None:
        """Set the mode of the patterning. (Serial, Parallel)"""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_patterning_mode(mode)

    def clear_patterns(self) -> None:
        """Clear all patterns."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.clear_patterns()

    def set_default_application_file(self, application_file: str = "Si") -> None:
        """
        Set the default application file.
        :param application_file: (str) Name of the default application file.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_default_application_file(application_file)

    def set_default_patterning_beam_type(self, channel: str) -> None:
        """
        Set the default patterning beam type.
        :param channel: (str) Name of one of the channels.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_default_patterning_beam_type(channel)

    def get_available_application_files(self) -> list:
        """Returns: (list) the available application files."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_available_application_files()

    def estimate_milling_time(self) -> float:
        """Returns: (float) the estimated milling time for drawn patterns."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.estimate_milling_time()

class Scanner(model.Emitter):
    """
    This is an extension of the model.Emitter class. It contains Vigilant
    Attributes for magnification, accel voltage, blanking, spotsize, beam shift,
    rotation and dwell time. Whenever one of these attributes is changed, its
    setter also updates another value if needed.
    """

    def __init__(self, name, role, parent:FIBSEM, hfw_nomag, channel, has_detector=False, **kwargs):
        """
        channel (str): name of the electron channel
        has_detector (bool): True if a Detector is also controlled. In this case,
          the .resolution, .scale and associated VAs will be provided too.
        """
        model.Emitter.__init__(self, name, role, parent=parent, **kwargs)

        self.channel = channel  # name of the channel used

        # will take care of executing auto contrast/brightness and auto stigmator asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        self._hfw_nomag = hfw_nomag
        self._has_detector = has_detector

        dwell_time_info = self.parent.dwell_time_info(self.channel)
        self.dwellTime = model.FloatContinuous(
            self.parent.get_dwell_time(self.channel),
            dwell_time_info["range"],
            unit=dwell_time_info["unit"],
            setter=self._setDwellTime)
        # when the range has changed, clip the current dwell time value to the new range
        self.dwellTime.clip_on_range = True

        voltage_info = self.parent.high_voltage_info(self.channel)
        init_voltage = numpy.clip(self.parent.get_high_voltage(self.channel), 
                            voltage_info['range'][0], voltage_info['range'][1])
        self.accelVoltage = model.FloatContinuous(
            init_voltage,
            voltage_info["range"],
            unit=voltage_info["unit"],
            setter=self._setVoltage
        )

        beam_current_info = self.parent.beam_current_info(self.channel)
        self.beamCurrent = model.FloatContinuous(
            value=self.parent.get_beam_current(self.channel),
            range=beam_current_info["range"],
            unit=beam_current_info["unit"],
            setter=self._setCurrent
        )

        # blanker_choices = {True: 'blanked', False: 'unblanked'}
        # if has_detector:
        #     blanker_choices[None] = 'auto'

        # self.blanker = model.VAEnumerated(
        #     None if has_detector else self.parent.beam_is_blanked(self.channel),
        #     setter=self._setBlanker,
        #     choices=blanker_choices)

        # if self.channel == "electron":
            # spotsize_info = self.parent.spotsize_info(self.channel)
            # self.spotSize = model.FloatContinuous(
            #     self.parent.get_spotsize(self.channel),
            #     spotsize_info["range"],
            #     unit=spotsize_info["unit"],
            #     setter=self._setSpotSize)

        beam_shift_info = self.parent.beam_shift_info(self.channel)
        range_x = beam_shift_info["range"]["x"]
        range_y = beam_shift_info["range"]["y"]
        self.shift = model.TupleContinuous(
            self.parent.get_beam_shift(self.channel),
            ((range_x[0], range_y[0]), (range_x[1], range_y[1])),
            cls=(int, float),
            unit=beam_shift_info["unit"],
            setter=self._setBeamShift)

        stigmator_info = self.parent.stigmator_info(self.channel)
        range_x = stigmator_info["range"]["x"]
        range_y = stigmator_info["range"]["y"]
        self.stigmator = model.TupleContinuous(
            self.parent.get_stigmator(self.channel),
            ((range_x[0], range_y[0]), (range_x[1], range_y[1])),
            cls=(float, float),
            unit=stigmator_info["unit"],
            setter=self._setStigmator)


        rotation_info = self.parent.scan_rotation_info(self.channel)
        self.rotation = model.FloatContinuous(
            self.parent.get_scan_rotation(self.channel),
            rotation_info["range"],
            unit=rotation_info["unit"],
            setter=self._setRotation)

        fov_info = self.parent.field_of_view_info(self.channel)
        fov = self.parent.get_field_of_view(self.channel)
        self.horizontalFoV = model.FloatContinuous(
            fov,
            unit=fov_info["unit"],
            range=fov_info["range"],
            setter=self._setHorizontalFoV)
        self.horizontalFoV.subscribe(self._onHorizontalFoV)

        mag = self._hfw_nomag / fov
        mag_range_max = self._hfw_nomag / fov_info["range"][0]
        mag_range_min = self._hfw_nomag / fov_info["range"][1]
        self.magnification = model.FloatContinuous(mag, unit="",
                                                   range=(mag_range_min, mag_range_max),
                                                   readonly=True)
        # To provide some rough idea of the step size when changing focus
        # Depends on the pixelSize, so will be updated whenever the HFW changes
        self.depthOfField = model.FloatContinuous(1e-6, range=(0, 1e3),
                                                  unit="m", readonly=True)
        self._updateDepthOfField()

        if has_detector:
            rng = ((768, 512), (6144, 4096))
            self._shape = (rng[1][0], rng[1][1])
            # pixelSize is the same as MD_PIXEL_SIZE, with scale == 1
            # == smallest size/ between two different ebeam positions
            pxs = (fov / self._shape[0],
                   fov / self._shape[0])
            self.pixelSize = model.VigilantAttribute(pxs, unit="m", readonly=True)

            # .resolution is the number of pixels actually scanned. It's almost
            # fixed to full frame, with the exceptions of the resolutions which
            # are a different aspect ratio from the shape are "more than full frame".
            # So it's read-only and updated when the scale is updated.
            resolution = self.parent.get_resolution(self.channel)
            res_choices = set(r for r in RESOLUTIONS)
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

        # emode = self._isExternal()
        # self.external = model.BooleanVA(emode, setter=self._setExternal)

        # Refresh regularly the values, from the hardware, starting from now
        self._updateSettings()
        self._va_poll = util.RepeatingTimer(5, self._updateSettings, "Settings polling")
        self._va_poll.start()

    def _updateSettings(self):
        """
        Read all the current settings from the SEM and reflects them on the VAs
        """
        logging.debug("Updating SEM settings")
        try:
            # external = self._isExternal()
            # if external != self.external.value:
            #     self.external._value = external
            #     self.external.notify(external)
            # Read dwellTime and resolution settings from the SEM and reflects them on the VAs only
            # when external is False i.e. the scan mode is 'full_frame'.
            # If external is True i.e. the scan mode is 'external' the dwellTime and resolution are
            # disabled and hence no need to reflect settings on the VAs.
            # if not self.external.value:
            dwell_time = self.parent.get_dwell_time(self.channel)
            if dwell_time != self.dwellTime.value:
                self.dwellTime._value = dwell_time
                self.dwellTime.notify(dwell_time)
            if self._has_detector:
                self._updateResolution()
            voltage = self.parent.get_high_voltage(self.channel)
            v_range = self.accelVoltage.range
            if not v_range[0] <= voltage <= v_range[1]:
                logging.info("Voltage {} V is outside of range {}, clipping to nearest value.".format(voltage, v_range))
                voltage = self.accelVoltage.clip(voltage)
            if voltage != self.accelVoltage.value:
                self.accelVoltage._value = voltage
                self.accelVoltage.notify(voltage)
            # blanked = self.parent.beam_is_blanked(self.channel)  # blanker status on the HW
            # if blanker is in auto mode (None), don't care about HW status (self-regulated)
            # if self.blanker.value is not None and blanked != self.blanker.value:
            #     self.blanker._value = blanked
            #     self.blanker.notify(blanked)
            # spot_size = self.parent.get_spotsize(self.channel)
            # if spot_size != self.spotSize.value:
            #     self.spotSize._value = spot_size
            #     self.spotSize.notify(spot_size)
            beam_shift = self.parent.get_beam_shift(self.channel)
            if beam_shift != self.shift.value:
                self.shift._value = beam_shift
                self.shift.notify(beam_shift)
            rotation = self.parent.get_scan_rotation(self.channel)
            if rotation != self.rotation.value:
                self.rotation._value = rotation
                self.rotation.notify(rotation)
            fov = self.parent.get_field_of_view(self.channel)
            if fov != self.horizontalFoV.value:
                self.horizontalFoV._value = fov
                mag = self._hfw_nomag / fov
                self.magnification._value = mag
                self.horizontalFoV.notify(fov)
                self.magnification.notify(mag)
        except Exception:
            logging.exception("Unexpected failure when polling settings")

    def _setScale(self, value):
        """
        value (1 < float, 1 < float): increase of size between pixels compared to
            the original pixel size. It will adapt the resolution to
            have the same ROI (just different amount of pixels scanned)
        return the actual value used
        """
        # Pick the resolution which matches the scale in X
        res_x = int(round(self._shape[0] / value[0]))
        res = next(r for r in self.resolution.choices if r[0] == res_x)

        # TODO: instead of setting both X and Y, only set X, and read back Y?
        # This would be slightly more flexible in case the XT lib supports other
        # resolutions than the hard-coded ones. For now we assume the hard-coded
        # ones are all the possibles ones.
        self.parent.set_resolution(res, channel=self.channel)
        self.resolution._set_value(res, force_write=True)

        return value

    def _onScale(self, s):
        self._updatePixelSize()

    def _updateResolution(self):
        """
        To be called to read the server resolution and update the corresponding VAs
        """
        resolution = tuple(self.parent.get_resolution(self.channel))
        if resolution != self.resolution.value:
            scale = (self._shape[0] / resolution[0],) * 2
            self.scale._value = scale  # To not call the setter
            self.resolution._set_value(resolution, force_write=True)
            self.scale.notify(scale)

    def _updatePixelSize(self):
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

    def _setDwellTime(self, dwell_time):
        self.parent.set_dwell_time(dwell_time, channel=self.channel)
        # Cannot set the dwell_time on the parent if the scan mode is 'external'
        # hence return the requested value itself
        # if self._isExternal():
            # return dwell_time
        return self.parent.get_dwell_time(self.channel)

    def _setVoltage(self, voltage: float) -> float:
        self.parent.set_high_voltage(voltage, channel=self.channel)
        return self.parent.get_high_voltage(self.channel)   # return the actual value used

    def _setCurrent(self, current: float) -> float:
        self.parent.set_beam_current(current, channel=self.channel)
        return self.parent.get_beam_current(self.channel)  # return the actual value used
    
    def _setBlanker(self, blank: bool) -> bool:
        """
        Parameters
        ----------
        blank (bool or None): True if the the electron beam should blank, False if it should be unblanked,
            None if it should be blanked/unblanked automatically. Only useful when using the Detector or the
            XTTKDetector component. Not useful when operating the SEM in external mode.

        Returns
        -------
        (bool or None): True if the the electron beam is blanked, False if it is unblanked. See Notes for edge case,
            None if it should be blanked/unblanked automatically.

        """
        if blank is None:
            # TODO Blanker should explicitly be set based on whether we are scanning or not.
            return None

        if blank:
            self.parent.blank_beam(self.channel)
        else:
            self.parent.unblank_beam(self.channel)
        return self.parent.beam_is_blanked(self.channel)

    # def _setSpotSize(self, spotsize):
    #     self.parent.set_spotsize(spotsize, channel=self.channel)
    #     return self.parent.get_spotsize(self.channel)

    def _setBeamShift(self, beam_shift):
        self.parent.set_beam_shift(x=beam_shift[0], y=beam_shift[1], channel=self.channel)
        return self.parent.get_beam_shift(self.channel)

    def _setStigmator(self, stigmator):
        self.parent.set_stigmator(x=stigmator[0], y=stigmator[1], channel=self.channel)
        return self.parent.get_stigmator(self.channel)

    def _setRotation(self, rotation):
        self.parent.set_scan_rotation(rotation, channel=self.channel)
        return self.parent.get_scan_rotation(self.channel)

    def _setHorizontalFoV(self, fov):
        self.parent.set_field_of_view(fov, channel=self.channel)
        fov = self.parent.get_field_of_view(self.channel)
        mag = self._hfw_nomag / fov
        self.magnification._value = mag
        self.magnification.notify(mag)
        return fov

    def _onHorizontalFoV(self, fov):
        self._updateDepthOfField()
        # the dwell time range is dependent on the magnification/horizontalFoV
        self._updateDwellTimeRng()
        if self._has_detector:
            self._updatePixelSize()

    def _updateDepthOfField(self):
        fov = self.horizontalFoV.value
        # Formula was determined by experimentation
        K = 100  # Magical constant that gives a not too bad depth of field
        dof = K * (fov / 1024)
        self.depthOfField._set_value(dof, force_write=True)

    def _updateDwellTimeRng(self):
        """The dwell time range is dependent on the magnification/horizontalFoV, the range whenever the fov updates."""
        self.dwellTime._set_range(self.parent.dwell_time_info(self.channel)["range"])

    # def _isExternal(self):
    #     """
    #     :return:
    #     bool, True if the scan mode is 'external', False if the scan mode is different than 'external'.
    #     """
    #     return self.parent.get_scan_mode(self.channel).lower() == "external"

    # def _setExternal(self, external):
    #     """
    #     Switching between internal and external control of the SEM.
    #     :param external: (bool) True is external, False is full frame mode.
    #     :return: (bool) True if the scan mode should be 'external'.
    #                     False if the scan mode should be internally controlled by the SEM.
    #     """
    #     scan_mode = "external" if external else "full_frame"
    #     self.parent.set_scan_mode(scan_mode, channel=self.channel)
    #     # The dwellTime and scale VA setter can only reflect changes on the SEM server side (parent)
    #     # after the external VA is set to False i.e. 'full_frame'
    #     if not external:
    #         if self.dwellTime.value != self.parent.get_dwell_time(self.channel):
    #             # Set the VA value again to reflect changes on the parent
    #             self.dwellTime.value = self.dwellTime.value
    #         if self.resolution.value != tuple(self.parent.get_resolution(self.channel)):
    #             # Set the VA value again to reflect changes on the parent
    #             self.scale.value = self.scale.value
    #     return external

    def prepareForScan(self):
        """
        Make sure the beam is unblanked when the blanker is in 'auto' mode before starting to scan.
        """
        if self.blanker.value is None:
            self.parent.unblank_beam(self.channel)

    def finishScan(self):
        """
        Make sure the beam is blanked when the blanker is in 'auto' mode at the end of scanning.
        """
        if self.blanker.value is None:
            self.parent.blank_beam(self.channel)

# TODO: add support for patterning


class Detector(model.Detector):
    """
    This is an extension of model.Detector class. It performs the main functionality
    of the SEM. It sets up a Dataflow and notifies it every time that an SEM image
    is captured.
    """

    def __init__(self, name, role, parent, channel: str, **kwargs):
        # The acquisition is based on a FSM that roughly looks like this:
        # Event\State |    Stopped    |   Acquiring    | Receiving data |
        #    START    | Ready for acq |        .       |       .        |
        #    DATA     |       .       | Receiving data |       .        |
        #    STOP     |       .       |     Stopped    |    Stopped     |
        #    TERM     |     Final     |      Final     |     Final      |

        model.Detector.__init__(self, name, role, parent=parent, **kwargs)
        self._shape = (256,)  # Depth of the image
        self.data = SEMDataFlow(self)
        self.channel = channel
        
        if self.channel == "electron":
            self._scanner = self.parent._scanner
        elif self.channel == "ion":
            self._scanner = self.parent._fib_scanner
        else:
            raise ValueError("Invalid channel name. No scanner available for this channel.")

        brightness_info = self.parent.brightness_info(self.channel)
        self.brightness = model.FloatContinuous(
            self.parent.get_brightness(self.channel),
            brightness_info["range"],
            unit=brightness_info["unit"],
            setter=self._setBrightness)

        contrast_info = self.parent.contrast_info(self.channel)
        self.contrast = model.FloatContinuous(
            self.parent.get_contrast(self.channel),
            contrast_info["range"],
            unit=contrast_info["unit"],
            setter=self._setContrast)
        
        detector_type = self.parent.get_detector_type(self.channel)
        self.detector_type = model.StringEnumerated(
            detector_type,
            choices=set(self.parent.detector_type_info(self.channel)["choices"]),
            setter=self._setDetectorType)
        
        detector_mode = self.parent.get_detector_mode(self.channel)
        self.detector_mode = model.StringEnumerated(
            detector_mode,
            choices=set(self.parent.detector_mode_info(self.channel)["choices"]),
            setter=self._setDetectorMode)
        
        self._genmsg = queue.Queue()  # GEN_*
        self._generator = None

        # Refresh regularly the values, from the hardware, starting from now
        self._updateSettings()
        self._va_poll = util.RepeatingTimer(5, self._updateSettings, "Settings polling detector")
        self._va_poll.start()

    def terminate(self):
        if self._generator:
            self.stop_generate()
            self._genmsg.put(GEN_TERM)
            self._generator.join(5)
            self._generator = None

    def start_generate(self):
        self._genmsg.put(GEN_START)
        if not self._generator or not self._generator.is_alive():
            logging.info("Starting acquisition thread")
            self._generator = threading.Thread(target=self._acquire,
                                               name="autoscript acquisition thread")
            self._generator.start()

    def stop_generate(self):
        self.stop_acquisition()
        self._genmsg.put(GEN_STOP)

    def _acquire(self):
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
                    # self._scanner.prepareForScan()
                    # self.parent.set_channel_state(self._scanner.channel, True)
                    # The channel needs to be stopped to acquire an image, therefore immediately stop the channel.
                    # self.parent.set_channel_state(self._scanner.channel, False)

                    md = self._scanner._metadata.copy()
                    if hasattr(self._scanner, "dwellTime"):
                        md[model.MD_BEAM_DWELL_TIME] = self._scanner.dwellTime.value
                    if hasattr(self._scanner, "rotation"):
                        md[model.MD_BEAM_SCAN_ROTATION] = self._scanner.rotation.value
                    # if hasattr(self._scanner, "spotSize"):
                    #     md[model.MD_BEAM_SPOT_DIAM] = self._scanner.spotSize.value
                    if hasattr(self._scanner, "accelVoltage"):
                        md[model.MD_BEAM_VOLTAGE] = self._scanner.accelVoltage.value
                    # if hasattr(self._scanner, "beamCurrent"):
                    #     md[model.MD_BEAM_CURRENT] = self._scanner.beamCurrent.value
                    # if hasattr(self._scanner, "shift"):
                    #     md[model.MD_BEAM_SHIFT] = self._scanner.shift.value
                    # if hasatter(self._scanner, "horizontalFoV"):
                    #     md[model.MD_BEAM_FIELD_OF_VIEW] = self._scanner.horizontalFoV.value
                    # if hasattr(self._scanner, "magnification"):
                    #     md[model.MD_LENS_MAG] = self._scanner.magnification.value
                        
                    # TODO: this causes timeout?
                    # stage_position = self.parent._stage.position.value
                    stage = model.getComponent(role="stage-bare")
                    stage_position = stage.position.value
                    md[model.MD_STAGE_POSITION_RAW] = stage_position
                    md[model.MD_POS] = (stage_position["x"], stage_position["y"])
                        
                    # Estimated time for an acquisition is the dwell time times the total amount of pixels in the image.
                    if hasattr(self._scanner, "dwellTime") and hasattr(self._scanner, "resolution"):
                        n_pixels = self._scanner.resolution.value[0] * self._scanner.resolution.value[1]
                        est_acq_time = self._scanner.dwellTime.value * n_pixels
                    else:
                        # Acquisition time is unknown => assume it will be long
                        est_acq_time = 5 * 60  # 5 minutes

                    # Wait for the acquisition to be received
                    # logging.debug("Starting one image acquisition")
                    # try:
                    #     if self._acq_wait_data(est_acq_time + 20):
                    #         logging.debug("Stopping measurement early")
                    #         self.stop_acquisition()
                    #         break
                    # except TimeoutError as err:
                    #     logging.error(err)
                    #     self.stop_acquisition()
                    #     break

                    # Retrieve the image (scans image, blocks until the image is received)
                    image = self.parent.acquire_image(self._scanner.channel)
                    md.update(self._metadata)
                    da = DataArray(image, md)
                    logging.debug("Notify dataflow with new image.")
                    self.data.notify(da)
                    break  # TODO: make this work properly, only acquires single images at the momenet
            logging.debug("Acquisition stopped")
        except TerminationRequested:
            logging.debug("Acquisition thread requested to terminate")
        except Exception as err:
            logging.exception("Failure in acquisition thread: {}".format(err))
        finally:
            self._generator = None

    def start_acquisition(self):
        """Start acquiring images"""

        if self.parent.get_imaging_state(self._scanner.channel) == "Running":
            logging.info(f"Imaging state is already running for channel {self._scanner.channel}")
            return
        
        self.parent.start_acquisition(self._scanner.channel)

    def stop_acquisition(self):
        """Stop acquiring images"""

        if self.parent.get_imaging_state(self._scanner.channel) == "Idle":
            logging.info(f"Imaging state is already stopped for channel {self._scanner.channel}")
            return
        
        self.parent.stop_acquisition(self._scanner.channel)


    def _acq_should_stop(self, timeout=None):
        """
        Indicate whether the acquisition should now stop or can keep running.
        Non blocking.
        Note: it expects that the acquisition is running.

        return (bool): True if needs to stop, False if can continue
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

    def _acq_wait_data(self, timeout=0):
        """
        Block until data or a stop message is received.
        Note: it expects that the acquisition is running.

        timeout (0<=float): how long to wait to check (use 0 to not wait)
        return (bool): True if needs to stop, False if data is ready
        raise TerminationRequested: if a terminate message was received
        """
        tend = time.time() + timeout
        t = time.time()
        logging.debug("Waiting for %g s:", tend - t)
        while self.parent.get_imaging_state(self._scanner.channel) != "Idle":
            t = time.time()
            if t > tend:
                raise TimeoutError("Acquisition timeout after %g s" % timeout)

            if self._acq_should_stop(timeout=0.1):
                return True

        return False  # Data received

    def _acq_wait_start(self):
        """
        Blocks until the acquisition should start.
        Note: it expects that the acquisition is stopped.

        raise TerminationRequested: if a terminate message was received
        """
        while True:
            # return True # TODO: implement this properly
            msg = self._get_acq_msg(block=True)
            if msg == GEN_TERM:
                raise TerminationRequested()
            elif msg == GEN_START:
                return

            # Duplicate Stop
            logging.debug("Skipped message %s as acquisition is stopped", msg)

    def _get_acq_msg(self, **kwargs):
        """
        Read one message from the acquisition queue
        return (str): message
        raises queue.Empty: if no message on the queue
        """
        msg = self._genmsg.get(**kwargs)
        if msg in (GEN_START, GEN_STOP, GEN_TERM):
            logging.debug("Acq received message %s", msg)
        else:
            logging.warning("Acq received unexpected message %s", msg)
        return msg

    def _updateSettings(self):
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
        detector_type = self.parent.get_detector_type(self._scanner.channel)
        if detector_type != self.detector_type.value:
            self.detector_type._value = detector_type
            self.detector_type.notify(detector_type)
        detector_mode = self.parent.get_detector_mode(self._scanner.channel)
        if detector_mode != self.detector_mode.value:
            self.detector_mode._value = detector_mode
            self.detector_mode.notify(detector_mode)

    def _setBrightness(self, brightness):
        self.parent.set_brightness(brightness, self._scanner.channel)
        return self.parent.get_brightness(self._scanner.channel)

    def _setContrast(self, contrast):
        self.parent.set_contrast(contrast, self._scanner.channel)
        return self.parent.get_contrast(self._scanner.channel)

    def _setDetectorMode(self, mode: str) -> str:
        self.parent.set_detector_mode(mode, self._scanner.channel)
        return self.parent.get_detector_mode(self._scanner.channel)
    
    def _setDetectorType(self, detector_type: str) -> str:
        self.parent.set_detector_type(detector_type, self._scanner.channel)
        return self.parent.get_detector_type(self._scanner.channel)

    # TODO: add support for auto functions


# Very approximate values
PRESSURE_VENTED = 100e3  # Pa
PRESSURE_PUMPED = 10e-3  # Pa


class Chamber(model.Actuator):
    """
    Component representing the vacuum chamber. Changing the pressure is possible by moving the "vacuum" axis,
    which accepts two position values: 0 for vented, 1 for vacuum.
    """

    def __init__(self, name, role, parent, **kwargs):
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

    def stop(self, axes=None):
        self._executor.cancel()
        if self._executor._queue:
            logging.warning("Stopping the pumping/venting process is not supported.")

    @isasync
    def moveRel(self, shift):
        raise NotImplementedError("Relative movements are not implemented for vacuum control. Use moveAbs instead.")

    @isasync
    def moveAbs(self, pos):
        self._checkMoveAbs(pos)
        return self._executor.submit(self._changePressure, pos["vacuum"])

    def _changePressure(self, target):
        """
        target (0, 1): 0 for pumping to vacuum, 1 for venting the chamber
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

    def _refreshPressure(self):
        # Position (vacuum state)
        state = self.parent.get_chamber_state()
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

    def terminate(self):
        self._polling_thread.cancel()


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

    def __init__(self, detector):
        """
        detector (model.Detector): the detector that the dataflow corresponds to
        """
        model.DataFlow.__init__(self)
        self._detector = detector

    # start/stop_generate are _never_ called simultaneously (thread-safe)
    def start_generate(self):
        self._detector.start_generate()

    def stop_generate(self):
        self._detector.stop_generate()


class Stage(model.Actuator):
    """
    This is an extension of the model.Actuator class. It provides functions for
    moving the TFS stage and updating the position.
    """

    def __init__(self, name, role, parent, rng=None, **kwargs):
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
            rng["rx"] = stage_info["range"]["T"]
        if "rz" not in rng:
            rng["rz"] = stage_info["range"]["R"]

        axes_def = {
            "x": model.Axis(unit=stage_info["unit"]["x"], range=rng["x"]),
            "y": model.Axis(unit=stage_info["unit"]["y"], range=rng["y"]),
            "z": model.Axis(unit=stage_info["unit"]["z"], range=rng["z"]),
            "rx": model.Axis(unit=stage_info["unit"]["t"], range=rng["rx"]),
            "rz": model.Axis(unit=stage_info["unit"]["r"], range=rng["rz"]),
        } # TODO: make these axis and unit arguments consistent

        model.Actuator.__init__(self, name, role, parent=parent, axes=axes_def,
                                **kwargs)
        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        self.position = model.VigilantAttribute({}, unit=stage_info["unit"],
                                                readonly=True)
        self._updatePosition()

        # Refresh regularly the position
        self._pos_poll = util.RepeatingTimer(5, self._refreshPosition, "Stage position polling")
        self._pos_poll.start()

    def _updatePosition(self):
        """
        update the position VA
        """
        old_pos = self.position.value
        pos = self._getPosition()
        self.position._set_value(self._applyInversion(pos), force_write=True)
        if old_pos != self.position.value:
            logging.debug("Updated position to %s", self.position.value)

    def _refreshPosition(self):
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

    def _getPosition(self):
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

    def _moveTo(self, future, pos, rel=False, timeout=60):
        with future._moving_lock:
            try:
                if future._must_stop.is_set():
                    raise CancelledError()
                if rel:
                    logging.debug("Moving by shift {}".format(pos))
                else:
                    logging.debug("Moving to position {}".format(pos))

                if "rx" in pos.keys():
                    pos["t"] = pos.pop("rx")
                if "rz" in pos.keys():
                    pos["r"] = pos.pop("rz")
                # self.parent.move_stage(pos, rel=rel)
                if rel:
                    self.parent.move_stage_relative(pos)
                else:
                    self.parent.move_stage_absolute(pos)
                time.sleep(0.1)  # It takes a little while before the stage is being reported as moving

                
                # TODO: these movements are blocking, so we can't report progress with self._updatePosition()?
                # how to work around this?


                # Wait until the move is over.
                # Don't check for future._must_stop because anyway the stage will
                # stop moving, and so it's nice to wait until we know the stage is
                # not moving.
                # moving = True
                # tstart = time.time()
                # last_pos_update = 0
                # while moving:
                #     # Take the opportunity to update .position (every 100 ms)
                #     now = time.time()
                #     if now - last_pos_update > 0.1:
                #         self._updatePosition()
                #         last_pos_update = now

                #     if time.time() > tstart + timeout:
                #         self.parent.stop_stage_movement()
                #         logging.error("Timeout after submitting stage move. Aborting move.")
                #         break

                #     # Wait for a little while so that we do not keep using the CPU all the time.
                #     time.sleep(20e-3)
                #     moving = self.parent.stage_is_moving()
                #     if not moving:
                #         # Be a little careful, because sometimes, half-way through a move
                #         # the stage is reported not moving for a short while (usually,
                #         # when one axis finished moving, and another is about to start).
                #         self._updatePosition()
                #         logging.debug("Confirming the stage really stopped")

                #         time.sleep(20e-3)
                #         moving = self.parent.stage_is_moving()
                #         if moving:
                #             logging.warning("Stage reported stopped but moving again, will wait longer")
                # else:
                #     logging.debug("Stage move completed")

                # If it was cancelled, Abort() has stopped the stage before, and
                # we still have waited until the stage stopped moving. Now let
                # know the user that the move is not complete.
                if future._must_stop.is_set():
                    raise CancelledError()
            except Exception:
                if future._must_stop.is_set():
                    raise CancelledError()
                raise
            finally:
                future._was_stopped = True
                # Update the position, even if the move didn't entirely succeed
                self._updatePosition()

    def _doMoveRel(self, future, shift):
        """
        shift (dict): position in internal coordinates (ie, axes in the same
           direction as the hardware expects)
        """
        # We don't check the target position fit the range, the xt-adapter will take care of that
        self._moveTo(future, shift, rel=True)

    @isasync
    def moveRel(self, shift):
        """
        Shift the stage the given position in meters. This is non-blocking.
        Throws an error when the requested position is out of range.

        Parameters
        ----------
        shift: dict(string->float)
            Relative shift to move the stage to per axes in m for 'x', 'y', 'z' in rad for 'rx', 'rz'.
            Axes are 'x', 'y', 'z', 'rx' and 'rz'.
        """
        if "coordinate_system" in shift:
            shift.pop("coordinate_system") # TODO: better integrate with _checkMove, assume always RAW?
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        shift = self._applyInversion(shift)

        f = self._createFuture()
        f = self._executor.submitf(f, self._doMoveRel, f, shift)
        return f

    def _doMoveAbs(self, future, pos):
        self._moveTo(future, pos, rel=False)

    @isasync
    def moveAbs(self, pos):
        """
        Move the stage the given position in meters. This is non-blocking.
        Throws an error when the requested position is out of range.

        Parameters
        ----------
        pos: dict(string->float)
            Absolute position to move the stage to per axes in m for 'x', 'y', 'z' in rad for 'rx', 'rz'.
            Axes are 'x', 'y', 'z', 'rx' and 'rz'.
        """
        if "coordinate_system" in pos:
            pos.pop("coordinate_system") # TODO: better integrate with _checkMove, assume always RAW?
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversion(pos)

        f = self._createFuture()
        f = self._executor.submitf(f, self._doMoveAbs, f, pos)
        return f

    def stop(self, axes=None):
        """Stop the movement of the stage."""
        self._executor.cancel()
        self.parent.stop_stage_movement()
        try:
            self._updatePosition()
        except Exception:
            logging.exception("Unexpected failure when updating position")

    def _createFuture(self):
        """
        Return (CancellableFuture): a future that can be used to manage a move
        """
        f = CancellableFuture()
        f._moving_lock = threading.Lock()  # taken while moving
        f._must_stop = threading.Event()  # cancel of the current future requested
        f._was_stopped = False  # if cancel was successful
        f.task_canceller = self._cancelCurrentMove
        return f

    def _cancelCurrentMove(self, future):
        """
        Cancels the current move (both absolute or relative). Non-blocking.
        future (Future): the future to stop. Unused, only one future must be
         running at a time.
        return (bool): True if it successfully cancelled (stopped) the move.
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

    def __init__(self, name, role, parent, **kwargs):
        """
        axes (set of string): names of the axes
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

    @isasync
    def applyAutofocus(self, detector):
        """
        Wrapper for running the autofocus functionality asynchronously. It sets the state of autofocus,
        the beam must be turned on and unblanked. Also a a reasonable manual focus is needed. When the image is too far
        out of focus, an incorrect focus can be found using the autofocus functionality.
        This call is non-blocking.

        :param detector: (model.Detector)
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

    def _applyAutofocus(self, future):
        """
        Starts autofocussing and checks if the autofocussing process is finished for ProgressiveFuture.
        :param future (Future): the future to start running.
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

    def _cancelAutoFocus(self, future):
        """
        Cancels the autofocussing. Non-blocking.
        :param future (Future): the future to stop.
        :return (bool): True if it successfully cancelled (stopped) the move.
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

    def _updatePosition(self):
        """
        update the position VA
        """
        z = self.parent.get_working_distance()
        self.position._set_value({"z": z}, force_write=True)

    def _refreshPosition(self):
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

    def _doMoveRel(self, foc):
        """
        move by foc
        foc (float): relative change in m
        """
        try:
            foc += self.parent.get_working_distance()
            self.parent.set_working_distance(foc)
        finally:
            # Update the position, even if the move didn't entirely succeed
            self._updatePosition()

    def _doMoveAbs(self, foc):
        """
        move to pos
        foc (float): unit m
        """
        try:
            self.parent.set_free_working_distance(foc)
        finally:
            # Update the position, even if the move didn't entirely succeed
            self._updatePosition()

    @isasync
    def moveRel(self, shift):
        """
        shift (dict): shift in m
        """
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)

        foc = shift["z"]
        f = self._executor.submit(self._doMoveRel, foc)
        return f

    @isasync
    def moveAbs(self, pos):
        """
        pos (dict): pos in m
        """
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)

        foc = pos["z"]
        f = self._executor.submit(self._doMoveAbs, foc)
        return f

    def stop(self, axes=None):
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

