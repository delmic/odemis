# -*- coding: utf-8 -*-
'''
Created on 28 Jul 2014

@author: Éric Piel

Copyright © 2014-2022 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
# Fake component for testing purpose

from past.builtins import long
import logging
import time

from odemis import model
import threading
import weakref


class MockComponent(model.HwComponent):
    """
    A very special component which does nothing but can pretend to be any component
    It's used for validation of the instantiation model.
    Do not use or inherit when writing a device driver!
    """
    def __init__(self, name, role, _realcls, parent=None, children=None, _vas=None, daemon=None, **kwargs):
        """
        _realcls (class): the class we pretend to be
        _vas (list of string): a list of mock vigilant attributes to create
        """
        model.HwComponent.__init__(self, name, role, daemon=daemon, parent=parent)
        if len(kwargs) > 0:
            logging.debug("Component '%s' got init arguments %r", name, kwargs)

        # Special handling of actuators, for actuator wrappers
        # Can not be generic for every roattribute, as we don't know what to put as value
        if issubclass(_realcls, model.Actuator):
            self.axes = {"x": model.Axis(range=[-1, 1])}
            # make them roattributes for proxy
            self._odemis_roattributes = ["axes"]

        if _vas is not None:
            for va in _vas:
                self.__dict__[va] = model.VigilantAttribute(None)

        if not children:
            children = {}

        cc = set()
        for child_name, child_args in children.items():
            # we don't care of child_name as it's only for internal use in the real component

            if isinstance(child_args, dict): # delegation
                # the real class is unknown, so just give a generic one
                logging.debug("Instantiating mock child component %s", child_name)
                child = MockComponent(_realcls=model.HwComponent, parent=self, daemon=daemon, **child_args)
            else: # explicit creation (already done)
                child = child_args

            cc.add(child)

        # use explicit setter to be sure the changes are notified
        self.children.value = self.children.value | cc

    # To pretend being a PowerSupplier
    def supply(self, sup):
        logging.debug("Pretending to power on components %s", sup)
        return model.InstantaneousFuture()


class FakeCCD(model.HwComponent):
    """
    Fake CCD component that returns a spot image
    """
    def __init__(self, fake_img):
        """
        Use .fake_img to change the image sent by the ccd
        Args:
            fake_img: 2D DataArray
        """
        super(FakeCCD, self).__init__("testccd", "ccd")
        self.exposureTime = model.FloatContinuous(0.1, (1e-6, 1000), unit="s")
        res = fake_img.shape[1], fake_img.shape[0]  # X, Y
        depth = 2 ** (fake_img.dtype.itemsize * 8)
        self.shape = (res[0], res[1], depth)
        self.binning = model.TupleContinuous((1, 1), [(1, 1), (8, 8)],
                                       cls=(int, long, float), unit="")
        self.resolution = model.ResolutionVA(res, [(1, 1), res])
        self.readoutRate = model.FloatVA(1e9, unit="Hz", readonly=True)

        pxs_sens = fake_img.metadata.get(model.MD_SENSOR_PIXEL_SIZE, (10e-6, 10e-6))
        self.pixelSize = model.VigilantAttribute(pxs_sens, unit="m", readonly=True)

        self.data = CCDDataFlow(self)
        self._acquisition_thread = None
        self._acquisition_lock = threading.Lock()
        self._acquisition_init_lock = threading.Lock()
        self._acquisition_must_stop = threading.Event()
        self.fake_img = fake_img

        self._metadata = fake_img.metadata

    def start_acquire(self, callback):
        with self._acquisition_lock:
            self._wait_acquisition_stopped()
            target = self._acquire_thread
            self._acquisition_thread = threading.Thread(target=target,
                    name="FakeCCD acquire flow thread",
                    args=(callback,))
            logging.debug("Starting CCD simulation thread")
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

    def _simulate_image(self):
        """
        Generates the fake output.
        """
        with self._acquisition_lock:
            md = self.fake_img.metadata.copy()
            logging.debug("Simulating image with res %s @ %f", self.fake_img.shape, md.get(model.MD_ACQ_DATE, 0))
            md[model.MD_ACQ_DATE] = time.time()
            output = model.DataArray(self.fake_img, md)
            return output

    def _acquire_thread(self, callback):
        """
        Thread that simulates the CCD acquisition.
        """
        try:
            while not self._acquisition_must_stop.is_set():
                duration = self.exposureTime.value
                if self._acquisition_must_stop.wait(duration):
                    break
                callback(self._simulate_image())
        except:
            logging.exception("Unexpected failure during image acquisition")
        finally:
            logging.debug("Acquisition thread closed")
            self._acquisition_must_stop.clear()


class CCDDataFlow(model.DataFlow):
    """
    This is an extension of model.DataFlow. It receives notifications from the
    FakeCCD component once the fake output is generated. This is the dataflow to
    which the CCD acquisition streams subscribe.
    """
    def __init__(self, ccd):
        model.DataFlow.__init__(self)
        self.component = weakref.ref(ccd)

    def start_generate(self):
        try:
            self.component().start_acquire(self.notify)
        except ReferenceError:
            pass

    def stop_generate(self):
        try:
            self.component().stop_acquire()
        except ReferenceError:
            pass


