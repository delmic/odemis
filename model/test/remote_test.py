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
from threading import Thread
import Pyro4
import gc
import model
import numpy
import os
import pickle
import threading
import time
import unittest

#gc.set_debug(gc.DEBUG_LEAK | gc.DEBUG_STATS)


class ContainerTest(unittest.TestCase):
    
    def test_empty_container(self):
        container = model.createNewContainer("testempty")
        container.ping()
        container.terminate()
    
    def test_instantiate_component(self):
        comp = model.createInNewContainer("testcont", MyComponent, {"name":"MyComp"})
        self.assertEqual(comp.name, "MyComp")
        val = comp.my_value
        self.assertEqual(val, "ro", "Reading attribute failed")
        
        comp_prime = model.getObject("testcont", "MyComp")
        self.assertEqual(comp_prime.name, "MyComp")
        
        container = model.getContainer("testcont")
        container.ping()
        comp.terminate()
        container.terminate()
        
    def test_multi_components(self):
        comp = model.createInNewContainer("testmulti", FatherComponent, {"name":"Father", "children_num":3})
        self.assertEqual(comp.name, "Father")
        self.assertEqual(len(comp.children), 3, "Component should have 3 children")
        
        for child in comp.children:
            self.assertLess(child.value, 3)
            comp_direct = model.getObject("testmulti", child.name)
            self.assertEqual(comp_direct.name, child.name)
#            child.terminate()
        
        comp.terminate()
        # we are not terminating the children, but this should be caught by the container
        model.getContainer("testmulti").terminate()

#@unittest.skip("simple")
class SerializerTest(unittest.TestCase):
    
    def test_recursive(self):
        try:
            os.remove("test")
        except OSError:
            pass
        daemon = Pyro4.Daemon(unixsocket="test")
        childc = FamilyValueComponent("child", 43, daemon=daemon)
        parentc = FamilyValueComponent("parent", 42, children=[childc], daemon=daemon)
        childc.parent = parentc
#        childc.parent = None
        
        dump = pickle.dumps(parentc, pickle.HIGHEST_PROTOCOL)
#        print "dump size is", len(dump)
        parentc_unpickled = pickle.loads(dump)
        self.assertEqual(parentc_unpickled.value, 42)
        
# TODO test sharing a shared component from the client (probably broken for now)
#@unittest.skip("simple")
class RemoteTest(unittest.TestCase):
    """
    Test the Component, DataFlow, and Properties when shared remotely.
    The test cases are run as "clients" and at start a server is started.
    """
    container_name = "test"
    
    def setUp(self):
        # Use Thread for debug:
        self.server = Thread(target=ServerLoop, args=(self.container_name,))
#        self.server = Process(target=ServerLoop, args=(self.container_name,))
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
        MyComponent queues the future in order of request
        """
        rdaemon = Pyro4.Proxy("PYRO:Pyro.Daemon@./u:"+self.container_name)
        comp = rdaemon.getObject("mycomp")

        comp.set_number_futures(0)
        
        ft1 = comp.do_long(2) # long enough we can cancel ft2
        ft2 = comp.do_long(1) # shorter than ft1
        self.assertFalse(ft1.done(), "Future finished too early")
        self.assertFalse(ft2.done(), "Future finished too early")
        self.assertFalse(ft2.cancelled(), "future doesn't claim being cancelled")
        self.assertFalse(ft2.cancelled(), "future doesn't claim being cancelled")
        self.assertGreater(ft2.result(), 1) # wait for ft2
        self.assertFalse(ft2.cancel(), "could cancel the finished future")

        self.assertTrue(ft1.done(), "Future not finished")
        self.assertGreater(ft1.result(), 2)
        
        self.assertEqual(comp.get_number_futures(), 2)
        
        comp.stopServer()
        time.sleep(0.1) # give it some time to terminate

    def test_async_cancel(self):
        """
        test futures
        """
        rdaemon = Pyro4.Proxy("PYRO:Pyro.Daemon@./u:"+self.container_name)
        comp = rdaemon.getObject("mycomp")

        comp.set_number_futures(0)
        
        ft1 = comp.do_long(2) # long enough we can cancel ft2
        ft2 = comp.do_long(1) # shorter than ft1
        self.assertTrue(ft2.cancel(), "couldn't cancel the future")
        self.assertTrue(ft2.cancelled(), "future doesn't claim being cancelled")
        self.assertRaises(futures.CancelledError, ft2.result)

        # wait for ft1
        res1a = ft1.result()
        self.assertGreater(res1a, 2)
        self.assertTrue(ft1.done(), "Future not finished")
        res1b = ft1.result()
        self.assertEqual(res1a, res1b)
        self.assertGreater(res1b, 2)
        
        self.assertEqual(comp.get_number_futures(), 2)
        
        comp.stopServer()
        time.sleep(0.1) # give it some time to terminate

    def test_subcomponents(self):
        # via method and via roattributes
        # need to test cycles
        rdaemon = Pyro4.Proxy("PYRO:Pyro.Daemon@./u:"+self.container_name)
        comp = rdaemon.getObject("mycomp")
        
        p = rdaemon.getObject("parent")
        self.assertEqual(len(p.children), 1, "parent doesn't have one child")
        c = list(p.children)[0]
#        self.assertEqual(c.parent, p, "Component and parent of child is different")
        self.assertEqual(p.value, 42)
        self.assertEqual(c.value, 43)
        self.assertEqual(len(c.children), 0, "child shouldn't have children")
                
        comp.stopServer()
        time.sleep(0.1) # give it some time to terminate

    def test_dataflow_subscribe(self):
        rdaemon = Pyro4.Proxy("PYRO:Pyro.Daemon@./u:"+self.container_name)
        comp = rdaemon.getObject("mycomp")

        self.count = 0
        self.data_arrays_sent = 0
        comp.data.reset()
        
        comp.data.subscribe(self.receive_data)
        time.sleep(0.5)
        comp.data.unsubscribe(self.receive_data)
        count_end = self.count
        print "received %d arrays over %d" % (self.count, self.data_arrays_sent)
        
        time.sleep(0.1)
        self.assertEqual(count_end, self.count)

        comp.stopServer()
        time.sleep(0.1) # give it some time to terminate
    
    def receive_data(self, dataflow, data):
        self.count += 1
        self.assertEqual(data.shape, (2048, 2048))
        self.data_arrays_sent = data[0][0]
        self.assertGreaterEqual(self.data_arrays_sent, self.count)

    def test_dataflow_unsubscribe_from_callback(self):
        rdaemon = Pyro4.Proxy("PYRO:Pyro.Daemon@./u:"+self.container_name)
        comp = rdaemon.getObject("mycomp")

        self.count = 0
        self.data_arrays_sent = 0
        comp.data.reset()
        
        comp.data.subscribe(self.receive_data_and_unsubscribe)
        time.sleep(0.3)
        self.assertEqual(self.count, 1)
        # It should be 1, or if the generation went very fast, it might be bigger
        self.assertGreaterEqual(self.data_arrays_sent, 1)
#        print "received %d arrays over %d" % (self.count, self.data_arrays_sent)
        
        comp.stopServer()
        time.sleep(0.1) # give it some time to terminate
#        data = comp.data
#        del comp
#        print gc.get_referrers(data)
#        gc.collect()
#        print gc.get_referrers(data)
    
    def receive_data_and_unsubscribe(self, dataflow, data):
        self.count += 1
        self.assertEqual(data.shape, (2048, 2048))
        self.data_arrays_sent = data[0][0]
        self.assertGreaterEqual(self.data_arrays_sent, self.count)
        dataflow.unsubscribe(self.receive_data_and_unsubscribe)

    def test_dataflow_get(self):
        rdaemon = Pyro4.Proxy("PYRO:Pyro.Daemon@./u:"+self.container_name)
        comp = rdaemon.getObject("mycomp")

        comp.data.reset()
        array = comp.data.get()
        self.assertEqual(array.shape, (2048, 2048))
        self.assertEqual(array[0][0], 0)
        
        array = comp.data.get()
        self.assertEqual(array.shape, (2048, 2048))
        self.assertEqual(array[0][0], 0)
        
        comp.stopServer()
        time.sleep(0.1) # give it some time to terminate

    def test_property_update(self):
        rdaemon = Pyro4.Proxy("PYRO:Pyro.Daemon@./u:"+self.container_name)
        comp = rdaemon.getObject("mycomp")

        prop = comp.prop
        self.assertEqual(prop.value, 42)
        prop.value += 1
        self.assertEqual(prop.value, 43)
        
        self.called = 0
        self.last_value = None
        prop.subscribe(self.receive_property_update)
        # now count
        prop.value = 3 # +1
        prop.value = 0 # +1
        prop.value = 0 # nothing because same value
        time.sleep(0.1) # give time to receive notifications
        prop.unsubscribe(self.receive_property_update)
        
        self.assertEqual(prop.value, 0)
        self.assertEqual(self.last_value, 0)
        # called once or twice depending if the brief 3 was seen
        self.assertTrue(1 <= self.called and self.called <= 2)
        called_before = self.called
        
        # check we are not called anymore
        prop.value = 3 # +1
        self.assertEqual(self.called, called_before)
        
        try:
            prop.value = 7.5
            self.fail("Assigning float to a int should not be allowed.")
        except model.InvalidTypeError:
            pass # as it should be
        
        comp.stopServer()
        time.sleep(0.1) # give it some time to terminate
    
    def receive_property_update(self, value):
        self.called += 1
        self.last_value = value
        self.assertIsInstance(value, (int, float))
    
    def test_complex_properties(self):
        rdaemon = Pyro4.Proxy("PYRO:Pyro.Daemon@./u:"+self.container_name)
        comp = rdaemon.getObject("mycomp")

        # enumerated
        self.assertEqual(comp.enum.value, "a")
        self.assertEqual(comp.enum.choices, set(["a", "c", "bfds"]))
        comp.enum.value = "c"
        self.assertEqual(comp.enum.value, "c")
                
        try:
            comp.enum.value = "wfds"
            self.fail("Assigning out of bound should not be allowed.")
        except model.OutOfBoundError:
            pass # as it should be
        
        # continuous
        self.assertEqual(comp.cont.value, 2)
        self.assertEqual(comp.cont.range, (-1, 3.4))
        
        comp.cont.value = 3.0
        self.assertEqual(comp.cont.value, 3)
        
        try:
            comp.cont.value = 4.0
            self.fail("Assigning out of bound should not be allowed.")
        except model.OutOfBoundError:
            pass # as it should be
        
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
    childc = FamilyValueComponent("child", 43, daemon=daemon)
    parentc = FamilyValueComponent("parent", 42, parent=None, children=[childc], daemon=daemon)
    childc.parent = parentc
    daemon.requestLoop()
    component.terminate()
    parentc.terminate()
    daemon.close()


class MyError(Exception):
    pass

class MyComponent(model.Component):
    """
    A component that does everything
    """
    def __init__(self, name, daemon):
        model.Component.__init__(self, name=name, daemon=daemon)
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.number_futures = 0
        self.data = FakeDataFlow()
        # TODO automatically register the property when serializing the Component
        self.prop = model.IntProperty(42)
        self.cont = model.FloatContinuous(2.0, [-1, 3.4])
        self.enum = model.StringEnumerated("a", set(["a", "c", "bfds"]))
        
    
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
        """
        returns the time it took
        """
        start = time.time()
        time.sleep(duration)
        return (time.time() - start)
    
    def get_number_futures(self):
        return self.number_futures
    
    def set_number_futures(self, value):
        self.number_futures = value
    
    def _on_end_long(self, future):
        self.number_futures += 1
    
    # it'll never be able to answer back if everything goes fine
    @oneway
    def stopServer(self):
        self._pyroDaemon.shutdown()


class FamilyValueComponent(model.Component):
    """
    Simple component referencing other components
    """
    def __init__(self, name, value=0, *args, **kwargs):
        model.Component.__init__(self, name, *args, **kwargs)
        self._value = value
    
    @roattribute
    def value(self):
        return self._value
    

class FatherComponent(model.Component):
    """
    Simple component creating children components at init
    """
    def __init__(self, name, value=0, children_num=0, *args, **kwargs):
        """
        children_num (int): number of children to create
        """
        model.Component.__init__(self, name, *args, **kwargs)
        self._value = value
        
        daemon=kwargs.get("daemon", None)
        for i in range(children_num):
            child = FamilyValueComponent("child%d" % i, i, parent=self, daemon=daemon)
            self.children.add(child)
    
    @roattribute
    def value(self):
        return self._value
    
    
class FakeDataFlow(model.DataFlowRemotable):
    def __init__(self, *args, **kwargs):
        super(FakeDataFlow, self).__init__(*args, **kwargs)
        self.shape = (2048, 2048)
        self.bpp = 16
        self._stop = threading.Event()
        self._thread = None
        self.count = 0
    
    def _create_one(self, shape, bpp, index):
        array = numpy.zeros(shape, dtype=("uint%d" % bpp)).view(model.DataArray)
        array[index % shape[0],:] = 255
        return array
    
    def reset(self):
        self.count = 0
        
    def setShape(self, shape=None, bpp=None):
        if shape is not None:
            self.shape = shape
        if bpp is not None:
            self.bpp = bpp
        
    def get(self):
        array = self._create_one(self.shape, self.bpp, 0)
        array[0][0] = 0
        return array
            
    def start_generate(self):
        self.count = 0 # reset
        assert self._thread is None
        self._stop.clear()
        self._thread = threading.Thread(name="array generator", target=self.generate)
        self._thread.deamon = True
        self._thread.start()
    
    def stop_generate(self):
        assert self._thread is not None
        self._stop.set()
        
        # to avoid blocking when unsubscribe from callback
        if threading.current_thread() != self._thread:
            self._thread.join()
        self._thread = None
    
    # method for thread
    def generate(self):
        while not self._stop.isSet():
            self.count += 1
            array = self._create_one(self.shape, self.bpp, self.count)
            array[0][0] = self.count
#            print "generating array %d" % self.count
            self.notify(array)

if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()