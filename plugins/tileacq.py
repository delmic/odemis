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

import csv
import logging
import math
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures._base import CancelledError
from enum import Enum
from typing import Any, Dict, List, Tuple

import numpy
import wx
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
from shapely.geometry import MultiPoint, Point
from scipy.spatial import Delaunay, ConvexHull

import odemis.gui
from odemis import dataio, model
from odemis.acq import stream
from odemis.acq.acqmng import AcquisitionTask
from odemis.acq.stitching import (
    REGISTER_GLOBAL_SHIFT,
    REGISTER_IDENTITY,
    WEAVER_COLLAGE,
    WEAVER_COLLAGE_REVERSE,
    WEAVER_MEAN,
    acquireTiledArea,
)
from odemis.acq.stitching._tiledacq import TiledAcquisitionTask
from odemis.acq.stream import (
    UNDEFINED_ROI,
    ARStream,
    EMStream,
    MonochromatorSettingsStream,
    MultipleDetectorStream,
    SpectrumStream,
    StaticStream,
)
from odemis.dataio import get_available_formats
from odemis.gui.comp import popup
from odemis.gui.comp.stream_panel import OPT_BTN_REMOVE, OPT_BTN_SHOW
from odemis.gui.conf import get_acqui_conf
from odemis.gui.plugin import AcquisitionDialog, Plugin
from odemis.gui.util import call_in_wx_main, formats_to_wildcards
from odemis.model import CancellableThreadPoolExecutor
from odemis.util import dataio as udataio

try:
    from sparc_calibrations.parabolic_mirror_alignment import (
        AlignmentAxis,
        ParabolicMirrorAlignmentTask,
    )
    sparc_calib_available = True
except ImportError as err:
    sparc_calib_available = False
    logging.info("sparc_calibrations module not available, mirror z alignment not possible: %s", err)


class TileMode(Enum):
    STANDARD = "Standard (X × Y grid)"
    CUSTOM = "Custom (from tsv file with mirror alignment)"


class TileColumnNames(Enum):
    NUMBER = "Tile Number"
    POSX = "Stage Position.X [m]"
    POSY = "Stage Position.Y [m]"


