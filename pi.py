'''
Created on 22 Feb 2012

@author: piel
'''

# Status:
# byte 1
STATUS_ECHO_ON          = 0x000001 #Bit 0: Echo ON
#Bit 1: Wait in progress
#Bit 2: Command error
#Bit 3: Leading zero suppression active
#Bit 4: Macro command called
#Bit 5: Leading zero suppression disabled
#Bit 6: Number mode in effect
STATUS_BOARD_ADDRESSED  = 0x000080 #Bit 7: Board addressed
# byte 2
#Bit 0: Joystick X enabled
#Bit 1: Joystick Y enabled
#Bit 2: Pulse output on channel 1 (X)
#Bit 3: Pulse output on channel 2 (Y)
#Bit 4: Pulse delay in progress (X)
#Bit 5: Pulse delay in progress (Y)
STATUS_MOVING_X         = 0x004000 #Bit 6: Is moving (X)
STATUS_MOVING_Y         = 0x008000 #Bit 7: Is moving (Y)
# byte 3
#Bit 0: Limit Switch ON
#Bit 1: Limit switch active state HIGH
#Bit 2: Find edge operation in progress
#Bit 3: Brake ON
#Bit 4: n.a.
#Bit 5: n.a.
#Bit 6: n.a.
#Bit 7: n.a.
# byte 4
#Bit 0: n.a.
#Bit 1: Reference signal input
#Bit 2: Positive limit signal input
#Bit 3: Negative limit signal input
#Bit 4: n.a.
#Bit 5: n.a.
#Bit 6: n.a.
#Bit 7: n.a.
# byte 5 (Error codes)
ERROR_NO = 0x00 #00: no error
ERROR_COMMAND_NOT_FOUND = 0x01 #01: command not found
#02: First command character was not a letter
#05: Character following command was not a digit
#06: Value too large
#07: Value too small
#08: Continuation character was not a comma
#09: Command buffer overflow
#0A: macro storage overflow

