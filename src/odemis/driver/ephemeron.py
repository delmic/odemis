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
import math
import threading
import time
import queue
from asyncio import AbstractEventLoop

import asyncio
from typing import Callable, Optional

import numpy
from asyncua import Client, Server, Node, ua
from asyncua.common.methods import uamethod
from asyncua.common.statemachine import State, StateMachine
from numpy import ndarray

from odemis import model
from odemis.model import Detector, oneway, MD_ACQ_DATE

# constants
STATE_ID_IDLE = "server-idle"
STATE_ID_RUNNING = "server-running"
STATE_ID_TRIGGER = "server-triggered"
STATE_ID_CHECKING_DWELL = "server-checking-dwell"
STATE_ID_STOPPED = "server-stopped"
STATE_ID_ERROR = "server-error"

STATE_NAME_IDLE = "IDLE"
STATE_NAME_BUSY = "BUSY"
STATE_NAME_TRIGGER = "TRIGGER"
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
NAMESPACE_INDEX = 0
DATA_VAR_NAME = "MightyEBICDataArray"
DEFAULT_TIMEOUT = 30  #s
EBIC_CONTROLLER_NODE = "MightyEBICController"
EBIC_STATE_NODE = "MightyEBICState"
EBIC_INFO_NODE = "MightyEBICInfo"


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
        max_res = (4096, 4096) # TODO make it a constant
        min_res = (1, 1)

        self.acq_data: Optional[ndarray] = None
        self._name = name
        self._channel = channel
        self._namespace = namespace
        self._url = url
        self._opc_client: Optional[UaClient] = None
        # server is only used for simulation of the opcServer if it is non-existent
        self._opc_server_sim: Optional[MightyEBICSimulator] = None
        self._error_msg: Optional[str] = None
        self._t_simserver: Optional[threading.Thread] = None
        self._trigger_mode = False

        if "localhost" in self._url:
            # if the device should be simulated, start a simulator server first
            self._opc_server_sim = MightyEBICSimulator(self._url, self._namespace, self)
            self._t_simserver = threading.Thread(target=self._start_opc_simserver)
            self._t_simserver.start()
            while not self._opc_server_sim.connected:
                time.sleep(0.1)

        # fire up the client needed for the opcua communication with the server
        self._opc_client = UaClient(self._url, DEFAULT_TIMEOUT)  # pass a 30s server request time-out
        asyncio.run(self._opc_client.initialize_client())
        #self._opc_client.create_state_subscription()  # also initializes self._scan_state

        # The number of samples that are measured at each pixel and averaged.
        # higher spp will force a higher dwell_time as well as increasing scan_time
        self.samplesPerPixel = model.IntEnumerated(1,
                                                   set(range(1, MAX_SAMPLES_PER_PIXEL + 1)),
                                                   setter=self.on_spp_change)
        self.numberOfChannels = model.IntEnumerated(1,
                                                    set(range(1, MAX_NUMBER_OF_CHANNELS + 1)),
                                                    setter=self.on_chan_num_change)
        # Oversampling is a digital low-pass filter that removes high-frequency
        # noise and sends back a single clean 18-bit value
        self.oversampling = model.IntEnumerated(0, {0, 2, 4, 8, 16, 32, 64})
        self.oversampling.subscribe(self.on_oversampling_change)
        # the resolution of the scanner of the EBIC controller
        self.resolution = model.ResolutionVA(max_res, (min_res, max_res), unit="px")
        #self.repetition = model.TupleVA((max_res[1], max_res[1]), unit="px")
        # VA's ->  SPP need to go will be dwell time -> from the GUI acquisition to driver dwell time (set dwell time)
        # find good spp which makes the dt a bit shorter than requested by the user in the stream
        self.dwellTime = model.FloatContinuous(1e-5, (0.1e-6, 1000), unit="s")

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

    def _start_opc_simserver(self):
        try:
            asyncio.run(self._opc_server_sim.connect_to_server())
        except ConnectionError as ex:
            self._error_msg = ex
        except Exception as ex:
            raise Exception(ex)

    def start_acquire(self, callback):
        with self._acquisition_lock:
            self._wait_acquisition_stopped()
            self._acquisition_thread = threading.Thread(target=self._acquire_thread,
                                                        name="EBIC acquire flow thread")

            # res = self.resolution.value
            # scan_time = asyncio.run(self._opc_client.get_scan_time(self.dwellTime.value, res[0], res[1]))
            # end_time = time.time() + 60 + scan_time

            self._acquisition_thread.start()
            # wait just a little bit to let the statemachine state change
            #time.sleep(1)

            # while self._opc_client.controller_state != STATE_NAME_IDLE:
            #     if time.time() > end_time:
            #         raise TimeoutError("Scan time took more than expected")
            #     if self._acquisition_must_stop.wait(0.1):
            #         asyncio.run(self._opc_client.stop_scan())
            #         #self._wait_acquisition_stopped()
            #         return

    def _acquire_thread(self) -> None:
        """
        Thread that simulates the acquisition. It imitates the delay according to the dwell time
        and resolution and provides the new generated output to the Dataflow.
        """
        try:
            self.data._waitSync()
            res = self.resolution.value
            md = self.getMetadata()
            md[MD_ACQ_DATE] = time.time()

            scan_time = self._opc_client.get_scan_time(self.dwellTime.value, res[0], res[1])
            time.sleep(0.5)
            end_time = time.time() + 60 + scan_time

            f = self._opc_client.start_scan(self.dwellTime.value, 0, 1, 0, res[0], res[1])
            time.sleep(0.5)

            while self._opc_client.controller_state != STATE_NAME_IDLE:
                if time.time() > end_time:
                    raise TimeoutError("Scan time took more than expected")
                if self._acquisition_must_stop.wait(0.1):
                    #self._opc_client.stop_scan()
                    f.cancel()
                    return

            f.result()

            da = asyncio.run(self.read_data(res, md))
            self.data.notify(da)

        except Exception:
            logging.exception("Unexpected failure during image acquisition")
        finally:
            logging.debug("Acquisition thread closed")
            # self._acquisition_must_stop.clear()

    def stop_acquire(self):
        # stop the acquisition
        with self._acquisition_lock:
            self._acquisition_must_stop.set()

    def _wait_acquisition_stopped(self):
        """
        Waits until the acquisition thread is fully finished _iff_ it was requested to stop.
        """
        # "if" is to not wait if it's already finished
        if self._acquisition_must_stop.is_set():
            logging.debug("Waiting for thread to stop.")
            self._acquisition_thread.join(10)  # 10s timeout for safety
            if self._acquisition_thread.is_alive():
                logging.exception("Failed to stop the acquisition thread")
                # Now let's hope everything is back to normal...
            # ensure it's not set, even if the thread died prematurely
            self._acquisition_must_stop.clear()

    async def read_data(self, resolution, md):
        # if scan is successful there should be data in EBIC_NODE_INFO
        async with self._opc_client.client:
            scan_result_node = await self._opc_client.ebic_info_node.get_child(f"{NAMESPACE_INDEX}:scan_result")
            raw_data = await scan_result_node.read_value()

        # reshape the data first -> Data array ND array + MD
        raw_arr = numpy.array(raw_data)
        raw_arr = raw_arr.reshape(resolution[0], resolution[1])

        da = model.DataArray(raw_arr, md)

        return da

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

        self._opc_client.close_connection()
        # self._connected = False
        # self.t_opc_connection.terminated = True
        # self.t_opc_connection.join()


class MightyEBICSimulator(Server):
    """
    OPC Server class: This class is responsible for setting up the server and the state machine
    """
    def __init__(self, url: str, namespace: str, parent_det: MightyEbic):
        """Initialize the OPC Server class."""
        #self.server = Server()
        super().__init__()
        self.state_machine: Optional[StateMachine] = None
        self.state_machine_node: Optional[Node] = None
        self.ebic_controller_node: Optional[Node] = None
        self.ebic_data_node: Optional[Node] = None
        self.connected = False
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
        await self.init()
        self.set_endpoint(self._url)

        # set up our own namespace, not really necessary but should as spec
        await self.register_namespace(self._namespace)
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
        self.ebic_controller_node = await self.nodes.objects.add_object(
            NAMESPACE_INDEX,
            EBIC_CONTROLLER_NODE,
        )
        # Add the method to the EBIC controller node
        await self.ebic_controller_node.add_method(
            ua.NodeId("StartScanOPCUA", NAMESPACE_INDEX),
            ua.QualifiedName("StartScanOPCUA", NAMESPACE_INDEX),
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
        self.ebic_data_node = await self.nodes.objects.add_object(
            NAMESPACE_INDEX,
            EBIC_INFO_NODE)

        data_var = await self.ebic_data_node.add_variable(
            NAMESPACE_INDEX,
            DATA_VAR_NAME,
            (numpy.ones((3000, 3000), dtype=numpy.float64)).tolist())
        # Set the variable to be writable by clients
        await data_var.set_writable()

    async def setup_state_machine(self) -> None:
        """Set up the state machine for the server."""
        self.state_machine = StateMachine(
            self,
            self.nodes.objects,
            NAMESPACE_INDEX,
            EBIC_STATE_NODE,
        )

        # install the state machine
        await self.state_machine.install(optionals=True)

        # create states
        idle_state = State(STATE_ID_IDLE, STATE_NAME_IDLE, 1, node=None)
        self.states[STATE_NAME_IDLE] = idle_state
        await self.state_machine.add_state(idle_state, state_type=ua.NodeId(2309, 0))

        running_state = State(STATE_ID_RUNNING, STATE_NAME_BUSY, 2)
        self.states[STATE_NAME_BUSY] = running_state
        await self.state_machine.add_state(running_state, state_type=ua.NodeId(2307, 0))

        trigger_state = State(STATE_ID_TRIGGER, STATE_NAME_TRIGGER, 3)
        self.states[STATE_NAME_TRIGGER] = trigger_state
        await self.state_machine.add_state(trigger_state)

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

    async def setup_scan_variables(self) -> None:
        """Set up the scan variables for the server."""
        OS = await self.ebic_controller_node.add_variable(NAMESPACE_INDEX, "oversampling", 0)
        await OS.set_writable()

        CH = await self.ebic_controller_node.add_variable(NAMESPACE_INDEX, "channels", 2)
        await CH.set_writable()

        samples = await self.ebic_controller_node.add_variable(NAMESPACE_INDEX, "samples", 1)
        await samples.set_writable()

        delay = await self.ebic_controller_node.add_variable(NAMESPACE_INDEX, "delay", 5e-8)
        await delay.set_writable()

        points_fast = await self.ebic_controller_node.add_variable(
            NAMESPACE_INDEX, "points_fast", 1000
        )
        await points_fast.set_writable()

        points_slow = await self.ebic_controller_node.add_variable(
            NAMESPACE_INDEX, "points_slow", 1000
        )
        await points_slow.set_writable()

        trigger = await self.ebic_controller_node.add_variable(NAMESPACE_INDEX, "trigger", False)
        await trigger.set_writable()

        simulate = await self.ebic_controller_node.add_variable(NAMESPACE_INDEX, "simulate", False)
        await simulate.set_writable()

        dwellTime = await self.ebic_controller_node.add_variable(NAMESPACE_INDEX, "dwellTime", 0)
        await dwellTime.set_writable()

        scanTime = await self.ebic_controller_node.add_variable(NAMESPACE_INDEX, "scanTime", 0)
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
        event_type = await self.create_custom_event_type(
            NAMESPACE_INDEX,
            "StateChangeEvent",
            ua.ObjectIds.BaseEventType,
            [("state", ua.VariantType.String)],
        )

        #state_machine_node = await self.server.nodes.objects.get_child(

        self.state_machine_node = await self.nodes.objects.get_child(
            f"{NAMESPACE_INDEX}:{EBIC_STATE_NODE}",
        )

        self.state_change_event_gen = await self.get_event_generator(
            event_type,
            self.state_machine_node,
        )

    async def connect_to_server(self):
        """
        Setup and operation example of OPC server. Simulates transmitting data via a variable
        and changing the state of the state machine with an attached event generator.
        """
        try:
            await self.setup()
            current_state_node = await self.state_machine_node.get_child(
                f"0:CurrentState"
            )

            logging.info("Starting server!")

            async with self:
                #await asyncio.sleep(1)
                current_state = await current_state_node.read_value()
                logging.info(f"Current state before starting is {current_state.Text}")
                self.connected = True
                while True:
                    await asyncio.sleep(1)
                    new_state = await current_state_node.read_value()
                    await self.nodes.objects.call_method(
                        f"{NAMESPACE_INDEX}:change_state",
                        new_state.Text)
                    self._parent_det.scan_state = new_state
        except ConnectionError as ex:
            self._parent_det._error_msg = ex
        except Exception as ex:
            raise Exception(ex)

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


class EBICDataFlow(model.DataFlow):
    def __init__(self, detector):
        """
        detector (semcomedi.Detector): the detector that the dataflow corresponds to
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
    def __init__(self, url, timeout):
        self.client = Client(url=url, timeout=timeout)
        self._url = url
        self._ebic_state_node: Optional[Node] = None
        self.ebic_info_node: Optional[Node] = None
        self._ebic_controller_node: Optional[Node] = None
        self._controller_state = ua.LocalizedText("Disconnected", "en-US")
        self._loop: Optional[AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None

    async def initialize_client(self):
        async with self.client:
            objects_node = await self.client.nodes.root.get_child(f"{NAMESPACE_INDEX}:Objects")
            state_node = await objects_node.get_child(f"{NAMESPACE_INDEX}:{EBIC_STATE_NODE}")
            self.ebic_info_node = await objects_node.get_child(f"{NAMESPACE_INDEX}:{EBIC_INFO_NODE}")  # needed
            self._ebic_controller_node = await objects_node.get_child(f"{NAMESPACE_INDEX}:{EBIC_CONTROLLER_NODE}")
            self._ebic_state_node = await state_node.get_child(f"{NAMESPACE_INDEX}:CurrentState")

            # Create a new event loop
            self._loop = asyncio.new_event_loop()
            self._loop_thread = threading.Thread(target=self.start_event_loop, args=(self._loop,), daemon=True)
            self._loop_thread.start()

    async def _read_controller_state(self):
        async with self.client:
            ret_val = await self._ebic_state_node.read_value()
        return ret_val

    def _get_controller_state(self):
        f = asyncio.run_coroutine_threadsafe(self._read_controller_state(), self._loop)
        return f.result()

    def get_scan_time(self, dt, res_fast, res_slow) -> float:
        f = asyncio.run_coroutine_threadsafe(self._get_scan_time(dt, res_fast, res_slow) , self._loop)
        return f.result()

    async def _get_scan_time(self, dt, res_fast, res_slow) -> float:
        async with self.client:
            # dt pf, ps
            st = await self.ebic_info_node.call_method(f"{NAMESPACE_INDEX}:calculate_scan_time", dt, res_fast, res_slow)
            return st

    async def guess_samples_per_pixel(self, req_dt: float) -> float:
        """
        Calculate the best guess for the requested dwell time using a range of SPP apply the 50% guess method.
        :param req_dt: The requested dwell time in microseconds
        :return: Closest calculated samples per pixel
        """
        best_spp = 1.0
        req_dt *= 1e6

        for i in range(10):
            # OS, channels, samples, delay
            async with self.client:
                dt = await self.ebic_info_node.call_method(f"{NAMESPACE_INDEX}:calculate_dwell_time", 0, 1, best_spp, 0)
            if math.isclose(dt, req_dt, abs_tol=1e-3):  # abs tolerance of 1 ns
                logging.debug("Samples per pixels match found for requested dwell time")
                break
            elif dt > req_dt:
                best_spp /= dt / req_dt
            elif dt < req_dt:
                best_spp *= req_dt / dt

        return best_spp

    def start_scan(self, req_dt, oversampling, channels, delay, p_fast, p_slow, sim=False):
        f = asyncio.run_coroutine_threadsafe(self._start_scan(req_dt, oversampling, channels, delay, p_fast, p_slow, sim), self._loop)
        return f

    async def _start_scan(self, req_dt, oversampling, channels, delay, p_fast, p_slow, sim=False):
        """
        Starts a scan with the MightyEBIC scan controller.
        Send specific arguments to the server/client with the start_trigger_scan method.
        """
        spp = await self.guess_samples_per_pixel(req_dt)

        async with self.client:
            # run the start scan method on the server
            logging.info(f"Starting EBIC scan, with requested dwell time of {req_dt}s")

            # OS, channels, samples, delay, PF, PS, sim
            await self._ebic_controller_node.call_method(f"{NAMESPACE_INDEX}:start_trigger_scan",
                                                         oversampling,
                                                         channels,
                                                         spp,
                                                         delay,
                                                         p_fast,
                                                         p_slow,
                                                         not sim)  #TODO remove not

            await asyncio.sleep(1)  # wait just a little until the server updated scan_result internally

    def stop_scan(self):
        f = asyncio.run_coroutine_threadsafe(self._stop_scan(), self._loop)
        return f.result()

    async def _stop_scan(self):
        async with self.client:
            # run the stop scan method on the server
            logging.info(f"Stopping EBIC scan..")
            await self._ebic_controller_node.call_method(f"{NAMESPACE_INDEX}:stop_scan")

    def start_event_loop(self, loop):
        asyncio.set_event_loop(loop)
        loop.run_forever()

    @property
    def controller_state(self):
        self._controller_state = self._get_controller_state()
        return self._controller_state.Text

    def close_connection(self):
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join()

