# -*- coding: utf-8 -*-
"""
Created on 16 Aug 2019

@author: Thera Pals, Kornee Kleijwegt, Sabrina Rossberger

Copyright © 2019-2021 Thera Pals, Delmic

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
from __future__ import division, print_function

import logging
import math
import queue
import threading
import time
from concurrent.futures import CancelledError

import Pyro5.api
import msgpack_numpy
import numpy

from odemis import model
from odemis import util
from odemis.model import CancellableThreadPoolExecutor, HwError, isasync, CancellableFuture, ProgressiveFuture, \
    DataArray

Pyro5.api.config.SERIALIZER = 'msgpack'
msgpack_numpy.patch()

XT_RUN = "run"
XT_STOP = "stop"

# Acquisition control messages
GEN_START = "S"  # Start acquisition
GEN_STOP = "E"  # Don't acquire image anymore
GEN_TERM = "T"  # Stop the generator

# Convert from a detector role (following the Odemis convention) to a detector name in xtlib
DETECTOR2CHANNELNAME = {
    "se-detector": "electron1",
}


class SEM(model.HwComponent):
    """
    Driver to communicate with XT software on TFS microscopes. XT is the software TFS uses to control their microscopes.
    To use this driver the XT adapter developed by Delmic should be running on the TFS PC. Communication to the
    Microscope server is done via Pyro5.
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

        model.HwComponent.__init__(self, name, role, daemon=daemon, **kwargs)
        self._proxy_access = threading.Lock()
        try:
            self.server = Pyro5.api.Proxy(address)
            self.server._pyroTimeout = 30  # seconds
            self._swVersion = self.server.get_software_version()
            self._hwVersion = self.server.get_hardware_version()
        except Exception as err:
            raise HwError("Failed to connect to XT server '%s'. Check that the "
                          "uri is correct and XT server is"
                          " connected to the network. %s" % (address, err))

        # create the scanner child
        try:
            kwargs = children["scanner"]
        except (KeyError, TypeError):
            raise KeyError("SEM was not given a 'scanner' child")
        self._scanner = Scanner(parent=self, daemon=daemon, **kwargs)
        self.children.value.add(self._scanner)

        # create the stage child, if requested
        if "stage" in children:
            ckwargs = children["stage"]
            self._stage = Stage(parent=self, daemon=daemon, **ckwargs)
            self.children.value.add(self._stage)

        # create a focuser, if requested
        if "focus" in children:
            ckwargs = children["focus"]
            self._focus = Focus(parent=self, daemon=daemon, **ckwargs)
            self.children.value.add(self._focus)

        # create a detector, if requested
        if "detector" in children:
            ckwargs = children["detector"]
            self._detector = Detector(parent=self, daemon=daemon, **ckwargs)
            self.children.value.add(self._detector)

    def list_available_channels(self):
        """
        List all available channels and their current state as a dict.

        Returns
        -------
        available channels: dict
            A dict of the names of the available channels as keys and the corresponding channel state as values.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.list_available_channels()

    def move_stage(self, position, rel=False):
        """
        Move the stage the given position in meters. This is non-blocking. Throws an error when the requested position
        is out of range.

        Parameters
        ----------
        position: dict(string->float)
            Absolute or relative position to move the stage to per axes in m. Axes are 'x' and 'y'.
        rel: boolean
            If True the staged is moved relative to the current position of the stage, by the distance specified in
            position. If False the stage is moved to the absolute position.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.move_stage(position, rel)

    def stage_is_moving(self):
        """Returns: (bool) True if the stage is moving and False if the stage is not moving."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.stage_is_moving()

    def stop_stage_movement(self):
        """Stop the movement of the stage."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.stop_stage_movement()

    def get_stage_position(self):
        """
        Returns: (dict) the axes of the stage as keys with their corresponding position.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_stage_position()

    def stage_info(self):
        """Returns: (dict) the unit and range of the stage position."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.stage_info()

    def get_latest_image(self, channel_name):
        """
        Acquire an image observed via the currently set channel. Note: the channel needs to be stopped before an image
        can be acquired. To acquire multiple consecutive images the channel needs to be started and stopped. This
        causes the acquisition speed to be approximately 1 fps.

        Returns
        -------
        image: numpy array
            The acquired image.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            image = self.server.get_latest_image(channel_name)
            return image

    def set_scan_mode(self, mode):
        """
        Set the scan mode.
        Parameters
        ----------
        mode: str
            Name of desired scan mode, one of: unknown, external, full_frame, spot, or line.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_scan_mode(mode)

    def get_scan_mode(self):
        """
        Get the scan mode.
        Returns
        -------
        mode: str
            Name of set scan mode, one of: unknown, external, full_frame, spot, or line.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_scan_mode()

    def set_selected_area(self, start_position, size):
        """
        Specify a selected area in the scan field area.

        Parameters
        ----------
        start_position: (tuple of int)
            (x, y) of where the area starts in pixel, (0,0) is at the top left.
        size: (tuple of int)
            (width, height) of the size in pixel.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_selected_area(start_position, size)

    def get_selected_area(self):
        """
        Returns
        -------
        x, y, width, height: pixels
            The current selected area. If selected area is not active it returns the stored selected area.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            x, y, width, height = self.server.get_selected_area()
            return x, y, width, height

    def selected_area_info(self):
        """Returns: (dict) the unit and range of set selected area."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.selected_area_info()

    def reset_selected_area(self):
        """Reset the selected area to select the entire image."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.reset_selected_area()

    def set_scanning_size(self, x):
        """
        Set the size of the to be scanned area (aka field of view or the size, which can be scanned with the current
        settings).

        Parameters
        ----------
        x: (float)
            size for X in meters.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_scanning_size(x - 1e-18)  # Necessary because it doesn't accept the max of the range

    def get_scanning_size(self):
        """
        Returns: (tuple of floats) x and y scanning size in meters.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_scanning_size()

    def scanning_size_info(self):
        """Returns: (dict) the scanning size unit and range."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.scanning_size_info()

    def set_ebeam_spotsize(self, spotsize):
        """
        Setting the spot size of the ebeam.
        Parameters
        ----------
        spotsize: float
            desired spotsize, unitless
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_ebeam_spotsize(spotsize)

    def get_ebeam_spotsize(self):
        """Returns: (float) the current spotsize of the electron beam (unitless)."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_ebeam_spotsize()

    def spotsize_info(self):
        """Returns: (dict) the unit and range of the spotsize. Unit is None means the spotsize is unitless."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.spotsize_info()

    def set_dwell_time(self, dwell_time):
        """

        Parameters
        ----------
        dwell_time: float
            dwell time in seconds
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_dwell_time(dwell_time)

    def get_dwell_time(self):
        """Returns: (float) the dwell time in seconds."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_dwell_time()

    def dwell_time_info(self):
        """Returns: (dict) range of the dwell time and corresponding unit."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.dwell_time_info()

    def set_ht_voltage(self, voltage):
        """
        Set the high voltage.

        Parameters
        ----------
        voltage: float
            Desired high voltage value in volt.

        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_ht_voltage(voltage)

    def get_ht_voltage(self):
        """Returns: (float) the HT Voltage in volt."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_ht_voltage()

    def ht_voltage_info(self):
        """Returns: (dict) the unit and range of the HT Voltage."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.ht_voltage_info()

    def blank_beam(self):
        """Blank the electron beam."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.blank_beam()

    def unblank_beam(self):
        """Unblank the electron beam."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.unblank_beam()

    def beam_is_blanked(self):
        """Returns: (bool) True if the beam is blanked and False if the beam is not blanked."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.beam_is_blanked()

    def pump(self):
        """Pump the microscope's chamber. Note that pumping takes some time. This is blocking."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.pump()

    def get_vacuum_state(self):
        """Returns: (string) the vacuum state of the microscope chamber to see if it is pumped or vented."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_vacuum_state()

    def vent(self):
        """Vent the microscope's chamber. Note that venting takes time (appr. 3 minutes). This is blocking."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.vent()

    def get_pressure(self):
        """Returns: (float) the chamber pressure in pascal."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_pressure()

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

    def set_channel_state(self, name, state):
        """
        Stop or start running the channel. This is non-blocking.

        Parameters
        ----------
        name: str
            name of channel.
        state: bool
            Desired state of the channel, if True set state to run, if False set state to stop.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_channel_state(name, state)

    def wait_for_state_changed(self, desired_state, name, timeout=10):
        """
        Wait until the state of the channel has changed to the desired state, if it has not changed after a certain
        timeout an error will be raised.

        Parameters
        ----------
        desired_state: XT_RUN or XT_STOP
            The state the channel should change into.
        name: str
            name of channel.
        timeout: int
            Amount of time in seconds to wait until the channel state has changed.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.wait_for_state_changed(desired_state, name, timeout)

    def get_channel_state(self, name):
        """Returns: (str) the state of the channel: XT_RUN or XT_STOP."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_channel_state(name)

    def get_free_working_distance(self):
        """Returns: (float) the free working distance in meters."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_free_working_distance()

    def set_free_working_distance(self, free_working_distance):
        """
        Set the free working distance.
        Parameters
        ----------
        free_working_distance: float
            free working distance in meters.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_free_working_distance(free_working_distance)

    def fwd_info(self):
        """Returns the unit and range of the free working distance."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.fwd_info()

    def get_fwd_follows_z(self):
        """
        Returns: (bool) True if Z follows free working distance.
        When Z follows FWD and Z-axis of stage moves, FWD is updated to keep image in focus.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_fwd_follows_z()

    def set_fwd_follows_z(self, follow_z):
        """
        Set if z should follow the free working distance. When Z follows FWD and Z-axis of stage moves, FWD is updated
        to keep image in focus.
        Parameters
        ---------
        follow_z: bool
            True if Z should follow free working distance.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_fwd_follows_z(follow_z)

    def set_autofocusing(self, name, state):
        """
        Set the state of autofocus, beam must be turned on. This is non-blocking.

        Parameters
        ----------
        name: str
            Name of one of the electron channels, the channel must be running.
        state: XT_RUN or XT_STOP
            If state is start, autofocus starts. States cancel and stop both stop the autofocusing. Some microscopes
            might need stop, while others need cancel. The Apreo system requires stop.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_autofocusing(name, state)

    def is_autofocusing(self, channel_name):
        """
        Parameters
        ----------
            channel_name (str): Holds the channels name on which the state is checked.

        Returns: (bool) True if autofocus is running and False if autofocus is not running.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.is_autofocusing(channel_name)

    def set_auto_contrast_brightness(self, name, state):
        """
        Set the state of auto contrast brightness. This is non-blocking.

        Parameters
        ----------
        name: str
            Name of one of the electron channels.
        state: XT_RUN or XT_STOP
            If state is start, auto contrast brightness starts. States cancel and stop both stop the auto contrast
            brightness.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_auto_contrast_brightness(name, state)

    def is_running_auto_contrast_brightness(self, channel_name):
        """
        Parameters
        ----------
            channel_name (str): Holds the channels name on which the state is checked.

        Returns: (bool) True if auto contrast brightness is running and False if auto contrast brightness is not
        running.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.is_running_auto_contrast_brightness(channel_name)

    def get_beam_shift(self):
        """Returns: (float) the current beam shift (DC coils position) x and y values in meters."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return tuple(self.server.get_beam_shift())

    def set_beam_shift(self, x_shift, y_shift):
        """Set the current beam shift (DC coils position) values in meters."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_beam_shift(x_shift, y_shift)

    def beam_shift_info(self):
        """Returns: (dict) the unit and xy-range of the beam shift (DC coils position)."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.beam_shift_info()

    def get_stigmator(self):
        """
        Retrieves the current stigmator x and y values. This stigmator corrects for the astigmatism of the probe shape.
        In MBSEM systems this stigmator also controls the individual probe shape within the multi-probe pattern
        (stigmator: secondary, physical position column: upper).

        Returns
        -------
        tuple, (float, float) current x and y values of stigmator, unitless.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return tuple(self.server.get_stigmator())

    def set_stigmator(self, x, y):
        """
        Sets the stigmator x and y values. This stigmator corrects for the astigmatism of the probe shape.
        In MBSEM systems this stigmator also controls the individual probe shape within the multi-probe pattern
        (stigmator: secondary, physical position column: upper).

        Parameters
        -------
        x (float): x value of stigmator, unitless.
        y (float): y value of stigmator, unitless.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_stigmator(x, y)

    def stigmator_info(self):
        """
        Returns the unit and range of the stigmator. This stigmator corrects for the astigmatism of the probe shape.
        In MBSEM systems this stigmator also controls the individual probe shape within the multi-probe pattern
        (stigmator: secondary, physical position column: upper).

        Returns
        -------
        dict, keys: "unit", "range"
        'unit': returns physical unit of the stigmator, typically None.
        'range': returns dict with keys 'x' and 'y' -> returns range of axis (tuple of length 2).
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.stigmator_info()

    def get_rotation(self):
        """Returns: (float) the current rotation value in rad."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_rotation()

    def set_rotation(self, rotation):
        """Set the current rotation value in rad."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_rotation(rotation)

    def rotation_info(self):
        """Returns: (dict) the unit and range of the rotation."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.rotation_info()

    def set_resolution(self, resolution):
        """
        Set the resolution of the image.

        Parameters
        ----------
        resolution (tuple): The resolution of the image in pixels as (width, height). Options: (768, 512),
                            (1536, 1024), (3072, 2048), (6144, 4096)
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_resolution(resolution)

    def get_resolution(self):
        """Returns the resolution of the image as (width, height)."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_resolution()

    def resolution_info(self):
        """Returns the unit and range of the resolution of the image."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.resolution_info()

    def set_beam_power(self, state):
        """
        Turn on or off the beam power.

        Parameters
        ----------
        state: bool
            True to turn on the beam and False to turn off the beam.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_beam_power(state)

    def get_beam_is_on(self):
        """Returns True if the beam is on and False if the beam is off."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_beam_is_on()

    def is_autostigmating(self, channel_name):
        """
        Parameters
        ----------
            channel_name (str): Holds the channels name on which the state is checked.

        Returns True if autostigmator is running and False if autostigmator is not running.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.is_autostigmating(channel_name)

    def set_autostigmator(self, channel_name, state):
        """
        Set the state of autostigmator, beam must be turned on. This is non-blocking.

        Parameters
        ----------
        channel_name: str
            Name of one of the electron channels, the channel must be running.
        state: XT_RUN or XT_STOP
            State is start, starts the autostigmator. States cancel and stop both stop the autostigmator, some
            microscopes might need stop, while others need cancel.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.set_autostigmator(channel_name, state)

    def get_pitch(self):
        """
        Get the pitch between two neighboring beams within the multiprobe pattern.

        Returns
        -------
        pitch: float, [um]
            The distance between two beams of the multiprobe pattern.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_pitch()

    def set_pitch(self, pitch):
        """
        Set the pitch between two beams within the multiprobe pattern.

        Returns
        -------
        pitch: float, [um]
            The distance between two beams of the multiprobe pattern.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.set_pitch(pitch)

    def pitch_info(self):
        """"Returns a dict with the 'unit' and 'range' of the pitch."""
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.pitch_info()

    def get_primary_stigmator(self):
        """
        Retrieves the current primary stigmator x and y values. Within the MBSEM system
        there are two stigmators to correct for both beamlet astigmatism as well
        as multi-probe shape. Only available on MBSEM systems.
        Note: Will be deprecated as soon as FumoBeta is refurbished.

        Returns
        -------
        tuple, (float, float) current x and y values of stigmator, unitless.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return tuple(self.server.get_primary_stigmator())

    def set_primary_stigmator(self, x, y):
        """
        Sets the primary stigmator x and y values. Within the MBSEM system
        there are two stigmators to correct for both beamlet astigmatism as well
        as multi-probe shape. Only available on MBSEM systems.
        Note: Will be deprecated as soon as FumoBeta is refurbished.

        Parameters
        -------
        x (float): x value of stigmator, unitless.
        y (float): y value of stigmator, unitless.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.set_primary_stigmator(x, y)

    def primary_stigmator_info(self):
        """"
        Returns the unit and range of the primary stigmator. Only available on MBSEM systems.
        Note: Will be deprecated as soon as FumoBeta is refurbished.

        Returns
        -------
        dict, keys: "unit", "range"
        'unit': returns physical unit of the stigmator, typically None.
        'range': returns dict with keys 'x' and 'y' -> returns range of axis (tuple of length 2).
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.primary_stigmator_info()

    def get_secondary_stigmator(self):
        """
        Retrieves the current secondary stigmator x and y values. Within the MBSEM system
        there are two stigmators to correct for both beamlet astigmatism as well
        as multi-probe shape. Only available on MBSEM systems.
        Note: Will be deprecated as soon as FumoBeta is refurbished.

        Returns
        -------
        tuple, (float, float) current x and y values of stigmator, unitless.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return tuple(self.server.get_secondary_stigmator())

    def set_secondary_stigmator(self, x, y):
        """
        Sets the secondary stigmator x and y values. Within the MBSEM system
        there are two stigmators to correct for both beamlet astigmatism as well
        as multi-probe shape. Only available on MBSEM systems.
        Note: Will be deprecated as soon as FumoBeta is refurbished.

        Parameters
        -------
        x (float): x value of stigmator, unitless.
        y (float): y value of stigmator, unitless.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.set_secondary_stigmator(x, y)

    def secondary_stigmator_info(self):
        """"
        Returns the unit and range of the secondary stigmator. Only available on MBSEM systems.
        Note: Will be deprecated as soon as FumoBeta is refurbished.

        Returns
        -------
        dict, keys: "unit", "range"
        'unit': returns physical unit of the stigmator, typically None.
        'range': returns dict with keys 'x' and 'y' -> returns range of axis (tuple of length 2).
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.secondary_stigmator_info()

    def get_pattern_stigmator(self):
        """
        Retrieves the current pattern stigmator x and y values. Only available on MBSEM systems.
        This stigmator corrects for the astigmatism of the multi-probe pattern shape
        (stigmator: primary, physical position column: lower).

        Returns
        -------
        tuple, (float, float) current x and y values of stigmator, unitless.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return tuple(self.server.get_pattern_stigmator())

    def set_pattern_stigmator(self, x, y):
        """
        Sets the pattern stigmator x and y values. Only available on MBSEM systems.
        This stigmator corrects for the astigmatism of the multi-probe pattern shape
        (stigmator: primary, physical position column: lower).

        Parameters
        -------
        x (float): x value of stigmator, unitless.
        y (float): y value of stigmator, unitless.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.set_pattern_stigmator(x, y)

    def pattern_stigmator_info(self):
        """"
        Returns the unit and range of the pattern stigmator. Only available on MBSEM systems.
        This stigmator corrects for the astigmatism of the multi-probe pattern shape
        (stigmator: primary, physical position column: lower).

        Returns
        -------
        dict, keys: "unit", "range"
        'unit': returns physical unit of the stigmator, typically None.
        'range': returns dict with keys 'x' and 'y' -> returns range of axis (tuple of length 2).
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.pattern_stigmator_info()

    def get_dc_coils(self):
        """
        Get the four values of the dc coils.

        Returns
        -------
        list of tuples of two floats, len 4
            A list of 4 tuples containing 2 values (floats) of each of the 4 dc coils, in the order:
            [x lower, x upper, y lower, y upper].
            These 4 items describe 4x2 transformation matrix for a required beam shift using DC coils.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_dc_coils()

    def get_use_case(self):
        """
        Get the current use case state. The use case reflects whether the system
        is currently in multi-beam or single beam mode.

        Returns
        -------
        state: str, 'MultiBeamTile' or 'SingleBeamlet'

        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_use_case()

    def set_use_case(self, state):
        """
        Set the current use case state. The use case reflects whether the system
        is currently in multi-beam or single beam mode.

        Parameters
        ----------
        state: str, 'MultiBeamTile' or 'SingleBeamlet'

        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.set_use_case(state)

    def get_mpp_orientation(self):
        """
        Get the current multi probe pattern orientation in degrees.

        :return (float): Multi probe orientation in degrees
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.get_mpp_orientation()

    def mpp_orientation_info(self):
        """
        Get the current multi probe pattern orientation information, contains the range and the unit (defined as
        degrees).

        :return (dict str -> list (of length 2)): The range of the rotation for the keyword "range"
        and the 'degrees' for the keyword "unit"
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.mpp_orientation_info()

    def get_aperture_index(self):
        """
        Get the current aperture index.

        :return (int): Aperture index (typical range 0 - 14)
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return int(self.server.get_aperture_index())

    def set_aperture_index(self, aperture_idx):
        """
        Set the current aperture index.

        :param aperture_idx (int): Aperture index (typical range 0 - 14)
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_aperture_index(float(aperture_idx))

    def aperture_index_info(self):
        """
        Get the current aperture index information, contains the range (typically 0 - 14).

        :return (dict str -> list (of length 2)): The range of the aperture index for the keyword "range".
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.aperture_index_info()

    def get_beamlet_index(self):
        """
        Get the current beamlet index which is represented by the two values in a grid (x,y).

        :return (tuple of ints): Beamlet index (typical range 1 - 8)
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return tuple(int(i) for i in self.server.get_beamlet_index())

    def set_beamlet_index(self, beamlet_idx):
        """
        Set the current beamlet index which is represented by the two values in a grid (x,y).

        :param beamlet_idx (tuple of ints): Beamlet index (typical range 1 - 8)
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            self.server.set_beamlet_index(*tuple(float(i) for i in beamlet_idx))

    def beamlet_index_info(self):
        """
        Get the current beamlet index information, contains the range in x and y direction (1 - 8).

        :return (dict with sub-dict, str -> list (of length 2)): The range of the beamlet index for the keyword
        "range" and in that sub dictionary either of the keywords "x"/"y".
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.beamlet_index_info()


