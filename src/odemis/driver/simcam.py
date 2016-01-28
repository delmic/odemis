# -*- coding: utf-8 -*-
'''
Created on 19 Jun 2014

@author: Éric Piel

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

import Queue
import logging
import numpy
from odemis import model, util, dataio
from odemis.model import isasync, oneway
import os
from scipy import ndimage
import time


class Camera(model.DigitalCamera):
    '''
    This represent a fake digital camera, which generates as data the image
    given at initialisation.
    '''

    def __init__(self, name, role, image, children=None, daemon=None, **kwargs):
        '''
        children (dict string->kwargs): parameters setting for the children.
            The only possible child is "focus".
            They will be provided back in the .children VA
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
        self._img = converter.read_data(image)[0]  # can be RGB or greyscale

        # we will fill the set of children with Components later in ._children
        model.DigitalCamera.__init__(self, name, role, daemon=daemon, **kwargs)

        if self._img.ndim > 3:  # remove dims of length 1
            self._img = numpy.squeeze(self._img)

        imshp = self._img.shape
        if len(imshp) == 3 and imshp[0] in {3, 4}:
            # CYX, change it to YXC, to simulate a RGB detector
            self._img = numpy.rollaxis(self._img, 2) # XCY
            self._img = numpy.rollaxis(self._img, 2) # YXC
            imshp = self._img.shape

        # For RGB, the colour is last dim, but we still indicate it as higher
        # dimension to ensure shape always starts with X, Y
        if len(imshp) == 3 and imshp[-1] in {3, 4}:
            # resolution doesn't affect RGB dim
            res = imshp[-2::-1]
            self._shape = res + imshp[-1::] # X, Y, C
            # indicate it's RGB pixel-per-pixel ordered
            self._img.metadata[model.MD_DIMS] = "YXC"
        else:
            res = imshp[::-1]
            self._shape = res # X, Y,...
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
        self.translation = model.ResolutionVA(self._translation, tran_rng,
                                              cls=(int, long), unit="px",
                                              setter=self._setTranslation)

        exp = self._img.metadata.get(model.MD_EXP_TIME, 0.1) # s
        self.exposureTime = model.FloatContinuous(exp, (1e-3, 1e3), unit="s")
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
            kwargs = children["focus"]
        except (KeyError, TypeError):
            logging.info("Will not simulate focus")
            self._focus = None
        else:
            self._focus = CamFocus(parent=self, daemon=daemon, **kwargs)
            self.children.value = self.children.value | {self._focus}

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
        max_res = self.resolution.range[1]
        new_res = (min(new_res[0], max_res[0]),
                   min(new_res[1], max_res[1]))
        self.resolution.value = new_res
        return self._binning

    def _setResolution(self, value):
        """
        value (2-tuple int)
        Called when "resolution" VA is modified. It actually modifies the camera resolution.
        """
        self._resolution = value
        if not self.translation.readonly:
            self.translation.value = self.translation.value  # force re-check
        return value

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
        gen_img = self._simulate()
        timer = self._generator  # might be replaced by None afterwards, so keep a copy
        self.data._waitSync()
        if self.data._sync_event:
            # If sync event, we need to simulate period after event (not efficient, but works)
            time.sleep(self.exposureTime.value)

        metadata = gen_img.metadata.copy()
        metadata.update(self._metadata)

        # update fake output metadata
        exp = timer.period
        metadata[model.MD_ACQ_DATE] = time.time() - exp
        metadata[model.MD_EXP_TIME] = exp
        logging.debug("Generating new fake image of shape %s", gen_img.shape)
        if self._focus:
            # apply the defocus
            pos = self._focus.position.value['z']
            dist = abs(pos - self._focus._good_focus) * 1e4
            img = ndimage.gaussian_filter(gen_img, sigma=dist)
        else:
            img = gen_img

        img = model.DataArray(img, metadata)

        # send the new image (if anyone is interested)
        self.data.notify(img)

        # simulate exposure time
        timer.period = self.exposureTime.value

    def _simulate(self):
        """
        Processes the fake image based on the translation, resolution and
        current drift.
        """
        binning = self.binning.value
        res = self.resolution.value
        pxs_pos = self.translation.value
        shape = self._img.shape
        center = (shape[1] / 2, shape[0] / 2)
        lt = (center[0] + pxs_pos[0] - (res[0] / 2) * binning[0],
              center[1] + pxs_pos[1] - (res[1] / 2) * binning[1])
        assert(lt[0] >= 0 and lt[1] >= 0)
        # compute each row and column that will be included
        # TODO: Could use something more hardwarish like that:
        # data0 = data0.reshape(shape[0]//b0, b0, shape[1]//b1, b1).mean(3).mean(1)
        coord = ([int(round(lt[0] + i * binning[0])) for i in range(res[0])],
                 [int(round(lt[1] + i * binning[1])) for i in range(res[1])])
        sim_img = self._img[numpy.ix_(coord[1], coord[0])]  # copy
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
            self._evtq = Queue.Queue()  # to be sure it's empty

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


class CamFocus(model.Actuator):
    """
    Simulated focus component.
    Just pretends to be able to move Z (instantaneously).
    """
    # Duplicate of simsem.EbeamFocus
    def __init__(self, name, role, **kwargs):
        self._good_focus = 0.006
        axes_def = {"z": model.Axis(unit="m", range=(-0.01, 0.01))}
        self._position = {"z": self._good_focus}

        model.Actuator.__init__(self, name, role, axes=axes_def, **kwargs)

        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute(
                                    self._applyInversion(self._position),
                                    unit="m", readonly=True)

    def _updatePosition(self):
        """
        update the position VA
        """
        # it's read-only, so we change it via _value
        self.position._value = self._applyInversion(self._position)
        self.position.notify(self.position.value)

    @isasync
    def moveRel(self, shift):
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        shift = self._applyInversion(shift)

        for axis, change in shift.items():
            self._position[axis] += change
            rng = self.axes[axis].range
            if not rng[0] < self._position[axis] < rng[1]:
                logging.warning("moving axis %s to %f, outside of range %r",
                                axis, self._position[axis], rng)
            else:
                logging.info("moving axis %s to %f", axis, self._position[axis])

        self._updatePosition()
        return model.InstantaneousFuture()

    @isasync
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversion(pos)

        for axis, new_pos in pos.items():
            self._position[axis] = new_pos
            logging.info("moving axis %s to %f", axis, self._position[axis])

        self._updatePosition()
        return model.InstantaneousFuture()

    def stop(self, axes=None):
        logging.warning("Stopping z axis")
