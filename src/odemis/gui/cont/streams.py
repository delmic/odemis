# -*- coding: utf-8 -*-
"""
Created on 26 Sep 2012

@author: Éric Piel

Copyright © 2012-2014 Éric Piel, Delmic

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
import logging
from wx.lib.pubsub import pub

import odemis.acq.stream as acqstream
import odemis.gui.model as guimodel
from odemis import model
from odemis.gui import comp


# Stream scheduling policies: decides which streams which are with .should_update
# get .is_active
SCHED_LAST_ONE = 1  # Last stream which got added to the should_update set
SCHED_ALL = 2  # All the streams which are in the should_update stream
# Note: it seems users don't like ideas like round-robin, where the hardware
# keeps turn on and off, (and with fluorescence fine control must be done, to
# avoid bleaching).
# TODO: SCHED_ALL_INDIE -> Schedule at the same time all the streams which
# are independent (no emitter from a stream will affect any detector of another
# stream).


class StreamController(object):
    """
    Manages the insertion/suppression of streams (with their corresponding
    stream panels in the stream bar).
    This include the management of "Add XXX stream" actions at the bottom of
    the stream panel.
    """

    def __init__(self, tab_data, stream_bar, static=False, locked=False):
        """
        tab_data (MicroscopyGUIData): the representation of the microscope Model
        stream_bar (StreamBar): an empty stream panel
        static (Boolean): Treat streams as static
        locked (Boolean): Don't allow to add/remove/hide/show streams
        """
        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._stream_bar = stream_bar

        self._scheduler_subscriptions = {} # stream -> callable
        self._sched_policy = SCHED_LAST_ONE # works well in most cases

        if stream_bar.btn_add_stream:
            self._createAddStreamActions()

        self._tab_data_model.focussedView.subscribe(self._onView, init=True)
        pub.subscribe(self.removeStream, 'stream.remove')

        # TODO: uncomment if needed
        # if hasattr(tab_data, 'opticalState'):
        #     tab_data.opticalState.subscribe(self.onOpticalState, init=True)
        #
        # if hasattr(tab_data, 'emState'):
        #     tab_data.emState.subscribe(self.onEMState, init=True)

        # This attribute indicates whether live data is processed by the streams
        # in the controller, or that they just display static data.
        self.static_mode = static
        # Disable all controls
        self.locked_mode = locked

    @classmethod
    def data_to_static_streams(cls, data):
        """ Split the given data into static streams
        :param data: (list of DataArrays) Data to be split
        :return: (list) A list of Stream instances

        """

        result_streams = []

        # AR data is special => all merged in one big stream
        ar_data = []

        # Add each data as a stream of the correct type
        for d in data:
            # Hack for not displaying Anchor region data
            # TODO: store and use acquisition type with MD_ACQ_TYPE?
            if d.metadata[model.MD_DESCRIPTION] == "Anchor region":
                continue

            # Streams only support 2D data (e.g., no multiple channels like RGB)
            # except for spectra which have a 3rd dimensions on dim 5.
            # So if that's the case => separate into one stream per channel
            channels_data = cls._split_channels(d)

            for channel_data in channels_data:
                # TODO: be more clever to detect the type of stream
                if (
                        model.MD_WL_LIST in channel_data.metadata or
                        model.MD_WL_POLYNOMIAL in channel_data.metadata or
                        (len(channel_data.shape) >= 5 and channel_data.shape[-5] > 1)
                ):
                    name = channel_data.metadata.get(model.MD_DESCRIPTION, "Spectrum")
                    klass = acqstream.StaticSpectrumStream
                elif model.MD_AR_POLE in channel_data.metadata:
                    # AR data
                    ar_data.append(channel_data)
                    continue
                elif (
                        (model.MD_IN_WL in channel_data.metadata and
                         model.MD_OUT_WL in channel_data.metadata) or
                        model.MD_USER_TINT in channel_data.metadata
                ):
                    # No explicit way to distinguish between Brightfield and Fluo,
                    # so guess it's Brightfield iif:
                    # * No tint
                    # * (and) Large band for excitation wl (> 100 nm)
                    in_wl = d.metadata[model.MD_IN_WL]
                    if (
                            model.MD_USER_TINT in channel_data.metadata or
                            in_wl[1] - in_wl[0] < 100e-9
                    ):
                        # Fluo
                        name = channel_data.metadata.get(model.MD_DESCRIPTION, "Filtered colour")
                        klass = acqstream.StaticFluoStream
                    else:
                        # Brightfield
                        name = channel_data.metadata.get(model.MD_DESCRIPTION, "Brightfield")
                        klass = acqstream.StaticBrightfieldStream
                elif model.MD_IN_WL in channel_data.metadata:  # no MD_OUT_WL
                    name = channel_data.metadata.get(model.MD_DESCRIPTION, "Brightfield")
                    klass = acqstream.StaticBrightfieldStream
                else:
                    name = channel_data.metadata.get(model.MD_DESCRIPTION, "Secondary electrons")
                    klass = acqstream.StaticSEMStream

                result_streams.append(klass(name, channel_data))

        # Add one global AR stream
        if ar_data:
            result_streams.append(acqstream.StaticARStream("Angular", ar_data))

        return result_streams

    @classmethod
    def _split_channels(cls, data):
        """ Separate a DataArray into multiple DataArrays along the 3rd dimension (channel)

        :param data: (DataArray) can be any shape
        :return: (list of DataArrays) a list of one DataArray (if no splitting is needed) or more
            (if splitting happened). The metadata is the same (object) for all the DataArrays.

        """

        # Anything to split?
        if len(data.shape) >= 3 and data.shape[-3] > 1:
            # multiple channels => split
            das = []
            for c in range(data.shape[-3]):
                das.append(data[..., c, :, :])  # metadata ref is copied
            return das
        else:
            # return just one DA
            return [data]

    def to_static_mode(self):
        self.static_mode = True

    def to_locked_mode(self):
        self.locked_mode = True

    def setSchedPolicy(self, policy):
        """
        Change the stream scheduling policy
        policy (SCHED_*): the new policy
        """
        assert policy in [SCHED_LAST_ONE, SCHED_ALL]
        self._sched_policy = policy

    def _createAddStreamActions(self):
        """ Create the compatible "add stream" actions according to the current
        microscope.
        To be executed only once, at initialisation.
        """
        # Basically one action per type of stream

        # TODO: always display the action (if it's compatible), but update
        # the disable/enable depending on the state of the chamber (iow if SEM
        # or optical button is enabled)

        # First: Fluorescent stream (for dyes)
        if (
                self._main_data_model.light and
                self._main_data_model.light_filter and
                self._main_data_model.ccd
        ):
            def fluor_capable():
                # TODO: need better way to check, maybe opticalState == STATE_DISABLED?
                enabled = self._main_data_model.chamberState.value in {guimodel.CHAMBER_VACUUM,
                                                                       guimodel.CHAMBER_UNKNOWN}
                view = self._tab_data_model.focussedView.value
                compatible = view.is_compatible(acqstream.FluoStream)
                return enabled and compatible

            # TODO: how to know it's _fluorescent_ microscope?
            #  => multiple source? filter?
            self._stream_bar.add_action("Filtered colour", self._userAddFluo, fluor_capable)

        # Bright-field
        if self._main_data_model.brightlight and self._main_data_model.ccd:

            def brightfield_capable():
                enabled = self._main_data_model.chamberState.value in {guimodel.CHAMBER_VACUUM,
                                                                       guimodel.CHAMBER_UNKNOWN}
                view = self._tab_data_model.focussedView.value
                compatible = view.is_compatible(acqstream.BrightfieldStream)
                return enabled and compatible

            self._stream_bar.add_action("Bright-field", self.addBrightfield, brightfield_capable)

        def sem_capable():
            enabled = self._main_data_model.chamberState.value in {guimodel.CHAMBER_VACUUM,
                                                                   guimodel.CHAMBER_UNKNOWN}
            view = self._tab_data_model.focussedView.value
            compatible = view.is_compatible(acqstream.SEMStream)
            return enabled and compatible

        # SED
        if self._main_data_model.ebeam and self._main_data_model.sed:
            self._stream_bar.add_action("Secondary electrons", self.addSEMSED, sem_capable)
        # BSED
        if self._main_data_model.ebeam and self._main_data_model.bsd:
            self._stream_bar.add_action("Backscattered electrons", self.addSEMBSD, sem_capable)

    def _userAddFluo(self, **kwargs):
        """
        Called when the user request adding a Fluo stream
        Same as addFluo, but also changes the focus to the name text field
        """
        se = self.addFluo(**kwargs)
        se.set_focus_on_label()

    def addFluo(self, **kwargs):
        """
        Creates a new fluorescence stream and a stream panel in the stream bar
        returns (StreamPanel): the panel created
        """
        # Find a name not already taken
        names = [s.name.value for s in self._tab_data_model.streams.value]
        for i in range(1, 1000):
            name = "Filtered colour %d" % i
            if not name in names:
                break
        else:
            logging.error("Failed to find a new unique name for stream")
            name = "Filtered colour"

        s = acqstream.FluoStream(
            name,
            self._main_data_model.ccd,
            self._main_data_model.ccd.data,
            self._main_data_model.light,
            self._main_data_model.light_filter
        )

        # TODO: automatically pick a good set of excitation/emission which is
        # not yet used by any FluoStream (or the values from the last stream
        # deleted?) Or is it better to just use the values fitting the current
        # hardware settings as it is now?

        return self._addStream(s, **kwargs)

    def addBrightfield(self, **kwargs):
        """
        Creates a new brightfield stream and panel in the stream bar
        returns (StreamPanel): the stream panel created
        """
        s = acqstream.BrightfieldStream(
            "Bright-field",
            self._main_data_model.ccd,
            self._main_data_model.ccd.data,
            self._main_data_model.brightlight
        )
        return self._addStream(s, **kwargs)

    def addSEMSED(self, **kwargs):
        """
        Creates a new SED stream and panel in the stream bar
        returns (StreamPanel): the panel created
        """
        if self._main_data_model.role == "delphi":
            # For the Delphi, the SEM stream needs to be more "clever" because
            # it needs to run a simple spot alignment every time the stage has
            # moved before starting to acquire.
            s = acqstream.AlignedSEMStream(
                "Secondary electrons",
                self._main_data_model.sed,
                self._main_data_model.sed.data,
                self._main_data_model.ebeam,
                self._main_data_model.ccd,
                self._main_data_model.stage,
                shiftebeam="Ebeam shift"
            )
            # Select between "Metadata update" and "Stage move"
            # TODO: use shiftebeam once the phenom driver supports it
        else:
            s = acqstream.SEMStream(
                "Secondary electrons",
                self._main_data_model.sed,
                self._main_data_model.sed.data,
                self._main_data_model.ebeam
            )
        return self._addStream(s, **kwargs)

    def addSEMBSD(self, **kwargs):
        """
        Creates a new backscattered electron stream and panel in the stream bar
        returns (StreamPanel): the panel created
        """
        if self._main_data_model.role == "delphi":
            # For the Delphi, the SEM stream needs to be more "clever" because
            # it needs to run a simple spot alignment every time the stage has
            # moved before starting to acquire.
            s = acqstream.AlignedSEMStream(
                "Backscattered electrons",
                self._main_data_model.bsd,
                self._main_data_model.bsd.data,
                self._main_data_model.ebeam,
                self._main_data_model.ccd,
                self._main_data_model.stage,
                shiftebeam="Ebeam shift"
            )
            # Select between "Metadata update" and "Stage move"
            # TODO: use shiftebeam once the phenom driver supports it
        else:
            s = acqstream.SEMStream(
                "Backscattered electrons",
                self._main_data_model.bsd,
                self._main_data_model.bsd.data,
                self._main_data_model.ebeam
            )
        return self._addStream(s, **kwargs)

    def addStatic(self, name, image, cls=acqstream.StaticStream, **kwargs):
        """
        Creates a new static stream and panel in the stream bar

        :param name: (string)
        :param image: (DataArray)
        :param cls: (class of Stream)
        :param returns: (StreamPanel): the panel created

        """

        s = cls(name, image)
        return self.addStream(s, **kwargs)

    def addStream(self, stream, **kwargs):
        """ Create a stream entry for the given existing stream

        :return StreamPanel: the panel created for the stream
        """
        return self._addStream(stream, **kwargs)

    def _addStream(self, stream, add_to_all_views=False, visible=True, play=None):
        """ Add the given stream to the tab data model and appropriate views

        stream (stream.Stream): the new stream to add
        add_to_all_views (boolean): if True, add the stream to all the
            compatible views, otherwise add only to the current view.
        visible (boolean): If True, create a stream entry, otherwise adds the
            stream but do not create any entry.
        play (None or boolean): If True, immediately start it, if False, let it
            stopped, and if None, only play if already a stream is playing
        returns (StreamPanel or Stream): stream entry or stream (if visible
            is False) that was created

        """

        if stream not in self._tab_data_model.streams.value:
            # Insert it as first, so it's considered the latest stream used
            self._tab_data_model.streams.value.insert(0, stream)

        if add_to_all_views:
            for v in self._tab_data_model.views.value:
                if hasattr(v, "stream_classes") and isinstance(stream, v.stream_classes):
                    v.addStream(stream)
        else:
            v = self._tab_data_model.focussedView.value
            if hasattr(v, "stream_classes") and not isinstance(stream, v.stream_classes):
                warn = "Adding %s stream incompatible with the current view"
                logging.warning(warn, stream.__class__.__name__)
            v.addStream(stream)

        # TODO: create a StreamScheduler call it like self._scheduler.addStream(stream)
        # ... or simplify to only support a stream at a time
        self._scheduleStream(stream)

        # start the stream right now (if requested)
        if play is None:
            if not visible:
                play = False
            else:
                play = any(s.should_update.value for s in self._tab_data_model.streams.value)
        stream.should_update.value = play

        if visible:
            spanel = comp.stream.StreamPanel(self._stream_bar, stream, self._tab_data_model)
            show = isinstance(spanel.stream, self._tab_data_model.focussedView.value.stream_classes)
            self._stream_bar.add_stream(spanel, show)

            if self.locked_mode:
                spanel.to_locked_mode()
            elif self.static_mode:
                spanel.to_static_mode()

            # TODO: make StreamTree a VA-like and remove this
            logging.debug("Sending stream.ctrl.added message")
            pub.sendMessage('stream.ctrl.added',
                            streams_present=True,
                            streams_visible=self._has_visible_streams(),
                            tab=self._tab_data_model)

            return spanel
        else:
            return stream

    def addStreamForAcquisition(self, stream):
        """ Create a stream entry for the given existing stream, adapted to ac

        :return StreamPanel:

        """
        sp = comp.stream.StreamPanel(self._stream_bar, stream, self._tab_data_model)
        self._stream_bar.add_stream(sp, True)
        sp.to_static_mode()

        return sp

    # === VA handlers

    def _onView(self, view):
        """
        Called when the current view changes
        """

        if not view:
            return

        # import sys
        # print sys.getrefcount(self)

        # hide/show the stream panels which are compatible with the view
        allowed_classes = view.stream_classes
        for e in self._stream_bar.stream_panels:
            e.Show(isinstance(e.stream, allowed_classes))
        # self.Refresh()
        self._stream_bar._fit_streams()

        # update the "visible" icon of each stream panel to match the list
        # of streams in the view
        visible_streams = view.getStreams()

        for e in self._stream_bar.stream_panels:
            e.set_visible(e.stream in visible_streams)

        logging.debug("Sending stream.ctrl message")
        pub.sendMessage('stream.ctrl',
                        streams_present=True,
                        streams_visible=self._has_visible_streams(),
                        tab=self._tab_data_model)

    def _onStreamUpdate(self, stream, updated):
        """
        Called when a stream "updated" state changes
        """
        # Ensure it's visible in the current view (if feasible)
        if updated:
            fv = self._tab_data_model.focussedView.value
            if (isinstance(stream, fv.stream_classes) and # view is compatible
                not stream in fv.getStreams()):
                # Add to the view
                fv.addStream(stream)
                # Update the graphical display
                for e in self._stream_bar.stream_panels:
                    if e.stream is stream:
                        e.set_visible(True)

        # This is a stream scheduler:
        # * "should_update" streams are the streams to be scheduled
        # * a stream becomes "active" when it's currently acquiring
        # * when a stream is just set to be "should_update" (by the user) it
        #   should be scheduled as soon as possible

        # Note we ensure that .streams is sorted with the new playing stream as
        # the first one in the list. This means that .streams is LRU sorted,
        # which can be used for various stream information.
        # TODO: that works nicely for live tabs, but in analysis tab, this
        # never happens so the latest stream is always the same one.
        # => need more ways to change current stream (at least pick one from the
        # current view?)

        if self._sched_policy == SCHED_LAST_ONE:
            # Only last stream with should_update is active
            if not updated:
                stream.is_active.value = False
                # the other streams might or might not be updated, we don't care
            else:
                # Make sure that other streams are not updated (and it also
                # provides feedback to the user about which stream is active)
                for s in self._scheduler_subscriptions:
                    if s != stream:
                        s.should_update.value = False
                # activate this stream
                # It's important it's last, to ensure hardware settings don't
                # mess up with each other.
                stream.is_active.value = True
        elif self._sched_policy == SCHED_ALL:
            # All streams with should_update are active
            stream.is_active.value = updated
        else:
            raise NotImplementedError("Unknown scheduling policy %s" % self._sched_policy)

        if updated:
            # put it back to the beginning of the list to indicate it's the
            # latest stream used
            l = self._tab_data_model.streams.value
            try:
                i = l.index(stream)
            except ValueError:
                logging.info("Stream %s is not in the stream list", stream.name)
                return
            if i == 0:
                return # fast path
            l = [stream] + l[:i] + l[i + 1:] # new list reordered
            self._tab_data_model.streams.value = l

    def _scheduleStream(self, stream):
        """ Add a stream to be managed by the update scheduler.
        stream (Stream): the stream to add. If it's already scheduled, it's fine.
        """
        # create an adapted subscriber for the scheduler
        def detectUpdate(updated, stream=stream):
            self._onStreamUpdate(stream, updated)
            self._updateMicroscopeStates()

        self._scheduler_subscriptions[stream] = detectUpdate
        stream.should_update.subscribe(detectUpdate)

    def _unscheduleStream(self, stream):
        """
        Remove a stream from being managed by the scheduler. It will also be
        stopped from updating.
        stream (Stream): the stream to remove. If it's not currently scheduled,
          it's fine.
        """
        stream.is_active.value = False
        stream.should_update.value = False
        if stream in self._scheduler_subscriptions:
            callback = self._scheduler_subscriptions.pop(stream)
            stream.should_update.unsubscribe(callback)

    def onOpticalState(self, state):
        # TODO: disable/enable add stream actions
        if state == guimodel.STATE_OFF:
            pass
        elif state == guimodel.STATE_ON:
            pass

    def onEMState(self, state):
        # TODO: disable/enable add stream actions
        if state == guimodel.STATE_OFF:
            pass
        elif state == guimodel.STATE_ON:
            pass

    def _updateMicroscopeStates(self):
        """
        Update the SEM/optical states based on the stream currently playing
        """
        streams = set()  # streams currently playing
        for s in self._tab_data_model.streams.value:
            if s.should_update.value:
                streams.add(s)

        # optical state = at least one stream playing is optical
        if hasattr(self._tab_data_model, 'opticalState'):
            if any(isinstance(s, acqstream.OpticalStream) for s in streams):
                self._tab_data_model.opticalState.value = guimodel.STATE_ON
            else:
                self._tab_data_model.opticalState.value = guimodel.STATE_OFF

        # sem state = at least one stream playing is sem
        if hasattr(self._tab_data_model, 'emState'):
            if any(isinstance(s, acqstream.EMStream) for s in streams):
                self._tab_data_model.emState.value = guimodel.STATE_ON
            else:
                self._tab_data_model.emState.value = guimodel.STATE_OFF

    # TODO: shall we also have a suspend/resume streams that directly changes
    # is_active, and used when the tab/window is hidden?

    def enableStreams(self, enabled, classes=acqstream.Stream):
        """
        Enable/disable the play/pause button of all the streams of the given class

        enabled (boolean): True if the buttons should be enabled, False to
         disable them.
        classes (class or list of class): classes of streams that should be
          disabled.

        Returns (set of Stream): streams which were actually enabled/disabled
        """
        streams = set() # stream changed
        for e in self._stream_bar.stream_panels:
            s = e.stream
            if isinstance(s, classes):
                streams.add(s)
                e.enable_updated_btn(enabled)

        return streams

    def pauseStreams(self, classes=acqstream.Stream):
        """
        Pause (deactivate and stop updating) all the streams of the given class
        classes (class or list of class): classes of streams that should be
        disabled.

        Returns (set of Stream): streams which were actually paused
        """
        streams = set() # stream paused
        for s in self._tab_data_model.streams.value:
            if isinstance(s, classes):
                if s.should_update.value:
                    streams.add(s)
                    s.is_active.value = False
                    s.should_update.value = False
                    # TODO also disable stream panel "update" button?

        return streams

    def resumeStreams(self, streams):
        """
        (Re)start (activate) streams
        streams (set of streams): Streams that will be resumed
        """
        for s in streams:
            s.should_update.value = True
            # it will be activated by the stream scheduler

    def removeStream(self, stream):
        """
        Removes the given stream.
        stream (Stream): the stream to remove
        Note: the stream panel is to be destroyed separately via the stream_bar
        It's ok to call if the stream has already been removed
        """
        # don't schedule any more
        self._unscheduleStream(stream)

        # Remove from the views
        for v in self._tab_data_model.views.value:
            if hasattr(v, "removeStream"):
                v.removeStream(stream)

        try:
            self._tab_data_model.streams.value.remove(stream)
        except ValueError:
            logging.warn("Stream not found, so not removed")

        logging.debug("Sending stream.ctrl.removed message")
        pub.sendMessage('stream.ctrl.removed',
                        streams_present=self._has_streams(),
                        streams_visible=self._has_visible_streams(),
                        tab=self._tab_data_model)

    def clear(self):
        """
        Remove all the streams (from the model and the GUI)
        """
        # We could go for each stream panel, and call removeStream(), but it's
        # as simple to reset all the lists

        # clear the graphical part
        while self._stream_bar.stream_panels:
            spanel = self._stream_bar.stream_panels[0]
            self._stream_bar.remove_stream_panel(spanel)

        # clear the interface model
        # (should handle cases where a new stream is added simultaneously)
        while self._tab_data_model.streams.value:
            stream = self._tab_data_model.streams.value.pop()
            self._unscheduleStream(stream)

            # Remove from the views
            for v in self._tab_data_model.views.value:
                if hasattr(v, "removeStream"):
                    v.removeStream(stream)

        if self._has_streams() or self._has_visible_streams():
            logging.warning("Failed to remove all streams")

        logging.debug("Sending stream.ctrl.removed message")
        pub.sendMessage('stream.ctrl.removed',
                        streams_present=False,
                        streams_visible=False,
                        tab=self._tab_data_model)

    def _has_streams(self):
        return len(self._stream_bar.stream_panels) > 0

    def _has_visible_streams(self):
        return any(s.IsShown() for s in self._stream_bar.stream_panels)
