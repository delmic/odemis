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
import ctypes
import glob
import logging
import numpy
import odemis.driver.comedi_simple as comedi
import os
import threading
import time
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
# arrays" for the instructions and sampl arrays, which are very unconvenient to 
# manipulate. To create a structure, you need to create an object with the name
# of the structure, plus _struct. 
# pycomedi is object-oriented. It tries to be less verbose but fails a bit
# because each object is in a separate module. At least it handles call errors
# as exceptions. It also has some non implemented parts, for example to_phys,
# from_phys are not available and to_physical, from_physical only work if the
# device is hardware calibrated, it's not (yet?) implemented for software
# calibrated devices. For now there is no documentation but some examples.

class SEMComedi(model.HwComponent):
    '''
    A generic HwComponent which provides children for controlling the scanning
    area and receiving the data from the detector of a SEM via Comedi.
    '''

    def __init__(self, name, role, children, device, **kwargs):
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
        model.HwComponent.__init__(self, name, role, children=None, **kwargs)
        
        try:
            self._device = comedi.open(self._device_name)
            self._fileno = comedi.fileno(self._device)
            self._file = os.fdopen(self._fileno, 'r+')
        except comedi.ComediError:
            raise ValueError("Failed to open DAQ device '%s'", device)
        
        try:
            self._ai_subdevice = comedi.find_subdevice_by_type(self._device,
                                            comedi.SUBD_AI, 0)
            self._ao_subdevice = comedi.find_subdevice_by_type(self._device,
                                            comedi.SUBD_AO, 0)
        except comedi.ComediError:
            raise ValueError("Failed to find both input and output on DAQ device '%s'", device)
        
        self._metadata = {model.MD_HW_NAME: self.getHwName()}
        self._swVersion = "%s (driver %s)" % (__version__.version, self.getSwVersion()) 
        self._metadata[model.MD_SW_VERSION] = self._swVersion
#        self._hwVersion = self.getHwVersion()
        self._metadata[model.MD_HW_VERSION] = self._hwVersion
        
        # detect when values are strange
        comedi.set_global_oor_behavior(comedi.OOR_NAN)
        self._init_calibration()
        
        # converters: dict (3-tuple int->number callable(number)):
        # subdevice, channel, range -> converter from value to value
        self._convert_to_phys = {}
        self._convert_from_phys = {}
    
    # There are two temperature sensors:
    # * One on the board itself (TODO how to access it with Comedi?)
    # * One on the SCB-68. From the manual, the temperature sensor outputs
    #   10 mV/°C and has an accuracy of ±1 °C => T = 100 * Vt
    # sudo ./cmd -f /dev/comedi0 -s 0 -c 0 -a 2 -n 1 -N 1 -p

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


    def _get_converter(self, subdevice, channel, range, direction):
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
            if direction == comedi.TO_PHYSICAL:
                return lambda d: comedi.to_phys(d, range_info, maxdata)
            else:
                return lambda d: comedi.from_phys(d, range_info, maxdata)
        else:
            # calibrated: return polynomial-based converter
            logging.debug("creating a calibrated converter for s%dc%dr%d",
                          subdevice, channel, range)
            if direction == comedi.TO_PHYSICAL:
                return lambda d: comedi.to_physical(d, poly)
            else:
                return lambda d: comedi.from_physical(d, poly)
    

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
        # get the cached converter, or create a new one
        try:
            converter = self._convert_to_phys[subdevice, channel, range]
        except KeyError:
            converter = self._get_converter(subdevice, channel, range, comedi.TO_PHYSICAL)
            self._convert_to_phys[subdevice, channel, range] = converter
        
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
        # get the cached converter, or create a new one
        try:
            converter = self._convert_from_phys[subdevice, channel, range]
        except KeyError:
            converter = self._get_converter(subdevice, channel, range, comedi.FROM_PHYSICAL)
            self._convert_from_phys[subdevice, channel, range] = converter
        
        return converter(value)

        
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
        
    def get_data(self, channels, period, size):
        """
        read n data from the given analog input channel
        channels (list of int): channels 
        period (float): sampling period in s
        size (0<int): number of data to read
        return (numpy.array with shape=(size, len(channels)) and dtype=float) 
        Note: this is only for testing, and will go away in the final version
        """
        #construct a comedi command
        
        nchans = len(channels) #number of channels
        aref = [comedi.AREF_GROUND] * nchans
        nscans = size
        
        ranges = []
        clist = comedi.chanlist(nchans)
        for i, channel in enumerate(channels):
            data_lim = (-10, 10)
            try:
                best_range = comedi.find_range(self._device, self._ai_subdevice, 
                                  channel, comedi.UNIT_volt, data_lim[0], data_lim[1])
            except comedi.ComediError:
                logging.exception("Data range between %g and %g V is too high for hardware." %
                              (data_lim[0], data_lim[1]))
                raise
            
            ranges.append(best_range)
            clist[i] = comedi.cr_pack(channel, best_range, aref[i])
        
        logging.debug("Generating a new command for %d scans", nscans)
        
        period_ns = int(round(period * 1e9))  # in nanoseconds
        cmd = comedi.cmd_struct()
        comedi.get_cmd_generic_timed(self._device, self._ai_subdevice,
                                                  cmd, nchans, period_ns)
        
        cmd.chanlist = clist # adjust for our particular context
