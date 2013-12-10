# -*- coding: utf-8 -*-
"""
Created on 5 Feb 2013

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

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

from __future__ import division

from collections import OrderedDict
import collections
from concurrent import futures
from concurrent.futures._base import CancelledError, FINISHED, CANCELLED, \
    CANCELLED_AND_NOTIFIED, RUNNING
import logging
import numpy
from odemis import model
from odemis.gui.model.stream import FluoStream, ARStream, SpectrumStream, \
    SEMSpectrumMDStream, OPTICAL_STREAMS, EM_STREAMS, SEMARMDStream
from odemis.gui.util import img
import sys
import threading
import time


# This is the "manager" of an acquisition. The basic idea is that you give it
# a list of streams to acquire, and it will acquire them in the best way in the
# background. You are in charge of ensuring that no other acquisition is
# going on at the same time.
# The manager receives a list of streams to acquire, order them in the best way,
# and then creates a separate thread to run the acquisition of each stream. It
# returns a special "ProgressiveFuture" which is a Future object that can be
# stopped while already running, and reports from time to time progress on its
# execution.
def startAcquisition(streams):
    """
    Starts an acquisition task for the given streams. It will decide in which
      order the stream must be acquired.
      Note: it is highly recommended to not have any other acquisition going on.
    streams (list of Stream): the streams to acquire
    returns (ProgressiveFuture): an object that represents the task, allow to
      know how much time before it is over and to cancel it. It also permits to
      receive the result of the task, which is:
      (list of model.DataArray): the raw acquisition data
    """
    # create a future
    future = model.ProgressiveFuture()

    # create a task
    task = AcquisitionTask(_mergeStreams(streams), future)
    future.task_canceller = task.cancel # let the future cancel the task

    # run executeTask in a thread
    thread = threading.Thread(target=_executeTask, name="Acquisition task",
                              args=(future, task.run))
    thread.start()

    # return the interface to manipulate the task
    return future

def estimateTime(streams):
    """
    Computes the approximate time it will take to run the acquisition for the
     given streams (same arguments as startAcquisition())
    streams (list of Stream): the streams to acquire
    return (0 <= float): estimated time in s.
    """
    tot_time = 0
    # We don't use mergeStreams() as it creates new streams at every call, and
    # anyway sum of each stream should give already a good estimation.
    for s in streams:
        tot_time += s.estimateAcquisitionTime()

    return tot_time

def computeThumbnail(streamTree, acqTask):
    """
    compute the thumbnail of a given (finished) acquisition according to a
    streamTree
    streamTree (StreamTree): the tree of rendering
    acqTask (Future): a Future specifically returned by startAcquisition(),
      representing an acquisition task
    returns model.DataArray: the thumbnail with metadata
    """
    raw_data = acqTask.result() # get all the raw data from the acquisition

    # FIXME: need to use the raw images of the acqTask as the source in the
    # streams of the streamTree

    # FIXME: this call now doesn't work. We need a hack to call the canvas
    # method from outside the canvas, or use a canvas to render everything
    # thumbnail = self._streamTree.getImage()

    # poor man's implementation: take the first image of the streams, hoping
    # it actually has a renderer (.image)
    streams = sorted(streamTree.getStreams(), key=_weight_stream,
                               reverse=True)
    iim = streams[0].image.value

    # convert the RGB image into a DataArray
    thumbnail = img.wxImage2NDImage(iim.image, keep_alpha=False)
    # add some basic info to the image
    metadata = {model.MD_POS: iim.center,
                model.MD_PIXEL_SIZE: (iim.mpp, iim.mpp),
                model.MD_DESCRIPTION: "Composited image preview"}
    return model.DataArray(thumbnail, metadata=metadata)

def _mergeStreams(streams):
    """
    Modifies a list of streams by merging possible streams into
    MultipleDetectorStreams
    streams (list of streams): the original list of streams
    return (list of streams): the same list or a shorter one
    """
    # TODO: move the logic to all the MDStreams? Each class would be able to
    # say whether it finds some potential streams to merge?

    merged = list(streams)
    # For now, this applies only to the SPARC streams
    # SEM CL + Spectrum => SEMSpectrumMD
    # SEM CL + AR => SEMARMD
    semcls = [s for s in streams if isinstance(s, EM_STREAMS) and s.name.value == "SEM CL"]
    specs = [s for s in streams if isinstance(s, SpectrumStream)]
    ars = [s for s in streams if isinstance(s, ARStream)]
    if semcls:
        if len(semcls) > 1:
            logging.warning("More than one SEM CL stream, not sure how to use them")
        semcl = semcls[0]

        for s in specs:
            mds = SEMSpectrumMDStream("%s - %s" % (semcl.name.value, s.name.value),
                                      semcl, s)
            merged.remove(s)
            if semcl in merged:
                merged.remove(semcl)
            merged.append(mds)

        for s in ars:
            mds = SEMARMDStream("%s - %s" % (semcl.name.value, s.name.value),
                                semcl, s)
            merged.remove(s)
            if semcl in merged:
                merged.remove(semcl)
            merged.append(mds)

    return merged


def _weight_stream(stream):
    """
    Defines how much a stream is of priority (should be done first) for
      acquisition.
    stream (model.stream.Stream): a stream to weight
    returns (number): priority (the higher the more it should be done first)
    """
    # SECOM: Optical before SEM to avoid bleaching
    if isinstance(stream, FluoStream):
        return 100 # Fluorescence ASAP to avoid bleaching
    elif isinstance(stream, OPTICAL_STREAMS):
        return 90 # any other kind of optical after fluorescence
    elif isinstance(stream, EM_STREAMS):
        if stream.name.value == "SEM CL": # special name on Sparc
            return 40 # should be done after SEM live
        else:
            return 50 # can be done after any light
    elif isinstance(stream, SEMSpectrumMDStream):
        return 40 # at the same time as SEM CL
    elif isinstance(stream, SpectrumStream):
        return 40 # at the same time as SEM CL
    elif isinstance(stream, SEMARMDStream):
        return 40 # at the same time as SEM CL
    elif isinstance(stream, ARStream):
        return 40 # at the same time as SEM CL
    else:
        logging.debug("Unexpected stream of type %s", stream.__class__.__name__)
        return 0

class AcquisitionTask(object):

    # TODO: needs a better handling of the stream dependencies. Also, features
    # like drift-compensation might need a special handling.
    def __init__(self, streams, future):
        self._future = future

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
        """
        assert(self._current_stream is None) # Task should be used only once
        expected_time = numpy.sum(self._streamTimes.values())

        # This is a little trick to force the future to give updates even if
        # the estimation is the same
        upd_period = min(10, max(0.1, expected_time/100))
        timer = threading.Thread(target=self._future_time_upd,
                       name="Acquisition timer update",
                       args=(upd_period,))
        timer.start()

        raw_images = []
        # no need to set the start time of the future: it's automatically done
        # when setting its state to running.
        self._future.set_end_time(time.time() + expected_time)

        # TODO: either stream has .acquire() that returns a (Progressive) Future
        # or wrap the .image.subscribe into a Future.
        # If future, just wait (and our progressive future automatically updates)
        # If ProgressiveFuture, update final time based on info received.
        for s in self._streams:
            # Get the future of the acquisition, depending on the Stream type
            if hasattr(s, "acquire"):
                f = s.acquire()
            else: # fall-back to old style stream
                f = _wrapSimpleStreamIntoFuture(s)
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
            data = f.result()

            # add the stream name to the image if nothing yet
            for d in data:
                if not model.MD_DESCRIPTION in d.metadata:
                    d.metadata[model.MD_DESCRIPTION] = s.name.value
            raw_images.extend(data)

            # update the time left
            expected_time -= self._streamTimes[s]
            self._future.set_end_time(time.time() + expected_time)

        # return all the raw data
        return raw_images

    def _on_progress_update(self, f, past, left):
        """
        Called when the current future has made a progress (and so it should
        provide a better time estimation).
        """
        if self._current_future != f:
            logging.warning("Progress update from not the current future: %s", f)
            return

        now = time.time()
        time_left = left
        for s in self._streams_left:
            time_left += self._streamTimes[s]

        self._future.set_end_time(now + time_left)

    def _future_time_upd(self, period):
        """
        Force the future to give a progress update at a given periodicity
        period (float): period in s
        Note: it automatically finishes when the future is done
        """
        logging.debug("starting thread update")
        while not self._future.done():
            logging.debug("updating the future")
            self._future._invoke_upd_callbacks()
            time.sleep(period)

    def cancel(self):
        """
        cancel the acquisition
        """
        # put the cancel flag
        self._cancelled = True

        if self._current_future is not None:
            self._current_future.cancel()


