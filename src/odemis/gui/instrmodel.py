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
from odemis.gui.util.units import readable_str
from odemis.model import VigilantAttribute, MD_POS, MD_PIXEL_SIZE, \
    MD_SENSOR_PIXEL_SIZE
from odemis.model._vattributes import FloatContinuous
import logging
import threading
import time



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
        self.optical_contrast = model.FloatContinuous(0., range=[-100, 100]) # ratio, contrast if no auto
        self.optical_brightness = model.FloatContinuous(0., range=[-100, 100]) # ratio, balance if no auto

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

        # h = hpy() # memory profiler
        # print h.heap()
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
        # if hasattr(self.stage, "moveAbs"):
        #     # absolute
        #     move = {"x": val[0], "y": val[1]}
        #     self.stage.moveAbs(move)
        # else:

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
    # It'd be best to have it as a subclass of wx.Image, but wxPython has many
    # functions which return a wx.Image. We'd need to "override" them as well.

    def __init__(self, im, mpp=None, center=None, rotation=0.0):
        """
        im (None or wx.Image)
        mpp (None or float>0): meters per pixel
        center (None or 2-tuple float): position (in meters) of the center of the image
        rotation (float): rotation in degrees (i.e., 180 = upside-down)

        Note: When displayed, the scaling, translation, and rotation have to be
        applied "independently": scaling doesn't affect the translation, and
        rotation is applied from the center of the image.
        """
        self.image = im
        # TODO should be a tuple (x/y)
        assert(mpp is None or (mpp > 0))
        self.mpp = mpp
        assert(center is None or (len(center) == 2))
        self.center = center
        self.rotation = rotation


# THE FUTURE：

# The different states of a microscope
STATE_OFF = 0
STATE_ON = 1
STATE_PAUSE = 2

# The different types of view layouts
VIEW_LAYOUT_ONE = 0 # one big view
VIEW_LAYOUT_22 = 1 # 2x2 layout
VIEW_LAYOUT_FULLSCREEN = 2 # Fullscreen view (not yet supported)

