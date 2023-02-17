#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 13 Oct 2022

@author: Canberk Akin

Copyright © 2023 Canberk Akin, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""
import logging
import time
from concurrent import futures
from typing import Tuple

from odemis import model

from ConsoleClient.Communication.Connection import Connection
from MillingPattern.MillingObjects.Rectangle import Rectangle
from MillingPattern.MillingObjects.Point import Point
from MillingPattern.Layer import Layer
from MillingPattern.Procedure import Procedure


# The executor is a single object, independent of how many times the module is loaded.
_executor = model.CancellableThreadPoolExecutor(max_workers=1)

# Orsay server accepts 5 concurrent connections at the same time and there is no way to terminate an already
# established connection. This can easily cause connection issues. This global variable is created to hold the
# connection information and prevent creating a new connection for every milling job
_connection = None


def _get_orsay_connection(scanner):
    """
    Function to check if there is already a connection established and creates a new connection otherwise
    :param scanner: (HwComponent) To get the IP Address to create/check the connection
    """
    global _connection
    if _connection is None:
        _connection = Connection(scanner.parent.host)
    else:
        # connection already established, check it still works
        try:
            _connection.datamodel
        except Exception as exp:
            logging.warning("Connection issue detected: %s", exp)
            _connection = Connection(scanner.parent.host)

    return _connection


def mill_rectangle(rect: Tuple[float, float, float, float],
                   scanner,
                   iteration: int,
                   duration: float,
                   probe_size: float,
                   overlap: Tuple[float, float]) -> futures.Future:
    """
    Function to pass
    :param rect: (tuple of floats) xmin, ymin, xmax, ymax of the rectangle in values relative to the FoV (0 to 1)
    :param scanner: (HwComponent) To get the current HorizontalFoV value in meters
    :param iteration: (int)
    :param duration: (float)
    :param probe_size: (float) probe size in meters
    :param overlap: (tuple of floats) overlap 0 means no overlapping, 0.1 means 10 percent overlapping
        distance between two pixels is probeSize*(1 - overlap)
    :return: future
    """
    now = time.time()
    f = model.ProgressiveFuture(start=now, end=now + iteration * duration + 1)
    miller = OrsayMilling(f, rect, scanner, iteration, duration, probe_size, overlap)
    f.task_canceller = miller.cancel_milling

    # Connect the future to the task and run it in a thread.
    # task.run is executed by the executor and runs as soon as no other task is executed
    _executor.submitf(f, miller.do_milling)

    return f


class OrsayMilling:
    def __init__(self, f: futures.Future,
                 rect: Tuple[float, float, float, float],
                 scanner,
                 iteration: int,
                 duration: float,
                 probe_size: float,
                 overlap: Tuple[float, float]):
        """
        :param f: future to handle milling process
        :param rect: (tuple of floats) xmin, ymin, xmax, ymax of the rectangle in values relative to the FoV (0 to 1)
        :param scanner: (HwComponent) To get the current HorizontalFoV value in meters
        :param iteration: (int)
        :param duration: (float)
        :param probe_size: (float) probe size in meters
        :param overlap: (tuple of floats) overlap 0 means no overlapping, 0.1 means 10 percent overlapping
            distance between two pixels is probeSize*(1 - overlap)
        """
        self.future = f
        self.rect = rect
        self.scanner = scanner
        self.iteration = iteration
        self.duration = duration
        self.probe_size = probe_size
        self.overlap = overlap
        self.miller = None  # to be held HybridPatternCreator

    def do_milling(self):
        """
        Function that handles milling action
        """
        rect = self.rect
        iteration = self.iteration
        duration = self.duration
        probe_size = self.probe_size
        overlap = self.overlap

        server = _get_orsay_connection(self.scanner)
        miller = server.datamodel.HybridPatternCreator
        self.miller = miller
        scanner = server.datamodel.HybridScanUnit

        try:
            miller.MillingActivationState.Subscribe(self.param_logger)
            scanner.MillingStatus.Subscribe(self.param_logger)
            # scanner.CurrentSubList.Subscribe(self.param_logger)
            # scanner.CurrentMillingPoint.Subscribe(self.param_logger)  # Very verbose

            # It is assumed that the self.scanner.shape is always square (eg 1024x1024)
            # If not, the physical dimension of Y should be adjust proportionally.
            fov = self.scanner.horizontalFoV.value

            # the Y axes in odemis and orsay are inverted, that is why centerOfMassY is always inverted
            # hardcoded 0.5's in the formulas below are the shift relative to the center of the FoV
            if rect[2] == rect[0] and rect[1] == rect[3]:
                # mill a point
                obj_to_mill = Point()
                obj_to_mill.primaryAxisX = 0
                obj_to_mill.secondaryAxisLength = 0
            else:
                obj_to_mill = Rectangle()
                obj_to_mill.primaryAxisOverlap = overlap[0]
                obj_to_mill.secondaryAxisOverlap = overlap[1]
                obj_to_mill.primaryAxisY = 0  # primaryAxisY is always 0 to keep the  rectangle straight
                if rect[0] == rect[2]:
                    logging.debug("Milling a vertical line. Orsay doesn't like rectangles with 0 "
                                  "height. Line height automatically set equal to the probe size to fit a single line.")
                    obj_to_mill.primaryAxisX = probe_size / 2
                    obj_to_mill.secondaryAxisLength = (rect[3] - rect[1]) / 2 * fov
                elif rect[1] == rect[3]:
                    logging.debug("Milling a horizontal line. Orsay doesn't like rectangles with 0 "
                                  "width. Line width automatically set equal to the probe size to fit a single line.")
                    obj_to_mill.primaryAxisX = (rect[2] - rect[0]) / 2 * fov
                    obj_to_mill.secondaryAxisLength = probe_size / 2
                else:
                    obj_to_mill.primaryAxisX = (rect[2] - rect[0]) / 2 * fov
                    obj_to_mill.secondaryAxisLength = (rect[3] - rect[1]) / 2 * fov
            # common attribute value for point and rectangle, hence outside of if block
            obj_to_mill.NumberOfPasses = iteration
            obj_to_mill.totalScanTime = duration * iteration

            obj_to_mill.centerOfMassX = ((rect[2] + rect[0]) / 2 - 0.5) * fov
            obj_to_mill.centerOfMassY = (0.5 - (rect[3] + rect[1]) / 2) * fov

            lay = Layer()
            lay.probeSize = probe_size
            lay.AddForm(0, obj_to_mill)

            proc = Procedure()
            proc.AddLayer(0, lay)
            # it is not allowed to use ':' character in the procedure names
            proc.name = f"Odemis rectangle milling {time.time()}"

            prev_milling_pt = scanner.CurrentMillingPoint.Actual

            if self.future.cancelled():
                raise futures.CancelledError()

            # this is going the start the milling indirectly
            # It unsubscribes by itself
            miller.ProcedureInfoSerialNumber.Subscribe(self.on_procedure_info)

            # send the procedure to the server.
            proc_string = proc.ToString()
            miller.SetCurrentProcedure(proc_string)
            logging.debug("Sending procedure with name %s, using FoV = %s m", proc_string, fov)

            milling_activation_timeout = time.time() + 20  # s, it often takes ~7s, so be generous
            # wait for milling action to start
            while miller.MillingActivationState.Actual != '1':
                if self.future.cancelled():
                    raise futures.CancelledError()

                time.sleep(0.1)
                if time.time() > milling_activation_timeout:
                    raise TimeoutError("Milling failed to start up after trying for 10 seconds.")

            milling_start_t = time.time()
            milling_execution_timeout = milling_start_t + (duration * iteration) * 1.1 + 5

            # Make sure the milling is really started by verifying that the milling
            # point changes (and it's not 0).
            while scanner.CurrentMillingPoint.Actual in (prev_milling_pt, "0"):
                if self.future.cancelled():
                    raise futures.CancelledError()

                time.sleep(0.1)
                if time.time() > milling_activation_timeout:
                    logging.debug("Current milling point = %s, milling state = %s",
                                  scanner.CurrentMillingPoint.Actual,
                                  miller.MillingActivationState.Actual)
                    raise TimeoutError("Milling failed to start up after trying for 10 seconds.")

            logging.debug("Now milling a rectangle %sm by %sm during %s passes of %s seconds",
                          obj_to_mill.primaryAxisX * 2, obj_to_mill.secondaryAxisLength * 2, iteration, duration)

            # Now wait until the milling is done (the miller will set the state target to 0 at the end)
            while miller.MillingActivationState.Target != '0':
                time.sleep(0.1)
                if time.time() > milling_execution_timeout:
                    raise TimeoutError(f"Milling operation timed out after {time.time() - milling_start_t}s.")

            # Wait until it's actually stopped
            while miller.MillingActivationState.Actual != '0':
                time.sleep(0.1)
                if time.time() > milling_execution_timeout:
                    raise TimeoutError(f"Milling operation timed out after {time.time() - milling_start_t}s.")

            milling_dur = time.time() - milling_start_t
            if milling_dur < duration * iteration:
                logging.warning("Milling completely only after %s s, while expected %s s",
                                milling_dur, duration * iteration)
            else:
                logging.debug("Milling completed.")

            if self.future.cancelled():
                raise futures.CancelledError()
        finally:
            # make sure the Unsubscribe is always called even if any exception happens
            miller.MillingActivationState.Unsubscribe(self.param_logger)
            scanner.MillingStatus.Unsubscribe(self.param_logger)
            # scanner.CurrentSubList.Unsubscribe(self.param_logger)
            # scanner.CurrentMillingPoint.Unsubscribe(self.param_logger)


    def cancel_milling(self, future):
        """
        Cancels the active milling process
        """
        # Don't use self.miller so that even if there is an issue with the original connection, still possible to cancel
        server = _get_orsay_connection(self.scanner)
        miller = server.datamodel.HybridPatternCreator
        miller.MillingActivationState.Target = 0
        logging.debug("Milling cancelled.")

        return True

    def param_logger(self, param, attr: str):
        """
        Logs the change of an OrsayParameter
        param (OrsayParameter): the parameter which is subscribed to
        attr (str): the attribute that has changed
        """
        # Reduce log
        if attr not in ("Actual", "Target"):
            return

        logging.info("param %s.%s changed to %s", param.Name, attr, getattr(param, attr))

    # run the procedure which has been built from scratch
    def on_procedure_info(self, param, attr):
        """
        Called when a new milling procedure is loaded
        Starts the milling process when the procedure is loaded
        Automatically unsubscribes from the Parameter
        """
        if attr == 'Actual':
            logging.debug('New active procedure (%s) : %s', param.Actual, self.miller.ActiveProcedureName.Target)
            self.miller.MillingActivationState.Target = 1  # Start milling once the new procedure has been loaded
            param.Unsubscribe(self.on_procedure_info)
