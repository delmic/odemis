******************************************
Back-end Application Programming Interface
******************************************

This describes the core API of Odemis. This is the API that device adapters must
follow to provide access to the underlying hardware. The back-end manager 
takes care of instantiating all the components of the microscope and sharing
them with the front-end. User interfaces and scripts can control the hardware
via the use of this API.

Microscope specific component

HwComponent:
This is the generic type for representing a hardware component. The subtypes are typical of microscopes in this model.


.role (ro, str): Role (function) of the component in the microscope (the software should used this to locate the different parts of the microscope in case the type is not sufficient: each role is unique in a given model)

.swVersion (ro, str): version of the HwComponent code itself. For all the device drivers provided with Odemis, this is identical to the Odemis version. It might be different if provided by a third party.

.hwVersion (ro, str): version of the hardware (and firmware and SDK) represented by this component. Rational: there can be many types of versions for just a given component and it depends a lot on how it's actually build. We cannot grasp every kind of detail. So either we make a metadata-like dict which will eventually appear as a string most probably or directly just a string.


A set of named properties: only the properties that are provided by the hardware are present. 
.. TODO: isPropertyAvailabe(name) tells whether a property is present or not, and .getProperties() returns a list of all properties available.

A set of named methods: if the component can support the method, then it is present, otherwise the component does not have the method. Eg: .degauss() for a SEM e-beam. A generic function isMethodAvailable(name) tells whether it's present or not. getMethods() returns all the methods present.

