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
import os
import subprocess
import sys
import time
import wx

# controller to handle snapshot and high-res image acquisition in a "global"
# context. In particular, it needs to be aware of which viewport is currently
# focused, and block any change of settings during acquisition.

class AcquisitionController(object):
    def __init__(self, main_frame):
        """
        main_frame (wx.Frame): the main GUI frame, which we will control
        """
        self._frame = main_frame
        
        # nice default paths
        # Snapshots: always the "Pictures" user folder
        self._snapshot_folder = AcquisitionController.get_picture_folder()
        # High-res: last folder selected, and default to same as snapshot
        self._acquisition_folder = self._snapshot_folder
        
        # Link snapshot menu to snapshot action
        wx.EVT_MENU(self._frame,
            self._frame.menu_item_qacquire.GetId(),
            self.snapshot_viewport)
        
        # Link "acquire image" button to image acquisition
        # TODO: for now it's just snapshot, but should be linked to the acquisition window
        self._frame.btn_aquire.Bind(wx.EVT_BUTTON, self.snapshot_viewport)
        
        
    def snapshot_viewport(self, event):
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
        
        # TODO
        # find the current focused viewport
        scope_panels = [self._frame.pnl_view_tl,
                        self._frame.pnl_view_tr,
                        self._frame.pnl_view_bl,
                        self._frame.pnl_view_br]
        panel = scope_panels[0] # default to first one
        for p in scope_panels:
            if panel.HasFocus():
                panel = p
                break
        
        # get actual viewport image, for thumbnail/preview 
        
        # for each stream seen in the viewport
            # get the raw data
            # add metadata on the way the stream is displayed 
        
        # FIXME: this is a very simplified version until we introduce full support for streams
        df = panel.secom_model.camera.data
        data = df.get() # TODO we should reuse last image, instead of getting a new one
             
        # record everything to a file
        exporter.export(data, filename)
        log.info("Snapshot saved as file '%s'.", filename)
    
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
            logging.warning("Platform not supported for picture folder")
        
        
        # fall-back to HOME
        folder = os.path.expanduser("~")
        if os.path.isdir(folder):
            return folder
        
        # last resort: current working directory should always be existing
        return os.getcwd()
        