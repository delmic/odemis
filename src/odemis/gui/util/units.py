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
from __future__ import division
import collections
import logging
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
        return (1, u"")

    most_significant = math.floor(math.log10(abs(x)))
    prefix_order = (most_significant // 3) * 3 # rounding to multiple of 3
    prefix_order = max(-12, min(prefix_order, 9)) # clamping
    return (10 ** prefix_order), SI_PREFIXES[int(prefix_order)]

def to_si_scale(x):
    """ Scale the given value x to the best fitting metric prefix.
    Return a tuple: (scaled value of x, prefix)
    """
    divisor, prefix = get_si_scale(x)
    return x / divisor, prefix

def si_scale_list(values):
    """ Scales a list of numerical values using the same metrix scale """
    if values:
        marker = max(values)
        divisor, prefix = get_si_scale(marker)
        return [v / divisor for v in values], prefix
    return None, u""

def to_string_si_prefix(x, sig=None):
    """
    Convert a number to a string with the most appropriate SI prefix appended
    ex: 0.0012 -> "1.2 m"
    x (float): number
    return (string)
    """
    value, prefix = to_si_scale(x)
    return u"%s %s" % (to_string_pretty(value, sig), prefix)

def to_string_pretty(x, sig=None):
    """
    Convert a number to a string as int or float as most appropriate
    :param sig: (int) The number of significant decimals
    """
    if x == 0:
        # don't consider this a float
        return u"0"

    # so close from an int that it's very likely one?
    if abs(x - round(x)) < 1e-5:
        x = int(round(x)) # avoid the .0

    if abs(x) < 1 or isinstance(x, float):
        # just a float
        if sig:
            fmt = "{0:0.%sf}" % sig
            return fmt.format(x)
        else:
            return u"%r" % x

    return u"%s" % x

def readable_str(value, unit=None, sig=3):
    """
    Convert a value with a unit into a displayable string for the user

    :param value: (number or [number...]): value(s) to display
    :param unit: (None or string): unit of the values. If necessary a SI prefix
        will be used to make the value more readable, unless None is given.
    :param sig: (int) The number of significant decimals

    return (string)
    """
    if unit is None:
        # don't put SI scaling prefix
        if isinstance(value, collections.Iterable):
            # Could use "×" , but less readable than "x"
            return u" x ".join([to_string_pretty(v, sig) for v in value])
        else:
            return to_string_pretty(value, sig)

    if isinstance(value, collections.Iterable):
        values, prefix = si_scale_list(value)
        return u"%s %s%s" % (u" x ".join([to_string_pretty(v, sig) for v in values]), prefix, unit)
    else:
        return u"%s%s" % (to_string_si_prefix(value, sig), unit)


def readable_time(seconds):
    """This function translates intervals given in seconds into human readable
    strings.
    seconds (float)
    """
    # TODO: a way to indicate some kind of significant number? (If it's going to
    # last 5 days, the number of seconds is generally pointless)
    result = []

    sign = 1
    if seconds < 0:
        # it's just plain weird, but let's do as well as we can
        logging.warning("Asked to display negative time %f", seconds)
        sign = -1
        seconds = -seconds

    if seconds > 60 * 60 * 24 * 30:
        # just for us to remember to extend the function
        logging.debug("Converting time longer than a month.")

    second, subsec = divmod(seconds, 1)
    msec = round(subsec * 1e3)
    if msec == 1000:
        msec = 0
        second += 1
    if second == 0 and msec == 0:
        # exactly 0 => special case
        return "0 second"

    minute, second = divmod(second, 60)
    hour, minute = divmod(minute, 60)
    day, hour = divmod(hour, 24)

    if day:
        result.append("%d day%s" % (day, "" if day == 1 else "s"))

    if hour:
        result.append("%d hour%s" % (hour, "" if hour == 1 else "s"))

    if minute:
        result.append("%d minute%s" % (minute, "" if minute == 1 else "s"))

    if second:
        result.append("%d second%s" % (second, "" if second == 1 else "s"))

    if msec:
        result.append("%d ms" % msec)

    if len(result) == 1:
        # simple case
        ret = result[0]
    else:
        # make them "x, x, x and x"
        ret = "{} and {}".format(", ".join(result[:-1]), result[-1])

    if sign == -1:
        ret = "minus " + ret

    return ret

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: