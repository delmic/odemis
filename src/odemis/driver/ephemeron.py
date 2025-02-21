# -*- coding: utf-8 -*-
"""
Created on 24 April 2024

@author: Stefan Sneep

Copyright © 2024-2025 Stefan Sneep & Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the
GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty
of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis.
If not, see http://www.gnu.org/licenses/.

"""
# This driver is developed for communication with the Ephemeron MightyEBIC controller API.
# The MightyEBIC scan controller will acquire a signal synchronized on the pixel signal
# from the e-beam scanner. Eventually, the complete signal of the frame is sent digitally to
# Odemis.

import asyncio
import logging
import math
import re
import threading
import time
import queue
from asyncio import AbstractEventLoop
from functools import wraps
from typing import Optional, Tuple, Dict, Any, List, Coroutine

from odemis import model
from odemis.model import Detector, oneway, MD_ACQ_DATE, HwError

import numpy
from asyncua import Client, Server, Node, ua
from asyncua.common.methods import uamethod
from asyncua.common.statemachine import State, StateMachine
from asyncua.ua import NodeId, ObjectIds, LocalizedText, Argument

# Don't use too verbose logging for asyncua, otherwise it's really an explosion
logging.getLogger("asyncua").setLevel(logging.WARNING)

# OPCUA StateMachine constants
STATE_NAME_IDLE = "Idle"
STATE_NAME_BUSY = "Busy"
STATE_NAME_TRIGGER = "Trigger"
STATE_NAME_ERROR = "Error"

# MightyEBIC driver constants
MAX_SAMPLES_PER_PIXEL = 16
MAX_NUMBER_OF_CHANNELS = 8
OVERSAMPLING_VALUES = (0, 2, 4, 8, 16, 32, 64)  # Ordered from smallest to largest
MIN_RESOLUTION = (100, 16)  # Not clear how small is "too small"... short lines (in time) are not supported
MAX_RESOLUTION = (4096, 4096)  # TODO: check the MightyEBIC

NAMESPACE_INDEX = 0
NAMESPACE_ADDRESS = "http://opcfoundation.org/UA/"
SIMULATED_URL = "opc.tcp://localhost:4840/mightyebic/server/"
EBIC_CONTROLLER_NODE = "MightyEBICController"
EBIC_STATE_NODE = "MightyEBICState"
EBIC_INFO_NODE = "MightyEBICInfo"

# Extra time to give to the scan, in addition to the expect scan time. This is mainly to account for
# the time between the device is ready and the e-beam scanner starts (typically a few seconds).
# Also accounts for the time it takes to report the scan is complete (should take less than a few seconds).
SCAN_EXTRA_TIMEOUT = 60  # s


class MightyEBIC(Detector):
    def __init__(self, name: str, role: str, channel: int, url: str, **kwargs):
        """
        Initialise the EBIC controller
        :param name: The name of the device configured through the configuration file.
        :param role: The role of the device configured through the configuration file.
        :param channel: The channel from which the device read the data (starts from 0)
        :param url: The url address to use with the OPC UA protocol.
            Example of such an url might be opc.tcp://192.168.50.2:4840/mightyebic/server/
            Note: Pass "fake" to use a simulator.
        """
        super().__init__(name, role, **kwargs)

        self._channel = channel
        if channel < 0 or channel >= MAX_NUMBER_OF_CHANNELS:
            raise ValueError(f"Invalid channel number {channel}, should be between 0 and {MAX_NUMBER_OF_CHANNELS - 1}.")
        if channel > 0:
            # The server only supports sending the first N channels, not just a specific one.
            # So, if the channel is not 0, extra data will be acquired, sent, and discarded.
            logging.warning("Using channel > 0 is inefficient, consider switching the signal to the first channel")

        # server_sim is only used for simulation of the opcServer if it is non-existent
        self._opc_server_sim: Optional[MightyEBICSimulator] = None

        if url == "fake":
            # if the device should be simulated, start a simulated server first
            self._url = SIMULATED_URL
            self._opc_server_sim = MightyEBICSimulator(self._url, self)
        else:
            url_check = r"^opc\.tcp://(localhost|(\d{1,3}\.){3}\d{1,3}):[0-9]*/"
            if not re.search(url_check, url):
                raise ValueError(f"The url {url} to connect to is not in the right format."
                                 "Should be like opc.tcp://192.168.50.2:4840/mightyebic/server/ .")
            self._url = url

        try:
            self._opc_client = MightyEBICUaClient(self._url, timeout=10, component=self)
        except OSError as ex:
            raise HwError(f"Failed to connect to MightyEBIC computer, check connection: {ex}")

        # TODO: is there a need to support multiple channels? If so, we would need to either
        # provide multiple Detectors, each with a DataFlow, or provide multiple DataFlows on this
        # single Detector. Needs to be decided... For now, we only support one channel.
        # Use a float for the depth, to indicate it returns data in floating point format.
        self._shape = MAX_RESOLUTION + (2.0 ** 64,)
        self.resolution = model.ResolutionVA(MIN_RESOLUTION, (MIN_RESOLUTION, MAX_RESOLUTION))

        dt_min = self._opc_client.calculate_dwell_time(oversampling=0, channels=self._channel + 1, spp=1, delay=0)
        # The maximum dwell time depends on the delay, which could be arbitrary large, but for now
        # we don't allow the user to change the delay, so it's easy.
        dt_max = self._opc_client.calculate_dwell_time(oversampling=max(OVERSAMPLING_VALUES),
                                                       channels=self._channel + 1,
                                                       spp=MAX_SAMPLES_PER_PIXEL,
                                                       delay=0)
        self.dwellTime = model.FloatContinuous(dt_min, (dt_min, dt_max), unit="s",
                                               setter=self.on_dwell_time_change)

        self.data = EBICDataFlow(self)
        self._acquisition_thread: Optional[threading.Thread] = None
        self._acquisition_lock = threading.Lock()
        self._acquisition_must_stop = threading.Event()

        # Special event to request software unblocking on the scan
        self.softwareTrigger = model.Event()
        self._metadata[model.MD_DET_TYPE] = model.MD_DT_NORMAL
        self._swVersion = self._opc_client.get_version()
        self._metadata[model.MD_SW_VERSION] = self._swVersion

    def terminate(self):
        self.stop_acquire()  # Just in case acquisition was running
        self._wait_acquisition_stopped()

        if self._opc_server_sim:
            self._opc_server_sim.terminate()
        self._opc_client.terminate()
        super().terminate()

    def start_acquire(self):
        with self._acquisition_lock:
            self._wait_acquisition_stopped()
            self._acquisition_thread = threading.Thread(target=self._acquire_thread,
                                                        name="EBIC acquisition thread")
            self._acquisition_thread.start()

    def stop_acquire(self):
        """ Stops the current running acquisition, should have no effect when there is none. """
        with self._acquisition_lock:
            self._acquisition_must_stop.set()

    def _wait_acquisition_stopped(self):
        """ Waits until the acquisition thread is fully finished _if_ it was requested to stop. """
        if self._acquisition_must_stop.is_set():
            logging.debug("Waiting for thread to stop.")
            if self._acquisition_thread is None:
                return
            self._acquisition_thread.join(10)  # 10s timeout for safety
            if self._acquisition_thread.is_alive():
                logging.exception("Failed to stop the acquisition thread")
                # Now let's hope everything is back to normal...
            # ensure it's not set, even if the thread died prematurely
            self._acquisition_must_stop.clear()

    def _acquire_thread(self) -> None:
        """
        Initiate a scan on the EBIC scan controller, before the scan starts the required data is gathered
        and a co-routine is initiated within the ua_client. The scan time, with an overhead of 60 seconds
        determine the time-out of the scan. After the method is initiated the scan controller waits for the
        pixel signal of the scanner, to trigger the beginning of the scan.
        """
        try:
            self.data._waitSync()
            res = self.resolution.value
            delay = 0
            md = self.getMetadata()

            act_dt, spp, os = self._opc_client.guess_samples_per_pixel_and_oversampling(self.dwellTime.value, self._channel + 1, 0)
            md[model.MD_DWELL_TIME] = act_dt
            md[model.MD_INTEGRATION_COUNT] = os * spp
            scan_time = self._opc_client.calculate_scan_time(act_dt, res[0], res[1])
            end_time = time.time() + SCAN_EXTRA_TIMEOUT + scan_time

            if self._opc_client.controller_state != STATE_NAME_IDLE:
                logging.warning("Scan controller is not idle (%s), will stop the current scan",
                                self._opc_client.controller_state)
                self._opc_client.stop_scan()
                time.sleep(0.1)
                while self._opc_client.controller_state != STATE_NAME_IDLE:
                    logging.warning("Waiting longer for controller to become idle, currently %s",
                                    self._opc_client.controller_state)
                    if self._acquisition_must_stop.wait(0.1):
                        self._opc_client.stop_scan()
                        return

            # The timeout here defines how long the scan controller will wait for the trigger signal,
            # which we have no idea. However, typically, in the use cases, it should be almost immediate (< 1s)
            # To be certain to handle every case, we still a large timeout.
            self._opc_client.start_trigger_scan(os, self._channel + 1, spp, delay, res[0], res[1], timeout=60)
            earliest_start = time.time()
            # State is supposed to change immediately to "Trigger", and back to "Idle" when the scan
            # is finished.

            state = self._opc_client.controller_state
            logging.debug("Acquisition started, state = %s, expected scan time = %s s, will wait for up to %s seconds",
                          state, scan_time, SCAN_EXTRA_TIMEOUT + scan_time)
            # TODO: is it helpful to not expect the state to be "Busy" immediately?
            if self._acquisition_must_stop.wait(0.1):
                self._opc_client.stop_scan()
                return

            # while the scan controller is "trigger", check for a time-out or stop signal
            while self._opc_client.controller_state in (STATE_NAME_BUSY, STATE_NAME_TRIGGER):
                if time.time() > end_time:
                    # Timeout => give up the acquisition
                    self._opc_client.stop_scan()
                    raise TimeoutError("Acquisition not ended after %s seconds" % (SCAN_EXTRA_TIMEOUT + scan_time,))
                if self._acquisition_must_stop.wait(0.1):
                    self._opc_client.stop_scan()
                    return

            # It shouldn't happen, but good to detect some odd trigger issue
            while time.time() < earliest_start + scan_time:
                logging.warning("Waiting longer for the scan to really finish, state is %s",
                                self._opc_client.controller_state)
                # Wait for the scan to be finished, but not too long
                if self._acquisition_must_stop.wait(0.1):
                    self._opc_client.stop_scan()
                    return

            logging.debug("Scan completed (state = %s), will receive data", self._opc_client.controller_state)

            # MD_ACQ_DATE should contain the time when the acquisition started, and some part of Odemis
            # will get upset if it's before receiving the (hardware) trigger. However, we don't really
            # know when the trigger has been received. So, compute the acquisition date in two ways:
            # when we asked to start, and retroactively, when the scan stopped minus the scan time.
            # In theory the second time should be always latest, but in case the scan was shorter
            # than expected, we'll take the latest of the two.
            latest_start = time.time() - scan_time
            if earliest_start > latest_start:
                logging.warning("Acquisition ended in %s s, which is less than scan time %g s.",
                                time.time() - earliest_start, scan_time)
                md[MD_ACQ_DATE] = earliest_start
            else:
                md[MD_ACQ_DATE] = latest_start

            # get the data from the last acquisition and notify subscribers
            das = self.read_data(res, self._channel + 1, md)
            da = das[self._channel]  # Only keep the channel we are interested in
            self.data.notify(da)
        except Exception:
            logging.exception("Unexpected failure during acquisition")
        finally:
            logging.debug("Acquisition thread closed")

    def read_data(self, resolution: Tuple[int, int], channels: int, md: Dict[str, Any]) -> List[model.DataArray]:
        """
        After a successful scan data generated by the scan controller should be available for retrieval.
        :param resolution: The used resolution (X, Y in pixels) for the scan.
        :param channels: (>= 1) The number of channels which the data should have
        :param md: The metadata to be added to the raw data.
        :return: a series of DataArray, containing the data acquired for each channel.
        """
        assert resolution[0] >= 1 and resolution[1] >= 1
        assert channels >= 1

        result_shape = self._opc_client.get_scan_result_shape()  # X, Y, C
        if result_shape != (resolution[0], resolution[1], channels):
            logging.warning("Expected a result of shape %s, but reported to be %s",
                            (resolution[0], resolution[1], channels), result_shape)

        logging.debug("Will read data, with expected shape %s", result_shape)
        raw_data = self._opc_client.get_scan_result()

        # reshape the data: it's in the order X, Y, C, but we want the (numpy) conventional C, Y, X
        raw_arr = numpy.array(raw_data)
        try:
            raw_arr.shape = result_shape  # XYC
            raw_arr = numpy.moveaxis(raw_arr, [0, 1, 2], [2, 1, 0])
        except ValueError as ex:
            # TODO: if the (expected) resolution is different from result_shape, try with the expected res?
            logging.error("Data shape %s does not match the expected %s: %s",
                          raw_arr.shape, result_shape, ex)
            # return the data anyway, which might be more useful than nothing, but 1D to make it clearer
            # the shape is unknown.
            return [model.DataArray(raw_arr, md)]

        # separate along the first dimension (channels)
        das = [model.DataArray(channel_data, md) for channel_data in raw_arr]
        return das

    def on_dwell_time_change(self, value: float) -> float:
        """
        Called when the dwell time is changed, the value is checked and a value compatible with the
        hardware and *smaller* or equal to the requested value is returned.
        :param value: request dwell time (s)
        :return: accepted dwell time (s)
        """
        # Find the closest SPP & oversampling that matches the requested dwell time. It always returns a smaller or equal value,
        # unless a value smaller than the minimum is requested (in which case it returns the minimum)
        dt, spp, os = self._opc_client.guess_samples_per_pixel_and_oversampling(value, self._channel + 1, 0)
        if not value * 0.9 <= dt <= value: # 10% tolerance as a rule-of-thumb (it does happen sometimes)
            logging.warning(f"Requested dwell time {value} differs from calculated dwell time {dt}, "
                            f"with {self._channel + 1} channels, using SPP {spp} and oversampling {os}.")
        return dt


