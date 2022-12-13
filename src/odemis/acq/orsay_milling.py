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
        # for now the ip address is hardcoded, it will be replaced with scanner.parent.host
        rect = self.rect
        iteration = self.iteration
        duration = self.duration
        probe_size = self.probe_size
        overlap = self.overlap

        server = _get_orsay_connection(self.scanner)
        miller = server.datamodel.HybridPatternCreator
        self.miller = miller

        try:
            miller.MillingActivationState.Subscribe(self.on_milling_state)

            fov = self.scanner.horizontalFoV.value

            # the Y axes in odemis and orsay are inverted, that is why centerOfMassY is always inverted
            # hardcoded 0.5's in the formulas below are the shift relative to the center of the FoV
            if rect[2] == rect[0] and rect[1] == rect[3]:
                # mill a point
                obj_to_mill = Point()
                obj_to_mill.primaryAxisX = 0
                obj_to_mill.secondaryAxisLength = 0
            else:
                # scanner.shape gives the resolution like 1024x1024
                # it is assumed that the scanner.shape is always square
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

            # this is going the start the milling indirectly
            miller.ProcedureInfoSerialNumber.Subscribe(self.on_procedure_info)

            # send the procedure to the server.
            proc_string = proc.ToString()
            miller.SetCurrentProcedure(proc_string)
            logging.debug("Sending procedure with name %s, using FoV = %s m", proc_string, fov)

            milling_activation_timeout = time.time() + 10
            # wait for milling action to start
            while miller.MillingActivationState.Actual != '1':
                time.sleep(0.1)
                if time.time() > milling_activation_timeout:
                    raise TimeoutError("Milling failed to start up after trying for 10 seconds.")

            logging.debug("Now milling a rectangle %sm by %sm during %s passes of %s seconds",
                          obj_to_mill.primaryAxisX*2, obj_to_mill.secondaryAxisLength*2, iteration, duration)

            milling_execution_timeout = time.time() + duration * iteration + 5
            while miller.MillingActivationState.Actual != '0':
                time.sleep(0.1)
                if time.time() > milling_execution_timeout:
                    raise TimeoutError("Milling operation timed out. Please check whether the milling is done correctly.")
            if self.future.cancelled():
                raise futures.CancelledError()

            logging.debug("Milling completed.")

        # make sure the Unsubscribe is always called even if any exception happens
        finally:
            miller.MillingActivationState.Unsubscribe(self.on_milling_state)

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

    def on_milling_state(self, attr, param):
        """
        Reports whenever the milling state is changed/updated
        or the milling state reaches the target value
        """
        if attr == 'Actual':
            logging.debug('New milling state: ' + param.Actual)

        if attr == 'AtTarget':
            logging.debug('Milling state at target: ' + param.AtTarget)

    # run the procedure which has been built from scratch
    def on_procedure_info(self, param, attr):
        """
        Called when a new milling procedure is loaded
        Starts the milling process when the procedure is loaded
        """
        if attr == 'Actual':
            logging.debug('New active procedure (%s) : %s', param.Actual, self.miller.ActiveProcedureName.Target)
            self.miller.MillingActivationState.Target = 1  # Start milling once the new procedure has been loaded
            param.Unsubscribe(self.on_procedure_info)
