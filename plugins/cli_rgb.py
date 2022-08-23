# -*- coding: utf-8 -*-
"""
Created on Wed Jul 05 2017

@author: Toon Coenen and Éric Piel

Plugin that allows collecting 3 PMT images with drift correction in between
for making RGB PMT images.

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
"""

from collections import OrderedDict
from concurrent.futures import CancelledError
import logging
from odemis import dataio, model
import odemis.util
from odemis.acq import stream, drift, acqmng
from odemis.acq.stream import UNDEFINED_ROI
from odemis.dataio import get_available_formats
import odemis.gui
from odemis.gui.conf import get_acqui_conf
from odemis.gui.conf import util
from odemis.gui.plugin import Plugin, AcquisitionDialog
from odemis.gui.util import formats_to_wildcards, get_home_folder
import os.path
import time
import wx

try:
    import configparser
except ImportError:  # Python 2
    import ConfigParser as configparser

CONF_FILE = os.path.join(get_home_folder(), ".config", "odemis", "cli_rgb.ini")


class RGBCLIntensity(Plugin):
    name = "RGB CL-intensity"
    __version__ = "1.2"
    __author__ = u"Toon Coenen & Éric Piel"
    __license__ = "GNU General Public License 2"

    vaconf = OrderedDict((
        ("filter1", {
            "label": "Blue",
            "choices": util.format_band_choices,
        }),
        ("filter2", {
            "label": "Green",
            "choices": util.format_band_choices,
        }),
        ("filter3", {
            "label": "Red",
            "choices": util.format_band_choices,
        }),
        ("filename", {
            "control_type": odemis.gui.CONTROL_SAVE_FILE,
            "wildcard": formats_to_wildcards(get_available_formats(os.O_WRONLY))[0],
        }),
        ("expectedDuration", {
        }),
    ))

    def __init__(self, microscope, main_app):
        super(RGBCLIntensity, self).__init__(microscope, main_app)

        # Can only be used on a SPARC with a CL-intensity detector
        if not microscope:
            return
        try:
            self.ebeam = model.getComponent(role="e-beam")
            self.cldetector = model.getComponent(role="cl-detector")
            self.filterwheel = model.getComponent(role="cl-filter")
            self.sed = model.getComponent(role="se-detector")
            # We could also check the filter wheel has at least 3 filters, but
            # let's not be too picky, if the user has installed the plugin, he
            # probably wants to use it anyway.
        except LookupError:
            logging.info("Hardware not found, cannot use the RGB CL plugin")
            return

        # The SEM survey and CLi stream (will be updated when showing the window)
        self._survey_s = None
        self._cl_int_s = None
        self._acqui_tab = main_app.main_data.getTabByName("sparc_acqui").tab_data_model

        # The settings to be displayed in the dialog
        # TODO: pick better default filters than first 3 filters
        # => based on the wavelengths fitting best RGB, or the names (eg, "Blue"),
        # and avoid "pass-through".
        fbchoices = self.filterwheel.axes["band"].choices
        if isinstance(fbchoices, dict):
            fbvalues = sorted(fbchoices.keys())
        else:
            fbvalues = fbchoices
        # FloatEnumerated because filter positions can be in rad (ie, not int positions)
        self.filter1 = model.FloatEnumerated(fbvalues[0],
                                             choices=fbchoices)
        self.filter2 = model.FloatEnumerated(fbvalues[min(1, len(fbvalues) - 1)],
                                             choices=fbchoices)
        self.filter3 = model.FloatEnumerated(fbvalues[min(2, len(fbvalues) - 1)],
                                             choices=fbchoices)

        self._filters = [self.filter1, self.filter2, self.filter3]
        self._colours = [(0, 0, 255), (0, 255, 0), (255, 0, 0)]  # B, G, R

        self.filename = model.StringVA("a.tiff")
        self.expectedDuration = model.VigilantAttribute(1, unit="s", readonly=True)

        self.addMenu("Acquisition/RGB CL intensity...", self.start)

    def _read_config(self):
        """
        Updates the filter values based on the content of the config file
        It will not fail (if there is no config file, or the config file is incorrect).
        In the worst case, it will not update the filter values.
        """
        try:
            config = configparser.SafeConfigParser()  # Note: in Python 3, this is now also just called "ConfigParser"
            config.read(CONF_FILE)  # Returns empty config if no file
            for fname, va in zip(("blue", "green", "red"), self._filters):
                fval = config.getfloat("filters", fname)
                # Pick the same/closest value if it's available in the choices, always returns something valid
                va.value = odemis.util.find_closest(fval, va.choices)
                logging.debug("Updated %s to %s (from config %s)", fname, va.value, fval)

        except (configparser.NoOptionError, configparser.NoSectionError) as ex:
            logging.info("Config file is not existing or complete, no restoring filter values: %s", ex)
        except Exception:
            logging.exception("Failed to open the config file")

    def _write_config(self):
        """
        Store the filter values into the config file
        """
        try:
            config = configparser.SafeConfigParser()
            config.add_section("filters")
            config.set("filters", "blue", "%f" % self.filter1.value)
            config.set("filters", "green", "%f" % self.filter2.value)
            config.set("filters", "red", "%f" % self.filter3.value)

            with open(CONF_FILE, "w") as configfile:
                config.write(configfile)
        except Exception:
            logging.exception("Failed to save the config file")

    def _update_exp_dur(self, _=None):
        """
        Called when VA that affects the expected duration is changed
        """
        at = self.estimateAcquisitionTime()

        # Use _set_value as it's read only
        self.expectedDuration._set_value(round(at), force_write=True)

    def _calc_acq_times(self):
        """
        Calculate exposure times for different elements of the acquisition.
        return (3 float): in s
        """
        dt_survey = 0
        dt_cl = 0
        dt_drift = 0

        if self._survey_s:
            dt_survey = self._survey_s.estimateAcquisitionTime()

        if self._cl_int_s:
            dt_cl = self._cl_int_s.estimateAcquisitionTime()

        # For each CL filter acquisition, the drift correction will run once
        # (*in addition* to the standard in-frame drift correction)
        dc = self._acqui_tab.driftCorrector
        if dc.roi.value != UNDEFINED_ROI:
            drift_est = drift.AnchoredEstimator(self.ebeam, self.sed,
                                    dc.roi.value, dc.dwellTime.value)
            dt_drift = drift_est.estimateAcquisitionTime() + 0.1

        return dt_survey, dt_cl, dt_drift

    def estimateAcquisitionTime(self):
        """
        Estimate the time it will take for the measurement.
        The number of pixels still has to be defined in the stream part
        """
        dt_survey, dt_cl, dt_drift = self._calc_acq_times()
        return dt_survey + len(self._filters) * (dt_cl + dt_drift)

    def _get_new_filename(self):
        conf = get_acqui_conf()
        # Use TIFF by default, as it's a little bit more user-friendly for simple
        # coloured images.
        return os.path.join(
            conf.last_path,
            u"%s%s" % (time.strftime("%Y%m%d-%H%M%S"), ".tiff")
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

    def _get_cl_intensity(self):
        """
        Finds the CL intensity acquisition (aka MD) stream in the acquisition tab
        return (SEMStream or None): None if not found
        """
        tab_data = self.main_app.main_data.tab.value.tab_data_model

        # Look for the MultiDetector stream which contains a CL intensity stream
        for mds in tab_data.acquisitionStreams:
            if not isinstance(mds, stream.MultipleDetectorStream):
                continue
            for ss in mds.streams:
                if isinstance(ss, stream.CLSettingsStream):
                    return mds

        logging.warning("No CL intensity stream found")
        return None

    def _pause_streams(self):
        """
        return (list of streams): the streams paused
        """
        try:
            str_ctrl = self.main_app.main_data.tab.value.streambar_controller
        except AttributeError:  # Odemis v2.6 and earlier versions
            str_ctrl = self.main_app.main_data.tab.value.stream_controller
        return str_ctrl.pauseStreams()

    def start(self):
        # Check the acquisition tab is open, and a CL-intensity stream is available
        ct = self.main_app.main_data.tab.value
        if ct.name == "sparc_acqui":
            cls = self._get_cl_intensity()
        else:
            cls = None
        if not cls:
            logging.info("Failed to start RGB CL intensity stream")
            dlg = wx.MessageDialog(self.main_app.main_frame,
                                   "No CL-intensity stream is currently open.\n"
                                   "You need to open a CL intensity stream "
                                   "and set the acquisition parameters.\n",
                                   caption="RGB CL intensity",
                                   style=wx.OK | wx.ICON_WARNING)
            dlg.ShowModal()
            dlg.Destroy()
            return

        # Normally, since Odemis v3.1, all CLSettingsStreams on systems with a cl-filter
        # have a "local axis" as a VA "axisFilter".
        assert any(hasattr(s, "axisFilter") for s in cls.streams)

        self._pause_streams()

        self._read_config()  # Restore filter values from the config file

        # immediately switch optical path, to save time
        self.main_app.main_data.opm.setPath(cls)  # non-blocking

        # Get survey stream too
        self._survey_s = self._get_sem_survey()
        self._cl_int_s = cls

        self._update_exp_dur()

        # Create a window
        dlg = AcquisitionDialog(self, "RGB CL intensity acquisition",
                                "Acquires a RGB CL-intensity image\n"
                                "Specify the relevant settings and start the acquisition\n"
                                )

        self.filename.value = self._get_new_filename()
        dlg.addSettings(self, conf=self.vaconf)
        dlg.addButton("Close")
        dlg.addButton("Acquire", self.acquire, face_colour='blue')

        # Show the window, and wait until the acquisition is over
        ans = dlg.ShowModal()

        # The window is closed
        if ans == 0:
            logging.debug("RGB CL intensity acquisition cancelled")
        elif ans == 1:
            logging.debug("RGB CL intensity acquisition completed")
        else:
            logging.warning("Unknown return code %d", ans)

        self._write_config()  # Store the filter values to restore them on next time

        # Make sure we don't hold reference to the streams forever
        self._survey_s = None
        self._cl_int_s = None

        dlg.Destroy()

    def acquire(self, dlg):
        # Stop the spot stream and any other stream playing to not interfere with the acquisition
        self._pause_streams()

        # We use the acquisition CL intensity stream, so there is a concurrent
        # SEM acquisition (in addition to the survey). The drift correction is run both
        # during the acquisition, and in-between each acquisition. The drift
        # between each acquisition is corrected by updating the metadata. So
        # it's some kind of post-processing compensation. The advantage is that
        # it doesn't affect the data, and if the entire field of view is imaged,
        # it still works properly, but when opening in another software (eg,
        # ImageJ), that compensation will not be applied automatically).
        # Alternatively, the images could be cropped to just the region which is
        # common for all the acquisitions, but there might then be data loss.
        # Note: The compensation could also be done by updating the ROI of the
        # CL stream. However, in the most common case, the user will acquire the
        # entire area, so drift compensation cannot be applied. We could also
        # use SEM concurrent stream and measure drift afterwards but that
        # doubles the dwell time).
        dt_survey, dt_clint, dt_drift = self._calc_acq_times()
        cl_set_s = next(s for s in self._cl_int_s.streams if hasattr(s, "axisFilter"))

        das = []
        fn = self.filename.value
        exporter = dataio.find_fittest_converter(fn)

        # Prepare the Future to represent the acquisition progress, and cancel
        dur = self.expectedDuration.value
        end = time.time() + dur
        ft = model.ProgressiveFuture(end=end)

        # Allow to cancel by cancelling also the sub-task
        def canceller(future):
            # To be absolutely correct, there should be a lock, however, in
            # practice in the worse case the task will run a little longer before
            # stopping.
            if future._subf:
                logging.debug("Cancelling sub future %s", future._subf)
                return future._subf.cancel()

        ft._subf = None  # sub-future corresponding to the task currently happening
        ft.task_canceller = canceller  # To allow cancelling while it's running

        # Indicate the work is starting now
        ft.set_running_or_notify_cancel()
        dlg.showProgress(ft)

        try:
            # acquisition of SEM survey
            if self._survey_s:
                ft._subf = acqmng.acquire([self._survey_s], self.main_app.main_data.settings_obs)
                d, e = ft._subf.result()
                das.extend(d)
                if e:
                    raise e

            if ft.cancelled():
                raise CancelledError()

            dur -= dt_survey
            ft.set_progress(end=time.time() + dur)

            # Extra drift correction between each filter
            dc_roi = self._acqui_tab.driftCorrector.roi.value
            dc_dt = self._acqui_tab.driftCorrector.dwellTime.value

            # drift correction vector
            tot_dc_vect = (0, 0)
            if dc_roi != UNDEFINED_ROI:
                drift_est = drift.AnchoredEstimator(self.ebeam, self.sed,
                                                    dc_roi, dc_dt)
                drift_est.acquire()
                dur -= dt_drift
                ft.set_progress(end=time.time() + dur)
            else:
                drift_est = None

            # Loop over the filters, for now it's fixed to 3 but this could be flexible
            for fb, co in zip(self._filters, self._colours):
                cl_set_s.axisFilter.value = fb.value
                logging.debug("Using band %s", fb.value)
                ft.set_progress(end=time.time() + dur)

                # acquire CL stream
                ft._subf = acqmng.acquire([self._cl_int_s], self.main_app.main_data.settings_obs)
                d, e = ft._subf.result()
                if e:
                    raise e
                if ft.cancelled():
                    raise CancelledError()
                dur -= dt_clint
                ft.set_progress(end=time.time() + dur)

                if drift_est:
                    drift_est.acquire()
                    dc_vect = drift_est.estimate()
                    pxs = self.ebeam.pixelSize.value
                    tot_dc_vect = (tot_dc_vect[0] + dc_vect[0] * pxs[0],
                                   tot_dc_vect[1] - dc_vect[1] * pxs[1])  # Y is inverted in physical coordinates
                    dur -= dt_drift
                    ft.set_progress(end=time.time() + dur)

                # Convert the CL intensity stream into a "fluo" stream so that it's nicely displayed (in colour) in the viewer
                for da in d:
                    # Update the center position based on drift
                    pos = da.metadata[model.MD_POS]
                    logging.debug("Correcting position for drift by %s m", tot_dc_vect)
                    pos = tuple(p + dc for p, dc in zip(pos, tot_dc_vect))
                    da.metadata[model.MD_POS] = pos

                    if model.MD_OUT_WL not in da.metadata:
                        # check it's not the SEM concurrent stream
                        continue
                    # Force the colour, which forces it to be a FluoStream when
                    # opening it in the analysis tab, for nice colour merging.
                    da.metadata[model.MD_USER_TINT] = co

                das.extend(d)
                if ft.cancelled():
                    raise CancelledError()

            ft.set_result(None)  # Indicate it's over

        except CancelledError as ex:
            logging.debug("Acquisition cancelled")
            return
        except Exception as ex:
            logging.exception("Failure during RGB CL acquisition")
            ft.set_exception(ex)
            # TODO: show the error in the plugin window
            return

        if ft.cancelled() or not das:
            return

        logging.debug("Will save data to %s", fn)
        exporter.export(fn, das)
        self.showAcquisition(fn)
        dlg.Close()
