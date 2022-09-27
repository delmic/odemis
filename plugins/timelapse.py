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

from collections import OrderedDict
import logging
import math
from odemis import model, dataio
from odemis.acq import stream, acqmng
from odemis.acq.stream import MonochromatorSettingsStream, ARStream, \
    SpectrumStream, UNDEFINED_ROI, StaticStream, LiveStream, Stream
from odemis.dataio import get_available_formats
import odemis.gui
from odemis.gui.conf import get_acqui_conf
from odemis.gui.plugin import Plugin, AcquisitionDialog
from odemis.gui.util import formats_to_wildcards
from odemis.util.dataio import splitext
import os
import queue
import threading
import time
import wx


class TimelapsePlugin(Plugin):
    name = "Timelapse"
    __version__ = "2.2"
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
            "wildcard": formats_to_wildcards(get_available_formats(os.O_WRONLY))[0],
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
        self.numberOfAcquisitions = model.IntContinuous(100, (2, 100000))
        self.semOnlyOnLast = model.BooleanVA(False)
        self.filename = model.StringVA("a.h5")
        self.expectedDuration = model.VigilantAttribute(1, unit="s", readonly=True)

        self.period.subscribe(self._update_exp_dur)
        self.numberOfAcquisitions.subscribe(self._update_exp_dur)

        # On SECOM/DELPHI, propose to only acquire the SEM at the end
        if microscope.role in ("secom", "delphi", "enzel"):
            self.vaconf["semOnlyOnLast"]["control_type"] = odemis.gui.CONTROL_CHECK

        self._dlg = None
        self.addMenu("Acquisition/Timelapse...\tCtrl+T", self.start)

        self._to_store = queue.Queue()  # queue of tuples (str, [DataArray]) for saving data
        self._sthreads = []  # the saving threads
        self._exporter = None  # dataio exporter to use

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

        sacqt = acqmng.estimateTime(ss)
        logging.debug("Estimating %g s acquisition for %d streams", sacqt, len(ss))
        intp = max(0, p - sacqt)

        dur = sacqt * nb + intp * (nb - 1)
        if last_ss:
            dur += acqmng.estimateTime(ss + last_ss) - sacqt

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
            acqt = max(1e-3, acqt - Stream.SETUP_OVERHEAD)
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

        for s in ss:
            if isinstance(s, StaticStream):
                ss.remove(s)
        return ss

    def _get_acq_streams(self):
        """
        Return the streams that should be used for acquisition
        return:
           acq_st (list of streams): the streams to be acquired at every repetition
           last_st (list of streams): streams to be acquired at the end
        """
        if not self._dlg:
            return [], []

        live_st = (self._dlg.view.getStreams() +
                   self._dlg.hidden_view.getStreams())
        logging.debug("View has %d streams", len(live_st))

        # On the SPARC, the acquisition streams are not the same as the live
        # streams. On the SECOM/DELPHI, they are the same (for now)
        tab_data = self.main_app.main_data.tab.value.tab_data_model
        if hasattr(tab_data, "acquisitionStreams"):
            acq_st = tab_data.acquisitionStreams
            if isinstance(acq_st, model.VigilantAttribute):  # On ENZEL/METEOR, acquisitionStreams is a ListVA (instead of a set)
                acq_st = acq_st.value

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
        if tab.name not in ("secom_live", "sparc_acqui", "cryosecom-localization"):
            available_tabs = self.main_app.main_data.tab.choices.values()
            exp_tab_name = "localization" if "cryosecom-localization" in available_tabs else "acquisition"
            box = wx.MessageDialog(self.main_app.main_frame,
                       "Timelapse acquisition must be done from the %s tab." % (exp_tab_name,),
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
        tab.streambar_controller.pauseStreams()

        self.filename.value = self._get_new_filename()
        dlg = AcquisitionDialog(self, "Timelapse acquisition",
                                "The same streams will be acquired multiple times, defined by the 'number of acquisitions'.\n"
                                "The time separating each acquisition is defined by the 'period'.\n")
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

        # Force to re-check the minimum period time
        self.period.value = self.period.value

        # Update acq time when streams are added/removed
        dlg.view.stream_tree.flat.subscribe(self._update_exp_dur, init=True)
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

        dlg.view.stream_tree.flat.unsubscribe(self._update_exp_dur)

        dlg.Destroy()

    # Functions to handle the storage of the data in parallel threads

    def _saving_thread(self, i):
        try:
            while True:
                fn, das = self._to_store.get()
                if fn is None:
                    self._to_store.task_done()
                    return
                logging.info("Saving data %s in thread %d", fn, i)
                self._exporter.export(fn, das)
                self._to_store.task_done()
        except Exception:
            logging.exception("Failure in the saving thread")
        finally:
            logging.debug("Saving thread %d done", i)

    def _start_saving_threads(self, n=4):
        """
        n (int >= 1): number of threads
        """
        if self._sthreads:
            logging.warning("The previous saving threads were not stopped, stopping now")
            self._stop_saving_threads()

        for i in range(n):
            t = threading.Thread(target=self._saving_thread, args=(i,))
            t.start()
            self._sthreads.append(t)

    def _stop_saving_threads(self):
        """
        Blocks until all the data has been stored
        Can be called multiple times in a row
        """
        # Indicate to all the threads that they should stop
        for _ in self._sthreads:
            self._to_store.put((None, None))  # Special "quit" message for each thread

        # Wait for all the threads to complete
        self._to_store.join()
        for t in self._sthreads:
            t.join()
        self._sthreads = []

    def _save_data(self, fn, das):
        """
        Queue the requested DataArrays to be stored in the given file
        """
        self._to_store.put((fn, das))

    def acquire(self, dlg):
        main_data = self.main_app.main_data
        str_ctrl = main_data.tab.value.streambar_controller
        stream_paused = str_ctrl.pauseStreams()
        dlg.pauseSettings()

        self._start_saving_threads(4)

        ss, last_ss = self._get_acq_streams()
        sacqt = acqmng.estimateTime(ss)
        p = self.period.value
        nb = self.numberOfAcquisitions.value

        try:
            # If the user just wants to acquire as fast as possible, and there
            # a single stream, we can use an optimised version
            if (len(ss) == 1 and isinstance(ss[0], LiveStream)
                and nb >= 2
                and sacqt < 5 and p < sacqt + Stream.SETUP_OVERHEAD
               ):
                logging.info("Fast timelapse detected, will acquire as fast as possible")
                self._fast_acquire_one(dlg, ss[0], last_ss)
            else:
                self._acquire_multi(dlg, ss, last_ss)
        finally:
            # Make sure the threads are stopped even in case of error
            self._stop_saving_threads()

        # self.showAcquisition(self.filename.value)

        logging.debug("Closing dialog")
        dlg.Close()

    def _fast_acquire_one(self, dlg, st, last_ss):
        """
        Acquires one stream, *as fast as possible* (ie, the period is not used).
        Only works with LiveStreams (and not with MDStreams)
        st (LiveStream)
        last_ss (list of Streams): all the streams to be acquire on the last time
        """
        # Essentially, we trick a little bit the stream, by convincing it that
        # we want a live view, but instead of display the data, we store them.
        # It's much faster because we don't have to stop/start the detector between
        # each acquisition.
        nb = self.numberOfAcquisitions.value

        fn = self.filename.value
        self._exporter = dataio.find_fittest_converter(fn)
        bs, ext = splitext(fn)
        fn_pat = bs + "-%.5d" + ext

        self._acq_completed = threading.Event()

        f = model.ProgressiveFuture()
        f.task_canceller = self._cancel_fast_acquire
        f._stream = st
        if last_ss:
            nb -= 1
            extra_dur = acqmng.estimateTime([st] + last_ss)
        else:
            extra_dur = 0
        self._hijack_live_stream(st, f, nb, fn_pat, extra_dur)

        try:
            # Start acquisition and wait until it's done
            f.set_running_or_notify_cancel()  # Indicate the work is starting now
            dlg.showProgress(f)
            st.is_active.value = True
            self._acq_completed.wait()

            if f.cancelled():
                dlg.resumeSettings()
                return
        finally:
            st.is_active.value = False  # just to be extra sure it's stopped
            logging.debug("Restoring stream %s", st)
            self._restore_live_stream(st)

        # last "normal" acquisition, if needed
        if last_ss:
            logging.debug("Acquiring last acquisition, with all the streams")
            ss = [st] + last_ss
            f.set_progress(end=time.time() + acqmng.estimateTime(ss))
            das, e = acqmng.acquire(ss, self.main_app.main_data.settings_obs).result()
            self._save_data(fn_pat % (nb,), das)

        self._stop_saving_threads()  # Wait for all the data to be stored
        f.set_result(None)  # Indicate it's over

    def _cancel_fast_acquire(self, f):
        f._stream.is_active.value = False
        self._acq_completed.set()
        return True

    def _hijack_live_stream(self, st, f, nb, fn_pat, extra_dur=0):
        st._old_shouldUpdateHistogram = st._shouldUpdateHistogram
        st._shouldUpdateHistogram = lambda: None
        self._data_received = 0

        dur_one = st.estimateAcquisitionTime() - Stream.SETUP_OVERHEAD

        # Function that will be called after each new raw data has been received
        def store_raw_data():
            i = self._data_received
            self._data_received += 1
            logging.debug("Received data %d", i)
            if self._data_received == nb:
                logging.debug("Stopping the stream")
                st.is_active.value = False
                self._acq_completed.set()
            elif self._data_received > nb:
                # sometimes it goes too fast, and an extra data is received
                logging.debug("Skipping extra data")
                return

            self._save_data(fn_pat % (i,), [st.raw[0]])

            # Update progress bar
            left = nb - i
            dur = dur_one * left + extra_dur
            f.set_progress(end=time.time() + dur)

        st._old_shouldUpdateImage = st._shouldUpdateImage
        st._shouldUpdateImage = store_raw_data

    def _restore_live_stream(self, st):
        st._shouldUpdateImage = st._old_shouldUpdateImage
        del st._old_shouldUpdateImage
        st._shouldUpdateHistogram = st._old_shouldUpdateHistogram
        del st._old_shouldUpdateHistogram

    def _acquire_multi(self, dlg, ss, last_ss):
        p = self.period.value
        nb = self.numberOfAcquisitions.value

        fn = self.filename.value
        self._exporter = dataio.find_fittest_converter(fn)
        bs, ext = splitext(fn)
        fn_pat = bs + "-%.5d" + ext

        sacqt = acqmng.estimateTime(ss)
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
                dur += acqmng.estimateTime(ss) - sacqt

            startt = time.time()
            f.set_progress(end=startt + dur)
            das, e = acqmng.acquire(ss, self.main_app.main_data.settings_obs).result()
            if f.cancelled():
                dlg.resumeSettings()
                return

            self._save_data(fn_pat % (i,), das)

            # Wait the period requested, excepted the last time
            if left > 1:
                sleept = (startt + p) - time.time()
                if sleept > 0:
                    time.sleep(sleept)
                else:
                    logging.info("Immediately starting next acquisition, %g s late", -sleept)

        self._stop_saving_threads()  # Wait for all the data to be stored
        f.set_result(None)  # Indicate it's over
