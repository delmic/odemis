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

import logging
import math
import os
import time
from collections import OrderedDict
from concurrent.futures._base import CancelledError
from typing import Any, Dict, Tuple

import wx

import odemis.gui
from odemis import dataio, model
from odemis.acq import stream
from odemis.acq.stitching import (
    REGISTER_GLOBAL_SHIFT,
    WEAVER_COLLAGE_REVERSE,
    WEAVER_MEAN,
    acquireTiledArea,
    estimateTiledAcquisitionMemory,
    estimateTiledAcquisitionTime,
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
from odemis.gui.conf import get_acqui_conf
from odemis.gui.plugin import AcquisitionDialog, Plugin
from odemis.gui.util import call_in_wx_main, formats_to_wildcards
from odemis.util import dataio as udataio


class TileAcqPlugin(Plugin):
    name = "Tile acquisition"
    __version__ = "1.8"
    __author__ = "Éric Piel, Philip Winkler"
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

        # Select weaving method
        # On a Sparc system the mean weaver gives the best result since it smoothes the
        # transitions between tiles. However, using this weaver on the Secom/Delphi
        # generates an image with dark stripes in the overlap regions which are the
        # result of carbon decomposition effects that typically occur in samples imaged
        # by these systems. To mediate this, we use the collage_reverse weaver that
        # only shows the overlap region of the tile that was imaged first.
        if self.microscope.role in ("secom", "delphi"):
            self.weaver = WEAVER_COLLAGE_REVERSE
            logging.info("Using weaving method WEAVER_COLLAGE_REVERSE.")
        else:
            self.weaver = WEAVER_MEAN
            logging.info("Using weaving method WEAVER_MEAN.")

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
            "%s%s" % (time.strftime("%Y%m%d-%H%M%S"), conf.last_extension)
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
        try:
            stitch_ss = self._get_stitch_streams()
            if not stitch_ss:
                self.expectedDuration._set_value(1, force_write=True)
                return

            # Calculate bounding box of the acquisition region
            region = self._get_region(self.main_app.main_data.stage.position.value)
            if region is None:
                return

            overlap_frac = self.overlap.value / 100
            overlay_stream = None
            if self.fineAlign.value and self._can_fine_align(stitch_ss):
                overlay_stream = self._ovrl_stream

            tat = estimateTiledAcquisitionTime(
                stitch_ss,
                self.main_app.main_data.stage,
                region,
                overlap=overlap_frac,
                settings_obs=self.main_app.main_data.settings_obs,
                weaver=self.weaver if self.stitch.value else None,
                registrar=REGISTER_GLOBAL_SHIFT if self.stitch.value else None,
                overlay_stream=overlay_stream,
                sfov=self._guess_smallest_fov(),
            )
        except (ValueError, AttributeError):
            # No streams or cannot compute FoV
            tat = 1

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

        return TiledAcquisitionTask.guessSmallestFov(ss)

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

            overlap_frac = self.overlap.value / 100
            overlay_stream = None
            if self.fineAlign.value and self._can_fine_align(stitch_ss):
                overlay_stream = self._ovrl_stream

            mem_sufficient, mem_est = estimateTiledAcquisitionMemory(
                stitch_ss,
                self.main_app.main_data.stage,
                region,
                overlap=overlap_frac,
                settings_obs=self.main_app.main_data.settings_obs,
                weaver=self.weaver if self.stitch.value else None,
                registrar=REGISTER_GLOBAL_SHIFT if self.stitch.value else None,
                overlay_stream=overlay_stream,
                sfov=self._guess_smallest_fov(),
            )
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

        overlap_frac = self.overlap.value / 100.0

        # Reliable FoV
        # The size of the smallest tile, non-including the overlap, which will be
        # lost (and also indirectly represents the precision of the stage)
        reliable_fov = ((1 - overlap_frac) * sfov[0], (1 - overlap_frac) * sfov[1])

        xmin = start_pos["x"] - reliable_fov[0] / 2
        ymax = start_pos["y"] + reliable_fov[1] / 2
        xmax = xmin + self.totalArea.value[0]
        ymin = ymax - self.totalArea.value[1]

        return (xmin, ymin, xmax, ymax)

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
            overlap_frac = self.overlap.value / 100
            stitch_ss = self._get_stitch_streams()
            ss += stitch_ss
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
            region = self._get_region(orig_pos)
            ft = acquireTiledArea(
                stitch_ss,
                main_data.stage,
                region,
                overlap=overlap_frac,
                settings_obs=main_data.settings_obs,
                log_path=fn,
                weaver=self.weaver if self.stitch.value else None,
                registrar=REGISTER_GLOBAL_SHIFT if self.stitch.value else None,
                overlay_stream=overlay_stream,
                sfov=self._guess_smallest_fov(),
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
