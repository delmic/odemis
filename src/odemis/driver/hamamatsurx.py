# -*- coding: utf-8 -*-
'''
Created on Aug 2018

@author: Sabrina Rossberger, Delmic

Copyright © 2018 Sabrina Rossberger, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

import queue
import logging
import math

from odemis import model, util
from odemis.model import oneway
import threading
import time
import socket
import numpy
import collections
import numbers
from odemis.util import to_str_escape

#  0= Boolean: Can have the values true or false. Valid entries are „true“ (true), „false“
#              (false), „on“ (true), „off“ (false), „yes“ (true), „no“ (false), „0“ (false), or
#               any other numerical value (true). On output only 0 (false) and 1 (true) is
#               used.
#  1= Numeric: A numerical value. In the case of a numerical value the minimum and
#              maximum value is returned.
#  2= List: The value is one entry in a list.
#  3= String: Any string can be used.
#  4= ExposureTime: An expression which evaluates to a time like „5ms“, „1h“, „1s“ etc. Valid
#                   units are ns (nanosecond), us (microsecond), ms (millisecond), s (second), m
#                   (minute), h(hour).
#  5= Display: A string which is displayed only (read only).

PARAM_TYPE_BOOL = 0
PARAM_TYPE_NUMERIC = 1
PARAM_TYPE_LIST = 2
PARAM_TYPE_STRING = 3
PARAM_TYPE_EXPTIME = 4
PARAM_TYPE_DISPLAY = 5


class RemoteExError(IOError):

    def __init__(self, errno, *args, **kwargs):
        # Needed for pickling, cf https://bugs.python.org/issue1692335 (fixed in Python 3.3)
        desc = self._errordict.get(errno, "Unknown RemoteEx error.")
        strerror = "RemoteEx error %d: %s" % (errno, desc)
        IOError.__init__(self, errno, strerror, *args, **kwargs)

    def __str__(self):
        return self.strerror

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


class ReadoutCamera(model.DigitalCamera):
    """
    Represents Hamamatsu readout camera.
    """

    def __init__(self, name, role, parent, spectrograph=None, **kwargs):
        """ Initializes the Hamamatsu OrcaFlash readout camera.
        :parameter name: (str) as in Odemis
        :parameter role: (str) as in Odemis
        :parameter parent: class streakcamera
        """
        self.parent = parent

        self._spectrograph = spectrograph
        if not spectrograph:
            logging.warning("No spectrograph specified. No wavelength metadata will be attached.")

        try:
            # TODO make nice!
            self.parent._getReadoutCamInfo = True
            cam_info = parent.CamParamGet("Setup", "CameraInfo")
        except IOError:
            logging.exception("Failed to get readout camera info")
            # Might be due to the frame grabber failing to initialise (sometimes happens),
            # or the camera not being turned on.
            raise model.HwError("Failed to find readout camera, check it is powered. If powered, restart the Hamamatsu PC")

        # Only initiliase the component after we are sure not to raise HwError,
        # because HwError tells the back-end it should try again. As this
        # component is a child it doesn't get automatically unregistered from
        # the back-end (Pyro4) on, and next trial would fail.
        super(ReadoutCamera, self).__init__(name, role, parent=parent, **kwargs)

        try:
            self._hwVersion = cam_info[0] + ", " + cam_info[1] + ", " + cam_info[2]  # needs to be a string
        except:
            self._hwVersion = "N.A."
            logging.debug("Could not get hardware information for streak readout camera.")
        self._metadata[model.MD_HW_VERSION] = self._hwVersion
        try:
            self._swVersion = cam_info[3] + ", " + cam_info[4]  # needs to be a string
        except:
            self._swVersion = "N.A."
            logging.debug("Could not get software information for streak readout camera.")
        self._metadata[model.MD_SW_VERSION] = self._swVersion
        self._metadata[model.MD_DET_TYPE] = model.MD_DT_INTEGRATING

        # Set parameters readout camera
        parent.CamParamSet("Setup", "TimingMode", "Internal timing")  # TODO external check displayed command in GUI
        # parent.CamParamSet("Setup", "TriggerMode", 'Edge trigger')
        # parent.CamParamSet("Setup", "TriggerSource", 'BNC')
        # parent.CamParamSet("Setup", "TriggerPolarity", 'neg.')
        parent.CamParamSet("Setup", "ScanMode", 'Subarray')
        parent.CamParamSet("Setup", "Binning", '2 x 2')
        parent.CamParamSet("Setup", "VWidth", '1016')
        parent.CamParamSet("Setup", "HWidth", '1344')
        parent.CamParamSet("Setup", "ShowGainOffset", 'True')

        # sensor size (resolution)
        # Note: sensor size of OrcaFlash is actually much larger (2048px x 2048px)
        # However, only a smaller subarea is used for operating the streak system.
        # x (lambda): horizontal, y (time): vertical
        full_res = self._transposeSizeToUser((int(parent.CamParamGet("Setup", "HWidth")[0]),
                                              int(parent.CamParamGet("Setup", "VWidth")[0])))
        self._metadata[model.MD_SENSOR_SIZE] = full_res
        self._metadata[model.MD_DIMS] = "TC"

        # 16-bit
        self._shape = full_res + (2 ** 16,)

        self._binning = self._transposeSizeToUser(self._getBinning())  # used by _setResolution

        # need to be before binning, as it is modified when changing binning
        self._resolution = (int(full_res[0]/self._binning[0]), int(full_res[1]/self._binning[1]))
        self.resolution = model.ResolutionVA(self._resolution, ((1, 1), full_res), setter=self._setResolution)

        choices_bin = set(self._transposeSizeToUser(b) for b in self._getReadoutCamBinningChoices())
        self.binning = model.VAEnumerated(self._binning, choices_bin, setter=self._setBinning)
        self._metadata[model.MD_BINNING] = self.binning.value

        # physical pixel size is 6.5um x 6.5um
        sensor_pixelsize = self._transposeSizeToUser((6.5e-06, 6.5e-06))
        self._metadata[model.MD_SENSOR_PIXEL_SIZE] = sensor_pixelsize

        # pixelsize VA is the sensor size, it does not include binning or magnification
        self.pixelSize = model.VigilantAttribute(sensor_pixelsize, unit="m", readonly=True)

        range_exp = self._getCamExpTimeRange()
        self._exp_time = self.GetCamExpTime()
        self.exposureTime = model.FloatContinuous(self._exp_time, range_exp, unit="s", setter=self._setCamExpTime)
        self._metadata[model.MD_EXP_TIME] = self.exposureTime.value
        # Note: timeRange of streakunit > exposureTime readoutcam is possible and okay.

        self.readoutRate = model.VigilantAttribute(425000000, unit="Hz", readonly=True)  # MHz
        self._metadata[model.MD_READOUT_TIME] = 1 / self.readoutRate.value  # s

        # spectrograph VAs after readout camera VAs
        if self._spectrograph:
            logging.debug("Starting streak camera with spectrograph.")
            self._spectrograph.position.subscribe(self._updateWavelengthList, init=True)

        # for synchronized acquisition
        self._sync_event = None
        self.softwareTrigger = model.Event()
        # queue events starting an acquisition (advantageous when event.notify is called very fast)
        self.queue_events = collections.deque()
        self._acq_sync_lock = threading.Lock()

        # start thread, which keeps reading the dataport when an image/scaling table has arrived
        # after commandport thread to be able to set the RingBuffer
        # AcqLiveMonitor writes images to Ringbuffer, which we can read from
        # only works if we use "Live" or "SingleLive" mode
        self.parent.AcqLiveMonitor("RingBuffer", "10")  # Note: need to be handled in case we use other acq modes
        self.t_image = threading.Thread(target=self._getDataFromBuffer)
        self.t_image.start()

        self.data = StreakCameraDataFlow(self._start, self._stop, self._sync)

    def _updateWavelengthList(self, _=None):
        """
        Updates the wavelength list MD based on the current spectrograph position.
        """
        npixels = self._resolution[0]  # number of pixels, horizontal is wavelength
        # pixelsize VA is sensor px size without binning and magnification
        pxs = self.pixelSize.value[0] * self._binning[0] / self._metadata.get(model.MD_LENS_MAG, 1.0)
        wll = self._spectrograph.getPixelToWavelength(npixels, pxs)
        if len(wll) == 0 and model.MD_WL_LIST in self._metadata:
            del self._metadata[model.MD_WL_LIST]  # remove WL list from MD if empty
        else:
            self._metadata[model.MD_WL_LIST] = wll

    def _getReadoutCamBinningChoices(self):
        """
        Get min and max values for exposure time. Values are in order. First to fourth values see CamParamInfoEx.
        :return: (tuple) min and max exposure time
        """
        choices_raw = self.parent.CamParamInfoEx("Setup", "Binning")[4:]
        choices = []
        for choice in choices_raw:
            choices.append((int(choice[0]), int(choice[4])))

        return set(choices)

    def _getBinning(self):
        """
        Get binning setting from camera.
        Convert the format provided by RemoteEx.
        :return: (tuple) current binning
        """
        _binning = self.parent.CamParamGet("Setup", "Binning")  # returns list of format e.g. [2 x 2]
        # convert as .resolution VA need tuple instead of list
        binning = int(_binning[0].split("x")[0].strip(" ")), int(_binning[0].split("x")[1].strip(" "))

        return binning

    def _setBinning(self, value):
        """
        Called when .binning VA is modified. It actually modifies the camera binning.
        :parameter value: (2-tuple int) binning value to set
        :return: current binning value
        """

        # only update resolution and especially update wavelength list (on spectrograph) when necessary
        if value == self.binning.value:
            return value

        # ResolutionVA need tuple instead of list of format "2 x 2"
        binning = "%s x %s" % self._transposeSizeFromUser(value)

        # If camera is acquiring, it is essential to stop cam first and then change binning.
        # Currently, this only affects the Alignment tab, where camera is continuously acquiring.
        if self.data.active:  # Note: not thread save -> change # TODO use update_settings()
            self.parent.AcqStop()
            self.parent.CamParamSet("Setup", "Binning", binning)
            self.parent.StartAcquisition("Live")
        else:
            self.parent.CamParamSet("Setup", "Binning", binning)

        prev_binning, self._binning = self._binning, value

        # adapt resolution
        change = (prev_binning[0] / self._binning[0],
                  prev_binning[1] / self._binning[1])
        old_resolution = self.resolution.value
        new_res = (int(round(old_resolution[0] * change[0])),
                   int(round(old_resolution[1] * change[1])))

        # fit and also ensure wl is correct (calls updateWavelengthList)
        self.resolution.value = new_res

        self._metadata[model.MD_BINNING] = self._binning  # update MD

        return self._binning

    def _setResolution(self, _=None):
        """
        Sets the resolution VA.
        So far the full field of view is always used. Therefore, resolution only changes with binning.
        :return: current resolution value
        """
        # Note: we can keep it simple as long as we do not provide to change the sensor size yet...
        resolution = self._shape[:2]
        new_res = (int(resolution[0] // self._binning[0]),
                   int(resolution[1] // self._binning[1]))  # floor division

        self._resolution = new_res
        if self._spectrograph:
            self._updateWavelengthList()  # update WavelengthList when changing binning

        return new_res

    def _getCamExpTimeRange(self):
        """
        Get min and max values for the camera exposure time.
        :return: tuple containing min and max exposure time
        """
        exp = self.parent.CamParamInfoEx("Live", "Exposure")  # returns list
        # Values in returned list "exp" are in order. 1st - 4th values see CamParamInfoEx.
        min_value = exp[4]
        max_value = exp[-1]

        min_value_raw, min_unit = min_value.split(' ')[0:2]
        max_value_raw, max_unit = max_value.split(' ')[0:2]

        min_exp = self.parent.convertUnit2Time(min_value_raw, min_unit)
        max_exp = self.parent.convertUnit2Time(max_value_raw, max_unit)

        return min_exp, max_exp

    def GetCamExpTime(self):
        """
        Get the camera exposure time.
        Converts the provided value received from RemoteEx into sec.
        :return: (float) exposure time in sec
        """
        exp_time_raw = self.parent.CamParamGet("Live", "Exposure")[0].split(' ')
        try:
            exp_time = self.parent.convertUnit2Time(exp_time_raw[0], exp_time_raw[1])
        except Exception:
            raise IOError("Exposure time of %s is not supported for read-out camera." % exp_time_raw)

        return exp_time

    def _setCamExpTime(self, value):
        """
        Set the camera exposure time.
        Converts the time range in sec into a for RemoteEx readable format.
        :parameter value: (float) exposure time to be set
        :return: (float) current exposure time
        """
        try:
            exp_time_raw = self.parent.convertTime2Unit(value)
        except Exception:
            raise IOError("Exposure time of %s sec is not supported for read-out camera." % value)

        # Note: RemoteEx uses different exposure times depending on acquisition mode
        # If we support e.g. photon counting, we need to specify a different location in RemoteEx.
        # For now location is always "Live"
        self.parent.CamParamSet("Live", "Exposure", exp_time_raw)
        self._metadata[model.MD_EXP_TIME] = value  # update MD

        return value

    def _start(self):
        """
        Start an acquisition.
        """
        # Note: no function to get current acqMode.
        # Note: Acquisition mode, needs to be before exposureTime!
        # Acquisition mode should be either "Live" (non-sync acq) or "SingleLive" (sync acq) for now.
        if self._sync_event is None:  # do not care about synchronization, start acquire
            self.parent.StartAcquisition("Live")

    def _stop(self):
        """
        Stop the acquisition.
        """
        self.parent.AcqStop()
        self.parent.queue_img.put("F")  # Flush, to stop reading all images still in the ring buffer
        # Note: set MCPGain to zero after acquiring for HW safety reasons
        self.parent._streakunit.MCPGain.value = 0

    def _sync(self, event):  # event = self.softwareTrigger
        """
        Synchronize the acquisition on the given event. Every time the event is
          triggered, the DataFlow will start a new acquisition.
        event (model.Event or None): event to synchronize with. Use None to
          disable synchronization.
        The DataFlow can be synchronized only with one Event at a time.
        If the camera is already running in live-mode and receiving a sync event, the live-mode will be stopped.
        TODO: If the sync event is removed, the live-mode is currently not automatically restarted.
        Need to explicitly restart live-mode for now.
        """
        # if event None and sync as well -> return, or if event sync, but sync already set -> return
        if self._sync_event == event:
            return

        if self._sync_event:  # if new event = None, unsubscribe previous event (which was softwareTrigger)
            self._sync_event.unsubscribe(self)

        self._sync_event = event

        if self._sync_event:
            # softwareTrigger subscribes to onEvent method: if softwareTrigger.notify() called, onEvent method called
            self._sync_event.subscribe(self)  # must have onEvent method

    @oneway
    def onEvent(self):
        """
        Called by the Event when it is triggered  (e.g. self.softwareTrigger.notify()).
        """
        logging.debug("Event triggered to start a new synchronized acquisition.")
        self.queue_events.append(time.time())
        self.parent.queue_img.put("start")

    # override
    def updateMetadata(self, md):
        """
        Update the metadata.
        """
        super(ReadoutCamera, self).updateMetadata(md)
        if model.MD_LENS_MAG in md and self._spectrograph:
            self._updateWavelengthList()

    def _mergeMetadata(self, md):
        """
        Create dict containing all metadata from the children readout camera, streak unit, delay genereator
        and the metadata from the parent streak camera.
        """

        md_devices = [self.parent._streakunit._metadata, self.parent._delaybox._metadata]

        for md_dev in md_devices:
            for key in md_dev.keys():
                if key not in md:
                    md[key] = md_dev[key]
                elif key in (model.MD_HW_NAME, model.MD_HW_VERSION, model.MD_SW_VERSION):
                    md[key] = ", ".join([md[key], md_dev[key]])
        return md

    def _getDataFromBuffer(self):
        """
        This method runs in a separate thread and waits for messages in queue indicating
        that some data was received. The image is then received from the device via the dataport IP socket or
        the vertical scaling table is received, which corresponds to a time range for a single sweep.
        It corrects the vertical time information. The table contains the actual timestamps for each px.
        The camera should already be prepared with a RingBuffer.
        """
        logging.debug("Starting data thread.")
        time.sleep(1)  # TODO: why? => Document.

        is_receiving_image = False  # used during synchronised acquisition

        try:
            while True:
                if self._sync_event and not is_receiving_image:
                    timeout = 2
                    start = time.time()
                    while int(self.parent.AsyncCommandStatus()[0]):
                        time.sleep(0)
                        logging.debug("Asynchronous RemoteEx command still in process. Wait until finished.")
                        if time.time() > start + timeout:  # most likely camera is in live-mode, so stop camera
                            self.parent.AcqStop()
                            start = time.time()
                    try:
                        event_time = self.queue_events.popleft()
                        logging.warning("Starting acquisition delayed by %g s.", time.time() - event_time)
                        self.parent.AcqStart("SingleLive")  # should never be a different
                        is_receiving_image = True
                    except IndexError:
                        # No event (yet) => fine
                        pass

                if self._sync_event:
                    timeout = max(self.exposureTime.value * 2, 1)  # wait at least 1s
                else:
                    timeout = None

                try:
                    rargs = self.parent.queue_img.get(block=True, timeout=timeout)  # block until receive something
                except queue.Empty:
                    logging.warning("Failed to receive image from streak ccd. Timed out after %f s. Will try again.",
                                    timeout)
                    is_receiving_image = False
                    continue

                logging.debug("Received img message %s", rargs)

                if rargs is None:  # if message is None end the thread
                    return

                if self._sync_event:  # synchronized mode
                    if rargs == "start":
                        logging.info("Received event trigger")
                        continue
                    else:
                        logging.info("Get the synchronized image.")
                else:  # non-sync mode
                    while not self.parent.queue_img.empty():
                        # keep reading to check if there might be a newer image for display
                        # in case we are too slow with reading
                        rargs = self.parent.queue_img.get(block=False)

                        if rargs is None:  # if message is None end the thread
                            return
                    logging.info("No more images in queue, so get the image.")

                if rargs == "F":  # Flush => the previous images are from the previous acquisition
                    logging.debug("Acquisition was stopped so flush previous images.")
                    continue

                reception_time_image = time.time()

                # get the image from the buffer
                img_num = rargs[1]
                img_info = self.parent.ImgRingBufferGet("Data", img_num)

                if not img_info:  # TODO check if this ever happens in log and if not remove!
                    logging.warning("Image info received from buffer is empty!")
                    continue

                img_size = int(img_info[0]) * int(img_info[1]) * 2  # num of bytes we need to receive (uint16)
                img_num_actual = img_info[4]

                img = b""
                try:
                    while len(img) < img_size:  # wait until all bytes are received
                        img += self.parent._dataport.recv(img_size)
                except socket.timeout as msg:
                    logging.error("Did not receive an image: %s", msg)
                    continue

                image = numpy.frombuffer(img, dtype=numpy.uint16)  # convert to array
                image.shape = (int(img_info[1]), int(img_info[0]))
                logging.debug("Requested image number %s, received number %s of shape %s.",
                              img_num, img_num_actual, image.shape)

                # Get the scaling table to correct the time axis
                # TODO only request scaling table if corresponding MD not available for this time range
                if self.parent._streakunit.streakMode.value:
                    # There should be no sync problem, as we only receive images and scaling table via the dataport

                    logging.debug("Request scaling table for time axis of Hamamatsu streak camera.")
                    try:
                        # request scaling table
                        scl_table_info = self.parent.ImgDataGet("current", "ScalingTable", "Vertical")
                        scl_table_size = int(scl_table_info[0]) * 4  # num of bytes we need to receive

                        # receive the bytes via the dataport
                        tab = b""
                        try:
                            while len(tab) < scl_table_size:  # keep receiving bytes until we received all expected bytes
                                tab += self.parent._dataport.recv(scl_table_size)
                            table = numpy.frombuffer(tab, dtype=numpy.float32)  # convert to array
                            table_converted = table * self.parent._streakunit.timeRangeFactor  # convert to sec
                            self._metadata[model.MD_TIME_LIST] = table_converted
                        except socket.timeout as msg:
                            logging.error("Did not receive a scaling table: %s", msg)
                            continue
                        logging.debug("Received scaling table for time axis of Hamamatsu streak camera.")
                    except Exception:
                        logging.exception("Failed to get scaling table")
                else:
                    # remove MD_TIME_LIST if not applicable
                    self._metadata.pop(model.MD_TIME_LIST, None)

                # update MD for the current image
                try:
                    self.parent._delaybox._updateTriggerRate()
                except Exception:
                    logging.exception("Failed to update trigger rate")

                md = dict(self._metadata)  # make a copy of md dict so cannot be accidentally changed
                self._mergeMetadata(md)  # merge dict with metadata from other HW devices (streakunit and delaybox)
                md[model.MD_ACQ_DATE] = reception_time_image - md[model.MD_EXP_TIME] + md[model.MD_READOUT_TIME]
                dataarray = model.DataArray(self._transposeDAToUser(image), md)
                self.data.notify(dataarray)  # pass the new image plus MD to the callback fct

                is_receiving_image = False

        except Exception:
            logging.exception("Hamamatsu streak camera TCP/IP image thread failed.")
        finally:
            logging.info("Hamamatsu streak camera TCP/IP image thread ended.")

    def terminate(self):
        # terminate image thread
        if self.t_image.isAlive():
            self.parent.queue_img.put(None)
            self.t_image.join(5)
        try:
            self._stop()  # stop any acquisition
        except Exception:
            pass


class StreakUnit(model.HwComponent):
    """
    Represents the Hamamatsu streak unit.
    """

    def __init__(self, name, role, parent, daemon=None, **kwargs):
        super(StreakUnit, self).__init__(name, role, parent=parent, daemon=daemon, **kwargs)  # init HwComponent

        self.parent = parent
        self.location = "Streakcamera"  # don't change, internally needed by HPDTA/RemoteEx

        self._hwVersion = parent.DevParamGet(self.location, "DeviceName")[0]   # needs to be a string
        self._metadata[model.MD_HW_VERSION] = self._hwVersion

        # Set parameters streak unit
        parent.DevParamSet(self.location, "Time Range", "1 ns")
        parent.DevParamSet(self.location, "MCP Gain", 0)
        # Switch Mode to "Focus", MCPGain = 0 (implemented in RemoteEx and also here in the driver).
        parent.DevParamSet(self.location, "Mode", "Focus")
        # Resets behavior for a vertical single shot sweep: Automatic reset occurs after each sweep.
        parent.DevParamSet(self.location, "Trig. Mode", "Cont")
        # [Volt] Input and indication of the trigger level for the vertical sweep.
        parent.DevParamSet(self.location, "Trig. level", 1)  # TODO check what value needed regarding HW
        parent.DevParamSet(self.location, "Trig. slope", "Rising")

        # parent.DevParamGet(self.location, "Trig. status")  # read only

        # Ready: Is displayed when the system is ready to receive a trigger signal.
        # Fired: Is displayed when the system has received a trigger signal but the sweep has not
        # been completed or no reset signal has been applied until now. The system will ignore trigger signals
        # during this state.
        # Do Reset: Do Reset can be selected when the system is in trigger mode Fired. After selecting Do
        # Reset the trigger status changes to Ready.

        self._metadata[model.MD_STREAK_TIMERANGE] = self.GetTimeRange()
        self._metadata[model.MD_STREAK_MCPGAIN] = self.GetMCPGain()
        self._metadata[model.MD_STREAK_MODE] = self.GetStreakMode()

        # VAs
        mode = self.GetStreakMode()
        self.streakMode = model.BooleanVA(mode, setter=self._setStreakMode)  # default False see set params above

        gain = self.GetMCPGain()
        range_gain = self._getStreakUnitMCPGainRange()
        self.MCPGain = model.IntContinuous(gain, range_gain, setter=self._setMCPGain)
        # Note: MCPGain auto set to 0 is handled by stream not by driver except when changing from
        # "Operate" mode to "Focus" mode

        timeRange = self.GetTimeRange()
        choices = set(self._getStreakUnitTimeRangeChoices())
        timeRange = util.find_closest(timeRange, choices)  # make sure value is in choices
        self.timeRange = model.FloatEnumerated(timeRange, choices, setter=self._setTimeRange, unit="s")

        # a variable that stores the current timeRange conversion for e.g. the scaling table conversion
        # is set in the setter of the timeRange VA
        self.timeRangeFactor = None

        # read-only VAs TODO: Trig. Mode, Trig. level, Trig. slope?

    def GetStreakMode(self):
        """
        Get the current value from the the streak unit HW.
        :return: (bool) current streak mode value
        """
        streakMode_raw = self.parent.DevParamGet(self.location, "Mode")  # returns a list
        if streakMode_raw[0] == "Focus":
            streakMode = False
        elif streakMode_raw[0] == "Operate":
            streakMode = True
        else:
            logging.warning("Unexpected streak mode %s", streakMode_raw)
            streakMode = True  # safer!

        return streakMode

    def _setStreakMode(self, value):
        """
        Updates the streakMode VA.
        :parameter value: (bool) value to be set
        :return: (bool) current streak mode
        """
        if not value:
            self.MCPGain.value = 0
            self.parent.DevParamSet(self.location, "Mode", "Focus")
        else:
            self.parent.DevParamSet(self.location, "Mode", "Operate")
        logging.debug("Reporting mode %s for streak unit.", value)
        self._metadata[model.MD_STREAK_MODE] = value

        return value

    def GetMCPGain(self):
        """
        Get the current value from the the streak unit HW.
        :return: (int) current MCPGain value
        """
        MCPGain_raw = self.parent.DevParamGet(self.location, "MCP Gain")  # returns a list
        MCPGain = int(MCPGain_raw[0])

        return MCPGain

    def _setMCPGain(self, value):
        """
        Updates the MCPGain VA.
        :parameter value: (int) value to be set
        :return: (int) current MCPGain
        """
        self.parent.DevParamSet(self.location, "MCP Gain", value)
        logging.debug("Reporting MCP gain %s for streak unit.", value)
        self._metadata[model.MD_STREAK_MCPGAIN] = value

        return value

    def _getStreakUnitMCPGainRange(self):
        """
        Get range for streak unit MCP gain.
        :return: (tuple of int) range for MCP gain values
        """
        # First 5 values see CamParamInfoEx.
        MCPGainRange_raw = self.parent.DevParamInfoEx(self.location, "MCP Gain")[5:]
        MCPGainRange = (int(MCPGainRange_raw[0]),
                        int(MCPGainRange_raw[1]))

        return MCPGainRange

    def GetTimeRange(self):
        """
        Get the current value from the the streak unit HW.
        :return: (float) current time range value
        """
        timeRange = self._getStreakUnitTimeRange()

        return timeRange

    def _setTimeRange(self, value):
        """
        Updates the timeRange VA.
        :parameter value: (float) value to be set
        :return: (float) current time range
        """
        self._setStreakUnitTimeRange(self.location, value)
        logging.debug("Reporting time range %s for streak unit.", value)
        self._metadata[model.MD_STREAK_TIMERANGE] = value

        # set corresponding trigger delay
        tr2d = self.parent._delaybox._metadata.get(model.MD_TIME_RANGE_TO_DELAY)
        if tr2d:
            key = util.find_closest(value, tr2d.keys())
            if util.almost_equal(key, value):
                self.parent._delaybox.triggerDelay.value = tr2d[key]
            else:
                logging.warning("Time range %s is not a key in MD for time range to "
                                "trigger delay calibration" % value)

        return value

    def _setStreakUnitTimeRange(self, location, time_range):
        """
        Sets the time range for the streak unit.
        Converts the value in sec into a for RemoteEx readable format.
        :parameter location: (str) see DevParamGet
        :parameter time_range: (float) time range for one sweep in sec
        """
        try:
            time_range_raw = self.parent.convertTime2Unit(time_range)
            self._setTimeRangeFactor(time_range)
        except Exception:
            raise ValueError("Time range of %s sec for one sweep is not supported for streak unit." % time_range)

        self.parent.DevParamSet(location, "Time Range", time_range_raw)

    def _getStreakUnitTimeRangeChoices(self):
        """
        Get choices for streak unit time range. Values are in order.
        First six values see CamParamInfoEx.
        :return: (set of floats) possible choices for time range
        """
        choices_raw = self.parent.DevParamInfoEx(self.location, "Time Range")[6:]
        choices = []
        for choice in choices_raw:
            choice_raw = choice.split(" ")
            choices.append(self.parent.convertUnit2Time(choice_raw[0], choice_raw[1]))

        return choices

    def _getStreakUnitTimeRange(self):
        """
        Gets the time range of the streak unit.
        Converts the provided value received from RemoteEx into sec.
        :return: (float) current time range for one sweep in sec
        """
        time_range_raw = self.parent.DevParamGet(self.location, "Time Range")[0].split(" ")
        time_range = self.parent.convertUnit2Time(time_range_raw[0], time_range_raw[1])

        return time_range

    def _setTimeRangeFactor(self, value):
        """
        Sets the time range factor needed for conversion of RemoteEx values to sec.
        This method maps the values and units obtained from the
        scaling table (correlating of px positions with corresponding time values) to values only.
        :parameter value: (float) conversion factor
        """
        if 1e-9 <= value < 1:
            self.timeRangeFactor = 10 ** (math.log10(abs(value)) // 3 * 3)
        else:
            raise ValueError("Cannot calculate time range conversion factor. "
                             "Time range of value %s not supported" % value)

    def terminate(self):
        self.MCPGain.value = 0
        self.streakMode.value = False


class DelayGenerator(model.HwComponent):
    """
    Represents the delay generator.
    """

    def __init__(self, name, role, parent, daemon=None, **kwargs):
        super(DelayGenerator, self).__init__(name, role, parent=parent, daemon=daemon, **kwargs)  # init HwComponent

        self.parent = parent
        self.location = "Delaybox"  # don't change, internally needed by HPDTA/RemoteEx

        self._hwVersion = parent.DevParamGet(self.location, "DeviceName")[0]   # needs to be a string
        self._metadata[model.MD_HW_VERSION] = self._hwVersion

        # Set parameters delay generator
        parent.DevParamSet(self.location, "Setting", "M1")  # TODO might be enough and don't need the rest...check!!
        parent.DevParamSet(self.location, "Trig. Mode", "Ext. rising")  # Note: set to "Int." for testing without SEM
        parent.DevParamSet(self.location, "Repetition Rate", 1000000)  # [0.001, 10000000] # read-only for Ext. rising
        parent.DevParamSet(self.location, "Delay A", 0)
        parent.DevParamSet(self.location, "Delay B", 0.00000002)
        parent.DevParamSet(self.location, "Burst Mode", "Off")

        self._metadata[model.MD_TRIGGER_DELAY] = self.GetTriggerDelay()
        # Note: trigger rate (repetition rate) corresponds to the ebeam blanking frequency (read only in RemoteEx)
        self._metadata[model.MD_TRIGGER_RATE] = int(self.parent.DevParamGet(self.location, "Repetition Rate")[0])

        triggerDelay = self.GetTriggerDelay()
        range_trigDelay = self._getTriggerDelayRange()
        self.triggerDelay = model.FloatContinuous(triggerDelay, range_trigDelay, setter=self._setTriggerDelay, unit="s")

        # read only VAs TODO Burst Mode, Setting, Trig. Mode, delay B?

    # override HwComponent.updateMetadata
    def updateMetadata(self, md):

        if model.MD_TIME_RANGE_TO_DELAY in md:
            for timeRange, delay in md[model.MD_TIME_RANGE_TO_DELAY].items():
                if not isinstance(delay, numbers.Real):
                    raise ValueError("Trigger delay %s corresponding to time range %s is not of type float. "
                                     "Please check calibration file for trigger delay." % (delay, timeRange))
                if not self.triggerDelay.range[0] <= delay <= self.triggerDelay.range[1]:
                    raise ValueError("Trigger delay %s corresponding to time range %s is not in range %s. "
                                     "Please check the calibration file for the trigger delay."
                                     % (delay, timeRange, self.triggerDelay.range))

        super(DelayGenerator, self).updateMetadata(md)

    def GetTriggerDelay(self):
        """
        Get the current value from the the trigger delay HW (RemoteEx: delay A).
        :return: (float) current trigger delay value
        """
        triggerDelay_raw = self.parent.DevParamGet(self.location, "Delay A")  # returns a list
        triggerDelay = float(triggerDelay_raw[0])

        return triggerDelay

    def _setTriggerDelay(self, value):
        """
        Updates the trigger delay VA.
        :parameter value: (float) value to be set
        :return: (float) current trigger delay value
        """
        self.parent.DevParamSet(self.location, "Delay A", value)
        logging.debug("Reporting trigger delay %s for delay generator.", value)
        self._metadata[model.MD_TRIGGER_DELAY] = value

        return self.GetTriggerDelay()

    def _getTriggerDelayRange(self):
        """
        Get the range allowed for delay A. RemoteEx provides a negative minimum,
        which is internally set to zero whenever a negative delay is requested.
        :return: (tuple) the trigger delay range
        """
        min_time = 0
        max_time = float(self.parent.DevParamInfoEx(self.location, "Delay A")[-1])
        max_time = min(max_time, 10)  # don't report too high range
        range_time = (min_time, max_time)

        return range_time

    def _updateTriggerRate(self):
        """
        Get the trigger rate (repetition) rate from the delay generator and updates the MD.
        The Trigger rate corresponds to the ebeam blanking frequency. As the delay
        generator is operated "external", the trigger rate is a read-only value.
        Called whenever an image arrives.
        """
        triggerRate_raw = self.parent.DevParamGet(self.location, "Repetition Rate")  # returns a list
        triggerRate = float(triggerRate_raw[0])
        self._metadata[model.MD_TRIGGER_RATE] = triggerRate


class StreakCamera(model.HwComponent):
    """
    Represents Hamamatsu readout camera for the streak unit.
    Client to connect to HPD-TA software via RemoteEx.
    """

    def __init__(self, name, role, port, host, children=None, dependencies=None, daemon=None, **kwargs):
        """
        Initializes the device.
        :parameter host: (str) IP-adress or hostname
        :parameter port: (int) port for sending/receiving commands
        """
        super(StreakCamera, self).__init__(name, role, dependencies=dependencies, daemon=daemon, **kwargs)

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
            logging.exception("Failed to initialise Hamamatsu readout camera.")
            raise

        # collect responses (error_code = 0-3,6-10) from commandport
        self.queue_command_responses = queue.Queue(maxsize=0)
        # save messages (error_code = 4,5) from commandport
        self.queue_img = queue.Queue(maxsize=0)

        self.should_listen = True  # used in readCommandResponse thread
        self._getReadoutCamInfo = False  # nasty trick to get cam info

        # start thread, which keeps reading the commandport response continuously
        self._start_receiverThread()

        # Note: start HPDTA after initializing queue and command and receiver treads
        # but before image thread and initializing children!

        # TODO check if already running....otherwise start multiple apps
        # TODO  -> in acquisition mode it looks like it does not start a second app, but also does not report that
        # TODO -> in processing mode it is possible to start multiple apps....
        # TODO is there a clever way for checking if app still running? Seems to be no command available to check
        # TODO appEnd only works for the last opened window
        # TODO want to check if we want to start app invisible (sVisible = False)

        self.AppStart()  # start HPDTA software  # Note: comment out for testing in order to not start a new App
        try:
            children = children or {}
            dependencies = dependencies or {}

            try:
                ckwargs = children["readoutcam"]
            except Exception:
                raise ValueError("Required child readoutcam not provided")

            self._readoutcam = ReadoutCamera(parent=self, spectrograph=dependencies.get("spectrograph"),
                                             daemon=daemon, **ckwargs)
            self.children.value.add(self._readoutcam)  # add readoutcam to children-VA
            try:
                ckwargs = children["streakunit"]
            except Exception:
                raise ValueError("Required child streakunit not provided")
            self._streakunit = StreakUnit(parent=self, daemon=daemon, **ckwargs)
            self.children.value.add(self._streakunit)  # add streakunit to children-VA
            try:
                ckwargs = children["delaybox"]
            except Exception:
                raise ValueError("Required child delaybox not provided")
            self._delaybox = DelayGenerator(parent=self, daemon=daemon, **ckwargs)
            self.children.value.add(self._delaybox)  # add delaybox to children-VA
        except Exception:
            try:
                # Close back the app, so that we have some chance it can be started
                # again next on next start attempt.
                self.AppEnd()
            except Exception:
                logging.exception("AppEnd failed")
            self.should_listen = False  # terminates receiver thread
            self._closeConnection()
            raise

    def _openConnection(self):
        """
        Open connection with RemoteEx client.
        :return: connection to RemoteEx command and data port
        """
        # connect to sockets
        try:
            self._commandport = socket.create_connection((self.host, self.port), timeout=5)
            self._dataport = socket.create_connection((self.host, self.port_d), timeout=5)
        except (socket.timeout, socket.error):
            raise model.HwError("Failed to connect to host %s using port %d. Check the server "
                                "is connected to the network, turned "
                                "on, and correctly configured." % (self.host, self.port))

        # check if connection returns correct response
        try:
            message = self._commandport.recv(self.port)
            if message != b"RemoteEx Ready\r":
                raise ValueError("Connection Hamamatsu RemoteEx via port %s not successful. "
                                 "Response %s from server is not as expected." % (self.port, message))
        except socket.timeout:
            raise model.HwError("Hamamatsu RemoteEx didn't respond. "
                                "Check that it is running properly, or restart the streak camera computer.")

        try:
            message_d = self._dataport.recv(self.port_d)
            if message_d != b"RemoteEx Data Ready\r":
                raise IOError("Connection Hamamatsu RemoteEx via port %s not successful. "
                              "Response %s from server is not as expected." % (self.port_d, message))
        except socket.timeout:
            raise model.HwError("Hamamatsu RemoteEx didn't respond. "
                                "Check that it is running properly, or restart the streak camera computer.")

        # set timeout
        self._commandport.settimeout(1.0)
        self._dataport.settimeout(5.0)

        return self._commandport, self._dataport

    def _start_receiverThread(self):
        """
        Start the receiver thread, which keeps listening to the response of the command port.
        """
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
        # terminate children
        for child in self.children.value:
            child.terminate()

        try:
            self.AppEnd()
        except Exception:
            logging.info("Failed to stop the HPDTA App (Hamamatsu streak camera)", exc_info=True)

        self.should_listen = False  # terminates receiver thread
        if self.t_receiver.isAlive():
            self.t_receiver.join(5)
        self._closeConnection()

        super(StreakCamera, self).terminate()

    def sendCommand(self, func, *args, **kwargs):
        """
        Sends a command to RemoteEx.
        :parameter func: (str) command or function, which should be send to RemoteEx
        :parameter args: (str) optional parameters allowed for function
        :parameter kwargs: optional arguments not defined in advance
           kwargs timeout: (int) timeout while waiting for command response [sec]
        :return: (list of str) values returned by the function
        :raise:
           HwError: if error communicating with the hardware, probably due to
              the hardware not being in a good state (or connected)
           IOError: if error during the communication (such as the protocol is
              not respected)
        """
        # set timeout for waiting for command response
        timeout = kwargs.pop("timeout", 5)  # default = 5s
        command = "%s(%s)\r" % (func, ",".join(args))
        command = command.encode("ascii")

        with self._lock_command:  # lock this code, when finished lock is automatically released
            # send command to socket
            try:
                logging.debug("Sending: '%s'", to_str_escape(command))
                self._commandport.send(command)
            except Exception:
                try:  # try to reconnect if connection was lost
                    logging.exception("Failed to send the command %s, will try to reconnect to RemoteEx."
                                      % to_str_escape(command))
                    self._commandport, self._dataport = self._openConnection()
                    # restart receiver thread, which keeps reading the commandport response continuously
                    self._start_receiverThread()
                    logging.debug("Sending: '%s'", to_str_escape(command))
                    self._commandport.send(command)
                except (socket.error, socket.timeout) as err:
                    raise model.HwError(err, "Could not connect to RemoteEx.")

            latest_response = None  # None or tuple of str
            while True:  # wait for correct response until Timeout
                try:
                    # if not receive something after timeout
                    response = self.queue_command_responses.get(timeout=timeout)
                except queue.Empty:
                    if latest_response:
                        # log the last error code received before timeout
                        logging.error("Latest response before timeout was '%s'",
                                      latest_response)
                    # TODO: try to close/reopen the connection. However, not re-send
                    # the command as we don't know whether it was received, and
                    # whether it's safe to send twice the same command. So still
                    # report a timeout, but hopefully the next command works again.
                    raise util.TimeoutError("No answer received after %s s for command %s."
                                            % (timeout, to_str_escape(command)))

                # save the latest response in case we don't receive any other response before timeout
                latest_response = response

                # TODO: also check the timeout here, in case a lot of messages arrive, but never the right one.
                try:
                    error_code, rfunc, rargs = int(response[0]), response[1], response[2:]
                except Exception as ex:
                    raise IOError("Unexpected response %s: %s" % (response, ex))

                # check if the response corresponds to the command sent before
                # the response corresponding to a command always also includes the command name
                if rfunc.lower() != func.lower():  # fct name not case sensitive
                    logging.debug("Response not about function %s, will wait some more time.", func)
                    continue  # continue listening to receive the correct response for the sent command or timeout

                logging.debug("Interpreted response: %s.", response)
                if error_code:  # != 0, response corresponds to command, but an error occurred
                    logging.error(RemoteExError(error_code))
                    raise RemoteExError(error_code)
                else:  # successfully executed command and return message
                    return rargs

    def readCommandResponse(self):
        """
        This method runs in a separate thread and continuously listens for messages returned from
        the device via the commandport IP socket.
        The messages are made available either on .queue_command_responses (for the standard responses)
        or .queue_img (for messages related to the images).
        """
        try:
            responses = b""  # received data not yet processed

            while self.should_listen:
                try:
                    received = self._commandport.recv(4096)  # buffersize should be small value of power 2 (4096)
                except socket.timeout:
                    # when socket timed out (receiving no response)
                    logging.debug("Timeout on the socket, will wait for more data packets.")
                    continue
                if not received:
                    # TODO: this seems to get triggered "sometimes", and then
                    # never stop (until a back-end restart). Maybe if the
                    # connection drops. This needs to be investigated further
                    # and probably have a mechanism to recover to a sane state.
                    logging.debug("Received empty data")
                    time.sleep(0.1)
                    continue

                logging.debug("Received: '%s'", to_str_escape(received))
                responses += received

                resp_splitted = responses.split(b"\r")
                # split responses, overwrite var responses with the remaining messages (usually empty)
                resp_splitted, responses = resp_splitted[:-1], resp_splitted[-1]

                for msg in resp_splitted:
                    msg_splitted = msg.decode("latin1").split(",")

                    try:
                        error_code, rfunc, rargs = int(msg_splitted[0]), msg_splitted[1], msg_splitted[2:]
                    except (TypeError, ValueError, IOError):
                        logging.warning("Received response, which is not according to the known protocol.")
                        continue  # return to try-statement and start receiving again

                    if self._getReadoutCamInfo:
                        # HACK: command CamParamGet("Setup", "CameraInfo") behaves differently than all other commands.
                        # Use nasty trick to work around for this command.
                        # This command first receives the first data until \r and then additional information.
                        # Currently the total information always has 5 lines:
                        # rargs: 'OrcaFlash 4.0 V3'
                        # additional_info:
                        # '\nProduct number: C13440-20C\r\nSerial number: 301730\r\nFirmware: 4.20.B\r\nVersion: 4.20.B03-A19-B02-4.02\r'
                        # processed output:
                        # 'OrcaFlash 4.0 V3'
                        # 'Product number: C13440-20C'
                        # 'Serial number: 301730'
                        # 'Firmware: 4.20.B'
                        # 'Version: 4.20.B03-A19-B02-4.02'
                        # Note: It is not clear whether rargs might sometimes already contain parts of additional info
                        additional_info = responses
                        timeout = 1  # wait for 1sec, if not receiving all additional info in that time: skip it
                        start = time.time()
                        while time.time() < start + timeout:
                            try:
                                # continue listening as there is additional info coming in
                                additional_info += self._commandport.recv(4096)   # receive more data
                            except Exception:
                                break
                            logging.debug("Received: '%s'", to_str_escape(additional_info))

                        try:
                            additional_info = additional_info.decode("latin1").split("\r")
                            for item in additional_info[:-1]:
                                msg_splitted.append(item.strip("\n"))
                            if additional_info[-1]:
                                logging.warning("Discarding data after CameraInfo '%s'", additional_info[-1])
                        except Exception:
                            logging.exception("Failure while decoding readout camera information.")
                        self._getReadoutCamInfo = False

                    if error_code in (4, 5):
                        # A new image is available on the dataport => Send to the special queue
                        if error_code == 4 and rfunc == "Livemonitor":
                            self.queue_img.put(rargs)
                        # Note: all other messages with error_code 4 or 5 are currently discarded
                        # as not of interest for now
                    else:  # send response including error_code to queue
                        self.queue_command_responses.put(msg_splitted)

        except Exception:
            logging.exception("Hamamatsu streak camera TCP/IP receiver thread failed.")
        finally:
            logging.info("Hamamatsu streak camera TCP/IP receiver thread ended.")

    def StartAcquisition(self, AcqMode):
        """
        Start an acquisition.
        :parameter AcqMode: (str) see AcqStart
        """
        # Note: sync acquisition calls directly AcqStart

        # restart thread in case it was terminated
        if not self._readoutcam.t_image.isAlive():
            self.AcqLiveMonitor("RingBuffer", nbBuffers=3)
            self._readoutcam.t_image = threading.Thread(target=self._readoutcam._getDataFromBuffer)
            self._readoutcam.t_image.start()

        try:
            self.AcqStart(AcqMode)
        except RemoteExError as ex:
            if ex.errno != 7:  # 7 = command already running
                raise
            logging.debug("Starting acquisition currently not possible. An acquisition or live mode might be still "
                          "running. Will stop and restart live mode.")
            self.AcqStop()
            start = time.time()
            timeout = 5
            while int(self.AsyncCommandStatus()[0]):
                time.sleep(0)
                if time.time() > start + timeout:
                    logging.error("Could not start acquisition.")
                    return
            self.AcqStart(AcqMode)

    # === General commands ============================================================

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
        # Note: "1": App starts visible (use 0 for invisible)
        # returnValue = self.sendCommand("AppStart", "1", "C:\ProgramData\Hamamatsu\HPDTA\HPDTA8.ini")
        # need ~15 s when starting App -> use larger timeout
        self.sendCommand("AppStart", timeout=30)

    def AppEnd(self):
        """Close RemoteEx."""
        logging.debug("Closing RemoteEx App.")
        self.sendCommand("AppEnd")

    def AppInfo(self, parameter):
        """Returns information about the application.
        :parameter parameter: (str) Date, Version, Directory, Title, Titlelong, ProgDataDir.
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
        :parameter parameter: (str) ImageSize, Message, Temperature, GateMode, MCPGain, Mode, Plugin, Shutter,
                                    StreakCamera,TimeRange
        :returns: Label, Current value, Param type (PARAM_TYPE_DISPLAY)
        """
        return self.sendCommand("MainParamInfo", parameter)

    def MainParamInfoEx(self, parameter):
        """Returns information about parameters visible in the main window. Returns more detailed information in
        case of a PARAM_TYPE_LIST than MainParamInfo.
        :parameter parameter: (str) see _mainParamInfo
        :returns: Label, Current value, Param type (PARAM_TYPE_DISPLAY)"""
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
        :parameter iSwitch: (int) 0 to switch sync off, 1 to switch sync on."""
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
        :parameter value: (str) PARAM_TYPE_BOOL."""
        value = str(value)
        self.sendCommand("GenParamSet", parameter, value)

    def GenParamInfo(self, parameter):
        """Returns information about the specified parameter.
        :parameter parameter: (str) RestoreWindowPos: Restore window positions
                    UserFunctions: Call user functions
                    ShowStreakControl: Shows or hides the Streak status/control dialog
                    ShowDelay1Control: Shows or hides the Delay1 status/control dialog
                    ShowDelay2Control: Shows or hides the Delay2 status/control dialog
                    ShowSpectrControl: Shows or hides the Spectrograph status/control dialog
        :returns: Label, Current value (bool), Param Type (PARAM_TYPE_BOOL)"""
        try:
            label, val, typ = self.sendCommand("GenParamInfo", parameter)
            param_typ = int(typ)
            value = bool(val)
        except (IndexError, TypeError, ValueError) as ex:
            raise IOError("Failed to decode response from GenParamInfo: %s" % ex)
        return label, value, param_typ

    def GenParamInfoEx(self, parameter):
        """Returns the information about the specified parameter. Returns more detailed information
        in case of a PARAM_TYPE_LIST than GenParamInfo.
        :parameter parameter: (str) see GenParamInfo
        :returns: Label, Current value (bool), Param Type (PARAM_TYPE_BOOL)"""
        try:
            label, val, typ = self.sendCommand("GenParamInfoEx", parameter)
            param_typ = int(typ)
            value = bool(val)
        except (IndexError, TypeError, ValueError) as ex:
            raise IOError("Failed to decode response from GenParamInfo: %s" % ex)
        return label, value, param_typ

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
        self.sendCommand("AcqStart", AcqMode)

    def AcqStatus(self):
        """Returns the status of an acquisition.
        :return: status, mode"""
        return self.sendCommand("AcqStatus")

    def AcqStop(self, timeout=1):
        """Stops the currently running acquisition.
        :parameter timeout: (0.001<= float <=60) The timeout value (in s)
        until this command should wait for an acquisition to end.
        :return: (float) timeout (in s)"""
        # Note: RemoteEx needs timeout in ms
        self.sendCommand("AcqStop", str(timeout * 1000))  # returns empty list
        return timeout

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
            param type: PARAM_TYPE_BOOL, PARAM_TYPE_NUMERIC, PARAM_TYPE_LIST,
                PARAM_TYPE_STRING, PARAM_TYPE_EXPTIME, PARAM_TYPE_DISPLAY
            """
        return self.sendCommand("AcqParamInfo", parameter)

    def AcqParamInfoEx(self, parameter):
        """Returns information about the specified parameter. Returns more detailed information in case of a list
        parameter (Parameter type = 2) than AcqParamInfo. In case of a numeric parameter (Parameter
        type = 1) it additionally returns the step width
        :parameter parameter: (str) see AcqParamGet
        :return: Label, current value, param type, min (num type only), max (num type only)
            param type: PARAM_TYPE_BOOL, PARAM_TYPE_NUMERIC, PARAM_TYPE_LIST,
                PARAM_TYPE_STRING, PARAM_TYPE_EXPTIME, PARAM_TYPE_DISPLAY
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

    def AcqLiveMonitor(self, monitorType, nbBuffers=None, *args):
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
        # TODO check monitorType and then add the correct opt param to the fct call when defined by the caller
        # Note: args can be only one argument
        if nbBuffers and monitorType == "RingBuffer":
            args = (str(nbBuffers),)
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
                        Unix or Linux: Seconds and μseconds since 01.01.1970"""
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
                    TimingMode: Timing mode (Internal / External) # Note: exists for OrcaFlash 4.0
                    TriggerMode: Trigger mode  # Note: exists for OrcaFlash 4.0
                    TriggerSource: Trigger source  # Note: exists for OrcaFlash 4.0
                    TriggerPolarity: Trigger polarity  # Note: exists for OrcaFlash 4.0
                    ScanMode: Scan mode  # Note: exists for OrcaFlash 4.0
                    Binning: Binning factor  # Note: exists for OrcaFlash 4.0
                    CCDArea: CCD area
                    LightMode: Light mode
                    Hoffs: Horizontal Offset (Subarray)
                    HWidth: Horizontal Width (Subarray)  # Note: exists for OrcaFlash 4.0
                    VOffs: Vertical Offset (Subarray)
                    VWidth: Vertical Width (Subarray)  # Note: exists for OrcaFlash 4.0
                    ShowGainOffset: Show Gain and Offset on acquisition dialog  # Note: exists for OrcaFlash 4.0
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
                    CameraInfo: Camera info text  # Note: exists for OrcaFlash 4.0
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
        :parameter parameter: (str) see CamParamGet
        :parameter value: (str) value for param"""
        # Note: When using self.acqMode = "SingleLive" parameters regarding the readout camera
        # need to be changed via location = "Live"!!!
        self.sendCommand("CamParamSet", location, parameter, value)

    def CamParamInfo(self, location, parameter):
        """Returns information about the specified parameter.
        :parameter location: (str) see CamParamGet
        :parameter parameter: (str) see CamParamGet
        :return: Label, current value, param type, min (num type only), max (num type only)
            param type: PARAM_TYPE_BOOL, PARAM_TYPE_NUMERIC, PARAM_TYPE_LIST,
                PARAM_TYPE_STRING, PARAM_TYPE_EXPTIME, PARAM_TYPE_DISPLAY"""
        return self.sendCommand("CamParamInfo", location, parameter)

    def CamParamInfoEx(self, location, parameter):
        """Returns information about the specified parameter.
        Returns more detailed information in case of a list parameter (Parameter type = 2) than CamParamInfo.
        :parameter location: (str) see CamParamGet
        :parameter parameter: (str) see CamParamGet
        :return: Label, current value, param type, min (num type only), max (num type only)
            param type: PARAM_TYPE_BOOL, PARAM_TYPE_NUMERIC, PARAM_TYPE_LIST,
                PARAM_TYPE_STRING, PARAM_TYPE_EXPTIME, PARAM_TYPE_DISPLAY"""
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

    def CamSetupSendSerial(self):
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
                                DeviceName, PluginName, OptionName1, OptionName2, OptionName3, OptionName4

                                Additionally to the parameters from the status/control boxes the user can get or set
                                also the following parameters from the Device options:
                                Streakcamera:
                                AutoMCP, AutoStreakDelay, AutoStreakShutter, DoStatusRegularly, AutoActionWaitTime
                                Delaybox:
                                AutoDelayDelay
        :return: (list) value of parameter"""
        return self.sendCommand("DevParamGet", location, parameter)

    def DevParamSet(self, location, parameter, value):
        """Sets the specified parameter of the acquisition options.
        :parameter location: (str) see DevParamGet
        :parameter parameter: (str) see DevParamGet
        :parameter value: (str) The value has to be written as it appears in the corresponding control."""

        # convert any input to a string as requested by RemoteEx
        if not isinstance(value, str):
            value = self._convertInput2Str(value)

        self.sendCommand("DevParamSet", location, parameter, value)

    def _convertInput2Str(self, input_value):
        """Function that converts any input to a string as requested by RemoteEx."""
        if isinstance(input_value, int):
            return str(input_value)
        elif isinstance(input_value, float):
            value = '{:.11f}'.format(input_value)
            # important remove all additional zeros and 0. -> 0: otherwise RemoteEx error!
            return value.rstrip("0").rstrip(".")
        else:
            logging.debug("Requested conversion of input type %s is not supported.", type(input))

    def DevParamInfo(self, location, parameter):
        """Return information about the specified parameter.
        :parameter location: (str) see DevParamGet
        :parameter parameter: (str) see DevParamGet
        :return: Label, current value, param type, min (numerical only), max (numerical only).
            param type: PARAM_TYPE_NUMERIC, PARAM_TYPE_LIST,
            Note: In case of a list the number of entries and all list entries are returned in the response of the
            DevParamInfoEx command."""
        return self.sendCommand("DevParamInfo", location, parameter)

    def DevParamInfoEx(self, location, parameter):
        """Return information about the specified parameter.
        Returns more detailed information in case of a list parameter (param type=2) than DevParamInfo.
        :parameter location: (str) see DevParamGet
        :parameter parameter: (str) see DevParamGet
        :return: Control available, status available, label, current value, param type, number of entries, entries.
            param type: PARAM_TYPE_NUMERIC, PARAM_TYPE_LIST"""
        return self.sendCommand("DevParamInfoEx", location, parameter)

    def DevParamsList(self, device):
        """Return list of all parameters of a specified device.
        :parameter device: (str) see location in DevParamGet
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
        :parameter parameter: (str) see SeqParamGet
        :parameter value: (str) The value for the sequence option or parameter."""
        self.sendCommand("SeqParamSet", parameter, value)

    def SeqParamInfo(self, parameter):
        """Return information about the specified parameter.
        :parameter parameter: (str) see SeqParamGet
        :return: label, current value, param type"""
        return self.sendCommand("SeqParamInfo", parameter)

    def SeqParamInfoEx(self, parameter):
        """Return information about the specified parameter.
        Returns more detailed information in case of a list parameter (param type=2) than SeqParamInfo.
        In case of a numeric parameter (Parameter type = 1) it additionally returns the step width.
        :parameter parameter: (str) see SeqParamGet
        :return: label, current value, param type"""
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

    def SeqSave(self, imageType, fileName, overwrite=False):
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
        :parameter overwrite: (bool) If this is set to true
                            the file is also saved if it exists. If set to false
                            the file is not saved if it already exists and an error is returned."""
        self.sendCommand("SeqSave", imageType, fileName, str(overwrite))

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
        :return: (str) index"""
        return self.sendCommand("SeqImgIndexGet")

    def SeqImgExist(self):
        """Can be used to find out whether an image sequence exists.
        :return: (str) true/false"""
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
            BirdViewSmoothing: Smoothing for Bird view display (from 9.4 pf0)
            BirdViewScaling: Intensity scaling for Bird view display (from 9.4 pf0)
            FixedITEXHeader: Save ITEX files with fixed header
        :return: value of parameter"""
        return self.sendCommand("ImgParamGet", parameter)

    def ImgParamSet(self, parameter, value):
        """Sets the values of the image options.
        :parameter parameter: (str) see ImgParamGet
        :parameter value: (str) TODO"""
        self.sendCommand("ImgParamSet", parameter, value)

    def ImgRingBufferGet(self, type, seqNumber, filename=None):
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
        :parameter filename: (str) location to write the data to. Raw data is written to the file without any header.
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
        args = ()
        if filename:
            args += (filename,)
        return self.sendCommand("ImgRingBufferGet", type, seqNumber, *args)

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
        """
        Converts a value plus its corresponding unit as received from RemoteEx, to a value.
        :param value: (str) value
        :param unit: (str) unit
        :return: (float) value
        """
        units = ['s', 'ms', 'us', 'ns']
        try:
            value = float(value) * 10 ** (units.index(unit) * -3)
        except ValueError:
            raise ValueError("Unit conversion %s for value %s not supported" % (unit, value))

        return value

    def convertTime2Unit(self, value):
        """
        Converts a value to a value plus corresponding unit, which will be accepted by RemoteEx.
        :param value: (float) value
        :return: (str) a string consisting of a value plus unit
        """
        # Note: For CamParamSet it doesn't matter if value + unit includes a white space or not.
        # However, for DevParamSet it does matter!!!

        if 1e-9 <= value < 1:
            units = ['s', 'ms', 'us', 'ns']
            magnitude = math.log10(abs(value)) // 3
            conversion = 10 ** (magnitude * -3)
            unit_index = int(abs(magnitude))
            value_raw = str(int(round(value * conversion))) + " " + units[unit_index]
        elif 1 <= value <= 10:  # Note values > 10s are caught by VA as not in range of VA
            value_raw = "%.3f s" % (value,)  # only used for exposure time -> can be float
        else:
            raise ValueError("Unit conversion for value %s not supported" % value)

        return value_raw


class StreakCameraDataFlow(model.DataFlow):
    """
    Represents Hamamatsu streak camera.
    """

    def __init__(self,  start_func, stop_func, sync_func):
        """
        camera: instance ready to acquire images
        """
        # initialize dataset, which can be subscribed to, to receive data acquired by the dataflow
        model.DataFlow.__init__(self)
        self._start = start_func
        self._stop = stop_func
        self._sync = sync_func
        self.active = False

    # start/stop_generate are _never_ called simultaneously (thread-safe)
    def start_generate(self):
        """
        Start the dataflow.
        """
        self._start()
        self.active = True

    def stop_generate(self):
        """
        Stop the dataflow.
        """
        self._stop()
        self.active = False

    def synchronizedOn(self, event):
        """
        Synchronize the dataflow.
        """
        self._sync(event)

