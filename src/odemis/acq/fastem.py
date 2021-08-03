#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 19 Apr 2021

Copyright © 2021 Philip Winkler, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""
import logging
import math
from odemis import model, util
from odemis.acq import stitching
from odemis.acq.stitching import REGISTER_IDENTITY
from odemis.acq.stream import SEMStream
import time


# The executor is a single object, independent of how many times the module is loaded.
_executor = model.CancellableThreadPoolExecutor(max_workers=1)


class FastEMROA(object):
    """ Representation of a FastEM ROA (region of acquisition). """

    def __init__(self, name, coordinates, roc):
        """
        :param name: (str) name of the ROA
        :param coordinates: (float, float, float, float) l, t, r, b coordinates in m
        :param roc: (FastEMROC) corresponding region of calibration
        """
        self.name = model.StringVA(name)
        self.coordinates = model.TupleContinuous(coordinates,
                                                 range=((-1, -1, -1, -1), (1, 1, 1, 1)),
                                                 cls=(int, float),
                                                 unit='m')
        self.roc = model.VigilantAttribute(roc)


class FastEMROC(object):
    """ Representation of a FastEM ROC (region of calibration). """

    def __init__(self, name, coordinates):
        """
        :param name: (str) name of the ROC
        :param coordinates: (float, float, float, float) l, t, r, b coordinates in m
        """
        self.name = model.StringVA(name)
        self.coordinates = model.TupleContinuous(coordinates,
                                                 range=((-1, -1, -1, -1), (1, 1, 1, 1)),
                                                 cls=(int, float),
                                                 unit='m')
        self.parameters = None  # calibration object with all relevant parameters


# TODO: replace fake testing functions with actual acquisition
def acquire(roa, path):
    """
    :param roa: (FastEMROA) acquisition region to be acquired
    :param path: (str) path and filename of the acquisition on the server
    :returns: (ProgressiveFuture): acquisition future
    """
    # TODO: pass path through attribute on ROA instead of second argument?
    f = model.ProgressiveFuture()
    _executor.submitf(f, _run_fake_acquisition)
    return f


def _run_fake_acquisition():
    time.sleep(2)


def estimateTime(roas):
    return len(roas) * 2


# Overview acquisition

# Fixed settings
# We use a "legacy" resolution in XT, because has the advantage of having a more
# square aspect ratio, compared to new resolutions like  1536 x 1024.
TILE_RES = (1024, 884)  # px
# Maximum FoV without seeing the pole-piece (with T1, immersion off).
# Possibly, using the ETD could allow a slightly wider FoV.
TILE_FOV_X = 1.5e-3  # m

STAGE_PRECISION = 1e-6  # m, how far the stage may go from the requested position

# Observed time it takes to acquire a tile with 1µs dwell time, for time estimation
TIME_PER_TILE_1US = 4.7  # s/tile


def acquireTiledArea(stream, stage, area, live_stream=None):
    """
    :param stream: (SEMStream) Stream used for the acquisition.
     It must have the detector and emitter connected to the TFS XT client detector and scanner.
     It should be in focus.
     It must NOT have the following local VAs: horizontalFoV. resolution, scale
      (because the VAs of the hardware will be changed directly, and so they shouldn’t be changed by the stream).
    :param stage: (Actuator). It should have axes "x" and "y", which should already be referenced.
    :param area: (float, float, float, float) minx, miny, maxx, maxy:  coordinates of the overview region
    :param live_stream: (StaticStream or None): StaticStream to be updated with
       each tile acquired, to build up live the whole acquisition. NOT SUPPORTED YET.
    : return: (ProgressiveFuture), acquisition future. It returns the complete DataArray.
    """
    # Check the parameters
    if len(area) != 4:
        raise ValueError("area should be 4 float, but got %r" % (area,))

    for vaname in ("horizontalFoV", "resolution", "scale"):
        if vaname in stream.emt_vas:
            raise ValueError("Stream shouldn't have its own VA %s" % (vaname,))

    if set(stage.axes) < {"x", "y"}:
        raise ValueError("Stage needs axes x and y, but has %s" % (stage.axes.keys(),))
    if model.hasVA(stage, "referenced"):
        refd = stage.referenced.value
        for a in ("x", "y"):
            if a in refd:
                if not refd[a]:
                    raise ValueError("Stage axis '%s' is not referenced. Reference it first" % (a,))
            else:
                logging.warning("Going to use the stage in absolute mode, but it doesn't report %s in .referenced VA", a)

    else:
        logging.warning("Going to use the stage in absolute mode, but it doesn't have .referenced VA")

    if live_stream:
        raise NotImplementedError("live_stream not supported")

    # FIXME: if stream has .focuser, the acquireTiledArea will try to refocus from time to time
    # => temporarily remove it? Or duplicate the stream? Or pass an argument to
    # acquireTiledArea to disable the autofocusing.
    # FIXME: if the stream already has an image, it will be used to compute the FoV,
    # which might be completely wrong. => provide a .guessFoV() on the LiveStream
    # to guess the FoV on the next image.
    # FIXME: if the stream has .horizontalFoV, it's automatically set to the biggest
    # value, which do not work.

    # To avoid the focuser, and horizontalFoV, and the already present data, we
    # just create our own SEMStream
    sem_stream = SEMStream(stream.name.value + " copy", stream.detector, stream.detector.data, stream.emitter)

    est_dur = estimateTiledAcquisitionTime(sem_stream, stage, area)
    f = model.ProgressiveFuture(start=time.time(), end=time.time() + est_dur)
    _executor.submitf(f, _run_overview_acquisition, f, sem_stream, stage, area, live_stream)

    return f


