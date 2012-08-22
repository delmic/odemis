#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 20 Feb 2012

@author: Éric Piel

Various utility functions for displaying numbers (with and without units).

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
import math

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
#    assert(abs(ret) <= abs(x))
    return ret

def to_string_si_prefix(x):
    """
    Convert a number to a string with the most appropriate SI prefix appended
    ex: 0.0012 -> "1.2m"
    x (float): number
    return (string)
    """
    prefixes = {9: "G", 6: "M", 3: "k", 0: "", -3: "m", -6:"µ", -9:"n", -12:"p"}
    if x == 0:
        return "0"
    most_significant = int(math.floor(math.log10(abs(x))))
    prefix_order = (most_significant / 3) * 3 # rounding
    prefix_order = max(-12, min(prefix_order, 9)) # clamping
    rounded = "{:g}".format(x / (10.0 ** prefix_order))
    prefix = prefixes[prefix_order]
    return "{0} {1}".format(rounded, prefix)

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: