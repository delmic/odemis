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
from odemis.acq.stream import Stream, SEMStream, CameraStream, DataProjection
import odemis.gui
from odemis.gui.conf import get_acqui_conf
from odemis.gui.plugin import Plugin, AcquisitionDialog
from odemis.util import img, TimeoutError
from odemis.util import dataio as udataio
import os
import time


class TileAcqPlugin(Plugin):
    name = "Tile acquisition"
    __version__ = "1.0"
    __author__ = u"Éric Piel"
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
        ("expectedDuration", {
        }),
        ("totalArea", {
            "tooltip": "Approximate area covered by all the streams"
        }),
    ))

    def __init__(self, microscope, main_app):
        super(TileAcqPlugin, self).__init__(microscope, main_app)
        # Can only be used with a microscope
        if not microscope:
            return
        else:
            # Check if microscope supports tiling (= has a sample stage)
            main_data = self.main_app.main_data
            if main_data.stage:
                self.addMenu("Acquisition/Tile...", self.show_dlg)

        self.nx = model.IntContinuous(5, (1, 1000))
        self.ny = model.IntContinuous(5, (1, 1000))
        self.overlap = model.FloatContinuous(20, (1, 80), unit="%")
        self.filename = model.StringVA("a.ome.tiff")
        self.expectedDuration = model.VigilantAttribute(1, unit="s", readonly=True)
        self.totalArea = model.TupleVA((1, 1), unit="m", readonly=True)

        self.nx.subscribe(self._update_exp_dur)
        self.ny.subscribe(self._update_exp_dur)

        self.nx.subscribe(self._update_total_area)
        self.ny.subscribe(self._update_total_area)
        self.overlap.subscribe(self._update_total_area)

        self._dlg = None

    def _get_streams(self):
        """
        Returns the streams set as visible in the acquisition dialog
        """
        if not self._dlg:
            return []
        ss = self._dlg.microscope_view.getStreams()
        logging.debug("View has %d streams", len(ss))
        return [s.stream if isinstance(s, DataProjection) else s for s in ss]

    def _get_new_filename(self):
        conf = get_acqui_conf()
        return os.path.join(
            conf.last_path,
            u"%s%s" % (time.strftime("%Y%m%d-%H%M%S"), conf.last_extension)
        )

    def _update_exp_dur(self, _=None):
        """
        Called when VA that affects the expected duration is changed
        """
        at = acq.estimateTime(self._get_streams())

        # 0.5 s for move between tile
        tat = (at + 0.5) * (self.nx.value * self.ny.value)

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

    def _guess_smallest_fov(self):
        """
        Return (float, float): smallest width and smallest height of all the FoV
          Note: they are not necessarily from the same FoV.
        raise ValueError: If no stream selected
        """
        fovs = [self._get_fov(s) for s in self._get_streams()]
        if not fovs:
            raise ValueError("No stream so no FoV, so no minimum one")

        return (min(f[0] for f in fovs),
                min(f[1] for f in fovs))

    def show_dlg(self):
        # TODO: only accept to run if in "secom_live" or "sparc_acqui" tab
        # (iow, has a streambar_controller)
        # TODO: support SPARC by using "sparc_acqui"
        # TODO: if there is a chamber, only allow if there is vacuum

        tab = self.main_app.main_data.getTabByName("secom_live")
        ss = tab.tab_data_model.streams.value

        self.filename.value = self._get_new_filename()

        dlg = AcquisitionDialog(self, "Tiled acquisition",
                                "Acquire a large area by acquiring the streams multiple "
                                "times over a grid.")
        self._dlg = dlg
        dlg.addSettings(self, self.vaconf)
        for s in ss:
            dlg.addStream(s)
        dlg.addButton("Cancel")
        dlg.addButton("Acquire", self.acquire, face_colour='blue')

        # Update acq time and area when streams are added/removed
        dlg.microscope_view.stream_tree.flat.subscribe(self._update_exp_dur, init=True)
        dlg.microscope_view.stream_tree.flat.subscribe(self._update_total_area, init=True)
        # TODO: also update when settings change

        # TODO: disable "acquire" button if no stream selected.

        ans = dlg.ShowModal()
        if ans == 0:
            logging.info("Tiled acquisition cancelled")
        elif ans == 1:
            logging.info("Tiled acquisition completed")
        else:
            logging.warning("Got unknown return code %s", ans)

        # Don't hold references
        self._dlg = None

    def _generate_scanning_indices(self, rep):
        """
        Generate the explicit X/Y position of each tile, in the scanning order
        rep (int, int): X, Y number of tiles
        return (generator of tuple(int, int)): x/y positions, starting from 0,0
        """
        # For now we do forward/backward on X, and Y slowly
        dir = 1
        for iy in range(rep[1]):
            if dir == 1:
                for ix in range(rep[0]):
                    yield (ix, iy)
            else:
                for ix in range(rep[0] - 1, -1, -1):
                    yield (ix, iy)

            dir *= -1

    def _move_to_tile(self, idx, orig_pos, tile_size):
        # Go left/down, with every second line backward:
        # similar to writing/scanning convention, but move of just one unit
        # every time.
        # A-->-->-->--v
        #             |
        # v--<--<--<---
        # |
        # --->-->-->--Z
        overlap = 1 - self.overlap.value / 100
        tile_pos = (orig_pos["x"] + idx[0] * tile_size[0] * overlap,
                    orig_pos["y"] - idx[1] * tile_size[1] * overlap)

        logging.debug("Moving to tile %s at %s m", idx, tile_pos)
        f = self.main_app.main_data.stage.moveAbs({"x": tile_pos[0], "y": tile_pos[1]})
        try:
            f.result(10)
        except TimeoutError:
            logging.warning("Failed to move to tile %s", idx)
            f.cancel()
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
            else:
                raise TypeError("Unsupported Stream")
        else:
            raise TypeError("Unsupported object")

    def acquire(self, dlg):
        main_data = self.main_app.main_data
        str_ctrl = main_data.tab.value.streambar_controller
        stream_paused = str_ctrl.pauseStreams()

        orig_pos = main_data.stage.position.value
        trep = (self.nx.value, self.ny.value)
        nb = trep[0] * trep[1]
        # It's not a big deal if it was a bad guess as we'll use the actual data
        # before the first move
        sfov = self._guess_smallest_fov()
        fn = self.filename.value
        bs, ext = udataio.splitext(fn)
        fn_tile_pat = bs + "-%.5dx%.5d" + ext

        exporter = dataio.find_fittest_converter(fn_tile_pat)

        ss = self._get_streams()
        acqt = acq.estimateTime(ss)
        end = time.time() + (acqt + 0.5) * nb  # same formula as in _update_exp_dur()
        ft = model.ProgressiveFuture(end=end)
        ft.task_canceller = lambda l: True  # To allow cancelling while it's running
        ft.set_running_or_notify_cancel()  # Indicate the work is starting now
        dlg.showProgress(ft)

        i = 0
        try:
            for ix, iy in self._generate_scanning_indices(trep):
                # Update the progress bar
                left = nb - i
                dur = (acqt + 0.5) * left
                ft.set_progress(end=time.time() + dur)

                self._move_to_tile((ix, iy), orig_pos, sfov)

                dur -= 0.5
                ft.set_progress(end=time.time() + dur)
                fa = acq.acquire(ss)
                das, e = fa.result()  # blocks until all the acquisitions are finished
                if e:
                    logging.warning("Acquisition for tile %dx%d partially failed: %s",
                                    ix, iy, e)

                if ft.cancelled():
                    logging.debug("Acquisition cancelled")
                    return

                # TODO: do in a separate thread
                fn_tile = fn_tile_pat % (ix, iy)
                logging.debug("Will save data of tile %dx%d to %s", ix, iy, fn_tile)
                exporter.export(fn_tile, das)

                if ft.cancelled():
                    logging.debug("Acquisition cancelled")
                    return

                # Check the FoV is correct using the data, and if not update
                if i == 0:
                    afovs = [self._get_fov(d) for d in das]
                    asfov = (min(f[0] for f in afovs),
                             min(f[1] for f in afovs))
                    if not all(util.almost_equal(e, a) for e, a in zip(sfov, asfov)):
                        logging.warning("Unexpected min FoV = %s, instead of %s", asfov, sfov)
                        sfov = asfov

                i += 1

            ft.set_result(None)  # Indicate it's over

            # End of the (completed) acquisition
            if not ft.cancelled():
                dlg.Destroy()

            # TODO: also export a full image (based on reported position, or based
            # on alignment detection)
        finally:
            logging.info("Tiled acquisition ended")
            main_data.stage.moveAbs(orig_pos)

