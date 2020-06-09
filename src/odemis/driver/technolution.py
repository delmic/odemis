# -*- coding: utf-8 -*-
'''
Created on 11 May 2020

@author: Sabrina Rossberger, Kornee Kleijwegt

Copyright © 2019-2020 Kornee Kleijwegt, Delmic

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
# Driver/wrapper for the ASP API in Odemis which can connect Odemis to the ASM server of Technolution of the
# multi-beam project
from __future__ import division

import json
import logging
import queue
import re
import threading
import time
from datetime import datetime
from urllib.parse import urlparse, urlunparse

import numpy
from odemis import model
from requests import Session

# TODO K.K. will change package/folder name for next simulator
from src.openapi_server.models.cell_parameters import CellParameters
from src.openapi_server.models.field_meta_data import FieldMetaData
from src.openapi_server.models.mega_field_meta_data import MegaFieldMetaData

DATA_CONTENT_TO_ASM = {"empty": None, "thumbnail": True, "full": False}


class AcquisitionServer(model.HwComponent):
    """
    Component representing the Acquisition server module which is connected via the ASM API. This module controls the
    camera (mppc sensor) for acquiring the image data. It is also connected to the Scan and Acquisition module (SAM),
    which triggers the scanner on the SEM to move the electron beam. Moreover it controls the de-scanner which counter
    scans the scanner movement to ensure that the collected signal always hits the center of each mppc cell on the
    detector.
    """

    def __init__(self, name, role, server_url, children={}, **kwargs):
        """
        Initialize the Acquisition server and the connection with the ASM API.

        :param name (str): Name of the component
        :param role (str): Role of the component
        :param server_url (str): URL of the ASM API
        :param children (dict): dictonary containing children and there respetive configuration
        :param kwargs:
        """

        super(AcquisitionServer, self).__init__(name, role, **kwargs)

        self._server_url = server_url
        self._session = Session()

        # TODO K.K. Set external storage and connection

        # Stop any acquisition if already one was in progress
        self.ASM_API_Post_Call("/scan/finish_mega_field", 204)

        # Order of initialisation matters due to dependency of VA's and variables in between children.
        try:
            ckwargs = children["MirrorDescanner"]
        except Exception:
            raise ValueError("Required child MirrorDescanner not provided")
        self._mirror_descanner = MirrorDescanner(parent=self, **ckwargs)
        self.children.value.add(self._mirror_descanner)

        try:
            ckwargs = children["EBeamScanner"]
        except Exception:
            raise ValueError("Required child EBeamScanner not provided")
        self._ebeam_scanner = EBeamScanner(parent=self, **ckwargs)
        self.children.value.add(self._ebeam_scanner)

        try:
            ckwargs = children["MPPC"]
        except Exception:
            raise ValueError("Required child MPPC not provided")
        self._mppc = MPPC(parent=self, **ckwargs)
        self.children.value.add(self._mppc)

    def terminate(self):
        # terminate children
        for child in self.children.value:
            child.terminate()

    def ASM_API_Get_Call(self, url, expected_status, data=None, raw_response=False, timeout=600):
        """
        Call to the ASM API to get data from the ASM API

        :param url (str): url of the command, server part is defined in object variable self._server_url
        :param expected_status (int): expected feedback of server for a positive call
        :param data: data (request body) added to the call to the ASM server (mega field metadata, scan location etc.)
        :param raw_response (bool): specified the format of the structure returned
        :param timeout (int): [s] if within this period no bytes are received an timeout exception is raised
        :return: content dictionary(getting), or entire response (raw_response=True)
        """
        logging.debug("Executing: %s" % url)
        resp = self._session.get(self._server_url + url, json=data, timeout=timeout)

        if resp.status_code != expected_status:
            raise AsmApiException(url, resp, expected_status)

        logging.debug("Call to %s went fine, no problems occured\n" % url)
        if raw_response:
            return resp
        else:
            return json.loads(resp.content)

    def ASM_API_Post_Call(self, url, expected_status, data=None, raw_response=False, timeout=600):
        """
        Call to the ASM API to post data to the ASM API

        :param url (str): url of the command, server part is defined in object variable self._server_url
        :param expected_status (int): expected feedback of server for a positive call
        :param data: data (request body) added to the call to the ASM server (mega field metadata, scan location etc.)
        :param raw_response (bool): specified the format of the structure returned
        :param timeout (int): [s] if within this period no bytes are received an timeout exception is raised
        :return: status_code(int) or entire response (raw_response=True)
        """
        logging.debug("Executing: %s" % url)
        resp = self._session.post(self._server_url + url, json=data, timeout=timeout)

        if resp.status_code != expected_status:
            raise AsmApiException(url, resp, expected_status)

        logging.debug("Call to %s went fine, no problems occurred\n" % url)
        if raw_response:
            return resp
        else:
            return resp.status_code


class EBeamScanner(model.Emitter):
    """
    Represents the e-beam scanner of a single field image.
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Initialize the e-beam scanner of a single field image..

        :param name:
        :param role:
        :param parent:
        :param kwargs:
        """
        super(EBeamScanner, self).__init__(name, role, parent=parent, **kwargs)

        clockFrequencyData = self.parent.ASM_API_Get_Call("/scan/clock_frequency", 200)
        # Check if clockFrequencyData holds the proper key
        if 'frequency' not in clockFrequencyData:
            raise IOError("Could not obtain clock frequency, received data does not hold the proper key")
        clockFrequency = clockFrequencyData['frequency']

        self.clockPeriod = model.FloatVA(1 / clockFrequency, unit='s')
        self._shape = (6400, 6400)
        # The resolution min/maximum are derived from the effective cell size restriction defined in the API
        self.resolution = model.ResolutionVA((6400, 6400), ((10, 10), (1000 * 8, 1000 * 8)))
        self.dwellTime = model.FloatContinuous(self.clockPeriod.value, (max(self.clockPeriod.value, 400e-9), 100.0),
                                               unit='s')
        self.pixelSize = model.TupleContinuous((4e-9, 4e-9), range=((1e-9, 1e-9), (1e-3, 1e-3)), unit='m',
                                               setter=self._setPixelSize)
        self.rotation = model.FloatContinuous(0, range=(0, 2 * numpy.pi), unit='rad')
        self.scanFlyback = model.FloatContinuous(0, range=(0, 100), unit='s')
        self.scanOffset = model.TupleContinuous((0.0, 0.0), range=((-10.0, -10.0), (10.0, 10.0)), unit='V')
        self.scanGain = model.TupleContinuous((0.0, 0.0), range=((-10.0, -10.0), (10.0, 10.0)), unit='V')
        self.scanDelay = model.TupleContinuous((0, 0), range=((0, 0), (100000, 100000)), unit='s',
                                               setter=self._setScanDelay)

        self._metadata[model.MD_PIXEL_SIZE] = self.pixelSize.value
        self._metadata[model.MD_DWELL_TIME] = self.dwellTime.value


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
            return (pixelSize[0], pixelSize[0])

    def _setScanDelay(self, scanDelay):
        """
        Setter which checks if detector can record images before ebeam scanner has started to scan.

        :param pixelSize (tuple):
        :return (tuple):
        """
        # Check if detector can record images before ebeam scanner has started to scan.
        if not (hasattr(self.parent, "_mppc")) or self.parent._mppc.acqDelay.value >= scanDelay[0]:
            return scanDelay
        else:
            # Change values so that 'self.parent._mppc. acqDelay.value - self.scanDelay.value[0]' has a positive result
            logging.warning("Detector cannot record images before ebeam scanner has started to scan.\n"
                            "Detector needs to start after scanner.")
            logging.info("The entered acquisition delay is %s in the eBeamScanner and the scan delay in the MPPC is "
                         "%s" % (self.parent._mppc.acqDelay.value, scanDelay[0]))
            return self.scanDelay.value


class MirrorDescanner(model.Emitter):
    """
    Represents the Mirror de scanner of a single field image which counter scans the scanner movement to ensure that
    the collected signal always hits the center of each mppc cell on the detector.
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Initializes Mirror de scanner of a single field image which counter scans the scanner movement to ensure
        that the collected signal always hits the center of each mppc cell on the detector.

        :param name:
        :param role:
        :param parent:
        :param kwargs:
        """
        super(MirrorDescanner, self).__init__(name, role, parent=parent, **kwargs)

        self.rotation = model.FloatContinuous(0, range=(0, 2 * numpy.pi), unit='rad')
        self.scanOffset = model.TupleContinuous((0.0, 0.0), range=((-10.0, -10.0), (10.0, 10.0)), unit='V')
        self.scanGain = model.TupleContinuous((0.0, 0.0), range=((-10.0, -10.0), (10.0, 10.0)), unit='V')


class MPPC(model.Detector):
    """
    Represents the camera (mppc sensor) for acquiring the image data.
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Initializes the camera (mppc sensor) for acquiring the image data.

        :param name:
        :param role:
        :param parent:
        :param kwargs:
        """
        super(MPPC, self).__init__(name, role, parent=parent, **kwargs)

        self._server_URL = self.parent._server_url

        self._shape = (8, 8)
        self.filename = model.StringVA(time.strftime("default--%Y-%m-%d-%H-%M-%S"), setter=self._setFilename)
        self.dataContent = model.StringEnumerated('empty', DATA_CONTENT_TO_ASM.keys())
        # NOTE: Do not write real username/password here since this is published on github in plaintext!
        self.externalStorageURL = model.VigilantAttribute(urlparse('ftp://username:password@example.com/Pictures'),
                                                          setter=self._setURL)
        self.acqDelay = model.FloatContinuous(2.0, range=(0, 100000), unit='s', setter=self._setAcqDelay)

        # Cell acquisition parameters
        self.cellTranslation = model.ListVA([[[50, 50]] * self._shape[0]] * self._shape[1],
                                            setter=self._setCellTranslation)
        self.cellDarkOffset = model.ListVA([[0] * self._shape[0]] * self._shape[1], setter=self._setcellDarkOffset)
        self.cellDigitalGain = model.ListVA([[1.2] * self._shape[0]] * self._shape[1], setter=self._setcellDigitalGain)
        self.cellCompleteResolution = model.ResolutionVA((800, 800), ((10, 10), (1000, 1000)))

        # TODO K.K. pass right metadata from new simulator

        # Setup hw and sw version
        # TODO make call set_system_sw_name to new simulator (if implemented)
        self._swVersion = self._swVersion + ", " + "PUT NEW SIMULATOR DATA HERE"
        self._hwVersion = self._hwVersion + ", " + "PUT NEW SIMULATOR DATA HERE"

        # Gather metadata from all related HW components and own _meta_data
        self.md_devices = [self._metadata, self.parent._mirror_descanner._metadata,
                           self.parent._ebeam_scanner._metadata]
        self._metadata[model.MD_HW_NAME] = "MPPC" + "/" + name
        self._metadata[model.MD_SW_VERSION] = self._swVersion
        self._metadata[model.MD_HW_VERSION] = self._hwVersion

        # Initialize acquisition processes
        self.acq_queue = queue.Queue()  # acquisition queue with commands of actions that need to be executed.
        self._acq_thread = threading.Thread(target=self._acquire, name="acquisition thread")
        self._acq_thread.deamon = False
        self._acq_thread.start()

        self.data = ASMDataFlow(self.start_acquisition, self.get_next_field,
                                self.stop_acquisition, self.acquire_single_field)

    def terminate(self):
        """
        Terminate acquisition thread and empty the queue
        """
        super(MPPC, self).terminate()

        # Clear the queue
        while True:
            try:
                self.acq_queue.get(block=False)
            except queue.Empty:
                break

        self.acq_queue.put(("terminate", None))

    def _assemble_megafield_metadata(self):
        """
        Gather all the megafield metadata from the VA's and convert into a MegaFieldMetaData Model using the ASM API

        :return: MegaFieldMetaData Model of the ASM API
        """
        cellTranslation = sum(self.cellTranslation.value, [])
        celldarkOffset = sum(self.cellDarkOffset.value, [])
        celldigitalGain = sum(self.cellDigitalGain.value, [])
        eff_cell_size = (int(self.parent._ebeam_scanner.resolution.value[0] / self._shape[0]),
                         int(self.parent._ebeam_scanner.resolution.value[1] / self._shape[1]))

        megafield_metadata = \
            MegaFieldMetaData(
                    mega_field_id=self.filename.value,
                    storage_directory=self.externalStorageURL.value.path,
                    # Convert pixels size from meters to nanometers
                    pixel_size=int(self.parent._ebeam_scanner.pixelSize.value[0] * 1e9),
                    dwell_time=int(self.parent._ebeam_scanner.dwellTime.value /
                                   self.parent._ebeam_scanner.clockPeriod.value),
                    x_cell_size=self.cellCompleteResolution.value[0],
                    x_eff_cell_size=eff_cell_size[0],
                    y_cell_size=self.cellCompleteResolution.value[1],
                    y_eff_cell_size=eff_cell_size[1],
                    cell_parameters=[CellParameters(translation[0], translation[1], darkOffset, digitalGain)
                                     for translation, darkOffset, digitalGain in
                                     zip(cellTranslation, celldarkOffset, celldigitalGain)],
                    x_scan_to_acq_delay=int(self.acqDelay.value - self.parent._ebeam_scanner.scanDelay.value[0]),
                    x_scan_delay=self.parent._ebeam_scanner.scanDelay.value[0],
                    y_prescan_lines=self.parent._ebeam_scanner.scanDelay.value[1],
                    flyback_time=int(self.parent._ebeam_scanner.scanFlyback.value /
                                     self.parent._ebeam_scanner.clockPeriod.value),
                    x_scan_offset=self.parent._ebeam_scanner.scanOffset.value[0],
                    y_scan_offset=self.parent._ebeam_scanner.scanOffset.value[1],
                    x_scan_gain=self.parent._ebeam_scanner.scanGain.value[0],
                    y_scan_gain=self.parent._ebeam_scanner.scanGain.value[1],
                    x_descan_gain=self.parent._mirror_descanner.scanGain.value[0],
                    y_descan_gain=self.parent._mirror_descanner.scanGain.value[1],
                    x_descan_offset=self.parent._mirror_descanner.scanOffset.value[0],
                    y_descan_offset=self.parent._mirror_descanner.scanOffset.value[1],
                    scan_rotation=self.parent._ebeam_scanner.rotation.value,
                    descan_rotation=self.parent._mirror_descanner.rotation.value,
            )

        return megafield_metadata

    def _acquire(self):
        """
        Acquisition thread takes input from the self.acq_queue which holds a command ('start', 'next', 'stop',
        'terminate') and extra arguments (MegaFieldMetaData Model or FieldMetaData Model and the notifier function to
        which any return will be redirected)
        """

        try:
            acquisition_in_progress = None  # To prevent that acquisitions mix up, or stop the acquisition twice.
            tnext = 0

            while True:
                # Wait until a message is available
                command, *args = self.acq_queue.get(block=True)

                if command == "start":
                    if acquisition_in_progress:
                        logging.warning("ASM acquisition was already at status '%s'" % command)
                        continue

                    acquisition_in_progress = True
                    megafield_metadata = args[0]
                    self.parent.ASM_API_Post_Call("/scan/start_mega_field", 204, megafield_metadata.to_dict())

                elif command == "next":
                    if not acquisition_in_progress:
                        logging.warning("Start ASM acquisition before taking field images")
                        continue

                    field_data = args[0]  # Field metadata for the specific position of the field to scan
                    dataContent = args[1]  # Specifies the type of image to return (empty, thumbnail or full)
                    notifier_func = args[2]  # Return function (usually, Dataflow.notify or acquire_single_filed queue)

                    self.parent.ASM_API_Post_Call("/scan/scan_field", 204, field_data.to_dict())

                    # TODO add metadata from queue/ASM info mergaMetadata function so that metadata is correct.
                    if DATA_CONTENT_TO_ASM[dataContent] == None:
                        da = model.DataArray(numpy.array([[0]], dtype=numpy.uint8), metadata=self._mergeMetadata())
                    else:
                        # TODO add functionality for getting a full and thumbnail image if simulator is updated
                        logging.error("Option %s not yet implemented" % args[1])
                        img = self.parent.ASM_API_Get_Call("/scan/field", 200,
                                                           (field_data.position_x,
                                                            field_data.position_y,
                                                            DATA_CONTENT_TO_ASM[dataContent].to_dict()))
                        da = model.DataArray(img, metadata=self._mergeMetadata())

                    notifier_func(da)

                elif command == "stop":
                    if not acquisition_in_progress:
                        logging.warning("ASM acquisition was already at status '%s'" % command)
                        continue

                    acquisition_in_progress = False
                    self.parent.ASM_API_Post_Call("/scan/finish_mega_field", 204)

                elif command == "terminate":
                    acquisition_in_progress = None
                    raise TerminationRequested()

                else:
                    logging.error("Received invalid command '%s' is skipped" % command)
                    raise ValueError

                tnow = time.time()
                # sleep a bit to avoid refreshing too fast
                tsleep = tnext - tnow
                if tsleep > 0.01:
                    time.sleep(tsleep)

                tnext = time.time() + 0.2  # max 5 Hz

        except TerminationRequested:
            logging.info("Terminating acquisition")

        except Exception:
            if command is not None:
                logging.exception("Last message was not executed, should have perfomed action: '%s'\n"
                                  "Reinitialize and restart the acquisition" % command)
        finally:
            self.parent.ASM_API_Post_Call("/scan/finish_mega_field", 204)
            logging.debug("Acquisition thread ended")

    def start_acquisition(self):
        """
        Put a the command 'start' mega field scan on the queue with the appropriate MegaFieldMeta Model of the mega
        field image to be scannend. All subsequent calls to scan_field will use a part of this meta data to store the image
        data until the stop command is executed.
        """
        if not self._acq_thread or not self._acq_thread.is_alive():
            logging.info('Starting acquisition thread and clearing remainder of the old queue')

            # Clear the queue
            while True:
                try:
                    self.acq_queue.get(block=False)
                except queue.Empty:
                    break

            self._acq_thread = threading.Thread(target=self._acquire,
                                                name="acquisition thread")
            self._acq_thread.deamon = False
            self._acq_thread.start()

        megafield_metadata = self._assemble_megafield_metadata()
        self.acq_queue.put(("start", megafield_metadata))

    def get_next_field(self, field_num):
        '''
        Put a the command 'next' field image scan on the queue with the appropriate field meta data model of the field
        image to be scannend. Can only be executed if it proceeded by a 'start' mega field scan command on the queue.
        As notifier function the dataflow.notify is given which means the returned image will be redirected to this
        function.

        :param field_num: x,y
        '''
        field_data = FieldMetaData(*self.convert_field_num2pixels(field_num))
        self.acq_queue.put(("next", field_data, self.dataContent.value, self.data.notify))

    def stop_acquisition(self):
        """
        Puts a 'stop' field image scan on the queue, after this call, no fields can be scanned anymore. A new mega
        field can be started. The call triggers the post prosessing process to generate and offload additional zoom
        levels
        """
        self.acq_queue.put(("stop",))

    def acquire_single_field(self, field_num=(0, 0)):
        """
        Puts a the series 'start','next','stop' commands on the queue with the appropriate metadata models and
        scans a single field image. By providing as notifier function a return_queue the image can be returned. The
        use of the queue allows the use of the timeout functionality

        :param field_num:
        :return: DA of the single field image
        """

        return_queue = queue.Queue()  # queue which allows to return images and be blocked when waiting on images
        mega_field_data = self._assemble_megafield_metadata()

        self.acq_queue.put(("start", mega_field_data))
        field_data = FieldMetaData(*self.convert_field_num2pixels(field_num))

        self.acq_queue.put(("next", field_data, self.dataContent.value, return_queue.put))
        self.acq_queue.put(("stop",))

        return return_queue.get(timeout=600)

    def convert_field_num2pixels(self, field_num):
        return (field_num[0] * self.parent._ebeam_scanner.resolution.value[0],
                field_num[1] * self.parent._ebeam_scanner.resolution.value[1])

    def _mergeMetadata(self):
        """
        Create dict containing all metadata from siblings and own metadata
        """
        md = {}
        self._metadata[model.MD_ACQ_DATE] = time.time()  # Time since Epoch

        for md_dev in self.md_devices:
            for key in md_dev.keys():
                if key not in md:
                    md[key] = md_dev[key]
                elif key in (model.MD_HW_NAME, model.MD_HW_VERSION, model.MD_SW_VERSION):
                    # TODO update to add metadata call to sam_firmware_version, sam_service_version,
                    #  sam_rootfs_version,  asm_service_version
                    md[key] = ", ".join([md[key], md_dev[key]])
        return md

    def _setAcqDelay(self, delay):
        """
        Setter which checks if detector can record images before ebeam scanner has started to scan.

        :param delay (tuple):
        :return (tuple):
        """
        # Check if detector can record images before ebeam scanner has started to scan.
        if delay >= self.parent._ebeam_scanner.scanDelay.value[0]:
            return delay
        else:
            # Change values so that 'self.acqDelay.value - self.parent._ebeam_scanner.scanDelay.value[0]' has a positive result
            logging.warning("Detector cannot record images before ebeam scanner has started to scan.\n"
                            "Detector needs to start after scanner.")
            logging.info("The entered acquisition delay is %s in the eBeamScanner and the scan delay in the MPPC is "
                         "%s" % (delay, self.parent._ebeam_scanner.scanDelay.value[0]))
            return self.acqDelay.value

    def _setFilename(self, file_name):
        """
        Check if filename complies with set allowed characters
        :param file_name:
        :return:
        """
        ASM_FILE_ALLOWED_CHARS = r'[^a-z0-9_()-]'
        search = re.compile(ASM_FILE_ALLOWED_CHARS).search
        if not bool(search(file_name)):
            return file_name
        else:
            logging.warning("File_name contains invalid characters, file_name remains unchanged (only the characters "
                            "'%s' are allowed)" % ASM_FILE_ALLOWED_CHARS[2:-1])
            return self.filename.value

    def _setURL(self, url_parser):
        """
        Setter which checks for correctness of FTP url_parser and otherwise returns old value.

        :param url_parser: e.g. ftp://username:password@example.com
        :return: correct ftp url_parser
        """
        ASM_GENERAL_ALLOWED_CHARS = r'[^A-Za-z0-9/_()-:@]'
        ASM_USER_ALLOWED_CHARS = r'[^A-Za-z0-9]'
        ASM_PASSWORD_ALLOWED_CHARS = r'[^A-Za-z0-9]'
        ASM_HOST_ALLOWED_CHARS = r'[^A-Za-z0-9.]'
        ASM_PATH_ALLOWED_CHARS = r'[^A-Za-z0-9/_()-]'

        def checkCharacters(input, allowed_characters):
            """
            Check if input complies with allowed characters
            :param input (sting): input string
            :param allowed_characters: allowed_characters for different parts of input string
            :return (boolean) True if passes test on allowed_characters
            """
            search = re.compile(allowed_characters).search
            if not bool(search(input)):
                return True
            else:
                return False

        # Perform general check on valid characters (parses works incorrectly for some invalid characters
        if not checkCharacters(urlunparse(url_parser), ASM_GENERAL_ALLOWED_CHARS):
            logging.warning("Invalid character in ftp url is provided, allowed characters are %s in the form:: "
                            "'ftp://username:password@host_example.com/path/to/Pictures'\n"
                            "(Only use the @ to separate the password and the host." % ASM_GENERAL_ALLOWED_CHARS[2:-1])
            return self.externalStorageURL.value

        # Perform detailed checks on input
        if url_parser.scheme != 'ftp' \
                or not url_parser.scheme or not url_parser.username or not url_parser.password \
                or not url_parser.hostname or not url_parser.path:
            # Check both the scheme as well if all sub-elements are non-empty
            # Note that if an extra @ is used (e.g. in the password) the parser works incorrectly and sub-elements
            # are empty after splitting the url input
            logging.warning("Incorrect ftp url is provided, please use form: "
                            "'ftp://username:password@host_example.com/path/to/Pictures'\n"
                            "(Only use the @ to separate the password and the host.")
            return self.externalStorageURL.value

        elif not checkCharacters(url_parser.username, ASM_USER_ALLOWED_CHARS):
            logging.warning(
                    "Username contains invalid characters, username remains unchanged "
                    "(only the characters '%s' are allowed)" % ASM_USER_ALLOWED_CHARS[2:-1])
            return self.externalStorageURL.value

        elif not checkCharacters(url_parser.password, ASM_PASSWORD_ALLOWED_CHARS):
            logging.warning(
                    "Password contains invalid characters, password remains unchanged "
                    "(only the characters '%s' are allowed)" % ASM_PASSWORD_ALLOWED_CHARS[2:-1])
            return self.externalStorageURL.value

        elif not checkCharacters(url_parser.hostname, ASM_HOST_ALLOWED_CHARS):
            logging.warning(
                    "Host contains invalid characters, host remains unchanged "
                    "(only the characters '%s' are allowed)" % ASM_HOST_ALLOWED_CHARS[2:-1])
            return self.externalStorageURL.value

        elif not checkCharacters(url_parser.path, ASM_PATH_ALLOWED_CHARS):
            logging.warning("Path on ftp server contains invalid characters, path remains unchanged "
                            "(only the characters '%s' are allowed)" % ASM_PATH_ALLOWED_CHARS[2:-1])
        else:
            return url_parser

    def _setCellTranslation(self, cellTranslation):
        if len(cellTranslation) != self._shape[0]:
            logging.warning("An incorrect shape of the cell translation parameters is provided.\n "
                            "Please change the shape of the cell translation parameters according to the shape of the "
                            "MPPC detector.\n "
                            "Cell translation parameters remain unchanged.")
            return self.cellTranslation.value

        for row, cellTranslationRow in enumerate(cellTranslation):
            if len(cellTranslationRow) != self._shape[1]:
                logging.warning("An incorrect shape of the cell translation parameters is provided.\n"
                                "Please change the shape of the cellTranslation parameters according to the shape of "
                                "the MPPC detector.\n "
                                "Cell translation parameters remain unchanged.")
                return self.cellTranslation.value

            for column, eff_origin in enumerate(cellTranslationRow):
                if len(eff_origin) != 2:
                    logging.warning("Incorrect cell translation parameters provided, wrong number of coordinates for "
                                    "cell (%s, %s) are provided.\n"
                                    "Please provide an 'x effective origin' and an 'y effective origin' for this cell "
                                    "image.\n "
                                    "Cell translation parameters remain unchanged." %
                                    (row, column))
                    return self.cellTranslation.value

                if not isinstance(eff_origin[0], int) or not isinstance(eff_origin[1], int):
                    logging.warning("An incorrect type is used for the cell translation coordinates of cell (%s, %s).\n"
                                    "Please use type integer for both 'x effective origin' and and 'y effective "
                                    "origin' for this cell image.\n"
                                    "Type expected is: '(%s, %s)' type received '(%s, %s)'\n"
                                    "Cell translation parameters remain unchanged." %
                                    (row, column, int, int, type(eff_origin[0]), type(eff_origin[1])))
                    return self.cellTranslation.value
                elif eff_origin[0] < 0 or eff_origin[1] < 0:
                    logging.warning("Please use a minimum of 0 cell translation coordinates of cell (%s, %s).\n"
                                    "Cell translation parameters remain unchanged." %
                                    (row, column))
                    return self.cellTranslation.value

        return cellTranslation


    def _setcellDigitalGain(self, cellDigitalGain):
        if len(cellDigitalGain) != self._shape[0]:
            logging.warning("An incorrect shape of the digital gain parameters is provided. Please change the "
                            "shape of the digital gain parameters according to the shape of the MPPC detector.\n"
                            "Digital gain parameters value remain unchanged.")
            return self.cellDigitalGain.value

        for row, cellDigitalGain_row in enumerate(cellDigitalGain):
            if len(cellDigitalGain_row) != self._shape[1]:
                logging.warning("An incorrect shape of the digital gain parameters is provided.\n"
                                "Please change the shape of the digital gain parameters according to the shape of the "
                                "MPPC detector.\n "
                                "Digital gain parameters value remain unchanged.")
                return self.cellDigitalGain.value

            for column, DigitalGain in enumerate(cellDigitalGain_row):
                if not isinstance(DigitalGain, float):
                    logging.warning("An incorrect type is used for the digital gain parameters of cell (%s, %s).\n"
                                    "Please use type float for digital gain parameters for this cell image.\n"
                                    "Type expected is: '%s' type received '%s' \n"
                                    "Digital gain parameters value remain unchanged." %
                                    (row, column, float, type(DigitalGain)))
                    return self.cellDigitalGain.value
                elif DigitalGain < 0:
                    logging.warning("Please use a minimum of 0 for digital gain parameters of cell image (%s, %s).\n"
                                    "Digital gain parameters value remain unchanged." %
                                    (row, column))
                    return self.cellDigitalGain.value

        return cellDigitalGain

    def _setcellDarkOffset(self, cellDarkOffset):
        if len(cellDarkOffset) != self._shape[0]:
            logging.warning("An incorrect shape of the dark offset parameters is provided.\n"
                            "Please change the shape of the dark offset parameters according to the shape of the MPPC "
                            "detector.\n "
                            "Dark offset parameters value remain unchanged.")
            return self.cellDarkOffset.value

        for row, cellDarkOffsetRow in enumerate(cellDarkOffset):
            if len(cellDarkOffsetRow) != self._shape[1]:
                logging.warning("An incorrect shape of the dark offset parameters is provided.\n"
                                "Please change the shape of the dark offset parameters according to the shape of the "
                                "MPPC detector.\n "
                                "Dark offset parameters value remain unchanged.")
                return self.cellDarkOffset.value

            for column, DarkOffset in enumerate(cellDarkOffsetRow):
                if not isinstance(DarkOffset, int):
                    logging.warning("An incorrect type is used for the dark offset parameter of cell (%s, "
                                    "%s). \n"
                                    "Please use type integer for dark offset for this cell image.\n"
                                    "Type expected is: '%s' type received '%s' \n"
                                    "Dark offset parameters value remain unchanged." %
                                    (row, column, float, type(DarkOffset)))
                    return self.cellDarkOffset.value
                elif DarkOffset < 0:
                    logging.warning("Please use a minimum of 0 for dark offset parameters of cell image (%s, %s).\n"
                                    "Dark offset parameters value remain unchanged." %
                                    (row, column))
                    return self.cellDarkOffset.value

        return cellDarkOffset


class ASMDataFlow(model.DataFlow):
    """
    Represents the acquisition on the ASM
    """

    def __init__(self, start_func, next_func, stop_func, get_func):
        super(ASMDataFlow, self).__init__(self)

        self._start = start_func
        self._next = next_func
        self._stop = stop_func
        self._get = get_func

    def start_generate(self):
        """
        Start the dataflow using the provided function. The approriate settings are retrieved via the VA's of the
        each component
        """
        self._start()

    def next(self, field_num):
        """
        Acquire the next field image using the provided function.
        :param field_num (tuple): tuple with x,y coordinates in integers of the field 
        :return: 
        """
        self._next(field_num)

    def stop_generate(self):
        """
        Stop the dataflow using the provided function.
        """
        self._stop()

    def get(self):
        """
        Acquire a single field, can only be called if no other acquisition is active.
        :return:
        """
        if self._count_listeners() < 1:
            # Acquire and return received image
            image = self._get()
            return image

        else:
            logging.error("There is already an acquisition on going with %s listeners subscribed, first cancel/stop "
                          "current running acquisition to acquire a single field-image" % self._count_listeners())
            raise Exception("There is already an acquisition on going with %s listeners subscribed, first cancel/stop "
                            "current running acquisition to acquire a single field-image" % self._count_listeners())


class AsmApiException(Exception):
    """
    Exception for and error in the ASM API call
    """

    def __init__(self, url, response, expected_status):
        """
        Initializes exception object which defines a message based on the response available by trying to display as
        much relevant information as possible.

        :param url: URL of the call tried which was tried to make
        :param response: full/raw response from the ASM API
        :param expected_status: the expected status code
        """
        self.url = url
        self.status_code = response.status_code
        self.reason = response.reason
        self.expected_status = expected_status

        try:
            self.content_translated = json.loads(response.content)
            if 'status_code' in self.content_translated and 'message' in self.content_translated:
                self.error_message_response(self.content_translated['status_code'], self.content_translated['message'])
        except:
            if hasattr(response, "text"):
                self.error_message_response(self.status_code, response.text)
            elif hasattr(response, "content"):
                self.error_message_response(self.status_code, response.content)
            else:
                self.empty_response()

    def __str__(self):
        return self._error

    def error_message_response(self, error_code, error_message):
        # Received bad response with an error message for the user
        self._error = ("\n"
                       "Call to %s received unexpected answer.\n"
                       "Got status code '%s' because of the reason '%s', but expected status code was'%s'\n"
                       "Error code '%s' with the message: '%s'\n" %
                       (self.url,
                        self.status_code, self.reason, self.expected_status,
                        error_code, error_message))

    def empty_response(self):
        # Received bad response and without an error message
        self._error = ("\n"
                       "Call to %s received unexpected answer.\n"
                       "Got status code '%s' because of the reason '%s', but expected '%s'\n" %
                       (self.url,
                        self.status_code, self.reason, self.expected_status))


class TerminationRequested(Exception):
    """
    Acquisition termination requested.
    """
    pass


if __name__ == '__main__':
    # TODO K.K. remove this part after new simulator, test and code are fully implemented!
    import requests

    # Variable to differentiate "get" and "post" requests to the ASM server
    _METHOD_GET = 1
    _METHOD_POST = 2


    def ASMAPICall(url, method, expected_status, data=None, raw_response=False, timeout=600):
        """

        :param url: url of the command, server part is defined in global variable url
        :param method: getting or posting via global variables _METHOD_GET/_METHOD_POST
        :param expected_status: expected feedback of server for a positive call
        :param data: data (request body) added to the call to the ASM server (mega field metadata, scan location etc.)
        :param raw_response: specified the format of the structure returned
        :param timeout: [s] if within this period no bytes are received an timeout exception is raised
        :return: status_code(posting), content dictionary(getting), or entire response (raw_response=True)
        """
        logging.debug("Executing: %s" % url)
        if method == _METHOD_GET:
            resp = requests.get(url, json=data, timeout=timeout)
        elif method == _METHOD_POST:
            resp = requests.post(url, json=data, timeout=timeout)

        if resp.status_code != expected_status:
            raise AsmApiException(url, resp, expected_status)

        logging.debug("Call to %s went fine, no problems occured\n" % url)

        if raw_response:
            return resp
        elif method == _METHOD_POST:
            return resp.status_code
        elif method == _METHOD_GET:
            return json.loads(resp.content)


    MEGA_FIELD_DATA = MegaFieldMetaData(
            mega_field_id=datetime.now().strftime("megafield_%Y%m%d-%H%M%S"),
            pixel_size=4,
            dwell_time=2,
            x_cell_size=900,
            x_eff_cell_size=800,
            y_cell_size=900,
            y_eff_cell_size=800,
            cell_parameters=[CellParameters(50, 50, 0, 1.2)] * 64,
            x_scan_to_acq_delay=2,
            x_scan_delay=0,
            flyback_time=0,
            x_scan_offset=0,
            y_scan_offset=0,
            x_scan_gain=0,
            y_scan_gain=0,
            x_descan_gain=0,
            y_descan_gain=0,
            x_descan_offset=0,
            y_descan_offset=0,
            scan_rotation=0,
            descan_rotation=0,
            y_prescan_lines=0,
    )

    server_URL = "http://localhost:8080/v1"
    ASMAPICall(server_URL + "/scan/clock_frequency", _METHOD_GET, 200)
    ASMAPICall(server_URL + "/scan/finish_mega_field", _METHOD_POST, 204)
    # ASMAPICall(_server_url + "/scan/start_mega_field", _METHOD_POST, 204, MEGA_FIELD_DATA.to_dict())
    # scan_body = FieldMetaData(position_x=0, position_y=0)
    # ASMAPICall(_server_url + "/scan/scan_field", _METHOD_POST, 204, scan_body.to_dict())
    # scan_body = FieldMetaData(position_x=6400, position_y=6400)
    # scan_body = FieldMetaData(position_x=6400*3, position_y=6400*3)
    # ASMAPICall(_server_url + "/scan/scan_field", _METHOD_POST, 204, scan_body.to_dict())
    # ASMAPICall(_server_url + "/scan/finish_mega_field", _METHOD_POST, 204)
    #
    print("\n \n \n \n"
          "ended test calls at start\n"
          "\n")
    time.sleep(1.0)

    CONFIG_SCANNER = {"name": "EBeamScanner", "role": "multibeam"}
    CONFIG_DESCANNER = {"name": "MirrorDescanner", "role": "galvo"}
    CONFIG_MPPC = {"name": "MPPC", "role": "mppc"}

    ASM_manager = AcquisitionServer("ASM", "main", server_URL, children={"EBeamScanner"   : CONFIG_DESCANNER,
                                                                         "MirrorDescanner": CONFIG_DESCANNER,
                                                                         "MPPC"           : CONFIG_MPPC})

    for child in ASM_manager.children.value:
        if child.name == CONFIG_MPPC["name"]:
            MPPC_obj = child
        elif child.name == CONFIG_SCANNER["name"]:
            EBeamScanner_obj = child
        elif child.name == CONFIG_DESCANNER["name"]:
            MirrorDescanner_obj = child

    MPPC_obj.start_acquisition()

    for y in range(4):
        for x in range(4):
            # if x == 3 and y == 2:
            #     MPPC_obj.terminate()
            #     break
            MPPC_obj.get_next_field((x, y))

    MPPC_obj.stop_acquisition()
    time.sleep(5)
    ASM_manager.terminate()

    print("The END!")
