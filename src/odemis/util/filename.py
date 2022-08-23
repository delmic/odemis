# -*- coding: utf-8 -*-
"""
Created on 29 Jan 2018

@author: Philip Winkler

Copyright © 2018 Philip Winkler, Delmic

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

import logging
from odemis.util.dataio import splitext
import os
import re
import time

from datetime import datetime, timedelta


def guess_pattern(fn):
    """
    Generates a filename pattern form a given filename. The function detects certain 
    formats of dates, time, and counters. It returns a string with replacement fields which
    can be used in create_filename to provide an updated version of the filename.
    input
        fn (String): filename (may include path and extension)
    returns 
        fn_ptn (String): filename pattern (without path and extension)
        cnt (String): counter
    """

    _, fn_ptn = os.path.split(fn)

    # Get extension
    fn_ptn, _ = splitext(fn_ptn)

    # Detect {}
    fn_ptn = re.sub('{', "{{", fn_ptn)
    fn_ptn = re.sub('}', "}}", fn_ptn)

    # Detect date
    # First check daterev, then datelng because sequence daterev + timelng might otherwise
    # be mistaken to be number + datelng + counter
    date_ptn = time.strftime("%d%m%Y")
    fn_ptn = re.sub(date_ptn, "{daterev}", fn_ptn)

    date_ptn = time.strftime("%Y%m%d")
    fn_ptn = re.sub(date_ptn, "{datelng}", fn_ptn)

    date_ptn = time.strftime("%d-%m-%Y")
    fn_ptn = re.sub(date_ptn, "{daterev_hyphen}", fn_ptn)

    date_ptn = time.strftime("%Y-%m-%d")
    fn_ptn = re.sub(date_ptn, "{datelng_hyphen}", fn_ptn)

    # Short dates second, only in case the long version had no match
    date_ptn = time.strftime("%d%m%y")
    fn_ptn = re.sub(date_ptn, "{dshrtrev}", fn_ptn)

    date_ptn = time.strftime("%y%m%d")
    fn_ptn = re.sub(date_ptn, "{dateshrt}", fn_ptn)

    date_ptn = time.strftime("%d-%m-%y")
    fn_ptn = re.sub(date_ptn, "{dshrtrev_hyphen}", fn_ptn)

    date_ptn = time.strftime("%y-%m-%d")
    fn_ptn = re.sub(date_ptn, "{dateshrt_hyphen}", fn_ptn)

    year_ptn = '%s' % time.strftime('%Y')
    fn_ptn = re.sub(year_ptn, "{year}", fn_ptn)

    # Detect time, it must be within 5 min of the current time, to reduce risks of collision
    now = datetime.now()
    for name_ptn, time_ptn_re, time_ptn_strp in [
        ("{timelng}", "[0-2][0-9][0-5][0-9][0-5][0-9]", "%H%M%S"),
        ("{timelng_colon}", "[0-2][0-9]:[0-5][0-9]:[0-5][0-9]", "%H:%M:%S"),
        ("{timelng_hyphen}", "[0-2][0-9]-[0-5][0-9]-[0-5][0-9]", "%H-%M-%S"),
        # We used to support timeshrt without separation and with hyphen, but they
        # too easily collided with other meanings for the users, so we dropped them.
        ("{timeshrt_colon}", "[0-2][0-9]:[0-5][0-9]", "%H:%M")
        ]:
        m = re.search(time_ptn_re, fn_ptn)
        if m:
            time_match = m.group()
            # Use the current date, with the provided time
            time_found = datetime.combine(now.date(), datetime.strptime(time_match, time_ptn_strp).time())

            if abs(time_found - now) < timedelta(minutes=5):
                # That's a real match => change the pattern
                fn_ptn = fn_ptn[0:m.start()] + name_ptn + fn_ptn[m.end():]

    # Detect count
    cnt_ptn = r'\d{1,5}'
    cnt_m = None
    for m in re.finditer(cnt_ptn, fn_ptn):
        cnt_m = m
    # if multiple numbers are present, use last
    if cnt_m:
        cnt = cnt_m.group()
        fn_ptn = fn_ptn[:cnt_m.start()] + "{cnt}" + fn_ptn[cnt_m.end():]
    else:
        cnt = "001"  # will be used in case cnt pattern is added afterwards

    # If neither time, nor count are specified, add count, if only short time (h, min)
    # specified and no count, add seconds to make filename unique
    # Doesn't behave properly if user enters terms with curly braces like {{cnt}}.
    if '{cnt}' not in fn_ptn:
        if '{timeshrt_colon}' in fn_ptn:
            fn_ptn = re.sub('{timeshrt_colon}', '{timelng_colon}', fn_ptn)
        elif ("{timelng}" in fn_ptn or
              "{timelng_colon}" in fn_ptn or
              "{timelng_hyphen}" in fn_ptn):
            pass
        else:
            fn_ptn = fn_ptn + '-{cnt}'

    return fn_ptn, cnt

def create_projectname(path, ptn, count="001"):
    """
    Create new project directory name from pattern. Calls create_filename with no extension
    :return: pn (String): unique project name (including path)
    """
    return create_filename(path, ptn, ext="", count=count)

def create_filename(path, ptn, ext, count="001"):
    """
    Creates a new unique filename from filename pattern. If the default filename from the
    filename pattern is already in the directory, the count is increased, or, in case no
    counter is present in the pattern, a counter is added.
    input
        path (String): path to filename (as in conf.last_path)
        ptn (String): filename pattern (without path and extension) as in conf.fn_ptn
        ext (String): filename extension (as in conf.last_extension)
        count (String): counter (as String, so new counter can be returned with same number
            of leading zeros as in conf.fn_count
    returns
        fn (String): unique filename (including path and extension)
    """

    def generate_fn(ptn, count):
        return ptn.format(datelng=time.strftime("%Y%m%d"),
                          daterev=time.strftime("%d%m%Y"),
                          datelng_hyphen=time.strftime("%Y-%m-%d"),
                          daterev_hyphen=time.strftime("%d-%m-%Y"),
                          dateshrt=time.strftime("%y%m%d"),
                          dshrtrev=time.strftime("%d%m%y"),
                          dateshrt_hyphen=time.strftime("%y-%m-%d"),
                          dshrtrev_hyphen=time.strftime("%d-%m-%y"),
                          year=time.strftime("%Y"),
                          timelng=time.strftime("%H%M%S"),
                          timelng_colon=time.strftime("%H:%M:%S"),
                          timelng_hyphen=time.strftime("%H-%M-%S"),
                          timeshrt_colon=time.strftime("%H:%M"),
                          timeshrt_hyphen=time.strftime("%H-%M"),
                          cnt='%s' % count)

    fn = generate_fn(ptn, count)

    # Ensure filename is unique
    try:
        while fn + ext in os.listdir(path):
            count = update_counter(count)
            new_fn = generate_fn(ptn, count)
            if new_fn == fn:
                # No counter in the pattern => add one
                ptn += "-{cnt}"
                count = "001"
            else:
                fn = new_fn
    except OSError as ex:
        # Mostly in case "path" doesn't exists
        logging.warning("%s, will not check filename is unique", ex)

    return os.path.join(path, fn + ext)

def make_unique_name(name, existing):
    """
    Creates a name based on the input name which is unique in a list of existing names by adding a counter.
    input
        name (str): proposed name (without extension)
        names (list of str): list of existing names
    returns
        name (str): unique name
    """
    # Filename not in names, just return
    if name not in existing:
        return name

    # Detect if filename has counter already, otherwise use 1
    cnt_ptn = r'-\d{1,5}'
    cnt_m = None
    for m in re.finditer(cnt_ptn, name):
        cnt_m = m
    # if multiple numbers are present, use last
    if cnt_m:
        cnt = int(cnt_m.group()[1:])
        ptn = name[:cnt_m.start()] + "{cnt}" + name[cnt_m.end():]
    else:
        cnt = 1  # will be used in case cnt pattern is added afterwards
        ptn = name + "{cnt}"

    # Count up until unique filename is found
    while name in existing:
        name = ptn.format(cnt="-%s" % cnt)
        cnt += 1

    return name

def update_counter(old_count):
    """
    Update counter for filename. Keeps leading zeros.
    input:
        old_count (String): number (possibly with leading zeros) formatted as String
    returns:
        new_count (String): number += 1 in same format
    raises
        AssertionError if old_count is negative
    """

    assert old_count[0] != "-"
    c = int(old_count) + 1
    n_digits = len(old_count)
    new_count = str(c).zfill(n_digits)  # add as many leading zeros as specified
    return new_count

