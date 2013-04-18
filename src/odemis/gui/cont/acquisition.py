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

TODO: Go over the methods in both SecomAcquiController and the AcquisitionDialog
and see what method belongs where.

"""

from concurrent.futures._base import CancelledError
from odemis import model, dataio
from odemis.gui import acqmng, conf
from odemis.gui.cont import get_main_tab_controller
from odemis.gui.model import stream
from odemis.gui.model.stream import UNDEFINED_ROI
from odemis.gui.util import img, get_picture_folder, call_after, units
from odemis.gui.win.acquisition import preset_as_is, AcquisitionDialog
from wx.lib.pubsub import pub
import logging
import math
import os
import re
import subprocess
import sys
import threading
import time
import wx



class AcquisitionController(object):
    """ controller to handle snapshot and high-res image acquisition in a
    "global" context. In particular, it needs to be aware of which viewport
    is currently focused, and block any change of settings during acquisition.
    It relies on the acquisition manager to actually do the acquisition.
    """
    def __init__(self, micgui, main_frame):
        """
        micgui (MicroscopeModel): the representation of the microscope GUI
        main_frame: (wx.Frame): the frame which contains the 4 viewports
        """
        # TODO: get tab_controller from arguments or setting and stream controller
        self._microscope = micgui
        self._main_frame = main_frame
        self._anim_thread = None # for snapshot animation

        # nice default paths
        # Snapshots: always the "Pictures" user folder
        self._snapshot_folder = get_picture_folder()

        # For snapshot animation find the names of the active (=connected) screens
        # it's slow, so do it only at init (=expect not to change screen during
        # acquisition)
        self._outputs = self.get_display_outputs()

    # TODO: function never used => delete?
    def onTakeScreenShot(self):
        """ Takes a screenshot of the screen at give pos & size (rect). """
        logging.debug('Starting screenshot')
        rect = self._main_frame.GetRect()
        # http://aspn.activestate.com/ASPN/Mail/Message/wxpython-users/3575899
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

    def start_snapshot_viewport(self, event):
        """Wrapper to run snapshot_viewport in a separate thread."""
        thread = threading.Thread(target=self.snapshot_viewport)
        thread.start()

    def snapshot_viewport(self):
        """ Save a snapshot of the raw image from the focused view to the
        filesystem.
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
            msg = "File '%s' for snapshot already exists, cancelling snapshot"
            logging.warning(msg,
                            filename)
            return

        # get currently focused view
        view = self._microscope.focussedView.value
        if not view:
            logging.warning("Failed to take snapshot, no view is selected")
            return

        streams = view.getStreams()
        if len(streams) == 0:
            msg = "Failed to take snapshot, no stream visible in view %s"
            logging.warning(msg, view.name.value)
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
            if len(data) == 0:
                msg = ("Failed to get the last raw image of stream %s, will "
                       "acquire a new one")
                logging.warning(msg, s.name.value)
                # FIXME: ask the stream to get activated and return an image
                # it's the only one which know precisely how to configure
                # detector and emitters
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


class SecomAcquiController(AcquisitionController):
    """ controller to handle snapshot and high-res image acquisition in a
    "global" context. In particular, it needs to be aware of which viewport
    is currently focused, and block any change of settings during acquisition.
    """

    def __init__(self, micgui, main_frame):
        """
        micgui (MicroscopeModel): the representation of the microscope GUI
        main_frame: (wx.Frame): the frame which contains the 4 viewports
        """
        AcquisitionController.__init__(self, micgui, main_frame)

        # Event binding

        # TODO: only if the current tab is live view?
        # Link snapshot menu to snapshot action
        wx.EVT_MENU(self._main_frame,
            self._main_frame.menu_item_qacquire.GetId(),
            self.start_snapshot_viewport)

        # Listen to "acquire image" button
        self._main_frame.btn_secom_acquire.Bind( wx.EVT_BUTTON, self.on_acquire)

        pub.subscribe(self.on_stream_changed, 'stream.ctrl')

    def on_stream_changed(self, streams_present, streams_visible):
        """ Handler for pubsub 'stream.changed' messages """
        self._main_frame.btn_secom_acquire.Enable(streams_present and streams_visible)

    def on_acquire(self, evt):
        self.open_acquisition_dialog()
        
    def open_acquisition_dialog(self):
        mtc = get_main_tab_controller()

        # save the original settings
        main_settings_controller = mtc['secom_live'].settings_controller
        orig_settings = preset_as_is(main_settings_controller.entries)
        main_settings_controller.pause()
        # TODO: also pause the MicroscopeViews

        # pause all the live acquisitions
        main_stream_controller = mtc['secom_live'].stream_controller
        paused_streams = main_stream_controller.pauseStreams()

        # create the dialog
        acq_dialog = AcquisitionDialog(self._main_frame, self._microscope)
        parent_size = [v * 0.66 for v in self._main_frame.GetSize()]

        try:
            acq_dialog.SetSize(parent_size)
            acq_dialog.Center()
            acq_dialog.ShowModal()
        finally:
            main_stream_controller.resumeStreams(paused_streams)

            for se, value in orig_settings.items():
                se.va.value = value
            main_settings_controller.resume()

            # Make sure that the acquisition button is enabled again.
            self._main_frame.btn_secom_acquire.Enable()


