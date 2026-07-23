# -*- coding: utf-8 -*-
"""
Created on Aug 2018

@author: Sabrina Rossberger, Delmic

Copyright © 2018 Sabrina Rossberger, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
"""

import collections
import functools
import select
import socketserver
import threading
import logging
import math
import numbers
import queue
import socket
import time
import re
from typing import Tuple, Optional, Dict, Callable, List

import numpy

from odemis import model, util
from odemis.model import oneway
from odemis.util import to_str_escape, driver

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

DELAY_NAMES = {
    # Delay A is on the DG645, while the C12270 has "Delay Time". They are mapped to the same VA.
    "Delay A": "triggerDelay",
    "Delay Time": "triggerDelay",
    "Delay B": "delayB",
    "Delay C": "delayC",
    "Delay D": "delayD",
    "Delay E": "delayE",
    "Delay F": "delayF",
    "Delay G": "delayG",
    "Delay H": "delayH",
}

# Commands that can be passed to queue_img as Tuple[CMD_*, ...].
# For CMD_IMG, the extra elements are the image event arguments (previously passed as a list).
# For all other commands, no extra arguments are passed.
CMD_QUIT = "Q"
CMD_STOP = "F"
CMD_SW_TRIGGER = "T"
CMD_IMG = "I"
CMD_START = "S"


class TerminationRequested(Exception):
    """
    Acquisition thread termination requested.
    """
    pass

class RemoteExError(IOError):
    def __init__(self, errno, *args, **kwargs):
        # Needed for pickling, cf https://bugs.python.org/issue1692335 (fixed in Python 3.3)
        desc = self._errordict.get(errno, "Unknown RemoteEx error.")
        strerror = "RemoteEx error %d: %s" % (errno, desc)
        IOError.__init__(self, errno, strerror, *args, **kwargs)

    def __str__(self):
        return self.strerror

    _errordict = {
        0: "Command successfully executed",
        # command must be followed by parentheses and must have the correct number and type of
        # parameters separated by comma
        1: "Invalid syntax for command",
        2: "Command or Parameters are unknown",
        3: "Command currently not possible",
        6: "Parameter is missing",
        7: "Command cannot be executed",
        8: "An error has occurred during execution",
        9: "Data cannot be sent by TCP-IP",
        10: "Value of a parameter is out of range",
    }


