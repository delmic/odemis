# -*- coding: utf-8 -*-
"""
Created on 16 Feb 2012

@author: Éric Piel

Copyright © 2012-2013 Éric Piel, Delmic

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

import collections
import logging
import threading
import time

from odemis import model
from odemis.gui.model.stream import Stream, StreamTree
from odemis.model import FloatContinuous, VigilantAttribute, VA_EXCEPTIONS

# The different states of a microscope
STATE_OFF = 0
STATE_ON = 1
STATE_PAUSE = 2

# The different types of view layouts
VIEW_LAYOUT_ONE = 0 # one big view
VIEW_LAYOUT_22 = 1 # 2x2 layout
VIEW_LAYOUT_FULLSCREEN = 2 # Fullscreen view (not yet supported)


class MicroscopeModel(object):
    """ Represent a microscope directly for a graphical user interface.

    Provides direct reference to the HwComponents.
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
        self.light_filter = None # emission light filter for fluorescence micro.
        self.ebeam = None
        self.sed = None # secondary electron detector
        self.bsd = None # back-scatter electron detector
        self.spectrometer = None # spectrometer

        for d in microscope.detectors:
            if d.role == "ccd":
                self.ccd = d
            elif d.role == "se-detector":
                self.sed = d
            elif d.role == "bs-detector":
                self.bsd = d
            elif d.role == "spectrometer":
                self.spectrometer = d
        if not self.ccd and not self.sed and not self.bsd:
            msg = "no camera nor electron detector found in the microscope"
            raise Exception(msg)

        for a in microscope.actuators:
            if a.role == "stage":
                self.stage = a
                # TODO: viewports should subscribe to the stage
            elif a.role == "focus":
                self.focus = a
        if not self.stage and microscope.role == "secom":
            raise Exception("no stage found in the microscope")

        # it's not an error to not have focus
        if not self.focus:
            logging.info("No focus actuator found for the microscope")

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
                            logging.warning("Unknown light power value")
            elif e.role == "filter":
                self.light_filter = e
            elif e.role == "e-beam":
                self.ebeam = e

        if not self.light and not self.ebeam:
            raise Exception("No emitter found in the microscope")

        self.streams = set() # Streams available (handled by StreamController)

        # MicroscopeViews available, (handled by ViewController)
        # The ViewController cares about position (top left, etc),
        # MicroscopeModel cares about what's what.
        self.views = {
            "sem_view": None,
            "opt_view": None,
            "combo1_view": None,
            "combo2_view": None,
        }

        # The MicroscopeView currently focused
        self.focussedView = VigilantAttribute(None)

        layouts = set([VIEW_LAYOUT_ONE, VIEW_LAYOUT_22, VIEW_LAYOUT_FULLSCREEN])
        hw_states = set([STATE_OFF, STATE_ON, STATE_PAUSE])

        self.viewLayout = model.IntEnumerated(VIEW_LAYOUT_22, choices=layouts)

        self.opticalState = model.IntEnumerated(STATE_OFF, choices=hw_states)
        self.opticalState.subscribe(self.onOpticalState)

        self.emState = model.IntEnumerated(STATE_OFF, choices=hw_states)
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
        """ Immediately stops all movement on all axis """
        self.stage.stop()
        self.focus.stop()
        logging.info("Stopped motion on all axes")

    def onOpticalState(self, state):
        """ Event handler for when the state of the optical microscope changes
        """
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
        """ Event handler for when the state of the electron microscope changes
        """
        if state == STATE_OFF and self.ebeam:
            # TODO: actually turn off the ebeam and detector
            try:
                # TODO save the previous value
                # blank the ebeam
                self.ebeam.energy.value = 0
            except VA_EXCEPTIONS:
                # Too bad. let's just do nothing then.
                logging.debug("Ebeam doesn't support setting energy to 0")
        elif state == STATE_PAUSE and self.ebeam:
            try:
                # TODO save the previous value
                # blank the ebeam
                self.ebeam.energy.value = 0
            except VA_EXCEPTIONS:
                # Too bad. let's just do nothing then.
                logging.debug("Ebeam doesn't support setting energy to 0")

        elif state == STATE_ON and self.ebeam:
            try:
                # TODO use the previous value
                if hasattr(self.ebeam.energ, "choice"):
                    if isinstance(self.ebeam.energy.choices,
                                  collections.Iterable):
                        self.ebeam.energy.value = self.ebeam.energy.choices[1]
            except VA_EXCEPTIONS:
                # Too bad. let's just do nothing then (and hope it's on)
                logging.debug("Ebeam doesn't support setting energy")

