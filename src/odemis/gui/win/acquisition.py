import copy
import logging
import math
import os.path
import time
from collections import OrderedDict
from concurrent.futures._base import CancelledError

import wx
from wx.lib.pubsub import pub

from odemis import model, dataio
from odemis.gui import acqmng, instrmodel
from odemis.gui.conf import get_acqui_conf
from odemis.gui.cont.settings import SecomSettingsController
from odemis.gui.cont.streams import StreamController
from odemis.gui.instrmodel import VIEW_LAYOUT_ONE
from odemis.gui.main_xrc import xrcfr_acq
from odemis.gui.util import units, call_after

def preset_hq(entries):
    """
    Preset for highest quality image
    entries (list of SettingEntries): each value as originally set
    returns (dict SettingEntries -> value): new value for each SettingEntry that should be modified
    """
    ret = {}
    for entry in entries:
        if not entry.va or entry.va.readonly:
            # not a real setting, just info
            logging.debug("Skipping the value %s", entry.name)
            continue


        value = entry.va.value
        if entry.name == "resolution":
            # if resolution => get the best one
            try:
                value = entry.va.range[1] # max
            except (AttributeError, model.NotApplicableError):
                pass
        elif entry.name in ("exposureTime", "dwellTime"):
            # if exposureTime/dwellTime => x10
            value = entry.va.value * 10

            # make sure it still fits
            if isinstance(entry.va.range, tuple):
                value = sorted(entry.va.range + (value,))[1] # clip

        elif entry.name == "binning":
            # if binning => smallest
            try:
                value = entry.va.range[0] # min
            except (AttributeError, model.NotApplicableError):
                try:
                    value = min(entry.va.choices)
                except (AttributeError, model.NotApplicableError):
                    pass
            # TODO: multiply exposuretime by the original binning
        elif entry.name == "readoutRate":
            # if readoutrate => smallest
            try:
                value = entry.va.range[0] # min
            except (AttributeError, model.NotApplicableError):
                try:
                    value = min(entry.va.choices)
                except (AttributeError, model.NotApplicableError):
                    pass
        # rest => as is

        logging.debug("Adapting value %s from %s to %s", entry.name, entry.va.value, value)
        ret[entry] = value

    return ret

def preset_as_is(entries):
    """
    Preset which don't change anything (exactly as live)
    entries (list of SettingEntries): each value as originally set
    returns (dict SettingEntries -> value): new value for each SettingEntry that
        should be modified
    """
    ret = {}
    for entry in entries:
        if not entry.va or entry.va.readonly:
            # not a real setting, just info
            logging.debug("Skipping the value %s", entry.name)
            continue

        # everything as-is
        logging.debug("Copying value %s = %s", entry.name, entry.va.value)
        ret[entry] = entry.va.value

    return ret

def preset_no_change(entries):
    """
    Special preset which matches everything and doesn't change anything
    """
    return {}


# Name -> callable (list of SettingEntries -> dict (SettingEntries -> value))
presets = OrderedDict(
            (   (u"High quality", preset_hq),
                (u"Fast", preset_as_is),
                (u"Custom", preset_no_change)
            )
)

