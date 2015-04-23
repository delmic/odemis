****************************************
Basic objects of the component framework
****************************************

Each part of the software runs as a separate component. Components are isolated 
in containers, which are actually a Unix process listening for requests. In most
cases this is transparent to the development. However, there are a couple of 
guidelines and restrictions.

To stop the system, all the components should be terminated (:py:meth:`Component.terminate`)
and then the back-end container can be terminated (:py:meth:`Container.terminate`).
This container will ensure that all the other containers are also terminated.

All the methods of a component are directly accessible remotely.
However, not all attributes are remotely accessible.
Only the DataFlows, VigilantAttributes, Events, and roattributes are 
automatically shared. 
roattributes are read-only attributes which value must not be modified after 
initialisation. They are declared with the :py:obj:`@roattribute` decorator
(like a property).
Methods that do not return any value and for which the caller never needs to 
know when they are finished can be decorated with :py:obj:`@oneway` to improve performance.
When accessing an object running in the same container, a normal python object 
is always returned automatically.

In addition, when a component is accessed from a separate container (e.g., when 
accessing a hardware adapter from the GUI) the actual Python object is a proxy
to the real component. While in most cases this is transparent, you should be aware of:

* :py:func:`isinstance` (and everything related to type) will not work as expected 
  because all objects are actually :py:class:`Proxy`.
  So relying on class type to take a decision will not work.
  There is an (important) exception for the Component, VigilantAttribute, Future,
  DataFlow, and Event classes, which all have an equivalent \*Base classes from
  which the Proxy inherits. So for these types, :py:func:`isinstance` can be used.
  
  .. TODO It's recommended to rely on the .capabilities attribute. TODO create .capabilities. Create also a ._realclass_ on proxy?

* :py:func:`hasattr` (and everything related to accessing non-existing attributes) will
  not work as expected because Proxys *always* return an object from :py:meth:`__getitem__`
  (a :py:class:`Pyro4.core._RemoteMethod`).
  
  .. TODO It is recommended to rely on the .capabilities attribute, or if an attribute is expected compare the type of the attribute to _RemoteMethod.


Component
=========
This is the generic type for representing a component. This is an abstract class,
from which actual components classes inherit. It bears a parent and children in
order to be able to construct a tree.
No specific meaning is given to the tree structure,
but it is expected that children are instantiated *before* their parent.

.. py:class:: model.Component(name[, parent=None][, children=None])

    Initialise the component. 
    Depending on the implementation, it might require children or not.
    It can even create its own children (what is called "creation by delegation")
    and provide them via the :py:attr:`children` attribute.
    
    :param str name: name of the component
    :param parent: Parent of the component
    :type parent: Component or None
    :param children: Children of the component. The string corresponds to the 
        role that the children plays for the component and exact meaning is
        implementation dependent. If the value is a Component, it means the 
        child has already been created, and it is just a child in the logical
        tree of components. If the value is a dict (\*\*kwargs), then it means
        it is expected that the component create the child, with the given
        arguments.
    :type children: dict str → (Component or \*\*kwargs) 

    .. py:method:: terminate()
        
        Stops the component, and frees the resources it uses. After a call to this 
        method, it is invalid to call any other method, or access attributes of the
        instance. It is only possible to call this method again, and in this case it
        will do nothing.

    .. py:attribute:: name
        
        *(ro, str)* Name to be displayed/understood by the user.
        Note: it should only be stored, and should not be used to affect the behaviour of the component.

    .. py:attribute:: children

        *(VA, set of Components)* Set of children provided/contained by the Component.
        Filled in at initialisation by the device driver.

    .. py:attribute:: parent

        *(ro, Component or None)* Component which provides this Component.
        If None, it means the component was instantiated by itself (and not by
        delegation).
        It has to be set at initialisation.
        
The following helper functions allow to list selectively the special attributes
of a component.

.. py:function:: model.getVAs(component)

    :returns: all the VAs in the component with their name
    :rtype: dict of name → VigilantAttributeBase

.. py:function:: model.getROAttributes(component)

    :returns: all the names of the roattributes and their values
    :rtype: dict of name → value

.. py:function:: model.getDataFlows(component)

    :returns: all the DataFlows in the component with their name
    :rtype: dict of name → DataFlow

