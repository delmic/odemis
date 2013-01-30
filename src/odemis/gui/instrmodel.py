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
from odemis.gui.util.units import readable_str
from odemis.model import VigilantAttribute, MD_POS, MD_PIXEL_SIZE, \
    MD_SENSOR_PIXEL_SIZE
from odemis.model._vattributes import FloatContinuous
import json
import logging
import numpy
import threading
import time

#logging.getLogger().setLevel(logging.DEBUG) # for the messages of dye database to appear

# List of places to look for the database file
FLUODB_PATHS = ["/usr/share/odemis/fluodb/",
                "./install/linux/usr/share/odemis/fluodb/"]
def LoadDyeDatabase():
    """
    Try to fill the dye database from known files
    returns (boolean): True if a database was found, false otherwise
    Note: it uses a cached version of the Fluorophores.org JSON database
    """

    # For the API see doc/fluorophores-api.txt
    index = None
    basedir = None
    for p in FLUODB_PATHS:
        try:
            findex = open(p + "environment/index.json")
        except IOError:
            # can't find this file, try the next one
            continue
        index = json.load(findex)
        basedir = p
        break

    if index is None:
        return False

    # Load the main excitation and emission peak for each environment
    # For each environment, download it
    for eid, e in index.items():
        # find the names (of the substance)
        names = set()
        s = e["substance"]
        names.add(s["common_name"].strip()) # in case loading the substance file fails
        nsid = int(s["substance_id"])
        sname = basedir + "substance/%d.json" % nsid
        try:
            fs = open(sname, "r")
            fulls = json.load(fs)
            for n in fulls["common_names"]:
                names.add(n.strip())
        except (IOError, ValueError):
            # no such file => no problem
            logging.debug("Failed to open %s", sname)
        names.discard("") # just in case some names are empty
        if not names:
            logging.debug("Skipping environment %d which has substance without name", eid)

        # find the peaks
        xpeaks = e["excitation_max"]
        epeaks = e["emission_max"]
        if len(xpeaks) == 0 or len(epeaks) == 0:
            # not enough information to be worthy
            continue
        xwl = xpeaks[0] * 1e-9 # m
        ewl = epeaks[0] * 1e-9 # m

        # Note: if two substances have the same name -> too bad, only the last
        # one will be in our database. (it's not a big deal, as it's usually
        # just duplicate entries)
        # TODO: if the peaks are really different, and the solvent too, then
        # append the name of the solvent in parenthesis.
        for n in names:
            if n in DyeDatabase:
                logging.debug("Dye database already had an entry for dye %s", n)
            DyeDatabase[n] = (xwl, ewl)

    # TODO: also de-duplicate names in a case insensitive way

    logging.info("Loaded %d dye names from the database.", len(DyeDatabase))
    return True

# Simple dye database, that will be filled in at initialisation, if there is a
# database file available
# string (name) -> 2-tuple of float (excitation peak wl, emission peak wl in m)
# TODO: Should support having multiple peaks, orderer by strength
DyeDatabase = None

