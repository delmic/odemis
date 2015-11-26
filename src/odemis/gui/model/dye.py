# -*- coding: utf-8 -*-
"""
:created: 22 Feb 2013
:author: Rinze de Laat
:copyright: © 2013 Rinze de Laat, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License version 2 as published
    by the Free Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
    FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
    details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.




This module contains classes that control the dye database.

"""
from __future__ import division
import json
import logging
import time

# List of places to look for the database file
FLUODB_PATHS = (u"/usr/share/odemis/fluodb/",
                u"./install/linux/usr/share/odemis/fluodb/")

# Simple dye database, that will be filled in at initialisation, if there is a
# database file available
# string (name) -> 2-tuple of float (excitation peak wl, emission peak wl in m)
# TODO: Should support having multiple peaks, ordered by strength
DyeDatabase = None


def _clean_up_name(name):
    name = name.strip()
    # first letter upper-case
    name = name[:1].upper() + name[1:]

    return name


def LoadDyeDatabase():
    """ Try to fill the dye database from known files
    returns (boolean): True if a database was found, false otherwise
    Note: it uses a cached version of the Fluorophores.org JSON database
    """

    # For the API see doc/fluorophores-api.txt
    index = None
    basedir = None
    for p in FLUODB_PATHS:
        try:
            findex = open(p + u"environment/index.json")
        except IOError:
            # can't find this file, try the next one
            continue
        index = json.load(findex)
        basedir = p
        break
    else:
        return False

    # Name of the substance -> name of the substance + solvent
    fullnames = {}

    # Load the main excitation and emission peak for each environment
    # For each environment, download it
    for eid, e in index.items():
        # find the names (of the substance)
        names = set()
        s = e["substance"]
        names.add(_clean_up_name(s["common_name"]))  # in case loading the substance file fails
        nsid = int(s["substance_id"])
        sname = basedir + u"substance/%d.json" % nsid
        try:
            fs = open(sname, "r")
            fulls = json.load(fs)
            for n in fulls["common_names"]:
                names.add(_clean_up_name(n))
        except (IOError, ValueError):
            # no such file => no problem
            logging.debug("Failed to open %s", sname)
        names.discard("") # just in case some names are empty
        if not names:
            logging.debug("Skipping environment %d which has substance without name", eid)

        # solvent name
        solname = e["solvent"] or ""
        solname = solname.strip()

        # find the peaks
        xpeaks = e["excitation_max"]
        epeaks = e["emission_max"]
        if len(xpeaks) == 0 or len(epeaks) == 0:
            # not enough information to be worthy
            continue

        # In case of multiple peaks, select the one that make most sense:
        # excitation is just before emission
        if len(xpeaks) == 1:
            xp = xpeaks[0]
            if len(epeaks) == 1:  # easy
                ep = epeaks[0]
            else:  # Closest emission above excitation
                for ep in sorted(epeaks):
                    if ep > xp:
                        break
                else:
                    ep = max(epeaks)
        else: # multiple excitations
            if len(epeaks) == 1:  # Closest excitation below emissions
                ep = epeaks[0]
                for xp in sorted(xpeaks, reverse=True):
                    if xp < ep:
                        break
                else:
                    xp = min(xpeaks)
            else:  # Find something not too weird
                for ep in sorted(epeaks):
                    if any(xp < ep for xp in xpeaks):
                        break
                else:
                    ep = max(epeaks)
                for xp in sorted(xpeaks, reverse=True):
                    if xp < ep:
                        break
                else:
                    xp = min(xpeaks)

        if not xp <= ep:
            logging.info("Dye %s, excitation is %d > emission %d nm", s["common_name"], xp, ep)
        xwl = xp * 1e-9  # m
        ewl = ep * 1e-9  # m

        # Note: if two substances have the same name (and it changes something)
        # => add the solvent name.
        for n in names:
            if not solname:
                fullname = n
            else:
                fullname = n + u" (in %s)" % solname

            if n not in DyeDatabase:
                DyeDatabase[n] = (xwl, ewl)
                fullnames[n] = fullname
            else:
                odye = DyeDatabase[n]
                if odye == (xwl, ewl):
                    continue

                # Move the old simple name to its fullname
                # Note, if there is no solvent, then it doesn't really move
                del DyeDatabase[n]
                DyeDatabase[fullnames[n]] = odye

                # Add this new dye directly with its fullname
                if fullname in DyeDatabase and DyeDatabase[fullname] != (xwl, ewl):
                    logging.info("Dropping duplicated dye %s", fullname)

                DyeDatabase[fullname] = (xwl, ewl)

                if fullname == n:
                    # This one had no solvent, so no real fullname
                    fullnames[n] = fullname

    # TODO: also de-duplicate names in a case insensitive way
    logging.info("Loaded %d dye names from the database.", len(DyeDatabase))
    return True

# Load the database the first time the module is imported
if DyeDatabase is None:
    DyeDatabase = {} # This ensures we try only once
    start = time.time()
    try:
        # TODO: do it in a thread so that it doesn't slow down the loading?
        # Or preparse the database so that's very fast to load
        # For now, it seems to take 0.3 s => so let's say it's not needed
        # TODO: Don't use catchs-alls for exceptions.
        result = LoadDyeDatabase()
    except:
        logging.exception("Failed to load the fluorophores database.")
    else:
        if not result:
            logging.info("No fluorophores database found.")

    load_time = time.time() - start
    logging.debug("Dye database loading took %g s", load_time)

