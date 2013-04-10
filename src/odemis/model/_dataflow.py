# -*- coding: utf-8 -*-
'''
Created on 2 Apr 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from . import _core
from ._core import WeakMethod, WeakRefLostError
from Pyro4.core import oneway
import Pyro4
import inspect
import logging
import numpy
import threading
import time
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
MD_BINNING = "Binning" # (px, px), number of pixels acquired as one big pixel, in each dimension
MD_SAMPLES_PER_PIXEL = "Samples per pixel" # samples (number of samples acquired for each pixel) default: 1
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
# Note that the following two might be a set of ranges
MD_ROTATION = "Rotation" # degree (0<=float<360) rotation applied to the image (from its center) counter-clockwise 
MD_IN_WL = "Input wavelength range" # (m, m), lower and upper range of the wavelength input
MD_OUT_WL = "Output wavelength range"  # (m, m), lower and upper range of the filtered wavelength before the camera
MD_LIGHT_POWER = "Light power" # W, power of the emitting light
MD_LENS_NAME = "Lens name" # str, product name of the lens
MD_LENS_MAG = "Lens magnification" # float (ratio), magnification factor
MD_FILTER_NAME = "Filter name" # str, product name of the light filter
MD_DWELL_TIME = "Pixel dwell time" # s (float), time the electron beam spends per pixel
MD_EBEAM_ENERGY = "Electron beam energy" # eV (float), energy of the electron beam TODO: in SI, ie, Joules? 
MD_EBEAM_SPOT_DIAM = "Electron beam spot diameter" # m (float), approximate diameter of the electron beam spot (typically function of the current)  
MD_WL_POLYNOMIAL = "Wavelength polynomial" # m, m/px, m/px²... (list of float), polynomial to convert from a pixel number of a spectrum to the wavelength

# The following tags are not to be filled at acquisition, but by the user interface
MD_DESCRIPTION = "Description" # (string) User-friendly name that describes what this acquisition is
MD_USER_NOTE = "User note" # (string) Whatever comment the user has added to the image  

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
    def __new__(cls, input_array, metadata=None):
        """
        input_array: array from which to initialise the data
        metadata (dict str-> value): a dict of (standard) names to their values
        """
        obj = numpy.asarray(input_array).view(cls)
        if metadata is None:
            metadata = {}
        obj.metadata = metadata
        return obj

    def __array_finalize__(self, obj):
        if obj is None:
            return
        self.metadata = getattr(obj, 'metadata', {})
    
    # Used to send the DataArray over Pyro (over ZMQ, we use an optimised way)
    def __reduce__(self):
        # take the normal output (need to convert to list to modify it)
        ret = list(numpy.ndarray.__reduce__(self))
        # add to the state our additional state
        ret[2] = (ret[2], self.metadata)
        return tuple(ret)
    
    def __setstate__(self,state):
        nd_state, md = state
        numpy.ndarray.__setstate__(self, nd_state)
        self.metadata = md
        
class DataFlowBase(object):
    """
    This is an abstract class that must be extended by each detector which
    wants to provide a dataflow.
    extend: subscribe() and unsubcribe() to start stop generating data. 
            Each time a new data is available it should call notify(DataArray)
    extend: get() to synchronously return the next DataArray available
    """
    def __init__(self):
        self._listeners = set()
        self._lock = threading.Lock() # need to be acquired to modify the set
    
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
        
        with self._lock:
            count_before = len(self._listeners)
            self._listeners.add(WeakMethod(listener))
            logging.debug("Listener %r subscribed, now %d subscribers", listener, count_before + 1)
            if count_before == 0:
                self.start_generate()
        
    def unsubscribe(self, listener):
        with self._lock:
            count_before = len(self._listeners)
            self._listeners.discard(WeakMethod(listener))
            count_after = len(self._listeners)
            logging.debug("Listener %r unsubscribed, now %d subscribers", listener, count_after)
            if count_before > 0 and count_after == 0:
                self.stop_generate()
    
#    # to be overridden
#    def synchronizedOn(self, event):
#        raise NotImplementedError("This DataFlow doesn't support Event synchronization")
    
    # TODO should default to open a thread that continuously call get() ?
    # For now we default to have get() as a continuous acquisition which gets
    # unsubscribed after one data received.
        
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
        
        # Never take the lock here, to avoid the case where stop_generate() waits
        # for one last notify
        
        # to allow modify the set while calling
        snapshot_listeners = frozenset(self._listeners)
        for l in snapshot_listeners: 
            try:
                l(self, data)
            except WeakRefLostError:
                self.unsubscribe(l)
            except:
                # we cannot abort just because one listener failed
                logging.exception("Exception when notifying a data_flow")


# DataFlow object to create on the server (in a component)
class DataFlow(DataFlowBase):
    def __init__(self, max_discard=100):
        """
        max_discard (int): mount of messages that can be discarded in a row if
                            a new one is already available. 0 to keep (notify) 
                            all the messages (dangerous if callback is slower
                            than the generator).
        """
        DataFlowBase.__init__(self)       
        # different from ._listeners for notify() to do different things
        self._remote_listeners = set() # any unique string works
        
        self._global_name = None # to be filled when registered
        self._ctx = None
        self.pipe = None
        self._max_discard = max_discard
        self._update_pipe_hwm()
        
    def _getproxystate(self):
        """
        Equivalent to __getstate__() of the proxy version
        """
        proxy_state = Pyro4.core.pyroObjectSerializer(self)[2]
        return (proxy_state, _core.dump_roattributes(self), self.max_discard)
    
    @property
    def max_discard(self):
        return self._max_discard
    
    @max_discard.setter
    def max_discard(self, value):
        self._max_discard = value
        self._update_pipe_hwm()
    
    def _update_pipe_hwm(self):
        """
        updates the high water mark option of OMQ pipe according to max_discard
        """
        if self.pipe is None:
            return
        if self._max_discard == 0:
            # High-water mark   
            self.pipe.hwm = 0
        else:
            self.pipe.hwm = 1
            
    def _register(self, daemon):
        """
        Get the dataflow ready to be shared. It gets registered to the Pyro 
        daemon and over 0MQ. It should be called only once. Note that you have
        to call this method to register a dataflow, a simple daemon.register(df)
        is not enough.
        daemon (Pyro4.Daemon): daemon used to share this object
        """
        daemon.register(self)
        
        # create a zmq pipe to publish the data
        # Warning: notify() will most likely run in a separate thread, which is
        # not recommended by 0MQ. At least, we should never access it from this
        # thread anymore. To be safe, it might need a pub-sub forwarder proxy inproc
        self._ctx = zmq.Context(1)
        self.pipe = self._ctx.socket(zmq.PUB)
        self.pipe.linger = 1 # don't keep messages more than 1s after close
        self._update_pipe_hwm()
        
        uri = daemon.uriFor(self)
        # uri.sockname is the file name of the pyro daemon (with full path)
        self._global_name = uri.sockname + "@" + uri.object
        logging.debug("server is registered to send to " + "ipc://" + self._global_name)
        self.pipe.bind("ipc://" + self._global_name)
        
    def _unregister(self):
        """
        unregister the dataflow from the daemon and clean up the 0MQ bindings
        """
        daemon = getattr(self, "_pyroDaemon", None)
        if daemon:
            daemon.unregister(self)
        if self._ctx:
            self.pipe.close()
            self.pipe = None
            self._ctx.term()
            self._ctx = None
    
    def _count_listeners(self):
        return len(self._listeners) + len(self._remote_listeners)
    
    def get(self):
        """
        Acquires one image and return it
        return (DataArray)
        Default implementation: it subscribes and, after receiving the first
         image, unsubscribes. It's inefficient but simple and works in every case.
        """
        is_received = threading.Event()
        data_shared = [None] # in python2 we need to create a new container object
        
        def receive_one_image(df, data):
            df.unsubscribe(receive_one_image)
            data_shared[0] = data
            is_received.set()
        
        self.subscribe(receive_one_image)
        is_received.wait()
        return data_shared[0]
     
    @oneway
    def subscribe(self, listener):
        with self._lock:
            count_before = self._count_listeners()
            
            # add string to listeners if listener is string
            if isinstance(listener, basestring):
                self._remote_listeners.add(listener)
            else:
                assert callable(listener)
                self._listeners.add(WeakMethod(listener))

            logging.debug("Listener %r subscribed, now %d subscribers", listener, count_before + 1)
            if count_before == 0:
                self.start_generate()
            
    @oneway
    def unsubscribe(self, listener):
        with self._lock:
            count_before = self._count_listeners()
            if isinstance(listener, basestring):
                # remove string from listeners  
                self._remote_listeners.discard(listener)
            else:
                assert callable(listener)
                self._listeners.discard(WeakMethod(listener))
    
            count_after = self._count_listeners()
            logging.debug("Listener %r unsubscribed, now %d subscribers", listener, count_after)
            if count_before > 0 and count_after == 0:
                self.stop_generate()
        
    def notify(self, data):
        # publish the data remotely
        if self.pipe and len(self._remote_listeners) > 0:
            # TODO thread-safe for self.pipe ? 
            dformat = {"dtype": str(data.dtype), "shape": data.shape}
            self.pipe.send_pyobj(dformat, zmq.SNDMORE)
            self.pipe.send_pyobj(data.metadata, zmq.SNDMORE)
            try:
                self.pipe.send(numpy.getbuffer(data), copy=False)
            except TypeError:
                # not all buffers can be sent zero-copy (e.g., has strides)
                # try harder by copying (which removes the strides)
                logging.debug("Failed to send data with zero-copy")
                self.pipe.send(numpy.getbuffer(data.copy()), copy=False)
        
        # publish locally
        DataFlowBase.notify(self, data)
    
    def __del__(self):
        if self._count_listeners() > 0:
            self.stop_generate()
        self._unregister()

# DataFlowBase object automatically created on the client (in an Odemic component)
class DataFlowProxy(DataFlowBase, Pyro4.Proxy):
    # init is as light as possible to reduce creation overhead in case the
    # object is actually never used
    def __init__(self, uri, max_discard=100):
        """
        uri : see Proxy
        max_discard (int): amount of messages that can be discarded in a row if
                            a new one is already available. 0 to keep (notify) 
                            all the messages (dangerous if callback is slower
                            than the generator).
        Note: there is no reason to create a proxy explicitly!
        """ 
        Pyro4.Proxy.__init__(self, uri)
        self._global_name = uri.sockname + "@" + uri.object
        DataFlowBase.__init__(self)
        self.max_discard = max_discard
        
        self._ctx = None
        self._commands = None
        self._thread = None
        
    def __getstate__(self):
        # must permit to recreate a proxy to a data-flow in a different container
        proxy_state = Pyro4.Proxy.__getstate__(self)
        return (proxy_state, _core.dump_roattributes(self), self.max_discard)
        
    def __setstate__(self, state):
        proxy_state, roattributes, self.max_discard = state
        Pyro4.Proxy.__setstate__(self, proxy_state)
        _core.load_roattributes(self, roattributes)
        
        self._global_name = self._pyroUri.sockname + "@" + self._pyroUri.object
        DataFlowBase.__init__(self)
        
        self._ctx = None
        self._commands = None
        self._thread = None
    
    # .get() is a direct remote call
    
    # next three methods are directly from DataFlowBase
    #.subscribe()
    #.unsubscribe()
    #.notify()
    
    def _create_thread(self):
        self._ctx = zmq.Context(1) # apparently 0MQ reuse contexts
        self._commands = self._ctx.socket(zmq.PAIR)
        self._commands.bind("inproc://" + self._global_name)
        self._thread = SubscribeProxyThread(self.notify, self._global_name, self.max_discard, self._ctx)
        self._thread.start()
        
    def start_generate(self):
        # start the remote subscription
        if not self._thread:
            self._create_thread()
        self._commands.send("SUB")
        self._commands.recv() # synchronise
    
        # send subscription to the actual dataflow
        # a bit tricky because the underlying method gets created on the fly
#        Pyro4.Proxy.subscribe(self, self._global_name)
        Pyro4.Proxy.__getattr__(self, "subscribe")(self._global_name)

    def stop_generate(self):
        # stop the remote subscription
        Pyro4.Proxy.__getattr__(self, "unsubscribe")(self._global_name)
        self._commands.send("UNSUB") # asynchronous (necessary to not deadlock)

    def __del__(self):
        # end the thread (but it will stop as soon as it notices we are gone anyway)
        if self._thread:
            if self._thread.is_alive():
                if len(self._listeners):
                    logging.warning("Stopping subscription while there are still subscribers because dataflow '%s' is going out of context", self._global_name)
                self._commands.send("STOP")
                self._thread.join()
            self._commands.close()
            self._ctx.term()


class SubscribeProxyThread(threading.Thread):
    def __init__(self, notifier, uri, max_discard, zmq_ctx):
        """
        notifier (callable): method to call when a new array arrives
        uri (string): unique string to identify the connection
        max_discard (int)
        zmq_ctx (0MQ context): available 0MQ context to use
        """
        threading.Thread.__init__(self, name="zmq for dataflow " + uri)
        self.daemon = True
        self.uri = uri
        self.max_discard = max_discard
        self._ctx = zmq_ctx
        # don't keep strong reference to notifier so that it can be garbage 
        # collected normally and it will let us know then that we can stop
        self.w_notifier = WeakMethod(notifier)
        
        # create a zmq synchronised channel to receive _commands
        self._commands = zmq_ctx.socket(zmq.PAIR)
        self._commands.connect("inproc://" + uri)
        
        # create a zmq subscription to receive the data
        self.data = zmq_ctx.socket(zmq.SUB)
        self.data.connect("ipc://" + uri)
        # TODO find out if it does something and if it does, depend on max_discard
        self.data.hwm = 1 # probably does nothing 
        
    def run(self):
        # Process messages for commands and data
        poller = zmq.Poller()
        poller.register(self._commands, zmq.POLLIN)
        poller.register(self.data, zmq.POLLIN)
        discarded = 0
        while True:
            socks = dict(poller.poll())

            # process commands
            if socks.get(self._commands) == zmq.POLLIN:
                message = self._commands.recv()
                if message == "SUB":
                    self.data.setsockopt(zmq.SUBSCRIBE, '')
                    logging.debug("Subscribed to remote dataflow %s", self.uri)
                    self._commands.send("SUBD")
                elif message == "UNSUB":
                    self.data.setsockopt(zmq.UNSUBSCRIBE, '')
                    logging.debug("Unsubscribed to remote dataflow %s", self.uri)
                    # no confirmation (async)
                elif message == "STOP":
                    self._commands.close()
                    self.data.close()
                    return
            
            # receive data
            if socks.get(self.data) == zmq.POLLIN:
                array_format = self.data.recv_pyobj()
                array_md = self.data.recv_pyobj()
                array_buf = self.data.recv(copy=False)
                # more fresh data already?
                if (self.data.getsockopt(zmq.EVENTS) & zmq.POLLIN and
                    discarded < self.max_discard):
                    discarded += 1
                    continue
                if discarded:
                    logging.debug("had discarded %d arrays", discarded)
                discarded = 0
                # TODO: any need to use zmq.utils.rebuffer.array_from_buffer()?
                array = numpy.frombuffer(array_buf, dtype=array_format["dtype"])
                array.shape = array_format["shape"]
                darray = DataArray(array, metadata=array_md)
                
                try:
                    self.w_notifier(darray)
                except WeakRefLostError:
                    self._commands.close()
                    self.data.close()
                    return
        
def unregister_dataflows(self):
    for name, value in inspect.getmembers(self, lambda x: isinstance(x, DataFlowBase)):
        value._unregister()

def dump_dataflows(self):
    """
    return the names and value of all the DataFlows added to an object 
    (component). If a dataflow is not registered yet, it is registered.
    self (Component): the object (instance of a class). It must already be
                      registered to a Pyro daemon.
    return (dict string -> value): attribute name -> dataflow
    """
    dataflows = dict()
    daemon = self._pyroDaemon
    for name, value in inspect.getmembers(self, lambda x: isinstance(x, DataFlowBase)):
        if not hasattr(value, "_pyroDaemon"):
            value._register(daemon)
        dataflows[name] = value
    return dataflows

def load_dataflows(self, dataflows):
    """
    duplicate the given dataflows into the instance.
    useful only for a proxy class
    """
    for name, df in dataflows.items():
        setattr(self, name, df)

def DataFlowSerializer(self):
    """reduce function that automatically replaces Pyro objects by a Proxy"""
    daemon=getattr(self,"_pyroDaemon",None)
    if daemon: 
        # only return a proxy if the object is a registered pyro object
        return (DataFlowProxy, (daemon.uriFor(self),), self._getproxystate())
    else:
        return self.__reduce__()
    
Pyro4.Daemon.serializers[DataFlow] = DataFlowSerializer


# Be careful: for now, only the Components have their Events and DataFlows copied
# when used remotely. IOW, if a dataflow has an Event as attribute, it will not
# be accessible remotely.
class EventBase(object):
    pass
            

class Event(EventBase):
    """
    An Event is used to transmit information that "something" has happened.
    DataFlow can be synchronized on an Event to ensure that it starts acquisition
    at a specific moment.
    Simple implementation of simplistic event interface. Callback directly each
    subscriber. Low latency, but blocking in each subscriber.
    Pretty similar to a VigilantAttribute, but:
     * doesn't contain value (so no unit, range either)
     * every notify matters, so none should be discarded ever.
    """ 
    def __init__(self):
        self._listeners = set() # object (None -> None)
    
    def _getMostDirectObject(self, obj):
        """
        obj (object): any object
        returns (object): if obj is a pyroProxy of an object handled by the same
          Pyro daemon as this component is handled, returns the actual object, 
          otherwise, returns obj
        """
        if not isinstance(obj, Pyro4.core.Proxy):
            return obj
        daemon = getattr(self, "_pyroDaemon", None)
        if daemon is None:
            return obj
        
        # check if this daemon is exporting an object with the same URI
        uri = obj._pyroUri
        for obj_id, act_obj in daemon.objectsById.items():
            if uri == daemon.uriFor(obj_id):
                return act_obj
        return obj
    
    def hasListeners(self):
        """
        returns (boolean): True if the event currently has some listeners, or
         False otherwise.
        """
        return not not self._listeners # = not empty
    
    def subscribe(self, listener):
        """
        Register a callback function to be called when the Event is changed
        listener (obj with onEvent method): callback function which takes no argument and return nothing
        """
        # if direct (python call): latency ~100us (down to ~20us with RT priority)
        # via Pyro: ~2ms (first one is much bigger)
        # => if object is on the same container as us, use the direct connection
        # if possible to find lower latency communication channel => create a proxy
        # object and use it.
        
        # TODO: listener could be directly a callable, and if it is a bound method,
        # get the object and the method name, and reconstruct it with the direct
        # object 
        callback = self._getMostDirectObject(listener).onEvent
        assert callable(callback)
        # not using WeakMethod, because callback would immediately be unreferenced
        # and disappear anyway.
        self._listeners.add(listener)
        
    def unsubscribe(self, listener):
        self._listeners.discard(listener)

    def notify(self):
        for l in frozenset(self._listeners):
            l.onEvent() # for debugging: pass time.time()

# All the classes and functions bellow is to make the remote objects look like 
# Events. 
    def _getproxystate(self):
        """
        Equivalent to __getstate__() of the proxy version
        """
        return Pyro4.core.pyroObjectSerializer(self)[2]

class EventProxy(EventBase, Pyro4.Proxy):
    def __init__(self, uri):
        Pyro4.Proxy.__init__(self, uri)
        
    def __getstate__(self):
        # must permit to recreate a proxy to a data-flow in a different container
        return Pyro4.Proxy.__getstate__(self)
        
    def __setstate__(self, state):
        Pyro4.Proxy.__setstate__(self, state)


def unregister_events(self):
    for name, value in inspect.getmembers(self, lambda x: isinstance(x, EventBase)):
        daemon = getattr(value, "_pyroDaemon", None)
        if daemon:
            daemon.unregister(value)

def dump_events(self):
    """
    return the names and value of all the Events added to an object 
    (component). If an Event is not registered yet, it is registered.
    self (Component): the object (instance of a class). It must already be
                      registered to a Pyro daemon.
    return (dict string -> value): attribute name -> Event
    """
    events = dict()
    daemon = self._pyroDaemon
    for name, value in inspect.getmembers(self, lambda x: isinstance(x, EventBase)):
        if not hasattr(value, "_pyroDaemon"):
            daemon.register(value)
        events[name] = value
    return events

def load_events(self, events):
    """
    duplicate the given events into the instance.
    useful only for a proxy class
    """
    for name, evt in events.items():
        setattr(self, name, evt)

# Without this one, it would share events, but they would look like a basic Proxy,
# so there would be no way to know 
def EventSerializer(self):
    """reduce function that automatically replaces Pyro objects by a Proxy"""
    daemon=getattr(self,"_pyroDaemon",None)
    if daemon: 
        # only return a proxy if the object is a registered pyro object
        return (EventProxy, (daemon.uriFor(self),), self._getproxystate())
    else:
        return self.__reduce__()
    
Pyro4.Daemon.serializers[Event] = EventSerializer

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: