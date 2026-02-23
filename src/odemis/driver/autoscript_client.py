# -*- coding: utf-8 -*-
"""
Created on 20 Feb 2024

@author: Patrick Cleeve

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
import queue
import threading
import time
from concurrent.futures import CancelledError, Future
from typing import Any, Dict, List, Optional, Tuple, Union

import msgpack  # only used for debug information
import msgpack_numpy
import numpy
import pkg_resources
import Pyro5.api
from Pyro5.errors import CommunicationError
from scipy import ndimage

from odemis import model, util
from odemis.driver.xt_client import check_and_transfer_latest_package
from odemis.model import (
    CancellableFuture,
    CancellableThreadPoolExecutor,
    DataArray,
    HwError,
    isasync,
)

Pyro5.api.config.SERIALIZER = 'msgpack'
msgpack_numpy.patch()

# Acquisition control messages
GEN_START = "S"  # Start acquisition
GEN_STOP = "E"  # Don't acquire image anymore
GEN_TERM = "T"  # Stop the generator

# List of known supported resolutions.
# Although the API only provide the min/max of resolution in X/Y, not every value
# or combination works. Actually, setting X always changes Y to the corresponding
# value. Note that they are not all the same aspect ratio. "Legacy" resolutions
# are ~8/7 while the new ones are 3/2. The "legacy" resolutions end up with a
# larger vertical field of view.
# TODO: non-standard resolutions are available in autoscript, so we should eventually enable them
RESOLUTIONS = (
    (192, 128),
    (512, 442),
    (1024, 884),
    (2048, 1768),
    (4096, 3536),
    (768, 512),
    (1536, 1024),
    (3072, 2048),
    (6144, 4096),
)
DETECTOR_RNG = ((768, 512), (6144, 4096))

# imaging acquisition states
IMAGING_STATE_IDLE = "Idle"
IMAGING_STATE_RUNNING = "Running"
IMAGING_STATE_PAUSED = "Paused"
IMAGING_STATE_ERROR = "Error"

# milling states
MILLING_STATE_IDLE = "Idle"
MILLING_STATE_RUNNING = "Running"
MILLING_STATE_PAUSED = "Paused"
MILLING_STATE_ERROR = "Error"

# information on compatible versions
debug_connection_info = f"""PYRO 5: {pkg_resources.get_distribution('Pyro5').version},
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

class SEM(model.HwComponent):
    """
    Driver to communicate with autoscript software on TFS microscopes. autoscript is the software TFS uses to control their microscopes.
    To use this driver the autoscript adapter developed by Delmic should be running on the TFS PC. Communication to the
    Microscope server is done via Pyro5. The component is a parent to the scanner, stage, focus, and detector components, and supports both SEM and FIB.
    """

    def __init__(self, name, role, children, address, port: str = '4243', daemon=None,
                 **kwargs):
        """
        :param name: str, Name of the microscope.
        :param role: str, Role of the microscope.
        :param children: dict, Dictionary with the children of the microscope.
            "sem-scanner": dict, SEM scanner child configuration (required).
            "sem-focus": dict, SEM focus child configuration (optional).
            "sem-detector": dict, SEM detector child configuration (optional).
            "fib-scanner": dict, FIB scanner child configuration (required).
            "fib-focus": dict, FIB focus child configuration (optional).
            "fib-detector": dict, FIB detector child configuration (optional).
            "stage": dict, Stage child configuration (optional).
            Note: At least one of the required scanners types must be included as a child.
        :param address: str, server ip address for the microscope server (sim address is localhost)
        :param port: str, server port of the Microscope server, default is '4243'
        :param daemon: Pyro4.Daemon (or None), as defined in HwComponent.
        :param kwargs: dict, Additional keyword arguments.
        """

        model.HwComponent.__init__(self, name, role, daemon=daemon, **kwargs)
        self._proxy_access = threading.Lock()
        try:
            self.server = Pyro5.api.Proxy(f"PYRO:Microscope@{address}:{port}")
            self.server._pyroTimeout = 30  # seconds
            self._swVersion = self.server.get_software_version()
            self._hwVersion = self.server.get_hardware_version()
            if "adapter: autoscript" not in self._swVersion:
                raise HwError("The connected server is not an autoscript server. Please check the xt adapter configuration."
                              "The server software version is '%s'." % self._swVersion)
            logging.debug(
                f"Successfully connected to autoscript server with software version {self._swVersion} and hardware"
                f"version {self._hwVersion}")
        except CommunicationError as err:
            raise HwError("Failed to connect to autoscript server '%s'. Check that the "
                          "uri is correct and autoscript server is"
                          " connected to the network. %s %s" % (address, err, debug_connection_info))
        except OSError as err:
            raise HwError("XT server reported error: %s." % (err,))

        # Transfer latest xtadapter package if available
        # The transferred package will be a zip file in the form of bytes
        check_and_transfer_latest_package(self)

        # Create the scanner type child(ren)
        # Check if at least one of the required scanner types is instantiated
        scanner_types = ["sem-scanner", "fib-scanner"]  # All allowed scanners types
        if not any(scanner_type in children for scanner_type in scanner_types):
            raise KeyError("SEM was not given any scanner as child. "
                           "One of 'sem-scanner', 'fib-scanner' need to be included as child")

        if "sem-scanner" in children:
            kwargs = children["sem-scanner"]
            has_detector = "sem-detector" in children
            self._scanner = Scanner(parent=self, daemon=daemon, channel="electron", has_detector=has_detector, **kwargs)
            self.children.value.add(self._scanner)

        if "fib-scanner" in children:
            kwargs = children["fib-scanner"]
            has_detector = "fib-detector" in children
            self._fib_scanner = Scanner(parent=self, daemon=daemon, channel="ion", has_detector=has_detector, **kwargs)
            self.children.value.add(self._fib_scanner)

        # create the stage child, if requested
        if "stage" in children:
            ckwargs = children["stage"]
            self._stage = Stage(parent=self, daemon=daemon, **ckwargs)
            self.children.value.add(self._stage)

        # create a focuser, if requested
        if "sem-focus" in children:
            ckwargs = children["sem-focus"]
            self._focus = Focus(parent=self, daemon=daemon, channel="electron", **ckwargs)
            self.children.value.add(self._focus)

        if "fib-focus" in children:
            ckwargs = children["fib-focus"]
            self._fib_focus = Focus(parent=self, daemon=daemon, channel="ion", **ckwargs)
            self.children.value.add(self._fib_focus)

        # create a detector, if requested
        if "sem-detector" in children:
            ckwargs = children["sem-detector"]
            self._detector = Detector(parent=self, daemon=daemon, channel="electron", **ckwargs)
            self.children.value.add(self._detector)

        if "fib-detector" in children:
            ckwargs = children["fib-detector"]
            self._fib_detector = Detector(parent=self, daemon=daemon, channel="ion", **ckwargs)
            self.children.value.add(self._fib_detector)

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

    def get_software_version(self) -> str:
        """Returns: (str) the software version of the microscope."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_software_version()

    def get_hardware_version(self) -> str:
        """Returns: (str) the hardware version of the microscope."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_hardware_version()

    def move_stage_absolute(self, position: Dict[str, float]) -> None:
        """ Move the stage the given position. This is blocking.
        :param position: absolute position to move the stage to per axes.
            Axes are 'x', 'y', 'z', 't', 'r'.
            The unit is meters for axes 'x', 'y' and 'z', and radians for axes 't', 'r'.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.move_stage_absolute(position)

    def move_stage_relative(self, position: Dict[str, float]) -> None:
        """
        Move the stage by the given relative position. This is blocking.
        :param position: relative position to move the stage to per axes in m.
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

    def get_stage_position(self) -> Dict[str, float]:
        """
        :return: the axes of the stage as keys with their corresponding position.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_stage_position()

    def stage_info(self) -> Dict[str, Dict[str, Union[str, Tuple[float, float]]]]:
        """Returns: (dict) the unit and range of the stage position."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.stage_info()

    def set_default_stage_coordinate_system(self, coordinate_system: str) -> None:
        """
        Set the default stage coordinate system. (Raw, Specimen)
        Raw: raw stage coordinates use the stage's encoder positions
        Specimen: specimen coordinates use the linked-z coordinate system (which is based on
                    the SEM working distance)
        :param coordinate_system: Name of the coordinate system to set as default.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_default_stage_coordinate_system(coordinate_system)

    def get_stage_coordinate_system(self) -> str:
        """
        Get the current stage coordinate system. (Raw, Specimen)
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_stage_coordinate_system()

    def home_stage(self):
        """Home stage asynchronously. This is non-blocking."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.home_stage()

    def is_homed(self) -> bool:
        """Returns: True if the stage is homed and False otherwise."""
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

    def is_linked(self) -> bool:
        """
        Returns: True if Z follows free working distance.
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

    def get_chamber_state(self) -> str:
        """return: the vacuum state of the microscope chamber to see if it is pumped or vented,
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

    def pressure_info(self) -> Dict[str, Union[str, Tuple[float, float]]]:
        """Returns: the unit and range of the pressure."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.pressure_info()

##### BEAM CONTROL
    def set_external_scan_mode(self, channel: str) -> None:
        """Set the external scan mode."""
        self.set_scan_mode(mode="external", channel=channel, value=None)

    def set_full_frame_scan_mode(self, channel: str) -> None:
        """Set the full frame scan mode."""
        self.set_scan_mode(mode="full_frame", channel=channel, value=None)

    def set_spot_scan_mode(self, channel: str, x: float, y: float) -> None:
        """Set the spot scan mode."""
        self.set_scan_mode(mode="spot", channel=channel, value={"x": x, "y": y})

    def set_line_scan_mode(self, channel: str, position: float) -> None:
        """Set the line scan mode."""
        self.set_scan_mode(mode="line", channel=channel, value=position)

    def set_crossover_scan_mode(self, channel: str) -> None:
        """Set the crossover scan mode."""
        self.set_scan_mode(mode="crossover", channel=channel, value=None)

    def set_reduced_area_scan_mode(self, channel: str, left: float, top: float, width: float, height: float) -> None:
        """Set the reduced area scan mode."""
        self.set_scan_mode(mode="reduced_area", channel=channel,
                           value={"left": left, "top": top, "width": width, "height": height})

    def set_scan_mode(self, mode: str, channel: str, value: Optional[Union[float, dict]] = None) -> None:
        """
        Set the scan mode.
        :param mode: (str) Name of desired scan mode, one of: crossover, external, reduced_area, full_frame, spot, or line.
        :param channel: (str) Name of the channel to set the scan mode for.
        :param value: (float or dict) Value of the scan mode. The value is dependent on the scan mode.
            mode = line:            value = float for position of line
            mode = reduced_area:    value = dict with keys: left, top, width, height (0 - 1)
            mode = spot:            value = dict with keys x, y (0 - 1)
            mode = full_frame:      value = None
            mode = external:        value = None
            mode = crossover:       value = None
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_scan_mode(mode=mode, channel=channel, value=value)

    def get_scan_mode(self, channel: str) -> str:
        """
        Get the scan mode.
        :param channel: Name of the channel to get the scan mode for.
        :return: Name of set scan mode, one of: unknown, external, full_frame, spot, or line.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_scan_mode(channel)

    def scan_mode_info(self) -> List[str]:
        """Returns: the available scanning modes"""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.scan_mode_info()

    def set_spotsize(self, spotsize: float, channel: str) -> None:
        """
        Setting the spot size of the selected beam.
        :param spotsize: the spot size of the beam in meters.
        :param channel: Name of the channel to set the spot size for.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_spotsize(spotsize, channel)

    def get_spotsize(self, channel: str) -> float:
        """Returns: the current spotsize of the selected beam (unitless)."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_spotsize(channel)

    def spotsize_info(self, channel: str) -> Dict[str, Union[str, Tuple[float, float]]]:
        """Returns: the unit and range of the spotsize. Unit is None means the spotsize is unitless."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.spotsize_info(channel)

    def set_dwell_time(self, dwell_time: float, channel: str) -> None:
        """
        :param dwell_time: the dwell time in seconds.
        :param channel: Name of the channel to set the dwell time for.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_dwell_time(dwell_time, channel)

    def get_dwell_time(self, channel: str) -> float:
        """return: the dwell time in seconds."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_dwell_time(channel)

    def dwell_time_info(self, channel: str) -> Dict[str, Union[str, Tuple[float, float]]]:
        """:return: range of the dwell time and corresponding unit."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.dwell_time_info(channel)

    def set_field_of_view(self, field_of_view: float, channel: str) -> None:
        """
        Set the field of view.
        :param field_of_view: the field of view in meters.
        :param channel: Name of the channel to set the field of view for.
        :return: None
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_field_of_view(field_of_view, channel)

    def get_field_of_view(self, channel: str) -> float:
        """
        :param channel: Name of the channel to get the field of view for.
        :return: the field of view in meters.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_field_of_view(channel)

    def field_of_view_info(self, channel: str) -> Dict[str, Union[str, Tuple[float, float]]]:
        """returns the scanning size unit and range."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.field_of_view_info(channel)

    def set_high_voltage(self, voltage: float, channel: str) -> None:
        """
        Set the high voltage.
        :param voltage: the high voltage in volt.
        :param channel: Name of the channel to set the high voltage for.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_high_voltage(voltage, channel)

    def get_high_voltage(self, channel: str) -> float:
        """:return the high voltage in volt."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_high_voltage(channel)

    def high_voltage_info(self, channel: str) -> Dict[str, Union[str, Tuple[float, float]]]:
        """:return the unit and range of the high voltage."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.high_voltage_info(channel)

    def set_beam_current(self, current: float, channel:str) -> None:
        """
        Set the beam current.
        :param current: the beam current in ampere.
        :param channel: Name of the channel to set the beam current for.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_beam_current(current, channel)

    def get_beam_current(self, channel: str) -> float:
        """Returns: the beam current in ampere."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_beam_current(channel)

    def beam_current_info(self, channel: str) -> dict:
        """Returns: the unit and range of the beam current."""
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
        """return:  True if the beam is blanked and False if the beam is not blanked."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.beam_is_blanked(channel)

    def beam_is_installed(self, channel: str) -> bool:
        """return: True if the beam is installed and False if the beam is not installed."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.beam_is_installed(channel)

    def get_working_distance(self, channel: str) -> float:
        """Returns: the working distance in meters.
        :param channel: Name of the channel to get the free working distance for.
        :return: the working distance in meters.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_working_distance(channel)

    def set_working_distance(self, working_distance: float, channel: str) -> None:
        """
        Set the working distance.
        :param working_distance: the free working distance in meters.
        :param channel: Name of the channel to set the free working distance for.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_working_distance(working_distance, channel)

    def working_distance_info(self, channel: str) -> Dict[str, Union[str, Tuple[float, float]]]:
        """Returns the unit and range of the working distance.
        :param channel: Name of the channel to get the free working distance for.
        :return: the unit and range of the working distance."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.working_distance_info(channel)

    def get_beam_shift(self, channel: str) -> Tuple[float, float]:
        """Returns: the current beam shift (DC coils position) x and y values in meters."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return tuple(self.server.get_beam_shift(channel))

    def set_beam_shift(self, x: float, y: float, channel: str) -> None:
        """Set the beam shift values in metrers (absolute movement).
        :param x: the x value of the beam shift in meters.
        :param y: the y value of the beam shift in meters.
        :param channel: Name of the channel to set the beam shift for.
        :return None
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_beam_shift(x, y, channel)

    def move_beam_shift(self, x: float, y: float, channel: str) -> None:
        """Move the beam shift values in meters (relative movement).
        :param x_shift: the x value of the beam shift in meters.
        :param y_shift: the y value of the beam shift in meters.
        :param channel: Name of the channel to move the beam shift for.
        :return None"""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.move_beam_shift(x, y, channel)

    def beam_shift_info(self, channel: str) -> Dict[str, Union[str, Tuple[float, float]]]:
        """Returns: the unit and xy-range of the beam shift
        :param channel: Name of the channel to get the beam shift for.
        :return: the unit and xy-range of the beam shift.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.beam_shift_info(channel)

    def get_stigmator(self, channel: str) -> Tuple[float, float]:
        """
        Retrieves the current stigmator x and y values.
        This stigmator corrects for the astigmatism of the probe shape.
        :param channel: name of the channel
        :return: tuple, (x, y) current stigmator values, unitless
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return tuple(self.server.get_stigmator(channel))

    def set_stigmator(self, x: float , y: float, channel: str) -> None:
        """
        Set the current stigmator x and y values.
        This stigmator corrects for the astigmatism of the probe shape.
        :param x: the x value of the stigmator, unitless
        :param y: the y value of the stigmator, unitless
        :param channel: name of the channel
        :return None
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_stigmator(x, y, channel)

    def stigmator_info(self, channel: str) -> Dict[str, Union[str, Tuple[float, float]]]:
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
        """Returns: the current rotation value in rad."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_scan_rotation(channel)

    def set_scan_rotation(self, rotation: float, channel: str) -> None:
        """Set the current rotation value in rad."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_scan_rotation(rotation, channel)

    def scan_rotation_info(self, channel: str) -> Dict[str, Union[str, Tuple[float, float]]]:
        """Returns: the unit and range of the rotation."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.scan_rotation_info(channel)

    def set_resolution(self, resolution: Tuple[int, int], channel: str) -> None:
        """
        Set the resolution of the image.
        :param resolution: The resolution of the image in pixels as (width, height).
            Options: (768, 512), (1536, 1024), (3072, 2048), (6144, 4096).
            Technically can be anything, but not all modes are supported for non-standard res
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_resolution(resolution, channel)

    def get_resolution(self, channel: str) -> Tuple[int, int]:
        """Returns the resolution of the image as (width, height)."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return tuple(self.server.get_resolution(channel))

    def resolution_info(self, channel: str) -> Dict[str, Union[str, Tuple[int, int]]]:
        """Returns the unit and range of the resolution of the image."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.resolution_info(channel)

    def set_beam_power(self, state: bool, channel: str) -> None:
        """
        Turn the beam on or off.
        :param state: True to turn the beam on and False to turn the beam off.
        :param channel: Name of the channel to turn the beam on or off for.
        :return None
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.turn_beam_on(state, channel)

    def get_beam_is_on(self, channel: str) -> bool:
        """Returns True if the beam is on and False if the beam is off."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.beam_is_on(channel)

