# -*- coding: utf-8 -*-
'''
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
'''
# Driver/wrapper for the ASP API in Odemis which can connect Odemis to the ASM API made by Technolution for the
# multi-beam project
from __future__ import division

import math
import numpy
import base64
import json
import logging
import queue
import re
import threading
import time
from PIL import Image
from io import BytesIO
from urllib.parse import urlparse, urlunparse
from requests import Session
from scipy import signal

from odemis import model
from odemis.model import HwError
from odemis.util import almost_equal

from technolution_asm.models.field_meta_data import FieldMetaData
from technolution_asm.models.mega_field_meta_data import MegaFieldMetaData
from technolution_asm.models.cell_parameters import CellParameters
from technolution_asm.models.calibration_loop_parameters import CalibrationLoopParameters

VOLT_RANGE = (-10, 10)
DATA_CONTENT_TO_ASM = {"empty": None, "thumbnail": True, "full": False}
RUNNING = "installation in progress"
FINISHED = "last installation successful"
FAILED = "last installation failed"

def convertRange(value, value_range, output_range):
    """
    Converts a value from one range to another range. For example: map a value in volts to the respective value in bits.
    Converts an value with a given range to a provided output range.
    The min value of the ranges cannot be above zero
    :param value (tuple/array/list): input values to be converted
    :param value_range (tuple/array/list): min, max values of the range that is provided for the input value.
    :param output_range (tuple/array/list): min, max values of the range that is provided for the output value.
    :return (numpy.array): Input value mapped to new range with same shape as input value
    """
    # Convert from possible tuple/list input to numpy arrays
    input_range = numpy.array(value_range)
    output_range = numpy.array(output_range)

    # Determine the span of each input range
    span_input_range = value_range[1] - value_range[0]
    span_output_range = output_range[1] - output_range[0]

    # Map to range with span of 1
    normalized_value = (value - input_range[0]) / span_input_range
    # Map to output range
    mapped_value = normalized_value * span_output_range + output_range[0]
    return mapped_value


def convert2Bits(value, value_range):
    """
    Converts an input value with corresponding range to a float with an int16 bit range.
    Uses the convertRange function with as default output_range the int16 bit range. Does not round or apply floor.
    :param value (tuple/array/list): input values to be converted
    :param value_range (tuple/array/list): min, max values of the range that is provided for the input value.
    :return(numpy.array of floats): Converted to INT16 range with same shape as the input value. Has type 'float' and
    is not rounded nor is floor applied.
    """
    return convertRange(value, value_range, (-2**15, 2**15 - 1))


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
            self.asmApiPostCall("/scan/finish_mega_field", 204) # Stop acquisition
            self.asmApiGetCall("/scan/clock_frequency", 200) # Test connection from ASM to SAM

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

        clockFrequencyData = self.asmApiGetCall("/scan/clock_frequency", 200)
        self.clockPeriod = model.FloatVA(1 / clockFrequencyData['frequency'], unit='s', readonly=True)

        # NOTE: Do not write real username/password here since this is published on github in plain text!
        # example = ftp://username:password@127.0.0.1:5000/directory/sub-directory
        self.externalStorageURL = model.StringVA('ftp://%s:%s@%s/%s' %
                                                 (externalStorage["username"],
                                                  externalStorage["password"],
                                                  externalStorage["host"],
                                                  externalStorage["directory"]),
                                                 setter=self._setURL)
        self._setURL(self.externalStorageURL.value)  # Check and set the external storage URL to the ASM.

        # VA to switch between calibration and acquisition mode (megafield acquisition)
        self.calibrationMode = model.BooleanVA(False, setter=self._setCalibrationMode)

        # CalibrationParameters contains the current calibration parameters
        self._calibrationParameters = None

        # TODO: Commented out because not present on EA
        # self.asmApiPostCall("/config/set_system_sw_name?software=%s" % name, 204)

        # Setup hw and sw version
        # TODO make call set_system_sw_name too new simulator (if implemented)
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
            logging.exception("Performing system checks failed. Could not perform a successful call to %s ." % item_name)

    def checkMegaFieldExists(self, mega_field_id, storage_dir):
        """
        Check if filename complies with set allowed characters.
        :param mega_field_id (string): name of the mega field.
        :param storage_dir (string): path to the mega field.
        :return (bool): True if mega field exists.
        """
        ASM_FILE_ILLEGAL_CHARS = r'[^a-z0-9_()-]'
        ASM_PATH_ILLEGAL_CHARS = r'[^A-Za-z0-9/_()-]'
        if re.search(ASM_PATH_ILLEGAL_CHARS, storage_dir):
            logging.error("The specified storage directory contains invalid characters, cannot check if mega field "
                          "exists (only the characters '%s' are allowed)." % ASM_FILE_ILLEGAL_CHARS[2:-1])
            return False

        if re.search(ASM_FILE_ILLEGAL_CHARS, mega_field_id):
            logging.error("The specified mega_field_id contains invalid characters, cannot check if mega field exists"
                          "(only the characters '%s' are allowed)." % ASM_FILE_ILLEGAL_CHARS[2:-1])
            return False

        response = self.asmApiPostCall("/scan/check_mega_field?mega_field_id=%s&storage_directory=%s" %
                                       (mega_field_id, storage_dir), 200, raw_response=True)
        return json.loads(response.content)["exists"]

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

        :return calibration_data (CalibrationLoopParameters object): calibration data object which can be send to the
        ASM API
        """
        descanner = self._mirror_descanner
        scanner = self._ebeam_scanner
        acq_dwell_time = scanner.dwellTime.value

        resolution = self._mppc.cellCompleteResolution.value[0]
        line_scan_time = acq_dwell_time * resolution
        flyback_time = descanner.physicalFlybackTime

        # Check if the descanner clock period is still a multiple of the system clock period, otherwise raise an
        # error. This check is needed because as a fallback option for the sampling period/calibration dwell time of
        # the scanner the descanner period might be used. The descanner period value needs to be send to the ASM in
        # number of ticks.
        if not almost_equal(descanner.clockPeriod.value % self.clockPeriod.value, 0):
            logging.error("Descanner and/or system clock period changed. Descanner period is no longer a multiple of "
                          "the system clock period. The calculation of the scanner calibration setpoints need "
                          "to be adjusted.")
            raise ValueError("Descanner clock period is no longer a whole multiple of the system clock period.")

        # Remainder of the line scan time, part which is not a whole multiple of the descan periods.
        remainder_scanning_time = line_scan_time % descanner.clockPeriod.value
        if remainder_scanning_time is not 0:
            # Adjusted the flyback time if there is a remainder of scanning time by adding one setpoint to ensure the
            # line scan time is equal to a whole multiple of the descanner clock period
            flyback_time = flyback_time + (descanner.clockPeriod.value - remainder_scanning_time)

        # Total line scan time is the period of the calibration signal.
        total_line_scan_time = numpy.round(line_scan_time + flyback_time, 9)  # Round to prevent floating point errors

        # Get the scanner and descanner setpoints
        x_descan_setpoints, y_descan_setpoints = self._mirror_descanner.getCalibrationSetpoints(total_line_scan_time)

        x_scan_setpoints, y_scan_setpoints, tick_scan_calibration_dwell_time =\
                                                self._ebeam_scanner.getCalibrationSetpoints(total_line_scan_time)

        calibration_data = CalibrationLoopParameters(descanner.rotation.value,
                                                     0,  # Descan X offset parameter unused.
                                                     x_descan_setpoints,
                                                     0,  # Descan Y offset parameter unused.
                                                     y_descan_setpoints,
                                                     tick_scan_calibration_dwell_time,
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
        Setter which checks for correctness of FTP url and otherwise returns old value.

        :param url(str): e.g. ftp://username:password@127.0.0.1:5000/directory/sub-directory
        :return: correct ftp url_parser
        """
        ASM_GENERAL_ILLEGAL_CHARS = r'[^A-Za-z0-9/_()-:@]'
        ASM_USER_ILLEGAL_CHARS = r'[^A-Za-z0-9]'
        ASM_PASSWORD_ILLEGAL_CHARS = r'[^A-Za-z0-9]'
        ASM_HOST_ILLEGAL_CHARS = r'[^A-Za-z0-9.]'
        ASM_PATH_ILLEGAL_CHARS = r'[^A-Za-z0-9/_()-]'

        url_parser = urlparse(url)  # Transform input string to url_parse object

        # Perform general check on valid characters (parses works incorrectly for some invalid characters
        if re.search(ASM_GENERAL_ILLEGAL_CHARS, urlunparse(url_parser)):
            raise ValueError("Invalid character in ftp url is provided, allowed characters are %s placed in the form:"
                             "'ftp://username:password@127.0.0.1:5000/directory/sub-directory'\n"
                             "(Only use the @ to separate the password and the host." % ASM_GENERAL_ILLEGAL_CHARS[2:-1])

        # Perform detailed checks on input
        if url_parser.scheme != 'ftp' \
                or not url_parser.scheme or not url_parser.username or not url_parser.password \
                or not url_parser.hostname or not url_parser.path:
            # Check both the scheme as well if all sub-elements are non-empty
            # Note that if an extra @ is used (e.g. in the password) the parser works incorrectly and sub-elements
            # are empty after splitting the url input
            raise ValueError("Incorrect ftp url is provided, please use form: "
                             "'ftp://username:password@127.0.0.1:5000/directory/sub-directory'\n"
                             "(Only use the @ to separate the password and the host.")

        if re.search(ASM_USER_ILLEGAL_CHARS, url_parser.username):
            raise ValueError(
                    "Username contains invalid characters, username remains unchanged "
                    "(only the characters '%s' are allowed)." % ASM_USER_ILLEGAL_CHARS[2:-1])

        if re.search(ASM_PASSWORD_ILLEGAL_CHARS, url_parser.password):
            raise ValueError(
                    "Password contains invalid characters, password remains unchanged "
                    "(only the characters '%s' are allowed)." % ASM_PASSWORD_ILLEGAL_CHARS[2:-1])

        if re.search(ASM_HOST_ILLEGAL_CHARS, url_parser.hostname):
            raise ValueError(
                    "Host contains invalid characters, host remains unchanged "
                    "(only the characters '%s' are allowed)." % ASM_HOST_ILLEGAL_CHARS[2:-1])

        if re.search(ASM_PATH_ILLEGAL_CHARS, url_parser.path):
            raise ValueError("Path on ftp server contains invalid characters, path remains unchanged "
                             "(only the characters '%s' are allowed)." % ASM_PATH_ILLEGAL_CHARS[2:-1])

        # TODO: Commented out because not present on EA
        # self.asmApiPostCall("/config/set_external_storage?host=%s&user=%s&password=%s" %
        #                     (urlparse(self.externalStorageURL.value).hostname,
        #                      urlparse(self.externalStorageURL.value).username,
        #                      urlparse(self.externalStorageURL.value).password), 204)
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
        self.resolution = model.ResolutionVA((6400, 6400),
                                             ((12*mppcDetectorShape[0], 12*mppcDetectorShape[1]),
                                              (1000*mppcDetectorShape[0], 1000*mppcDetectorShape[1])),
                                             setter=self._setResolution)
        self._shape = self.resolution.range[1]
        # TODO: Dwell time is currently set at a maximum of 40 micro seconds because we cannot calibrate as long as
        #  1e-4 seconds. This is because we are limited to 4000 calibration setpoints.
        self.dwellTime = model.FloatContinuous(4e-7, (4e-7, 4e-5), unit='s')
        self.pixelSize = model.TupleContinuous((4e-9, 4e-9), range=((1e-9, 1e-9), (1e-3, 1e-3)), unit='m',
                                               setter=self._setPixelSize)
        self.rotation = model.FloatContinuous(0.0, range=(0.0, 2 * math.pi), unit='rad')

        self.scanOffset = model.TupleContinuous((0.0, 0.0), range=((-1.0, -1.0), (1.0, 1.0)))
        self.scanGain = model.TupleContinuous((0.3, 0.3), range=((-1.0, -1.0), (1.0, 1.0)))
        # TODO: y scan delay is y prescan lines which is currently unused an can probably be deleted.
        # The scanDelay in x direction maximum (200e-6) is experimentally determined.
        self.scanDelay = model.TupleContinuous((0.0, 0.0), range=((0.0, 0.0), (200e-6, 10.0)), unit='s',
                                               setter=self._setScanDelay)

        self._metadata[model.MD_PIXEL_SIZE] = self.pixelSize.value
        self._metadata[model.MD_DWELL_TIME] = self.dwellTime.value

    def getCalibrationSetpoints(self, total_line_scan_time):
        """
        Calculate the calibration setpoints in Volts for the scanner with a sinus shape for both x and y.

        :param total_line_scan_time (float): Total line scanning time in seconds
        :return:
                x_setpoints (list of ints): Contains the calibration setpoints in x direction in Volts
                y_setpoints (list of ints): Contains the calibration setpoints in y direction in Volts
                calibration_dwell_time_ticks (int): Sampling period in ticks
        """
        # The calibration frequency is the inverse of the total line scan time.
        calibration_frequency = 1 / total_line_scan_time

        # Calculate the sampling period and number of setpoints (the sampling period in seconds is not needed here)
        calibration_dwell_time_ticks, _, nmbr_scanner_points = self._calc_calibration_sampling_period(
                                                                        total_line_scan_time,
                                                                        self.parent.clockPeriod.value,
                                                                        self.parent._mirror_descanner.clockPeriod.value)

        # Determine the amplitude of the setpoints function in bits.
        scan_gain_volts = convertRange(self.scanGain.value, numpy.array(self.scanGain.range)[:, 1], VOLT_RANGE)
        scan_offset_volts = convertRange(self.scanOffset.value, numpy.array(self.scanOffset.range)[:, 1], VOLT_RANGE)
        scan_amplitude_volts = scan_gain_volts - scan_offset_volts

        # Using the time stamps the setpoints are determined.
        time_points_scanner = numpy.linspace(0, total_line_scan_time, nmbr_scanner_points)
        x_setpoints = scan_offset_volts[0] + scan_amplitude_volts[0] * numpy.sin(
                2 * math.pi * calibration_frequency * time_points_scanner)
        y_setpoints = scan_offset_volts[1] + scan_amplitude_volts[1] * signal.sawtooth(
                2 * math.pi * calibration_frequency * time_points_scanner)

        return x_setpoints.tolist(),\
               y_setpoints.tolist(),\
               calibration_dwell_time_ticks

    def _calc_calibration_sampling_period(self, total_line_scan_time, system_clock_period, descanner_clock_period):
        """
        For creation of the calibration setpoints the sampling period is variable but the total number of
        setpoints is limited to maximum 4000 points. Depending on the total line scanning time the calibration
        setpoints have a variable sampling period (calibration dwell time) which is a multiple of the system
        clock period. The number of setpoints (and the corresponding sampling period is chosen such that the
        biggest number of setpoints is used (highest resolution of the signal).
        The sampling period is defined by finding the higest number of setpoints for the calibration signal
        with a sampling period equal to a whole multiple of the system clock period. This is done by iteratively
        increasing the sampling period.

        :param total_line_scan_time (float): Total line scanning time in seconds
        :param system_clock_period (float): clock period of the system and scanner in seconds
        :param descanner_clock_period (float): Descanner lock period of the scanner in seconds
        :return: (int) Sampling period in ticks
                 (float) Sampling period is seconds
                 (int) Number of setpoints
        """
        # TODO MAX_NMBR_POINT value of 4000 is sufficient for the entire range of the dwell time because the maximum
        #  dwell_time is decreased. However, for the original maximum dwell time of 1e-4 seconds, this value
        #  needs to be increased on the ASM HW to a value above 9000.
        MAX_NMBR_POINT = 4000  # Constant maximum number of setpoints

        # Calculate the range (minimum and maximum multiplication factor) in which to search for the best
        # calibration dwell time/sampling period.

        # The minimal number of ticks of the system clock period to get a sampling period with max 4000 setpoints
        min_sampling_period_ticks = int(numpy.ceil((total_line_scan_time / MAX_NMBR_POINT) / system_clock_period))
        # If the found minimum sampling period (in ticks) is smaller than the minimal dwell time for the scanner,
        # use the minimum dwell time in ticks.
        min_sampling_period_ticks = max(min_sampling_period_ticks, int(self.dwellTime.range[0] / system_clock_period))

        # The max sampling period in ticks is defined by the descanner clock period
        max_sampling_period_ticks = int(descanner_clock_period / system_clock_period)

        for sampling_period_ticks in range(min_sampling_period_ticks, max_sampling_period_ticks):
            sampling_period = sampling_period_ticks * system_clock_period
            number_setpoints = total_line_scan_time / sampling_period
            # Check if the number of points is an integer number (and round because of floating point error). If the
            # number of points is an integer the found sampling period is correct and has the highest resolution
            # possible.
            if numpy.round(number_setpoints, 10) % 1 == 0:
                # For now the sampling period in seconds remains unused by the callers of this method.
                return sampling_period_ticks, sampling_period, int(numpy.round(number_setpoints))

        else:
            logging.debug("Could not optimize the sampling period/calibration dwell time for the scanner. The "
                          "descanner sampling period is used for the scanner as well")
            sampling_period_ticks = max_sampling_period_ticks
            number_setpoints = numpy.round(total_line_scan_time / descanner_clock_period, 10).astype(int)
            # For now the sampling period in seconds remains unused by the callers of this method.
            return sampling_period_ticks, descanner_clock_period, number_setpoints

    def getTicksScanDelay(self):
        """
        :return: Scan delay in multiple of ticks of the ebeam scanner clock frequency
        """
        return (int(self.scanDelay.value[0] / self.parent.clockPeriod.value),
                int(self.scanDelay.value[1] / self.parent.clockPeriod.value))

    def getTicksDwellTime(self):
        """
        :return: Dwell time in multiple of ticks of the system clock period
        """
        return int(self.dwellTime.value / self.parent.clockPeriod.value)

    def getScanOffsetVolts(self):
        """
        Get the scan offset value in volts as defined in the ASM API.
        In this wrapper the scan offset is defined as the minimum scan value on a scale from -10 to 10, while in the
        ASM API the scan offset is defined as the median value of the setpoints during the scanning phase.

        :return (tuple of ints): slope found during the scanning phase for x, y in volts (with range VOLT_RANGE).
        """
        scan_offset_volts = convertRange(self.scanOffset.value, numpy.array(self.scanOffset.range)[:, 1], VOLT_RANGE)
        scan_gain_volts = convertRange(self.scanGain.value, numpy.array(self.scanGain.range)[:, 1], VOLT_RANGE)
        return tuple(((scan_gain_volts + scan_offset_volts) / 2))

    def getScanGainVolts(self):
        """
        Get the scan gain value in Volts as defined in the ASM API.
        In this wrapper the scan gain as the maximum scan value on a range from -1 to 1, while in the
        ASM API the scan gain is defined as the gradient of the setpoints during the scanning phase.

        :return (tuple of ints): slope found during the scanning phase for x, y in volts (with range VOLT_RANGE).
        """
        ##################################################################
        # For the acquisition in the ASM API the scan gain is defined as the stepsize in volts per setpoints.
        # From the first to the last pixels the number of steps are: number of points - 1.
        # So: number of steps = resolution - 1
        # This means that the stepsize in volts per setpoint is (maximum value - minimum value) / number of steps
        # example: scanning ramp resembled by 4 setpoints (values: 2 to 8)
        #               --8-- amplitude (scan_gain) -> 4th (last) setpoint
        #          --6--
        #     --4--
        # --2-- offset (scan_offset) -> 1st setpoint
        # Number of setpoints = 4
        # Number of steps = number of setpoints - 1 = 3
        # step size = (8 - 2) / 3 = 6 / 3 = 2
        #################################################################
        scan_offset_volts = convertRange(self.scanOffset.value, numpy.array(self.scanOffset.range)[:, 1], VOLT_RANGE)
        scan_gain_volts = convertRange(self.scanGain.value, numpy.array(self.scanGain.range)[:, 1], VOLT_RANGE)
        resolution = numpy.array(self.parent._mppc.cellCompleteResolution.value)
        step_size_in_volts = tuple((scan_gain_volts - scan_offset_volts) / (resolution - 1))
        return step_size_in_volts

    def _setPixelSize(self, pixelSize):
        """
        Setter for the pixel size which ensures only square pixel size are entered

        :param pixelSize (tuple):
        :return (tuple):
        """
        if pixelSize[0] == pixelSize[1]:
            return pixelSize
        else:
            logging.warning("Non-square pixel size entered, only square pixel sizes are supported. "
                            "Width of pixel size is used as height.")
            return pixelSize[0], pixelSize[0]

    def _setScanDelay(self, scanDelay):
        """
        Sets the delay for the scanner to start scanning after a mega field acquisition was started/triggered. It is
        checked that the scanner starts scanning before the detector starts recording. Setter which prevents the mppc
        detector from recording before the ebeam scanner has started.

        :param scanDelay (tuple):
        :return (tuple):
        """
        # Check if detector can record images before ebeam scanner has started to scan.
        if self.parent._mppc.acqDelay.value >= scanDelay[0]:
            return scanDelay
        else:
            # Change Scan Delay value so that the mppc does not start recording before the ebeam scanner has started to
            # scan.
            logging.warning("Detector cannot record images before ebeam scanner has started to scan.\n"
                            "Detector needs to start after scanner.")
            logging.info("The entered acquisition delay is %s in the eBeamScanner and the scan delay in the mppc is "
                         "%s" % (scanDelay[0], self.parent._mppc.acqDelay.value))
            return self.scanDelay.value

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

        self.rotation = model.FloatContinuous(0, range=(0, 2 * math.pi), unit='rad')
        self.scanOffset = model.TupleContinuous((0.0, 0.0), range=((-1, -1), (1, 1)))
        self.scanGain = model.TupleContinuous((0.007, 0.007), range=((-1, -1), (1, 1)))

        clockFrequencyData = self.parent.asmApiGetCall("/scan/descan_control_frequency", 200)
        self.clockPeriod = model.FloatVA(1 / clockFrequencyData['frequency'], unit='s', readonly=True)

        # TODO: Adapt value of physical flyback time after testing on HW. --> Wilco/Andries
        # Physical time for the mirror descanner to perform a flyback, assumed constant [s].
        self.physicalFlybackTime = 250e-6

    def getXAcqSetpoints(self):
        """
        Creates the setpoints for the descanner in x direction (used by the ASM) for scanning one row of pixels. The
        x setpoints describe the movement of the descanner during the scanning of one full row of pixels.  To the ASM
        API one period of setpoints (scanning of one row) is send which is repeated for all following rows.
        A single sawtooth profile (rise and crash) followed by a flyback period (X=0) is used as trajectory. No
        smoothing or low-pas filtering is used to create the trajectory of these setpoints.

        :return (list of ints): Setpoints in x direction.
        """
        # The setpoints for an acquisition resemble a linear ramp for scanning a line of pixels, followed by the
        # flyback, where the descanner moves back to its starting position.
        # The scanning setpoints are determined via the linear equation x_setpoints = A*t + B
        # With 'A' being the slope of the scanning line which is: (maximum - minimum) / scanning time
        # And 'B' being the offset

        descan_period = self.clockPeriod.value  # in seconds
        dwellTime = self.parent._ebeam_scanner.dwellTime.value  # in seconds
        x_cell_resolution = self.parent._mppc.cellCompleteResolution.value[0]  # pixels
        scan_offset_bits = convert2Bits(self.scanOffset.value[0], numpy.array(self.scanOffset.range)[:, 1])
        scan_gain_bits = convert2Bits(self.scanGain.value[0], numpy.array(self.scanGain.range)[:, 1])

        # all units in seconds
        scanning_time = dwellTime * x_cell_resolution

        # Remainder of the scanning time which is not a whole multiple of the descan periods.
        remainder_scanning_time = scanning_time % descan_period

        # Calculate the time stamps for the setpoints excluding the remainder of the scanning time.
        number_setpoints = int(scanning_time // descan_period)
        timestamp_setpoints = numpy.linspace(0, scanning_time - remainder_scanning_time, number_setpoints)

        # Slope of the scanning ramp in bits per second
        scanning_slope = (scan_gain_bits - scan_offset_bits) / scanning_time
        scanning_points = scanning_slope * timestamp_setpoints + scan_offset_bits  # x_setpoints=slope*time + offset

        # Use almost_equal to handle floating point errors.
        if not almost_equal(remainder_scanning_time, 0, rtol=0, atol=1e-10):
            # Allow the descanner to finish scanning the last pixels by adding one setpoint with the scan gain value.
            scanning_points = numpy.hstack((scanning_points, scan_gain_bits))
            # Note: For now the extra setpoint to compensate for the remaining scanning time has a value equal to the
            # scan gain. Because of the inertia of the mirror it is expected that this is the fastest solution
            # which will not significantly disturb the trajectory of the mirror.

        # Calculation of the flyback points:
        # Round up so that if the physical flyback time (physical restricted time for the mirror to move back to its
        # original position) is not a whole multiple of the descan period the flyback points allow enough time for the
        # descanner to move back.
        number_flyback_points = math.ceil(self.physicalFlybackTime / descan_period)
        flyback_points = scan_offset_bits + numpy.zeros(number_flyback_points)

        setpoints = numpy.concatenate((scanning_points, flyback_points))

        # Converting to an integer using numpy.floor() is more correct than int() because the latter one rounds down
        # towards 0, so negative values are not treated the same as positive values, which makes values not uniformly
        # distributed.
        return numpy.floor(setpoints).astype(int).tolist()

    def getYAcqSetpoints(self):
        """
        Creates the setpoints for the descanner in y direction for the ASM.
        During the scanning of a row of pixels the y value is constant. Only one y descan setpoint per full row of
        pixels will be read. After completing the scan of a full row of pixels a y setpoints is read which
        describes the movement when the scanner goes from one row to the next row of pixels.

        :return (list of ints): contains the setpoints in y direction.
        """
        # Start value of the scanning ramp in bits
        scan_offset_bits = convert2Bits(self.scanOffset.value[1], numpy.array(self.scanOffset.range)[:, 1])
        # Amplitude of the scanning ramp in bits.
        scan_gain_bits = convert2Bits(self.scanGain.value[1], numpy.array(self.scanGain.range)[:, 1])

        y_cell_size = self.parent._mppc.cellCompleteResolution.value[1]  # pixels
        setpoints = numpy.linspace(scan_offset_bits, scan_gain_bits, y_cell_size)

        # Converting to an integer using numpy.floor() is more correct than int() because the latter one rounds down
        # towards 0, so negative values are not treated the same as positive values, which makes values not uniformly
        # distributed.
        return numpy.floor(setpoints).astype(int).tolist()

    def getCalibrationSetpoints(self, total_line_scan_time):
        """
        Calculate and return the calibration setpoints in bits for the descanner with a sinus shape for x and a flat
        line at zero for y.

        :param total_line_scan_time (float): Total line scanning time in seconds
        :return:
                x_setpoints (list of ints): Contains the calibration setpoints in x direction in bits
                y_setpoints (list of ints): Contains the calibration setpoints in y direction  in bits
        """
        # The calibration frequency is the inverse of the total line scan time.
        calibration_frequency = 1 / total_line_scan_time

        # Sampling period is equal to the descanner clock period. The number of setpoints is the total line scanning
        # time divided by the descanner clock period.
        nmr_setpoints = numpy.round(total_line_scan_time / self.clockPeriod.value, 10).astype(int)

        # Determine the amplitude of the setpoints function in bits.
        descan_gain_bits = convert2Bits(self.scanGain.value, numpy.array(self.scanGain.range)[:, 1])
        descan_offset_bits = convert2Bits(self.scanOffset.value, numpy.array(self.scanOffset.range)[:, 1])
        descan_amplitude_bits = descan_gain_bits - descan_offset_bits

        # Using the time stamps the setpoints are determined
        time_points_descanner = numpy.linspace(0, total_line_scan_time, nmr_setpoints)
        x_setpoints = descan_offset_bits[0] + descan_amplitude_bits[0] * numpy.sin(
                2 * math.pi * calibration_frequency * time_points_descanner)
        # The decan setpoints in y direction should be constant and have a output of zero.
        y_setpoints = 0 * time_points_descanner

        # Converting the setpoints to integers using numpy.floor() is more correct than int() because the latter
        # one rounds down towards 0, so negative values are not treated the same as positive values, which makes values
        # not uniformly distributed.
        return numpy.floor(x_setpoints).astype(int).tolist(), numpy.floor(y_setpoints).astype(int).tolist()

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
        self.filename = model.StringVA("unnamed_acquisition", setter=self._setFilename)
        self.dataContent = model.StringEnumerated('empty', DATA_CONTENT_TO_ASM.keys())
        self.acqDelay = model.FloatContinuous(0.0, range=(0, 200e-6), unit='s', setter=self._setAcqDelay)
        self.overVoltage = model.FloatContinuous(1.5, range=(0, 5), unit='V')

        # Cell acquisition parameters
        self.cellTranslation = model.TupleVA(
                tuple(tuple((50, 50) for i in range(0, self.shape[0])) for i in range(0, self.shape[1])),
                setter=self._setCellTranslation)
        self.cellDarkOffset = model.TupleVA(
                tuple(tuple(0 for i in range(0, self.shape[0])) for i in range(0, self.shape[1]))
                , setter=self._setCellDarkOffset)
        self.cellDigitalGain = model.TupleVA(
                tuple(tuple(1.2 for i in range(0, self.shape[0])) for i in range(0, self.shape[1])),
                setter=self._setCellDigitalGain)

        # The minimum of the cell resolution cannot be lower than the minimum effective cell size.
        self.cellCompleteResolution = model.ResolutionVA((900, 900), ((12, 12), (1000, 1000)))

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
            self.acq_queue.put(("terminate", ))
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

        scan_to_acq_delay = int((self.acqDelay.value - self._scanner.scanDelay.value[0]) /
                                self.parent.clockPeriod.value)  # Calculate and convert from seconds to ticks

        X_descan_setpoints = self._descanner.getXAcqSetpoints()
        Y_descan_setpoints = self._descanner.getYAcqSetpoints()

        megafield_metadata = \
            MegaFieldMetaData(
                    mega_field_id=self.filename.value,
                    storage_directory=urlparse(self.parent.externalStorageURL.value).path,
                    custom_data="No_custom_data",
                    stage_position_x=float(stage_position[0]),
                    stage_position_y=float(stage_position[1]),
                    # Convert pixels size from meters to nanometers
                    pixel_size=int(self._scanner.pixelSize.value[0] * 1e9),
                    dwell_time=self._scanner.getTicksDwellTime(),
                    x_scan_to_acq_delay=scan_to_acq_delay,
                    x_scan_delay=self._scanner.getTicksScanDelay()[0],
                    x_cell_size=self.cellCompleteResolution.value[0],
                    x_eff_cell_size=eff_cell_size[0],
                    y_cell_size=self.cellCompleteResolution.value[1],
                    y_eff_cell_size=eff_cell_size[1],
                    # y_prescan_lines is not available on EA1
                    # y_prescan_lines=self._scanner.getTicksScanDelay()[1],
                    x_scan_gain=self._scanner.getScanGainVolts()[0],
                    y_scan_gain=self._scanner.getScanGainVolts()[1],
                    x_scan_offset=self._scanner.getScanOffsetVolts()[0],
                    y_scan_offset=self._scanner.getScanOffsetVolts()[1],
                    # TODO API gives error for values < 0 but YAML does not specify so
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
                    if acquisition_in_progress:
                        logging.warning("ASM acquisition already had the '%s', received this command again." % command)
                        continue

                    acquisition_in_progress = True
                    megafield_metadata = args[0]
                    self.parent.asmApiPostCall("/scan/start_mega_field", 204, megafield_metadata.to_dict())

                elif command == "next":
                    if not acquisition_in_progress:
                        logging.warning("Start ASM acquisition before request to acquire field images.")
                        continue

                    self._metadata = self._mergeMetadata()
                    field_data = args[0]  # Field metadata for the specific position of the field to scan
                    dataContent = args[1]  # Specifies the type of image to return (empty, thumbnail or full)
                    notifier_func = args[2]  # Return function (usually, dataflow.notify or acquire_single_field queue)

                    # FIXME: Hack: The current ASM HW does not scan the very first field image correctly. This issue
                    #  needs to be fixed in HW. However, until this is done, we need to "through away" the first field
                    #  image and scan it a second time to receive a good first field image. To do so, just always scan
                    #  the first image twice.
                    if field_data.position_x == 0 and field_data.position_y == 0:
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
                        img = Image.open(BytesIO(base64.b64decode(resp.raw.data)))

                        da = model.DataArray(img, metadata=self._metadata)

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
        Put a the command 'start' mega field scan on the queue with the appropriate MegaFieldMetaData Model of the mega
        field image to be scanned. The MegaFieldMetaData is used to setup the HW accordingly, for each field image
        additional field image related metadata is provided.
        """
        self._ensure_acquisition_thread()
        megafield_metadata = self._assembleMegafieldMetadata()
        self.acq_queue.put(("start", megafield_metadata))

    def getNextField(self, field_num):
        """
        Puts the command 'next' field image scan on the queue with the appropriate field meta data model of the field
        image to be scanned. Can only be executed if it preceded by a 'start' mega field scan command on the queue.
        The acquisition thread returns the acquired image to the provided notifier function added in the acquisition queue
        with the "next" command. As notifier function the dataflow.notify is send. The returned image will be
        returned to the dataflow.notify which will provide the new data to all the subscribers of the dataflow.

        :param field_num(tuple): tuple with x,y coordinates in integers of the field number.
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
        Puts a 'stop' field image scan on the queue, after this call, no fields can be scanned anymore. A new mega
        field can be started. The call triggers the post processing process to generate and offload additional zoom
        levels.
        """
        self.acq_queue.put(("stop",))

    def cancelAcquistion(self, execution_wait=0.2):
        """
        Clears the entire queue and finished the current acquisition. Does not terminate acquisition thread.
        """
        time.sleep(0.3)  # Wait to make sure noting is being loaded on the queue
        # Clear the queue
        while True:
            try:
                self.acq_queue.get(block=False)
            except queue.Empty:
                break

        self.acq_queue.put(("stop", ))

        if execution_wait > 30:
            logging.error("Failed to cancel the acquisition. mppc is terminated.")
            self.terminate()
            raise ConnectionError("Connection quality was to low to cancel the acquisition. mppc is terminated.")

        time.sleep(execution_wait)  # Wait until finish command is executed

        if not self.acq_queue.empty():
            self.cancelAcquistion(execution_wait=execution_wait * 2)  # Let the waiting time increase

    def acquireSingleField(self, dataContent="thumbnail", field_num=(0, 0)):
        """
        Scans a single field image via the acquire thread and the acquisition queue with the appropriate metadata models.
        The function returns this image by providing a return_queue to the acquisition thread. The use of this queue
        allows the use of the timeout functionality of a queue to prevent waiting to long on a return image (
        timeout=600 seconds).

        :param dataContent (string): Can be either: "empty", "thumbnail", "full"
        :param field_num (tuple): x,y integer number, location of the field number with the metadata provided.
        :return: DA of the single field image
        """
        if dataContent not in DATA_CONTENT_TO_ASM:
            logging.warning("Incorrect dataContent provided for acquiring a single image, thumbnail is used as default "
                            "instead.")
            dataContent = "thumbnail"

        return_queue = queue.Queue()  # queue which allows to return images and be blocked when waiting on images
        mega_field_data = self._assembleMegafieldMetadata()

        self._ensure_acquisition_thread()

        self.acq_queue.put(("start", mega_field_data))
        field_data = FieldMetaData(*self.convertFieldNum2Pixels(field_num))

        self.acq_queue.put(("next", field_data, dataContent, return_queue.put))
        self.acq_queue.put(("stop",))

        return return_queue.get(timeout=600)

    def convertFieldNum2Pixels(self, field_num):
        """

        :param field_num(tuple): tuple with x,y coordinates in integers of the field number.
        :return: field number (tuple of ints)
        """
        return (field_num[0] * self._scanner.resolution.value[0],
                field_num[1] * self._scanner.resolution.value[1])

    def getTicksAcqDelay(self):
        """
        :return: Acq delay in multiple of ticks of the system clock period
        """
        return int(self.acqDelay.value / self.parent.clockPeriod.value)

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

    def _setAcqDelay(self, delay):
        """
        Setter which prevents the mppc detector from recording before the ebeam scanner has started for the delay
        between starting the scanner and starting the recording.

        :param delay (tuple): x,y seconds
        :return (tuple): x,y seconds
        """
        # Check if detector can record images before ebeam scanner has started to scan.
        if delay >= self._scanner.scanDelay.value[0]:
            return delay
        else:
            # Change Acq Delay value so that the mppc does not start recording before the ebeam scanner has started to
            # scan.
            logging.warning("Detector cannot record images before ebeam scanner has started to scan.\n"
                            "Detector needs to start after scanner. The acquisition delay is adjusted accordingly.")
            delay = self._scanner.scanDelay.value[0]
            logging.info("The adjusted acquisition delay used is %s in the eBeamScanner and the scan delay for the "
                         "mppc is %s" % (delay, self._scanner.scanDelay.value[0]))
            return delay

    def _setFilename(self, file_name):
        """
        Check if filename complies with the set of allowed characters.
        :param file_name: (str) The requested filename for the image data to be acquired.
        :return: (str) The set filename for the image data to be acquired.
        """
        ASM_FILE_CHARS = r'[^a-z0-9_()-]'
        if re.search(ASM_FILE_CHARS, file_name):
            raise ValueError("Filename contains invalid characters. Only the following characters are allowed: "
                             "'%s'. Please choose a new filename." % ASM_FILE_CHARS[2:-1])
        else:
            return file_name

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
                            (row, column, int, int, type(eff_origin[0]), type(eff_origin[1])))

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


class ASMDataFlow(model.DataFlow):
    """
    Represents the acquisition on the ASM
    """

    def __init__(self, mppc):
        super(ASMDataFlow, self).__init__(self)

        # Make mppc object an private attribute (which is used to call the start, next, stop and get methods)
        self._mppc = mppc

    def start_generate(self):
        """
        Start the dataflow using the provided function. The appropriate settings are retrieved via the VA's of the
        each component
        """
        self._mppc.startAcquisition()

    def next(self, field_num):
        """
        Acquire the next field image using the provided function.
        :param field_num (tuple): tuple with x,y coordinates in integers of the field number.
        """
        self._mppc.getNextField(field_num)

    def stop_generate(self):
        """
        Stop the dataflow using the provided function.
        """
        self._mppc.stopAcquisition()

    def get(self, *args, **kwargs):
        """
        Acquire a single field, can only be called if no other acquisition is active.
        :return: (DataArray)
        """
        if self._count_listeners() < 1:
            # Acquire and return received image
            image = self._mppc.acquireSingleField(*args, **kwargs)
            return image

        else:
            logging.error("There is already an acquisition on going with %s listeners subscribed, first cancel/stop "
                          "current running acquisition to acquire a single field-image" % self._count_listeners())
            raise Exception("There is already an acquisition on going with %s listeners subscribed, first cancel/stop "
                            "current running acquisition to acquire a single field-image" % self._count_listeners())


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
                self._emptyResponse(url, status_code, reason, expected_status,)

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

    def _emptyResponse(self, url, status_code, reason, expected_status,):
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
