# -*- coding: utf-8 -*-
"""
Created on 24 April 2024

@author: Stefan Sneep

Copyright © 2024 Stefan Sneep, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the
GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty
of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis.
If not, see http://www.gnu.org/licenses/.

# this driver is developed for communication with an EBIC controller API
# the EBIC scan controller from Ephemeron will acquire a digital EBIC signal
# to be used simultaneously with other signals such as SE/CL
"""

import asyncio
import logging
import math
import re
import threading
import time
import queue
from asyncio import AbstractEventLoop
from socketserver import ThreadingMixIn
from threading import Thread
from typing import Optional, Tuple

from odemis import model
from odemis.model import Detector, oneway, MD_ACQ_DATE, HwError

import numpy
from asyncua import Client, Server, Node, ua
from asyncua.common.methods import uamethod
from asyncua.common.statemachine import State, StateMachine
from asyncua.ua import NodeId, ObjectIds, LocalizedText, Argument

# OPCUA StateMachine constants
STATE_NAME_IDLE = "Idle"
STATE_NAME_BUSY = "Busy"
STATE_NAME_TRIGGER = "Trigger"
STATE_NAME_ERROR = "Error"

# MightyEBIC driver constants
MAX_SAMPLES_PER_PIXEL = 10
MAX_NUMBER_OF_CHANNELS = 8
OVERSAMPLING_SET_VALUES = {0, 2, 4, 8, 16, 32, 64}
NAMESPACE_INDEX = 0
NAMESPACE_ADDRESS = "http://opcfoundation.org/UA/"
SIMULATED_URL = "opc.tcp://localhost:4840/mightyebic/server/"
DEFAULT_TIMEOUT = 60  #s
DATA_VAR_NAME = "scan_result"
EBIC_CONTROLLER_NODE = "MightyEBICController"
EBIC_STATE_NODE = "MightyEBICState"
EBIC_INFO_NODE = "MightyEBICInfo"

# simulated OPCUA Server constants
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


