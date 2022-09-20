# -*- coding: utf-8 -*-
'''
Created on 18 Mar 2022

@author: Éric Piel

Sets the ebeam rotation metadata to the same value as the .rotation VA, so that
the image is shown non-rotated, but that the fine alignment knows about it, and
does not get confused (by rotating the optical image).

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

import logging
from odemis import model
from odemis.gui.plugin import Plugin


class EbeamRotPlugin(Plugin):
    name = "E-beam rotation metadata fixed"
    __version__ = "1.0"
    __author__ = u"Éric Piel"
    __license__ = "Public domain"

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)

        self.ebeam = main_app.main_data.ebeam

        if not self.ebeam:
            logging.info("No e-beam, plugin disabled ")
            return
        elif not model.hasVA(self.ebeam, "rotation"):
            logging.info("e-beam has no rotation, plugin disabled ")
            return

        self.ebeam.rotation.subscribe(self._on_rotation, init=True)

    def _on_rotation(self, rot: float):
        """
        Called when the e-beam rotation is changed.
        Takes care of updating the metadata with it.
        rot (0 <= float < 2pi): the new rotation
        """
        self.ebeam.updateMetadata({model.MD_ROTATION: rot,
                                   model.MD_ROTATION_COR: rot
                                 })
