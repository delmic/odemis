# -*- coding: utf-8 -*-
'''
Created on 4 Mar 2014

@author: Kimon Tsitsikas

Copyright © 2014-2016 Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
'''

from enum import Enum
import gc
import logging
import math
import queue
import re
import socket
import threading
import time
import weakref
from typing import List, Literal, Callable, Any, Dict, Tuple, Union

import numpy

try:
    from tescansharksem import sem
# Make sure to handle old (delmic-only) name for the API, up to v3.1.0.
except ModuleNotFoundError:
    from tescan import sem

from odemis import model, util
from odemis.model import (HwError, isasync, CancellableThreadPoolExecutor,
                          roattribute, oneway)
from odemis.util import TimeoutError
from odemis.util.driver import isNearPosition

ACQ_CMD_UPD = 1
ACQ_CMD_TERM = 2
# FIXME: Tescan integrations lower limit. For some reason when trying to acquire
# a spot with less than 100 integrations it gets an enormous delay to receive
# new data from the server.
TESCAN_PXL_LIMIT = 100
PROBE_CURRENT_RANGE = (10e-12, 100e-9)

DEFAULT_ION_PRESET = ""

MIN_MOVE_SIZE_LIN_MM = 10e-6  # 10e-9 m (delmic) --> 10e-6 mm (tescan)
MIN_MOVE_SIZE_ROT_DEG = 0.05

class DeviceType(Enum):
    ELECTRON = "electron"
    ION = "ion"

DEFAULT_BITDEPTH = 16


class CancelledError(Exception):
    """Data receive was cancelled"""
    pass

# Maps canonical method names (SEM) to FIB equivalents.
# These methods are present in TESCAN's SharkSEM API.
FIB_NAME_MAP: Dict[str, str] = {
    "GUISetScanning": "FibGUISetScan",
    "GUIGetScanning": "FibGUIGetScan",
    "DtSelect": "FibDtSelect",
    "DtEnable": "FibDtEnable",
    "DtAutoSignal": "FibDtAutoSig",  # Channel (int), Dwell (float): dwell time with unit ns
    "HVGetVoltage": "FibHVGetVoltage",  # V
    "HVGetBeam": "FibHVGetBeam",
    "HVBeamOn": "FibHVBeamOn",
    "HVBeamOff": "FibHVBeamOff",
    "ScStopScan": "FibScStopScan",
    "GetViewField": "FibGetViewField",
    "SetViewField": "FibSetViewField",
    "FetchImage": "FibFetchImage",
    "FetchImageEx": "FibFetchImageEx",
    "ScScanLine": "FibScScanLine",
    "ScScanXY": "FibScScanXY",
    "ScGetExternal": "FibScGetExtern",
    "GetBeamCurrent": "FibReadFCCurr",  # pA
    "PresetEnum": "FibEnumPresets",
    "PresetSetEx": "FibSetPresetEx",  # Id (str): name/value of the preset
    "ScEnumSpeeds": "FibScEnumSpeeds",
    "ScGetSpeed": "FibScGetSpeed",
    "ScSetSpeed": "FibScSetSpeed",
    "DtEnumDetectors": "FibDtEnumDetec",
    "CancelRecv": "CancelRecv",
    "DtGetEnabled": "FibDtGetEnabled",
    "GetImageRot": "FibGetImageRot",
    "SetImageRot": "FibSetImageRot",
    # Not possible to control the FIB blanker
    "ScGetBlanker": None,
    "ScSetBlanker": None,
    # All handled via presets for FIB
    "SetBeamCurrent": None,
    "HVEnumIndexes": None,
    "HVSetVoltage": None,
    "GetWD": None,
    "SetWD": None,
}


class DeviceHandler:
    """
    A handler class to dynamically map and invoke methods for SEM and FIB devices.

    This class provides a unified interface to interact with SEM and FIB via the SharkSEM API. It uses a mapping
    (`FIB_NAME_MAP`) to translate SEM method names to their FIB equivalents, if available, and dynamically invokes the
    appropriate method on the device.
    """
    def __init__(
        self,
        device: sem.Sem,
        device_type: Literal[DeviceType.ELECTRON, DeviceType.ION] = DeviceType.ELECTRON
    ):
        """
        Initializes the DeviceHandler with a TESCAN SEM controller.

        :param device: the TESCAN SEM controller
        """
        self.device = device
        self.device_type = device_type

    def __getattr__(self, name: str) -> Callable[[Literal[DeviceType.ELECTRON, DeviceType.ION], Any], Any]:
        """
        Dynamically resolves and invokes the appropriate method for the given device type.

        name: The name of the method to invoke. Using the SEM method name as canonical.
        """
        def call_and_log_func(func, args, kwargs):
            logging.debug(f"SharkSEM: Calling {func.__name__} with args: {args} and kwargs: {kwargs}")
            return func(*args, **kwargs)

        def method(*args, **kwargs):
            """
            Resolves and invokes the appropriate method for the specified device type (electron for SEM or ion for FIB).

            :param device_type: The type of device to invoke the method on. Use DeviceType.ELECTRON or "ion'.
            :param *args: Positional arguments to pass to the resolved method.
            :param **kwargs: Keyword arguments to pass to the resolved method.

            :returns: The result of the invoked method.

            :raises AttributeError: If the method name does not have an equivalent in FIB or if the
                method does not exist in the SharkSEM API.
            :raises ValueError: If an unknown device type is provided.
            """
            if self.device_type == DeviceType.ELECTRON:
                # Try to get SEM method from device. Raises AttributeError if not existing.
                func = getattr(self.device, name)

            elif self.device_type == DeviceType.ION:
                fib_name = FIB_NAME_MAP.get(name)
                if not fib_name:
                    raise AttributeError(f"No FIB equivalent for '{name}'")
                try:
                    func = getattr(self.device, fib_name)
                except AttributeError:
                    raise AttributeError(f"FIB equivalent method '{fib_name}' for '{name}' does not exist in the API")

            else:
                raise ValueError(f"Unknown device_type: {self.device_type}")
            return call_and_log_func(func, args, kwargs)
        return method