class MightyEBIC(Detector):
    def __init__(self, name: str, role: str, channel: int, url: str, **kwargs):
        """
        Initialise the EBIC controller
        :param name (str): The name of the device configured through the configuration file.
        :param role (str): The role of the device configured through the configuration file.
        :param channel (int): The channel the device should use for hw triggering.
        :param url (str): The url address to use with the OPC UA protocol.
            Example of such an url might be opc.tcp://192.168.50.2:4840/mightyebic/server/
            Note: Can contain "fake" to indicate it should be simulated.
        """
        super().__init__(name, role, **kwargs)

        self._channel = channel
        self._url = url
        self._namespace = NAMESPACE_ADDRESS

        # server_sim is only used for simulation of the opcServer if it is non-existent
        self._opc_server_sim: Optional[MightyEBICSimulator] = None
        self._opc_client: Optional[UaClient] = None
        self._error_msg: Optional[str] = None
        # TODO: hardcoded now, eventually -> get resolution from the controller/detector if possible
        self._ebeam_res = ((1, 1), (4096, 4096))

        if url == "fake":
            # if the device should be simulated, start a simulated server first
            self._url = SIMULATED_URL
            self._opc_server_sim = MightyEBICSimulator(self._url , self._namespace, self)
        else:
            url_check = r"^opc\.tcp://(localhost|(\d{1,3}\.){3}\d{1,3}):4840/"
            if not re.search(url_check, url):
                raise HwError(f"The url {url} to connect to is not in the right format.")

        # fire up the client needed for the opcua communication with the server
        self._opc_client = UaClient(self._url, DEFAULT_TIMEOUT)
        asyncio.run(self._opc_client.initialize_client())  # this will wait/block until the client is initialized

        self.numberOfChannels = model.IntEnumerated(1,
                                                    set(range(1, MAX_NUMBER_OF_CHANNELS + 1)),
                                                    setter=self.on_chan_num_change)
        # Oversampling is a digital low-pass filter that removes high-frequency
        # noise and sends back a single clean 18-bit value.
        self.oversampling = model.IntEnumerated(0, OVERSAMPLING_SET_VALUES)
        self.oversampling.subscribe(self.on_oversampling_change)
        self.resolution = model.ResolutionVA(self._ebeam_res[1], (self._ebeam_res[0], self._ebeam_res[1]), unit="px")
        # set 10 us dwell time as default value, the minimum range values is determined by tests
        # the maximum value is just a very high value which might never be used in a real acquisition
        self.dwellTime = model.FloatContinuous(1e-5, (6e-6, 1000), unit="s", setter=self.on_dwell_time_change)

        self.data = EBICDataFlow(self)
        self._acquisition_thread: Optional[threading.Thread] = None
        self._acquisition_lock = threading.Lock()
        self._acquisition_must_stop = threading.Event()

        # Special event to request software unblocking on the scan
        self.softwareTrigger = model.Event()
        self._metadata[model.MD_DET_TYPE] = model.MD_DT_NORMAL
        self._swVersion = self._opc_client.get_mightyebic_sw_version
        self._metadata[model.MD_SW_VERSION] = self._swVersion

    def start_acquire(self, _):
        with self._acquisition_lock:
            self._wait_acquisition_stopped()
            self._acquisition_thread = threading.Thread(target=self._acquire_thread,
                                                        name="EBIC acquisition thread")
            self._acquisition_thread.start()

    def _acquire_thread(self) -> None:
        """
        Initiate a scan on the EBIC scan controller, before the scan starts the required data is gathered
        and a co-routine is initiated within the ua_client. The scan time, with an overhead of 60 seconds
        determin the time-out of the scan. After the method is initiated the scan controller waits for the
        signal of the nidaq board through TTL.
        """
        try:
            self.data._waitSync()
            res = self.resolution.value
            md = self.getMetadata()
            md[MD_ACQ_DATE] = time.time()

            scan_time = self._opc_client.calculate_scan_time(self.dwellTime.value, res[0], res[1])
            end_time = time.time() + DEFAULT_TIMEOUT + scan_time

            self._opc_client.start_trigger_scan(self.dwellTime.value, 0, 1, 0, res[0], res[1], True)

            # while the scan controller is busy, check for a time-out or stop signal
            while self._opc_client.controller_state == STATE_NAME_BUSY:
                if time.time() > end_time:
                    raise TimeoutError()
                # TODO check if the interval is correct
                if self._acquisition_must_stop.wait(1):
                    self._opc_client.stop_scan()
                    return

            # get the data from the last acquisition and notify subscribers
            da = asyncio.run(self.read_data(res, md))
            self.data.notify(da)

        except TimeoutError:
            logging.error("Acquisition time took more than expected")
            # stop the running acquisition
            self._opc_client.stop_scan()

        except Exception:
            logging.error("Unexpected failure during acquisition")
        finally:
            logging.debug("Acquisition thread closed")

    def stop_acquire(self):
        """ Stops the current running acquisition, should have no effect when there is none. """
        with self._acquisition_lock:
            self._acquisition_must_stop.set()

    def _wait_acquisition_stopped(self):
        """ Waits until the acquisition thread is fully finished _if_ it was requested to stop. """
        if self._acquisition_must_stop.is_set():
            logging.debug("Waiting for thread to stop.")
            self._acquisition_thread.join(10)  # 10s timeout for safety
            if self._acquisition_thread.is_alive():
                logging.exception("Failed to stop the acquisition thread")
                # Now let's hope everything is back to normal...
            # ensure it's not set, even if the thread died prematurely
            self._acquisition_must_stop.clear()

    async def read_data(self, resolution, md):
        """
        After a successful scan data generated by the scan controller should be available for retrieval.
        :param resolution: The used resolution (in pixels) for the scan.
        :param md: The metadata to be added to the raw data.
        :return: a Numpy array with the right shape containing the data acquired.
        """
        raw_data = self._opc_client.get_scan_result()

        # reshape the data first -> Data array ND array + MD
        raw_arr = numpy.array(raw_data)
        raw_arr = raw_arr.reshape(resolution[0], resolution[1])

        da = model.DataArray(raw_arr, md)
        return da

    def on_chan_num_change(self, value):
        self._opc_client.channels = value
        return value

    def on_oversampling_change(self, value):
        return value

    def on_dwell_time_change(self, value):
        # guess the samples per pixel according to the set (requested) value -> note dt_min is 5.615e-6 for a spp of 1
        dt, spp, attempts = self._opc_client.guess_samples_per_pixel(value, self.oversampling.value)
        if not math.isclose(dt, value, rel_tol=1e-5):
            logging.warning(f"Requested dwell time {value} differs from calculated dwell time {dt}.")
        self._opc_client.spp = spp
        return dt

    def terminate(self):
        super().terminate()
        if self._opc_server_sim:
            self._opc_server_sim.terminate()
        self._opc_client.close_connection()


