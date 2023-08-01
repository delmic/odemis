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
 * secom: A SECOMv1 or SECOMv2
 * delphi: a DELPHI
 * meteor: a METEOR
 * enzel: an ENZEL
 * sparc: SPARCv1
 * sparc-simplex: a SPARC without any alignment controls (deprecated)
 * sparc2: SPARCv2

Roles of components found in the DELPHI/SECOM system:
-----------------------------------------------------
.. TODO additional missing components in SECOM confocal:
.. TODO * det-selector: Mirror to switch between ..
.. TODO * time-correlator: A one-dimension detector with "T", the time, as dimension. It reports the energy emission over time (from a specific event).
.. TODO * tc-scanner
.. TODO * tc-detectorX
.. TODO * tc-detectorX-live

Emitters:
 * e-beam: Electron beam of the SEM to scan the sample (emitter).
 * light: Controls the excitation light of the fluorescence microscope.
 * laser-mirror: Scanner of a confocal microscope.

Actuators:
 * stage: Moves the sample. It can have up to 3 linear axes (x, y, z) and 3 rotational axes (rx, ry, rz).
 * focus: Changes the distance between the sample and the optical detectors. It has one axis: z.
 * filter: Emission filter on the optical path to select a specific wavelength band. It has an axis: band.
 * ebeam-focus: Changes the focus position of the e-beam. It has one axis: z.
 * chamber: Manages the pressure and/or sample loading. It must have a "vacuum" axis to switch between the different vacuum states.
   If it also has a sensor to read the actual pressure, it should be provided on the .pressure VA.
 * pinhole: To change the size of the pinhole in a confocal microscope. It has one axis: d.
 * sem-stage: The stage of the DELPHI that moves the (whole) sample holder.
 * align: Alignment actuator for the SECOM and DELPHI microscopes.
   For the SECOM, it must have two axes: "a" and "b".
   For the DELPHI, it must have two axes: "x" and "y".
 * delayer: Actuator for optical path length extender to add time delay. 

Detectors:
 * se-detector: Secondary electron detector of the SEM (detector).
 * bs-detector: Backscattered electron detector of the SEM.
 * ebic-detector: EBIC detector of the SEM.
 * ccd: The main optical pixelated detector (e.g. ccd/cmos/spectral camera).
 * photo-detector(N): A 0D photon detector (eg, PMT or APD). It's currently used
   only in confocal microscopes (photo-detector0, photo-detectorN).

System:
 * lens: Defines the optical parameters (e.g magnification) of the optical path.

Roles of components found in the ENZEL system:
----------------------------------------------
The role of the microscope is *enzel*.

Emitters:
 * e-beam: Electron beam of the SEM to scan the sample.
 * light: Controls the excitation light of the fluorescence microscope.

Actuators:
 * ebeam-focus: Changes the focus position of the e-beam. It has one axis: z. 
 * filter: Emission filter to select a specific wavelength band. It has one axis: band.
 * stigmator: Controls the rotation of the astigmatic lens. It has one axis rz.

 * stage: Moves the sample. It has 3 linear axes (x, y, z) and 2 rotational axes (rx, rz).
   The component has the following metadata:

    #. FAV_POS_DEACTIVE: Loading/unloading position.
    #. FAV_POS_ACTIVE: Imaging position.
    #. FAV_POS_COATING: Coating position of the gas injection system (GIS).
    #. POS_ACTIVE_RANGE: The allowed position range during the FM/SEM imaging.
    #. FAV_POS_SEM_IMAGING: The position for SEM imaging consisting of 5 axes.
    #. FAV_POS_ALIGN: The initial position to start the alignment from.
    #. ION_BEAM_TO_SAMPLE_ANGLE: Angle of the e-beam with the sample when rx = 0.

 * focus: Changes the distance between the sample and the optical detectors. It has one axis: z. It has one metadata:
  
    #. FAV_POS_ACTIVE: The latest focus position for optical microscopy.

 * align: Alignment actuator. It has 2 axes: x and y. It has three metadata:

    #. FAV_POS_ACTIVE: The position corresponding to alignment.
    #. FAV_POS_DEACTIVE: The safe position to go such that the stage cannot hit the objective lens.
    #. FAV_POS_ALIGN: The default position when doing alignment.

Detectors:
 * se-detector: Secondary electron detector of the SEM. 
 * ccd: The main optical pixelated detector.

System:
 * sample-thermostat: Controls the temperature of the sample finely. The metadata are:

    #. SAFE_REL_RANGE: Safe operating temperature range relative to target temperature.
    #. SAFE_SPEED_RANGE: Safe operating speed range.

 * cooler: Controls the starting and stopping of the cooling process by changing the temperature setpoint of the cryo-stage.
 * lens: Defines the optical parameters (e.g magnification) of the optical path. 

Roles of components found in the METEOR system:
-----------------------------------------------
The role of the microscope is *meteor*.

Emitters:
 * light: Controls the excitation light of the fluorescence microscope.

Actuators:
 * filter: Emission filter to select a specific wavelength band.
 * stigmator: (optional) Controls the rotation of the astigmatic lens. It has one axis rz.
 * stage: Moves the sample. It has 3 linear axes (x, y, z) and 2 rotational axes (rx, rz).
   The component has the following metadata:

    #. FAV_POS_DEACTIVE: Loading/unloading position.
    #. FAV_POS_COATING: Coating position of the gas injection system (GIS).
    #. FM_IMAGING_RANGE: The allowed position range during the FM imaging.
    #. SEM_IMAGING_RANGE: The allowed position range during the SEM imaging.

 * focus: Changes the distance between the sample and the optical detectors. It has one axis: z. It has one metadata:

    #. FAV_POS_ACTIVE: The latest focus position for optical microscopy.

Detectors:
 * ccd: The main optical pixelated detector.
 * lens: Defines the optical parameters (e.g magnification) of the optical path.

Roles of components found in the SPARCv1/SPARCv2 system:
--------------------------------------------------------
The role of the microscope is *sparc* or *sparc2*.

Emitters:
 * e-beam: Electron beam of the SEM to scan the sample (emitter).

Actuators:
 * ebeam-focus: Changes the focus position of the e-beam. It has one axis: z.
 * mirror: To engage the parabolic mirror into the beam path.
   It has two axes in the SPARCv2 system: s (short), l (long).
 * mirror-xy: To perform the fine adjustments of the position of the parabolic mirror. It is the same as mirror,
   but has a different reference, where x and y are aligned with the x/y of the sample (and of the ebeam).
   It has two axes: x and y.
 * lens-mover: Allows to position lens 1 within the optical path perpendicular to the optical axis.
   Lens 1 focuses the incoming collimated light. It has an axis: x.
 * lens-switch: Switches lens 2 between two positions (on: within light path; off: outside of light path).
   Lens 2 is used to further focus the light coming from lens 1.
   It has an axis: x or rx.
   If the SPARC doesn't support Ek imaging, the axis can have just two choices: "on" and "off".
   If Ek imaging is supported, the axis should have a continuous range,
   and the metadata FAV_POS_ACTIVE and FAV_POS_DEACTIVE. It should then also provide POS_ACTIVE_RANGE to indicate
   the whole scanning range during Ek scanning.
 * brightlight: Is used to calibrate the position offset between the two detectors, the grating offset and
   the focus (mirror) within the spectrograph.
 * pol-analyzer: It is used to switch the quarter wave plate and the linear polarizer to well
   specified relative positions to analyze the polarization grade of the emitted light. It has one axis: pol.
 * quarter-wave-plate: Quarter wave plate or retarder component of the polarization analyzer.
   It is positioned in front of the linear polarizer. It has one axis: rz.
 * lin-pol: Linear polarizer component of the polarization analyzer.
   It is positioned after the quarter wave plate. It has one axis: rz.
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
 * focus: Changes the distance between the sample and the optical detectors. It has one axis: z.
 * spec-det-selector: Mirror to switch between multiple detectors connected to a spectrograph.
   It has an axis: rx.
   The rx axis has a set of positions, which as description have a list of strings representing the affects for each position.
 * spec-switch: Actuator to engage or retract the mirror responsible for redirecting direct laser light out or keep
   inside of a module. This will force the light onto an internal or external spectrometer.
   It typically only has one axis: x.
 * fiber-aligner: Actuator to move the optical fiber input in order to optimise the amount of light going to the fiber.
   It typically has axes: x and y.
 * spec-selector: Selector between the external (fiber) output and the internal spectrometer(s).
   It typically has one axis "x", which can be the same axis as the fiber-aligner.
   It has as metadata FAV_POS_ACTIVE, and FAV_POS_DEACTIVE corresponding to the positions when
   "active" (light goes to the external output) and "deactive" (light goes to the internal spectrometers).
   It also has as metadata FAV_POS_ACTIVE_DEST, FAV_POS_DEACTIVE_DEST which represents the affects
   when respectively in active and deactive positions.
 * ar-spec-selector: Selector between AR/Spectrometer for the SPARCv1.
   It changes the optical path between AR detector (ccd) and spectrometer.
   It has an axis: rx.
 * stage: Moves the sample. It can have up to 3 linear axes (x, y, z) and 3 rotational axes (rx, ry, rz).
 * scan-stage: Optional fast and accurate moving stage used to move the sample during an acquisition instead of
   moving the e-beam. It has two axes: x and y.

Detectors:
 * se-detector: Secondary electron detector of the SEM (detector).
 * ccd (or ccd0): the main optical pixelated detector (e.g. ccd/cmos/spectral camera).
 * ccd\ *N* (with *N* going from 1 to 9): another pixelated detector.
 * sp-ccd: the second pixelated detector (e.g. ccd/cmos/spectral camera). Deprecated, use ccd1.
 * spectrometer: A detector to acquire multiple wavelengths information simultaneously.
   It provides the same interface as a DigitalCamera, but the Y dimension of the shape is 1.
   If the device has actuators, for instance to change the centre wavelength or the orientation
   of the grating turret, they are accessed via the component "spectrograph", which affects this detector.
   Note that in case it's physically a 2D detector, it's possible to access the raw 2D data via the "sp-ccd" detector.
 * spectrometer\ *M* (with *M* going from 1 to 9): another spectrometer. Not necessarily matching the ccd\ *N* number.
 * spectrometer-integrated: A similar component as the "spectrometer", but corresponding to the "ccd" 2D detector.
   Deprecated, use spectrometer1.
 * cl-detector: A cathodoluminescence detector, synchronized with the e-beam.
 * monochromator: A detector to acquire one wavelength at a time.
 * overview-ccd: A (optical) view of the whole sample from above.
 * chamber-ccd: A (optical) view of the inside chamber.
 * time-correlator: A one-dimension detector with "T", the time, as dimension.
   It reports the energy emission over time (from a specific event).
 * tc-detector: A detector, typically an APD, which reports a count of detected photons over time. 

System:
 * lens: Contains parameters concerning the parabolic mirror and the lens system.
   If it has a polePosition VA, then the microscope is considered supporting Angular Resolved acquisition.
   If it has a mirrorPositionTop and mirrorPositionBottom VAs, then the microscope
   is considered supporting Ek (angular spectrum) acquisition.
 * power-control: Power supply for the hardware components (e.g., ccd, sp-ccd,
   polarization filters, lens actuators, spectrograph).


Overview schemas
----------------


.. figure:: secom-roles.*
    :width: 50 %
    :align: center

    Schema of a SECOM and the roles of the components

.. figure:: secom-confocal-roles.*
    :width: 70 %
    :align: center

    Schema of a SECOM confocal with fluorescence life-time imaging (FLIM) and the roles of the components

.. figure:: enzel-roles.*
    :width: 50 %
    :align: center    

    Schema of an ENZEL system and the roles of the components 

.. figure:: sparc2-roles.*
    :width: 100 %
    
    Generic schema of a SPARCv2 and the roles of most of supported components

.. figure:: SPARC2_AR.*
    :width: 100 %
    :align: center

    Schema of a SPARCv2 and the roles of the components for CL spectroscopy.

.. figure:: SPARC2_ARPOL.*
    :width: 100 %
    :align: center

    Schema of a SPARCv2 and the roles of the components for angle resolved CL polarimetry.

.. figure:: SPARC2_SPEC.*
    :width: 100 %
    :align: center

    Schema of a SPARCv2 and the roles of the components for angle resolved (AR) CL imaging.

.. figure:: SPARC2_ARPOLSPEC.*
    :width: 100 %
    :align: center

    Schema of a SPARCv2 and the roles of the components for angle resolved CL polarization spectroscopy.
    
.. figure:: SPARC2_StreakCam.*
    :width: 100 %
    :align: center

    Schema of a SPARCv2 and the roles of the components for CL spectrometry and streak camera to acquire temporal spectrum information.
