# -*- coding: utf-8 -*-
'''
Created on 26 Mar 2012

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
import _core
import inspect
import logging
import threading
import zmq

class InvalidTypeError(Exception):
    pass

class OutOfBoundError(Exception):
    pass

class NotSettableError(Exception):
    pass


class VigilantAttributeBase(object):
    '''
    A VigilantAttributeBase represents a value (an object) with:
     * meta-information (min, max, unit, read-only...)
     * observable behaviour (any one can ask to be notified when the value changes) 
    '''

    def __init__(self, initval=None, unit="", readonly=False):
        """
        Creates a VigilantAttributeBase with a given initial value
        initval : any type
        unit (str): a SI unit in which the VA is expressed
        readonly (bool): if True, value setter will raise an exception. It's still
            possible to change the value by calling _set() and then notify()
        """
        # TODO shall we have a assigner callback which is called to set the value and returns the actual value (for hardware)?
        self._listeners = set()
        self._value = initval
        self.unit = unit
        self.readonly = readonly

    def _get_value(self):
        """The value of this VA"""
        return self._value
    
    def _set(self, value):
        """
        Override to do checking on the value.
        """
        self._value = value

    # cannot be oneway because we need the exception in case of error
    def _set_value(self, value):
        if self.readonly:
            raise NotSettableError("Value is readonly")
        prev_value = self._value
        self._set(value)
        if prev_value != self._value:
            self.notify(self._value)
    
    def _del_value(self):
        del self._value
    
    value = property(_get_value, _set_value, _del_value, "The actual value")
        
    def subscribe(self, listener, init=False):
        """
        Register a callback function to be called when the VigilantAttributeBase is changed
        listener (function): callback function which takes as argument val the new value
        init (boolean): if True calls the listener directly, to initialise it
        """
        assert callable(listener)
        self._listeners.add(WeakMethod(listener))
        
        if init:
            listener(self.value)
            
        # TODO allow to pass custom additional parameters to the callback 

    def unsubscribe(self, listener):
        self._listeners.discard(WeakMethod(listener))

    def notify(self, v):
        for l in self._listeners.copy():
            try:
                l(v)
            except WeakRefLostError:
                self.unsubscribe(l)


class VigilantAttribute(VigilantAttributeBase):

    def __init__(self, initval, max_discard=100, *args, **kwargs):
        """
        max_discard (int): mount of updates that can be discarded in a row if
                            a new one is already available. 0 to keep (notify) 
                            all the messages (dangerous if callback is slower
                            than the generator).
        """
        VigilantAttributeBase.__init__(self, initval, *args, **kwargs)
        # different from ._listeners for notify() to do different things
        self._remote_listeners = set() # any unique string works
        
        self._global_name = None # to be filled when registered
        self.ctx = None
        self.pipe = None
        self.max_discard = max_discard
        
    def _register(self, daemon):
        """
        Get the VigilantAttributeBase ready to be shared. It gets registered to the Pyro 
        daemon and over 0MQ. It should be called only once. Note that you have
        to call this method to register a VA, a simple daemon.register(p)
        is not enough.
        daemon (Pyro4.Daemon): daemon used to share this object
        """
        daemon.register(self)
        
        # create a zmq pipe to publish the data
        # Warning: notify() will most likely run in a separate thread, which is
        # not recommended by 0MQ. At least, we should never access it from this
        # thread anymore. To be safe, it might need a pub-sub forwarder proxy inproc
        self.ctx = zmq.Context(1)
        self.pipe = self.ctx.socket(zmq.PUB)
        self.pipe.linger = 1 # don't keep messages more than 1s after close
        # self.pipe.hwm has to be 0 (default), otherwise it drops _new_ values  
        
        uri = daemon.uriFor(self)
        # uri.sockname is the file name of the pyro daemon (with full path)
        self._global_name = uri.sockname + "@" + uri.object
        logging.debug("VA server is registered to send to " + "ipc://" + self._global_name)
        self.pipe.bind("ipc://" + self._global_name)
    
    def _unregister(self):
        """
        unregister the VA from the daemon and clean up the 0MQ bindings
        """
        daemon = getattr(self, "_pyroDaemon", None)
        if daemon:
            daemon.unregister(self)
        if self.ctx:
            self.pipe.close()
            self.ctx.term()
            self.ctx = None
            
    def _count_listeners(self):
        return len(self._listeners) + len(self._remote_listeners)

    @oneway
    def subscribe(self, listener, init=False):
        """
        listener (string) => uri of listener of zmq
        listener (callable) => method to call (locally)
        """
        # add string to listeners if listener is string
        if isinstance(listener, basestring):
            self._remote_listeners.add(listener)
            if init:
                self.pipe.send_pyobj(self.value)
        else:
            VigilantAttributeBase.subscribe(self, listener, init)

    @oneway
    def unsubscribe(self, listener):
        """
        listener (string) => uri of listener of zmq
        listener (callable) => method to call (locally)
        """
        if isinstance(listener, basestring):
            # remove string from listeners  
            self._remote_listeners.discard(listener)
        else:
            VigilantAttributeBase.unsubscribe(self, listener)
        
    def notify(self, v):
        # publish the data remotely
        if len(self._remote_listeners) > 0:
            self.pipe.send_pyobj(v)
        
        # publish locally
        VigilantAttributeBase.notify(self, v)
    
    def __del__(self):
        self._unregister()


class VigilantAttributeProxy(VigilantAttributeBase, Pyro4.Proxy):
    # init is as light as possible to reduce creation overhead in case the
    # object is actually never used
    def __init__(self, uri, oneways=set(), asyncs=set(), max_discard=100, unit="", readonly=False):
        """
        uri, oneways, asyncs: see Proxy
        max_discard (int): amount of messages that can be discarded in a row if
                            a new one is already available. 0 to keep (notify) 
                            all the messages (dangerous if callback is slower
                            than the generator).
        """ 
        Pyro4.Proxy.__init__(self, uri, oneways, asyncs)
        self._global_name = uri.sockname + "@" + uri.object
        VigilantAttributeBase.__init__(self, None, unit=unit, readonly=readonly) # TODO setting None might not always be valid
        self.max_discard = max_discard
        
        self.ctx = None
        self.commands = None
        self._thread = None
        
    @property
    def value(self):
        return Pyro4.Proxy.__getattr__(self, "_get_value")()
    
    @value.setter
    def value(self, v):
        return Pyro4.Proxy.__getattr__(self, "_set_value")(v)
    # no delete remotely
    
    # for enumerated VA
    @property
    def choices(self):
        return Pyro4.Proxy.__getattr__(self, "_get_choices")()
    
    # for continuous VA
    @property
    def range(self):
        return Pyro4.Proxy.__getattr__(self, "_get_range")()
    
    def __getstate__(self):
        return (_core.dump_roattributes(self), )
        
    def __setstate__(self, state):
        """
        roattributes (dict string -> value)
        """
        roattributes, = state
        _core.load_roattributes(self, roattributes)
        
    def _create_thread(self):
        self.ctx = zmq.Context(1) # apparently 0MQ reuse contexts
        self.commands = self.ctx.socket(zmq.PAIR)
        self.commands.bind("inproc://" + self._global_name)
        self._thread = SubscribeProxyThread(self.notify, self._global_name, self.max_discard, self.ctx)
        self._thread.start()
        
    def subscribe(self, listener, init=False):
        count_before = len(self._listeners)
        
        # TODO when init=True, if already listening, reuse last received value 
        VigilantAttributeBase.subscribe(self, listener, init)
        
        if count_before == 0:
            self._start_listening()
    
    def _start_listening(self):
        """
        start the remote subscription
        """
        if not self._thread:
            self._create_thread()
        self.commands.send("SUB")
        self.commands.recv() # synchronise
    
        # send subscription to the actual VA
        # a bit tricky because the underlying method gets created on the fly
        Pyro4.Proxy.__getattr__(self, "subscribe")(self._global_name)

    def unsubscribe(self, listener):
        VigilantAttributeBase.unsubscribe(self, listener)
        if len(self._listeners) == 0:
            self._stop_listening()
            
    def _stop_listening(self):
        """
        stop the remote subscription
        """
        Pyro4.Proxy.__getattr__(self, "unsubscribe")(self._global_name)
        self.commands.send("UNSUB")
                
    def __del__(self):
        # end the thread (but it will stop as soon as it notices we are gone anyway)
        if self._thread:
            if self._thread.is_alive():
                self.commands.send("STOP")
                self._thread.join()
            self.commands.close()
            self.ctx.term()


class SubscribeProxyThread(threading.Thread):
    def __init__(self, notifier, uri, max_discard, zmq_ctx):
        """
        notifier (callable): method to call when a new value arrives
        uri (string): unique string to identify the connection
        max_discard (int)
        zmq_ctx (0MQ context): available 0MQ context to use
        """
        threading.Thread.__init__(self, name="zmq for VA " + uri)
        self.daemon = True
        self.uri = uri
        self.max_discard = max_discard
        self.ctx = zmq_ctx
        # don't keep strong reference to notifier so that it can be garbage 
        # collected normally and it will let us know then that we can stop
        self.w_notifier = WeakMethod(notifier)
        
        # create a zmq synchronised channel to receive commands
        self.commands = zmq_ctx.socket(zmq.PAIR)
        self.commands.connect("inproc://" + uri)
        
        # create a zmq subscription to receive the data
        self.data = zmq_ctx.socket(zmq.SUB)
        self.data.connect("ipc://" + uri)
        
    def run(self):
        # Process messages for commands and data
        poller = zmq.Poller()
        poller.register(self.commands, zmq.POLLIN)
        poller.register(self.data, zmq.POLLIN)
        discarded = 0
        while True:
            socks = dict(poller.poll())

            # process commands
            if socks.get(self.commands) == zmq.POLLIN:
                message = self.commands.recv()
                if message == "SUB":
                    self.data.setsockopt(zmq.SUBSCRIBE, '')
                    self.commands.send("SUBD")
                elif message == "UNSUB":
                    self.data.setsockopt(zmq.UNSUBSCRIBE, '')
                    # no confirmation (async)
                elif message == "STOP":
                    self.commands.close()
                    self.data.close()
                    return
            
            # receive data
            if socks.get(self.data) == zmq.POLLIN:
                value = self.data.recv_pyobj()
                # more fresh data already?
                if (self.data.getsockopt(zmq.EVENTS) & zmq.POLLIN and
                    discarded < self.max_discard):
                    discarded += 1
                    continue
                if discarded:
                    logging.debug("had discarded %d values", discarded)
                discarded = 0
    
                try:
                    self.w_notifier(value)
                except WeakRefLostError:
                    self.commands.close()
                    self.data.close()
                    return


def unregister_vigilant_attributes(self):
    for name, value in inspect.getmembers(self, lambda x: isinstance(x, VigilantAttribute)):
        value._unregister()
    
def dump_vigilant_attributes(self):
    """
    return the names and value of all the VAs added to an object (component)
    If a VA is not registered yet, it is registered.
    self (Component): the object (instance of a class).  It must already be
                      registered to a Pyro daemon.
    return (dict string -> value): attribute name -> VigilantAttributeBase
    """
    vas = dict()
    daemon = self._pyroDaemon
    for name, value in inspect.getmembers(self, lambda x: isinstance(x, VigilantAttribute)):
        if not hasattr(value, "_pyroDaemon"):
            value._register(daemon)
        vas[name] = value
    return vas

def load_vigilant_attributes(self, vas):
    """
    duplicate the given VAs into the instance.
    useful only for a proxy class
    """
    for name, df in vas.items():
        setattr(self, name, df)

def odemicVASerializer(self):
    """reduce function that automatically replaces Pyro objects by a Proxy"""
    daemon=getattr(self,"_pyroDaemon",None)
    if daemon: # TODO might not be even necessary: They should be registering themselves in the init
        self._odemicShared = True
        # only return a proxy if the object is a registered pyro object
        return (VigilantAttributeProxy, 
                (daemon.uriFor(self),
                 Pyro4.core.get_oneways(self),
                 Pyro4.core.get_asyncs(self),
                 self.max_discard, self.unit, self.readonly), 
                # in the state goes everything that might be recursive
                (_core.dump_roattributes(self), )
                )
    else:
        return self.__reduce__()
    
Pyro4.Daemon.serializers[VigilantAttribute] = odemicVASerializer

     
class StringVA(VigilantAttribute):
    """
    A VA which contains a string
    """
    
    def __init__(self, value="", *args, **kwargs):
        VigilantAttribute.__init__(self, value, *args, **kwargs)
        
    def _set(self, value):
        if not isinstance(value, basestring):
            raise InvalidTypeError("Value '%s' is not a string." % str(value))
        VigilantAttribute._set(self, value)

class FloatVA(VigilantAttribute):
    """
    A VA which contains a float
    """
    
    def __init__(self, value=0.0, *args, **kwargs):
        VigilantAttribute.__init__(self, value, *args, **kwargs)
        
    def _set(self, value):
        try:
            converted = float(value)
        except ValueError:
            raise InvalidTypeError("Value '%s' is not a float." % str(value))
        VigilantAttribute._set(self, converted)

class IntVA(VigilantAttribute):
    """
    A VA which contains a float
    """
    
    def __init__(self, value=0, *args, **kwargs):
        VigilantAttribute.__init__(self, value, *args, **kwargs)
        
    def _set(self, value):
        # we really accept only int, to avoid hiding lose of precision
        if not isinstance(value, int):
            raise InvalidTypeError("Value '%s' is not a int." % str(value))
        VigilantAttribute._set(self, value)

class ListVA(VigilantAttribute):
    """
    A VA which contains a list of values
    """
    
    def __init__(self, value=[], *args, **kwargs):
        VigilantAttribute.__init__(self, value, *args, **kwargs)
        
    def _set(self, value):
        try:
            converted = list(value)
        except TypeError:
            raise InvalidTypeError("Value '%s' is not a list." % str(value))
        # TODO we need to also detect whenever this list is modified
        
        VigilantAttribute._set(self, converted)

# TODO maybe should provide a factory that can take a VigilantAttributeBase class and return it
# either Continuous or Enumerated

class Continuous(object):
    """
    Adds the ability to a VA to specify a min and max.
    It has an attribute range (2-tuple) min, max
    It checks that any value set is min <= val <= max
    """
    
    def __init__(self, range):
        """
        range (2-tuple)
        """
        self._set_range(range)
    
    def _get_range(self):
        return self._range
    
    @property
    def range(self):
        """The range within which the value of the VA can be"""
        return self._get_range()
    
    def _set_range(self, new_range):
        """
        Override to do more checking on the range.
        """
        if len(new_range) != 2:
                raise InvalidTypeError("Range '%s' is not a 2-tuple." % str(new_range))
        if new_range[0] > new_range[1]:
            raise InvalidTypeError("Range min (%s) should be smaller than max (%s)." 
                                   % (str(new_range[0]), str(new_range[1])))
        if hasattr(self, "value"):
            if self.value < new_range[0] or self.value > new_range[1]:
                raise OutOfBoundError("Current value '%s' is outside of the range %s-%s." % 
                            (str(self.value), str(new_range[0]), str(new_range[1])))
        self._range = tuple(new_range)

    # To be called only by the owner of the object
    @range.setter
    def range(self, value):
        self._set_range(value)
    
    @range.deleter
    def range(self):
        del self._range

    def _set(self, value):
        """
        Should be called _in addition_ to the ._set() of VigilantAttributeBase
        returns nothing
        Raises:
            OutOfBoundError if the value is not within the authorised range
        """
        if value < self._range[0] or value > self._range[1]:
            raise OutOfBoundError("Trying to assign value '%s' outside of the range %s-%s." % 
                        (str(value), str(self._range[0]), str(self._range[1])))

class Enumerated(object):
    """
    Adds the ability to a VA to specify a set of authorised values.
    It has an attribute choices which is of type set
    It checks that any value set is among choice
    """

    def __init__(self, choices):
        """
        choices (seq): all the possible value that can be assigned
        """
        self._set_choices(choices)
        
    def _set(self, value):
        if not value in self._choices:
            raise OutOfBoundError("Value '%s' is not part of possible choices: %s." % 
                        (str(value), ", ".join(map(str, self._choices))))
    
    def _get_choices(self):
        return self._choices
        
    @property
    def choices(self):
        return self._get_choices()
    
    def _set_choices(self, new_choices_raw):
        try:
            new_choices = frozenset(new_choices_raw)
        except TypeError:
            raise InvalidTypeError("Choices '%s' is not a set." % str(new_choices_raw))
        if hasattr(self, "value"):
            if not self.value in new_choices:
                raise OutOfBoundError("Current value '%s' is not part of possible choices: %s." % 
                            (str(self.value), ", ".join(map(str, new_choices))))
        self._choices = new_choices    
    
    @choices.setter
    def choices(self, value):
        self._set_choices(value)
    
    @choices.deleter
    def choices(self):
        del self._choices


class FloatContinuous(FloatVA, Continuous):
    """
    A simple class which is both floating and continuous
    """
    def __init__(self, value=0.0, range=[], unit=""):
        Continuous.__init__(self, range)
        FloatVA.__init__(self, value, unit=unit)

    def _set(self, value):
        # order is important
        Continuous._set(self, value)
        FloatVA._set(self, value)

class StringEnumerated(StringVA, Enumerated):
    """
    A simple class which is both string and Enumerated
    """
    def __init__(self, value, choices, unit=""):
        Enumerated.__init__(self, choices)
        StringVA.__init__(self, value, unit=unit)

    def _set(self, value):
        # order is important
        Enumerated._set(self, value)
        StringVA._set(self, value)

class FloatEnumerated(FloatVA, Enumerated):
    """
    A simple class which is both floating and enumerated
    """
    def __init__(self, value=0.0, choices=[], unit=""):
        Enumerated.__init__(self, choices)
        FloatVA.__init__(self, value, unit=unit)

    def _set(self, value):
        # order is important
        Enumerated._set(self, value)
        FloatVA._set(self, value)

class IntEnumerated(IntVA, Enumerated):
    """
    A simple class which is both int and enumerated
    """
    def __init__(self, value=0.0, choices=[], unit=""):
        Enumerated.__init__(self, choices)
        IntVA.__init__(self, value, unit=unit)

    def _set(self, value):
        # order is important
        Enumerated._set(self, value)
        IntVA._set(self, value)


class MultiSpeedVA(VigilantAttribute, Continuous):
    """
    A class to define speed (m/s) for several axes
    It's especially made for Actuator.speed: the value is a dict name => float
    Also the speed must be >0
    """
    def __init__(self, value={}, range=[], unit="m/s", *args, **kwargs):
        Continuous.__init__(self, range)
        assert(range[0] >= 0)
        VigilantAttribute.__init__(self, value, unit=unit, *args, **kwargs)
        
    # TODO detect whenever a value of the dict is changed 
    def _set(self, value):
        # a dict
        if not isinstance(value, dict):
            raise InvalidTypeError("Value '%s' is not a dict." % str(value))
        for axis, v in value.items():
            # It has to be within the range, but also > 0
            if v <= 0 or v < self._range[0] or v > self._range[1]:
                raise OutOfBoundError("Trying to assign axis '%s' value '%s' outside of the range %s-%s." % 
                            (str(axis), str(value), str(self._range[0]), str(self._range[1])))
        VigilantAttributeBase._set(self, value)



# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