def _executeTask(future, fn, *args, **kwargs):
    """
    Executes a task represented by a future.
    Usually, called as main task of a (separate thread).
    Based on the standard futures code _WorkItem.run()
    future (Future): future that is used to represent the task
    fn (callable): function to call for running the future
    *args, **kwargs: passed to the fn
    returns None: when the task is over (or cancelled)
    """
    if not future.set_running_or_notify_cancel():
        return

    try:
        result = fn(*args, **kwargs)
    except CancelledError:
        # cancelled via the future (while running) => it's all already handled
        pass
    except BaseException:
        e = sys.exc_info()[1]
        future.set_exception(e)
    else:
        future.set_result(result)

def _wrapSimpleStreamIntoFuture(stream):
    """
    Starts one stream acquisition and return a Future
    Works with streams having only .is_active and .image .
    returns (Future that returns list of DataArray): the acquisition task 
    """
    # Create a Future, not started yet
    future = SimpleStreamFuture(stream)
    # run executeTask in a thread that will actually run/wait the acquisition
    thread = threading.Thread(target=_executeTask,
                              name="Simple stream Future runner",
                              args=(future, future._run))
    thread.start()
    return future

class SimpleStreamFuture(futures.Future):
    """
    Dedicated Future for a stream without .acquire
    Same as a normal future, excepted it can also cancel the execution
    while it's running.
    """
    def __init__(self, stream):
        """
        stream (Stream): Stream with at least .is_active and .image
        """
        futures.Future.__init__(self)
        self._stream = stream
        self._acq_over = threading.Event()

    def cancel(self):
        """Cancel the future if possible.

        Returns True if the future was cancelled, False otherwise. A future
        cannot be cancelled if it has already completed.
        """
        # Based on standard code, but with tweak in case it's running
        with self._condition:
            if self._state == FINISHED:
                return False

            if self._state in [CANCELLED, CANCELLED_AND_NOTIFIED]:
                return True

            if self._state == RUNNING:
                # disable the stream
                self._stream.image.unsubscribe(self._image_listener)
                self._stream.is_active.value = False
                self._acq_over.set()

            self._state = CANCELLED
            self._condition.notify_all()

        self._invoke_callbacks()
        return True

    def _run(self):
        """
        To be called to start the acquisition in the stream, and blocks until
        the task is finished
        returns (list of DataArray): acquisition data
        raises CancelledError if the acquisition was cancelled
        """
        # start stream
        self._stream.image.subscribe(self._image_listener)
        self._stream.is_active.value = True

        # TODO: timeout exception if too long (> 10 x estimated time)
        # wait until one image acquired or cancelled
        self._acq_over.wait()
        if self._state in [CANCELLED, CANCELLED_AND_NOTIFIED]:
            raise CancelledError()

        return self._stream.raw # the acquisition data

    def _image_listener(self, image):
        """
        called when a new image is generated, indicating end of acquisition
        """
        # stop acquisition
        self._stream.image.unsubscribe(self._image_listener)
        self._stream.is_active.value = False

        # let the _run() waiter know that it's all done
        self._acq_over.set()


