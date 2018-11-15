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

from __future__ import division

import Queue
import logging
from odemis import model, util, dataio
from odemis.model import oneway
import time
import numpy
import os


class ReadoutCamera(model.DigitalCamera):
    """
    Represents Hamamatsu readout camera.
    """

    def __init__(self, name, role, parent, spectrograph=None, daemon=None, **kwargs):
        """ Initializes a fake Hamamatsu OrcaFlash readout camera.
        :parameter name: (str) as in Odemis
        :parameter role: (str) as in Odemis
        :parameter parent: class streakcamera
        """
        # TODO image focus and operate mode
        # get the fake images
        try:
            image = kwargs.pop("image")
            image = unicode(image)
            # ensure relative path is from this file
            if not os.path.isabs(image):
                image = os.path.join(os.path.dirname(__file__), image)
            converter = dataio.find_fittest_converter(image, mode=os.O_RDONLY)
            self._img = []
            for i in range(10):
                img = converter.read_data(image)[i+2]
                if img.ndim > 3:  # remove dims of length 1
                    img = numpy.squeeze(img)
                self._img.append(img)  # can be RGB or greyscale
            self._img_counter = 0  # image counter to provide different images to live view
        except:
            raise Exception("No fake image provided")

        super(ReadoutCamera, self).__init__(name, role, parent=parent,
                                            daemon=daemon, **kwargs)  # init HwComponent

        self.parent = parent

        self._spectrograph = spectrograph
        if not spectrograph:
            logging.warning("No spectrograph specified. No wavelength metadata will be attached.")

        self._metadata[model.MD_HW_VERSION] = 'Simulated readout camera OrcaFlash 4.0 V3, ' \
                                              'Product number: C13440-20C, Serial number: 301730'
        self._metadata[model.MD_SW_VERSION] = 'Firmware: 4.20.B, Version: 4.20.B03-A19-B02-4.02'
        self._metadata[model.MD_DET_TYPE] = model.MD_DT_INTEGRATING

        # sensor size (resolution)
        # Note: sensor size of OrcaFlash is actually much larger (2048px x 2048px)
        # However, only a smaller subarea is used for operating the streak system.
        resolution = (1344, 1016)  # x (lambda): horizontal, y (time): vertical
        self._metadata[model.MD_SENSOR_SIZE] = resolution

        # 16-bit
        self._shape = resolution + (2 ** 16,)

        self._binning = (2, 2)

        # need to be before binning, as it is modified when changing binning
        _resolution = (int(resolution[0]/self._binning[0]), int(resolution[1]/self._binning[1]))
        self.resolution = model.ResolutionVA(_resolution, ((1, 1), resolution), setter=self._setResolution)

        choices_bin = {(1, 1), (2, 2), (4, 4)}
        self.binning = model.VAEnumerated(self._binning, choices_bin, setter=self._setBinning)
        self._metadata[model.MD_BINNING] = self.binning.value

        # physical pixel size is 6.5um x 6.5um
        sensor_pixelsize = (6.5e-06, 6.5e-06)
        self._metadata[model.MD_SENSOR_PIXEL_SIZE] = sensor_pixelsize

        # pixelsize VA is the sensor size, it does not include binning or magnification
        self.pixelSize = model.VigilantAttribute(sensor_pixelsize, unit="m", readonly=True)

        # multiply with mag as we use the 1/M as input in yaml file!
        eff_pixelsize = sensor_pixelsize[0] * self._binning[0] * self._metadata.get(model.MD_LENS_MAG, 1.0)
        # self._metadata[model.MD_RESOLUTION] = eff_pixelsize  # TODO think it is useful

        # Note: no function to get current acqMode.
        # Note: Acquisition mode, needs to be before exposureTime!
        # Acquisition mode should be either "Live" (non-sync acq) or "SingleLive" (sync acq) for now.
        self.acqMode = "Live"

        range_exp = [0.00001, 10]  # 10us to 10s
        self._exp_time = 0.1  # 100 msec
        self.exposureTime = model.FloatContinuous(self._exp_time, range_exp, unit="s", setter=self._setCamExpTime)
        self._metadata[model.MD_EXP_TIME] = self.exposureTime.value

        self.readoutRate = model.VigilantAttribute(425000000, unit="Hz", readonly=True)  # MHz
        self._metadata[model.MD_READOUT_TIME] = 1 / self.readoutRate.value  # s

        # spectrograph VAs after readout camera VAs
        if self._spectrograph:
            logging.debug("Starting streak camera with spectrograph.")
            self._spectrograph.position.subscribe(self._updateWavelengthList, init=True)

        # for synchronized acquisition
        self._sync_event = None
        self.softwareTrigger = model.Event()

        # Simple implementation of the flow: we keep generating images and if
        # there are subscribers, they'll receive it.
        self.data = SimpleStreakCameraDataFlow(self._start, self._stop, self._sync)
        self._generator = None

    def _updateWavelengthList(self, _=None):
        npixels = self.resolution.value[0]  # number of pixels, horizontal is wavelength
        # pixelsize VA is sensor px size without binning and magnification
        pxs = self.pixelSize.value[0] * self.binning.value[0] * self._metadata.get(model.MD_LENS_MAG, 1.0)
        wll = self._spectrograph.getPixelToWavelength(npixels, pxs)
        self._metadata[model.MD_WL_LIST] = wll

    def _setBinning(self, value):
        """
        value (2-tuple int)
        Called when "binning" VA is modified. It actually modifies the camera binning.
        """
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

        if self._spectrograph:
            self._updateWavelengthList()  # update WavelengthList when changing binning

        return self._binning

    def _setResolution(self, _=None):
        """Sets the resolution VA.
        So far the full field of view is always used. Therefore, resolution only changes with binning
        or magnification."""

        # Note: we can keep it simple as long as we do not provide to change the sensor size yet...
        resolution = self._shape[:2]
        new_res = (int(resolution[0] // self._binning[0]),
                    int(resolution[1] // self._binning[1]))  # floor division: not below zero

        if self._spectrograph:
            self._updateWavelengthList()  # update WavelengthList when changing binning

        return new_res

    def _setCamExpTime(self, value):
        """Translate exposure time into a for RemoteEx readable format.
        :parameter location: (str) see CamParamGet
        :parameter exp_time (float): exposure time"""
        self._metadata[model.MD_EXP_TIME] = value  # update MD

        return value

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
            self._sync_event.unsubscribe(self)
            if not event:
                self._evtq.put(None)  # in case it was waiting for this event

        self._sync_event = event

        if self._sync_event:
            # softwareTrigger subscribes to onEvent method: if softwareTrigger.notify() called, onEvent method called
            self._sync_event.subscribe(self)  # must have onEvent method
            self._evtq = Queue.Queue()  # to be sure it's empty

    @oneway
    def onEvent(self):
        """Called by the Event when it is triggered  (e.g. self.softwareTrigger.notify())."""
        logging.debug("Event triggered to start a new synchronized acquisition.")
        self._evtq.put(time.time())

    # override
    def updateMetadata(self, md):
        super(ReadoutCamera, self).updateMetadata(md)
        if model.MD_LENS_MAG in md:
            self._updateWavelengthList()

    def _mergeMetadata(self, md):
        """Create dict containing all metadata from the children readout camera, streak unit, delay genereator
        and the metadata from the parent streak camera."""

        md_devices = [self.parent._streakunit._metadata, self.parent._delaybox._metadata]

        for md_dev in md_devices:
            for key in md_dev.keys():
                if key not in md.keys():
                    md[key] = md_dev[key]
                else:
                    md[key] = md[key] + ", " + md_dev[key]  # TODO change in real driver
                    # md[key].append(md_dev[key])  # TODO make nice  ", ".join(c)
        return md

    def terminate(self):
        try:
            self._stop()  # stop any acquisition
        except Exception:  # TODO which exception?
            pass

    def _start(self):
        if self._generator is not None:
            logging.warning("Generator already running")
            return
        self._generator = util.RepeatingTimer(self.exposureTime.value,
                                              self._generate,
                                              "SimCam image generator")
        self._generator.start()

    def _stop(self):
        if self._generator is not None:
            self._generator.cancel()
            self._generator = None

    def _getNewImage(self):
        """Gets a new image from the list of fake images provided."""
        if self._img_counter == 10:
            self._img_counter = 0
        return self._img[self._img_counter]

    def _generate(self):
        """
        Generates the fake output based on the resolution.
        """
        gen_img = self._getNewImage()
        # TODO pass different image
        # gen_img = self._img  # TODO needed if we allow to change binning: self._simulate()
        timer = self._generator  # might be replaced by None afterwards, so keep a copy
        self._waitSync()
        if self._sync_event:
            # If sync event, we need to simulate period after event (not efficient, but works)
            time.sleep(self.exposureTime.value)

        # update the trigger rate VA and MD for the current image
        self.parent._delaybox._getTriggerRate()

        metadata = gen_img.metadata.copy()  # MD of image
        metadata.update(self._metadata)  # MD of camera
        self._mergeMetadata(metadata)

        # update fake output metadata
        exp = timer.period
        metadata[model.MD_ACQ_DATE] = time.time() - exp
        metadata[model.MD_EXP_TIME] = exp
        metadata.pop(model.MD_PIXEL_SIZE, None)  # TODO get rid of this MD for now

        # TODO check if that time list can be used to update the live view legend if there is one
        if self.parent._streakunit.streakMode.value:  # if in Mode 'Operate'
            # shape of gen_image is (time, lambda)
            timeRange = self.parent._streakunit.timeRange.value  # time range of the time axis in image
            # each px in time axis corresponds to a time stamp (easy approch: equally distributed)
            step_time = timeRange/float(gen_img.shape[0])
            metadata[model.MD_TIME_LIST] = list(numpy.arange(0, timeRange, step_time))  # append fake time list
        logging.debug("Generating new fake image of shape %s", gen_img.shape)

        img = model.DataArray(gen_img, metadata)

        # send the new image (if anyone is interested)
        self.data.notify(img)

        # simulate exposure time
        timer.period = self.exposureTime.value

        self._img_counter += 1

    def _waitSync(self):
        """
        Block until the Event on which the dataflow is synchronised has been
          received. If the DataFlow is not synchronised on any event, this
          method immediately returns
        """
        if self._sync_event:
            self._evtq.get()

    # def _simulate(self):  # TODO
    #     """
    #     Processes the fake image based on the translation, resolution and
    #     current drift.
    #     """
    #     binning = self.binning.value
    #     res = self.resolution.value
    #     pxs_pos = self.translation.value
    #     shape = self._img.shape
    #     center = (shape[1] / 2, shape[0] / 2)
    #     lt = (center[0] + pxs_pos[0] - (res[0] / 2) * binning[0],
    #           center[1] + pxs_pos[1] - (res[1] / 2) * binning[1])
    #     assert(lt[0] >= 0 and lt[1] >= 0)
    #     # compute each row and column that will be included
    #     # TODO: Could use something more hardwarish like that:
    #     # data0 = data0.reshape(shape[0]//b0, b0, shape[1]//b1, b1).mean(3).mean(1)
    #     # (or use sum, to simulate binning)
    #     # Alternatively, it could use just [lt:lt+res:binning]
    #     coord = ([int(round(lt[0] + i * binning[0])) for i in range(res[0])],
    #              [int(round(lt[1] + i * binning[1])) for i in range(res[1])])
    #     sim_img = self._img[numpy.ix_(coord[1], coord[0])]  # copy
    #     return sim_img


class StreakUnit(model.HwComponent):
    """
    Represents Hamamatsu streak unit.
    """

    def __init__(self, name, role, parent, daemon=None, **kwargs):
        super(StreakUnit, self).__init__(name, role, parent=parent, daemon=daemon, **kwargs)  # init HwComponent

        self.parent = parent
        self.location = "Streakcamera"  # don't change, internally needed by HPDTA/RemoteEx

        self._metadata[model.MD_HW_VERSION] = "Simulated streak unit C10627"

        # VAs
        self.streakMode = model.BooleanVA(False, setter=self._updateStreakMode)  # default False see set params above

        gain = 0
        range_gain = (0, 63)
        self.MCPgain = model.IntContinuous(gain, range_gain, setter=self._updateMCPGain)

        timeRange = 0.000000001
        choices = {0.000000001, 0.000000002, 0.000000005, 0.00000001, 0.00000002, 0.00000005, 0.0000001,
                   0.0000002, 0.0000005,
                   0.000001, 0.000002, 0.000005, 0.00001, 0.00002, 0.00005, 0.0001, 0.0002, 0.0005,
                   0.001, 0.002, 0.005, 0.01}
        self.timeRange = model.FloatEnumerated(timeRange, choices, setter=self._updateTimeRange)

        self._metadata[model.MD_STREAK_TIMERANGE] = self.timeRange.value
        self._metadata[model.MD_STREAK_MCPGAIN] = self.MCPgain.value
        self._metadata[model.MD_STREAK_MODE] = self.streakMode.value

    def _updateStreakMode(self, value):
        """
        update the mode VA
        """
        # when changing the StreakMode to Focus, set MCPGain zero for HW safety reasons
        if not value:
            self.MCPgain.value = 0
        logging.debug("Reporting mode %s for streak unit.", value)
        self._metadata[model.MD_STREAK_MODE] = value

        return value

    def _updateMCPGain(self, value):
        """
        update the MCP gain VA
        """
        logging.debug("Reporting MCP gain %s for streak unit.", value)
        self._metadata[model.MD_STREAK_MCPGAIN] = value

        return value

    def _updateTimeRange(self, value):
        """
        update the time range VA
        """
        # when changing the timeRange, set MCPGain zero for HW safety reasons
        self.MCPgain.value = 0
        logging.debug("Reporting time range %s for streak unit.", value)
        self._metadata[model.MD_STREAK_TIMERANGE] = value

        return value

    def terminate(self):
        self.MCPgain.value = 0
        self.streakMode = False


class DelayGenerator(model.HwComponent):
    """
    Represents delay generator.
    """

    def __init__(self, name, role, parent, daemon=None, **kwargs):
        super(DelayGenerator, self).__init__(name, role, parent=parent, daemon=daemon, **kwargs)  # init HwComponent

        self.parent = parent
        self.location = "Delaybox"  # don't change, internally needed by HPDTA/RemoteEx

        self._metadata[model.MD_HW_VERSION] = "Simulated delay generator DG645"

        range_trigDelay = [0.0, 1]  # sec TODO  check which values were actually allowed
        self.triggerDelay = model.FloatContinuous(0.0, range_trigDelay, setter=self._updateTriggerDelay)

        self._metadata[model.MD_TRIGGER_DELAY] = self.triggerDelay.value
        self._metadata[model.MD_TRIGGER_RATE] = 1000000

    def _updateTriggerDelay(self, value):
        """
        update the mode VA
        """
        logging.debug("Reporting trigger delay` %s for delay generator.", value)
        self._metadata[model.MD_TRIGGER_DELAY] = value

        return value

    def _getTriggerRate(self):
        """Get the trigger rate (repetition) rate from the delay generator and updates the VA.
        The Trigger rate corresponds to the ebeam blanking frequency. As the delay
        generator is operated "external", the trigger rate is a read-only value.
        Called whenever an image arrives."""
        value = numpy.random.randint(100000, 1000000)  # return a random trigger rate
        self._metadata[model.MD_TRIGGER_RATE] = value

    def terminate(self):
        """nothing to do here"""
        pass


class StreakCamera(model.HwComponent):
    """
    Represents Hamamatsu readout camera for the streak unit.
    Client to connect to HPD-TA software via RemoteEx.
    """

    def __init__(self, name, role, children=None, port=None, host=None, daemon=None, **kwargs):
        """
        Initializes the device.
        host (str): hostname or IP-address
        port (int or None): port number for sending/receiving commands (None if not set)
        """
        super(StreakCamera, self).__init__(name, role, daemon=daemon, **kwargs)

        if port is None:
            raise ValueError("Please specify port of camera to be used.")
        if host is None:
            raise ValueError("Please specify host to connect to.")

        port_d = port + 1  # the port number to receive the image data
        self.host = host
        self.port = port
        self.port_d = port_d

        # collect responses (EC = 0-3,6-10) from commandport
        self.queue_command_responses = Queue.Queue(maxsize=0)
        # save messages (EC = 4,5) from commandport
        self.queue_img = Queue.Queue(maxsize=0)

        self.should_listen = True  # used in readCommandResponse thread
        self._waitForCorrectResponse = True  # used in sendCommand

        if children:
            try:
                kwargs = children["readoutcam"]
            except Exception:
                raise
            self._readoutcam = ReadoutCamera(parent=self, spectrograph=children.get("spectrograph"),
                                             daemon=daemon, **kwargs)
            self.children.value.add(self._readoutcam)  # add readoutcam to children-VA
            try:
                kwargs = children["streakunit"]
            except Exception:
                raise
            self._streakunit = StreakUnit(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._streakunit)  # add streakunit to children-VA
            try:
                kwargs = children["delaybox"]
            except Exception:
                raise
            self._delaybox = DelayGenerator(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._delaybox)  # add delaybox to children-VA

    def terminate(self):
        """
        Close App (HPDTA) and RemoteEx and close connection to RemoteEx. Called by backend.
        """
        # terminate children
        for child in self.children.value:
            child.terminate()

        self.should_listen = False  # terminates receiver thread

    def StartAcquisition(self, AcqMode):
        """Start an acquisition.
        """
        self._readoutcam._generate()


class SimpleStreakCameraDataFlow(model.DataFlow):
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

