# -*- coding: utf-8 -*-
"""
Created on 22 Aug 2012

@author: Éric Piel, Rinze de Laat, Philip Winkler

Copyright © 2012-2022 Éric Piel, Rinze de Laat, Delmic

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

import logging
import os
import re
import subprocess
import threading
import time

import wx

from odemis import model, dataio
from odemis.gui import conf
from odemis.gui.comp import popup
from odemis.gui.util import img, get_picture_folder, call_in_wx_main
from odemis.gui.win.acquisition import ShowAcquisitionFileDialog
from odemis.model import DataArrayShadow


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
        self._anim_thread = None  # for snapshot animation

        # For snapshot animation find the names of the active (=connected)
        # screens it's slow, so do it only at init (=expect not to change screen
        # during acquisition)
        self._outputs = self.get_display_outputs()

        # Link snapshot menu to snapshot action
        self._main_frame.Bind(wx.EVT_MENU, self.start_snapshot_viewport, id=self._main_frame.menu_item_snapshot.GetId())

        self._main_frame.Bind(wx.EVT_MENU, self.start_snapshot_as_viewport,
                              id=self._main_frame.menu_item_snapshot_as.GetId())

        self._prev_streams = None  # To unsubscribe afterwards
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
        brightness_orig = 1.0  # TODO: read the previous brightness

        # start with very bright and slowly decrease to 1.0
        try:
            brightness_max = 10.0
            start = time.time()
            end = start + duration
            self.set_output_brightness(self._outputs, brightness_max)
            time.sleep(0.1)  # first is a bit longer
            now = time.time()
            while now <= end:
                # it should decrease quickly at the beginning and slowly at the
                # end => 1/x (x 1/max->1)
                pos = (now - start) / duration
                brightness = 1 / (1 / brightness_max + (1 - 1 / brightness_max) * pos)
                self.set_output_brightness(self._outputs, brightness)
                time.sleep(0.05)  # ensure not to use too much CPU
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
            try:
                xrandr_out = subprocess.check_output("xrandr")
            except subprocess.CalledProcessError as ex:
                logging.warning("Failed to detect displays: %s", ex)
                return []
            # only pick the "connected" outputs
            ret = re.findall(b"^(\\S+) connected ", xrandr_out, re.MULTILINE)
            return [o.decode("utf-8") for o in ret]
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
