# -*- coding: utf-8 -*-
'''
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
'''
from __future__ import annotations

# this driver is developed for communication with an EBIC controller API
# the EBIC scan controller from Ephemeron will acquire a digital EBIC signal
# to be used simultaneously with other signals such as SE/CL

import logging
import threading
import time
import queue
import weakref

import asyncio
from typing import Callable, Optional

import numpy
from asyncua import Client, Server, Node, ua
from asyncua.common.methods import uamethod
from asyncua.common.statemachine import State, StateMachine
from numpy import ndarray

from odemis import model
from odemis.model import Detector, HwError, oneway

# constants
STATE_ID_IDLE = "server-idle"
STATE_ID_RUNNING = "server-running"
STATE_ID_TRIGGER = "server-triggered"
STATE_ID_CHECKING_DWELL = "server-checking-dwell"
STATE_ID_STOPPED = "server-stopped"
STATE_ID_ERROR = "server-error"

STATE_NAME_IDLE = "Idle"
STATE_NAME_RUNNING = "Running"
STATE_NAME_TRIGGER = "Trigger"
STATE_NAME_CHECKING_DWELL = "CheckingDwell"
STATE_NAME_STOPPED = "Stopped"
STATE_NAME_ERROR = "Error"

TRANSITION_ID_IDLE = "to-idle"
TRANSITION_ID_RUNNING = "to-running"
TRANSITION_ID_TRIGGER = "to-trigger"
TRANSITION_ID_CHECKING_DWELL = "to-checking-dwell"
TRANSITION_ID_STOPPED = "to-stopped"
TRANSITION_ID_ERROR = "to-error"

TRANSITION_NAME_IDLE = "To Idle"
TRANSITION_NAME_RUNNING = "To Running"
TRANSITION_NAME_TRIGGER = "To Trigger"
TRANSITION_NAME_CHECKING_DWELL = "To Checking Dwell"
TRANSITION_NAME_STOPPED = "To Stopped"
TRANSITION_NAME_ERROR = "To Error"

MAX_SAMPLES_PER_PIXEL = 10
MAX_NUMBER_OF_CHANNELS = 8
DATA_VAR_NAME = "MightyEBICDataArray"
EBIC_CONTROLLER_NODE = "MightyEBICController"
EBIC_STATE_NODE = "MightyEBICState"
EBIC_DATA_NODE = "MightyEBICData"