#### DETECTOR CONTROL
    def set_detector_mode(self, mode: str, channel: str) -> None:
        """
        Set the mode of the detector.
        :param mode: Name of the mode to set the detector to.
        :param channel: Name of one of the channels.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_detector_mode(mode, channel)

    def get_detector_mode(self, channel: str) -> str:
        """
        Get the mode of the detector.
        :param channel: Name of one of the channels.
        :return: Name of the mode the detector is set to.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_detector_mode(channel)

    def detector_mode_info(self, channel: str) -> Dict[str, List[str]]:
        """Returns the mode of the detector."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.detector_mode_info(channel)

    def set_detector_type(self, detector_type: str, channel: str) -> None:
        """
        Set the type of the detector.
        :param detector_type: Name of the type to set the detector to.
        :param channel: Name of one of the channels.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_detector_type(detector_type, channel)

    def get_detector_type(self, channel: str) -> str:
        """
        Get the type of the detector.
        :param channel: Name of one of the channels.
        :return: Name of the type the detector is set to.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_detector_type(channel)

    def detector_type_info(self, channel: str) -> Dict[str, List[str]]:
        """Returns the type of the detector."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.detector_type_info(channel)

    def set_contrast(self, contrast: float, channel: str) -> None:
        """
        Set the contrast of the scanned image to a specified factor.
        :param contrast: Value the contrast should be set to as a factor between 0 and 1.
        :param channel: Name of one of the channels.

        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_contrast(contrast, channel)

    def get_contrast(self, channel: str) -> float:
        """
        Get the contrast of the scanned image.
        :param channel: Name of one of the channels.
        :return: Returns value of current contrast as a factor between 0 and 1.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_contrast(channel)

    def contrast_info(self, channel: str) -> Dict[str, Union[str, Tuple[float, float]]]:
        """Returns the contrast unit [-] and range [0, 1]."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.contrast_info(channel)

    def set_brightness(self, brightness: float, channel: str) -> None:
        """
        Set the brightness of the scanned image to a specified factor.
        :param brightness: Value the brightness should be set to as a factor between 0 and 1.
        :param channel: Name of one of the channels.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_brightness(brightness, channel)

    def get_brightness(self, channel: str) -> float:
        """
        Get the brightness of the scanned image.
        :param channel_name: Name of one of the channels.
        :return: Returns value of current brightness as a factor between 0 and 1.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_brightness(channel)

    def brightness_info(self, channel: str) -> Dict[str, Union[str, Tuple[float, float]]]:
        """Returns the brightness unit [-] and range [0, 1]."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.brightness_info(channel)

