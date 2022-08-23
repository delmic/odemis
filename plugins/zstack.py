# -*- coding: utf-8 -*-
'''
Created on 20 July 2018

@author: Anders Muskens

Gives ability to acquire a set of streams multiple times over time.

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
'''

from collections import OrderedDict
from concurrent.futures import CancelledError
import copy
import logging
import math
import numpy
from odemis import model, dataio
from odemis.acq import stream, acqmng
from odemis.acq.stream import MonochromatorSettingsStream, ARStream, \
    SpectrumStream, UNDEFINED_ROI, StaticStream
from odemis.dataio import get_available_formats
import odemis.gui
from odemis.gui.conf import get_acqui_conf
from odemis.gui.plugin import Plugin, AcquisitionDialog
from odemis.gui.util import formats_to_wildcards
from odemis.model import DataArray
from odemis.util import driver
import os
import time
import wx


class ZStackPlugin(Plugin):
    name = "Z Stack"
    __version__ = "1.4"
    __author__ = u"Anders Muskens, Éric Piel"
    __license__ = "GPLv2"

    # Describe how the values should be displayed
    # See odemis.gui.conf.data for all the possibilities
    vaconf = OrderedDict((
        ("filename", {
            "control_type": odemis.gui.CONTROL_SAVE_FILE,
            "wildcard": formats_to_wildcards(get_available_formats(os.O_WRONLY))[0],
        }),
        ("zstep", {
            "control_type": odemis.gui.CONTROL_FLT,
        }),
        ("zstart", {
            "control_type": odemis.gui.CONTROL_FLT,
        }),
        ("numberOfAcquisitions", {
            "control_type": odemis.gui.CONTROL_INT,  # no slider
        }),
        ("expectedDuration", {}),
    ))

    def __init__(self, microscope, main_app):
        super(ZStackPlugin, self).__init__(microscope, main_app)
        # Can only be used with a microscope
        main_data = self.main_app.main_data
        
        if not microscope or main_data.focus is None:
            return

        self.focus = main_data.focus
        self._zrange = self.focus.axes['z'].range
        zunit = self.focus.axes['z'].unit
        self._old_pos = self.focus.position.value
        z = max(self._zrange[0], min(self._old_pos['z'], self._zrange[1]))
        self.zstart = model.FloatContinuous(z, range=self._zrange, unit=zunit)
        self.zstep = model.FloatContinuous(1e-6, range=(-1e-5, 1e-5), unit=zunit, setter=self._setZStep)
        self.numberOfAcquisitions = model.IntContinuous(3, (2, 999), setter=self._setNumberOfAcquisitions)

        self.filename = model.StringVA("a.h5")
        self.expectedDuration = model.VigilantAttribute(1, unit="s", readonly=True)

        self.zstep.subscribe(self._update_exp_dur)
        self.numberOfAcquisitions.subscribe(self._update_exp_dur)
        
        # Two acquisition order possible:
        # * for each Z, all the streams (aka intertwined): Z exactly the same for each stream
        # * for each stream, the whole Z stack: Might be faster (if filter wheel used for changing wavelength)
        self._streams_intertwined = True
        if main_data.light_filter and len(main_data.light_filter.axes["band"].choices) > 1:
            logging.info("Filter-wheel detected, Z-stack will be acquired stream-per-stream")
            self._streams_intertwined = False

        self._acq_streams = None  # previously folded streams, for optimisation
        self._dlg = None
        self.addMenu("Acquisition/ZStack...\tCtrl+B", self.start)
        
    def _acqRangeIsValid(self, acq_range):
        return self._zrange[0] <= acq_range <= self._zrange[1]

    def _setZStep(self, zstep):
        # Check if the acquisition will be within the range of the actuator
        acq_range = self.zstart.value + zstep * self.numberOfAcquisitions.value
        if self._acqRangeIsValid(acq_range):
            return zstep
        else:
            return self.zstep.value  # Old value
        
    def _setNumberOfAcquisitions(self, n_acq):
        # Check if the acquisition will be within the range of the actuator
        acq_range = self.zstart.value + self.zstep.value * n_acq
        if self._acqRangeIsValid(acq_range):
            return n_acq
        else:
            return self.numberOfAcquisitions.value  # Old value

    def _get_new_filename(self):
        conf = get_acqui_conf()
        return os.path.join(
            conf.last_path,
            u"%s%s" % (time.strftime("%Y%m%d-%H%M%S"), conf.last_extension)
        )

    def _estimate_step_duration(self):
        """
        return (float > 0): estimated time (in s) that it takes to move the focus
          by one step.
        """
        speed = None
        if model.hasVA(self.focus, "speed"):
            speed = self.focus.speed.value.get('z', None)
        if speed is None:
            speed = 10e-6  # m/s, pessimistic

        return driver.estimateMoveDuration(abs(self.zstep.value), speed, 0.01)

    def _update_exp_dur(self, _=None):
        """
        Called when VA that affects the expected duration is changed
        """
        nsteps = self.numberOfAcquisitions.value
        step_time = self._estimate_step_duration()
        ss = self._get_acq_streams()

        sacqt = acqmng.estimateTime(ss)
        if self._streams_intertwined:
            # Moving the focus will have to be done for every stream
            dur = sacqt * nsteps + step_time * (nsteps - 1) * len(ss)
        else:
            dur = sacqt * nsteps + step_time * (nsteps - 1)

        logging.debug("Estimating acquisition of %d streams will take %g s", len(ss), dur)

        # Use _set_value as it's read only
        self.expectedDuration._set_value(math.ceil(dur), force_write=True)

    def _get_live_streams(self, tab_data):
        """
        Return all the live streams present in the given tab
        """
        ss = list(tab_data.streams.value)

        # On the SPARC, there is a Spot stream, which we don't need for live
        if hasattr(tab_data, "spotStream"):
            try:
                ss.remove(tab_data.spotStream)
            except ValueError:
                pass  # spotStream was not there anyway

        for s in ss:
            if isinstance(s, StaticStream):
                ss.remove(s)
        return ss

    def _get_acq_streams(self):
        """
        Return the streams that should be used for acquisition
        return:
           acq_st (list of streams): the streams to be acquired at every repetition
        """
        if not self._dlg:
            return []

        live_st = (self._dlg.view.getStreams() +
                   self._dlg.hidden_view.getStreams())
        logging.debug("View has %d streams", len(live_st))

        # On the SPARC, the acquisition streams are not the same as the live
        # streams. On the SECOM/DELPHI, they are the same (for now)
        tab_data = self.main_app.main_data.tab.value.tab_data_model
        if hasattr(tab_data, "acquisitionStreams"):
            acq_st = tab_data.acquisitionStreams
            # Discard the acquisition streams which are not visible
            ss = []
            for acs in acq_st:
                if isinstance(acs, stream.MultipleDetectorStream):
                    if any(subs in live_st for subs in acs.streams):
                        ss.append(acs)
                        break
                elif acs in live_st:
                    ss.append(acs)
        else:
            # No special acquisition streams
            ss = live_st

        self._acq_streams = acqmng.foldStreams(ss, self._acq_streams)
        return self._acq_streams

    def _on_focus_pos(self, pos):
        # Do not listen to zstart when we change it, to make sure there is no loop
        self.zstart.unsubscribe(self._on_zstart)
        self.zstart.value = pos["z"]
        self.zstart.subscribe(self._on_zstart)

    def _on_zstart(self, zpos):
        self.focus.moveAbs({"z": zpos})
        # Don't wait for it to finish moving, eventually it will update the
        # focus position... and will set the zstart value

    def start(self):
        # Fail if the live tab is not selected
        tab = self.main_app.main_data.tab.value
        if tab.name not in ("secom_live", "sparc_acqui", "cryosecom-localization"):
            box = wx.MessageDialog(self.main_app.main_frame,
                       "ZStack acquisition must be done from the acquisition stream.",
                       "ZStack acquisition not possible", wx.OK | wx.ICON_STOP)
            box.ShowModal()
            box.Destroy()
            return

        # On SPARC, fail if no ROI selected
        if hasattr(tab.tab_data_model, "roa") and tab.tab_data_model.roa.value == UNDEFINED_ROI:
            box = wx.MessageDialog(self.main_app.main_frame,
                       "You need to select a region of acquisition.",
                       "Z stack acquisition not possible", wx.OK | wx.ICON_STOP)
            box.ShowModal()
            box.Destroy()
            return

        # Stop the stream(s) playing to not interfere with the acquisition
        tab.streambar_controller.pauseStreams()

        self.filename.value = self._get_new_filename()
        dlg = AcquisitionDialog(self, "Z Stack acquisition",
                                "The same streams will be acquired multiple times at different Z positions, defined starting from Z start, with a step size.\n")
        self._dlg = dlg
        dlg.addSettings(self, self.vaconf)
        ss = self._get_live_streams(tab.tab_data_model)
        for s in ss:
            if isinstance(s, (ARStream, SpectrumStream, MonochromatorSettingsStream)):
                # TODO: instead of hard-coding the list, a way to detect the type
                # of live image?
                logging.info("Not showing stream %s, for which the live image is not spatial", s)
                dlg.addStream(s, index=None)
            else:
                dlg.addStream(s)
        dlg.addButton("Cancel")
        dlg.addButton("Acquire", self.acquire, face_colour='blue')

        # Connect zstart with the actual focus position
        self.zstart.subscribe(self._on_zstart)
        self.focus.position.subscribe(self._on_focus_pos, init=True)

        # Update acq time when streams are added/removed
        dlg.view.stream_tree.flat.subscribe(self._update_exp_dur, init=True)
        dlg.hidden_view.stream_tree.flat.subscribe(self._update_exp_dur, init=True)
        # TODO: update the acquisition time whenever a setting changes

        # TODO: disable "acquire" button if no stream selected

        # TODO: also display the repetition and axis settings for the SPARC streams.

        ans = dlg.ShowModal()

        if ans == 0:
            logging.info("Acquisition cancelled")
        elif ans == 1:
            logging.info("Acquisition completed")
        else:
            logging.warning("Got unknown return code %s", ans)

        self.focus.position.unsubscribe(self._on_focus_pos)
        self.zstart.unsubscribe(self._on_zstart)

        # Don't hold references
        self._acq_streams = None
        if dlg:  # If dlg hasn't been destroyed yet
            dlg.Destroy()

    def constructCube(self, images):
        # images is a list of 3 dim data arrays.
        ret = []
        for image in images:
            stack = numpy.dstack(image)
            stack = numpy.swapaxes(stack, 1, 2)
            ret.append(stack[0])

        # Add back metadata
        metadata3d = copy.copy(images[0].metadata)
        # Extend pixel size to 3D
        ps_x, ps_y = metadata3d[model.MD_PIXEL_SIZE]
        ps_z = self.zstep.value

        # Computer cube centre
        c_x, c_y = metadata3d[model.MD_POS]
        c_z = self.zstart.value + (self.zstep.value * self.numberOfAcquisitions.value) / 2
        metadata3d[model.MD_POS] = (c_x, c_y, c_z)

        # For a negative pixel size, convert to a positive and flip the z axis
        if ps_z < 0:
            ret = numpy.flipud(ret)
            ps_z = -ps_z

        metadata3d[model.MD_PIXEL_SIZE] = (ps_x, ps_y, abs(ps_z))
        metadata3d[model.MD_DIMS] = "ZYX"

        ret = DataArray(ret, metadata3d)

        return ret

    """
    The acquire function API is generic.
    Special functionality is added in the functions
    """

    def initAcquisition(self):
        """
        Called before acquisition begins.
        Returns: (float) estimate of time per step
        """
        logging.info("Z stack acquisition started with %d levels", self.numberOfAcquisitions.value)

        # Move the focus to the start z position
        logging.debug("Preparing Z Stack acquisition. Moving focus to start position")
        self._old_pos = self.focus.position.value
        self.focus.moveAbs({'z': self.zstart.value}).result()
        self.focus.position.unsubscribe(self._on_focus_pos)  # to not update zstart when going through the steps
        self.zstart.unsubscribe(self._on_zstart)
        return self._estimate_step_duration()

    def preStepAcquisition(self, i):
        """
        Called before the ith step of the acquisition
        i (0 <= int): the step number
        """
        self.focus.moveAbs({'z': self.zstart.value + self.zstep.value * i}).result()

    def postStepAcquisition(self, i, images):
        """
        Called after the ith step of the acquisition
        i (0 <= int): the step number
        images []: A list of images as DataArrays
        """
        # Nothing to do after a focus step
        pass

    def completeAcquisition(self, completed):
        """
        Run actions that clean up after the acquisition occurs.
        completed (bool): True if completed without trouble
        """
        # Mvoe back to start
        if completed:
            logging.info("Z Stack acquisition complete.")
        logging.debug("Returning focus to original position %s", self._old_pos)
        self.focus.moveAbs(self._old_pos).result()
        self.focus.position.subscribe(self._on_focus_pos)
        self.zstart.subscribe(self._on_zstart)
        
    def postProcessing(self, images):
        """
        Post-process the images after the acquisition is done.
        images []: list of list of DataArrays (2D): first dim is the different streams,
        the second dimension is the different acquisition number.
        Returns: [list] list of a list of images that have been processed
        """
        cubes = [self.constructCube(ims) for ims in images]
        return cubes

    def acquire(self, dlg):
        """
        Acquisition operation.
        """
        main_data = self.main_app.main_data
        str_ctrl = main_data.tab.value.streambar_controller
        stream_paused = str_ctrl.pauseStreams()
        dlg.pauseSettings()

        nb = self.numberOfAcquisitions.value
        ss = self._get_acq_streams()
        sacqt = acqmng.estimateTime(ss)
        logging.debug("Acquisition streams: %s", ss)

        # all_ss is a list of list of streams to acquire. In theory, we could do
        # several set of acquisitions with each a set of streams. However, that's
        # not how it's used. It's just a generic way to handle both cases:
        # either each acquisition has only one stream, or there is a single
        # acquisition to do all the stream.
        if self._streams_intertwined:
            # Streams are fastest changed: for each step, all streams are acquired
            all_ss = [ss]
        else:
            # Streams are slowest changed: for each stream, do all steps together
            all_ss = [[s] for s in ss]
        
        try:
            # list of list of list of DataArray: for each acquisition, for each stream, for each step, the data acquired
            all_images = [[] for _ in all_ss]
            completed = False

            step_time = self.initAcquisition()
            # TODO: if drift correction, use it over all the time
            f = model.ProgressiveFuture()
            f.task_canceller = lambda l: True  # To allow cancelling while it's running
            f.set_running_or_notify_cancel()  # Indicate the work is starting now
            dlg.showProgress(f)

            total_nb = left = len(all_ss) * nb
            
            logging.debug("Will repeat the acquisition %d times", len(all_ss))
            for ss, images in zip(all_ss, all_images):
                for i in range(nb):
                    dur = sacqt * left + step_time * (left - 1)
                    logging.debug("Acquisition %d of %d", total_nb - left, total_nb)

                    startt = time.time()
                    f.set_progress(end=startt + dur)

                    # Prepare the axis for this step
                    self.preStepAcquisition(i)
                    das, e = acqmng.acquire(ss, self.main_app.main_data.settings_obs).result()
                    if e:
                        logging.warning("Will continue, although acquisition %d partially failed: %s", e)
                    if len(das) != len(ss):
                        logging.warning("Expected %d DataArrays, but got %d", len(ss), len(das))

                    if not images:
                        images[:] = [[] for _ in das]

                    for im, da in zip(images, das):
                        im.append(da)

                    if f.cancelled():
                        raise CancelledError()

                    # Clean-up or adjust the images
                    self.postStepAcquisition(i, images)
                    left -= 1

            # Collate back all the data as "for each stream, all the images acquired"
            images = []
            for ii in all_images:
                images.extend(ii)
            # Construct a cube from each stream's image.
            images = self.postProcessing(images)

            # Export image
            exporter = dataio.find_fittest_converter(self.filename.value)
            exporter.export(self.filename.value, images)

            f.set_result(None)  # Indicate it's over
            completed = True
            dlg.Close()
            
        except CancelledError:
            logging.debug("Acquisition cancelled.")
            dlg.resumeSettings()

        except Exception as e:
            logging.exception(e)

        finally:
            # Do completion actions
            self.completeAcquisition(completed)
