# -*- coding: utf-8 -*-
'''
Created on 10 April 2017

@author: Guilherme Stiebler

Gives ability to automatically place a EM image so that it's aligned with
another one (already present).

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

from collections import OrderedDict
import cv2
import logging
import numpy
from odemis import dataio, model, gui
from odemis.acq import stream
from odemis.acq.align import keypoint
from odemis.acq.stream import OpticalStream, EMStream, CLStream
from odemis.gui.conf import get_acqui_conf
from odemis.gui.plugin import Plugin, AcquisitionDialog
from odemis.gui.util import call_in_wx_main
from odemis.util import img, limit_invocation
from odemis.util.conversion import get_img_transformation_md
import os
import weakref
import wx

from odemis.acq.align.keypoint import preprocess
import odemis.gui.util as guiutil
import odemis.util.dataio as udataio


class AlignmentAcquisitionDialog(AcquisitionDialog):

    # override the standard addStream() method to not create the stream panels.
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
                self.view.addStream(stream)
            else:
                self.view_r.addStream(stream)

class AlignmentProjection(stream.RGBSpatialProjection):

    def __init__(self, in_stream):
        super(AlignmentProjection, self).__init__(in_stream)
        self.setPreprocessingParams(False, (False, False), (0, 0, 0, 0), 0, False,
                                    None, None)

    def setPreprocessingParams(self, invert, flip, crop, gaussian_sigma, eqhis,
                               kp=None, mkp=None):
        ''' Sets the parameters for the preprocessing function called on ._updateImage
        invert (bool): Invert the brightness levels of the image
        flip (tuple(bool, bool)): Determine if the image should be flipped on the X and Y axis
        crop (tuple(t,b,l,r): Crop values in pixels
        gaussian_sigma (int): Blur intensity
        eqhis (bool): Determine if an histogram equalization should be executed
        kp (None or list of Keypoints): position of all the keypoints
        mkp (None or list of Keypoints): position of the matching keypoints
        '''
        self._invert = invert
        self._flip = flip
        self._crop = crop
        self._gaussian_sigma = gaussian_sigma
        self._eqhis = eqhis
        self._kp = kp
        self._mkp = mkp

    def _updateImage(self):
        raw = self.stream.raw[0]
        metadata = self.stream._find_metadata(raw.metadata)
        raw = img.ensure2DImage(raw)  # Remove extra dimensions (of length 1)
        grayscale_im = preprocess(raw, self._invert, self._flip, self._crop,
                                  self._gaussian_sigma, self._eqhis)
        rgb_im = img.DataArray2RGB(grayscale_im)
        if self._kp:
            rgb_im = cv2.drawKeypoints(rgb_im, self._kp, None, color=(30, 30, 255), flags=0)
        if self._mkp:
            rgb_im = cv2.drawKeypoints(rgb_im, self._mkp, None, color=(0, 255, 0), flags=0)

        rgb_im = model.DataArray(rgb_im, metadata)
        rgb_im.flags.writeable = False
        self.image.value = rgb_im


class AutomaticOverlayPlugin(Plugin):
    name = "Automatic Alignment"
    __version__ = "1.1"
    __author__ = u"Guilherme Stiebler, Éric Piel"
    __license__ = "GPLv2"

    # Describe how the values should be displayed
    # See odemis.gui.conf.data for all the possibilities
    vaconf = OrderedDict((
        ("im_ref", {
            "label": "Reference image",
            "tooltip": "Change the reference image (left)",
            # Normally it's automatically a combo-box, but if there is only one
            # it'll become a read-only text. However the read-only text is not
            # able to properly show the stream name (for now), so we force it to
            # always be a combo-box.
            "control_type": gui.CONTROL_COMBO,
        }),
        ("blur_ref", {
            "label": "Blur reference",
            "tooltip": "Blur window size for the reference SEM image (left)",
        }),
        ("blur", {
            "label": "Blur new",
            "tooltip": "Blur window size for the new EM image (right)",
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
        ("draw_kp", {
            "label": "Show key-points"
        }),
    ))

    def __init__(self, microscope, main_app):
        super(AutomaticOverlayPlugin, self).__init__(microscope, main_app)
        self.addMenu("Data correction/Add && Align EM...", self.start)

        self._dlg = None

        # Projections of the reference and new data
        self._rem_proj = None
        self._nem_proj = None

        # On-the-fly keypoints and matching keypoints computed
        self._nem_kp = None
        self._nem_mkp = None
        self._rem_kp = None
        self._rem_mkp = None

        # im_ref.choices contains the streams and their name
        self.im_ref = model.VAEnumerated(None, choices={None: ""})
        self.blur_ref = model.IntContinuous(2, range=(0, 20), unit="px")
        self.blur = model.IntContinuous(5, range=(0, 20), unit="px")
        self.crop_top = model.IntContinuous(0, range=(0, 200), unit="px")
        self.crop_top.clip_on_range = True
        self.crop_bottom = model.IntContinuous(0, range=(0, 200), unit="px")
        self.crop_bottom.clip_on_range = True
        self.crop_left = model.IntContinuous(0, range=(0, 200), unit="px")
        self.crop_left.clip_on_range = True
        self.crop_right = model.IntContinuous(0, range=(0, 200), unit="px")
        self.crop_right.clip_on_range = True
        # TODO: inverting the values doesn't seem to really affect the keypoints
        self.invert = model.BooleanVA(False)
        # TODO: ideally, the flip shouldn't be needed, but it seems the matchers
        # in OpenCV are not able to handle "negative" scale
        self.flip_x = model.BooleanVA(False)
        self.flip_y = model.BooleanVA(False)
        self.draw_kp = model.BooleanVA(True)
#         self.wta = model.IntContinuous(2, range=(2, 4))
#         self.scaleFactor = model.FloatContinuous(1.2, range=(1.01, 2))
#         self.nlevels = model.IntContinuous(8, range=(4, 48))
#         self.patchSize = model.IntContinuous(31, range=(4, 256))

        # Any change on the VAs should update the stream
        self.blur_ref.subscribe(self._on_ref_stream)
        self.blur.subscribe(self._on_new_stream)
        self.crop_top.subscribe(self._on_new_stream)
        self.crop_bottom.subscribe(self._on_new_stream)
        self.crop_left.subscribe(self._on_new_stream)
        self.crop_right.subscribe(self._on_new_stream)
        self.invert.subscribe(self._on_new_stream)
        self.flip_x.subscribe(self._on_new_stream)
        self.flip_y.subscribe(self._on_new_stream)
        self.draw_kp.subscribe(self._on_draw_kp)
#         self.wta.subscribe(self._on_new_stream)
#         self.scaleFactor.subscribe(self._on_new_stream)
#         self.nlevels.subscribe(self._on_new_stream)
#         self.patchSize.subscribe(self._on_new_stream)

    def start(self):
        self.im_ref.unsubscribe(self._on_im_ref)
        try:
            self._update_im_ref()
        except ValueError:
            box = wx.MessageDialog(self.main_app.main_frame,
                                   "No spatial stream found to use as reference.",
                                   "Failed to find spatial stream", wx.OK | wx.ICON_STOP)
            box.ShowModal()
            box.Destroy()
            return

        tem_stream = self.open_image(self.main_app.main_frame)
        if not tem_stream:
            return

        dlg = AlignmentAcquisitionDialog(self, "Automatic image alignment",
                                         text="Adjust the parameters so that the two images looks similar\n"
                                              "and the key-points are detected at similar areas.")
        self._dlg = dlg

        # removing the play overlay from the viewports
        dlg.viewport_l.canvas.remove_view_overlay(dlg.viewport_l.canvas.play_overlay)
        dlg.viewport_l.canvas.fit_view_to_next_image = True
        dlg.viewport_r.canvas.remove_view_overlay(dlg.viewport_r.canvas.play_overlay)
        dlg.viewport_r.canvas.fit_view_to_next_image = True

        rem_projection = AlignmentProjection(self.im_ref.value())
        self._rem_proj = rem_projection

        nem_projection = AlignmentProjection(tem_stream)
        self._nem_proj = nem_projection

        self._on_ref_stream()
        self._on_new_stream()
        dlg.addSettings(self, self.vaconf)

        # Note: normally we are supposed to give stream (and the view will
        # automatically create a projection), not directly a projection. However
        # we want a special projection, and it works (excepted for a warning).
        dlg.addStream(rem_projection, 0)
        dlg.addStream(nem_projection, 1)

        self.im_ref.subscribe(self._on_im_ref)

        dlg.addButton("Align", self.align, face_colour='blue')
        dlg.addButton("Cancel", None)
        dlg.pnl_gauge.Hide()
        dlg.ShowModal() # Blocks until the window is closed

        if dlg:
            dlg.Destroy()

    def _update_im_ref(self):
        """
        Find all the compatible streams, fill-up the choices in im_ref, and
        update the value, while trying to keep the same as before if possible.
        raise ValueError: if they are no compatible streams.
        """
        tab = self.main_app.main_data.getTabByName("analysis")
        tab_data = tab.tab_data_model

        # Any greyscale spatial stream should be compatible. For now we consider
        # that it is either EM, Optical or CLi (which is an approximation).
        s_compatible = [s for s in tab_data.streams.value if isinstance(s, (OpticalStream, EMStream, CLStream))]
        # Put the EMStreams first, as it'd typically be the preferred stream to use
        s_compatible.sort(key=lambda s: isinstance(s, EMStream), reverse=True)

        if not s_compatible:
            raise ValueError("No spatial stream found")

        # The names of the steams should be a set, but to force the order of
        # display, we need to pass an OrderedDict.
        s_names = OrderedDict((weakref.ref(s), s.name.value) for s in s_compatible)

        prev_stream_ref = self.im_ref.value
        self.im_ref._choices = s_names  # To avoid checking against the current value
        # Leave the previous value if still available
        if prev_stream_ref not in s_names:
            self.im_ref.value = next(iter(s_names))

    @call_in_wx_main
    def _on_im_ref(self, s_ref):
        """
        Called when a new stream is selected for the reference image
        """
        # remove "all" streams from the left view (actually there is only one)
        for s in self._dlg.view.getStreams():
            logging.info("removing stream %s", s)
            self._dlg.view.removeStream(s)

        # Create a new projection and put it in the canvas
        self._dlg.viewport_l.canvas.fit_view_to_next_image = True
        rem_projection = AlignmentProjection(s_ref())
        self._rem_proj = rem_projection

        self._on_ref_stream()
        self._dlg.addStream(rem_projection, 0)

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
            if (numpy.all(data[:, :, 0] == data[:, :, 1]) and
                numpy.all(data[:, :, 0] == data[:, :, 2])):
                data = data[:, :, 0]
            else:
                raise ValueError("Coloured RGB image not supported")
        return data

    def open_image(self, dlg):
        tab = self.main_app.main_data.getTabByName("analysis")
        tab_data = tab.tab_data_model
        fi = tab_data.acq_fileinfo.value

        if fi and fi.file_name:
            path, _ = os.path.split(fi.file_name)
        else:
            config = get_acqui_conf()
            path = config.last_path

        # Find the available formats (and corresponding extensions)
        formats_to_ext = dataio.get_available_formats(os.O_RDONLY)
        wildcards, formats = guiutil.formats_to_wildcards(formats_to_ext, include_all=True)
        dialog = wx.FileDialog(dlg,
                               message="Choose a file to load",
                               defaultDir=path,
                               defaultFile="",
                               style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
                               wildcard=wildcards)

        # Show the dialog and check whether is was accepted or cancelled
        if dialog.ShowModal() != wx.ID_OK:
            return None

        # Detect the format to use
        filename = dialog.GetPath()

        data = udataio.open_acquisition(filename)[0]
        try:
            data = self._ensureGrayscale(data)
        except ValueError as ex:
            box = wx.MessageDialog(dlg, str(ex), "Failed to open image",
                                   wx.OK | wx.ICON_STOP)
            box.ShowModal()
            box.Destroy()
            return None

        self.crop_top.range = (0, data.shape[0] // 2)
        self.crop_bottom.range = (0, data.shape[0] // 2)
        self.crop_left.range = (0, data.shape[1] // 2)
        self.crop_right.range = (0, data.shape[1] // 2)

        data.metadata[model.MD_POS] = (0, 0)
        data.metadata[model.MD_PIXEL_SIZE] = (1e-9, 1e-9)

        basename = os.path.splitext(os.path.split(filename)[1])[0]
        return stream.StaticSEMStream(basename, data)

    @call_in_wx_main
    def align(self, dlg):
        ''' Executes the alignment. If the alignment is successful, the aligned stream is
            added to the main window. If not, an error message is shown.
        dlg (AlignmentAcquisitionDialog): The plugin dialog
        '''
        crop = (self.crop_top.value, self.crop_bottom.value,
                self.crop_left.value, self.crop_right.value)
        flip = (self.flip_x.value, self.flip_y.value)
        tem_img = preprocess(self._nem_proj.raw[0], self.invert.value, flip, crop,
                             self.blur.value, True)
        sem_raw = img.ensure2DImage(self._rem_proj.raw[0])
        sem_img = preprocess(sem_raw, False, (False, False), (0, 0, 0, 0),
                             self.blur_ref.value, True)
        try:
            tmat, _, _, _, _ = keypoint.FindTransform(tem_img, sem_img)

            # get the metadata corresponding to the transformation
            transf_md = get_img_transformation_md(tmat, tem_img, sem_img)
            logging.debug("Computed transformation metadata: %s", transf_md)
        except ValueError as ex:
            box = wx.MessageDialog(dlg, str(ex), "Failed to align images",
                                   wx.OK | wx.ICON_STOP)
            box.ShowModal()
            box.Destroy()
            return

        # Shear is really big => something is gone wrong
        if abs(transf_md[model.MD_SHEAR]) > 1:
            logging.warning("Shear is %g, which means the alignment is probably wrong",
                            transf_md[model.MD_SHEAR])
            transf_md[model.MD_SHEAR] = 0
        # Pixel size ratio is more than 2 ? => something is gone wrong
        # TODO: pixel size 100x bigger/smaller than the reference is also wrong
        pxs = transf_md[model.MD_PIXEL_SIZE]
        if not (0.5 <= pxs[0] / pxs[1] <= 2):
            logging.warning("Pixel size is %s, which means the alignment is probably wrong",
                            pxs)
            transf_md[model.MD_PIXEL_SIZE] = (pxs[0], pxs[0])

        # The actual image inserted is not inverted and not blurred, but we still
        # want it flipped and cropped.
        raw = preprocess(self._nem_proj.raw[0], False, flip, crop, 0, False)
        raw.metadata.update(transf_md)

        # Add a new stream panel (removable)
        analysis_tab = self.main_app.main_data.getTabByName('analysis')
        aligned_stream = stream.StaticSEMStream(self._nem_proj.stream.name.value, raw)
        scont = analysis_tab.stream_bar_controller.addStream(aligned_stream, add_to_view=True)
        scont.stream_panel.show_remove_btn(True)

        # Finish by closing the window
        dlg.Close()

    @limit_invocation(0.3)
    def _precompute_kp(self):
        if self.draw_kp.value:
            if not self._nem_proj or not self._rem_proj:
                return

#             # TODO: pass extra args for the keypoint detector
#             dtkargs = {"WTA_K": self.wta.value,
#                        "scaleFactor": self.scaleFactor.value,
#                        "nlevels": self.nlevels.value,
#                        "patchSize": self.patchSize.value,
#                        "edgeThreshold": self.patchSize.value,  # should be equal
#                        }
            crop = (self.crop_top.value, self.crop_bottom.value,
                    self.crop_left.value, self.crop_right.value)
            flip = (self.flip_x.value, self.flip_y.value)
            tem_img = preprocess(self._nem_proj.raw[0], self.invert.value, flip, crop,
                                 self.blur.value, True)
            sem_raw = img.ensure2DImage(self._rem_proj.raw[0])
            sem_img = preprocess(sem_raw, False, (False, False), (0, 0, 0, 0),
                                 self.blur_ref.value, True)
            try:
                tmat, self._nem_kp, self._rem_kp, self._nem_mkp, self._rem_mkp = \
                         keypoint.FindTransform(tem_img, sem_img)
            except ValueError as ex:
                logging.debug("No match found: %s", ex)
                # TODO: if no match, still show the keypoints
                self._nem_kp = None
                self._nem_mkp = None
                self._rem_kp = None
                self._rem_mkp = None
        else:
            self._nem_kp = None
            self._nem_mkp = None
            self._rem_kp = None
            self._rem_mkp = None

        self._update_ref_stream()
        self._update_new_stream()

    def _on_draw_kp(self, draw):
        self._precompute_kp()

    def _on_ref_stream(self, _=None):
        self._precompute_kp()
        self._update_ref_stream()

    def _on_new_stream(self, _=None):
        self._precompute_kp()
        self._update_new_stream()

    def _update_ref_stream(self):
        if not self._rem_proj:
            return
        self._rem_proj.setPreprocessingParams(False, (False, False), (0, 0, 0, 0),
                                               self.blur_ref.value, True,
                                               self._rem_kp, self._rem_mkp)
        self._rem_proj._shouldUpdateImage()

    def _update_new_stream(self):
        if not self._nem_proj:
            return
        crop = (self.crop_top.value, self.crop_bottom.value,
                self.crop_left.value, self.crop_right.value)
        flip = (self.flip_x.value, self.flip_y.value)
        self._nem_proj.setPreprocessingParams(self.invert.value, flip,
                                               crop, self.blur.value, True,
                                               self._nem_kp, self._nem_mkp)
        self._nem_proj._shouldUpdateImage()
