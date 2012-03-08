#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 6 Mar 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Delmic Acquisition Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''

from ctypes import *
from PIL import Image
import time
import numpy

class ATError(Exception):
    pass

class ATDLL(CDLL):
    """
    Subclass of CDLL specific to atcore library, which handles error codes for
    all the functions automatically.
    It works by setting a default _FuncPtr.errcheck.
    """

    @staticmethod
    def at_errcheck(result, func, args):
        """
        Analyse the return value of a call and raise an exception in case of 
        error.
        Follows the ctypes.errcheck callback convention
        """
        if result != 0:
            raise ATError("Call to %s failed with error code %d" %
                               (str(func.__name__), result))
        return result

    def __getitem__(self, name):
#        func = self._FuncPtr((name, self))
        func = CDLL.__getitem__(self, name)
#        if not isinstance(name, (int, long)):
        func.__name__ = name
        func.errcheck = self.at_errcheck
        return func

def scan():
    atcore = ATDLL("libatcore.so.3", RTLD_GLOBAL) # Global so that its sub-libraries can access it
    atcore.AT_InitialiseLibrary()
    dc = c_longlong()
    atcore.AT_GetInt(1, u"Device Count", byref(dc))
    print "found %d devices." % dc.value
    
    for i in range(dc.value):
        hndl = c_int()
        atcore.AT_Open(i, byref(hndl))
        model = create_unicode_buffer(128)
        atcore.AT_GetString(hndl, u"Camera Model", model, len(model))
        print i, model.value
        atcore.AT_Close(hndl)
        
    atcore.AT_FinaliseLibrary()