class EBICDataFlow(model.DataFlow):
    def __init__(self, detector):
        """
        detector (ephemeron.MightyEBIC): the detector that the dataflow corresponds to
        """
        model.DataFlow.__init__(self)
        self._detector = detector

        self._sync_event = None  # event to be synchronised on, or None
        self._evtq = None  # a Queue to store received events (= float, time of the event)

    # start/stop_generate are _never_ called simultaneously (thread-safe)
    def start_generate(self):
        try:
            self._detector.start_acquire()
        except ReferenceError:
            # sem/component has been deleted, it's all fine, we'll be GC'd soon
            pass

    def stop_generate(self):
        try:
            self._detector.stop_acquire()
            # Note that after that acquisition might still go on for a short time
        except ReferenceError:
            # sem/component has been deleted, it's all fine, we'll be GC'd soon
            pass

    def synchronizedOn(self, event):
        """
        Synchronize the acquisition on the given event. Every time the event is triggered, the scanner will
        start a new acquisition/scan. The DataFlow can be synchronized only with one Event at a time.
        However, each DataFlow can be synchronized, separately. The scan will only start once each active
        DataFlow has received an event.
        event (model.Event or None): event to synchronize with. Use None to disable synchronization.
        """
        super().synchronizedOn(event)

        if self._sync_event == event:
            return
        if self._sync_event:
            self._sync_event.unsubscribe(self)
            if not event:
                self._evtq.put(None)  # in case it was waiting for this event
        self._sync_event = event
        if self._sync_event:
            # if the df is synchronized, the subscribers probably don't want to
            # skip some data
            self._evtq = queue.Queue()  # to be sure it's empty
            self._sync_event.subscribe(self)

    @oneway
    def onEvent(self):
        """
        Called by the Event when it is triggered
        """
        if not self._evtq.empty():
            logging.warning("Received synchronization event but already %d queued",
                            self._evtq.qsize())
        self._evtq.put(time.time())

    def _waitSync(self):
        """
        Block until the Event on which the dataflow is synchronised has been received.
        If the DataFlow is not synchronised on any event, this method immediately returns.
        """
        if self._sync_event:
            self._evtq.get()


