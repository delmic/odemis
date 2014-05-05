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
 
  * optical: for an optical microscope (only)
  * sem: an SEM (only)
  * secom
  * sparc
  * secom-mini : for a SECOM/Phenom microscope

Typical detectors found in a microscope can be of the following roles:

  * ccd: the main optical camera
  * se-detector: secondary electron detector of the SEM
  * bs-detector: backscatter electron detector of the SEM
  * spectrometer: A spectrometer. 
    It provides the same interface as a DigitalCamera,
    but the Y dimension of the shape is 1.
    If it can change of centre wavelength, it should have a child, 
    with role "spectrograph" that provide a "wavelength" axis and 
    possibly a "grating" axis.
  * overview-ccd: a (optical) view of the whole sample from above
  * chamber-ccd: a (optical) view of the inside chamber

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
  * align: alignment actuator for the SECOM microscope. 
    It must have two axes: "a" and "b".
  * filter: Emission filter on the fluorescence microscope or the filter on the 
    optical path of the SPARC. It must have a "band" axis.
  * spectrograph: See the spectrometer
  * chamber: manages the pressure and/or sample loading.
    It must have a "pressure" axis.

