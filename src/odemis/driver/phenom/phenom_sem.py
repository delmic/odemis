  # -*- coding: utf-8 -*-
'''
Created on 30 April 2014

@author: Kimon Tsitsikas

Copyright © 2014 Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms 
of the GNU General Public License version 2 as published by the Free Software 
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; 
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR 
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with 
Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

from suds.client import Client
import Image
import base64
import urllib2
import os
import logging
import math
import numpy
from odemis import model, util
from odemis.dataio import hdf5
from odemis.util import img
from odemis.model import isasync
import os.path
import threading
import time
from random import randint
import weakref
import time
import Image
import re

class PhenomSEM(model.HwComponent):
    '''
    This is an extension of the model.HwComponent class. It instantiates the scanner 
    and se-detector children components and provides an update function for its 
    metadata. 
    '''

    def __init__(self, name, role, children, daemon=None, **kwargs):
        '''
        children (dict string->kwargs): parameters setting for the children.
            Known children are "scanner" and "detector"
            They will be provided back in the .children roattribute
        Raise an exception if the device cannot be opened
        '''
        # we will fill the set of children with Components later in ._children
        model.HwComponent.__init__(self, name, role, daemon=daemon, **kwargs)

        # you can change the 'localhost' string and provide another SEM addres
        client = Client("http://localhost:8888?om", location="http://localhost:8888", username="SummitTAD", password="SummitTADSummitTAD")
        self._device = client.service

        # we can read some parameter
        print('wd: ', self._device.GetSEMWD())

        # tuple is returned if the function has more output arguments
        print('stage position: ', self._device.GetStageModeAndPosition())

        self._imagingDevice = client.factory.create('ns0:imagingDevice')

        self._metadata = {model.MD_HW_NAME: "PhenomSEM"}

        # create the scanner child
        try:
            kwargs = children["scanner"]
        except (KeyError, TypeError):
            raise KeyError("PhenomSEM was not given a 'scanner' child")
        self._scanner = Scanner(parent=self, daemon=daemon, **kwargs)
        self.children.add(self._scanner)

        # create the detector child
        try:
            kwargs = children["detector0"]
        except (KeyError, TypeError):
            raise KeyError("PhenomSEM was not given a 'detector' child")
        self._detector = Detector(parent=self, daemon=daemon, **kwargs)
        self.children.add(self._detector)

#         # create the stage child
#         try:
#             kwargs = children["stage"]
#         except (KeyError, TypeError):
#             raise KeyError("PhenomSEM was not given a 'stage' child")
#         self._stage = Stage(parent=self, daemon=daemon, **kwargs)
#         self.children.add(self._stage)
#
#         # create the focus child
#         try:
#             kwargs = children["focus"]
#         except (KeyError, TypeError):
#             raise KeyError("PhenomSEM was not given a 'focus' child")
#         self._focus = EbeamFocus(parent=self, daemon=daemon, **kwargs)
#         self.children.add(self._focus)
#
#         # create the camera child
#         try:
#             kwargs = children["camera"]
#         except (KeyError, TypeError):
#             raise KeyError("PhenomSEM was not given a 'camera' child")
#         self._camera = ChamberView(parent=self, daemon=daemon, **kwargs)
#         self.children.add(self._camera)
#
#         # create the pressure child
#         try:
#             kwargs = children["pressure"]
#         except (KeyError, TypeError):
#             raise KeyError("PhenomSEM was not given a 'pressure' child")
#         self._pressure = ChamberPressure(parent=self, daemon=daemon, **kwargs)
#         self.children.add(self._pressure)

    def updateMetadata(self, md):
        self._metadata.update(md)

    def terminate(self):
        """
        Must be called at the end of the usage. Can be called multiple times,
        but the component shouldn't be used afterward.
        """
        # finish
        pass

class Scanner(model.Emitter):
    """
    This is an extension of the model.Emitter class. It contains Vigilant 
    Attributes and setters for magnification, pixel size, translation, resolution,
    scale, rotation and dwell time. Whenever one of these attributes is changed, 
    its setter also updates another value if needed e.g. when scale is changed, 
    resolution is updated, when resolution is changed, the translation is recentered 
    etc. Similarly it subscribes to the VAs of scale and magnification in order 
    to update the pixel size.
    """
    def __init__(self, name, role, parent, **kwargs):
        # It will set up ._shape and .parent
        model.Emitter.__init__(self, name, role, parent=parent, **kwargs)

        self._shape = (2048, 2048)

        # next two values are just to determine the pixel size
        # Distance between borders if magnification = 1. It should be found out
        # via calibration. We assume that image is square, i.e., VFW = HFW
        self._hfw_nomag = 0.00183908649384  # m

        # Allow the user to modify the value, to copy it from the SEM software
        mag = 1e3  # pretty random value which could be real
        # Field of view in Phenom is set in m
        self.parent._device.SetSEMHFW(self._hfw_nomag / mag)
        self.magnification = model.FloatContinuous(mag, range=[1, 1e9], unit="")
        self.magnification.subscribe(self._onMagnification)

        # pixelSize is the same as MD_PIXEL_SIZE, with scale == 1
        # == smallest size/ between two different ebeam positions
        pxs = (self._hfw_nomag / (self._shape[0] * mag),
               self._hfw_nomag / (self._shape[1] * mag))
        self.pixelSize = model.VigilantAttribute(pxs, unit="m", readonly=True)

        # (.resolution), .translation, .rotation, and .scaling are used to
        # define the conversion from coordinates to a region of interest.

        # (float, float) in px => moves center of acquisition by this amount
        # independent of scale and rotation.
        tran_rng = [(-self._shape[0] / 2, -self._shape[1] / 2),
                    (self._shape[0] / 2, self._shape[1] / 2)]
        self.translation = model.TupleContinuous((0, 0), tran_rng,
                                              cls=(int, long, float), unit="",
                                              setter=self._setTranslation)

        # .resolution is the number of pixels actually scanned. If it's less than
        # the whole possible area, it's centered.
        resolution = (self._shape[0] // 8, self._shape[1] // 8)
        self.resolution = model.ResolutionVA(resolution, [(1, 1), self._shape],
                                             setter=self._setResolution)
        self._resolution = resolution

        # (float, float) as a ratio => how big is a pixel, compared to pixelSize
        # it basically works the same as binning, but can be float
        # (Default to scan the whole area)
        self._scale = (self._shape[0] / resolution[0], self._shape[1] / resolution[1])
        self.scale = model.TupleContinuous(self._scale, [(1, 1), self._shape],
                                           cls=(int, long, float),
                                           unit="", setter=self._setScale)
        self.scale.subscribe(self._onScale, init=True)  # to update metadata

        # (float) in rad => rotation of the image compared to the original axes
        # TODO: for now it's readonly because no rotation is supported
        self.rotation = model.FloatContinuous(0, [0, 2 * math.pi], unit="rad",
                                              readonly=True)

        self.dwellTime = model.FloatContinuous(1e-06, (1e-06, 1000), unit="s")
        self.dwellTime.subscribe(self._onDwellTime)

        # Range is according to min and max voltages accepted by Tescan API
        volt = self.parent._device.HVGetVoltage()
        self.accelVoltage = model.FloatContinuous(volt, (200, 35000), unit="V")
        self.accelVoltage.subscribe(self._onVoltage)

        # 0 turns off the e-beam, 1 turns it on
        power_choices = set([0, 1])
        self._power = max(power_choices)  # Just in case more choises are added
        self.parent._device.HVBeamOn()
        self.power = model.IntEnumerated(self._power, power_choices, unit="",
                                  setter=self.setPower)

        # Blanker is automatically enabled when no scanning takes place
        # TODO it may cause time overhead, check on testing
        # self.parent._device.ScSetBlanker(0, 2)

        # Enumerated float with respect to the PC indexes of Tescan API
        pc_choices = set(self.GetProbeCurrents())
        self._list_currents = sorted(pc_choices, reverse=True)
        self._probeCurrent = min(pc_choices)
        self.probeCurrent = model.FloatEnumerated(self._probeCurrent, pc_choices, unit="A",
                                  setter=self.setPC)


    def updateMetadata(self, md):
        # we share metadata with our parent
        self.parent.updateMetadata(md)

    def _onMagnification(self, mag):
        # HFW to mm to comply with Tescan API
        self.parent._device.SetViewField(self._hfw_nomag * 1e03 / mag)
        self._updatePixelSize()

    def _onDwellTime(self, dt):
        # TODO interrupt current scanning when dwell time is changed
        # ScStopScan does not work this way
        pass

    def _onVoltage(self, volt):
        self.parent._device.HVSetVoltage(volt)

    def setPower(self, value):
        powers = self.power.choices

        self._power = util.find_closest(value, powers)
        if self._power == 0:
            self.parent._device.HVBeamOff()
        else:
            self.parent._device.HVBeamOn()
        return self._power

    def setPC(self, value):
        currents = self.probeCurrent.choices

        self._probeCurrent = util.find_closest(value, currents)
        self._indexCurrent = util.index_closest(value, self._list_currents)

        # Set the corresponding current index to Tescan SEM
        self.parent._device.SetPCContinual(self._indexCurrent + 1)

        return self._probeCurrent

    def GetProbeCurrents(self):
        """
        return (list of float): probe current values ordered by index
        """
        currents = []
        pcs = self.parent._device.EnumPCIndexes()
        cur = re.findall(r'\=(.*?)\n', pcs)
        for i in enumerate(cur):
            # picoamps to amps
            currents.append(float(i[1]) * 1e-12)
        return currents

    def _onScale(self, s):
        self._updatePixelSize()

    def _updatePixelSize(self):
        """
        Update the pixel size using the scale, HFWNoMag and magnification
        """
        mag = self.magnification.value
        self.parent._metadata[model.MD_LENS_MAG] = mag

        pxs = (self._hfw_nomag / (self._shape[0] * mag),
               self._hfw_nomag / (self._shape[1] * mag))

        # it's read-only, so we change it only via _value
        self.pixelSize._value = pxs
        self.pixelSize.notify(pxs)

        # If scaled up, the pixels are bigger
        pxs_scaled = (pxs[0] * self.scale.value[0], pxs[1] * self.scale.value[1])
        self.parent._metadata[model.MD_PIXEL_SIZE] = pxs_scaled

    def _setScale(self, value):
        """
        value (1 < float, 1 < float): increase of size between pixels compared to
         the original pixel size. It will adapt the translation and resolution to
         have the same ROI (just different amount of pixels scanned)
        return the actual value used
        """
        prev_scale = self._scale
        self._scale = value

        # adapt resolution so that the ROI stays the same
        change = (prev_scale[0] / self._scale[0],
                  prev_scale[1] / self._scale[1])
        old_resolution = self.resolution.value
        new_resolution = (max(int(round(old_resolution[0] * change[0])), 1),
                          max(int(round(old_resolution[1] * change[1])), 1))
        # no need to update translation, as it's independent of scale and will
        # be checked by setting the resolution.
        self.resolution.value = new_resolution  # will call _setResolution()

        return value

    def _setResolution(self, value):
        """
        value (0<int, 0<int): defines the size of the resolution. If the 
         resolution is not possible, it will pick the most fitting one. It will
         recenter the translation if otherwise it would be out of the whole
         scanned area.
        returns the actual value used
        """
        max_size = (int(self._shape[0] // self._scale[0]),
                    int(self._shape[1] // self._scale[1]))

        # at least one pixel, and at most the whole area
        size = (max(min(value[0], max_size[0]), 1),
                max(min(value[1], max_size[1]), 1))
        self._resolution = size

        # setting the same value means it will recheck the boundaries with the
        # new resolution, and reduce the distance to the center if necessary.
        self.translation.value = self.translation.value
        return size

    def _setTranslation(self, value):
        """
        value (float, float): shift from the center. It will always ensure that
          the whole ROI fits the screen.
        returns actual shift accepted
        """
        # compute the min/max of the shift. It's the same as the margin between
        # the centered ROI and the border, taking into account the scaling.
        max_tran = ((self._shape[0] - self._resolution[0] * self._scale[0]) / 2,
                    (self._shape[1] - self._resolution[1] * self._scale[1]) / 2)

        # between -margin and +margin
        tran = (max(min(value[0], max_tran[0]), -max_tran[0]),
                max(min(value[1], max_tran[1]), -max_tran[1]))
        return tran

    def pixelToPhy(self, px_pos):
        """
        Converts a position in pixels to physical (at the current magnification)
        Note: the convention is that in internal coordinates Y goes down, while
        in physical coordinates, Y goes up.
        px_pos (tuple of 2 floats): position in internal coordinates (pixels)
        returns (tuple of 2 floats): physical position in meters 
        """
        pxs = self.pixelSize.value  # m/px
        phy_pos = (px_pos[0] * pxs[0], -px_pos[1] * pxs[1])  # - to invert Y
        return phy_pos