.. py:function:: model.getEvents(component)

    :returns: all the Events in the component with their name
    :rtype: dict of name → Events

DataArray
=========

Set of data, with its metadata. It's a subclass of `Numpy ndarray 
<http://docs.scipy.org/doc/numpy-1.6.0/reference/arrays.html>`_, with the 
additional attribute :py:attr:`metadata` which contains information about the 
data.
As a ndarray, it contains efficiently a multiple dimension array of data of one
type. 
All Numpy functions and routines that accept ndarrays should work with DataArrays.
When using functions that take multiple arrays, the output array will in most
case contain the same metadata as the first array. 
It might not be what is expected, and special care must be taken to update this
metadata.

Be aware that it mostly behaves like a normal ndarray, but in some corner cases 
(such as .min() returning a DataArray of empty shape, instead of a scalar), 
it might be safer to first cast it to an ndarray (ex: ``nd = da.view(numpy.ndarray)``).

.. py:class:: model.DataArray(data[, metadata=None])

    Creates a DataArray.
    
    :param ndarray data: the data to contain. It can also be a python list, in
        which case it will be converted.
    :param metadata: Metadata about the data. Each entry of the dictionary 
        represents one information about the data. For the list of metadata,
        refer to model.MD_* constants.
    :type metadata: dict str → value
    
    .. py:attribute:: metadata

        *(dict str → value)* The metadata.
        See also :py:meth:`HwComponent.updateMetadata` and :py:meth:`HwComponent.getMetadata`.

    .. TODO: list all the metadata possible

DataFlow
========
Represents a (possibly infinite) dataset which is generated by blocks over time
(as a *flow* along the time).
For example, this allows to represent the output of a hardware detector,
or the computed image whenever a user changes processing settings.

The basic behaviour of the object is very straightforward:
any client interested in the flow can *subscribe* to. From the moment it is
subscribed, the client will receive data in form of a :py:class:`DataArray` 
from this dataflow, and until it is *unsubscribed*. 

When there are no subscribers, the dataflow can stop generating the data entirely.
This allows to turn off the related hardware component if necessary, and 
reduce processor usage.
It is up to the implementation to define precisely what to do if too much data
is generated to be processed in time by the subscribers. Data might either be
dropped, or queued. The callback of the subscriber (called by :py:meth:`Dataflow.notify`)
might be executed in different threads. 
Therefore, a call to the callback might not be finished processing before another call
to the callback is started.
Each call to the callback receives one DataArray.
In any case, the calls to the callback are always ordered in the same order the data was generated.

When the dataflow is already generating data (i.e. there is at least one 
subscriber), the first data received by new subscriber might have been 
generated/acquired prior to the time of subscription. 

If there are settings or attributes that affect the generation of the data
(e.g., the exposure time for a CCD component), modifying them while data is
generated only affects the next data generation. In other words, the settings
are taken into account only at the beginning of a data acquisition. Note that
from a subscriber point of view this means that the behaviour might differ 
depending whether there are other subscribers or not (for the first data 
received).

.. py:class:: model.DataFlow()

    Initialise the DataFlow.
    This is an abstract class which actual dataflows should inherit from.

    .. py:method:: subscribe(callback):

        Registers a function (callable) which will receive new version of the data every time it is available with the metadata. The format of the callback is callback(dataflow, dataarray), with dataflow the dataflow which calls it and dataarray the new data coming (which should not be modified, as other subscribers might receive the same object). It returns nothing.

    .. TODO: optionally a “recommended update rate” which indicates how often we want data update maximum?

    .. TODO: optionally indicate whether the subscriber wants all the data, or only 
        cares about the last one generated.

    .. py:method:: unsubscribe(callback)

        Unregister a given callback. Can be called from the callback itself.

    .. py:method:: get()

        Acquire and returns one DataArray. It is equivalent to subscribing, and
        unsubscribing as soon as the first DataArray is received by the callback.
        
    .. TODO: maybe allow to .get several data in a row? Useful for example when doing spectrum acquisition.

    .. py:method:: synchronizedOn(Event or None)

        Changes the configuration of the DataFlow so that an acquisition starts just after (as close as possible) the event is triggered.
        A DataFlow can only wait for one event (or none).
        If None is passed, no synchronization is taking place.
        See :py:class:`Event` for more information on synchronization.

	
    .. py:attribute:: parent

        The component which owns this data-flow.


    The rest of the methods are private and should only be used by the DataFlow 
    subclass (or the classes related).

    .. py:method:: start_generate()

        internal to the data-flow, it is called when the first subscriber arrives.
        
    .. py:method:: stop_generate()

        internal to the data-flow, it is called when the last subscriber is gone.

    .. py:method:: notify(DataArray)

        to be used only by the component owning the DataArray. It provides the new data to every subscriber.


