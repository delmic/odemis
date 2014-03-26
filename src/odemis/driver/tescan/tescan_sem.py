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
from odemis.dataio import hdf5
from odemis.util import img
import os.path
import threading
import time
import weakref
import time
from odemis.driver.tescan import sem
import Image


class TescanSEM(model.HwComponent):
    '''
    This is an extension of the model.HwComponent class. It instantiates the scanner 
    and se-detector children components and provides an update function for its 
    metadata. 
    '''

    def __init__(self, name, role, children, daemon=None, **kwargs):
        '''
        children (dict string->kwargs): parameters setting for the children.
            Known children are "scanner" and "detector"
            They will be provided back in the .children roattribute
        Raise an exception if the device cannot be opened
        '''
        # we will fill the set of children with Components later in ._children
        model.HwComponent.__init__(self, name, role, daemon=daemon, **kwargs)

        self._device = sem.Sem()
        # you can change the 'localhost' string and provide another SEM addres
        result = self._device.Connect('192.168.92.91', 8300)

        if result < 0:
            print("Error: unable to connect")
            return

        # we can read some parameter
        print('wd: ', self._device.GetWD())

        # tuple is returned if the function has more output arguments
        print('stage position: ', self._device.StgGetPosition())

        # let us take a look at the detector configuration
        print(self._device.DtEnumDetectors())

        # set the Probe Current - this is equivalent to BI in SEM Generation 3
        self._device.SetPCIndex(10)

        # important: stop the scanning before we start scanning or before automatic procedures,
        # even before we configure the detectors
        self._device.ScStopScan()

        self._metadata = {model.MD_HW_NAME: "TescanSEM"}

        # create the scanner child
        try:
            kwargs = children["scanner"]
        except (KeyError, TypeError):
            raise KeyError("TescanSEM was not given a 'scanner' child")

        self._scanner = Scanner(parent=self, daemon=daemon, **kwargs)
        self.children.add(self._scanner)

        # create the scanner child
        try:
            kwargs = children["detector0"]
        except (KeyError, TypeError):
            raise KeyError("TescanSEM was not given a 'detector' child")
        self._detector = Detector(parent=self, daemon=daemon, **kwargs)
        self.children.add(self._detector)

    def updateMetadata(self, md):
        self._metadata.update(md)

    def terminate(self):
        """
        Must be called at the end of the usage. Can be called multiple times,
        but the component shouldn't be used afterward.
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
    def __init__(self, name, role, parent, **kwargs):
        # It will set up ._shape and .parent
        model.Emitter.__init__(self, name, role, parent=parent, **kwargs)

        self._shape = (2048, 2048)

        # next two values are just to determine the pixel size
        # Distance between borders if magnification = 1. It should be found out
        # via calibration. We assume that image is square, i.e., VFW = HFW
        self._hfw_nomag = 0.2752  # m

        # Allow the user to modify the value, to copy it from the SEM software
        mag = 1e3  # pretty random value which could be real
        self.parent._device.SetViewField(self._hfw_nomag * 1e03 / mag)
        self.magnification = model.FloatContinuous(mag, range=[1, 1e9], unit="")
        self.magnification.subscribe(self._onMagnification)

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
        # TODO: for now it's readonly because no rotation is supported
        self.rotation = model.FloatContinuous(0, [0, 2 * math.pi], unit="rad",
                                              readonly=True)

        self.dwellTime = model.FloatContinuous(1e-06, (1e-06, 1000), unit="s")

    def updateMetadata(self, md):
        # we share metadata with our parent
        self.parent.updateMetadata(md)

    def _onMagnification(self, mag):
        print "MAG"
        print mag
        # HFW to mm to comply with Tescan API
        self.parent._device.SetViewField(self._hfw_nomag * 1e03 / mag)
        self._updatePixelSize()

    def _onScale(self, s):
        self._updatePixelSize()

    def _updatePixelSize(self):
        """
        Update the pixel size using the scale, HFWNoMag and magnification
        """
        mag = self.magnification.value
        self.parent._metadata[model.MD_LENS_MAG] = mag

        pxs = (self._hfw_nomag / (self._shape[0] * mag),
               self._hfw_nomag / (self._shape[1] * mag))

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
        self.parent._device.DtEnable(self._channel, 1, 8)
        # now tell the engine to wait for scanning inactivity and auto procedure finish,
        # see the docs for details
        self.parent._device.SetWaitFlags(0x09)

        # adjust brigtness and contrast, read back the result
        self.parent._device.DtAutoSignal(0)
        print('gain/black: ', self.parent._device.DtGetGainBlack(0))

        self.data = SEMDataFlow(self, parent)
        self._acquisition_thread = None
        self._acquisition_lock = threading.Lock()
        self._acquisition_init_lock = threading.Lock()
        self._acquisition_must_stop = threading.Event()

        # The shape is just one point, the depth
#         idt = numpy.iinfo(str)
#         data_depth = idt.max - idt.min + 1
#         self._shape = (data_depth,)  # only one point

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
            with self._acquisition_init_lock:
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
        metadata = dict(self.parent._metadata)

        with self._acquisition_init_lock:
            pxs = self.parent._scanner.pixelSize.value  # m/px

            pxs_pos = self.parent._scanner.translation.value
            scale = self.parent._scanner.scale.value
            res = (self.parent._scanner.resolution.value[0],
                   self.parent._scanner.resolution.value[1])

            phy_pos = metadata.get(model.MD_POS, (0, 0))
            trans = self.parent._scanner.pixelToPhy(pxs_pos)
            updated_phy_pos = (phy_pos[0] + trans[0], phy_pos[1] + trans[1])

        #     x, y, z, rot, tilt = m.StgGetPosition()
        #     print x, y, z, rot, tilt
        #     m.StgMoveTo(x, y, z, 0, tilt)
            # start single frame acquisition at speed 3
            # self.parent._device.ScSetSpeed(1)
            shape = (2048, 2048)
            center = ((res[0] / 2), (res[1] / 2))
            l = center[0] + pxs_pos[1] - (res[1] / 2)
            t = center[1] + pxs_pos[0] - (res[0] / 2)
            r = center[0] + pxs_pos[1] + (res[1] / 2) - 1
            b = center[1] + pxs_pos[0] + (res[0] / 2) - 1
            self.parent._device.ScScanXY(0, res[0], res[1], l,
                                    t,
                                    r,
                                    b, 1, self.parent._scanner.dwellTime.value)

            print l, t, r, b
            print res
            # fetch the image (blocking operation), string is returned
            img_str = self.parent._device.FetchImage(0, res[0] * res[1])
            list_str = numpy.fromstring(img_str, dtype=numpy.uint8)
            sem_img = numpy.reshape(list_str, (res[0], res[1]))

            # we must stop the scanning even after single scan
            self.parent._device.ScStopScan()

            # save it in a file (only here the 'Image' library is required)
            # img = Image.fromstring("L", (res[0], res[1]), img_str)
            # img.save('python_image.png')
            # print "ready!"

#             shape = self.fake_img.shape
#             # Simulate drift
#             center = ((shape[0] / 2) - self.current_drift, (shape[1] / 2) + self.current_drift)
#
#             sim_img = self.fake_img[center[0] + pxs_pos[1] - (res[1] / 2):center[0] + pxs_pos[1] + (res[1] / 2):scale[0],
#                                     center[1] + pxs_pos[0] - (res[0] / 2):center[1] + pxs_pos[0] + (res[0] / 2):scale[1]]

            # update fake output metadata
            metadata[model.MD_POS] = updated_phy_pos
            metadata[model.MD_PIXEL_SIZE] = (pxs[0] * scale[0], pxs[1] * scale[1])
            metadata[model.MD_ACQ_DATE] = time.time()
            metadata[model.MD_ROTATION] = self.parent._scanner.rotation.value,
            metadata[model.MD_DWELL_TIME] = self.parent._scanner.dwellTime.value
            return model.DataArray(sem_img, metadata)

    def _acquire_thread(self, callback):
        """
        Thread that performs the SEM acquisition. It calculates and updates the
        center (e-beam) position based on the translation and provides the new 
        generated output to the Dataflow. 
        """
        try:
            while not self._acquisition_must_stop.is_set():
                dwelltime = self.parent._scanner.dwellTime.value
                resolution = self.parent._scanner.resolution.value
                duration = numpy.prod(resolution) * dwelltime
                print "TATA"
                callback(self._acquire_image())
                print "TOTO"
        except:
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

