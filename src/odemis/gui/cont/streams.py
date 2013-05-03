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

from odemis.gui import comp, instrmodel, model
from odemis.gui.instrmodel import STATE_OFF, STATE_PAUSE, STATE_ON
from odemis.gui.model import SPECTRUM_STREAMS

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


class StreamController(object):
    """
    Manages the insertion/suppression of streams (with their corresponding
    stream panels in the stream bar), and the de/activation of the
    streams when the microscope is turned on/off.
    """

    def __init__(self, microscope_model, stream_bar):
        """
        microscope_model (MicroscopeModel): the representation of the microscope Model
        stream_bar (StreamBar): an empty stream panel
        """
        self._interface_model = microscope_model
        self._stream_bar = stream_bar

        self._scheduler_subscriptions = {} # stream -> callable

        # TODO probably need a lock to access it correctly
        # streams to be restarted when turning on again
        self._streams_to_restart_opt = set()
        self._streams_to_restart_em = set()

        # TODO remove the actions when microscope goes off
        if stream_bar.btn_add_stream:
            self._createAddStreamActions()

        # On the first time, we'll create the streams, to be nice to the user
        self._opticalWasTurnedOn = False
        self._semWasTurnedOn = False

        if hasattr(self._interface_model, 'opticalState'):
            self._interface_model.opticalState.subscribe(self.onOpticalState)

        if hasattr(self._interface_model, 'emState'):
            self._interface_model.emState.subscribe(self.onEMState)

        self._interface_model.focussedView.subscribe(self._onView, init=True)
        pub.subscribe(self.removeStream, 'stream.remove')

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
        if (self._interface_model.light and self._interface_model.light_filter
            and self._interface_model.ccd):
            # TODO: how to know it's _fluorescent_ microscope?
            #  => multiple source? filter?
            self._stream_bar.add_action("Filtered colour",
                                    self.addFluo,
                                    self.optical_was_turned_on)

        # Bright-field
        if self._interface_model.light and self._interface_model.ccd:
            self._stream_bar.add_action("Bright-field",
                                    self.addBrightfield,
                                    self.optical_was_turned_on)

        # SED
        if self._interface_model.ebeam and self._interface_model.sed:
            self._stream_bar.add_action("Secondary electrons",
                                    self.addSEMSED,
                                    self.sem_was_turned_on)


    def addFluo(self, add_to_all_views=False):
        """
        Creates a new fluorescence stream and a stream panel in the stream bar
        returns (StreamPanel): the panel created
        """
        # Find a name not already taken
        existing_names = [s.name.value for s in self._interface_model.streams]
        for i in range(1000):
            name = "Filtered colour %d" % i
            if not name in existing_names:
                break
        else:
            logging.error("Failed to find a new unique name for stream")
            name = "Filtered colour"

        stream = model.stream.FluoStream(name,
                  self._interface_model.ccd, self._interface_model.ccd.data,
                  self._interface_model.light, self._interface_model.light_filter)
        return self._addStream(stream, comp.stream.DyeStreamPanel, add_to_all_views)

    def addBrightfield(self, add_to_all_views=False):
        """
        Creates a new brightfield stream and panel in the stream bar
        returns (StreamPanel): the stream panel created
        """
        stream = model.stream.BrightfieldStream("Bright-field",
                  self._interface_model.ccd, self._interface_model.ccd.data,
                  self._interface_model.light)
        return self._addStream(stream, comp.stream.SecomStreamPanel, add_to_all_views)

    def addSEMSED(self, add_to_all_views=False):
        """
        Creates a new SED stream and panel in the stream bar
        returns (StreamPanel): the panel created
        """
        stream = model.stream.SEMStream("Secondary electrons",
                  self._interface_model.sed, self._interface_model.sed.data,
                  self._interface_model.ebeam)
        return self._addStream(stream, comp.stream.SecomStreamPanel, add_to_all_views)

    def addSpectrumStream(self):
        """ Method not needed/used """
        stream = model.stream.SpectrumStream(
                    "Spectrometer",
                    self._interface_model.spccd,
                    self._interface_model.spccd.data,
                    self._interface_model.ebeam)
        return self._addStream(stream, comp.stream.BandwidthStreamPanel)

    def addStatic(self, name, image,
                  cls=model.stream.StaticStream, add_to_all_views=False):
        """
        Creates a new static stream and panel in the stream bar
        Note: only for debugging/testing

        :param name: (string)
        :param image: (InstrumentalImage)
        :param cls: (class of Stream)
        :param returns: (StreamPanel): the panel created
        """
        stream = cls(name, image)
        return self.addStream(stream, add_to_all_views)

    def _addStream(self, stream, spanel_cls, add_to_all_views=False):
        """
        Adds a stream.

        stream (Stream): the new stream to add
        spanel_cls (class): the type of stream panel to create
        add_to_all_views (boolean): if True, add the stream to all the compatible
          views, otherwise add only to the current view
        returns the StreamPanel of subclass 'spanel_cls' that was created
        """
        self._interface_model.streams.add(stream)
        if add_to_all_views:
            for v in self._interface_model.views:
                if isinstance(stream, v.stream_classes):
                    v.addStream(stream)
        else:
            v = self._interface_model.focussedView.value
            if isinstance(stream, v.stream_classes):
                logging.warning("Adding stream incompatible with the current view")
            v.addStream(stream)

        # TODO create a StreamScheduler
        # call it like self._scheduler.addStream(stream)
        self._scheduleStream(stream)

        spanel = spanel_cls(self._stream_bar, stream, self._interface_model)

        show = isinstance(
                    spanel.stream,
                    self._interface_model.focussedView.value.stream_classes)
        self._stream_bar.add_stream(spanel, show)

        logging.debug("Sending stream.ctrl.added message")
        pub.sendMessage('stream.ctrl.added',
                        streams_present=True,
                        streams_visible=self._has_visible_streams())

        return spanel

    def addStream(self, stream, add_to_all_views=False):
        """ Create a stream entry for the given existing stream
        Will pick the right panel fitting the stream type.

        :return StreamPanel: the panel created for the stream
        """
        # find the right panel type
        if isinstance(stream, model.stream.FluoStream):
            cls = comp.stream.DyeStreamPanel
        elif isinstance(stream, SPECTRUM_STREAMS):
            cls = comp.stream.BandwithStreamPanel
        else:
            cls = comp.stream.SecomStreamPanel

        return self._addStream(stream, cls, add_to_all_views)

    def addStreamForAcquisition(self, stream):
        """ Create a stream entry for the given existing stream, adapted to ac

        :return StreamPanel:

        """
        # TODO: generalise addStream() to support this case too
        # find the right panel type
        if isinstance(stream, model.stream.FluoStream):
            cls = comp.stream.DyeStreamPanel
        else:
            cls = comp.stream.SecomStreamPanel

        sp = cls(self._stream_bar, stream, self._interface_model)
        self._stream_bar.add_stream(sp, True)
        sp.to_acquisition_mode()

        return sp

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
        visible_streams = view.stream_tree.getStreams()

        for e in self._stream_bar.stream_panels:
            e.setVisible(e.stream in visible_streams)

        logging.debug("Sending stream.ctrl message")
        pub.sendMessage('stream.ctrl',
                        streams_present=True,
                        streams_visible=self._has_visible_streams())


    # def __del__(self):
    #     logging.debug("%s Desctructor", self.__class__.__name__)
    #     #self._interface_model.focussedView.unsubscribe(self._onView)

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
            stream.is_active.value = False
            # the other streams might or might not be updated, we don't care
        else:
            # make sure that every other streams is not updated
            for s in self._scheduler_subscriptions:
                if s != stream:
                    s.should_update.value = False
            # activate this stream
            stream.is_active.value = True

    def _scheduleStream(self, stream):
        """
        Add a stream to be managed by the update scheduler.
        stream (Stream): the stream to add. If it's already scheduled, it's fine.
        """
        # create an adapted subscriber for the scheduler
        def detectUpdate(updated, stream=stream):
            self._onStreamUpdate(stream, updated)

        self._scheduler_subscriptions[stream] = detectUpdate
        stream.should_update.subscribe(detectUpdate)

        # show the stream right now
        stream.should_update.value = True

    def _unscheduleStream(self, stream):
        """
        Remove a stream from being managed by the scheduler. It will also be 
        stopped from updating.
        stream (Stream): the stream to remove. If it's not currently scheduled,
          it's fine.
        """
        stream.is_active.value = False
        stream.should_update.value = False
        if stream in self._scheduler_subscriptions:
            callback = self._scheduler_subscriptions.pop(stream)
            stream.should_update.unsubscribe(callback)


    def onOpticalState(self, state):
        # only called when it changes
        if state == STATE_OFF or state == STATE_PAUSE:
            self._streams_to_restart_opt = self.pauseStreams(model.OPTICAL_STREAMS)
        elif state == STATE_ON:
            if not self._opticalWasTurnedOn:
                self._opticalWasTurnedOn = True
                self.addBrightfield(add_to_all_views=True)

            self.resumeStreams(self._streams_to_restart_opt)

    def onEMState(self, state):
        if state == STATE_OFF or state == STATE_PAUSE:
            self._streams_to_restart_em = self.pauseStreams(model.EM_STREAMS)
        elif state == STATE_ON:
            if not self._semWasTurnedOn:
                self._semWasTurnedOn = True
                if self._interface_model.sed:
                    self.addSEMSED(add_to_all_views=True)

            self.resumeStreams(self._streams_to_restart_em)


    def pauseStreams(self, classes=instrmodel.Stream):
        """
        Pause (deactivate and stop updating) all the streams of the given class
        classes (class or list of class): classes of streams that should be disabled
        returns (set of Stream): streams which were actually paused
        """
        streams = set() # stream paused
        for s in self._interface_model.streams:
            if isinstance(s, classes):
                if s.should_update.value:
                    streams.add(s)
                    s.is_active.value = False
                    s.should_update.value = False
                    # TODO also disable stream panel "update" button?

        return streams

    def resumeStreams(self, streams):
        """
        (Re)start (activate) streams
        streams (set of streams): Streams that will be resumed
        """
        for s in streams:
            s.should_update.value = True
            # it will be activated by the stream scheduler


    def removeStream(self, stream):
        """
        Removes the given stream.
        stream (Stream): the stream to remove
        Note: the stream panel is to be destroyed separately via the stream_bar
        It's ok to call if the stream has already been removed
        """
        # don't schedule any more
        self._unscheduleStream(stream)

        # Remove from the views
        for v in self._interface_model.views:
            v.removeStream(stream)

        self._streams_to_restart_opt.discard(stream)
        self._streams_to_restart_em.discard(stream)
        self._interface_model.streams.discard(stream)

        logging.debug("Sending stream.ctrl.removed message")
        pub.sendMessage('stream.ctrl.removed',
                        streams_present=self._has_streams(),
                        streams_visible=self._has_visible_streams())

    def clear(self):
        """
        Remove all the streams (from the model and the GUI)
        """
        # We could go for each stream panel, and call removeStream(), but it's
        # as simple to reset all the lists

        # clear the graphical part
        while self._stream_bar.stream_panels:
            spanel = self._stream_bar.stream_panels[0]
            self._stream_bar.remove_stream_panel(spanel)

        # clear the interface model
        # (should handle cases where a new stream is added simultaneously)
        while self._interface_model.streams:
            stream = self._interface_model.streams.pop()
            self._unscheduleStream(stream)

            # Remove from the views
            for v in self._interface_model.views:
                v.removeStream(stream)

        # reset lists
        self._streams_to_restart_opt = set()
        self._streams_to_restart_em = set()

        if self._has_streams() or self._has_visible_streams():
            logging.warning("Failed to remove all streams")

        logging.debug("Sending stream.ctrl.removed message")
        pub.sendMessage('stream.ctrl.removed',
                        streams_present=False,
                        streams_visible=False)

    def _has_streams(self):
        return len(self._stream_bar.stream_panels) > 0

    def _has_visible_streams(self):
        return any(s.IsShown() for s in self._stream_bar.stream_panels)

