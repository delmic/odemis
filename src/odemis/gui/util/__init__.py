#-*- coding: utf-8 -*-

import logging
import time
import inspect
from threading import Timer

import wx
from decorator import decorator


#### Decorators ########

@decorator
def call_after(f, self, *args, **kwargs):
    """ This method decorator makes sure the method is called form the main
    (GUI) thread.
    """
    return wx.CallAfter(f, self, *args, **kwargs)

def limit_invocation(rate):
    """ This decorator limits how often a method will be executed.

    :param rate: (float) The minimum interval between executions in seconds.
    """
    def limit(f, self, *args, **kwargs):

        if inspect.isclass(self):
            raise ValueError("limit_invocation decorators should only be "
                             "assigned to instance methods!")

        if rate > 5:
            logging.warn("Warning! Long delay interval. Please consider using "
                         "and interval of 5 or less seconds")
        now = time.time()

        # If the function was called later than 'rate' seconds ago...
        if hasattr(f, 'last_call') and now - f.last_call < rate:
            # If a timer for a previous call is already running, cancel it.
            if hasattr(f, 'timer'):
                logging.debug("Cancelling old delayed method call")
                f.timer.cancel()

            logging.debug('Delaying method call')
            f.timer = Timer(rate, f, args=[self] + list(args), kwargs=kwargs)
            f.timer.start()
            return

        #exectue method call
        f.last_call = now
        logging.debug("Calling delayed method")
        return f(self, *args, **kwargs)
    return decorator(limit)


#### Wrappers ########

def call_after_wrapper(f, *args, **kwargs):
    def wrapzor(*args, **kwargs):
        return wx.CallAfter(f, *args, **kwargs)
    return wrapzor