# TODO: presets shouldn't work on SettingEntries (GUI-only objects), but on
# Stream (and HwComponents).
def apply_preset(preset):
    """
    Apply the presets. It tries to ensure that they are set in the right order
     if the hardware needs it.
    preset (dict SettingEntries -> value): new value for each SettingEntry that
            should be modified.
    """
    # TODO: Once presets only affect the streams, we don't have dependency order
    # problem anymore?

    preset = dict(preset) # shallow copy (so we don't change the input)

    # There are mostly 2 (similar) dependencies:
    # * binning > resolution
    # * scale > resolution > translation
    # => do it in order: binning | scale > resolution > translation

    def apply_presets_named(name):
        for se, value in preset.items():
            if se.name == name:
                logging.debug("Updating preset %s -> %s", se.name, value)
                se.va.value = value
                del preset[se]

    apply_presets_named("binning")
    apply_presets_named("scale")
    apply_presets_named("resolution")
    apply_presets_named("translation")

    for se, value in preset.items():
        logging.debug("Updating preset %s -> %s", se.name, value)
        se.va.value = value

def _get_entry(entries, comp, name):
    """
    find the entry for the given component with the name
    entries (list of SettingEntries): all the entries
    comp (model.Component)
    name (String)
    return (SettingEntry or None)
    """
    for e in entries:
        if e.comp == comp and e.name == name:
            return e
    else:
        return None


