# -*- coding: utf-8 -*-
'''
Created on 15 Oct 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

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
from numpy.core import umath
from odemis import model
from odemis.model._core import roattribute
import gc
import glob
import logging
import math
import mmap
import numpy
import odemis
import odemis.driver.comedi_simple as comedi
import os
import threading
import time
import weakref
#pylint: disable=E1101

# This is a module to drive a FEI Scanning electron microscope via the so-called
# "external X/Y" line. It uses a DA-conversion and acquisition (DAQ) card on the
# computer side to control the X/Y position of the electron beam (e-beam), while
# receiving the intensity sent by the secondary electron and/or backscatter
# detector. The DAQ card is handled via the (Linux) Comedi interface.
#
# Although it should in theory be quite generic, this driver is only tested on
# Linux with Comedilib 0.10.1, with a NI PCI 6251 DAQ card, and a FEI Quanta SEM.
#
# From the point of view of Odemis, this driver provides several HwComponents.
# The e-beam position control is represented by an Scanner (Emitter) component,
# while each detector is represented by a separate Detector device.
#
# The pin connection should be the following for the NI PCI 6251:
# Scanner X : AO0/AO GND = pins 22/55
# Scanner Y : AO1/AO GND = pins 21/54
# SED : AI1/AI GND = pins 33/32
# BSD : AI2/AI GND = pins 65/64
# SCB-68 Temperature Sensor differential : AI0+/AI0- = AI0/AI8 = pins 68/34 (by jumper)
#
# Over-sampling consists in measuring the value multiple times for the same pixel.
# It is followed by "decimation", which aggregates the data back into just one
# pixel (the simplest and most usual technique being averaging).
# For SEM, this is necessary to acquire good quality picture as otherwise the
# sampling is done only during a very short period. This is the purpose of
# increasing the dwell time: to have a longer period to allow multiple samples
# to be acquired. It can be done either by keeping the e-beam on the same place
# and taking multiple samples, or moving the e-beam around the pixel (in which
# it's exactly equivalent to reducing the image resolution).
#
# Note about using comedi in Python:
# There are two available bindings for comedi in Python: python-comedilib
# (provided with comedi) and pycomedi.  python-comedilib provides just a direct
# mapping of the C functions. It's quite verbose because every name starts with
# comedi_, and not object oriented. You can directly use the C documentation. The
# only thing to know is that parameters which are a simple type and used as output
# (e.g., int *, double *) are not passed as parameters but directly returned as
# output. However structures must be first allocated and then given as input
# parameter. See comedi_wrap.doc for parameters. It also uses special "unbounded
# arrays" for the instructions and sampl arrays, which are very inconvenient to
# manipulate. To create a structure, you need to create an object with the name
# of the structure, plus _struct.
# pycomedi is object-oriented. It tries to be less verbose but fails a bit
# because each object is in a separate module. At least it handles call errors
# as exceptions. It also has some non implemented parts, for example to_phys,
# from_phys are not available. For now there is no documentation but some
# examples.
# We ended up with using our own wrapper "comedi_simple". It's actually just a
# wrapper of python-comedilib to remove the "comedi_" part of each function, and
# generate the right exception in case of errors.

NI_TRIG_AI_START1 = 18 # Trigger number for AI Start1 (= beginning of a command)

# helper functions
def get_best_dtype_for_acc(idtype, count):
    """
    Computes the smallest dtype that allows to accumulate all the _count_ integers
    idtype (dtype): dtype of the input (the raw data that is accumulated)
    count (int): number of values accumulated
    returns (dtype): the best fitting dtype
    """
    maxval = numpy.iinfo(idtype).max * count
    if maxval <= numpy.iinfo(numpy.uint32).max:
        adtype = numpy.uint32
    elif maxval <= numpy.iinfo(numpy.uint64).max:
        adtype = numpy.uint64
    else:
        logging.debug("Going to use lossy intermediate type in order to support values up to %d", maxval)
        adtype = numpy.float64 # might accumulate errors

    return adtype

class CancelledError(Exception):
    """
    Raised when trying to access the result of a task which was cancelled
    """
    pass

class SEMComedi(model.HwComponent):
    '''
    A generic HwComponent which provides children for controlling the scanning
    area and receiving the data from the detector of a SEM via Comedi.
    '''

    def __init__(self, name, role, children, device, daemon=None, **kwargs):
        '''
        children (dict string->kwargs): parameters setting for the children.
            Known children are "scanner", "detector0", "detector1"...
            They will be provided back in the .children roattribute
        device (string): name of the /dev comedi  device (ex: "/dev/comedi0")
        Raise an exception if the device cannot be opened
        '''
        # TODO is the device name better like Comedi board name style? "pci-6251"?
        self._device_name = device

        # we will fill the set of children with Components later in ._children
        model.HwComponent.__init__(self, name, role, daemon=daemon, **kwargs)

        try:
            self._device = comedi.open(self._device_name)
            self._fileno = comedi.fileno(self._device)
        except comedi.ComediError:
            raise ValueError("Failed to open DAQ device '%s'" % device)

        try:
            self._ai_subdevice = comedi.find_subdevice_by_type(self._device,
                                            comedi.SUBD_AI, 0)
            self._ao_subdevice = comedi.find_subdevice_by_type(self._device,
                                            comedi.SUBD_AO, 0)
            # The detector children will do more thorough checks
            nchan = comedi.get_n_channels(self._device, self._ai_subdevice)
            if nchan < 1:
                raise IOError("DAQ device '%s' has only %d input channels", nchan)
            # The scanner child will do more thorough checks
            nchan = comedi.get_n_channels(self._device, self._ao_subdevice)
            if nchan < 2:
                raise IOError("DAQ device '%s' has only %d output channels", nchan)
        except comedi.ComediError:
            raise ValueError("Failed to find both input and output on DAQ device '%s'" % device)

        self._reader = Reader(self)
        self._writer = Writer(self)

        self._metadata = {model.MD_HW_NAME: self.getHwName()}
        self._swVersion = "%s (driver %s)" % (odemis.__version__, self.getSwVersion())
        self._metadata[model.MD_SW_VERSION] = self._swVersion
        self._metadata[model.MD_HW_VERSION] = self._hwVersion # unknown

        self._check_test_device()

        # detect when values are strange
        comedi.set_global_oor_behavior(comedi.OOR_NAN)
        self._init_calibration()

        # converters: dict (3-tuple int->number callable(number)):
        # subdevice, channel, range -> converter from value to value
        self._convert_to_phys = {}
        self._convert_from_phys = {}

        # TODO only look for 2 output channels and len(detectors) input channels
        self._min_ai_periods, self._min_ao_periods = self._get_min_periods()
        # On the NI-6251, according to the doc:
        # AI is 1MHz (aggregate) (or 1.25MHz with only one channel)
        # AO is 2.86/2.0 MHz for one/two channels
        # => that's more or less what we get from comedi :-)

        self._max_bufsz = self._get_max_buffer_size()

        # acquisition thread setup
        # FIXME: we have too many locks. Need to simplify the acquisition and cancellation code
        self._acquisition_data_lock = threading.Lock()
        self._acquisition_mng_lock = threading.Lock()
        self._acquisition_init_lock = threading.Lock()
        self._acquisition_must_stop = threading.Event()
        self._acquisition_thread = None
        self._acquisitions = {} # detector -> callable (callback)

        # create the scanner child "scanner"
        try:
            kwargs = children["scanner"]
        except (KeyError, TypeError):
            raise KeyError("SEMComedi device '%s' was not given a 'scanner' child" % device)
        # init detector with the right length for find_closest_dwell_time
        self._detectors = dict([(n, None) for n in children if n.startswith("detector")])
        self._scanner = Scanner(parent=self, daemon=daemon, **kwargs)
        self.children.add(self._scanner)
        # for scanner.newPosition
        self._new_position_thread = None
        self._new_position_thread_pipe = [] # list to communicate with the current thread

        # create the detector children "detectorN"
        self._detectors = {} # string (name) -> component
        for name, kwargs in children.items():
            if name.startswith("detector"):
                self._detectors[name] = Detector(parent=self, daemon=daemon, **kwargs)
                self.children.add(self._detectors[name])

        if not self._detectors:
            raise KeyError("SEMComedi device '%s' was not given any 'detectorN' child" % device)
        rchannels = set([d.channel for d in self._detectors.values()])
        if len(rchannels) != len(self._detectors):
            raise ValueError("SEMComedi device '%s' was given multiple detectors with the same channel" % device)

        self.set_to_resting_position()

    # There are two temperature sensors:
    # * One on the board itself (TODO how to access it with Comedi?)
    # * One on the SCB-68. From the manual, the temperature sensor outputs
    #   10 mV/°C and has an accuracy of ±1 °C => T = 100 * Vt
    # We could create a VA for the board temperature.

    def _reset_device(self):
        logging.info("Resetting device %s", self._device_name)
        try:
            comedi.close(self._device)
            self._device = None

            # need to be done explicitly to catch exceptions
            self._reader.close()
            self._writer.close()
        except Exception:
            logging.exception("closing device failed")

        self._device = comedi.open(self._device_name)
        self._fileno = comedi.fileno(self._device)
        self._reader = Reader(self)
        if not self._test:
            self._writer = Writer(self)

    def _init_calibration(self):
        """
        Load the calibration file if possible.
        Necessary for _get_converter to work.
        """
        self._calibration = None  # means not calibrated

        # is any subdevice soft-calibrated?
        nsubd = comedi.get_n_subdevices(self._device)
        is_soft_calibrated = False
        for i in range(nsubd):
            flags = comedi.get_subdevice_flags(self._device, i)
            if flags & comedi.SDF_SOFT_CALIBRATED:
                is_soft_calibrated = True
                break

        if not is_soft_calibrated:
            # nothing more to do
            # TODO: do we still need to check comedi_calibrate has been called?
            # TODO: do the hardware calibrated devices need to have the file loaded?
            # see comedi_apply_calibration() => probably not, but need to call this
            # function when we read from different channel.
            return


        # Only works if the device is soft-calibrated, and has been calibrated
        try:
            path = comedi.get_default_calibration_path(self._device)
            self._calibration = comedi.parse_calibration_file(path)
        except comedi.ComediError:
            logging.warning("Failed to read calibration information, you might "
                            "want to calibrate your device with:\n"
                            "sudo comedi_soft_calibrate -f %s\n",
                            self._device_name)

    def _check_test_device(self):
        """
        Check whether a real device is connected or comedi_test
        In case of comedi_test, we "patch" the class to pretend to have a real
        device, although the driver is very limited.
        """
        driver = comedi.get_driver_name(self._device)
        if driver == "comedi_test":
            self._test = True
            self._write_read_raw_one_cmd = self._fake_write_read_raw_one_cmd
            self._actual_writer = self._writer # keep a ref to avoid closing the file
            self._writer = FakeWriter(self) # does no write
            logging.info("Driver %s detected, going to use fake behaviour", driver)
        else:
            self._test = False

    def _get_min_periods(self):
        """
        Read the minimum scan periods for the AI and AO subdevices
        return (2-tuple (list of float, list of float)): AI period for each number
         of channel, and AO period for each number of channels (times are in
         seconds). 
        """
        min_ai_periods = [0] # 0 channel == as fast as you like
        nchans = comedi.get_n_channels(self._device, self._ai_subdevice)
        for n in range(1, nchans + 1):
            min_ai_periods.append(self._get_min_period(self._ai_subdevice, n))

        min_ao_periods = [0]
        nchans = comedi.get_n_channels(self._device, self._ao_subdevice)
        for n in range(1, nchans + 1):
            min_ao_periods.append(self._get_min_period(self._ao_subdevice, n))

        return min_ai_periods, min_ao_periods

    def _get_min_period(self, subdevice, nchannels):
        """
        subdevice (int): subdevice ID
        nchannels (0< int): number of channels to be accessed simultaneously
        returns (float): min scan period for the given subdevice with the given
        amount of channels  
        """
        # we create a timed command for the given parameters with a very short
        # period (1 ns) and see what period we actually get back.
        cmd = comedi.cmd_struct()
        try:
            comedi.get_cmd_generic_timed(self._device, subdevice, cmd, nchannels, 1)
        except comedi.ComediError:
            # happens with the comedi_test driver
            pass

        if cmd.scan_begin_src != comedi.TRIG_TIMER:
            logging.warning("Failed to find minimum period for subdevice %d with %d channels",
                            subdevice, nchannels)
            return 0
        period = cmd.scan_begin_arg / 1e9
        return period

    def _get_max_buffer_size(self):
        """
        Returns the maximum buffer size for one read command. Reading more bytes
          should be done in multiple commands.
        returns (0 < int): size in bytes
        """
        # There are two limitations to the maximum buffer:
        #  * the size in memory of the sample read (it can be huge if the
        #    oversampling rate is large) => restrict to << 4Gb
        bufsz = 50 * 2 ** 20 # max 50 MB: big, but no risk to take too much memory

        #  * the maximum amount of samples the DAQ device can read in one shot
        #    (on the NI 652x, it's 2**24 samples)
        try:
            # see how much the device is willing to accept by asking for the maximum
            cmd = comedi.cmd_struct()
            comedi.get_cmd_generic_timed(self._device, self._ai_subdevice, cmd, 1, 1)
            cmd.stop_src = comedi.TRIG_COUNT
            cmd.stop_arg = 0xffffffff # 32 bits
            self._prepare_command(cmd)
            bufsz = min(cmd.stop_arg, bufsz)
        except comedi.ComediError:
            # consider it can take the max
            pass

        return bufsz

    def getSwVersion(self):
        """
        Returns (string): displayable string showing the driver version
        """
        driver = comedi.get_driver_name(self._device)
        version = comedi.get_version_code(self._device)
        lversion = []
        for i in range(3):
            lversion.insert(0, version & 0xff)  # grab lowest 8 bits
            version >>= 8  # shift over 8 bits
        sversion = '.'.join(str(x) for x in lversion)
        return "%s v%s" % (driver, sversion)

    def getHwName(self):
        """
        Returns (string): displayable string showing whatever can be found out 
          about the actual hardware.
        """
        return comedi.get_board_name(self._device)

    def getMetadata(self):
        return self._metadata

    def updateMetadata(self, md):
        """
        Update the metadata associated with every image acquired to these
        new values. It's accumulative, so previous metadata values will be kept
        if they are not given.
        md (dict string -> value): the metadata
        """
        # We receive as MD_POS the _center_ position. When applied to an image,
        # the scanner translation will be added to it.
        self._metadata.update(md)

    def _get_converter_actual(self, subdevice, channel, range, direction):
        """
        Finds the best converter available for the given conditions
        subdevice (int): the subdevice index
        channel (int): the channel index
        range (int): the range index
        direction (enum): comedi.COMEDI_TO_PHYSICAL or comedi.COMEDI_FROM_PHYSICAL
        return a callable number -> number
        """
        assert(direction in [comedi.TO_PHYSICAL, comedi.FROM_PHYSICAL])

        # 3 possibilities:
        # * the device is hard-calibrated -> simple converter from get_hardcal_converter
        # * the device is soft-calibrated -> polynomial converter from  get_softcal_converter
        # * the device is not calibrated -> linear approximation converter
        poly = None
        flags = comedi.get_subdevice_flags(self._device, subdevice)
        if not flags & comedi.SDF_SOFT_CALIBRATED:
            # hardware-calibrated
            poly = comedi.polynomial_t()
            result = comedi.get_hardcal_converter(self._device,
                              subdevice, channel, range, direction, poly)
            if result < 0:
                logging.error("Failed to get converter for hardware calibrated device")
                poly = None
        elif self._calibration:
            # soft-calibrated
            poly = comedi.polynomial_t()
            try:
                result = comedi.get_softcal_converter(subdevice, channel,
                              range, direction, self._calibration, poly)
            except comedi.ComediError:
                # It's quite possible that it fails if asking for opposite
                # direction than the calibration polynomial, if the polynomial
                # has a order > 1 (e.g., AI on NI PCI 6251).
                logging.warning("Failed to get converter from calibration")
                poly = None

        if poly is None:
            # not calibrated
            logging.debug("creating a non calibrated converter for s%dc%dr%d",
                          subdevice, channel, range)
            maxdata = comedi.get_maxdata(self._device, subdevice, channel)
            range_info = comedi.get_range(self._device, subdevice,
                                                 channel, range)
            # using default parameter to copy values into local-scope
            if direction == comedi.TO_PHYSICAL:
                return lambda d, r = range_info, m = maxdata: comedi.to_phys(d, r, m)
            else:
                return lambda d, r = range_info, m = maxdata: comedi.from_phys(d, r, m)
        else:
            # calibrated: return polynomial-based converter
            logging.debug("creating a calibrated converter for s%dc%dr%d",
                          subdevice, channel, range)
            if direction == comedi.TO_PHYSICAL:
                return lambda d, p = poly: comedi.to_physical(d, p)
            else:
                if poly.order > 1:
                    logging.info("polynomial of order %d, linear conversion would be imprecise",
                                 poly.order)
                return lambda d, p = poly: comedi.from_physical(d, p)

    def _get_converter(self, subdevice, channel, range, direction):
        """
        Finds the best converter available for the given conditions
        subdevice (int): the subdevice index
        channel (int): the channel index
        range (int): the range index
        direction (enum): comedi.COMEDI_TO_PHYSICAL or comedi.COMEDI_FROM_PHYSICAL
        return a callable number -> number
        """
        # get the cached converter, or create a new one
        try:
            converter = self._convert_to_phys[subdevice, channel, range]
        except KeyError:
            converter = self._get_converter_actual(subdevice, channel, range, direction)
            self._convert_to_phys[subdevice, channel, range] = converter

        return converter

    def _to_phys(self, subdevice, channel, range, value):
        """
        Converts a raw value to the physical value, using the best converter 
          available.
        subdevice (int): the subdevice index
        channel (int): the channel index
        range (int): the range index
        value (int): the value to convert
        return (float): value in physical unit
        """
        converter = self._get_converter(subdevice, channel, range, comedi.TO_PHYSICAL)
        return converter(value)

    def _from_phys(self, subdevice, channel, range, value):
        """
        Converts a physical value to raw, using the best converter available.
        subdevice (int): the subdevice index
        channel (int): the channel index
        range (int): the range index
        value (float): the value to convert
        return (int): value in raw data 
        """
        converter = self._get_converter(subdevice, channel, range, comedi.FROM_PHYSICAL)
        return converter(value)

    def _array_to_phys(self, subdevice, channels, ranges, data):
        """
        Converts an array containing raw values to physical
        subdevice (int): the subdevice index
        channel (list of int): the channel index for each value of the last dim 
        ranges (list of int): the range index for each value of the last dim
        data (numpy.ndarray): the array to convert, its last dimension must be the
          same as the channels and ranges. dtype should be uint (of any size)
        return (numpy.ndarray of the same shape as data, dtype=double): physical values
        """
        # FIXME: this is very slow (2us/element), might need to go into numba,
        # cython, C, or be avoided.
        array = numpy.empty(shape=data.shape, dtype=numpy.double)
        converters = []
        for i, c in enumerate(channels):
            converters.append(self._get_converter(subdevice, c, ranges[i],
                                                  comedi.TO_PHYSICAL))

        # converter needs lsampl (uint32). So for lsampl devices, it's pretty
        # straightforward, just a matter of convincing SWIG that a numpy.uint32
        # is a uint32. For sampl devices, everything needs to be converted.

        # Note: it should be possible to access always the values as ctypes
        # using data.ctypes.strides and
        # cbuf = data.ctypes.data_as(ctypes.POINTER(ctypes.c_uint32))
        # but ctypes.addressof(cbuf) != data.ctypes.data
        # which is a sign of copy (bad)
        try:
            flat_data = numpy.reshape(data, numpy.prod(data.shape))
            cbuf = numpy.ctypeslib.as_ctypes(flat_data)
        except TypeError:
            # strided array
            cbuf = None

        if data.dtype.char == 'L' and cbuf is not None:
            logging.debug("Using casting to access the raw data")
            # can just force-cast to a ctype buffer of unsigned int (that swig accepts)
            # TODO: maybe could be speed-up by something like vectorize()/map()?
            flat_array = numpy.reshape(array, numpy.prod(data.shape))
            nchans = len(channels)
            for i in range(flat_data.size / nchans):
                for j in range(nchans):
                    flat_array[nchans * i + j] = converters[j](cbuf[nchans * i + j])
        else:
            # Needs real conversion
            logging.debug("Using full conversion of the raw data")
            for i, v in numpy.ndenumerate(data):
                array[i] = converters[i[-1]](int(v))

        return array

    def _array_from_phys(self, subdevice, channels, ranges, data):
        """
        Converts an array containing physical values to raw
        subdevice (int): the subdevice index
        channel (list of int): the channel index for each value of the last dim 
        ranges (list of int): the range index for each value of the last dim
        data (numpy.ndarray): the array to convert, its last dimension must be the
          same as the channels and ranges
        return (numpy.ndarray of the same shape as data): raw values, the dtype
          fits the subdevice
        """
        # FIXME: this is very slow (2us/element), might need to go into numba,
        # cython, C, or be avoided.
        dtype = self._get_dtype(subdevice)
        # forcing the order is not necessary but just to ensure good performance
        buf = numpy.empty(shape=data.shape, dtype=dtype, order='C')

        # prepare the converters
        converters = []
        for i, c in enumerate(channels):
            converters.append(self._get_converter(subdevice, c, ranges[i],
                                                  comedi.FROM_PHYSICAL))

        # TODO: check if it's possible to avoid multiple type conversion in the call

        for i, v in numpy.ndenumerate(data):
            buf[i] = converters[i[-1]](v)

        return buf

    def getTemperatureSCB(self):
        """
        returns (-300<float<300): temperature in °C reported by the Shielded
          Connector Block (which must be set to temperature sensor differential)
        """
        # On the SCB-68. From the manual, the temperature sensor outputs on
        # AI0+/AI0- 10 mV/°C and has an accuracy of ±1 °C => T = 100 * Vt

        channel = 0

        # TODO: selecting a range should be done only once, at initialisation
        # Get AI0 in differential, with values going between 0 and 1V
        try:
            range = comedi.find_range(self._device, self._ai_subdevice, channel,
                                        comedi.UNIT_volt, 0, 1)
        except comedi.ComediError:
            logging.warning("Couldn't find a fitting range, using a random one")
            range = 0

        range_info = comedi.get_range(self._device, self._ai_subdevice, channel, range)
        logging.debug("Reading temperature with range %g->%g V", range_info.min, range_info.max)


        # read the raw value
        data = comedi.data_read(self._device, self._ai_subdevice,
                            channel, range, comedi.AREF_DIFF)

        # convert using calibration
        pvalue = self._to_phys(self._ai_subdevice, channel, range, data)
        temp = pvalue * 100.0
        return temp

    def _get_dtype(self, subdevice):
        """
        Return the appropriate numpy.dtype for the given subdevice
        """
        flags = comedi.get_subdevice_flags(self._device, subdevice)
        if flags & comedi.SDF_LSAMPL:
            return numpy.dtype(numpy.uint32)
        else:
            return numpy.dtype(numpy.uint16)

    def set_to_resting_position(self):
        """
        Set the beam to a resting position. The best would be to blank the beam
        but it's not controllable via the DAQ board.
        """
        pos, channels, ranges = self._scanner.get_resting_point_data()
        if self._test:
            return

        # need lock to avoid setting up the command at the same time as the
        # (next) acquisition is starting.
        with self._acquisition_init_lock:
            logging.debug("Setting rest position")
            # There was a bug in the NI driver, it's fixed in the latest kernels.
            # Set min_period to "500" to work around it.
            min_period = int(self._min_ao_periods[2] * 1e9)
            self.setup_timed_command(self._ao_subdevice, channels, ranges, min_period)

            # we expect that both values can fit in the buffer
            pos.tofile(self._writer.file)
            self._writer.file.flush()

            comedi.internal_trigger(self._device, self._ao_subdevice, 0)

            # we use a timer because of the problem with the NI driver with 1 scan only
            # (although it seems to work when period is min_period)
            time.sleep(0.001) # that should be more than enough
            logging.debug("Canceling resting command")
            comedi.cancel(self._device, self._ao_subdevice)

    def _get_data(self, channels, period, size):
        """
        read n data from the given analog input channel
        channels (list of int): channels 
        period (float): sampling period in s
        size (0<int): number of data to read
        return (numpy.array with shape=(size, len(channels)) and dtype=float) 
        Note: this is only for testing, and will go away in the final version
        """
        nchans = len(channels) #number of channels
        nscans = size
        period_ns = int(round(period * 1e9))  # in nanoseconds
        expected_time = nscans * period # s

        rranges = []
        for i, channel in enumerate(channels):
            data_lim = (-10, 10)
            try:
                best_range = comedi.find_range(self._device, self._ai_subdevice,
                                  channel, comedi.UNIT_volt, data_lim[0], data_lim[1])
            except comedi.ComediError:
                logging.exception("Data range between %g and %g V is too high for hardware." %
                              (data_lim[0], data_lim[1]))
                raise

            rranges.append(best_range)

        self._reader.prepare(nscans * nchans, expected_time)

        # run the commands
        logging.debug("Going to start the command")
        self.setup_timed_command(self._ai_subdevice, channels, rranges, period_ns,
                    start_src=comedi.TRIG_NOW, # start immediately
                    stop_arg=nscans)
        self._reader.run()

        timeout = (nscans * period) * 1.10 + 1 # s   == expected time + 10% + 1s
        rbuf = self._reader.wait(timeout)
        rbuf.shape = (nscans, nchans)

        # convert data to physical values
        logging.debug("Converting raw data to physical: %s", rbuf)
        # Allocate a separate memory block per channel as they'll later be used
        # completely separately
        parrays = []
        for i, c in enumerate(channels):
            parrays.append(self._array_to_phys(self._ai_subdevice,
                                   [c], [rranges[i]], rbuf[:, i, numpy.newaxis]))

        return parrays

    def _prepare_command(self, cmd):
        """
        Prepare a command for the comedi device (try to make it fits the device
          capabilities)
        raise IOError if the command cannot be prepared
        """
        # that's the official way: give it twice to command_test
        rc = comedi.command_test(self._device, cmd)
        if rc != 0:
            # on the second time, it should report 0, meaning "perfect"
            rc = comedi.command_test(self._device, cmd)
            if rc != 0:
                raise IOError("failed to prepare command (%d)" % rc)

    def find_closest_dwell_time(self, period):
        """
        Returns the closest dwell time _longer_ than the given time compatible
          with the device.
        period (float): dwell time requested (in s)
        returns (0<float): a value slightly smaller, or larger than the period (in s)
        raises:
            ValueError if no compatible dwell time can be found
        Note: the dwell time is computed assuming all the detectors are active
           simultaneously.
        """
        # TODO: min dwell time should be just for one input channel, and adapt if
        # more than one channel is read simultaneously => pass nrchans?

        nwchans = 2 # always
        nrchans = len(self._detectors) # one channel per detector
        period_ns = int(period * 1e9)  # in nanoseconds

        # should be finding it in 2 steps in normal cases
        for i in range(10):
            rcmd = comedi.cmd_struct()
            comedi.get_cmd_generic_timed(self._device, self._ai_subdevice,
                                                      rcmd, nrchans, period_ns)
            wcmd = comedi.cmd_struct()
            if self._test:
                wcmd.scan_begin_arg = rcmd.scan_begin_arg
            else:
                comedi.get_cmd_generic_timed(self._device, self._ao_subdevice,
                                                      wcmd, nwchans, period_ns)
            # scan_begin_arg contains a possible period for the subdevice
            if rcmd.scan_begin_arg == wcmd.scan_begin_arg:
                return rcmd.scan_begin_arg / 1e9

            # try again with the longest of both periods
            period_ns = max(rcmd.scan_begin_arg, wcmd.scan_begin_arg)

        # no compatible dwell time found
        raise ValueError("No compatible dwell time found for %g s." % period)

    def find_best_oversampling_rate(self, period, max_osr=2 ** 24):
        """
        Returns the closest dwell time _longer_ than the given time compatible
          with the output device and the highest over-sampling rate compatible 
          with the input device.
        period (float): dwell time requested (in s)
        max_osr (1<=int): maximum over-sampling rate returned
        returns (2-tuple: period (0<float), osr (1<=int)):
         period: a value slightly smaller, or larger than the period (in s)
         osr: a ratio indicating how many times faster runs the input clock
        raises:
            ValueError if no compatible dwell time can be found
        Note: the dwell time is computed assuming all the detectors are active
           simultaneously.
        """
        # TODO: min dwell time should be just for one input channel, and adapt if
        # more than one channel is read simultaneously => pass nrchans?
        assert(max_osr >= 1)
        nwchans = 2 # always
        nrchans = len(self._detectors) # one channel per detector
        period_ns = int(period * 1e9)  # in nanoseconds

        # let's find a compatible minimum dwell time for the output device
        wcmd = comedi.cmd_struct()
        if self._test:
            wcmd.scan_begin_arg = period_ns
        else:
            comedi.get_cmd_generic_timed(self._device, self._ao_subdevice,
                                                  wcmd, nwchans, period_ns)
        period_ns = float(wcmd.scan_begin_arg)

        # the best osr we can get for this dwell time
        min_rperiod_ns = self._min_ai_periods[nrchans] * 1e9
        rperiod_ns = max(period_ns, min_rperiod_ns)
        max_osr = min(max_osr, int(math.ceil(rperiod_ns / min_rperiod_ns)))

        if max_osr == 1:
            # go the obvious way:
            period = self.find_closest_dwell_time(period)
            return period, 1

        # The read period should be as close as possible from the minimum read
        # period (as long as it's equal or above). Then try to find a write
        # period compatible with this read period, more or less in the same order
        # of time as given
        rcmd = comedi.cmd_struct()
        wcmd = comedi.cmd_struct()

        rperiod_ns = int(min_rperiod_ns)
        for i in range(5):
            # it'll probably work on the first time, but just in case, we try
            # 5 times, with slighly bigger read periods
            comedi.get_cmd_generic_timed(self._device, self._ai_subdevice,
                                                      rcmd, nrchans, rperiod_ns)
            rperiod_ns = rcmd.scan_begin_arg

            # get a write period multiple of it
            for osr in range(max_osr, max_osr + 5):
                wperiod_ns = rperiod_ns * osr
                if self._test:
                    return wperiod_ns / 1e9, osr
                # check this _exact_ write period is compatible
                comedi.get_cmd_generic_timed(self._device, self._ao_subdevice,
                                                  wcmd, nwchans, wperiod_ns)
                if wcmd.scan_begin_arg == wperiod_ns:
                    # we've found it!
                    logging.debug("Found over-sampling rate: %g ns x %d = %g ns", rperiod_ns, osr, wperiod_ns)
                    return wperiod_ns / 1e9, osr

            # try a bigger read period
            rperiod_ns = int(math.ceil(rperiod_ns * 1.1))

        # We ought to never come here, but just in case, don't completely fail
        logging.error("Failed to find compatible over-sampling rate for dwell time of %g s", period)
        period = self.find_closest_dwell_time(period)
        return period, 1

    def write_data(self, channels, period, data):
        """
        write n data on the given analog output channels
        channels (list of int): channels to write (in same the order as data) 
        period (float): sampling period in s
        data (numpy.ndarray of float): two dimension array to write (physical values)
          first dimension is along the time, second is along the channels
        Note: this is only for testing, and will go away in the final version
        """
        #construct a comedi command
        nchans = data.shape[1]
        nscans = data.shape[0]
        assert len(channels) == nchans

        # create a chanlist
        ranges = []
        for i, channel in enumerate(channels):
            data_lim = (data[:, i].min(), data[:, i].max())
            try:
                best_range = comedi.find_range(self._device, self._ao_subdevice,
                                  channel, comedi.UNIT_volt, data_lim[0], data_lim[1])
            except comedi.ComediError:
                logging.exception("Data range between %g and %g V is too high for hardware." %
                              (data_lim[0], data_lim[1]))
                raise

            ranges.append(best_range)

        logging.debug("Generating a new command for %d scans", nscans)
        period_ns = int(round(period * 1e9))  # in nanoseconds
        self.setup_timed_command(self._ao_subdevice, channels, ranges, period_ns,
                    stop_arg=nscans)

        # convert physical values to raw data
        # Note: on the NI 6251, as probably many other devices, conversion is linear.
        # So it might be much more efficient to generate raw data directly
        dtype = self._get_dtype(self._ao_subdevice)
        # forcing the order is not necessary but just to ensure good performance
        buf = numpy.empty(shape=data.shape, dtype=dtype, order='C')
        converters = []
        for i, c in enumerate(channels):
            converters.append(self._get_converter(self._ao_subdevice, c, ranges[i],
                                                comedi.FROM_PHYSICAL)
                              )
        # TODO: check if it's possible to avoid multiple type conversion in the call
        for i, v in numpy.ndenumerate(data):
            buf[i] = converters[i[1]](v)
        # flatten the array
        buf = numpy.reshape(buf, nscans * nchans, order='C')

        logging.debug("Converted physical value to raw data: %s", buf)

        # preload the buffer with enough data first
        dev_buf_size = comedi.get_buffer_size(self._device, self._ao_subdevice)
        preload_size = dev_buf_size / buf.itemsize
        logging.debug("Going to preload %d bytes", buf[:preload_size].nbytes)
        buf[:preload_size].tofile(self._writer.file)
        logging.debug("Going to flush")
        self._writer.file.flush()

        # run the command
        logging.debug("Going to start the command")

        start_time = time.time()
        comedi.internal_trigger(self._device, self._ao_subdevice, 0)

        logging.debug("Going to write %d bytes more", buf[preload_size:].nbytes)
        buf[preload_size:].tofile(self._writer.file)
        logging.debug("Going to flush")
        self._writer.file.flush()

        # According to https://groups.google.com/forum/?fromgroups=#!topic/comedi_list/yr2U179x8VI
        # To finish a write fully, we need to do a cancel().
        # Wait until SDF_RUNNING is gone, then cancel() to reset SDF_BUSY
        expected = nscans * period
        left = start_time + expected - time.time()
        logging.debug("Waiting %g s for the write to finish", left)
        if left > 0:
            time.sleep(left)
        end_time = start_time + expected * 1.10 + 1 # s = expected time + 10% + 1s
        had_timeout = True
        while time.time() < end_time:
            flags = comedi.get_subdevice_flags(self._device, self._ao_subdevice)
            if not (flags & comedi.SDF_RUNNING):
                had_timeout = False
                break
            time.sleep(0.001)

        comedi.cancel(self._device, self._ao_subdevice)
        if had_timeout:
            raise IOError("Write command stopped due to timeout after %g s" % (time.time() - start_time))

    def write_read_2d_data_phys(self, wchannels, rchannels, rranges, period,
                                margin, osr, data):
        """
        write data on the given analog output channels and read the same amount 
         synchronously on the given analog input channels
        wchannels (list of int): channels to write (in same the order as data)
        rchannels (list of int): channels to write (in same the order as data)
        period (float): sampling period in s (time between two writes on the same
         channel)
        osr (1<=int): over-sampling rate, how many input samples should be acquired by pixel
        data (numpy.ndarray of float): two dimension array to write (physical values)
          first dimension is along the time, second is along the channels
        return (list of 1D numpy.array with shape=data.shape[0] and dtype=float)
            the data read converted to physical value (volt) for each channel
        """
        # XXX broken: need to update for new write_read_2d_data_raw()
        assert len(wchannels) == data.shape[1]

        # pick nice ranges according to the data to write
        wranges = []
        for i, channel in enumerate(wchannels):
            data_lim = (data[:, i].min(), data[:, i].max())
            try:
                best_range = comedi.find_range(self._device, self._ao_subdevice,
                                  channel, comedi.UNIT_volt, data_lim[0], data_lim[1])
            except comedi.ComediError:
                logging.exception("Data range between %g and %g V is too high for hardware." %
                              (data_lim[0], data_lim[1]))
                raise

            wranges.append(best_range)

        # convert physical values to raw data
        # Note: on the NI 6251, as probably many other devices, conversion is linear.
        # So it might be much more efficient to generate raw data directly
        wbuf = self._array_from_phys(self._ao_subdevice, wchannels, wranges, data)

        # write and read the raw data
        rbuf = self.write_read_2d_data_raw(wchannels, wranges, rchannels, rranges,
                                           period, margin, osr, wbuf)

        # convert data to physical values
        logging.debug("Converting raw data to physical: %s", rbuf)
        # Allocate a separate memory block per channel as they'll later be used
        # completely separately
        parrays = []
        for i, c in enumerate(rchannels):
            # FIXME: rbuf is now already n arrays
            parrays.append(self._array_to_phys(self._ai_subdevice,
                                   [c], [rranges[i]], rbuf[:, i, numpy.newaxis]))

        return parrays


    def setup_timed_command(self, subdevice, channels, ranges, period_ns,
                            start_src=comedi.TRIG_INT, start_arg=0,
                            stop_src=comedi.TRIG_COUNT, stop_arg=1):
        """
        Creates and sends a command to a subdevice.
        subdevice (0<int): subdevice id
        channels (list of int): channels of the command
        ranges (list of int): ranges of each channel
        period_ns (0<int): sampling period in ns (time between two conversions
         on the same channel)
        start_src, start_arg, stop_src, stop_arg: same meaning as the fields of
          a command.
        Raises:
            IOError: if the device didn't accept the command at all.
            ErrorValue: if the device didn't accept the command with precisely
              the given arguments. The command is sent to the subdevice.
        """
        nchans = len(channels)
        assert(0 < period_ns)

        # create a command
        cmd = comedi.cmd_struct()
        comedi.get_cmd_generic_timed(self._device, subdevice, cmd, nchans, period_ns)
        clist = comedi.chanlist(nchans)
        for i in range(nchans):
            clist[i] = comedi.cr_pack(channels[i], ranges[i], comedi.AREF_GROUND)
        cmd.chanlist = clist
        cmd.start_src = start_src
        cmd.start_arg = start_arg
        cmd.stop_src = stop_src
        cmd.stop_arg = stop_arg
        self._prepare_command(cmd)

        if (cmd.scan_begin_arg != period_ns or cmd.start_arg != start_arg or
            cmd.stop_arg != stop_arg):
            raise ValueError("Failed to create the precise command")

        # send the command
        comedi.command(self._device, cmd)

    def write_read_2d_data_raw(self, wchannels, wranges, rchannels, rranges,
                            period, margin, osr, data):
        """
        write data on the given analog output channels and read synchronously on
         the given analog input channels and convert back to 2d array 
        wchannels (list of int): channels to write (in same the order as data)
        wranges (list of int): ranges of each write channel
        rchannels (list of int): channels to read (in same the order as data)
        rranges (list of int): ranges of each read channel
        period (float): sampling period in s (time between two writes on the same
         channel)
        margin (0 <= int): number of additional pixels at the begginning of each line
        osr: over-sampling rate, how many input samples should be acquired by pixel
        data (3D numpy.ndarray of int): array to write (raw values)
          first dimension is along the slow axis, second is along the fast axis,
          third is along the channels
        return (list of 2D numpy.array with shape=(data.shape[0], data.shape[1]-margin)
         and dtype=device type): the data read (raw) for each channel, after
         decimation.
        """
        # We write at the given period, and read "osr" samples for each pixel
        nrchans = len(rchannels)

        if self._scanner.newPosition.hasListeners() and period >= 1e-3:
            # if the newPosition event is used, prefer the per pixel write/read
            # as it's much more precise (albeit a bit slower). It just needs to
            # not be too costly (1 ms should be higher than the setup cost).
            force_per_pixel = True
        else:
            force_per_pixel = False

        # find the best method to read that fit the buffer: /lines, or /pixel
        # try to fit a couple of lines
        linesz = data.shape[1] * nrchans * osr * self._reader.dtype.itemsize
        if linesz < self._max_bufsz and not force_per_pixel:
            lines = self._max_bufsz // linesz
            return self._write_read_2d_lines(wchannels, wranges, rchannels, rranges,
                         period, margin, osr, lines, data)

        # fit a pixel
        pixelsz = nrchans * osr * self._reader.dtype.itemsize
        if pixelsz > self._max_bufsz:
            # probably going to fail, but let's try...
            logging.warning("Going to try to read very large buffer of %g MB.", pixelsz / 2.**20)

        # TODO: read several pixels at a time => need clever placement
        return self._write_read_2d_pixel(wchannels, wranges, rchannels, rranges,
                         period, margin, osr, data)


    def _write_read_2d_lines(self, wchannels, wranges, rchannels, rranges,
                            period, margin, osr, maxlines, data):
        """
        Implementation of write_read_2d_data_raw by reading the input data n
          lines at a time.
        """
        if self._scanner.newPosition.hasListeners() and margin > 0:
            # we don't support margin detection on multiple lines for
            # newPosition trigger.
            maxlines = 1

        logging.debug("Reading %d lines at a time: %d samples/read every %g µs",
                      maxlines, maxlines * data.shape[1] * osr * len(rchannels),
                      period * 1e6)
        rshape = (data.shape[0], data.shape[1] - margin)

        # allocate one full buffer per channel
        buf = []
        for c in rchannels:
            buf.append(numpy.empty(rshape, dtype=self._reader.dtype))
        adtype = get_best_dtype_for_acc(self._reader.dtype, osr)


        # read "maxlines" lines at a time
        x = 0
        while x < data.shape[0]:
            lines = min(data.shape[0] - x, maxlines)
            logging.debug("Going to read %d lines", lines)
            wdata = data[x:x + lines, :, :] # just a couple of lines
            wdata = wdata.reshape(-1, wdata.shape[2]) # flatten X/Y
            rbuf = self._write_read_raw_one_cmd(wchannels, wranges, rchannels,
                                            rranges, period, osr, wdata, margin)

            # decimate into each buffer
            for i, b in enumerate(buf):
                self._scan_raw_to_lines(rshape, margin, osr, x, rbuf[..., i], b[x:x + lines, ...], adtype)

            x += lines

        return buf

    @staticmethod
    def _scan_raw_to_lines(shape, margin, osr, x, data, oarray, adtype):
        """
        Converts a linear array resulting from a scan with oversampling to a 2D array
        shape (2-tuple int): H,W dimension of the scanned image (margin not included)
        margin (int): amount of useless pixels at the beginning of each line
        osr (int): over-sampling rate
        x (int): x position of the line in the output array
        data (1D ndarray): the raw linear array (including oversampling), of one channel
        oarray (2D ndarray): the output array, already allocated, of shape self.shape
        adtype (dtype): intermediary type to use for the accumulator
        """
        # reshape to a 3D array with margin and sub-samples
        rectangle = data.reshape((-1, shape[1] + margin, osr))
        # trim margin
        tr_rect = rectangle[:, margin:]
        if osr == 1:
            # only one sample per pixel => copy
            oarray = tr_rect[:, :, 0]
        else:
            # inspired by _mean() from numpy, but save the accumulated value in
            # a separate array of a big enough dtype.
            acc = umath.add.reduce(tr_rect, axis=2, dtype=adtype)
            umath.true_divide(acc, osr, out=oarray, casting='unsafe', subok=False)

    def _write_read_2d_pixel(self, wchannels, wranges, rchannels, rranges,
                            period, margin, osr, data):
        """
        Implementation of write_read_2d_data_raw by reading the input data one 
          pixel at a time.
        """
        logging.debug("Reading one pixel at a time: %d samples/read every %g µs",
                      osr * len(rchannels), period * 1e6)
        rshape = (data.shape[0], data.shape[1] - margin)

        # allocate one full buffer per channel
        buf = []
        for c in rchannels:
            buf.append(numpy.empty(rshape, dtype=self._reader.dtype))
        adtype = get_best_dtype_for_acc(self._reader.dtype, osr)

        # read one pixel at a time
        for x, y in numpy.ndindex(data.shape[0], data.shape[1]):
            wdata = data[x, y, :] # just one pixel
            wdata.shape = (1, data.shape[2]) # reshape to 2D
            if y < margin:
                ss = 1
            else:
                ss = 0
            rbuf = self._write_read_raw_one_cmd(wchannels, wranges, rchannels,
                                                rranges, period, osr, wdata, ss)

            # decimate into each buffer
            for i, b in enumerate(buf):
                self._scan_raw_to_pixel(rshape, margin, osr, x, y, rbuf[..., i], b, adtype)

        return buf

    @staticmethod
    def _scan_raw_to_pixel(shape, margin, osr, x, y, data, oarray, adtype):
        """
        Converts acquired data for one pixel resulting into the pixel for the a 2D array
        shape (2-tuple int): H,W dimension of the scanned image (margin not included)
        margin (int): amount of useless pixels at the beginning of each line
        osr (int): over-sampling rate
        x (int): x position of the pixel in the input array
        y (int): y position of the pixel in the input array
        data (1D ndarray): the raw data (including oversampling), of one channel
        oarray (2D ndarray): the output array, already allocated, of shape self.shape
        """
        if y < margin:
            return
        oarray[x, y - margin] = numpy.sum(data, dtype=adtype) / osr

    def _fake_write_read_raw_one_cmd(self, wchannels, wranges, rchannels, rranges,
                                 period, osr, data, settling_samples):
        """
        Imitates _write_read_raw_one_cmd() but works with the comedi_test driver,
          just read data.
        """
        begin = time.time()
        nwscans = data.shape[0]
        nwchans = data.shape[1]
        nrscans = nwscans * osr
        nrchans = len(rchannels)
        rperiod_ns = int(round(period * 1e9) / osr)  # in nanoseconds
        expected_time = nwscans * period # s

        with self._acquisition_init_lock:
            # Check if the acquisition has already been cancelled
            # After this block (i.e., reader and writer prepared), the .cancel()
            # methods will have enough effect to stop the acquisition
            if self._acquisition_must_stop.is_set():
                raise CancelledError("Acquisition cancelled during preparation")

            logging.debug("Generating a new read command for %d scans", nrscans)

            # flatten the array
            wbuf = numpy.reshape(data, nwscans * nwchans)
            self._writer.prepare(wbuf, expected_time)

            # prepare read buffer info
            self._reader.prepare(nrscans * nrchans, expected_time)

        # FIXME: some times, after many fine acquisitions, this command fails
        # with "ComediError: returned -1 -> (16) Device or resource busy"
        # create a command for reading, with a period osr times smaller than the write
        self.setup_timed_command(self._ai_subdevice, rchannels, rranges, rperiod_ns,
                    start_src=comedi.TRIG_NOW, # start immediately (trigger is not supported)
                    stop_arg=nrscans)
        start = time.time()

        np_to_report = nwscans - settling_samples
        shift_report = settling_samples
        if settling_samples == 0:  # indicate a new ebeam position
            self._scanner.newPosition.notify()
            np_to_report -= 1
            shift_report += 1

        # run the commands
        self._reader.run()
        self._writer.run()
        self._start_new_position_notifier(np_to_report,
                                      start + shift_report * period,
                                      period)

        timeout = expected_time * 1.10 + 0.1 # s   == expected time + 10% + 0.1s
        logging.debug("Waiting %g s for the acquisition to finish", timeout)
        rbuf = self._reader.wait(timeout)
        self._writer.wait(0.1)
        # reshape to 2D
        rbuf.shape = (nrscans, nrchans)
        logging.debug("acquisition took %g s, init=%g s", time.time() - begin, start - begin)
        return rbuf

    def _write_read_raw_one_cmd(self, wchannels, wranges, rchannels, rranges,
                            period, osr, data, settling_samples):
        """
        write data on the given analog output channels and read synchronously
          on the given analog input channels in one command
        wchannels (list of int): channels to write (in same the order as data)
        wranges (list of int): ranges of each write channel
        rchannels (list of int): channels to read (in same the order as data)
        rranges (list of int): ranges of each read channel
        period (float): sampling period in s (time between two writes on the same
         channel)
        osr: over-sampling rate, how many input samples should be acquired per 
          output sample 
        data (2D numpy.ndarray of int): array to write (raw values)
          first dimension is along the time, second is along the channels
        settling_samples (int): number of first write samples used for the 
          settling of the beam, and so don't need to trigger newPosition 
        return (2D numpy.array with dtype=device type)
            the raw data read (first dimension is data.shape[0] * osr) for each
            channel (as second dimension).
        raises:
            IOError: in case of timeout or cancellation
        """
        # We write at the given period, and read osr samples for each pixel
        nwscans = data.shape[0]
        nwchans = data.shape[1]
        nrscans = nwscans * osr
        nrchans = len(rchannels)
        period_ns = int(round(period * 1e9))  # in nanoseconds
        rperiod_ns = int(round(period * 1e9) / osr)  # in nanoseconds
        expected_time = nwscans * period # s

        with self._acquisition_init_lock:
            # Check if the acquisition has already been cancelled
            # After this block (i.e., reader and writer prepared), the .cancel()
            # methods will have enough effect to stop the acquisition
            if self._acquisition_must_stop.is_set():
                raise CancelledError("Acquisition cancelled during preparation")

            # create a command for writing
            logging.debug("Generating new write and read commands for %d scans on "
                          "channels %r/%r", nwscans, wchannels, rchannels)
            self.setup_timed_command(self._ao_subdevice, wchannels, wranges, period_ns,
                        start_src=comedi.TRIG_EXT, # from PyComedi docs: should improve synchronisation
                        start_arg=NI_TRIG_AI_START1, # when the AI starts reading
                        stop_arg=nwscans)

            # create a command for reading, with a period osr times smaller than the write
            self.setup_timed_command(self._ai_subdevice, rchannels, rranges, rperiod_ns,
                        stop_arg=nrscans)

            # prepare to write the flattened buffer
            wbuf = data.reshape((nwscans * nwchans,))
            self._writer.prepare(wbuf, expected_time)

            # prepare to read
            self._reader.prepare(nrscans * nrchans, expected_time)

        # run the commands
        # AO is waiting for AI/Start1, so not sure why internal trigger needed,
        # but it is. Maybe just to let Comedi know that the command has started.
        comedi.internal_trigger(self._device, self._ao_subdevice, 0)
        comedi.internal_trigger(self._device, self._ai_subdevice, 0)
        start = time.time()

        np_to_report = nwscans - settling_samples
        shift_report = settling_samples
        if settling_samples == 0:
            # no margin => indicate a new ebeam position right now
            self._scanner.newPosition.notify()
            np_to_report -= 1
            shift_report += 1

        self._reader.run()
        self._writer.run()
        self._start_new_position_notifier(np_to_report,
                                      start + shift_report * period,
                                      period)

        timeout = expected_time * 1.10 + 0.1 # s   == expected time + 10% + 0.1s
        logging.debug("Waiting %g s for the acquisition to finish", timeout)
        rbuf = self._reader.wait(timeout)
        self._writer.wait() # writer is faster, so there should be no wait
        # reshape to 2D
        rbuf.shape = (nrscans, nrchans)
        return rbuf

    def _start_new_position_notifier(self, n, start, period):
        """
        Notify the newPosition Event n times with the given period.
        n (0 <= int): number of event notifications
        start (float): time for the first event (should be in the future)
        period (float): period between two events
        Note: this is used to emulate an actual ebeam change of position when 
         the hardware is requested to move the ebeam at multiple positions in a
         row. Do not expect a precision better than 10us.
        Note 2: this method returns immediately (and the emulation is run in a
         separate thread).
        """
        # no need if no one's listening
        if not self._scanner.newPosition.hasListeners():
            return

        if n <= 0:
            return

        if period < 10e-6:
            # don't even try: that's the time it'd take to have just one loop
            # doing nothing
            logging.error("Cannot generate newPosition events at such a "
                              "small period of %s µs", period * 1e6)
            return

        self._new_position_thread_pipe = []
        self._new_position_thread = threading.Thread(
                         target=self._notify_new_position,
                         args=(n, start, period, self._new_position_thread_pipe),
                         name="SEM new position notifier")

        self._new_position_thread.start()

    def _notify_new_position(self, n, start, period, pipe):
        """
        The thread content
        """
        trigger = 0
        failures = 0
        for i in range(n):
            now = time.time()
            trigger += period # accumulation error should be small
            left = start - now + trigger
            if left > 0:
                if left > 10e-6: # TODO: if left < 1 ms => use usleep or nsleep
                    time.sleep(left)
            else:
                failures += 1
            if pipe: # put anything in the pipe and it will mean it has to stop
                logging.debug("npnotifier received cancel message")
                return
            self._scanner.newPosition.notify()

        if failures:
            logging.warning("Failed to trigger newPosition in time %d times, "
                            "last trigger was %g µs late.", failures, -left * 1e6)

    def _cancel_new_position_notifier(self):
        logging.debug("cancelling npnotifier")
        self._new_position_thread_pipe.append(True) # means it has to stop


    def start_acquire(self, detector, callback):
        """
        Start acquiring images on the given detector (i.e., input channel).
        detector (Detector): detector from which to acquire an image
        callback (callable): function to callback with every acquired image
        Note: The acquisition parameters are defined by the scanner. Acquisition
        might already be going on for another detector, in which case the detector
        will be added on the next acquisition.
        raises KeyError if the detector is already being acquired.
        """
        # to be thread-safe (simultaneous calls to start/stop_acquire())
        with self._acquisition_data_lock:
            if detector in self._acquisitions:
                raise KeyError("Channel %d already set up for acquisition.", detector.channel)

            self._acquisitions[detector] = callback

        # the thread uses acquisition_data_lock, so we should never wait for
        # the thread to stop with this lock acquired.
        with self._acquisition_mng_lock:
            self._wait_acquisition_stopped() # only wait if acquisition thread is stopping
            if not self._acquisition_thread or not self._acquisition_thread.isAlive():
                # Set up thread
                self._acquisition_thread = threading.Thread(target=self._acquisition_run,
                                                    name="SEM acquisition thread")
                self._acquisition_thread.start()

    def stop_acquire(self, detector):
        """
        Stop acquiring images on the given channel.
        detector (Detector): detector from which to acquire an image
        Note: acquisition might still go on on other channels
        """
        with self._acquisition_data_lock:
            del self._acquisitions[detector]
            if self._acquisitions:
                # Still something to acquire => keep the thread running
                return

        with self._acquisition_mng_lock:
            self._req_stop_acquisition()

    def _req_stop_acquisition(self):
        """
        Request the acquisition thread to stop
        """
        # This must be entirely done before any new comedi command is started
        # So it's protected with the init of read/write and set_to_resting_position
        with self._acquisition_init_lock:
            self._acquisition_must_stop.set()
            self._cancel_new_position_notifier()
            self._writer.cancel()
            self._reader.cancel()

    def _wait_acquisition_stopped(self):
        """
        Waits until the acquisition thread is fully finished _iif_ it was requested
        to stop.
        """
        # "if" is to not wait if it's already finished
        if self._acquisition_must_stop.is_set():
            self._acquisition_thread.join(10) # 10s timeout for safety
            if self._acquisition_thread.isAlive():
                raise OSError("Failed to stop the acquisition thread")
            # ensure it's not set, even if the thread died prematurely
            self._acquisition_must_stop.clear()

    def _acquisition_run(self):
        """
        Acquire images until asked to stop. Sends the raw acquired data to the
          callbacks.
        Note: to be run in a separate thread
        """
        try:
            nfailures = 0
            while not self._acquisition_must_stop.is_set():
                # get the channels to acquire
                with self._acquisition_data_lock:
                    detectors = self._acquisitions.keys()
                if not detectors:
                    # another way to quit
                    break

                rchannels = [d.channel for d in detectors]
                rranges = [d._range for d in detectors]

                # get the scan values (automatically updated to the latest needs)
                scan, period, shape, margin, wchannels, wranges, osr = self._scanner.get_scan_data()

                metadata = dict(self._metadata) # duplicate
                metadata[model.MD_ACQ_DATE] = time.time() # time at the beginning
                metadata[model.MD_DWELL_TIME] = period
                metadata[model.MD_SAMPLES_PER_PIXEL] = osr

                # add scanner translation to the center
                center = metadata.get(model.MD_POS, (0, 0))
                tran = self._scanner.translation.value # px, hopefully hasn't been changed since data generation
                pxs = self._scanner.pixelSize.value # m/px
                metadata[model.MD_POS] = (center[0] + tran[0] * pxs[0],
                                          center[1] + tran[1] * pxs[1])

                # write and read the raw data
                try:
                    rbuf = self.write_read_2d_data_raw(wchannels, wranges, rchannels,
                                                    rranges, period, margin, osr, scan)
                except (IOError, comedi.ComediError):
                    # could be genuine or just due to cancellation
                    if self._acquisition_must_stop.is_set():
                        return

                    nfailures += 1
                    if nfailures == 5:
                        logging.exception("Acquisition failed %d times in a row, giving up", nfailures)
                        return
                    else:
                        logging.exception("Acquisition failed, will retry")
                        time.sleep(1)
                        self._reset_device()
                        continue
                except CancelledError:
                    # either because must be stopped or settings updated
                    logging.debug("Acquisition was cancelled")
                    continue

                nfailures = 0
                #logging.debug("Converting raw data to physical: %s", rbuf)
                # TODO decimate/convert the data while reading, to save time, or do not convert at all

                # the channels to acquire might have changed, only send to the one
                # still interested
                with self._acquisition_data_lock:
                    acq = dict(self._acquisitions) # duplicate
                for i, c in enumerate(rchannels):
                    callback = None
                    for d, cb in acq.items():
                        if d.channel == c:
                            callback = cb
                            break
                    if callback is None: # unsubscribed
                        continue

                    # Convert to a nice 2D DataArray
                    parray = rbuf[i]
                    darray = model.DataArray(parray, metadata)
                    callback(darray)

                # force the GC to non-used buffers, for some reason, without this
                # the GC runs only after we've managed to fill up the memory
                gc.collect()

        except:
            logging.exception("Unexpected failure during image acquisition")
        finally:
            try:
                self.set_to_resting_position()
            except comedi.ComediError:
                # can happen if the driver already terminated
                pass
            logging.debug("Acquisition thread closed")
            self._acquisition_must_stop.clear()

    def terminate(self):
        """
        Must be called at the end of the usage. Can be called multiple times,
        but the component shouldn't be used afterward.
        """
        if self._calibration:
            comedi.cleanup_calibration(self._calibration)
            self._calibration = None
        if self._device:
            # stop the acquisition thread if it's still running
            self._req_stop_acquisition()

            comedi.close(self._device)
            self._device = None

            # need to be done explicitly to catch exceptions
            self._reader.close()
            self._writer.close()

    def selfTest(self):
        # let's see if we can get data from each channel, any data is good
        channels = []
        for d in self._detectors.values():
            channel = d.channel
            channels.append(channel)
            try:
                data = comedi.data_read(self._device, self._ai_subdevice,
                                        channel, 0, comedi.AREF_GROUND)
            except comedi.ComediError:
                logging.info("Failed to read data from channel %d", channel)
                return False

        # try to read multiple values from all the channel simultaneously
        try:
            array = self._get_data(channels, self._min_ai_periods[len(channels)], 10)
        except comedi.ComediError:
            array = None
        if not array or len(array) != len(channels) or array[0].shape != (10, 1):
            logging.info("Failed to read multiple data from channels %r", channels)
            return False

        return True

    @staticmethod
    def scan():
        """
        List all the available comedi devices compatible with the need for SEM.
        return (list of 2-tuple: name (string), kwargs (dict))
        """
        names = glob.glob('/dev/comedi?') # should not catch /dev/comedi0_subd*

        found = []
        for n in names:
            try:
                device = comedi.open(n)
            except comedi.ComediError:
                    continue
            try:
                logging.debug("Checking comedi device '%s'", n)

                # Should have at least one analog input and an analog output with 2 channels
                try:
                    ai_subdevice = comedi.find_subdevice_by_type(device,
                                                    comedi.SUBD_AI, 0)
                    ao_subdevice = comedi.find_subdevice_by_type(device,
                                                    comedi.SUBD_AO, 0)
                except comedi.ComediError:
                    continue

                if comedi.get_n_channels(device, ai_subdevice) < 1:
                    continue
                if comedi.get_n_channels(device, ao_subdevice) < 2:
                    continue


                # create the args for one detector
                range_info = comedi.get_range(device, ai_subdevice, 0, 0)
                limits = [range_info.min, range_info.max]
                kwargs_d0 = {"name": "detector0", "role":"detector",
                             "channel": 0, "limits": limits}

                # create the args for the scanner
                wchannels = [0, 1]
                limits = []
                for c in wchannels:
                    # TODO check every range, not just the first one
                    range_info = comedi.get_range(device, ao_subdevice, c, 0)
                    limits.append([range_info.min, range_info.max])

                # find min_period for AO, as settle_time
                cmd = comedi.cmd_struct()
                try:
                    comedi.get_cmd_generic_timed(device, ao_subdevice, cmd, len(wchannels), 1)
                except comedi.ComediError:
                    #continue
                    cmd.scan_begin_arg = 0

                # Check disabled, to allow comedi_test to be compatible
#                if cmd.scan_begin_src != comedi.TRIG_TIMER:
#                    continue # no timer => impossible to use the device
                min_ao_period = cmd.scan_begin_arg / 1e9

                kwargs_s = {"name": "scanner", "role":"ebeam",
                            "limits": limits, "channels": wchannels,
                            "settle_time": min_ao_period, "hfw_nomag": 10e-3}

                name = "SEM/" + comedi.get_board_name(device)
                kwargs = {"device": n,
                          "children": {"detector0": kwargs_d0, "scanner": kwargs_s}}
                found.append((name, kwargs))

            finally:
                comedi.close(device)

        return found



class Accesser(object):
    """
    Abstract class to access the device either for input or output
    Each acquisition should be done by calling in order prepare(), run(), and wait() 
    """

    def __init__(self, parent):
        """
        parent (SEMComedi)
        """
        self.parent = parent
        self._device = parent._device # the device id to pass to comedi

        # they are pointing to the same "file", but must be different objects
        # to be able to read and write simultaneously.
        # Closing any of them will close the device as well
        self.file = os.fdopen(parent._fileno, 'rb+', 0) # buffer = 0 => flush should be not necessary
        self.cancelled = False
        self.thread = None
        self.duration = None
        self.buf = None

    def close(self):
        """
        To be called before deleting it
        """
        # Probably going to fail as they point to the same "file" as the device
        # If we don't do it explicitly, it will be done automatically on garbage
        # collection, which will give a IOError that looks almost random.
        try:
            self.file.close()
        except IOError:
            pass

    def prepare(self):
        pass

    def run(self):
        pass

    def wait(self, timeout=None):
        pass

    def cancel(self):
        pass

class Reader(Accesser):
    """
    Classical version of the reader, using a... read() (aka fromfile()). It's
    supposed to avoid latency as the read will return as soon as all the data 
    is received. But in the current behaviour of comedi, the cancel() is really
    complicated and unstable, as a new empty command must be sent (reported 
    upstream and a fix was published on 2013-07-08).
    """
    def __init__(self, parent):
        Accesser.__init__(self, parent)
        self._subdevice = parent._ai_subdevice

        self.dtype = parent._get_dtype(self._subdevice)
        self.buf = None
        self.count = None
        self._lock = threading.Lock()

    def prepare(self, count, duration):
        """
        count: number of values to read
        duration: expected total duration it will take (in s)
        """
        with self._lock:
            self.count = count
            self.duration = duration
            self.cancelled = False
            if self.thread and self.thread.isAlive():
                logging.warning("Preparing a new acquisition while previous one is not over")
            self.thread = threading.Thread(name="SEMComedi reader", target=self._thread)
            self.file.seek(0)

    def run(self):
        if self.cancelled:
            raise CancelledError("Reader thread was cancelled")
        # start reader thread
        self._begin = time.time()
        self.thread.start()

    def _thread(self):
        """To be called in a separate thread"""
        try:
            self.buf = numpy.fromfile(self.file, dtype=self.dtype, count=self.count)
            logging.debug("read took %g s", time.time() - self._begin)
        except IOError:
            # might be due to a cancel
            logging.debug("Read ended before the end")
        except:
            logging.exception("Unhandled error in reading thread")

    def wait(self, timeout=None):
        """
        timeout (float): maximum number of seconds to wait for the read to finish
        """
        timeout = timeout or self.duration

        # Note: join is pretty costly when timeout is not None, because it'll
        # do long sleeps between each checks.
        self.thread.join(timeout)
        logging.debug("Waited for the read thread for actually %g s", time.time() - self._begin)
        if self.cancelled:
            if self.thread.isAlive():
                self.thread.join(1) # waiting for the cancel to finish
            raise CancelledError("Reading thread was cancelled")
        elif self.thread.isAlive():
            logging.warning("Reading thread is still running after %g s", timeout)
            self.cancel()

        # the result should be in self.buf
        if self.buf is None:
            raise IOError("Failed to read all the %d expected values" % self.count)
        elif self.buf.size != self.count:
            raise IOError("Read only %d values from the %d expected" % (self.buf.size, self.count))

        return self.buf

    def cancel(self):
        with self._lock:
            logging.debug("Cancelling read")
            if not self.thread or self.cancelled:
                return

            try:
                comedi.cancel(self._device, self._subdevice)
                self.cancelled = True
            except comedi.ComediError:
                logging.debug("Failed to cancel read")

            # if the thread is stopped/not started, it's all fine
            if not self.thread.isAlive():
                return
            self.thread.join(0.5) # wait maximum 0.5 s
            if not self.thread.isAlive():
                return

            # Currently, after cancelling a comedi command, the read doesn't
            # unblock. A trick to manage to stop a current read, is to give a
            # new command of few reads (e.g., 1 read), on any channel
            logging.debug("Seems the read didn't end, will force it")
            try:
                cmd = comedi.cmd_struct()
                comedi.get_cmd_generic_timed(self._device, self._subdevice, cmd, 1, 0)
                clist = comedi.chanlist(1)
                clist[0] = comedi.cr_pack(0, 0, comedi.AREF_GROUND)
                cmd.chanlist = clist
                cmd.stop_src = comedi.TRIG_COUNT
                cmd.stop_arg = 1
                comedi.command(self._device, cmd)
            except comedi.ComediError:
                logging.error("Failed to give read command of 1 element")

            self.thread.join(0.5) # wait maximum 0.5 s
            try:
                comedi.cancel(self._device, self._subdevice)
            except comedi.ComediError:
                logging.debug("Failed to cancel read")

            if self.thread.isAlive():
                logging.warning("failed to cancel fully the reading thread")

class MMapReader(Reader):
    """
    MMap based reader. It might introduce a very little bit of latency to detect
    the end of a complete acquisition read, but has the advantage of being much
    simpler to cancel. However, there seems to be a bug with detecting the end
    of a read, and it tends to read too much data.
    """
    def __init__(self, parent):
        Reader.__init__(self, parent)
        self.mmap_size = comedi.get_buffer_size(self._device, self._subdevice)
        self.mmap = mmap.mmap(self.parent._fileno, self.mmap_size, access=mmap.ACCESS_READ)

    def close(self):
        Reader.close(self)

    def prepare(self, count, duration):
        with self._lock:
            self.count = count
            self.duration = duration
            self.buf = numpy.empty(count, dtype=self.dtype)
            self.remaining = self.buf.nbytes
            self.buf_offset = 0
            self.mmap.seek(0)
            self.cancelled = False
            if self.thread and self.thread.isAlive():
                logging.warning("Preparing a new acquisition while previous one is not over")
            self.thread = threading.Thread(name="SEMComedi reader", target=self._thread)

    # run() is identical

    # Code inspired by pycomedi
    def _thread(self):
        # time it takes to read 10% of the buffer at maximum speed
        sleep_time = ((self.mmap_size / 10) / self.buf.itemsize) * self.parent._min_ai_periods[1]
        # at least 1 ms, for scheduler, and 100 ms for cancel latency
        sleep_time = min(0.1, max(sleep_time, 0.001))
        try:
            while self.remaining > 0 and not self.cancelled:
                avail_bytes = comedi.get_buffer_contents(self._device, self._subdevice)
                if avail_bytes > 0:
#                    logging.debug("Need to read %d bytes from mmap", avail_bytes)
                    read_bytes = self._act(avail_bytes)
                    self.buf_offset += read_bytes
                    self.remaining -= read_bytes
                else:
                    # a bit of time to fill the buffer
                    if self.remaining < (self.mmap_size / 10):
                        # almost the end, finish quickly
                        sleep_time = self.remaining / self.buf.itemsize * self.parent._min_ai_periods[1]
                    time.sleep(sleep_time)

            # TODO: it seems that cancel prevent from reading the buffer, but
            # next command everything left will still be there. So we end up
            # with more to read on the next read.
            offset = comedi.get_buffer_offset(self._device, self._subdevice)
            logging.debug("Offset after reading = %d", offset)
            time.sleep(0.01)
            avail_bytes = comedi.get_buffer_contents(self._device, self._subdevice)
            total = self.buf.nbytes
            while avail_bytes > 0:
                if self.cancelled:
                    logging.debug("Flushing %d bytes", avail_bytes)
                else:
                    logging.warning("Still able to read %d bytes", avail_bytes)
                comedi.mark_buffer_read(self._device, self._subdevice, avail_bytes)
                total += avail_bytes
                comedi.poll(self._device, self._subdevice)
                time.sleep(0.01)
                avail_bytes = comedi.get_buffer_contents(self._device, self._subdevice)
            logging.debug("Got %d bytes, while expected %d", total, self.buf.nbytes)
            offset = comedi.get_buffer_offset(self._device, self._subdevice)
            logging.debug("Offset at end = %d", offset)


            logging.debug("read took %g s", time.time() - self._begin)
        except:
            logging.exception("Unhandled error in reading thread")
        finally:
            if not self.cancelled:
                #comedi.cancel(self._device, self._subdevice)
                pass

    def _act(self, avail_bytes):
        read_size = min(avail_bytes, self.remaining)
        if self.mmap.tell() + read_size >= self.mmap_size - 1:
            read_size = self.mmap_size - self.mmap.tell()
            wrap = True
        else:
            wrap = False

        # mmap_action = copy to numpy array
        offset = self.buf_offset / self.buf.itemsize
        s = read_size / self.buf.itemsize
        self.buf[offset:offset + s] = numpy.fromstring(self.mmap.read(read_size),
                                                       dtype=self.dtype)
        comedi.mark_buffer_read(self._device, self._subdevice, read_size)
        if wrap:
            self.mmap.seek(0)

        return read_size

    def wait(self, timeout=None):
        timeout = timeout or (self.duration + 1)

        # Note: join is pretty costly when timeout is not None, because it'll
        # do long sleeps between each checks.
        self.thread.join(timeout)
        logging.debug("Waited for the read thread for actually %g s", time.time() - self._begin)
        if self.cancelled:
            if self.thread.isAlive():
                self.thread.join(1) # waiting for the cancel to finish
            raise CancelledError("Reading thread was cancelled")
        elif self.thread.isAlive():
            logging.warning("Reading thread is still running after %g s", timeout)
            self.cancel()

        # the result should be in self.buf
        if self.buf is None:
            raise IOError("Failed to read all the %d expected values" % self.count)
        elif self.remaining != 0:
            raise IOError("Read only %d values from the %d expected" %
                          (self.count - self.remaining / self.buf.itemsize), self.count)

        return self.buf

    def cancel(self):
        with self._lock:
            if not self.thread or self.cancelled or not self.thread.isAlive():
                return

            logging.debug("Cancelling read")
            try:
                comedi.cancel(self._device, self._subdevice)
                self.cancelled = True
            except comedi.ComediError:
                logging.warning("Failed to cancel read")

            self.thread.join(0.5)

        # if the thread is stopped, it's all fine
        if self.thread.isAlive():
            logging.warning("Failed to stop the reading thread")

class Writer(Accesser):
    def __init__(self, parent):
        Accesser.__init__(self, parent)
        self._subdevice = parent._ao_subdevice

        self._expected_end = 0
        self._preload_size = None
        self._lock = threading.Lock()

    def prepare(self, buf, duration):
        """
        buf (numpy.ndarray): 1 dimension array to write
        duration: expected total duration it will take (in s)
        """
        with self._lock:
            self.duration = duration
            self.buf = buf
            self.cancelled = False
            self.file.seek(0)

            # preload the buffer with enough data first
            dev_buf_size = comedi.get_buffer_size(self._device, self._subdevice)
            self._preload_size = dev_buf_size / buf.itemsize
            logging.debug("Going to preload %d bytes", buf[:self._preload_size].nbytes)
            buf[:self._preload_size].tofile(self.file)
            self.file.flush() # it can block here if we preload too much

            self.thread = threading.Thread(name="SEMComedi writer", target=self._thread)

    def run(self):
        self._begin = time.time()
        self._expected_end = time.time() + self.duration
        self.thread.start()

    def _thread(self):
        """
        Ends once the output is fully over 
        """
        if self.cancelled:
            return
        try:
            self.buf[self._preload_size:].tofile(self.file)
            if self.cancelled:
                return
            self.file.flush()

            # TODO: investigate further this issue and report upstream.
            # Need a C sample code (also seems to work ok if period is min_period)
            # There seems to be is a bug in the NI comedi driver that cause writes
            # of only one scan to never finish. So we force the stop by cancelling
            # after enough time.
            if self.buf.size <= 2:
                left = self._expected_end - time.time()
                if left > 0:
                    time.sleep(left)
                comedi.cancel(self._device, self._subdevice)
                return
        except (IOError, comedi.ComediError):
            # might be due to a cancel
            logging.debug("Write ended before the end")
        except:
            logging.exception("Unhandled error in writing thread")

    def wait(self, timeout=None):
        now = time.time()
        if timeout is None:
            timeout = max(now - self._expected_end + 1, 0.1)
        max_time = now + timeout

        self.thread.join(timeout)

        # Wait until the buffer is fully emptied to state the output is over
        while (comedi.get_subdevice_flags(self._device, self._subdevice)
               & comedi.SDF_RUNNING):
            # sleep longer if the end is far away
            left = min(self._expected_end - time.time(), 0.1)
            if left > 0.01:
                time.sleep(left / 2)
            else:
                time.sleep(0) # just yield

            if time.time() > max_time:
                comedi.cancel(self._device, self._subdevice)
                raise IOError("Write timeout while device is still generating data")

        if self.thread.isAlive():
            comedi.cancel(self._device, self._subdevice)
            raise IOError("Write timeout while device is idle")

        if self.cancelled:
            raise CancelledError("Writer thread was cancelled")

        # According to https://groups.google.com/forum/?fromgroups=#!topic/comedi_list/yr2U179x8VI
        # To finish a write fully, we need to do a cancel().
        # Wait until SDF_RUNNING is gone, then cancel() to reset SDF_BUSY
        comedi.cancel(self._device, self._subdevice)

        logging.debug("Write finished after %g s, while expected  %g s",
                      time.time() - self._begin, self.duration)

    def cancel(self):
        """
        Warning: it's not possible to cancel a thread which has not already been 
         prepared.
        """
        try:
            if not self.thread or self.cancelled:
                return

            logging.debug("Cancelling write")
            with self._lock:
                comedi.cancel(self._device, self._subdevice)
                self.cancelled = True
                logging.debug("Write cmd cancel sent")

            if not self.thread.isAlive():
                return

            self.thread.join(0.5)
        except comedi.ComediError:
            logging.debug("Failed to cancel write")


class FakeWriter(Accesser):
    """
    Same as Writer, but does nothing => for the comedi_test driver
    """
    def __init__(self, parent):
        self.duration = None
        self._must_stop = threading.Event()
        self._expected_end = 0

    def close(self):
        pass

    def prepare(self, buf, duration):
        self.duration = duration
        self._must_stop.clear()

    def run(self):
        self._expected_end = time.time() + self.duration

    def wait(self, timeout=None):
        left = self._expected_end - time.time()
        if left > 0:
            logging.debug("simulating a write for another %g s", left)
            self._must_stop.wait(left)

    def cancel(self):
        self._must_stop.set()

class Scanner(model.Emitter):
    """
    Represents the e-beam scanner
    
    Note that the .resolution, .translation, .scale and .rotation VAs are 
      linked, so that the region of interest stays approximately the same (in
      terms of physical space). So to change them to specific values, it is 
      recommended to set them in the following order: Rotation > Scale > 
      Resolution > Translation.  
    """
    def __init__(self, name, role, parent, channels, limits, settle_time,
                 hfw_nomag, **kwargs):
        """
        channels (2-tuple of (0<=int)): output channels for X/Y to drive. X is
          the fast scanned axis, Y is the slow scanned axis. 
        limits (2x2 array of float): lower/upper bounds of the scan area in V.
          first dim is the X/Y, second dim is min/max. Ex: limits[0][1] is the
          voltage for the max value of X. 
        settle_time (0<=float<=1e-3): time in s for the signal to settle after
          each scan line
        hfw_nomag (0<float<=1): (theoretical) distance between horizontal borders 
          (lower/upper limit in X) if magnification is 1 (in m)
        """
        if len(channels) != 2:
            raise ValueError("E-beam scanner '%s' needs 2 channels" % (name,))

        if settle_time < 0:
            raise ValueError("Settle time of %g s for e-beam scanner '%s' is negative"
                             % (settle_time, name))
        elif settle_time > 1e-3:
            # a larger value is a sign that the user mistook in units
            raise ValueError("Settle time of %g s for e-beam scanner '%s' is too long"
                             % (settle_time, name))
        self._settle_time = settle_time

        if len(channels) != len(set(channels)):
            raise ValueError("Duplicated channels %r on device '%s'"
                             % (channels, parent._device_name))
        # write channel as Y/X, for compatibility with numpy
        self._channels = channels[::-1]
        nchan = comedi.get_n_channels(parent._device, parent._ao_subdevice)
        if nchan < max(channels):
            raise ValueError("Requested channels %r on device '%s' which has only %d output channels"
                             % (channels, parent._device_name, nchan))

        # write limits as Y/X, for compatibility with numpy
        # check the limits are reachable
        self._limits = limits[::-1]
        for i, channel in enumerate(channels):
            data_lim = self._limits[i]
            try:
                comedi.find_range(parent._device, parent._ao_subdevice,
                                  channel, comedi.UNIT_volt, data_lim[0], data_lim[1])
            except comedi.ComediError:
                raise ValueError("Data range between %g and %g V is too high for hardware." %
                                 (data_lim[0], data_lim[1]))

        # TODO: only set this to True if the order of the conversion polynomial <=1
        self._can_generate_raw_directly = True

        # It will set up ._shape and .parent
        model.Emitter.__init__(self, name, role, parent=parent, **kwargs)

        # In theory the shape depends on the X/Y ranges, the actual ranges that
        # can be used and the maxdata. For simplicity we just fix it to 2048
        # which is probably sufficient for most usages and almost always reachable
        # shapeX = (diff_limitsX / diff_bestrangeX) * maxdataX
        self._shape = (2048, 2048)

        # next two values are just to determine the pixel size
        # Distance between borders if magnification = 1. It should be found out
        # via calibration. We assume that image is square, i.e., VFW = HFW
        if not 0 <= hfw_nomag < 1:
            raise ValueError("hfw_nomag is %g m, while it should be between 0 and 1 m."
                             % hfw_nomag)
        self._hfw_nomag = hfw_nomag # m

        # Allow the user to modify the value, to copy it from the SEM software
        mag = 1e3 # pretty random value which could be real
        self.magnification = model.FloatContinuous(mag, range=[1, 1e9], unit="")
        self.magnification.subscribe(self._onMagnification)

        # pixelSize is the same as MD_PIXEL_SIZE, with scale == 1
        # == smallest size/ between two different ebeam positions
        pxs = (self.HFWNoMag / (self._shape[0] * mag),
               self.HFWNoMag / (self._shape[1] * mag))
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
        self.scale.subscribe(self._onScale, init=True) # to update metadata

        # (float) in rad => rotation of the image compared to the original axes
        # TODO: for now it's readonly because no rotation is supported
        self.rotation = model.FloatContinuous(0, [0, 2 * math.pi], unit="rad",
                                              readonly=True)

        # max dwell time is purely arbitrary
        range_dwell = (parent.find_closest_dwell_time(0), 1) # s
        self._osr = 1
        self.dwellTime = model.FloatContinuous(range_dwell[0], range_dwell,
                                               unit="s", setter=self._setDwellTime)

        # event to allow another component to synchronize on the beginning of
        # a pixel position. Only sent during an actual pixel of a scan, not for
        # the beam settling time or when put to rest.
        self.newPosition = model.Event()

        self._prev_settings = [None, None, None, None] # resolution, scale, translation, margin
        self._scan_array = None # last scan array computed

    @roattribute
    def channels(self):
        return self._channels

    @roattribute
    def settleTime(self):
        return self._settle_time

    @roattribute
    def HFWNoMag(self):
        return self._hfw_nomag

    def _onMagnification(self, mag):
        self._updatePixelSize()

    def _onScale(self, s):
        self._updatePixelSize()

    def _updatePixelSize(self):
        """
        Update the pixel size using the scale, HFWNoMag and magnification
        """
        mag = self.magnification.value
        self.parent._metadata[model.MD_LENS_MAG] = mag

        pxs = (self.HFWNoMag / (self._shape[0] * mag),
               self.HFWNoMag / (self._shape[1] * mag))
        # it's read-only, so we change it only via _value
        self.pixelSize._value = pxs
        self.pixelSize.notify(pxs)

        # If scaled up, the pixels are bigger
        pxs_scaled = (pxs[0] * self.scale.value[0], pxs[1] * self.scale.value[1])
        self.parent._metadata[model.MD_PIXEL_SIZE] = pxs_scaled

    def _setDwellTime(self, value):
        dt, self._osr = self.parent.find_best_oversampling_rate(value)
        return dt

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
        self.resolution.value = new_resolution # will call _setResolution()

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
        # FIXME: slightly pessimistic if there is no fuzzing as it could go
        # half a pixel further to reach the exact border.

        # compute the min/max of the shift. It's the same as the margin between
        # the centered ROI and the border, taking into account the scaling.
        max_tran = ((self._shape[0] - self._resolution[0] * self._scale[0]) / 2,
                    (self._shape[1] - self._resolution[1] * self._scale[1]) / 2)

        # between -margin and +margin
        tran = (max(min(value[0], max_tran[0]), -max_tran[0]),
                max(min(value[1], max_tran[1]), -max_tran[1]))
        return tran

    def updateMetadata(self, md):
        # we share metadata with our parent
        self.parent.updateMetadata(md)

    def get_resting_point_data(self):
        """
        Returns all the data needed to set the beam to a nice resting position
        returns: array (1D numpy.ndarray), channels (list of int), ranges (list of int):
            array is 2 raw values (X and Y positions)
            channels: the output channels to use
            ranges: the range index of each output channel
        """
        # Let's put it at the top-left, where the next acquisition will start.
        # It has the advantage of not being in the center, so less noticable
        # if there is an optical camera aligned.
        pos = [self._limits[0][0], self._limits[1][0]]
        ranges = []
        for i, channel in enumerate(self._channels):
            best_range = comedi.find_range(self.parent._device,
                                           self.parent._ao_subdevice,
                              channel, comedi.UNIT_volt, pos[i], pos[i])
            ranges.append(best_range)

        # computes the position in raw values
        rpos = self.parent._array_from_phys(self.parent._ao_subdevice,
                                              self._channels, ranges,
                                              numpy.array(pos, dtype=numpy.double))
        return rpos, self._channels, ranges

    def get_scan_data(self):
        """
        Returns all the data as it has to be written the device to generate a 
          scan.
        returns: array (3D numpy.ndarray), period (0<=float), shape (2-tuple int),
                 margin (0<=int), channels (list of int), ranges (list of int)
                 osr (1<=int):
          array is of shape HxWx2: H,W is the scanning area. dtype is fitting the
             device raw data. .shape = shape[0], shape[1] + margin, 2
          period: time between a pixel in s
          shape: H,W dimension of the scanned image (e.g., the resolution in numpy order)
          margin: amount of fake pixels inserted at the beginning of each (Y) line
            to allow for the settling time
          channels: the output channels to use
          ranges: the range index of each output channel
          osr: over-sampling rate, how many input samples should be acquired by pixel 
        Note: it only recomputes the scanning array if the settings have changed
        Note: it's not thread-safe, you must ensure no simultaneous calls.
        """
        dwell_time = self.dwellTime.value
        resolution = self.resolution.value
        scale = self.scale.value
        translation = self.translation.value
        margin = int(math.ceil(self._settle_time / dwell_time))

        new_settings = [resolution, scale, translation, margin]
        if self._prev_settings != new_settings:
            # TODO: if only margin changes, just duplicate the margin columns
            # need to recompute the scanning array
            self._update_raw_scan_array(resolution[-1::-1], scale[-1::-1],
                                        translation[-1::-1], margin)

            self._prev_settings = new_settings

        return (self._scan_array, dwell_time, resolution[-1::-1],
                margin, self._channels, self._ranges, self._osr)

    def _update_raw_scan_array(self, shape, scale, translation, margin):
        """
        Update the raw array of values to send to scan the 2D area.
        shape (list of 2 int): H/W=Y/X of the scanning area (slow, fast axis)
        scale (tuple of 2 float): scaling of the pixels
        translation (tuple of 2 float): shift from the center
        margin (0<=int): number of additional pixels to add at the begginning of
            each scanned line
        Warning: the dimensions follow the numpy convention, so opposite of user API
        returns nothing, but update ._scan_array and ._ranges.
        """
        area_shape = self._shape[-1::-1]
        # adapt limits according to the scale and translation so that if scale
        # == 1,1 and translation == 0,0 , the area is centered and a pixel is
        # the size of pixelSize
        roi_limits = [] # min/max for X/Y in V
        for i, lim in enumerate(self._limits):
            center = (lim[0] + lim[1]) / 2
            width = lim[1] - lim[0]
            ratio = (shape[i] * scale[i]) / area_shape[i]
            assert ratio <= 1 # cannot be bigger than the whole area
            pxv = width / area_shape[i] # V/px
            # pxv/2 is to ensure the point scanned of each pixel is at the center
            # of the area of each pixel
            roi_hwidth = (width * ratio - pxv) / 2
            shift = translation[i] * pxv
            roi_lim = (center + shift - roi_hwidth,
                       center + shift + roi_hwidth)
            if lim[0] < lim[1]:
                assert roi_lim[0] <= roi_lim[1]
                assert roi_lim[0] >= lim[0] and roi_lim[1] <= lim[1]
            else:
                assert roi_lim[0] >= roi_lim[1]
                assert roi_lim[0] <= lim[0] and roi_lim[1] >= lim[1]
            roi_limits.append(roi_lim)
        logging.debug("ranges X = %sV, Y = %sV", roi_limits[1], roi_limits[0])

        # if the conversion polynomial has degree <= 1, it's as precise and
        # much faster to generate directly the raw data.
        if self._can_generate_raw_directly:
            # Compute the best ranges for each channel
            ranges = []
            for i, channel in enumerate(self._channels):
                data_lim = roi_limits[i]
                best_range = comedi.find_range(self.parent._device,
                                               self.parent._ao_subdevice,
                                  channel, comedi.UNIT_volt, data_lim[0], data_lim[1])
                ranges.append(best_range)
            self._ranges = ranges

            # computes the limits in raw values
            limits = self.parent._array_from_phys(self.parent._ao_subdevice,
                                                  self._channels, ranges,
                                                  numpy.array(roi_limits, dtype=numpy.double))
            scan_raw = self._generate_scan_array(shape, limits, margin)
            self._scan_array = scan_raw
        else:
            limits = numpy.array(roi_limits, dtype=numpy.double)
            scan_phys = self._generate_scan_array(shape, limits, margin)

            # Compute the best ranges for each channel
            ranges = []
            for i, channel in enumerate(self._channels):
                data_lim = (scan_phys[:, i].min(), scan_phys[:, i].max())
                best_range = comedi.find_range(self.parent._device,
                                               self.parent._ao_subdevice,
                                  channel, comedi.UNIT_volt, data_lim[0], data_lim[1])
                ranges.append(best_range)
            self._ranges = ranges

            self._scan_array = self.parent._array_from_phys(self.parent._ao_subdevice,
                                            self._channels, ranges, scan_phys)

    @staticmethod
    def _generate_scan_array(shape, limits, margin):
        """
        Generate an array of the values to send to scan a 2D area, using linear
        interpolation between the limits. It's basically a saw-tooth curve on 
        the W dimension and a linear increase on the H dimension.
        shape (list of 2 int): H/W of the scanning area (slow, fast axis)
        limits (2x2 ndarray): the min/max limits of W/H
        margin (0<=int): number of additional pixels to add at the begginning of
            each scanned line
        returns (3D ndarray of shape[0] x (shape[1] + margin) x 2): the H/W
            values for each points of the array, with W scanned fast, and H 
            slowly. The type is the same one as the limits.
        """
        # prepare an array of the right type
        full_shape = (shape[0], shape[1] + margin, 2)
        scan = numpy.empty(full_shape, dtype=limits.dtype, order='C')

        # TODO see if meshgrid is faster (it needs to be in C order!)

        # Force the conversion to full number (e.g., instead of uint16), which
        # avoids linspace() to go crazy when limits are going down.
        pylimits = limits.tolist()
        # fill the X dimension
        scanx = scan[:, :, 0].swapaxes(0, 1) # just a view to have X as last dim
        scanx[:, :] = numpy.linspace(pylimits[0][0], pylimits[0][1], shape[0])
        # fill the Y dimension
        scan[:, margin:, 1] = numpy.linspace(pylimits[1][0], pylimits[1][1], shape[1])

        # fill the margin with the first pixel (X dimension is already filled)
        if margin:
            fp = scan[:, margin, 1]
            # use the transpose, as the broadcast rule is to extend on the row
            scan[:, :margin, 1].T[:] = fp

        return scan


class Detector(model.Detector):
    """
    Represents a detector activated by the e-beam. E.g., secondary electron 
    detector, backscatter detector.  
    """
    def __init__(self, name, role, parent, channel, limits, **kwargs):
        """
        channel (0<= int): input channel from which to read
        limits (2-tuple of number): min/max voltage to acquire (in V)
        Note: parent should have a child "scanner" alredy initialised
        """
        # It will set up ._shape and .parent
        model.Detector.__init__(self, name, role, parent=parent, **kwargs)
        self._channel = channel
        nchan = comedi.get_n_channels(parent._device, parent._ai_subdevice)
        if nchan < channel:
            raise ValueError("Requested channel %d on device '%s' which has only %d input channels"
                             % (channel, parent._device_name, nchan))
        self._scanner = parent._scanner

        # TODO allow limits to be None, meaning take the biggest range available
        self._limits = limits
        # find the range
        try:
            best_range = comedi.find_range(parent._device, parent._ai_subdevice,
                                           channel, comedi.UNIT_volt,
                                           limits[0], limits[1])
        except comedi.ComediError:
                raise ValueError("Data range between %g and %g V is too high for hardware." %
                                 (limits[0], limits[1]))
        self._range = best_range

        # The closest to the actual precision of the device
        self._maxdata = comedi.get_maxdata(parent._device, parent._ai_subdevice,
                                           channel)
        self._shape = (self._maxdata,) # only one point
        self.data = SEMDataFlow(self, parent)

    @roattribute
    def channel(self):
        return self._channel

    def updateMetadata(self, md):
        # we share metadata with our parent
        self.parent.updateMetadata(md)

class SEMDataFlow(model.DataFlow):
    def __init__(self, detector, sem):
        """
        detector (semcomedi.Detector): the detector that the dataflow corresponds to
        sem (semcomedi.SEMComedi): the SEM
        """
        model.DataFlow.__init__(self)
        self.component = weakref.ref(detector)
        self._sem = weakref.proxy(sem)

    # start/stop_generate are _never_ called simultaneously (thread-safe)
    def start_generate(self):
        try:
            # TODO specify if phys or raw, or maybe always raw and notify is
            # in charge of converting if we want phys
            self._sem.start_acquire(self.component(), self.notify)
        except ReferenceError:
            # sem/component has been deleted, it's all fine, we'll be GC'd soon
            pass

    def stop_generate(self):
        try:
            self._sem.stop_acquire(self.component())
            # Note that after that acquisition might still go on for a short time
        except ReferenceError:
            # sem/component has been deleted, it's all fine, we'll be GC'd soon
            pass

    def notify(self, data):
        # TODO: fast way to convert to physical values (and if possible keep
        # integers as output
        # converting to physical value would look a bit like this:
        # the ranges could be save in the metadata.
#        parray = self._array_to_phys(self_sem._ai_subdevice,
#                                       [self.component.channel], [rranges], data)
        # For now, the data seems linear enough that we don't care.
        model.DataFlow.notify(self, data)
