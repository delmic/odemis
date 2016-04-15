# -*- coding: utf-8 -*-
'''
Created on 12 Apr 2016

@author: Éric Piel

Gives ability to acquire SEM or fluorescence stream multiple times over time.

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
from odemis import model, dataio, acq
from odemis.acq import stream
import odemis.gui
from odemis.gui.conf import get_acqui_conf
from odemis.gui.conf.data import get_local_vas
import os
import time

from odemis.gui.plugin import Plugin, AcquisitionDialog


class TimelapsePlugin(Plugin):
    name = "Timelapse"
    __version__ = "1.0"
    __author__ = "Éric Piel"
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
        ("filename", {
            "control_type": odemis.gui.CONTROL_NEW_FILE,  # TODO: NEW_FILE
        }),
    ))

    def __init__(self, microscope, main_app):
        super(TimelapsePlugin, self).__init__(microscope, main_app)
        # Can only be used with a microscope
        if not microscope:
            return
        else:
            # Check which stream the microscope supports
            main_data = self.main_app.main_data
            # TODO: also support backscatter detector
            if main_data.sed and main_data.ebeam:
                self.addMenu("Acquisition/Timelapse SEM...", self.start_sem)
            if main_data.ccd and main_data.light:
                self.addMenu("Acquisition/Timelapse Fluorescence...", self.start_fluo)

        self.period = model.FloatContinuous(10, (1e-3, 10000), unit="s")
        self.numberOfAcquisitions = model.IntContinuous(100, (2, 1000))
        self.filename = model.StringVA("a.h5")

        self._stream = None

    def _get_new_filename(self):
        conf = get_acqui_conf()
        return os.path.join(
            conf.last_path,
            u"%s%s" % (time.strftime("%Y%m%d-%H%M%S"), conf.last_extension)
        )

    def start_sem(self):
        main_data = self.main_app.main_data
        sem_stream = stream.SEMStream(
            "Secondary electrons survey",
            main_data.sed,
            main_data.sed.data,
            main_data.ebeam,
            focuser=main_data.ebeam_focus,
            emtvas=get_local_vas(main_data.ebeam),
            detvas=get_local_vas(main_data.sed),
        )
        self.start(sem_stream)

    def start_fluo(self):
        main_data = self.main_app.main_data
        fluo_stream = stream.FluoStream(
            "Filtered colour",
            main_data.ccd,
            main_data.ccd.data,
            main_data.light,
            main_data.light_filter,
            focuser=main_data.focus,
            emtvas={"power"},
            detvas=get_local_vas(main_data.ccd),
        )
        self.start(fluo_stream)

    def start(self, st):
        self._stream = st
        self.filename.value = self._get_new_filename()

        dlg = AcquisitionDialog(self, "Timelapse acquisition",
                                "The same stream will be acquired multiple times, defined by the 'number of acquisitions'.\n"
                                "The time separating each acquisition is defined by the 'period'.\n")
        dlg.addSettings(self, self.vaconf)
        dlg.addStream(st)
        dlg.addButton("Cancel")
        dlg.addButton("Acquire", self.acquire, face_colour='blue')
        ans = dlg.ShowModal()

        if ans == 0:
            logging.info("Acquisition cancelled")
        elif ans == 1:
            logging.info("Acquisition completed")
        else:
            logging.warning("Got unknown return code %s", ans)

    def acquire(self, dlg):
        nb = self.numberOfAcquisitions.value
        p = self.period.value
        sacqt = self._stream.estimateAcquisitionTime()
        intp = max(0, p - sacqt)
        if p < sacqt:
            logging.warning("Acquisition will take %g s, but period between acquisition must be only %g s",
                            sacqt, p)

        exporter = dataio.find_fittest_converter(self.filename.value)

        f = model.ProgressiveFuture()
        f.task_canceller = lambda l: True  # To allow cancelling while it's running
        f.set_running_or_notify_cancel()  # Indicate the work is starting now
        dlg.showProgress(f)

        das = []
        for i in range(nb):
            left = nb - i
            dur = sacqt * left + intp * (left - 1)
            startt = time.time()
            f.set_progress(end=startt + dur)
            d, e = acq.acquire([self._stream]).result()
            das.extend(d)
            if f.cancelled():
                return

            # Wait the period requested, excepted the last time
            if left > 1:
                sleept = (startt + p) - time.time()
                if sleept > 0:
                    time.sleep(sleept)
                else:
                    logging.info("Immediately starting next acquisition, %g s late", -sleept)

        exporter.export(self.filename.value, das)
        f.set_result(None)  # Indicate it's over

        # self.showAcquisition(self.filename.value)
        dlg.Destroy()