class PIRedStone(object):
    '''
    This represents the bare PI C-170 piezo motor controller, with a
    information comes from C-170_User_MS133E104.pdf
    '''

    def __init__(self, serial, address=None):
        '''
        Initialise the given controller #id over the given serial port
        serial: a serial port
        address 0<int<15: the address of the controller as defined by its jumpers 1-4
        if no address is given, then no controller is selected
        '''
        self.serial = serial
        self.serial.timeout = 0.1 # s
        
        self.address = address
        # allow to not initialise the controller (mostly for ScanNetwork())
        if address is None:
            return

    def _SendSetCommand(self, com):
        """
        Send a command which does not expect any report back
        com (string): command to send (including the \r if necessary)
        """
        assert(len(com) < 10)
        self.serial.write(com)
        # TODO allow to check for error via TellStatus afterwards
        
    def _SendGetCommand(self, com, report_prefix=""):
        """
        Send a command and return its report
        com (string): the command to send
        report_prefix (string): the prefix to the report,
            it will be removed from the return value
        return (string): the report without prefix nor newline
        """
        assert(len(com) <= 10)
        assert(len(report_prefix) <= 2)
        self.serial.write(com)
        report = self.serial.readline()
        if not report.startswith(report_prefix):
            raise IOError("Report prefix unexpected after '%s': '%s'." % (com, report))

        return report.strip(report_prefix + "\r\n")
    
    # Low-level functions
    def AddressSelection(self, address):
        """
        Send the address selection command over the bus to select the given controller
        address 0<int<15: the address of the controller as defined by its jumpers 1-4  
        """
        assert((0 <= address) and (address <= 15))
        self._SendSetCommand("\x01%X" % address) # XXX not sure if there is a \r?
        
    def SelectController(self, address):
        assert((0 <= address) and (address <= 15))
        self._SendSetCommand("SC%d\r" % address)
        
    def TellStatus(self):
        """
        Call the Tell Status command and return the answer.
        return (2-tuple (status: int, error: int): 
            * status is a flag based value (cf STATUS_*)
            * error is a number corresponding to the last error (cf ERROR_*)
        """ 
        bytes_str = self._SendGetCommand("TS\r", "S:")
        # expect report like "S:A1 00 FF 00 00\r\n"
        bytes_int = [int(b, 16) for b in bytes_str]
        st = bytes_int[0] + bytes_int[1] << 8 + bytes_int[2] << 16 + bytes_int[3] << 24
        err =  bytes_int[5]
        return (st, err) 

    def TellBoardAddress(self):
        """
        returns the device address as set by DIP switches at the
        Redstone's front panel.
        return (0<=int<=15): device address
        """
        address = self._SendGetCommand("TB\r", "B:")
        assert((0 <= address) and (address <= 15))
        return address

    def VersionReport(self):
        version = self._SendGetCommand("VE\r")
        # expects something like:
        #(C)2004 PI GmbH Karlsruhe, Ver. 2.20, 7 Oct, 2004 CR LF ETX 
        return version
            
    def Help(self):
        report = self._SendGetCommand("HE\r")
        return report
    
    def WaitStop(self, time = 1):
        """
        Force the controller to wait until a burst is done before reading the 
        next command.
        time (1 <= int <= 65537): additional time to wait after the burst (ms)
        """
        assert((1 <= time) and (time <= 65537))
        self._SendSetCommand("WS%d\r" % time)
    
    def AbortMotion(self):
        """
        Stops the running output pulse sequences started by GP or GN.
        """
        self._SendSetCommand("AB\r")

    def PulseOutput(self, axis, duration):
        """
        Outputs pulses of length duration on channel axis
        axis (int 1 or 2): the output channel
        duration (1<=int<=255): the duration of the pulse
        """
        assert((1 <= axis) and (axis <= 2))
        assert((1 <= duration) and (duration <= 255))
        self._SendSetCommand("%dCA%d" % (axis, duration))

    def SetDirection(self, axis, direction):
        """
        Applies a static direction flag (positive or negative) to the axis. 
        axis (int 1 or 2): the output channel
        direction (int 0 or 1): 0=low(positive) and 1=high(negative)
        """
        assert((1 <= axis) and (axis <= 2))
        assert((0 <= direction) and (direction <= 1))
        self._SendSetCommand("%dCD%d" % (axis, direction))
        
    def GoPositive(self, axis):
        """
        Used to execute a move in the positive direction as defined by
            the SS, SR and SW values.
        axis (int 1 or 2): the output channel
        """
        assert((1 <= axis) and (axis <= 2))
        self._SendSetCommand("%dGP" % axis)

    def GoNegative(self, axis):
        """
        Used to execute a move in the negative direction as defined by
            the SS, SR and SW values.
        axis (int 1 or 2): the output channel
        """
        assert((1 <= axis) and (axis <= 2))
        self._SendSetCommand("%dGN" % axis)

    def SetRepeatCounter(self, axis, repetitions):
        """
        Set the repeat counter for the given axis
        axis (int 1 or 2): the output channel
        repetitions (0<=int<=65535): the amount of repetitions
        """
        assert((1 <= axis) and (axis <= 2))
        assert((1 <= repetitions) and (repetitions <= 65535))
        self._SendSetCommand("%dSR%d" % (axis, repetitions))


    def SetStepSize(self, axis, duration):
        """
        Set the step size that corresponds with the length of the output
            pulse for the given axis
        axis (int 1 or 2): the output channel
        duration (0<=int<=255): the length of pulse in μs
        """
        assert((1 <= axis) and (axis <= 2))
        assert((1 <= duration) and (duration <= 255))
        self._SendSetCommand("%dSS%d" % (axis, duration))


    def SetWaitTime(self, axis, duration):
        """
        This command sets the delay time (wait) between the output of pulses when
            commanding a burst move for the given axis.
        axis (int 1 or 2): the output channel
        duration (0<=int<=65535): the wait time (ms), 1 gives the highest output frequency.
        """
        assert((1 <= axis) and (axis <= 2))
        assert((1 <= duration) and (duration <= 65535))
        self._SendSetCommand("%dSW%d" % (axis, duration))
    
    # High-level functions
    def Select(self, address):
        """
        select a given controller to manage
        address (0<int<15): the address of the controller as defined by its jumpers 1-4
        """
        assert((0 <= address) and (address <= 15))
        self.AddressSelection(address)
        reported_add = self.TellBoardAddress()
        if reported_add != address:
            raise IOError("Failed to select controller " + str(address))
        
        (status, error) = self.TellStatus()
        if error:
            raise IOError("Select Controller returned error " + str(error))
        if not (status | STATUS_BOARD_ADDRESSED):
            raise IOError("Failed to select controller " + str(address))
    
    def MoveRelativeSmall(self, axis, duration):
        """
        Move on a given axis for a given pulse length
        axis (int 1 or 2): the output channel
        duration (-255<=int<=255): the duration of pulse in μs,
                                   negative to go negative direction
        """
        assert((1 <= axis) and (axis <= 2))
        assert((-255 <= duration) and (duration <= 255))
        if duration == 0:
            return
        elif duration > 0:
            self.SetDirection(axis, 1)
        else:
            self.SetDirection(axis, 2)
            
        self.PulseOutput(axis, abs(duration))
        
    def MoveRelative(self, axis, duration):
        """
        Move on a given axis for a given pulse length
        axis (int 1 or 2): the output channel
        duration (int): the duration of pulse in μs 
        """
        assert((1 <= axis) and (axis <= 2))
        if duration == 0:
            return

        (steps, left) = divmod(duration, 255)
        
        # Run the main length
        self.SetWaitTime(axis, 1) # as fast as possible
        self.SetStepSize(axis, 255) # big steps
        self.SetRepeatCounter(axis, steps)
        if duration > 0:
            self.GoPositive(axis)
        else:
            self.GoNegative(axis)
            
        # Finish with the small left over
        self.MoveRelativeSmall(axis, left)
    
    def isMoving(self, axis=None):
        """
        Indicate whether the motors are moving. 
        axis (None, 1, or 2): axis to check whether it is moving, or both if None
        return (boolean): True if moving, False otherwise
        """
        (st, err) = self.TellStatus()
        if axis == 1:
            mask = STATUS_MOVING_X
        elif axis == 2:
            mask = STATUS_MOVING_Y
        else:
            mask = STATUS_MOVING_X | STATUS_MOVING_Y
        
        return bool(st | mask)
        
    def ScanNetwork(self, max_add = 15):
        """
        Scan the serial network for all the PI C-170 available.
        max_add (0<=int<=15): maximum address to scan
        return (set of (0<=int<=15)): set of addresses of available controllers
        Note: after the scan the selected device is unspecified
        """
        # TODO see MRC_initNetwork, which takes 400ms per address

        
        present = set([])
        for i in range(max_add):
            # ask for controller #i
            self.AddressSelection(i)

            # is it answering?
            try:
                add = self.TellBoardAddress()
                if add == i:
                    present.add(add)
                else:
                    print "Warning: asked for controller %d and was answered by controller %d." % (i, add)
            except IOError:
                pass
        
        return present
        
        
