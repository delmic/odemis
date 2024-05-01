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
from collections.abc import Mapping
from itertools import chain
import logging
import math
import threading
from typing import Dict, Tuple

from odemis import model
from odemis.acq import path, acqmng
from odemis.acq.move import MicroscopePostureManager
from odemis.gui import conf
from odemis.gui.conf.data import get_hw_settings_config
from odemis.gui.model._constants import (CHAMBER_UNKNOWN, CHAMBER_VENTED, CHAMBER_PUMPING,
                                         CHAMBER_VACUUM, CHAMBER_VENTING, STATE_OFF,
                                         STATE_ON, STATE_DISABLED)
from odemis.model import FloatContinuous, StringVA, hasVA
from odemis.gui.log import observe_comp_state
from odemis.gui.util.conversion import sample_positions_to_layout


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
        "overview-focus": "overview_focus",
        "mirror": "mirror",
        "mirror-xy": "mirror_xy",
        "align": "aligner",
        "fiber-aligner": "fibaligner",
        "lens-mover": "lens_mover",  # lens1 of SPARCv2
        "lens-switch": "lens_switch",  # lens2 of SPARCv2. Supports EK if has FAV_POS_ACTIVE
        "spec-selector": "spec_sel",
        "spec-switch": "spec_switch",
        "pcd-selector": "pcd_sel",
        "chamber": "chamber",
        "light": "light",
        "light-aligner": "light_aligner",  # for light in-coupler on SPARCv2
        "brightlight": "brightlight",
        "backlight": "backlight",
        "filter": "light_filter",
        "cl-filter": "cl_filter",
        "lens": "lens",
        "e-beam": "ebeam",
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
        self.backlight = None  # for dark field illumination (SECOM)
        self.light_filter = None  # emission light filter for SECOM/output filter for SPARC
        self.cl_filter = None  # light filter for SPARCv2 on the CL components
        self.lens = None  # Optical lens for SECOM/focus lens for the SPARC
        self.ebeam = None
        self.ebeam_focus = None  # change the e-beam focus
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

        # The microscope object will be probed for common detectors, actuators, emitters etc.
        if microscope:
            self.role = microscope.role
            comps_with_role = []
            components = model.getComponents()

            for c in components:
                if c.role is None:
                    continue
                try:
                    attrname = self._ROLE_TO_ATTR[c.role]
                    setattr(self, attrname, c)
                    comps_with_role.append(c)
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
                required_roles += ["light", "stage", "focus"]
            elif self.role == "mimas":
                required_roles += ["light", "stage", "focus", "align", "ion-beam"]
            elif self.role in ("sparc", "sparc2"):
                # SPARCv1 can also work without a lens
                required_roles += ["e-beam"]
                if self.role == "sparc2":
                    required_roles += ["lens"]
            elif self.role == "mbsem":
                required_roles += ["e-beam", "stage"]

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
            # Initialize settings observer to keep track of all relevant settings that should be
            # stored as metadata
            self.settings_obs = acqmng.SettingsObserver(comps_with_role)

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

        # Set to True to request debug info to be displayed
        self.debug = model.BooleanVA(False)
        self.level = model.IntVA(0)  # Highest message level not seen by the user so far

        # Current tab (+ all available tabs in choices as a dict tab -> name)
        # Fully set and managed later by the TabBarController.
        # Not very beautiful because Tab is not part of the model.
        # MicroscopyGUIData would be better in theory, but is less convenient
        # do directly access additional GUI information.
        self.tab = model.VAEnumerated(None, choices={None: ""})

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
        self.sample_centers : Dict[str, Tuple[float, float]] = {}  # sample name -> center position (x, y)

        # Controls the stage movement based on the imaging mode
        self.posture_manager = MicroscopePostureManager(microscope)

        # stage.MD_SAMPLE_CENTERS contains the date in almost the right format, but the
        # position is a dict instead of a tuple. => Convert it, while checking the data.
        # Ex: {"grid 1": {"x": 0.1, "y": -0.2}} -> {"grid 1": (0.1, -0.2)}
        sample_centers_raw = self.stage.getMetadata().get(model.MD_SAMPLE_CENTERS)

        # TODO: on the METEOR, the MD_SAMPLE_CENTERS is on the stage-bare, in
        # the stage-bare coordinates (SEM). To display them, we'd need to
        # convert them to the stage coordinates. The acq.move code only needs
        # the stage-bare coordinates (even if in FM), but we would need the
        # sample centers position during the FLM mode, in the stage coordinates...
        # and we don't have explicit functions for that. The "stage" component
        # knows how to convert a position from it dependencies stages, which
        # themselves are other wrappers to the "stage-bare", but we don't have
        # an explicit way to ask "if the stage-bare was at this position, what
        # would the stage position be?".

        if sample_centers_raw:
            try:
                self.sample_centers = {n: (p["x"], p["y"]) for n, p in sample_centers_raw.items()}
            except Exception as exp:
                raise ValueError(f"Failed to parse MD_SAMPLE_CENTERS, expected format "
                                 f"{{\"grid 1\": {{\"x\": 0.1, \"y\": -0.2}}}}: {exp}")

        # Radius of a sample, for display
        self.sample_radius = self.SAMPLE_RADIUS_TEM_GRID
        # Bounding-box of the "useful area" relative to the center of a grid
        self.sample_rel_bbox = self.SAMPLE_USABLE_BBOX_TEM_GRID


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
        md = self.stage.getMetadata()
        if model.MD_POS_ACTIVE_RANGE not in md:
            raise KeyError("Stage has no MD_POS_ACTIVE_RANGE metadata.")
        # POS_ACTIVE_RANGE contains the bounding-box of the positions with a sample
        carrier_range = md[model.MD_POS_ACTIVE_RANGE]
        minx, miny = carrier_range["x"][0], carrier_range["y"][0]  # bottom-left of carrier 1 in m
        # SAMPLE_CENTERS contains the center position of the scintillators from bottom-left
        # position with a sample
        if model.MD_SAMPLE_CENTERS not in md:
            raise KeyError("Stage has no MD_SAMPLE_CENTERS metadata.")
        centers = md[model.MD_SAMPLE_CENTERS]
        # SAMPLE_SIZES contains the sizes of the scintillators
        if model.MD_SAMPLE_SIZES not in md:
            raise KeyError("Stage has no MD_SAMPLE_SIZES metadata.")
        sample_sizes = md[model.MD_SAMPLE_SIZES]
        # Handle error cases for sample centers and sizes
        if centers.keys() != sample_sizes.keys():
            raise KeyError("MD_SAMPLE_CENTERS and MD_SAMPLE_SIZES metadata should have "
                "the same keys.")
        if not all(isinstance(i, float) for i in chain.from_iterable(centers.values())):
            raise TypeError("The sample centers must be of the type float.")
        if not all(isinstance(i, float) for i in chain.from_iterable(sample_sizes.values())):
            raise TypeError("The sample sizes must be of the type float.")
        layout = sample_positions_to_layout(centers)
        if None in chain.from_iterable(layout):
            raise TypeError(
                "Layout could not be determined from stage's MD_SAMPLE_CENTERS metadata,"
                "check if the sample centers values are correct.")
        for row_idx, row in enumerate(layout):
            for column_idx, name in enumerate(row):
                try:
                    scintillator_number = int(name.split()[-1])
                except Exception:
                    raise ValueError(
                        "Name of the sample must have a number in second place, e.g. 'SAMPLE 1'")
                layout[row_idx][column_idx] = scintillator_number
        sizes = {}
        for name, size in sample_sizes.items():
            try:
                scintillator_number = int(name.split()[-1])
            except Exception:
                raise ValueError(
                    "Name of the sample must have a number in second place, e.g. 'SAMPLE 1'")
            sizes[scintillator_number] = size
        # SAMPLE_BACKGROUND contains the minx, miny, maxx, maxy positions of rectangles for
        # background from bottom-left position with a sample
        if model.MD_SAMPLE_BACKGROUND not in md:
            raise KeyError("Stage has no MD_SAMPLE_BACKGROUND metadata.")
        background = md[model.MD_SAMPLE_BACKGROUND]

        # Initialize attributes related to the sample carrier
        #  * .scintillator_sizes (dict: int --> (float, float)): size of scintillators in m
        #  * .scintillator_positions (dict: int --> (float, float)): positions in stage coordinates
        #  * .scintillator_layout (list of list of int): 2D layout of scintillator grid
        #  * .background (list of ltrb tuples): coordinates for background overlay,
        #    rectangles can be displayed in world overlay as grey bars, e.g. for simulating a sample carrier
        self.scintillator_sizes = sizes
        self.scintillator_positions = {}
        for name, center in centers.items():
            try:
                scintillator_number = int(name.split()[-1])
            except Exception:
                raise ValueError(
                    "Name of the sample must have a number in second place, e.g. 'SAMPLE 1'")
            self.scintillator_positions[scintillator_number] = (minx + center[0], miny + center[1])
        self.scintillator_layout = layout
        self.background = []
        for rect in background:
            if len(rect) != 4:
                raise ValueError(
                    "The positions of rectangles for background must contain 4 elements,"
                    "i.e. minx, miny, maxx, maxy [m].")
            if not all(isinstance(i, float) for i in rect):
                raise TypeError(
                    "The positions of rectangles for background must be of the type float.")
            self.background.append((minx + rect[0], miny + rect[1], minx + rect[2], miny + rect[3]))

        # Overview streams
        self.overview_streams = model.VigilantAttribute({})  # dict: int --> stream or None

        # Scintillators containing sample (manual selection in chamber tab)
        self.active_scintillators = model.ListVA([])

        # Indicate state of ebeam button
        hw_states = {STATE_OFF, STATE_ON, STATE_DISABLED}
        self.emState = model.IntEnumerated(STATE_OFF, choices=hw_states)

        # Alignment status, reset to "not aligned" every time the emState or chamberState is changed
        self.is_aligned = model.BooleanVA(False)
        self.emState.subscribe(self._reset_is_aligned)
        self.chamberState.subscribe(self._reset_is_aligned)

    def _reset_is_aligned(self, _):
        self.is_aligned.value = False
