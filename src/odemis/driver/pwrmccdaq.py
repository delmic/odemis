import logging
import time
from threading import Thread
from typing import List, Tuple, Dict

import odemis
from odemis import model
from odemis.driver._mccdaq import usb_1208LS
from odemis.model import HwError, Emitter, HwComponent

MAX_VOLTAGE_VALUE = 0x3ff  # for 1208LS
MAX_VOLTAGE = 5.0  # V
INTERLOCK_POLL_INTERVAL = 0.1


class MCCDevice(HwComponent):
    """
    Basic version of the Class for MCC DAQ device functionality with support for digital input status.
    This class can be inherited for more functionality to support more complex components.
    """
    def __init__(self, name: str, role: str, mcc_device: str,
                 di_channels: Dict[int, Tuple[str, bool]] = None, **kwargs):
        """
        :param mcc_device (str or None): serial number or None (null in yaml file) for auto-detect.
            the device is considered a USB-powered MCC DAQ 1208LS device.
        :param di_channels (dict -> int, list(str, bool)):
            the DIO channel used for TTL signal status change through the MCC device.
            for example di_channels: {2: ["interlockTriggered", False], 7: ["leftSwitch", True]}.
            key is the channel number(int), value is a list with VA name(str) and tll high is true(bool),
                this means that the VA will be considered True or False when the TTL signal is high.
            with this approach it is actually possible to keep track of customized status changes.
        """
        super().__init__(name, role, **kwargs)
        # force add a trailing 0 or just add a comment to config file?
        # if device and len(device) < 8:
        #     device = "0" + device
        self._name = name
        self._di_channels = di_channels if di_channels else {}
        self.device = None
        self._status_thread = None
        self._channel_vas = {}

        if mcc_device == "fake":
            self.device = MCCDeviceSimulator()
        else:
            try:
                self.device = usb_1208LS(mcc_device)
            except HwError:
                raise HwError("Failed to open MCC DAQ device with s/n '%s'" % mcc_device)

        for channel, (va_name, ttl_high) in self._di_channels.items():
            # create a VA with False as default
            va = model.BooleanVA(False, readonly=True)
            # append a new dict with the added VA
            self._channel_vas[channel] = va_name, ttl_high, va
            setattr(self, va_name, va)  # set the class VA variable name
            logging.info(f"{va_name} status activated for component {self._name} on channel {channel}")

        # create the thread to poll all the status bits
        self._status_thread = MCCDeviceDIStatus(self.device, self._channel_vas)
        self._status_thread.start()

    @classmethod
    def channel_to_port(cls, channel: int):
        """
        This is a support method to return the port and bit of a selected channel
        which is mainly used for the DBitIn and DBitOut commands of the MCC device
        :param channel (int): the channel number from the config file (0-15)
        :return (tuple(int, int)): the port of the device and the individual bit
        """
        if channel < 0 or channel > 15:
            raise ValueError("DIO channel value has to be between 0 and 15")

        # convert the channel number to port and bit
        if channel in range(0, 8):
            # channel 0 - 7 in config file
            port = usb_1208LS.DIO_PORTA
            bit = channel
        else:
            port = usb_1208LS.DIO_PORTB
            # channel 8 - 15 in config file
            bit = channel - 8

        return port, bit

    def terminate(self):
        # release the running status poll thread
        self._status_thread.terminated = True
        # wait for the tread to be really suspended
        self._status_thread.join()
        super().terminate()


