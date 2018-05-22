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
   If the component is created by delegation (i.e., it is provided by another
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
------------------------------------------
 * optical: for an optical microscope (only)
 * sem: an SEM (only)
 * secom
 * sparc
 * sparc-simplex: a SPARC without any alignment controls
 * sparc2 (sparcv2)
 * delphi

Roles of components found in the DELPHI/SECOM system:
----------------------------------------------------------------------------------
# TODO: more detailed explanations
Emitters:
 * e-beam: Electron beam of the SEM to scan the sample (emitter).
 * light: controls the excitation light of the fluorescence microscope.
 * laser-mirror: scanner of a confocal microscope

Actuators:
 * stage: It can have 3 linear axes (x, y, z) and 3 rotational axes (rx, ry, rz). # TODO: stage is where?
 * ebeam-focus: Changes the focus position of the e-beam. It has one axis: z.
 * chamber: Manages the pressure and/or sample loading. It must have a "pressure" axis.
 * pinhole: To change the size of the pinhole in a confocal microscope. It has one axis: d.
 * sem-stage: the stage of the DELPHI that moves the (whole) sample holder.
 * align: alignment actuator for the SECOM and DELPHI microscopes.
   For the SECOM, it must have two axes: "a" and "b".
   For the DELPHI, it must have two axes: "x" and "y".

Detectors:
 * se-detector: Secondary electron detector of the SEM (detector).
 * bs-detector: backscattered electron detector of the SEM
 * ebic-detector: EBIC detector of the SEM
 * ccd: the main optical pixelated detector (e.g. ccd/cmos/spectral camera).
 * photo-detector(N): a 0D photon detector (eg, PMT or APD). It's currently used
   only in confocal microscopes.

System:
 * lens: Contains parameters concerning the parabolic mirror and the lens system.

Roles of components found in the SPARC/SPARCv2 system:
-----------------------------------------------------------------------------------
Emitters:
 * e-beam: Electron beam of the SEM to scan the sample (emitter).

Actuators:
 * stage: It can have 3 linear axes (x, y, z) and 3 rotational axes (rx, ry, rz). # TODO: stage is where?
 * ebeam-focus: Changes the focus position of the e-beam. It has one axis: z.
 * mirror: To engage the parabolic mirror into the beam path.
   It has two axes in the SPARCv2 system: s (short), l (long).
 * mirror-xy: To perform the fine adjustments of the position of the parabolic mirror.
   It has two axes: x and y.
 * lens-mover: Allows to position lens 1 within the optical path perpendicular to the optical axis.
   Lens 1 focuses the incoming collimated light. It has an axis: x.
 * lens-switch: Switches lens 2 between two positions (on: within light path; off: outside of light path).
   Lens 2 is used to further focus the light coming from lens 1. It has an axis: x or rx.
 * brightlight: Is used to calibrate the position offset between the two detectors, the grating offset and
   the focus (mirror) within the spectrograph.
 * polarization analyzer: It is used to switch the quarter wave plate and the linear polarizer to well
   specified relative positions to analyze the polarization grade of the emitted light. It has one axis: pol.
 * quarter wave plate: First polarizer of the polarization analyzer. It has one axis: rz.
 * linear polarizer: Second polarizer of the polarization analyzer. It has one axis: rz.
 * slit-in-big: Slit is used to tune the spectral resolution. It can be switched between position "on",
   which is completely opened, and position "off", which is nearly closed. If switched to "off" axis "slit-in"
   in spectrograph is initiated, which allows a fine tuning of the slit size.
 * filter: Emission filter on the optical path of the SPARCv2 to select a specific wavelength band.
   It has an axis: band.
 * spectrograph: Controls the actuators related to spectrometry. It controls the spectrograph
   components slit and grating turret.
   It has an axis: wavelength.
   It has the optional axes: grating, slit-in (independent of each other).
   The grating turret can be either consisting of two mirrors (one on each side) or a mirror and
   a grating. Then axis "grating" controls the switching between these two positions.
   In combination with the axis "wavelength" the center wavelength of the grating can be selected.
   If a mirror is selected on the grating turret within the optical path, the spectrograph is not
   operated as a spectrograph in the classical sense anymore and the mandatory axis wavelength is 0.
   The axis "slit-in" controls the fine adjustments of the slit. If the slit is switched "on" via "slit-in-big"
   (completely open the slit), axis "slit-in" is forced to be completely opened.
   If the slit is switched to "off" via "slit-in-big", fine adjustments of the slit can be conducted via
   the axis "slit-in".
 * focus: Changes the lens distance to the sample. It has one axis: z. # TODO which lens?? fixed in spectrograph?
 * spec-det-selector: Mirror to switch between multiple detectors connected to a spectrograph.
   It has an axis: rx.
 * fiber-aligner: To move optical fiber, typically with axes "x" and "y". # TODO: explanation
 * ar-spec-selector: Selector between AR/Spectrometer for the SPARC.
   It has an axis: rx. # TODO: explanation
 * stage: TODO??
 * scan-stage: TODO???

Detectors:
 * se-detector: Secondary electron detector of the SEM (detector).
 * ccd: the main optical pixelated detector (e.g. ccd/cmos/spectral camera).
 * sp-ccd: the second pixelated detector (e.g. ccd/cmos/spectral camera).
 * spectrometer: Combines the components "spectrograph" and "sp-ccd".
   A detector to acquire for example multiple wavelengths information
   simultaneously or to acquire angular information.
   It provides the same interface as a DigitalCamera,
   but the Y dimension of the shape is 1. If the device has actuators, for
   instance to change the centre wavelength or the orientation of the grating turret,
   they can be accessed via the component "spectrograph", which affects this detector. # TODO y=1 still true?
 * spectrometer-integrated: Combines the components "spectrograph" and "ccd".
   A detector to acquire for example multiple wavelengths information
   simultaneously or to acquire angular information.
   It provides the same interface as a DigitalCamera.
   If the device has actuators, for
   instance to change the centre wavelength or the orientation of the grating turret,
   they can be accessed via the component "spectrograph", which affects this detector.
 * cl-detector: a cathodoluminescence detector, synchronised with the e-beam # TODO
 * monochromator: A detector to acquire one wavelength at a time. # TODO
 * overview-ccd: a (optical) view of the whole sample from above. # TODO
 * chamber-ccd: a (optical) view of the inside chamber. # TODO
 * time-correlator: a one-dimension detector with "T", the time, as dimension.
   It reports the energy emission over time (from a specific event). # TODO

System:
 * lens: Contains parameters concerning the parabolic mirror and the lens system.
 * power control unit: Power supply for the hardware components (e.g. ccd and sp-ccd
  (depending on the hardware), polarization filters, lens actuators, spectrograph).


Overview schemas
----------------

The figure below represents the different roles in a `secom`.

.. figure:: secom-roles.*
    :width: 50 %
    :align: center

    Schema of a SECOM and the roles of the components

The figure below represents the different roles in a `secom` with confocal optical microscope.

.. figure:: secom-confocal-roles.*
    :width: 50 %
    :align: center

    Schema of a SECOM confocal and the roles of the components



The figure below represents the different roles in a `sparc2`, with every
supported type of detector connected.

.. figure:: sparc2-roles.*
    :width: 100 %
    
    Schema of a SPARCv2 and the roles of the components



The figure below represents the different roles in a `sparc2` for CL spectroscopy (SPEC).

.. figure:: SPARC2_AR.*
    :width: 100 %
    :align: center

    Schema of a SPARCv2 and the roles of the components for CL spectroscopy (SPEC).

The figure below represents the different roles in a `sparc2` for angle resolved CL polarimetry (ARPOL).

.. figure:: SPARC2_ARPOL.*
    :width: 100 %
    :align: center

    Schema of a SPARCv2 and the roles of the components for angle resolved CL polarimetry (ARPOL).

The figure below represents the different roles in a `sparc2` for angle resolved CL imaging (AR).

.. figure:: SPARC2_SPEC.*
    :width: 100 %
    :align: center

    Schema of a SPARCv2 and the roles of the components for angle resolved CL imaging (AR).

The figure below represents the different roles in a `sparc2` for angle resolved CL polarization spectroscopy (ARPOLSPEC).

.. figure:: SPARC2_ARPOLSPEC.*
    :width: 100 %
    :align: center

    Schema of a SPARCv2 and the roles of the components for angle resolved CL polarization spectroscopy (ARPOLSPEC)