class SEM(model.HwComponent):
    """
    This is an extension of the model.HwComponent class. It instantiates the (fib-)scanner
    and se-detector(-ion) children components and provides an update function for its
    metadata.
    """

    def __init__(self, name, role, children, host, port=8300, daemon=None, **kwargs):
        """
        children (dict string->kwargs): parameters setting for the children.
            Known children are "scanner", "detector", "stage", "focus", "camera"
            and "pressure". They will be provided back in the .children VA
        host (string): ip address of the SEM server
        Raise an exception if the device cannot be opened
        """
        # we will fill the set of children with Components later in ._children
        model.HwComponent.__init__(self, name, role, daemon=daemon, **kwargs)

        self._host = host
        self._port = port
        self._socket_timeout = 2  # Seconds. This value is a balance for responsiveness vs how much frames you lose
        self._connect_socket()
        # Lock in order to synchronize all the child component functions
        # that acquire data from the SEM while we continuously acquire images
        self._acq_progress_lock = threading.Lock()
        self._acquisition_mng_lock = threading.Lock()
        self._acquisition_init_lock = threading.Lock()
        self._acq_cmd_q = queue.Queue()
        self._acquisition_must_stop = threading.Event()
        self._acquisitions = set()  # detectors currently active
        self.pre_res = None
        self._scaled_shape = None
        self._roi = None
        self._dt = None

        # If no detectors, no need to annoy the user by stopping the current scanning
        # detector0, detector1, etc.
        if any(n.startswith("detector") for n in children.keys()):
            # important: stop the scanning before we start scanning or before
            # automatic procedures, even before we configure the detectors
            DeviceHandler(self._device, DeviceType.ELECTRON).ScStopScan()
        # fib-detector
        elif any(n.endswith("detector") for n in children.keys()):
            DeviceHandler(self._device, DeviceType.ION).ScStopScan()

        self._hwName = "TescanSEM (s/n: %s)" % (self._device.TcpGetDevice())
        self._metadata[model.MD_HW_NAME] = self._hwName
        self._swVersion = "SEM sw %s, protocol %s" % (self._device.TcpGetSWVersion(),
                                                      self._device.TcpGetVersion())
        self._metadata[model.MD_SW_VERSION] = self._swVersion

        scanner_types = ["scanner", "fib-scanner"]  # All allowed scanners types
        if not any(scanner_type in children for scanner_type in scanner_types):
            raise KeyError("SEM was not given any scanner as child. "
                           "One of 'scanner', 'fib-scanner' need to be included as child")

        self._scanners = {}
        self._detectors = {}
        # Check for detectors and scanners. We don't use the component's role, since that is mainly used for UI
        # purposes. For now we expect scanner <-> detector<number> (could be multiple) for SEM and
        # fib-scanner <-> fib-detector for FIB.
        if "scanner" in children:
            scanner = Scanner(parent=self, daemon=daemon, device_type=DeviceType.ELECTRON, **children["scanner"])
            self._scanners["scanner"] = scanner

            for irole, ckwargs in children.items():
                if irole.startswith("detector"): # Matches only SEM detectors (detector0, detector1, etc.)
                    detector = Detector(parent=self, daemon=daemon, scanner=scanner, **ckwargs)
                    self._detectors[irole] = detector

        if "fib-scanner" in children:
            scanner = Scanner(parent=self, daemon=daemon, device_type=DeviceType.ION, **children["fib-scanner"])
            self._scanners["fib-scanner"] = scanner

            if "fib-detector" in children:
                detector = Detector(parent=self, daemon=daemon, scanner=scanner, **children["fib-detector"])
                self._detectors["fib-detector"] = detector

        for scanner in self._scanners.values():
            self.children.value.add(scanner)

        for detector in self._detectors.values():
            self.children.value.add(detector)

        # create the focus child
        try:
            kwargs = children["focus"]
        except (KeyError, TypeError):
            logging.info("TescanSEM was not given a 'focus' child")
        else:
            self._focus = EbeamFocus(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._focus)

        # create the stage child
        try:
            kwargs = children["stage"]
        except (KeyError, TypeError):
            logging.info("TescanSEM was not given a 'stage' child")
        else:
            self._stage = Stage(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._stage)

        # create the camera child
        try:
            kwargs = children["camera"]
        except (KeyError, TypeError):
            logging.info("TescanSEM was not given a 'camera' child")
        else:
            self._camera = ChamberView(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._camera)

        # create the pressure child
        try:
            kwargs = children["pressure"]
        except (KeyError, TypeError):
            logging.info("TescanSEM was not given a 'pressure' child")
        else:
            self._pressure = ChamberPressure(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._pressure)

        # create the light child
        try:
            kwargs = children["light"]
        except (KeyError, TypeError):
            logging.info("TescanSEM was not given a 'light' child")
        else:
            self._light = Light(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._light)

        self._acquisition_thread = threading.Thread(target=self._acquisition_run,
                                                    name="SEM acquisition thread")
        self._acquisition_thread.start()

    def start_acquire(self, detector):
        """
        Start acquiring images on the given detector (i.e., input channel).
        detector (Detector): detector from which to acquire an image
        Note: The acquisition parameters are defined by the scanner. Acquisition
        might already be going on for another detector, in which case the detector
        will be added on the next acquisition.
        raises KeyError if the detector is already being acquired.
        """
        # to be thread-safe (simultaneous calls to start/stop_acquire())
        with self._acquisition_mng_lock:
            if detector in self._acquisitions:
                raise KeyError("Channel %d already set up for acquisition." % detector.channel)
            self._acquisitions.add(detector)
            self._acq_cmd_q.put(ACQ_CMD_UPD)

            # If something went wrong with the thread, report also here
            if self._acquisition_thread is None:
                raise IOError("Acquisition thread is gone, cannot acquire")

    def stop_acquire(self, detector):
        """
        Stop acquiring images on the given channel.
        detector (Detector): detector from which to acquire an image
        Note: acquisition might still go on on other channels
        """
        with self._acquisition_mng_lock:
            # This call to stop the scanning will timeout the socket, which allows us to exit early.
            detector._device_handler.ScStopScan()
            self._acquisitions.discard(detector)
            self._acq_cmd_q.put(ACQ_CMD_UPD)
            if not self._acquisitions:
                self._req_stop_acquisition()

    def _check_cmd_q(self, block=True):
        """
        block (bool): if True, will wait for a (just one) command to come,
          otherwise, will wait for no more command to be queued
        raise CancelledError: if the TERM command was received.
        """
        # Read until there are no more commands
        while True:
            try:
                cmd = self._acq_cmd_q.get(block=block)
            except queue.Empty:
                break

            # Decode command
            if cmd == ACQ_CMD_UPD:
                pass
            elif cmd == ACQ_CMD_TERM:
                logging.debug("Acquisition thread received terminate command")
                raise CancelledError("Terminate command received")
            else:
                logging.error("Unexpected command %s", cmd)

            if block:
                return

    def _acquisition_run(self):
        """
        Acquire images until asked to stop. Sends the raw acquired data to the
          callbacks.
        Note: to be run in a separate thread
        """
        last_gc = 0
        nfailures = 0
        try:
            while True:
                with self._acquisition_init_lock:
                    # Ensure that if a rest/term is needed, and we don't catch
                    # it in the queue (yet), it will be detected before starting
                    # the read/write commands.
                    self._acquisition_must_stop.clear()

                self._check_cmd_q(block=False)

                detectors = tuple(self._acq_wait_detectors_ready())  # ordered
                if detectors:
                    # write and read the raw data
                    try:
                        rdas = self._acquire_detectors(detectors)
                    except CancelledError as e:
                        # either because must terminate or just need to rest
                        logging.debug(f"Acquisition halted {e}")
                        continue
                    except Exception as e:
                        logging.exception(e)
                        # could be genuine or just due to cancellation
                        self._check_cmd_q(block=False)

                        nfailures += 1
                        if nfailures == 5:
                            logging.warning("Acquisition failed %d times in a row, giving up", nfailures)
                            return
                        else:
                            logging.warning("Acquisition failed, will retry")
                            time.sleep(1)
                            self._connect_socket()
                            continue

                    nfailures = 0

                    for d, da in zip(detectors, rdas):
                        d.data.notify(da)

                    # force the GC to non-used buffers, for some reason, without this
                    # the GC runs only after we've managed to fill up the memory
                    if time.time() - last_gc > 2:  # Costly, so not too often
                        gc.collect()  # TODO: if scan is long enough, during scan
                        last_gc = time.time()
                else:  # nothing to acquire => rest
                    with self._acq_progress_lock:
                        for detector in detectors:
                            detector._device_handler.ScStopScan()
                    gc.collect()
                    # wait until something new comes in
                    self._check_cmd_q(block=True)
                    last_gc = time.time()
        except CancelledError:
            logging.info("Acquisition threading terminated on request")
        except Exception:
            logging.exception("Unexpected failure during image acquisition")
        finally:
            try:
                with self._acq_progress_lock:
                    for detector in detectors:
                        detector._device_handler.ScStopScan()
            except Exception:
                # can happen if the driver already terminated
                pass
            logging.info("Acquisition thread closed")
            self._acquisition_thread = None

    def _connect_socket(self):
        logging.info("Attempting to connect sockets")
        self._device = sem.Sem()
        # Attempts to connect all sockets, but does nothing if already running
        connection = self._device.connection.Connect(self._host, self._port, self._socket_timeout)
        if connection < 0:
            raise HwError("Failed to connect to TESCAN server '%s'. "
                "Check that the ip address is correct and TESCAN server "
                "connected to the network." % (self._host,))

        # Disable Nagle's algorithm (batching data messages) and send them asap instead.
        # This is to avoid the 200ms ceiling on data transmission.
        self._device.connection.socket_c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._device.connection.socket_d.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        self._device.connection.socket_d.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._device.connection.socket_d.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        logging.info("Connected to sockets")

    def _req_stop_acquisition(self):
        """
        Request the acquisition thread to stop
        """
        with self._acquisition_init_lock:
            self._acquisition_must_stop.set()

    def _acq_wait_detectors_ready(self):
        """
        Block until all the detectors to acquire are ready (ie, received
          synchronisation event, or not synchronized)
        returns (set of Detectors): the detectors to acquire from
        """
        detectors = self._acquisitions.copy()
        det_not_ready = detectors.copy()
        det_ready = set()
        while det_not_ready:
            # Wait for all the DataFlows to have received a sync event
            for d in det_not_ready:
                d.data._waitSync()
                det_ready.add(d)

            # Check if new detectors were added (or removed) in the mean time
            with self._acquisition_mng_lock:
                detectors = self._acquisitions.copy()
            det_not_ready = detectors - det_ready

        return detectors

    def _acquire_detectors(self, detectors):
        """
        Run the acquisition for multiple detectors
        return (list of DataArrays): acquisition for each detector in order
        """
        rdas = []
        for d in detectors:
            rbuf = self._single_acquisition(d)
            rdas.append(rbuf)

        return rdas

    def _single_acquisition(self, detector):
        channel = detector.channel
        scanner = detector._scanner
        with self._acquisition_init_lock:
            if self._acquisition_must_stop.is_set():
                raise CancelledError("Acquisition cancelled during preparation")
            pxs = scanner.pixelSize.value  # m/px

            pxs_pos = scanner.translation.value
            scale = scanner.scale.value
            res = (scanner.resolution.value[0],
                   scanner.resolution.value[1])

            metadata = dict(self._metadata)
            metadata.update(scanner.getMetadata())
            # If there is an image center set by the sample stage in the posture manager,
            # make sure to update it with scanner translation.
            phy_pos = metadata.get(model.MD_POS, (0, 0))
            trans = scanner.pixelToPhy(pxs_pos)
            updated_phy_pos = (phy_pos[0] + trans[0], phy_pos[1] + trans[1])

            # update changed metadata
            metadata[model.MD_POS] = updated_phy_pos
            metadata[model.MD_ACQ_DATE] = time.time()

            scaled_shape = (scanner._shape[0] / scale[0], scanner._shape[1] / scale[1])
            scaled_trans = (pxs_pos[0] / scale[0], pxs_pos[1] / scale[1])
            center = (scaled_shape[0] / 2, scaled_shape[1] / 2)
            l = int(center[0] + scaled_trans[0] - (res[0] / 2))
            t = int(center[1] + scaled_trans[1] - (res[1] / 2))
            r = l + res[0] - 1
            b = t + res[1] - 1

            dt = scanner.dwellTime.value * 1e9
            logging.debug(f"Acquiring {detector.name} image of {res} with dwell time {dt} ns")

            # make sure socket settings are always set
            self._device.connection.socket_c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._device.connection.socket_d.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        with self._acq_progress_lock:
            try:
                bpp = detector.bpp.value
                scanner._device_handler.DtEnable(detector._channel, 1, bpp)
                scanner._device_handler.ScScanXY(0, scaled_shape[0], scaled_shape[1],
                                         l, t, r, b, 1, dt)

                # Fetch the image (blocking operation), ndarray is returned.
                # The Ex version of this method allows for acquiring multiple channels at once, and returns a list
                # with each item the image data corresponding to a channel. Since we only acquire one channel, take only
                # the first element (index 0).
                img = scanner._device_handler.FetchImageEx([channel], res[0] * res[1])[0]
                dtype = numpy.uint8 if bpp == 8 else "<u2"
                img = numpy.frombuffer(img, dtype=dtype)
                logging.debug(f"Received {detector.name} image of length {len(img)}")
            except OSError:
                raise CancelledError("Acquisition halted during scanning")

            # we must stop the scanning even after single scan
            scanner._device_handler.ScStopScan()
            self.pre_res = res
            try:
                img.shape = res[::-1]
            except Exception as e:
                logging.exception(f"Failed to update the image shape {e}")

            return model.DataArray(img, metadata)

    def terminate(self):
        """
        Must be called at the end of the usage. Can be called multiple times,
        but the component shouldn't be used afterwards.
        """
        if not self._device:
            return

        # stop the acquisition thread
        with self._acquisition_mng_lock:
            self._acquisitions.clear()
            self._acq_cmd_q.put(ACQ_CMD_TERM)
            self._req_stop_acquisition()

        acq_thread = self._acquisition_thread
        if acq_thread:
            acq_thread.join(10)

        # Terminate components
        for s in self._scanners.values():
            s.terminate()
        for d in self._detectors.values():
            d.terminate()
        if hasattr(self, "_stage"):
            self._stage.terminate()
        if hasattr(self, "_focus"):
            self._focus.terminate()
        if hasattr(self, "_camera"):
            self._camera.terminate()
        if hasattr(self, "_pressure"):
            self._pressure.terminate()
        if hasattr(self, "_light"):
            self._light.terminate()

        self._device.Disconnect()
        self._device = None

        super(SEM, self).terminate()

    @roattribute
    def host(self):
        """
        str: The IP address of the SEM server
        """
        return self._host

    @roattribute
    def port(self):
        """
        int: The TCP port of the SEM server
        """
        return self._port