class AcquisitionDialog(xrcfr_acq):
    """ Wrapper class responsible for additional initialization of the
    Acquisition Dialog created in XRCed
    """

    def __init__(self, parent, interface_model):
        xrcfr_acq.__init__(self, parent)

        self.conf = get_acqui_conf()

        for n in presets:
            self.cmb_presets.Append(n)
        # TODO: record and reuse the preset used?
        self.cmb_presets.Select(0)

        self.filename = model.StringVA(self._get_default_filename())
        self.filename.subscribe(self._onFilename, init=True)

        # a ProgressiveFuture if the acquisition is going on
        self.acq_future = None

        # Create a new settings controller for the acquisition dialog
        self.settings_controller = SecomSettingsController(self,
                                                           interface_model,
                                                           True)
        # FIXME: pass the fold_panels

        # Compute the preset values for each preset
        self._preset_values = {} # dict string ->  dict (SettingEntries -> value)
        orig_entries = self.settings_controller.entries
        self._orig_settings = preset_as_is(orig_entries) # used to detect changes
        for n, preset in presets.items():
            self._preset_values[n] = preset(orig_entries)
        # Presets which have been confirmed on the hardware
        self._presets_confirmed = set() # (string)

        # duplicate the interface, but with only one view
        self.interface_model = self.duplicate_interface_model(interface_model)

        orig_view = interface_model.focussedView.value
        view = self.interface_model.focussedView.value

        self.stream_controller = StreamController(self.interface_model,
                                                  self.pnl_secom_streams)
        # The streams currently displayed are the one
        self.add_all_streams(orig_view.getStreams())

        # make sure the view displays the same thing as the one we are
        # duplicating
        view.view_pos.value = orig_view.view_pos.value
        view.mpp.value = orig_view.mpp.value
        view.merge_ratio.value = orig_view.merge_ratio.value

        # attach the view to the viewport
        self.pnl_view_acq.setView(view, self.interface_model)

        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)

        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_close)
        self.btn_change_file.Bind(wx.EVT_BUTTON, self.on_change_file)
        self.btn_secom_acquire.Bind(wx.EVT_BUTTON, self.on_acquire)
        self.cmb_presets.Bind(wx.EVT_COMBOBOX, self.on_preset)
        self.Bind(wx.EVT_CLOSE, self.on_close)

        self.on_preset(None) # will force setting the current preset

        pub.subscribe(self.on_setting_change, 'setting.changed')


    def duplicate_interface_model(self, orig):
        """
        Duplicate a MicroscopeModel and adapt it for the acquisition window
        The streams will be shared, but not the views
        orig (MicroscopeModel)
        return (MicroscopeModel)
        """
        new = copy.copy(orig) # shallow copy

        # create view (which cannot move or focus)
        view = instrmodel.MicroscopeView(orig.focussedView.value.name.value)

        # differentiate it (only one view)
        new.views = {"all": view}
        new.focussedView = model.VigilantAttribute(view)
        new.viewLayout = model.IntEnumerated(VIEW_LAYOUT_ONE,
                                              choices=set([VIEW_LAYOUT_ONE]))

        return new

    def add_all_streams(self, visible_streams):
        """
        Add all the streams present in the interface model to the stream panel.
        visible_streams (list of streams): the streams that should be visible
        """
        # the order the streams are added should not matter on the display, so
        # it's ok to not duplicate the streamTree literally
        view = self.interface_model.focussedView.value

        # go through all the streams available in the interface model
        for s in self.interface_model.streams:
            # add to the stream bar
            sp = self.stream_controller.addStreamForAcquisition(s)
            if s in visible_streams:
                view.addStream(s)
                sp.show_stream()
            else:
                sp.hide_stream()

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
                if entry.va.value != value:
                    different = True
                    break
            if not different:
                return n

        raise KeyError()

    def update_setting_display(self):
        # if gauge was left over from an error => now hide it
        if self.gauge_acq.IsShown():
            self.gauge_acq.Hide()
            self.Layout()

        self.update_acquisition_time()

        # update highlight
        for se, value in self._orig_settings.items():
            se.highlight(se.va.value != value)

    def on_setting_change(self, setting_ctrl):
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
        st = self.interface_model.focussedView.value.stream_tree
        if st.streams:
            acq_time = acqmng.estimateAcquistionTime(st)
            self.gauge_acq.Range = 100 * acq_time
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
        return os.path.join(self.conf.last_path, 
                            u"%s%s" % (time.strftime("%Y%m%d-%H%M%S"),
                                             self.conf.last_extension)
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
        for se, value in new_preset.items():
            # TODO: it might be more tricky that this because some values might
            # affect others like resolution/binning => change them in a specific
            # order.
            se.va.value = value

        # The hardware might not exactly apply the setting as computed in the
        # preset. We need the _exact_ same value to find back which preset is
        # currently selected. So update the values the first time.
        if not preset_name in self._presets_confirmed:
            for se in new_preset.keys():
                new_preset[se] = se.va.value
            self._presets_confirmed.add(preset_name)

        self.update_setting_display()

    def on_key(self, evt):
        """ Dialog key press handler. """
        if evt.GetKeyCode() == wx.WXK_ESCAPE:
            self.Close()
        else:
            evt.Skip()

    @staticmethod
    def _convert_formats_to_wildcards(formats2ext):
        """Convert formats into wildcards string compatible with wx.FileDialog()

        formats2ext (dict (string -> list of strings)): format names and lists of
            their possible extensions.

        returns (tuple (string, list of strings)): wildcards, name of the format
            in the same order as in the wildcards
        """
        wildcards = []
        formats = []
        for fmt, extensions in formats2ext.items():
            ext_wildcards = ";".join(["*" + e for e in extensions])
            wildcard = "%s files (%s)|%s" % (fmt, ext_wildcards, ext_wildcards)
            formats.append(fmt)
            wildcards.append(wildcard)

        # the whole importance is that they are in the same order
        return "|".join(wildcards), formats

    def on_change_file(self, evt):
        """
        Shows a dialog to change the path, name, and format of the acquisition
        file.
        returns nothing, but updates .filename and .conf
        """
        # Find the available formats (and corresponding extensions)
        formats_to_ext = dataio.get_available_formats()
        
        # current filename
        path, base = os.path.split(self.filename.value)
        
        # Note: When setting 'defaultFile' when creating the file dialog, the
        #   first filter will automatically be added to the name. Since it
        #   cannot be changed by selecting a different file type, this is big
        #   nono. Also, extensions with multiple periods ('.') are not correctly
        #   handled. The solution is to use the SetFilename method instead.
        wildcards, formats = self._convert_formats_to_wildcards(formats_to_ext)
        dialog = wx.FileDialog(self,
                               message="Choose a filename and destination",
                               defaultDir=path,
                               defaultFile="",
                               style=wx.FD_SAVE|wx.FD_OVERWRITE_PROMPT,
                               wildcard=wildcards)

        # Select the last format used
        prev_fmt = self.conf.last_format
        try:
            idx = formats.index(self.conf.last_format)
        except ValueError:
            idx = 0
        dialog.SetFilterIndex(idx)

        # Strip the extension, so that if the user changes the file format,
        # it will not have 2 extensions in a row.
        if base.endswith(self.conf.last_extension):
            base = base[:-len(self.conf.last_extension)]
        dialog.SetFilename(base)

        # Show the dialog and check whether is was accepted or cancelled
        if dialog.ShowModal() != wx.ID_OK:
            return

        # New location and name have been selected...
        # Store the path
        path = dialog.GetDirectory()
        self.conf.last_path = path

        # Store the format
        fmt = formats[dialog.GetFilterIndex()]
        self.conf.last_format = fmt

        # Check the filename has a good extension, or add the default one
        fn = dialog.GetFilename()
        ext = None
        for extension in formats_to_ext[fmt]:
            if fn.endswith(extension) and len(extension) > len(ext or ""):
                ext = extension

        if ext is None:
            if fmt == prev_fmt and self.conf.last_extension in formats_to_ext[fmt]:
                # if the format is the same (and extension is compatible): keep
                # the extension. This avoid changing the extension if it's not
                # the default one.
                ext = self.conf.last_extension
            else:
                ext = formats_to_ext[fmt][0] # default extension
            fn += ext

        self.conf.last_extension = ext

        # save the filename
        self.filename.value = os.path.join(path, fn)

        self.conf.write()

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

        # stop listening to events
        pub.unsubscribe(self.on_setting_change, 'setting.changed')

        self.Destroy()

    def on_acquire(self, evt):
        """
        Start the acquisition (really)
        """
        st = self.interface_model.focussedView.value.streams
        # It should never be possible to reach here with an empty streamTree

        # start acquisition + connect events to callback
        self.acq_future = acqmng.startAcquisition(st)
        self.acq_future.add_update_callback(self.on_acquisition_upd)
        self.acq_future.add_done_callback(self.on_acquisition_done)

        self.btn_secom_acquire.Disable()
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_cancel)

        # the range of the progress bar was already set in
        # update_acquisition_time()
        self.gauge_acq.Value = 0
        self.gauge_acq.Show()
        self.Layout() # to put the gauge at the right place

    def on_cancel(self, evt):
        """
        Called during acquisition when pressing the cancel button
        """
        if not self.acq_future:
            msg = "Tried to cancel acquisition while it was not started"
            logging.warning(msg)
            return

        self.acq_future.cancel()
        # all the rest will be handled by on_acquisition_done()

    @call_after
    def on_acquisition_done(self, future):
        """
        Callback called when the acquisition is finished (either successfully or
        cancelled)
        """
        # bind button back to direct closure
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_close)

        try:
            data, thumb = future.result(1) # timeout is just for safety
            # make sure the progress bar is at 100%
            self.gauge_acq.Value = self.gauge_acq.Range
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

        # save result to file
        try:
            filename = self.filename.value
            exporter = dataio.get_exporter(self.conf.last_format)
            exporter.export(filename, data, thumb)
            logging.info("Acquisition saved as file '%s'.", filename)
        except Exception:
            logging.exception("Saving acquisition failed")
            self.btn_secom_acquire.Enable()
            self.lbl_acqestimate.SetLabel("Saving acquisition file failed.")
            return

        self.lbl_acqestimate.SetLabel("Acquisition completed.")

        # change the "cancel" button to "close"
        self.btn_cancel.SetLabel("Close")

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
        logging.debug("updating the progress bar to %f/%f", past, past + left)
        self.gauge_acq.Range = 100 * (past + left)
        self.gauge_acq.Value = 100 * past

        left = math.ceil(left) # pessimistic
        if left > 2:
            lbl_txt = "%s left." % units.readable_time(left)
            self.lbl_acqestimate.SetLabel(lbl_txt)
        else:
            # don't be too precise
            self.lbl_acqestimate.SetLabel("a few seconds left.")
