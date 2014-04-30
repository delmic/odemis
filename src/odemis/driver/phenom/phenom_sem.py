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
        self._device = Client("http://localhost:8888?om", location="http://localhost:8888", username="SummitTAD", password="SummitTADSummitTAD")

        # we can read some parameter
        print('wd: ', self._device.GetSEMWD())

        # tuple is returned if the function has more output arguments
        print('stage position: ', self._device.StgGetPosition())

        # let us take a look at the detector configuration
        print(self._device.DtEnumDetectors())

        # set the Probe Current - this is equivalent to BI in SEM Generation 3
        self._device.SetPCIndex(10)

        # important: stop the scanning before we start scanning or before automatic procedures,
        # even before we configure the detectors
        self._device.ScStopScan()

        self._metadata = {model.MD_HW_NAME: "PhenomSEM"}

        # create the scanner child
        try:
            kwargs = children["scanner"]
        except (KeyError, TypeError):
            raise KeyError("TescanSEM was not given a 'scanner' child")

        self._scanner = Scanner(parent=self, daemon=daemon, **kwargs)
        self.children.add(self._scanner)

        # create the detector child
        try:
            kwargs = children["detector0"]
        except (KeyError, TypeError):
            raise KeyError("TescanSEM was not given a 'detector' child")
        self._detector = Detector(parent=self, daemon=daemon, **kwargs)
        self.children.add(self._detector)

        # create the stage child
        try:
            kwargs = children["stage"]
        except (KeyError, TypeError):
            raise KeyError("TescanSEM was not given a 'stage' child")

        self._stage = Stage(parent=self, daemon=daemon, **kwargs)
        self.children.add(self._stage)

        # create the focus child
        try:
            kwargs = children["focus"]
        except (KeyError, TypeError):
            raise KeyError("TescanSEM was not given a 'focus' child")
        self._focus = EbeamFocus(parent=self, daemon=daemon, **kwargs)
        self.children.add(self._focus)

        # create the camera child
        try:
            kwargs = children["camera"]
        except (KeyError, TypeError):
            raise KeyError("TescanSEM was not given a 'camera' child")
        self._camera = ChamberView(parent=self, daemon=daemon, **kwargs)
        self.children.add(self._camera)

        # create the pressure child
        try:
            kwargs = children["pressure"]
        except (KeyError, TypeError):
            raise KeyError("TescanSEM was not given a 'pressure' child")
        self._pressure = ChamberPressure(parent=self, daemon=daemon, **kwargs)
        self.children.add(self._pressure)

    def updateMetadata(self, md):
        self._metadata.update(md)

    def terminate(self):
        """
        Must be called at the end of the usage. Can be called multiple times,
        but the component shouldn't be used afterward.
        """
        # finish
        self._device.Disconnect()
