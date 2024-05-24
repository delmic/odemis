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
# this driver is developed for communication with an EBIC controller API
# the EBIC scan controller from Ephemeron will acquire a digital EBIC signal
# to be used simultaneously with other signals such as SE/CL

import logging
import threading
import time
from typing import Any

import asyncio
from asyncua import Client, Server, Node, ua
from asyncua.common.methods import uamethod
from asyncua.common.statemachine import State, StateMachine, Transition
import numpy

import functions
from constants import *
from odemis import model
from odemis.model import Detector


class MightyEbic(Detector):
    def __init__(self, name: str, role: str, device: str, port: int, channel: int, url: str, namespace: str, **kwargs):
        """
        Initialize the EBIC controller
        :param name:
        :param role:
        :param device:
        :param port:
        :param channel:
        :param url:
        :param namespace:
        """
        super().__init__(name, role, **kwargs)
        # for debug, eventually -> get resolution from the controller
        max_res = (1024, 1024)
        min_res = (1, 1)
        max_res_hw = self._transposeSizeFromUser(max_res)

        self._name = name
        self.device = device
        self.port = port
        self._channel = channel
        self._namespace = namespace
        self._client = Client(url=url)
        self._opc_server = None
        self.idx = None
        self.sub_handler = SubHandler()
        self._state = "Disconnected"
        self._translation = (0, 0)
        self._binning = (1, 1)
        self._resolution = max_res_hw
        #self.init_done = False
        self.t_opc_connection = None
        self._error_msg = None
        self._simserver = None
        self.scan_time = None
        self.dwell_time = None

        try:
            self._start_opcclient_thread()
        except ConnectionError:
            raise ConnectionError(self._error_msg)

        # register the VA's
        # higher spp will force a higher scan_time
        self.spp = model.IntEnumerated(1, set(range(1, 11)), setter=self.on_spp_change)
        self.numberOfChannels = model.IntEnumerated(2, set(range(1, 9)), setter=self.on_chan_num_change)
        #self.numberOfChannels.subscribe(self.on_chan_num_change)
        # higher spp will force a higher dwell_time
        self.oversampling = model.IntEnumerated(0, {0, 2, 4, 8, 16, 32, 64}, setter=self.on_oversampling_change)
        #self.oversampling.subscribe(self.on_oversampling_change)
        self.binning = model.ResolutionVA((1, 1), ((1, 1), (1, 1)))
        # the resolution of the scanner of the EBIC controller
        self.resolution = model.ResolutionVA(max_res, (min_res, max_res), unit="px")
        self.repetition = model.TupleVA((1, 1), unit="px", setter=self.on_repetition_change)

        self.data = EBICDataFlow(self)
        #self.softwareTrigger = model.Event()

        self._acquisition_thread = None
        self._acquisition_lock = threading.Lock()
        self._acquisition_init_lock = threading.Lock()
        self._acquisition_must_stop = threading.Event()

        # Thread of the generator
        self._generator = None

        self._hwVersion = "Ephemeron EBIC controlbox S/N: 123456-SIM"
        self._swVersion = "Firmware: simulated-device"
        self._metadata[model.MD_HW_VERSION] = self._hwVersion
        self._metadata[model.MD_SW_VERSION] = self._swVersion

    def _start_opcclient_thread(self):
        """
        Start the receiver thread, which keeps listening to the response of the command port.
        """
        if self.device == "fake":
            # if the device should be simulated, start a simulator server first
            self._simserver = threading.Thread(target=self._start_opc_simserver)
            self._simserver.start()
            while not model.hasVA(self, "serverSim"):
                time.sleep(0.1)

        self.t_opc_connection = threading.Thread(target=self._start_opc_client)
        self.t_opc_connection.start()
        time.sleep(1)  # wait just a moment to check if it's alive

        if self.t_opc_connection.is_alive():
            logging.info(f"Connected to EBIC controller ({self.device})")
        else:
            raise ConnectionError()

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
        Setup and operation example of OPC server. Simulates transmitting data via a variable and changing the
        state of the state machine with an attached event generator.
        """
        try:
            self._opc_server = MightyEbicSimulator()
            await self._opc_server.setup()
            await self._opc_server.create_variable("MyArray", numpy.array([1, 2, 3], dtype=numpy.int32))
            current_state_node = await self._opc_server.state_machine_node.get_child(
                f"{self._opc_server.idx}:current_state"
            )

            myobj = await self._opc_server.server.nodes.objects.add_object(self._opc_server.idx, "EBIC_Controller")
            myvar = await myobj.add_variable(self._opc_server.idx, "pixel_num", 5)
            # Set the variable to be writable by clients
            await myvar.set_writable()

            logging.info("Starting server!")

            async with self._opc_server.server:
                await asyncio.sleep(1)
                new_state = await current_state_node.read_value()
                logging.info(f"Current State is {new_state}")
                # set the server RO VA
                va = model.BooleanVA(True, readonly=True)
                setattr(self, "serverSim", va)  # set the class VA variable name
                while True:
                    await asyncio.sleep(1)
                    new_state = await current_state_node.read_value()
                    # await self._opc_server.change_state(new_state)
                    # _logger.info(f"Current State is {new_state}")
                    # await current_state_node.write_value(new_state)
        except ConnectionError as ex:
            self._error_msg = ex
        except Exception as ex:
            raise Exception(ex)

    def terminate(self):
        # stop the simulator server if a fake device is used
        if self._simserver:
            self._simserver.terminated = True
            self._simserver.join()

        super().terminate()

    async def setup_controller(self):
        # search for connected EBIC controllers using the opcua protocol
        # connecting to the controller/Client -> async with Client(url=self.url) as self.client
        self.idx = await self._client.get_namespace_index(self._namespace)
        await self.create_state_subscription()

    async def connect_to_controller(self):
        try:
            async with self._client:
                logging.info("Client connected")
                await self.setup_controller()
                await self.get_controller_state()
                logging.info(f"Server state is now: {self._state}")

                # TODO: fix/patch for setting the controller state this does not work now
                # await self.set_controller_state(STATE_NAME_RUNNING)
                # await asyncio.sleep(5)
                # await self.set_controller_state(STATE_NAME_STOPPED)
                logging.info("Connection is active")
                await asyncio.sleep(1)
                await self._client.check_connection()
                #self.init_done = True
        except (ConnectionError, ua.UaError) as e:
            raise ConnectionError(str(e) + " -> Client disconnected")

    async def create_state_subscription(self) -> None:
        """Creates a subscription for the change state event in the state machine."""
        state_machine_node = await self._client.nodes.root.get_child(
            f"0:Objects/{self.idx}:state_machine",
        )
        state_change_event = await self._client.nodes.root.get_child(
            ["0:Types", "0:EventTypes", "0:BaseEventType", "2:StateChangeEvent"],
        )

        subscription = await self._client.create_subscription(
            period=500,
            handler=self.sub_handler,
        )
        await subscription.subscribe_events(state_machine_node, state_change_event)

    async def get_controller_state(self):
        state_node = await self._client.nodes.root.get_child(
            f"0:Objects/{self.idx}:state_machine/{self.idx}:current_state",
        )
        self._state = await state_node.read_value()

    async def set_controller_state(self, req_state):
        current_state = await self._client.nodes.root.get_child(
            f"0:Objects/{self.idx}:state_machine/{self.idx}:current_state",
        )
        await current_state.write_value(req_state)
        await self._client.nodes.objects.call_method(f"{self.idx}:change_state")
        # TODO: only assign if state change = success
        self._state = req_state

    def start_generate(self):
        with self._acquisition_lock:
            self._wait_acquisition_stopped()
            self._acquisition_thread = threading.Thread(target=self._acquire_thread,
                    name="EBIC acquire flow thread")
            self._acquisition_thread.start()

    def stop_generate(self):
        with self._acquisition_lock:
            with self._acquisition_init_lock:
                self._acquisition_must_stop.set()

    def _acquire_thread(self):
        """
        Thread that simulates the SEM acquisition. It calculates and updates the
        center (e-beam) position based on the translation, imitates the delay according
        to the dwell time and resolution and provides the new generated output to
        the Dataflow.
        """
        try:
            while not self._acquisition_must_stop.is_set():
                dwelltime = self.parent._scanner.dwellTime.value
                resolution = self.parent._scanner.resolution.value
                duration = numpy.prod(resolution) * dwelltime
                if self._acquisition_must_stop.wait(duration):
                    break
                # TODO: it's not a very proper simulation for multiple detectors,
                # as in Odemis the convention for SEM is that the ebeam waits
                # for _all_ the detectors to be ready before scanning.
                self.data._waitSync()
                #callback(self._simulate_image())
        except Exception:
            logging.exception("Unexpected failure during image acquisition")
        finally:
            logging.debug("Acquisition thread closed")
            self._acquisition_must_stop.clear()

    def _wait_acquisition_stopped(self):
        """
        Waits until the acquisition thread is fully finished _iff_ it was requested
        to stop.
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

    def get_dwell_time(self, OS=0, CH: int = 2, samples: int = 1, delay: float = 5e-8, trigger: bool = True) -> int:
        """Gets the dwell time of each pixel based on Channels, samples, delay and oversampling.

        Args:
            OS (int): oversampling rate
            CH (int): number of channels
            samples (int): number of samples per channel
            delay (float): delay between samples
            TRIGGER (bool): trigger state

        Returns:
            int: dwell time in us

        """
        delayCycle = functions.delayCycles(delay)  # delay in cycles

        return functions.dwellTime(OS, CH, delayCycle, trigger)

    def get_scan_time(self, dwell_time: int, points_fast: int, points_slow: int) -> float:
        """Gets the scan time of the system based on dwell time and number of points.

        Args:
            dwell_time (int): dwell time in us
            points_fast (int): number of points in the fast axis default is x axis
            points_slow (int): number of points in the slow axis default is y axis
        Returns:
            float: scan time in seconds
        """
        return functions.scanTime(dwell_time, points_fast, points_slow)

    def set_scan_parameters(self, trigger=True) -> bool:
        """
        Sets the scan parameters of the EBIC scan box.
        :return:
        """
        delay = 1e-2  # 10ms default

        self.dwell_time = self.get_dwell_time(self.oversampling.value,
                                              self.numberOfChannels.value,
                                              self.spp.value,
                                              delay,
                                              trigger)
        self.scan_time = self.get_scan_time(self.dwell_time, self.repetition.value[0], self.repetition.value[1])
        # TODO not return boolean but float -> sum of dwell_time and scan_time?
        return True

    def start_scan(self):
        # this function is to be used in the special SPARC acq and calls the start state of the API
        pass

    def stop_scan(self):
        # this function is to be used in the special SPARC acq
        pass

    async def retrieve_acquired_data(self):
        pass

    async def read_variable(self, obj_name: str, var_name: str) -> Any:
        """
        Reads a variable from the OPC server.
        :param obj_name:
        :param var_name:
        :return:
        """
        var = await self._client.nodes.root.get_child(f"0:Objects/{self.idx}:{obj_name}/{self.idx}:{var_name}")
        value = await var.read_value()
        # return numpy.array(list(value), dtype=numpy.int32)
        return value

    def on_repetition_change(self, value):
        return value

    def on_spp_change(self, value):
        return value

    def on_chan_num_change(self, value):
        return value

    def on_oversampling_change(self, value):
        return value


