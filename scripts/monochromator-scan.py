#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 19 Nov 2015

@author: Éric Piel

This is a script to acquire a full spectrum based on a monochromator, by scanning
along the center wavelength of the spectrograph

run as:
./scripts/monochromator-scan.py

You first need to run Odemis (with a SPARC). Then, in the acquisition tab,
select spot mode, and pick the point you're interested.

'''

from past.builtins import long
from collections import OrderedDict
from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, RUNNING
import logging
import math
import numpy
from odemis import dataio, model
from odemis.acq import stream, acqmng
import odemis.gui
from odemis.gui.conf import get_acqui_conf
from odemis.gui.model import TOOL_SPOT
from odemis.gui.plugin import Plugin, AcquisitionDialog
from odemis.util import units, executeAsyncTask
import os
import sys
import threading
import time
import wx
from builtins import input

logging.getLogger().setLevel(logging.INFO)  # put "DEBUG" level for more messages


class MonochromatorScanStream(stream.Stream):
    """
    Stream that allows to acquire a spectrum by scanning the wavelength of a
    spectrograph and acquiring with a monochromator
    """

    def __init__(self, name, detector, emitter, spectrograph, opm=None):
        """
        name (string): user-friendly name of this stream
        detector (Detector): the monochromator
        emitter (Emitter): the emitter (eg: ebeam scanner)
        spectrograph (Actuator): the spectrograph
        """
        self.name = model.StringVA(name)

        # Hardware Components
        self._detector = detector
        self._emitter = emitter
        self._sgr = spectrograph
        self._opm = opm

        self.is_active = model.BooleanVA(False)

        wlr = spectrograph.axes["wavelength"].range
        self.startWavelength = model.FloatContinuous(400e-9, wlr, unit="m")
        self.endWavelength = model.FloatContinuous(500e-9, wlr, unit="m")
        self.numberOfPixels = model.IntContinuous(51, (2, 10001), unit="px")
        # TODO: could be a local attribute?
        self.dwellTime = model.FloatContinuous(1e-3, range=self._emitter.dwellTime.range,
                                               unit="s")
        self.emtTranslation = model.TupleContinuous((0, 0),
                                                    range=self._emitter.translation.range,
                                                    cls=(int, long, float),
                                                    unit="px")

        # For acquisition
        self._pt_acq = threading.Event()
        self._data = []
        self._md = {}

    def estimateAcquisitionTime(self):
        """
        Estimate the time it will take to put through the overlay procedure

        returns (float): approximate time in seconds that overlay will take
        """
        nbp = self.numberOfPixels.value
        dt = self.dwellTime.value
        return nbp * (dt + 0.05)  # 50 ms to change wavelength

    def acquire(self):
        """
        Runs the acquisition
        returns Future that will have as a result a DataArray with the spectrum
        """
        # Make sure every stream is prepared, not really necessary to check _prepared
        f = self.prepare()
        f.result()

        est_start = time.time() + 0.1
        f = model.ProgressiveFuture(start=est_start,
                                    end=est_start + self.estimateAcquisitionTime())
        f.task_canceller = self._cancelAcquisition
        f._acq_state = RUNNING
        f._acq_lock = threading.Lock()
        f._acq_done = threading.Event()

        # run task in separate thread
        executeAsyncTask(f, self._runAcquisition, args=(f,))
        return f

    def _on_mchr_data(self, df, data):
        if not self._md:
            self._md = data.metadata.copy()
        if data.shape != (1, 1):
            logging.error("Monochromator scan got %s values for just one point", data.shape)
        self._data.append(data[0, 0])
        self._pt_acq.set()

    def _runAcquisition(self, future):
        self._data = []
        self._md = {}

        wls = self.startWavelength.value
        wle = self.endWavelength.value
        res = self.numberOfPixels.value
        dt = self.dwellTime.value
        trig = self._detector.softwareTrigger
        df = self._detector.data

        # Prepare the hardware
        self._emitter.resolution.value = (1, 1)  # Force one pixel only
        self._emitter.translation.value = self.emtTranslation.value
        self._emitter.dwellTime.value = dt

        df.synchronizedOn(trig)
        df.subscribe(self._on_mchr_data)

        wllist = []
        if wle == wls:
            res = 1

        if res <= 1:
            res = 1
            wli = 0
        else:
            wli = (wle - wls) / (res - 1)

        try:
            for i in range(res):
                left = (res - i) * (dt + 0.05)
                future.set_progress(end=time.time() + left)

                cwl = wls + i * wli  # requested value
                self._sgr.moveAbs({"wavelength": cwl}).result()
                if future._acq_state == CANCELLED:
                    raise CancelledError()
                cwl = self._sgr.position.value["wavelength"]  # actual value
                logging.info("Acquiring point %d/%d @ %s", i + 1, res,
                             units.readable_str(cwl, unit="m", sig=3))

                self._pt_acq.clear()
                trig.notify()
                if not self._pt_acq.wait(dt * 5 + 1):
                    raise IOError("Timeout waiting for the data")
                if future._acq_state == CANCELLED:
                    raise CancelledError()
                wllist.append(cwl)

            # Done
            df.unsubscribe(self._on_mchr_data)
            df.synchronizedOn(None)

            # Convert the sequence of data into one spectrum in a DataArray

            if wls > wle:  # went backward? => sort back the spectrum
                logging.debug("Inverting spectrum as acquisition went from %g to %g m", wls, wls)
                self._data.reverse()
                wllist.reverse()

            na = numpy.array(self._data)  # keeps the dtype
            na.shape += (1, 1, 1, 1)  # make it 5th dim to indicate a channel
            md = self._md
            md[model.MD_WL_LIST] = wllist
            if model.MD_OUT_WL in md:
                # The MD_OUT_WL on the monochromator contains the current cw, which we don't want
                del md[model.MD_OUT_WL]

            # MD_POS should already be at the correct position (from the e-beam metadata)

            # MD_PIXEL_SIZE is not meaningful but handy for the display in Odemis
            # (it's the size of the square on top of the SEM survey => BIG!)
            sempxs = self._emitter.pixelSize.value
            md[model.MD_PIXEL_SIZE] = (sempxs[0] * 50, sempxs[1] * 50)

            spec = model.DataArray(na, md)

            with future._acq_lock:
                if future._acq_state == CANCELLED:
                    raise CancelledError()
                future._acq_state = FINISHED

            return [spec]

        except CancelledError:
            raise  # Just don't log the exception
        except Exception:
            logging.exception("Failure during monochromator scan")
        finally:
            # In case it was stopped before the end
            df.unsubscribe(self._on_mchr_data)
            df.synchronizedOn(None)

            future._acq_done.set()

    def _cancelAcquisition(self, future):
        with future._acq_lock:
            if future._acq_state == FINISHED:
                return False  # too late
            future._acq_state = CANCELLED

        logging.debug("Cancelling acquisition of components %s and %s",
                      self._emitter.name, self._detector.name)

        self._pt_acq.set()  # To help end quickly

        # Wait for the thread to be complete (and hardware state restored)
        future._acq_done.wait(5)
        return True


def acquire_spec(wls, wle, res, dt, filename):
    """
    wls (float): start wavelength in m
    wle (float): end wavelength in m
    res (int): number of points to acquire
    dt (float): dwell time in seconds
    filename (str): filename to save to
    """
    # TODO: take a progressive future to update and know if it's the end

    ebeam = model.getComponent(role="e-beam")
    sed = model.getComponent(role="se-detector")
    mchr = model.getComponent(role="monochromator")
    try:
        sgrh = model.getComponent(role="spectrograph")
    except LookupError:
        sgrh = model.getComponent(role="spectrograph-dedicated")
    opm = acq.path.OpticalPathManager(model.getMicroscope())

    prev_dt = ebeam.dwellTime.value
    prev_res = ebeam.resolution.value
    prev_scale = ebeam.scale.value
    prev_trans = ebeam.translation.value
    prev_wl = sgrh.position.value["wavelength"]

    # Create a stream for monochromator scan
    mchr_s = MonochromatorScanStream("Spectrum", mchr, ebeam, sgrh, opm=opm)
    mchr_s.startWavelength.value = wls
    mchr_s.endWavelength.value = wle
    mchr_s.numberOfPixels.value = res
    mchr_s.dwellTime.value = dt
    mchr_s.emtTranslation.value = ebeam.translation.value

    # Create SEM survey stream
    survey_s = stream.SEMStream("Secondary electrons survey",
                                sed, sed.data, ebeam,
        emtvas={"translation", "scale", "resolution", "dwellTime"},
    )
    # max FoV, with scale 4
    survey_s.emtTranslation.value = (0, 0)
    survey_s.emtScale.value = (4, 4)
    survey_s.emtResolution.value = (v / 4 for v in ebeam.resolution.range[1])
    survey_s.emtDwellTime.value = 10e-6  # 10µs is hopefully enough

    # Acquire using the acquisition manager
    # Note: the monochromator scan stream is unknown to the acquisition manager,
    # so it'll be done last
    expt = acqmng.estimateTime([survey_s, mchr_s])
    f = acqmng.acquire([survey_s, mchr_s])

    try:
        # Note: the timeout is important, as it allows to catch KeyboardInterrupt
        das, e = f.result(2 * expt + 1)
    except KeyboardInterrupt:
        logging.info("Stopping before end of acquisition")
        f.cancel()
        return
    finally:
        logging.debug("Restoring hardware settings")
        if prev_res != (1, 1):
            ebeam.resolution.value = prev_res
        ebeam.dwellTime.value = prev_dt
        sgrh.moveAbs({"wavelength": prev_wl})
        ebeam.scale.value = prev_scale
        ebeam.translation.value = prev_trans
        if prev_res != (1, 1):
            ebeam.resolution.value = prev_res
        ebeam.dwellTime.value = prev_dt

    if e:
        logging.error("Acquisition failed: %s", e)

    if das:
        # Save the file
        exporter = dataio.find_fittest_converter(filename)
        exporter.export(filename, das)
        logging.info("Spectrum successfully saved to %s", filename)
        input("Press Enter to close.")


def getNumber(prompt):
    """
    return (float)
    """
    while True:
        input(prompt)
        try:
            return float(s)
        except ValueError:
            print("Please type in a valid number")


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """
    ebeam = model.getComponent(role="e-beam")
    while ebeam.resolution.value != (1, 1):
        input("Please select spot mode and pick a point and press Enter...")

    wls = getNumber("Starting wavelength (in nm): ") * 1e-9
    wle = getNumber("Ending wavelength (in nm): ") * 1e-9
    nbp = getNumber("Number of wavelengths to acquire: ")
    dt = getNumber("Dwell time (in ms): ") * 1e-3
    exp_time = nbp * (dt + 0.05)  # 50 ms to change wavelength
    print("Expected duration: %s" % (units.readable_time(math.ceil(exp_time)),))

    filename = input("Filename to store the spectrum: ")
    if "." not in filename:
        # No extension -> force hdf5
        filename += ".h5"

    print("Press Ctrl+C to cancel the acquisition")

    try:
        acquire_spec(wls, wle, int(nbp), dt, filename)
    except Exception:
        logging.exception("Unexpected error while performing action.")
        return 127

    return 0


# Plugin version for the GUI
class MonoScanPlugin(Plugin):
    name = "Monochromator Scan"
    __version__ = "1.3"
    __author__ = u"Éric Piel"
    __license__ = "GNU General Public License 2"

    # Describe how the values should be displayed
    # See odemis.gui.conf.data for all the possibilities
    vaconf = OrderedDict((
        ("startWavelength", {
            "control_type": odemis.gui.CONTROL_FLT,  # no slider
        }),
        ("endWavelength", {
            "control_type": odemis.gui.CONTROL_FLT,  # no slider
        }),
        ("numberOfPixels", {
            "control_type": odemis.gui.CONTROL_INT,  # no slider
        }),
        ("dwellTime", {
            "tooltip": "Time spent by the e-beam on each pixel",
            "range": (1e-9, 10),
            "scale": "log",
        }),
        ("filename", {
            "control_type": odemis.gui.CONTROL_SAVE_FILE,
        }),
        ("expectedDuration", {
        }),
    ))

    def __init__(self, microscope, main_app):
        super(MonoScanPlugin, self).__init__(microscope, main_app)

        # Can only be used on a Sparc with a monochromator
        if not microscope:
            return
        try:
            self.ebeam = model.getComponent(role="e-beam")
            self.mchr = model.getComponent(role="monochromator")
        except LookupError:
            logging.info("No monochromator, cannot use the plugin")
            return
        try:
            self.sgrh = model.getComponent(role="spectrograph")
        except LookupError:
            try:
                self.sgrh = model.getComponent(role="spectrograph-dedicated")
            except LookupError:
                logging.info("No spectrograph found, cannot use the plugin")
                return

        self.addMenu("Acquisition/Monochromator scan...", self.start)

        # the SEM survey stream (will be updated when showing the window)
        self._survey_s = None

        # Create a stream for monochromator scan
        self._mchr_s = MonochromatorScanStream("Spectrum", self.mchr, self.ebeam, self.sgrh,
                                               opm=main_app.main_data.opm)

        # The settings to be displayed in the dialog
        # Trick: we use the same VAs as the stream, so they are directly synchronised
        self.startWavelength = self._mchr_s.startWavelength
        self.endWavelength = self._mchr_s.endWavelength
        self.numberOfPixels = self._mchr_s.numberOfPixels
        self.dwellTime = self._mchr_s.dwellTime

        self.filename = model.StringVA("a.h5")
        self.expectedDuration = model.VigilantAttribute(1, unit="s", readonly=True)

        # Update the expected duration when values change
        self.dwellTime.subscribe(self._update_exp_dur)
        self.numberOfPixels.subscribe(self._update_exp_dur)

    def _update_exp_dur(self, _=None):
        """
        Called when VA that affects the expected duration is changed
        """
        expt = self._mchr_s.estimateAcquisitionTime()
        if self._survey_s:
            expt += self._survey_s.estimateAcquisitionTime()

        # Use _set_value as it's read only
        self.expectedDuration._set_value(expt, force_write=True)

    def _get_new_filename(self):
        conf = get_acqui_conf()
        return os.path.join(
            conf.last_path,
            u"%s%s" % (time.strftime("%Y%m%d-%H%M%S"), conf.last_extension)
        )

    def _get_sem_survey(self):
        """
        Finds the SEM survey stream in the acquisition tab
        return (SEMStream or None): None if not found
        """
        tab_data = self.main_app.main_data.tab.value.tab_data_model
        for s in tab_data.streams.value:
            if isinstance(s, stream.SEMStream):
                return s

        logging.warning("No SEM survey stream found")
        return None

    def start(self):
        # Error message if not in acquisition tab + spot mode
        ct = self.main_app.main_data.tab.value
        if (
            ct.name != "sparc_acqui" or
            ct.tab_data_model.tool.value != TOOL_SPOT or
            None in ct.tab_data_model.spotPosition.value
        ):
            logging.info("Failed to start monochromator scan as no spot is selected")
            dlg = wx.MessageDialog(self.main_app.main_frame,
                                   "No spot is currently selected.\n"
                                   "You need to select the point where the spectrum will be "
                                   "acquired with monochromator scan.\n",
                                   caption="Monochromator scan",
                                   style=wx.OK | wx.ICON_WARNING)
            dlg.ShowModal()
            dlg.Destroy()
            return

        self._survey_s = self._get_sem_survey()
        self._update_exp_dur()

        # Create a window
        dlg = AcquisitionDialog(self, "Monochromator scan acquisition",
                                "Acquires a spectrum using the monochomator while scanning over "
                                "multiple wavelengths.\n\n"
                                "Specify the settings and start the acquisition.")

        self.filename.value = self._get_new_filename()
        dlg.addSettings(self, conf=self.vaconf)
        dlg.addButton("Close")
        dlg.addButton("Acquire", self.acquire, face_colour='blue')

        # Show the window, and wait until the acquisition is over
        ans = dlg.ShowModal()

        # Save the current folder
        conf = get_acqui_conf()
        conf.last_path = os.path.dirname(self.filename.value)

        # The window is closed
        if ans == 0:
            logging.info("Monochromator scan acquisition cancelled")
        elif ans == 1:
            logging.info("Monochromator scan acquisition completed")
        else:
            logging.debug("Unknown return code %d", ans)

        dlg.Destroy()

    def acquire(self, dlg):
        # Configure the monochromator stream according to the settings
        # TODO: read the value from spotPosition instead?
        self._mchr_s.emtTranslation.value = self.ebeam.translation.value
        strs = []
        if self._survey_s:
            strs.append(self._survey_s)
        strs.append(self._mchr_s)

        fn = self.filename.value
        exporter = dataio.find_fittest_converter(fn)

        # Stop the spot stream and any other stream playing to not interfere with the acquisition
        str_ctrl = self.main_app.main_data.tab.value.streambar_controller
        stream_paused = str_ctrl.pauseStreams()

        try:
            # opm is the optical path manager, that ensures the path is set to the monochromator
            f = acqmng.acquire(strs)
            dlg.showProgress(f)
            das, e = f.result()
        except CancelledError:
            pass
        finally:
            # The new convention is to not restart the streams afterwards
            # str_ctrl.resumeStreams(stream_paused)
            pass

        if not f.cancelled() and das:
            if e:
                logging.warning("Monochromator scan partially failed: %s", e)
            logging.debug("Will save data to %s", fn)
            exporter.export(fn, das)

            self.showAcquisition(fn)

        dlg.Close()


if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)

