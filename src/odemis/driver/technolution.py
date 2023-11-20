# -*- coding: utf-8 -*-
"""
Created on 11 May 2020

@author: Sabrina Rossberger, Kornee Kleijwegt

Copyright © 2019-2021 Kornee Kleijwegt, Delmic

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

# Driver/wrapper for the ASP API in Odemis which can connect Odemis to the ASM API made by Technolution for the
# multi-beam project
import json
import logging
import math
import os
import queue
import re
import threading
import time
from io import BytesIO
from urllib.parse import urlparse

import numpy
import pkg_resources
from PIL import Image
from requests import Session
from scipy import signal

import technolution_asm
from odemis import model
from odemis.model import HwError
from odemis.util import almost_equal
from technolution_asm.models.calibration_loop_parameters import CalibrationLoopParameters
from technolution_asm.models.cell_parameters import CellParameters
from technolution_asm.models.field_meta_data import FieldMetaData
from technolution_asm.models.mega_field_meta_data import MegaFieldMetaData

SUPPORTED_VERSION = "3.0.0"
if pkg_resources.parse_version(technolution_asm.__version__) < pkg_resources.parse_version(SUPPORTED_VERSION):
    raise ImportError(f"Version {technolution_asm.__version__} for technolution_asm not supported,"
                      f"version {SUPPORTED_VERSION} or higher is expected")

VOLT_RANGE = (-10, 10)
I16_SYM_RANGE = (-2 ** 15, 2 ** 15)  # Note: on HW range is not symmetrically (-2**15, 2**15 - 1)
DATA_CONTENT_TO_ASM = {"empty": None, "thumbnail": True, "full": False}
RUNNING = "installation in progress"
FINISHED = "last installation successful"
FAILED = "last installation failed"

ASM_USER_CHARS = r'[A-Za-z0-9]+'  # + -> should be at least one character
ASM_PASSWORD_CHARS = r'[A-Za-z0-9]+'
ASM_HOST_CHARS = r'[A-Za-z0-9.]+'
ASM_PATH_CHARS = r'[A-Za-z0-9/_()-]+'
ASM_SUBDIR_CHARS = r'[A-Za-z0-9/_()-.]*'  # * -> subdirectories can also be empty string
ASM_FILE_CHARS = r'[A-Za-z0-9_()-]+'


def convertRange(value, value_range, output_range):
    """
    Converts a value from one range to another range. Can be used to map value(s) from one unit to another.
    :param value: (tuple/array/list) Input value(s) of any unit to be mapped to the new range.
    :param value_range: (tuple/array/list) Min and max values of the range for the input value(s).
    :param output_range: (tuple/array/list) Min and max values of the range that the input value should be mapped to.
    :return (numpy.array): Input value(s) mapped to the new range with same shape as input value(s).
    """
    # convert to numpy arrays
    input_range = numpy.array(value_range)
    output_range = numpy.array(output_range)

    # determine the span of each input range
    span_input_range = value_range[1] - value_range[0]
    span_output_range = output_range[1] - output_range[0]

    # map to range [0, 1]
    normalized_value = (value - input_range[0]) / span_input_range
    # map to output range
    mapped_value = normalized_value * span_output_range + output_range[0]

    return mapped_value


class AcquisitionServer(model.HwComponent):
    """
    Component representing the Acquisition server module which is connected via the ASM API. This module controls the
    camera (mppc sensor) for acquiring the image data. It is also connected to the Scan and Acquisition module (SAM),
    which triggers the scanner on the SEM to move the electron beam. Moreover it controls the de-scanner which counter
    scans the scanner movement to ensure that the collected signal always hits the center of each mppc cell on the
    detector.
    """

    def __init__(self, name, role, host, children, externalStorage, **kwargs):
        """
        Initialize the Acquisition server and the connection with the ASM API.

        :param name (str): Name of the component
        :param role (str): Role of the component
        :param host (str): URL of the host (ASM)
        :param children (dict): dictionary containing HW components and their respective configuration
        :param externalStorage (dict): keys with the username, password, host and directory of the external storage
        :param kwargs:
        """

        super(AcquisitionServer, self).__init__(name, role, **kwargs)

        self._host = host
        # Use session object avoids creating a new connection for each message sent
        # (note: Session() auto-reconnects if the connection is broken for a new call)
        self._session = Session()

        # Test the connection with the host and stop any acquisition if already one was in progress
        try:
            self.asmApiPostCall("/scan/finish_mega_field", 204)  # Stop acquisition
            self.asmApiGetCall("/scan/clock_frequency", 200)  # Test connection from ASM to SAM

        except Exception as error:
            logging.warning("First try to connect with the ASM host was not successful.\n"
                            "This is possible because of an incorrect starting sequence, first the SAM should be "
                            "started up, then the ASM. To fix this a second call to the ASM will be made.\n"
                            "Received error:\n %s" % error)
            try:
                self.asmApiPostCall("/scan/finish_mega_field", 204)  # Stop acquisition
                self.asmApiGetCall("/scan/clock_frequency", 200)  # Test connection from ASM to SAM

            except Exception as error:
                logging.error("Could not connect with the ASM host.\n"
                              "Check if the connection with the host is available and if the host URL is entered "
                              "correctly. Received error:\n %s" % error)
                raise HwError("Could not connect with the ASM host.\n"
                              "Check if the connection with the host is available and if the host URL is entered "
                              "correctly.")

        # NOTE: Do not write real username/password here since this is published on github in plain text!
        # example = ftp://username:password@127.0.0.1:5000/directory/
        # fixed url of the external storage configuration
        self.externalStorageURL = model.StringVA('ftp://%s:%s@%s/%s' %
                                                 (externalStorage["username"],
                                                  externalStorage["password"],
                                                  externalStorage["host"],
                                                  externalStorage["directory"]),
                                                 setter=self._setURL, readonly=True)
        self.externalStorageURL._set_value(self.externalStorageURL.value, force_write=True)  # check URL ok

        # VA to switch between calibration and acquisition mode (megafield acquisition)
        self.calibrationMode = model.BooleanVA(False, setter=self._setCalibrationMode)

        # contains the current calibration settings
        self._calibrationParameters = None

        # TODO: Commented out because not present on EA
        # self.asmApiPostCall("/config/set_system_sw_name?software=%s" % name, 204)

        # Read HW and SW version from ASM and SAM
        # TODO make call set_system_sw_name to new simulator (if implemented)
        self._swVersion = "ASM service version '%s' " % (self.getAsmServiceVersion())
        self._hwVersion = "SAM firmware version '%s', SAM service version '%s'" % (self.getSamFirmwareVersion(),
                                                                                   self.getSamServiceVersion())

        # Order of initialisation matters due to dependency of VA's and variables in between children.
        try:
            ckwargs = children["EBeamScanner"]
        except Exception:
            raise ValueError("Required child EBeamScanner not provided")
        self._ebeam_scanner = EBeamScanner(parent=self, daemon=kwargs.get("daemon"), **ckwargs)
        self.children.value.add(self._ebeam_scanner)

        try:
            ckwargs = children["MirrorDescanner"]
        except Exception:
            raise ValueError("Required child MirrorDescanner not provided")
        self._mirror_descanner = MirrorDescanner(parent=self, daemon=kwargs.get("daemon"), **ckwargs)
        self.children.value.add(self._mirror_descanner)

        try:
            ckwargs = children["MPPC"]
        except Exception:
            raise ValueError("Required child mppc not provided")
        self._mppc = MPPC(parent=self, daemon=kwargs.get("daemon"), **ckwargs)
        self.children.value.add(self._mppc)

    def terminate(self):
        """
        Stops the calibration method, calls the terminate command on all the children,
        and closes the connection (via the request session) to the ASM.
        """
        self.calibrationMode.value = False
        # terminate children
        for child in self.children.value:
            child.terminate()
        self._session.close()

    def asmApiGetCall(self, url, expected_status, data=None, raw_response=False, timeout=600, **kwargs):
        """
        Call to the ASM API to get data from the ASM API

        :param url (str): url of the command, server part is defined in object variable self._host
        :param expected_status (int): expected feedback of server for a successful call
        :param data (request body): added to the call to the ASM server (mega field metadata, scan location etc.)
        :param raw_response (bool): Specifies the format of the structure returned. For not raw (False) the content
        of the response is translated from json and returned. Otherwise the entire response is returned.
        :param timeout (int): [s] if within this period no bytes are received an timeout exception is raised
        :return: translate content from the response, or entire response (raw_response=True)
        """
        logging.debug("Executing GET: %s" % url)
        resp = self._session.get(self._host + url, json=data, timeout=timeout, **kwargs)

        if resp.status_code != expected_status:
            if isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(value, list) and len(value) > 10:
                        # Convert data elements of type list (which have a length bigger than 10) to string so they can
                        # be returned in a logging message.
                        try:
                            # Limit to first 10 values to not overload error output message
                            data[key] = "First 10 values of the list:" + str(value[0:10])
                        except:
                            data[key] = "Empty - because data cannot be converted to a string"
                logging.error("Data dictionary used to make call %s contains:\n %s" % (url, str(data)))
            raise AsmApiException(url, resp, expected_status)
        if raw_response:
            return resp
        else:
            return json.loads(resp.content)

    def asmApiPostCall(self, url, expected_status, data=None, raw_response=False, timeout=600, **kwargs):
        """
        Call to the ASM API to post data to the ASM API

        :param url (str): url of the command, server part is defined in object variable self._host
        :param expected_status (int): expected feedback of server for a successful call
        :param data: data (request body) added to the call to the ASM server (mega field metadata, scan location etc.)
        :param raw_response (bool): Specifies the format of the structure returned. For not raw (False) the content
        of the response is translated from json and returned. Otherwise the entire response is returned.
        :param timeout (int): [s] if within this period no bytes are received an timeout exception is raised
        :return: status_code(int) or entire response (raw_response=True)
        """
        logging.debug("Executing POST: %s" % url)
        resp = self._session.post(self._host + url, json=data, timeout=timeout, **kwargs)

        if resp.status_code != expected_status:
            if isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(value, list) and len(value) > 10:
                        # Convert data elements of type list (which have a length bigger than 10) to string so they can
                        # be returned in a logging message.
                        try:
                            # Limit to first 10 values to not overload error output message
                            data[key] = "First 10 values of the list:" + str(value[0:10])
                        except:
                            data[key] = "Empty - because data cannot be converted to a string"
                logging.error("Data dictionary used to make call %s contains:\n %s" % (url, str(data)))
            raise AsmApiException(url, resp, expected_status)

        logging.debug("Call to %s was successful.\n" % url)
        if raw_response:
            return resp
        else:
            return resp.status_code

    def system_checks(self):
        """
        Performs default checks on the system, to help inform the user if any problem in the system might be a cause of
        the error. If a negative outcome of these logged and the HW state of the system is changed to an error.
        """
        # TODO Test these checks on EA1 since these do not work on the simulator.
        try:
            # Check SAM connection
            item_name = "sam_connection_operational"
            response = self.asmApiGetCall(item_name, 200)
            if not response:
                self.state._set_value(HwError("Sam connection not operational."), force_write=True)
                logging.error("Sam connection not operational.")

            # Check external storage connection
            item_name = "ext_store_connection_operational"
            response = self.asmApiGetCall(item_name, 200)
            if not response:
                self.state._set_value(HwError("External storage connection not operational."), force_write=True)
                logging.error("External storage connection not operational.\n"
                              "When the connection with the external storage is lost, scanning is "
                              "still possible. There is a large offload queue on which multiple field images may be "
                              "saved temporarily. As long as there is space left in that queue, field scanning can "
                              "continue.")

            # Check offload queue
            item_name = "offload_queue_fill_level"  # defined item_name for logging message in except.
            # TODO: queue filling level can already be problematic at values lower than 99%, test this.
            max_queue_fill = 99
            response = self.asmApiGetCall(item_name, 200)
            if response >= max_queue_fill:
                self.state._set_value(HwError("The offload queue is full, filling rate is: %s percent." % response),
                                      force_write=True)
                logging.error(" Fill rate of the queue in percent: 0 .. 100. When the connection with the external "
                              "storage is lost, images will be stored in the offload queue. When the queue fill level "
                              "is nearly 100 percent, field scanning is not possible anymore.\n"
                              "The filling rate of the que is now at %s percent." % response)

            # Check installation
            item_name = "install_in_progress"
            response = self.asmApiGetCall(item_name, 200)
            if response:
                self.state._set_value(HwError("Installation in progress."), force_write=True)
                logging.error("An installation is in progress.")

            item_name = "last_install_success"
            response = self.asmApiGetCall(item_name, 200)
            if not response:
                self.state._set_value(HwError("Last installation was unsuccessful."), force_write=True)
                logging.error(response)

        except Exception:
            logging.exception("Performing system checks failed. Could not perform a successful call to %s ."
                              % item_name)

    def getSamServiceVersion(self):
        """
        Get the SAM (Scanning and Acquisition Module) service software version via the ASM API.
        :return (str): Version string of the SAM service software.
        """
        item_name = "sam_service_version"
        response = self.asmApiGetCall("/monitor/item?item_name=" + item_name, 200, raw_response=True)
        return response.text

    # TODO Not yet implemented on simulator side, uncomment when implemented.
    # def getSamRootfsVersion(self):
    #     """
    #     Makes monitor call to the ASM to retrieve the SAM root file system version.
    #     :return (str):  Version string of the root file system of the SAM
    #     """
    #     item_name = "sam_rootfs_version"
    #     response = self.asmApiGetCall("/monitor/item?item_name=" + item_name, 200, raw_response=True)
    #     return response.text

    def getSamFirmwareVersion(self):
        """
        Makes monitor call to the ASM to retrieve the SAM firmware version (Scanning and Acquisition Module).
        :return (str):  Version string of the SAM firmware.
        """
        item_name = "sam_firmware_version"
        response = self.asmApiGetCall("/monitor/item?item_name=" + item_name, 200, raw_response=True)
        return response.text

    def getAsmServiceVersion(self):
        """
        Makes monitor call to the ASM to retrieve the ASM service software version.
        :return (str): Version string of the SAM service software.
        """
        item_name = "asm_service_version"
        response = self.asmApiGetCall("/monitor/item?item_name=" + item_name, 200, raw_response=True)
        return response.text

    def getStateInstallation(self):
        """
        Checks the current installation status and returns the status of the last installation or if an installation is
        in progress.
        :return (str): one of the following values:
                                "RUNNING": "installation in progress",
                                "FINISHED": "last installation successful",
                                "FAILED": "last installation failed"
        """
        #  TODO is this for the ASM or the SAM or... change name an docstring accordingly
        install_in_progress = self.asmApiGetCall("/monitor/item?item_name=install_in_progress", 200, raw_response=True)
        last_install_success = self.asmApiGetCall("/monitor/item?item_name=last_install_success", 200)

        if install_in_progress:
            logging.info("An software installation on the ASM is in progress.")
            return RUNNING
        elif last_install_success:
            return FINISHED
        else:
            logging.error("Last software installation on the ASM failed")
            return FAILED

    def isSamConnected(self):
        """
        Check if the SAM connection is operational: true/false. When the
        connection to the SAM is not operational, no scanning is possible.

        :return (bool): True for connected with the ASM, False for when not connected.
        """
        item_name = "sam_connection_operational"
        response = self.asmApiGetCall("/monitor/item?item_name=" + item_name, 200)
        return response

    def isExtStorageConnected(self):
        """
        Check if the external storage is connected via the FTP protocol: true/false. When the connection with the
        external storage is lost, scanning is still possible. There is a large offload queue. Connection loss is only
        detected during scanning. During connection loss the service retries to offload data. As soon as the
        offloading succeeds, the state is set to true again. As long as there is space left on the queue, field
        scanning can continue.

        :return (bool): True for connected with the external storage, False for when not connected.
        """
        item_name = "ext_store_connection_operational"
        response = self.asmApiGetCall("/monitor/item?item_name=" + item_name, 200)
        return response

    def getFillLevelOffloadingQueue(self):
        """
        When the connection with the external storage is lost, images will be stored in the offload queue.
        When the queue fill level is nearly 100%, field scanning is not possible anymore.
        :return (int): Fill rate of the queue in percent: 0 .. 100.
        """
        item_name = "offload_queue_fill_level"
        response = self.asmApiGetCall("/monitor/item?item_name=" + item_name, 200)
        return response

    def _assembleCalibrationMetadata(self):
        """
        Assemble the calibration data and retrieve the input values from the scanner, descanner and mppc VA's.
        :return calibration_data: (CalibrationLoopParameters object) Calibration data object which contains all
                                    the HW settings and scanning profiles for calibration mode.
        """
        descanner = self._mirror_descanner
        scanner = self._ebeam_scanner

        total_line_scan_time = self._mppc.getTotalLineScanTime()

        # get the scanner setpoints
        x_descan_setpoints, y_descan_setpoints = self._mirror_descanner.getCalibrationSetpoints(total_line_scan_time)

        # get the descanner setpoints
        x_scan_setpoints, y_scan_setpoints, scan_calibration_dwell_time_ticks = \
            self._ebeam_scanner.getCalibrationSetpoints(total_line_scan_time)

        calibration_data = CalibrationLoopParameters(descanner.rotation.value,
                                                     0,  # Descan X offset parameter unused.
                                                     x_descan_setpoints,
                                                     0,  # Descan Y offset parameter unused.
                                                     y_descan_setpoints,
                                                     scan_calibration_dwell_time_ticks,
                                                     scanner.rotation.value,
                                                     scanner.getTicksScanDelay()[0],
                                                     0.0,  # Scan X offset parameter unused.
                                                     x_scan_setpoints,
                                                     0.0,  # Scan Y offset parameter unused.
                                                     y_scan_setpoints)

        return calibration_data

    def _setCalibrationMode(self, mode):
        """
        Setter for the calibration mode. This methods starts (True) or stops the calibration mode (False).
        If calibration parameters are changed the calibration mode should be turned off and on again.
        The calibration metadata is saved in self._calibrationParameters.

        :param mode (bool): starting calibration mode (True) or stops the calibration mode (False)
        :return (bool): starting calibration mode (True) or stops the calibration mode (False)
        """
        prev_state_calibration_mode = self.calibrationMode.value

        if mode:
            if not self._mppc.acq_queue.empty():
                logging.error("There is still an unfinished acquisition in progress. Calibration mode cannot be "
                              "started yet.")
                return False

            # If the calibration loop was already running stop it so it can be restarted with new parameters.
            if prev_state_calibration_mode:
                # Sending this command without the calibration loop being active might cause errors.
                self.asmApiPostCall("/scan/stop_calibration_loop", 204)

            # Retrieve and assemble calibration metadata.
            self._calibrationParameters = self._assembleCalibrationMetadata()
            self.asmApiPostCall("/scan/start_calibration_loop", 204, data=self._calibrationParameters.to_dict())
            return True

        else:
            # Only stop the calibration loop if it was running before.
            if prev_state_calibration_mode:
                # Stop calibration loop and clear the calibration parameters attribute
                self._calibrationParameters = None
                # Sending this command without the calibration loop being active might cause errors.
                self.asmApiPostCall("/scan/stop_calibration_loop", 204)

            return False

    def _setURL(self, url):
        """
        Set the external storage URL. Check if the requested url complies with the allowed pattern and characters.
        :param url: (str) The requested external storage url
                          e.g. ftp://username:password@127.0.0.1:5000/directory/sub-directory
        :return: (str) The new external storage url.
        """

        url_parser = urlparse(url)  # Transform input string to url_parse object

        # check that all sub-elements exist. There are special cases where the parser fails splitting correctly.
        # This can happen, when for example an extra '@' is used after the first one. Then the parser works
        # incorrectly and sub-elements are NoneType objects.
        if not url_parser.scheme or not url_parser.username or not url_parser.password \
                or not url_parser.hostname or not url_parser.path:
            raise ValueError("URL %s scheme is incorrect. Must be of format: "
                             "'ftp://username:password@127.0.0.1:5000/directory/sub-directory'." % url)

        # check the scheme is correct
        if url_parser.scheme != 'ftp':
            raise ValueError("URL %s scheme is incorrect. Must be: 'ftp'." % url_parser.scheme)

        if not re.fullmatch(ASM_USER_CHARS, url_parser.username):
            raise ValueError("Username %s contains invalid characters. Only the following characters are allowed: "
                             " '%s'." % (url_parser.username, ASM_USER_CHARS[1:-2]))

        if not re.fullmatch(ASM_PASSWORD_CHARS, url_parser.password):
            raise ValueError("Password %s contains invalid characters. Only the following characters are allowed: "
                             "'%s'." % (url_parser.password, ASM_PASSWORD_CHARS[1:-2]))

        if not re.fullmatch(ASM_HOST_CHARS, url_parser.hostname):
            raise ValueError("Host %s contains invalid characters. Only the following characters are allowed: "
                             "'%s'." % (url_parser.hostname, ASM_HOST_CHARS[1:-2]))

        if not re.fullmatch(ASM_PATH_CHARS, url_parser.path):
            raise ValueError("Path %s contains invalid characters. Only the following characters are allowed: "
                             "'%s'." % (url_parser.path, ASM_PATH_CHARS[1:-2]))

        return url


