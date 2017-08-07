# -*- coding: utf-8 -*-
'''
Created on 19 Jul 2017

@author: Éric Piel

Copyright © 2017 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division


# This is a series of classes which use different methods to compute the "best
# location" of an image set based on their metadata and content. IOW, it does
# "image registration".

# TODO: define a generic API: can take a set of images, with some being explicitly
# linked/locked in position, and allow to add new images on the set (to run it
# in live mode).

# TODO: simple version which returns the data as-is

# TODO: use cross-correlation (cf work already done) + global optimisation

