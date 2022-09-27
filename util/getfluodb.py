#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 21 Jan 2013

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
'''
import os
import shutil
import logging
import sys
import json
from future.moves.urllib.request import urlopen

logging.getLogger().setLevel(logging.DEBUG)

# Downloads the fluorophores database from fluorophores.org (using the special
# JSON interface we've sponsored)
# See fluorophores-api.txt for the API description. Basically, there are 
# "environments" (a dye) which have a excitation and emission wavelength. Each
# environment is defined also by a temperature, solvent, pH, and substance.
# Each substance can be queried, to get more information on it.

URL_DB = "http://www.fluorophores.tugraz.at/"
# Where all the files will be saved (must exists)
OUT_DIR = "./install/linux/usr/share/odemis/fluodb/"


def download(url, filename):
    """
    url (string): place from where to download
    filename (sting): place to where to save (if already exists, will be deleted)
    """
    if url.startswith("/"): # absolute path
        url = URL_DB + url
    ufile = urlopen(url)
    lfile = open(filename, 'wb')  # will delete if exists
    shutil.copyfileobj(ufile, lfile)

def open_json_or_remove(filename):
    """
    try to open a JSON file. Try to fix it if it has strange escape sequences,
    and delete the file if it's really bad.
    filename (string): path to a JSON file to open
    return (object): python object representing the content of the JSON file
    raises: 
        ValueError: if the file is not a correct JSON file (the file has been 
          deleted)
    """
    f = open(filename, "r")
    try:
        content = json.load(f)
        return content
    except ValueError:
        logging.error("File %s seems to be an invalid JSON file, trying to fix its escape sequences...", filename)

    # fluorophores.org sometimes returns JSON files which have "\ ". It's
    # officially invalid, but it's not hard to make sense of it.
    f.seek(0)
    text = f.read()
    fixed_text = text.replace("\\ ", " ")

    # fluorophores.org sometimes returns JSON files which have multiple lines
    # strings, separated with \r\n. It's invalid, but easy to understand.
    # => change \r\n to " ".
    fixed_text = fixed_text.replace("\r\n", " ")

    try:
        if fixed_text == text:
            # not much hope
            raise ValueError()
        else:
            # save the fixed version
            f.close()
            f = open(filename, "w")
            f.write(fixed_text)

        content = json.loads(fixed_text)
        logging.error("File %s was fixed", filename)
        return content
    except ValueError:
        logging.info("File %s seems to be an invalid JSON file, deleting", filename)
        f.close()
        os.remove(filename)
        raise


def main(args):
    if not os.path.exists(OUT_DIR):
        logging.error("Directory '%s' doesn't exists, stopping.", OUT_DIR)
        return 1

    # create the sub directories
    for p in ["environment", "substance"]:
        fp = OUT_DIR + p
        if not os.path.exists(fp):
            os.mkdir(fp)

    # Download the root (environment index)
    logging.debug("Downloading the Environment index")
    download(URL_DB + "environment/index.json", OUT_DIR + "environment/index.json")
    # parse it
    try:
        index = open_json_or_remove(OUT_DIR + "environment/index.json")
    except ValueError:
        logging.error("Cannot go further")
        return 1

    # For each environment, download it
    for eid, e in index.items():
        eurl = e["environment_url"]
        assert(eid == e["environment_id"])
        neid = int(eid) # should be a int (also ensures that there is no trick in the name)
        ename = OUT_DIR + "environment/%d.json" % neid
        logging.debug("Downloading environment %s", eid)
        download(eurl, ename)
        try:
            open_json_or_remove(ename)
        except ValueError:
            logging.exception("Skipping %s", eurl)

    substances = {} # id -> url
    # For each substance, download it
    for eid, e in index.items():
        s = e["substance"]
        surl = s["substance_url"]
        nsid = int(s["substance_id"])
        if nsid in substances:
            logging.info("Already downloaded substance %d, skipping", nsid)
            continue
        substances[nsid] = surl
        logging.debug("Downloading substance %d", nsid)
        sname = OUT_DIR + "substance/%d.json" % nsid
        download(surl, sname)

        try:
            fulls = open_json_or_remove(sname)
        except ValueError:
            logging.exception("Skipping %s", surl)
            continue

        # gif/png file too, if it is there
        strurl = fulls["structure"]
        if strurl:
            strsname = strurl.rsplit("/", 1)[1]
            logging.debug("Downloading structure %s", strsname)
            strname = OUT_DIR + "substance/" + strsname

            try:
                download(strurl, strname)
            except Exception:
                logging.exception("Failed to download structure image @ %s", strurl)

if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)
