# -*- coding: utf-8 -*-
'''
Created on 19 Sep 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Open Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from odemis import model, __version__
import glob
import logging
import os
import serial
import sys
import threading

class LLE(model.Emitter):
    '''
    Represent (interfaces) a Lumencor Light Engine (multi-channels light engine). It
    is connected via a serial port (physically over USB). It is written for the
    Spectra, but might be compatible with other hardware with less channels.
    Documentation: Spectra TTL IF Doc.pdf. Micromanager's driver "LumencorSpectra"
    might also be a source of documentation (BSD license).
    
    The API doesn't allow asynchronous actions. So the switch of source/intensities
    is considered instantaneous by the software. It obviously is not, but the 
    documentation states about 200 μs. As it's smaller than most camera frame
    rates, it shouldn't matter much. 
    '''


    def __init__(self, name, role, port, _noinit=False, **kwargs):
        """
        port (string): name of the serial port to connect to.
        _noinit (boolean): for internal use only, don't try to initialise the device 
        """
        # start with this opening the port: if it fails, we are done
        self._serial = self.openSerialPort(port)
        
        # to acquire before sending anything on the serial port
        self._ser_access = threading.Lock()
        
        if _noinit:
            return
        
        model.Emitter.__init__(self, name, role, **kwargs)
        
        # Test the LLE answers back
        current_temp = self.GetTemperature()
        
        # Init the LLE
        self._initDevice()
        
        self.shape = (1)
        self._max_power = 100
        self.power = model.FloatEnumerated(0, (0, self._max_power), unit="W")

        # emissions is list of 0 <= floats <= 1.
        self._intensities = [0.0] * 7 # start off
        self.emissions = model.ListVA(list(self._intensities), unit="", 
                                      setter=self._setEmissions)
        # FIXME: values are just from reading the documentation graph => better values?
        # list of 5-tuples of floats: filter-low, 25% low, max, 25% high, filter-high 
        self.spectra = model.ListVA([(615e-9, 625e-9, 635e-9, 640e-9, 650e-9), # Red
                                     (455e-9, 465e-9, 475e-9, 485e-9, 495e-9), # Cyan
                                     (525e-9, 540e-9, 550e-9, 555e-9, 560e-9), # Green
                                     (375e-9, 390e-9, 400e-9, 402e-9, 405e-9), # UV
                                     (595e-9, 580e-9, 565e-9, 560e-9, 555e-9), # Yellow
                                     (420e-9, 430e-9, 437e-9, 445e-9, 455e-9), # Blue
                                     (495e-9, 505e-9, 515e-9, 520e-9, 530e-9), # Teal
                                     ],
                                     unit="m", readonly=True) 
        
        self._prev_intensities = [None] * 7 # => will update for sure
        self._updateIntensities() # turn off every source
        
        self.power.subscribe(self._updatePower)
        # set HW and SW version
        self._swVersion = "%s (serial driver: %s)" % (__version__.version, self.getSerialDriver(port))
        self._hwVersion = "Lumencor Light Engine" # hardware doesn't report any version
        
        
        # Update temperature every 10s
        self.temperature = model.FloatVA(current_temp, unit="C", readonly=True)
        self.temp_timer = RepeatingTimer(10, self._updateTemperature,
                                         "LLE temperature update")
        self.temp_timer.start()
    
    
    def getMetadata(self):
        metadata = {}
        # MD_IN_WL expects just min/max => if multiple sources, we need to combine
        wl_range = (1, 0) # big min, small max
        power = 0
        for i in range(len(self._intensities)):
            if self._intensities[i] > 0:
                wl_range = (min(wl_range[0], self.spectra.value[i][0]),
                            min(wl_range[1], self.spectra.value[i][4]))
                # FIXME: not sure how to combine
                power = max(power, self._intensities[i])
        
        if wl_range == (1, 0):
            wl_range = (0, 0)
        metadata[model.MD_IN_WL] = (380e-9, 740e-9)
        metadata[model.MD_LIGHT_POWER] = power
        return metadata

    def _sendCommand(self, com):
        """
        Send a command which does not expect any report back
        com (string): command to send
        """
        assert(len(com) <= 10) # commands cannot be long
        logging.debug("Sending: %s", com.encode('string_escape'))
        self._serial.write(com)
        
    def _readResponse(self, length):
        """
        receive a response from the engine
        com (string): command to send (including the \n if necessary)
        length (0<int): length of the response to receive
        return (string of length == length): the response received (raw) 
        raises:
            IOError in case of timeout
        """
        response = ""
        while len(response) < length:
            char = self._serial.read()
            if not char:
                raise IOError("Device timeout after receiving %s.", 
                              response.encode('string_escape'))
            response += char
            
        logging.debug("Received: %s", response.encode('string_escape'))
        return response
        
    def _initDevice(self):
        """
        Initialise the device
        """
        with self._ser_access:
            # from the documentation:
            self._sendCommand("\x57\x02\xff\x50") # Set GPIO0-3 as open drain output
            self._sendCommand("\x57\x03\xab\x50") # Set GPI05-7 push-pull out, GPIO4 open drain out

    def _setDeviceManual(self):
        """
        Reset the device to the manual mode
        """
        with self._ser_access:
            # from the documentation:
            self._sendCommand("\x57\x02\x55\x50") # Set GPIO0-3 as input
            self._sendCommand("\x57\x03\x55\x50") # Set GPI04-7 as input

    # Sources number for the driver are:
    # 0: Red
    # 1: Green (see below)
    # 2: Cyan
    # 3: UV
    # 4: Yellow (see below)
    # 5: Blue
    # 6: Teal

    # Actual sources are more complicated:
    # 0, 2, 3, 5, 6 are as is. 1 is for Yellow/Green. Setting 4 selects 
    # whether yellow (activated) or green (deactivated) is used.
   
    def _enableSources(self, sources):
        """
        Select the light sources which must be enabled.
        Note: If yellow/green (1/4) are activated, no other channel will work. 
        Yellow has precedence over green.
        sources (set of 0<= int <= 6): source to be activated, the rest will be turned off
        """
        com = "\x4F\x00\x50" # the second byte will contain the sources to activate
        
        # Do we need to active Green filter?
        if (1 in sources or 4 in sources) and len(sources) > 1:
            logging.warning("Asked to activate multiple conflicting sources %r", sources)
            
        s_byte = 0x7f # reset a bit to 0 to activate
        for s in sources:
            assert(0 <= s and s <= 6)
            if s == 4: # for yellow, "green/yellow" (1) channel must be activated (0)
                s_byte &= ~ (1 << 1)
            s_byte &= ~ (1 << s) 
        
        com[1] = chr(s_byte)
        with self._ser_access:
            self._sendCommand(com)
    
    # map of source number to bit & address for source intensity setting
    source2BitAddr = { 0: (3, 0x18),
                       1: (2, 0x18),
                       2: (1, 0x18),
                       3: (0, 0x18),
                       4: (2, 0x18), # Yellow is the same source as Green
                       5: (1, 0x1A),
                       6: (0, 0x1A)
                      }
    def _setSourceIntensity(self, source, intensity):
        """
        Select the intensity of the given source (it needs to be activated separately).
        source (0 <= int <= 6): source number
        intensity (0<= int <= 255): intensity value 0=> off, 255 => fully bright
        """
        assert(0 <= source and source <= 6)
        bit, addr = self.source2BitAddr[source]
        
        com = "\x53\x18\x03\x0F\xFF\xF0\x50"
        #            ^^       ^   ^  ^ : modified bits
        #         address    bit intensity
        
        # address
        com[1] = chr(addr)
        # bit
        com[3] = chr(1 << bit)
        
        # intensity is inverted
        b_intensity = 0xfff0 & (((~intensity) << 4) | 0xf00f)
        com[4] = chr(b_intensity >> 8)
        com[5] = chr(b_intensity & 0xff)
        
        with self._ser_access:
            self._sendCommand(com)
    
    def GetTemperature(self):
        """
        returns (-300 < float < 300): temperature in degrees
        """
        # From the documentation:
        # The most significant 11 bits of the two bytes are used
        # with a resolution of 0.125 deg C.
        with self._ser_access:
            self._sendCommand("\x53\x91\x02\x50")
            resp = bytearray(self._readResponse(2))
        val = 0.125 * ((((resp[1] << 8) | resp[0]) >> 5) & 0x7ff)
        return val
    
    def _updateTemperature(self):
        temp = self.GetTemperature()
        self.temperature._value = temp
        self.temperature.notify(self.temperature.value)
        logging.debug("LLE temp is %g", temp)
    
    def _updateIntensities(self):
        """
        Update the sources setting of the hardware, if necessary
        """
        toTurnOn = set()
        need_update = False
        for i in range(7):
            if self._prev_intensities[i] != self._intensities[i]:
                need_update = True
                if self._intensities[i] > self._max_power/255.:
                    toTurnOn.add(i)  
                self._setSourceIntensity(i, self._intensities[i] * 255. / self._max_power)
        
        if need_update:
            self._enableSources(toTurnOn)
            
        self._prev_intensities = self._intensities
        
    def _updatePower(self, value):
        # set the actual values
        for i in range(7):
            self._intensities[i] = self.emissions.value[i] * value

        if value == 0:
            logging.debug("Light is off")
        else:
            logging.debug("Light is on")
    
    def _setEmissions(self, intensities):
        """
        intensities (list of 7 floats [0..1]): intensity of each source
        """ 
        # Green (1) and Yellow (4) can only be activated independently
        # => force it, with yellow taking precedence
        if intensities[4] > 0.0:
            yellow = intensities[4]
            intensities = [0.] * 7 # new object
            intensities[4] = yellow
        elif intensities[1] > 0.0:
            green = intensities[1]
            intensities = [0.] * 7 # new object
            intensities[1] = green
        
        # set the actual values
        for i in range(7):
            self._intensities[i] = intensities[i] * self.power.value
        self._updateIntensities()
        return intensities
        

    def terminate(self):
        if self.temp_timer is not None:
            self.temp_timer.cancel()
            self.temp_timer = None
        
        self._setDeviceManual()
        
    def __del__(self):
        self.terminate()
        
    def selfTest(self):
        """
        check as much as possible that it works without actually moving the motor
        return (boolean): False if it detects any problem
        """
        # only the temperature response something
        try:
            temp = self.GetTemperature()
            if -300 < temp and temp < 250:
                return True
        except:
            logging.exception("Selftest failed")
        
        return False

    @staticmethod
    def scan(port=None):
        """
        port (string): name of the serial port. If None, all the serial ports are tried
        returns (list of 2-tuple): name, args (port)
        Note: it's obviously not advised to call this function if a device is already under use
        """
        if port:
            ports = [port]
        else:
            if os.name == "nt":
                ports = ["COM" + str(n) for n in range (0,8)]
            else:
                ports = glob.glob('/dev/ttyS?*') + glob.glob('/dev/ttyUSB?*')
        
        logging.info("Serial ports scanning for Lumencor light engines in progress...")
        found = []  # (list of 2-tuple): name, args (port, axes(channel -> CL?)
        for p in ports:
            try:
                dev = LLE(None, None, p, _noinit=True)
            except serial.SerialException:
                # not possible to use this port? next one!
                continue

            # Try to connect and get back some answer.
            # The LLE only answers back for the temperature
            try:
                temp = dev.GetTemperature()
                if temp < 250: # avoid 255 => only 1111's, which is bad sign
                    found.append(("LLE", {"port": p}))
            except:
                continue

        return found
    
    @staticmethod
    def getSerialDriver(name):
        """
        return (string): the name of the serial driver used for the given port
        """
        # In linux, can be found as link of /sys/class/tty/tty*/device/driver
        if sys.platform.startswith('linux'):
            path = "/sys/class/tty/" + os.path.basename(name) + "/device/driver"
            try:
                return os.path.basename(os.readlink(path))
            except OSError:
                return "Unknown"
        else:
            return "Unknown"
        
    @staticmethod
    def openSerialPort(port):
        """
        Opens the given serial port the right way for the Spectra LLE.
        port (string): the name of the serial port (e.g., /dev/ttyUSB0)
        return (serial): the opened serial port
        """
        ser = serial.Serial(
            port = port,
            baudrate = 9600,
            bytesize = serial.EIGHTBITS,
            parity = serial.PARITY_NONE,
            stopbits = serial.STOPBITS_ONE,
            timeout = 0.3 #s
        )
        
        return ser 
    

# Copy from andorcam3
class RepeatingTimer(threading.Thread):
    """
    An almost endless timer thread. 
    It stops when calling cancel() or the callback disappears.
    """
    def __init__(self, period, callback, name="TimerThread"):
        """
        period (float): time in second between two calls
        callback (callable): function to call
        name (str): fancy name to give to the thread
        """
        threading.Thread.__init__(self, name=name)
        self.callback = model.WeakMethod(callback)
        self.period = period
        self.daemon = True
        self.must_stop = threading.Event()
    
    def run(self):
        # use the timeout as a timer
        while not self.must_stop.wait(self.period):
            try:
                self.callback()
            except model.WeakRefLostError:
                # it's gone, it's over
                return
        
    def cancel(self):
        self.must_stop.set()