class MCCDeviceDIStatus(Thread):
    """
    This thread will be tracking the change in status bits of the given channel/port of the MCC device.
    Polling is done at a fixed interval and a total of 8 bits can be read out or written to at the same time.
    If the component which instantiated this class is terminated, this thread is suspended first.
    """
    def __init__(self, mcc_device, channel_vas):
        super().__init__()
        self._channel_vas = channel_vas  # dict (channel: int -> list(va_name: str, ttl_high: bool, va: BooleanVA))
        self._device = mcc_device
        self.terminated = False

    def run(self):
        # retrieve only the port, the individual bits are not used separately
        port, _ = MCCDevice.channel_to_port(list(self._channel_vas.keys())[0])
        # set the start bit status value of the specified port
        current_bit_status = self._device.DIn(port)

        while not self.terminated:
            # wait for a fixed interval
            time.sleep(INTERLOCK_POLL_INTERVAL)

            # bit status = 0 -> TTL LOW (0.0V) | bit status = 1 -> TTL HIGH (3.3-5.0V)
            new_bit_status = self._device.DIn(port)

            if current_bit_status != new_bit_status:
                # convert the bit status value to a binary string
                bin_str_cur, bin_str_new = format(current_bit_status, "#010b"), format(new_bit_status, "#010b")
                bin_str_cur, bin_str_new = bin_str_cur.replace("0b", ""), bin_str_new.replace("0b", "")

                for num, (old_bit, new_bit) in enumerate(zip(bin_str_cur[::-1], bin_str_new[::-1])):
                    # only check the changed DI bit values in reverse order
                    if int(new_bit) != int(old_bit):
                        if num in self._channel_vas.keys():
                            # change the status VA according to TTL readout config e.g. high is true or false
                            bool_value = True if self._channel_vas[num][1] == bool(int(old_bit)) else False
                            self._channel_vas[num][2].value._set_value(bool_value, force_write=True)

                # set the new bit status value
                current_bit_status = new_bit_status


