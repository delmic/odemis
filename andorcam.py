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

def list():
    atcore = ATDLL("libatcore.so.3", RTLD_GLOBAL) # Global so that its sub-libraries can access it
    atcore.AT_InitialiseLibrary()
    dc = c_longlong()
    atcore.AT_GetInt(1, u"Device Count", byref(dc))
    print "found %d devices." % dc.value
    
    for i in range(dc.value):
        hndl = c_int()
        atcore.AT_Open(0, byref(hndl))
        model = create_unicode_buffer(128)
        atcore.AT_GetString(hndl, u"Camera Model", model, len(model))
        print i, model.value
        atcore.AT_Close(hndl)
        
    atcore.AT_FinaliseLibrary()

def acquire(device, size, exp):
    atcore = ATDLL("libatcore.so.3", RTLD_GLOBAL) # Global so that its sub-libraries can access it
    atcore.AT_InitialiseLibrary()

    hndl = c_int()
    atcore.AT_Open(0, byref(hndl))

    # set exposure time (which is automatically adapted to a working one)
    newExposure = c_double(exp)
    atcore.AT_SetFloat(hndl, u"ExposureTime", newExposure)
    actualExposure =  c_double()
    atcore.AT_GetFloat(hndl, u"ExposureTime", byref(actualExposure))
    print "exposure time:", actualExposure.value
    
    # Set up the buffer for containing one image
    ImageSizeBytes = c_uint64()
    atcore.AT_GetInt(hndl, u"ImageSizeBytes", byref(ImageSizeBytes))

#    userbuffer = bytearray(ImageSizeBytes.value)
#    cbuffer = (c_ubyte * ImageSizeBytes.value).from_buffer(userbuffer)
    cbuffer = (c_ubyte * ImageSizeBytes.value)()
    atcore.AT_QueueBuffer(hndl, cbuffer, ImageSizeBytes.value)

    # Get one image
    atcore.AT_Command(hndl, u"AcquisitionStart")

    pBuffer = POINTER(c_ubyte)()
    BufferSize = c_int()
    timeout = c_uint(int(round((exp + 1) * 1000))) # ms
    atcore.AT_WaitBuffer(hndl, byref(pBuffer), byref(BufferSize), timeout)
    
    print addressof(pBuffer.contents), addressof(cbuffer)
#    array = (c_ubyte * BufferSize.value).from_adress(pBuffer)
    print "buffer", BufferSize.value, "=", pBuffer[0]
    
    atcore.AT_Command(hndl, u"AcquisitionStop")
    atcore.AT_Flush(hndl)

    # Close everything
    atcore.AT_Close(hndl)
    atcore.AT_FinaliseLibrary()
    return string_at(pBuffer, BufferSize.value)


def image_from_raw(raw, width, height, bits_per_sample = 8, bit_shift = 0):
    """Converts an image from raw format returned by the Quanta to a PIL image.
    raw (bytearray): the raw image
    width (int): width of the image (pixel)
    height (int): height of the image
    bits_per_sample (int): how many bits are making one pixel => 8 or 16 only allowed
    bit_shift (int): how many bits should the value be shifted to the right to get the original value
    returns a PIL image        
    """
    # TODO make it less quick and dirty by supporting unsual parameters...
    if (bit_shift != 0 and 
       not (bits_per_sample == 8 and bit_shift == 8)):
        print "Warning, bit shift not supported: " + str(bit_shift)

    # The Quanta documentation says only 8 or 16 is possible
    if bits_per_sample == 8:
        return Image.frombuffer('L', (width, height), raw, 'raw', 'L', 0, 1)
    elif bits_per_sample == 16:
        print "Slow decoding mode as pixels are 16 bits."
        return Image.frombuffer('F', (width, height), raw, 'raw', 'F;16', 0, 1)
        # L I;16?
    else:
        print "Error, pixel format not supported: " + str(bits_per_sample)
        raise Exception("Pixel format not supported")

list()
size = (1280,256)
raw = acquire(0, size, 0.1)
i = Image.fromstring('L', size, raw, 'raw', 'L', 0, 1)
i.save("test.tiff", "TIFF")

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: