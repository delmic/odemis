# -*- coding: utf-8 -*-
'''
Created on 12 Apr 2016

@author: Éric Piel

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
from odemis import model, dataio, acq
from odemis.acq import stream
from odemis.acq.stream._base import UNDEFINED_ROI
import odemis.gui
from odemis.gui.conf import get_acqui_conf
from odemis.gui.plugin import Plugin, AcquisitionDialog
from odemis.util.dataio import splitext
import os
import time
import wx


class TimelapsePlugin(Plugin):
    name = "Timelapse"
    __version__ = "1.2"
    __author__ = u"Éric Piel"
    __license__ = "Public domain"

    # Describe how the values should be displayed
    # See odemis.gui.conf.data for all the possibilities
    vaconf = OrderedDict((
        ("period", {
            "tooltip": "Time between each acquisition",
            "scale": "log",
        }),
        ("numberOfAcquisitions", {
            "control_type": odemis.gui.CONTROL_INT,  # no slider
        }),
        ("semOnlyOnLast", {
            "label": "SEM only on the last",
            "tooltip": "Acquire SEM images only once, after the timelapse",
            "control_type": odemis.gui.CONTROL_NONE,  # hidden by default
        }),
        ("filename", {
            "control_type": odemis.gui.CONTROL_SAVE_FILE,
        }),
        ("expectedDuration", {
        }),
    ))

    def __init__(self, microscope, main_app):
        super(TimelapsePlugin, self).__init__(microscope, main_app)
        # Can only be used with a microscope
        if not microscope:
            return

        self.period = model.FloatContinuous(10, (1e-3, 10000), unit="s",
                                            setter=self._setPeriod)
        # TODO: prevent period < acquisition time of all streams
        self.numberOfAcquisitions = model.IntContinuous(100, (2, 1000))
        self.semOnlyOnLast = model.BooleanVA(False)
        self.filename = model.StringVA("a.h5")
        self.expectedDuration = model.VigilantAttribute(1, unit="s", readonly=True)

        self.period.subscribe(self._update_exp_dur)
        self.numberOfAcquisitions.subscribe(self._update_exp_dur)

        # On SECOM/DELPHI, propose to only acquire the SEM at the end
        if microscope.role in ("secom", "delphi"):
            self.vaconf["semOnlyOnLast"]["control_type"] = odemis.gui.CONTROL_CHECK

        self._dlg = None
        self.addMenu("Acquisition/Timelapse...\tCtrl+T", self.start)

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
        nb = self.numberOfAcquisitions.value
        p = self.period.value
        ss, last_ss = self._get_acq_streams()

        sacqt = acq.estimateTime(ss)
        logging.debug("Estimating %g s acquisition for %d streams", sacqt, len(ss))
        intp = max(0, p - sacqt)

        dur = sacqt * nb + intp * (nb - 1)
        if last_ss:
            dur += acq.estimateTime(ss + last_ss) - sacqt

        # Use _set_value as it's read only
        self.expectedDuration._set_value(math.ceil(dur), force_write=True)

    def _setPeriod(self, period):
        # It should be at least as long as the acquisition time of all the streams
        tot_time = 0
        for s in self._get_acq_streams()[0]:
            acqt = s.estimateAcquisitionTime()
            # Normally we round-up in order to be pessimistic on the duration,
            # but here it's better to be a little optimistic and allow the user
            # to pick a really short period (if each stream has a very short
            # acquisition time).
            acqt = max(1e-3, acqt - stream.Stream.SETUP_OVERHEAD)
            tot_time += acqt

        return min(max(tot_time, period), self.period.range[1])

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

        return ss

    def _get_acq_streams(self):
        """
        Return the streams that should be used for acquisition
        return:
           acq_st (list of streams): the streams to be acquired at every repetition
           last_st (list of streams): streams to be acquired at the end
        """
        if not self._dlg:
            return []

        live_st = self._dlg.microscope_view.getStreams()
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
        if self.semOnlyOnLast.value:
            last_ss = [s for s in ss if isinstance(s, stream.EMStream)]
            ss = [s for s in ss if not isinstance(s, stream.EMStream)]

        return ss, last_ss

    def start(self):
        # Fail if the live tab is not selected
        tab = self.main_app.main_data.tab.value
        if tab.name not in ("secom_live", "sparc_acqui"):
            box = wx.MessageDialog(self.main_app.main_frame,
                       "Timelapse acquisition must be done from the acquisition stream.",
                       "Timelapse acquisition not possible", wx.OK | wx.ICON_STOP)
            box.ShowModal()
            box.Destroy()
            return

        # On SPARC, fail if no ROI selected
        try:
            if tab.tab_data_model.semStream.roi.value == UNDEFINED_ROI:
                box = wx.MessageDialog(self.main_app.main_frame,
                           "You need to select a region of acquisition.",
                           "Timelapse acquisition not possible", wx.OK | wx.ICON_STOP)
                box.ShowModal()
                box.Destroy()
                return
        except AttributeError:
            pass # Not a SPARC

        # Stop the stream(s) playing to not interfere with the acquisition
        stream_paused = tab.streambar_controller.pauseStreams()

        self.filename.value = self._get_new_filename()
        dlg = AcquisitionDialog(self, "Timelapse acquisition",
                                "The same streams will be acquired multiple times, defined by the 'number of acquisitions'.\n"
                                "The time separating each acquisition is defined by the 'period'.\n")
        self._dlg = dlg
        dlg.addSettings(self, self.vaconf)
        ss = self._get_live_streams(tab.tab_data_model)
        for s in ss:
            dlg.addStream(s)
        dlg.addButton("Cancel")
        dlg.addButton("Acquire", self.acquire, face_colour='blue')

        # Force to re-check the minimum period time
        self.period.value = self.period.value

        # Update acq time when streams are added/removed
        dlg.microscope_view.stream_tree.flat.subscribe(self._update_exp_dur, init=True)
        # TODO: update the acquisition time whenever a setting changes

        # TODO: disable "acquire" button if no stream selected

        # TODO: don't even try to display streams which have no spatial projection

        # TODO: also display the repetition and axis settings for the SPARC streams.

        ans = dlg.ShowModal()

        if ans == 0:
            logging.info("Acquisition cancelled")
        elif ans == 1:
            logging.info("Acquisition completed")
        else:
            logging.warning("Got unknown return code %s", ans)

        if dlg: # If dlg hasn't been destroyed yet
            dlg.Destroy()

    def acquire(self, dlg):
        main_data = self.main_app.main_data
        str_ctrl = main_data.tab.value.streambar_controller
        stream_paused = str_ctrl.pauseStreams()

        nb = self.numberOfAcquisitions.value
        p = self.period.value
        ss, last_ss = self._get_acq_streams()

        fn = self.filename.value
        exporter = dataio.find_fittest_converter(fn)
        bs, ext = splitext(fn)
        fn_pat = bs + "-%.5d" + ext

        sacqt = acq.estimateTime(ss)
        intp = max(0, p - sacqt)
        if p < sacqt:
            logging.warning(
                "Acquisition will take %g s, but period between acquisition must be only %g s",
                sacqt, p
            )

        # TODO: if drift correction, use it over all the time

        f = model.ProgressiveFuture()
        f.task_canceller = lambda l: True  # To allow cancelling while it's running
        f.set_running_or_notify_cancel()  # Indicate the work is starting now
        dlg.showProgress(f)

        for i in range(nb):
            left = nb - i
            dur = sacqt * left + intp * (left - 1)
            if left == 1 and last_ss:
                ss += last_ss
                dur += acq.estimateTime(ss) - sacqt

            startt = time.time()
            f.set_progress(end=startt + dur)
            das, e = acq.acquire(ss).result()
            if f.cancelled():
                return

            exporter.export(fn_pat % (i,), das)

            # Wait the period requested, excepted the last time
            if left > 1:
                sleept = (startt + p) - time.time()
                if sleept > 0:
                    time.sleep(sleept)
                else:
                    logging.info("Immediately starting next acquisition, %g s late", -sleept)

        f.set_result(None)  # Indicate it's over

        # self.showAcquisition(self.filename.value)
        dlg.Destroy()
