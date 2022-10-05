# -*- coding: utf-8 -*-
"""
Created on 27 Nov 2013

@author: Éric Piel

Copyright © 2013-2020 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

# Everything related to high-level image acquisition on the microscope.


from collections import OrderedDict
from collections.abc import Iterable
from concurrent.futures import CancelledError
import logging
import threading

from odemis import model
from odemis.acq import _futures
from odemis.acq.stream import FluoStream, SEMCCDMDStream, SEMMDStream, SEMTemporalMDStream, \
    OverlayStream, OpticalStream, EMStream, ScannedFluoStream, ScannedFluoMDStream, \
    ScannedRemoteTCStream, ScannedTCSettingsStream
from odemis.util import img, fluo, executeAsyncTask
import time
import copy
from odemis.model import prepare_to_listen_to_more_vas
from concurrent.futures._base import CANCELLED, FINISHED, RUNNING
from odemis.util.driver import guessActuatorMoveDuration 
from odemis.util.img import assembleZCube

# This is the "manager" of an acquisition. The basic idea is that you give it
# a list of streams to acquire, and it will acquire them in the best way in the
# background. You are in charge of ensuring that no other acquisition is
# going on at the same time.
# The manager receives a list of streams to acquire, order them in the best way,
# and then creates a separate thread to run the acquisition of each stream. It
# returns a special "ProgressiveFuture" which is a Future object that can be
# stopped while already running, and reports from time to time progress on its
# execution.
def acquire(streams, settings_obs=None):
    """ Start an acquisition task for the given streams.

    It will decide in which order the stream must be acquired.

    ..Note:
        It is highly recommended to not have any other acquisition going on.

    :param streams: [Stream] the streams to acquire
    :param settings_obs: [SettingsObserver or None] class that contains a list of all VAs
        that should be saved as metadata
    :return: (ProgressiveFuture) an object that represents the task, allow to
        know how much time before it is over and to cancel it. It also permits
        to receive the result of the task, which is a tuple:
            (list of model.DataArray): the raw acquisition data
            (Exception or None): exception raised during the acquisition
    """

    # create a future
    future = model.ProgressiveFuture()

    # create a task
    task = AcquisitionTask(streams, future, settings_obs)
    future.task_canceller = task.cancel # let the future cancel the task

    # connect the future to the task and run in a thread
    executeAsyncTask(future, task.run)

    # return the interface to manipulate the task
    return future


def acquireZStack(streams, zlevels, settings_obs=None):
    """
    The acquisition manager of a zstack
    streams (list of Stream): the streams to be acquired
    zlevels (dict Stream -> list of floats): a dictionary containing the streams and the
    corresponding lists of actuator z positions
    settings_obs (SettingsObserver): VAs to be integrated in the acquired data as metadata
    return (ProgressiveFuture): the future that will be executing the task
    """
    # create future
    future = model.ProgressiveFuture()
    # create acquisition task
    acqui_task = ZStackAcquisitionTask(future, streams, zlevels, settings_obs)
    # add the ability of cancelling the future during execution
    future.task_canceller = acqui_task.cancel

    # set the progress of the future
    total_duration = acqui_task.estimate_total_duration()
    future.set_end_time(time.time() + total_duration)

    # assign the acquisition task to the future
    executeAsyncTask(future, acqui_task.run)

    return future


class ZStackAcquisitionTask(object):
    """
    This class represents an acquisition task for a zstack of data.
    """

    def __init__(self, future, streams, zlevels, settings_obs):
        """
        The class constructor
        future (ProgressiveFuture): the future that will execute the task asynchronously
        streams (list of Stream): the streams to be acquired
        zlevels (dict Stream -> list of floats): a dictionary containing the streams and the
        corresponding lists of actuator z positions
        settings_obs (SettingsObserver): VAs to be integrated in the acquired data as metadata
        """
        self._main_future = future
        self._future_lock = threading.Lock()
        self._actuator_f = None 
        self._single_acqui_f = None 
        self._future_state = RUNNING 
        self._streams = sortStreams(streams)
        self._zlevels = zlevels
        self._settings_obs = settings_obs
        self._zstep_duration = {}
        for s, z in zlevels.items():
            if len(z) > 1:
                self._zstep_duration[s] = guessActuatorMoveDuration(s.focuser, axis="z", distance=abs(z[0] - z[1]))

    def cancel(self, _):
        """
        Cancels the acquisition task
        (but there might  be some delay (not instantly called) )
        return (Boolean): True if canceled, False otherwise
        """
        with self._future_lock:
            self._future_state = CANCELLED
        # cancel the sub-futures
        if self._actuator_f:
            self._actuator_f.cancel()
        if self._single_acqui_f:
            self._single_acqui_f.cancel()
        return True

    def estimate_total_duration(self):
        """
        Estimates the total time for the zstack acquisition.
        return (Float > 0): the estimated time
        """
        return estimateZStackAcquisitionTime(self._streams, self._zlevels)

    def run(self):
        """
        The main function of the task class, which will be called by the future asynchronously
        """
        remaining_t = self.estimate_total_duration()
        acquired_data = []
        # iterate through streams
        for stream in self._streams:
            zstack = []
            if stream not in self._zlevels: 
                try:
                    # acquire this single stream, and get the data
                    self._single_acqui_f = acquire([stream], self._settings_obs)
                    data, exp = self._single_acqui_f.result()
                    if exp:
                        return acquired_data, exp
                    # check if cancellation happened while the acquiring future is working
                    if self._future_state == CANCELLED:
                        raise CancelledError()

                    # check on the acquired data
                    if not data:
                        logging.warning("The acquired data array for stream %s is empty", stream)
                    else:
                        logging.info("The acquisition for stream %s is done", stream)

                except CancelledError:
                    raise 
                except Exception as e:
                    logging.exception("The acquisition of stream %s failed", stream.name.value)
                    # return what was acquired so far  
                    return acquired_data, e

                if data:
                    acquired_data.append(data[0])
                # update the remaining time
                remaining_t -= stream.estimateAcquisitionTime()
                self._main_future.set_end_time(time.time() + remaining_t)

            else: 
                # for each stream, iterate through zlevels
                for i, z in enumerate(self._zlevels[stream]):
                    # move the focuser
                    self._actuator_f = stream.focuser.moveAbs({"z": z})
                    self._actuator_f.result()

                    # check if cancellation happened while the actuator future is working
                    if self._future_state == CANCELLED:
                        raise CancelledError()

                    # subtract one zstep time
                    if i != len(self._zlevels[stream]) - 1:
                        remaining_t -= self._zstep_duration[stream]
                        self._main_future.set_end_time(time.time() + remaining_t)

                    try:
                        # acquire this single stream, and get the data
                        self._single_acqui_f = acquire([stream], self._settings_obs)
                        data, exp = self._single_acqui_f.result()
                        if exp:
                            return acquired_data, exp
                        # check if cancellation happened while the acquiring future is working
                        if self._future_state == CANCELLED:
                            raise CancelledError()

                        # check on the acquired data
                        if not data:
                            logging.warning("The acquired data array for stream %s is empty", stream)
                        else:
                            logging.info("The acquisition for stream %s is done", stream)

                    except CancelledError:
                        raise
                    except Exception as e:
                        logging.exception("The acquisition failed at the %s-th zlevel of the stream %s, because %s" %(i+1, stream, e))
                        # TODO handle zstack assembling in case of error 
                        return acquired_data, e
                        
                    # only if there is data acquired
                    if data:
                        zstack.append(data[0])
                    # update the remaining time
                    remaining_t -= stream.estimateAcquisitionTime()
                    self._main_future.set_end_time(time.time() + remaining_t)

                zcube = assembleZCube(zstack, self._zlevels[stream])
                acquired_data.append(zcube)

        # state that the future has finished
        with self._future_lock:
            self._future_state = FINISHED

        return acquired_data, exp


def estimateZStackAcquisitionTime(streams, zlevels):
    """
    Estimates the total time for zstack acquisition including the movements time 
        of the zsteps 
    streams(list of Stream): the streams to be acquired 
    zlevels(dict Stream -> list of float): the zlevels dictionary containing
        the streams along with the list of z positions 
    return (float): total estimated time of acquisition of streams and zsteps 
    """
    acq_time = 0
    for s in streams:
        if s in zlevels.keys():
            acq_time += s.estimateAcquisitionTime() * len(zlevels[s])
        else:
            acq_time += s.estimateAcquisitionTime() 
    for s in streams:
        zs = zlevels.get(s, [0])  # concider that streams without zlevels are acquired at a single z
        if len(zs) > 1 and s.focuser:
            zstep = abs(zs[0] - zs[1])
            tot_zstep_time = (len(zs) - 1) *  guessActuatorMoveDuration(s.focuser, axis="z", distance=zstep)
            acq_time += tot_zstep_time

    return acq_time


def estimateTime(streams):
    """
    Computes the approximate time it will take to run the acquisition for the
     given streams (same arguments as acquire())
    streams (list of Stream): the streams to acquire
    return (0 <= float): estimated time in s.
    """
    tot_time = 0
    # We don't use foldStreams() as it creates new streams at every call, and
    # anyway sum of each stream should give already a good estimation.
    for s in streams:
        tot_time += s.estimateAcquisitionTime()

    return tot_time


def foldStreams(streams, reuse=None):
    """
    Merge (aka "fold) streams which can be acquired simultaneously into
     multi-detector streams.
    Note: currently only supports folding ScannedFluoMDStreams, not the SPARC
      streams.
    streams (list of Streams): Streams to be folded
    reuse (list of Streams, or None): list of streams which was previously output
      by this function. If it's present, the streams will be reused when possible,
      for optimisation.
    return (list of Streams): The list of streams, with the ones that can be
      folded replaced by a MD streams, the other streams are pass as-is.
    """
    # TODO: support SPARC streams
    if reuse is None:
        reuse = []

    # Copy the streams as-is, excepted for the ScannedFluoStream, which must
    # be "folded" into a ScannedFluoMDStream.

    folds = set()
    scan_fluos = []  # List of sets of ScannedFluoStream with compatible settings
    for s in streams:
        if isinstance(s, ScannedFluoStream):
            # Store it for folding
            for sfs in scan_fluos:
                sf = next(iter(sfs))
                # "compatible" means: same emitter/scanner/excitation
                if (sf.emitter is s.emitter and
                    sf.scanner is s.scanner and
                    sf.excitation.value == s.excitation.value):
                    sfs.add(s)
                    break
            else:
                scan_fluos.append({s})

        elif isinstance(s, ScannedTCSettingsStream):
            remote = ScannedRemoteTCStream("FLIM", s)
            folds.add(remote)
        elif isinstance(s, ScannedRemoteTCStream):
            # Don't add extra FLIM streams
            continue
        else:
            folds.add(s)

    # Generate the MD streams
    for sfs in scan_fluos:
        # Try to reuse current ScannedFluoMDStreams (optimisation)
        for s in reuse:
            if not isinstance(s, ScannedFluoMDStream):
                continue
            if sfs == set(s.streams):
                logging.debug("Reusing %s", s)
                folds.add(s)
                break
        else:
            logging.debug("Creating a new FluoMDStream for %d streams", len(sfs))
            name = "Combined %s" % (", ".join(sf.name.value for sf in sfs),)
            s = ScannedFluoMDStream(name, tuple(sfs))
            folds.add(s)

    return folds


def computeThumbnail(streamTree, acqTask):
    """
    compute the thumbnail of a given (finished) acquisition according to a
    streamTree
    streamTree (StreamTree): the tree of rendering
    acqTask (Future): a Future specifically returned by acquire(),
      representing an acquisition task
    returns model.DataArray: the thumbnail with metadata
    """
    raw_data, e = acqTask.result() # get all the raw data from the acquisition

    # FIXME: need to use the raw images of the acqTask as the source in the
    # streams of the streamTree (instead of whatever is the latest content of
    # .raw .
    # cf gui.util.img
    # => move to gui.util.img?

    # poor man's implementation: take the first image of the streams, hoping
    # it actually has a renderer (.image)
    streams = sorted(streamTree.getProjections(), key=_weight_stream,
                     reverse=True)
    if not streams:
        logging.warning("No stream found in the stream tree")
        return None

    iim = streams[0].image.value
    # add some basic info to the image
    if iim is not None:
        iim.metadata[model.MD_DESCRIPTION] = "Composited image preview"
    return iim


def _weight_stream(stream):
    """
    Defines how much a stream is of priority (should be done first) for
      acquisition.
    stream (acq.stream.Stream): a stream to weight
    returns (number): priority (the higher the more it should be done first)
    """
    if isinstance(stream, (FluoStream, ScannedFluoMDStream)):
        # Fluorescence ASAP to avoid bleaching
        if isinstance(stream, ScannedFluoMDStream):
            # Just take one of the streams, to keep things "simple"
            stream = stream.streams[0]

        # If multiple fluorescence acquisitions: prefer the long emission
        # wavelengths first because there is no chance their emission light
        # affects the other dyes (and which could lead to a little bit of
        # bleaching).
        ewl_center = fluo.get_center(stream.emission.value)
        if isinstance(ewl_center, Iterable):
            # multi-band filter, so fallback to guess based on excitation
            xwl_center = fluo.get_center(stream.excitation.value)
            if isinstance(xwl_center, Iterable):
                # also unguessable => just pick one "randomly"
                ewl_bonus = ewl_center[0]
            else:
                ewl_bonus = xwl_center + 50e-6 # add 50nm as guesstimate for emission
        else:
            ewl_bonus = ewl_center # normally, between 0 and 1
        return 100 + ewl_bonus
    elif isinstance(stream, OpticalStream):
        return 90 # any other kind of optical after fluorescence
    elif isinstance(stream, ScannedRemoteTCStream):
        return 85  # Stream for FLIM acquisition with time correlator
    elif isinstance(stream, EMStream):
        return 50 # can be done after any light
    elif isinstance(stream, (SEMCCDMDStream, SEMMDStream, SEMTemporalMDStream)):
        return 40 # after standard (=survey) SEM
    elif isinstance(stream, OverlayStream):
        return 10 # after everything (especially after SEM and optical)
    else:
        logging.debug("Unexpected stream of type %s", stream.__class__.__name__)
        return 0


def sortStreams(streams):
    """
    Sorts a list of streams based on the order they will be acquired 
    streams (acq.stream.Stream): a list of streams to be sorted 
    returns (acq.stream.Stream): a list of sorted streams 
    """
    return sorted(streams, key=_weight_stream, reverse=True)


class AcquisitionTask(object):

    def __init__(self, streams, future, settings_obs=None):
        self._future = future
        self._settings_obs = settings_obs

        # order the streams for optimal acquisition
        self._streams = sorted(streams, key=_weight_stream, reverse=True)

        # get the estimated time for each streams
        self._streamTimes = {} # Stream -> float (estimated time)
        for s in streams:
            self._streamTimes[s] = s.estimateAcquisitionTime()

        self._streams_left = set(self._streams) # just for progress update
        self._current_stream = None
        self._current_future = None
        self._cancelled = False

    def run(self):
        """
        Runs the acquisition
        returns:
            (list of DataArrays): all the raw data acquired
            (Exception or None): exception raised during the acquisition
        raise:
            Exception: if it failed before any result were acquired
        """
        exp = None
        assert(self._current_stream is None) # Task should be used only once
        expected_time = sum(self._streamTimes.values())
        # no need to set the start time of the future: it's automatically done
        # when setting its state to running.
        self._future.set_progress(end=time.time() + expected_time)

        logging.info("Starting acquisition of %s streams, with expected duration of %f s",
                     len(self._streams), expected_time)

        # Keep order so that the DataArrays are returned in the order they were
        # acquired. Not absolutely needed, but nice for the user in some cases.
        raw_images = OrderedDict()  # stream -> list of raw images
        try:
            # Tell the leeches that the acquisition is starting
            for s in self._streams:
                try:
                    for l in s.leeches:
                        try:
                            l.series_start()
                        except Exception:
                            logging.exception("Leech %s failed to start the series, "
                                              "will pretend nothing happened", l)
                except AttributeError:
                    # No leeches
                    pass

            if not self._settings_obs:
                logging.info("Acquisition task has no SettingsObserver, not saving extra "
                             "metadata.")
            for s in self._streams:
                # Get the future of the acquisition, depending on the Stream type
                if hasattr(s, "acquire"):
                    f = s.acquire()
                else: # fall-back to old style stream
                    f = _futures.wrapSimpleStreamIntoFuture(s)
                self._current_future = f
                self._current_stream = s
                self._streams_left.discard(s)

                # in case acquisition was cancelled, before the future was set
                if self._cancelled:
                    f.cancel()
                    raise CancelledError()

                # If it's a ProgressiveFuture, listen to the time update
                try:
                    f.add_update_callback(self._on_progress_update)
                except AttributeError:
                    pass # not a ProgressiveFuture, fine

                # Wait for the acquisition to be finished.
                # Will pass down exceptions, included in case it's cancelled
                das = f.result()
                if not isinstance(das, Iterable):
                    logging.warning("Future of %s didn't return a list of DataArrays, but %s", s, das)
                    das = []

                # Add extra settings to metadata
                if self._settings_obs:
                    settings = self._settings_obs.get_all_settings()
                    for da in das:
                        da.metadata[model.MD_EXTRA_SETTINGS] = copy.deepcopy(settings)
                raw_images[s] = das

                # update the time left
                expected_time -= self._streamTimes[s]
                self._future.set_progress(end=time.time() + expected_time)

            # Tell the leeches it's over. Note: we don't do it in case of
            # (partial) error.
            for s in self._streams:
                try:
                    for l in s.leeches:
                        try:
                            l.series_complete(s.raw)
                        except Exception:
                            logging.warning("Leech %s failed to complete the series",
                                            l, exc_info=True)
                except AttributeError:
                    # No leeches
                    pass

        except CancelledError:
            raise
        except Exception as ex:
            # If no acquisition yet => just raise the exception,
            # otherwise, the results we got might already be useful
            if not raw_images:
                raise
            logging.warning("Exception during acquisition (after some data already acquired)",
                            exc_info=True)
            exp = ex
        finally:
            # Don't hold references to the streams once it's over
            self._streams = []
            self._streams_left.clear()
            self._streamTimes = {}
            self._current_stream = None
            self._current_future = None

        # Update metadata using OverlayStream (if there was one)
        self._adjust_metadata(raw_images)

        # merge all the raw data (= list of DataArrays) into one long list
        ret = sum(raw_images.values(), [])
        return ret, exp

    def _adjust_metadata(self, raw_data):
        """
        Update/adjust the metadata of the raw data received based on global
        information.
        raw_data (dict Stream -> list of DataArray): the raw data for each stream.
          The raw data is directly updated, and even removed if necessary.
        """
        # Update the pos/pxs/rot metadata from the fine overlay measure.
        # The correction metadata is in the metadata of the only raw data of
        # the OverlayStream.
        opt_cor_md = None
        sem_cor_md = None
        for s, data in list(raw_data.items()):
            if isinstance(s, OverlayStream):
                if opt_cor_md or sem_cor_md:
                    logging.warning("Multiple OverlayStreams found")
                opt_cor_md = data[0].metadata
                sem_cor_md = data[1].metadata
                del raw_data[s] # remove the stream from final raw data

        # Even if no overlay stream was present, it's worthy to update the
        # metadata as it might contain correction metadata from basic alignment.
        for s, data in raw_data.items():
            if isinstance(s, OpticalStream):
                for d in data:
                    img.mergeMetadata(d.metadata, opt_cor_md)
            elif isinstance(s, EMStream):
                for d in data:
                    img.mergeMetadata(d.metadata, sem_cor_md)

        # add the stream name to the image if nothing yet
        for s, data in raw_data.items():
            for d in data:
                if model.MD_DESCRIPTION not in d.metadata:
                    d.metadata[model.MD_DESCRIPTION] = s.name.value

    def _on_progress_update(self, f, start, end):
        """
        Called when the current future has made a progress (and so it should
        provide a better time estimation).
        """
        # If the acquisition is cancelled or failed, we might receive updates
        # from the sub-future a little after. Let's not make a fuss about it.
        if self._future.done():
            return

        # There is a tiny chance that self._current_future is already set to
        # None, but the future isn't officially ended yet. Also fine.
        if self._current_future != f and self._current_future is not None:
            logging.warning("Progress update not from the current future: %s instead of %s",
                            f, self._current_future)
            return

        total_end = end + sum(self._streamTimes[s] for s in self._streams_left)
        self._future.set_progress(end=total_end)

    def cancel(self, future):
        """
        cancel the acquisition
        """
        # put the cancel flag
        self._cancelled = True

        if self._current_future is not None:
            cancelled = self._current_future.cancel()
        else:
            cancelled = False

        # Report it's too late for cancellation (and so result will come)
        if not cancelled and not self._streams_left:
            return False

        return True


HIDDEN_VAS = ['children', 'dependencies', 'affects', 'alive', 'state', 'ghosts']
class SettingsObserver(object):
    """
    Class that listens to all settings, so they can be easily stored as metadata
    at the end of an acquisition.
    """

    def __init__(self, components):
        """
        components (set of HwComponents): component which should be observed
        """
        self._all_settings = {}
        self._components = components  # keep a reference to the components, so they are not garbage collected
        self._va_updaters = []  # keep a reference to the subscribers so they are not garbage collected

        for comp in components:
            self._all_settings[comp.name] = {}
            vas = model.getVAs(comp).items()
            prepare_to_listen_to_more_vas(len(vas))

            for va_name, va in vas:
                if va_name in HIDDEN_VAS:
                    continue
                # Store current value of VA (calling .value might take some time)
                self._all_settings[comp.name][va_name] = [va.value, va.unit]
                # Subscribe to VA, update dictionary on callback
                def update_settings(value, comp_name=comp.name, va_name=va_name):
                    self._all_settings[comp_name][va_name][0] = value
                self._va_updaters.append(update_settings)
                va.subscribe(update_settings)

    def get_all_settings(self):
        return copy.deepcopy(self._all_settings)
