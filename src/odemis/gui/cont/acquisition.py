# -*- coding: utf-8 -*-
"""
Created on 22 Aug 2012

@author: Éric Piel

Copyright © 2012-2013 Éric Piel, Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.


### Purpose ###

This module contains classes to control the actions related to the acquisition
of microscope images.
"""
from __future__ import division

from concurrent import futures
from concurrent.futures._base import CancelledError
import logging
import math
from odemis import model, dataio, acq
from odemis.gui import conf, acqmng
from odemis.gui.acqmng import preset_as_is
from odemis.gui.util import img, get_picture_folder, call_after, \
    wxlimit_invocation
from odemis.gui.win.acquisition import AcquisitionDialog, \
    ShowAcquisitionFileDialog
from odemis.util import units
import os
import re
import subprocess
import threading
import time
import wx
from wx.lib.pubsub import pub

from odemis.acq.stream import UNDEFINED_ROI


class SnapshotController(object):
    """ controller to handle snapshot acquisition in a
    "global" context. In particular, it needs to be aware of which tab/view
    is currently focused.
    """
    def __init__(self, main_data, main_frame):
        """
        main_data (MainGUIData): the representation of the microscope GUI
        main_frame: (wx.Frame): the whole GUI frame
        """
        self._main_data_model = main_data
        self._main_frame = main_frame
        self._anim_thread = None # for snapshot animation

        # For snapshot animation find the names of the active (=connected) screens
        # it's slow, so do it only at init (=expect not to change screen during
        # acquisition)
        self._outputs = self.get_display_outputs()

        # Link snapshot menu to snapshot action
        wx.EVT_MENU(self._main_frame,
            self._main_frame.menu_item_qacquire.GetId(),
            self.start_snapshot_viewport)

        # TODO: disable the menu if no focused view/no stream

    def start_snapshot_viewport(self, event):
        """Wrapper to run snapshot_viewport in a separate thread."""
        # Find out the current tab
        tab = self._main_data_model.tab.value
        thread = threading.Thread(target=self.snapshot_viewport, args=(tab,))
        thread.start()

    def snapshot_viewport(self, tab):
        """ Save a snapshot of the raw image from the focused view to the
        filesystem.
        tab (Tab): the current tab to save the snapshot from
        The name of the file follows the scheme date-time.tiff (e.g.,
        20120808-154812.tiff) and is located in the user's picture directory.
        """
        try:
            tab_data_model = tab.tab_data_model

            # TODO: allow user to chose the file format in preferences
            config = conf.get_acqui_conf()
            exporter = dataio.get_exporter(config.last_format)
            extention = config.last_extension

            # filename
            # always the "Pictures" user folder
            dirname = get_picture_folder() # TODO: use last path?
            basename = time.strftime("%Y%m%d-%H%M%S", time.localtime())
            filename = os.path.join(dirname, basename + extention)
            if os.path.exists(filename):
                msg = "File '%s' for snapshot already exists, cancelling snapshot"
                logging.warning(msg, filename)
                return

            # get currently focused view
            view = tab_data_model.focussedView.value
            if not view:
                try:
                    view = tab_data_model.views.value[0]
                except IndexError:
                    logging.warning("Failed to take snapshot, no view available")
                    return

            # Take all the streams available
            streams = tab_data_model.streams.value
            if not streams:
                logging.warning("Failed to take snapshot, no stream in tab %s",
                                tab.name)
                return

            self.start_snapshot_animation()

            # let's try to get a thumbnail
            if view.thumbnail.value is None:
                thumbnail = None
            else:
                # need to convert from wx.Image to ndimage
                thumbnail = img.wxImage2NDImage(view.thumbnail.value,
                                                keep_alpha=False)
                # add some basic info to the image
                mpp = view.mpp.value
                metadata = {model.MD_POS: view.view_pos.value,
                            model.MD_PIXEL_SIZE: (mpp, mpp),
                            model.MD_DESCRIPTION: "Composited image preview"}
                thumbnail = model.DataArray(thumbnail, metadata=metadata)

            # for each stream seen in the viewport
            raw_images = []
            for s in streams:
                data = s.raw # list of raw images for this stream (with metadata)
                # add the stream name to the image
                for d in data:
                    if model.MD_DESCRIPTION not in d.metadata:
                        d.metadata[model.MD_DESCRIPTION] = s.name.value
                raw_images.extend(data)

            # record everything to a file
            exporter.export(filename, raw_images, thumbnail)
            logging.info("Snapshot saved as file '%s'.", filename)
        except Exception:
            logging.exception("Failed to save snapshot")

    def start_snapshot_animation(self):
        """
        Starts an animation to indicate that a snapshot is taken
        Note: there is no way to stop it
        """
        # if there is already a thread: let it know to restart
        if self._anim_thread and self._anim_thread.is_alive():
            return

        # otherwise start a new animation thread
        self._anim_thread = threading.Thread(target=self.snapshot_animation,
                                             name="snapshot animation")
        self._anim_thread.start()

    def snapshot_animation(self, duration=0.6):
        """Show an animation indicating that a snapshot was taken.

        Change the brightness of all the screens to very high, and slowly
        decrease it back to the original value (1.0).

        duration (float): duration in seconds of the animation.
        """
        assert (0 < duration)
        brightness_orig = 1.0 # TODO: read the previous brightness

        # start with very bright and slowly decrease to 1.0
        try:
            brightness_max = 10.0
            start = time.time()
            end = start + duration
            self.set_output_brightness(self._outputs, brightness_max)
            time.sleep(0.1) # first is a bit longer
            now = time.time()
            while now <= end:
                # it should decrease quickly at the beginning and slowly at the
                # end => 1/x (x 1/max->1)
                pos = (now - start) / duration
                brightness = 1 / (1 / brightness_max + (1 - 1 / brightness_max) * pos)
                self.set_output_brightness(self._outputs, brightness)
                time.sleep(0.05) # ensure not to use too much CPU
                now = time.time()
        except subprocess.CalledProcessError:
            logging.info("Failed to run snapshot animation.")
        finally:
            # make sure we put it back
            time.sleep(0.05)
            try:
                self.set_output_brightness(self._outputs, brightness_orig)
            except subprocess.CalledProcessError:
                pass

    @staticmethod
    def get_display_outputs():
        """
        returns (set of strings): names of outputs used
        """
        xrandr_out = subprocess.check_output("xrandr")
        # only pick the "connected" outputs
        ret = re.findall("^(\\w+) connected ", xrandr_out, re.MULTILINE)
        return ret

    @staticmethod
    def set_output_brightness(outputs, brightness):
        """
        Set the brightness of all the display outputs given

        outputs (set of string): names of graphical output (screen) as xrandr
            uses them
        brightness (0<=float): brightness
        raises:
            exception in case change of brightness failed
        """
        assert (0 <= brightness)
        logging.debug("setting brightness to %f", brightness)
        if not len(outputs):
            return
        # to simplify, we don't use the XRANDR API, but just call xrandr command
        # we need to build a whole line with all the outputs, like:
        # xrandr --output VGA1 --brigthness 2 --output LVDS1 --brigthness 2
        args = ["xrandr"]
        for o in outputs:
            args += ["--output", o, "--brightness", "%f" % brightness]

        logging.debug("Calling: %s", " ".join(args))
        subprocess.check_call(args)


