# -*- coding: utf-8 -*-
'''
Created on 14 Jun 2016

@author: Sabrina Rossberger

Copyright © 2018 Sabrina Rossberger, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

from __future__ import division

import Queue
import logging
from odemis import model, util
from odemis.model import oneway
import threading
import time
import socket
import numpy
import collections


class RemoteExError(StandardError):

    def __init__(self, errnum, *args, **kwargs):
        # Needed for pickling, cf https://bugs.python.org/issue1692335 (fixed in Python 3.3)
        StandardError.__init__(self, errnum, *args, **kwargs)
        self.errnum = errnum

    def __str__(self):
        errmsg = self._errordict.get(self.errnum, "Unknown RemoteEx error.")
        return "Hamamatsu streak camera RemoteEx error %d: %s" % (self.errnum, errmsg)

    _errordict = {
            0: "Command successfully executed.",
            1: "Invalid syntax (command must be followed by"
               "parentheses and must have the correct number"
               "and type of parameters separated by comma).",
            2: "Command or Parameters are unknown.",
            3: "Command currently not possible.",
            6: "Parameter is missing.",
            7: "Command cannot be executed.",
            8: "An error has occurred during execution.",
            9: "Data cannot be sent by TCP-IP.",
            10: "Value of a parameter is out of range.",
        }


class CancelledError(Exception):  #TODO needed?
    """
    raise to indicate the acquisition is cancelled and must stop
    """
    pass


class OrcaFlash(model.DigitalCamera):
    """
    Represents Hamamatsu readout camera.
    """

    def __init__(self, name, role, parent, **kwargs):
        """ Initializes the Hamamatsu OrcaFlash readout camera.
        :parameter name: (str) as in Odemis  # TODO
        :parameter role: (str) as in Odemis  # TODO
        :parameter parent: class streakcamera
        """
        super(OrcaFlash, self).__init__(name, role, parent=parent, **kwargs)  # init HwComponent

        self.parent = parent

        # Set parameters readout camera
        # TODO what we want to have in MD
        # TODO check if we need to do this or can use default HW file?
        parent.CamParamSet("Setup", "TimingMode", "Internal timing")  # TODO internal or external?
        parent.CamParamSet("Setup", "TriggerMode", 'Edge trigger')
        parent.CamParamSet("Setup", "TriggerSource", 'BNC')
        parent.CamParamSet("Setup", "TriggerPolarity", 'neg.')
        parent.CamParamSet("Setup", "ScanMode", 'Subarray')
        parent.CamParamSet("Setup", "Binning", '2 x 2')
        parent.CamParamSet("Setup", "VWidth", '1016')
        parent.CamParamSet("Setup", "HWidth", '1344')
        parent.CamParamSet("Setup", "ShowGainOffset", 'True')

        # TODO trouble reading see readCommandResponse
        # self._hwVersion = parent.CamParamGet("Setup", "CameraInfo")
        # self._metadata[model.MD_HW_VERSION] = self._hwVersion
        # self._swVersion = self._hwVersion[3]
        # self._metadata[model.MD_SW_VERSION] = self._swVersion
        # self._metadata[model.MD_DET_TYPE] = model.MD_DT_INTEGRATING # ?? MD_DT_NORMAL

        # output CameraInfo:
        # 'Product number: C13440-20C'
        # 'Serial number: 301730'
        # 'Firmware: 4.20.B'
        # 'Version: 4.20.B03-A19-B02-4.02'

        # sensor size (resolution)
        # Note: sensor size of OrcaFlash is actually much larger (2048px x 2048px)
        # However, only a smaller subarea is used for operating the streak system.
        resolution = (int(parent.CamParamGet("Setup", "HWidth")[0]),
                       int(parent.CamParamGet("Setup", "VWidth")[0]))
        self._metadata[model.MD_SENSOR_SIZE] = resolution

        # 16-bit
        self._shape = resolution + (2 ** 16,)

        # physical pixel size is 6.5um x 6.5um
        pixelsize = (6.5e-06, 6.5e-06)
        self.pixelSize = model.VigilantAttribute(pixelsize, unit="m", readonly=True)
        self._metadata[model.MD_SENSOR_PIXEL_SIZE] = self.pixelSize.value

        self._binning = self._getBinning()  # used by resolutionFitter()

        # need to be before binning, as it is modified when changing binning
        # _resolution = physical pixelsize * _binning / _magnification
        _resolution = (int(resolution[0]/self._binning[0]), int(resolution[1]/self._binning[1]))
        self.resolution = model.ResolutionVA(_resolution, ((1, 1), resolution), setter=self._setResolution)
        self._metadata[model.MD_RESOLUTION] = self.resolution

        choices_bin = self._getReadoutCamBinningChoices()
        self.binning = model.VAEnumerated(self._binning, choices_bin, setter=self._setBinning)
        self._metadata[model.MD_BINNING] = self.binning.value

        # Note: no function to get current acqMode.
        # Note: Acquisition mode, needs to be before exposureTime!
        # Acquisition mode should be either "Live" (non-sync acq) or "SingleLive" (sync acq) for now.
        self.acqMode = "Live"

        # Note: This is the exposure time in live mode, however it is possible to specify multiple ones.
        # TODO Might be necessary to have multiple VAs in future for using other acq options in RemoteEx.
        range_exp = self._getCamExpTimeRange()
        self._exp_time = self._getCamExpTime()
        self.exposureTime = model.FloatContinuous(self._exp_time, range_exp, unit="s", setter=self.setExposureTime)
        self._metadata[model.MD_EXP_TIME] = self.exposureTime.value

        # Note: timeRange of streakunit > exposureTime readoutcam is possible and okay.

        self.readoutRate = model.VigilantAttribute(425000000, unit="Hz", readonly=True)  # MHz
        self._metadata[model.MD_READOUT_TIME] = 1 / self.readoutRate.value  # s

        # for synchronized acquisition
        self._sync_event = None
        self.softwareTrigger = model.Event()
        # queue events starting an acquisition (advantageous when event.notify is called very fast)
        self.queue_events = collections.deque()
        self._acq_sync_lock = threading.Lock()

        # # TODO move maybe to readoutcam
        # # start thread, which keeps reading the dataport when an image/scaling table has arrived
        # # after commandport thread to be able to set the RingBuffer
        # # AcqLiveMonitor writes images to Ringbuffer, which we can read from
        # # only works if we use "Live" or "SingleLive" mode
        # self.parent.AcqLiveMonitor("RingBuffer", "10")  # TODO need to be handled in case we use other acq modes
        # self.t_image = threading.Thread(target=self._getDataFromBuffer)
        # self.t_image.start()

        self.data = streakCameraDataFlow(self._start, self._stop, self._sync)

    def _getReadoutCamBinningChoices(self):
        """
        Get min and max values for exposure time. Values are in order. First to fourth values see CamParamInfoEx.
        :return: tuple containing min and max exposure time
        """
        choices_raw = self.parent.CamParamInfoEx("Setup", "Binning")[4:]
        choices = []
        for choice in choices_raw:
            choices.append((int(choice[0]), int(choice[4])))

        return set(choices)

    def _getBinning(self):
        """Get binning setting from camera and transfer to format, which resolution VA needs as input."""
        _binning = self.parent.CamParamGet("Setup", "Binning")
        # ResolutionVA need tuple instead of list of format [2 x 2]
        binning = int(_binning[0].split("x")[0].strip(" ")), int(_binning[0].split("x")[1].strip(" "))
        return binning

    def _setBinning(self, value):
        """
        value (2-tuple int)
        Called when "binning" VA is modified. It actually modifies the camera binning.
        """
        # ResolutionVA need tuple instead of list of format [2 x 2]
        binning = "%s x %s" % (value[0], value[1])
        self.parent.CamParamSet("Setup", "Binning", binning)

        prev_binning, self._binning = self._binning, value

        # adapt resolution
        # TODO check if really necessary: why not just call resolutionFitter
        # TODO self._binning is already updated and shape does not change
        change = (prev_binning[0] / self._binning[0],
                  prev_binning[1] / self._binning[1])
        old_resolution = self.resolution.value
        new_res = (int(round(old_resolution[0] * change[0])),
                   int(round(old_resolution[1] * change[1])))

        # fit
        self.resolution.value = new_res

        self._metadata[model.MD_BINNING] = self._binning  # update MD

        return self._binning

    def _setResolution(self, value):
        new_res = self.resolutionFitter(value)
        self._metadata[model.MD_RESOLUTION] = new_res  # update MD
        return new_res

    def resolutionFitter(self, size_req):  # TODO do we need to do it that fancy?
        # TODO think we can keep it simple as we do not provide to change the sensor size yet...
        """
        Finds a resolution allowed by the camera which fits best the requested
          resolution.
        size_req (2-tuple of int): resolution requested
        returns (2-tuple of int): resolution which fits the camera. It is equal
         or bigger than the requested resolution
        """
        resolution = self._shape[:2]
        # max_size = (int(resolution[0] // self._binning[0]),
        #             int(resolution[1] // self._binning[1]))  # floor division: not below zero

        size = (int(resolution[0] // self._binning[0]),
                    int(resolution[1] // self._binning[1]))  # floor division: not below zero

        # smaller than the whole sensor
        # size = (min(size_req[0], max_size[0]), min(size_req[1], max_size[1]))
        # # Note: the current binning is taken into account for the ranges
        # ranges = (self._bin_to_resrng[0][self._binning[0]],
        #           self._bin_to_resrng[1][self._binning[1]])
        # size = (max(ranges[0][0], size[0]), max(ranges[1][0], size[1]))

        return size

    def getExposureTime(self):
        """Get the exposure time from the VA.
        :return: exposure time
        """
        exp_time = self._getCamExpTime()  # TODO why not directly call that one?
        return exp_time

    def setExposureTime(self, value):
        """Set the exposure time VA.
        :param value: exposure time to set
        :return: exposure time
        """
        self._setCamExpTime(value)  # TODO why not directly call that one?
        self._metadata[model.MD_EXP_TIME] = value  # update MD
        return value

    def _getCamExpTimeRange(self):
        """
        Get min and max values for exposure time. Values are in order. First to fourth values see CamParamInfoEx.
        :parameter location: (str) see CamParamGet
        :return: tuple containing min and max exposure time
        """
        min_value = self.parent.CamParamInfoEx("Live", "Exposure")[4]
        max_value = self.parent.CamParamInfoEx("Live", "Exposure")[-1]

        min_value_raw, min_unit = min_value.split(' ')[0], min_value.split(' ')[1]
        max_value_raw, max_unit = max_value.split(' ')[0], max_value.split(' ')[1]

        self.min_exp = self.parent.convertUnit2Time(min_value_raw, min_unit)
        self.max_exp = self.parent.convertUnit2Time(max_value_raw, max_unit)

        range = (self.min_exp, self.max_exp)
        return range

    def _getCamExpTime(self):
        """Recalculate exposure time.
        :parameter location: (str) see CamParamGet
        :return: exposure time in sec"""
        exp_time_raw = self.parent.CamParamGet("Live", "Exposure")[0].split(' ')
        try:
            exp_time = self.parent.convertUnit2Time(exp_time_raw[0], exp_time_raw[1])
        except Exception:
            raise logging.error("Exposure time of %s is not supported for read-out camera." % exp_time_raw)

        return exp_time

    def _setCamExpTime(self, exp_time):
        """Translate exposure time into a for RemoteEx readable format.
        :parameter location: (str) see CamParamGet
        :parameter exp_time (float): exposure time"""
        try:
            exp_time_raw = self.parent.convertTime2Unit(exp_time)
        except Exception:
            raise logging.debug("Exposure time of %s sec is not supported for read-out camera." % exp_time)

        self.parent.CamParamSet("Live", "Exposure", exp_time_raw)

    def _start(self):
        """Start an acquisition.
        :parameter AcqMode: (str) see AcqStart
        :raises CancelledError if the acquisition must stop.
        """
        if self._sync_event is None:  # do not care about synchronization, start acquire
            self.parent.StartAcquisition(self.acqMode)

        # raise CancelledError() # TODO needed?

    def _stop(self):
        """Stop the acquisition."""
        self.parent.AcqStop()
        self.parent.queue_img.put("F")  # Flush, to stop reading all images still in the ring buffer

    def _sync(self, event):  # event = self.softwareTrigger
        """
        Synchronize the acquisition on the given event. Every time the event is
          triggered, the DataFlow will start a new acquisition.
        Behaviour is unspecified if the acquisition is already running.  # TODO still True?
        event (model.Event or None): event to synchronize with. Use None to
          disable synchronization.
        The DataFlow can be synchronized only with one Event at a time.
        """
        # if event None and sync as well -> return, or if event sync, but sync already set -> return
        if self._sync_event == event:
            return

        if self._sync_event:  # if new event = None, unsubscribe previous event (which was softwareTrigger)
            self.acqMode = "Live"
            self._sync_event.unsubscribe(self)

        self._sync_event = event

        if self._sync_event:
            self.acqMode = "SingleLive"  # TODO or use "Live"? and just get latest image in buffer when software trigger request it?
            # softwareTrigger subscribes to onEvent method: if softwareTrigger.notify() called, onEvent method called
            self._sync_event.subscribe(self)  # must have onEvent method

    @oneway
    def onEvent(self):
        """Called by the Event when it is triggered  (e.g. self.softwareTrigger.notify())."""
        logging.debug("Event triggered to start a new synchronized acquisition.")
        self.queue_events.append(time.time())
        self.parent.queue_img.put("start")

    def _update_settings(self):
        """
        Commits the settings to the camera. Only the settings which have been
        modified are updated.
        Note: acquisition_lock must be taken, and acquisition must _not_ going on.
        return:
            size (3 ints): width, height, itemsize
            synchronised (bool): whether the acquisition has a software trigger
        """
        pass  # TODO needed for MD? now handle update MD directly when updating the VAs.

    # TODO move fct to readoutcam however, steakunit.mode VA needs to be available somehow....
    # def _getDataFromBuffer(self):
    #     """This method runs in a separate thread and waits for messages in queue indicating
    #     that some data was received. The image is then received from the device via the dataport IP socket or
    #     the vertical scaling table is received, which corresponds to a time range for a single sweep.
    #     It corrects the vertical time information. The table contains the actual timestamps for each px.."""
    #
    #     # TODO need to check that there is an ringbuffer available!?
    #
    #
    #     logging.debug("Starting data thread.")
    #     time.sleep(2)
    #     is_receiving_image = False
    #
    #     try:
    #         while True:
    #
    #             if self._sync_event and not is_receiving_image:
    #                 commandStatus = self.parent.AsyncCommandStatus()
    #                 while int(commandStatus[1]) or int(commandStatus[2]):
    #                     # TODO it can happen that we run into this loop, which is okay, but in general
    #                     # it looks like that it happens when RemoteEx is not responding anymore...
    #                     # RemoteEx does then not stop the acquisition properly if requested.
    #                     time.sleep(0)
    #                     self.parent.AcqStop()
    #                     logging.debug("Asynchronous RemoteEx command still in process. Wait until finished.")
    #                 try:
    #                     event_time = self.queue_events.popleft()
    #                     logging.warning("Starting acquisition delayed by %g s.", time.time() - event_time)
    #                     self.parent.AcqStart(self.acqMode)
    #                     is_receiving_image = True
    #                 except IndexError:
    #                     # No event (yet) => fine
    #                     pass
    #
    #             rargs = self.parent.queue_img.get(block=True)  # block until receive something
    #             logging.debug("Received message %s", rargs)
    #
    #             if rargs is None:  # if message is None end the thread
    #                 return
    #
    #             # synchronized mode
    #             if self._sync_event:
    #                 if rargs == "start":
    #                     logging.info("Received event trigger")
    #                     continue
    #                 else:
    #                     logging.info("Get the synchronized image.")
    #
    #             # non-sync mode
    #             else:
    #                 while not self.parent.queue_img.empty():
    #                     # keep reading to check if there might be a newer image for display
    #                     # in case we are too slow with reading
    #                     rargs = self.parent.queue_img.get(block=False)
    #
    #                     if rargs is None:  # if message is None end the thread
    #                         return
    #                 logging.info("No more images in queue, so get the image.")
    #
    #             if rargs == "F":  # Flush => the previous images are from the previous acquisition
    #                 logging.debug("Acquisiton was stopped so flush previous images.")
    #                 continue
    #
    #             self._metadata[model.MD_ACQ_DATE] = time.time()
    #             # TODO more fancy maybe? metadata[model.MD_ACQ_DATE] = time.time() - (exposure_time + readout_time)
    #
    #             # get the image from the buffer
    #             img_num = rargs[1]
    #             img_info = self.parent.ImgRingBufferGet("Data", img_num)
    #
    #             # can be NoneType if img_num to high!!
    #             # TODO looks like img_info can be empty/None type object -> need a check here?
    #             # TODO can it be empty? Should there not first the Live acq start, and then getImage as we have the
    #             # TODO lock for sending commands
    #
    #             img_size = int(img_info[0]) * int(img_info[1]) * 2  # num of bytes we need to receive #TODO why 2
    #             img_num_actual = img_info[4]
    #
    #             img = ""
    #             try:
    #                 while len(img) < img_size:  # wait until all bytes are received
    #                     img += self.parent._dataport.recv(img_size)
    #             except socket.timeout as msg:
    #                 logging.error("Did not receive an image: %s", msg)
    #                 continue
    #
    #             image = numpy.frombuffer(img, dtype=numpy.uint16)  # convert to array
    #             image.shape = (int(img_info[1]), int(img_info[0]))
    #
    #             logging.debug("Requested image number %s, received image number %s from buffer."
    #                           % (img_num, img_num_actual))
    #
    #             # # get the scaling table to correct the time axis
    #             # # TODO only request scaling table if corresponding MD not available for this time range
    #             # if self._streakunit.mode.value:
    #             #     # TODO some sync problem might be here if a different command is in queue
    #             #     # in between ImgRingBufferGet and ImgDataGet: check again!
    #             #     scl_table_info = self.parent.ImgDataGet("current", "ScalingTable", "Vertical")  # request scaling table
    #             #
    #             #     scl_table_size = int(scl_table_info[0]) * 4  # num of bytes we need to receive
    #             #
    #             #     # receive the bytes via the dataport
    #             #     tab = ''
    #             #     try:
    #             #         while len(tab) < scl_table_size:  # keep receiving bytes until we received all expected bytes
    #             #             tab += self.parent._dataport.recv(scl_table_size)
    #             #             table = numpy.frombuffer(tab, dtype=numpy.float32)  # convert to array
    #             #             table_converted = table * self.parent.timeRangeConversionFactor  # convert to sec
    #             #             self._metadata[model.MD_TIME_LIST] = table_converted
    #             #     except socket.timeout as msg:
    #             #         logging.error("Did not receive a scaling table: %s", msg)
    #             #         continue
    #             # else:
    #             #     if model.MD_TIME_LIST in self._metadata.keys():
    #             #         self._metadata.pop(model.MD_TIME_LIST, None)
    #
    #             md = dict(self._metadata)  # make a copy of md dict so cannot be accidentally changed
    #             self.updateMetadata(md)  # merge dict
    #             dataarray = model.DataArray(image, md)
    #             self.data.notify(dataarray)  # pass the new image plus MD to the callback fct  #TODO correct?
    #
    #             if self._sync_event:
    #                 is_receiving_image = False
    #
    #     except Exception:
    #         logging.exception("Hamamatsu streak camera TCP/IP image thread failed.")
    #     finally:
    #         logging.info("Hamamatsu streak camera TCP/IP image thread ended.")

    def terminate(self):
        try:
            self._stop()  # stop any acquisition
        except Exception:  # TODO which exception?
            pass


class StreakUnit(model.HwComponent):
    """
    Represents Hamamatsu streak unit.
    """

    def __init__(self, name, role, parent, location, **kwargs):
        super(StreakUnit, self).__init__(name, role, parent=parent, **kwargs)  # init HwComponent

        self.parent = parent
        self.location = location

        self._hwVersion = parent.DevParamGet(location, "DeviceName")
        self._metadata[model.MD_HW_VERSION] = self._hwVersion

        # Set parameters streak unit
        parent.DevParamSet(location, "Time Range", "1 ns")
        parent.DevParamSet(location, "MCP Gain", "0")
        # Switch Mode to "Focus", MCPGain = 0 (implemented in RemoteEx and also here in the driver).
        parent.DevParamSet(location, "Mode", "Focus")
        # Resets behavior for a vertical single shot sweep: Automatic reset occurs after each sweep.
        parent.DevParamSet(location, "Trig. Mode", "Cont")
        # [Volt] Input and indication of the trigger level for the vertical sweep.
        parent.DevParamSet(location, "Trig. level", "1") # TODO??
        parent.DevParamSet(location, "Trig. slope", "Rising")

        parent.DevParamGet(location, "Trig. status")  # read only

        # Ready: Is displayed when the system is ready to receive a trigger signal.
        # Fired: Is displayed when the system has received a trigger signal but the sweep has not
        # been completed or no reset signal has been applied until now. The system will ignore trigger signals
        # during this state.
        # Do Reset: Do Reset can be selected when the system is in trigger mode Fired. After selecting Do
        # Reset the trigger status changes to Ready.

        self._metadata[model.MD_STREAK_TIMERANGE] = parent.DevParamGet(location, "Time Range")
        self._metadata[model.MD_STREAK_MCPGAIN] = parent.DevParamGet(location, "MCP Gain")
        self._metadata[model.MD_STREAK_MODE] = parent.DevParamGet(location, "Mode")

        # VAs
        self.mode = model.BooleanVA(False, setter=self._updateMode)  # default False see set params above

        gain = self._convertOutput2Value(self.parent.DevParamGet(location, "MCP Gain"))
        self.MCPgain = model.VigilantAttribute(gain, setter=self._updateMCPGain)

        timeRange = self._getStreakUnitTimeRange()
        choices = set(self._getStreakUnitTimeRangeChoices())
        self.timeRange = self.exposureTime = model.FloatEnumerated(timeRange, choices,
                                                                   setter=self._updateTimeRange)
        # read-only VAs
        # TODO: Trig. Mode, Trig. level, Trig. slope??? plus MD!?

    def _updateMode(self, value):
        """
        update the mode VA
        """
        if not value:
            self.MCPgain.value = 0
            self.parent.DevParamSet(self.location, "Mode", "Focus")
        else:
            self.parent.DevParamSet(self.location, "Mode", "Operate")
        logging.debug("Reporting mode %s for streak unit.", value)
        self._metadata[model.MD_STREAK_MODE] = value

        return value

    def _updateMCPGain(self, value):
        """
        update the MCP gain VA
        """
        value_str = self._convertInput2Str(value)
        self.parent.DevParamSet(self.location, "MCP Gain", value_str)
        logging.debug("Reporting MCP gain %s for streak unit.", value)
        self._metadata[model.MD_STREAK_MCPGAIN] = value

        return value

    def _updateTimeRange(self, value):
        """
        update the time range VA
        """
        self._setStreakUnitTimeRange(self.location, value)
        logging.debug("Reporting time range %s for streak unit.", value)
        self._metadata[model.MD_STREAK_TIMERANGE] = value

        return value

    def _convertInput2Str(self, input_value):
        """Function that converts any input to a string as requested by RemoteEx."""
        if isinstance(input_value, str):
            return input_value
        elif isinstance(input_value, int):
            return str(input_value)
        else:
            logging.debug("Requested conversion of input type %s is not supported.", type(input))

    def _convertOutput2Value(self, output_value):
        """Converts an output of type list and length 1 containing strings to a value
        if value is a number."""
        try:
            return int(output_value[0])
        except:
            return output_value[0]  # return a string

    def _getStreakUnitTimeRangeChoices(self):
        """
        Get min and max values for exposure time. Values are in order. First to fourth values see CamParamInfoEx.
        :return: tuple containing min and max exposure time
        """
        choices_raw = self.parent.DevParamInfoEx(self.location, "Time Range")[6:]
        choices = []
        for choice in choices_raw:
            choice_raw = choice.split(" ")
            choices.append(self.parent.convertUnit2Time(choice_raw[0], choice_raw[1]))

        return choices

    def _getStreakUnitTimeRange(self):
        """Convert time range.
        :return: time range for one sweep in sec"""
        time_range_raw = self.parent.DevParamGet(self.location, "Time Range")[0].split(" ")
        time_range = self.parent.convertUnit2Time(time_range_raw[0], time_range_raw[1])

        return time_range

    def _setStreakUnitTimeRange(self, location, time_range):
        """Translate time range into a for RemoteEx readable format.
        :parameter location: (str) see DevParamGet
        :parameter time range (float): time range for one sweep"""
        try:
            time_range_raw = self.parent.convertTime2Unit(time_range)
        except Exception:
            raise logging.debug("Time range of %s sec for one sweep is not supported for streak unit." % time_range)

        self.parent.DevParamSet(location, "Time Range", time_range_raw)

    def terminate(self):
        self.MCPgain.value = 0
        self.mode = False


class DelayGenerator(model.HwComponent):
    """
    Represents delay generator.
    """

    def __init__(self, name, role, parent, location, **kwargs):
        super(DelayGenerator, self).__init__(name, role, parent=parent, **kwargs)  # init HwComponent

        self.parent = parent
        self.location = location

        self._hwVersion = parent.DevParamGet(location, "DeviceName")
        self._metadata[model.MD_HW_VERSION] = self._hwVersion
        # self._swVersion = ??
        # self._metadata[model.MD_SW_VERSION] = self._swVersion

        # Set parameters delay generator
        parent.DevParamSet(location, "Setting", "M1")  # TODO might be enough and don't need the rest...check!!
        parent.DevParamSet(location, "Trig. Mode", "Int.")  # TODO set to "Ext. rising" for SEM
        parent.DevParamSet(location, "Repetition Rate", "1000000")  # [0.001, 10000000] # read-only for Ext. rising
        parent.DevParamSet(location, "Delay A", "0")
        parent.DevParamSet(location, "Delay B", "0.00000002")
        parent.DevParamSet(location, "Burst Mode", "Off")

        self._metadata[model.MD_DELAY_A] = self.parent.DevParamGet(location, "Delay A")
        self._metadata[model.MD_DELAY_REPRATE] = self.parent.DevParamGet(location, "Repetition Rate")  # TODO check how to update!

        # VAs
        self.repetitionRate = model.VigilantAttribute(self.parent.DevParamGet(location, "Repetition Rate"),
                                                      readonly=True)

        triggerDelay = self._getTriggerDelay()
        range = self._getTriggerDelayTimeRange()
        self.triggerDelay = model.FloatContinuous(triggerDelay, range, setter=self._updateTriggerDelay)

        # TODO do we need: Burst Mode, Setting, Trig. Mode, delay B ??? as read only... plus MD!?
        # self.delayB = model.VigilantAttribute(self.parent.DevParamGet(location, "Delay B"), readonly=True)

    def _updateTriggerDelay(self, value):
        """
        update the mode VA
        """
        value_str = self._convertInput2Str(value)
        self.parent.DevParamSet(self.location, "Delay A", value_str)
        logging.debug("Reporting trigger delay %s for delay generator.", value)
        self._metadata[model.MD_DELAY_A] = value

        return value

    def _getTriggerDelayTimeRange(self):
        """
        Get the time range allowed for delay A. RemoteEx provides a negative minimum,
        which is internally set to zero whenever a negative delay is requested.
        """
        min_time = 0
        max_time = float(self.parent.DevParamInfoEx(self.location, "Delay A")[-1])
        range_time = (min_time, max_time)

        return range_time

    def _getTriggerDelay(self):
        """Get the value for the trigger delay (RemoteEx: delay A)."""
        triggerDelay_raw = self.parent.DevParamGet(self.location, "Delay A")
        triggerDelay = self._convertOutput2Value(triggerDelay_raw)

        return triggerDelay

    def _convertInput2Str(self, input_value):
        """Function that converts any input to a string as requested by RemoteEx."""
        if isinstance(input_value, str):
            return input_value
        elif isinstance(input_value, int):
            return str(input_value)
        elif isinstance(input_value, float):
            value = '{:.9f}'.format(input_value) # TODO check which precision needed
            # important remove all additional zeros: otherwise RemoteEx error!
            # TODO not nice I know...
            return "0" + value.strip("0")
        else:
            logging.debug("Requested conversion of input type %s is not supported.", type(input))

    def _convertOutput2Value(self, output_value):
        """Converts an output of type list and length 1 containing strings to a value
        if value is a number."""
        try:
            return float(output_value[0])
        except:  # TODO ValueError?
            return output_value[0]  # return a string

    def terminate(self):
        """nothing to do here"""
        pass


class StreakCamera(model.HwComponent):
    """
    Represents Hamamatsu readout camera for the streak unit.
    Client to connect to HPD-TA software via RemoteEx.
    """

    def __init__(self, name, role, children=None, port=None, host=None, **kwargs):
        """
        Initializes the device.
        host (str): hostname or IP-address
        port (int or None): port number for sending/receiving commands (None if not set)
        """
        super(StreakCamera, self).__init__(name, role, **kwargs)

        if port is None:
            raise ValueError("Please specify port of camera to be used.")
        if host is None:
            raise ValueError("Please specify host to connect to.")

        port_d = port + 1  # the port number to receive the image data
        self.host = host
        self.port = port
        self.port_d = port_d

        self._lock_command = threading.Lock()

        # TODO start RemoteEx via SSH
        # or TODO autostart of RemoteEx when turning on hamamatsu pc?

        # connect to readout camera
        try:
            # initialize connection with RemoteEx client
            self._commandport, self._dataport = self._openConnection()
        except Exception:
            raise logging.error("Failed to initialise Hamamatsu readout camera.")
            # TODO errors
            # if isinstance(exp, ATError):
            #     if exp.errno == 6: # OUTOFRANGE
            #         raise HwError("Failed to find Andor camera %d, check that it "
            #                       "is turned on and connected to the computer." %
            #                       device)
            #     elif exp.errno in (10, 38): # CONNECTION, DEVICEINUSE
            #         raise HwError("Failed to initialise Andor camera %d, try to "
            #                       "turning it off, waiting for 10 s and turning "
            #                       "in on again." % device)
            # raise
        # if device is None:
        #     # nothing else to initialise
        #     return  # TODO  don't need this maybe

        # a variable that stores the current Time Range unit for e.g. the scaling table conversion
        # is set in the setter of the timeRange VA
        self.timeRangeConversionFactor = None

        # collect responses (EC = 0-3,6-10) from commandport
        self.queue_command_responses = Queue.Queue(maxsize=0)
        # save messages (EC = 4,5) from commandport
        self.queue_img = Queue.Queue(maxsize=0)

        self.should_listen = True  # used in readCommandResponse thread
        self._waitForCorrectResponse = True  # used in sendCommand

        # start thread, which keeps reading the commandport response continuously
        self._start_receiverThread()

        # Note: start HPDTA after initializing queue and command and receiver treads
        # but before image thread and initializing children!

        # TODO check if already running....otherwise start multiple apps
        # TODO  -> in acquisition mode it looks like it does not start a second app, but also does not report that
        # TODO -> in processing mode it is possible to start multiple apps....
        # TODO find out where to ask for acq or processing mode
        # TODO is there a clever way for checking if app still running? Seems to be no command available to check
        # TODO appEnd only works for the last opened window
        # TODO want to check if we want to start app invisible (sVisible = False)
        self.timeout_commandport = 15  # need a long timeout for starting App as it takes a while
        # self.AppStart() # start HPDTA software  # TODO for testing in order to not start a new App
        self.timeout_commandport = 5  # new timeout for standard commands
        #  TODO might be other commands also needing a longer timeout

        # # TODO move maybe to readoutcam
        # # start thread, which keeps reading the dataport when an image/scaling table has arrived
        # # after commandport thread to be able to set the RingBuffer
        # # AcqLiveMonitor writes images to Ringbuffer, which we can read from
        # # only works if we use "Live" or "SingleLive" mode
        # self.AcqLiveMonitor("RingBuffer", "10")  # TODO need to be handled in case we use other acq modes
        # self.t_image = threading.Thread(target=self._getDataFromBuffer)
        # self.t_image.start()

        if children:
            try:
                kwargs = children["readoutcam"]
            except Exception:
                raise
            self._readoutcam = OrcaFlash(parent=self, **kwargs)
            self.children.value.add(self._readoutcam)  # add readoutcam to children-VA
            try:
                kwargs = children["streakunit"]
            except Exception:
                raise
            self._streakunit = StreakUnit(parent=self, **kwargs)
            self.children.value.add(self._streakunit)  # add streakunit to children-VA
            try:
                kwargs = children["delaybox"]
            except Exception:
                raise
            self._delaybox = DelayGenerator(parent=self, **kwargs)
            self.children.value.add(self._delaybox)  # add delaybox to children-VA

        # TODO move maybe to readoutcam
        # Note: needs to be after initializing children and after commandport thread to be able to set the RingBuffer
        # start thread, which keeps reading the dataport when an image/scaling table has arrived
        # AcqLiveMonitor writes images to Ringbuffer, which we can read from
        # only works if we use "Live" or "SingleLive" mode
        self.AcqLiveMonitor("RingBuffer", "10")  # TODO need to be handled in case we use other acq modes
        self.t_image = threading.Thread(target=self._getDataFromBuffer)
        self.t_image.start()

    def _openConnection(self):
        """
        open connection with RemoteEx client.
        :parameter host: IP-adress or hostname
        :parameter port: port for sending/receiving commands
        :parameter port_d: port for reading images
        return: connection to RemoteEx command and data port
        """
        # connect to sockets
        try:
            self._commandport = socket.create_connection((self.host, self.port), timeout=5)
            self._dataport = socket.create_connection((self.host, self.port_d), timeout=5)
        except socket.timeout as msg:
            raise model.HwError(msg, "Failed to connect to '%s using port %d'. Check the server "
                                "is connected to the network, turned "
                                " on, and correctly configured." % (self.host, self.port))
        except socket.error as msg:
            raise model.HwError(msg, "Failed to connect to '%s:%d'. Check ...." % (self.host, self.port))

        # check if connection returns correct response
        try:
            message = self._commandport.recv(self.port)
            if message != 'RemoteEx Ready\r':
                raise ValueError("Connection to port %s not successfull. "
                                 "Response %s from server is not as expected." % (self.port, message))
        except socket.timeout as msg:
            raise model.HwError(msg, "Failed to receive response from '%s:%d'. Check ..." % (self.host, self.port))

        try:
            message_d = self._dataport.recv(self.port_d)
            if message_d != 'RemoteEx Data Ready\r':
                raise ValueError("Connection to port %s not successfull. "
                                 "Response %s from server is not as expected." % (self.port_d, message))
        except socket.timeout as msg:
            raise model.HwError(msg, "Failed to receive response from '%s:%d'. Check ..." % (self.host, self.port_d))

        # set timeout
        self._commandport.settimeout(1.0)
        self._dataport.settimeout(5.0)

        return self._commandport, self._dataport

    def _start_receiverThread(self):
        """Start the receiver thread, which keeps listening to the response of the command port."""
        self.t_receiver = threading.Thread(target=self.readCommandResponse)
        self.t_receiver.start()

    def _closeConnection(self):
        """
        Close connection to RemoteEx.
        """
        self._commandport.close()
        self._dataport.close()

    def terminate(self):
        """
        Close App (HPDTA) and RemoteEx and close connection to RemoteEx. Called by backend.
        """
        # terminate image thread
        if self.t_image.isAlive():
            self.queue_img.put(None)
            self.t_image.join(5)
        # terminate children
        for child in self.children.value:
            child.terminate()

        self.should_listen = False  # terminates receiver thread
        if self.t_receiver.isAlive():
            self.t_receiver.join(5)
        self._closeConnection()

    def sendCommand(self, func, *args):
        """
        Sends a command to RemoteEx.
        :parameter func: (str) command or function, which should be send to RemoteEx
        :parameter args: (str) optional parameters allowed for function
        :raise:
           HwError: if error communicating with the hardware, probably due to
              the hardware not being in a good state (or connected)
           IOError: if error during the communication (such as the protocol is
              not respected)
        """
        command = "%s(%s)\r" % (func, ",".join(args))

        last_error_code = "None"
        last_error_fct = "None"
        last_error_msg = "None"

        with self._lock_command:  # lock this code, when finished lock is automatically released

            # send command to socket
            try:
                logging.debug("Sending: '%s'", command.encode('string_escape'))
                self._commandport.send(command)
            except Exception:  # TODO what type of exception?
                try:  # try to reconnect if connection was lost
                    self._commandport, self._dataport = self._openConnection()
                    # restart receiver thread, which keeps reading the commandport response continuously
                    self._start_receiverThread()
                    logging.debug("Sending: '%s'", command.encode('string_escape'))
                    self._commandport.send(command)
                except (socket.error, socket.timeout) as err:
                    raise model.HwError(err, "Could not connect to RemoteEx.")

            while self._waitForCorrectResponse:  # wait for correct response until Timeout
                try:
                    # if not receive something after timeout
                    response = self.queue_command_responses.get(timeout=self.timeout_commandport)
                except Queue.Empty:
                    # log the last error code received before timeout
                    logging.error("Last error code for function %s before timeout was %s with message %s."
                                  % (last_error_fct, last_error_code, last_error_msg))
                    raise util.TimeoutError("No answer received after %s sec for command %s."
                                            % (self.timeout_commandport, command.encode('string_escape')))

                try:
                    EC, rfunc, rargs = int(response[0]), response[1], response[2:]
                except Exception:
                    raise IOError("Received response, which is not according to the known protocol.")

                # check if the response corresponds to the command sent before
                # the response corresponding to a command always also includes the command name
                if rfunc.lower() == func.lower() and EC == 0:  # fct name not case sensitive
                    logging.debug("Hamamatsu streak camera RemoteEx response: %s." % response)
                    return rargs  # successfully executed command and return message
                elif rfunc.lower() == func.lower() and EC != 0:  # response corresponds to command, but an error occured
                    logging.error(RemoteExError(EC))
                    logging.error("Hamamatsu streak camera RemoteEx error response: %s." % response)
                    raise RemoteExError(EC)
                else:
                    # save the last error message and code in case we don't receive any other response before timeout
                    last_error_code = EC
                    last_error_fct = rfunc
                    last_error_msg = rargs
                    logging.debug("Hamamatsu streak camera RemoteEx error response not as expected. "
                                  "Will wait some more time.")
                    continue  # continue listening to receive the correct response for the sent command or timeout
                    # TODO if for some reason there was still something in the buffer, it will raise
                    # TODO an exception though the correct answer might be also in the buffer

    def readCommandResponse(self):
        """This method runs in a separate thread and continuously listens for messages returned from
        the device via the commandport IP socket."""
        try:
            responses = ""  # received data not yet processed

            while self.should_listen:
                try:
                    returnValue = self._commandport.recv(4096)  # buffersize should be small value of power 2 (4096)
                except socket.timeout:
                    # when socket timed out (receiving no response)
                    logging.debug("Timeout on the socket, will wait for more data packages.")
                    continue

                responses += returnValue

                resp_splitted = responses.split("\r")
                # split responses, overwrite var responses with the remaining messages (usually empty)
                resp_splitted, responses = resp_splitted[:-1], resp_splitted[-1]

                for msg in resp_splitted:
                    msg_splitted = msg.split(",")

                    try:
                        EC, rfunc, rargs = int(msg_splitted[0]), msg_splitted[1], msg_splitted[2:]
                    except (TypeError, ValueError, IOError):
                        logging.warning("Received response, which is not according to the known protocol.")
                        continue  # return to try-statement and start receiving again

                    # continue listening as there is additional info in coming

                    # TODO
                    # problem for e.g. parent.CamParamGet("Setup", "CameraInfo")
                    # nasty trick to work around for this command TODO are there more cases like that??
                    # This command is nasty as it first receives the EC and then additional information
                    # if rfunc == "CamParamGet":
                    #     # TODO make a dict to extract firmware version
                    #     # TODO need something that waits until all lines are received..
                    #     i = 0
                    #     while i < 4:
                    #         additional_info = self._commandport.recv(4096)  # receive more data
                    #         additional_info = additional_info.split("\r")
                    #         additional_info = additional_info[:-1]
                    #         for i, item in enumerate(additional_info):
                    #             additional_info[i] = item.replace("\n", "")
                    #         rargs.append(additional_info)
                    #         i += 1
                    #     # rargs = list(rargs) + additional_info

                    if EC in (4, 5):
                        logging.debug("Received message %s from RemoteEx software." % rargs)
                        if EC == 4 and rfunc == "Livemonitor":
                            # if len(rargs) > 0: # TODO maybe check if rargs is empty, should not for type 4 and 5 i think
                            self.queue_img.put(rargs)  # only put msg in queue when it notifies about an image

                    else:  # send response including EC to queue
                        self.queue_command_responses.put(msg_splitted)

        except Exception:
            logging.exception("Hamamatsu streak camera TCP/IP receiver thread failed.")
        finally:
            logging.info("Hamamatsu streak camera TCP/IP receiver thread ended.")

    def updateMetadata(self, md):
        """Create dict containing all metadata from the children readout camera, streak unit, delay genereator
        and the metadata from the parent streak camera."""

        md_children = [self._readoutcam._metadata, self._streakunit._metadata, self._delaybox._metadata]

        for md_dict in md_children:
            # TODO if key exists, append string e.g. for HW version
            md.update(md_dict)

        return md

    def _getDataFromBuffer(self):
        """This method runs in a separate thread and waits for messages in queue indicating
        that some data was received. The image is then received from the device via the dataport IP socket or
        the vertical scaling table is received, which corresponds to a time range for a single sweep.
        It corrects the vertical time information. The table contains the actual timestamps for each px.."""

        # TODO need to check that there is an ringbuffer available!?
        # TODO move fct to readoutcam see comment readoutcam

        logging.debug("Starting data thread.")
        is_receiving_image = False

        try:
            while True:

                if self._readoutcam._sync_event and not is_receiving_image:
                    while int(self.AsyncCommandStatus()[0]):
                        time.sleep(0)
                        logging.debug("Asynchronous RemoteEx command still in process. Wait until finished.")
                        # TODO if not finished after some time might be live mode, so StopAcq again
                    try:
                        event_time = self._readoutcam.queue_events.popleft()
                        logging.warning("Starting acquisition delayed by %g s.", time.time() - event_time)
                        self.AcqStart(self._readoutcam.acqMode)
                        is_receiving_image = True
                    except IndexError:
                        # No event (yet) => fine
                        pass

                rargs = self.queue_img.get(block=True)  # block until receive something
                logging.debug("Received message %s", rargs)

                if rargs is None:  # if message is None end the thread
                    return

                # synchronized mode
                if self._readoutcam._sync_event:
                    if rargs == "start":
                        logging.info("Received event trigger")
                        continue
                    else:
                        logging.info("Get the synchronized image.")

                # non-sync mode
                else:
                    while not self.queue_img.empty():
                        # keep reading to check if there might be a newer image for display
                        # in case we are too slow with reading
                        rargs = self.queue_img.get(block=False)

                        if rargs is None:  # if message is None end the thread
                            return
                    logging.info("No more images in queue, so get the image.")

                if rargs == "F":  # Flush => the previous images are from the previous acquisition
                    logging.debug("Acquisiton was stopped so flush previous images.")
                    continue

                self._metadata[model.MD_ACQ_DATE] = time.time()
                # TODO more fancy maybe? metadata[model.MD_ACQ_DATE] = time.time() - (exposure_time + readout_time)

                # get the image from the buffer
                img_num = rargs[1]
                img_info = self.ImgRingBufferGet("Data", img_num)

                # can be NoneType if img_num to high!!
                # TODO looks like img_info can be empty/None type object -> need a check here?
                # TODO can it be empty? Should there not first the Live acq start, and then getImage as we have the
                # TODO lock for sending commands

                img_size = int(img_info[0]) * int(img_info[1]) * 2  # num of bytes we need to receive #TODO why 2
                img_num_actual = img_info[4]

                img = ""
                try:
                    while len(img) < img_size:  # wait until all bytes are received
                        img += self._dataport.recv(img_size)
                except socket.timeout as msg:
                    logging.error("Did not receive an image: %s", msg)
                    continue

                image = numpy.frombuffer(img, dtype=numpy.uint16)  # convert to array
                image.shape = (int(img_info[1]), int(img_info[0]))

                logging.debug("Requested image number %s, received image number %s from buffer."
                              % (img_num, img_num_actual))

                # get the scaling table to correct the time axis
                # TODO only request scaling table if corresponding MD not available for this time range
                if self._streakunit.mode.value:
                    # TODO some sync problem might be here if a different command is in queue
                    # in between ImgRingBufferGet and ImgDataGet: check again!
                    scl_table_info = self.ImgDataGet("current", "ScalingTable", "Vertical")  # request scaling table

                    scl_table_size = int(scl_table_info[0]) * 4  # num of bytes we need to receive

                    # receive the bytes via the dataport
                    tab = ''
                    try:
                        while len(tab) < scl_table_size:  # keep receiving bytes until we received all expected bytes
                            tab += self._dataport.recv(scl_table_size)
                            table = numpy.frombuffer(tab, dtype=numpy.float32)  # convert to array
                            table_converted = table * self.timeRangeConversionFactor  # convert to sec
                            self._readoutcam._metadata[model.MD_TIME_LIST] = table_converted
                    except socket.timeout as msg:
                        logging.error("Did not receive a scaling table: %s", msg)
                        continue
                else:
                    if model.MD_TIME_LIST in self._readoutcam._metadata.keys():
                        self._readoutcam._metadata.pop(model.MD_TIME_LIST, None)

                md = dict(self._metadata)  # make a copy of md dict so cannot be accidentally changed
                self.updateMetadata(md)  # merge dict
                dataarray = model.DataArray(image, md)
                self._readoutcam.data.notify(dataarray)  # pass the new image plus MD to the callback fct

                if self._readoutcam._sync_event:
                    is_receiving_image = False

        except Exception:
            logging.exception("Hamamatsu streak camera TCP/IP image thread failed.")
        finally:
            logging.info("Hamamatsu streak camera TCP/IP image thread ended.")

    def StartAcquisition(self, AcqMode):
        """Start an acquisition.
        :parameter AcqMode: (str) see AcqStart
        """
        # Note: sync acquisition calls directly AcqStart

        # TODO this is not a nice solution but seems to work for now
        try:
            self.AcqStart(AcqMode)
        except Exception:  # TODO RemoteEx error not catched...
            logging.debug("Starting acquisition currently not possible. An acquisition or live mode might be still "
                          "running. Will stop and restart live mode.")
            self.AcqStop()
            timestamp = time.time()
            timeout = 5
            while int(self.AsyncCommandStatus()[0]):
                time.sleep(0)
                timestamp += time.time()
                if timestamp > timeout:
                    logging.error("Could not start acquisition.")
                    return
            self.AcqStart(AcqMode)

        # first idea always stop acquisition and restart!
        # while int(self.AsyncCommandStatus()[1]):  # action preparing
        #     logging.debug("Already an acquisition in preparation. Wait until started.")
        #     logging.debug("Asynchronous command status is %s." % self.AsyncCommandStatus()[0])
        # if int(self.AsyncCommandStatus()[2]):  # action active (acquisition running)
        #     logging.debug("Acquisition already running. "
        #                   "Stop acquisition and restart with new acquisition mode %s." % AcqMode)
        #     # Acq stop also seems to reset the buffer counting
        #     self.AcqStop()
        # # wait until action is finished
        # while int(self.AsyncCommandStatus()[0]):  # action pending (if True still acquisition running)
        #     logging.debug("Wait until previous acquisition is finished properly.")
        #
        # self.AcqStart(AcqMode)

        # second solution keep live when already running. however, not clear if status shows live or singlelive
        # also not clear why self.AsyncCommandStatus()[1] can be True again ....see below...
        # TODO maybe in some situations it is not wanted to stop a running acq aka "Live"
        # For now we just stop and restart the acquisition as we only use "Live" and "SingleLive" mode.

        # if we only use "Live" mode here and "SingleLive" mode in sync acquisition we can be sure
        # that the current acq is already the "Live" mode here. If we plan to use other acquisition
        # modes, we need to stop an ongoing acquisition or check it has finished before starting a new one.
        # while int(self.AsyncCommandStatus()[1]):  # action preparing
        #     logging.debug("Already an acquisition in preparation. Wait until started.")
        #
        # # TODO can happen that action preparing is True now...which is weired....
        # if int(self.AsyncCommandStatus()[2]):  # action active (acquisition running)
        #     logging.debug("Acquisition already running.")
        # else:
        #     self.AcqStart(AcqMode)

    # === General commands ============================================================

    def Status(self):
        """Returns whether or not a command is currently executed."""
        return self.sendCommand("Status")

    def Appinfo(self):
        """Returns the current application type. Can be executed even if application (HPDTA or HiPic)
        have not been started yet."""
        return self.sendCommand("Appinfo", "type")

    def Stop(self):
        """Stops the command currently executed if possible.
        (Few commands have implemented this command right now)."""
        self.sendCommand("Stop")

    def Shutdown(self):
        """Shuts down the application and the RemoteEx program.
        The usefulness of this command is limited because it cannot be sent once the application has been
        hang up. Restarting of the remote application if an error has occurred should be done by other
        means (example: Power off and on the computer from remote and starting the RemoteEx from the
        autostart)."""
        self.sendCommand("Shutdown")

    # === Application commands ========================================================

    def AppStart(self):
        """Start RemoteEx. Function names and args need to be strings."""
        logging.debug("Starting RemoteEx App.")
        # TODO think about where we want to specify the ini-file!
        # "1": App starts visible (use 0 for invisible)
        # returnValue = self.sendCommand("AppStart", "1", "C:\ProgramData\Hamamatsu\HPDTA\HPDTA8.ini")
        self.sendCommand("AppStart")

    def AppEnd(self):
        """Close RemoteEx."""
        logging.debug("Closing RemoteEx App.")
        self.sendCommand("AppEnd")

    def AppInfo(self, parameter):
        """Returns information about the application.
        :parameter paramter (str): Date, Version, Directory, Title, Titlelong, ProgDataDir.
        :return (str): message"""
        return self.sendCommand("AppInfo", parameter)

    def AsyncCommandStatus(self):
        """Returns information whether an asynchronous command is currently running.
        :returns: [iPending, iPreparing, iActive]
            iPending: Command is pending (iPending= iPreparing or iActive)
            iPreparing: Command has been issued but not started
            iActive: Command is executed
            sCommand: Command name (if any)"""
        return self.sendCommand("AsyncCommandStatus")

    def AppLicenceGet(self):
        """Returns information about implemented license keys at the application.
        Note: The result of every key is either 0 (not licence) or 1 (licence found).
        :returns: ApplicationKeyFound,LicenceAcquire,LicenceSave,
                  LicenceFitting,LicencePhotonCorr,LicenceTransAbs"""
        return self.sendCommand("AppLicenceGet")

    def MainParamGet(self, parameter):
        """Returns the values of parameters visible in the main window.
        :parameter parameter: (str) ImageSize, Message, Temperature, GateMode, MCPGain, Mode, Plugin, Shutter, StreakCamera, TimeRange.
        :returns: Current value of parameter."""
        return self.sendCommand("MainParamGet", parameter)

    def MainParamInfo(self, parameter):
        """Returns information about parameters visible in the main window.
        :parameter parameter: (str) ImageSize, Message, Temperature, GateMode, MCPGain, Mode, Plugin, Shutter, StreakCamera, TimeRange
        :returns: Label, Current value, Param type
                (Param type 5 (=Display): A string which is displayed only (read only))."""
        return self.sendCommand("MainParamInfo", parameter)

    def MainParamInfoEx(self, parameter):
        """Returns information about parameters visible in the main window. Returns more detailed information in
        case of a list parameter (Parameter type = 2) than MainParamInfo.
        :parameter parameter: (str) see _mainParamInfo
        :returns: Label, Current value, Param type"""
        return self.sendCommand("MainParamInfoEx", parameter)

    def MainParamList(self):
        """Returns a list of all parameters related to main window.
        This command can be used to build up a complete parameter list related to main window at runtime.
        :returns: NumberOfParameters,Parameter1,..., ParameterN"""
        return self.sendCommand("MainParamList")

    def MainSyncGet(self):
        """Returns the setting of the sync parameter which is available on the HPD-TA main window.
        This command can be used to build up a complete parameter list related to main window at runtime.
        :returns: DoSync, CanSync, IsVisible, Label
            DoSync: 0 or 1 indication whether Sync is switched on or off.
            CanSync: Indicates whether it is possible to switch on or off sync
            IsVisible: The Controls to switch on or off sync are visible
            Note: Actual synchronisation takes only place if all three parameters show 1
            Label: The label which can be read on the toolbar"""
        return self.sendCommand("MainSyncGet")

    def MainSyncSet(self, iSwitch):
        """Allows to switch the sync parameter which is available on the HPD-TA main window.
        :parameter iSwitch (int):0 to switch sync off, 1 to switch sync on."""
        self.sendCommand("MainSyncSet", iSwitch)

    def GenParamGet(self, parameter):
        """Returns the values of parameters in the general options.
        :parameter parameter: (str) RestoreWindowPos: Restore window positions
                    UserFunctions: Call user functions
                    ShowStreakControl: Shows or hides the Streak status/control dialog
                    ShowDelay1Control: Shows or hides the Delay1 status/control dialog
                    ShowDelay2Control: Shows or hides the Delay2 status/control dialog
                    ShowSpectrControl: Shows or hides the Spectrograph status/control dialog"""
        self.sendCommand("GenParamGet", parameter)

    def GenParamSet(self, parameter, value):
        """Returns the setting of the sync parameter which is available on the HPD-TA main window.
        :parameter parameter: (str) RestoreWindowPos: Restore window positions
                    UserFunctions: Call user functions
                    ShowStreakControl: Shows or hides the Streak status/control dialog
                    ShowDelay1Control: Shows or hides the Delay1 status/control dialog
                    ShowDelay2Control: Shows or hides the Delay2 status/control dialog
                    ShowSpectrControl: Shows or hides the Spectrograph status/control dialog
        :parameter value: (str) TODO which values?? 0 and 1??."""
        self.sendCommand("GenParamSet", parameter, value)

    def GenParamInfo(self, parameter):
        """Returns information about the specified parameter.
        :parameter parameter: (str) RestoreWindowPos: Restore window positions
                    UserFunctions: Call user functions
                    ShowStreakControl: Shows or hides the Streak status/control dialog
                    ShowDelay1Control: Shows or hides the Delay1 status/control dialog
                    ShowDelay2Control: Shows or hides the Delay2 status/control dialog
                    ShowSpectrControl: Shows or hides the Spectrograph status/control dialog
        :returns: Label, Current value (TODO values???), Param Type
                    Param type (boolean): True or False
                                (Valid entries are „true“ (true), „false“ (false),
                                „on“ (true), „off“ (false), „yes“ (true), „no“ (false), „0“
                                (false), or any other numerical value (true).
                                As output only 0 (false) and 1 (true) are used.)."""
        return self.sendCommand("GenParamInfo", parameter)

    def GenParamInfoEx(self, parameter):
        """Returns the information about the specified parameter. Returns more detailed information
        in case of a list parameter (Parameter type = 2) than GenParamInfo.
        :parameter parameter: (str) see GenParamInfo
        :returns: Label, Current value (TODO values???), Param Type
                    Param type (boolean): see GenParamInfo"""
        return self.sendCommand("GenParamInfoEx", parameter)

    def GenParamsList(self):
        """Returns a list of all parameters related to the general options.
        :returns: NumberOfParameters,Parameter1,..., ParameterN."""
        return self.sendCommand("GenParamsList")

    # === Acquisition commands ========================================================

    def AcqStart(self, AcqMode):
        """Start an acquisition.
        :parameter AcqMode: (str) Live: Live mode
                      SingleLive: Live mode (single exposure)
                      Acquire: Acquire mode
                      AI: Analog integration
                      PC: Photon counting"""
        if not self.t_image.isAlive():  # restart thread in case it was terminated
            self.AcqLiveMonitor("RingBuffer", "10")
            self.t_image = threading.Thread(target=self._getDataFromBuffer)
            self.t_image.start()
        self.sendCommand("AcqStart", AcqMode)

    def AcqStatus(self):
        """Returns the status of an acquisition.
        :return: status, mode"""
        return self.sendCommand("AcqStatus")

    def AcqStop(self, *args):
        """Stops the currently running acquisition.
        :parameter args: (str) Optional parameter indicating the timeout value (in ms) until this command
        should wait for an acquisition to end.
        range: [1...60000]
        default: 1000
        :return: timeout if args"""
        return self.sendCommand("AcqStop", *args)

    def AcqParamGet(self, parameter):
        """Returns the values of the acquisition options.
        :parameter parameter: (str)
            DisplayInterval: Display interval in Live mode
            32BitInAI: Creates 32 bit images in Analog integration mode
            WriteDPCFile: Writes dynamic photon counting file
            AdditionalTimeout: Additional timeout
            DeactivateGrbNotInUse: Deactivate the grabber while not in use
            CCDGainForPC: Default setting for photon counting mode
            32BitInPC: Create 32 bit images in Photon counting mode
            MoireeReduction: Strength of Moiré reduction
            PCMode: Photon counting mode
        :return: value for parameter"""
        return self.sendCommand("AcqParamGet", parameter)

    def AcqparameterSet(self, parameter, value):
        """Set the values of the acquisition options.
        :parameter parameter: (str) see AcqParamGet
        :parameter value: (str) value to set for parameter"""
        self.sendCommand("AcqParamSet", parameter, value)

    def AcqParamInfo(self, parameter):
        """Returns information about the specified parameter.
        :parameter parameter: (str) see AcqParamGet
        :return: Label, current value, param type, min (num type only), max (num type only)
            0= Boolean: Can have the values true or false. Valid entries are „true“ (true), „false“
                (false), „on“ (true), „off“ (false), „yes“ (true), „no“ (false), „0“ (false), or
                any other numerical value (true). On output only 0 (false) and 1 (true) is
                used.
            1= Numeric: A numerical value. In the case of a numerical value the minimum and
                maximum value is returned.
            2= List: The value is one entry in a list.
            3=String: Any string can be used.
            4= ExposureTime: An expression which evaluates to a time like „5ms“, „1h“, „1s“ etc. Valid
                units are ns (nanosecond), us (microsecond), ms (millisecond), s (second), m
                (minute), h(hour).
            5=Display: A string which is displayed only (read only).
            """
        return self.sendCommand("AcqParamInfo", parameter)

    def AcqParamInfoEx(self, parameter):
        """Returns information about the specified parameter. Returns more detailed information in case of a list
        parameter (Parameter type = 2) than AcqParamInfo. In case of a numeric parameter (Parameter
        type = 1) it additionally returns the step width
        :parameter parameter: (str) see AcqParamGet
        :return: Label, current value, param type, min (num type only), max (num type only)
        Note: For param type see AcqParamInfo.
        Note: In case of a list or an exposure time the number of entries and all list entries are returned in
        the response of the AcqParamInfoEx command. In case of a numeric parameter (Parameter type =
        1) it additionally returns the step width
            """
        return self.sendCommand("AcqParamInfoEx", parameter)

    def AcqParamsList(self):
        """Returns a list of all parameters related to acquisition. This command can be used to build up
         a complete parameter list related to acquisition at runtime.
        :return: NumberOfParameters,Parameter1,..., ParameterN"""
        return self.sendCommand("AcqParamsList")

    def AcqLiveMonitor(self, monitorType, *args):
        """Starts a mode which returns information on every new image acquired in live mode.
        Once this command is activated, for every new live image a message is returned.
        :parameter monitorType: (str)
            Off: No messages are output. This setting can be used to stop live monitoring.
            Notify: A message is sent with every new live image. No other information is
                    attached. The message can then be used to observe activity or to get
                    image or other data explicitly.
            NotifyTimeStamp: A message is sent with every new live image. The message
                    contains the timestamp of the image when it was acquired in ms.
            RingBuffer: The data acquired in Live mode is written to a ring buffer inside the
                    RemoteEx application. A message is sent with every new live image.
                    This message contains a sequence number. The imgRingBufferGet
                    command can be used to get the data associated to the specified
                    sequence number. Please see also the description of the
                    ImgRingBufferGet command and the description of the sample client program.
            Average: Returns the average value within the full image or a specified area.
            Minimum: Returns the minimum value within the full image or a specified area.
            Maximum: Returns the maximum value within the full image or a specified area.
            Profile: Returns a profile extracted within the full image or a specified area in text form.
            PCMode: Photon counting mode
        :parameter args: (str)
            NumberOfBuffers (MonitorType=RingBuffer): Specifies the number of buffers allocated inside the RemoteEx.
            FullArea (MonitorType=Average/Minimum/Maximum): The specified calculation algorithm is performed
                        on the full image area.
            Subarray,X,Y,DX,DY (MonitorType=Average/Minimum/Maximum): The specified calculation algorithm is performed
                        on a sub array specified by X (X-Offset), Y (Y-Offset), DX, (Image width) and DY (Image height).
            ProfileType,FullArea (MonitorType=Profile): The profile is extracted from the full image area.
                        1=Line profile
                        2=Horizontal profile (integrated)
                        3=Vertical profile (integrated)
            ProfileType,Subarray,X,Y,DX,DY (MonitorType=Profile): The profile is extracted from a subarray
                        specified by X (X-Offset), Y (Y-Offset), DX (Image width) and DY (Image height).
        Note: For examples see page 20 RemoteExProgrammerHandbook.
        :return: msg"""
        return self.sendCommand("acqLiveMonitor", monitorType, *args)

    def AcqLiveMonitorTSInfo(self):
        """Correlates the current time with the timestamp. It outputs the current time and the time stamp.
        With this information the real time for any other time stamp can be calculated.
        :return: current time, timestamp"""
        return self.sendCommand("AcqLiveMonitorTSInfo")

    def AcqLiveMonitorTSFormat(self, format):
        """Sets the format of the time stamp.
        :parameter format: (str) Timestamp (default): In msec from start of pc.
                        DateTime: yyyy/mm:dd-hh-ss
                        Unix or Linux: Seconds and μseconds since 01.01.1070"""
        self.sendCommand("AcqLiveMonitorTSFormat", format)

    def AcqAcqMonitor(self, type):
        """Starts a mode which returns information on every new image or part image acquired in
        Acquire/Analog Integration or Photon counting mode (Acquisition monitoring).
        :parameter type: (str)
                    Off: No messages are output. This setting can be used to stop acquisition monitoring.
                    EndAcq: For every new part image a message is output. A part is a single image which
                            contributes to a full image. For example in Analog Integration or Photon counting
                            mode several images are combined to give one resulting image.
                    All: For every new image or every new part a message is output.
        :return: msg"""
        return self.sendCommand("AcqAcqMonitor", type)

    # === Camera commands ========================================================

    def CamParamGet(self, location, parameter):
        """Returns the values of the camera options.
        :parameter location: (str)
                    Setup: Parameters on the options dialog.
                    Live: Parameters on the Live tab of the acquisition dialog.
                    Acquire: Parameters on the Acquire tab of the acquisition dialog.
                    AI: Parameters on the Analog Integration tab of the acquisition dialog.
                    PC: Parameters on the Photon counting tab of the acquisition dialog.
        :parameter parameter: (str) (Which of these parameters are relevant is dependent on
                                the camera type. Please refer to the camera options dialog)
                    === Setup (options) parameter===  (Settings to be found in "Options" and not GUI
                    TimingMode: Timing mode (Internal / External) # TODO exists
                    TriggerMode: Trigger mode  # TODO exists
                    TriggerSource: Trigger source  # TODO exists
                    TriggerPolarity: Trigger polarity  # TODO exists
                    ScanMode: Scan mode  # TODO exists
                    Binning: Binning factor  # TODO exists
                    CCDArea: CCD area
                    LightMode: Light mode
                    Hoffs: Horizontal Offset (Subarray)
                    HWidth: Horizontal Width (Subarray)  # TODO exists
                    VOffs: Vertical Offset (Subarray)
                    VWidth: Vertical Width (Subarray)  # TODO exists
                    ShowGainOffset: Show Gain and Offset on acquisition dialog  # TODO exists
                    NoLines: Number of lines (TDI mode)
                    LinesPerImage: Number of lines (TDI mode)
                    ScrollingLiveDisplay: Scrolling or non scrolling live display
                    FrameTrigger: Frame trigger (TDI or X-ray line sensors)
                    VerticalBinning: Vertical Binning (TDI mode)
                    TapNo: Number of Taps (Multitap camera)
                    ShutterAction: Shutter action
                    Cooler: Cooler switch
                    TargetTemperature: Cooler target temperature
                    ContrastEnhancement: Contrast enhancement
                    Offset: Analog Offset
                    Gain: Analog Gain
                    XDirection: Pixel number in X direction
                    Offset: Vertical Offset in Subarray mode
                    Width: Vertical Width in Subarray mode
                    ScanSpeed: Scan speed
                    MechanicalShutter: Behavior of Mechanical Shutter
                    Subtype: Subtype (X-Ray Flatpanel)
                    AutoDetect: Auto detect subtype
                    Wait2ndFrame: Wait for second frame in Acquire mode
                    DX: Image Width (Generic camera)
                    DY: Image height (Generic camera)
                    XOffset: X-Offset (Generic camera)
                    YOffset: Y-Offset (Generic camera)
                    BPP: Bits per Pixel(Generic camera)
                    CameraName: Camera name (Generic camera)
                    ExposureTime: Exposure time (Generic camera)
                    ReadoutTime: Readout time Generic camera)
                    OnChipAmp: On chip amplifier
                    CoolingFan: Cooling fan
                    Cooler: Coolier
                    ExtOutputPolarity: External output polarity
                    ExtOutputDelay: External output delay
                    ExtOutputWidth: External output width
                    LowLightSensitivity: Low light sensitivity
                    TDIMode: TDI Mode
                    BinningX: Binning X direction
                    BinningY: Binning Y direction
                    AreaExposureTime: Exposure time in area mode
                    Magnifying: Use maginfying geometry
                    ObjectDistance: Object Distance
                    SensorDistance: Sensor Distance
                    ConveyerSpeed: Conveyer speed
                    LineSpeed: Line speed
                    LineFrequency: Line frequence
                    ExposureTime: Exposure time in line scan mode
                    DisplayDuringMeasurement: Display during measurement option
                    GainTable: Gain table
                    NoOfTimesToCheck: Number of times to check
                    MaximumBackgroundLevel: Maximum background level
                    MinimumSensitivityLevel: Maximum sensitivity level
                    Fluctuation: Fluctuation
                    NoOfIntegration: Number of Integration
                    DualEnergyCorrection: Dual energy correction method
                    LowEnergyValue: Dual energy correction low energy value
                    HighEnergyValue: Dual energy correction high energy value
                    NoofAreasO: Number of Ouput areas
                    AreaStartO1 – AreaStartO4: Output area start
                    AreaEndO1 – AreaEndO4: Output area end
                    NoofAreasC: Number of areas for confirmation
                    AreaStartC1 – AreaStartC4: Area for confirmation start
                    AreaEndC1 – AreaEndC4: Area for confirmation end
                    SensorType: Sensor type
                    Firmware: Firmware version
                    Option: Option list
                    NoOfPixels: Number of pixels
                    ClockFrequency: Clock frequency
                    BitDepth: Bit depth
                    TwoPCThreshold: Use two thresholds instead of one (DCAM only.
                    AutomaticBundleHeight: Use automatic calculation of bundle height.
                    DCam3SetupProp_xxxx: A setup property in the Options(setup) of a DCam 3.0
                                        module. The word xxxx stand for the name of the property
                                        (This is what you see in the labeling of the property). Blanks
                                        or underscores are ignored.
                                        Example: Dcam2SetupProp_ReadoutDirection (a parameter for the C10000)
                    GenericCamTrigger: Programming of the Trigger (GenericCam only)
                    IntervalTime: Programming of the Interval Time (GenericCam only),
                    PulseWidth: Programming of the Interval Time (GenericCam only)
                    SerialIn: Programming of the Serial In string (GenericCam only)
                    SerialOut: Programming of the Serial Out string (GenericCam only)
                    EnableRS232: Enable RS232 communication (GenericCam only)
                    RS232HexInput: HEX input for RS232 communication (GenericCam only)
                    RS232CR: Send and receive <CR> for RS232 communication (GenericCam only)
                    RS232LF: Send and receive <LF> for RS232 communication (GenericCam only)
                    RS232RTS: Use RTS handshake for RS232 communication (GenericCam only)
                    AlternateTrigger: Use alternate trigger (GenericCam only)
                    NegativeLogic: Use negative trigger (GenericCam only)
                    DataValid: Data valid
                    ComPort: Com port
                    DataBit: Data Bit
                    XMaxArea: Max Area in X-Direction
                    YMaxArea: Max Area in Y-Direction
                    OutputMode: Output mode
                    TapConfiguration: Tap configuration
                    Mode0: Mode0
                    Mode1: Mode1
                    Mode2: Mode2
                    RS232Baud: Baud rate for RS232(GenericCam only)
                    AdditionalData: Additional data
                    CameraInfo: Camera info text  # TODO exists
                    ===Parameters on the acquisition Tabs of the Acquisition dialog===
                    Exposure: Exposure time
                    Gain: Analog gain
                    Offset: Analog Offset
                    NrTrigger: Number of trigger
                    Threshold: Photon counting threshold
                    Threshold2: Second photon counting threshold (in case two thresholds are available)
                    DoRTBacksub: Do realtime background subtraction
                    DoRTShading: Do realtime shading correction
                    NrExposures: Number of exposures
                    ClearFrameBuffer: Clear frame buffer on start
                    AmpGain: Amp gain
                    SMD: Scan mode
                    RecurNumber: Recursive filter
                    HVoltage: High Voltage
                    AMD: Acquire mode
                    ASH: Acquire shutter
                    ATP: Acquire trigger polarity
                    SOP: Scan optical black
                    SPX: Superpixel
                    MCP: MCP gain
                    TDY: Time delay
                    IntegrAfterTrig: Integrate after trigger
                    SensitivityValue: Sensitivity (value)
                    EMG: EM-gain (EM-CCD camera)
                    BGSub: Background Sub
                    RecurFilter: Recursive Filter
                    HighVoltage: High Voltage
                    StreakTrigger: Streak trigger
                    FGTrigger: Frame grabber Trigger
                    SensitivitySwitch: Sensitivity (switch)
                    BGOffset: Background offset
                    ATN: Acquire trigger number
                    SMDExtended: Scan mode extended
                    LightMode: Light mode
                    ScanSpeed: Scan Speed
                    BGDataMemory: Memory number for background data (Inbuilt background sub)
                    SHDataMemory: Memory number for shading data (Inbuilt shading correction)
                    SensitivityMode: Sensitivity mode
                    Sensitivity: Sensitivity
                    Sensitivity2Mode: Sensitivity 2 mode
                    Sensitivity2: Sensitivity 2
                    ContrastControl: Contrast control
                    ContrastGain: Contrast gain
                    ContrastOffset: Contrast offset
                    PhotonImagingMode: Photon Imaging mode
                    HighDynamicRangeMode: High dynamic range mode
                    RecurNumber2: Second number for recursive filter (There is a software recursive
                                  filter and some camera have this as a hardware feature)
                    RecurFilter2: Second recursive filter (There is a software recursive filter and
                                  some camera have this as a hardware feature)
                    FrameAvgNumber: Frame average number
                    FrameAvg: Frame average
        :return: value of location, value of parameter"""
        return self.sendCommand("CamParamGet", location, parameter)

    def CamParamSet(self, location, parameter, value):
        """Sets the specified parameter of the acquisition options.
        :parameter location: (str) see CamParamGet
        :parameter parameter (str): see CamParamGet
        :parameter value: (str) value for param"""
        # Note: When using self.acqMode = "SingleLive" parameters regarding the readout camera
        # need to be changed via location = "Live"!!!
        self.sendCommand("CamParamSet", location, parameter, value)

    def CamParamInfo(self, location, parameter):
        """Returns information about the specified parameter.
        :parameter location: (str) see CamParamGet
        :parameter parameter: (str) see CamParamGet
        :return: Label, current value, param type, min (num type only), max (num type only)
        Note: For param type see AcqParamInfo."""
        return self.sendCommand("CamParamInfo", location, parameter)

    def CamParamInfoEx(self, location, parameter):
        """Returns information about the specified parameter.
        Returns more detailed information in case of a list parameter (Parameter type = 2) than CamParamInfo.
        :parameter location: (str) see CamParamGet
        :parameter parameter: (str) see CamParamGet
        :return: Label, current value, param type, min (num type only), max (num type only)
        Note: For param type see AcqParamInfo."""
        return self.sendCommand("CamParamInfoEx", location, parameter)

    def CamParamsList(self, location):
        """Returns a list of all camera parameters of the specified location.
        This command can be used to build up a complete parameter list for the corresponding camera at runtime.
        :parameter location: (str) see CamParamGet
        :return: NumberOfParameters,Parameter1,..., ParameterN"""
        return self.sendCommand("CamParamsList", location)

    def CamGetLiveBG(self):
        """Gets a new background image which is used for real time background subtraction (RTBS).
        It is only available of LIVE mode is running."""
        self.sendCommand("CamGetLiveBG")

    def CamSetupSendSerial(self):  # TODO check if needed
        """Sends a command to the camera if this is a possibility in the Camera Options
        (This is mainly intended for the GenericCam camera). The user has to write the string
         to send in the correct edit box and can then get the command response from the appropriate edit box."""
        self.sendCommand("CamSetupSendSerial")

    # === External devices commands ========================================================
    # === delay generator and streak camera controls =======================================

    def DevParamGet(self, location, parameter):
        """Returns the values of the streak camera parameters and the delay generator.
        :parameter location: (str)
                Streakcamera/Streak/TD: streak camera
                Del/Delay/Delaybox/Del1: delay box 1
        :parameter parameter: (str) Can be every parameter which appears in the external devices status/control box.
                                The parameter should be written as indicated in the Parameter name field.
                                This function also allows to get information about the device name, plugin name and
                                option name of these devices. The following keywords are available:
                                DeviceName, PluginName, OptionName1, OptionName2, OptionName3, OptionName4  TODO check

                                Additionally to the parameters from the status/control boxes the user can get or set
                                also the following parameters from the Device options:
                                Streakcamera:
                                AutoMCP, AutoStreakDelay, AutoStreakShutter, DoStatusRegularly, AutoActionWaitTime
                                Delaybox:
                                AutoDelayDelay
        :return: value of parameter"""
        return self.sendCommand("DevParamGet", location, parameter)

    def DevParamSet(self, location, parameter, value):
        """Sets the specified parameter of the acquisition options.
        :parameter location: (str) see DevParamSet
        :parameter parameter: (str) see DevParamSet
        :parameter value: (str) The value has to be written as it appears in the corresponding control."""
        self.sendCommand("DevParamSet", location, parameter, value)

    def DevParamInfo(self, location, parameter):
        """Return information about the specified parameter.
        :parameter location: (str) see DevParamSet
        :parameter parameter: (str) see DevParamSet
        :return: Label, current value, param type, min (numerical only), max (numerical only).
            param type:
                1= Numeric: A numerical value. In the case of a numerical value the minimum and maximum
                            value is returned (But not for other parameter types).
                2= List: The value is one entry in a list.
            Note: In case of a list the number of entries and all list entries are returned in the response of the
            DevParamInfoEx command."""
        return self.sendCommand("DevParamInfo", location, parameter)

    def DevParamInfoEx(self, location, parameter):
        """Return information about the specified parameter.
        Returns more detailed information in case of a list parameter (param type=2) than DevParamInfo.
        :parameter location: (str) see DevParamSet
        :parameter parameter: (str) see DevParamSet
        :return: Control available, status available, label, current value, param type, number of entries, entries.
            param type: see DevParamInfo"""
        # TODO useful for checking if value is valid!!
        return self.sendCommand("DevParamInfoEx", location, parameter)

    def DevParamsList(self, device):
        """Return list of all parameters of a specified device.
        :parameter device (str): see location in DevParamSet
        :return: number of parameters, parameters"""
        return self.sendCommand("DevParamsList", device)

    # === Sequence commands ========================================================

    def SeqParamGet(self, parameter):
        """Returns the values of the sequence options or parameters.
        :parameter parameter: (str)
            === From options: ==================
            AutoCorrectAfterSeq: Do auto corrections after sequence
            DisplayImgDuringSequence: Always display image during acquisition
            PromptBeforeStart: Prompt before start
            EnableStop: Enable stop
            Warning: Warning on
            EnableAcquireWrap: Enable wrap during acquisition
            LoadHISSequence: Load HIS sequences after acquisition
            PackHisFiles: Pack 10 or 12 bit image files in a HIS file
            NeverLoadToRAM: Do not attempt to load a sequence to RAM
            LiveStreamingBuffers: Number of Buffers for Live Streaming
            WrapPlay: Wrap during play
            PlayInterval: Play interval
            ProfileNo: Profile number for jitter correction
            CorrectionDirection: Jitter Correction direction
            === From acquisition tab: ==================
            AcquisitionMode: Acquisition mode
            NoOfLoops: No of Loops
            AcquisitionSpeed: Acquisition speed (full speed / fixed intervals)
            AcquireInterval: Acquire interval
            DoAcquireWrap: Do wrap during acquisition
            === From data storage tab: ==================
            AcquireImages: Store images
            ROIOnly: Acquire images in ROI
            StoreTo: Data storage
            FirstImgToStore: File name of first image to store
            DisplayDataOnly: Store display data (8 bit with LUT)
            UsedHDSpaceForCheck: Amount of HD space for HD check
            AcquireProfiles: Store profiles
            FirstPrfToStore: File name of first profile to store
            === From processing tab: ==================
            AutoFixpoint: Find Fixpoint automatically
            ExcludeSample: Exclude the current sample
            === From general sequence dialog: ==================
            SampleType: Sample type
            CurrentSample: Index of current sample
            NumberOfSamples: Number of samples (Images or profiles)
        :return: value of parameter"""
        return self.sendCommand("SeqParamGet", parameter)

    def SeqParamSet(self, parameter, value):
        """Sets the specified parameter of the sequence options or parameters.
        :parameter parameter: (str) see SeqParamSet
        :parameter value: (str) The value for the sequence option or parameter."""
        self.sendCommand("SeqParamSet", parameter, value)

    def SeqParamInfo(self, parameter):
        """Return information about the specified parameter.
        :parameter parameter: (str) see SeqParamSet
        :return: TODO not specified in manual check return"""
        return self.sendCommand("SeqParamInfo", parameter)

    def SeqParamInfoEx(self, location, parameter):
        """Return information about the specified parameter.
        Returns more detailed information in case of a list parameter (param type=2) than SeqParamInfo.
        In case of a numeric parameter (Parameter type = 1) it additionally returns the step width.
        :parameter parameter: (str) see SeqParamSet
        :return: TODO not specified in manual check return"""
        return self.sendCommand("SeqParamInfoEx", parameter)

    def SeqParamsList(self):
        """Return list of all parameters related to sequence mode.
        This command can be used to build up a complete parameter list related to sequence mode at runtime.
        :return: number of parameters, parameters"""
        return self.sendCommand("SeqParamsList")

    def SeqSeqMonitor(self, type):
        """This command starts a mode which returns information on every new image or part image acquired in Sequence
        mode (Sequence monitoring). Its behavior is similar to AcqLiveMonitor or AcqAcqMonitor, which returns
        information on every new live or acquisition image.
        :parameter type: (str)
                Off: No messages are output. This setting can be used to stop acquisition monitoring.
                EndAcq: Whenever a complete new image is acquired in sequence mode a message is output.
                EndPart: For every new part image in sequence mode a message is output. A part is a single
                        image which contributes to a full image. For example in Analog Integration or Photon counting
                        mode several images are combined to give one resulting image.
                All: For every new image or every new part a message is output.
        :return: msg"""
        return self.sendCommand("SeqSeqMonitor")

    def SeqStart(self):
        """Starts a sequence acquisition with the current parameters.
        Note: Any sequence which eventually exist is overwritten by this command."""
        self.sendCommand("SeqStart")

    def SeqStop(self):
        """Stops the sequence acquisition currently under progress."""
        self.sendCommand("SeqStop")

    def SeqStatus(self):
        """Returns the current sequence status.
        :return: status, msg
        e.g. idle (no sequence acquisition in progress), busy, PendingAcquisition (seq acq in progress)
        PendingAcquisition: Sequence Acquisition, Live Streaming, Save Sequence, Load Sequence or
                            No sequence related async command: command"""
        return self.sendCommand("SeqStatus")

    def SeqDelete(self):
        """Deletes the current sequence from memory.
        Note: This function does not delete a sequence on the hard disk."""
        self.sendCommand("SeqDelete")

    def SeqSave(self, imageType, fileName, *overwrite):
        """Save a sequence.
        :parameter imageType: (str)
                IMG: ITEX image
                TIF: TIFF image
                TIFF: TIFF image
                ASCII: ASCII file
                ASCIICAL: ASCII file with calibration
                data2tiff: Data to tiff
                data2tif: Data to tiff
                display2tiff: Display to tiff
                display2tif: Display to tiff
                HIS: HIS sequence (Hamamatsu image sequence)
                DISPLAY2HIS: HIS sequence (Hamamatsu image sequence) containing only display data (8 bit)
        :parameter fileName: (str) can be any valid filename. This function can also save images on a network device, so
                            it can transfer image data from one computer to another computer.
        :parameter overwrite: (str) can be either true or false. This is an optional parameter. If this is set to true
                            (or 1) the file is also saved if it exists. If the parameter is omitted or is set to false
                            (or 0) the file is not saved if it already exists and an error is returned."""
        self.sendCommand("SeqSave", imageType, fileName, *overwrite)

    def SeqLoad(self, imageType, fileName):
        """Save a sequence.
        :parameter imageType: (str) see SeqSave
        :parameter fileName: (str) see SeqSave"""
        self.sendCommand("SeqLoad", imageType, fileName)

    def SeqCopyToSeparateImg(self):
        """Copies the currently selected image of a sequence to a separate image."""
        self.sendCommand("SeqCopyToSeparateImg")

    def SeqImgIndexGet(self):
        """Returns the image index of the sequence.
        This is needed for image functions like CorrDoCorrection where we have to specify the Destination parameter.
        :return: TODO not specified in manual check!"""
        return self.sendCommand("SeqImgIndexGet")

    def SeqImgExist(self):
        """Can be used to find out whether an image sequence exists.
        :return: TODO not specified in manual check!"""
        return self.sendCommand("SeqImgExist")

    # === Image commands ====================================================================================
    # TODO more fct available in RemoteEx

    def ImgParamGet(self, parameter):
        """Returns the values of the image options.
        :parameter parameter: (str)
            AcquireToSameWindow: Acquire always to the same window
            DefaultZoomFactor: Default zooming factor
            WarnWhenUnsaved: Warn when unsaved images are closed
            Calibrated: Calibrated (Quickprofiles, Rulers, FWHM)
            LowerLUTIsZero: Force the lower LUT limit to zero when executing auto LUT
            AutoLUT: AutoLut function
            AutoLUTInLive: AutoLut in Live mode function
            AutoLUTInROI: Calculate AutoLut values in ROI
            HorizontalRuler: Display horizontal rulers
            VerticalRuler: Display vertical rulers
            IntensityRuler: Display intensity rulers (Bird view only)
            BirdViewLineThickness: Line thickness for Bird view display
            BirdViewSmoothing:Smooting for Bird view display (from 9.4 pf0)
            BirdViewScaling: Intensity scaling for Bird view display (from 9.4 pf0)
            FixedITEXHeader: Save ITEX files with fixed header
        :return: value of parameter"""
        return self.sendCommand("ImgParamGet", parameter)

    def ImgParamSet(self, parameter, value):
        """Sets the values of the image options.
        :parameter parameter: (str) see ImgParamGet
        :parameter value: (str) TODO"""
        self.sendCommand("ImgParamSet", parameter, value)

    def ImgRingBufferGet(self, type, seqNumber, *filename):
        """Returns the image or profile data of the select image. This command can be used only in
        combination with AcqLiveMonitor(RingBuffer,NumberOfBuffers). As soon as
        AcqLiveMonitor with option RingBuffer has been started the data of every new live image is
        written to a ring buffer and a continuously increasing sequence number is assigned to this data. As
        long as the image with this sequence number is still in the buffer it can be accessed by calling
        ImgRingBufferGet(Type,SeqNumber). If SeqNumber is smaller then the oldest remaining live
        image in the sequence buffer, the oldest live image is returned together with its sequence number. If
        SeqNumber is higher than the most recent live image in the buffer an error is returned.
        Note: The data is transferred by the second TCP-IP port. If this is not opened an error will be issued.
        :parameter type: (str)
            Data: The image raw data (1,2 or 4 BBP)
            Profile: A profile is returned (4 bytes floating point values)
        :parameter seqNumber: (str) sequence number of the image to return
        :parameter filename: (str) optional: location to write the data to. Raw data is written to the file without any header.
            If a file name is specified the date is written to this file (same as with ImgDataDump). If no file
            name is written the image data is transferred by the optional second TCP-IP channel. If this channel
            is not available an error is issued.
        Note: If Profile is selected for Type the syntax is:
            ImgRingBufferGet(Profile,Profiletype,iX,iY,iDX,iDY,seqnumber,file)
            where Profiletype has to be one of the following:
                    1=Line profile
                    2=Horizontal profile (integrated)
                    3=Vertical profile(integrated)
            iX,iY,iDX,iDY are the coordinates of the area where to extract the profile.
        :return: iDX,iDY,BBP,Type,seqnumber,timestamp (Data,Display)
              or: NumberOfData,Type,seqnumber,timestamp (Profile)"""
        return self.sendCommand("ImgRingBufferGet", type, seqNumber, *filename)

    def ImgDataGet(self, destination, type, *args):
        """

        :param destination: (str)
                    current: The currently selected image.
                    A number from 0 to 19: The specified image number.
        :param type: (str)
                    Data: The image raw data (1,2 or 4 BBP)
                    Display: The display data (always 1 BBP)
                    Profile: A profile returned (4 bytes floating point values)
                    ScalingTable: A profile indicating the scaling values in the case the image has
                    table scaling (4 bytes floating point values).
        :param args: (str)
                    Profiletype (if type=Profile):  1=Line profile
                                                    2=Horizontal profile (integrated)
                                                    3=Vertical profile(integrated)
                                                    iX,iY,iDX,iDY: coordinates of the area where to extract the profile.
                    iDirection (if type=ScalingTable):  H, Hor, Horizontal or X: Horizontal Scaling
                                                        V, Ver, Vertical or Y: Vertical Scaling
        :return:    iDX, iDY, BBP, Type (if type is Data or Display)
                    NumberOfData, Type (if type is Profile or ScalingTable)
        """
        return self.sendCommand("ImgDataGet", destination, type, *args)

    # === non RemoteEx functions ================================================================

    def convertUnit2Time(self, value, unit):
        """ Converts a value plus its corresponding unit as received from RemoteEx, to a value.
        :param value: (str) value
        :param unit: (str) unit
        :return: (float) value
        """
        # TODO make generic by searching list of possible units
        if unit == "ns":
            value = float(value) * 1e-9
        elif unit == "us":
            value = float(value) * 1e-6
        elif unit == "ms":
            value = float(value) * 1e-3
        elif unit == "s":
            value = float(value)
        else:
            raise logging.error("Unit conversion %s for value %s not supported" % (unit, value))

        return value

    def convertTime2Unit(self, value):
        """ Converts a value to a value plus corresponding unit, which will be accepted by RemoteEx.
        :param value: value to
        :return: (str) a string consisting of a value plus unit
        """
        # Note: For CamParamSet it doesn't matter if value + unit includes a white space or not.
        # However, for DevParamSet it does matter!!!
        # TODO make generic for possible units
        # TODO elegant range fct for float? or itertools?
        if 1e-9 <= value < 1e-6:
            value_raw = str(int(value * 1e9)) + " ns"
            self.timeRangeConversionFactor = 1e-9
        elif 1e-6 <= value < 1e-3:
            value_raw = str(int(value * 1e6)) + " us"
            self.timeRangeConversionFactor = 1e-6
        elif 1e-3 <= value < 1:
            value_raw = str(int(value * 1e3)) + " ms"
            self.timeRangeConversionFactor = 1e-3
        elif value in range(1, 10):
            value_raw = str(int(value)) + " s"
            self.timeRangeConversionFactor = 1
        else:
            raise logging.error("Unit conversion for value %s not supported" % value)

        return value_raw

        # TODO update max and min values via CamParamInfoEx("Live", "Exposure")


class streakCameraDataFlow(model.DataFlow):
    """
    Represents Hamamatsu streak camera.
    """

    def __init__(self,  start_func, stop_func, sync_func):
        """
        camera: instance ready to acquire images  TODO is that correct?
        """
        # initialize dataset, which can be subscribed to, to receive data acquired by the dataflow
        model.DataFlow.__init__(self)
        self._start = start_func
        self._stop = stop_func
        self._sync = sync_func

    # start/stop_generate are _never_ called simultaneously (thread-safe)
    def start_generate(self):
        self._start()

    def stop_generate(self):
        self._stop()

    def synchronizedOn(self, event):
        self._sync(event)