# Quality setting presets
def preset_hq(entries):
    """
    Preset for highest quality image
    entries (list of SettingEntries): each value as originally set
    returns (dict SettingEntries -> value): new value for each SettingEntry that should be modified
    """
    ret = {}

    for entry in entries:
        if not entry.va or entry.va.readonly:
            # not a real setting, just info
            logging.debug("Skipping the value %s", entry.name)
            continue


        value = entry.va.value
        if entry.name == "resolution":
            # if resolution => get the best one
            try:
                value = entry.va.range[1] # max
            except (AttributeError, model.NotApplicableError):
                pass

        elif entry.name == "dwellTime":
            # SNR improves logarithmically with the dwell time => x10
            value = entry.va.value * 10

            # make sure it still fits
            if isinstance(entry.va.range, collections.Iterable):
                value = sorted(list(entry.va.range) + [value])[1] # clip

        elif entry.name == "scale": # for scanners only
            # => smallest = 1,1
            value = tuple(1 for v in entry.va.value)

            # TODO: ensure it still fits

        elif entry.name == "binning":
            # if binning => smallest
            prev_val = entry.va.value
            try:
                value = entry.va.range[0] # min
            except (AttributeError, model.NotApplicableError):
                try:
                    value = min(entry.va.choices)
                except (AttributeError, model.NotApplicableError):
                    pass
            # Compensate decrease in energy by longer exposure time
            et_entry = _get_entry(entries, entry.comp, "exposureTime")
            if et_entry:
                et_value = ret.get(et_entry, et_entry.va.value)
                for prevb, newb in zip(prev_val, value):
                    et_value *= prevb / newb
                ret[et_entry] = et_value

        elif entry.name == "readoutRate":
            # the smallest, the less noise (and slower, but we don't care)
            try:
                value = entry.va.range[0] # min
            except (AttributeError, model.NotApplicableError):
                try:
                    value = min(entry.va.choices)
                except (AttributeError, model.NotApplicableError):
                    pass
        # rest => as is

        logging.debug("Adapting value %s from %s to %s", entry.name, entry.va.value, value)
        ret[entry] = value

    return ret

def preset_as_is(entries):
    """
    Preset which don't change anything (exactly as live)
    entries (list of SettingEntries): each value as originally set
    returns (dict SettingEntries -> value): new value for each SettingEntry that
        should be modified
    """
    ret = {}
    for entry in entries:
        if not entry.va or entry.va.readonly:
            # not a real setting, just info
            logging.debug("Skipping the value %s", entry.name)
            continue

        # everything as-is
        logging.debug("Copying value %s = %s", entry.name, entry.va.value)
        ret[entry] = entry.va.value

    return ret

def preset_no_change(entries):
    """
    Special preset which matches everything and doesn't change anything
    """
    return {}


# Name -> callable (list of SettingEntries -> dict (SettingEntries -> value))
presets = OrderedDict(
            (   (u"High quality", preset_hq),
                (u"Fast", preset_as_is),
                (u"Custom", preset_no_change)
            )
)