class MightyEBICSimulator(Server):
    """ OPC Server class: This class is required for setting up a simulated server and the state machine. """
    def __init__(self, url: str, namespace: str, parent_det: MightyEBIC):
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
        self._namespace = namespace
        self._parent_det = parent_det
        self._data_var: Optional[Node] = None
        self._stop_scan = False
        self._dt = 1e-5
        self._server_exception: Optional[str] = None
        self._points_fast: Optional[int] = None
        self._points_slow: Optional[int] = None

        self._t_simserver = threading.Thread(target=self._start_opc_simserver)
        self._t_simserver.start()
        # start the simulated server threaded but wait for it to be ready (running)
        while not self.ready:
            time.sleep(0.1)
            if self._server_exception:
                raise HwError(self._server_exception)

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

        await self.register_namespace(self._namespace)
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
        await self.ebic_controller_node.add_method(NAMESPACE_INDEX, "start_trigger_scan", self.request_scan_start, [], [])
        await self.ebic_controller_node.add_method(NAMESPACE_INDEX, "set_controller_state", self.change_state, STATE_ARGS, [])
        await self.ebic_controller_node.add_method(NAMESPACE_INDEX, "stop_scan", self.request_scan_stop, [], [])

        # Info section holds updated read-only state variables from the EBIC GUI as well as all "query functions" that
        # clients use to compute properties of device scans without actually running them
        await self.ebic_info_node.add_method(NAMESPACE_INDEX, "calculate_dwell_time", self.calculate_dwellTime, DWELLTIME_ARGS,
                                   [Argument(Name="dwell_time",
                                             DataType = NodeId(ObjectIds.Int64),
                                             ValueRank = -1, ArrayDimensions = [],
                                             Description = LocalizedText("Dwell Time (ms)"))])
        await self.ebic_info_node.add_method(NAMESPACE_INDEX, "calculate_scan_time", self.calculate_scanTime, SCANTIME_ARGS,
                                   [Argument(Name="scan_time",
                                             DataType = NodeId(ObjectIds.Int64),
                                             ValueRank = -1, ArrayDimensions = [],
                                             Description = LocalizedText("Scan Time (s)")),])

    async def setup_info_node(self):
        """ Set up the EBIC info node which will contain data after a successful scan. """
        self.ebic_info_node = await self.nodes.objects.add_object(
            NAMESPACE_INDEX,
            EBIC_INFO_NODE)

        self._data_var = await self.ebic_info_node.add_variable(
            NAMESPACE_INDEX,
            DATA_VAR_NAME,
            (numpy.zeros((1, 1), dtype=numpy.float64)).tolist())

        # Set the variable to be writable by clients
        await self._data_var.set_writable()

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
        logging.info("Starting opcua server")
        await self.setup()

        async with self:
            self.ready = True
            while not self.terminated:
                await asyncio.sleep(2)

    @uamethod
    async def calculate_dwellTime(self, parent: MightyEBIC, oversampling, channels, spp, delay=0.0) -> float:
        """
        Calculates the dwell time of each pixel based on Channels, samples, delay and oversampling.
        Time constants are based on PRU code that drives for AD5764 DAC and AD7608 ADC.
        All constants are in nanoseconds. This method is a copy of the method Ephemeron uses their code.
        :param parent (MightyEBIC): The parent detector to have access to a few needed VA's.
        :param oversampling (float): The oversampling rate that is applied.
        :param channels (int): The number of channels used simultaneously.
        :param spp (float): The number of samples per pixel used, this value is determined by the requested dt.
        :param delay (int, optional): Number of clock cycles, this is a variable delay that allows for the signal to
            reach a steady state before it is measured.
        :return: the calculated dwell time in microseconds.
        """
        # calculation of delay cycles (delay step is set at 10e-9 default)
        delay = numpy.uint32(delay/10e-9)
        trigger = True  # TODO: do we need to be able to set this to other values?

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
        CH_T = CH_scalar * oversampling

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

        return Dwell_us

    @uamethod
    async def calculate_scanTime(self, parent: MightyEBIC, dt, res_fast, res_slow) -> float:
        """
        Calculate and return the scan time based on the dwell time and the resolution.
        :param parent (MightyEBIC): The parent detector to have access to a few needed VA's.
        :param dt (float): The requested dwell time.
        :param res_fast: The horizontal points of the resolution.
        :param res_slow: The vertical points of the resolution.
        :return: Scan time in ns.
        """
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
    async def request_scan_start(self, parent: MightyEBIC, oversampling, channels, spp, delay, points_fast, points_slow, simulate=True):
        """
        The actual scan implementation on simulated server.
        :param parent (MightyEBIC): The parent detector to have access to a few needed VA's.
        :param oversampling (float): The oversampling rate that is applied.
        :param channels (int): The number of channels used simultaneously.
        :param spp (float): The number of samples per pixel used, this value is determined by the requested dt.
        :param delay (int, optional): Number of clock cycles, this is a variable delay that allows for the signal to
            reach a steady state before it is measured.
        :param points_fast (int): The horizontal points of the resolution.
        :param points_slow (int): The vertical points of the resolution.
        :param simulate: Simulate the scan.
        """
        await self.state_machine.change_state(self.states[STATE_NAME_BUSY])
        self._points_fast = points_fast
        self._points_slow = points_slow

        # scan time in seconds
        scan_time = (1025 + (((self._dt * 1000 + 40) * points_fast + 5) + 40) * points_slow) / 1.0e9
        acquisition_thread = threading.Thread(target=self.start_trigger_scan,
                                              name="Simulated EBIC acquisition thread",
                                              args=(scan_time,))
        acquisition_thread.start()

    def start_trigger_scan(self, scan_time):
        try:
            # as the scan time can be tiny, multiply by 5 to stay in this state for a significant period of time
            scan_time_end = scan_time * 5
            scan_time_start = 0.0
            scan_interval = 0.1

            while scan_time_start < scan_time_end:
                # if stop scan is requested return without updating the data
                if self._stop_scan:
                    self._stop_scan = False
                    return
                time.sleep(scan_interval)
                scan_time_start += scan_interval

            # update the data
            asyncio.run(self.update_data())

        except Exception as ex:
            logging.warning(f"Scan interrupted: {ex}")

    async def update_data(self):
        scan_result = numpy.random.rand(self._points_fast, self._points_slow, 1)
        data_node_name = await self._data_var.read_display_name()
        logging.debug(f"updating nparray {data_node_name} with shape {scan_result.shape}")
        await self._data_var.write_value(scan_result.flatten().tolist())
        await self.state_machine.change_state(self.states[STATE_NAME_IDLE])

    @uamethod
    async def request_scan_stop(self, parent):
        logging.warning(f"stop_scan requested from client")
        self._stop_scan = True

    def terminate(self):
        # the opcServer is simulated, stop the controlling thread first
        self.terminated = True
        self._t_simserver.join()


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
            self._detector.start_acquire(self.notify)
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