class GUIMicroscope(object):
    """
    Represent a microscope directly for a graphical user interface.
    Provides direct reference to the HwComponents and
    """

    def __init__(self, microscope):
        """
        microscope (model.Microscope): the root of the HwComponent tree provided
                                       by the back-end
        """
        self.microscope = microscope
        # These are either HwComponents or None (if not available)
        self.ccd = None
        self.stage = None
        self.focus = None # actuator to change the camera focus
        self.light = None
        self.light_filter = None # emission light filter for fluorescence microscopy
        self.ebeam = None
        self.sed = None # secondary electron detector
        self.bsd = None # back-scatter electron detector

        for d in microscope.detectors:
            if d.role == "ccd":
                self.ccd = d
            elif d.role == "se-detector":
                self.sed = d
            elif d.role == "bs-detector":
                self.bsd = d
        if not self.ccd and not self.sed and not self.bsd:
            raise Exception("no camera nor electron detector found in the microscope")

        for a in microscope.actuators:
            if a.role == "stage":
                self.stage = a
                # TODO: viewports should subscribe to the stage
            elif a.role == "focus":
                self.focus = a
        if not self.stage:
            raise Exception("no stage found in the microscope")
        # it's not an error to not have focus
        if not self.focus:
            log.info("no focus actuator found in the microscope")

        for e in microscope.emitters:
            if e.role == "light":
                self.light = e
                # pick a nice value to turn on the light
                if self.light.power.value > 0:
                    self._light_power_on = self.light.power.value
                else:
                    try:
                        self._light_power_on = max(self.light.power.range)
                    except (AttributeError, model.NotApplicableError):
                        try:
                            self._light_power_on = max(self.light.power.choices)
                        except (AttributeError, model.NotApplicableError):
                            self._light_power_on = 1
                            logging.warning("Unknown value to turn on the light")
            elif e.role == "filter":
                self.light_filter = e
            elif e.role == "e-beam":
                self.ebeam = e
        if not self.light and not self.ebeam:
            raise Exception("no emitter found in the microscope")

        self.streams = set() # Streams available (handled by StreamController)
        # MicroscopeViews available, (handled by ViewController)
        # The Viewcontroller cares about position (top left, etc), GUIMicroscope
        # cares about what's what.
        self.views = {
            "sem_view": None,
            "opt_view": None,
            "combo1_view": None,
            "combo2_view": None,
        }

        # The MicroscopeView currently focused
        self.focussedView = VigilantAttribute(None)
        # the view layout
        # TODO maybe start with just one view
        self.viewLayout = model.IntEnumerated(VIEW_LAYOUT_22,
                                              choices=set([VIEW_LAYOUT_ONE,
                                                       VIEW_LAYOUT_22,
                                                       VIEW_LAYOUT_FULLSCREEN]))

        self.opticalState = model.IntEnumerated(STATE_OFF,
                                choices=set([STATE_OFF, STATE_ON, STATE_PAUSE]))
        self.opticalState.subscribe(self.onOpticalState)
        self.emState = model.IntEnumerated(STATE_OFF,
                                choices=set([STATE_OFF, STATE_ON, STATE_PAUSE]))
        self.emState.subscribe(self.onEMState)

    # Getters and Setters

    @property
    def optical_view(self):
        return self.views["opt_view"]

    @optical_view.setter #pylint: disable=E1101
    def optical_view(self, value): #pylint: disable=E0102
        self.views["opt_view"] = value

    @property
    def sem_view(self):
        return self.views["sem_view"]

    @sem_view.setter #pylint: disable=E1101
    def sem_view(self, value): #pylint: disable=E0102
        self.views["sem_view"] = value

    @property
    def combo1_view(self):
        return self.views["combo1_view"]

    @combo1_view.setter #pylint: disable=E1101
    def combo1_view(self, value): #pylint: disable=E0102
        self.views["combo1_view"] = value

    @property
    def combo2_view(self):
        return self.views["combo2_view"]

    @combo2_view.setter #pylint: disable=E1101
    def combo2_view(self, value): #pylint: disable=E0102
        self.views["combo2_view"] = value

    def stopMotion(self):
        """
        Stops immediately every axis
        """
        self.stage.stop()
        self.focus.stop()
        logging.info("stopped motion on every axes")

    def onOpticalState(self, state):
        # only called when it changes
        if state in (STATE_OFF, STATE_PAUSE):
            # Turn off the optical path. All the streams using it should be
            # already deactivated.
            if self.light:
                if self.light.power.value > 0:
                    # save the value only if it makes sense
                    self._light_power_on = self.light.power.value
                self.light.power.value = 0
        elif state == STATE_ON:
            # the image acquisition from the camera is handled solely by the
            # streams
            if self.light:
                self.light.power.value = self._light_power_on

    def onEMState(self, state):
        if state == STATE_OFF:
            # TODO turn off really the ebeam and detector
            if self.ebeam:
                try:
                    # TODO save the previous value
                    # blank the ebeam
                    self.ebeam.energy.value = 0
                except:
                    # Too bad. let's just do nothing then.
                    logging.debug("Ebeam doesn't support setting energy to 0")
        elif state == STATE_PAUSE:
            if self.ebeam:
                try:
                    # TODO save the previous value
                    # blank the ebeam
                    self.ebeam.energy.value = 0
                except:
                    # Too bad. let's just do nothing then.
                    logging.debug("Ebeam doesn't support setting energy to 0")

        elif state == STATE_ON:
            # TODO anything else to turn on?
            if self.ebeam:
                try:
                    # TODO use the previous value
                    self.ebeam.energy.value = self.ebeam.energy.choices[1]
                except:
                    # Too bad. let's just do nothing then (and hope it's on)
                    logging.debug("Ebeam doesn't support setting energy")

    # TODO dye database
    # name (for the user only)
    # excitation wl (used to control the hardware)
    # emission wl (used to control the hardware)
    # ?official full excitation/emission spectra? (for user only)



    # viewport controller (to be merged with stream controller?)
    # Creates the 4 microscope views at init, with the right names, depending on
    #   the available microscope hardware.
    # (The 4 viewports canvas are already created, the main interface connect
    #   them to the view, by number)
    # In charge of switching between 2x2 layout and 1 layout.
    # In charge of updating the view focus
    # In charge of updating the view thumbnails???
    # In charge of ensuring they all have same zoom and center position
    # In charge of applying the toolbar actions on the right viewport
    # in charge of changing the "hair-cross" display



    # Note about image acquisition:
    # when image acquisition window appears:
    # * current view is used a template for StreamTree
    # * current settings are read and converted according to the current preset
    # * live-acquisition is stopped in them main viewports => instead it is used to update the preview window
    # Then the user can
    # * change StreamTree (different stream, different merge operations): independent of live view
    # * change stream settings (brightness/contrast/tint) => also affects the steams in the live view
    # * change settings to the one in live view, or reset all of them to the preset, or put a separate value (without affecting the live-view settings)
    #   => we need to be able to freeze/unfreeze widgets interfacing the VA's
    # During image acquisition:
    # * everything stays frozen
    # * streams (detector/emitter) can get activated/deactivated in any order

