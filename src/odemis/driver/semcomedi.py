# -*- coding: utf-8 -*-
'''
Created on 15 Oct 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from odemis import model, __version__
from odemis.model._core import roattribute
import ctypes
import gc
import glob
import logging
import math
import numpy
import odemis.driver.comedi_simple as comedi
import os
import threading
import time
import weakref
#pylint: disable=E1101

#logging.getLogger().setLevel(logging.DEBUG)

# This is a module to drive a FEI Scanning electron microscope via the so-called
# "external X/Y" line. It uses a DA-conversion and acquisition (DAQ) card on the
# computer side to control the X/Y position of the electron beam (e-beam), while
# receiving the intensity sent by the secondary electron and/or backscatter
# detector. The DAQ card is handled via the (Linux) Comedi interface.
#
# Although it should in theory be quite generic, this driver is only tested on
# Linux with Comedilib 0.8.1, with a NI PCI 6251 DAQ card, and a FEI Quanta SEM.
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
# from_phys are not available and to_physical, from_physical only work if the
# device is hardware calibrated, it's not (yet?) implemented for software
# calibrated devices. For now there is no documentation but some examples.

NI_TRIG_AI_START1 = 18 # Trigger number for AI Start1 (= beginning of a command) 

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
            # they are pointing to the same "file", but must be different objects
            # to be able to read and write simultaneously.
            # Closing any of them will close the device as well
            self._rfile = os.fdopen(self._fileno, 'r+') 
            self._wfile = os.fdopen(self._fileno, 'r+')
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

        self._metadata = {model.MD_HW_NAME: self.getHwName()}
        self._swVersion = "%s (driver %s)" % (__version__.version, self.getSwVersion()) 
        self._metadata[model.MD_SW_VERSION] = self._swVersion
#        self._hwVersion = self.getHwVersion()
        self._metadata[model.MD_HW_VERSION] = self._hwVersion

        self._check_test_device()
        
        # detect when values are strange
        comedi.set_global_oor_behavior(comedi.OOR_NAN)
        self._init_calibration()
        
        # converters: dict (3-tuple int->number callable(number)):
        # subdevice, channel, range -> converter from value to value
        self._convert_to_phys = {}
        self._convert_from_phys = {}

        self._min_ai_periods, self._min_ao_periods = self._get_min_periods()
        # On the NI-6251, according to the doc:
        # AI is 1MHz (aggregate) (or 1.25MHz with only one channel)
        # AO is 2.86/2.0 MHz for one/two channels
        # => that's more or less what we get from comedi :-)
        
        # acquisition thread setup
        self._acquisition_data_lock = threading.Lock()
        self._acquisition_must_stop = threading.Event()
        self._acquisition_thread = None
        self._acquisitions = {} # detector -> callable (callback)
        
        # create the scanner child "scanner"
        try:
            kwargs = children["scanner"]
        except (KeyError, TypeError):
            raise KeyError("SEMComedi device '%s' was not given a 'scanner' child" % device)
        # TODO: min dwell time should be just for one input channel, and adapt if
        # more than one channel is read simultaneously
        # min dwell time is the worst of output and input minimun period
        nrchannels = len([n for n in children if n.startswith("detector")])
        min_period = max(self._min_ai_periods[nrchannels], self._min_ao_periods[2]) 
        self._scanner = Scanner(parent=self, min_dwell_time=min_period, daemon=daemon, **kwargs)
        self.children.add(self._scanner)
        
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
    
    # There are two temperature sensors:
    # * One on the board itself (TODO how to access it with Comedi?)
    # * One on the SCB-68. From the manual, the temperature sensor outputs
    #   10 mV/°C and has an accuracy of ±1 °C => T = 100 * Vt
    # We could create a VA for the board temperature. 

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
            self.write_read_data_raw = self.fake_write_read_data_raw
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
                return lambda d, r=range_info, m=maxdata: comedi.to_phys(d, r, m)
            else:
                return lambda d, r=range_info, m=maxdata: comedi.from_phys(d, r, m)
        else:
            # calibrated: return polynomial-based converter
            logging.debug("creating a calibrated converter for s%dc%dr%d",
                          subdevice, channel, range)
            if direction == comedi.TO_PHYSICAL:
                return lambda d, p=poly: comedi.to_physical(d, p)
            else:
                if poly.order > 1:
                    logging.info("polynomial of order %d, linear conversion would be imprecise",
                                 poly.order)
                return lambda d, p=poly: comedi.from_physical(d, p)
    
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
            for i in range(flat_data.size/nchans):
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
            
        logging.debug("Generating a new read command for %d scans", nscans)
        rcmd = comedi.cmd_struct()
        comedi.get_cmd_generic_timed(self._device, self._ai_subdevice,
                                                  rcmd, nchans, period_ns)
        clist = comedi.chanlist(nchans)
        for i in range(nchans):
            clist[i] = comedi.cr_pack(channels[i], rranges[i], comedi.AREF_GROUND)
        rcmd.chanlist = clist
        rcmd.stop_src = comedi.TRIG_COUNT
        rcmd.stop_arg = nscans
        self._prepare_command(rcmd)


        shape = (nscans, nchans)
        dtype = self._get_dtype(self._ai_subdevice)
        self._init_read_from_file()
        
        # run the commands
        logging.debug("Going to start the command")
        comedi.command(self._device, rcmd)
        self._read_from_file(dtype, shape[0] * shape[1])
        
        timeout = (nscans * period) * 1.10 + 1 # s   == expected time + 10% + 1s
        rbuf = self._wait_read_from_file(timeout)
        rbuf.shape = shape # FIXME: check that the order/stride is correct
        
        # convert data to physical values
        logging.debug("Converting raw data to physical: %s", rbuf)
        # TODO convert the data while reading, to save time
        # TODO do not convert the margin data
        # Allocate a separate memory block per channel as they'll later be used
        # completely separately
        parrays = []
        for i, c in enumerate(channels):
            parrays.append(self._array_to_phys(self._ai_subdevice,
                                   [c], [rranges[i]], rbuf[:,i,numpy.newaxis]))

        return parrays
        
        # TODO: be able to stop while reading, using comedi_cancel()
    
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
    
    def _run_inttrig(self, subdevice, num):
        """
        This is the same as calling comedi_internal_trigger(), so just for trying
        to use instructions."""
        insn = comedi.insn_struct()
        insn.subdev = subdevice
        insn.insn = comedi.INSN_INTTRIG
        insn.n = 1
        data = comedi.lsampl_array(insn.n)
        data[0] = num
        insn.data = data.cast()
        return comedi.do_insn(self._device, insn)
    
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
        clist = comedi.chanlist(nchans)
        for i, channel in enumerate(channels):
            data_lim = (data[:,i].min(), data[:,i].max())
            try:
                best_range = comedi.find_range(self._device, self._ao_subdevice, 
                                  channel, comedi.UNIT_volt, data_lim[0], data_lim[1])
            except comedi.ComediError:
                logging.exception("Data range between %g and %g V is too high for hardware." %
                              (data_lim[0], data_lim[1]))
                raise
            
            ranges.append(best_range)
            clist[i] = comedi.cr_pack(channel, best_range, comedi.AREF_GROUND)
        
        logging.debug("Generating a new command for %d scans", nscans)
        period_ns = int(round(period * 1e9))  # in nanoseconds
        cmd = comedi.cmd_struct()
        comedi.get_cmd_generic_timed(self._device, self._ao_subdevice,
                                                  cmd, nchans, period_ns)
        
        cmd.chanlist = clist
        # the following are not necessary, already set by get_cmd_generic_timed
        #cmd.chanlist_len = nchans
        #cmd.scan_end_arg = nchans
        # start_src: to only start when we send an interrupt (necessary, to fill the buffer before starting)
        cmd.start_src = comedi.TRIG_INT
        cmd.start_arg = 0
#        cmd.start_src = comedi.TRIG_NOW
#        cmd.start_arg = 0
        cmd.stop_src = comedi.TRIG_COUNT
        cmd.stop_arg = nscans
        
        # clean up the command
        rc = comedi.command_test(self._device, cmd)
        if rc != 0:
            # on the second time, it should report 0, meaning "perfect"
            rc = comedi.command_test(self._device, cmd)
            if rc != 0:
                raise IOError("failed to prepare command")

        # readying the subdevice with the command (needs to be done before
        # writing anything to the device
        comedi.command(self._device, cmd)

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
        buf[:preload_size].tofile(self._wfile)
        logging.debug("Going to flush")
        self._wfile.flush()
        #d._wfile.write(buf[:preload_size].tostring())

        # run the command
        logging.debug("Going to start the command")
        
        start_time = time.time()
        comedi.internal_trigger(self._device, self._ao_subdevice, 0)
        
        logging.debug("Going to write %d bytes more", buf[preload_size:].nbytes)
        # TODO: can this handle faults? 
        buf[preload_size:].tofile(self._wfile)
        logging.debug("Going to flush")
        self._wfile.flush()
        
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

    def write_read_data_phys(self, wchannels, rchannels, period, data):
        """
        write data on the given analog output channels and read the same amount 
         synchronously on the given analog input channels
        wchannels (list of int): channels to write (in same the order as data)
        rchannels (list of int): channels to write (in same the order as data)
        period (float): sampling period in s (time between two writes on the same
         channel)
        data (numpy.ndarray of float): two dimension array to write (physical values)
          first dimension is along the time, second is along the channels
        return (list of 1D numpy.array with shape=data.shape[0] and dtype=float)
            the data read converted to physical value (volt) for each channel
        """
        assert len(wchannels) == data.shape[1]
        
        # pick nice ranges according to the data to write
        wranges = []
        for i, channel in enumerate(wchannels):
            data_lim = (data[:,i].min(), data[:,i].max())
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


        # TODO add parameter to select the range and ref for each rchannel
        rranges = []
        for i, channel in enumerate(rchannels):
            data_lim = (-10, 10)
            try:
                best_range = comedi.find_range(self._device, self._ai_subdevice, 
                                  channel, comedi.UNIT_volt, data_lim[0], data_lim[1])
            except comedi.ComediError:
                logging.exception("Data range between %g and %g V is too high for hardware." %
                              (data_lim[0], data_lim[1]))
                raise
            
            rranges.append(best_range)

        # write and read the raw data
        rbuf = self.write_read_data_raw(wchannels, wranges, rchannels, rranges, period, wbuf)
        
        # convert data to physical values
        logging.debug("Converting raw data to physical: %s", rbuf)
        # TODO convert the data while reading, to save time
        # TODO do not convert the margin data
        # Allocate a separate memory block per channel as they'll later be used
        # completely separately
        parrays = []
        for i, c in enumerate(rchannels):
            parrays.append(self._array_to_phys(self._ai_subdevice,
                                   [c], [rranges[i]], rbuf[:,i,numpy.newaxis]))

        return parrays
    def fake_write_read_data_raw(self, wchannels, wranges, rchannels, rranges, period, data):
        """
        Imitates write_read_data_raw() but just read data, for the comedi_test driver
        """
        nscans = data.shape[0]
        nwchans = data.shape[1]
        nrchans = len(rchannels)
        period_ns = int(round(period * 1e9))  # in nanoseconds
                
        logging.debug("Generating a new read command for %d scans", nscans)
        rcmd = comedi.cmd_struct()
        comedi.get_cmd_generic_timed(self._device, self._ai_subdevice,
                                                  rcmd, nrchans, period_ns)
        clist = comedi.chanlist(nrchans)
        for i in range(nrchans):
            clist[i] = comedi.cr_pack(rchannels[i], rranges[i], comedi.AREF_GROUND)
        rcmd.chanlist = clist
        rcmd.stop_src = comedi.TRIG_COUNT
        rcmd.stop_arg = nscans
        self._prepare_command(rcmd)

        # Checks the periods are the same
        if rcmd.scan_begin_arg != period_ns:
            logging.warning("Asked dwell time of %g s, but got %g s", period, rcmd.scan_begin_arg / 1e9)

        
        # flatten the array
        wbuf = numpy.reshape(data, nscans * nwchans, order='C')

        # prepare read buffer info        
        rshape = (nscans, nrchans)
        rdtype = self._get_dtype(self._ai_subdevice)
        self._init_read_from_file()
        
        # run the commands
        comedi.command(self._device, rcmd)
        # AO is waiting for AI/Start1, so not sure why internal trigger needed,
        # but it is. Maybe just to let Comedi know that the command has started
        self._read_from_file(rdtype, rshape[0] * rshape[1])
        
        timeout = (nscans * period) * 1.10 + 1 # s   == expected time + 10% + 1s
        logging.debug("Waiting %g s for the acquisition to finish", timeout)
        rbuf = self._wait_read_from_file(timeout)
        rbuf.shape = rshape # FIXME: check that the order/stride is correct
        return rbuf
    
    def write_read_data_raw(self, wchannels, wranges, rchannels, rranges, period, data):
        """
        write data on the given analog output channels and read the same amount 
         synchronously on the given analog input channels
        wchannels (list of int): channels to write (in same the order as data)
        wranges (list of int): ranges of each write channel
        rchannels (list of int): channels to read (in same the order as data)
        rranges (list of int): ranges of each read channel
        period (float): sampling period in s (time between two writes on the same
         channel)
        data (numpy.ndarray of float): two dimension array to write (raw values)
          first dimension is along the time, second is along the channels
        return (2D numpy.array with shape=data.shape and dtype=float)
            the data read (raw) for each channel
        """
        nscans = data.shape[0]
        nwchans = data.shape[1]
        nrchans = len(rchannels)
        period_ns = int(round(period * 1e9))  # in nanoseconds
        
        # create a command for writing
        logging.debug("Generating new write and read commands for %d scans", nscans)
        wcmd = comedi.cmd_struct()
        comedi.get_cmd_generic_timed(self._device, self._ao_subdevice,
                                                  wcmd, nwchans, period_ns)
        clist = comedi.chanlist(nwchans)
        for i in range(nwchans):
            clist[i] = comedi.cr_pack(wchannels[i], wranges[i], comedi.AREF_GROUND)
        wcmd.chanlist = clist
#        wcmd.start_src = comedi.TRIG_INT
#        wcmd.start_arg = 0
        # from PyComedi docs
        wcmd.start_src = comedi.TRIG_EXT
        wcmd.start_arg = NI_TRIG_AI_START1 # when the AI starts reading 
        wcmd.stop_src = comedi.TRIG_COUNT
        wcmd.stop_arg = nscans
        self._prepare_command(wcmd)

        # create a command for reading
        rcmd = comedi.cmd_struct()
        comedi.get_cmd_generic_timed(self._device, self._ai_subdevice,
                                                  rcmd, nrchans, period_ns)
        clist = comedi.chanlist(nrchans)
        for i in range(nrchans):
            clist[i] = comedi.cr_pack(rchannels[i], rranges[i], comedi.AREF_GROUND)
        rcmd.chanlist = clist
        rcmd.start_src = comedi.TRIG_INT # start synchronously with the write
        rcmd.start_arg = 0
        rcmd.stop_src = comedi.TRIG_COUNT
        rcmd.stop_arg = nscans
        self._prepare_command(rcmd)

        # Checks the periods are the same
        assert(rcmd.scan_begin_arg == wcmd.scan_begin_arg)
        if rcmd.scan_begin_arg != period_ns:
            logging.warning("Asked dwell time of %g s, but got %g s", period, rcmd.scan_begin_arg / 1e9)
        # TODO: if periods are different => pick the closest period that works for both

        # readying the subdevice with the command (needs to be done before
        # writing anything to the device)
        comedi.command(self._device, wcmd)
        comedi.command(self._device, rcmd)
        
        # flatten the array
        wbuf = numpy.reshape(data, nscans * nwchans, order='C')
        self._init_write_to_file(wbuf)

        # prepare read buffer info        
        rshape = (nscans, nrchans)
        rdtype = self._get_dtype(self._ai_subdevice)
        self._init_read_from_file()
        
        # run the commands
        # AO is waiting for AI/Start1, so not sure why internal trigger needed,
        # but it is. Maybe just to let Comedi know that the command has started
        comedi.internal_trigger(self._device, self._ao_subdevice, 0)
        comedi.internal_trigger(self._device, self._ai_subdevice, 0)
        self._read_from_file(rdtype, rshape[0] * rshape[1])
        self._write_to_file()

        timeout = (nscans * period) * 1.10 + 1 # s   == expected time + 10% + 1s
        logging.debug("Waiting %g s for the acquisition to finish", timeout)
        self._wait_write_to_file(timeout)
        rbuf = self._wait_read_from_file(timeout)
        rbuf.shape = rshape # FIXME: check that the order/stride is correct
        return rbuf


    def _init_read_from_file(self):
        pass
    
    def _read_from_file(self, dtype, count):
        # start reader thread
        self._rbuf = None # FIXME: so ugly!!!
        self._rcount = count
        self._rthread = threading.Thread(target=self._read_from_file_thread,
                                   args=(dtype, count))
        self._rthread.start()
    
    def _read_from_file_thread(self, dtype, count):
        """To be called in a separate thread"""
        self._rbuf = numpy.fromfile(self._rfile, dtype=dtype, count=count)
    
    def _wait_read_from_file(self, timeout):
        """
        Call it after the wait_write
        """
        self._rthread.join(timeout)
        if self._rthread.isAlive():
            comedi.cancel(self._device, self._ai_subdevice)
        
        # the result should be in self._rbuf
        if self._rbuf is None:
            raise IOError("Failed to read all the %d expected values" % (self._rcount, ))
        elif self._rbuf.size != self._rcount:
            raise IOError("Read only %d values from the %d expected" % (self._rbuf.size, self._rcount))
    
        return self._rbuf
    
    def _init_write_to_file(self, buf):
        self._wbuf = buf
        # preload the buffer with enough data first
        dev_buf_size = comedi.get_buffer_size(self._device, self._ao_subdevice)
        self._preload_size = dev_buf_size / self._wbuf.itemsize
        logging.debug("Going to preload %d bytes", self._wbuf[:self._preload_size].nbytes)
        self._wbuf[:self._preload_size].tofile(self._wfile)
        self._wfile.flush() # it can block here if we preload too much
    
    def _write_to_file(self):
        self._wthread = threading.Thread(target=self._write_to_file_thread)
        self._wthread.start()    
    
    def _write_to_file_thread(self):
        """To be called in a separate thread"""
        # TODO: can this handle faults? 
        self._wbuf[self._preload_size:].tofile(self._wfile)
        self._wfile.flush()
        
    def _wait_write_to_file(self, timeout):
        try:
            self._wthread.join(timeout)
            if self._wthread.isAlive():
                # try to see why
                flags = comedi.get_subdevice_flags(self._device, self._ao_subdevice)
                if flags & comedi.SDF_RUNNING:
                    raise IOError("Write timeout while device is still generating data")
                else:
                    raise IOError("Write timeout while device is idle")
        finally:
            # According to https://groups.google.com/forum/?fromgroups=#!topic/comedi_list/yr2U179x8VI
            # To finish a write fully, we need to do a cancel().
            # Wait until SDF_RUNNING is gone, then cancel() to reset SDF_BUSY
            comedi.cancel(self._device, self._ao_subdevice)
    
    @staticmethod
    def _scan_result_to_array(data, shape, margin):
        """
        Converts a linear array resulting from a scan to a 2D array
        data (1D ndarray): the linear array, of shape=shape[0]*(shape[1] + margin)
        shape (2-tuple of int): shape of the array
        margin (0<=int): number of pixels at the beginning of each line to skip
        """
        # reshape to a 2D array with margin
        rectangle = numpy.reshape(data, (shape[0], shape[1] + margin))
        # trim margin
        return rectangle[:, margin:]  
    
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
            if not self._acquisitions:
                # Nothing to acquire => stop the thread (almost) immediately
                self._req_stop_acquisition()
    
    def _req_stop_acquisition(self):
        """
        Request the acquisition thread to stop
        """
        self._acquisition_must_stop.set()
        try:
            # make sure it stops quickly
            comedi.cancel(self._device, self._ai_subdevice)
            comedi.cancel(self._device, self._ao_subdevice)
        except comedi.ComediError:
            pass
    
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
            self._acquisition_thread = None
        
    def _acquisition_run(self):
        """
        Acquire images until asked to stop. Sends the raw acquired data to the
          callbacks.
        Note: to be run in a separate thread
        """
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
            scan, period, resolution, margin, wchannels, wranges = self._scanner.get_scan_data()
            
            metadata = dict(self._metadata) # duplicate
            metadata[model.MD_ACQ_DATE] = time.time() # time at the beginning
            metadata[model.MD_DWELL_TIME] = period 
            
            # write and read the raw data
            rbuf = self.write_read_data_raw(wchannels, wranges, rchannels, rranges, period, scan)
        
            #logging.debug("Converting raw data to physical: %s", rbuf)
            # TODO convert the data while reading, to save time, or do not convert at all
            # TODO do not convert the margin data
            
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
                parray = self._scan_result_to_array(rbuf[:,i,numpy.newaxis], resolution, margin)
                darray = model.DataArray(parray, metadata)
                callback(darray)
            
            # force the GC to non-used buffers, for some reason, without this
            # the GC runs only after we've managed to fill up the memory
            gc.collect()
    
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
            
            # Probably going to fail as they point to the same "file"
            # If we don't do it here, it will be done automatically on garbage
            # collection, which will give a IOError that looks almost random.  
            try:
                self._rfile.close()
            except IOError:
                pass
            try:
                self._wfile.close()
            except IOError:
                pass
    
    # TODO selfTest() which tries to read some data
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
        if not array or len(array) != len(channels) or array[0].shape != (10,1):
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


class Scanner(model.Emitter):
    """
    Represents the e-beam scanner
    """
    def __init__(self, name, role, parent, channels, limits, settle_time, 
                 min_dwell_time, hfw_nomag, **kwargs):
        """
        channels (2-tuple of (0<=int)): output channels for X/Y to drive
        limits (2x2 array of float): lower/upper bounds of the scan area in V.
          first dim is the X/Y, second dim is min/max. Ex: limits[0][1] is the
          voltage for the max value of X. 
        settle_time (0<=float<=1e-3): time in s for the signal to settle after
          each scan line
        min_dwell_time (0<=float): minimum dwell time in s. Provided by 
          the parent.
        pixel_size_no_mag (0<float<=1): (theoritical) distance between horizontal borders 
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
        
        self._channels = channels
        nchan = comedi.get_n_channels(parent._device, parent._ao_subdevice)
        if nchan < max(channels):
            raise ValueError("Requested channels %r on device '%s' which has only %d output channels" 
                             % (channels, parent._device_name, nchan))
        
        # check the limits are reachable
        self._limits = limits
        for i, channel in enumerate(channels):
            data_lim = self._limits[i]
            try:
                r = comedi.find_range(parent._device, parent._ao_subdevice, 
                                  channel, comedi.UNIT_volt, data_lim[0], data_lim[1])
            except comedi.ComediError:
                raise ValueError("Data range between %g and %g V is too high for hardware." %
                                 (data_lim[0], data_lim[1]))
        
        # TODO: only set this to True if the order of the convertion polynomial <=1
        self._can_generate_raw_directly = True
        
        # It will set up ._shape and .parent
        model.Emitter.__init__(self, name, role, parent=parent, **kwargs)
        
        # In theory the shape depends on the X/Y ranges, the actual ranges that
        # can be used and the maxdata. For simplicity we just fix it to 2048
        # which is probably sufficient for most usages and almost always reachable
        # shapeX = (diff_limitsX / diff_bestrangeX) * maxdataX
        self._shape = (2048, 2048)
        resolution = [256, 256] # small resolution to get a fast display
        self.resolution = model.ResolutionVA(resolution, [(1, 1), self._shape])
        self.resolution.subscribe(self._onResolution)
        
        # TODO: introduce .transformation, which is a 3x3 matrix that allows 
        # to specify the translation, rotation, and scaling applied to get the
        # conversion from coordinates to physical units.
        
        # max dwell time is purely arbitrary
        range_dwell = (min_dwell_time, 1) # s
        assert range_dwell[0] <= range_dwell[1]
        self.dwellTime = model.FloatContinuous(range_dwell[0], range_dwell, unit="s")

        # next two values are just to determine the pixel size
        # Distance between borders if magnification = 1. It should be found out
        # via calibration. We assume that image is square, i.e., VFW = HFW
        if hfw_nomag <= 0 or hfw_nomag > 1:
            raise ValueError("hfw_nomag is %g m, while it should be between 0 and 1 m." 
                             % hfw_nomag)
        self._hfw_nomag = hfw_nomag # m
        
        # Allow the user to modify the value, to copy it from the SEM software
        self.magnification = model.FloatContinuous(1e3, range=[1, 1e9], unit="")
        self.magnification.subscribe(self._onMagnification, init=True)

        self._prev_settings = [None, None, None] # resolution, margin, dwellTime
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
        
    def _onResolution(self, res):
        self._updatePixelSize()
    
    def _updatePixelSize(self):
        """
        Update the pixel size using the resolution, HFW_NoMag and magnification
        """
        # This is correct as long as there is not region of interest/transformation
        mag = self.magnification.value
        res = self.resolution.value
        pxs = [self.HFWNoMag / (res[0] * mag), self.HFWNoMag / (res[1] * mag)]
        self.parent._metadata[model.MD_PIXEL_SIZE] = pxs
    
    def get_scan_data(self):
        """
        Returns all the data as it has to be written the device to generate a 
          scan.
        returns: array (2D numpy.ndarray), period (0<=float), shape (2-tuple int),
                 margin (0<=int), channels (list of ints), ranges (list of float):
          array is of shape Nx2: N is the number of pixels. dtype is fitting the
             device raw data
          period: time between a pixel in s
          shape: X/Y dimension of the scanned image (e.g., the resolution)
          margin: amount of fake pixels inserted at the beginning of each Y line
            to allow for the settling time
          channels: the output channel to use
          ranges: the range index of each output channel
        Note: it only recomputes the scanning array if the settings have changed
        Note: it's not thread-safe!
        """
        dwell_time = self.dwellTime.value
        resolution = self.resolution.value
        margin = int(math.ceil(self._settle_time / dwell_time))
        
        prev_resolution, prev_margin, prev_dwell_time = self._prev_settings
        if prev_resolution != resolution or margin != prev_margin:
            # TODO: if only margin changes, just duplicate the margin columns
            # need to recompute the scanning array
            self._update_raw_scan_array(resolution, margin)
            
        self._prev_settings = [resolution, margin, dwell_time]
        return self._scan_array, dwell_time, resolution, margin, self._channels, self._ranges

    def _update_raw_scan_array(self, shape, margin):
        """
        Update the raw array of values to send to scan the 2D area.
        shape (list of 2 int): X/Y resolution of the scanning area
        margin (0<=int): number of additional pixels to add at the begginning of
            each scanned line
        returns nothing, but update ._scan_array and ._ranges.
        """
        # TODO: if the conversion polynom is order <= 1, it's as precise and
        # much faster to generate directly the raw data.
        if self._can_generate_raw_directly:
            # Compute the best ranges for each channel
            ranges = []
            for i, channel in enumerate(self._channels):
                data_lim = self._limits[i]
                best_range = comedi.find_range(self.parent._device,
                                               self.parent._ao_subdevice, 
                                  channel, comedi.UNIT_volt, data_lim[0], data_lim[1])
                ranges.append(best_range)
            self._ranges = ranges
            
            # computes the limits in raw values
            limits = self.parent._array_from_phys(self.parent._ao_subdevice,
                                                  self._channels, ranges, 
                                                  numpy.array(self._limits, dtype=numpy.double))

            scan_raw = self._generate_scan_array(shape, limits, margin)
            self._scan_array = scan_raw
        else:
            limits = numpy.array(self._limits, dtype=numpy.double)
            scan_phys = self._generate_scan_array(shape, limits, margin)
            
            # Compute the best ranges for each channel
            ranges = []
            for i, channel in enumerate(self._channels):
                data_lim = (scan_phys[:,i].min(), scan_phys[:,i].max())
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
        the Y dimension and a linear increase on the X dimension.
        shape (list of 2 int): X/Y resolution of the scanning area
        limits (2x2 ndarray): the min/max limits of X/Y
        margin (0<=int): number of additional pixels to add at the begginning of
            each scanned line
        returns (2D ndarray of (shape[0] x (shape[1] + margin)) x 2): the X/Y
            values for each points of the array, with Y scanned fast, and X 
            slowly. The type is the same one as the limits.
        """
        # prepare an array of the right type
        full_shape = (shape[0], shape[1] + margin, 2)
        scan = numpy.empty(full_shape, dtype=limits.dtype, order='C')
        
        # TODO see if meshgrid is faster (it needs to be in C order!) 
        
        # fill the X dimension
        scanx = scan[:,:,0].swapaxes(0,1) # just a view to have X as last dim
        scanx[:,:] = numpy.linspace(limits[0,0], limits[0,1], shape[0])
        # fill the Y dimension
        scan[:,margin:,1] = numpy.linspace(limits[1,0], limits[1,1], shape[1])
        
        # fill the margin with the first pixel
        if margin:
            fp = scan[:,margin,1,numpy.newaxis] # first pixel + add dimension
            fp.take([0] * margin, axis=1, out=scan[:,:margin,1]) # a copy of "margin" times 
        
        # reshape the array to a full flat scan values (the C order should make
        # sure that the array is fully continuous
        scan.shape = [full_shape[0] * full_shape[1], 2]
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
        self._shape = self._scanner.shape + (self._maxdata,)
        self.data = SEMDataFlow(self, parent)
        
    @roattribute
    def channel(self):
        return self._channel

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
        model.DataFlow.notify(self, data)
