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
from odemis.gui import instrmodel, comp
from odemis.gui.instrmodel import STATE_OFF, STATE_PAUSE, STATE_ON


# stream controller:
# create the default streams when a part of the microscope is turned on, and
#  create a corresponding stream entry in the panel. (when part is turned
#  off, stream stays)
# ensures the right "Add XXX stream" entries are available in the "Add stream"
#   button
# on stream remove: contacted to remove the stream from the layers and the 
#   list
# on microscope off: pause (set .updated to False) every stream which uses
#  this microscope
# TODO: how to prevent the user from turning on camera/light again from the
#   stream entry when the microscope is off? => either stream entry "update"
#   icon is disabled/enable (decided by the stream controller), or the event
#   handler checks first that the appropriate microscope is On or Off.
# the stream entries directly update the VA's 


# all the stream types related to optical
OPTICAL_STREAMS = (instrmodel.FluoStream, instrmodel.BrightfieldStream)
# all the stream types related to electron microscope
EM_STREAMS = (instrmodel.SEMStream)

class StreamController(object):
    '''
    Manages the insertion/suppression of streams (with their corresponding
    entries in the panel), and the de/activation of the streams when the 
    microscope is turned on/off.
    '''

    def __init__(self, livegui, spanel):
        '''
        microscope (MicroscopeGUI): the representation of the microscope GUI
        spanel (StreamPanel): an empty stream panel
        '''
        self._livegui = livegui
        self._spanel = spanel
        self._spanel.setMicroscope(self._livegui, self)
        self._scheduler_subscriptions = {} # stream -> callable
        
        # TODO probably need a lock to access it correctly
        self._streams_to_restart = set() # streams to be restarted when turning on again
    
        # TODO remove the actions when microscope goes off
        self._createAddStreamActions()
    
        # On the first time, we'll create the streams, to be nice to the user
        self._opticalWasTurnedOn = False
        self._semWasTurnedOn = False 
        
        self._livegui.opticalState.subscribe(self.onOpticalState)
        self._livegui.emState.subscribe(self.onEMState)
        
    def _createAddStreamActions(self):
        """
        Create the possible "add stream" actions according to the current 
        microscope.
        To be executed only once, at initialisation.
        """
        # Basically one action per type of stream
        
        # First: Fluorescent stream (for dyes)
        if (self._livegui.light and self._livegui.light_filter
            and self._livegui.ccd):
            # TODO: how to know it's _fluorescent_ microscope?
            #  => multiple source? filter?
            self._spanel.add_action("Filtered colour", self.addFluo)
        
        # Bright-field
        if self._livegui.light and self._livegui.ccd:
            self._spanel.add_action("Bright-field", self.addBrightfield)

        # SED
        if self._livegui.ebeam and self._livegui.sed:
            self._spanel.add_action("Secondary electrons", self.addSEMSED)
    
    
    def addFluo(self):
        """
        Creates a new fluorescence stream and entry into the stream panel
        returns (StreamPanelEntry): the entry created
        """
        # Find a name not already taken
        existing_names = [s.name.value for s in self._livegui.streams]
        for i in range(1000):
            name = "Filtered colour %d" % i
            if not name in existing_names:
                break
        
        stream = instrmodel.FluoStream(name,
                  self._livegui.ccd, self._livegui.ccd.data,
                  self._livegui.light, self._livegui.light_filter)
        return self._addStream(stream, comp.stream.CustomStreamPanelEntry)
        
    def addBrightfield(self):
        """
        Creates a new brightfield stream and entry into the stream panel
        returns (StreamPanelEntry): the entry created
        """
        stream = instrmodel.BrightfieldStream("Bright-field",
                  self._livegui.ccd, self._livegui.ccd.data,
                  self._livegui.light)
        return self._addStream(stream, comp.stream.FixedStreamPanelEntry)
    
    def addSEMSED(self):
        """
        Creates a new SED stream and entry into the stream panel
        returns (StreamPanelEntry): the entry created
        """
        stream = instrmodel.SEMStream("Secondary electrons",
                  self._livegui.sed, self._livegui.sed.data,
                  self._livegui.ebeam)
        return self._addStream(stream, comp.stream.FixedStreamPanelEntry)

    def _addStream(self, stream, entry_cls):
        """
        Adds a stream.
        stream (Stream): the new stream to add
        entry_cls (class): the type of stream entry to create
        returns the entry created
        """
        self._livegui.streams.add(stream)
        self._livegui.currentView.value.addStream(stream)
        
        # TODO create a StreamScheduler
        # call it like self._scheduler.addStream(stream)
        # create an adapted subscriber for the scheduler
        def detectUpdate(updated):
            self._onStreamUpdate(stream, updated)
        self._scheduler_subscriptions[stream] = detectUpdate
        stream.updated.subscribe(detectUpdate)

        # show the stream right now
        stream.updated.value = True
        
        entry = entry_cls(self._spanel, stream, self._livegui)
        self._spanel.add_stream(entry)
        return entry        

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
            self._pauseStreams(OPTICAL_STREAMS)
        elif state == STATE_ON:
            if not self._opticalWasTurnedOn:
                self._opticalWasTurnedOn = True
                self.addBrightfield()
                # TODO need to hide if the view is not the right one
        
            self._startStreams(OPTICAL_STREAMS)
    
    def onEMState(self, state):
        if state == STATE_OFF or state == STATE_PAUSE:
            self._pauseStreams(EM_STREAMS)
        elif state == STATE_ON:
            if not self._semWasTurnedOn:
                self._semWasTurnedOn = True
                if self._livegui.sed:
                    self.addSEMSED()
                # TODO need to hide if the view is not the right one
        
            self._startStreams(OPTICAL_STREAMS)

        
    def _pauseStreams(self, classes):
        """
        Pause (deactivate and stop updating) all the streams of the given class
        """
        for s in self._livegui.streams:
            if isinstance(s, classes):
                if s.updated.value:
                    self._streams_to_restart.add(s)
                    s.active.value = False
                    s.updated.value = False
                    # TODO also disable entry "update" button?
            

    def _startStreams(self, classes):
        """
        (Re)start (activate) streams that are related to the classes
        """
        for s in self._livegui.streams:
            if (s in self._streams_to_restart and isinstance(s, classes)):
                self._streams_to_restart.remove(s)
                s.updated.value = True
                # it will be activated by the stream scheduler

        
    def removeStream(self, stream):
        """
        Removes a stream. 
        stream (Stream): the stream to remove
        Note: the stream entry is to be destroyed separately via the spanel
        It's ok to call if the stream has already been removed 
        """
        self._streams_to_restart.discard(stream)
        self._livegui.streams.discard(stream)

        stream.active.value = False
        stream.updated.value = False
        
        # don't schedule any more
        if stream in self._scheduler_subscriptions:
            callback = self._scheduler_subscriptions.pop(stream)
            stream.updated.unsubscribe(callback)
        
        # TODO: shall we delegate this to the view controller?
        # FIXME: need lock to modify views? 
        for v in self._livegui.views:
            v.removeStream(stream)