# almost every public attribute of the model object is exported as a VA, in order
# to have the GUI automatically modify and update it.

class Stream(object):
    WARNING_EXCITATION_NOT_OPT="""The excitation wavelength selected cannot be
    optimally generated by the hardware."""
    WARNING_EXCITATION_IMPOSSIBLE="""The excitation wavelength selected cannot be
    generated by the hardware."""
    WARNING_EMISSION_NOT_OPT="""The emission wavelength selected cannot be
    optimally detected by the hardware."""
    WARNING_EMISSION_IMPOSSIBLE="""The emission wavelength selected cannot be
    detected by the hardware."""

    """
    Represents a stream: the data coming from a detector's dataflow, couples
     with a given emitter active
    (or several emitters, if a subclass implements it)
    Basically it handles acquiring the data from the hardware and renders it as
    an InstrumentalImage with the given image transformation.

    This is an abstract class, unless the emitter doesn't need any configuration
    (always on, with the right settings).
    """
    def __init__(self, name, detector, dataflow, emitter):
        """
        name (string): user-friendly name of this stream
        detector (Detector): the detector which has the dataflow
        dataflow (Dataflow): the dataflow from which to get the data
        emitter (Emitter): the emitter
        """
        self.name = model.StringVA(name)
        # this should not be accessed directly
        self._detector = detector
        self._dataflow = dataflow
        self._emitter = emitter

        # list of DataArray received and used to generate the image
        # every time it's modified, image is also modified
        self.raw = []
        # the most important attribute
        self.image = VigilantAttribute(InstrumentalImage(None))

        # TODO should maybe to 2 methods activate/deactivate to explicitly
        # start/stop acquisition, and one VA "updated" to stated that the user
        # want this stream updated (as often as possible while other streams are
        # also updated)
        self.updated = model.BooleanVA(False) # Whether the user wants the stream to be updated
        self.active = model.BooleanVA(False)
        self.active.subscribe(self.onActive)
        # TODO do we also need a set of incompatible streams? When any of the
        # incompatible stream is active, this stream cannot be active


        if self._detector:
            self._depth = self._detector.shape[-1] # used for B/C adjustment
        self.auto_bc = model.BooleanVA(True) # whether to use auto brightness & contrast
        # these 2 are only used if auto_bc is False
        self.contrast = model.FloatContinuous(0, range=[-100, 100]) # ratio, contrast if no auto
        self.brightness = model.FloatContinuous(0, range=[-100, 100]) # ratio, balance if no auto

        self.auto_bc.subscribe(self.onBrightnessContrast)
        self.contrast.subscribe(self.onBrightnessContrast)
        self.brightness.subscribe(self.onBrightnessContrast)

        # list of warnings to display to the user
        # TODO should be a set
        self.warnings = model.ListVA([]) # should only contains WARNING_*

    def _removeWarnings(self, warnings):
        """
        Remove all the given warnings, if they are present
        warnings (set of WARNING_*): the warnings to remove
        """
        new_warnings = set(self.warnings.value) - set(warnings)
        self.warnings.value = list(new_warnings)

    def _addWarning(self, warning):
        """
        Add a warning if not already present
        warning (WARNING_*)
        """
        # Surprisingly set([1]).add(2) returns None
        new_warnings = set(self.warnings.value) | set(warning)
        self.warnings.value = list(new_warnings)

    def onActive(self, active):
        # Called only the value _changes_
        if active:
            log.debug("Subscribing to dataflow of component %s", self._detector.name)
            if not self.updated.value:
                log.warning("Trying to activate stream while it's not supposed to update")
            self._dataflow.subscribe(self.onNewImage)
        else:
            log.debug("Unsubscribing from dataflow of component %s", self._detector.name)
            self._dataflow.unsubscribe(self.onNewImage)

    # No __del__: subscription should be automatically stopped when the object
    # disappears, and the user should stop the update first anyway.

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
        # For now, raw images are pretty simple: we only have one
        # (in the future, we could keep the old ones which are not fully overlapped
        if len(self.raw) > 0:
            self.raw.pop()
        self.raw.insert(0, data)
        self._updateImage()

