**********************************************
Microscope configuration syntax and convention
**********************************************

The back-end uses a configuration file to instantiate the components of the
microscope. There are few rules on how to define it. However, there are many
strong conventions used in order to get the back-end and clients know which
component does what in the microscope.

Syntax
======

A microscope file is a series of component descriptions in the `YAML format <http://www.yaml.org/spec/1.2/spec.html>`_.
For edition, if your editor doesn't support explicitly YAML, you can select Python for correct syntax highlighting.

The basic idea is to describe the model as set of named components (a mapping of
a name (str) to a mapping). Each component description has the following information:

 * Name of component
 * class (str): python class of the component. It should be a subclass of 
   ``odemis.driver`` or be a ``Microscope``. It is written as ``module.class``.
   If the component is create by delegation (i.e., it is provided by another 
   component), this is not a required key.
 * role (str): compulsory string representing the role of the component in the system
 * init: mapping of str → values representing the initialisation arguments (optional)
 * properties (optional) (mapping of str → values): properties to set at initialisation (should be existing and valid for the given component)
 * children (optional): mapping of str (arbitrary names defined by the class)
   → str (names of other components provided or used by this component). 
 * creator (optional): name of the component that will create and provide this 
   component. It is only valid if the component has no class specified. The
   creator component must have this component specified in its "children". 
   This key will be automatically filled in unless several components 
   use the component as a child (in which case it is required).
 * affects: sequence of str (names of other components). By default it is empty.
 * emitters (only for Microscope): sequence of str (names of other components)
 * detectors (only for Microscope): sequence of str (names of other components)
 * actuators (only for Microscope): sequence of str (names of other components)

Roles
=====

The main convention is use the role of each component to indicate the function
of each component in the microscope.

The microscope component can have as role:
 * optical: for an optical microscope (only)
 * sem: an SEM (only)
 * secom
 * sparc
 * sparc-simplex: a SPARC without any alignment controls
 * sparc2
 * delphi

Typical detectors found in a microscope can be of the following roles:
 * ccd: the main optical camera
 * se-detector: secondary electron detector of the SEM
 * bs-detector: backscattered electron detector of the SEM
 * ebic-detector: EBIC detector of the SEM
 * cl-detector: a cathodoluminescence detector, synchronised with the e-beam
 * spectrometer: A detector to acquire multiple wavelengths information
   simultaneously. It provides the same interface as a DigitalCamera,
   but the Y dimension of the shape is 1. If the device has actuators, for
   instance to change the centre wavelength, access to them is via another
   component "spectrograph" which affects this detector. See below.
 * spectrometer-integrated: same as the spectrometer, but the detector is also
   used as a 'ccd'.
 * monochromator: A detector to acquire one wavelength at a time.
 * overview-ccd: a (optical) view of the whole sample from above
 * chamber-ccd: a (optical) view of the inside chamber
 * time-correlator: a one-dimension detector with "T", the time, as dimension.
   It reports the energy emission over time (from a specific event).

Typical emitters can be of the following roles:
 * light: controls the excitation light of the fluorescence microscope
 * lens: the lens on the optical path of the optical microscope (or of the SPARC)
 * e-beam: scanner of the SEM.

Typical actuators found can be of the following roles:
 * stage: it can have 3 linear axes ("x", "y", and "z"), and 3 rotational axes
   ("rx", "ry", and "rz")
 * focus: Changes the lens distance to the sample. Must have "z" axis.
 * ebeam-focus: Changes the focus of the e-beam. Must have "z" axis.
 * chamber: manages the pressure and/or sample loading.
   It must have a "pressure" axis.
 * mirror: To move the mirror of the SPARC, can have four axes: x, y, rz (yaw), ry (pitch)
 * align: alignment actuator for the SECOM and DELPHI microscopes. 
   For the SECOM, it must have two axes: "a" and "b".
   For the DELPHI, it must have two axes: "x" and "y".
 * sem-stage: the stage of the DELPHI that moves the (whole) sample holder.
 * filter: Emission filter on the fluorescence microscope or the filter on the 
   optical path of the SPARC. It must have a "band" axis.
 * spectrograph: controls the actuators related to spectrometry. It should
   provide a "wavelength" axis and possibly also the following axes: "grating",
   and "slit-in".
 * spectrograph-dedicated: Same as a spectrograph, but must be accessed via optical
   fiber. It should have a "rx" axis.
 * spec-det-selector: Selector between multiple detectors connected to a
   spectrograph. It should have a "rx" axis.
 * lens-switch: Switch between lens on/off for the SPARC.
   It should have a "x" or "rx" axis.
 * ar-spec-selector: Selector between AR/Spectrometer for the SPARC.
   It should have a "rx" axis.
 * fiber-aligner: To move optical fiber, typically with axes "x" and "y".


Overview schemas
----------------

The figure below represents the different roles in a `secom`.

.. figure:: secom-roles.*
    :width: 50 %
    :align: center

    Schema of a SECOM and the roles of the components


The figure below represents the different roles in a `sparc2`, with every
supported type of detector connected.

.. figure:: sparc2-roles.*
    :width: 100 %
    
    Schema of a SPARCv2 and the roles of the components