Event
=====

Object used to indicate that a specific event has happened. It allows to wait for an event before doing an action. For example a scanning emitter moving to the next position (pixel), the end of a complete line scan. There is only one owner (generator) of the event, but there might be multiple listeners. Each listener has a separate queue, which ensures it will never miss the fact an event has happened.


.. py:class:: model.Event()

    Initialise the Event.

    .. py:method:: wait(object, timeout=None)

        wait for the event to happen. Returns either True (the event has happened) or False (timeout, or the the object is no more synchronised on this event). It automatically remove from the listener queue the fact the event has happened.

    .. py:method:: clear(object)

        empties the queue of events.

    .. py:method:: subscribe(object)

        add the object as listener to the events. 
        
    .. TODO: allow to give a callback function, in which case it will just call the function, instead of having to do a wait? It should allow to avoid the scheduling latency (~1ms). Or maybe just a callback function, (and declare it as @oneway), then it's still extensible later to use the queue mechanism if object is not callable (e.g, just self).

    .. py:method:: unsubscribe(object)

        remove the object as listener.

    .. py:method:: trigger()

        Indicates an event has just occurred. Only to be done by the owner of the event.

Future
======

All asynchronous functions return a Future (:py:class:`concurrent.futures.Future`).
This is standard Python class, see the `official documentation 
<http://docs.python.org/dev/library/concurrent.futures.html>`_ for more information.
Nevertheless, we use a slightly different semantic, as :py:meth:`concurrent.futures.Future.cancel` might 
work while the task is being executed (oppositely to the official implementation
which fails as soon as the task has started to be executed). 

Note that within the component framework every method returning a future must 
be explicitly indicated. 
This is done by decorating them with @\ :py:func:`isasync`.
Futures will work even if the method is not decorated, however, from a behavioural point of view, 
they imply a very big performance penalty when used remotely.
The only exception is in case of the special :py:class:`InstantaneousFuture`.
As it defines an action already completed, it is fine to not decorate the
function specifically.

.. py:class:: concurrent.futures.Future()

    .. py:method:: cancel()

    Attempt to cancel the task. If the task has finished executing, it will fail
    and return False. If the task is being executed, it will be done in best 
    effort manner. If possible, the execution will be stopped immediately, and
    the work done so far *might or might not* be undone.

    .. py:method:: cancelled()

       Return ``True`` if the call was successfully cancelled.

    .. py:method:: running()

       Return ``True`` if the call is currently being executed and cannot be
       cancelled.

    .. py:method:: done()

       Return ``True`` if the call was successfully cancelled or finished
       running.

    .. py:method:: result(timeout=None)

       Return the value returned by the call. If the call hasn't yet completed
       then this method will wait up to *timeout* seconds.  If the call hasn't
       completed in *timeout* seconds, then a :exc:`TimeoutError` will be
       raised. *timeout* can be an int or float.  If *timeout* is not specified
       or ``None``, there is no limit to the wait time.

       If the future is cancelled before completing then :exc:`CancelledError`
       will be raised.

       If the call raised, this method will raise the same exception.

    .. py:method:: exception(timeout=None)

       Return the exception raised by the call.  If the call hasn't yet
       completed then this method will wait up to *timeout* seconds.  If the
       call hasn't completed in *timeout* seconds, then a :exc:`TimeoutError`
       will be raised.  *timeout* can be an int or float.  If *timeout* is not
       specified or ``None``, there is no limit to the wait time.

       If the future is cancelled before completing then :py:exc:`CancelledError`
       will be raised.

       If the call completed without raising, ``None`` is returned.

    .. py:method:: add_done_callback(fn)

       Attaches the callable *fn* to the future.  *fn* will be called, with the
       future as its only argument, when the future is cancelled or finishes
       running.

       Added callables are called in the order that they were added and are
       always called in a thread belonging to the process that added them.  If
       the callable raises a :py:exc:`Exception` subclass, it will be logged and
       ignored.  If the callable raises a :py:exc:`BaseException` subclass, the
       behaviour is undefined.

       If the future has already completed or been cancelled, *fn* will be
       called immediately.