# TODO: Once the Secom acquisition is merged back into the main stream tab,
# the difference between controller should be small enough to merge a lots of
# things together
class SecomAcquiController(object):
    """ controller to handle high-res image acquisition in a
    "global" context. In particular, it needs to be aware of which viewport
    is currently focused, and block any change of settings during acquisition.
    """

    def __init__(self, tab_data, main_frame):
        """
        tab_data (MicroscopyGUIData): the representation of the microscope GUI
        main_frame: (wx.Frame): the frame which contains the 4 viewports
        """
        self._tab_data_model = tab_data
        self._main_frame = main_frame

        # Listen to "acquire image" button
        self._main_frame.btn_secom_acquire.Bind(wx.EVT_BUTTON, self.on_acquire)

        pub.subscribe(self.on_stream_changed, 'stream.ctrl')

    def on_stream_changed(self, streams_present, streams_visible, tab):
        """ Handler for pubsub 'stream.changed' messages """
        if tab != self._tab_data_model:
            return
        self._main_frame.btn_secom_acquire.Enable(streams_present and streams_visible)

    def on_acquire(self, evt):
        self.open_acquisition_dialog()

    def open_acquisition_dialog(self):
        secom_live_tab = self._tab_data_model.main.getTabByName("secom_live")

        # save the original settings
        main_settings_controller = secom_live_tab.settings_controller
        orig_settings = preset_as_is(main_settings_controller.entries)
        main_settings_controller.pause()
        # TODO: also pause the MicroscopeViews

        # pause all the live acquisitions
        main_stream_controller = secom_live_tab.stream_controller
        paused_streams = main_stream_controller.pauseStreams()

        # create the dialog
        acq_dialog = AcquisitionDialog(self._main_frame, self._tab_data_model)
        parent_size = [v * 0.77 for v in self._main_frame.GetSize()]

        try:
            acq_dialog.SetSize(parent_size)
            acq_dialog.Center()
            acq_dialog.ShowModal()
        finally:
            main_stream_controller.resumeStreams(paused_streams)

            acqmng.apply_preset(orig_settings)
            main_settings_controller.resume()

            # Make sure that the acquisition button is enabled again.
            self._main_frame.btn_secom_acquire.Enable()