#        cmd.chanlist_len = nchans
#        cmd.scan_end_arg = nchans
        cmd.stop_src = comedi.TRIG_COUNT
        cmd.stop_arg = nscans
        
        # clean up the command
        rc = comedi.command_test(self._device, cmd)
        if rc != 0:
            # on the second time, it should report 0, meaning "perfect"
            rc = comedi.command_test(self._device, cmd)
            if rc != 0:
                raise IOError("failed to prepare command")

        # run the command
        logging.debug("Going to start the command")
        comedi.command(self._device, cmd)

        shape = (nscans, nchans)
        dtype = self._get_dtype(self._ai_subdevice)
        nbytes = dtype.itemsize * shape[0] * shape[1]
        
        logging.debug("Going to read %d bytes", nbytes)
        # TODO: can this handle faults? 
        buf = numpy.fromfile(self._file, dtype=dtype, count=(shape[0] * shape[1]))
        
        # FIXME: needed? (probably not)
        comedi.cancel(self._device, self._ai_subdevice)
        
#        BUFSZ = 10000
#        while True:
#            data = os.read(fd, BUFSZ)
#            #print "len(data) = ", len(data)
#            if len(data) == 0:
#                break
#            n = len(data)/2 # 2 bytes per 'H'
        
        if buf.size != (shape[0] * shape[1]):
            raise IOError("Failed to read all the values from the %d expected", shape[0] * shape[1])
        buf.shape = shape # FIXME: check that the order/stride is correct
        
        logging.debug("Converting raw data to physical: %s", buf)
        
        # convert data to physical values
        # TODO convert the data while reading, to save time
        # TODO we probably prefer to have each channel as a separate array
        parray = numpy.empty(shape=shape, dtype=numpy.double)
        converters = []
        for i, c in enumerate(channels):
            converters.append(self._get_converter(self._ai_subdevice,
                                 c, ranges[i], comedi.TO_PHYSICAL))

        # converter needs lsampl (uint32). So for lsampl devices, it's pretty
        # straightforward, just a matter of convincing SWIG that a numpy.uint32
        # is a unsigned int. For sampl devices, everything need to be converted.
        if dtype.itemsize == 4:
            logging.debug("Using casting to access the raw data")
            # can just force-cast to a ctype buffer of unsigned int (that swig accepts)
            cbuf = numpy.ctypeslib.as_ctypes(buf)
            # TODO: maybe could be speed-up by something like vectorize()/map()?
            for i in range(buf.shape[0]):
                for j in range(buf.shape[1]):
                    parray[i,j] = converters[j](cbuf[i][j])
        else:
            # Needs real conversion
            logging.debug("Using full conversion to provide the raw data")
            for i, v in numpy.ndenumerate(buf):
                parray[i] = converters[i[1]](int(v))
        
        return parray
        
        
        # TODO: be able to stop while reading, using comedi_cancel()
    
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
        buf[:preload_size].tofile(self._file)
        logging.debug("Going to flush")
        self._file.flush()
        #d._file.write(buf[:preload_size].tostring())

        # run the command
        logging.debug("Going to start the command")
        
        start_time = time.time()
        comedi.internal_trigger(self._device, self._ao_subdevice, 0)
        
        logging.debug("Going to write %d bytes more", buf[preload_size:].nbytes)
        # TODO: can this handle faults? 
        buf[preload_size:].tofile(self._file)
        logging.debug("Going to flush")
        self._file.flush()
        
        # According to https://groups.google.com/forum/?fromgroups=#!topic/comedi_list/yr2U179x8VI
        # To finish a write fully, we need to do a cancel().
        # Wait until SDF_RUNNING is gone, then cancel() to reset SDF_BUSY
        expected = nscans * period
        left = start_time + expected - time.time()
        logging.debug("Waiting %g s for the write to finish", left)
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



    def write_read_data(self, wchannels, rchannels, period, data):
        """
        write data on the given analog output channels and read the same amount 
         synchronously on the given analog input channels
        wchannels (list of int): channels to write (in same the order as data)
        rchannels (list of int): channels to write (in same the order as data)
        period (float): sampling period in s (time between two writes on the same
         channel)
        data (numpy.ndarray of float): two dimension array to write (physical values)
          first dimension is along the time, second is along the channels
        return (numpy.array with shape=(data.shape[0], len(rchannels)) and dtype=float)
            the data read converted to physical value (volt)
        """
        #construct a comedi command
        
        nscans = data.shape[0]
        nwchans = data.shape[1]
        nrchans = len(rchannels)
        assert len(wchannels) == nwchans
        
        # create a command for writing
        wranges = []
        clist = comedi.chanlist(nwchans)
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
            clist[i] = comedi.cr_pack(channel, best_range, comedi.AREF_GROUND)
        
        logging.debug("Generating a new write command for %d scans", nscans)
        period_ns = int(round(period * 1e9))  # in nanoseconds
        wcmd = comedi.cmd_struct()
        comedi.get_cmd_generic_timed(self._device, self._ao_subdevice,
                                                  wcmd, nwchans, period_ns)
        
        wcmd.chanlist = clist
        wcmd.start_src = comedi.TRIG_INT
        wcmd.start_arg = 0
        wcmd.stop_src = comedi.TRIG_COUNT
        wcmd.stop_arg = nscans
        
        # clean up the command
        rc = comedi.command_test(self._device, wcmd)
        if rc != 0:
            # on the second time, it should report 0, meaning "perfect"
            rc = comedi.command_test(self._device, wcmd)
            if rc != 0:
                raise IOError("failed to prepare command")



        # create a command for reading
        # TODO add parameter to select the range and ref for each rchannel
        rranges = []
        clist = comedi.chanlist(nrchans)
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
            clist[i] = comedi.cr_pack(channel, best_range, comedi.AREF_GROUND)

        logging.debug("Generating a new read command for %d scans", nscans)
        rcmd = comedi.cmd_struct()
        comedi.get_cmd_generic_timed(self._device, self._ai_subdevice,
                                                  rcmd, nrchans, period_ns)
        
        rcmd.chanlist = clist
        rcmd.start_src = comedi.TRIG_INT # start synchronously with the write
        rcmd.start_arg = 0
        rcmd.stop_src = comedi.TRIG_COUNT
        rcmd.stop_arg = nscans


        # readying the subdevice with the command (needs to be done before
        # writing anything to the device
        comedi.command(self._device, wcmd)
        comedi.command(self._device, rcmd)
        

        # TODO do this outside of the method (=> data should already be raw)
        # convert physical values to raw data
        # Note: on the NI 6251, as probably many other devices, conversion is linear.
        # So it might be much more efficient to generate raw data directly
        wdtype = self._get_dtype(self._ao_subdevice)
        # forcing the order is not necessary but just to ensure good performance
        wbuf = numpy.empty(shape=data.shape, dtype=wdtype, order='C')
        converters = []
        for i, c in enumerate(wchannels):
            converters.append(self._get_converter(self._ao_subdevice,
                                          c, wranges[i], comedi.FROM_PHYSICAL))
        # TODO: check if it's possible to avoid multiple type conversion in the call
        for i, v in numpy.ndenumerate(data):
            wbuf[i] = converters[i[1]](v)
        # flatten the array
        wbuf = numpy.reshape(wbuf, nscans * nwchans, order='C')
        
        logging.debug("Converted physical value to raw data: %s", wbuf)
        
        # prepare read buffer info        
        rshape = (nscans, nrchans)
        rdtype = self._get_dtype(self._ai_subdevice)
        
        # preload the buffer with enough data first
        dev_buf_size = comedi.get_buffer_size(self._device, self._ao_subdevice)
        preload_size = dev_buf_size / wbuf.itemsize
        logging.debug("Going to preload %d bytes", wbuf[:preload_size].nbytes)
        wbuf[:preload_size].tofile(self._file)
        self._file.flush()

        # start reader thread
        self._rbuf = None
        rthread = threading.Thread(target=self.read_from_device, 
                                   args=(rdtype, rshape[0] * rshape[1]))
        rthread.start()
        
        # run the commands
        logging.debug("Going to start the command")
        
        start_time = time.time()
        comedi.internal_trigger(self._device, self._ao_subdevice, 0)
        
        logging.debug("Going to write %d bytes more", wbuf[preload_size:].nbytes)
        # TODO: can this handle faults? 
        wbuf[preload_size:].tofile(self._file)
        logging.debug("Going to flush")
        self._file.flush()
        
        
        # According to https://groups.google.com/forum/?fromgroups=#!topic/comedi_list/yr2U179x8VI
        # To finish a write fully, we need to do a cancel().
        # Wait until SDF_RUNNING is gone, then cancel() to reset SDF_BUSY
        expected = nscans * period
        left = start_time + expected - time.time()
        logging.debug("Waiting %g s for the write to finish", left)
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

        rthread.join(1) # very short timeout as it should finish at the same time as the output
        if rthread.isAlive():
            comedi.cancel(self._device, self._ai_subdevice)
        
        # the result should be in self._rbuf
        if self._rbuf is None or self._rbuf.size != (rshape[0] * rshape[1]):
            raise IOError("Failed to read all the values from the %d expected", rshape[0] * rshape[1])
        self._rbuf.shape = rshape # FIXME: check that the order/stride is correct
        
        logging.debug("Converting raw data to physical: %s", self._rbuf)
        
        # convert data to physical values
        # TODO convert the data while reading, to save time
        # TODO we probably prefer to have each channel as a separate array
        parray = numpy.empty(shape=rshape, dtype=numpy.double)
        converters = []
        for i, c in enumerate(rchannels):
            converters.append(self._get_converter(self._ai_subdevice,
                                          c, rranges[i], comedi.TO_PHYSICAL))

        # converter needs lsampl (uint32). So for lsampl devices, it's pretty
        # straightforward, just a matter of convincing SWIG that a numpy.uint32
        # is a unsigned int. For sampl devices, everything need to be converted.
        if rdtype.itemsize == 4:
            logging.debug("Using casting to access the raw data")
            # can just force-cast to a ctype buffer of unsigned int (that swig accepts)
            cbuf = numpy.ctypeslib.as_ctypes(self._rbuf)
            # TODO: maybe could be speed-up by something like vectorize()/map()?
            for i in range(self._rbuf.shape[0]):
                for j in range(self._rbuf.shape[1]):
                    parray[i,j] = converters[j](cbuf[i][j])
        else:
            # Needs real conversion
            logging.debug("Using full conversion to provide the raw data")
            for i, v in numpy.ndenumerate(self._rbuf):
                parray[i] = converters[i[1]](int(v))
        
        return parray


    def read_from_device(self, dtype, count):
        """To be called in a separate thread"""
        nbytes = dtype.itemsize * count
        logging.debug("Going to read %d bytes", nbytes)
        self._rbuf = numpy.fromfile(self._file, dtype=dtype, count=count)
        
        
    @staticmethod
    def _generate_scan_array(shape, limits, margin=0):
        """
        Generate an array of the values to send to scan a 2D area, using linear
        interpolation between the limits.
        shape (list of 2 int): X/Y resolution of the scanning area
        limits (ndarray of 2*2 int/float): lower/upper physical bounds of the area
            first dim is the X (0)/Y(1), second dim is min(0)/max(1)
            ex: limits[0,1] is the max value on the X dimension
        margin (0<=int): number of additional pixels to add at the begginning of
            each scanned line
        returns (2D ndarray of (shape[0] x (shape[1] + margin)) x 2 of int/float): the X/Y
            values for each points of the array, with Y scanned fast, and X 
            slowly. The type is the same as the limits.
        """
        # prepare an array of the right type
        dtype = limits.dtype
        full_shape = (shape[0], shape[1] + margin, 2)
        scan = numpy.empty(full_shape, dtype=dtype, order='C')
        
        # TODO see if meshgrid is faster (it needs to be in C order!) 
        
        # fill the X dimension
        scanx = scan[:,:,0].swapaxes(0,1) # just a view to have X as last dim
        scanx[:,:] = numpy.linspace(limits[0,0], limits[0,1], shape[0])
        # fill the Y dimension
        scan[:,margin:,1] = numpy.linspace(limits[1,0], limits[1,1], shape[1])
        
        # fill the margin with the first pixel
        if margin:
            fp = scan[:,margin,1:numpy.newaxis] # first pixel + add dimension
            scan[:,:margin,1] = fp.take([0] * margin, axis=1) # a view of "margin" times 
        
        # reshape the array to a full flat scan values (the C order should make
        # sure that the array is fully continuous
        scan.shape = [full_shape[0] * full_shape[1], 2]
        return scan
    
    
    
    
    def terminate(self):
        """
        Must be called at the end of the usage. Can be called multiple times,
        but the component shouldn't be used afterward.
        """
        if self._calibration:
            comedi.cleanup_calibration(self._calibration)
            self._calibration = None
        if self._device:
            comedi.close(self._device)
            self._device = None
            
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
                
                # TODO if not enough channels, should try to look for more subdevices
                
                name = "SEM/" + comedi.get_board_name(device)
                kwargs = {"device": n}
                found.append((name, kwargs))
                
            finally:
                comedi.close(device)
        
        return found


# For testing
#from odemis.driver.semcomedi import SEMComedi
#import numpy
#import logging
#import comedi
#logging.getLogger().setLevel(logging.DEBUG)
#comedi.comedi_loglevel(3)
#d = SEMComedi("a", "", None, "/dev/comedi0")
#r = d.get_data([0], 0.01, 3)
#w = numpy.array([[1],[2],[3],[4]], dtype=float)
#d.write_data([0], 0.01, w)
#limits = numpy.array([[-5, 5], [-7, 7]], dtype=float)
#s = SEMComedi._generate_scan_array([300, 300], limits)
#d.write_data([0, 1], 100e-6, s)

