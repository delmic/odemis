import copy
import json
import queue
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

from odemis.model import _vattributes

_METHOD_GET = 1
_METHOD_POST = 2


class AcquisitionServer(model.HwComponent):

    def __init__(self, name, role, server_URL, children={}, deamon=None, **kwargs):
        super(AcquisitionServer, self).__init__(name, role, **kwargs)

        self.server_URL = server_URL
        # TODO K.K. Set external storage and connection

        # Stop any acquisition if already one was in progress
        self.simpleASMCall(self.server_URL + "/scan/finish_mega_field", _METHOD_POST, 204)

        # TODO K.K. remove *kwargs and include setup of entire component in try (each sperated) first check if childeren
        # holds value of component
        try:
            kwargs = children["MirrorDescanner"]
        except Exception:
            raise ValueError("Required child MirrorDescanner not provided")
        self._MirrorDescanner = MirrorDescanner("MirrorDescanner", role=None, parent=self)
        self.children.value.add(self._MirrorDescanner)

        try:
            kwargs = children["EBeamScanner"]
        except Exception:
            raise ValueError("Required child EBeamScanner not provided")
        self._EBeamScanner = EBeamScanner("EBeamScanner", role=None, parent=self)
        self.children.value.add(self._EBeamScanner)

        try:
            kwargs = children["MPPC"]
        except Exception:
            raise ValueError("Required child MPPC not provided")
        self._MPPC = MPPC("MPPC", None, self)
        self.children.value.add(self._MPPC)

    @classmethod
    def simpleASMCall(cls, url, method, expected_status, data=None, raw_response=False, timeout=600):
        """

        :param url: url of the command, server part is defined in global variable URL
        :param method: getting or posting via global variables _METHOD_GET/_METHOD_POST
        :param expected_status: expected feedback of server for a positive call
        :param data: data added to the call
        :param raw_response: specified the format of the structure returned
        :param timeout: [s] if within this period no bytes are received an timeout exception is raised
        :return: status_code(posting), content dictionary(getting), or entire response (raw_response=True)
        """
        logging.getLogger().setLevel(logging.INFO)
        logging.info("Executing: %s" % url)
        if method == _METHOD_GET:
            resp = requests.get(url, json=data, timeout=timeout)
        elif method == _METHOD_POST:
            resp = requests.post(url, json=data, timeout=timeout)

        if resp.status_code != expected_status:
            raise AsmApiException(url, resp, expected_status)

        logging.info("Call to %s went fine, no problems occured\n" % url)

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

        clockFrequency = self.parent.simpleASMCall(self.parent.server_URL + "/scan/clock_frequency", _METHOD_GET, 200)[
            'frequency']
        self.clockFrequency = model.IntVA(clockFrequency, unit='Hz')
        self.clockPeriod = model.FloatVA(1 / self.clockFrequency.value, unit='s')
        self._shape = model.TupleVA((6400, 6400), unit='px,px')
        self.resolution = model.ResolutionVA((6400, 6400), ((10, 10), (7200, 7200)), setter=self._setResolution)
        self.dwellTime = model.FloatContinuous(float(self.clockPeriod.value), (float(self.clockPeriod.value), 100000.0),
                                               unit='s')
        self.pixelSize = model.TupleContinuous((4, 4), range=((1, 1), (100000, 100000)), unit='px,px',
                                               setter=self._setpixelSize)
        self.rotation = model.FloatContinuous(0, range=(0, 2 * numpy.pi), unit='rad, cw=+')
        self.scanFlyback = model.FloatVA(0, unit='s')
        self.scanOffset = model.TupleContinuous((0.0, 0.0), range=((-10.0, -10.0), (10.0, 10.0)), unit='V,V')
        self.scanGain = model.TupleContinuous((0.0, 0.0), range=((-10.0, -10.0), (10.0, 10.0)), unit='V,V')
        self.scanDelay = model.TupleContinuous((0, 0), range=((1, 1), (100000, 100000)), unit='s,s',
                                               setter=self._setScanDelay)

        self._metadata[model.MD_PIXEL_SIZE] = self.pixelSize.value
        self._metadata[model.MD_DWELL_TIME] = self.dwellTime.value

    def _setResolution(self, input_resolution):
        if input_resolution[0] == input_resolution[1]:
            return input_resolution
        else:
            logging.warning("Non-square resolution entered, only square resolution sizes are supported")
            return self.resolution.value

    def _setpixelSize(self, input_resolution):
        if input_resolution[0] == input_resolution[1]:
            return input_resolution
        else:
            logging.warning("Non-square pixel size entered, only square pixel sizes are supported")
            return self.pixelSize.value

    def _setScanDelay(self, input_delay):
        if not(hasattr(self.parent, "_MPPC")) or self.parent._MPPC.acqDelay.value - input_delay[0] >= 0:
            return input_delay
        else:
            logging.error("Wrong input x_scan_to_acq_delay got a negative value.\n"
                          "Change values so that 'self.parent._MPPC. acqDelay.value - self.scanDelay.value[0]' has a positive result")
            return self.scanDelay.value

