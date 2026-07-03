# -*- coding: utf-8 -*-

"""
@author: Rinze de Laat, Éric Piel, Philip Winkler, Victoria Mavrikopoulou,
         Anders Muskens, Bassim Lazem

Copyright © 2012-2022 Rinze de Laat, Éric Piel, Delmic

Handles the switch of the content of the main GUI tabs.

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""
from typing import Dict
from odemis import model

MIRROR_AXES_LS = {"l", "s"}
MIRROR_ONPOS_RADIUS = 2e-3  # m, distance from a position that is still considered that position

# Different states of the mirror stage positions
MIRROR_NOT_REFD = 0
MIRROR_PARKED = 1
MIRROR_BAD = 2  # not parked, but not fully engaged either
MIRROR_ENGAGED = 3


def get_mirror_pos_parked(mirror: model.HwComponent) -> Dict[str, float]:
    """
    Return the position dict corresponding to the parked position of the given mirror actuator.
    If MD_FAV_POS_DEACTIVE metadata is defined for the mirror, it is used. Any axes not present
    in the metadata default to 0. If the metadata is not defined at all, all axes default to 0.
    :param mirror: the mirror component (must have .axes)
    :return: parked position as a dict of axis name -> position (m), with one entry per axis
    """
    pos_parked = mirror.getMetadata().get(model.MD_FAV_POS_DEACTIVE, None)
    if pos_parked is not None:
        return {a: pos_parked.get(a, 0) for a in mirror.axes}
    return {a: 0 for a in mirror.axes}
