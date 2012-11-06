# -*- coding: utf-8 -*-
'''
Created on 24 Oct 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
"""
A wrapper to the comedi wrapper to make it more python like.
The main adjustments are:
 * functions and constants don't start with comedi_/COMEDI_
 * functions raise a ComediError exception on error 
"""

# first, add everything from comedi as is
from comedi import *

import inspect
import logging
from functools import partial

class ComediError(Exception):
    def __init__(self, rc, errno, strerror):
        self.args = (rc, errno, strerror)
        
    def __str__(self):
        return "returned %r -> (%d) %s" % self.args

def _raise_comedi_error(rc):
    errno = comedi_errno()
    raise ComediError(rc, errno, comedi_strerror(errno))

def _default_function_wrapper(comedi_f, *args):
    """
    calls a comedi function and check the return value for error
    error is considered when:
        * return code is int and < 0
        * return code is None (for pointers)
    """
    rc = comedi_f(*args)
    if rc is None or (isinstance(rc, int) and rc < 0):
        _raise_comedi_error(rc)
    return rc

def _data_read_wrapper(comedi_f, *args):
    """
    calls a comedi function which return 2 values and check the return value for error
    """
    rc, data = comedi_f(*args)
    if rc < 0:
        _raise_comedi_error(rc)
    return data

# str -> callable: function name -> wrapper (function, *args)
# callable == None will result in no wrapper
_function_wrappers = {
                     "data_read": _data_read_wrapper,
                     "data_read_delayed": _data_read_wrapper,
                     }
def _wrap():
    import comedi as _comedi
    global_dict = globals()
    
    for name in dir(_comedi):
        if name.startswith("_"):
            continue
        
        value = getattr(_comedi, name)
        
        # wrap every function starting with "comedi_"
        if name.startswith('comedi_'):
            shortname = name[7:]
            if shortname in global_dict:
                logging.warning("comedi has already both %s and %s", name, shortname)
                continue
            
            if inspect.isclass(value):
                # if it's a struct, no wrapper
                global_dict[shortname] = value
            elif callable(value): # function
                wrapper = _function_wrappers.get(shortname, _default_function_wrapper)
                if wrapper:
                    fwrapped = partial(wrapper, value)
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
            
# wrap all comedi functions/constants that can be wrapped
_wrap()
