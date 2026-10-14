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
import json
import logging
import math
from typing import Dict, Optional

from odemis import model


# Upstream fibsemOS converter keys, also used to read older reference-image settings.
FIBSEMOS_COMPONENT_ROLES = {
    "Electron-Beam": "e-beam",
    "Ion-Beam": "ion-beam",
    "Electron-Focus": "ebeam-focus",
    "Ion-Focus": "ion-focus",
    "Electron-Detector": "se-detector",
    "Ion-Detector": "se-detector-ion",
    "Stage": "stage-bare",
}
BEAM_FIELDS = ("beamPreset", "accelVoltage", "probeCurrent", "beamCurrent", "horizontalFoV",
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
    """Find milling components by role, independently of their names."""
    return {component.role: component for component in model.getComponents()
            if component.role in COMPONENT_FIELDS}


def capture_system_state() -> dict:
    """Capture a snapshot indexed by Odemis component roles.

    Use this live snapshot to restore the imaging conditions present immediately
    before milling. Reference state is derived from acquisition metadata instead.
    """
    state = {}
    for key, component in get_milling_components().items():
        vas = model.getVAs(component)
        state[key] = {field: [copy.deepcopy(vas[field].value), vas[field].unit]
                      for field in COMPONENT_FIELDS[key] if field in vas}
    return state


def validate_system_state(state: dict) -> None:
    """Reject snapshots without the beam settings and raw stage position needed for milling."""
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


def system_state_from_settings(extra_settings: object,
                               components: Optional[Dict[str, model.HwComponent]] = None) -> dict:
    """Extract reference state from the acquisition's SettingsObserver record.

    Resolve component names using a supplied role-to-component mapping without
    reading hardware settings. Roles and old canonical keys need no microscope.
    """
    settings = json.loads(extra_settings) if isinstance(extra_settings, str) else extra_settings
    if not isinstance(settings, dict):
        raise ValueError("Missing recorded acquisition settings; acquire a new reference image.")
    names = {role: name for name, role in FIBSEMOS_COMPONENT_ROLES.items()}
    names.update({role: component.name for role, component in (components or {}).items()
                  if component.name in settings})
    state = {}
    for role, name in names.items():
        values = settings.get(role, settings.get(name))
        if isinstance(values, dict):
            state[role] = {field: copy.deepcopy(values[field])
                           for field in COMPONENT_FIELDS[role] if field in values}
    validate_system_state(state)
    return state


def migrate_system_state(extra_settings: object) -> Optional[dict]:
    """Recover older reference-image state in memory until the project is saved."""
    try:
        try:
            return system_state_from_settings(extra_settings)
        except ValueError:
            return system_state_from_settings(extra_settings, get_milling_components())
    except Exception:
        logging.warning("Could not migrate recorded milling system state; acquire a new reference image.",
                        exc_info=True)
    return None


def restore_system_state(state: dict) -> None:
    """Restore recorded imaging settings, leaving the sample stage in place.

    Activate beam presets before restoring scan settings. Attempt the remaining
    settings even if one fails, and report incomplete restoration to the caller.
    """
    failures = []
    for key, component in get_milling_components().items():
        if key == "stage-bare":
            continue
        vas = model.getVAs(component)
        for field in COMPONENT_FIELDS[key]:
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