def acquire(device, size, exp, binning=1):
    atcore = ATDLL("libatcore.so.3", RTLD_GLOBAL) # Global so that its sub-libraries can access it
    atcore.AT_InitialiseLibrary()

    hndl = c_int()
    atcore.AT_Open(device, byref(hndl))
    
    
    # It affect max size, so before everything
    binning_str = u"%dx%d" % (binning, binning)
    atcore.AT_SetEnumString(hndl, u"AOIBinning", binning_str)

    # Set size
    maxsize = (c_uint64(), c_uint64())
    atcore.AT_GetIntMax(hndl, u"AOIWidth", byref(maxsize[0]))
    atcore.AT_GetIntMax(hndl, u"AOIHeight", byref(maxsize[1]))
    print maxsize[0].value, maxsize[1].value
    minlt = (c_uint64(), c_uint64())
    atcore.AT_GetIntMax(hndl, u"AOITop", byref(minlt[0]))
    atcore.AT_GetIntMax(hndl, u"AOILeft", byref(minlt[1]))
    print minlt[0].value, minlt[1].value
    
    cursize = (c_uint64(), c_uint64())
    atcore.AT_GetInt(hndl, u"AOIWidth", byref(cursize[0]))
    atcore.AT_GetInt(hndl, u"AOIHeight", byref(cursize[1]))
    print cursize[0].value, cursize[1].value

    implemented = c_int()
    atcore.AT_IsImplemented(hndl, u"AOIWidth", byref(implemented))
    print implemented.value
    writable = c_int()
    atcore.AT_IsWritable(hndl, u"AOIWidth", byref(writable))
    print writable.value

    if writable.value != 0:
        lt = ((maxsize[0].value - size[0]) / 2 + 1, (maxsize[1].value - size[1]) / 2 + 1)
        print lt

        # recommended order
        atcore.AT_SetInt(hndl, u"AOIWidth", c_uint64(size[0]))
        atcore.AT_SetInt(hndl, u"AOILeft", c_uint64(lt[0]))
        atcore.AT_SetInt(hndl, u"AOIHeight", c_uint64(size[1]))
        atcore.AT_SetInt(hndl, u"AOITop", c_uint64(lt[1]))
    else:
        size = (cursize[0].value, cursize[1].value)
    
    cstride = c_uint64()    
    atcore.AT_GetInt(hndl, u"AOIStride", byref(cstride)) # = size of a line in bytes (not pixel)
    stride = cstride.value
    #size = (12,5)

    # set exposure time (which is automatically adapted to a working one)
    newExposure = c_double(exp)
    atcore.AT_SetFloat(hndl, u"ExposureTime", newExposure)
    actualExposure =  c_double()
    atcore.AT_GetFloat(hndl, u"ExposureTime", byref(actualExposure))
    print "exposure time:", actualExposure.value
    
    # Stop making too much noise

    atcore.AT_SetFloat(hndl, u"TargetSensorTemperature", c_double(-15))
    atcore.AT_IsImplemented(hndl, u"FanSpeed", byref(implemented))
    if implemented.value != 0:
        atcore.AT_IsWritable(hndl, u"FanSpeed", byref(writable))
        print writable.value
        num_gain = c_int()
        atcore.AT_GetEnumCount(hndl, u"FanSpeed", byref(num_gain))
        for i in range(num_gain.value):
            gain = create_unicode_buffer(128)
            atcore.AT_GetEnumStringByIndex(hndl, u"FanSpeed", i, gain, len(gain))
            print i, gain.value

        atcore.AT_SetEnumString(hndl, u"FanSpeed", u"Low")

    # Set up the triggermode
    atcore.AT_IsImplemented(hndl, u"TriggerMode", byref(implemented))
    if implemented.value != 0:
        atcore.AT_IsWritable(hndl, u"TriggerMode", byref(writable))
        print writable.value
        num_gain = c_int()
        atcore.AT_GetEnumCount(hndl, u"TriggerMode", byref(num_gain))
        for i in range(num_gain.value):
            gain = create_unicode_buffer(128)
            atcore.AT_GetEnumStringByIndex(hndl, u"TriggerMode", i, gain, len(gain))
            print i, gain.value

        atcore.AT_SetEnumString(hndl, u"TriggerMode", u"Internal") # Software is much slower (0.05 instead of 0.015 s)
    
    atcore.AT_IsImplemented(hndl, u"CycleMode", byref(implemented))
    if implemented.value != 0:
        atcore.AT_IsWritable(hndl, u"CycleMode", byref(writable))
        print writable.value
        num_gain = c_int()
        atcore.AT_GetEnumCount(hndl, u"CycleMode", byref(num_gain))
        for i in range(num_gain.value):
            gain = create_unicode_buffer(128)
            atcore.AT_GetEnumStringByIndex(hndl, u"CycleMode", i, gain, len(gain))
            print i, gain.value

        atcore.AT_SetEnumString(hndl, u"CycleMode", u"Continuous")

    # Set up the encoding
    atcore.AT_IsImplemented(hndl, u"PreAmpGainControl", byref(implemented))
    if implemented.value != 0:
        atcore.AT_IsWritable(hndl, u"PreAmpGainControl", byref(writable))
        print writable.value
        num_gain = c_int()
        atcore.AT_GetEnumCount(hndl, u"PreAmpGainControl", byref(num_gain))
        for i in range(num_gain.value):
            gain = create_unicode_buffer(128)
            atcore.AT_GetEnumStringByIndex(hndl, u"PreAmpGainControl", i, gain, len(gain))
            print i, gain.value

        atcore.AT_SetEnumString(hndl, u"PreAmpGainControl", u"Gain 1 Gain 3 (16 bit)")

    atcore.AT_IsWritable(hndl, u"PixelEncoding", byref(writable))
    print writable.value
    num_encoding = c_int()
    atcore.AT_GetEnumCount(hndl, u"PixelEncoding", byref(num_encoding))
    for i in range(num_encoding.value):
        encoding = create_unicode_buffer(128)
        atcore.AT_GetEnumStringByIndex(hndl, u"PixelEncoding", i, encoding, len(encoding))
        print i, encoding.value

    atcore.AT_SetEnumString(hndl, u"PixelEncoding", u"Mono16")
    #atcore.AT_SetEnumIndex(hndl, u"PixelEncoding", 2)


    # Set up the buffers for containing each one image
    ImageSizeBytes = c_uint64()
    atcore.AT_GetInt(hndl, u"ImageSizeBytes", byref(ImageSizeBytes))
    cbuffers = []
    numbuff = 3
    for i in range(numbuff):
        cbuffer = (c_uint16 * (ImageSizeBytes.value / 2))() # empty array
        atcore.AT_QueueBuffer(hndl, cbuffer, ImageSizeBytes.value)
        print addressof(cbuffer)
        assert(addressof(cbuffer) % 8 == 0) # check alignment
        cbuffers.append(cbuffer)

    print "Starting acquisition"
    pBuffer = POINTER(c_uint16)() # null pointer to ubyte
    BufferSize = c_int()
    timeout = c_uint(int(round((exp + 1) * 1000))) # ms
    atcore.AT_Command(hndl, u"AcquisitionStart")
    #atcore.AT_Command(hndl, u"SoftwareTrigger")
    curbuf = 0
    for i in range(5):
        # Get one image
        start = time.time()

        atcore.AT_WaitBuffer(hndl, byref(pBuffer), byref(BufferSize), timeout)
        print "Got image in", time.time() - start
        #atcore.AT_Command(hndl, u"SoftwareTrigger")
        print addressof(pBuffer.contents), addressof(cbuffers[curbuf])
        #im = string_at(pBuffer, BufferSize.value) # seems to copy the data :-(
        # as_array() is a no-copy mechanism
        array = numpy.ctypeslib.as_array(cbuffers[curbuf]) # what's the type?
        print array.shape, size, size[0] * size[1]
        #array.shape = (stride/2, size[1])
        #print array[136]
        #im = Image.fromarray(array)
        # Two memory copies for one conversion! because of the stride, fromarray() does as bad
        im = Image.fromstring('I', size, array.tostring(), 'raw', 'I;16', stride, -1)
        #im = Image.frombuffer('I', size, cbuffers[curbuf], 'raw', 'I;16', stride, -1)
        im.convert("L").save("test%d.tiff" % i, "TIFF") # 16bits TIFF are not well supported!
        #print "buffer", BufferSize.value, "=", pBuffer[0]
        print "Record image in", time.time() - start
        # Be sure not to queue the buffer before we absolutely don't need the data
        atcore.AT_QueueBuffer(hndl, cbuffers[curbuf], ImageSizeBytes.value)
        curbuf = (curbuf + 1) % len(cbuffers)

        print "Process image in", time.time() - start

    atcore.AT_Command(hndl, u"AcquisitionStop")
    atcore.AT_Flush(hndl)

    # Get another image
#    atcore.AT_QueueBuffer(hndl, cbuffer, ImageSizeBytes.value)
#    atcore.AT_Command(hndl, u"AcquisitionStart")
#    atcore.AT_WaitBuffer(hndl, byref(pBuffer), byref(BufferSize), timeout)
    
#    print addressof(pBuffer.contents), addressof(cbuffer)
#    im = string_at(pBuffer, BufferSize.value) # seems the only way to get pythonic raw data
#    print "buffer", BufferSize.value, "=", pBuffer[0]
    
#    atcore.AT_Command(hndl, u"AcquisitionStop")
#    print "Got second image", (time.time() - start)/2

    atcore.AT_Flush(hndl)

    # Close everything
    atcore.AT_Close(hndl)
    atcore.AT_FinaliseLibrary()
    return (im, size, stride)


scan()
size = (1280,1080)
raw, size, stride = acquire(0, size, 0.1, 1)
print size
i = Image.fromstring('F', size, raw, 'raw', 'F;16', stride, -1)
#print list(i.getdata())
c = i.convert("L")
c.save("test.tiff", "TIFF")

# Neo encodings:
#0 Mono12
#1 Mono12Packed
#2 Mono16 ->18
#3 RGB8Packed
#4 Mono12Coded
#5 Mono12CodedPacked
#6 Mono22Parallel
#7 Mono22PackedParallel
#8 Mono8 -> 19
#9 Mono32


# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
