# -*- coding: utf-8 -*-
'''
Created on 24 Oct 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

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

# A wrapper to the comedi wrapper to make it more python like.
# The main adjustments are:
#  * functions and constants don't start with comedi_/COMEDI_
#  * functions raise a ComediError exception on error


# first, add everything from comedi as is
import comedi as _comedi

import inspect
import logging

class ComediError(Exception):
    def __init__(self, msg, errno, strerror, *args, **kwargs):
        super(ComediError, self).__init__(msg, errno, strerror, *args, **kwargs)
        self.args = (msg, errno, strerror)
        self.errno = errno
        self.strerror = strerror

    def __str__(self):
        return "%s -> (%d) %s" % self.args

def _raise_comedi_error(fname, rc):
    errno = _comedi.comedi_errno()
    raise ComediError("%s() returned %r" % (fname, rc),
                      errno, _comedi.comedi_strerror(errno))

def _default_function_wrapper(comedi_f):
    """
    calls a comedi function and check the return value for error
    error is considered when:
        * return code is int and < 0
        * return code is None (for pointers)
    """
    def f(*args):
        rc = comedi_f(*args)
        if rc is None or (isinstance(rc, int) and rc < 0):
            _raise_comedi_error(comedi_f.__name__, rc)
        return rc

    return f

def _data_read_wrapper(comedi_f):
    """
    calls a comedi function which return 2 values and check the return value for error
    """
    def f(*args):
        rc, data = comedi_f(*args)
        if rc < 0:
            _raise_comedi_error(comedi_f.__name__, rc)
        return data

    return f

# str -> callable: function name -> wrapper (function, *args)
# callable == None will result in no wrapper
_function_wrappers = {
                     "data_read": _data_read_wrapper,
                     "data_read_delayed": _data_read_wrapper,
                     "cleanup_calibration": None,
                     "perror": None,
                     }
def _wrap():
    # With comedi = 0.10.2 (not later), the functions already have comedi_
    # removed and are wrapped, so need to handle it differently
    if hasattr(_comedi, "wrapped"):
        mwrapped = _comedi.wrapped
    else:
        mwrapped = _comedi

    global_dict = globals()

    for name in dir(mwrapped):
        if name.startswith("_"):
            continue

        value = getattr(mwrapped, name)

        # Whatever happens, first duplicate this here
        if name not in global_dict:
            global_dict[name] = value
        else:
            logging.debug("comedi has already %s", name)

        # wrap every function starting with "comedi_"
        if name.startswith('comedi_'):
            shortname = name[7:]
#             if shortname in global_dict:
#                 logging.warning("comedi has already both %s and %s", name, shortname)
#                 continue

            if inspect.isclass(value):
                # if it's a struct, no wrapper
                global_dict[shortname] = value
            elif callable(value): # function
                wrapper = _function_wrappers.get(shortname, _default_function_wrapper)
                if wrapper:
                    fwrapped = wrapper(value)
                else:
                    fwrapped = value
                global_dict[shortname] = fwrapped
            else:
                logging.warning("%s is not callable, but starts with comedi_", name)

        # duplicate every constant starting with "COMEDI_"
        elif name.startswith('COMEDI_'):
            shortname = name[7:]
            if shortname in global_dict:
                logging.warning("comedi has already both %s and %s", name, shortname)
                continue
            global_dict[shortname] = value

        # For the new comedi, which already has the comedi_* dropped
        # => we just need to wrap the functions for error handling
        elif callable(value):
            wrapper = _function_wrappers.get(name, _default_function_wrapper)
            if wrapper:
                fwrapped = wrapper(value)
            else:
                fwrapped = value
            global_dict[name] = fwrapped

# wrap all comedi functions/constants that can be wrapped
_wrap()
