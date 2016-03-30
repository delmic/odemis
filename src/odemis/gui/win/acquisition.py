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

from __future__ import division

from concurrent.futures._base import CancelledError
import copy
import logging
import math
from odemis import acq, model, dataio
from odemis.acq import stream
from odemis.acq.stream import EMStream, OpticalStream
from odemis.gui.acqmng import presets, preset_as_is, apply_preset, \
    get_global_settings_entries, get_local_settings_entries
from odemis.gui.conf import get_acqui_conf
from odemis.gui.cont.settings import SecomSettingsController
from odemis.gui.cont.streams import StreamBarController
from odemis.gui.main_xrc import xrcfr_acq
from odemis.gui.util import call_in_wx_main, formats_to_wildcards, \
    wxlimit_invocation
from odemis.gui.util.widgets import ProgressiveFutureConnector
from odemis.util import units
import os.path
import time
import wx
from wx.lib.pubsub import pub

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

        self.filename = model.StringVA(self._get_default_filename())
        self.filename.subscribe(self._onFilename, init=True)

        # The name of the last file that got written to disk (used for auto viewing on close)
        self.last_saved_file = None

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

        # To turn on/off the fan
        self._orig_fan_speed = None

        orig_view = orig_tab_data.focussedView.value
        self._view = self._tab_data_model.focussedView.value

        self.streambar_controller = StreamBarController(self._tab_data_model,
                                                        self.pnl_secom_streams)
        # The streams currently displayed are the one visible
        self.add_all_streams()

        # FIXME: pass the fold_panels

        # Compute the preset values for each preset
        self._preset_values = {}  # dict string -> dict (SettingEntries -> value)
        orig_entries = get_global_settings_entries(self._settings_controller)
        for sc in self.streambar_controller.stream_controllers:
            orig_entries += get_local_settings_entries(sc)
        self._orig_settings = preset_as_is(orig_entries) # to detect changes
        for n, preset in presets.items():
            self._preset_values[n] = preset(orig_entries)
        # Presets which have been confirmed on the hardware
        self._presets_confirmed = set() # (string)

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
                elif isinstance(s, OpticalStream):
                    opt_det = s.detector
            self._ovrl_stream = stream.OverlayStream("Fine alignment", opt_det, em_emt, em_det)
            if self._main_data_model.role == "delphi":
                self._main_data_model.fineAlignDwellTime.value = 0.5
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
        self.pnl_view_acq.setView(self._view, self._tab_data_model)

        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)

        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_close)
        self.btn_change_file.Bind(wx.EVT_BUTTON, self.on_change_file)
        self.btn_secom_acquire.Bind(wx.EVT_BUTTON, self.on_acquire)
        self.cmb_presets.Bind(wx.EVT_COMBOBOX, self.on_preset)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        # on_streams_changed is compatible because it doesn't use the args
        self.chkbox_fine_align.Bind(wx.EVT_CHECKBOX, self.on_streams_changed)

        self.on_preset(None) # will force setting the current preset

        pub.subscribe(self.on_setting_change, 'setting.changed')
        # TODO: we should actually listen to the stream tree, but it's not
        # currently possible.
        # Currently just use view.lastUpdate which should be "similar"
        # (but doesn't work if the stream contains no image)
        self._view.lastUpdate.subscribe(self.on_streams_changed)

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
                                             choices=set([guimodel.VIEW_LAYOUT_ONE]))

        return new

    def add_all_streams(self):
        """
        Add all the streams present in the interface model to the stream panel.
        """
        # the order the streams are added should not matter on the display, so
        # it's ok to not duplicate the streamTree literally

        # go through all the streams available in the interface model
        for s in self._tab_data_model.streams.value:
            self._view.addStream(s)  # Add first to the view, so "visible" button is correct
            self.streambar_controller.add_acquisition_stream_cont(s)

    def remove_all_streams(self):
        """ Remove the streams we added to the view on creation """
        # Ensure we don't update the view after the window is destroyed
        for s in list(self._tab_data_model.streams.value):  # copy, as it's modified by stream cont
            self.streambar_controller.removeStream(s)

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
        for s in streams:
            if isinstance(s, OpticalStream):
                break
        else:
            return False

        return True

    @wxlimit_invocation(0.1)
    def update_setting_display(self):
        # if gauge was left over from an error => now hide it
        if self.gauge_acq.IsShown():
            self.gauge_acq.Hide()
            self.Layout()

        # Enable/disable Fine alignment check box
        streams = self._view.getStreams()
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

    def on_setting_change(self):
        self.update_setting_display()

        # check presets and fall-back to custom
        try:
            preset_name = self.find_current_preset()
            logging.debug("Detected preset %s", preset_name)
        except KeyError:
            # should not happen with the current preset_no_change
            logging.exception("Couldn't match any preset")
            preset_name = u"Custom"

        self.cmb_presets.SetValue(preset_name)

    def update_acquisition_time(self):
        streams = self._view.getStreams()
        if streams:
            if self.chkbox_fine_align.Value:
                streams.append(self._ovrl_stream)
            acq_time = acq.estimateTime(streams)
            acq_time = math.ceil(acq_time) # round a bit pessimistically
            txt = "The estimated acquisition time is {}."
            txt = txt.format(units.readable_time(acq_time))
        else:
            txt = "No streams present."

        self.lbl_acqestimate.SetLabel(txt)

    def _get_default_filename(self):
        """
        Return a good default filename
        """
        # TODO: check the file doesn't yet exist (if the computer clock is
        # correct it's unlikely)
        return os.path.join(
            self.conf.last_path,
            u"%s%s" % (time.strftime("%Y%m%d-%H%M%S"), self.conf.last_extension)
        )

    def _onFilename(self, name):
        """ updates the GUI when the filename is updated """
        # decompose into path/file
        path, base = os.path.split(name)
        self.txt_destination.SetValue(unicode(path))
        # show the end of the path (usually more important)
        self.txt_destination.SetInsertionPointEnd()
        self.txt_filename.SetValue(unicode(base))

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
        # TODO: this should not be necessary once the settings only change the
        # stream settings, and not directly the hardware.
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
        new_name = ShowAcquisitionFileDialog(self, self.filename.value)
        if new_name is not None:
            self.filename.value = new_name
            # It means the user wants to do a new acquisition
            self.btn_secom_acquire.SetLabel("START")
            self.last_saved_file = None

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

        self.remove_all_streams()
        # stop listening to events
        pub.unsubscribe(self.on_setting_change, 'setting.changed')
        self._view.lastUpdate.unsubscribe(self.on_streams_changed)

        self.EndModal(wx.ID_CANCEL)

    def _view_file(self):
        """
        Called to open the file which was just acquired
        """

        self.remove_all_streams()
        # stop listening to events
        pub.unsubscribe(self.on_setting_change, 'setting.changed')
        self._view.lastUpdate.unsubscribe(self.on_streams_changed)

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

    def _set_fan(self, enable):
        """
        Turn on/off the fan of the CCD
        enable (boolean): True to turn on/restore the fan, and False to turn if off
        """
        if model.hasVA(self._main_data_model.ccd, "fanSpeed"):
            return

        fs = self._main_data_model.ccd.fanSpeed
        if enable:
            if self._orig_fan_speed is not None:
                fs.value = max(fs.value, self._orig_fan_speed)
        else:
            self._orig_fan_speed = fs.value
            fs.value = 0

    def on_acquire(self, evt):
        """ Start the actual acquisition """
        if self.last_saved_file:  # This means the button is actually "View"
            self._view_file()
            return

        logging.info("Acquire button clicked, starting acquisition")

        self._main_data_model.is_acquiring.value = True
        self.btn_secom_acquire.Disable()

        # disable estimation time updates during acquisition
        self._view.lastUpdate.unsubscribe(self.on_streams_changed)

        # TODO: freeze all the settings so that it's not possible to change anything
        self._pause_settings()

        self.gauge_acq.Show()
        self.Layout()  # to put the gauge at the right place

        # start acquisition + connect events to callback
        streams = self._view.getStreams()

        # Add the overlay stream if the fine alignment check box is checked
        if self.chkbox_fine_align.Value:
            streams.append(self._ovrl_stream)

        # Turn off the fan to avoid vibrations (in all acquisitions)
        self._set_fan(False)

        # It should never be possible to reach here with no streams
        self.acq_future = acq.acquire(streams)
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
        self.btn_cancel.SetLabel("Close")
        # all the rest will be handled by on_acquisition_done()

    @call_in_wx_main
    def on_acquisition_done(self, future):
        """ Callback called when the acquisition is finished (either successfully or cancelled) """
        self._set_fan(True)  # Turn the fan back on
        self._main_data_model.is_acquiring.value = False

        # bind button back to direct closure
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_close)
        self._resume_settings()

        # re-enable estimation time updates
        self._view.lastUpdate.subscribe(self.on_streams_changed)

        try:
            data, exp = future.result(1) # timeout is just for safety
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
            # leave the gauge, to give a hint on what went wrong.
            return

        # Handle the case acquisition failed "a bit"
        if exp:
            logging.error("Acquisition failed (after %d streams): %s",
                          len(data), exp)

        # save result to file
        self.lbl_acqestimate.SetLabel("Saving file...")
        try:
            thumb = acq.computeThumbnail(self._view.stream_tree, future)
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
            return

        if exp:
            self.lbl_acqestimate.SetLabel("Acquisition failed (partially).")
        else:
            self.lbl_acqestimate.SetLabel("Acquisition completed.")
            # As the action is complete, rename "Cancel" to "Close"
            self.btn_cancel.SetLabel("Close")

        # Make sure the file is not overridden
        self.btn_secom_acquire.Enable()


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

