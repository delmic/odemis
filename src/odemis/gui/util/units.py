# -*- coding: utf-8 -*-
"""
Created on 20 Feb 2012

@author: Éric Piel

Various utility functions for displaying numbers (with and without units).

Copyright © 2012 Éric Piel, Delmic

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

"""

import collections
import math

SI_PREFIXES = {9: u"G",
               6: u"M",
               3: u"k",
               0: u"",
               -3: u"m",
               -6: u"µ",
               -9: u"n",
               -12: u"p"}

def round_significant(x, n):
    """
    Round a number to n significant figures
    """
    if x == 0:
        return 0

    return round(x, int(n - math.ceil(math.log10(abs(x)))))

def round_down_significant(x, n):
    """
    Round a number to n significant figures making sure it's smaller
    """
    if x == 0:
        return 0

    exp = n - math.ceil(math.log10(abs(x)))
    if x > 0:
        ret = math.floor(x * 10 ** exp) / (10 ** exp)
    else:
        ret = math.ceil(x * 10 ** exp) / (10 ** exp)
    # assert(abs(ret) <= abs(x))
    return ret

def get_si_scale(x):
    """ This function returns the best fitting SI scale for the given numerical
    value x.
    Returns a (float, string) tuple: (divisor , SI prefix)
    """
    if x == 0:
        return (1, "")
    
    most_significant = int(math.floor(math.log10(abs(x))))
    prefix_order = (most_significant / 3) * 3 # rounding to multiple of 3
    prefix_order = max(-12, min(prefix_order, 9)) # clamping
    return (10.0 ** prefix_order), SI_PREFIXES[prefix_order]

def to_si_scale(x):
    """ Scale the given value x to the best fitting metric prefix.
    Return a tuple: (scaled value of x, prefix)
    """
    divisor, prefix = get_si_scale(x)
    return x / divisor, prefix

def si_scale_list(values):
    """ Scales a list of numerical values using the same metrix scale """
    if values:
        marker = max(values) or min(values)
        divisor, prefix = get_si_scale(marker)
        return [v / divisor for v in values], prefix
    return None, ""

def to_string_si_prefix(x):
    """
    Convert a number to a string with the most appropriate SI prefix appended
    ex: 0.0012 -> "1.2 m"
    x (float): number
    return (string)
    """
    return "%g %s" % to_si_scale(x)

def readable_str(value, unit=None):
    """
    Convert a value with a unit into a displayable string for the user
    value (any type): can be a number or a collection of number
    return (string)
    """
    unit = unit or u""
    if isinstance(value, collections.Iterable):
        val_str = u"%s%s" % (u" x ".join([to_string_si_prefix(v) for v in value]), unit)
    else:
        val_str = u"%s%s" % (to_string_si_prefix(value), unit)

    return val_str
# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: