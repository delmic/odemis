# -*- coding: utf-8 -*-
'''
Created on 19 Dec 2016

@author: Éric Piel

Gives ability to acquire SEM stream multiple times and average the result.


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
import numpy
from odemis import model, dataio
import odemis.gui
from odemis.gui.conf import get_acqui_conf
from odemis.gui.plugin import Plugin, AcquisitionDialog
import os
import threading
import time


class AveragePlugin(Plugin):
    name = "Frame Average"
    __version__ = "1.0"
    __author__ = "Éric Piel"
    __license__ = "Public domain"

    # Describe how the values should be displayed
    # See odemis.gui.conf.data for all the possibilities
    vaconf = OrderedDict((
        ("dwellTime", {
            "tooltip": "Time spent on each pixel for one frame",
            "scale": "log",
            "type": "float",
            "accuracy": 2,
        }),
        ("accumulations", {
            "tooltip": "Number of frames acquired and averaged",
            "control_type": odemis.gui.CONTROL_INT,  # no slider
        }),
        ("resolution", {
            "control_type": odemis.gui.CONTROL_READONLY,  # don't allow to change
        }),
        ("filename", {
            "control_type": odemis.gui.CONTROL_SAVE_FILE,
        }),
        ("expectedDuration", {
        }),
    ))

    def __init__(self, microscope, main_app):
        super(AveragePlugin, self).__init__(microscope, main_app)
        # Can only be used with a microscope
        if not microscope:
            return

        # Check which stream the microscope supports
        main_data = self.main_app.main_data
        if not main_data.ebeam:
            return

        self.addMenu("Acquisition/Averaged frame...", self.start)

        dt = main_data.ebeam.dwellTime
        dtrg = (dt.range[0], min(dt.range[1], 1))
        self.dwellTime = model.FloatContinuous(dt.value, range=dtrg, unit=dt.unit)
        self.resolution = main_data.ebeam.resolution  # Just for info
        self.accumulations = model.IntContinuous(10, (1, 10000))
        self.filename = model.StringVA("a.h5")
        self.expectedDuration = model.VigilantAttribute(1, unit="s", readonly=True)

        self.dwellTime.subscribe(self._update_exp_dur)
        self.accumulations.subscribe(self._update_exp_dur)

    def _get_new_filename(self):
        conf = get_acqui_conf()
        return os.path.join(
            conf.last_path,
            u"%s%s" % (time.strftime("%Y%m%d-%H%M%S"), conf.last_extension)
        )

    def start(self):
        """
        Called when the menu entry is selected
        """
        main_data = self.main_app.main_data

        self.filename.value = self._get_new_filename()
        self.dwellTime.value = main_data.ebeam.dwellTime.value
        self._update_exp_dur()

        dlg = AcquisitionDialog(self, "Averaged acquisition",
                    "Acquires the SEM and CL intensity streams multiple times and store the average value.\n"
                    "The number of acquisitions is defined by the 'accumulations' setting.\n")
        dlg.addSettings(self, self.vaconf)
        dlg.addButton("Cancel")
        dlg.addButton("Acquire", self.acquire, face_colour='blue')
        ans = dlg.ShowModal()

        if ans == 0:
            logging.info("Acquisition cancelled")
        elif ans == 1:
            logging.info("Acquisition completed")
        else:
            logging.warning("Got unknown return code %s", ans)

    def _update_exp_dur(self, _=None):
        """
        Called when VA that affects the expected duration is changed
        """
        res = self.main_app.main_data.ebeam.resolution.value
        frt = numpy.prod(res) * self.dwellTime.value * 1.05  # +5% for margin
        tott = frt * self.accumulations.value + 0.1

        # Use _set_value as it's read only
        self.expectedDuration._set_value(tott, force_write=True)

    def acquire(self, dlg):
        main_data = self.main_app.main_data
        nb = self.accumulations.value
        res = self.main_app.main_data.ebeam.resolution.value
        frt = numpy.prod(res) * self.dwellTime.value * 1.05  # +5% for margin

        # All the detectors to use
        dets = [d for d in (main_data.sed, main_data.bsd, main_data.cld) if d]
        if not dets:
            raise ValueError("No EM detector available")
        logging.info("Will acquire frame average on %d detectors", len(dets))
        exporter = dataio.find_fittest_converter(self.filename.value)

        self._rawdas = tuple([] for d in dets) # to store each raw frame
        self._prepare_acq(dets)

        f = model.ProgressiveFuture(end=time.time() + self.expectedDuration.value)
        f.task_canceller = lambda l: True  # To allow cancelling while it's running
        f.set_running_or_notify_cancel()  # Indicate the work is starting now
        dlg.showProgress(f)

        try:
            das = []
            for i in range(nb):
                # Update the progress bar
                left = nb - i
                dur = frt * left + 0.1
                f.set_progress(end=time.time() + dur)

                # Start acquisition
                dets[0].softwareTrigger.notify()

                # Wait for the acquisition
                for ev in self._events:
                    if not ev.wait(dur * 3 + 5):
                        raise IOError("Timeout while waiting for frame")
                    ev.clear()

                logging.info("Acquired frame %d", i + 1)

                if f.cancelled():
                    logging.debug("Acquisition cancelled")
                    self._rawdas = None # To recover the memory immediately
                    return
        finally:
            self._end_acq(dets)

        # Compute the average data
        fdas = []
        for das in self._rawdas:
            # TODO: if it's a lot of accumulation, we should have intermediate
            # averaging during the acquisition
            fdas.append(self._average_data(das))
        self._rawdas = None

        logging.info("Exporting data to %s", self.filename.value)
        exporter.export(self.filename.value, fdas)
        f.set_result(None)  # Indicate it's over

        # Display the file
        self.showAcquisition(self.filename.value)
        dlg.Destroy()

    def _prepare_acq(self, dets):
        # We could synchronize all the detectors, but doing just one, will force
        # the others to wait, as they are all handled by the same e-beam driver
        d0 = dets[0]
        d0.data.synchronizedOn(d0.softwareTrigger)

        # For each detector, create a listener to receive the data, and an event
        # to let the main loop know this data has been received
        self._events = []
        self._listeners = []
        for d, rdas in zip(dets, self._rawdas):
            ev = threading.Event()
            self._events.append(ev)

            # Ad-hoc function to receive the data
            def on_data(df, data, ev=ev, rdas=rdas):
                rdas.append(data)
                ev.set()

            self._listeners.append(on_data)
            d.data.subscribe(on_data)

    def _end_acq(self, dets):
        dets[0].data.synchronizedOn(None)
        for d, l in zip(dets, self._listeners):
            d.data.unsubscribe(l)

    def _average_data(self, das):
        """
        das (list of DataArrays): all the acquisitions from a detector
        return (DataArray): the averaged frame (with the correct metadata)
        """
        # mean() convert to a float for the intermediary type, but to force
        # immediately converting to integers again, we provide an int array
        a = numpy.empty_like(das[0])
        numpy.mean(das, axis=0, out=a)

        # The metadata is the on from the first DataArray, which is good for
        # _almost_ everything
        if model.MD_DWELL_TIME in a.metadata:
            a.metadata[model.MD_DWELL_TIME] *= len(das)

        return a