def estimateTiledAcquisitionTime(stream, stage, area):
    # TODO: fix function to limit the acquisition area so that the FoV is taken into account.
    # t_estim = estimateTiledAcquisitionTime(stream, stage, area, overlap=0)

    # For now, it's just simpler to hard-code the time spent per tile, and derive the total time based on it.
    fov = (TILE_FOV_X, TILE_FOV_X * TILE_RES[1] / TILE_RES[0])
    normalized_area = util.normalize_rect(area)
    area_size = (normalized_area[2] - normalized_area[0],
                 normalized_area[3] - normalized_area[1])
    nx = math.ceil(abs(area_size[0] / fov[0]))
    ny = math.ceil(abs(area_size[1] / fov[1]))

    # TODO: compensate for longer dwell times => should be a A+Bx formula?
    return nx * ny * TIME_PER_TILE_1US  # s


def _configure_overview_hw(scanner):
    """
    Set good parameters an overview acquisition
    """
    # Typically, all these settings should have already been set, but better be safe.
    scanner.multiBeamMode.value = False
    scanner.external.value = False
    scanner.blanker.value = None  # Automatic
    # Disable immersion, to get a larger field of view.
    # It needs to be done before changing the horizontalFoV, as the range is updated
    scanner.immersion.value = False

    scanner.horizontalFoV.value = TILE_FOV_X  # m

    # => compute the scale needed in X, use the same one in Y, and then compute the Y resolution.
    # => set resolution to full FoV, and then adjust
    scale = scanner.shape[0] / TILE_RES[0]
    scanner.scale.value = (scale, scale)
    if scanner.resolution.value != TILE_RES:
        logging.warning("Unexpected resolution %s on e-beam scanner, expected %s",
                        scanner.resolution.value, TILE_RES)


def _run_overview_acquisition(f, stream, stage, area, live_stream):
    """
    :returns: (DataArray)
    """
    _configure_overview_hw(stream.emitter)

    # The stage movement precision is quite good (just a few pixels). The stage's
    # position reading is much better, and we can assume it's below a pixel.
    # So as long as we are sure there is some overlap, the tiles will be positioned
    # correctly and without gap.
    overlap = STAGE_PRECISION / stream.emitter.horizontalFoV.value
    logging.debug("Overlap is %s%%", overlap * 100)  # normally < 1%

    def _pass_future_progress(sub_f, start, end):
        f.set_progress(start, end)

    # Note, for debugging, it's possible to keep the intermediary tiles with log_path="./tile.ome.tiff"
    sf = stitching.acquireTiledArea([stream], stage, area, overlap, registrar=REGISTER_IDENTITY)
    # Connect the progress of the underlying future to the main future
    sf.add_update_callback(_pass_future_progress)
    das = sf.result()

    if len(das) != 1:
        logging.warning("Expected 1 DataArray, but got %d: %r", len(das), das)
    return das[0]
