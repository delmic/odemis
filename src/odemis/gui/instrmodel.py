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
from odemis.gui import util
from odemis.gui.log import log
from odemis.gui.util.img import DataArray2wxImage
from odemis.model import VigilantAttribute, MD_POS, MD_PIXEL_SIZE, \
    MD_SENSOR_PIXEL_SIZE
import logging
import wx
from odemis.gui.util.units import readable_str



class SECOMModel(object):
    """
    Represent the data of a SECOM microscope
    This is the main Model, from a Model/View/Controller perspective
    """

    def __init__(self):
        self.stage_pos = VigilantAttribute((0, 0), setter=self.avOnStagePos) # m,m

        # FIXME: maybe could go into (sub)classes like OpticalEmitter, SEDetector...
        self.optical_emt_wavelength = VigilantAttribute(488, unit="nm") 
        self.optical_det_wavelength = VigilantAttribute(507, unit="nm")
        self.optical_det_exposure_time = VigilantAttribute(1.0) # s
        self.optical_det_image = VigilantAttribute(InstrumentalImage(None, None, None))
        self.optical_det_raw = None # the last raw data received
        self.optical_auto_bc = VigilantAttribute(True) # whether to use auto brightness & contrast
        self.optical_contrast = model.FloatContinuous(0, range=[-100, 100]) # ratio, contrast if no auto
        self.optical_brightness = model.FloatContinuous(0, range=[-100, 100]) # ratio, balance if no auto

        self.sem_emt_dwell_time = VigilantAttribute(0.00001) #s
        self.sem_emt_spot = VigilantAttribute(4) # no unit (could be m²)
        self.sem_emt_hv = VigilantAttribute(30000) # eV
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
                # TODO: subscribe to the real position, self.stage.position, and recenter the view of all viewports
                # on each of these moves
                self.stage.position.subscribe(self._onPhysicalStagePos)
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

        try:
            self.prev_pos = (self.stage.position.value["x"],
                             self.stage.position.value["y"])
        except (KeyError, AttributeError):
            self.prev_pos = (0, 0) # TODO moves this control to stage setup above
        # TODO: instead do:
        # self._onPhysicalStagePos(self.stage.position.value)
        # just setting the raw value should be fine
        self.stage_pos.value = self.prev_pos
        
        # direct linking
        self.optical_det_exposure_time = self.camera.exposureTime
        self.optical_depth = self.camera.shape[2]
        
        # get notified when brightness/contrast is updated
        self.optical_auto_bc.subscribe(self.onBrightnessContrast)
        self.optical_contrast.subscribe(self.onBrightnessContrast)
        self.optical_brightness.subscribe(self.onBrightnessContrast)

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

    def onBrightnessContrast(self, unused):
        # called whenever brightness/contrast changes
        # => needs to recompute the image (but not too often, so we do it in a timer)
        
        # is there any image to update?
        if self.optical_det_raw is None:
            return
        # TODO: in timer
        self.onNewCameraImage(None, self.optical_det_raw)
        
    def onNewCameraImage(self, dataflow, data):
        if self.optical_auto_bc.value:
            brightness = None
            contrast = None
        else:
            brightness = self.optical_brightness.value / 100.
            contrast = self.optical_contrast.value / 100.

        self.optical_det_raw = data
        im = DataArray2wxImage(data, self.optical_depth, brightness, contrast)
        im.InitAlpha() # it's a different buffer so useless to do it in numpy

        try:
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

    def _onPhysicalStagePos(self, pos):
        # TODO make sure we cannot go into an infinite loop with avOnStagePos
        # * avOnStagePos should be a setter
        # * we should update the raw value of stage_pos + notify
        #self.stage_pos.value = (pos["x"], pos["y"])
        pass


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

    def stopMotion(self):
        """
        Stops immediately every axis
        """
        self.stage.stop()
        self.opt_focus.stop()
        logging.info("stopped motion on every axes")

class SECOMBackendConnected(SECOMModel):
    """
    A class representing a SECOM microscope based on a model.Microscope instance
    It's a very simple version which always acquires from the SEM and camera
    """
    pass

class InstrumentalImage(object):
    """
    Contains an RGB bitmap and meta-data about where it is taken
    """

    def __init__(self, im, mpp, center, rotation=0.0):
        """
        im wx.Image
        mpp (float>0): meters per pixel
        center (2-tuple float): position (in meters) of the center of the image
        rotation (float): rotation in degrees (i.e., 180 = upside-down) 
        Note: When displayed, the scaling, translation, and rotation have to be 
        applied "independently": scaling doesn't affect the translation, and 
        rotation is applied from the center of the image.
        """
        self.image = im
        # TODO should be a tuple (x/y)
        assert(mpp > 0)
        self.mpp = mpp
        assert(len(center) == 2)
        self.center = center
        self.rotation = rotation



# THE FUTURE：
class Microscope(object):
    """
    Represent a microscope directly for a graphical user interface.
    Provides direct reference to the HwComponents and 
    """
    
    def __init__(self, microscope):
        """
        microscope (model.Microscope): the root of the HwComponent tree provided by the back-end
        """
        # These are either HwComponents or None (if not available)
        self.ccd = None
        self.stage = None
        self.focus = None # actuator to change the camera focus 
        self.light = None
        self.ebeam = None
        self.sed = None
        
        for d in microscope.detectors:
            if d.role == "ccd":
                self.ccd = d
            elif d.role == "se-detector":
                self.sed = d
        if not self.ccd and not self.sed:
            raise Exception("no camera nor SE-detector found in the microscope")

        for a in microscope.actuators:
            if a.role == "stage":
                self.stage = a
                # TODO: viewports should subscribe to the stage
            elif a.role == "focus":
                self.opt_focus = a
        if not self.stage:
            raise Exception("no stage found in the microscope")
        # it's not an error to not have focus
        if not self.focus:
            log.info("no focus actuator found in the microscope")

        for e in microscope.emitters:
            if e.role == "light":
                self.light = e
            elif e.role == "e-beam":
                self.ebeam = e
        if not self.light and not self.ebeam:
            raise Exception("no emitter found in the microscope")

        self.streams = [] # list of streams available (handled by StreamController)
        self.viewports = [] # list of viewports available
        
    
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
    
    
# almost every public attribute of the model object is exported as a VA, in order
# to have the GUI automatically modify and update it. 
    
class Stream(object):
    """
    Represents a stream: the data from a detector with a given emitter active
    Basically it handles acquiring the data from the hardware and renders it as
    an InstrumentalImage with the given image transformation .
    """
    def __init__(self, name, detector, dataflow):
        """
        name (string): user-friendly name of this stream
        detector (Detector): the detector which has the dataflow
        dataflow (Dataflow): the dataflow from which to get the data 
        """
        self.name = VigilantAttribute(name)
        self._detector = detector
        self._dataflow = dataflow # this should not be accessed directly
        self.raw = [] # list of DataArray received and used to generate the image
        # the most important attribute
        self.image = VigilantAttribute(InstrumentalImage(None, None, None))
        
        # whether
        self.active = model.BooleanVA(False)
        self.active.subscribe(self.onActive)
        
        self._depth = self._detector.shape[2] # used for B/C adjustment
        self.auto_bc = model.BooleanVA(True) # whether to use auto brightness & contrast
        # these 2 are only used if auto_bc is False
        self.contrast = model.FloatContinuous(0, range=[-100, 100]) # ratio, contrast if no auto
        self.brightness = model.FloatContinuous(0, range=[-100, 100]) # ratio, balance if no auto

        self.auto_bc.subscribe(self.onBrightnessContrast)
        self.contrast.subscribe(self.onBrightnessContrast)
        self.brightness.subscribe(self.onBrightnessContrast)

    def onActive(self, active):
        # Normally is called only the value _changes_
        if active:
            self._dataflow.subscribe(self.onNewImage)
        else:
            self._dataflow.unsubscribe(self.onNewImage)

    # TODO: see if really necessary: because __del__ prevents GC to work
    def __del__(self):
        self.active.value = False

    def _updateImage(self, tint=(255, 255, 255)):
        """
        Recomputes the image with all the raw data available
        tint (int): colouration of the image, only used by FluoStream to avoid code duplication
        """
        data = self.raw[0]
        if self.auto_bc.value:
            brightness = None
            contrast = None
        else:
            brightness = self.brightness.value / 100.
            contrast = self.contrast.value / 100.
        
        im = DataArray2wxImage(data, self._depth, brightness, contrast, tint)
        im.InitAlpha() # it's a different buffer so useless to do it in numpy

        try:
            pos = data.metadata[MD_POS]
        except KeyError:
            log.warning("position of image unknown")
            pos = (0, 0)

        try:
            mpp = data.metadata[MD_PIXEL_SIZE][0]
        except KeyError:
            log.warning("pixel density of image unknown")
            # Hopefully it'll be within the same magnitude
            mpp = data.metadata[MD_SENSOR_PIXEL_SIZE][0] / 10.

        self.image.value = InstrumentalImage(im, mpp, pos)
        
    def onBrightnessContrast(self, unused):
        # called whenever brightness/contrast changes
        # => needs to recompute the image (but not too often, so we do it in a timer)
        
        # is there any image to update?
        if not len(self.raw):
            return
        # TODO: in timer
        self._updateImage()
        
    def onNewImage(self, dataflow, data):
        self.raw[0] = data
        self._updateImage()

