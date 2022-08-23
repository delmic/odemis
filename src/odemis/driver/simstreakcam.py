# -*- coding: utf-8 -*-
'''
Created on 29 Oct 2018

@author: Sabrina Rossberger

Copyright © 2018 Sabrina Rossberger, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

from builtins import str
import queue
import logging
from odemis import model, util, dataio
from odemis.model import oneway
import time
import numpy
import os
import numbers


class ReadoutCamera(model.DigitalCamera):
    """
    Represents a readout camera for the streak camera system.
    """

    def __init__(self, name, role, parent, image, spectrograph=None, daemon=None, **kwargs):
        """ Initializes a fake readout camera.
        :parameter name: (str) as in Odemis
        :parameter role: (str) as in Odemis
        :parameter parent: class streakcamera
        :parameter image: fake input image
        """
        # TODO image focus and operate mode
        # get the fake images
        try:
            image_filename = str(image)
            # ensure relative path is from this file
            if not os.path.isabs(image):
                image_filename = os.path.join(os.path.dirname(__file__), image)
            converter = dataio.find_fittest_converter(image_filename, mode=os.O_RDONLY)
            self._img_list = []
            img_list = converter.read_data(image_filename)
            for img in img_list:
                if img.ndim > 3:  # remove dims of length 1
                    img = numpy.squeeze(img)
                self._img_list.append(img)  # can be RGB or greyscale
        except Exception:
            raise ValueError("Fake image does not fit requirements for temporal spectrum acquisition.")

        super(ReadoutCamera, self).__init__(name, role, parent=parent,
                                            daemon=daemon, **kwargs)  # init HwComponent

        self.parent = parent

        self._metadata[model.MD_HW_VERSION] = 'Simulated readout camera OrcaFlash 4.0 V3, ' \
                                              'Product number: C13440-20C, Serial number: 301730'
        self._metadata[model.MD_SW_VERSION] = 'Firmware: 4.20.B, Version: 4.20.B03-A19-B02-4.02'
        self._metadata[model.MD_DET_TYPE] = model.MD_DT_INTEGRATING

        # sensor size (resolution)
        # x (lambda): horizontal, y (time): vertical
        full_res = self._transposeSizeToUser((self._img_list[0].shape[1], self._img_list[0].shape[0]))
        self._metadata[model.MD_SENSOR_SIZE] = full_res
        self._metadata[model.MD_DIMS] = "TC"

        # 16-bit
        depth = 2 ** (self._img_list[0].dtype.itemsize * 8)
        self._shape = full_res + (depth,)

        # variable needed to update resolution VA and wavelength list correctly (_updateWavelengthList())
        self._binning = self._transposeSizeToUser((2, 2))

        # need to be before binning, as it is modified when changing binning
        resolution = (int(full_res[0]/self._binning[0]), int(full_res[1]/self._binning[1]))
        self.resolution = model.ResolutionVA(resolution,
                                             ((1, 1), full_res),
                                             setter=self._setResolution)

        # variable needed to update wavelength list correctly (_updateWavelengthList())
        self._resolution = self.resolution.value

        choices_bin = {(1, 1), (2, 2), (4, 4)}  # Should be converted to user, but they are identical
        self.binning = model.VAEnumerated(self._binning, choices_bin, setter=self._setBinning)
        self._metadata[model.MD_BINNING] = self.binning.value

        # physical pixel size is 6.5um x 6.5um
        sensor_pixelsize = self._transposeSizeToUser((6.5e-06, 6.5e-06))
        self._metadata[model.MD_SENSOR_PIXEL_SIZE] = sensor_pixelsize

        # pixelsize VA is the sensor size, it does not include binning or magnification
        self.pixelSize = model.VigilantAttribute(sensor_pixelsize, unit="m", readonly=True)

        range_exp = [0.00001, 1]  # 10us to 1s
        self._exp_time = 0.1  # 100 msec
        self.exposureTime = model.FloatContinuous(self._exp_time, range_exp, unit="s", setter=self._setCamExpTime)
        self._metadata[model.MD_EXP_TIME] = self.exposureTime.value

        self.readoutRate = model.VigilantAttribute(425000000, unit="Hz", readonly=True)  # MHz
        self._metadata[model.MD_READOUT_TIME] = 1 / self.readoutRate.value  # s

        # spectrograph VAs after readout camera VAs
        self._spectrograph = spectrograph
        if self._spectrograph:
            logging.debug("Starting streak camera with spectrograph.")
            self._spectrograph.position.subscribe(self._updateWavelengthList, init=True)
        else:
            logging.warning("No spectrograph specified. No wavelength metadata will be attached.")

        # for synchronized acquisition
        self._sync_event = None
        self.softwareTrigger = model.Event()

        # Simple implementation of the flow: we keep generating images and if
        # there are subscribers, they'll receive it.
        self.data = SimpleStreakCameraDataFlow(self._start, self._stop, self._sync)
        self._generator = None

        self._img_counter = 0  # initialize the image counter

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

    def _setBinning(self, value):
        """
        Called when "binning" VA is modified. It actually modifies the camera binning.
        :parameter value: (2-tuple int) binning value to set
        :return: current binning value
        """
        prev_binning, self._binning = self._binning, value

        # call resolution setter to update res
        self.resolution.value = self.resolution.value  # set any value

        self._metadata[model.MD_BINNING] = self._binning  # update MD

        return self._binning

    def _setResolution(self, _=None):
        """
        Sets the resolution VA and also ensures the wavelength list is correct.
        So far the full field of view is always used. Therefore, resolution only changes with binning.
        :return: current resolution value
        """
        # Note: we can keep it simple as long as we do not provide to change the sensor size yet...

        resolution = self._shape[:2]
        new_res = (int(resolution[0] // self._binning[0]),
                   int(resolution[1] // self._binning[1]))  # floor division

        self._resolution = new_res  # update so wavelength list is correctly calculated

        if self._spectrograph:
            self._updateWavelengthList()  # update WavelengthList when changing binning

        return new_res

    def _setCamExpTime(self, value):
        """
        Set the camera exposure time.
        :parameter value: (float) exposure time to be set
        :return: (float) current exposure time
        """
        self._metadata[model.MD_EXP_TIME] = value  # update MD

        return value

    def _sync(self, event):  # event = self.softwareTrigger
        """
        Synchronize the acquisition on the given event. Every time the event is
          triggered, the DataFlow will start a new acquisition.
        Behaviour is unspecified if the acquisition is already running.  # TODO still True???
        event (model.Event or None): event to synchronize with. Use None to
          disable synchronization.
        The DataFlow can be synchronized only with one Event at a time.
        """
        # if event None and sync as well -> return, or if event sync, but sync already set -> return
        if self._sync_event == event:
            return

        if self._sync_event:  # if new event = None, unsubscribe previous event (which was softwareTrigger)
            self._sync_event.unsubscribe(self)
            if not event:
                self._evtq.put(None)  # in case it was waiting for this event

        self._sync_event = event

        if self._sync_event:
            # softwareTrigger subscribes to onEvent method: if softwareTrigger.notify() called, onEvent method called
            self._sync_event.subscribe(self)  # must have onEvent method
            self._evtq = queue.Queue()  # to be sure it's empty

    @oneway
    def onEvent(self):
        """
        Called by the Event when it is triggered  (e.g. self.softwareTrigger.notify()).
        """
        logging.debug("Event triggered to start a new synchronized acquisition.")
        self._evtq.put(time.time())

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
        Create dict containing all metadata from the children readout camera, streak unit,
        delay genereator and the metadata from the parent streak camera.
        :return: merged metadata
        """

        md_devices = [self.parent._streakunit._metadata, self.parent._delaybox._metadata]

        for md_dev in md_devices:
            for key in md_dev.keys():
                if key not in md:
                    md[key] = md_dev[key]
                elif key in (model.MD_HW_NAME, model.MD_HW_VERSION, model.MD_SW_VERSION):
                    md[key] = md[key] + ", " + md_dev[key]

        return md

    def terminate(self):
        try:
            self._stop()  # stop any acquisition
        except Exception:
            pass

    def _start(self):
        """
        Start an acquisition.
        """
        if self._generator is not None:
            logging.warning("Generator already running")
            return
        self._generator = util.RepeatingTimer(self.exposureTime.value,
                                              self._generate,
                                              "SimCam image generator")
        self._generator.start()

    def _stop(self):
        """
        Stop the acquisition.
        """
        if self._generator is not None:
            self._generator.cancel()
            self._generator = None

    def _getNewImage(self):
        """
        Gets a new image from the list of fake images provided.
        :return: (dataarray) image
        """
        self._img_counter = (self._img_counter + 1) % len(self._img_list)
        image = self._img_list[self._img_counter].copy()

        # Add some noise
        mx = image.max()
        image += numpy.random.randint(0, max(mx // 100, 10), image.shape, dtype=image.dtype)
        # Clip, but faster than clip() on big array.
        # There can still be some overflow, but let's just consider this "strong noise"
        image[image > mx] = mx

        return self._transposeDAToUser(image)

    def _generate(self):
        """
        Generates the fake output image based on the resolution.
        """
        gen_img = self._getNewImage()

        # Processes the fake image based on resolution and binning.
        binning = self.binning.value
        res = self.resolution.value
        gen_img = gen_img.reshape((res[1], binning[0], res[0],
                                   binning[1])).mean(axis=3).mean(axis=1).astype(gen_img.dtype)

        timer = self._generator  # might be replaced by None afterwards, so keep a copy
        self._waitSync()
        if self._sync_event:
            # If sync event, we need to simulate period after event (not efficient, but works)
            time.sleep(self.exposureTime.value)

        # update MD for the current image
        self.parent._delaybox._updateTriggerRate()

        metadata = gen_img.metadata.copy()  # MD of image
        metadata.update(self._metadata)  # MD of camera
        self._mergeMetadata(metadata)

        # update fake output metadata
        exp = timer.period
        metadata[model.MD_ACQ_DATE] = time.time() - exp
        metadata[model.MD_EXP_TIME] = exp
        metadata.pop(model.MD_PIXEL_SIZE, None)  # TODO get rid of this MD for now

        if self.parent._streakunit.streakMode.value:  # if in Mode 'Operate'
            # shape of gen_image is (time, lambda)
            timeRange = self.parent._streakunit.timeRange.value  # time range of the time axis in image
            # each px in time axis corresponds to a time stamp (easy approach: equally distributed)
            metadata[model.MD_TIME_LIST] = list(numpy.linspace(0, timeRange, gen_img.shape[0]))  # append fake time list
        logging.debug("Generating new fake image of shape %s", gen_img.shape)

        img = model.DataArray(gen_img, metadata)

        # send the new image (if anyone is interested)
        self.data.notify(img)

        # simulate exposure time
        timer.period = self.exposureTime.value

    def _waitSync(self):
        """
        Block until the Event on which the dataflow is synchronised has been
          received. If the DataFlow is not synchronised on any event, this
          method immediately returns
        """
        if self._sync_event:
            self._evtq.get()


class StreakUnit(model.HwComponent):
    """
    Represents a streak unit.
    """

    def __init__(self, name, role, parent, daemon=None, **kwargs):
        super(StreakUnit, self).__init__(name, role, parent=parent, daemon=daemon, **kwargs)  # init HwComponent

        self.parent = parent

        self._metadata[model.MD_HW_VERSION] = "Simulated streak unit C10627"

        # VAs
        self.streakMode = model.BooleanVA(False, setter=self._setStreakMode)  # default False see set params above

        gain = 0
        range_gain = (0, 63)
        self.MCPGain = model.IntContinuous(gain, range_gain, setter=self._setMCPGain)

        timeRange = 0.000000001
        choices = {0.000000001, 0.000000002, 0.000000005, 0.00000001, 0.00000002, 0.00000005, 0.0000001,
                   0.0000002, 0.0000005,
                   0.000001, 0.000002, 0.000005, 0.00001, 0.00002, 0.00005, 0.0001, 0.0002, 0.0005,
                   0.001, 0.002, 0.005, 0.01}
        self.timeRange = model.FloatEnumerated(timeRange, choices, setter=self._setTimeRange, unit="s")

        self._metadata[model.MD_STREAK_TIMERANGE] = self.timeRange.value
        self._metadata[model.MD_STREAK_MCPGAIN] = self.MCPGain.value
        self._metadata[model.MD_STREAK_MODE] = self.streakMode.value

    def _setStreakMode(self, value):
        """
        Updates the streakMode VA.
        :parameter value: (bool) value to be set
        :return: (bool) current streak mode
        """
        # when changing the StreakMode to Focus, set MCPGain zero for HW safety reasons
        if not value:
            self.MCPGain.value = 0
        logging.debug("Reporting mode %s for streak unit.", value)
        self._metadata[model.MD_STREAK_MODE] = value

        return value

    def _setMCPGain(self, value):
        """
        Updates the MCPGain VA.
        :parameter value: (int) value to be set
        :return: (int) current MCPGain
        """
        logging.debug("Reporting MCP gain %s for streak unit.", value)
        self._metadata[model.MD_STREAK_MCPGAIN] = value

        return value

    def _setTimeRange(self, value):
        """
        Updates the timeRange VA.
        :parameter value: (float) value to be set
        :return: (float) current time range
        """
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

    def terminate(self):
        self.MCPGain.value = 0
        self.streakMode.value = False


class DelayGenerator(model.HwComponent):
    """
    Represents a delay generator.
    """

    def __init__(self, name, role, parent, daemon=None, **kwargs):
        super(DelayGenerator, self).__init__(name, role, parent=parent, daemon=daemon, **kwargs)  # init HwComponent

        self.parent = parent

        self._metadata[model.MD_HW_VERSION] = "Simulated delay generator DG645"

        range_trigDelay = [0.0, 1]  # sec
        # set default value according to timeRange setting (look up in MD)
        self.triggerDelay = model.FloatContinuous(0.0, range_trigDelay, setter=self._setTriggerDelay, unit="s")

        self._metadata[model.MD_TRIGGER_DELAY] = self.triggerDelay.value
        self._metadata[model.MD_TRIGGER_RATE] = 1000000

    # override HwComponent.updateMetadata
    def updateMetadata(self, md):

        if model.MD_TIME_RANGE_TO_DELAY in md:
            for timeRange, delay in md[model.MD_TIME_RANGE_TO_DELAY].items():
                if not isinstance(delay, numbers.Real):
                    raise ValueError("Trigger delay %s corresponding to time range %s is not of type float."
                                     "Please check calibration file for trigger delay." % (delay, timeRange))
                if not 0 <= delay <= 1:
                    raise ValueError("Trigger delay %s corresponding to time range %s is not in range (0, 1)."
                                     "Please check the calibration file for the trigger delay." % (delay, timeRange))

        super(DelayGenerator, self).updateMetadata(md)

    def _setTriggerDelay(self, value):
        """
        Updates the trigger delay VA.
        :parameter value: (float) value to be set
        :return: (float) current trigger delay value
        """
        logging.debug("Reporting trigger delay %s for delay generator.", value)
        self._metadata[model.MD_TRIGGER_DELAY] = value

        return value

    def _updateTriggerRate(self):
        """Get the trigger rate (repetition) rate from the delay generator and updates the VA.
        The Trigger rate corresponds to the ebeam blanking frequency. As the delay
        generator is operated "external", the trigger rate is a read-only value.
        Called whenever an image arrives."""
        value = numpy.random.randint(100000, 1000000)  # return a random trigger rate
        self._metadata[model.MD_TRIGGER_RATE] = value


class StreakCamera(model.HwComponent):
    """
    Represents the streak camera system.
    """

    def __init__(self, name, role, children=None, dependencies=None, daemon=None, **kwargs):
        """
        Initializes the device.
        """
        super(StreakCamera, self).__init__(name, role, dependencies=dependencies, daemon=daemon, **kwargs)

        children = children or {}
        dependencies = dependencies or {}

        try:
            kwargs = children["readoutcam"]
        except Exception:
            raise ValueError("Required child readoutcam not provided")
        self._readoutcam = ReadoutCamera(parent=self,
                                         spectrograph=dependencies.get("spectrograph"),
                                         daemon=daemon, **kwargs)
        self.children.value.add(self._readoutcam)  # add readoutcam to children-VA
        try:
            kwargs = children["streakunit"]
        except Exception:
            raise ValueError("Required child streakunit not provided")
        self._streakunit = StreakUnit(parent=self, daemon=daemon, **kwargs)
        self.children.value.add(self._streakunit)  # add streakunit to children-VA
        try:
            kwargs = children["delaybox"]
        except Exception:
            raise ValueError("Required child delaybox not provided")
        self._delaybox = DelayGenerator(parent=self, daemon=daemon, **kwargs)
        self.children.value.add(self._delaybox)  # add delaybox to children-VA

    def terminate(self):
        """
        Terminate all components.
        """
        # terminate children
        for child in self.children.value:
            child.terminate()

    def StartAcquisition(self):
        """
        Start an acquisition.
        """
        self._readoutcam._generate()


class SimpleStreakCameraDataFlow(model.DataFlow):
    """
    Represents the dataflow on the readout camera.
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

    # start/stop_generate are _never_ called simultaneously (thread-safe)
    def start_generate(self):
        """
        Start the dataflow.
        """
        self._start()

    def stop_generate(self):
        """
        Stop the dataflow.
        """
        self._stop()

    def synchronizedOn(self, event):
        """
        Synchronize the dataflow.
        """
        self._sync(event)

