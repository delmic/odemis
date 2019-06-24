# -*- coding: utf-8 -*-
'''
Created on 19 Jun 2014

@author: Éric Piel, Iheb Zaabouti

Copyright © 2014 Éric Piel, Kimon Tsitsikas, Delmic

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
from __future__ import division

import queue
import logging
import numpy
from odemis import model, util, dataio
from odemis.model import oneway
import os
from scipy import ndimage
import time
from PIL import Image, ImageDraw, ImageFont


class Camera(model.DigitalCamera):
    '''
    This represent a fake digital camera, which generates as data the image
    given at initialisation.
    '''

    def __init__(self, name, role, image, dependencies=None, daemon=None, blur_factor=1e4, resolution=None, **kwargs):
        '''
        dependencies (dict string->Component): If "focus" is passed, and it's an
            actuator with a z axis, the image will be blurred based on the
            position, to simulate a focus axis.
        image (str or None): path to a file to use as fake image (relative to
         the directory of this class)
        '''

        # TODO: support transpose? If not, warn that it's not accepted
        # fake image setup
        image = unicode(image)
        # ensure relative path is from this file
        if not os.path.isabs(image):
            image = os.path.join(os.path.dirname(__file__), image)
        converter = dataio.find_fittest_converter(image, mode=os.O_RDONLY)
        self._imgs = converter.read_data(image)      # can be RGB or greyscale
        model.DigitalCamera.__init__(self, name, role, dependencies=dependencies, daemon=daemon, **kwargs)

        for i, img in enumerate(self._imgs):
            if img.ndim > 3:  # remove dims of length 1
                self._imgs[i] = numpy.squeeze(img)
            imshp = img.shape
            if len(imshp) == 3 and imshp[i] in {3, 4}:
                # CYX, change it to YXC, to simulate a RGB detector
                self._imgs[i] = util.img.ensureYXC(img)

        for img in self._imgs[1:]:
            if self._imgs[0].shape != self._imgs[i].shape:
                raise ValueError("all images must have the same resolution")
        imshp = self._imgs[0].shape

        # For RGB, the colour is last dim, but we still indicate it as higher
        # dimension to ensure shape always starts with X, Y
        if len(imshp) == 3 and imshp[-1] in {3, 4}:
            # resolution doesn't affect RGB dim
            if resolution:
                if resolution >= imshp[-2::-1]:
                    res = tuple(resolution)
            else:
                res = imshp[-2::-1]
            self._shape = res + imshp[-1::] # X, Y, C
        else:
            if resolution:
                res = tuple(resolution)
            else:
                res = imshp[::-1]

            self._shape = res # X, Y,...
        # TODO: handle non integer dtypes
        depth = 2 ** (self._imgs[0].dtype.itemsize * 8)
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
        self.translation = model.ResolutionVA(self._translation, tran_rng,
                                              cls=(int, long), unit="px",
                                              setter=self._setTranslation)

        exp = self._imgs[0].metadata.get(model.MD_EXP_TIME, 0.1) # s
        self.exposureTime = model.FloatContinuous(exp, (1e-3, 1e3), unit="s")
        # Some code care about the readout rate to know how long an acquisition will take
        self.readoutRate = model.FloatVA(1e9, unit="Hz", readonly=True)

        pxs = self._imgs[0].metadata.get(model.MD_PIXEL_SIZE, (10e-6, 10e-6))
        mag = self._imgs[0].metadata.get(model.MD_LENS_MAG, 1)
        spxs = tuple(s * mag for s in pxs)
        self.pixelSize = model.VigilantAttribute(spxs, unit="m", readonly=True)

        self._metadata = {model.MD_HW_NAME: "FakeCam",
                          model.MD_SENSOR_PIXEL_SIZE: spxs,
                          model.MD_DET_TYPE: model.MD_DT_INTEGRATING,
                          model.MD_PIXEL_SIZE: pxs}

        # Set the amount of blurring during defocusing.
        self._blur_factor = float(blur_factor)

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

        try:
            stage = dependencies["stage"]
            if (not isinstance(stage, model.ComponentBase) or
                not hasattr(stage, "axes") or not isinstance(stage.axes, dict)
               ):
                raise ValueError("stage %s must be a Actuator with a 'z' axis", stage)
            self._stage = stage
            if resolution == None:
                raise ValueError("resolution is %s", resolution)
            # the position of the center of the image
            self._orig_stage_pos = self._stage.position.value["x"], self._stage.position.value["y"]
            logging.debug("Simulating stage at %s m", self._orig_stage_pos)
        except (TypeError, KeyError):
            logging.info("Will not simulate stage")
            self._stage = None

        # Simple implementation of the flow: we keep generating images and if
        # there are subscribers, they'll receive it.
        self.data = SimpleDataFlow(self)
        self._generator = None
        # Convenience event for the user to connect and fire
        self.softwareTrigger = model.Event()

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
        self._stop_generate()

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
        gen_img = self._simulate()
        self.data._waitSync()
        if self.data._sync_event:
            # If sync event, we need to simulate period after event (not efficient, but works)
            time.sleep(self.exposureTime.value)

        metadata = gen_img.metadata.copy()  # MD of image
        metadata.update(self._metadata)  # MD of camera

        # write text with polarization position on image
        if model.MD_POL_MODE in self._metadata:
            txt = self._metadata[model.MD_POL_MODE]
            gen_img = self._write_txt_image(gen_img, txt)

        # update fake output metadata
        exp = timer.period
        metadata[model.MD_ACQ_DATE] = time.time() - exp
        metadata[model.MD_EXP_TIME] = exp
        logging.debug("Generating new fake image of shape %s", gen_img.shape)

        if self._focus:
            # apply the defocus
            pos = self._focus.position.value['z']
            dist = abs(pos - self._metadata[model.MD_FAV_POS_ACTIVE]["z"]) * self._blur_factor
            logging.debug("Focus dist = %g", dist)
            img = ndimage.gaussian_filter(gen_img, sigma=dist)
        else:
            img = gen_img
        if self._stage:

            pos = self._stage.position.value["x"], self._stage.position.value["y"]
            dista = abs(numpy.array(pos) - numpy.array(self._orig_stage_pos)) * 1e4

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
        fnt = ImageFont.truetype('/Library/Fonts/Arial.ttf', fnt_size)
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
        pxs_pos = self.translation.value

        #Shift the image based on the stage position
        if self._stage:
            pos = self._stage.position.value["x"], self._stage.position.value["y"]

            phys_shift = self._orig_stage_pos[0] - pos[0], self._orig_stage_pos[1] - pos[1]
            phys_shift = tuple(phys_shift)

            # #convert to pxl
            pxs = self._metadata[model.MD_PIXEL_SIZE]
            px_shift = phys_shift[0] / pxs[0], phys_shift[1] / pxs[1]

            npos=pxs_pos[0]+px_shift[0], pxs_pos[1]+px_shift[1]
            pxs_pos=tuple(npos)
            logging.debug('the position is %s',pxs_pos)
        # Pick the image based on the current excitation wl
        def dist_wl(img):
            try:
                current_inwl = self.getMetadata()[model.MD_IN_WL]   # (350e-9, 360e-9)
                current_inwl_c = util.fluo.get_center(current_inwl) # 355e-9
                c = util.fluo.get_center(img.metadata[model.MD_IN_WL])
                return abs(current_inwl_c - c)
            except Exception:
                return float('inf')

        img = min(self._imgs, key=dist_wl)
        shape = img.shape

        center = (shape[1] / 2, shape[0] / 2)

        lt = (-center[0] - pxs_pos[0] - (res[0] / 2) * binning[0],
              center[1] + pxs_pos[1] - (res[1] / 2) * binning[1])

        # assert(lt[0] >= 0 and lt[1] >= 0)
        # compute each row and column that will be included
        # TODO: Could use something more hardwarish like that:
        # data0 = data0.reshape(shape[0]//b0, b0, shape[1]//b1, b1).mean(3).mean(1)
        # (or use sum, to simulate binning)
        # Alternatively, it could use just [lt:lt+res:binning]
        coord = ([int(round(lt[0] + i * binning[0])) % res[0] for i in range(res[0])],
                 [int(round(lt[1] + i * binning[1])) % res[1] for i in range(res[1])])
        sim_img = img[numpy.ix_(coord[1], coord[0])]
        return sim_img

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
        """
        if self._sync_event:
            self._evtq.get()
