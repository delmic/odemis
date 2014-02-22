**********************************************
Microscope configuration syntax and convention
**********************************************

The back-end uses a configuration file to instantiate the components of the
microscope. There are few rules on how to define it. However, there are many
strong conventions used in order to get the back-end and clients know which
component does what in the microscope.

Roles
=====

The main convention is use the role of each component to indicate the function
of each component in the microscope.

The microscope component can have as role:
 
  * optical : for an optical microscope (only)
  * sem
  * secom
  * sparc

Typical detectors found in a microscope can be of the following roles:

  * ccd:
  * se-detector
  * bs-detector
  * spectrometer: A spectrometer. 
    It provides the same interface as a DigitalCamera,
    but the Y dimension of the shape is 1.
    If it can change of centre wavelength, it should have a child, 
    with role "spectrograph" that provide a "wavelength" axis and 
    possibly a "grating" axis.

Typical actuators found can be of the following roles:

  * stage
  * focus
  * mirror
  * align: alignment actuator for the SECOM microscope. 
    It must have two axes: a and b.
  * spectrograph: See the spectrometer

Typical emitters can be of the following roles:
  * light
  * filter
  * lens
  * e-beam: Scanner of the SEM.

