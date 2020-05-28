from __future__ import division
import json
import queue
import re
from datetime import datetime
import logging
import time
import threading
import requests
import numpy
from odemis import model
import weakref

from odemis import model
from src.openapi_server.models.mega_field_meta_data import MegaFieldMetaData
from src.openapi_server.models.cell_parameters import CellParameters as CellAcqParameters
from src.openapi_server.models.field_meta_data import FieldMetaData
from src.openapi_server.models.error_status import ErrorStatus

# TODO K.K. remove set logging level
logging.getLogger().setLevel(logging.DEBUG)

# Variable to differentiate "get" and "post" requests to the ASM server
_METHOD_GET = 1
_METHOD_POST = 2

DATA_CONTENT_TO_ASM = {"empty": None, "thumbnail": True, "full": False}


class AcquisitionServer(model.HwComponent):

    def __init__(self, name, role, server_url, children={}, deamon=None, **kwargs):
        super(AcquisitionServer, self).__init__(name, role, **kwargs)

        self.server_url = server_url
        # TODO K.K. Set external storage and connection

        # Stop any acquisition if already one was in progress
        self.ASMAPICall(self.server_url + "/scan/finish_mega_field", _METHOD_POST, 204)

        # TODO K.K. remove *kwargs and include setup of entire component in try (each sperated) first check if childeren
        # holds value of component
        try:
            kwargs = children["MirrorDescanner"]
        except Exception:
            raise ValueError("Required child MirrorDescanner not provided")
        self._mirror_descanner = MirrorDescanner("MirrorDescanner", role=None, parent=self)
        self.children.value.add(self._mirror_descanner)

        try:
            kwargs = children["EBeamScanner"]
        except Exception:
            raise ValueError("Required child EBeamScanner not provided")
        self._ebeam_scanner = EBeamScanner("EBeamScanner", role=None, parent=self)
        self.children.value.add(self._ebeam_scanner)

        try:
            kwargs = children["MPPC"]
        except Exception:
            raise ValueError("Required child MPPC not provided")
        self._mppc = MPPC("MPPC", role=None, parent=self)
        self.children.value.add(self._mppc)

    @classmethod
    def ASMAPICall(cls, url, method, expected_status, data=None, raw_response=False, timeout=600):
        """

        :param url: url of the command, server part is defined in global variable URL
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


class EBeamScanner(model.Emitter):

    def __init__(self, name, role, parent, **kwargs):
        super(EBeamScanner, self).__init__(name, role, **kwargs)
        self.parent = parent

        # self._metadata[model.MD_PIXEL_SIZE] =

        clockFrequency = self.parent.ASMAPICall(self.parent.server_url + "/scan/clock_frequency", _METHOD_GET, 200)[
            'frequency']
        self.clockPeriod = model.FloatVA(1 / clockFrequency, unit='s')
        self._shape = model.TupleVA((6400, 6400), unit='px')
        # The resolution min/maximum are derived from the effective cell size restriction defined in the API
        self.resolution = model.ResolutionVA((6400, 6400), ((10, 10), (1000 * 8, 1000 * 8)))
        self.dwellTime = model.FloatContinuous(self.clockPeriod.value, (max(self.clockPeriod.value, 400e-9), 100.0),
                                               unit='s')
        self.pixelSize = model.TupleContinuous((4, 4), range=((1, 1), (100000, 100000)), unit='m',
                                               setter=self._setPixelSize)
        self.rotation = model.FloatContinuous(0, range=(0, 2 * numpy.pi), unit='rad')
        self.scanFlyback = model.FloatVA(0, unit='s')
        self.scanOffset = model.TupleContinuous((0.0, 0.0), range=((-10.0, -10.0), (10.0, 10.0)), unit='V')
        self.scanGain = model.TupleContinuous((0.0, 0.0), range=((-10.0, -10.0), (10.0, 10.0)), unit='V')
        self.scanDelay = model.TupleContinuous((0, 0), range=((0, 0), (100000, 100000)), unit='s',
                                               setter=self._setScanDelay)

        self._metadata[model.MD_PIXEL_SIZE] = self.pixelSize.value
        self._metadata[model.MD_DWELL_TIME] = self.dwellTime.value

    def _setPixelSize(self, pixelSize):
        if pixelSize[0] == pixelSize[1]:
            return pixelSize
        else:
            logging.warning("Non-square pixel size entered, only square pixel sizes are supported. "
                            "Width of pixel size is used as height.")
            return (pixelSize[0], pixelSize[0])

    def _setScanDelay(self, scanDelay):
        # Check if detector can record images before ebeam scanner has started to scan.
        if not (hasattr(self.parent, "_mppc")) or self.parent._mppc.acqDelay.value - scanDelay[0] >= 0:
            return scanDelay
        else:
            # Change values so that 'self.parent._mppc. acqDelay.value - self.scanDelay.value[0]' has a positive result
            logging.error("Detector cannot record images before ebeam scanner has started to scan.\n"
                          "Detector needs to start after scanner.")
            return self.scanDelay.value


class MirrorDescanner(model.Emitter):

    def __init__(self, name, role, parent, **kwargs):
        super(MirrorDescanner, self).__init__(name, role, **kwargs)
        self.parent = parent
        # model.Emitter(name, role)
        self.rotation = model.FloatContinuous(0, range=(0, 2 * numpy.pi), unit='rad')
        self.scanOffset = model.TupleContinuous((0.0, 0.0), range=((-10.0, -10.0), (10.0, 10.0)), unit='V')
        self.scanGain = model.TupleContinuous((0.0, 0.0), range=((-10.0, -10.0), (10.0, 10.0)), unit='V')


class MPPC(model.Detector):
    def __init__(self, name, role, parent, **kwargs):
        super(MPPC, self).__init__(name, role, **kwargs)
        self.parent = parent
        self._server_URL = self.parent.server_url

        # Scanner and descanner needs to be initialized before instantiating the detector.
        if not hasattr(self.parent, "_mirror_descanner") or not hasattr(self.parent, "_ebeam_scanner"):
            ConnectionError("Not all required children are defined (maybe order of object creation is not correct)")

        self._field_data = FieldMetaData(None, None)  # Current field_data

        self._shape = model.TupleVA((8, 8, 65536), unit="cell-images and depth")
        # path to external storage folder
        self.path = model.StringVA('~/Pictures', setter=self.setPath)
        self.filename = model.StringVA(time.strftime("default-%H-%M-%S-%Y-%m-%d"), setter=self.setFilename)
        self.dataContent = model.StringEnumerated('empty', {'empty', 'thumbnail', 'full'})
        self.externalStorageURL_path = model.StringVA('/Pictures/', setter=self.setPath)
        # NOTE: Do not write real username/password here since this is published on github in plaintext!
        self.externalStorageURL_ftp = model.StringVA('ftp://username:password@example.com', setter=self.setFTP)
        self.acqDelay = model.FloatContinuous(2.0, range=(0, 100000), unit='s', setter=self._setAcqDelay)

        # Cell acquisition parameters
        # TODO K.K. Perform type check on intput into the VA's floats and ints
        self.cellTranslation = model.ListVA([[[50, 50]] * self._shape.value[0]] * self._shape.value[1])
        # TODO K.K. Put a check according to this input in the assemble_mega_field_check whih includes the counting up
        #  per list. Then delete the dupplications below. (counting up allows to visually inspect te coordinate shifts)
        self.cellTranslation = model.ListVA([[[10 + j, 20 + j] for j in range(i, i + self._shape.value[0])]
                                             for i in range(0, self._shape.value[1] * self._shape.value[0],
                                                            self._shape.value[0])])
        self.celldarkOffset = model.ListVA([[0] * self._shape.value[0]] * self._shape.value[1])
        self.celldigitalGain = model.ListVA([[1.2] * self._shape.value[0]] * self._shape.value[1])
        self.celldigitalGain = model.ListVA([[j for j in range(i, i + self._shape.value[0])]
                                             for i in range(0, self._shape.value[1] * self._shape.value[0],
                                                            self._shape.value[0])])
        self.cellCompleteResolution = model.ResolutionVA((800, 800), ((10, 10), (1000, 1000)))

        # Gather metadata from all related HW components and own _meta_data
        self.md_devices = [self._metadata, self.parent._mirror_descanner._metadata,
                           self.parent._ebeam_scanner._metadata]
        self._metadata[model.MD_HW_NAME] = "MPPC"
        self._metadata[model.MD_SW_VERSION] = self._swVersion

        self.acq_queue = queue.Queue()  # acquisition queue with steps that need to be executed
        self.acq_thread = threading.Thread(target=self._acquire,
                                           name="acquisition thread")
        self.acq_thread.deamon = False
        self.acq_thread.start()

        self.dataFlow = ASMDataFlow(self, self.start_acquisition, self.get_next_field, self.stop_acquisition,
                                    self.acquire_single_field)

    def setFTP(self, URL):
        """
        setter which checks for correctness of FTP URL and otherwise returns old value
        :param URL: e.g. ftp://username:password@example.com
        :return: correct ftp URL
        """
        def checkName(file_name, allowed_characters=r'[^a-z0-9]'):
            search = re.compile(allowed_characters).search
            if not bool(search(file_name)):
                return True
            else:
                return False
        try:
            host = URL[URL.find('@') + 1:-1]
            user = URL[URL.find('//') + 2:URL.find(':')]
            password = URL[URL.find(':') + 1:URL.find('@')]
        except:
            logging.warning("Incorrect ftp URL is provided, please use form: ftp://username:password@example.com")
            return self.externalStorageURL_ftp.value


        if not checkName(host, allowed_characters=r'[^a-z0-9]'):
            logging.warning(
                "host contains invalid characters, host remains unchanged (only the characters '%s' are allowed)" % 'a-z 0-9')
            return self.externalStorageURL_ftp.value
        elif not checkName(user, allowed_characters=r'[^a-z0-9]'):
            logging.warning(
                "user contains invalid characters, user remains unchanged (only the characters '%s' are allowed)" % 'a-z 0-9')
            return self.externalStorageURL_ftp.value
        elif not checkName(password, allowed_characters=r'[^a-z0-9]'):
            logging.warning(
                "password contains invalid characters, password remains unchanged (only the characters '%s' are allowed)" % 'a-z 0-9')
            return self.externalStorageURL_ftp.value
        elif URL[0:6] is not 'ftp://':
            logging.warning("Incorrect ftp URL is provided, please use form: ftp://username:password@example.com")
            return self.externalStorageURL_ftp.value
        else:
            return URL

    def setPath(self, path, allowed_characters=r'[^a-z0-9/_()-]'):
        search = re.compile(allowed_characters).search
        if not bool(search(path)):
            return path
        else:
            logging.warning("path contains invalid characters, path remains unchanged.(only the characters "
                            "'%s' are allowed)" % allowed_characters[1:-1])
            return self.path.value

    def setFilename(self, file_name, allowed_characters=r'[^a-z0-9_()-]'):
        search = re.compile(allowed_characters).search
        if not bool(search(file_name)):
            return file_name
        else:
            logging.warning("file_name contains invalid characters, file_name remains unchanged (only the characters "
                            "'%s' are allowed)" % allowed_characters[1:-1])
            return self.filename.value

    def _assemble_megafield_metadata(self, *args):
        cellTranslation = sum(self.cellTranslation.value, [])
        celldarkOffset = sum(self.celldarkOffset.value, [])
        celldigitalGain = sum(self.celldigitalGain.value, [])
        eff_cell_size = (int(self.parent._ebeam_scanner.resolution.value[0] / self._shape.value[0]),
                         int(self.parent._ebeam_scanner.resolution.value[1] / self._shape.value[1]))

        self._megafield_metadata = \
            MegaFieldMetaData(
                    mega_field_id=self.filename.value,
                    pixel_size=self.parent._ebeam_scanner.pixelSize.value[0],
                    # TODO K.K. check input units vs units of VA's is it right that API takes seconds for dwell time?
                    dwell_time=int(self.parent._ebeam_scanner.dwellTime.value /
                                   self.parent._ebeam_scanner.clockPeriod.value),
                    x_cell_size=self.cellCompleteResolution.value[0],
                    x_eff_cell_size=eff_cell_size[0],
                    y_cell_size=self.cellCompleteResolution.value[1],
                    y_eff_cell_size=eff_cell_size[1],
                    cell_parameters=[CellAcqParameters(translation[0], translation[1], darkOffset, digitalGain)
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

        return self._megafield_metadata

    def _acquire(self):
        try:
            self._acquisition_in_progress = None # To prevent that acquisitions mix up, or stop the acquisition twice.
            tnext = 0

            while True:
                # Wait until a message is available
                command, *meta_info = self.acq_queue.get(block=True)

                if command == "start":
                    if not self._acquisition_in_progress:
                        self._acquisition_in_progress = True
                        megafield_metadata = meta_info[0]
                        self.parent.ASMAPICall(self._server_URL + "/scan/start_mega_field", _METHOD_POST, 204,
                                               megafield_metadata.to_dict())
                    else:
                        logging.warning("ASM acquisition was already at status '%s'" % command)

                elif command == "next":
                    if self._acquisition_in_progress:
                        field_data = meta_info[0]  # Field metadata for the specific position of the field to scan
                        dataContent = meta_info[1]  # Specifies the type of image to return (empty, thumbnail or full)
                        notifier_func = meta_info[2]  # Return function (usually, Dataflow.notify or acquire_single_filed queue)

                        self.parent.ASMAPICall(self._server_URL + "/scan/scan_field", _METHOD_POST, 204,
                                               field_data.to_dict())

                        # TODO add metadata from queue into mergaMetadata function so that metadata is correct.
                        if DATA_CONTENT_TO_ASM[dataContent] == None:
                            da = model.DataArray(numpy.array([[0]], dtype=numpy.uint8), metadata=self._mergeMetadata())
                        else:
                            # TODO add functionality for getting a full and thumbnail image if simulator is updated
                            logging.error("Option %s not yet implemented" % meta_info[1])
                            img = self.parent.ASMAPICall(self._server_URL + "/scan/field", _METHOD_GET, 200,
                                                         (field_data.position_x,
                                                          field_data.position_y,
                                                          DATA_CONTENT_TO_ASM[dataContent].to_dict()))
                            da = model.DataArray(img, metadata=self._mergeMetadata())

                        notifier_func(da)

                    else:
                        logging.warning("Start ASM acquisition before taking field images")

                elif command == "stop":
                    if self._acquisition_in_progress:
                        self._acquisition_in_progress = False
                        self.parent.ASMAPICall(self._server_URL + "/scan/finish_mega_field", _METHOD_POST, 204)
                    else:
                        logging.warning("ASM acquisition was already at status '%s'" % command)

                elif command == "terminate":
                    self._acquisition_in_progress = None
                    # self.parent.ASMAPICall(self._server_URL + "/scan/finish_mega_field", _METHOD_POST, 204)
                    # time.sleep(0.5)
                    raise Exception("Terminated acquisition")

                else:
                    logging.error("Received invalid command '%s' is skipped" % command)
                    raise ValueError

                tnow = time.time()
                # sleep a bit to avoid refreshing too fast
                tsleep = tnext - tnow
                if tsleep > 0.01:
                    time.sleep(tsleep)

                tnext = time.time() + 0.2  # max 5 Hz

        except Exception as e:
            if command == "terminate":
                raise Exception("Terminated acquisition")

            elif command is not None:
                logging.error("last message was not executed, should have perfomed action: '%s'\n"
                              "received exepction: \n %s" % (command, e))
        finally:
            self.parent.ASMAPICall(self._server_URL + "/scan/finish_mega_field", _METHOD_POST, 204)
            logging.debug("Acquisition thread ended")

    def start_acquisition(self):
        megafield_metadata = self._assemble_megafield_metadata()
        self.acq_queue.put(("start", megafield_metadata))

    def get_next_field(self, field_num):
        '''
        :param field_num: x,y
        :return:
        '''
        self._field_data = FieldMetaData(
                field_num[0] * self.parent._ebeam_scanner.resolution.value[0],
                field_num[1] * self.parent._ebeam_scanner.resolution.value[1])
        #TODO K.K. remove temporary vas A
        A = self.convert_field_num2pixels(field_num)
        self.acq_queue.put(("next", self._field_data, self.dataContent.value, self.dataFlow.notify))

    def stop_acquisition(self):
        self.acq_queue.put(("stop", ))

    def acquire_single_field(self, field_num=(0, 0)):
        return_queue = queue.Queue()  # queue which allows to return images and be blocked when waiting on images
        mega_field_data = self._assemble_megafield_metadata()

        self.acq_queue.put(("start", mega_field_data))
        self._field_data = FieldMetaData(field_num[0] * self.parent._ebeam_scanner.resolution.value[0],
                                         field_num[1] * self.parent._ebeam_scanner.resolution.value[1])
        self.acq_queue.put(("next", self._field_data, self.dataContent.value, return_queue.put))
        self.acq_queue.put(("stop", ))

        return return_queue.get(timeout=600)

    def terminate(self):
        # Clear the queue
        while True:
            try:
                self.acq_queue.get(block=False)
            except queue.Empty:
                break

        self.acq_queue.put(("terminate", None))

    def convert_field_num2pixels(self, field_num):
        return (field_num[0] * self._shape.value[0] * self.parent._ebeam_scanner.resolution.value[0],
                field_num[1] * self._shape.value[1] * self.parent._ebeam_scanner.resolution.value[1])

    def _mergeMetadata(self):
        """
        Create dict containing all metadata from siblings and own metadata
        """
        md = {}
        self._metadata[model.MD_ACQ_DATE] = time.time()  # Time since Epoch
        self._metadata[model.MD_POS] = (0.0, 0.0)  # TODO implement in next API

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
        # Check if detector can record images before ebeam scanner has started to scan.
        if delay - self.parent._ebeam_scanner.scanDelay.value[0] >= 0:
            return delay
        else:
            # Change values so that 'self.acqDelay.value - self.parent._ebeam_scanner.scanDelay.value[0]' has a positive result
            logging.error("Detector cannot record images before ebeam scanner has started to scan.\n"
                          "Detector needs to start after scanner.")
            return self.acqDelay.value


class ASMDataFlow(model.DataFlow):
    def __init__(self, detector, start_func, next_func, stop_func, get_func):
        super(ASMDataFlow, self).__init__(self)

        self._start = start_func
        self._next = next_func
        self._stop = stop_func
        self._get = get_func

    def start_generate(self):
        """
        Start the dataflow.
        """
        self._start()

    def next(self, field_num):
        self._next(field_num)

    def stop_generate(self):
        """
        Stop the dataflow.
        """
        self._stop()

    def get(self):
        """
        acquire a single field only
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
    def __init__(self, url, response, expected_status):
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


if __name__ == '__main__':
    # TODO K.K. remove this part! After most of implementation is done.
    logging.getLogger().setLevel(logging.INFO)
    MEGA_FIELD_DATA = MegaFieldMetaData(
            mega_field_id=datetime.now().strftime("megafield_%Y%m%d-%H%M%S"),
            pixel_size=4,
            dwell_time=2,
            x_cell_size=900,
            x_eff_cell_size=800,
            y_cell_size=900,
            y_eff_cell_size=800,
            cell_parameters=[CellAcqParameters(50, 50, 0, 1.2)] * 64,
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
    AcquisitionServer.ASMAPICall(server_URL + "/scan/clock_frequency", _METHOD_GET, 200)
    AcquisitionServer.ASMAPICall(server_URL + "/scan/finish_mega_field", _METHOD_POST, 204)
    # AcquisitionServer.simpleASMCall(server_url + "/scan/start_mega_field", _METHOD_POST, 204, MEGA_FIELD_DATA.to_dict())
    # scan_body = FieldMetaData(position_x=0, position_y=0)
    # AcquisitionServer.simpleASMCall(server_url + "/scan/scan_field", _METHOD_POST, 204, scan_body.to_dict())
    # scan_body = FieldMetaData(position_x=6400, position_y=6400)
    # scan_body = FieldMetaData(position_x=6400*3, position_y=6400*3)
    # AcquisitionServer.simpleASMCall(server_url + "/scan/scan_field", _METHOD_POST, 204, scan_body.to_dict())
    # AcquisitionServer.simpleASMCall(server_url + "/scan/finish_mega_field", _METHOD_POST, 204)
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
    MPPC_obj.terminate()

    print("The END!")
