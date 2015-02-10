# -*- coding: utf-8 -*-
"""
Created on 5 Feb 2013

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

from __future__ import division

from collections import OrderedDict
import collections
import logging
from odemis import model


# TODO: presets shouldn't work on SettingEntries (GUI-only objects), but on
# Stream (and HwComponents).
def apply_preset(preset):
    """
    Apply the presets. It tries to ensure that they are set in the right order
     if the hardware needs it.
    preset (dict SettingEntries -> value): new value for each SettingEntry that
            should be modified.
    """
    # TODO: Once presets only affect the streams, we don't have dependency order
    # problem anymore?

    preset = dict(preset) # shallow copy (so we don't change the input)

    # There are mostly 2 (similar) dependencies:
    # * binning > resolution
    # * scale > resolution > translation
    # => do it in order: binning | scale > resolution > translation

    def apply_presets_named(name):
        for se, value in preset.items():
            if se.name == name:
                logging.debug("Updating preset %s -> %s", se.name, value)
                try:
                    se.vigilattr.value = value
                except Exception:
                    logging.exception("Failed to update preset %s", se.name)
                del preset[se]

    apply_presets_named("binning")
    apply_presets_named("scale")
    apply_presets_named("resolution")
    apply_presets_named("translation")

    for se, value in preset.items():
        logging.debug("Updating preset %s -> %s", se.name, value)
        try:
            se.vigilattr.value = value
        except Exception:
            logging.exception("Failed to update preset %s", se.name)

def _get_entry(entries, hw_comp, name):
    """
    find the entry for the given component with the name
    entries (list of SettingEntries): all the entries
    comp (model.Component)
    name (String)
    return (SettingEntry or None)
    """
    for e in entries:
        if e.hw_comp == hw_comp and e.name == name:
            return e
    else:
        return None


# Quality setting presets
def preset_hq(entries):
    """
    Preset for highest quality image
    entries (list of SettingEntries): each value as originally set
    returns (dict SettingEntries -> value): new value for each SettingEntry that should be modified
    """
    ret = {}

    # TODO: also handle AxisEntry ?
    for entry in entries:
        if (not hasattr(entry, "vigilattr") or entry.vigilattr is None
            or entry.vigilattr.readonly):
            # not a real setting, just info
            logging.debug("Skipping the value %s", entry.name)
            continue


        value = entry.vigilattr.value
        if entry.name == "resolution":
            # if resolution => get the best one
            try:
                value = entry.vigilattr.range[1] # max
            except (AttributeError, model.NotApplicableError):
                pass

        elif entry.name == "dwellTime":
            # SNR improves logarithmically with the dwell time => x4
            value = entry.vigilattr.value * 4

            # make sure it still fits
            if isinstance(entry.vigilattr.range, collections.Iterable):
                value = sorted(list(entry.vigilattr.range) + [value])[1] # clip

        elif entry.name == "scale": # for scanners only
            # Double the current resolution (in each dimensions)
            # (best == smallest == 1,1)
            value = tuple(v / 2 for v in entry.vigilattr.value)

            # make sure it still fits
            if isinstance(entry.vigilattr.range, collections.Iterable):
                value = tuple(max(v, m) for v, m in zip(value, entry.vigilattr.range[0]))

        elif entry.name == "binning":
            # if binning => smallest
            prev_val = entry.vigilattr.value
            try:
                value = entry.vigilattr.range[0]  # min
            except (AttributeError, model.NotApplicableError):
                try:
                    value = min(entry.vigilattr.choices)
                except (AttributeError, model.NotApplicableError):
                    pass
            # Compensate decrease in energy by longer exposure time
            et_entry = _get_entry(entries, entry.hw_comp, "exposureTime")
            if et_entry:
                et_value = ret.get(et_entry, et_entry.vigilattr.value)
                for prevb, newb in zip(prev_val, value):
                    et_value *= prevb / newb
                ret[et_entry] = et_value

        elif entry.name == "readoutRate":
            # the smallest, the less noise (and slower, but we don't care)
            try:
                value = entry.vigilattr.range[0]  # min
            except (AttributeError, model.NotApplicableError):
                try:
                    value = min(entry.vigilattr.choices)
                except (AttributeError, model.NotApplicableError):
                    pass
        # rest => as is

        logging.debug("Adapting value %s from %s to %s", entry.name, entry.vigilattr.value, value)
        ret[entry] = value

    return ret


def preset_as_is(entries):
    """
    Preset which don't change anything (exactly as live)
    entries (list of SettingEntries): each value as originally set
    returns (dict SettingEntries -> value): new value for each SettingEntry that
        should be modified
    """
    ret = {}
    for entry in entries:
        if (not hasattr(entry, "vigilattr") or entry.vigilattr is None
            or entry.vigilattr.readonly):
            # not a real setting, just info
            logging.debug("Skipping the value %s", entry.name)
            continue

        # everything as-is
        logging.debug("Copying value %s = %s", entry.name, entry.vigilattr.value)
        ret[entry] = entry.vigilattr.value

    return ret


def preset_no_change(entries):
    """
    Special preset which matches everything and doesn't change anything
    """
    return {}


# Name -> callable (list of SettingEntries -> dict (SettingEntries -> value))
presets = OrderedDict((
    (u"High quality", preset_hq),
    (u"Fast", preset_as_is),
    (u"Custom", preset_no_change)
))
