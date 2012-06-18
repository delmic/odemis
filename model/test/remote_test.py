#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 18 Jun 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Open Delmic Microscope Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''
from concurrent import futures
from concurrent.futures.thread import ThreadPoolExecutor
from model import roattribute, oneway, isasync
from multiprocessing.process import Process
import Pyro4
import model
import os
import time
import unittest

"""
Test the Component, DataFlow, and Properties when shared remotely.
The test cases are run as "clients" and at start a server is started.
"""

class RemoteTest(unittest.TestCase):
    container_name = "test"
    
    def setUp(self):
        self.server = Process(target=ServerLoop, args=(self.container_name,))
        self.server.start()
        time.sleep(0.1) # give it some time to start

    def tearDown(self):
        if self.server.is_alive():
            print "Warning: killing server still alive"
            self.server.terminate()

    def test_simple(self):
        """
        start a component, ping, and stop it
        """
        rdaemon = Pyro4.Proxy("PYRO:Pyro.Daemon@./u:"+self.container_name)
        comp = rdaemon.getObject("mycomp")
        ret = comp.ping()
        self.assertEqual(ret, "pong", "Ping failed")
        comp.stopServer()
        time.sleep(0.1) # give it some time to terminate
        
    def test_exception(self):
        rdaemon = Pyro4.Proxy("PYRO:Pyro.Daemon@./u:"+self.container_name)
        comp = rdaemon.getObject("mycomp")
        
        # test it raises
        self.assertRaises(MyError, comp.bad_call)
        
        # test it raises when wrong argument
        self.assertRaises(TypeError, comp.ping, ("non needed arg",))
        
        # non existing method
        self.assertRaises(AttributeError, comp.non_existing_method)
        
        comp.stopServer()
        time.sleep(0.1) # give it some time to terminate

    def test_roattributes(self):
        """
        check roattributes
        """
        rdaemon = Pyro4.Proxy("PYRO:Pyro.Daemon@./u:"+self.container_name)
        comp = rdaemon.getObject("mycomp")
        val = comp.my_value
        self.assertEqual(val, "ro", "Reading attribute failed")
        comp.stopServer()
        time.sleep(0.1) # give it some time to terminate
        
    def test_async(self):
        """
        test futures
        """
        rdaemon = Pyro4.Proxy("PYRO:Pyro.Daemon@./u:"+self.container_name)
        comp = rdaemon.getObject("mycomp")

        ft1 = comp.do_long()
        ft2 = comp.do_long(4)
        result_cancel = ft2.cancel()
        print "Cancel returned ", result_cancel
        try:
            print ft2.result()
        except futures.CancelledError:
            print "ft2 was cancelled"
#        assert ft2.result() >= 4
        ft1.result()
        assert ft1.done()
        print "first future lasted", ft1.result()

    def test_subcomponents(self):
        # via method and via roattributes
        # need to test cycles
        pass
    
    def test_dataflow(self):
        pass
    
    def test_properties(self):
        pass
# a basic server (component container)
def ServerLoop(socket_name):
    try:
        os.remove(socket_name)
    except OSError:
        pass
    daemon = Pyro4.Daemon(unixsocket=socket_name)
    component = MyComponent("mycomp", daemon)
    daemon.requestLoop()
    component.terminate()
    daemon.close()


class MyError(Exception):
    pass

class MyComponent(model.Component):
    """
    The actual class generating arrays
    """
    def __init__(self, name, daemon):
        model.Component.__init__(self, name=name, daemon=daemon)
        self.executor = ThreadPoolExecutor(max_workers=1)
        
    
    @roattribute
    def my_value(self):
        return "ro"
    
    def ping(self):
        """
        Returns (string): pong
        """
        return "pong"
     
    def bad_call(self):
        """
        always raise an exception
        """
        raise MyError
    
    @isasync
    def do_long(self, duration=5):
        """
        return a futures.Future
        """
        ft = self.executor.submit(self._long_task, duration)
        ft.add_done_callback(self._on_end_long)
        return ft

    def _long_task(self, duration):
        start = time.time()
        time.sleep(duration)
        print "done doing something long"
        return (time.time() - start)
    
    def _on_end_long(self, future):
        if future.cancelled():
            print "server finished future due to cancellation"
        else:
            print "server finished future "+str(future)+" with result="+str(future.result())
         
    
    
    def get_subcomp(self):
        print "server get_obj"
#        return self.object
    
    # it'll never be able to answer back if everything goes fine
    @oneway
    def stopServer(self):
        self._pyroDaemon.shutdown()

if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()