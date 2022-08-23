# -*- coding: utf-8 -*-
'''
Created on 9 Jan 2015

@author: Éric Piel

Copyright © 2015-2017 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

# This script collects multiple fluorescence images at high frame rate in order
# to provide input for high-resolution reconstruction algorithm.
from collections import OrderedDict
import logging
from odemis import dataio, model, gui
from odemis.acq import stream
from odemis.dataio import get_available_formats
from odemis.gui.conf import get_acqui_conf
from odemis.gui.plugin import Plugin, AcquisitionDialog
from odemis.gui.util import formats_to_wildcards
import os
import queue
import threading
import time


LIVE_UPDATE_PERIOD = 10  # s, time between two images in the GUI (during acquisition)

class SRAcqPlugin(Plugin):
    name = "Super-resolution acquisition"
    __version__ = "1.1"
    __author__ = u"Éric Piel"
    __license__ = "Public domain"

    # Describe how the values should be displayed
    # See odemis.gui.conf.data for all the possibilities
    vaconf = OrderedDict((
        ("number", {
            "label": "Number of frames",
            "tooltip": "Number of frames acquired",
            "control_type": gui.CONTROL_INT,  # no slider
            "accuracy": None,
        }),
        ("countConvertWavelength", {
            "label": "Emission wavelength",
            "tooltip": "Light wavelength received by the camera for count conversion.",
            "control_type": gui.CONTROL_FLT,
        }),
        ("exposureTime", {
            "control_type": gui.CONTROL_SLIDER,
            "scale": "log",
            "range": (0.001, 10.0),
            "type": "float",
            "accuracy": 2,
        }),
        ("binning", {
            "control_type": gui.CONTROL_RADIO,
            "tooltip": "Number of pixels combined",
#             "choices": conf.util.binning_1d_from_2d,
        }),
        ("resolution", {
            "control_type": gui.CONTROL_COMBO,
            "tooltip": "Number of pixels in the image",
            "accuracy": None,  # never simplify the numbers
#             "choices": conf.util.resolution_from_range,
        }),
        ("gain", {}),
        ("emGain", {
            "label": "EMCCD gain",
            "tooltip": "None means automatic selection based on the gain and readout rate.",
        }),
        ("readoutRate", {}),
        ("verticalReadoutRate", {
            "tooltip": "NoneHz means automatically picks the fastest recommended clock."
        }),
        ("verticalClockVoltage", {
            "tooltip": "At higher vertical readout rate, voltage must be increased, \n"
                       "but it might introduce extra noise. 0 means standard voltage.",
        }),
        ("temperature", {}),
        ("filename", {
            "tooltip": "Each acquisition will be saved with the name and the number appended.",
            "control_type": gui.CONTROL_SAVE_FILE,
            "wildcard": formats_to_wildcards(get_available_formats(os.O_WRONLY))[0],
        }),
        ("expectedDuration", {
        }),
    ))

    def __init__(self, microscope, main_app):
        super(SRAcqPlugin, self).__init__(microscope, main_app)
        # Can only be used with a microscope
        if not microscope:
            return

        # Check if the microscope is a SECOM
        main_data = self.main_app.main_data
        if not main_data.ccd or not main_data.light:
            return
        self.light = main_data.light
        self.ccd = main_data.ccd

        self.addMenu("Acquisition/Super-resolution...", self.start)

        # Add the useful VAs which are available on the CCD.
        # (on an iXon, they should all be there)
        for n in ("exposureTime", "resolution", "binning", "gain", "emGain",
                  "countConvertWavelength", "temperature",
                  "readoutRate", "verticalReadoutRate", "verticalClockVoltage"):
            if model.hasVA(self.ccd, n):
                va = getattr(self.ccd, n)
                setattr(self, n, va)

        # Trick to pass the component (ccd to binning_1d_from_2d())
        self.vaconf["binning"]["choices"] = (lambda cp, va, cf:
                       gui.conf.util.binning_1d_from_2d(self.ccd, va, cf))
        self.vaconf["resolution"]["choices"] = (lambda cp, va, cf:
                       gui.conf.util.resolution_from_range(self.ccd, va, cf))

        self.number = model.IntContinuous(1000, (1, 1000000))

        self.filename = model.StringVA("a.tiff")
        self.filename.subscribe(self._on_filename)

        self.expectedDuration = model.VigilantAttribute(1, unit="s", readonly=True)
        self.number.subscribe(self._update_exp_dur)
        self.exposureTime.subscribe(self._update_exp_dur)

        # Create a stream to show the settings changes
        self._stream = stream.FluoStream(
            "Filtered colour",
            self.ccd,
            self.ccd.data,
            emitter=main_data.light,
            em_filter=main_data.light_filter,
            focuser=main_data.focus,
        )

        # For the acquisition
        self._acq_done = threading.Event()
        self._n = 0
        self._startt = 0  # starting time of acquisition
        self._last_display = 0  # last time the GUI image was updated
        self._future = None  # future to represent the acquisition progress
        self._exporter = None  # to save the file

        self._q = queue.Queue()  # queue of tuples (str, DataArray) for saving data
        self._qdisplay = queue.Queue()
        # TODO: find the right number of threads, based on CPU numbers (but with
        # python threading that might be a bit overkill)
        for i in range(4):
            t = threading.Thread(target=self._saving_thread, args=(i,))
            t.daemon = True
            t.start()

    def _get_new_filename(self):
        conf = get_acqui_conf()
        return os.path.join(
            conf.last_path,
            u"%s%s" % (time.strftime("sr-%Y%m%d-%H%M%S"), ".tiff")
        )

    def _on_filename(self, fn):
        # Make the name "fn" -> "fn-XXXXXX.ext"
        bn, ext = os.path.splitext(fn)
        self._fntmpl = bn + "-%06d" + ext
        if not ext.endswith(".tiff"):
            logging.warning("Only TIFF format is recommended to use")

        # Store the directory so that next filename is in the same place
        conf = get_acqui_conf()
        p, bn = os.path.split(fn)
        if p:
            conf.last_path = p

    def _update_exp_dur(self, _=None):
        """
        Called when VA that affects the expected duration is changed
        """
        # On the Andor iXon, in frame transfer mode, the readout is done while
        # the next frame is exposed. So only exposure time counts
        tott = self.exposureTime.value * self.number.value + 0.1

        # Use _set_value as it's read only
        self.expectedDuration._set_value(tott, force_write=True)

    def start(self):
        """
        Called when the menu entry is selected
        """
        main_data = self.main_app.main_data

        # Stop the streams
        tab_data = main_data.tab.value.tab_data_model
        for s in tab_data.streams.value:
            s.should_update.value = False

        self.filename.value = self._get_new_filename()
        self._update_exp_dur()

        # Special CCD settings to get values as photon counting
        if model.hasVA(self.ccd, "countConvert"):
            self.ccd.countConvert.value = 2  # photons

        dlg = AcquisitionDialog(self, "Super-resolution acquisition",
                                "Acquires a series of shortly exposed images, "
                                "and store them in sequence.\n"
                                "Note, the advanced settings are only applied "
                                "after restarting the stream.")
        dlg.addStream(self._stream)
        dlg.addSettings(self, self.vaconf)
        dlg.addButton("Close")
        dlg.addButton("Acquire", self.acquire, face_colour='blue')
        dlg.Maximize()
        ans = dlg.ShowModal()

        # Make sure the stream is not playing anymore and CCD is back to normal
        self._stream.should_update.value = False
        if model.hasVA(self.ccd, "countConvert"):
            try:
                self.ccd.countConvert.value = 0  # normal
            except Exception:
                logging.exception("Failed to set back count convert mode")

        if ans == 0:
            logging.info("Acquisition cancelled")
        elif ans == 1:
            logging.info("Acquisition completed")
        else:
            logging.warning("Got unknown return code %s", ans)

        dlg.Destroy()

    def acquire(self, dlg):
        # Make sure the stream is not playing
        self._stream.should_update.value = False

        self._exporter = dataio.find_fittest_converter(self.filename.value)

        nb = self.number.value
        self._n = 0
        self._acq_done.clear()

        self._startt = time.time()
        self._last_display = self._startt
        end = self._startt + self.expectedDuration.value

        f = model.ProgressiveFuture(end=end)
        f.task_canceller = lambda l: True  # To allow cancelling while it's running
        f.set_running_or_notify_cancel()  # Indicate the work is starting now
        self._future = f
        dlg.showProgress(f)

        try:
            # Special CCD settings to get values as photon counting
            if model.hasVA(self.ccd, "countConvert"):
                self.ccd.countConvert.value = 2  # photons

            # Switch on laser (at the right wavelength and power)
            self._stream._setup_emission()
            self._stream._setup_excitation()

            # Let it start!
            self.ccd.data.subscribe(self._on_image)

            # Wait for the complete acquisition to be done
            while not self._acq_done.wait(1):
                # Update the progress bar
                left = nb - self._n
                dur = self.exposureTime.value * left + 0.1
                f.set_progress(end=time.time() + dur)

                # Update the image
                try:
                    da = self._qdisplay.get(block=False)
                    # Hack: we pretend the stream has received an image it was
                    # subscribed to (although it's paused)
                    self._stream._onNewData(None, da)
                except queue.Empty:
                    pass

            logging.info("Waiting for all data to be saved")
            dur = self._q.qsize() * 0.1  # very pessimistic
            f.set_progress(end=time.time() + dur)
            self._q.join()

            if f.cancelled():
                logging.debug("Acquisition cancelled")
                return
        except Exception as ex:
            self.ccd.data.unsubscribe(self._on_image)
            # TODO: write this in the window
            logging.exception("Failure during SR acquisition")
            f.set_exception(ex)
            return
        finally:
            # Revert CCD count to normal behaviour
            if model.hasVA(self.ccd, "countConvert"):
                try:
                    self.ccd.countConvert.value = 0  # normal
                except Exception:
                    logging.exception("Failed to set back count convert mode")

        f.set_result(None)  # Indicate it's over
        fps = nb / (time.time() - self._startt)
        logging.info("Finished with average %g fps", fps)

        dlg.Close()

    def _on_image(self, df, data):
        """
        Called for each new image
        """
        try:
            self._n += 1
            self._q.put((self._n, data))
            now = time.time()
            fps = self._n / (now - self._startt)
            logging.info("Received data %d (%g fps), queue size = %d",
                         self._n, fps, self._q.qsize())

            if self._q.qsize() > 8:
                logging.warning("Saving queue is behind acquisition")
            # TODO: if queue size too long => pause until it's all processed

            if self._future.cancelled():
                logging.info("Stopping early due to cancellation")
                self.ccd.data.unsubscribe(self._on_image)
                self._acq_done.set()  # indicate it's over
                return

            if now > self._last_display + LIVE_UPDATE_PERIOD:
                if not self._qdisplay.qsize():
                    self._qdisplay.put(data)
                else:
                    logging.debug("Not pushing new image to display as previous one hasn't been processed")

            if self._n == self.number.value:
                self.ccd.data.unsubscribe(self._on_image)
                self._acq_done.set()  # indicate it's over
        except Exception as ex:
            logging.exception("Failure to save acquisition %d", self._n)
            self._future.set_exception(ex)
            self.ccd.data.unsubscribe(self._on_image)
            self._acq_done.set()  # indicate it's over

    def _saving_thread(self, i):
        try:
            while True:
                n, da = self._q.get()
                logging.info("Saving data %d in thread %d", n, i)
                filename = self._fntmpl % (n,)
                try:
                    self._exporter.export(filename, da, compressed=True)
                except Exception:
                    logging.exception("Failed to store data %d", n)
                self._q.task_done()
                logging.debug("Data %d saved", n)
        except Exception:
            logging.exception("Failure in the saving thread")