class SEMStream(Stream):
    """
    Stream containing images obtained via Scanning electron microscope.
    It basically knows how to activate the scanning electron and the detector.
    """

    def onActive(self, active):
        if active:
            # TODO if can blank => unblank
            pass
        Stream.onActive(self, active)


class BrightfieldStream(Stream):
    """
    Stream containing images obtained via optical brightfield illumination.
    It basically knows how to select white light and disable any filter.
    """

    def onActive(self, active):
        if active:
            self._setLightExcitation()
            # TODO do we need to have a special command to disable filter??
            #  or should it be disabled automatically by the other streams not using it?
#            self._setFilterEmission()
        Stream.onActive(self, active)

#    def _setFilterEmission(self):
#        if not self._filter.band.readonly:
#            raise NotImplementedError("Do not know how to change filter band")


    def _setLightExcitation(self):
        # TODO how to select white light??? We need a brightlight hardware?
        # Turn on all the sources? Does this always mean white?
        # At least we should set a warning if the final emission range is quite
        # different from the normal white spectrum
        em = [1 for e in self._emitter.emissions.value]
        self._emitter.emissions.value = em


class FluoStream(Stream):
    """
    Stream containing images obtained via epi-fluorescence.
    It basically knows how to select the right emission/filtered wavelengths,
    and how to taint the image.
    """

    def __init__(self, name, detector, dataflow, emitter, filter):
        """
        name (string): user-friendly name of this stream
        detector (Detector): the detector which has the dataflow
        dataflow (Dataflow): the dataflow from which to get the data
        emitter (Light): the HwComponent to modify the light excitation
        filter (Filter): the HwComponent to modify the emission light filtering
        """
        Stream.__init__(self, name, detector, dataflow, emitter)
        self._filter = filter

        # This is what is displayed to the user
        # TODO what should be nice default value of the light and filter?
        exc_range = [min([s[0] for s in emitter.spectra.value]),
                     max([s[4] for s in emitter.spectra.value])]
        self.excitation = model.FloatContinuous(488e-9, range=exc_range, unit="m")
        self.excitation.subscribe(self.onExcitation)
        em_range = [min([s[0] for s in filter.band.value]),
                    max([s[1] for s in filter.band.value])]
        self.emission = model.FloatContinuous(507e-9, range=em_range, unit="m")
        self.emission.subscribe(self.onEmission)

        # colouration of the image
        defaultTint = util.conversion.wave2rgb(self.emission.value)
        self.tint = VigilantAttribute(defaultTint, unit="RGB")

    def onActive(self, active):
        # TODO update Emission or Excitation only if the stream is active
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
        # TODO: we need a way to know if the HwComponent can change automatically
        # or only manually. For now we suppose it's manual

        # Changed manually: we can only check that it's correct
        fitting = False
        for l, h in self._filter.band.value:
            if l < wl and wl < h:
                fitting = True
                break
        self._removeWarnings([Stream.WARNING_EMISSION_IMPOSSIBLE,
                              Stream.WARNING_EMISSION_NOT_OPT])
        if not fitting:
            log.warning("Emission wavelength %s doesn't fit the filter",
                        readable_str(wl, "m"))
            self._addWarning(Stream.WARNING_EMISSION_IMPOSSIBLE)
            # TODO detect no optimal situation (within 10% band of border?)
        return

        # changed automatically
#        raise NotImplementedError("Do not know how to change filter band")

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

        spectra = self._emitter.spectra.value
        # arg_max with fitting function as key
        best = max(spectra, key=lambda x: fitting(wl, x))
        i = spectra.index(best)

        # create an emissions with only one source active
        emissions = [0] * len(spectra)
        emissions[i] = 1
        self._emitter.emissions.value = emissions


        # TODO read back self._emitter.emissions.value to get the actual value
        # set warnings if necessary
        self._removeWarnings([Stream.WARNING_EXCITATION_IMPOSSIBLE,
                              Stream.WARNING_EXCITATION_NOT_OPT])
        # TODO if the band is too wide (e.g., white), it should also have a warning
        # TODO if the light can only be changed manually, display a warning
        if wl < best[0] or wl > best[4]: # outside of band
            self._addWarning(Stream.WARNING_EXCITATION_IMPOSSIBLE)
        elif wl < best[1] or wl > best[3]: # outside of main 50% band
            self._addWarning(Stream.WARNING_EXCITATION_NOT_OPT)

class StaticStream(Stream):
    """
    Stream containing one static image.
    For test and static images.
    """
    def __init__(self, name, image):
        """
        Note: parameters are different from the base class.
        image (InstrumentalImage): image to display
        """
        Stream.__init__(self, name, None, None, None)
        self.image = VigilantAttribute(image)

    def onActive(self, active):
        # don't do anything
        pass

    def onBrightnessContrast(self, unused):
        # TODO use original image as raw, and update the image
        pass


class MicroscopeView(object):
    """
    Represents a view from a microscope, and ways to alter it.
    Basically, its "input" is a StreamTree, and can request stage and focus move.
    It never computes itself the composited image from all the streams. It's up
    to other objects (e.g., the canvas) to ask the StreamTree for its latest
    image (the main goal of this scheme is to avoid computation when not needed).
    Similarly, the thumbnail is never automatically recomputed, but other
    objects can update it.
    """

    # TODO we need to differenciate views according to what they should be showing
    # to the user (in terms of streams):
    #  * inherit (with nothing inside each subclass)
    #  * special attribute with list of streams classes this view is for.

    def __init__(self, name, stage=None, focus0=None, focus1=None, stream_classes=None):
        """
        name (string): user-friendly name of the view
        stage (Actuator): actuator with two axes: x and y
        focus0 (Actuator): actuator with one axis: z. Can be None
        focus1 (Actuator): actuator with one axis: z. Can be None
        Focuses 0 and 1 are modified when changing focus respectively along the
           X and Y axis.
        stream_classes (None, or tuple of classes): all subclasses that the streams
          in this view can show (restriction is not technical, only for the user)
        """
        self.name = model.StringVA(name)
        self.stream_classes = stream_classes or (Stream,)
        self._stage = stage
        self._focus = [focus0, focus1]

        # The real stage position, to be modified via moveStageToView()
        # it's a direct access from the stage, so looks like a dict of axes
        if stage:
            self.stage_pos = stage.position
            # stage.position.subscribe(self.onStagePos)

            # the current center of the view, which might be different from the stage
            # TODO: we might need to have it on the GUIMicroscope, if all the viewports must display the same location
            pos = self.stage_pos.value
            view_pos_init = (pos["x"], pos["y"])
        else:
            view_pos_init = (0, 0)
        self.view_pos = model.ListVA(view_pos_init, unit="m")

        # current density (meter per pixel, ~ scale/zoom level)
        self.mpp = PositiveVA(10e-6, unit="m/px") # (10um/px => ~large view of the sample)

        # how much one image is displayed on the other one
        # TODO rinze had added a non-notifying add_value. Needed?!!
        self.merge_ratio = FloatContinuous(0.3, range=[0, 1], unit="")
        self.merge_ratio.subscribe(self._onMergeRatio)

        # Streams to display (can be considered in most cases a implementation detail)
        # Note: use addStream/removeStream for simple modifications
        self.streams = StreamTree(kwargs={"merge": self.merge_ratio.value})
        # Only modify with this lock acquired:
        self._streams_lock = threading.Lock()

        # Last time the image of the view was changed. It's actually mostly
        # a trick to allow other parts of the GUI to know when the (theoretical)
        # composited image has changed.
        self.lastUpdate = model.FloatVA(time.time(), unit="s")
        self._has_received_image = False # last initialisation is done on the first image received

        # a thumbnail version of what is displayed
        self.thumbnail = VigilantAttribute(None) # contains a wx.Image

        # TODO list of annotations to display
        self.crosshair = model.BooleanVA(True)

    def moveStageToView(self):
        """
        move the stage to the current view_pos
        return: a future (that allows to know when the move is finished)
        Note: once the move is finished stage_pos will be updated (by the back-end)
        """
        if not self._stage:
            return
        pos = self.view_pos.value
        # TODO: a way to know if it can do absolute move? => .capabilities!
#        if hasattr(self.stage, "moveAbs"):
#            # absolute
#            move = {"x": pos[0], "y": pos[1]}
#            self._stage.moveAbs(move)
#        else:

        # relative
        prev_pos = self.stage_pos.value
        move = {"x": pos[0] - prev_pos["x"], "y": pos[1] - prev_pos["y"]}
        return self._stage.moveRel(move)

#    def onStagePos(self, pos):
#        # we want to recenter the viewports whenever the stage moves
#        # Not sure whether that's really the right way to do it though...
#        # TODO: avoid it to move the view when the user is dragging the view
#        #  => might require cleverness
#        self.view_pos = model.ListVA((pos["x"], pos["y"]), unit="m")

    def getStreams(self):
        """
        returns (list of Stream): list of streams that are displayed in the view
        Do not modify directly, use addStream(), and removeStream().
        Note: use .streams for getting the raw StreamTree
        """
        return self.streams.getStreams()

    def addStream(self, stream):
        """
        Add a stream to the view. It takes care of updating the StreamTree
        according to the type of stream.
        stream (Stream): stream to add
        If the stream is already present, nothing happens
        """
        # check if the stream is already present
        if stream in self.streams.getStreams():
            return

        if not isinstance(stream, self.stream_classes):
            log.warning("Adding incompatible stream %s to view %s", stream.name.value, self.name.value)

        # Find out where the stream should go in the streamTree
        # FIXME: manage sub-trees, with different merge operations
        # For now we just add it to the list of streams, with the only merge operation possible
        with self._streams_lock:
            self.streams.streams.append(stream)

        # subscribe to the stream's image
        stream.image.subscribe(self._onNewImage)

        # if the stream already has an image, update now
        if stream.image.value and stream.image.value.image:
            self._onNewImage(stream.image.value)

    def removeStream(self, stream):
        """
        Remove a stream from the view. It takes care of updating the StreamTree.
        stream (Stream): stream to remove
        If the stream is not present, nothing happens
        """
        # Stop listening to the stream changes
        stream.image.unsubscribe(self._onNewImage)

        with self._streams_lock:
            # check if the stream is already removed
            if not stream in self.streams.getStreams():
                return

            # remove stream from the StreamTree()
            # TODO handle more complex trees
            self.streams.streams.remove(stream)

        # let everyone know that the view has changed
        self.lastUpdate.value = time.time()

    def _onNewImage(self, im):
        """
        Called when one stream has its image updated
        im (InstrumentalImage)
        """
        # if it's the first image ever, set mpp to the mpp of the image
        if not self._has_received_image and im.mpp:
            self.mpp.value = im.mpp
            self._has_received_image = True

        # just let everyone that the composited image has changed
        self.lastUpdate.value = time.time()

    def _onMergeRatio(self, ratio):
        """
        Called when the merge ratio is modified
        """
        # This actually modifies the root operator of the stream tree
        # It has effect only if the operator can do something with the "merge" argument
        with self._streams_lock:
            self.streams.kwargs["merge"] = ratio

        # just let everyone that the composited image has changed
        self.lastUpdate.value = time.time()

class StreamTree(object):
    """
    Object which contains a set of streams, and how they are merged to appear
    as one image. It's a tree which has one stream per leaf and one merge
    operation per node. => recursive structure (= A tree is just a node with
    a merge method and a list of subnodes, either streamtree as well, or stream)
    """

    def __init__(self, operator=None, streams=None, **kwargs):
        """
        operator (callable): a function that takes a list of InstrumentalImage in the
            same order as the streams are given, and the additional arguments and
            returns one InstrumentalImage.
            By default operator is an average function.
        streams (list of Streams or StreamTree): a list of streams, or StreamTrees.
            If a StreamTree is provided, its outlook is first computed and then
            passed as an InstrumentalImage.
        kwargs: any argument to be given to the operator function
        """
        self.operator = operator or StreamTree.Average

        streams = streams or []
        assert(isinstance(streams, list))
        for s in streams:
            assert(isinstance(s, (Stream, StreamTree)))
        self.streams = streams

        self.kwargs = kwargs


    def getStreams(self):
        """
        Return the set of streams used to compose the picture. IOW, the leaves
        of the tree.
        """
        leaves = set()
        for s in self.streams:
            if isinstance(s, Stream):
                leaves.add(s)
            elif isinstance(s, StreamTree):
                leaves += s.getStreams()

        return leaves

    def getImage(self):
        """
        Returns an InstrumentalImage composed of all the current stream images.
        Precisely, it returns the output of a call to operator.
        """
        # TODO: probably not so useful function, need to see what canvas
        #  it will likely need as argument a wx.Bitmap, and view rectangle
        #  that will define where to save the result

        # create the arguments list for operator
        images = []
        for s in self.streams:
            if isinstance(s, Stream):
                images.append(s.image.value)
            elif isinstance(s, StreamTree):
                images.append(s.getImage())

        return self.operator(images, **self.kwargs)

    def getRawImages(self):
        """
        Returns a list of all the raw images used to create the final image
        """
        # TODO not sure if a list is enough, we might need to return more
        # information about how the image was built (operator, args...)
        lraw = []
        for s in self.getStreams():
            lraw.extend(s.raw)

        return lraw

    @staticmethod
    def Average(images):
        """
        mix the given images into a big image so that each pixel is the average of each
         pixel (separate operation for each colour channel).
        """
        # TODO (once the operator callable is clearly defined)
        raise NotImplementedError()

class PositiveVA(VigilantAttribute):
    """
    VigilantAttribute with special validation for only allowing positive values (float>0)
    """
    def _check(self, value):
        assert(0.0 < value)


# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