class MCCDeviceLight(Emitter, MCCDevice):
    """
    Class to support laser emitter control of one or more lasers through the use of a MCCDevice.
    Inherits from Emitter for ComponentProxy support and from MCCDevice for power and interlock control.
    """
    def __init__(self, name: str, role: str, mcc_device: str, ao_channels: List[int], do_channels: List[int],
                 spectra, pwr_curve, di_channels: Dict[int, Tuple[str, bool]] = None, **kwargs):
        """
        :param mcc_device (str or None): refer to parent.
        :param ao_channels: (list of (0<=int<=3)):
            The analogue output channel for each source, as numbered in the mccdaq device.
        :param do_channels: (list of (0<=int<=15)):
            The digital output (0 or 5 v) channel for each source, as numbered in the mccdaq device.
        :param spectra (list of 5-tuple of float): the spectra for each output channel used.
            Each tuple represents the wavelength in m for the 99% low, 25% low,
            centre/max, 25% high, 99% high. They do not have to be extremely precise.
            The most important is the centre, and that they are all increasing values.
        :param pwr_curve (list of dict (float -> 0<float)): Power curve segment map for each source.
            A segment map is a  series of voltage output on the analog output -> emission power of the light (W).
            It represents a series of linear segments to map the voltage output to the light emission.
            At least one pair should be provided. If no voltage is linked to 0W, then a 0V -> 0W mapping is used.
            The total curve should be monotonic.
        :param di_channels (dict -> int, list(str, bool)): refer to parent.
        """
        Emitter.__init__(self, name, role, **kwargs)
        MCCDevice.__init__(self, name, role, mcc_device, di_channels)

        self._shape = ()
        self._name = name

        # check for the right amount of info from the config file
        if len(ao_channels) != len(spectra):
            raise ValueError("spectra argument should have the same length as ao_channels (%d)" % len(ao_channels))
        if len(ao_channels) != len(pwr_curve):
            raise ValueError("pwr_curve argument should have the same length as ao_channels (%d)" % len(ao_channels))
        if len(ao_channels) != len(do_channels):
            raise ValueError("do_channels argument should have the same length as ao_channels (%d)" % len(ao_channels))

        self._ao_channels = ao_channels
        self._do_channels = do_channels

        # Check and append the power curves to the list
        self._pwr_curve = []
        for c, crv in zip(ao_channels, pwr_curve):
            crv = [v for v in crv.items()]
            # Add 0W = 0V if nothing = 0W
            if 0 not in [w for v, w in crv]:
                crv.append((0, 0))
                logging.info("Adding 0V -> 0W mapping to pwr_curve for channel %d", c)
            # At least beginning and end values
            if len(crv) < 2:
                raise ValueError("pwr_curve for channel %d has less than 2 values: %s" % (c, crv))
            # Check it's monotonic
            crv = sorted(crv, key=lambda v: v[0])
            if crv[0][1] < 0:
                raise ValueError("pwr_curve for channel %d has negative power: %g W" % (c, crv[0][1]))
            if crv[0][1] > 5:
                raise ValueError("pwr_curve for channel %d has more than 5V power: %g W" % (c, crv[0][1]))
            if len(crv) != len(set(v for v, w in crv)):
                raise ValueError("pwr_curve for channel %d has identical voltages: %s" % (c, crv))
            if not all((crv[i][1] < crv[i + 1][1]) for i in range(len(crv) - 1)):
                raise ValueError("pwr_curve for channel %d is not monotonic: %s" % (c, crv))

            self._pwr_curve.append(crv)

        # Check and append the spectra to the list of the 5 wavelength points
        spect = []
        for c, wls in zip(ao_channels, spectra):
            if len(wls) != 5:
                raise ValueError("Spectra for ao_channel %d doesn't have exactly 5 wavelength points: %s" % (c, wls))
            if list(wls) != sorted(wls):
                raise ValueError("Spectra for ao_channel %d has unsorted wavelengths: %s" % (c, wls))
            for wl in wls:
                if not 0 < wl < 100e-6:
                    raise ValueError("Spectra for ao_channel %d has unexpected wavelength = %f nm"
                                     % (c, wl * 1e9))
            spect.append(tuple(wls))

        # Maximum power for channel to be used as a range for power
        max_power = tuple([crv[-1][1] for crv in self._pwr_curve])
        # Power value for each channel of the device
        self.power = model.ListContinuous(value=[0.] * len(ao_channels),
                                          range=(tuple([0.] * len(ao_channels)), max_power,),
                                          unit="W", cls=(int, float), )
        self.power.subscribe(self._update_power)

        # info on which channel is which wavelength
        self.spectra = model.ListVA(spect, unit="m", readonly=True)

        # make sure everything is off (turning on the HUB will turn on the lights)
        self.power.value = self.power.range[0]

        self._metadata = {model.MD_HW_NAME: f"{self.device.getManufacturer()} "
                                            f"{self.device.getProduct()} "
                                            f"{self.device.getSerialNumber()}"}
        self._swVersion = odemis.__version__
        self._metadata[model.MD_SW_VERSION] = self._swVersion
        self._metadata[model.MD_HW_VERSION] = self._hwVersion

    def _power_to_volt(self, power: float, curve: List[Tuple[float, float]]) -> float:
        """
        Calculate the power to the right voltage using the specified power curve
        :param power (0 < float): the requested power of the light
        :param curve (list of tuple (float, float)): the mapping between volt -> power
        :return (float): voltage for outputting the given power
        raise (ValueError): if the requested power value is out of the power curve range limits
        """
        if power < curve[0][1]:
            raise ValueError("Power requested %g < %g" % (power, curve[0][1]))

        # Find the segment that correspond to that power
        for i, (v, w) in enumerate(curve[1:]):
            if power <= w:
                seg = i
                break
        else:
            raise ValueError("Power requested %g > %g" % (power, curve[-1][1]))

        logging.debug("Converting %g W using segment %d: %s -> %s",
                      power, seg, curve[seg], curve[seg + 1])

        basev, basew = curve[seg]
        endv, endw = curve[seg + 1]

        ratio = (power - basew) / (endw - basew)
        v = basev + ratio * (endv - basev)
        return v

    def _update_power(self, value: List[float]):
        for ao_ch, do_ch, crv, pwr in zip(self._ao_channels, self._do_channels, self._pwr_curve, value):
            pwr = min(pwr, crv[-1][1])
            volt = self._power_to_volt(pwr, crv)
            data = int((volt / MAX_VOLTAGE) * MAX_VOLTAGE_VALUE)  # data input expects an uint16
            # update the analogue output value
            logging.debug(f"Setting ao_channel {ao_ch} to {volt} V = {pwr} W")
            self.device.AOut(ao_ch, data)

            port, bit = MCCDevice.channel_to_port(do_ch)
            old_bit_value = self.device.DBitIn(port, bit)
            new_bit_value = int(pwr > 0.0)
            # update the digital output value by using a direct digital port bit
            if old_bit_value != new_bit_value:
                logging.debug(f"Setting do_channel {do_ch} from {old_bit_value} to {new_bit_value}")
                self.device.DBitOut(port, bit, new_bit_value)