#### IMAGING CONTROL
    def get_active_view(self) -> int:
        """Returns: the active view."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_active_view()

    def get_active_device(self) -> int:
        """Returns: the active device."""
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

    def set_channel(self, channel: str) -> None:
        """Set the active channel. (electron or ion)"""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_channel(channel)

    def acquire_image(self, channel: str, frame_settings: Optional[Dict] = None) -> Tuple[numpy.ndarray, Dict[str, Any]]:
        """
        Acquire an image from the detector (blocking).
        :param channel: Name of one of the channels.
        :return: the acquired image and metadata.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.acquire_image(channel, frame_settings)

    def get_last_image(self, channel: str, wait_for_frame: bool = True) -> Tuple[numpy.ndarray, Dict[str, Any]]:
        """
        Get the last acquired image from the detector.
        :param channel: Name of one of the channels.
        :return: the last acquired image and metadata.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_last_image(channel, wait_for_frame)

    def start_acquisition(self, channel: str) -> None:
        """
        Start the acquisition of images.
        :param channel: Name of one of the channels.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.start_acquisition(channel)

    def stop_acquisition(self, channel: str, wait_for_frame: bool = True) -> None:
        """
        Stop the acquisition of images.
        :param channel: Name of one of the channels.
        :param wait_for_frame: If True, the function will wait until the current frame is acquired.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.stop_acquisition(channel, wait_for_frame)

    def get_imaging_state(self, channel: str) -> str:
        """
        Get the state of the imaging scan device (Error, Idle, Running, Paused).
        :param channel: Name of one of the channels.
        :return: Name of the state the imaging device is set to.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_imaging_state(channel)

    def set_scanning_filter(self, channel: str, filter_type: int, n_frames: int = 1) -> None:
        """
        Set the scanning filter for the detector.
        :param channel: Name of one of the channels.
        :param filter_type: Type of the filter to set [1: None, 2: Averaging, 3: Integrating]
        :param n_frames: Number of frames to average over.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_scanning_filter(channel, filter_type, n_frames)

    def get_scanning_filter(self, channel: str) -> Dict[str, int]:
        """
        Get the scanning filter for the detector.
        :param channel: Name of one of the channels.
        :return: the scanning filter type (filter_type) and number of frames (n_frames).
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_scanning_filter(channel)

    def get_scanning_filter_info(self, channel: str) -> List[str]:
        """
        Get the available scanning filters for the detector.
        :param channel: Name of one of the detector channels.
        :return: the available scanning filters for the channel
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_scanning_filter_info(channel)

#### AUTO FUNCTIONS
    def run_auto_contrast_brightness(self, channel: str, parameters: Dict = {}) -> None:
        """Run auto contrast brightness function (blocking)
        :param channel: Name of one of the channels.
        :param parameters: (dict) Dictionary containing the parameters
        :return: None
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.run_auto_contrast_brightness(channel, parameters=parameters)