class SparcAcquiController(object):
    """
    Takes care of the acquisition button and process on the Sparc acquisition
    tab.
    """

    def __init__(self, tab_data, main_frame, settings_controller, roa, vas):
        """
        main_frame: (wx.Frame): the frame which contains the 4 viewports
        tab_data (MicroscopyGUIData): the representation of the microscope GUI
        settings_controller (SettingsController)
        roa (VA): VA of the ROA
        vas (list of VAs): all the VAs which might affect acquisition (time)
        """
        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._main_frame = main_frame
        self._roa = roa
        self._vas = vas

        # For file selection
        self.conf = conf.get_acqui_conf()

        # for saving/restoring the settings
        self._settings_controller = settings_controller
        self._orig_settings = {} # Entry -> value to restore

        # TODO: this should be the date at which the user presses the acquire
        # button (or when the last settings were changed)!
        # At least, we must ensure it's a new date after the acquisition
        # is done.
        # Filename to save the acquisition
        self.filename = model.StringVA(self._get_default_filename())
        self.filename.subscribe(self._onFilename, init=True)

        # For acquisition
        # a ProgressiveFuture if the acquisition is going on
        self.btn_acquire = self._main_frame.btn_sparc_acquire
        self.btn_change_file = self._main_frame.btn_sparc_change_file
        self.btn_cancel = self._main_frame.btn_sparc_cancel
        self.acq_future = None
        self._prev_left = None
        self.gauge_acq = self._main_frame.gauge_sparc_acq
        self.lbl_acqestimate = self._main_frame.lbl_sparc_acq_estimate

        # TODO: share an executor with the whole GUI.
        self._executor = futures.ThreadPoolExecutor(max_workers=2)

        # Link buttons
        self.btn_acquire.Bind(wx.EVT_BUTTON, self.on_acquisition)
        self.btn_change_file.Bind(wx.EVT_BUTTON, self.on_change_file)
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_cancel)

        self.gauge_acq.Hide()
        self._main_frame.Layout()

        # TODO: we need to be informed if the user closes suddenly the window
        # self.Bind(wx.EVT_CLOSE, self.on_close)

        # Event binding
        pub.subscribe(self.on_setting_change, 'setting.changed')
        # TODO We should also listen to repetition, in case it's modified after we've
        # received the new ROI. Or maybe always compute acquisition time a bit delayed?
        for va in vas:
            va.subscribe(self.onAnyVA)
        roa.subscribe(self.onROA, init=True)

    def __del__(self):
        self._executor.shutdown(wait=False)

    def _get_default_filename(self):
        """
        Return a good default filename
        """
        return os.path.join(self.conf.last_path,
                            u"%s%s" % (time.strftime("%Y%m%d-%H%M%S"),
                                             self.conf.last_extension)
                            )

    def _onFilename(self, name):
        """ updates the GUI when the filename is updated """
        # decompose into path/file
        path, base = os.path.split(name)
        self._main_frame.txt_destination.SetValue(unicode(path))
        # show the end of the path (usually more important)
        self._main_frame.txt_destination.SetInsertionPointEnd()
        self._main_frame.txt_filename.SetValue(unicode(base))

    def onROA(self, roi):
        """ updates the acquire button according to the acquisition ROI """
        self.btn_acquire.Enable(roi != UNDEFINED_ROI)
        self.update_acquisition_time() # to update the message

    def onAnyVA(self, val):
        """
        Called whenever a VA which might affect the acquisition is modified
        """
        self.update_acquisition_time() # to update the message

    def on_setting_change(self, setting_ctrl):
        """ Handler for pubsub 'setting.changed' messages """
        self.update_acquisition_time()

    def on_change_file(self, evt):
        """
        Shows a dialog to change the path, name, and format of the acquisition
        file.
        returns nothing, but updates .filename and .conf
        """
        new_name = ShowAcquisitionFileDialog(self._main_frame, self.filename.value)
        self.filename.value = new_name

    @wxlimit_invocation(1) # max 1/s
    @call_after
    def update_acquisition_time(self):

        if self._roa.value == UNDEFINED_ROI:
            # TODO: update the default text to be the same
            txt = "Region of acquisition needs to be selected"
        else:
            streams = self._tab_data_model.acquisitionView.getStreams()
            acq_time = acq.estimateTime(streams)
            self.gauge_acq.Range = 100 * acq_time
            acq_time = math.ceil(acq_time) # round a bit pessimistic
            txt = "Estimated time is {}."
            txt = txt.format(units.readable_time(acq_time))

        self.lbl_acqestimate.SetLabel(txt)


    def _pause_settings(self):
        """
        Pause the settings of the GUI and save the values for restoring them later
        """
        # save the original settings
        self._orig_settings = preset_as_is(self._settings_controller.entries)
        self._settings_controller.pause()

        # FIXME: it doesn't seem to the freeze the settings

        # TODO: also freeze the MicroscopeView (for now we just pause the streams)
        # pause all the live acquisitions
        live_streams = self._tab_data_model.focussedView.value.getStreams()
        for s in live_streams:
            s.is_active.value = False
            s.should_update.value = False

    def _resume_settings(self):
        """
        Resume (unfreeze) the settings in the GUI and make sure the value are
        back to the previous value
        """
        live_streams = self._tab_data_model.focussedView.value.getStreams()
        for s in live_streams:
            s.should_update.value = True
            s.is_active.value = True

        acqmng.apply_preset(self._orig_settings)
        self._settings_controller.resume()

        # Make sure that the acquisition button is enabled again.
        self._main_frame.btn_sparc_acquire.Enable()

    def _reset_acquisition_gui(self, text=None, keep_filename=False):
        """
        Set back every GUI elements to be ready for the next acquisition
        text (None or str): a (error) message to display instead of the
          estimated acquisition time
        keep_filename (bool): if True, will not update the filename
        """
        self.btn_cancel.Hide()
        self.btn_acquire.Enable()
        self._main_frame.Layout()
        self._resume_settings()

        self._main_data_model.acquiring = False

        if not keep_filename:
            # change filename, to ensure not overwriting anything
            self.filename.value = self._get_default_filename()

        if text is not None:
            self.lbl_acqestimate.SetLabel(text)
        else:
            self.update_acquisition_time()

    def _show_acquisition(self, data, acqfile):
        """
        Show the acquired data (saved into a file) in the analysis tab.
        data (list of DataFlow): all the raw data acquired
        acqfile (File): file object to which the data was saved
        """
        # get the analysis tab
        analysis_tab = self._main_data_model.getTabByName("analysis")
        analysis_tab.display_new_data(acqfile.name, data)

        # show the new tab
        self._main_data_model.tab.value = analysis_tab

    def on_acquisition(self, evt):
        """
        Start the acquisition (really)
        Similar to win.acquisition.on_acquire()
        """
        self._pause_settings()
        self.btn_acquire.Disable()
        self.btn_cancel.Enable()

        # the range of the progress bar was already set in
        # update_acquisition_time()
        self._prev_left = None
        self.gauge_acq.Value = 0
        self.gauge_acq.Show()
        self.btn_cancel.Show()

        self._main_data_model.is_acquiring.value = True

        # FIXME: probably not the whole window is required, just the file settings
        self._main_frame.Layout() # to put the gauge at the right place

        # start acquisition + connect events to callback
        streams = self._tab_data_model.acquisitionView.getStreams()

        self.acq_future = acq.acquire(streams)
        self.acq_future.add_update_callback(self.on_acquisition_upd)
        self.acq_future.add_done_callback(self.on_acquisition_done)

    def on_cancel(self, evt):
        """
        Called during acquisition when pressing the cancel button
        """
        if not self.acq_future:
            msg = "Tried to cancel acquisition while it was not started"
            logging.warning(msg)
            return

        self.acq_future.cancel()
        self._main_data_model.is_acquiring.value = False
        # all the rest will be handled by on_acquisition_done()

    def _export_to_file(self, acq_future):
        """
        return (list of DataArray, filename): data exported and filename
        """
        st = self._tab_data_model.acquisitionView.stream_tree
        thumb = acq.computeThumbnail(st, acq_future)
        data = acq_future.result()
        filename = self.filename.value
        exporter = dataio.get_exporter(self.conf.last_format)
        exporter.export(filename, data, thumb)
        logging.info(u"Acquisition saved as file '%s'.", filename)
        return data, filename

    @call_after
    def on_acquisition_done(self, future):
        """
        Callback called when the acquisition is finished (either successfully or
        cancelled)
        """
        self.btn_cancel.Disable()
        self._main_data_model.is_acquiring.value = False

        try:
            future.result()
        except CancelledError:
            # hide progress bar (+ put pack estimated time)
            self.gauge_acq.Hide()
            # don't change filename => we can reuse it
            self._reset_acquisition_gui(keep_filename=True)
            return
        except Exception:
            # leave the gauge, to give a hint on what went wrong.
            logging.exception("Acquisition failed")
            self._reset_acquisition_gui("Acquisition failed.")
            return

        # make sure the progress bar is at 100%
        self.gauge_acq.Value = self.gauge_acq.Range

        # save result to file
        self.lbl_acqestimate.SetLabel("Saving file...")
        # on big acquisitions, it can take ~20s
        sf = self._executor.submit(self._export_to_file, future)
        sf.add_done_callback(self.on_file_export_done)

    @call_after
    def on_acquisition_upd(self, future, past, left):
        """
        Callback called during the acquisition to update on its progress
        past (float): number of s already past
        left (float): estimated number of s left
        """
        if future.done():
            # progress bar and text is handled by on_acquisition_done
            return

        # progress bar: past / past+left
        can_update = True
        try:
            ratio = past / (past + left)
            # Don't update gauge if ratio reduces
            prev_ratio = self.gauge_acq.Value / self.gauge_acq.Range
            logging.debug("current ratio %g, old ratio %g", ratio * 100, prev_ratio * 100)
            if (self._prev_left is not None and
                prev_ratio - 0.1 < ratio < prev_ratio):
                can_update = False
        except ZeroDivisionError:
            pass

        if can_update:
            logging.debug("updating the progress bar to %f/%f", past, past + left)
            self.gauge_acq.Range = 100 * (past + left)
            self.gauge_acq.Value = 100 * past

        # Time left
        left = math.ceil(left) # pessimistic
        # Avoid back and forth estimation => don't increase unless really huge (> 5s)
        if (self._prev_left is not None and
            0 < left - self._prev_left < 5):
            logging.debug("No updating progress bar as new estimation is %g s "
                          "while the previous was only %g s",
                          left, self._prev_left)
            return

        self._prev_left = left

        if left > 2:
            lbl_txt = "%s left." % units.readable_time(left)
            self.lbl_acqestimate.SetLabel(lbl_txt)
        else:
            # don't be too precise
            self.lbl_acqestimate.SetLabel("a few seconds left.")

    @call_after
    def on_file_export_done(self, future):
        """
        Callback called when the acquisition is finished (either successfully or
        cancelled)
        """
        # hide progress bar
        self.gauge_acq.Hide()

        try:
            data, filename = future.result()
        except Exception:
            logging.exception("Saving acquisition failed")
            self._reset_acquisition_gui("Saving acquisition file failed")
            return

        # display in the analysis tab
        self._show_acquisition(data, open(filename))
        self._reset_acquisition_gui()
