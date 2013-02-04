# -*- coding: utf-8 -*-
"""
Created on 22 Aug 2012

@author: Éric Piel

Copyright © 2012-2013 Éric Piel, Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 2 of the License, or (at your option) any later
version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.


### Purpose ###

This module contains classes to control the actions related to the acquisition
of microscope images.

"""

import logging
import os
import re
import subprocess
import sys
import threading
import time

import wx
from wx.lib.pubsub import pub

from odemis import model, dataio
from odemis.gui.conf import get_acqui_conf
from odemis.gui.cont.settingspanel import SettingsBarController
from odemis.gui.main_xrc import xrcfr_acq
from odemis.gui.util import img, get_picture_folder
from odemis.gui.util.conversion import seconds2human



class AcquisitionController(object):
    """ controller to handle snapshot and high-res image acquisition in a "global"
    context. In particular, it needs to be aware of which viewport is currently
    focused, and block any change of settings during acquisition.
    """
    def __init__(self, micgui, main_frame):
        """
        micgui (GUIMicroscope): the representation of the microscope GUI
        main_frame: (wx.Frame): the frame which contains the 4 viewports
        """
        self._microscope = micgui
        self._main_frame = main_frame
        self._anim_thread = None

        # nice default paths
        # Snapshots: always the "Pictures" user folder
        self._snapshot_folder = get_picture_folder()
        # High-res: last folder selected, and default to same as snapshot
        self._acquisition_folder = self._snapshot_folder

        # Event binding

        # Link snapshot menu to snapshot action
        wx.EVT_MENU(self._main_frame,
            self._main_frame.menu_item_qacquire.GetId(),
            self.start_snapshot_viewport)

        # Link "acquire image" button to image acquisition
        self._main_frame.btn_acquire.Bind(wx.EVT_BUTTON, self.open_acquisition_dialog)

        # find the names of the active (=connected) screens
        # it's slow, so do it only at init (=expect not to change screen during acquisition)
        self._outputs = self.get_display_outputs()

        pub.subscribe(self.on_stream_changed, 'stream.ctrl')


    def on_stream_changed(self, streams_present, streams_visible):
        """ Handler for pubsub 'stream.changed' messages """
        self._main_frame.btn_acquire.Enable(streams_present and streams_visible)

    def onTakeScreenShot(self):
        """ Takes a screenshot of the screen at give pos & size (rect). """
        logging.debug('Starting screenshot')
        rect = self._main_frame.GetRect()
        # see http://aspn.activestate.com/ASPN/Mail/Message/wxpython-users/3575899
        # created by Andrea Gavana

        # adjust widths for Linux (figured out by John Torres
        # http://article.gmane.org/gmane.comp.python.wxpython/67327)
        if sys.platform == 'linux2':
            client_x, client_y = self._main_frame.ClientToScreen((0, 0))
            border_width = client_x - rect.x
            title_bar_height = client_y - rect.y
            rect.width += (border_width * 2)
            rect.height += title_bar_height + border_width

        #Create a DC for the whole screen area
        dcScreen = wx.ScreenDC()

        #Create a Bitmap that will hold the screenshot image later on
        #Note that the Bitmap must have a size big enough to hold the screenshot
        #-1 means using the current default colour depth
        bmp = wx.EmptyBitmap(rect.width, rect.height)

        #Create a memory DC that will be used for actually taking the screenshot
        memDC = wx.MemoryDC()

        #Tell the memory DC to use our Bitmap
        #all drawing action on the memory DC will go to the Bitmap now
        memDC.SelectObject(bmp)

        #Blit (in this case copy) the actual screen on the memory DC
        #and thus the Bitmap
        memDC.Blit( 0, #Copy to this X coordinate
                    0, #Copy to this Y coordinate
                    rect.width, #Copy this width
                    rect.height, #Copy this height
                    dcScreen, #From where do we copy?
                    rect.x, #What's the X offset in the original DC?
                    rect.y  #What's the Y offset in the original DC?
                    )

        #Select the Bitmap out of the memory DC by selecting a new
        #uninitialized Bitmap
        memDC.SelectObject(wx.NullBitmap)

        return bmp.ConvertToImage()

    def open_acquisition_dialog(self, evt):

        parent_size = [v * 0.66 for v in self._main_frame.GetSize()]

        acq_dialog = AcquisitionDialog(self._main_frame,
                                             wx.GetApp().interface_model)

        acq_dialog.SetSize(parent_size)
        acq_dialog.Center()
        acq_dialog.ShowModal()

        # Make sure that the acquisition button is enabled again.
        self._main_frame.btn_acquire.Enable()

    def start_snapshot_viewport(self, event):
        """
        wrapper to run snapshot_viewport in a separate thread as it can take time
        """
        thread = threading.Thread(target=self.snapshot_viewport)
        thread.start()

    def snapshot_viewport(self):
        """
        Save a snapshot of the raw image from the focused viewport on the filesystem.
        The name of the file follows the scheme date-time.tiff (e.g.,
        20120808-154812.tiff) and is located in the user's picture directory.
        """
        # TODO: allow user to chose the file format in preferences
        import odemis.dataio.tiff as exporter
        #import odemis.dataio.hdf5 as exporter

        # filename
        dirname = self._snapshot_folder
        basename = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        extention = exporter.EXTENSIONS[0] # includes the .
        filename = os.path.join(dirname, basename + extention)
        if os.path.exists(filename):
            logging.warning("File '%s' for snapshot already exists, cancelling snapshot",
                            filename)
            return

        # get currently focused view
        view = self._microscope.focussedView.value
        if not view:
            logging.warning("Failed to take snapshot, no view is selected")
            return

        streams = view.getStreams()
        if len(streams) == 0:
            logging.warning("Failed to take snapshot, no stream visible in view %s", view.name.value)
            return

        self.start_snapshot_animation()

        # let's try to get a thumbnail
        if view.thumbnail.value is None:
            thumbnail = None
        else:
            # need to convert from wx.Image to ndimage
            thumbnail = img.wxImage2NDImage(view.thumbnail.value, keep_alpha=False)
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
            if len(data) == 0:
                logging.warning("Failed to get the last raw image of stream %s, will acquire a new one", s.name.value)
                # FIXME: ask the stream to get activated and return an image
                # it's the only one which know precisely how to configure detector and emitters
                data = [s._dataflow.get()]
            # add the stream name to the image
            for d in data:
                d.metadata[model.MD_DESCRIPTION] = s.name.value
            raw_images.extend(data)

        # record everything to a file
        exporter.export(filename, raw_images, thumbnail)
        logging.info("Snapshot saved as file '%s'.", filename)

    def start_snapshot_animation(self):
        """
        Starts an animation to indicate that a snapshot is taken
        Note: there is no way to stop it
        """
        # if there is already a thread: let it know to restart
        if self._anim_thread and self._anim_thread.is_alive():
            return

        # otherwise start a new animation thread
        self._anim_thread = threading.Thread(target=self.snapshot_animation, name="snapshot animation")
        self._anim_thread.start()

    def snapshot_animation(self, duration=0.6):
        """
        Change the brightness of all the screens to very high, and slowly decrease it back to original value (1.0)
        duration (0<float): duration in second of the animation
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
                # it should decrease quickly at the beginning and slowly at the end => 1/x (x 1/max->1)
                pos = (now - start) / duration
                brightness = 1/(1/brightness_max + (1 - 1/brightness_max) * pos)
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

class AcquisitionDialog(xrcfr_acq):
    """ Wrapper class responsible for additional initialization of the
    Acquisition Dialog created in XRCed
    """

    def __init__(self, parent, interface_model):
        xrcfr_acq.__init__(self, parent)

        self.conf = get_acqui_conf()

        self.cmb_presets.Append(u"high")
        self.cmb_presets.Append(u"medium")
        self.cmb_presets.Append(u"low")

        self.cmb_presets.Select(0)

        self.set_default_filename_and_path()

        # Store current values and pause updates on the current settings
        # controller
        main_settings_controller = wx.GetApp().settings_controller
        main_settings_controller.store()
        main_settings_controller.pause()

        # Create a new settings controller for the acquisition dialog
        self.settings_controller = SettingsBarController(interface_model, self, True)

        main_stream_controller = wx.GetApp().stream_controller
        self.stream_controller = main_stream_controller.duplicate_visible(self.pnl_stream)

        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)

        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_close)
        self.btn_change_file.Bind(wx.EVT_BUTTON, self.on_change_file)
        self.Bind(wx.EVT_CLOSE, self.on_close)

        self.estimate_acquisition_time()

        pub.subscribe(self.on_setting_change, 'setting.changed')

    def on_setting_change(self, setting_ctrl):
        self.estimate_acquisition_time()

    def estimate_acquisition_time(self):

        logging.warn("Estimating acquisition time")

        seconds = 0

        str_panels = self.stream_controller.get_stream_panels()

        if str_panels:
            for str_pan in str_panels:
                seconds += str_pan.get_stream_mod().estimateAcquisitionTime()
            txt = "Estimated acquisition time is %s" % seconds2human(seconds)
        else:
            txt = "No streams present."

        self.lbl_acqestimate.SetLabel(txt)

    def set_default_filename_and_path(self):
        self.txt_filename.SetValue(u"%s%s" % (time.strftime("%Y%m%d-%H%M%S"),
                                              self.conf.last_extension))
        self.txt_destination.SetValue(self.conf.last_path)

    def on_key(self, evt):
        """ Dialog key press handler. """
        if evt.GetKeyCode() == wx.WXK_ESCAPE:
            self.Close()
        else:
            evt.Skip()

    @staticmethod
    def _get_available_formats():
        """
        Find the available file formats
        returns (dict string -> list of strings): name of each format -> list of extensions
        """
        formats = {}
        # Look dynamically which format is available
        for module_name in dataio.__all__:
            try:
                exporter = __import__("odemis.dataio."+module_name, fromlist=[module_name])
            except:  #pylint: disable=W0702
                continue # module cannot be loaded
            formats[exporter.FORMAT] = exporter.EXTENSIONS

        if not formats:
            logging.error("Not file exporter found!")
        return formats

    @staticmethod
    def _convert_formats_to_wildcards(formats2ext):
        """
        Convert formats into wildcards string compatible with wx.FileDialog()
        formats2ext (dict string -> list of strings): name of each format -> list of extensions
        returns (tuple (string, list of strings)): wildcards, name of the format in the same order as in the wildcards
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

        # Note:
        # - Combining multiple filters into one wildcard is not supported
        # - When setting 'defaultFile' when creating the file dialog, the
        #   first filter will automatically be added to the name. Since it
        #   cannot be changed by selecting a different file type, this is big
        #   nono. Also, extensions with multiple periods ('.') are not correctly
        #   handled. The solution is to use the SetFilename method instead.
        formats2extensions = self._get_available_formats()
        wildcards, formats = self._convert_formats_to_wildcards(formats2extensions)
        dialog = wx.FileDialog(self,
                            message="Choose a filename and destination",
                            defaultDir=self.conf.last_path,
                            defaultFile="",
                            style=wx.FD_SAVE|wx.FD_OVERWRITE_PROMPT,
                            wildcard=wildcards)

        # Get and select the last extension used.
        prev_fmt = self.conf.last_format
        try:
            idx = formats.index(self.conf.last_format)
        except ValueError:
            idx = 0
        dialog.SetFilterIndex(idx)

        # Strip the extension, so that if the user changes the file format,
        # it will not have 2 extensions in a row.
        fn = self.txt_filename.GetValue()
        if fn.endswith(self.conf.last_extension):
            fn = fn[:-len(self.conf.last_extension)]
        dialog.SetFilename(fn)

        # Show the dialog and check whether is was accepted or cancelled
        if dialog.ShowModal() != wx.ID_OK:
            return
        
        # New location and name have been selected...
        # Store the path
        dest_dir = dialog.GetDirectory()
        self.txt_destination.SetValue(dest_dir)
        self.conf.last_path = dest_dir
        
        # Store the format
        fmt = formats[dialog.GetFilterIndex()]
        self.conf.last_format = fmt
        
        # Check the filename has a good extension, or add the default one
        fn = dialog.GetFilename()
        ext = None
        for e in formats2extensions[fmt]:
            if fn.endswith(e) and len(e) > len(ext or ""):
                ext = e

        if ext is None:
            if fmt == prev_fmt and self.conf.last_extension in formats2extensions[fmt]:
                # if the format is the same (and extension is compatible): keep
                # the extension. This avoid changing the extension if it's not
                # the default one.
                ext = self.conf.last_extension
            else:
                ext = formats2extensions[fmt][0] # default extension
            fn += ext

        self.conf.last_extension = ext
        
        # save the filename
        self.txt_filename.SetValue(unicode(fn))

        self.conf.write()

    def on_close(self, evt):
        """ Close event handler that executes various cleanup actions
        """
        logging.warn("Canceling acquisition")

        # Restore current values
        main_settings_controller = wx.GetApp().settings_controller
        main_settings_controller.resume()
        main_settings_controller.restore()

        self.Destroy()