# Load the database the first time the module is imported
if DyeDatabase is None:
    DyeDatabase = {} # This ensures we try only once
    start = time.time()
    try:
        # TODO: do it in a thread so that it doesn't slow down the loading?
        # Or preparse the database so that's very fast to load
        # For now, it seems to take 0.3 s => so let's say it's not needed
        result = LoadDyeDatabase()
    except:
        logging.exception("Failed to load the fluorophores database.")
    else:
        if not result:
            logging.info("No fluorophores database found.")

    load_time = time.time() - start
    logging.debug("Dye database loading took %g s", load_time)

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
    TODO: Rename, since the current name GUIMicroscope is confusing because
    no actual gui controls are represented by or contained withing this class.
    Suggested name: MicroscopeModel

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
            logging.info("no focus actuator found in the microscope")

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
    WARNING_EXCITATION_NOT_OPT = ("The excitation wavelength selected cannot "
                                  "be optimally generated by the hardware.")
    WARNING_EXCITATION_IMPOSSIBLE = ("The excitation wavelength selected "
                                     "cannot be generated by the hardware.")
    WARNING_EMISSION_NOT_OPT = ("The emission wavelength selected cannot be "
                                "optimally detected by the hardware.")
    WARNING_EMISSION_IMPOSSIBLE = ("The emission wavelength selected cannot be "
                                   "detected by the hardware.")

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

        self.auto_bc.subscribe(self.onAutoBC)
        self.contrast.subscribe(self.onBrightnessContrast)
        self.brightness.subscribe(self.onBrightnessContrast)

        # list of warnings to display to the user
        # TODO should be a set
        self.warnings = model.ListVA([]) # should only contains WARNING_*

    def estimateAcquisitionTime(self):
        """
        Estimate the time it will take to acquire one image with the current
        settings of the detector and emitter.
        returns (0 <= float): approximate time in s that it will take
        """
        # default implementation is very hopeful, but not exactly 0, to take
        # into account infrastructure overhead.
        return 0.1

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
        # set([1]).add(2) returns None (because add() never returns anything)
        new_warnings = set(self.warnings.value) | set(warning)
        self.warnings.value = list(new_warnings)

    def onActive(self, active):
        # Called only when the value _changes_
        if active:
            logging.debug("Subscribing to dataflow of component %s", self._detector.name)
            if not self.updated.value:
                logging.warning("Trying to activate stream while it's not supposed to update")
            self._dataflow.subscribe(self.onNewImage)
        else:
            logging.debug("Unsubscribing from dataflow of component %s", self._detector.name)
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

        im = util.img.DataArray2wxImage(data, self._depth, brightness, contrast, tint)
        im.InitAlpha() # it's a different buffer so useless to do it in numpy

        try:
            pos = data.metadata[MD_POS]
        except KeyError:
            logging.warning("position of image unknown")
            pos = (0, 0)

        try:
            mpp = data.metadata[MD_PIXEL_SIZE][0]
        except KeyError:
            logging.warning("pixel density of image unknown")
            # Hopefully it'll be within the same magnitude
            mpp = data.metadata[MD_SENSOR_PIXEL_SIZE][0] / 10.

        self.image.value = InstrumentalImage(im, mpp, pos)

    def onAutoBC(self, enabled):
        if len(self.raw) == 0:
            return  # no image acquired yet

        # if changing to manual: need to set the current (automatic) B/C
        if enabled == False:
            b, c = util.img.FindOptimalBC(self.raw[0], self._depth)
            self.brightness.value = b * 100
            self.contrast.value = c * 100
        else:
            # B/C might be different from the manual values => redisplay
            self._updateImage()

    def onBrightnessContrast(self, unused):
        # called whenever brightness/contrast changes
        # => needs to recompute the image (but not too often, so we do it in a timer)

        if len(self.raw) == 0:
            return  # no image acquired yet
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
    def __init__(self, name, detector, dataflow, emitter):
        Stream.__init__(self, name, detector, dataflow, emitter)

        try:
            self._prevDwellTime = emitter.dwellTime.value
            emitter.dwellTime.subscribe(self.onDwellTime)
        except AttributeError:
            # if emitter has no dwell time -> no problem
            pass

    def estimateAcquisitionTime(self):
        # each pixel * dwell time + set up overhead

        try:
            res = list(self._emitter.resolution.value)
            # Typically there is few more pixels inserted at the beginning of each
            # line for the settle time of the beam. We guesstimate by justing adding
            # 1 pixel to each line
            if len(res) == 2:
                res[1] += 1
            else:
                logging.warning("Resolution of scanner is not 2 dimension, time estimation might be wrong")
            duration = self._emitter.dwellTime * numpy.prod(res) + 0.1

            return duration
        except:
            logging.exception("Exception while trying to estimate time of SEM acquisition")
            return Stream.estimateAcquisitionTime(self)

    def onActive(self, active):
        if active:
            # TODO if can blank => unblank
            pass
        Stream.onActive(self, active)

    def onDwellTime(self, value):
        # When the dwell time changes, the new value is only used on the next
        # acquisition. Assuming the change comes from the user (very likely),
        # then if the current acquisition would take a long time, cancel it, and
        # restart acquisition so that the new value is directly used. The main
        # goal is to avoid cases where user mistakenly put a 10+ s acquisition,
        # and it takes ages to get back to a faster acquisition. Note: it only
        # works if we are the only subscriber (but that's very likely).

        try:
            if self.active.value == False:
                # not acquiring => nothing to do
                return

            # approximate time for the current image acquisition
            res = self._emitter.resolution.value
            prevDuration = self._prevDwellTime * numpy.prod(res)

            if prevDuration < 1:
                # very short anyway, not worthy
                return

            # TODO: do this on a rate-limited fashion (now, or ~1s)
            # unsubscribe, and re-subscribe immediately
            self._dataflow.unsubscribe(self.onNewImage)
            self._dataflow.subscribe(self.onNewImage)

        finally:
            self._prevDwellTime = value

