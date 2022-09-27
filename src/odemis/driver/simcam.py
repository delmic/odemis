# -*- coding: utf-8 -*-
"""
Created on 19 Jun 2014

@author: Éric Piel

Copyright © 2014-2022 Éric Piel, Kimon Tsitsikas, Delmic

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
import math
import threading
from past.builtins import long
from builtins import str
import queue
import logging
import numpy
from odemis import model, util, dataio
from odemis.model import oneway
import os
from scipy import ndimage
import time
from PIL import Image, ImageDraw, ImageFont

ERROR_STATE_FILE = "simcam-hw.error"


class Camera(model.DigitalCamera):
    '''
    This represent a fake digital camera, which generates as data the image
    given at initialisation.
    '''

    def __init__(self, name, role, image, dependencies=None, daemon=None, max_res=None, **kwargs):
        """
        dependencies (dict string->Component): If "focus" is passed, and it's an
            actuator with a z axis, the image will be blurred based on the
            position, to simulate a focus axis.
        image (str or None): path to a file to use as fake image (relative to the directory of this class)
        max_res (tuple of (int, int) or None): maximum resolution to clip simulated image, if None whole image shape
            will be used. The simulated image will be a part of the original image based on the MD_POS metadata.
        """
        # TODO: support transpose? If not, warn that it's not accepted
        # fake image setup
        image = str(image)
        # ensure relative path is from this file
        if not os.path.isabs(image):
            image = os.path.join(os.path.dirname(__file__), image)
        converter = dataio.find_fittest_converter(image, mode=os.O_RDONLY)
        self._img = converter.read_data(image)[0]  # can be RGB or greyscale

        model.DigitalCamera.__init__(self, name, role, dependencies=dependencies, daemon=daemon, **kwargs)
        
        # remove metadata which would not be on real hardware 
        self._img.metadata.pop(model.MD_DESCRIPTION, None)
        
        if self._img.ndim > 3:  # remove dims of length 1
            self._img = numpy.squeeze(self._img)

        imshp = self._img.shape
        if len(imshp) == 3 and imshp[0] in {3, 4}:
            # CYX, change it to YXC, to simulate a RGB detector
            self._img = numpy.rollaxis(self._img, 2) # XCY
            self._img = numpy.rollaxis(self._img, 2) # YXC
            imshp = self._img.shape

        def clip_max_res(img_res):
            if len(max_res) != 2:
                raise ValueError("Shape of max_res should be = 2.")
            return tuple(min(x, y) for x, y in zip(img_res, max_res))  # in case max_res > image shape

        # For RGB, the colour is last dim, but we still indicate it as higher
        # dimension to ensure shape always starts with X, Y
        if len(imshp) == 3 and imshp[-1] in {3, 4}:
            # resolution doesn't affect RGB dim
            res = imshp[-2::-1]
            self._img_res = res  # Original image shape in case it's clipped
            if max_res:
                res = clip_max_res(res)
            self._shape = res + imshp[-1::]  # X, Y, C
            # indicate it's RGB pixel-per-pixel ordered
            self._img.metadata[model.MD_DIMS] = "YXC"
        else:
            self._img_res = imshp[::-1]  # Original image shape in case it's clipped
            res = imshp[::-1] if max_res is None else tuple(max_res)
            if max_res:
                res = clip_max_res(res)
            self._shape = res  # X, Y,...
        # TODO: handle non integer dtypes
        depth = 2 ** (self._img.dtype.itemsize * 8)
        self._shape += (depth,)

        self._resolution = res
        self.resolution = model.ResolutionVA(self._resolution,
                              ((1, 1),
                               self._resolution), setter=self._setResolution)

        self._binning = (1, 1)
        self.binning = model.ResolutionVA(self._binning,
                              ((1, 1), (16, 16)), setter=self._setBinning)

        hlf_shape = (self._shape[0] // 2 - 1, self._shape[1] // 2 - 1)
        tran_rng = [(-hlf_shape[0], -hlf_shape[1]),
                    (hlf_shape[0], hlf_shape[1])]
        self._translation = (0, 0)
        self.translation = model.ResolutionVA(self._translation, tran_rng, unit="px",
                                              cls=(int, long), setter=self._setTranslation)

        self._orig_exp = self._img.metadata.get(model.MD_EXP_TIME, 0.1)  # s
        self.exposureTime = model.FloatContinuous(self._orig_exp, range=(1e-3, max(10, self._orig_exp * 2)), unit="s")

        # Some code care about the readout rate to know how long an acquisition will take
        self.readoutRate = model.FloatVA(1e9, unit="Hz", readonly=True)

        pxs = self._img.metadata.get(model.MD_PIXEL_SIZE, (10e-6, 10e-6))
        mag = self._img.metadata.get(model.MD_LENS_MAG, 1)
        spxs = tuple(s * mag for s in pxs)
        self.pixelSize = model.VigilantAttribute(spxs, unit="m", readonly=True)

        self._metadata = {model.MD_HW_NAME: "FakeCam",
                          model.MD_SENSOR_PIXEL_SIZE: spxs,
                          model.MD_DET_TYPE: model.MD_DT_INTEGRATING}

        try:
            focuser = dependencies["focus"]
            if (not isinstance(focuser, model.ComponentBase) or
                not hasattr(focuser, "axes") or not isinstance(focuser.axes, dict) or
                "z" not in focuser.axes
               ):
                raise ValueError("focus %s must be a Actuator with a 'z' axis" % (focuser,))
            self._focus = focuser

            # The "good" focus is at the current position
            self._good_focus = self._focus.position.value["z"]
            self._metadata[model.MD_FAV_POS_ACTIVE] = {"z": self._good_focus}
            logging.debug("Simulating focus, with good focus at %g m", self._good_focus)
        except (TypeError, KeyError):
            logging.info("Will not simulate focus")
            self._focus = None

        # Simple implementation of the flow: we keep generating images and if
        # there are subscribers, they'll receive it.
        self.data = SimpleDataFlow(self)
        self._generator = None
        # Convenience event for the user to connect and fire
        self.softwareTrigger = model.Event()
        self._last_acq_time = 0  # Time of latest acquisition

        # Include a thread which creates or fixes an hardware error in the simcam on the basis of the presence of the
        # file ERROR_STATE_FILE in model.BASE_DIRECTORY
        self._is_running = True
        self._error_creation_thread = threading.Thread(target=self._state_error_run,
                                                       name="Creating and state error")
        self._error_creation_thread.daemon = True
        self._error_creation_thread.start()

    def _setBinning(self, value):
        """
        value (2-tuple int)
        Called when "binning" VA is modified. It actually modifies the camera binning.
        """
        prev_binning, self._binning = self._binning, value

        # adapt resolution so that the AOI stays the same
        change = (prev_binning[0] / self._binning[0],
                  prev_binning[1] / self._binning[1])
        old_resolution = self.resolution.value
        new_res = (int(round(old_resolution[0] * change[0])),
                   int(round(old_resolution[1] * change[1])))

        # fit
        self.resolution.value = self.resolution.clip(new_res)

        self._metadata[model.MD_BINNING] = self._binning
        return self._binning

    def _setResolution(self, value):
        """
        value (2-tuple int)
        Called when "resolution" VA is modified. It actually modifies the camera resolution.
        """
        max_res = (self._shape[0] // self._binning[0],
                   self._shape[1] // self._binning[1])

        self._resolution = (max(1, min(value[0], max_res[0])),
                            max(1, min(value[1], max_res[1])))

        if not self.translation.readonly:
            self.translation.value = self.translation.value  # force re-check
        return self._resolution

    def _setTranslation(self, value):
        """
        value (2-tuple int)
        Called when "translation" VA is modified. It actually modifies the camera translation.
        """
        # compute the min/max of the shift. It's the same as the margin between
        # the centered ROI and the border, taking into account the binning.
        max_tran = ((self._shape[0] - self._resolution[0] * self._binning[0]) // 2,
                    (self._shape[1] - self._resolution[1] * self._binning[1]) // 2)

        # between -margin and +margin
        trans = (max(-max_tran[0], min(value[0], max_tran[0])),
                 max(-max_tran[1], min(value[1], max_tran[1])))
        self._translation = trans
        return trans

    def terminate(self):
        """
        Must be called at the end of the usage. Can be called multiple times,
        but the component shouldn't be used afterwards.
        """
        self._is_running = False #Stop error creation thread
        self._stop_generate()

        super(Camera, self).terminate()

    def _start_generate(self):
        if self._generator is not None:
            logging.warning("Generator already running")
            return
        self._generator = util.RepeatingTimer(self.exposureTime.value,
                                              self._generate,
                                              "SimCam image generator")
        self._generator.start()

    def _stop_generate(self):
        if self._generator is not None:
            self._generator.cancel()
            self._generator = None

    def _generate(self):
        """
        Generates the fake output based on the translation, resolution and
        current drift.
        """
        timer = self._generator  # might be replaced by None afterwards, so keep a copy
        event_t = self.data._waitSync()
        exp = self.exposureTime.value
        if event_t is not None:
            # If sync event, we need to simulate period after event
            # If several events were sent before the end of the acquisition, then
            # it's the acquisition time that counts.
            end_acq_time = max(self._last_acq_time, event_t) + exp
            extra_time = max(0, end_acq_time - time.time())
            logging.debug("Sleeping extra %g s, for simulating event", extra_time)
            time.sleep(extra_time)

        gen_img = self._simulate()
        metadata = gen_img.metadata.copy()  # MD of image
        metadata.update(self._metadata)  # MD of camera

        # write text with polarization position on image
        if model.MD_POL_MODE in self._metadata:
            txt = self._metadata[model.MD_POL_MODE]
            gen_img = self._write_txt_image(gen_img, txt)

        # update fake output metadata
        self._last_acq_time = time.time() - exp
        metadata[model.MD_ACQ_DATE] = self._last_acq_time
        metadata[model.MD_EXP_TIME] = exp
        logging.debug("Generating new fake image of shape %s", gen_img.shape)

        if self._focus:
            # apply the defocus
            pos = self._focus.position.value['z']
            # max blur of 30 pixels, else image generation takes too long
            dist = min(math.sqrt(abs(pos - self._metadata[model.MD_FAV_POS_ACTIVE]["z"]) / self.depthOfField.value), 30)
            logging.debug("Focus blur = %g", dist)
            img = ndimage.gaussian_filter(gen_img, sigma=dist)
        else:
            img = gen_img
        # to simulate changing the exposure time exp/self._orig_exp
        numpy.multiply(img, exp/self._orig_exp, out=img, casting="unsafe")

        img = model.DataArray(img, metadata)

        # send the new image (if anyone is interested)
        self.data.notify(img)

        # simulate exposure time
        timer.period = self.exposureTime.value

    def _write_txt_image(self, image, txt):
        """write polarization position as text into image for simulation
        :return: image with polarization pos text"""

        shape = image.shape
        binning = self.binning.value
        fnt_size = int(80/binning[0])
        fnt = ImageFont.truetype('FreeSans.ttf', fnt_size)
        # create txt image for overlay
        im_txt = Image.new('F', shape[::-1], 0)
        d = ImageDraw.Draw(im_txt)
        pol_pos = txt
        d.text((shape[1] // 3, shape[0] // 2), pol_pos, font=fnt, fill=255)
        txt_array = numpy.asarray(im_txt)
        image[txt_array == 255] = image.max()

        return image

    def set_image(self, new_img):
        """
        Warning : Used only for unit tests
        Args:
            new_img: a new image with the light on
        """
        self._img = new_img

    def _simulate(self):
        """
        Processes the fake image based on the translation, resolution and
        current drift.
        """
        binning = self.binning.value
        res = self.resolution.value
        trans = self.translation.value
        center = self._img_res[0] / 2, self._img_res[1] / 2

        # Extra translation to simulate stage movement
        pos = self._metadata.get(model.MD_POS, (0, 0))
        pixel_size = self._metadata.get(model.MD_PIXEL_SIZE, self.pixelSize.value)
        pxs = [p / b for p, b in zip(pixel_size, self.binning.value)]
        stage_shift = pos[0] / pxs[0], -pos[1] / pxs[1]  # Y goes opposite

        # First and last index (eg, 0 -> 255)
        ltrb = [center[0] + trans[0] + stage_shift[0] - (res[0] / 2) * binning[0],
                center[1] + trans[1] + stage_shift[1] - (res[1] / 2) * binning[1],
                center[0] + trans[0] + stage_shift[0] + ((res[0] / 2) - 1) * binning[0],
                center[1] + trans[1] + stage_shift[1] + ((res[1] / 2) - 1) * binning[1]
                ]
        # If the shift caused the image to go out of bounds, limit it
        if ltrb[0] < 0:
            ltrb[0] = 0
        elif ltrb[2] > self._img_res[0] - 1:
            ltrb[0] -= ltrb[2] - (self._img_res[0] - 1)
        if ltrb[1] < 0:
            ltrb[1] = 0
        elif ltrb[3] > self._img_res[1] - 1:
            ltrb[1] -= ltrb[3] - (self._img_res[1] - 1)

        ltrb = [round(v) for v in ltrb]  # Smooth out floating point errors

        if not (ltrb[0] >= 0 and ltrb[1] >= 0):
            raise IndexError(f"Unexpected range {ltrb} with {center}, {trans}, {stage_shift}, {binning} for res {self._img_res}")

        # compute each row and column that will be included
        # TODO: Could use something more hardwarish like that:
        # data0 = data0.reshape(shape[0]//b0, b0, shape[1]//b1, b1).mean(3).mean(1)
        # (or use sum, to simulate binning)
        # Alternatively, it could use just [lt:lt+res:binning]
        coord = ([int(round(ltrb[0] + i * binning[0])) for i in range(res[0])],
                 [int(round(ltrb[1] + i * binning[1])) for i in range(res[1])])
        sim_img = self._img[numpy.ix_(coord[1], coord[0])]  # copy

        # Add some noise
        mx = self._img.max()
        sim_img += numpy.random.randint(0, max(mx // 100, 10), sim_img.shape, dtype=sim_img.dtype)
        # Clip, but faster than clip() on big array.
        # There can still be some overflow, but let's just consider this "strong noise"
        sim_img[sim_img > mx] = mx

        return sim_img

    def _state_error_run(self):
        '''
        Creates or fixes an hardware error in the simcam if an file is present.
        Creating the file 'ERROR_STATE_FILE' (or by deleting this file) in the folder 'model.BASE_DIRECTORY' the
        state of the simcam is changed to and error (or fixed by putting the state back to the standard 'running').
        '''
        try:
            while self._is_running:
                time.sleep(0.3)  # max 3 Hz

                # Check if an error file is present
                error_file_present = os.path.isfile(os.path.join(model.BASE_DIRECTORY, ERROR_STATE_FILE))
                if error_file_present and not isinstance(self.state.value, model.HwError):
                    self.state._set_value(
                            model.HwError("Camera disconnected due to forced testing error."),
                            force_write=True)

                elif not error_file_present and isinstance(self.state.value, model.HwError):
                    self.state._set_value(model.ST_RUNNING, force_write=True)

        except Exception as e:
            logging.warning("In changing states in SimCam the error '%s' occurred", e)


class SimpleDataFlow(model.DataFlow):
    def __init__(self, ccd):
        super(SimpleDataFlow, self).__init__()
        self._ccd = ccd
        self._sync_event = None
        self._evtq = None  # a Queue to store received events (= float, time of the event)

    def start_generate(self):
        self._ccd._start_generate()

    def stop_generate(self):
        self._ccd._stop_generate()
        if self._sync_event:
            self._evtq.put(None)  # in case it was waiting for an event

    def synchronizedOn(self, event):
        if self._sync_event == event:
            return
        if self._sync_event:
            self._sync_event.unsubscribe(self)
            if not event:
                self._evtq.put(None)  # in case it was waiting for this event

        self._sync_event = event
        if self._sync_event:
            event.subscribe(self)
            self._evtq = queue.Queue()  # to be sure it's empty

    @oneway
    def onEvent(self):
        """
        Called by the Event when it is triggered
        """
        if not self._evtq.empty():
            logging.warning("Received synchronization event but already %d queued",
                            self._evtq.qsize())

        self._evtq.put(time.time())

    def _waitSync(self):
        """
        Block until the Event on which the dataflow is synchronised has been
          received. If the DataFlow is not synchronised on any event, this
          method immediatly returns
        return (float or None): if an event happened, it's the time the event was
          sent. Otherwise, it returns None.
        """
        if self._sync_event:
            return self._evtq.get()

        return None
