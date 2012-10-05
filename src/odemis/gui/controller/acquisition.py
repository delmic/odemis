# -*- coding: utf-8 -*-
'''
Created on 22 Aug 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

from odemis.gui.log import log
from odemis.gui.util import img
import os
import re
import subprocess
import sys
import threading
import time
import wx

# controller to handle snapshot and high-res image acquisition in a "global"
# context. In particular, it needs to be aware of which viewport is currently
# focused, and block any change of settings during acquisition.

class AcquisitionController(object):
    def __init__(self, micgui, main_frame):
        """
        micgui (MicroscopeGUI): the representation of the microscope GUI
        main_frame: (wx.Frame): the frame which contains the 4 viewports
        """
        self._microscope = micgui
        self._main_frame = main_frame
        self._anim_thread = None 
        
        # nice default paths
        # Snapshots: always the "Pictures" user folder
        self._snapshot_folder = AcquisitionController.get_picture_folder()
        # High-res: last folder selected, and default to same as snapshot
        self._acquisition_folder = self._snapshot_folder
        
        # Link snapshot menu to snapshot action
        wx.EVT_MENU(self._main_frame,
            self._main_frame.menu_item_qacquire.GetId(),
            self.start_snapshot_viewport)
        
        # Link "acquire image" button to image acquisition
        # TODO: for now it's just snapshot, but should be linked to the acquisition window
        self._main_frame.btn_aquire.Bind(wx.EVT_BUTTON, self.start_snapshot_viewport)
        
        # find the names of the active (=connected) screens
        # it's slow, so do it only at init (=expect not to change screen during acquisition)
        self._outputs = self.get_display_outputs()
        
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

        # filename
        dirname = self._snapshot_folder
        basename = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        extention = exporter.EXTENSIONS[0] # includes the .
        filename = os.path.join(dirname, basename + extention)
        if os.path.exists(filename):
            log.warning("File '%s' for snapshot already exists, cancelling snapshot",
                            filename)
            return
        
        # get currently focused view
        view = self._microscope.currentView.value
        if not view:
            log.warning("Failed to take snapshot, no view is selected")
            return
        
        streams = view.getStreams()
        if len(streams) == 0:
            log.warning("Failed to take snapshot, no stream visible in view %s", view.name.value)
            return

        self.start_snapshot_animation()
        
        # let's try to get a thumbnail
        if view.thumbnail.value is None:
            thumbnail = None
        else:
            # need to convert from wx.Image to ndimage
            thumbnail = img.wxImage2NDImage(view.thumbnail.value, keep_alpha=False)
        
        # for each stream seen in the viewport
        raw_images = []
        for s in streams:
            data = s.raw # list of raw images for this stream (with metadata)
            if len(data) == 0:
                log.warning("Failed to get the last raw image of stream %s, will acquire a new one", s.name.value)
                # FIXME: ask the stream to get activated and return an image
                # it's the only one which know precisely how to configure detector and emitters
                data = [s._dataflow.get()]
            raw_images.extend(data)
        
        # record everything to a file
        exporter.export(filename, raw_images, thumbnail)
        log.info("Snapshot saved as file '%s'.", filename)
    
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
            log.info("Failed to run snapshot animation.")
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
        outputs (set of string): names of graphical output (screen) as xrandr uses them
        brightness (0<=float): brightness
        raises:
            exception in case change of brightness failed
        """
        assert (0 <= brightness)
        log.debug("setting brightness to %f", brightness)
        if not len(outputs):
            return
        # to simplify, we don't use the XRANDR API, but just call xrandr command
        # we need to build a whole line with all the outputs, like:
        # xrandr --output VGA1 --brigthness 2 --output LVDS1 --brigthness 2
        args = ["xrandr"]
        for o in outputs:
            args += ["--output", o, "--brightness", "%f" % brightness]
        
        log.debug("Calling: %s", " ".join(args))
        subprocess.check_call(args)
    
    @staticmethod
    def get_picture_folder():
        """
        return (string): a full path to the "Picture" user folder.
        It tries to always return an existing folder.
        """
        if sys.platform.startswith('linux'):
            # First try to find the XDG picture folder
            folder = None
            try:
                folder = subprocess.check_output(["xdg-user-dir", "PICTURES"])
                folder = folder.strip()
            except subprocess.CalledProcessError:
                # XDG not supported
                pass
            if os.path.isdir(folder):
                return folder
            # drop to default
        elif sys.platform.startswith('win32'):
            # TODO Windows code
            pass
            # drop to default
        else:
            log.warning("Platform not supported for picture folder")
        
        
        # fall-back to HOME
        folder = os.path.expanduser("~")
        if os.path.isdir(folder):
            return folder
        
        # last resort: current working directory should always be existing
        return os.getcwd()
        