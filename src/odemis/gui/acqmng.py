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

from collections import OrderedDict
from collections.abc import Iterable
import logging

EXTRA_STREAM_VAS = ("dwellTime", "pixelSize", "repetition")


def get_global_settings_entries(settings_cont):
    """
    Find all the entries needed for the presets in a settings controller (aka
      global settings)
    settings_cont (SettingsBarController)
    return (list of SettingsEntry): all the SettingsEntry on the settings controller
    """
    entries = []
    for e in settings_cont.entries:
        if hasattr(e, "vigilattr") and e.vigilattr is not None and not e.vigilattr.readonly:
            entries.append(e)

    return entries


def get_local_settings_entries(stream_cont):
    """
    Find all the entries needed for the presets in a stream controller (aka
      local settings)
    stream_cont (StreamController)
    return (list of SettingsEntry): all the SettingsEntry on the stream
     controller that correspond to a local setting (ie, a duplicated VA of
     the hardware VA present on the stream)
    """
    local_entries = []
    local_vas = set(stream_cont.stream.emt_vas.values()) | set(stream_cont.stream.det_vas.values())
    for vaname in EXTRA_STREAM_VAS:
        if hasattr(stream_cont.stream, vaname):
            local_vas.add(getattr(stream_cont.stream, vaname))

    for e in stream_cont.entries:
        if (hasattr(e, "vigilattr") and e.vigilattr in local_vas
            and e.vigilattr is not None and not e.vigilattr.readonly
           ):
            logging.debug("Added local setting %s", e.name)
            local_entries.append(e)

    return local_entries


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
        for se, value in list(preset.items()):  # need to create separate list because elements can be deleted
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


def _get_entries(entries, hw_comp, name):
    """
    Find the entries for the given component with the name
    entries (list of SettingEntries): all the entries
    comp (model.Component)
    name (String)
    return (list of SettingEntry): all the entries which match
    """
    matchs = []
    for e in entries:
        if e.hw_comp == hw_comp and e.name == name:
            matchs.append(e)

    return matchs


# Quality setting presets
def preset_hq(entries):
    """
    Preset for highest quality image
    entries (list of SettingEntries): each value as originally set
    returns (dict SettingEntries -> value): new value for each SettingEntry that should be modified
    """
    logging.debug("Computing HQ preset")
    ret = {}
    # TODO: also handle AxisEntry ?
    for entry in entries:
        # TODO: not needed anymore?
        if (not hasattr(entry, "vigilattr") or entry.vigilattr is None or
            entry.vigilattr.readonly or entry.value_ctrl is None
           ):
            # not a real setting, just info
            logging.debug("Skipping the value %s", entry.name)
            continue

        value = entry.vigilattr.value
        if entry.name == "resolution":
            # if resolution => get the best one
            try:
                value = entry.vigilattr.range[1] # max
            except AttributeError:
                pass

        elif entry.name == "dwellTime":
            if entry.hw_comp and entry.hw_comp.role == "e-beam":
                # For detectors of type MD_DT_NORMAL, the signal level doesn't
                # depend on time, so we can increase it. That's typical of e-beam
                # (and not for laser-mirror of confocal)
                # TODO: detect that the streams using with the given scanner
                # have detectors of type MD_DT_NORMAL or MD_DT_INTEGRATING.

                # SNR improves logarithmically with the dwell time => x4
                value = entry.vigilattr.value * 4

                # make sure it still fits
                if isinstance(entry.vigilattr.range, Iterable):
                    value = sorted(list(entry.vigilattr.range) + [value])[1]  # clip

        elif entry.name == "scale": # for scanners only
            # Double the current resolution (in each dimensions)
            # (best == smallest == 1,1)
            # Note: some hardware provide scale < 1, but that's typically out
            # of spec or with some limitations, so don't go there.
            value = tuple(max(1, v / 2) for v in entry.vigilattr.value)

            # make sure it still fits
            if isinstance(entry.vigilattr.range, Iterable):
                value = tuple(max(v, m) for v, m in zip(value, entry.vigilattr.range[0]))

        elif entry.name == "binning":
            # if binning => smallest
            prev_val = entry.vigilattr.value
            try:
                value = entry.vigilattr.range[0]  # min
            except AttributeError:
                try:
                    value = min(entry.vigilattr.choices)
                except AttributeError:
                    pass
            # Compensate decrease in energy by longer exposure time
            et_entries = _get_entries(entries, entry.hw_comp, "exposureTime")
            for e in et_entries:
                et_value = ret.get(e, e.vigilattr.value)
                for prevb, newb in zip(prev_val, value):
                    et_value *= prevb / newb
                try:
                    # Clip the value (if it's a continuous VA, with a range)
                    et_value = e.vigilattr.clip(et_value)
                    # TODO: revert back the binning in such case?
                except AttributeError:
                    pass
                ret[e] = et_value

        elif entry.name == "exposureTime":
            if entry in ret:  # already computed (by binning), just reuse that value
                # We could just continue, but that'd skip the debug message
                value = ret[entry]

        elif entry.name == "readoutRate":
            # the smallest, the less noise (and slower, but we don't care)
            try:
                value = entry.vigilattr.range[0]  # min
            except AttributeError:
                try:
                    value = min(entry.vigilattr.choices)
                except AttributeError:
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
    logging.debug("Computing as-is preset")
    ret = {}
    for entry in entries:
        if (not hasattr(entry, "vigilattr") or entry.vigilattr is None or
            entry.vigilattr.readonly or entry.value_ctrl is None
           ):
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
