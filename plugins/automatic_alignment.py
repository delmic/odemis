# -*- coding: utf-8 -*-
'''
Created on 10 April 2017

@author: Guilherme Stiebler

Gives ability to automatically change the overlay-metadata.

Copyright © 2017 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
'''

from __future__ import division

from collections import OrderedDict
import functools
import logging
import math
from odemis import dataio, model
from odemis.acq import stream
from odemis.gui.plugin import Plugin, AcquisitionDialog
from odemis.util.dataio import data_to_static_streams, open_acquisition
from odemis.acq.align import keypoint
from odemis.util.conversion import get_img_transformation_md
import odemis.gui.util as guiutil
from odemis.gui.conf import get_acqui_conf
from scipy import ndimage
import os
import wx
import cv2
from odemis.gui.util import call_in_wx_main
from odemis.util import img
import numpy

class AlignmentAcquisitionDialog(AcquisitionDialog):

    @call_in_wx_main
    def addStream(self, stream, index):
        """
        Adds a stream to the canvas, and a stream entry to the stream panel.
        It also ensures the panel box and canvas are shown.
        """
        new_stream_l = index == 0 and not self.viewport_l.IsShown()
        new_stream_r = index == 1 and not self.viewport_r.IsShown()

        if index == 0:
            self.viewport_l.Show()
        else:
            self.viewport_r.Show()

        if new_stream_l or new_stream_r:
            self.Layout()
            self.Fit()
            self.Update()

        if stream:
            if index == 0:
                self.microscope_view.addStream(stream)
            else:
                self.microscope_view_r.addStream(stream)

def preprocess(img, invert, flip, crop, gaussian_sigma, eqhis):
    '''
    The pre-processing function
    img (DataArray): Input image
    invert (bool): Invert the brightness levels of the image
    flip (tuple(bool, bool)): Determine if the image should be flipped on the X and Y axis
    crop (tuple(t,b,l,r): Crop values in pixels
    gaussian_sigma (int): Blur intensity
    eqhis (bool): Determine if an histogram equalization should be executed
    return: Processed image
    '''
    metadata = img.metadata

    flip_x, flip_y = flip
    # flip on X axis
    if flip_x:
        img = img[:, ::-1]

    # flip on Y axis
    if flip_y:
        img = img[::-1, :]

    crop_top, crop_bottom, crop_left, crop_right = crop
    # remove the bar
    img = img[crop_top:img.shape[0] - crop_bottom, crop_left:img.shape[1] - crop_right]

    # Invert the image brightness
    if invert:
        mn = img.min()
        mx = img.max()
        img = mx + mn - img

    # equalize histogram
    if eqhis:
        if img.dtype == numpy.uint16:
            img = cv2.convertScaleAbs(img, alpha=(255.0/65535.0))
        img = cv2.equalizeHist(img)

    # blur the image using a gaussian filter
    img = ndimage.gaussian_filter(img, sigma=gaussian_sigma)

    # return a new DataArray with the metadata of the original image
    return  model.DataArray(img, metadata)


class AlignmentProjection(stream.RGBSpatialProjection):

    def __init__(self, in_stream):
        super(AlignmentProjection, self).__init__(in_stream)
        self._invert = False
        self._flip = (False, False)
        self._crop = (0, 0, 0, 0)
        self._gaussian_sigma = 0
        self._eqhis = False

    def setPreprocessingParams(self, invert, flip, crop, gaussian_sigma, eqhis):
        ''' Sets the parameters for the preprocessing function called on ._updateImage
        invert (bool): Invert the brightness levels of the image
        flip (tuple(bool, bool)): Determine if the image should be flipped on the X and Y axis
        crop (tuple(t,b,l,r): Crop values in pixels
        gaussian_sigma (int): Blur intensity
        eqhis (bool): Determine if an histogram equalization should be executed
        '''
        self._invert = invert
        self._flip = flip
        self._crop = crop
        self._gaussian_sigma = gaussian_sigma
        self._eqhis = eqhis

    def _updateImage(self):
        raw = self.stream.raw[0]
        metadata = raw.metadata
        grayscale_im = preprocess(raw, self._invert, self._flip, self._crop,
            self._gaussian_sigma, self._eqhis)
        rgb_im = cv2.cvtColor(grayscale_im, cv2.COLOR_GRAY2RGB)
        rgb_im = model.DataArray(rgb_im, metadata)
        self.image.value = rgb_im

class AutomaticOverlayPlugin(Plugin):
    name = "Automatic Overlay"
    __version__ = "1.0"
    __author__ = "Guilherme Stiebler"
    __license__ = "GPLv2"

    # Describe how the values should be displayed
    # See odemis.gui.conf.data for all the possibilities
    vaconf = OrderedDict((
        ("blur", {
            "label": "Blur window size"
        }),
        ("crop_top", {
            "label": "Crop top"
        }),
        ("crop_bottom", {
            "label": "Crop bottom"
        }),
        ("crop_left", {
            "label": "Crop left"
        }),
        ("crop_right", {
            "label": "Crop right"
        }),
        ("invert", {
            "label": "Invert brightness"
        }),
        ("flip_x", {
            "label": "Flip on X axis"
        }),
        ("flip_y", {
            "label": "Flip on Y axis"
        }),
    ))

    def __init__(self, microscope, main_app):
        super(AutomaticOverlayPlugin, self).__init__(microscope, main_app)
        self.addMenu("Overlay/Add & Align to SEM...", self.start)

        self.blur = model.IntContinuous(10, range=(0, 20), unit="pixels")
        # TODO set the limits of the crop VAs based on the size of the image
        self.crop_top = model.IntContinuous(0, range=(0, 100), unit="pixels")
        self.crop_bottom = model.IntContinuous(0, range=(0, 100), unit="pixels")
        self.crop_left = model.IntContinuous(0, range=(0, 100), unit="pixels")
        self.crop_right = model.IntContinuous(0, range=(0, 100), unit="pixels")
        self.invert = model.BooleanVA(True)
        self.flip_x = model.BooleanVA(False)
        self.flip_y = model.BooleanVA(True)

        # Any change on the VAs should update the stream
        self.blur.subscribe(self._update_stream)
        self.crop_top.subscribe(self._update_stream)
        self.crop_bottom.subscribe(self._update_stream)
        self.crop_left.subscribe(self._update_stream)
        self.crop_right.subscribe(self._update_stream)
        self.invert.subscribe(self._update_stream)
        self.flip_x.subscribe(self._update_stream)
        self.flip_y.subscribe(self._update_stream)


    def start(self):
        dlg = AlignmentAcquisitionDialog(self, "Automatically change the alignment",
                                text="Automatically change the alignment")

        # removing the play overlay from the viewports
        dlg.viewport_l.canvas.remove_view_overlay(dlg.viewport_l.canvas.play_overlay)
        dlg.viewport_r.canvas.remove_view_overlay(dlg.viewport_r.canvas.play_overlay)

        sem_stream = self._get_sem_stream()
        sem_projection = AlignmentProjection(sem_stream)
        crop = (self.crop_top.value, self.crop_bottom.value,\
                self.crop_left.value, self.crop_right.value)
        sem_projection.setPreprocessingParams(False, (False, False), (0, 0, 0, 0), 5, True)
        self._semStream = sem_projection
        dlg.addStream(sem_projection, 1)

        dlg.addSettings(self, self.vaconf)
        dlg.addButton("Align", self.align, face_colour='blue')
        dlg.addButton("Cancel", None)
        dlg.pnl_gauge.Hide()
        self.open_image(dlg)
        dlg.ShowModal()

    def _get_sem_stream(self):
        """
        Finds the SEM stream in the acquisition tab
        return (SEMStream or None): None if not found
        """
        tab_data = self.main_app.main_data.tab.value.tab_data_model
        for s in tab_data.streams.value:
            if isinstance(s, stream.EMStream):
                return s

        logging.warning("No SEM stream found")
        return None

    def _ensureGrayscale(self, data):
        ''' Ensures that the image is grayscale. If the image is an grayscale RGB,
        convert it to an 8bit grayscale image.
        data (DataArray or DataArrayShadow): The input image
        return (DataArray): The result 8bit grayscale image
        raises: ValueError if the image is RGB with different color channels
        '''
        if len(data.shape) > 3:
            raise ValueError("Image format not supported")
        elif len(data.shape) == 3:
            if isinstance(data, model.DataArrayShadow):
                data = data.getData()
            data = img.ensureYXC(data)
            if numpy.all(data[:, :, 0] == data[:, :, 1]) and\
                    numpy.all(data[:, :, 0] == data[:, :, 2]):
                data = data[:, :, 0]
            else:
                raise ValueError("Colored RGB image not supported")
        return data

    @call_in_wx_main
    def open_image(self, dlg):
        # Find the available formats (and corresponding extensions)
        formats_to_ext = dataio.get_available_formats(os.O_RDONLY)
        config = get_acqui_conf()
        path = config.last_path

        wildcards, formats = guiutil.formats_to_wildcards(formats_to_ext, include_all=True)
        # TODO dlg.pnl_desc?
        dialog = wx.FileDialog(dlg.pnl_desc,
                               message="Choose a file to load",
                               defaultDir=path,
                               defaultFile="",
                               style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
                               wildcard=wildcards)

        # Show the dialog and check whether is was accepted or cancelled
        if dialog.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return

        # Detect the format to use
        filename = dialog.GetPath()

        data = open_acquisition(filename)[0]
        try:
            data = self._ensureGrayscale(data)
        except ValueError as exception:
            exception_msg = str(exception)
            box = wx.MessageDialog(dlg, exception_msg, "Exit", wx.OK | wx.ICON_STOP)
            box.ShowModal()
            box.Destroy()
            dlg.Destroy()
            return

        s = stream.StaticSEMStream("TEM stream", data)
        tem_projection = AlignmentProjection(s)
        crop = (self.crop_top.value, self.crop_bottom.value,\
                self.crop_left.value, self.crop_right.value)
        flip = (self.flip_x.value, self.flip_y.value)
        tem_projection.setPreprocessingParams(self.invert.value, flip, crop, self.blur.value, True)
        dlg.addStream(tem_projection, 0)
        self._temStream = tem_projection

    @call_in_wx_main
    def align(self, dlg):
        ''' Executes the alignment. If the alignment is successful, the aligned stream is
            added to the main window. If not, an error message is shown.
        dlg (AlignmentAcquisitionDialog): The plugin dialog
        '''
        crop = (self.crop_top.value, self.crop_bottom.value,\
                self.crop_left.value, self.crop_right.value)
        flip = (self.flip_x.value, self.flip_y.value)
        tem_img = preprocess(self._temStream.raw[0], self.invert.value, flip, crop,
                self.blur.value, True)
        sem_img = preprocess(self._semStream.raw[0], False, (False, False), (0, 0, 0, 0), 5, True)
        try:
            tmat, kp_tem, kp_sem = keypoint.FindTransform(tem_img, sem_img)
        except ValueError as exception:
            exception_msg = str(exception)
            box = wx.MessageDialog(dlg, exception_msg, "Exit", wx.OK | wx.ICON_STOP)
            box.ShowModal()
            box.Destroy()
            return

        # get the metadata corresponding to the transformation
        transf_md = get_img_transformation_md(tmat, tem_img, sem_img)
        raw = preprocess(self._temStream.stream.raw[0], False, flip, crop, 0, False)
        raw.metadata = transf_md
        analysis_tab = self.main_app.main_data.getTabByName('analysis')
        aligned_stream = stream.StaticSEMStream("TEM", raw)
        wx.CallAfter(analysis_tab.stream_bar_controller.addStream, aligned_stream, add_to_view=True)
        dlg.Destroy()

    def _update_stream(self, value):
        crop = (self.crop_top.value, self.crop_bottom.value,
                self.crop_left.value, self.crop_right.value)
        flip = (self.flip_x.value, self.flip_y.value)
        self._temStream.setPreprocessingParams(self.invert.value, flip,
                crop, self.blur.value, True)
        self._temStream._shouldUpdateImage()