class SparcAcquiController(AcquisitionController):
    """ Acquisition controller for the Sparc platform
    """

    def __init__(self, main_frame, micgui):
        """
        micgui (MicroscopeModel): the representation of the microscope GUI
        main_frame: (wx.Frame): the frame which contains the 4 viewports
        """
        AcquisitionController.__init__(self, micgui, main_frame)

        # Event binding


        # FIXME: this should not listen to on selection changed, but on_stream_changed
        # and then check if all the activated
        pub.subscribe(self.on_selection_changed, 'sparc.acq.selection.changed')


        # For file selection
        # FIXME: we need a file selection gui
        self.conf = conf.get_acqui_conf()

        # FIXME: this should be the date at which the user presses the acquire
        # button (or when the last settings were changed)!
        # At least, we must ensure it's a new date after the acquisition
        # is done.
        # Filename to save the acquisition 
        default_fn = os.path.join(self.conf.last_path, 
                                  u"%s%s" % (time.strftime("%Y%m%d-%H%M%S"),
                                             self.conf.last_extension)
                                  )
        self.filename = model.StringVA(default_fn)
        self.filename.subscribe(self._onFilename, init=True)

        # For acquisition
        # a ProgressiveFuture if the acquisition is going on
        self.btn_acquire = self._main_frame.btn_sparc_acquire
        self.acq_future = None
        self.gauge_acq = self._main_frame.gauge_sparc_acq
        self.lbl_acqestimate = self._main_frame.lbl_sparc_acq_estimate

        # TODO: only if the current tab is acquisition?
        # Link snapshot menu to snapshot action
        # Note: On the Sparc, there is only one view, the SEM (complete ROI). So
        # a snapshot will always take this, which is actually the most expectable
        # from the user's point of view.
        wx.EVT_MENU(self._main_frame,
            self._main_frame.menu_item_qacquire.GetId(),
            self.start_snapshot_viewport)

        # Link buttons
        self.btn_acquire.Bind(wx.EVT_BUTTON, self.on_acquisition)
        self.btn_change_file.Bind(wx.EVT_BUTTON, self.on_change_file)
        
        # TODO: we need to be informed if the user closes suddenly the window
        # self.Bind(wx.EVT_CLOSE, self.on_close)
        
        
        # look for the SEM CL stream 
        self._sem_cl = None # SEM CL stream
        for s in self._microscope.acquisitionView.getStreams():
            if s.name.value == "SEM CL":
                self._sem_cl = s
                break
        else:
            raise KeyError("Failed to find SEM CL stream, required for the Sparc acquisition")
        
    def on_selection_changed(self, region_of_interest):
        #FIXME
        pass

    def _onFilename(self, name):
        """ updates the GUI when the filename is updated """
        # decompose into path/file
        path, base = os.path.split(name)
        self._main_frame.txt_destination.SetValue(unicode(path))
        # show the end of the path (usually more important)
        self._main_frame.txt_destination.SetInsertionPointEnd()
        self._main_frame.txt_filename.SetValue(unicode(base))
    
    # TODO: delete?
    def on_stream_changed(self, streams_present, streams_visible):
        """ Handler for pubsub 'stream.changed' messages """
        self.btn_acquire.Enable(streams_present and streams_visible)
        
        self.btn_acquire.Enable(self._sem_cl.roi.value != UNDEFINED_ROI)
        self.update_acquisition_time()
        

    def update_acquisition_time(self):
        
        if self._sem_cl.roi.value == UNDEFINED_ROI:
            # TODO: update the default text to be the same
            txt = "Region of acquisition needs to be selected"
        else:
            st = self._microscope.acquisitionView.stream_tree
            acq_time = acqmng.estimateAcquistionTime(st)
            self.gauge_acq.Range = 100 * acq_time
            acq_time = math.ceil(acq_time) # round a bit pessimistically
            txt = "The estimated acquisition time is {}."
            txt = txt.format(units.readable_time(acq_time))

        self.lbl_acqestimate.SetLabel(txt)

    def _getStreamTree(self):
        """
        create a StreamTree for the current streams to acquire
        returns StreamTree: a StreamTree with at least one stream (the SEM)
        """
        streams = list(self._microscope.streams)
        if not streams:
            # normally, there should be at least 3 streams: 1 for the whole SEM
            # area, 1 for the ROI SEM, and 1 or 2 for the CCDs.
            logging.error("Unexpected empty stream list for the microscope")

        # put the ROI SEM stream as the first and only stream non-transparent,
        # to get a nice thumbnail.
        # FIXME: have a special .acqusitionView which contains all the streams
        # as they should be acquired
        return self._microscope.acquisitionView.stream_tree

    def on_acquisition(self, evt):
        mtc = get_main_tab_controller()

        # save the original settings
        main_settings_controller = mtc['sparc_acqui'].settings_controller
        orig_settings = preset_as_is(main_settings_controller.entries)
        main_settings_controller.pause()
        # TODO: also pause the MicroscopeView

        # pause all the live acquisitions
        main_stream_controller = mtc['sparc_acqui'].stream_controller
        paused_streams = main_stream_controller.pauseStreams()

        self.btn_acquire.Disable()

        # run the acquisition
        try:
            self.run_acquisition()
        finally:
            main_stream_controller.resumeStreams(paused_streams)

            for se, value in orig_settings.items():
                se.va.value = value
            main_settings_controller.resume()

            # Make sure that the acquisition button is enabled again.
            self._main_frame.btn_sparc_acquire.Enable()

    def run_acquisition(self):
        """
        Start the acquisition (really)
        Similar to win.acquisition.on_acquire()
        """
        # TODO: create a StreamTree with each stream to acquire, but the full
        # SEM is first and only visible one (for the thumbnail).
        streams = self._microscope.streams
        st = stream.StreamTree(streams) # FIXME streams is a set
        # It should never be possible to reach here with an empty streamTree

        # start acquisition + connect events to callback
        self.acq_future = acqmng.startAcquisition(st)
        self.acq_future.add_update_callback(self.on_acquisition_upd)
        self.acq_future.add_done_callback(self.on_acquisition_done)

        # TODO: cancel button
        # self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_cancel)

        # the range of the progress bar was already set in
        # estimate_acquisition_time()
        self.gauge_acq.Value = 0
        self.gauge_acq.Show()
        # FIXME: probably not the whole window is required, just the file settings
        self._main_frame.Layout() # to put the gauge at the right place

        # FIXME: catch "close the window" event and cancel acquisition before
        # fully closing.

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
        # TODO: hide the "cancel" button
#         self.btn_cancel.SetLabel("Close")

        try:
            data, thumb = future.result(1) # timeout is just for safety
            # make sure the progress bar is at 100%
            self.gauge_acq.Value = self.gauge_acq.Range
        except CancelledError:
            # put back to original state:
            # re-enable the acquire button
            self.btn_acquire.Enable()

            # hide progress bar (+ put pack estimated time)
            self._update_estimated_time()
            self.gauge_acq.Hide()
            self._main_frame.Layout()
            return
        except Exception:
            # We cannot do much: just warn the user and pretend it was cancelled
            logging.exception("Acquisition failed")
            self.btn_acquire.Enable()
            self.lbl_acqestimate.SetLabel("Acquisition failed.")
            # leave the gauge, to give a hint on what went wrong.
            return

        # save result to file
        try:
            filename = os.path.join(self.txt_destination.Value,
                                    self.txt_filename.Value)
            exporter = dataio.get_exporter(self.conf.last_format)
            exporter.export(filename, data, thumb)
            logging.info("Acquisition saved as file '%s'.", filename)
        except Exception:
            logging.exception("Saving acquisition failed")
            self.btn_acquire.Enable()
            self.lbl_acqestimate.SetLabel("Saving acquisition file failed.")
            return

        self.lbl_acqestimate.SetLabel("Acquisition completed.")


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

