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

from collections import OrderedDict
from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, \
    RUNNING
import logging
import math
import numpy
from odemis import model, dataio, util
from odemis.acq import stitching, stream, acqmng
from odemis.acq.stitching import WEAVER_MEAN, WEAVER_COLLAGE_REVERSE
from odemis.acq.stream import Stream, SEMStream, CameraStream, \
    RepetitionStream, StaticStream, UNDEFINED_ROI, EMStream, ARStream, SpectrumStream, \
    FluoStream, MultipleDetectorStream, MonochromatorSettingsStream, CLStream
from odemis.dataio import get_available_formats
import odemis.gui
from odemis.gui.comp import popup
from odemis.gui.conf import get_acqui_conf
from odemis.gui.plugin import Plugin, AcquisitionDialog
from odemis.gui.util import call_in_wx_main, formats_to_wildcards
from odemis.util import dataio as udataio
from odemis.util import img, TimeoutError
import os
import psutil
import threading
import time
import wx


class TileAcqPlugin(Plugin):
    name = "Tile acquisition"
    __version__ = "1.7"
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
            "tooltip": "Approximate amount of overlapping area between tiles",
        }),

        ("filename", {
            "tooltip": "Pattern of each filename",
            "control_type": odemis.gui.CONTROL_SAVE_FILE,
            "wildcard": formats_to_wildcards(get_available_formats(os.O_WRONLY))[0],
        }),
        ("stitch", {
            "tooltip": "Use all the tiles to create a large-scale image at the end of the acquisition",
        }),
        ("expectedDuration", {
        }),
        ("totalArea", {
            "tooltip": "Approximate area covered by all the streams"
        }),
        ("fineAlign", {
            "label": "Fine alignment",
        })
    ))

    def __init__(self, microscope, main_app):
        super(TileAcqPlugin, self).__init__(microscope, main_app)

        self._dlg = None
        self._tab = None  # the acquisition tab
        self.ft = model.InstantaneousFuture()  # acquisition future
        self.microscope = microscope

        # Can only be used with a microscope
        if not microscope:
            return
        else:
            # Check if microscope supports tiling (= has a sample stage)
            main_data = self.main_app.main_data
            if main_data.stage:
                self.addMenu("Acquisition/Tile...\tCtrl+G", self.show_dlg)
            else:
                logging.info("Tile acquisition not available as no stage present")
                return

        self._ovrl_stream = None  # stream for fine alignment

        self.nx = model.IntContinuous(5, (1, 1000), setter=self._set_nx)
        self.ny = model.IntContinuous(5, (1, 1000), setter=self._set_ny)
        self.overlap = model.FloatContinuous(20, (1, 80), unit="%")
        self.filename = model.StringVA("a.ome.tiff")
        self.expectedDuration = model.VigilantAttribute(1, unit="s", readonly=True)
        self.totalArea = model.TupleVA((1, 1), unit="m", readonly=True)
        self.stitch = model.BooleanVA(True)

        # Allow to running fine alignment procedure, only on SECOM and DELPHI
        self.fineAlign = model.BooleanVA(False)
        if microscope.role not in ("secom", "delphi"):
            self.vaconf["fineAlign"]["control_type"] = odemis.gui.CONTROL_NONE

        # TODO: manage focus (eg, autofocus or ask to manual focus on the corners
        # of the ROI and linearly interpolate)

        self.nx.subscribe(self._update_exp_dur)
        self.ny.subscribe(self._update_exp_dur)
        self.fineAlign.subscribe(self._update_exp_dur)
        self.nx.subscribe(self._update_total_area)
        self.ny.subscribe(self._update_total_area)
        self.overlap.subscribe(self._update_total_area)

        # Warn if memory will be exhausted
        self.nx.subscribe(self._memory_check)
        self.ny.subscribe(self._memory_check)
        self.stitch.subscribe(self._memory_check)

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
            if isinstance(s, stream.OpticalStream) and not isinstance(s, stream.ScannedFluoStream):
                break
        else:
            return False

        return True

    def _get_visible_streams(self):
        """
        Returns the streams set as visible in the acquisition dialog
        """
        if not self._dlg:
            return []
        ss = self._dlg.view.getStreams() + self._dlg.hidden_view.getStreams()
        logging.debug("View has %d streams", len(ss))
        return ss

    def _get_new_filename(self):
        conf = get_acqui_conf()
        return os.path.join(
            conf.last_path,
            u"%s%s" % (time.strftime("%Y%m%d-%H%M%S"), conf.last_extension)
        )

    def _on_streams_change(self, _=None):
        ss = self._get_visible_streams()
        # Subscribe to all relevant setting changes
        for s in ss:
            for va in self._get_settings_vas(s):
                va.subscribe(self._update_exp_dur)
                va.subscribe(self._memory_check)

        # Disable fine alignment if it's not possible
        if self._dlg:
            for entry in self._dlg.setting_controller.entries:
                if hasattr(entry, "vigilattr"):
                    if entry.vigilattr == self.fineAlign:
                        if self._can_fine_align(ss):
                            entry.lbl_ctrl.Enable(True)
                            entry.value_ctrl.Enable(True)
                            self._ovrl_stream = self._create_overlay_stream(ss)
                        else:
                            entry.lbl_ctrl.Enable(False)
                            entry.value_ctrl.Enable(False)
                        break

    def _unsubscribe_vas(self):
        ss = self._get_live_streams()

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
        # Find the stream with the smallest FoV
        try:
            fov = self._guess_smallest_fov()
        except ValueError as ex:
            logging.debug("Cannot compute total area: %s", ex)
            return

        # * number of tiles - overlap
        nx = self.nx.value
        ny = self.ny.value
        logging.debug("Updating total area based on FoV = %s m x (%d x %d)", fov, nx, ny)
        ta = (fov[0] * (nx - (nx - 1) * self.overlap.value / 100),
              fov[1] * (ny - (ny - 1) * self.overlap.value / 100))

        # Use _set_value as it's read only
        self.totalArea._set_value(ta, force_write=True)

    def _set_nx(self, nx):
        """
        Check that stage limit is not exceeded during acquisition of nx tiles.
        It automatically clips the maximum value.
        """
        stage = self.main_app.main_data.stage
        orig_pos = stage.position.value
        tile_size = self._guess_smallest_fov()
        overlap = 1 - self.overlap.value / 100
        tile_pos_x = orig_pos["x"] + self.nx.value * tile_size[0] * overlap

        # The acquisition region only extends to the right and to the bottom, never
        # to the left of the top of the current position, so it is not required to
        # check the distance to the top and left edges of the stage.
        if hasattr(stage.axes["x"], "range"):
            max_x = stage.axes["x"].range[1]
            if tile_pos_x > max_x:
                nx = max(1, int((max_x - orig_pos["x"]) / (overlap * tile_size[0])))
                logging.info("Restricting number of tiles in x direction to %i due to stage limit.",
                             nx)
        return nx

    def _set_ny(self, ny):
        """
        Check that stage limit is not exceeded during acquisition of ny tiles.
        It automatically clips the maximum value.
        """
        stage = self.main_app.main_data.stage
        orig_pos = stage.position.value
        tile_size = self._guess_smallest_fov()
        overlap = 1 - self.overlap.value / 100
        tile_pos_y = orig_pos["y"] - self.ny.value * tile_size[1] * overlap

        if hasattr(stage.axes["y"], "range"):
            min_y = stage.axes["y"].range[0]
            if tile_pos_y < min_y:
                ny = max(1, int(-(min_y - orig_pos["y"]) / (overlap * tile_size[1])))
                logging.info("Restricting number of tiles in y direction to %i due to stage limit.",
                             ny)

        return ny

    def _guess_smallest_fov(self):
        """
        Return (float, float): smallest width and smallest height of all the FoV
          Note: they are not necessarily from the same FoV.
        raise ValueError: If no stream selected
        """
        ss = self._get_live_streams()
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
        self._tab = self.main_app.main_data.tab.value
        if self._tab.name not in ("secom_live", "sparc_acqui"):
            box = wx.MessageDialog(self.main_app.main_frame,
                       "Tiled acquisition must be done from the acquisition tab.",
                       "Tiled acquisition not possible", wx.OK | wx.ICON_STOP)
            box.ShowModal()
            box.Destroy()
            return

        self._tab.streambar_controller.pauseStreams()

        # If no ROI is selected, select entire area
        try:
            if self._tab.tab_data_model.semStream.roi.value == UNDEFINED_ROI:
                self._tab.tab_data_model.semStream.roi.value = (0, 0, 1, 1)
        except AttributeError:
            pass  # Not a SPARC

        # Disable drift correction (on SPARC)
        if hasattr(self._tab.tab_data_model, "driftCorrector"):
            self._tab.tab_data_model.driftCorrector.roi.value = UNDEFINED_ROI

        ss = self._get_live_streams()
        self.filename.value = self._get_new_filename()

        dlg = AcquisitionDialog(self, "Tiled acquisition",
                                "Acquire a large area by acquiring the streams multiple "
                                "times over a grid.")
        self._dlg = dlg
        # don't allow adding/removing streams
        self._dlg.streambar_controller.to_static_mode()

        dlg.addSettings(self, self.vaconf)
        for s in ss:
            if isinstance(s, (ARStream, SpectrumStream, MonochromatorSettingsStream)):
                # TODO: instead of hard-coding the list, a way to detect the type
                # of live image?
                logging.info("Not showing stream %s, for which the live image is not spatial", s)
                dlg.addStream(s, index=None)
            else:
                dlg.addStream(s, index=0)

        dlg.addButton("Cancel")
        dlg.addButton("Acquire", self.acquire, face_colour='blue')

        # Update acq time and area when streams are added/removed. Add stream settings
        # to subscribed vas.
        dlg.view.stream_tree.flat.subscribe(self._update_exp_dur, init=True)
        dlg.view.stream_tree.flat.subscribe(self._update_total_area, init=True)
        dlg.view.stream_tree.flat.subscribe(self._on_streams_change, init=True)

        # Default fineAlign to True if it's possible
        # Use live streams to make the decision since visible streams might not be initialized yet
        # TODO: the visibility of the streams seems to be reset when the plugin is started,
        # a stream that is invisible in the main panel becomes visible. This should be fixed.
        if self._can_fine_align(ss):
            self.fineAlign.value = True
            self._ovrl_stream = self._create_overlay_stream(ss)

        # This looks tautologic, but actually, it forces the setter to check the
        # value is within range, and will automatically reduce it if necessary.
        self.nx.value = self.nx.value
        self.ny.value = self.ny.value
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
        dlg.Destroy()
        self._dlg = None

    # black list of VAs name which are known to not affect the acquisition time
    VAS_NO_ACQUSITION_EFFECT = ("image", "autoBC", "intensityRange", "histogram",
                                "is_active", "should_update", "status", "name", "tint")

    def _create_overlay_stream(self, streams):
        for s in streams:
            if isinstance(s, EMStream):
                em_det = s.detector
                em_emt = s.emitter
            elif isinstance(s, stream.OpticalStream) and not isinstance(s, stream.ScannedFluoStream):
                opt_det = s.detector
        main_data = self.main_app.main_data
        st = stream.OverlayStream("Fine alignment", opt_det, em_emt, em_det, opm=main_data.opm)
        st.dwellTime.value = main_data.fineAlignDwellTime.value
        return st

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

    def _get_live_streams(self):
        """
        Return all the live streams for tiled acquisition present in the given tab
        """
        tab_data = self._tab.tab_data_model
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
        all_ss (list of Streams): all acquisition streams possibly including overlay stream
        stitch_ss (list of Streams): acquisition streams to be used for stitching (no overlay stream)
        """
        # On the SPARC, the acquisition streams are not the same as the live
        # streams. On the SECOM/DELPHI, they are the same (for now)
        live_st = self._get_visible_streams()
        tab_data = self._tab.tab_data_model

        if hasattr(tab_data, "acquisitionStreams"):
            acq_st = tab_data.acquisitionStreams
            # Discard the acquisition streams which are not visible
            stitch_ss = []
            for acs in acq_st:
                if (acs in live_st or
                    (isinstance(acs, MultipleDetectorStream) and
                     any(subs in live_st for subs in acs.streams))
                   ):
                    stitch_ss.append(acs)
        else:
            # No special acquisition streams
            stitch_ss = live_st[:]

        # Add the overlay stream if requested
        all_ss = stitch_ss[:]
        if self.fineAlign.value and self._can_fine_align(live_st):
            all_ss = stitch_ss + [self._ovrl_stream]
        return all_ss, stitch_ss

    def _generate_scanning_indices(self, rep):
        """
        Generate the explicit X/Y position of each tile, in the scanning order
        rep (int, int): X, Y number of tiles
        return (generator of tuple(int, int)): x/y positions, starting from 0,0
        """
        # For now we do forward/backward on X (fast), and Y (slowly)
        direction = 1
        for iy in range(rep[1]):
            if direction == 1:
                for ix in range(rep[0]):
                    yield (ix, iy)
            else:
                for ix in range(rep[0] - 1, -1, -1):
                    yield (ix, iy)

            direction *= -1

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
                return shape[0] * pxs[0], shape[1] * pxs[1]

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
        ss, stitch_ss = self._get_acq_streams()

        if remaining is None:
            remaining = self.nx.value * self.ny.value
        acqt = acqmng.estimateTime(ss)

        if self.stitch.value:
            # Estimate stitching time based on number of pixels in the overlapping part
            max_pxs = 0
            for s in stitch_ss:
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
                # one with the smallest FoV (on the SECOM) and the most contrast.
                # TODO: on the SPARC the CL (aka PMT) images often have even a
                # better contrast, so maybe make it higher priority than EM?
                if da.metadata[model.MD_ACQ_TYPE] == model.MD_AT_EM:
                    return numpy.prod(da.shape)  # More pixel to find the overlap
                elif da.metadata[model.MD_ACQ_TYPE] == model.MD_AT_CL:
                    return numpy.prod(da.shape) / 10
                elif da.metadata[model.MD_ACQ_TYPE]:
                    # A lot less likely
                    return numpy.prod(da.shape) / 100

            das.sort(key=leader_quality, reverse=True)
        return das

    def _filter_duplicate_pos(self, tiles):
        """
        Remove tiles which are at the same position as another one.
        It only looks at the "leader" of each set of tile (acquired at the same place).
        If the leader is considered duplicate, the whole set is discarded.
        Useful because the stitching functions forbid tiles (aka fail to run) if
        two tiles are at the same position, but due to hardware issue, that might happen.
        tiles: list of tuples of DataArrays
        return: list of tuples of DataArrays
        """
        pos_seen = set()
        tiles_uniq = []
        for das in tiles:
            pos = das[0].metadata.get(model.MD_POS)
            if pos in pos_seen:
                logging.warning("Found a tile at the same position as another one %s, will drop it", pos)
            else:
                pos_seen.add(pos)
                tiles_uniq.append(das)

        return tiles_uniq

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

    def _estimateStreamPixels(self, s):
        """
        return (int): the number of pixels the stream will generate during an
          acquisition
        """
        px = 0
        if isinstance(s, MultipleDetectorStream):
            for st in s.streams:
                # For the EMStream of a SPARC MDStream, it's just one pixel per
                # repetition (excepted in case  of fuzzing, but let's be optimistic)
                if isinstance(st, (EMStream, CLStream)):
                    px += 1
                else:
                    px += self._estimateStreamPixels(st)

            if hasattr(s, 'repetition'):
                px *= s.repetition.value[0] * s.repetition.value[1]

            return px
        elif isinstance(s, (ARStream, SpectrumStream)):
            # Temporarily reports 0 px, as we don't stitch these streams for now
            return 0

        if hasattr(s, 'emtResolution'):
            px = numpy.prod(s.emtResolution.value)
        elif hasattr(s, 'detResolution'):
            px = numpy.prod(s.detResolution.value)
        elif model.hasVA(s.detector, "resolution"):
            px = numpy.prod(s.detector.resolution.value)
        elif model.hasVA(s.emitter, "resolution"):
            px = numpy.prod(s.emitter.resolution.value)
        else:
            # This shouldn't happen, but let's "optimistic" by assuming it'll
            # only acquire one pixel.
            logging.info("Resolution of stream %s cannot be determined.", s)
            px = 1

        return px

    MEMPP = 22  # bytes per pixel, found empirically
    @call_in_wx_main
    def _memory_check(self, _=None):
        """
        Makes an estimate for the amount of memory that will be consumed during
        stitching and compares it to the available memory on the computer.
        Displays a warning if memory exceeds available memory.
        """
        if not self._dlg:  # Already destroyed? => no need to care
            return

        if self.stitch.value:
            # Number of pixels for acquisition
            pxs = sum(self._estimateStreamPixels(s) for s in self._get_acq_streams()[1])
            pxs *= self.nx.value * self.ny.value

            # Memory calculation
            mem_est = pxs * self.MEMPP
            mem_computer = psutil.virtual_memory().total
            logging.debug("Estimating %g GB needed, while %g GB available",
                          mem_est / 1024 ** 3, mem_computer / 1024 ** 3)
            # Assume computer is using 2 GB RAM for odemis and other programs
            mem_sufficient = mem_est < mem_computer - (2 * 1024 ** 3)
        else:
            mem_sufficient = True

        # Display warning
        if mem_sufficient:
            self._dlg.setAcquisitionInfo(None)
        else:
            txt = ("Stitching this area requires %.1f GB of memory.\n"
                   "Running the acquisition might cause your computer to crash." %
                   (mem_est / 1024 ** 3,))
            self._dlg.setAcquisitionInfo(txt, lvl=logging.ERROR)

    def acquire(self, dlg):
        main_data = self.main_app.main_data
        str_ctrl = self._tab.streambar_controller
        str_ctrl.pauseStreams()
        dlg.pauseSettings()
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

        ss, stitch_ss = self._get_acq_streams()
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

                ft.running_subf = acqmng.acquire(ss, self.main_app.main_data.settings_obs)
                das, e = ft.running_subf.result()  # blocks until all the acquisitions are finished
                if e:
                    logging.warning("Acquisition for tile %dx%d partially failed: %s",
                                    ix, iy, e)

                if ft._task_state == CANCELLED:
                    raise CancelledError()

                # TODO: do in a separate thread
                fn_tile = "%s-%.5dx%.5d%s" % (fn_bs, ix, iy, fn_ext)
                logging.debug("Will save data of tile %dx%d to %s", ix, iy, fn_tile)
                exporter.export(fn_tile, das)

                if ft._task_state == CANCELLED:
                    raise CancelledError()

                if self.stitch.value:
                    # Sort tiles (typically, largest SEM on first position)
                    # Make it a tuple to indicate to the stitching that all the streams should be tied in position.
                    da_list.append(tuple(self.sort_das(das, stitch_ss)))

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

                # Check that all the tiles have a different MD_POS, because the
                # registrar doesn't like tiles on the same position. This shouldn't
                # happen, but sometimes the stage may do weird things...
                da_list = self._filter_duplicate_pos(da_list)

                logging.info("Computing big image out of %d images", len(da_list))
                das_registered = stitching.register(da_list)

                # Select weaving method
                # On a Sparc system the mean weaver gives the best result since it
                # smoothes the transitions between tiles. However, using this weaver on the
                # Secom/Delphi generates an image with dark stripes in the overlap regions which are
                # the result of carbon decomposition effects that typically occur in samples imaged
                # by these systems. To mediate this, we use the
                # collage_reverse weaver that only shows the overlap region of the tile that
                # was imaged first.
                if self.microscope.role in ("secom", "delphi"):
                    weaving_method = WEAVER_COLLAGE_REVERSE
                    logging.info("Using weaving method WEAVER_COLLAGE_REVERSE.")
                else:
                    weaving_method = WEAVER_MEAN
                    logging.info("Using weaving method WEAVER_MEAN.")

                # Weave every stream
                if isinstance(das_registered[0], tuple):
                    for s in range(len(das_registered[0])):
                        streams = []
                        for da in das_registered:
                            streams.append(da[s])
                        da = stitching.weave(streams, weaving_method)
                        da.metadata[model.MD_DIMS] = "YX"  # TODO: do it in the weaver
                        st_data.append(da)
                else:
                    da = stitching.weave(das_registered, weaving_method)
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
            if ft._task_state == CANCELLED:
                raise CancelledError()
            dlg.Close()

            # Open analysis tab
            if st_data:
                popup.show_message(self.main_app.main_frame, "Tiled acquisition complete",
                                   "Will display stitched image")
                self.showAcquisition(fn)
            else:
                popup.show_message(self.main_app.main_frame, "Tiled acquisition complete",
                                   "Will display last tile")
                # It's easier to know the last filename, and it's also the most
                # interesting for the user, as if something went wrong (eg, focus)
                # it's the tile the most likely to show it.
                self.showAcquisition(fn_tile)

            # TODO: also export a full image (based on reported position, or based
            # on alignment detection)
        except CancelledError:
            logging.debug("Acquisition cancelled")
            dlg.resumeSettings()
        except Exception as ex:
            logging.exception("Acquisition failed.")
            ft.running_subf.cancel()
            ft.set_result(None)
            # Show also in the window. It will be hidden next time a setting is changed.
            self._dlg.setAcquisitionInfo("Acquisition failed: %s" % (ex,),
                                         lvl=logging.ERROR)
        finally:
            logging.info("Tiled acquisition ended")
            main_data.stage.moveAbs(orig_pos)

