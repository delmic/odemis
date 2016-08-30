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
 * delphi

Typical detectors found in a microscope can be of the following roles:
 * ccd: the main optical camera
 * se-detector: secondary electron detector of the SEM
 * bs-detector: backscattered electron detector of the SEM
 * ebic-detector: EBIC detector of the SEM
 * cl-detector: a catholumincesence detector, synchronised with the e-beam
 * spectrometer: A spectrometer. 
   It provides the same interface as a DigitalCamera,
   but the Y dimension of the shape is 1.
   If it can change of centre wavelength, it should have a child, 
   with role "spectrograph" that provide a "wavelength" axis and 
   possibly a "grating" axis.
 * spectrometer-integrated: same as the spectrometer, but the detector is also
   used as a 'ccd'.
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
 * mirror: To move the mirror of the SPARC, can have four axes: x, y, rz (yaw), ry (pitch)
 * align: alignment actuator for the SECOM and DELPHI microscopes. 
   For the SECOM, it must have two axes: "a" and "b".
   For the DELPHI, it must have two axes: "x" and "y".
 * sem-stage: the stage of the DELPHI that moves the (whole) sample holder.
 * filter: Emission filter on the fluorescence microscope or the filter on the 
   optical path of the SPARC. It must have a "band" axis.
 * spectrograph: See the spectrometer
 * spectrograph-dedicated: Same as a spectrograph, but must be accessed via optical
   fiber.
 * chamber: manages the pressure and/or sample loading.
   It must have a "pressure" axis.
 * lens-switch: Switch between lens on/off for the SPARC
 * ar-spec-selector: Selector between AR/Spectrometer for the SPARC
 * fiber-aligner: To move optical fiber, typically with axes "x" and "y".