class MightyEbic(Detector):
    def __init__(self, name: str, role: str, channel: int, url: str, namespace: str, **kwargs):
        """
        Initialise the EBIC controller
        :param name (str): The name of the device configured through the configuration file
        :param role (str): The role of the device configured through the configuration file
        :param channel (int): The channel the device should use for hw triggering.
        :param url (str): The url address to use with the OPC UA protocol
        :param namespace (str): The object name identifier to use for the OPC UA protocol
        """
        super().__init__(name, role, **kwargs)
        # for debug, eventually -> get resolution from the controller/detector
        max_res = (4096, 4096)
        min_res = (1, 1)

        self.acq_data: Optional[ndarray] = None
        self.idx: Optional[int] = None
        self.sub_handler = SubHandler()
        self.t_opc_connection: Optional[threading.Thread] = None
        self.ebic_scan_controller: Optional[Node] = None
        self._name = name
        self._channel = channel
        self._namespace = namespace
        self._url = url
        self._opc_client = Client(url=url)
        # server is only used for simulation of the opcServer if it is non-existent
        self._opc_server: Optional[MightyEbicSimulator] = None
        self._error_msg: Optional[str] = None
        self._t_simserver: Optional[threading.Thread] = None
        self._connected = False
        self._scan_time = 0.0
        self._scan_state = "Disconnected"
        self._trigger_mode = False
        self._opc_client_running = False

        try:
            self._start_opcclient_thread(60)
        except HwError:
            raise HwError(f"Cannot connect to device -> {self._error_msg}")

        # The number of samples that are measured at each pixel and averaged.
        # higher spp will force a higher dwell_time as well as increasing scan_time
        self.samplesPerPixel = model.IntEnumerated(1,
                                                   set(range(1, MAX_SAMPLES_PER_PIXEL + 1)),
                                                   setter=self.on_spp_change)
        self.numberOfChannels = model.IntEnumerated(2,
                                                    set(range(1, MAX_NUMBER_OF_CHANNELS + 1)),
                                                    setter=self.on_chan_num_change)
        # TODO: add comment explaining diff between oversampling and samples per pixel
        self.oversampling = model.IntEnumerated(0, {0, 2, 4, 8, 16, 32, 64})
        self.oversampling.subscribe(self.on_oversampling_change)
        # the resolution of the scanner of the EBIC controller
        self.resolution = model.ResolutionVA(max_res, (min_res, max_res), unit="px")
        self.repetition = model.TupleVA((max_res[1], max_res[1]), unit="px")
        self.dwellTime = model.FloatContinuous(1e-6, (0.1e-6, 1000), unit="s")

        #self.sub_handler = ScanSubHandler()  # SubHandler()
        self.data = EBICDataFlow(self)
        self._acquisition_thread: Optional[threading.Thread] = None
        self._acquisition_lock = threading.Lock()
        # self._acquisition_init_lock = threading.Lock()
        self._acquisition_must_stop = threading.Event()

        # Special event to request software unblocking on the scan
        self.softwareTrigger = model.Event()

        self._metadata[model.MD_DET_TYPE] = model.MD_DT_NORMAL
        self._metadata[model.MD_HW_VERSION] = self._hwVersion
        self._metadata[model.MD_SW_VERSION] = self._swVersion

    def _start_opcclient_thread(self, timeout: int=60):
        """
        Start the receiver thread, which keeps listening to the response of the command port.
        """
        if "localhost" in self._url:
            # if the device should be simulated, start a simulator server first
            self._t_simserver = threading.Thread(target=self._start_opc_simserver)
            self._t_simserver.start()
            while self._scan_state != STATE_NAME_IDLE:
                time.sleep(0.1)

        self.t_opc_connection = threading.Thread(target=self._start_opc_client)
        self.t_opc_connection.start()

        # # wait just a moment to check if it's alive
        # counter = 0
        # while not self._opc_client_running and counter < timeout:
        #     time.sleep(1)
        #     counter += 1
        #     logging.debug("Trying to connect to the EBIC controller")
        # if counter > timeout:
        #     raise ConnectionError()

        logging.info(f"Connected to EBIC controller (url:{self._url} namespace:{self._namespace})")
        self._connected = True

    def _start_opc_client(self):
        try:
            asyncio.run(self.connect_to_controller())
        except ConnectionError as ex:
            self._error_msg = ex
        except Exception as ex:
            raise Exception(ex)

    def _start_opc_simserver(self):
        try:
            asyncio.run(self.connect_to_simserver())
        except ConnectionError as ex:
            self._error_msg = ex
        except Exception as ex:
            raise Exception(ex)

    async def connect_to_simserver(self):
        """
        Setup and operation example of OPC server. Simulates transmitting data via a variable
        and changing the state of the state machine with an attached event generator.
        """
        try:
            self._opc_server = MightyEbicSimulator(self._url, self._namespace, self)
            await self._opc_server.setup()
            current_state_node = await self._opc_server.state_machine_node.get_child(
                f"0:CurrentState"
            )

            logging.info("Starting server!")

            async with self._opc_server.server:
                await asyncio.sleep(1)
                current_state = await current_state_node.read_value()
                logging.info(f"Current state before starting is {current_state.Text}")
                while True:
                    await asyncio.sleep(1)
                    new_state = await current_state_node.read_value()
                    await self._opc_server.server.nodes.objects.call_method(
                        f"{self._opc_server.idx}:change_state",
                        new_state.Text)
                    self._scan_state = new_state.Text
        except ConnectionError as ex:
            self._error_msg = ex
        except Exception as ex:
            raise Exception(ex)

    async def get_node_id_by_browse_name(self, browse_name: str) -> ua.NodeId | None:
        # Assuming root node is the starting point for browsing
        objects_node = await self._opc_client.nodes.root.get_child("0:Objects")

        # Browse recursively starting from the objects node
        nodes_to_browse = [objects_node]
        while nodes_to_browse:
            current_node = nodes_to_browse.pop()
            children = await current_node.get_children()
            for child in children:
                # Get the BrowseName attribute of the child node
                child_browse_name = await child.read_browse_name()
                if child_browse_name.Name == browse_name:
                    nodes_to_browse.append(child)
                    return child.nodeid
        return None

    async def connect_to_controller(self):
        try:
            async with self._opc_client:
                logging.info("Client connected")
                await self.create_state_subscription()
                self._opc_client_running = True
                while self._connected:
                    current_state = self._scan_state
                    await asyncio.sleep(1)
                    # only fire a change state when _scan_state value actually changed
                    # it will generate a LOT of unnecessary lines in the logfile.
                    if current_state != self._scan_state:
                        # only acquire data from the scan controller after a transition from running -> stopped
                        if current_state == STATE_NAME_RUNNING and self._scan_state == STATE_NAME_STOPPED:
                            # check for data
                            logging.debug(f"requesting data after state change from {current_state} to {self._scan_state}")
                            await self.retrieve_acquired_data()
                            # set state to idle again after retrieving the data
                            self._scan_state = STATE_NAME_IDLE

        except (ConnectionError, ua.UaError) as e:
            raise ConnectionError(str(e) + " -> Client disconnected")

    async def create_state_subscription(self) -> int:
        """Creates a subscription for the change state event in the state machine."""
        current_state_node = await self._opc_server.state_machine_node.get_child(
            "0:CurrentState",
        )

        subscription = await self._opc_client.create_subscription(
            period=500,
            handler=self.sub_handler,
        )
        sub = await subscription.subscribe_data_change(current_state_node)

        return sub

    async def get_controller_state(self):
        return self._scan_state

    async def change_state(self, new_state: str) -> None:
        """
        Change state of server state machine based on state name.
        new_state (str): state name constant to change to
        """
        await self._opc_client.nodes.objects.call_method(f"{self.idx}:change_state", new_state)

    def start_acquire(self, callback):
        with self._acquisition_lock:
            self._wait_acquisition_stopped()
            self._acquisition_thread = threading.Thread(target=self._acquire_thread,
                                                        name="IndependentDetector acquire flow thread",
                                                        args=(callback,))
            self._acquisition_thread.start()

    def stop_acquire(self):
        with self._acquisition_lock:
            self._acquisition_must_stop.set()

    def _wait_acquisition_stopped(self) -> None:
        """
        Waits until the acquisition thread is fully finished if (and only if) it was requested to stop.
        """
        if self._acquisition_must_stop.is_set():
            logging.debug("Waiting for thread to stop.")
            self._acquisition_thread.join(10)  # 10s timeout for safety
            if self._acquisition_thread.is_alive():  # Should never happen
                logging.error("Failed to stop the acquisition thread after 10s")
                # No idea how we could recover... so we just keep going as if everything is fine
            # ensure it's not set, even if the thread died prematurely
            self._acquisition_must_stop.clear()

    def _acquire_thread(self, callback: Callable[[model.DataArray], None]) -> None:
        """
        Thread that simulates the acquisition. It imitates the delay according to the dwell time
        and resolution and provides the new generated output to the Dataflow.
        """
        try:
            while not self._acquisition_must_stop.is_set():
                dwelltime = self.dwellTime.value
                resolution = self.resolution.value
                duration = numpy.prod(resolution) * dwelltime
                self.data._waitSync()
                # start scan OPCUA
                if self._t_simserver:
                    img = self._simulate_image()
                if self._acquisition_must_stop.wait(duration):
                    break
                callback(img)

                if not self._continuous and not self.data._sync_event:
                    logging.debug("Stopping acquisition as DataFlow is not synchronized and 1 data acquired")
                    return
        except Exception:
            logging.exception("Unexpected failure during image acquisition")
        finally:
            logging.debug("Acquisition thread closed")
            self._acquisition_must_stop.clear()

    def get_dwell_time(self, delay: float = 5e-8, trigger: bool = False) -> float:
        """
        :param delay (float, optional): Number of clock cycles, this is a variable delay that
            allows for the signal to reach a steady state before it is measured, default -> 0.05μs
        :param trigger (bool): Define if trigger mode is active with respect to DAC update
        :return (float): Dwell time in μs
        """
        # calculation of delay cycles (delay step is set at 10e-9 default)
        delay = numpy.uint32(delay/10e-9)

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
        if self.oversampling.value >= 2:
            WAIT = OS_scalar * self.oversampling.value
        # for each conversion amount of time it takes clock out each channel
        CH_T = CH_scalar * self.numberOfChannels.value

        # total ADC READ based on CH and OS
        ADCREAD = 120 + CH_T + WAIT

        # total number of conversions at a pixel
        LOOP3 = sample_Overhead + samples_scalar * self.samplesPerPixel.value + ADCREAD * self.samplesPerPixel.value

        # time for each DAC update
        # DacUpdate not needed in Trigger mode
        if trigger:
            DACUPDATE = 0
        else:
            DACUPDATE = LOADDAC_OV + DAC_var * delay + DACUP_OV

        Dwell_us = (LOOP3 + DACUPDATE) / 1000.0  # convert to us

        return Dwell_us

    def get_scan_time(self) -> float:
        """
        Takes the dwell time and scan size to calculate scan time in seconds
        :return (float): Scan time in seconds
        """
        LOOP2_OV = 40
        LOOP1_OV = 40
        SETUP = 1025  # Overhead to set up scan

        LOOP2 = (self.dwellTime.value * 1000 + LOOP2_OV) * self.repetition.value[0] + 5  # in nanoseconds
        LOOP1 = (LOOP2 + LOOP1_OV) * self.repetition.value[1]

        ScanTime_ns = SETUP + LOOP1  # scantime in nanoseconds
        ScanTime_s = ScanTime_ns / 1.0e9

        return ScanTime_s

    @uamethod
    async def start_scan(self):
        """
        Starts a scan with the EBIC scan controlbox.
        Send fixed settings to the server/client to initiate scan.
        -> 0:Root,0:Objects,2:ebic_scan_box
        """
        # FIXME How do we use the dwell time here
        # self._dwell_time = self.get_dwell_time()
        self._scan_time = self.get_scan_time()

        logging.info("Starting EBIC scan")
        result = await self.ebic_scan_controller.call_method(
            f"{self.idx}:StartScanOPCUA",
            self.oversampling.value,
            self.numberOfChannels.value,
            self.samplesPerPixel.value, # drop the VA, compute it base on DT + OS rate
            self.dwellTime.value, # = delay and could be set to default of 1 (clock cycle)
            self.repetition.value[0],
            self.repetition.value[1],
            self._trigger_mode,
            self._t_simserver is not None,
        )
        logging.info(f"EBIC scan finished with method call result {result}")
        await self.change_state(STATE_NAME_STOPPED)

    def stop_scan(self):
        # this function is to be used in the special SPARC acq
        pass

    async def retrieve_acquired_data(self):
        """
        Reads a variable from the server.
        """
        data = await self._opc_client.nodes.root.get_child(["0:Objects", f"{self.idx}:{DATA_VAR_NAME}"])
        value = await data.read_value()
        try:
            self.acq_data = numpy.array(list(value), dtype=numpy.float64)
        except ValueError:
            logging.error("Unable to convert the retrieved data to a numpy array.")

    def on_spp_change(self, value):
        return value

    def on_chan_num_change(self, value):
        return value

    def on_oversampling_change(self, value):
        return value

    def terminate(self):
        super().terminate()
        # stop the simulator server
        if self._t_simserver:
            self._t_simserver.terminated = True
            self._t_simserver.join()

        self._connected = False
        self.t_opc_connection.terminated = True
        self.t_opc_connection.join()


class MightyEbicSimulator:
    """
    OPC Server class: This class is responsible for setting up the server and the state machine
    """
    def __init__(self, url: str, namespace: str, parent_det: MightyEbic):
        """Initialize the OPC Server class."""
        self.server = Server()
        self.idx = None
        self.state_machine = None
        self.state_machine_node = None
        self.ebic_controller_node = None
        self.ebic_data_node = None
        #self.current_state_var = None
        self.state_change_event_gen = None
        self.states = {}
        self.transitions = {}
        self._url = url
        self._namespace = namespace
        self._parent_det = parent_det

        self.inargOS = ua.Argument()
        self.inargCH = ua.Argument()
        self.inargSamp = ua.Argument()
        self.inargDel = ua.Argument()
        self.inargPF = ua.Argument()
        self.inargPS = ua.Argument()
        self.inargTR = ua.Argument()
        self.inargSIM = ua.Argument()
        self.outarg = ua.Argument()

    async def setup(self) -> None:
        """Call server async setup functions."""
        await self.server.init()
        self.server.set_endpoint(self._url)

        # set up our own namespace, not really necessary but should as spec
        self.idx = await self.server.register_namespace(self._namespace)
        await self.setup_state_machine()
        await self.setup_controller()
        await self.setup_data_node()
        await self.setup_events()
        await self.setup_scan_variables()
        self.setup_scan_args()

    async def setup_controller(self) -> None:
        """
        Search and assign the connected EBIC controller to the opcua client.
        This is called asynchronous in connect_to_controller.
        """
        self.ebic_controller_node = await self.server.nodes.objects.add_object(
            self.idx,
            EBIC_CONTROLLER_NODE,
        )
        # Add the method to the EBIC controller node
        await self.ebic_controller_node.add_method(
            ua.NodeId("StartScanOPCUA", self.idx),
            ua.QualifiedName("StartScanOPCUA", self.idx),
            self._parent_det.start_scan,
            [
                self.inargOS,
                self.inargCH,
                self.inargSamp,
                self.inargDel,
                self.inargPF,
                self.inargPS,
                self.inargTR,
                self.inargSIM,
            ],
            [ self.outarg ],
        )

    async def setup_data_node(self):
        self.ebic_data_node = await self.server.nodes.objects.add_object(
            self.idx,
            EBIC_DATA_NODE)

        data_var = await self.ebic_data_node.add_variable(
            self.idx,
            DATA_VAR_NAME,
            (numpy.ones((3000, 3000), dtype=numpy.float64)).tolist())
        # Set the variable to be writable by clients
        await data_var.set_writable()

    async def setup_state_machine(self) -> None:
        """Set up the state machine for the server."""
        self.state_machine = StateMachine(
            self.server,
            self.server.nodes.objects,
            self.idx,
            EBIC_STATE_NODE,
        )

        # install the state machine
        await self.state_machine.install(optionals=True)

        # create states
        idle_state = State(STATE_ID_IDLE, STATE_NAME_IDLE, 1, node=None)
        self.states[STATE_NAME_IDLE] = idle_state
        await self.state_machine.add_state(idle_state, state_type=ua.NodeId(2309, 0))

        running_state = State(STATE_ID_RUNNING, STATE_NAME_RUNNING, 2)
        self.states[STATE_NAME_RUNNING] = running_state
        await self.state_machine.add_state(running_state, state_type=ua.NodeId(2307, 0))

        trigger_state = State(STATE_ID_TRIGGER, STATE_NAME_TRIGGER, 3)
        self.states[STATE_NAME_TRIGGER] = trigger_state
        await self.state_machine.add_state(trigger_state)

        stop_state = State(STATE_ID_STOPPED, STATE_NAME_STOPPED, 4)
        self.states[STATE_NAME_STOPPED] = stop_state
        await self.state_machine.add_state(stop_state)

        error_state = State(STATE_ID_ERROR, STATE_NAME_ERROR, 5)
        self.states[STATE_NAME_ERROR] = error_state
        await self.state_machine.add_state(error_state)

        await self.state_machine.change_state(idle_state)

        inarg_new_state = ua.Argument()
        inarg_new_state.Name = "new_state"
        inarg_new_state.DataType = ua.NodeId(ua.ObjectIds.String)
        inarg_new_state.ValueRank = -1
        inarg_new_state.ArrayDimensions = []
        inarg_new_state.Description = ua.LocalizedText("New State")

        await self.server.nodes.objects.add_method(
            ua.NodeId("change_state", self.idx),
            ua.QualifiedName("change_state", self.idx),
            self.change_state,
            [inarg_new_state],
            [],
        )

    async def setup_scan_variables(self) -> None:
        """Set up the scan variables for the server."""
        OS = await self.ebic_controller_node.add_variable(self.idx, "oversampling", 0)
        await OS.set_writable()

        CH = await self.ebic_controller_node.add_variable(self.idx, "channels", 2)
        await CH.set_writable()

        samples = await self.ebic_controller_node.add_variable(self.idx, "samples", 1)
        await samples.set_writable()

        delay = await self.ebic_controller_node.add_variable(self.idx, "delay", 5e-8)
        await delay.set_writable()

        points_fast = await self.ebic_controller_node.add_variable(
            self.idx, "points_fast", 1000
        )
        await points_fast.set_writable()

        points_slow = await self.ebic_controller_node.add_variable(
            self.idx, "points_slow", 1000
        )
        await points_slow.set_writable()

        trigger = await self.ebic_controller_node.add_variable(self.idx, "trigger", False)
        await trigger.set_writable()

        simulate = await self.ebic_controller_node.add_variable(self.idx, "simulate", False)
        await simulate.set_writable()

        dwellTime = await self.ebic_controller_node.add_variable(self.idx, "dwellTime", 0)
        await dwellTime.set_writable()

        scanTime = await self.ebic_controller_node.add_variable(self.idx, "scanTime", 0)
        await scanTime.set_writable()

    def setup_scan_args(self):
        """Define the arguments for the method."""
        self.inargOS.Name = "oversampling"
        self.inargOS.DataType = ua.NodeId(ua.ObjectIds.Int64)
        self.inargOS.ValueRank = -1
        self.inargOS.ArrayDimensions = []
        self.inargOS.Description = ua.LocalizedText("Oversampling")

        self.inargCH.Name = "channels"
        self.inargCH.DataType = ua.NodeId(ua.ObjectIds.Int64)
        self.inargCH.ValueRank = -1
        self.inargCH.ArrayDimensions = []
        self.inargCH.Description = ua.LocalizedText("Channels")

        self.inargSamp.Name = "samples"
        self.inargSamp.DataType = ua.NodeId(ua.ObjectIds.Int64)
        self.inargSamp.ValueRank = -1
        self.inargSamp.ArrayDimensions = []
        self.inargSamp.Description = ua.LocalizedText("Samples")

        self.inargDel.Name = "delay"
        self.inargDel.DataType = ua.NodeId(ua.ObjectIds.Float)
        self.inargDel.ValueRank = -1
        self.inargDel.ArrayDimensions = []
        self.inargDel.Description = ua.LocalizedText("Delay")

        self.inargPF.Name = "points_fast"
        self.inargPF.DataType = ua.NodeId(ua.ObjectIds.Int64)
        self.inargPF.ValueRank = -1
        self.inargPF.ArrayDimensions = []
        self.inargPF.Description = ua.LocalizedText("Points Fast")

        self.inargPS.Name = "points_slow"
        self.inargPS.DataType = ua.NodeId(ua.ObjectIds.Int64)
        self.inargPS.ValueRank = -1
        self.inargPS.ArrayDimensions = []
        self.inargPS.Description = ua.LocalizedText("Points Slow")

        self.inargTR.Name = "Trigger"
        self.inargTR.DataType = ua.NodeId(
            ua.ObjectIds.Boolean
        )  # was ua.NodeId(ua.ObjectIds.Boolean
        self.inargTR.ValueRank = -1
        self.inargTR.ArrayDimensions = []
        self.inargTR.Description = ua.LocalizedText("Trigger")  # was ua.LocalizedText("TRIGGER")

        self.inargSIM.Name = "Simulate"
        self.inargSIM.DataType = ua.NodeId(
            ua.ObjectIds.Boolean
        )  # was ua.NodeId(ua.ObjectIds.Boolean)
        self.inargSIM.ValueRank = -1
        self.inargSIM.ArrayDimensions = []
        self.inargSIM.Description = ua.LocalizedText("Simulate")

        self.outarg.Name = "Result"
        self.outarg.DataType = ua.NodeId(ua.ObjectIds.Int64)
        self.outarg.ValueRank = -1
        self.outarg.ArrayDimensions = []
        self.outarg.Description = ua.LocalizedText("Result of the method call")

    async def setup_events(self) -> None:
        """Set up the events for the server."""
        event_type = await self.server.create_custom_event_type(
            self.idx,
            "StateChangeEvent",
            ua.ObjectIds.BaseEventType,
            [("state", ua.VariantType.String)],
        )

        #state_machine_node = await self.server.nodes.objects.get_child(

        self.state_machine_node = await self.server.nodes.objects.get_child(
            f"{self.idx}:{EBIC_STATE_NODE}",
        )

        self.state_change_event_gen = await self.server.get_event_generator(
            event_type,
            self.state_machine_node,
        )

    @uamethod
    async def change_state(self, parent, new_state) -> None:
        """Change the state of the server.

        TODO: connect state with real hardware client.
        """
        if new_state in self.states:
            await self.state_machine.change_state(self.states[new_state])
            # await self.current_state_var.write_value(new_state)
        else:
            logging.error(f"Invalid state: {new_state}")

        # event triggered here is caught and reacted to on the client side
        await self.state_change_event_gen.trigger(message=f"State changed to {new_state}")

    async def create_variable(self, name: str, data: numpy.ndarray) -> None:
        """Creates a variable in the server and writes the input data to it.

        Takes in numpy array and converts it to list and then to bytes,
        which allows for variable creation for any type of data.

        Args:
            name (str): The name of the variable to create
            data (np.ndarray): The input data array to transfer
        """
        data_var = await self.server.nodes.objects.add_variable(
            self.idx,
            name,
            bytes(data.tolist()),
        )
        await data_var.set_writable()