class Scanner(model.Emitter):
    """
    This is an extension of the model.Emitter class. It contains Vigilant
    Attributes and setters for magnification, pixel size, translation, resolution,
    scale, rotation and dwell time. Whenever one of these attributes is changed,
    its setter also updates another value if needed e.g. when scale is changed,
    resolution is updated, when resolution is changed, the translation is recentered
    etc. Similarly it subscribes to the VAs of scale and magnification in order
    to update the pixel size.
    """
    def __init__(
        self,
        name,
        role,
        parent,
        fov_range,
        device_type=DeviceType.ELECTRON,
        current_range=PROBE_CURRENT_RANGE,
        **kwargs
    ):
        # It will set up ._shape and .parent
        model.Emitter.__init__(self, name, role, parent=parent, **kwargs)
        self._device_handler = DeviceHandler(self.parent._device, device_type)

        self._shape = (2048, 2048)

        # This is the field of view when in Tescan Software magnification = 100
        # and working distance = 0,27 m (maximum WD of Mira TC). When working
        # distance is changed (for example when we focus) magnification mention
        # in odemis and Tescan software are expected to be different.
        # TODO: check if the same for FIB
        self._hfw_nomag = 0.195565  # m

        # Get current field of view and compute magnification
        fov = self._device_handler.GetViewField() * 1e-3
        mag = self._hfw_nomag / fov

        # Field of view in Tescan is set in mm
        self.magnification = model.VigilantAttribute(mag, unit="", readonly=True)

        self.horizontalFoV = model.FloatContinuous(fov, range=fov_range, unit="m",
                                                   setter=self._setHorizontalFOV)
        self.horizontalFoV.subscribe(self._onHorizontalFOV)  # to update RO VAs and metadata

        # pixelSize is the same as MD_PIXEL_SIZE, with scale == 1
        # == smallest size/ between two different ebeam positions
        pxs = (self._hfw_nomag / (self._shape[0] * mag),
               self._hfw_nomag / (self._shape[1] * mag))
        self.pixelSize = model.VigilantAttribute(pxs, unit="m", readonly=True)

        # TODO: compute a good depthOfField based on the current hfw
        # self.depthOfField = model.FloatContinuous(1e-6, range=(0, 1e9),
        #                                           unit="m", readonly=True)

        # (.resolution), .translation, .rotation, and .scaling are used to
        # define the conversion from coordinates to a region of interest.

        # (float, float) in px => moves center of acquisition by this amount
        # independent of scale and rotation.
        tran_rng = [(-self._shape[0] / 2, -self._shape[1] / 2),
                    (self._shape[0] / 2, self._shape[1] / 2)]
        self.translation = model.TupleContinuous((0, 0), tran_rng,
                                              cls=(int, float), unit="",
                                              setter=self._setTranslation)

        # .resolution is the number of pixels actually scanned. If it's less than
        # the whole possible area, it's centered.
        resolution = (self._shape[0] // 8, self._shape[1] // 8)
        self.resolution = model.ResolutionVA(resolution, [(1, 1), self._shape], setter=self._setResolution)
        self._resolution = resolution

        # (float, float) as a ratio => how big is a pixel, compared to pixelSize
        # it basically works the same as binning, but can be float
        # (Default to scan the whole area)
        self._scale = (self._shape[0] / resolution[0], self._shape[1] / resolution[1])
        self.scale = model.TupleContinuous(self._scale, [(1, 1), self._shape],
                                           cls=(int, float),
                                           unit="", setter=self._setScale)
        self.scale.subscribe(self._onScale, init=True)  # to update metadata

        rotation = math.radians(self._device_handler.GetImageRot())
        self.rotation = model.FloatContinuous(rotation, (0, 2 * math.pi), unit="rad", setter=self._setRotation)
        self.rotation.subscribe(self._onRotation, init=True)
        self.dwell_time_lookup = self.get_dwell_time_lookup()
        dwell_time_index = self._device_handler.ScGetSpeed()
        dwell_time = self.dwell_time_lookup[dwell_time_index]
        min_dwell_time = min(self.dwell_time_lookup.values())
        max_dwell_time = max(self.dwell_time_lookup.values())
        self.dwellTime = model.FloatContinuous(dwell_time, (min_dwell_time, max_dwell_time), unit="s",
                                               setter=self._setDwellTime)
        self.dwellTime.subscribe(self._onDwellTime, init=True)
        volt = self._device_handler.HVGetVoltage()

        if self._device_handler.device_type == DeviceType.ELECTRON:
            # Range is according to min and max voltages accepted by Tescan API
            volt_range = self.GetVoltagesRange()
            self.accelVoltage = model.FloatContinuous(volt, volt_range, unit="V",
                                                    setter=self._setVoltage)
            self.accelVoltage.subscribe(self._onVoltage)

            pc = self._device_handler.GetBeamCurrent() * 1e-12  # Convert from pA to A
            # For limits of current, values from the Tescan UI are used, since the API did not specify any.
            self.probeCurrent = model.FloatContinuous(pc, current_range, unit="A",
                                                        setter=self._setPC)
            self.probeCurrent.subscribe(self._onPC)
        elif self._device_handler.device_type == DeviceType.ION:
            beam_presets = self.get_beam_presets()
            self.beamPreset = model.StringEnumerated(DEFAULT_ION_PRESET, beam_presets, setter=self._setBeamPreset)
            # For clarity, still maintain read-only VA's. The values from the preset titles do not match exactly with
            # the actual device values.
            self.accelVoltage = model.FloatVA(volt, unit="V", readonly=True)
            # Polling the current for the ion beam is blocking the Tescan UI for a second or so.
            # This is not ideal.
            # TODO: As an alternative we could do a single get call only after changing the preset, but
            # the timing is not trivial and it will not sync-back the Essence value after change.
            # self.probeCurrent = model.FloatVA(pc, unit="A", readonly=True)

        # The filament and beam status codes are: -1 for filament blown (G3) or unable to determine beam status (G4),
        # 0 for beam off, 1 for beam on, and 1000 for on/off procedure in progress.
        power = self._device_handler.HVGetBeam()  # Don't change state
        # Currently this value is instantiated but never altered for safety reasons.
        self.power = model.IntEnumerated(power, {-1, 0, 1, 1000}, unit="",
                                         setter=self._setPower)

        if self._device_handler.device_type == DeviceType.ELECTRON:
            bmode = self._device_handler.ScGetBlanker(1)
            blanked = (bmode != 0)
            self.blanker = model.BooleanVA(blanked, setter=self._setBlanker)
        if self._device_handler.device_type == DeviceType.ION:
            # NOTE: workaround for check in SecomStateController (which is a generic controller, contrary to it's name)
            self.blanker = model.VAEnumerated(None, choices={None})

        # To select "external" scan, which is used to control the scan via the
        # analog interface. So mostly useful when this driver is used only for
        # controlling the e-beam settings, and a DAQ board is used for scanning.
        emode = self._device_handler.ScGetExternal()
        self.external = model.BooleanVA(bool(emode), setter=self._setExternal)

        # Timer polling VAs so we keep up to date with changes made via Tescan UI
        self._va_poll = util.RepeatingTimer(5, self._pollVAs, "VAs polling")
        self._va_poll.start()

    def get_dwell_time_lookup(self) -> Dict[int, float]:
        """
        Obtains the discrete scan speeds from the microscope and presents it as a lookup that
        maps the TESCAN speed index to dwell time in seconds.
        """
        # First obtain the string with the dwell times. Example of such a string is:
        # 'speed.1.dwell=0.1\nspeed.2.dwell=0.32\nspeed.3.dwell=1\nspeed.4.dwell=3.2\nspeed.5.dwell=10\n'
        dwell_times = self._device_handler.ScEnumSpeeds()
        dwell_times = re.findall(r"speed.([0-9]+).dwell=([0-9]+[.]?[0-9]?)", dwell_times)
        dwell_times_dict = {}
        for dwell_times in dwell_times:
            index, speed = dwell_times
            # Speed seems in µs, so convert to seconds
            dwell_times_dict[int(index)] = float(speed) * 1e-6
        return dwell_times_dict

    def get_beam_presets(self) -> List[str]:
        """
        Get the available beam presets from the Tescan device and neglect empty ones.
        """
        beam_presets = self._device_handler.PresetEnum().split("\n")
        beam_presets = set(p for p in beam_presets if p)
        # Add an empty preset to serve as the default, since the current preset cannot be
        # obtained directly via the API.
        beam_presets.add(DEFAULT_ION_PRESET)
        return beam_presets

    def _onHorizontalFOV(self, s):
        # Update current pixelSize and magnification
        self._updatePixelSize()
        self._updateMagnification()

    def _updateHorizontalFOV(self):
        prev_fov = self.horizontalFoV.value

        with self.parent._acq_progress_lock:
            new_fov = self._device_handler.GetViewField() * 1e-3

        if prev_fov != new_fov:
            self.horizontalFoV._value = new_fov
            self.horizontalFoV.notify(new_fov)

    def _setHorizontalFOV(self, value):
        # The requested value can deviate from the actual value, for instance when requesting above the maximum.
        # The value will be automatically clipped to the range of the hardware.
        # Stop any current scan
        self._device_handler.ScStopScan()
        with self.parent._acq_progress_lock:
            # FOV to mm to comply with Tescan API
            self._device_handler.SetViewField(value * 1e3)
            cur_fov = self._device_handler.GetViewField() * 1e-3
        return cur_fov

    def _updateMagnification(self):
        mag = self._hfw_nomag / self.horizontalFoV.value
        self.magnification._set_value(mag, force_write=True)

    def _setVoltage(self, volt):
        self._device_handler.ScStopScan()
        with self.parent._acq_progress_lock:
            # Asynchronously call this, since it can block the whole system
            self._device_handler.HVSetVoltage(volt, Async=1)
            initial_time = time.time()
            while time.time() - initial_time < 20:
                # Setting voltage halts the system and socket connection, so make this blocking
                # Not sure if making the HVSetVoltage call asynchronous fixes the problem.
                logging.info("Waiting for voltage to settle")
                volt_read = self._device_handler.HVGetVoltage()
                if volt_read:
                    logging.info("Voltage settled")
                    break
                time.sleep(1)
            else:
                logging.warning(f"Voltage ({volt} V) did not settle within timeout")
        return volt

    def _onVoltage(self, volt):
        self.updateMetadata({model.MD_BEAM_VOLTAGE: volt})

    def _setPower(self, value):
        powers = self.power.choices

        power = util.find_closest(value, powers)
        if power == 0:
            self._device_handler.HVBeamOff()
        else:
            self._device_handler.HVBeamOn()
        return power

    def _setPC(self, value):
        self._device_handler.ScStopScan()
        with self.parent._acq_progress_lock:
            self._device_handler.SetBeamCurrent(value * 1e12)  # Convert from A to pA
            initial_time = time.time()
            # Typically a second on a real system, but a few seconds on a VM simulator
            while time.time() - initial_time < 10:
                # Setting current halts the system and socket connection
                logging.info("Waiting for current to settle")
                pc_read = self._device_handler.GetBeamCurrent()
                if pc_read:
                    logging.info("Current settled")
                    break
                time.sleep(1)
            else:
                logging.warning("Current ({value} A) did not settle within timeout")
        return value

    def _onPC(self, current):
        self.updateMetadata({model.MD_BEAM_CURRENT: current})

    def _onRotation(self, rotation):
        self.updateMetadata({model.MD_ROTATION: rotation})

    def _onDwellTime(self, dwellTime):
        self.updateMetadata({model.MD_DWELL_TIME: dwellTime})

    def _setBeamPreset(self, preset: str) -> str:
        """
        Set the name of the beam preset, which can be any arbitrary string. Sometimes the string contains information
        about the current and voltage, but it is not enforced by TESCAN.
        """
        # Check if there is a non empty preset set. Currently the default at startup is an empty string.
        if preset:
            self._device_handler.ScStopScan()
            with self.parent._acq_progress_lock:
                self._device_handler.PresetSetEx(preset)
        return preset

    def _setDwellTime(self, dwell_time: float):
        """
        Set the per pixel dwell tiem in seconds.

        The Tescan API only supports int's for indices while their interface supports floats (which results in an
        interpolated speed value). We are a thus a bit limited in what we receive and send. It's only a UI issue
        though (the actual scan speed is not affected).

        :param dwell_time: The dwell time per pixel in seconds
        """
        # Stop any current scan
        self._device_handler.ScStopScan()
        with self.parent._acq_progress_lock:
            idx = min(self.dwell_time_lookup, key=lambda k: abs(self.dwell_time_lookup[k] - dwell_time))
            self._device_handler.ScSetSpeed(idx)
        return dwell_time

    def GetVoltagesRange(self):
        """
        return (list of float): accelerating voltage values ordered by index
        """
        voltages = []
        avs = self._device_handler.HVEnumIndexes()
        vol = re.findall(r'\=(.*?)\n', avs)
        for i in enumerate(vol):
            voltages.append(float(i[1]))
        volt_range = (voltages[0], voltages[-2])
        return volt_range

    def _setBlanker(self, blanked):
        # Index:
        # 0 = electrostatic blanker
        # 1 = magnetic gun blanker
        # Mode:
        # 0 = Blanker off (beam active)
        # 1 = Blanker always on (beam inactive)
        # 2 = Auto: blanker on when no scanning and during fly-back of the ebeam
        # Note: the documentation states that mode 1 is not possible because the
        # magnetic gun is too slow. So it might be that 1 & 2 are inverted
        mode = 2 if blanked else 0
        with self.parent._acq_progress_lock:
            logging.debug("Setting blanker to %d", mode)
            self._device_handler.ScSetBlanker(1, mode)
        # The command is not blocking, but tests on a SEM showed it takes around 0.75s after unblanking
        # for the e-beam to actually be ready. So explicitly wait to ensure that if a code acquires
        # right after unblanking, the data will be correct. Use 1s to be really safe.
        if not blanked:
            time.sleep(1.0)  # s
        return blanked

    def _setExternal(self, external):
        logging.debug(f"Setting {self.name} external mode to {external}")
        # 1 if external, 0 if not
        self._device_handler.ScSetExternal(int(external))
        # Tests on a SEM showed that, contrarily to the blanker, external mode switch is very fast.
        # So there is not need to wait explicitly after changing the value.
        return external

    def _onScale(self, s):
        self._updatePixelSize()

    def _updatePixelSize(self):
        """
        Update the pixel size using the horizontalFoV
        """
        fov = self.horizontalFoV.value

        pxs = (fov / self._shape[0],
               fov / self._shape[1])

        # it's read-only, so we change it only via _value
        self.pixelSize._value = pxs
        self.pixelSize.notify(pxs)

        # If scaled up, the pixels are bigger
        pxs_scaled = (pxs[0] * self.scale.value[0], pxs[1] * self.scale.value[1])
        self.updateMetadata({model.MD_PIXEL_SIZE: pxs_scaled})

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
        # no need to update translation, as it's independent of scale and will
        # be checked by setting the resolution.
        self.resolution.value = new_resolution  # will call _setResolution()

        return value

    def _setResolution(self, value):
        """
        value (0<int, 0<int): defines the size of the resolution. If the
         resolution is not possible, it will pick the most fitting one. It will
         recenter the translation if otherwise it would be out of the whole
         scanned area.
        returns the actual value used
        """
        max_size = (int(self._shape[0] // self._scale[0]),
                    int(self._shape[1] // self._scale[1]))

        # at least one pixel, and at most the whole area
        size = (max(min(value[0], max_size[0]), 1),
                max(min(value[1], max_size[1]), 1))
        self._resolution = size

        # setting the same value means it will recheck the boundaries with the
        # new resolution, and reduce the distance to the center if necessary.
        self.translation.value = self.translation.value
        return size

    def _setTranslation(self, value):
        """
        value (float, float): shift from the center. It will always ensure that
          the whole ROI fits the screen.
        returns actual shift accepted
        """
        # compute the min/max of the shift. It's the same as the margin between
        # the centered ROI and the border, taking into account the scaling.
        max_tran = ((self._shape[0] - self._resolution[0] * self._scale[0]) / 2,
                    (self._shape[1] - self._resolution[1] * self._scale[1]) / 2)

        # between -margin and +margin
        tran = (max(min(value[0], max_tran[0]), -max_tran[0]),
                max(min(value[1], max_tran[1]), -max_tran[1]))
        return tran


    def _setRotation(self, value: float) -> float:
        """
        value: Scan rotation in radians
        """

        rotation_degrees = math.degrees(value)
        self._device_handler.SetImageRot(rotation_degrees)
        return value

    def pixelToPhy(self, px_pos):
        """
        Converts a position in pixels to physical (at the current magnification)
        Note: the convention is that in internal coordinates Y goes down, while
        in physical coordinates, Y goes up.
        px_pos (tuple of 2 floats): position in internal coordinates (pixels)
        returns (tuple of 2 floats): physical position in meters
        """
        pxs = self.pixelSize.value  # m/px
        phy_pos = (px_pos[0] * pxs[0], -px_pos[1] * pxs[1])  # - to invert Y
        return phy_pos

    def _pollVAs(self):
        try:
            with self.parent._acquisition_init_lock:
                logging.debug(f"Updating {self.name} FoV, voltage and current")
                self._updateHorizontalFOV()
                # TODO: update power
                with self.parent._acq_progress_lock:
                    prev_volt = self.accelVoltage._value
                    new_volt = self._device_handler.HVGetVoltage()
                    if prev_volt != new_volt:
                        # Skip the setter
                        self.accelVoltage._value = new_volt
                        self.accelVoltage.notify(new_volt)

                    prev_dt = self.dwellTime._value
                    prev_idx = min(self.dwell_time_lookup, key=lambda k: abs(self.dwell_time_lookup[k] - prev_dt))
                    new_dt_idx = self._device_handler.ScGetSpeed()
                    if prev_idx != new_dt_idx:
                        new_dt = self.dwell_time_lookup[new_dt_idx]
                        self.dwellTime._value = new_dt
                        self.dwellTime.notify(new_dt)

                    prev_rotation = self.rotation._value
                    new_rotation = math.radians(self._device_handler.GetImageRot()) % (2 * math.pi)
                    if prev_rotation != new_rotation:
                        self.rotation._value = new_rotation
                        self.rotation.notify(new_rotation)

                    if self._device_handler.device_type == DeviceType.ELECTRON:
                        # if blanker is in auto, don't change its value
                        # NOTE: FIB does not allow for blanker control, seemingly
                        if self.blanker.value is not None:
                            bmode = self._device_handler.ScGetBlanker(1)
                            blanked = (bmode != 0)
                            if blanked != self.blanker._value:
                                self.blanker._value = blanked
                                self.blanker.notify(blanked)

                        prev_pc = self.probeCurrent._value
                        # For FIB, the beam current getter briefly disables the Essence interface.
                        # Therefore, we don't poll current for FIB.
                        new_pc = self._device_handler.GetBeamCurrent() * 1e-12  # Convert from pA to A
                        if prev_pc != new_pc:
                            self.probeCurrent._value = new_pc
                            self.probeCurrent.notify(new_pc)

                    new_ext = bool(self._device_handler.ScGetExternal())
                    if new_ext != self.external._value:
                        self.external._value = new_ext
                        self.external.notify(new_ext)
        except TypeError:
            # A TypeError is caused by one of the getters returning a None, which is an indication that the API
            # is blocked. Most of the time momentarily, so handle it gracefully.
            logging.warning("Could not poll, probably because the SharkSEM API is momentarily blocked")
        except Exception:
            logging.exception("Unexpected failure during VAs polling")

    def terminate(self):
        self._va_poll.cancel()
        self._va_poll.join(5)


class Detector(model.Detector):
    """
    This is an extension of model.Detector class. It performs the main functionality
    of the SEM. It sets up a Dataflow and notifies it every time that an SEM image
    is captured.
    """
    def __init__(self, name, role, parent, channel, detector, scanner, **kwargs):
        """
        channel (0<= int): input channel from which to read
        detector (0<= int): detector index
        """
        # It will set up ._shape and .parent
        model.Detector.__init__(self, name, role, parent=parent, **kwargs)
        self._scanner = scanner
        self._device_handler = self._scanner._device_handler

        self._channel = channel
        self._detector = self.get_detector_idx(detector)
        self._device_handler.DtSelect(self._channel, self._detector)

        # 8 or 16 bits image
        self.bpp = model.IntEnumerated(DEFAULT_BITDEPTH, {8, 16}, unit="bit")
        self._shape = (2 ** 16,)  # only one point

        # will take care of executing autocontrast asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        self.data = SEMDataFlow(self, parent)

        # Special event to request software unblocking on the scan
        self.softwareTrigger = model.Event()

    def get_detector_idx(self, detector: Union[int, str]) -> int:
        """Get the index of the detector by int (do nothing) or by name (match with Tescan's available detectors).

        :param detector: the detector index or the name of the detector
        :returns: the detector's index on the Tescan hardware
        """
        detector_idx = None
        if isinstance(detector, int):
            detector_idx = detector
        elif isinstance(detector, str):
            # Obtain the available detectors. An example of such a string:
            # "det.0.name=MD\ndet.0.detector=1\ndet.0.ADCinput=0\ndet.1.name=E-T\ndet.1.detector=4\ndet.1.ADCinput=1\n
            available_detectors = self._device_handler.DtEnumDetectors()
            # First find the enumeration index for the desired detector (by name)
            enum_idx_for_name = re.findall(rf"det.([0-9])+.name={detector.lower()}", available_detectors.lower())
            if enum_idx_for_name:
                enum_idx_for_name = enum_idx_for_name[0]  # There should only one captured group
                # Now find the corresponding detector idx (which is not the same as the enumeration index!).
                det_idx_for_name = re.findall(rf"det.{enum_idx_for_name}.detector=([0-9]+)", available_detectors)
                if det_idx_for_name:
                    detector_idx = int(det_idx_for_name[0])

        if detector_idx:
            return detector_idx
        else:
            raise ValueError(
                f"The 'detector' parameter from the config ({detector}) does not match an available detector"
            )

    @roattribute
    def channel(self):
        return self._channel

    @roattribute
    def detector(self):
        return self._detector

    @isasync
    def applyAutoContrastBrightness(self):
        # Create ProgressiveFuture and update its state to RUNNING
        est_start = time.time() + 0.1
        f = model.ProgressiveFuture(start=est_start,
                                    end=est_start + 5)  # rough time estimation

        return self._executor.submitf(f, self._applyAutoContrastBrightness, f)

    def _applyAutoContrastBrightness(self, future):
        with self.parent._acquisition_init_lock:
            with self.parent._acq_progress_lock:
                self._device_handler.DtAutoSignal(self._channel)

    def terminate(self):
        self._device_handler.DtEnable(self._channel, 0, self.bpp.value)


class SEMDataFlow(model.DataFlow):
    """
    This is an extension of model.DataFlow. It receives notifications from the
    detector component once the SEM output is captured. This is the dataflow to
    which the SEM acquisition streams subscribe.
    """
    def __init__(self, detector, sem):
        """
        detector (model.Detector): the detector that the dataflow corresponds to
        sem (model.Emitter): the SEM
        """
        model.DataFlow.__init__(self)
        self.component = weakref.ref(detector)
        self._sem = weakref.proxy(sem)

        self._sync_event = None  # event to be synchronised on, or None
        self._evtq = None  # a Queue to store received events (= float, time of the event)

    # start/stop_generate are _never_ called simultaneously (thread-safe)
    def start_generate(self):
        comp = self.component()
        if comp is None:
            # sem/component has been deleted, it's all fine, we'll be GC'd soon
            return

        try:
            self._sem.start_acquire(comp)
        except ReferenceError:
            # sem/component has been deleted, it's all fine, we'll be GC'd soon
            pass
        except Exception as e:
            logging.error(f"Acquisition could not be started {e}")

    def stop_generate(self):
        comp = self.component()
        if comp is None:
            # sem/component has been deleted, it's all fine, we'll be GC'd soon
            return

        try:
            self._sem.stop_acquire(comp)
            if self._sync_event:
                self._evtq.put(None)  # in case it was waiting for an event
        except ReferenceError:
            # sem/component has been deleted, it's all fine, we'll be GC'd soon
            pass

    def synchronizedOn(self, event):
        """
        Synchronize the acquisition on the given event. Every time the event is
          triggered, the scanner will start a new acquisition/scan.
          The DataFlow can be synchronized only with one Event at a time.
          However each DataFlow can be synchronized, separately. The scan will
          only start once each active DataFlow has received an event.
        event (model.Event or None): event to synchronize with. Use None to
          disable synchronization.
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


STAGE_WAIT_DURATION = 20e-03  # s
STAGE_WAIT_TIMEOUT = 5  # s
STAGE_FRACTION_TOTAL_MOVE = 0.01  # tolerance of the requested stage movement
STAGE_TOL_LINEAR = 1e-06  # in m, minimum tolerance
STAGE_TOL_ROTATION = 0.00436  # in radians (0.25 degrees), minimum tolerance


class Stage(model.Actuator):
    """
    This is an extension of the model.Actuator class. It provides functions for
    moving the SEM stage with the Tescan Essence software.
    """
    def __init__(self, name, role, parent, **kwargs):
        self._position = {}
        axes_def = {}
        # limits are always returned as a list with min and max values in the order of x,y,z,r,t
        # the list should always return 10 values and if an axis is not motorized, limits are zero
        axes_rng = parent._device.StgGetLimits(1)  # using the soft limits parameter here
        axes_motorized = parent._device.StgGetMotorized()

        for num, ax in enumerate(["x", "y", "z", "rz", "rx"]):
            # axes ranges are always returned in pairs of 2 values (min, max)
            if len(axes_rng) >= 2:
                # if an axis is not motorized do not add it to the axes definition
                if axes_motorized[num]:
                    # make a distinction between linear axes and rotational ones
                    # rotational axes will need their range converted to radians
                    if ax.startswith("r"):
                        if axes_rng[:2] == [-360, 360]:  # Full rotation: report as 0 -> 360°
                            axes_rng[0] = 0
                        axes_def[ax] = model.Axis(unit="rad",
                                                  range=(math.radians(axes_rng[0]),
                                                         math.radians(axes_rng[1])))
                    else:
                        # convert the axis min and max range to meters
                        axis_m = (axes_rng[0] * 1e-3, axes_rng[1] * 1e-3)
                        # note that the linear axes range is switched and inverted by default (for historical reasons)
                        axes_def[ax] = model.Axis(unit="m", range=(-axis_m[1], -axis_m[0]))
                # slice the axes_rng list for the next iteration
                axes_rng = axes_rng[2::]
            else:
                # if there are fewer axes present than 5, stop updating the axes definition
                break

        # Demand calibrated stage
        if parent._device.StgIsCalibrated() != 1:
            logging.warning("Stage is not calibrated. Move commands will be ignored until it has been calibrated.")
            # TODO: support doing it from Odemis, via reference()
            # parent._device.StgCalibrate()

        # Wait for stage to be stable after calibration
        while parent._device.StgIsBusy() != 0:
            # If the stage is busy (movement is in progress), current position is
            # updated approximately every 500 ms
            time.sleep(0.5)

        model.Actuator.__init__(self, name, role, parent=parent, axes=axes_def, **kwargs)

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute({}, unit="m", readonly=True)
        self._updatePosition()

        self._xyz_poll = util.RepeatingTimer(5, self._pollXYZ, "XYZ polling")
        self._xyz_poll.start()

    def _pollXYZ(self):
        try:
            with self.parent._acquisition_init_lock:
                with self.parent._acq_progress_lock:
                    self._updatePosition()
        except TypeError:
            # A TypeError is caused by one of the getters returning a None, which is an indication that the API
            # is blocked. Most of the time momentarily, so handle it gracefully.
            logging.warning("Could not poll, probably because the SharkSEM API is momentarily blocked")
        except Exception:
            logging.exception("Unexpected failure during XYZ polling")

    def _checkPosition(self, orig_pos: Dict[str, float], target_pos: Dict[str, float], timeout: float = STAGE_WAIT_TIMEOUT):
        """
        Checks and waits for the current stage position to report the requested stage position given by pos.
        :param orig_pos: original stage position before the stage movement in absolute coordinates
        :param target_pos: requested stage position in absolute coordinates
        :param timeout: maximum time in seconds to wait for the stage to report the requested position
        :raises ValueError: if the stage position is not near the requested position after a timeout
        """
        # Drop axes from the original position, which are not important because they have not moved
        orig_pos = {a: orig_pos[a] for a, nv in target_pos.items() if nv != orig_pos[a]}

        # TODO: base the timeout on stage movement time estimation, needs to be checked on hardware
        expected_end_time = time.time() + timeout  # s

        # Update rotational and linear tolerances according to the magnitude of requested change
        linear_axes_to_check = {"x", "y", "z"}.intersection(target_pos.keys())
        rotational_axes_to_check = {"rx", "rz"}.intersection(target_pos.keys())
        current_pos = self._position
        movement_req = {ax: STAGE_FRACTION_TOTAL_MOVE * abs(target_pos[ax] - current_pos[ax]) for ax in
                        target_pos.keys()}
        tol_linear = STAGE_TOL_LINEAR
        tol_rotation = STAGE_TOL_ROTATION
        if linear_axes_to_check:
            movement_req_linear = [movement_req[ax] for ax in linear_axes_to_check]
            tol_linear = max(min(movement_req_linear), STAGE_TOL_LINEAR)  # m

        if rotational_axes_to_check:
            movement_req_rotational = [movement_req[ax] for ax in rotational_axes_to_check]
            tol_rotation = max(min(movement_req_rotational), STAGE_TOL_ROTATION)  # radians

        axes_to_check = linear_axes_to_check | rotational_axes_to_check

        while not isNearPosition(current_pos=current_pos, target_position=target_pos,
                                 axes=axes_to_check, rot_axes=rotational_axes_to_check,
                                 atol_linear=tol_linear, atol_rotation=tol_rotation):
            time.sleep(STAGE_WAIT_DURATION)
            self._updatePosition()
            current_pos = self._position
            if time.time() > expected_end_time:
                raise ValueError(
                    f"Stage position after + {timeout} s is {current_pos} instead of requested position: "
                    f"{target_pos}. Start position: {orig_pos}. Aborting move.")
        else:
            logging.debug("Position has updated fully: from %s -> %s", orig_pos,
                          current_pos)

    def _updatePosition(self):
        """
        update the position VA
        """
        x, y, z, rz, rx = self.parent._device.StgGetPosition()
        self._position["x"] = -x * 1e-3
        self._position["y"] = -y * 1e-3
        self._position["z"] = -z * 1e-3

        if "rz" in self.axes:
            self._position["rz"] = math.radians(rz)
        if "rx" in self.axes:
            self._position["rx"] = math.radians(rx)

        # it's read-only, so we change it via _value
        pos = self._applyInversion(self._position)
        self.position._set_value(pos, force_write=True)
        logging.debug("Updated stage position to %s", pos)

    def _doMoveAbs(self, pos: dict):
        """
        move to the requested (absolute) position. If a move on a requested axis is deemed insignificant
        by this method, the move for that axis will not be requested. This is to prevent the stage from applying undesired anti-backlash
        correction.
        :param pos (dict[str, float]): positions of linear axes in m or rotational axes in radians
        """
        # TODO: support cancelling (= call StgStop) will be addressed in separate PR
        with self.parent._acq_progress_lock:
            logging.debug("Requesting stage move to %s", pos)

            x, y, z, rz, rx = self.parent._device.StgGetPosition()
            current_pos = {"x": x, "y": y, "z": z, "rz": rz, "rx": rx}
            req_pos = {}

            for axis in {"x", "y", "z", "rz", "rx"}:
                if axis in pos:
                    # Convert from m to mm and invert for the linear axes
                    # Also, per axis, check if requested move is significant enough. Otherwise, drop the request to move that axis.
                    # This helps preventing unneeded stage movement due to anti-backlash correction.
                    if axis in {"x", "y", "z"}:
                        tescan_pos = -pos[axis] * 1e3
                        move_distance = abs(tescan_pos - current_pos[axis])
                        if move_distance < MIN_MOVE_SIZE_LIN_MM:  # 10e-9 m (delmic) --> 10e-6 mm (tescan)
                            tescan_pos = None
                            logging.debug(f"Requested move in axis {axis} dropped (current: {current_pos[axis]}, requested: {tescan_pos})")
                    # convert from radians to degrees for the rotational axes
                    elif axis in {"rz", "rx"}:
                        tescan_pos = math.degrees(pos[axis])
                        move_distance = abs(tescan_pos - current_pos[axis])
                        if move_distance < MIN_MOVE_SIZE_ROT_DEG:
                            tescan_pos = None
                            logging.debug(f"Requested move in axis {axis} dropped (current: {current_pos[axis]}, requested: {tescan_pos})")
                else:
                    tescan_pos = None
                req_pos[axis] = tescan_pos

            orig_pos = self._position

            # always issue a move command containing values for all 5 axes so
            # there is no separate code needed for backward compatibility
            self.parent._device.StgMoveTo(req_pos["x"],
                                          req_pos["y"],
                                          req_pos["z"],
                                          req_pos["rz"],
                                          req_pos["rx"],
                                          )

            # a very small delay before checking if the stage is busy
            time.sleep(0.1)

            # Wait until move is completed
            while self.parent._device.StgIsBusy():
                time.sleep(0.1)

            logging.debug("Stage move to %s reported as completed. Checking the new stage position...", pos)
            self._updatePosition()
            # In some cases, the stage fails to move to the requested position but no error is raised by the
            # Tescan API. This check will ensure that the stage has reached the requested position within a
            # certain tolerance otherwise it will raise an error and stop the subsequent moves from being executed,
            # for e.g. the other stage axes or the subsequent objective stage movement will halt if one of
            # the stage axis failed to move or did not move correctly.
            self._checkPosition(orig_pos, pos, timeout=STAGE_WAIT_TIMEOUT)

    def _doMoveRel(self, shift: dict):
        """
        Request the position in absolute coordinates and create new positions after applying shift values
        :param shift (dict[str, float]): shift of linear axes in m or rotational axes in radians
        """
        self._updatePosition()
        pos = {axis: position for axis, position in self._position.items() if axis in shift}

        # add the requested change to the current position
        for axis, change in shift.items():
            pos[axis] += change
            if axis in ("rx", "rz"):
                rng = self.axes[axis].range
                if abs(rng[1] - rng[0]) >= 2 * math.pi:
                    # for full rotational axes this check maps values outside the range to between 0 and 2pi
                    pos[axis] = (pos[axis] - rng[0]) % (2 * math.pi) + rng[0]

        self._checkMoveAbs(self._applyInversion(pos))
        self._doMoveAbs(pos)

    @isasync
    def moveRel(self, shift):
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        shift = self._applyInversion(shift)

        return self._executor.submit(self._doMoveRel, shift)

    @isasync
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversion(pos)

        return self._executor.submit(self._doMoveAbs, pos)

    def stop(self, axes=None):
        self.parent._device.StgStop()  # TODO: can be removed once move cancellation is supported
        # Empty the queue for the given axes
        self._executor.cancel()
        self.parent._device.StgStop()  # Make sure it's stopped in any case
        logging.info("Stopping all axes: %s", ", ".join(self.axes))

    def terminate(self):
        self._xyz_poll.cancel()
        self._xyz_poll.join(5)
        if self._executor:
            self.stop()
            self._executor.shutdown()
            self._executor = None


class EbeamFocus(model.Actuator):
    """
    This is an extension of the model.Actuator class. It provides functions for
    adjusting the ebeam focus by changing the working distance i.e. the distance
    between the end of the objective and the surface of the observed specimen
    """
    def __init__(self, name, role, parent, axes, ranges=None, **kwargs):
        assert len(axes) > 0
        if ranges is None:
            ranges = {}

        axes_def = {}
        self._position = {}

        # Just z axis
        a = axes[0]
        # The maximum, obviously, is not 1 meter. We do not actually care
        # about the range since Tescan API will adjust the value set if the
        # required one is out of limits.
        rng = ranges.get(a, (0, 1))
        axes_def[a] = model.Axis(unit="m", range=rng)

        model.Actuator.__init__(self, name, role, parent=parent, axes=axes_def, **kwargs)

        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute({}, unit="m", readonly=True)
        self._updatePosition()

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

    def _updatePosition(self):
        """
        update the position VA
        """
        wd = self.parent._device.GetWD()
        self._position["z"] = wd * 1e-3
        # it's read-only, so we change it via _value
        pos = self._applyInversion(self._position)
        self.position._set_value(pos, force_write=True)

    def _doMove(self, pos):
        """
        move to the position
        """
        # Perform move through Tescan API
        # Position from m to mm and inverted
        with self.parent._acq_progress_lock:
            self.parent._device.SetWD(self._position["z"] * 1e03)
            # Obtain the finally reached position after move is performed.
            self._updatePosition()
        # Changing WD results to change in fov
        self.parent._scanners["scanner"]._updateHorizontalFOV()

    @isasync
    def moveRel(self, shift):
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)

        shift = self._applyInversion(shift)

        for axis, change in shift.items():
            self._position[axis] += change

        pos = self._position
        return self._executor.submit(self._doMove, pos)

    @isasync
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversion(pos)

        for axis, new_pos in pos.items():
            self._position[axis] = new_pos

        pos = self._position
        return self._executor.submit(self._doMove, pos)

    def stop(self, axes=None):
        # Empty the queue for the given axes
        self._executor.cancel()
        logging.info("Cancelled moved on all axes: %s", ", ".join(self.axes))

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown()
            self._executor = None


class ChamberView(model.DigitalCamera):
    """
    Represents one chamber camera - chamberscope. Provides video consisted of
    static images sent in regular intervals.
    This implementation is for the Tescan. Note that some Tescans have several
    chamber cameras, but the current API is limited to acquiring images from the
    first one.
    """
    def __init__(self, name, role, parent, **kwargs):
        """
        Initialises the device.
        Raise an exception if the device cannot be opened.
        """
        model.DigitalCamera.__init__(self, name, role, parent=parent, **kwargs)

        # Parameters explanation: Chamber camera enabled on channel 0 (reserved),
        # without zoom applied so we get the maximum size of the image (1),
        # in the maximum fps (5), and compression mode 0 (must be so, according,
        # to Tescan API documentation).
        self.parent._device.CameraEnable(0, 1, 5, 0)
        # Wait for camera to be enabled
        while (self.parent._device.CameraGetStatus(0))[0] != 1:
            time.sleep(0.5)
        # Get a first image to determine the resolution
        width, height, img_str = self.parent._device.FetchCameraImage(0)
        self.parent._device.CameraDisable()
        resolution = (height, width)
        self._shape = resolution + (2 ** 8,)
        self.resolution = model.ResolutionVA(resolution, [resolution, resolution],
                                             readonly=True)

        self.acquisition_lock = threading.Lock()
        self.acquire_must_stop = threading.Event()
        self.acquire_thread = None

        self.data = ChamberDataFlow(self)

        logging.debug("Camera component ready to use.")

    def GetStatus(self):
        """
        return int: chamber camera status, 0 - off, 1 - on
        """
        with self.parent._acq_progress_lock:
            status = self.parent._device.CameraGetStatus(0)  # channel 0, reserved
        return status[0]

    def start_flow(self, callback):
        """
        Set up the chamber camera and start acquiring images.
        callback (callable (DataArray) no return):
         function called for each image acquired
        """
        with self.parent._acq_progress_lock:
            self.parent._device.CameraEnable(0, 1, 5, 0)

        # if there is a very quick unsubscribe(), subscribe(), the previous
        # thread might still be running
        self.wait_stopped_flow()  # no-op is the thread is not running
        self.acquisition_lock.acquire()

        assert(self.GetStatus() == 1)  # Just to be sure

        target = self._acquire_thread_continuous
        self.acquire_thread = threading.Thread(target=target,
                name="chamber camera acquire flow thread",
                args=(callback,))
        self.acquire_thread.start()

    def req_stop_flow(self):
        """
        Cancel the acquisition of a flow of images: there will not be any notify() after this function
        Note: the thread should be already running
        Note: the thread might still be running for a little while after!
        """
        assert not self.acquire_must_stop.is_set()
        self.acquire_must_stop.set()
        # self.parent._device.CancelRecv()
        with self.parent._acq_progress_lock:
            self.parent._device.CameraDisable()

    def _acquire_thread_continuous(self, callback):
        """
        The core of the acquisition thread. Runs until acquire_must_stop is set.
        """
        try:
            while not self.acquire_must_stop.is_set():
                with self.parent._acq_progress_lock:
                    width, height, img_str = self.parent._device.FetchCameraImage(0)
                sem_img = numpy.frombuffer(img_str, dtype=numpy.uint8)
                sem_img.shape = (height, width)
                logging.debug("Acquiring chamber image of %s", sem_img.shape)
                array = model.DataArray(sem_img)
                # update resolution
                self.resolution._set_value(sem_img.shape, force_write=True)
                # first we wait ourselves the typical time (which might be very long)
                # while detecting requests for stop
                # If the Chamber view is just enabled it may take several seconds
                # to get the first image.

                callback(self._transposeDAToUser(array))

        except Exception:
            logging.exception("Failure during acquisition")
        finally:
            self.acquisition_lock.release()
            logging.debug("Acquisition thread closed")
            self.acquire_must_stop.clear()

    def wait_stopped_flow(self):
        """
        Waits until the end acquisition of a flow of images. Calling from the
         acquisition callback is not permitted (it would cause a dead-lock).
        """
        # "if" is to not wait if it's already finished
        if self.acquire_must_stop.is_set():
            self.acquire_thread.join(10)  # 10s timeout for safety
            if self.acquire_thread.is_alive():
                raise OSError("Failed to stop the acquisition thread")
            # ensure it's not set, even if the thread died prematurely
            self.acquire_must_stop.clear()

    def terminate(self):
        """
        Must be called at the end of the usage
        """
        self.req_stop_flow()
        self.wait_stopped_flow()


class ChamberDataFlow(model.DataFlow):
    def __init__(self, camera):
        """
        camera: chamber camera instance ready to acquire images
        """
        model.DataFlow.__init__(self)
        self.component = weakref.ref(camera)

    def start_generate(self):
        comp = self.component()
        if comp is None:
            return
        comp.start_flow(self.notify)

    def stop_generate(self):
        comp = self.component()
        if comp is None:
            return
        comp.req_stop_flow()


PRESSURE_VENTED = 1e05  # Pa
PRESSURE_PUMPED = 1e-02  # Pa
VACUUM_TIMEOUT = 5 * 60  # seconds


class ChamberPressure(model.Actuator):
    """
    This is an extension of the model.Actuator class. It provides functions for
    adjusting the chamber pressure. It actually allows the user to evacuate or
    vent the chamber and get the current pressure of it.
    """
    def __init__(self, name, role, parent, ranges=None, **kwargs):
        axes = {"vacuum": model.Axis(unit="Pa",
                                       choices={PRESSURE_VENTED: "vented",
                                                PRESSURE_PUMPED: "vacuum"})}
        model.Actuator.__init__(self, name, role, parent=parent, axes=axes, **kwargs)

        # last official position
        if self.GetStatus() == 0:
            self._position = PRESSURE_PUMPED
        else:
            self._position = PRESSURE_VENTED

        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute({}, unit="Pa", readonly=True)
        # Almost the same as position, but gives the actual value
        self.pressure = model.VigilantAttribute({}, unit="Pa", readonly=True)
        self._updatePosition()

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

    def GetStatus(self):
        """
        return int: vacuum status,
            -1 error
            0 ready for operation
            1 pumping in progress
            2 venting in progress
            3 vacuum off (pumps are switched off, valves are closed)
            4 chamber open
        """
        with self.parent._acq_progress_lock:
            status = self.parent._device.VacGetStatus()  # channel 0, reserved
        return status

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown()
            self._executor = None

    def _updatePosition(self):
        """
        update the position VA and .pressure VA
        """
        # it's read-only, so we change it via _value
        pos = self.parent._device.VacGetPressure(0)
        self.pressure._value = pos
        self.pressure.notify(pos)

        # .position contains the last known/valid position
        # it's read-only, so we change it via _value
        self.position._value = {"vacuum": self._position}
        self.position.notify(self.position.value)

    @isasync
    def moveRel(self, shift):
        self._checkMoveRel(shift)

        # convert into an absolute move
        pos = {}
        for a, v in shift.items:
            pos[a] = self.position.value[a] + v

        return self.moveAbs(pos)

    @isasync
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        return self._executor.submit(self._changePressure, pos["vacuum"])

    def _changePressure(self, p):
        """
        Synchronous change of the pressure
        p (float): target pressure
        """
        if p["vacuum"] == PRESSURE_VENTED:
            self.parent._device.VacVent()
        else:
            self.parent._device.VacPump()

        start = time.time()
        while not self.GetStatus() == 0:
            if (time.time() - start) >= VACUUM_TIMEOUT:
                raise TimeoutError("Vacuum action timed out")
            # Update chamber pressure until pumping/venting process is done
            self._updatePosition()
        self._position = p
        self._updatePosition()

    def stop(self, axes=None):
        self._executor.cancel()
        logging.warning("Stopped pressure change")


class Light(model.Emitter):
    """
    Chamber illumination LED component.
    """
    def __init__(self, name, role, parent, **kwargs):
        model.Emitter.__init__(self, name, role, parent=parent, **kwargs)

        self._shape = ()
        self.power = model.ListContinuous([10], ((0,), (10,)), unit="W", cls=(int, float),
                                          setter=self._setPower)
        # turn on when initializing
        self.parent._device.ChamberLed(1)
        # just one band: white
        # TODO: update spectra VA to support the actual spectra of the lamp
        self.spectra = model.ListVA([(380e-9, 390e-9, 560e-9, 730e-9, 740e-9)],
                                    unit="m", readonly=True)

    def _setPower(self, value):
        # Switch the chamber LED based on the power value (On in case of max,
        # off in case of min)
        if value[0] == self.power.range[1][0]:
            self.parent._device.ChamberLed(1)
            return self.power.range[1]
        else:
            self.parent._device.ChamberLed(0)
            return self.power.range[0]
