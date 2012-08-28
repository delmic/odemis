# -*- coding: utf-8 -*-
"""
Created on 16 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or
modify it under the terms of the GNU General Public License as published by the
Free Software Foundation, either version 2 of the License, or (at your option)
any later version.

Odemis is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

from odemis import model
from odemis.gui.log import log
from odemis.gui.util.img import DataArray2wxImage
from odemis.model import VigilantAttribute, MD_POS, MD_PIXEL_SIZE, \
    MD_SENSOR_PIXEL_SIZE
import logging



class SECOMModel(object):
    """
    Represent the data of a SECOM microscope
    This is the main Model, from a Model/View/Controller perspective
    """

    def __init__(self):
        self.stage_pos = VigilantAttribute((0, 0), setter=self.avOnStagePos) # m,m

        # FIXME: maybe could go into (sub)classes like OpticalEmitter, SEDetector...
        self.optical_emt_wavelength = VigilantAttribute(450) # nm XXX a range?
        self.optical_det_wavelength = VigilantAttribute(568) # nm
        self.optical_det_exposure_time = VigilantAttribute(1.0) # s
        self.optical_det_image = VigilantAttribute(InstrumentalImage(None, None, None))
        self.optical_det_raw = None # the last raw data received
        self.optical_auto_bc = True # whether to use auto brightness & contrast
        self.optical_contrast = model.FloatContinuous(0, range=[-1, 1]) # ratio, contrast if no auto
        self.optical_brightness = model.FloatContinuous(0, range=[-1, 1]) # ratio, balance if no auto

        self.sem_emt_dwell_time = VigilantAttribute(0.00001) #s
        self.sem_emt_spot = VigilantAttribute(4) # no unit (could be m²)
        self.sem_emt_hv = VigilantAttribute(30000) # V
        self.sem_det_image = VigilantAttribute(InstrumentalImage(None, None, None))

        self.opt_focus = None # this is directly an Actuator

    def avOnStagePos(self, val):
        logging.info("requested to move stage to pos: %s", str(val))
        return val

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

        self.opt_focus = None
        for a in microscope.actuators:
            if a.role == "focus":
                self.opt_focus = a
                break
        # it's not an error to not have focus
        if not self.opt_focus:
            log.info("no focus actuator found in the microscope")

        # DEBUG XXX
        self.opt_focus.moveRel({"z": 0})

        try:
            self.prev_pos = (self.stage.position.value["x"],
                             self.stage.position.value["y"])
        except (KeyError, AttributeError):
            self.prev_pos = (0, 0)
        # override
        self.stage_pos = VigilantAttribute(self.prev_pos) # (m,m) => (X,Y)
        self.stage_pos.subscribe(self.avOnStagePos)

        # direct linking
        self.optical_det_exposure_time = self.camera.exposureTime
        self.optical_depth = self.camera.shape[2]


        # No SEM
        #self.sem_det_image = VigilantAttribute(InstrumentalImage(None, None, None))

        # FIXME: on ON/OFF from GUI
        #self.turnOn()

    def turnOn(self):
        # TODO turn on the light
        self.camera.data.subscribe(self.onNewCameraImage)

    def turnOff(self):
        # TODO turn of the light
        # TODO forbid move in this mode (or just forbid move in the canvas if no stream?)
        self.camera.data.unsubscribe(self.onNewCameraImage)

    # TODO: see if really necessary: because __del__ prevents GC to work
    def __del__(self):
        self.turnOff()

    def onNewCameraImage(self, dataflow, data):
        if self.optical_auto_bc:
            brightness = None
            contrast = None
        else:
            brightness = self.optical_brightness
            contrast = self.optical_contrast

        self.optical_det_raw = data
        im = DataArray2wxImage(data, self.optical_depth, brightness, contrast)
        im.InitAlpha() # it's a different buffer so useless to do it in numpy

        try:
            # TODO should be initialised by backend
            pos = data.metadata[MD_POS]
        except KeyError:
            log.warning("position of image unknown")
            pos = self.prev_pos # at least it shouldn't be too wrong

        try:
            mpp = data.metadata[MD_PIXEL_SIZE][0]
        except KeyError:
            log.warning("pixel density of image unknown")
            # Hopefully it'll be within the same magnitude
            mpp = data.metadata[MD_SENSOR_PIXEL_SIZE][0] / 60 # XXX

#        h = hpy() # memory profiler
#        print h.heap()
        self.optical_det_image.value = InstrumentalImage(im, mpp, pos)

    def avOnStagePos(self, val):
        move = {}
        # TODO: a way to know if it can do absolute move? => .capabilities!
#        if hasattr(self.stage, "moveAbs"):
#            # absolute
#            move = {"x": val[0], "y": val[1]}
#            self.stage.moveAbs(move)
#        else:

        # relative
        move = {"x": val[0] - self.prev_pos[0], "y": val[1] - self.prev_pos[1]}
        self.stage.moveRel(move)

        self.prev_pos = val
        return val


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
    #    + list of raw images that compose the whole view (ordered by time)
    #    + colouration + contrast + brightness + name + ...
    #    + InstrumentalImage corresponding to the tiling of all the raw images
    # streams can be add, removed, listed.
    # stage : to move the sample
    # focus_a, focus_b...: actuators whose name is associated to a specific action in the GUI
    # microscope: links to the real microscope component provided by the backend
    #

    # each canvas gets a set of streams to display
# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: