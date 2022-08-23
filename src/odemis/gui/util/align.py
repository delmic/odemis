# -*- coding: utf-8 -*-
"""
Created on 14 Jan 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
"""

# Various helper functions and classes for the lens alignment
TOP_LEFT = 0
TOP_RIGHT = 1
BOTTOM_LEFT = 2
BOTTOM_RIGHT = 3


def dichotomy_to_region(seq):
    """
    Converts a dichotomy sequence into a region
    See DichotomyOverlay for more information
    seq (list of 0<=int<4): list of sub part selected
    returns (tuple of 4 0<=float<=1): left, top, right, bottom (in ratio)
    """
    roi = (0, 0, 1, 1)  # starts from the whole area
    for quad in seq:
        l, t, r, b = roi
        # divide the roi according to the quadrant
        if quad in (TOP_LEFT, BOTTOM_LEFT):
            r = l + (r - l) / 2
        else:
            l = (r + l) / 2
        if quad in (TOP_LEFT, TOP_RIGHT):
            b = t + (b - t) / 2
        else:
            t = (b + t) / 2
        assert(0 <= l <= r <= 1 and 0 <= t <= b <= 1)
        roi = (l, t, r, b)

    return roi