class MightyEbicSimulator:
    """
    OPC Server class: This class is responsible for setting up the server and the state machine.
    it can interface directly with hardware using the functions module.
    """
    def __init__(self):
        """Initialize the OPC Server class."""
        self.server = Server()
        self.idx = None
        self.state_machine = None
        self.state_machine_node = None
        self.states = {}
        self.transitions = {}

    async def setup(self) -> None:
        """Call server async setup functions."""
        await self.server.init()
        self.server.set_endpoint("opc.tcp://localhost:4840/freeopcua/server/")

        # set up our own namespace, not really necessary but should as spec
        self.idx = await self.server.register_namespace("http://examples.freeopcua.github.io")
        await self.setup_state_machine()
        await self.setup_events()

    async def setup_state_machine(self) -> None:
        """Set up the state machine for the server."""
        self.state_machine = StateMachine(
            self.server,
            self.server.nodes.objects,
            self.idx,
            "OPCServerStateMachine",
        )

        await self.state_machine.install(optionals=True)
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

        idle_transition = Transition(
            TRANSITION_ID_RUNNING,
            TRANSITION_NAME_RUNNING,
            1,
        )
        self.transitions[STATE_NAME_IDLE] = idle_transition
        await self.state_machine.add_transition(idle_transition)
        running_transition = Transition(
            TRANSITION_ID_TRIGGER,
            TRANSITION_NAME_TRIGGER,
            2,
        )
        self.transitions[STATE_NAME_RUNNING] = running_transition
        await self.state_machine.add_transition(running_transition)
        trigger_transition = Transition(
            TRANSITION_ID_CHECKING_DWELL,
            TRANSITION_NAME_CHECKING_DWELL,
            3,
        )
        self.transitions[STATE_NAME_TRIGGER] = trigger_transition
        await self.state_machine.add_transition(trigger_transition)
        stop_transition = Transition(
            TRANSITION_ID_STOPPED,
            TRANSITION_NAME_STOPPED,
            4,
        )
        self.transitions[STATE_NAME_STOPPED] = stop_transition
        await self.state_machine.add_transition(stop_transition)
        error_transition = Transition(
            TRANSITION_ID_ERROR,
            TRANSITION_NAME_ERROR,
            5,
        )
        self.transitions[STATE_NAME_ERROR] = error_transition
        await self.state_machine.add_transition(error_transition)

        # TODO: state_machine source code contains an error and needs to be patched in our version
        # Issue in question: https://github.com/FreeOpcUa/opcua-asyncio/issues/1479
        # await self.state_machine.change_state(idle_state, idle_transition)

        self.state_machine_node = await self.server.nodes.objects.add_object(
            self.idx,
            "state_machine",
        )
        current_state_var = await self.state_machine_node.add_variable(
            self.idx,
            "current_state",
            "Idle",
        )
        await current_state_var.set_writable()
        await self.server.nodes.objects.add_method(
            ua.NodeId("change_state", self.idx),
            ua.QualifiedName("change_state", self.idx),
            self.change_state,
            [ua.VariantType.String],
            [ua.VariantType.String],
        )

    async def setup_events(self) -> None:
        """Set up the events for the server."""
        event_type = await self.server.create_custom_event_type(
            self.idx,
            "StateChangeEvent",
            ua.ObjectIds.BaseEventType,
            [("state", ua.VariantType.String)],
        )

        self.state_change_event_gen = await self.server.get_event_generator(
            event_type,
            self.state_machine_node,
        )

    @uamethod
    async def change_state(self, new_state: str) -> None:
        """Change the state of the server.

        TODO: connect state with real hardware client.
        """
        if new_state in self.states:
            await self.state_machine.change_state(
                self.states[new_state], self.transitions[new_state]
            )
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
        detector (Detector): the detector that the dataflow corresponds to
        """
        model.DataFlow.__init__(self)
        self._detector = detector
        self._sync_event = None  # event to be synchronised on, or None
        self._evtq = None  # a Queue to store received events (= float, time of the event)

    # start/stop_generate are _never_ called simultaneously (thread-safe)
    def start_generate(self):
        self._detector.start_generate()

    def stop_generate(self):
        self._detector.stop_generate()

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
    def datachange_notification(self, node: Node, val, data):
        """Called for every datachange notification from server.
        Args:
            node (Node): Node object being monitored
            val (_type_): value of the node being updated
            data (_type_): data of the node being changed
        """
        logging.info("data_change_notification %r %s", node, val)

    def event_notification(self, event: ua.EventNotificationList) -> None:
        """Called for every event notification from server.
        Args:
            event (ua.EventNotificationList): event notification object
        """
        logging.info("event_notification %r", event)

    def state_change_notification(self, state: ua.NotificationMessage, transition: ua.NotificationMessage) -> None:
        """Called for every state change notification from server state machine.
        Args:
            state (ua.NotificationMessage): state notification object
            transition (ua.NotificationMessage): transition notification object
        """
        logging.info("state_change_notification %r %r", state, transition)
