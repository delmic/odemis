# -*- coding: utf-8 -*-
'''
Created on 10 May 2016

@author: Lennard Voortman

Gives ability to manually change the overlay-metadata.

This is free and unencumbered software released into the public domain.

Anyone is free to copy, modify, publish, use, compile, sell, or
distribute this software, either in source code form or as a compiled
binary, for any purpose, commercial or non-commercial, and by any
means.

In jurisdictions that recognize copyright laws, the author or authors
of this software dedicate any and all copyright interest in the
software to the public domain. We make this dedication for the benefit
of the public at large and to the detriment of our heirs and
successors. We intend this dedication to be an overt act of
relinquishment in perpetuity of all present and future rights to this
software under copyright law.

The software is provided "as is", without warranty of any kind,
express or implied, including but not limited to the warranties of
merchantability, fitness for a particular purpose and non-infringement.
In no event shall the authors be liable for any claim, damages or
other liability, whether in an action of contract, tort or otherwise,
arising from, out of or in connection with the software or the use or
other dealings in the software.
'''

from collections import OrderedDict
import functools
import logging
import math
from odemis import model
from odemis.acq.stream import DataProjection
from odemis.gui.plugin import Plugin, AcquisitionDialog
import wx


class VAHolder(object):
    pass


class ManualOverlayPlugin(Plugin):
    name = "Manual Overlay"
    __version__ = "1.1"
    __author__ = "Lennard Voortman"
    __license__ = "Public domain"

    def __init__(self, microscope, main_app):
        super(ManualOverlayPlugin, self).__init__(microscope, main_app)
        self.addMenu("Data correction/Overlay corrections...", self.start)
        self._dlg = None

    def start(self):
        dlg = AcquisitionDialog(self, "Manually change the alignment",
                                text="Change the translation, rotation, and pixel size "
                                     "of any stream. The display is immediately updated, "
                                     "but you need to save the file (as a snapshot) for "
                                     "the changes to be permanent.")
        self._dlg = dlg

        vah = VAHolder()
        vah._subscribers = []
        vaconf = OrderedDict()

        tab_data = self.main_app.main_data.tab.value.tab_data_model
        if not tab_data.streams.value:
            box = wx.MessageDialog(self.main_app.main_frame,
                       "No stream is present, so it's not possible to modify the alignment.",
                       "No stream", wx.OK | wx.ICON_STOP)
            box.ShowModal()
            box.Destroy()
            return

        for i, stream in enumerate(tab_data.streams.value):
            dlg.addStream(stream)

            # Add 5 VAs for each stream, to modify the overlay metadata
            poscor = stream.raw[0].metadata.get(model.MD_POS_COR, (0, 0))
            rotation = stream.raw[0].metadata.get(model.MD_ROTATION_COR, 0)
            scalecor = stream.raw[0].metadata.get(model.MD_PIXEL_SIZE_COR, (1, 1))
            shear = stream.raw[0].metadata.get(model.MD_SHEAR_COR, 0)
            vatransx = model.FloatContinuous(-poscor[0], range=(-10e-6, 10e-6), unit="m")
            vatransy = model.FloatContinuous(-poscor[1], range=(-10e-6, 10e-6), unit="m")
            varot = model.FloatContinuous(rotation, range=(-math.pi, math.pi), unit="rad")
            vascalex = model.FloatContinuous(scalecor[0], range=(0.5, 1.5))
            vascaley = model.FloatContinuous(scalecor[1], range=(0.5, 1.5))
            vashear = model.FloatContinuous(shear, range=(-1, 1))

            # Add the VAs to the holder, and to the vaconf mainly to force the order
            setattr(vah, "%dTransX" % i, vatransx)
            setattr(vah, "%dTransY" % i, vatransy)
            setattr(vah, "%dRotation" % i, varot)
            setattr(vah, "%dScaleX" % i, vascalex)
            setattr(vah, "%dScaleY" % i, vascaley)
            setattr(vah, "%dShear" % i, vashear)
            vaconf["%dTransX" % i] = {"label": "%s trans X" % stream.name.value}
            vaconf["%dTransY" % i] = {"label": "%s trans Y" % stream.name.value}
            vaconf["%dRotation" % i] = {"label": "%s rotation" % stream.name.value}
            vaconf["%dScaleX" % i] = {"label": "%s scale X" % stream.name.value}
            vaconf["%dScaleY" % i] = {"label": "%s scale Y" % stream.name.value}
            vaconf["%dShear" % i] = {"label": "%s shear" % stream.name.value}

            # Create listeners with information of the stream and dimension
            va_on_transx = functools.partial(self._on_trans, stream, 0)
            va_on_transy = functools.partial(self._on_trans, stream, 1)
            va_on_rotation = functools.partial(self._on_rotation, stream)
            va_on_scalex = functools.partial(self._on_scale, stream, 0)
            va_on_scaley = functools.partial(self._on_scale, stream, 1)
            va_on_shear = functools.partial(self._on_shear, stream)

            # We hold a reference to the listeners to prevent automatic subscription
            vah._subscribers.append(va_on_transx)
            vah._subscribers.append(va_on_transy)
            vah._subscribers.append(va_on_rotation)
            vah._subscribers.append(va_on_scalex)
            vah._subscribers.append(va_on_scaley)
            vah._subscribers.append(va_on_shear)
            vatransx.subscribe(va_on_transx)
            vatransy.subscribe(va_on_transy)
            varot.subscribe(va_on_rotation)
            vascalex.subscribe(va_on_scalex)
            vascaley.subscribe(va_on_scaley)
            vashear.subscribe(va_on_shear)

        dlg.addSettings(vah, vaconf)
        # TODO: add a 'reset' button
        dlg.addButton("Done", None, face_colour='blue')
        dlg.ShowModal()

        # The end
        dlg.Destroy()
        vah._subscribers = []
        self._dlg = None

    def _force_update_proj(self, st):
        """
        Force updating the projection of the given stream
        """
        # Update in the view of the window, and also the current tab
        views = [self._dlg.view]
        views.extend(self.main_app.main_data.tab.value.tab_data_model.views.value)

        for v in views:
            for sp in v.stream_tree.getProjections():  # stream or projection
                if isinstance(sp, DataProjection):
                    s = sp.stream
                else:
                    s = sp
                if s is st:
                    sp._shouldUpdateImage()

    def _on_trans(self, stream, i, value):
        logging.debug("New trans = %f on stream %s", value, stream.name.value)
        poscor = stream.raw[0].metadata.get(model.MD_POS_COR, (0, 0))
        if i == 0:
            poscor = (-value, poscor[1])
        else:
            poscor = (poscor[0], -value)
        stream.raw[0].metadata[model.MD_POS_COR] = poscor
        self._force_update_proj(stream)

    def _on_scale(self, stream, i, value):
        logging.debug("New scale = %f on stream %s", value, stream.name.value)
        scalecor = stream.raw[0].metadata.get(model.MD_PIXEL_SIZE_COR, (1, 1))
        if i == 0:
            scalecor = (value, scalecor[1])
        else:
            scalecor = (scalecor[0], value)
        stream.raw[0].metadata[model.MD_PIXEL_SIZE_COR] = scalecor
        self._force_update_proj(stream)

    def _on_rotation(self, stream, value):
        logging.debug("New rotation = %f on stream %s", value, stream.name.value)
        stream.raw[0].metadata[model.MD_ROTATION_COR] = value
        self._force_update_proj(stream)

    def _on_shear(self, stream, value):
        logging.debug("New shear = %f on stream %s", value, stream.name.value)
        stream.raw[0].metadata[model.MD_SHEAR_COR] = value
        self._force_update_proj(stream)
