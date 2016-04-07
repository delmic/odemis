******************************************
Back-end Application Programming Interface
******************************************

This describes the core API of Odemis. This is the API that device adapters must
follow to provide access to the underlying hardware. The back-end manager takes care
of instantiating all the components of the microscope and sharing them with the
front-end. User interfaces and scripts can control the hardware via the use of this
API. It is specifically focused to represent and manipulate microscope hardware.

To get the (root) microscope component, the special :py:meth:`model.getMicroscope`
function can be used. There are a few other helper functions to directly access an
component:

.. py:function:: model.getComponents()

    Return all the components that are available in the back-end. 

    :returns: All the components 
    :rtype: set of HwComponents

.. py:function:: model.getComponent(name=None, role=None)

    Find a component, according to its name or role.
    At least a name or a role should be provided.

    :param str name: name of the component to look for
    :param str role: role of the component to look for
    :returns HwComponent: the component with the given name/role
    :raises LookupError: if no component with such a name is given


.. py:class:: HwComponent(name, role[, parent=None][, children=None])
    
    This is the generic type for representing a hardware component.
    
    This is an abstract class. Typically subclasses have a set of 
    :py:class:`model.VigilantAttribute` that represent the properties and
    settings of the underlying hardware.

    .. py:attribute:: role 
        
        *(RO, str)* Role (function) of the component in the microscope (the software should use this to locate the different parts of the microscope in case the type is not sufficient: each role is unique in a given model)

    .. py:attribute:: swVersion
    
        *(RO, str)* version of the HwComponent code itself, and of the underlying
        device driver if applicable.

    .. py:attribute:: hwVersion
    
        *(RO, str)* version of the hardware (and firmware and SDK) represented by this component.
    
    .. Rationale: there can be many types of versions for just a given component and it depends a lot on how it's actually build. We cannot grasp every kind of detail. So either we make a metadata-like dict which will eventually appear as a string most probably or directly just a string.

    .. py:attribute:: state
    
    	*(RO VA, int or Exception)*: the state of the component, which is either
    	ST_UNLOADED, ST_STARTING, ST_RUNNING, ST_STOPPED, or an exception
    	(most usually a HwError) indicating the error that the hardware is
    	experiencing. 
    
    .. TODO: how to turn on/off the hardware components? Via a method or a property? How about an .state enumerated property which has 'on', 'standby',  'off' possible value. At init it should automatically turned on, and automatically turned standby (or off if it's ok). For now, some emitters have a .power VA which allow to stop the hardware from emitting when set to 0, but it's pretty ad-hoc.

    .. TODO: isPropertyAvailabe(name) tells whether a property is present or not, and .getProperties() returns a list of all properties available.

    .. TODO: A set of named methods: if the component can support the method, then it is present, otherwise the component does not have the method. Eg: .degauss() for a SEM e-beam. A generic function isMethodAvailable(name) tells whether it's present or not. getMethods() returns all the methods present.

    .. py:attribute:: affects
    
        *(VA, list of str)* names of the components which can detect changes 
        when the component is used. Typically, this is a set of Detectors, which
        are affected by the emission of energy (in the case of an Emitter),
        or a movement (in the case of an Actuator). 

    .. py:method:: updateMetadata(metadata)

        Required only for detectors.
            
        Update the metadata dict corresponding to the current physical state of the components affecting the component (detector). The goal is to attach this information to DataArrays. The key is the name of the metadata, which must be one of the constants model.MD_* whenever this is possible, but usage of additional strings is permitted. The detector can overwrite or append the metadata dict with its own metadata. The internal metadata is accumulative, so previous metadata keys which are not updated keep their previous value (i.e., they are not deleted).
        
        :param metadata:
        :type metadata: dict of str → value

    .. py:method:: getMetadata()

        Required only for emitters.
        
        :return: the metadata of the component. 
        :rtype: dict of str → value

    .. py:method:: selfTest()
    
        *(optional)* Request the driver to test whether the component works properly. It should not (on purpose) lead the component to do dangerous actions (e.g.: rotate a motor as fast as possible). It most cases it should limit its check to validate that the hardware component is correctly connected and is ready to use.
        :returns: True if everything went fine (success), False otherwise (failure). It might also throw an exception, in which case the test is considered failed. Description of the problems that occur should be logged using logging.error() or at similar levels.

        .. TODO: argument to allow dangerous actions?

    .. py:staticmethod:: scan()
   
        *(optional)* Return a list of arguments that correspond to each
        available hardware (that could be controlled by this driver).
        Each element in the list is a tuple with a user-friendly name (str)
        and a dict containing the arguments to be passed to __init__() for 
        actually using this specific component (in addition to name, and role).

Microscope
==========

There is only one of such component in the system. It's (one of) the root of the
graph. It can be specifically accessed with function :py:func:`model.getMicroscope`.
Getting access to this component is getting access to the whole microscope "model".

.. py:class:: Microscope()

    .. py:attribute:: role
        
        *(RO, str)* Typical values are secom, sparc, sem, optical.

    .. py:attribute:: alive
        
        *(VA, set of Component)* All the components which are loaded.
        Should be considered read-only. It must only be modified by the back-end.

    .. py:attribute:: ghosts
        
        *(VA, dict str → state)* Name of the components which are not loaded, 
        and their state (or the error that caused them to fail loading, see
        :py:attr:`HwComponent.state`).
    	Should be considered read-only. It must only be modified by the back-end.

Emitter
=======

Emitters represent a hardware component whose main purpose is to generate energy
which will interact (or not) with the sample. For example, an electron beam, a
light...

.. py:class:: Emitter()

    .. py:attribute:: shape
    
        *(RO, list of ints)* the available range of emission for each dimension.
        For example, a SEM e-beam might have a 2D shape like 
        *(1024, 1024)*, while a simple light might have an empty shape of
        *()*.

    .. TODO: see if the shape should also indicate the “depth” (number of emission source/power).

Light
=====

Lights are a type of emitters which generates an electromagnetic radiation at one or
several frequencies. Typically (but it's not compulsory), they generate visible
light with a shape of (1) (i.e., no scanning).

.. py:class:: Light()

    .. py:attribute:: power
    
        *(VA, 0 <= float, unit=W)* FloatContinuous which contains the power generated by the hardware in Watt. 0 turns off the light. The range indicates the maximum power that can be generated.

    .. py:attribute:: emissions
    
        *(VA, list of 0 <= float <=1)* ListVA which contains one or more entries of relative strength of emission source.
        The actual wavelength generated by each source is described in the :py:attr:`Light.spectra` attribute (e.g., this can be seen as a palette-based pixel).
        The hardware might or might not be able to generate light from all the entries simultaneously.
        However, the component should accept all potentially correct values and adapt the value to the actual hardware.

    .. py:attribute:: spectra
    
        *(RO VA, list of 5-tuple of floats > 0)* for each entry of power, contains a description of the spectrum generated by the entry if set to 1 (maximum). It contains a 5-tuples which represents the Gaussian shaped (bell-shaped) emission spectrum, with a min and max filter. The 3rd entry indicate the wavelength for which emission is maximum. The 2nd and 4th entries indicate the wavelengths for 1st and last quartile of the Gaussian. The 1st and 5th entries indicate the wavelengths for which is there is less than 1% of the maximum emission (irrespective of the Gaussian). The length of the array is always the same as the length of the emissions array. 
        
        .. TODO: see whether this is a nice structure for describing a spectrum, or we'd need something even more complicated?

Scanner
=======

An emitter that scan a set of points repetitively.

.. py:class:: Scanner()
    
    .. py:attribute:: power
        
        *(VA, enumerated 0 or 1)* 0 turns off the emitter source (e.g., e-beam), 1 turns it
        on. If the source takes time to change state, setting the value is 
        blocking until the change of state is over.
    
    .. py:attribute:: pixelSize
    
        *(RO VA, tuple of floats, unit=m)* Size of a pixel (in meters).
        More precisely it should be the average distance between the centres of two pixels (for each dimension).
        
    .. py:attribute:: resolution
    
        *(VA, tuple of ints, same dimension of shape, unit=px)* Number of points to scan in each dimension. See notes in :py:attr:`DigitalCamera.resolution`.

    .. py:attribute:: dwellTime
    
        *(VA of float, optional, unit=s)* How long each pixel is scanned. Also called sometimes "integration time".

    .. py:attribute:: magnification
    
        *(VA of float, optional, unit=ratio)* How much the hardware component reduces the emitter movements (giving the effect of zooming into the center). Changing it will affect pixelSize, but no other properties (in particular, the region of interest gets zoomed as well).
        
    The following three attributes permit to define a region of interest 
    (i.e., a sub-region).
    
    .. py:attribute:: translation
    
        *(VA, tuple of floats, unit=px)* How much shift is applied to the center of the area acquired. It is expressed in pixels (the size of a pixel being defined by pixelSize, and so independent of .scale).

    .. py:attribute:: scale
    
        *(VA, tuple of floats or int, unit=ratio)* ratio of the size of the scannable area divided by the size of the scanned area. Note that this is the inverse of the typical definition of scale (i.e., increasing the scale leads to a smaller scanned area). The advantage of this definition is that its meaning is very similar to binning. Note that the MD_PIXEL_SIZE metadata of a dataflow will depend both on pixelSize and scale (i.e., MD_PIXEL_SIZE = pixelSize * scale).

    .. py:attribute:: rotation
    
        *(VA, float, unit=rad)* counter-clockwise rotation to apply on the original area to obtain the actual area to scan.
    
    .. Rationale: we could have done slightly differently by using a general .transformation (VA, array of float, shape of (3,3) for a 2D resolution). It would have been a transformation matrix from the scanning area to the actual value. Very generic, but more complex to use and read and the advanced transformations possible don't seem to be useful.


    .. py:attribute:: accelVoltage
    
        *(VA, float, unit=V)* Acceleration voltage of the e-beam.

    .. py:attribute:: probeCurrent
    
        *(VA, float, unit=A)* probe current of the e-beam (which is typically
        affecting the spot size linearly).

If there is a blanker available, it should be automatically set whenever no scanning
is needed, and automatically disabled when a scanning takes place.

Detector
========

Detectors represent hardware components which receive emission from the sample. For
example, a secondary electron detector, the CCD of a camera.

.. py:class:: Detector()

    .. py:attribute:: shape
    
        *(RO, list of ints)* maximum value of each dimension of the detector.
        A greyscale CCD camera 2560x1920 with 12 bits intensity has a 3D shape *(2560, 1920, 2048)*.
        A RGB camera has a shape of 4 values (eg, *(2560, 1920, 3, 2048)*)
        The actual size of the data sent in the data-flow can be smaller
        (though it should always have the same number of dimensions)
        and found in the data-flow.
        
    .. py:attribute:: data
    
        *(DataFlow)* Data coming from this detector. If the detector provide more than one data-flow, data is the most typical flow for this type of detector. Other data-flows are provided via other names. (and several names can actually provide the same data-flow, e.g., aliases are permitted).


    .. py:attribute:: pixelSize
    
        *(RO VA, tuple of floats, unit=m)* property representing the size of a pixel (in meters). More precisely it should be the average distance between the centres of two pixels (for each dimension).

DigitalCamera
=============

DigitialCamera is a subtype of Detector which detects light with an array.

.. py:class:: DigitalCamera()

    :param transpose: Allows to rotate/mirror the CCD image. For each axis (indexed from 1) of the output data is the corresponding axis of the detector indicated. Each detector axis must be indicated precisely once. If an axis is mentioned as a negative number, it is mirrored. For example, the default (None) is equivalent to *[1, 2]* for a 2D detector. Mirroring on the Y axis is done with *[1, -2]*, and if a 90° clockwise rotation is needed, this is done with *[-2, 1]*. 
    :type transpose: list of ints

    .. py:attribute:: binning
    
        *(VA, tuple of ints)* How many CCD pixels are merged (for each dimension) to form one pixel on the image. Changing this property will automatically adapt the resolution to make sure the actual sensor region stays the same one. For this reason, it is recommended to set this property before the resolution property. It has a .range attribute with two 2-tuples for min and max.

    .. py:attribute:: resolution
    
        *(VA, tuple of ints)* Number of pixels in the image generated for each dimension (width, height). If it's smaller than the full resolution of the captor, it's centred. It's value is the same as the shape of the data generated by the Data Flow (taking into account that DataArrays' shape follow numpy's convention so height is first, and width second). Binning is taken into account, so a captor of 1024x1024 with a binning of 2x2 and resolution of 512x512 will generate a data of shape 512x512. If when setting it, the resolution is not available, another resolution can be picked. It  will try to select an acceptable resolution bigger than the resolution requested. If the resolution is smaller than the entire captor, the centre part of the captor is used. It has a .range attribute with two 2-tuples for min and max.

    .. py:attribute:: exposureTime
    
        *(VA, float, unit=s)* time in second for the exposure for one image.

Actuator
========

Actuator represents hardware components which can move. For example a stage. In case
of linear move the axis value is expressed in meters, and in case of rotation it is
expressed in radians. The most important concept this component brings is that a
move can take a long time, so a move request is asynchronous, controlled via a
:py:class:`concurrent.futures.Future`.

Note that .moveRel() and .moveAbs() are asynchronous. If several moves are requested
before one is finished, the driver must  ensure that the final position is equal to
calling the moves while being synchronised (within an error margin). However the
path that is taken to reach the final position is implementation dependent. So
calling ``.moveAbs({“x”: 1})`` and immediately followed by ``.moveRel({“x”: -0.5})``
will eventually be equivalent to just one call to ``.moveAbs({“x”: 0.5})``, but
whether the stage passed by position *x=1* is unknown (to the client).

.. py:class:: Actuator()

    :param inverted: the axes which the driver should control inverted (i.e., a positive relative move become negative, an absolute move goes at the symmetric position from the center, or any other interpretation that fit better the hardware)
    :type inverted: set of str

    .. py:attribute:: role
    
        *(RO, str)* if it is the main way to move the sample in x, y (,z) axes, then it should be *"stage"*.
    
    .. py:attribute:: axes
    
        *(RO, dict str → Axis)* name of each axis available, and the :py:class:`Axis` information.
        The name is dependent on the role, for a stage they are typically 'x', 'y', 'z', 'rz' (rotation around Z axis).

    .. py:attribute:: speed
    
        *(VA, dict str → float)* speed of each axis in m/s. 
        The value allowed is axis dependent and is indicated via the :py:attr:`Axis.speed` as a range.  
        	
    .. py:attribute:: position
    
        *(RO VA, dict str → float)* The current position of each axis in the actuator.
        If only relative moves is possible, the driver has to maintain an “ideal”
        current position (by summing all the moves requested), with the initial
        value at 0 (or anything most likely). It is up
        to the implementation to define how often it is updated, but should be
        updated at least after completion of every moves.
        The value allowed is axis dependent and is available via the 
        :py:attr:`Axis.choices` or :py:attr:`Axis.range` .
    
    .. py:attribute:: referenced
    
        *(RO VA, dict str → bool)* Whether axes have been referenced or not.
        For the actuators which requires referencing to give accurate position
        information.
        If an axis cannot be referenced at all (e.g., not sensor), it is not 
        listed.

    .. py:method:: moveRel(shift)
    
        Request a move by a relative amount. If the hardware supports it, the 
        driver should move all axes simultaneously, otherwise, axes will be moved
        sequentially in a non-specified order.
        Note that if the axis has  :py:attr:`Axis.canUpdate` ``True``, that
        method will accept the argument ``update``.
        See the documentation of that attribute for more information.
        
        :param shift: distance (or angle) that should be moved for each axis. 
            If an axis is not mentioned it should not be moved.
        :type shift: dict str → float
        :rtype: Future 

    .. py:method:: moveAbs(pos)
        
        Requests a move to a specific position.
        Note that if the axis has  :py:attr:`Axis.canUpdate` ``True``, that
        method will accept the argument ``update``.
        See the documentation of that attribute for more information.
        
        :param pos: Position to reach for each axis. If an axis is not mentioned it should not be moved.
        :type pos: dict str → float
        :rtype: Future

    .. py:method:: reference(axes)
        
        Requests a referencing move (sometimes called "homing"). After the move,
        the axis might be anywhere although if possible, it should be back to 
        the position before the call, or at "central" position. The position 
        information might be reset.
        
        :param axes: The axes which must be referenced
        :type axes: set str
        :rtype: Future

    .. py:method:: stop([axes=None])
    
        Stops all moves immediately. If multiple moves were queued, they are all
        cancelled.
        
        :param axes: Axes which must be stopped, otherwise all the axes are stopped.
        :type axes: set of str

Axis
====

Axis represents one axis of an :py:class:`Actuator`. It is a simple static object
that holds information on the axis, but all the dynamic information and actions are
performed via the :py:class:`Actuator`.

There are mostly two types of Axes, either *continuous*, with the
:py:attr:`Axis.range` attribute (e.g., translation actuator) or *enumerated*, with
the :py:attr:`Axis.choices` attribute (e.g., switch).
 
.. py:class:: Axis()

    .. py:attribute:: unit
    
        *(RO, str)* the unit of the axis position (and indirectly the speed).
        None indicates unknown or not applicable.
        "" indicates a ratio.

    .. py:attribute:: choices
    
        *(RO, set or dict)* Allowed positions. If it's a dict, the value
         is indicating what the position corresponds to.

    .. py:attribute:: range
    
        *(RO, tuple of 2 numbers)* min/max position (in the unit)

    .. TODO: .rangeRel: min, max value of moveRel: max is same as .ranges[1]-.ranges[0], min is the minimum distance which will actually move the motor (less, nothing happens).

    .. py:attribute:: speed
    
        *(RO, tuple of 2 numbers)* min/max speed of the axis (in unit/s).
        
    .. py:attribute:: canAbs
    
        *(RO, bool)* indicates whether the hardware supports absolute positioning.
        If it is not supported by hardware, the :py:meth:`Actuator.moveAbs` will
        approximate the move by a relative one.

    .. py:attribute:: canUpdate

        *(RO, bool)* indicates whether the hardware supports updates of moves.
        If it is supported by hardware, the methods the :py:meth:`Actuator.moveRel`
        and :py:meth:`Actuator.moveAbs` will accept the ``update`` argument. If this
        argument is ``True``, the requested move will continue the current move
        if the current move is limited to the same axes. In such case, the current
        move future will end before the actual end of the move, and that move
        might never be entirely complete (ie, the path followed by the actuator
        might not pass by the target position of the current move). If no move
        is on going, the update argument will have no effect, and the new requested
        move will take place as a standard move.
    	

Data and Metadata
=================

In Odemis, all the instrument data is represented via a :py:class:`DataArray`. The
attributes :py:attr:`DataArray.shape` and :py:attr:`DataArray.dtype` contains the
basic information on this data: the size of each dimension of the array and the type
of the elements in the array. Additional information about this data, the metadata,
can be recorded in the :py:attr:`DataArray.metadata` attribute. It is a dictionary
which allows to record information such as the date of acquisition, the exposure
time, the type of hardware used, the wavelength of the energy received, etc. For the
list of metadata, refer to the
model.MD_* constants. The file ``odemis/model/_metadata.py`` contains description of each metadata. 

The convention for the dimensions of the DataArray is to always record the data in
the order CTZYX, where C is channel (i.e., energy wavelength), T is time, and ZYX
are the 3 standard axes dimensions. If a DataArray has less than 5 dimensions, the
available dimensions are the last dimensions and the missing dimensions are
considered of size 1. For example, a 2D DataArray is considered by default of
dimensions YX. In some cases, it is more convenient or efficient to store dimensions
in a different order. It is possible to override the default dimension order by
using the ``MD_DIMS`` metadata. For instance, RGB data is often stored with the
channel as last dimension for display. Such case can be indicated with "YXC".

Convention about measurement units
==================================

Most of the data in Odemis is represented either as standard Python types, as
:py:class:`DataArray` or as :py:class:`VigilantAttribute`. This means that often
they do not bear unit information explicitly, even though they represent physical
quantities. The convention is to use the standard
`SI <http://en.wikipedia.org/wiki/SI>`_ measurement units whenever it can be
applied. For example, distance and wavelengths are expressed in meters (m), angles
in radians (rad), and times in seconds (s). Never express anything in multiples of a
official unit (e.g., never put anything in nm).
