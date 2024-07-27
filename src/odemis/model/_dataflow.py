# -*- coding: utf-8 -*-
"""
Created on 2 Apr 2012

@author: Éric Piel

Copyright © 2012-2021 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

# Provides data-flow: an object that can contain a large array of data regularly
# updated. Typically it is used to transmit video (sequence of images). It does it
# losslessly and with metadata attached (see _metadata for the conventional ones).

import Pyro4
import logging
import numpy
from odemis.model import _metadata, _vattributes
from odemis.util import inspect_getmembers
from odemis.util.weak import WeakMethod, WeakRefLostError
import os
import threading
import time
import weakref
import zmq

from . import _core


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

        if hasattr(obj, 'metadata'):
            # Create a shallow copy of the meta data, otherwise when the array
            # gets copied, both will use the same meta data dictionary.
            self.metadata = obj.metadata.copy()
        else:
            self.metadata = {}

    # Used to send the DataArray over Pyro (over ZMQ, we use an optimised way)
    def __reduce__(self):
        # take the normal output (need to convert to list to modify it)
        ret = list(numpy.ndarray.__reduce__(self))
        # add to the state our additional state
        ret[2] = (ret[2], self.metadata)
        return tuple(ret)

    def __setstate__(self, state):
        nd_state, md = state
        numpy.ndarray.__setstate__(self, nd_state)
        self.metadata = md

    # def __array_wrap__(self, out_arr, context=None):
    #     print 'In __array_wrap__:'
    #     print '   self is %s' % repr(self)
    #     print '   arr is %s' % repr(out_arr)
    #     # then just call the parent
    #     out_arr.metadata = self.metadata
    #     return numpy.ndarray.__array_wrap__(self, out_arr, context)


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
        self._lock = threading.RLock()  # need to be acquired to modify the set

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
            logging.debug("Listener %r subscribed, now %d subscribers", listener, len(self._listeners))
            if count_before == 0:
                try:
                    self.start_generate()
                except Exception as ex:
                    logging.error("Subscribing listener %r to the dataflow failed. %s", listener, ex)
                    self._listeners.discard(WeakMethod(listener))
                    logging.debug("Listener %r unsubscribed, now %d subscribers", listener, len(self._listeners))
                    raise

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
        self._remote_listeners = set()  # any unique string works
        self._sync_lock = threading.RLock()  # To ensure only one sync change at a time
        self._was_synchronized = False

        self._global_name = None  # to be filled when registered
        self._ctx = None
        self.pipe = None

        self._max_discard = max_discard
        self._max_discard_orig = max_discard  # Used when switching between synchronized and not
        self._max_discard_last_update = None  # Value when last updated (when there are no remote listeners)

    def _getproxystate(self):
        """
        Equivalent to __getstate__() of the proxy version
        """
        proxy_state = Pyro4.core.pyroObjectSerializer(self)[2]
        return proxy_state, _core.dump_roattributes(self)

    @property
    def max_discard(self):
        return self._max_discard

    @max_discard.setter
    def max_discard(self, value):
        self._max_discard = value
        self._update_pipe_hwm()

    # getter & setter for the remote proxy (only!)
    def _get_max_discard(self):
        return self.max_discard

    def _set_max_discard(self, value):
        self.max_discard = value

    def _update_pipe_hwm(self):
        """
        updates the high water mark option of OMQ pipe according to max_discard
        """
        # As of 0MQ v4.3.4, changing the HWM while connected still doesn't work correctly. It can
        # crash the server, or just drop messages unexpectedly. So, we just leave to a large
        # number and let the client drop the messages.
        # Uncomment below once 0MQ is fixed (on all supported platforms), so that when max_discard
        # is > 0, the server will drop messages early.
        return

        # if (self._max_discard == self._max_discard_last_update
        #     or self.pipe is None
        #    ):
        #     return
        #
        # # When discarding, allow a bit of delay, but nothing more: if more than
        # # 2 DataArray (= 2*2 msg) already queued, the _newest_ one will be dropped.
        # # The best would be to drop the _oldest_ messages.
        # # Note: before ZMQ 4.2, it was necessary to unbind/bind the pipe to update the HWM, however,
        # # this automatically drops all subscribers. And sometimes, even if there are no subscribers,
        # # it crashes ZMQ. From ZMQ 4.2, it's possible to change the HWM without rebinding.
        # hwm = 4 if self._max_discard else 2000
        # self.pipe.sndhwm = hwm
        # self._max_discard_last_update = self._max_discard
        # logging.debug("Updating 0MQ HWM to %d on %s (max_discard = %s)",
        #                hwm, self._global_name, self._max_discard)
        #
        # # TODO: in ZMQ v4, ZMQ_CONFLATE allows to have a queue of 1 message
        # # containing only the newest message. That sounds closer to what we
        # # need (though, currently multi-part messages are not supported).

    def synchronizedOn(self, event):
        """
        Changes the configuration of the DataFlow so that an acquisition starts just after
        (as close as possible) the event is triggered.
        Behaviour is unspecified if the acquisition is already running.
        A DataFlow can only wait for one event (or none).
        event (Event or None): event to synchronize with. Use None to disable synchronization.
        See the Event class for more information on synchronization.
        """
        with self._sync_lock:
            is_synchronized = event is not None
            if is_synchronized == self._was_synchronized:
                return  # No change

            self._was_synchronized = is_synchronized
            if is_synchronized:
                self._max_discard_orig = self.max_discard
                self.max_discard = 0
                logging.debug("Dataflow %s is synchronized on an event, will not discard data", self._global_name)
            else:
                self.max_discard = self._max_discard_orig

    def _register(self, daemon):
        """
        Get the dataflow ready to be shared. It gets registered to the Pyro
        daemon and over 0MQ. It should be called only once. Note that you have
        to call this method to register a dataflow, a simple daemon.register(df)
        is not enough.
        daemon (Pyro4.Daemon): daemon used to share this object
        """
        if zmq.zmq_version_info()[0] <= 2:
            raise NotImplementedError("0MQ v2 is not supported anymore, upgrade to 0MQ v3+")

        daemon.register(self)

        # create a zmq pipe to publish the data
        # Warning: notify() will most likely run in a separate thread, which is
        # not recommended by 0MQ. At least, we should never access it from this
        # thread anymore. To be safe, it might need a pub-sub forwarder proxy inproc
        self._ctx = zmq.Context(1)
        self.pipe = self._ctx.socket(zmq.PUB)
        self.pipe.linger = 1  # s, don't keep messages more than 1s after close
        self.pipe.sndhwm = 200  # For now, it's just hard-coded (see _update_pipe_hwm)
        # self._update_pipe_hwm()

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

    def get(self, asap=True):
        """
        Acquires one image and return it
        asap (boolean): if True, returns the first image received, otherwise
         ensures that the image has been acquired after the call to this function
        return (DataArray)
        Default implementation: it subscribes and, after receiving the first
         image, unsubscribes. It's inefficient but simple and works in every case.
        """
        if asap:
            min_time = 0
        else:
            min_time = time.time()

        is_received = threading.Event()
        data_shared = [None] # in python2 we need to create a new container object

        def receive_one_image(df, data, min_time=min_time):
            if data.metadata.get(_metadata.MD_ACQ_DATE, float("inf")) >= min_time:
                df.unsubscribe(receive_one_image)
                data_shared[0] = data
                is_received.set()

        self.subscribe(receive_one_image)
        is_received.wait()
        return data_shared[0]

    # subscribe and unsubscribe look like they could use @oneway (which would
    # speed up a bit calls to them), but as Pyro doesn't ensure the order, it's
    # not possible because it could lead to wrong behaviour in case of quick
    # subscribe/unsubscribe.
    def subscribe(self, listener):
        with self._lock:
            count_before = self._count_listeners()

            # add string to listeners if listener is string
            if isinstance(listener, str):
                self._remote_listeners.add(listener)
            else:
                assert callable(listener)
                self._listeners.add(WeakMethod(listener))

            # Use '%' instead of ',' because logging may be holding on too long to the reference,
            # which we really don't want
            logging.debug("Listener %r subscribed, now %d subscribers on %s" % (
                          listener, self._count_listeners(), self._global_name))
            if count_before == 0:
                try:
                    self.start_generate()
                except Exception as ex:
                    logging.error("Subscribing listener %r to the dataflow failed. %s", listener, ex)
                    if isinstance(listener, str):
                        # remove string from listeners
                        self._remote_listeners.discard(listener)
                    else:
                        self._listeners.discard(WeakMethod(listener))
                    logging.debug("Listener %r unsubscribed, now %d subscribers on %s", listener,
                                  self._count_listeners(), self._global_name)
                    raise

    def unsubscribe(self, listener):
        with self._lock:
            count_before = self._count_listeners()
            if isinstance(listener, str):
                # remove string from listeners
                self._remote_listeners.discard(listener)
            else:
                self._listeners.discard(WeakMethod(listener))

            count_after = self._count_listeners()
            logging.debug("Listener %r unsubscribed, now %d subscribers on %s", listener, count_after, self._global_name)
            if count_before > 0 and count_after == 0:
                self.stop_generate()

            if len(self._remote_listeners) == 0:
                # It's the right moment to unbind/rebind the pipe
                self._update_pipe_hwm()

    def notify(self, data):
        # publish the data remotely
        if self.pipe and len(self._remote_listeners) > 0:
            # TODO: is there any way to know how many recipients of the pipe?
            # If possible, we would detect it's 0, because some listener closed
            # without unsubscribing, and we would kick it out.
            # => use zmq_socket_monitor() to detect connection/disconnection and
            # update the count of subscribers, or detect when a remote_listener
            # is gone (if there is a way to associate it)

            # TODO thread-safe for self.pipe ?
            dformat = {"dtype": str(data.dtype), "shape": data.shape, "metadata": data.metadata}
            self.pipe.send_pyobj(dformat, zmq.SNDMORE)
            try:
                if not data.flags["C_CONTIGUOUS"]:
                    # if not in C order, it will be received incorrectly
                    # TODO: if it's just rotated, send the info to reconstruct it
                    # and avoid the memory copy
                    raise TypeError("Need C ordered array")
                self.pipe.send(memoryview(data), copy=False)
            except TypeError:
                # not all buffers can be sent zero-copy (e.g., has strides)
                # try harder by copying (which removes the strides)
                logging.debug("Failed to send data with zero-copy")
                data = numpy.require(data, requirements=["C_CONTIGUOUS"])
                self.pipe.send(memoryview(data), copy=False)

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
    def __init__(self, uri):
        """
        uri : see Proxy
        Note: there is no reason to create a proxy explicitly!
        """
        Pyro4.Proxy.__init__(self, uri)
        self._global_name = uri.sockname + "@" + uri.object
        # Should be unique among all the subscribers of the real DataFlow
        self._proxy_name = "%x/%x" % (os.getpid(), id(self))
        DataFlowBase.__init__(self)

        self._ctx = None
        self._commands = None
        self._thread = None

    @property
    def max_discard(self):
        return self._get_max_discard()

    @max_discard.setter
    def max_discard(self, value):
        self._set_max_discard(value)

    def __getstate__(self):
        # must permit to recreate a proxy to a data-flow in a different container
        proxy_state = Pyro4.Proxy.__getstate__(self)
        return proxy_state, _core.dump_roattributes(self)

    def __setstate__(self, state):
        proxy_state, roattributes = state
        Pyro4.Proxy.__setstate__(self, proxy_state)
        _core.load_roattributes(self, roattributes)

        self._global_name = self._pyroUri.sockname + "@" + self._pyroUri.object
        self._proxy_name = "%x/%x" % (os.getpid(), id(self))
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
        self._thread = SubscribeProxyThread(self, self._global_name, self._ctx)
        self._thread.start()

    def start_generate(self):
        # start the remote subscription
        if not self._thread:
            self._create_thread()
        self._commands.send(b"SUB")
        self._commands.recv()  # synchronise

        try:
            # send subscription to the actual dataflow and inform dataflow that this remote listener is interested
            # a bit tricky because the underlying method gets created on the fly
            Pyro4.Proxy.__getattr__(self, "subscribe")(self._proxy_name)
        except Exception as ex:
            logging.error("Subscribing to the dataflow failed. %s", ex)
            self._commands.send(b"UNSUB")  # asynchronous (necessary to not deadlock)
            raise

    def stop_generate(self):
        # stop the remote subscription
        Pyro4.Proxy.__getattr__(self, "unsubscribe")(self._proxy_name)
        self._commands.send(b"UNSUB")  # asynchronous (necessary to not deadlock)

    def __del__(self):
        try:
            # end the thread (but it will stop as soon as it notices we are gone anyway)
            if self._thread:
                if self._thread.is_alive():
                    if len(self._listeners):
                        if logging:
                            logging.debug("Stopping subscription while there "
                                          "are still subscribers because dataflow '%s' is going out of context",
                                          self._global_name)
                        Pyro4.Proxy.__getattr__(self, "unsubscribe")(self._proxy_name)
                    self._commands.send(b"STOP")
                self._commands.close()
                # Not needed: called when garbage-collected and it's dangerous
                # as it blocks until all connections are closed.
                # self._ctx.term()
        except Exception:
            pass
        try:
            Pyro4.Proxy.__del__(self)
        except Exception:
            pass # don't be too rough if that fails, it's not big deal anymore


class SubscribeProxyThread(threading.Thread):
    def __init__(self, df_proxy, uri, zmq_ctx):
        """
        df_proxy: the DataFlowProxy which uses this thread
        uri (string): unique string to identify the connection
        zmq_ctx (0MQ context): available 0MQ context to use
        """
        super().__init__(name="zmq for dataflow " + uri)
        self.daemon = True
        self.uri = uri
        self._ctx = zmq_ctx
        # don't keep strong reference to DataFlowProxy so that it can be garbage
        # collected normally and this will let us know then that we can stop
        self.weak_df = weakref.proxy(df_proxy)

        # create a zmq synchronised channel to receive _commands
        self._commands = zmq_ctx.socket(zmq.PAIR)
        self._commands.connect("inproc://" + uri)

        # create a zmq subscription to receive the data
        self._data = zmq_ctx.socket(zmq.SUB)
        # Don't automatically discard messages on 0MQ as it's hard to change live (based on max_discard)
        # and also as we receive 3 messages per DataArray that would be very unreliable.
        # Instead, we will discard the DataArray in the thread itself (based on the max_discard).
        self._data.rcvhwm = 0
        self._data.connect("ipc://" + uri)

    def run(self):
        """
        Process messages for commands and data
        """
        # Warning: this might run even when ending (aka "in a __del__() state")
        # Which means: logging might be None, and zmq might not be working
        # normally (apparently zmq.POLLIN == None during this time).
        try:
            poller = zmq.Poller()
            poller.register(self._commands, zmq.POLLIN)
            poller.register(self._data, zmq.POLLIN)

            # Maximum number of messages discarded in a row.
            # Read from the remote DataFlow when the subscription is started.
            max_discard = 0
            discarded = 0  # Number of messages discarded in a row
            while True:
                socks = dict(poller.poll())

                # process commands
                if self._commands in socks:
                    message = self._commands.recv()
                    if message == b"SUB":
                        self._data.setsockopt(zmq.SUBSCRIBE, b'')
                        max_discard = self.weak_df.max_discard
                        logging.debug("Subscribed to remote dataflow %s, with max_discard = %s", self.uri, max_discard)
                        self._commands.send(b"SUBD")
                    elif message == b"UNSUB":
                        self._data.setsockopt(zmq.UNSUBSCRIBE, b'')
                        if logging:
                            logging.debug("Unsubscribed from remote dataflow %s", self.uri)
                        # no confirmation (async)
                    elif message == b"STOP":
                        return
                    else:
                        logging.warning("Received unknown message %s", message)

                # receive data
                if self._data in socks:
                    # TODO: be more resilient if wrong data is received (can block forever)
                    array_format = self._data.recv_pyobj()
                    array_buf = self._data.recv(copy=False)
                    # logging.debug("Received new DataArray over ZMQ for %s", self.uri)
                    # more fresh data already?
                    if (discarded < max_discard
                        and self._data.getsockopt(zmq.EVENTS) & zmq.POLLIN
                       ):
                        discarded += 1
                        # logging.debug("Discarding object received as a newer one is available")
                        continue
                    # Don't log here, because if we are discarding message it's because we are running
                    # out of time, and logging is slow.
                    # TODO: only log the accumulated number every second, to avoid log flooding
                    if discarded:
                        logging.warning("Dataflow %s dropped %d arrays", self.uri, discarded)
                    discarded = 0
                    # TODO: any need to use zmq.utils.rebuffer.array_from_buffer()?
                    if len(array_buf):
                        array = numpy.frombuffer(array_buf, dtype=array_format["dtype"])
                    else:  # frombuffer doesn't support zero length array
                        array = numpy.empty((0,), dtype=array_format["dtype"])
                    array.shape = array_format["shape"]
                    darray = DataArray(array, metadata=array_format["metadata"])
                    self.weak_df.notify(darray)

        except ReferenceError:  # The DataFlow(Proxy) is gone
            # => stop this thread too
            logging.debug("Dataflow proxy %s is gone, stopping the subscription thread", self.uri)
            return
        except Exception:
            if logging:
                logging.exception("Ending ZMQ thread due to exception")
        finally:
            try:
                self._commands.close()
            except Exception:
                print("Exception closing ZMQ commands connection")
            try:
                self._data.close()
            except Exception:
                print("Exception closing ZMQ data connection")


def unregister_dataflows(self):
    # Only for the "DataFlow"s, the real objects, not the proxys
    for name, value in inspect_getmembers(self, lambda x: isinstance(x, DataFlow)):
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
    for name, value in inspect_getmembers(self, lambda x: isinstance(x, DataFlowBase)):
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
    daemon = getattr(self, "_pyroDaemon", None)
    if daemon:
        # only return a proxy if the object is a registered pyro object
        return DataFlowProxy, (daemon.uriFor(self),), self._getproxystate()
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
        self.affects = _vattributes.ListVA()  # list of names (str) of component

    def hasListeners(self):
        """
        returns (boolean): True if the event currently has some listeners, or
         False otherwise.
        """
        return not not self._listeners # = not empty

    def get_type(self):
        """
        Return the class of the object. Used to work-around Pyro's proxy limitation,
        when checking whether a trigger is a HwTrigger or a standard one.
        """
        return type(self)

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
        # To do all that clever shortcut, we need the actual object, that is why
        # listener is not directly a callback.
        # TODO: listener could be directly a callable, and if it is a bound method,
        # get the object and the method name, and reconstruct it with the direct
        # object
        callback = _core._getMostDirectObject(self, listener).onEvent
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
        proxy_state = Pyro4.core.pyroObjectSerializer(self)[2]
        return (proxy_state, _vattributes.dump_vigilant_attributes(self))


class HwTrigger(Event):
    """
    Special type of Event used to signal that a DataFlow should be synchronized
    on a hardware event (eg, a TTL signal received by the hardware).
    Using it to actually notify() is not allowed, as it's the physical trigger
    that should do that.
    """
    # TODO: add a "name" argument/attribute to allow differentiating between multiple
    # hardware triggers on the same component?

    def notify(self):
        raise ValueError("A HwTrigger cannot be used to send events in software")


class EventProxy(EventBase, Pyro4.Proxy):
    def __init__(self, uri):
        Pyro4.Proxy.__init__(self, uri)

    def __getstate__(self):
        # must permit to recreate a proxy to a data-flow in a different container
        proxy_state = Pyro4.Proxy.__getstate__(self)
        return (proxy_state, _vattributes.dump_vigilant_attributes(self))

    def __setstate__(self, state):
        proxy_state, vas = state
        Pyro4.Proxy.__setstate__(self, proxy_state)
        _vattributes.load_vigilant_attributes(self, vas)


def unregister_events(self):
    for name, value in inspect_getmembers(self, lambda x: isinstance(x, Event)):
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
    for name, value in inspect_getmembers(self, lambda x: isinstance(x, EventBase)):
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
    daemon = getattr(self, "_pyroDaemon", None)
    if daemon:
        # only return a proxy if the object is a registered pyro object
        return EventProxy, (daemon.uriFor(self),), self._getproxystate()
    else:
        return self.__reduce__()


Pyro4.Daemon.serializers[Event] = EventSerializer
