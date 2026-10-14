# -*- coding: utf-8 -*-
"""
Created on 3 April 2025

@author: Patrick Cleeve, Alexéy Ilyushkin

Copyright © 2025-2026 Patrick Cleeve, Alexéy Ilyushkin, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

import logging
import math
import os
import sys
import threading
import time
from concurrent import futures
from concurrent.futures import CancelledError
from copy import deepcopy
from inspect import signature
from pathlib import Path
from typing import List, Optional, Tuple, Union

from odemis import model
from odemis.acq.feature import (
    CryoFeature,
    DEFAULT_MILLING_ALIGNMENT_AREA,
    REFERENCE_IMAGE_FILENAME,
    constrain_milling_alignment_area,
)
from odemis.acq.milling.patterns import (
    CompositeRectanglePatternParameters,
    CorrelationPatternParameters,
    MicroexpansionPatternParameters,
    MillingPatternParameters,
    RectanglePatternParameters,
    TrenchPatternParameters,
)
from odemis.acq.milling.system_state import (
    FIBSEMOS_COMPONENT_ROLES,
    capture_system_state,
    restore_system_state,
    validate_system_state,
)
from odemis.acq.milling.tasks import (
    MillingSettings,
    MillingTaskSettings,
)
from odemis.util import executeAsyncTask

# Check if fibsemOS is available
try:
    from fibsem.microscopes.odemis_microscope import (
        OdemisThermoMicroscope,
        OdemisTescanMicroscope,
        from_odemis_image
    )
    from fibsem.milling import (
        FibsemMillingStage,
        MillingAlignment,
        estimate_total_milling_time,
    )
    from fibsem.milling.patterning.patterns2 import (
        BasePattern,
        MicroExpansionPattern,
        RectanglePattern,
        TrenchPattern,
    )
    from fibsem.structures import (
        FibsemMillingSettings,
        Point,
        FibsemImage,
        FibsemImageMetadata,
        FibsemRectangle,
        FibsemRectangleSettings,
        BeamType,
        ImageSettings,
        MicroscopeState,
    )
    from fibsem.utils import load_microscope_configuration
    FIBSEMOS_INSTALLED = True
except ImportError as e:
    logging.warning(f"fibsemOS is not installed or not available: {e}")
    FIBSEMOS_INSTALLED = False
    # Hack to let _RectanglePatternGroup load without fibsemOS.
    RectanglePattern = object

_persistent_millmng: Optional["FibsemOSMillingTaskManager"] = None
_MILLING_FOV_MARGIN = 0.01


def _convert_reference_image(image: model.DataArray, system_state: dict,
                             path: Optional[str] = None) -> 'FibsemImage':
    """Convert a reference image without changing its Odemis metadata.

    Share the pixels and copy metadata before adapting the role-keyed state
    to the converter's expected format.
    """
    image = model.DataArray(image, metadata=deepcopy(image.metadata))
    conversion_state = deepcopy(system_state)
    if "system_state" in signature(from_odemis_image).parameters:
        converted = from_odemis_image(image, system_state=conversion_state)
    else:
        # Upstream converters still read the snapshot and beam name from image metadata.
        for role in ("e-beam", "ion-beam"):
            # The release converter wraps tuples in another list.
            conversion_state[role]["resolution"][0] = list(conversion_state[role]["resolution"][0])
            for field in ("shift", "stigmator"):
                if conversion_state[role].get(field, [None])[0] is None:
                    conversion_state[role][field] = [[0.0, 0.0]]
        image.metadata[model.MD_EXTRA_SETTINGS] = {name: conversion_state[role]
                                                 for name, role in FIBSEMOS_COMPONENT_ROLES.items()
                                                 if role in conversion_state}
        beam_names = {model.MD_AT_EM: "SEM", model.MD_AT_FIB: "FIB"}
        beam_name = beam_names.get(image.metadata.get(model.MD_ACQ_TYPE))
        if beam_name is not None:
            image.metadata[model.MD_DESCRIPTION] = beam_name
        converted = from_odemis_image(image)
    microscope_state = converted.metadata.microscope_state
    if microscope_state is not None:
        microscope_state.timestamp = image.metadata.get(model.MD_ACQ_DATE)
        for role, beam in (("e-beam", microscope_state.electron_beam),
                           ("ion-beam", microscope_state.ion_beam)):
            if beam is None:
                continue
            recorded = system_state[role]
            for field, attribute in (("shift", "shift"), ("stigmator", "stigmation")):
                if recorded.get(field, [None])[0] is None:
                    setattr(beam, attribute, None)
            # The release converter can report measured Tescan FIB current as a setpoint.
            beam.beam_current = recorded.get("beamCurrent", recorded.get("probeCurrent", [None]))[0]
    if path is not None:
        converted.metadata.image_settings.path = str(Path(path).parent)
        converted.metadata.image_settings.filename = Path(path).name
    return converted


class _RectanglePatternGroup(RectanglePattern):
    """Expose several fibsemOS rectangles as one executable pattern.

    Instantiation requires fibsemOS; the object fallback only allows module loading.
    """

    def __init__(self, rectangles: List['RectanglePattern']) -> None:
        """Initialize the group from ordinary rectangle patterns.

        :param rectangles: Rectangle patterns drawn in one milling stage.
        """
        if not rectangles:
            raise ValueError("A rectangle pattern group cannot be empty.")
        first = rectangles[0]
        super().__init__(
            point=first.point,
            width=first.width,
            height=first.height,
            depth=first.depth,
            rotation=first.rotation,
            time=first.time,
            passes=first.passes,
            scan_direction=first.scan_direction,
            cross_section=first.cross_section,
        )
        self.rectangles = rectangles

    def define(self) -> List['FibsemRectangleSettings']:
        """Return all rectangle settings for one fibsemOS milling run."""
        self.shapes = [shape for rectangle in self.rectangles
                       for shape in rectangle.define()]
        return self.shapes


def _get_reference_image(feature: CryoFeature) -> model.DataArray:
    """Get the in-memory reference image for a feature or raise."""

    if feature.reference_image is None:
        logging.error(
            "Missing reference image for feature '%s' (path=%s). "
            "This feature was likely loaded from disk without hydrating reference_image.",
            feature.name.value,
            getattr(feature, "path", None),
        )
        raise ValueError("Missing feature.reference_image.")
    return feature.reference_image


def _crop_to_reduced_area(ref_img: 'FibsemImage', rect: 'FibsemRectangle') -> 'FibsemImage':
    """Crop a fibsemOS image to the provided reduced-area rectangle.

    :param ref_img: The image to crop.
    :param rect: Rectangle with fractional coordinates (left, top, width, height).
    :return: The same image instance with cropped data.
    """

    h, w = ref_img.data.shape[-2], ref_img.data.shape[-1]

    # fractional to pixel indices
    x0 = int(rect.left * w)
    y0 = int(rect.top * h)
    x1 = int((rect.left + rect.width) * w)
    y1 = int((rect.top + rect.height) * h)

    # clamp to valid range just in case of rounding
    x0 = max(0, min(w, x0))
    x1 = max(0, min(w, x1))
    y0 = max(0, min(h, y0))
    y1 = max(0, min(h, y1))

    # crop along the last two axes, DataArray slicing behaves like numpy
    ref_img.data = ref_img.data[..., y0:y1, x0:x1]
    return ref_img


def create_fibsemos_tfs_microscope() -> 'OdemisThermoMicroscope':
    """Create and return a fibsemOS Thermo microscope instance."""

    # TODO: extract the rest of the required metadata

    # stage metadata
    stage_bare = model.getComponent(role="stage-bare")
    stage_md = stage_bare.getMetadata()
    pre_tilt = stage_md[model.MD_CALIB].get(model.MD_SAMPLE_PRE_TILT, math.radians(35))
    rotation_reference = stage_md[model.MD_FAV_SEM_POS_ACTIVE]["rz"]

    # loads the default config
    config = load_microscope_configuration()
    config.system.stage.shuttle_pre_tilt = math.degrees(pre_tilt)
    # Used by fibsemOS for moving the stage flat to the electron beam
    config.system.stage.rotation_reference = math.degrees(rotation_reference)
    # Used by fibsemOS for moving the stage flat to the ion beam
    config.system.stage.rotation_180 = math.degrees(rotation_reference + math.pi)
    microscope = OdemisThermoMicroscope(config.system)

    return microscope

def create_fibsemos_tescan_microscope() -> 'OdemisTescanMicroscope':
    """Create, connect, and return a fibsemOS Tescan microscope instance."""

    # TODO: Extract the rest of the required metadata

    # stage metadata
    stage_bare = model.getComponent(role="stage-bare")
    stage_md = stage_bare.getMetadata()
    pre_tilt = stage_md[model.MD_CALIB].get(model.MD_SAMPLE_PRE_TILT, math.radians(35))
    rotation_reference = stage_md[model.MD_FAV_SEM_POS_ACTIVE]["rz"]

    # loads the default config
    config = load_microscope_configuration()
    config.system.stage.shuttle_pre_tilt = math.degrees(pre_tilt)
    # Used by fibsemOS for moving the stage flat to the electron beam
    config.system.stage.rotation_reference = math.degrees(rotation_reference)
    # Used by fibsemOS for moving the stage flat to the ion beam
    config.system.stage.rotation_180 = math.degrees(rotation_reference + math.pi)

    # Get the Tescan SEM component to extract host and port info
    fibsem = model.getComponent(role="fibsem")
    ip_address: str = fibsem.host
    port: int = fibsem.port
    # Pass the IP address to the fibsemOS config as well
    config.system.info.ip_address = ip_address
    microscope = OdemisTescanMicroscope(config.system)

    microscope.connect_to_microscope(ip_address, port)

    return microscope

def create_fibsemos_microscope() -> Union['OdemisThermoMicroscope', 'OdemisTescanMicroscope']:
    """Create and return a fibsemOS microscope instance matching the detected stage version."""
    stage_bare = model.getComponent(role="stage-bare")
    stage_md = stage_bare.getMetadata()
    md_calib = stage_md.get(model.MD_CALIB, {})
    stage_version = md_calib.get("version", None)

    if stage_version == "tfs_3":
        return create_fibsemos_tfs_microscope()
    elif stage_version == "tescan_1":
        return create_fibsemos_tescan_microscope()
    else:
        raise ValueError(f"Stage version {stage_version} is not supported")


def convert_pattern_to_fibsemos(p: MillingPatternParameters) -> 'BasePattern':
    """Convert from an Odemis pattern to a fibsemOS pattern"""
    if isinstance(p, RectanglePatternParameters):
        return _convert_rectangle_pattern(p)

    elif isinstance(p, TrenchPatternParameters):
        return _convert_trench_pattern(p)

    elif isinstance(p, MicroexpansionPatternParameters):
        return _convert_microexpansion_pattern(p)
    else:
        raise NotImplementedError(f"Conversion not implemented for pattern type: {type(p)}")


def _apply_spot_size_correction(dimension: float, correction: float, dimension_name: str) -> float:
    """Reduce a scan dimension by the measured opening excess."""
    corrected_dimension = dimension - correction
    if corrected_dimension <= 0:
        raise ValueError(
            f"Spot size correction {correction} m makes the {dimension_name} non-positive "
            f"({corrected_dimension} m)."
        )
    return corrected_dimension


def _convert_rectangle_pattern(p: RectanglePatternParameters) -> 'RectanglePattern':
    """Convert an Odemis rectangle pattern to a fibsemOS RectanglePattern."""
    correction = p.spot_size_correction.value
    return RectanglePattern(
        width=_apply_spot_size_correction(p.width.value, correction, "rectangle width"),
        height=_apply_spot_size_correction(p.height.value, correction, "rectangle height"),
        depth=p.depth.value,
        rotation=math.degrees(p.rotation.value),
        scan_direction=p.scan_direction.value,
        point=Point(x=p.center.value[0], y=p.center.value[1])
    )

def _convert_trench_pattern(p: TrenchPatternParameters) -> 'TrenchPattern':
    """Convert an Odemis trench pattern to a fibsemOS TrenchPattern."""
    correction = p.spot_size_correction.value
    return TrenchPattern(
        width=_apply_spot_size_correction(p.width.value, correction, "trench width"),
        upper_trench_height=_apply_spot_size_correction(p.height.value, correction, "upper trench height"),
        lower_trench_height=_apply_spot_size_correction(p.height.value, correction, "lower trench height"),
        # fibsemOS derives trench centers from height and spacing. Increasing
        # spacing keeps the smaller sent rectangles centered on the displayed ones.
        spacing=p.spacing.value + correction,
        depth=p.depth.value,
        point=Point(x=p.center.value[0], y=p.center.value[1])
    )

def _convert_microexpansion_pattern(p: MicroexpansionPatternParameters) -> 'MicroExpansionPattern':
    """Convert an Odemis microexpansion pattern to a fibsemOS MicroExpansionPattern."""
    correction = p.spot_size_correction.value
    return MicroExpansionPattern(
        width=_apply_spot_size_correction(p.width.value, correction, "microexpansion width"),
        height=_apply_spot_size_correction(p.height.value, correction, "microexpansion height"),
        depth=p.depth.value,
        distance=p.spacing.value,
        point=Point(x=p.center.value[0], y=p.center.value[1])
    )

def _format_preset(voltage: float, current: float) -> str:
    """
    Format voltage (V) and current (A) into a preset name string like '30 keV; 150 pA'.
    Voltage is shown in eV below 1 keV, keV otherwise. Current is shown in pA, nA, or uA.

    This format is a convention for Tescan preset names. Presets in the Tescan
    software must be named following this convention so that fibsemOS can match
    them correctly.

    :param voltage: beam voltage in volts (must be a positive finite number).
    :param current: beam current in amperes (must be a positive finite number).
    :raises ValueError: if voltage or current are not positive finite numbers.
    """
    if not math.isfinite(voltage):
        raise ValueError(f"Voltage must be a finite number, got {voltage!r}")
    if voltage <= 0:
        raise ValueError(f"Voltage must be positive, got {voltage!r}")
    if not math.isfinite(current):
        raise ValueError(f"Current must be a finite number, got {current!r}")
    if current <= 0:
        raise ValueError(f"Current must be positive, got {current!r}")

    # Voltage: choose eV or keV based on order of magnitude
    if voltage < 1000:
        # eV range: [0, 1 keV)
        voltage_str = f"{voltage:g} eV"
    else:
        # keV range: [1 keV, ...)
        voltage_str = f"{voltage / 1000:g} keV"

    # Current: pA, nA, or uA based on order of magnitude
    if current < 1e-9:
        # pA range: [0, 1 nA)
        current_val = current * 1e12
        unit = "pA"
    elif current < 1e-6:
        # nA range: [1 nA, 1 uA)
        current_val = current * 1e9
        unit = "nA"
    else:
        # uA range: [1 uA, ...)
        current_val = current * 1e6
        unit = "uA"
    current_str = f"{current_val:g} {unit}"
    return f"{voltage_str}; {current_str}"


def convert_milling_settings(s: MillingSettings) -> 'FibsemMillingSettings':
    """Convert Odemis milling settings to fibsemOS milling settings.

    Both milling_current/milling_voltage and preset are populated because
    fibsemOS uses them selectively depending on the microscope backend:
    milling_current and milling_voltage are used by the TFS backend, while
    preset (a human-readable string such as "30 keV; 150 pA") is used by the
    Tescan backend.
    """
    return FibsemMillingSettings(
        milling_current=s.current.value,
        milling_voltage=s.voltage.value,
        patterning_mode=s.mode.value,
        hfw=s.field_of_view.value,
        preset=_format_preset(s.voltage.value, s.current.value)
    )


# task converter
def convert_task_to_milling_stage(
        task: MillingTaskSettings,
        alignment_area: Tuple[float, float, float, float] = DEFAULT_MILLING_ALIGNMENT_AREA) -> 'FibsemMillingStage':
    """Convert a single Odemis milling task to a fibsemOS milling stage.

    :param task: Task supplying the name, milling settings, and first pattern
        to convert.
    :param alignment_area: Normalized (left, top, right, bottom) coordinates
        in the saved FIB reference image, with values between 0 and 1.
        Defaults to DEFAULT_MILLING_ALIGNMENT_AREA. Width and height are
        derived from these coordinates for the fibsemOS rectangle.
    :return: Milling stage for the task's first pattern, with its alignment
        enabled according to the task's milling settings.
    """
    s = convert_milling_settings(task.milling)
    p = convert_pattern_to_fibsemos(task.patterns[0])
    left, top, right, bottom = alignment_area
    a = MillingAlignment(
        enabled=task.milling.align.value,
        rect=FibsemRectangle(
            left=left,
            top=top,
            width=right - left,
            height=bottom - top,
        ),
    )

    milling_stage = FibsemMillingStage(
        name=task.name,
        milling=s,
        pattern=p,
        alignment=a,
    )
    return milling_stage

def _convert_composite_pattern_to_milling_stage(
        task: MillingTaskSettings, pattern: CompositeRectanglePatternParameters, name: str,
        alignment_area: Tuple[float, float, float, float]) -> 'FibsemMillingStage':
    """Convert a composite rectangle pattern into one fibsemOS milling stage.

    :param task: Task supplying the shared milling and alignment settings.
    :param pattern: Composite pattern whose generated rectangles belong to the stage.
    :param name: Milling stage name.
    :param alignment_area: Normalized (left, top, right, bottom) reference-image coordinates.
    :return: One stage that draws and mills every generated rectangle together.
    """
    rectangles = [_convert_rectangle_pattern(rectangle)
                  for rectangle in pattern.generate()]
    milling = convert_milling_settings(task.milling)
    if isinstance(pattern, CorrelationPatternParameters):
        center_x, center_y = pattern.center.value
        required_fov = 2 * max(
            abs(center_x) + pattern.width.value / 2,
            abs(center_y) + pattern.height.value / 2,
        )
        minimum_fov = required_fov * (1 + _MILLING_FOV_MARGIN)
        maximum_fov = task.milling.field_of_view.range[1]
        if minimum_fov > maximum_fov:
            raise ValueError(
                f"Correlation pattern requires a {minimum_fov:g} m milling field including its safety margin, "
                f"which does not fit inside the maximum {maximum_fov:g} m field of view."
            )
        if minimum_fov >= milling.hfw:
            milling.hfw = maximum_fov
    left, top, right, bottom = alignment_area
    alignment = MillingAlignment(
        enabled=task.milling.align.value,
        rect=FibsemRectangle(left=left, top=top, width=right - left, height=bottom - top),
    )
    return FibsemMillingStage(
        name=name,
        milling=milling,
        pattern=_RectanglePatternGroup(rectangles),
        patterns=rectangles,
        alignment=alignment,
    )

def convert_milling_tasks_to_milling_stages(
        milling_tasks: List[MillingTaskSettings],
        alignment_area: Tuple[float, float, float, float] = DEFAULT_MILLING_ALIGNMENT_AREA) -> List['FibsemMillingStage']:
    """Convert tasks to fibsemOS milling stages.

    Each pattern in a selected task produces one stage. All rectangles
    generated by a composite pattern share that stage and milling run.

    :param milling_tasks: Tasks to convert in order. Tasks whose selected
        attribute is False are skipped.
    :param alignment_area: Normalized (left, top, right, bottom) coordinates
        in the saved FIB reference image, with values between 0 and 1.
        Defaults to DEFAULT_MILLING_ALIGNMENT_AREA and applies to every stage.
    :return: Stages in task and pattern order, or an empty list when no
        selected tasks contain patterns.
    """
    milling_stages = []

    for task in milling_tasks:
        if not task.selected:
            continue
        for pattern in task.patterns:
            name = task.name if len(task.patterns) == 1 else f"{task.name}: {pattern.name.value}"
            if isinstance(pattern, CompositeRectanglePatternParameters):
                milling_stages.append(
                    _convert_composite_pattern_to_milling_stage(
                        task, pattern, name, alignment_area))
            else:
                stage_task = MillingTaskSettings(
                    milling=task.milling, patterns=[pattern], name=name)
                milling_stages.append(convert_task_to_milling_stage(
                    stage_task, alignment_area=alignment_area))

    return milling_stages


class FibsemOSMillingTaskManager:
    """Manage running milling tasks via fibsemOS using a persistent microscope connection."""

    def __init__(self):
        """Initialize the manager and establish the fibsemOS microscope connection."""
        # create microscope connection
        self.microscope = create_fibsemos_microscope()
        self._lock = threading.Lock()
        self._active = False
        self._cancel_requested = False

        # per-run state (set in async_run)
        self.milling_stages: List["FibsemMillingStage"] = []
        self._future: Optional[futures.Future] = None

    def cancel(self, future: futures.Future) -> bool:
        """Request cancellation of the current milling run."""
        logging.debug("Canceling milling procedure...")
        with self._lock:
            if not self._active:
                return False
            if self._cancel_requested:
                return True
            self._cancel_requested = True
        # Do not hold the lock during potentially blocking calls
        subf = getattr(future, "running_subf", None)
        if subf is not None:
            subf.cancel()
        try:
            self.microscope.stop_milling()
        finally:
            logging.debug("Milling procedure cancelled.")
        return True

    def estimate_milling_time(self) -> float:
        """Estimate the total milling time for the currently configured stages (seconds)."""
        return estimate_total_milling_time(self.milling_stages)

    def _normalize_system_state(self, system_state: dict) -> dict:
        """Validate a recorded snapshot for Tescan or Thermo image conversion.

        The snapshot is indexed by component role. Supply optional fields
        required by upstream converters while preserving recorded values.
        Thermo probeCurrent is a setpoint. Tescan FIB probeCurrent is a
        measurement, so only the SEM value can be copied into beamCurrent
        for Tescan.

        :param system_state: The dedicated snapshot stored with the feature.
        :return: An independent dictionary with optional conversion fields populated.
        :raises ValueError: Required metadata is missing or invalid.
        """
        validate_system_state(system_state)
        state = deepcopy(system_state)

        is_tescan = isinstance(self.microscope, OdemisTescanMicroscope)

        for beam_key in ("e-beam", "ion-beam"):
            beam_md = state.get(beam_key)
            if "beamCurrent" not in beam_md:
                # Only Tescan FIB probeCurrent is measured rather than configured.
                if "probeCurrent" in beam_md and (not is_tescan or beam_key == "e-beam"):
                    beam_md["beamCurrent"] = beam_md["probeCurrent"].copy()
                elif is_tescan:
                    beam_md["beamCurrent"] = [None]
                else:
                    raise ValueError(f"Milling system state is missing {beam_key}.probeCurrent.")

        for focus_key in ("ebeam-focus", "ion-focus"):
            focus_md = state.setdefault(focus_key, {})
            if not focus_md.get("position"):
                focus_md["position"] = [{"z": None}]
            else:
                focus_md["position"][0].setdefault("z", None)

        for detector_key in ("se-detector", "se-detector-ion"):
            detector_md = state.setdefault(detector_key, {})
            for field in ("type", "mode", "brightness", "contrast"):
                detector_md.setdefault(field, [None])

        return state

    def _finish_milling(self, system_state: dict) -> None:
        """Clear patterns without selecting a default Tescan imaging preset."""
        if isinstance(self.microscope, OdemisTescanMicroscope):
            self.microscope.connection.DrawBeam.UnloadLayer()
            # Reset fibsemOS's cached layer and pre-milling preset.
            self.microscope._milling_layer_loaded = False
            self.microscope._preset_before_milling = None
        else:
            beam = system_state["ion-beam"]
            current = beam.get("beamCurrent", beam.get("probeCurrent", [None]))[0]
            self.microscope.finish_milling(imaging_current=current,
                                          imaging_voltage=beam["accelVoltage"][0])

    def _run_stage(self, stage: 'FibsemMillingStage', system_state: dict) -> None:
        """Run the shared strategy API and restore conditions without hiding errors."""
        channel = stage.milling.milling_channel
        initial_shift = self.microscope.get("shift", beam_type=channel)
        try:
            # fibsemOS 0.5.0a0 no longer exports mill_stages. Both versions expose stage.strategy.run().
            stage.strategy.run(microscope=self.microscope, stage=stage, asynch=False)
        finally:
            failed = sys.exc_info()[0] is not None
            cleanup_error = None
            cleanup = [self.microscope.stop_milling] if failed else []
            cleanup.append(lambda: self._finish_milling(system_state))
            if initial_shift is not None:
                cleanup.append(lambda: self.microscope.set("shift", value=initial_shift, beam_type=channel))
            for action in cleanup:
                try:
                    action()
                except Exception as exc:
                    logging.exception("Failed to clean up milling stage %s.", stage.name)
                    if cleanup_error is None:
                        cleanup_error = exc
            if cleanup_error is not None and not failed:
                raise cleanup_error

    def _run(self):
        """Internal worker that performs the milling stages sequentially."""
        future = self._future
        if future is None:
            # Should never happen if async_run configured correctly, but don't use assert.
            with self._lock:
                self._active = False
            raise RuntimeError("Internal error: milling run started without an associated future.")

        restoration_state = None
        milling_started = False
        try:
            recorded_state = deepcopy(self.feature.system_state)
            system_state = self._normalize_system_state(recorded_state)
            if isinstance(self.microscope, OdemisTescanMicroscope):
                preset = system_state["ion-beam"].get("beamPreset")
                if not preset or not preset[0]:
                    raise ValueError("Recorded Tescan imaging preset is missing; select an imaging preset "
                                     "and acquire a new reference image.")
            for stage in self.milling_stages:
                with self._lock:
                    if self._cancel_requested:
                        raise CancelledError()

                logging.info(f"Running milling stage: {stage.name}")
                reference_image = _get_reference_image(self.feature)
                ref_img = _convert_reference_image(reference_image, system_state)
                ref_img.metadata.image_settings.path = self.path
                ref_img.metadata.image_settings.reduced_area = stage.alignment.rect

                ref_img = _crop_to_reduced_area(ref_img, stage.alignment.rect)
                # fibsemOS 0.4.1a0 introduced stage.reference_image for strategy alignment.
                stage.reference_image = ref_img
                stage.imaging.path = self.path

                if restoration_state is None:
                    restoration_state = capture_system_state()
                milling_started = True
                self._run_stage(stage, restoration_state)

        finally:
            failed = sys.exc_info()[0] is not None
            try:
                if milling_started:
                    try:
                        restore_system_state(restoration_state)
                    except Exception:
                        if not failed:
                            raise
                        logging.exception("Imaging restoration failed after interrupted milling.")
            finally:
                with self._lock:
                    self._active = False
                    self._cancel_requested = False

    def async_run(self,
                  *,
                  future: futures.Future,
                  tasks: List[MillingTaskSettings],
                  feature: CryoFeature,
                  path: Optional[str] = None) -> futures.Future:
        """Prepare and start a milling run asynchronously (one run at a time)."""
        if path is None:
            path = os.getcwd()

        reference_image = _get_reference_image(feature)
        alignment_area = constrain_milling_alignment_area(feature.milling_alignment_area.value, reference_image.shape)
        feature.milling_alignment_area.value = alignment_area
        milling_stages = convert_milling_tasks_to_milling_stages(tasks, alignment_area=alignment_area)
        estimated_dur = estimate_total_milling_time(milling_stages) + 30

        with self._lock:
            if self._active:
                raise RuntimeError("A fibsemOS milling session is already running.")
            self._active = True
            self._cancel_requested = False
            self.microscope._last_imaging_settings.path = path
            self.milling_stages = milling_stages
            self.path = path
            self.feature = feature
            self._future = future
            self._future.running_subf = model.InstantaneousFuture()
            self._future.task_canceller = self.cancel
            # +30 s as estimate time only includes milling time, not current switching time, etc
            self._future.set_progress(remaining_time=estimated_dur)

            try:
                executeAsyncTask(self._future, self._run)
            except Exception:
                self._active = False
                raise
        return self._future


def run_milling_tasks_fibsemos(tasks: List[MillingTaskSettings], feature: CryoFeature, path: Optional[str] = None) -> futures.Future:
    """Run milling tasks asynchronously via fibsemOS.

    :param tasks: Milling tasks to execute (converted to fibsemOS stages and run in order).
    :param feature: Feature providing the in-memory reference image used for alignment.
        Must have ``feature.reference_image`` set.
    :param path: Directory where acquired reference images will be stored.
        Defaults to the current working directory.
    :return: A progressive future for progress reporting and cancellation.
    """
    global _persistent_millmng

    if _persistent_millmng is None:
        _persistent_millmng = FibsemOSMillingTaskManager()

    future = model.ProgressiveFuture()
    return _persistent_millmng.async_run(future=future, tasks=tasks, feature=feature, path=path)