#### MILLING CONTROL
    def create_rectangle(self, parameters: Dict[str, Union[str, int, float]]) -> Dict[str, int]:
        """
        Create a rectangle milling pattern.
        :param parameters: Dictionary containing the pattern parameters.
        :return: Dictionary containing pattern id and estimated time.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.create_rectangle(parameters)

    def create_cleaning_cross_section(self, parameters: Dict[str, Union[str, int, float]]) -> Dict[str, int]:
        """
        Create a cleaning cross section milling pattern.
        :param parameters: Dictionary containing the pattern parameters.
        :return: Dictionary containing pattern id and estimated time.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.create_cleaning_cross_section(parameters)

    def create_regular_cross_section(self, parameters: Dict[str, Union[str, int, float]]) -> Dict[str, int]:
        """
        Create a regular cross section milling pattern.
        :param parameters: Dictionary containing the pattern parameters.
        :return: Dictionary containing pattern id and estimated time.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.create_regular_cross_section(parameters)

    def create_line(self, parameters: Dict[str, Union[str, int, float]]) -> Dict[str, int]:
        """
        Create a line milling pattern.
        :param parameters: Dictionary containing the pattern parameters.
        :return: Dictionary containing pattern id and estimated time.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.create_line(parameters)

    def create_circle(self, parameters: Dict[str, Union[str, int, float]]) -> Dict[str, int]:
        """
        Create a circle milling pattern.
        :param parameters: Dictionary containing the pattern parameters.
        :return: Dictionary containing pattern id and estimated time.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.create_circle(parameters)

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
        """Returns: the state of the patterning."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_patterning_state()

    def get_patterning_mode(self) -> str:
        """Returns: the mode of the patterning. (Serial, Parallel)"""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_patterning_mode()

    def set_patterning_mode(self, mode: str) -> None:
        """Set the mode of the patterning. (Serial, Parallel)"""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_patterning_mode(mode)

    def clear_patterns(self) -> None:
        """Clear all patterns in fib. NOTE: active_view 2 is the fib view"""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_active_view(2) # channel = ion
            self.server.clear_patterns()

    def set_default_application_file(self, application_file: str = "Si") -> None:
        """
        Set the default application file.
        :param application_file: Name of the default application file.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_default_application_file(application_file)

    def set_default_patterning_beam_type(self, channel: str) -> None:
        """
        Set the default patterning beam type.
        :param channel: Name of one of the channels.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_default_patterning_beam_type(channel)

    def get_available_application_files(self) -> List[str]:
        """Returns: the available application files."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_available_application_files()

    def estimate_milling_time(self) -> float:
        """Returns: the estimated milling time for currently drawn patterns."""
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

    def __init__(
        self,
        name: str,
        role: str,
        parent: SEM,
        hfw_nomag: float,
        channel: str,
        has_detector: bool = False,
        **kwargs,
    ):
        """
        :param name: name of the Scanner
        :param role: role of the Scanner
        :param parent: parent of the Scanner
        :param hfw_nomag: horizontal field width at nominal magnification
        :param channel: name of the electron channel
        :param has_detector: True if a Detector is also controlled. In this case,
          the .resolution, .scale and associated VAs will be provided too.
        """
        self.parent: SEM  # for type hinting
        model.Emitter.__init__(self, name, role, parent=parent, **kwargs)

        self.channel = channel  # name of the channel used

        # will take care of executing auto contrast/brightness and auto stigmator asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        self._hfw_nomag = hfw_nomag
        self._has_detector = has_detector
        self._acq_type = model.MD_AT_EM if channel == "electron" else model.MD_AT_FIB

        # beam voltage
        voltage_info = self.parent.high_voltage_info(self.channel)
        init_voltage = numpy.clip(self.parent.get_high_voltage(self.channel),
                            voltage_info['range'][0], voltage_info['range'][1])
        self.accelVoltage = model.FloatContinuous(
            init_voltage,
            voltage_info["range"],
            unit=voltage_info["unit"],
            setter=self._setVoltage
        )

        # beam current
        # NOTE: VA is named probeCurrent to match existing API
        beam_current_info = self.parent.beam_current_info(self.channel)
        if "choices" in beam_current_info:
            self.probeCurrent = model.FloatEnumerated(
                value=self.parent.get_beam_current(self.channel),
                choices=set(beam_current_info["choices"]),
                unit=beam_current_info["unit"],
                setter=self._setCurrent)
        else:
            self.probeCurrent = model.FloatContinuous(
                value=self.parent.get_beam_current(self.channel),
                range=beam_current_info["range"],
                unit=beam_current_info["unit"],
                setter=self._setCurrent
            )

        # beamshift
        beam_shift_info = self.parent.beam_shift_info(self.channel)
        range_x = beam_shift_info["range"]["x"]
        range_y = beam_shift_info["range"]["y"]
        self.shift = model.TupleContinuous(
            self.parent.get_beam_shift(self.channel),
            ((range_x[0], range_y[0]), (range_x[1], range_y[1])),
            cls=(int, float),
            unit=beam_shift_info["unit"],
            setter=self._setBeamShift)

        # stigmator
        stigmator_info = self.parent.stigmator_info(self.channel)
        range_x = stigmator_info["range"]["x"]
        range_y = stigmator_info["range"]["y"]
        self.stigmator = model.TupleContinuous(
            self.parent.get_stigmator(self.channel),
            ((range_x[0], range_y[0]), (range_x[1], range_y[1])),
            cls=(float, float),
            unit=stigmator_info["unit"],
            setter=self._setStigmator)

        # scan rotation
        rotation_info = self.parent.scan_rotation_info(self.channel)
        self.rotation = model.FloatContinuous(
            self.parent.get_scan_rotation(self.channel),
            rotation_info["range"],
            unit=rotation_info["unit"],
            setter=self._setRotation)

        # horizontal field of view
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

            # dwell time
            dwell_time_info = self.parent.dwell_time_info(self.channel)
            self.dwellTime = model.FloatContinuous(
                self.parent.get_dwell_time(self.channel),
                dwell_time_info["range"],
                unit=dwell_time_info["unit"],
                setter=self._setDwellTime)
            # when the range has changed, clip the current dwell time value to the new range
            self.dwellTime.clip_on_range = True

            rng = DETECTOR_RNG
            self._shape = (rng[1][0], rng[1][1])
            # pixelSize is the same as MD_PIXEL_SIZE, with scale == 1
            # == smallest size/ between two different ebeam positions
            pxs = (fov / self._shape[0],
                   fov / self._shape[0])
            # pixelsize is inferred indirectly via resolution and fov
            self.pixelSize = model.VigilantAttribute(pxs, unit="m", readonly=True)

            # scanning resolution
            resolution = self.parent.get_resolution(self.channel)
            res_choices = set(RESOLUTIONS)
            self.resolution = model.VAEnumerated(resolution, res_choices, unit="px" , setter=self._setResolution)

            # (float, float) as a ratio => how big is a pixel, compared to pixelSize
            # it basically works the same as binning, but can be float.
            # Defined as the scale to match the allowed resolutions, with pixels
            # always square (ie, scale is always the same in X and Y).
            scale = (self._shape[0] / resolution[0],) * 2
            scale_choices = set((self._shape[0] / r[0],) * 2 for r in res_choices)
            self.scale = model.VAEnumerated(scale, scale_choices, unit="", setter=self._setScale)
            self.scale.subscribe(self._onScale, init=True)  # to update metadata

            # Just to make some code happy
            self.translation = model.TupleContinuous((0, 0), range=[(-512, -512), (512, 512)], unit="px", readonly=True)

        # beam blank
        self.blanker = model.BooleanVA(
            value=self.parent.beam_is_blanked(channel=self.channel),
                                       setter=self._setBlanker)
        # beam is on/off
        # NOTE: VA is named power to match existing API
        self.power = model.BooleanVA(
            self.parent.get_beam_is_on(channel=self.channel),
            setter=self._setBeamPower
        )

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
        try:
            if self._has_detector:
                dwell_time = self.parent.get_dwell_time(self.channel)
                if dwell_time != self.dwellTime.value:
                    self.dwellTime._value = dwell_time
                    self.dwellTime.notify(dwell_time)
                res = self.parent.get_resolution(self.channel)
                if res != self.resolution.value:
                    self.resolution._value = res
                    self.resolution.notify(res)
                self._updateResolution()

            voltage = self.parent.get_high_voltage(self.channel)
            v_range = self.accelVoltage.range
            if not v_range[0] <= voltage <= v_range[1]:
                logging.info("Voltage {} V is outside of range {}, clipping to nearest value.".format(voltage, v_range))
                voltage = self.accelVoltage.clip(voltage)
            if voltage != self.accelVoltage.value:
                self.accelVoltage._value = voltage
                self.accelVoltage.notify(voltage)
            beam_current = self.parent.get_beam_current(self.channel)
            if beam_current != self.probeCurrent.value:
                self.probeCurrent._value = beam_current
                self.probeCurrent.notify(beam_current)
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
            beam_is_on = self.parent.get_beam_is_on(self.channel)
            if beam_is_on != self.power.value:
                self.power._value = beam_is_on
                self.power.notify(beam_is_on)
            is_blanked = self.parent.beam_is_blanked(self.channel)
            if is_blanked != self.blanker.value:
                self.blanker._value = is_blanked
                self.blanker.notify(is_blanked)
        except Exception:
            logging.exception("Unexpected failure when polling settings")

    def _setScale(self, value: Tuple[int, int]) -> Tuple[int, int]:
        """
        value (1 < float, 1 < float): increase of size between pixels compared to
            the original pixel size. It will adapt the resolution to
            have the same ROI (just different amount of pixels scanned)
        return the actual value used
        """
        # Pick the resolution which matches the scale in X
        res_x = int(round(self._shape[0] / value[0]))
        res = next(r for r in self.resolution.choices if r[0] == res_x)

        self.resolution.value = res

        return value

    def _setResolution(self, value: Tuple[int, int]) -> Tuple[int, int]:
        self.parent.set_resolution(value, self.channel)
        self._updateResolution() # to update scale -> pixelsize
        return value

    def _onScale(self, s) -> None:
        self._updatePixelSize()

    def _updateResolution(self) -> None:
        """
        To be called to read the server resolution and update the corresponding VAs
        """
        resolution = tuple(self.parent.get_resolution(self.channel))
        if resolution != self.resolution.value:
            scale = (self._shape[0] / resolution[0],) * 2
            self.scale._value = scale  # To not call the setter
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
        self.parent.set_dwell_time(dwell_time, channel=self.channel)
        return self.parent.get_dwell_time(self.channel)

    def _setVoltage(self, voltage: float) -> float:
        self.parent.set_high_voltage(voltage, channel=self.channel)
        return self.parent.get_high_voltage(self.channel)   # return the actual value used

    def _setCurrent(self, current: float) -> float:
        self.parent.set_beam_current(current, channel=self.channel)
        return self.parent.get_beam_current(self.channel)  # return the actual value used

    def _setBlanker(self, blank: bool) -> bool:
        if blank:
            self.parent.blank_beam(self.channel)
        else:
            self.parent.unblank_beam(self.channel)
        return self.parent.beam_is_blanked(self.channel)

    def _setBeamShift(self, beam_shift: Tuple[float, float]) -> Tuple[float, float]:
        self.parent.set_beam_shift(x=beam_shift[0], y=beam_shift[1], channel=self.channel)
        return self.parent.get_beam_shift(self.channel)

    def _setStigmator(self, stigmator: Tuple[float, float]) -> Tuple[float, float]:
        self.parent.set_stigmator(x=stigmator[0], y=stigmator[1], channel=self.channel)
        return self.parent.get_stigmator(self.channel)

    def _setRotation(self, rotation: float) -> float:
        self.parent.set_scan_rotation(rotation, channel=self.channel)
        return self.parent.get_scan_rotation(self.channel)

    def _setHorizontalFoV(self, fov: float) -> float:
        self.parent.set_field_of_view(fov, channel=self.channel)
        fov = self.parent.get_field_of_view(self.channel)
        mag = self._hfw_nomag / fov
        self.magnification._value = mag
        self.magnification.notify(mag)
        return fov

    def _onHorizontalFoV(self, fov: float) -> None:
        self._updateDepthOfField()
        if self._has_detector:
            self._updatePixelSize()

    def _updateDepthOfField(self) -> None:
        fov = self.horizontalFoV.value
        # Formula was determined by experimentation
        K = 100  # Magical constant that gives a not too bad depth of field
        dof = K * (fov / 1024)
        self.depthOfField._set_value(dof, force_write=True)

    def _setBeamPower(self, on: bool) -> bool:
        self.parent.set_beam_power(on, channel=self.channel)
        return self.parent.get_beam_is_on(self.channel)

class Detector(model.Detector):
    """
    This is an extension of model.Detector class. It performs the main functionality
    of the SEM. It sets up a Dataflow and notifies it every time that an SEM image
    is captured.
    """

    def __init__(self, name: str, role: str, parent: SEM, channel: str, **kwargs):
        """
        :param name: name of the Detector
        :param role: role of the Detector
        :param parent: parent of the Detector
        :param channel: name of the acquistion channel (electron, ion)
        :param kwargs: additional keyword arguments
        """
        # The acquisition is based on a FSM that roughly looks like this:
        # Event\State |    Stopped    |   Acquiring    | Receiving data |
        #    START    | Ready for acq |        .       |       .        |
        #    DATA     |       .       | Receiving data |       .        |
        #    STOP     |       .       |     Stopped    |    Stopped     |
        #    TERM     |     Final     |      Final     |     Final      |
        self.parent: SEM  # for type hinting
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

        # detector type: ETD, TLD, etc.
        self.type = model.StringEnumerated(
            value=self.parent.get_detector_type(self.channel),
            choices=set(self.parent.detector_type_info(self.channel)["choices"]),
            setter=self._setDetectorType)

        # detector mode: SecondaryElectrons, BackscatterElectrons, etc
        self.mode = model.StringEnumerated(
            value=self.parent.get_detector_mode(self.channel),
            choices=set(self.parent.detector_mode_info(self.channel)["choices"]),
            setter=self._setDetectorMode)

        self._genmsg = queue.Queue()  # GEN_*
        self._generator = None

        # Refresh regularly the values, from the hardware, starting from now
        self._updateSettings()
        # self._va_poll = util.RepeatingTimer(5, self._updateSettings, "Settings polling detector")
        # self._va_poll.start()
        # Note: using the repeated polling causes the view in xtUI to be changed every five seconds,
        # this makes it pretty annoying to do anything on that side, and error prone.
        # disabling this until a better solution is found

        # median filter applied to the image (required for cryo data)
        self.medianFilter = model.IntContinuous(0, range=(0, 9), setter=self._setMedianFilter)

    def terminate(self) -> None:
        if self._generator:
            self.stop_generate()
            self._genmsg.put(GEN_TERM)
            self._generator.join(5)
            self._generator = None
        super().terminate()

    def start_generate(self) -> None:
        self._genmsg.put(GEN_START)
        if not self._generator or not self._generator.is_alive():
            logging.info("Starting acquisition thread")
            self._generator = threading.Thread(target=self._acquire,
                                               name="autoscript acquisition thread")
            self._generator.start()

    def stop_generate(self) -> None:
        self.stop_acquisition(wait_for_frame=False)
        self._genmsg.put(GEN_STOP)
        # TODO: add a cancel once scanning is asynchronous

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

                    # HACK: from xt_client to prevent double scanning
                    if self._acq_should_stop(timeout=0.0):
                        logging.debug("Image acquisition should stop, exiting loop")
                        break

                    # NOTE: the stage metadata is late when acquiring an overview (it is for the previous tile)
                    # we need to do some kind of synchronization to get the right stage position
                    time.sleep(0.2) # TODO: do something more intelligent

                    md = self._scanner._metadata.copy()
                    md[model.MD_BEAM_DWELL_TIME] = self._scanner.dwellTime.value
                    md[model.MD_ROTATION] = self._scanner.rotation.value
                    md[model.MD_BEAM_VOLTAGE] = self._scanner.accelVoltage.value
                    md[model.MD_BEAM_CURRENT] = self._scanner.probeCurrent.value
                    md[model.MD_BEAM_SHIFT] = self._scanner.shift.value
                    md[model.MD_BEAM_FIELD_OF_VIEW] = self._scanner.horizontalFoV.value
                    md[model.MD_ACQ_TYPE] = self._scanner._acq_type
                    md[model.MD_ACQ_DATE] = time.time()
                    md[model.MD_STAGE_POSITION_RAW] = self.parent._stage.position.value
                    md.update(self._metadata)

                    # Estimated time for an acquisition is the dwell time times the total amount of pixels in the image.
                    n_pixels = self._scanner.resolution.value[0] * self._scanner.resolution.value[1]
                    est_acq_time = self._scanner.dwellTime.value * n_pixels

                    # HACK: from xt_client to prevent double scanning
                    if self._acq_should_stop(timeout=0.2):
                        logging.debug("Image acquisition should stop, exiting loop")
                        break

                    # Retrieve the image (scans image, blocks until the image is received)
                    # TODO: use the metadata from the image acquisition _md once it's available
                    image, _md = self.parent.acquire_image(self._scanner.channel)

                    # median filter to remove noise (required for cryo data)
                    if self.medianFilter.value > 0:
                        image = ndimage.median_filter(image, self.medianFilter.value)
                    # non-blocking acquisition (disabled until hw testing)
                    # logging.debug("Starting one image acquisition")
                    # # start the acquisition
                    # self.start_acquisition()
                    # # stop the acquisition at the end of the frame
                    # self.stop_acquisition(wait_for_frame=True)
                    # # wait for the frame to be received, or timeout
                    # try:
                    #     if self._acq_wait_data(est_acq_time * 1.1):
                    #         logging.debug("Stopping acquisition early")
                    #         self.stop_acquisition(wait_for_frame=False)
                    #         break
                    # except TimeoutError as err:
                    #     logging.error(err)
                    #     self.stop_acquisition(wait_for_frame=False)
                    #     break

                    # # Retrieve the image
                    # image = self.parent.get_last_image(self._scanner.channel, wait_for_frame=True)

                    da = DataArray(image, md)
                    logging.debug("Notify dataflow with new image.")
                    self.data.notify(da)
        except TerminationRequested:
            logging.debug("Acquisition thread requested to terminate")
        except Exception as err:
            logging.exception("Failure in acquisition thread: {}".format(err))
        finally:
            self._generator = None

    def start_acquisition(self) -> None:
        """Start acquiring images"""
        try:
            if self.parent.get_imaging_state(self._scanner.channel) == IMAGING_STATE_RUNNING:
                logging.info(f"Imaging state is already running for channel {self._scanner.channel}")
                return
        except Exception as e:
            logging.error(f"Error when checking imaging state: {e}")
            pass
        self.parent.start_acquisition(self._scanner.channel)

    def stop_acquisition(self, wait_for_frame: bool = True) -> None:
        """Stop acquiring images"""
        try:
            if self.parent.get_imaging_state(self._scanner.channel) == IMAGING_STATE_IDLE:
                logging.info(f"Imaging state is already stopped for channel {self._scanner.channel}")
                return
        except Exception as e:
            logging.error(f"Error when checking imaging state: {e}")
            pass

        self.parent.stop_acquisition(self._scanner.channel, wait_for_frame=wait_for_frame)

    def _acq_should_stop(self, timeout: Optional[int] = None) -> bool:
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

    def _acq_wait_data(self, timeout: int = 0) -> bool:
        """
        Block until data or a stop message is received.
        Note: it expects that the acquisition is running.

        timeout (0<=float): how long to wait to check (use 0 to not wait)
        return: True if needs to stop, False if data is ready
        raise TerminationRequested: if a terminate message was received
        """
        tend = time.time() + timeout
        t = time.time()
        logging.debug("Waiting for %g s:", tend - t)
        while self.parent.get_imaging_state(self._scanner.channel) != IMAGING_STATE_IDLE:
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
        return: message
        raises queue.Empty: if no message on the queue
        """
        msg = self._genmsg.get(**kwargs)
        if msg in (GEN_START, GEN_STOP, GEN_TERM):
            logging.debug("Acq received message %s", msg)
        else:
            logging.warning("Acq received unexpected message %s", msg)
        return msg

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
        detector_type = self.parent.get_detector_type(self._scanner.channel)
        if detector_type != self.type.value:
            self.type._value = detector_type
            self.type.notify(detector_type)
        detector_mode = self.parent.get_detector_mode(self._scanner.channel)
        if detector_mode != self.mode.value:
            self.mode._value = detector_mode
            self.mode.notify(detector_mode)

    def _setBrightness(self, brightness: float) -> float:
        self.parent.set_brightness(brightness, self._scanner.channel)
        return self.parent.get_brightness(self._scanner.channel)

    def _setContrast(self, contrast: float) -> float:
        self.parent.set_contrast(contrast, self._scanner.channel)
        return self.parent.get_contrast(self._scanner.channel)

    def _setDetectorMode(self, mode: str) -> str:
        self.parent.set_detector_mode(mode, self._scanner.channel)
        return self.parent.get_detector_mode(self._scanner.channel)

    def _setDetectorType(self, detector_type: str) -> str:
        self.parent.set_detector_type(detector_type, self._scanner.channel)
        return self.parent.get_detector_type(self._scanner.channel)

    def _setMedianFilter(self, value: int) -> int:
        """Set the median filter value and update the metadata."""

        # if value is 0, remove the filter from the metadata
        if value == 0:
            self.updateMetadata({model.MD_DATA_FILTER: None})
        else:
            self.updateMetadata({model.MD_DATA_FILTER: f"median-filter:{value}"})
        return value

    # TODO: add support for auto functions


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

    def __init__(self, name: str, role: str, parent: SEM, rng: Optional[Dict[str, Tuple[float, float]]] = None, **kwargs):
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

        # We use a "hybrid" coordinate system: the main advantage of the raw coordinate system
        # is that the Z axis is never "linked" to the working distance. When the Z axis is linked,
        # it's oriented in the other direction (0 is at the pole-piece, and an increase goes down),
        # and is dependent on the SEM working distance (focus).
        # In raw coordinates, Z always means the same thing, independent of what the user has done
        # in the GUI. However, for the other axes, there might be an offset, but as it's convenient
        # to show the same value as in the TFS GUI, we compensate for that offset.
        self._raw_offset = {"x": 0, "y": 0, "rx": 0, "rz": 0}

        model.Actuator.__init__(self, name, role, parent=parent, axes=axes_def,
                                **kwargs)
        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        self.position = model.VigilantAttribute({}, unit=stage_info["unit"],
                                                readonly=True)
        self._update_coordinate_system_offset() # to get the offset values for raw coordinate system
        self._updatePosition()

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

    def _update_coordinate_system_offset(self):
        """
        Calculate the offset values for raw coordinate system.
        The offset is the difference between the specimen (linked) and raw coordinate system.
        """
        self.parent.set_default_stage_coordinate_system("SPECIMEN")
        pos_linked = self._getPosition()
        self.parent.set_default_stage_coordinate_system("RAW")
        pos = self._getPosition()
        for axis in self._raw_offset.keys():
            self._raw_offset[axis] = pos_linked[axis] - pos[axis]
        logging.debug(f"The raw coordinates offset is {self._raw_offset}. "
                      f"Computed from raw stage coordinates: {pos}, specimen stage coordinates: {pos_linked}")

    def _updatePosition(self):
        """
        update the position VA
        """
        old_pos = self.position.value
        pos = self._getPosition()
        # Apply the offset to the raw coordinates
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

    def _refreshPosition(self):
        """
        Called regularly to update the current position
        """
        # We don't use the VA setters, to avoid sending back to the hardware a
        # set request
        try:
            self._updatePosition()
        except Exception:
            logging.exception("Unexpected failure when updating position")

    def _getPosition(self) -> Dict[str, float]:
        """Get position and translate the axes names to be Odemis compatible."""
        pos = self.parent.get_stage_position()
        pos["rx"] = pos.pop("t")
        pos["rz"] = pos.pop("r")
        return pos

    def _moveTo(self, future: CancellableFuture, pos: Dict[str, float], rel: bool = False, timeout: int = 60):
        with future._moving_lock:
            try:
                if future._must_stop.is_set():
                    raise CancelledError()
                if rel:
                    logging.debug("Moving by shift {}".format(pos))
                else:
                    # apply the offset to the raw coordinates
                    for axis, offset in self._raw_offset.items():
                        if axis in pos:
                            pos[axis] -= offset
                    logging.debug("Moving to position {}".format(pos))

                if "rx" in pos.keys():
                    pos["t"] = pos.pop("rx")
                if "rz" in pos.keys():
                    pos["r"] = pos.pop("rz")
                # movements are blocking
                if rel:
                    self.parent.move_stage_relative(pos)
                else:
                    self.parent.move_stage_absolute(pos)

            # if the move is cancelled, an exception is raised on the server side
            except Exception:
                if future._must_stop.is_set():
                    raise CancelledError()
                raise
            finally:
                future._was_stopped = True
                # Update the position, even if the move didn't entirely succeed
                self._updatePosition()

    def _doMoveRel(self, future: CancellableFuture, shift: Dict[str, float]) -> None:
        """
        shift (dict): position in internal coordinates (ie, axes in the same
           direction as the hardware expects)
        """
        # We don't check the target position fit the range, the autoscript-adapter will take care of that
        self._moveTo(future, shift, rel=True)

    @isasync
    def moveRel(self, shift: Dict[str, float]) -> Future:
        """
        Shift the stage the given position in meters. This is non-blocking.
        Throws an error when the requested position is out of range.

        Parameters
        ----------
        shift: dict(string->float)
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

    def _doMoveAbs(self, future, pos):
        self._moveTo(future, pos, rel=False)

    @isasync
    def moveAbs(self, pos: Dict[str, float]) -> Future:
        """
        Move the stage the given position in meters. This is non-blocking.
        Throws an error when the requested position is out of range.

        Parameters
        ----------
        pos: dict(string->float)
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

    def stop(self, axes=None) -> None:
        """Stop the movement of the stage."""
        self._executor.cancel()
        self.parent.stop_stage_movement()
        try:
            self._updatePosition()
        except Exception:
            logging.exception("Unexpected failure when updating position")

    def _createFuture(self) -> CancellableFuture:
        """
        Return (CancellableFuture): a future that can be used to manage a move
        """
        f = CancellableFuture()
        f._moving_lock = threading.Lock()  # taken while moving
        f._must_stop = threading.Event()  # cancel of the current future requested
        f._was_stopped = False  # if cancel was successful
        f.task_canceller = self._cancelCurrentMove
        return f

    def _cancelCurrentMove(self, future: CancellableFuture) -> bool:
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

    def __init__(self, name, role, parent, channel:str, **kwargs):
        """
        axes (set of string): names of the axes
        """

        self.channel = channel
        fwd_info = parent.working_distance_info(channel=channel)
        axes_def = {
            "z": model.Axis(unit=fwd_info["unit"], range=fwd_info["range"]),
        }

        model.Actuator.__init__(self, name, role, parent=parent, axes=axes_def, **kwargs)

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        # RO, as to modify it the server must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute({}, unit="m", readonly=True)
        self._updatePosition()

        if self.channel == "electron" and not hasattr(self.parent, "_scanner"):
            raise ValueError("Required scanner child was not provided."
                             "An ebeam scanner is a required child component for the Focus class")
        if self.channel == "ion" and not hasattr(self.parent, "_fib_scanner"):
            raise ValueError("Required fib scanner child was not provided."
                             "An fib scanner is a required child component for the Focus class")

        # Refresh regularly the position
        self._pos_poll = util.RepeatingTimer(5, self._refreshPosition, "Focus position polling")
        self._pos_poll.start()

    def terminate(self):
        if self._pos_poll:
            self._pos_poll.cancel()
            self._pos_poll = None
        super().terminate()

    def _updatePosition(self):
        """
        update the position VA
        """
        z = self.parent.get_working_distance(self.channel)
        if self.position.value.get("z") != z:
            logging.debug("Updating %s position to %s for channel %s", self.name, z, self.channel)
        self.position._set_value({"z": z}, force_write=True)

    def _refreshPosition(self):
        """
        Called regularly to update the current position
        """
        # We don't use the VA setters, to avoid sending back to the hardware a
        # set request
        try:
            self._updatePosition()
        except Exception:
            logging.exception("Unexpected failure when updating SEM focus position")

    def _doMoveRel(self, foc):
        """
        move by foc
        foc (float): relative change in m
        """
        try:
            foc += self.parent.get_working_distance(self.channel)
            self.parent.set_working_distance(foc, channel=self.channel)
        finally:
            # Update the position, even if the move didn't entirely succeed
            self._updatePosition()

    def _doMoveAbs(self, foc):
        """
        move to pos
        foc (float): unit m
        """
        try:
            self.parent.set_working_distance(foc, channel=self.channel)
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
