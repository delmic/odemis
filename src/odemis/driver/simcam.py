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

from odemis.model import isasync
import logging
import math
import numpy
from odemis import model, util, dataio
from odemis.util import img
import os.path
from scipy import ndimage
import threading
import time
import weakref


class Camera(model.DigitalCamera):
    '''
    This represent a fake digital camera, which generates as data the image
    given at initialisation.
    Very simple implementation: it doesn't support cropping/binning/translation or any
    settings but exposureTime.
    '''

    def __init__(self, name, role, children, image=None, daemon=None, **kwargs):
        '''
        children (dict string->kwargs): parameters setting for the children.
            The only possible child is "focus".
            They will be provided back in the .children roattribute
        image (str or None): path to a file to use as fake image (relative to
         the directory of this class)
        '''
        # fake image setup
        image = unicode(image)
        # change to this directory to ensure relative path is from this file
        os.chdir(os.path.dirname(unicode(__file__)))
        exporter = dataio.find_fittest_exporter(image)
        self._img = exporter.read_data(image)[0] # can be RGB or greyscale          
        imshp = self._img.shape
        
        # If RGB with colour as last dim, still indicate it as higher dimension
        # to ensure shape always starts with X, Y
        # TODO: review if it makes sense, and how can we handle this case more 
        # gratefully in Odemis (-> format metadata?)
        if len(imshp) >= 3 and imshp[-1] in {3, 4}:
            self._shape = imshp[-2::-1] + imshp[-1::] # X, Y..., C
        else:
            self._shape = imshp[::-1] # X, Y,...
        # if 
        # TODO: handle non integer dtypes
        depth = 2 ** (self._img.dtype.itemsize * 8) - 1
        self._shape += (depth,)
        
        # we will fill the set of children with Components later in ._children
        model.DigitalCamera.__init__(self, name, role, daemon=daemon, **kwargs)
        
        # TODO: don't provide range? or don't make it readonly?
        res = self._shape[:-1]
        self.resolution = model.ResolutionVA(res, [res, res], readonly=True)

        exp = 0.1 # s
        self.exposureTime = model.FloatContinuous(exp, [1e-3, 1e3], unit="s")

        pxs = self._img.metadata.get(model.MD_PIXEL_SIZE, 10e-6)
        self.pixelSize = model.VigilantAttribute(pxs, unit="m", readonly=True)
        
        # Simple implementation of the flow: we keep generating images and if
        # there are subscribers, they'll receive it.
        self.data = DataFlow(self)
        self._generator = util.RepeatingTimer(exp, self._generate,
                                              "SimCam image generator")
        self._generator.start()
        
        self._metadata = {model.MD_HW_NAME: "FakeCam",
                          model.MD_SENSOR_PIXEL_SIZE: pxs}

        try:
            kwargs = children["focus"]
        except (KeyError, TypeError):
            logging.info("Will not simulate focus")
            self._focus = None
        else:
            self._focus = CamFocus(parent=self, daemon=daemon, **kwargs)
            self.children.add(self._focus)

    def updateMetadata(self, md):
        self._metadata.update(md)

    def terminate(self):
        """
        Must be called at the end of the usage. Can be called multiple times,
        but the component shouldn't be used afterwards.
        """
        self._generator.cancel()

    def _simulate_image(self):
        """
        Generates the fake output based on the translation, resolution and
        current drift.
        """
            metadata = dict(self._img.metadata)
            metadata.update(self._metadata)
            
            # update fake output metadata
            exp = self._generator.period
            metadata[model.MD_ACQ_DATE] = time.time() - exp
            metadata[model.MD_EXP_TIME] = exp

            img = model.DataArray(self._img, metadata)

            if self._focus:
                # apply the defocus
                pos = self._focus.position.value['z']
                dist = abs(pos) * 1e4
                img = ndimage.gaussian_filter(img, sigma=dist)

            # send the new image (if anyone is interested)            
            self.data.notify(d)
            
            # simulate exposure time
            self._generator.period = self.exposureTime.value
    
class CamFocus(model.Actuator):
    """
    Simulated focus component.
    Just pretends to be able to move Z (instantaneously).
    """
    # Duplicate of simsem.EbeamFocus
    def __init__(self, name, role, **kwargs):
        axes_def = {"z": model.Axis(unit="m", range=[-0.3, 0.3])}
        self._position = {"z": 0}

        model.Actuator.__init__(self, name, role, axes=axes_def, **kwargs)

        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute(
                                    self._applyInversionAbs(self._position),
                                    unit="m", readonly=True)

    def _updatePosition(self):
        """
        update the position VA
        """
        # it's read-only, so we change it via _value
        self.position._value = self._applyInversionAbs(self._position)
        self.position.notify(self.position.value)

    @isasync
    def moveRel(self, shift):
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        shift = self._applyInversionRel(shift)

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
        pos = self._applyInversionAbs(pos)

        for axis, new_pos in pos.items():
            self._position[axis] = new_pos
            logging.info("moving axis %s to %f", axis, self._position[axis])

        self._updatePosition()
        return model.InstantaneousFuture()

    def stop(self, axes=None):
        logging.warning("Stopping z axis")
