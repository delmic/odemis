# -*- coding: utf-8 -*-
"""
Created on 12 Apr 2013

@author: Rinze de Laat

Copyright © 2013 Éric Piel, Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

from builtins import str
from concurrent.futures._base import CancelledError
import copy
import gc
import logging
import math
import numpy
from odemis import model, dataio
from odemis.acq import stream, path, acqmng, stitching
from odemis.acq.stitching import FocusingMethod, WEAVER_MEAN, REGISTER_IDENTITY
from odemis.acq.stream import NON_SPATIAL_STREAMS, EMStream, OpticalStream, ScannedFluoStream, LiveStream
from odemis.acq.stream import SEMStream, CameraStream, EMStream
from odemis.gui.acqmng import presets, preset_as_is, apply_preset, \
    get_global_settings_entries, get_local_settings_entries
from odemis.gui.comp.overlay.world import RepetitionSelectOverlay
from odemis.gui.conf import get_acqui_conf, util
from odemis.gui.cont.settings import SecomSettingsController, LocalizationSettingsController
from odemis.gui.cont.streams import StreamBarController
from odemis.gui.main_xrc import xrcfr_acq, xrcfr_overview_acq
from odemis.gui.model import TOOL_NONE, StreamView, AcquisitionWindowData
from odemis.gui.util import call_in_wx_main, formats_to_wildcards, \
    wxlimit_invocation
from odemis.gui.util.widgets import ProgressiveFutureConnector, \
    VigilantAttributeConnector
from odemis.util import rect_intersect, units
from odemis.util.comp import compute_scanner_fov, compute_camera_fov
from odemis.util.filename import guess_pattern, create_filename, update_counter
import os.path
import wx

import odemis.gui.model as guimodel


class AcquisitionDialog(xrcfr_acq):
    """ Wrapper class responsible for additional initialization of the
    Acquisition Dialog created in XRCed
    """

    # TODO: share more code with cont.acquisition
    def __init__(self, parent, orig_tab_data):
        xrcfr_acq.__init__(self, parent)

        self.conf = get_acqui_conf()

        for n in presets:
            self.cmb_presets.Append(n)
        # TODO: record and reuse the preset used?
        self.cmb_presets.Select(0)

        self.filename = model.StringVA(create_filename(self.conf.last_path, self.conf.fn_ptn,
                                                       self.conf.last_extension, self.conf.fn_count))
        self.filename.subscribe(self._onFilename, init=True)

        # The name of the last file that got written to disk (used for auto viewing on close)
        self.last_saved_file = None
        
        # True when acquisition occurs
        self.acquiring = False

        # a ProgressiveFuture if the acquisition is going on
        self.acq_future = None
        self._acq_future_connector = None

        self._main_data_model = orig_tab_data.main

        # duplicate the interface, but with only one view
        self._tab_data_model = self.duplicate_tab_data_model(orig_tab_data)

        # Create a new settings controller for the acquisition dialog
        self._settings_controller = SecomSettingsController(
            self,
            self._tab_data_model,
            highlight_change=True  # also adds a "Reset" context menu
        )

        orig_view = orig_tab_data.focussedView.value
        self._view = self._tab_data_model.focussedView.value
        self._hidden_view = StreamView("Plugin View Hidden")

        self.streambar_controller = StreamBarController(self._tab_data_model,
                                                        self.pnl_secom_streams,
                                                        static=True,
                                                        ignore_view=True)
        # The streams currently displayed are the one visible
        self.add_all_streams()

        # The list of streams ready for acquisition (just used as a cache)
        self._acq_streams = {}

        # FIXME: pass the fold_panels

        # Compute the preset values for each preset
        self._preset_values = {}  # dict string -> dict (SettingEntries -> value)
        self._orig_entries = get_global_settings_entries(self._settings_controller)
        for sc in self.streambar_controller.stream_controllers:
            self._orig_entries += get_local_settings_entries(sc)
        self._orig_settings = preset_as_is(self._orig_entries)  # to detect changes
        for n, preset in presets.items():
            self._preset_values[n] = preset(self._orig_entries)
        # Presets which have been confirmed on the hardware
        self._presets_confirmed = set() # (string)

        self.start_listening_to_va()

        # If it could be possible to do fine alignment, allow the user to choose
        if self._can_fine_align(self._tab_data_model.streams.value):
            self.chkbox_fine_align.Show()
            # Set to True to make it the default, but will be automatically
            # disabled later if the current visible streams don't allow it.
            self.chkbox_fine_align.Value = True

            for s in self._tab_data_model.streams.value:
                if isinstance(s, EMStream):
                    em_det = s.detector
                    em_emt = s.emitter
                elif isinstance(s, OpticalStream) and not isinstance(s, ScannedFluoStream):
                    opt_det = s.detector
            self._ovrl_stream = stream.OverlayStream("Fine alignment", opt_det, em_emt, em_det,
                                                     opm=self._main_data_model.opm)
            self._ovrl_stream.dwellTime.value = self._main_data_model.fineAlignDwellTime.value
        else:
            self.chkbox_fine_align.Show(False)
            self.chkbox_fine_align.Value = False

        self._prev_fine_align = self.chkbox_fine_align.Value

        # make sure the view displays the same thing as the one we are
        # duplicating
        self._view.view_pos.value = orig_view.view_pos.value
        self._view.mpp.value = orig_view.mpp.value
        self._view.merge_ratio.value = orig_view.merge_ratio.value

        # attach the view to the viewport
        self.pnl_view_acq.canvas.fit_view_to_next_image = False
        self.pnl_view_acq.setView(self._view, self._tab_data_model)

        # The TOOL_ROA is not present because we don't allow the user to change
        # the ROA), so we need to explicitly request the canvas to show the ROA.
        if hasattr(self._tab_data_model, "roa") and self._tab_data_model.roa is not None:
            cnvs = self.pnl_view_acq.canvas
            self.roa_overlay = RepetitionSelectOverlay(cnvs, self._tab_data_model.roa,
                                                             self._tab_data_model.fovComp)
            cnvs.add_world_overlay(self.roa_overlay)

        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)

        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_close)
        self.btn_change_file.Bind(wx.EVT_BUTTON, self.on_change_file)
        self.btn_secom_acquire.Bind(wx.EVT_BUTTON, self.on_acquire)
        self.cmb_presets.Bind(wx.EVT_COMBOBOX, self.on_preset)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        # on_streams_changed is compatible because it doesn't use the args
        self.chkbox_fine_align.Bind(wx.EVT_CHECKBOX, self.on_streams_changed)

        self.on_preset(None) # will force setting the current preset

        # To update the estimated time when streams are removed/added
        self._view.stream_tree.flat.subscribe(self.on_streams_changed)
        self._hidden_view.stream_tree.flat.subscribe(self.on_streams_changed)
        
    def start_listening_to_va(self):
        # Get all the VA's from the stream and subscribe to them for changes.
        for entry in self._orig_entries:
            if hasattr(entry, "vigilattr"):
                entry.vigilattr.subscribe(self.on_setting_change)
                
    def stop_listening_to_va(self):
        for entry in self._orig_entries:
            if hasattr(entry, "vigilattr"):
                entry.vigilattr.unsubscribe(self.on_setting_change)

    def duplicate_tab_data_model(self, orig):
        """
        Duplicate a MicroscopyGUIData and adapt it for the acquisition window
        The streams will be shared, but not the views
        orig (MicroscopyGUIData)
        return (MicroscopyGUIData)
        """
        # TODO: we'd better create a new view and copy the streams
        new = copy.copy(orig)  # shallow copy

        new.streams = model.ListVA(orig.streams.value)  # duplicate

        # create view (which cannot move or focus)
        view = guimodel.MicroscopeView("All")

        # differentiate it (only one view)
        new.views = model.ListVA()
        new.views.value.append(view)
        new.focussedView = model.VigilantAttribute(view)
        new.viewLayout = model.IntEnumerated(guimodel.VIEW_LAYOUT_ONE,
                                             choices={guimodel.VIEW_LAYOUT_ONE})
        new.tool = model.IntEnumerated(TOOL_NONE, choices={TOOL_NONE})
        return new

    def add_all_streams(self):
        """
        Add all the streams present in the interface model to the stream panel.
        """
        # the order the streams are added should not matter on the display, so
        # it's ok to not duplicate the streamTree literally

        # go through all the streams available in the interface model
        for s in self._tab_data_model.streams.value:

            if isinstance(s, NON_SPATIAL_STREAMS):
                v = self._hidden_view
            else:
                v = self._view

            self.streambar_controller.addStream(s, add_to_view=v)

    def remove_all_streams(self):
        """
        Remove the streams we added to the view on creation
        Must be called in the main GUI thread
        """
        # Ensure we don't update the view after the window is destroyed
        self.streambar_controller.clear()

        # TODO: need to have a .clear() on the settings_controller to clean up?
        self._settings_controller = None
        self._acq_streams = {}  # also empty the cache

        gc.collect()  # To help reclaiming some memory

    def get_acq_streams(self):
        """
        return (list of Streams): the streams to be acquired
        """
        # Only acquire the streams which are displayed
        streams = self._view.getStreams() + self._hidden_view.getStreams()

        # Add the overlay stream if requested, and folds all the streams
        if streams and self.chkbox_fine_align.Value:
            streams.append(self._ovrl_stream)
        self._acq_streams = acqmng.foldStreams(streams, self._acq_streams)
        return self._acq_streams

    def find_current_preset(self):
        """
        find the name of the preset identical to the current settings (not
          including "Custom")
        returns (string): name of the preset
        raises KeyError: if no preset can be found
        """
        # check each preset
        for n, settings in self._preset_values.items():
            # compare each value between the current and proposed
            different = False
            for entry, value in settings.items():
                if entry.vigilattr.value != value:
                    different = True
                    break
            if not different:
                return n

        raise KeyError()

    def _can_fine_align(self, streams):
        """
        Return True if with the given streams it would make sense to fine align
        streams (iterable of Stream)
        return (bool): True if at least a SEM and an optical stream are present
        """
        # check for a SEM stream
        for s in streams:
            if isinstance(s, EMStream):
                break
        else:
            return False

        # check for an optical stream
        # TODO: allow it also for ScannedFluoStream once fine alignment is supported
        # on confocal SECOM.
        for s in streams:
            if isinstance(s, OpticalStream) and not isinstance(s, ScannedFluoStream):
                break
        else:
            return False

        return True

    @wxlimit_invocation(0.1)
    def update_setting_display(self):
        if not self:
            return

        # if gauge was left over from an error => now hide it
        if self.acquiring:
            self.gauge_acq.Show()
            return
        elif self.gauge_acq.IsShown():
            self.gauge_acq.Hide()
            self.Layout()

        # Enable/disable Fine alignment check box
        streams = self._view.getStreams() + self._hidden_view.getStreams()
        can_fa = self._can_fine_align(streams)
        if self.chkbox_fine_align.Enabled:
            self._prev_fine_align = self.chkbox_fine_align.Value
        self.chkbox_fine_align.Enable(can_fa)
        # Uncheck if disabled, otherwise put same as previous value
        self.chkbox_fine_align.Value = (can_fa and self._prev_fine_align)

        self.update_acquisition_time()

        # update highlight
        for se, value in self._orig_settings.items():
            se.highlight(se.vigilattr.value != value)

    def on_streams_changed(self, val):
        """
        When the list of streams to acquire has changed
        """
        self.update_setting_display()

    def on_setting_change(self, _=None):
        self.update_setting_display()

        # check presets and fall-back to custom
        try:
            preset_name = self.find_current_preset()
            logging.debug("Detected preset %s", preset_name)
        except KeyError:
            # should not happen with the current preset_no_change
            logging.exception("Couldn't match any preset")
            preset_name = u"Custom"

        wx.CallAfter(self.cmb_presets.SetValue, preset_name)

    def update_acquisition_time(self):
        """
        Must be called in the main GUI thread.
        """
        streams = self.get_acq_streams()
        if streams:
            acq_time = acqmng.estimateTime(streams)
            acq_time = math.ceil(acq_time)  # round a bit pessimisticly
            txt = "The estimated acquisition time is {}."
            txt = txt.format(units.readable_time(acq_time))
        else:
            txt = "No streams present."

        self.lbl_acqestimate.SetLabel(txt)

    @call_in_wx_main
    def _onFilename(self, name):
        """ updates the GUI when the filename is updated """
        # decompose into path/file
        path, base = os.path.split(name)
        self.txt_destination.SetValue(str(path))
        # show the end of the path (usually more important)
        self.txt_destination.SetInsertionPointEnd()
        self.txt_filename.SetValue(str(base))

    def on_preset(self, evt):
        preset_name = self.cmb_presets.GetValue()
        try:
            new_preset = self._preset_values[preset_name]
        except KeyError:
            logging.debug("Not changing settings for preset %s", preset_name)
            return

        logging.debug("Changing setting to preset %s", preset_name)

        # TODO: presets should also be able to change the special stream settings
        # (eg: accumulation/interpolation) when we have them

        # apply the recorded values
        apply_preset(new_preset)

        # The hardware might not exactly apply the setting as computed in the
        # preset. We need the _exact_ same value to find back which preset is
        # currently selected. So update the values the first time.
        if preset_name not in self._presets_confirmed:
            for se in new_preset.keys():
                new_preset[se] = se.vigilattr.value
            self._presets_confirmed.add(preset_name)

        self.update_setting_display()

    def on_key(self, evt):
        """ Dialog key press handler. """
        if evt.GetKeyCode() == wx.WXK_ESCAPE:
            self.Close()
        else:
            evt.Skip()

    def on_change_file(self, evt):
        """
        Shows a dialog to change the path, name, and format of the acquisition
        file.
        returns nothing, but updates .filename and .conf
        """
        fn = create_filename(self.conf.last_path, self.conf.fn_ptn,
                             self.conf.last_extension, self.conf.fn_count)
        new_name = ShowAcquisitionFileDialog(self, fn)
        if new_name is not None:
            self.filename.value = new_name
            self.conf.fn_ptn, self.conf.fn_count = guess_pattern(new_name)
            logging.debug("Generated filename pattern '%s'", self.conf.fn_ptn)
            # It means the user wants to do a new acquisition
            self.btn_secom_acquire.SetLabel("START")
            self.last_saved_file = None

    def terminate_listeners(self):
        """
        Disconnect all the connections to the streams.
        Must be called in the main GUI thread.
        """
        # stop listening to events
        self._view.stream_tree.flat.unsubscribe(self.on_streams_changed)
        self._hidden_view.stream_tree.flat.unsubscribe(self.on_streams_changed)
        self.stop_listening_to_va()

        self.remove_all_streams()

    def on_close(self, evt):
        """ Close event handler that executes various cleanup actions
        """
        if self.acq_future:
            # TODO: ask for confirmation before cancelling?
            # What to do if the acquisition is done while asking for
            # confirmation?
            msg = "Cancelling acquisition due to closing the acquisition window"
            logging.info(msg)
            self.acq_future.cancel()

        self.terminate_listeners()

        self.EndModal(wx.ID_CANCEL)

    def _view_file(self):
        """
        Called to open the file which was just acquired
        Must be called in the main GUI thread.
        """
        self.terminate_listeners()

        self.EndModal(wx.ID_OPEN)
        logging.debug("My return code is %d", self.GetReturnCode())

    def _pause_settings(self):
        """ Pause the settings of the GUI and save the values for restoring them later """
        self._settings_controller.pause()
        self._settings_controller.enable(False)

        self.streambar_controller.pause()
        self.streambar_controller.enable(False)

    def _resume_settings(self):
        """ Resume the settings of the GUI and save the values for restoring them later """
        self._settings_controller.enable(True)
        self._settings_controller.resume()

        self.streambar_controller.enable(True)
        self.streambar_controller.resume()

    def on_acquire(self, evt):
        """ Start the actual acquisition """
        if self.last_saved_file:  # This means the button is actually "View"
            self._view_file()
            return

        logging.info("Acquire button clicked, starting acquisition")
        self.acquiring = True

        self.btn_secom_acquire.Disable()

        # disable estimation time updates during acquisition
        self._view.lastUpdate.unsubscribe(self.on_streams_changed)

        # Freeze all the settings so that it's not possible to change anything
        self._pause_settings()

        self.gauge_acq.Show()
        self.Layout()  # to put the gauge at the right place

        # For now, always indicate the best quality (even if the preset is set
        # to "live")
        if self._main_data_model.opm:
            self._main_data_model.opm.setAcqQuality(path.ACQ_QUALITY_BEST)

        # Note: It should never be possible to reach here with no streams
        streams = self.get_acq_streams()
        v_streams = self._view.getStreams()  # visible streams
        for s in streams:
            # Add extra viewable streams to view. However, do not add incompatible streams.
            if s not in v_streams and not isinstance(s, NON_SPATIAL_STREAMS):
                self._view.addStream(s)

            # Update the filename in the streams
            if hasattr(s, "filename"):
                pathname, base = os.path.split(self.filename.value)
                s.filename.value = base

        self.acq_future = acqmng.acquire(streams, self._main_data_model.settings_obs)
        self._acq_future_connector = ProgressiveFutureConnector(self.acq_future,
                                                                self.gauge_acq,
                                                                self.lbl_acqestimate)
        self.acq_future.add_done_callback(self.on_acquisition_done)

        self.btn_cancel.SetLabel("Cancel")
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_cancel)

    def on_cancel(self, evt):
        """ Handle acquisition cancel button click """
        if not self.acq_future:
            logging.warning("Tried to cancel acquisition while it was not started")
            return

        logging.info("Cancel button clicked, stopping acquisition")
        self.acq_future.cancel()
        self.acquiring = False
        self.btn_cancel.SetLabel("Close")
        # all the rest will be handled by on_acquisition_done()

    @call_in_wx_main
    def on_acquisition_done(self, future):
        """ Callback called when the acquisition is finished (either successfully or cancelled) """
        if self._main_data_model.opm:
            self._main_data_model.opm.setAcqQuality(path.ACQ_QUALITY_FAST)

        # bind button back to direct closure
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_close)
        self._resume_settings()
        
        self.acquiring = False

        # re-enable estimation time updates
        self._view.lastUpdate.subscribe(self.on_streams_changed)

        self.acq_future = None  # To avoid holding the ref in memory
        self._acq_future_connector = None

        try:
            data, exp = future.result(1)  # timeout is just for safety
            self.conf.fn_count = update_counter(self.conf.fn_count)
        except CancelledError:
            # put back to original state:
            # re-enable the acquire button
            self.btn_secom_acquire.Enable()

            # hide progress bar (+ put pack estimated time)
            self.update_acquisition_time()
            self.gauge_acq.Hide()
            self.Layout()
            return
        except Exception:
            # We cannot do much: just warn the user and pretend it was cancelled
            logging.exception("Acquisition failed")
            self.btn_secom_acquire.Enable()
            self.lbl_acqestimate.SetLabel("Acquisition failed.")
            self.lbl_acqestimate.Parent.Layout()
            # leave the gauge, to give a hint on what went wrong.
            return

        # Handle the case acquisition failed "a bit"
        if exp:
            logging.warning("Acquisition failed (after %d streams): %s",
                            len(data), exp)

        # save result to file
        self.lbl_acqestimate.SetLabel("Saving file...")
        self.lbl_acqestimate.Parent.Layout()
        try:
            thumb = acqmng.computeThumbnail(self._view.stream_tree, future)
            filename = self.filename.value
            exporter = dataio.get_converter(self.conf.last_format)
            exporter.export(filename, data, thumb)
            logging.info("Acquisition saved as file '%s'.", filename)
            # Allow to see the acquisition
            self.btn_secom_acquire.SetLabel("VIEW")
            self.last_saved_file = filename
        except Exception:
            logging.exception("Saving acquisition failed")
            self.btn_secom_acquire.Enable()
            self.lbl_acqestimate.SetLabel("Saving acquisition file failed.")
            self.lbl_acqestimate.Parent.Layout()
            return

        if exp:
            self.lbl_acqestimate.SetLabel("Acquisition failed (partially).")
        else:
            self.lbl_acqestimate.SetLabel("Acquisition completed.")
            # As the action is complete, rename "Cancel" to "Close"
            self.btn_cancel.SetLabel("Close")
        self.lbl_acqestimate.Parent.Layout()

        # Make sure the file is not overridden
        self.btn_secom_acquire.Enable()

# Step value for z stack levels
DEFAULT_FOV = (100e-6, 100e-6) # m

class OverviewAcquisitionDialog(xrcfr_overview_acq):
    """
    Class used to control the overview acquisition dialog
    The data acquired is stored in a file, with predefined name, available on
      .filename and it is opened (as pyramidal data) in .data .
    """
    def __init__(self, parent, orig_tab_data):
        xrcfr_overview_acq.__init__(self, parent)

        self.conf = get_acqui_conf()

        # True when acquisition occurs
        self.acquiring = False
        self.data = None

        # a ProgressiveFuture if the acquisition is going on
        self.acq_future = None
        self._acq_future_connector = None

        self._main_data_model = orig_tab_data.main

        # duplicate the interface, but with only one view
        self._tab_data_model = self.duplicate_tab_data_model(orig_tab_data)

        # Store the final image as {datelng}-{timelng}-overview
        # The pattern to store them in a sub folder, with the name xxxx-overview-tiles/xxx-overview-NxM.ome.tiff
        # The pattern to use for storing each tile file individually
        # None disables storing them
        save_dir = self.conf.last_path
        if isinstance(orig_tab_data, guimodel.CryoGUIData):
            save_dir = self.conf.pj_last_path
        self.filename = create_filename(save_dir, "{datelng}-{timelng}-overview",
                                              ".ome.tiff")
        assert self.filename.endswith(".ome.tiff")
        dirname, basename = os.path.split(self.filename)
        tiles_dir = os.path.join(dirname, basename[:-len(".ome.tiff")] + "-tiles")
        self.filename_tiles = os.path.join(tiles_dir, basename)

        # Create a new settings controller for the acquisition dialog
        self._settings_controller = LocalizationSettingsController(
            self,
            self._tab_data_model,
        )

        self.zsteps = model.IntContinuous(1, range=(1, 51))
        # The depth of field is an indication of how far the focus needs to move
        # to see the current in-focus position out-of-focus. So it's a good default
        # value for the zstep size. We use 2x to "really" see something else.
        # Typically, it's about 1 µm.
        dof = self._main_data_model.ccd.depthOfField.value
        self.zstep_size = model.FloatContinuous(2 * dof, range=(1e-9, 100e-6), unit="m")
        self._zstep_size_vac = VigilantAttributeConnector(
            self.zstep_size, self.zstep_size_ctrl, events=wx.EVT_COMMAND_ENTER)

        self.tiles_nx = model.IntContinuous(5, range=(1, 1000))
        self.tiles_ny = model.IntContinuous(5, range=(1, 1000))
        self._zsteps_vac = VigilantAttributeConnector(
            self.zsteps, self.zstack_steps, events=wx.EVT_SLIDER)
        self._tiles_n_vacx = VigilantAttributeConnector(
            self.tiles_nx, self.tiles_number_x, events=wx.EVT_COMMAND_ENTER)
        self._tiles_n_vacy = VigilantAttributeConnector(
            self.tiles_ny, self.tiles_number_y, events=wx.EVT_COMMAND_ENTER)

        self.area = None  # None or 4 floats: left, top, right, bottom positions of the acquisition area (in m)

        orig_view = orig_tab_data.focussedView.value
        self._view = self._tab_data_model.focussedView.value

        self.streambar_controller = StreamBarController(self._tab_data_model,
                                                        self.pnl_secom_streams,
                                                        static=True,
                                                        ignore_view=True)
        # The streams currently displayed are the one visible
        self.add_streams()

        # The list of streams ready for acquisition (just used as a cache)
        self._acq_streams = {}

        # Find every setting, and listen to it
        self._orig_entries = get_global_settings_entries(self._settings_controller)
        for sc in self.streambar_controller.stream_controllers:
            self._orig_entries += get_local_settings_entries(sc)

        self.start_listening_to_va()

        # make sure the view displays the same thing as the one we are
        # duplicating
        self._view.view_pos.value = orig_view.view_pos.value
        self._view.mpp.value = orig_view.mpp.value
        self._view.merge_ratio.value = orig_view.merge_ratio.value

        # attach the view to the viewport
        self.pnl_view_acq.canvas.fit_view_to_next_image = False
        self.pnl_view_acq.setView(self._view, self._tab_data_model)

        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)

        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_close)
        self.btn_secom_acquire.Bind(wx.EVT_BUTTON, self.on_acquire)
        self.Bind(wx.EVT_CLOSE, self.on_close)

        # Set parameters for tiled acq
        self.overlap = 0.2
        try:
            # Use the stage range, which can be overridden by the MD_POS_ACTIVE_RANGE.
            # Note: this last one might be temporary, until we have a RoA tool provided in the GUI.
            self._tiling_rng = {
                "x": self._main_data_model.stage.axes["x"].range,
                "y": self._main_data_model.stage.axes["y"].range
            }

            stage_md = self._main_data_model.stage.getMetadata()
            if model.MD_POS_ACTIVE_RANGE in stage_md:
                self._tiling_rng.update(stage_md[model.MD_POS_ACTIVE_RANGE])
        except (KeyError, IndexError):
            raise ValueError("Failed to find stage.MD_POS_ACTIVE_RANGE with x and y range")

        # Note: It should never be possible to reach here with no streams
        streams = self.get_acq_streams()
        for s in streams:
            self._view.addStream(s)

        # To update the estimated time & area when streams are removed/added
        self._view.stream_tree.flat.subscribe(self.on_streams_changed, init=True)


    def start_listening_to_va(self):
        # Get all the VA's from the stream and subscribe to them for changes.
        for entry in self._orig_entries:
            if hasattr(entry, "vigilattr"):
                entry.vigilattr.subscribe(self.on_setting_change)

        self.zsteps.subscribe(self.on_setting_change)
        self.tiles_nx.subscribe(self.on_tiles_number)
        self.tiles_ny.subscribe(self.on_tiles_number)

    def stop_listening_to_va(self):
        for entry in self._orig_entries:
            if hasattr(entry, "vigilattr"):
                entry.vigilattr.unsubscribe(self.on_setting_change)

        self.zsteps.unsubscribe(self.on_setting_change)
        self.tiles_nx.unsubscribe(self.on_tiles_number)
        self.tiles_ny.unsubscribe(self.on_tiles_number)

    def duplicate_tab_data_model(self, orig):
        """
        Duplicate a MicroscopyGUIData and adapt it for the acquisition window
        The streams will be shared, but not the views
        orig (MicroscopyGUIData)
        return (MicroscopyGUIData)
        """
        data_model = AcquisitionWindowData(orig.main)
        data_model.streams.value.extend(orig.streams.value)

        # create view (which cannot move or focus)
        view = guimodel.MicroscopeView("All")
        data_model.views.value = [view]
        data_model.focussedView.value = view
        return data_model

    def add_streams(self):
        """
        Add live streams
        """
        # go through all the streams available in the interface model
        for s in self._tab_data_model.streams.value:

            if not isinstance(s, LiveStream):
                continue

            self.streambar_controller.addStream(s, add_to_view=self._view)

    def remove_all_streams(self):
        """
        Remove the streams we added to the view on creation
        Must be called in the main GUI thread
        """
        # Ensure we don't update the view after the window is destroyed
        self.streambar_controller.clear()

        # TODO: need to have a .clear() on the settings_controller to clean up?
        self._settings_controller = None
        self._acq_streams = {}  # also empty the cache

        gc.collect()  # To help reclaiming some memory

    def get_acq_streams(self):
        """
        return (list of Streams): the streams to be acquired
        """
        # Only acquire the streams which are displayed
        streams = self._view.getStreams()
        return streams

    def update_area_size(self):
        """
        Calculates the requested tiling area size, based on the tiles number
        """
        # get smallest fov
        fovs = [self.get_fov(s) for s in self.get_acq_streams()]
        if not fovs:
            # fall back to a small fov (default)
            self.fov = DEFAULT_FOV
        else:
            # smallest fov
            self.fov = (min(f[0] for f in fovs),
                        min(f[1] for f in fovs))

        nx = self.tiles_nx.value
        ny = self.tiles_ny.value
        # these formulas for w and h have to match the ones used in the 'stitching' module.
        w = nx * self.fov[0] * (1 - self.overlap)
        h = ny * self.fov[1] * (1 - self.overlap)

        pos = self._tab_data_model.main.stage.position.value
        # Note the area can accept LTRB or LBRT.
        self.area = self.clip_tiling_area_to_range(w, h, pos, self._tiling_rng)
        if self.area is None:
            # there is no intersection
            logging.warning("Couldn't find intersection between stage pos %s and tiling range %s" % (pos, self._tiling_rng))

    @wxlimit_invocation(0.1)
    def update_setting_display(self):
        if not self:
            return

        # if gauge was left over from an error => now hide it
        if self.acquiring:
            self.gauge_acq.Show()
            return
        elif self.gauge_acq.IsShown():
            self.gauge_acq.Hide()
            self.Layout()

        # Some settings can affect the FoV. Also, adding/removing the stream with
        # the smallest FoV would also affect the area.
        self.update_area_size()

        # Disable acquisition button if no area
        self.btn_secom_acquire.Enable(self.area is not None)

        if self.area is None:
            self.area_size_txt.SetLabel("Invalid stage position")
            return

        area_size = self.area[2] - self.area[0], self.area[3] - self.area[1]
        area_size_str = util.readable_str(area_size, unit="m", sig=3)
        self.area_size_txt.SetLabel(area_size_str)

        self.update_acquisition_time()

    def on_streams_changed(self, _=None):
        """
        When the list of streams to acquire has changed
        """
        self.update_setting_display()

    def on_tiles_number(self, _=None):
        """
        Called when the user enters values for the tiles number in the GUI.
        """
        self.update_setting_display()

    def on_setting_change(self, _=None):
        self.update_setting_display()

    def update_acquisition_time(self):
        """
        Must be called in the main GUI thread.
        """
        if self.acquiring:
            return

        if not self.area:
            logging.debug("Unknown acquisition area, cannot estimate acquisition time")
            return

        streams = self.get_acq_streams()
        if not streams:
            acq_time = 0
        else:
            zlevels = self._get_zstack_levels()
            focus_mtd = FocusingMethod.MAX_INTENSITY_PROJECTION if zlevels else FocusingMethod.NONE
            acq_time = stitching.estimateTiledAcquisitionTime(streams,
                                                          self._main_data_model.stage,
                                                          self.area, self.overlap,
                                                          zlevels=zlevels,
                                                          focusing_method=focus_mtd)

        txt = "The estimated acquisition time is {}."
        txt = txt.format(units.readable_time(math.ceil(acq_time)))
        self.lbl_acqestimate.SetLabel(txt)

    def get_fov(self, s):
        try:
            return s.guessFoV()
        except (NotImplementedError, AttributeError):
            raise TypeError("Unsupported Stream %s, it doesn't have a .guessFoV()" % (s,))

    @staticmethod
    def clip_tiling_area_to_range(w, h, pos, tiling_rng):
        """
        Finds the intersection between the requested tiling area and the tiling range.
        w (float): width of the tiling area
        h (float): height of the tiling area
        pos (dict -> float): current position of the stage
        tiling_rng (dict -> list): the tiling range along x and y axes as
          (xmin, ymin, xmax, ymax), or (xmin, ymax, xmax, ymin)
        return (None or tuple of 4 floats): None if there is no intersection, or
          the rectangle representing the intersection as (xmin, ymin, xmax, ymax).
        """
        area_req = (pos["x"] - w / 2, pos["y"] - h / 2,
                    pos["x"] + w / 2, pos["y"] + h / 2)
        # clip the tiling area, if needed (or find the intersection between the active range and the requested area)
        return rect_intersect(area_req,
            (tiling_rng["x"][0], tiling_rng["y"][1], tiling_rng["x"][1], tiling_rng["y"][0]))

    def on_key(self, evt):
        """ Dialog key press handler. """
        if evt.GetKeyCode() == wx.WXK_ESCAPE:
            self.Close()
        else:
            evt.Skip()

    def terminate_listeners(self):
        """
        Disconnect all the connections to the streams.
        Must be called in the main GUI thread.
        """
        # stop listening to events
        self._view.stream_tree.flat.unsubscribe(self.on_streams_changed)
        self.stop_listening_to_va()

        self.remove_all_streams()
        # Set the streambar controller to None so it wouldn't be a listener to stream.remove
        self.streambar_controller = None

    def on_close(self, evt):
        """ Close event handler that executes various cleanup actions
        """
        if self.acq_future:
            # TODO: ask for confirmation before cancelling?
            # What to do if the acquisition is done while asking for
            # confirmation?
            msg = "Cancelling acquisition due to closing the acquisition window"
            logging.info(msg)
            self.acq_future.cancel()

        self.terminate_listeners()

        self.EndModal(wx.ID_CANCEL)

    def _pause_settings(self):
        """ Pause the settings of the GUI and save the values for restoring them later """
        self._settings_controller.pause()
        self._settings_controller.enable(False)

        self.streambar_controller.pause()
        self.streambar_controller.enable(False)

    def _resume_settings(self):
        """ Resume the settings of the GUI and save the values for restoring them later """
        self._settings_controller.enable(True)
        self._settings_controller.resume()

        self.streambar_controller.enable(True)
        self.streambar_controller.resume()

    def _get_zstack_levels(self):
        """
        Calculate the zstack levels from the current focus position and zsteps value
        :returns (list(float) or None) zstack levels for zstack acquisition.
          return None if only one zstep is requested.
        """
        zsteps = self.zsteps.value
        if zsteps == 1:
            return None

        # Clip zsteps value to allowed range
        focus_value = self._main_data_model.focus.position.value['z']
        focus_range = self._main_data_model.focus.axes['z'].range
        zmin = focus_value - (zsteps / 2 * self.zstep_size.value)
        zmax = focus_value + (zsteps / 2 * self.zstep_size.value)
        if (zmax - zmin) > (focus_range[1] - focus_range[0]):
            # Corner case: it'd be larger than the entire range => limit to the entire range
            zmin = focus_range[0]
            zmax = focus_range[1]
        if zmax > focus_range[1]:
            # Too high => shift down
            zmax -= zmax - focus_range[1]
            zmin -= zmax - focus_range[1]
        if zmin < focus_range[0]:
            # Too low => shift up
            zmin += focus_range[0] - zmin
            zmax += focus_range[0] - zmin

        # Create focus zlevels from the given zsteps number
        zlevels = numpy.linspace(zmin, zmax, zsteps).tolist()
        return zlevels

    def _fit_view_to_area(self):
        if self.area is None:
            logging.warning("Unknown area, cannot fit view")
            return

        center = ((self.area[0] + self.area[2]) / 2,
                  (self.area[1] + self.area[3]) / 2)
        self._view.view_pos.value = center

        fov = (self.area[2] - self.area[0], self.area[3] - self.area[1])
        self.pnl_view_acq.set_mpp_from_fov(fov)

    def on_acquire(self, evt):
        """ Start the actual acquisition """
        logging.info("Acquire button clicked, starting acquisition")
        acq_streams = self.get_acq_streams()
        if not acq_streams:
            logging.info("No stream to acquire, ending immediately")
            self.on_close(evt)  # Nothing to do, so it's the same as closing the window

        self.acquiring = True

        # Adjust view FoV to the whole area, so that it's possible to follow the acquisition
        self._fit_view_to_area()

        self.btn_secom_acquire.Disable()

        # Freeze all the settings so that it's not possible to change anything
        self._pause_settings()

        self.gauge_acq.Show()
        self.Layout()  # to put the gauge at the right place

        # For now, always indicate the best quality
        if self._main_data_model.opm:
            self._main_data_model.opm.setAcqQuality(path.ACQ_QUALITY_BEST)

        zlevels = self._get_zstack_levels()
        focus_mtd = FocusingMethod.MAX_INTENSITY_PROJECTION if zlevels else FocusingMethod.NONE

        if self.filename_tiles:
            logging.info("Acquisition tiles logged at %s", self.filename_tiles)
            os.makedirs(os.path.dirname(self.filename_tiles))

        self.acq_future = stitching.acquireTiledArea(acq_streams, self._main_data_model.stage, area=self.area,
                                                     overlap=self.overlap,
                                                     settings_obs=self._main_data_model.settings_obs,
                                                     log_path=self.filename_tiles,
                                                     weaver=WEAVER_MEAN,
                                                     registrar=REGISTER_IDENTITY,
                                                     zlevels=zlevels,
                                                     focusing_method=focus_mtd)
        self._acq_future_connector = ProgressiveFutureConnector(self.acq_future,
                                                                self.gauge_acq,
                                                                self.lbl_acqestimate)
        # TODO: Build-up the complete image during the acquisition, so that the
        #       progress can be followed live.
        self.acq_future.add_done_callback(self.on_acquisition_done)

        self.btn_cancel.SetLabel("Cancel")
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_cancel)

    def on_cancel(self, evt):
        """ Handle acquisition cancel button click """
        logging.info("Cancel button clicked, stopping acquisition")
        self.acq_future.cancel()
        self.acquiring = False
        self.btn_cancel.SetLabel("Close")
        # all the rest will be handled by on_acquisition_done()

    @call_in_wx_main
    def on_acquisition_done(self, future):
        """ Callback called when the acquisition is finished (either successfully or cancelled) """
        if self._main_data_model.opm:
            self._main_data_model.opm.setAcqQuality(path.ACQ_QUALITY_FAST)

        # bind button back to direct closure
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_close)
        self._resume_settings()

        self.acquiring = False

        self.acq_future = None  # To avoid holding the ref in memory
        self._acq_future_connector = None

        try:
            data = future.result(1)  # timeout is just for safety
            self.conf.fn_count = update_counter(self.conf.fn_count)
        except CancelledError:
            # put back to original state:
            # re-enable the acquire button
            self.btn_secom_acquire.Enable()

            # hide progress bar (+ put pack estimated time)
            self.update_acquisition_time()
            self.gauge_acq.Hide()
            self.Layout()
            return
        except Exception:
            # We cannot do much: just warn the user and pretend it was cancelled
            logging.exception("Acquisition failed")
            self.btn_secom_acquire.Enable()
            self.lbl_acqestimate.SetLabel("Acquisition failed.")
            self.lbl_acqestimate.Parent.Layout()
            # leave the gauge, to give a hint on what went wrong.
            return

        # Now store the data (as pyramidal data), and open it again (but now it's
        # backed with the persistent storage.
        try:
            exporter = dataio.find_fittest_converter(self.filename)
            if exporter.CAN_SAVE_PYRAMID:
                exporter.export(self.filename, data, pyramid=True)
            else:
                logging.warning("File format doesn't support saving image in pyramidal form")
                exporter.export(self.filename)
            self.data = exporter.open_data(self.filename).content
        except Exception:
            # We cannot do much: just warn the user and pretend it was cancelled
            logging.exception("Storage failed")
            self.btn_secom_acquire.Enable()
            self.lbl_acqestimate.SetLabel("Storage failed.")
            self.lbl_acqestimate.Parent.Layout()
            return

        self.terminate_listeners()
        self.EndModal(wx.ID_OPEN)


def ShowAcquisitionFileDialog(parent, filename):
    """
    parent (wxFrame): parent window
    filename (string): full filename to propose by default
    Note: updates the acquisition configuration if the user did pick a new file
    return (string or None): the new filename (or the None if the user cancelled)
    """
    conf = get_acqui_conf()

    # Find the available formats (and corresponding extensions)
    formats_to_ext = dataio.get_available_formats()

    # current filename
    path, base = os.path.split(filename)

    # Note: When setting 'defaultFile' when creating the file dialog, the
    #   first filter will automatically be added to the name. Since it
    #   cannot be changed by selecting a different file type, this is big
    #   nono. Also, extensions with multiple periods ('.') are not correctly
    #   handled. The solution is to use the SetFilename method instead.
    wildcards, formats = formats_to_wildcards(formats_to_ext)
    dialog = wx.FileDialog(parent,
                           message="Choose a filename and destination",
                           defaultDir=path,
                           defaultFile="",
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
                           wildcard=wildcards)

    # Select the last format used
    prev_fmt = conf.last_format
    try:
        idx = formats.index(conf.last_format)
    except ValueError:
        idx = 0
    dialog.SetFilterIndex(idx)

    # Strip the extension, so that if the user changes the file format,
    # it will not have 2 extensions in a row.
    if base.endswith(conf.last_extension):
        base = base[:-len(conf.last_extension)]
    dialog.SetFilename(base)

    # Show the dialog and check whether is was accepted or cancelled
    if dialog.ShowModal() != wx.ID_OK:
        return None

    # New location and name have been selected...
    # Store the path
    path = dialog.GetDirectory()
    conf.last_path = path

    # Store the format
    fmt = formats[dialog.GetFilterIndex()]
    conf.last_format = fmt

    # Check the filename has a good extension, or add the default one
    fn = dialog.GetFilename()
    ext = None
    for extension in formats_to_ext[fmt]:
        if fn.endswith(extension) and len(extension) > len(ext or ""):
            ext = extension

    if ext is None:
        if fmt == prev_fmt and conf.last_extension in formats_to_ext[fmt]:
            # if the format is the same (and extension is compatible): keep
            # the extension. This avoid changing the extension if it's not
            # the default one.
            ext = conf.last_extension
        else:
            ext = formats_to_ext[fmt][0] # default extension
        fn += ext

    conf.last_extension = ext

    return os.path.join(path, fn)

def ShowChamberFileDialog(parent, projectname):
    """
    parent (wxframe): parent window
    projectname (string): project name to propose by default
    return (string or none): the new project name (or the none if the user cancelled)
    """
    # current project name
    path, base = os.path.split(projectname)
    dialog = wx.FileDialog(parent,
                           message="Choose a project name and destination",
                           defaultDir=path,
                           defaultFile="",
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
                           wildcard="*")
    dialog.SetFilename(base)

    # Show the dialog and check whether is was accepted or cancelled
    if dialog.ShowModal() != wx.ID_OK:
        return None

    # New location and name have been selected...
    path = dialog.GetDirectory()
    fn = dialog.GetFilename()
    return os.path.join(path, fn)