class MCCDeviceSimulator(object):
    """
    A really basic and simple interface to simulate a USB-1208LS device with
    support for DIO pin read/write commands and a single AO write command.
    """
    def __init__(self):
        # initialize values
        self.productID = 0x0007a  # USB-1208LS
        # to keep track of the individual bits and values
        self.port_a_bit_config = {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "6": 0, "7": 0}
        self.port_b_bit_config = {"0": 1, "1": 1, "2": 1, "3": 1, "4": 1, "5": 1, "6": 1, "7": 1}
        self.AO_channels = {"0": 0, "1": 0}  # value (uint16) in counts to output [10-bits 0-5V]

        # set default configuration
        self.DConfig(usb_1208LS.DIO_PORTA, 0x00)  # Port A output
        self.DConfig(usb_1208LS.DIO_PORTB, 0x00)  # Port B input
        self.DOut(usb_1208LS.DIO_PORTA, 0x0)
        self.DOut(usb_1208LS.DIO_PORTB, 0x0)
        self.AOut(0, 0x0)
        self.AOut(1, 0x0)

    def getManufacturer(self):
        return "MCC"

    def getProduct(self):
        return "USB-1208LS"

    def getSerialNumber(self):
        return "021B0CB8"

    def DConfig(self, port_number, bit_mask):
        """
        This command sets the direction of the digital bits for a port.
        :param port_number: AUXPORT = 0x10 | Port A = 0x01 | Port B = 0x04
        :param bit_mask (int:bit value): 0 = output | 1 = input
        """
        if self.productID == 0x0075 and port_number == usb_1208LS.DIO_AUXPORT:
            bit_mask = ((bit_mask ^ 0xff) & 0xff)

        self.DOut(port_number, bit_mask)

    def DIn(self, port_number):
        """
        :param port_number: AUXPORT = 0x10 | Port A = 0x01 | Port B = 0x04
        :return: the value seen at the port pins
        """
        ret_val = "0b"
        DIO_port = self._return_port_config(port_number)

        for bit in DIO_port.keys():
            ret_val += str(DIO_port[bit])

        return int(ret_val, 2)

    def DOut(self, port_number, value):
        """
        This command writes data to the DIO port bits that are configured as outputs.
        :param port_number: AUXPORT = 0x10 | Port A = 0x01 | Port B = 0x04
        :param value: value to write to the port (0-255)
        """
        if value < 0 or value > 255:
            raise ValueError("Value to set is not between 0 and 255")

        DIO_port = self._return_port_config(port_number)
        in_val = format(value, '#010b')
        in_val = in_val.replace("0b", "")

        for num, c in enumerate(in_val[::-1]):
            DIO_port[str(num)] = int(c)

    def DBitIn(self, port_number, bit):
        """
        This command reads an individual digital port bit.  It will return the
        value seen at the port pin, so may be used for an input or output bit.
        :param port_number: AUXPORT = 0x10 | Port A = 0x01 | Port B = 0x04
        :param bit: the bit to read (0-7)
        :return (int): value 0 or 1
        """
        if bit < 0 or bit > 7:
            raise ValueError("Bit value is not between 0 and 7")

        DIO_port = self._return_port_config(port_number)

        return DIO_port[str(bit)]

    def DBitOut(self, port_number, bit, value):
        """
        This command writes an individual digital port bit.
        :param port_number: AUXPORT = 0x10 | Port A = 0x01 | Port B = 0x04
        :param bit: the bit to read (0-7)
        :param value: the value to write to the bit (0 or 1)
        """
        if bit < 0 or bit > 7:
            raise ValueError("Bit value is not between 0 and 7")
        if value < 0 or value > 1:
            raise ValueError("Value to set should be either 0 or 1")

        DIO_port = self._return_port_config(port_number)
        DIO_port[str(bit)] = value

    def AOut(self, channel, value):
        """
        This command sets the voltage output of the specified analog output channel
        :param channel: selects output channel (0 or 1)
        :param value: value (uint16) in counts to output [10-bits 0-5V]
        """
        channel = 0 if channel > 1 or channel < 0 else None
        # force automatic clipping
        if (value > 0x3ff):
            value = 0x3ff
        if (value < 0):
            value = 0
        self.AO_channels[str(channel)] = value

    def _return_port_config(self, port_number):
        if port_number == usb_1208LS.DIO_PORTB:
            return self.port_b_bit_config
        else:
            # Port A as default
            return self.port_a_bit_config
