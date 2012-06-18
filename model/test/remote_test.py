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
from Pyro4.core import oneway
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
     
    def get_object(self):
        print "server get_obj"
#        return self.object
    
    # it'll never be able to answer back if everything goes fine
    @oneway
    def stopServer(self):
        self._pyroDaemon.shutdown()

if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()