def FluoStream(Stream):
    """
    Stream containing images obtained via epi-fluorescence.
    It basically knows how to select the right emission/filtered wavelengths, 
    and how to taint the image.
    """
    
    def __init__(self, name, detector, dataflow, light, filter):
        """
        name (string): user-friendly name of this stream
        detector (Detector): the detector which has the dataflow
        dataflow (Dataflow): the dataflow from which to get the data
        light (Light): the HwComponent to modify the light excitation
        filter (Filter): the HwComponent to modify the emission light filtering
        """
        Stream.__init__(name, detector, dataflow)
        self._light = light
        self._filter = filter
        
        # This is what is displayed to the user
        # TODO use the current value of the light and filter
        self.excitation = model.FloatContinuous(488e-9, unit="m")
        self.excitation.subscribe(self.onExcitation)
        self.emission = model.FloatContinuous(507e-9, unit="m") 
        self.emission.subscribe(self.onEmission)
        
        # colouration of the image
        defaultTint = util.conversion.wave2rgb(self.emission.value)
        self.tint = VigilantAttribute(defaultTint, unit="RGB")  
        
        # Only update Emission or Excitation only if the stream is active
        
    def onActive(self, active):
        if active:
            self._setLightExcitation()
            self._setFilterEmission()
        Stream.onActive(self, active)

    def _updateImage(self):
        Stream._updateImage(self, self.tint.value)
      
    def onExcitation(self, value):
        if self.active.value:
            self._setLightExcitation()
    
    def onEmission(self, value):
        if self.active.value:
            self._setFilterEmission()
            
    def _setFilterEmission(self):
        wl = self.emission.value
        if self._filter.band.readonly:
            # we can only check that it's correct
            fitting = False
            for l, h in self._filter.band.value:
                if l < wl and wl < h:
                    fitting = True
                    break
            if not fitting:
                log.warning("Emission wavelength %s doesn't fit the filter", 
                            readable_str(wl, "m"))
            return
        
        # TODO: improve fitting algorithm!
        # at least, we need to decide a way to select the band
        # choices on the VA?
        pass
    
    def _setLightExcitation(self):
        wl = self.excitation.value 
        def fitting(wl, spec):
            """
            returns a big number if spec fits to wl
            wl (float)
            spec (5-tuple float)
            """
            # is it included?
            if spec[0] > wl or wl < spec[4]:
                return 0
            
            distance = abs(wl - spec[2]) # distance to 100%
            if distance == 0:
                return float("inf")
            return 1. / distance
            
        spectra = self._light.spectra.value
        # arg_max with fitting function as key 
        i = spectra.index(max(spectra, key=lambda x: fitting(wl, x)))
        
        # create an emissions with only one source active
        emissions = [0] * len(spectra)
        emissions[i] = 1
        self._light.emissions.value = emissions
        
# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
