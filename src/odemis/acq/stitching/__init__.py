# -*- coding: utf-8 -*-
'''
Created on 19 Jul 2017

@author: Éric Piel, Philip Winkler

Copyright © 2017 Éric Piel, Philip Winkler, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from odemis.acq.stitching._constants import REGISTER_GLOBAL_SHIFT, REGISTER_SHIFT, \
    REGISTER_IDENTITY, WEAVER_MEAN, WEAVER_COLLAGE, WEAVER_COLLAGE_REVERSE
from odemis.acq.stitching._tiledacq import acquireTiledArea, estimateTiledAcquisitionTime, estimateTiledAcquisitionMemory, FocusingMethod
from odemis.acq.stitching._registrar import *
from odemis.acq.stitching._weaver import *
from odemis.acq.stitching._simple import register, weave