class MicroscopeView(object):
    """ Represents a view from a microscope and ways to alter it.

    Basically, its "input" is a StreamTree and can request stage and focus move.
    It never computes the composited image from all the streams itself. It's up
    to other objects (e.g., the canvas) to ask the StreamTree for its latest
    image (the main goal of this scheme is to avoid computation when not needed).
    Similarly, the thumbnail is never automatically recomputed, but other
    objects can update it.
    """

    def __init__(self, name, stage=None,
                 focus0=None, focus1=None, stream_classes=None):
        """
        :param name (string): user-friendly name of the view
        :param stage (Actuator): actuator with two axes: x and y
        :param focus0 (Actuator): actuator with one axis: z. Can be None
        :param focus1 (Actuator): actuator with one axis: z. Can be None

        Focuses 0 and 1 are modified when changing focus respectively along the
        X and Y axis.

        stream_classes (None, or tuple of classes): all subclasses that the
        streams in this view can show (restriction is not technical, only for
        the user)

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

            # the current center of the view, which might be different from
            # the stage
            # TODO: we might need to have it on the MicroscopeModel, if all the
            # viewports must display the same location
            pos = self.stage_pos.value
            view_pos_init = (pos["x"], pos["y"])
        else:
            view_pos_init = (0, 0)

        self.view_pos = model.ListVA(view_pos_init, unit="m")

        # current density (meter per pixel, ~ scale/zoom level)
        # 10µm/px => ~large view of the sample
        self.mpp = FloatContinuous(10e-6, range=(10e-12, 1e-3), unit="m/px")

        # How much one image is displayed on the other one. Value used by
        # StreamTree
        self.merge_ratio = FloatContinuous(0.3, range=[0, 1], unit="")
        self.merge_ratio.subscribe(self._onMergeRatio)

        # Streams to display (can be considered an implementation detail in most
        # cases)
        # Note: use addStream/removeStream for simple modifications
        self.stream_tree = StreamTree(merge=self.merge_ratio.value)
        # Only modify with this lock acquired:
        # TODO: Is this the source of the intermittent locking of the GUI when
        # Streams are active? If so, is there another/better way?
        self._streams_lock = threading.Lock()

        # Last time the image of the view was changed. It's actually mostly
        # a trick to allow other parts of the GUI to know when the (theoretical)
        # composited image has changed.
        self.lastUpdate = model.FloatVA(time.time(), unit="s")
        # Last initialisation is done on the first image received
        self._has_received_image = False

        # a thumbnail version of what is displayed
        self.thumbnail = VigilantAttribute(None) # contains a wx.Image

        # TODO list of annotations to display
        self.show_crosshair = model.BooleanVA(True)

    def get_focus(self, i):
        return self._focus[i]

    def moveStageToView(self):
        """ Move the stage to the current view_pos

        :return: a future (that allows to know when the move is finished)

        Note: once the move is finished stage_pos will be updated (by the
        back-end)
        """

        if not self._stage:
            return

        # TODO: a way to know if it can do absolute move? => .capabilities!
        # if hasattr(self.stage, "moveAbs"):
        #     # absolute
        #     move = {"x": pos[0], "y": pos[1]}
        #     self._stage.moveAbs(move)
        # else:

        view_pos = self.view_pos.value
        # relative
        prev_pos = self.stage_pos.value
        move = {
            "x": view_pos[0] - prev_pos["x"],
            "y": view_pos[1] - prev_pos["y"]
        }

        return self._stage.moveRel(move)

        #    def onStagePos(self, pos):
        #        # we want to recenter the viewports whenever the stage moves
        #        # Not sure whether that's really the right way to do it though...
        #        # TODO: avoid it to move the view when the user is dragging the view
        #        #  => might require cleverness
        # self.view_pos = model.ListVA((pos["x"], pos["y"]), unit="m")

    def getStreams(self):
        """
        :returns [Stream]: list of streams that are displayed in the view

        Do not modify directly, use addStream(), and removeStream().
        Note: use .streams for getting the raw StreamTree
        """
        return self.stream_tree.getStreams()

    def addStream(self, stream):
        """
        Add a stream to the view. It takes care of updating the StreamTree
        according to the type of stream.
        stream (Stream): stream to add
        If the stream is already present, nothing happens
        """
        # check if the stream is already present
        if stream in self.stream_tree.getStreams():
            return

        if not isinstance(stream, self.stream_classes):
            logging.warning("Adding incompatible stream %s to view %s", stream.name.value, self.name.value)

        # Find out where the stream should go in the streamTree
        # FIXME: manage sub-trees, with different merge operations
        # For now we just add it to the list of streams, with the only merge operation possible
        with self._streams_lock:
            self.stream_tree.streams.append(stream)

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
            if not stream in self.stream_tree.getStreams():
                return

            # remove stream from the StreamTree()
            # TODO handle more complex trees
            self.stream_tree.streams.remove(stream)

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
        # It has effect only if the operator can do something with the "merge"
        # argument
        with self._streams_lock:
            self.stream_tree.kwargs["merge"] = ratio

        # just let everyone that the composited image has changed
        self.lastUpdate.value = time.time()
