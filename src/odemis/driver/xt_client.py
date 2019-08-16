#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Created on 16 Aug 2019

@author: Thera Pals

Copyright © 2019 Thera Pals, Delmic

This file is part of Delmic Acquisition Software.

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

import msgpack_numpy as mn
import zerorpc

mn.patch()

# Necessary, otherwise nothing at all will be shown
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s")
logging.getLogger().setLevel(logging.INFO)


class MicroscopeClient:
    """
    Class to communicate with a Microscope server via the ZeroRPC protocol.
    """

    def __init__(self, server_address="tcp://192.168.1.1:4242", timeout=30):
        """
        Parameters
        ----------
        server_address: str
            server address of the Microscope server as a string.
        timeout: int
            Time in seconds the client should wait for a response from the server.
        """
        # set heartbeat to None on client and server side, otherwise after two missed heartbeats the client thinks the
        # connection is lost. A heartbeat happens every 5 seconds, when a function takes longer than 5 seconds to
        # respond a heartbeat is skipped. timeout controls how long a call can take to respond, the default is 30
        # seconds.
        self.client = zerorpc.Client(server_address, heartbeat=None, timeout=timeout)

    def list_available_channels(self):
        """List all available channels and their current state as a dict."""
        available_channels = self.client.list_available_channels()
        return available_channels

    def move_stage(self, position, rel=False):
        """
        Move the stage the given position in meter. This is non-blocking.

        Parameters
        ----------
        position: dict(string->float)
            Absolute or relative position to move the stage to per axes in m. Axes are 'x' and 'y'.
        rel: boolean
            If True the staged is moved relative to the current position of the stage, by the distance specified in
            position. If false the stage is moved to the absolute position.
        """
        self.client.move_stage(position, rel)

    def stage_is_moving(self):
        """Returns True if the stage is moving and False if the stage is not moving."""
        return self.client.stage_is_moving()

    def stop_stage_movement(self):
        """Stop the movement of the stage."""
        self.client.stop_stage_movement()

    def get_stage_position(self):
        """
        Returns
        -------
        position: dict
            The current position of the stage.
        """
        return self.client.get_stage_position()

    def stage_info(self):
        """Returns the unit and range of the stage position."""
        return self.client.stage_info()

    def acquire_image(self, channel_name='electron1'):
        """
        Acquire an image observed via the currently set channel. Note: the channel needs to be stopped before an image
        can be acquired. To acquire multiple consecutive images the channel needs to be started and stopped. This
        causes the acquisition speed to be approximately 1 fps.

        Returns
        -------
        image: numpy array
            The acquired image.
        """
        return self.client.acquire_image(unicode(channel_name))

    def set_scan_mode(self, mode):
        """
        Set the scan mode.
        Parameters
        ----------
        mode: str
            Name of desired scan mode, one of: unknown, external, full_frame, spot, or line.
        """
        self.client.set_scan_mode(unicode(mode))

    def set_selected_area(self, start_position, size):
        """
        Specify a selected area in the scan field area.

        Parameters
        ----------
        start_position: (tuple of int)
            (x, y) of where the area starts in pixel, (0,0) is at the top left (checked for Apreo).
        size: (tuple of int)
            (width, height) of the size in pixel.
        """
        self.client.set_selected_area(start_position, size)

    def get_selected_area(self):
        """
        Returns the current selected area in x, y, width, height in pixels.
        If selected area is not active it returns the stored selected area.
        """
        x, y, width, height = self.client.get_selected_area()
        return x, y, width, height

    def selected_area_info(self):
        """Returns the unit and range of set selected area."""
        return self.client.selected_area_info()

    def reset_selected_area(self):
        """Reset the selected area to select the entire image."""
        self.client.reset_selected_area()

    def set_scanning_size(self, size):
        """
        Set the size of the to be scanned area (aka field of view or the size, which can be scanned with the current
        settings).

        Parameters
        ----------
        size: (float)
            size for X in [m]. Y is always X * 442/512
        """
        self.client.set_scanning_size(size)

    def get_scanning_size(self):
        """
        Returns
        -------
        scanning_size: dict
            Dictionary containing the 'x' and 'y' scanning size in [m].
        """
        x, y = self.client.get_scanning_size()
        return x, y

    def scanning_size_info(self):
        """Returns the scanning size unit and range."""
        return self.client.scanning_size_info()

    def set_ebeam_spotsize(self, spotsize):
        """
        Setting the spot size of the ebeam.
        Parameters
        ----------
        spotsize: float
            desired spotsize, unitless [-]
        """
        self.client.set_ebeam_spotsize(spotsize)

    def get_ebeam_spotsize(self):
        """Get the current spotsize of the electron beam."""
        return self.client.get_ebeam_spotsize()

    def spotsize_info(self):
        """Returns the unit and range of the spotsize. Unit is None means the spotsize is unitless."""
        return self.client.spotsize_info()

    def set_dwell_time(self, dwell_time):
        """

        Parameters
        ----------
        dwell_time: float
            dwell time in seconds
        """
        return self.client.set_dwell_time(dwell_time)

    def get_dwell_time(self):
        """Returns the dwell time in seconds."""
        return self.client.get_dwell_time()

    def dwell_time_info(self):
        """Returns the unit and range of the dwell time."""
        return self.client.dwell_time_info()

    def set_ht_voltage(self, voltage):
        """
        Set the high voltage.

        Parameters
        ----------
        voltage: float
            Desired high voltage value in volt [V].

        """
        self.client.set_ht_voltage(voltage)

    def get_ht_voltage(self):
        """Get the HT Voltage in [V]."""
        return self.client.get_ht_voltage()

    def ht_voltage_info(self):
        """Returns the unit and range of the HT Voltage."""
        return self.client.ht_voltage_info()

    def blank_beam(self):
        """Blank the electron beam."""
        self.client.blank_beam()

    def unblank_beam(self):
        """Unblank the electron beam."""
        self.client.unblank_beam()

    def beam_is_blanked(self):
        """Returns True if the beam is blanked and False if the beam is not blanked."""
        return self.client.beam_is_blanked()

    def pump(self):
        """Pump the microscope's chamber. Note that pumping takes some time. This blocking."""
        self.client.pump()

    def get_vacuum_state(self):
        """Get the vacuum state of the microscope chamber to see if it is pumped or vented."""
        return self.client.get_vacuum_state()

    def vent(self):
        """Vent the microscope's chamber. Note that venting takes time (appr. 3 minutes). This is blocking."""
        self.client.vent()

    def get_pressure(self):
        """Get the chamber pressure in [Pa]."""
        return self.client.get_pressure()

    def home_stage(self):
        """Home stage asynchronously. This is non-blocking."""
        self.client.home_stage()

    def is_homed(self):
        """Returns True if the stage is homed and False otherwise."""
        return self.client.is_homed()

    def get_channel(self, name='electron1'):
        """Get an xtlib channel object."""
        return self.client.get_channel(unicode(name))

    def set_channel_state(self, name='electron1', state='run'):
        """
        Stop or start running the channel. This is non-blocking.

        Parameters
        ----------
        name: str
            name of channel.
        state: "run" or "stop"
            desired state of the channel.
        """
        self.client.set_channel_state(unicode(name), unicode(state))

    def wait_for_state_changed(self, desired_state, name='electron1', timeout=10):
        """Wait for channel state to be changed, if it's not changed after 10 seconds break."""
        self.client.wait_for_state_changed(unicode(desired_state), unicode(name), timeout)

    def get_channel_state(self, name='electron1'):
        """Return the state of the channel: run, stop or cancel."""
        return self.client.get_channel_state(unicode(name))

    def get_free_working_distance(self):
        """Get the free working distance in [m]."""
        return self.client.get_free_working_distance()

    def set_free_working_distance(self, free_working_distance):
        """Set the free working distance in [m]."""
        self.client.set_free_working_distance(free_working_distance)

    def get_fwd_follows_z(self):
        """
        Returns True if Z follows free working distance functionality is turned on.
        When Z follows FWD functionality is switched on and Z stage axis moves, FWD is updated to keep image in focus.
        """
        return self.client.get_fwd_follows_z()

    def set_fwd_follows_z(self, follow_z):
        """
        Parameters
        ---------
        follow_z: bool
            True if Z follows free working distance functionality should be turned on.
        """
        self.client.set_fwd_follows_z(follow_z)

    def set_autofocusing(self, channel, state):
        """
        Set the state of autofocus, beam must be turned on. This is non-blocking.

        Parameters
        ----------
        channel: xtlib channel object
            One of the electron channels, the channel must be running.
        state: "start", "cancel" or "stop"
            State is start, starts the autofocus. States cancel and stop both stop the autofocusing, some microscopes
            might need stop, while others need cancel.
        """
        self.client.set_autofocusing(channel, unicode(state))

    def is_autofocusing(self):
        """Returns True if autofocus is running and False if autofocus is not running."""
        return self.client.is_autofocusing()

    def get_beam_shift(self):
        """Retrieves the current beam shift x and y values in meter [m]."""
        return self.client.get_beam_shift()

    def set_beam_shift(self, x_shift, y_shift):
        """Sets the current beam shift values in meter [m]."""
        self.client.set_beam_shift(x_shift, y_shift)

    def beam_shift_info(self):
        """Returns the unit and xy-range of the beam shift."""
        return self.client.beam_shift_info()
