# -*- coding: utf-8 -*-
"""
:created: 16 Feb 2012
:author: Éric Piel
:copyright: © 2012 - 2022 Éric Piel, Rinze de Laat, Philip Winkler, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License version 2 as published by the Free
    Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
    FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
    details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""
import logging
import math
import threading
from abc import ABCMeta, abstractmethod
from collections.abc import Mapping
from typing import Dict, Optional, Tuple

from odemis import model
from odemis.acq import acqmng, path
from odemis.acq.align.fastem import Calibrations
from odemis.acq.fastem import FastEMCalibration, FastEMROC
from odemis.acq.move import MicroscopePostureManager, MeteorTFS3PostureManager
from odemis.gui import (
    FG_COLOUR_BLIND_BLUE,
    FG_COLOUR_BLIND_ORANGE,
    FG_COLOUR_BLIND_PINK,
    conf,
)
from odemis.gui.conf.data import get_hw_settings_config
from odemis.gui.log import observe_comp_state
from odemis.gui.model import CALIBRATION_1, CALIBRATION_2, CALIBRATION_3
from odemis.gui.model._constants import (
    CHAMBER_PUMPING,
    CHAMBER_UNKNOWN,
    CHAMBER_VACUUM,
    CHAMBER_VENTED,
    CHAMBER_VENTING,
    STATE_DISABLED,
    STATE_OFF,
    STATE_ON,
)
from odemis.model import FloatContinuous, StringVA, hasVA


class MainGUIData(object):
    """
    Contains all the data corresponding to the entire GUI.

    In the MVC terminology, it's a model. It contains attributes to directly
    access the microscope components, and data to be used or represented in the
    entire GUI.

    Normally, there is only one instance of this object per running GUI, so only one microscope
    can be manipulated at a time by the interface. An instance of this class will normally be
    created in the `main.py` module during start-up of the GUI.

    The two main attributes are:

        .microscope:
            The HwComponent root of all the other components (can be None
            if there is no microscope available, like an interface to display
            recorded acquisition).
        .role (string): copy of .microscope.role (string) should be used to find out
            the generic type of microscope connected.

    There are also many .ccd, .stage, etc. attributes which can be used to access
    the sub-components directly.

    """
    # Mapping between the component role and the attribute name on the MainGUIData
    _ROLE_TO_ATTR = {
        "ccd": "ccd",
        # ccd* -> ccds[]
        "se-detector": "sed",
        "bs-detector": "bsd",
        "ebic-detector": "ebic",
        "cl-detector": "cld",
        "pc-detector": "pcd",
        "laser-mirror": "laser_mirror",
        # photo-detectorN -> photo_ds[]
        "time-correlator": "time_correlator",
        "tc-scanner": "tc_scanner",
        "tc-detector": "tc_detector",
        "tc-detector-live": "tc_detector_live",
        "spectrometer": "spectrometer",
        # spectrometer* -> spectrometers[]
        "sp-ccd": "sp_ccd",  # Only used for a hack in the Sparc2 align tab
        # sp-ccd* -> sp_ccds[]
        "spectrograph": "spectrograph",
        "spectrograph-dedicated": "spectrograph_ded",
        "monochromator": "monochromator",
        "chamber-ccd": "chamber_ccd",
        "overview-ccd": "overview_ccd",
        "stage": "stage",
        # In SPARC scan-stage is an extra stage that scans instead of moving the e-beam,
        # in FAST-EM scan-stage is the bare stage converted to move in the scan direction.
        "scan-stage": "scan_stage",
        "stage-bare": "stage_bare",
        "focus": "focus",
        "spec-ded-focus": "spec_ded_focus",
        "pinhole": "pinhole",
        "stigmator": "stigmator",
        "ebeam-focus": "ebeam_focus",
        "ebeam-blanker": "ebeam_blanker",
        "ebeam-gun-exciter": "ebeam_gun_exciter",
        "overview-focus": "overview_focus",
        "mirror": "mirror",
        "mirror-xy": "mirror_xy",
        "align": "aligner",
        "fiber-aligner": "fibaligner",
        "lens-mover": "lens_mover",  # lens1 of SPARCv2
        "lens-switch": "lens_switch",  # lens2 of SPARCv2. Supports EK if has FAV_POS_ACTIVE
        "spec-selector": "spec_sel",
        "spec-switch": "spec_switch",
        "spec-ded-aligner": "spec_ded_aligner",
        "pcd-selector": "pcd_sel",
        "chamber": "chamber",
        "light": "light",
        "light-aligner": "light_aligner",  # for light in-coupler on SPARCv2
        "brightlight": "brightlight",
        "brightlight-ext": "brightlight_ext",
        "backlight": "backlight",
        "filter": "light_filter",
        "cl-filter": "cl_filter",
        "lens": "lens",
        "e-beam": "ebeam",
        "fibsem": "fibsem",
        "chamber-light": "chamber_light",
        "overview-light": "overview_light",
        "pol-analyzer": "pol_analyzer",
        "streak-cam": "streak_cam",
        "streak-ccd": "streak_ccd",
        "streak-unit": "streak_unit",
        "streak-delay": "streak_delay",
        "streak-lens": "streak_lens",
        "tc-od-filter": "tc_od_filter",
        "tc-filter": "tc_filter",
        "slit-in-big": "slit_in_big",
        "sample-thermostat": "sample_thermostat",
        "asm": "asm",
        "multibeam": "multibeam",
        "descanner": "descanner",
        "mppc": "mppc",
        "ion-beam": "ion_beam",
        "ion-focus": "ion_focus",
        "ebeam-shift": "beamshift",
        "diagnostic-ccd": "ccd",
        "det-rotator": "det_rotator",
        "se-detector-ion": "ion_sed",
        "stage-global": "stage_global",
    }

    def __init__(self, microscope):
        """
        :param microscope: (model.Microscope or None): the root of the HwComponent tree
            provided by the back-end. If None, it means the interface is not
            connected to a microscope (and displays a recorded acquisition).

        """

        self.microscope = microscope
        self.role = None

        # The following attributes are either HwComponents or None (if not available)
        self.ccd = None
        self.stage = None
        self.scan_stage = None  # fast stage to scan, instead of the ebeam (SPARC)
        self.stage_bare = None # stage in the chamber referential
        self.focus = None  # actuator to change the camera focus
        self.pinhole = None  # actuator to change the pinhole (confocal SECOM)
        self.stigmator = None  # actuator to change the optical astigmatism (METEOR/ENZEL)
        self.aligner = None  # actuator to align ebeam/ccd (SECOM)
        self.laser_mirror = None  # the scanner on confocal SECOM
        self.time_correlator = None  # life-time measurement on SECOM-FLIM or SPARC
        self.tc_detector = None  # the raw detector of the time-correlator (for settings & rough 2D acquisition)
        self.tc_detector_live = None  # APD count live detector, for better data in FLIM live (optional)
        self.tc_scanner = None  # copy of the scanner settings for FLIM (optional)
        self.mirror = None  # actuator to change the mirror position (SPARC)
        self.mirror_xy = None  # mirror in X/Y referential (SPARCv2)
        self.fibaligner = None  # actuator to move/calibrate the fiber (SPARC)
        self.light_aligner = None  # actuator to move/calibrate the light aligner mirror (SPARCv2)
        self.light = None  # epi-fluorescence light (SECOM/DELPHI)
        self.brightlight = None  # special light for white illumination (SECOM) or calibration (SPARC)
        self.brightlight_ext = None  # external light for purple UV illumination, e.g. for FSLT (SPARCv2)
        self.backlight = None  # for dark field illumination (SECOM)
        self.light_filter = None  # emission light filter for SECOM/output filter for SPARC
        self.cl_filter = None  # light filter for SPARCv2 on the CL components
        self.lens = None  # Optical lens for SECOM/focus lens for the SPARC
        self.ebeam = None
        self.ebeam_focus = None  # change the e-beam focus
        self.ebeam_blanker = None  # for advanced blanker control (eg, pulsed)
        self.ebeam_gun_exciter = None  # for advanced e-beam control (eg, pulsed)
        self.fibsem = None  # Optional component to control the FIB/SEM on METEOR/MIMAS
        self.sed = None  # secondary electron detector
        self.bsd = None  # backscattered electron detector
        self.ebic = None  # electron beam-induced current detector
        self.cld = None  # cathodoluminescnence detector (aka PMT)
        self.pcd = None  # Probe current detector (to measure actual e-beam current)
        self.spectrometer = None  # 1D detector that returns a spectrum
        self.sp_ccd = None  # raw access to the spectrometer
        self.spectrograph = None  # actuator to change the wavelength/grating (on SPARCv2, it's directly on the optical path)
        self.spectrograph_ded = None  # spectrograph connected via an optical fiber (SPARCv2)
        self.spec_ded_focus = None  # focus on spectrograph dedicated (SPARCv2)
        self.spec_ded_aligner = None  # special lens aligner for the FSLT (SPARCv2)
        self.monochromator = None  # 0D detector behind the spectrograph
        self.lens_mover = None  # actuator to align the lens1 (SPARCv2)
        self.lens_switch = None  # actuator to align the lens2 (SPARCv2)
        self.spec_sel = None  # actuator to activate the path to the spectrometer (SPARCv2)
        self.spec_switch = None  # actuator to activate the path to an external spectrometer (SPARCv2)
        self.pcd_sel = None  # actuator to activate the path to the probe current
        self.chamber = None  # actuator to control the chamber (has vacuum, pumping etc.)
        self.chamber_ccd = None  # view of inside the chamber
        self.chamber_light = None   # Light illuminating the chamber
        self.overview_ccd = None  # global view from above the sample
        self.overview_focus = None  # focus of the overview CCD
        self.overview_light = None  # light of the overview CCD
        self.pol_analyzer = None  # polarization analyzer
        self.streak_cam = None  # streak camera
        self.streak_ccd = None  # readout camera of the streak camera
        self.streak_unit = None  # streak unit of the streak camera
        self.streak_delay = None  # delay generator of the streak camera
        self.streak_lens = None  # input optics in front of the streak camera
        self.tc_od_filter = None
        self.tc_filter = None
        self.slit_in_big = None
        self.sample_thermostat = None  # thermostat for temperature control of cryosecom
        self.asm = None  # acquisition server module of the fastem microscope
        self.multibeam = None  # multibeam scanner of the fastem microscope
        self.descanner = None  # descan mirrors of the fastem microscope
        self.mppc = None  # detector of the fastem microscope
        self.ion_beam = None
        self.ion_focus = None
        self.beamshift = None  # beam shift deflection controller
        self.det_rotator = None  # detector rotator of the fastem microscope
        self.ion_sed = None  # detector for the ions of a composited detector component
        self.stage_global = None  # stage with coordinates converted into a global coordinate system

        # Lists of detectors
        self.ccds = []  # All the cameras which could be used for AR (SPARC)
        self.sp_ccds = []  # All the cameras, which are only used for spectrometry (SPARC)
        self.spectrometers = []  # All the spectrometers (SPARC)
        self.photo_ds = []  # All the photo detectors on confocal SECOM or SPARC with time-resolved

        self.ebeamControlsMag = None  # None (if no ebeam) or bool

        # Indicates whether the microscope is acquiring a high quality image
        self.is_acquiring = model.BooleanVA(False)

        # Indicates whether a stream is in preparation (i.e., a prepare() future is active)
        self.is_preparing = model.BooleanVA(False)

        # Indicates whether the microscope is milling
        self.is_milling = model.BooleanVA(False)

        # The microscope object will be probed for common detectors, actuators, emitters etc.
        if microscope:
            self.role = microscope.role
            comps_with_role = []
            components = model.getComponents()

            for c in components:
                if c.role is None:
                    continue

                comps_with_role.append(c)
                try:
                    attrname = self._ROLE_TO_ATTR[c.role]
                    setattr(self, attrname, c)
                except KeyError:
                    pass

                # (also) add it to the detectors lists
                if c.role.startswith("ccd"):
                    self.ccds.append(c)
                elif c.role.startswith("sp-ccd"):
                    self.sp_ccds.append(c)
                elif c.role.startswith("spectrometer"):
                    self.spectrometers.append(c)
                elif c.role.startswith("photo-detector"):
                    self.photo_ds.append(c)

                # Otherwise, just not interested by this component

            #If the state of an HW component changes create an OS pop up message
            observe_comp_state(components)

            # Sort the list of detectors by role in alphabetical order, to keep behaviour constant
            for l in (self.photo_ds, self.ccds, self.sp_ccds, self.spectrometers):
                l.sort(key=lambda c: c.role)

            # Automatically pick the first of each list as the "main" detector
            if self.ccd is None and self.ccds:
                self.ccd = self.ccds[0]
            if self.sp_ccd is None and self.sp_ccds:
                self.sp_ccd = self.sp_ccds[0]
            if self.spectrometer is None and self.spectrometers:
                self.spectrometer = self.spectrometers[0]

            # Check for the most known microscope types that the basics are there
            required_roles = []
            if self.role in ("secom", "delphi", "enzel"):
                required_roles += ["e-beam", "light", "stage", "focus"]
                if self.role in ("secom", "enzel"):
                    required_roles += ["align", "se-detector"]
                if self.role == "enzel":
                    required_roles += ["ion-beam", "se-detector-ion"]
            elif self.role == "meteor":
                required_roles += ["light", "stage", "focus", "stage-bare"]
                # add additional roles when fibsem control enabled
                if self.fibsem:
                    required_roles += ["e-beam", "se-detector", "ebeam-focus",
                                       "ion-beam", "se-detector-ion", "ion-focus"]
            elif self.role == "mimas":
                required_roles += ["light", "stage", "focus", "align", "ion-beam"]
            elif self.role in ("sparc", "sparc2"):
                # SPARCv1 can also work without a lens
                required_roles += ["e-beam"]
                if self.role == "sparc2":
                    required_roles += ["lens"]
            elif self.role == "mbsem":
                required_roles += ["e-beam", "stage"]

            # (special case): remove stage role for meteor tfs_3, as it's replaced with sample_stage
            if self.role == "meteor":
                stage_bare = model.getComponent(role="stage-bare")
                md = stage_bare.getMetadata().get(model.MD_CALIB, {})
                if md.get("version", "tfs_1") == "tfs_3":
                    required_roles.remove("stage")

            for crole in required_roles:
                attrname = self._ROLE_TO_ATTR[crole]
                if getattr(self, attrname) is None:
                    raise KeyError("Microscope (%s) is missing the '%s' component" % (self.role, crole))
            # Add project_path string VA to notify when `cryo` projects change
            config = conf.get_acqui_conf()
            pj_last_path = config.get("project", "pj_last_path")
            self.project_path = StringVA(pj_last_path)  # a unicode
            # Check that the components that can be expected to be present on an actual microscope
            # have been correctly detected.

            if not any((self.ccd, self.photo_ds, self.sed, self.bsd, self.ebic, self.cld, self.spectrometer, self.time_correlator)):
                raise KeyError("No detector found in the microscope")

            if not self.light and not self.ebeam:
                raise KeyError("No emitter found in the microscope")

            # Optical path manager: used to control the actuators so that the
            # light goes to the right detector (in the right way).
            # On the SECOM/DELPHI it's mostly used to turn off the fan during
            # high-quality acquisition.
            try:
                self.opm = path.OpticalPathManager(microscope)
            except NotImplementedError as ex:
                logging.info("No optical path manager: %s", ex)
                self.opm = None

            # Used when doing SECOM fine alignment, based on the value used by the user
            # when doing manual alignment. 0.1s is not too bad value if the user
            # hasn't specified anything (yet).
            self.fineAlignDwellTime = FloatContinuous(0.1, range=(1e-9, 100),
                                                      unit="s")
            if microscope.role == "delphi":
                # On the Delphi, during grid pattern, the dwell time is fixed, at ~0.2s.
                # So the fine alignment dwell time should be at least 0.2 s.
                self.fineAlignDwellTime.value = 0.5

            if microscope.role in ["meteor", "enzel", "mimas"]:
                # List VA contains all the CryoFeatures
                self.features = model.ListVA()
                # VA for the currently selected feature
                self.currentFeature = model.VigilantAttribute(None)
                # VAs for currently selected targets
                self.targets = model.ListVA()
                self.currentTarget = model.VigilantAttribute(None)
            # Initialize settings observer to keep track of all relevant settings that should be
            # stored as metadata
            self.settings_obs = acqmng.SettingsObserver(microscope, comps_with_role)

            # There are two kinds of SEM (drivers): the one that are able to
            # control the magnification, and the one that cannot. The former ones
            # then relies on the user to report the current magnification by setting
            # it to the .magnification VA. Quite some parts of the GUI changes
            # depending on which type of SEM component we have, so save it here.
            # To distinguish it, the magnification VA is read-only on SEM with full
            # control (and .horizontalFoV is used to 'zoom'). On a SEM without
            # magnification control, .magnification is writeable, and they typically
            # don't have a .horizontalFoV (but that shouldn't be a problem).
            if self.ebeam is not None:
                self.ebeamControlsMag = self.ebeam.magnification.readonly and hasVA(self.ebeam, "horizontalFoV")
                if (not self.ebeamControlsMag and
                    hasVA(self.ebeam, "horizontalFoV") and
                    not self.ebeam.horizontalFoV.readonly):
                    # If mag is writeable, for now we assume FoV is readonly
                    logging.warning("ebeam has both magnification and horizontalFoV writeable")
                elif self.ebeamControlsMag and not hasVA(self.ebeam, "horizontalFoV"):
                    logging.warning("ebeam has no way to change FoV")

        # Chamber is complex so we provide a "simplified state"
        # It's managed by the ChamberController. Setting to PUMPING or VENTING
        # state will request a pressure change.
        chamber_states = {CHAMBER_UNKNOWN, CHAMBER_VENTED, CHAMBER_PUMPING,
                          CHAMBER_VACUUM, CHAMBER_VENTING}
        self.chamberState = model.IntEnumerated(CHAMBER_UNKNOWN, chamber_states)

        self.hw_settings_config = get_hw_settings_config(self.role)

        self.posture_manager = None  # Posture manager for the microscope  (on METEOR/MIMAS)

        # Used by the MIMAS, but needs to be initialized as empty for the SECOM & cryo microscopes.
        self.sample_centers: Dict[str, Tuple[float, float]] = {}  # sample name -> center position (x, y)

        # Set to True to request debug info to be displayed
        self.debug = model.BooleanVA(False)
        self.level = model.IntVA(0)  # Highest message level not seen by the user so far

        # Current tab (+ all available tabs in choices as a dict tab -> name)
        # Fully set and managed later by the TabBarController.
        # Not very beautiful because Tab is not part of the model.
        # MicroscopyGUIData would be better in theory, but is less convenient
        # do directly access additional GUI information.
        self.tab = model.VAEnumerated(None, choices={None: ""})

        # Indicate whether the gui is loaded as viewer
        self.is_viewer = self.microscope is None

    def protect_detectors(self):
        """
        Put all the potentially damageable detectors to a safe mode.
        """
        if self.streak_unit:
            # Note that in the SPARC acquisition tab, pausing the TemporalSpectrumSettingsStream
            # should have the same effect. However, in case an acquisition is running, or another
            # tab is active, the safest option is to directly set the streak camera to a safe state.
            try:
                if model.hasVA(self.streak_unit, "MCPGain"):
                    self.streak_unit.MCPGain.value = 0
            except Exception:
                logging.exception("Failed to reset the streak-unit MCPGain")

            try:
                if model.hasVA(self.streak_unit, "shutter"):
                    self.streak_unit.shutter.value = True
            except Exception:
                logging.exception("Failed to activate the streak-unit shutter")

            logging.info("Set streak-unit to a safe state")

        # TODO: add more detectors here
        # Example time-correlator shutter

    def stopMotion(self):
        """
        Stops immediately every axis
        """
        if self.microscope is None:
            return

        ts = []
        for c in self.microscope.alive.value:
            # Actuators have an .axes roattribute
            if not isinstance(c.axes, Mapping):
                continue
            # Run each of them in a separate thread, to ensure we stop all ASAP
            t = threading.Thread(target=self._stopActuator, name=c.name, args=(c,))
            t.start()
            ts.append(t)

        # Wait for all the threads to be finished
        for t in ts:
            t.join(5)
            if t.is_alive():
                logging.warning("Actuator %s still not done stopping after 5s", t.name)
        logging.info("Stopped motion on every axes")

    def _stopActuator(self, actuator):
        """
        Calls stop actuator.
        A separate function, so that it can be called in a thread (ie, non-blocking)
        """
        try:
            actuator.stop()
        except Exception:
            logging.exception("Failed to stop %s actuator", actuator.name)

    def getTabByName(self, name):
        """
        Look in .tab.choices for a tab with the given name
        name (str): name to look for
        returns (Tab): tab whose name fits the provided name
        raise:
            LookupError: if no tab exists with such a name
        """
        for t, n in self.tab.choices.items():
            if n == name:
                return t
        else:
            raise LookupError("Failed to find tab %s among %d defined tabs" %
                              (name, len(self.tab.choices)))

    def isAngularSpectrumSupported(self):
        """
        Detects whether the SPARC supports Angular Spectrum acquisition.
        It only makes sense on SPARCv2. "Simple/old" systems didn't support it.
        If lens-switch has MD_FAV_POS_ACTIVE, it's a sign that it's supported.
        return (bool): True if the SPARC supports EK imaging
        """
        if not self.ccds or not self.lens_switch or not self.lens:
            return False

        if model.hasVA(self.lens, "mirrorPositionTop") and model.hasVA(self.lens, "mirrorPositionBottom"):
            return True


class CryoMainGUIData(MainGUIData):
    """
    Data common to all Cryo tabs (METEOR/ENZEL/MIMAS).
    """
    SAMPLE_RADIUS_TEM_GRID = 1.25e-3  # m, standard TEM grid size including the borders
    # Bounding-box relative to the center of a sample, corresponding to usable area
    # for imaging/milling. Used in particular for the overview image.
    hwidth = SAMPLE_RADIUS_TEM_GRID / math.sqrt(2)
    SAMPLE_USABLE_BBOX_TEM_GRID = (-hwidth, -hwidth, hwidth, hwidth)  # m, minx, miny, maxx, maxy

    def __init__(self, microscope):
        super().__init__(microscope)

        # Controls the stage movement based on the imaging mode
        self.posture_manager = MicroscopePostureManager(microscope)
        if hasattr(self.posture_manager, "sample_stage"):
            self.stage = self.posture_manager.sample_stage
            logging.debug("Using sample stage with supported postures: %s", self.posture_manager.postures)

        # stage.MD_SAMPLE_CENTERS contains the data in almost the right format, but the
        # position is a dict instead of a tuple. => Convert it, while checking the data.
        # Ex: {"grid 1": {"x": 0.1, "y": -0.2}} -> {"grid 1": (0.1, -0.2)}
        sample_centers_raw = self.stage_bare.getMetadata().get(model.MD_SAMPLE_CENTERS)

        # TODO: on the METEOR, the MD_SAMPLE_CENTERS is on the stage-bare, in
        # the stage-bare coordinates (SEM). To display them, we'd need to
        # convert them to the stage coordinates. The acq.move code only needs
        # the stage-bare coordinates (even if in FM), but we would need the
        # sample centers position during the FLM mode, in the stage coordinates...
        # The PostureManager now has a method to convert the coordinates: to_sample_stage_from_stage_position(),
        # and the sample stage (should) provide a "global" coordinates referential system.
        # These need to be used.

        if sample_centers_raw:
            try:
                self.sample_centers = {n: (p["x"], p["y"]) for n, p in sample_centers_raw.items()}
            except Exception as exp:
                raise ValueError(f"Failed to parse MD_SAMPLE_CENTERS, expected format "
                                 f"{{\"grid 1\": {{\"x\": 0.1, \"y\": -0.2}}}}: {exp}")

        # Sample centres can not be directly used for meteor. The grid centres
        # are in stage-bare coordinates which need to be converted into
        # sample stage coordinates.
        if self.role == "meteor":
            self.sample_centers = None

        # Radius of a sample, for display
        self.sample_radius = self.SAMPLE_RADIUS_TEM_GRID
        # Bounding-box of the "useful area" relative to the center of a grid
        self.sample_rel_bbox = self.SAMPLE_USABLE_BBOX_TEM_GRID


class ScintillatorShape(metaclass=ABCMeta):
    """Base class representing a geometric shape."""
    def __init__(self, position: Tuple[float, float]):
        self.position = position

    @abstractmethod
    def get_size(self):
        pass

    @abstractmethod
    def get_bbox(self):
        pass


class RectangleScintillator(ScintillatorShape):
    """Class representing a rectangle."""
    def __init__(self, position: Tuple[float, float], width: float, height: float):
        super().__init__(position)
        self.width = width
        self.height = height

    def get_size(self):
        return self.width, self.height

    def get_bbox(self):
        return (
            self.position[0] - self.width / 2,
            self.position[1] - self.height / 2,
            self.position[0] + self.width / 2,
            self.position[1] + self.height / 2,
        )  # (minx, miny, maxx, maxy) [m]


class CircleScintillator(ScintillatorShape):
    """Class representing a circle."""
    def __init__(self, position: Tuple[float, float], radius: float):
        super().__init__(position)
        self.radius = radius

    def get_size(self):
        return 2 * self.radius, 2 * self.radius

    def get_bbox(self):
        return (
            self.position[0] - self.radius,
            self.position[1] - self.radius,
            self.position[0] + self.radius,
            self.position[1] + self.radius,
        )  # (minx, miny, maxx, maxy) [m]


class Scintillator:
    """Class representing a scintillator with its position and size."""
    def __init__(self, number: int, shape: ScintillatorShape):
        self.number = number
        self.shape = shape
        self.calibrations: Dict[str, FastEMCalibration] = {}


class Sample:
    """Class representing a sample carrier / holder for FastEM."""
    def __init__(self, type: str):
        self.type = type
        self.scintillators: Dict[int, Scintillator] = {}

    def find_closest_scintillator(self, position: Tuple[float, float]) -> Optional[Scintillator]:
        """
        Find the closest scintillator for the provided region of acquisition (ROA) position.
        :param position: (float, float) the position of the ROA.
        :return: int the number of the closest scintillator, or None if no scintillator is found.
        """
        mindist = 1  # distances always lower than 1 m
        closest_scintillator = None

        for scintillator in self.scintillators.values():
            dist = math.dist(position, scintillator.shape.position)
            if dist < mindist:
                mindist = dist
                closest_scintillator = scintillator

        return closest_scintillator


class FastEMMainGUIData(MainGUIData):
    """
    Data common to all FastEM tabs.
    """

    def __init__(self, microscope):
        super(FastEMMainGUIData, self).__init__(microscope)

        # Make sure we have a stage with the range metadata (this metadata is required to map user-selected
        # screen positions to stage positions for the acquisition)
        if self.stage is None:
            raise KeyError("No stage found in the microscope.")

        # Calibration sequence
        if microscope.name.lower().endswith("sim"):
            calib_1_calibrations = [Calibrations.OPTICAL_AUTOFOCUS,
                                    Calibrations.IMAGE_TRANSLATION_PREALIGN]
            calib_2_calibrations = [Calibrations.OPTICAL_AUTOFOCUS,
                                    Calibrations.IMAGE_TRANSLATION_PREALIGN,]
            calib_3_calibrations = [Calibrations.OPTICAL_AUTOFOCUS,
                                    Calibrations.IMAGE_TRANSLATION_PREALIGN]
        else:
            # Run image translation before image rotation to ensure the pattern is fully on the mppc, run it again
            # after image rotation because the pattern is not rotated exactly around its own center.
            calib_1_calibrations = [Calibrations.OPTICAL_AUTOFOCUS,
                                    Calibrations.IMAGE_ROTATION_PREALIGN,
                                    Calibrations.SCAN_ROTATION_PREALIGN,
                                    Calibrations.DESCAN_GAIN_STATIC,
                                    Calibrations.IMAGE_TRANSLATION_PREALIGN,
                                    Calibrations.IMAGE_TRANSLATION_FINAL,
                                    Calibrations.IMAGE_ROTATION_FINAL,
                                    Calibrations.IMAGE_TRANSLATION_FINAL]
            calib_2_calibrations = [Calibrations.OPTICAL_AUTOFOCUS,
                                    Calibrations.IMAGE_TRANSLATION_PREALIGN,
                                    Calibrations.DARK_OFFSET,
                                    Calibrations.DIGITAL_GAIN]
            calib_3_calibrations = [Calibrations.OPTICAL_AUTOFOCUS,
                                    Calibrations.IMAGE_TRANSLATION_PREALIGN,
                                    Calibrations.SCAN_ROTATION_FINAL,
                                    Calibrations.CELL_TRANSLATION]

        md = self.stage.getMetadata()
        if model.MD_POS_ACTIVE_RANGE not in md:
            raise KeyError("Stage has no MD_POS_ACTIVE_RANGE metadata.")
        # POS_ACTIVE_RANGE contains the bounding-box of the positions with a sample
        carrier_range = md[model.MD_POS_ACTIVE_RANGE]
        minx, miny = carrier_range["x"][0], carrier_range["y"][0]  # bottom-left of carrier 1 in m
        # Field-size
        sz = (self.multibeam.resolution.value[0] * self.multibeam.pixelSize.value[0],
              self.multibeam.resolution.value[1] * self.multibeam.pixelSize.value[1])
        # The SAMPLE_CENTERS contains information about the supported sample carriers
        # A sample carrier has a number of scintillators. A scintillator is associated with a number.
        # Further a scintillator is described by its shape and center. Given the shape type, the
        # shape's dimensions are described by its size (rectangle) or radius (circle).
        # Currently two shape types are supported namely rectangle and circle.
        # center (X, Y) [m] of each scintillator is measured from the bottom-left position of the sample carrier
        if model.MD_SAMPLE_CENTERS not in md:
            raise KeyError("Stage has no MD_SAMPLE_CENTERS metadata.")
        centers = md[model.MD_SAMPLE_CENTERS]

        self.samples = model.VigilantAttribute({})  # Dict[str, Sample]

        for sample_type, sample_info in centers.items():
            if sample_type not in self.samples.value:
                sample = Sample(type=sample_type)
                self.samples.value[sample_type] = sample
            for scintillator_number, scintillator_info in sample_info.items():
                center = scintillator_info["center"]
                shape = scintillator_info["shape"]
                position = (minx + center[0], miny + center[1])

                # Determine shape and dimensions
                if shape == "rectangle" and "size" in scintillator_info:
                    size = scintillator_info["size"]
                    shape = RectangleScintillator(position=position, width=size[0], height=size[1])
                elif shape == "circle" and "radius" in scintillator_info:
                    radius = scintillator_info["radius"]
                    shape = CircleScintillator(position=position, radius=radius)
                else:
                    raise ValueError("Unknown scintillator shape dimensions")

                scintillator = Scintillator(number=int(scintillator_number), shape=shape)
                for calibration_name in (CALIBRATION_1, CALIBRATION_2, CALIBRATION_3):
                    calibration = FastEMCalibration(name=calibration_name)
                    xmin = position[0] - 0.5 * sz[0]
                    xmax = position[0] + 0.5 * sz[0]
                    ymin = position[1] + 0.5 * sz[1]
                    ymax = position[1] - 0.5 * sz[1]
                    if calibration_name == CALIBRATION_1:
                        number = 1
                        colour = FG_COLOUR_BLIND_BLUE
                        calibration.sequence.value = calib_1_calibrations
                        # overlay location left on init
                        xmin -= 1.2 * sz[0]
                        xmax -= 1.2 * sz[0]
                    elif calibration_name == CALIBRATION_2:
                        number = 2
                        colour = FG_COLOUR_BLIND_ORANGE
                        calibration.sequence.value = calib_2_calibrations
                        # overlay location middle on init
                    elif calibration_name == CALIBRATION_3:
                        number = 3
                        colour = FG_COLOUR_BLIND_PINK
                        calibration.sequence.value = calib_3_calibrations
                        # overlay location right on init
                        xmin += 1.2 * sz[0]
                        xmax += 1.2 * sz[0]
                    calibration.region = FastEMROC(name=str(number),
                                                   scintillator_number=int(scintillator_number),
                                                   coordinates=(xmin, ymin, xmax, ymax), colour=colour)
                    scintillator.calibrations[calibration_name] = calibration
                self.samples.value[sample_type].scintillators[scintillator_number] = scintillator

        # Overview streams
        self.overview_streams = model.VigilantAttribute({})  # dict: int --> stream or None

        # Indicate state of ebeam button
        hw_states = {STATE_OFF, STATE_ON, STATE_DISABLED}
        self.emState = model.IntEnumerated(STATE_OFF, choices=hw_states)

        # Alignment status, reset to "not aligned" every time the emState or chamberState is changed
        self.is_aligned = model.BooleanVA(False)
        self.emState.subscribe(self._reset_is_aligned)
        self.chamberState.subscribe(self._reset_is_aligned)

        # The current user of the system
        self.current_user = model.StringVA()
        # The current sample in use
        self.current_sample = model.VigilantAttribute(None)
        # User preferred dwell time for single-beam acquisition
        self.user_dwell_time_sb = model.FloatVA()
        # User preferred dwell time for multi-beam acquisition
        self.user_dwell_time_mb = model.FloatVA()
        # Indicates the calibration state for Calibration 1
        self.is_calib_1_done = model.BooleanVA(False)
        # Indicates the optical autofocus calibration state; True: is calibrated successfully; False: not yet calibrated
        self.is_optical_autofocus_done = model.BooleanVA(False)
        # The user preferred horizontal field width for single-beam acquisition
        self.user_hfw_sb = model.FloatVA()
        # The user preferred resolution for single-beam acquisition
        self.user_resolution_sb = model.TupleVA()

    def _reset_is_aligned(self, _):
        self.is_aligned.value = False