.. py:class:: model.InstantaneousFuture([result=None][, exception=None])
    
    This creates a Future which is immediately finished.
    This is a helper class for implementations need to return a Future to
    conform to the API but are actually synchronous (and so the result is already
    available at the end of the method call.

.. py:class:: model.ProgressiveFuture()

    A Future which provides also information about the execution progress.
    
    
    .. py:method:: add_update_callback(fn)

        Adds a callback *fn* that will receive progress updates whenever a new one is
        available. 
        The callback is always called at least once, when the task is finished.
        
        :param fn: The callback.
            *past* is the number of seconds elapsed since the beginning of the task.
            *left* is the estimated number of seconds until the end of the
            task.
            If the task is not yet started, past can be negative, indicating
            the estimated time before the task starts. If the task is finished (or
            cancelled) the time left is 0 and the time past is the duration of the
            task. 
        :type fn: callable: (Future, float past, float left) → None


    The following two methods are only to be used by the executor, to provide 
    the update information.
    
    .. py:method:: set_start_time(t)
    
        :param float t: The time in seconds since epoch that the task (will be) started.
    
    
    .. py:method:: set_stop_time(t)


VigilantAttribute
===================

VigilantAttributes are objects purposed to be used as attributes of other objects.
As normal attributes, they contain a :py:attr:`value`, but they also provide
mechanisms to validate the value and to let interested code know when the
value changes.

It can also contain metadata on the value with the :py:attr:`unit`, 
:py:attr:`range` and :py:attr:`choices` attributes.


Typically they are used to configure the device to a specific mode (e.g., change the resolution of a camera, change the speed of a motor) or obtain information on the device (e.g., current temperature of a CCD sensor, internal pressure) in which case the property might be read-only.

.. py:class:: model.VigilantAttribute([initval=None][, readonly=False][, setter=None][, unit=None])

    Create a VigilantAttribute.
    
    :param initval: Original value.
    :param bool readonly: Whether the value can be changed afterwards
    :param callable setter: Callable to be used when the value is set. It is
        called with the request value, and must return the value that should
        actually be set. This is typically useful when not every value is 
        valid but the rules are not to be precisely known by the client (e.g.,
        the exposure time of a CCD component, in which case the setter will 
        accept any positive value but return the actual value set).
    :param str unit: the unit of the value. The convention is to set *None* when
        unknown or meaningless and "" if it is a unit-less ratio.

    .. py:attribute:: value

        The value. When setting a property to an invalid value (e.g, too big,
        not in the enumerated value, incompatible with the other values),
        depending on the implementation, the setter can either decide to silently 
        set the value to a valid one, modify other attributes of the object for this
        one to be valid (then observers of these other properties get notified), or 
        raise an exception.

        All the accesses are synchronous: at the end of a set, all the subscribers
        have been notified or an exception was raised.
        
    .. py:method:: subscribe(callback)
    
        Attaches the callable *callback* to the VigilantAttribute. 
        *callback* will be called when the value changes, with the
        new value as its only argument.
        Note that if the value is set to the same value it contained previously,
        no notification is sent.
        
        One important difference with the normal Python behaviour, is that 
        the VigilantAttribute does not hold a reference to *callback* (it only
        keeps a weak reference). This means that the caller of subscribe is 
        in charge to keep a reference to *callback* as long as it should 
        receive notifications. In particular, this means that lambda functions
        must be kept explicitly in reference by the caller (for example, in a list). 
        
        .. Rationale: this permits to have objects subscribed to a VA be easily garbage collected, without the developer having to ensure that every VA is unsubscribed when the object is not used. That also forces the subscribers to always be able to unsubscribe (as unsubscribe uses the callback as identifier).
        
    .. py:method:: unsubscribe(callback)

        Removes the callable from being called when value notification happens.

    .. py:attribute:: unit
    
        *(ro, str)*: The unit of the value. The convention is to express measured
        quantities whenever possible in SI units (e.g., m, rad, C, s).
     
    The following method can be used by the VigilantAttribute implementations

    .. py:method:: notify(value)
        
        Notify the subscribers with the given value.

The following two Mixin classes can be inherited by any VigilantAttribute class.

.. py:class:: model.ContinuousVA

    .. py:attribute:: range
    
        *(min, max)* minimum and maximum possible values (of the same type as
        the value.
        If the value of the VigilantAttribute is an Iterable (e.g. the resolution
        of a CCD), *min* and *max* contain the minimum and maximum for each index.

.. py:class:: model.EnumeratedVA

    .. py:attribute:: choices
    
        Set of valid values.

.range and .choices can be modified at runtime, but only by the owner of the VA and only if the current value is compatible. This should be avoided whenever possible because no notification is sent to the subscribers.


.. py:class:: model.FloatVA

    VigilantAttribute which can only contain floats or ints.
    
.. py:class:: model.IntegerVA

    VigilantAttribute which can only contain ints.
    
.. py:class:: model.BooleanVA

    VigilantAttribute which can only contain booleans.
    
.. py:class:: StringVA

    VigilantAttribute which can only contain strings.
    
.. py:class:: model.ListVA

    VigilantAttribute which can only contain an Iterable. The type of each 
    element might be different, and the length might change.
    
    Be careful when using list (instead of a tuple), clients which change the
    value must ensure to always set the entire value to a new object. In other
    words, never change just one element of the list. Failure to do so will 
    prevent notification to work.
    
    .. Rationale: because it is pretty hard to detect changes of a list.

.. py:class:: model.TupleContinuous

    VigilantAttribute which contains tuple of fixed length and has all the
    elements of the same type.
    It's allowed to request any value within the lower and upper bound of 
    :py:attr:`range`, but might also have additional constraints.
    The length of the original value determines the allowed tuple length.
    The type of the first element of the original value determines the allowed
    type.
    
.. py:class:: model.ResolutionVA

    VigilantAttribute which can only contain a tuple of ints of a fixed length.


Container
=========
A container is an isolated entity of execution. It executes Components in a 
separate Unix process. 
When developing a driver (i.e., one Component), it is not necessary to be aware of 
containers. 

.. py:class:: model.Container(name)

    Instantiate the container inside a newly created process.
    Do not call directly. Use :py:func:`model.createNewContainer` to create a
    new container.
    
    .. py:method:: instantiate(klass, kwargs)
    
        Instantiate a Component and publish it
        
        :param class klass: Component class
        :param kwargs: arguments for the __init__() of the component
        :type kwargs: dict (str → value)
        :returns: The new component instantiated
        :rtype: Component
    
    .. py:method:: terminate()
    
    .. py:method:: run()
    
    .. py:method:: close()
    

The following additional functions allow to manage containers.

.. py:function:: model.createNewContainer(name[, validate=True])
    
    Creates a new container in an independent and isolated process
    
    :param bool validate: whether the connection should be validated
    :returns: the (proxy to the) new container

.. py:function:: model.createInNewContainer(container_name, klass, kwargs)

    Creates a new component in a new container
    
    :param str container_name: Name of the container
    :param class klass: component class
    :param kwargs: arguments for the __init__() of the component
    :type kwargs: dict (str → value)
    :returns: the (proxy to the) new component
    
.. py:function:: model.getContainer(name[, validate=True])

    :param bool validate: whether the connection should be validated
    :returns: (a proxy to) the container with the given name
    :raises: an exception if no such container exist
    
.. py:function:: model.getObject(container_name, object_name)

    Returns an object in a container based on its name and
    Only the name of the main back-end container is fixed: :py:data:`model.BACKEND_NAME`.
    In practice, most components are either in 
    the back-end container or in a separate container with the same name as the 
    component.
    
    :param str container_name: Name of the container
    :param str object_name: Name of the object (for Components, it's the same as
      :py:attr:`Component.name`)
    :returns: (a proxy to) the object with the given name in the given container
    :raises: an exception if no such object or container exist