class ReadoutCamera(model.DigitalCamera):
    """
    Represents Hamamatsu readout camera.
    """

    def __init__(self, name: str, role: str, parent: model.HwComponent,
                 spectrograph: Optional[model.HwComponent] = None,
                 can_photon_counting: bool = False,
                 **kwargs):
        """ Initializes the Hamamatsu OrcaFlash readout camera.
        :param name: as in Odemis
        :param role: as in Odemis
        :param parent: class StreakCamera
        :param spectrograph: should provide .position and getPixelToWavelength() to
        obtain the wavelength list.
        :param can_photon_counting: whether the camera supports photon counting
        :param transp: (int, int) transpose the resolution from the camera to the user (see Detector)
        """
        self.parent = parent

        self._spectrograph = spectrograph
        if not spectrograph:
            logging.warning("No spectrograph specified. No wavelength metadata will be attached.")

        try:
            cam_info = parent.CamParamGet("Setup", "CameraInfo")
            # Should have 1 argument, containing the camera info as multiline string, like this:
            # "OrcaFlash 4.0 V3\r\nProduct number: C13440-20C\r\nSerial number: 301730\r\nFirmware: 4.20.B\r\nVersion: 4.20.B03-A19-B02-4.02"
            cam_info = cam_info[0].split("\r\n")
        except IOError:
            logging.exception("Failed to get readout camera info")
            # Might be due to the frame grabber failing to initialise (sometimes happens),
            # or the camera not being turned on.
            raise model.HwError("Failed to find readout camera, check it is powered. If powered, restart the Hamamatsu PC")

        # Only initialise the component after we are sure not to raise HwError,
        # because HwError tells the back-end it should try again. As this
        # component is a child it doesn't get automatically unregistered from
        # the back-end (Pyro4) on, and next trial would fail.
        super().__init__(name, role, parent=parent, **kwargs)

        try:
            self._hwVersion = cam_info[0] + ", " + cam_info[1] + ", " + cam_info[2]  # needs to be a string
        except Exception:
            logging.debug("Could not get hardware information for streak readout camera.", exc_info=True)
        self._metadata[model.MD_HW_VERSION] = self._hwVersion
        try:
            self._swVersion = cam_info[3] + ", " + cam_info[4]  # needs to be a string
        except Exception:
            logging.debug("Could not get software information for streak readout camera.", exc_info=True)
        self._metadata[model.MD_SW_VERSION] = self._swVersion
        self._metadata[model.MD_DET_TYPE] = model.MD_DT_INTEGRATING

        avail_params = parent.CamParamsList("Setup")
        # Set parameters readout camera
        if "TimingMode" in avail_params:
            parent.CamParamSet("Setup", "TimingMode", "Internal timing")  # TODO external check displayed command in GUI
        if "ShowGainOffset" in avail_params:
            parent.CamParamSet("Setup", "ShowGainOffset", 'True')
        # Camera is not synchronized, so we don't need to set the trigger mode
        # parent.CamParamSet("Setup", "TriggerMode", 'Edge trigger')
        # parent.CamParamSet("Setup", "TriggerSource", 'BNC')
        # parent.CamParamSet("Setup", "TriggerPolarity", 'neg.')
        parent.CamParamSet("Setup", "ScanMode", 'Subarray')

        # sensor size (resolution)
        # Note: sensor size of OrcaFlash is actually much larger (2048px x 2048px)
        # However, only a smaller subarea is used for operating the streak system.
        # x (lambda): horizontal, y (time): vertical
        parent.CamParamSet("Setup", "Binning", '1 x 1')  # Force binning to 1x1 to read the maximum resolution
        full_res = self._transposeSizeToUser((int(parent.CamParamGet("Setup", "HWidth")[0]),
                                              int(parent.CamParamGet("Setup", "VWidth")[0])))
        # We never change the offset, but it's handy to log them, in case of issue with the settings
        logging.debug("Readout camera offset: %s, %s",
                      parent.CamParamGet("Setup", "HOffs")[0], parent.CamParamGet("Setup", "VOffs")[0])
        self._metadata[model.MD_SENSOR_SIZE] = full_res
        self._metadata[model.MD_DIMS] = "TC"

        # 16-bit
        self._shape = full_res + (2 ** 16,)

        parent.CamParamSet("Setup", "Binning", '2 x 2')  # Recommended binning
        self._binning = self._transposeSizeToUser(self._getBinning())  # used by _setResolution

        # For now, changing the resolution is not really supported, as the full field of view is always used.
        # It's just automatically adjusted when changing the binning.
        self._resolution = int(full_res[0] / self._binning[0]), int(full_res[1] / self._binning[1])
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
        exp_time = self.GetCamExpTime()
        self.exposureTime = model.FloatContinuous(exp_time, range_exp, unit="s", setter=self._setCamExpTime)
        self._metadata[model.MD_EXP_TIME] = self.exposureTime.value
        # Note: timeRange of streakunit > exposureTime readoutcam is possible and okay.

        if can_photon_counting:
            # When set, the photon-counting procedure is used to do acquisition.
            # Changing while acquiring is not supported.
            self.photonCounting = model.BooleanVA(False, setter=self._set_photon_counting)
            # Number of exposures during photon-counting. It has no effect when photon-counting acquisition
            # is disabled.
            count_range = self._get_exposure_count_range()
            integ_count = self._get_exposure_count()
            self.pcIntegrationCounts = model.IntContinuous(integ_count, count_range, unit="",
                                                           setter=self._set_exposure_count)
            # Dedicated exposure time for photon-counting mode
            range_exp = self._get_pc_exp_time_range()
            exp_time = self._get_pc_exp_time()
            self.pcExposureTime = model.FloatContinuous(exp_time, range_exp, unit="s", setter=self._set_pc_exp_time)

            theshold_range = self._get_threshold_range()
            threshold = self._get_pc_threshold()
            self.pcThreshold = model.IntContinuous(threshold, theshold_range, unit="counts", setter=self._set_pc_threshold)

            # Refresh regularly the values. Only done for photon-counting values, because these
            # values are typically modified directly in HPDTA by the user during calibration.
            self._va_poll = util.RepeatingTimer(5, self._update_settings,
                                                "Readout cam settings polling")
            self._va_poll.start()

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
        self._queue_events = collections.deque()
        self._acq_sync_lock = threading.Lock()

        self.t_image = None  # thread for reading images from the RingBuffer
        self._update_monitor_mode(False)

        self.data = StreakCameraDataFlow(self._start, self._stop, self._sync)

    def terminate(self):
        try:
            self._stop()  # stop any acquisition
        except Exception:
            pass

        # terminate image thread
        if self.t_image and self.t_image.is_alive():
            self.parent.queue_img.put((CMD_QUIT,))  # Special message to request end of the thread
            self.t_image.join(5)

        # Just in case the acquisition thread failed, directly stop the acquisition
        try:
            self.parent.AcqStop()
        except Exception:
            pass

        if self._va_poll.is_alive():
            self._va_poll.cancel()
            self._va_poll.join(1)

        super().terminate()

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

    def _update_settings(self) -> None:
        """
        Read the photon-counting settings from HPDTA and reflect them on the VAs
        """
        logging.debug("Updating readout cam photon-counting settings")
        try:
            exp_time = self._get_pc_exp_time()
            if exp_time != self.pcExposureTime.value:
                self.pcExposureTime._value = exp_time
                self.pcExposureTime.notify(exp_time)

            count = self._get_exposure_count()
            if count != self.pcIntegrationCounts.value:
                self.pcIntegrationCounts._value = count
                self.pcIntegrationCounts.notify(count)

            threshold = self._get_pc_threshold()
            if threshold != self.pcThreshold.value:
                self.pcThreshold._value = threshold
                self.pcThreshold.notify(threshold)

        except Exception:
            logging.exception("Unexpected failure when polling photon-counting settings")


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
        :param value: (2-tuple int) binning value to set
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
            self._stop()
            self.parent.AcqStop()
            self.parent.CamParamSet("Setup", "Binning", binning)
            self._start()
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
        # HPDTA seems to use this computation:
        # max_res = self.resolution.range[1]
        # new_res = (int(max_res[0] // self._binning[0]),
        #            int(max_res[1] // self._binning[1]))  # floor division

        # Just accept whatever HPDTA computed
        res = self._transposeSizeToUser((int(self.parent.CamParamGet("Setup", "HWidth")[0]),
                                         int(self.parent.CamParamGet("Setup", "VWidth")[0])))

        self._resolution = res
        if self._spectrograph:
            self._updateWavelengthList()  # WavelengthList has to be the same length as the resolution

        return res

    def _set_photon_counting(self, value: bool) -> bool:
        """
        Sets the photon-counting mode for the camera.
        :param value: (bool) True to enable photon-counting, False to disable
        :return: current photon-counting mode
        """
        if value == self.photonCounting.value:
            return value

        if self.data.active:
            # We could support it, but it's a lot of extra complexity to the code, and in reality, never used.
            logging.warning("Photon-counting mode changed to %s while acquiring: not supported", value)

        # On HPDTA, the exposure time is different setting in photon-counting mode. Typically, in
        # photon-counting mode, a very short exposure time is used.
        # TODO: To make it easier for the user, automatically read the exposure time corresponding
        # to the mode, and switch the value of exposureTime. BUT that only works if the .exposureTime
        # doesn't set always both exposure times!
        # However, it can be annoying to automatically set back previous settings, as the order
        # would matter (ie, photonCounting must be set first, otherwise it resets the exposure Time)

        return value

    def _getCamExpTimeRange(self):
        """
        Get min and max values for the camera exposure time.
        :return: tuple containing min and max exposure time
        """
        # Although it returns list of possible exposure times, any value between the smallest and
        # largest value is accepted.
        exp = self.parent.CamParamInfoEx("Live", "Exposure")
        # Values in returned list "exp" are in order.
        min_value = exp[4]  # First exposure time, eg "1200 us"
        max_value = exp[-1]  # Last/longest exposure time, eg "1 s"

        min_value_raw, min_unit = min_value.split(' ')[0:2]
        max_value_raw, max_unit = max_value.split(' ')[0:2]

        min_exp = self.parent.convertUnit2Time(min_value_raw, min_unit)
        max_exp = self.parent.convertUnit2Time(max_value_raw, max_unit)

        return min_exp, max_exp

    def GetCamExpTime(self) -> float:
        """
        Get the camera exposure time.
        Converts the provided value received from RemoteEx into sec.
        :return: (float) exposure time in sec
        """
        exp_time_raw = self.parent.CamParamGet("Live", "Exposure")[0].split(' ')
        try:
            exp_time = self.parent.convertUnit2Time(exp_time_raw[0], exp_time_raw[1])
        except Exception:
            raise IOError("Exposure time of %s failed to be converted to a float" % exp_time_raw)

        return exp_time

    def _setCamExpTime(self, value: float) -> float:
        """
        Set the camera exposure time in photon-counting mode.
        Converts the time range in sec into a for RemoteEx readable format.
        :param value: (float) exposure time to be set
        :return: (float) current exposure time
        """
        try:
            exp_time_raw = self.parent.convertTime2Unit(value)
        except Exception:
            raise IOError("Exposure time of %s sec is not supported for read-out camera." % value)

        # Note: RemoteEx uses different exposure times depending on acquisition mode
        # If we support e.g. photon counting, we need to specify a different location in RemoteEx.
        # See pcExposureTime.
        self.parent.CamParamSet("Live", "Exposure", exp_time_raw)

        # Although it is associated to a list, almost any value is accepted, with just a small rounding.
        exp_time = self.GetCamExpTime()
        return exp_time

    def _get_pc_exp_time_range(self) -> Tuple[float, float]:
        """
        Get min and max values for the camera exposure time.
        :return: tuple containing min and max exposure time
        """
        exp = self.parent.CamParamInfoEx("PC", "Exposure")  # returns list of possible exposure times
        # Values in returned list "exp" are in order.
        min_value = exp[4]  # First exposure time, eg "1200 us"
        max_value = exp[-1]  # Last/longest exposure time, eg "1 s"

        min_value_raw, min_unit = min_value.split(' ')[0:2]
        max_value_raw, max_unit = max_value.split(' ')[0:2]

        min_exp = self.parent.convertUnit2Time(min_value_raw, min_unit)
        max_exp = self.parent.convertUnit2Time(max_value_raw, max_unit)

        return min_exp, max_exp

    def _get_pc_exp_time(self) -> float:
        """
        Get the camera exposure time in photon-counting mode
        Converts the provided value received from RemoteEx into sec.
        :return: (float) exposure time in sec
        """
        exp_time_raw = self.parent.CamParamGet("PC", "Exposure")[0].split(' ')
        try:
            exp_time = self.parent.convertUnit2Time(exp_time_raw[0], exp_time_raw[1])
        except Exception:
            raise IOError("Exposure time of %s failed to be converted to a float" % exp_time_raw)

        return exp_time

    def _set_pc_exp_time(self, value: float) -> float:
        """
        Set the camera exposure time in photon-counting mode.
        Converts the time range in sec into a for RemoteEx readable format.
        :param value: (float) exposure time to be set
        :return: (float) current exposure time
        """
        try:
            exp_time_raw = self.parent.convertTime2Unit(value)
        except Exception:
            raise IOError("Exposure time of %s sec is not supported for read-out camera." % value)

        try:
            self.parent.CamParamSet("PC", "Exposure", exp_time_raw)
        except Exception:
            logging.warning("Failed to set exposure time for photon-counting mode.")

        exp_time = self._get_pc_exp_time()
        return exp_time

    def _get_exposure_count(self) -> int:
        """
        Reads the current exposure count from the camera, in photon-counting mode.
        """
        count = self.parent.CamParamGet("PC", "NrExposures")
        return int(count[0])

    def _get_exposure_count_range(self) -> Tuple[int, int]:
        """
        Get min and max values for the camera exposure count, in photon-counting mode.
        :return: tuple containing min and max exposure count
        """
        count_info = self.parent.CamParamInfoEx("PC", "NrExposures")
        min_value = int(count_info[3])
        max_value = int(count_info[4])

        return min_value, max_value

    def _set_exposure_count(self, count: int) -> int:
        """
        Sets the exposure count for the camera, in photon-counting mode.
        :param count: requested exposure count
        :return: actual exposure count
        """
        self.parent.CamParamSet("PC", "NrExposures", str(count))
        return count  # Any value in range is accepted, so no need to read it back

    def _get_threshold_range(self) -> Tuple[int, int]:
        """
        Get min and max values for the camera threshold, in photon-counting mode.
        :return: tuple containing min and max threshold
        """
        thresh_info = self.parent.CamParamInfoEx("PC", "Threshold")
        min_value = int(thresh_info[3])
        max_value = int(thresh_info[4])

        return min_value, max_value

    def _get_pc_threshold(self) -> int:
        """
        Get the camera threshold in photon-counting mode.
        :return: (int) threshold in counts
        """
        threshold = self.parent.CamParamGet("PC", "Threshold")
        return int(threshold[0])

    def _set_pc_threshold(self, threshold: int) -> int:
        """
        Set the camera threshold in photon-counting mode.
        :param threshold: (int) threshold in counts
        :return: (int) actual threshold set
        """
        self.parent.CamParamSet("PC", "Threshold", str(threshold))
        return threshold  # Any value in range is accepted, so no need to read it back

    def _update_monitor_mode(self, active: bool, photon_counting: bool = False) -> None:
        """
        Update the image monitoring mode of HPDTA, to receive the correct image, depending on the
        acquisition mode.
        :param active: (bool) True if acquisition is active, False otherwise
        :param photon_counting: (bool) True if photon-counting mode is enabled, False otherwise
        """
        if active:
            if photon_counting:
                # Receive the final image, once the acquisition stops.
                # In photon-counting, the intermediary images are not useful. We acquire one image
                # at a time.
                self.parent.AcqLiveMonitor("Off")
                self.parent.AcqAcqMonitor("EndAcq")
                # Typically, each acquisition is opened in a separate window, and if too many windows
                # are opened (19), the acquisition can fail.
                self.parent.ImgParamSet("AcquireToSameWindow", "1")
            else:
                # For standard mode, use the "live" mode to get a fluid image.
                # The last image is the same as the latest from the ring buffer, so we don't need it.
                self.parent.AcqLiveMonitor("RingBuffer", nbBuffers=3)
                self.parent.AcqAcqMonitor("Off")
        else:
            self.parent.AcqLiveMonitor("Off")
            self.parent.AcqAcqMonitor("Off")

    def _start(self):
        """
        Start an acquisition.
        """
        # restart thread in case it was terminated
        if not self.t_image or not self.t_image.is_alive():
            # start acquisition thread, which waits for monitor messages that indicate an image
            # is available.
            self.t_image = threading.Thread(target=self._acquire)
            self.t_image.start()

        self.parent.queue_img.put((CMD_START,))

        # Force trigger rate reading
        try:
            # This is a bit of a hack: ideally, we would subscribe to the triggerRate VA.
            # However, with the hamamatsurx, that would require very frequent polling.
            # So, instead, we query the device just after starting acquiring. That's the most likely
            # moment the trigger is useful to read. If it changes during acquisition, it'll still be
            # updated via polling (every 5s), but that's a very rare case, which typically would
            # not require good metadata anyway.
            if self.parent._delaybox:
                self._metadata[model.MD_TRIGGER_RATE] = self.parent._delaybox.triggerRate.value
        except Exception:
            logging.exception("Failed to update trigger rate")

    def _stop(self):
        """
        Stop the acquisition.
        """
        self.parent.queue_img.put((CMD_STOP,))
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
        self._queue_events.append(time.time())
        self.parent.queue_img.put((CMD_SW_TRIGGER,))

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
        Create dict containing all metadata from the children readout camera, streak unit, delay generator
        and the metadata from the parent streak camera.
        """
        md_dev = self.parent._streakunit.getMetadata()
        for key in md_dev.keys():
            if key not in md:
                md[key] = md_dev[key]
            elif key in (model.MD_HW_NAME, model.MD_HW_VERSION, model.MD_SW_VERSION):
                md[key] = ", ".join([md[key], md_dev[key]])
        return md

    def _get_time_scale(self) -> numpy.ndarray:
        """
        Retrieve the scale value for the time axis of the readout camera.
        :return: one dimensional array, with one valueper pixel along the Y dimension,
        corresponding to the time (in s) for the pixels on this line.
        :raises: TimeoutError if the scaling table could not be retrieved.
        """
        logging.debug("Request scaling table for time axis of Hamamatsu streak camera.")
        # request scaling table
        scl_table_info = self.parent.ImgDataGet("current", "ScalingTable", "Vertical")
        scl_table_size = int(scl_table_info[0]) * 4  # num of bytes we need to receive

        # receive the bytes via the dataport
        tab = b""
        try:
            while len(tab) < scl_table_size:  # keep receiving bytes until we received all expected bytes
                tab += self.parent._dataport.recv(scl_table_size)
        except socket.timeout as ex:
            raise TimeoutError(f"Did not receive a scaling table: {ex}")

        table = numpy.frombuffer(tab, dtype=numpy.float32)  # convert to (read-only) array
        # No way to read the unit prefix, so need to "guess" it
        t_factor = self.parent._streakunit.get_time_scale_factor()
        logging.debug("Received scaling table for time axis from %s to %s * %s s.",
                      table[0], table[-1], t_factor)
        # The prefix unit varies depending on the time range (eg, ns, us), so need scale it, based
        # on the time range of the streak unit.
        table = table * t_factor

        return table

    def _get_acq_msg(self, **kwargs) -> Tuple[str, ...]:
        """
        Read one message from the acquisition queue
        :return: message
        :raises queue.Empty: if no message on the queue
        :raise TerminationRequested: if CMD_QUIT is received
        """
        while True:
            cmd, *args = self.parent.queue_img.get(**kwargs)
            if cmd in (CMD_START, CMD_SW_TRIGGER, CMD_STOP, CMD_SW_TRIGGER, CMD_IMG, CMD_QUIT):
                logging.debug("Acq received message %s", cmd)
                break
            else:
                logging.warning("Acq received unexpected message %s, skipping", cmd)
                # wait for a new message

        if cmd == CMD_QUIT:
            raise TerminationRequested()

        return (cmd, *args)

    def _acquire(self):
        """
        Acquisition thread. Runs all the time, until receive a GEN_QUIT message.
        Managed via the .queue_img queue, by passing CMD_* messages.
        """
        try:
            while True: # Waiting/Acquiring loop
                # Wait until we have a start (or terminate) message
                photon_counting = self._acq_wait_start()

                # acquisition loop (until stop requested)
                self._acquire_images(photon_counting)

        except TerminationRequested:
            logging.debug("Acquisition thread requested to terminate")
        except Exception:
            logging.exception("Failure in acquisition thread")

        logging.debug("Acquisition thread ended")

    def _acq_wait_start(self) -> bool:
        """
        Blocks until the acquisition should start.
        It flushes the previous CMD_IMG (monitor) messages too.
        Note: it expects that the acquisition is stopped.
        raise TerminationRequested: if a terminate message was received
        """
        while True:
            cmd, *args = self._get_acq_msg(block=True)
            if cmd == CMD_START:
                logging.debug("Acquisition started")
                break
            # Either a (duplicated) Stop or a trigger => we don't care
            logging.debug("Skipped message %s as acquisition is stopped", cmd)

        # Not synchronized => start immediately
        photon_counting = hasattr(self, "photonCounting") and self.photonCounting.value
        self._update_monitor_mode(active=True, photon_counting=photon_counting)
        if not self._sync_event:
            if photon_counting:
                self.parent.StartAcquisition("PC")
            else:
                self.parent.StartAcquisition("Live")

        return photon_counting

    def _acquire_images(self, photon_counting: bool):
        """
        This method runs in a separate thread and waits for messages in queue indicating
        that some data was received. The image is then received from the device via the dataport IP socket or
        the vertical scaling table is received, which corresponds to a time range for a single sweep.
        It corrects the vertical time information. The table contains the actual timestamps for each px.
        The camera should already be prepared with a RingBuffer.
        """
        logging.debug("Starting data reception.")

        # TODO: handle changing synchronization while acquiring. It could be done by sending
        # a CMD message to report that the synchronization has changed. Currently changing the
        # synchronization during, or even just after stopping a photon-counting acqusition can
        # end-up with a long time (~10s) for the thread to catch up with the state.
        # TODO: handle settings change during the acquisition. See the binning change hack. This
        # could be done by (yet again) another CMD message to report that the settings have changed.
        is_receiving_image = False  # used during synchronised acquisition

        # When the acquisition is triggered per frame, store the start time of the frame for the metadata.
        # In case, it's "live", then the frame start will be computed from the time the image is received,
        # which is a little bit less accurate.
        if photon_counting:
            acq_start_t = time.time()
        else:
            acq_start_t = None

        try:
            while True:
                # In synchronized mode, with previous image received => need to wait for next trigger
                if self._sync_event and not is_receiving_image:
                    # Wait until HPDTA is ready again: there is no "pending" command (ie, either running or about to run)
                    timeout = 2  # s
                    start = time.time()
                    while int(self.parent.AsyncCommandStatus()[0]):
                        time.sleep(1e-3)
                        if time.time() > start + timeout:
                            logging.info("Asynchronous RemoteEx command still in process after %g s. "
                                         "Stopping acquisition to reset state.", timeout)
                            # most likely camera is in live-mode, so stop camera, and wait a bit more
                            self.parent.AcqStop()
                            start = time.time()

                    # Start next acquisition, if a trigger event was received.
                    # This handles all the cases (event delayed or not), because the message wait
                    # function gets a trigger event, it jumps back to the beginning of this while loop.
                    try:
                        event_time = self._queue_events.popleft()
                        acq_start_t = time.time()
                        logging.info("Starting acquisition delayed by %g s.", acq_start_t - event_time)
                        if photon_counting:
                            self.parent.AcqStart("PC")
                        else:
                            self.parent.AcqStart("SingleLive")
                        is_receiving_image = True
                    except IndexError:
                        # No event (yet) => fine, will wait via queue_img for CMD_SW_TRIGGER.
                        pass

                if self._sync_event and is_receiving_image:
                    if photon_counting:
                        acq_time = self.exposureTime.value * self.pcIntegrationCounts.value
                    else:
                        acq_time = self.exposureTime.value

                    timeout = max(acq_time * 2, 1)  # wait at least 1s
                else:
                    timeout = None

                # Check for the communication queue:
                # * (CMD_IMG, *args) -> monitor message from HPDTA = a new image is available
                # * (CMD_*,) -> from the Odemis backend, to report a change in acqusition, or trigger event
                try:
                    cmd, *args = self._get_acq_msg(block=True, timeout=timeout)
                except queue.Empty:
                    logging.warning("Failed to receive image from streak ccd. Timed out after %f s. Will try again.",
                                    timeout)
                    is_receiving_image = False
                    acq_start_t = None
                    continue

                if cmd == CMD_SW_TRIGGER:
                    if not self._sync_event:
                        logging.warning("Received a trigger event, but no sync event is set. Ignoring.")
                    else:
                        logging.info("Received event trigger")
                    continue
                elif cmd == CMD_STOP:
                    return
                elif cmd == CMD_IMG:  # info from the HPDTA image monitor
                    rargs = args
                else:
                    logging.warning("Received unknown command %s from queue_img, skipping.", cmd)
                    continue

                # If live mode, and multiple images are in the queue, flush all but the last one
                if not self._sync_event:
                    while True:
                        # keep reading to check if there might be a newer image for display
                        # in case we are too slow with reading
                        try:
                            cmd, *args = self._get_acq_msg(block=False)
                        except queue.Empty:
                            break  # no more images in queue

                        if cmd == CMD_START:
                            logging.debug("Received start command, but acquisition already started. Ignoring.")
                            continue  # ignore, acquisition was already started
                        elif cmd == CMD_SW_TRIGGER:
                            logging.warning("Received a trigger event, but no sync event is set. Ignoring.")
                            continue
                        elif cmd == CMD_STOP:
                            return
                        elif cmd == CMD_IMG:  # info from the HPDTA image monitor
                            logging.debug("Discarding previous image")
                            rargs = args
                        else:
                            logging.warning("Received unknown command %s from queue_img, skipping.",
                                            cmd)
                            continue
                    logging.info("No more images in queue, will read the latest one.")

                try:
                    image = self._get_image(rargs, acq_start_t, photon_counting)  # get the image and metadata from the buffer
                    self.data.notify(image)  # send to the listeners of the DataFlow
                except (OSError, TimeoutError) as ex:
                    logging.warning("Failed to receive image: %s", ex)
                finally:
                    is_receiving_image = False

                # Photon-counting mode always only acquire a single image, so if no synchronization
                # is used, we need to start a new acquisition
                if photon_counting and not self._sync_event:
                    acq_start_t = time.time()
                    self.parent.StartAcquisition("PC")

        finally:
            self.parent.AcqStop()
            self._update_monitor_mode(active=False)

    def _get_image(self, event_args: List[str], acq_start_t: Optional[float], photon_counting: bool) -> model.DataArray:
        """
        Receive an image corresponding to the monitor event from HPDTA
        :param event_args: monitor event information, as received from HPDTA
        :param acq_start_t: time when the acquisition started, or None if not known
        :param photon_counting: True if photon-counting mode is enabled, False otherwise
        :return: the DataArray
        :raise OSError: if the image could not be received
        :raise TimeoutError: if the image could not be received in time
        """
        reception_time_image = time.time()

        # Getting the image is slightly different depending on the type of monitor
        # We use the "event" to differenciate: "ringbuffer" if LiveMonitor, and
        # "Endacq" or "Endpart" if AcqMonitor
        event = event_args[0].lower()
        if event == "ringbuffer":  # From AcqLiveMonitor
            img_num = event_args[1]
            img_info = self.parent.ImgRingBufferGet("Data", img_num)
            # returns: iDX,iDY,BBP,Type,seqnumber,timestamp
            img_num_actual = img_info[4]
            if img_num != img_num_actual:
                logging.warning(
                    "Requested image number %s, but received number %s. Will use it anyway.",
                    img_num, img_num_actual)
        elif event in ("endacq", "endpart"):  # From AcqAcqMonitor
            img_info = self.parent.ImgDataGet("current", "data")
            # returns: iDX,iDY,BBP,Type . Example: 672,508,2,0
        else:
            raise OSError(f"Received unknown event {event} from queue_img, skipping.")

        if not img_info:  # TODO check if this ever happens in log and if not, remove!
            raise OSError("Image info received from buffer is empty!")

        img_bpp = int(img_info[2])
        if img_bpp != 2:
            logging.warning("Received image with unexpected depth of %s bytes, will try to read it anyway",
                img_bpp)
            # TODO: also handle img_bpp == 1 and 4.

        img_size = int(img_info[0]) * int(img_info[1]) * img_bpp  # num of bytes we need to receive (uint16)

        img = b""
        try:
            while len(img) < img_size:  # wait until all bytes are received
                img += self.parent._dataport.recv(img_size)
        except socket.timeout as msg:
            raise TimeoutError(f"Did not receive an image: {msg}")

        image = numpy.frombuffer(img, dtype=numpy.uint16)  # convert to array
        image.shape = (int(img_info[1]), int(img_info[0]))  # Y, X
        logging.debug("Received image of shape %s.", image.shape)

        # Get the scaling table to correct the time axis
        if self.parent._streakunit.streakMode.value:
            # There should be no sync problem, as we only receive images and scaling table via the dataport
            try:
                # TODO only request scaling table if corresponding MD not available for this time range
                self._metadata[model.MD_TIME_LIST] = self._get_time_scale()
            except Exception:
                logging.exception("Failed to get scaling table")
        else:
            # remove MD_TIME_LIST if not applicable
            self._metadata.pop(model.MD_TIME_LIST, None)

        md = dict(self._metadata)  # make a copy of md dict so cannot be accidentally changed
        if photon_counting:
            # Save the extra metadata for photon-counting mode.
            # Don't trust .pcIntegrationCounts & .exposureTime too much, as the user might have changed in HPDTA
            try:
                exposure_count = self._get_exposure_count()
                exp_time_raw = self.parent.CamParamGet("PC", "Exposure")[0].split(' ')
                exp_time_pc = self.parent.convertUnit2Time(exp_time_raw[0], exp_time_raw[1])
                md[model.MD_EXP_TIME] = exp_time_pc * exposure_count  # Total exposure time
                md[model.MD_INTEGRATION_COUNT] = exposure_count
            except Exception:
                logging.exception("Failed to get photon-counting metadata.")
        else:  # Standard acquisition => use the "Live" settings
            exp_time_raw = self.parent.CamParamGet("Live", "Exposure")[0].split(' ')
            exp_time = self.parent.convertUnit2Time(exp_time_raw[0], exp_time_raw[1])
            md[model.MD_EXP_TIME] = exp_time

        self._mergeMetadata(md)  # merge dict with metadata from other HW devices (streakunit and delaybox)
        if acq_start_t is not None:
            md[model.MD_ACQ_DATE] = acq_start_t
        else:
            md[model.MD_ACQ_DATE] = reception_time_image - md[model.MD_EXP_TIME] + md[model.MD_READOUT_TIME]

        return model.DataArray(self._transposeDAToUser(image), md)


class StreakUnit(model.HwComponent):
    """
    Represents the Hamamatsu streak unit.
    """

    def __init__(self, name, role, parent, daemon=None, time_ranges: Optional[Dict[int, float]] = None, **kwargs):
        """
        :param time_ranges: actual value for the time ranges, if they are just arbitrary integers (ex, with synchroscan)
        """

        super().__init__(name, role, parent=parent, daemon=daemon, **kwargs)  # init HwComponent

        self.parent = parent
        self.location = "Streakcamera"  # don't change, internally needed by HPDTA/RemoteEx

        self._hwVersion = parent.DevParamGet(self.location, "DeviceName")[0]  # needs to be a string
        self._metadata[model.MD_HW_VERSION] = self._hwVersion
        avail_params = parent.DevParamsList(self.location)

        self._time_ranges = time_ranges or {}
        if not isinstance(self._time_ranges, dict):
            raise TypeError(f"time_ranges must be a dictionary int -> float (range ID -> time), but got {type(self._time_ranges)}")
        for k, t in self._time_ranges.items():
            if not isinstance(k, int) or not isinstance(t, numbers.Real):
                raise TypeError(f"time_ranges must be a dictionary int -> float (range ID -> time), but got {k} -> {t}")

        # Set default "good" parameters, which are not controlled/changed afterward.
        # There are several types of streak unit (eg, single sweep, synchroscan).
        # Synchroscan:  DevParamsList 'Time Range', 'Mode', 'Gate Mode', 'MCP Gain', 'Shutter', 'FocusTimeOver', 'Delay'
        # Single Sweep: DevParamsList 'Time Range', 'Mode', 'Gate Mode', 'MCP Gain', 'Shutter', 'Trig. Mode', 'Trigger status', 'Trig. level', 'Trig. slope', 'FocusTimeOver'
        # In order to support all of them we need to check the available parameters.
        parent.DevParamSet(self.location, "MCP Gain", 0)
        # Switch Mode to "Focus", MCPGain = 0 (implemented in RemoteEx and also here in the driver).
        parent.DevParamSet(self.location, "Mode", "Focus")
        # Resets behavior for a vertical single shot sweep: Automatic reset occurs after each sweep.
        if "Trig. Mode" in avail_params:
            parent.DevParamSet(self.location, "Trig. Mode", "Cont")
        # [Volt] Input and indication of the trigger level for the vertical sweep.
        if "Trig. level" in avail_params:
            parent.DevParamSet(self.location, "Trig. level", 1)
        if "Trig. slope" in avail_params:
            parent.DevParamSet(self.location, "Trig. slope", "Rising")

        # only add the shutter parameter if it is possible to control the shutter via software
        if "Shutter" in avail_params:
            parent.DevParamSet(self.location, "Shutter", "Closed")
            shutter = self.GetShutter()
            self.shutter = model.BooleanVA(shutter, setter=self._setShutter)

        # parent.DevParamGet(self.location, "Trig. status")  # read only

        # Ready: Is displayed when the system is ready to receive a trigger signal.
        # Fired: Is displayed when the system has received a trigger signal but the sweep has not
        # been completed or no reset signal has been applied until now. The system will ignore trigger signals
        # during this state.
        # Do Reset: Do Reset can be selected when the system is in trigger mode Fired. After selecting Do
        # Reset the trigger status changes to Ready.

        # VAs
        mode = self.GetStreakMode()
        self.streakMode = model.BooleanVA(mode, setter=self._setStreakMode)  # default False see set params above

        gain = self.GetMCPGain()
        range_gain = self.GetMCPGainRange()
        self.MCPGain = model.IntContinuous(gain, range_gain, setter=self._setMCPGain)
        # Note: MCPGain auto set to 0 is handled by stream not by driver except when changing from
        # "Operate" mode to "Focus" mode

        time_range = self.GetTimeRange()
        choices = set(self.GetTimeRangeChoices())
        time_range = util.find_closest(time_range, choices)  # make sure value is in choices
        self.timeRange = model.FloatEnumerated(time_range, choices, setter=self._setTimeRange, unit="s")

        self.MCPGain.subscribe(self._onMCPGain, init=True)
        self.timeRange.subscribe(self._onTimeRange, init=True)
        self.streakMode.subscribe(self._onStreakMode, init=True)

        # TODO: Add some read-only VAs for Trig. Mode, Trig. level, Trig. slope?

        # Refresh regularly the values, from the hardware, starting from now
        self._updateSettings()
        self._va_poll = util.RepeatingTimer(5, self._updateSettings, "Streak unit settings polling")
        self._va_poll.start()

    def terminate(self):
        if self._va_poll.is_alive():
            self._va_poll.cancel()
            self._va_poll.join(1)

        # Put device into the safest mode possible
        self.parent.DevParamSet(self.location, "MCP Gain", 0)
        self.parent.DevParamSet(self.location, "Mode", "Focus")  # streakMode = False
        if hasattr(self, "shutter"):
            self.parent.DevParamSet(self.location, "Shutter", "Closed")

        super().terminate()

    def _onMCPGain(self, value: int) -> None:
        """
        Called when the MCP Gain VA changes, to update the metaadata
        :param value: value to be set
        """
        logging.debug("Reporting MCP gain %s for streak unit.", value)
        self._metadata[model.MD_STREAK_MCPGAIN] = value

    def _onTimeRange(self, value: float) -> None:
        """
        Called when the Time Range VA changes, to update the metaadata
        :param value: value to be set
        """
        logging.debug("Reporting time range %s for streak unit.", value)
        self._metadata[model.MD_STREAK_TIMERANGE] = value

    def _onStreakMode(self, value: bool) -> None:
        """
        Called when the Streak Mode VA changes, to update the metaadata
        :param value: value to be set
        """
        logging.debug("Reporting streak mode %s for streak unit.", value)
        self._metadata[model.MD_STREAK_MODE] = value

    def _updateSettings(self) -> None:
        """
        Read all the current streak unit settings from the RemoteEx and reflect them on the VAs
        """
        logging.debug("Updating streak unit settings")
        try:
            timeRange = self.GetTimeRange()
            if timeRange != self.timeRange.value:
                self.timeRange._value = timeRange
                self.timeRange.notify(timeRange)

            gain = self.GetMCPGain()
            if gain != self.MCPGain.value:
                self.MCPGain._value = gain
                self.MCPGain.notify(gain)

            mode = self.GetStreakMode()
            if mode != self.streakMode.value:
                self.streakMode._value = mode
                self.streakMode.notify(mode)

            # update only if the VA exists
            if hasattr(self, "shutter"):
                shutter = self.GetShutter()
                if shutter != self.shutter.value:
                    self.shutter._value = shutter
                    self.shutter.notify(shutter)

        except Exception:
            logging.exception("Unexpected failure when polling streak unit settings")

    def GetShutter(self) -> bool:
        """
        Get the current state from the shutter.
        :return: True if the shutter is active (closed), False if the shutter is inactive (open)
        """
        shutter_raw = self.parent.DevParamGet(self.location, "Shutter")  # returns a list
        logging.debug("Shutter state is %s.", shutter_raw)

        if shutter_raw[0] == "Closed":
            shutter = True
        elif shutter_raw[0] == "Open":
            shutter = False
        else:
            logging.warning("Unexpected shutter mode %s. Assuming it's open.", shutter_raw[0])
            shutter = False

        return shutter

    def _setShutter(self, value: bool) -> bool:
        """
        Updates the shutter state VA.
        :param value: True if the shutter is active (closed), False if the shutter is inactive (open)
        :return: shutter state
        """
        pos = "Closed" if value else "Open"
        self.parent.DevParamSet(self.location, "Shutter", pos)
        logging.debug("Setting shutter to: %s = %s.", pos, value)

        time.sleep(0.15)  # make sure the shutter movement is done

        return value

    def GetStreakMode(self):
        """
        Get the current value from the streak unit HW.
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
        :param value: (bool) value to be set
        :return: (bool) current streak mode
        """
        if not value:
            # For safety, always set the get to the minimum when not sweeping
            self.MCPGain.value = 0
            self.parent.DevParamSet(self.location, "Mode", "Focus")
        else:
            self.parent.DevParamSet(self.location, "Mode", "Operate")

        return value

    def GetMCPGain(self) -> int:
        """
        Get the current value from the streak unit HW.
        :return: current MCPGain value
        """
        MCPGain_raw = self.parent.DevParamGet(self.location, "MCP Gain")  # returns a list
        MCPGain = int(MCPGain_raw[0])

        return MCPGain

    def _setMCPGain(self, value: int) -> int:
        """
        Updates the MCPGain VA.
        :param value: value to be set
        :return: current MCPGain
        """
        self.parent.DevParamSet(self.location, "MCP Gain", value)

        return value

    def GetMCPGainRange(self) -> Tuple[int, int]:
        """
        Get range for streak unit MCP gain.
        :return: min/max of MCP gain values
        """
        # First 5 values see CamParamInfoEx.
        MCPGainRange_raw = self.parent.DevParamInfoEx(self.location, "MCP Gain")[5:]
        MCPGainRange = (int(MCPGainRange_raw[0]), int(MCPGainRange_raw[1]))

        return MCPGainRange

    def _setTimeRange(self, value: float) -> float:
        """
        Updates the timeRange VA.
        :param value: value to be set (s)
        :return: actual time range (s)
        """
        self.SetTimeRange(self.location, value)
        return value

    def SetTimeRange(self, location, time_range):
        """
        Sets the time range for the streak unit.
        Converts the value in sec into a for RemoteEx readable format.
        :param location: (str) see DevParamGet
        :param time_range: (float) time range for one sweep in sec
        """
        try:
            if self._time_ranges:
                # Convert from time to time_id
                time_range_raw = self._time_to_time_id(time_range)
            else:
                # Convert from time to string (e.g. "1.0 ns")
                time_range_raw = self.parent.convertTime2Unit(time_range)
        except Exception as ex:
            raise ValueError("Time range of %s sec is not supported (%s)." % (time_range, ex))

        self.parent.DevParamSet(location, "Time Range", time_range_raw)

    def GetTimeRangeChoices(self):
        """
        Get choices for streak unit time range. Values are in order.
        First six values see CamParamInfoEx.
        :return: (set of floats) possible choices for time range
        """
        choices_raw = self.parent.DevParamInfoEx(self.location, "Time Range")[6:]
        choices = []
        for choice in choices_raw:
            choice_raw = choice.split(" ")
            if len(choice_raw) == 1:  # No unit -> typically it's just a number (from 1 to 5)
                time_id = int(choice_raw[0])
                t = self._time_id_to_time(time_id)
            else:
                t = self.parent.convertUnit2Time(choice_raw[0], choice_raw[1])
            choices.append(t)

        return choices

    def GetTimeRange(self) -> float:
        """
        Gets the time range of the streak unit.
        Converts the provided value received from RemoteEx into sec.
        :return: (float) current time range for one sweep in sec
        """
        time_range_raw = self.parent.DevParamGet(self.location, "Time Range")[0].split(" ")
        if len(time_range_raw) == 1:  # No unit -> typically it's just a number (from 1 to 5)
            time_id = int(time_range_raw[0])
            time_range = self._time_id_to_time(time_id)
        else:
            time_range = self.parent.convertUnit2Time(time_range_raw[0], time_range_raw[1])

        return time_range

    def get_time_scale_factor(self) -> float:
        """
        Guess the factor used in the time scale metadata, to convert the values to seconds.
        Typically, the time range is in the order of ns, us, ms. This depends on the time range.
        :return:
        """
        # When the synchroscan sweep unit is used, the time range is just an integer, and the time
        # scale seems to always be in ps.
        if self._time_ranges:
            return 1e-12

        # The values are expressed with the same prefix as the time range
        tr = self.timeRange.value
        if 1e-15 <= tr < 1:
            return 10 ** ((math.log10(abs(tr)) // 3) * 3)
        else:
            # Let's not completely fail, and instead assume it's an index number (int), so in ps.
            logging.warning("Unexpected time range of %s s, will guess time scale is in ps", tr)
            return 1e-12

    def _time_id_to_time(self, time_id: int) -> float:
        """
        Convert from an arbitrary time range ID to time (in s), based on the "time_ranges" specified
        by the user.
        :param time_id: the time range ID, as used by the streak unit
        :return: the time in s
        :raises: KeyError if time_id is not in the known time ranges
        """
        try:
            return self._time_ranges[time_id]
        except KeyError:
            raise KeyError(f"Time ID {time_id} is not in time_ranges")

    def _time_to_time_id(self, time_phy: float) -> int:
        """
        Convert from time (in s) to an arbitrary time range ID.
        :param time_phy: time in s
        :return: the time ID as used by the streak unit
        :raises: KeyError if time_phy is not in the known time ranges
        """
        for tid, t in self._time_ranges.items():
            if util.almost_equal(t, time_phy):
                return tid
        raise KeyError(f"Time {time_phy} s is not in time_ranges")


class DelayGenerator(model.HwComponent):
    """
    Represents the delay generator.
    """

    def __init__(self, name, role, parent, daemon=None, streak_unit=None, **kwargs):
        """
        :param streak_unit: a streak unit Component, which has a timeRange VA. When this VA changes,
        the trigger delay VA of the delay generator is updated, based on the MD_TIME_RANGE_TO_DELAY
        metadata.
        """
        super().__init__(name, role, parent=parent, daemon=daemon, **kwargs)

        self._streak_unit = streak_unit
        self.location = "Delaybox"  # don't change, internally needed by HPDTA/RemoteEx

        self._hwVersion = parent.DevParamGet(self.location, "DeviceName")[0]   # needs to be a string
        self._metadata[model.MD_HW_VERSION] = self._hwVersion

        avail_params = parent.DevParamsList(self.location)

        # FIXME: on synchroscan unit, the parameters are quite different.
        # On synchroscan, DevParamsList: 'Delay Time', 'Lock Mode', 'Device Status'
        # Set parameters delay generator
        if "Setting" in avail_params:
            parent.DevParamSet(self.location, "Setting", "M1")  # TODO might be enough and don't need the rest...check!!
        if "Trig. Mode" in avail_params:
            parent.DevParamSet(self.location, "Trig. Mode", "Ext. rising")  # Note: set to "Int." for testing without SEM
        # Note: this is legacy code, to maintain the behavior of the old version
        # A better way would be to use the "properties" in the microscope file, to set the triggerDelay (Delay A)
        # and delayB values just after initialisation.
        if "Delay A" in avail_params:
            parent.DevParamSet(self.location, "Delay A", 0)
        if "Delay B" in avail_params:
            parent.DevParamSet(self.location, "Delay B", 0.00000002)
        if "Burst Mode" in avail_params:
            parent.DevParamSet(self.location, "Burst Mode", "Off")

        # Note: trigger rate (repetition rate) corresponds to the ebeam blanking frequency (read only in RemoteEx)
        if "Repetition Rate" in avail_params:
            self.triggerRate = model.FloatVA(0, unit="Hz", readonly=True, getter=self.GetTriggerRate)

        # VAs
        self._delay_setters: Dict[str, Callable] = {}  # name of the RemoteEx param -> setter function

        for rx_param, va_name in DELAY_NAMES.items():
            # do not assign VA if RemoteEx param does not exist
            if rx_param not in avail_params:
                continue

            delay = self.GetDelayByName(rx_param)
            range_delay = self.GetDelayRangeByName(rx_param)

            # Create a VA, with a corresponding setter function
            delay_setter = functools.partial(self._setDelayByName, rx_param)
            self._delay_setters[rx_param] = delay_setter  # keep a ref to the partial so that it's not garbage collected
            if rx_param == "Delay Time":  # C12270
                # On this delay generator, the unit is just arbitrary, representing the number of internal
                # ticks relative to the frequency of the signal. IOW, it's not possible to map it to
                # a time unit reliably. Moreover, it's an int, so the range is much larger than other VAs.
                unit = None
            else:
                unit = "s"
                # Clip the maximum range to 10s, to avoid an unhelpfully large range. Typically, values are < 0.1s.
                range_delay = (range_delay[0], min(range_delay[1], 10))

            if not range_delay[0] <= delay <= range_delay[1]:
                logging.info("Delay %s is not in range %s, clipping it", delay, range_delay)
                delay = min(max(range_delay[0], delay), range_delay[1])
            delay_va = model.FloatContinuous(delay, range_delay, setter=delay_setter, unit=unit)
            setattr(self, va_name, delay_va)

        # With the Synchroscan (C12270), the delay generator has a "Lock Mode" parameter.
        # It could be controlled as a VA phaseLock. The "Device Status" parameter indicates whether
        # the phase lock (aka PLL) works or not. If not, the .state VA could be changed accordingly,
        # and phaseLock reset to False.
        # According to the manual, the values can be "UNLOCKED" -> SCANNING -> LOCKED
        # Device status can be "Lock Error" (while the lock mode is "Locked")
        if "Lock Mode" in avail_params:
            locked = self.GetLockMode()
            self.phaseLock = model.BooleanVA(locked, setter=self._set_phase_lock)

        # Refresh regularly the values, from the hardware, starting from now
        self._updateSettings()
        self._va_poll = util.RepeatingTimer(5, self._updateSettings, "Delay generator settings polling")
        self._va_poll.start()

        if streak_unit:
            streak_unit.timeRange.subscribe(self._on_time_range)

    def terminate(self):
        if self._va_poll.is_alive():
            self._va_poll.cancel()
            self._va_poll.join(1)
        if self._streak_unit:
            self._streak_unit.timeRange.unsubscribe(self._on_time_range)
            self._streak_unit = None
        super().terminate()

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

        is_first_time = model.MD_TIME_RANGE_TO_DELAY not in self._metadata
        super().updateMetadata(md)

        if is_first_time and model.MD_TIME_RANGE_TO_DELAY in md and self._streak_unit:
            self._on_time_range(self._streak_unit.timeRange.value)

    def GetTriggerRate(self) -> float:
        """
        Get the trigger rate (repetition) rate from the delay generator.
        The Trigger rate corresponds to the ebeam blanking frequency. As the delay
        generator is operated "external", the trigger rate is a read-only value.
        :return: (float) current trigger rate (Hz)
        """
        triggerRate_raw = self.parent.DevParamGet(self.location, "Repetition Rate")  # returns a list
        return float(triggerRate_raw[0])

    def GetLockMode(self) -> bool:
        """
        Get the (phase) lock mode status
        :return: the current mode
        """
        mode_raw = self.parent.DevParamGet(self.location, "Lock Mode")  # returns a list
        mode_str = mode_raw[0].lower()
        # From tests, it seems it can only be "Locked" or "Unlocked", and what actually happens is
        # reported in the "Device Status".
        if mode_str == "locked":
            return True
        elif mode_str == "unlocked":
            return False
        else:
            # TODO: need to return a different value, and the caller would do something more sensible
            # with it. It can typically indicate that the locking failed (because of the signal not
            # containing the expected frequency). One option would be then to change the .state to error.
            # and report unlocked.
            logging.warning("Unknown delay generator lock mode: %s", mode_raw[0])
            return False

    def SetLockMode(self, locked: bool) -> None:
        """
        Changes the (phase) lock mode status.
        Note: blocking. When setting Locked, it can take a while (> 10 s).
        """
        mode = "Locked" if locked else "Unlocked"
        # DevParamSet doesn't have a timeout parameter, so directly use the low-level function.
        # self.parent.DevParamSet(self.location, "Lock Mode", mode)
        self.parent.sendCommand("DevParamSet", self.location, "Lock Mode", mode, timeout=30)

    def _set_phase_lock(self, lock: bool) -> bool:
        self.SetLockMode(lock)  # Blocking, can take long
        return self.GetLockMode()

    def GetDelayByName(self, delay_name: str):
        """
        Get the current value from the trigger delay HW (RemoteEx: delay D).
        :param delay_name: name of the delay e.g. (Delay H)
        :return: (float) current trigger delay value
        """
        delay_raw = self.parent.DevParamGet(self.location, delay_name)  # returns a list
        delay = float(delay_raw[0])
        return delay

    def _setDelayByName(self, delay_name: str, value: float):
        """
        Updates the trigger delay VA.
        :param delay_name: name of the delay e.g. (Delay A)
        :param value: value to be set
        :return: (float) current trigger delay value
        """
        self.parent.DevParamSet(self.location, delay_name, value)
        return self.GetDelayByName(delay_name)

    def GetDelayRangeByName(self, delay_name: str) -> Tuple[float, float]:
        """
        Get the range allowed for a delay. RemoteEx provides a negative minimum,
        which is internally set to zero whenever a negative delay is requested.
        :param delay_name: name of the delay. Ex: "Delay A".
        :return: the trigger delay min/max (s)
        """
        min_time = 0
        max_time = float(self.parent.DevParamInfoEx(self.location, delay_name)[-1])
        range_time = (min_time, max_time)

        return range_time

    def _updateSettings(self) -> None:
        """
        Read all the current delay generator settings from the RemoteEx and reflect them on the VAs
        """
        logging.debug("Updating streak unit settings")
        try:
            if hasattr(self, "triggerRate"):
                # As there is a getter, just reading the VA updates ._value, so need to use the
                # direct function to check the value has changed, and notify its change.
                trigger_rate = self.GetTriggerRate()
                if trigger_rate != self.triggerRate._value:
                    self.triggerRate._set_value(trigger_rate, force_write=True)

            for rxname in self._delay_setters:
                vaname = DELAY_NAMES[rxname]
                va = getattr(self, vaname)
                rx_val = self.GetDelayByName(rxname)
                if va._value != rx_val:
                    va._value = rx_val
                    va.notify(rx_val)

            if hasattr(self, "phaseLock"):
                lock_mode = self.GetLockMode()
                if lock_mode != self.phaseLock._value:
                    self.phaseLock._value = lock_mode
                    self.phaseLock.notify(lock_mode)

        except Exception:
            logging.exception("Unexpected failure when polling delay generator settings")

    def _on_time_range(self, time_range: float):
        # set corresponding trigger delay
        tr2d = self._metadata.get(model.MD_TIME_RANGE_TO_DELAY)
        if tr2d:
            key = util.find_closest(time_range, tr2d.keys())
            if util.almost_equal(key, time_range):
                self.triggerDelay.value = tr2d[key]
            else:
                logging.warning("Time range %s is not a key in MD for time range to "
                                "trigger delay calibration" % time_range)

# Just keep enough log messages to be able to detect the previous command error or warning
LOG_QUEUE_MAX_SIZE = 16  # max number of log messages to keep in the queue

class StreakCamera(model.HwComponent):
    """
    Represents Hamamatsu readout camera for the streak unit.
    Client to connect to HPD-TA software via RemoteEx.
    Note: the RemoteEx software needs to be running on the streak camera computer. Typically, this
    can be done by placing it in the autostart folder.
    """

    def __init__(self, name, role, port: int, host: str, settings_ini: Optional[str] = None,
                 children=None, dependencies=None, daemon=None, **kwargs):
        """
        Initializes the device.
        :param port: port for sending/receiving commands
        :param host: IP-adress or hostname
        :param settings_ini: path to the INI file for HPDTA, which defines which hardware is initialized
        If None, the default INI file is used.
        :param children: should contain a "streakunit", a "readoutcam" (optional), and a "delaybox" (optional)
        :param dependencies:
        * "spectrograph" (optional): for the readout camera, to obtain the wavelength metadata
        """
        super().__init__(name, role, dependencies=dependencies, daemon=daemon, **kwargs)

        port_d = port + 1  # the port number to receive the image data
        self.port = port
        self.port_d = port_d

        self._lock_command = threading.Lock()

        # When host is "fake-singlesweep" or "fake-synchroscan", start a local simulator
        # and connect to it instead of a real HPDTA machine.
        if host.startswith("fake-"):
            streak_unit_type = host[len("fake-"):]
            logging.info("Starting HPDTASim (streak_unit=%s) on port %d", streak_unit_type, port)
            self._simulator = HPDTASim(streak_unit=streak_unit_type, port=port)
            self.host = "localhost"
        else:
            self._simulator = None
            self.host = host

        # connect to readout camera
        try:
            # initialize connection with RemoteEx client
            self._commandport, self._dataport = self._openConnection()
        except Exception:
            logging.exception("Failed to initialise Hamamatsu readout camera.")
            if self._simulator:
                self._simulator.terminate()
            raise

        # collect responses (error_code = 0-3,6-10) from commandport
        self.queue_command_responses = queue.Queue()  # List[str]
        # Latest log (error code 4) and warning (error code 5) messages
        self.queue_log = collections.deque(maxlen=LOG_QUEUE_MAX_SIZE) # List[str]
        self.queue_warning = queue.Queue()  # str
        # Communication with the acquisition thread:
        self.queue_img = queue.Queue()  # Tuple[CMD_*, ...] where extra elements are command arguments

        # For notifications
        gui_users = driver.get_active_gui_users()
        self._gui_user = next(iter(gui_users), None)  # just take the first user, if any
        if self._gui_user:
            self._notification_thread = threading.Thread(target=self._notification_loop, daemon=True)
            self._notification_thread.start()

        self.should_listen = True  # used in readCommandResponse thread

        # start thread, which keeps reading the commandport response continuously
        self._start_receiverThread()

        # Note: start HPDTA after initializing queue and command and receiver threads
        # but before image thread and initializing children.
        # Note: if already running, it will return ["parameters ignored"] and continue
        # TODO: add an option to allow showing the dialogs? the drawback is that all dialogs are
        # shown and all operations are blocked, including closing the app.
        self.AppStart(visible=True, ini_file=settings_ini, no_dialog=True)  # Note: comment out for testing in order to not start a new App

        try:
            # Detect when a device is not turned on, or the wrong sweep unit is selected.
            # Typically, that leads to an error such as:
            # "4,HExternalDevices: Communication error. Device: C16910 Parameter: Time Range"
            for msg in self.queue_log:
                if len(msg) > 1 and msg[0] == "4" and "communication error" in msg[1].lower():
                    raise model.HwError(f"{msg[1]}. Check the right hardware is connected.")

            # If the USB dongle is missing, the software will still run, but not actually control the
            # hardware, and mostly everything will fail to run. So it's handy to check.
            license_status = self.AppLicenceGet()
            if "1" not in license_status[:2]:  # Application key found and/or acquire
                logging.warning("HPDTA software didn't find the license. Will not be able to control the streak camera.")
                raise model.HwError("HPDTA software didn't find the license. Check the USB dongle is plugged in.")
            vinfo = self.AppInfo("Version")
            self._swVersion = vinfo[0]

            # don't send warning when closing the app with unsaved images. In remote mode, we never
            # need to save on the images on the streak camera computer.
            self.ImgParamSet("WarnWhenUnsaved", "0")

            # TODO: grap the queue logs in a separate thread?

            children = children or {}
            dependencies = dependencies or {}
            try:
                ckwargs = children["streakunit"]
            except Exception:
                raise ValueError("Required child streakunit not provided")
            self._streakunit = StreakUnit(parent=self, daemon=daemon, **ckwargs)
            self.children.value.add(self._streakunit)  # add streakunit to children-VA

            if "delaybox" in children.keys():
                ckwargs = children["delaybox"]
                self._delaybox = DelayGenerator(parent=self, daemon=daemon, streak_unit=self._streakunit, **ckwargs)
                self.children.value.add(self._delaybox)  # add delaybox to children-VA
            else:
                self._delaybox = None
                logging.info("No delaybox provided.")

            if "readoutcam" in children.keys():
                ckwargs = children["readoutcam"]
                self._readoutcam = ReadoutCamera(parent=self, spectrograph=dependencies.get("spectrograph"),
                                                 daemon=daemon, **ckwargs)
                self.children.value.add(self._readoutcam)  # add readoutcam to children-VA
            else:
                logging.info("No readout camera provided.")

        except Exception:
            self.terminate()
            raise

    def _notification_loop(self) -> None:
        """
        Grabs the warning messages from the queue and sends them as notifications to the GUI.
        Expected to run in a separate thread.
        """
        try:
            while True:
                msg = self.queue_warning.get()
                if msg is None:  # Special "message" to terminate the thread
                    break
                self._show_warning(msg)
        except Exception:
            logging.exception("Error in notification thread")
        finally:
            logging.info("Notification thread terminated.")

    def _show_warning(self, msg: str) -> None:
        """
        Sends a warning message as a notification to the GUI user.
        :param msg: The warning message to be sent.
        """
        if self._gui_user:
            try:
                # RemoteEx sends new lines as \r\n, which is shown oddly in the notifications.
                # => replace with just \n, and use the first line as the title.
                lines = msg.split("\r\n")
                title = lines[0]
                message = "\n".join(lines[1:])
                driver.notify_to_user(self._gui_user, title=title,  message=message,
                                      app=self.name, icon="warning")
            except Exception as ex:
                logging.warning("Error sending notification: %s", ex)

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

        if self._notification_thread:
            self.queue_warning.put(None)  # Special "message" to terminate the thread
            self._notification_thread.join(5)
            self._notification_thread = None

        self.should_listen = False  # terminates receiver thread
        if self.t_receiver.is_alive():
            self.t_receiver.join(5)
        self._closeConnection()

        if self._simulator is not None:
            self._simulator.terminate()
            self._simulator = None

        super().terminate()

    def sendCommand(self, func, *args, **kwargs) -> List[str]:
        """
        Sends a command to RemoteEx.
        :param func: (str) command or function, which should be send to RemoteEx
        :param args: (str) optional parameters allowed for function
        :param kwargs: optional arguments not defined in advance
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
                    raise TimeoutError("No answer received after %s s for command %s."
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

                logging.debug("Command %s got response: %s, %s.", func, error_code, rargs)
                if error_code:  # != 0, response corresponds to command, but an error occurred
                    # FIXME: pass the rargs directly to RemoteExError, which can contain extra info
                    logging.error("Function %s raised: %s, %s", rfunc, RemoteExError(error_code), rargs)
                    raise RemoteExError(error_code)
                else:  # successfully executed command and return message
                    return rargs

    def readCommandResponse(self):
        """
        This method runs in a separate thread and continuously listens for messages returned from
        the device via the commandport IP socket.
        The messages are made available either on .queue_command_responses (for the standard responses)
        or .queue_img (for messages related to the images, as Tuple[CMD_*, ...]).
        """
        try:
            responses = ""  # received data not yet processed

            while self.should_listen:
                try:
                    received = self._commandport.recv(4096)  # buffersize should be small value of power 2 (4096)
                except socket.timeout:
                    # no data received yet, nothing to worry, just continue
                    # logging.debug("Timeout on the socket, will wait for more data packets.")
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
                responses += received.decode("latin1")

                # Sometimes the answer comes in multiple parts, separated by \r\n, but cut on \r.
                # so need to wait a tiny bit longer (<3ms) to check if the response is continuing.
                if len(responses) > 100 or responses[-1:] != "\r":  # if the response look like it could be cut, wait for more
                    for i in range(100):
                        time.sleep(3e-3)
                        readable, _, _ = select.select([self._commandport], [], [], 0)  # wait max 0s.
                        if not readable:  # No more data available => good to process it
                            break
                        try:
                            received = self._commandport.recv(4096)
                            logging.debug("Received extra: '%s'", to_str_escape(received))
                            responses += received.decode("latin1")
                        except Exception as ex:  # No extra data, that's unexpected, but fine!
                            logging.debug("No more data available while so was supposed to be there: %s", ex)
                            break
                    else:
                        logging.warning("Responses keep coming in, will process the data received so far.")

                # Separate commands on \r... but not \r\n (which is used to separate lines inside a response)
                # Note: in reality, it's even more muddy, because some error messages may contain raw
                # data which can have \r inside without any escaping. We could try to detect that the
                # \r is not followed by a number... but for now we don't care about such error messages,
                # so just let them be handled later by detecting they are not a normal response.
                resp_splitted = re.split(r"\r(?!\n)", responses)
                # split responses, overwrite var responses with the remaining messages (usually empty)
                resp_splitted, responses = resp_splitted[:-1], resp_splitted[-1]

                for msg in resp_splitted:
                    msg_splitted = msg.split(",")

                    try:
                        error_code, rfunc, rargs = int(msg_splitted[0]), msg_splitted[1], msg_splitted[2:]
                    except (TypeError, ValueError, IOError) as ex:
                        logging.warning("Skipping unexpected response (%s): %s", ex, to_str_escape(msg))
                        continue

                    # logging.debug("Interpreted response: %s", msg_splitted)

                    if error_code in (4, 5):
                        # A new image is available on the dataport => Send to the special queue
                        if error_code == 4 and rfunc in ("Livemonitor", "Acqmonitor"):
                            self.queue_img.put((CMD_IMG, *rargs))
                        elif error_code == 4:
                            self.queue_log.append(msg_splitted)
                        else:  # error_code == 5
                            # Discard old wernings if too many
                            while self.queue_warning.qsize() >= LOG_QUEUE_MAX_SIZE:
                                self.queue_warning.get()
                            self.queue_warning.put(msg_splitted[1])
                    else:  # send response including error_code to queue
                        self.queue_command_responses.put(msg_splitted)

        except Exception:
            logging.exception("Hamamatsu streak camera TCP/IP receiver thread failed.")
        finally:
            logging.info("Hamamatsu streak camera TCP/IP receiver thread ended.")

    def StartAcquisition(self, AcqMode):
        """
        Start an acquisition.
        :param AcqMode: (str) see AcqStart
        """
        # Note: sync acquisition calls directly AcqStart
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
                time.sleep(1e-3)
                if time.time() > start + timeout:
                    logging.error("Could not start acquisition, HPDTA still processing command.")
                    return
            self.AcqStart(AcqMode)

    # === General commands ============================================================

    def Appinfo(self):
        """Returns the current application type. Can be executed even if application (HPDTA or HiPic)
        have not been started yet.
        Not to be confused with "AppInfo"! (upper case "I")
        """
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

    def AppStart(self, visible: bool = True, ini_file: str = None, no_dialog: bool = True):
        """
        Start RemoteEx. If the application is already running, it will not do anything.
        Blocks until the application is started.
        :param ini_file: (str) path to the INI file for HPDTA, default is HDPTA8.INI
        :param no_dialog: if False, the HPDTA application will communicate with the user by normal message
        boxes. Otherwise, all messages that are normally shown are only sent to the RemoteEx client
        with error code 5.
        """
        # The function accepts up to 4 arguments: fVisible, sINIFile, fNoDialogs, iEncoding
        # fVisible: 0 = invisible, 1 = visible (default)
        # sINIFile: ini file (Default is HDPTA8.INI). Warning INI != HWP (although the
        # HWP file also follows the INI syntax, so it will not complain!). However, the INI file
        # points towards the hardware profile (HWprofile) file.
        # fNoDialogs: False = show dialogs, True = no dialogs (default)
        logging.debug("Starting RemoteEx App.")
        if ini_file is None:
            ini_file = ""
        else:
            logging.debug("Starting with ini file: %s", ini_file)

        self.sendCommand("AppStart",
                         "1" if visible else "0",
                         ini_file,
                         "1" if no_dialog else "0",
                         timeout=30)


    def AppEnd(self):
        """Close RemoteEx."""
        logging.debug("Closing RemoteEx App.")
        self.sendCommand("AppEnd")

    def AppInfo(self, parameter):
        """Returns information about the application.
        Not to be confused with "Appinfo"! (lower case "i")
        :param parameter: (str) Date, Version, Directory, Title, Titlelong, ProgDataDir.
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
        :param parameter: (str) ImageSize, Message, Temperature, GateMode, MCPGain, Mode, Plugin, Shutter, StreakCamera, TimeRange.
        :returns: Current value of parameter."""
        return self.sendCommand("MainParamGet", parameter)

    def MainParamInfo(self, parameter):
        """Returns information about parameters visible in the main window.
        :param parameter: (str) ImageSize, Message, Temperature, GateMode, MCPGain, Mode, Plugin, Shutter,
                                    StreakCamera,TimeRange
        :returns: Label, Current value, Param type (PARAM_TYPE_DISPLAY)
        """
        return self.sendCommand("MainParamInfo", parameter)

    def MainParamInfoEx(self, parameter):
        """Returns information about parameters visible in the main window. Returns more detailed information in
        case of a PARAM_TYPE_LIST than MainParamInfo.
        :param parameter: (str) see _mainParamInfo
        :returns: Label, Current value, Param type (PARAM_TYPE_DISPLAY)"""
        return self.sendCommand("MainParamInfoEx", parameter)

    def _send_params_list_cmd(self, cmd: str, *args: str) -> List[str]:
        """Send a *ParamsList command and parse the count-prefixed response.

        :param cmd: the RemoteEx command name (e.g. "MainParamsList")
        :param args: optional positional arguments forwarded to sendCommand
        :return: list of parameter name strings reported by the command
        """
        result = self.sendCommand(cmd, *args)
        if not result:
            logging.warning("%s returned empty result.", cmd)
            return []
        try:
            expected_count = int(result[0])
        except (ValueError, IndexError):
            logging.warning("%s returned unexpected format: %s", cmd, result)
            return result
        params = result[1:]
        if len(params) != expected_count:
            logging.warning("%s(%s) reported %d parameters but received %d",
                                cmd, ", ".join(args), expected_count, len(params))
        return params

    def MainParamsList(self) -> List[str]:
        """Returns a list of all parameters related to main window.
        This command can be used to build up a complete parameter list related to main window at runtime.
        :return: list of main window parameter names
        """
        return self._send_params_list_cmd("MainParamsList")

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
        :param iSwitch: (int) 0 to switch sync off, 1 to switch sync on."""
        self.sendCommand("MainSyncSet", iSwitch)

    def GenParamGet(self, parameter):
        """Returns the values of parameters in the general options.
        :param parameter: (str) RestoreWindowPos: Restore window positions
                    UserFunctions: Call user functions
                    ShowStreakControl: Shows or hides the Streak status/control dialog
                    ShowDelay1Control: Shows or hides the Delay1 status/control dialog
                    ShowDelay2Control: Shows or hides the Delay2 status/control dialog
                    ShowSpectrControl: Shows or hides the Spectrograph status/control dialog"""
        self.sendCommand("GenParamGet", parameter)

    def GenParamSet(self, parameter, value):
        """Returns the setting of the sync parameter which is available on the HPD-TA main window.
        :param parameter: (str) RestoreWindowPos: Restore window positions
                    UserFunctions: Call user functions
                    ShowStreakControl: Shows or hides the Streak status/control dialog
                    ShowDelay1Control: Shows or hides the Delay1 status/control dialog
                    ShowDelay2Control: Shows or hides the Delay2 status/control dialog
                    ShowSpectrControl: Shows or hides the Spectrograph status/control dialog
        :param value: (str) PARAM_TYPE_BOOL."""
        value = str(value)
        self.sendCommand("GenParamSet", parameter, value)

    def GenParamInfo(self, parameter):
        """Returns information about the specified parameter.
        :param parameter: (str) RestoreWindowPos: Restore window positions
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
        :param parameter: (str) see GenParamInfo
        :returns: Label, Current value (bool), Param Type (PARAM_TYPE_BOOL)"""
        try:
            label, val, typ = self.sendCommand("GenParamInfoEx", parameter)
            param_typ = int(typ)
            value = bool(val)
        except (IndexError, TypeError, ValueError) as ex:
            raise IOError("Failed to decode response from GenParamInfo: %s" % ex)
        return label, value, param_typ

    def GenParamsList(self) -> List[str]:
        """Returns a list of all parameters related to the general options.
        :return: list of general option parameter names
        """
        return self._send_params_list_cmd("GenParamsList")

    # === Acquisition commands ========================================================

    def AcqStart(self, AcqMode):
        """Start an acquisition.
        :param AcqMode: (str) Live: Live mode
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
        :param timeout: (0.001<= float <=60) The timeout value (in s)
        until this command should wait for an acquisition to end.
        :return: (float) timeout (in s)"""
        # Note: RemoteEx needs timeout in ms
        self.sendCommand("AcqStop", str(timeout * 1000))  # returns empty list
        return timeout

    def AcqParamGet(self, parameter):
        """Returns the values of the acquisition options.
        :param parameter: (str)
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
        :param parameter: (str) see AcqParamGet
        :param value: (str) value to set for parameter"""
        self.sendCommand("AcqParamSet", parameter, value)

    def AcqParamInfo(self, parameter):
        """Returns information about the specified parameter.
        :param parameter: (str) see AcqParamGet
        :return: Label, current value, param type, min (num type only), max (num type only)
            param type: PARAM_TYPE_BOOL, PARAM_TYPE_NUMERIC, PARAM_TYPE_LIST,
                PARAM_TYPE_STRING, PARAM_TYPE_EXPTIME, PARAM_TYPE_DISPLAY
            """
        return self.sendCommand("AcqParamInfo", parameter)

    def AcqParamInfoEx(self, parameter: str) -> List[str]:
        """Returns information about the specified parameter. Returns more detailed information in case of a list
        parameter (Parameter type = 2) than AcqParamInfo. In case of a numeric parameter (Parameter
        type = 1) it additionally returns the step width
        :param parameter: (str) see AcqParamGet
        :return: Label, current value, param type, min (num type only), max (num type only)
            param type: PARAM_TYPE_BOOL, PARAM_TYPE_NUMERIC, PARAM_TYPE_LIST,
                PARAM_TYPE_STRING, PARAM_TYPE_EXPTIME, PARAM_TYPE_DISPLAY
        Note: In case of a list or an exposure time the number of entries and all list entries are returned in
        the response of the AcqParamInfoEx command. In case of a numeric parameter (Parameter type =
        1) it additionally returns the step width
        """
        return self.sendCommand("AcqParamInfoEx", parameter)

    def AcqParamsList(self) -> List[str]:
        """Returns a list of all parameters related to acquisition. This command can be used to build up
        a complete parameter list related to acquisition at runtime.
        :return: list of acquisition parameter names
        """
        return self._send_params_list_cmd("AcqParamsList")

    def AcqLiveMonitor(self, monitorType, nbBuffers=None, *args):
        """Starts a mode which returns information on every new image acquired in live mode.
        Once this command is activated, for every new live image a message is returned.
        :param monitorType: (str)
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
        :param args: (str)
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
        if nbBuffers is not None and monitorType == "RingBuffer":
            args = (str(nbBuffers),)
        return self.sendCommand("AcqLiveMonitor", monitorType, *args)

    def AcqLiveMonitorTSInfo(self):
        """Correlates the current time with the timestamp. It outputs the current time and the time stamp.
        With this information the real time for any other time stamp can be calculated.
        :return: current time, timestamp"""
        return self.sendCommand("AcqLiveMonitorTSInfo")

    def AcqLiveMonitorTSFormat(self, format):
        """Sets the format of the time stamp.
        :param format: (str) Timestamp (default): In msec from start of pc.
                        DateTime: yyyy/mm:dd-hh-ss
                        Unix or Linux: Seconds and μseconds since 01.01.1970"""
        self.sendCommand("AcqLiveMonitorTSFormat", format)

    def AcqAcqMonitor(self, type: str) -> List[str]:
        """Starts a mode which returns information on every new image or part image acquired in
        Acquire/Analog Integration or Photon counting mode (Acquisition monitoring).
        :param type: (str)
                    Off: No messages are output. This setting can be used to stop acquisition monitoring.
                    EndAcq: For every new part image a message is output. A part is a single image which
                            contributes to a full image. For example in Analog Integration or Photon counting
                            mode several images are combined to give one resulting image.
                    All: For every new image or every new part a message is output.
        :return: msg"""
        return self.sendCommand("AcqAcqMonitor", type)

    # === Camera commands ========================================================

    def CamParamGet(self, location: str, parameter: str) -> List[str]:
        """Returns the values of the camera options.
        :param location: (str)
                    Setup: Parameters on the options dialog.
                    Live: Parameters on the Live tab of the acquisition dialog.
                    Acquire: Parameters on the Acquire tab of the acquisition dialog.
                    AI: Parameters on the Analog Integration tab of the acquisition dialog.
                    PC: Parameters on the Photon counting tab of the acquisition dialog.
        :param parameter: (str) (Which of these parameters are relevant is dependent on
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
        :param location: (str) see CamParamGet
        :param parameter: (str) see CamParamGet
        :param value: (str) value for param"""
        # Note: When using self.acqMode = "SingleLive" parameters regarding the readout camera
        # need to be changed via location = "Live"!!!
        self.sendCommand("CamParamSet", location, parameter, value)

    def CamParamInfo(self, location, parameter):
        """Returns information about the specified parameter.
        :param location: (str) see CamParamGet
        :param parameter: (str) see CamParamGet
        :return: Label, current value, param type, min (num type only), max (num type only)
            param type: PARAM_TYPE_BOOL, PARAM_TYPE_NUMERIC, PARAM_TYPE_LIST,
                PARAM_TYPE_STRING, PARAM_TYPE_EXPTIME, PARAM_TYPE_DISPLAY"""
        return self.sendCommand("CamParamInfo", location, parameter)

    def CamParamInfoEx(self, location, parameter):
        """Returns information about the specified parameter.
        Returns more detailed information in case of a list parameter (Parameter type = 2) than CamParamInfo.
        :param location: (str) see CamParamGet
        :param parameter: (str) see CamParamGet
        :return: Label, current value, param type, min (num type only), max (num type only)
            param type: PARAM_TYPE_BOOL, PARAM_TYPE_NUMERIC, PARAM_TYPE_LIST,
                PARAM_TYPE_STRING, PARAM_TYPE_EXPTIME, PARAM_TYPE_DISPLAY"""
        return self.sendCommand("CamParamInfoEx", location, parameter)

    def CamParamsList(self, location: str) -> List[str]:
        """Returns a list of all camera parameters of the specified location.
        This command can be used to build up a complete parameter list for the corresponding camera at runtime.
        :param location: see CamParamGet
        :return: list of parameter names for the given location
        """
        return self._send_params_list_cmd("CamParamsList", location)

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
        :param location: (str)
                Streakcamera/Streak/TD: streak camera
                Del/Delay/Delaybox/Del1: delay box 1
        :param parameter: (str) Can be every parameter which appears in the external devices status/control box.
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
        :param location: (str) see DevParamGet
        :param parameter: (str) see DevParamGet
        :param value: (str) The value has to be written as it appears in the corresponding control."""

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
        :param location: (str) see DevParamGet
        :param parameter: (str) see DevParamGet
        :return: Label, current value, param type, min (numerical only), max (numerical only).
            param type: PARAM_TYPE_NUMERIC, PARAM_TYPE_LIST,
            Note: In case of a list the number of entries and all list entries are returned in the response of the
            DevParamInfoEx command."""
        return self.sendCommand("DevParamInfo", location, parameter)

    def DevParamInfoEx(self, location, parameter):
        """Return information about the specified parameter.
        Returns more detailed information in case of a list parameter (param type=2) than DevParamInfo.
        :param location: (str) see DevParamGet
        :param parameter: (str) see DevParamGet
        :return: Control available, status available, label, current value, param type, number of entries, entries.
            param type: PARAM_TYPE_NUMERIC, PARAM_TYPE_LIST"""
        return self.sendCommand("DevParamInfoEx", location, parameter)

    def DevParamsList(self, device: str) -> List[str]:
        """Return list of all parameters of a specified device.
        :param device: see location in DevParamGet
        :return: list of parameter names for the given device
        """
        return self._send_params_list_cmd("DevParamsList", device)

    # === Sequence commands ========================================================

    def SeqParamGet(self, parameter):
        """Returns the values of the sequence options or parameters.
        :param parameter: (str)
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
        :param parameter: (str) see SeqParamGet
        :param value: (str) The value for the sequence option or parameter."""
        self.sendCommand("SeqParamSet", parameter, value)

    def SeqParamInfo(self, parameter):
        """Return information about the specified parameter.
        :param parameter: (str) see SeqParamGet
        :return: label, current value, param type"""
        return self.sendCommand("SeqParamInfo", parameter)

    def SeqParamInfoEx(self, parameter):
        """Return information about the specified parameter.
        Returns more detailed information in case of a list parameter (param type=2) than SeqParamInfo.
        In case of a numeric parameter (Parameter type = 1) it additionally returns the step width.
        :param parameter: (str) see SeqParamGet
        :return: label, current value, param type"""
        return self.sendCommand("SeqParamInfoEx", parameter)

    def SeqParamsList(self) -> List[str]:
        """Return list of all parameters related to sequence mode.
        This command can be used to build up a complete parameter list related to sequence mode at runtime.
        :return: list of sequence mode parameter names
        """
        return self._send_params_list_cmd("SeqParamsList")

    def SeqSeqMonitor(self, type):
        """This command starts a mode which returns information on every new image or part image acquired in Sequence
        mode (Sequence monitoring). Its behavior is similar to AcqLiveMonitor or AcqAcqMonitor, which returns
        information on every new live or acquisition image.
        :param type: (str)
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
        :param imageType: (str)
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
        :param fileName: (str) can be any valid filename. This function can also save images on a network device, so
                            it can transfer image data from one computer to another computer.
        :param overwrite: (bool) If this is set to true
                            the file is also saved if it exists. If set to false
                            the file is not saved if it already exists and an error is returned."""
        self.sendCommand("SeqSave", imageType, fileName, str(overwrite))

    def SeqLoad(self, imageType, fileName):
        """Save a sequence.
        :param imageType: (str) see SeqSave
        :param fileName: (str) see SeqSave"""
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
        :param parameter: (str)
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
        :param parameter: (str) see ImgParamGet
        :param value: (str) depends on the parameter. See documentation """
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
        :param type: (str)
            Data: The image raw data (1,2 or 4 BBP)
            Profile: A profile is returned (4 bytes floating point values)
        :param seqNumber: (str) sequence number of the image to return
        :param filename: (str) location to write the data to. Raw data is written to the file without any header.
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

    def ImgDataGet(self, destination: str, type: str, *args) -> List[str]:
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
        elif 1 <= value:  # typically: values for the exposure time
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
        super().synchronizedOn(event)
        self._sync(event)


class HPDTASim:
    """
    Simulates the HPDTA (Hamamatsu HPD-TA) software via the RemoteEx TCP/IP protocol.

    Listens on two TCP ports: a command port that speaks the RemoteEx command/response
    protocol, and a data port that carries raw binary image and scaling-table data.

    Architecture mirrors hitachi.SUIPSim: a single state-holder class owns two
    socketserver.ThreadingTCPServer instances (one per port). A request-handler
    class processes the persistent per-connection command session. A second handler
    class manages the data-port connection and stores a reference to the socket so
    that command responses can push binary data through it.
    """

    SENSOR_HWIDTH = 1024
    SENSOR_VWIDTH = 256

    SINGLESWEEP_TIME_RANGES = [
        "1 ns", "2 ns", "5 ns", "10 ns", "20 ns", "50 ns",
        "100 ns", "200 ns", "500 ns",
        "1 us", "2 us", "5 us", "10 us", "20 us", "50 us",
        "100 us", "200 us", "500 us",
        "1 ms", "2 ms", "5 ms", "10 ms",
    ]

    SYNCHROSCAN_TIME_RANGES = ["1", "2", "3", "4", "5"]

    def __init__(self, streak_unit: str = "singlesweep", port: int = 1001):
        """
        :param streak_unit: type of streak unit to simulate, "singlesweep" or "synchroscan"
        :param port: TCP port for the command channel; data channel uses port + 1
        """
        if streak_unit not in ("singlesweep", "synchroscan"):
            raise ValueError("streak_unit must be 'singlesweep' or 'synchroscan', got %r" % streak_unit)
        self.streak_unit = streak_unit
        self.port = port
        self.port_d = port + 1

        self._must_stop = threading.Event()

        # Camera state
        self._cam_binning = (2, 2)
        self._cam_exp_time = "100 ms"      # used for Live and SingleLive modes
        self._cam_pc_exp_time = "50 ms"    # used for photon-counting (PC) mode
        self._cam_pc_nr_exposures = 500  # frames
        self._cam_pc_threshold = 123  # counts

        # Streak unit state
        self._su_mode = "Focus"
        self._su_mcp_gain = 0
        self._su_gate_mode = "Normal"
        self._su_shutter = "Closed"
        if streak_unit == "singlesweep":
            self._su_time_range = "1 ns"
            self._su_trig_mode = "Cont"
            self._su_trig_level = 1.0
            self._su_trig_slope = "Rising"
            self._su_focus_time_over = "false"
        else:  # synchroscan
            self._su_time_range = "1"
            self._su_delay = 0.0

        # Delay box state
        if streak_unit == "singlesweep":
            self._db_setting = "M1"
            self._db_trig_mode = "Ext. rising"
            self._db_delay_a = 0.0
            self._db_delay_b = 2e-8
            self._db_delay_c = 0.0
            self._db_delay_d = 0.0
            self._db_delay_e = 0.0
            self._db_delay_f = 0.0
            self._db_delay_g = 0.0
            self._db_delay_h = 0.0
            self._db_burst_mode = "Off"
            self._db_repetition_rate = 1e6
        else:  # synchroscan (C12270)
            self._db_delay_time = 0
            self._db_lock_mode = "Unlocked"
            self._db_device_status = "OK"

        # Acquisition state
        self._acq_mode = None  # None, "Live", "SingleLive", or "PC"
        self._live_monitor_type = "Off"
        self._acq_monitor_type = "Off"
        self._ring_buffer_size = 3
        self._ring_buffer = {}  # int (seq_num) -> bytes
        self._ring_buffer_seq = 0
        self._ring_buffer_lock = threading.Lock()

        # Active connections (set by handlers)
        self._data_conn = None
        self._data_conn_lock = threading.Lock()

        # Acquisition background thread
        self._acq_thread = None
        self._acq_lock = threading.Lock()

        # Time until which AsyncCommandStatus should report a pending task (0 = no pending task)
        self._task_end_t = 0

        self._cmd_server = _HPDTAServer(self, ("localhost", port), _HPDTACommandHandler)
        self._data_server = _HPDTAServer(self, ("localhost", self.port_d), _HPDTADataHandler)

        t_cmd = threading.Thread(target=self._cmd_server.serve_forever,
                                 name="HPDTASim command server", daemon=True)
        t_cmd.start()
        t_data = threading.Thread(target=self._data_server.serve_forever,
                                  name="HPDTASim data server", daemon=True)
        t_data.start()

        logging.info("HPDTASim started on ports %d/%d (streak_unit=%s)", port, self.port_d, streak_unit)

    def terminate(self):
        """Stop the simulator and close all servers."""
        self._must_stop.set()
        self._stop_acquisition()
        self._cmd_server.shutdown()
        self._cmd_server.server_close()
        self._data_server.shutdown()
        self._data_server.server_close()
        logging.info("HPDTASim terminated")

    # ── helpers ────────────────────────────────────────────────────────────

    def _get_hwidth(self) -> int:
        """Return the current horizontal pixel count (HWidth) given the binning."""
        return self.SENSOR_HWIDTH // self._cam_binning[0]

    def _get_vwidth(self) -> int:
        """Return the current vertical pixel count (VWidth) given the binning."""
        return self.SENSOR_VWIDTH // self._cam_binning[1]

    def _parse_time_range_s(self) -> float:
        """
        Parse the current streak-unit time range string to seconds.

        :return: time range in seconds
        """
        tr = self._su_time_range.strip()
        parts = tr.split(" ")
        if len(parts) == 2:
            units_map = {"s": 1.0, "ms": 1e-3, "us": 1e-6, "ns": 1e-9}
            try:
                return float(parts[0]) * units_map[parts[1]]
            except (KeyError, ValueError):
                return 1e-6
        elif len(parts) == 1:  # synchroscan integer ID -> arbitrary ps-scale values
            return (2 ** int(parts[0])) * 50e-12
        else:
            logging.warning("Unrecognized time range format %r", tr)
            return 1e-6

    # ── image and scaling-table generation ────────────────────────────────

    def _generate_image(self) -> bytes:
        """
        Generate a synthetic streak-camera image.

        Three cases based on streak mode and shutter state:

        * Operate mode, shutter open: Gaussian peak swept along the full time axis
          (intensity fades with row index, simulating a temporal sweep).
        * Focus mode (not Operate), shutter open: Gaussian peak confined to the
          centre horizontal line only (simulates the focused beam visible in Focus
          mode without time-sweeping).
        * Shutter closed: flat background with noise.

        :return: raw bytes of a uint16 image in row-major (C) order, shape (VWidth, HWidth)
        """
        h = self._get_vwidth()
        w = self._get_hwidth()

        shutter_closed = (self._su_shutter == "Closed")

        x_idx = numpy.arange(w, dtype=numpy.float32).reshape(1, -1)
        center_col = w / 2.0
        sigma_x = w / 8.0
        gauss_x = numpy.exp(-0.5 * ((x_idx - center_col) / sigma_x) ** 2)

        if shutter_closed:
            image = numpy.full((h, w), 100.0, dtype=numpy.float32)
        elif self._su_mode == "Operate":
            # Full sweep: peak fades as time progresses (row 0 = brightest)
            y_idx = numpy.arange(h, dtype=numpy.float32).reshape(-1, 1)
            fade = numpy.exp(-y_idx / max(h, 1) * 3.0)
            image = (gauss_x * fade * 50000.0).astype(numpy.float32)
        else:
            # Focus mode, shutter open: horizontal peak around 1/3rd of the screen
            center_row = h / 3.0
            sigma_y = max(h / 100.0, 1.0)
            y_idx = numpy.arange(h, dtype=numpy.float32).reshape(-1, 1)
            gauss_y = numpy.exp(-0.5 * ((y_idx - center_row) / sigma_y) ** 2)
            image = (gauss_x * gauss_y * 50000.0).astype(numpy.float32)

        # Apply MCP gain: gain=0 → ×0.1, gain=63 → ×6.4
        gain_factor = (self._su_mcp_gain + 1) / 10.0
        image *= gain_factor

        # Add shot noise
        image += numpy.random.randint(0, 50, (h, w)).astype(numpy.float32)

        return numpy.clip(image, 0, 65535).astype(numpy.uint16).tobytes()

    def _generate_scaling_table(self) -> bytes:
        """
        Generate a float32 scaling table for the time axis.

        Values are expressed in the natural unit prefix for the current time range
        (e.g. ns for a 1 ns range, us for 1 µs). This matches the convention of
        the real HPDTA: the ReadoutCamera driver multiplies by get_time_scale_factor()
        to convert the values to seconds.

        :return: raw bytes of a float32 array with one entry per vertical pixel
        """
        h = self._get_vwidth()
        time_range_s = self._parse_time_range_s()

        # Compute the same unit prefix that get_time_scale_factor() would return
        if self.streak_unit == "synchroscan":  # always ps
            unit_factor = 1e-12
        else:
            unit_factor = 10.0 ** (int(math.log10(time_range_s) // 3) * 3)
        values = numpy.linspace(0.0, time_range_s / unit_factor, h, dtype=numpy.float32)

        return values.tobytes()

    # ── acquisition control ───────────────────────────────────────────────

    def _start_acquisition(self, mode: str, command_conn, send_lock: threading.Lock):
        """
        Start the background acquisition thread.

        :param mode: "Live", "SingleLive", or "PC"
        :param command_conn: command-port socket used to push monitor notifications
        :param send_lock: lock protecting writes to command_conn
        """
        if mode not in ("Live", "SingleLive", "PC"):
            raise ValueError(f"Invalid acquisition mode {mode!r}")

        logging.debug("Starting acquisition mode %r", mode)
        with self._acq_lock:
            self._stop_acquisition_locked()
            logging.debug("acquisition ready to start")
            self._acq_mode = mode
            self._acq_thread = threading.Thread(
                target=self._acq_worker,
                args=(command_conn, send_lock),
                name="HPDTASim %s acquisition" % mode,
                daemon=True,
            )
            self._acq_thread.start()

    def _stop_acquisition(self):
        """Stop any running acquisition (thread-safe)."""
        with self._acq_lock:
            self._stop_acquisition_locked()

    def _stop_acquisition_locked(self):
        """Stop acquisition; caller must hold _acq_lock."""
        self._acq_mode = None
        t = self._acq_thread
        self._acq_thread = None
        if t is not None and t.is_alive():
            t.join(timeout=3)

    def _acq_worker(self, command_conn, send_lock: threading.Lock):
        """
        Background thread for simulating acquisition.

        In Live mode, generates one image per exposure period in a continuous
        loop. In SingleLive & PC mode, generates exactly one image and then stops.
        Each image is stored in the ring buffer and triggers a monitor
        notification on the command socket.
        """
        try:
            while self._acq_mode is not None and not self._must_stop.is_set():
                single = self._acq_mode in ("SingleLive", "PC")
                photon_counting = self._acq_mode == "PC"

                # Simulate one frame acquisition
                if photon_counting:
                    exp_s = self._parse_exp_time_s(self._cam_pc_exp_time)
                    total_time = exp_s * self._cam_pc_nr_exposures
                    logging.debug("Starting photon-counting acquisition of %s s", total_time)
                else:
                    total_time = self._parse_exp_time_s()

                self._must_stop.wait(max(total_time, 0.05))
                if self._acq_mode is None or self._must_stop.is_set():
                    break

                img_data = self._generate_image()
                with self._ring_buffer_lock:
                    # In reality, the ring-buffer is only used of live monitor, but as we only use
                    # EndAcq in PC mode, it's safe to also use the ring-buffer to hold the latest image.
                    self._ring_buffer_seq += 1
                    seq = self._ring_buffer_seq
                    self._ring_buffer[seq] = img_data
                    # Evict images beyond the ring-buffer window
                    for k in sorted(k for k in self._ring_buffer
                                     if k <= seq - self._ring_buffer_size):
                        del self._ring_buffer[k]

                if self._live_monitor_type == "RingBuffer":
                    self._push_notification(command_conn, send_lock,
                                            "4,Livemonitor,RingBuffer,%d\r" % seq)
                if self._acq_monitor_type == "EndAcq":
                    self._push_notification(command_conn, send_lock, "4,Acqmonitor,EndAcq\r")

                if single:
                    self._acq_mode = None
                    break
        except Exception:
            logging.exception("HPDTASim: exception in acquisition thread")
        finally:
            logging.debug("Acquisition thread exiting")

    @staticmethod
    def _push_notification(command_conn, send_lock: threading.Lock, msg: str):
        """
        Send an async notification on the command socket.

        :param command_conn: the command-port socket
        :param send_lock: lock protecting writes to command_conn
        :param msg: the notification string to send (must end with \\r)
        """
        with send_lock:
            try:
                command_conn.sendall(msg.encode("latin1"))
            except Exception:
                logging.debug("HPDTASim: failed to send notification %r", msg, exc_info=True)

    def _parse_exp_time_s(self, exp_time_str: Optional[str] = None) -> float:
        """
        Parse a camera exposure time string to seconds.

        :param exp_time_str: exposure time string such as "100 ms"; when None the
            Live/SingleLive exposure time (_cam_exp_time) is used
        :return: exposure time in seconds; falls back to 0.1 s on parse error
        """
        units_map = {"s": 1.0, "ms": 1e-3, "us": 1e-6, "ns": 1e-9}
        time_str = exp_time_str if exp_time_str is not None else self._cam_exp_time
        try:
            val, unit = time_str.strip().split(" ")
            return float(val) * units_map[unit]
        except (KeyError, ValueError):
            return 0.1

    # ── ring-buffer access ────────────────────────────────────────────────

    def get_ring_buffer_image(self, seq_num: int) -> Tuple[int, bytes]:
        """
        Retrieve an image from the ring buffer by sequence number.

        If the requested number is not in the buffer the oldest available image
        is returned, mirroring the real HPDTA behaviour.

        :param seq_num: the requested sequence number
        :return: (actual_seq_num, raw_image_bytes)
        """
        with self._ring_buffer_lock:
            if not self._ring_buffer:
                data = self._generate_image()
                return seq_num, data
            if seq_num not in self._ring_buffer:
                actual = min(self._ring_buffer)
            else:
                actual = seq_num
            return actual, self._ring_buffer[actual]

    def get_current_image(self) -> bytes:
        """
        Return the most recently acquired image (for AcqMonitor mode).

        :return: raw image bytes (uint16, row-major)
        """
        with self._ring_buffer_lock:
            if not self._ring_buffer:
                return self._generate_image()
            return self._ring_buffer[max(self._ring_buffer)]

    def send_data(self, data: bytes):
        """
        Push raw bytes to the client via the data port.

        :param data: bytes to transmit
        """
        with self._data_conn_lock:
            conn = self._data_conn
        if conn is not None:
            try:
                conn.sendall(data)
            except Exception:
                logging.debug("HPDTASim: failed to send data", exc_info=True)
        else:
            logging.warning("HPDTASim: no data connection available to send %d bytes", len(data))


class _HPDTAServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """
    TCPServer subclass that carries a back-reference to the HPDTASim instance.
    """
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, simulator: HPDTASim, *args, **kwargs):
        self.simulator = simulator
        super().__init__(*args, **kwargs)


class _HPDTADataHandler(socketserver.BaseRequestHandler):
    """
    Handles the data-port connection for HPDTASim.

    Sends the RemoteEx data-port greeting, stores the socket in the simulator
    so that command handlers can push binary data, and holds the connection open
    until the simulator is shut down.
    """

    def setup(self):
        self.sim = self.server.simulator

    def handle(self):
        try:
            self.request.sendall(b"RemoteEx Data Ready\r")
            with self.sim._data_conn_lock:
                self.sim._data_conn = self.request
            while not self.sim._must_stop.is_set():
                time.sleep(0.1)
        except Exception:
            logging.debug("HPDTASim data handler error", exc_info=True)
        finally:
            with self.sim._data_conn_lock:
                if self.sim._data_conn is self.request:
                    self.sim._data_conn = None


class _HPDTACommandHandler(socketserver.BaseRequestHandler):
    """
    Handles the command-port connection for HPDTASim.

    Implements the RemoteEx command protocol:
      - Client sends  FunctionName(arg1,arg2,...)\\r
      - Server replies error_code,FunctionName[,value1,...]\\r
      - Server proactively pushes 4,MonitorName[,args...]\\r notifications

    The handler runs as a persistent per-connection loop (like SUSimTCPRequestHandler
    in hitachi.py) and exits when the client closes the connection or AppEnd() is received.
    """

    def setup(self):
        self.sim = self.server.simulator
        self.request.settimeout(1.0)
        self._send_lock = threading.Lock()

    def handle(self):
        try:
            self.request.sendall(b"RemoteEx Ready\r")
            buf = ""
            while not self.sim._must_stop.is_set():
                try:
                    data = self.request.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    logging.debug("HPDTASim: command connection closed by client")
                    break
                buf += data.decode("latin1")
                # Commands are delimited by \r NOT followed by \n
                parts = re.split(r"\r(?!\n)", buf)
                buf = parts[-1]
                for cmd in parts[:-1]:
                    cmd = cmd.strip()
                    if cmd:
                        self._process_command(cmd)
        except Exception:
            logging.exception("HPDTASim command handler error")
        finally:
            logging.debug("HPDTASim command handler ended")

    # ── protocol helpers ──────────────────────────────────────────────────

    def _send_response(self, func_name: str, *values, error_code: int = 0):
        """
        Format and send a RemoteEx response.

        :param func_name: name of the function being responded to
        :param values: positional return values (converted to str)
        :param error_code: RemoteEx error code (0 = success)
        """
        parts = [str(error_code), func_name] + [str(v) for v in values]
        msg = ",".join(parts) + "\r"
        with self._send_lock:
            self.request.sendall(msg.encode("latin1"))

    def _process_command(self, msg: str):
        """
        Parse a command string and dispatch it.

        :param msg: raw command without the trailing \\r, e.g. "AcqStart(Live)"
        """
        m = re.match(r"(\w+)\((.*)\)$", msg.strip())
        if not m:
            logging.warning("HPDTASim: cannot parse command: %s", to_str_escape(msg))
            return
        func = m.group(1)
        args_str = m.group(2).strip()
        args = [a.strip() for a in args_str.split(",")] if args_str else []
        try:
            self._dispatch(func, args)
        except Exception:
            logging.exception("HPDTASim: error handling %s(%s)", func, args)
            self._send_response(func, error_code=8)

    # ── command dispatcher ────────────────────────────────────────────────

    def _dispatch(self, func: str, args: list):
        """
        Dispatch a parsed command to its handler method.

        :param func: function name as received (original case)
        :param args: list of argument strings
        """
        fl = func.lower()
        sim = self.sim

        # General / application
        # Note: Appinfo(type) and AppInfo(param) both lowercase to "appinfo";
        # they are distinguished by argument value ("type" vs "Version"/"Date"/...).
        if fl == "appinfo":
            param = args[0] if args else ""
            if param.lower() == "type":
                # Appinfo(type) – returns the application name
                self._send_response(func, "HPDTA")
            elif param.lower() == "version":
                self._send_response(func, "9.0 (HPDTASim)")
            elif param.lower() == "date":
                self._send_response(func, "01.01.2024")
            elif param.lower() == "directory":
                self._send_response(func, "C:\\HPDTASim\\")
            else:
                self._send_response(func, "HPDTASim")

        elif fl == "appstart":
            self._send_response(func)

        elif fl == "append":
            self._send_response(func)
            sim._must_stop.set()

        elif fl == "applicenceget":
            # AppLicenceGet – return "found" for application key + acquire licence
            self._send_response(func, "1", "1", "0", "0", "0", "0")

        elif fl == "asynccommandstatus":
            if time.time() < sim._task_end_t:
                self._send_response(func, "1", "0", "1")
            else:
                self._send_response(func, "0", "0", "0")

        elif fl == "stop" or fl == "shutdown":
            self._send_response(func)

        # Acquisition
        elif fl == "acqstart":
            mode = args[0] if args else "Live"
            sim._task_end_t = time.time() + 0.1
            sim._start_acquisition(mode, self.request, self._send_lock)
            self._send_response(func)

        elif fl == "acqstop":
            sim._stop_acquisition()
            self._send_response(func)

        elif fl == "acqstatus":
            status = "busy" if sim._acq_mode else "idle"
            self._send_response(func, status, sim._acq_mode or "")

        elif fl == "acqlivemonitor":
            monitor_type = args[0] if args else "Off"
            sim._live_monitor_type = monitor_type
            if monitor_type == "RingBuffer" and len(args) > 1:
                try:
                    sim._ring_buffer_size = int(args[1])
                except ValueError:
                    pass
            self._send_response(func)

        elif fl == "acqacqmonitor":
            sim._acq_monitor_type = args[0] if args else "Off"
            self._send_response(func)

        elif fl == "acqparamget":
            param = args[0] if args else ""
            defaults = {
                "displayinterval": "100",
                "32bitinai": "false",
                "pcmode": "Normal",
                "32bitinpc": "false",
                "moirrereduction": "0",
            }
            self._send_response(func, defaults.get(param.lower(), "0"))

        elif fl == "acqparamset":
            self._send_response(func)

        elif fl == "acqparaminfo":
            self._send_response(func, args[0] if args else "", "0", "0")

        elif fl == "acqparaminfoex":
            self._send_response(func, args[0] if args else "", "0", "0")

        elif fl == "acqparamslist":
            params = ["DisplayInterval", "32BitInAI", "PCMode", "32BitInPC", "MoireeReduction"]
            self._send_response(func, str(len(params)), *params)

        # Camera
        elif fl == "camparamget":
            self._handle_camparamget(func, args)

        elif fl == "camparamset":
            self._handle_camparamset(func, args)

        elif fl == "camparaminfo":
            self._handle_camparaminfo(func, args)

        elif fl == "camparaminfoex":
            self._handle_camparaminfoex(func, args)

        elif fl == "camparamslist":
            self._handle_camparamslist(func, args)

        elif fl == "camgetlivebg" or fl == "camsetupsendserial":
            self._send_response(func)

        # External devices (streak unit + delay box)
        elif fl == "devparamget":
            self._handle_devparamget(func, args)

        elif fl == "devparamset":
            self._handle_devparamset(func, args)

        elif fl == "devparaminfo":
            self._handle_devparaminfo(func, args)

        elif fl == "devparaminfoex":
            self._handle_devparaminfoex(func, args)

        elif fl == "devparamslist":
            self._handle_devparamslist(func, args)

        # Image
        elif fl == "imgringbufferget":
            self._handle_imgringbufferget(func, args)

        elif fl == "imgdataget":
            self._handle_imgdataget(func, args)

        elif fl == "imgparamget":
            self._send_response(func, "0")

        elif fl == "imgparamset":
            self._send_response(func)

        # Main / General params (minimal stubs)
        elif fl == "mainparamget":
            self._handle_mainparamget(func, args)

        elif fl in ("mainparaminfo", "mainparaminfoex"):
            self._send_response(func, args[0] if args else "", "0", "5")

        elif fl == "mainparamslist":
            params = ["ImageSize", "Message", "MCPGain", "Mode", "TimeRange"]
            self._send_response(func, str(len(params)), *params)

        elif fl in ("mainsyncget",):
            self._send_response(func, "0", "0", "0", "Sync")

        elif fl in ("mainsyncset", "genparamset"):
            self._send_response(func)

        elif fl == "genparamget":
            self._send_response(func, "0")

        elif fl in ("genparaminfo", "genparaminfoex"):
            self._send_response(func, args[0] if args else "", "false", "0")

        elif fl == "genparamslist":
            params = ["RestoreWindowPos", "UserFunctions", "ShowStreakControl"]
            self._send_response(func, str(len(params)), *params)

        else:
            logging.warning("HPDTASim: unknown command %s(%s)", func, args)
            self._send_response(func, error_code=2)

    # ── camera parameter handlers ─────────────────────────────────────────

    def _handle_camparamget(self, func: str, args: list):
        """
        Handle CamParamGet(location, parameter).

        :param func: original function name
        :param args: parsed argument list
        """
        if len(args) < 2:
            self._send_response(func, error_code=6)
            return
        _loc, param = args[0], args[1]
        pl = param.lower()
        sim = self.sim

        if pl == "camerainfo":
            info = (
                "OrcaFlash 4.0 V3\r\nProduct number: C13440-20C\r\n"
                "Serial number: 301730\r\nFirmware: 4.20.B\r\n"
                "Version: 4.20.B03-A19-B02-4.02"
            )
            self._send_response(func, info)
        elif pl == "binning":
            b = sim._cam_binning
            self._send_response(func, "%d x %d" % (b[0], b[1]))
        elif pl == "hwidth":
            self._send_response(func, str(sim._get_hwidth()))
        elif pl == "vwidth":
            self._send_response(func, str(sim._get_vwidth()))
        elif pl == "hoffs":
            self._send_response(func, "0")
        elif pl == "voffs":
            self._send_response(func, "0")
        elif pl == "exposure":
            exp = sim._cam_pc_exp_time if _loc.lower() == "pc" else sim._cam_exp_time
            self._send_response(func, exp)
        elif pl == "nrexposures" and _loc.lower() == "pc":
            self._send_response(func, str(sim._cam_pc_nr_exposures))
        elif pl == "threshold" and _loc.lower() == "pc":
            self._send_response(func, str(sim._cam_pc_threshold))
        elif pl == "timingmode":
            self._send_response(func, "Internal timing")
        elif pl == "scanmode":
            self._send_response(func, "Subarray")
        elif pl == "showgainoffset":
            self._send_response(func, "true")
        elif pl == "triggermode":
            self._send_response(func, "Edge trigger")
        elif pl == "triggersource":
            self._send_response(func, "BNC")
        elif pl == "triggerpolarity":
            self._send_response(func, "neg.")
        else:
            logging.debug("HPDTASim: CamParamGet unknown param %s", param)
            self._send_response(func, "0")

    def _handle_camparamset(self, func: str, args: list):
        """
        Handle CamParamSet(location, parameter, value).

        :param func: original function name
        :param args: parsed argument list
        """
        if len(args) < 3:
            self._send_response(func, error_code=6)
            return
        loc, param, value = args[0], args[1], args[2]
        pl = param.lower()
        sim = self.sim

        if pl == "binning":
            parts = value.split("x")
            if len(parts) == 2:
                try:
                    sim._cam_binning = (int(parts[0].strip()), int(parts[1].strip()))
                except ValueError:
                    pass
        elif pl == "exposure":
            if loc.lower() == "pc":
                sim._cam_pc_exp_time = value
            else:
                sim._cam_exp_time = value
        elif pl == "nrexposures" and loc.lower() == "pc":
            try:
                sim._cam_pc_nr_exposures = int(value)
            except ValueError:
                pass
        elif pl == "threshold" and loc.lower() == "pc":
            try:
                sim._cam_pc_threshold = int(value)
            except ValueError:
                pass
        self._send_response(func)

    def _handle_camparaminfo(self, func: str, args: list):
        """
        Handle CamParamInfo(location, parameter).

        :param func: original function name
        :param args: parsed argument list
        """
        if len(args) < 2:
            self._send_response(func, error_code=6)
            return
        loc, param = args[0], args[1]
        pl = param.lower()
        sim = self.sim

        if pl == "binning":
            b = sim._cam_binning
            self._send_response(func, "Binning", "%d x %d" % (b[0], b[1]), "2")
        elif pl == "exposure":
            exp = sim._cam_pc_exp_time if loc.lower() == "pc" else sim._cam_exp_time
            self._send_response(func, "Exposure", exp, "4", "100 us", "10 s")
        elif pl == "nrexposures" and loc.lower() == "pc":
            self._send_response(func, "# of exposures:",
                                str(sim._cam_pc_nr_exposures), "1", "1", "100000")
        elif pl == "threshold" and loc.lower() == "pc":
            self._send_response(func, "Threshold",
                                str(sim._cam_pc_threshold), "1", "0", "65535")
        else:
            self._send_response(func, param, "0", "3")

    def _handle_camparaminfoex(self, func: str, args: list):
        """
        Handle CamParamInfoEx(location, parameter).

        Response format for list parameters:  label, value, type=2, count, choice1, ...
        Response format for numeric parameters: label, value, type=1, min, max
        Response format for exposure-time lists: label, value, type=4, count, choice1, ...
          where index [4] (first choice) is the minimum and index [-1] is the maximum.

        :param func: original function name
        :param args: parsed argument list
        """
        if len(args) < 2:
            self._send_response(func, error_code=6)
            return
        loc, param = args[0], args[1]
        pl = param.lower()
        sim = self.sim

        if pl == "binning":
            b = sim._cam_binning
            choices = ["1 x 1", "2 x 2", "4 x 4"]
            self._send_response(func, "Binning", "%d x %d" % (b[0], b[1]),
                                "2", str(len(choices)), *choices)
        elif pl == "exposure":
            # type=EXPTIME (4), list of exposure times in increasing order
            exp_choices = [
                "20 us", "25 us", "30 us", "40 us", "50 us", "60 us", "70 us", "80 us",
                "100 us", "200 us", "500 us",
                "1 ms", "2 ms", "5 ms", "10 ms", "20 ms", "50 ms",
                "100 ms", "200 ms", "500 ms",
                "1 s", "2 s", "5 s", "10 s",
            ]
            exp = sim._cam_pc_exp_time if loc.lower() == "pc" else sim._cam_exp_time
            self._send_response(func, "Exposure:", exp,
                                "4", str(len(exp_choices)), *exp_choices)
        elif pl == "nrexposures" and loc.lower() == "pc":
            # type=NUMERIC (1); client reads [3] and [4] for min/max
            self._send_response(func, "# of exposures:",
                                str(sim._cam_pc_nr_exposures), "1", "1", "100000")
        elif pl == "threshold" and loc.lower() == "pc":
            self._send_response(func, "Threshold",
                                str(sim._cam_pc_threshold), "1", "0", "65535")
        else:
            self._send_response(func, param, "0", "3")

    def _handle_camparamslist(self, func: str, args: list):
        """
        Handle CamParamsList(location).

        :param func: original function name
        :param args: parsed argument list
        """
        loc = args[0].lower() if args else "setup"
        if loc == "setup":
            params = [
                "TimingMode", "TriggerMode", "TriggerSource", "TriggerPolarity",
                "ScanMode", "Binning", "HOffs", "HWidth", "VOffs", "VWidth",
                "ShowGainOffset", "CameraInfo",
            ]
        elif loc == "live":
            params = ["Exposure", "Gain", "Offset"]
        elif loc == "acquire":
            params = ["Exposure", "NrTrigger"]
        elif loc == "pc":
            params = ["Exposure", "NrExposures", "Threshold"]
        else:
            params = ["Exposure"]
        self._send_response(func, str(len(params)), *params)

    # ── device parameter handlers ─────────────────────────────────────────

    def _handle_devparamget(self, func: str, args: list):
        """
        Handle DevParamGet(location, parameter).

        :param func: original function name
        :param args: parsed argument list
        """
        if len(args) < 2:
            self._send_response(func, error_code=6)
            return
        loc, param = args[0], args[1]
        # Normalize: remove spaces and dots for easy comparison
        pl = param.lower().replace(" ", "").replace(".", "")
        ll = loc.lower()

        if ll in ("streakcamera", "streak", "td"):
            self._get_streak_param(func, param, pl)
        elif ll in ("del", "delay", "delaybox", "del1"):
            self._get_delay_param(func, param, pl)
        else:
            logging.warning("HPDTASim: DevParamGet unknown location %s", loc)
            self._send_response(func, error_code=2)

    def _get_streak_param(self, func: str, param: str, pl: str):
        """
        Return a streak-unit parameter value.

        :param func: original function name
        :param param: original parameter name (for logging)
        :param pl: normalised parameter name (lowercase, no spaces/dots)
        """
        sim = self.sim
        if pl == "devicename":
            self._send_response(func, "C10627" if sim.streak_unit == "singlesweep" else "C16910")
        elif pl == "pluginname":
            self._send_response(func, "HPDTASim")
        elif pl == "mode":
            self._send_response(func, sim._su_mode)
        elif pl == "mcpgain":
            self._send_response(func, str(sim._su_mcp_gain))
        elif pl == "timerange":
            self._send_response(func, sim._su_time_range)
        elif pl == "gatemode":
            self._send_response(func, sim._su_gate_mode)
        elif pl == "shutter":
            self._send_response(func, sim._su_shutter)
        elif pl == "trigmode" and sim.streak_unit == "singlesweep":
            self._send_response(func, sim._su_trig_mode)
        elif pl == "triglevel" and sim.streak_unit == "singlesweep":
            self._send_response(func, str(sim._su_trig_level))
        elif pl == "trigslope" and sim.streak_unit == "singlesweep":
            self._send_response(func, sim._su_trig_slope)
        elif pl == "focustimeover" and sim.streak_unit == "singlesweep":
            self._send_response(func, sim._su_focus_time_over)
        elif pl == "triggerstatus" and sim.streak_unit == "singlesweep":
            self._send_response(func, "Ready")
        elif pl == "delay" and sim.streak_unit == "synchroscan":
            self._send_response(func, str(sim._su_delay))
        else:
            logging.debug("HPDTASim: DevParamGet unknown streak param %s", param)
            self._send_response(func, "0")

    def _get_delay_param(self, func: str, param: str, pl: str):
        """
        Return a delay-box parameter value.

        :param func: original function name
        :param param: original parameter name (for logging)
        :param pl: normalised parameter name
        """
        sim = self.sim
        if pl == "devicename":
            self._send_response(func, "DG645" if sim.streak_unit == "singlesweep" else "C12270")
        elif pl == "pluginname":
            self._send_response(func, "HPDTASim")
        elif sim.streak_unit == "singlesweep":
            delay_attr = {
                "delaya": "_db_delay_a", "delayb": "_db_delay_b",
                "delayc": "_db_delay_c", "delayd": "_db_delay_d",
                "delaye": "_db_delay_e", "delayf": "_db_delay_f",
                "delayg": "_db_delay_g", "delayh": "_db_delay_h",
            }
            if pl == "setting":
                self._send_response(func, sim._db_setting)
            elif pl == "trigmode":
                self._send_response(func, sim._db_trig_mode)
            elif pl in delay_attr:
                self._send_response(func, str(getattr(sim, delay_attr[pl])))
            elif pl == "burstmode":
                self._send_response(func, sim._db_burst_mode)
            elif pl == "repetitionrate":
                self._send_response(func, str(sim._db_repetition_rate))
            else:
                logging.debug("HPDTASim: DevParamGet unknown delay param %s", param)
                self._send_response(func, "0")
        else:  # synchroscan
            if pl == "delaytime":
                self._send_response(func, str(sim._db_delay_time))
            elif pl == "lockmode":
                self._send_response(func, sim._db_lock_mode)
            elif pl == "devicestatus":
                self._send_response(func, sim._db_device_status)
            else:
                logging.debug("HPDTASim: DevParamGet unknown delay param %s", param)
                self._send_response(func, "0")

    def _handle_devparamset(self, func: str, args: list):
        """
        Handle DevParamSet(location, parameter, value).

        :param func: original function name
        :param args: parsed argument list
        """
        if len(args) < 3:
            self._send_response(func, error_code=6)
            return
        loc, param, value = args[0], args[1], args[2]
        pl = param.lower().replace(" ", "").replace(".", "")
        ll = loc.lower()
        sim = self.sim

        if ll in ("streakcamera", "streak", "td"):
            if pl == "mode":
                sim._su_mode = value
            elif pl == "mcpgain":
                try:
                    sim._su_mcp_gain = int(float(value))
                except ValueError:
                    pass
            elif pl == "timerange":
                sim._su_time_range = value
            elif pl == "gatemode":
                sim._su_gate_mode = value
            elif pl == "shutter":
                sim._su_shutter = value
                if value == "Open":
                    # Send a warning, just for testing
                    self._send_response("Shutter opened\r\nBe careful!", "6", error_code=5)
            elif pl == "trigmode":
                sim._su_trig_mode = value
            elif pl == "triglevel":
                try:
                    sim._su_trig_level = float(value)
                except ValueError:
                    pass
            elif pl == "trigslope":
                sim._su_trig_slope = value
            elif pl == "delay":
                try:
                    sim._su_delay = float(value)
                except ValueError:
                    pass

        elif ll in ("del", "delay", "delaybox", "del1"):
            float_params = {
                "delaya": "_db_delay_a", "delayb": "_db_delay_b",
                "delayc": "_db_delay_c", "delayd": "_db_delay_d",
                "delaye": "_db_delay_e", "delayf": "_db_delay_f",
                "delayg": "_db_delay_g", "delayh": "_db_delay_h",
                "delaytime": "_db_delay_time",
            }
            str_params = {
                "setting": "_db_setting",
                "trigmode": "_db_trig_mode",
                "burstmode": "_db_burst_mode",
                "lockmode": "_db_lock_mode",
            }
            if pl in float_params:
                try:
                    setattr(sim, float_params[pl], float(value))
                except ValueError:
                    pass
            elif pl in str_params:
                setattr(sim, str_params[pl], value)

        self._send_response(func)

    def _handle_devparaminfo(self, func: str, args: list):
        """
        Handle DevParamInfo(location, parameter).

        :param func: original function name
        :param args: parsed argument list
        """
        if len(args) < 2:
            self._send_response(func, error_code=6)
            return
        _loc, param = args[0], args[1]
        pl = param.lower().replace(" ", "").replace(".", "")
        sim = self.sim

        if pl == "mcpgain":
            self._send_response(func, "MCP Gain", str(sim._su_mcp_gain), "1", "0", "63")
        elif pl == "timerange":
            self._send_response(func, "Time Range", sim._su_time_range, "2")
        elif pl in ("delaya", "delayb", "delayc", "delayd", "delaye",
                    "delayf", "delayg", "delayh"):
            attr = "_db_" + pl
            val = getattr(sim, attr, 0.0)
            self._send_response(func, param, str(val), "1", "0.0", "1.0")
        elif pl == "delaytime":
            self._send_response(func, "Delay Time", str(sim._db_delay_time), "1", "0", "65535")
        elif pl == "repetitionrate":
            self._send_response(func, "Repetition Rate", str(sim._db_repetition_rate), "5")
        else:
            self._send_response(func, param, "0", "3")

    def _handle_devparaminfoex(self, func: str, args: list):
        """
        Handle DevParamInfoEx(location, parameter).

        Response format: ctrl_avail, stat_avail, label, current_value, type, [extra...]
          - NUMERIC (type=1): ctrl, stat, label, val, 1, min, max
            client uses [5:] for MCP Gain range and [-1] for delay max
          - LIST (type=2): ctrl, stat, label, val, 2, count, choice1, ...
            client uses [6:] for Time Range choices

        :param func: original function name
        :param args: parsed argument list
        """
        if len(args) < 2:
            self._send_response(func, error_code=6)
            return
        _loc, param = args[0], args[1]
        pl = param.lower().replace(" ", "").replace(".", "")
        sim = self.sim

        if pl == "mcpgain":
            # client reads [5:] → (min, max)
            self._send_response(func, "1", "1", "MCP Gain",
                                str(sim._su_mcp_gain), "1", "0", "63")
        elif pl == "timerange":
            # client reads [6:] → list of choices
            choices = (HPDTASim.SINGLESWEEP_TIME_RANGES
                       if sim.streak_unit == "singlesweep"
                       else HPDTASim.SYNCHROSCAN_TIME_RANGES)
            self._send_response(func, "1", "1", "Time Range", sim._su_time_range, "2",
                                str(len(choices)), *choices)
        elif pl in ("delaya", "delayb", "delayc", "delayd", "delaye",
                    "delayf", "delayg", "delayh"):
            # client reads [-1] as max
            attr = "_db_" + pl
            val = getattr(sim, attr, 0.0)
            self._send_response(func, "1", "1", param, str(val), "1", "0.0", "1.0")
        elif pl == "delaytime":
            self._send_response(func, "1", "1", "Delay Time",
                                str(sim._db_delay_time), "1", "0", "65535")
        elif pl == "repetitionrate":
            self._send_response(func, "0", "1", "Repetition Rate",
                                str(sim._db_repetition_rate), "5")
        elif pl == "gatemode":
            choices = ["Normal", "Gate"]
            self._send_response(func, "1", "1", "Gate Mode", sim._su_gate_mode, "2",
                                str(len(choices)), *choices)
        elif pl == "mode":
            choices = ["Focus", "Operate"]
            self._send_response(func, "1", "1", "Mode", sim._su_mode, "2",
                                str(len(choices)), *choices)
        elif pl == "shutter":
            choices = ["Closed", "Open"]
            self._send_response(func, "1", "1", "Shutter", sim._su_shutter, "2",
                                str(len(choices)), *choices)
        elif pl == "trigmode":
            choices = ["Cont", "Single", "Ext"]
            self._send_response(func, "1", "1", "Trig. Mode", sim._su_trig_mode, "2",
                                str(len(choices)), *choices)
        elif pl == "setting":
            choices = ["M1", "M2", "M3"]
            self._send_response(func, "1", "1", "Setting", sim._db_setting, "2",
                                str(len(choices)), *choices)
        elif pl == "lockmode":
            choices = ["Locked", "Unlocked"]
            self._send_response(func, "1", "1", "Lock Mode", sim._db_lock_mode, "2",
                                str(len(choices)), *choices)
        else:
            self._send_response(func, "1", "1", param, "0", "3")

    def _handle_devparamslist(self, func: str, args: list):
        """
        Handle DevParamsList(device).

        :param func: original function name
        :param args: parsed argument list
        """
        device = args[0].lower() if args else ""
        sim = self.sim

        if device in ("streakcamera", "streak", "td"):
            if sim.streak_unit == "singlesweep":
                params = [
                    "Time Range", "Mode", "Gate Mode", "MCP Gain",
                    "Shutter", "Trig. Mode", "Trigger status",
                    "Trig. level", "Trig. slope", "FocusTimeOver",
                ]
            else:
                params = ["Time Range", "Mode", "Gate Mode", "MCP Gain", "Delay","Shutter"]
        elif device in ("del", "delay", "delaybox", "del1"):
            if sim.streak_unit == "singlesweep":
                params = [
                    "Setting", "Trig. Mode",
                    "Delay A", "Delay B", "Delay C", "Delay D",
                    "Delay E", "Delay F", "Delay G", "Delay H",
                    "Burst Mode", "Repetition Rate",
                ]
            else:
                params = ["Delay Time", "Lock Mode", "Device Status"]
        else:
            logging.warning("HPDTASim: DevParamsList unknown device %s", device)
            params = []
        self._send_response(func, str(len(params)), *params)

    # ── image handlers ────────────────────────────────────────────────────

    def _handle_imgringbufferget(self, func: str, args: list):
        """
        Handle ImgRingBufferGet(type, seqNumber[, filename]).

        Responds with image geometry info on the command port and pushes the raw
        pixel data on the data port.

        :param func: original function name
        :param args: parsed argument list
        """
        if len(args) < 2:
            self._send_response(func, error_code=6)
            return
        if args[0].lower() != "data":
            self._send_response(func, error_code=2)
            return
        try:
            seq_num = int(args[1])
        except ValueError:
            self._send_response(func, error_code=1)
            return

        actual_seq, img_data = self.sim.get_ring_buffer_image(seq_num)
        h = self.sim._get_vwidth()
        w = self.sim._get_hwidth()
        timestamp = int(time.time() * 1000)
        # response: iDX, iDY, BBP, Type, seqnumber, timestamp
        self._send_response(func, str(w), str(h), "2", "0", str(actual_seq), str(timestamp))
        self.sim.send_data(img_data)

    def _handle_imgdataget(self, func: str, args: list):
        """
        Handle ImgDataGet(destination, type[, direction]).

        For type=Data: pushes raw uint16 image on data port.
        For type=ScalingTable: pushes float32 scaling-table bytes on data port.

        :param func: original function name
        :param args: parsed argument list
        """
        if len(args) < 2:
            self._send_response(func, error_code=6)
            return
        data_type = args[1].lower()
        h = self.sim._get_vwidth()
        w = self.sim._get_hwidth()

        if data_type == "data":
            img_data = self.sim.get_current_image()
            self._send_response(func, str(w), str(h), "2", "0")
            self.sim.send_data(img_data)
        elif data_type == "scalingtable":
            direction = args[2].lower() if len(args) > 2 else "vertical"
            num_values = h if direction in ("v", "ver", "vertical", "y") else w
            table_data = self.sim._generate_scaling_table()
            self._send_response(func, str(num_values), "0")
            self.sim.send_data(table_data)
        else:
            self._send_response(func, error_code=2)

    # ── main-param stubs ──────────────────────────────────────────────────

    def _handle_mainparamget(self, func: str, args: list):
        """
        Handle MainParamGet(parameter).

        :param func: original function name
        :param args: parsed argument list
        """
        param = args[0] if args else ""
        pl = param.lower().replace(" ", "")
        sim = self.sim

        if pl == "mcpgain":
            self._send_response(func, str(sim._su_mcp_gain))
        elif pl == "mode":
            self._send_response(func, sim._su_mode)
        elif pl == "timerange":
            self._send_response(func, sim._su_time_range)
        elif pl == "imagesize":
            self._send_response(func, "%dx%d" % (sim._get_hwidth(), sim._get_vwidth()))
        elif pl == "message":
            self._send_response(func, "")
        elif pl == "temperature":
            self._send_response(func, "25.0")
        elif pl == "shutter":
            val = sim._su_shutter if sim.streak_unit == "singlesweep" else "Open"
            self._send_response(func, val)
        else:
            self._send_response(func, "0")
