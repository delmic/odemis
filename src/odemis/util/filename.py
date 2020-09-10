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

from __future__ import division

import logging
from odemis.util.dataio import splitext
import os
import re
import time


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

    # Detect time h-min-s
    time_ptn = '[0-2][0-9][0-5][0-9][0-5][0-9]'
    fn_ptn = re.sub(time_ptn, "{timelng}", fn_ptn)

    time_ptn = '[0-2][0-9]:[0-5][0-9]:[0-5][0-9]'
    fn_ptn = re.sub(time_ptn, "{timelng_colon}", fn_ptn)

    time_ptn = '[0-2][0-9]-[0-5][0-9]-[0-5][0-9]'
    fn_ptn = re.sub(time_ptn, "{timelng_hyphen}", fn_ptn)

    # Detect time h-min
    time_ptn = '[0-2][0-9]:[0-5][0-9]'
    fn_ptn = re.sub(time_ptn, "{timeshrt_colon}", fn_ptn)

    # Don't allow separation of short time by hyphen, causes problems with some names

    # If 4-digit number corresponds to time in an interval of +- 15 min
    # around the current time, recognize it as time pattern, otherwise
    # assume it's a counter.
    time_ptn = '[0-2][0-9][0-5][0-9]'
    if re.search('[0-2][0-9][0-5][0-9]', fn_ptn):
        h = int(re.search('[0-2][0-9][0-5][0-9]', fn_ptn).group()[:2])
        m = int(re.search('[0-2][0-9][0-5][0-9]', fn_ptn).group()[2:])
        t_s = h * 3600 + m * 60
        cur_t_s = int(time.strftime('%H')) * 3600 + int(time.strftime('%M')) * 60
        if cur_t_s - 900 < t_s < cur_t_s + 900:
            fn_ptn = re.sub(time_ptn, "{timeshrt}", fn_ptn)

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
        if '{timeshrt}' in fn_ptn:
            fn_ptn = re.sub('{timeshrt}', '{timelng}', fn_ptn)
        if '{timeshrt_colon}' in fn_ptn:
            fn_ptn = re.sub('{timeshrt_colon}', '{timelng_colon}', fn_ptn)
        elif ("{timelng}" in fn_ptn or
              "{timelng_colon}" in fn_ptn or
              "{timelng_hyphen}" in fn_ptn):
            pass
        else:
            fn_ptn = fn_ptn + '-{cnt}'

    return fn_ptn, cnt


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
                          year="%Y",
                          timelng=time.strftime("%H%M%S"),
                          timelng_colon=time.strftime("%H:%M:%S"),
                          timelng_hyphen=time.strftime("%H-%M-%S"),
                          timeshrt=time.strftime("%H%M"),
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

