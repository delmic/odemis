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

from builtins import str
from concurrent import futures
from concurrent.futures._base import CancelledError
import logging
import math
from odemis import model, dataio
from odemis.acq import align, acqmng, stream
from odemis.acq.align.spot import OBJECTIVE_MOVE
from odemis.acq.stream import UNDEFINED_ROI, ScannedTCSettingsStream, ScannedTemporalSettingsStream, TemporalSpectrumSettingsStream
from odemis.gui import conf
from odemis.gui.acqmng import preset_as_is, get_global_settings_entries, \
    get_local_settings_entries, apply_preset
from odemis.gui.comp import popup
from odemis.gui.comp.canvas import CAN_DRAG, CAN_FOCUS
from odemis.gui.model import TOOL_NONE, TOOL_SPOT
from odemis.gui.util import img, get_picture_folder, call_in_wx_main, \
    wxlimit_invocation
from odemis.gui.util.widgets import ProgressiveFutureConnector, EllipsisAnimator
from odemis.gui.win.acquisition import AcquisitionDialog, \
    ShowAcquisitionFileDialog
from odemis.model import DataArrayShadow
from odemis.util import units
from odemis.util.filename import guess_pattern, create_filename, update_counter
import os
import re
import subprocess
import threading
import time
import wx

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
        self._main_frame.Bind(wx.EVT_MENU, self.start_snapshot_viewport, id=self._main_frame.menu_item_snapshot.GetId())

        self._main_frame.Bind(wx.EVT_MENU, self.start_snapshot_as_viewport, id=self._main_frame.menu_item_snapshot_as.GetId())

        self._prev_streams = None # To unsubscribe afterwards
        self._main_data_model.tab.subscribe(self.on_tab_change, init=True)

    def on_tab_change(self, tab):
        """ Called when the current tab changes """
        # Listen to .streams, to know whether the current tab has any stream
        if self._prev_streams:
            self._prev_streams.unsubscribe(self.on_streams_change)
        tab.tab_data_model.streams.subscribe(self.on_streams_change, init=True)
        self._prev_streams = tab.tab_data_model.streams

    @call_in_wx_main
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
                for d in s.raw:
                    if isinstance(d, DataArrayShadow):
                        # Load the entire raw data
                        # TODO: first check that it's not going to be too big?
                        d = d.getData()

                    # add the stream name to the image
                    if not hasattr(d, "metadata"):
                        # Not a DataArray => let's try to convert it
                        try:
                            d = model.DataArray(d)
                        except Exception:
                            logging.warning("Raw data of stream %s doesn't seem to be DataArray", s.name.value)
                            continue

                    if model.MD_DESCRIPTION not in d.metadata:
                        d.metadata[model.MD_DESCRIPTION] = s.name.value

                    raw_images.append(d)

            # record everything to a file
            exporter.export(filepath, raw_images, thumbnail)
            popup.show_message(self._main_frame,
                               "Snapshot saved as %s" % (os.path.basename(filepath),),
                               message="In %s" % (os.path.dirname(filepath),),
                               timeout=3
                               )

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
        """ Detect and return output displays

        This method returns an empty list on MS Windows

        :return: (set of strings): names of outputs used

        """

        if not os.name == 'nt':
            xrandr_out = subprocess.check_output("xrandr")
            # only pick the "connected" outputs
            ret = re.findall(b"^(\\w+) connected ", xrandr_out, re.MULTILINE)
            return ret
        else:
            return []

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

    def __init__(self, tab_data, tab_panel):
        """
        tab_data (MicroscopyGUIData): the representation of the microscope GUI
        tab_panel: (wx.Frame): the frame which contains the 4 viewports
        """
        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._tab_panel = tab_panel

        # Listen to "acquire image" button
        self._tab_panel.btn_secom_acquire.Bind(wx.EVT_BUTTON, self.on_acquire)

        # Only possible to acquire if there are streams, and the chamber is
        # under vacuum
        tab_data.streams.subscribe(self.on_stream_chamber)
        tab_data.main.chamberState.subscribe(self.on_stream_chamber)

        if hasattr(tab_data, "roa"):
            tab_data.roa.subscribe(self.on_stream_chamber, init=True)

        # Disable the "acquire image" button while preparation is in progress
        self._main_data_model.is_preparing.subscribe(self.on_preparation)

    # Some streams (eg, TCSettingsStream) require a ROA for acquiring.
    # So if any of this type of Stream is present, forbid to acquire until the ROA is defined.
    def _roa_is_valid(self):
        roa_valid = True
        if hasattr(self._tab_data_model, "roa") and self._main_data_model.time_correlator is not None and \
            any(isinstance(s, ScannedTCSettingsStream) for s in self._tab_data_model.streams.value):
            roa_valid = self._tab_data_model.roa.value != UNDEFINED_ROI

        return roa_valid

    @call_in_wx_main
    def on_stream_chamber(self, _):
        """
        Called when chamber state or streams change.
        Used to update the acquire button state
        """
        st_present = not not self._tab_data_model.streams.value
        ch_vacuum = (self._tab_data_model.main.chamberState.value
                     in {guimod.CHAMBER_VACUUM, guimod.CHAMBER_UNKNOWN})

        should_enable = st_present and ch_vacuum and not self._main_data_model.is_preparing.value and self._roa_is_valid()

        self._tab_panel.btn_secom_acquire.Enable(should_enable)

    @call_in_wx_main
    def on_preparation(self, is_preparing):
        self._tab_panel.btn_secom_acquire.Enable(not is_preparing and self._roa_is_valid())

    def on_acquire(self, evt):
        self.open_acquisition_dialog()

    def open_acquisition_dialog(self):
        main_data = self._tab_data_model.main
        secom_live_tab = main_data.getTabByName("secom_live")

        # Indicate we are acquiring, especially important for the SEM which
        # need to get the external signal to not scan (cf MicroscopeController)
        main_data.is_acquiring.value = True

        # save the original settings
        settingsbar_controller = secom_live_tab.settingsbar_controller
        orig_entries = get_global_settings_entries(settingsbar_controller)
        for sc in secom_live_tab.streambar_controller.stream_controllers:
            orig_entries += get_local_settings_entries(sc)
        orig_settings = preset_as_is(orig_entries)
        settingsbar_controller.pause()
        settingsbar_controller.enable(False)

        # pause all the live acquisitions
        streambar_controller = secom_live_tab.streambar_controller
        streambar_controller.pauseStreams()
        streambar_controller.pause()

        if self._tab_data_model.tool.value == TOOL_SPOT:
            self._tab_data_model.tool.value = TOOL_NONE

        streambar_controller.enable(False)

        # create the dialog
        try:
            acq_dialog = AcquisitionDialog(self._tab_panel.Parent, self._tab_data_model)
            parent_size = [v * 0.77 for v in self._tab_panel.Parent.GetSize()]

            acq_dialog.SetSize(parent_size)
            acq_dialog.Center()
            action = acq_dialog.ShowModal()
        except Exception:
            logging.exception("Failed to create acquisition dialog")
            raise
        finally:
            apply_preset(orig_settings)

            settingsbar_controller.enable(True)
            settingsbar_controller.resume()

            streambar_controller.enable(True)
            streambar_controller.resume()

            main_data.is_acquiring.value = False

            acq_dialog.Destroy()

        if action == wx.ID_OPEN:
            tab = main_data.getTabByName('analysis')
            main_data.tab.value = tab
            tab.load_data(acq_dialog.last_saved_file)