class TileAcqPlugin(Plugin):
    name = "Tile acquisition"
    __version__ = "1.8"
    __author__ = "Éric Piel, Philip Winkler"
    __license__ = "GPLv2"

    # Describe how the values should be displayed
    # See odemis.gui.conf.data for all the possibilities
    vaconf = OrderedDict((
        ("tile_mode", {
            "label": "Tile mode",
            "control_type": odemis.gui.CONTROL_COMBO,
            "tooltip": "Standard: Regular grid of X × Y tiles\n"
                       "Custom: Load tile positions from external file",
        }),
        ("nx", {
            "label": "Tiles X",
            "control_type": odemis.gui.CONTROL_INT,  # no slider
        }),
        ("ny", {
            "label": "Tiles Y",
            "control_type": odemis.gui.CONTROL_INT,  # no slider
        }),
        ("tiles_file", {
            "label": "Tiles file",
            "tooltip": "TSV file with tile positions (x, y in meters)",
            "control_type": odemis.gui.CONTROL_OPEN_FILE,
            "wildcard": formats_to_wildcards({"TSV": [".tsv"]})[0],
        }),
        ("overlap", {
            "tooltip": "Approximate amount of overlapping area between tiles",
        }),
        ("filename", {
            "tooltip": "Pattern of each filename",
            "control_type": odemis.gui.CONTROL_SAVE_FILE,
            "wildcard": formats_to_wildcards(get_available_formats(os.O_WRONLY))[0],
        }),
        ("z_map", {
            "label": "Z-map",
            "tooltip": "Use a z-map to adjust the z position for each tile during acquisition.",
            "control_type": odemis.gui.CONTROL_CHECK,
        }),
        ("stitch", {
            "tooltip": "Use all the tiles to create a large-scale image at the end of the acquisition",
        }),
        ("weaver", {
            "label": "Weaving method",
            "control_type": odemis.gui.CONTROL_COMBO,
            "tooltip": "Mean: Overlapping pixels in the final image are averaged across tiles\n"
                       "Collage: Shows tiles at their center positions, assuming uniform pixel"
                       " sizes and ignoring rotation/skew, new tiles are shown on top of the previous tile.\n"
                       "Collage (reverse order): Similar to Collage, but fills only empty regions"
                       " with new tiles to preserve higher-quality overlaps from first-time imaging.",
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

        self._executor = None
        mirror_align_possible = False
        if microscope.role == "sparc2":
            if main_data.mirror and main_data.stage and main_data.ccd and main_data.ebeam_focus:
                mirror_md = main_data.mirror.getMetadata()
                calib = mirror_md.get(model.MD_CALIB, {})
                mirror_align_calib = "auto_align_min_step_size" in calib and "ebeam_working_distance" in calib
                mirror_align_possible = mirror_align_calib and sparc_calib_available

        tile_mode_choices = {
            TileMode.STANDARD.name: TileMode.STANDARD.value,
        }
        if mirror_align_possible:
            tile_mode_choices[TileMode.CUSTOM.name] = TileMode.CUSTOM.value
            self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time
        self.tile_mode = model.VAEnumerated(TileMode.STANDARD.name, choices=tile_mode_choices)
        self.nx = model.IntContinuous(5, (1, 1000), setter=self._set_nx)
        self.ny = model.IntContinuous(5, (1, 1000), setter=self._set_ny)
        self.z_map = model.BooleanVA(False)
        self.overlap = model.FloatContinuous(0.2, (0., 0.8))
        self.filename = model.StringVA("a.ome.tiff")
        self.tiles_file = model.StringVA("")
        self.tile_map: Dict[int, Tuple[float, float]] = {}
        self.expectedDuration = model.VigilantAttribute(1, unit="s", readonly=True)
        self.totalArea = model.TupleVA((1, 1), unit="m", readonly=True)
        self.stitch = model.BooleanVA(True)
        weaver_choices = {
            WEAVER_MEAN: "Mean",
            WEAVER_COLLAGE: "Collage",
            WEAVER_COLLAGE_REVERSE: "Collage (reverse order)",
        }
        self.weaver = model.VAEnumerated(WEAVER_MEAN, choices=weaver_choices)
        self.registrar = REGISTER_GLOBAL_SHIFT

        # Allow to running fine alignment procedure, only on SECOM and DELPHI
        self.fineAlign = model.BooleanVA(False)
        if microscope.role not in ("secom", "delphi"):
            self.vaconf["fineAlign"]["control_type"] = odemis.gui.CONTROL_NONE

        # Select weaving method
        # On a Sparc system the mean weaver gives the best result since it smoothes the
        # transitions between tiles. However, using this weaver on the Secom/Delphi
        # generates an image with dark stripes in the overlap regions which are the
        # result of carbon decomposition effects that typically occur in samples imaged
        # by these systems. To mediate this, we use the collage_reverse weaver that
        # only shows the overlap region of the tile that was imaged first.
        if self.microscope.role in ("secom", "delphi"):
            self.weaver.value = WEAVER_COLLAGE_REVERSE
            logging.info("Using weaving method WEAVER_COLLAGE_REVERSE.")
        else:
            self.weaver.value = WEAVER_MEAN
            logging.info("Using weaving method WEAVER_MEAN.")

        # TODO: manage focus (eg, autofocus or ask to manual focus on the corners
        # of the ROI and linearly interpolate)

        self.nx.subscribe(self._update_total_area)
        self.nx.subscribe(self._update_exp_dur)
        self.nx.subscribe(self._memory_check)

        self.ny.subscribe(self._update_total_area)
        self.ny.subscribe(self._update_exp_dur)
        self.ny.subscribe(self._memory_check)

        self.overlap.subscribe(self._update_total_area)

        self.fineAlign.subscribe(self._update_exp_dur)

        self.stitch.subscribe(self._memory_check)

        self.weaver.subscribe(self._on_weaver_change)
        self.weaver.subscribe(self._update_exp_dur)
        self.weaver.subscribe(self._memory_check)

        self.tile_mode.subscribe(self._on_tile_mode_change)

        self.tiles_file.subscribe(self._on_tile_file_change)

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
            "%s%s" % (time.strftime("%Y%m%d-%H%M%S"), conf.last_extension)
        )

    @call_in_wx_main
    def _on_tile_mode_change(self, mode):
        """Show/hide relevant controls based on tile mode"""
        if not self._dlg:
            return

        if mode == TileMode.CUSTOM.name:
            self.overlap.value = 0

        for entry in self._dlg.setting_controller.entries:
            if hasattr(entry, "vigilattr"):
                # Show grid controls only in standard mode
                if entry.vigilattr in (self.nx, self.ny, self.overlap):
                    entry.lbl_ctrl.Show(mode == TileMode.STANDARD.name)
                    entry.value_ctrl.Show(mode == TileMode.STANDARD.name)
                # Show file control only in custom mode
                if entry.vigilattr in (self.tiles_file, self.z_map):
                    entry.lbl_ctrl.Show(mode == TileMode.CUSTOM.name)
                    entry.value_ctrl.Show(mode == TileMode.CUSTOM.name)
        self._update_total_area(None)
        self._update_exp_dur(None)
        self._memory_check(None)
        self._dlg.Layout()
        self._dlg.Refresh()

    def _on_tile_file_change(self, filepath: str):
        """
        """
        self.tile_map.clear()
        try:
            with open(filepath, 'r', encoding='utf-8-sig', newline='') as f:
                reader = csv.DictReader(f, delimiter='\t')
                for row in reader:
                    logging.debug(row)
                    # Skip empty rows
                    if not row:
                        continue

                    try:
                        # Parse the columns
                        tile_num = int(row[TileColumnNames.NUMBER.value].strip())
                        x = float(row[TileColumnNames.POSX.value].strip())
                        y = float(row[TileColumnNames.POSY.value].strip())

                        self.main_app.main_data.stage._checkMoveAbs({"x": x, "y" : y})
                        self.tile_map[tile_num] = (x, y)

                    except (ValueError, KeyError):
                        logging.debug(f"Not able to process row {row}")
                        continue
            self._update_total_area(None)
            self._update_exp_dur(None)
            self._memory_check(None)
        except Exception:
            logging.exception("Failed to load tile positions from %s", filepath)

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

    def _on_weaver_change(self, weaver):
        # For WEAVER_COLLAGE, use REGISTER_IDENTITY, which is the fastest and stiches the tiles based on the stage positions
        if weaver == WEAVER_COLLAGE:
            self.registrar = REGISTER_IDENTITY
        else:
            self.registrar = REGISTER_GLOBAL_SHIFT

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
        try:
            stitch_ss = self._get_stitch_streams()
            if not stitch_ss:
                self.expectedDuration._set_value(1, force_write=True)
                return

            # Calculate bounding box of the acquisition region
            region = self._get_region(self.main_app.main_data.stage.position.value)
            if region is None:
                return

            overlay_stream = None
            if self.fineAlign.value and self._can_fine_align(stitch_ss):
                overlay_stream = self._ovrl_stream

            task = TiledAcquisitionTask(
                stitch_ss,
                self.main_app.main_data.stage,
                region,
                overlap=self.overlap.value,
                settings_obs=self.main_app.main_data.settings_obs,
                weaver=self.weaver.value if self.stitch.value else None,
                registrar=self.registrar if self.stitch.value else None,
                overlay_stream=overlay_stream,
                sfov=self._guess_smallest_fov(),
            )
            if self.tile_mode.value == TileMode.CUSTOM.name:
                task._number_of_tiles = len(self.tile_map)
            tat = task.estimateTime()
        except (ValueError, AttributeError):
            # No streams or cannot compute FoV
            logging.debug("Cannot compute expected acquisition duration")
            tat = 0

        # Typically there are a few more pixels inserted at the beginning of
        # each line for the settle time of the beam. We don't take this into
        # account and so tend to slightly under-estimate.

        # Use _set_value as it's read only
        self.expectedDuration._set_value(math.ceil(tat), force_write=True)

    def _update_total_area(self, _=None):
        """
        Called when VA that affects the total area is changed
        """
        if self.tile_mode.value == TileMode.STANDARD.name:
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
        elif self.tile_mode.value == TileMode.CUSTOM.name:
            area = (0, 0)
            if self.tile_map:
                coords = list(self.tile_map.values())

                xs, ys = zip(*coords)
                xmin, xmax = min(xs), max(xs)
                ymin, ymax = min(ys), max(ys)
                area = (xmax - xmin, ymax - ymin)

            self.totalArea._set_value(area, force_write=True)

    def _set_nx(self, nx):
        """
        Check that stage limit is not exceeded during acquisition of nx tiles.
        It automatically clips the maximum value.
        """
        stage = self.main_app.main_data.stage
        orig_pos = stage.position.value
        tile_size = self._guess_smallest_fov()
        overlap = 1 - self.overlap.value
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
        overlap = 1 - self.overlap.value
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

        return TiledAcquisitionTask.guessSmallestFov(ss)

    def show_dlg(self):
        # TODO: if there is a chamber, only allow if there is vacuum

        # Fail if the live tab is not selected
        self._tab = self.main_app.main_data.tab.value
        self._align_tab = self.main_app.main_data.getTabByName("sparc2_align")
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
        sp_options = OPT_BTN_REMOVE | OPT_BTN_SHOW

        dlg.addSettings(self, self.vaconf)
        for s in ss:
            if isinstance(s, (ARStream, SpectrumStream, MonochromatorSettingsStream)):
                # TODO: instead of hard-coding the list, a way to detect the type
                # of live image?
                logging.info("Not showing stream %s, for which the live image is not spatial", s)
                dlg.addStream(s, index=None, sp_options=sp_options)
            else:
                dlg.addStream(s, index=0, sp_options=sp_options)

        dlg.addButton("Cancel")
        dlg.addButton("Acquire", self.acquire, face_colour='blue')

        # Update acq time and area when streams are added/removed. Add stream settings
        # to subscribed vas.
        dlg.view.stream_tree.flat.subscribe(self._update_exp_dur, init=True)
        dlg.view.stream_tree.flat.subscribe(self._update_total_area, init=True)
        dlg.view.stream_tree.flat.subscribe(self._on_streams_change, init=True)
        dlg.hidden_view.stream_tree.flat.subscribe(self._update_exp_dur, init=True)
        dlg.hidden_view.stream_tree.flat.subscribe(self._update_total_area, init=True)
        dlg.hidden_view.stream_tree.flat.subscribe(self._on_streams_change, init=True)

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
        self.tile_mode._set_value(self.tile_mode.value, must_notify=True)
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

    def _get_stitch_streams(self):
        """
        :return: (list of Streams) acquisition streams to be used for stitching (no overlay stream)
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

        return stitch_ss

    @call_in_wx_main
    def _memory_check(self, _=None):
        """
        Makes an estimate for the amount of memory that will be consumed during
        stitching and compares it to the available memory on the computer.
        Displays a warning if memory exceeds available memory.
        """
        if not self._dlg:  # Already destroyed? => no need to care
            return

        mem_est = 0
        try:
            stitch_ss = self._get_stitch_streams()
            if not stitch_ss:
                return

            # Calculate bounding box of the acquisition region
            region = self._get_region(self.main_app.main_data.stage.position.value)
            if region is None:
                return

            overlay_stream = None
            if self.fineAlign.value and self._can_fine_align(stitch_ss):
                overlay_stream = self._ovrl_stream

            task = TiledAcquisitionTask(
                stitch_ss,
                self.main_app.main_data.stage,
                region,
                overlap=self.overlap.value,
                settings_obs=self.main_app.main_data.settings_obs,
                weaver=self.weaver.value if self.stitch.value else None,
                registrar=self.registrar if self.stitch.value else None,
                overlay_stream=overlay_stream,
                sfov=self._guess_smallest_fov(),
            )
            if self.tile_mode.value == TileMode.CUSTOM.name:
                task._number_of_tiles = len(self.tile_map)
            mem_sufficient, mem_est = task.estimateMemory()
        except (ValueError, AttributeError):
            # No streams or cannot compute FoV
            mem_sufficient = True

        # Display warning
        if mem_sufficient:
            self._dlg.setAcquisitionInfo(None)
        else:
            txt = ("Stitching this area requires %.1f GB of memory.\n"
                   "Running the acquisition might cause your computer to crash." %
                   (mem_est / 1024 ** 3,))
            self._dlg.setAcquisitionInfo(txt, lvl=logging.ERROR)

    def _get_region(self, start_pos: dict) -> Tuple[float, float, float, float]:
        """
        Calculate the acquisition region.

        :param start_pos: dict with 'x' and 'y' keys for the starting position,
            which is the center of the first tile.
        :return: (xmin, ymin, xmax, ymax) defining the acquisition region in meters
        """
        sfov = self._guess_smallest_fov()

        # Reliable FoV
        # The size of the smallest tile, non-including the overlap, which will be
        # lost (and also indirectly represents the precision of the stage)
        reliable_fov = ((1 - self.overlap.value) * sfov[0], (1 - self.overlap.value) * sfov[1])

        xmin = start_pos["x"] - reliable_fov[0] / 2
        ymax = start_pos["y"] + reliable_fov[1] / 2
        xmax = xmin + reliable_fov[0] * self.nx.value
        ymin = ymax - reliable_fov[1] * self.ny.value

        return (xmin, ymin, xmax, ymax)

    # Constants for safe Z movements
    SAFE_Z_OFFSET = 50.e-6  # [m] Safe offset from mirror to prevent collisions

    def _order_positions_nearest_neighbor(self,
                                        positions: List[Tuple[float, float]],
                                        start_pos: Tuple[float, float]) -> List[Tuple[float, float]]:
        """
        Order positions using intelligent adaptive path planning.

        HYBRID ALGORITHM:
        1. Detect if start_pos is inside the convex hull of positions
        2. If INSIDE: Use spiral/concentric ring traversal (optimal for circles, polygons)
        3. If OUTSIDE: Use snake/lane pattern (optimal for grids, peripheral points)

        This unified approach handles arbitrary point distributions with minimal stage travel.

        INSIDE CASE - Spiral from center outward:
            Ring 3: ●←●←●  ↙
                    ↙
            Ring 2: ●→●→●  ↘
                    ↘
            Ring 1:    ★  ↘ (start)
                    ↗
            Ring 0: ●→●→●

        OUTSIDE CASE - Snake lanes:
            Lane 0: ★→●→●→● ↘
                            ↘
            Lane 1:          ●←●←●
                            ↗
                        ↗
            Lane 2: ●→●→● →

        :param positions: List of (x, y) tuples representing all points to visit
        :param start_pos: Current stage position (x, y) tuple
        :return: List of positions ordered in optimized path (spiral or snake)
        """
        if not positions:
            return []
        if len(positions) == 1:
            return positions
        if len(positions) == 2:
            dist_0 = (positions[0][0] - start_pos[0])**2 + (positions[0][1] - start_pos[1])**2
            dist_1 = (positions[1][0] - start_pos[0])**2 + (positions[1][1] - start_pos[1])**2
            return positions if dist_0 <= dist_1 else [positions[1], positions[0]]

        # ========== DETECT IF START IS INSIDE CONVEX HULL ==========

        is_inside = False
        try:
            tri = Delaunay(positions)
            is_inside = tri.find_simplex(start_pos) >= 0
            logging.debug(f"Start position {'INSIDE' if is_inside else 'OUTSIDE'} convex hull")
        except Exception as e:
            logging.debug(f"Could not compute convex hull (likely collinear points): {e}. Using snake pattern.")
            is_inside = False

        if is_inside:
            # ========== SPIRAL PATTERN FROM CENTER OUTWARD ==========
            logging.info(f"Start position inside convex hull - using spiral outward pattern for {len(positions)} positions")
            return self._order_positions_spiral_outward(positions, start_pos)
        else:
            # ========== SNAKE LANE PATTERN FROM OUTSIDE ==========
            logging.info(f"Start position outside convex hull - using snake lane pattern for {len(positions)} positions")
            return self._order_positions_snake_lanes(positions, start_pos)

    def _order_positions_spiral_outward(self,
                                    positions: List[Tuple[float, float]],
                                    start_pos: Tuple[float, float]) -> List[Tuple[float, float]]:
        """
        Order positions in concentric rings spiraling inward to center, then outward.

        Optimal for:
        - Circular distributions (wafer area)
        - Polygonal boundaries
        - Any convex region with interior starting point

        Algorithm:
        1. Calculate centroid of all positions (center point)
        2. Find the position CLOSEST to current stage position (critical for collision avoidance!)
        3. Compute distance and angle from centroid for each position
        4. Group into concentric rings by distance from centroid
        5. Sort each ring by angle (polar coordinates)
        6. Start spiral from closest position's ring
        7. Traverse INWARD to innermost ring (decreasing distance)
        8. Then traverse OUTWARD to outermost ring (increasing distance)
        9. Alternate direction within each ring (prevents backtracking)

        Result: Smooth concentric spiral with NO backtracking, NO big jumps.

        Visual Flow (closest point at Ring 2):

        Start at Ring 2 (closest to stage):
        Ring 2: ★→●→●→● ↘
                            ↘
        Spiral INWARD:
        Ring 1:          ●←●←●
                        ↗
                    ↗
        Ring 0: ●→●→● (center)

        Then spiral OUTWARD:
        Ring 2: ●→●→● (already visited, skip)
        Ring 3: ●←●←●
        Ring 4: ●→●→●

        :param positions: List of (x, y) tuples
        :param start_pos: Current stage position (used to find spiral start point)
        :return: List of positions ordered in spiral pattern starting from closest point
        """

        positions_array = numpy.array(positions)

        # Find centroid of all positions (center reference point)
        centroid = positions_array.mean(axis=0)

        # ========== FIND CLOSEST POSITION TO START (KEY FOR SAFETY) ==========
        closest_distance_sq = float('inf')
        closest_pos = None
        closest_idx = None

        for idx, pos in enumerate(positions):
            distance_sq = (pos[0] - start_pos[0])**2 + (pos[1] - start_pos[1])**2
            if distance_sq < closest_distance_sq:
                closest_distance_sq = distance_sq
                closest_pos = pos
                closest_idx = idx

        logging.debug(
            f"Closest position to start_pos {start_pos}@{closest_idx}: {closest_pos}"
            f"at distance {numpy.sqrt(closest_distance_sq):.2e} m"
        )

        # Compute distance and angle from centroid (for ring grouping and spiral ordering)
        vectors = positions_array - centroid
        distances = numpy.linalg.norm(vectors, axis=1)
        angles = numpy.arctan2(vectors[:, 1], vectors[:, 0])

        # Determine ring spacing: adaptive based on point density
        distance_range = distances.max() - distances.min()
        num_rings = max(2, min(10, len(positions) // 5))  # 2-10 rings based on point count
        ring_spacing = distance_range / num_rings if distance_range > 0 else 1.0

        # Group points into concentric rings
        rings = {}
        closest_ring_key = None

        for idx, (pos, dist, angle) in enumerate(zip(positions, distances, angles)):
            # Determine which ring this point belongs to
            if ring_spacing > 0:
                ring_key = int(round((dist - distances.min()) / ring_spacing))
            else:
                ring_key = 0

            if ring_key not in rings:
                rings[ring_key] = []
            rings[ring_key].append((angle, list(pos)))

            # Track which ring contains the closest position
            if numpy.allclose(pos, closest_pos):
                closest_ring_key = ring_key

        # ========== BUILD SPIRAL: INWARD THEN OUTWARD ==========
        ordered_path = []

        # PHASE 1: Traverse INWARD from closest ring to center (ring 0)
        logging.debug(f"Closest position is in ring {closest_ring_key}. Spiraling inward to center (ring 0)")

        reverse_direction = False

        # Go from closest_ring_key down to 0 (innermost)
        for ring_idx in range(closest_ring_key, -1, -1):
            if ring_idx not in rings:
                continue

            ring_positions = rings[ring_idx]
            ring_positions.sort(key=lambda x: x[0])  # Sort by angle

            # For the closest ring, put the closest position first
            if ring_idx == closest_ring_key:
                try:
                    closest_angle = None
                    for angle, pos in ring_positions:
                        if numpy.allclose(pos, closest_pos):
                            closest_angle = angle
                            break

                    if closest_angle is not None:
                        # Find index of closest position in ring
                        closest_idx_in_ring = next(
                            i for i, (angle, _) in enumerate(ring_positions)
                            if numpy.isclose(angle, closest_angle)
                        )

                        # Determine initial direction (forward or backward from closest)
                        if closest_idx_in_ring < len(ring_positions) / 2:
                            # Closer to start of ring, traverse forward (clockwise)
                            ordered_path.extend([tuple(pos) for _, pos in ring_positions[closest_idx_in_ring:]])
                            reverse_direction = True  # Next ring (inward) goes backward (counter-clockwise)
                        else:
                            # Closer to end of ring, traverse backward (counter-clockwise)
                            ring_positions_reversed = ring_positions[:closest_idx_in_ring+1]
                            ring_positions_reversed.reverse()
                            ordered_path.extend([tuple(pos) for _, pos in ring_positions_reversed])
                            reverse_direction = False  # Next ring (inward) goes forward (clockwise)

                        logging.debug(
                            f"Ring {ring_idx} (closest): Starting from angle {closest_angle:.2f}, "
                            f"direction={'backward' if reverse_direction else 'forward'}, "
                            f"positions: {len(ring_positions)}"
                        )
                except (ValueError, StopIteration):
                    ordered_path.extend([tuple(pos) for _, pos in ring_positions])
                    reverse_direction = not reverse_direction
            else:
                # For inward rings, alternate direction (smooth spiral)
                if reverse_direction:
                    ordered_path.extend([tuple(pos) for _, pos in reversed(ring_positions)])
                    logging.debug(f"Ring {ring_idx} (inward): backward, positions: {len(ring_positions)}")
                else:
                    ordered_path.extend([tuple(pos) for _, pos in ring_positions])
                    logging.debug(f"Ring {ring_idx} (inward): forward, positions: {len(ring_positions)}")
                reverse_direction = not reverse_direction

        # PHASE 2: Traverse OUTWARD from closest ring to outermost (skip ring 0 which we already did)
        logging.debug(f"Now spiraling outward from ring {closest_ring_key} to outermost")

        max_ring = max(rings.keys()) if rings else 0

        # Go from closest_ring_key+1 up to max_ring (outermost)
        for ring_idx in range(closest_ring_key + 1, max_ring + 1):
            if ring_idx not in rings:
                continue

            ring_positions = rings[ring_idx]
            ring_positions.sort(key=lambda x: x[0])  # Sort by angle

            # Alternate direction in successive rings (spiral effect, no backtracking)
            if reverse_direction:
                ordered_path.extend([tuple(pos) for _, pos in reversed(ring_positions)])
                logging.debug(f"Ring {ring_idx} (outward): backward, positions: {len(ring_positions)}")
            else:
                ordered_path.extend([tuple(pos) for _, pos in ring_positions])
                logging.debug(f"Ring {ring_idx} (outward): forward, positions: {len(ring_positions)}")
            reverse_direction = not reverse_direction

        logging.info(
            f"Spiral pattern (inward→outward): {len(rings)} rings, {len(ordered_path)} total positions. "
            f"Starting ring: {closest_ring_key}, Center (ring 0), Outermost (ring {max_ring}), "
            f"Centroid: ({centroid[0]:.2e}, {centroid[1]:.2e}), "
            f"Distance range: {distance_range:.2e} m"
        )

        return ordered_path


    def _order_positions_snake_lanes(self,
                                    positions: List[Tuple[float, float]],
                                    start_pos: Tuple[float, float]) -> List[Tuple[float, float]]:
        """
        Order positions using snake/boustrophedon pattern with smooth lane transitions.

        Optimal for:
        - Grid-like distributions
        - Starting point outside the survey region
        - Rectangular/square survey areas

        Algorithm:
        1. Analyze grid structure: determine dominant axis (X or Y)
        2. Create lanes perpendicular to dominant axis
        3. Find closest position to current stage
        4. Traverse lanes in snake pattern (alternating directions)
        5. Connect lanes smoothly to nearest endpoint (no big jumps)

        Result: Parallel passes through survey area with minimal backtracking.

        Visual example:
            Lane 0: ★→●→●→● ↘
                                ↘
            Lane 1:          ●←●←●
                            ↗
                        ↗
            Lane 2: ●→●→● →

        :param positions: List of (x, y) tuples
        :param start_pos: Current stage position
        :return: List of positions ordered in snake pattern
        """
        if not positions:
            return []

        # ========== PHASE 1: ANALYZE GRID STRUCTURE ==========
        xs = [p[0] for p in positions]
        ys = [p[1] for p in positions]

        x_range = max(xs) - min(xs)
        y_range = max(ys) - min(ys)

        # Determine dominant axis (where points are more spread out)
        use_y_lanes = x_range >= y_range  # If X spread > Y spread, create Y-lanes
        lane_threshold = 10.e-6  # 10 micrometers: points within this belong to same lane

        if use_y_lanes:
            # Create lanes based on X coordinate (lanes run along Y direction)
            sorted_positions = sorted(positions, key=lambda p: p[0])
            lanes_dict = {}
            lane_key = 0
            last_x = sorted_positions[0][0]

            for pos in sorted_positions:
                # New lane if X coordinate jumps more than threshold
                if abs(pos[0] - last_x) > lane_threshold:
                    lane_key += 1
                if lane_key not in lanes_dict:
                    lanes_dict[lane_key] = []
                lanes_dict[lane_key].append(pos)
                last_x = pos[0]

            # Sort each lane by Y coordinate (ascending)
            for lane_positions in lanes_dict.values():
                lane_positions.sort(key=lambda p: p[1])

            lanes = lanes_dict
            lane_keys = sorted(lanes_dict.keys())
            axis_name = "Y"

        else:
            # Create lanes based on Y coordinate (lanes run along X direction)
            sorted_positions = sorted(positions, key=lambda p: p[1])
            lanes_dict = {}
            lane_key = 0
            last_y = sorted_positions[0][1]

            for pos in sorted_positions:
                # New lane if Y coordinate jumps more than threshold
                if abs(pos[1] - last_y) > lane_threshold:
                    lane_key += 1
                if lane_key not in lanes_dict:
                    lanes_dict[lane_key] = []
                lanes_dict[lane_key].append(pos)
                last_y = pos[1]

            # Sort each lane by X coordinate (ascending)
            for lane_positions in lanes_dict.values():
                lane_positions.sort(key=lambda p: p[0])

            lanes = lanes_dict
            lane_keys = sorted(lanes_dict.keys())
            axis_name = "X"

        # ========== PHASE 2: FIND STARTING POINT ==========
        closest_distance_sq = float('inf')
        closest_pos = None
        closest_lane_key = None

        for lane_key in lane_keys:
            lane_positions = lanes[lane_key]
            for pos in lane_positions:
                distance_sq = (pos[0] - start_pos[0])**2 + (pos[1] - start_pos[1])**2
                if distance_sq < closest_distance_sq:
                    closest_distance_sq = distance_sq
                    closest_pos = pos
                    closest_lane_key = lane_key

        # ========== PHASE 3: BUILD SNAKE PATTERN WITH SMOOTH LANE TRANSITIONS ==========
        ordered_path = []
        visited_lanes = set()
        current_lane = closest_lane_key
        reverse_direction = False

        while len(visited_lanes) < len(lane_keys):
            lane_positions = lanes[current_lane][:]  # Copy to avoid modifications
            visited_lanes.add(current_lane)

            # For starting lane, begin from closest position
            if current_lane == closest_lane_key:
                try:
                    start_idx = lane_positions.index(closest_pos)
                    # Determine direction: closer to lane start or end?
                    if start_idx < len(lane_positions) / 2:
                        # Closer to start of lane, traverse forward
                        ordered_path.extend(lane_positions[start_idx:])
                        reverse_direction = True  # Next lane goes backward
                    else:
                        # Closer to end of lane, traverse backward
                        lane_positions_reversed = lane_positions[:start_idx+1]
                        lane_positions_reversed.reverse()
                        ordered_path.extend(lane_positions_reversed)
                        reverse_direction = False  # Next lane goes forward
                except ValueError:
                    ordered_path.extend(lane_positions)
                    reverse_direction = not reverse_direction
            else:
                # For subsequent lanes, alternate direction (snake pattern)
                if reverse_direction:
                    ordered_path.extend(reversed(lane_positions))
                else:
                    ordered_path.extend(lane_positions)
                reverse_direction = not reverse_direction

            # Find next lane: closest unvisited lane to current endpoint
            if ordered_path and len(visited_lanes) < len(lane_keys):
                last_pos = ordered_path[-1]

                # Find unvisited lane with nearest endpoint
                closest_next_lane = None
                closest_next_distance = float('inf')

                for lane_key in lane_keys:
                    if lane_key not in visited_lanes:
                        next_lane_positions = lanes[lane_key]

                        # Calculate distance to both ends of this lane
                        # Choose the end that is closer (will be traversal start)
                        dist_to_first = (next_lane_positions[0][0] - last_pos[0])**2 + \
                                    (next_lane_positions[0][1] - last_pos[1])**2
                        dist_to_last = (next_lane_positions[-1][0] - last_pos[0])**2 + \
                                    (next_lane_positions[-1][1] - last_pos[1])**2

                        closest_end_distance = min(dist_to_first, dist_to_last)

                        if closest_end_distance < closest_next_distance:
                            closest_next_distance = closest_end_distance
                            closest_next_lane = lane_key

                if closest_next_lane is not None:
                    current_lane = closest_next_lane
                else:
                    break

        logging.info(
            f"Snake pattern: {len(lanes)} lanes along {axis_name}-direction, "
            f"{len(ordered_path)} total positions. "
            f"Range X: {x_range:.2e} m, Range Y: {y_range:.2e} m"
        )

        return ordered_path

    def _get_z_alignment_position(self, tile_center: Tuple[float, float],
                                   fov: Tuple[float, float],
                                   corner: str = "top-left") -> Tuple[float, float]:
        """
        Calculate a safe Z-alignment position offset from the tile center based on the FoV and corner choice.

        This function computes an offset position for Z-alignment that is safely away from the tile,
        taking into account the FoV size and the desired corner for alignment.

        :param tile_center: (x, y) coordinates of the tile center in meters
        :param fov: (fov_x, fov_y) size of the field of view in meters
        :param corner: Desired corner for Z-alignment ("top-left", "top-right", "bottom-left", "bottom-right")
        :return: (x, y) coordinates for Z-alignment position in meters
        """
        # Calculate FoV offsets
        offset_x = fov[0] * 1.5
        offset_y = fov[1] * 1.5

        # Map corner names to offsets
        corner_offsets = {
            "top-left": (-offset_x, offset_y),
            "top-right": (offset_x, offset_y),
            "bottom-left": (-offset_x, -offset_y),
            "bottom-right": (offset_x, -offset_y),
        }

        if corner not in corner_offsets:
            logging.warning(f"Unknown corner '{corner}', defaulting to 'top-left'")
            corner = "top-left"

        offset = corner_offsets[corner]
        z_align_pos = (tile_center[0] + offset[0], tile_center[1] + offset[1])

        return z_align_pos

    def _filter_survey_points_away_from_tiles(self,
                                              survey_points: List[Tuple[float, float]],
                                              tiles: Dict,
                                              min_distance: float = 30.e-6) -> List[Tuple[float, float]]:
        """
        Filter survey points that are too close to tile positions and offset them away.

        When survey points are generated near tile positions, acquiring Z-alignment data
        there would interfere with or damage tiles. This function identifies survey points
        that are too close to any tile and offsets them radially outward to a safe distance.

        :param survey_points: List of (x, y) survey point tuples from Z-map generation
        :param tiles: Dict of tile positions {tile_num: (x, y), ...}
        :param min_distance: Minimum safe distance between survey and tile points in meters
                            Default: 30µm (matching grid spacing)
        :return: List of (x, y) survey points with unsafe ones removed or offset
        """
        if not tiles:
            return survey_points

        tile_positions = list(tiles.values())
        filtered_points = []

        for survey_point in survey_points:
            too_close_to_tile = False

            # Check distance to all tiles
            for tile_pos in tile_positions:
                distance = math.sqrt((survey_point[0] - tile_pos[0])**2 +
                                    (survey_point[1] - tile_pos[1])**2)

                if distance < min_distance:
                    too_close_to_tile = True

                    # Offset survey point away from this tile
                    # Calculate direction from tile to survey point
                    if distance > 0:
                        direction_x = (survey_point[0] - tile_pos[0]) / distance
                        direction_y = (survey_point[1] - tile_pos[1]) / distance
                    else:
                        # Survey point is exactly at tile position, move along arbitrary direction
                        direction_x = 1.0 / math.sqrt(2)
                        direction_y = 1.0 / math.sqrt(2)

                    # Move survey point further away
                    new_survey_point = (
                        tile_pos[0] + direction_x * min_distance,
                        tile_pos[1] + direction_y * min_distance
                    )

                    logging.info(
                        f"Survey point {survey_point} was {distance*1e6:.1f}µm from tile {tile_pos}. "
                        f"Offsetting to {new_survey_point}"
                    )

                    # Use the offset point instead (only first offset applied)
                    filtered_points.append(new_survey_point)
                    break

            if not too_close_to_tile:
                # Survey point is safe, keep it as-is
                filtered_points.append(survey_point)

        return filtered_points

    def _calculate_optimal_survey_spacing(self, tile_positions: List[Tuple[float, float]]) -> float:
        """
        Calculate optimal SPARSE survey point spacing based on tile layout.

        Strategy for INITIAL Z-MAP SURVEY:
        1. Compute average nearest-neighbor distance between tiles
        2. Use LARGER spacing than tiles (sparse survey to cover wafer efficiently)
        3. Survey points should be BETWEEN tiles, not overlapping them
        4. Goal: Get coarse Z-variation map with minimal points
        5. Tiles will fill in the fine details during acquisition

        The survey spacing should be:
        - LARGER than tile spacing (sparse coverage of wafer area)
        - Typically 1.5x to 2.5x the tile spacing
        - Ensures survey points are distributed across entire wafer
        - Avoids clustering near tile positions

        Example:
        If tiles are 100µm apart → survey points 150-250µm apart
        If tiles are 50µm apart → survey points 75-125µm apart

        :param tile_positions: List of (x, y) tuples representing tile center positions
        :return: Optimal max_dist in meters for sparse survey point generation
        """

        if not tile_positions or len(tile_positions) < 2:
            # Default fallback if insufficient tiles
            logging.warning("Insufficient tiles for optimal spacing calculation, using default 100µm")
            return 100.e-6

        tile_array = numpy.array(tile_positions)

        # ========== CALCULATE TILE SPACING STATISTICS ==========
        # For each tile, find distance to nearest neighbor
        nearest_distances = []

        for i, tile_pos in enumerate(tile_positions):
            distances_to_others = [
                numpy.sqrt((tile_pos[0] - other[0])**2 + (tile_pos[1] - other[1])**2)
                for j, other in enumerate(tile_positions) if i != j
            ]
            nearest_distances.append(min(distances_to_others))

        # Statistics on tile spacing
        avg_tile_spacing = numpy.mean(nearest_distances)
        min_tile_spacing = numpy.min(nearest_distances)
        max_tile_spacing = numpy.max(nearest_distances)

        logging.debug(
            f"Tile spacing statistics: "
            f"avg={avg_tile_spacing:.2e}m, "
            f"min={min_tile_spacing:.2e}m, "
            f"max={max_tile_spacing:.2e}m"
        )

        # ========== DETERMINE CONVEX HULL OF TILES ==========
        try:
            hull = ConvexHull(tile_positions)
            hull_vertices = tile_array[hull.vertices]

            # Calculate hull perimeter for geometry assessment
            hull_perimeter = sum(
                numpy.linalg.norm(hull_vertices[(i+1) % len(hull_vertices)] - hull_vertices[i])
                for i in range(len(hull_vertices))
            )

            logging.debug(
                f"Tile hull perimeter: {hull_perimeter:.2e}m"
            )
        except Exception as e:
            logging.debug(f"Could not compute convex hull of tiles: {e}")

        # ========== CALCULATE OPTIMAL SPARSE SURVEY SPACING ==========
        # Rule: Survey spacing should be 1.5-2.0x the average tile spacing
        # This creates a sparse grid that covers the wafer but doesn't cluster near tiles

        # Use 1.8x as a balanced multiplier (creates good sparse coverage)
        sparse_multiplier = 1.8
        optimal_spacing = avg_tile_spacing * sparse_multiplier

        # Bounds: keep between 50µm (minimum sparse) and 500µm (maximum sparse)
        optimal_spacing = max(50.e-6, min(500.e-6, optimal_spacing))

        logging.info(
            f"Calculated optimal SPARSE survey spacing: {optimal_spacing:.2e}m "
            f"({sparse_multiplier}x avg tile spacing of {avg_tile_spacing:.2e}m). "
            f"Tile spacing range: {min_tile_spacing:.2e}m to {max_tile_spacing:.2e}m. "
            f"Survey points will be between tiles, not overlapping them."
        )

        return optimal_spacing

    def _generate_triangulation_points(self, max_dist: float) -> List[Tuple[float, float]]:
        """
        Generates survey points for the Z-Map.
        - If shape is a Point or Line (Area=0): Returns the Centroid.
        - If shape is a Polygon (Area>0): Returns grid points strictly inside the polygon.
        """
        if not self.tile_map:
            return []

        coords = list(self.tile_map.values())

        # Convex Hull wraps the points.
        # It returns a Point, LineString, or Polygon.
        geom = MultiPoint(coords).convex_hull

        # CASE 1: Point or Line (Area is 0)
        # As requested: just return the midpoint.
        if geom.area == 0:
            return [(geom.centroid.x, geom.centroid.y)]

        # CASE 2: Valid Polygon (Area > 0)
        # Generate the grid strictly inside.
        minx, miny, maxx, maxy = geom.bounds

        # Use arange for fixed steps.
        x_arr = numpy.arange(minx, maxx, max_dist)
        y_arr = numpy.arange(miny, maxy, max_dist)

        valid_points = []
        for px in x_arr:
            for py in y_arr:
                # .contains is strict (points on the edge are False).
                # Use .intersects or buffer if you want edge points,
                # but usually strict inside is safer for microscope limits.
                if geom.contains(Point(px, py)):
                    valid_points.append((px, py))

        # Fallback: If the grid was too coarse and missed the polygon entirely
        # (e.g., a thin diagonal polygon between grid points), add the centroid.
        if not valid_points:
            valid_points.append((geom.centroid.x, geom.centroid.y))

        return valid_points

    def _create_z_interpolator(self, known_points, known_z):
        """
        Create a Z-height interpolator from survey points.

        Uses scipy LinearNDInterpolator for smooth interpolation within the convex hull
        of survey points, and NearestNDInterpolator as fallback for extrapolation beyond.

        :param known_points: List of (x, y) tuples representing survey point positions
        :param known_z: List of z values corresponding to each survey point
        :return: Interpolator function (x, y) -> z, or None if interpolation fails
        """
        if not known_points or not known_z or len(known_points) != len(known_z):
            logging.error(
                f"Cannot create Z interpolator: {len(known_points)} points, {len(known_z)} z values"
            )
            return None

        try:
            # Convert to numpy arrays
            points = numpy.array(known_points)
            z_values = numpy.array(known_z)

            # Create linear interpolator for smooth interpolation
            lin_interp = LinearNDInterpolator(points, z_values, fill_value=numpy.nan)

            # Create nearest-neighbor interpolator for fallback extrapolation
            nn_interp = NearestNDInterpolator(points, z_values)

            # Combined interpolator: use linear inside, nearest outside
            def interpolator(x, y):
                """Interpolate Z at given (x, y) position."""
                z_lin = lin_interp(x, y)

                # If linear interpolation returns NaN (outside convex hull),
                # fall back to nearest-neighbor extrapolation
                if numpy.isnan(z_lin):
                    z_lin = float(nn_interp(x, y))
                else:
                    z_lin = float(z_lin)

                return z_lin

            logging.info(
                f"Created Z-height interpolator from {len(known_points)} survey points"
            )
            return interpolator

        except Exception as e:
            logging.error(f"Failed to create Z interpolator: {e}")
            return None

    def _perform_z_survey_point(
        self,
        tile_pos: Dict,
    ) -> model.ProgressiveFuture:
        """
        Perform Z-height measurement at a survey point with safe movements.

        Moves safely to the survey point with configured safe Z offset,
        then acquires the Z-height through mirror alignment.

        :param tile_pos: Dict with 'x', 'y' keys for the XY position
        :return: model.ProgressiveFuture representing the survey operation
        """
        def do_move(f_survey_move, stage, tile_pos, safe_z_offset):
            """Move up by safe offset first, then to XY position"""
            f_survey_move.running_subf_z = stage.moveRel({"z": safe_z_offset})
            f_survey_move.running_subf_z.result()
            f_survey_move.running_subf_xy = stage.moveAbs(tile_pos)
            f_survey_move.running_subf_xy.result()

        def do_z_align(f_survey_move, z_align_task, opm, search_range, max_iter):
            """Perform Z alignment to measure the surface height"""
            f_survey_move.result()  # Wait for move to complete
            # opm.setPath("mirror-align").result()
            # time.sleep(5)
            # self._align_tab._ccd_stream.is_active.value = True
            # self._align_tab._ccd_stream.should_update.value = True
            try:
                z_align_task.align_mirror(search_range=search_range, max_iter=max_iter)
            except StopIteration:
                logging.debug("StopIteration raised during survey, continuing")

        def cancel_move(future):
            if hasattr(future, "running_subf_z"):
                logging.debug("Cancelling survey z move.")
                future.running_subf_z.cancel()
            if hasattr(future, "running_subf_xy"):
                logging.debug("Cancelling survey xy move.")
                future.running_subf_xy.cancel()

        main_data = self.main_app.main_data
        opm = main_data.opm
        mirror_md = main_data.mirror.getMetadata()
        min_step_size = mirror_md[model.MD_CALIB]["auto_align_min_step_size"]
        ebeam_wd_calib = mirror_md[model.MD_CALIB]["ebeam_working_distance"]
        current_ebeam_wd = main_data.ebeam_focus.position.value["z"]
        current_stage_z = main_data.stage.position.value["z"]
        wd_delta = ebeam_wd_calib - current_ebeam_wd
        good_z = current_stage_z + wd_delta

        if main_data.lens:
            focus_dist = main_data.lens.focusDistance.value
        else:
            focus_dist = 500e-6  # [m]

        z_min = good_z - focus_dist * 0.3
        z_max = good_z + focus_dist
        z_align = AlignmentAxis("z", min_step_size["z"], main_data.stage, abs_bounds=(z_min, z_max))

        futures = {}
        # Move survey position
        f_survey_move = model.ProgressiveFuture()
        futures[f_survey_move] = 5

        f_survey_move.task_canceller = cancel_move

        # Z-align task - waits for move to complete
        f_z_align = model.ProgressiveFuture()
        f_z_align.n_steps = 0
        f_z_align.current_step = 0
        z_align_task = ParabolicMirrorAlignmentTask([z_align], main_data.ccd, f_z_align, stop_early=True, save_images=True)
        f_z_align.task_canceller = z_align_task.cancel
        futures[f_z_align] = 30

        # Submit all tasks to plugin executor - they'll run sequentially
        self._executor.submitf(f_survey_move, do_move, f_survey_move, main_data.stage, tile_pos, self.SAFE_Z_OFFSET)
        self._executor.submitf(f_z_align, do_z_align, f_survey_move, z_align_task, opm, focus_dist * 0.8, 50)

        # Return a BatchFuture that tracks all operations
        future = model.ProgressiveBatchFuture(futures)
        return future

    def _acquire_single_tile(
        self,
        tile_pos: Dict,
        streams,
        tile_path,
        da_list,
        fov: Tuple[float, float] = None,
        target_z: float = None,
        enable_z_alignment: bool = False,
    ) -> model.ProgressiveFuture:
        """
        Unified tile acquisition function that handles both calibration-based Z-alignment
        and direct Z-positioning modes.

        This replaces both _custom_tiled_target_z_acquisition and _custom_tiled_acquisition,
        providing a clean, modular approach to tile acquisition with or without Z-alignment.

        When Z-alignment is enabled, the function moves to a safe corner position (offset
        from the tile center by FoV/2) to perform Z-height alignment. This prevents damage
        to the sample in the actual imaging region.

        :param tile_pos: Dict with 'x', 'y' keys for the tile XY position (center)
        :param streams: List of streams to acquire
        :param tile_path: File path to save the tile data
        :param da_list: List to accumulate acquired data arrays
        :param fov: Tuple of (width, height) field of view in meters.
                   Required when enable_z_alignment=True. Used to calculate safe corner position.
        :param target_z: Target Z position (used when enable_z_alignment=False)
        :param enable_z_alignment: If True, use mirror alignment for Z at a safe corner position;
                                   if False, move to target_z directly at tile center
        :return: model.ProgressiveFuture tracking the acquisition
        """
        def save_tile(tile_path, das):
            """Save tile data to disk"""
            exporter = dataio.find_fittest_converter(tile_path)
            logging.debug("Will save data of tile %s", tile_path)
            exporter.export(tile_path, das)

        def do_move(f_tile_move, stage, move_pos, safe_z_offset, target_z=None, is_z_align_move=False):
            """
            Safe movement: move up first by offset, then to XY, then to target Z.
            This prevents collision with the sample when moving across the wafer.

            :param f_tile_move: Future to track movement
            :param stage: Stage component
            :param move_pos: Target (x, y) position
            :param safe_z_offset: Safe Z offset to move up first
            :param target_z: Target Z position (if not z-align mode)
            :param is_z_align_move: If True, move to z_align position (corner); else to tile center
            """
            f_tile_move.running_subf_z = stage.moveRel({"z": safe_z_offset})
            f_tile_move.running_subf_z.result()
            f_tile_move.running_subf_xy = stage.moveAbs(move_pos)
            f_tile_move.running_subf_xy.result()

            # If target_z provided and z-alignment not enabled, move to target Z
            if target_z is not None and not is_z_align_move:
                f_tile_move.running_subf_target_z = stage.moveAbs({"z": target_z})
                f_tile_move.running_subf_target_z.result()

        def do_z_align(f_tile_move, z_align_task, opm, search_range, max_iter):
            """Perform Z alignment for this tile at the safe corner position"""
            f_tile_move.result()  # Wait for move to complete
            # opm.setPath("mirror-align").result()
            # time.sleep(5)
            # self._align_tab._ccd_stream.is_active.value = True
            # self._align_tab._ccd_stream.should_update.value = True
            try:
                z_align_task.align_mirror(search_range=search_range, max_iter=max_iter)
            except StopIteration:
                logging.debug("StopIteration raised during tile z-alignment")

        def do_tile_acq(f_pre_acq, tile_acq_task, tile_path, da_list):
            """Acquire tile data"""
            f_pre_acq.result()  # Wait for positioning to complete
            try:
                das, e = tile_acq_task.run()
                if e:
                    logging.warning(f"Acquisition for tile {tile_path} partially failed: {e}")
                da_list.extend(das)
                threading.Thread(target=save_tile, args=(tile_path, das)).start()
            except IndexError:
                raise IndexError(f"Failure in acquiring tile {tile_path}.")

        def cancel_move(future):
            """Cancel movement operations"""
            if hasattr(future, "running_subf_z"):
                logging.debug("Cancelling tile z move.")
                future.running_subf_z.cancel()
            if hasattr(future, "running_subf_xy"):
                logging.debug("Cancelling tile xy move.")
                future.running_subf_xy.cancel()
            if hasattr(future, "running_subf_target_z"):
                logging.debug("Cancelling tile target z move.")
                future.running_subf_target_z.cancel()

        main_data = self.main_app.main_data

        if main_data.lens:
            focus_dist = main_data.lens.focusDistance.value
        else:
            focus_dist = 500e-6  # [m]

        # Setup Z-alignment if enabled
        z_align_task = None
        z_align_axis = None
        z_align_pos = None

        if enable_z_alignment:
            if fov is None:
                logging.error("FoV required for Z-alignment mode but not provided")
                raise ValueError("FoV parameter required when enable_z_alignment=True")

            opm = main_data.opm
            mirror_md = main_data.mirror.getMetadata()
            min_step_size = mirror_md[model.MD_CALIB]["auto_align_min_step_size"]
            ebeam_wd_calib = mirror_md[model.MD_CALIB]["ebeam_working_distance"]
            current_ebeam_wd = main_data.ebeam_focus.position.value["z"]
            current_stage_z = main_data.stage.position.value["z"]
            wd_delta = ebeam_wd_calib - current_ebeam_wd
            good_z = current_stage_z + wd_delta

            z_min = good_z - focus_dist * 0.3
            z_max = good_z + focus_dist
            z_align_axis = AlignmentAxis("z", min_step_size["z"], main_data.stage, abs_bounds=(z_min, z_max))

            # Calculate safe Z-alignment position at tile corner
            z_align_pos = self._get_z_alignment_position(
                (tile_pos["x"], tile_pos["y"]),
                fov,
                corner="top-left"
            )
            logging.debug(f"Tile center: {tile_pos}, Z-align corner position: {z_align_pos}")

        futures = {}

        # Step 1: Position movement
        f_tile_move = model.ProgressiveFuture()
        futures[f_tile_move] = 5

        f_tile_move.task_canceller = cancel_move

        # Determine what precedes tile acquisition
        if enable_z_alignment:
            # Step 2a: Z-alignment at safe corner position
            f_z_align = model.ProgressiveFuture()
            f_z_align.n_steps = 0
            f_z_align.current_step = 0
            z_align_task = ParabolicMirrorAlignmentTask([z_align_axis], main_data.ccd, f_z_align,
                                                         stop_early=True, save_images=True)
            f_z_align.task_canceller = z_align_task.cancel
            futures[f_z_align] = 30

            # Step 3: Tile acquisition (waits for z-align, moves to tile center for imaging)
            f_tile_acq = model.ProgressiveFuture()
            tile_acq_task = AcquisitionTask(streams, f_tile_acq, main_data.settings_obs)
            f_tile_acq.task_canceller = tile_acq_task.cancel
            futures[f_tile_acq] = 5

            # Move to Z-align corner position instead of tile center
            move_to_pos = {"x": z_align_pos[0], "y": z_align_pos[1]}

            # Submit tasks in order:
            # 1. Move to z-align corner position
            self._executor.submitf(f_tile_move, do_move, f_tile_move, main_data.stage,
                                  move_to_pos, self.SAFE_Z_OFFSET, is_z_align_move=True)
            # 2. Perform Z-alignment at corner
            self._executor.submitf(f_z_align, do_z_align, f_tile_move, z_align_task, main_data.opm,
                                   70e-6, 50)
            # TODO: 3. Move back to tile center for acquisition (current behavior: acquire from corner)
            # For now, acquire from corner position. In future, move to tile center before acquisition.
            # Movement to tile center would require additional future chaining.
            self._executor.submitf(f_tile_acq, do_tile_acq, f_z_align, tile_acq_task, tile_path, da_list)
        else:
            # Direct positioning without z-alignment
            f_tile_acq = model.ProgressiveFuture()
            tile_acq_task = AcquisitionTask(streams, f_tile_acq, main_data.settings_obs)
            f_tile_acq.task_canceller = tile_acq_task.cancel
            futures[f_tile_acq] = 5

            # Submit tasks
            self._executor.submitf(f_tile_move, do_move, f_tile_move, main_data.stage, tile_pos,
                                   self.SAFE_Z_OFFSET, target_z)
            self._executor.submitf(f_tile_acq, do_tile_acq, f_tile_move, tile_acq_task, tile_path, da_list)

        # Return a BatchFuture that tracks all operations
        future = model.ProgressiveBatchFuture(futures)
        return future

    def acquire(self, dlg):
        main_data = self.main_app.main_data
        str_ctrl = self._tab.streambar_controller
        str_ctrl.pauseStreams()
        dlg.pauseSettings()
        self._unsubscribe_vas()

        fn = self.filename.value
        log_dir = os.path.dirname(fn)
        fn_bs, fn_ext = udataio.splitext(fn)
        orig_hw_values: Dict[model.VigilantAttribute, Any] = {}  # VA -> value
        orig_pos = main_data.stage.position.value

        try:
            ss = []
            overlay_stream = None
            stitch_ss = self._get_stitch_streams()
            ss.extend(stitch_ss)
            if self.fineAlign.value and self._can_fine_align(stitch_ss):
                ss.append(self._ovrl_stream)
                overlay_stream = self._ovrl_stream

            if not ss:
                logging.warning("No stream available for tiled acquisition.")
                dlg.resumeSettings()
                return

            # Force external to all streams with emitters
            for s in ss:
                if (s.emitter
                    and model.hasVA(s.emitter, "external")
                    and s.emitter.external.value is None
                   ):
                    orig_hw_values[s.emitter.external] = s.emitter.external.value
                    s.emitter.external.value = True

            # Start the tiled acquisition task
            if self.tile_mode.value == TileMode.CUSTOM.name:
                if not self.tile_map:
                    logging.warning("No tile positions loaded for tiled acquisition.")
                    dlg.resumeSettings()
                    return

                da_list = []
                if self.z_map.value:
                    # ========== IMPROVED Z-MAP INTERPOLATION WITH SMART PATH PLANNING ==========
                    # Strategy: Find closest survey point to current position and traverse
                    # survey points using nearest-neighbor approach to avoid large Z jumps

                    known_points = [] # (x, y)
                    known_z = []      # z
                    max_dist = self._calculate_optimal_survey_spacing(list(self.tile_map.values()))
                    survey_points = self._generate_triangulation_points(max_dist=max_dist)

                    # Get FoV for safe Z-alignment positioning (will be used for survey points)
                    try:
                        fov = self._guess_smallest_fov()
                    except ValueError:
                        logging.error("Cannot determine FoV for Z-alignment positioning. Aborting.")
                        dlg.resumeSettings()
                        return

                    if not survey_points:
                        logging.error("No survey points generated. Aborting.")
                        dlg.resumeSettings()
                        return

                    # Filter out survey points that are too close to tile positions
                    # This prevents Z-alignment from interfering with actual tile acquisition
                    survey_points = self._filter_survey_points_away_from_tiles(
                        survey_points,
                        self.tile_map,
                        min_distance=max(fov) * 2
                    )

                    if not survey_points:
                        logging.error("No valid survey points after filtering. Aborting.")
                        dlg.resumeSettings()
                        return

                    # Get current position for intelligent path planning
                    current_xy = (main_data.stage.position.value["x"],
                                  main_data.stage.position.value["y"])

                    # Order survey points using nearest-neighbor to minimize travel distance
                    ordered_survey_points = self._order_positions_nearest_neighbor(
                        survey_points, current_xy
                    )

                    logging.info(f"Measuring {len(ordered_survey_points)} survey points in optimized order")

                    # Measure Z at each survey point in smart order
                    for i, survey_point in enumerate(ordered_survey_points, start=1):
                        self._dlg.setAcquisitionInfo(
                            f"Measuring survey point {i}/{len(ordered_survey_points)} "
                            f"at position {survey_point}"
                        )
                        ft = self._perform_z_survey_point({"x": survey_point[0], "y": survey_point[1]})
                        dlg.showProgress(ft)
                        ft.result()

                        measured_z = main_data.stage.position.value["z"]
                        known_z.append(measured_z)
                        known_points.append(survey_point)

                        logging.debug(f"Survey point {i}: pos={survey_point}, z={measured_z:.6e}")

                    # Create interpolator from the surveyed points
                    z_interpolator = self._create_z_interpolator(known_points, known_z)

                    if z_interpolator is None:
                        logging.error("Z-Map generation failed (no valid points). Aborting.")
                        dlg.resumeSettings()
                        return

                    main_data.stage.moveRel({"z": 300e-6}).result()  # Move away from sample before moving to tiles

                    for acq_idx, (tile_num, tile_pos) in enumerate(self.tile_map, start=1):
                        self._dlg.setAcquisitionInfo(
                            f"Acquiring tile {acq_idx}/{len(self.tile_map)}\n"
                            f"(Tile #{tile_num}) at ({tile_pos}"
                        )

                        fn_tile = "%s-%d-%.3f-%.3f%s" % (fn_bs, tile_num, tile_pos[0] * 1e6,
                                                         tile_pos[1] * 1e6, fn_ext)
                        tile_path = os.path.join(log_dir, fn_tile)

                        # Interpolate Z height at this tile position
                        target_z = float(z_interpolator(tile_pos[0], tile_pos[1]))

                        logging.debug(f"Tile {tile_num}: pos={tile_pos}, target_z={target_z:.6e}")

                        # Acquire tile using unified function (without z-alignment)
                        ft = self._acquire_single_tile(
                            {"x": tile_pos[0], "y": tile_pos[1]},
                            stitch_ss,
                            tile_path,
                            da_list,
                            fov=fov,
                            target_z=target_z,
                            enable_z_alignment=False,
                        )

                        # Wait for this tile to complete before moving to next
                        dlg.showProgress(ft)
                        ft.result()
                else:
                    # Original approach: acquire tiles with Z-alignment per tile
                    logging.info(f"Acquiring {len(self.tile_map)} tiles with per-tile Z-alignment")

                    for tile_num, tile_pos in self.tile_map.items():
                        self._dlg.setAcquisitionInfo(
                            f"Acquiring tile {tile_num} at position {tile_pos}"
                        )

                        fn_tile = "%s-%d-%.3f-%.3f%s" % (fn_bs, tile_num, tile_pos[0] * 1e6,
                                                         tile_pos[1] * 1e6, fn_ext)
                        tile_path = os.path.join(log_dir, fn_tile)

                        # Get FoV for safe Z-alignment positioning at tile corners
                        try:
                            fov_align = self._guess_smallest_fov()
                        except ValueError:
                            logging.error("Cannot determine FoV for Z-alignment positioning. Aborting.")
                            dlg.resumeSettings()
                            return

                        # Acquire tile using unified function (with z-alignment at safe corner)
                        ft = self._acquire_single_tile(
                            {"x": tile_pos[0], "y": tile_pos[1]},
                            stitch_ss,
                            tile_path,
                            da_list,
                            fov=fov_align,
                            enable_z_alignment=True,
                        )

                        # Wait for this tile to complete before moving to next
                        dlg.showProgress(ft)
                        ft.result()

                # Stitching
                st_data = []
                if self.stitch.value and da_list:
                    st_data = TiledAcquisitionTask.stitchTiles(da_list, self.registrar, self.weaver.value)
            else:
                # Start the tiled acquisition task
                region = self._get_region(orig_pos)
                ft = acquireTiledArea(
                    stitch_ss,
                    main_data.stage,
                    region,
                    overlap=self.overlap.value,
                    settings_obs=main_data.settings_obs,
                    log_path=fn,
                    weaver=self.weaver.value if self.stitch.value else None,
                    registrar=self.registrar if self.stitch.value else None,
                    overlay_stream=overlay_stream,
                    sfov=self._guess_smallest_fov(),
                    batch_acquire_streams=True,
                )

                dlg.showProgress(ft)

                # Wait for the acquisition and stitching to complete
                st_data = ft.result()

            dlg.Close()

            # Open analysis tab
            if st_data:
                exporter = dataio.find_fittest_converter(fn)
                if exporter.CAN_SAVE_PYRAMID:
                    exporter.export(fn, st_data, pyramid=True)
                else:
                    logging.warning("File format doesn't support saving image in pyramidal form")
                    exporter.export(fn, st_data)

                popup.show_message(self.main_app.main_frame, "Tiled acquisition complete",
                                   "Will display stitched image")
                self.showAcquisition(fn)
            else:
                popup.show_message(self.main_app.main_frame, "Tiled acquisition complete",
                                   "Will display last tile")
                files = []
                for f in os.listdir(log_dir):
                    if f.startswith(os.path.basename(fn_bs)) and f.endswith(fn_ext):
                        files.append(os.path.join(log_dir, f))
                if files:
                    last_tile_fn = max(files, key=os.path.getctime)
                    # It's easier to know the last filename, and it's also the most
                    # interesting for the user, as if something went wrong (eg, focus)
                    # it's the tile the most likely to show it.
                    self.showAcquisition(last_tile_fn)

            # TODO: also export a full image (based on reported position, or based
            # on alignment detection)
        except CancelledError:
            logging.debug("Acquisition cancelled")
            dlg.resumeSettings()
        except Exception as ex:
            logging.exception("Acquisition failed.")
            # Show also in the window. It will be hidden next time a setting is changed.
            self._dlg.setAcquisitionInfo("Acquisition failed: %s" % (ex,),
                                         lvl=logging.ERROR)
        finally:
            logging.info("Tiled acquisition ended")
            main_data.stage.moveAbs(orig_pos)
            # reset all external values
            for va, value in orig_hw_values.items():
                try:
                    va.value = value
                except Exception:
                    logging.exception("Failed to restore VA %s to %s", va, value)
