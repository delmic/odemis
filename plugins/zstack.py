# -*- coding: utf-8 -*-
'''
Created on 20 July 2018

@author: Anders Muskens

Gives ability to acquire a set of streams multiple times over time.

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

from __future__ import division

from collections import OrderedDict
import logging
import math
import copy
import numpy as np
from odemis import model, dataio, acq
from odemis.acq import stream
from odemis.util import driver
from odemis.acq.stream import MonochromatorSettingsStream, ARStream, \
    SpectrumStream, UNDEFINED_ROI, StaticStream
import odemis.gui
from odemis.gui.conf import get_acqui_conf
from odemis.gui.plugin import Plugin, AcquisitionDialog
from odemis.util.dataio import splitext
import os
import time
import wx
from odemis.model._dataflow import DataArray


class ZStackPlugin(Plugin):
    name = "Z Stack"
    __version__ = "1.0"
    __author__ = u"Anders Muskens"
    __license__ = "Public domain"

    # Describe how the values should be displayed
    # See odemis.gui.conf.data for all the possibilities
    vaconf = OrderedDict((
        ("numberOfAcquisitions", {
            "control_type": odemis.gui.CONTROL_INT,  # no slider
        }),
        ("filename", {
            "control_type": odemis.gui.CONTROL_SAVE_FILE,
        }),
        ("expectedDuration", {
        }),
    ))

    def __init__(self, microscope, main_app):
        super(ZStackPlugin, self).__init__(microscope, main_app)
        # Can only be used with a microscope
        main_data = self.main_app.main_data
        
        if not microscope or main_data.focus is None:
            return

        self.focus = main_data.focus
        self._zrange = self.focus.axes['z'].range
        zunit = self.focus.axes['z'].unit
        self.old_pos = self.focus.position.value
        self.zstart = model.FloatContinuous(self.old_pos['z'], range=self._zrange, unit=zunit)
        self.zstep = model.FloatContinuous(1e-6, range=(-1e-5, 1e-5), unit=zunit, setter=self._setZStep)
        self.numberofAcquisitions = model.IntContinuous(3, (2, 999), setter=self._setNumberOfAcquisitions)

        self.filename = model.StringVA("a.h5")
        self.expectedDuration = model.VigilantAttribute(1, unit="s", readonly=True)

        self.zstep.subscribe(self._update_exp_dur)
        self.numberofAcquisitions.subscribe(self._update_exp_dur)
        
        # Metadata for the acquisition
        self._metadata = None
        
        self._acq_streams = None

        self._dlg = None
        self.addMenu("Acquisition/ZStack...\tCtrl+T", self.start)
        
    def _acqRangeIsValid(self, acq_range):
        return self._zrange[0] <= acq_range <= self._zrange[1]

    def _setZStep(self, zstep):
        # Check if the acquisition will be within the range of the actuator
        acq_range = self.zstart.value + zstep * self.numberofAcquisitions.value
        if self._acqRangeIsValid(acq_range):
            return zstep
        else:
            return self.zstep.value  # Old value
        
    def _setNumberOfAcquisitions(self, n_acq):
        # Check if the acquisition will be within the range of the actuator
        acq_range = self.zstart.value + self.zstep.value * n_acq
        if self._acqRangeIsValid(acq_range):
            return n_acq
        else:
            return self.numberofAcquisitions.value  # Old value

    def _get_new_filename(self):
        conf = get_acqui_conf()
        return os.path.join(
            conf.last_path,
            u"%s%s" % (time.strftime("%Y%m%d-%H%M%S"), conf.last_extension)
        )

    def _update_exp_dur(self, _=None):
        """
        Called when VA that affects the expected duration is changed
        """
        nsteps = self.numberofAcquisitions.value
        speed = self.focus.speed.value['z']
        step_time = driver.estimateMoveDuration(abs(self.zstep.value), speed, 0.01)
        ss, last_ss = self._get_acq_streams()

        sacqt = acq.estimateTime(ss)
        logging.debug("Estimating %g s acquisition for %d streams", sacqt, len(ss))

        dur = sacqt * nsteps + step_time * (nsteps - 1)
        if last_ss:
            dur += acq.estimateTime(ss + last_ss) - sacqt

        # Use _set_value as it's read only
        self.expectedDuration._set_value(math.ceil(dur), force_write=True)

    def _get_live_streams(self, tab_data):
        """
        Return all the live streams present in the given tab
        """
        ss = list(tab_data.streams.value)

        # On the SPARC, there is a Spot stream, which we don't need for live
        if hasattr(tab_data, "spotStream"):
            try:
                ss.remove(tab_data.spotStream)
            except ValueError:
                pass  # spotStream was not there anyway

        for s in ss:
            if isinstance(s, StaticStream):
                ss.remove(s)
        return ss

    def _get_acq_streams(self):
        """
        Return the streams that should be used for acquisition
        return:
           acq_st (list of streams): the streams to be acquired at every repetition
           last_st (list of streamsintp): streams to be acquired at the end
        """
        if not self._dlg:
            return []

        live_st = (self._dlg.microscope_view.getStreams() +
                   self._dlg.hidden_view.getStreams())
        logging.debug("View has %d streams", len(live_st))

        # On the SPARC, the acquisition streams are not the same as the live
        # streams. On the SECOM/DELPHI, they are the same (for now)
        tab_data = self.main_app.main_data.tab.value.tab_data_model
        if hasattr(tab_data, "acquisitionStreams"):
            acq_st = tab_data.acquisitionStreams
            # Discard the acquisition streams which are not visible
            ss = []
            for acs in acq_st:
                if isinstance(acs, stream.MultipleDetectorStream):
                    if any(subs in live_st for subs in acs.streams):
                        ss.append(acs)
                        break
                elif acs in live_st:
                    ss.append(acs)
        else:
            # No special acquisition streams
            ss = live_st

        last_ss = []
        self._acq_streams = acq.foldStreams(ss, self._acq_streams)
        return self._acq_streams, last_ss

    def start(self):
        # Fail if the live tab is not selected
        tab = self.main_app.main_data.tab.value
        if tab.name not in ("secom_live", "sparc_acqui"):
            box = wx.MessageDialog(self.main_app.main_frame,
                       "ZStack acquisition must be done from the acquisition stream.",
                       "ZStack acquisition not possible", wx.OK | wx.ICON_STOP)
            box.ShowModal()
            box.Destroy()
            return

        # On SPARC, fail if no ROI selected
        try:
            if tab.tab_data_model.semStream.roi.value == UNDEFINED_ROI:
                box = wx.MessageDialog(self.main_app.main_frame,
                           "You need to select a region of acquisition.",
                           "Z stack acquisition not possible", wx.OK | wx.ICON_STOP)
                box.ShowModal()
                box.Destroy()
                return
        except AttributeError:
            pass  # Not a SPARC

        # Stop the stream(s) playing to not interfere with the acquisition
        tab.streambar_controller.pauseStreams()

        self.filename.value = self._get_new_filename()
        dlg = AcquisitionDialog(self, "Z Stack acquisition",
                                "The same streams will be acquired multiple times at different Z positions, defined starting from Z start, with a step size.\n")
        self._dlg = dlg
        dlg.addSettings(self, self.vaconf)
        ss = self._get_live_streams(tab.tab_data_model)
        for s in ss:
            if isinstance(s, (ARStream, SpectrumStream, MonochromatorSettingsStream)):
                # TODO: instead of hard-coding the list, a way to detect the type
                # of live image?
                logging.info("Not showing stream %s, for which the live image is not spatial", s)
                dlg.addStream(s, index=None)
            else:
                dlg.addStream(s)
        dlg.addButton("Cancel")
        dlg.addButton("Acquire", self.acquire, face_colour='blue')

        # Update acq time when streams are added/removed
        dlg.microscope_view.stream_tree.flat.subscribe(self._update_exp_dur, init=True)
        dlg.hidden_view.stream_tree.flat.subscribe(self._update_exp_dur, init=True)
        # TODO: update the acquisition time whenever a setting changes

        # TODO: disable "acquire" button if no stream selected

        # TODO: also display the repetition and axis settings for the SPARC streams.

        ans = dlg.ShowModal()

        if ans == 0:
            logging.info("Acquisition cancelled")
        elif ans == 1:
            logging.info("Acquisition completed")
        else:
            logging.warning("Got unknown return code %s", ans)

        if dlg:  # If dlg hasn't been destroyed yet
            dlg.Destroy()

    def initAcquisition(self):
        # Move the focus to the start z position
        logging.debug("Preparing Z Stack acquisition. Moving focus to start position")
        self.old_pos = self.focus.position.value
        self.focus.moveAbs({'z': self.zstart.value}).result()

    def stepAcquisition(self, i):
        self.focus.moveRel({'z': self.zstep.value}).result()
        
    def exportAcquisition(self, images):
        logging.debug("Exporting Z Stack to HDF5")
        exporter = dataio.find_fittest_converter(self.filename.value)
        exporter.export(self.filename.value, images)

    def completeAcquisition(self):
        # Mvoe back to start
        logging.debug("Z Stack acquisition complete. Returning focus to start position")
        self.focus.moveAbs(self.old_pos).result()
        
    def postProcessing(self, images):
        cubes = [self.constructCube(im) for im in images]
        return cubes

    def constructCube(self, images):
        # images is a list of 5 dim data arrays.
        ret = []
        for image in images:
            stack = np.dstack(image)
            #stack = np.swapaxes(stack, 0, 2)
            #stack = np.expand_dims(stack, 1)
            ret.append(stack[0])
            
        # Add back metadata
        metadata3d = copy.copy(images[0].metadata)
        # Extend pixel size to 3D
        ps_x, ps_y = metadata3d[model.MD_PIXEL_SIZE]
        ps_z = self.zstep.value
        metadata3d[model.MD_PIXEL_SIZE] = (ps_x, ps_y, ps_z)
        metadata3d[model.MD_DIMS] = "ZYX"
        self._metadata = copy.copy(metadata3d)
        
        ret = DataArray(ret, metadata3d)
            
        return ret

    # ALL GENERIC
    def acquire(self, dlg):
        main_data = self.main_app.main_data
        str_ctrl = main_data.tab.value.streambar_controller
        stream_paused = str_ctrl.pauseStreams()

        nb = self.numberofAcquisitions.value
        ss, last_ss = self._get_acq_streams()

        sacqt = acq.estimateTime(ss)
        speed = self.focus.speed.value['z']
        step_time = driver.estimateMoveDuration(abs(self.zstep.value), speed, 0.01)

        logging.info("Z stack acquisition started with %d levels with streams %s", nb, ss)

        # Specific plugin init
        self.initAcquisition()

        # TODO: if drift correction, use it over all the time
        f = model.ProgressiveFuture()
        f.task_canceller = lambda l: True  # To allow cancelling while it's running
        f.set_running_or_notify_cancel()  # Indicate the work is starting now
        dlg.showProgress(f)
        
        images = None

        for i in range(nb):
            left = nb - i
            dur = sacqt * left + step_time * (left - 1)
            
            logging.debug("Z Stack Acquisition %d of %d", i, nb)
            
            if left == 1 and last_ss:
                ss += last_ss
                dur += acq.estimateTime(ss) - sacqt

            startt = time.time()
            f.set_progress(end=startt + dur)
            das, e = acq.acquire(ss).result()
            if images is None:
                # Copy metadata from the first acquisition
                self._metadata = copy.copy(das[0].metadata)
                images = [[] for i in range(len(das))]
            
            for im, da in zip(images, das):
                im.append(da)

            if f.cancelled():
                return

            # Execute an action to prepare the next acquisition for the ith acquisition
            self.stepAcquisition(i)

        f.set_result(None)  # Indicate it's over
        
        # Construct a cube from each stream's image. 
        images = self.postProcessing(images)

        # Export image
        self.exportAcquisition(images)

        # Do completion actions
        self.completeAcquisition()

        # self.showAcquisition(self.filename.value)
        dlg.Destroy()