.. TODO: how to turn on/off the hardware components? Via a method or a property? How about an .state enumerated property which has 'on', 'standby',  'off' possible value. At init it should automatically turned on, and automatically turned standby (or off if it's ok). For now, some emitters have a .power VA which allow to stop the hardware from emitting when set to 0, but it's pretty ad-hoc.

Exceptions: call to methods, modifying some values or properties, accessing data-flows might generate exceptions.

.updateMetadata(metadata) (metadata: dict of str->value): update the metadata dict corresponding to the current physical state of the components affecting the component (detector). The goal is to attach this information to DataArrays. The key is the name of the metadata, which must be one of the constants model.MD_* whenever this is possible, but usage of additional strings is permitted. The detector can overwrite or append the metadata dict with its own metadata. The internal metadata is accumulative, so previous metadata keys which are updated keep their previous (i.e., they are not deleted).

.getMetadata() return (dict of str->value): returns the metadata of the component. Required only for emitters.

.selfTest() (optional): method to request the driver to test whether the component works properly. It should not (on purpose) lead the component to do dangerous actions (e.g.: rotate a motor as fast as possible). It most cases it should limit its check to validate that the hardware component is correctly connected and is ready to use.  Returns True if everything went fine (success), False otherwise (failure). It might also throw an exception, in which case the test is considered failed. Description of the problems that occur should be logged using logging.error() or at similar levels.

.. TODO: argument to allow dangerous actions?

.scan() (optional): static method which return a list of hw components that are available for being controlled by the driver. Each element in the list is a tuple with a user-friendly name (str) and a dict containing the arguments to be passed to __init__() for actually using this specific component (in addition to name, role and children).

.. TODO have an RO enumerated VA status, which indicate the state of the component in some standard way, with values from a constant type: RUNNING, IDLE, ERROR, OFF. Maybe it could even be a way to turn off the component or set it to powersave mode.

.. TODO: we actually need a way to be able to initialise a component later than at initialisation. Either __init__ raises an Error, and there is a special function to know the status of a component, or __init__ always succeeds, but if the component is OFF, then it will actually automatically be initialised later and be switched RUNNING then.

Microscope
==========

There is only one of such component in the system. It's (one of) the root of the graph. It can be accessed by a special method. Getting access to this component is getting access to the whole model.

.emitters: set of Emitters

.detectors: set of Detectors

.actuators: an Actuator/Stage used to change the physical position of the sample

.role: (secom, sparc, sem, epifluorescent)

Emitter
=======

Emitters represent a hardware component whose main purpose is to generate emissions which will interact (or not) with the sample. Eg: e-beam, light...

.affects (read-only): set of Detectors which are supposed to detect changes when the component is emitting.

.shape (read-only): the available range of emission for each dimension. A SEM e-beam has a 2D shape like (1024, 1024), while a simple light might have (1).

.. TODO: see if the shape should also indicate the “depth” (number of emission source/power).

Light
=====

Lights are a type of emitters which generates an electromagnetic radiation at one or several frequencies. Typically (but it's not compulsory), they generate visible light with a shape of (1) (i.e., no scanning).

.power (0<= float): FloatContinuous which contains the power generated by the hardware in Watt. 0 turns off the light. The range indicates the maximum power that can be generated.

.emissions (array of 0 <=float <=1): VigilantAttribute which contains one or more entries of relative strength of emission source. The actual wavelength generated by each source is described in the .spectra attribute (e.g., this can be seen as a palette-based  The hardware might or might not be able to generate light from all the entries simultaneously. However, the component should accept all potentially correct values and adapt the value to the actual hardware.

.spectra (RO, array of 5-tuple of float > 0): for each entry of power, contains a description of the spectrum generated by the entry if set to 1 (maximum). It contains a 5-tuples which represents the Gaussian shaped (bell-shaped) emission spectrum, with a min and max filter. The 3rd entry indicate the wavelength for which emission is maximum. The 2nd and 4th entries indicate the wavelengths for 1st and last quartile of the Gaussian. The 1st and 5th entries indicate the wavelengths for which is there is less than 1% of the maximum emission (irrespective of the Gaussian). The length of the array is always the same as the length of the emissions array. TODO: see whether this is a nice structure for describing a spectrum, or we'd need something even more complicated?

Scanner
=======

An emitter that scan a set of points repetitively.
.resolution (VA, tuple of ints, same dimension of shape, unit=px): the number of points to scan in each dimension. See notes for .resolution of a DigitalCamera.

.pixelSize (read-only VA, 2-tuple of float, unit=m): property representing the size of a pixel (in meters). More precisely it should be the average distance between the centres of two pixels (for each dimension).

.magnification (VA of float, optional, unit=ratio): how much the hardware component reduces the emitter movements (giving the effect of zooming into the center). Changing it will affect pixelSize, but no other properties (in particular, the region of interest gets zoomed as well).

The following three properties permit to define a region of interest.
.translation (VA, tuple of floats, unit=px): how much shift is applied to the center of the area acquired. It is expressed in pixels (the size of a pixel being defined by pixelSize, and so independent of .scale).

.scale (VA, tuple of floats or int, unit=ratio): ratio of the size of the scannable area divided by the size of the scanned area. Note that this is the inverse of the typical definition of scale (i.e., increasing the scale leads to a smaller scanned area). The advantage of this definition is that its meaning is very similar to binning. Note that the PIXEL_SIZE metadata of a dataflow will depend both on pixelSize and scale.

.rotation (VA, float, unit=rad): counter-clockwise rotation to apply on the original area to obtain the actual area to scan.
Rational: we could have done slightly differently by using a general .transformation (VA, array of float, shape of (3,3) for a 2D resolution). It would have been a transformation matrix from the scanning area to the actual value. Very generic, but more complex to use and read and the advanced transformations possible don't seem to be useful.

Detector
========

Detectors represent hardware components which receive emission from the sample (eg: SE detector, CCD camera sensor).

.data : Data-flow coming from this detector. If the detector provide more than one data-flow, data is the most typical flow for this type of detector. Other data-flows are provided via other names. (and several names can actually provide the same data-flow, e.g., aliases are permitted).

.shape (read-only): maximum value of each dimension of the detector. A CCD camera 2560x1920 with 12 bits intensity has a 3D shape (2560,1920,2048). The actual dimension of the data sent in the data-flow can be smaller, and found in the data-flow.

.pixelSize (read-only VA, 2-tuple of float, unit=m): property representing the size of a pixel (in meters). More precisely it should be the average distance between the centres of two pixels (for each dimension).

DigitalCamera
=============

DigitialCamera is a subtype of Detector which must have also as properties:
init arguments:
transpose (list of int): Allows to rotate/mirror the CCD. For each axis (indexed from 1) of the output data is the corresponding axis of the detector indicated. Each detector axis must be indicated precisely once. If an axis is mentioned as a negative number, it is mirrored. For example, the default (None) is equivalent to [1, 2] for a 2D detector. Mirroring on the Y axis is done with [1, -2], and if a 90° clockwise rotation is needed, this is done with [-2, 1]. 

.binning (2-tuple of int): how many CCD pixels are merged (for each dimension) to form one pixel on the image. Changing this property will automatically adapt the resolution to make sure the actual sensor region stays the same one. For this reason, it is recommended to set this property before the resolution property. It has a .range attribute with two 2-tuples for min and max.

.resolution (2-tuple of int): number of pixels in the image generated for each dimension (width, height). If it's smaller than the full resolution of the captor, it's centred. It's value is the same as the shape of the data generated by the Data Flow (taking into account that DataArrays' shape follow numpy's convention so height is first, and width second). Binning is taken into account, so a captor of 1024x1024 with a binning of 2x2 and resolution of 512x512 will generate a data of shape 512x512. If when setting it, the resolution is not available, another resolution can be picked. It  will try to select an acceptable resolution bigger than the resolution requested. If the resolution is smaller than the entire captor, the centre part of the captor is used. It has a .range attribute with two 2-tuples for min and max.

.exposureTime (float, continuous): time in second for the exposure for one image.

Actuator
========

Actuator represent hardware components which can move. For example a stage. In case of linear move the axis value is expressed in meter, and in case of rotation it is expressed in radiant. The most important concept this component brings is that a move can take a long time, so a move request is asynchronous, controlled via a Future.

init arguments:
inverted (set of string): the axes which the driver should control inverted (i.e., a positive relative move become negative, an absolute move goes at the symmetric position from the center, or any other interpretation that fit better the hardware)

.. TODO: support actuators that move to only specific positions (eg, a switch, the grating selection of a spectrograph). Instead of a .ranges, it would need a .choices (with either a set or a dict value → user-friendly string description).

.. TODO: need a way to indicate whether absolute positioning is possible. And if so, whether “homing” (calibration) procedure is needed to be run. add .initAbs() function to do the home procedure? Cannot be done automatically in most cases as it might move at a bad moment otherwise. So the interface needs to ask the user first before doing it. Could be a RO VA .canAbs (dict string (axis name) → value) with 3 values possible: False, NEED_INIT, True.

.role (ro, str): if it is the main way to move the sample in x, y (,z) axes, then it should be 'stage'.

.axes (ro, set of str): name of each axis available. The name is dependent on the role, for a stage they are typically 'x', 'y', 'z', 'rz' (rotation around Z axis).

.. TODO: it could be cleaner to have .axes a dict str → Axis object. The Axis object would have .position (RO), .unit (static), .speed, .range (static) and .rangeRel (static) or .choices (static), .canAbs (RO). .subscribe() and .unsubscribe() would manage subscription to the change of any of the properties.

.ranges (ro, dict of 2-tuple of numbers): (min, max) value of the axis for moving (relative and absolute are same)

.. TODO: .rangesRel: min, max value of moveRel: max is same as .ranges[1]-.ranges[0], min is the minimum distance which will actually move the motor (less, nothing happens).

.moveRel(pos) returns Future: method to move by a relative amount. Pos is a dict with each axis which must be moved. If an axis is not mentioned it should not be moved. If the hardware supports it the driver should move all axes simultaneously, if not, it will move them sequentially in a non specified order.

.moveAbs(pos) returns Future: optional method to move to a specific position. Pos is a dict with each axis which must be moved. If an axis is not mentioned it should not be moved.

.position (RO VA dict str → float): contains the current position of each axis in the actuator. If only relative move is possible, the driver has to maintain an “ideal” current position (by summing all the moves requested), with the initial value at 0 (or anything most likely).

TODO: use it to provide .ranges (dict of 2-tuple of numbers): (min, max) value of the axis for moving. It could also have .choices for the axes which have specific positions. A .unit should also be used to indicate the unit. Problem: it's annoying to have it represent all the axes. It might be better to have one VA per axis (but to support it over the current remote model, each VA must be a direct attribute of the component, so maybe position_axisname could be used).

.stop(axes=None): stops moving immediately. If the set of str axes is provided, only the axes listed are stopped, otherwise all the axes are stopped.

.affects (read-only): set of Detectors which might detect changes when the actuator moves.

.speed (VA dict str-> float): speed of each axis in m/s. It has a .range = (min, max) which is common for all the axes.
Note that .moveRel() and .moveAbs() are asynchronous. If several are requested before one is finished, it is up to the driver to ensure that the final position is equal to calling the moves while being synchronised (within an error margin). However the path that is taken to reach the final position is device dependent. So calling .moveAbs({“x”: 1}) and immediately after .moveRel({“x”: -0.5})  will eventually be equivalent to just one call to .moveAbs({“x”: 0.5}), but whether the stage passed by position x:1 is unknown (to the client).




