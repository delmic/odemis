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

    def __init__(self, micgui, spanel):
        '''
        microscope (MicroscopeGUI): the representation of the microscope GUI
        spanel (StreamPanel): an empty stream panel
        '''
        self._microscope = micgui
        self._spanel = spanel
        # TODO probably need a lock to access it correctly
        self._streams_to_restart = set() # streams to be restarted when turning on again
    
        # TODO remove the actions when microscope goes off
        self._createAddStreamActions()
    
        # On the first time, we'll create the streams, to be nice to the user
        self._opticalWasTurnedOn = False
        self._semWasTurnedOn = False 
        
        micgui.opticalState.subscribe(self.onOpticalState)
        micgui.emState.subscribe(self.onEMState)
    
    def _createAddStreamActions(self):
        """
        Create the possible "add stream" actions according to the current 
        microscope.
        To be executed only once, at initialisation.
        """
        # Basically one action per type of stream
        
        # First: Fluorescent stream (for dyes)
        if (self._microscope.light and self._microscope.light_filter
            and self._microscope.ccd):
            # TODO: how to know it's _fluorescent_ microscope?
            #  => multiple source? filter?
            self._spanel.add_action("Filtered colour", self.addFluo)
        
        # Brightfield
        if self._microscope.light and self._microscope.ccd:
            self._spanel.add_action("Bright-field", self.addBrightfield)

        # SED
        if self._microscope.ebeam and self._microscope.sed:
            self._spanel.add_action("Secondary electrons", self.addSEMSED)
    
    
    # TODO automatically add new stream to the current view
    
    def addFluo(self):
        """
        Creates a new fluorescence stream and entry into the stream panel
        returns (StreamPanelEntry): the entry created
        """
        # Find a name not already taken
        existing_names = [s.name.value for s in self._microscope.streams]
        for i in range(1000):
            name = "Filtered colour %d" % i
            if not name in existing_names:
                break
        
        stream = instrmodel.FluoStream(name,
                  self._microscope.ccd, self._microscope.ccd.data,
                  self._microscope.light, self._microscope.light_filter)
        self._microscope.streams.add(stream)
        stream.updated.value = True
        
        entry = comp.stream.CustomStreamPanelEntry(self._spanel, stream)
        self._spanel.add_stream(entry)
        return entry
        
    def addBrightfield(self):
        """
        Creates a new brightfield stream and entry into the stream panel
        returns (StreamPanelEntry): the entry created
        """
        stream = instrmodel.BrightfieldStream("Bright-field",
                  self._microscope.ccd, self._microscope.ccd.data,
                  self._microscope.light)
        self._microscope.streams.add(stream)
        stream.updated.value = True
        
        entry = comp.stream.FixedStreamPanelEntry(self._spanel, stream)
        self._spanel.add_stream(entry)
        return entry
    
    def addSEMSED(self):
        """
        Creates a new SED stream and entry into the stream panel
        returns (StreamPanelEntry): the entry created
        """
        stream = instrmodel.SEMStream("Secondary electrons",
                  self._microscope.sed, self._microscope.sed.data,
                  self._microscope.ebeam)
        self._microscope.streams.add(stream)
        stream.updated.value = True
        
        entry = comp.stream.FixedStreamPanelEntry(self._spanel, stream)
        self._spanel.add_stream(entry)
        return entry

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
                if self._microscope.sed:
                    self.addSEMSED()
                # TODO need to hide if the view is not the right one
        
            self._startStreams(OPTICAL_STREAMS)

        
    def _pauseStreams(self, classes):
        """
        Pause (deactivate and stop updating) all the streams of the given class
        """
        for s in self._microscope.streams:
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
        for s in self._microscope.streams:
            if (s in self._streams_to_restart and isinstance(s, classes)):
                self._streams_to_restart.remove(s)
                s.updated.value = True

        # TODO how to activate the stream? Is it done automatically by the
        # (so far, magical) stream scheduler?

    
    def removeStream(self, stream):
        """
        Removes a stream. 
        stream (Stream): the stream to remove
        Note: the stream entry is to be destroyed separately via the spanel
        It's ok to call if the stream has already been removed 
        """
        self._streams_to_restart.discard(stream)
        self._microscope.streams.discard(stream)
        stream.active.value = False
        stream.updated.value = False
        
