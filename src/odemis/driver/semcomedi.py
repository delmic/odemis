# -*- coding: utf-8 -*-
'''
Created on 15 Oct 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Open Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from odemis import model, __version__
import comedi
import glob
import logging

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
# comedi_, and not object oriented. You can directly the C documentation. The
# only thing to know is that parameters which are a simple type and used as output
# (e.g., int *, double *) are not passed as parameters but directly returned as
# output. However structures must be first allocated and then given as input
# parameter.
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
        
        self._device = comedi.comedi_open(self._device_name)
        if self._device is None:
            raise ValueError("Failed to open DAQ device '%s'", device)
        
        self._ai_subdevice = comedi.comedi_find_subdevice_by_type(self._device,
                                            comedi.COMEDI_SUBD_AI, 0)
        if self._ai_subdevice < 0:
            raise ValueError("Failed to open AI subdevice")
        
        self._metadata = {model.MD_HW_NAME: self.getHwName()}
        self._swVersion = "%s (driver %s)" % (__version__.version, self.getSwVersion()) 
        self._metadata[model.MD_SW_VERSION] = self._swVersion
#        self._hwVersion = self.getHwVersion()
        self._metadata[model.MD_HW_VERSION] = self._hwVersion
        
        # detect when values are strange
        comedi.comedi_set_global_oor_behavior(comedi.COMEDI_OOR_NAN)
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
        
        # This will only work if the device is soft_calibrated, and calibration has been done
        path = comedi.comedi_get_default_calibration_path(self._device)
        if path is None:
            logging.warning("Failed to read calibration information")
            return
        
        self._calibration = comedi.comedi_parse_calibration_file(path)
        if self._calibration is None:
            # TODO: only do a warning if the device has any soft-cal subdevice
            logging.warning("Failed to read calibration information, you might " 
                            "want to calibrate your device with:\n"
                            "sudo comedi_soft_calibrate -f /dev/comedi0\n"
                            "or\n"
                            "sudo comedi_calibrate -f /dev/comedi0")
            return
        
        # TODO: do the hardware calibrated devices need to have the file loaded?
        # see comedi_apply_calibration() => probably not, but need to call this
        # function when we read from different channel.
        
    def getSwVersion(self):
        """
        Returns (string): displayable string showing the driver version
        """
        driver = comedi.comedi_get_driver_name(self._device)
        version = comedi.comedi_get_version_code(self._device)
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
        return comedi.comedi_get_board_name(self._device)


    def _get_converter(self, subdevice, channel, range, direction):
        """
        Finds the best converter available for the given conditions
        subdevice (int): the subdevice index
        channel (int): the channel index
        range (int): the range index
        direction (enum): comedi.COMEDI_TO_PHYSICAL or comedi.COMEDI_FROM_PHYSICAL
        return a callable number -> number
        """
        assert(direction in [comedi.COMEDI_TO_PHYSICAL, comedi.COMEDI_FROM_PHYSICAL])
        
        # 3 possibilities:
        # * the device is hard-calibrated -> simple converter from get_hardcal_converter
        # * the device is soft-calibrated -> polynomial converter from  get_softcal_converter
        # * the device is not calibrated -> linear approximation converter
        poly = None
        flags = comedi.comedi_get_subdevice_flags(self._device, subdevice)
        if not flags & comedi.SDF_SOFT_CALIBRATED:
            # hardware-calibrated
            poly = comedi.comedi_polynomial_t()
            result = comedi.comedi_get_hardcal_converter(self._device,
                              subdevice, channel, range, direction, poly)
            if result < 0:
                logging.warning("Failed to get converter from calibration")
                poly = None
        elif self._calibration:
            # soft-calibrated
            poly = comedi.comedi_polynomial_t()
            result = comedi.comedi_get_softcal_converter(subdevice, channel,
                              range, direction, self._calibration, poly)
            if result < 0:
                logging.warning("Failed to get converter from calibration")
                poly = None
        
        if poly is None:
            # not calibrated
            logging.debug("creating a non calibrated converter for s%dc%dr%d",
                          subdevice, channel, range)
            maxdata = comedi.comedi_get_maxdata(self._device, subdevice, channel)
            range_info = comedi.comedi_get_range(self._device, subdevice, 
                                                 channel, range)
            if direction == comedi.COMEDI_TO_PHYSICAL:
                return lambda d: comedi.comedi_to_phys(d, range_info, maxdata)
            else:
                return lambda d: comedi.comedi_from_phys(d, range_info, maxdata)
        else:
            # calibrated: return polynomial-based converter
            logging.debug("creating a calibrated converter for s%dc%dr%d",
                          subdevice, channel, range)
            if direction == comedi.COMEDI_TO_PHYSICAL:
                return lambda d: comedi.comedi_to_physical(d, poly)
            else:
                return lambda d: comedi.comedi_from_physical(d, poly)
    

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
            converter = self._get_converter(subdevice, channel, range, comedi.COMEDI_TO_PHYSICAL)
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
            converter = self._get_converter(subdevice, channel, range, comedi.COMEDI_FROM_PHYSICAL)
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
        range = comedi.comedi_find_range(self._device, self._ai_subdevice, channel,
                                        comedi.UNIT_volt, 0, 1)
        if range < 0:
            logging.warning("Couldn't find a fitting range, using a random one")
            range = 0
        
        range_info = comedi.comedi_get_range(self._device, self._ai_subdevice, channel, range)
        logging.debug("Reading temperature with range %g->%g V", range_info.min, range_info.max)

        
        # read the raw value
        result, data = comedi.comedi_data_read(self._device, self._ai_subdevice,
                            channel, range, comedi.AREF_DIFF)
        if result < 0:
            logging.error("Failed to read temperature")
            raise IOError("Failed to read data")
        
        # convert using calibration
        pvalue = self._to_phys(self._ai_subdevice, channel, range, data)
        temp = pvalue * 100.0
        return temp
    
    
    def terminate(self):
        """
        Must be called at the end of the usage. Can be called multiple times,
        but the component shouldn't be used afterward.
        """
        if self._calibration:
            comedi.comedi_cleanup_calibration(self._calibration)
            self._calibration = None
        if self._device:
            comedi.comedi_close(self._device)
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
            device = comedi.comedi_open(n)
            if device is None:
                continue
            try:
                logging.debug("Checking comedi device '%s'", n)
                
                # Should have at least one analog input and an analog output with 2 channels
                ai_subdevice = comedi.comedi_find_subdevice_by_type(device,
                                                comedi.COMEDI_SUBD_AI, 0)
                if ai_subdevice < 0:
                    continue
                number_ai = comedi.comedi_get_n_channels(device, ai_subdevice)
                if number_ai < 1:
                    continue
                ao_subdevice = comedi.comedi_find_subdevice_by_type(device,
                                                comedi.COMEDI_SUBD_AO, 0)
                if ao_subdevice < 0:
                    continue
                number_ao = comedi.comedi_get_n_channels(device, ao_subdevice)
                if number_ao < 2:
                    continue
                
                # TODO if not enough channels, should try to look for more subdevices
                
                name = "SEM/" + comedi.comedi_get_board_name(device)
                kwargs = {"device": n}
                found.append((name, kwargs))
                
            finally:
                comedi.comedi_close(device)
        
        return found
    