class SparcAcquiController(object):
    """
    Takes care of the acquisition button and process on the Sparc acquisition
    tab.
    """

    def __init__(self, tab_data, tab_panel, streambar_controller):
        """
        tab_data (MicroscopyGUIData): the representation of the microscope GUI
        tab_panel: (wx.Frame): the frame which contains the 4 viewports
        stream_ctrl (StreamBarController): controller to pause/resume the streams
        """
        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._tab_panel = tab_panel
        self._streambar_controller = streambar_controller

        # For file selection
        self.conf = conf.get_acqui_conf()

        # TODO: this should be the date at which the user presses the acquire
        # button (or when the last settings were changed)!
        # At least, we must ensure it's a new date after the acquisition
        # is done.
        # Filename to save the acquisition
        self.filename = model.StringVA(create_filename(self.conf.last_path, self.conf.fn_ptn,
                                                       self.conf.last_extension, self.conf.fn_count))
        self.filename.subscribe(self._onFilename, init=True)

        # For acquisition
        # a ProgressiveFuture if the acquisition is going on
        self.btn_acquire = self._tab_panel.btn_sparc_acquire
        self.btn_change_file = self._tab_panel.btn_sparc_change_file
        self.btn_cancel = self._tab_panel.btn_sparc_cancel
        self.acq_future = None
        self.gauge_acq = self._tab_panel.gauge_sparc_acq
        self.lbl_acqestimate = self._tab_panel.lbl_sparc_acq_estimate
        self.bmp_acq_status_warn = self._tab_panel.bmp_acq_status_warn
        self.bmp_acq_status_info = self._tab_panel.bmp_acq_status_info
        self._acq_future_connector = None

        # TODO: share an executor with the whole GUI.
        self._executor = futures.ThreadPoolExecutor(max_workers=2)

        # Link buttons
        self.btn_acquire.Bind(wx.EVT_BUTTON, self.on_acquisition)
        self.btn_change_file.Bind(wx.EVT_BUTTON, self.on_change_file)
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_cancel)

        self.gauge_acq.Hide()
        self._tab_panel.Parent.Layout()

        # Animator for messages containing ellipsis character
        self._ellipsis_animator = None

        # TODO: we need to be informed if the user closes suddenly the window
        # self.Bind(wx.EVT_CLOSE, self.on_close)

        self._roa = tab_data.semStream.roi

        # Listen to change of streams to update the acquisition time
        self._prev_streams = set() # set of streams already listened to
        tab_data.streams.subscribe(self._onStreams, init=True)
        # also listen to .semStream, which is not in .streams
        for va in self._get_settings_vas(tab_data.semStream):
            va.subscribe(self._onAnyVA)
        # Extra options affecting the acquisitions globally
        tab_data.pcdActive.subscribe(self._onAnyVA)
        # TODO: should also listen to the VAs of the leeches on semStream
        tab_data.useScanStage.subscribe(self._onAnyVA)

        self._roa.subscribe(self._onROA, init=True)

        # Listen to preparation state
        self._main_data_model.is_preparing.subscribe(self.on_preparation)

    def __del__(self):
        self._executor.shutdown(wait=False)

    # black list of VAs name which are known to not affect the acquisition time
    VAS_NO_ACQUSITION_EFFECT = ("image", "autoBC", "intensityRange", "histogram",
                                "is_active", "should_update", "status", "name", "tint")

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

    def _onFilename(self, name):
        """ updates the GUI when the filename is updated """
        # decompose into path/file
        path, base = os.path.split(name)
        self._tab_panel.txt_destination.SetValue(str(path))
        # show the end of the path (usually more important)
        self._tab_panel.txt_destination.SetInsertionPointEnd()
        self._tab_panel.txt_filename.SetValue(str(base))

    def _onROA(self, roi):
        """ updates the acquire button according to the acquisition ROI """
        self.check_acquire_button()
        self.update_acquisition_time()  # to update the message

    def on_preparation(self, is_preparing):
        self.check_acquire_button()
        self.update_acquisition_time()

    def check_acquire_button(self):
        self.btn_acquire.Enable(self._roa.value != UNDEFINED_ROI and
                                not self._main_data_model.is_preparing.value)

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

    def update_fn_suggestion(self):
        """
        When the filename counter is updated in a plugin, the suggested name for
        the main acquisition needs to be updated
        """
        self.filename.value = create_filename(self.conf.last_path, self.conf.fn_ptn,
                                              self.conf.last_extension, self.conf.fn_count)

    def on_change_file(self, evt):
        """
        Shows a dialog to change the path, name, and format of the acquisition
        file.
        returns nothing, but updates .filename and .conf
        """
        # Update .filename with new filename instead of input name so the right
        # time is used
        fn = create_filename(self.conf.last_path, self.conf.fn_ptn,
                             self.conf.last_extension, self.conf.fn_count)
        new_name = ShowAcquisitionFileDialog(self._tab_panel, fn)
        if new_name is not None:
            self.filename.value = new_name
            self.conf.fn_ptn, self.conf.fn_count = guess_pattern(new_name)
            logging.debug("Generated filename pattern '%s'", self.conf.fn_ptn)

    @wxlimit_invocation(1) # max 1/s
    def update_acquisition_time(self):
        if self._ellipsis_animator:
            # cancel if there is an ellipsis animator updating the status message
            self._ellipsis_animator.cancel()
            self._ellipsis_animator = None

        # Don't update estimated time if acquisition is running (as we are
        # sharing the label with the estimated time-to-completion).
        if self._main_data_model.is_acquiring.value:
            return

        lvl = None  # icon status shown
        if self._main_data_model.is_preparing.value:
            txt = u"Optical path is being reconfigured…"
            self._ellipsis_animator = EllipsisAnimator(txt, self.lbl_acqestimate)
            self._ellipsis_animator.start()
            lvl = logging.INFO
        elif self._roa.value == UNDEFINED_ROI:
            # TODO: update the default text to be the same
            txt = u"Region of acquisition needs to be selected"
            lvl = logging.WARN
        else:
            streams = self._tab_data_model.acquisitionStreams
            acq_time = acqmng.estimateTime(streams)
            acq_time = math.ceil(acq_time)  # round a bit pessimistic
            txt = u"Estimated time is {}."
            txt = txt.format(units.readable_time(acq_time))

        logging.debug("Updating status message %s, with level %s", txt, lvl)
        self.lbl_acqestimate.SetLabel(txt)
        self._show_status_icons(lvl)

    def _show_status_icons(self, lvl):
        # update status icon to show the logging level
        self.bmp_acq_status_info.Show(lvl in (logging.INFO, logging.DEBUG))
        self.bmp_acq_status_warn.Show(lvl == logging.WARN)
        self._tab_panel.Layout()

    def _pause_streams(self):
        """
        Freeze the streams settings and ensure no stream is playing
        """
        self._streambar_controller.pauseStreams()
        self._streambar_controller.pause()

    def _resume_streams(self):
        """
        Resume (unfreeze) the stream settings
        """
        self._streambar_controller.resume()

    def _reset_acquisition_gui(self, text=None, level=None, keep_filename=False):
        """
        Set back every GUI elements to be ready for the next acquisition
        text (None or str): a (error) message to display instead of the
          estimated acquisition time
        level (None or logging.*): logging level of the text, shown as an icon.
          If None, no icon is shown.
        keep_filename (bool): if True, will not update the filename
        """
        self.btn_cancel.Hide()
        self.btn_acquire.Enable()
        self._tab_panel.Layout()
        self._resume_streams()

        if not keep_filename:
            self.conf.fn_count = update_counter(self.conf.fn_count)

        # Update filename even if keep_filename is True (but don't update counter). This
        # ensures that the time is always up to date.
        self.filename.value = create_filename(self.conf.last_path, self.conf.fn_ptn,
                                              self.conf.last_extension, self.conf.fn_count)

        if text is not None:
            self.lbl_acqestimate.SetLabel(text)
            self._show_status_icons(level)
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
        # Time-resolved data cannot be saved in .ome.tiff format for now
        # OME-TIFF wants to save each time data on a separate "page", which causes too many pages.
        has_temporal = False
        for s in self._tab_data_model.streams.value:
            if (isinstance(s, ScannedTemporalSettingsStream) or
                isinstance(s, ScannedTCSettingsStream) or
                isinstance(s, TemporalSpectrumSettingsStream)):
                has_temporal = True

        #  ADD the the overlay (live_update) in the SEM window which displays the SEM measurements of the current
        #  acquisition if a  stream is added and acquisition is started.
        for v in self._tab_data_model.visible_views.value:
            if hasattr(v, "stream_classes") and isinstance(self._tab_data_model.semStream, v.stream_classes):
                v.addStream(self._tab_data_model.semStream)

        if (self.conf.last_format == 'TIFF' or self.conf.last_format == 'Serialized TIFF') and has_temporal:
            raise NotImplementedError("Cannot save temporal data in %s format, data format must be HDF5." \
                                      % self.conf.last_format)

        self._pause_streams()

        self.btn_acquire.Disable()
        self.btn_cancel.Enable()
        self._main_data_model.is_acquiring.value = True

        self.gauge_acq.Show()
        self.btn_cancel.Show()
        self._show_status_icons(None)
        self._tab_panel.Layout()  # to put the gauge at the right place

        # start acquisition + connect events to callback
        self.acq_future = acqmng.acquire(self._tab_data_model.acquisitionStreams, self._main_data_model.settings_obs)
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
        streams = list(self._tab_data_model.acquisitionStreams)
        st = stream.StreamTree(streams=streams)
        thumb = acqmng.computeThumbnail(st, acq_future)
        data, exp = acq_future.result()

        filename = self.filename.value
        if data:
            exporter = dataio.get_converter(self.conf.last_format)
            exporter.export(filename, data, thumb)
            logging.info(u"Acquisition saved as file '%s'.", filename)
        else:
            logging.debug("Not saving into file '%s' as there is no data", filename)

        return data, exp, filename

    @call_in_wx_main
    def on_acquisition_done(self, future):
        """
        Callback called when the acquisition is finished (either successfully or
        cancelled)
        """
        self.btn_cancel.Disable()
        self._main_data_model.is_acquiring.value = False
        self.acq_future = None  # To avoid holding the ref in memory
        self._acq_future_connector = None

        try:
            data, exp = future.result()
        except CancelledError:
            # hide progress bar (+ put pack estimated time)
            self.gauge_acq.Hide()
            # don't change filename => we can reuse it
            self._reset_acquisition_gui(keep_filename=True)
            return
        except Exception as exp:
            # leave the gauge, to give a hint on what went wrong.
            logging.exception("Acquisition failed")
            self._reset_acquisition_gui("Acquisition failed (see log panel).",
                                        level=logging.WARNING,
                                        keep_filename=True)
            return

        # Handle the case acquisition failed "a bit"
        if exp:
            logging.error("Acquisition failed (after %d streams): %s",
                          len(data), exp)

        #  REMOVE the the overlay (live_update) in the SEM window which displays the SEM measurements of the current
        #  acquisition if a  stream is added and acquisition is started.
        for v in self._tab_data_model.visible_views.value:
            if hasattr(v, "removeStream"):
                v.removeStream(self._tab_data_model.semStream)

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
            self._reset_acquisition_gui("Saving acquisition file failed (see log panel).",
                                        level=logging.WARNING)
            return

        if exp is None:
            # Needs to be done before changing tabs as it will play again the stream
            # (and they will be immediately be stopped when changing tab).
            self._reset_acquisition_gui()

            # TODO: we should add the file to the list of recently-used files
            # cf http://pyxdg.readthedocs.org/en/latest/recentfiles.html

            # display in the analysis tab
            self._show_acquisition(data, open(filename))
        else:
            self._reset_acquisition_gui("Acquisition failed (see log panel).",
                                        level=logging.WARNING,
                                        keep_filename=(not data))


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

    def __init__(self, tab_data, tab_panel, main_frame):
        """
        tab_data (MicroscopyGUIData): the representation of the microscope GUI
        tab_panel: (wx.Panel): the tab that contains the viewports
        """
        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._tab_panel = tab_panel
        self._main_frame = main_frame
        self._sizer = self._tab_panel.pnl_align_tools.GetSizer()

        tab_panel.btn_fine_align.Bind(wx.EVT_BUTTON, self._on_fine_align)
        self._fa_btn_label = self._tab_panel.btn_fine_align.Label
        self._acq_future = None
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

        self._tab_panel.btn_fine_align.Enable(spot and not acquiring)
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
        self._tab_panel.lbl_fine_align.Label = txt

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
        self._tab_panel.lens_align_tb.enable(False)
        self._tab_panel.btn_auto_center.Enable(False)

        # make sure to not disturb the acquisition
        for s in self._tab_data_model.streams.value:
            s.is_active.value = False

        # Prevent moving the stages
        for c in [self._tab_panel.vp_align_ccd.canvas,
                  self._tab_panel.vp_align_sem.canvas]:
            c.abilities -= {CAN_DRAG, CAN_FOCUS}

    def _resume(self):
        self._tab_panel.lens_align_tb.enable(True)
        self._tab_panel.btn_auto_center.Enable(True)

        # Restart the streams (which were being played)
        for s in self._tab_data_model.streams.value:
            s.is_active.value = s.should_update.value

        # Allow moving the stages
        for c in [self._tab_panel.vp_align_ccd.canvas,
                  self._tab_panel.vp_align_sem.canvas]:
            c.abilities |= {CAN_DRAG, CAN_FOCUS}

    def _on_fine_align(self, event):
        """
        Called when the "Fine alignment" button is clicked
        """
        self._pause()
        main_data = self._main_data_model
        main_data.is_acquiring.value = True

        logging.debug("Starting overlay procedure")
        f = align.FindOverlay(
            self.OVRL_REPETITION,
            main_data.fineAlignDwellTime.value,
            self.OVRL_MAX_DIFF,
            main_data.ebeam,
            main_data.ccd,
            main_data.sed,
            skew=True
        )
        logging.debug("Overlay procedure is running...")
        self._acq_future = f
        # Transform Fine alignment button into cancel
        self._tab_panel.btn_fine_align.Bind(wx.EVT_BUTTON, self._on_cancel)
        self._tab_panel.btn_fine_align.Label = "Cancel"

        # Set up progress bar
        self._tab_panel.lbl_fine_align.Hide()
        self._tab_panel.gauge_fine_align.Show()
        self._sizer.Layout()
        self._faf_connector = ProgressiveFutureConnector(f, self._tab_panel.gauge_fine_align)

        f.add_done_callback(self._on_fa_done)

    def _on_cancel(self, _):
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
        self._acq_future = None  # To avoid holding the ref in memory
        self._faf_connector = None

        try:
            trans_val, cor_md = future.result()
            opt_md, sem_md = cor_md

            # Save the optical correction metadata straight into the CCD
            main_data.ccd.updateMetadata(opt_md)

            # The SEM correction metadata goes to the ebeam
            main_data.ebeam.updateMetadata(sem_md)
        except CancelledError:
            self._tab_panel.lbl_fine_align.Label = "Cancelled"
        except Exception as ex:
            logging.warning("Failure during overlay: %s", ex)
            self._tab_panel.lbl_fine_align.Label = "Failed"
        else:
            self._main_frame.menu_item_reset_finealign.Enable(True)

            # Check whether the values make sense. If not, we still accept them,
            # but hopefully make it clear enough to the user that the calibration
            # should not be trusted.
            rot = opt_md.get(model.MD_ROTATION_COR, 0)
            rot0 = (rot + math.pi) % (2 * math.pi) - math.pi  # between -pi and pi
            rot_deg = math.degrees(rot0)
            opt_scale = opt_md.get(model.MD_PIXEL_SIZE_COR, (1, 1))[0]
            shear = sem_md.get(model.MD_SHEAR_COR, 0)
            scaling_xy = sem_md.get(model.MD_PIXEL_SIZE_COR, (1, 1))
            if (not abs(rot_deg) < 10 or  # Rotation < 10°
                not 0.9 < opt_scale < 1.1 or  # Optical mag < 10%
                not abs(shear) < 0.3 or  # Shear < 30%
                any(not 0.9 < v < 1.1 for v in scaling_xy) # SEM ratio diff < 10%
               ):
                # Special warning in case of wrong magnification
                if not 0.9 < opt_scale < 1.1 and model.hasVA(main_data.lens, "magnification"):
                    lens_mag = main_data.lens.magnification.value
                    measured_mag = lens_mag / opt_scale
                    logging.warning("The measured optical magnification is %fx, instead of expected %fx. "
                                    "Check that the lens magnification and the SEM magnification are correctly set.",
                                    measured_mag, lens_mag)
                else:  # Generic warning
                    logging.warning(u"The fine alignment values are very large, try on a different place on the sample. "
                                    u"mag correction: %f, rotation: %f°, shear: %f, X/Y scale: %f",
                                    opt_scale, rot_deg, shear, scaling_xy)

                title = "Fine alignment probably incorrect"
                lvl = logging.WARNING
                self._tab_panel.lbl_fine_align.Label = "Probably incorrect"
            else:
                title = "Fine alignment successful"
                lvl = logging.INFO
                self._tab_panel.lbl_fine_align.Label = "Successful"

            # Rotation is compensated in software on the FM image, but the user
            # can also change the SEM scan rotation, and re-run the alignment,
            # so show it clearly, for the user to take action.
            # The worse the rotation, the longer it's displayed.
            timeout = max(2, min(abs(rot_deg), 10))
            popup.show_message(
                self._tab_panel,
                title,
                u"Rotation applied: %s\nShear applied: %s\nX/Y Scaling applied: %s"
                % (units.readable_str(rot_deg, unit=u"°", sig=3),
                   units.readable_str(shear, sig=3),
                   units.readable_str(scaling_xy, sig=3)),
                timeout=timeout,
                level=lvl
            )
            logging.info(u"Fine alignment computed mag correction of %f, rotation of %f°, "
                         u"shear needed of %s, and X/Y scaling needed of %s.",
                         opt_scale, rot, shear, scaling_xy)

        # As the CCD image might have different pixel size, force to fit
        self._tab_panel.vp_align_ccd.canvas.fit_view_to_next_image = True

        main_data.is_acquiring.value = False
        self._tab_panel.btn_fine_align.Bind(wx.EVT_BUTTON, self._on_fine_align)
        self._tab_panel.btn_fine_align.Label = self._fa_btn_label
        self._resume()

        self._tab_panel.lbl_fine_align.Show()
        self._tab_panel.gauge_fine_align.Hide()
        self._sizer.Layout()


