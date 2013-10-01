Architecture Overview
*********************

For stability purpose, the backend should run in a separate process than the user interface. If feasible, every driver (component instance) should also be run in a separate process.
One driver = one component?
Maintains a model representing the whole microscope hardware.
Metamodel: generic enough to represent any kind of microscope we might develop. Model: defined by us for each version of a microscope. Saved into a modifiable file using a structured format (our metamodel on top of XML, YAML...?). At initialisation the model is read, drivers instantiated according to it, and self-tests allow to validate which part of the microscope is currently usable. However, no automatic structure modification (eg: discover every component connected to the computer) so that if a component is off/broken, it's easy to detect.

.. figure:: sys-arch.*
    :width: 100 %
    :alt: Odemis architecture overview
    
    The architecture of Odemis is made of 2 main parts: the back-end, containing
    the drivers, and the front-end, providing the user interface.
    
There are in total 3 interfaces to/from the microscope model:
Driver (aka device adapters):
Connect Odemis to the hardware devices. There is a specific interface to write a driver. There is relatively direct relation between a driver (instance) and a microscope component (in the internal representation). In most case 1 driver instance is 1 microscope component (eg: digital camera, axis controller). However 1 driver instance should be able to provide several microscope components if the underlying connection requires them to be handle together (eg: SEM e-beam and SE-detector managed together). It should not happen that several driver instances are required to represent 1 microscope component: microscope components should be the smallest unit it makes sense to create device in a microscope (but in case of exception, it's always possible to write a device driver which glues several other device drivers, but that should not be seen from the odemis back-end point of view). The API should be relatively stable but extensible. Typically each device driver implement a set of common interfaces and also a set of interfaces specific to the type of component.

Microscope model instantiation: This is accessed by the user/microscope-technician via one file to represent how the microscope model can be created. It links microscope model and device drivers. It's a close representation of the internal model, and all information (but comments) in the file are to be found in the internal model. We need to provide a precise description of the model description format (derived from the internal python model). This format will follow the internal representation evolution and can even change independently, so there is not much stability to be expected.

Live microscope representation: this allows high-level programs to discover and manipulate the microscope independently of the hardware. It allows the program to get access to the internal model of the microscope. This model comes as a set of objects which allows flexible and efficient access to data. This interface should try to remain stable but evolutions and especially extensions can happen.

