"""
The MIT License (MIT)

Copyright (c) 2014 Michael Kropat

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

Original code on github:
https://gist.github.com/mkropat/7550097

Importing this module from a platform other than Windows will fail.
"""

import ctypes
from ctypes import windll, wintypes
from uuid import UUID


class GUID(ctypes.Structure):   # [1]
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8)
    ]

    def __init__(self, uuid_):
        ctypes.Structure.__init__(self)
        self.Data1, self.Data2, self.Data3, self.Data4[0], self.Data4[1], rest = uuid_.fields
        for i in range(2, 8):
            self.Data4[i] = rest >> (8 - i - 1) * 8 & 0xff


class FOLDERID(object):  # [2]
    Pictures = UUID('{33E28130-4E1E-4676-835A-98395C3BC3BB}')
    Profile = UUID('{5E6C858F-0E22-4760-9AFE-EA3317B67173}')
    Desktop = UUID('{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}')
    Downloads = UUID('{374DE290-123F-4565-9164-39C4925E467B}')
    ProgramFiles = UUID('{905e63b6-c1bf-494e-b29c-65b732d3d21a}')
    ProgramFilesX86 = UUID('{7C5A40EF-A0FB-4BFC-874A-C0F2E0B9FA8E}')
    Public = UUID('{DFDF76A2-C82A-4D63-906A-5644AC457385}')
    # The complete list is much longer, see https://gist.github.com/mkropat/7550097


class UserHandle(object):  # [3]
    current = wintypes.HANDLE(0)
    common = wintypes.HANDLE(-1)


_CoTaskMemFree = windll.ole32.CoTaskMemFree     # [4]
_CoTaskMemFree.restype = None
_CoTaskMemFree.argtypes = [ctypes.c_void_p]

_SHGetKnownFolderPath = windll.shell32.SHGetKnownFolderPath     # [5] [3]
_SHGetKnownFolderPath.argtypes = [
    ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)
]


class PathNotFoundException(Exception):
    pass


def get_path(folderid, user_handle=UserHandle.current):
    fid = GUID(folderid)
    pPath = ctypes.c_wchar_p()
    S_OK = 0
    if _SHGetKnownFolderPath(ctypes.byref(fid), 0, user_handle, ctypes.byref(pPath)) != S_OK:
        raise PathNotFoundException()
    path = pPath.value
    _CoTaskMemFree(pPath)
    return path

# [1] http://msdn.microsoft.com/en-us/library/windows/desktop/aa373931.aspx
# [2] http://msdn.microsoft.com/en-us/library/windows/desktop/dd378457.aspx
# [3] http://msdn.microsoft.com/en-us/library/windows/desktop/bb762188.aspx
# [4] http://msdn.microsoft.com/en-us/library/windows/desktop/ms680722.aspx
# [5] http://www.themacaque.com/?p=954