class AutoCenterController(object):
    """
    Takes care of the auto centering button and process on the SECOM lens
    alignment tab.
    Not an "acquisition" process per-se but actually very similar, the main
    difference being that the result is not saved as a file, but directly
    applied to the microscope
    """

    def __init__(self, tab_data, aligner_xy, tab_panel):
        """
        tab_data (MicroscopyGUIData): the representation of the microscope GUI
        aligner_xy (Stage): the stage used to move the objective, with axes X/Y
        tab_panel: (wx.Panel): the tab panel which contains the viewports
        """
        self._tab_data_model = tab_data
        self._aligner_xy = aligner_xy
        self._main_data_model = tab_data.main
        self._tab_panel = tab_panel
        self._sizer = self._tab_panel.pnl_align_tools.GetSizer()

        tab_panel.btn_auto_center.Bind(wx.EVT_BUTTON, self._on_auto_center)
        self._ac_btn_label = self._tab_panel.btn_auto_center.Label
        self._acf_connector = None

        self._main_data_model.ccd.exposureTime.subscribe(self._update_est_time, init=True)

    @call_in_wx_main
    def _update_est_time(self, _):
        """
        Compute and displays the estimated time for the auto centering
        """
        if self._main_data_model.is_acquiring.value:
            return

        et = self._main_data_model.ccd.exposureTime.value
        t = align.spot.estimateAlignmentTime(et)
        t = math.ceil(t) # round a bit pessimistic
        txt = u"~ %s" % units.readable_time(t, full=False)
        self._tab_panel.lbl_auto_center.Label = txt

    def _pause(self):
        """
        Pause the settings and the streams of the GUI
        """
        self._tab_panel.lens_align_tb.enable(False)
        self._tab_panel.btn_fine_align.Enable(False)

        # make sure to not disturb the acquisition
        for s in self._tab_data_model.streams.value:
            s.is_active.value = False

        # Prevent moving the stages
        for c in [self._tab_panel.vp_align_ccd.canvas,
                  self._tab_panel.vp_align_sem.canvas]:
            c.abilities -= {CAN_DRAG, CAN_FOCUS}

    def _resume(self):
        self._tab_panel.lens_align_tb.enable(True)
        # Spot mode should always be active, so it's fine to directly enable FA
        self._tab_panel.btn_fine_align.Enable(True)

        # Restart the streams (which were being played)
        for s in self._tab_data_model.streams.value:
            s.is_active.value = s.should_update.value

        # Allow moving the stages
        for c in [self._tab_panel.vp_align_ccd.canvas,
                  self._tab_panel.vp_align_sem.canvas]:
            c.abilities |= {CAN_DRAG, CAN_FOCUS}

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
        self._tab_panel.btn_auto_center.Bind(wx.EVT_BUTTON, self._on_cancel)
        self._tab_panel.btn_auto_center.Label = "Cancel"

        # Set up progress bar
        self._tab_panel.lbl_auto_center.Hide()
        self._tab_panel.gauge_auto_center.Show()
        self._sizer.Layout()
        self._acf_connector = ProgressiveFutureConnector(f,
                                            self._tab_panel.gauge_auto_center)

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
            self._tab_panel.lbl_auto_center.Label = "Cancelled"
        except Exception as exp:
            logging.info("Centering procedure failed: %s", exp)
            self._tab_panel.lbl_auto_center.Label = "Failed"
        else:
            self._tab_panel.lbl_auto_center.Label = "Successful"

        # As the CCD image might have different pixel size, force to fit
        self._tab_panel.vp_align_ccd.canvas.fit_view_to_next_image = True

        main_data.is_acquiring.value = False
        self._tab_panel.btn_auto_center.Bind(wx.EVT_BUTTON, self._on_auto_center)
        self._tab_panel.btn_auto_center.Label = self._ac_btn_label
        self._resume()

        self._tab_panel.lbl_auto_center.Show()
        self._tab_panel.gauge_auto_center.Hide()
        self._sizer.Layout()
