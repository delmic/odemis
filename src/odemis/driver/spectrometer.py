# -*- coding: utf-8 -*-
'''
Created on 6 Mar 2013

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

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
from odemis import model
from odemis.model import ComponentBase, DataFlowBase
import logging
import math

# This is a class that represents a spectrometer (ie, a detector to acquire
# a spectrum) by wrapping a DigitalCamera and a spectrograph (ie, actuator which
# offers a wavelength dimension).

NON_SPEC_MD = {model.MD_AR_POLE, model.MD_AR_FOCUS_DISTANCE, model.MD_AR_PARABOLA_F,
               model.MD_AR_XMAX, model.MD_AR_HOLE_DIAMETER, model.MD_ROTATION,
               model.MD_ROTATION_COR, model.MD_SHEAR, model.MD_SHEAR_COR}

class CompositedSpectrometer(model.Detector):
    '''
    A generic Detector which takes 2 children to create a spectrometer. It's
    essentially a wrapper to a DigitalCamera to generate a spectrum as data
    from the DataFlow. Manipulation of the mirrors/gratings/prism must be done
    via the "spectrograph" child. On the contrary, access to the detector must
    be done only via this Component, and never directly on the "detector" child.

    The main differences between a Spectrometer and a normal DigitalCamera are:
     * the spectrometer data of the DataFlow has only one dimension (i.e., second
       dimension is fixed to 1)
     * the shape is the same as one of the DigitalCamera, but the second dim of
       max resolution is 1.
     * the maximum binning can be bigger than the maximum resolution (but not
       of the shape).
     * the metadata has an additional entry MD_WL_LIST which indicates the
       wavelength associated to each pixel.
    '''

    def __init__(self, name, role, children, **kwargs):
        '''
        children (dict string->model.HwComponent): the children
            There must be exactly two children "spectrograph" and "detector". The
            first dimension of the CCD is supposed to be along the wavelength,
            with the first pixels representing the lowest wavelengths.
        Raise:
          ValueError: if the children are not compatible
        '''
        # we will fill the set of children with Components later in ._children
        model.Detector.__init__(self, name, role, **kwargs)

        # Check the children
        dt = children["detector"]
        if not isinstance(dt, ComponentBase):
            raise ValueError("Child detector is not a component.")
        if ((not hasattr(dt, "shape") or not isinstance(dt.shape, tuple)) or
            not model.hasVA(dt, "pixelSize")):
            raise ValueError("Child detector is not a Detector component.")
        if not hasattr(dt, "data") or not isinstance(dt.data, DataFlowBase):
            raise ValueError("Child detector has not .data DataFlow.")
        self._detector = dt
        self.children.value.add(dt)

        sp = children["spectrograph"]
        if not isinstance(sp, ComponentBase):
            raise ValueError("Child spectrograph is not a component.")
        try:
            if "wavelength" not in sp.axes:
                raise ValueError("Child spectrograph has no 'wavelength' axis.")
        except Exception:
            raise ValueError("Child spectrograph is not an Actuator.")
        self._spectrograph = sp
        self.children.value.add(sp)

        # set up the detector part
        # check that the shape is "horizontal"
        if dt.shape[0] <= 1:
            raise ValueError("Child detector must have at least 2 pixels horizontally")
        if dt.shape[0] < dt.shape[1]:
            logging.warning("Child detector is shaped vertically (%dx%d), "
                            "this is probably incorrect, as wavelengths are "
                            "expected to be along the horizontal axis",
                            dt.shape[0], dt.shape[1])
        # shape is same as detector (raw sensor), but the max resolution is always flat
        self._shape = tuple(dt.shape) # duplicate

        # Wrapper for the dataflow
        self.data = SpecDataFlow(self, dt.data)

        # The resolution and binning are derived from the detector, but with
        # settings set so that there is only one horizontal line.

        # TODO: give a init parameter or VA to specify a smaller window height
        # than the entire CCD (some spectrometers have only noise on the top and
        # bottom)
        if dt.binning.range[1][1] < dt.resolution.range[1][1]:
            # without software binning, we are stuck to the max binning
            # TODO: support software binning by rolling up our own dataflow that
            # does data merging
            logging.info("Spectrometer %s will only use a %d px band of the %d "
                         "px of the sensor", name, dt.binning.range[1][1],
                         dt.resolution.range[1][1])

        assert dt.resolution.range[0][1] == 1
        resolution = (dt.resolution.range[1][0], 1)  # max,1
        min_res = (dt.resolution.range[0][0], 1)
        max_res = (dt.resolution.range[1][0], 1)
        self.resolution = model.ResolutionVA(resolution, (min_res, max_res),
                                             setter=self._setResolution)
        # 2D binning is like a "small resolution"
        # Initial binning is minimum binning horizontally, and maximum vertically
        self._binning = (1, min(dt.binning.range[1][1], dt.resolution.range[1][1]))
        self.binning = model.ResolutionVA(self._binning, dt.binning.range,
                                          setter=self._setBinning)

        self._setBinning(self._binning) # will also update the resolution

        # TODO: also wrap translation, if it exists?

        # duplicate every other VA and Event from the detector
        # that includes required VAs like .pixelSize and .exposureTime
        for aname, value in model.getVAs(dt).items() + model.getEvents(dt).items():
            if not hasattr(self, aname):
                setattr(self, aname, value)
            else:
                logging.debug("skipping duplication of already existing VA '%s'", aname)

        assert hasattr(self, "pixelSize")
        if not model.hasVA(self, "exposureTime"):
            logging.warning("Spectrometer %s has no exposureTime VA", name)

        sp.position.subscribe(self._onPositionUpdate)
        self.resolution.subscribe(self._onResBinning)
        self.binning.subscribe(self._onResBinning)
        self._updateWavelengthList()

    # The metadata is an overlay of our special metadata with the standard one
    # from the CCD
    def getMetadata(self):
        md = self._detector.getMetadata().copy()
        md.update(self._metadata)
        return md

    def _onPositionUpdate(self, pos):
        """
        Called when the wavelength position or grating (ie, groove density)
          of the spectrograph is changed.
        """
        self._updateWavelengthList()

    def _onResBinning(self, value):
        self._updateWavelengthList()

    def _updateWavelengthList(self):
        npixels = self.resolution.value[0]
        pxs = self.pixelSize.value[0] * self.binning.value[0]
        wll = self._spectrograph.getPixelToWavelength(npixels, pxs)
        md = {model.MD_WL_LIST: wll}
        self.updateMetadata(md)

    def _setBinning(self, value):
        """
        Called when "binning" VA is modified. It also updates the resolution so
        that the horizontal AOI is approximately the same. The vertical size
        stays 1.
        value (int): how many pixels horizontally and vertically
          are combined to create "super pixels"
        """
        # Everything accepted by the VA should be acceptable
        prev_binning = self._binning
        self._binning = tuple(value) # duplicate

        if self.data.active:
            self._applyBinning(value)

        # adapt horizontal resolution so that the AOI stays the same
        changeh = prev_binning[0] / self._binning[0]
        old_resolution = self.resolution.value
        assert old_resolution[1] == 1
        new_resh = int(round(old_resolution[0] * changeh))
        new_resh = max(min(new_resh, self.resolution.range[1][0]), self.resolution.range[0][0])
        new_resolution = (new_resh, 1)

        # setting resolution and binning is slightly tricky, because binning
        # will change resolution to keep the same area. So first set binning, then
        # resolution
        self.resolution.value = new_resolution
        return value

    def _setResolution(self, value):
        """
        Called when the resolution VA is to be updated.
        """
        # only the width might change
        assert value[1] == 1

        # fit the width to the maximum possible given the binning
        max_size = int(self.resolution.range[1][0] // self._binning[0])
        min_size = int(math.ceil(self.resolution.range[0][0] / self._binning[0]))
        size = (max(min(value[0], max_size), min_size), 1)

        if self.data.active:
            self._applyResolution(size)

        return size

    def _applyBinning(self, b):
        self._detector.binning.value = b
        if self._detector.binning.value != b:
            logging.error("Hw binning didn't follow requested binning %s", b)

    def _applyResolution(self, res):
        self._detector.resolution.value = res
        assert self._detector.resolution.value[1] == 1

    def _applyCCDSettings(self):
        self._applyBinning(self.binning.value)
        self._applyResolution(self.resolution.value)

    def selfTest(self):
        return self._detector.selfTest() and self._spectrograph.selfTest()


class SpecDataFlow(model.DataFlow):
    def __init__(self, comp, ccddf):
        """
        comp: the spectrometer instance
        ccddf (DataFlow): the dataflow of the real CCD
        """
        model.DataFlow.__init__(self)
        self.component = comp
        self._ccddf = ccddf
        self.active = False
        # Metadata is a little tricky because it must be synchronised with the
        # actual acquisition, but it's difficult to know with which settings
        # the acquisition was taken (when the settings are changing while
        # generating).
        self._beg_metadata = {}  # Metadata (more or less) at the beginning of the acquisition

    def start_generate(self):
        logging.debug("Activating Spectrometer acquisition")
        self.active = True
        self.component._applyCCDSettings()
        self._beg_metadata = self.component._metadata.copy()
        self._ccddf.subscribe(self._newFrame)

    def stop_generate(self):
        self._ccddf.unsubscribe(self._newFrame)
        self.active = False
        logging.debug("Spectrometer acquisition finished")
        # TODO: tell the component that it's over?

    def synchronizedOn(self, event):
        self._ccddf.synchronizedOn(event)

    def _newFrame(self, df, data):
        """
        Get the new frame from the detector
        """
        if data.shape[0] != 1:
            logging.warning("Shape of spectrometer data is %s, while first dim should be 1", data.shape)
            # TODO: do software binning (= sum/mean over the first axis)

        # Check the metadata seems correct, and if not, recompute it on-the-fly
        md = self._beg_metadata
        if model.MD_WL_LIST in md:
            # TODO: the current metadata should contain the pxs/res/wl/grating it corresponds to
            if len(md[model.MD_WL_LIST]) != data.shape[1]:
                dmd = data.metadata
                logging.debug("WL_LIST len = %d vs %d", len(md[model.MD_WL_LIST]), data.shape[1])
                try:
                    npixels = data.shape[1]
                    pxs = dmd[model.MD_SENSOR_PIXEL_SIZE][0] * dmd[model.MD_BINNING][0]
                    logging.info("Recomputing correct WL_LIST metadata")
                    wll = self.component._spectrograph.getPixelToWavelength(npixels, pxs)
                    md[model.MD_WL_LIST] = wll
                except KeyError:
                    logging.warning("Failed to compute correct WL_LIST metadata", exc_info=True)

        # Remove non useful metadata
        for k in NON_SPEC_MD:
            data.metadata.pop(k, None)

        data.metadata.update(md)
        udata = self.component._transposeDAToUser(data)
        model.DataFlow.notify(self, udata)

        # If the acquisition continues, it will likely be using the current settings
        self._beg_metadata = self.component._metadata.copy()

