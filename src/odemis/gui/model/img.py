# -*- coding: utf-8 -*-
"""
Created on 22 Feb 2013

@author: Rinze de Laat

Copyright © 2013 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms  of the GNU General Public License version 2 as published by the Free
Software  Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY;  without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR  PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.


### Purpose ###

This module contains classes that describe images produced by hardware
components.

"""

class InstrumentalImage(object):
    """ A light wrapper around a wx.Image and meta data about where it is taken.
    """

    # It'd be best to have it as a subclass of wx.Image, but wxPython has many
    # functions which return a wx.Image. We'd need to "override" them as well.

    def __init__(self, wx_img, mpp=None, center=None, rotation=0.0):
        """
        wx_img (None or wx.Image)
        mpp (None or float>0): meters per pixel
        center (None or 2-tuple float): position (in meters) of the center of
            the image
        rotation (float): rotation in degrees (i.e., 180 = upside-down)

        Note: When displayed, the scaling, translation, and rotation have to be
        applied "independently": scaling doesn't affect the translation, and
        rotation is applied from the center of the image.
        """
        self.image = wx_img
        # TODO: should be a tuple (x/y) to support images like acquisition from
        # SPARC
        assert(mpp is None or (mpp > 0))
        self.mpp = mpp
        assert(center is None or (len(center) == 2))
        self.center = center
        self.rotation = rotation

    def get_pixel_size(self):
        if self.image:
            return self.image.GetSize()

    def get_phy_size(self):
        return tuple([d * self.mpp for d in self.get_pixel_size()])

    def get_phy_surface(self):
        x, y = self.get_phy_size()
        return x * y


