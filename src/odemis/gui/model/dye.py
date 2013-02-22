# -*- coding: utf-8 -*-
"""
Created on 22 Feb 2013

@author: Rinze de Laat

Copyright © 2013 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 2 of the License, or (at your option) any later
version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.


### Purpose ###

This module contains classes that control the dye database.

"""

import json
import logging
import time

# List of places to look for the database file
FLUODB_PATHS = ["/usr/share/odemis/fluodb/",
                "./install/linux/usr/share/odemis/fluodb/"]

# Simple dye database, that will be filled in at initialisation, if there is a
# database file available
# string (name) -> 2-tuple of float (excitation peak wl, emission peak wl in m)
# TODO: Should support having multiple peaks, orderer by strength
DyeDatabase = None

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
            findex = open(p + "environment/index.json")
        except IOError:
            # can't find this file, try the next one
            continue
        index = json.load(findex)
        basedir = p
        break

    if index is None:
        return False

    # Load the main excitation and emission peak for each environment
    # For each environment, download it
    for eid, e in index.items():
        # find the names (of the substance)
        names = set()
        s = e["substance"]
        names.add(s["common_name"].strip()) # in case loading the substance file fails
        nsid = int(s["substance_id"])
        sname = basedir + "substance/%d.json" % nsid
        try:
            fs = open(sname, "r")
            fulls = json.load(fs)
            for n in fulls["common_names"]:
                names.add(n.strip())
        except (IOError, ValueError):
            # no such file => no problem
            logging.debug("Failed to open %s", sname)
        names.discard("") # just in case some names are empty
        if not names:
            logging.debug("Skipping environment %d which has substance without name", eid)

        # find the peaks
        xpeaks = e["excitation_max"]
        epeaks = e["emission_max"]
        if len(xpeaks) == 0 or len(epeaks) == 0:
            # not enough information to be worthy
            continue
        xwl = xpeaks[0] * 1e-9 # m
        ewl = epeaks[0] * 1e-9 # m

        # Note: if two substances have the same name -> too bad, only the last
        # one will be in our database. (it's not a big deal, as it's usually
        # just duplicate entries)
        # TODO: if the peaks are really different, and the solvent too, then
        # append the name of the solvent in parenthesis.
        for n in names:
            if n in DyeDatabase:
                logging.debug("Dye database already had an entry for dye %s", n)
            DyeDatabase[n] = (xwl, ewl)

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

