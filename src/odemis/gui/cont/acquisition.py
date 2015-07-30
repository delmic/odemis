# -*- coding: utf-8 -*-
"""
Created on 22 Aug 2012

@author: Éric Piel

Copyright © 2012-2013 Éric Piel, Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

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
from odemis.acq import align, stream
from odemis.acq.align.spot import OBJECTIVE_MOVE
from odemis.acq.stream import UNDEFINED_ROI
from odemis.gui import conf, acqmng
from odemis.gui.acqmng import preset_as_is
from odemis.gui.comp.canvas import CAN_DRAG, CAN_FOCUS
from odemis.gui.comp.popup import Message
from odemis.gui.model import TOOL_NONE
from odemis.gui.util import img, get_picture_folder, call_in_wx_main, \
    wxlimit_invocation
from odemis.gui.util.widgets import ProgressiveFutureConnector
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

import odemis.gui.model as guimod


class SnapshotController(object):
    """ Controller to handle snapshot acquisition in a 'global' context.

    In particular, it needs to be aware of which tab/view is currently focused.

    """

    def __init__(self, main_data, main_frame):
        """
        main_data (MainGUIData): the representation of the microscope GUI
        main_frame: (wx.Frame): the whole GUI frame
        """

        self._main_data_model = main_data
        self._main_frame = main_frame
        self._anim_thread = None # for snapshot animation

        # For snapshot animation find the names of the active (=connected)
        # screens it's slow, so do it only at init (=expect not to change screen
        # during acquisition)
        self._outputs = self.get_display_outputs()

        # Link snapshot menu to snapshot action
        wx.EVT_MENU(self._main_frame,
            self._main_frame.menu_item_snapshot.GetId(),
            self.start_snapshot_viewport)

        wx.EVT_MENU(self._main_frame,
            self._main_frame.menu_item_snapshot_as.GetId(),
            self.start_snapshot_as_viewport)

        self._main_data_model.tab.subscribe(self.on_tab_change, init=True)

    def on_tab_change(self, tab):
        """ Subscribe to the foccusedView VA of the current tab """
        tab.tab_data_model.streams.subscribe(self.on_streams_change, init=True)

    def on_streams_change(self, streams):
        """ Enable Snapshot menu items iff the tab has at least one stream """

        enabled = (len(streams) > 0)
        self._main_frame.menu_item_snapshot.Enable(enabled)
        self._main_frame.menu_item_snapshot_as.Enable(enabled)

    def start_snapshot_viewport(self, event):
        """ Wrapper to run snapshot_viewport in a separate thread."""
        # Find out the current tab
        tab, filepath, exporter = self._get_snapshot_info(dialog=False)
        if None not in (tab, filepath, exporter):
            thread = threading.Thread(target=self.snapshot_viewport,
                                      args=(tab, filepath, exporter, True))
            thread.start()

    def start_snapshot_as_viewport(self, event):
        """ Wrapper to run snapshot_viewport in a separate thread."""
        # Find out the current tab
        tab, filepath, exporter = self._get_snapshot_info(dialog=True)
        if None not in (tab, filepath, exporter):
            thread = threading.Thread(target=self.snapshot_viewport,
                                      args=(tab, filepath, exporter, False))
            thread.start()

    def _get_snapshot_info(self, dialog=False):
        config = conf.get_acqui_conf()

        tab, filepath, exporter = self._main_data_model.tab.value, None, None

        extension = config.last_extension
        basename = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        if dialog:
            filepath = os.path.join(config.last_path, basename + extension)
            # filepath will be None if cancelled by user
            filepath = ShowAcquisitionFileDialog(self._main_frame, filepath)
        else:
            dirname = get_picture_folder()
            filepath = os.path.join(dirname, basename + extension)

            if os.path.exists(filepath):
                msg = "File '%s' already exists, cancelling snapshot"
                logging.warning(msg, filepath)
                tab, filepath = None, None

        exporter = dataio.get_converter(config.last_format)

        return tab, filepath, exporter

    def snapshot_viewport(self, tab, filepath, exporter, anim):
        """ Save a snapshot of the raw image from the focused view to the
        filesystem.

        :param tab: (Tab) the current tab to save the snapshot from
        :param filepath: (str) full path to the destination file
        :param exporter: (func) exporter to use for writing the file
        :param anim: (bool) if True will show an animation

        When no dialog is shown, the name of the file will follow the scheme
        `date`-`time`.tiff (e.g., 20120808-154812.tiff) and it will be saved
        in the user's picture directory.

        """

        try:
            tab_data_model = tab.tab_data_model

            # Take all the streams available
            streams = tab_data_model.streams.value
            if not streams:
                logging.info("Failed to take snapshot, no stream in tab %s",
                                tab.name)
                return

            if anim:
                self.start_snapshot_animation()

            # get currently focused view
            view = tab_data_model.focussedView.value
            if not view:
                try:
                    view = tab_data_model.views.value[0]
                except IndexError:
                    view = None

            # let's try to get a thumbnail
            if not view or view.thumbnail.value is None:
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

            Message.show_message(self._main_frame,
                                 "Snapshot saved in %s" % (filepath,),
                                 timeout=3
                                 )
            # record everything to a file
            exporter.export(filepath, raw_images, thumbnail)

            logging.info("Snapshot saved as file '%s'.", filepath)
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
        # xrandr --output VGA1 --brightness 2 --output LVDS1 --brightness 2
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

        # Only possible to acquire if there are streams, and the chamber is
        # under vacuum
        tab_data.streams.subscribe(self.on_stream_chamber)
        tab_data.main.chamberState.subscribe(self.on_stream_chamber, init=True)

    def on_stream_chamber(self, unused):
        """
        Called when chamber state or streams change.
        Used to update the acquire button state
        """
        st_present = not not self._tab_data_model.streams.value
        ch_vacuum = (self._tab_data_model.main.chamberState.value
                        in {guimod.CHAMBER_VACUUM, guimod.CHAMBER_UNKNOWN})
        self._main_frame.btn_secom_acquire.Enable(st_present and ch_vacuum)

    def on_acquire(self, evt):
        self.open_acquisition_dialog()

    def open_acquisition_dialog(self):
        secom_live_tab = self._tab_data_model.main.getTabByName("secom_live")

        # save the original settings
        settingsbar_controller = secom_live_tab.settingsbar_controller
        orig_settings = preset_as_is(settingsbar_controller.entries)
        settingsbar_controller.pause()
        settingsbar_controller.enable(False)
        # TODO: also pause the MicroscopeViews

        # pause all the live acquisitions
        streambar_controller = secom_live_tab.streambar_controller
        paused_streams = streambar_controller.pauseStreams()
        streambar_controller.pause()
        streambar_controller.enable(False)

        # create the dialog
        acq_dialog = AcquisitionDialog(self._main_frame, self._tab_data_model)
        parent_size = [v * 0.77 for v in self._main_frame.GetSize()]

        try:
            acq_dialog.SetSize(parent_size)
            acq_dialog.Center()
            acq_dialog.ShowModal()
        finally:
            streambar_controller.resumeStreams(paused_streams)

            acqmng.apply_preset(orig_settings)

            settingsbar_controller.enable(True)
            settingsbar_controller.resume()

            streambar_controller.enable(True)
            streambar_controller.resume()

            # Make sure that the acquisition button is enabled again.
            self._main_frame.btn_secom_acquire.Enable()


class SparcAcquiController(object):
    """
    Takes care of the acquisition button and process on the Sparc acquisition
    tab.
    """

    def __init__(self, tab_data, main_frame, streambar_controller):
        """
        tab_data (MicroscopyGUIData): the representation of the microscope GUI
        main_frame: (wx.Frame): the frame which contains the 4 viewports
        stream_ctrl (StreamBarController): controller to pause/resume the streams
        """
        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._main_frame = main_frame
        self._streambar_controller = streambar_controller

        # For file selection
        self.conf = conf.get_acqui_conf()

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
        self.gauge_acq = self._main_frame.gauge_sparc_acq
        self.lbl_acqestimate = self._main_frame.lbl_sparc_acq_estimate
        self._acq_future_connector = None

        self._stream_paused = ()

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

        # Listen to change of streams to update the acquisition time
        self._prev_streams = set() # set of streams already listened to
        tab_data.streams.subscribe(self._onStreams, init=True)
        # also listen to .semStream, which is not in .streams
        for va in self._get_settings_vas(tab_data.semStream):
            va.subscribe(self._onAnyVA)

        self._roa = tab_data.semStream.roi
        self._roa.subscribe(self._onROA, init=True)

    def __del__(self):
        self._executor.shutdown(wait=False)

    # black list of VAs name which are known to not affect the acquisition time
    VAS_NO_ACQUSITION_EFFECT = ("image", "autoBC", "intensityRange", "histogram",
                                "is_active", "should_update", "status")

    def _get_settings_vas(self, stream):
        """
        Find all the VAs of a stream which can potentially affect the acquisition time
        return (set of VAs)
        """
        nvas = model.getVAs(stream) # name -> va
        vas = set()
        # remove some VAs known to not affect the acquisition time
        for n, va in nvas.items():
            if n not in self.VAS_NO_ACQUSITION_EFFECT:
                vas.add(va)
        return vas

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

    def _onROA(self, roi):
        """ updates the acquire button according to the acquisition ROI """
        self.btn_acquire.Enable(roi != UNDEFINED_ROI)
        self.update_acquisition_time()  # to update the message

    def _onStreams(self, streams):
        """
        Called when streams are added/deleted. Used to listen to settings change
         and update the acquisition time.
        """
        streams = set(streams)
        # remove subscription for streams that were deleted
        for s in (self._prev_streams - streams):
            for va in self._get_settings_vas(s):
                va.unsubscribe(self._onAnyVA)

        # add subscription for new streams
        for s in (streams - self._prev_streams):
            for va in self._get_settings_vas(s):
                va.subscribe(self._onAnyVA)

        self._prev_streams = streams
        self.update_acquisition_time()  # to update the message

    def _onAnyVA(self, val):
        """
        Called whenever a VA which might affect the acquisition is modified
        """
        self.update_acquisition_time() # to update the message

    def on_change_file(self, evt):
        """
        Shows a dialog to change the path, name, and format of the acquisition
        file.
        returns nothing, but updates .filename and .conf
        """
        new_name = ShowAcquisitionFileDialog(self._main_frame, self.filename.value)
        if new_name is not None:
            self.filename.value = new_name

    @wxlimit_invocation(1) # max 1/s
    def update_acquisition_time(self):

        if self._roa.value == UNDEFINED_ROI:
            # TODO: update the default text to be the same
            txt = "Region of acquisition needs to be selected"
        else:
            streams = self._tab_data_model.acquisitionView.getStreams()
            acq_time = acq.estimateTime(streams)
            acq_time = math.ceil(acq_time) # round a bit pessimistic
            txt = "Estimated time is {}."
            txt = txt.format(units.readable_time(acq_time))

        self.lbl_acqestimate.SetLabel(txt)

    def _pause_streams(self):
        """
        Freeze the streams settings and ensure no stream is playing
        """
        self._stream_paused = self._streambar_controller.pauseStreams()

        self._streambar_controller.pause()
        self._streambar_controller.enable(False)

    def _resume_streams(self):
        """
        Resume (unfreeze) the stream settings
        """
        # We don't restart the streams paused, because it's unlikely the user
        # is again interested it this one, and if the detector is sensitive,
        # it could even be dangerous. So just start the SEM survey. It also
        # ensures that the e-beam settings are reasonable for if the GUI is
        # restarted.
        # TODO: if acquisition was cancelled => put back streams as they were?
        self._tab_data_model.tool.value = TOOL_NONE
        for s in self._tab_data_model.streams.value:
            if isinstance(s, stream.SEMStream):
                s.should_update.value = True
                break

        self._streambar_controller.enable(True)
        self._streambar_controller.resume()

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
        self._resume_streams()

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
        self._pause_streams()

        self.btn_acquire.Disable()
        self.btn_cancel.Enable()

        self.gauge_acq.Show()
        self.btn_cancel.Show()

        self._main_data_model.is_acquiring.value = True

        # FIXME: probably not the whole window is required, just the file settings
        self._main_frame.Layout()  # to put the gauge at the right place

        # start acquisition + connect events to callback
        streams = self._tab_data_model.acquisitionView.getStreams()

        self.acq_future = acq.acquire(streams, self._main_data_model.opm)
        self._acq_future_connector = ProgressiveFutureConnector(self.acq_future,
                                                                self.gauge_acq,
                                                                self.lbl_acqestimate)
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
        # self._main_data_model.is_acquiring.value = False
        # all the rest will be handled by on_acquisition_done()

    def _export_to_file(self, acq_future):
        """
        return (list of DataArray, filename): data exported and filename
        """
        st = self._tab_data_model.acquisitionView.stream_tree
        thumb = acq.computeThumbnail(st, acq_future)
        data, exp = acq_future.result()

        # Handle the case acquisition failed "a bit"
        if exp:
            logging.error("Acquisition failed (after %d streams): %s",
                          len(data), exp)

        filename = self.filename.value
        exporter = dataio.get_converter(self.conf.last_format)
        exporter.export(filename, data, thumb)
        logging.info(u"Acquisition saved as file '%s'.", filename)
        return data, exp, filename

    @call_in_wx_main
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

        # save result to file
        self.lbl_acqestimate.SetLabel("Saving file...")
        # on big acquisitions, it can take ~20s
        sf = self._executor.submit(self._export_to_file, future)
        sf.add_done_callback(self.on_file_export_done)

    @call_in_wx_main
    def on_file_export_done(self, future):
        """
        Callback called when the acquisition is finished (either successfully or
        cancelled)
        """
        # hide progress bar
        self.gauge_acq.Hide()

        try:
            data, exp, filename = future.result()
        except Exception:
            logging.exception("Saving acquisition failed")
            self._reset_acquisition_gui("Saving acquisition file failed")
            return

        # Needs to be done before changing tabs as it will play again the stream
        # (and they will be immediately be stopped when changing tab).
        self._reset_acquisition_gui()

        # TODO: we should add the file to the list of recently-used files
        # cf http://pyxdg.readthedocs.org/en/latest/recentfiles.html
        if exp is None:
            # display in the analysis tab
            self._show_acquisition(data, open(filename))


# TODO: merge with AutoCenterController because they share too many GUI elements
class FineAlignController(object):
    """
    Takes care of the fine alignment button and process on the SECOM lens
    alignment tab.
    Not an "acquisition" process per-se but actually very similar, the main
    difference being that the result is not saved as a file, but sent to the
    CCD (for calibration).

    Note: It needs the VA .fineAlignDwellTime on the main GUI data (contains
      the time to expose each spot to the ebeam).
    """

    # TODO: make the max diff dependant on the optical FoV?
    OVRL_MAX_DIFF = 10e-06  # m, don't be too picky
    OVRL_REPETITION = (4, 4)  # Not too many, to keep it fast
    # OVRL_REPETITION = (7, 7)  # DEBUG (for compatibility with fake image)

    def __init__(self, tab_data, main_frame, settings_controller):
        """
        tab_data (MicroscopyGUIData): the representation of the microscope GUI
        main_frame: (wx.Frame): the frame which contains the viewports
        settings_controller (SettingController)
        """
        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._main_frame = main_frame
        self._settings_controller = settings_controller
        self._sizer = self._main_frame.pnl_align_tools.GetSizer()

        main_frame.btn_fine_align.Bind(wx.EVT_BUTTON, self._on_fine_align)
        self._fa_btn_label = self._main_frame.btn_fine_align.Label
        self._faf_connector = None

        # Make sure to reset the correction metadata if lens move
        self._main_data_model.aligner.position.subscribe(self._on_aligner_pos)

        self._main_data_model.fineAlignDwellTime.subscribe(self._on_dwell_time)
        self._tab_data_model.tool.subscribe(self._onTool, init=True)

    @call_in_wx_main
    def _onTool(self, tool):
        """
        Called when the tool (mode) is changed
        """
        # Don't enable during "acquisition", as we don't want to allow fine
        # alignment during auto centering. When button is "cancel", the tool
        # doesn't change so it's never disabled.
        acquiring = self._main_data_model.is_acquiring.value
        # Only allow fine alignment when spot mode is on (so that the exposure
        # time has /some chances/ to represent the needed dwell time)
        spot = (tool == guimod.TOOL_SPOT)

        self._main_frame.btn_fine_align.Enable(spot and not acquiring)
        self._update_est_time()

    def _on_dwell_time(self, dt):
        self._update_est_time()

    @call_in_wx_main
    def _update_est_time(self):
        """
        Compute and displays the estimated time for the fine alignment
        """
        if self._tab_data_model.tool.value == guimod.TOOL_SPOT:
            dt = self._main_data_model.fineAlignDwellTime.value
            t = align.find_overlay.estimateOverlayTime(dt, self.OVRL_REPETITION)
            t = math.ceil(t) # round a bit pessimistic
            txt = u"~ %s" % units.readable_time(t, full=False)
        else:
            txt = u""
        self._main_frame.lbl_fine_align.Label = txt

    def _on_aligner_pos(self, pos):
        """
        Called when the position of the lens is changed
        """
        # This means that the translation correction information from fine
        # alignment is not correct anymore, so reset it.
        self._tab_data_model.main.ccd.updateMetadata({model.MD_POS_COR: (0, 0)})

        # The main goal is to remove the "Successful" text if it there
        self._update_est_time()

    def _pause(self):
        """
        Pause the settings and the streams of the GUI
        """
        # save the original settings
        self._settings_controller.enable(False)
        self._settings_controller.pause()

        self._main_frame.lens_align_tb.enable(False)
        self._main_frame.btn_auto_center.Enable(False)

        # make sure to not disturb the acquisition
        for s in self._tab_data_model.streams.value:
            s.is_active.value = False

        # Prevent moving the stages
        for c in [self._main_frame.vp_align_ccd.canvas,
                  self._main_frame.vp_align_sem.canvas]:
            c.abilities -= set([CAN_DRAG, CAN_FOCUS])

    def _resume(self):
        self._settings_controller.resume()
        self._settings_controller.enable(True)

        self._main_frame.lens_align_tb.enable(True)
        self._main_frame.btn_auto_center.Enable(True)

        # Restart the streams (which were being played)
        for s in self._tab_data_model.streams.value:
            s.is_active.value = s.should_update.value

        # Allow moving the stages
        for c in [self._main_frame.vp_align_ccd.canvas,
                  self._main_frame.vp_align_sem.canvas]:
            c.abilities |= set([CAN_DRAG, CAN_FOCUS])

    def _on_fine_align(self, event):
        """
        Called when the "Fine alignment" button is clicked
        """
        self._pause()
        main_data = self._main_data_model
        main_data.is_acquiring.value = True

        logging.debug("Starting overlay procedure")
        f = align.FindOverlay(self.OVRL_REPETITION,
                                     main_data.fineAlignDwellTime.value,
                                     self.OVRL_MAX_DIFF,
                                     main_data.ebeam,
                                     main_data.ccd,
                                     main_data.sed,
                                     skew=True)
        logging.debug("Overlay procedure is running...")
        self._acq_future = f
        # Transform Fine alignment button into cancel
        self._main_frame.btn_fine_align.Bind(wx.EVT_BUTTON, self._on_cancel)
        self._main_frame.btn_fine_align.Label = "Cancel"

        # Set up progress bar
        self._main_frame.lbl_fine_align.Hide()
        self._main_frame.gauge_fine_align.Show()
        self._sizer.Layout()
        self._faf_connector = ProgressiveFutureConnector(f,
                                            self._main_frame.gauge_fine_align)

        f.add_done_callback(self._on_fa_done)

    def _on_cancel(self, evt):
        """
        Called during acquisition when pressing the cancel button
        """
        if not self._acq_future:
            msg = "Tried to cancel acquisition while it was not started"
            logging.warning(msg)
            return

        self._acq_future.cancel()
        # self._main_data_model.is_acquiring.value = False
        # all the rest will be handled by _on_fa_done()


    @call_in_wx_main
    def _on_fa_done(self, future):
        logging.debug("End of overlay procedure")
        main_data = self._main_data_model
        try:
            trans_val, cor_md = future.result()
            opt_md, sem_md = cor_md

            # Save the optical correction metadata straight into the CCD
            main_data.ccd.updateMetadata(opt_md)

            # The SEM correction metadata goes to the ebeam
            main_data.ebeam.updateMetadata(sem_md)
        except CancelledError:
            self._main_frame.lbl_fine_align.Label = "Cancelled"
        except Exception:
            logging.warning("Failed to run the fine alignment, a report "
                            "should be available in ~/odemis-overlay-report.")
            self._main_frame.lbl_fine_align.Label = "Failed"
        else:
            self._main_frame.lbl_fine_align.Label = "Successful"
            self._main_frame.menu_item_reset_finealign.Enable(True)
            # Temporary info until the GUI can actually rotate the images
            rot = math.degrees(opt_md.get(model.MD_ROTATION_COR, 0))
            shear = sem_md.get(model.MD_SHEAR_COR, 0)
            scaling_xy = sem_md.get(model.MD_PIXEL_SIZE_COR, (1, 1))
            # the worse is the rotation, the longer it's displayed
            timeout = max(2, min(abs(rot), 10))
            Message.show_message(
                self._main_frame,
                u"Rotation applied: %s\nShear applied: %s\nX/Y Scaling applied: %s"
                % (units.readable_str(rot, unit="°", sig=3),
                   units.readable_str(shear, sig=3),
                   units.readable_str(scaling_xy, sig=3)),
                timeout=timeout
            )
            logging.warning("Fine alignment computed rotation needed of %f°, "
                            "shear needed of %s, and X/Y scaling needed of %s.",
                            rot, shear, scaling_xy)

        # As the CCD image might have different pixel size, force to fit
        self._main_frame.vp_align_ccd.canvas.fit_view_to_next_image = True

        main_data.is_acquiring.value = False
        self._main_frame.btn_fine_align.Bind(wx.EVT_BUTTON, self._on_fine_align)
        self._main_frame.btn_fine_align.Label = self._fa_btn_label
        self._resume()

        self._main_frame.lbl_fine_align.Show()
        self._main_frame.gauge_fine_align.Hide()
        self._sizer.Layout()


class AutoCenterController(object):
    """
    Takes care of the auto centering button and process on the SECOM lens
    alignment tab.
    Not an "acquisition" process per-se but actually very similar, the main
    difference being that the result is not saved as a file, but directly
    applied to the microscope
    """

    def __init__(self, tab_data, aligner_xy, main_frame, settings_controller):
        """
        tab_data (MicroscopyGUIData): the representation of the microscope GUI
        aligner_xy (Stage): the stage used to move the objective, with axes X/Y
        main_frame: (wx.Frame): the frame which contains the viewports
        settings_controller (SettingController)
        """
        self._tab_data_model = tab_data
        self._aligner_xy = aligner_xy
        self._main_data_model = tab_data.main
        self._main_frame = main_frame
        self._settings_controller = settings_controller
        self._sizer = self._main_frame.pnl_align_tools.GetSizer()

        main_frame.btn_auto_center.Bind(wx.EVT_BUTTON, self._on_auto_center)
        self._ac_btn_label = self._main_frame.btn_auto_center.Label
        self._acf_connector = None

        self._main_data_model.ccd.exposureTime.subscribe(self._update_est_time, init=True)

    @call_in_wx_main
    def _update_est_time(self, unused):
        """
        Compute and displays the estimated time for the auto centering
        """
        et = self._main_data_model.ccd.exposureTime.value
        t = align.spot.estimateAlignmentTime(et)
        t = math.ceil(t) # round a bit pessimistic
        txt = u"~ %s" % units.readable_time(t, full=False)
        self._main_frame.lbl_auto_center.Label = txt

    def _pause(self):
        """
        Pause the settings and the streams of the GUI
        """
        # save the original settings
        self._settings_controller.enable(False)
        self._settings_controller.pause()

        self._main_frame.lens_align_tb.enable(False)
        self._main_frame.btn_fine_align.Enable(False)

        # make sure to not disturb the acquisition
        for s in self._tab_data_model.streams.value:
            s.is_active.value = False

        # Prevent moving the stages
        for c in [self._main_frame.vp_align_ccd.canvas,
                  self._main_frame.vp_align_sem.canvas]:
            c.abilities -= set([CAN_DRAG, CAN_FOCUS])

    def _resume(self):
        self._settings_controller.resume()
        self._settings_controller.enable(True)

        self._main_frame.lens_align_tb.enable(True)
        # Spot mode should always be active, so it's fine to directly enable FA
        self._main_frame.btn_fine_align.Enable(True)

        # Restart the streams (which were being played)
        for s in self._tab_data_model.streams.value:
            s.is_active.value = s.should_update.value

        # Allow moving the stages
        for c in [self._main_frame.vp_align_ccd.canvas,
                  self._main_frame.vp_align_sem.canvas]:
            c.abilities |= set([CAN_DRAG, CAN_FOCUS])

    def _on_auto_center(self, event):
        """
        Called when the "Auto centering" button is clicked
        """
        # Force spot mode: not needed by the code, but makes sense for the user
        self._tab_data_model.tool.value = guimod.TOOL_SPOT
        self._pause()

        main_data = self._main_data_model
        main_data.is_acquiring.value = True

        logging.debug("Starting auto centering procedure")
        f = align.AlignSpot(main_data.ccd,
                            self._aligner_xy,
                            main_data.ebeam,
                            main_data.focus,
                            type=OBJECTIVE_MOVE)
        logging.debug("Auto centering is running...")
        self._acq_future = f
        # Transform auto centering button into cancel
        self._main_frame.btn_auto_center.Bind(wx.EVT_BUTTON, self._on_cancel)
        self._main_frame.btn_auto_center.Label = "Cancel"

        # Set up progress bar
        self._main_frame.lbl_auto_center.Hide()
        self._main_frame.gauge_auto_center.Show()
        self._sizer.Layout()
        self._acf_connector = ProgressiveFutureConnector(f,
                                            self._main_frame.gauge_auto_center)

        f.add_done_callback(self._on_ac_done)

    def _on_cancel(self, evt):
        """
        Called during acquisition when pressing the cancel button
        """
        if not self._acq_future:
            msg = "Tried to cancel acquisition while it was not started"
            logging.warning(msg)
            return

        self._acq_future.cancel()
        # all the rest will be handled by _on_ac_done()

    @call_in_wx_main
    def _on_ac_done(self, future):
        logging.debug("End of auto centering procedure")
        main_data = self._main_data_model
        try:
            dist = future.result() # returns distance to center
        except CancelledError:
            self._main_frame.lbl_auto_center.Label = "Cancelled"
        except Exception as exp:
            logging.info("Centering procedure failed: %s", exp)
            self._main_frame.lbl_auto_center.Label = "Failed"
        else:
            self._main_frame.lbl_auto_center.Label = "Successful"

        # As the CCD image might have different pixel size, force to fit
        self._main_frame.vp_align_ccd.canvas.fit_view_to_next_image = True

        main_data.is_acquiring.value = False
        self._main_frame.btn_auto_center.Bind(wx.EVT_BUTTON, self._on_auto_center)
        self._main_frame.btn_auto_center.Label = self._ac_btn_label
        self._resume()

        self._main_frame.lbl_auto_center.Show()
        self._main_frame.gauge_auto_center.Hide()
        self._sizer.Layout()