class EBICDataFlow(model.DataFlow):
    def __init__(self, detector):
        """
        detector (semcomedi.Detector): the detector that the dataflow corresponds to
        sem (semcomedi.SEMComedi): the SEM
        """
        model.DataFlow.__init__(self)
        self.component = weakref.ref(detector)

        self._sync_event = None  # event to be synchronised on, or None
        self._evtq = None  # a Queue to store received events (= float, time of the event)

    # start/stop_generate are _never_ called simultaneously (thread-safe)
    def start_generate(self):
        try:
            self.component().start_acquire(self.notify)
        except ReferenceError:
            # sem/component has been deleted, it's all fine, we'll be GC'd soon
            pass

    def stop_generate(self):
        try:
            self.component().stop_acquire()
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
        Block until the Event on which the dataflow is synchronised has been
          received. If the DataFlow is not synchronised on any event, this
          method immediately returns
        """
        if self._sync_event:
            self._evtq.get()


class SubHandler:
    """Subscription Handler. To receive events from server for a subscription."""
    def __init__(self):
        self.state = "disconnected"

    def datachange_notification(self, node: Node, state: str) -> None:
        """Callback for asyncua Subscription.

        Args:
            node (Node): node of the state machine
            state (str): state the machine is being changed to
        """
        logging.info(f"Changing state to {state.Text} at node {node}")

        if state.Text == "Running":
            self.state = state
        elif state.Text == "Idle":
            self.state = state
        elif state.Text == "Stopped":
            self.state = state
            if node.read_display_name() == EBIC_STATE_NODE:
                pass
