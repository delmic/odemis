#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Created on 16 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Delmic Acquisition Software.

Delmic Acquisition Software is free software: you can redistribute it and/or
modify it under the terms of the GNU General Public License as published by the
Free Software Foundation, either version 2 of the License, or (at your option)
any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
details.

You should have received a copy of the GNU General Public License along with
Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.

"""

import logging
from odemis import model
import numpy
import wx

from odemis.model import VigilantAttribute

class SECOMModel(object):
    """
    Represent the data of a SECOM microscope
    This is the main Model, from a Model/View/Controller perspective
    """

    def __init__(self):
        self.stage_pos = VigilantAttribute((0, 0)) # m,m
        self.stage_pos.subscribe(self.avOnStagePos)

        # FIXME: maybe could go into (sub)classes like OpticalEmitter, SEDetector...
        self.optical_emt_wavelength = VigilantAttribute(450) # nm XXX a range?
        self.optical_det_wavelength = VigilantAttribute(568) # nm
        self.optical_det_exposure_time = VigilantAttribute(0.5) # s
        self.optical_det_image = VigilantAttribute(InstrumentalImage(None, None, None))

        self.sem_emt_dwell_time = VigilantAttribute(0.00001) #s
        self.sem_emt_spot = VigilantAttribute(4) # no unit (could be m²)
        self.sem_emt_hv = VigilantAttribute(30000) # V
        self.sem_det_image = VigilantAttribute(InstrumentalImage(None, None, None))

    def avOnStagePos(self, val):
        logging.info("requested to move stage to pos: %s", str(val))

class OpticalBackendConnected(SECOMModel):
    """
    A class representing a SECOM microscope based on a model.Microscope instance
    without any SEM.
    It's a very simple version which always acquires from the camera.
    """
    def __init__(self, microscope):
        """
        microscope (model.Microscope): a microscope component on which the interface
         will be based.
        """
        SECOMModel.__init__(self)
        # Find the camera: detector of type DigitalCamera
        self.camera = None
        for d in microscope.detectors:
            if d.role == "ccd":
                self.camera = d
                break
        if not self.camera:
            raise Exception("no camera found in the microscope")

        # Find the stage: actuator with role "stage"
        self.stage = None
        for a in microscope.actuators:
            if a.role == "stage":
                self.stage = a
                break
        if not self.stage:
            raise Exception("no stage found in the microscope")


        try:
            self.prev_pos = (self.stage.position["x"], self.stage.position["y"])
        except (KeyError, AttributeError):
            self.prev_pos = (0,0)
        # override
        self.stage_pos = VigilantAttribute(self.prev_pos) # (m,m) => (X,Y)
        self.stage_pos.subscribe(self.avOnStagePos)

        # direct linking
        self.optical_det_exposure_time = self.camera.exposureTime
        #self.camera.data.subscribe(self.onNewCameraImage)

        # empty
        self.sem_det_image = VigilantAttribute(InstrumentalImage(None, None, None))

    def onNewCameraImage(self, dataflow, data):
        size = data.shape[0:2]
        # TODO make only one copy for conversion 16bits -> 3x8
        # TODO insert brightness and contrast computation instead of copy
        data8 = numpy.array(data, dtype="uint8") # 1 copy
        rgb = numpy.dstack((data8, data8, data8)) # 1 copy
        im = wx.ImageFromData(*size, data=rgb.tostring())
        im.InitAlpha() # it's a different buffer so useless to do it in numpy

        try:
            # TODO should be initialised by backend
            pos = data.metadata[model.MD_POS]
        except KeyError:
            logging.warning("position of image unknown")
            pos = self.prev_pos # at least it shouldn't be too wrong

        try:
            mpp = data.metadata[model.MD_PIXEL_SIZE][0]
        except KeyError:
            logging.warning("pixel density of image unknown")
            # Hopefully it'll be within the same magnitude
            mpp = data.metadata[model.MD_SENSOR_PIXEL_SIZE][0] / 60 # XXX

#        h = hpy() # memory profiler
#        print h.heap()
        self.optical_det_image.value = InstrumentalImage(im, mpp, pos)

    def avOnStagePos(self, val):
        move = {}
        if hasattr(self.stage, "moveAbs"):
            # absolute
            move = {"x": val[0], "y": val[1]}
            self.stage.moveAbs(move)
        else:
            # relative
            move = {"x": val[0] - self.prev_pos[0], "y": val[1] - self.prev_pos[1]}
            self.stage.moveRel(move)
        self.prev_pos = val


class SECOMBackendConnected(SECOMModel):
    """
    A class representing a SECOM microscope based on a model.Microscope instance
    It's a very simple version which always acquires from the SEM and camera
    """
    pass

class InstrumentalImage(object):
    """
    Contains a bitmap and meta data about it
    """

    def __init__(self, im, mpp, center):
        """
        im wx.Image
        mpp (float>0)
        center (2-tuple float)
        """
        self.image = im
        # TODO should be a tuple (x/y)
        self.mpp = mpp
        self.center = center



# THE FUTURE：
class MicroscopeModel(object):
    """
    Represent a microscope directly for a graphical user interface
    """
    pass
    # streams:
    #    + list of raw images (ordered by time)
    #    + coloration + contrast + brightness + name
    #    + InstrumentalImage corresponding to the tiling of all the raw images
    # stage : to move the sample
    # microscope: links to the real microscope component provided by the backend
    #

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: