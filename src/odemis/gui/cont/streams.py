# -*- coding: utf-8 -*-
"""
Created on 26 Sep 2012

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

import logging

from wx.lib.pubsub import pub

from odemis.gui import instrmodel, comp
from odemis.gui.instrmodel import STATE_OFF, STATE_PAUSE, STATE_ON

# stream controller:
# create the default streams when a part of the microscope is turned on, and
#  create a corresponding stream panel in the stream bar. (when part is turned
#  off, stream stays)
# ensures the right "Add XXX stream" stream panels are available in the
#  "Add stream" button
# on stream remove: contacted to remove the stream from the layers and the
#   list
# on microscope off: pause (set .updated to False) every stream which uses
#  this microscope
# TODO: how to prevent the user from turning on camera/light again from the
#   stream panel when the microscope is off? => either stream panel "update"
#   icon is disabled/enable (decided by the stream controller), or the event
#   handler checks first that the appropriate microscope is On or Off.
# the stream panels directly update the VA's

# all the stream types related to optical
OPTICAL_STREAMS = (instrmodel.FluoStream,
                   instrmodel.BrightfieldStream,
                   instrmodel.StaticStream)

# all the stream types related to electron microscope
EM_STREAMS = (instrmodel.SEMStream, instrmodel.StaticStream)

class StreamController(object):
    """
    Manages the insertion/suppression of streams (with their corresponding
    stream panels in the stream bar), and the de/activation of the
    streams when the microscope is turned on/off.
    """

    def __init__(self, microscope_model, stream_bar):
        """
        microscope (GUIMicroscope): the representation of the microscope Model
        stream_bar (StreamBar): an empty stream panel
        """
        self.microscope = microscope_model
        self._stream_bar = stream_bar
        self.setMicroscope(self.microscope)
        self._scheduler_subscriptions = {} # stream -> callable

        # TODO probably need a lock to access it correctly
        self._streams_to_restart = set() # streams to be restarted when turning on again

        # TODO remove the actions when microscope goes off
        if stream_bar.btn_add_stream:
            self._createAddStreamActions()

        # On the first time, we'll create the streams, to be nice to the user
        self._opticalWasTurnedOn = False
        self._semWasTurnedOn = False

        self.microscope.opticalState.subscribe(self.onOpticalState)
        self.microscope.emState.subscribe(self.onEMState)

        pub.subscribe(self.remove_stream, 'stream.remove')

    def optical_was_turned_on(self):
        return self._opticalWasTurnedOn

    def sem_was_turned_on(self):
        return self._semWasTurnedOn

    def _createAddStreamActions(self):
        """
        Create the possible "add stream" actions according to the current
        microscope.
        To be executed only once, at initialisation.
        """
        # Basically one action per type of stream

        # First: Fluorescent stream (for dyes)
        if (self.microscope.light and self.microscope.light_filter
            and self.microscope.ccd):
            # TODO: how to know it's _fluorescent_ microscope?
            #  => multiple source? filter?
            self._stream_bar.add_action("Filtered colour",
                                    self.addFluo,
                                    self.optical_was_turned_on)

        # Bright-field
        if self.microscope.light and self.microscope.ccd:
            self._stream_bar.add_action("Bright-field",
                                    self.addBrightfield,
                                    self.optical_was_turned_on)

        # SED
        if self.microscope.ebeam and self.microscope.sed:
            self._stream_bar.add_action("Secondary electrons",
                                    self.addSEMSED,
                                    self.sem_was_turned_on)


    def addFluo(self, add_to_all_views=False):
        """
        Creates a new fluorescence stream and a stream panel in the stream bar
        returns (StreamPanel): the panel created
        """
        # Find a name not already taken
        existing_names = [s.name.value for s in self.microscope.streams]
        for i in range(1000):
            name = "Filtered colour %d" % i
            if not name in existing_names:
                break

        stream = instrmodel.FluoStream(name,
                  self.microscope.ccd, self.microscope.ccd.data,
                  self.microscope.light, self.microscope.light_filter)
        return self._addStream(stream, comp.stream.DyeStreamPanel, add_to_all_views)

    def addBrightfield(self, add_to_all_views=False):
        """
        Creates a new brightfield stream and panel in the stream bar
        returns (StreamPanel): the stream panel created
        """
        stream = instrmodel.BrightfieldStream("Bright-field",
                  self.microscope.ccd, self.microscope.ccd.data,
                  self.microscope.light)
        return self._addStream(stream, comp.stream.StandardStreamPanel, add_to_all_views)

    def addSEMSED(self, add_to_all_views=False):
        """
        Creates a new SED stream and panel in the stream bar
        returns (StreamPanel): the panel created
        """
        stream = instrmodel.SEMStream("Secondary electrons",
                  self.microscope.sed, self.microscope.sed.data,
                  self.microscope.ebeam)
        return self._addStream(stream, comp.stream.StandardStreamPanel, add_to_all_views)

    def addStatic(self, name, image, cls=instrmodel.StaticStream, add_to_all_views=False, ):
        """
        Creates a new static stream and panel in the stream bar
        Note: only for debugging/testing
        name (string)
        image (InstrumentalImage)
        cls (class of Stream)
        returns (StreamPanel): the panel created
        """
        stream = cls(name, image)
        return self._addStream(stream, comp.stream.StandardStreamPanel, add_to_all_views)


    def _addStream(self, stream, spanel_cls, add_to_all_views=False):
        """
        Adds a stream.

        stream (Stream): the new stream to add
        spanel_cls (class): the type of stream panel to create
        add_to_all_views (boolean): if True, add the stream to all the compatible
          views, otherwise add only to the current view
        returns the StreamPanel of subclass 'spanel_cls' that was created
        """
        self.microscope.streams.add(stream)
        if add_to_all_views:
            for _, v in self.microscope.views.items():
                if isinstance(stream, v.stream_classes):
                    v.addStream(stream)
        else:
            v = self.microscope.focussedView.value
            if isinstance(stream, v.stream_classes):
                logging.warning("Adding stream incompatible with the current view")
            v.addStream(stream)

        # TODO create a StreamScheduler
        # call it like self._scheduler.addStream(stream)
        # create an adapted subscriber for the scheduler
        def detectUpdate(updated):
            self._onStreamUpdate(stream, updated)

        self._scheduler_subscriptions[stream] = detectUpdate
        stream.updated.subscribe(detectUpdate)

        # show the stream right now
        stream.updated.value = True

        spanel = spanel_cls(self._stream_bar, stream, self.microscope)

        show = isinstance(spanel.stream,
                          self._microscope.focussedView.value.stream_classes)
        self._stream_bar.add_stream(spanel, show)

        logging.debug("Sending stream.ctrl.added message")
        pub.sendMessage('stream.ctrl.added',
                        streams_present=True,
                        streams_visible=self._has_visible_streams())

        return spanel

    def duplicate(self, stream_bar):
        """ Create a new Stream controller with the same streams as are visible
        in this controller.

        :return StreamController:

        """

        # Note: self.microscope already has all the streams it needs, so we only
        # need to duplicate the stream panels in the actual StreamBar widget

        new_controller = StreamController(self.microscope, stream_bar)

        for sp in self._stream_bar.stream_panels:
            # TODO: temporary 'pause' should be removed when all handling has
            # been passed to scheduler. IMPORTANT: the pausing of the streams
            # should be done before they are duplicated!
            sp.pause()
            stream_panel = sp.__class__(stream_bar,
                                        sp.stream,
                                        self.microscope)
            # Used Streams can always be shown
            stream_bar.add_stream(stream_panel, True)
            stream_panel.to_acquisition_mode()

        return new_controller


    # === VA handlers

    def _onView(self, view):
        """
        Called when the current view changes
        """

        if not view:
            return

        # import sys
        # print sys.getrefcount(self)

        # hide/show the stream panels which are compatible with the view
        allowed_classes = view.stream_classes
        for e in self._stream_bar.stream_panels:
            e.Show(isinstance(e.stream, allowed_classes))
        # self.Refresh()
        self._stream_bar._fitStreams()

        # update the "visible" icon of each stream panel to match the list
        # of streams in the view
        visible_streams = view.streams.getStreams()

        for e in self._stream_bar.stream_panels:
            e.setVisible(e.stream in visible_streams)

        logging.debug("Sending stream.ctrl message")
        pub.sendMessage('stream.ctrl',
                        streams_present=True,
                        streams_visible=self._has_visible_streams())

    def setMicroscope(self, microscope):
        self._microscope = microscope
        self._microscope.focussedView.subscribe(self._onView, init=True)

    # def __del__(self):
    #     logging.debug("%s Desctructor", self.__class__.__name__)
    #     #self._microscope.focussedView.unsubscribe(self._onView)

    def _onStreamUpdate(self, stream, updated):
        """
        Called when a stream "updated" state changes
        """
        # This is a stream scheduler:
        # * "updated" streams are the streams to be scheduled
        # * a stream becomes "active" when it's currently acquiring
        # * when a stream is just set to be "updated" (by the user) it should
        #   be scheduled as soon as possible

        # Two versions:
        # * Manual: incompatible streams are forced non-updated
        # * Automatic: incompatible streams are switched active from time to time

        # TODO there are two difficulties:
        # * know which streams are incompatible with each other. Only compatible
        #   streams can be acquiring concurrently. As an approximation, it is
        #   safe to assume every stream is incompatible with every other one.
        # * in automatic mode only) detect when we can switch to a next stream
        #   => current stream should have acquired at least one picture, and
        #   it should not be changed too often due to overhead in hardware
        #   configuration changes.

        # For now we do very basic scheduling: manual, considering that every
        # stream is incompatible

        if not updated:
            stream.active.value = False
            # the other streams might or might not be updated, we don't care
        else:
            # make sure that every other streams is not updated
            for s in self._scheduler_subscriptions:
                if s != stream:
                    s.updated.value = False
            # activate this stream
            stream.active.value = True

    def onOpticalState(self, state):
        # only called when it changes
        if state == STATE_OFF or state == STATE_PAUSE:
            self._pauseStreams(instrmodel.OPTICAL_STREAMS)
        elif state == STATE_ON:
            if not self._opticalWasTurnedOn:
                self._opticalWasTurnedOn = True
                self.addBrightfield(add_to_all_views=True)

            self._startStreams(instrmodel.OPTICAL_STREAMS)

    def onEMState(self, state):
        if state == STATE_OFF or state == STATE_PAUSE:
            self._pauseStreams(instrmodel.EM_STREAMS)
        elif state == STATE_ON:
            if not self._semWasTurnedOn:
                self._semWasTurnedOn = True
                if self.microscope.sed:
                    self.addSEMSED(add_to_all_views=True)

            self._startStreams(instrmodel.EM_STREAMS)


    def _pauseStreams(self, classes):
        """
        Pause (deactivate and stop updating) all the streams of the given class
        """
        for s in self.microscope.streams:
            if isinstance(s, classes):
                if s.updated.value:
                    self._streams_to_restart.add(s)
                    s.active.value = False
                    s.updated.value = False
                    # TODO also disable stream panel "update" button?


    def _startStreams(self, classes):
        """
        (Re)start (activate) streams that are related to the classes
        """
        for s in self.microscope.streams:
            if (s in self._streams_to_restart and isinstance(s, classes)):
                self._streams_to_restart.remove(s)
                s.updated.value = True
                # it will be activated by the stream scheduler


    def remove_stream(self, stream):
        """
        Removes the given stream.
        stream (Stream): the stream to remove
        Note: the stream panel is to be destroyed separately via the stream_bar
        It's ok to call if the stream has already been removed
        """
        self._streams_to_restart.discard(stream)
        self.microscope.streams.discard(stream)

        # don't schedule any more
        stream.active.value = False
        stream.updated.value = False
        if stream in self._scheduler_subscriptions:
            callback = self._scheduler_subscriptions.pop(stream)
            stream.updated.unsubscribe(callback)

        # Remove from the views
        for v in [v for v in self.microscope.views.itervalues()]:
            v.removeStream(stream)

        logging.debug("Sending stream.ctrl.removed message")
        pub.sendMessage('stream.ctrl.removed',
                        streams_present=self._has_streams(),
                        streams_visible=self._has_visible_streams())

    def _has_streams(self):
        return len(self._stream_bar.stream_panels) > 0

    def _has_visible_streams(self):
        return any(s.IsShown() for s in self._stream_bar.stream_panels)

    def get_stream_panels(self):
        return self._stream_bar.get_stream_panels()

