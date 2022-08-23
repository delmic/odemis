# -*- coding: utf-8 -*-
'''
Created on 5 October 2020

@author: Victoria Mavrikopoulou

The RGB plugin can be used for merging 3 SEM images into a single RGB image.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
'''

import functools
import logging
import numpy
import odemis
import odemis.util.dataio as udataio
import os
import wx
from collections import OrderedDict
from odemis import model, gui
from odemis.acq import stream
from odemis.acq.stream import DataProjection
from odemis.dataio import get_available_formats
from odemis.gui.plugin import Plugin, AcquisitionDialog
from odemis.gui.util import call_in_wx_main
from odemis.gui.util import formats_to_wildcards
from odemis.util import img

TINT_RED = (255, 0, 0)
TINT_GREEN = (0, 255, 0)
TINT_BLUE = (0, 0, 255)


class MergeChannelsPlugin(Plugin):
    name = "Add RGB channels"
    __version__ = "1.0"
    __author__ = u"Victoria Mavrikopoulou"
    __license__ = "GPLv2"

    # The values are displayed with the following order
    vaconf = OrderedDict((
        ("filenameR", {
            "label": "Red channel",
            "control_type": odemis.gui.CONTROL_OPEN_FILE,
            "wildcard": formats_to_wildcards(get_available_formats(os.O_RDONLY), include_all=True)[0]
        }),
        ("redShiftX", {
            "label": "   Red shift X"
        }),
        ("redShiftY", {
            "label": "   Red shift Y"
        }),
        ("filenameG", {
            "label": "Green channel",
            "control_type": odemis.gui.CONTROL_OPEN_FILE,
            "wildcard": formats_to_wildcards(get_available_formats(os.O_RDONLY), include_all=True)[0]
        }),
        ("greenShiftX", {
            "label": "   Green shift X"
        }),
        ("greenShiftY", {
            "label": "   Green shift Y"
        }),
        ("filenameB", {
            "label": "Blue channel",
            "control_type": odemis.gui.CONTROL_OPEN_FILE,
            "wildcard": formats_to_wildcards(get_available_formats(os.O_RDONLY), include_all=True)[0]
        }),
        ("blueShiftX", {
            "label": "   Blue shift X"
        }),
        ("blueShiftY", {
            "label": "   Blue shift Y"
        }),
        ("cropBottom", {
            "label": "Crop bottom"
        }),
    ))

    def __init__(self, microscope, main_app):
        super(MergeChannelsPlugin, self).__init__(microscope, main_app)

        self.filenameR = model.StringVA(" ")
        self.filenameG = model.StringVA(" ")
        self.filenameB = model.StringVA(" ")
        self.redShiftX = model.FloatContinuous(0, range=(-500, 500), unit="px")
        self.redShiftY = model.FloatContinuous(0, range=(-500, 500), unit="px")
        self.greenShiftX = model.FloatContinuous(0, range=(-500, 500), unit="px")
        self.greenShiftY = model.FloatContinuous(0, range=(-500, 500), unit="px")
        self.blueShiftX = model.FloatContinuous(0, range=(-500, 500), unit="px")
        self.blueShiftY = model.FloatContinuous(0, range=(-500, 500), unit="px")
        self.cropBottom = model.IntContinuous(0, range=(0, 200), unit="px")

        analysis_tab = self.main_app.main_data.getTabByName('analysis')
        analysis_tab.stream_bar_controller.add_action("Add RGB channels...", self.start)

        self.filenameR.subscribe(self._filenameR)
        self.filenameG.subscribe(self._filenameG)
        self.filenameB.subscribe(self._filenameB)
        self.cropBottom.subscribe(self._cropBottom)

        self._subscribers = []
        self._dlg = None
        self._stream_red = None
        self._stream_green = None
        self._stream_blue = None
        self._raw_orig = {}  # dictionary (Stream -> DataArray) to handle the (un)cropping

    def start(self):
        dlg = AcquisitionDialog(self, "Merging channels to RGB image",
                                text="Insert 3 R, G, B files so that they are assigned the tints \n"
                                     "and are merged to an RGB image.")
        # remove the play overlay from the viewport
        dlg.viewport_l.canvas.remove_view_overlay(dlg.viewport_l.canvas.play_overlay)

        self._dlg = dlg
        dlg.addStream(None)
        dlg.Size = (1000, 600)

        dlg.addSettings(self, self.vaconf)
        dlg.addButton("Cancel", None)
        dlg.addButton("Add", self._updateViewer, face_colour='blue')

        dlg.pnl_gauge.Hide()
        dlg.ShowModal()  # Blocks until the window is closed

        # Destroy the dialog and reset the VAs and subscribers
        dlg.Destroy()
        self.filenameR.value = " "
        self.filenameG.value = " "
        self.filenameB.value = " "
        self.redShiftX.value = 0
        self.redShiftY.value = 0
        self.greenShiftX.value = 0
        self.greenShiftY.value = 0
        self.blueShiftX.value = 0
        self.blueShiftY.value = 0
        self.cropBottom.value = 0
        self._subscribers = []
        self._dlg = None
        self._raw_orig = {}

    def _filenameR(self, filenameR):
        """Open the filename that corresponds to RED channel. If an image is already inserted, remove the old stream
        and add the new stream in the Acquisition Dialog."""
        if self._stream_red is not None:
            self._removeStream(self._stream_red)
        self._stream_red = self._openImage(filenameR, TINT_RED, self.redShiftX, self.redShiftY)
        self._storeDir(filenameR)

    def _filenameG(self, filenameG):
        """Open the filename that corresponds to GREEN channel. If an image is already inserted, remove the old stream
        and add the new stream in the Acquisition Dialog."""
        if self._stream_green is not None:
            self._removeStream(self._stream_green)
        self._stream_green = self._openImage(filenameG, TINT_GREEN, self.greenShiftX, self.greenShiftY)
        self._storeDir(filenameG)

    def _filenameB(self, filenameB):
        """Open the filename that corresponds to BLUE channel. If an image is already inserted, remove the old stream
        and add the new stream in the Acquisition Dialog."""
        if self._stream_blue is not None:
            self._removeStream(self._stream_blue)
        self._stream_blue = self._openImage(filenameB, TINT_BLUE, self.blueShiftX, self.blueShiftY)
        self._storeDir(filenameB)

    def _storeDir(self, fn):
        """Store the directory of the given filename so as the next filename is in the same place"""
        path, bn = os.path.split(fn)
        files = [self.filenameR, self.filenameG, self.filenameB]
        for se in self._dlg.setting_controller.entries:
            if se.vigilattr in files:
                se.value_ctrl.default_dir = path

    def _openImage(self, filename, tint, shiftX, shiftY):
        """ Open the given filename and assign the tint of the corresponding channel. Add the stream to the dialog and
        apply the crop functionality. Two sliders are displayed for every image to provide the option of shifting the
        streams in x and y dimension. If there is no filename given return None.
        Args:
            filename(str) : the given filename with the R, G or B stream
            tint(tuple): the color tint to be assigned
            shiftX(ContinuousVA): shift x value in meters
            shiftY(ContinuousVA): shift y value in meters
        Returns (Stream or None): the displayed stream
        """
        if filename == " ":
            return None

        try:
            data = udataio.open_acquisition(filename)[0]
            pxs = data.metadata.get(model.MD_PIXEL_SIZE, (1e-06, 1e-06))
            if pxs[0] > 1e-04 or pxs[1] > 1e-04:
                data.metadata[model.MD_PIXEL_SIZE] = (1e-06, 1e-06)
                logging.warning("The given pixel size %s is too big, it got replaced to the default value %s", pxs,
                                (1e-06, 1e-06))
            data = self._ensureRGB(data, tint)
        except Exception as ex:
            logging.exception("Failed to open %s", filename)
            self._showErrorMessage("Failed to open image", "Failed to open image:\n%s" % (ex,))
            return None

        basename, ext = os.path.splitext(os.path.split(filename)[1])
        stream_ch = stream.StaticFluoStream(basename, data)
        self._raw_orig[stream_ch] = data
        self._dlg.addStream(stream_ch)
        self._setupStreambar()

        self._cropBottom()
        self._connectShift(stream_ch, 0, shiftX)
        self._connectShift(stream_ch, 1, shiftY)

        return stream_ch

    @call_in_wx_main
    def _showErrorMessage(self, title, msg):
        """
        Shows an error message in a message box
        title (str)
        msg (str)
        """
        box = wx.MessageDialog(self._dlg, msg, title, wx.OK | wx.ICON_STOP)
        box.ShowModal()
        box.Destroy()

    def _ensureRGB(self, data, tint):
        """
        Ensures that the image is grayscale. If the image is a grayscale RGB, convert it
        to an 8bit grayscale image of 2 dimensions and assign the corresponding tint to it.
        Update the metadata of the image.
        data (DataArray or DataArrayShadow): The input image
        return (DataArray): The result image which the assigned tint
        raises: ValueError if the image is RGB with different color channels
        """
        if len(data.shape) > 3:
            raise ValueError("Image format not supported")
        if isinstance(data, model.DataArrayShadow):
            data = data.getData()
        if len(data.shape) == 3:
            data = img.ensureYXC(data)
            if (numpy.all(data[:, :, 0] == data[:, :, 1]) and
                numpy.all(data[:, :, 0] == data[:, :, 2])):
                data = data[:, :, 0]
                data.metadata[model.MD_DIMS] = "YX"
            else:
                raise ValueError("Coloured RGB image not supported")

        if model.MD_POS not in data.metadata:
            data.metadata[model.MD_POS] = (0, 0)
        if model.MD_PIXEL_SIZE not in data.metadata:
            data.metadata[model.MD_PIXEL_SIZE] = (1e-9, 1e-9)
        data.metadata[model.MD_USER_TINT] = tint

        return data

    def _connectShift(self, stream, index, vashift):
        """Create listeners with information of the stream and the dimension.
        Hold a reference to the listeners to prevent automatic subscription"""
        va_on_shift = functools.partial(self._onShift, stream, index)
        self._subscribers.append(va_on_shift)
        vashift.subscribe(va_on_shift)

    def _removeStream(self, st):
        """Remove the given stream since another one is loaded from the user for display"""
        sconts = self._dlg.streambar_controller.stream_controllers
        for sc in sconts:
            if sc.stream is st:
                sc.stream_panel.on_remove_btn(st)
                del self._raw_orig[st]

    @call_in_wx_main
    def _setupStreambar(self):
        """Force stream panel to static mode. Needed for preventing user to play or
        remove streams from the stream panel"""
        sconts = self._dlg.streambar_controller.stream_controllers
        for sctrl in sconts:
            sctrl.stream_panel.to_static_mode()

    def _onShift(self, stream, i, value):
        """
        Update the stream after shifting it by the given value.
        Args:
            stream(StaticFluoStream): stream to be shifted
            i(int): index to show at which dimension the stream is to be shifted
            value(ContinuousVA): shift values in meters
        """
        logging.debug("New shift = %f on stream %s", value, stream.name.value)
        poscor = stream.raw[0].metadata.get(model.MD_POS_COR, (0, 0))
        px_size = stream.raw[0].metadata[model.MD_PIXEL_SIZE]
        if i == 0:
            poscor = (-value * px_size[0], poscor[1])
        else:
            poscor = (poscor[0], -value * px_size[1])
        stream.raw[0].metadata[model.MD_POS_COR] = poscor
        self._forceUpdate(stream)

    def _cropBottom(self, _=None):
        """Crop the data bar at the bottom of the image"""
        for st, r in self._raw_orig.items():
            prev_md = st.raw[0].metadata
            st.raw[0] = r[:max(1, r.shape[0] - self.cropBottom.value), :]
            st.raw[0].metadata = prev_md
            self._forceUpdate(st)

    def _forceUpdate(self, st):
        """Force updating the projection of the given stream"""
        views = [self._dlg.view]
        for v in views:
            for sp in v.stream_tree.getProjections():  # stream or projection
                if isinstance(sp, DataProjection):
                    s = sp.stream
                else:
                    s = sp
                if s is st:
                    sp._shouldUpdateImage()

    def _updateViewer(self, dlg):
        """Update the view in the Analysis Tab with the merged image.
        Called when the user clicks on Done to close the dialog"""
        views = [self._dlg.view]
        das = []
        for v in views:
            for st in v.stream_tree.getProjections():  # stream or projection
                if isinstance(st, DataProjection):
                    s = st.stream
                else:
                    s = st
                das.append(s.raw[0])

        analysis_tab = self.main_app.main_data.tab.value
        analysis_tab.display_new_data(self.filenameR.value, das, extend=True)

        dlg.Close()
