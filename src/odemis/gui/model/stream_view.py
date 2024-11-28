# -*- coding: utf-8 -*-
"""
:created: 16 Feb 2012
:author: Éric Piel
:copyright: © 2012 - 2022 Éric Piel, Rinze de Laat, Philip Winkler, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License version 2 as published by the Free
    Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
    FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
    details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""
import logging
import math
import queue
import threading
import time
from typing import Tuple

from odemis import model
from odemis.acq.stream import DataProjection, RGBSpatialProjection, Stream, StreamTree
from odemis.model import MD_POS, FloatContinuous, InstantaneousFuture, VigilantAttribute

MAX_SAFE_MOVE_DISTANCE = 10e-3  # 1 cm


class View(object):

    def __init__(self, name):
        self.name = model.StringVA(name)

        # a thumbnail version of what is displayed
        self.thumbnail = VigilantAttribute(None)  # contains a wx.Image

        # Last time the image of the view was changed. It's actually mostly
        # a trick to allow other parts of the GUI to know when the (theoretical)
        # composited image has changed.
        self.lastUpdate = model.FloatVA(time.time(), unit="s")

    def __unicode__(self):
        return u"{}".format(self.name.value)

    def __str__(self):
        return "{}".format(self.name.value)


class StreamView(View):
    """
    An abstract class that is common for every view which display spatially
    layers of streams and might have also actuators such as a stage and a focus.

    Basically, its "input" is a StreamTree and it can request stage and focus
    move. It never computes the composited image from all the streams itself.
    It's up to other objects (e.g., the canvas) to ask the StreamTree for its
    latest image (the main goal of this scheme is to avoid computation when not
    needed). Similarly, the thumbnail is never automatically recomputed, but
    other objects can update it.
    """

    def __init__(
        self,
        name,
        stage=None,
        stream_classes=None,
        fov_hw=None,
        projection_class=RGBSpatialProjection,
        zPos=None,
        view_pos_init=None,
        fov_range=((0.0, 0.0), (1e9, 1e9)),
    ):
        """
        :param name (string): user-friendly name of the view
        :param stage (Actuator): actuator with two axes: x and y
        :param stream_classes (None, or tuple of classes): all subclasses that the
          streams in this view is allowed to show.
        :param fov_hw (None or Component): Component with a .horizontalFoV VA and
          a .shape. If not None, the view mpp (=mag) will be linked to that FoV.
        :param projection_class (DataProjection):
            Determines the projection used to display streams which have no .image
        :param zPos (None or Float VA): Global position in Z coordinate for the view.
          Used when a stream supports Z stack display, which is controlled by the focuser.
        :param view_pos_init: (tuple) The view position on init, If None and if stage
            component is present, use the stage position. Otherwise set to (0, 0).
        :param fov_range: (2 tuples of len n)
            The first tuple contains the minimum values corresponding to each element in `value`,
            the second tuple contains the maximum values corresponding to each element in `value`.
        """

        super(StreamView, self).__init__(name)

        if stream_classes is None:
            self.stream_classes = (Stream,)
        else:
            self.stream_classes = stream_classes
        self._stage = stage

        self._projection_klass = projection_class

        # Two variations on adapting the content based on what the view shows.
        # They are only used as an _indication_ from the widgets, about what
        # is displayed. To change the area (zoom), use the .mpp .
        # TODO: need more generic API to report the FoV. Ideally, it would have
        # just something like .fov, .view_pos and .mpp. It would take care of
        # the hardware link that the viewport currently does.

        # .fov_hw allows the viewport to link the mpp/fov with the hardware
        # (which provides .horizontalFoV).
        self.fov_hw = fov_hw

        # .fov allows the viewport to report back the area shown (and actually
        # drawn, including the margins, via fov_buffer). This is used to update
        # the (static) streams with a projection which can be resized via .rect
        # and .mpp.
        self.fov = model.TupleContinuous((0.0, 0.0), cls=(int, float), range=fov_range)
        self.fov_buffer = model.TupleContinuous((0.0, 0.0), cls=(int, float), range=fov_range)
        self.fov_buffer.subscribe(self._onFovBuffer)

        # Will be created on the first time it's needed
        self._focus_thread = {}  # Focuser -> thread
        self._focus_queue = {}  # Focuser -> queue.Queue() of float (relative distance)

        # The real stage position, to be modified via moveStageToView()
        # it's a direct access from the stage, so looks like a dict of axes
        if stage:
            self.stage_pos = stage.position

            if view_pos_init is None:
                # the current center of the view, which might be different from
                # the stage
                pos = self.stage_pos.value
                view_pos_init = (pos["x"], pos["y"])
        else:
            view_pos_init = (0, 0)

        self.view_pos = model.ListVA(view_pos_init, unit="m")

        self._fstage_move = InstantaneousFuture() # latest future representing a move request

        # current density (meter per pixel, ~ scale/zoom level)
        # 1µm/px => ~large view of the sample (view width ~= 1000 px)
        self.mpp = FloatContinuous(1e-6, range=(10e-12, 10e-3), unit="m/px")
        # self.mpp.debug = True

        # How much one image is displayed on the other one. Value used by
        # StreamTree
        self.merge_ratio = FloatContinuous(0.5, range=[0, 1], unit="")
        self.merge_ratio.subscribe(self._onMergeRatio)

        # Streams to display (can be considered an implementation detail in most
        # cases)
        # Note: use addStream/removeStream for simple modifications
        self.stream_tree = StreamTree(merge=self.merge_ratio.value)
        # Only modify with this lock acquired:
        # TODO: Is this the source of the intermittent locking of the GUI when
        # Streams are active? If so, is there another/better way?
        self._streams_lock = threading.Lock()

        # TODO: list of annotations to display
        self.show_crosshair = model.BooleanVA(True)
        self.show_pixelvalue = model.BooleanVA(False)
        self.interpolate_content = model.BooleanVA(False)

        if zPos is not None:
            self.zPos = zPos

        self.mpp.subscribe(self._onMpp, init=True)
        self.view_pos.subscribe(self._onViewPos, init=True)

    def _onFovBuffer(self, fov):
        self._updateStreamsViewParams()

    def _onViewPos(self, view_pos):
        self._updateStreamsViewParams()

    def _onMpp(self, mpp):
        self._updateStreamsViewParams()

    def _updateStreamsViewParams(self):
        ''' Updates .rect and .mpp members of all streams based on the field of view of the buffer
        '''
        half_fov = (self.fov_buffer.value[0] / 2, self.fov_buffer.value[1] / 2)
        # view_rect is a tuple containing minx, miny, maxx, maxy
        view_rect = (
            self.view_pos.value[0] - half_fov[0],
            self.view_pos.value[1] - half_fov[1],
            self.view_pos.value[0] + half_fov[0],
            self.view_pos.value[1] + half_fov[1],
        )
        streams = self.stream_tree.getProjections()
        for stream in streams:
            if hasattr(stream, 'rect'): # the stream is probably pyramidal
                stream.rect.value = stream.rect.clip(view_rect)
                stream.mpp.value = stream.mpp.clip(self.mpp.value)

    def has_stage(self):
        return self._stage is not None

    def _getFocuserQueue(self, focuser):
        """
        return (Queue): queue to send move requests to the given focuser
        """
        try:
            return self._focus_queue[focuser]
        except KeyError:
            # Create a new thread and queue
            q = queue.Queue()
            self._focus_queue[focuser] = q

            t = threading.Thread(target=self._moveFocus, args=(q, focuser),
                                 name="Focus mover view %s/%s" % (self.name.value, focuser.name))
            # TODO: way to detect the view is not used and so we need to stop the thread?
            # (cf __del__?)
            t.daemon = True
            t.start()
            self._focus_thread[focuser] = t

            return q

    def _moveFocus(self, q, focuser):
        """
        Focuser thread
        """
        time_last_move = 0
        try:
            axis = focuser.axes["z"]
            try:
                rng = axis.range
            except AttributeError:
                rng = None

            if axis.canUpdate:
                # Update the target position on the fly
                logging.debug("Will be moving the focuser %s via position update", focuser.name)
            fpending = []  # pending futures (only used if axis.canUpdate)

            while True:
                # wait until there is something to do
                shift = q.get()
                if rng:
                    pos = focuser.position.value["z"]

                # rate limit to 20 Hz
                sleept = time_last_move + 0.05 - time.time()
                if sleept < -5:  # More than 5 s since last move = new focusing streak
                    # We always wait a bit, so that we don't start with a tiny move
                    sleept = 0.05
                else:
                    sleept = max(0.01, sleept)
                time.sleep(sleept)

                # Remove futures that are over and wait if too many moves pending
                while True:
                    fpending = [f for f in fpending if not f.done()]
                    if len(fpending) <= 2:
                        break

                    logging.info("Still %d pending futures for focuser %s",
                                 len(fpending), focuser.name)
                    try:
                        # Wait until all the moves but the last are over
                        fpending[-1].result()
                        # TODO: display errors for each failed move (not just 1 over 3)
                    except Exception:
                        logging.warning("Failed to apply focus move", exc_info=1)

                # Add more moves if there are already more
                try:
                    while True:
                        ns = q.get(block=False)
                        shift += ns
                except queue.Empty:
                    pass

                logging.debug(u"Moving focus '%s' by %f μm", focuser.name, shift * 1e6)

                # clip to the range
                if rng:
                    new_pos = pos + shift
                    new_pos = max(rng[0], min(new_pos, rng[1]))
                    req_shift = shift
                    shift = new_pos - pos
                    if abs(shift - req_shift) > 1e-9:
                        logging.info(u"Restricting focus move to %f µm as it reached the end",
                                     shift * 1e6)

                time_last_move = time.time()

                try:
                    if axis.canUpdate:
                        # Update the target position on the fly
                        fpending.append(focuser.moveRel({"z": shift}, update=True))
                    else:
                        # Wait until it's finished so that we don't accumulate requests,
                        # but instead only do requests of size "big enough"
                        focuser.moveRelSync({"z": shift})
                except Exception:
                    logging.info("Failed to apply focus move", exc_info=1)
        except Exception:
            logging.exception("Focus mover thread failed")

    def moveFocusRel(self, shift):
        """
        shift (float): position change in "virtual pixels".
            >0: toward up/right
            Note: "virtual pixel" represents the number of pixels, taking into
            account mouse movement and key context. So it can be different from
            the actual number of pixels that were moved by the mouse.
        return (float): actual distance moved by the focus in meter
        """
        # FIXME: "stop all axes" should also clear the queue

        # If streams have a z-level, we calculate the shift differently.

        if hasattr(self, "zPos"):

            # Multiplier found by testing based on the range of zPos
            # Moving the mouse 400 px moves through the whole range.
            k = abs(self.zPos.range[1] - self.zPos.range[0]) / 400
            val = k * shift

            old_pos = self.zPos.value
            new_pos = self.zPos.clip(self.zPos.value + val)
            self.zPos.value = new_pos
            logging.debug("Moving zPos to %f in range %s", self.zPos.value, self.zPos.range)
            return new_pos - old_pos

        # TODO: optimise by only updating focuser when the stream tree changes
        for s in self.getStreams():
            if s.should_update.value:
                focuser = s.focuser
                curr_s = s
                break
        else:
            logging.info("Trying to change focus while no stream is playing")
            return 0

        # TODO: optimise with the focuser
        # Find the depth of field (~ the size of one "focus step")
        for c in (curr_s.detector, curr_s.emitter):
            if model.hasVA(c, "depthOfField"):
                dof = c.depthOfField.value
                break
        else:
            logging.debug("No depth of field info found")
            dof = 1e-6  # m, not too bad value

        # positive == opt lens goes up == closer from the sample
        # k is a magical constant that allows to ensure a small move has a small
        # effect, and a big move has a significant effect.
        k = 50e-3  # 1/px
        val = dof * k * shift  # m
        assert(abs(val) < 0.01)  # a move of 1 cm is a clear sign of bug
        q = self._getFocuserQueue(focuser)
        q.put(val)
        return val

    def moveStageBy(self, shift):
        """
        Request a relative move of the stage
        pos (tuple of 2 float): X, Y offset in m
        :return (None or Future): a future (that allows to know when the move is finished)
        """
        if not self._stage:
            return None

        # TODO: Use the max FoV of the streams to determine what's a big
        # distance (because on the overview cam a move can be much bigger than
        # on a SEM image at high mag).

        # Check it makes sense (=> not too big)
        distance = math.hypot(*shift)
        if distance > MAX_SAFE_MOVE_DISTANCE:
            logging.error("Cancelling request to move by %f m (because > %f m)",
                          distance, MAX_SAFE_MOVE_DISTANCE)
            return
        elif distance < 0.1e-9:
            logging.debug("skipping move request of almost 0")
            return

        rel_move = {"x": shift[0], "y": shift[1]}
        current_pos = self._stage.position.value
        req_abs_move = {"x": current_pos["x"] + shift[0], "y": current_pos["y"] + shift[1]}  # Requested absolute move

        # If needed clip current movements in x/y direction to the maximum allowed stage limits
        stage_limits = self._getStageLimitsXY()
        if not stage_limits["x"][0] <= req_abs_move["x"] <= stage_limits["x"][1]:
            rel_move["x"] = max(stage_limits["x"][0], min(req_abs_move["x"], stage_limits["x"][1])) - current_pos["x"]
            logging.info("The movement of the stage in x direction is limited by the stage limits to %s mm." % (
                        rel_move["x"] * 1e3))

        if not stage_limits["y"][0] <= req_abs_move["y"] <= stage_limits["y"][1]:
            rel_move["y"] = max(stage_limits["y"][0], min(req_abs_move["y"], stage_limits["y"][1])) - current_pos["y"]
            logging.info("The movement of the stage in y direction is limited by the stage limits to %s mm." % (
                        rel_move["y"] * 1e3))

        # Only pass the "update" keyword if the actuator accepts it for sure
        # It should increase latency in case of slow moves (ex: closed-loop
        # stage that vibrate a bit when reaching target position).
        kwargs = {}
        if self._stage.axes["x"].canUpdate and self._stage.axes["y"].canUpdate:
            kwargs["update"] = True

        logging.debug("Requesting stage to move by %s m", rel_move)
        f = self._stage.moveRel(rel_move, **kwargs)
        self._fstage_move = f
        f.add_done_callback(self._on_stage_move_done)
        return f

    def moveStageToView(self):
        """ Move the stage to the current view_pos

        :return (None or Future): a future (that allows to know when the move is finished)

        Note: once the move is finished stage_pos will be updated (by the
        back-end)
        """
        if not self._stage:
            return

        view_pos = self.view_pos.value
        prev_pos = self.stage_pos.value
        shift = (view_pos[0] - prev_pos["x"], view_pos[1] - prev_pos["y"])
        return self.moveStageBy(shift)

    def moveStageTo(self, pos: Tuple[float, float]):
        """
        Request an absolute move of the stage to a given position

        pos (tuple of 2 float): X, Y absolute coordinates
        :return (None or Future): a future (that allows to know when the move is finished)
        """
        if not self._stage:
            return None

        if isinstance(pos, tuple):
            move = self.clipToStageLimits({"x": pos[0], "y": pos[1]})

        logging.debug("Requesting stage to move to %s mm in x direction and %s mm in y direction",
                      move["x"] * 1e3, move["y"] * 1e3)
        f = self._stage.moveAbs(move)
        self._fstage_move = f
        f.add_done_callback(self._on_stage_move_done)
        return f

    def clipToStageLimits(self, pos):
        """
        Clip current position in x/y direction to the maximum allowed stage limits.

        :param pos (dict): Position to be clipped with keys "x" and "y"
        :return(dict): Position clipped to the stage limits with keys "x" and "y"
        """
        if not self._stage:
            return pos

        stage_limits = self._getStageLimitsXY()
        if not stage_limits["x"][0] <= pos["x"] <= stage_limits["x"][1]:
            pos["x"] = max(stage_limits["x"][0], min(pos["x"], stage_limits["x"][1]))
            logging.info("Movements of the stage in x limited to %s m, restricting movement to %s m.",
                         stage_limits["x"], pos["x"])

        if not stage_limits["y"][0] <= pos["x"] <= stage_limits["y"][1]:
            pos["y"] = max(stage_limits["y"][0], min(pos["y"], stage_limits["y"][1]))
            logging.info("Movements of the stage in y limited to %s m, restricting movement to %s m.",
                         stage_limits["y"], pos["y"])
        return pos

    def _getStageLimitsXY(self):
        """
        Based on the physical stage limit and the area of the image used for imaging the stage limits are returned in
        a dict. (MD_POS_ACTIVE_RANGE defines the area which can be used for imaging)
        If no stage limits are defined an empty dict is returned.

        :return (dictionary): dict which contains the stage limits in x and y direction
        """
        stage_limits = {}
        # Physical stage limits
        if hasattr(self._stage.axes["x"], "range"):
            stage_limits["x"] = list(self._stage.axes["x"].range)
        if hasattr(self._stage.axes["y"], "range"):
            stage_limits["y"] = list(self._stage.axes["y"].range)

        # Area which can be used for imaging
        pos_active_range = self._stage.getMetadata().get(model.MD_POS_ACTIVE_RANGE, {})
        if "x" in pos_active_range:
            stage_limits = self._updateStageLimits(stage_limits, {"x": pos_active_range["x"]})
        if "y" in pos_active_range:
            stage_limits = self._updateStageLimits(stage_limits, {"y": pos_active_range["y"]})

        if not stage_limits:
            logging.info("No stage limits defined")
        return stage_limits

    def _updateStageLimits(self, stage_limits, new_limits):
        """
        Updates the stage limits dictionary with the intersection of both the existing and new limits. So that the
        updated limits comply with both defined limits.

        :param stage_limits (dict): Contains the limits for each axis of the stage which is limited.
        :param new_limits (dict): Contains new limits for the stage for one or multiple axis
        :return (dict): Contains the updated stage limits
        """
        for key in new_limits:
            if key in stage_limits:
                # Update the stage limits with the intersection of both the existing and new limits.
                stage_limits.update({key: [max(stage_limits[key][0], new_limits[key][0]),
                                           min(stage_limits[key][1], new_limits[key][1])]})
            else:
                stage_limits[key] = list(stage_limits[key])  # If key isn't already in stage limits

        return stage_limits

    def _on_stage_move_done(self, f):
        """
        Called whenever a stage move is completed
        """
        ex = f.exception()
        if ex:
            logging.warning("Stage move failed: %s", ex)

    def getStreams(self):
        """
        :return: [Stream] list of streams that are displayed in the view

        Do not modify directly, use addStream(), and removeStream().
        Note: use .stream_tree for getting the raw StreamTree (with the DataProjection)
        """
        ss = self.stream_tree.getProjections()
        # ss is a list of either Streams or DataProjections, so need to convert
        # back to only streams.
        return [s.stream if isinstance(s, DataProjection) else s for s in ss]

    def getProjections(self):
        """
        :return: [Stream] list of streams that are displayed in the view

        Do not modify directly, use addStream(), and removeStream().
        Note: use .stream_tree for getting the raw StreamTree (with the DataProjection)
        """
        ss = self.stream_tree.getProjections()
        return ss

    def addStream(self, stream):
        """
        Add a stream to the view. It takes care of updating the StreamTree
        according to the type of stream.
        stream (acq.stream.Stream): stream to add
        If the stream is already present, nothing happens
        """
        # check if the stream is already present
        if stream in self.stream_tree:
            logging.warning("Aborting the addition of a duplicate stream")
            return

        if not isinstance(stream, self.stream_classes):
            msg = "Adding incompatible stream '%s' to view '%s'. %s needed"
            logging.warning(msg, stream.name.value, self.name.value, self.stream_classes)

        if not hasattr(stream, 'image'):
            logging.debug("Creating a projection for stream %s", stream)
            stream = self._projection_klass(stream)

        # Find out where the stream should go in the streamTree
        # FIXME: manage sub-trees, with different merge operations
        # For now we just add it to the list of streams, with the only merge
        # operation possible
        with self._streams_lock:
            self.stream_tree.add_stream(stream)

            # subscribe to the stream's image
            if hasattr(stream, "image"):
                stream.image.subscribe(self._onNewImage)

                # if the stream already has an image, update now
                if stream.image.value is not None:
                    self._onNewImage(stream.image.value)
            else:
                logging.debug("No image found for stream %s", type(stream))

        if isinstance(stream, DataProjection):
            # sets the current mpp and viewport to the projection
            self._updateStreamsViewParams()

    def removeStream(self, stream):
        """
        Remove a stream from the view. It takes care of updating the StreamTree.
        stream (Stream): stream to remove
        If the stream is not present, nothing happens
        """

        with self._streams_lock:
            for node in self.stream_tree.getProjections():
                ostream = node.stream if isinstance(node, DataProjection) else node

                # check if the stream is still present on the stream list
                if stream == ostream:
                    # Stop listening to the stream changes
                    if hasattr(node, "image"):
                        node.image.unsubscribe(self._onNewImage)

                    # remove stream from the StreamTree()
                    # TODO: handle more complex trees
                    self.stream_tree.remove_stream(node)
                    # let everyone know that the view has changed
                    self.lastUpdate.value = time.time()
                    break

    def _onNewImage(self, im):
        """
        Called when one stream has im (DataArray)
        """
        # just let everyone know that the composited image has changed
        self.lastUpdate.value = time.time()

    def _onMergeRatio(self, ratio):
        """
        Called when the merge ratio is modified
        """
        # This actually modifies the root operator of the stream tree
        # It has effect only if the operator can do something with the "merge"
        # argument
        with self._streams_lock:
            self.stream_tree.kwargs["merge"] = ratio

        # just let everyone that the composited image has changed
        self.lastUpdate.value = time.time()

    def is_compatible(self, stream_cls):
        """ Check if the given stream class is compatible with this view.
        """
        return issubclass(stream_cls, self.stream_classes)


class MicroscopeView(StreamView):
    """
    Represents a view from a microscope and ways to alter it.
    It will stay centered on the stage position.
    """
    def __init__(self, name, stage=None, **kwargs):
        StreamView.__init__(self, name, stage=stage, **kwargs)
        if stage:
            self.stage_pos.subscribe(self._on_stage_pos)

    def _on_stage_pos(self, pos):
        # we want to recenter the viewports whenever the stage moves

        # Don't recenter if a stage move has been requested and on going
        # as view_pos is already at the (expected) final position
        if not self._fstage_move.done():
            return

        self.view_pos.value = [pos["x"], pos["y"]]

    def _on_stage_move_done(self, f):
        """
        Called whenever a stage move is completed
        """
        super(MicroscopeView, self)._on_stage_move_done(f)
        self._on_stage_pos(self.stage_pos.value)


class ContentView(StreamView):
    """
    Represents a view from a microscope but (almost) always centered on the
    content
    """
    def __init__(self, name, **kwargs):
        StreamView.__init__(self, name, **kwargs)

    def _onNewImage(self, im):
        # Don't recenter if a stage move has been requested and on going
        # as view_pos is already at the (expected) final position
        if self._fstage_move.done() and im is not None:
            # Move the center's view to the center of this new image
            try:
                pos = im.metadata[MD_POS]
            except KeyError:
                pass
            else:
                self.view_pos.value = pos

        super(ContentView, self)._onNewImage(im)

    # Note: we don't reset the view position at the end of the move. It will
    # only be reset on the next image after the end of the move (if it ever
    # comes). This is done on purpose to clearly show that the image displayed
    # is not yet at the place where the move finished.


class FixedOverviewView(StreamView):
    """
    A large FoV view which is used to display the previous positions reached
    (if possible) on top of an overview image of the sample.
    The main difference with the standard MicroscopeView is that it is not
    centered on the current stage position.
    """
    def __init__(self, name, **kwargs):
        StreamView.__init__(self, name, **kwargs)

        self.show_crosshair.value = False
        self.interpolate_content.value = False
        self.show_pixelvalue.value = False

        self.mpp.value = 10e-6
        self.mpp.range = (1e-10, 1)


class FeatureView(MicroscopeView):
    """
    A stream view with optional bookmarked features
    """
    def __init__(self, name, stage=None, **kwargs):
        MicroscopeView.__init__(self, name, stage=stage, **kwargs)
        # booleanVA to toggle showing/hiding the features
        self.showFeatures = model.BooleanVA(True)


class FeatureOverviewView(FeatureView):
    """
    A large FoV view which is used to display an overview map with optional bookmarked features
    """
    def __init__(self, name, stage=None, **kwargs):
        FeatureView.__init__(self, name, stage=stage, **kwargs)

        self.show_crosshair.value = False
        self.mpp.value = 10e-6
        self.mpp.range = (1e-10, 1)

        # add stage-bare, convert-stage here, so we can convert the stage coordinates?

    def _on_stage_pos(self, pos):
        # we DON'T want to recenter the viewports whenever the stage moves
        # (contrarily to the standard MicroscopeView/FeatureView)
        pass
