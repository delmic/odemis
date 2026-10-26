# -*- coding: utf-8 -*-
"""
Created on Oct 2026

@author: Alexéy Ilyushkin

Copyright © 2026 Alexéy Ilyushkin, Delmic

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

import copy
import logging
import math
from typing import Dict

from odemis import model


# Component names expected by the legacy fibsemOS converter.
FIBSEMOS_COMPONENT_ROLES = {
    "Electron-Beam": "e-beam",
    "Ion-Beam": "ion-beam",
    "Electron-Focus": "ebeam-focus",
    "Ion-Focus": "ion-focus",
    "Electron-Detector": "se-detector",
    "Ion-Detector": "se-detector-ion",
    "Stage": "stage-bare",
}
BEAM_FIELDS = ("beamPreset", "accelVoltage", "probeCurrent", "horizontalFoV",
               "scale", "resolution", "translation", "dwellTime", "rotation", "shift", "stigmator")
COMPONENT_FIELDS = {
    "e-beam": BEAM_FIELDS,
    "ion-beam": BEAM_FIELDS,
    "ebeam-focus": ("position",),
    "ion-focus": ("position",),
    "se-detector": ("type", "mode", "brightness", "contrast"),
    "se-detector-ion": ("type", "mode", "brightness", "contrast"),
    "stage-bare": ("position",),
}


def get_milling_components() -> Dict[str, model.HwComponent]:
    """Find milling components by role, independently of their names.

    :returns: Available milling components indexed by their Odemis roles.
    """
    return {component.role: component for component in model.getComponents()
            if component.role in COMPONENT_FIELDS}


def capture_system_state() -> dict:
    """Capture a snapshot indexed by Odemis component roles.

    Capture after acquiring a reference image to record the settings needed for
    alignment against that image. Capture again immediately before milling to
    restore the user's current imaging settings afterward. The user may have
    changed settings since acquiring the reference, so these snapshots can differ.

    :returns: Independent component settings indexed by role, with value and unit entries.
    """
    state = {}
    for key, component in get_milling_components().items():
        vas = model.getVAs(component)
        state[key] = {field: [copy.deepcopy(vas[field].value), vas[field].unit]
                      for field in COMPONENT_FIELDS[key] if field in vas}
    return state


def validate_system_state(state: dict) -> None:
    """Reject snapshots without the beam settings and raw stage position needed for milling.

    :param state: Recorded acquisition settings indexed by component role.
    :raises ValueError: Required beam settings or raw stage coordinates are missing or invalid.
    """
    if not isinstance(state, dict):
        raise ValueError("Missing or invalid milling system state; acquire a new reference image.")
    for key in ("e-beam", "ion-beam"):
        beam = state.get(key)
        if not isinstance(beam, dict):
            raise ValueError(f"Milling system state is missing {key}.")
        for field in ("dwellTime", "horizontalFoV", "accelVoltage", "resolution", "rotation"):
            entry = beam.get(field)
            if not entry or entry[0] is None:
                raise ValueError(f"Milling system state is missing {key}.{field}.")
            if field in ("dwellTime", "horizontalFoV") and (not math.isfinite(entry[0]) or entry[0] <= 0):
                raise ValueError(f"Milling system state has invalid {key}.{field}: {entry[0]!r}.")
    stage = state.get("stage-bare")
    position = stage.get("position") if isinstance(stage, dict) else None
    if not position or not isinstance(position[0], dict):
        raise ValueError("Milling system state is missing stage-bare.position.")
    for axis in ("x", "y", "z", "rx", "rz"):
        if position[0].get(axis) is None:
            raise ValueError(f"Milling system state is missing stage-bare.position.{axis}.")


def restore_system_state(state: dict) -> None:
    """Restore recorded imaging settings, leaving the sample stage in place.

    Activate all beam presets before restoring scan settings or focus, regardless
    of component discovery order. Preset setters wait for the hardware to settle.
    Attempt the remaining settings even if one fails, and report incomplete
    restoration to the caller.

    :param state: Imaging settings captured immediately before milling, indexed by role.
    :raises RuntimeError: One or more recorded settings could not be restored.
    """
    failures = []
    settings = []
    for key, component in get_milling_components().items():
        if key == "stage-bare":
            continue
        vas = model.getVAs(component)
        settings.extend((key, component, vas, field) for field in COMPONENT_FIELDS[key])
    # Focus can be discovered before the beam; preset activation must finish first.
    settings.sort(key=lambda setting: setting[3] != "beamPreset")
    for key, component, vas, field in settings:
        entry = state.get(key, {}).get(field)
        if not entry or entry[0] is None or field not in vas:
            continue
        if field != "position" and vas[field].readonly:
            continue
        try:
            value = copy.deepcopy(entry[0])
            if field == "position":
                value = {axis: position for axis, position in value.items() if position is not None}
                if not value:
                    continue
                component.moveAbs(value).result()
            else:
                vas[field].value = value
        except Exception:
            failures.append(f"{key}.{field}")
            logging.exception("Failed to restore %s.%s.", key, field)
    if failures:
        raise RuntimeError(f"Could not restore recorded imaging settings: {', '.join(failures)}")