class Scanner(model.Emitter):
    """
    This is an extension of the model.Emitter class. It contains Vigilant
    Attributes for magnification, accel voltage, blanking, spotsize, beam shift,
    rotation and dwell time. Whenever one of these attributes is changed, its
    setter also updates another value if needed.
    """

    def __init__(self, name, role, parent, hfw_nomag, **kwargs):
        model.Emitter.__init__(self, name, role, parent=parent, **kwargs)

        # will take care of executing auto contrast/brightness and auto stigmator asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        self._hfw_nomag = hfw_nomag

        dwell_time_info = self.parent.dwell_time_info()
        self.dwellTime = model.FloatContinuous(
            self.parent.get_dwell_time(),
            dwell_time_info["range"],
            unit=dwell_time_info["unit"],
            setter=self._setDwellTime)

        voltage_info = self.parent.ht_voltage_info()
        init_voltage = numpy.clip(self.parent.get_ht_voltage(), voltage_info['range'][0], voltage_info['range'][1])
        self.accelVoltage = model.FloatContinuous(
            init_voltage,
            voltage_info["range"],
            unit=voltage_info["unit"],
            setter=self._setVoltage
        )
        self.blanker = model.VAEnumerated(
            None,
            setter=self._setBlanker,
            choices={True: 'blanked', False: 'unblanked', None: 'auto'})

        spotsize_info = self.parent.spotsize_info()
        self.spotSize = model.FloatContinuous(
            self.parent.get_ebeam_spotsize(),
            spotsize_info["range"],
            unit=spotsize_info["unit"],
            setter=self._setSpotSize)

        beam_shift_info = self.parent.beam_shift_info()
        range_x = beam_shift_info["range"]["x"]
        range_y = beam_shift_info["range"]["y"]
        self.beamShift = model.TupleContinuous(
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
        rng = self.parent.resolution_info()["range"]
        self._shape = (rng["x"][1], rng["y"][1])
        # pixelSize is the same as MD_PIXEL_SIZE, with scale == 1
        # == smallest size/ between two different ebeam positions
        pxs = (self._hfw_nomag / (self._shape[0] * mag),
               self._hfw_nomag / (self._shape[0] * mag))
        self.pixelSize = model.VigilantAttribute(pxs, unit="m", readonly=True)

        # .resolution is the number of pixels actually scanned. If it's less than
        # the whole possible area, it's centered.
        resolution = self.parent.get_resolution()

        self.resolution = model.ResolutionVA(tuple(resolution),
                                             ((rng["x"][0], rng["y"][0]),
                                              (rng["x"][1], rng["y"][1])),
                                             setter=self._setResolution)
        self._resolution = resolution

        # (float, float) as a ratio => how big is a pixel, compared to pixelSize
        # it basically works the same as binning, but can be float
        # (Default to scan the whole area)
        self._scale = (self._shape[0] / resolution[0], self._shape[1] / resolution[1])
        self.scale = model.TupleContinuous(self._scale,
                                           [(1, 1), self._shape],
                                           unit="",
                                           cls=(int, float),  # int; when setting scale the GUI returns a tuple of ints.
                                           setter=self._setScale)
        self.scale.subscribe(self._onScale, init=True)  # to update metadata

        # If scaled up, the pixels are bigger
        pxs_scaled = (pxs[0] * self.scale.value[0], pxs[1] * self.scale.value[1])
        self.parent._metadata[model.MD_PIXEL_SIZE] = pxs_scaled

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
            dwell_time = self.parent.get_dwell_time()
            if dwell_time != self.dwellTime.value:
                self.dwellTime._value = dwell_time
                self.dwellTime.notify(dwell_time)
            voltage = self.parent.get_ht_voltage()
            v_range = self.accelVoltage.range
            if not v_range[0] <= voltage <= v_range[1]:
                logging.info("Voltage {} V is outside of range {}, clipping to nearest value.".format(voltage, v_range))
                voltage = self.accelVoltage.clip(voltage)
            if voltage != self.accelVoltage.value:
                self.accelVoltage._value = voltage
                self.accelVoltage.notify(voltage)
            blanked = self.parent.beam_is_blanked()
            if blanked != self.blanker.value:
                self.blanker._value = blanked
                self.blanker.notify(blanked)
            spot_size = self.parent.get_ebeam_spotsize()
            if spot_size != self.spotSize.value:
                self.spotSize._value = spot_size
                self.spotSize.notify(spot_size)
            beam_shift = self.parent.get_beam_shift()
            if beam_shift != self.beamShift.value:
                self.beamShift._value = beam_shift
                self.beamShift.notify(beam_shift)
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

    def _setScale(self, value):
        """
        value (1 < float, 1 < float): increase of size between pixels compared to
            the original pixel size. It will adapt the translation and resolution to
            have the same ROI (just different amount of pixels scanned)
        return the actual value used
        """
        prev_scale = self._scale
        self._scale = value

        # adapt resolution so that the ROI stays the same
        change = (prev_scale[0] / self._scale[0],
                  prev_scale[1] / self._scale[1])
        old_resolution = self.resolution.value
        new_resolution = (max(int(round(old_resolution[0] * change[0])), 1),
                          max(int(round(old_resolution[1] * change[1])), 1))
        self.resolution.value = new_resolution
        return value

    def _onScale(self, s):
        self._updatePixelSize()

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
        self.parent._metadata[model.MD_PIXEL_SIZE] = pxs_scaled

    def _setDwellTime(self, dwell_time):
        self.parent.set_dwell_time(dwell_time)
        return self.parent.get_dwell_time()

    def _setVoltage(self, voltage):
        self.parent.set_ht_voltage(voltage)
        return self.parent.get_ht_voltage()

    def _setBlanker(self, blank):
        """
        Parameters
        ----------
        blank (bool): True if the the electron beam should blank, False if it should be unblanked.

        Returns
        -------
        (bool): True if the the electron beam is blanked, False if it is unblanked. See Notes for edge case.

        Notes
        -----
        When pausing the stream in XT it will blank the beam and return True for the beam_is_blanked check. It is
        possible to unblank the beam when the stream is paused. The physical unblanking will then occur when the stream
        is started, and at that moment beam_is_blanked will return False. It is impossible to check if the beam will
        be unblanked or blanked when starting the stream.
        """
        if blank:
            self.parent.blank_beam()
        else:
            self.parent.unblank_beam()
        return self.parent.beam_is_blanked()

    def _setSpotSize(self, spotsize):
        self.parent.set_ebeam_spotsize(spotsize)
        return self.parent.get_ebeam_spotsize()

    def _setBeamShift(self, beam_shift):
        self.parent.set_beam_shift(*beam_shift)
        return self.parent.get_beam_shift()

    def _setRotation(self, rotation):
        self.parent.set_rotation(rotation)
        return self.parent.get_rotation()

    def _setHorizontalFoV(self, fov):
        self.parent.set_scanning_size(fov)
        fov = self.parent.get_scanning_size()[0]
        mag = self._hfw_nomag / fov
        self.magnification._value = mag
        self.magnification.notify(mag)
        self._updateDepthOfField()
        self._updatePixelSize()
        return fov

    def _updateDepthOfField(self):
        fov = self.horizontalFoV.value
        # Formula was determined by experimentation
        K = 100  # Magical constant that gives a not too bad depth of field
        dof = K * (fov / 1024)
        self.depthOfField._set_value(dof, force_write=True)

    def _setResolution(self, value):
        """
        value (0<int, 0<int): defines the size of the resolution. If the requested
            resolution is not possible, it will pick the most fitting one.
        returns the actual value used
        """
        max_size = self.resolution.range[1]

        # At least one pixel, and at most the whole area.
        size = (max(min(value[0], max_size[0]), 1),
                max(min(value[1], max_size[1]), 1))
        self._resolution = size
        self.parent.set_resolution(size)

        self.translation = model.VigilantAttribute((0, 0), unit="px", readonly=True)
        return value

    @isasync
    def applyAutoContrastBrightness(self, detector):
        """
        Wrapper for running the automatic setting of the contrast brightness functionality asynchronously. It
        automatically sets the contrast and the brightness via XT, the beam must be turned on and unblanked. Auto
        contrast brightness functionality works best if there is a feature visible in the image. This call is
        non-blocking.

        :param detector (str): Role of the detector.
        :return: Future object

        """
        # Create ProgressiveFuture and update its state
        est_start = time.time() + 0.1
        f = ProgressiveFuture(start=est_start,
                              end=est_start + 20)  # Rough time estimation
        f._auto_contrast_brighness_lock = threading.Lock()
        f._must_stop = threading.Event()  # Cancel of the current future requested
        f.task_canceller = self._cancelAutoContrastBrightness
        f._channel_name = DETECTOR2CHANNELNAME[detector]
        return self._executor.submitf(f, self._applyAutoContrastBrightness, f)

    def _applyAutoContrastBrightness(self, future):
        """
        Starts applying auto contrast brightness and checks if the process is finished for the ProgressiveFuture object.
        :param future (Future): the future to start running.
        """
        channel_name = future._channel_name
        with future._auto_contrast_brighness_lock:
            if future._must_stop.is_set():
                raise CancelledError()
            self.parent.set_auto_contrast_brightness(channel_name, XT_RUN)
            time.sleep(0.5)  # Wait for the auto contrast brightness to start

        # Wait until the microscope is no longer performing auto contrast brightness
        while self.parent.is_running_auto_contrast_brightness(channel_name):
            future._must_stop.wait(0.1)
            if future._must_stop.is_set():
                raise CancelledError()

    def _cancelAutoContrastBrightness(self, future):
        """
        Cancels the auto contrast brightness. Non-blocking.
        :param future (Future): the future to stop.
        :return (bool): True if it successfully cancelled (stopped) the move.
        """
        future._must_stop.set()  # Tell the thread taking care of auto contrast brightness it's over.

        with future._auto_contrast_brighness_lock:
            logging.debug("Cancelling auto contrast brightness")
            try:
                self.parent.set_auto_contrast_brightness(future._channel_name, XT_STOP)
                return True
            except OSError as error_msg:
                logging.warning("Failed to cancel auto brightness contrast: %s", error_msg)
                return False

    # TODO Commented out code because it is currently not supported by XT. An update or another implementation may be
    # made later

    # @isasync
    # def applyAutoStigmator(self, detector):
    #     """
    #     Wrapper for running the auto stigmator functionality asynchronously. It sets the state of autostigmator,
    #     the beam must be turned on and unblanked. This call is non-blocking.
    #
    #     :param detector (str): Role of the detector.
    #     :return: Future object
    #     """
    #     # Create ProgressiveFuture and update its state
    #     est_start = time.time() + 0.1
    #     f = ProgressiveFuture(start=est_start,
    #                           end=est_start + 8)  # rough time estimation
    #     f._auto_stigmator_lock = threading.Lock()
    #     f._must_stop = threading.Event()  # cancel of the current future requested
    #     f.task_canceller = self._cancelAutoStigmator
    #     if DETECTOR2CHANNELNAME[detector] != "electron1":
    #         # Auto stigmation is only supported on channel electron1, not on the other channels
    #         raise KeyError("This detector is not supported for auto stigmation")
    #     f.c = DETECTOR2CHANNELNAME[detector]
    #     return self._executor.submitf(f, self._applyAutoStigmator, f)
    #
    # def _applyAutoStigmator(self, future):
    #     """
    #     Starts applying auto stigmator and checks if the process is finished for the ProgressiveFuture object.
    #     :param future (Future): the future to start running.
    #     """
    #     channel_name = future._channel_name
    #     with future._auto_stigmator_lock:
    #         if future._must_stop.is_set():
    #             raise CancelledError()
    #         self.parent.set_autostigmator(channel_name, XT_RUN)
    #         time.sleep(0.5)  # Wait for the auto stigmator to start
    #
    #     # Wait until the microscope is no longer applying auto stigmator
    #     while self.parent.is_autostigmating(channel_name):
    #         future._must_stop.wait(0.1)
    #         if future._must_stop.is_set():
    #             raise CancelledError()
    #
    # def _cancelAutoStigmator(self, future):
    #     """
    #     Cancels the auto stigmator. Non-blocking.
    #     :param future (Future): the future to stop.
    #     :return (bool): True if it successfully cancelled (stopped) the move.
    #     """
    #     future._must_stop.set()  # tell the thread taking care of auto stigmator it's over
    #
    #     with future._auto_stigmator_lock:
    #         logging.debug("Cancelling auto stigmator")
    #         self.parent.set_autostigmator(future._channel_name, XT_STOP)
    #         return True


class Detector(model.Detector):
    """
    This is an extension of model.Detector class. It performs the main functionality
    of the SEM. It sets up a Dataflow and notifies it every time that an SEM image
    is captured.
    """

    def __init__(self, name, role, parent, channel_name, **kwargs):
        """
        channel_name (str): Name of one of the electron channels.
        """
        # The acquisition is based on a FSM that roughly looks like this:
        # Event\State |    Stopped    |   Acquiring    | Receiving data |
        #    START    | Ready for acq |        .       |       .        |
        #    DATA     |       .       | Receiving data |       .        |
        #    STOP     |       .       |     Stopped    |    Stopped     |
        #    TERM     |     Final     |      Final     |     Final      |

        model.Detector.__init__(self, name, role, parent=parent, **kwargs)
        self._shape = (256,)  # Depth of the image
        self.data = SEMDataFlow(self)

        self._channel_name = channel_name
        self._genmsg = queue.Queue()  # GEN_*
        self._generator = None

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
                                               name="XT acquisition thread")
            self._generator.start()

    def stop_generate(self):
        if self.parent._scanner.blanker.value is None:
            self.parent.blank_beam()
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
                    self.parent.set_channel_state(self._channel_name, True)
                    if self.parent._scanner.blanker.value is None:
                        self.parent.unblank_beam()
                    # The channel needs to be stopped to acquire an image, therefore immediately stop the channel.
                    self.parent.set_channel_state(self._channel_name, False)

                    # Estimated time for an acquisition is the dwell time times the total amount of pixels in the image.
                    n_pixels = self.parent._scanner.shape[0] * self.parent._scanner.shape[1]
                    est_acq_time = self.parent._scanner.dwellTime.value * n_pixels

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
                    # Acquire the image
                    image = self.parent.get_latest_image(self._channel_name)

                    md = self.parent._metadata.copy()
                    md[model.MD_DWELL_TIME] = self.parent._scanner.dwellTime.value
                    md[model.MD_ROTATION] = self.parent._scanner.rotation.value

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

    def stop_acquisition(self):
        """
        Stop acquiring images.
        """
        # Stopping the channel once stops it after the acquisition is done.
        self.parent.set_channel_state(self._channel_name, False)
        if self.parent.get_channel_state(self._channel_name) == XT_STOP:
            return
        else:  # Channel is canceling
            logging.debug("Channel not fully stopped will try again.")
            time.sleep(0.5)
            # Stopping it twice does a full stop.
            self.parent.set_channel_state(self._channel_name, False)

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
        while self.parent.get_channel_state(self._channel_name) != XT_STOP:
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
        sem (model.Emitter): the SEM
        channel_name (str): Name of one of the electron channels
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

        # Refresh regularly the position
        self._pos_poll = util.RepeatingTimer(5, self._refreshPosition, "Stage position polling")
        self._pos_poll.start()

    def _updatePosition(self, raw_pos=None):
        """
        update the position VA
        raw_pos (dict str -> float): the position (as received from the SEM). If None is passed the current position is
            requested from the SEM.
        """
        pos = raw_pos if raw_pos else self._getPosition()
        self.position._set_value(self._applyInversion(pos), force_write=True)

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
        return pos

    def _moveTo(self, future, pos, timeout=60):
        with future._moving_lock:
            try:
                if future._must_stop.is_set():
                    raise CancelledError()
                logging.debug("Moving to position {}".format(pos))
                if "rx" in pos.keys():
                    pos["t"] = pos.pop("rx")
                if "rz" in pos.keys():
                    pos["r"] = pos.pop("rz")
                self.parent.move_stage(pos, rel=False)
                time.sleep(0.5)

                # Wait until the move is over.
                # Don't check for future._must_stop because anyway the stage will
                # stop moving, and so it's nice to wait until we know the stage is
                # not moving.
                moving = True
                tstart = time.time()
                while moving:
                    pos = self._getPosition()
                    moving = self.parent.stage_is_moving()
                    # Take the opportunity to update .position
                    self._updatePosition(pos)

                    if time.time() > tstart + timeout:
                        self.parent.stop_stage_movement()
                        logging.error("Timeout after submitting stage move. Aborting move.")
                        break

                    # Wait for 50ms so that we do not keep using the CPU all the time.
                    time.sleep(50e-3)

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
        pos = self._getPosition()
        for k, v in shift.items():
            pos[k] += v

        target_pos = self._applyInversion(pos)
        # Check range (for the axes we are moving)
        for an in shift.keys():
            rng = self.axes[an].range
            if rng == (0, 2 * math.pi) and an in ("rx", "rz"):
                pos[an] = pos[an] % (2 * math.pi)
                target_pos[an] = target_pos[an] % (2 * math.pi)
            p = target_pos[an]
            if not rng[0] <= p <= rng[1]:
                raise ValueError("Relative move would cause axis %s out of bound (%g m)" % (an, p))

        self._moveTo(future, pos)

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
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        shift = self._applyInversion(shift)

        f = self._createFuture()
        f = self._executor.submitf(f, self._doMoveRel, f, shift)
        return f

    def _doMoveAbs(self, future, pos):
        self._moveTo(future, pos)

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

        :param detector (str): Role of the detector.
        :param state (str):  "run", or "stop"
        :return: Future object
        """
        # Create ProgressiveFuture and update its state
        est_start = time.time() + 0.1
        f = ProgressiveFuture(start=est_start,
                              end=est_start + 11)  # rough time estimation
        f._autofocus_lock = threading.Lock()
        f._must_stop = threading.Event()  # cancel of the current future requested
        f.task_canceller = self._cancelAutoFocus
        f._channel_name = DETECTOR2CHANNELNAME[detector]
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
        z = self.parent.get_free_working_distance()
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
            foc += self.parent.get_free_working_distance()
            self.parent.set_free_working_distance(foc)
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
