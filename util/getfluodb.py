#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 21 Jan 2013

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
import os
import shutil
import urllib2
import logging
import sys
import json

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
    ufile = urllib2.urlopen(url)
    lfile = open(filename, 'w') # will delete if exists
    shutil.copyfileobj(ufile, lfile)

def main(*args):
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
    findex = open(OUT_DIR + "environment/index.json", "r")
    index = json.load(findex)
    
    # For each environment, download it
    for eid, e in index.items():
        eurl = e["environment_url"]
        assert(eid == e["environment_id"])
        neid = int(eid) # should be a int (also ensures that there is no trick in the name)
        ename = OUT_DIR + "environment/%d.json" % neid
        logging.debug("Downloading environment %s", eid)
        download(eurl, ename)
        
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

if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)