class EBeamScanner(model.Emitter):
    """
    HW component representing the e-beam scanner.
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Initialize the e-beam scanner.

        :param name(str): Name of the component
        :param role(str): Role of the component
        :param parent (AcquisitionServer object): Parent object of the component
        """
        super(EBeamScanner, self).__init__(name, role, parent=parent, **kwargs)

        clockFrequencyData = self.parent.asmApiGetCall("/scan/clock_frequency", 200)
        # period (=1/frequency) of the ASM clock
        self.clockPeriod = model.FloatVA(1 / clockFrequencyData['frequency'], unit='s', readonly=True)

        # Minimum resolution is determined by:
        #  - The shape of the MPPC detector, 8 cell images per field image
        #  - The minimal cell size (at least 10 pixels width/height)
        #  - The cell size shape needs to be a whole multiple of 4 (as checked in the setter)
        # This means that the minimum resolution needs to fulfill the conditions:
        #          min_res / 8 > 10    and    (min_res/8)/4 is an integer
        # Making the minimum resolution (12*8) , because 12/4 is an integer (3.0)
        # Since the maximum cell size is 1000 (dividable by 4) the maximum resolution is (1000*8)
        mppcDetectorShape = MPPC.SHAPE
        # size of a single field image (excluding overscanned pixels)
        self.resolution = model.ResolutionVA((6400, 6400),
                                             ((12 * mppcDetectorShape[0], 12 * mppcDetectorShape[1]),
                                              (1000 * mppcDetectorShape[0], 1000 * mppcDetectorShape[1])),
                                             setter=self._setResolution)
        self._shape = self.resolution.range[1]
        # TODO: Dwell time is currently set at a maximum of 40 micro seconds because we cannot calibrate as long as
        #  1e-4 seconds. This is because we are limited to 4000 calibration setpoints.
        self.dwellTime = model.FloatContinuous(5e-6, (4e-7, 4e-5), unit='s', setter=self._setDwellTime)
        self.pixelSize = model.TupleContinuous((4e-9, 4e-9), range=((1e-9, 1e-9), (1e-3, 1e-3)), unit='m',
                                               setter=self._setPixelSize)
        # direction of the executed scan
        self.rotation = model.FloatContinuous(0.0, range=(0.0, 2 * math.pi), unit='rad')

        # Scanner settings:
        # In x we typically scan from negative to positive centered around zero and
        # in y from positive to negative centered around zero.
        # the start of the sawtooth scanning signal
        self.scanOffset = model.TupleContinuous((0.0, 0.0), range=((-1.0, -1.0), (1.0, 1.0)), cls=(int, float))
        # heights of the sawtooth scanner signal (it does not include the offset!)
        self.scanAmplitude = model.TupleContinuous((0.1, -0.1), range=((-1.0, -1.0), (1.0, 1.0)), cls=(int, float))
        # FIXME add a check that offset + amplitude >! 2**15 - 1 and offset + amplitude <! -2**15

        # delay between the trigger signal to start the acquisition and the scanner to start scanning
        # x: delay in starting a line scan; y: delay in scanning full lines (prescan lines)
        # The scan delay depends on the acquisition delay (MPPC.acqDelay). Acquisition delay must be decreased first.
        # TODO: y scan delay is y prescan lines in ASM API [not implemented yet]. If needed, convert from seconds to
        #  line scan time via getTicksScanDelay(). For now here range is just 0.
        self.scanDelay = model.TupleContinuous((0.0, 0.0), range=((0.0, 0.0), (1.0, 0.0)), unit='s', cls=(int, float),
                                               setter=self._setScanDelay)

        self._metadata[model.MD_PIXEL_SIZE] = self.pixelSize.value
        self._metadata[model.MD_DWELL_TIME] = self.dwellTime.value

    def getCalibrationSetpoints(self, total_line_scan_time):
        """
        Calculate the setpoints for the scanner during calibration mode. The setpoints resemble a sine shape
        in x and a sawtooth profile in y.

        :param total_line_scan_time: (float) Total line scanning time in seconds including
                                    overscanned pixels and flyback time. TODO do we need the flyback?
        :return:
                x_setpoints (list of floats): The calibration setpoints in x direction in volt.
                y_setpoints (list of floats): The calibration setpoints in y direction in volt.
                calibration_dwell_time_ticks (int): Sampling period in ticks.
        """
        # The calibration frequency is the inverse of the total line scan time.
        calibration_frequency = 1 / total_line_scan_time  # [1/sec]

        # Calculate the total number of setpoints and the calibration dwell time (update frequency of the setpoints).
        calibration_dwell_time_ticks, number_setpoints = self.getCalibrationDwellTime(total_line_scan_time)

        # convert offset and amplitude from [a.u.] to [V]
        scan_offset = convertRange(self.scanOffset.value, numpy.array(self.scanOffset.range)[:, 1], VOLT_RANGE)
        scan_amplitude = convertRange(self.scanAmplitude.value, numpy.array(self.scanAmplitude.range)[:, 1], VOLT_RANGE)

        timestamps_setpoints = numpy.linspace(0, total_line_scan_time, number_setpoints)  # [sec]
        # setpoints in x direction resemble a sine
        x_setpoints = scan_offset[0] + scan_amplitude[0] * \
                      numpy.sin(2 * math.pi * calibration_frequency * timestamps_setpoints)  # [V + V * sec/sec = V]
        # setpoints in y direction resemble a sawtooth profile
        y_setpoints = scan_offset[1] + scan_amplitude[1] * \
                      signal.sawtooth(2 * math.pi * calibration_frequency * timestamps_setpoints)  # [V]

        return x_setpoints.tolist(), y_setpoints.tolist(), calibration_dwell_time_ticks

    def getCalibrationDwellTime(self, total_line_scan_time):
        """
        Calculate the calibration dwell time. The calibration dwell time is the time the scanner stays at one
        pixel position. However, the calibration dwell time is only used when the ASM is in calibration mode and
        the calibration dwell time is calculated based on the acquisition dwell time (setting on the scanner.dwellTime
        VA) that should be used during acquisition mode. During calibration mode the acquisition dwell time
        is replaced with the calibration dwell time on the ASM.

        For the calculation of the calibration setpoints the calibration dwell time (or sampling period) is
        variable but the total number of setpoints is limited to a maximum of 4000 points.
        Depending on the total line scanning time the calibration setpoints can have a variable calibration
        dwell time which needs to be multiple of the system clock period.
        The number of setpoints and the corresponding calibration dwell time are chosen such that the
        maximum number of setpoints is achieved (highest resolution of the signal).

        :param total_line_scan_time: (float) Total line scanning time for the acquisition in seconds including
                                     overscanned pixels and flyback time.
        :return: (int) The calibration dwell time (sampling period) in ticks.
                 (int) Total number of setpoints.
        """
        # TODO MAX_NMBR_POINT value of 4000 setpoints is currently sufficient for the entire range of the dwell time
        #  on the scanner because the maximum dwell time is currently reduced.
        #  However, for the anticipated maximum dwell time of 1e-4 seconds, more than 9000 setpoints are needed
        #  in order to cover a full line scan for the given update frequency (system clock period).
        MAX_NMBR_POINT = 4000  # maximum number of setpoints possible

        descanner_clock_period = self.parent._mirror_descanner.clockPeriod.value  # [sec]
        scanner_clock_period = self.clockPeriod.value  # [sec]

        # Calculate the range (minimum and maximum multiplication factor) in which to search for the best
        # calibration dwell time/sampling period.

        # The min calibration dwell time with a max number of setpoints
        min_calib_dwell_time_ticks = int(numpy.ceil((total_line_scan_time / MAX_NMBR_POINT) / scanner_clock_period))
        # check that the min calibration dwell time is not smaller than the allowed acquisition dwell time
        acq_dwell_time_ticks = int(self.dwellTime.range[0] / scanner_clock_period)
        if min_calib_dwell_time_ticks < acq_dwell_time_ticks:
            min_calib_dwell_time_ticks = acq_dwell_time_ticks

        # The max calibration dwell time in ticks is defined by the descanner clock period
        max_calib_dwell_time_ticks = int(descanner_clock_period / scanner_clock_period)

        # find the best possible calibration dwell time
        # the smaller the dwell time, the higher the total number of setpoints (higher resolution)
        for calib_dwell_time_ticks in range(min_calib_dwell_time_ticks, max_calib_dwell_time_ticks):
            calib_dwell_time = calib_dwell_time_ticks * scanner_clock_period  # [sec]
            number_setpoints = total_line_scan_time / calib_dwell_time  # [sec/sec]

            # Check if the found number of setpoints is an integer number. If yes, use the found sampling period
            # (calibration dwell time) as it has the highest possible resolution for the given total line scan time.
            if numpy.round(number_setpoints, 10) % 1 == 0:  # round for floating point errors
                logging.debug("Found calibration dwell time of %s sec and total number of %s setpoints."
                              % (calib_dwell_time_ticks, number_setpoints))

                return calib_dwell_time_ticks, number_setpoints  # break the loop -> found the best possible dwell time
        else:
            calib_dwell_time_ticks = max_calib_dwell_time_ticks
            # round for floating point errors
            number_setpoints = numpy.round(total_line_scan_time / descanner_clock_period, 10).astype(int)
            logging.debug("Could not optimize the calibration dwell time for the scanner. Use calibration time "
                          "of %s sec and total number of %s setpoints." % (calib_dwell_time_ticks, number_setpoints))

            return calib_dwell_time_ticks, number_setpoints

    def getTicksScanDelay(self):
        """
        Convert the scan delay in seconds into a multiple of the system clock period in ticks.
        Convert the number of pre-scanned lines to integer.
        :return:
                x_delay (int): Scan delay in ticks.
                y_delay (int): Number of line scans before starting the actual acquisition.
        """
        return (int(self.scanDelay.value[0] / self.clockPeriod.value),
                int(self.scanDelay.value[1]))

    def getTicksDwellTime(self):
        """
        :return: Dwell time in multiple of ticks of the system clock period
        """
        return int(self.dwellTime.value / self.clockPeriod.value)

    def getCenterScanVolt(self):
        """
        Calculate the center of the scanning ramp in volt. Convert from arbitrary units to volts.
        The scan offset is defined as the start of the scanning ramp (typically a sawtooth signal),
        while the acquisition server (ASM) handles the offset as the center of the scanning ramp.

        :return (tuple of floats): Center (offset) of the scanning ramp in x and y direction in volt.
        """
        ##################################################################
        # example: scanning ramp resembled by 4 pixels (values: 2 to 8)
        #               --8-- end -> 4th (last) pixel
        #          --6--
        #     --4--
        # --2-- start = offset (scan_offset)
        # end = start + amplitude = 2 + 6 = 8
        # center =  start + end = 2 + 8 = 5
        ##################################################################
        # Convert start of the scanning ramp from [a.u.] to [V].
        scan_start = convertRange(self.scanOffset.value,
                                  numpy.array(self.scanOffset.range)[:, 1],
                                  VOLT_RANGE)  # [V]
        # Convert end of the scanning ramp (offset + amplitude) from [a.u.] to [V].
        scan_end = convertRange((self.scanOffset.value[0] + self.scanAmplitude.value[0],
                                 self.scanOffset.value[1] + self.scanAmplitude.value[1]),
                                numpy.array(self.scanAmplitude.range)[:, 1],
                                VOLT_RANGE)  # [V]

        # Calculate center of the scanning ramp in x and y.
        center_scan = ((scan_start[0] + scan_end[0]) / 2,
                       (scan_start[1] + scan_end[1]) / 2)  # [V]

        return center_scan

    def getGradientScanVolt(self):
        """
        Calculate the gradient of the scanning ramp. Convert from arbitrary units to volts.
        The scan amplitude is defined as the heights of the scanning ramp (typically a sawtooth signal),
        while the acquisition server (ASM) handles the gain as the gradient of the scanning ramp in [V/px].

        :return (tuple of floats): Gradient (gain or step size) of the scanning ramp in x and y direction in volt.
        """
        #########################################################################################
        # From the first to the last pixel: number of steps = number of pixels (resolution) - 1.
        # -> stepsize in volt/pixel = (amplitude of the scanning ramp)/(number of steps)
        #
        # example: scanning ramp resembled by 4 pixels (values: 2 to 8)
        #               --8-- end -> 4th (last) pixel
        #          --6--
        #     --4--
        # --2-- start = offset (scan_offset)
        # heights/amplitude = end - start = 8 -2 = 6
        # number of pixels (ebeam positions) = 4
        # number of steps = number of pixels - 1 = 4 - 1 = 3
        # step size = 6 / 3 = 2 = amplitude/(resolution-1)
        #########################################################################################
        # Convert the amplitude from [a.u.] to [V].
        scan_amplitude = convertRange(self.scanAmplitude.value,
                                      numpy.array(self.scanAmplitude.range)[:, 1],
                                      VOLT_RANGE)  # [V]

        # number of pixel positions
        resolution = numpy.array(self.parent._mppc.cellCompleteResolution.value)
        # calculate the gradient (gain); number of steps from start to end of scanning ramp is resolution -1
        gradient = tuple(scan_amplitude / (resolution - 1))

        return gradient

    def _setDwellTime(self, dwell_time):
        """
        Sets the dwell time per pixel (ebeam position) in seconds. Updates the metadata on the component accordingly.

        :param dwell_time (float): The requested dwell time in seconds.
        :return (float): The set dwell time in seconds.
        """

        self._metadata[model.MD_DWELL_TIME] = dwell_time

        return dwell_time

    def _setPixelSize(self, pixel_size):
        """
        Sets the pixel size in x and y in meter. Enforces square pixels based on the x value.
        Updates the metadata on the component accordingly.

        :param pixel_size (float, float): The requested pixel size in x and y in meter.
        :return (float, float): The set pixel size in x and y in meter.
        """
        if pixel_size[0] != pixel_size[1]:
            logging.warning("Non-square pixel size of %s - converting to square value of %s.",
                            pixel_size, (pixel_size[0], pixel_size[0]))
            pixel_size = (pixel_size[0], pixel_size[0])

        self._metadata[model.MD_PIXEL_SIZE] = pixel_size

        return pixel_size

    def _setScanDelay(self, scanDelay):
        """
        Sets the delay for the scanner to start scanning after a mega field acquisition was started/triggered. It is
        checked that the scanner starts scanning before the mppc detector starts recording.

        x: The delay between the trigger signal to start the acquisition and the scanner to start scanning in seconds.
        y: The number of full line scans executed (prescan lines) before the acquisition is started. The number of
        times the first line of pixels is descanned before actual acquisition is started. This can be used to bring
        the descan mirror up to speed before scanning the first line of pixels. [NOT IMPLEMENTED] Always set to zero.

        :param scanDelay (float, int): The requested scan delay in x direction in seconds.
                         The requested number of full line scans executed before starting the actual acquisition in y.
        :return (float, int): The set scan delay in x in seconds. The set number of pre lines scans in y.
        """
        # scanning with the ebeam needs to start before acquiring images with mppc detector
        if self.parent._mppc.acqDelay.value < scanDelay[0]:
            raise ValueError("Requested scan delay is %s sec. Scan delay cannot be greater than current acquisition "
                             "delay of %s sec." % (scanDelay[0], self.parent._mppc.acqDelay.value))

        return scanDelay

    def _setResolution(self, resolution):
        """
        Sets the resolution of a single field image. The resolution of a single field image is the product of the
        number of cell images (detector shape) multiplied with the size of a cell image (effective cell size). Method
        sets the resolution of a single field image, so that the effective cell size is a multiple of 4 (in both x
        and y direction).

        :param resolution (int, int): requested resolution for a single field image. It is the product of the number of
        cell images multiplied by the effective cell size.
        :return (int, int): resolution closest possible to the requested resolution for the single field image.
        """
        COMMON_DIVISOR = 4

        req_eff_cell_size = numpy.array(resolution) / self.parent._mppc.shape[0:2]  # Requested effective cell size
        # Round to closest multiple of COMMON_DIVISOR (x.5 is rounded down)
        eff_cell_size = numpy.round(req_eff_cell_size / COMMON_DIVISOR) * COMMON_DIVISOR
        resolution = eff_cell_size * self.parent._mppc.shape[0:2]

        return tuple(int(i) for i in resolution)


