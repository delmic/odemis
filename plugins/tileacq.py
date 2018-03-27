# -*- coding: utf-8 -*-
'''
Created on 22 Mar 2017

@author: Éric Piel

Gives ability to acquire the streams over a large area by separating it into
tiles with some overlap. In other words, it acquires the streams at multiple
stage position organised in a grid fashion.

Copyright © 2017 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
'''

from __future__ import division

from collections import OrderedDict
import logging
import math
from odemis import model, acq, dataio, util
from odemis.acq import stream
import odemis.gui
from odemis.gui.conf import get_acqui_conf
from odemis.gui.plugin import Plugin, AcquisitionDialog
from odemis.util import dataio as udataio
from odemis.util import img, TimeoutError
import os
import time
import wx
from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, \
    RUNNING
import threading

from odemis.acq import stitching
from odemis.acq.stream import Stream, SEMStream, CameraStream, \
    RepetitionStream, StaticStream, UNDEFINED_ROI, EMStream, ARStream, SpectrumStream, \
    FluoStream, MultipleDetectorStream
import numpy
import psutil
from odemis.gui.util import call_in_wx_main


class TileAcqPlugin(Plugin):
    name = "Tile acquisition"
    __version__ = "1.2"
    __author__ = u"Éric Piel, Philip Winkler"
    __license__ = "GPLv2"

    # Describe how the values should be displayed
    # See odemis.gui.conf.data for all the possibilities
    vaconf = OrderedDict((
        ("nx", {
            "label": "Tiles X",
            "control_type": odemis.gui.CONTROL_INT,  # no slider
        }),
        ("ny", {
            "label": "Tiles Y",
            "control_type": odemis.gui.CONTROL_INT,  # no slider
        }),
        ("overlap", {
            "tooltip": "Approximate amount of overlapping area between tiles.",
        }),

        ("filename", {
            "tooltip": "Pattern of each filename",
            "control_type": odemis.gui.CONTROL_SAVE_FILE,
        }),
        ("Stitch", {
            "tooltip": "Use all the tiles to create a large-scale image"
        }),
        ("expectedDuration", {
        }),
        ("totalArea", {
            "tooltip": "Approximate area covered by all the streams"
        }),

    ))

    def __init__(self, microscope, main_app):
        super(TileAcqPlugin, self).__init__(microscope, main_app)

        self._dlg = None
        self.ft = model.InstantaneousFuture()  # acquisition future

        # Can only be used with a microscope
        if not microscope:
            return
        else:
            # Check if microscope supports tiling (= has a sample stage)
            main_data = self.main_app.main_data
            if main_data.stage:
                self.addMenu("Acquisition/Tile...\tCtrl+G", self.show_dlg)

        self.nx = model.IntContinuous(5, (1, 1000))
        self.ny = model.IntContinuous(5, (1, 1000))
        self.overlap = model.FloatContinuous(20, (1, 80), unit="%")
        self.filename = model.StringVA("a.ome.tiff")
        self.expectedDuration = model.VigilantAttribute(1, unit="s", readonly=True)
        self.totalArea = model.TupleVA((1, 1), unit="m", readonly=True)
        self.stitch = model.BooleanVA(True)
        # TODO: manage focus (eg, autofocus or ask to manual focus on the corners
        # of the ROI and linearly interpolate)
        # TODO: on SECOM allow to do fine alignment for each tile

        self.nx.subscribe(self._check_range)
        self.ny.subscribe(self._check_range)
        self.nx.subscribe(self._update_exp_dur)
        self.ny.subscribe(self._update_exp_dur)
        self.nx.subscribe(self._update_total_area)
        self.ny.subscribe(self._update_total_area)
        self.overlap.subscribe(self._update_total_area)

        # Warn if memory will be exhausted
        self.nx.subscribe(self._memory_check)
        self.ny.subscribe(self._memory_check)
        self.stitch.subscribe(self._memory_check)

    def _get_streams(self):
        """
        Returns the streams set as visible in the acquisition dialog
        """
        if not self._dlg:
            return []
        ss = self._dlg.microscope_view.getStreams() + self._dlg.microscope_view_r.getStreams() + \
            self._dlg.microscope_view_spectrum.getStreams()
        logging.debug("View has %d streams", len(ss))
        return ss

    def _get_new_filename(self):
        conf = get_acqui_conf()
        return os.path.join(
            conf.last_path,
            u"%s%s" % (time.strftime("%Y%m%d-%H%M%S"), conf.last_extension)
        )

    def _on_streams_change(self, _=None):
        tab = self.main_app.main_data.tab.value
        ss = self._get_live_streams(tab.tab_data_model)

        # Subscribe to all relevant setting changes
        for s in ss:
            for va in self._get_settings_vas(s):
                va.subscribe(self._update_exp_dur)
                va.subscribe(self._memory_check)

    def _unsubscribe_vas(self):
        tab = self.main_app.main_data.tab.value
        ss = self._get_live_streams(tab.tab_data_model)

        # Unsubscribe from all relevant setting changes
        for s in ss:
            for va in self._get_settings_vas(s):
                va.unsubscribe(self._update_exp_dur)
                va.unsubscribe(self._memory_check)

    def _update_exp_dur(self, _=None):
        """
        Called when VA that affects the expected duration is changed
        """
        tat = self.estimate_time()

        # Typically there are a few more pixels inserted at the beginning of
        # each line for the settle time of the beam. We don't take this into
        # account and so tend to slightly under-estimate.

        # Use _set_value as it's read only
        self.expectedDuration._set_value(math.ceil(tat), force_write=True)

    def _update_total_area(self, _=None):
        """
        Called when VA that affects the total area is changed
        """
        logging.debug("Updating total area")
        # Find the stream with the smallest FoV
        try:
            fov = self._guess_smallest_fov()
        except ValueError as ex:
            logging.debug("Cannot compute total area: %s", ex)
            return

        # * number of tiles - overlap
        nx = self.nx.value
        ny = self.ny.value
        ta = (fov[0] * (nx - (nx - 1) * self.overlap.value / 100),
              fov[1] * (ny - (ny - 1) * self.overlap.value / 100))

        # Use _set_value as it's read only
        self.totalArea._set_value(ta, force_write=True)

    def _check_range(self, _=None):
        """
        Check that stage limit is not exceeded during acquisition of nx * ny tiles. Set
        self.nx and self.ny to its maximum values if input exceeds possible number of tiles. 
        """
        stage = self.main_app.main_data.stage
        orig_pos = stage.position.value
        tile_size = self._guess_smallest_fov()
        overlap = 1 - self.overlap.value / 100
        tile_pos = (orig_pos["x"] + self.nx.value * tile_size[0] * overlap,
                    orig_pos["y"] - self.ny.value * tile_size[1] * overlap)

        # The acquisition region only extends to the right and to the bottom, never
        # to the left of the top of the current position, so it is not required to
        # check the distance to the top and left edges of the stage.
        if hasattr(stage.axes["x"], "range"):
            max_x = stage.axes["x"].range[1]
            if tile_pos[0] > max_x:
                self.nx.value = int((max_x - orig_pos["x"]) / (overlap * tile_size[0]))
                logging.info("Restricting number of tiles in x direction to %i due to stage limit."
                              % self.nx.value)
        if hasattr(stage.axes["y"], "range"):
            min_y = stage.axes["y"].range[0]
            if tile_pos[1] < min_y:
                self.ny.value = int(-(min_y - orig_pos["y"]) / (overlap * tile_size[1]))
                logging.info("Restricting number of tiles in y direction to %i due to stage limit."
                              % self.ny.value)

    def _guess_smallest_fov(self):
        """
        Return (float, float): smallest width and smallest height of all the FoV
          Note: they are not necessarily from the same FoV.
        raise ValueError: If no stream selected
        """
        ss = self._get_live_streams(self.main_app.main_data.tab.value.tab_data_model)
        for s in ss:
            if isinstance(s, StaticStream):
                ss.remove(s)
        fovs = [self._get_fov(s) for s in ss]
        if not fovs:
            raise ValueError("No stream so no FoV, so no minimum one")

        return (min(f[0] for f in fovs),
                min(f[1] for f in fovs))

    def show_dlg(self):
        # TODO: if there is a chamber, only allow if there is vacuum

        # Fail if the live tab is not selected
        tab = self.main_app.main_data.tab.value
        if tab.name not in ("secom_live", "sparc_acqui"):
            box = wx.MessageDialog(self.main_app.main_frame,
                       "Tiled acquisition must be done from the acquisition stream.",
                       "Tiled acquisition not possible", wx.OK | wx.ICON_STOP)
            box.ShowModal()
            box.Destroy()
            return

        tab = self.main_app.main_data.tab.value
        tab.streambar_controller.pauseStreams()

        # If no ROI is selected, select entire area
        try:
            if tab.tab_data_model.semStream.roi.value == UNDEFINED_ROI:
                tab.tab_data_model.semStream.roi.value = (0, 0, 1, 1)
        except AttributeError:
            pass  # Not a SPARC

        # Disable drift correction (on SPARC)
        if hasattr(tab.tab_data_model, "driftCorrector"):
            tab.tab_data_model.driftCorrector.roi.value = UNDEFINED_ROI

        ss = self._get_live_streams(tab.tab_data_model)
        self.filename.value = self._get_new_filename()

        dlg = AcquisitionDialog(self, "Tiled acquisition",
                                "Acquire a large area by acquiring the streams multiple "
                                "times over a grid.")

        self._dlg = dlg
        dlg.addSettings(self, self.vaconf)
        for s in ss:
            # add ARStream and Spectrum stream in different view
            if isinstance(s, ARStream):
                dlg.addStream(s, index=1)
            elif isinstance(s, SpectrumStream):
                dlg.addStream(s, index=2)
            else:
                dlg.addStream(s, index=0)

        dlg.addButton("Cancel")
        dlg.addButton("Acquire", self.acquire, face_colour='blue')

        # Update acq time and area when streams are added/removed. Add stream settings
        # to subscribed vas.
        dlg.microscope_view.stream_tree.flat.subscribe(self._update_exp_dur, init=True)
        dlg.microscope_view.stream_tree.flat.subscribe(self._update_total_area, init=True)
        dlg.microscope_view.stream_tree.flat.subscribe(self._on_streams_change, init=True)

        self._memory_check()

        # TODO: disable "acquire" button if no stream selected.

        ans = dlg.ShowModal()
        if ans == 0 or ans == wx.ID_CANCEL:
            logging.info("Tiled acquisition cancelled")
            self.ft.cancel()
        elif ans == 1:
            logging.info("Tiled acquisition completed")
        else:
            logging.warning("Got unknown return code %s", ans)

        # Don't hold references
        self._unsubscribe_vas()
        self._dlg = None

    # black list of VAs name which are known to not affect the acquisition time
    VAS_NO_ACQUSITION_EFFECT = ("image", "autoBC", "intensityRange", "histogram",
                                "is_active", "should_update", "status", "name", "tint")

    def _get_settings_vas(self, stream):
        """
        Find all the VAs of a stream which can potentially affect the acquisition time
        return (set of VAs)
        """

        nvas = model.getVAs(stream)  # name -> va
        vas = set()
        # remove some VAs known to not affect the acquisition time
        for n, va in nvas.items():
            if n not in self.VAS_NO_ACQUSITION_EFFECT:
                vas.add(va)
        return vas

    def _get_live_streams(self, tab_data):
        """
        Return all the live streams for tiled acquisition present in the given tab
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
        """
        # On the SPARC, the acquisition streams are not the same as the live
        # streams. On the SECOM/DELPHI, they are the same (for now)
        live_st = self._get_streams()
        tab_data = self.main_app.main_data.tab.value.tab_data_model
        if hasattr(tab_data, "acquisitionStreams"):  # Odemis v2.7+
            acq_st = tab_data.acquisitionStreams
        elif hasattr(tab_data, "acquisitionView"):  # Odemis v2.6 and earlier
            acq_st = tab_data.acquisitionView.getStreams()
        else:
            # No special acquisition streams
            return live_st

        # Discard the acquisition streams which are not visible
        ss = []
        for acs in acq_st:
            if isinstance(acs, stream.MultipleDetectorStream):
                if any(subs in live_st for subs in acs.streams):
                    ss.append(acs)
                    break
            elif acs in live_st:
                ss.append(acs)
        return ss

    def _generate_scanning_indices(self, rep):
        """
        Generate the explicit X/Y position of each tile, in the scanning order
        rep (int, int): X, Y number of tiles
        return (generator of tuple(int, int)): x/y positions, starting from 0,0
        """
        # For now we do forward/backward on X (fast), and Y (slowly)
        dir = 1
        for iy in range(rep[1]):
            if dir == 1:
                for ix in range(rep[0]):
                    yield (ix, iy)
            else:
                for ix in range(rep[0] - 1, -1, -1):
                    yield (ix, iy)

            dir *= -1

    def _move_to_tile(self, idx, orig_pos, tile_size, prev_idx):
        # Go left/down, with every second line backward:
        # similar to writing/scanning convention, but move of just one unit
        # every time.
        # A-->-->-->--v
        #             |
        # v--<--<--<---
        # |
        # --->-->-->--Z
        overlap = 1 - self.overlap.value / 100
        # don't move on the axis that is not supposed to have changed
        m = {}
        idx_change = numpy.subtract(idx, prev_idx)
        if idx_change[0]:
            m["x"] = orig_pos["x"] + idx[0] * tile_size[0] * overlap
        if idx_change[1]:
            m["y"] = orig_pos["y"] - idx[1] * tile_size[1] * overlap

        logging.debug("Moving to tile %s at %s m", idx, m)
        f = self.main_app.main_data.stage.moveAbs(m)
        try:
            speed = 10e-6  # m/s. Assume very low speed for timeout.
            t = math.hypot(tile_size[0] * overlap, tile_size[1] * overlap) / speed + 1
            # add 1 to make sure it doesn't time out in case of a very small move
            f.result(t)
        except TimeoutError:
            logging.warning("Failed to move to tile %s", idx)
            self.ft.running_subf.cancel()
            # Continue acquiring anyway... maybe it has moved somewhere near

    def _get_fov(self, sd):
        """
        sd (Stream or DataArray): If it's a stream, it must be a live stream,
          and the FoV will be estimated based on the settings.
        return (float, float): width, height in m
        """
        if isinstance(sd, model.DataArray):
            # The actual FoV, as the data recorded it
            return (sd.shape[0] * sd.metadata[model.MD_PIXEL_SIZE][0],
                    sd.shape[1] * sd.metadata[model.MD_PIXEL_SIZE][1])
        elif isinstance(sd, Stream):
            # Estimate the FoV, based on the emitter/detector settings
            if isinstance(sd, SEMStream):
                ebeam = sd.emitter
                return (ebeam.shape[0] * ebeam.pixelSize.value[0],
                        ebeam.shape[1] * ebeam.pixelSize.value[1])

            elif isinstance(sd, CameraStream):
                ccd = sd.detector
                # Look at what metadata the images will get
                md = ccd.getMetadata().copy()
                img.mergeMetadata(md)  # apply correction info from fine alignment

                shape = ccd.shape[0:2]
                pxs = md[model.MD_PIXEL_SIZE]
                # compensate for binning
                binning = ccd.binning.value
                pxs = [p / b for p, b in zip(pxs, binning)]
                return (shape[0] * pxs[0], shape[1] * pxs[1])

            elif isinstance(sd, RepetitionStream):
                # CL, Spectrum, AR
                ebeam = sd.emitter
                global_fov = (ebeam.shape[0] * ebeam.pixelSize.value[0],
                              ebeam.shape[1] * ebeam.pixelSize.value[1])
                l, t, r, b = sd.roi.value
                fov = abs(r - l) * global_fov[0], abs(b - t) * global_fov[1]
                return fov
            else:
                raise TypeError("Unsupported Stream %s" % (sd,))
        else:
            raise TypeError("Unsupported object")

    def _cancel_acquisition(self, future):
        """
        Canceler of acquisition task.
        """
        logging.debug("Canceling acquisition...")

        with future._task_lock:
            if future._task_state == FINISHED:
                return False
            future._task_state = CANCELLED
            future.running_subf.cancel()
            logging.debug("Acquisition cancelled.")

        return True

    STITCH_SPEED = 1e-8  # s/px
    MOVE_SPEED = 1e3  # s/m

    def estimate_time(self, remaining=None):
        """
        Estimates duration for acquisition and stitching.
        """
        ss = self._get_acq_streams()

        if remaining is None:
            remaining = self.nx.value * self.ny.value
        acqt = acq.estimateTime(ss)

        if self.stitch.value:
            # Estimate stitching time based on number of pixels in the overlapping part
            max_pxs = 0
            for s in ss:
                for sda in s.raw:
                    pxs = sda.shape[0] * sda.shape[1]
                    if pxs > max_pxs:
                        max_pxs = pxs

            stitcht = self.nx.value * self.ny.value * max_pxs * self.overlap.value * self.STITCH_SPEED
        else:
            stitcht = 0

        try:
            movet = max(self._guess_smallest_fov()) * self.MOVE_SPEED * (remaining - 1)
            # current tile is part of remaining, so no need to move there
        except ValueError:  # no current streams
            movet = 0.5

        return acqt * remaining + movet + stitcht

    def sort_das(self, das, ss):
        """
        Sorts das based on priority for stitching, i.e. largest SEM da first, then
        other SEM das, and finally das from other streams.
        das: list of DataArrays
        ss: streams from which the das were extracted

        returns: list of DataArrays, reordered input
        """
        # Add the ACQ_TYPE metadata (in case it's not there)
        # In practice, we check the stream the DA came from, and based on the stream
        # type, fill the metadata
        # TODO: make sure acquisition type is added to data arrays before, so this
        # code can be deleted
        for da in das:
            if model.MD_ACQ_TYPE in da.metadata:
                continue
            for s in ss:
                for sda in s.raw:
                    if da is sda:  # Found it!
                        if isinstance(s, EMStream):
                            da.metadata[model.MD_ACQ_TYPE] = model.MD_AT_EM
                        elif isinstance(s, ARStream):
                            da.metadata[model.MD_ACQ_TYPE] = model.MD_AT_AR
                        elif isinstance(s, SpectrumStream):
                            da.metadata[model.MD_ACQ_TYPE] = model.MD_AT_SPECTRUM
                        elif isinstance(s, FluoStream):
                            da.metadata[model.MD_ACQ_TYPE] = model.MD_AT_FLUO
                        elif isinstance(s, MultipleDetectorStream):
                            if model.MD_OUT_WL in da.metadata:
                                da.metadata[model.MD_ACQ_TYPE] = model.MD_AT_CL
                            else:
                                da.metadata[model.MD_ACQ_TYPE] = model.MD_AT_EM
                        else:
                            logging.warning("Unknown acq stream type for %s", s)
                        break
                if model.MD_ACQ_TYPE in da.metadata:
                    # if da is found, no need to search other streams
                    break
            else:
                logging.warning("Couldn't find the stream for DA of shape %s", da.shape)

        # save tiles for stitching
        if self.stitch.value:
            # Remove the DAs we don't want to (cannot) stitch
            das = [da for da in das if da.metadata[model.MD_ACQ_TYPE] \
                   not in (model.MD_AT_AR, model.MD_AT_SPECTRUM)]

            def leader_quality(da):
                """
                return int: The bigger the more leadership
                """
                # For now, we prefer a lot the EM images, because they are usually the
                # one with the smallest FoV and the most contrast
                if da.metadata[model.MD_ACQ_TYPE] == model.MD_AT_EM:
                    return numpy.prod(da.shape)  # More pixel to find the overlap
                elif da.metadata[model.MD_ACQ_TYPE]:
                    # A lot less likely
                    return numpy.prod(da.shape) / 100

            das.sort(key=leader_quality, reverse=True)
            das = tuple(das)
        return das

    def _check_fov(self, das, sfov):
        """
        Checks the fov based on the data arrays.
        das: list of DataArryas
        sfov: previous estimate for the fov
        """
        afovs = [self._get_fov(d) for d in das]
        asfov = (min(f[1] for f in afovs),
                 min(f[0] for f in afovs))
        if not all(util.almost_equal(e, a) for e, a in zip(sfov, asfov)):
            logging.warning("Unexpected min FoV = %s, instead of %s", asfov, sfov)
            sfov = asfov
        return sfov

    MEMPP = 2e-8  # memory per pixel in GB, found empirically
    @call_in_wx_main
    def _memory_check(self, _=None):
        """
        Makes an estimate for the amount of memory that will be consumed during
        stitching and compares it to the available memory on the computer.
        Displays a warning if memory exceeds available memory.
        """
        if self.stitch.value:
            # Number of pixels for acquisition
            pxs = 0
            for s in self._get_acq_streams():
                if hasattr(s, 'emtResolution'):
                    pxs += s.emtResolution.value[0] * s.emtResolution.value[1]
                elif hasattr(s, 'repetition'):
                    pxs += s.repetition.value[0] * s.repetition.value[1]
                else:
                    logging.warning("Resolution of stream %s cannot be determined." % s)
            pxs = pxs * self.nx.value * self.ny.value

            # Memory calculation
            mem_est = pxs * self.MEMPP
            mem_computer = psutil.virtual_memory().total / 1024 ** 3  # in GB
            # Assume computer is using 2 GB RAM for odemis and other programs
            mem_sufficient = mem_est < mem_computer - 2
        else:
            mem_sufficient = True

        # Display warning
        if mem_sufficient:
            self._dlg.setAcquisitionInfo(None)
        else:
            txt = "Stitching this area requires %.1f GB of memory.\n" % (mem_est) + \
                "Running the acquisition might cause your computer to crash."
            self._dlg.setAcquisitionInfo(txt, lvl=logging.ERROR)

    def acquire(self, dlg):
        main_data = self.main_app.main_data
        str_ctrl = main_data.tab.value.streambar_controller
        stream_paused = str_ctrl.pauseStreams()
        self._unsubscribe_vas()

        orig_pos = main_data.stage.position.value
        trep = (self.nx.value, self.ny.value)
        nb = trep[0] * trep[1]
        # It's not a big deal if it was a bad guess as we'll use the actual data
        # before the first move
        sfov = self._guess_smallest_fov()
        fn = self.filename.value
        exporter = dataio.find_fittest_converter(fn)
        fn_bs, fn_ext = udataio.splitext(fn)

        ss = self._get_acq_streams()
        end = self.estimate_time() + time.time()

        ft = model.ProgressiveFuture(end=end)
        self.ft = ft  # allows future to be canceled in show_dlg after closing window
        ft.running_subf = model.InstantaneousFuture()
        ft._task_state = RUNNING
        ft._task_lock = threading.Lock()
        ft.task_canceller = self._cancel_acquisition  # To allow cancelling while it's running
        ft.set_running_or_notify_cancel()  # Indicate the work is starting now
        dlg.showProgress(ft)

        # For stitching only
        da_list = []  # for each position, a list of DataArrays
        i = 0
        prev_idx = [0, 0]
        try:
            for ix, iy in self._generate_scanning_indices(trep):
                logging.debug("Acquiring tile %dx%d", ix, iy)
                self._move_to_tile((ix, iy), orig_pos, sfov, prev_idx)
                prev_idx = ix, iy
                # Update the progress bar
                ft.set_progress(end=self.estimate_time(nb - i) + time.time())

                ft.running_subf = acq.acquire(ss)
                das, e = ft.running_subf.result()  # blocks until all the acquisitions are finished
                if e:
                    logging.warning("Acquisition for tile %dx%d partially failed: %s",
                                    ix, iy, e)

                if ft._task_state == CANCELLED:
                    logging.debug("Acquisition cancelled")
                    return

                # TODO: do in a separate thread
                fn_tile = "%s-%.5dx%.5d%s" % (fn_bs, ix, iy, fn_ext)
                logging.debug("Will save data of tile %dx%d to %s", ix, iy, fn_tile)
                exporter.export(fn_tile, das)

                if ft._task_state == CANCELLED:
                    logging.debug("Acquisition cancelled")
                    return

                # Sort tiles (largest sem on first position)
                da_list.append(self.sort_das(das, ss))

                # Check the FoV is correct using the data, and if not update
                if i == 0:
                    sfov = self._check_fov(das, sfov)
                i += 1

            # Move stage to original position
            main_data.stage.moveAbs(orig_pos)

            # Stitch SEM and CL streams
            st_data = []
            if self.stitch.value and (not da_list or not da_list[0]):
                # if only AR or Spectrum are acquired
                logging.warning("No stream acquired that can be used for stitching.")
            elif self.stitch.value:
                logging.info("Acquisition completed, now stitching...")
                ft.set_progress(end=self.estimate_time(0) + time.time())

                logging.info("Computing big image out of %d images", len(da_list))
                das_registered = stitching.register(da_list)

                # Weave every stream
                if isinstance(das_registered[0], tuple):
                    for s in range(len(das_registered[0])):
                        streams = []
                        for da in das_registered:
                            streams.append(da[s])
                        da = stitching.weave(streams)
                        da.metadata[model.MD_DIMS] = "YX"  # TODO: do it in the weaver
                        st_data.append(da)
                else:
                    da = stitching.weave(das_registered)
                    st_data.append(da)

                # Save
                exporter = dataio.find_fittest_converter(fn)
                if exporter.CAN_SAVE_PYRAMID:
                    exporter.export(fn, st_data, pyramid=True)
                else:
                    logging.warning("File format doesn't support saving image in pyramidal form")
                    exporter.export(fn, st_data)

            ft.set_result(None)  # Indicate it's over

            # End of the (completed) acquisition
            if not ft._task_state == CANCELLED:
                dlg.Destroy()

            # Open analysis tab
            if st_data:
                tab = main_data.getTabByName('analysis')
                main_data.tab.value = tab
                tab.load_data(fn)

            # TODO: also export a full image (based on reported position, or based
            # on alignment detection)
        except CancelledError:
            logging.debug("Acquisition cancelled")
        except Exception, e:
            logging.info("An error occurred. Acquisition unsuccessful.")
            ft.running_subf.cancel()
            raise
        finally:
            logging.info("Tiled acquisition ended")
            main_data.stage.moveAbs(orig_pos)

