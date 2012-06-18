# -*- coding: utf-8 -*-
'''
Created on 2 Apr 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Open Delmic Microscope Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''
from Pyro4.core import oneway
from _core import WeakMethod, WeakRefLostError
import Pyro4
import inspect
import logging
import numpy
import threading
import zmq

"""
Provides data-flow: an object that can contain a large array of data regularly
updated. Typically it is used to transmit video (sequence of images). It does it
losslessly and with metadata attached.
"""

# This list of constants are used as key for the metadata
MD_EXP_TIME = "Exposure time" # s
MD_ACQ_DATE = "Acquisition date" # s since epoch
# distance between two points on the sample that are seen at the centre of two
# adjacent pixels considering that these two points are in focus 
MD_PIXEL_SIZE = "Pixel size" # (m, m)  
MD_BINNING = "Binning" # px
MD_HW_VERSION = "Hardware version" # str
MD_SW_VERSION = "Software version" # str
MD_HW_NAME = "Hardware name" # str, product name of the hardware component (and s/n)
MD_GAIN = "Gain" # no unit (ratio)
MD_BPP = "Bits per pixel" # bit
MD_READOUT_TIME = "Pixel readout time" # s, time to read one pixel
MD_SENSOR_PIXEL_SIZE = "Sensor pixel size" # (m, m), distance between the centre of 2 pixels on the detector sensor
MD_SENSOR_SIZE = "Sensor size" # px, px
MD_SENSOR_TEMP = "Sensor temperature" # C
MD_POS = "Centre position" # (m, m), location of the picture centre relative to top-left of the sample)
MD_IN_WL = "Input wavelength range" # (m, m), lower and upper range of the wavelenth input
MD_OUT_WL = "Output wavelength range"  # (m, m), lower and upper range of the filtered wavelenth before the camera
MD_LIGHT_POWER = "Light power" # W, power of the emitting light

class DataArray(numpy.ndarray):
    """
    Array of data (a numpy nd.array) + metadata.
    It is the main object returned by a dataflow.
    It can be created either explicitly:
     DataArray([2,3,1,0], metadata={"key": 2})
    or via a view:
     x = numpy.array([2,3,1,0])
     x.view(DataArray)
    """
    
    # see http://docs.scipy.org/doc/numpy/user/basics.subclassing.html
    def __new__(cls, input_array, metadata={}):
        """
        input_array: array from which to initialise the data
        metadata (dict str-> value): a dict of (standard) names to their values
        """
        obj = numpy.asarray(input_array).view(cls)
        obj.metadata = metadata
        return obj

    def __array_finalize__(self, obj):
        if obj is None:
            return
        self.metadata = getattr(obj, 'metadata', {})

class DataFlow(object):
    """
    This is an abstract class that must be extended by each detector which
    wants to provide a dataflow.
    extend: subscribe() and unsubcribe() to start stop generating data. 
            Each time a new data is available it should call notify(dataarray)
    extend: get() to synchronously return the next dataarray available
    """
    def __init__(self):
        self._listeners = set()
        
        # to be overridden
        self.parent = None
    
    # to be overridden
    # not defined at all so that the proxy version automatically does a remote call 
#    def get(self):
#        # TODO timeout argument?
#        pass
    
    
    def subscribe(self, listener):
        """
        Register a callback function to be called when the ActiveValue is 
        listener (function): callback function which takes as arguments 
           dataflow (this object) and data (the new data array)
        """
        # TODO update rate argument to indicate how often we need an update?
        assert callable(listener)
        
        count_before = len(self._listeners)
        self._listeners.add(WeakMethod(listener))
        if count_before == 0:
            self.start_generate()
        
    def unsubscribe(self, listener):
        self._listeners.discard(WeakMethod(listener))
        
        count_after = len(self._listeners)
        if count_after == 0:
            self.stop_generate()
        
    # The following methods are only to be used by the object which own
    # the dataflow, they are not part of the external API
    # to be overridden
    def start_generate(self):
        """
        called whenever there is a need to start generating the data. IOW, when
        the number of listeners goes from 0 to 1.
        """
        pass
    
    # to be overridden
    def stop_generate(self):
        """
        called whenever there is no need to generate the data anymore. IOW, when
        the number of listeners goes from 1 to 0.
        """
        pass

    def notify(self, data):
        """
        Call this method to share the data with all the listeners
        data (DataArray): the data to be sent to listeners
        """
        assert(isinstance(data, numpy.ndarray))
        
        for l in self._listeners.copy(): # to allow modify the set while calling
            try:
                l(self, data)
            except WeakRefLostError:
                self.unsubscribe(l)


# DataFlowObject to create on the server (in an Odemic component)
class DataFlowRemotable(DataFlow):
    # TODO max_discard, to be passed to the proxy as well.
    def __init__(self, parent=None, daemon=None):
        DataFlow.__init__(self, parent)
        # add it to the parent
        if parent:
            parent._odemicDataFlows.add(self)
        else:
            print "Warning, no parent for dataflow", self
        
        self._global_name = None # to be filled when registered
        
        # different from ._listeners for notify() to do different things
        self._remote_listeners = set() # any unique string works
        
        # create a zmq pipe to publish the data
        # Warning: notify() will most likely run in a separate thread, which is
        # not recommanded by 0MQ. At least, we should never access it from this
        # thread anymore. To be safe, it might need a pub-sub forwarder proxy inproc
        self.ctx = zmq.Context(1)
        self.pipe = self.ctx.socket(zmq.PUB)
        self.pipe.linger = 1 # don't keep messages more than 1s after close
        # discard messages quickly (0 to have a loss-less transmission)   
        self.pipe.hwm = 1
        if daemon:
            self._register(daemon)
    
    def _register(self, daemon):
        daemon.register(self)
        self._global_name = self.parent.name + "." + self._pyroId
        print "server is registered to send to " + "ipc://" + self._global_name + ".ipc"
        self.pipe.bind("ipc://" + self._global_name + ".ipc")
    
    def _count_listeners(self):
        return len(self._listeners) + len(self._remote_listeners)
    
#    # To be overridden
#    def get(self):
#        # TODO timeout argument?
#        pass
    
    @oneway
    def subscribe(self, listener):
        count_before = len(self._listeners)
        
        # add string to listeners if listener is string
        if isinstance(listener, basestring):
            self._remote_listeners.add(listener)
        else:
            assert callable(listener)
            self._listeners.add(WeakMethod(listener))

        if count_before == 0:
            self.start_generate()
            
    @oneway
    def unsubscribe(self, listener):
        if isinstance(listener, basestring):
            # remove string from listeners  
            self._remote_listeners.discard(listener)
        else:
            assert callable(listener)
            self._listeners.discard(WeakMethod(listener))

        count_after = self._count_listeners()
        if count_after == 0:
            self.stop_generate()
        
    def notify(self, data):
        # publish the data remotely
        if len(self._remote_listeners) > 0:
            md = {"dtype": str(data.dtype), "shape": data.shape}
            self.pipe.send_pyobj(md, zmq.SNDMORE)
            self.pipe.send(numpy.getbuffer(data), copy=False)
        
        # publish locally
        DataFlow.notify(self, data)
    
    def __del__(self):
        self.pipe.close()
        self.ctx.term()
    

# DataFlow object automatically created on the client (in an Odemic component)
class ProxyDataFlow(DataFlow, Pyro4.Proxy):
    
    # init is as light as possible to reduce creation overhead in case the
    # object is actually never used
    def __init__(self, uri, oneways, asyncs):
        Pyro4.Proxy.__init__(self, uri, oneways, asyncs)
        self._parent = None
        self._global_name = None #unknown until parent is defined
        DataFlow.__init__(self, parent=None)
        
        # It should be no problem to have one context per dataflow,
        # maybe more efficient to have one global for the whole process? see .instance()
        self.ctx = zmq.Context(1)
        self._thread = None
        self._must_unsubscribe = False
        
        # amount of messages that can be discarded if a new one is alredy available
        self.max_discard = 100
    
    # allow late parent update
    @property
    def parent(self):
        return self._parent
    
    @parent.setter
    def parent(self, value):
        # Should be done only once with a real parent!
        if self._parent == value:
            return
        elif self._parent is not None:
            logging.error("Changing parent of dataflow is not allowed (from %s to %s)",
                          self._parent, value)
        self._parent = value
        
        # update global name
        if self._parent is None:
            self._global_name = None
        else:
            self._global_name = self._parent.name + "." + self._pyroUri.object

    # .get() is a direct remote call
    
    # next two methods are directly from DataFlow
    #.subscribe()
    #.unsubscribe()
    # directly handled by DataFlow
#    def notify(self, data):
#        # publish the data locally (only)
#        DataFlow.notify(self, data)
    
    def _create_thread(self):
        self.commands = self.ctx.socket(zmq.PAIR)
        self.commands.bind("inproc://" + self._global_name)
        self._thread = threading.Thread(name="zmq" + self._global_name, 
                              target=self._listenForData, args=(self.ctx,))
        self._thread.deamon = True
        self._thread.start()
        
    def start_generate(self):
        # start the remote subscription
        if not self._thread:
            self._create_thread()
        self.commands.send("SUB")
        self.commands.recv() # synchronise
    
        # send subscription to the actual dataflow
        # a bit tricky because the underlying method gets created on the fly
        Pyro4.Proxy.__getattr__(self, "subscribe")(self._global_name)

    def stop_generate(self):
        # stop the remote subscription
        Pyro4.Proxy.__getattr__(self, "unsubscribe")(self._global_name)
        
        # Kludge to avoid dead-lock when called from inside the callback
        if threading.current_thread() == self._thread:
            self._must_unsubscribe = True
        else:
            self.commands.send("UNSUB")
#            self.commands.recv() # synchronise

    # to be executed in a separate thread
    def _listenForData(self, ctx):
        """
        ctx (zmq context): global zmq context
        """
        assert self._global_name is not None
        
        # create a zmq synchronised channel to receive commands
        commands = ctx.socket(zmq.PAIR)
        commands.connect("inproc://" + self._global_name)
        
        # create a zmq subscription to receive the data
        data = ctx.socket(zmq.SUB)
        data.connect("ipc://" + self._global_name + ".ipc")
        data.hwm = 1 # probably does nothing
        
        # Process messages for commands and data
        poller = zmq.Poller()
        poller.register(commands, zmq.POLLIN)
        poller.register(data, zmq.POLLIN)
        discarded = 0
        while True:
            socks = dict(poller.poll())

            # process commands
            if socks.get(commands) == zmq.POLLIN:
                message = commands.recv()
                if message == "SUB":
                    data.setsockopt(zmq.SUBSCRIBE, '')
                    commands.send("SUBD")
                elif message == "UNSUB":
                    data.setsockopt(zmq.UNSUBSCRIBE, '')
                    print "unsubscribe"
                    # no confirmation (async)
#                    commands.send("UNSUBD")
                elif message == "STOP":
                    commands.close()
                    data.close()
                    commands.send("STOPPED")
                    return
            
            # receive data
            if socks.get(data) == zmq.POLLIN:
                md = data.recv_pyobj()
#                md = {"dtype": "uint16", "shape": (2048,2048)}
                array_buf = data.recv(copy=False)
                # more fresh data already?
                if (data.getsockopt(zmq.EVENTS) & zmq.POLLIN and
                    discarded < self.max_discard):
#                    print "skipping one data array"
                    discarded += 1
                    continue
                if discarded:
                    print "had discarded %d arrays" % discarded
                discarded = 0
                # TODO: any need to use zmq.utils.rebuffer.array_from_buffer()?
                array = numpy.frombuffer(array_buf, dtype=md["dtype"])
                array.shape = md["shape"]
#                data.setsockopt(zmq.UNSUBSCRIBE, '') # XXX
#                data.getsockopt(zmq.EVENTS)
                self.notify(array)
                # Handle subscription closure from the callbacks
                if self._must_unsubscribe:
                    data.setsockopt(zmq.UNSUBSCRIBE, '')
                    print "unsubscribe"
                    self._must_unsubscribe = False
#                else:
#                    data.setsockopt(zmq.SUBSCRIBE, '') #XXX

    def __del__(self):
        # end the thread
        if self._thread:
            self.commands.send("STOP")
            self.commands.recv()
        self.commands.close()

def dump_dataflows(self):
    """
    return the names and value of all the DataFlows added to an object (component)
    self: the object (instance of a class)
    return (dict string -> value)
    """
    dataflows = dict()
    for name, value in inspect.getmembers(self, lambda x: isinstance(x, DataFlowRemotable)):
        dataflows[name] = value
    return dataflows

def load_dataflows(self, dataflows):
    """
    duplicate the given dataflows into the instance.
    useful only for a proxy class
    """
    for name, df in dataflows.items():
        self.__dict__[name] = df
        df.parent = self

def odemicDataFlowSerializer(self):
    """reduce function that automatically replaces Pyro objects by a Proxy"""
    daemon=getattr(self,"_pyroDaemon",None)
    if daemon: # TODO might not be even necessary: They should be registering themselves in the init
        self._odemicShared = True
        # only return a proxy if the object is a registered pyro object
        return ProxyDataFlow, (daemon.uriFor(self),
                                Pyro4.core.get_oneways(self),
                                Pyro4.core.get_asyncs(self),
                                # self.parent # not possible due to recursion
                                )
    else:
        return self.__reduce__()
    
Pyro4.Daemon.serializers[DataFlowRemotable] = odemicDataFlowSerializer

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: