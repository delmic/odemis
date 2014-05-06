  # -*- coding: utf-8 -*-
'''
Created on 4 Mar 2014

@author: Kimon Tsitsikas

Copyright © 2014 Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms 
of the GNU General Public License version 2 as published by the Free Software 
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; 
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR 
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with 
Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

import logging
import math
import numpy
from odemis import model, util
from odemis.model import isasync
import threading
import time
import weakref
from tescan import sem
import re

class TescanSEM(model.HwComponent):
    '''
    This is an extension of the model.HwComponent class. It instantiates the scanner 
    and se-detector children components and provides an update function for its 
    metadata. 
    '''

    def __init__(self, name, role, children, host, daemon=None, **kwargs):
        '''
        children (dict string->kwargs): parameters setting for the children.
            Known children are "scanner", "detector", "stage", "focus", "camera"
            and "pressure". They will be provided back in the .children roattribute
        host (string): ip address of the SEM server 
        Raise an exception if the device cannot be opened
        '''
        # we will fill the set of children with Components later in ._children
        model.HwComponent.__init__(self, name, role, daemon=daemon, **kwargs)

        self._device = sem.Sem()
        result = self._device.Connect(host, 8300)

        if result < 0:
            raise IOError()

        # Lock in order to synchronize all the child component functions
        # that acquire data from the SEM while we continuously acquire images
        self._acquisition_init_lock = threading.Lock()

        # important: stop the scanning before we start scanning or before automatic
        # procedures, even before we configure the detectors
        self._device.ScStopScan()

        self._metadata = {model.MD_HW_NAME: "TescanSEM"}

        # create the scanner child
        try:
            kwargs = children["scanner"]
        except (KeyError, TypeError):
            raise KeyError("TescanSEM was not given a 'scanner' child")

        self._scanner = Scanner(parent=self, daemon=daemon, **kwargs)
        self.children.add(self._scanner)

        # create the detector child
        try:
            kwargs = children["detector"]
        except (KeyError, TypeError):
            raise KeyError("TescanSEM was not given a 'detector' child")
        self._detector = Detector(parent=self, daemon=daemon, **kwargs)
        self.children.add(self._detector)

        # create the stage child
        try:
            kwargs = children["stage"]
        except (KeyError, TypeError):
            raise KeyError("TescanSEM was not given a 'stage' child")

        self._stage = Stage(parent=self, daemon=daemon, **kwargs)
        self.children.add(self._stage)

        # create the focus child
        try:
            kwargs = children["focus"]
        except (KeyError, TypeError):
            raise KeyError("TescanSEM was not given a 'focus' child")
        self._focus = EbeamFocus(parent=self, daemon=daemon, **kwargs)
        self.children.add(self._focus)

        # create the camera child
        try:
            kwargs = children["camera"]
        except (KeyError, TypeError):
            raise KeyError("TescanSEM was not given a 'camera' child")
        self._camera = ChamberView(parent=self, daemon=daemon, **kwargs)
        self.children.add(self._camera)

        # create the pressure child
        try:
            kwargs = children["pressure"]
        except (KeyError, TypeError):
            raise KeyError("TescanSEM was not given a 'pressure' child")
        self._pressure = ChamberPressure(parent=self, daemon=daemon, **kwargs)
        self.children.add(self._pressure)

    def updateMetadata(self, md):
        self._metadata.update(md)

    def terminate(self):
        """
        Must be called at the end of the usage. Can be called multiple times,
        but the component shouldn't be used afterwards.
        """
        # finish
        self._device.Disconnect()

class Scanner(model.Emitter):
    """
    This is an extension of the model.Emitter class. It contains Vigilant 
    Attributes and setters for magnification, pixel size, translation, resolution,
    scale, rotation and dwell time. Whenever one of these attributes is changed, 
    its setter also updates another value if needed e.g. when scale is changed, 
    resolution is updated, when resolution is changed, the translation is recentered 
    etc. Similarly it subscribes to the VAs of scale and magnification in order 
    to update the pixel size.
    """
    def __init__(self, name, role, parent, fov_range, **kwargs):
        # It will set up ._shape and .parent
        model.Emitter.__init__(self, name, role, parent=parent, **kwargs)

        self._shape = (2048, 2048)

        # This is the field of view when in Tescan Software magnification = 100
        # and working distance = 0,27 m (maximum WD of Mira TC). When working
        # distance is changed (for example when we focus) magnification mention
        # in odemis and Tescan software are expected to be different.
        self._hfw_nomag = 0.195565  # m

        # Get current field of view and compute magnification
        fov = self.parent._device.GetViewField() * 1e-03
        mag = self._hfw_nomag / fov

        # Field of view in Tescan is set in mm
        self.parent._device.SetViewField(self._hfw_nomag * 1e03 / mag)
        self.magnification = model.VigilantAttribute(mag, unit="", readonly=True)

        self.horizontalFOV = model.FloatContinuous(fov, range=fov_range, unit="m",
                                                   setter=self._setHorizontalFOV)
        self.horizontalFOV.subscribe(self._onHorizontalFOV)  # to update metadata

        # pixelSize is the same as MD_PIXEL_SIZE, with scale == 1
        # == smallest size/ between two different ebeam positions
        pxs = (self._hfw_nomag / (self._shape[0] * mag),
               self._hfw_nomag / (self._shape[1] * mag))
        self.pixelSize = model.VigilantAttribute(pxs, unit="m", readonly=True)

        # (.resolution), .translation, .rotation, and .scaling are used to
        # define the conversion from coordinates to a region of interest.

        # (float, float) in px => moves center of acquisition by this amount
        # independent of scale and rotation.
        tran_rng = [(-self._shape[0] / 2, -self._shape[1] / 2),
                    (self._shape[0] / 2, self._shape[1] / 2)]
        self.translation = model.TupleContinuous((0, 0), tran_rng,
                                              cls=(int, long, float), unit="",
                                              setter=self._setTranslation)

        # .resolution is the number of pixels actually scanned. If it's less than
        # the whole possible area, it's centered.
        resolution = (self._shape[0] // 8, self._shape[1] // 8)
        self.resolution = model.ResolutionVA(resolution, [(1, 1), self._shape],
                                             setter=self._setResolution)
        self._resolution = resolution

        # (float, float) as a ratio => how big is a pixel, compared to pixelSize
        # it basically works the same as binning, but can be float
        # (Default to scan the whole area)
        self._scale = (self._shape[0] / resolution[0], self._shape[1] / resolution[1])
        self.scale = model.TupleContinuous(self._scale, [(1, 1), self._shape],
                                           cls=(int, long, float),
                                           unit="", setter=self._setScale)
        self.scale.subscribe(self._onScale, init=True)  # to update metadata

        # (float) in rad => rotation of the image compared to the original axes
        x, y, z, rot, tilt = parent._device.StgGetPosition()
        rotation = (rot * math.pi) / 180
        rot_range = (0, 2 * math.pi)
        self.rotation = model.FloatContinuous(rotation, rot_range, unit="rad")
        self.rotation.subscribe(self._onRotation)

        self.dwellTime = model.FloatContinuous(1e-06, (1e-06, 1000), unit="s")
        self.dwellTime.subscribe(self._onDwellTime)

        # Range is according to min and max voltages accepted by Tescan API
        volt_range = self.GetVoltagesRange()
        volt = self.parent._device.HVGetVoltage()
        self.accelVoltage = model.FloatContinuous(volt, volt_range, unit="V")
        self.accelVoltage.subscribe(self._onVoltage)

        # 0 turns off the e-beam, 1 turns it on
        power_choices = set([0, 1])
        self._power = self.parent._device.HVGetBeam()  # Don't change state
        self.power = model.IntEnumerated(self._power, power_choices, unit="",
                                  setter=self._setPower)

        # Blanker is automatically enabled when no scanning takes place
        # TODO it may cause time overhead, check on testing => If so put some
        # small timeout (~ a few seconds) before blanking the beam.
        # self.parent._device.ScSetBlanker(0, 2)

        # Enumerated float with respect to the PC indexes of Tescan API
        self._list_currents = self.GetProbeCurrents()
        pc_choices = set(self._list_currents)
        # We use the current PC
        self._probeCurrent = self._list_currents[self.parent._device.GetPCIndex() - 1]
        self.probeCurrent = model.FloatEnumerated(self._probeCurrent, pc_choices, unit="A",
                                  setter=self._setPC)


    def updateMetadata(self, md):
        # we share metadata with our parent
        self.parent.updateMetadata(md)

    def _onHorizontalFOV(self, s):
        # Update current pixelSize and magnification
        self._updatePixelSize()
        self._updateMagnification()

    def _setHorizontalFOV(self, value):
        # FOV to mm to comply with Tescan API
        self.parent._device.SetViewField(value * 1e03)

        # Ensure fov odemis field always shows the right value
        # Also useful in case fov value that we try to set is
        # out of range
        with self.parent._acquisition_init_lock:
            cur_fov = self.parent._device.GetViewField() * 1e-03
            value = cur_fov

        return value

    def _updateMagnification(self):

        # it's read-only, so we change it only via _value
        mag = self._hfw_nomag / self.horizontalFOV.value
        self.magnification._value = mag
        self.magnification.notify(mag)

    def _onDwellTime(self, dt):
        # TODO interrupt current scanning when dwell time is changed
        # ScStopScan does not work this way
        pass

    def _onRotation(self, rot):
        move = {'rz':rot}
        self.parent._stage.moveAbs(move)

    def _onVoltage(self, volt):
        self.parent._device.HVSetVoltage(volt)
        # Adjust brightness and contrast
        # TODO only on great change
        with self.parent._acquisition_init_lock:
            self.parent._device.DtAutoSignal(self.parent._detector._channel)

    def _setPower(self, value):
        powers = self.power.choices

        self._power = util.find_closest(value, powers)
        if self._power == 0:
            self.parent._device.HVBeamOff()
        else:
            self.parent._device.HVBeamOn()
        return self._power

    def _setPC(self, value):
        currents = self.probeCurrent.choices

        self._probeCurrent = util.find_closest(value, currents)
        self._indexCurrent = util.index_closest(value, self._list_currents)

        # Set the corresponding current index to Tescan SEM
        self.parent._device.SetPCIndex(self._indexCurrent + 1)
        # Adjust brightness and contrast
        with self.parent._acquisition_init_lock:
            self.parent._device.DtAutoSignal(self.parent._detector._channel)

        return self._probeCurrent

    def GetVoltagesRange(self):
        """
        return (list of float): accelerating voltage values ordered by index
        """
        voltages = []
        avs = self.parent._device.HVEnumIndexes()
        vol = re.findall(r'\=(.*?)\n', avs)
        for i in enumerate(vol):
            voltages.append(float(i[1]))
        volt_range = (voltages[0], voltages[-2])
        return volt_range

    def GetProbeCurrents(self):
        """
        return (list of float): probe current values ordered by index
        """
        currents = []
        pcs = self.parent._device.EnumPCIndexes()
        cur = re.findall(r'\=(.*?)\n', pcs)
        for i in enumerate(cur):
            # picoamps to amps
            currents.append(float(i[1]) * 1e-12)
        return currents

    def _onScale(self, s):
        self._updatePixelSize()

    def _updatePixelSize(self):
        """
        Update the pixel size using the scale, HFWNoMag and magnification
        """
        fov = self.horizontalFOV.value

        pxs = (fov / self._shape[0],
               fov / self._shape[1])

        # it's read-only, so we change it only via _value
        self.pixelSize._value = pxs
        self.pixelSize.notify(pxs)

        # If scaled up, the pixels are bigger
        pxs_scaled = (pxs[0] * self.scale.value[0], pxs[1] * self.scale.value[1])
        self.parent._metadata[model.MD_PIXEL_SIZE] = pxs_scaled

    def _setScale(self, value):
        """
        value (1 < float, 1 < float): increase of size between pixels compared to
         the original pixel size. It will adapt the translation and resolution to
         have the same ROI (just different amount of pixels scanned)
        return the actual value used
        """
        prev_scale = self._scale
        self._scale = value

        # adapt resolution so that the ROI stays the same
        change = (prev_scale[0] / self._scale[0],
                  prev_scale[1] / self._scale[1])
        old_resolution = self.resolution.value
        new_resolution = (max(int(round(old_resolution[0] * change[0])), 1),
                          max(int(round(old_resolution[1] * change[1])), 1))
        # no need to update translation, as it's independent of scale and will
        # be checked by setting the resolution.
        self.resolution.value = new_resolution  # will call _setResolution()

        return value

    def _setResolution(self, value):
        """
        value (0<int, 0<int): defines the size of the resolution. If the 
         resolution is not possible, it will pick the most fitting one. It will
         recenter the translation if otherwise it would be out of the whole
         scanned area.
        returns the actual value used
        """
        max_size = (int(self._shape[0] // self._scale[0]),
                    int(self._shape[1] // self._scale[1]))

        # at least one pixel, and at most the whole area
        size = (max(min(value[0], max_size[0]), 1),
                max(min(value[1], max_size[1]), 1))
        self._resolution = size

        # setting the same value means it will recheck the boundaries with the
        # new resolution, and reduce the distance to the center if necessary.
        self.translation.value = self.translation.value
        return size

    def _setTranslation(self, value):
        """
        value (float, float): shift from the center. It will always ensure that
          the whole ROI fits the screen.
        returns actual shift accepted
        """
        # compute the min/max of the shift. It's the same as the margin between
        # the centered ROI and the border, taking into account the scaling.
        max_tran = ((self._shape[0] - self._resolution[0] * self._scale[0]) / 2,
                    (self._shape[1] - self._resolution[1] * self._scale[1]) / 2)

        # between -margin and +margin
        tran = (max(min(value[0], max_tran[0]), -max_tran[0]),
                max(min(value[1], max_tran[1]), -max_tran[1]))
        return tran

    def pixelToPhy(self, px_pos):
        """
        Converts a position in pixels to physical (at the current magnification)
        Note: the convention is that in internal coordinates Y goes down, while
        in physical coordinates, Y goes up.
        px_pos (tuple of 2 floats): position in internal coordinates (pixels)
        returns (tuple of 2 floats): physical position in meters 
        """
        pxs = self.pixelSize.value  # m/px
        phy_pos = (px_pos[0] * pxs[0], -px_pos[1] * pxs[1])  # - to invert Y
        return phy_pos

class Detector(model.Detector):
    """
    This is an extension of model.Detector class. It performs the main functionality 
    of the SEM. It sets up a Dataflow and notifies it every time that an SEM image 
    is captured.
    """
    def __init__(self, name, role, parent, channel, **kwargs):
        """
        Note: parent should have a child "scanner" already initialised
        """
        # It will set up ._shape and .parent
        model.Detector.__init__(self, name, role, parent=parent, **kwargs)

        # select detector and enable channel
        self._channel = channel
        self.parent._device.DtSelect(self._channel, 0)
        self.parent._device.DtEnable(self._channel, 1, 16)  # 16 bits
        # now tell the engine to wait for scanning inactivity and auto procedure finish,
        # see the docs for details
        self.parent._device.SetWaitFlags(0x08 or 0x09)

        # adjust brightness and contrast
        self.parent._device.DtAutoSignal(self._channel)

        self._lock = self.parent._acquisition_init_lock

        self.data = SEMDataFlow(self, parent)
        self._acquisition_thread = None
        self._acquisition_lock = threading.Lock()
        # self._acquisition_init_lock = threading.Lock()
        self._acquisition_must_stop = threading.Event()

        # The shape is just one point, the depth
        self._shape = (2 ** 16,)  # only one point

    def start_acquire(self, callback):
        with self._acquisition_lock:
            self._wait_acquisition_stopped()
            target = self._acquire_thread
            self._acquisition_thread = threading.Thread(target=target,
                    name="TescanSEM acquire flow thread",
                    args=(callback,))
            self._acquisition_thread.start()

    def stop_acquire(self):
        with self._acquisition_lock:
            with self._lock:
                self._acquisition_must_stop.set()

    def _wait_acquisition_stopped(self):
        """
        Waits until the acquisition thread is fully finished _iff_ it was requested
        to stop.
        """
        # "if" is to not wait if it's already finished
        if self._acquisition_must_stop.is_set():
            logging.debug("Waiting for thread to stop.")
            self._acquisition_thread.join(10)  # 10s timeout for safety
            if self._acquisition_thread.isAlive():
                logging.exception("Failed to stop the acquisition thread")
                # Now let's hope everything is back to normal...
            # ensure it's not set, even if the thread died prematurely
            self._acquisition_must_stop.clear()

    def _acquire_image(self):
        """
        Acquires the SEM image based on the translation, resolution and
        current drift.
        """

        with self._lock:
            pxs = self.parent._scanner.pixelSize.value  # m/px

            pxs_pos = self.parent._scanner.translation.value
            scale = self.parent._scanner.scale.value
            res = (self.parent._scanner.resolution.value[0],
                   self.parent._scanner.resolution.value[1])

            metadata = dict(self.parent._metadata)
            phy_pos = metadata.get(model.MD_POS, (0, 0))
            trans = self.parent._scanner.pixelToPhy(pxs_pos)
            updated_phy_pos = (phy_pos[0] + trans[0], phy_pos[1] + trans[1])

            # update changed metadata
            metadata[model.MD_POS] = updated_phy_pos
            metadata[model.MD_PIXEL_SIZE] = (pxs[0] * scale[0], pxs[1] * scale[1])
            metadata[model.MD_ACQ_DATE] = time.time()
            metadata[model.MD_ROTATION] = self.parent._scanner.rotation.value,
            metadata[model.MD_DWELL_TIME] = self.parent._scanner.dwellTime.value

            center = (res[0] / 2, res[1] / 2)
            l = center[0] + pxs_pos[1] - (res[1] / 2)
            t = center[1] + pxs_pos[0] - (res[0] / 2)
            r = center[0] + pxs_pos[1] + (res[1] / 2) - 1
            b = center[1] + pxs_pos[0] + (res[0] / 2) - 1

            # Prevent scanning via the standard UI (as it prevents us from
            # getting the acquisition result), and tell the user why.
            self.parent._device.ProgressShow("Odemis scanning",
                        "Odemis scanning in progress, pause Odemis acquisition "
                        "to access this interface.",
                        0, 1, 0, 100)
            self.parent._device.ScScanXY(0, res[0], res[1],
                                 l, t, r, b, 1,
                                 self.parent._scanner.dwellTime.value * 1e9)

            # fetch the image (blocking operation), ndarray is returned
            sem_img = self.parent._device.FetchArray(0, res[0] * res[1])
            sem_img.shape = res

            # we must stop the scanning even after single scan
            self.parent._device.ScStopScan()

            return model.DataArray(sem_img, metadata)

    def _acquire_thread(self, callback):
        """
        Thread that performs the SEM acquisition. It calculates and updates the
        center (e-beam) position based on the translation and provides the new 
        generated output to the Dataflow. 
        """
        try:
            while not self._acquisition_must_stop.is_set():
                callback(self._acquire_image())
        except Exception:
            logging.exception("Unexpected failure during image acquisition")
        finally:
            logging.debug("Acquisition thread closed")
            self._acquisition_must_stop.clear()

    def updateMetadata(self, md):
        # we share metadata with our parent
        self.parent.updateMetadata(md)

class SEMDataFlow(model.DataFlow):
    """
    This is an extension of model.DataFlow. It receives notifications from the 
    detector component once the SEM output is captured. This is the dataflow to 
    which the SEM acquisition streams subscribe.
    """
    def __init__(self, detector, sem):
        """
        detector (semcomedi.Detector): the detector that the dataflow corresponds to
        sem (semcomedi.SEMComedi): the SEM
        """
        model.DataFlow.__init__(self)
        self.component = weakref.ref(detector)

    # start/stop_generate are _never_ called simultaneously (thread-safe)
    def start_generate(self):
        try:
            self.component().start_acquire(self.notify)
        except ReferenceError:
            # sem/component has been deleted, it's all fine, we'll be GC'd soon
            pass

    def stop_generate(self):
        try:
            self.component().stop_acquire()
            # Note that after that acquisition might still go on for a short time
        except ReferenceError:
            # sem/component has been deleted, it's all fine, we'll be GC'd soon
            pass

class Stage(model.Actuator):
    """
    This is an extension of the model.Actuator class. It provides functions for
    moving the Tescan stage and updating the position. 
    """
    def __init__(self, name, role, parent, **kwargs):
        """
        axes (set of string): names of the axes
        """
        axes_def = {}
        self._position = {}

        rng = [-0.1, 0.1]
        axes_def["x"] = model.Axis(unit="m", range=rng)
        axes_def["y"] = model.Axis(unit="m", range=rng)
        rng_rot = [0, 2 * math.pi]
        axes_def["rz"] = model.Axis(unit="rad", range=rng_rot)

        x, y, z, rot, tilt = parent._device.StgGetPosition()
        self._position["x"] = -x * 1e-3
        self._position["y"] = -y * 1e-3
        self.init_z = -z * 1e-3
        # degrees to rad
        self._position["rz"] = (rot * math.pi) / 180

        model.Actuator.__init__(self, name, role, parent=parent, axes=axes_def, **kwargs)

        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute(
                                    self._applyInversionAbs(self._position),
                                    unit="m", readonly=True)

        # First calibrate
        self.parent._device.StgCalibrate()

    def _updatePosition(self):
        """
        update the position VA
        """
        # it's read-only, so we change it via _value
        self.position._value = self._applyInversionAbs(self._position)
        self.position.notify(self.position.value)

    @isasync
    def moveRel(self, shift):
        # TODO add limits to position change
        shift = self._applyInversionRel(shift)

        for axis, change in shift.items():
            if not axis in shift:
                raise ValueError("Axis '%s' doesn't exist." % str(axis))
            self._position[axis] += change

            if (self._position[axis] < self.axes[axis].range[0] or
                self._position[axis] > self.axes[axis].range[1]):
                logging.warning("moving axis %s to %f, outside of range %r",
                                axis, self._position[axis], self.axes[axis].range)
            else:
                logging.info("moving axis %s to %f", axis, self._position[axis])

        # FIXME: move this code to a separate function, and return a Future
        # cf tlaptfm for example. Also, better to use StgMove, or update
        # _position, once executing the code.

        # Perform move through Tescan API
        # Position from m to mm and inverted
        # print self.parent._device.TcpGetSWVersion()
        self.parent._device.StgMoveTo(-self._position["x"] * 1e3,
                                    - self._position["y"] * 1e3,
                                    self.init_z * 1e3,
                                    (self._position["rz"] * 180) / math.pi)
        self._updatePosition() # TODO: possibly, during the move, update the
        # position regularly (~ 5Hz)


        return model.InstantaneousFuture()

    @isasync
    def moveAbs(self, pos):
        pos = self._applyInversionAbs(pos)

        for axis, new_pos in pos.items():
            if not axis in pos:
                raise ValueError("Axis '%s' doesn't exist." % str(axis))
            change = self._position[axis] - new_pos
            self._position[axis] = new_pos
            logging.info("moving axis %s to %f", axis, self._position[axis])

        # FIXME: move to a future
        # Perform move through Tescan API
        # Position from m to mm and inverted
        self.parent._device.StgMoveTo(-self._position["x"] * 1e3,
                                    - self._position["y"] * 1e3,
                                    self.init_z * 1e3,
                                    (self._position["rz"] * 180) / math.pi)
        self._updatePosition()
        return model.InstantaneousFuture()

    def stop(self, axes=None):
        # TODO empty the queue for the given axes
        # FIXME: STOP for real!
        logging.warning("Stopping all axes: %s", ", ".join(self.axes))
        return

class EbeamFocus(model.Actuator):
    """
    This is an extension of the model.Actuator class. It provides functions for
    adjusting the ebeam focus by changing the working distance i.e. the distance 
    between the end of the objective and the surface of the observed specimen 
    """
    def __init__(self, name, role, parent, axes, ranges=None, **kwargs):
        assert len(axes) > 0
        if ranges is None:
            ranges = {}

        axes_def = {}
        self._position = {}
        init_speed = {}

        # Just z axis
        a = axes[0]
        # TODO: possible to get the range via the API? Otherwise, explain where
        # does these value come from? Probably the highest point is 0, and lowest
        # is -270 mm (according to axis
        rng = ranges.get(a, [0, 270e-3])
        axes_def[a] = model.Axis(unit="m", range=rng, speed=[0., 10.])

        # start at the centre
        self._position[a] = parent._device.GetWD()
        # FIXME: if we don't support speed => don't provide it
        init_speed[a] = 10.0  # we are super fast!

        model.Actuator.__init__(self, name, role, parent=parent, axes=axes_def, **kwargs)
        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute(
                                    self._applyInversionAbs(self._position),
                                    unit="m", readonly=True)

        self.speed = model.MultiSpeedVA(init_speed, [0., 10.], "m/s")

    def _updatePixelSize(self):
        """
        Update the pixel size using the working distance
        """
        # FIXME: merge with _scanner._updatePixelSize()? Or explain how come
        # the two methods don't step on each other toes...
        scanner = self.parent._scanner
        mag = self._position["z"] / scanner._hfw_nomag
        self.parent._metadata[model.MD_LENS_MAG] = mag

        pxs = (scanner._hfw_nomag / (scanner._shape[0] * mag),
               scanner._hfw_nomag / (scanner._shape[1] * mag))

        # it's read-only, so we change it only via _value
        scanner.pixelSize._value = pxs
        scanner.pixelSize.notify(pxs)

        # If scaled up, the pixels are bigger
        pxs_scaled = (pxs[0] * scanner.scale.value[0], pxs[1] * scanner.scale.value[1])
        self.parent._metadata[model.MD_PIXEL_SIZE] = pxs_scaled

    def _updatePosition(self):
        """
        update the position VA
        """
        # it's read-only, so we change it via _value
        self.position._value = self._applyInversionAbs(self._position)
        self.position.notify(self.position.value)

    @isasync
    def moveRel(self, shift):
        shift = self._applyInversionRel(shift)
        time_start = time.time()
        maxtime = 0
        for axis, change in shift.items():
            if not axis in shift:
                raise ValueError("Axis '%s' doesn't exist." % str(axis))

            # TODO GetWD to stay updated to changes made via Tescan sw
            self._position[axis] += change
            if (self._position[axis] < self.axes[axis].range[0] or
                self._position[axis] > self.axes[axis].range[1]):
                logging.warning("moving axis %s to %f, outside of range %r",
                                axis, self._position[axis], self.axes[axis].range)
            else:
                logging.info("moving axis %s to %f", axis, self._position[axis])
            maxtime = max(maxtime, abs(change) / self.speed.value[axis])

        # Perform move through Tescan API
        # Position from m to mm and inverted
        self.parent._device.SetWD(self._position["z"] * 1e03)

        time_end = time_start + maxtime
        self._updatePosition()
        self._updatePixelSize()
        # TODO queue the move and pretend the position is changed only after the given time
        return model.InstantaneousFuture()

    @isasync
    def moveAbs(self, pos):
        pos = self._applyInversionAbs(pos)
        time_start = time.time()
        maxtime = 0
        for axis, new_pos in pos.items():
            if not axis in pos:
                raise ValueError("Axis '%s' doesn't exist." % str(axis))
            change = self._position[axis] - new_pos
            self._position[axis] = new_pos
            logging.info("moving axis %s to %f", axis, self._position[axis])
            maxtime = max(maxtime, abs(change) / self.speed.value[axis])

        # Perform move through Tescan API
        # Position from m to mm and inverted
        self.parent._device.SetWD(self._position["z"] * 1e03)

        # TODO stop add this move
        time_end = time_start + maxtime
        self._updatePosition()
        self._updatePixelSize()
        return model.InstantaneousFuture()

    def stop(self, axes=None):
        # TODO empty the queue for the given axes
        # FIXME: if not possible to stop, just put a logging.warning that it will not be
        # stopped, and empty the futures queue.
        logging.warning("Stopping all axes: %s", ", ".join(self.axes))
        return

class ChamberView(model.DigitalCamera):
    """
    Represents one chamber camera - chamberscope. Provides video consisted of
    static images sent in regular intervals.
    This implementation is for the Tescan.
    """
    def __init__(self, name, role, parent, **kwargs):
        """
        Initialises the device.
        Raise an exception if the device cannot be opened.
        """
        model.DigitalCamera.__init__(self, name, role, parent=parent, **kwargs)
        # FIXME: does enabling the camera has any drawback(eg: light on?), if
        # so, it should be enabled only when acquiring.
        self.parent._device.CameraEnable(0, 0.05, 5, 0) # TODO: what are these values?

        self._shape = (2 ** 8,) # FIXME: only one point?? Should be the max X, Y + depth

        self.acquisition_lock = threading.Lock()
        self.acquire_must_stop = threading.Event()
        self.acquire_thread = None

        self.data = ChamberDataFlow(self)

        logging.debug("Camera component ready to use.")

    def Shutdown(self):
        self.parent._device.CameraDisable()

    def GetStatus(self):
        """
        return int: chamber camera status, 0 - off, 1 - on
        """
        status = self.parent._device.CameraGetStatus(0) # TODO: what is 0?
        return status[0]

    def start_flow(self, callback):
        """
        Set up the chamber camera and start acquiring images.
        callback (callable (DataArray) no return):
         function called for each image acquired
        """
        # if there is a very quick unsubscribe(), subscribe(), the previous
        # thread might still be running
        self.wait_stopped_flow()  # no-op is the thread is not running
        self.acquisition_lock.acquire()

        assert(self.GetStatus() == 1)  # Just to be sure

        target = self._acquire_thread_continuous
        self.acquire_thread = threading.Thread(target=target,
                name="chamber camera acquire flow thread",
                args=(callback,))
        self.acquire_thread.start()

    def _acquire_thread_continuous(self, callback):
        """
        The core of the acquisition thread. Runs until acquire_must_stop is set.
        """
        try:
            while not self.acquire_must_stop.is_set():
                width, height, img_str = self.parent._device.FetchCameraImage(0)
                sem_img = numpy.frombuffer(img_str, dtype=numpy.uint8)
                sem_img.shape = (width, height) # FIXME: why not height, width?
                array = model.DataArray(sem_img)
                # first we wait ourselves the typical time (which might be very long)
                # while detecting requests for stop
                if self.acquire_must_stop.wait(5000): # FIXME: why 5000s?!
                    break

                callback(self._transposeDAToUser(array))

        except:
            logging.exception("Failure during acquisition")
        finally:
            self.acquisition_lock.release()
            logging.debug("Acquisition thread closed")
            self.acquire_must_stop.clear()

    def wait_stopped_flow(self):
        """
        Waits until the end acquisition of a flow of images. Calling from the
         acquisition callback is not permitted (it would cause a dead-lock).
        """
        # "if" is to not wait if it's already finished
        if self.acquire_must_stop.is_set():
            self.acquire_thread.join(10)  # 10s timeout for safety
            if self.acquire_thread.isAlive():
                raise OSError("Failed to stop the acquisition thread")
            # ensure it's not set, even if the thread died prematurately
            self.acquire_must_stop.clear()

    def terminate(self):
        """
        Must be called at the end of the usage
        """
        self.Shutdown()

class ChamberDataFlow(model.DataFlow):
    def __init__(self, camera):
        """
        camera: chamber camera instance ready to acquire images
        """
        model.DataFlow.__init__(self)
        self.component = weakref.ref(camera)

    def start_generate(self):
        comp = self.component()
        if comp is None:
            return
        comp.start_flow(self.notify)

    def stop_generate(self):
        comp = self.component()
        if comp is None:
            return
        comp.req_stop_flow()

class ChamberPressure(model.Actuator):
    """
    This is an extension of the model.Actuator class. It provides functions for
    adjusting the chamber pressure. It actually allows the user to evacuate or
    vent the chamber and get the current pressure of it.
    """
    def __init__(self, name, role, parent, axes, ranges=None, **kwargs):
        # TODO: fix the axis name to "pressure"

        if ranges is None:
            ranges = {}

        axes_def = {}
        self._position = {}

        # Just z axis
        a = axes[0]
        # 1 for evacuated, 0 for vented chamber
        # FIXME: axis choices is like : {0: 'vented', 1:'pumped'}
        _dict = dict([('vented', 0), ('pumped', 1)])
        axes_def[a] = model.Axis(unit="", range=_dict, speed=[0., 10.])

        # VA that retains the current chamber pressure
        pressure = parent._device.VacGetPressure(0)
        # TODO: is it possible to get the range from
        self.chamberPressure = model.FloatContinuous(pressure, (1e-4, 1e6), unit="Pa")

        # Try to decide if we are currently pumped or vented
        if parent._device.VacGetStatus() == 0 and pressure < 1:
            self._position[a] = 1
        else:
            self._position[a] = 0
            
        model.Actuator.__init__(self, name, role, parent=parent, axes=axes_def, **kwargs)
        # RO, as to modify it the client must use .moveAbs()
        self.position = model.VigilantAttribute(
                                    self._applyInversionAbs(self._position),
                                    unit="", readonly=True)

    def _updatePosition(self):
        """
        update the position VA
        """
        # it's read-only, so we change it via _value
        self.position._value = self._applyInversionAbs(self._position)
        self.position.notify(self.position.value)

    @isasync
    def moveRel(self, shift):
        # FIXME: for enumerated axis, moveRel should report a warning, and call
        # moveAbs
        shift = self._applyInversionRel(shift)
        time_start = time.time()
        maxtime = 0
        for axis, change in shift.items():
            if not axis in shift:
                raise ValueError("Axis '%s' doesn't exist." % str(axis))

            self._position[axis] += change
            if (self._position[axis] < self.axes[axis].range[0] or
                self._position[axis] > self.axes[axis].range[1]):
                logging.warning("moving axis %s to %f, outside of range %r",
                                axis, self._position[axis], self.axes[axis].range)
            else:
                logging.info("moving axis %s to %f", axis, self._position[axis])
            maxtime = max(maxtime, abs(change) / self.speed.value[axis])

        # Make sure we update with the initial and final chamber pressure
        self.chamberPressure.value = self.parent._device.VacGetPressure(0)
        if self._position["z"] == 1:
            self.parent._device.VacPump()
        else:
            self.parent._device.VacVent()
        # TODO add timeout
        while not self.parent._device.VacGetStatus() == 0:
            # Update chamber pressure until pumping/venting process is done
            self.chamberPressure.value = self.parent._device.VacGetPressure(0)
        self.chamberPressure.value = self.parent._device.VacGetPressure(0)

        time_end = time_start + maxtime
        # Update only once we are in the required state
        self._updatePosition()
        # TODO queue the move and pretend the position is changed only after the given time
        return model.InstantaneousFuture()

    @isasync
    def moveAbs(self, pos):
        pos = self._applyInversionAbs(pos)
        time_start = time.time()
        maxtime = 0
        for axis, new_pos in pos.items():
            if not axis in pos:
                raise ValueError("Axis '%s' doesn't exist." % str(axis))
            change = self._position[axis] - new_pos
            self._position[axis] = new_pos
            logging.info("moving axis %s to %f", axis, self._position[axis])
            maxtime = max(maxtime, abs(change) / self.speed.value[axis])

        # FIXME: move to separate code and call via future
        # Make sure we update with the initial and final chamber pressure
        self.chamberPressure.value = self.parent._device.VacGetPressure(0)
        if self._position["z"] == 1:
            self.parent._device.VacPump()
        else:
            self.parent._device.VacVent()
        # TODO add timeout
        while not self.parent._device.VacGetStatus() == 0:
            # Update chamber pressure until pumping/venting process is done
            self.chamberPressure.value = self.parent._device.VacGetPressure(0)
        self.chamberPressure.value = self.parent._device.VacGetPressure(0)

        # TODO stop add this move
        time_end = time_start + maxtime
        self._updatePosition()
        return model.InstantaneousFuture()

    def stop(self, axes=None):
        # TODO empty the queue for the given axes
        # FIXME: can the procedure be stopped? Probably? Maybe just a matter of
        # requesting the previous pressure?
        logging.warning("Stopping all axes: %s", ", ".join(self.axes))
        return
