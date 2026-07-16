# -*- coding: utf-8 -*-
"""
Created on 16 Jul 2026

@author: Éric Piel

Copyright © 2026 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

General constants used across the odemis model.
"""

__all__ = ["UNDEFINED_ROI"]

# Sentinel value for a Region Of Interest (ROI) that has not yet been defined
# by the user. The ROI is expressed as (xmin, ymin, xmax, ymax) in relative
# coordinates (0 to 1). This sentinel uses (0, 0, 0, 0), which is an empty
# region and therefore not a valid ROI.
UNDEFINED_ROI = (0, 0, 0, 0)
