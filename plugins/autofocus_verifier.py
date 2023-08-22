# -*- coding: utf-8 -*-
"""
Created on 10 July 2023

@author: Canberk Akın

Runs procedures acquire an image of the slit and verifies that the image is focused via a button under Help > Development

Copyright © 2023 Canberk Akın, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
"""
import os
import time
import wx

from odemis import dataio, model
from odemis.acq import acqmng
from odemis.acq.align.autofocus import (Sparc2ManualFocus, _getSpectrometerFocusingComponents)
from odemis.gui.comp import popup
from odemis.gui.plugin import Plugin

class AutofocusVerifierPlugin(Plugin):
    name = "Autofocus Verifier"
    __version__ = "1.0"
    __author__ = "Canberk Akın"
    __license__ = "Public domain"

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)

        self.addMenu("Help/Development/Verify autofocus", self.show_alignment)

        self.tab = self.main_app.main_data.getTabByName("sparc2_align")
        self.tab_data = self.tab.tab_data_model

        self._mf_future = None

        self.main = self.tab_data.main
        self.bl = self.main.brightlight

        date = time.strftime('%Y%m%d-%H%M%S')
        self.path = os.path.join(os.path.expanduser(u"~"), f"Documents/System FAT hostname {date}/")


    def verify_autofocus(self):
        """
        Initiates the autofocus verification process.
        """
        self.tab_data.align_mode.value = "lens-align"
        self.tab_data.autofocus_active.value = True

        self.tab_data.autofocus_active.subscribe(self.on_auto_focus_done)
        popup.show_message(self.main_app.main_frame, "Initiating the autofocus verification",
                           "Will run multiple procedures to verify autofocus")

    def on_auto_focus_done(self, active):
        """
        Called when the autofocus process is finished
        Calls a manual focus process 1 second after the autofocus is completed
        """
        if active:
            return
        self.tab_data.autofocus_active.unsubscribe(self.on_auto_focus_done)
        wx.CallLater(1000, self.start_manual_focus)

    def start_manual_focus(self):
        """
        Pauses the streams, calls the manual focus process
        """
        # pause the streams
        acqui_tab = self.main_app.main_data.getTabByName("sparc_acqui")
        acqui_tab.streambar_controller.pauseStreams()

        self._mf_future = Sparc2ManualFocus(self.main.opm, self.bl, "spec-focus", toggled=True)
        self._mf_future.add_done_callback(self._on_manual_focus_done)

    def _on_manual_focus_done(self, future):
        """
        Calls when manual focus is done
        Acquires image for all the detector-grating combinations
        """
        spectrograph, dets, selector = _getSpectrometerFocusingComponents(self.tab._focus_streams[0].focuser)
        gratings = list(spectrograph.axes["grating"].choices.keys())

        self.filenames = []

        for g in gratings:
            f = spectrograph.moveAbs({"grating": g})
            f.result()
            for s in self.tab._focus_streams:
                ftr = acqmng.acquire([s])
                das, exp = ftr.result()
                if exp:
                    raise exp
                # remove metadata
                md = das[0].metadata
                for k in (model.MD_AR_POLE, model.MD_AR_MIRROR_BOTTOM, model.MD_AR_MIRROR_TOP,
                          model.MD_AR_FOCUS_DISTANCE, model.MD_AR_HOLE_DIAMETER, model.MD_AR_PARABOLA_F,
                          model.MD_AR_XMAX, model.MD_ROTATION, model.MD_WL_LIST):
                    md.pop(k, None)
                detector = s.detector.name
                grating = spectrograph.axes["grating"].choices[g]
                grating = grating.replace("/", "")
                md[model.MD_DESCRIPTION] = f"{grating} {detector}"
                fn = os.path.join(self.path, f"focus - {detector} - {grating}.h5")
                self.filenames.append(fn)
                os.makedirs(self.path, exist_ok = True)
                exporter = dataio.find_fittest_converter(fn)
                exporter.export(fn, das)

        self._mf_future = Sparc2ManualFocus(self.main.opm, self.bl, "spec-focus", toggled=False)
        self._mf_future.add_done_callback(self._on_auto_focus_verifier_done)

    def _on_auto_focus_verifier_done(self, future):
        """
        Loads the acquired images in analysis tab
        """
        analysis_tab = self.main_app.main_data.getTabByName("analysis")
        self.main_app.main_data.tab.value = analysis_tab

        extend = False
        for fn_img in self.filenames:
            analysis_tab.load_data(fn_img, extend=extend)
            extend = True

        popup.show_message(self.main_app.main_frame, "Autofocus verification done",
                           "The autofocus verification is completed.")

    def show_alignment(self):
        """
        Shows the alignment tab with a little bit of delay to ensure autofocus buttons can be found by the plugin.
        """
        alignment_tab = self.main_app.main_data.getTabByName('sparc2_align')
        self.main_app.main_data.tab.value = alignment_tab

        wx.CallLater(500, self.verify_autofocus)
