# -*- coding: utf-8 -*-
"""
Created on 20 Feb 2012

@author: Éric Piel

Various utility functions for displaying numbers (with and without units).

Copyright © 2012-2015 Éric Piel, Delmic

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
from collections.abc import Iterable, Sequence
import logging
import math
import numbers
import numpy
import re


SI_PREFIXES = {
    9: u"G",
    6: u"M",
    3: u"k",
    0: u"",
    -3: u"m",
    -6: u"µ",
    -9: u"n",
    -12: u"p",

    u"G": 9,
    u"M": 6,
    u"k": 3,
    u"": 0,
    u"m": -3,
    u"µ": -6,
    u"n": -9,
    u"p": -12,
}

# The following units should ignore SI formatting
IGNORE_UNITS = (None, "", "px", "C", u"°C", u"°", "%", "od")


def round_significant(x, n):
    """
    Round a number to n significant figures
    """
    if x == 0:
        return 0

    return round(x, int(n - math.ceil(math.log10(abs(x)))))


def round_down_significant(x: float, n: int) -> float:
    """
    Round a number to n significant figures making sure it's smaller.
    It rounds towards 0, so for negative numbers the absolute value is also reduced.
    x: the value to round
    n (>=1): number of significant figures to keep
    returns: the rounded value
    Example:
        round_down_significant(136, 2) -> 130
        round_down_significant(-0.136, 2) -> -0.13
    """
    if x == 0:
        return 0

    exp = n - math.ceil(math.log10(abs(x)))
    return math.trunc(x * 10 ** exp) / (10 ** exp)


def get_si_scale(x):
    """ This function returns the best fitting SI scale for the given numerical
    value x.
    Returns a (float, string) tuple: (divisor , SI prefix)
    """
    if x == 0 or math.isnan(x) or math.isinf(x):
        return 1, u""

    most_significant = math.floor(math.log10(abs(x)))
    prefix_order = (most_significant // 3) * 3  # rounding to multiple of 3
    prefix_order = max(-12, min(prefix_order, 9))  # clamping
    return (10 ** prefix_order), SI_PREFIXES[int(prefix_order)]


def to_si_scale(x):
    """ Scale the given value x to the best fitting metric prefix.
    Return a tuple: (scaled value of x, prefix)
    """
    divisor, prefix = get_si_scale(x)
    return x / divisor, prefix


def si_scale_val(val, si):
    """ Scale the value according to the given si prefix """
    if si in SI_PREFIXES:
        val *= (10 ** SI_PREFIXES[si])
    return val


def si_scale_list(values, prefix=None):
    """
    Scales a list of numerical values using the same metric scale
    values (list of numbers)
    prefix (None or SI_PREFIXES): the unit prefix to use. If None, the best one
      will be picked, based on the values.
    returns:
       rescaled values (list of floats)
       prefix (str): the unit prefix
    """
    if values:
        if prefix is None:
            marker = max(values)
            divisor, prefix = get_si_scale(marker)
        elif prefix in SI_PREFIXES:
            divisor = 10 ** SI_PREFIXES[prefix]
        else:
            raise ValueError(u"Prefix %s is unknown" % prefix)
        return [v / divisor for v in values], prefix
    return None, u""


def to_string_si_prefix(x, sig=None):
    """ Convert a number to a string with the most appropriate SI prefix appended

    e.g.: 0.0012 -> "1.2 m"

    Args:
        x (float): Number to parse
        sig (int or None): Number of significant numbers

    Returns:
        (str): String representing the number value

    """

    if sig is not None:
        # Round it *before* calculating the scale, so that values
        # like 999.7 first become 1000, which can then get converted to 1k.
        x = round_significant(x, sig)

    value, prefix = to_si_scale(x)
    return u"%s %s" % (to_string_pretty(value, sig), prefix)


def decompose_si_prefix(str_val, unit=None):
    """ Decompose the given string value into a value string, an si prefix and a unit

    Args:
        str_val: (string) A string representation of a value with a si prefixed unit
        unit: (string or None) If the unit is provided and a different unit is detected
          it will return the str_val as-is.

    Returns:
        (string) str_val, (string) si prefix, (string) unit

    Note:
        This function also matches an ASCII 'u' character as a greek letter 'µ'!

    """

    if unit:
        match = re.match(u"([+-]?[\\d.]+(?:[eE][+-]?[\\d]+)?)[ ]*([GMkmµunp])?(%s)?$" % unit,
                         str_val.strip())
    else:  # Look for any unit
        match = re.match(u"([+-]?[\\d.]+(?:[eE][+-]?[\\d]+)?)[ ]*([GMkmµunp])?([A-Za-z]+)?$",
                         str_val.strip())

    if match:
        val, rprefix, runit = match.group(1, 2, 3)
        # If we found a "prefix" but no unit (eg, "1 m"), assume it's the unit
        if (rprefix is not None and runit is None and
            (not unit or rprefix == unit)):
            rprefix, runit = runit, rprefix
        if rprefix == u"u":
            rprefix = u"µ"
        return val, rprefix, runit
    else:
        return str_val, None, None


def to_string_pretty(x, sig=None, unit=None):
    """ Convert a number to a string as int or float as most appropriate

    Args:
        x: (int or float) The number to be converted
        sig:  (int or None) The number of significant figures
        unit: (str or None) unit of the values

    Returns:
        (str): the decimal representation of the number with possibly a unit prefix to indicate the
        magnitude (but _not_ the unit).

        It can also return "unknown" or "∞" for NaN and inf.

    """

    if x == 0:
        # don't consider this a float
        return u"0"

    elif math.isnan(x):
        return "unknown"
    elif math.isinf(x):
        if x < 0:
            return u"-∞"
        else:
            return u"∞"

    if sig is not None:
        x = round_significant(x, sig)

    # so close from an int that it's very likely one?
    if abs(x - round(x)) < 1e-5 and abs(x) >= 1:
        x = int(round(x))  # avoid the .0

    if isinstance(x, float):
        if isinstance(x, numpy.floating):
            x = float(x)  # convert to standard float to get the standard display

        str_val = "%r" % x

        if unit in IGNORE_UNITS:
            return str_val
        else:
            # Get the scale that a readable (formatted) string would use
            eo, _ = get_si_scale(x)
            scale = int(round(math.log(eo, 10)))

            fn, _, ep = str_val.partition('e')
            ep = int(ep or 0)

            dot_move = ep - scale

            if dot_move and '.' in fn:
                dot_pos = fn.index('.')
                new_dot_pos = dot_pos + dot_move
                fn = fn.replace(".", "")

                if new_dot_pos > len(fn):
                    fn = fn.ljust(new_dot_pos, '0')

                fn = ".".join([fn[:new_dot_pos], fn[new_dot_pos:]])
                return u"%se%d" % (fn.strip('0').strip('.'), scale)
            else:
                return str_val

    return u"%s" % x


def readable_str(value, unit=None, sig=None):
    """
    Convert a value with a unit into a displayable string for the user

    :param value: (number or [number...]): value(s) to display
    :param unit: (None or string): unit of the values. If necessary a SI prefix
        will be used to make the value more readable, unless None is given.
    :param sig: (int or None) The number of significant figures. If None, it
      will use not round the value (and just limit the display to a certain
      number of decimals)

    return (string)
    """
    # TODO: convert % to ‰ when small value?
    # check against our black list of units which don't support SI prefix

    if value is None:
        return ""

    if unit in IGNORE_UNITS:
        # don't put SI scaling prefix
        if unit in (None, ""):
            sunit = u""
        else:
            sunit = u" %s" % unit
        if isinstance(value, Iterable):
            # Could use "×" , but less readable than "x"
            return u"%s%s" % (u" x ".join([to_string_pretty(v, sig) for v in value]), sunit)
        else:
            return u"%s%s" % (to_string_pretty(value, sig), sunit)

    # TODO: special case for s: only if < 10

    if isinstance(value, Iterable):
        values, prefix = si_scale_list(value)
        return u"%s %s%s" % (u" x ".join([to_string_pretty(v, sig) for v in values]), prefix, unit)
    else:
        return u"%s%s" % (to_string_si_prefix(value, sig), unit)


def readable_time(seconds, full=True):
    """This function translates intervals given in seconds into human readable
    strings.
    seconds (float)
    full (bool): if True, will display with full words, otherwise will use an
                 abbreviated way
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
    msec, submsec = divmod(subsec, 1e-3)
    usec = round(submsec * 1e6)
    if usec >= 1000:
        usec -= 1000
        msec += 1
    if msec >= 1000:
        msec -= 1000
        second += 1

    if second == 0 and msec == 0 and usec == 0:
        # exactly 0 => special case
        if full:
            return u"0 second"
        else:
            return u"0 s"

    minute, second = divmod(second, 60)
    hour, minute = divmod(minute, 60)
    day, hour = divmod(hour, 24)

    if day:
        if full:
            result.append(u"%d day%s" % (day, u"" if day == 1 else u"s"))
        else:
            result.append(u"%d d" % (day,))

    if hour:
        if full:
            result.append(u"%d hour%s" % (hour, u"" if hour == 1 else u"s"))
        else:
            result.append(u"%d h" % (hour,))

    if minute:
        if full:
            result.append(u"%d minute%s" % (minute, u"" if minute == 1 else u"s"))
        else:
            result.append(u"%d min" % (minute,))

    if second:
        if full:
            result.append(u"%d second%s" % (second, u"" if second == 1 else u"s"))
        else:
            result.append(u"%d s" % (second,))

    if msec:
        if full:
            result.append(u"%d millisecond%s" % (msec, u"" if msec == 1 else u"s"))
        else:
            result.append(u"%d ms" % msec)

    if usec:
        if full:
            result.append(u"%d microsecond%s" % (usec, u"" if usec == 1 else u"s"))
        else:
            result.append(u"%d µs" % usec)

    if len(result) == 1:
        # simple case
        ret = result[0]
    else:
        # make them "x, x, x and x"
        ret = u"{} and {}".format(u", ".join(result[:-1]), result[-1])

    if sign == -1:
        ret = u"minus " + ret

    return ret


def value_to_str(value, unit=None, accuracy=None, pretty_time=False):
    """
    Attempt to convert any type of value to the most user-friendly string.
    value (value)
    unit (str or None): if value is a number or a sequence of numbers, it will be
      shown after the value
    accuracy (0<=int or None): The number of significant figures, if the value
      is a number of a sequence of numbers.
    pretty_time (bool): If True, values associated with the "s" unit will be
      converted to a full time display (as in "day, hour, min...")
    return (unicode string): user friendly string of the value
    """

    try:
        if pretty_time and unit == "s" and isinstance(value, numbers.Real):
            return readable_time(value, full=False)
        elif (isinstance(value, numbers.Real) or
              (isinstance(value, Sequence) and
               len(value) > 0 and
               isinstance(value[0], numbers.Real))
             ):
            return readable_str(value, unit, sig=accuracy)
        else:
            return u"%s" % value
    except Exception:
        logging.warning("Failed to convert value to string", exc_info=True)
        return u"%s" % value