class CameraStream(Stream):
    """
    Abstract class representing all streams which have a digital camera as
    detector. Used to share time estimation mostly only.
    """
    def estimateAcquisitionTime(self):
        # exposure time + readout time * pixels (if CCD) + set-up time
        try:
            exp = self._detector.exposureTime.value
            res = self._detector.resolution.value
            try:
                readout = 1.0 / self._detector.readoutRate.value
            except AttributeError:
                # let's assume it's super fast
                readout = 0

            duration = exp + numpy.prod(res) * readout + 0.1
            return duration
        except TypeError:
            logging.exception("Exception while trying to estimate time of SEM acquisition")
            return Stream.estimateAcquisitionTime(self)

class BrightfieldStream(CameraStream):
    """
    Stream containing images obtained via optical brightfield illumination.
    It basically knows how to select white light and disable any filter.
    """

    def onActive(self, active):
        if active:
            self._setLightExcitation()
            # TODO do we need to have a special command to disable filter??
            #  or should it be disabled automatically by the other streams not using it?
            # self._setFilterEmission()
        Stream.onActive(self, active)

    # def _setFilterEmission(self):
    #     if not self._filter.band.readonly:
    #         raise NotImplementedError("Do not know how to change filter band")


    def _setLightExcitation(self):
        # TODO how to select white light??? We need a brightlight hardware?
        # Turn on all the sources? Does this always mean white?
        # At least we should set a warning if the final emission range is quite
        # different from the normal white spectrum
        em = [1 for e in self._emitter.emissions.value]
        self._emitter.emissions.value = em


class FluoStream(CameraStream):
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
        self.tint = model.ListVA(defaultTint, unit="RGB") # 3-tuple R,G,B
        self.tint.subscribe(self.onTint)

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

    def onTint(self, value):
        if len(self.raw) == 0:
            return  # no image acquired yet
        self._updateImage()

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
            logging.warning("Emission wavelength %s doesn't fit the filter",
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

class StaticSEMStream(StaticStream):
    """
    Same as a StaticStream, but considered a SEM stream
    """
    pass

# all the stream types related to optical
OPTICAL_STREAMS = (FluoStream, BrightfieldStream, StaticStream)
# all the stream types related to electron microscope
EM_STREAMS = (SEMStream, StaticSEMStream)

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
        self.merge_ratio = FloatContinuous(0.3, range=[0, 1], unit="")
        self.merge_ratio.subscribe(self._onMergeRatio)

        # Streams to display (can be considered in most cases a implementation detail)
        # Note: use addStream/removeStream for simple modifications
        self.streams = StreamTree(merge=self.merge_ratio.value)
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
            logging.warning("Adding incompatible stream %s to view %s", stream.name.value, self.name.value)

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
        self.operator = operator or util.img.Average

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

    def getImage(self, rect, mpp):
        """
        Returns an InstrumentalImage composed of all the current stream images.
        Precisely, it returns the output of a call to operator.
        rect (2-tuple of 2-tuple of float): top-left and bottom-right points in
          world position (m) of the area to draw
        mpp (0<float): density (meter/pixel) of the image to compute
        """
        # TODO: probably not so useful function, need to see what canvas
        #  it will likely need as argument a wx.Bitmap, and view rectangle
        #  that will define where to save the result

        # TODO: cache with the given rect and mpp and last update time of each image

        # create the arguments list for operator
        images = []
        for s in self.streams:
            if isinstance(s, Stream):
                images.append(s.image.value)
            elif isinstance(s, StreamTree):
                images.append(s.getImage(rect, mpp))


        return self.operator(images, rect, mpp, **self.kwargs)

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

class PositiveVA(VigilantAttribute):
    """
    VigilantAttribute with special validation for only allowing positive values (float>0)
    """
    def _check(self, value):
        assert(0.0 < value)


# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