class MirrorDescanner(model.Emitter):

    def __init__(self, name, role, parent, **kwargs):
        super(MirrorDescanner, self).__init__(name, role, **kwargs)
        self.parent = parent
        # model.Emitter(name, role)
        self.rotation = model.FloatContinuous(0, range=(0, 2 * numpy.pi), unit='rad, cw=+')
        self.scanOffset = model.TupleContinuous((0.0, 0.0), range=((-10.0, -10.0), (10.0, 10.0)), unit='V,V')
        self.scanGain = model.TupleContinuous((0.0, 0.0), range=((-10.0, -10.0), (10.0, 10.0)), unit='V,V')


class MPPC(model.Detector):
    def __init__(self, name, role, parent, **kwargs):
        super(MPPC, self).__init__(name, role, **kwargs)
        self.parent = parent

        if not (hasattr(self.parent, "_MirrorDescanner")) or not (hasattr(self.parent, "_EBeamScanner")):
            ConnectionError("Not all required children are defined (maybe definition order is not correct)")

        self._field_data = FieldMetaData(None, None)  # Current field_data

        # VA defauls values
        self._shape = model.TupleVA((8, 8, 65536), unit="cell-images and depth")
        self.path = model.StringVA('~/Pictures')
        self.dataContent = model.StringVA('empty')
        self.filename = model.StringVA(time.strftime("default-%H-%M-%S-%Y-%m-%d"))
        self._server_URL = self.parent.server_URL
        self.externalStorageURL_host = model.StringVA('host')
        #NOTE: Do not write real username/password here since this is published on github in plaintext!
        self.externalStorageURL_user = model.StringVA('user')
        self.externalStorageURL_password = model.StringVA('password')
        self.acqDelay = model.FloatVA(2.0, unit='s', setter=self._setAcqDelay)

        # Cell acquisition parameters
        #TODO K.K. Perform type check on intput into the VA's floats and ints
        self.cellTranslation = model.ListVA([[[50, 50]] * self._shape.value[0]] * self._shape.value[1])
        # TODO K.K. Put a check according to this input in the assemble_mega_field_check whih includes the counting up
        #  per list
        self.cellTranslation = model.ListVA([[[10 + j, 20 + j] for j in range(i,i+self._shape.value[0])]
                                             for i in range(0,self._shape.value[1]*self._shape.value[0],self._shape.value[0])])
        self.celldarkOffset = model.ListVA([[0] * self._shape.value[0]] * self._shape.value[1])
        self.celldigitalGain = model.ListVA([[1.2] * self._shape.value[0]] * self._shape.value[1])
        self.celldigitalGain = model.ListVA([[j for j in range(i, i+self._shape.value[0])]
                                        for i in range(0,self._shape.value[1]*self._shape.value[0], self._shape.value[0])])
        self.cellCompleteResolution = model.ResolutionVA((800, 800), ((10, 10), (900, 900)))

        # Gather metadata from Siblings and own _meta_data
        self.md_devices = [self._metadata, self.parent._MirrorDescanner._metadata, self.parent._EBeamScanner._metadata]
        self._metadata[model.MD_HW_NAME] = "MPPC"
        self._metadata[model.MD_SW_VERSION] = self._swVersion
        self._metadata[model.MD_ACQ_DATE] = int(time.time())  # Time since Epoch
        self._metadata[model.MD_POS] = (0.0, 0.0)  # TODO implement in next API

        self.acq_queue = queue.Queue()  # acquisition queue with steps that need to be executed
        self.acq_thread = threading.Thread(target=self._acquire,
                                           name="acquisition thread")
        self.acq_thread.deamon = False
        self.acq_thread.start()

        self.data = ASMDataFlow(self, self.start_acquisition, self.get_next_field, self.stop_acquisition,
                                self.acquire_single_field)

    def _assemble_mega_field_data(self, *args):
        cellTranslation = sum(self.cellTranslation.value, [])
        celldarkOffset = sum(self.celldarkOffset.value, [])
        celldigitalGain = sum(self.celldigitalGain.value, [])
        eff_cell_size = (int(self.parent._EBeamScanner.resolution.value[0]/self._shape.value[0]),
                         int(self.parent._EBeamScanner.resolution.value[1] / self._shape.value[1]))

        self._megafield_metadata = \
            MegaFieldMetaData(
                    mega_field_id=self.filename.value,
                    pixel_size=self.parent._EBeamScanner.pixelSize.value[0],
                    #TODO K.K. check input units vs units of VA's is it right that API takes seconds for dwell time?
                    dwell_time=int(self.parent._EBeamScanner.dwellTime.value /
                                   self.parent._EBeamScanner.clockPeriod.value),
                    x_cell_size=self.cellCompleteResolution.value[0],
                    x_eff_cell_size=eff_cell_size[0],
                    y_cell_size=self.cellCompleteResolution.value[1],
                    y_eff_cell_size=eff_cell_size[1],
                    cell_parameters=[CellAcqParameters(translation[0], translation[1], darkOffset, digitalGain)
                                     for translation, darkOffset, digitalGain in zip(cellTranslation,celldarkOffset,celldigitalGain)],
                    x_scan_to_acq_delay=int(self.acqDelay.value - self.parent._EBeamScanner.scanDelay.value[0]),
                    x_scan_delay=self.parent._EBeamScanner.scanDelay.value[0],
                    y_prescan_lines=self.parent._EBeamScanner.scanDelay.value[1],
                    flyback_time=int(self.parent._EBeamScanner.scanFlyback.value /
                                   self.parent._EBeamScanner.clockPeriod.value),
                    x_scan_offset=self.parent._EBeamScanner.scanOffset.value[0],
                    y_scan_offset=self.parent._EBeamScanner.scanOffset.value[1],
                    x_scan_gain=self.parent._EBeamScanner.scanGain.value[0],
                    y_scan_gain=self.parent._EBeamScanner.scanGain.value[1],
                    x_descan_gain=self.parent._MirrorDescanner.scanGain.value[0],
                    y_descan_gain=self.parent._MirrorDescanner.scanGain.value[1],
                    x_descan_offset=self.parent._MirrorDescanner.scanOffset.value[0],
                    y_descan_offset=self.parent._MirrorDescanner.scanOffset.value[1],
                    scan_rotation=self.parent._EBeamScanner.rotation.value,
                    descan_rotation=self.parent._MirrorDescanner.rotation.value,
            )

        return self._megafield_metadata

    def _acquire(self):
        try:
            self._current_status = None
            tnext = 0

            while True:
                # Wait until a message is available
                command, *meta_info = self.acq_queue.get(block=True)
                if command == "start" and self._current_status != "start":
                    self._current_status = command
                    self.parent.simpleASMCall(self._server_URL + "/scan/start_mega_field", _METHOD_POST, 204, meta_info[
                        0].to_dict())

                elif command == "next" and self._current_status != "stop":
                    self.parent.simpleASMCall(self._server_URL + "/scan/scan_field", _METHOD_POST, 204,
                                              meta_info[0].to_dict())

                    # TODO add functionality for getting a full and thumbnail image if simulator is updated
                    if meta_info[1] != 'empty':
                        logging.error("Option %s not yet implemented" % meta_info[1])
                        img = self.parent.simpleASMCall(self._server_URL + "/scan/field", _METHOD_GET, 200, (meta_info[0].position_x,
                                                                                                             meta_info[0].position_y, self._data_content2value(meta_info[1]).to_dict()))
                        self.data.notify(model.DataArray(img, dtype=numpy.uint8), self._getMetadata())
                    else:
                        self.data.notify(model.DataArray(numpy.array([[]], dtype=numpy.uint8), self._getMetadata()))

                elif command == "stop" and self._current_status != "stop":
                    self._current_status = command
                    self.parent.simpleASMCall(self._server_URL + "/scan/finish_mega_field", _METHOD_POST, 204)

                elif command == "terminate":
                    self._current_status = command
                    self.parent.simpleASMCall(self._server_URL + "/scan/finish_mega_field", _METHOD_POST, 204)
                    time.sleep(0.5)
                    raise Exception("Terminated acquisition")

                elif command == "stop" or command == "start":
                    logging.warning("ASM acquisition was already at status '%s'" % command)
                elif command == "next":
                    logging.warning("Start ASM acquisition before taking field images")
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

        logging.debug("Acquisition thread ended")

    def start_acquisition(self):
        mega_field_data = self._assemble_mega_field_data()
        self.acq_queue.put(("start", mega_field_data))

    def get_next_field(self, field_num):
        '''
        :param field_num: x,y
        :return:
        '''
        self._field_data = FieldMetaData(
                field_num[0] * self.parent._EBeamScanner._shape.value[0],
                field_num[1] * self.parent._EBeamScanner._shape.value[1])
        self.acq_queue.put(("next", self._field_data, self.dataContent.value))

    def stop_acquisition(self):
        self.acq_queue.put(("stop", None))

    def acquire_single_field(self, field_num=(0, 0)):
        mega_field_data = self._assemble_mega_field_data()
        self.acq_queue.put(("start", mega_field_data))
        self._field_data = FieldMetaData(field_num[0] * self.parent._EBeamScanner._shape.value[0],
                                         field_num[1] * self.parent._EBeamScanner._shape.value[1])
        self.acq_queue.put(("next", self._field_data, self.dataContent.value))
        self.acq_queue.put(("stop", None))

    def terminate(self):
        def clear_queue(queue_size):
            if queue_size > 1:
                self.acq_queue.get()
                clear_queue(self.acq_queue.qsize())
            else:
                logging.info("Cleared the remainder of the acquisition queue upon termination")
                return

        clear_queue(self.acq_queue.qsize())
        self.acq_queue.put(("terminate", None))

    def convert_field_num2pixels(self, field_num, mega_field_data):
        mega_field_data = self._megafield_metadata
        return (field_num[0] * self._shape.value[0] * mega_field_data.x_eff_cell_size,
                field_num[1] * self._shape.value[1] * mega_field_data.y_eff_cell_size)

    def _getMetadata(self):
        """
        Create dict containing all metadata from siblings and own metadata
        """
        md = {}
        for md_dev in self.md_devices:
            for key in md_dev.keys():
                if key not in md:
                    md[key] = md_dev[key]
                elif key in (model.MD_HW_NAME, model.MD_HW_VERSION, model.MD_SW_VERSION):
                    md[key] = ", ".join([md[key], md_dev[key]])
        return md

    def _data_content2value(self, content):
        """
        Converts string with options for the representation of an image into a value for an ASM call
        :param content:  string holding one of the accepted options
        :return:
        """
        if content == 'thumbnail':
            logging.error("Option %s not yet implemented" % content)
            return True
        elif content == 'full':
            logging.error("Option %s not yet implemented" % content)
            return False
        else:
            # logging.error("Option %s not yet implemented" % content)
            raise ValueError("Option %s not yet implemented" % content)

    def _setAcqDelay(self, input_delay):
        if input_delay - self.parent._EBeamScanner.scanDelay.value[0] >= 0:
            return input_delay
        else:
            logging.error("Wrong input x_scan_to_acq_delay got a negative value.\n"
                          "Change values so that 'self.acqDelay.value - self.parent._EBeamScanner.scanDelay.value[0]' has a positive result")
            return self.acqDelay.value

class ASMDataFlow(model.DataFlow):
    def __init__(self, detector, start_func, next_func, stop_func, get_single_field_func):
        super(ASMDataFlow, self).__init__(self)

        self._start = start_func
        self._next = next_func
        self._stop = stop_func
        self._single_field = get_single_field_func

        self.active = False

        self._acquisition_lock = threading.Lock()

    def start_generate(self):
        """
        Start the dataflow.
        """
        with self._acquisition_lock:
            self._start()
            self.active = True

    def next(self, field_num):
        with self._acquisition_lock:
            self._next(field_num)

    def stop_generate(self):
        """
        Stop the dataflow.
        """
        self._stop()
        self.active = False

    def get(self, asap=True):
        """
        acquire a single field only
        :param asap:
        :return:
        """
        if self._count_listeners() < 1:
            with self._acquisition_lock:
                self._single_field()

        else:
            logging.warning("Already %s listeners subscribed, first stop acquisition to acquire a single "
                            "field-image" % self._count_listeners())

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
    AcquisitionServer.simpleASMCall(server_URL + "/scan/clock_frequency", _METHOD_GET, 200)
    AcquisitionServer.simpleASMCall(server_URL + "/scan/finish_mega_field", _METHOD_POST, 204)
    # AcquisitionServer.simpleASMCall(server_URL + "/scan/start_mega_field", _METHOD_POST, 204, MEGA_FIELD_DATA.to_dict())
    # scan_body = FieldMetaData(position_x=0, position_y=0)
    # AcquisitionServer.simpleASMCall(server_URL + "/scan/scan_field", _METHOD_POST, 204, scan_body.to_dict())
    # scan_body = FieldMetaData(position_x=6400, position_y=6400)
    # scan_body = FieldMetaData(position_x=6400*3, position_y=6400*3)
    # AcquisitionServer.simpleASMCall(server_URL + "/scan/scan_field", _METHOD_POST, 204, scan_body.to_dict())
    # AcquisitionServer.simpleASMCall(server_URL + "/scan/finish_mega_field", _METHOD_POST, 204)
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
    # MPPC_obj.terminate()

    # TODO K.K. finsish acquisition implement so that the thread is nicely terminated
    print("The END!")
