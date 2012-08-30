#-*- coding: utf-8 -*-

import wx
from decorator import decorator

@decorator
def call_after(f, self, *args, **kwargs):
    """ This method decorator makes sure the method is called form the main
    (GUI) thread.
    """
    return wx.CallAfter(f, self, *args, **kwargs)

def call_after_wrapper(f, *args, **kwargs):
    def wrapzor(*args, **kwargs):
        return f(*args, **kwargs)
    return wrapzor