class UaClient:
    """
    A dedicated class to support an opcClient.
    """
    def __init__(self, url, timeout):
        self.client = Client(url=url, timeout=timeout)
        self.spp = 1  # set default to 1
        self.channels = 1  # set default to 1
        self._ebic_info_node: Optional[Node] = None
        self._ebic_state_node: Optional[Node] = None
        self._ebic_controller_node: Optional[Node] = None
        # set the controller state to disconnected at default
        self._controller_state = ua.LocalizedText("Disconnected", "en-US")
        self._loop: Optional[AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None

        # Create a new event loop
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop_thread = threading.Thread(target=self.start_event_loop, daemon=True)
        self._loop_thread.start()

    async def initialize_client(self):
        """ Assign all the necessary nodes and create the event loop. """
        async with self.client:
            objects_node = await self.client.nodes.root.get_child(f"{NAMESPACE_INDEX}:Objects")
            state_node = await objects_node.get_child(f"{NAMESPACE_INDEX}:{EBIC_STATE_NODE}")
            self._ebic_info_node = await objects_node.get_child(f"{NAMESPACE_INDEX}:{EBIC_INFO_NODE}")  # needed
            self._ebic_controller_node = await objects_node.get_child(f"{NAMESPACE_INDEX}:{EBIC_CONTROLLER_NODE}")
            self._ebic_state_node = await state_node.get_child(f"{NAMESPACE_INDEX}:CurrentState")

    def start_event_loop(self):
        """ the command run_forever() has to be set in a separate thread due to its blocking nature. """
        self._loop.run_forever()

    def guess_samples_per_pixel(self, req_dt: float, oversampling: int) -> Tuple[float, int, int]:
        f = asyncio.run_coroutine_threadsafe(self._guess_samples_per_pixel(req_dt, oversampling), self._loop)
        return f.result()

    async def _guess_samples_per_pixel(self, req_dt: float, oversampling: int) -> Tuple[float, int, int]:
        """
        Samples per pixel (SPP) is the number of samples that are measured at each pixel and averaged.
        Calculate the best guess for the requested dwell time using a range of SPP apply the 50% guess method.
        :param req_dt: The requested dwell time in seconds.
        :return: Closest calculated samples per pixel value.
        """
        best_spp = 1.0
        round_spp = 1.0
        # convert to dwell time in µs, the method call calculate_dwell_time eventually
        # returns a dwell time value in µs. Therefore, keep comparing values the same.
        req_dt *= 1e6
        dt = req_dt
        attempts = 20

        # give the guessing 10 attempts, to prevent infinite looping, after 10 attempts log a warning
        for i in range(attempts):
            round_spp = max(math.floor(best_spp), 1)
            # note dt returned is an integer in μs
            dt = await self._calculate_dwell_time(oversampling, self.channels, round_spp, 0)
            if math.isclose(dt, req_dt, abs_tol=1e-3):  # abs tolerance of 1 ns
                logging.debug(f"Samples per pixel match found ({best_spp}) for requested dwell time of {req_dt / 1e6}")
                attempts = i
                break
            if i == attempts - 1:
                logging.warning(f"Best samples per pixel match not found, picking lowest "
                                f"({best_spp}) for requested dwell time of {req_dt / 1e6}")
                break
            elif dt > req_dt:
                best_spp /= dt / req_dt
            elif dt < req_dt:
                best_spp *= req_dt / dt

        dt /= 1e6

        return dt, round_spp, attempts

    async def _read_controller_state(self):
        async with self.client:
            ret_val = await self._ebic_state_node.read_value()
        return ret_val

    def _get_controller_state(self):
        f = asyncio.run_coroutine_threadsafe(self._read_controller_state(), self._loop)
        return f.result()

    def set_controller_state(self, new_state):
        f = asyncio.run_coroutine_threadsafe(self._set_controller_state(new_state), self._loop)
        f.result()

    async def _set_controller_state(self, new_state):
        async with self.client:
            await self._ebic_controller_node.call_method(f"{NAMESPACE_INDEX}:set_controller_state", new_state)

    def calculate_scan_time(self, dt, p_fast, p_slow) -> float:
        f = asyncio.run_coroutine_threadsafe(self._calculate_scan_time(dt, p_fast, p_slow), self._loop)
        return f.result()

    async def _calculate_scan_time(self, dt, p_fast, p_slow) -> float:
        async with self.client:
            # dwell_time, points_fast, points_slow
            st = await self._ebic_info_node.call_method(f"{NAMESPACE_INDEX}:calculate_scan_time", dt, p_fast, p_slow)
            return st

    async def _calculate_dwell_time(self, oversampling, channels, spp, delay) -> int:
        async with self.client:
            # oversampling, channels, spp, delay -> returns dt (int) in μs
            dt = await self._ebic_info_node.call_method(
                f"{NAMESPACE_INDEX}:calculate_dwell_time",
                oversampling,
                channels,
                spp,
                delay)
            return dt

    def start_trigger_scan(self, req_dt, oversampling, channels, delay, p_fast, p_slow, sim=False):
        f = asyncio.run_coroutine_threadsafe(
            self._start_trigger_scan(req_dt, oversampling, channels, delay, p_fast, p_slow, sim),
            self._loop)
        return f.result()

    async def _start_trigger_scan(self, req_dt, oversampling, channels, delay, p_fast, p_slow, sim=False):
        """
        Starts a scan with the MightyEBIC scan controller.
        Send specific arguments to the server/client with the start_trigger_scan method.
        """
        # TODO change this in the detector code
        # spp = await self.guess_samples_per_pixel(req_dt)

        async with self.client:
            # run the start scan method on the server
            logging.info(f"Starting EBIC scan, with requested dwell time of {req_dt}s")

            # oversampling, channels, samples, delay, points_fast, points_slow, sim
            await self._ebic_controller_node.call_method(f"{NAMESPACE_INDEX}:start_trigger_scan",
                                                         oversampling,
                                                         channels,
                                                         self.spp,
                                                         delay,
                                                         p_fast,
                                                         p_slow,
                                                         sim)

            await asyncio.sleep(1)  # wait just a little until the server updated scan_result internally

    def stop_scan(self):
        f = asyncio.run_coroutine_threadsafe(self._stop_scan(), self._loop)
        return f.result()

    async def _stop_scan(self):
        async with self.client:
            # run the stop scan method on the server
            logging.info(f"Stopping EBIC scan..")
            await self._ebic_controller_node.call_method(f"{NAMESPACE_INDEX}:stop_scan")

    def get_scan_result(self) -> float:
        f = asyncio.run_coroutine_threadsafe(self._get_scan_result(), self._loop)
        return f.result()

    async def _get_scan_result(self) -> float:
        async with self.client:
            scan_result_node = await self._ebic_info_node.get_child(f"{NAMESPACE_INDEX}:scan_result")
            raw_data = await scan_result_node.read_value()
            return raw_data

    def get_mightyebic_sw_version(self):
        f = asyncio.run_coroutine_threadsafe(self._get_mightyebic_sw_version(), self._loop)
        return f.result()

    async def _get_mightyebic_sw_version(self):
        async with self.client:
            await self._ebic_controller_node.call_method(f"{NAMESPACE_INDEX}:version")

    @property
    def controller_state(self):
        self._controller_state = self._get_controller_state()
        return self._controller_state.Text

    def close_connection(self):
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join()