class MirrorDescanner(model.Emitter):
    """
    Represents the Mirror descanner which counter scans the scanner movement to ensure that
    the collected signal always hits the center of each mppc cell on the detector.
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Initialize the mirror descanner.

        :param name(str): Name of the component
        :param role(str): Role of the component
        :param parent (AcquisitionServer object): Parent object of the component
        """
        super(MirrorDescanner, self).__init__(name, role, parent=parent, **kwargs)

        # direction of the executed descan
        self.rotation = model.FloatContinuous(0, range=(0, 2 * math.pi), unit='rad')
        # start of the sawtooth descanner signal
        self.scanOffset = model.TupleContinuous((0.0, 0.0), range=((-1, -1), (1, 1)), cls=(int, float))
        # heights of the sawtooth descanner signal (it does not include the offset!)
        self.scanAmplitude = model.TupleContinuous((0.008, 0.008), range=((-1, -1), (1, 1)), cls=(int, float))
        # FIXME add a check that offset + amplitude >! 2**15 - 1 and offset + amplitude <! -2**15

        clockFrequencyData = self.parent.asmApiGetCall("/scan/descan_control_frequency", 200)  # [1/sec]
        # period (=1/frequency) of the descanner in seconds; update frequency for setpoints upload
        self.clockPeriod = model.FloatVA(1 / clockFrequencyData['frequency'], unit='s', readonly=True)

        # physical time for the mirror descanner to perform a flyback (moving back to start of a line scan)
        self.physicalFlybackTime = model.FloatContinuous(150e-6, range=(0, 1e-3), unit='s')

    def getXAcqSetpoints(self):
        """
        Creates the setpoints for the descanner in x direction for de-scanning one row of pixels. The
        x setpoints describe the movement of the descanner during the scanning of one full row of pixels.
        The setpoints resemble a sawtooth profile followed by a flyback period.

        :return (list of ints): The setpoints (descanner positions) resembling the scanning ramp in x direction in bits.
        """
        # The setpoints for an acquisition resemble a linear ramp for scanning a line of pixels, followed by the
        # flyback, where the descanner moves back to its starting position.
        # scanning setpoints: x_setpoints = A*t + B
        # 'A': gradient of the scanning line -> (maximum - minimum) / scanning time
        # 'B': offset

        descan_period = self.clockPeriod.value  # [sec]
        dwellTime = self.parent._ebeam_scanner.dwellTime.value  # [sec]
        x_cell_resolution = self.parent._mppc.cellCompleteResolution.value[0]  # [px]

        # Note: .scanOffset and .scanAmplitude are in a.u. with range [-1, 1].
        # Setpoints (here denoted in [bits]) calculated based on those VAs are also in a.u. but mapped to range
        # [-2**15, 2**15] and finally clipped to [-2**15, 2**15 - 1] before send to the ASM.

        # Convert start of the scanning ramp from [a.u.] to [bits].
        scan_start = convertRange(self.scanOffset.value[0], numpy.array(self.scanOffset.range)[:, 1],
                                  I16_SYM_RANGE)  # [bits]
        # Convert amplitude of the scanning ramp from [a.u.] to [bits].
        scan_amplitude = convertRange(self.scanAmplitude.value[0],
                                      numpy.array(self.scanAmplitude.range)[:, 1],
                                      I16_SYM_RANGE)  # [bits]
        # Convert the end of the scanning ramp from [a.u.] to [bits].
        scan_end = convertRange(self.scanOffset.value[0] + self.scanAmplitude.value[0],
                                numpy.array(self.scanAmplitude.range)[:, 1], I16_SYM_RANGE)  # [bits]

        # line scan time including overscanned pixels but without flyback
        scanning_time = dwellTime * x_cell_resolution  # [sec]

        # Remainder of the line scan time which is not a whole multiple of the descan period.
        remainder_scanning_time = scanning_time % descan_period  # [sec]

        # Calculate the time stamps for the setpoints excluding the remainder of the scanning time.
        # The number of setpoint does not necessarily match the number of pixels scanned.
        # Every descanner period a new setpoint (mirror position) is set.
        number_setpoints = int(scanning_time // descan_period)  # [sec/sec = no unit]
        timestamp_setpoints = numpy.linspace(0, scanning_time - remainder_scanning_time, number_setpoints)  # [sec]

        # Gradient of the scanning ramp (independent of the offset).
        scanning_gradient = scan_amplitude / scanning_time  # [bits/sec]

        # Calculate positions of the descanner in the scanning ramp: x_setpoints = gradient * timestamps + offset
        scanning_points = scanning_gradient * timestamp_setpoints + scan_start  # [bits/sec * sec + bits = bits]

        # Check if the remaining scanning time is 0. If not, add one extra setpoint.
        # Use almost_equal to handle floating point errors.
        if not almost_equal(remainder_scanning_time, 0, rtol=0, atol=1e-10):
            # add one setpoint with value same as end of scanning ramp
            scanning_points = numpy.hstack((scanning_points, scan_end))  # [bits]

        # Calculation of the flyback points:
        # Check if the physical flyback time (time the mirror needs to move back to the start position
        # of the line scan) is a whole multiple of the descan period. If not, round up.
        number_flyback_points = math.ceil(self.physicalFlybackTime.value / descan_period)  # [sec/sec = no unit]
        # convert flyback setpoints into bits; should be at the same level as the start of the ramp (=offset)
        flyback_points = scan_start + numpy.zeros(number_flyback_points)  # [bits]

        setpoints = numpy.concatenate((scanning_points, flyback_points))  # [bits]

        # Setpoints need to be integers when send to the ASM. First round down found setpoints to next integer
        # then convert to int type.
        # For consistency in the rounding use numpy.floor() to always round down (also for negative numbers):
        # e.g. int(-3.4) = -3 vs. numpy.floor(-3.4) = -4.
        setpoints = numpy.floor(setpoints).astype(int)  # [bits]

        # Mapping from a.u. to bits is symmetrically around 0, whereas the range in bits that is accepted by the ASM is
        # not symetrically around 0 ([-32768, 32767]). Clip 2**15 = 32768 by one bit to 32767 bit.
        setpoints = numpy.minimum(setpoints, I16_SYM_RANGE[1] - 1)

        return setpoints.tolist()

    def getYAcqSetpoints(self):
        """
        Creates the setpoints for the descanner in y direction. The setpoints resemble a sawtooth profile
        During the scanning of a row of pixels (x direction) the value of the corresponding y setpoint is constant.
        Only one y descan setpoint per full row of pixels will be calculated and send to the ASM.
        After completing the scan of a full row of pixels the next y setpoint is set.

        :return (list of ints): The setpoints (descanner positions) in y direction in bits (one setpoint per row).
        """
        # Note: .scanOffset and .scanAmplitude are in a.u. with range [-1, 1].
        # Setpoints (here denoted in [bits]) calculated based on those VAs are also in a.u. but mapped to range
        # [-2**15, 2**15] and finally clipped to [-2**15, 2**15 - 1] before send to the ASM.

        # Convert start of the scanning ramp from [a.u.] to [bits].
        scan_start = convertRange(self.scanOffset.value[1], numpy.array(self.scanOffset.range)[:, 1],
                                  I16_SYM_RANGE)  # [bits]
        # Convert end of the scanning ramp (offset + amplitude) from [a.u.] to [bits].
        scan_end = convertRange(self.scanOffset.value[1] + self.scanAmplitude.value[1],
                                numpy.array(self.scanAmplitude.range)[:, 1], I16_SYM_RANGE)  # [bits]

        y_cell_size = self.parent._mppc.cellCompleteResolution.value[1]  # including overscanned pixels [px]
        # calculate the setpoints: one setpoint per row
        setpoints = numpy.linspace(scan_start, scan_end, y_cell_size)  # [bits]

        # Setpoints need to be integers when send to the ASM. First round down found setpoints to next integer
        # then convert to int type.
        # For consistency in the rounding use numpy.floor() to always round down (also for negative numbers):
        # e.g. int(-3.4) = -3 vs. numpy.floor(-3.4) = -4.
        setpoints = numpy.floor(setpoints).astype(int)  # [bits]

        # Mapping from a.u. to bits is symmetrically around 0, whereas the range in bits that is accepted by the ASM is
        # not symetrically around 0 ([-32768, 32767]). Clip 2**15 = 32768 by one bit to 32767 bit.
        setpoints = numpy.minimum(setpoints, I16_SYM_RANGE[1] - 1)

        return setpoints.tolist()

    def getCalibrationSetpoints(self, total_line_scan_time):
        """
        Calculate the setpoints for the descanner during calibration mode. The setpoints resemble a sine shape
        in x and a flat line at zero in y.

        :param total_line_scan_time: (float) Total line scanning time in seconds including
                                     overscanned pixels and flyback time. TODO do we need the flyback?
        :return:
                x_setpoints (list of ints): The calibration setpoints in x direction in bits.
                y_setpoints (list of ints): The calibration setpoints in y direction in bits.
        """
        # The calibration frequency is the inverse of the total line scan time.
        calibration_frequency = 1 / total_line_scan_time  # [1/sec]

        # Sampling period is equal to the descanner clock period. The number of setpoints is the total line scanning
        # time divided by the descanner clock period.
        number_setpoints = numpy.round(total_line_scan_time / self.clockPeriod.value, 10).astype(int)  # [no unit]

        # Note: .scanOffset and .scanAmplitude are in a.u. with range [-1, 1].
        # Setpoints (here denoted in [bits]) calculated based on those VAs are also in a.u. but mapped to range
        # [-2**15, 2**15] and finally clipped to [-2**15, 2**15 - 1] before send to the ASM.

        # convert offset and amplitude from [a.u.] to [bits]
        sine_offset = convertRange(self.scanOffset.value,
                                   numpy.array(self.scanOffset.range)[:, 1], I16_SYM_RANGE)  # [bits]
        sine_amplitude = convertRange(self.scanAmplitude.value,
                                      numpy.array(self.scanAmplitude.range)[:, 1], I16_SYM_RANGE)  # [bits]

        timestamps_setpoints = numpy.linspace(0, total_line_scan_time, number_setpoints)  # [sec]
        # setpoints in x direction resemble a sine
        # Note: There is not necessarily a setpoint at the max/min amplitude of the sine.
        #
        # *               *  *
        #   *           *      *
        # -----------------------------------
        #     *      *           *
        #       *  *
        #
        x_setpoints = sine_offset[0] + sine_amplitude[0] * \
                      numpy.sin(2 * math.pi * calibration_frequency * timestamps_setpoints)  # [bits+bits*sec/sec=bits]

        # setpoints in y direction are constant (=0)
        y_setpoints = 0 * timestamps_setpoints  # [bits]

        # Setpoints need to be integers when send to the ASM. First round down found setpoints to next integer
        # then convert to int type.
        # For consistency in the rounding use numpy.floor() to always round down (also for negative numbers):
        # e.g. int(-3.4) = -3 vs. numpy.floor(-3.4) = -4.
        x_setpoints = numpy.floor(x_setpoints).astype(int)  # [bits]
        y_setpoints = numpy.floor(y_setpoints).astype(int)  # [bits]

        # mapping from a.u. to bits is symmetrically around 0, whereas the range in bits that is accepted by the ASM is
        # not symetrically around 0 ([-32768, 32767]). Clip 2**15 = 32768 by one bit to 32767 bit.
        x_setpoints = numpy.minimum(x_setpoints, I16_SYM_RANGE[1] - 1)
        y_setpoints = numpy.minimum(y_setpoints, I16_SYM_RANGE[1] - 1)

        return x_setpoints.tolist(), y_setpoints.tolist()


class MPPC(model.Detector):
    """
    Represents the camera (mppc sensor) for acquiring the image data.
    """
    SHAPE = (8, 8, 65536)

    def __init__(self, name, role, parent, **kwargs):
        """
        Initializes the camera (mppc sensor) for acquiring the image data.

        :param name(str): Name of the component
        :param role(str): Role of the component
        :param parent (AcquisitionServer object): Parent object of the component
        """
        super(MPPC, self).__init__(name, role, parent=parent, **kwargs)

        # Store siblings on which this class is dependent as attributes
        self._scanner = self.parent._ebeam_scanner
        self._descanner = self.parent._mirror_descanner

        self._shape = MPPC.SHAPE
        # subdirectory + filename (megafield id) - adjustable part of the path on the external storage
        self.filename = model.StringVA("storage/images/project/megafield_id", setter=self._setFilename)
        self.dataContent = model.StringEnumerated('empty', DATA_CONTENT_TO_ASM.keys())
        # delay between the trigger signal to start the acquisition, and the start of the recording by the mppc detector
        # The acquisition delay depends on the scan delay (EbeamScanner.scanDelay). Scan delay must be increased first.
        self.acqDelay = model.FloatContinuous(0.0, range=(0.0, 1.0), unit='s', setter=self._setAcqDelay)
        # regulates the sensitivity of the mppc sensor
        self.overVoltage = model.FloatContinuous(2.2, range=(0, 5), unit='V')

        # Cell acquisition parameters
        self.cellTranslation = model.TupleVA(
            tuple(tuple((50, 50) for i in range(0, self.shape[0])) for i in range(0, self.shape[1])),
            setter=self._setCellTranslation
        )
        self.cellDarkOffset = model.TupleVA(
            tuple(tuple(0 for i in range(0, self.shape[0])) for i in range(0, self.shape[1]))
            , setter=self._setCellDarkOffset
        )
        self.cellDigitalGain = model.TupleVA(
            tuple(tuple(1.2 for i in range(0, self.shape[0])) for i in range(0, self.shape[1])),
            setter=self._setCellDigitalGain
        )

        # The minimum of the cell resolution cannot be lower than the minimum effective cell size.
        self.cellCompleteResolution = model.ResolutionVA((900, 900), ((12, 12), (1000, 1000)))

        # acquisition time for a single field image including overscanned pixels and flyback time
        self.frameDuration = model.FloatContinuous(0, range=(0, 100), unit='s', readonly=True)
        # listen to changes to settings that affect the frame duration
        self.cellCompleteResolution.subscribe(self._updateFrameDuration, init=True)
        self.parent._ebeam_scanner.dwellTime.subscribe(self._updateFrameDuration)

        # Setup hw and sw version
        self._swVersion = self.parent.swVersion
        self._hwVersion = self.parent.hwVersion

        self._metadata[model.MD_HW_NAME] = "MPPC"
        self._metadata[model.MD_SW_VERSION] = self._swVersion
        self._metadata[model.MD_HW_VERSION] = self._hwVersion
        self._metadata[model.MD_POS] = (0, 0)  # m

        # Initialize acquisition processes
        # Acquisition queue with commands of actions that need to be executed. The queue should hold "(str,
        # *)" containing "(command, data corresponding to the call)".
        self.acq_queue = queue.Queue()

        self._acq_thread = None

        self.data = ASMDataFlow(self)

    def terminate(self):
        """
        Terminate acquisition thread and empty the acquisition queue
        """
        super(MPPC, self).terminate()

        # Clear the queue
        while True:
            try:
                self.acq_queue.get(block=False)
            except queue.Empty:
                break

        if self._acq_thread:
            self.acq_queue.put(("terminate",))
            self._acq_thread.join(5)

    def _assembleMegafieldMetadata(self):
        """
        Gather all the mega field metadata from the VA's and convert to correct format accepted by the ASM API.
        :return: MegaFieldMetaData Model of the ASM API
        """
        stage_position = self._metadata[model.MD_POS]
        cellTranslation = sum(self.cellTranslation.value, ())
        cellDarkOffset = sum(self.cellDarkOffset.value, ())
        cellDigitalGain = sum(self.cellDigitalGain.value, ())
        eff_cell_size = (int(self._scanner.resolution.value[0] / self._shape[0]),
                         int(self._scanner.resolution.value[1] / self._shape[1]))

        # Calculate and convert from seconds to ticks
        scan_to_acq_delay = int(
            (self.acqDelay.value - self._scanner.scanDelay.value[0]) / self.parent._ebeam_scanner.clockPeriod.value
        )

        X_descan_setpoints = self._descanner.getXAcqSetpoints()
        Y_descan_setpoints = self._descanner.getYAcqSetpoints()

        md = self.getMetadata()
        custom_data = md.get(model.MD_EXTRA_SETTINGS, "")  # if no custom metadata, pass an empty string
        # TODO support USER_NOTE in GUI
        info = md.get(model.MD_USER_NOTE, None)  # if no user note pass None, so it is not added to the metadata.yaml
        z_position = md.get(model.MD_SLICE_IDX, 0)  # if slice number is not provided use 0. TODO support in GUI
        eff_field_size = md.get(model.MD_FIELD_SIZE, self._scanner.resolution.value)

        megafield_metadata = MegaFieldMetaData(
            stack_id=os.path.basename(self.filename.value),
            info=info,
            storage_directory=os.path.dirname(self.filename.value),
            custom_data=custom_data,
            stage_position_x=float(stage_position[0]),
            stage_position_y=float(stage_position[1]),
            z_position=z_position,
            # Convert pixels size from meters to nanometers
            pixel_size=int(self._scanner.pixelSize.value[0] * 1e9),
            dwell_time=self._scanner.getTicksDwellTime(),
            x_scan_to_acq_delay=scan_to_acq_delay,
            x_scan_delay=self._scanner.getTicksScanDelay()[0],
            x_cell_size=self.cellCompleteResolution.value[0],
            y_cell_size=self.cellCompleteResolution.value[1],
            x_eff_cell_size=eff_cell_size[0],
            y_eff_cell_size=eff_cell_size[1],
            x_eff_field_size=eff_field_size[0],
            y_eff_field_size=eff_field_size[1],
            x_scan_gain=self._scanner.getGradientScanVolt()[0],
            y_scan_gain=self._scanner.getGradientScanVolt()[1],
            x_scan_offset=self._scanner.getCenterScanVolt()[0],
            y_scan_offset=self._scanner.getCenterScanVolt()[1],
            x_descan_setpoints=X_descan_setpoints,
            y_descan_setpoints=Y_descan_setpoints,
            # Descan offset is set to zero and is currently unused. The offset is implemented via the setpoints.
            x_descan_offset=0,
            y_descan_offset=0,
            scan_rotation=self._scanner.rotation.value,
            descan_rotation=self._descanner.rotation.value,
            # Reshape cell parameters such that the order of the cells is from left to right, then top to
            # bottom. So the cells in the upper line are numbered 0..7, the bottom line 56..63.
            cell_parameters=[CellParameters(translation[0], translation[1], darkOffset, digitalGain)
                             for translation, darkOffset, digitalGain in
                             zip(cellTranslation, cellDarkOffset, cellDigitalGain)],
            sensor_over_voltage=self.overVoltage.value
        )
        return megafield_metadata

    def _acquire(self):
        """
        Acquisition thread takes input from the acquisition queue (self.acq_queue) which contains a command (for
        starting/stopping acquisition or acquiring a field image; 'start', 'stop','terminate', 'next') and extra
        arguments (MegaFieldMetaData Model or FieldMetaData Model and the notifier function to
        which any return will be redirected)
        """
        try:
            # Prevents acquisitions thread from from starting/performing two acquisitions, or stopping the acquisition
            # twice.
            acquisition_in_progress = None

            while True:
                # Wait until a message is available
                command, *args = self.acq_queue.get(block=True)
                logging.debug("Loaded the command '%s' in the acquisition thread from the acquisition queue." % command)

                if command == "start":
                    megafield_metadata = args[0]
                    notifier_func = args[1]  # Return function: queue.put(), content of queue is then read by caller

                    if acquisition_in_progress:
                        logging.warning("ASM acquisition already had the '%s', received this command again." % command)
                        # Return None so that the caller receives something and does not timeout. Thus, the caller is
                        # still able to request a next field image and the current acquisition can keep going.
                        notifier_func(None)
                        continue

                    try:
                        self.parent.asmApiPostCall("/scan/start_mega_field", 204, megafield_metadata.to_dict())
                        notifier_func(None)  # return None on the queue if all went fine
                    except Exception as ex:
                        logging.error("During the start of the acquisition an error has occurred: %s." % ex)
                        notifier_func(ex)  # return exception on the queue
                        continue  # let the caller decide on what to do next

                    acquisition_in_progress = True  # acquisition was successfully started

                elif command == "next":
                    self._metadata = self._mergeMetadata()
                    field_data = args[0]  # Field metadata for the specific position of the field to scan
                    dataContent = args[1]  # Specifies the type of image to return (empty, thumbnail or full)
                    # Return function (dataflow.notify() for megafields or queue.put() for single field acquisition)
                    notifier_func = args[2]

                    if not acquisition_in_progress:
                        logging.warning("Start the acquisition first before requesting to acquire field images.")
                        notifier_func(ValueError("Start acquisition first before requesting to acquire field images."))
                        continue

                    try:
                        # FIXME: Hack: The current ASM HW does not scan the very first field image correctly. This issue
                        #  needs to be fixed in HW. However, until this is done, we need to "throw away" the first field
                        #  image and scan it a second time to receive a good first field image. To do so, just always
                        #  scan the first image twice. Note: scan_field is a blocking call - it waits until the scan is
                        #  finished.
                        if field_data.position_x == 0 and field_data.position_y == 0:
                            logging.debug("Rescanning first field to workaround hardware limitations.")
                            self.parent.asmApiPostCall("/scan/scan_field", 204, field_data.to_dict())

                        self.parent.asmApiPostCall("/scan/scan_field", 204, field_data.to_dict())

                        if DATA_CONTENT_TO_ASM[dataContent] is None:
                            da = model.DataArray(numpy.array([[0]], dtype=numpy.uint8), metadata=self._metadata)
                        else:
                            # TODO remove time.sleep if the function "waitOnFieldImage" exists. Otherwise the image is
                            #  not yet loaded on the ASM when trying to retrieve it.
                            time.sleep(0.5)
                            resp = self.parent.asmApiGetCall(
                                "/scan/field?x=%d&y=%d&thumbnail=%s" %
                                (field_data.position_x, field_data.position_y,
                                 str(DATA_CONTENT_TO_ASM[dataContent]).lower()),
                                200, raw_response=True, stream=True)
                            resp.raw.decode_content = True  # handle spurious Content-Encoding
                            img = Image.open(BytesIO(resp.raw.data))  # the data is expected to be a TIFF

                            da = model.DataArray(img, metadata=self._metadata)
                    except Exception as ex:
                        logging.error("During the acquisition of field %s an error has occurred: %s.",
                                      (field_data.position_x, field_data.position_y), ex)
                        notifier_func(ex)
                        continue  # let the caller decide on what to do next

                    # Send DA to the function to be notified
                    notifier_func(da)

                elif command == "stop":
                    if not acquisition_in_progress:
                        logging.warning("ASM acquisition was already at status '%s'" % command)
                        continue

                    acquisition_in_progress = False
                    self.parent.asmApiPostCall("/scan/finish_mega_field", 204)

                elif command == "terminate":
                    acquisition_in_progress = None
                    raise TerminationRequested()

                else:
                    logging.error("Received invalid command '%s' is skipped" % command)
                    raise ValueError

        except TerminationRequested:
            logging.info("Terminating acquisition")

        except Exception:
            if command is not None:
                logging.exception("Last message was not executed, should have performed action: '%s'\n"
                                  "Reinitialize and restart the acquisition" % command)
        finally:
            self.parent.asmApiPostCall("/scan/finish_mega_field", 204)
            logging.debug("Acquisition thread ended")

    def _ensure_acquisition_thread(self):
        """
        Make sure that the acquisition thread is running. If not, it (re)starts it.
        """
        if self._acq_thread and self._acq_thread.is_alive():
            return

        logging.info('Starting acquisition thread and clearing remainder of the old queue')

        # Clear the queue
        while True:
            try:
                self.acq_queue.get(block=False)
            except queue.Empty:
                break

        self._acq_thread = threading.Thread(target=self._acquire,
                                            name="acquisition thread")
        self._acq_thread.deamon = True
        self._acq_thread.start()

    def startAcquisition(self):
        """
        Put the command 'start' mega field scan on the queue with the appropriate MegaFieldMetaData model of the mega
        field image to be scanned. The MegaFieldMetaData is used to setup the HW accordingly. For each field image
        additional field image related metadata is provided.
        :raise: (Exception) Raise exception if start of megafield acquisition failed.
                (TimeoutError) Raise if return queue did not receive either an Exception or None within time.
        """
        self._ensure_acquisition_thread()
        return_queue = queue.Queue()  # queue which allows to return error messages or None (if all was fine)
        megafield_metadata = self._assembleMegafieldMetadata()
        self.acq_queue.put(("start", megafield_metadata, return_queue.put))
        try:
            status_start = return_queue.get(timeout=60)
        except queue.Empty:
            logging.error("Start of the megafield acquisition timed out.")
            # Something went wrong during the start of the acquisition, so terminate the acquisition thread, so it is
            # properly restarted at a new acquisition attempt.
            self.acq_queue.put(("terminate",))
            raise TimeoutError("Start of the megafield acquisition timed out after 60s.")
        if isinstance(status_start, Exception):
            logging.debug("Received an exception from the acquisition thread.")
            raise status_start

    def getNextField(self, field_num):
        """
        Puts the command 'next' field image scan on the queue with the appropriate field meta data model of the field
        image to be scanned. Can only be executed if it preceded by a 'start' mega field scan command on the queue.
        The acquisition thread returns the acquired image to the provided notifier function added in the acquisition queue
        with the "next" command. As notifier function the dataflow.notify is send. The returned image will be
        returned to the dataflow.notify which will provide the new data to all the subscribers of the dataflow.

        :param field_num: (int, int) x,y coordinates of the field number.
        :raise: (ValueError) Raise if field coordinates are not of correct type, length and positive.
        """
        # Note that this means we don't support numpy ints for now.
        # That's actually correct, as they'd fail in the JSON encoding (which
        # could be worked around too, of course, for instance with the JsonExtraEncoder).
        if len(field_num) != 2 or not all(v >= 0 and isinstance(v, int) for v in field_num):
            raise ValueError("field_num must be 2 ints >= 0, but got %s" % (field_num,))

        field_data = FieldMetaData(*self.convertFieldNum2Pixels(field_num))
        self.acq_queue.put(("next", field_data, self.dataContent.value, self.data.notify))

    def stopAcquisition(self):
        """
        Puts the command 'stop' field image scan on the queue. After this call, no fields can be scanned anymore.
        A new mega field can be started. The call triggers the post processing process to generate and offload
        additional zoom levels.
        """
        self.acq_queue.put(("stop",))

    def cancelAcquistion(self, execution_wait=0.2):
        """
        Clears the entire queue and finishes the current acquisition. Does not terminate the acquisition thread.
        """
        time.sleep(0.3)  # Wait to make sure nothing is still being put on the queue
        # Clear the queue
        while True:
            try:
                self.acq_queue.get(block=False)
            except queue.Empty:
                break

        self.acq_queue.put(("stop",))

        if execution_wait > 30:
            logging.error("Failed to cancel the acquisition. MPPC detector is terminated.")
            self.terminate()
            raise ConnectionError("Connection quality was too poor to cancel the acquisition. MPPC is terminated.")

        time.sleep(execution_wait)  # Wait until command executed is finished

        if not self.acq_queue.empty():
            self.cancelAcquistion(execution_wait=execution_wait * 2)  # Increase the waiting time

    def acquireSingleField(self, dataContent="thumbnail", field_num=(0, 0)):
        """
        Scans a single field image via the acquire thread and the acquisition queue with the appropriate metadata
        models. The function returns the image by providing a return_queue to the acquisition thread. Making use of
        the timeout functionality of the queue prevents waiting too long for an image (timeout=600 seconds).

        :param dataContent: (str) Can be either: "empty", "thumbnail", "full".
        :param field_num: (int, int) x,y location of the field.
        :return: (DataArray) The single field image.
        :raise: (TimeoutError) Raise if return queue did not receive either an Exception or None within time.
                (Exception) Exception raised during the single field acquisition.
                (ValueError) Raised if not an image of type DataArray was received but something else.
        """
        logging.debug("Acquire single field.")
        if dataContent not in DATA_CONTENT_TO_ASM:
            raise ValueError("Unknown data content: %s" % (dataContent,))

        return_queue = queue.Queue()  # queue which allows to return images and be blocked when waiting on images
        mega_field_data = self._assembleMegafieldMetadata()

        self._ensure_acquisition_thread()

        # request to start the acquisition
        self.acq_queue.put(("start", mega_field_data, return_queue.put))
        try:
            status_start = return_queue.get(timeout=60)
        except queue.Empty:
            logging.error("Start of the single field acquisition timed out.")
            # Something went wrong during the start of the acquisition, so terminate the acquisition thread, so it is
            # properly restarted at a new acquisition attempt.
            self.acq_queue.put(("terminate",))
            raise TimeoutError("Start of the single field image acquisition timed out after 60s.")
        if isinstance(status_start, Exception):
            logging.debug("Received an exception from the acquisition thread during the start of the single field "
                          "image acquisition.")
            raise status_start

        field_data = FieldMetaData(*self.convertFieldNum2Pixels(field_num))

        # request to scan a single field image
        self.acq_queue.put(("next", field_data, dataContent, return_queue.put))
        # request to stop the acquisition
        self.acq_queue.put(("stop",))  # make sure it always stops even in case of errors

        try:
            status_next = return_queue.get(timeout=600)
        except queue.Empty:
            logging.error("Acquisition of the single field image timed out.")
            self.acq_queue.put(("terminate",))  # terminate the acquisition
            raise TimeoutError("Acquisition of the single field image timed out after 600s.")

        if isinstance(status_next, Exception):
            logging.debug("Received an exception from the acquisition thread during the acquisition of the single "
                          "field image.")
            raise status_next
        elif not isinstance(status_next, model.DataArray):
            raise ValueError("Did not receive image data but the following: %s." % status_next)  # should never happen

        return status_next  # return the image data

    def convertFieldNum2Pixels(self, field_num):
        """
        :param field_num(tuple): tuple with x,y coordinates in integers of the field number.
        :return: field number (tuple of ints)
        """
        return (field_num[0] * self._scanner.resolution.value[0],
                field_num[1] * self._scanner.resolution.value[1])

    def getTicksAcqDelay(self):
        """
        Converts the acquisition delay in seconds into a multiple of the system clock period in ticks.
        :return (int): Acquisition delay in ticks.
        """
        return int(self.acqDelay.value / self.parent._ebeam_scanner.clockPeriod.value)

    def _mergeMetadata(self):
        """
        Create dict containing all metadata from siblings and own metadata
        """
        md = {}
        self._metadata[model.MD_ACQ_DATE] = time.time()  # Time since Epoch

        # Gather metadata from all related HW components and own _meta_data
        md_devices = [self.parent._metadata, self._metadata, self._descanner._metadata, self._scanner._metadata]
        for md_dev in md_devices:
            for key in md_dev.keys():
                if key not in md:
                    md[key] = md_dev[key]
                elif key in (model.MD_HW_NAME, model.MD_HW_VERSION, model.MD_SW_VERSION):
                    # TODO for updated simulator version here the ASM_service version, SAM firmware etc. is merged
                    md[key] = ", ".join([md[key], md_dev[key]])
        return md

    def _setAcqDelay(self, acqDelay):
        """
        Sets the delay for the detector to start recording after a mega field acquisition was started/triggered. It is
        checked that the mppc detector does not start recording before the scanner starts scanning.

        :param acqDelay: (float) The requested acquisition delay in seconds.
        :return (float): The set acquisition delay in seconds.
        """
        # acquiring images with mppc detector needs to start after scanning with the ebeam started
        if acqDelay < self._scanner.scanDelay.value[0]:
            raise ValueError("Requested acquisition delay is %s sec. Acquisition delay cannot be smaller than the "
                             "current scan delay of %s sec." % (acqDelay, self._scanner.scanDelay.value[0]))

        return acqDelay

    def _setFilename(self, filename):
        """
        Set the requested sub-directories, where the image data should be stored on the external storage,
        and the filename (megafield id). Note: Name stored in filename will be also a directory on the external
        storage containing the tiles of the respective megafield.
        Check if the file name complies with the set of allowed characters.
        :param filename: (str) The requested sub-directories and filename for the image data to be acquired.
        :return: (str) The set sub-directories and filename for the image data to be acquired.
        """
        # basename is equivalent to megafield id
        if not re.fullmatch(ASM_FILE_CHARS, os.path.basename(filename)):
            raise ValueError("Filename %s contains invalid characters. Only the following characters are allowed: "
                             "'%s'." % (filename, ASM_FILE_CHARS[1:-2]))

        # basename cannot be > 50 characters
        if len(os.path.basename(filename)) > 50:
            raise ValueError("Filename '%s' contains %s characters. Maximum of 50 characters is allowed."
                             % (os.path.basename(filename), len(os.path.basename(filename))))

        # dirname is equivalent to subdirectories on external storage
        if not re.fullmatch(ASM_SUBDIR_CHARS, os.path.dirname(filename)):
            raise ValueError("Filename %s contains invalid characters. Only the following characters are allowed: "
                             "'%s'." % (filename, ASM_SUBDIR_CHARS[1:-2]))

        return filename

    def _setCellTranslation(self, cellTranslation):
        """
        Setter for the cell translation, each x, y cell translation (overscan parameters) is stored as a tuple of two
        ints. The cell translations of all cells for a row are nested in another tuple.
        And finally, all cell translations per row are nested in another tuple representing the full cell image.
        (((x0-int, y0-int), (x1-int, y1-int)....), ((x8-int, x8-int), .....), ......) etc.
        This setter checks the correct shape of the nested tuples, the type and minimum value.

        :param cellTranslation: (nested tuple of ints)
        :return: cell translation: (nested tuple of ints)
        """
        if len(cellTranslation) != self._shape[0]:
            raise ValueError("An incorrect shape of the cell translation parameters is provided.\n "
                             "Please change the shape of the cell translation parameters according to the shape of the "
                             "mppc detector.\n "
                             "Cell translation parameter values remain unchanged.")

        for row, cellTranslationRow in enumerate(cellTranslation):
            if len(cellTranslationRow) != self._shape[1]:
                raise ValueError("An incorrect shape of the cell translation parameters is provided.\n"
                                 "Please change the shape of the cell translation parameters according to the shape of "
                                 "the mppc detector.\n "
                                 "Cell translation parameter values remain unchanged.")

            for column, eff_origin in enumerate(cellTranslationRow):
                if not isinstance(eff_origin, (tuple, list)) or len(eff_origin) != 2:
                    raise ValueError("Incorrect cell translation parameters provided, wrong number/type of coordinates "
                                     "for cell (%s, %s) are provided.\n"
                                     "Cell translation parameter values remain unchanged." %
                                     (row, column))

                if not isinstance(eff_origin[0], int) or not isinstance(eff_origin[1], int):
                    raise ValueError(
                        "An incorrect type is used for the cell translation coordinates of cell (%s, %s).\n"
                        "Type expected is: '(%s, %s)' type received '(%s, %s)'\n"
                        "Cell translation parameter values remain unchanged." %
                        (row, column, int, int, type(eff_origin[0]), type(eff_origin[1]))
                    )

                if eff_origin[0] < 0 or eff_origin[1] < 0:
                    raise ValueError("Please use a minimum of 0 cell translation coordinates of cell (%s, %s).\n"
                                     "Cell translation parameter values remain unchanged." %
                                     (row, column))

        # force items to be a tuple
        cellTranslation = tuple(tuple(tuple(item) for item in row) for row in cellTranslation)

        return cellTranslation

    def _setCellDigitalGain(self, cellDigitalGain):
        """
        Setter for the digital gain of the cells, each cell has a digital gain stored as a float (compensating for
        the differences in gain for the grey values in each detector cell). The digital gain values for a full row
        are nested in a tuple. And finally, all the digital gain values per row are nested in a another tuple
        representing the full cell image. This setter checks the correct shape of the nested tuples, the type and
        minimum value.

        :param cellDigitalGain: (nested tuple of floats)
        :return: cellDigitalGain: (nested tuple of floats)
        """
        if len(cellDigitalGain) != self._shape[0]:
            raise ValueError("An incorrect shape of the digital gain parameters is provided. Please change the "
                             "shape of the digital gain parameters according to the shape of the mppc detector.\n"
                             "Digital gain parameter values remain unchanged.")

        for row, cellDigitalGain_row in enumerate(cellDigitalGain):
            if len(cellDigitalGain_row) != self._shape[1]:
                raise ValueError("An incorrect shape of the digital gain parameters is provided.\n"
                                 "Please change the shape of the digital gain parameters according to the shape of the "
                                 "mppc detector.\n "
                                 "Digital gain parameter values remain unchanged.")

            for column, DigitalGain in enumerate(cellDigitalGain_row):

                if not isinstance(DigitalGain, (int, float)):
                    raise ValueError("An incorrect type is used for the digital gain parameters of cell (%s, %s).\n"
                                     "Type expected is: '%s' or '%s'; type received '%s' \n"
                                     "Digital gain parameter values remain unchanged." %
                                     (row, column, float, int, type(DigitalGain)))

                if DigitalGain < 0:
                    raise ValueError("Please use a minimum of 0 for digital gain parameters of cell image (%s, %s).\n"
                                     "Digital gain parameter values remain unchanged." %
                                     (row, column))

        # force items to be a tuple
        cellDigitalGain = tuple(tuple(item) for item in cellDigitalGain)

        return cellDigitalGain

    def _setCellDarkOffset(self, cellDarkOffset):
        """
        Setter for the dark offset of the cells, each cell has a dark offset stored as an integer (compensating for
        the offset in darkness in each detector cell). The dark offset  values for a full row are nested in a tuple.
        And finally, all dark offset values per row are nested in a another tuple representing the full cell image.
        This setter checks the correct shape of the nested tuples, the type and minimum value.

        :param cellDarkOffset: (nested tuple of ints)
        :return: cellDarkOffset: (nested tuple of ints)
        """
        if len(cellDarkOffset) != self._shape[0]:
            raise ValueError("An incorrect shape of the dark offset parameters is provided.\n"
                             "Please change the shape of the dark offset parameters according to the shape of the mppc "
                             "detector.\n "
                             "Dark offset parameter values remain unchanged.")

        for row, cellDarkOffsetRow in enumerate(cellDarkOffset):
            if len(cellDarkOffsetRow) != self._shape[1]:
                raise ValueError("An incorrect shape of the dark offset parameters is provided.\n"
                                 "Please change the shape of the dark offset parameters according to the shape of the "
                                 "mppc detector.\n "
                                 "Dark offset parameter values remain unchanged.")

            for column, DarkOffset in enumerate(cellDarkOffsetRow):
                if not isinstance(DarkOffset, int):
                    raise ValueError("An incorrect type is used for the dark offset parameter of cell (%s, "
                                     "%s). \n"
                                     "Please use type integer for dark offset for this cell image.\n"
                                     "Type expected is: '%s' type received '%s' \n"
                                     "Dark offset parameter values remain unchanged." %
                                     (row, column, int, type(DarkOffset)))

                if DarkOffset < 0:
                    raise ValueError("Please use a minimum of 0 for dark offset parameters of cell image (%s, %s).\n"
                                     "Dark offset parameter values remain unchanged." %
                                     (row, column))

        # force items to be a tuple
        cellDarkOffset = tuple(tuple(item) for item in cellDarkOffset)

        return cellDarkOffset

    def getTotalLineScanTime(self):
        """
        Calculate the time for scanning one line (row) of pixels in a single field image including over-scanned
        pixels (cell complete resolution) and flyback time (time the descanner needs to move back to the start
        position for the next line scan).
        :return: (float) Estimated time to scan a single line including overscanned pixels and the flyback
                 time in seconds.
        """
        descanner = self.parent._mirror_descanner
        scanner = self.parent._ebeam_scanner
        acq_dwell_time = scanner.dwellTime.value

        resolution_x = self.cellCompleteResolution.value[0]
        line_scan_time = acq_dwell_time * resolution_x
        flyback_time = descanner.physicalFlybackTime.value

        # Check if the descanner clock period is still a multiple of the system clock period, otherwise raise an
        # error. This check is needed because as a fallback option for the sampling period/calibration dwell time of
        # the scanner the descanner period might be used. The descanner period value needs to be send to the ASM in
        # number of ticks.
        if not almost_equal(descanner.clockPeriod.value % self.parent._ebeam_scanner.clockPeriod.value, 0):
            logging.error("Descanner and/or system clock period changed. Descanner period is no longer a multiple of "
                          "the system clock period. The calculation of the scanner calibration setpoints need "
                          "to be adjusted.")
            raise ValueError("Descanner clock period is no longer a whole multiple of the system clock period.")

        # Remainder of the line scan time, part which is not a whole multiple of the descan periods.
        remainder_scanning_time = line_scan_time % descanner.clockPeriod.value
        if remainder_scanning_time != 0:
            # Adjusted the flyback time if there is a remainder of scanning time by adding one setpoint to ensure the
            # line scan time is equal to a whole multiple of the descanner clock period
            flyback_time = flyback_time + (descanner.clockPeriod.value - remainder_scanning_time)

        # Total line scan time is the period of the calibration signal.
        return numpy.round(line_scan_time + flyback_time, 9)  # Round to prevent floating point errors

    def getTotalFieldScanTime(self):
        """
        Calculate the time for scanning a single field image including over-scanned pixels (cell complete resolution)
        and flyback time (time the descanner needs to move back to the start position for the next line scan).
        :return: (float) Estimated time to scan a single field image including over-scanned pixels and the flyback
                 time in seconds.
        """
        line_scan_time = self.getTotalLineScanTime()
        resolution_y = self.cellCompleteResolution.value[1]
        field_scan_time = line_scan_time * resolution_y

        return field_scan_time

    def _updateFrameDuration(self, _):
        """
        Update everytime when one of the settings that affect the acquisition time for a
        single field image (frame), has changed.
        """
        field_scan_time = self.getTotalFieldScanTime()
        self.frameDuration._set_value(field_scan_time, force_write=True)

    def updateMetadata(self, md):
        if model.MD_FIELD_SIZE in md:
            # The ASM API only accepts an effective field size that is a multiple of 32
            eff_field_size = md.get(model.MD_FIELD_SIZE)
            # FIXME there is a bug on technolutions side, therefore an overlap of 0% is not allowed.
            #  Once this bug is fixed the first check can be removed.
            if (eff_field_size[0] == self._scanner.resolution.value[0] or
                    eff_field_size[1] == self._scanner.resolution.value[0]):
                sug_field_size = self._scanner.resolution.value[0] - 32
                suggested_overlap = 1 - sug_field_size / self._scanner.resolution.value[0]
                raise ValueError(f"Overlap of 0 is not allowed, suggested overlap: {suggested_overlap * 100}%")
            elif eff_field_size[0] % 32 != 0 or eff_field_size[1] % 32 != 0:
                # Calculate the suggested overlap only based on the first axis,
                # because the overlap is a single number and it is just a suggestion.
                if eff_field_size[0] % 32 <= 16:
                    sug_field_size = eff_field_size[0] - eff_field_size[0] % 32
                else:
                    sug_field_size = eff_field_size[0] + 32 - eff_field_size[0] % 32
                suggested_overlap = 1 - sug_field_size / self._scanner.resolution.value[0]
                raise ValueError(
                    f"Overlap must result in the value of the effective field size being a multiple of 32. "
                    f"Effective field size is: {eff_field_size}, suggested overlap: {suggested_overlap * 100}%"
                )
        super().updateMetadata(md)


class ASMDataFlow(model.DataFlow):
    """
    Represents the acquisition on the ASM.
    """

    def __init__(self, mppc):
        super().__init__()

        # Make mppc object an private attribute (which is used to call the start, next, stop and get methods)
        self._mppc = mppc

    def start_generate(self):
        """
        Start the dataflow.
        """
        self._mppc.startAcquisition()

    def next(self, field_num):
        """
        Acquire the next field image if at least one subscriber on the dataflow is present.
        :param field_num: (int, int) x,y coordinates of the field number.
        :raise: (ValueError) Raise if there is no listener on the dataflow.
        """
        if self._count_listeners() == 0:
            raise ValueError("There is no listener subscribed to the dataflow yet.")

        self._mppc.getNextField(field_num)

    def notify(self, data):
        """
        Call this method to share the data with all the listeners or return in case an exception was raised. Note, that
        in the later case, the listeners are not notified and are not aware that an exception has occurred.
        :param data: (DataArray or Exception) Either the image data to be sent to the listeners or an Exception that
            was raised during image acquisition.
        """
        if isinstance(data, Exception):
            logging.error("During image data acquisition, an exception has occurred: %s", data)
            return  # makes sure that the acquisition thread does not fail
        super().notify(data)  # if image data received, notify the listeners

    def stop_generate(self):
        """
        Stop the dataflow.
        """
        self._mppc.stopAcquisition()

    def get(self, *args, **kwargs):
        """
        Acquire a single field image. Can only be called if no other acquisition is active.
        :return: (DataArray or Exception) The acquired single field image.
        :raise (ValueError) Raise if there is already a listener on the dataflow.
        """
        if self._count_listeners() < 1:
            # Acquire and return received image
            image = self._mppc.acquireSingleField(*args, **kwargs)
            return image
        else:
            raise ValueError("There is already an acquisition ongoing with %s listeners subscribed. First cancel/stop "
                             "the current running acquisition before acquiring a single field-image."
                             % self._count_listeners())


class AsmApiException(Exception):
    """
    Exception for raising errors while calling the ASM API.
    """

    def __init__(self, url, response, expected_status):
        """
        Initializes exception object defining the error message to be displayed to the user as a response.
        And performs basic checks on ASM items to see of those are not the cause of the error. (e.g. monitor/item
        sam_connection_operational, ext_store_connection_operational, offload_queue_fill_level, install_in_progress,
        last_install_success)

        :param url (str): URL of the call.
        :param response (requests.models.Response object): full/raw response from the ASM API
        :param expected_status (int): the expected status code
        """
        url = url
        status_code = response.status_code
        reason = response.reason
        expected_status = expected_status

        try:
            content_translated = json.loads(response.content)
            self._errorMessageResponse(url, status_code, reason, expected_status,
                                       content_translated['status_code'],
                                       content_translated['message'])

        except Exception:
            # Create an alternative error message when creating the intended error message fails. This may happen when
            # the response does not hold content of the type json, or the content does not hold the proper keys in
            # the dict. This may happen if the call is not found by the ASM API.
            if hasattr(response, "text"):
                self._errorMessageResponse(url, status_code, reason, expected_status, status_code, response.text)
            elif hasattr(response, "content"):
                self._errorMessageResponse(url, status_code, reason, expected_status, status_code, response.content)
            else:
                self._emptyResponse(url, status_code, reason, expected_status)

        # Also log the error so it is easier to find it back when the error was received in the log
        logging.error(self._error)

    def __str__(self):
        # For displaying the error
        return self._error

    def _errorMessageResponse(self, url, status_code, reason, expected_status, error_code, error_message):
        """
        Defines the error message if a response which contains information is received from the ASM. Uses all input
        to create an error message.

        :param url (str): URL of the call.
        :param status_code (int): Status code in the response object.
        :param reason (str): Text corresponding to the returned status code.
        :param expected_status (int): Expected status code.
        :param error_code (int): received status code.
        :param error_message (str): received error message (translated from json, via a dict to a str).
        """
        self._error = ("\n"
                       "Call to %s received unexpected answer.\n"
                       "Received status code '%s' because of the reason '%s', but expected status code was'%s'\n"
                       "Error status code '%s' with the message: '%s'\n" %
                       (url, status_code, reason, expected_status, error_code, error_message))

    def _emptyResponse(self, url, status_code, reason, expected_status):
        """
        Defines the error message if the response received from the ASM does not contain the proper error
        information.
        :param url (str): URL of the call.
        :param status_code (int): Status code in the response object.
        :param reason (str): Text corresponding to the returned status code.
        :param expected_status (int): Expected status code.
        """
        self._error = ("\n"
                       "Call to %s received unexpected answer.\n"
                       "Got status code '%s' because of the reason '%s', but expected '%s'\n" %
                       (url, status_code, reason, expected_status))


class TerminationRequested(Exception):
    """
    Acquisition termination requested closing the acquisition thread in the _acquire method.
    """
    pass
