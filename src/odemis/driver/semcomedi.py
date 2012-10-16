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
import logging
import pycomedi.device

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
        
#        try:
#            self.device = pycomedi.device.Device(device)
#            self.device.open()
#        except pycomedi.PyComediError:
#            raise ValueError("Failed to open DAQ device '%s'", device)
#        
#        self._metadata = {model.MD_HW_NAME: self.getHwName()}
#        self._swVersion = "%s (driver %s)" % (__version__.version, self.getSwVersion()) 
#        self._metadata[model.MD_SW_VERSION] = self._swVersion
##        self._hwVersion = self.getHwVersion()
#        self._metadata[model.MD_HW_VERSION] = self._hwVersion
            
        
    
    # There are two temperature sensors:
    # * One on the board itself (TODO how to access it with Comedi?)
    # * One on the SCB-68. From the manual, the temperature sensor outputs
    #   10 mV/°C and has an accuracy of ±1 °C => T = 100 * Vt
    # sudo ./cmd -f /dev/comedi0 -s 0 -c 0 -a 2 -n 1 -N 1 -p

        
    def getSwVersion(self):
        """
        Returns (string): displayable string showing the driver version
        """
        driver = self.device.get_driver_name()
        dversion = '.'.join(str(x) for x in self.device.get_version())
        return "%s v%s" % driver, dversion
    
    def getHwName(self):
        """
        Returns (string): displayable string showing whatever can be found out 
          about the actual hardware.
        """
        return self.device.get_board_name()

    def getTemperatureSCB(self):
        """
        returns (-300<float<300): temperature in °C reported by the Shielded
          Connector Block (which must be set to temperature sensor differential)
        """
        # On the SCB-68. From the manual, the temperature sensor outputs on 
        # AI0+/AI0- 10 mV/°C and has an accuracy of ±1 °C => T = 100 * Vt
        
        device = comedi.comedi_open(self._device_name)
        ai_subdevice = comedi.comedi_find_subdevice_by_type(device,
                                        comedi.COMEDI_SUBD_AI, 0)
        channel = 0
        
        # Get AI0 in differential
        range = comedi.comedi_find_range(device, ai_subdevice, channel,
                                        comedi.UNIT_volt, 0, 10)
        if range == -1:
            logging.warning("Couldn't find a fitting range")
            range = 0
            
        result, data = comedi.comedi_data_read(device, ai_subdevice, channel, range,
                                    comedi.AREF_DIFF)
        if result == -1:
            logging.error("Failed to read temperature")
            raise IOError("Failed to read data")
        
        # convert to volt
        maxdata = comedi.comedi_get_maxdata(device, ai_subdevice, channel)
        range_info = comedi.comedi_get_range(device, ai_subdevice, channel, range)
        pvalue = comedi.comedi_to_phys(data, range_info, maxdata)
        
        temp = pvalue * 100.0
        
        # convert using calibration
        # This will only work if the device is soft_calibrated, and calibration has been done
        path = comedi.comedi_get_default_calibration_path(device)
        if path is None:
            logging.error("Failed to read calibration information")
            return
        
        calibration = comedi.comedi_parse_calibration_file(path)
        if calibration is None:
            logging.error("Failed to read calibration information")
            return

        poly = comedi.comedi_polynomial_t()
        result = comedi.comedi_get_softcal_converter(
            ai_subdevice, channel,
            range,
            comedi.COMEDI_TO_PHYSICAL,
            calibration,
            poly)
        if result == -1:
            logging.error("Failed to read calibration information")
            return
        pvalue_cal = comedi.comedi_to_physical(data, poly)

        temp_cal = pvalue_cal * 100.0
        
        return temp, temp_cal
        
        
#        # Get AI0 in differential
#        ai_subdevice = self.device.find_subdevice_by_type(
#                                        pycomedi.constant.SUBDEVICE_TYPE.ai)
#        channel_temp = ai_subdevice.channel(
#            index=0, factory=pycomedi.channel.AnalogChannel, range=range, 
#            aref=pycomedi.constant.AREF.diff)
#        
#        # the device is soft calibrated, and pycomedi doesn't support converters
#        # for soft calibrated devices nor the simple to_phys version.
#        
#        data = c.data_read()
#        converter = c.get_converter()
#        physical_data = converter.to_physical(data)