class MightyEBICUaClient:
    """
    Represents the MightyEBIC, connected over OPC-UA.
    Note: it seems the default of the OPC-UA server is to limit messages to 100Mb, which is just enough
    to pass one (float) array at 4096x3072. If multiple channels are passed, then the message length
    is increased proportionally. So one need to make sure that the server uses a higher limit size
    if a higher resolution is used, or the EBIC channel is > 0.
    """
    def __init__(self, url: str, timeout: float, component: MightyEBIC):
        """
        :param url: url of the server. Example: "opc.tcp://192.168.50.2:4840/mightyebic/server/"
        :param timeout: Maximum time to wait for a request sent to the server before failing (in s)
        :param component: The component that uses this client.
        """
        self.client = Client(url=url, timeout=timeout)
        self._ebic_info_node: Optional[Node] = None
        self._ebic_state_node: Optional[Node] = None
        self._ebic_controller_node: Optional[Node] = None
        self._loop: Optional[AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._prev_state: Optional[str] = None
        self._component = component

        # Create an event loop to run the asyncio calls (aka "coroutines")
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop_thread = threading.Thread(target=self.run_event_loop, daemon=True)
        self._loop_thread.start()

        # Connect to the server, via the event loop
        f = asyncio.run_coroutine_threadsafe(self._initialize_client(), self._loop)
        f.result()
        logging.debug("OPCUA client initialized")

    def terminate(self):
        if self.client is None:  # already terminated
            return

        asyncio.run_coroutine_threadsafe(self.client.disconnect(), self._loop).result()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join()
        self.client = None

    async def _initialize_client(self):
        """ Lookup all the necessary OPCUA nodes. """
        await self.client.connect()
        logging.info("OPCUA connection established")

        objects_node = await self.client.nodes.root.get_child(f"{NAMESPACE_INDEX}:Objects")
        state_node = await objects_node.get_child(f"{NAMESPACE_INDEX}:{EBIC_STATE_NODE}")
        self._ebic_info_node = await objects_node.get_child(f"{NAMESPACE_INDEX}:{EBIC_INFO_NODE}")  # needed
        self._ebic_controller_node = await objects_node.get_child(f"{NAMESPACE_INDEX}:{EBIC_CONTROLLER_NODE}")
        self._ebic_state_node = await state_node.get_child(f"{NAMESPACE_INDEX}:CurrentState")

        # Note: it could be tempting to use a "state change" subscription, but in practice it seems
        # to be implemented as a polling mechanism, which is equivalent to what we already do...
        # but less good because the polling would happen constantly, at a low frequency, instead of
        # only doing it while waiting for the acquisition to complete.
        # This is done this way:
        # handler = StateChangeHandler()
        # subscription = await self.client.create_subscription(500, handler)
        # await subscription.subscribe_data_change([self._ebic_state_node])
        # self._subscription = subscription  # keep a reference to avoid it being garbage collected
        #
        # class StateChangeHandler:
        #     """
        #     Used to handle subscriptions to data changes (see asyncua.DataChangeNotificationHandlerAsync)
        #     """
        #     def datachange_notification(self, node: Node, val: LocalizedText, data: "DataChangeNotification"):
        #         """
        #         Callback when the EBIC server state changes (ie, start/stops acquiring)
        #         """
        #         logging.debug("Controller state notification %r %s", node, val)

    def run_event_loop(self):
        """ the command run_forever() has to be set in a separate thread due to its blocking nature. """
        try:
            self._loop.run_forever()
        except Exception:
            logging.exception("Event loop stopped unexpectedly")
        finally:
            logging.debug("Event loop ended")

    def retry_on_connection_error(coro):
        """"
        Decorator for coroutines, which detects the OPC-UA connection failed,
        and automatically attempts to reconnect, and calls again the coroutine.
        """
        # Note, asyncua.Client has a ".connection_lost_callback", which is called when the connection
        # is lost. However, it's not clear how it could be used to automatically reconnect... and
        # retry the call.

        @wraps(coro)
        async def wrapper(self, *args, **kwargs):
            try:
                return await coro(self, *args, **kwargs)
            except ConnectionError:
                logging.error("Connection to the MightyEBIC server lost, trying to reconnect.")
                self._component.state._set_value(HwError("MightyEBIC disconnected"), force_write=True)
                try:
                    await self.client.disconnect()  # Safe to call even if not connected
                    await self._initialize_client()
                    self._component.state._set_value(model.ST_RUNNING, force_write=True)
                except Exception:
                    logging.exception("Failed to reconnect to the MightyEBIC server.")
                    raise
                # Try again
                return await coro(self, *args, **kwargs)
        return wrapper

    # Not a function directly provided by the OPC-UA server, but as it does many calls to the server,
    # it's more efficient to have them all in one function, instead of having to request the event
    # loop to schedule every call to _calculate_dwell_time().
    def guess_samples_per_pixel_and_oversampling(self, req_dt: float, channels: int, delay: float
                                                 ) -> Tuple[float, int, int]:
        """

        Compute the best samples per pixel (SPP) and oversampling rate.
        "samples per pixel" is the number of consecutive measurements corresponding to the same pixel,
        which will be averaged in the result.
        "oversampling rate" is essentially the same, but it is done at a lower level, and so is more
        efficient, but has a limited set of values possible: {0, 2, 4, 8, 16, 32, 64}.
        It will return a dwell time shorter or equal to the requested dwell time, unless the requested
        dwell time is below the minimum dwell time, in which case the minimum dwell time is returned.
        :param req_dt: The requested dwell time in seconds.
        :param channels: The number of channels to acquire (1 to 8)
        :param delay: The delay between the trigger and the start of the acquisition (s)
        :return:
            dt: actual dwell time accepted (s)
            spp: number of samples per pixel for reaching the dwell time
            osr: oversampling rate needed
        """
        f = asyncio.run_coroutine_threadsafe(
            self._guess_samples_per_pixel_and_oversampling(req_dt, channels, delay),
            self._loop)
        return f.result()

    async def _guess_samples_per_pixel_and_oversampling(self, req_dt: float, channels: int, delay: float
                                                        ) -> Tuple[float, int, int]:
        """
        See guess_samples_per_pixel_and_oversampling() for info.
        This is the actual implementation, which is asynchronous, as it calls coroutines.
        """
        # Try every oversampling rate (OSR) , and for each of them find the best Sample per pixel (SPP).
        # Pick the best combination of SPP x OSR, offering the largest dwell time below the requested dwell time.

        dt_to_params = {}
        for osr in OVERSAMPLING_VALUES:
            dt, spp = await self._guess_samples_per_pixel(req_dt, osr, channels, delay)
            dt_to_params[dt] = (spp, osr)
            if spp == 1 and dt >= req_dt:
                # Increasing the oversampling will not allow to have a shorter dwell time, so stop early
                break

        # Pick the best one: the largest, below the requested dwell time (and the largest OSR)
        try:
            best_dt = max(dt for dt in dt_to_params if dt <= req_dt)
            spp, osr = dt_to_params[best_dt]
            # If there are several dwell times with the same value for spp * osr, pick the largest osr
            # (which might be slightly short dwell time, but will be more efficient)
            int_counts = spp * osr
            dt_same_counts = [dt for dt, (spp, osr) in dt_to_params.items() if spp * osr == int_counts]
            best_dt = min(dt_same_counts, key=lambda dt: dt_to_params[dt][1])
            spp, osr = dt_to_params[best_dt]
            logging.debug("Best dwell time found for %s: %s, with spp = %s, osr = %s",
                          req_dt, best_dt, spp, osr)
        except ValueError:  # No dt < req_dt
            best_dt = min(dt_to_params.keys())
            spp, osr = dt_to_params[best_dt]
            logging.debug("No dwell time found below the requested %s s, picking %s s with spp = %s, osr = %s",
                          req_dt,best_dt, spp, osr)

        return best_dt, spp, osr

    async def _guess_samples_per_pixel(self, req_dt: float, oversampling: int, channels: int, delay: float
                                       ) -> Tuple[float, int]:
        """
        Compute the best samples per pixel (SPP)
        samples per pixel is the number of consecutive measurements corresponding to the same pixel,
        which will be averaged in the result.
        It will *always* return dwell time shorter or equal to the requested dwell time, unless
        the requested dwell time is below the minimum possible dwell time.
        :param req_dt: The requested dwell time in seconds.
        :param oversampling: The oversampling rate that is applied.
        :return:
            dt: actual dwell time accepted (s)
            spp: number of samples per pixel for reaching the dwell time
        """
        # The server doesn't provide information about the best SPP for a given dwell time. It only
        # provides the reverse: for a given SPP, what is the dwell time. So we need to "play" a guess
        # game to find the best SPP by asking with various values until we find the one matching the
        # requested dwell time.

        req_dt_us = req_dt * 1e6
        attempts = 0

        # read the lowest dt (spp = 1) -> dt_min
        dt_min = await self._calculate_dwell_time(oversampling, channels, 1, delay)

        if req_dt_us < dt_min:
            logging.info(f"Requested dwell time {req_dt} µs is less than minimum dwell time {dt_min} µs "
                         f"at oversampling {oversampling}.")
            return dt_min * 1e-6, 1

        # guesstimate by assuming it's linear (so spp == req_dt / dt_min).
        # It's usually not too bad, but might be a little too small.
        spp_min = 1
        spp_max = min(math.ceil(req_dt_us / dt_min), MAX_SAMPLES_PER_PIXEL)
        dt_max = await self._calculate_dwell_time(oversampling, channels, spp_max, delay)
        logging.debug("Starting with an estimate of dt = %f (spp = %s, osr = %d)",
                      dt_max, spp_max, oversampling)

        # Make sure we have an upper bound on the dwell time: double until > requested dt
        while dt_max < req_dt_us and spp_max < MAX_SAMPLES_PER_PIXEL:
            attempts += 1
            spp_min = spp_max
            spp_max = min(spp_max * 2, MAX_SAMPLES_PER_PIXEL)
            dt_max = await self._calculate_dwell_time(oversampling, channels, spp_max, delay)
            logging.debug(f"Updated spp_max to {spp_max} due to dt_max < req_dt.")

        # Dichotomy between spp_min and spp_max:
        # need to find spp so that dt <= req_dt but spp+1 -> dt_p1 > req_dt
        logging.debug("Will search for dt between %f and %f", dt_min, dt_max)
        while spp_min < spp_max:
            attempts += 1
            spp = (spp_min + spp_max) // 2
            dt = await self._calculate_dwell_time(oversampling, channels, spp, delay)
            dt_p1 = await self._calculate_dwell_time(oversampling, channels, spp + 1, delay)

            if dt <= req_dt_us < dt_p1:
                break
            elif dt > req_dt_us:
                spp_max = spp
            else:
                spp_min = spp + 1
        else:
            logging.debug("Returning spp_min %s as spp_min == spp_max.", spp_min)
            spp = spp_min
            dt = await self._calculate_dwell_time(oversampling, channels, spp, delay)

        logging.info("Guessing samples per pixel for dt_req = %s µs as dt %s µs = %s spp * %s osr (in %d attempts)",
                     req_dt_us, dt, spp, oversampling, attempts)
        return dt * 1e-6, spp

    @property
    def controller_state(self) -> str:
        controller_state = self.read_controller_state()
        state_name = controller_state.Text
        if state_name != self._prev_state:
            logging.debug("Controller state changed to %s", state_name)
            self._prev_state = state_name
        return state_name

    def read_controller_state(self) -> LocalizedText:
        """
        :return: The state of the MightyEBIC controller (see STATE_NAME_*)
        """
        f = asyncio.run_coroutine_threadsafe(self._read_controller_state(), self._loop)
        return f.result()

    @retry_on_connection_error
    async def _read_controller_state(self) -> LocalizedText:
        ret_val = await self._ebic_state_node.read_value()
        return ret_val

    def set_controller_state(self, new_state: State):
        f = asyncio.run_coroutine_threadsafe(self._set_controller_state(new_state), self._loop)
        f.result()

    @retry_on_connection_error
    async def _set_controller_state(self, new_state: State):
        await self._ebic_controller_node.call_method(f"{NAMESPACE_INDEX}:set_controller_state",
                                                    new_state)

    def calculate_scan_time(self, dt: float, p_fast: int, p_slow: int) -> float:
        f = asyncio.run_coroutine_threadsafe(self._calculate_scan_time(round(dt * 1e6), p_fast, p_slow),
                                             self._loop)
        return f.result()

    @retry_on_connection_error
    async def _calculate_scan_time(self, dt: int, p_fast: int, p_slow: int) -> float:
        """
        :param dt: dwell time in μs (not "ms" as the documentation claims!)
        :param p_fast: number of pixels in the fast dimension (X)
        :param p_slow: number of pixels in the slow dimension (Y)
        :return: scan time in s
        """
        st = await self._ebic_info_node.call_method(f"{NAMESPACE_INDEX}:calculate_scan_time",
                                                    dt, p_fast, p_slow)
        return st

    def calculate_dwell_time(self, oversampling: int, channels: int, spp: int, delay: float) -> float:
        """
        Computes how long the measurement of one pixel will take, for the given settings.
        See _calculate_dwell_time()
        :param oversampling: Must be within {0, 2, 4, 8, 16, 32, 64}. Number of times the signal is sampled.
        The result is averaged.
        :param channels: The number of channels used simultaneously. The server will only use the first N.
        :param spp: The number of samples per pixel, which will be averaged in the result. Similar in
        behaviour to the "oversampling", but this is done at a higher level, and so is more flexible,
        while requiring extra memory on the ephemeron computer.
        :param delay: time to wait for each pixel before starting the acquisition (in s).
        allows for the signal to reach a steady state before it is measured.
        :return: dwell time in s
        """
        f = asyncio.run_coroutine_threadsafe(self._calculate_dwell_time(oversampling, channels, spp, delay),
                                             self._loop)
        dt_us = f.result()
        # If some incorrect parameters are sent, it returns an int <= 0.
        if dt_us <= 0:
            raise ValueError(f"Invalid calculate_dwell_time() returned error {dt_us}.")
        return dt_us * 1e-6

    @retry_on_connection_error
    async def _calculate_dwell_time(self, oversampling: int, channels: int, spp: int, delay: float) -> int:
        """
        Wrapper around the OPC-UA method to calculate the dwell time.
        See calculate_dwell_time() for more info. (the only difference is that this function returns
        the dwell time in μs, not in s, and it's a coroutine)
        :return: dwell time in μs
        """
        if not oversampling in OVERSAMPLING_VALUES:
            raise ValueError(f"Oversampling value {oversampling} is not valid.")
        # oversampling, channels, spp, delay -> returns dt (int) in μs
        dt = await self._ebic_info_node.call_method(f"{NAMESPACE_INDEX}:calculate_dwell_time",
                                              oversampling, channels, spp, delay)
        return dt

    def start_trigger_scan(self, oversampling: int, channels: int, spp: int, delay: float,
                           p_fast: int, p_slow: int,
                           sim: bool = False, timeout: int = 10):
        f = asyncio.run_coroutine_threadsafe(
            self._start_trigger_scan(oversampling, channels, spp, delay, p_fast, p_slow, sim, timeout),
            self._loop)
        return f.result()

    @retry_on_connection_error
    async def _start_trigger_scan(self, oversampling: int, channels: int, spp: int, delay: float,
                                  p_fast: int, p_slow: int,
                                  sim: bool = False, timeout: int = 10):
        """
        Starts a scan with the MightyEBIC scan controller.
        Non-blocking: it returns as soon as the request to start the scan is accepted.
        :param oversampling: Must be within {0, 2, 4, 8, 16, 32, 64}. Number of times the signal is sampled.
        The result is averaged.
        :param channels: The number of channels used simultaneously. The server will only use the first N.
        :param spp: The number of samples per pixel, which will be averaged in the result. Similar in
        behaviour to the "oversampling", but this is done at a higher level, and so is more flexible,
        while requiring extra memory on the ephemeron computer.
        :param delay: time to wait for each pixel before starting the acquisition (in s).
        allows for the signal to reach a steady state before it is measured.
        :param p_fast: number of pixels in the fast dimension (X)
        :param p_slow: number of pixels in the slow dimension (Y)
        :param sim: If True, the scan will be simulated (by the server)
        :param timeout: The maximum time to wait for the trigger for the scan to start (in s). If the trigger
        doesn't arrive within this time, the scan will be aborted by the server.
        """
        # run the start scan method on the server
        logging.debug("Starting EBIC scan, with os=%s, channels=%s, spp=%s, delay=%s, shape = (%s, %s), delay=%s",
                      oversampling, channels, spp, delay, p_fast, p_slow, sim)

        # oversampling, channels, samples, delay, points_fast, points_slow, sim
        await self._ebic_controller_node.call_method(f"{NAMESPACE_INDEX}:start_trigger_scan",
                                                     oversampling, channels, spp, delay,
                                                     p_fast, p_slow,
                                                     sim, timeout)

    def stop_scan(self) -> None:
        """
        Cancels the scan, if it's running.
        """
        f = asyncio.run_coroutine_threadsafe(self._stop_scan(), self._loop)
        return f.result()

    @retry_on_connection_error
    async def _stop_scan(self) -> None:
        # run the stop scan method on the server
        logging.info(f"Stopping EBIC scan..")
        await self._ebic_controller_node.call_method(f"{NAMESPACE_INDEX}:stop_scan")

    def get_scan_result(self) -> List[float]:
        """
        :return: the raw data of the latest scan result. The order of the data is C (channels), slow, fast,
        (listed in order from the fastest to the slowest). So it is *not* in the same order as it was acquired.
        It is possible to reconstruct a numpy array by using the shape returned by get_scan_result_shape().
        """
        f = asyncio.run_coroutine_threadsafe(self._get_scan_result(), self._loop)
        return f.result()

    @retry_on_connection_error
    async def _get_scan_result(self) -> List[float]:
        """
        Note: this function can be called several times, and the data will still be available.
        :return: See get_scan_result()
        """
        scan_result_node = await self._ebic_info_node.get_child(f"{NAMESPACE_INDEX}:scan_result")
        raw_data = await scan_result_node.read_value()
        return raw_data

    def get_scan_result_shape(self) -> Tuple[int, int, int]:
        """
        Returns the shape of the scan result, as used in numpy.
        :return: p_fast, p_slow, channels
        """
        f = asyncio.run_coroutine_threadsafe(self._get_scan_result_shape(), self._loop)
        return tuple(f.result())

    @retry_on_connection_error
    async def _get_scan_result_shape(self) -> List[int]:
        """
        :return: p_fast, p_slow, channels
        """
        scan_result_shape_node = await self._ebic_info_node.get_child(f"{NAMESPACE_INDEX}:scan_result_shape")
        data_shape = await scan_result_shape_node.read_value()
        return data_shape

    def get_version(self) -> str:
        """
        :return: The software version of the MightyEBIC (server)
        """
        f = asyncio.run_coroutine_threadsafe(self._get_version(), self._loop)
        return f.result()

    @retry_on_connection_error
    async def _get_version(self) -> str:
        """
        :return: The software version of the MightyEBIC (server)
        """
        v = await self._ebic_info_node.call_method(f"{NAMESPACE_INDEX}:version")
        return v


# Simulated OPC-UA Server constants
SCAN_ARGS = [
    Argument(Name="oversampling",
             DataType=NodeId(ObjectIds.Int64),
             ValueRank=-1,
             ArrayDimensions=[],
             Description=LocalizedText("Oversampling Value")),
    Argument(Name="channels",
             DataType=NodeId(ObjectIds.Int64),
             ValueRank=-1,
             ArrayDimensions=[],
             Description=LocalizedText("Number of Scan Channels")),
    Argument(Name="samples",
             DataType=NodeId(ObjectIds.Int64),
             ValueRank=-1,
             ArrayDimensions=[],
             Description=LocalizedText("Number of Samples")),
    Argument(Name="delay",
             DataType=NodeId(ObjectIds.Float),
             ValueRank=-1,
             ArrayDimensions=[],
             Description=LocalizedText("Delay time (ms)")),
    Argument(Name="points_fast",
             DataType=NodeId(ObjectIds.Int64),
             ValueRank=-1,
             ArrayDimensions=[],
             Description=LocalizedText("Points Fast")),
    Argument(Name="points_slow",
             DataType=NodeId(ObjectIds.Int64),
             ValueRank=-1,
             ArrayDimensions=[],
             Description=LocalizedText("Points Slow")),
    Argument(Name="simulate",
             DataType=NodeId(ObjectIds.Boolean),
             ValueRank=-1,
             ArrayDimensions=[],
             Description=LocalizedText("Simulate Scan")),
    Argument(Name="timeout",
             DataType=NodeId(ObjectIds.Int64),
             ValueRank=-1,
             ArrayDimensions=[],
             Description=LocalizedText("Timeout (s)")),
]

DWELLTIME_ARGS = [
    Argument(Name="oversampling",
             DataType=NodeId(ObjectIds.Int64),
             ValueRank=-1,
             ArrayDimensions=[],
             Description=LocalizedText("Oversampling Value")),
    Argument(Name="channels",
             DataType=NodeId(ObjectIds.Int64),
             ValueRank=-1,
             ArrayDimensions=[],
             Description=LocalizedText("Number of Scan Channels")),
    Argument(Name="samples",
             DataType=NodeId(ObjectIds.Int64),
             ValueRank=-1,
             ArrayDimensions=[],
             Description=LocalizedText("Number of Samples")),
    Argument(Name="delay",
             DataType=NodeId(ObjectIds.Float),
             ValueRank=-1,
             ArrayDimensions=[],
             Description=LocalizedText("Delay time (ms)"))
]

SCANTIME_ARGS = [
    Argument(Name="dwell_time",
             DataType=NodeId(ObjectIds.Int64),
             ValueRank=-1, ArrayDimensions=[],
             Description=LocalizedText("Dwell Time (ms)")),
    Argument(Name="points_fast",
             DataType=NodeId(ObjectIds.Int64),
             ValueRank=-1,
             ArrayDimensions=[],
             Description=LocalizedText("Points Fast")),
    Argument(Name="points_slow",
             DataType=NodeId(ObjectIds.Int64),
             ValueRank=-1,
             ArrayDimensions=[],
             Description=LocalizedText("Points Slow"))
]

STATE_ARGS = [
    Argument(Name="new_state",
             DataType=NodeId(ObjectIds.String),
             ValueRank=-1,
             ArrayDimensions=[],
             Description=LocalizedText("Requested State"))
]


class MightyEBICSimulator(Server):
    """ OPC Server class: This class is required for setting up a simulated server and the state machine. """
    def __init__(self, url: str, parent_det: MightyEBIC):
        super().__init__()
        self.state_machine: Optional[StateMachine] = None
        self.ebic_state_node: Optional[Node] = None
        self.ebic_controller_node: Optional[Node] = None
        self.ebic_info_node: Optional[Node] = None
        self.scan_time: Optional[float] = None
        self.ready = False
        self.states = {}
        self.terminated = False
        self._url = url
        self._parent_det = parent_det
        self._data_var: Optional[Node] = None
        self._data_shape_var: Optional[Node] = None
        self._stop_scan = threading.Event()  # Set when a request to stop the scan (early) is received
        self._dt = 1e-5
        self._server_exception: Optional[str] = None

        self._t_simserver = threading.Thread(target=self._start_opc_simserver)
        self._t_simserver.start()
        # start the simulated server threaded but wait for it to be ready (running)
        while not self.ready:
            time.sleep(0.1)
            if self._server_exception:
                raise self._server_exception

    def terminate(self):
        # the opcServer is simulated, stop the controlling thread first
        self.terminated = True
        self._t_simserver.join()

    def _start_opc_simserver(self):
        try:
            asyncio.run(self.connect_to_server())
        except ConnectionError:
            self._server_exception = ConnectionError(f"Unable to start up the simulated server")
        except Exception as ex:
            self._server_exception = ex

    async def setup(self) -> None:
        """ Set up the server StateMachine, nodes, events, methods and variables. """
        await self.init()
        self.set_endpoint(self._url)

        await self.register_namespace(NAMESPACE_ADDRESS)
        await self.setup_state_machine()
        await self.setup_info_node()
        await self.setup_controller()

    async def setup_controller(self) -> None:
        """ Set up the EBIC scan controller node and its own methods. """
        self.ebic_controller_node = await self.nodes.objects.add_object(
            NAMESPACE_INDEX,
            EBIC_CONTROLLER_NODE,
        )

        # Add the required methods to the EBIC controller node
        await self.ebic_controller_node.add_method(NAMESPACE_INDEX, "start_trigger_scan", self.request_scan_start, SCAN_ARGS, [])
        await self.ebic_controller_node.add_method(NAMESPACE_INDEX, "set_controller_state", self.change_state, STATE_ARGS, [])
        await self.ebic_controller_node.add_method(NAMESPACE_INDEX, "stop_scan", self.request_scan_stop, [], [])

        # Info section holds updated read-only state variables from the EBIC GUI as well as all "query functions" that
        # clients use to compute properties of device scans without actually running them
        await self.ebic_info_node.add_method(NAMESPACE_INDEX, "calculate_dwell_time", self.calculate_dwell_time, DWELLTIME_ARGS,
                                             [Argument(Name="dwell_time",
                                             DataType = NodeId(ObjectIds.Int64),
                                             ValueRank = -1, ArrayDimensions = [],
                                             Description = LocalizedText("Dwell Time (ms)"))])
        await self.ebic_info_node.add_method(NAMESPACE_INDEX, "calculate_scan_time", self.calculate_scan_time, SCANTIME_ARGS,
                                             [Argument(Name="scan_time",
                                             DataType = NodeId(ObjectIds.Int64),
                                             ValueRank = -1, ArrayDimensions = [],
                                             Description = LocalizedText("Scan Time (s)")),])
        await self.ebic_info_node.add_method(NAMESPACE_INDEX, "version", self.get_version, [],
                                             [Argument(Name = "version",
                                             DataType = NodeId(ObjectIds.String),
                                             ValueRank = -1, ArrayDimensions = [],
                                             Description = LocalizedText("MightyEBIC Version")),])

    async def setup_info_node(self):
        """ Set up the EBIC info node which will contain data after a successful scan. """
        self.ebic_info_node = await self.nodes.objects.add_object(
            NAMESPACE_INDEX,
            EBIC_INFO_NODE)

        self._data_var = await self.ebic_info_node.add_variable(
            NAMESPACE_INDEX,
            "scan_result",
            (numpy.zeros((1, 1), dtype=numpy.float64)).tolist())

        self._data_shape_var = await self.ebic_info_node.add_variable(
            NAMESPACE_INDEX,
            "scan_result_shape",
            [1, 1, 1])

    async def setup_state_machine(self) -> None:
        """ Set up the state machine for the server. """
        self.state_machine = StateMachine(
            self,
            self.nodes.objects,
            NAMESPACE_INDEX,
            EBIC_STATE_NODE,
        )

        # install the state machine
        await self.state_machine.install()

        # create all the states that will be used
        self.states[STATE_NAME_IDLE] = State(STATE_NAME_IDLE, STATE_NAME_IDLE, 1, node=None)
        await self.state_machine.add_state(self.states[STATE_NAME_IDLE], state_type=ua.NodeId(2309, 0))

        self.states[STATE_NAME_BUSY] = State(STATE_NAME_BUSY, STATE_NAME_BUSY, 2, node=None)
        await self.state_machine.add_state(self.states[STATE_NAME_BUSY])

        self.states[STATE_NAME_TRIGGER] = State(STATE_NAME_TRIGGER, STATE_NAME_TRIGGER, 2, node=None)
        await self.state_machine.add_state(self.states[STATE_NAME_TRIGGER])

        self.states[STATE_NAME_ERROR] = State(STATE_NAME_ERROR, STATE_NAME_ERROR, 2, node=None)
        await self.state_machine.add_state(self.states[STATE_NAME_ERROR])

        # set the state to IDLE after installation
        await self.state_machine.change_state(self.states[STATE_NAME_IDLE])

    async def connect_to_server(self):
        """
        Set up a very basic operational example of an OPC server, it only needs to be alive as long as it is necessary.
        """
        logging.info("Starting OPC-UA MightyEBIC server simulator")
        await self.setup()

        async with self:
            self.ready = True
            while not self.terminated:
                await asyncio.sleep(2)

    @uamethod
    async def get_version(self, parent):
        return "0.0.1-sim"

    @uamethod
    async def calculate_dwell_time(self, parent, oversampling: int, channels: int, spp: int, delay: float) -> int:
        """
        Calculates the dwell time of each pixel based on channels, samples, delay and oversampling.
        Time constants are based on PRU code that drives for AD5764 DAC and AD7608 ADC.
        This method is a copy of the method Ephemeron uses in their example server code.
        (see https://bitbucket.org/delmic/delmicephemeron/ )
        :param parent: NodeId
        :param oversampling: The oversampling rate that is applied.
        :param channels: The number of channels used simultaneously.
        :param spp: The number of samples per pixel used, this value is determined by the requested dt.
        :param delay: this is a variable delay that allows for the signal to
            reach a steady state before it is measured.
        :return: the calculated dwell time in microseconds.
        """
        return self._calculate_dwell_time(oversampling, channels, spp, delay)

    def _calculate_dwell_time(self, oversampling: int, channels: int, spp: int, delay: float) -> int:
        # All values are in ns
        # calculation of delay cycles (delay step is set at 10e-9 default)
        delay = numpy.uint32(delay / 10e-9)
        trigger = True  # Only simulate the trigger mode

        # ADC Time constants
        WAIT = 4000  # default conversion rate
        OS_scalar = 4500  # Oversampling scalar per OS multiple 0,2,4,8,16,32,64
        CH_scalar = 1365  # Scalar for each channel we need to clock out
        samples_scalar = 20  #

        # DAC time constants
        DAC_var = 10  # variable delay multiplier in ns

        # ADC sampling Overhead
        sample_Overhead = 110  #

        # DAC write Overhead
        LOADDAC_OV = 5345  # overhead
        DACUP_OV = 200

        # Calculate how long it takes to do an ADC read with
        # if OS is greater than 2 we need to use different scalar
        if oversampling >= 2:
            WAIT = OS_scalar * oversampling
        # for each conversion amount of time it takes clock out each channel
        CH_T = CH_scalar * oversampling * channels

        # total ADC READ based on CH and OS
        ADCREAD = 120 + CH_T + WAIT

        # total number of conversions at a pixel
        LOOP3 = (sample_Overhead + samples_scalar * spp + ADCREAD * spp)

        # time for each DAC update
        # DacUpdate not needed in Trigger mode
        if trigger:
            DACUPDATE = 0
        else:
            DACUPDATE = LOADDAC_OV + DAC_var * delay + DACUP_OV

        Dwell_us = (LOOP3 + DACUPDATE) / 1000.0  # convert to us

        return math.ceil(Dwell_us)

    @uamethod
    async def calculate_scan_time(self, parent, dt: float, res_fast: int, res_slow: int) -> float:
        """
        Calculate and return the scan time based on the dwell time and the resolution.
        :param parent: NodeId
        :param dt: The requested dwell time.
        :param res_fast: The horizontal points of the resolution.
        :param res_slow: The vertical points of the resolution.
        :return: Scan time in s.
        """
        return self._calculate_scan_time(dt, res_fast, res_slow)

    def _calculate_scan_time(self, dt: float, res_fast: int, res_slow: int) -> float:
        self._dt = dt

        LOOP2_OV = 40
        LOOP1_OV = 40
        SETUP = 1025  # Overhead to set up scan

        LOOP2 = (dt * 1000 + LOOP2_OV) * res_fast + 5
        LOOP1 = (LOOP2 + LOOP1_OV) * res_slow

        ScanTime_ns = SETUP + LOOP1  # scan time in nanoseconds
        ScanTime_s = ScanTime_ns / 1.0e9

        return ScanTime_s

    @uamethod
    async def change_state(self, parent, new_state):
        logging.debug(f"Setting StateMachine CurrentState to new state -> {new_state}")
        await self.state_machine.change_state(self.states[new_state], transition=None)

    @uamethod
    async def request_scan_start(self, parent,
                                 oversampling: int, channels: int, spp: int, delay: float,
                                 points_fast: int, points_slow: int, simulate: bool, timeout: int):
        """
        The actual scan implementation on simulated server.
        :param parent: NodeId
        :param oversampling: The oversampling rate that is applied.
        :param channels: The number of channels used simultaneously.
        :param spp: The number of samples per pixel used.
        :param delay: variable delay that allows for the signal to reach a steady state before it is measured (ms)
        :param points_fast: The horizontal points of the resolution.
        :param points_slow: The vertical points of the resolution.
        :param simulate: Simulate the scan.
        :param timeout: maximum time to wait for the trigger (in s)
        """
        await self.state_machine.change_state(self.states[STATE_NAME_TRIGGER])

        # Estimate the scan time, to simulate
        dt = self._calculate_dwell_time(oversampling, channels, spp, delay)
        scan_time = self._calculate_scan_time(dt, points_fast, points_slow)
        self._stop_scan.clear()
        acquisition_thread = threading.Thread(target=self.start_trigger_scan,
                                              name="Simulated EBIC acquisition thread",
                                              args=(scan_time, channels, points_fast, points_slow))
        acquisition_thread.start()

    def start_trigger_scan(self, scan_time: float, channels: int, points_fast: int, points_slow: int):
        try:
            # as the scan time can be tiny, add a 0.1 s overhead
            if self._stop_scan.wait(scan_time + 0.1):
                # if stop scan is requested return without updating the data
                logging.debug("Scan stopped before it was completed")
            else:
                asyncio.run(self.update_data((points_fast, points_slow, channels)))

            asyncio.run(self.state_machine.change_state(self.states[STATE_NAME_IDLE]))
        except Exception as ex:
            logging.exception(f"Simulated scan failed")
        finally:
            logging.debug("Simulated scan thread completed")

    async def update_data(self, shape):
        scan_result = numpy.random.rand(*shape) * 10  # between 0 and 10 (mA), as the device typically does
        logging.debug(f"Simulating data with shape {scan_result.shape}")
        await self._data_shape_var.write_value(shape)
        await self._data_var.write_value(scan_result.flatten().tolist())

    @uamethod
    async def request_scan_stop(self, parent):
        logging.debug(f"stop_scan requested from client")
        self._stop_scan.set()
